CASH02 FIX 1 — make verify_cash02.py pass. 8 FAILs from TWO verify defects:
the seed crashed (so 6 fixture sections never ran), and the teardown was
refused by the rep6 tax-input guard (so fixture rows are probably LEFT IN
THE DATABASE right now). Fix both, clear the leftovers, then run every
fixture section one-off and fix whatever it finds — code or verify — this
sprint. 4 tasks. Branch cash02-work. Decide code vs verify with evidence;
never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash02 run, 2026-11-09 ~01:52–
02:05 UTC: TOTAL 74 PASS, 8 FAIL; the sod item type cash_policy (maker
set_cash_policy, checker approve_cash_policy, policy self_approval never,
enforce_floor true), the cash_policy_approval task kind, min_coverage_months
having ONE home (cash_coverage_minimums gone), the INSERT-column check (15
tables), rolbypassrls=false, node tests, npm run build, no network / no model
call, and the regression verify_cash01.py 152 PASS 0 FAIL (own final TOTAL)
all PASSED — keep them):
- FAIL 1 "seed raised — CheckViolationError: … indicator_observations
  violates check constraint indicator_obs_source_provider_chk" at
  verify_cash02.py:828: `INSERT INTO market_data.indicator_observations
  (series_id, obs_date, value, source_provider) VALUES ($1, $2, $3,
  'none')`. VERIFY defect: 'none' is not an allowed source_provider. Read the
  CHECK's allowed values from the catalog (pg_get_constraintdef) and seed
  with the provider the FRED series really carries (expected 'fred'); also
  confirm the fixture indicator_series rows match what fred.py / read_service
  expect (series code, provider), so the yield read in the service under
  test finds them. Extend the verify's catalog pre-check so every literal it
  seeds into a CHECKed column is compared to the CHECK before seeding (the
  existing "every literal the seeds use is allowed" pattern from s29b).
- FAILs 2–7 "calculation / yields / maker-checker / minimum / inbox + bulk /
  visibility + isolation: not run — the seed failed" — follow from FAIL 1.
  After the fix, EVERY one must run; run each one-off on fixtures until it
  passes and list them with their PASS counts.
- FAIL 8 "teardown raised — InsufficientPrivilegeError: tax inputs are never
  deleted (a correction is a new version)" — public.tax_inputs_guard()
  (docs/rep6_part1.sql:~200) allows DELETE only when
  public.execution_teardown_allowed() is true, i.e. the documented teardown
  GUC is set (verify_rep6* pass gucs=("execution.teardown", …) to
  _verify_cleanup.cleanup — see their call at ~:562). verify_cash02's
  teardown (line ~532) passes no gucs. VERIFY defect: pass the GUC(s) the
  guard reads (confirm execution_teardown_allowed()'s definition) so the
  fixture tax profile / tax-input rows delete, still keyed by fixture ids.
  Because this teardown raised inside its transaction, the fixture rows from
  the run were NOT removed: FIRST (Task 1) count every fixture row by the
  verify's fixture ids / reserved prefixes across every touched table, print
  the counts, then run verify_cash02 --cleanup-only after the fix and prove
  zero remain. Confirm no REAL tax input, policy or obligation was touched
  (count + checksum before / after, by non-fixture org).
- Check the start-of-run teardown takes the same path (it must also have
  the GUC), and grep the other cash verifies (verify_cash01.py) for the same
  omission — cash01 passed, so state why (it seeds no tax inputs) or fix it.
- WSL memory is limited: do NOT run the full verify_cash02.py,
  verify_cash01.py or npm run build. --self-checks-only, --cleanup-only and
  one-off sections MAY be run — and here you SHOULD run each fixture section
  one-off on fixtures until it passes.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row; never weaken the rep6 guard or any SoD rule. Commit on cash02-work
with message "cash02 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === the CHECK's allowed values; the fixture series vs
what the service reads; execution_teardown_allowed()'s GUC; leftover
fixture-row counts per table (print them).
=== TASK 2: FIX === seed values, catalog pre-check, teardown GUCs (start and
end), and anything the one-off sections find (code or verify, named).
=== TASK 3: PROVE === --cleanup-only → zero fixture rows in every touched
table (list counts); seed one-off → completes; each of the six fixture
sections one-off → PASS (list each with PASS count); teardown one-off →
zero fixture rows; verify_cash02 --self-checks-only → 0 FAIL; real rows
unchanged (count + checksum).
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash02 listing
every fix (code vs verify).
