#!/usr/bin/env bash
# Edition panel driver: every (model x task) cell at N=3.
#
# Runs are SEQUENTIAL by design. The codex control arm identifies its session
# log by mtime against a marker, so two concurrent legs would each see the
# other's rollout and both would fail capture. Sequential also keeps wall-clock
# comparable across cells, which parallel execution would distort.
#
# Order is model-major, replicate-minor: a cell's three replicates run
# back-to-back, so a warm prompt cache benefits (or doesn't) the whole cell
# rather than splitting it across the edition.
#
# One leg never aborts the batch: run.sh records failures as rows, and a
# non-zero exit here is logged and stepped over.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

REPS="${REPS:-3}"
TASKS="${TASKS:-baseline cronspec}"
export AGENT_TIMEOUT="${AGENT_TIMEOUT:-1800}"

CLAUDE_MODELS="fable opus sonnet haiku"
CODEX_MODELS="gpt-5.6-sol gpt-5.6-terra gpt-5.6-luna"

total=0; ok=0; bad=0
start=$(date +%s)
for task in $TASKS; do
  for m in $CLAUDE_MODELS; do
    for r in $(seq 1 "$REPS"); do
      total=$((total+1))
      echo "###### [$total] claude-code/$m/$task rep$r ######"
      if ./run.sh claude-code "$m" "$task" "$r"; then ok=$((ok+1)); else
        bad=$((bad+1)); echo "###### leg returned non-zero (recorded, continuing) ######"; fi
    done
  done
  for m in $CODEX_MODELS; do
    for r in $(seq 1 "$REPS"); do
      total=$((total+1))
      echo "###### [$total] codex/$m/$task rep$r ######"
      if ./run.sh codex "$m" "$task" "$r"; then ok=$((ok+1)); else
        bad=$((bad+1)); echo "###### leg returned non-zero (recorded, continuing) ######"; fi
      sleep 2   # let the rollout log settle before the next marker
    done
  done
done
echo "###### BATCH COMPLETE: $total legs, $ok clean exits, $bad non-zero exits, $(( ($(date +%s)-start)/60 ))m ######"
