# skill-corner-film-chatbot

A chatbot for coaches and scouts: ask a natural-language question about a team or player,
get back a ranked list of clip segments that answer it. Clips are rendered as animations of
tracking data (players + ball as dots on a pitch) — there is no broadcast film anywhere in
this project.

Data source: [SkillCorner Open Data](https://github.com/SkillCorner/opendata) — 20 matches
of broadcast tracking data (10 fps) plus SkillCorner's derived Dynamic Events and Phases of
Play datasets.

**Start with [`docs/master_plan.md`](docs/master_plan.md)** — current status, what's built,
issues hit and fixed, test-set results, and next steps. See `CLAUDE.md` for the project
brief and architecture decisions.

## Reference material

- `coach_question_test_set.md` — 80 coach/scout questions across 8 categories the retrieval
  architecture is designed against.
- `skillcorner_schema.md` — reference schema for the tracking data, Dynamic Events CSV, and
  Phases of Play framework.
- `skillcorner_exploration.ipynb` — exploration notebook, now run against a real match with
  real outputs committed.
- `docs/real_data_validation.md` — the first two validation passes (its 47/22/11 headline is
  superseded; see below).
- `docs/enrichment.md` — Tier 1 + Tier 2 enrichment: 32 derived columns that took the test
  set to **57 exact, 16 approximate, 7 unresolved**.
- `docs/field_catalog.md` — generated event_type x column availability map. Read before
  writing a filter: 168 of 354 columns are populated on exactly one event_type.
- `docs/tier3_lazy_retrieval_plan.md` — the adopted Tier 3 design (lazy per-event tracking
  confirmation), with `docs/tier3_tracking_plan.md` holding the metric definitions.

## Getting started

```bash
pip install -r requirements.txt
scripts/fetch_match_data.sh all --tracking    # Bronze (~1.8 GB; omit --tracking for 89 MB)
python scripts/build_silver.py                # Silver: cleaned/typed tables + validation
python scripts/build_gold.py                  # Gold:   derived, query-ready tables
python scripts/test_all_questions.py          # all 80 test-set questions

cp .env.example .env                         # then set ANTHROPIC_API_KEY (gitignored)
python scripts/query_parse.py --status       # check the credential is picked up
python scripts/query_parse.py "show me every shot from outside the box"
python scripts/query_parse.py --offline ...  # no key needed; keyword stub, plumbing only
```

Stage 2 (question -> structured query) needs an Anthropic API key. Copy `.env.example` to
`.env` and fill it in - `.env` is gitignored, `.env.example` is committed so the required
variables stay documented. Without a key, `--offline` still exercises the chain and both
guardrails using a keyword stub, which proves the plumbing but says nothing about how well
questions are actually parsed.

Data follows a mandated bronze/silver/gold layout — see
[`docs/data_architecture.md`](docs/data_architecture.md). Bronze is immutable raw; each
layer reads only the layer below it.

`scripts/fetch_match_data.sh` shallow-clones SkillCorner's public open data repo and copies
match files into `data/matches/<id>/`, which is gitignored. Pass a match id for a single
match, or `all` (the default) for every match. `_tracking_extrapolated.jsonl` is Git-LFS-only
upstream — `--tracking` pulls it, which works anonymously and costs ~91 MB per match. It is
only needed for Tier 3; every result in `docs/enrichment.md` is event-level.
