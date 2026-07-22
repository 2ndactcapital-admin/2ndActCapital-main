#!/bin/bash
# ============================================================
# run_sprint.sh — autonomous sprint execution wrapper (v2)
#
# USAGE:
#   ./scripts/run_sprint.sh sprint23.structural
#   ./scripts/run_sprint.sh sprint24.lowrisk
#
# WHAT'S NEW IN V2 (fixes a real incident from Sprint 23):
#   Step 2 now runs claude -p DETACHED via nohup, writing
#   straight to a log file instead of being captured through
#   $(...) command substitution in the foreground. This means:
#     - A frozen or interrupted TERMINAL can no longer sever
#       the running job or lose its output. The job survives
#       independently of this script/terminal.
#     - You can watch progress live in another window with:
#         tail -f sprint_prompts/logs/<name>.sprint.log
#     - If you Ctrl+C this script, the sprint keeps running in
#       the background — check on it with the STATUS command
#       below rather than assuming it died.
#
# STATUS / RECOVERY (if this script itself gets interrupted):
#   Check if a sprint is still running:
#     ps aux | grep "claude -p"
#   Watch its output live:
#     tail -f sprint_prompts/logs/<name>.sprint.log
#   Once it finishes (process no longer in ps aux), re-run this
#   same command — it detects a completed-but-unprocessed run
#   and picks up at Step 3 (verify) instead of restarting Step 2.
#
# NAMING CONVENTION:
#   sprint_prompts/<name>.lowrisk.md    -> auto-merges to main
#                                          on green verify
#   sprint_prompts/<name>.structural.md -> commits + pushes the
#                                          feature branch, then
#                                          STOPS for manual review
#
# WHAT THIS DOES NOT DO:
#   - Does not design the sprint (Part 1/Part 2 still in chat).
#   - Does not touch production (trusts apps/api/.env DATABASE_URL).
#   - Does not use --dangerously-skip-permissions.
#
# PREREQUISITES: same as v1 — .mcp.json, refresh-schema command,
# apps/api/.env, jq, sprint prompt file saved.
# ============================================================

set -uo pipefail

SPRINT_NAME="${1:-}"
if [[ -z "$SPRINT_NAME" ]]; then
  echo "Usage: $0 <sprint_name>  (e.g. sprint23.structural or sprint24.lowrisk)" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROMPT_FILE="sprint_prompts/${SPRINT_NAME}.md"
LOG_DIR="sprint_prompts/logs"
mkdir -p "$LOG_DIR"
DECISION_LOG="$LOG_DIR/decision_log.jsonl"
SPRINT_LOG="$LOG_DIR/${SPRINT_NAME}.sprint.log"
SPRINT_JSON="$LOG_DIR/${SPRINT_NAME}.sprint.json"
SPRINT_PID_FILE="$LOG_DIR/${SPRINT_NAME}.pid"
SPRINT_DONE_FILE="$LOG_DIR/${SPRINT_NAME}.done"

if [[ "$SPRINT_NAME" == *".lowrisk"* ]]; then
  RISK_TIER="lowrisk"
elif [[ "$SPRINT_NAME" == *".structural"* ]]; then
  RISK_TIER="structural"
else
  echo "ERROR: sprint name must contain '.lowrisk' or '.structural'." >&2
  exit 1
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "ERROR: prompt file not found: $PROMPT_FILE" >&2
  exit 1
fi

echo "=================================================="
echo " Sprint:     $SPRINT_NAME"
echo " Risk tier:  $RISK_TIER"
echo " Prompt:     $PROMPT_FILE"
echo "=================================================="

ALLOWED_TOOLS="Read,Write,Edit,Bash(python scripts/*),Bash(git add*),Bash(git commit*),Bash(git push*),Bash(git checkout*),Bash(git merge*),Bash(git fetch*),mcp__supabase-2ndact-dev"

