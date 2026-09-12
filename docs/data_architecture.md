# Data Architecture — Bronze / Silver / Gold (mandated)

This is the required layout for all data in this project. It is a mandate, not a
suggestion: code that reads or writes data outside these rules should be treated as a bug
in review.

```
data/bronze/    raw, immutable, exactly as fetched from SkillCorner
data/silver/    cleaned, typed, conformed entity tables + validation gates
data/gold/      derived, query-ready tables the retrieval layer actually reads
```

Everything under `data/` is gitignored and reproducible from `scripts/fetch_match_data.sh`.

---

## The rules

1. **Bronze is immutable.** `scripts/fetch_match_data.sh` is the only thing that writes
   there. Nothing edits, patches, or cleans a Bronze file in place. If raw data is wrong,
   that fact gets handled in Silver and documented — Bronze still shows what upstream sent.
2. **Only Silver builders read Bronze.** Analysis code, notebooks, and the retrieval layer
   never open a raw CSV or `_match.json`. If you find yourself writing
   `pd.read_csv(".../data/bronze/...")` outside `build_silver.py`, the column you want
   belongs in a Silver table.
3. **Silver cleans and conforms; it does not analyse.** Typing, concatenation, flattening
   JSON into entities, and validation. No derived football concepts — those are Gold.
4. **Gold is what is precomputed.** Everything the retrieval layer filters on, computed
   once. Gold reads Silver only.
5. **Each layer reads only the layer below it.** Bronze → Silver → Gold → consumers. No
   skipping, no reaching back up.
6. **Validation gates live at the Silver boundary, and they fail the build.** A gate that
   prints a warning and writes the table anyway is not a gate. Non-fatal observations are
   printed as `note:`; anything that would corrupt downstream results raises and writes
   nothing.
7. **Every derived column is declared.** `enrich.DERIVED_COLUMNS` is the registry;
   `build_gold.py` asserts the derivation actually produced each one, and
   `scripts/field_catalog.py` picks them up automatically.
8. **Rebuilds are cheap and total.** No incremental-update logic, no partial invalidation.
   Silver takes ~60s, Gold ~25s. If a definition changes, rebuild.

---

## Layer contracts

### Bronze — `data/bronze/`

| Path | Contents |
|---|---|
| `matches/{id}/{id}_dynamic_events.csv` | Dynamic Events, one file per match |
| `matches/{id}/{id}_match.json` | Lineups, pitch size, scores, periods |
| `matches/{id}/{id}_phases_of_play.csv` | Phases of play |
| `matches/{id}/{id}_tracking_extrapolated.jsonl` | 10fps tracking (~91 MB/match, LFS) |
| `matches.json` | Upstream match list |

~1.8 GB with tracking, 89 MB without. Written only by `scripts/fetch_match_data.sh`.

### Silver — `data/silver/`

Cleaned, typed, one table per entity. Built by `scripts/build_silver.py`.

| Table | Grain | Notes |
|---|---|---|
| `events.parquet` | one row per event | 94,517 × 323, all 20 matches concatenated |
| `matches.parquet` | one row per match | pitch dims, team ids, scores, period frames |
| `players.parquet` | one row per (match, player) | position, starter flag, on-pitch intervals |
| `phases.parquet` | one row per phase | 8,874 rows |
| `tracking_index.parquet` | one row per match | frame counts + source file fingerprint |
| `tracking_index/{id}_frames.npy`, `{id}_offsets.npy` | one entry per frame | 1,244,814 frames indexed |

**Typing is the main job.** The raw CSVs give 130 object-dtype columns costing 635 MB in
memory, of which 58 are booleans. Those currently hold real Python bools, so `col == True`
happens to work — but that is a property of how pandas parsed *these* files, not a
guarantee. A column that is all-null in one match, or read with different parameters, lands
as `"True"`/`"False"` strings and every comparison silently returns False. Given that silent
zeros are this project's dominant failure mode (see `docs/master_plan.md` §4a), casting once
at the Silver boundary is the structural fix. Result: **635 MB → 210 MB, 130 object columns
→ 11.**

