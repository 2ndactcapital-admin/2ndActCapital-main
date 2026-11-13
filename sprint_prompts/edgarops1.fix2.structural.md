EDGAROPS1 FIX 2 — finish edgarops1. The fix-1 leg (a restart after an API sign-in error) was KILLED at the 90-minute cap (2026-11-13 ~15:57 UTC) during verify one-offs. All build work is committed. Do NOT redo committed work. 3 tasks, keep it SHORT. Branch edgarops1-work. Never weaken an assertion.

LESSONS: commit after EVERY step; record each one-off result in docs/EDGAROPS1_NOTES.md and commit immediately; the 90-minute cap killed this leg — run only the sections not yet proven.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (git log main..edgarops1-work):
799df0b status + discovery · f822f83 nightly download (the day's new selected filings queued as one scheduled bulk_fetch run; cap; carry-over; once per day; bounded chunks) · 85ea3e3 institutions table adds up (API) · 2a0b1ee UIT ETFs (SPY / DIA / MDY / QQQ) by trust CIK (497K > 485BPOS > 497; N-PORT by CIK) · 94fbf97 screen + run summaries · 3adf7fd verify_edgarops1.py (self-checks 26/0) + regression_set · 2a300f4 two CODE bugs the verify caught and fixed (jsonb_set with NULL nulled a run's details when the waiting reason cleared; the Runner on the tick's RLS pool left peeked filings 'selected' — now a plain pool like run_bulk_fetch.py); sections PROVEN: t 34/0, u 25/0, s 14/0, f 16/0, w 21/0. Working tree clean.
- WSL memory is limited: do NOT run the full verify_edgarops1.py, any other full verify, or npm run build — the runner runs the full verify after this leg. --self-checks-only, --cleanup-only and section one-offs (`timeout 600`) only.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — never wait for one; run every command synchronously in the foreground. No network (SEC / R2 stubbed). No model call. Never print a secret. Fixtures only; never write a real index row, run row or R2 object. Final commit on edgarops1-work with message "edgarops1 fix 2: proof completed (<sections with PASS counts>)".

=== TASK 1: PROVE THE REST === list verify_edgarops1's sections; the ones not in the PROVEN list above → one-off each (`timeout 600`), record + commit after each; any FAIL → decide CODE vs VERIFY with evidence, fix, re-run that section, commit. Then --cleanup-only → zero, --self-checks-only → 0 FAIL, fast-mode self-checks of verify_dailyops1 / verify_readerfix1 and the bulkfetch / edgar verifies that exist → 0 FAIL.
=== TASK 2: STATUS === complete the EDGAROPS1 line in docs/PROJECT_STATUS.md (incl. operator steps: env defaults, any Doppler / render.yaml change for R2 on the scheduler, "check the first weekday run: index refresh then the queued download run, summary on the runs list; a waiting run is claimable by the laptop worker"). COMMIT.
=== TASK 3: FINAL === final commit.
