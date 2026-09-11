# skill-corner-film-chatbot

A chatbot for coaches and scouts: ask a natural-language question about a team or player,
get back a ranked list of clip segments that answer it. Clips are rendered as animations of
tracking data (players + ball as dots on a pitch) — there is no broadcast film anywhere in
this project.

Data source: [SkillCorner Open Data](https://github.com/SkillCorner/opendata) — 20 matches
of broadcast tracking data (10 fps) plus SkillCorner's derived Dynamic Events and Phases of
Play datasets.

See `CLAUDE.md` for the full project brief, architecture decisions, and open questions.

## Reference material

- `coach_question_test_set.md` — 80 coach/scout questions across 8 categories the retrieval
  architecture is designed against.
- `skillcorner_schema.md` — reference schema for the tracking data, Dynamic Events CSV, and
  Phases of Play framework.
- `skillcorner_exploration.ipynb` — exploration notebook, now run against a real match with
  real outputs committed.
- `docs/real_data_validation.md` — two validation passes: 4 representative queries against
  one match, then all 80 test-set questions against all 20 matches (47 exact, 22
  approximate, 11 unresolved — see the doc for why).

## Getting started

```bash
pip install -r requirements.txt
scripts/fetch_match_data.sh 1874553      # pulls one real match into data/matches/
python scripts/validate_real_data.py 1874553    # the 4 representative queries
python scripts/test_all_questions.py            # all 80 test-set questions, across every match fetched
```

`scripts/fetch_match_data.sh` shallow-clones SkillCorner's public open data repo and copies
one match's files (`_match.json`, `_dynamic_events.csv`, `_phases_of_play.csv`) into
`data/matches/<id>/`, which is gitignored; run it once per match id you want
(`test_all_questions.py` picks up every match already fetched). `_tracking_extrapolated.jsonl`
is Git-LFS-only upstream and isn't fetched by this script; it's only needed for a handful of
tracking-dependent test-set questions — see `docs/real_data_validation.md` for which ones.
