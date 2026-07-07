#!/bin/bash
# A/B suite: run apps 1-5, BASELINE (no hints) and HINTS.
#
# For each variant it runs the same gold defect-detection pipeline already used by
# run_webtester_cc_gold_single.sh / run_webtester_cc_hints_single.sh, just looped
# over apps WebTestBench_0001..0005. Each (app, variant) is run REPS times
# (default 1); set REPS higher to collect several samples per (app, variant) for
# means / confidence intervals.
#
#   variant "baseline" -> agent claude_code_gold       (reads the a11y tree only)
#                         project root web_applications           (UN-hinted apps)
#   variant "hints"    -> agent claude_code_gold_hints  (+ semantic-hints MCP)
#                         project root web_applications_hinted    (hinted apps)
#
# Repo functionality reused as-is:
#   - npm install + dev-server deploy/teardown happen inside the agent
#     (BaseAgent.server_deploy / kill_local_server). Installed node_modules are
#     left in place between/after runs (npm install is a no-op once present).
#   - The browser is VISIBLE (headed) for both variants, so you watch every run.
#
# Outputs:  outputs/<version>/<app>/            Logs: logs/eval/<version>/<app>/
# Versions: claudecode-<model>-gold-rep<r>      (baseline)
#           claudecode-<model>-gold-hints-rep<r> (hints)
# A run whose result_extracted.md already exists is skipped, so re-running resumes.
#
# Run from the WebTestBench directory:  bash scripts/run_webtester_cc_suite.sh
set -uo pipefail

# ======================================================================
# Config (override any of these from the environment)
# ======================================================================
[ -f .env ] && set -a && . ./.env && set +a

