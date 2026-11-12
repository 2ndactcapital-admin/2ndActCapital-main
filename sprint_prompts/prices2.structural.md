PRICES2 — the nightly price job must keep the WHOLE coverage set current every night. Today one nightly run fits ~128 securities in its 180 s step, so with track1's lists (602 covered, ~2,600 once the Russell 2000 loads) each security is refreshed only every 5–20 nights. 5 tasks. Branch prices2-work. Never weaken an existing assertion.

LESSONS: STATUS line first; small committed steps ("prices2 (wip): …"); every verify section self-contained, including the clock; the runner kills a leg at 90 minutes — commit after every step.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- services/security_data/prices.py: GAP_FILL_DAYS = 10 (line 51; the nightly re-reads 10 calendar days per covered security), NIGHTLY_MAX_NEW = 200 (52; new members given full history per night), NIGHTLY_DEADLINE_S = 180.0 (53; inside the 200 s step deadline, security_master_jobs.STEP_DEADLINE_S); nightly() at line 515. Provider yahoo_chart, daily call budget 2,000 (registry), one call per security.
- services/security_data/tracking.py:247 (track1) warns: "the nightly job refreshes about N securities a night". Joe's dry run of security_lists_load.py --all (2026-11-12): 592 distinct list securities; "upkeep 602 calls/day for 602 covered"; "the nightly job refreshes about 128 securities a night; 602 covered means each is refreshed every 5 nights". The Russell 2000 (IWM via sec_nport) adds ~2,000 more on the real run → ~2,600 covered > the 2,000-call daily budget at one call per security.
- The cron tick (workflow_scheduler_tick.py) runs every 5 minutes on Render; platform jobs run as steps with a deadline; EDGAR's scheduled step (bulkfetchui3) shows the pattern for "once per day, resumable".

=== THE FIX (CODE) ===
1. RESUMABLE NIGHTLY: the day's price refresh continues across ticks (bounded chunks within the step deadline) until every covered security has been refreshed for that day, or the day's call budget is spent — one run row per day with progress ("1,840 of 2,600 refreshed"), never two runs racing, failures recorded and never raised into the tick. Start time stays after the US close data is available (find the current schedule; keep it).
2. FEWER CALLS PER SECURITY: check whether the provider adapter can fetch several symbols per request (e.g. a multi-symbol endpoint the provider already exposes) — if yes, use it for the daily refresh and record calls honestly; if not, say so in the notes.
3. PRIORITY WHEN THE BUDGET CANNOT COVER EVERYTHING: held positions, models / replicas and benchmarks first, then tracked lists in the order core ETFs → fixed income → commodities → key benchmarks → S&P 500 → Russell 2000; anything not refreshed today goes first tomorrow (oldest-first), so nothing is ever more than a few days stale. The Security master screen shows "covered N · refreshed today M · oldest price date D".
4. track1's warning text reflects the new capacity (refreshes per day at the budget, not per run).
Do NOT raise the provider's daily budget (Joe's call; note it as an operator option).

WSL memory is limited: do NOT run npm run build or any other sprint's full verify. Nested regressions in fast mode. One-offs `timeout 600`.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — nothing will ever notify you that a script finished. Never wait for one. Run every command synchronously in the foreground. No network (stub the provider). No model call. Never print a secret. Fixtures only; never write a real price row. Light theme. Stick with existing constructs. Final commit on prices2-work with message "prices2: nightly price refresh resumes across ticks until the coverage set is current; priority + oldest-first under the call budget".

=== TASK 1: STATUS FIRST + DISCOVER === docs/PROJECT_STATUS.md PRICES2 line; nightly(), the job step, schedule, the provider adapter (multi-symbol?), budget accounting. docs/PRICES2_NOTES.md (capacity math before / after). COMMIT.
=== TASK 2: RESUMABLE NIGHTLY + PRIORITY === COMMIT.
=== TASK 3: SCREEN + WARNING TEXT === COMMIT.
=== TASK 4: VERIFY === apps/api/scripts/verify_prices2.py (self-contained, --self-checks-only, --cleanup-only, in regression_set.txt), stub provider: 2,600 covered at budget 2,000 → day 1 refreshes 2,000 in priority order across several ticks, one run row, progress recorded; day 2 starts with the 600 left (oldest first); multi-symbol path (if built) counts calls correctly; a tick past its deadline stops cleanly and the next tick resumes; failure recorded, tick continues; the screen numbers. Fast-mode regressions: verify_prices1, verify_track1, verify_secmaster2. One-offs listed with PASS counts. COMMIT.
=== TASK 5: FINAL === PROJECT_STATUS line + operator note (what the screen shows; the budget option). Final commit.
