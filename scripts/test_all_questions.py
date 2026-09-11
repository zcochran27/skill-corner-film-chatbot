"""
Runs all 80 questions from coach_question_test_set.md against every real match currently
in data/matches/ (fetch with scripts/fetch_match_data.sh, or see its docstring for pulling
more than one match at once) and prints a markdown results table.

Each question is marked with a fidelity level:
  - exact       : the query directly matches what the question asks.
  - approximate : the query is a reasonable proxy, but coarser than the real question
                   (documented per-question why).
  - unresolved  : nothing in the SkillCorner schema (events + match roster) can answer
                   this question as asked; documented why.

Usage:
    python scripts/test_all_questions.py > /tmp/all_questions_results.md
"""

import glob
import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "matches"


def load_events() -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA_DIR / "*" / "*_dynamic_events.csv")))
    if not files:
        raise FileNotFoundError(f"No dynamic_events.csv files found under {DATA_DIR}")
    dfs = [pd.read_csv(f, low_memory=False) for f in files]
    events = pd.concat(dfs, ignore_index=True)
    events["_pk"] = events["match_id"].astype(str) + "_" + events["phase_index"].astype(str)
    return events, len(files)


def load_roster() -> pd.DataFrame:
    rows = []
    for f in sorted(DATA_DIR.glob("*/*_match.json")):
        m = json.load(open(f))
        for p in m["players"]:
            rows.append(dict(
                match_id=m["id"], player_id=p["id"], number=p.get("number"),
                is_substitute=(p.get("start_time") not in (None, "00:00:00")),
            ))
    return pd.DataFrame(rows)


def phase_reaches(events: pd.DataFrame, starts: pd.DataFrame, col: str, val: str) -> pd.DataFrame:
    """Starting events whose phase (_pk) contains at least one row where col == val."""
    keys = starts["_pk"].unique()
    sub = events[events["_pk"].isin(keys)]
    reached_keys = sub.loc[sub[col] == val, "_pk"].unique()
    return starts[starts["_pk"].isin(reached_keys)]


