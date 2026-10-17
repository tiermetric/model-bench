#!/usr/bin/env bash
# Reusable cross-tool / cross-model TIER benchmark runner.
#
# Usage: ./run.sh <tool> <model> [prompt]
#   tool   ∈ claude-code | codex
#   model  — claude-code: fable | opus | sonnet | haiku   (or a full claude-* model ID)
#            codex:       gpt-5.6-sol | gpt-5.6-terra | gpt-5.6-luna  (any codex -m value)
#   prompt — name of prompts/<prompt>.md (default: baseline). The task text is
#            everything AFTER the first `---` line of the prompt file.
#
# One run = one fresh git repo at model-bench/<tool>/<model>/<prompt>/ .
# Adding a model = adding a dir + a row; a harder task = a new prompts/<name>.md.
# This harness is meant to be re-run and published on new model releases.
#
# Per run:
#   1. FRESH dir <tool>/<model>/<prompt>/ + git init (refuses to reuse — no staging).
#   2. Control arm (before): no attributable session log may pre-exist ($0 before).
#   3. Headless agent run:
#        claude-code: claude -p --model <id> --dangerously-skip-permissions "<task>"
#        codex:       codex exec -m <model> -C <dir> -s workspace-write \
#                       --skip-git-repo-check --color never "<task>"
#      (codex: sandboxed, NOT the dangerous bypass; NEVER --ephemeral — that would
#       suppress the rollout log spend is captured from. If the sandboxed run is
#       blocked, this script fails cleanly for an interactive fallback.)
#   4. The model's own `python3 -m unittest test_duration`.
#   5. The independent acceptance suite (acceptance_test.py — the uniform judge).
#   6. Control arm (after): exactly ONE new session log; score.py sums its usage,
#      prices at list rates from TIER prices.yaml, asserts spend > 0, emits the
#      markdown diagnostics row + result.json.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="${1:?usage: run.sh <claude-code|codex> <model> [prompt]}"
MODEL_ARG="${2:?usage: run.sh <claude-code|codex> <model> [prompt]}"
PROMPT_NAME="${3:-baseline}"
PROMPT_FILE="$BENCH_DIR/prompts/$PROMPT_NAME.md"

[ -f "$PROMPT_FILE" ] || { echo "FATAL: no such prompt: $PROMPT_FILE" >&2; exit 1; }

# ── Per-task judge + own-test dispatch (by prompt name) ──────────────────────
# Each prompt dictates a fixed implementation/test filename that the model must
# write and that this harness knows a-priori; the judge is acceptance_<prompt>.py
# (baseline keeps the historical acceptance_test.py name via the mapping below).
# Adding a task = adding a prompts/<name>.md, an acceptance_<name>.py judge, and
# one case here. An unknown prompt with no judge file is a FATAL loud error
# (never a silent pass).
case "$PROMPT_NAME" in
  baseline) JUDGE_FILE="acceptance_test.py";      TEST_MODULE="test_duration" ;;
  cronspec) JUDGE_FILE="acceptance_cronspec.py";  TEST_MODULE="test_cron" ;;
  *)        JUDGE_FILE="acceptance_$PROMPT_NAME.py"; TEST_MODULE="test_$PROMPT_NAME" ;;
esac
JUDGE_PATH="$BENCH_DIR/$JUDGE_FILE"
if [ ! -f "$JUDGE_PATH" ]; then
  echo "FATAL: no judge for prompt '$PROMPT_NAME' — expected $JUDGE_PATH." >&2
  echo "       (an unknown task must never judge as a silent pass — add the judge file.)" >&2
  exit 1
fi

