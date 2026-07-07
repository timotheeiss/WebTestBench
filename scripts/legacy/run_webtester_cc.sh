#!/bin/bash
set -euo pipefail
set -x
# ======================================================================
# Load variables from .env if present (API_KEY, MODEL, API_MODEL, ...)
[ -f .env ] && set -a && . ./.env && set +a
# ======================================================================
API_BASE_URL=https://openrouter.ai/api  # e.g., https://openrouter.ai/api
API_KEY=${API_KEY:?API_KEY is required (e.g., sk-or-v1-XXX)}
MODEL=${MODEL:-openai/gpt-5.4}         # e.g., z-ai/glm-5
VERSION=claudecode-${MODEL##*/}
# ======================================================================
export ANTHROPIC_DEFAULT_SONNET_MODEL=$MODEL
export ANTHROPIC_DEFAULT_OPUS_MODEL=$MODEL
export ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL
# ======================================================================
DATA_JSONL_PATH=./data/WebTestBench/WebTestBench.jsonl
PROJECT_ROOT=./data/WebTestBench/web_applications
OUTPUT_ROOT=./outputs
LOG_ROOT=./logs/eval
# ======================================================================
BASE_PORT=6000

python eval/run_agent.py \
    --agent claude_code \
    --data_jsonl_path $DATA_JSONL_PATH \
    --project_root $PROJECT_ROOT \
    --output_root $OUTPUT_ROOT \
    --log_root $LOG_ROOT \
    --version $VERSION \
    --base_port $BASE_PORT \
    --api_base_url $API_BASE_URL \
    --api_key $API_KEY \
    --model $MODEL 
