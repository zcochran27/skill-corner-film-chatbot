#!/usr/bin/env python
"""
Grade parsed queries by EXECUTING them, not by comparing column names.

Why this exists: the first stage 2 evaluation graded the parser on column recall - did it
reach for the same columns the hand-written query uses - and reported 71%. Executing the same
parses against Gold told a different story. Only 5 of 13 row-comparable questions returned
the right rows, one returned zero rows, and several matched every correct column but
still found a quarter of the answer or less:

  Q35  column recall 67%   executes to ZERO rows
  Q39  column recall 50%   finds 4% of the answer
  Q42  column recall 100%  finds 26% of the answer

Column recall cannot see that a correct column, correctly valued, is ANDed with another
gate's version of the same idea at a different grain. Q42's sequence gate emitted exactly
the right chain-level filters, and the event_type gate independently added
`end_type in [direct_regain, indirect_regain]`, which keeps only the single engagement that
won the ball rather than every engagement in a chain that ended in a regain. Every column
was valid. The answer was quietly cut to a quarter.

That is this project's dominant failure mode - plausible output, no error, wrong answer - and
only execution catches it, so execution is the headline metric here.

Verdicts per question
  equivalent      finds >=90% of the true rows, and no more than 1.5x as many in total
  superset        finds >=90%, but buried in extra rows (too broad)
  partial         finds 50-90%
  misses          finds under 50%
  SILENT ZERO     returns nothing where the truth has rows

Questions answered by tracking predicates, anti-joins or phase-level tables have no single
event-row expression and are reported as not comparable rather than guessed at.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "gold"
GROUND_TRUTH = Path(__file__).resolve().parent / "test_all_questions.py"


def _b(col):
    return col.astype(str).str.lower().eq("true")


@lru_cache(maxsize=1)
def _events() -> pd.DataFrame:
    return pd.read_parquet(GOLD / "events_enriched.parquet")


@lru_cache(maxsize=1)
def _namespace() -> dict:
    ev = _events()
    return dict(pd=pd, _b=_b, events=ev,
                PP=ev[ev.event_type == "player_possession"],
                OBR=ev[ev.event_type == "off_ball_run"],
                OBE=ev[ev.event_type == "on_ball_engagement"],
                PO=ev[ev.event_type == "passing_option"],
                WINGERS=["LW", "RW"], CB=["CB", "LCB", "RCB"],
                FB=["LB", "RB", "LWB", "RWB"], PIVOT=["DM", "LDM", "RDM"])


@lru_cache(maxsize=1)
def truth_expressions() -> dict:
    """{question: pandas expression} for every question with a single event-row query.

    The source is split into one block per question BEFORE matching, so a lazy regex can never
    run past a question that delegates to a function and pick up the next question's lambda -
    which is exactly the bug an earlier draft of this had (Q59 was graded against Q60's query).
    """
    src = GROUND_TRUTH.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"\n    (?:add\(|def q\d+\()", src)] + [len(src)]
    blocks = [src[a:b] for a, b in zip(starts, starts[1:])]
    funcs = {m.group(1): b for b in blocks if (m := re.match(r"\s*def (q\d+)\(\):", b))}
    out = {}
    for b in blocks:
        m = re.match(r"\s*add\(\s*(\d+)", b)
        if not m:
            continue
        q = int(m.group(1))
        lam = re.search(r"lambda:(.*)\)\s*$", b, re.S)
        if lam:
            out[q] = lam.group(1).strip()
            continue
        ref = re.search(r"(q\d+)\)\s*$", b)
        body = funcs.get(ref.group(1), "") if ref else ""
        # The function must BE its return expression. q72 filters on one line and returns on
        # the next, so taking only the return line silently dropped its first condition;
        # q54 returns an aggregate, not rows. Skip both rather than grade against a fragment.
        lines = re.sub(r'"""[\s\S]*?"""', "", body).splitlines()
        # Blocks begin with a newline, so a fixed [1:] still included the def line - which
        # made every single-return function look non-comparable and silently dropped Q35's
        # SILENT ZERO from the tally. Start after the def line itself.
        start = next((i for i, ln in enumerate(lines) if re.match(r"\s*def q\d+\(", ln)), -1)
        code = [ln.strip() for ln in lines[start + 1:]
                if ln.strip() and not ln.strip().startswith("#")]
        if (code and code[0].startswith("return ")
                and len(re.findall(r"return ", body)) == 1
                and not any(k in body for k in ("evaluate(", "merge(", "phase_shape",
                                                "groupby("))):
            # the block holds only this function, so everything after `return` is the
            # expression - multi-line returns (q35 spans three lines) included
            out[q] = body.split("return ", 1)[1].strip()
    return out


def _apply(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    c, op, v = f["column"], f["op"], f["value"]
    if c not in df.columns:
        return df.iloc[0:0]
    s = df[c]
    low = s.astype(str).str.lower()
    if op == "eq":
        return df[low == str(v).lower()]
    if op == "ne":
        return df[low != str(v).lower()]
    if op == "in":
        vals = v if isinstance(v, list) else [v]
        return df[low.isin([str(x).lower() for x in vals])]
    if op == "notnull":
        return df[s.notna()]
    num = pd.to_numeric(s, errors="coerce")
    return df[{"gt": num > v, "gte": num >= v, "lt": num < v, "lte": num <= v}[op]]


def execute(filters: list[dict]) -> pd.DataFrame:
    """The rows a parsed query returns against Gold."""
    ev = _events()
    ets = []
    for f in filters:
        if f["column"] == "event_type":
            ets = f["value"] if isinstance(f["value"], list) else [f["value"]]
    df = ev[ev.event_type.isin(ets)] if ets else ev
    for f in filters:
        if f["column"] != "event_type":
            df = _apply(df, f)
    return df


def grade_one(qid: int, filters: list[dict]) -> dict | None:
    """Execution verdict for one parsed question, or None if it is not row-comparable."""
    expr = truth_expressions().get(qid)
    if expr is None:
        return None
    truth = eval(expr, _namespace())  # noqa: S307 - our own hand-written test queries
    got = execute(filters)
    t, p = set(truth.index), set(got.index)
    inter = len(t & p)
    recall = inter / len(t) if t else float("nan")
    if not p and t:
        verdict = "SILENT ZERO"
    elif recall >= 0.9 and len(p) <= 1.5 * len(t):
        verdict = "equivalent"
    elif recall >= 0.9:
        verdict = "superset"
    elif recall >= 0.5:
        verdict = "partial"
    else:
        verdict = "misses"
    return dict(truth_rows=len(t), parser_rows=len(p), recall=recall,
                jaccard=inter / len(t | p) if (t | p) else float("nan"),
                blowup=(len(p) / len(t)) if t else float("nan"), verdict=verdict)
