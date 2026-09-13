#!/usr/bin/env python
"""
Random access into a match's tracking JSONL - the foundation of Tier 3's lazy sweep.

Phase 1 event filters produce a candidate set (median 156 events); this module fetches only
those candidates' frames rather than scanning 1.2M. See docs/tier3_lazy_retrieval_plan.md.

The frame -> byte-offset arrays are built by scripts/build_silver.py and live in
data/silver/tracking_index/. This module loads them, validates them against the Bronze
source file, and serves windows.

Two fetch modes, because the sampling optimisation matters:
  fetch_window(lo, hi)    every frame in a span - for genuinely time-series predicates
                          (foot race, recovery run) using cheap per-frame metrics
  fetch_sampled(lo, hi, n)  n evenly spaced frames - for predicates that need the shape at
                          a few instants. Convex hull costs 625us/frame, so a 156-candidate
                          job over 50-frame windows costs 4.9s at full density and 0.5s at
                          5 samples. Density is a per-predicate choice.

Cost model, measured: JSON parsing is ~96% of a fetch. Seek + readline for a 156-window job
is 33 ms; parsing the same frames is 837 ms, because a full 22-player frame is ~1.9 KB and
costs ~86 us to parse. So the lever is parsing FEWER FRAMES, not reading fewer bytes - which
is what makes fetch_sampled the dominant optimisation rather than a minor one. Installing
orjson (optional) is the other easy win; this module uses it automatically if present.

Every fetch reports coverage. ~27% of frames across the file carry an empty player_data list
(ball out of play), though real event windows average 99.8% coverage because events only
happen while the ball is live. A window that cannot be measured MUST be distinguishable from
one where the thing did not happen - the negative/absence gate turns absence into a positive
answer, so conflating the two manufactures findings.

Usage:
    from frame_index import FrameIndex, sample_frames
    idx = FrameIndex.load(1874553)
    w = idx.fetch_window(21830, 21918)
    if w.sufficient:
        ...

    python scripts/frame_index.py --benchmark
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

try:                                    # optional accelerator, see requirements.txt
    import orjson

    def _loads(b: bytes) -> dict:
        return orjson.loads(b)
    FAST_JSON = True
except ImportError:
    def _loads(b: bytes) -> dict:
        return json.loads(b)
    FAST_JSON = False

ROOT = Path(__file__).resolve().parent.parent
BRONZE = ROOT / "data" / "bronze" / "matches"
SILVER = ROOT / "data" / "silver"
INDEX_DIR = SILVER / "tracking_index"

#: A window below this share of usable frames is reported as insufficient rather than
#: producing a (probably false) negative.
MIN_COVERAGE = 0.70


class IndexStaleError(Exception):
    """The Bronze tracking file changed since the index was built."""


@dataclass
class Window:
    """Frames fetched for one candidate event, plus how measurable they are."""

    match_id: int
    frame_lo: int
    frame_hi: int
    frames: list[dict]
    requested: int

    @property
    def usable(self) -> list[dict]:
        """Frames carrying full player tracking (always 22 players when present)."""
        return [f for f in self.frames if f.get("player_data")]

    @property
    def coverage(self) -> float:
        return len(self.usable) / self.requested if self.requested else 0.0

    @property
    def sufficient(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    def frame_at(self, frame_number: int) -> dict | None:
        """The frame with this exact number, or None.

        Predicates that mean a specific instant ("where was he when the event started")
        MUST use this rather than taking the first frame in the window: a sampled policy
        does not necessarily include the endpoints, and even a full window spans the whole
        event, over which a sprinting player moves several metres.
        """
        for f in self.frames:
            if f.get("frame") == frame_number:
                return f
        return None

    def __len__(self) -> int:
        return len(self.frames)


def sample_frames(lo: int, hi: int, n: int) -> list[int]:
    """n evenly spaced frame numbers across [lo, hi] inclusive.

    NOTE n == 1 gives the MIDPOINT, not the start. That is the right sample for "what was
    the shape during this event", and the wrong one for "where was he when it started" -
    use Window.frame_at(ctx.frame_start) for the latter.
    """
    if n <= 0 or hi < lo:
        return []
    if n == 1:
        return [(lo + hi) // 2]
    return [int(round(x)) for x in np.linspace(lo, hi, min(n, hi - lo + 1))]


@dataclass
class FrameIndex:
    """frame -> byte offset for one match's tracking file."""

    match_id: int
    path: Path
    frames: np.ndarray
    offsets: np.ndarray
    _cache: dict = field(default_factory=dict, repr=False)

    _loaded: dict = None  # class-level registry, set below

    # ---------------------------------------------------------------------------------
    @classmethod
    def load(cls, match_id: int, validate: bool = True) -> "FrameIndex":
        """Load (and memoise) the index for one match."""
        if cls._loaded is None:
            cls._loaded = {}
        if match_id in cls._loaded:
            return cls._loaded[match_id]

        fpath = INDEX_DIR / f"{match_id}_frames.npy"
        opath = INDEX_DIR / f"{match_id}_offsets.npy"
        if not fpath.exists() or not opath.exists():
            raise FileNotFoundError(
                f"No tracking index for match {match_id} under {INDEX_DIR}. "
                f"Run: scripts/fetch_match_data.sh {match_id} --tracking "
                f"&& python scripts/build_silver.py")

        src = BRONZE / str(match_id) / f"{match_id}_tracking_extrapolated.jsonl"
        if not src.exists():
            raise FileNotFoundError(f"Bronze tracking file missing: {src}")

        idx = cls(match_id=match_id, path=src,
                  frames=np.load(fpath), offsets=np.load(opath))
        if validate:
            idx.validate()
        cls._loaded[match_id] = idx
        return idx

    def validate(self) -> None:
        """Byte offsets are invalidated by any rewrite of the source file, so check the
        fingerprint Silver recorded rather than trusting the index blindly."""
        meta_path = SILVER / "tracking_index.parquet"
        if not meta_path.exists():
            return
        meta = pd.read_parquet(meta_path)
        row = meta[meta.match_id == self.match_id]
        if row.empty:
            return
        row = row.iloc[0]
        if "source_bytes" in row and pd.notna(row.source_bytes):
            actual = self.path.stat().st_size
            if int(row.source_bytes) != actual:
                raise IndexStaleError(
                    f"match {self.match_id}: tracking file is {actual} bytes but the index "
                    f"was built against {int(row.source_bytes)}. "
                    f"Re-run python scripts/build_silver.py")

    # ---------------------------------------------------------------------------------
    def _slice(self, lo: int, hi: int) -> tuple[int, int]:
        """Positions in the index arrays covering the frame range [lo, hi]."""
        i = int(np.searchsorted(self.frames, lo, side="left"))
        j = int(np.searchsorted(self.frames, hi, side="right"))
        return i, j

    def fetch_window(self, lo: int, hi: int) -> Window:
        """Every frame in [lo, hi]. One seek, then sequential reads."""
        i, j = self._slice(lo, hi)
        out = []
        if j > i:
            with open(self.path, "rb") as fh:
                fh.seek(int(self.offsets[i]))
                for _ in range(j - i):
                    line = fh.readline()
                    if not line:
                        break
                    out.append(_loads(line))
        return Window(self.match_id, lo, hi, out, requested=max(j - i, 0))

    def fetch_sampled(self, lo: int, hi: int, n: int) -> Window:
        """n evenly spaced frames from [lo, hi] - one seek each, no intervening parses."""
        wanted = sample_frames(lo, hi, n)
        out = []
        if wanted:
            with open(self.path, "rb") as fh:
                for f in wanted:
                    pos = int(np.searchsorted(self.frames, f, side="left"))
                    if pos >= len(self.frames) or self.frames[pos] != f:
                        continue
                    fh.seek(int(self.offsets[pos]))
                    line = fh.readline()
                    if line:
                        out.append(_loads(line))
        return Window(self.match_id, lo, hi, out, requested=len(wanted))

    def fetch_at(self, frames: list[int]) -> Window:
        """Specific frame numbers - for predicates anchored on known instants
        (a delivery, a pass, a first contact)."""
        out = []
        with open(self.path, "rb") as fh:
            for f in sorted(frames):
                pos = int(np.searchsorted(self.frames, f, side="left"))
                if pos >= len(self.frames) or self.frames[pos] != f:
                    continue
                fh.seek(int(self.offsets[pos]))
                line = fh.readline()
                if line:
                    out.append(_loads(line))
        lo = min(frames) if frames else 0
        hi = max(frames) if frames else 0
        return Window(self.match_id, lo, hi, out, requested=len(frames))


