#!/usr/bin/env python
"""
Tier 1 + Tier 2 enrichment of the SkillCorner Dynamic Events tables.

Adds derived columns that let the retrieval layer answer test-set questions which the
raw schema can only approximate (or not answer at all). Every column added here is
listed in DERIVED_COLUMNS.

Tier 1 (no tracking data needed)
  1. Goal timeline      -> seconds_since_goal_for/against, seconds_until_goal_for/against
                           Unblocks Q49/Q51/Q76 ("N minutes after scoring/conceding").
  2. Outcome windows    -> seconds_to_next_shot/goal_same_team
                           Replaces lead_to_shot's hardcoded 10s so any window (4s/6s/8s)
                           is a filter parameter. Fixes Q39/Q41/Q44/Q77/Q79.
  3. Possession chains  -> team_possession_id + chain_* rollups
                           Replaces phase_index as the "same sequence" key. Fixes
                           Q35/Q43/Q79 and makes Q36 tractable.
  4. Mirror-safe zones  -> own_/opp_ penalty area + six-yard box, scaled per match
                           Fixes Q29/Q32 and gives Q21 a real six-yard split.

Tier 2
  5. Squad units        -> is_starter, team_back_line_size, team_front_line_size,
                           team_pivot_size, is_starting_cb_pair
                           Makes "back four" / "front two" / "CB pairing" literal
                           (Q4/Q14/Q79) instead of a bare position filter.
  6. Captain            -> is_captain, from an optional external data/captains.json.
                           NOT derivable from SkillCorner data - see load_captains().

Coordinate frame (verified empirically, not assumed): every row's x/y are mirrored into
that row's OWN team's attacking frame. Joining defensive engagements to the possession
they engage gives median |x_obe + x_pp| = 4.75m vs |x_obe - x_pp| = 36.6m, i.e. mirrored.
So x < 0 is the row player's own half for EVERY event type, including on_ball_engagement.
Coordinates are real metres on each match's own pitch (104-106m long here), so box
geometry must be scaled per match rather than hardcoded.

Usage:
    from enrich import load_enriched
    events, meta, goals = load_enriched(verbose=True)
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SILVER = ROOT / "data" / "silver"
GOLD = ROOT / "data" / "gold"
CAPTAINS_FILE = ROOT / "data" / "captains.json"

FPS = 10.0                              # verified: 28980 frames / 48.3 min = 600 per min
BOX_DEPTH, BOX_HALF_W = 16.5, 20.16     # penalty area: 16.5m deep, 40.32m wide
SIX_DEPTH, SIX_HALF_W = 5.5, 9.16       # six-yard box: 5.5m deep, 18.32m wide
GOAL_CLUSTER_FRAMES = 100               # 10s: collapse duplicate markers for one goal
MARKER_LOOKBACK_FRAMES = 1500           # 150s: max lag from goal to the score-bump event

BACK_LINE = {"CB", "LCB", "RCB", "LB", "RB", "LWB", "RWB"}
FRONT_LINE = {"CF", "LF", "RF"}
PIVOT = {"DM", "LDM", "RDM"}
CENTRE_BACKS = {"CB", "LCB", "RCB"}

DERIVED_COLUMNS = [
    "seconds_since_goal_for", "seconds_since_goal_against",
    "seconds_until_goal_for", "seconds_until_goal_against",
    "seconds_to_next_shot_same_team", "seconds_to_next_goal_same_team",
    "seconds_to_next_shot_opponent", "seconds_to_next_goal_opponent",
    "team_possession_id", "chain_n_possessions", "chain_reached_final_third",
    "chain_reached_box", "chain_ended_in_shot", "chain_duration_s",
    "own_penalty_area_start", "own_penalty_area_end",
    "opp_penalty_area_start", "opp_penalty_area_end",
    "own_six_yard_box_start", "own_six_yard_box_end",
    "opp_six_yard_box_start", "opp_six_yard_box_end",
    "own_penalty_area_reception", "opp_penalty_area_reception",
    "own_six_yard_box_reception", "opp_six_yard_box_reception",
    "number", "is_substitute",
    "is_starter", "team_back_line_size", "team_front_line_size",
    "team_pivot_size", "is_starting_cb_pair", "is_captain",
]


def _as_bool(s: pd.Series) -> pd.Series:
    """SkillCorner booleans arrive as object dtype ('True'/'False' strings)."""
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().eq("true")


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------
def load_silver():
    """Read the Silver entity tables. Build them first with scripts/build_silver.py."""
    need = ["events.parquet", "matches.parquet", "players.parquet"]
    missing = [n for n in need if not (SILVER / n).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing Silver tables {missing} under {SILVER}. "
            f"Run: python scripts/build_silver.py")
    return (pd.read_parquet(SILVER / "events.parquet"),
            pd.read_parquet(SILVER / "matches.parquet"),
            pd.read_parquet(SILVER / "players.parquet"))


# --------------------------------------------------------------------------------------
# Tier 1.1 - goal timeline
# --------------------------------------------------------------------------------------
def build_goal_timeline(events: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """One row per goal: match_id, frame, period, scoring_team_id, frame_source.

    Neither available signal is complete on its own, so both are unioned:

    * game_interruption_after == 'goal_for' marks the possession that ended in the goal,
      so its frame_end is the exact goal moment - but the marker is missing for some
      goals (match 2006229: two goals by one scorer, one marker).
    * The running team_score is complete mid-match, but only updates on the first event
      after the restart (~55-100s late) and never registers a goal scored so late that no
      further event follows (matches 1927964 and 2015213 each end 4-x with only 3 bumps).

    So: take every marker, then add any score bump that no marker already explains,
    timestamping those from the team's last shot before the restart. Counts are validated
    against the official score in _match.json by validate_goal_timeline().
    """
    rows = []
    for m in matches.itertuples():
        mid = int(m.match_id)
        sub = events[events.match_id == mid]
        if sub.empty:
            continue
        shots = sub[(sub.event_type == "player_possession") & (sub.end_type == "shot")]
        # Only player_possession rows are authoritative: a goal is scored by the player
        # in possession. on_ball_engagement rows also carry 'goal_for', but stamped from
        # the possessing team's perspective rather than the defending row player's, which
        # credits the wrong team (match 2016236, frame 13074, no matching score bump).
        markers = sub[(sub.game_interruption_after == "goal_for")
                      & (sub.event_type == "player_possession")]
        for team in sorted(sub.team_id.dropna().unique()):
            g = sub[sub.team_id == team].sort_values("frame_start")
            t_shots = np.sort(shots.loc[shots.team_id == team, "frame_end"].to_numpy(float))
            raw = np.sort(markers.loc[markers.team_id == team, "frame_end"].to_numpy(float))

            # Collapse repeat markers for the same goal (one fires per event_type).
            found = []
            for f in raw:
                if not found or f - found[-1] > GOAL_CLUSTER_FRAMES:
                    found.append(float(f))
            placed = [dict(frame=f, frame_source="goal_marker") for f in found]

            score = pd.concat([pd.Series([0]), g["team_score"]], ignore_index=True)
            bumps = score.diff().fillna(0)
            for pos in np.flatnonzero(bumps.to_numpy() > 0):
                observed = float(g.iloc[pos - 1].frame_start)
                for _ in range(int(bumps.iloc[pos])):
                    explained = [p for p in placed
                                 if 0 <= observed - p["frame"] <= MARKER_LOOKBACK_FRAMES]
                    if explained:
                        continue
                    prior = [p["frame"] for p in placed if p["frame"] < observed]
                    floor = max(prior) if prior else -(10 ** 9)
                    cand = t_shots[(t_shots <= observed) & (t_shots > floor)]
                    placed.append(dict(frame=float(cand[-1]) if len(cand) else observed,
                                       frame_source="last_shot" if len(cand) else "score_change"))

            period_edges = [m.period_1_end, m.period_2_end]
            for p in sorted(placed, key=lambda d: d["frame"]):
                period = 1 + sum(1 for e in period_edges[:-1] if p["frame"] > e)
                rows.append(dict(match_id=int(mid), frame=p["frame"], period=period,
                                 scoring_team_id=int(team), frame_source=p["frame_source"]))
    if not rows:
        return pd.DataFrame(columns=["match_id", "frame", "period",
                                     "scoring_team_id", "frame_source"])
    return pd.DataFrame(rows).sort_values(["match_id", "frame"]).reset_index(drop=True)


def validate_goal_timeline(goals: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Compare derived goal counts against each match's official score."""
    out = []
    for m in matches.itertuples():
        g = goals[goals.match_id == int(m.match_id)]
        dh = int((g.scoring_team_id == m.home_team_id).sum())
        da = int((g.scoring_team_id == m.away_team_id).sum())
        out.append(dict(match_id=int(m.match_id),
                        official=f"{m.home_team_score}-{m.away_team_score}",
                        derived=f"{dh}-{da}",
                        ok=(dh == m.home_team_score and da == m.away_team_score)))
    return pd.DataFrame(out)


