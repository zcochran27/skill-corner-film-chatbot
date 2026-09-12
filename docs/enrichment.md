# Tier 1 + Tier 2 Enrichment

Implements the enrichment backlog identified from the 80-question validation pass. Code
lives in `scripts/enrich.py` (derived columns) and `scripts/field_catalog.py` (the
event_type × column availability guardrail).

## Result

Re-running all 80 test-set questions through `scripts/test_all_questions.py`, which now
loads via `enrich.load_enriched()`:

| | exact | approximate | unresolved |
|---|---|---|---|
| Before (event schema only) | 47 | 22 | 11 |
| After Tier 1 + Tier 2 | **57** | **16** | **7** |

The 7 that remain: captain identity (5, 78), multi-player tracking geometry (17, 56, 59),
and two genuinely untagged concepts (27 offside, 66 "tracked back"). All are Tier 3 or
external-data problems — see `docs/tier3_tracking_plan.md`.

## What was added

### Tier 1.1 — Goal timeline
`seconds_since_goal_for/against`, `seconds_until_goal_for/against`.

Goal timestamps *are* derivable, contrary to the previous doc's "no goal-timestamp field"
conclusion. But neither available signal is complete alone, and the enrichment only became
correct once both were unioned:

- `game_interruption_after == 'goal_for'` marks the possession that ended in the goal, so
  its `frame_end` is the exact goal moment — but the marker is **missing** for some goals
  (match 2006229 has two goals by one scorer and one marker).
- The running `team_score` is complete mid-match but updates only on the first event after
  the restart (~55–100s late), and **never registers a goal scored so late no further event
  follows** — matches 1927964 and 2015213 each finish 4–x with only three score bumps.
- `goal_for` on an `on_ball_engagement` row is stamped from the *possessing* team's
  perspective, not the defending row player's, so it credits the wrong team (match 2016236,
  frame 13074). Only `player_possession` rows are authoritative.

Validated: **66 goals derived, 66 official, all 20 matches match their `_match.json` score.**
64 timestamps come from the exact marker, 2 from the last-shot fallback.

Unblocks Q49, Q51, Q76.

### Tier 1.2 — Parameterised outcome windows
`seconds_to_next_shot_same_team`, `seconds_to_next_goal_same_team`, and the `_opponent`
counterparts.

`lead_to_shot` is a fixed 10s boolean, so questions asking for 6s or 8s could only
approximate it. These make the window a query parameter.

Use the `_opponent` columns for `on_ball_engagement` rows — the row player there is
*defending*, so native `lead_to_shot` on an OBE row refers to a shot by the team in
possession. Checking OBE rows against `_same_team` disagrees with `lead_to_shot` on 1,716
rows; against the correct side, agreement is **99.8%** (0 false positives, 154 rows where
`lead_to_shot` is True but no possession-ending shot is found within 10s).

Makes Q39 (6s), Q41 (8s), Q77 (6s) genuinely exact rather than exact-with-a-caveat, and
gives Q36 a query that matches what the question means.

### Tier 1.3 — True possession chains
`team_possession_id`, `chain_n_possessions`, `chain_reached_final_third`,
`chain_reached_box`, `chain_ended_in_shot`, `chain_duration_s`.

`phase_index` is **not** the same grouping as an unbroken team possession. Across the
dataset: 94,517 events span 7,523 phases but only 4,071 team possessions. A single
possession can cross two phases (match 1874553, frames 29→162 spans phase 0→1) and a single
phase can contain possessions by *both* teams. So the old `phase_reaches()` helper both
over- and under-matched — this was a correctness issue, not just coarseness.

SkillCorner already delimits possessions with
`first/last_player_possession_in_team_possession`; chains are a cumulative sum of those,
propagated to PO/OBR/OBE rows through `associated_player_possession_event_id`. 100% of rows
get a chain id.

Fixes Q35, Q43.

### Tier 1.4 — Mirror-safe, per-match-scaled zone flags
`own_/opp_penalty_area_{start,end,reception}`, `own_/opp_six_yard_box_{start,end,reception}`.

**Correction to `docs/real_data_validation.md`.** That doc recorded that
`penalty_area_start/end` "only ever fires for the *attacking* box". That is wrong. Of its
3,863 True rows, 2,764 are in the opponent box and **1,062 are in the row team's own box**.
The native flag means "in a penalty area" and conflates the two — which is why it cannot
answer Q29/Q32's "in their own box", the real reason those queries needed help.

