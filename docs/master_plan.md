# Master Plan & Project Status

Single entry point for where this project stands. Start here, then follow the links.

| Doc | What it holds |
|---|---|
| `CLAUDE.md` | Project brief, architecture decisions, open questions |
| `coach_question_test_set.md` | The 80-question test set everything is measured against |
| `skillcorner_schema.md` | Reference schema (treat as a guide, not ground truth — see §4) |
| `docs/real_data_validation.md` | The first two validation passes (superseded numbers; see §5) |
| `docs/answerability.md` | **Mandated** — what to say when the data cannot answer |
| `docs/data_architecture.md` | **Mandated** bronze/silver/gold layout and layer contracts |
| `docs/enrichment.md` | Tier 1 + Tier 2 enrichment, built and validated |
| `docs/field_catalog.md` | Generated event_type × column availability map |
| `docs/tier3_tracking_plan.md` | Tracking prerequisites + metric definitions (§2 superseded) |
| `docs/tier3_lazy_retrieval_plan.md` | **Adopted Tier 3 design** — lazy per-event confirmation |
| `scripts/frame_index.py` | Tier 3 random access into tracking (`--benchmark`) |
| `scripts/mirror_sign.py` | Measured tracking/event coordinate sign per (match, team, period) |
| `scripts/predicates.py` | Phase-2 predicate harness (`--selftest`) |
| `scripts/setpiece.py` | Phase-2 set-piece predicates: Q68, Q70 (`--sensitivity`) |
| `scripts/defensive.py` | Phase-2 recovery runs: Q66 (`--distribution`) |
| `docs/test_results_enriched.md` | Current full 80-question run output |

---

## 1. Where the pipeline is

`CLAUDE.md` defines five stages. Three are built, two are not.

| Stage | Status | Notes |
|---|---|---|
| 0. Data layers | **Built** | Bronze/silver/gold mandated; consumer load 9.8s -> 0.73s |
| 1. Preprocessing / enrichment | **Built** | `enrich.py` derivations applied by `build_gold.py`, 32 derived columns |
| 2. Query parsing (8 gates) | **Not started** | No code turns free text into a structured query yet |
| 3. Retrieval — phase 1 (events) | **Built** | Median 5ms, max 75ms across 73 timed questions |
| 3. Retrieval — phase 2 (tracking) | **Working** | Q66/Q68/Q70 exact via tracking; 1.6-7.0s vs a 7.7ms event-filter median |
| 4. Validation | **Designed** | Folded into phase 2: the predicate *is* the validation |
| 5. Output / ranking | **Not started** | Clip dedup now tractable via `team_possession_id` |

**The largest gap is stage 2, not tracking.** Every result so far comes from a hand-written
query for a known test-set question. Nothing yet converts a coach's sentence into the 8-gate
structured form. The enrichment work has been about making sure there is something *worth*
parsing into — that is now true, and stage 2 is the thing standing between this and a
working product.

---

## 2. What has been done

**Validation pass 1** — 4 representative queries against one real match. Confirmed the
structured-filtering bet; caught that real value vocab is richer than the synthetic
generator's (a regain resolves as `pass_interception`, not only `recovery`).

**Validation pass 2** — all 80 questions against all 20 matches (94,517 events). Result:
47 exact / 22 approximate / 11 unresolved. Established that Category 6
(comparative/relational) is mostly resolvable from event fields, contrary to the original
assumption that it needed raw tracking.

**Tier 1 + Tier 2 enrichment** (`scripts/enrich.py`, `docs/enrichment.md`) — 32 derived
columns across six workstreams: goal timeline, parameterised outcome windows, true
possession chains, mirror-safe per-match-scaled zone flags, squad units, captain hook.
Result: **57 / 16 / 7**.

**Field availability catalog** (`scripts/field_catalog.py`) — generated guardrail against
the project's dominant failure mode (§4a). 354 columns mapped.

**Tracking prerequisites verified** — LFS pull works anonymously; all 20 matches (1.82 GB)
fetched. Frame structure, coverage, and the event↔tracking coordinate relationship measured
rather than assumed.

**Tier 3 design** — precompute architecture drafted, then replaced with lazy per-event
confirmation after measuring the access cost. See §6.