def add_goal_relative_time(events: pd.DataFrame, goals: pd.DataFrame) -> pd.DataFrame:
    """seconds_since/until the row team's own and conceded goals."""
    for c in ["seconds_since_goal_for", "seconds_since_goal_against",
              "seconds_until_goal_for", "seconds_until_goal_against"]:
        events[c] = np.nan

    for mid, gm in goals.groupby("match_id"):
        m_mask = events.match_id == mid
        teams = events.loc[m_mask, "team_id"].dropna().unique()
        for team in teams:
            idx = events.index[m_mask & (events.team_id == team)]
            if len(idx) == 0:
                continue
            f = events.loc[idx, "frame_start"].to_numpy(float)
            scored = np.sort(gm.loc[gm.scoring_team_id == team, "frame"].to_numpy(float))
            conceded = np.sort(gm.loc[gm.scoring_team_id != team, "frame"].to_numpy(float))
            for frames, since_col, until_col in (
                (scored, "seconds_since_goal_for", "seconds_until_goal_for"),
                (conceded, "seconds_since_goal_against", "seconds_until_goal_against"),
            ):
                if len(frames) == 0:
                    continue
                prev_i = np.searchsorted(frames, f, side="right") - 1
                nxt_i = np.searchsorted(frames, f, side="left")
                since = np.where(prev_i >= 0,
                                 (f - frames[np.clip(prev_i, 0, None)]) / FPS, np.nan)
                until = np.where(nxt_i < len(frames),
                                 (frames[np.clip(nxt_i, None, len(frames) - 1)] - f) / FPS,
                                 np.nan)
                events.loc[idx, since_col] = since
                events.loc[idx, until_col] = until
    return events


