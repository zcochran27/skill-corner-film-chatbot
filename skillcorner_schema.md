---
name: skillcorner-schema
description: Reference schema for SkillCorner Open Data (tracking + dynamic events + phases of play) — used for the coach/scout film-clip retrieval app project
---

Reference documentation pulled from the SkillCorner Open Data repo (github.com/SkillCorner/opendata)
README and the official Dynamic Events CSV Specification PDF (2025/02/16), for the
coach/scout question -> film-clip retrieval app (uses tracking + dynamic events, no video —
clips are animated from tracking data).

## Repo structure
- `data/matches.json` — basic match info; use to pick a match `id`.
- `data/matches/{id}/` folder per match, 4 files each:
  - `{id}_match.json` — lineups, time played, referee, pitch size.
  - `{id}_tracking_extrapolated.jsonl` — tracking data (players + ball), 10 fps.
  - `{id}_dynamic_events.csv` — Game Intelligence dynamic events (see below).
  - `{id}_phases_of_play.csv` — phases of play framework (see below).
- `data/aggregates/` — season-level CSVs (Physical, Off-Ball Runs, Passing), AUS A-League 2024/25.
- Coverage: 10 matches, 2024/25 Australian A-League.

## Tracking data (`_tracking_extrapolated.jsonl`)
List of per-frame dicts (10 fps), each with:
- `frame`, `timestamp` (1/10s precision), `period` (1 or 2)
- `ball_data`: ball tracking for the frame
- `possession`: `{player_id, group}` (group = home/away)
- `image_corners_projection`
- `player_data`: list of `{x, y, player_id, is_detected}` (is_detected=False means extrapolated)

Coordinates in metres, pitch center = (0,0), x = long axis, y = short axis (e.g. 105x68m pitch).
~97% player identity accuracy; some speed/acceleration needs smoothing.

## Dynamic Events (`_dynamic_events.csv`)
One CSV per match; each row = one event, one of 4 types (`event_type`):
`player_possession` (PP), `passing_option` (PO), `off_ball_run` (OBR),
`on_ball_engagement`/`defensive_engagement` (OBE).
- `event_id` unique per match only. `index` = chronological ordering.
- x/y not pre-scaled to standard pitch size — needs adjustment.
- Direction of play normalized: coordinates mirrored so team in possession always
  attacks left-to-right (except `attacking_side`/`attacking_side_id` fields).
- Produced by combining SkillCorner Tracking v3 + Wyscout event data; only delivered
  for matches meeting quality thresholds.

### Event relationships (key for sequence-building)
- Every PO and OBR belongs to exactly one PP (`associated_player_possession_event_id`).
- Every OBR is associated with exactly one PO; runner must have been a passing option
  at some point during the run.
- Every OBE belongs to one PP, and can be linked to one line-breaking PO.
- PP references the PO it targeted (`targeted_passing_option_event_id`) if it ended in a pass.
- Consecutive events (same player, <0.5s gap, same PP) are merged.

### Key attribute groups (selected, most relevant to retrieval)
**Basics:** `event_id, index, match_id, frame_start, frame_end, time_start, time_end,
minute_start, duration, period, event_type, event_subtype, player_id, player_name,
player_position, player_in_possession_id/name/position, team_id, team_shortname,
x_start/y_start, channel_start (wide_left/half_space_left/center/half_space_right/wide_right),
third_start (defensive/middle/attacking_third), penalty_area_start, x_end/y_end,
channel_end, third_end, penalty_area_end.`

**Off-ball run subtypes:** behind, coming_short, cross_receiver, dropping_off, overlap,
pulling_half_space, pulling_wide, run_ahead_of_the_ball, support, underlap.
**On-ball engagement subtypes:** pressing, pressure, counter_press, recovery_press, other
(pressing = part of a collective chain; pressure = solo action; see Pressing Chain rules).

**Associated events:** `associated_player_possession_event_id/frame_start/frame_end/end_type,
associated_off_ball_run_event_id/subtype` — for stitching sequences.

**Game context:** `game_state (Winning/Losing/Drawing), team_score, opponent_team_score,
phase_index, team_in_possession_phase_type, team_out_of_possession_phase_type,
current_team_in/out_of_possession_next/previous_phase_type, n_player_possessions_in_phase,
first/last_player_possession_in_team_possession, lead_to_different_phase,
team_possession_loss_in_phase.`

**Event start/end:** `game_interruption_before/after` (corner/free_kick/goal/goal_kick/
penalty/throw_in, for/against), `start_type` (pass_reception, pass_interception,
keep_possession, recovery, set-piece receptions/interceptions, unknown), `end_type`
(pass, shot, clearance, foul_suffered, possession_loss, unknown, regain/disruption
variants for OBE), pass angle/direction/distance/range fields for the reception that
started the possession.

**Outcome (very useful for retrieval — largely pre-computed):** `lead_to_shot,
lead_to_goal` (shot/goal within 10s after event end) — present on ALL 4 event types.
`targeted, received, received_in_space, possession_danger, beaten_by_possession,
beaten_by_movement, stop_possession_danger, reduce_possession_danger, force_backward`
(EPV/xThreat-based defender evaluation flags).

**Physical:** `distance_covered, trajectory_angle, trajectory_direction,
in_to_out, out_to_in` (== lateral movement toward/away from center — directly answers
"cut inside" style questions), `speed_avg, speed_avg_band` (jogging/running/hsr/sprinting).

