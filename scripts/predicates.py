#!/usr/bin/env python
"""
Phase-2 predicate harness: confirm candidate events against raw tracking.

Phase 1 filters events down to a candidate set; this module fetches each candidate's
tracking window and asks a predicate whether the moment actually matches. See
docs/tier3_lazy_retrieval_plan.md.

The harness exists so every predicate gets the same four things right, rather than each one
reimplementing them:

1. **Coordinate frame.** Tracking is absolute; events are mirrored per (match, team, period)
   and flip at halftime. The harness applies the measured sign before the predicate sees a
   single coordinate, so predicates always work in the event frame where +x is the direction
   that candidate's team attacks. Getting this wrong reflects positions through the origin
   and silently inverts answers.
2. **Insufficient data is not absence.** A window that cannot be measured returns
   status="insufficient_data", never matched=False. The negative/absence gate turns absence
   into a positive answer, so conflating the two manufactures findings.
3. **Evidence, not booleans.** Results carry the measured value and the frames that justify
   them - which are also the clip's in/out points, and what makes a wrong answer replayable.
4. **Sampling.** Parsing is ~96% of fetch cost, so density is a per-predicate choice:
   instantaneous predicates sample a few frames, time-series ones take the full window.

Usage:
    from predicates import WindowPolicy, evaluate, player_in_own_box

    hits = evaluate(candidates, player_in_own_box, WindowPolicy(pre=0, post=0))
    hits[hits.matched]

    python scripts/predicates.py --selftest
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame_index import MIN_COVERAGE, FrameIndex, Window  # noqa: E402
from mirror_sign import load_signs  # noqa: E402

BOX_DEPTH, BOX_HALF_W = 16.5, 20.16
SIX_DEPTH, SIX_HALF_W = 5.5, 9.16

OK = "ok"
INSUFFICIENT = "insufficient_data"
ERROR = "error"


# --------------------------------------------------------------------------------------
@dataclass
class EventContext:
    """Everything a predicate needs about the candidate, beyond the frames themselves."""

    match_id: int
    team_id: int
    period: int
    frame_start: int
    frame_end: int
    sign: int
    player_id: int | None = None
    pitch_length: float = 105.0
    pitch_width: float = 68.0
    row: object = None          # the full event row, for predicates that need more
    teams: dict = field(default_factory=dict)   # {player_id: team_id} for this match
    positions: dict = field(default_factory=dict)   # {player_id: position} for this match

    def players_at(self, players: dict, position: str | tuple,
                   own_team: bool = True) -> dict:
        """Of a {player_id: (x, y)} map, those playing a given position (or positions).

        Tracking carries neither team nor position, so "the winger" / "the back four" can
        only be resolved through this lookup from the Silver players table.
        """
        want = {position} if isinstance(position, str) else set(position)
        side = self.team_id
        return {p: xy for p, xy in players.items()
                if self.positions.get(p) in want
                and ((self.teams.get(p) == side) == own_team)}

    def teammates(self, players: dict) -> dict:
        """Of a {player_id: (x, y)} map, those on this event's own team."""
        return {p: xy for p, xy in players.items()
                if self.teams.get(p) == self.team_id}

    def opponents(self, players: dict) -> dict:
        t = self.teams
        return {p: xy for p, xy in players.items()
                if p in t and t[p] != self.team_id}

    @property
    def box_edge_x(self) -> float:
        """x beyond which a point is inside a penalty area, on this match's pitch."""
        return self.pitch_length / 2.0 - BOX_DEPTH

    @property
    def six_edge_x(self) -> float:
        return self.pitch_length / 2.0 - SIX_DEPTH

    def goal_x(self, own: bool = False) -> float:
        """x of a goal line in the event frame (+x is the direction this team attacks)."""
        return -self.pitch_length / 2.0 if own else self.pitch_length / 2.0


@dataclass
class PredicateResult:
    matched: bool = False
    value: float | None = None
    evidence_frames: list[int] = field(default_factory=list)
    status: str = OK
    detail: str = ""

    @classmethod
    def insufficient(cls, detail: str = "") -> "PredicateResult":
        return cls(matched=False, status=INSUFFICIENT, detail=detail)


