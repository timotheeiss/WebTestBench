#!/bin/bash
# Score every <condition>/rep<r> leaf of a suite run with the deterministic
# oracle scorer (eval/scoring_oracle.py — id alignment, no judge model).
#
# For each (condition, rep) it writes:
#   WebTestBench_00XX/rep<r>/score.json   (per app-rep)
#   <condition>/rep<r>_score_avg.json, rep<r>_missing_results.json   (accuracy over apps)
#
# Usage (from the WebTestBench directory):
#   bash scripts/score_suite.sh <run-id>
#   bash scripts/score_suite.sh 2026-07-07_sonnet-4-6
set -uo pipefail

RUN_ID=${1:?usage: bash scripts/score_suite.sh <run-id>}
EXP_RUNS=${EXP_RUNS:-../experiments/runs}
RUN_DIR="$EXP_RUNS/$RUN_ID"
DATASET=${DATASET:-./data/WebTestBench/WebTestBench.jsonl}

PYTHON=python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

[ -d "$RUN_DIR" ] || { echo "❌ run dir not found: $RUN_DIR"; exit 1; }

scored=0
for cond in baseline hints; do
  cond_dir="$RUN_DIR/$cond"
  [ -d "$cond_dir" ] || continue
  # Reps live per app (<cond>/<app>/<rep>/); collect the distinct rep labels.
  reps=$(ls -d "$cond_dir"/WebTestBench_*/rep*/ 2>/dev/null | xargs -n1 basename 2>/dev/null | sort -u)
  [ -n "$reps" ] || { echo "⚠️  no reps found under $cond_dir"; continue; }
  for rep in $reps; do
    echo "🧮 scoring $cond/$rep"
    "$PYTHON" eval/scoring_oracle.py \
        --dataset_path "$DATASET" \
        --output_root "$RUN_DIR" \
        --version "$cond" \
        --rep "$rep" \
    && scored=$((scored+1)) || echo "⚠️  scoring failed for $cond/$rep"
  done
done

echo
echo "✅ scored $scored condition/rep leaf(s) under $RUN_DIR"
echo "Next: $PYTHON ../experiments/analysis/aggregate.py --run_dir $RUN_DIR"
