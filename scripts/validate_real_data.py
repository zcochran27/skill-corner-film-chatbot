"""
Re-runs the 4 sample gate queries from skillcorner_exploration.ipynb against a real
match's `_dynamic_events.csv`, in place of the synthetic data used in the first pass.

This is the "priority next step" flagged in CLAUDE.md: the exploration notebook was
built against synthetic data matching the schema exactly (no network access in that
chat's sandbox); this script checks the same 4 queries resolve against real data with
the exact real column names/values, and reports actual hit counts.

Usage:
    python scripts/fetch_match_data.sh 1874553   # populates data/matches/1874553/
    python scripts/validate_real_data.py [match_id]
"""

import sys
from pathlib import Path

import pandas as pd

MATCH_ID = sys.argv[1] if len(sys.argv) > 1 else "1874553"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "matches" / MATCH_ID
EVENTS_PATH = DATA_DIR / f"{MATCH_ID}_dynamic_events.csv"


def load_events() -> pd.DataFrame:
    if not EVENTS_PATH.exists():
        raise FileNotFoundError(
            f"{EVENTS_PATH} not found. Run scripts/fetch_match_data.sh {MATCH_ID} first."
        )
    return pd.read_csv(EVENTS_PATH, low_memory=False)


def q1_shots_outside_box(events: pd.DataFrame) -> pd.DataFrame:
    """Category 3 (event-type): 'Show me all shots taken from outside the box.'"""
    return events[
        (events.event_type == "player_possession")
        & (events.end_type == "shot")
        & (events.penalty_area_end == False)  # noqa: E712
    ]


def q2_winger_cuts_inside(events: pd.DataFrame) -> pd.DataFrame:
    """Category 2 (spatial): 'winger cut inside at the top of the box.'"""
    return events[
        (events.player_position.isin(["LW", "RW"]))
        & (events.out_to_in == True)  # noqa: E712
        & (events.third_end == "attacking_third")
        & (events.x_end.between(20, 40))
    ]


def q3_regain_to_shot(events: pd.DataFrame) -> pd.DataFrame:
    """Category 4 (sequence): 'regain in midfield and ended in a shot within 10 seconds.'"""
    return events[
        (events.event_type == "player_possession")
        & (events.start_type.isin(["recovery", "pass_interception"]))
        & (events.third_start == "middle_third")
        & (events.lead_to_shot == True)  # noqa: E712
    ]


def q4_no_cf_press_after_goal_kick(events: pd.DataFrame) -> pd.DataFrame:
    """Category 7 (negative/absence): 'striker did not press the center back after a goal kick.'"""
    goal_kick_windows = events[
        events.game_interruption_before.isin(["goal_kick_for", "goal_kick_against"])
    ].copy()
    striker_presses = events[
        (events.event_type == "on_ball_engagement") & (events.player_position == "CF")
    ]
    merged = goal_kick_windows.merge(
        striker_presses[["phase_index"]].drop_duplicates(),
        on="phase_index",
        how="left",
        indicator=True,
    )
    return merged[merged["_merge"] == "left_only"], goal_kick_windows


def main() -> None:
    events = load_events()
    print(f"Match {MATCH_ID}: {len(events)} dynamic events, "
          f"{events['event_id'].nunique()} unique event_ids")
    print(events["event_type"].value_counts().to_string())
    print()

    q1 = q1_shots_outside_box(events)
    print(f"Q1 (shots outside the box) hits: {len(q1)}")
    if len(q1):
        print(q1[["event_id", "minute_start", "player_name", "team_shortname",
                   "x_end", "y_end"]].head(6).to_string(index=False))
    print()

    q2 = q2_winger_cuts_inside(events)
    print(f"Q2 (winger cut inside at top of box) hits: {len(q2)}")
    if len(q2):
        print(q2[["event_id", "minute_start", "player_name", "channel_start",
                   "channel_end", "x_end", "y_end"]].head(6).to_string(index=False))
    print()

    q3 = q3_regain_to_shot(events)
    print(f"Q3 (regain in midfield -> lead_to_shot) hits: {len(q3)}")
    if len(q3):
        print(q3[["event_id", "minute_start", "player_name", "team_shortname",
                   "start_type", "third_start"]].head(6).to_string(index=False))
    print()

    q4, goal_kick_windows = q4_no_cf_press_after_goal_kick(events)
    print(f"Q4 (goal kick with NO CF press) — goal-kick windows: {len(goal_kick_windows)}, "
          f"with a CF press: {len(goal_kick_windows) - len(q4)}, without (hits): {len(q4)}")
    if len(q4):
        print(q4[["event_id", "minute_start", "team_shortname",
                   "game_interruption_before"]].head(6).to_string(index=False))


if __name__ == "__main__":
    main()