**Tracking is a deliberate exception.** It stays JSONL in Bronze and is *not* converted to a
Silver Parquet table. Tier 3 reads ~0.6% of frames per query by seeking to a byte offset,
which a row-oriented file supports directly; converting 1.8 GB to Parquet would optimise for
a full-scan pattern the design specifically avoids. Silver therefore holds only the **frame
index** — the minimum needed to make Bronze randomly accessible.

### Gold — `data/gold/`

Query-ready. Built by `scripts/build_gold.py`, which applies the derivations in
`scripts/enrich.py`.

| Table | Grain | Notes |
|---|---|---|
| `events_enriched.parquet` | one row per event | 94,517 × 355, incl. 32 derived columns |
| `goals.parquet` | one row per goal | 66, validated against all 20 official scores |
| `possession_chains.parquet` | one row per team possession | 4,071, with frame spans |

Planned (Tier 3 thin eager base, see below): `frame_scalars.parquet`,
`team_baselines.parquet`.

---

## Where Tier 3 fits

This is the part the medallion framing makes clearest, and it encodes the adopted design:

- **Gold = precomputed.** Only cheap, targetable, broadly-reused tracking fields belong
  here: per-frame scalars (block width/height/centroid, nearest-opponent distance) at
  ~22 µs/frame, ~39s for the whole corpus, plus the per-team baselines derived from them.
  Baselines *must* be Gold — "narrower than their own average" is a corpus statistic that
  lazy per-event evaluation cannot compute by definition.
- **The lazy sweep reads Silver, not Gold.** Advanced geometry (convex hull at 625 µs/frame,
  pitch control, anything dyadic or query-parameterised) is evaluated at query time against
  Bronze tracking via the Silver frame index. Precomputing it into Gold would defeat the
  design.

So the rule is: **if it is cheap, reused, and not query-parameterised, it is Gold. Otherwise
it is a lazy read through Silver's index.**

---

## Validation gates

`build_silver.py` fails the build on any of:

- event coverage not matching the number of matches
- nulls in `match_id`, `event_id`, `event_type`, `frame_start`, `frame_end`, `team_id`
- duplicate `(match_id, event_id)`
- `frame_end < frame_start`
- a `(match, team)` pair not in that match's roster

`build_gold.py` additionally asserts every column in `DERIVED_COLUMNS` was produced, and
reports goal-timeline agreement against official scores (currently **20/20**).

Observations that are expected to differ are printed as notes rather than failures — e.g.
per-player goal counts not summing to the official score in two matches, which own goals
explain.

---

## Rebuilding

```bash
scripts/fetch_match_data.sh all --tracking   # Bronze  (~1.8 GB)
python scripts/build_silver.py               # Silver  (~34 MB)
python scripts/build_gold.py                 # Gold    (~16 MB)

python scripts/build_silver.py --check       # validate without writing
python scripts/field_catalog.py              # regenerate docs/field_catalog.md
python scripts/test_all_questions.py         # all 80 questions
```

Consumers call `enrich.load_enriched()`, which reads Gold and raises a message telling you
which build step to run if a table is missing.

---

## Why — the measured case

| | Before | After |
|---|---|---|
| Consumer load time | 9.8s (re-read 89 MB of CSV, recompute every session) | **0.73s** |
| Events in memory | 635 MB | **210 MB** |
| Object-dtype columns | 130 | **11** |
| On-disk (events) | 89 MB CSV | 34 MB Silver + 16 MB Gold |
| Validation | ad hoc, inside analysis scripts | enforced gate that fails the build |

Test-set results are unchanged through the refactor — **57 exact / 16 approximate /
7 unresolved**, identical to the pre-layer run, which is the check that the migration
preserved behaviour rather than quietly altering it.