@dataclass
class WindowPolicy:
    """How many frames around the event a predicate needs, and at what density.

    See docs/tier3_lazy_retrieval_plan.md for the per-gate table; e.g. set-piece geometry
    wants pre=10/post=30, a foot race pre=10/post=50, an instantaneous spatial check 0/0.
    """

    pre: int = 0
    post: int = 0
    samples: int | None = None       # None = every frame in the span
    min_coverage: float | None = None
    """Share of frames that must carry tracking before the predicate is even run.

    None uses frame_index.MIN_COVERAGE (70%), which suits windows tight around live play.
    Set-piece windows must lower it: a corner window spans the dead-ball setup, so a large
    share of its frames legitimately carry no tracking, and the default gate rejects 79% of
    corners before the predicate sees them. Lowering it does not weaken the
    insufficient-data guarantee - the predicate still returns insufficient when it cannot
    find what it needs (e.g. no locatable delivery), which is the check that actually
    matters for negative/absence questions.
    """

    def span(self, ctx: EventContext) -> tuple[int, int]:
        return ctx.frame_start - self.pre, ctx.frame_end + self.post


# --------------------------------------------------------------------------------------
def signed_players(frame: dict, sign: int, detected_only: bool = False) -> dict:
    """{player_id: (x, y)} in the event frame.

    is_detected=False means the position was extrapolated, not observed. Measured against
    the event table's own coordinates, extrapolated positions agree to within 0.5m only
    88.6% of the time versus 98.5% for detected ones, so pass detected_only=True for
    precision-critical checks and accept the coverage loss (~13% of player-frames).
    """
    out = {}
    for p in frame.get("player_data") or []:
        x, y = p.get("x"), p.get("y")
        if x is None or y is None:
            continue
        if detected_only and not p.get("is_detected"):
            continue
        out[p["player_id"]] = (x * sign, y * sign)
    return out


def detection_rate(frame: dict) -> float:
    """Share of this frame's players whose position was observed rather than extrapolated."""
    players = frame.get("player_data") or []
    if not players:
        return 0.0
    return sum(1 for p in players if p.get("is_detected")) / len(players)


def signed_ball(frame: dict, sign: int) -> tuple[float, float] | None:
    b = frame.get("ball_data") or {}
    if b.get("x") is None or b.get("y") is None:
        return None
    return b["x"] * sign, b["y"] * sign


def in_penalty_area(x: float, y: float, edge_x: float, own: bool) -> bool:
    return (x < -edge_x if own else x > edge_x) and abs(y) <= BOX_HALF_W


# --------------------------------------------------------------------------------------
_window_cache: dict = {}


def fetch_for(ctx: EventContext, policy: WindowPolicy, cache: bool = True) -> Window:
    lo, hi = policy.span(ctx)
    key = (ctx.match_id, lo, hi, policy.samples)
    if cache and key in _window_cache:
        return _window_cache[key]
    idx = FrameIndex.load(ctx.match_id)
    win = (idx.fetch_sampled(lo, hi, policy.samples) if policy.samples
           else idx.fetch_window(lo, hi))
    if cache:
        _window_cache[key] = win
    return win


def clear_cache() -> None:
    _window_cache.clear()