# ============================================================
# RECOVERY CHECK — if a previous run already finished (the
# .done marker exists) but never got processed past Step 2
# (e.g. this script was killed before reaching verify), skip
# straight to Step 3 instead of re-running the sprint.
# ============================================================
if [[ -f "$SPRINT_DONE_FILE" && -f "$SPRINT_JSON" ]]; then
  echo ""
  echo ">>> Found a completed prior run for $SPRINT_NAME that was"
  echo ">>> never processed (likely an interrupted script, not a"
  echo ">>> failed sprint). Skipping schema-refresh and re-running"
  echo ">>> Step 2 — jumping straight to verify."
  echo ">>> Delete $SPRINT_DONE_FILE first if you want a full re-run."
  sprint_cost=$(jq -r '.total_cost_usd // "n/a"' "$SPRINT_JSON" 2>/dev/null || echo "n/a")
  sprint_duration=$(jq -r '.duration_ms // "n/a"' "$SPRINT_JSON" 2>/dev/null || echo "n/a")
  sprint_turns=$(jq -r '.num_turns // "n/a"' "$SPRINT_JSON" 2>/dev/null || echo "n/a")
  goto_verify=true
else
  goto_verify=false
fi

if [[ "$goto_verify" == "false" ]]; then

  echo ""
  echo "--- Step 1: refresh-schema ---"
  refresh_result=$(timeout 300 claude -p "/refresh-schema" \
    --permission-mode acceptEdits \
    --allowedTools "$ALLOWED_TOOLS" \
    --output-format json 2>"$LOG_DIR/${SPRINT_NAME}.refresh.err")
  refresh_status=$?

  echo "$refresh_result" > "$LOG_DIR/${SPRINT_NAME}.refresh.json"

  if [[ $refresh_status -ne 0 ]]; then
    echo "FATAL: refresh-schema step crashed (exit $refresh_status)." >&2
    cat "$LOG_DIR/${SPRINT_NAME}.refresh.err" >&2
    exit 1
  fi

  refresh_is_error=$(echo "$refresh_result" | jq -r '.is_error // "unknown"')
  if [[ "$refresh_is_error" == "true" ]]; then
    echo "FATAL: refresh-schema reported an error." >&2
    exit 1
  fi
  echo "Schema refresh OK."

  # ==========================================================
  # STEP 2 — DETACHED execution. This is the fix: claude -p
  # runs via nohup, writing to a log file, backgrounded, with
  # its PID recorded. This script then WAITS on that PID, but
  # if the script itself is killed, the background job survives
  # and keeps writing to SPRINT_LOG regardless.
  # ==========================================================
  echo ""
  echo "--- Step 2: sprint execution (Part 3) — DETACHED ---"
  echo "    Live log: $SPRINT_LOG"
  echo "    Watch it in another terminal with:"
  echo "      tail -f $SPRINT_LOG"
  echo ""

  rm -f "$SPRINT_DONE_FILE"

  nohup claude -p "$(cat "$PROMPT_FILE")" \
    --permission-mode acceptEdits \
    --allowedTools "$ALLOWED_TOOLS" \
    --max-turns 60 \
    --output-format json \
    > "$SPRINT_JSON" 2>"$SPRINT_LOG" &

  SPRINT_PID=$!
  echo "$SPRINT_PID" > "$SPRINT_PID_FILE"
  echo "Running as PID $SPRINT_PID (detached — survives this terminal)."

  # Poll instead of a blind `wait`, so we can print a heartbeat
  # every 30s and you're never staring at total silence.
  elapsed=0
  while kill -0 "$SPRINT_PID" 2>/dev/null; do
    sleep 30
    elapsed=$((elapsed + 30))
    echo "    ...still running (${elapsed}s elapsed, PID $SPRINT_PID). Tail $SPRINT_LOG for detail."
    if [[ $elapsed -ge 1800 ]]; then
      echo "FATAL: sprint exceeded 30 min cap. Killing PID $SPRINT_PID." >&2
      kill -9 "$SPRINT_PID" 2>/dev/null || true
      exit 1
    fi
  done

  wait "$SPRINT_PID" 2>/dev/null
  sprint_status=$?
  touch "$SPRINT_DONE_FILE"
  rm -f "$SPRINT_PID_FILE"

  if [[ $sprint_status -ne 0 ]]; then
    echo "FATAL: sprint run exited with status $sprint_status." >&2
    echo "See $SPRINT_LOG for detail." >&2
    exit 1
  fi

  if [[ ! -s "$SPRINT_JSON" ]]; then
    echo "FATAL: sprint finished but $SPRINT_JSON is empty. Check $SPRINT_LOG." >&2
    exit 1
  fi

  sprint_is_error=$(jq -r '.is_error // "unknown"' "$SPRINT_JSON")
  sprint_cost=$(jq -r '.total_cost_usd // "n/a"' "$SPRINT_JSON")
  sprint_duration=$(jq -r '.duration_ms // "n/a"' "$SPRINT_JSON")
  sprint_turns=$(jq -r '.num_turns // "n/a"' "$SPRINT_JSON")

  echo "Sprint run complete. cost=\$${sprint_cost}  duration_ms=${sprint_duration}  turns=${sprint_turns}"

  if [[ "$sprint_is_error" == "true" ]]; then
    echo "FATAL: Claude Code reported an error during the sprint run." >&2
    jq -r '.result' "$SPRINT_JSON" >&2
    exit 1
  fi