# --------------------------------------------------------------------------------------
# Tier 1.2 - parameterised outcome windows
# --------------------------------------------------------------------------------------
def add_outcome_windows(events: pd.DataFrame, goals: pd.DataFrame) -> pd.DataFrame:
    """Seconds from this event's end to the next shot / goal, by the row team and by its
    opponent.

    lead_to_shot is a fixed 10s boolean; these make the window a query parameter, so
    "shot within 6s" and "within 8s" become exact filters instead of 10s approximations.

    Use the _opponent columns for on_ball_engagement rows: the row player there is
    DEFENDING, so native lead_to_shot on an OBE row refers to a shot by the team in
    possession, i.e. the opponent. Checking those rows against _same_team instead
    disagrees with lead_to_shot on 1,716 rows.
    """
    for c in ["seconds_to_next_shot_same_team", "seconds_to_next_goal_same_team",
              "seconds_to_next_shot_opponent", "seconds_to_next_goal_opponent"]:
        events[c] = np.nan

    pp = events[events.event_type == "player_possession"]
    shots = pp[pp.end_type == "shot"]

    for mid, sub in events.groupby("match_id"):
        sm = shots[shots.match_id == mid]
        gm = goals[goals.match_id == mid]
        for team in sub.team_id.dropna().unique():
            idx = sub.index[sub.team_id == team]
            if len(idx) == 0:
                continue
            f_end = events.loc[idx, "frame_end"].to_numpy(float)
            pairs = (
                (np.sort(sm.loc[sm.team_id == team, "frame_end"].to_numpy(float)),
                 "seconds_to_next_shot_same_team"),
                (np.sort(gm.loc[gm.scoring_team_id == team, "frame"].to_numpy(float)),
                 "seconds_to_next_goal_same_team"),
                (np.sort(sm.loc[sm.team_id != team, "frame_end"].to_numpy(float)),
                 "seconds_to_next_shot_opponent"),
                (np.sort(gm.loc[gm.scoring_team_id != team, "frame"].to_numpy(float)),
                 "seconds_to_next_goal_opponent"),
            )
            for frames, col in pairs:
                if len(frames) == 0:
                    continue
                nxt = np.searchsorted(frames, f_end, side="left")
                val = np.where(nxt < len(frames),
                               (frames[np.clip(nxt, None, len(frames) - 1)] - f_end) / FPS,
                               np.nan)
                events.loc[idx, col] = val
    return events


