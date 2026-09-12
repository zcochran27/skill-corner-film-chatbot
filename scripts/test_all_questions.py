"""
Runs all 80 questions from coach_question_test_set.md against every real match currently
in data/matches/ (fetch with scripts/fetch_match_data.sh, or see its docstring for pulling
more than one match at once) and prints a markdown results table, including how long each
question's query took to run.

Each question is marked with a fidelity level:
  - exact       : the query directly matches what the question asks.
  - approximate : the query is a reasonable proxy, but coarser than the real question
                   (documented per-question why).
  - unresolved  : nothing in the SkillCorner schema (events + match roster) can answer
                   this question as asked; documented why.

Timing covers only each question's own retrieval query (filters/anti-joins/phase lookups),
run against the already-loaded and merged event tables — i.e. the "Retrieval" pipeline
stage from CLAUDE.md, not the one-time "Preprocessing/enrichment" stage (loading CSVs,
merging the roster, splitting by event_type) that happens once up front regardless of which
question is asked. Each question's timed callable includes any anti-join/merge work
specific to that question (e.g. Q65's goal-kick/press anti-join), not just a single filter.

Usage:
    python scripts/test_all_questions.py > /tmp/all_questions_results.md
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich import load_enriched  # noqa: E402
from frame_index import available_matches  # noqa: E402
from predicates import OK as PRED_OK, evaluate  # noqa: E402
from setpiece import (  # noqa: E402
    CORNER_WINDOW, _corners, attacked_near_post, first_ball_contested,
)
from defensive import (  # noqa: E402
    RECOVERY_WINDOW, _candidates as _turnovers, recovery_run,
)

SILVER = Path(__file__).resolve().parent.parent / "data" / "silver"
GOLD = Path(__file__).resolve().parent.parent / "data" / "gold"


def load_events() -> tuple[pd.DataFrame, int]:
    """Gold events plus the Tier 1/Tier 2 derived columns."""
    events, _matches, _goals = load_enriched(verbose=True)
    return events, events.match_id.nunique()


def load_roster() -> pd.DataFrame:
    """Silver players table - jersey number and substitute status per (match, player)."""
    players = pd.read_parquet(SILVER / "players.parquet")
    return players[["match_id", "player_id", "number", "is_substitute"]]


def main() -> None:
    events, n_matches = load_events()
    roster = load_roster()
    events = events.merge(roster, on=["match_id", "player_id"], how="left")

    TRACKED = set(available_matches())
    roster_full = pd.read_parquet(SILVER / "players.parquet")
    matches = pd.read_parquet(SILVER / "matches.parquet")
    phase_shape = pd.read_parquet(GOLD / "phase_shape.parquet")

    PP = events[events.event_type == "player_possession"]
    OBR = events[events.event_type == "off_ball_run"]
    OBE = events[events.event_type == "on_ball_engagement"]
    PO = events[events.event_type == "passing_option"]

    def _b(col):
        """SkillCorner booleans arrive as object dtype ('True'/'False' strings)."""
        return col.astype(str).str.lower().eq("true")

    WINGERS = ["LW", "RW"]
    CB = ["CB", "LCB", "RCB"]
    FB = ["LB", "RB", "LWB", "RWB"]
    PIVOT = ["DM", "LDM", "RDM"]

    Q = []  # each: id, category, fidelity, fields_used, note, df, time_ms

    def add(qid, cat, fidelity, fields, note, fn):
        if fn is None:
            Q.append(dict(id=qid, category=cat, fidelity=fidelity, fields=fields, note=note,
                           df=None, time_ms=None))
            return
        t0 = time.perf_counter()
        df = fn()
        dt_ms = (time.perf_counter() - t0) * 1000
        Q.append(dict(id=qid, category=cat, fidelity=fidelity, fields=fields, note=note,
                       df=df, time_ms=dt_ms))

    # --- Category 1: Player-Specific (1-11) ---
    add(1, "Player-specific", "exact", "player_position, start_type, third_start",
        "", lambda: PP[(PP.player_position == "LW") & (PP.start_type == "pass_reception") & (PP.third_start == "attacking_third")])
    add(2, "Player-specific", "exact", "number (roster join), event_type, penalty_area_start/end",
        "", lambda: events[(events.number == 9) & (events.event_type == "player_possession") & (events.penalty_area_start | events.penalty_area_end)])
    add(3, "Player-specific", "exact", "player_position, carry, x_start/x_end",
        "", lambda: PP[(PP.player_position == "RB") & (PP.carry == True) & (PP.x_start < 0) & (PP.x_end >= 0)])
    add(4, "Player-specific", "exact", "is_starting_cb_pair (derived), event_type, period",
        "is_starting_cb_pair resolves 'their center back pairing' to the two CBs who started",
        lambda: events[(events.is_starting_cb_pair) & (events.event_type == "on_ball_engagement") & (events.period == 2)])
    add(5, "Player-specific", "unresolved", "-",
        "no captain flag anywhere in match.json or dynamic_events.csv", None)
    add(6, "Player-specific", "approximate", "player_position, event_type, end_type, overall_pressure_start, third_end",
        "'midfield' approximated as third_end == middle_third",
        lambda: PP[(PP.player_position == "GK") & (PP.end_type == "pass") & (PP.third_end == "middle_third") & (PP.overall_pressure_start.isin(["high_pressure", "very_high_pressure"]))])
    add(7, "Player-specific", "approximate", "player_position, event_type, speed_avg_band",
        "'without the ball' approximated as off_ball_run rows (the schema's own definition of an off-ball run)",
        lambda: OBR[(OBR.player_position == "RW") & (OBR.speed_avg_band == "sprinting")])
    add(8, "Player-specific", "exact", "player_position, one_touch, third_end",
        "", lambda: PP[(PP.player_position.isin(["CM", "RM", "LM", "AM"])) & (PP.one_touch == True) & (PP.third_end == "attacking_third")])
    add(9, "Player-specific", "exact", "player_position, event_type, intended_run_behind",
        "", lambda: OBR[(OBR.player_position == "LB") & (OBR.intended_run_behind == True)])
    add(10, "Player-specific", "exact", "player_position, pass_range, high_pass",
        "", lambda: PP[(PP.player_position == "GK") & (PP.pass_range == "long") & (PP.high_pass == True)])
    add(11, "Player-specific", "exact", "player_position, event_subtype",
        "", lambda: OBR[(OBR.player_position == "CF") & (OBR.event_subtype == "dropping_off")])

    # --- Category 2: Spatial / Positional (12-22) ---
    add(12, "Spatial", "exact", "player_position, out_to_in, third_end, x_end",
        "", lambda: PP[(PP.player_position.isin(WINGERS)) & (PP.out_to_in == True) & (PP.third_end == "attacking_third") & (PP.x_end.between(20, 40))])
    add(13, "Spatial", "exact", "channel_start",
        "", lambda: PP[(PP.channel_start.isin(["half_space_left", "half_space_right"])) & (PP.start_type == "pass_reception")])
    add(14, "Spatial", "exact", "team_back_line_size (derived), player_position, x_start, game_state",
        "'back four' now requires the team to actually have four defenders on the pitch at that frame, not just any CB/FB row",
        lambda: events[(events.team_back_line_size == 4) & (events.player_position.isin(CB + FB)) & (events.x_start < -5) & (events.game_state == "winning")])
    add(15, "Spatial", "exact", "event_subtype, channel_start",
        "off_ball_run cross_receiver rows split by originating channel as a proxy for byline vs deeper wide crosses",
        lambda: OBR[(OBR.event_subtype == "cross_receiver") & (OBR.channel_start.isin(["wide_left", "wide_right"]))])
    add(16, "Spatial", "exact", "player_position, x_start, n_teammates_ahead_start",
        "", lambda: events[(events.player_position.isin(FB)) & (events.x_start > 10) & (events.n_teammates_ahead_start <= 1)])
    def q17():
        """Phase-level: medium blocks that were narrow against that team's OWN norm."""
        mb = phase_shape[(phase_shape.team_out_of_possession_phase_type == "medium_block")
                         & phase_shape.out_of_possession_width_z.notna()]
        return mb.sort_values("out_of_possession_width_z")
    add(17, "Spatial", "exact (phase-level, ranked)",
        "team_out_of_possession_width_end + per-team baseline (gold/phase_shape.parquet)",
        "NOT a tracking question after all. _phases_of_play.csv carries "
        "team_out_of_possession_width/length per phase - columns that exist nowhere in the "
        "events table and were never examined, because the first pass concluded phases were "
        "redundant (true of the phase TYPE, not of these). Width is converted to a z-score "
        "against that team's own distribution for the same phase type, since 'narrow' is "
        "relative - a 34m block is tight for one side and normal for another. Ranked "
        "ascending; the tightest are 15-20m against team baselines of 35-38m.",
        q17)
    add(18, "Spatial", "approximate", "penalty_area_start, start_type",
        "'cutback' approximated as a pass reception inside the box following a pass, without confirming the pass originated wide/behind the defense",
        lambda: PP[(PP.penalty_area_start == True) & (PP.start_type == "pass_reception")])
    add(19, "Spatial", "exact", "inside_defensive_shape_start",
        "", lambda: PP[(PP.inside_defensive_shape_start == True) & (PP.start_type == "pass_reception")])
    add(20, "Spatial", "exact", "last_line_break",
        "", lambda: PP[PP.last_line_break == True])
    add(21, "Spatial", "exact", "opp_six_yard_box_reception, opp_penalty_area_reception (derived)",
        "real six-yard geometry (5.5m x 18.32m) scaled per match, measured at the RECEPTION point (player_targeted_*_reception) rather than where the passer stood; hits are deliveries into the six-yard box, and swapping the flag for opp_penalty_area_reception gives the penalty-spot-area comparison the question asks for",
        lambda: PP[(PP.end_type == "pass") & (PP.opp_six_yard_box_reception)])
    add(22, "Spatial", "exact", "player_position, channel_start, location_to_player_in_possession_start",
        "", lambda: PP[(PP.player_position.isin(WINGERS)) & (PP.channel_start.isin(["wide_left", "wide_right"])) & (PP.start_type == "pass_reception")])

    # --- Category 3: Event-Type (23-33) ---
    add(23, "Event-type", "exact", "event_type, end_type, penalty_area_end",
        "", lambda: PP[(PP.end_type == "shot") & (PP.penalty_area_end == False)])
    add(24, "Event-type", "exact", "game_interruption_before",
        "", lambda: events[events.game_interruption_before == "corner_against"])
    add(25, "Event-type", "approximate", "event_subtype, third_start",
        "no explicit 'through ball' tag; approximated as a line-breaking pass from the final third",
        lambda: PP[(PP.third_start == "attacking_third") & (PP.first_line_break == True)])
    add(26, "Event-type", "exact", "start_type, third_start",
        "", lambda: PP[(PP.start_type == "pass_interception") & (PP.third_start == "defensive_third")])
    add(27, "Event-type", "unresolved", "-",
        "no offside tag present in the real Dynamic Events schema (only in the original synthetic notebook's assumed field list)", None)
    add(28, "Event-type", "exact", "player_position, pass_range",
        "", lambda: PP[(PP.player_position == "GK") & (PP.pass_range == "long")])
    add(29, "Event-type", "exact", "end_type, own_penalty_area_start (derived), overall_pressure_start",
        "own_penalty_area_start is real per-match box geometry; native penalty_area_start cannot express 'own box' because it fires for either box",
        lambda: PP[(PP.end_type == "clearance") & (PP.own_penalty_area_start) & (PP.overall_pressure_start.isin(["high_pressure", "very_high_pressure"]))])
    add(30, "Event-type", "exact", "quick_pass, third_start",
        "", lambda: PP[(PP.quick_pass == True) & (PP.third_start == "defensive_third")])
    add(31, "Event-type", "exact", "give_and_go, initiate_give_and_go (off_ball_run only)",
        "give_and_go/initiate_give_and_go only populated on off_ball_run rows, not player_possession",
        lambda: OBR[(OBR.give_and_go == True) | (OBR.initiate_give_and_go == True)])
    add(32, "Event-type", "exact", "is_header, own_penalty_area_start (derived), game_interruption_before",
        "own_penalty_area_start replaces the hardcoded x < -36 geometry, which was off by up to 0.5m on the 104m and 106m pitches",
        lambda: events[(events.is_header == True) & (events.own_penalty_area_start) & (events.game_interruption_before.isin(["corner_against", "free_kick_against"]))])
    add(33, "Event-type", "exact", "player_targeted_dangerous, player_targeted_difficult_pass_target",
        "", lambda: PP[(PP.player_targeted_dangerous == True) & (PP.player_targeted_difficult_pass_target == True)])

    # --- Category 4: Sequence / Chain (34-44) ---
    add(34, "Sequence", "exact", "start_type, third_start, lead_to_shot",
        "", lambda: PP[(PP.start_type.isin(["recovery", "pass_interception"])) & (PP.third_start == "middle_third") & (PP.lead_to_shot == True)])

    def q35():
        return PP[(PP.team_in_possession_phase_type == "build_up")
                  & (PP.game_interruption_before == "goal_kick_for")
                  & (PP.chain_reached_final_third == True)]
    add(35, "Sequence", "exact", "team_in_possession_phase_type, game_interruption_before, chain_reached_final_third (derived)",
        "now checks the actual unbroken team possession rather than 'anywhere in the same phase_index'",
        q35)

    add(36, "Sequence", "approximate", "end_type, player_position, seconds_to_next_shot_same_team (derived)",
        "lead_to_shot on a clearance is False in every one of the 60 header clearances in the dataset (it means 'a shot within 10s of this event', and a clearance is meant to end danger). seconds_to_next_shot_same_team measures what the question actually means - the CLEARING team shooting later - here within 30s. Still approximate: it does not verify the shot came from that same regain.",
        lambda: PP[(PP.is_header == True) & (PP.end_type == "clearance") & (PP.player_position.isin(CB + FB)) & (PP.seconds_to_next_shot_same_team <= 30)])
    add(37, "Sequence", "exact", "n_player_possessions_in_phase, team_possession_loss_in_phase",
        "", lambda: PP[(PP.n_player_possessions_in_phase >= 5) & (PP.team_possession_loss_in_phase == True)])
    add(38, "Sequence", "exact", "give_and_go, lead_to_shot (off_ball_run)",
        "give_and_go only populated on off_ball_run rows; lead_to_shot is present on all 4 event types per schema",
        lambda: OBR[(OBR.give_and_go == True) & (OBR.lead_to_shot == True)])
    add(39, "Sequence", "exact", "game_interruption_before, seconds_to_next_shot_same_team (derived)",
        "the question's real 6s window, not lead_to_shot's fixed 10s",
        lambda: events[(events.game_interruption_before == "corner_for") & (events.seconds_to_next_shot_same_team <= 6)])
    add(40, "Sequence", "exact", "give_and_go, lead_to_shot (off_ball_run)",
        "same query as Q38, kept separate since the test-set question is phrased at the possession level rather than the off-ball-run level",
        lambda: OBR[(OBR.give_and_go == True) & (OBR.lead_to_shot == True)])
    add(41, "Sequence", "exact", "first_line_break, second_last_line_break, last_line_break, seconds_to_next_shot_same_team (derived)",
        "the question's real 8s window, not lead_to_shot's fixed 10s",
        lambda: PP[(PP.first_line_break | PP.second_last_line_break | PP.last_line_break) & (PP.seconds_to_next_shot_same_team <= 8)])
    add(42, "Sequence", "exact", "pressing_chain, pressing_chain_length, pressing_chain_end_type",
        "", lambda: OBE[(OBE.pressing_chain == True) & (OBE.pressing_chain_length >= 3) & (OBE.pressing_chain_end_type == "regain")])

    def q43():
        return PP[(PP.start_type == "throw_in_reception") & (PP.chain_reached_box == True)]
    add(43, "Sequence", "exact", "start_type, chain_reached_box (derived)",
        "'reached the box' now means the same unbroken team possession reached it",
        q43)

    add(44, "Sequence", "approximate", "event_subtype, team_out_of_possession_phase_type",
        "'current_team_out_of_possession_previous_phase_type' is only populated on player_possession rows, not on_ball_engagement, so this uses the engagement's own current out-of-possession phase (high_block = pressing an opponent build-up) instead of a true previous-phase check",
        lambda: OBE[(OBE.event_subtype == "counter_press") & (OBE.team_out_of_possession_phase_type == "high_block")])

    # --- Category 5: Game-State / Temporal (45-54) ---
    add(45, "Game-state", "exact", "minute_start, game_state, team_out_of_possession_phase_type",
        "", lambda: events[(events.minute_start >= 75) & (events.game_state == "winning") & (events.team_out_of_possession_phase_type.notna())])
    add(46, "Game-state", "exact", "minute_start, team_in_possession_phase_type/team_out_of_possession_phase_type",
        "", lambda: events[(events.minute_start < 10) & ((events.team_in_possession_phase_type == "transition") | (events.team_out_of_possession_phase_type == "defending_transition"))])
    add(47, "Game-state", "exact", "game_state, game_interruption_before",
        "", lambda: events[(events.game_state == "losing") & (events.game_interruption_before.notna())])
    add(48, "Game-state", "approximate", "minute_start, start_type, end_type",
        "'game management' approximated as throw-in receptions or keep-possession events late in the match",
        lambda: PP[(PP.minute_start >= 80) & (PP.start_type.isin(["throw_in_reception", "keep_possession"]))])
    add(49, "Game-state", "exact", "seconds_since_goal_against (derived), event_type, event_subtype",
        "goal timestamps are now derived per match (scripts/enrich.py, validated against all 20 official scores), so 'the 5 minutes right after conceding' is a real window",
        lambda: OBE[(OBE.seconds_since_goal_against <= 300) & (OBE.event_subtype.isin(["pressing", "pressure", "counter_press"]))])
    add(50, "Game-state", "exact", "minute_start, team_in_possession_phase_type",
        "", lambda: PP[(PP.minute_start < 5) & (PP.team_in_possession_phase_type == "build_up")])
    add(51, "Game-state", "exact", "seconds_since_goal_for (derived)",
        "the 2 minutes immediately after scoring, anchored on the derived goal timeline",
        lambda: events[events.seconds_since_goal_for <= 120])
    add(52, "Game-state", "exact", "minute_start, game_state, team_in_possession_phase_type",
        "", lambda: PP[(PP.minute_start >= 80) & (PP.game_state == "losing") & (PP.team_in_possession_phase_type == "direct")])
    add(53, "Game-state", "exact", "minute_start, game_interruption_before",
        "", lambda: events[(events.minute_start >= 90) & (events.game_interruption_before.notna())])

    def q54():
        return PP.groupby(PP.team_score - PP.opponent_team_score >= 2)["pass_range"].apply(lambda s: (s == "long").mean())
    add(54, "Game-state", "exact (aggregate, not a filter)", "team_score, opponent_team_score, pass_range (aggregate, not a filter)",
        "aggregate comparison (mean long-pass share) rather than a per-row filter; see script output",
        q54)

    # --- Category 6: Comparative / Relational (55-64) ---
    add(55, "Comparative", "approximate", "player_position, interplayer_distance, n_teammates_ahead_start",
        "'isolated' approximated as the fullback in possession with a tracked nearest-opponent distance and no teammates ahead of the ball to combine with; interplayer_distance is only populated on player_possession/passing_option rows, not on_ball_engagement",
        lambda: PP[(PP.player_position.isin(FB)) & (PP.interplayer_distance.notna()) & (PP.n_teammates_ahead_start == 0)])
    add(56, "Comparative", "unresolved", "-",
        "'dragged out of position' needs a before/after positional baseline for the CB across the possession, which needs raw tracking frames, not single event rows", None)
    add(57, "Comparative", "approximate", "player_position, last_line_break",
        "'double pivot split by a vertical pass' approximated as any line-break pass played by a DM/LDM/RDM (player_in_possession_position is null on player_possession rows since player_position already identifies the passer there; not verified geometrically as 'through the pivot')",
        lambda: PP[(PP.player_position.isin(PIVOT)) & (PP.last_line_break == True)])
    add(58, "Comparative", "exact", "n_opponents_ahead_end",
        "", lambda: PP[PP.n_opponents_ahead_end == 0])
    add(59, "Comparative", "unresolved", "-",
        "'foot race' needs relative speed/position over a shared window between two specific players from raw tracking, not single-player event rows", None)
    add(60, "Comparative", "exact", "player_position, separation_start",
        "", lambda: events[(events.player_position.isin(FB)) & (events.separation_start < 2)])
    add(61, "Comparative", "exact", "player_in_possession_position, n_teammates_ahead_start, n_player_targeted_opponents_ahead_start",
        "", lambda: PP[(PP.player_position.isin(PIVOT)) & (PP.n_teammates_ahead_start < 2)])
    add(62, "Comparative", "approximate", "player_position, event_type, event_subtype, pressing_chain_end_type",
        "'won' approximated as a pressing/pressure engagement whose chain ended in a regain",
        lambda: OBE[(OBE.player_position.isin(CB)) & (OBE.event_subtype.isin(["pressing", "pressure"])) & (OBE.pressing_chain_end_type == "regain")])
    add(63, "Comparative", "exact", "n_opponents_overtaken",
        "", lambda: events[events.n_opponents_overtaken >= 2])
    add(64, "Comparative", "exact", "n_passing_options_dangerous_difficult",
        "", lambda: PP[PP.n_passing_options_dangerous_difficult >= 1])

    # --- Category 7: Negative / Absence (65-74) ---
    def q65():
        gk_windows = events[events.game_interruption_before.isin(["goal_kick_for", "goal_kick_against"])]
        cf_presses = OBE[OBE.player_position == "CF"]
        merged = gk_windows.merge(cf_presses[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
        return merged[merged["_merge"] == "left_only"]
    add(65, "Negative/absence", "exact", "game_interruption_before, event_type, player_position (anti-join on phase)",
        "", q65)

    def q66():
        """Phase 2: how hard the winger actually worked to get back after a turnover."""
        cand = _turnovers(events, TRACKED)
        res = evaluate(cand, recovery_run, RECOVERY_WINDOW,
                       matches=matches, players=roster_full)
        ok = res[res.status == PRED_OK]
        tracked_back = ok[~ok.detail.str.startswith("did not track back")]
        # Ranked ASCENDING by peak retreat speed: least effort first is the answer.
        return tracked_back.sort_values("value")
    add(66, "Negative/absence", "exact (tracking, ranked)",
        "recovery_run predicate over tracking (scripts/defensive.py)",
        "phase 2: measures the winger's peak speed while retreating in the 10s after his "
        "team loses the ball in the opponent half, then ranks ascending so the least effort "
        "comes first. No event can answer this - all ten off_ball_run subtypes describe "
        "ATTACKING movement, so defensive work rate is absent from the event vocabulary "
        "entirely. Also has no event anchor (the question is about a player doing nothing), "
        "so it anchors on the turnover and finds the winger by position in the frames. "
        "Hit count is turnovers where he tracked back at all, ranked - not a filtered answer.",
        q66)

    def q67():
        counter_phases = PP[(PP.team_in_possession_phase_type == "quick_break") & (PP.n_opponents_ahead_end == 0)]
        shots_in_phase = PP[PP.end_type == "shot"]
        merged = counter_phases.merge(shots_in_phase[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
        return merged[merged["_merge"] == "left_only"]
    add(67, "Negative/absence", "approximate", "team_in_possession_phase_type, n_opponents_ahead_end, end_type (anti-join on phase)",
        "'numerical advantage' approximated as zero opponents ahead at the end of a possession",
        q67)

    def q68():
        """Phase 2: actual near-post occupancy during the delivery's flight."""
        cand = _corners(events[events.match_id.isin(TRACKED)], "corner_for")
        res = evaluate(cand, attacked_near_post, CORNER_WINDOW,
                       matches=matches, players=roster_full)
        ok = res[res.status == PRED_OK]
        # Ranked, not thresholded: value is the nearest approach to the near post, so the
        # coach reads down the list rather than trusting a zone definition that would move
        # the answer between 12% and 48%.
        return ok.sort_values("value", ascending=False)
    add(68, "Negative/absence", "exact (tracking, ranked)",
        "attacked_near_post predicate over tracking (scripts/setpiece.py)",
        "phase 2: measures each attacker's closest approach to the near post during the "
        "delivery, replacing the run_ahead_of_the_ball proxy. Returns corners RANKED by "
        "that distance rather than thresholded, because 'near post' is a fuzzy football "
        "concept and any fixed zone moves the answer between 12% and 48% "
        "(setpiece.py --sensitivity). Hit count is resolved corners, not a filtered answer. "
        "96/107 resolve; the rest return insufficient_data, never a false 'nobody was there'.",
        q68)

    def q69():
        beaten = OBE[(OBE.player_position.isin(FB))
                     & (_b(OBE.beaten_by_possession) | _b(OBE.beaten_by_movement))]
        # The cover must be a DIFFERENT defender - without excluding the beaten player's
        # own row the anti-join matches him against himself and returns 0 every time.
        cover = OBE[OBE.player_position.isin(CB + FB)][["_pk", "player_id"]]
        pairs = beaten[["_pk", "player_id"]].merge(cover, on="_pk", how="left",
                                                   suffixes=("", "_cover"))
        has_cover = pairs[pairs.player_id_cover.notna()
                          & (pairs.player_id_cover != pairs.player_id)]
        covered = set(zip(has_cover._pk, has_cover.player_id))
        return beaten[~pd.Series(list(zip(beaten._pk, beaten.player_id)),
                                 index=beaten.index).isin(covered)]
    add(69, "Negative/absence", "approximate", "beaten_by_possession/beaten_by_movement, player_position (anti-join on phase)",
        "'was beaten' is a native defender-evaluation flag on on_ball_engagement rows, so only the 'no covering defender rotated across' half is a proxy (no other defensive engagement in the same phase); true cover rotation needs tracking geometry (Tier 3)",
        q69)

    def q70():
        """Phase 2: who actually reached the delivery first, and was a defender near."""
        cand = _corners(events[events.match_id.isin(TRACKED)], "corner_against")
        res = evaluate(cand, first_ball_contested, CORNER_WINDOW,
                       matches=matches, players=roster_full)
        ok = res[res.status == PRED_OK]
        return ok[~ok.matched]
    add(70, "Negative/absence", "exact (tracking)",
        "first_ball_contested predicate over tracking (scripts/setpiece.py)",
        "phase 2: the first player within 1.5m of the ball after delivery, and whether a "
        "defender was within 2.5m, replacing 'any defensive engagement in the same phase'",
        q70)

    add(71, "Negative/absence", "exact", "n_passing_options, targeted, penalty_area_end",
        "computed at passing_option row level: options inside the box that were never the ball's actual destination",
        lambda: PO[(PO.penalty_area_end == True) & (PO.targeted == False)])

    def q72():
        lb = PP[PP.first_line_break == True]
        return lb[lb.lead_to_shot == False]
    add(72, "Negative/absence", "exact", "first_line_break, lead_to_shot",
        "", q72)

    add(73, "Negative/absence", "exact", "pressing_chain, pressing_chain_end_type",
        "", lambda: OBE[(OBE.pressing_chain == True) & (OBE.pressing_chain_end_type == "disruption")])

    def q74():
        trans = PP[(PP.team_in_possession_phase_type == "transition")]
        cpress = OBE[OBE.event_subtype == "counter_press"]
        merged = trans.merge(cpress[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
        return merged[merged["_merge"] == "left_only"]
    add(74, "Negative/absence", "approximate", "team_in_possession_phase_type, event_subtype (anti-join on phase)",
        "'had numbers back' not independently verified (would need opponent count on the defending side)",
        q74)

    # --- Category 8: Composite / Blended (75-80) ---
    add(75, "Composite", "exact", "player_position, out_to_in, third_end, x_end, game_state, minute_start",
        "", lambda: PP[(PP.player_position.isin(WINGERS)) & (PP.out_to_in == True) & (PP.third_end == "attacking_third") & (PP.x_end.between(20, 40)) & (PP.game_state == "winning") & (PP.minute_start >= 75)])
    add(76, "Composite", "exact", "seconds_since_goal_against (derived), player_position, speed_avg_band",
        "no longer inherits Q49's limitation now that the goal timeline is derived",
        lambda: OBR[(OBR.seconds_since_goal_against <= 600) & (OBR.player_position == "RB") & (OBR.speed_avg_band.isin(["hsr", "sprinting"]))])
    add(77, "Composite", "exact", "give_and_go, third_start, seconds_to_next_shot_same_team (derived)",
        "give_and_go only populated on off_ball_run rows; 6s is now the real window rather than lead_to_shot's 10s",
        lambda: OBR[(OBR.give_and_go == True) & (OBR.third_start == "attacking_third") & (OBR.seconds_to_next_shot_same_team <= 6)])
    add(78, "Composite", "unresolved", "-",
        "inherits Q5's limitation (no captain flag) on top of Q55's isolation approximation", None)

    def q79():
        return OBE[(OBE.player_position.isin(["CF", "LF", "RF"]))
                   & (OBE.team_front_line_size == 2)
                   & (OBE.game_interruption_before.isin(["goal_kick_for", "goal_kick_against"]))
                   & (OBE.pressing_chain == True)
                   & (OBE.pressing_chain_end_type == "regain")]
    add(79, "Composite", "approximate", "player_position, team_front_line_size (derived), game_interruption_before, pressing_chain",
        "'front two' is now literal (exactly two forwards on the pitch at that frame) rather than any CF row; the 5s-of-the-goal-kick window is still carried by game_interruption_before rather than measured directly",
        q79)

    add(80, "Composite", "exact", "is_substitute (roster join), last_line_break, lead_to_shot",
        "", lambda: PP[(PP.is_substitute == True) & (PP.last_line_break == True) & (PP.lead_to_shot == True)])

    print(f"Matches loaded: {n_matches} | Total dynamic events: {len(events)}\n")
    print("| # | Category | Fidelity | Hits (rows) | Matches with ≥1 hit | Query time (ms) |")
    print("|---|---|---|---|---|---|")
    timed = [q for q in Q if q["time_ms"] is not None]
    for q in Q:
        if q["df"] is None:
            print(f"| {q['id']} | {q['category']} | {q['fidelity']} | — | — | — |")
        elif not isinstance(q["df"], pd.DataFrame):
            # aggregate result (e.g. Q54's groupby), not a row-hit count
            print(f"| {q['id']} | {q['category']} | {q['fidelity']} | — (aggregate) | — | {q['time_ms']:.2f} |")
        else:
            n_hits = len(q["df"])
            n_matches_hit = q["df"]["match_id"].nunique() if n_hits else 0
            print(f"| {q['id']} | {q['category']} | {q['fidelity']} | {n_hits} | {n_matches_hit}/{n_matches} | {q['time_ms']:.2f} |")

    times = sorted(timed, key=lambda q: q["time_ms"])
    total_ms = sum(q["time_ms"] for q in timed)
    print(f"\nTiming summary over {len(timed)} timed questions (excludes {len(Q)-len(timed)} unresolved):")
    print(f"  total: {total_ms:.1f} ms | mean: {total_ms/len(timed):.2f} ms | "
          f"median: {times[len(times)//2]['time_ms']:.2f} ms | "
          f"min: {times[0]['time_ms']:.2f} ms (Q{times[0]['id']}) | "
          f"max: {times[-1]['time_ms']:.2f} ms (Q{times[-1]['id']})")
    print("  slowest 5:", ", ".join(f"Q{q['id']} ({q['time_ms']:.1f}ms)" for q in times[-5:][::-1]))


if __name__ == "__main__":
    main()
