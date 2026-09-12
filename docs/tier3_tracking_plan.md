# Tier 3 Plan — Tracking-Derived Enrichment

> **Status: complete.** Written when 7 questions were unresolved; the test set now stands at
> 63 exact / 14 approximate / 3 unresolved. The adopted design is lazy per-event
> confirmation (`docs/tier3_lazy_retrieval_plan.md`), which supersedes §2 below. §3's metric
> definitions are what got built, with two corrections recorded in `docs/master_plan.md`:
> Q17 needed no tracking (§4h), and Q56 had to be measured relative to the back line (§4i).

Tier 1 and Tier 2 (`docs/enrichment.md`) took the test set from 47/22/11 to 57/16/7. The
remaining 7 unresolved questions split into three groups, only one of which Tier 3 can fix:

| Group | Questions | Tier 3? |
|---|---|---|
| Multi-player geometry | ~~17~~, 56, 59 | Partly - **Q17 turned out to need no tracking at all**; `_phases_of_play.csv` already carries per-phase team width/length. See `docs/master_plan.md` 4h. |
| Untagged concepts | 27 (offside), 66 ("tracked back") | Partly — 66 yes, 27 only as a proxy |
| External data | 5, 78 (captain) | No — needs a roster source SkillCorner doesn't ship |

Tier 3 is also the larger opportunity: beyond unblocking 4 questions, it adds a new
*class* of filterable attribute (shape, space, pressure, dyadic relations) that the event
schema has no vocabulary for at all. Section 4 works through those.

---

## 1. Prerequisites — settled, not assumptions

All verified against the real data before writing this plan.

**The tracking data is available.** `_tracking_extrapolated.jsonl` is Git-LFS-only upstream,
but `git lfs pull` works anonymously. All 20 matches pulled: **1.82 GB, no stubs remaining**.
The earlier "not yet pulled" blocker is gone.

**Frame structure.** 60,301 frames per match at 10fps. Of those, **44,244 (73%) carry full
22-player + ball data**; the remaining 16,057 carry an empty `player_data` list (ball out of
play or untracked). Coverage is all-or-nothing per frame — when players are present there
are always exactly 22, never a partial set. So no interpolation over missing players is
needed, only awareness that ~27% of frames are unusable.

**`is_detected: False` means extrapolated**, not absent. The README quotes ~97% player
identity accuracy and notes speed/acceleration needs smoothing. Any velocity-derived metric
(sprints, foot races, accelerations) must smooth before differentiating — a Savitzky-Golay
or centred rolling mean over ~0.5s (5 frames) is the usual choice.

**The coordinate-frame join is the main hazard, and the rule is exact.** Tracking is in
**absolute** pitch coordinates. Events are **mirrored per row** so the row team always
attacks +x. Verified by matching event x/y against tracking x/y for the same player at the
same frame:

```
period 1   Brisbane FC       ev_x == tr_x       (sign +1)
period 1   Adelaide United   ev_x == -tr_x      (sign -1)
period 2   Brisbane FC       ev_x == -tr_x      (sign -1)   <- flips at halftime
period 2   Adelaide United   ev_x == tr_x       (sign +1)
```

So `event_xy = sign(team, period) * tracking_xy`, with the sign flipping between periods.
Derive the sign per (match, team, period) once by regressing a sample of event rows against
tracking, rather than hardcoding it — home/away attacking direction is not stated in
`_match.json` in a form worth trusting over direct measurement. **Every Tier 3 metric must
declare which frame it is in.** Team-shape metrics are most naturally computed in absolute
coordinates and then signed into the event frame at join time.

**Join key.** `events.frame_start` → `tracking.frame` is a direct integer match; both use
the same match-global frame counter (period 1: 10–28990, period 2: 29000–60300, continuous).

---

## 2. Architecture

> **Superseded by `docs/tier3_lazy_retrieval_plan.md`.** This section proposed featurising
> all 1.2M frames offline. The adopted design instead uses the Tier 1/2 filters to produce a
> candidate set (median 156 events) and fetches only those events' frames — 0.6% of the
> corpus — confirming each candidate geometrically at query time. That is also what
> `CLAUDE.md`'s Retrieval stage specified. A thin eager base survives from this section: the
> cheap per-frame scalars (22 us/frame, 39s for the corpus) and the team baselines computed
> from them. The expensive parts below (convex hull at 625 us/frame, all-pairs dyads) are
> what the lazy design avoids paying up front.
>
> Sections 1, 3 and 4 are unaffected — the prerequisites and metric definitions hold
> regardless of when they run.

The original precompute sketch, kept for the sizing analysis:

```
_tracking_extrapolated.jsonl  (1.8 GB JSONL, 20 matches)
        |  scripts/tracking_to_parquet.py      [one-time, offline]
        v
player_frames.parquet         (match_id, frame, period, player_id, team_id, x, y, is_detected)
ball_frames.parquet           (match_id, frame, x, y, z, possession_player_id, possession_group)
        |  scripts/tracking_features.py        [one-time, offline]
        v
frame_features.parquet        one row per (match_id, frame, team_id) - shape/space metrics
dyad_features.parquet         one row per (match_id, frame, player_a, player_b) - PAIRS ONLY
        |  enrich.add_tracking_features()      [per session, cheap]
        v
events + tracking-derived columns, joined on (match_id, frame_start)
```

