#!/bin/bash
set -x
# ======================================================================
# Load variables from .env if present (API_KEY, MODEL, API_MODEL, ...)
[ -f .env ] && set -a && . ./.env && set +a
# ======================================================================
ts=`date +%Y_%m_%d_%H_%M`
log_dir=./logs/scoring
mkdir -p $log_dir
# ======================================================================
API_BASE_URL=https://openrouter.ai/api  # e.g., https://openrouter.ai/api
API_KEY=${API_KEY:?API_KEY is required (e.g., sk-or-v1-XXX)}
API_MODEL=${API_MODEL:-qwen/qwen3.5-27b}
# ======================================================================
DATASET_PATH=./data/WebTestBench/WebTestBench.jsonl
OUTPUT_ROOT=./outputs

VERSION=claudecode-gpt-5.1
# ======================================================================
USE_CHECKLIST_Fallback=True

python eval/scoring.py \
    --dataset_path $DATASET_PATH \
    --output_root $OUTPUT_ROOT \
    --version $VERSION \
    --use_checklist_fallback $USE_CHECKLIST_Fallback \
    --api_base_url $API_BASE_URL \
    --api_key $API_KEY \
    --api_model $API_MODEL 2>&1 | tee ${log_dir}/log_${ts}_${VERSION}.log
