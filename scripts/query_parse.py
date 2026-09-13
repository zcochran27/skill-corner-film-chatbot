#!/usr/bin/env python
"""
Stage 2: turn a coach's question into the 8-gate structured query.

Design follows CLAUDE.md: a step-by-step chain rather than one extraction call, because
accuracy and debuggability matter more than latency here. Every gate is OPTIONAL and
self-reporting - it first decides whether its dimension is present at all, and only extracts
when it is. Most real questions touch two to four of the eight, so a single call that is
asked to fill in all eight at once invents the rest.

GATE ORDERING (CLAUDE.md listed this as undecided; this is the decision and the reason)

  Shape       1. negative/absence   inverts the whole query - an anti-join, not a filter,
                                    so it changes the query's structure and must be known
                                    before anything is built
              2. sequence           event-level or chain-level grain
  Subject     3. event type         determines which COLUMNS EXIST downstream: 168 of 354
                                    columns are populated on exactly one event_type, so
                                    every later gate's field vocabulary depends on this
              4. player/role
  Conditions  5. spatial            may additionally flag phase 2 (tracking)
              6. temporal/game-state
              7. comparative        needs the subject, hence after gate 4
              8. outcome/result     "led to a shot" is about the sequence, hence after 2

Shape before subject before conditions. The two hard dependencies are event type before
every field-emitting gate, and negation before everything, since it decides whether the
result of the rest is the answer or the thing being anti-joined against.

TWO GUARDRAILS, both required, both from the project's own scar tissue:

  answerability.check()  Refuse rather than silently substituting a proxy for a concept the
                         data does not contain (docs/answerability.md).
  validate_filter()      Reject a filter aimed at a column that is null for the event type
                         it targets. That returns zero rows rather than an error, and an
                         empty result reads as a finding - the failure mode behind five
                         separate bugs here (docs/master_plan.md 4a).

Running it
    cp .env.example .env                    # then set ANTHROPIC_API_KEY (gitignored)
    python scripts/query_parse.py "show me every shot from outside the box"
    python scripts/query_parse.py --offline "..."   # rule-based stub, no API key needed
    python scripts/query_parse.py --test-set        # parse all 80 test-set questions
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from answerability import check as answerability_check  # noqa: E402
from env import effort as env_effort, require_api_key, status as env_status  # noqa: E402
from query_schema import FilterError, card_for, validate_filter  # noqa: E402

MODEL = "claude-opus-5"
MAX_TOKENS = 8000

GATES = ["negative", "sequence", "event_type", "player",
         "spatial", "temporal", "comparative", "outcome"]

GATE_QUESTION = {
    "negative": "Is the coach asking about something that did NOT happen - an absence, a "
                "failure to act, or a missing response? Phrases like 'no one', 'without', "
                "'failed to', 'didn't'. A question that merely mentions a negative outcome "
                "(a loss, a miss) is NOT an absence question.",
    "sequence": "Is the coach asking about a SEQUENCE or build-up (several connected "
                "actions) rather than a single isolated action? 'Show me the goal' implies "
                "the build-up. 'Show me every header' does not.",
    "event_type": "What kind of on-pitch action is being asked about?",
    "player": "Does the question name a specific player, shirt number, or positional role "
              "('their left winger', 'the back four')? A question about the team as a whole "
              "does not.",
    "spatial": "Does the question constrain WHERE on the pitch this happened?",
    "temporal": "Does the question constrain WHEN - a minute range, a period, the score "
                "state, or a window relative to a goal?",
    "comparative": "Is this about a RELATIONSHIP between two players or units - a matchup, "
                   "an isolation, a duel - rather than one player in isolation?",
    "outcome": "Does the coach care what the action LED TO (a shot, a goal, a regain), as "
               "opposed to just the action itself?",
}

#: Concepts a gate may name so answerability can refuse before any filter is emitted.
KNOWN_CONCEPTS = ("captain", "offside_call", "through_ball", "cutback", "game_management",
                  "duel_won", "numerical_advantage", "dragged_out_of_position", "foot_race")

GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "applies": {"type": "boolean"},
        "reasoning": {"type": "string"},
        "concepts": {"type": "array", "items": {"type": "string"}},
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "column": {"type": "string"},
                    "op": {"type": "string",
                           "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "in", "notnull"]},
                    "value": {"type": ["string", "number", "boolean", "array", "null"]},
                },
                "required": ["column", "op", "value"],
                "additionalProperties": False,
            },
        },
        "grain": {"type": "string", "enum": ["event", "chain", "unspecified"]},
        "needs_tracking": {"type": "boolean"},
    },
    "required": ["applies", "reasoning", "concepts", "filters", "grain", "needs_tracking"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------------------
@dataclass
class GateResult:
    gate: str
    applies: bool
    reasoning: str = ""
    concepts: list = field(default_factory=list)
    filters: list = field(default_factory=list)
    grain: str = "unspecified"
    needs_tracking: bool = False
    rejected: list = field(default_factory=list)   # filters validate_filter() threw out


@dataclass
class ParsedQuery:
    question: str
    gates: list = field(default_factory=list)
    refusal: str | None = None          # set when a concept is unanswerable
    refusal_kind: str | None = None
    disclosures: list = field(default_factory=list)
    negated: bool = False
    grain: str = "event"
    event_types: list = field(default_factory=list)
    needs_tracking: bool = False

    @property
    def answerable(self) -> bool:
        return self.refusal is None

    @property
    def filters(self) -> list:
        return [f for g in self.gates for f in g.filters]

    def summary(self) -> str:
        if not self.answerable:
            return f"REFUSED ({self.refusal_kind}): {self.refusal}"
        applied = [g.gate for g in self.gates if g.applies]
        bits = [f"grain={self.grain}", f"gates={'+'.join(applied) or 'none'}"]
        if self.negated:
            bits.append("NEGATED (anti-join)")
        if self.needs_tracking:
            bits.append("phase-2 tracking")
        if self.event_types:
            bits.append(f"event_types={','.join(self.event_types)}")
        return " | ".join(bits)


# --------------------------------------------------------------------------------------
# LLM clients
# --------------------------------------------------------------------------------------
class AnthropicGateClient:
    """One structured-output call per gate against the Messages API.

    The system prompt (instructions + that gate's field card) is stable across every
    question, so it carries the cache breakpoint; only the question varies. That is the
    prefix-stability rule from the caching guidance: stable content first, volatile last.
    """

    def __init__(self, model: str = MODEL, effort: str | None = None):
        try:
            import anthropic
        except ImportError as exc:                      # pragma: no cover
            raise RuntimeError("pip install anthropic") from exc
        # Loads the gitignored .env if present; an exported variable still wins.
        require_api_key("the query-parsing chain")
        self._client = anthropic.Anthropic()
        self.model = model
        self.effort = effort or env_effort()
        #: One record per API call, for cost accounting. Tokens only - never content.
        self.usage_log: list[dict] = []

    def run_gate(self, gate: str, question: str) -> dict:
        system = [{
            "type": "text",
            "text": _gate_system_prompt(gate),
            "cache_control": {"type": "ephemeral"},
        }]
        kwargs = dict(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": GATE_SCHEMA}},
            messages=[{"role": "user", "content": f"Coach's question: {question}"}],
        )
        if self.effort:
            kwargs["output_config"]["effort"] = self.effort
        import time
        t0 = time.perf_counter()
        resp = self._client.messages.create(**kwargs)
        u = resp.usage
        self.usage_log.append(dict(
            gate=gate,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            seconds=time.perf_counter() - t0,
            stop_reason=resp.stop_reason,
        ))
        if resp.stop_reason == "refusal":                # guard before reading content
            raise RuntimeError(f"model refused: {getattr(resp, 'stop_details', None)}")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)


class OfflineGateClient:
    """Keyword stub so the chain, the guardrails and the trace can be exercised without an
    API key. It is deliberately crude - it exists to test the plumbing, not to parse
    football. Anything it gets wrong is not evidence about the real parser."""

    PATTERNS = {
        "negative": r"\bno\b|\bnobody\b|\bwithout\b|failed to|did ?n[o']t|never|no one",
        "sequence": r"sequence|build[- ]?up|chain|led to|leading to|ended in|started (?:from|with)",
        "player": r"winger|back|striker|midfield|keeper|captain|number \d+|pivot|fullback|forward",
        "spatial": r"box|third|channel|wide|half[- ]?space|near post|byline|penalty area|inside|outside",
        "temporal": r"minute|half|after|before|when (?:leading|losing|drawing)|score|\d+th",
        "comparative": r"one[- ]on[- ]one|1v1|isolat|against|matchup|duel|versus|\bvs\b|race",
        "outcome": r"led to|resulted in|ended in|shot|goal|regain",
    }

    def run_gate(self, gate: str, question: str) -> dict:
        q = question.lower()
        if gate == "event_type":
            types = []
            if re.search(r"pass|cross|ball played", q):
                types.append("player_possession")
            if re.search(r"run|sprint|movement", q):
                types.append("off_ball_run")
            if re.search(r"press|engage|tackle|defend", q):
                types.append("on_ball_engagement")
            types = types or ["player_possession"]
            return dict(applies=True, reasoning="offline keyword stub",
                        concepts=[], grain="unspecified", needs_tracking=False,
                        filters=[{"column": "event_type", "op": "in", "value": types}])
        applies = bool(re.search(self.PATTERNS.get(gate, r"(?!)"), q))
        concepts = [c for c in KNOWN_CONCEPTS if c.replace("_", " ") in q]
        if "captain" in q:
            concepts.append("captain")
        if "offside" in q:
            concepts.append("offside_call")
        return dict(applies=applies, reasoning="offline keyword stub",
                    concepts=sorted(set(concepts)), filters=[],
                    grain="chain" if gate == "sequence" and applies else "unspecified",
                    needs_tracking=False)


def _gate_system_prompt(gate: str) -> str:
    card = card_for(gate) if gate in ("event_type", "player", "spatial", "temporal",
                                      "sequence", "comparative", "outcome") else ""
    return f"""You are one gate in a chain that converts a football coach's question into a
structured database query over SkillCorner tracking-derived event data.

YOUR GATE: {gate}
YOUR QUESTION: {GATE_QUESTION[gate]}

Answer in two steps.

1. Decide whether your dimension is present in the coach's question AT ALL. Most questions
   touch only two to four of the eight gates. If yours is not present, set applies=false and
   emit NO filters. Inventing a condition the coach did not ask for is worse than omitting
   one - it silently narrows their results.

2. Only if it applies, emit filters using ONLY the columns and values listed below. Never
   invent a column name. If the question needs something not in the list, name it in
   `concepts` and leave `filters` empty - a later stage decides whether that is answerable.

Set `needs_tracking` true only if answering genuinely requires per-frame player geometry
(team shape, two-player races, marking) that no listed column expresses.

{card}

Reply with JSON matching the provided schema. `reasoning` should be one short sentence.
"""


# --------------------------------------------------------------------------------------
def parse(question: str, client=None, verbose: bool = False) -> ParsedQuery:
    """Run the gate chain over one question."""
    client = client or OfflineGateClient()
    out = ParsedQuery(question=question)

    for gate in GATES:
        try:
            raw = client.run_gate(gate, question)
        except Exception as exc:                       # a gate failure is not a "no"
            out.gates.append(GateResult(gate=gate, applies=False,
                                        reasoning=f"gate error: {type(exc).__name__}: {exc}"))
            if verbose:
                print(f"  [{gate}] ERROR {exc}", file=sys.stderr)
            continue

        res = GateResult(
            gate=gate,
            applies=bool(raw.get("applies")),
            reasoning=raw.get("reasoning", ""),
            concepts=list(raw.get("concepts") or []),
            grain=raw.get("grain", "unspecified"),
            needs_tracking=bool(raw.get("needs_tracking")),
        )

        # Answerability BEFORE any filter is emitted, so an unanswerable concept short
        # circuits the chain instead of being quietly approximated.
        for concept in res.concepts:
            verdict = answerability_check(concept)
            if verdict is None:
                continue
            if verdict.kind in ("no_data", "not_implemented"):
                out.refusal = verdict.say + (
                    f"\n\n{verdict.nearest}" if verdict.nearest else "")
                out.refusal_kind = verdict.kind
                out.gates.append(res)
                if verbose:
                    print(f"  [{gate}] REFUSED on concept {concept!r}", file=sys.stderr)
                return out
            out.disclosures.append(verdict.say)

        # Every filter is validated against the event types in play. A column that cannot
        # be populated there would return zero rows silently.
        targets = out.event_types or [None]
        for f in raw.get("filters") or []:
            col = f.get("column")
            try:
                for et in targets:
                    validate_filter(col, et, f.get("op"), f.get("value"))
                res.filters.append(f)
            except FilterError as e:
                res.rejected.append({"filter": f, "why": str(e)})
                if verbose:
                    print(f"  [{gate}] rejected {col!r}: {e}", file=sys.stderr)

        if gate == "negative" and res.applies:
            out.negated = True
        if gate == "sequence" and res.grain in ("event", "chain"):
            out.grain = res.grain
        if gate == "event_type":
            for f in res.filters:
                if f["column"] == "event_type":
                    v = f["value"]
                    out.event_types = list(v) if isinstance(v, list) else [v]
        out.needs_tracking = out.needs_tracking or res.needs_tracking
        out.gates.append(res)
        if verbose:
            mark = "YES" if res.applies else "no "
            print(f"  [{gate:11s}] {mark} {res.reasoning}", file=sys.stderr)

    return out


# --------------------------------------------------------------------------------------
def _make_client(offline: bool, effort: str | None):
    """Offline stub, or the real client - which exits with instructions if no key is set."""
    return OfflineGateClient() if offline else AnthropicGateClient(effort=effort)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?", help="a coach's question")
    ap.add_argument("--offline", action="store_true",
                    help="use the keyword stub instead of the API (no key needed)")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--test-set", action="store_true",
                    help="parse every question in coach_question_test_set.md")
    ap.add_argument("--json", action="store_true", help="emit the full trace as JSON")
    ap.add_argument("--status", action="store_true", help="report credential status and exit")
    args = ap.parse_args()

    if args.status:
        print(env_status())
        return

    client = _make_client(args.offline, args.effort)

    if args.test_set:
        text = (Path(__file__).resolve().parent.parent
                / "coach_question_test_set.md").read_text(encoding="utf-8")
        questions = re.findall(r"^\s*(\d+)\.\s+(.+)$", text, re.M)
        refused = 0
        for qid, q in questions:
            p = parse(q, client=client)
            if not p.answerable:
                refused += 1
            print(f"Q{qid:>2} {p.summary()}")
        print(f"\n{len(questions)} questions | {refused} refused as unanswerable")
        return

    if not args.question:
        ap.error("give a question, or use --test-set")
    parsed = parse(args.question, client=client, verbose=True)
    print()
    if args.json:
        print(json.dumps(asdict(parsed), indent=2, default=str))
        return
    print(f"Q: {parsed.question}")
    print(f"   {parsed.summary()}")
    for f in parsed.filters:
        print(f"   filter: {f['column']} {f['op']} {f['value']!r}")
    for g in parsed.gates:
        for r in g.rejected:
            print(f"   REJECTED by guardrail [{g.gate}]: {r['why']}")
    for d in parsed.disclosures:
        print(f"   disclose: {d}")


if __name__ == "__main__":
    main()
