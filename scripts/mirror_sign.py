#!/usr/bin/env python
"""
Resolve the coordinate mirror sign between tracking and event data.

Tracking is in ABSOLUTE pitch coordinates. Events are MIRRORED per row so that the row's
own team always attacks +x, and the mirroring flips at halftime because teams switch ends.
So for every (match, team, period):

    event_xy = sign * tracking_xy,   sign in {+1, -1}

Getting this wrong does not raise - it silently reflects every position through the origin,
which turns "in their own box" into "in the opponent's box" and quietly inverts the answer.
It is the single most likely source of wrong results in Tier 3, so the sign is MEASURED per
group against real data rather than inferred from home/away or a stated attacking direction.

Method: for a sample of that group's events, look up the same player in the same tracking
frame and compare. A +1 vote if event x matches tracking x, -1 if it matches the negation.
The sign is the majority, and `confidence` is its share - anything below CONFIDENCE_MIN is
reported as unresolved rather than guessed at.

Output is data/silver/mirror_signs.parquet: silver owns derived access structures (the same
rule that puts the tracking frame index there), because this makes Bronze readable rather
than adding football meaning.

Usage:
    from mirror_sign import load_signs, to_event_frame
    signs = load_signs()
    sign = signs[(match_id, team_id, period)]
    x, y = to_event_frame(player["x"], player["y"], sign)

    python scripts/mirror_sign.py            # report resolved signs
    python scripts/mirror_sign.py --rebuild  # re-measure and rewrite the table
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame_index import FrameIndex, available_matches  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SILVER = ROOT / "data" / "silver"
SIGNS_FILE = SILVER / "mirror_signs.parquet"

#: Coordinates are derived from the same tracking, so a match is near-exact. 0.5m is
#: generous; a genuine mismatch is tens of metres away.
TOLERANCE_M = 0.5
#: Samples per (match, team, period). Votes are near-unanimous, so this is cheap insurance.
SAMPLES_PER_GROUP = 40
#: Below this share of agreeing votes the group is left unresolved rather than guessed.
CONFIDENCE_MIN = 0.90


class UnresolvedSignError(Exception):
    """A (match, team, period) group's mirror sign could not be measured confidently."""


def _vote(ev_x: float, ev_y: float, tr_x: float, tr_y: float) -> int | None:
    """+1 if the event coords equal the tracking coords, -1 if they equal the negation."""
    if any(v is None for v in (tr_x, tr_y)):
        return None
    if abs(ev_x - tr_x) <= TOLERANCE_M and abs(ev_y - tr_y) <= TOLERANCE_M:
        return 1
    if abs(ev_x + tr_x) <= TOLERANCE_M and abs(ev_y + tr_y) <= TOLERANCE_M:
        return -1
    return None


def resolve_group(idx: FrameIndex, group: pd.DataFrame,
                  n_samples: int = SAMPLES_PER_GROUP) -> dict:
    """Measure the sign for one (match, team, period) group."""
    usable = group.dropna(subset=["x_start", "y_start", "player_id", "frame_start"])
    if usable.empty:
        return dict(sign=0, confidence=0.0, n_votes=0, n_sampled=0)

    sample = usable.sample(min(n_samples, len(usable)), random_state=0)
    votes = []
    for r in sample.itertuples():
        frame = int(r.frame_start)
        win = idx.fetch_at([frame])
        if not win.frames:
            continue
        players = win.frames[0].get("player_data") or []
        match = next((p for p in players if p.get("player_id") == int(r.player_id)), None)
        if match is None:
            continue
        v = _vote(float(r.x_start), float(r.y_start), match.get("x"), match.get("y"))
        if v is not None:
            votes.append(v)

    if not votes:
        return dict(sign=0, confidence=0.0, n_votes=0, n_sampled=len(sample))
    arr = np.asarray(votes)
    pos, neg = int((arr > 0).sum()), int((arr < 0).sum())
    sign = 1 if pos >= neg else -1
    return dict(sign=sign, confidence=max(pos, neg) / len(arr),
                n_votes=len(arr), n_sampled=len(sample))