# ── Model mapping ────────────────────────────────────────────────────────────
# claude-code short names map to full API model IDs, which double as the
# prices.yaml key (all four verified present in prices.yaml v7).
case "$TOOL" in
  claude-code)
    case "$MODEL_ARG" in
      fable)  MODEL_ID="claude-fable-5" ;;
      opus)   MODEL_ID="claude-opus-4-8" ;;
      sonnet) MODEL_ID="claude-sonnet-5" ;;
      haiku)  MODEL_ID="claude-haiku-4-5" ;;
      claude-*) MODEL_ID="$MODEL_ARG" ;;
      *) echo "FATAL: unknown claude-code model '$MODEL_ARG' (fable|opus|sonnet|haiku)" >&2; exit 1 ;;
    esac
    DIR_NAME="$MODEL_ARG"
    PRICE_KEY="$MODEL_ID"
    EXTRA_SCORE_FLAGS=""
    ;;
  codex)
    MODEL_ID="$MODEL_ARG"
    DIR_NAME="$MODEL_ARG"
    PRICE_KEY="$MODEL_ID"
    # prices.yaml v8 (#461) landed gpt-5.6-sol/terra/luna, so the unpriced
    # bypass is gone: an unpriced model is now a hard error for EVERY vendor,
    # as PROTOCOL §4.3 requires. Never substitute a neighbouring model's rate.
    EXTRA_SCORE_FLAGS=""
    ;;
  *) echo "FATAL: unknown tool '$TOOL' (claude-code|codex)" >&2; exit 1 ;;
esac

# ── Cheap-first gate (PROTOCOL §3.4) ────────────────────────────────────────
# No expensive model runs against a task until a cheaper model has failed it.
# Enforced here rather than remembered, because the failure mode is expensive
# and silent: we twice ran a full roster including flagship models against a
# task everyone passed. The floor detectors could have told us the same thing
# for a fiftieth of the cost.
#
# A task is CLEARED for the dear models once calibration/<prompt>.cleared
# exists. Create it only when a floor detector has actually failed the task —
# ./calibrate.sh writes it. Override for a deliberate re-measurement of an
# already-cleared task (e.g. re-running a published edition) with
# ALLOW_EXPENSIVE=1, which is logged into the stamp so the exception is visible.
FLOOR_MODELS=" haiku gpt-5.6-luna "
CLEARED_MARK="$BENCH_DIR/calibration/$PROMPT_NAME.cleared"
if [ "${ALLOW_EXPENSIVE:-0}" != "1" ] \
   && [ ! -f "$CLEARED_MARK" ] \
   && ! printf '%s' "$FLOOR_MODELS" | grep -q " $MODEL_ARG "; then
  echo "REFUSED: '$MODEL_ARG' is not a floor model and '$PROMPT_NAME' has no calibration record." >&2
  echo "         Run the floor detectors first:" >&2
  echo "           ./run.sh claude-code haiku $PROMPT_NAME" >&2
  echo "           ./run.sh codex gpt-5.6-luna $PROMPT_NAME" >&2
  echo "         A task only earns the dear models by defeating a cheap one." >&2
  echo "         (deliberate re-measurement of a cleared task: ALLOW_EXPENSIVE=1)" >&2
  exit 3
fi

# ── Run identity: a cell is (tool, model, prompt); a RUN is one replicate ────
# N=3 requires replicate directories. `rep<k>` is the run index inside the cell;
# it also makes the Claude project slug unique per replicate, so the "no
# pre-existing session log" control arm is true by construction rather than by
# remembering to clear ~/.claude/projects/<slug> on archive (the defect that
# cost the cronspec-v2 pilot a full round).
CELL_DIR="$BENCH_DIR/$TOOL/$DIR_NAME/$PROMPT_NAME"
REP="${4:-}"
if [ -z "$REP" ]; then
  REP=1
  while [ -e "$CELL_DIR/rep$REP" ]; do REP=$((REP + 1)); done
fi
RUN_DIR="$CELL_DIR/rep$REP"
if [ -e "$RUN_DIR" ]; then
  echo "FATAL: $RUN_DIR already exists — each replicate needs a fresh repo." >&2
  exit 1
fi