# --------------------------------------------------------------------------------------
# Tier 1.3 - true possession chains
# --------------------------------------------------------------------------------------
def add_possession_chains(events: pd.DataFrame) -> pd.DataFrame:
    """team_possession_id = one unbroken team possession, plus chain-level rollups.

    SkillCorner already delimits these with first/last_player_possession_in_team_possession.
    This is NOT the same grouping as phase_index: in match 1874553 there are 192 team
    possessions vs 360 phases, and a single possession can span two phases while a single
    phase can contain possessions by both teams. Using phase_index as the "same sequence"
    key (as phase_reaches() did) therefore both over- and under-matches.
    """
    events["team_possession_id"] = np.nan
    pp_mask = events.event_type == "player_possession"
    pp = events[pp_mask].sort_values(["match_id", "frame_start"])

    first_flag = _as_bool(pp["first_player_possession_in_team_possession"])
    tp = first_flag.groupby(pp["match_id"]).cumsum()
    tp_id = pp["match_id"].astype(str) + "_tp" + tp.astype(int).astype(str)
    events.loc[pp.index, "team_possession_id"] = tp_id.values

    # Propagate to PO / OBR / OBE via their parent possession.
    pp_lookup = (events.loc[pp_mask, ["match_id", "event_id", "team_possession_id"]]
                 .rename(columns={"event_id": "assoc_id"}))
    other = events[~pp_mask & events.associated_player_possession_event_id.notna()]
    if not other.empty:
        merged = other[["match_id", "associated_player_possession_event_id"]].merge(
            pp_lookup, left_on=["match_id", "associated_player_possession_event_id"],
            right_on=["match_id", "assoc_id"], how="left")
        events.loc[other.index, "team_possession_id"] = merged["team_possession_id"].values

    # Chain-level rollups, broadcast back onto every row of the chain.
    known = events.dropna(subset=["team_possession_id"])
    g = known.groupby("team_possession_id")
    roll = pd.DataFrame({
        "chain_n_possessions": g["event_id"].size(),
        "chain_reached_final_third": g["third_end"].apply(lambda s: (s == "attacking_third").any()),
        "chain_reached_box": g["penalty_area_end"].apply(lambda s: _as_bool(s).any()),
        "chain_ended_in_shot": g["end_type"].apply(lambda s: (s == "shot").any()),
        "chain_duration_s": (g["frame_end"].max() - g["frame_start"].min()) / FPS,
    })
    for c in roll.columns:
        events[c] = events["team_possession_id"].map(roll[c])
    return events


