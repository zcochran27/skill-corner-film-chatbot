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
- `scripts/test_all_questions.py` — the full follow-up: all 80 questions from
  `coach_question_test_set.md` run against all 20 real matches (94,517 events), each
  tagged exact/approximate/unresolved. **47 exact, 22 approximate, 11 unresolved.** Full
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

## Open / not yet decided
- Exact ordering/dependency of the 8 gates within the parsing chain.
- Validation stage design.
- Clip ranking logic — now more concrete since `scripts/test_all_questions.py` reports raw
  event/row hit counts, not deduplicated clips; still need to decide how multiple matching
  rows within one possession collapse into a single clip.
- Whether/when to add a vector-embedding fallback path for the structured filters that
  can't cover a question (explicitly deferred, not rejected).
- The 11 unresolved test-set questions' 4 underlying gaps: no captain flag anywhere in the
  schema, no goal-timestamp field to anchor "N minutes after scoring/conceding" windows,
  a few questions needing raw multi-player tracking geometry (Git-LFS-only upstream, not
  yet pulled), and a handful of concepts with no schema tag at all (offside, "tracked
  back", defender rotation on a beaten fullback).
- The actual query-parsing chain (LLM prompt(s) that turn a coach's question into the
  8-gate structured query) — everything so far has hand-written the target query for a
  known test-set question; no code yet turns free text into one.

## Working style notes
- Brainstorm-then-build sequencing was intentional: test set first, then architecture,
  then code. Don't skip ahead to code without checking a design decision against the
  test set.
- Prefer verifying claims against the official SkillCorner CSV spec (in
  `skillcorner_schema.md`) over assuming a field exists.
