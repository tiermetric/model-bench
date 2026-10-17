#!/usr/bin/env bash
# Selftest for the cheap-first calibration gate (PROTOCOL §3.4, model-bench#2).
#
# WHY THIS EXISTS. §3.4 says the cheap-first rule "is enforced by the harness,
# not by memory." It was not. Edition 1 ran five expensive models against
# `cronspec` with NO calibration record: the gate fired 6 times in
# edition1-panel.log and those cells published 45 minutes later, while run.sh's
# own comment claimed the ALLOW_EXPENSIVE escape "is logged into the stamp so the
# exception is visible" — and no stamp field existed. 89% of Edition 1's two-task
# spend ($18.92 of $21.1961) is non-floor spend on uncalibrated tasks.
#
# So this asserts the gate BLOCKS, not merely that it runs. Every arm drives the
# real run.sh via GATE_CHECK=1, never a reimplementation of the logic.
set -uo pipefail
# Edition 2 is closed: production prices.yaml is the v12 report table while
# run.sh correctly refuses its v11 run pin. Exercise the gate in a disposable
# copy with the archived run-table bytes, without bypassing any run.sh check.
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
GATE_FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/model-bench-gate.XXXXXX")"
trap 'rm -rf "$GATE_FIXTURE"' EXIT
mkdir -p "$GATE_FIXTURE/home"
# No caller controls (including ALLOW_EXPENSIVE, effort, session IDs or config)
# enter any arm. This selftest targets the frozen Edition 2 roster.
GATE_ENV=(env -i "PATH=$PATH" "HOME=$GATE_FIXTURE/home" "TMPDIR=${TMPDIR:-/tmp}"
  LANG=C LC_ALL=C TERM=dumb NO_COLOR=1 EDITION=2026-10 GATE_CHECK=1)
mkdir -p "$GATE_FIXTURE/editions/2026-10" "$GATE_FIXTURE/prompts" "$GATE_FIXTURE/calibration"
for name in run.sh edition.sh run_audit.py score.py token_evidence.py; do
  cp "$SOURCE_DIR/$name" "$GATE_FIXTURE/$name" || exit 1
done
for name in TIER_PIN MANIFEST.json; do
  cp "$SOURCE_DIR/editions/2026-10/$name" "$GATE_FIXTURE/editions/2026-10/$name" || exit 1
done
RUN_SHA="$(awk '/^prices_yaml_sha256:/{print $2}' "$GATE_FIXTURE/editions/2026-10/TIER_PIN")"
cp "$SOURCE_DIR/editions/2026-10/evidence/prices/$RUN_SHA.yaml" "$GATE_FIXTURE/prices.yaml" || exit 1
cd "$GATE_FIXTURE" || exit 1
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }

SEALED="__gate_selftest_uncalibrated__"
[ -e "calibration/$SEALED.cleared" ] && { echo "refusing to run: calibration/$SEALED.cleared exists"; exit 2; }
[ -e "prompts/$SEALED.md" ] && { echo "refusing to run: prompts/$SEALED.md exists"; exit 2; }
[ -e "acceptance_$SEALED.py" ] && { echo "refusing to run: acceptance_$SEALED.py exists"; exit 2; }

# The fixture needs a prompt that EXISTS but has no calibration record. run.sh
# validates the prompt file before it reaches the gate, so a nonexistent prompt
# exits 1 (FATAL: no such prompt) — which is how the first version of this
# selftest "failed" all four gate arms while proving nothing about the gate. Arm 5
# is what caught it. Clean up on any exit path, including a crash.
# run.sh validates BOTH a prompt and a judge before it reaches the gate — the
# judge check exists so "an unknown task must never judge as a silent pass". Both
# are therefore part of the fixture. Each layer was found by arm 5, one at a time.
cleanup(){ rm -rf "$GATE_FIXTURE"; }
trap cleanup EXIT INT TERM
printf '# gate selftest fixture — not a real task. Safe to delete.\n' > "prompts/$SEALED.md"
printf 'raise SystemExit("gate selftest fixture judge — must never actually run")\n' > "acceptance_$SEALED.py"

echo "== 1. REFUSE: a dear model on an uncalibrated task must exit 3 =="
out=$("${GATE_ENV[@]}" ./run.sh claude-code opus "$SEALED" 2>&1); rc=$?
if [ "$rc" -eq 3 ] && printf '%s' "$out" | grep -q "REFUSED"; then
  ok "exit 3 + REFUSED (the gate blocks)"
