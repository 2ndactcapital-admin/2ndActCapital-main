PRICES1 FIX 1 — make verify_prices1.py pass. 3 FAILs: one real code defect
in the X-ray price reader (it would crash in production), one check that only
failed because of it, and the regression's fixture Super Admin. 4 tasks.
Branch prices1-work. Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_prices1 run, 2026-11-07
~10:40 ET: TOTAL 132 PASS, 3 FAIL; the plug-in contract, the template
vendor, the Yahoo chart adapter, quality events, store / restatement /
coverage, backfill dry run + resume, the nightly job, isolation and own
routes all PASSED — keep them):
- FAIL 1 "readers raised — AttributeError: module 'services.security_data'
  has no attribute 'get_market'". services/xray.py:652 (new in this sprint,
  _priced_estimates) does `from services import security_data as SD` then
  `await SD.get_market(conn, org_id, sec, as_of=today, purpose="internal")`.
  get_market exists in services/security_data/layer.py:162, but the package
  __init__ re-exports only get, get_bulk, history (line 23). CODE defect: the
  X-ray's priced path raises for any fund that reaches it. Fix by exporting
  get_market from the package (with the others) — or import it from the
  layer — and grep every reader the sprint touched (roll-up, X-ray, tracking
  error, backtest, harvest, drift, options payoff, notes) for any other name
  used through the package that the package does not export; fix each.
  The verify's readers section then runs as written (hand-computed values).
- FAIL 2 "the licence section needs the readers section's X-ray figures —
  skipped". Follows from 1; must PASS once 1 is fixed (member hidden /
  staff labelled internal, identical request both ways).
- FAIL 3 nested verify_marketdata1.py (own final TOTAL): 113 PASS, 1 FAIL —
  "provider admin: Super Admin only (viewer and org admin 403, also on PUT);
  the super admin's envelope lists the editable fields — 403 403 403 403":
  the fixture Super Admin also gets 403. The prompt asked for
  verify_marketdata1's seeding to be fixed first; the sprint left the file
  unchanged (git diff main..prices1-work is empty for it). USERS (line ~209)
  seeds U_SUPER as (ORG_A, "super_admin", …) — since bugfix4 every role is
  decided from user_roles, so a role string (or a user_roles grant of an
  ORG-scoped role row named super_admin) is not a Super Admin. VERIFY defect:
  seed exactly as secmaster2 fix 1 did (rbac.grant_named_role for
  super_admin, grant_org_admin for org admins, users.role kept as the mirror,
  a seed-time check that the app decides each role as intended, teardown of
  user_roles by fixture user). Check verify_prices1's own seeding uses the
  same helpers (it PASSED its admin checks, so say how it seeds).
- Note for the record (no change): the chosen primary is Yahoo chart
  (yahoo_chart), fallback Tiingo free (written, not connected — needs
  TIINGO_API_KEY). Yahoo's terms are personal / non-commercial: confirm the
  data_providers row records lic_member_display = not_allowed and
  lic_redistribution = not_allowed, so prices never reach a member and are
  labelled internal for staff (Joe's internal-use assumption, 2026-10-03).
- WSL memory is limited: do NOT run verify_prices1.py, verify_marketdata1.py
  or npm run build; --self-checks-only and one-off scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row. Commit on prices1-work with message "prices1 fix 1: <one line per
fix, code vs verify>".

=== TASK 1: DISCOVER === 1 (the missing export and any other package names
the readers use); 3 (how verify_marketdata1 seeds U_SUPER, how the app
resolves it; how verify_prices1 seeds); the Yahoo licence row.
=== TASK 2: FIX === code (1), verify (3).
=== TASK 3: PROVE === one-off on fixtures: the X-ray priced path returns the
hand-computed estimate for a fixture fund with a fixture series and "needs
prices" without one, no exception; the licence check (member hidden, staff
internal) passes; verify_marketdata1's provider admin check: Super Admin
200 with the envelope, viewer and org admin 403 on GET and on a VALID PUT.
verify_prices1 --self-checks-only and verify_marketdata1 --self-checks-only
→ 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under prices1.
