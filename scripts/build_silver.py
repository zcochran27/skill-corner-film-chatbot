#!/usr/bin/env python
"""
Bronze -> Silver.

Reads the immutable raw files under data/bronze/ and writes cleaned, typed, conformed
entity tables to data/silver/. See docs/data_architecture.md for the layer contract.

Silver's job is to make the data *trustworthy and cheap to load*, not to add analysis:
  - concatenate 20 per-match CSVs into one events table
  - cast dtypes properly (the 58 boolean columns arrive as object; low-cardinality strings
    become categories) - this is where 635 MB becomes something far smaller
  - flatten _match.json into match and player entity tables
  - build the tracking frame index and mirror-sign table needed for Tier 3's lazy sweep
  - run the validation gates, and FAIL rather than write a bad table

No derived analytics here. Those are Gold (scripts/build_gold.py).

Usage:
    python scripts/build_silver.py            # build all silver tables
    python scripts/build_silver.py --check    # validate existing silver, write nothing
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
BRONZE = ROOT / "data" / "bronze"
SILVER = ROOT / "data" / "silver"

# Columns whose distinct values are few enough that category dtype pays for itself.
CATEGORY_MAX_CARDINALITY = 64


class ValidationError(Exception):
    """A Silver validation gate failed. Nothing is written."""


# --------------------------------------------------------------------------------------
# Typing
# --------------------------------------------------------------------------------------
def conform_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Cast object columns to the narrowest dtype that holds their values.

    SkillCorner booleans arrive as object dtype because NaN forces it. They currently hold
    real Python bools, so `col == True` happens to work - but that is a property of how
    pandas parsed this particular CSV, not a guarantee. A column that is all-null in one
    match, or read with different parameters, lands as strings instead and every comparison
    silently returns False. Casting once here removes the ambiguity permanently and is why
    downstream code should not need an _as_bool() helper.
    """
    for col in df.columns:
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        values = set(non_null.unique()[:1000])
        if values <= {True, False} or values <= {"True", "False"}:
            df[col] = df[col].map({True: True, False: False,
                                   "True": True, "False": False}).astype("boolean")
        elif non_null.nunique() <= CATEGORY_MAX_CARDINALITY:
            df[col] = df[col].astype("category")
    return df


# --------------------------------------------------------------------------------------
# Entity builders
# --------------------------------------------------------------------------------------
def build_matches() -> pd.DataFrame:
    rows = []
    for f in sorted(BRONZE.glob("matches/*/*_match.json")):
        m = json.load(open(f, encoding="utf-8"))
        periods = {p["period"]: p for p in m["match_periods"]}
        rows.append(dict(
            match_id=int(m["id"]),
            home_team_id=int(m["home_team"]["id"]),
            away_team_id=int(m["away_team"]["id"]),
            home_team_name=m["home_team"]["short_name"],
            away_team_name=m["away_team"]["short_name"],
            home_team_score=int(m["home_team_score"]),
            away_team_score=int(m["away_team_score"]),
            pitch_length=float(m["pitch_length"]),
            pitch_width=float(m["pitch_width"]),
            period_1_start=int(periods[1]["start_frame"]),
            period_1_end=int(periods[1]["end_frame"]),
            period_2_start=int(periods[2]["start_frame"]),
            period_2_end=int(periods[2]["end_frame"]),
            date_time=m.get("date_time"),
            stadium=(m.get("stadium") or {}).get("name"),
        ))
    return pd.DataFrame(rows).sort_values("match_id").reset_index(drop=True)


