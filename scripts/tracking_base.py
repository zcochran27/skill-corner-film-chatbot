#!/usr/bin/env python
"""
The thin eager tracking base: per-player positional baselines.

"Dragged out of position" (Q56) is meaningless without knowing where a player normally
stands, and that is a corpus statistic - it cannot be computed from the candidate's own
window, because the excursion you are trying to measure is inside that window. So it is
precomputed here and lives in Gold, exactly the split docs/data_architecture.md mandates:
cheap, reusable, non-parameterised measures are eager; everything else is a lazy sweep.

Scope note: docs/tier3_tracking_plan.md also proposed eager per-frame team-shape scalars
(block width, height, centroid). Those are NOT built, because their justification was
Q17 - and Q17 turned out to be answerable from _phases_of_play.csv without tracking at all
(master_plan 4h). Building them now would be speculative. The parse loop below is the
expensive part and is easy to extend if a future question needs them.

Method
Each frame's `possession.group` (home/away) says which side has the ball, so a player's
DEFENDING position is his position on frames where his own team is not in possession -
which is when "out of position" is a meaningful accusation. Coordinates are signed into
that player's own team's attacking frame so +x is always the way they attack.

Median, not mean: one 40m excursion should not move the baseline it is later measured
against.

Usage:
    python scripts/tracking_base.py            # build data/gold/player_baselines.parquet
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame_index import FrameIndex, available_matches  # noqa: E402
from mirror_sign import load_signs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SILVER = ROOT / "data" / "silver"
GOLD = ROOT / "data" / "gold"
OUT = GOLD / "player_baselines.parquet"

#: Below this many defending frames a median is not worth trusting (60s of play).
MIN_FRAMES = 600


def build(verbose: bool = True) -> pd.DataFrame:
    matches = pd.read_parquet(SILVER / "matches.parquet")
    players = pd.read_parquet(SILVER / "players.parquet")
    signs = load_signs()

    side_of = {}          # (match_id, "home"/"away") -> team_id
    for m in matches.itertuples():
        # The literal values are "home team" / "away team", not "home" / "away" - checked,
        # not assumed. Guessing them silently accumulated nothing at all.
        side_of[(int(m.match_id), "home team")] = int(m.home_team_id)
        side_of[(int(m.match_id), "away team")] = int(m.away_team_id)
    team_of = {(int(r.match_id), int(r.player_id)): int(r.team_id)
               for r in players.itertuples()}
    pos_of = {(int(r.match_id), int(r.player_id)): r.position
              for r in players.itertuples()}

    side_of_keys = {"home team", "away team"}
    rows = []
    for mid in available_matches():
        idx = FrameIndex.load(mid)
        period_2_start = int(matches.loc[matches.match_id == mid,
                                         "period_2_start"].iloc[0])
        acc = defaultdict(lambda: ([], []))
        win = idx.fetch_window(int(idx.frames[0]), int(idx.frames[-1]))
        for frame in win.usable:
            grp = (frame.get("possession") or {}).get("group")
            # ~27% of usable frames have no possessing side (loose ball); skip them rather
            # than attributing the moment to either team.
            if grp not in side_of_keys:
                continue
            in_poss = side_of.get((mid, grp))
            period = 1 if frame["frame"] < period_2_start else 2
            for p in frame["player_data"]:
                pid = p.get("player_id")
                x, y = p.get("x"), p.get("y")
                if pid is None or x is None or y is None:
                    continue
                team = team_of.get((mid, int(pid)))
                if team is None or team == in_poss:
                    continue                     # only measure while DEFENDING
                sign = signs.get((mid, team, period))
                if sign is None:
                    continue
                xs, ys = acc[(int(pid), team)]
                xs.append(x * sign)
                ys.append(y * sign)

        for (pid, team), (xs, ys) in acc.items():
            if len(xs) < MIN_FRAMES:
                continue
            rows.append(dict(match_id=mid, player_id=pid, team_id=team,
                             position=pos_of.get((mid, pid)),
                             n_frames=len(xs),
                             base_x=float(np.median(xs)), base_y=float(np.median(ys)),
                             spread_x=float(np.percentile(np.abs(np.asarray(xs)
                                                                 - np.median(xs)), 50)),
                             spread_y=float(np.percentile(np.abs(np.asarray(ys)
                                                                 - np.median(ys)), 50))))
        if verbose:
            print(f"[base] match {mid}: {len(acc)} players", flush=True)

    return pd.DataFrame(rows)


def load_baselines() -> dict:
    """{(match_id, player_id): (base_x, base_y, spread_x, spread_y)}."""
    if not OUT.exists():
        raise FileNotFoundError(
            f"{OUT} missing. Run: python scripts/tracking_base.py")
    df = pd.read_parquet(OUT)
    return {(int(r.match_id), int(r.player_id)):
            (r.base_x, r.base_y, r.spread_x, r.spread_y) for r in df.itertuples()}


def main() -> None:
    df = build()
    GOLD.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"\n[base] {len(df)} (match, player) baselines over "
          f"{df.match_id.nunique()} matches -> {OUT}")
    print(f"[base] median defending frames per player: {df.n_frames.median():.0f}")
    print()
    print("sanity check - defending base_x by position (negative = deeper, own half):")
    print(df.groupby("position").base_x.agg(["size", "median"]).round(1)
          .sort_values("median").to_string())


if __name__ == "__main__":
    main()
