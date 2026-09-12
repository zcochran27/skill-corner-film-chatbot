#!/usr/bin/env python
"""
Silver -> Gold.

Applies the Tier 1 / Tier 2 derivations in scripts/enrich.py to the Silver entity tables
and writes query-ready tables to data/gold/. See docs/data_architecture.md.

Gold is what is PRECOMPUTED. Tier 3's lazy tracking sweep deliberately does not live here -
it reads Silver's tracking index at query time, because precomputing geometry for the 99.4%
of frames no query touches is exactly what that design avoids. The only tracking work that
belongs in Gold is the thin eager base: cheap per-frame scalars (~22 us/frame) and the
per-team baselines derived from them, which lazy evaluation cannot compute because they are
corpus statistics by definition.

Usage:
    python scripts/build_gold.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich import (  # noqa: E402
    DERIVED_COLUMNS, GOLD, build_enriched, load_silver,
)

FPS = 10.0


def build_possession_chains(events: pd.DataFrame) -> pd.DataFrame:
    """One row per unbroken team possession, with its frame span.

    The span is what a clip segmenter needs: chain id gives the grouping, and Tier 3
    evidence frames narrow the in/out points within it.
    """
    g = events.dropna(subset=["team_possession_id"]).groupby("team_possession_id")
    chains = pd.DataFrame({
        "match_id": g["match_id"].first(),
        "team_id": g["team_id"].first(),
        "frame_start": g["frame_start"].min(),
        "frame_end": g["frame_end"].max(),
        "n_events": g["event_id"].size(),
        "n_possessions": g["chain_n_possessions"].first(),
        "reached_final_third": g["chain_reached_final_third"].first(),
        "reached_box": g["chain_reached_box"].first(),
        "ended_in_shot": g["chain_ended_in_shot"].first(),
    }).reset_index()
    chains["duration_s"] = (chains.frame_end - chains.frame_start) / FPS
    return chains


def main() -> None:
    print("[gold] reading silver ...")
    events, matches, players = load_silver()
    print(f"[gold] {len(events):,} events, {len(matches)} matches, {len(players)} players")

    events, goals = build_enriched(events, matches, players, verbose=True)
    chains = build_possession_chains(events)

    missing = [c for c in DERIVED_COLUMNS if c not in events.columns]
    if missing:
        raise RuntimeError(f"derivation did not produce declared columns: {missing}")

    GOLD.mkdir(parents=True, exist_ok=True)
    events.to_parquet(GOLD / "events_enriched.parquet", index=False)
    goals.to_parquet(GOLD / "goals.parquet", index=False)
    chains.to_parquet(GOLD / "possession_chains.parquet", index=False)

    total = sum(f.stat().st_size for f in GOLD.rglob("*") if f.is_file())
    print(f"[gold] events_enriched {events.shape[0]:,} x {events.shape[1]} "
          f"({len(DERIVED_COLUMNS)} derived) | goals {len(goals)} | chains {len(chains):,}")
    print(f"[gold] wrote {GOLD} ({total / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
