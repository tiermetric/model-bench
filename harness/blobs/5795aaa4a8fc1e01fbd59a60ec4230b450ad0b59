#!/usr/bin/env bash
# Sequential panel: roster and rates fixed before any run. No paid build tests.
set -uo pipefail
trap 'exit 130' INT
trap 'exit 143' TERM
BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BENCH_DIR"
source "$BENCH_DIR/edition.sh"
export EDITION
REPS="${REPS:-$DEFAULT_REPS}"
TASKS="${TASKS:-$DEFAULT_TASKS}"
export AGENT_TIMEOUT="${AGENT_TIMEOUT:-1800}"
# Edition 2 is the operator's anchors-only panel. Changing roster/tasks/N needs
# a new declaration, never an in-flight environment override.
if [ "$EDITION" = 2026-10 ] && { [ "$TASKS" != orgsync ] || [ "$REPS" != 1 ]; }; then
  echo "FATAL: Edition 2 is fixed at orgsync N=1, failures expand to N=3" >&2; exit 2
fi
# Fail closed BEFORE any leg: dry-run is the only bypass for a paid batch.
if [ "${DRY_RUN:-0}" != 1 ]; then
  python3 "$BENCH_DIR/preflight_check.py" require --root "$BENCH_DIR" || exit 4
fi
total=0; ok=0; bad=0; skipped=0
start=$(date +%s)
leg() {
  local tool="$1" m="$2" task="$3" r="$4"
  if [ -f "$EDITION_ROOT/$tool/$m/$task/rep$r/result.json" ]; then
    skipped=$((skipped+1))
    echo "###### SKIP existing result: $EDITION/$tool/$m/$task rep$r ######"
    return 0
  fi
  total=$((total+1))
  echo "###### [$total] $EDITION/$tool/$m/$task rep$r ######"
  if PREFLIGHT=0 ./run.sh "$tool" "$m" "$task" "$r"; then ok=$((ok+1)); else
    local rc=$?
    case "$rc" in 130|143) exit "$rc" ;; esac
    bad=$((bad+1)); echo "###### leg returned non-zero (recorded, continuing) ######"
  fi
  [ "${DRY_RUN:-0}" = 1 ] || sleep 2
}
for task in $TASKS; do
  for tool in claude-code codex opencode muse; do
    case "$tool" in
      claude-code)
        models="$CLAUDE_MODELS"
        [ "$EDITION" != 2026-07 ] || models="fable opus sonnet haiku"
        ;;
      codex) models="$CODEX_MODELS" ;;
      opencode) models="$OPENCODE_MODELS" ;;
      muse) models="$MUSE_MODELS" ;;
    esac
    for m in $models; do
      cell_reps="$REPS"
      if [ "$EDITION" = 2026-10 ]; then
        cell_reps=$(python3 - "$EDITION_ROOT/MANIFEST.json" "$tool" "$m" "$REPS" <<'REPS'
import json, sys
manifest = json.load(open(sys.argv[1]))
print(next((entry['reps'] for entry in manifest.get('late_admissions', [])
            if (entry['tool'], entry['model']) == (sys.argv[2], sys.argv[3])), sys.argv[4]))
REPS
)
      fi
      for r in $(seq 1 "$cell_reps"); do leg "$tool" "$m" "$task" "$r"; done
      if [ "$EDITION" = 2026-10 ] && [ "${DRY_RUN:-0}" != 1 ]; then
        result="$EDITION_ROOT/$tool/$m/$task/rep1/result.json"
        if ! python3 - "$result" <<'PY'
import json,sys
try:
    with open(sys.argv[1]) as result:
        outcome = json.load(result).get('outcome')
except (OSError, ValueError, AttributeError):
    outcome = None
raise SystemExit(0 if outcome == 'PASS' else 1)
PY
        then
          echo "###### anchor failure: confirming this cell at N=3 ######"
          leg "$tool" "$m" "$task" 2
          leg "$tool" "$m" "$task" 3
        fi
      fi
    done
  done
done
echo "###### BATCH COMPLETE: $total legs, $ok clean exits, $bad non-zero exits, $skipped existing results skipped, $(( ($(date +%s)-start)/60 ))m ######"
[ "$bad" = 0 ]
