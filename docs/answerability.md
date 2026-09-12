# Answerability — what the chatbot must say when it cannot answer

**Mandate.** When a coach asks for something the data does not contain, the app says so.
It does not substitute the nearest available proxy and present it as the real thing.

Registry and user-facing text: `scripts/answerability.py`. Run it to see the current list.

---

## Why this is a hard rule, not a nicety

A coach acts on these clips. They take them into a meeting and say "here are their 20
offside calls." If those are really 20 moments a player *stood* in an offside position,
nothing in the output reveals the swap — the clips look right, the count looks plausible,
and the conclusion is wrong.

That is the response-layer twin of the failure mode that has bitten this project five
separate times (`docs/master_plan.md` §4a): **plausible output, no error, wrong
conclusion.** Every instance so far was caught by looking at a distribution or a
cross-check, never by the code failing. At the response layer there is no distribution to
look at — the only defence is refusing to substitute in the first place.

The same discipline already runs one layer down. Phase-2 predicates return
`insufficient_data` rather than `False` when a window cannot be measured, precisely because
the negative/absence gate (Q65–Q74) turns absence into a positive answer. "No defender
contested it" and "I couldn't see who contested it" are different answers, and only one of
them is a finding. This document extends that rule from a predicate to the whole response.

---

## Four response modes

Every answer carries its fidelity. The fidelity is not a footnote — it changes what the
chatbot says.

| Mode | When | Behaviour |
|---|---|---|
| **Exact** | A deterministic filter answers the question as asked | Answer plainly. 63 of 80 test-set questions. |
| **Approximate** | Answerable only through a documented proxy | Answer, **and disclose the proxy in the response**. 14 questions. |
| **Ranked** | The concept is a continuum with no natural cutoff | Return an ordering, and do not imply a filtered set. Q17, Q66, Q68. |
| **Unanswerable** | No data, or not built | **Refuse. Explain why. Offer the nearest answerable thing.** 3 questions, all `no_data`. |

Plus a per-result caveat that cuts across all four:

| **Insufficient data** | Some candidates could not be measured | Report as coverage ("8% of corners couldn't be measured"), **never** folded into the answer set. |

---

## Unanswerable: the two kinds

These are different promises and must not sound alike.

### `no_data` — refuse permanently

The concept is absent from every available source and will not become answerable by
building more retrieval.

| Concept | Questions | Why |
|---|---|---|
| **captain** | 5, 78 | `_match.json`'s `player_role` holds a *position* (`RW`, `GK`, `SUB`), never an armband flag. No dynamic-events column encodes it. |
| **offside call** | 27 | An offside *call* is a referee decision and there is no referee-event feed. Tracking gives offside *position*, which is a different and far more common thing. |

> *"I can't answer that — I don't know who the captain is. SkillCorner's data records each
> player's position but never who wore the armband, so there's nothing for me to filter on.*
>
> *Ask for a named player or a shirt number instead, and I can answer the same question
> exactly."*

Note what the refusal does: it says **what is missing**, **why**, and **what would work
instead**. A bare "I can't answer that" wastes the coach's turn; the alternative usually
gets them what they wanted.

Captain is one external join away — `data/captains.json` already exists as a hook. If that
file is ever populated, captain moves out of this table. Offside never does.

### `not_implemented` — refuse, for now

The data supports it; the retrieval has not been built. Saying "I can't" without the
"yet" makes a permanent-sounding promise the app will break later.

**Currently empty.** Both former entries have since been built, so every question the data
can support is now answerable:

| Concept | Questions | Resolved by |
|---|---|---|
| ~~dragged out of position~~ | 56 | `scripts/dyadic.py` + per-player baselines in Gold |
| ~~foot race~~ | 59 | `scripts/dyadic.py` |

The category stays in the registry because it will be needed again - any time the data
supports something the retrieval does not yet do. Entries must say "yet".

---

## Approximate: answer, but say so

Fourteen questions resolve only through a proxy. Answering is fine. Answering *without
disclosing* is the silent-substitution failure in milder form.

The disclosure belongs **with the results**, not buried in a tooltip:

> *"There's no 'through ball' tag in the data, so these are line-breaking passes from the
> final third — close, but broader than a true through ball."*

Documented proxies live in `APPROXIMATE_CONCEPTS` in `scripts/answerability.py`: through
ball, cutback, game management, duel won, numerical advantage. The full per-question notes
are inline in `scripts/test_all_questions.py`.

---

## Ranked: do not imply a cutoff

Q17, Q66 and Q68 return orderings rather than filtered sets, because the underlying concept
is a continuum. Q68 is the clearest case: "near post" has no crisp boundary, and a
defensible zone definition moves the answer between **12% and 48%**
(`scripts/setpiece.py --sensitivity`).

So the chatbot must not say *"here are the 18 corners where nobody attacked the near post."*
It should say *"here are their corners ordered by how unattacked the near post was"* and let
the coach stop reading where their judgement says to. Presenting a ranking as a filter
smuggles in a threshold that was never a measurement.

---

## Where this gets enforced

1. **Stage 2, query parsing** — each gate resolves the coach's phrasing to a concept. Before
   emitting filters, call `answerability.check(concept)`. A `no_data` or `not_implemented`
   verdict short-circuits: return the refusal, run no query.
2. **Stage 4, validation** — carry `status` through from the predicates. Anything not `ok`
   is a coverage caveat, never a row in the answer.
3. **Stage 5, ranking and output** — attach the fidelity and any `say` disclosure to the
   rendered result, next to the clips rather than away from them.

Also, per `docs/master_plan.md` §4a: a `validate_filter(column, event_type)` assertion
belongs alongside stage 2, so that a filter aimed at a column that is null for that event
type raises instead of returning an empty result that reads as a finding. That is the same
rule as this document, one layer down — **the system should be unable to answer confidently
from nothing.**

---

## Adding to the registry

When a new gap is found, add it to `scripts/answerability.py` with all four fields: the
concept, *why* it is blocked (for developers), what to *say* (verbatim, user-facing), and
the *nearest* answerable alternative. A gap recorded only in prose will not be consulted by
the parser and will eventually be answered with a silent proxy.