def evaluate(candidates: pd.DataFrame, predicate: Callable, policy: WindowPolicy,
             matches: pd.DataFrame | None = None, players: pd.DataFrame | None = None,
             progress: bool = False, **params) -> pd.DataFrame:
    """Run `predicate` over every candidate event, returning the candidates plus results.

    Adds columns: matched, value, status, detail, evidence_lo, evidence_hi, n_frames.
    Rows with status != "ok" have matched=False but must NOT be read as negatives.
    """
    signs = load_signs()
    pitch = {}
    if matches is not None:
        pitch = {int(r.match_id): (r.pitch_length, r.pitch_width)
                 for r in matches.itertuples()}
    # Tracking identifies players but not their team, so predicates that need to tell
    # attackers from defenders depend on this lookup from the Silver players table.
    team_of: dict = {}
    pos_of: dict = {}
    if players is not None:
        for r in players.itertuples():
            team_of.setdefault(int(r.match_id), {})[int(r.player_id)] = int(r.team_id)
            pos_of.setdefault(int(r.match_id), {})[int(r.player_id)] = r.position

    out = []
    for n, r in enumerate(candidates.itertuples()):
        mid, team, period = int(r.match_id), int(r.team_id), int(r.period)
        sign = signs.get((mid, team, period))
        if sign is None:
            out.append(dict(matched=False, value=np.nan, status=ERROR,
                            detail="no mirror sign for this (match, team, period)",
                            evidence_lo=np.nan, evidence_hi=np.nan, n_frames=0))
            continue

        pl, pw = pitch.get(mid, (105.0, 68.0))
        ctx = EventContext(match_id=mid, team_id=team, period=period,
                           frame_start=int(r.frame_start), frame_end=int(r.frame_end),
                           sign=sign,
                           player_id=int(r.player_id) if pd.notna(r.player_id) else None,
                           pitch_length=pl, pitch_width=pw, row=r,
                           teams=team_of.get(mid, {}),
                           positions=pos_of.get(mid, {}))
        win = fetch_for(ctx, policy)
        threshold = (policy.min_coverage if policy.min_coverage is not None
                     else MIN_COVERAGE)
        if win.coverage < threshold:
            res = PredicateResult.insufficient(
                f"coverage {win.coverage:.0%} < {threshold:.0%}")
        else:
            try:
                res = predicate(win, ctx, **params)
            except Exception as exc:                       # a predicate bug is not a "no"
                res = PredicateResult(matched=False, status=ERROR,
                                      detail=f"{type(exc).__name__}: {exc}")
        out.append(dict(
            matched=bool(res.matched), value=res.value, status=res.status,
            detail=res.detail,
            evidence_lo=min(res.evidence_frames) if res.evidence_frames else np.nan,
            evidence_hi=max(res.evidence_frames) if res.evidence_frames else np.nan,
            n_frames=len(win.usable)))
        if progress and n % 250 == 0 and n:
            print(f"  ... {n}/{len(candidates)}", file=sys.stderr)

    return pd.concat([candidates.reset_index(drop=True),
                      pd.DataFrame(out)], axis=1)


# --------------------------------------------------------------------------------------
# Reference predicate
# --------------------------------------------------------------------------------------
def player_in_own_box(win: Window, ctx: EventContext) -> PredicateResult:
    """Was the event's own player inside their own penalty area?

    Deliberately reproduces the Gold column own_penalty_area_start, which is computed from
    the event row's coordinates rather than from tracking. Agreement between the two is an
    end-to-end check of the whole phase-2 chain - frame index, mirror sign, and coordinate
    handling - against a value derived completely independently.
    """
    if ctx.player_id is None:
        return PredicateResult.insufficient("event has no player_id")
    # Anchor on the event's START frame specifically. Taking win.usable[0] instead silently
    # measured the window midpoint under a samples=1 policy, which for an off-ball run is
    # ~1.2s of sprinting - several metres, and enough to flip the answer.
    frame = win.frame_at(ctx.frame_start)
    if frame is None:
        return PredicateResult.insufficient(
            f"frame {ctx.frame_start} not in the fetched window")
    pos = signed_players(frame, ctx.sign).get(ctx.player_id)
    if pos is None:
        return PredicateResult.insufficient("player not tracked in the start frame")
    x, y = pos
    return PredicateResult(matched=in_penalty_area(x, y, ctx.box_edge_x, own=True),
                           value=x, evidence_frames=[frame["frame"]],
                           detail=f"x={x:.1f} y={y:.1f}")


