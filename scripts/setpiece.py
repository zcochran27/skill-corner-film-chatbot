#!/usr/bin/env python
"""
Set-piece geometry predicates - the first real phase-2 metrics.

Corners are the cheapest place to prove the lazy design: a small, well-delimited candidate
set (141 corners across 20 matches), short fixed windows, and cheap per-frame arithmetic.
Two test-set questions go from approximate to exact:

  Q68  "all corners where no player attacked the near post"
       was: a run_ahead_of_the_ball off-ball-run proxy, since no near-post tag exists
       now: actual occupancy of the near-post zone during the delivery's flight

  Q70  "every corner they conceded where no defender contested the first ball"
       was: "any defensive engagement somewhere in the same phase"
       now: the first player to reach the ball, and whether a defender was close to it

Both are negative/absence questions, which is exactly where `insufficient_data` has to stay
distinct from `False` - "no defender contested it" and "we could not see who contested it"
are different answers, and only one of them is a finding.

Delivery detection
The event row's frame_start is NOT the delivery. It is the first tagged event of the corner,
which is sometimes the reception ~1.8s after the kick and sometimes the taker's own
possession before it (measured: delivery - contact ranges from -50 to +5 frames, median -18).
So the delivery is found from the ball track instead - the last frame where the ball sits in
the corner region before leaving it - which resolves 92% of corners. The other 8% return
insufficient_data rather than a guess.

Usage:
    python scripts/setpiece.py --q68
    python scripts/setpiece.py --q70
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicates import (  # noqa: E402
    OK, EventContext, PredicateResult, WindowPolicy, evaluate,
    signed_ball, signed_players,
)
from frame_index import Window  # noqa: E402

# Corner region: the ball is within this box of a corner flag before being delivered.
CORNER_DX, CORNER_DY = 8.0, 8.0
#: Near-post zone, as a box in front of the near post (which sits at |y| = 3.66).
#: Lateral is measured on the corner's side: NEAR_POST_Y_MIN excludes the central goalmouth,
#: since a player standing at y=+1 in front of the keeper has not "attacked the near post".
#: These are DEFINITIONAL, not measured - see --sensitivity for how the answer moves with
#: them. Defaults give a ~6m x 6m box straddling the near post.
NEAR_POST_DEPTH = 6.0
NEAR_POST_Y_MIN, NEAR_POST_Y_MAX = 1.5, 8.0
#: Flight window after delivery in which a near-post run must occur (2.5s).
FLIGHT_FRAMES = 25
#: A player is "on the ball" within this distance; a defender "contests" within this radius.
CONTACT_RADIUS, CONTEST_RADIUS = 1.5, 2.5
#: Ignore the first few frames after delivery so the taker is not the first contact.
CONTACT_SKIP = 5

#: A corner window spans the dead-ball setup, so much of it carries no tracking by design.
#: The predicate's own delivery check is the real gate, not frame coverage.
CORNER_WINDOW = WindowPolicy(pre=250, post=80, min_coverage=0.10)


def _is_defending_corner(ctx: EventContext) -> bool:
    """True when this row's team conceded the corner (game_interruption_before ends
    '_against'), which puts the corner at their OWN goal rather than the one they attack."""
    gi = getattr(ctx.row, "game_interruption_before", None)
    return str(gi).endswith("_against")


def find_delivery(win: Window, ctx: EventContext) -> tuple[int, float] | None:
    """(frame, corner_side_y) of the delivery, or None.

    The delivery is the LAST frame where the ball is in the corner region - after that it
    leaves. Searching both before and after frame_start matters because the first tagged
    event is sometimes the taker's own possession rather than the reception.
    """
    own_end = _is_defending_corner(ctx)
    goal_x = ctx.goal_x(own=own_end)
    half_w = ctx.pitch_width / 2.0
    last = None
    for frame in win.usable:
        ball = signed_ball(frame, ctx.sign)
        if ball is None:
            continue
        bx, by = ball
        near_goal_line = (bx > goal_x - CORNER_DX if not own_end
                          else bx < goal_x + CORNER_DX)
        if near_goal_line and abs(by) > half_w - CORNER_DY:
            last = (frame["frame"], by)
    if last is None:
        return None
    # The ball must actually leave the corner afterwards, or this is not a delivery.
    if sum(1 for f in win.usable if f["frame"] > last[0] + CONTACT_SKIP) < 10:
        return None
    return last


# --------------------------------------------------------------------------------------
def attacked_near_post(win: Window, ctx: EventContext,
                       depth_max: float = NEAR_POST_DEPTH,
                       lateral_min: float = NEAR_POST_Y_MIN,
                       lateral_max: float = NEAR_POST_Y_MAX) -> PredicateResult:
    """Did any attacker occupy the near-post zone during the delivery's flight? (Q68)

    matched=True means someone DID attack the near post; Q68 asks for the negation.
    value is always the nearest approach to the near post in metres, so callers can rank by
    it instead of thresholding - see the comment at the return for why that matters.
    """
    if not ctx.teams:
        return PredicateResult.insufficient("no player->team map supplied")
    delivery = find_delivery(win, ctx)
    if delivery is None:
        return PredicateResult.insufficient("could not locate the corner delivery")
    d_frame, corner_y = delivery
    side = 1.0 if corner_y > 0 else -1.0
    own_end = _is_defending_corner(ctx)
    goal_x = ctx.goal_x(own=own_end)

    hit_frames, hit_players, closest = set(), set(), np.inf
    for frame in win.usable:
        if not (d_frame <= frame["frame"] <= d_frame + FLIGHT_FRAMES):
            continue
        players = signed_players(frame, ctx.sign)
        # For a conceded corner the row's team defends, so the attackers are the opponents.
        attackers = ctx.opponents(players) if own_end else ctx.teammates(players)
        for pid, (x, y) in attackers.items():
            depth = (goal_x - x) if not own_end else (x - goal_x)
            lateral = y * side
            if (0 <= depth <= depth_max
                    and lateral_min <= lateral <= lateral_max):
                hit_frames.add(frame["frame"])
                hit_players.add(pid)
            # Distance to the near post itself, for the "how close did they get" value.
            closest = min(closest, float(np.hypot(depth, lateral - 3.66)))

    # value is ALWAYS the nearest approach to the near post, in metres, so results can be
    # RANKED rather than thresholded. The zone test below still populates `matched` for
    # reference, but ranking is the primary answer: "near post" is a fuzzy football concept,
    # and any fixed zone moves the answer between 12% and 48% (--sensitivity). Ranking has
    # no arbitrary cutoff - the coach reads down the list and stops where they like - and
    # stage 5 needs ranking logic regardless.
    if hit_players:
        return PredicateResult(
            matched=True, value=float(closest),
            evidence_frames=[min(hit_frames), max(hit_frames)],
            detail=f"{len(hit_players)} attacker(s) reached the near-post zone "
                   f"(closest {closest:.1f}m), side={side:+.0f}")
    return PredicateResult(matched=False, value=float(closest),
                           evidence_frames=[d_frame, d_frame + FLIGHT_FRAMES],
                           detail=f"nearest attacker came within {closest:.1f}m of the "
                                  f"near post, side={side:+.0f}")


def first_ball_contested(win: Window, ctx: EventContext) -> PredicateResult:
    """Was a defender close to the first player who reached the delivery? (Q70)

    matched=True means the first ball WAS contested; Q70 asks for the negation.
    """
    if not ctx.teams:
        return PredicateResult.insufficient("no player->team map supplied")
    delivery = find_delivery(win, ctx)
    if delivery is None:
        return PredicateResult.insufficient("could not locate the corner delivery")
    d_frame, _ = delivery
    own_end = _is_defending_corner(ctx)

    for frame in win.usable:
        if frame["frame"] < d_frame + CONTACT_SKIP:
            continue
        ball = signed_ball(frame, ctx.sign)
        if ball is None:
            continue
        bx, by = ball
        players = signed_players(frame, ctx.sign)
        near = [(np.hypot(x - bx, y - by), pid) for pid, (x, y) in players.items()]
        if not near:
            continue
        dist, pid = min(near)
        if dist > CONTACT_RADIUS:
            continue
        # First contact found. Defenders are the row's own team on a conceded corner.
        defenders = ctx.teammates(players) if own_end else ctx.opponents(players)
        d_near = [np.hypot(x - bx, y - by) for p, (x, y) in defenders.items() if p != pid]
        closest_def = min(d_near) if d_near else np.inf
        contested = (ctx.teams.get(pid) == ctx.team_id if own_end
                     else ctx.teams.get(pid) != ctx.team_id) or closest_def <= CONTEST_RADIUS
        return PredicateResult(
            matched=bool(contested), value=float(closest_def),
            evidence_frames=[d_frame, frame["frame"]],
            detail=f"first contact at frame {frame['frame']}, "
                   f"nearest defender {closest_def:.1f}m")
    return PredicateResult.insufficient("no player reached the ball within the window")


# --------------------------------------------------------------------------------------
def _corners(events: pd.DataFrame, tag: str) -> pd.DataFrame:
    """One row per corner: the first tagged event of each (match, phase)."""
    c = events[events.game_interruption_before == tag]
    return (c.sort_values("frame_start")
            .groupby(["match_id", "phase_index"], observed=True).first().reset_index())


def _report_ranked(name: str, question: str, res: pd.DataFrame, unit: str) -> None:
    """Rank by `value` descending - no threshold, the coach reads down the list."""
    ok = res[res.status == OK].sort_values("value", ascending=False)
    print()
    print(f"=== {name} (ranked, no threshold) ===")
    print(f"question: {question}")
    print(f"corners evaluated: {len(res)} | resolved: {len(ok)} "
          f"({len(ok) / len(res):.0%}) | insufficient: {int((res.status != OK).sum())}")
    print(f"ranked by {unit}; top 8:")
    print(ok[["match_id", "frame_start", "value", "detail"]].head(8).to_string(index=False))


def _report(name: str, question: str, res: pd.DataFrame, negate: bool) -> None:
    ok = res[res.status == OK]
    hits = ok[~ok.matched] if negate else ok[ok.matched]
    print(f"\n=== {name} ===")
    print(f"question: {question}")
    print(f"corners evaluated: {len(res)} | resolved: {len(ok)} "
          f"({len(ok) / len(res):.0%}) | insufficient: {int((res.status != OK).sum())}")
    print(f"ANSWER: {len(hits)} corners ({len(hits) / len(ok):.0%} of resolved), "
          f"across {hits.match_id.nunique()}/{res.match_id.nunique()} matches")
    cols = ["match_id", "frame_start", "matched", "value", "detail"]
    print(hits[cols].head(5).to_string(index=False))


def _sensitivity(cand, matches, players) -> None:
    """The near-post zone is a definitional choice. Show the answer is not an artifact of
    one arbitrary threshold by sweeping the two parameters that matter."""
    print()
    print("=== Q68 sensitivity to the near-post zone definition ===")
    print(f"{'lateral range':>16} {'depth':>6} {'resolved':>9} {'no near-post run':>18}")
    for lat_min, lat_max in [(0.5, 9.16), (1.5, 8.0), (2.5, 7.0)]:
        for depth in (5.0, 6.0, 8.0):
            res = evaluate(cand, attacked_near_post, CORNER_WINDOW,
                           matches=matches, players=players,
                           depth_max=depth, lateral_min=lat_min, lateral_max=lat_max)
            ok = res[res.status == OK]
            miss = int((~ok.matched).sum())
            print(f"{lat_min:6.1f}-{lat_max:<9.1f} {depth:6.1f} {len(ok):9d} "
                  f"{miss:11d} ({miss / len(ok):.0%})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q68", action="store_true", help="corners with no near-post attacker")
    ap.add_argument("--q70", action="store_true", help="conceded corners, first ball uncontested")
    ap.add_argument("--sensitivity", action="store_true",
                    help="sweep the near-post zone definition")
    args = ap.parse_args()
    if not (args.q68 or args.q70 or args.sensitivity):
        ap.print_help()
        return

    from enrich import load_enriched
    from frame_index import available_matches

    events, matches, _goals = load_enriched()
    players = pd.read_parquet(Path(__file__).resolve().parent.parent
                              / "data" / "silver" / "players.parquet")
    have = set(available_matches())
    events = events[events.match_id.isin(have)]

    if args.q68:
        cand = _corners(events, "corner_for")
        res = evaluate(cand, attacked_near_post, CORNER_WINDOW,
                       matches=matches, players=players)
        _report_ranked("Q68", "corners where NO player attacked the near post", res,
                       unit="metres from the near post at closest approach (larger = "
                            "less attacked)")

    if args.sensitivity:
        _sensitivity(_corners(events, "corner_for"), matches, players)

    if args.q70:
        cand = _corners(events, "corner_against")
        res = evaluate(cand, first_ball_contested, CORNER_WINDOW,
                       matches=matches, players=players)
        _report("Q70", "conceded corners where NO defender contested the first ball",
                res, negate=True)


if __name__ == "__main__":
    main()
