#!/bin/bash
# Add one rep to the official Fable hinted run every five hours, up to rep 10.
#
# REPS is cumulative in run_suite.sh. Completed earlier reps are skipped, so
# invoking it with REPS=2, then REPS=3, ..., REPS=10 adds one full rep each time.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
INTERVAL_SECONDS=18000
START_REP=2
MAX_REPS=10

cd "$REPO_ROOT" || exit 1

for rep in $(seq "$START_REP" "$MAX_REPS"); do
  started_at=$(date +%s)
  echo
  echo "================================================================"
  echo "Starting official_fable_hinted with REPS=$rep/$MAX_REPS at $(date)"
  echo "================================================================"

  DEFECT_MAX_TURNS=400 \
  RUN_ID=official_fable_hinted \
  MODEL=claude-fable-5 \
  CONDITIONS=hints \
  APPS="0001 0003 0004 0005 0006 0010 0019 0023 0045 0054 0063 0074 0089 0097" \
  REPS="$rep" \
    bash scripts/run_suite.sh --base-port 7000
  status=$?

  if [ "$status" -ne 0 ]; then
    echo "Run-suite invocation failed for REPS=$rep (exit $status); stopping."
    exit "$status"
  fi

  if [ "$rep" -eq "$MAX_REPS" ]; then
    echo "Completed all $MAX_REPS reps."
    break
  fi

  next_start=$((started_at + INTERVAL_SECONDS))
  now=$(date +%s)
  wait_seconds=$((next_start - now))
  if [ "$wait_seconds" -gt 0 ]; then
    echo "Next invocation (REPS=$((rep + 1))) starts in $wait_seconds seconds."
    sleep "$wait_seconds"
  else
    echo "This invocation took at least five hours; starting REPS=$((rep + 1)) immediately."
  fi
done