# --------------------------------------------------------------------------------------
def _selftest(n: int = 400, margin_m: float = 1.0, concord_m: float = 1.0) -> None:
    """Validate the chain end-to-end by reproducing a Gold column from tracking.

    The Gold column own_penalty_area_start is computed from the EVENT table's coordinates;
    this predicate computes the same thing from TRACKING. Agreement therefore exercises the
    whole phase-2 chain - frame index, mirror sign, coordinate handling - against a value
    derived completely independently.

    Two sources of legitimate disagreement are separated out rather than counted as failures,
    because both measure upstream data quality rather than this code:

    1. **Concordance.** Dynamic Events are "produced by combining SkillCorner Tracking v3 +
       Wyscout event data" (skillcorner_schema.md), so event coordinates are a fusion and not
       a copy of tracking. Measured: 1.1% of rows put the same player >1m apart in the two
       sources, from a mix of player-identity errors (~0.3%, consistent with the documented
       ~97% identity accuracy), frame alignment, and unexplained residue. Where the two
       sources disagree about where the player was, they cannot be expected to agree about
       which side of a line he was on.
    2. **Boundary margin.** A candidate within a metre of the box edge can legitimately fall
       on opposite sides given sub-metre noise.

    The real assertion is on concordant, non-borderline candidates, where the chain must be
    essentially exact.
    """
    from enrich import load_enriched
    from frame_index import available_matches

    events, matches, _goals = load_enriched()
    have = set(available_matches())
    signs = load_signs()

    pool = events[(events.match_id.isin(have))
                  & events.player_id.notna()
                  & events.x_start.notna()].copy()

    half = pool.match_id.map(dict(zip(matches.match_id, matches.pitch_length / 2.0)))
    edge_x = half - BOX_DEPTH
    pool["boundary_margin"] = np.minimum((pool.x_start + edge_x).abs(),
                                         (pool.y_start.abs() - BOX_HALF_W).abs())

    inbox = pool[pool.own_penalty_area_start].sample(n // 2, random_state=0)
    outbox = pool[~pool.own_penalty_area_start].sample(n // 2, random_state=0)
    cand = pd.concat([inbox, outbox]).sample(frac=1, random_state=1)

    print(f"[selftest] {len(cand)} candidates "
          f"({int(cand.own_penalty_area_start.sum())} in own box per Gold)")
    res = evaluate(cand, player_in_own_box, WindowPolicy(pre=0, post=0),
                   matches=matches)

    # How far apart do the two sources place this player at this frame?
    discord = []
    for r in res.itertuples():
        sign = signs.get((int(r.match_id), int(r.team_id), int(r.period)))
        if sign is None or r.status != OK:
            discord.append(np.nan)
            continue
        w = FrameIndex.load(int(r.match_id)).fetch_at([int(r.frame_start)])
        p = next((q for q in (w.frames[0].get("player_data") or [])
                  if q["player_id"] == int(r.player_id)), None) if w.frames else None
        discord.append(np.hypot(r.x_start - p["x"] * sign, r.y_start - p["y"] * sign)
                       if p else np.nan)
    res["source_gap"] = discord

    ok = res[res.status == OK].copy()
    ok["agree"] = ok.matched == ok.own_penalty_area_start
    concordant = ok[ok.source_gap <= concord_m]
    clean = concordant[concordant.boundary_margin >= margin_m]

    print(f"[selftest] status: {res.status.value_counts().to_dict()}")
    print(f"[selftest] sources place the player >{concord_m}m apart on "
          f"{int((ok.source_gap > concord_m).sum())}/{len(ok)} candidates "
          f"({(ok.source_gap > concord_m).mean():.1%}) - upstream fusion, not this chain")
    print()
    print(f"[selftest] all candidates:                     {ok.agree.mean():.2%} "
          f"({int(ok.agree.sum())}/{len(ok)})")
    print(f"[selftest] concordant sources:                 "
          f"{concordant.agree.mean():.2%} "
          f"({int(concordant.agree.sum())}/{len(concordant)})")
    print(f"[selftest] concordant AND >={margin_m}m from edge: "
          f"{clean.agree.mean():.2%} ({int(clean.agree.sum())}/{len(clean)})  <-- assertion")

    bad = clean[~clean.agree]
    if len(bad):
        print()
        print(f"[selftest] {len(bad)} unexplained disagreements:")
        cols = ["match_id", "event_type", "x_start", "y_start", "boundary_margin",
                "source_gap", "own_penalty_area_start", "matched", "detail"]
        print(bad[cols].head(8).to_string(index=False))

    if clean.agree.mean() < 0.99:
        print("[selftest] FAIL - chain does not reproduce the independent column",
              file=sys.stderr)
        sys.exit(1)
    print("[selftest] PASS")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("-n", type=int, default=400)
    ap.add_argument("--concord", type=float, default=1.0,
                    help="metres of event-vs-tracking disagreement above which a candidate "
                         "is excluded as upstream fusion noise")
    ap.add_argument("--margin", type=float, default=1.0,
                    help="metres from the box edge below which a candidate is treated as "
                         "borderline (upstream noise, not a chain error)")
    args = ap.parse_args()
    if args.selftest:
        _selftest(args.n, args.margin, args.concord)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
