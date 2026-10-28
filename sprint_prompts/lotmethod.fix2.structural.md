LOTMETHOD FIX 2 — B6 un-archive still restores the wrong stage (a CODE
defect). 3 tasks. Branch lotmethod-work (lotmethod fix 1 = 330212d).

Context: verify_lotmethod.py ran after fix 1 (2026-10-28): TOTAL 142 PASS,
1 FAIL. The fixture now passes the lifecycle; the bug reproduction passes
(the frozen pre-B6 prior_status restores deal one to 'draft'); the regression
verify_rep6.py passed. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The FAIL: "fixed: each history row carries its deal's id; un-archive
  restores deal one to 'submitted' and deal two to 'draft'" → detail
  "draft draft draft draft tagged=5" = new1 (DLC.prior_status of deal one),
  new2, r1["deal_status"], r2["deal_status"] are ALL 'draft'; 5 closed
  versions carry version_of_deal_id. The expectation is right: deal one went
  draft → submitted → archived, so its prior stage is 'submitted'.
- The verify (verify_lotmethod.py ≈1096–1135) does, each in its own
  transaction: apply_status(d1, 'submitted', confirm=True);
  apply_status(d1, 'archived', confirm=True); apply_status(d2, 'archived',
  confirm=True); then prior_status for each; then un-archive each.
- services/deal_lifecycle.py prior_status (:196) takes the newest closed
  system-axis version of the deal (version_of_deal_id = deal) whose status is
  not 'archived', ORDER BY system_to DESC LIMIT 1 — and returns 'draft' for
  deal one. So either (a) the transition to 'submitted' does not leave a
  closed version holding 'submitted' (e.g. it updates in place, or the
  archive transition's closed row carries the pre-submit status), (b) the
  closed versions' system_to values tie or are ordered unexpectedly (same
  timestamp source, transaction timestamp reuse), or (c) the closed version
  written at archive time copies a stale status. Find which by reading
  apply_status and the versioning trigger / helper, and by a one-off
  synchronous script on fresh fixture-free test rows in a fixture org you
  create and delete yourself (is_test org, reserved prefix) — never on real
  deals.
- Fix the CODE so prior_status returns the status the deal held immediately
  before its newest archive, by deal id (e.g. order by the version sequence
  / system_from of the version that the archive closed, or record the prior
  status on the archive transition itself). Keep the fallback for pre-column
  versions exactly as it is. Do not change the verify's expectation.
- WSL memory is limited: do NOT run verify_lotmethod.py, verify_rep6.py or npm
  run build.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. No network calls. Never print a secret. Clean up every row your
one-off script creates (by its own ids) and confirm zero remain. Commit on
lotmethod-work with message "lotmethod fix 2: un-archive restores the status
held immediately before the newest archive".

=== TASK 1: DISCOVER === which of (a)/(b)/(c), with the rows your one-off
script saw (status, version_of_deal_id, system_from, system_to per version).
=== TASK 2: FIX services/deal_lifecycle.py (and the versioning helper if it is
the cause) ===
=== TASK 3: UPDATE PROJECT STATUS === one UPDATE line under lotmethod.
