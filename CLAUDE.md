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

## Answerability (MANDATED)
**When the data cannot answer a question, the app says so. It never substitutes the nearest
proxy and presents it as the real thing.** See `docs/answerability.md`; the registry and the
verbatim user-facing text live in `scripts/answerability.py`.
- `no_data` (captain, offside calls) - refuse permanently, explain why, offer the nearest
  answerable alternative.
- `not_implemented` - refuse *for now*; the data supports it, so do not make a
  permanent-sounding promise. **Currently empty** - both former entries (dragged out of
  position, foot race) have since been built.
- `approximate` (14 questions) - answer, but **disclose the proxy alongside the results**.
- `ranked` (Q17, Q66, Q68) - return an ordering; never phrase it as a filtered set, since
  that smuggles in a threshold that was never measured.
- `insufficient_data` - report as coverage, **never** fold into the answer set. Absence is a
  positive answer for the negative/absence gate, so the two must stay distinct.
Stage 2 must call `answerability.check(concept)` before emitting filters.

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

**Current state (keep this line current):** test set at **63 exact / 14 approximate / 3
unresolved**, which is the ceiling - the three left (captain x2, offside calls) cannot be
answered from any available data. Stages 0, 1, 3 and 4 are built; Tier 3 is complete.
Stage 2 (query parsing) is built but **not ready**: its first real evaluation returns the
right rows for only 5 of 13 comparable questions, because independent gates duplicate each
other's filters (`docs/master_plan.md` 4k). Stage 5 (ranking/clip output) has not been started.

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
- `docs/tier3_lazy_retrieval_plan.md` — **the adopted Tier 3 design, now complete.** Phase 1
  event filters produce a candidate set (median 156 events), then only those events' tracking
  frames are fetched via a frame->byte-offset index and each candidate is confirmed
  geometrically. Measured on real event windows: **~6ms per window cold, ~1.8ms warm** (OS page
  cache), and **parsing is 96% of fetch cost**, so frame sampling is the dominant optimisation.
  An earlier "1.79ms / 280ms per query" figure came from random frames plus a warm cache and
  is superseded. The eager base came out narrower than planned: only `player_baselines` and
  `phase_shape` were built, since nothing needed per-frame team scalars.
- `scripts/frame_index.py`, `mirror_sign.py`, `predicates.py` — Tier 3 foundation: window
  fetch with coverage, the measured tracking/event coordinate sign (80 groups at 100%
  confidence), and the predicate harness (`--selftest` reproduces a Gold column from
  tracking at 100%).
- `scripts/setpiece.py`, `defensive.py`, `dyadic.py`, `tracking_base.py` — the tracking
  predicates that resolved Q56, Q59, Q66, Q68, Q70, plus the per-player positional baselines.
- `scripts/query_parse.py`, `query_schema.py` — **stage 2**, the 8-gate parsing chain and its
  per-gate field vocabulary / `validate_filter()` guardrail. `--offline` runs a keyword stub.
- `scripts/parse_cost.py`, `parse_eval.py`, `parse_exec.py` — measured stage 2 cost, and
  grading against the hand-written queries. `parse_exec.py` grades by executing parses
  against Gold, and that is the number to trust. Parses are saved, and `--regrade` re-applies
  the current guardrails to them, so guardrail fixes can be verified without API calls.
- `scripts/env.py` / `.env` — the Anthropic credential. `.env` is **gitignored**;
  `.env.example` documents it. Never read or print `.env`.
- `scripts/test_all_questions.py` — the full follow-up: all 80 questions from
  `coach_question_test_set.md` run against all 20 real matches (94,517 events), each
  tagged exact/approximate/unresolved. **47 exact, 22 approximate, 11 unresolved** at the
  time of that pass; 57/16/7 after Tier 1+2 enrichment; **63/14/3 now** after Tier 3. Full
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
   question. **Built:** the phase-2 predicate harness (`scripts/predicates.py`) is the
   validation - it confirms each candidate against tracking and returns evidence frames,
   with `insufficient_data` kept distinct from a negative.
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

## Query-parsing chain (stage 2) - gate ordering DECIDED
`scripts/query_parse.py`. Ordering is **shape -> subject -> conditions**:
1. **negative/absence** - inverts the query into an anti-join, so it changes the structure
   and must be known before anything is built.
2. **sequence** - event-level or chain-level grain.
3. **event type** - decides which COLUMNS EXIST downstream (168 of 354 columns are populated
   on exactly one event_type), so every later field-emitting gate depends on it.
4. **player/role**, then 5. **spatial**, 6. **temporal**, 7. **comparative** (needs the
   subject), 8. **outcome** (needs the grain).

Every gate is optional and self-reporting: it first decides whether its dimension is present
at all, and emits nothing if not - inventing a condition silently narrows the coach's
results. Two guardrails run inside the chain: `answerability.check()` before any filter is
emitted, and `query_schema.validate_filter()` on every filter, which rejects a column that
is null for the event type it targets.

## Open / not yet decided
- **Stage 2 extraction quality - measured, and not good enough yet.** 5 of 13 comparable
  questions return the right rows. The main cause is architectural: each gate sees only the
  question, so gates encode the same idea at different levels and the AND silently shrinks
  the answer. The planned fix is to pass each gate the earlier gates' filters, plus a check
  that runs the finished query and flags zero rows. **Grade with `parse_exec.py` (does the
  query execute to the right rows?), never column recall, which reported 71% on the same run.**
- **Clip ranking and output (stage 5)** - not started. `team_possession_id` already
  collapses matching rows within one possession (Q66 turnovers go 749 -> 661), and phase-2
  predicates return evidence frames usable as clip in/out points, but ranking itself is
  undecided.
- **Captain identity** - needs an external roster source; `data/captains.json` is the hook.
- Whether/when to add a vector-embedding fallback (explicitly deferred, not rejected). Its
  strongest case is query-by-example, which needs stage 5's clips to exist first.

Resolved since this list was first written: the validation stage (the phase-2 predicate
harness *is* the validation), the query-parsing chain (built), and every buildable
unresolved question (Tier 3 complete).

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