else
  no "expected exit 3 + REFUSED, got rc=$rc"; printf '       %s\n' "$(printf '%s' "$out" | head -2)"
fi

echo "== 2. PERMIT (floor model): needs no clearance =="
out=$("${GATE_ENV[@]}" ./run.sh claude-code haiku "$SEALED" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "GATE_BASIS=floor-model"; then
  ok "floor model permitted, basis=floor-model"
else
  no "expected rc=0 + basis=floor-model, got rc=$rc"; printf '       %s\n' "$(printf '%s' "$out" | head -2)"
fi

echo "== 3. OVERRIDE is permitted AND self-identifying =="
out=$("${GATE_ENV[@]}" ALLOW_EXPENSIVE=1 ./run.sh claude-code opus "$SEALED" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "GATE_BASIS=allow-expensive-override"; then
  ok "override permitted, and names itself an override (not 'cleared')"
else
  no "expected rc=0 + basis=allow-expensive-override, got rc=$rc"; printf '       %s\n' "$(printf '%s' "$out" | head -2)"
fi

echo "== 4. CONTROL: a cleared task permits the dear model, basis=calibration-cleared =="
mkdir -p calibration && printf 'selftest fixture — safe to delete\n' > "calibration/$SEALED.cleared"
out=$("${GATE_ENV[@]}" ./run.sh claude-code opus "$SEALED" 2>&1); rc=$?
rm -f "calibration/$SEALED.cleared"
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "GATE_BASIS=calibration-cleared"; then
  ok "clearance permits, and is distinguishable from an override"
else
  no "expected rc=0 + basis=calibration-cleared, got rc=$rc"; printf '       %s\n' "$(printf '%s' "$out" | head -2)"
fi

echo "== 5. NON-VACUITY: arm 1 must fail for the RIGHT reason =="
# If run.sh exited 3 for an unrelated reason (bad args, missing prompt) arm 1
# would pass while proving nothing. A floor model on the SAME existing, uncalibrated prompt
# must NOT exit 3 — so exit 3 is attributable to the gate, not to the fixture.
out=$("${GATE_ENV[@]}" ./run.sh claude-code haiku "$SEALED" 2>&1); rc=$?
if [ "$rc" -ne 3 ]; then ok "exit 3 is gate-attributable, not fixture-attributable"
else no "a floor model also exits 3 — arm 1 proves nothing"; fi

echo "== 6. Only the declared floors are permitted =="
for pair in 'codex gpt-6-luna' 'claude-code claude-haiku-5-5'; do
  out=$("${GATE_ENV[@]}" ./run.sh $pair "$SEALED" 2>&1); rc=$?
  if [ "$rc" = 0 ] && printf '%s' "$out" | grep -q 'GATE_BASIS=floor-model'; then
    ok "$pair floor permitted"
  else no "$pair floor refused (rc=$rc)"; fi
done

echo "== 7. GLM and Muse are non-floors; only a logged override admits them =="
for pair in 'opencode glm-5.3' 'muse muse-spark-1.3'; do
  out=$("${GATE_ENV[@]}" ./run.sh $pair "$SEALED" 2>&1); rc=$?
  if [ "$rc" = 3 ] && printf '%s' "$out" | grep -q REFUSED; then ok "$pair refused"; else no "$pair should be refused (rc=$rc)"; fi
  out=$("${GATE_ENV[@]}" ALLOW_EXPENSIVE=1 ./run.sh $pair "$SEALED" 2>&1); rc=$?
  if [ "$rc" = 0 ] && printf '%s' "$out" | grep -q 'GATE_BASIS=allow-expensive-override'; then ok "$pair override logged"; else no "$pair override not logged (rc=$rc)"; fi
done

echo "== 8. DEFENCE: deliberately inherited effort must still be refused =="
out=$("${GATE_ENV[@]}" CLAUDE_EFFORT=high ./run.sh claude-code haiku "$SEALED" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'inherited effort environment/config refused: CLAUDE_EFFORT'; then
  ok "deliberate CLAUDE_EFFORT refused before the floor gate can permit"
else
  no "expected inherited-effort refusal, got rc=$rc"; printf '       %s\n' "$(printf '%s' "$out" | head -2)"
fi

printf '\n%s\n'  "gate selftest: $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