# --------------------------------------------------------------------------------------
# Tier 1.4 - mirror-safe, per-match-scaled zone flags
# --------------------------------------------------------------------------------------
def add_zone_flags(events: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """own_/opp_ penalty-area and six-yard-box flags for both start and end coordinates.

    Native penalty_area_start/end fires for EITHER box - of its 3,863 True rows, 2,764
    are in the opponent box and 1,062 in the row team's own box. (docs/real_data_validation.md
    previously recorded it as attacking-box-only; that is wrong, see docs/enrichment.md.)
    So the native flag answers "in a penalty area" but cannot answer "in THEIR OWN box",
    which is what Q29/Q32 ask. These columns split the two apart and scale the box edge to
    each match's real pitch length (104-106m here) rather than hardcoding 105m.

    There is no six-yard-box flag in the schema at all; opp_/own_six_yard_box_* are new.

    The _reception variants use player_targeted_x/y_reception - where a pass was actually
    RECEIVED, not where the passer stood. "Crosses delivered into the six-yard box" (Q21)
    means the destination: x_end on a possession row is where the passer released the ball,
    which finds only 4 rows across 20 matches versus 288 by reception point.
    """
    half = events.match_id.map(
        dict(zip(matches.match_id, matches.pitch_length / 2.0)))
    coords = {"start": ("x_start", "y_start"), "end": ("x_end", "y_end"),
              "reception": ("player_targeted_x_reception", "player_targeted_y_reception")}
    for suf, (xc, yc) in coords.items():
        x, y = events[xc], events[yc].abs()
        events[f"own_penalty_area_{suf}"] = (x < -(half - BOX_DEPTH)) & (y <= BOX_HALF_W)
        events[f"opp_penalty_area_{suf}"] = (x > (half - BOX_DEPTH)) & (y <= BOX_HALF_W)
        events[f"own_six_yard_box_{suf}"] = (x < -(half - SIX_DEPTH)) & (y <= SIX_HALF_W)
        events[f"opp_six_yard_box_{suf}"] = (x > (half - SIX_DEPTH)) & (y <= SIX_HALF_W)
    return events


# --------------------------------------------------------------------------------------
# Tier 2.5 - squad units
# --------------------------------------------------------------------------------------
def add_unit_flags(events: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Unit sizes on the pitch at each event's frame, plus starter flags.

    Consumes the Silver players table, which already resolved the two things that used to
    be done here: the modal OBSERVED position (the roster's player_role reads 'SUB' for
    every substitute) and the on-pitch intervals (playing_time.sequences exists in only 11
    of 20 matches, so Silver falls back to playing_time.total).

    On-pitch membership changes only at substitutions, so unit sizes are computed once per
    (match, team) segment boundary and mapped onto events with searchsorted rather than
    recomputed per row.
    """
    segs = []
    for r in players.itertuples():
        for lo, hi in json.loads(r.on_pitch_intervals):
            segs.append(dict(match_id=r.match_id, team_id=r.team_id, player_id=r.player_id,
                             position=r.position, start_frame=lo, end_frame=hi))
    segs = pd.DataFrame(segs)

    starters = set(zip(players.loc[players.is_starter, "match_id"],
                       players.loc[players.is_starter, "player_id"]))
    events["is_starter"] = [
        (int(m), int(p)) in starters if pd.notna(p) else False
        for m, p in zip(events.match_id, events.player_id)
    ]

    for c in ["team_back_line_size", "team_front_line_size", "team_pivot_size"]:
        events[c] = np.nan

    for (mid, team), g in segs.groupby(["match_id", "team_id"]):
        bounds = np.unique(np.concatenate([g.start_frame.to_numpy(), g.end_frame.to_numpy()]))
        back, front, pivot = [], [], []
        for b in bounds:
            on = g[(g.start_frame <= b) & (g.end_frame >= b)]
            back.append(int(on.position.isin(BACK_LINE).sum()))
            front.append(int(on.position.isin(FRONT_LINE).sum()))
            pivot.append(int(on.position.isin(PIVOT).sum()))
        idx = events.index[(events.match_id == mid) & (events.team_id == team)]
        if len(idx) == 0:
            continue
        slot = np.clip(np.searchsorted(bounds, events.loc[idx, "frame_start"].to_numpy(float),
                                       side="right") - 1, 0, len(bounds) - 1)
        events.loc[idx, "team_back_line_size"] = np.asarray(back)[slot]
        events.loc[idx, "team_front_line_size"] = np.asarray(front)[slot]
        events.loc[idx, "team_pivot_size"] = np.asarray(pivot)[slot]

    events["is_starting_cb_pair"] = (events.player_position.isin(CENTRE_BACKS)
                                     & events.is_starter)
    return events


# --------------------------------------------------------------------------------------
# Tier 2.6 - captain (external data only)
# --------------------------------------------------------------------------------------
def load_captains() -> dict:
    """Captain identity is NOT in SkillCorner open data - _match.json's player_role holds
    a position ('RW', 'GK', 'SUB'), never an armband flag, and no dynamic-events column
    encodes it. This reads an optional hand-maintained data/captains.json of the form
    {"<match_id>": {"<team_id>": <player_id>}}. Absent the file, is_captain is all-False
    and captain questions stay unresolved rather than silently guessing."""
    if not CAPTAINS_FILE.exists():
        return {}
    raw = json.load(open(CAPTAINS_FILE, encoding="utf-8"))
    return {(int(mid), int(tid)): int(pid)
            for mid, teams in raw.items() for tid, pid in teams.items()}


def add_roster_attributes(events: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Shirt number and substitute status, joined from the Silver players table.

    These used to be joined only inside scripts/test_all_questions.py at query time, so they
    never reached Gold. Retrieval worked because the test suite did its own merge - but the
    query parser reads Gold, so its field vocabulary reported both as "unavailable in this
    dataset" and could not have answered Q2 ("their number 9") or Q80 (substitutes). Gold is
    what retrieval filters on, so anything retrieval filters on belongs here.
    """
    roster = players[["match_id", "player_id", "number", "is_substitute"]].drop_duplicates(
        subset=["match_id", "player_id"])
    merged = events[["match_id", "player_id"]].merge(roster, on=["match_id", "player_id"],
                                                     how="left")
    events["number"] = merged["number"].to_numpy()
    events["is_substitute"] = merged["is_substitute"].fillna(False).astype(bool).to_numpy()
    return events


def add_captain_flag(events: pd.DataFrame) -> pd.DataFrame:
    caps = load_captains()
    if not caps:
        events["is_captain"] = False
        return events
    events["is_captain"] = [
        caps.get((int(m), int(t))) == p if pd.notna(t) and pd.notna(p) else False
        for m, t, p in zip(events.match_id, events.team_id, events.player_id)
    ]
    return events


# --------------------------------------------------------------------------------------
def build_enriched(events, matches, players, verbose: bool = False):
    """Apply every Tier 1 / Tier 2 derivation. Silver in, Gold out."""
    goals = build_goal_timeline(events, matches)
    if verbose:
        v = validate_goal_timeline(goals, matches)
        print(f"[gold] goal timeline: {len(goals)} goals, "
              f"{int(v.ok.sum())}/{len(v)} matches match the official score")
        if not v.ok.all():
            print(v[~v.ok].to_string(index=False))
    events = add_goal_relative_time(events, goals)
    events = add_outcome_windows(events, goals)
    events = add_possession_chains(events)
    events = add_zone_flags(events, matches)
    events = add_unit_flags(events, players)
    events = add_roster_attributes(events, players)
    events = add_captain_flag(events)
    return events.copy(), goals          # copy() defragments after many column inserts


def load_enriched(verbose: bool = False):
    """Read the Gold events table. Build it first with scripts/build_gold.py.

    Returns (events, matches, goals) - `matches` replaces the old raw _match.json dict.
    """
    need = [GOLD / "events_enriched.parquet", GOLD / "goals.parquet"]
    missing = [f.name for f in need if not f.exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing Gold tables {missing} under {GOLD}. "
            f"Run: python scripts/build_silver.py && python scripts/build_gold.py")
    events = pd.read_parquet(GOLD / "events_enriched.parquet")
    goals = pd.read_parquet(GOLD / "goals.parquet")
    matches = pd.read_parquet(SILVER / "matches.parquet")
    if verbose:
        print(f"[gold] {len(events):,} events x {events.shape[1]} cols, {len(goals)} goals")
    return events, matches, goals


if __name__ == "__main__":
    print("enrich.py is a library of derivations; run scripts/build_gold.py to apply them.")
