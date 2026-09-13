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
    DERIVED_COLUMNS, GOLD, SILVER, build_enriched, load_silver,
)

FPS = 10.0


def build_phase_shape(phases: pd.DataFrame) -> pd.DataFrame:
    """Phases plus team-shape measures expressed against each team's OWN norm.

    _phases_of_play.csv carries team_in/out_of_possession_width and _length per phase -
    columns that exist nowhere in the events table. They were never examined because the
    first validation pass concluded phases were redundant ("phase context is already inlined
    on every dynamic-events row"), which is true of the phase TYPE but not of these.

    They behave exactly as football predicts, which is the check that they mean what they
    say: median out-of-possession width runs 22.8m defending a set play, 34.1m in a low
    block, 36.3m medium, 37.1m high, and 50.2m defending a quick break.

    "Compressed into a narrow shape" (Q17) is relative, not absolute - a 34m block is tight
    for one side and normal for another - so width is converted to a percentile within the
    same team's distribution for the same phase type. That is a corpus statistic, which is
    why it belongs in Gold and cannot be computed lazily per candidate.
    """
    ph = phases.copy()

    # The out-of-possession measures describe the team NOT in possession, so name that team
    # explicitly rather than leaning on team_in_possession_id to imply it.
    two = (ph.groupby("match_id")["team_in_possession_id"]
           .apply(lambda s: sorted(s.dropna().unique())).to_dict())
    ph["defending_team_id"] = [
        next((t for t in two.get(m, []) if t != p), None)
        for m, p in zip(ph.match_id, ph.team_in_possession_id)
    ]

    # Baselines are per TEAM across all their matches, not per match: a single match gives
    # only ~50-150 phases per team per type, and "narrow for them" should mean narrow
    # against how that side usually sets up, not against one game's spread.
    specs = [("out_of_possession", "defending_team_id",
              "team_out_of_possession_phase_type"),
             ("in_possession", "team_in_possession_id",
              "team_in_possession_phase_type")]
    for side, team_col, type_col in specs:
        col = f"team_{side}_width_end"
        if col not in ph.columns:
            continue
        g = ph.groupby([team_col, type_col], observed=True)[col]
        ph[f"{side}_width_pctile"] = g.rank(pct=True)
        ph[f"{side}_width_z"] = (ph[col] - g.transform("mean")) / g.transform("std")
        ph[f"{side}_width_baseline"] = g.transform("median")
    return ph


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
    phases_path = SILVER / "phases.parquet"
    phases = pd.read_parquet(phases_path) if phases_path.exists() else pd.DataFrame()
    print(f"[gold] {len(events):,} events, {len(matches)} matches, "
          f"{len(players)} players, {len(phases):,} phases")

    events, goals = build_enriched(events, matches, players, verbose=True)
    chains = build_possession_chains(events)

    missing = [c for c in DERIVED_COLUMNS if c not in events.columns]
    if missing:
        raise RuntimeError(f"derivation did not produce declared columns: {missing}")

    GOLD.mkdir(parents=True, exist_ok=True)
    events.to_parquet(GOLD / "events_enriched.parquet", index=False)
    goals.to_parquet(GOLD / "goals.parquet", index=False)
    chains.to_parquet(GOLD / "possession_chains.parquet", index=False)
    if len(phases):
        phase_shape = build_phase_shape(phases)
        phase_shape.to_parquet(GOLD / "phase_shape.parquet", index=False)
        print(f"[gold] phase_shape {len(phase_shape):,} phases with team-relative "
              f"width percentiles")

    total = sum(f.stat().st_size for f in GOLD.rglob("*") if f.is_file())
    print(f"[gold] events_enriched {events.shape[0]:,} x {events.shape[1]} "
          f"({len(DERIVED_COLUMNS)} derived) | goals {len(goals)} | chains {len(chains):,}")
    print(f"[gold] wrote {GOLD} ({total / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
