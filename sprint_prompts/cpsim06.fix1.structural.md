CPSIM06 FIX 1 — the PC rebooted during the verify. Clean up and hand back so the runner re-runs verify_cpsim06.py. 2 tasks, keep it VERY SHORT. Branch cpsim06-work. Do NOT change product code or any assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The build completed: all 9 tasks committed, final commit d9d6285 "cpsim06: account opening at the simulated custodian …" (plus d4c1971, the CRM tax-id kind fix). The runner marked the sprint done at 10:25 UTC and began verify_cpsim06.py; the machine rebooted at ~10:31 UTC mid-verify (sprint_prompts/logs/cpsim06_run.out stops inside the NO-list account-type section, every line so far [PASS], no FAIL, no TOTAL).
- The verify was killed before its end teardown, so FIXTURE rows are probably left in the database. The working tree has one modified tracked file, logs/cpsim_conformance_20261112.json (a conformance output written during the interrupted run).
- WSL memory is limited: do NOT run the full verify_cpsim06.py or any other full verify, and do NOT run npm run build — the runner runs the full verify after this leg.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — never wait for one; run every command synchronously in the foreground. No network. No model call. Never print a secret, key, token or full tax id / account number. Fixtures only; never write a real or demo row; never apply a capability loader. Final commit on cpsim06-work with message "cpsim06 fix 1: cleanup after the interrupted verify (no code change)".

=== TASK 1: CLEAN === `git checkout -- logs/cpsim_conformance_20261112.json` if its diff is only the interrupted run's output (else commit it with a note). Count fixture rows per table (the verify's own counts), then `timeout 600 python apps/api/scripts/verify_cpsim06.py --cleanup-only` → every table at zero fixture rows, real-row fingerprints unchanged. Then `--self-checks-only` → 0 FAIL. Record both results (counts before / after) in docs/cpsim/CPSIM06_NOTES.md under "Fix 1 — interrupted verify". COMMIT.
=== TASK 2: FINAL === one line in the cpsim06 UPDATE in docs/PROJECT_STATUS.md: "verify interrupted by a reboot (2026-11-12 ~10:31 UTC); fixture rows cleaned; re-run by the runner". Final commit.
