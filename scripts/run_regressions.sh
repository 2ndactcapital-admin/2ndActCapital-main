#!/usr/bin/env bash
# run_regressions.sh — the full regression suite, run on the OPERATOR's schedule
# (weekly / before a big push), never inside a sprint's own verify (bulkfetchui3;
# docs/SPRINT_WORKFLOW_STANDARD.md, "Regression").
#
#     bash scripts/run_regressions.sh
#
# Runs every script named in scripts/regression_set.txt (one BARE verify_*.py name
# per line, resolved in apps/api/scripts/; blank lines and # comments ignored),
# ONE AFTER ANOTHER, in the foreground, each as
#
#     VERIFY_NESTED=1 timeout 1800 doppler run -p hollisworks -c prd -- \
#         apps/api/venv/bin/python -u apps/api/scripts/<name>
#
# with its full output in sprint_prompts/logs/regressions/<YYYY-MM-DD>/<name>.out.
# Prints one line per script as it finishes — name, PASS n, FAIL m, seconds (or
# TIMEOUT / CRASH) — KEEPS GOING after a failing script, then prints the per-verify
# TOTAL table again and a final summary, and exits 1 if any script failed, timed
# out or crashed. Everything it prints is also written to
# sprint_prompts/logs/regressions_<YYYY-MM-DD>.out (VERIFYFIX1).
#
# THE LIST IS CHECKED FIRST (VERIFYFIX1): an entry that is not a bare verify_*.py
# name, names no file in apps/api/scripts/, or is listed twice stops the run with
# exit 2 and every bad entry printed — NOTHING is run (a typo never silently
# shrinks the suite; the 2026-11 list mixed bare names and paths, and the paths
# resolved to apps/api/scripts/apps/api/scripts/… — a CRASH for each).
#
# BEFORE STARTING it checks for a laptop bulk worker heartbeating in the last
# 120 s (portfolio.edgar_fetch_workers): the bulkfetchui verifies refuse to run
# beside one. If one is live (or the check itself cannot run) it warns and stops
# with exit 2 — stop the worker first (tmux attach -t bulkworker, Ctrl-C).
#
# Overrides (the defaults are the real run; the stub test sets all of them):
#   REGRESSION_SET           list file                (scripts/regression_set.txt)
#   REGRESSION_SCRIPTS_DIR   where the scripts live   (apps/api/scripts)
#   REGRESSION_RUNNER        command prefix           (doppler run … -- apps/api/venv/bin/python -u)
#   REGRESSION_LOG_ROOT      log root                 (sprint_prompts/logs/regressions)
#   REGRESSION_TIMEOUT       seconds per script       (1800)
#   REGRESSION_SUMMARY_OUT   the summary file         (sprint_prompts/logs/regressions_<date>.out)
#   REGRESSION_WORKER_CHECK  command printing the number of live laptop workers
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 2

