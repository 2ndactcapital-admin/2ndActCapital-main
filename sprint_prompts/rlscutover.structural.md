RLS ENFORCEMENT CUTOVER — DATABASE_URL TO app_service. 5 tasks +
verification. CONFIRMED, LIVE, URGENT FINDING: the deployed
application's DATABASE_URL currently connects as the `postgres`
role (rolbypassrls=true), not `app_service` (rolbypassrls=false).
Every RLS policy across all ~77 public-schema tables plus
portfolio is currently NOT enforced by the running application.
A separate, working APP_SERVICE_DATABASE_URL secret already
exists. Strong hypothesis: this drifted during this session's own
repeated DATABASE_URL recovery work — verify, do not assume.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

*** CRITICAL: this changes the real, live production connection
role for the entire application. A real, working rollback path
must exist and be proven before this merges, not just described. ***

=== TASK 1: DISCOVER — confirm the real, full scope before
touching anything ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm exactly which real Render service(s) currently read
      DATABASE_URL as the `postgres` role — is this the main API
      only, or others too?
  1b. Enumerate every table in public + portfolio schemas that
      has NO RLS policy at all, or a policy that does not cleanly
      scope to org_id the way the application's real queries
      expect. This is the real risk surface for the cutover.
  1c. Confirm whether ANY real, current application code path
      genuinely requires postgres-level (bypass) access for a
      legitimate reason (e.g., a cross-org admin report, a
      platform-level job) — if so, that path needs its OWN
      explicit, narrow exception, not blanket bypass for
      everything.
  1d. Confirm app_service's real, current GRANT set — does it
      have the privileges every real application table operation
      actually needs (SELECT/INSERT/UPDATE/DELETE on every table
      the app touches), independent of RLS?

=== TASK 2: FIX — whatever Task 1b/1d found missing ===
Add or correct RLS policies for any table found genuinely missing
one. Add or correct GRANTs for app_service on any table found
missing required privileges. Do NOT weaken any existing policy to
make this pass easier — the goal is correct enforcement, not a
green checkmark.

=== TASK 3: THE CUTOVER, WITH A REAL ROLLBACK PATH ===
Change the real Render/Doppler config so DATABASE_URL uses
app_service's connection string. Document the EXACT, real,
one-step rollback (reverting to the prior postgres-based value)
directly in docs/PROJECT_STATUS.md, so it can be executed in
under a minute if something breaks post-cutover.

=== TASK 4: REAL PROOF ===
  - Every real, existing application endpoint this sprint can
    reach still functions correctly AFTER the cutover — a genuine
    smoke test across a representative sample of read AND write
    operations across multiple real modules (portfolio, workflow,
    fee, TA model, UDF), not just one.
  - Cross-org isolation is now GENUINELY enforced at the database
    level — prove this by attempting a deliberately cross-org
    query THROUGH THE REAL APPLICATION CODE PATH and confirming
    it returns nothing / is refused, where before the cutover the
    same raw query (run directly as postgres) would have returned
    real cross-org data.
  - Any legitimate cross-org path found in Task 1c still works
    correctly, through its own explicit, narrow, documented
    exception — not broken by the cutover.
  - If ANY real functionality broke, the documented rollback
    genuinely restores it — prove this by actually executing the
    rollback in this sprint's own verification and confirming
    the prior (broken RLS, working app) state returns.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and
docs/CROSS_PROJECT_STATUS_RECONCILIATION.md: record this as a
real, serious finding that was found and fixed, not just
discovered — with the exact date, the real root cause if
confirmed, and the rollback procedure kept visible for future
reference.

=== VERIFICATION: apps/api/scripts/verify_rlscutover.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Every table needing a fix (per 1b/1d) is confirmed fixed,
      not just identified
  [Y] A representative smoke test across multiple real modules
      passes post-cutover
  [Y] Cross-org isolation is proven THROUGH the real application
      path, contrasted with the pre-cutover bypass behavior
  [Y] Any legitimate cross-org exception (per 1c) still works,
      narrowly and explicitly
  [Y] The rollback path is proven by actually executing it
  [Y] Teardown: zero leftover rows
