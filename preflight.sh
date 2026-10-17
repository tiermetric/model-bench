#!/usr/bin/env bash
# Paid, tiny auth/capture smoke test. Every argv/env/collector comes from run.sh.
set -euo pipefail
BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BENCH_DIR"
# Smoke the current edition's admitted pairs, even before historical reproduction.
export EDITION=2026-10
source "$BENCH_DIR/edition.sh"
PROBE_MODELS=$(python3 "$BENCH_DIR/preflight_check.py" models --root "$BENCH_DIR")
mkdir -p "$BENCH_DIR/.preflight"
PREFLIGHT_RUN_ROOT=$(mktemp -d "$BENCH_DIR/.preflight/run.XXXXXX")
export PREFLIGHT_RUN_ROOT
HTTP_PID=""
cleanup() {
  if [ -n "$HTTP_PID" ]; then kill "$HTTP_PID" 2>/dev/null || true; wait "$HTTP_PID" 2>/dev/null || true; fi
  rm -rf "$PREFLIGHT_RUN_ROOT"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# A failed new attempt must not leave a previous authorization usable.
[ "${DRY_RUN:-0}" = 1 ] || rm -f "$BENCH_DIR/.preflight/pass.json"
if [ "${DRY_RUN:-0}" != 1 ]; then
  python3 "$BENCH_DIR/preflight_http.py" "$PREFLIGHT_RUN_ROOT" >"$PREFLIGHT_RUN_ROOT/http.log" 2>&1 &
  HTTP_PID=$!
  for _ in $(seq 1 50); do
    [ -s "$PREFLIGHT_RUN_ROOT/loopback-endpoints.json" ] && break
    kill -0 "$HTTP_PID" 2>/dev/null || break
    sleep 0.1
  done
  [ -s "$PREFLIGHT_RUN_ROOT/loopback-endpoints.json" ] || { echo 'PREFLIGHT FAILED: loopback server unavailable' >&2; exit 1; }
fi
failed=0
while read -r tool model; do
  if ! PREFLIGHT=1 AGENT_TIMEOUT=120 ./run.sh "$tool" "$model" baseline 1; then
    failed=1
  fi
done <<< "$PROBE_MODELS"
[ "$failed" = 0 ] || { echo 'PREFLIGHT FAILED: no pass record written' >&2; exit 1; }
if [ "${DRY_RUN:-0}" != 1 ]; then
  python3 "$BENCH_DIR/preflight_check.py" save --root "$BENCH_DIR" --run-root "$PREFLIGHT_RUN_ROOT"
  echo 'PREFLIGHT: required tool/model pass record saved to .preflight/pass.json (valid for 60 minutes)'
fi
