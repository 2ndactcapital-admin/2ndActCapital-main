#!/bin/bash
# ============================================================
# run_sprint.sh — autonomous sprint execution wrapper (v3)
#
# USAGE:
#   ./scripts/run_sprint.sh sprint23.structural
#   ./scripts/run_sprint.sh sprint24.structural 150
#                                            ^^^ optional max-turns
#                                                override (default 100)
#
# WHAT'S NEW IN V3 (fixes a real incident from Sprint 24):
#   - --max-turns is now configurable (arg 2), default raised
#     from 60 to 100. Sprint 24 hit the old 60-turn cap partway
#     through a large sweep task and stopped with error_max_turns
#     — real, good work had already been done (RBAC, settings
#     service, theme provider), it just ran out of turns.
#   - AUTOMATIC RESUME on error_max_turns: rather than restarting
#     the whole sprint (re-doing already-completed tasks and
#     re-spending on them), this now uses `claude --resume
#     <session_id>` to continue the SAME session up to 3 times,
#     each with a fresh max-turns budget. Claude Code's context
#     caching means a resume is far cheaper than a cold restart.
#   - Cost and turns are now ACCUMULATED across every resume
#     attempt and logged as sprint totals, not just the last leg.
#
# (v2 fix retained: Step 2 runs detached via nohup, survives a
#  frozen/interrupted terminal, heartbeats every 30s.)
#
# STATUS / RECOVERY:
#   ps aux | grep "claude -p"
#   tail -f sprint_prompts/logs/<name>.sprint.log
#
# NAMING CONVENTION:
#   sprint_prompts/<name>.lowrisk.md    -> auto-merges to main
#   sprint_prompts/<name>.structural.md -> holds for manual review
#
# WHAT THIS DOES NOT DO: design the sprint, touch production,
# or use --dangerously-skip-permissions.
# ============================================================

set -uo pipefail

SPRINT_NAME="${1:-}"
MAX_TURNS="${2:-100}"
RESUME_LIMIT=3

if [[ -z "$SPRINT_NAME" ]]; then
  echo "Usage: $0 <sprint_name> [max_turns]  (e.g. sprint24.structural 150)" >&2
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
echo " Max turns:  $MAX_TURNS (per leg; auto-resumes up to $RESUME_LIMIT times on max-turns)"
echo " Prompt:     $PROMPT_FILE"
echo "=================================================="

ALLOWED_TOOLS="Read,Write,Edit,Bash(cd*),Bash(python*),Bash(npm*),Bash(git*),mcp__supabase-2ndact-dev"
TOTAL_COST="0"
TOTAL_TURNS="0"