def build_mirror_signs(events: pd.DataFrame, strict: bool = True) -> pd.DataFrame:
    """Measure the sign for every (match, team, period) with tracking available."""
    have = set(available_matches())
    rows = []
    for (mid, team, period), g in events.groupby(["match_id", "team_id", "period"],
                                                 observed=True):
        mid = int(mid)
        if mid not in have:
            continue
        idx = FrameIndex.load(mid)
        res = resolve_group(idx, g)
        rows.append(dict(match_id=mid, team_id=int(team), period=int(period), **res))

    signs = pd.DataFrame(rows)
    if signs.empty:
        return signs

    bad = signs[(signs.sign == 0) | (signs.confidence < CONFIDENCE_MIN)]
    if len(bad) and strict:
        raise UnresolvedSignError(
            f"{len(bad)} (match, team, period) groups below "
            f"{CONFIDENCE_MIN:.0%} confidence:\n{bad.to_string(index=False)}")

    # Sanity check: within a match+period the two teams must have opposite signs, and a
    # team's sign must flip between periods. Either failing means the measurement is wrong
    # in a way that per-group confidence alone would not catch.
    problems = []
    for (mid, period), g in signs.groupby(["match_id", "period"]):
        if len(g) == 2 and g.sign.iloc[0] == g.sign.iloc[1]:
            problems.append(f"match {mid} period {period}: both teams have sign "
                            f"{g.sign.iloc[0]}, expected opposites")
    for (mid, team), g in signs.groupby(["match_id", "team_id"]):
        if len(g) == 2 and g.sign.iloc[0] == g.sign.iloc[1]:
            problems.append(f"match {mid} team {team}: sign {g.sign.iloc[0]} in both "
                            f"periods, expected a halftime flip")
    if problems and strict:
        raise UnresolvedSignError("mirror-sign consistency checks failed:\n  "
                                  + "\n  ".join(problems))
    signs.attrs["problems"] = problems
    return signs


def load_signs() -> dict:
    """{(match_id, team_id, period): sign}. Build with --rebuild if missing."""
    if not SIGNS_FILE.exists():
        raise FileNotFoundError(
            f"{SIGNS_FILE} missing. Run: python scripts/mirror_sign.py --rebuild")
    df = pd.read_parquet(SIGNS_FILE)
    return {(int(r.match_id), int(r.team_id), int(r.period)): int(r.sign)
            for r in df.itertuples()}


def to_event_frame(x: float, y: float, sign: int) -> tuple[float, float]:
    """Convert absolute tracking coordinates into the event frame for a given group,
    where +x is the direction that group's team attacks."""
    return x * sign, y * sign


def frame_to_event_frame(frame: dict, sign: int) -> dict:
    """Sign a whole tracking frame's player and ball coordinates in one pass."""
    out = dict(frame)
    out["player_data"] = [
        {**p, "x": (p["x"] * sign if p.get("x") is not None else None),
         "y": (p["y"] * sign if p.get("y") is not None else None)}
        for p in (frame.get("player_data") or [])
    ]
    ball = frame.get("ball_data") or {}
    out["ball_data"] = {**ball,
                        "x": (ball["x"] * sign if ball.get("x") is not None else None),
                        "y": (ball["y"] * sign if ball.get("y") is not None else None)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="re-measure and write the table")
    args = ap.parse_args()

    if args.rebuild:
        from enrich import load_enriched
        events, _matches, _goals = load_enriched()
        print(f"[mirror_sign] measuring {SAMPLES_PER_GROUP} samples per "
              f"(match, team, period) ...")
        signs = build_mirror_signs(events)
        SILVER.mkdir(parents=True, exist_ok=True)
        signs.to_parquet(SIGNS_FILE, index=False)
        print(f"[mirror_sign] resolved {len(signs)} groups across "
              f"{signs.match_id.nunique()} matches")
        print(f"[mirror_sign] confidence: min {signs.confidence.min():.1%}, "
              f"mean {signs.confidence.mean():.1%}")
        print(f"[mirror_sign] sign distribution: "
              f"{signs.sign.value_counts().to_dict()}")
        print(f"[mirror_sign] wrote {SIGNS_FILE}")
        return

    df = pd.read_parquet(SIGNS_FILE)
    print(df.head(12).to_string(index=False))
    print(f"\n{len(df)} groups | confidence min {df.confidence.min():.1%} "
          f"| signs {df.sign.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