**Bronze/silver/gold data architecture** (`docs/data_architecture.md`) — mandated and
implemented. Raw data is now immutable in Bronze, cleaned and typed in Silver behind
validation gates that fail the build, and derived in Gold. Consumer load time **9.8s →
0.73s**; test-set results unchanged through the migration, which is the check that it
preserved behaviour.

---

## 3. Current results on the test set

**61 exact · 14 approximate · 5 unresolved** (from 47 / 22 / 11).

Three of those exacts (Q66, Q68, Q70) are resolved by phase-2 tracking rather than event
filters. **Negative/absence — the weakest category in the first pass at 4 exact / 5
approximate / 1 unresolved — is now fully resolved at 7 / 3 / 0**, which is the clearest
evidence the lazy Tier 3 design works: that category is weak precisely where events record
what happened rather than what did not.

| Category | exact | approx | unresolved |
|---|---|---|---|
| 1. Player-specific | 8 | 2 | 1 |
| 2. Spatial | **10** | 1 | **0** |
| 3. Event-type | 9 | 1 | 1 |
| 4. Sequence | 9 | 2 | 0 |
| 5. Game-state | 9 | 1 | 0 |
| 6. Comparative | 5 | 3 | 2 |
| 7. Negative/absence | **7** | 3 | **0** |
| 8. Composite | 4 | 1 | 1 |

Every category has a majority resolved, and the two weakest (Negative/absence, Comparative)
are weak for a stated reason: both depend on relational or counterfactual conditions that
event rows only partly express.

**The 7 unresolved, by what would actually fix them:**

| # | Question | Blocked on |
|---|---|---|
| 5, 78 | captain | External roster data — not in SkillCorner at all |
| — | *(all five now have registered refusal text — see `docs/answerability.md`)* | |
| ~~17~~ | ~~block shape~~ | **resolved from phases, no tracking needed** - see 4h |
| 56, 59 | dragged out of position, foot race | Tier 3 tracking (dyadic) |
| ~~66~~ | ~~"tracked back"~~ | **resolved** - `scripts/defensive.py` |
| 27 | offside calls | **Nothing.** Tracking gives offside *positions*, never referee *calls* |

**The 14 approximate** (6, 7, 18, 25, 36, 44, 48, 55, 57, 62, 67, 69, 74, 79) split
roughly into: semantic narrowing where the coach's concept is finer than any available flag
(through ball, cutback, "game management"), set-piece geometry that Tier 3 will make exact
(68, 70), cover-rotation geometry (69), and unverifiable counterfactuals (67, 74).

**Timing.** 73 timed questions: total 727ms, median 5.5ms, max 75ms. Enrichment moved
several of the slowest off the phase-join path — Q43 went from 289ms (previously the
slowest) to 7ms. Retrieval is not and will not be the bottleneck; the LLM parsing call in
stage 2 will dominate.

**One zero-hit query remains, and it is a real finding:** Q6 (GK passing into midfield under
pressure) returns 0 across all 20 matches. Every GK pass into the middle third in the corpus
is tagged `no_pressure` or `low_pressure`.

---

## 4. Issues run into, and the fixes in place

### 4a. Silent zeros — the dominant failure mode

A filter against a column that is null for that event type returns **zero rows, not an
error**. It looks like a finding. This has now happened five separate times:

| Instance | Effect |
|---|---|
| `give_and_go` filtered on `player_possession` (it only lives on `off_ball_run`) | 4 questions, 0 hits |
| `interplayer_distance`, `current_team_out_of_possession_previous_phase_type`, `player_in_possession_position` on the wrong event type | 3 questions, 0 hits |
| Own-box queries built on a misdiagnosed `penalty_area_start` | 2 questions, 0 hits |
| `playing_time.sequences` absent from 9 of 20 matches | Half of all unit flags silently null |
| Q69 anti-join counting the beaten defender as his own cover | 0 hits across 20 matches |

**Fix in place:** `scripts/field_catalog.py` generates `docs/field_catalog.md` — of 354
columns, **168 are populated on exactly one event_type** and **28 are missing entirely from
at least one match**. The query-parsing stage must consult it before emitting a filter
rather than inferring availability. The catalog caught the `playing_time.sequences` case
during development.

**Still open:** nothing yet *enforces* this at query time. A `validate_filter(column,
event_type)` assertion in the retrieval layer would turn a silent zero into a loud error,
and should land alongside stage 2.