# Task text = everything after the first `---` line of the prompt file.
TASK="$(awk 'sep{print} /^---[[:space:]]*$/{if(!sep)sep=1}' "$PROMPT_FILE")"
if [ -z "$(printf '%s' "$TASK" | tr -d '[:space:]')" ]; then
  echo "FATAL: no task text after the first --- separator in $PROMPT_FILE" >&2
  exit 1
fi

mkdir -p "$RUN_DIR"

# ── Per-task setup: skeleton + fixture services ─────────────────────────────
# Most tasks start from a fresh EMPTY repo. A brownfield/integration task needs
# an existing codebase laid down and its fixture services running before the
# model starts. A task opts in by having tasks/<prompt>-skeleton/ and/or
# fixtures/<prompt>/server.py; anything else behaves exactly as before.
#
# The fixture is torn down and its request log archived into the run bundle in
# all cases — normal exit, failure, or interrupt — because a leaked port would
# silently corrupt the NEXT run, which is the same class of defect as the stale
# session log that cost an earlier pilot round.
FIXTURE_PID=""
cleanup_fixture() {
  # Kill by BOTH the backgrounded pid and the pid the server reports in its own
  # JSON. They are normally the same, but a wrapper or re-exec would make them
  # differ — and a surviving fixture holds its ports and silently corrupts the
  # next run, so this is worth being paranoid about.
  local pids="$FIXTURE_PID"
  if [ -s "${RUN_DIR:-}/.fixture-ports.json" ]; then
    local rp
    rp=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('pid',''))" \
         "$RUN_DIR/.fixture-ports.json" 2>/dev/null || true)
    [ -n "$rp" ] && [ "$rp" != "$FIXTURE_PID" ] && pids="$pids $rp"
  fi
  for p in $pids; do
    [ -n "$p" ] || continue
    kill -0 "$p" 2>/dev/null || continue
    kill -TERM "$p" 2>/dev/null || true
    for _ in 1 2 3 4 5; do kill -0 "$p" 2>/dev/null || break; sleep 1; done
    kill -KILL "$p" 2>/dev/null || true
  done
  # The request log is the judge's evidence — keep it in the bundle.
  [ -d "${RUN_DIR:-}/.fixture-logs" ] && echo "=== fixture logs archived: $RUN_DIR/.fixture-logs" || true
}
trap cleanup_fixture EXIT INT TERM

SKELETON="$BENCH_DIR/tasks/$PROMPT_NAME-skeleton"
if [ -d "$SKELETON" ]; then
  echo "=== laying down skeleton: $SKELETON"
  ( cd "$SKELETON" && tar cf - . ) | ( cd "$RUN_DIR" && tar xf - )
fi

FIXTURE="$BENCH_DIR/fixtures/$PROMPT_NAME/server.py"
if [ -f "$FIXTURE" ]; then
  mkdir -p "$RUN_DIR/.fixture-logs"
  # Ports are OS-assigned (0) so back-to-back runs cannot collide; the server
  # prints the chosen ports as one JSON line, which becomes the model's config.
  python3 "$FIXTURE" --directory-port 0 --ledger-port 0 \
    --log-dir "$RUN_DIR/.fixture-logs" > "$RUN_DIR/.fixture-ports.json" 2>"$RUN_DIR/.fixture-stderr.log" &
  FIXTURE_PID=$!
  for _ in $(seq 1 30); do
    [ -s "$RUN_DIR/.fixture-ports.json" ] && break
    kill -0 "$FIXTURE_PID" 2>/dev/null || break
    sleep 0.5
  done
  if ! kill -0 "$FIXTURE_PID" 2>/dev/null || [ ! -s "$RUN_DIR/.fixture-ports.json" ]; then
    echo "FATAL: fixture services for '$PROMPT_NAME' failed to start — see $RUN_DIR/.fixture-stderr.log" >&2
    exit 1
  fi
  python3 - "$RUN_DIR" <<'PY'
