#!/bin/bash
# A/B suite for the semantic-hints study — testing-execution (gold) task only.
#
# Runs the SAME gold defect-detection pipeline under two conditions, over the
# same apps, model, and turn budget, so the comparison is apples-to-apples:
#
#   condition "baseline" -> agent claude_code_gold        (accessibility tree only)
#                           project root web_applications          (UN-hinted apps)
#   condition "hints"    -> agent claude_code_gold_hints  (+ semantic-hints MCP)
#                           project root web_applications_hinted   (hinted apps)
#
# Each condition is run REPS times over the app set; one rep = one full pass.
#
# Output layout (see ../experiments/README.md for the naming spec):
#   ../experiments/runs/<run-id>/
#       run_config.json                          # manifest: params + git SHAs
#       <condition>/WebTestBench_00XX/rep<r>/     # results + logs + agent_view.txt
#
# agent_view.txt (what the agent saw + did) is dumped automatically per app by
# run_agent.py; disable with AGENT_VIEW_DUMP=0, full snapshots with AGENT_VIEW_FULL=1.
# An app whose result.md already exists is skipped, so re-running resumes.
# Run from the WebTestBench directory:  bash scripts/run_suite.sh
set -uo pipefail

# ======================================================================
# Config (override any of these from the environment)
# ======================================================================
[ -f .env ] && set -a && . ./.env && set +a

# AUTH_MODE=api          -> route the agent at API_BASE_URL with API_KEY (OpenRouter).
# AUTH_MODE=subscription -> use the Claude Code login on this machine (`claude /login`).
#
# Use "api" for measured runs that go in the write-up. A subscription enforces
# rate limits the API path doesn't: when a cap is hit mid-suite the agent stalls,
# and that lands directly in the latency measurements. Worse, it biases the A/B —
# the baseline condition burns more tokens by hypothesis, so it hits caps sooner
# than the hints condition and looks slower for reasons unrelated to hints.
# Token counts stay accurate, but total_cost_usd becomes a notional list-price
# estimate rather than real spend — a subscription run bills nothing per-request.
AUTH_MODE=${AUTH_MODE:-api}

case "$AUTH_MODE" in
  api)
    API_BASE_URL=${API_BASE_URL:-https://openrouter.ai/api}
    API_KEY=${API_KEY:?API_KEY is required for AUTH_MODE=api (set it in .env)}
    MODEL=${MODEL:-anthropic/claude-sonnet-4-6}     # same model for both conditions
    ;;
  subscription)
    # Ignore any OpenRouter values from .env: MODEL there is a gateway slug
    # (e.g. anthropic/claude-haiku-4.5) that won't resolve against the subscription.
    API_BASE_URL=""
    API_KEY=""
    MODEL=${SUBSCRIPTION_MODEL:-claude-sonnet-4-5}
    # These would be inherited by the spawned CLI and re-route it at the gateway.
    unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY
    ;;
  *)
    echo "❌ AUTH_MODE must be 'api' or 'subscription' (got '$AUTH_MODE')"; exit 1
    ;;
esac

APPS=${APPS:-"0001 0002 0003 0004 0005"}             # app numbers to test
REPS=${REPS:-1}                                       # repetitions per condition
BASE_PORT=${BASE_PORT:-6000}

# Which conditions to run, in order. Both is the point of the study — narrow it
# only to top up a partial run or to debug one arm. An A/B whose halves were run
# at different times isn't apples-to-apples: the model, the machine, and (on a
# subscription) the rate-limit state all drift between them.
CONDITIONS=${CONDITIONS:-"baseline hints"}
for c in $CONDITIONS; do
  case "$c" in
    baseline|hints) ;;
    *) echo "❌ CONDITIONS must contain only 'baseline' and/or 'hints' (got '$c')"; exit 1 ;;
  esac
done
NCONDS=$(echo $CONDITIONS | wc -w | tr -d ' ')