### 4b. The schema doc is a guide, not ground truth

Four documented behaviours turned out to be wrong or incomplete on real data:

| Claim | Reality | Fix |
|---|---|---|
| `penalty_area_start/end` fires only for the attacking box | Fires for **either** box — 1,062 of 3,863 True rows are in the row team's own box | `own_/opp_` split, per-match scaled |
| Goal timestamps aren't derivable | They are, but **neither signal is complete alone** — markers missing for some goals, score bumps missing for late goals, markers mis-attributed on defensive rows | Union of both, validated against official scores |
| `lead_to_shot` means "this team shot within 10s" | On a **defensive** row it refers to the *opponent's* shot — 1,716 rows disagree otherwise | `_same_team` and `_opponent` variants; 99.8% agreement |
| `phase_index` ≈ a possession | Different grouping: 7,523 phases vs 4,071 possessions; possessions cross phases, phases contain both teams | `team_possession_id` from native possession delimiters |

**Fix in place:** verify against data before trusting the spec. This is now recorded as a
working-style note in `CLAUDE.md`.

### 4c. Definitional mismatches — the query meant something else

| Issue | Evidence | Fix |
|---|---|---|
| `lead_to_shot` is a fixed 10s boolean; questions ask 4s / 6s / 8s | Q39, Q41, Q44, Q77, Q79 | `seconds_to_next_shot_same_team` makes the window a parameter |
| `lead_to_shot` on a clearance measures the wrong team's shot | False on all 60 header clearances — read as a finding, actually a definitional mismatch | Q36 rewritten against the clearing team |
| `x_end` on a possession row is where the **passer stood**, not the delivery destination | Q21: 4 rows vs **288** by reception point | `_reception` zone flags from `player_targeted_x/y_reception` |

### 4d. Geometry and coordinate frames

- **Pitch size varies** (104m ×4, 105m ×10, 106m ×6) and coordinates are real metres, so the
  hardcoded `x < -36` own-box test was off by up to 0.5m. Now scaled per match.
- **Every event row is mirrored into its own team's attacking frame** — verified empirically
  (median |x_obe + x_pp| = 4.75m vs |x_obe − x_pp| = 36.6m), so `x < 0` is the row player's
  own half for *every* event type including defensive engagements.
- **Tracking is absolute, events are mirrored, and the sign flips at halftime** — measured
  per (team, period). This is the single most likely source of silent wrong answers in
  Tier 3 and must be resolved by measurement, not assumption.

### 4h. A whole file was dismissed as redundant

Q17 ("midfield block compressed into a narrow shape") sat as unresolved-needs-tracking
through three planning documents, including my own Tier 3 plan, which listed it as the
tractable *team-shape geometry* item.

It needs no tracking. `_phases_of_play.csv` carries `team_in/out_of_possession_width` and
`_length` per phase - columns that exist **nowhere in the events table**. They were never
looked at because the first validation pass concluded phases were redundant: *"phase context
is already inlined on every dynamic-events row"*. That is true of the phase **type** and
false of these measures, and the file was then skipped in every later pass.

The columns behave exactly as football predicts, which is the check that they mean what they
claim: median out-of-possession width runs 22.8m defending a set play, 34.1m low block,
36.3m medium, 37.1m high, 50.2m defending a quick break.

Q17 now resolves in **3.9ms** as a phase-level query against a per-team baseline, versus the
1.5-7s a tracking predicate costs. **Same lesson as 4b, one level up: the earlier conclusion
was about a whole data source rather than a single field, so nothing re-examined it.** Before
building expensive machinery, re-check what the cheap sources actually contain.

### 4e. Tracking measurement traps

Three bugs found while building phase-2 predicates, all the same family: an arithmetic
mistake that produces plausible-looking output rather than an error.

| Bug | Symptom | How it was caught |
|---|---|---|
| `sample_frames(lo, hi, 1)` returns the window MIDPOINT; the reference predicate took the first frame in the window | Silently measured the middle of an event instead of its start - ~1.2s of sprinting for an off-ball run | Self-test agreement stuck at 95.7%, with `source_gap = 0.000000` rows that should have been exact |
| Blanket 70% coverage gate applied to set-piece windows | Rejected **79% of corners** before the predicate ran; a corner window legitimately spans dead-ball setup | Only 21% of corners resolved, against 92% delivery detection measured separately |
| `np.convolve(..., mode="same")` zero-pads when smoothing | Position collapses toward the origin at window edges: a player at x=30 appears to move 30m in 0.1s | Median "peak speed" of **245 km/h** - the distribution was checked before thresholding |

