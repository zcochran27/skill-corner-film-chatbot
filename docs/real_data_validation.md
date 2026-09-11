# Real-Data Validation of the Exploration Notebook's Queries

`skillcorner_exploration.ipynb` validated the 8-gate query approach against **synthetic**
data matching the SkillCorner schema exactly, since the chat sandbox that built it had no
network access. This doc records the priority next step called out in `CLAUDE.md`: re-running
the same 4 representative queries against a **real** match's `_dynamic_events.csv`.

Match used: `1874553` (Brisbane FC vs Adelaide United, A-League 2024/25) — fetched via
`scripts/fetch_match_data.sh 1874553`, validated with `scripts/validate_real_data.py`.

4,915 dynamic events total: 2,543 `passing_option`, 959 `player_possession`,
878 `on_ball_engagement`, 535 `off_ball_run`.

## Results

| # | Category | Question | Query fields used | Real hits |
|---|---|---|---|---|
| Q1 | Event-type | Shots taken from outside the box | `event_type`, `end_type`, `penalty_area_end` | 6 |
| Q2 | Spatial | Winger cut inside at the top of the box | `player_position`, `out_to_in`, `third_end`, `x_end` | 15 |
| Q3 | Sequence | Regain in midfield leading to a shot | `start_type`, `third_start`, `lead_to_shot` | 3 |
| Q4 | Negative/absence | Goal kick with no striker (CF) press | `game_interruption_before` anti-joined against `on_ball_engagement`/`CF` on `phase_index` | 18 of 31 goal-kick windows |

All four resolved as direct field filters (or, for Q4, a `left_only` anti-join) exactly as
designed — no query needed geometry derived from raw tracking data. Real column names and
value vocab matched the synthetic schema and `skillcorner_schema.md` almost exactly (a few
extra real-world values turned up, see below).

## Differences from the synthetic pass

- **Real value vocab is richer.** `end_type` has `indirect_regain` / `direct_regain` /
  `indirect_disruption` / `direct_disruption` / `foul_committed` in addition to the ones used
  in the synthetic generator (`pass`, `shot`, `clearance`, `foul_suffered`,
  `possession_loss`, `unknown`). `start_type` likewise has more `*_interception` /
  `*_reception` variants than the synthetic sample enumerated. Any production query builder
  needs to validate against the full real value set, not just the subset the synthetic
  generator happened to use.
- **Q3 was widened up front.** The synthetic Q3 only checked `start_type == "recovery"`.
  Inspecting the real `start_type` value set first (below) showed a regain can also resolve
  as `pass_interception`, so the real-data query checks
  `start_type.isin(["recovery", "pass_interception"])` rather than reusing the synthetic
  notebook's single value. Of the 3 real Q3 hits, only 1 is a `recovery` — the other 2 are
  `pass_interception` and would have been silently missed by the original query. This is
  exactly the kind of gap synthetic-only testing can hide, since a generator only ever
  emits the values it's told to.
- **Hit counts are plausible, not just non-zero.** 6 shots-outside-the-box and 3
  regain-to-shot sequences in a single 90-minute match line up with real match volumes; the
  goal-kick/no-press split (18/31 without a CF press) is a believable minority-press rate,
  not a degenerate all-or-nothing result.

## Not yet validated with real data (as of the 4-query pass above)

- Only one match was checked here. Hit-rate validation across more matches is superseded
  by the full test-set pass below.
- Comparative/relational questions (Category 6) were assumed to need raw tracking data —
  see the full pass below, which found this assumption wrong for most of the category.

---

# Full Test-Set Validation: All 80 Questions, 20 Matches

