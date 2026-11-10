VERIFYFIX1 FIX 2 — FINISH verifyfix1 (the wrap-up only). Fix 1's leg was killed at the runner's 90-minute cap with NO output. Fix 1 got the inventory, the known-issue confirmations, verify_verifyfix1.py and most of the proof committed as wip; it did NOT finish the proof tail or write the status. Do ONLY the wrap-up, fast. 5 tasks. Branch verifyfix1-work. Verify-script work only. Never weaken an assertion; never delete a check to make it pass.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (git log verifyfix1-work, 2026-11-10 ~19:41 UTC):
- 0af412b / 9c51561 / c7e748d / 30b8440 (wip, the first run): shared tooling, scoped counts (41 verifies), roles by user_roles + "decides as meant" assertion in the migrated verifies, normalised regression_set.txt + run_regressions.sh.
- 7962c7e (wip) docs/VERIFYFIX1_INVENTORY.md — 63 rows, all done; scanner --inventory + role-string-read rule.
- 483706e (wip) known issues confirmed done: rep5 nested rep2 2700 s; rep7 today() passed through; rep6 scoped pre-run cleanup; _verify_cleanup sets every teardown GUC.
- 5c6658a (wip) apps/api/scripts/verify_verifyfix1.py: static items (1)-(4), (6) runner on stub verifies, (T) teardown → 28/0 under --only=1,2,3,4,6; item (5) written.
- 5fef434 / 58d7f95 (wip) PROOF: --self-checks-only 64/64 verifies, 0 FAIL.
- 979ab8e (wip) _verify_oneoff runs main's pre-seed y_ sections before the seed; one-offs authz1 8/0, perms1 5/0, rep2 4/0, rep7 21/0.
- Uncommitted: apps/api/scripts/_verify_oneoff.py (+3 lines): review it — keep or revert (state which). Leave .claude/settings.local.json and the two untracked sprint_prompts/*.structural.md files alone.
- STILL TO DO: the rep6 and cash06 one-offs (seed + one section, `timeout 600` each; rep6's pre-run cleanup is now fixture-scoped); verify_verifyfix1.py item (5) once (it runs the self-checks as subprocesses, 600 s each — if it would exceed the budget, run it with the batches fix 1 already proved and record the 64/64 result instead, saying so); --cleanup-only → zero fixture rows; real rows unchanged (count + checksum of user_roles and audit_log rows outside fixture ids, before and after); the PROJECT_STATUS update (docs/PROJECT_STATUS.md has no verifyfix1 section beyond one mention).
- WSL memory is limited: do NOT run any full verify, run_regressions.sh or npm run build. `timeout 600` on every one-off. COMMIT after each task ("verifyfix1 fix 2 (wip): <task>"). Budget: the status doc FIRST (about 5 minutes), then the one-offs.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — nothing will ever notify you that a script finished. Never wait for one. Run every command synchronously in the foreground. No network. No model call. Never print a secret or a full account number. Fixtures only; never write a real row; never change a real person's roles. Final commit on verifyfix1-work with message "verifyfix1 fix 2: <one line per item>" (naming the earlier wip commits' content too).

=== TASK 1: UPDATE PROJECT STATUS (first) === docs/PROJECT_STATUS.md: a verifyfix1 section: what changed per verify group (tooling; roles via user_roles + the assertion; scoped counts; nested TOTAL; the known issues; the list + runner); the operator step "run scripts/run_regressions.sh unattended (start after 07:30 UTC or well before 06:00 UTC; ~N hours from the sizes in the inventory), then send the summary"; the inventory linked. COMMIT.
=== TASK 2: REVIEW === the uncommitted _verify_oneoff.py hunk. COMMIT or revert.
=== TASK 3: ONE-OFFS === rep6 and cash06: seed + one fixture section each → PASS (list with counts). COMMIT.
=== TASK 4: PROVE === verify_verifyfix1.py: items (1)-(4), (6) and (T) again (28/0), item (5) as above; --cleanup-only → zero fixture rows; real rows unchanged (counts + checksums before / after). COMMIT.
=== TASK 5: FINAL === re-run nothing else; final commit with the message above.