**Player in possession details:** location/distance to player in possession
(behind/same_line/ahead), player-in-possession x/y/channel/third at start & end,
`xloss_player_possession_*`, `xshot_player_possession_*` (start/end/max probabilities).

**Player targeted:** full location/channel/third at pass & reception, distance/angle to
goal, `player_targeted_xthreat, xpass_completion, dangerous, difficult_pass_target,
speed_difference`.

**Teammates & opponent context:** `last_defensive_line_x/height_(start/end/gain),
delta_to_last_defensive_line_(start/end/gain)` (negative = player passed the line),
`inside_defensive_shape_(start/end)` (inside opponent's convex-hull shape),
`n_teammates_ahead, n_player_targeted_(opponents/teammates)_ahead/within_5m,
separation_(start/end/gain)` (distance from nearest opponent — useful for 1v1/isolation
questions).

**Pass/passing option details:** `pass_distance/range/angle/direction, pass_ahead,
dangerous, difficult_pass_target, xthreat, xpass_completion, passing_option_score,
predicted_passing_option, peak_passing_option_frame, n_simultaneous_passing_options.`

**Line breaks:** `organised_defense, defensive_structure (e.g. "442"), n_defensive_lines,
first_line_break, second_last_line_break, last_line_break` (each with `_type`:
through/around), `furthest_line_break(_type)` — deliberately answers "line-breaking pass"
questions natively.

**Player possession specifics:** `one_touch, quick_pass, carry` (>=2m covered),
`forward_momentum, is_header, hand_pass, initiate_give_and_go.`

**Passing option availability (on the PP row):** `n_passing_options, n_off_ball_runs,
n_passing_options_line_break/first_line_break/second_last_line_break/last_line_break,
n_passing_options_ahead, n_passing_options_dangerous_difficult (and 3 other
dangerous x difficult combos), n_passing_options_at_start/end(_ahead).`

**Off-ball run specifics:** `n_simultaneous_runs, give_and_go, intended_run_behind,
push_defensive_line, break_defensive_line` (>=1m behind last defender within ±1s of
run end), `passing_option_at_start.`

**Player overtaken:** `n_opponents_ahead_(start/end), n_opponents_overtaken.`

**On-ball engagement specifics:** `affected_line_breaking_passing_option_*` fields,
`pressing_chain, pressing_chain_length/end_type/index, index_in_pressing_chain,
simultaneous_defensive_engagement_same_target(_rank), consecutive_on_ball_engagements.`
Pressing chain = 2+ possessions pressed/counter-pressed/recovery-pressed within 4s of
each other, only during build_up/direct/create phases; broken by a >=15m fast-break carry
with <8 defenders ahead.

**Data matching / confidence:** `is_player_possession_start/end_matched,
is_previous_pass_matched, is_pass_reception_matched, fully_extrapolated` — flag rows
where confidence is lower.

### Derived-concept cheat sheet (from spec, useful shortcuts)
- Speed bands: jogging <15km/h, running 15-20, hsr 20-25, sprinting >25 km/h.
- Pass range: short <15m, medium 15-30m, long >30m.
- Direction (relative to attack, -180..180°): forward -45..45°, backward >135° or
  <-135°, sideway left 45..135°, sideway right -135..-45°.
- Ahead/same_line/behind (on x-axis): >=3m ahead / within ±3m / >=3m behind.
- `high_pass` = ball crossed 1.8m height during the pass.

## Phases of Play (`_phases_of_play.csv`)
Each row = start/end frames of one phase; only defined while ball is in play; every
in-possession phase has a corresponding out-of-possession phase for the other team.

| In-possession | Out-of-possession | Description |
|---|---|---|
| build_up | high_block | Ball in own third, carrier under pressure or opponents very high |
| create | medium_block | Default phase, roughly middle third |
| finish | low_block | Final/middle third, defensive line near own box, possession established >=1s |
| direct | defending_direct | Starts with a 32m+ long ball (not a switch) targeting a player |
| quick_break | defending_quick_break | Regain in opponent's half + rapid progression |
| transition | defending_transition | Regain in own half + rapid progression |
| set_play | defending_set_play | Corners/free-kicks/long throw-ins into the box; ends on clear possession, clearance to own half, ball out, or 20s elapsed |
| chaotic | chaotic | Short contested possession (fails the "3 passes / 5s possession" test); clearances under pressure always chaotic |

Special case: brief interruptions (e.g. blocked shot, ball bounces back to attacker)
are labelled `Disruption` at the possession level but don't cut the overall phase index.

## Notes for this project
- Full attribute list (~200 columns across 8 themed groups) is in the official PDF:
  "Dynamic Events CSV Specifications" (2025/02/16), fetched from SkillCorner's HubSpot
  docs. Re-search ("SkillCorner Dynamic Events CSV Specifications") if the link needs
  refreshing — it's hosted at a HubSpot content URL that may rotate.
- The exploration notebook (`skillcorner_exploration.ipynb`) used synthetic data
  matching this exact schema, since that chat's sandbox had no live network access.
  Swap in real `_dynamic_events.csv` / `_tracking_extrapolated.jsonl` (clone
  github.com/SkillCorner/opendata) to re-run with real data — same column names, so
  the filters should work unchanged.
- Validated in that notebook: shot/spatial(`out_to_in`)/sequence(`lead_to_shot`) style
  questions resolve via direct field filters; negative/absence questions (e.g. "did NOT
  press") need an anti-join over candidate windows instead.