# ============================================================
# STEP 1 — refresh the schema snapshot
# ============================================================
echo ""
echo "--- Step 1: refresh-schema ---"
refresh_result=$(timeout 300 claude -p "/refresh-schema" \
  --permission-mode acceptEdits \
  --allowedTools "$

ALLOWED_TOOLS" \
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

# ============================================================
# STEP 2 — sprint execution, detached, with auto-resume on
# max-turns.
# ============================================================
echo ""
echo "--- Step 2: sprint execution (Part 3) — DETACHED ---"
echo "    Live log: $SPRINT_LOG"
echo "    Watch it in another terminal with:"
echo "      tail -f $SPRINT_LOG"
echo ""

rm -f "$SPRINT_DONE_FILE"

run_one_leg() {
  local prompt_arg="$1"
  local resume_session="${2:-}"
  local leg_json="$LOG_DIR/${SPRINT_NAME}.sprint.leg.json"

  if [[ -n "$resume_session" ]]; then
    nohup claude --resume "$resume_session" -p "$prompt_arg" \
      --permission-mode acceptEdits \
      --allowedTools "$ALLOWED_TOOLS" \
      --max-turns "$MAX_TURNS" \
      --output-format json \
      > "$leg_json" 2>>"$SPRINT_LOG" &
  else
    nohup claude -p "$prompt_arg" \
      --permission-mode acceptEdits \
      --allowedTools "$ALLOWED_TOOLS" \
      --max-turns "$MAX_TURNS" \
      --output-format json \
      > "$leg_json" 2>>"$SPRINT_LOG" &
  fi

  local pid=$!
  local elapsed=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 30
    elapsed=$((elapsed + 30))
    echo "    ...still running (${elapsed}s elapsed, PID $pid, leg budget ${MAX_TURNS} turns). Tail $SPRINT_LOG for detail."
    if [[ $elapsed -ge 1800 ]]; then
      echo "FATAL: leg exceeded 30 min cap. Killing PID $pid." >&2
      kill -9 "$pid" 2>/dev/null || true
      return 2
    fi
  done
  wait "$pid" 2>/dev/null
  return $?
}

attempt=0
resume_id=""
prompt_text="$(cat "$PROMPT_FILE")"
final_status=1

while [[ $attempt -le $RESUME_LIMIT ]]; do
  if [[ $attempt -eq 0 ]]; then
    echo ">>> Leg 1 of up to $((RESUME_LIMIT + 1)) (fresh run)"
    run_one_leg "$prompt_text"
    leg_status=$?
  else
    echo ">>> Resume leg $((attempt + 1)) of $((RESUME_LIMIT + 1)) — continuing session $resume_id"
    run_one_leg "Continue exactly where you left off. You hit the turn limit mid-sprint. First run 'git status' and 'git diff --stat' to see what you already changed — do NOT redo completed tasks. Then finish all remaining tasks and run the full verification steps." "$resume_id"
    leg_status=$?
  fi

  leg_json="$LOG_DIR/${SPRINT_NAME}.sprint.leg.json"
  if [[ ! -s "$leg_json" ]]; then
    echo "FATAL: leg produced no output ($leg_json empty). See $SPRINT_LOG." >&2
    exit 1
  fi

  leg_cost=$(jq -r '.total_cost_usd // 0' "$leg_json")
  leg_turns=$(jq -r '.num_turns // 0' "$leg_json")
  leg_subtype=$(jq -r '.subtype // "unknown"' "$leg_json")
  leg_is_error=$(jq -r '.is_error // "unknown"' "$leg_json")
  resume_id=$(jq -r '.session_id // empty' "$leg_json")

  TOTAL_COST=$(awk "BEGIN{print $TOTAL_COST + $leg_cost}")
  TOTAL_TURNS=$(awk "BEGIN{print $TOTAL_TURNS + $leg_turns}")
  cp "$leg_json" "$SPRINT_JSON"

  echo "    Leg result: subtype=$leg_subtype is_error=$leg_is_error cost=\$${leg_cost} turns=${leg_turns}"
  echo "    Running totals: cost=\$${TOTAL_COST} turns=${TOTAL_TURNS}"

  if [[ "$leg_subtype" == "error_max_turns" && $attempt -lt $RESUME_LIMIT ]]; then
    echo "    Hit max-turns — auto-resuming (attempt $((attempt + 2)))."
    attempt=$((attempt + 1))
    continue
  fi

  if [[ "$leg_is_error" == "true" ]]; then
    echo "FATAL: sprint leg reported an error ($leg_subtype)." >&2
    jq -r '.result // empty' "$leg_json" >&2
    final_status=1
  else
    final_status=0
  fi
  break
done

touch "$SPRINT_DONE_FILE"

if [[ $final_status -ne 0 ]]; then
  echo "FATAL: sprint did not complete cleanly after $((attempt + 1)) leg(s). Total cost so far: \$${TOTAL_COST}." >&2
  log_line=$(jq -n \
    --arg sprint "$SPRINT_NAME" --arg tier "$RISK_TIER" \
    --arg cost "$TOTAL_COST" --arg turns "$TOTAL_TURNS" \
    --arg result "incomplete_after_resumes" --arg ts "$(date -u +%FT%TZ)" \
    '{timestamp:$ts, sprint:$sprint, risk_tier:$tier, cost_usd:$cost, turns:$turns, result:$result}')
  echo "$log_line" >> "$DECISION_LOG"
  exit 1
fi

echo "Sprint run complete across $((attempt + 1)) leg(s). Total cost=\$${TOTAL_COST}  Total turns=${TOTAL_TURNS}"

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
    --arg cost "$TOTAL_COST" --arg turns "$TOTAL_TURNS" \
    --arg result "verify_failed" --arg ts "$(date -u +%FT%TZ)" \
    '{timestamp:$ts, sprint:$sprint, risk_tier:$tier, cost_usd:$cost, turns:$turns, result:$result}')
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
  --arg cost "$TOTAL_COST" --arg turns "$TOTAL_TURNS" \
  --arg result "$result_label" --arg ts "$(date -u +%FT%TZ)" \
  '{timestamp:$ts, sprint:$sprint, risk_tier:$tier, cost_usd:$cost, turns:$turns, result:$result}')
echo "$log_line" >> "$DECISION_LOG"

echo ""
echo "=================================================="
echo " Done. Total cost \$${TOTAL_COST} across $((attempt + 1)) leg(s). Logged to $DECISION_LOG"
echo "=================================================="
