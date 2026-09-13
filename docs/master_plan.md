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
| `scripts/dyadic.py` | Phase-2 two-player predicates: Q56, Q59 (`--distribution`) |
| `scripts/tracking_base.py` | Eager per-player positional baselines (Gold) |
| `scripts/query_parse.py` | Stage 2: the 8-gate parsing chain (`--offline`, `--test-set`) |
| `scripts/query_schema.py` | Per-gate field vocabulary + `validate_filter()` guardrail |
| `scripts/env.py` | Loads the gitignored `.env`; one place for credential resolution |
| `scripts/parse_cost.py` | Measured stage 2 cost calibration and full-run projection |
| `scripts/parse_eval.py` | Stage 2 accuracy grading against the hand-written queries |
| `scripts/parse_exec.py` | Grades parses by EXECUTING them against Gold - the metric to trust |
| `docs/test_results_enriched.md` | Current full 80-question run output |

---

## 1. Where the pipeline is

`CLAUDE.md` defines five stages. Three are built, two are not.

| Stage | Status | Notes |
|---|---|---|
| 0. Data layers | **Built** | Bronze/silver/gold mandated; consumer load 9.8s -> 0.73s |
| 1. Preprocessing / enrichment | **Built** | `enrich.py` derivations applied by `build_gold.py`, 32 derived columns |
| 2. Query parsing (8 gates) | **Built, improving, NOT ready** | Gates now see the query so far; 7 of 13 comparable questions return the right rows (was 5), no misses or empty results. Two regressions from the event_type gate deferring too far. See 4k, 4l |
| 3. Retrieval — phase 1 (events) | **Built** | Median 5ms, max 75ms across 73 timed questions |
| 3. Retrieval — phase 2 (tracking) | **Working** | Q66/Q68/Q70 exact via tracking; 1.6-7.0s vs a 7.7ms event-filter median |
| 4. Validation | **Designed** | Folded into phase 2: the predicate *is* the validation |
| 5. Output / ranking | **Not started** | Clip dedup now tractable via `team_possession_id` |

**Stage 2 is built and has had two real evaluations** against `claude-opus-5` on a
20-question sample (4k, 4l). It is not ready for the full 80: 7 of 13 comparable questions
return the right rows. Its output is a validated JSON filter spec executed against Gold -
not SQL, and not clips. Nothing yet runs a parsed query end to end with negation, chain grain
or tracking predicates; `parse_exec.py` ANDs filters at event grain.

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

**63 exact · 14 approximate · 3 unresolved** (from 47 / 22 / 11).

**Every question the data can support is now built.** The three that remain are permanently
unanswerable — captain identity and offside calls — and all three carry registered refusal
text (`docs/answerability.md`), so the app never has to guess.

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
| 6. Comparative | **7** | 3 | **0** |
| 7. Negative/absence | **7** | 3 | **0** |
| 8. Composite | 4 | 1 | 1 |

Every category has a majority resolved, and the two weakest (Negative/absence, Comparative)
are weak for a stated reason: both depend on relational or counterfactual conditions that
event rows only partly express.

**The 3 unresolved, and why nothing further can fix them** (struck-through rows were
resolved along the way):

| # | Question | Blocked on |
|---|---|---|
| 5, 78 | captain | External roster data — not in SkillCorner at all |
| — | *(all three carry registered refusal text — see `docs/answerability.md`)* | |
| ~~17~~ | ~~block shape~~ | **resolved from phases, no tracking needed** - see 4h |
| ~~59~~ | ~~foot race~~ | **resolved** - `scripts/dyadic.py` |
| ~~56~~ | ~~dragged out of position~~ | **resolved** - `scripts/dyadic.py` + player baselines |
| ~~66~~ | ~~"tracked back"~~ | **resolved** - `scripts/defensive.py` |
| 27 | offside calls | **Nothing.** Tracking gives offside *positions*, never referee *calls* |

**The 14 approximate** (6, 7, 18, 25, 36, 44, 48, 55, 57, 62, 67, 69, 74, 79) split
roughly into: semantic narrowing where the coach's concept is finer than any available flag
(through ball, cutback, "game management"), cover-rotation geometry (69), and unverifiable
counterfactuals (67, 74). Set-piece geometry (68, 70) used to be on this list; Tier 3 made
both exact.

