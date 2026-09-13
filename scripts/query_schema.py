#!/usr/bin/env python
"""
The vocabulary the query parser is allowed to use, and the guardrail that enforces it.

Two jobs:

1. **Schema cards.** Each parsing gate sees only the fields relevant to it, with their real
   value vocabularies, generated from Gold rather than written by hand. A hand-maintained
   list drifts; this cannot. Smaller per-gate cards also mean a shorter, cacheable prompt
   and less room for a wrong-but-plausible column name.

2. **validate_filter().** The guardrail docs/master_plan.md 4a asks for. A filter aimed at a
   column that is null for the event type it is applied to returns **zero rows, not an
   error** - that is how five separate bugs in this project produced empty results that read
   as findings. Here it raises. The parser must never be able to answer confidently from a
   column that could not have held an answer.

Usage:
    from query_schema import card_for, validate_filter, FilterError
    print(card_for("event_type"))
    validate_filter("give_and_go", "player_possession")   # raises FilterError
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "gold"

EVENT_TYPES = ["player_possession", "passing_option", "off_ball_run", "on_ball_engagement"]
#: A column populated on under this share of an event type's rows is treated as absent there.
POPULATED_THRESHOLD = 0.01
#: Above this many distinct values a column is described by range rather than enumeration.
MAX_ENUM = 40


class FilterError(Exception):
    """A filter references a column that cannot answer for the event type it targets."""


@dataclass(frozen=True)
class Field:
    name: str
    kind: str                 # "enum" | "bool" | "numeric" | "id"
    values: tuple = ()
    event_types: tuple = ()   # event types where it is actually populated
    note: str = ""

    def render(self) -> str:
        where = ("all types" if len(self.event_types) == len(EVENT_TYPES)
                 else "/".join(e.replace("_", " ") for e in self.event_types))
        if self.kind == "enum":
            body = " | ".join(self.values)
        elif self.kind == "bool":
            body = "true/false"
        else:
            body = self.kind
        line = f"- {self.name} ({body}) [{where}]"
        return line + (f"  # {self.note}" if self.note else "")


# --------------------------------------------------------------------------------------
# Which fields each gate may use. Curated on purpose: the events table has 354 columns and
# showing all of them invites plausible-but-wrong picks. Membership is by gate, availability
# and values come from the data.
# --------------------------------------------------------------------------------------
GATE_FIELDS: dict[str, list[str]] = {
    "event_type": [
        "event_type", "event_subtype", "end_type", "start_type", "is_header",
        "carry", "one_touch", "quick_pass", "high_pass", "pass_range",
        "game_interruption_before", "game_interruption_after",
    ],
    "player": [
        "player_position", "player_in_possession_position", "number", "is_substitute",
        "is_starter", "is_captain", "is_starting_cb_pair",
        "team_back_line_size", "team_front_line_size", "team_pivot_size",
    ],
    "spatial": [
        "third_start", "third_end", "channel_start", "channel_end",
        "penalty_area_start", "penalty_area_end",
        "own_penalty_area_start", "own_penalty_area_end",
        "opp_penalty_area_start", "opp_penalty_area_end",
        "own_six_yard_box_start", "opp_six_yard_box_start",
        "opp_penalty_area_reception", "opp_six_yard_box_reception",
        "x_start", "y_start", "x_end", "y_end",
        "out_to_in", "in_to_out", "inside_defensive_shape_start",
        "first_line_break", "second_last_line_break", "last_line_break",
    ],
    "temporal": [
        "minute_start", "period", "game_state", "team_score", "opponent_team_score",
        "seconds_since_goal_for", "seconds_since_goal_against",
        "seconds_until_goal_for", "seconds_until_goal_against",
    ],
    "sequence": [
        "team_possession_id", "chain_n_possessions", "chain_reached_final_third",
        "chain_reached_box", "chain_ended_in_shot", "chain_duration_s",
        "n_player_possessions_in_phase", "team_in_possession_phase_type",
        "team_out_of_possession_phase_type", "pressing_chain",
        "pressing_chain_length", "pressing_chain_end_type",
    ],
    "comparative": [
        "separation_start", "separation_end", "interplayer_distance",
        "n_teammates_ahead_start", "n_opponents_ahead_start", "n_opponents_ahead_end",
        "n_opponents_overtaken", "n_passing_options", "n_passing_options_dangerous_difficult",
        "beaten_by_possession", "beaten_by_movement",
    ],
    "outcome": [
        "lead_to_shot", "lead_to_goal", "seconds_to_next_shot_same_team",
        "seconds_to_next_goal_same_team", "seconds_to_next_shot_opponent",
        "targeted", "received", "possession_danger", "chain_ended_in_shot",
    ],
    "negative": [],   # structural, not field-based - see query_parse.py
}

NOTES = {
    "penalty_area_start": "fires for EITHER box; use own_/opp_ to disambiguate",
    "penalty_area_end": "fires for EITHER box; use own_/opp_ to disambiguate",
    "lead_to_shot": "fixed 10s window; prefer seconds_to_next_shot_* for other windows",
    "seconds_to_next_shot_opponent": "use this one on on_ball_engagement rows (defending)",
    "team_possession_id": "unbroken team possession; NOT the same grouping as phase_index",
    "is_captain": "always false unless data/captains.json is populated",
    "opp_penalty_area_reception": "where a pass was RECEIVED, not where the passer stood",
}


@lru_cache(maxsize=1)
def _events() -> pd.DataFrame:
    path = GOLD / "events_enriched.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing. Run: python scripts/build_silver.py && "
            f"python scripts/build_gold.py")
    return pd.read_parquet(path)


@lru_cache(maxsize=1)
def availability() -> dict:
    """{column: (event types where it is populated)} - the validate_filter() source."""
    ev = _events()
    out = {}
    for col in ev.columns:
        types = tuple(
            et for et in EVENT_TYPES
            if len(sub := ev.loc[ev.event_type == et, col]) and
            sub.notna().mean() > POPULATED_THRESHOLD
        )
        out[col] = types
    return out


@lru_cache(maxsize=4096)
def observed_values(column: str, event_type: str | None = None) -> frozenset | None:
    """Distinct values a low-cardinality column actually takes (on one event type, if given).

    None for high-cardinality or numeric columns, where "does this value occur" is not a
    meaningful check. Values are normalised to lower-case strings so a JSON `true` from the
    model compares equal to a numpy True in the data.
    """
    ev = _events()
    if column not in ev.columns:
        return None
    s = ev[column] if event_type is None else ev.loc[ev.event_type == event_type, column]
    s = s.dropna()
    dtype = str(ev[column].dtype)
    if dtype not in ("bool", "boolean", "category") and not (
            ev[column].dtype == object and ev[column].nunique(dropna=True) <= MAX_ENUM):
        return None
    return frozenset(str(v).lower() for v in s.unique())


@lru_cache(maxsize=1)
def _fields() -> dict:
    ev = _events()
    avail = availability()
    fields = {}
    for col in {c for cols in GATE_FIELDS.values() for c in cols}:
        if col not in ev.columns:
            continue
        s = ev[col]
        dtype = str(s.dtype)
        if dtype in ("bool", "boolean"):
            # A boolean that is never True cannot answer "where X is true", so it is left
            # out of the card entirely. Advertising is_captain - all False until
            # data/captains.json is populated - led the model to filter is_captain == True in
            # the first real evaluation, a query that could only ever return zero rows.
            if not s.fillna(False).astype(bool).any():
                continue
            kind, values = "bool", ()
        elif dtype == "category" or (s.dtype == object and s.nunique(dropna=True) <= MAX_ENUM):
            kind = "enum"
            values = tuple(sorted(str(v) for v in s.dropna().unique()))
        else:
            kind, values = "numeric", ()
        fields[col] = Field(name=col, kind=kind, values=values,
                            event_types=avail.get(col, ()), note=NOTES.get(col, ""))
    return fields


def card_for(gate: str) -> str:
    """The field vocabulary one gate is allowed to use, as prompt text."""
    if gate not in GATE_FIELDS:
        raise KeyError(f"unknown gate {gate!r}; known: {sorted(GATE_FIELDS)}")
    fields = _fields()
    lines = [f"FIELDS AVAILABLE TO THE {gate.upper()} GATE",
             "Use ONLY these column names and these values. If the question needs something",
             "not listed, say so rather than inventing a column.",
             ""]
    for col in GATE_FIELDS[gate]:
        f = fields.get(col)
        lines.append(f.render() if f else f"- {col} (unavailable in this dataset)")
    return "\n".join(lines)


def validate_filter(column: str, event_type: str | None = None,
                    op: str | None = None, value=None) -> None:
    """Raise unless `column` exists and is populated for `event_type`.

    This is the guardrail against the project's dominant failure mode. Filtering a column
    that is always null for the event type being filtered returns an empty result rather
    than an error, and an empty result reads as a finding. 168 of 354 columns are populated
    on exactly one event type, so the chance of getting this wrong by guessing is high.
    """
    avail = availability()
    if column not in avail:
        raise FilterError(
            f"no column named {column!r} in the events table - "
            f"the parser may not invent columns")
    populated = avail[column]
    if not populated:
        raise FilterError(f"column {column!r} is empty across the whole dataset")
    if event_type is not None and event_type not in populated:
        raise FilterError(
            f"{column!r} is never populated on {event_type!r} rows "
            f"(only on {', '.join(populated)}). Filtering it there would return zero rows "
            f"silently, which reads as a finding rather than a mistake.")

    # The column can hold values - but can it hold THIS one? A populated column checked for a
    # value it never takes is the same silent zero one step removed: is_captain is 100%
    # populated and 100% False, so `is_captain == True` passed the checks above and could
    # only ever match nothing.
    if op in ("eq", "in") and value is not None:
        seen = observed_values(column, event_type)
        if seen is not None:
            wanted = value if isinstance(value, list) else [value]
            missing = [v for v in wanted if str(v).lower() not in seen]
            if missing and len(missing) == len(wanted):
                where = f" on {event_type!r} rows" if event_type else ""
                raise FilterError(
                    f"{column!r} never takes the value(s) {missing!r}{where} - "
                    f"observed: {sorted(seen)[:12]}. The filter could only match zero rows.")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", help="print one gate's card")
    ap.add_argument("--check", nargs=2, metavar=("COLUMN", "EVENT_TYPE"))
    args = ap.parse_args()

    if args.check:
        try:
            validate_filter(*args.check)
            print(f"OK: {args.check[0]} is usable on {args.check[1]}")
        except FilterError as e:
            print(f"REJECTED: {e}")
        return
    if args.gate:
        print(card_for(args.gate))
        return
    for gate in GATE_FIELDS:
        card = card_for(gate)
        print(f"\n{'=' * 70}\n{card}")


if __name__ == "__main__":
    main()