import json, sys
d = sys.argv[1]
p = json.load(open(d + "/.fixture-ports.json"))
cfg = {"directory_url": p["directory_url"], "ledger_url": p["ledger_url"]}
json.dump(cfg, open(d + "/config.json", "w"), indent=2)
print(f"=== fixtures up: {cfg['directory_url']} (directory) · {cfg['ledger_url']} (ledger)")
PY
fi

git -C "$RUN_DIR" init -q

# ── Portable per-run timeout ─────────────────────────────────────────────────
# No `timeout`/`gtimeout` on stock macOS, so run a watchdog. An agent that hangs
# must not stall an unattended N=3 batch; a timed-out run is a RECORDED FAIL,
# never a crash and never a missing row.
AGENT_TIMEOUT="${AGENT_TIMEOUT:-1800}"
TIMED_OUT=0
run_with_timeout() {
  local secs="$1"; shift
  # `set -m` makes the background job a PROCESS-GROUP LEADER (pgid == pid), so
  # `kill -- -$pid` signals the agent CLI and all of its children. Signalling
  # the bare pid only kills the wrapper function's subshell and leaves the real
  # `claude`/`codex` process running — which then keeps writing session logs
  # into the NEXT run's detection window and corrupts its control arm.
  set -m
  "$@" & local pid=$!
  set +m
  ( sleep "$secs"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    sleep 10
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null ) &
  local wd=$!
  local rc=0
  wait "$pid" 2>/dev/null || rc=$?
  kill "$wd" 2>/dev/null || true; wait "$wd" 2>/dev/null || true
  # 143 = SIGTERM (watchdog), 137 = SIGKILL
  if [ "$rc" = "143" ] || [ "$rc" = "137" ]; then TIMED_OUT=1; fi
  return "$rc"
}