def build_players(events: pd.DataFrame) -> pd.DataFrame:
    """One row per (match, player), with on-pitch intervals flattened.

    playing_time.sequences exists in only 11 of 20 matches; playing_time.total.start_frame /
    end_frame exists for every player in all 20, so it is the fallback. Position comes from
    the modal OBSERVED player_position in the events, because the roster's player_role reads
    'SUB' for every substitute rather than the position they actually played.
    """
    observed = (events.dropna(subset=["player_position"])
                .groupby(["match_id", "player_id"], observed=True)["player_position"]
                .agg(lambda s: s.value_counts().idxmax()).to_dict())
    rows = []
    for f in sorted(BRONZE.glob("matches/*/*_match.json")):
        m = json.load(open(f, encoding="utf-8"))
        mid, kickoff = int(m["id"]), m["match_periods"][0]["start_frame"]
        for p in m["players"]:
            pid = int(p["id"])
            pt = p.get("playing_time") or {}
            seqs = pt.get("sequences") or []
            source = "sequences"
            if not seqs:
                tot = pt.get("total") or {}
                if tot.get("start_frame") is None:
                    continue
                seqs = [{"start_frame": tot["start_frame"], "end_frame": tot["end_frame"]}]
                source = "total"
            rows.append(dict(
                match_id=mid, player_id=pid, team_id=int(p["team_id"]),
                number=p.get("number"),
                short_name=p.get("short_name"),
                roster_position=(p.get("player_role") or {}).get("acronym"),
                position=observed.get((mid, pid), (p.get("player_role") or {}).get("acronym")),
                is_starter=min(s["start_frame"] for s in seqs) <= kickoff + 1,
                is_substitute=p.get("start_time") not in (None, "00:00:00"),
                goals=int(p.get("goal") or 0),
                own_goals=int(p.get("own_goal") or 0),
                on_pitch_intervals=json.dumps([[int(s["start_frame"]), int(s["end_frame"])]
                                               for s in seqs]),
                interval_source=source,
            ))
    return pd.DataFrame(rows).sort_values(["match_id", "player_id"]).reset_index(drop=True)


def build_events() -> pd.DataFrame:
    files = sorted(glob.glob(str(BRONZE / "matches" / "*" / "*_dynamic_events.csv")))
    if not files:
        raise FileNotFoundError(
            f"No dynamic_events.csv under {BRONZE}. Run scripts/fetch_match_data.sh first.")
    events = pd.concat([pd.read_csv(f, low_memory=False) for f in files], ignore_index=True)
    # Stable phase key, kept from the original pipeline. NOT a possession key - see
    # docs/enrichment.md; possessions are a different grouping, derived in Gold.
    events["_pk"] = events["match_id"].astype(str) + "_" + events["phase_index"].astype(str)
    return conform_dtypes(events)


def build_phases() -> pd.DataFrame:
    files = sorted(glob.glob(str(BRONZE / "matches" / "*" / "*_phases_of_play.csv")))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        df = pd.read_csv(f, low_memory=False)
        if "match_id" not in df.columns:
            df["match_id"] = int(Path(f).name.split("_")[0])
        frames.append(df)
    return conform_dtypes(pd.concat(frames, ignore_index=True))


def build_frame_indexes() -> pd.DataFrame:
    """frame -> byte offset per tracking file, for Tier 3's lazy window fetch.

    Tracking deliberately stays as JSONL rather than becoming a Silver Parquet table: the
    adopted Tier 3 design reads ~0.6% of frames per query by seeking to a byte offset, which
    a row-oriented file supports directly. Converting 1.8 GB to Parquet would optimise for a
    full-scan access pattern the design specifically avoids. Only the index is built here.
    """
    out = SILVER / "tracking_index"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for tr in sorted(BRONZE.glob("matches/*/*_tracking_extrapolated.jsonl")):
        mid = int(tr.name.split("_")[0])
        size = tr.stat().st_size
        if size < 10_000:
            rows.append(dict(match_id=mid, n_frames=0, status="lfs_stub"))
            continue
        frames, offsets, pos = [], [], 0
        with open(tr, "rb") as fh:
            for line in fh:
                head = line[:24]
                i, j = head.index(b":") + 1, head.index(b",")
                frames.append(int(head[i:j]))
                offsets.append(pos)
                pos += len(line)
        np.save(out / f"{mid}_frames.npy", np.asarray(frames, dtype=np.int64))
        np.save(out / f"{mid}_offsets.npy", np.asarray(offsets, dtype=np.int64))
        rows.append(dict(match_id=mid, n_frames=len(frames), status="ok",
                         source_bytes=size, source_mtime=tr.stat().st_mtime))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# Validation gates - Silver fails loudly rather than writing a bad table
