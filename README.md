# skill-corner-film-chatbot

A chatbot for coaches and scouts: ask a natural-language question about a team or player,
get back a ranked list of clip segments that answer it. Clips are rendered as animations of
tracking data (players + ball as dots on a pitch) — there is no broadcast film anywhere in
this project.

Data source: [SkillCorner Open Data](https://github.com/SkillCorner/opendata) — 10 matches
of broadcast tracking data (10 fps) plus SkillCorner's derived Dynamic Events and Phases of
Play datasets.

See `CLAUDE.md` for the full project brief, architecture decisions, and open questions.

## Reference material

- `coach_question_test_set.md` — the seed set of ~39 coach/scout questions the retrieval
  architecture is designed against.
- `skillcorner_schema.md` — reference schema for the tracking data, Dynamic Events CSV, and
  Phases of Play framework.
- `skillcorner_exploration.ipynb` — first-pass exploration against synthetic data matching
  the real schema.
- `docs/real_data_validation.md` — re-validation of that exploration's queries against a
  real match.

## Getting started

```bash
pip install -r requirements.txt
scripts/fetch_match_data.sh 1874553      # pulls one real match into data/matches/
python scripts/validate_real_data.py 1874553
```

`scripts/fetch_match_data.sh` shallow-clones SkillCorner's public open data repo and copies
one match's files (`_match.json`, `_dynamic_events.csv`, `_phases_of_play.csv`) into
`data/matches/<id>/`, which is gitignored. `_tracking_extrapolated.jsonl` is Git-LFS-only
upstream and isn't fetched by this script; it's only needed for the comparative/relational
query gate, which isn't implemented yet.