The coordinate frame was verified empirically rather than assumed: joining defensive
engagements to the possession they engage gives median |x_obe + x_pp| = 4.75m vs
|x_obe − x_pp| = 36.6m. Every row's x/y is mirrored into **that row's own team's attacking
frame**, so `x < 0` is the row player's own half for every event type, OBE included.

Pitch length varies across the 20 matches (104m ×4, 105m ×10, 106m ×6) and coordinates are
real metres, so the box edge is scaled per match instead of the previous hardcoded `x < -36`.

The `_reception` variants use `player_targeted_x/y_reception` — where a pass was actually
received. "Crosses delivered into the six-yard box" (Q21) is about the destination: `x_end`
on a possession row is where the *passer stood*, which finds 4 rows across 20 matches
versus 288 by reception point.

Fixes Q21, Q29, Q32.

### Tier 1.5 — Field availability catalog
`scripts/field_catalog.py` → `docs/field_catalog.md`.

Of 350 columns, **168 are populated on exactly one event_type** and **28 are missing
entirely from at least one match**. Filtering a column on an event_type that never
populates it returns zero rows silently — which is how three queries in the first
validation pass produced empty results that looked like findings. The query-parsing stage
should consult this catalog before emitting a filter rather than inferring availability.

This immediately caught a live instance: the roster's `playing_time.sequences` exists in
only **11 of 20** matches, while `playing_time.total.start_frame/end_frame` exists for every
player in all 20. The first version of the unit-flag code used `sequences` and silently
produced null unit sizes for half the dataset.

### Tier 2.5 — Squad units
`is_starter`, `team_back_line_size`, `team_front_line_size`, `team_pivot_size`,
`is_starting_cb_pair`.

"Back four", "front two" and "the centre back pairing" are group concepts that a bare
position filter cannot express. On-pitch membership changes only at substitutions, so unit
sizes are computed once per (match, team) segment boundary and mapped onto events with
`searchsorted`.

Sanity check: back line is 4 on 82% of rows, 5 on 14%, 3 on 2% — i.e. mostly back fours with
real back-five and back-three spells, not a degenerate constant. `is_starter` covers 87.9%
of event rows.

Fixes Q4, Q14; sharpens Q79.

### Tier 2.6 — Captain
`is_captain`, read from an optional hand-maintained `data/captains.json`
(`{"<match_id>": {"<team_id>": <player_id>}}`).

Captain identity is genuinely **not** in SkillCorner open data — `_match.json`'s
`player_role` holds a position (`RW`, `GK`, `SUB`), never an armband flag, and no
dynamic-events column encodes it. This is a data-acquisition problem, not a derivation one.
Without the file, `is_captain` is all-False and Q5/Q78 stay unresolved rather than silently
guessing.

## Other corrections this pass produced

- **Q36's premise.** `lead_to_shot` is False on all 60 header clearances in the dataset, and
  the previous doc read that as a finding about clearances ending danger. It is really a
  definitional mismatch: `lead_to_shot` asks "a shot within 10s of this event", while the
  question asks whether the *clearing team* later shot. `seconds_to_next_shot_same_team`
  measures the latter.
- **Q69 was over-classified as unresolved.** `beaten_by_possession` and
  `beaten_by_movement` are native defender-evaluation flags on every `on_ball_engagement`
  row, so "the fullback was beaten" is a direct filter. Only the cover-rotation half needs
  tracking. It is now approximate (47 hits, 18/20 matches).
- **Anti-joins must exclude the subject.** The first Q69 implementation counted the beaten
  fullback's own row as a covering defender and returned 0 across all 20 matches — the same
  silent-zero failure mode as the event_type bugs, in a different guise.

## Query timing

73 timed questions (7 unresolved have none): total 1.35s, mean 18ms, median 11ms, max 81ms
(Q65). Still no latency concern, and the enrichment moved several of the slowest questions
off the phase-join path onto plain boolean filters — Q43 went from 289ms (the previous
slowest) to 7ms by replacing a phase merge with a `chain_reached_box` lookup.

Enrichment itself is a one-time preprocessing cost of roughly 20s over all 20 matches.

## Reproducing

```bash
python scripts/enrich.py            # builds derived columns, prints goal-timeline validation
python scripts/field_catalog.py     # regenerates docs/field_catalog.md
python scripts/test_all_questions.py
```