A second, much larger validation pass: every question in `coach_question_test_set.md`
(80 questions, 8 categories) translated into a pandas query and run against **every real
match currently available locally** — 20 matches, 94,517 dynamic events total (not 10, as
`CLAUDE.md`'s original description of the dataset assumed; the upstream repo has grown).
Fetched by copying `data/matches/*/{_dynamic_events.csv,_match.json}` for all 20 match ids
in `SkillCorner/opendata`'s `data/matches.json` (`_phases_of_play.csv` wasn't needed — phase
context is already inlined on every dynamic-events row). Reproduce with
`scripts/test_all_questions.py` after fetching more than one match
(`scripts/fetch_match_data.sh` as currently written only pulls one at a time).

Each question is tagged with a **fidelity** level:
- **exact** — the query directly answers what the question asks.
- **approximate** — a reasonable proxy, coarser than the real question; the specific gap is
  noted inline in `scripts/test_all_questions.py` next to each query.
- **unresolved** — nothing in the schema (events + match roster) can answer this as asked.

**47 exact, 22 approximate, 11 unresolved**, out of 80.

## Results

| # | Category | Fidelity | Hits (rows) | Matches with ≥1 hit |
|---|---|---|---|---|
| 1 | Player-specific | exact | 429 | 20/20 |
| 2 | Player-specific | exact | 67 | 15/20 |
| 3 | Player-specific | exact | 68 | 19/20 |
| 4 | Player-specific | approximate | 724 | 20/20 |
| 5 | Player-specific | **unresolved** | — | — |
| 6 | Player-specific | approximate | 0 | 0/20 |
| 7 | Player-specific | approximate | 47 | 18/20 |
| 8 | Player-specific | exact | 260 | 20/20 |
| 9 | Player-specific | exact | 7 | 3/20 |
| 10 | Player-specific | exact | 101 | 20/20 |
| 11 | Player-specific | exact | 12 | 6/20 |
| 12 | Spatial | exact | 92 | 19/20 |
| 13 | Spatial | exact | 4260 | 20/20 |
| 14 | Spatial | approximate | 5100 | 20/20 |
| 15 | Spatial | exact | 67 | 20/20 |
| 16 | Spatial | exact | 270 | 20/20 |
| 17 | Spatial | **unresolved** | — | — |
| 18 | Spatial | approximate | 347 | 20/20 |
| 19 | Spatial | exact | 3327 | 20/20 |
| 20 | Spatial | exact | 371 | 20/20 |
| 21 | Spatial | approximate | 346 | 20/20 |
| 22 | Spatial | exact | 1056 | 20/20 |
| 23 | Event-type | exact | 193 | 20/20 |
| 24 | Event-type | exact | 103 | 20/20 |
| 25 | Event-type | approximate | 28 | 15/20 |
| 26 | Event-type | exact | 742 | 20/20 |
| 27 | Event-type | **unresolved** | — | — |
| 28 | Event-type | exact | 133 | 20/20 |
| 29 | Event-type | approximate | 72 | 16/20 |
| 30 | Event-type | exact | 1052 | 20/20 |
| 31 | Event-type | exact | 700 | 20/20 |
| 32 | Event-type | approximate | 35 | 14/20 |
| 33 | Event-type | exact | 1052 | 20/20 |
| 34 | Sequence | exact | 62 | 19/20 |
| 35 | Sequence | approximate | 80 | 18/20 |
| 36 | Sequence | approximate | 0 | 0/20 |
| 37 | Sequence | exact | 2845 | 20/20 |
| 38 | Sequence | exact | 115 | 20/20 |
| 39 | Sequence | exact | 47 | 19/20 |
| 40 | Sequence | exact | 115 | 20/20 |
| 41 | Sequence | exact | 89 | 18/20 |
| 42 | Sequence | exact | 1390 | 20/20 |
| 43 | Sequence | approximate | 112 | 20/20 |
| 44 | Sequence | approximate | 460 | 20/20 |
| 45 | Game-state | exact | 6474 | 19/20 |
| 46 | Game-state | exact | 353 | 12/20 |
| 47 | Game-state | exact | 927 | 19/20 |
| 48 | Game-state | approximate | 255 | 20/20 |
| 49 | Game-state | **unresolved** | — | — |
| 50 | Game-state | exact | 195 | 18/20 |
| 51 | Game-state | **unresolved** | — | — |
| 52 | Game-state | exact | 37 | 16/20 |
| 53 | Game-state | exact | 180 | 20/20 |
| 54 | Game-state | exact (aggregate, not a filter) | — | — |
| 55 | Comparative | approximate | 84 | 19/20 |
| 56 | Comparative | **unresolved** | — | — |
| 57 | Comparative | approximate | 19 | 7/20 |
| 58 | Comparative | exact | 327 | 20/20 |
| 59 | Comparative | **unresolved** | — | — |
| 60 | Comparative | exact | 888 | 20/20 |
| 61 | Comparative | exact | 37 | 10/20 |
| 62 | Comparative | approximate | 52 | 15/20 |
| 63 | Comparative | exact | 3126 | 20/20 |
| 64 | Comparative | exact | 2528 | 20/20 |
| 65 | Negative/absence | exact | 226 | 20/20 |
| 66 | Negative/absence | **unresolved** | — | — |
| 67 | Negative/absence | approximate | 13 | 9/20 |
| 68 | Negative/absence | approximate | 89 | 20/20 |
| 69 | Negative/absence | **unresolved** | — | — |
| 70 | Negative/absence | approximate | 42 | 15/20 |
| 71 | Negative/absence | exact | 2005 | 20/20 |
| 72 | Negative/absence | exact | 1379 | 20/20 |
| 73 | Negative/absence | exact | 496 | 20/20 |
| 74 | Negative/absence | approximate | 381 | 20/20 |
| 75 | Composite | exact | 5 | 5/20 |
| 76 | Composite | **unresolved** | — | — |
| 77 | Composite | exact | 67 | 17/20 |
| 78 | Composite | **unresolved** | — | — |
| 79 | Composite | approximate | 15 | 9/20 |
| 80 | Composite | exact | 1 | 1/20 |

