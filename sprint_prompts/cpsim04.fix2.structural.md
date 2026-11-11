CPSIM04 FIX 2 — one verify constant. verify_cpsim04.py ended 288 PASS / 1 FAIL; the only FAIL is the render-test count, not a test failure. 3 tasks, keep it SHORT. Branch cpsim04-work. Never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (cpsim04fix1_run.out, 2026-11-11 ~21:45–22:00 UTC; fix 1's commits fbab7c7 … 2c63637 are in; every other section PASSED, including governance, isolation, routing, the exact cpsim_app privilege set and the fast-mode regressions):
- FAIL (line 381) "the cpsim04 render tests (tests/cpsim04/cpsim04.test.mjs, counted by their file …) all pass (9/8) — 0": `node --test` ran 9 tests, 9 passed, 0 failed, exit 0. The check compares the count to `NODE_TEST_COUNT = 8` (verify_cpsim04.py y_front_end, ~line 2399). Fix 1 (commit 56b2bd2, CODE R) added a render test for the manual-route reason on the payment page, so the file now holds 9. VERIFY defect.
- Fix: derive the expected count from the test file itself (count its `test(` declarations statically, the way other verifies count their own node tests "by their file"), assert it equals the run's `# tests` and `# pass`, and assert it is at least 9 (the reason panel's test must stay). Do not loosen "0 fail / exit 0".
- Do NOT run the full verify or npm run build. Run only: the front-end section one-off (`--only=front_end` or the section's name — `timeout 900`), and `--self-checks-only`.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — never wait for one; run every command synchronously in the foreground. No network. No model call. Never print a secret. Fixtures only. Final commit on cpsim04-work with message "cpsim04 fix 2: render-test count derived from the test file (VERIFY)".

=== TASK 1: STATUS === add "fix 2: render-test count (VERIFY)" to the cpsim04 UPDATE line in docs/PROJECT_STATUS.md. COMMIT.
=== TASK 2: FIX === as above. COMMIT.
=== TASK 3: PROVE === the front-end section one-off → PASS (9/9); --self-checks-only → 0 FAIL. Final commit.
