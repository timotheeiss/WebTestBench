#!/bin/bash
# Dump the agent's-eye view (observations it received + actions it took) for every
# app-rep of a suite run, writing agent_view.txt next to each session_meta.json.
#
# Replays each run's Claude Agent SDK transcript (~/.claude/projects) via
# dump_agent_view.py. Runs with no transcript on this machine are skipped.
#
# Usage (from the WebTestBench directory):
#   bash scripts/dump_suite.sh <run-id> [--full]
#   bash scripts/dump_suite.sh 2026-06-29_sonnet-4-6 --full
set -uo pipefail

RUN_ID=${1:?usage: bash scripts/dump_suite.sh <run-id> [--full]}
shift || true
EXTRA="$@"                                            # e.g. --full
EXP_RUNS=${EXP_RUNS:-../experiments/runs}
RUN_DIR="$EXP_RUNS/$RUN_ID"

PYTHON=python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

[ -d "$RUN_DIR" ] || { echo "❌ run dir not found: $RUN_DIR"; exit 1; }

wrote=0 skipped=0
while IFS= read -r meta; do
  dir=$(dirname "$meta")
  sid=$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['defect_detection']['session_id'])" "$meta" 2>/dev/null)
  rel=${dir#"$RUN_DIR"/}
  if [ -z "$sid" ]; then
    echo "⚠️  $rel: no session_id in session_meta.json — skipping"; skipped=$((skipped+1)); continue
  fi
  if "$PYTHON" scripts/dump_agent_view.py "$sid" --out "$dir/agent_view.txt" $EXTRA >/dev/null 2>&1; then
    echo "📝 $rel/agent_view.txt"; wrote=$((wrote+1))
  else
    echo "⚠️  $rel: transcript $sid not found on this machine — skipping"; skipped=$((skipped+1))
  fi
done < <(find "$RUN_DIR" -path '*/WebTestBench_*/rep*/session_meta.json' | sort)

echo
echo "✅ wrote $wrote agent_view.txt file(s), skipped $skipped, under $RUN_DIR"
