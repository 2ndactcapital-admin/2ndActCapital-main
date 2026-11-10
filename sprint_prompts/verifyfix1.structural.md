VERIFYFIX1 — bring the REGRESSION SET up to today's rules so the full
regression run (scripts/run_regressions.sh, last full run 2026-11-02) can
pass: migrate the older verifies to roles-via-user_roles (bugfix4), count
audit_log and other live-written tables BY FIXTURE IDS only, and fix the
regression set's own list. Verify-script work only — no product code unless a
verify proves a real defect (then name it CODE and fix it minimally). 7 tasks.
Branch verifyfix1-work. Never weaken an assertion; never delete a check to
make it pass — make it test the same thing correctly.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-10):
- scripts/regression_set.txt lists 65 verifies (comment header: "Run on the
  operator's schedule … not in a sprint"). The newest seven entries are
  written as `apps/api/scripts/verify_s30.py`, `…verify_s29b.py`,
  `…verify_cash01.py` … `…verify_cash06.py`; every older entry is a bare
  name (`verify_rep13.py`). Make the format ONE way — whatever
  run_regressions.sh actually resolves (read it) — and make the script fail
  loudly on an entry it cannot find.
- bugfix4: ONE role source — public.user_roles; users.role is only a
  mirror. A verify that seeds a role by writing users.role (or a role
  string) no longer gives that user the permissions it assumes. Fixture
  roles go through rbac.grant_named_role / grant_org_admin (or the
  privileged fixture insert into user_roles the newer verifies use);
  profile / permission-set permissions through user_permission_sets with its
  REAL columns (user_id, permission_set_id, granted_at, granted_by — no
  org_id; s29b fix 1). Every migrated verify must ASSERT the app decides
  each fixture user's permissions as meant (the newer verifies' "every
  fixture role is a user_roles grant the app decides as meant" check —
  reuse its helper).
- 26 verifies count the WHOLE public.audit_log table (visibility1 fix 1):
  authz1, bugfix1, bugfix2, crmdetail1, dealroom1, dealroom2,
  event_emission, fee42b, lotmethod, perms1, portentry1, portentry2,
  rep1–rep12 (incl. rep7b), usermgmt1 — a real nightly / user write mid-run
  fails them. Scope each count to fixture orgs / users / resource ids
  (staffroles1 is the model). Same rule for sod_decisions, payment_events,
  workflow runs and any other live-written table a verify counts.
- Known older-verify issues already written down (fix those in the set):
  verify_rep5 nested verify_rep2 timeout 1200 s → 2700 s; verify_rep7
  TODAY fixed at start vs services' date.today() (pass today through);
  pre-run cleanups must be scoped by fixture id (verify_rep6's ~25-min
  FK-walk); a teardown must pass every teardown GUC a guard on a touched
  table reads (sod.teardown, execution.teardown, the payment_events guard —
  cash02 fix 1).
- Newer verify rules (cash track) are the standard to migrate TO: check()
  gets a real bool; RLS context set ON the connection inside its
  transaction; text ids compared as text, uuids as uuid; every INSERT's
  columns exist in docs/schema_snapshot.sql; every seeded literal in a
  CHECKed column is allowed; if the seed fails every fixture section is a
  [FAIL], never skipped; TOTAL printed on every exit path; a nested
  script's OWN final ^TOTAL line is parsed (shared helper).
- The nightly refresh writes real rows at ~07:00 UTC; the scheduler skips
  is_test orgs (rep6).
- WSL memory is limited and the runner kills a leg at 90 minutes: do NOT run
  any full verify, run_regressions.sh or npm run build in this sprint. Prove
  each migrated verify with --self-checks-only (add the flag where a verify
  lacks it) and, where it has one, its seed + one fixture section one-off,
  each under `timeout 600`. COMMIT after every 5 verifies migrated
  ("verifyfix1 (wip): <names>") so a cap never loses work.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. No model call. Never print a secret or a full account
number. Fixtures only; never write a real row; never change a real person's
roles. Final commit on verifyfix1-work with message "verifyfix1: <one line
per verify or group>".

=== TASK 1: INVENTORY === docs/VERIFYFIX1_INVENTORY.md: for EACH of the 65
regression-set verifies, a row: role seeding (user_roles | users.role |
none), unscoped counts (table list), missing teardown GUCs, --self-checks-only
present, TOTAL on every exit path, nested-TOTAL parsing, timeouts, known
issue from the list above, last known result (regressions_2026-11-02.out and
sprint run logs where present). Sort by work needed.
=== TASK 2: THE LIST === normalise regression_set.txt; run_regressions.sh
fails loudly on a missing entry; prints a per-verify TOTAL table and a final
summary; keeps going after a failure; writes logs/regressions_<date>.out.
=== TASK 3: ROLES === migrate every users.role-seeded verify in the set to
user_roles / permission sets with the "decides as meant" assertion.
=== TASK 4: COUNTS === scope every whole-table count (audit_log and the
other live-written tables) to fixture ids; keep the check's intent.
=== TASK 5: THE REST === the known issues above; teardown GUCs; TOTAL on
every exit path; nested TOTAL helper; scoped pre-run cleanup.
=== TASK 6: PROVE === every touched verify: --self-checks-only → 0 FAIL
(list each with its count); seed + one section one-off → PASS where the
verify has fixture sections (list); --cleanup-only → zero fixture rows; no
real row changed (count + checksum of user_roles, audit_log rows outside
fixture ids unchanged by the sprint).
=== TASK 7: UPDATE PROJECT STATUS === docs/PROJECT_STATUS.md (verifyfix1:
what changed per verify), the operator step: "run
scripts/run_regressions.sh unattended (start after 07:30 UTC or well before
06:00 UTC; ~N hours), then send the summary", and the inventory doc.

=== VERIFICATION: apps/api/scripts/verify_verifyfix1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts; 'TOTAL: N PASS, M FAIL'
on every exit path; non-zero exit on failure. STATIC + self-check proof only
(it must NOT run full verifies): (1) regression_set.txt entries all resolve,
one format; (2) no verify in the set seeds roles via users.role (AST / grep
by name, not by exact line); (3) no unscoped count of audit_log /
sod_decisions / payment_events in any verify in the set; (4) every verify in
the set prints TOTAL on every exit path (static scan for the shared wrapper)
and supports --self-checks-only; (5) runs each verify's --self-checks-only as
a subprocess (VERIFY_NESTED=1, 600 s each) and parses its OWN final ^TOTAL
line — 0 FAIL each; (6) run_regressions.sh: fails on a missing entry
(fixture list in a temp copy), continues after a failing entry, prints the
summary table (run with a two-entry fixture list of tiny stub verifies, not
the real set). Assertions are one [Y] per item above plus "teardown leaves
zero fixture rows".