# Canonical model slug: lowercase, '.'/'/' -> '-', drop leading 'claude-'.
MODEL_SLUG=$(printf '%s' "${MODEL##*/}" | tr '[:upper:]' '[:lower:]' | tr './' '--' | sed 's/^claude-//')
# run-id: date + intent slug. Default slug is the model; override RUN_ID for a purpose.
RUN_ID=${RUN_ID:-"$(date +%Y-%m-%d)_${MODEL_SLUG}"}

# Identical tool/turn budget for both conditions.
export DEFECT_MAX_TURNS=${DEFECT_MAX_TURNS:-200}
export SEMANTIC_HINTS_HEADLESS=${SEMANTIC_HINTS_HEADLESS:-false}   # visible browser
# Gateway model aliasing only — under a subscription these would override the
# real model IDs the CLI resolves against the Anthropic API.
if [ "$AUTH_MODE" = "api" ]; then
  export ANTHROPIC_DEFAULT_SONNET_MODEL=$MODEL
  export ANTHROPIC_DEFAULT_OPUS_MODEL=$MODEL
  export ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL
else
  unset ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
fi

DATASET=./data/WebTestBench/WebTestBench.jsonl
BASELINE_ROOT=./data/WebTestBench/web_applications
HINTS_ROOT=./data/WebTestBench/web_applications_hinted

EXP_RUNS=${EXP_RUNS:-../experiments/runs}            # results live outside the fork
RUN_DIR="$EXP_RUNS/$RUN_ID"

PYTHON=python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

MCP_DIR=${SEMANTIC_HINTS_MCP_DIR:-../semantic-hints-mcp}

# ======================================================================
# Ensure the semantic-hints MCP is built + a shared Chromium is installed
# (both conditions must use Playwright's bundled Chromium for a fair A/B).
# ======================================================================
if [ ! -f "$MCP_DIR/dist/index.js" ]; then
  echo "🔧 semantic-hints MCP not built — building in $MCP_DIR ..."
  ( cd "$MCP_DIR" && npm install && npm run build ) || { echo "❌ MCP build failed"; exit 1; }
fi
( cd "$MCP_DIR" && npx playwright install chromium ) || { echo "❌ chromium install failed"; exit 1; }