Sizing: ~44k usable frames × 22 players × 20 matches ≈ **19.4M player-frame rows**. Trivial
in Parquet (low hundreds of MB, columnar, predicate-pushdown on match_id/frame). The frame
feature table is ~44k × 2 teams × 20 ≈ 1.8M rows — small enough to hold in memory.

The dyad table is the one that can explode: all pairs is 22×21/2 = 231 per frame ≈ 200M
rows. **Do not materialise all pairs.** Restrict to (a) opponent pairs only (11×11 = 121),
and (b) only frames belonging to a possession where at least one of the pair is involved.
Better still, compute dyadic features lazily for the specific player pair a question names —
Q56 and Q59 are both "this defender vs that attacker" questions, so the pair is known at
query time. Keep the precomputed dyad table to nearest-opponent-per-player (22 rows/frame),
which is what most questions actually need.

Follow the same discipline as Tier 1: every derived column goes in `DERIVED_COLUMNS` and is
picked up automatically by `scripts/field_catalog.py`, so a tracking column that is only
populated on some frames or some matches is visible rather than a silent zero.

---

## 3. The four blocked questions

### Q17 — "midfield block compressed into a narrow shape"
Per (frame, team), over the 10 outfield players (exclude the GK, who distorts every spread
metric):

- `block_width` — y-range, or better the 10th–90th percentile y-spread to resist one
  fullback pushing on
- `block_height` — x-range on the same basis
- `block_area` — convex hull area of the 10 outfield players
- `compactness` — mean pairwise distance, or `block_area / 10`
- `line_gaps` — cluster x-positions into defensive/midfield/attacking lines (1-D k-means,
  k from `n_defensive_lines` where present) and record DEF→MID and MID→ATT distances
- `block_centroid_x/y`

"Compressed into a narrow shape" is then `block_width` below that team's own rolling
baseline — not an absolute metre threshold, since shape norms differ by team and phase.
Compute the baseline per (team, `team_out_of_possession_phase_type`) so a low block is
compared against low blocks. Join to events on frame and expose as `block_width`,
`block_width_pct_of_team_baseline`.

Note `defensive_structure` (values like 442, 424, 4231) and `n_defensive_lines` already
exist natively on PP/PO rows — useful as a cross-check and a cheap formation filter, but
they give a formation label, not width or compactness, so they cannot answer Q17 alone.

### Q56 — "centre back dragged out of position by a striker's movement"
Needs a positional baseline, which is exactly what a single event row cannot provide:

1. Establish each player's positional baseline: rolling median (x, y) over their own frames
   within the same phase type, over a window long enough to be stable (~2 min) — median, not
   mean, so one excursion doesn't move the baseline it's measured against.
2. `displacement_from_baseline` per frame.
3. Flag frames where a CB's displacement exceeds a threshold (2–3 baseline MADs) **and** the
   nearest opposing forward moved first — cross-correlate the two displacement series over a
   ±2s lag and require the forward to lead.

The "who moved first" step is what separates *dragged* from *stepped up to press*. Without
it this is just "CB out of position", a weaker question. This is the most algorithmically
involved item in Tier 3 and the one most worth prototyping on a single match before
committing.

### Q59 — "foot races between their centre back and an opposing striker in behind"
Dyadic, and the pair is named at query time, so compute lazily:

1. Both players' speed above a threshold (the schema's own bands: `hsr` 20–25 km/h,
   `sprinting` >25) for a sustained window (≥1s, i.e. 10 frames).
2. Velocity vectors roughly parallel (cosine similarity > ~0.8) and directed toward the
   defending goal.
3. `separation_gain` over the window — who is winning the race.
4. Require the ball to be ahead of both, or a through-pass event within ~1s before, so this
   is a race *for the ball* rather than two players jogging the same way.

Emit as a window/interval, not a single frame — the eventual clip is the race, so this
doubles as a clip-boundary signal for the open ranking question.

### Q66 — "winger tracked back but failed to make a recovery run"
The reason this is unresolved is that all ten off-ball-run subtypes (behind, coming_short,
cross_receiver, dropping_off, overlap, pulling_half_space, pulling_wide,
run_ahead_of_the_ball, support, underlap) are **attacking**. There is no defensive-run tag.
Tracking supplies one directly:

- `recovery_run` = sustained movement toward own goal (negative x in the team's own frame)
  above a speed band, while the team is out of possession.
- Then Q66 is the anti-join: the winger's team lost the ball, the winger *did* move back
  (tracked back) but never reached a `recovery_run` speed/distance threshold, or never
  entered their own defensive third before the opponent's attack ended.

This also gives the whole test set a defensive-work vocabulary it currently lacks, which is
worth more than the one question.

### Q27 — offside: honest limits
Tracking gives **offside position** (attacker beyond the second-last defender at the moment
of the pass, computed from the last-defensive-line x and the pass frame). It cannot give
**offside calls** — those are referee decisions, and the dataset has no referee-event feed.
A coach asking "show me offside calls against their front line" would get offside *positions*
instead, which is a different question and will include plenty of moments where play
continued. Recommend implementing it as `in_offside_position_at_pass` and **keeping Q27
tagged unresolved**, with the new column offered as a related-but-different filter, rather
than quietly redefining the question to match what the data can do.

---

## 4. Further tracking enrichments worth building

These are not needed by any specific test-set question, but each adds a filter dimension the
event schema cannot express — and the test set is a sample of coach questions, not the
population. Ordered by (value / effort).

### 4a. Space and pressure geometry — highest value
- **Pitch control / Voronoi surface.** Share of the pitch, and of the final third and box,
  controlled by each team per frame. Turns "did they have space" from a proxy into a
  measurement, and underpins a lot of fuzzy coach language ("we had joy in behind", "no
  space between the lines").
- **`space_between_the_lines`** — free area between the opponent's defensive and midfield
  lines. Directly answers a phrase coaches use constantly that has no schema field.
- **Free-man detection** — an attacker with no opponent within N metres in a given zone.
  `separation_start/end` exists but only at event moments and only for the event's own
  player; this generalises it to every player every frame.
- **Passing-lane openness** — is the straight line between two players intersected by an
  opponent within a corridor. Makes "the pass was on" answerable, and sharpens
  `passing_option_score` with a geometric reason.

### 4b. Set-piece geometry — cheap, and two questions become exact
Corners and free kicks are a small, well-delimited set of frames, so this is a low-cost job
with immediate payoff:
- **Near/far post zone occupancy** at the moment of delivery → makes Q68 ("no player attacked
  the near post") exact instead of a `run_ahead_of_the_ball` proxy.
- **First-contact player and location** → makes Q70 ("no defender contested the first ball")
  exact instead of "any defensive engagement in the same phase".
- **Marking assignment** — nearest opponent per attacker at delivery, and whether that
  assignment held. Opens up zonal-vs-man questions that nothing in the current schema can
  touch.

### 4c. Dyadic and relational features
- **Nearest-opponent distance per player per frame** (the 22-row-per-frame version, cheap).
  Generalises `separation_*` off the event row.
- **Marking-pair stability** — how long a defender stays nearest to the same attacker.
- **Defensive cover geometry** — for a beaten defender, whether another defender entered the
  vacated channel within N seconds. This is the missing half of Q69, currently approximate.
- **Overload detection** — local player-count imbalance in a zone (3v2 on the right). Coaches
  ask about overloads constantly; `n_teammates_ahead`/`n_opponents_ahead` are ball-relative
  counts, not local ones.

### 4d. Movement quality
- **Acceleration / deceleration events**, direction changes, turns — the schema has speed
  bands but not change-of-pace, which is most of what scouts mean by "sharp".
- **Run curvature and timing relative to the pass** — whether a run was early, late, or
  arrived with the ball. `off_ball_run` says a run happened, not whether it was well-timed.
- **Distance covered by phase and game state** — physical-output questions the aggregates
  CSVs answer at season level but not per clip.

### 4e. Clip-boundary support — ties into the open ranking question
`CLAUDE.md` still lists clip ranking and "how do multiple matching rows collapse into one
clip" as undecided. Tracking helps directly:
- **Possession-continuity boundaries** from ball and player motion give natural clip start/end
  points that are not tied to an event row.
- **Per-frame interest signal** (ball speed, pitch-control swing, proximity to goal) for
  choosing which N seconds of a long possession to show.
- Combined with `team_possession_id` from Tier 1, this is most of a clip segmenter: chain id
  gives the grouping, tracking gives the in/out points.

---

## 5. Sequencing

1. **`tracking_to_parquet.py`** — JSONL → Parquet, plus the per-(match, team, period) mirror
   sign derived by measurement. Everything else depends on this, and it is mostly mechanical.
2. **Frame-level team shape** (4a partially, Q17) — one table, no dyads, immediate payoff,
   and it validates the join end-to-end on a metric that is easy to eyeball.
3. **Set-piece geometry** (4b) — cheapest remaining win, converts Q68 and Q70 to exact.
4. **Recovery runs** (Q66) — small, and unlocks defensive-work filtering generally.
5. **Dyadic/lazy pair features** (Q59, then Q56) — hardest, most parameter-sensitive;
   prototype on one match first.
6. **Pitch control** (4a proper) — highest ceiling, but it is a modelling choice as much as
   an engineering one and should not gate the rest.

Per the project's brainstorm-then-build habit: check each stage against the test set before
building the next, and prefer measuring the data over trusting the spec — of the four
concrete Tier 1 bugs found, three came from a documented field behaving differently than
documented, and one from a field existing in only half the matches.
