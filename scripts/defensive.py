#!/usr/bin/env python
"""
Defensive off-ball movement from tracking - recovery runs (Q66).

  Q66  "all moments their winger tracked back but failed to make a recovery run"

Unresolvable from events, and not for the usual reason. All ten off-ball-run subtypes in
the schema (behind, coming_short, cross_receiver, dropping_off, overlap, pulling_half_space,
pulling_wide, run_ahead_of_the_ball, support, underlap) describe ATTACKING movement. There
is no defensive-run tag anywhere, so the whole concept of defensive work rate is missing
from the event vocabulary - not just this one question.

Two structural problems this question has that the set-piece ones did not:

1. **No event anchor.** The question is about a player doing nothing, so there is no event
   row for him to filter on - the exact limitation flagged in
   docs/tier3_lazy_retrieval_plan.md §4. The anchor used instead is the moment his team
   LOSES the ball, which is an event, and the winger is then located by position within the
   tracking frames.
2. **"Recovery run" is an intensity, not a state.** Effort is a continuum, so this reports a
   measured speed and RANKS by it rather than thresholding, for the same reason Q68 does:
   any cutoff would be a football judgement presented as a measurement.

Usage:
    python scripts/defensive.py --distribution   # what the measurements actually look like
    python scripts/defensive.py --q66
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame_index import Window  # noqa: E402
from predicates import (  # noqa: E402
    OK, EventContext, PredicateResult, WindowPolicy, evaluate, signed_players,
)

WINGERS = ("LW", "RW", "LM", "RM")
#: Seconds of play after the turnover in which a recovery run should happen.
RECOVERY_FRAMES = 100
#: Smoothing span before differentiating - the upstream README flags speed as needing it,
#: and positions are extrapolated on ~13% of player-frames.
SMOOTH_FRAMES = 5
#: Net retreat (metres toward own goal) that counts as having tracked back at all.
TRACKED_BACK_M = 5.0
#: Speed bands from the schema's own cheat sheet, km/h.
BAND_RUNNING, BAND_HSR, BAND_SPRINT = 15.0, 20.0, 25.0

RECOVERY_WINDOW = WindowPolicy(pre=0, post=RECOVERY_FRAMES, min_coverage=0.50)


def _series(win: Window, ctx: EventContext, player_id: int):
    """(frames, x, y) for one player across the window, in the event frame."""
    fs, xs, ys = [], [], []
    for frame in win.usable:
        pos = signed_players(frame, ctx.sign).get(player_id)
        if pos is None:
            continue
        fs.append(frame["frame"])
        xs.append(pos[0])
        ys.append(pos[1])
    return np.asarray(fs), np.asarray(xs), np.asarray(ys)


def _smooth(a: np.ndarray, n: int = SMOOTH_FRAMES) -> np.ndarray:
    """Centred rolling mean with correct edge handling.

    np.convolve(..., mode="same") zero-pads, which drags the first and last n/2 samples
    toward the origin - a player at x=30 appears to jump 30m in 0.1s, i.e. ~1000 km/h.
    That produced a median 'peak speed' of 245 km/h before this was fixed.
    """
    if len(a) < 2:
        return a
    return (pd.Series(a).rolling(n, center=True, min_periods=1).mean().to_numpy())


def recovery_run(win: Window, ctx: EventContext,
                 positions: tuple = WINGERS) -> PredicateResult:
    """Measure the hardest recovery effort by this team's winger after a turnover.

    Own goal is at -x in the event frame, so tracking back is movement in -x.

    value  = peak sustained speed while retreating, km/h (the rankable quantity)
    matched = True if that reached the schema's 'running' band (15 km/h); reported for
              reference only, since the real answer is the ranking
    """
    if not ctx.positions:
        return PredicateResult.insufficient("no player->position map supplied")
    if not win.usable:
        return PredicateResult.insufficient("no tracked frames in the window")

    # Locate this team's wingers from the first usable frame.
    first = signed_players(win.usable[0], ctx.sign)
    wingers = ctx.players_at(first, positions, own_team=True)
    if not wingers:
        return PredicateResult.insufficient("no winger of this team on the pitch")

    best = None
    for pid in wingers:
        fs, xs, ys = _series(win, ctx, pid)
        if len(fs) < SMOOTH_FRAMES * 2:
            continue
        sx, sy = _smooth(xs), _smooth(ys)
        dt = np.diff(fs) / 10.0                     # frames -> seconds
        dt[dt == 0] = np.nan
        vx = np.diff(sx) / dt
        speed = np.hypot(np.diff(sx), np.diff(sy)) / dt * 3.6    # km/h
        retreating = vx < 0                          # moving toward own goal
        net_retreat = float(sx[0] - sx[-1])          # +ve = ended nearer own goal
        peak = float(np.nanmax(speed[retreating])) if retreating.any() else 0.0
        rec = dict(pid=pid, peak=peak, net=net_retreat,
                   dist=float(np.nansum(np.hypot(np.diff(sx), np.diff(sy)))))
        # Report the winger who worked hardest; the question is about the unit, and taking
        # the minimum would flag a team whenever its second winger was on the far side.
        if best is None or rec["peak"] > best["peak"]:
            best = rec

    if best is None:
        return PredicateResult.insufficient("winger not tracked for long enough")

    tracked_back = best["net"] >= TRACKED_BACK_M
    if not tracked_back:
        return PredicateResult(
            matched=False, value=best["peak"],
            evidence_frames=[win.usable[0]["frame"], win.usable[-1]["frame"]],
            status=OK,
            detail=f"did not track back (net {best['net']:+.1f}m), "
                   f"peak retreat {best['peak']:.1f} km/h")
    return PredicateResult(
        matched=best["peak"] >= BAND_RUNNING, value=best["peak"],
        evidence_frames=[win.usable[0]["frame"], win.usable[-1]["frame"]],
        detail=f"tracked back {best['net']:.1f}m, peak retreat {best['peak']:.1f} km/h, "
               f"covered {best['dist']:.0f}m")


# --------------------------------------------------------------------------------------
def _candidates(events: pd.DataFrame, tracked: set) -> pd.DataFrame:
    """Turnovers: this team just lost the ball, so its wingers should be recovering."""
    pp = events[(events.event_type == "player_possession")
                & (events.end_type == "possession_loss")
                & (events.match_id.isin(tracked))]
    # Only losses in the opponent half - a winger caught upfield is what the question is
    # about; a turnover in your own third is a different situation entirely.
    pp = pp[pp.x_start > 0]
    # One recovery opportunity per lost possession. Without this, 749 turnovers collapse to
    # 661 possessions and the same moment is returned several times - the clip-dedup problem
    # CLAUDE.md lists as open, which team_possession_id (Tier 1) already solves at this grain.
    return (pp.sort_values("frame_start")
            .drop_duplicates(subset="team_possession_id", keep="first"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q66", action="store_true")
    ap.add_argument("--distribution", action="store_true",
                    help="show the measured speed distribution before any thresholding")
    ap.add_argument("-n", type=int, default=400, help="candidates to sample")
    args = ap.parse_args()
    if not (args.q66 or args.distribution):
        ap.print_help()
        return

    from enrich import load_enriched
    from frame_index import available_matches

    events, matches, _goals = load_enriched()
    players = pd.read_parquet(Path(__file__).resolve().parent.parent
                              / "data" / "silver" / "players.parquet")
    cand = _candidates(events, set(available_matches()))
    if args.n and len(cand) > args.n:
        cand = cand.sample(args.n, random_state=0)

    res = evaluate(cand, recovery_run, RECOVERY_WINDOW, matches=matches, players=players)
    ok = res[res.status == OK]

    print(f"turnovers evaluated: {len(res)} | resolved: {len(ok)} "
          f"({len(ok) / len(res):.0%}) | insufficient: {int((res.status != OK).sum())}")

    if args.distribution:
        print()
        print("peak retreat speed, km/h (all resolved):")
        print(ok.value.describe(percentiles=[.1, .25, .5, .75, .9]).round(1).to_string())
        tracked = ok[~ok.detail.str.startswith("did not track back")]
        print()
        print(f"tracked back >= {TRACKED_BACK_M}m: {len(tracked)}/{len(ok)} "
              f"({len(tracked) / len(ok):.0%})")
        print("peak retreat speed among those, vs the schema's own bands:")
        for name, lo in [("walk/jog <15", 0), ("running 15-20", BAND_RUNNING),
                         ("hsr 20-25", BAND_HSR), ("sprinting >25", BAND_SPRINT)]:
            hi = {0: BAND_RUNNING, BAND_RUNNING: BAND_HSR,
                  BAND_HSR: BAND_SPRINT, BAND_SPRINT: 1e9}[lo]
            n = int(((tracked.value >= lo) & (tracked.value < hi)).sum())
            print(f"  {name:16s} {n:4d} ({n / max(len(tracked), 1):.0%})")

    if args.q66:
        tracked = ok[~ok.detail.str.startswith("did not track back")]
        ranked = tracked.sort_values("value")      # least effort first
        print()
        print("=== Q66 (ranked, no threshold) ===")
        print("question: winger tracked back but failed to make a recovery run")
        print(f"tracked back at all: {len(tracked)} of {len(ok)} resolved turnovers")
        print("ranked by peak retreat speed ASCENDING - least effort first; top 10:")
        print(ranked[["match_id", "frame_start", "value", "detail"]]
              .head(10).to_string(index=False))


if __name__ == "__main__":
    main()
