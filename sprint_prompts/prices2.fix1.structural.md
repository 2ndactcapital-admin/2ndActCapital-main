PRICES2 FIX 1 — finish prices2. The build leg was KILLED at the 90-minute cap (2026-11-13 ~00:43 UTC) during Task 4; Tasks 1–3 and most of Task 4 are committed. Do NOT redo committed work. 3 tasks, keep it SHORT. Branch prices2-work. Never weaken an assertion.

LESSONS: commit after EVERY step; record each one-off result in docs/PRICES2_NOTES.md §5 and commit immediately; the 90-minute cap killed the build leg.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (git log main..prices2-work): f7944e9 Task 1 · 16a1bc6 migration price_refresh_days + price_refresh_state (applied) · a682f69 resumable day refresh + priority + prices_tick · 8944c2e Task 3 screen + track1 warning · d6d89ce verify_prices2 written (self-checks 39/0) · fc0488e section x 33/0 · 2deb6d0 FIND: 5 FKs onto securities_global lacked indexes, added CONCURRENTLY (applied) · a7bc3d5 refresh order fix, sections m,r 28/0 · 543c6fa one-off runs write no price_refresh_state (track1 --section n 44/0) · aa03fe8 verify [R] 26/0. UNCOMMITTED at the kill: docs/PRICES2_NOTES.md and docs/PROJECT_STATUS.md (modified) — review them and commit first.
- Also note: the production database was read-only for ~55 s at 2026-11-12 22:17:04–22:17:59 UTC (Supabase disk auto-grow during Joe's price backfill). If any prices2 step between 22:17 and 22:18 UTC failed or half-applied (the migration, an index, a one-off), find it in the notes / log and re-check it now; say "none affected" if so.
- WSL memory is limited: do NOT run the full verify_prices2.py, any other full verify, or npm run build — the runner runs the full verify after this leg. --self-checks-only, --cleanup-only and --section / --only one-offs (`timeout 600`) only.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — never wait for one; run every command synchronously in the foreground. No network. No model call. Never print a secret. Fixtures only; never write a real price row. Final commit on prices2-work with message "prices2 fix 1: proof completed (<sections with PASS counts>)".

=== TASK 1: COMMIT + CHECK === commit the two docs as they stand (review first); the 22:17 UTC read-only window check. COMMIT.
=== TASK 2: PROVE === --cleanup-only → zero; --self-checks-only → 0 FAIL; each verify_prices2 section not yet run one-off (list which were run in the notes; run the rest), plus fast-mode verify_prices1 / verify_track1 / verify_secmaster2 self-checks → 0 FAIL. Record + commit after each.
=== TASK 3: FINAL === complete the PROJECT_STATUS PRICES2 line (incl. operator note: the real prices job trigger is ON; after deploy the 21:15 New York run opens the day and the ticks continue it; the Security master shows covered / refreshed today / oldest date). Final commit.
