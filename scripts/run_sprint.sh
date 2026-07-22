#!/bin/bash
# ============================================================
# run_sprint.sh — autonomous sprint execution wrapper
#
# USAGE:
#   ./scripts/run_sprint.sh sprint23.structural
#   ./scripts/run_sprint.sh sprint24.lowrisk
#
# NAMING CONVENTION (this is how risk tier is set — nothing
# else in this script infers risk automatically):
#   sprint_prompts/<name>.lowrisk.md    -> auto-merges to main
#                                          on green verify
#   sprint_prompts/<name>.structural.md -> commits + pushes the
#                                          feature branch, then
#                                          STOPS for your manual
#                                          review before merge
#
# WHAT THIS DOES NOT DO:
#   - It does not design the sprint. Part 1 (SQL) and Part 2
#     (branch confirm) still happen in chat with Claude, and
#     you still apply Part 1 SQL in Supabase yourself before
#     running this script.
#   - It does not touch production. DATABASE_URL must point at
#     the dev project only — this script trusts whatever
#     apps/api/.env is already configured to.
#   - It does not use --dangerously-skip-permissions. Tool
#     access is explicitly scoped below.
#
# PREREQUISITES:
#   - .mcp.json at repo root, already configured (Supabase dev)
#   - .claude/commands/refresh-schema.md already in place
#   - apps/api/.env with DATABASE_URL (dev project)
#   - jq installed (sudo apt install jq / brew install jq)
#   - The sprint prompt file saved at:
#       sprint_prompts/<name>.md
#     (see naming convention above for the risk-tier suffix)
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
DECISION_LOG="$LOG_DIR/decision_log.jsonl"   # seeds the future TaskRouter (S27) decision log

# ---- Determine risk tier from filename ----
if [[ "$SPRINT_NAME" == *".lowrisk"* ]]; then
  RISK_TIER="lowrisk"
elif [[ "$SPRINT_NAME" == *".structural"* ]]; then
  RISK_TIER="structural"
else
  echo "ERROR: sprint name must contain '.lowrisk' or '.structural' so risk tier is explicit." >&2
  echo "       e.g. sprint23.structural  or  sprint24.lowrisk" >&2
  exit 1
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "ERROR: prompt file not found: $PROMPT_FILE" >&2
  echo "       Save the sprint's Part 3 prompt there first." >&2
  exit 1
fi

echo "=================================================="
echo " Sprint:     $SPRINT_NAME"
echo " Risk tier:  $RISK_TIER"
echo " Prompt:     $PROMPT_FILE"
echo "=================================================="

# ---- Tool scope: explicit allow-list, never a bare bypass ----
ALLOWED_TOOLS="Read,Write,Edit,Bash(python scripts/*),Bash(git add*),Bash(git commit*),Bash(git push*),Bash(git checkout*),Bash(git merge*),Bash(git fetch*),mcp__supabase-2ndact-dev"

# ============================================================
# STEP 1 — refresh the schema snapshot
# ============================================================
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
  echo "FATAL: refresh-schema reported an error. See $LOG_DIR/${SPRINT_NAME}.refresh.json" >&2
  exit 1
fi
echo "Schema refresh OK. (Diff, if any, is expected — sprints change schema on purpose.)"

# ============================================================
# STEP 2 — run the sprint's Part 3 prompt headlessly
# ============================================================
echo ""
echo "--- Step 2: sprint execution (Part 3) ---"
sprint_result=$(timeout 1800 claude -p "$(cat "$PROMPT_FILE")" \
  --permission-mode acceptEdits \
  --allowedTools "$ALLOWED_TOOLS" \
  --max-turns 60 \
  --output-format json 2>"$LOG_DIR/${SPRINT_NAME}.sprint.err")
sprint_status=$?

echo "$sprint_result" > "$LOG_DIR/${SPRINT_NAME}.sprint.json"

if [[ $sprint_status -eq 124 ]]; then
  echo "FATAL: sprint run timed out (30 min cap)." >&2
  exit 1
fi
if [[ $sprint_status -ne 0 ]]; then
  echo "FATAL: sprint run crashed (exit $sprint_status)." >&2
  cat "$LOG_DIR/${SPRINT_NAME}.sprint.err" >&2
  exit 1
fi

sprint_is_error=$(echo "$sprint_result" | jq -r '.is_error // "unknown"')
sprint_cost=$(echo "$sprint_result" | jq -r '.total_cost_usd // "n/a"')
sprint_duration=$(echo "$sprint_result" | jq -r '.duration_ms // "n/a"')
sprint_turns=$(echo "$sprint_result" | jq -r '.num_turns // "n/a"')

echo "Sprint run complete. cost=\$${sprint_cost}  duration_ms=${sprint_duration}  turns=${sprint_turns}"

if [[ "$sprint_is_error" == "true" ]]; then
  echo "FATAL: Claude Code reported an error during the sprint run." >&2
  echo "$sprint_result" | jq -r '.result' >&2
  exit 1
fi

# ============================================================
# STEP 3 — run the sprint's verify script (pass/fail gate)
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
    --arg cost "$sprint_cost" --arg dur "$sprint_duration" \
    --arg turns "$sprint_turns" --arg result "verify_failed" \
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

# ============================================================
# STEP 5 — decision log (seeds the future TaskRouter S27 log)
# ============================================================
log_line=$(jq -n \
  --arg sprint "$SPRINT_NAME" --arg tier "$RISK_TIER" \
  --arg cost "$sprint_cost" --arg dur "$sprint_duration" \
  --arg turns "$sprint_turns" --arg result "$result_label" \
  --arg ts "$(date -u +%FT%TZ)" \
  '{timestamp:$ts, sprint:$sprint, risk_tier:$tier, cost_usd:$cost, duration_ms:$dur, turns:$turns, result:$result}')
echo "$log_line" >> "$DECISION_LOG"

echo ""
echo "=================================================="
echo " Done. Logged to $DECISION_LOG"
echo "=================================================="