# ── Control arm (before) + run ───────────────────────────────────────────────
# NOTE ON `set -e`: the agent's exit code is captured, NEVER fatal. A crashed,
# refused, rate-limited or timed-out agent still gets judged and scored so the
# run appears in the results as a FAIL with its spend. Aborting here would drop
# failed runs from the record entirely — survivorship bias pointed straight at
# the "all runs are reported" commitment (PROTOCOL §4.1).
START=$(date +%s)
AGENT_RC=0
if [ "$TOOL" = "claude-code" ]; then
  # Claude Code munges the cwd path ('/' and '.' -> '-') into a project dir.
  PROJ_DIR="$HOME/.claude/projects/$(printf '%s' "$RUN_DIR" | tr '/.' '--')"
  if [ -d "$PROJ_DIR" ] && ls "$PROJ_DIR"/*.jsonl >/dev/null 2>&1; then
    echo "FATAL: control arm failed — pre-existing session JSONL under $PROJ_DIR" >&2
    exit 1
  fi
  echo "=== [$TOOL/$DIR_NAME/$PROMPT_NAME rep$REP] running claude ($MODEL_ID) ..."
  claude_leg() { ( cd "$RUN_DIR" && claude -p --model "$MODEL_ID" \
      --dangerously-skip-permissions "$TASK" ) >"$RUN_DIR/agent-output.txt" 2>&1; }
  run_with_timeout "$AGENT_TIMEOUT" claude_leg || AGENT_RC=$?
else
  SESS_ROOT="$HOME/.codex/sessions"
  MARKER="$BENCH_DIR/.session-marker.$$"   # harness state OUTSIDE the model's sandbox
  touch "$MARKER"; sleep 1
  echo "=== [$TOOL/$DIR_NAME/$PROMPT_NAME rep$REP] running codex exec ($MODEL_ID, sandboxed) ..."
  codex_leg() { codex exec -m "$MODEL_ID" -C "$RUN_DIR" -s workspace-write \
      --skip-git-repo-check --color never "$TASK" >"$RUN_DIR/agent-output.txt" 2>&1; }
  run_with_timeout "$AGENT_TIMEOUT" codex_leg || AGENT_RC=$?
fi
WALL=$(( $(date +%s) - START ))
tail -40 "$RUN_DIR/agent-output.txt" 2>/dev/null || true
if [ "$TIMED_OUT" = "1" ]; then
  echo "=== AGENT TIMEOUT after ${AGENT_TIMEOUT}s — run continues to scoring as a FAIL"
elif [ "$AGENT_RC" != "0" ]; then
  echo "=== AGENT EXIT $AGENT_RC (non-zero) — run continues to scoring, recorded as agent_exit=$AGENT_RC"
fi
echo "=== wall-clock: ${WALL}s"

# ── Control arm (after): exactly one new session log ─────────────────────────
# A missing/ambiguous log is a CAPTURE failure. It is recorded as an outcome
# (PROTOCOL §2 enum) rather than aborting the script, because aborting would
# delete the run from the record — the very bias this control arm exists to
# expose. More than one log is still fatal: we cannot attribute spend.
CAPTURE=OK
if [ "$TOOL" = "claude-code" ]; then
  COUNT=$(find "$PROJ_DIR" -maxdepth 1 -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
  SESSION_JSONL=$(find "$PROJ_DIR" -maxdepth 1 -name '*.jsonl' | head -1)
  FORMAT=claude
else
  NEW_LOGS=$(find "$SESS_ROOT" -name 'rollout-*.jsonl' -newer "$MARKER" 2>/dev/null)
  COUNT=$(printf '%s\n' "$NEW_LOGS" | grep -c . || true)
  SESSION_JSONL=$(printf '%s\n' "$NEW_LOGS" | head -1)
  FORMAT=codex
  rm -f "$MARKER"
fi
if [ "$COUNT" -gt 1 ]; then
  # Spend genuinely cannot be attributed across two logs — but this must not
  # abort an unattended batch, and the run must still appear in the record.
  echo "=== CAPTURE FAILURE: $COUNT session logs matched — spend unattributable" >&2
  CAPTURE=AMBIGUOUS_LOG
elif [ "$COUNT" = "0" ]; then
  CAPTURE=NO_SESSION_LOG
  echo "=== CAPTURE FAILURE: no session log (agent_exit=$AGENT_RC, timed_out=$TIMED_OUT)"
else
  echo "=== session log: $SESSION_JSONL"
  # Archive the spend evidence INTO the run bundle. Tool session stores are
  # pruned and live outside the archive; without this copy the sole evidence for
  # the $ half of the headline metric is not reproducible by a reader.
  cp "$SESSION_JSONL" "$RUN_DIR/session.jsonl"
  shasum -a 256 "$RUN_DIR/session.jsonl" | awk '{print $1}' > "$RUN_DIR/session.jsonl.sha256"
fi

# ── (a) model's own tests ────────────────────────────────────────────────────
if ( cd "$RUN_DIR" && python3 -m unittest "$TEST_MODULE" ) >"$RUN_DIR/own-tests.log" 2>&1; then
  OWN=PASS
else
  OWN=FAIL
fi
echo "=== own tests: $OWN ($TEST_MODULE)"

# ── (b) independent acceptance suite ─────────────────────────────────────────
set +e
python3 "$JUDGE_PATH" "$RUN_DIR" >"$RUN_DIR/acceptance.log" 2>&1
ACC_RC=$?
set -e
if [ "$ACC_RC" -eq 0 ]; then ACC=PASS; else ACC=FAIL; fi
ACC_LINE=$(grep '^ACCEPTANCE_RESULT' "$RUN_DIR/acceptance.log" || true)
EDGE=$(printf '%s' "$ACC_LINE" | sed -En 's/.*failures=([0-9]+) errors=([0-9]+).*/\1f+\2e/p')
EDGE="${EDGE:-?}"
echo "=== acceptance: $ACC ($ACC_LINE)"