**Timing.** 77 timed questions. The event-level ones run in single-digit to tens of
milliseconds, with a median around 17ms. The five tracking questions take 2-15 seconds each,
and Q66 (recovery runs over 661 turnovers) is the slowest. That spread is the phase-1/phase-2
contrast working as designed. Enrichment moved several slow event queries off the phase-join
path: Q43 went from 289ms to 7ms. Retrieval is still not the bottleneck. Stage 2 parsing costs
roughly 30-50s per question across 8 sequential API calls, which is more than all of
retrieval combined.

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
| Parse executor compared numbers as strings: a float column holding nulls stores 0 as `"0.0"` | `n_opponents_ahead_end eq 0` and shirt `number eq 9` matched nothing; Q2 and Q80 would have failed a full run |

**Fix in place:** `scripts/field_catalog.py` generates `docs/field_catalog.md` — of 354
columns, **168 are populated on exactly one event_type** and **28 are missing entirely from
at least one match**. The query-parsing stage must consult it before emitting a filter
rather than inferring availability. The catalog caught the `playing_time.sequences` case
during development.

**Now enforced at query time, twice.** `query_schema.validate_filter()` rejects a column
that is null for the event type in play, or a value the column never takes. And the parser
runs every finished query against Gold before returning it: an empty result is flagged with
the filter that emptied it and whether that filter is impossible alone or conflicts with an
earlier one. The second check caught the executor bug above on its first run, which the
first check could not have seen - every column and value was valid.

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

### 4i. Measuring something real, but not the thing asked

Q56 ("centre back dragged out of position by a striker's movement") first measured each
defender's displacement from his own positional baseline and ranked by it. The numbers were
correct and the top results were wrong: they were teams pressing high, where the **whole back
line** had stepped up 25m together. Inspecting the raw coordinates of one case made it
plain - all three of that team's centre backs sat 22-26m from baseline simultaneously.

A collective line push is a pressing scheme, not a striker dragging someone. The fix is to
measure **relative to his own line** - his displacement minus the median displacement of his
back-line colleagues - so a whole line stepping up nets to zero and only a defender leaving
while the others hold registers.

A second version of the same error followed: ranking by the LEVEL of line-relative
displacement surfaced defenders who were already out of line before the run began. Measuring
the GROWTH during the window fixed it.

Distinct from the bugs in 4e: nothing here was arithmetically wrong. The quantity was
computed correctly and simply was not the question. **The check that caught it was reading
the raw coordinates of a single top-ranked result and asking whether the football made
sense** - not a distribution, not a unit test.

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

## 4k. Stage 2's first real evaluation - not ready for a full run

20 questions against `claude-opus-5`, **$1.19**, graded against the hand-written queries
(`scripts/parse_eval.py`). Two numbers matter and they disagree sharply:

| Metric | Result | Trust it? |
|---|---|---|
| Column recall - did it pick the right columns | 71% | **No.** Badly overstates quality |
| **Executed against Gold - does it return the right rows** | **5 of 13** | Yes. This is the headline |

**Refusals failed 3 of 3, then were fixed to 20 of 20 at no cost.** The model correctly
recognised captain and offside, but described them in free text ("captain (player role
attribute)", "offside call") while the registry was an exact-key lookup, so nothing matched.
It also filtered `is_captain == True`. That column exists and is always False, so it passed
every check and would have returned zero rows. Three fixes: free-text concept matching,
`validate_filter()` rejecting values a column never takes, and never-true columns no longer
shown to the model. All three were verified by re-applying them to the model's saved output,
without another API call.

**Column recall is misleading, because correct columns can still produce a wrong answer:**

| Q | Column recall | Executed |
|---|---|---|
| 35 | 67% | **zero rows** |
| 39 | 50% | finds 4% of the answer |
| 42 | **100%** | finds 26% of the answer |

**The systematic flaw: gates duplicate each other.** Each gate sees only the question, never
what earlier gates emitted, so several gates encode the same idea at different levels, and
requiring all of them to match silently shrinks the answer. In Q42 the `sequence` gate
emitted exactly the right query (pressing chain, length >= 3, ended in a regain), and the
`event_type` gate independently added `end_type in [direct_regain, indirect_regain]`. That
keeps only the one engagement that won the ball. Every filter was valid; the answer was cut to
a quarter. Q35 goes to zero because "reached the final third" was encoded by both `sequence`
(the chain reached it) and `spatial` (this row ends there), and no build-up row starting from
a goal kick satisfies both. **This is an architecture problem, not a prompt problem.** The
current chain runs 8 independent calls in a row, not a true chain.

