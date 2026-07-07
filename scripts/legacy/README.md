# scripts/legacy/

Not used by the semantic-hints study. Kept for provenance / reference.

## Dismissed benchmark path (upstream two-stage tester)
The study uses only the **defect-detection (gold)** task, so the
checklist-generation pipeline and the judge-model scorer are retired:

- `run_webtester_cc.sh`, `run_webtester_cc_parallel.sh` — full two-stage tester
  (`claude_code` agent: checklist generation + defect detection).
- `run_scoring.sh` — judge-model scorer (`eval/scoring.py`).

## Superseded by the current suite
- `run_webtester_cc_gold_single.sh`, `run_webtester_cc_hints_single.sh` — single-app
  runners, now `run_suite.sh` with `APPS=00XX REPS=1`.
- `run_webtester_cc_suite.sh` — previous A/B suite (flat `outputs/<version>/` layout).
- `run_scoring_oracle.sh` — single-leaf oracle scorer, now `score_suite.sh`.

Live entrypoints are one level up: `run_suite.sh`, `score_suite.sh`,
`dump_agent_view.py`. See `../../../experiments/README.md` for the pipeline.
