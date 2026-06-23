#!/bin/bash
# Run the NO-HINTS (baseline) gold tester on WebTestBench_0001 only.
#
# This is the A/B counterpart to run_webtester_cc_hints_single.sh: same model and
# same turn budget, but the plain `claude_code_gold` agent — no semantic-hints MCP,
# no shared CDP browser. The Playwright MCP launches its own isolated browser and
# the agent reads the accessibility tree (the original baseline workflow).
#
# Run from the WebTestBench directory:  bash scripts/run_webtester_cc_gold_single.sh
set -euo pipefail

# ======================================================================
# Load API_KEY / MODEL from .env if present
[ -f .env ] && set -a && . ./.env && set +a
# ======================================================================
API_BASE_URL=${API_BASE_URL:-https://openrouter.ai/api}
API_KEY=${API_KEY:?API_KEY is required (set it in .env)}
# Keep the model identical to the hints run for a fair comparison.
MODEL=anthropic/claude-sonnet-4-6
# Each run gets its own version so outputs/, logs/ and session_meta.json are kept
# separately. Note the "-gold" (no "-hints") label distinguishes baseline runs.
RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
VERSION=claudecode-${MODEL##*/}-gold-${RUN_TAG}
# ======================================================================
export ANTHROPIC_DEFAULT_SONNET_MODEL=$MODEL
export ANTHROPIC_DEFAULT_OPUS_MODEL=$MODEL
export ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL

# Same turn budget as the hints run, so the comparison is apples-to-apples.
export DEFECT_MAX_TURNS=${DEFECT_MAX_TURNS:-200}
# ======================================================================
DATA_JSONL_PATH=./data/WebTestBench/WebTestBench_single.jsonl   # WebTestBench_0001 only
PROJECT_ROOT=./data/WebTestBench/web_applications
OUTPUT_ROOT=./outputs
LOG_ROOT=./logs/eval
BASE_PORT=6000
# ======================================================================
# Prefer the project venv if present.
PYTHON=python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

echo "▶️  Run version: $VERSION  (baseline, no hints)"
echo "   outputs -> $OUTPUT_ROOT/$VERSION/WebTestBench_0001/"
echo "   logs    -> $LOG_ROOT/$VERSION/WebTestBench_0001/"

"$PYTHON" eval/run_agent.py \
    --agent claude_code_gold \
    --data_jsonl_path "$DATA_JSONL_PATH" \
    --project_root "$PROJECT_ROOT" \
    --output_root "$OUTPUT_ROOT" \
    --log_root "$LOG_ROOT" \
    --version "$VERSION" \
    --base_port "$BASE_PORT" \
    --api_base_url "$API_BASE_URL" \
    --api_key "$API_KEY" \
    --model "$MODEL"
