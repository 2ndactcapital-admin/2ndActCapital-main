RLS PHASE 1 — non-bypassing connection wrapper, org-context
middleware, ONE pilot policy. 4 tasks + verification. This is
the highest-stakes sprint of the project so far — a mistake
here affects EVERY query in the app. Be conservative; discover
before changing; do not expand scope beyond what's listed.

CONTEXT: a new database role 'app_service' exists (created
manually by Joe, confirmed rolbypassrls=false). It is NOT yet
the app's live connection — DATABASE_URL in Render still points
at the original postgres role, and this sprint MUST NOT change
that. 74 of ~77 org_id-bearing tables already have RLS enabled
with ZERO policies (Supabase's per-table "enable RLS?" prompt
was answered yes on every CREATE TABLE, but no policy was ever
attached) — meaning if the connection were switched today,
every one of those tables would return zero rows to any
non-bypass role. This sprint's job is to build the mechanism
and prove it safe on ONE table — trusted_contacts (Part 1 SQL
already added its policy) — NOT to roll out policies broadly,
and NOT to touch the live connection.

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: Discover the real transaction pattern first ===
Read apps/api/services/database.py (already read — the current
get_pool() returns a plain asyncpg.Pool, and callers do
`async with pool.acquire() as conn:` scattered across 70+ files).
CRITICAL DISCOVERY: does any existing code path rely on
individual statements auto-committing independently within one
`acquire()` block (e.g. a function that intentionally continues
past a failed statement, expecting partial commits)? Search for
any acquire() block containing multiple execute() calls with
error handling BETWEEN them that would behave differently if the
whole block became one atomic transaction. Report any such
pattern found before proceeding — if none exist, proceed with
Task 2's design; if some do, flag them explicitly in your final
summary rather than silently changing their behavior.

=== TASK 2: Also discover ensure_user's bootstrap flow ===
Read services/users.py's ensure_user() (already documented
elsewhere: resolves by auth0_sub, may insert a new user row).
This runs BEFORE any org context can be established for a
brand-new user. Report exactly what table(s) it reads/writes and
confirm: does it operate on 'users' specifically? (already known
to have RLS enabled, no policy). This sprint does NOT need to
solve this carve-out yet (no policy is being added to 'users' in
this sprint) but MUST report clearly how ensure_user currently
works so a future sprint can design the correct carve-out
without re-discovering this.

=== TASK 3: Build the org-context wrapper ===
Modify apps/api/services/database.py:
  - Add a Python ContextVar (or two: one for org_id, one for
    is_super_admin boolean) — module-level, per-async-task scoped
  - Change the pool-acquisition pattern so that entering
    `async with pool.acquire() as conn:` (KEEP THIS EXACT SYNTAX
    UNCHANGED so none of the 70+ existing call sites need to
    change) now transparently: opens an explicit transaction,
    runs `SET LOCAL app.current_org_id = <value>` and
    `SET LOCAL app.is_super_admin = <value>` as the first
    statements using whatever the ContextVar currently holds,
    then commits on clean exit / rolls back on exception — this
    likely means wrapping asyncpg's connection/transaction
    objects, or providing a custom context manager that replaces
    the raw pool.acquire() while keeping the same call-site
    syntax. Get this exactly right — re-read asyncpg's
    transaction API docs/existing usage in this codebase before
    writing it.
  - Add a small FastAPI middleware (in main.py) that runs early
    per-request, resolves org_id and is_super_admin using the
    EXISTING get_org_id(request)/is_super_admin(principal) logic
    already in the codebase (do not reinvent), and sets the
    ContextVars before the route handler runs. Clear/reset them
    at the end of the request.
  - ADD DOCUMENTATION: a clear comment block at the top of
    database.py explaining (a) that the SET LOCAL/RLS mechanism
    itself is standard Postgres and portable to ANY Postgres host,
    (b) that the Supabase-SPECIFIC parts are isolated to the
    create_pool() call — specifically ssl="require",
    statement_cache_size=0 (required for Supabase's transaction-
    mode pooler/pgBouncer), and the project-ref-qualified username
    format in DATABASE_URL — and that migrating to a different
    Postgres host in the future should only require changing
    this function, not the RLS policies or the ContextVar
    mechanism.

=== TASK 4: Test the pilot policy directly against app_service
— NOT against Render ===
Using a LOCAL script or direct psql-equivalent test (NOT by
changing any deployed environment variable), connect AS
app_service (Joe will need to provide this connection detail
via env var for the test only, scoped to this verification, never
committed to any file) and confirm:
  - With app.current_org_id correctly set to the real org's UUID,
    a query against trusted_contacts for that org returns its
    rows normally
  - With app.current_org_id set to a DIFFERENT (fake/test) org's
    UUID, the SAME rows are correctly INVISIBLE
  - With app.is_super_admin set to 'true', rows are visible
    regardless of org_id
  - With NEITHER setting present at all (simulating a bug where
    the middleware failed to run), the query returns ZERO rows
    (safe default-deny, not an error, not accidental full access)

=== VERIFICATION ===
Write verify_rls1.py (apps/api/scripts/) — pass/fail only, no
interactive prompts. This verify script CONNECTS AS app_service
for its RLS-specific assertions (read DATABASE_URL for
app_service from an env var Joe provides at test time, never
hardcode it, never write it to a file).

Assertions to include:
  [Y] trusted_contacts_org_isolation policy exists (query
      pg_policies)
  [Y] Task 1's discovery findings reported (any risky multi-
      statement pattern found or confirmed none)
  [Y] Task 2's discovery findings reported (ensure_user's real
      flow, for a future sprint's reference)
  [Y] Connecting as app_service WITH org_id set to a test org
      sees only that org's trusted_contacts rows
  [Y] Connecting as app_service WITH a DIFFERENT org_id set does
      NOT see the first org's rows
  [Y] Connecting as app_service WITH is_super_admin=true sees
      rows regardless of org_id
  [Y] Connecting as app_service WITH NEITHER setting present
      returns ZERO rows (safe default-deny confirmed)
  [Y] Existing app functionality UNCHANGED when connecting as
      the ORIGINAL postgres role (bypass role) — confirms this
      sprint did not silently break anything for the CURRENT
      live connection, which remains on postgres, not app_service
  [Y] npm run build / existing API smoke path still works
      unaffected (the app is still connecting as postgres, this
      sprint changes NOTHING about production's actual behavior
      yet — only proves the mechanism works when used)

Report each assertion explicitly. In your final summary, state
CLEARLY that DATABASE_URL has NOT been changed anywhere, the
live app is completely unaffected by this sprint, and switching
to app_service remains a deliberate manual step for Joe to do
separately, only after he has personally reviewed database.py's
diff. Push when 100% pass — do NOT auto-merge regardless of risk
tier; this holds for review no matter what.