**The opposite failure: the gate recognises the idea but emits no filter for it.** Q55 found
all the right rows buried among 197x as many. The comparative gate named "1v1 isolation"
and wrote no filter. Q25 (111x) and Q21 (17x) behave the same way.

Also found, and fixed:
- **6 of 80 ground-truth labels named columns their own query never uses.** Q25's label said
  `event_subtype`; its query filters `first_line_break`. The grader now reads what each query
  actually executes, which cannot drift.
- **`number` and `is_substitute` were never in Gold.** The test suite joined them in itself,
  so the parser's vocabulary said they didn't exist, and Q2 and Q80 would have failed in a
  full run. Both are now in Gold. The suite still gives 63/14/3 with identical hit counts.

**Recommendation: do not run the full 80 yet.** At 5 of 13, most of the ~$5.50 would go to
measuring a known problem. Fix the duplication first: pass each gate the filters earlier gates
emitted, and add a check that runs the finished query and flags a zero-row result. Then re-run
this same 20-question sample (about $1.20) and compare against 5/13.

## 4l. Stage 2's second evaluation - gates see the query so far

The same 20 questions after the fix recommended in 4k, **$1.05** (cheaper than the first run
despite longer prompts: refusals now stop the chain at the gate that refuses).

**What changed**
- **Each gate is shown the query so far** in its user turn (the system prompt stays cached),
  told to encode each condition once, and may `supersede` an earlier filter it owns by
  emitting a replacement. Gate questions state ownership ("whether it led to a shot belongs
  to the outcome gate"), and `chain_ended_in_shot` moved off the sequence gate's card.
- **Zero-row check** inside the parser (above, 4a).
- **Approximate concepts apply their registered proxy** (`Verdict.proxy`). On Q25 the first
  run attached "these are line-breaking passes" and never filtered `first_line_break`: a
  disclosure describing a filter that did not run (`docs/answerability.md`).
- **Numeric equality bug in the executor** fixed (4a).
- `--regrade` now replays saved model replies through the real `parse()` rather than a copy
  of its logic, so the grade cannot drift from the chain.

Replaying the first run's saved replies through the new guardrails alone - no API calls -
took it from 5 to 6 of 13 (Q25, via the proxy) and flagged Q35's empty result correctly.

**Result: 7 of 13 comparable questions return the right rows, up from 5.** No misses, no
silent zeros, refusals still 20/20.

| Q | First run | Second run | Why |
|---|---|---|---|
| 35 | **zero rows** | equivalent (87/87) | spatial gate no longer restates `chain_reached_final_third` as `third_end` |
| 39 | misses (4%) | equivalent (93%) | sequence gate left the shot window to the outcome gate |
| 42 | misses (26%) | equivalent (100%) | event_type gate no longer adds an event-level regain |
| 25 | superset (111x) | equivalent (28/28) | registered proxy applied |
| 12 | misses (43%) | partial (50%) | see ground-truth note below |
| 75 | misses (20%) | partial (60%) | same |
| 55 | superset (197x) | partial (79%) | still no filter for "isolated"; player gate also dropped wing-backs this time |
| 21 | superset (17x) | superset (17x) | unchanged; "six-yard box *versus* penalty spot" is arguably both zones |
| **49** | equivalent | **partial (52%)** | **regression, caused by the context** |
| **52** | equivalent | **superset (6x)** | **regression, caused by the context** |
| 1, 9, 23 | equivalent | equivalent | |

**The two regressions share one cause: the event_type gate deferred too far.** "Encode each
condition once" works for conditions and fails for the event type, which is structural.
- Q52: the event_type gate decided "direct play" was already covered by the sequence gate's
  phase filter and emitted **no event type at all**. The query returned all four event
  types - including defending-team rows, whose `game_state` is the *defender's*, so
  "losing" pulled in 18 extra possessions. Not a grain artifact: 52 possessions vs 34.
- Q49: the sequence gate wrote "pressing" as `pressing_chain == True`, and the event_type
  gate deferred to that narrower encoding instead of the pressing subtypes, losing single
  pressing actions outside chains.

**`supersedes` was never used** in 20 questions. Q39 was fixed by the ownership wording, not
by a supersede, so that mechanism is still unexercised.

**Ground-truth note - Q12 and Q75.** Both ask about "their *left* winger"; both hand-written
queries use `player_position in [LW, RW]`. The parser's `LW` is what the question says.
Against LW-only rows, Q12's parse finds 46 of 46 in 63 rows, which is equivalent. The label
has not been changed - that is a test-set decision, and it moves the 63/14/3 hit counts.

**Next, in order**
1. **Make the event type non-deferrable.** Prompt: the event_type gate always emits an
   `event_type` filter; another gate's condition never covers it. Plus a free deterministic
   check that flags any query with no event type, since every later guardrail validates
   against it.
2. **Decide the Q12/Q75 label** (LW only, per the question text).
3. **The named-but-not-encoded failure (Q55)** is untouched by context. Q55 is an approximate
   question; registering "isolated 1v1" with its proxy, like through ball, would apply it
   deterministically.
4. Re-run the same 20 (~$1.05) and compare to 7/13; only then the full 80 (~$4.50-5.50).

## 4j. Stage 2 cost - measured before spending

Every parsing run costs real money, so the cost was measured on a small calibration sample
before any large run (`scripts/parse_cost.py`). Output tokens cannot be derived from input:
adaptive thinking decides per call how much to reason, so an estimate made without running
the model is a guess.

Calibration: 3 questions spanning simple, negative and composite, 24 calls, **$0.23**.

| | Share of spend |
|---|---|
| Input, uncached (just the question, ~35 tokens/call) | ~0% |
| Cache writes + reads (per-gate prompt and schema) | small |
| **Output, including thinking** | **90%** |

- **Caching works on all 8 gates.** One correction: the `negative` gate's instructions alone
  are 440 tokens, under Opus 5's 512-token cache minimum, and at first that looked like it
  would never cache. It does cache, because the structured-output schema is inside the cached
  prefix and brings it to about 1,000 tokens. **Measure cache hits instead of predicting them
  from prompt length.**
- **Output is the bill.** Input is nearly free once cached, so trimming prompts saves almost
  nothing. Effort is the real lever, but lowering it before a quality baseline exists would
  mix up "is the parser good" with "is it good at low effort".
- **Projection for all 80 questions:** about **$5.50** (range roughly $3.90-$7.20), or $9.33 if
  caching never engaged. About 50 minutes run sequentially. `event_type` is the costliest gate
  at ~$0.015 per call, which fits: it decides which columns exist for every later gate.
- **Levers not yet pulled:** the Batch API (~50% off at the same quality; fits a one-off
  eval), and running gates in parallel (~50 min -> ~10 min at the same cost). Neither is worth
  doing until the quality baseline says the parser is worth running at scale.

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
7. ~~**Team shape** → Q17~~, ~~**Q59 foot race**~~, ~~**Q56 dragged out of
   position**~~ — **all done.** Tier 3 is complete; the eager base
   (`scripts/tracking_base.py`) holds per-player positional baselines, and nothing else was
   built eagerly because nothing else needed it.

**Ceiling reached.** The projection was 63 / 14 / 3 and that is the result. Nothing
further is buildable without new data: captain needs an external roster source
(`data/captains.json` is the hook), and offside calls need a referee feed that does not
exist.

### In parallel — the actual product gap
8. ~~**Stage 2, the query-parsing chain.**~~ **Built** (`scripts/query_parse.py`): gate
   ordering decided (shape -> subject -> conditions, see CLAUDE.md), per-gate "does this
   apply?" self-report, gates shown the query so far, `validate_filter` rejecting
   hallucinated or mis-targeted columns, `answerability.check()` refusing before any filter
   is emitted, and a zero-row check on the finished query. **Evaluated twice on a 20-question
   sample: 5/13, then 7/13 comparable questions return the right rows.** Next steps are in
   4l; the full 80 waits until they land.
8b. **An end-to-end query runner.** The parser's output is only executed by the grader,
   at event grain. Negation (anti-join), chain grain and the tracking predicates exist and
   are tested separately, but nothing routes a `ParsedQuery` through them.
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
- **It would be strictly worse for the 63 exact questions**, which already resolve
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
