# eval/ — what's live vs. legacy

The semantic-hints study uses **only the defect-detection (gold) task**. The
upstream checklist-generation stage and judge-model scorer are dismissed. The
modules below are physically left in place (they're wired into the `agent/` and
`prompt/` import registries, and some carry in-progress edits), so this file is
the map of which code path is actually exercised.

## Live research path

| File | Role |
|---|---|
| `run_agent.py` | Entrypoint. `--output_root/--version` set the run leaf; app dirs written under it. Driven by `scripts/run_suite.sh`. |
| `agent/base_agent.py` | Shared agent: dev-server deploy/teardown, SDK loop, `session_meta.json`. |
| `agent/claude_code_gold.py` | **baseline** agent — materializes the gold checklist from the record, then defect-detects reading the accessibility tree. |
| `agent/claude_code_gold_hints.py` | **hints** agent — subclass of the above, adds the semantic-hints MCP. |
| `prompt/defect_detection_based_gold.py` | baseline defect-detection prompt (`defect_detection_based_gold`). |
| `prompt/defect_detection_based_gold_with_hints.py` | hints defect-detection prompt (`..._with_hints`). |
| `scoring_oracle.py` | Deterministic id-alignment scorer (no judge model). Driven by `scripts/score_suite.sh`. |
| `tools.py`, `utils.py` | Shared helpers. |

## Legacy (dismissed benchmark path — do not build on these)

| File | Why legacy |
|---|---|
| `agent/claude_code.py` | Full two-stage tester (LLM checklist generation + detection). Registry key `claude_code`; only used by `scripts/legacy/`. |
| `prompt/checklist_generation.py` | LLM checklist-generation prompt. The gold agent does **not** use it (it builds the checklist from the record). |
| `prompt/defect_detection.py` | Non-gold detection prompt, paired with `claude_code.py`. |
| `scoring.py` | Judge-model scorer. Superseded by `scoring_oracle.py`; imported nowhere. |
| `prompt/match_item.py` | Judge-scorer matching prompt. |

> Not moved on purpose: `agent/__init__.py` and `prompt/__init__.py` import these
> to populate their registries, and `claude_code.py` / `claude_code_gold.py` have
> uncommitted edits. Quarantining them into an `eval/legacy/` package is a
> separate, deliberate change (update both registries; drop the dead
> `checklist_generation`/`defect_detection`/`match_item` keys from `USER_PROMPT`).