def main() -> None:
    events, n_matches = load_events()
    roster = load_roster()
    events = events.merge(roster, on=["match_id", "player_id"], how="left")

    PP = events[events.event_type == "player_possession"]
    OBR = events[events.event_type == "off_ball_run"]
    OBE = events[events.event_type == "on_ball_engagement"]
    PO = events[events.event_type == "passing_option"]

    def own_box(df):
        # penalty_area_start/end only ever fires for the ATTACKING box (x > 0, since
        # coordinates are mirrored so the team on the ball always attacks left-to-right).
        # There's no equivalent flag for "defensive" box entries, so approximate with the
        # same real-world box geometry (16.5m x 40.32m) on the negative-x side instead.
        return (df.x_start < -36) & (df.y_start.abs() < 20.16)

    WINGERS = ["LW", "RW"]
    CB = ["CB", "LCB", "RCB"]
    FB = ["LB", "RB", "LWB", "RWB"]
    PIVOT = ["DM", "LDM", "RDM"]

    Q = []  # (id, category, fidelity, fields_used, note, result_df_or_None)

    def add(qid, cat, fidelity, fields, note, df):
        Q.append(dict(id=qid, category=cat, fidelity=fidelity, fields=fields, note=note, df=df))

    # --- Category 1: Player-Specific (1-11) ---
    add(1, "Player-specific", "exact", "player_position, start_type, third_start",
        "", PP[(PP.player_position == "LW") & (PP.start_type == "pass_reception") & (PP.third_start == "attacking_third")])
    add(2, "Player-specific", "exact", "number (roster join), event_type, penalty_area_start/end",
        "", events[(events.number == 9) & (events.event_type == "player_possession") & (events.penalty_area_start | events.penalty_area_end)])
    add(3, "Player-specific", "exact", "player_position, carry, x_start/x_end",
        "", PP[(PP.player_position == "RB") & (PP.carry == True) & (PP.x_start < 0) & (PP.x_end >= 0)])
    add(4, "Player-specific", "approximate", "player_position, event_type, period",
        "counts all CB on_ball_engagement rows, not just the starting pairing specifically",
        events[(events.player_position.isin(CB)) & (events.event_type == "on_ball_engagement") & (events.period == 2)])
    add(5, "Player-specific", "unresolved", "-",
        "no captain flag anywhere in match.json or dynamic_events.csv", None)
    add(6, "Player-specific", "approximate", "player_position, event_type, end_type, overall_pressure_start, third_end",
        "'midfield' approximated as third_end == middle_third",
        PP[(PP.player_position == "GK") & (PP.end_type == "pass") & (PP.third_end == "middle_third") & (PP.overall_pressure_start.isin(["high_pressure", "very_high_pressure"]))])
    add(7, "Player-specific", "approximate", "player_position, event_type, speed_avg_band",
        "'without the ball' approximated as off_ball_run rows (the schema's own definition of an off-ball run)",
        OBR[(OBR.player_position == "RW") & (OBR.speed_avg_band == "sprinting")])
    add(8, "Player-specific", "exact", "player_position, one_touch, third_end",
        "", PP[(PP.player_position.isin(["CM", "RM", "LM", "AM"])) & (PP.one_touch == True) & (PP.third_end == "attacking_third")])
    add(9, "Player-specific", "exact", "player_position, event_type, intended_run_behind",
        "", OBR[(OBR.player_position == "LB") & (OBR.intended_run_behind == True)])
    add(10, "Player-specific", "exact", "player_position, pass_range, high_pass",
        "", PP[(PP.player_position == "GK") & (PP.pass_range == "long") & (PP.high_pass == True)])
    add(11, "Player-specific", "exact", "player_position, event_subtype",
        "", OBR[(OBR.player_position == "CF") & (OBR.event_subtype == "dropping_off")])

    # --- Category 2: Spatial / Positional (12-22) ---
    add(12, "Spatial", "exact", "player_position, out_to_in, third_end, x_end",
        "", PP[(PP.player_position.isin(WINGERS)) & (PP.out_to_in == True) & (PP.third_end == "attacking_third") & (PP.x_end.between(20, 40))])
    add(13, "Spatial", "exact", "channel_start",
        "", PP[(PP.channel_start.isin(["half_space_left", "half_space_right"])) & (PP.start_type == "pass_reception")])
    add(14, "Spatial", "approximate", "player_position, x_start, game_state",
        "'back four' approximated as CB+FB positions; 'protecting a lead' as game_state == winning",
        events[(events.player_position.isin(CB + FB)) & (events.x_start < -5) & (events.game_state == "winning")])
    add(15, "Spatial", "exact", "event_subtype, channel_start",
        "off_ball_run cross_receiver rows split by originating channel as a proxy for byline vs deeper wide crosses",
        OBR[(OBR.event_subtype == "cross_receiver") & (OBR.channel_start.isin(["wide_left", "wide_right"]))])
    add(16, "Spatial", "exact", "player_position, x_start, n_teammates_ahead_start",
        "", events[(events.player_position.isin(FB)) & (events.x_start > 10) & (events.n_teammates_ahead_start <= 1)])
    add(17, "Spatial", "unresolved", "-",
        "'block shape/width' is a team-shape metric over simultaneous player positions - needs raw tracking frames, not per-event rows", None)
    add(18, "Spatial", "approximate", "penalty_area_start, start_type",
        "'cutback' approximated as a pass reception inside the box following a pass, without confirming the pass originated wide/behind the defense",
        PP[(PP.penalty_area_start == True) & (PP.start_type == "pass_reception")])
    add(19, "Spatial", "exact", "inside_defensive_shape_start",
        "", PP[(PP.inside_defensive_shape_start == True) & (PP.start_type == "pass_reception")])
    add(20, "Spatial", "exact", "last_line_break",
        "", PP[PP.last_line_break == True])
    add(21, "Spatial", "approximate", "penalty_area_end, y_end",
        "six-yard vs penalty-spot split approximated by |y_end| (near-goal-line width) since no six-yard-box flag exists",
        PP[(PP.end_type == "pass") & (PP.penalty_area_end == True)])
    add(22, "Spatial", "exact", "player_position, channel_start, location_to_player_in_possession_start",
        "", PP[(PP.player_position.isin(WINGERS)) & (PP.channel_start.isin(["wide_left", "wide_right"])) & (PP.start_type == "pass_reception")])

    # --- Category 3: Event-Type (23-33) ---
    add(23, "Event-type", "exact", "event_type, end_type, penalty_area_end",
        "", PP[(PP.end_type == "shot") & (PP.penalty_area_end == False)])
    add(24, "Event-type", "exact", "game_interruption_before",
        "", events[events.game_interruption_before == "corner_against"])
    add(25, "Event-type", "approximate", "event_subtype, third_start",
        "no explicit 'through ball' tag; approximated as a line-breaking pass from the final third",
        PP[(PP.third_start == "attacking_third") & (PP.first_line_break == True)])
    add(26, "Event-type", "exact", "start_type, third_start",
        "", PP[(PP.start_type == "pass_interception") & (PP.third_start == "defensive_third")])
    add(27, "Event-type", "unresolved", "-",
        "no offside tag present in the real Dynamic Events schema (only in the original synthetic notebook's assumed field list)", None)
    add(28, "Event-type", "exact", "player_position, pass_range",
        "", PP[(PP.player_position == "GK") & (PP.pass_range == "long")])
    add(29, "Event-type", "approximate", "end_type, x_start/y_start (own-box geometry), overall_pressure_start",
        "penalty_area_start only flags the attacking box (see own_box() docstring); own box approximated by geometry instead",
        PP[(PP.end_type == "clearance") & own_box(PP) & (PP.overall_pressure_start.isin(["high_pressure", "very_high_pressure"]))])
    add(30, "Event-type", "exact", "quick_pass, third_start",
        "", PP[(PP.quick_pass == True) & (PP.third_start == "defensive_third")])
    add(31, "Event-type", "exact", "give_and_go, initiate_give_and_go (off_ball_run only)",
        "give_and_go/initiate_give_and_go only populated on off_ball_run rows, not player_possession",
        OBR[(OBR.give_and_go == True) | (OBR.initiate_give_and_go == True)])
    add(32, "Event-type", "approximate", "is_header, x_start/y_start (own-box geometry), game_interruption_before",
        "restricted to *_against interruptions (defending a set piece) since penalty_area_start only flags the attacking box; own box approximated by geometry",
        events[(events.is_header == True) & own_box(events) & (events.game_interruption_before.isin(["corner_against", "free_kick_against"]))])
    add(33, "Event-type", "exact", "player_targeted_dangerous, player_targeted_difficult_pass_target",
        "", PP[(PP.player_targeted_dangerous == True) & (PP.player_targeted_difficult_pass_target == True)])

    # --- Category 4: Sequence / Chain (34-44) ---
    add(34, "Sequence", "exact", "start_type, third_start, lead_to_shot",
        "", PP[(PP.start_type.isin(["recovery", "pass_interception"])) & (PP.third_start == "middle_third") & (PP.lead_to_shot == True)])
    starts_35 = PP[(PP.team_in_possession_phase_type == "build_up") & (PP.game_interruption_before == "goal_kick_for")]
    add(35, "Sequence", "approximate", "team_in_possession_phase_type, game_interruption_before, third_end (phase-level)",
        "'reached the final third' checked anywhere later in the same phase_index, not strictly the same unbroken possession chain",
        phase_reaches(events, starts_35, "third_end", "attacking_third"))
    add(36, "Sequence", "approximate", "end_type, player_position, lead_to_shot",
        "'counter-attack' approximated as any header clearance by a defender that led to a shot within the pipeline's own lead_to_shot window",
        PP[(PP.is_header == True) & (PP.end_type == "clearance") & (PP.player_position.isin(CB + FB)) & (PP.lead_to_shot == True)])
    add(37, "Sequence", "exact", "n_player_possessions_in_phase, team_possession_loss_in_phase",
        "", PP[(PP.n_player_possessions_in_phase >= 5) & (PP.team_possession_loss_in_phase == True)])
    add(38, "Sequence", "exact", "give_and_go, lead_to_shot (off_ball_run)",
        "give_and_go only populated on off_ball_run rows; lead_to_shot is present on all 4 event types per schema",
        OBR[(OBR.give_and_go == True) & (OBR.lead_to_shot == True)])
    add(39, "Sequence", "exact", "game_interruption_before, lead_to_shot",
        "checks lead_to_shot on the corner event itself (schema defines lead_to_shot as shot within 10s; 6s is a subset we can't isolate exactly)",
        events[(events.game_interruption_before == "corner_for") & (events.lead_to_shot == True)])
    add(40, "Sequence", "exact", "give_and_go, lead_to_shot (off_ball_run)",
        "same query as Q38, kept separate since the test-set question is phrased at the possession level rather than the off-ball-run level",
        OBR[(OBR.give_and_go == True) & (OBR.lead_to_shot == True)])
    add(41, "Sequence", "exact", "first_line_break, second_last_line_break, last_line_break, lead_to_shot",
        "8s window approximated by lead_to_shot (schema's own window is 10s)",
        PP[(PP.first_line_break | PP.second_last_line_break | PP.last_line_break) & (PP.lead_to_shot == True)])
    add(42, "Sequence", "exact", "pressing_chain, pressing_chain_length, pressing_chain_end_type",
        "", OBE[(OBE.pressing_chain == True) & (OBE.pressing_chain_length >= 3) & (OBE.pressing_chain_end_type == "regain")])
    starts_43 = PP[PP.start_type == "throw_in_reception"]
    add(43, "Sequence", "approximate", "start_type, third_end (phase-level)",
        "'reached the box' checked anywhere later in the same phase_index",
        phase_reaches(events, starts_43, "penalty_area_end", True))
    add(44, "Sequence", "approximate", "event_subtype, team_out_of_possession_phase_type",
        "'current_team_out_of_possession_previous_phase_type' is only populated on player_possession rows, not on_ball_engagement, so this uses the engagement's own current out-of-possession phase (high_block = pressing an opponent build-up) instead of a true previous-phase check",
        OBE[(OBE.event_subtype == "counter_press") & (OBE.team_out_of_possession_phase_type == "high_block")])

    # --- Category 5: Game-State / Temporal (45-54) ---
    add(45, "Game-state", "exact", "minute_start, game_state, team_out_of_possession_phase_type",
        "", events[(events.minute_start >= 75) & (events.game_state == "winning") & (events.team_out_of_possession_phase_type.notna())])
    add(46, "Game-state", "exact", "minute_start, team_in_possession_phase_type/team_out_of_possession_phase_type",
        "", events[(events.minute_start < 10) & ((events.team_in_possession_phase_type == "transition") | (events.team_out_of_possession_phase_type == "defending_transition"))])
    add(47, "Game-state", "exact", "game_state, game_interruption_before",
        "", events[(events.game_state == "losing") & (events.game_interruption_before.notna())])
    add(48, "Game-state", "approximate", "minute_start, start_type, end_type",
        "'game management' approximated as throw-in receptions or keep-possession events late in the match",
        PP[(PP.minute_start >= 80) & (PP.start_type.isin(["throw_in_reception", "keep_possession"]))])
    add(49, "Game-state", "unresolved", "-",
        "requires knowing the exact minute of the opponent's goal per match to define a 'right after conceding' window, which is not in the events table (only game_state, not goal timestamps) - would need to derive from game_interruption_before == goal_against transitions", None)
    add(50, "Game-state", "exact", "minute_start, team_in_possession_phase_type",
        "", PP[(PP.minute_start < 5) & (PP.team_in_possession_phase_type == "build_up")])
    add(51, "Game-state", "unresolved", "-",
        "same limitation as Q49 - no goal-timestamp field to anchor a window on", None)
    add(52, "Game-state", "exact", "minute_start, game_state, team_in_possession_phase_type",
        "", PP[(PP.minute_start >= 80) & (PP.game_state == "losing") & (PP.team_in_possession_phase_type == "direct")])
    add(53, "Game-state", "exact", "minute_start, game_interruption_before",
        "", events[(events.minute_start >= 90) & (events.game_interruption_before.notna())])
    add(54, "Game-state", "exact", "team_score, opponent_team_score, pass_range (aggregate, not a filter)",
        "aggregate comparison (mean long-pass share) rather than a per-row filter; see script output", None)

    # --- Category 6: Comparative / Relational (55-64) ---
    add(55, "Comparative", "approximate", "player_position, interplayer_distance, n_teammates_ahead_start",
        "'isolated' approximated as the fullback in possession with a tracked nearest-opponent distance and no teammates ahead of the ball to combine with; interplayer_distance is only populated on player_possession/passing_option rows, not on_ball_engagement",
        PP[(PP.player_position.isin(FB)) & (PP.interplayer_distance.notna()) & (PP.n_teammates_ahead_start == 0)])
    add(56, "Comparative", "unresolved", "-",
        "'dragged out of position' needs a before/after positional baseline for the CB across the possession, which needs raw tracking frames, not single event rows", None)
    add(57, "Comparative", "approximate", "player_position, last_line_break",
        "'double pivot split by a vertical pass' approximated as any line-break pass played by a DM/LDM/RDM (player_in_possession_position is null on player_possession rows since player_position already identifies the passer there; not verified geometrically as 'through the pivot')",
        PP[(PP.player_position.isin(PIVOT)) & (PP.last_line_break == True)])
    add(58, "Comparative", "exact", "n_opponents_ahead_end",
        "", PP[PP.n_opponents_ahead_end == 0])
    add(59, "Comparative", "unresolved", "-",
        "'foot race' needs relative speed/position over a shared window between two specific players from raw tracking, not single-player event rows", None)
    add(60, "Comparative", "exact", "player_position, separation_start",
        "", events[(events.player_position.isin(FB)) & (events.separation_start < 2)])
    add(61, "Comparative", "exact", "player_in_possession_position, n_teammates_ahead_start, n_player_targeted_opponents_ahead_start",
        "", PP[(PP.player_position.isin(PIVOT)) & (PP.n_teammates_ahead_start < 2)])
    add(62, "Comparative", "approximate", "player_position, event_type, event_subtype, pressing_chain_end_type",
        "'won' approximated as a pressing/pressure engagement whose chain ended in a regain",
        OBE[(OBE.player_position.isin(CB)) & (OBE.event_subtype.isin(["pressing", "pressure"])) & (OBE.pressing_chain_end_type == "regain")])
    add(63, "Comparative", "exact", "n_opponents_overtaken",
        "", events[events.n_opponents_overtaken >= 2])
    add(64, "Comparative", "exact", "n_passing_options_dangerous_difficult",
        "", PP[PP.n_passing_options_dangerous_difficult >= 1])

    # --- Category 7: Negative / Absence (65-74) ---
    gk_windows = events[events.game_interruption_before.isin(["goal_kick_for", "goal_kick_against"])]
    cf_presses = OBE[OBE.player_position == "CF"]
    merged_65 = gk_windows.merge(cf_presses[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
    add(65, "Negative/absence", "exact", "game_interruption_before, event_type, player_position (anti-join on phase)",
        "", merged_65[merged_65["_merge"] == "left_only"])
    add(66, "Negative/absence", "unresolved", "-",
        "'tracked back' (defensive recovery run without a tagged event) vs 'no recovery run' can't be distinguished from off_ball_run tags alone - this is the exact anti-pattern case the original exploration flagged as needing its own window logic, and the window definition itself (what counts as 'should have recovered') isn't in the schema", None)
    counter_phases = PP[(PP.team_in_possession_phase_type == "quick_break") & (PP.n_opponents_ahead_end == 0)]
    shots_in_phase = PP[PP.end_type == "shot"]
    merged_67 = counter_phases.merge(shots_in_phase[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
    add(67, "Negative/absence", "approximate", "team_in_possession_phase_type, n_opponents_ahead_end, end_type (anti-join on phase)",
        "'numerical advantage' approximated as zero opponents ahead at the end of a possession",
        merged_67[merged_67["_merge"] == "left_only"])
    corners = events[events.game_interruption_before == "corner_for"]
    near_post_runs = OBR[OBR.event_subtype == "run_ahead_of_the_ball"]
    merged_68 = corners.merge(near_post_runs[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
    add(68, "Negative/absence", "approximate", "game_interruption_before, event_subtype (anti-join on phase)",
        "no explicit 'near post' tag; approximated via the closest available off-ball-run subtype",
        merged_68[merged_68["_merge"] == "left_only"])
    add(69, "Negative/absence", "unresolved", "-",
        "'covering defender rotated across' needs multi-player tracking geometry, not a taggable single event", None)
    corners_70 = events[events.game_interruption_before == "corner_against"]
    contests_70 = OBE[OBE.event_subtype.isin(["pressing", "pressure"])]
    merged_70 = corners_70.merge(contests_70[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
    add(70, "Negative/absence", "approximate", "game_interruption_before, event_subtype (anti-join on phase)",
        "'contested the first ball' approximated as any defensive engagement in the same phase",
        merged_70[merged_70["_merge"] == "left_only"])
    add(71, "Negative/absence", "exact", "n_passing_options, targeted, penalty_area_end",
        "computed at passing_option row level: options inside the box that were never the ball's actual destination",
        PO[(PO.penalty_area_end == True) & (PO.targeted == False)])
    lb_72 = PP[PP.first_line_break == True]
    add(72, "Negative/absence", "exact", "first_line_break, lead_to_shot",
        "", lb_72[lb_72.lead_to_shot == False])
    add(73, "Negative/absence", "exact", "pressing_chain, pressing_chain_end_type",
        "", OBE[(OBE.pressing_chain == True) & (OBE.pressing_chain_end_type == "disruption")])
    trans_74 = PP[(PP.team_in_possession_phase_type == "transition")]
    cpress_74 = OBE[OBE.event_subtype == "counter_press"]
    merged_74 = trans_74.merge(cpress_74[["_pk"]].drop_duplicates(), on="_pk", how="left", indicator=True)
    add(74, "Negative/absence", "approximate", "team_in_possession_phase_type, event_subtype (anti-join on phase)",
        "'had numbers back' not independently verified (would need opponent count on the defending side)",
        merged_74[merged_74["_merge"] == "left_only"])

    # --- Category 8: Composite / Blended (75-80) ---
    add(75, "Composite", "exact", "player_position, out_to_in, third_end, x_end, game_state, minute_start",
        "", PP[(PP.player_position.isin(WINGERS)) & (PP.out_to_in == True) & (PP.third_end == "attacking_third") & (PP.x_end.between(20, 40)) & (PP.game_state == "winning") & (PP.minute_start >= 75)])
    add(76, "Composite", "unresolved", "-",
        "inherits Q49's limitation: no goal-timestamp field to anchor a 'right after conceding' window", None)
    add(77, "Composite", "exact", "give_and_go, third_start, lead_to_shot (off_ball_run)",
        "give_and_go only populated on off_ball_run rows; 6s window approximated by lead_to_shot's 10s definition, same caveat as Q39/41",
        OBR[(OBR.give_and_go == True) & (OBR.third_start == "attacking_third") & (OBR.lead_to_shot == True)])
    add(78, "Composite", "unresolved", "-",
        "inherits Q5's limitation (no captain flag) on top of Q55's isolation approximation", None)
    fronttwo_starts = events[(events.player_position == "CF") & (events.game_interruption_before.isin(["goal_kick_for", "goal_kick_against"]))]
    add(79, "Composite", "approximate", "player_position, game_interruption_before, pressing_chain (phase-level)",
        "checks for a CF-involved pressing chain ending in regain anywhere in the same phase as a goal kick, not strictly a 5s start window",
        phase_reaches(events[events.pressing_chain_end_type == "regain"], fronttwo_starts, "pressing_chain_end_type", "regain"))
    add(80, "Composite", "exact", "is_substitute (roster join), last_line_break, lead_to_shot",
        "", PP[(PP.is_substitute == True) & (PP.last_line_break == True) & (PP.lead_to_shot == True)])

    print(f"Matches loaded: {n_matches} | Total dynamic events: {len(events)}\n")
    print("| # | Category | Fidelity | Hits (rows) | Matches with ≥1 hit |")
    print("|---|---|---|---|---|")
    for q in Q:
        if q["df"] is None:
            print(f"| {q['id']} | {q['category']} | {q['fidelity']} | — | — |")
        else:
            n_hits = len(q["df"])
            n_matches_hit = q["df"]["match_id"].nunique() if n_hits else 0
            print(f"| {q['id']} | {q['category']} | {q['fidelity']} | {n_hits} | {n_matches_hit}/{n_matches} |")

    # Q54 aggregate (not a row filter)
    long_share = PP.groupby(PP.team_score - PP.opponent_team_score >= 2)["pass_range"].apply(
        lambda s: (s == "long").mean()
    )
    print("\nQ54 aggregate — share of long passes by scoreline margin (True = leading by 2+):")
    print(long_share.to_string())


if __name__ == "__main__":
    main()