LIST="${REGRESSION_SET:-$REPO/scripts/regression_set.txt}"
SCRIPTS_DIR="${REGRESSION_SCRIPTS_DIR:-$REPO/apps/api/scripts}"
RUNNER="${REGRESSION_RUNNER:-doppler run -p hollisworks -c prd -- $REPO/apps/api/venv/bin/python -u}"
DAY="$(date +%Y-%m-%d)"
LOG_DIR="${REGRESSION_LOG_ROOT:-$REPO/sprint_prompts/logs/regressions}/$DAY"
SUMMARY_OUT="${REGRESSION_SUMMARY_OUT:-$REPO/sprint_prompts/logs/regressions_$DAY.out}"
TIMEOUT_S="${REGRESSION_TIMEOUT:-1800}"
DEFAULT_WORKER_CHECK="doppler run -p hollisworks -c prd -- $REPO/apps/api/venv/bin/python -c '
import asyncio, os, asyncpg
async def main():
    c = await asyncpg.connect(os.environ[\"DATABASE_URL\"], statement_cache_size=0)
    try:
        async with c.transaction():
            await c.execute(\"SET LOCAL app.is_super_admin = '\''true'\''\")
            n = await c.fetchval(\"SELECT count(*) FROM portfolio.edgar_fetch_workers WHERE target = '\''laptop'\'' AND stopped_at IS NULL AND heartbeat_at >= now() - interval '\''120 seconds'\''\")
    finally:
        await c.close()
    print(n)
asyncio.run(main())'"
WORKER_CHECK="${REGRESSION_WORKER_CHECK:-$DEFAULT_WORKER_CHECK}"

if [[ ! -f "$LIST" ]]; then
  echo "run_regressions: no list file at $LIST" >&2
  exit 2
fi

# ── the list, checked BEFORE anything runs ───────────────────────────────────
names=()
bad=()
declare -A seen=()
while IFS= read -r raw || [[ -n "$raw" ]]; do
  name="${raw%%#*}"
  name="$(echo "$name" | tr -d '[:space:]')"
  [[ -z "$name" ]] && continue
  if [[ ! "$name" =~ ^verify_[A-Za-z0-9_]+\.py$ ]]; then
    bad+=("not a bare verify_*.py name: '$name'")
  elif [[ ! -f "$SCRIPTS_DIR/$name" ]]; then
    bad+=("no such script: '$name' (looked in $SCRIPTS_DIR)")
  fi
  if [[ -n "${seen[$name]:-}" ]]; then
    bad+=("listed twice: '$name'")
  fi
  seen[$name]=1
  names+=("$name")
done <"$LIST"
if (( ${#bad[@]} > 0 )); then
  echo "run_regressions: the list $LIST has ${#bad[@]} bad entr$( (( ${#bad[@]} == 1 )) && echo y || echo ies) — NOTHING was run:" >&2
  for b in "${bad[@]}"; do echo "  MISSING/BAD: $b" >&2; done
  exit 2
fi
if (( ${#names[@]} == 0 )); then
  echo "run_regressions: the list $LIST names no script — NOTHING was run." >&2
  exit 2
fi

live="$(bash -c "$WORKER_CHECK" 2>/dev/null | tail -n 1 | tr -d '[:space:]')"
if [[ ! "$live" =~ ^[0-9]+$ ]]; then
  echo "run_regressions: WARNING — could not check for a laptop bulk worker (got '${live}'). Stopping." >&2
  exit 2
fi
if (( live > 0 )); then
  echo "run_regressions: WARNING — a laptop bulk worker is heartbeating ($live). The bulkfetchui verifies refuse to run beside it." >&2
  echo "run_regressions: stop it first (tmux attach -t bulkworker, Ctrl-C), then re-run. Nothing was run." >&2
  exit 2
fi

mkdir -p "$LOG_DIR" "$(dirname "$SUMMARY_OUT")"
: >"$SUMMARY_OUT"
say() { printf '%s\n' "$*" | tee -a "$SUMMARY_OUT"; }

n_total=0; n_clean=0; n_failed=0; n_timeout=0; n_crash=0
failed_names=()
rows=()
say "run_regressions: ${#names[@]} script(s) from $LIST, timeout ${TIMEOUT_S}s each, started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "$(printf '%-34s %-8s %-6s %-6s %s' VERIFY RESULT PASS FAIL SECONDS)"

for name in "${names[@]}"; do
  n_total=$((n_total + 1))
  out="$LOG_DIR/$name.out"
  start=$(date +%s)
  # shellcheck disable=SC2086  # RUNNER is a deliberate word-split command prefix
  VERIFY_NESTED=1 timeout "$TIMEOUT_S" $RUNNER "$SCRIPTS_DIR/$name" >"$out" 2>&1 </dev/null
  rc=$?
  secs=$(( $(date +%s) - start ))
  # the script's OWN final TOTAL line (anchored; the last one — _verify_total.final_total)
  total_line="$(grep -E '^TOTAL: [0-9]+ PASS, [0-9]+ FAIL' "$out" | tail -n 1)"
  p="-"; f="-"
  if [[ -n "$total_line" ]]; then
    p="$(echo "$total_line" | sed -E 's/^TOTAL: ([0-9]+) PASS, ([0-9]+) FAIL.*/\1/')"
    f="$(echo "$total_line" | sed -E 's/^TOTAL: ([0-9]+) PASS, ([0-9]+) FAIL.*/\2/')"
  fi
  if (( rc == 124 )); then
    result="TIMEOUT"; n_timeout=$((n_timeout + 1)); failed_names+=("$name")
  elif [[ -z "$total_line" ]]; then
    result="CRASH"; n_crash=$((n_crash + 1)); failed_names+=("$name")
  elif (( f > 0 || rc != 0 )); then
    result="FAIL"; n_failed=$((n_failed + 1)); failed_names+=("$name")
  else
    result="PASS"; n_clean=$((n_clean + 1))
  fi
  row="$(printf '%-34s %-8s %-6s %-6s %ss (exit %s)' "$name" "$result" "$p" "$f" "$secs" "$rc")"
  rows+=("$row")
  say "$row"
done

say "----"
say "PER-VERIFY TOTALS"
say "$(printf '%-34s %-8s %-6s %-6s %s' VERIFY RESULT PASS FAIL SECONDS)"
for row in "${rows[@]}"; do say "$row"; done
say "----"
say "SUMMARY: $n_total script(s): $n_clean clean, $n_failed with failures, $n_timeout timed out, $n_crash crashed. Logs: $LOG_DIR (summary: $SUMMARY_OUT)"
if (( ${#failed_names[@]} > 0 )); then
  say "NOT CLEAN: ${failed_names[*]}"
  exit 1
fi
exit 0
