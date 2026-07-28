RLS PHASE 2 — users table carve-out. 3 tasks + verification.
This is the highest-sensitivity table in the app — every
request's identity resolution depends on it. Be conservative;
discover before changing; do not touch any table besides users
in this sprint.

CONTEXT: the users_bootstrap_and_org_visibility policy already
exists on users (Part 1 SQL applied) — a three-way OR: self-
lookup by auth0_sub (works with NO org context, for bootstrap),
org-scoped listing (for admin screens), or super_admin. The
RLS-aware pool wrapper from RLS Phase 1 (services/database.py)
already exists — DO NOT redesign it, EXTEND it.

STANDING RULES: org_id never from request body; no interactive
prompts; do not modify anything about DATABASE_URL or which
role the app connects as — that remains the bypass 'postgres'
role in every deployed environment, unchanged by this sprint.

=== TASK 1: Discover, don't assume ===
Read services/database.py's CURRENT state fresh (it was
rewritten in RLS Phase 1 — do not work from a stale mental model
of it). Read services/users.py's ensure_user() fresh too. Confirm:
  - Exactly where/how request middleware currently calls
    set_rls_context() (or whatever the current function is named
    post-Phase-1) and in what order relative to ensure_user()
  - Whether ensure_user() itself goes through pool.acquire() (and
    therefore through the RLS wrapper) or uses some other
    connection path
Report findings before making changes.

=== TASK 2: Add the third context variable ===
Extend services/database.py:
  - Add a THIRD ContextVar for the raw auth0_sub claim (alongside
    the existing org_id and is_super_admin vars from Phase 1)
  - Update the internal "apply RLS settings" function to also
    set_config('app.current_auth0_sub', ...) as part of the same
    atomic statement it already uses for the other two settings
  - Add a setter (matching the existing set_rls_context/
    reset_rls_context pattern) for this third variable

=== TASK 3: Fix the middleware ordering ===
In main.py's request middleware:
  - Set app.current_auth0_sub FIRST, immediately from the raw JWT
    claim, BEFORE calling ensure_user/load_principal
  - AFTER ensure_user/load_principal resolves the user's real
    org_id and role, update the org_id/is_super_admin context
    variables for the REST of the request's queries
  - Confirm this ordering doesn't break anything for a request
    where the user already exists (the common case) — this
    should be a strict ADDITION to the sequence, not a
    reordering that risks the existing happy path

=== VERIFICATION ===
Write verify_rls2.py (apps/api/scripts/) — pass/fail only, no
interactive prompts. Include a LOCAL diagnostic-style check
(connecting directly as app_service, using an env var Joe
supplies at test time, NEVER hardcoded or committed) that proves,
with REAL data, not just code inspection:
  [Y] With ONLY app.current_auth0_sub set (no org context at
      all — simulating a brand-new user's very first request),
      a SELECT for that user's own row by auth0_sub succeeds
  [Y] The SAME connection/context CANNOT see a DIFFERENT user's
      row (proves the bootstrap leg doesn't accidentally grant
      broad access)
  [Y] With org_id correctly set (simulating a normal, already-
      resolved request), an admin can see ALL users in their org,
      not just their own row
  [Y] A user in a DIFFERENT org is NOT visible under the first
      org's context
  [Y] is_super_admin=true sees users regardless of org
  [Y] Inserting a brand-new user row succeeds when the new row's
      auth0_sub matches app.current_auth0_sub (the actual
      ensure_user INSERT path)
  [Y] With NEITHER auth0_sub NOR org context set at all, zero
      rows are visible (safe default-deny preserved)
  [Y] Bypass role (postgres) behavior is completely unchanged —
      confirms production is unaffected by this sprint

Report each assertion explicitly. State clearly in your summary
that DATABASE_URL has not been touched and production behavior
is unchanged. Push when 100% pass — hold for manual review
regardless of tier, given the sensitivity of this table.
