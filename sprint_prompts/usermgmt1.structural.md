USERMGMT1 — fix User Management so it shows one row per person, says how each
person signs in, and gives admins the actions they actually need (password
reset email, resend invite, link a pending login to an existing user, approve /
reject), hides test and retired accounts, and removes leftover verify fixtures.
7 tasks + verification.

Context: Joe could not sign in to several accounts, tried resets, and was left
with a confusing user list. Investigation (2026-10-21, live DB + main @
f115737): emails ARE unique in the database (users_email_key), but the
/admin/users list JOINs user_roles, so a user with N roles shows N rows (Joe's
culmina.io account shows twice, gmail three times). Several accounts were never
linked to an Auth0 login (auth0_sub NULL), so signing in as them creates a new
pending row instead. Placeholder emails ('<sub>@placeholder.local') make
password resets impossible. 25 leftover verify fixtures (@test.local) clutter
the list. There is no way to send a password reset.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- routers/admin.py: GET /admin/users selects users LEFT JOIN user_roles LEFT
  JOIN roles LEFT JOIN profiles → one row per (user, role). Other routes:
  /admin/roles, /admin/users/settings, PUT /admin/users/{id}/role, POST
  /admin/users/{id}/approve and /reject (authz1, pending users), PATCH
  /admin/users/{id}, POST /deactivate, /reactivate, DELETE (anonymizes:
  anonymized_email / is_anonymized). Gates: _require_people_access,
  _require_manage_members, _forbid_self, _forbid_staff_target.
- apps/web/components/admin/UserManagement.jsx renders the list, the Invite
  modal (createInviteAction → POST /admin/invites; roles member / org_admin;
  super_admin not invitable; shows an enrollment_url — email delivery not
  wired), and per-user edit (name, role, profile, deactivate, delete).
- users: status CHECK active | pending | retired | rejected; is_active,
  deactivated_at, invite_status, invite_token (UNIQUE), auth0_sub (UNIQUE,
  nullable), email (UNIQUE), last_login_at. user_identities (authz1): user_id,
  org_id, auth0_sub UNIQUE, issuer, email, linked_via CHECK verified_email |
  duplicate_merge | operator.
- authz1 (merged): services/authz.py Principal from the DB, fail-closed;
  services/users.ensure_user links an unknown login by VERIFIED email or holds
  it pending; super admin only via PLATFORM_STAFF_EMAILS. No Auth0 Management
  API grant exists on either tenant.
- Two Auth0 tenants: 2nd Act dev-smmrfubsfscif3t1.us.auth0.com, Hollisworks
  dev-gy85vzuf6mruzv3j.us.auth0.com. Auth0's public endpoint
  POST https://<tenant>/dbconnections/change_password {client_id, email,
  connection} sends a reset email for database-connection users and needs no
  Management API token.
- Live data after Joe's cleanup (2026-10-21): real accounts — jlarizza@culmina.io
  (super_admin), jlarizza@gmail.com (super_admin, Google login linked via
  user_identities), Joe's Hollisworks login (super_admin, Hollisworks org,
  placeholder email by design), jpl99172@gmail.com (org_admin, now linked to
  sub auth0|6ad905abe9b1a4ec5f3bc328), jpl991721/2/3@gmail.com (member, auth0_sub
  NULL — never linked), two retired duplicates. Plus 25 @test.local fixture
  users from old verify runs (chancery*, udf01b*, udf02a*, wfmgr3*, wfmgr4*,
  rlscutover_creator); some are seeded by scripts still in use, some are
  orphans of crashed teardowns.
- Never delete a real user: retire. Every write keeps users.role and the RBAC
  grant in lockstep via services.rbac helpers (CLAUDE.md).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop, never start
a script in the background; run every command synchronously in the foreground.
Write the verify script but DO NOT RUN IT. Make NO real Auth0 calls (mock
them). Never print a secret, token or password. Column names from the live DB.
Do not change authz1's permission rules or provisioning order except where a
task says so. Light/cream content, navy frame (existing look).

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. How invites work end to end (POST /admin/invites, enroll/accept, what
      sets auth0_sub, why jpl99172/jpl991721-3 have auth0_sub NULL yet one has
      a last_login_at).
  1b. Which Auth0 client ids and connection names the API can read per tenant
      (Doppler names only) for /dbconnections/change_password; which Auth0
      connection each real account uses (database vs google-oauth2) as far as
      the DB shows (sub prefix, user_identities).
  1c. For each @test.local user: which verify script creates or seeds it,
      whether that script still exists and still relies on it, and every
      table row referencing it (FK list from pg_constraint).
  1d. Whether outgoing email exists anywhere in the API (for invites).

