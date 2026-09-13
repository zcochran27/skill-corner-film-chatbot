# Tier 3 — Lazy Per-Event Tracking Confirmation

**Supersedes the precompute architecture in `docs/tier3_tracking_plan.md` §2.** That section
proposed featurising all 1.2M tracking frames offline into Parquet tables and joining them
to events. This plan inverts that: use the Tier 1/Tier 2 event filters to produce a small
candidate set, then pull *only* the tracking frames belonging to those candidates and decide
per candidate whether the moment actually matches.

The metric definitions in `docs/tier3_tracking_plan.md` §3 and §4 stay valid — this changes
*when and where* they are computed, not what they compute.

This restores what `CLAUDE.md` already specified for the Retrieval stage:

> run the parsed structured query against the enriched event data (phase 1: event-level
> filtering) then, if a spatial condition was flagged, a second pass against raw tracking
> data for fine-grained geometric confirmation (phase 2)

It also fills in the **Validation** stage, listed in `CLAUDE.md` as "not yet designed in
detail" — "does this moment actually answer the coach's question" is exactly what the phase-2
predicate decides.

---

## 1. Why this shape fits the data

Measured on the real corpus, not estimated:

| Quantity | Value |
|---|---|
| Events | 94,517 across 20 matches |
| Median event duration | **10 frames (1.0s)**; p90 32; p99 65; max 251 |
| Median candidate set after Tier 1/2 filters | **156 events**; p75 679; p90 1,943; max 6,474 |
| Questions with ≤200 candidates | **39 of 72** |
| Tracking frames | 60,301/match, 44,244 (73%) with full 22-player data |

A typical tracking-gated question therefore needs ~156 windows of ~50 frames (event span
plus context) — about **7,800 frames, or 0.6% of the 1.2M-frame corpus.** Precomputing the
other 99.4% is the part worth questioning.

---

## 2. Architecture

Three components. Only the first is new infrastructure.

```
parsed query (8 gates)
      |
      v
[1] Phase 1: event filter  ------------------> candidate events (median 156 rows)
      enrich.load_enriched(), existing columns    each with match_id, frame_start, frame_end,
                                                  team_id, player_id, team_possession_id
      |
      v
[2] Window resolver  -------------------------> (match_id, frame_lo, frame_hi) per candidate
      window policy per gate type                + any extra frame sets (baseline, dyad)
      |
      v
[3] Frame index + fetch  ---------------------> raw frames, only those windows
      frame -> byte offset, seek + readline
      |
      v
[4] Predicate evaluator  ---------------------> keep / drop + evidence
      the §3/§4 metrics, per candidate           (the measured value, for ranking + debugging)
      |
      v
ranked clips, each with the frames that justify it
```

### [3] The frame index — the one piece of new infrastructure

`_tracking_extrapolated.jsonl` is line-oriented and not seekable by frame, so random access
needs a `frame -> byte offset` map. Building it is one sequential pass that parses only the
frame number out of each line's first 24 bytes, never the whole JSON:

- **0.16s per match**, 60,301 entries, **~0.7 MB in memory** (~14 MB for all 20 matches)
- Persist as a sidecar `{id}_frame_index.npy` (int64 offsets) so it is built once, not per
  session. Rebuild is cheap enough that staleness is a non-issue — regenerate whenever the
  tracking file's mtime/size changes.

Measured against **real event windows** (`scripts/frame_index.py --benchmark`):

| Operation | Cold page cache | Warm page cache |
|---|---|---|
| One ~45-frame window, full density | ~6 ms | **~1.8 ms** |
| Typical query, 156 windows, full density | ~960 ms | **~280 ms** |
| Typical query, 156 windows, 5 samples each | ~140 ms | **~50 ms** |

The 3x spread is the OS page cache: a 91 MB tracking file read repeatedly stays resident,
so the warm column is what a running service sees and the cold column is first touch after
a restart. Both are real; quote the cold one when sizing worst case.

> **Correction to an earlier estimate.** This plan originally quoted 1.79 ms/window and
> 280 ms per query from a single benchmark. Two things were wrong with it: it drew *random*
> frames, 27% of which are near-empty ball-out-of-play lines that parse almost instantly,
> and it was run warm. Real event windows are always full 22-player frames. The original
> figure happens to match the warm-cache column above by coincidence, not because it
> measured the same thing. The design conclusion is unchanged.

**The cost is parsing, not I/O — and that changes which optimisation matters.** For a
156-window job, seek + readline alone is **33 ms**; parsing the same frames is **837 ms**,
i.e. **96% of the total**. A full 22-player frame is ~1.9 KB and costs ~86 µs to parse.

