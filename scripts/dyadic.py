#!/usr/bin/env python
"""
Two-player relational predicates - foot races (Q59).

  Q59  "all foot races between their centre back and an opposing striker in behind"

The last genuinely dyadic question, and the most parameter-sensitive thing built so far: it
depends on two players' movement being fast, sustained, and aligned over the same window,
each of which is a continuum. So this measures, prints the distribution, and RANKS - it does
not threshold. See docs/master_plan.md 4e for why: the last three tracking bugs all produced
plausible-looking numbers rather than errors, and two were visible only in the spread.

Why the pair is resolved lazily rather than precomputed: all opponent pairs is 11 x 11 x
44k frames x 20 matches, most of which no question ever asks about. The pair here is known
at query time - the striker is the event's own player, and the centre back is found in the
frames - so nothing needs materialising.

Anchor: off-ball runs tagged `behind` by a forward (406 across 20 matches, median 2.4s, none
slower than the 'running' band). That gives the striker's identity directly, which the
turnover anchor in defensive.py could not.

Usage:
    python scripts/dyadic.py --distribution
    python scripts/dyadic.py --q59
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
    BAND_HSR, BAND_RUNNING, OK, EventContext, PredicateResult, WindowPolicy,
    evaluate, player_series, signed_players, velocity,
)

FORWARDS = ("CF", "LF", "RF")
#: Off-ball-run subtypes that exist to move a marker rather than to receive in behind.
PULLING_SUBTYPES = ("dropping_off", "pulling_wide", "pulling_half_space", "coming_short")
CENTRE_BACKS = ("CB", "LCB", "RCB")
BACK_LINE = ("CB", "LCB", "RCB", "LB", "RB", "LWB", "RWB")
#: Frames of context. A race starts before the run is tagged and runs past its end.
RACE_WINDOW = WindowPolicy(pre=10, post=30, min_coverage=0.50)
#: Minimum frames both players must be tracked for the comparison to mean anything.
MIN_FRAMES = 12
#: Velocity alignment: 1.0 is identical heading, 0 is perpendicular.
MIN_COSINE = 0.60


def foot_race(win: Window, ctx: EventContext) -> PredicateResult:
    """Measure the closest thing to a foot race between the runner and an opposing CB.

    ctx.team_id is the ATTACKING team (the row is the striker's own off-ball run), so in the
    event frame the striker attacks +x and the chasing centre back runs toward +x as well -
    toward his own goal. Both moving +x is therefore the expected signature, not a bug.

    value = race_score, the LOWER of the two peak speeds in km/h. Taking the minimum is what
    makes it a race: a striker sprinting away from a jogging defender is a different event.
    """
    if ctx.player_id is None:
        return PredicateResult.insufficient("run has no player_id")
    if not ctx.positions:
        return PredicateResult.insufficient("no player->position map supplied")

    sf, sx, sy = player_series(win, ctx, ctx.player_id)
    if len(sf) < MIN_FRAMES:
        return PredicateResult.insufficient("striker not tracked for long enough")
    s_speed, s_vx, s_vy = velocity(sf, sx, sy)

    first = signed_players(win.usable[0], ctx.sign)
    backs = ctx.players_at(first, CENTRE_BACKS, own_team=False)
    if not backs:
        return PredicateResult.insufficient("no opposing centre back on the pitch")

    best = None
    for pid in backs:
        cf, cx, cy = player_series(win, ctx, pid)
        if len(cf) < MIN_FRAMES:
            continue
        # Compare only frames both players are tracked in.
        shared = np.intersect1d(sf[:-1], cf[:-1])
        if len(shared) < MIN_FRAMES:
            continue
        si = np.searchsorted(sf[:-1], shared)
        ci = np.searchsorted(cf[:-1], shared)
        c_speed, c_vx, c_vy = velocity(cf, cx, cy)

        sv = np.stack([s_vx[si], s_vy[si]])
        cv = np.stack([c_vx[ci], c_vy[ci]])
        norms = np.linalg.norm(sv, axis=0) * np.linalg.norm(cv, axis=0)
        cosine = np.where(norms > 0, (sv * cv).sum(axis=0) / np.where(norms > 0, norms, 1),
                          np.nan)

        # Separation measured on shared frames only.
        s_pos = np.stack([np.interp(shared, sf, sx), np.interp(shared, sf, sy)])
        c_pos = np.stack([np.interp(shared, cf, cx), np.interp(shared, cf, cy)])
        sep = np.linalg.norm(s_pos - c_pos, axis=0)

        # The racing portion: both moving toward the striker's attacking goal (+x).
        racing = (s_vx[si] > 0) & (c_vx[ci] > 0)
        if not racing.any():
            continue
        peak_s = float(np.nanmax(s_speed[si][racing]))
        peak_c = float(np.nanmax(c_speed[ci][racing]))
        rec = dict(cb=pid, score=min(peak_s, peak_c), striker=peak_s, back=peak_c,
                   cosine=float(np.nanmean(cosine[racing])),
                   sep_start=float(sep[0]), sep_end=float(sep[-1]),
                   frames=(int(shared[0]), int(shared[-1])))
        if best is None or rec["score"] > best["score"]:
            best = rec

    if best is None:
        return PredicateResult.insufficient(
            "no centre back tracked alongside the runner for long enough")

    aligned = best["cosine"] >= MIN_COSINE
    gain = best["sep_end"] - best["sep_start"]
    return PredicateResult(
        matched=bool(aligned and best["score"] >= BAND_RUNNING),
        value=best["score"],
        evidence_frames=list(best["frames"]),
        detail=f"striker {best['striker']:.1f} / CB {best['back']:.1f} km/h, "
               f"alignment {best['cosine']:+.2f}, separation {best['sep_start']:.1f}m -> "
               f"{best['sep_end']:.1f}m ({gain:+.1f}m)")


def dragged_out_of_position(win: Window, ctx: EventContext,
                            baselines: dict | None = None) -> PredicateResult:
    """How far an opposing centre back was pulled AWAY FROM HIS OWN LINE (Q56).

    The first version of this measured each centre back's distance from his own defending
    baseline and ranked by it. That measured something real and answered the wrong question:
    the top hits were teams pressing high, where the whole back line had pushed up 25m
    together. Inspecting the raw coordinates of one case made it obvious - all three of that
    team's centre backs were 22-26m from baseline at once. A collective line push is a
    pressing scheme, not a striker dragging someone.

    So the measure is RELATIVE to the rest of the back line:

        relative excursion = this defender's displacement from his own baseline
                             - the median displacement of his back-line colleagues

    A whole line stepping up nets out to roughly zero. One defender leaving while the others
    hold does not, which is what "dragged out of position" actually describes.

    Two further conditions, both about the striker being the cause rather than a coincidence:
      - the centre back must move TOWARD the forward (he is following him, not drifting)
      - the forward must move first, by cross-correlation lag

    value = relative excursion in metres. Ranked, not thresholded.
    """
    if ctx.player_id is None:
        return PredicateResult.insufficient("run has no player_id")
    if not baselines:
        return PredicateResult.insufficient("no player baselines supplied")

    ff, fx, fy = player_series(win, ctx, ctx.player_id)
    if len(ff) < MIN_FRAMES:
        return PredicateResult.insufficient("forward not tracked for long enough")

    first = signed_players(win.usable[0], ctx.sign)
    backs = ctx.players_at(first, CENTRE_BACKS, own_team=False)
    line = ctx.players_at(first, BACK_LINE, own_team=False)
    if not backs or len(line) < 3:
        return PredicateResult.insufficient(
            "no opposing centre back, or too few defenders to define a line")

    # Displacement from baseline for every defender, on a common set of frames.
    disp, series = {}, {}
    for pid in line:
        base = baselines.get((ctx.match_id, pid))
        if base is None:
            continue
        cfr, cx, cy = player_series(win, ctx, pid)
        if len(cfr) < MIN_FRAMES:
            continue
        # Coordinates arrive in the RUNNER's frame; the defender's baseline is in his own
        # team's frame, which is its mirror. Negate to compare like with like.
        d = np.hypot(-cx - base[0], -cy - base[1])
        disp[pid] = (cfr, d)
        series[pid] = (cfr, cx, cy)
    if len(disp) < 3:
        return PredicateResult.insufficient("too few defenders with baselines")

    grid = disp[next(iter(disp))][0]
    stacked = {pid: np.interp(grid, fr, d) for pid, (fr, d) in disp.items()}

    best = None
    for pid in backs:
        if pid not in stacked:
            continue
        others = [v for q, v in stacked.items() if q != pid]
        if not others:
            continue
        line_disp = np.median(np.stack(others), axis=0)
        relative = stacked[pid] - line_disp
        # The GROWTH in line-relative displacement during the window, not its level. A
        # defender who was already out of line when the run began was not dragged by it -
        # measuring the level put two such cases at the top, both with the peak at frame 0.
        gained = relative - relative[0]
        k = int(np.nanargmax(gained))
        peak_rel = float(gained[k])

        # Did he move toward the forward? Compare his distance to the forward at the start
        # of the window with the distance at his peak excursion.
        cfr, cx, cy = series[pid]
        cxi, cyi = np.interp(grid, cfr, cx), np.interp(grid, cfr, cy)
        fxi, fyi = np.interp(grid, ff, fx), np.interp(grid, ff, fy)
        to_fwd = np.hypot(cxi - fxi, cyi - fyi)
        closed = float(to_fwd[0] - to_fwd[k])

        lead = np.nan
        f_base = baselines.get((ctx.match_id, ctx.player_id))
        if f_base is not None:
            f_disp = np.interp(grid, ff, np.hypot(fx - f_base[0], fy - f_base[1]))
            a, b = f_disp - f_disp.mean(), relative - np.nanmean(relative)
            if a.std() > 0 and np.nanstd(b) > 0:
                lead = float(np.argmax(np.correlate(np.nan_to_num(b), a, mode="full"))
                             - (len(a) - 1))
        rec = dict(cb=pid, rel=peak_rel, closed=closed, lead=lead,
                   frames=(int(grid[0]), int(grid[-1])))
        if best is None or rec["rel"] > best["rel"]:
            best = rec

    if best is None:
        return PredicateResult.insufficient("no centre back comparable to his line")

    followed = best["closed"] > 0
    led = bool(best["lead"] > 0) if not np.isnan(best["lead"]) else False
    return PredicateResult(
        matched=bool(followed and led and best["rel"] >= 5.0),
        value=float(best["rel"]),
        evidence_frames=list(best["frames"]),
        detail=f"pulled {best['rel']:+.1f}m further out of line, "
               f"closed {best['closed']:+.1f}m on the forward"
               + (f", forward led by {best['lead'] / 10:.1f}s" if led else ", no clear lead"))


# --------------------------------------------------------------------------------------
def pulling_runs(events: pd.DataFrame, tracked: set) -> pd.DataFrame:
    """Off-ball runs by a forward whose purpose is to move a marker."""
    obr = events[(events.event_type == "off_ball_run")
                 & (events.event_subtype.isin(PULLING_SUBTYPES))
                 & (events.player_position.isin(FORWARDS))
                 & (events.match_id.isin(tracked))]
    return obr.sort_values("frame_start").drop_duplicates(
        subset=["match_id", "player_id", "team_possession_id"], keep="first")


def runs_in_behind(events: pd.DataFrame, tracked: set) -> pd.DataFrame:
    """Off-ball runs tagged 'behind' by a forward - the striker's identity comes free."""
    obr = events[(events.event_type == "off_ball_run")
                 & (events.event_subtype == "behind")
                 & (events.player_position.isin(FORWARDS))
                 & (events.match_id.isin(tracked))]
    return obr.sort_values("frame_start").drop_duplicates(
        subset=["match_id", "player_id", "team_possession_id"], keep="first")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q59", action="store_true")
    ap.add_argument("--q56", action="store_true")
    ap.add_argument("--distribution", action="store_true")
    args = ap.parse_args()
    if not (args.q59 or args.q56 or args.distribution):
        ap.print_help()
        return

    from enrich import load_enriched
    from frame_index import available_matches

    events, matches, _goals = load_enriched()
    players = pd.read_parquet(Path(__file__).resolve().parent.parent
                              / "data" / "silver" / "players.parquet")
    tracked = set(available_matches())
    if args.q56:
        from tracking_base import load_baselines
        c56 = pulling_runs(events, tracked)
        r56 = evaluate(c56, dragged_out_of_position, RACE_WINDOW, matches=matches,
                       players=players, baselines=load_baselines())
        ok56 = r56[r56.status == OK]
        print(f"pulling runs evaluated: {len(r56)} | resolved: {len(ok56)} "
              f"({len(ok56) / max(len(r56), 1):.0%})")
        print()
        print("CB excursion, multiples of his own spread:")
        print(ok56.value.describe(percentiles=[.5, .9]).round(2).to_string())
        led = ok56.detail.str.contains("forward led")
        print()
        print(f"forward moved first: {int(led.sum())}/{len(ok56)} ({led.mean():.0%})")
        print()
        print("=== Q56 (ranked, no threshold) ===")
        print("ranked by how much further out of line he was pulled; top 8:")
        print(ok56[ok56.matched].sort_values("value", ascending=False)
              [["match_id", "frame_start", "value", "detail"]].head(8).to_string(index=False))
        if not (args.q59 or args.distribution):
            return

    cand = runs_in_behind(events, tracked)
    res = evaluate(cand, foot_race, RACE_WINDOW, matches=matches, players=players)
    ok = res[res.status == OK]

    print(f"runs in behind evaluated: {len(res)} | resolved: {len(ok)} "
          f"({len(ok) / max(len(res), 1):.0%}) | "
          f"insufficient: {int((res.status != OK).sum())}")

    if args.distribution:
        print()
        print("race_score = lower of the two peak speeds, km/h:")
        print(ok.value.describe(percentiles=[.1, .25, .5, .75, .9]).round(1).to_string())
        cos = ok.detail.str.extract(r"alignment ([+-][\d.]+)")[0].astype(float)
        print()
        print("velocity alignment (1.0 = identical heading):")
        print(cos.describe(percentiles=[.1, .5, .9]).round(2).to_string())
        print()
        for lo, hi, name in [(0, BAND_RUNNING, "below running <15"),
                             (BAND_RUNNING, BAND_HSR, "running 15-20"),
                             (BAND_HSR, 1e9, "hsr+ >20")]:
            n = int(((ok.value >= lo) & (ok.value < hi)).sum())
            print(f"  both players {name:18s} {n:4d} ({n / max(len(ok), 1):.0%})")

    if args.q59:
        ranked = ok.sort_values("value", ascending=False)
        print()
        print("=== Q59 (ranked, no threshold) ===")
        print("question: foot races between their centre back and a striker in behind")
        print("ranked by race_score DESCENDING - most genuine races first; top 10:")
        print(ranked[["match_id", "frame_start", "value", "detail"]]
              .head(10).to_string(index=False))


if __name__ == "__main__":
    main()
