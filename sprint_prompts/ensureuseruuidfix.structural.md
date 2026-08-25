ENSURE_USER — uuid_generate_v4() SCHEMA FIX. 4 tasks +
verification. Real, confirmed live production error, from real
Render logs:

ERROR in ensure_user (sub='auth0|6a7c8b473069946d5a6d5400'):
function uuid_generate_v4() does not exist
HINT: No function matches the given name and argument types.

CONFIRMED, live: uuid_generate_v4() exists in the 'extensions'
schema, NOT 'public'. app_service's search_path is "$user",
public only (confirmed previously, same reason every portfolio.*
reference needed schema-qualification). This is a real,
CURRENTLY BROKEN production path — every brand-new user
provisioning attempt (any first-time Hollisworks-tenant login)
fails here.

GENUINE OPEN QUESTION, INVESTIGATE, DO NOT ASSUME: every
portfolio.* table also defaults its id column to
uuid_generate_v4() and has successfully inserted rows under
app_service repeatedly tonight. Task 1 must determine EXACTLY
why ensure_user's call fails while those succeed — the most
likely explanation is that ensure_user's INSERT calls
uuid_generate_v4() directly as literal SQL text (not relying on
a column DEFAULT), which resolves differently — but CONFIRM this
against the real code, do not assume it.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

STANDING RULES: no interactive prompts.

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the REAL current services/users.py ensure_user INSERT
      statement (apps/api/services/users.py, around line 204 per
      the live traceback) — confirm EXACTLY how it generates the
      new row's id. Is uuid_generate_v4() called as literal SQL
      text in the INSERT, or is it relying on a table column
      DEFAULT? Report the real, exact SQL.
  1b. Grep the ENTIRE apps/api codebase for every other literal,
      inline call to uuid_generate_v4() in application SQL text
      (not a table's DEFAULT clause in a migration file) — report
      every file/line found. This is the real question of how
      widespread this specific pattern is, versus this being an
      isolated occurrence in ensure_user alone.
  1c. Confirm the real, current users table's id column
      definition (DEFAULT clause) — does IT also default to a
      bare uuid_generate_v4(), and if so, why would a bare INSERT
      relying on that default succeed while ensure_user's
      apparently does not? (If ensure_user explicitly supplies id
      in its INSERT rather than omitting it and relying on the
      DEFAULT, that is the likely answer — confirm directly.)
Report all three findings before proceeding.

=== TASK 2: FIX — every occurrence found in Task 1b ===
Schema-qualify every literal uuid_generate_v4() call found:
extensions.uuid_generate_v4() — matching the exact convention
already used for portfolio.* table references throughout tonight
(explicit schema qualification, not relying on search_path).

=== TASK 3: REAL PROOF ===
  - A real ensure_user call for a genuinely new auth0_sub
    (simulate the exact real scenario: a Hollisworks-tenant
    login with no existing row) succeeds and creates a real row
    with a real UUID id — proven against the live database, not
    a mock.
  - The SAME fix does not regress any EXISTING successful
    uuid_generate_v4() usage elsewhere (the portfolio tables'
    table-level DEFAULTs are untouched and still work).

=== TASK 4: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: record this as a real, found-and-
fixed production bug — new-user provisioning for ANY brand-new
identity (Hollisworks-tenant or otherwise) was broken. Note this
was the actual root cause behind tonight's "jlarizza@gmail.com
sees limited access" investigation — not a menu-gating issue at
all, but this.

=== VERIFICATION: apps/api/scripts/verify_ensureuseruuidfix.py
===
Pass/fail only. No interactive prompts. Real database.

Assertions:
  [Y] Report Task 1's three findings explicitly, including the
      EXACT reason ensure_user's call failed while other tables'
      inserts succeeded
  [Y] A real ensure_user call for a brand-new, never-seen
      auth0_sub succeeds and returns a real, valid UUID id
  [Y] The created row is genuinely findable afterward by that
      same auth0_sub
  [Y] Every other occurrence found in Task 1b is fixed and proven
      individually, not just the ensure_user case
  [Y] Teardown: zero leftover rows
