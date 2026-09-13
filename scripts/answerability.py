#!/usr/bin/env python
"""
What the app can and cannot answer, and what it must say when it cannot.

The rule this enforces: **never silently substitute.** If a coach asks for something the
data does not contain, the answer is "I can't answer that, and here is why" - not the
nearest available proxy dressed up as the real thing.

This matters more here than in most retrieval systems. A coach acts on these clips. An
answer that looks like 20 offside calls but is actually 20 moments a player stood in an
offside position is worse than no answer, because nothing in the output reveals the
substitution. That is the response-layer twin of the silent-zero bug that has bitten this
project five separate times (docs/master_plan.md 4a): plausible output, no error, wrong
conclusion.

Three verdicts, three behaviours:

  NO_DATA          The concept is absent from every available source and always will be.
                   Refuse, explain, and offer the nearest thing that IS answerable.
  NOT_IMPLEMENTED  The data exists but the retrieval for it has not been built.
                   Say so plainly - it is a different promise from NO_DATA. Currently
                   empty: everything the data supports has been built.
  APPROXIMATE      Answerable only through a documented proxy. Answer, but DISCLOSE the
                   proxy in the response; never present it as exact.

Anything not listed here is answerable exactly, which is the default for 63 of the 80
test-set questions. Every question that is NOT answerable now has registered refusal text.

Usage:
    from answerability import check, Verdict
    v = check("captain")
    if v and v.kind == "no_data":
        return v.say          # the user-facing refusal

    python scripts/answerability.py          # print the registry
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

NO_DATA = "no_data"
NOT_IMPLEMENTED = "not_implemented"
APPROXIMATE = "approximate"


@dataclass(frozen=True)
class Verdict:
    concept: str
    kind: str
    why: str          # for logs and for developers
    say: str          # verbatim user-facing text
    nearest: str | None = None      # the closest answerable alternative, if any
    questions: tuple = ()           # test-set questions this blocks
    #: Phrases that identify this concept in free text. The parser's model names concepts
    #: in its own words ("captain (player role attribute)", "offside call"), never as
    #: registry keys, so an exact-key lookup silently matches nothing.
    synonyms: tuple = ()


#: Concepts no source in this project contains. These do not become answerable by building
#: more retrieval - they need a different dataset, or they are not in any dataset at all.
NO_DATA_GAPS = {
    "captain": Verdict(
        concept="captain",
        kind=NO_DATA,
        why="_match.json's player_role holds a POSITION (RW, GK, SUB), never an armband "
            "flag, and no dynamic-events column encodes captaincy. Not derivable.",
        say="I can't answer that — I don't know who the captain is. SkillCorner's data "
            "records each player's position but never who wore the armband, so there's "
            "nothing for me to filter on.",
        nearest="Ask for a named player or a shirt number instead, and I can answer the "
                "same question exactly.",
        questions=(5, 78),
        synonyms=("captain", "armband", "skipper"),
    ),
    "offside_call": Verdict(
        concept="offside_call",
        kind=NO_DATA,
        why="An offside CALL is a referee decision, and there is no referee-event feed. "
            "Tracking can show a player beyond the second-last defender at the moment of "
            "a pass, which is an offside POSITION - a different and much more common thing.",
        say="I can't answer that — offside calls are referee decisions, and I have no "
            "record of them. I'd be guessing, and I'd guess wrong often: most players in "
            "an offside position are never flagged.",
        nearest="I can show players in an offside position at the moment of a pass. That's "
                "a different question — it includes plenty of moments where play "
                "continued — but it may be what you're after.",
        questions=(27,),
        synonyms=("offside",),
    ),
}

#: Concepts the data supports but the retrieval for has not been built yet. These are
#: promises the app can keep later, so say so rather than implying a permanent gap.
NOT_IMPLEMENTED_GAPS: dict = {}
"""Currently empty: every question the data can support has been built. Entries belong here
when retrieval lags the data, and must say "yet" - that is a different promise from NO_DATA."""


#: Concepts answerable only through a documented proxy. Answering is fine; answering
#: WITHOUT saying so is not. The `say` text is a disclosure to attach to the results, not
#: a refusal.
APPROXIMATE_CONCEPTS = {
    "through_ball": Verdict(
        concept="through_ball", kind=APPROXIMATE,
        why="No through-ball tag exists; approximated as a line-breaking pass.",
        say="There's no 'through ball' tag in the data, so these are line-breaking passes "
            "from the final third — close, but broader than a true through ball.",
        questions=(25,),
        synonyms=("through ball", "throughball", "through pass")),
    "cutback": Verdict(
        concept="cutback", kind=APPROXIMATE,
        why="No cutback tag; approximated as a pass reception inside the box without "
            "confirming the pass came from wide and behind the defence.",
        say="These are receptions in the box from a pass. I can't confirm the pass came "
            "from the byline, so some of these won't be true cutbacks.",
        questions=(18,),
        synonyms=("cutback", "cut back", "pull back", "pullback")),
    "game_management": Verdict(
        concept="game_management", kind=APPROXIMATE,
        why="No tag; approximated as late throw-in receptions and keep-possession events.",
        say="'Game management' isn't tagged, so these are slow restarts and keep-ball "
            "events after the 80th minute — a reasonable stand-in, not the concept itself.",
        questions=(48,),
        synonyms=("game management", "time wasting", "timewasting", "killing the game")),
    "duel_won": Verdict(
        concept="duel_won", kind=APPROXIMATE,
        why="No duel outcome; approximated as a pressing chain that ended in a regain.",
        say="There's no duel win/loss flag, so 'won' here means the pressing sequence ended "
            "with the ball recovered.",
        questions=(62,),
        synonyms=("duel won", "won the duel", "won a duel", "duel win")),
    "numerical_advantage": Verdict(
        concept="numerical_advantage", kind=APPROXIMATE,
        why="Approximated as zero opponents ahead of the ball at possession end.",
        say="'Numerical advantage' is approximated as having no opponents goalside at the "
            "end of the possession — it doesn't count the full attacking overload.",
        questions=(67, 74),
        synonyms=("numerical advantage", "numerical superiority", "overload", "outnumber", "numbers up")),
}

ALL_GAPS = {**NO_DATA_GAPS, **NOT_IMPLEMENTED_GAPS, **APPROXIMATE_CONCEPTS}


def _normalise(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip() + " "


def check(concept: str) -> Verdict | None:
    """The verdict for a concept, or None when it is answerable exactly.

    Accepts free text. The first real-model evaluation failed all three refusals because
    this used to be an exact key lookup: the model wrote "captain (player role attribute)"
    and "offside call", which recognised the concepts correctly and matched no key. Matching
    is on whole words, so "offside" matches "offside call" but not "offsides_ratio".
    """
    if concept in ALL_GAPS:
        return ALL_GAPS[concept]
    text = _normalise(concept)
    for verdict in ALL_GAPS.values():
        phrases = (verdict.concept.replace("_", " "),) + tuple(verdict.synonyms)
        if any(_normalise(ph) in text for ph in phrases):
            return verdict
    return None


def blocked_questions() -> dict:
    """{question_id: Verdict} for every test-set question a gap blocks."""
    return {q: v for v in ALL_GAPS.values() for q in v.questions}


def format_refusal(v: Verdict) -> str:
    """The full user-facing message for a concept that cannot be answered."""
    if v.kind == APPROXIMATE:
        raise ValueError(f"{v.concept} is answerable; disclose `say`, do not refuse")
    return v.say + (f"\n\n{v.nearest}" if v.nearest else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concept", help="show the verdict for one concept")
    args = ap.parse_args()

    if args.concept:
        v = check(args.concept)
        print(f"{args.concept}: answerable exactly" if v is None
              else f"[{v.kind}] {v.concept}\n\n{v.say}"
                   + (f"\n\n{v.nearest}" if v.nearest else ""))
        return

    for title, group in [("NO DATA - refuse, permanently", NO_DATA_GAPS),
                         ("NOT IMPLEMENTED - refuse, for now", NOT_IMPLEMENTED_GAPS),
                         ("APPROXIMATE - answer, but disclose", APPROXIMATE_CONCEPTS)]:
        print(f"\n=== {title} ===")
        for v in group.values():
            qs = ", ".join(f"Q{q}" for q in v.questions)
            print(f"\n  {v.concept}  ({qs})")
            print(f"    why : {v.why}")
            print(f"    say : {v.say}")
            if v.nearest:
                print(f"    alt : {v.nearest}")


if __name__ == "__main__":
    main()
