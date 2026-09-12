# Project: Coach/Scout Film-Clip Retrieval App

## What this is
A chatbot for coaches and scouts: they ask a natural-language question about a team or
player, and the app returns a ranked list of clickable film-clip segments that answer
the question. There is no actual video — clips are rendered as animations of tracking
data (players + ball as dots on a pitch), not broadcast footage.

Data source: [SkillCorner Open Data](https://github.com/SkillCorner/opendata) — 20
matches (not 10; the upstream repo has grown since this project started) of broadcast
tracking data (10 fps) plus SkillCorner's derived Dynamic Events and Phases of Play
datasets. No film/video files are involved anywhere in this project.

## Data architecture (MANDATED)
All data lives in a bronze/silver/gold medallion layout — see `docs/data_architecture.md`
for the full contract. The rules are not optional:
- `data/bronze/` raw and **immutable**; only `scripts/fetch_match_data.sh` writes there.
- `data/silver/` cleaned, typed, conformed entity tables (`build_silver.py`). Cleaning and
  validation only — no football concepts. Validation gates **fail the build**, never warn.
- `data/gold/` derived, query-ready tables (`build_gold.py` applying `enrich.py`).
- **Each layer reads only the layer below it.** Never `read_csv` from bronze outside
  `build_silver.py`; never skip a layer.
- Every derived column must be declared in `enrich.DERIVED_COLUMNS`.
- Tracking is a deliberate exception: it stays JSONL in bronze, and silver holds only the
  frame->byte-offset index, because Tier 3's lazy sweep seeks rather than scans.
- **Gold is what is precomputed.** Cheap, reused, non-parameterised tracking fields go in
  gold; advanced geometry is a lazy read through silver's index at query time.

## Start here
`docs/master_plan.md` is the single status entry point: pipeline state, what's built, issues
hit and the fixes in place, current test-set results, and sequenced next steps.

## Reference files in this folder
- `coach_question_test_set.md` — 80 seed questions across 8 categories (player-specific,
  spatial, event-type, sequence/chain, game-state/temporal, comparative/relational,
  negative/absence, and composite/blended questions that combine 2-3 gates at once). This
  is the test set the retrieval architecture is being designed against — every
  architectural decision should be checked against whether it can actually answer these.
- `skillcorner_schema.md` — full reference schema for the SkillCorner tracking data,
  Dynamic Events CSV (~200 attributes across 8 themed groups), and Phases of Play
  framework, pulled from SkillCorner's official CSV specification.
- `skillcorner_exploration.ipynb` — now runs against a **real** match's
  `_dynamic_events.csv` (`1874553`, Brisbane FC vs Adelaide United), executed with real
  outputs committed. The original synthetic-data version (no network access in the chat
  sandbox that built it) is in git history; same 4 representative queries, now on real data.
- `docs/real_data_validation.md` / `scripts/validate_real_data.py` — the first real-data
  pass: re-ran the notebook's 4 representative queries against real data. All 4 resolved
  as direct field filters, confirming the structured-filtering bet; one query (Q3,
  sequence) needed widening to a real `start_type` value the synthetic generator never
  produced.
- `scripts/enrich.py` / `docs/enrichment.md` — **Tier 1 + Tier 2 enrichment, built and
  validated.** Adds 32 derived columns (goal timeline, parameterised shot/goal windows,
  true possession chains, mirror-safe per-match-scaled zone flags, squad units, captain
  hook). Took the test set from 47/22/11 to **57 exact, 16 approximate, 7 unresolved**.
  Also records three corrections to `docs/real_data_validation.md` — most importantly that
  `penalty_area_start/end` fires for *either* box, not attacking-only.
- `scripts/field_catalog.py` / `docs/field_catalog.md` — generated event_type × column
  availability map. 168 of 350 columns are populated on exactly one event_type and 28 are
  missing from at least one match entirely; filtering the wrong one returns zero rows
  silently. Consult before emitting a filter.
- `docs/tier3_tracking_plan.md` — tracking prerequisites (verified: LFS pull works, 73% of
  frames have full 22-player data, the event/tracking mirror-sign rule and its halftime
  flip), the metric definitions for the 4 tracking-blocked questions, and a catalogue of
  further tracking features (pitch control, set-piece geometry, dyadic relations, movement
  quality, clip boundaries). Its §2 precompute architecture is superseded.
- `docs/tier3_lazy_retrieval_plan.md` — **the adopted Tier 3 design.** Phase 1 event filters
  produce a candidate set (median 156 events), then only those events' tracking frames are
  fetched via a frame->byte-offset index (0.16s/match to build, 1.79ms per 50-frame window)
  and each candidate is confirmed geometrically. Typical query ~280ms. Also fills in the
  previously-undesigned Validation stage. Recommends a thin eager base for cheap per-frame
  scalars (22us/frame) and team baselines, staying lazy for expensive geometry (convex hull
  at 625us/frame) and anything dyadic or query-parameterised.
- `scripts/test_all_questions.py` — the full follow-up: all 80 questions from
  `coach_question_test_set.md` run against all 20 real matches (94,517 events), each
  tagged exact/approximate/unresolved. **47 exact, 22 approximate, 11 unresolved** at the
  time of that pass — now 57/16/7 after Tier 1+2 enrichment, see `docs/enrichment.md`. Full
  results and analysis in `docs/real_data_validation.md`'s second half, including:
  Category 6 (comparative/relational) turned out to be mostly resolvable from per-event
  fields (`separation_start/end`, `interplayer_distance`) without raw tracking, contrary
  to the original notebook's assumption; 3 real "which event_type is this field on"
  schema gotchas the script caught and fixed; and the 4 concrete gaps behind the 11
  unresolved questions (no captain flag, no goal-timestamp field, multi-player geometry
  needing raw tracking, a few untagged concepts like offside). Also times each question's
  retrieval query: median 19ms, max 289ms (a phase-level anti-join) across all 69 timed
  questions — no latency concern at the pandas-prototype level, well under whatever an
  LLM query-parsing call will cost once that stage exists.

## Architecture decided so far (structured filtering, no embeddings — yet)

Explicitly chose deterministic structured filtering over vector similarity search for
v1, for speed, determinism, and debuggability. Vector/embedding-based fallback for
fuzzy or novel phrasing is a noted **future exploration item**, not part of the current
design.

### Pipeline stages
1. **Preprocessing / enrichment** — combine SkillCorner tracking + Dynamic Events to
   enrich the event-level dataset with derived fields the AI can filter on (possession,
   location/channel/third, phase of play, nearby/involved players). Exploration so far
   shows a lot of this is **already native to the SkillCorner schema** (e.g. `out_to_in`
   for a winger cutting inside, `lead_to_shot` for outcome-based sequencing) — enrichment
   work is often "pick the right existing column," not build new geometry from scratch.
2. **Query parsing** — a step-by-step chain (not one single extraction call), because
   accuracy and debuggability matter more than speed at this stage. Each step is an
   **optional gate**: it first asks whether that dimension is even present in the
   question, and only extracts details if so (a generic question shouldn't get a
   hallucinated player or spatial condition attached).
3. **Retrieval** — run the parsed structured query against the enriched event data
   (phase 1: event-level filtering) then, if a spatial condition was flagged, a second
   pass against raw tracking data for fine-grained geometric confirmation (phase 2).
4. **Validation** — confirm the retrieved candidates actually answer the coach's
   question (stage not yet designed in detail).
5. **Output/ranking** — return a ranked list of clip segments (ranking logic is a
   TODO, explicitly left for later).

### The 8 parsing gates (in the order they were identified, not yet ordered for the pipeline)
1. Event type (dribble, shot, pass, etc.) — applies to nearly every question.
2. Single event vs. sequence/chain (e.g. "show me the goal" implies buildup, not just
   the shot).
3. Player or role (named player, jersey number, or positional role like "left winger").
4. Spatial condition (needs tracking-data-level analysis, not just event fields).
5. Temporal / game-state condition (score state, minute range).
6. Comparative / relational (matchup between two players, e.g. isolated 1v1 — distinct
   from a single-player filter).
7. Negative / absence (question asks what did NOT happen — needs an anti-join over
   candidate windows, not a boolean filter; confirmed in the exploration notebook to
   require genuinely different query code from the other 7 gates).
8. Outcome / result filtering (does the coach care what the sequence led to, e.g. "only
   sequences that ended in a shot" — separate from sequence detection itself).

Each gate should self-report whether it applies before extracting anything, since most
real questions only touch 2-4 of the 8 categories.

## Tier 3 decisions (settled)
- **Prefer ranking to thresholding for fuzzy football concepts.** Where a concept has no
  crisp definition ("near post"), return results ranked by the underlying measurement rather
  than filtered by a chosen cutoff - Q68's answer moves between 12% and 48% across
  defensible zone definitions, and ranking removes the cutoff entirely.
- **Vector/similarity search stays deferred, now with a scope.** Strongest case is
  query-by-example ("more like this clip"), then the untagged concepts; strictly worse for
  the questions that already resolve deterministically. Two reasons not to reach for it
  early: it hides judgement that a rectangle makes inspectable, and it is poor at absence,
  which is what the negative/absence gate needs. See `docs/master_plan.md`.
- **Lazy per-event confirmation, not bulk precompute.** Phase 1 event filters yield a
  candidate set (median 156 events); only those events' frames are fetched (~0.6% of the
  corpus) and confirmed geometrically.
- **Precompute only cheap, targetable fields.** Spreads/centroids/nearest-opponent cost
  22us/frame (39s for the corpus) and feed the per-team baselines that lazy evaluation
  can't compute; convex hull costs 625us/frame (~18 min) and stays lazy, as does anything
  dyadic or query-parameterised.
- **Sample frames within the window.** Convex hull on 50 frames x 156 candidates = 4.9s; at
  5 frames per window, 0.5s. Sample density is a per-predicate parameter.

## Open / not yet decided
- Exact ordering/dependency of the 8 gates within the parsing chain.
- Validation stage design.
- Clip ranking logic — now more concrete since `scripts/test_all_questions.py` reports raw
  event/row hit counts, not deduplicated clips; still need to decide how multiple matching
  rows within one possession collapse into a single clip.
- Whether/when to add a vector-embedding fallback path for the structured filters that
  can't cover a question (explicitly deferred, not rejected).
- **7** unresolved test-set questions remain (down from 11), in 3 groups: captain identity
  (5, 78 — genuinely absent from SkillCorner, needs an external roster source);
  multi-player tracking geometry (17, 56, 59 — Tier 3); and two untagged concepts
  (27 offside, 66 "tracked back"). Goal timestamps and defender-beaten flags turned out to
  be derivable after all — see `docs/enrichment.md`.
- Tracking data is **no longer a blocker**: `git lfs pull` works anonymously and all 20
  matches (1.82 GB) fetch fine via `scripts/fetch_match_data.sh all --tracking`. Tier 3 is
  buildable, not just plannable.
- The actual query-parsing chain (LLM prompt(s) that turn a coach's question into the
  8-gate structured query) — everything so far has hand-written the target query for a
  known test-set question; no code yet turns free text into one.

## Working style notes
- Brainstorm-then-build sequencing was intentional: test set first, then architecture,
  then code. Don't skip ahead to code without checking a design decision against the
  test set.
- Prefer verifying claims against the official SkillCorner CSV spec (in
  `skillcorner_schema.md`) over assuming a field exists.
- Better still, verify against the real data over the spec. Of the concrete bugs the
  enrichment pass found, three were documented fields behaving differently than documented
  (`penalty_area_*` firing for both boxes; `goal_for` markers missing on some goals and
  mis-attributed on defensive rows; `lead_to_shot` on a defensive row referring to the
  opponent's shot) and one was a field present in only half the matches
  (`playing_time.sequences`). A filter against a column that is null for that event type
  returns zero rows silently — it looks like a finding, not a bug.