=== TASK 2: ONE ROW PER PERSON ===
GET /admin/users returns one row per user with roles as an array (name, id)
and the same filters, paging and permission envelope. Add fields: sign_in
(list of identities: issuer label "2nd Act" / "Platform", method "Email &
password" / "Google" / other from the sub prefix, linked_via), never_signed_in
(no auth0_sub and no identity), email_is_placeholder, is_test. Filters: status
(active / pending / deactivated / retired / rejected), role, and "show test
accounts" + "show retired" (both off by default). Role assignment still works
per role.

=== TASK 3: ACTIONS ===
Each behind the same gate as today's user edits (manage_members; staff targets
need super admin as today), each audited in audit_log:
  - Send password reset email: allowed only for a real (non-placeholder) email
    and a database-connection login; calls the matching tenant's
    /dbconnections/change_password (mocked in tests). Clear refusal text
    otherwise ("This account signs in with Google — reset the password in
    Google" / "No real email on file").
  - Resend invite: for never-signed-in invited users, returns a fresh
    enrollment link (rotates invite_token); shows it to copy (no email sending
    unless 1d finds a mailer).
  - Link to existing user: on a pending user, pick an active user in the same
    org; moves the pending login's sub onto the target (target.auth0_sub if it
    is NULL, else a user_identities row with linked_via 'operator'), retires
    the pending row, closes its pending to-dos. Refuses across orgs and onto
    a super admin unless the caller is super admin.
  - Approve / Reject pending: existing endpoints, surfaced clearly with an
    explanation of what pending means; the list suggests a likely match
    (same email, or name equal to an existing email) as "Looks like <user>".
  - Edit email: for an account whose email is a placeholder or that never
    signed in; validated, unique (case-insensitive), audited. Note in the UI
    that this does not change the Auth0 login email.
  - Delete stays anonymize; label it "Retire (keeps history)".

=== TASK 4: PENDING AND EMAIL AT SIGN-UP ===
The Access pending page explains the wait and, when the account has no real
email, asks the person for their email address (stored as an unverified
contact_email; shown to admins; never used for linking without verification).
Org admins get one to-do per pending user (existing), linking to People &
Access filtered to pending.

=== TASK 5: TEST ACCOUNT HYGIENE ===
  - users.is_test (boolean, default false; migration). Set true for every
    @test.local user and every fixture user verify scripts create from now on
    (shared verify helpers set it). Hidden from lists by default.
  - Orphaned fixtures (1c: no live script relies on them): delete them and
    their dependent fixture rows in FK child-before-parent order read from
    pg_constraint, reporting counts per table; never touch a row that belongs
    to a real user. Fixtures still relied on: keep, is_test = true.

=== TASK 6: REAL PROOF ===
  - Reproduce first: before the change, GET /admin/users returns 2 rows for a
    fixture user with 2 roles; after, 1 row with both roles.
  - Password reset: called with the right tenant, client id, connection and
    email for a database login (mock records the request); refused with the
    right message for Google, placeholder and Platform-placeholder accounts.
  - Link: a pending fixture login linked to an existing fixture user resolves
    to that user on its next request (ensure_user), the pending row is
    retired, its to-dos closed; cross-org refused; non-super onto super
    admin refused.
  - Every action: org admin allowed, member refused (403), identical request;
    data unchanged on refusal (re-read); audit_log row on success.
  - Test hygiene: list hides is_test and retired by default and shows them
    when asked; orphan fixtures removed with zero rows referencing them left;
    no real user touched (count + checksum of real users before/after).

=== TASK 7: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md: the actions, the reset mechanism and its limits (no
Management API; Google logins reset at Google), is_test, and how to link a
duplicate login.

=== VERIFICATION: apps/api/scripts/verify_usermgmt1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app (authz1 rule). Hydrate secrets from Doppler over
HTTPS. Mock Auth0 entirely. Wrap every section so an exception is a [FAIL];
teardown at start and end in FK child-before-parent order from pg_constraint,
then re-read every touched table for its exact pre-test count. Fixture users
created explicitly with the auth0_sub the mocked token carries, is_test = true.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Fixture values satisfy live CHECK and FK constraints (pg_constraint,
    contype ::text). Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; an empty list never passes a check.
  - Never spawn a background process. Regression subprocesses get
    VERIFY_NESTED=1, run after teardown, 1200 s timeout; with VERIFY_NESTED=1
    this script skips its regression section with one '[SKIP] nested run' line.
  - Never touch real users (Task 5 deletes only proven orphan fixtures).

Assertions:
  [Y] Task 1 findings reported (invite flow, tenant client ids/connections,
      every @test.local user with its script and references, mailer or none)
  [Y] One row per user: fixture with 2 roles → 1 row, roles array of 2
      (reproduced as 2 rows on the old query first)
  [Y] sign_in / never_signed_in / email_is_placeholder / is_test correct for
      fixtures of each kind
  [Y] Filters: status, role; test and retired hidden by default, shown on request
  [Y] Password reset: correct mocked request for a database login; refusals
      for Google, placeholder email, never-signed-in without email
  [Y] Resend invite rotates invite_token and returns a working enrollment link
  [Y] Link pending → existing: next request resolves to the target; pending
      row retired; to-dos closed; cross-org and non-super→super refused
  [Y] Edit email: unique case-insensitively, audited; refused for a normal
      signed-in account
  [Y] Every action: org admin allowed, member 403 on the identical request,
      data unchanged on refusal, audit_log row on success
  [Y] Pending page asks for an email when none is real; stored as
      contact_email, never used for linking
  [Y] Orphan fixtures removed (per-table counts reported); kept fixtures
      flagged is_test; real users unchanged (count + checksum)
  [Y] npm run build exits 0; node tests for the list and actions pass
  [Y] Regression: verify_authz1.py and verify_navregroup.py each run ONCE as
      flat subprocesses AFTER teardown with VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test count
