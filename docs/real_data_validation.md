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

## Not yet validated with real data

- **Comparative/relational** questions (Category 6, e.g. fullback isolated 1v1) — per the
  exploration notebook's takeaways, these need raw tracking data joined in per-frame
  (`separation_start/end` exist on the event rows already, but confirming an isolated 1v1
  moment needs the actual player positions). `_tracking_extrapolated.jsonl` is Git-LFS-only
  in the upstream repo and wasn't pulled for this pass — needs `git lfs pull` with
  SkillCorner/opendata LFS access. Not required for the 3 other event-level gate categories.
- Only one match was checked. Hit-rate/false-positive-rate validation across the other 9
  matches in the dataset (per `CLAUDE.md`'s stated next step) is still open.

## Takeaway

The core architectural bet — deterministic structured filtering over the Dynamic Events
schema, no embeddings for v1 — holds up against real data for the event-type, spatial, and
sequence/negative-absence gates tested here. The one adjustment needed (Q3's `start_type`
set) came from real-world value vocab the synthetic generator didn't produce, not from a
flaw in the filtering approach itself.