# ── Reproducibility stamp (every published report must carry these) ──────────
# The instrument version comes from the per-edition PIN and the VENDORED rate
# table — never from a live sibling working tree. Reading tier's git HEAD made
# an edition's stamp drift with unrelated commits to that repo (two legs of one
# edition could stamp different versions), and made the bench unrunnable
# outside the tier checkout.
PIN_FILE="$BENCH_DIR/TIER_PIN"
[ -f "$PIN_FILE" ] || { echo "FATAL: missing $PIN_FILE (the edition's instrument pin)" >&2; exit 1; }
TIER_VERSION="$(awk '/^tier_version:/{print $2}' "$PIN_FILE")"
PRICES_FILE="$BENCH_DIR/prices.yaml"
[ -f "$PRICES_FILE" ] || { echo "FATAL: missing vendored $PRICES_FILE" >&2; exit 1; }
# The pin's sha is the integrity check on the vendored table: if they disagree,
# the table changed under the edition and every cost figure is suspect.
PIN_SHA="$(awk '/^prices_yaml_sha256:/{print $2}' "$PIN_FILE")"
ACTUAL_SHA="$(shasum -a 256 "$PRICES_FILE" | awk '{print $1}')"
[ "$PIN_SHA" = "$ACTUAL_SHA" ] || {
  echo "FATAL: prices.yaml sha mismatch — TIER_PIN says $PIN_SHA, file is $ACTUAL_SHA" >&2
  echo "       the rate table changed mid-edition; re-pin deliberately or restore it." >&2
  exit 1; }
PROMPT_SHA="$(shasum -a 256 "$PROMPT_FILE" | awk '{print $1}')"
PRICES_SHA="$(shasum -a 256 "$PRICES_FILE" | awk '{print $1}')"
# The judge hash is the commit-reveal witness (PROTOCOL §4.5): it proves the
# judge was fixed BEFORE the runs and not tuned to the results. Stamping it is
# what makes that guarantee a mechanism instead of an assertion.
JUDGE_SHA="$(shasum -a 256 "$JUDGE_PATH" | awk '{print $1}')"
PY_VERSION="$(python3 -c 'import platform;print(platform.python_version())' 2>/dev/null || echo unknown)"
if [ "$TOOL" = "claude-code" ]; then
  TOOL_VERSION="$(claude --version 2>/dev/null | head -1 || echo unknown)"
else
  TOOL_VERSION="$(codex --version 2>/dev/null | head -1 || echo unknown)"
fi
# Snapshot the rate table INTO the bundle — a sha proves which table was used,
# but only the bytes let a reader re-derive the cost years later.
cp "$PRICES_FILE" "$RUN_DIR/prices.snapshot.yaml"
cat > "$RUN_DIR/stamp.json" <<STAMP
{
  "date_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "tier_version": "$TIER_VERSION",
  "prices_yaml_sha256": "$PRICES_SHA",
  "prompt": "$PROMPT_NAME",
  "prompt_file_sha256": "$PROMPT_SHA",
  "judge": "$JUDGE_FILE",
  "judge_sha256": "$JUDGE_SHA",
  "python_version": "$PY_VERSION",
  "tool": "$TOOL",
  "tool_version": "$TOOL_VERSION",
  "model_flag": "$MODEL_ID",
  "rep": $REP,
  "agent_exit": $AGENT_RC,
  "timed_out": $( [ "$TIMED_OUT" = "1" ] && echo true || echo false ),
  "capture": "$CAPTURE",
  "machine": "$(uname -srm) / $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
}
STAMP
echo "=== stamp: $RUN_DIR/stamp.json (TIER $TIER_VERSION, judge ${JUDGE_SHA:0:12}…)"