Q54 ("passing tempo when leading by 2+") is an aggregate stat, not a hit-count filter: share
of long passes was 4.4% normally vs 4.1% leading by 2+ — a real but very small effect over
this data, i.e. no strong evidence teams in this dataset go noticeably more direct when
comfortably ahead.

## Bugs this pass caught (and fixed) in the query layer itself

Building 80 queries against the real schema surfaced three real gotchas that a smaller
sample wouldn't have forced out — all three are about **which event_type a field is actually
populated on**, not about the concept being unsupported:

1. **`give_and_go` / `initiate_give_and_go` only live on `off_ball_run` rows.** First draft
   filtered `player_possession` rows for these fields and got 0 hits across all 20 matches
   for 4 different questions (31, 38, 40, 77) before catching it — `player_possession` rows
   have these columns but they're always null there.
2. **`penalty_area_start`/`penalty_area_end` only ever fire for the *attacking* box.**
   Coordinates are mirrored so the team on the ball always attacks left-to-right, and these
   flags apparently only track the box that team is attacking toward (x > 0) — never the
   defensive box behind them. A "clearances in their own box" query (29) and a "headers won
   in their own box" query (32) both silently returned 0 until this was caught; fixed by
   approximating the defensive box with real-world geometry (`x < -36`, `|y| < 20.16`)
   instead of the flag.
3. **`interplayer_distance`, `current_team_out_of_possession_previous_phase_type`, and
   `player_in_possession_position` are each populated on only some event types**
   (`player_possession`/`passing_option` for the first; `player_possession` only for the
   second; `off_ball_run`/`on_ball_engagement`/`passing_option` — not `player_possession` —
   for the third, since on a possession row `player_position` already identifies the
   possessor). Three more approximate queries (44, 55, 57) returned 0 until re-pointed at
   the event type that actually carries the field.

After fixing all three, every previously-zero "approximate" query returns a plausible
non-zero count. Two genuine zeros remain (below) — checked individually and are real
findings, not bugs.

## Genuine zero-hit findings (not bugs)

