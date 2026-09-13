#!/usr/bin/env python
"""
Grade the query-parsing chain against the hand-written test-set queries.

Ground truth is scripts/test_all_questions.py: every question there has a query written by
hand and verified against the real data, and its `fields` argument names the columns that
query uses. That is what the parser's emitted filters are compared to.

Every parse is SAVED before grading, so the grading can be changed and re-run without
spending another cent on the API.

What is graded, and how far to trust each number
  refusal         Exact. Unresolved questions must refuse; everything else must not.
  column recall   Share of the ground-truth columns the parser reached for. The headline
                  metric, but a string match is only a proxy for meaning: the parser may use
                  a semantically equivalent column (opp_penalty_area_end for penalty_area_end
                  on "outside the box"). Misses are listed for a human to judge, not treated
                  as definitive failures.
  column precision Share of emitted columns that appear in the ground truth. Weaker still -
                  a hand-written query is one valid answer, not the only one - so read low
                  precision as "worth a look", not "wrong".
  tracking flag   For questions resolved by a tracking predicate, whether the parser set
                  needs_tracking. Those questions have no column ground truth at all.
  guardrail       Filters validate_filter() rejected. Any non-zero count means the model
                  reached for a column that could not have answered.

Usage:
    python scripts/parse_eval.py --sample          # run + grade the 20-question sample
    python scripts/parse_eval.py --ids 1,5,23      # run + grade specific questions
    python scripts/parse_eval.py --regrade         # re-grade saved parses, no API calls
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gold" / "parse_eval.json"
TEST_SET = ROOT / "coach_question_test_set.md"
GROUND_TRUTH = Path(__file__).resolve().parent / "test_all_questions.py"

#: Columns a hand-written query touches for bookkeeping rather than because the question asks
#: for them - e.g. Q68 scopes to `match_id.isin(TRACKED)`. Counting these as ground truth
#: would grade the parser on plumbing it has no reason to emit.
PLUMBING = {"match_id", "player_id", "team_id", "event_id", "frame_start", "frame_end"}

#: 20 questions spanning all 8 categories, weighted toward the cases most likely to break:
#: all 3 refusals, both kinds of tracking question, negatives, approximations and composites.
SAMPLE = [
    1, 5, 9,        # player-specific   (5 = captain, must refuse)
    12, 17, 21,     # spatial           (17 = phase-level team shape)
    23, 25, 27,     # event-type        (25 = approximate, 27 = offside, must refuse)
    35, 39, 42,     # sequence          (39 = 6-second window)
    49, 52,         # game-state        (49 = window after conceding)
    55, 59,         # comparative       (55 = approximate, 59 = tracking)
    66, 70,         # negative          (both tracking)
    75, 78,         # composite         (78 = captain, must refuse)
]


def load_questions() -> dict:
    text = TEST_SET.read_text(encoding="utf-8")
    return {int(q): t for q, t in re.findall(r"^\s*(\d+)\.\s+(.+)$", text, re.M)}


def load_ground_truth() -> dict:
    """Ground-truth columns per question, taken from what each hand-written query EXECUTES.

    This used to read the `fields` label on each add() call. An audit found 6 of 80 labels
    naming columns their own query never uses - Q25's label said event_subtype while its
    query filters first_line_break - so the parser was being graded against prose that had
    drifted from the code. The executable query cannot disagree with itself.

    Column references are the attribute accesses in the lambda, or in the qNN() function a
    question delegates to. Tracking-predicate questions reference no event columns and so
    have no column ground truth; they are graded on the tracking flag instead.
    """
    from query_schema import availability

    cols = set(availability())
    src = GROUND_TRUTH.read_text(encoding="utf-8")
    funcs = {m.group(1): m.group(2) for m in re.finditer(
        r"\n    def (q\d+)\(\):(.*?)(?=\n    (?:def |add\())", src, re.S)}
    out = {}
    for m in re.finditer(
            r'add\(\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]*)"(.*?)\)\n',
            src, re.S):
        qid, cat, fid, _label, tail = m.groups()
        lam = re.search(r"lambda:(.*)", tail, re.S)
        if lam:
            body = lam.group(1)
        else:
            ref = re.search(r"\b(q\d+)\s*$", tail.strip())
            body = funcs.get(ref.group(1), "") if ref else ""
        used = {t for t in re.findall(r"\.([a-z][a-z0-9_]+)", body)
                if t in cols and t not in PLUMBING}
        out[int(qid)] = dict(category=cat, fidelity=fid, columns=sorted(used))
    return out


def run(ids: list[int]) -> list[dict]:
    from query_parse import AnthropicGateClient, parse

    questions = load_questions()
    client = AnthropicGateClient()
    records = []
    for n, qid in enumerate(ids, 1):
        before = len(client.usage_log)
        t0 = time.perf_counter()
        parsed = parse(questions[qid], client=client)
        calls = client.usage_log[before:]
        records.append(dict(qid=qid, question=questions[qid],
                            seconds=round(time.perf_counter() - t0, 1),
                            parsed=asdict(parsed), usage=calls))
        from parse_cost import call_cost
        spent = sum(call_cost(c) for c in calls)
        total = sum(call_cost(c) for r in records for c in r["usage"])
        print(f"  [{n:>2}/{len(ids)}] Q{qid:<2} {len(calls)} calls "
              f"{records[-1]['seconds']:>5.0f}s  ${spent:.4f}  (running ${total:.2f})",
              flush=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    return records


def reapply_guardrails(p: dict) -> dict:
    """Re-run today's guardrails over a saved parse's raw model output.

    A saved parse holds two different things: what the MODEL said (concepts, filters) and
    what the GUARDRAILS decided about it (refusal, rejections) at the time. Only the first is
    worth paying for. Re-applying the current guardrails to it means a guardrail fix can be
    graded against real model output without another API call - which is how the refusal
    fix was verified at zero cost.
    """
    from answerability import check
    from query_schema import FilterError, validate_filter

    refused, kind, rejected, kept = None, None, [], []
    event_types: list = []
    for g in p["gates"]:
        for c in g["concepts"]:
            v = check(c)
            if v and v.kind in ("no_data", "not_implemented"):
                refused, kind = v.say, v.kind
                break
        if refused:
            break
        for f in g["filters"] + [x["filter"] for x in g.get("rejected", [])]:
            if f["column"] == "event_type":
                event_types = f["value"] if isinstance(f["value"], list) else [f["value"]]
        for f in g["filters"] + [x["filter"] for x in g.get("rejected", [])]:
            try:
                for et in (event_types or [None]):
                    validate_filter(f["column"], et, f.get("op"), f.get("value"))
                kept.append(f)
            except FilterError as e:
                rejected.append(str(e))
    return dict(refusal=refused, refusal_kind=kind, kept=kept, rejected=rejected)


def grade(records: list[dict]) -> None:
    from parse_cost import call_cost

    gt = load_ground_truth()
    rows = []
    for r in records:
        qid, p = r["qid"], r["parsed"]
        truth = gt[qid]
        expect_refuse = truth["fidelity"] == "unresolved"
        live = reapply_guardrails(p)
        refused = live["refusal"] is not None
        emitted = sorted({f["column"] for f in live["kept"] if f["column"] != "event_type"})
        truth_cols = [c for c in truth["columns"] if c != "event_type"]
        hit = sorted(set(emitted) & set(truth_cols))
        rejected = live["rejected"]
        tracking_q = "tracking" in truth["fidelity"]
        rows.append(dict(
            qid=qid, category=truth["category"], fidelity=truth["fidelity"],
            expect_refuse=expect_refuse, refused=refused,
            refusal_ok=expect_refuse == refused,
            truth=truth_cols, emitted=emitted, hit=hit,
            recall=(len(hit) / len(truth_cols)) if truth_cols else None,
            precision=(len(hit) / len(emitted)) if emitted and truth_cols else None,
            tracking_q=tracking_q, flagged_tracking=p["needs_tracking"],
            gates=[g["gate"] for g in p["gates"] if g["applies"]],
            rejected=rejected, disclosures=len(p["disclosures"]), kept=live["kept"],
            cost=sum(call_cost(c) for c in r["usage"]), seconds=r["seconds"]))

    n = len(rows)
    refusal_ok = sum(x["refusal_ok"] for x in rows)
    graded = [x for x in rows if x["recall"] is not None and not x["expect_refuse"]]
    trk = [x for x in rows if x["tracking_q"]]
    rej = sum(len(x["rejected"]) for x in rows)
    spend = sum(x["cost"] for x in rows)

    from parse_exec import grade_one

    exec_rows = []
    for x in rows:
        if x["expect_refuse"] or x["refused"]:
            continue
        v = grade_one(x["qid"], x["kept"])
        if v is not None:
            exec_rows.append((x["qid"], v))
    counts: dict = {}
    for _, v in exec_rows:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1

    print(f"\n{'=' * 78}\nPARSE ACCURACY - {n} questions, ${spend:.2f} spent\n{'=' * 78}")
    print(f"refusal correct          {refusal_ok}/{n}")
    if exec_rows:
        eq = counts.get("equivalent", 0)
        print(f"EXECUTED correctly       {eq}/{len(exec_rows)} row-comparable questions "
              f"return the right rows  <-- headline")
        order = ["equivalent", "superset", "partial", "misses", "SILENT ZERO"]
        print("   " + "  ".join(f"{k}={counts[k]}" for k in order if k in counts))
    if graded:
        mean_r = sum(x["recall"] for x in graded) / len(graded)
        prec = [x["precision"] for x in graded if x["precision"] is not None]
        full = sum(1 for x in graded if x["recall"] == 1.0)
        print(f"column recall (mean)     {mean_r:.0%}  over {len(graded)} column-graded "
              f"questions; {full} fully recalled  (OVERSTATES quality - see parse_exec.py)")
        if prec:
            print(f"column precision (mean)  {sum(prec) / len(prec):.0%}  "
                  f"(weak signal - see docstring)")
    if trk:
        print(f"tracking flagged         {sum(x['flagged_tracking'] for x in trk)}/{len(trk)} "
              f"tracking-resolved questions")
    print(f"guardrail rejections     {rej}")

    print(f"\n{'Q':>3} {'category':16s} {'ref':>4s} {'recall':>6s} {'trk':>4s} "
          f"{'rej':>3s}  gates / notes")
    for x in rows:
        ref = "ok" if x["refusal_ok"] else "BAD"
        rec = "-" if x["recall"] is None else f"{x['recall']:.0%}"
        trk_s = ("ok" if x["flagged_tracking"] else "miss") if x["tracking_q"] else ""
        print(f"{x['qid']:>3} {x['category'][:16]:16s} {ref:>4s} {rec:>6s} {trk_s:>4s} "
              f"{len(x['rejected']):>3d}  {'+'.join(x['gates']) or '(refused)'}")

    if exec_rows:
        print(f"\n--- executed against Gold ({len(exec_rows)} row-comparable) ---")
        print(f"{'Q':>3} {'truth':>6} {'parser':>7} {'recall':>7}  verdict")
        for q, v in exec_rows:
            extra = f"  ({v['blowup']:.0f}x the rows)" if v["verdict"] == "superset" else ""
            print(f"{q:>3} {v['truth_rows']:>6} {v['parser_rows']:>7} {v['recall']:>7.0%}  "
                  f"{v['verdict']}{extra}")

    misses = [x for x in graded if x["recall"] < 1.0]
    if misses:
        print(f"\n--- column misses, for a human to judge ({len(misses)}) ---")
        for x in misses:
            print(f"Q{x['qid']}: expected {x['truth']}")
            print(f"     emitted  {x['emitted'] or '(none)'}")
    bad_ref = [x for x in rows if not x["refusal_ok"]]
    if bad_ref:
        print("\n--- refusal errors ---")
        for x in bad_ref:
            print(f"Q{x['qid']}: expected refuse={x['expect_refuse']}, got {x['refused']}")
    if rej:
        print("\n--- guardrail rejections ---")
        for x in rows:
            for why in x["rejected"]:
                print(f"Q{x['qid']}: {why[:120]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help=f"the {len(SAMPLE)}-question sample")
    ap.add_argument("--ids", help="comma-separated question ids")
    ap.add_argument("--regrade", action="store_true",
                    help="re-apply today's guardrails to saved model output; no API calls")
    args = ap.parse_args()

    if args.regrade:
        grade(json.loads(OUT.read_text(encoding="utf-8")))
        return
    ids = SAMPLE if args.sample else ([int(x) for x in args.ids.split(",")] if args.ids
                                      else None)
    if not ids:
        ap.print_help()
        return
    from env import require_api_key
    require_api_key("the parse evaluation")
    print(f"parsing {len(ids)} questions ...")
    grade(run(ids))


if __name__ == "__main__":
    main()