# --------------------------------------------------------------------------------------
def validate(events, matches, players, tracking_index) -> list[str]:
    errors, notes = [], []

    n_expected = matches.match_id.nunique()
    if events.match_id.nunique() != n_expected:
        errors.append(f"events cover {events.match_id.nunique()} matches, "
                      f"expected {n_expected}")

    for col in ["match_id", "event_id", "event_type", "frame_start", "frame_end", "team_id"]:
        if col not in events.columns:
            errors.append(f"events missing required column {col}")
        elif events[col].isna().any():
            errors.append(f"events.{col} has {int(events[col].isna().sum())} nulls")

    dup = events.duplicated(subset=["match_id", "event_id"]).sum()
    if dup:
        errors.append(f"events has {dup} duplicate (match_id, event_id) rows")

    if (events.frame_end < events.frame_start).any():
        errors.append("events has rows with frame_end < frame_start")

    # every event's team must be one of that match's two teams
    teams = matches.set_index("match_id")[["home_team_id", "away_team_id"]]
    j = events[["match_id", "team_id"]].drop_duplicates().join(teams, on="match_id")
    orphan = j[(j.team_id != j.home_team_id) & (j.team_id != j.away_team_id)]
    if len(orphan):
        errors.append(f"{len(orphan)} (match, team) pairs not in the match roster")

    # per-player goals must reconcile with the official score
    for _, m in matches.iterrows():
        pm = players[players.match_id == m.match_id]
        scored = pm.groupby("team_id").goals.sum()
        tot = int(scored.sum()) if len(scored) else 0
        if tot != m.home_team_score + m.away_team_score:
            notes.append(f"match {m.match_id}: per-player goals {tot} != official "
                         f"{m.home_team_score + m.away_team_score} (own goals are expected "
                         f"to differ)")

    if tracking_index.empty:
        notes.append("no tracking files in bronze - Tier 3 unavailable "
                     "(run scripts/fetch_match_data.sh all --tracking)")
    else:
        stubs = tracking_index[tracking_index.status == "lfs_stub"]
        if len(stubs):
            notes.append(f"{len(stubs)} tracking files are still LFS stubs - Tier 3 "
                         f"unavailable for those matches "
                         f"(run scripts/fetch_match_data.sh all --tracking)")
        ok = tracking_index[tracking_index.status == "ok"]
        if len(ok):
            notes.append(f"tracking index built for {len(ok)} matches, "
                         f"{int(ok.n_frames.sum()):,} frames")

    for n in notes:
        print(f"  note: {n}")
    return errors


# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="validate only; write nothing")
    args = ap.parse_args()

    print("[silver] reading bronze ...")
    events = build_events()
    matches = build_matches()
    players = build_players(events)
    phases = build_phases()
    tracking_index = build_frame_indexes()

    print(f"[silver] events {events.shape[0]:,} x {events.shape[1]} | "
          f"matches {len(matches)} | players {len(players)} | phases {len(phases):,}")
    mem = events.memory_usage(deep=True).sum() / 1e6
    print(f"[silver] events in memory: {mem:.0f} MB "
          f"({int((events.dtypes == object).sum())} object columns remaining)")

    errors = validate(events, matches, players, tracking_index)
    if errors:
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        raise ValidationError(f"{len(errors)} Silver validation failure(s); nothing written")
    print("[silver] validation passed")

    if args.check:
        print("[silver] --check: nothing written")
        return

    SILVER.mkdir(parents=True, exist_ok=True)
    events.to_parquet(SILVER / "events.parquet", index=False)
    matches.to_parquet(SILVER / "matches.parquet", index=False)
    players.to_parquet(SILVER / "players.parquet", index=False)
    if len(phases):
        phases.to_parquet(SILVER / "phases.parquet", index=False)
    tracking_index.to_parquet(SILVER / "tracking_index.parquet", index=False)

    # Mirror signs depend on the frame index and metadata above, so they are measured last.
    # Silver owns this for the same reason it owns the frame index: it makes Bronze
    # readable rather than adding football meaning.
    if not tracking_index.empty and (tracking_index.status == "ok").any():
        from mirror_sign import SIGNS_FILE, build_mirror_signs
        signs = build_mirror_signs(events)
        if not signs.empty:
            signs.to_parquet(SIGNS_FILE, index=False)
            print(f"[silver] mirror signs: {len(signs)} (match, team, period) groups, "
                  f"min confidence {signs.confidence.min():.0%}")
    else:
        print("[silver] mirror signs skipped - no tracking available")

    total = sum(f.stat().st_size for f in SILVER.rglob("*") if f.is_file())
    print(f"[silver] wrote {SILVER} ({total / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