# ======================================================================
# One JSONL holding just the selected apps; run_agent.py loops its records,
# so each rep is a single run_agent.py call over the whole app set.
# ======================================================================
mkdir -p "$RUN_DIR"
APPS_JSONL="$RUN_DIR/.apps.jsonl"
"$PYTHON" - "$DATASET" "$APPS_JSONL" $APPS <<'PY'
import json, sys
dataset, out, nums = sys.argv[1], sys.argv[2], sys.argv[3:]
want = {f"WebTestBench_{n}" for n in nums}
found = set()
with open(out, "w", encoding="utf-8") as w:
    for line in open(dataset, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("index") in want:
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            found.add(rec["index"])
missing = want - found
if missing:
    sys.exit(f"apps not found in {dataset}: {sorted(missing)}")
PY
[ $? -eq 0 ] || { echo "❌ failed to build apps jsonl"; exit 1; }

# ======================================================================
# run_config.json — the manifest. Records everything the folder names don't,
# so any run is reproducible from this file alone.
# ======================================================================
"$PYTHON" - "$RUN_DIR/run_config.json" <<PY
import json, hashlib, subprocess, time, os

KNOWN = {
    "baseline": {"agent": "claude_code_gold",       "project_root": "$BASELINE_ROOT"},
    "hints":    {"agent": "claude_code_gold_hints",  "project_root": "$HINTS_ROOT"},
}
selected = "$CONDITIONS".split()
# This run's conditions, UNIONed with any already recorded for this run-id:
# topping up one arm must not erase the record that the other one ran, or the
# manifest stops describing what's actually in the folder.
conditions = {}
try:
    with open("$RUN_DIR/run_config.json", encoding="utf-8") as _f:
        conditions = json.load(_f).get("conditions", {}) or {}
except Exception:
    pass
for _c in selected:
    conditions[_c] = KNOWN[_c]

def sha(path):
    try:
        return subprocess.check_output(["git","-C",path,"rev-parse","HEAD"], text=True).strip()
    except Exception:
        return None
def filehash(path):
    try:
        return hashlib.sha256(open(path,"rb").read()).hexdigest()[:16]
    except Exception:
        return None
cfg = {
    "run_id": "$RUN_ID",
    "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "model": "$MODEL",
    "model_slug": "$MODEL_SLUG",
    "auth_mode": "$AUTH_MODE",
    "api_base_url": "$API_BASE_URL" or None,
    "apps": "$APPS".split(),
    "reps": int("$REPS"),
    "base_port": int("$BASE_PORT"),
    "defect_max_turns": int("$DEFECT_MAX_TURNS"),
    "headless": "$SEMANTIC_HINTS_HEADLESS",
    "dataset": "$DATASET",
    "conditions": conditions,
    "git_sha": {"webtestbench": sha("."), "semantic_hints_mcp": sha("$MCP_DIR")},
    "mcp_dist_sha256_16": filehash("$MCP_DIR/dist/index.js"),
}
json.dump(cfg, open("$RUN_DIR/run_config.json","w"), indent=2)
print("📄 wrote", "$RUN_DIR/run_config.json")
PY

# ======================================================================
# Run
# ======================================================================
declare -a FAILURES

run_condition() {  # $1 name  $2 agent  $3 project_root
  local cond="$1" agent="$2" root="$3"
  echo
  echo "================================================================"
  echo "==  CONDITION: $cond   (agent=$agent, root=$root)"
  echo "================================================================"
  for r in $(seq 1 "$REPS"); do
    echo "────────────────────────────────────────────────────────────"
    echo "▶️  $cond rep $r/$REPS  ->  $RUN_DIR/$cond/WebTestBench_XXXX/rep$r/"
    # run_agent writes per app to <output_root>/<version>/<app>/<rep>/ ; reps are per-app.
    "$PYTHON" eval/run_agent.py \
        --agent "$agent" \
        --data_jsonl_path "$APPS_JSONL" \
        --project_root "$root" \
        --output_root "$RUN_DIR" \
        --log_root "$RUN_DIR" \
        --version "$cond" \
        --rep "rep$r" \
        --base_port "$BASE_PORT" \
        --auth_mode "$AUTH_MODE" \
        ${API_BASE_URL:+--api_base_url "$API_BASE_URL"} \
        ${API_KEY:+--api_key "$API_KEY"} \
        --model "$MODEL" \
    || { echo "❌ FAILED: $cond rep$r"; FAILURES+=("$cond rep$r"); }
  done
}

NAPPS=$(echo $APPS | wc -w | tr -d ' ')
echo "Suite plan: run-id=$RUN_ID  apps [$APPS] × $REPS reps × $NCONDS condition(s) [$CONDITIONS] = $(( NAPPS * REPS * NCONDS )) app-runs"
[ "$NCONDS" -lt 2 ] && echo "⚠️  Single condition — this run alone is not an A/B comparison."
echo "Model: $MODEL   auth: $AUTH_MODE   headless=$SEMANTIC_HINTS_HEADLESS   turn budget: $DEFECT_MAX_TURNS"
[ "$AUTH_MODE" = "subscription" ] && echo "⚠️  Subscription auth: rate limits can distort latency — not for measured runs."

for cond in $CONDITIONS; do
  case "$cond" in
    baseline) run_condition "baseline" "claude_code_gold"       "$BASELINE_ROOT" ;;
    hints)    run_condition "hints"    "claude_code_gold_hints" "$HINTS_ROOT" ;;
  esac
done

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
echo "Results:  $RUN_DIR/"
echo "Next:     bash scripts/score_suite.sh $RUN_ID"
echo "Then:     $PYTHON ../experiments/analysis/aggregate.py --run_dir $RUN_DIR"
