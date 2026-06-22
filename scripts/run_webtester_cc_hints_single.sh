#!/bin/bash
# Run the HINTS-enabled gold tester on WebTestBench_0001 only.
#
# This launches one shared, VISIBLE Chromium; the official Playwright MCP attaches
# to it (--cdp-endpoint) and the semantic-hints MCP reads data-agent-* hints from
# the same browser (SEMANTIC_HINTS_CDP_URL). You watch the real interactions on
# screen, and token usage / cost is printed to the console at the end of the run.
#
# Run from the WebTestBench directory:  bash scripts/run_webtester_cc_hints_single.sh
set -euo pipefail

# ======================================================================
# Load API_KEY / MODEL from .env if present
[ -f .env ] && set -a && . ./.env && set +a
# ======================================================================
API_BASE_URL=${API_BASE_URL:-https://openrouter.ai/api}
API_KEY=${API_KEY:?API_KEY is required (set it in .env)}
# Use Sonnet 4.6 as the LLM (OpenRouter slug). For the direct Anthropic API,
# set API_BASE_URL=https://api.anthropic.com and MODEL=claude-sonnet-4-6.
MODEL=anthropic/claude-sonnet-4-6
# Each run gets its own version so outputs/, logs/ and session_meta.json are kept
# separately (nothing is overwritten) and the harness never skips a "done" record.
# Override with a meaningful label to compare runs, e.g.
#   RUN_TAG=with-option-hints bash scripts/run_webtester_cc_hints_single.sh
RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
VERSION=claudecode-${MODEL##*/}-gold-hints-${RUN_TAG}
# ======================================================================
export ANTHROPIC_DEFAULT_SONNET_MODEL=$MODEL
export ANTHROPIC_DEFAULT_OPUS_MODEL=$MODEL
export ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL

# Show the real browser on screen. Set to "true" to run headless instead.
export SEMANTIC_HINTS_HEADLESS=${SEMANTIC_HINTS_HEADLESS:-false}

# Tool-call/turn budget for the defect-detection agent. Read by both the baseline
# and hints gold agents, so keep it identical across A/B runs. The agent's stated
# budget in the prompt is synced to this value automatically.
export DEFECT_MAX_TURNS=${DEFECT_MAX_TURNS:-200}
# ======================================================================
DATA_JSONL_PATH=./data/WebTestBench/WebTestBench_single.jsonl   # WebTestBench_0001 only
PROJECT_ROOT=./data/WebTestBench/web_applications
OUTPUT_ROOT=./outputs
LOG_ROOT=./logs/eval
BASE_PORT=6000
# ======================================================================
# Ensure the semantic-hints MCP is built (../semantic-hints-mcp relative to here).
MCP_DIR=${SEMANTIC_HINTS_MCP_DIR:-../semantic-hints-mcp}
if [ ! -f "$MCP_DIR/dist/index.js" ]; then
  echo "🔧 semantic-hints MCP not built — building in $MCP_DIR ..."
  ( cd "$MCP_DIR" && npm install && npm run build )
  ( cd "$MCP_DIR" && npx playwright install chromium )
fi
# ======================================================================
# Prefer the project venv if present.
PYTHON=python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

echo "▶️  Run version: $VERSION"
echo "   outputs -> $OUTPUT_ROOT/$VERSION/WebTestBench_0001/"
echo "   logs    -> $LOG_ROOT/$VERSION/WebTestBench_0001/"

"$PYTHON" eval/run_agent.py \
    --agent claude_code_gold_hints \
    --data_jsonl_path "$DATA_JSONL_PATH" \
    --project_root "$PROJECT_ROOT" \
    --output_root "$OUTPUT_ROOT" \
    --log_root "$LOG_ROOT" \
    --version "$VERSION" \
    --base_port "$BASE_PORT" \
    --api_base_url "$API_BASE_URL" \
    --api_key "$API_KEY" \
    --model "$MODEL"
