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
  zero-row check  Parses the chain itself flagged as returning no rows. Every SILENT ZERO
                  the grader finds should already be flagged here; one that is not means
                  the check has a hole.

Usage:
    python scripts/parse_eval.py --sample          # run + grade the 20-question sample
    python scripts/parse_eval.py --ids 1,5,23      # run + grade specific questions
    python scripts/parse_eval.py --regrade         # re-grade saved parses, no API calls
    python scripts/parse_eval.py --regrade --file data/gold/parse_eval_run1.json
    python scripts/parse_eval.py --sample --provider gemini   # saves to parse_eval_gemini.json
    python scripts/parse_eval.py --all --resume    # all 80; skip questions already saved

--resume keeps the saved parses and only parses questions not yet in the file, so a run cut
off by a quota picks up where it stopped. It refuses to mix models in one file.
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


def out_path(provider: str) -> Path:
    """Each provider saves to its own file, so a Gemini run never overwrites a Claude one."""
    return OUT if provider == "anthropic" else OUT.with_stem(f"{OUT.stem}_{provider}")

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


def run(ids: list[int], provider: str, max_spend: float | None = None,
        resume: bool = False) -> list[dict]:
    from query_parse import GateClientFatal, make_client, parse

    questions = load_questions()
    client = make_client(provider)
    out = out_path(provider)
    records = []
    if resume and out.exists():
        records = json.loads(out.read_text(encoding="utf-8"))
        saved = {c["model"] for r in records for c in r["usage"] if c.get("model")}
        if saved - {client.model}:
            sys.exit(f"{out.name} holds parses from {sorted(saved)}, not {client.model}; "
                     f"move it aside rather than mixing models in one run.")
        done = {r["qid"] for r in records}
        print(f"  resuming: {len(done & set(ids))} of {len(ids)} already saved in {out.name}")
        ids = [q for q in ids if q not in done]
    for n, qid in enumerate(ids, 1):
        before = len(client.usage_log)
        t0 = time.perf_counter()
        try:
            parsed = parse(questions[qid], client=client)
        except GateClientFatal as e:
            # Q{qid} is not saved; everything before it is. Grade what exists.
            print(f"  STOPPED at Q{qid}: {e}\n  {len(ids) - n + 1} questions not parsed; "
                  f"rerun with --resume to continue", flush=True)
            break
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
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        if max_spend is not None and total >= max_spend and n < len(ids):
            print(f"  STOPPED: ${total:.2f} reached the --max-spend cap of ${max_spend:.2f}; "
                  f"{len(ids) - n} questions not parsed", flush=True)
            break
    return records


def reapply_guardrails(record: dict):
    """Re-run today's chain logic over a saved parse's model replies.

    A saved parse holds two different things: what the MODEL said (concepts, filters) and
    what the GUARDRAILS decided about it (refusal, rejections, supersedes, proxies, the
    zero-row check) at the time. Only the first is worth paying for. Replaying the replies
    through the real parse() - not a copy of its logic, which can drift - means a guardrail
    fix can be graded against real model output without another API call.

    What replay cannot change is what the model saw: a run made before gates were shown the
    query so far stays a run without that context.
    """
    from query_parse import ReplayClient, parse

    return parse(record["question"], client=ReplayClient(record["parsed"]))


def grade(records: list[dict]) -> None:
    from parse_cost import call_cost

    gt = load_ground_truth()
    rows = []
    for r in records:
        qid, p = r["qid"], r["parsed"]
        truth = gt[qid]
        expect_refuse = truth["fidelity"] == "unresolved"
        live = reapply_guardrails(r)
        refused = not live.answerable
        kept = live.filters
        emitted = sorted({f["column"] for f in kept if f["column"] != "event_type"})
        truth_cols = [c for c in truth["columns"] if c != "event_type"]
        hit = sorted(set(emitted) & set(truth_cols))
        rejected = [x["why"] for g in live.gates for x in g.rejected]
        superseded = [f"{s['filter']['column']} ({g.gate} -> {s['by']})"
                      for g in live.gates for s in g.superseded]
        tracking_q = "tracking" in truth["fidelity"]
        rows.append(dict(
            qid=qid, category=truth["category"], fidelity=truth["fidelity"],
            expect_refuse=expect_refuse, refused=refused,
            refusal_ok=expect_refuse == refused,
            truth=truth_cols, emitted=emitted, hit=hit,
            recall=(len(hit) / len(truth_cols)) if truth_cols else None,
            precision=(len(hit) / len(emitted)) if emitted and truth_cols else None,
            tracking_q=tracking_q, flagged_tracking=live.needs_tracking,
            gates=[g.gate for g in live.gates if g.applies],
            rejected=rejected, superseded=superseded, disclosures=len(live.disclosures),
            warnings=live.warnings, kept=kept, rows=live.rows, empty_reason=live.empty_reason,
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
    answered = [x for x in rows if not x["refused"]]
    flagged = [x for x in answered if x["rows"] == 0]
    silent = {q for q, v in exec_rows if v["verdict"] == "SILENT ZERO"}
    unflagged = silent - {x["qid"] for x in flagged}
    print(f"zero-row check           {len(flagged)} of {len(answered)} answered queries "
          f"flagged EMPTY" + (f"; MISSED {sorted(unflagged)}" if unflagged else
                              "; every SILENT ZERO was caught"))
    sup = sum(len(x["superseded"]) for x in rows)
    if sup:
        print(f"superseded filters       {sup}")

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

    if flagged:
        print("\n--- flagged EMPTY by the zero-row check ---")
        for x in flagged:
            print(f"Q{x['qid']}: {x['empty_reason']}")
    if sup:
        print("\n--- superseded (a later gate replaced an earlier filter) ---")
        for x in rows:
            for s in x["superseded"]:
                print(f"Q{x['qid']}: {s}")
    warned = [x for x in rows if x["warnings"]]
    if warned:
        print("\n--- warnings ---")
        for x in warned:
            for w in x["warnings"]:
                print(f"Q{x['qid']}: {w}")

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
    ap.add_argument("--all", action="store_true", help="every test-set question")
    ap.add_argument("--ids", help="comma-separated question ids")
    ap.add_argument("--resume", action="store_true",
                    help="keep saved parses and only parse questions not yet saved")
    ap.add_argument("--regrade", action="store_true",
                    help="re-apply today's guardrails to saved model output; no API calls")
    ap.add_argument("--max-spend", type=float,
                    help="stop once the run has spent this many dollars")
    ap.add_argument("--file", type=Path,
                    help="saved parses to regrade (default: the provider's latest run)")
    ap.add_argument("--provider", choices=["anthropic", "gemini"],
                    help="default: LLM_PROVIDER in .env, else anthropic")
    args = ap.parse_args()

    from env import provider as env_provider, require_api_key
    provider = env_provider(args.provider)
    if args.regrade:
        grade(json.loads((args.file or out_path(provider)).read_text(encoding="utf-8")))
        return
    if args.all:
        ids = sorted(load_questions())
    elif args.sample:
        ids = SAMPLE
    else:
        ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    if not ids:
        ap.print_help()
        return
    require_api_key("the parse evaluation", provider)
    print(f"parsing {len(ids)} questions with {provider} -> {out_path(provider).name} ...")
    records = run(ids, provider, max_spend=args.max_spend, resume=args.resume)
    if records:
        grade(sorted(records, key=lambda r: r["qid"]))


if __name__ == "__main__":
    main()