API_BASE_URL=${API_BASE_URL:-https://openrouter.ai/api}
API_KEY=${API_KEY:?API_KEY is required (set it in .env)}
# Same model for both variants so the A/B comparison is fair.
MODEL=${MODEL:-anthropic/claude-sonnet-4-6}

APPS=${APPS:-"0001 0002 0003 0004 0005"}   # app numbers to test
REPS=${REPS:-1}                            # repetitions per (app, variant)
BASE_PORT=${BASE_PORT:-6000}

# Identical tool/turn budget for both variants -> apples-to-apples.
export DEFECT_MAX_TURNS=${DEFECT_MAX_TURNS:-200}
# Visible browser. Set to "true" only on a display-less host.
export SEMANTIC_HINTS_HEADLESS=${SEMANTIC_HINTS_HEADLESS:-false}
export ANTHROPIC_DEFAULT_SONNET_MODEL=$MODEL
export ANTHROPIC_DEFAULT_OPUS_MODEL=$MODEL
export ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL

DATASET=./data/WebTestBench/WebTestBench.jsonl
BASELINE_ROOT=./data/WebTestBench/web_applications
HINTS_ROOT=./data/WebTestBench/web_applications_hinted
OUTPUT_ROOT=./outputs
LOG_ROOT=./logs/eval

PYTHON=python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

# ======================================================================
# Ensure the semantic-hints MCP is built (needed by the hints variant).
# ======================================================================
MCP_DIR=${SEMANTIC_HINTS_MCP_DIR:-../semantic-hints-mcp}
if [ ! -f "$MCP_DIR/dist/index.js" ]; then
  echo "🔧 semantic-hints MCP not built — building in $MCP_DIR ..."
  ( cd "$MCP_DIR" && npm install && npm run build ) || { echo "❌ MCP build failed"; exit 1; }
fi

# Ensure the Chromium build used by BOTH variants is installed. The hints
# launcher calls chromium.launch() and the baseline Playwright MCP runs with
# `--browser chromium`; both resolve to Playwright's bundled Chromium. Installing
# it via the MCP's playwright keeps the two arms on the SAME browser (fair A/B)
# and avoids the "Chromium distribution 'chrome' is not found" launch failure on
# hosts without Google Chrome. No-op once installed.
( cd "$MCP_DIR" && npx playwright install chromium ) || { echo "❌ chromium install failed"; exit 1; }

# ======================================================================
# Per-app single-record JSONLs (run_agent.py iterates every record in a file, so
# one app per file lets us run an app's REPS back-to-back before the next app).
# ======================================================================
TMP_JSONL_DIR=$(mktemp -d)
cleanup() { rm -rf "$TMP_JSONL_DIR"; }
trap cleanup EXIT

make_app_jsonl() {  # $1 = WebTestBench_000X -> path to a 1-line jsonl
  local app_id="$1"
  local out="$TMP_JSONL_DIR/$app_id.jsonl"
  "$PYTHON" - "$DATASET" "$app_id" "$out" <<'PY'
import json, sys
dataset, app_id, out = sys.argv[1], sys.argv[2], sys.argv[3]
for line in open(dataset, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    rec = json.loads(line)
    if rec.get("index") == app_id:
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        sys.exit(0)
sys.exit(f"{app_id} not found in {dataset}")
PY
  echo "$out"
}

# ======================================================================
# Run helpers
# ======================================================================
declare -a FAILURES

run_one() {  # $1 agent  $2 project_root  $3 version  $4 jsonl  $5 label
  local agent="$1" root="$2" version="$3" jsonl="$4" label="$5"
  echo "────────────────────────────────────────────────────────────"
  echo "▶️  $label"
  echo "    agent=$agent  version=$version"
  echo "    outputs -> $OUTPUT_ROOT/$version/   logs -> $LOG_ROOT/$version/"
  "$PYTHON" eval/run_agent.py \
      --agent "$agent" \
      --data_jsonl_path "$jsonl" \
      --project_root "$root" \
      --output_root "$OUTPUT_ROOT" \
      --log_root "$LOG_ROOT" \
      --version "$version" \
      --base_port "$BASE_PORT" \
      --api_base_url "$API_BASE_URL" \
      --api_key "$API_KEY" \
      --model "$MODEL" \
  || { echo "❌ FAILED: $label"; FAILURES+=("$label"); }
}

run_variant() {  # $1 variant-name  $2 agent  $3 project_root  $4 version-suffix
  local vname="$1" agent="$2" root="$3" vsuffix="$4"
  echo
  echo "================================================================"
  echo "==  VARIANT: $vname   (agent=$agent, root=$root)"
  echo "================================================================"
  for app in $APPS; do
    local app_id="WebTestBench_${app}"
    local jsonl; jsonl=$(make_app_jsonl "$app_id") || { echo "❌ $app_id: $jsonl"; FAILURES+=("$app_id jsonl"); continue; }
    if [ ! -d "$root/$app_id" ]; then
      echo "❌ Missing app dir: $root/$app_id — skipping."; FAILURES+=("$app_id missing in $root"); continue
    fi
    for r in $(seq 1 "$REPS"); do
      run_one "$agent" "$root" "claudecode-${MODEL##*/}-${vsuffix}-rep${r}" "$jsonl" "$vname $app_id rep $r/$REPS"
    done
  done
}

# ======================================================================
# Plan + execute
# ======================================================================
NAPPS=$(echo $APPS | wc -w | tr -d ' ')
echo "Suite plan: apps [$APPS] × $REPS reps × 2 variants = $(( NAPPS * REPS * 2 )) runs"
echo "Model: $MODEL   Visible browser: headless=$SEMANTIC_HINTS_HEADLESS   Turn budget: $DEFECT_MAX_TURNS"

run_variant "baseline" "claude_code_gold"       "$BASELINE_ROOT" "gold"
run_variant "hints"    "claude_code_gold_hints" "$HINTS_ROOT"    "gold-hints"

# ======================================================================
# Summary
# ======================================================================
echo
echo "================================================================"
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "🎉 Suite complete — all runs finished."
else
  echo "⚠️  Suite finished with ${#FAILURES[@]} failed run(s):"
  for f in "${FAILURES[@]}"; do echo "   - $f"; done
fi
echo "Results under: $OUTPUT_ROOT/claudecode-${MODEL##*/}-gold-rep*/  and  -gold-hints-rep*/"