# Archive the model's actual submission. This is what makes a judge defect
# discovered later a FREE offline replay against the original artifacts,
# instead of a choice between editing a published version and living with a
# known-wrong number.
mkdir -p "$RUN_DIR/submission"
for f in "$RUN_DIR"/*.py; do [ -e "$f" ] && cp "$f" "$RUN_DIR/submission/" || true; done

# ── (c) spend + diagnostics row (score.py enforces spend > 0) ────────────────
# shellcheck disable=SC2086  # EXTRA_SCORE_FLAGS is intentionally word-split
# Scored tasks are OFFLINE: --web-tools void makes score.py VOID the run (loud)
# if any WebSearch/WebFetch (claude) or web_search (codex) appears in the log.
# A future web-permitted "web-dig" task passes --web-tools record instead.
SCORE_RC=0
if [ "$CAPTURE" = "OK" ]; then
  # score.py failure is NOT fatal. A run that died before emitting any usage
  # event still produced a session log, so CAPTURE looks OK, but there is
  # nothing to price — and aborting here would delete the run from the record,
  # which is the same survivorship bias the agent-exit fix above closes.
  set +e
  python3 "$BENCH_DIR/score.py" --format "$FORMAT" --log "$RUN_DIR/session.jsonl" \
    --prices "$PRICES_FILE" \
    --model-key "$PRICE_KEY" --label "$DIR_NAME" --tool "$TOOL" \
    --own-tests "$OWN" --acceptance "$ACC" --edge-failures "$EDGE" --wall "$WALL" \
    --web-tools void \
    $EXTRA_SCORE_FLAGS \
    --out "$RUN_DIR/result.json" 2>"$RUN_DIR/result.stderr.json"
  SCORE_RC=$?
  set -e
  if [ "$SCORE_RC" != "0" ]; then
    CAPTURE="SCORE_FAILED: $(head -1 "$RUN_DIR/result.stderr.json" 2>/dev/null | tr -d '"' | cut -c1-120)"
    echo "=== SCORING FAILED (rc=$SCORE_RC) — recording the run with no cost figure"
    echo "    $CAPTURE"
  fi
fi
if [ "$CAPTURE" = "OK" ]; then
  # Stamp the run-level facts score.py doesn't know about, so every consumer
  # reads one file. A run is only PASS if the judge passed AND capture was clean.
  python3 - "$RUN_DIR" "$REP" "$AGENT_RC" "$TIMED_OUT" "$CAPTURE" "$PROMPT_NAME" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]+"/result.json"))
d.update(rep=int(sys.argv[2]), agent_exit=int(sys.argv[3]),
         timed_out=sys.argv[4]=="1", capture=sys.argv[5], task=sys.argv[6],
         session_log="session.jsonl", prices_snapshot="prices.snapshot.yaml")
d["outcome"] = "PASS" if (d.get("acceptance")=="PASS" and sys.argv[5]=="OK"
                          and sys.argv[4]!="1") else "FAIL"
json.dump(d, open(sys.argv[1]+"/result.json","w"), indent=2)
print(f"=== outcome: {d['outcome']}  ${d.get('cost_list_usd',0):.4f}")
PY
else
  # Capture failed: no spend evidence exists, so there is no cost figure. The
  # run STILL gets a row — a dropped run is invisible cherry-picking.
  python3 - "$RUN_DIR" "$REP" "$AGENT_RC" "$TIMED_OUT" "$CAPTURE" "$PROMPT_NAME" \
           "$TOOL" "$DIR_NAME" "$OWN" "$ACC" "$EDGE" "$WALL" <<'PY'
import json,sys
a=sys.argv
json.dump({"tool":a[7],"label":a[8],"task":a[6],"rep":int(a[2]),
           "own_tests":a[9],"acceptance":a[10],"edge_failures":a[11],
           "wall_seconds":int(a[12]),"agent_exit":int(a[3]),
           "timed_out":a[4]=="1","capture":a[5],
           "cost_list_usd":None,"priced":False,"outcome":"FAIL"},
          open(a[1]+"/result.json","w"), indent=2)
PY
  echo "=== outcome: FAIL (capture=$CAPTURE) — recorded with no cost figure"
fi

echo "=== done: $RUN_DIR/result.json"