What they share: **look at the distribution of a derived quantity before using it as a
filter.** Two of the three were invisible in the pass/fail outcome and obvious in the spread.

### 4f. Dtype fragility — a latent version of 4a

The raw CSVs produce **130 object-dtype columns costing 635 MB in memory, 58 of them
booleans**. They currently hold real Python `bool` values (NaN forces object dtype), so
`col == True` works and agrees exactly with the `_as_bool()` helper — checked, not assumed.

But that is a property of how pandas parsed *these* files, not a guarantee. A column that is
all-null in one match, or read with different parameters, lands as `"True"`/`"False"`
strings, and then every `== True` comparison silently returns False across the board. That
is failure mode 4a again, with a different trigger and a much wider blast radius.

**Fix in place:** the Silver layer casts once at the boundary (`build_silver.py:conform_dtypes`)
— booleans to nullable `boolean`, low-cardinality strings to `category`. Result: **635 MB →
210 MB, 130 object columns → 11**, and downstream code no longer needs `_as_bool()`.

### 4g. Infrastructure

- Tracking was recorded as blocked on Git LFS. It is not — `git lfs pull` works anonymously.
  All 20 matches, 1.82 GB.
- `fetch_match_data.sh` pulled one match at a time and no tracking. Rewritten: `all` plus
  an opt-in `--tracking`.
- Windows specifics: upstream notebook paths exceed MAX_PATH (harmless, data still checks
  out); `json.load` needs explicit `encoding="utf-8"` or cp1252 breaks on player names.

---

## 5. Architecture decisions locked in

1. **Deterministic structured filtering, no embeddings for v1.** Holds up: 73 of 80
   questions resolve exactly or approximately. A vector fallback remains a deferred
   exploration item, not a rejected one.
2. **Enrichment is preprocessing, not query-time.** Derived columns are computed once
   (~20s) and filtered like native ones.
3. **Tier 3 is lazy per-event confirmation, not bulk precompute.** Phase 1 event filters
   produce a candidate set (median 156 events), then only those events' frames are fetched —
   ~0.6% of the corpus. This is what `CLAUDE.md`'s Retrieval stage specified all along.
4. **A thin eager tracking base is worth it for cheap, targetable fields only.** The
   per-frame cost split is stark: spreads/centroids/nearest-opponent at **22 µs/frame** (39s
   for the whole corpus) versus convex hull at **625 µs/frame** (~18 min). Precompute the
   cheap scalars and the per-team baselines derived from them; sweep lazily for anything
   advanced, dyadic, or query-parameterised.
5. **Sample frames within the window.** Convex hull on all 50 frames of a 156-candidate job
   costs 4.9s; 5 frames per window costs 0.5s. Most geometric questions want the shape at a
   few instants, not 10×/second. Sample density is a per-predicate parameter.
6. **Predicates return evidence, not booleans.** The frames that justified a match are the
   clip's in/out points — this feeds ranking and makes wrong answers replayable.
7. **"Unmeasurable" is not "didn't happen."** 27% of frames lack player data. Since the
   negative/absence gate turns absence into a positive answer, predicates must return
   `insufficient_data` rather than a false negative.

---

## 6. Next steps

### Immediate — Tier 3 foundation
1. ~~`scripts/frame_index.py`~~ — **done.** Index built by `build_silver.py`; module
   provides window/sampled/at fetches, staleness validation and coverage. Measured on real
   event windows: ~6 ms/window full density, ~0.9 ms sampled, 99.8% coverage. Parsing is
   96% of the cost, so sampling is the dominant optimisation (7x).
2. ~~Mirror-sign resolver~~ — **done.** 80 groups at 100% confidence; opposite-signs and
   halftime-flip invariants both hold.
3. ~~Predicate harness~~ — **done.** Chain validated end-to-end by reproducing a Gold column
   from tracking: 100.00% (344/344) on concordant, non-borderline candidates.

