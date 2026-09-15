#!/usr/bin/env python
"""
Measure what the query-parsing chain actually costs, from real API usage.

Output tokens cannot be computed from input: adaptive thinking decides per call how much
to reason, so any estimate made without running the model is a guess. This runs a small
calibration sample, records real usage per call (tokens only, never content), and
extrapolates to a full run.

Caching is measured, not assumed. The per-gate system prompt carries a cache breakpoint,
but a prefix below the model's minimum cacheable size silently never caches - no error,
just cache_creation_input_tokens = 0 - so the report shows what actually happened.

Usage:
    python scripts/parse_cost.py --calibrate 1,23,70     # run these test-set questions
    python scripts/parse_cost.py --report                # re-read the last calibration
    python scripts/parse_cost.py --calibrate 1,23,70 --provider gemini   # own output file

Gemini calls are costed at $0: this project uses Gemini only on the free tier, which is not
billed. Their tokens and timings are still real.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gold" / "parse_calibration.json"
TEST_SET = ROOT / "coach_question_test_set.md"


def out_path(provider: str) -> Path:
    """Each provider saves to its own file, so a Gemini run never overwrites a Claude one."""
    return OUT if provider == "anthropic" else OUT.with_stem(f"{OUT.stem}_{provider}")

#: USD per million tokens for claude-opus-5 (first-party API rates).
PRICE = {"input": 5.00, "output": 25.00}
#: Cache pricing multipliers on the input rate (5-minute TTL).
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10


def load_questions() -> dict:
    text = TEST_SET.read_text(encoding="utf-8")
    return {int(q): t for q, t in re.findall(r"^\s*(\d+)\.\s+(.+)$", text, re.M)}


def call_cost(r: dict) -> float:
    """Cost of one call. input_tokens excludes cached tokens; cache writes and reads are
    billed separately at their own multipliers."""
    if r.get("provider") == "gemini":                  # free tier: unbilled
        return 0.0
    per = PRICE["input"] / 1e6
    return (r["input_tokens"] * per
            + r["cache_creation_input_tokens"] * per * CACHE_WRITE_MULT
            + r["cache_read_input_tokens"] * per * CACHE_READ_MULT
            + r["output_tokens"] * PRICE["output"] / 1e6)


def calibrate(qids: list[int], provider: str) -> dict:
    from query_parse import make_client, parse

    questions = load_questions()
    client = make_client(provider)
    per_question = []
    for qid in qids:
        before = len(client.usage_log)
        t0 = time.perf_counter()
        parsed = parse(questions[qid], client=client)
        calls = client.usage_log[before:]
        per_question.append(dict(
            qid=qid, question=questions[qid], seconds=time.perf_counter() - t0,
            calls=calls, refused=not parsed.answerable,
            gates_applied=[g.gate for g in parsed.gates if g.applies],
            rejected=[r["why"] for g in parsed.gates for r in g.rejected]))
        spent = sum(call_cost(c) for c in calls)
        print(f"  Q{qid}: {len(calls)} calls, {per_question[-1]['seconds']:.0f}s, "
              f"${spent:.4f}", flush=True)
    result = dict(model=client.model, effort=client.effort or "default",
                  per_question=per_question)
    out = out_path(provider)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def report(result: dict, full_n: int = 80) -> None:
    calls = [c for q in result["per_question"] for c in q["calls"]]
    if not calls:
        print("no calls recorded")
        return
    nq = len(result["per_question"])

    def tot(k):
        return sum(c[k] for c in calls)

    spent = sum(call_cost(c) for c in calls)
    print(f"\n=== CALIBRATION: {nq} questions, {len(calls)} calls, "
          f"model {result['model']}, effort {result['effort']} ===")
    print(f"input (uncached)     {tot('input_tokens'):>9,}")
    print(f"cache write          {tot('cache_creation_input_tokens'):>9,}")
    print(f"cache read           {tot('cache_read_input_tokens'):>9,}")
    print(f"output (incl. think) {tot('output_tokens'):>9,}")
    print(f"actual spend         ${spent:.4f}")

    print("\nper gate (mean over calls):")
    print(f"  {'gate':12s} {'in':>6s} {'c.write':>8s} {'c.read':>7s} {'out':>6s} "
          f"{'sec':>5s} {'$/call':>8s}")
    gates = sorted({c["gate"] for c in calls},
                   key=lambda g: [c["gate"] for c in calls].index(g))
    for g in gates:
        cs = [c for c in calls if c["gate"] == g]
        n = len(cs)
        print(f"  {g:12s} {sum(c['input_tokens'] for c in cs)/n:>6.0f} "
              f"{sum(c['cache_creation_input_tokens'] for c in cs)/n:>8.0f} "
              f"{sum(c['cache_read_input_tokens'] for c in cs)/n:>7.0f} "
              f"{sum(c['output_tokens'] for c in cs)/n:>6.0f} "
              f"{sum(c['seconds'] for c in cs)/n:>5.1f} "
              f"${sum(call_cost(c) for c in cs)/n:>7.4f}")

    # ---- extrapolation ---------------------------------------------------------------
    # The first question pays cache WRITES; every later question reads. So the steady-state
    # per-question cost is taken from questions after the first, not the calibration mean.
    warm = [q for q in result["per_question"][1:]] or result["per_question"]
    warm_calls = [c for q in warm for c in q["calls"]]
    warm_cost_q = sum(call_cost(c) for c in warm_calls) / len(warm)
    first_cost_q = sum(call_cost(c) for c in result["per_question"][0]["calls"])
    sec_q = sum(q["seconds"] for q in result["per_question"]) / nq
    out_q = sum(c["output_tokens"] for c in warm_calls) / len(warm)

    projected = first_cost_q + warm_cost_q * (full_n - 1)
    # Cold-cache counterfactual: what the run costs if caching never engaged.
    no_cache = sum(
        (c["input_tokens"] + c["cache_creation_input_tokens"] + c["cache_read_input_tokens"])
        * PRICE["input"] / 1e6 + c["output_tokens"] * PRICE["output"] / 1e6
        for c in warm_calls) / len(warm) * full_n

    out_share = (out_q * PRICE["output"] / 1e6) / warm_cost_q if warm_cost_q else 0
    print(f"\n=== PROJECTION: full run of {full_n} questions ({full_n * 8} calls) ===")
    print(f"first question (cache writes)   ${first_cost_q:.4f}")
    print(f"each later question (warm)      ${warm_cost_q:.4f}")
    print(f"projected total                 ${projected:.2f}")
    print(f"same run if caching never hit   ${no_cache:.2f}")
    print(f"output tokens share of spend    {out_share:.0%}")
    print(f"wall clock (sequential)         {sec_q * full_n / 60:.0f} min "
          f"(~{sec_q:.0f}s per question)")
    print("\nnote: calibration is a small sample - output tokens vary with question "
          "complexity, so treat the total as +/- 30%.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", help="comma-separated test-set question ids")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--full-n", type=int, default=80)
    ap.add_argument("--provider", choices=["anthropic", "gemini"],
                    help="default: LLM_PROVIDER in .env, else anthropic")
    args = ap.parse_args()

    from env import provider as env_provider, require_api_key
    provider = env_provider(args.provider)
    if args.calibrate:
        require_api_key("calibration", provider)
        ids = [int(x) for x in args.calibrate.split(",")]
        print(f"calibrating on {len(ids)} questions ({len(ids) * 8} API calls) ...")
        report(calibrate(ids, provider), args.full_n)
    elif args.report:
        report(json.loads(out_path(provider).read_text(encoding="utf-8")), args.full_n)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