- **Q6** (goalkeeper passing into midfield under pressure): 0/20 matches. Checked the
  broader base rate (GK passes into the middle third at all, no pressure filter): 25 across
  all matches, and every one of them is tagged `no_pressure` or `low_pressure`. In this
  dataset, goalkeepers are apparently never meaningfully pressured when playing out short —
  a real (if unglamorous) finding about this league's press intensity on keepers, not a
  query bug.
- **Q36** (counter-attack starting from a defensive header clearance): 0/20. 60 header
  clearances exist across the dataset, by the expected mostly-CB/FB positions, but
  `lead_to_shot` is `False` on literally all of them. This makes sense given how
  `lead_to_shot` is defined (shot within 10s after the event's own end) — a clearance is a
  defensive event that, by design, is supposed to end the danger, so a shot 10s later is
  rare regardless of team. The real gap is architectural, not a data bug: detecting "this
  clearance kicked off a counter-attack that ended in a shot" needs to check for a shot by
  the *clearing team* after they regain and progress, which isn't what `lead_to_shot` on the
  clearance event itself measures. Worth a note for the sequence-stitching design.

## Category-level takeaways

- **Comparative/relational (Category 6) turned out to be far more event-level-resolvable
  than the original exploration notebook assumed.** Its takeaways section flagged this
  category as needing raw per-frame tracking data. In practice, `separation_start/end`,
  `interplayer_distance`, `n_teammates_ahead_start/end`, and `n_opponents_overtaken` are all
  already columns on `player_possession`/`passing_option`/`off_ball_run` rows — 6 of 10
  questions in this category resolved at exact or approximate fidelity without touching
  tracking data at all. Only "dragged out of position" (56, needs a before/after positional
  baseline) and "foot race" (59, needs synchronized relative movement between two named
  players) actually require raw frames.
- **The roster join (`{id}_match.json`'s `players` list) unlocked jersey-number and
  substitute-status questions** (2, 80) that the events table alone can't answer — but it
  doesn't have a captain flag, which is why Q5 and the Q5-dependent Q78 are unresolved.
- **11 unresolved questions cluster into 4 real, distinct gaps**, not one: (a) no captain
  flag anywhere (5, 78); (b) no goal-timestamp field to anchor "N minutes after
  scoring/conceding" windows — `game_state` exists but not *when* it changed (49, 51, 76);
  (c) team-shape/multi-player-geometry questions need raw tracking frames, not single-event
  rows (17, 56, 59); (d) some concepts genuinely aren't tagged in the schema at all — no
  offside flag, no "tracked back" / recovery-run-failure signal, no defender-rotation signal
  (27, 66, 69).
- Every category has at least one "exact" hit with reasonable hit counts and broad
  match coverage (mostly 18-20/20 matches), reinforcing the core architecture bet: this is a
  structured-filtering problem, not one where absence of a v1 embeddings fallback is
  blocking basic coverage.

## Still open

- The 11 unresolved and several `approximate` questions above are exactly the backlog for
  future enrichment work (goal-timestamp derivation, a roster-level captain/role join if
  available elsewhere, tracking-data-based team-shape metrics).
- `_tracking_extrapolated.jsonl` is still Git-LFS-only and wasn't pulled — only needed for
  the narrower tracking-dependent subset identified above, not the majority of the test set.
- This pass counts raw event/row hits, not deduplicated "clips" — the future ranking/output
  stage still needs to decide how multiple matching rows within one possession collapse into
  a single clip.

## Takeaway

The core architectural bet — deterministic structured filtering over the Dynamic Events
schema, no embeddings for v1 — holds up well: 47/80 questions (59%) resolve exactly, and
another 22/80 (27%) resolve with a documented, reasonable approximation, across 20 real
matches. The 11 unresolved questions are concentrated in a few well-defined schema gaps
(captain identity, goal timestamps, multi-player geometry, a handful of untagged concepts)
rather than being spread evenly across the test set — which means they're a tractable,
scoped backlog rather than evidence against the architecture.