So the lever is *parsing fewer frames*, not reading fewer bytes:

- **Sampling is the dominant optimisation**, not a minor tweak — 7x measured (960 ms → 137 ms).
- `orjson` is the other easy win (~2-5x); `frame_index.py` uses it automatically when
  installed and falls back to stdlib `json` otherwise.
- It also strengthens the case for the thin eager Gold base: cheap scalars computed in one
  sequential parse pass (~30 s for the corpus) avoid re-parsing those frames ever again.

Reopening the file per window is free relative to parsing, so no connection pooling is
needed; keep it simple.

**Coverage on real event windows is 99.8%, not 73%.** The ~27% of unusable frames are
entirely ball-out-of-play, which by construction contains no events — in a 156-window sample,
156/156 were sufficient. The `insufficient_data` path is a genuine safety net rather than a
common case, which is a better position than this plan originally assumed.

### [2] Window policy — what "related frames" means per gate

The event row already carries `frame_start`/`frame_end`, so the event→frame mapping is
mostly arithmetic rather than a stored table. What varies is how much context each *kind* of
question needs:

| Gate / question type | Window | Rationale |
|---|---|---|
| Spatial, instantaneous ("was he in the box") | `[frame_start, frame_end]` | the event span is the moment |
| Set-piece geometry (Q68, Q70) | `[delivery - 10, delivery + 30]` | need the shape at delivery and first contact |
| Team shape (Q17) | `[frame_start - 20, frame_end + 20]` | shape is a ~2s property, not an instant |
| Recovery run (Q66) | `[frame_start, frame_start + 100]` | a run is up to ~10s |
| Foot race (Q59) | `[frame_start - 10, frame_end + 50]` | race starts before the event fires |
| Dragged out of position (Q56) | event window **plus** a disjoint baseline window | see below |

Two cases are *not* a contiguous span around the event, and these are where an explicit
mapping table earns its place rather than arithmetic:

- **Baseline windows (Q56).** "Dragged out of position" needs the player's own positional
  norm — a rolling median over ~2 min of their frames in the same phase type — which is a
  second, disjoint frame set per candidate.
- **Chain windows.** Tier 1's `team_possession_id` already groups events into unbroken
  possessions, so `chain_id -> [min(frame_start), max(frame_end)]` gives a natural clip span.
  Precompute this small table (4,071 chains, one row each) since it is cheap and reused by
  both phase 2 and clip segmentation.

### Data-quality findings from building this

Measured while validating the chain, and load-bearing for every predicate:

- **Event coordinates are not a copy of tracking.** Dynamic Events are "produced by combining
  SkillCorner Tracking v3 + Wyscout event data", so the two sources are a fusion. Measured:
  they place the same player >1m apart on **1.3%** of rows, from a mix of player-identity
  errors (consistent with the documented ~97% identity accuracy), frame alignment, and
  unexplained residue. Predicates must not assume the event row's x/y and the tracking x/y
  are interchangeable.
- **`is_detected=False` means measurably worse.** Extrapolated positions agree with the event
  table to within 0.5m **88.6%** of the time versus **98.5%** for detected ones, and 13% of
  player-frames are extrapolated. `signed_players(..., detected_only=True)` trades coverage
  for precision where that matters.
- **Anchoring is a live trap.** `sample_frames(lo, hi, 1)` returns the window MIDPOINT, which
  is right for "what was the shape during this event" and wrong for "where was he when it
  started". The reference predicate initially took the first frame in the window and was
  silently measuring the midpoint - for an off-ball run that is ~1.2s of sprinting, several
  metres, enough to flip the answer. Predicates that mean an instant must use
  `Window.frame_at(ctx.frame_start)`.

### [4] Predicate evaluator

One function per geometric concept, with a uniform signature so the query builder can compose
them:

```python
def predicate(frames: list[dict], ctx: EventContext, **params) -> PredicateResult:
    """frames: the fetched window, absolute tracking coordinates.
       ctx:    match/team/player/period + the mirror sign for this (team, period).
       returns: matched: bool, value: float, evidence_frames: list[int]"""
```