### Then — Tier 3 metrics, cheapest first
4. ~~**Set-piece geometry**~~ — **done.** Q68 and Q70 are exact (`scripts/setpiece.py`).
   Q68 is returned **ranked by closest approach to the near post rather than thresholded**,
   because a fixed zone moves the answer between 12% and 48% (`--sensitivity`) — ranking has
   no arbitrary cutoff and stage 5 needs ranking anyway.
5. ~~**Recovery runs**~~ — **done.** Q66 resolved (`scripts/defensive.py`), also ranked
   rather than thresholded. Gives the test set a defensive-work vocabulary the event schema
   lacks entirely.
6. **Thin eager base** — cheap per-frame scalars + per-team baselines (~40s build).
7. ~~**Team shape** → Q17~~ — **done, and it never needed tracking** (see 4h). Remaining:
   **dyadic** → Q59, then Q56 — the hardest and most parameter-sensitive; prototype on one
   match and eyeball the output before trusting it.

Projected ceiling once the remaining Tier 3 work lands: **~63 exact, ~14 approximate,
3 unresolved** (captain x2 plus offside). Only Q56 and Q59 are still blocked, both on dyadic
player-pair geometry.

### In parallel — the actual product gap
8. **Stage 2, the query-parsing chain.** Still zero code. Needs: the gate ordering and
   dependencies (undecided), a per-gate "does this apply?" self-report, the
   `validate_filter` guardrail from §4a so a hallucinated column fails loudly, and an
   `answerability.check(concept)` call before any filter is emitted so unanswerable
   questions are refused rather than silently proxied (`docs/answerability.md`).
9. **Stage 5, ranking and clip dedup.** `team_possession_id` gives the grouping and Tier 3
   evidence frames give in/out points — most of a clip segmenter now exists. Ranking logic
   itself is still undecided.

### Vector / similarity search — scoped, still deferred

Revisited while building the set-piece predicates. Three different things get called
similarity search here, and they do not share a verdict:

- **Query-by-example is the strong case.** "More like this clip" needs no labels up front —
  the clip *is* the label, and the coach defines a fuzzy concept by demonstration instead of
  someone guessing a rectangle. Needs the clip UI to exist first.
- **Untagged concepts are the second case** — offside (27), "tracked back" (66), and the
  semantic-narrowing approximates (through ball, cutback, "game management"). SkillCorner
  ships prior art: `Part6_BuildYourOwnMetric_Detecting_and_Evaluating_Cutback_Opportunities.ipynb`
  in their tutorials repo is Q18's exact problem.
- **It would be strictly worse for the 59 exact questions**, which already resolve
  deterministically in milliseconds with guarantees.

Two arguments against reaching for it sooner, both learned from Q68:

1. **Hidden vs visible arbitrariness.** Replacing a hand-drawn zone with a learned boundary
   does not remove the judgement, it conceals it. A rectangle can be sensitivity-swept and
   printed; an embedding cannot, and this is a tool where a coach must be able to see why a
   clip surfaced.
2. **Similarity is poor at absence.** Q68 and Q70 are negative questions, and you cannot
   reliably embed the absence of a thing. Even with a learned near-post concept the
   anti-join stays deterministic — similarity would be a component, never a replacement.

The cheaper move, taken for Q68: **rank instead of threshold.** The predicate already
measures closest approach in metres, so ordering by it removes the arbitrary cutoff without
any new machinery.

Architecturally this stays additive: a learned or nearest-neighbour predicate satisfies the
same `PredicateResult` contract as a geometric one, so the harness needs no rework to try it.

### Known dead ends — do not spend time here
- **Q27 offside.** Tracking gives offside *positions*; referee *calls* are not in any
  available feed. Implement `in_offside_position_at_pass` as a related filter, but keep Q27
  unresolved rather than quietly answering a different question.
- **Q5 / Q78 captain.** Not derivable from anything SkillCorner ships. `data/captains.json`
  is a one-file fix whenever an external source is available.

---

## 7. Reproducing current state

```bash
pip install -r requirements.txt
scripts/fetch_match_data.sh all --tracking   # Bronze (~1.8 GB; omit --tracking for 89 MB)
python scripts/build_silver.py               # Silver (~34 MB) + validation gates
python scripts/build_gold.py                 # Gold   (~16 MB) + 32 derived columns
python scripts/field_catalog.py              # regenerates docs/field_catalog.md
python scripts/test_all_questions.py         # all 80 questions
```