# --------------------------------------------------------------------------------------
def available_matches() -> list[int]:
    if not INDEX_DIR.exists():
        return []
    return sorted(int(p.name.split("_")[0]) for p in INDEX_DIR.glob("*_frames.npy"))


def _benchmark() -> None:
    """Reproduce the timing claims in docs/tier3_lazy_retrieval_plan.md."""
    import random

    mids = available_matches()
    if not mids:
        print("No tracking indexes found. Run scripts/fetch_match_data.sh all --tracking "
              "&& python scripts/build_silver.py", file=sys.stderr)
        sys.exit(1)

    mid = mids[0]
    t0 = time.perf_counter()
    idx = FrameIndex.load(mid)
    print(f"index load (match {mid}): {(time.perf_counter() - t0) * 1000:.1f} ms, "
          f"{len(idx.frames):,} frames, "
          f"{(idx.frames.nbytes + idx.offsets.nbytes) / 1e6:.1f} MB")
    parser = "orjson" if FAST_JSON else "stdlib json (pip install orjson for ~2-5x)"
    print(f"json parser: {parser}")

    # Benchmark against REAL event windows, not random frames. Random frames sample the
    # ~27% of the match where the ball is out of play and nothing is tracked, which
    # understates coverage badly: random windows average 73.6% coverage, real event
    # windows average 99.8%. A candidate set only ever contains real events.
    lo_hi = []
    try:
        from enrich import load_enriched
        events, _m, _g = load_enriched()
        sub = events[events.match_id == mid].sample(156, random_state=0)
        lo_hi = [(int(r.frame_start) - 10, int(r.frame_end) + 20)
                 for r in sub.itertuples()]
        print("windows: 156 real event windows (median candidate-set size)")
    except Exception as exc:                      # gold not built yet
        print(f"windows: random frames - gold unavailable ({type(exc).__name__})")
        random.seed(0)
        lo_hi = [(f, f + 49) for f in random.sample(idx.frames[:-60].tolist(), 156)]

    idx.fetch_window(*lo_hi[0])  # warm

    t0 = time.perf_counter()
    wins = [idx.fetch_window(lo, hi) for lo, hi in lo_hi]
    t_full = time.perf_counter() - t0
    cov = float(np.mean([w.coverage for w in wins]))
    ok = sum(w.sufficient for w in wins)
    print(f"fetch_window  x156 (50 frames each): {t_full * 1000:.0f} ms total, "
          f"{t_full / 156 * 1000:.2f} ms/window")
    print(f"  mean coverage {cov:.1%}, {ok}/156 windows sufficient "
          f"(>= {MIN_COVERAGE:.0%})")

    t0 = time.perf_counter()
    samp = [idx.fetch_sampled(lo, hi, 5) for lo, hi in lo_hi]
    t_s = time.perf_counter() - t0
    print(f"fetch_sampled x156 (5 frames each):  {t_s * 1000:.0f} ms total, "
          f"{t_s / 156 * 1000:.2f} ms/window  "
          f"({t_full / t_s:.1f}x less work than full density)")

    frames_full = sum(len(w) for w in wins)
    frames_samp = sum(len(w) for w in samp)
    total = sum(len(FrameIndex.load(m).frames) for m in mids)
    print(f"frames read: {frames_full:,} full / {frames_samp:,} sampled "
          f"of {total:,} in the corpus "
          f"({frames_full / total:.2%} / {frames_samp / total:.3%})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--match", type=int, default=None)
    args = ap.parse_args()

    if args.benchmark:
        _benchmark()
        return

    mids = available_matches()
    print(f"tracking indexes available: {len(mids)} matches")
    for m in mids[: args.match and 1 or 5]:
        idx = FrameIndex.load(m)
        print(f"  {m}: {len(idx.frames):,} frames, "
              f"{idx.frames[0]}..{idx.frames[-1]}")


if __name__ == "__main__":
    main()