Returning the **measured value and the frames that justify it**, not just a boolean, is what
makes this feed ranking (§5 of `CLAUDE.md`'s pipeline) and makes a wrong answer debuggable —
you can replay the exact frames that passed.

**The mirror sign belongs here.** Tracking is absolute; events are mirrored per row so the
row team attacks +x, and the sign flips at halftime (verified in
`docs/tier3_tracking_plan.md` §1). Resolve `sign(match, team, period)` once per session into
a small lookup and apply it at fetch time, so every predicate sees coordinates in one
declared frame. This is the single most likely source of silent wrong answers in the whole
stage.

---

## 3. Processing one event, concretely

Worked example — Q59, "foot races between their centre back and an opposing striker in
behind". Candidate generation is event-level; confirmation is geometric.

**Phase 1** narrows to on-ball engagements / off-ball runs where a CB and an opposing
forward are both involved and the ball went in behind. Say 60 candidates.

**Per candidate:**

1. **Resolve the window.** `frame_start = 21840`, `frame_end = 21868`. Foot races start
   before the event fires, so fetch `[21830, 21918]` — 88 frames.
2. **Fetch.** Seek to `index[21830]`, read 88 lines, `json.loads` each. ~3 ms.
3. **Drop unusable frames.** ~27% of frames carry an empty `player_data`; if coverage in the
   window falls below a threshold (say 70%), return `insufficient_data` rather than a false
   negative. **Distinguishing "did not happen" from "could not be measured" matters** — the
   negative/absence gate (Q65–Q74) turns absence into a positive answer, so silently
   treating unmeasurable as absent would manufacture findings.
4. **Sign into the event frame.** `xy *= sign(match, team, period)`.
5. **Extract the two players' series.** CB and forward `player_id` are on the event row;
   pull their (x, y) per frame into two arrays.
6. **Smooth before differentiating.** Positions are extrapolated (`is_detected: False`) and
   the upstream README flags speed as needing smoothing — centred rolling mean over ~5
   frames (0.5s) before computing velocity.
7. **Evaluate the predicate.** Both above `hsr` (20 km/h) for ≥10 consecutive frames;
   velocity vectors cosine-similar > 0.8; both directed toward the defending goal;
   `separation_gain` over the window as the value.
8. **Return** `matched`, `value = separation_gain`, `evidence_frames = [21847..21901]` — which
   is also the clip's in/out point.

**Cost:** 60 candidates × ~3 ms fetch + sub-ms arithmetic ≈ **under 300 ms**.

### Frame subsampling — the optimisation that matters most

Per-frame geometry cost splits sharply:

| Metric | Per frame | Over the full corpus (884k usable × 2 teams) |
|---|---|---|
| Spreads / centroid / distances | **22 µs** | 39 s |
| Convex hull | **625 µs** | **1,106 s (~18 min)** |

At 625 µs, running convex hull on every frame of a 156×50 job costs **4.9 s** — too slow to
stay interactive. But most geometric questions need the shape at a *few instants* (at the
pass, at reception, at first contact), not 10 times a second. Sampling 5 frames per window
instead of 50 drops that to **0.5 s**. Make sample density a per-predicate parameter:
instantaneous predicates sample; genuinely time-series predicates (foot race, recovery run)
take the full window but only use cheap metrics.

---

## 4. Pros and cons

### Pros

1. **It is the architecture the project already chose.** Phase 1 → phase 2 confirmation is
   what `CLAUDE.md` specifies; precompute-everything was the drift.
2. **Iteration speed.** Changing a threshold or redefining "narrow" is free. Under precompute
   it is an 18-minute rebuild per change — and these definitions are exactly the ones that
   will need a dozen iterations to get right.
3. **Pays only for what is asked.** 0.6% of frames for a typical query.
4. **No derived-table lifecycle.** Nothing to version, invalidate, or keep in sync — the
   category of bug that produced every silent zero so far.
5. **Query-time parameterisation.** "Within 3 metres" can come from the coach's question
   rather than being baked in at build time.
6. **Evidence frames fall out for free**, giving clip in/out points and a debuggable trail
   for exactly why a clip was returned.
7. **Cheap to abandon.** If it proves too slow for some question class, precomputing that one
   metric later is additive — the frame index is reused either way.

### Cons

1. **Latency moves into the query path.** Event-only queries run in 5–75 ms; add ~280 ms
   typical, ~3.5 s at p90, ~12 s worst case. Still small next to the LLM parsing call, but no
   longer negligible, and it scales with candidate-set size rather than being fixed.
2. **No reuse across queries.** Two questions over the same phase of play refetch and
   recompute the same frames. A session cache keyed on `(match_id, frame_lo, frame_hi)` helps
   within a session but not across users.
3. **It needs an event-level anchor, and some questions have none.** "Show me every moment
   their block was narrow" has no event to filter on first. Phase 1 would have to emit a
   coarse candidate set (e.g. every out-of-possession phase) and phase 2 would degrade toward
   a full scan. **This is the real architectural limit**, not the latency.
4. **Corpus-level baselines cannot be computed lazily.** "Narrower than their own average"
   needs the average — a corpus statistic by definition. Same for any percentile or z-score
   framing, which is how coaches usually mean these questions.
5. **Aggregate questions are a poor fit.** Q54-style questions ("their passing tempo when
   leading by 2+") want a statistic over everything, not confirmation of a candidate list.
6. **No cross-event ranking without a scan.** "The 10 narrowest blocks this season" requires
   evaluating all of them.
7. **Index/file coupling.** Byte offsets are invalidated by any rewrite of the tracking file;
   needs an mtime/size guard.

---

## 5. Recommendation: lazy by default, with a thin eager base

The measured 22 µs vs 625 µs split makes the boundary obvious, and it resolves cons 4 and 5
without giving up the benefits:

**Precompute eagerly (small, cheap, reused by everything):**
- Cheap per-frame, per-team scalars: block width, height, centroid, defensive-line height,
  nearest-opponent distance per player. **39 s for the whole corpus**, and they are exactly
  what the baselines in con 4 are computed from.
- Per-team, per-phase-type **baseline distributions** of those scalars, so "narrow" can mean
  "below this team's own 20th percentile" rather than an invented metre threshold.
- The `chain_id -> frame span` table (4,071 rows).

**Evaluate lazily (expensive, parameterised, or pair-specific):**
- Convex hull / compactness, pitch control, passing-lane openness.
- Everything dyadic — the pair is named at query time, so precomputing all pairs is
  200M rows of mostly-unwanted work.
- Anything with a threshold the coach's question supplies.

That base is ~40 s to build and a few hundred MB, versus ~20 min and a full derived-feature
warehouse for the eager design — while removing the two cons that would otherwise bite.

---

## 6. Sequencing

1. ~~**`scripts/frame_index.py`**~~ — **built.** Index construction moved into
   `scripts/build_silver.py` (Silver owns derived access structures); `frame_index.py`
   provides `FrameIndex.load()`, `fetch_window()`, `fetch_sampled()`, `fetch_at()`, staleness
   validation against the Bronze fingerprint, and `Window.coverage/.sufficient`.
   `--benchmark` reproduces the numbers above.
2. ~~**Mirror-sign resolver**~~ — **built** (`scripts/mirror_sign.py`, run inside
   `build_silver.py`). All 80 (match, team, period) groups resolve at 100% confidence, and
   both structural invariants hold: the two teams in a period have opposite signs, and every
   team's sign flips at halftime.
3. ~~**Predicate harness**~~ — **built** (`scripts/predicates.py`). `--selftest` validates the
   whole chain by reproducing a Gold column from tracking: 100.00% (344/344) on candidates
   where both sources agree and the point is off the box boundary.
4. ~~**First real predicate: set-piece geometry (Q68, Q70)**~~ — **built**
   (`scripts/setpiece.py`). Both are exact. Measured cost: ~1.5s per question over ~100
   corners with 330-frame windows, against a 6ms median for event-only questions — the
   phase-1/phase-2 contrast this plan predicted, in the test suite's own timing column.
   Three things this surfaced:
   - The event row's `frame_start` is **not** the delivery; it is the first tagged event,
     sometimes the reception (~1.8s after the kick) and sometimes the taker's own possession
     before it. Delivery is found from the ball track instead, resolving 92%.
   - `WindowPolicy` needed a per-policy `min_coverage`. A corner window spans the dead-ball
     setup, so the blanket 70% gate rejected 79% of corners before the predicate ran.
   - "Near post" is a definitional choice, not a measurement: a fixed zone moves the answer
     between 12% and 48%. Q68 is therefore returned **ranked by closest approach**, which
     has no cutoff at all.
5. ~~**Recovery runs (Q66)**~~ - **built** (`scripts/defensive.py`). Confirms the §4 con
   about needing an event anchor: the question is about a player doing nothing, so there is
   no row for him. The workaround is to anchor on his team's turnover - which IS an event -
   and locate the winger by position within the frames. That pattern should generalise to
   most "player failed to do X" questions.
6. ~~**Eager scalar base + baselines**~~ — **built, narrower than planned**
   (`scripts/tracking_base.py`). Only per-player positional baselines were needed (for Q56).
   The per-frame team-shape scalars were deliberately *not* built: their justification was
   Q17, which turned out to need no tracking at all.
7. ~~**Team shape (Q17)**~~ — resolved from `_phases_of_play.csv` without tracking.
   ~~**Dyadic (Q59, then Q56)**~~ — **built** (`scripts/dyadic.py`).

**Tier 3 is complete.** The test set reached its projected ceiling of 63 exact / 14
approximate / 3 unresolved; the remaining three are permanently unanswerable.

Per the project's stated habit, check each stage against the test set before starting the
next, and prefer measuring the data over trusting the spec.