fi  # end goto_verify skip

# ============================================================
# STEP 3 — verify
# ============================================================
echo ""
echo "--- Step 3: verify ---"
BASE_NAME="${SPRINT_NAME%%.*}"
VERIFY_SCRIPT="apps/api/scripts/verify_${BASE_NAME}.py"
if [[ ! -f "$VERIFY_SCRIPT" ]]; then
  echo "FATAL: expected verify script not found at $VERIFY_SCRIPT" >&2
  exit 1
fi

(
  cd apps/api
  set -a && source .env && set +a
  set +u
  source venv/bin/activate
  set -u
  python "scripts/verify_${BASE_NAME}.py"
)
verify_status=$?

if [[ $verify_status -ne 0 ]]; then
  echo "VERIFY FAILED (exit $verify_status). Stopping — nothing merged." >&2
  log_line=$(jq -n \
    --arg sprint "$SPRINT_NAME" --arg tier "$RISK_TIER" \
    --arg cost "${sprint_cost:-n/a}" --arg dur "${sprint_duration:-n/a}" \
    --arg turns "${sprint_turns:-n/a}" --arg result "verify_failed" \
    --arg ts "$(date -u +%FT%TZ)" \
    '{timestamp:$ts, sprint:$sprint, risk_tier:$tier, cost_usd:$cost, duration_ms:$dur, turns:$turns, result:$result}')
  echo "$log_line" >> "$DECISION_LOG"
  exit 1
fi

echo "VERIFY PASSED."

# ============================================================
# STEP 4 — risk-tiered merge behavior
# ============================================================
echo ""
echo "--- Step 4: merge (risk tier: $RISK_TIER) ---"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [[ "$RISK_TIER" == "lowrisk" ]]; then
  git add -A
  git commit -m "sprint: ${SPRINT_NAME} - verified green, auto-merged (lowrisk)" || true
  git push origin "$CURRENT_BRANCH"
  git checkout main
  git pull origin main
  git merge "$CURRENT_BRANCH" --no-edit
  git push origin main
  git checkout "$CURRENT_BRANCH"
  echo "Auto-merged to main (lowrisk sprint, verify passed)."
  result_label="auto_merged"
else
  git add -A
  git commit -m "sprint: ${SPRINT_NAME} - verified green, HELD for manual review (structural)" || true
  git push origin "$CURRENT_BRANCH"
  echo ""
  echo ">>> STRUCTURAL sprint - verify passed and pushed to $CURRENT_BRANCH, but NOT merged to main."
  echo ">>> Review manually, then run your usual Part 4 merge."
  result_label="held_for_review"
fi

rm -f "$SPRINT_DONE_FILE"

log_line=$(jq -n \
  --arg sprint "$SPRINT_NAME" --arg tier "$RISK_TIER" \
  --arg cost "${sprint_cost:-n/a}" --arg dur "${sprint_duration:-n/a}" \
  --arg turns "${sprint_turns:-n/a}" --arg result "$result_label" \
  --arg ts "$(date -u +%FT%TZ)" \
  '{timestamp:$ts, sprint:$sprint, risk_tier:$tier, cost_usd:$cost, duration_ms:$dur, turns:$turns, result:$result}')
echo "$log_line" >> "$DECISION_LOG"

echo ""
echo "=================================================="
echo " Done. Logged to $DECISION_LOG"
echo "=================================================="
