LOTMETHOD FIX 1 — one verify fixture defect. 2 tasks. Branch lotmethod-work
(lotmethod = b9ca830).

Context: verify_lotmethod.py ran (run_sprint Step 3, 2026-10-28): TOTAL 139
PASS, 1 FAIL. Everything else passed — every lot method, precedence, the
fast scoped cleanup (all five --cleanup-only runs ~1 min each, REP2 leftovers
gone), B2–B5, gates, routes, node tests, npm build, and the regression
verify_rep6.py 186 PASS / 0 FAIL. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The only FAIL: "b6 raised — LifecycleError: {'message': 'Before submitting
  for review, complete: Description, Asset class, Target raise, Minimum
  investment.', 'missing_fields': [description, asset_super_class,
  target_raise, minimum_investment]}". The B6 section's fixture deals are
  created without the fields the deal lifecycle requires before a stage
  change, so the un-archive scenario never reaches the code under test.
- Fix the FIXTURE (give each B6 fixture deal valid description,
  asset_super_class — a real key from its lookup / CHECK, read from the
  catalog — target_raise and minimum_investment, or create them through the
  same route the other deal verifies use), not the lifecycle rule. Then the
  B6 check runs as written: two deals created in the same transaction by the
  same person, archived and un-archived, each restored to its own prior stage
  via the new deal id on the history row.
- Do not change any other check. WSL memory is limited: do NOT run
  verify_lotmethod.py, verify_rep6.py or npm run build.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. No network calls. Never print a secret. You may run python -m
py_compile on the verify. Commit on lotmethod-work with message "lotmethod
fix 1: B6 verify fixture deals carry the fields the lifecycle requires".

=== TASK 1: FIX THE B6 FIXTURE in verify_lotmethod.py ===
Read the lifecycle's required fields and their valid values from the code /
catalog; seed them; check the seed literals against their CHECK / lookup like
every other seed; teardown already covers deals (confirm the new rows are
removed by the shared cleanup).

=== TASK 2: UPDATE PROJECT STATUS ===
One UPDATE line under the lotmethod entry in docs/PROJECT_STATUS.md.
