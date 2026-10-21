AUTHZ1 — one database-backed, fail-closed authorization path for every API
endpoint; no access granted by the absence of a claim or a role; safe
provisioning of unknown logins (link by verified email or hold as pending —
never auto super admin); duplicate accounts linked. 9 tasks + verification.

Context: navregroup's discovery and a code review on 2026-10-21 found that
authorization inside an organization is largely open in production. Real
Auth0 access tokens carry only sub/iss/aud/azp/scope — no roles or
permissions claims — and the token-claim helpers in services/permissions.py
default to ALLOW / STAFF when the claim is absent. The DB-backed
services/rbac.has_permission also default-allows a user with no user_roles
rows. Provisioning auto-creates any unknown login, and any login issued by the
Hollisworks tenant is made super_admin. Org isolation (RLS on org_id) is NOT
in question and must not change; this sprint is about who can do what inside
an org, and who becomes a user at all.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (read from main @ a911ac7 and the live
database, 2026-10-21):
- services/permissions.py: get_roles / get_permissions read token claims;
  is_staff() returns True when no roles claim; has_permission() returns True
  when no permissions claim (and staff implies all); require_permission /
  require_staff build on them. Call sites (token-claim checks) by router:
  spv.py 23 · marketplace.py 20 · entity_graph.py 9 · vdr.py 3 ·
  investment_profile.py 3 · portfolio.py 2 · semantic_search.py 1 ·
  ownership_tree.py 1 · document_links.py 1 · assistant.py 1 ·
  allocation_lens.py 1. get_user_id falls back to DEV_USER_ID
  00000000-0000-0000-0000-0000000000aa when the token has no UUID identity.
- services/rbac.py has_permission: Super Admin first (load_principal /
  is_super_admin), then "default-allow when the user has no roles", then the
  user's permissions via user_roles → role_permissions. require_permission
  raises 403 "Permission required: <name>". is_org_admin /
  can_manage_org_settings resolve manage_org_settings through it.
- apps/web/lib/menuVisibility.mjs canPerm keeps the same no-roles
  default-allow; canPermStrict is fail-closed.
- services/users.ensure_user: (1) lookup by auth0_sub; (2) if sub parses as a
  UUID matching a users.id, use that row (verify-script stub path); (3) insert
  a new row — role 'super_admin' and org Hollisworks
  (bb347258-8f28-4f49-8cc9-e29ccad82884) when main.is_hollisworks_claims
  (token iss == the Hollisworks issuer), else 'member' in the caller's org;
  email via the tenant's /userinfo, else '<sub>@placeholder.local'. An
  existing row from the Hollisworks issuer is promoted to super_admin and
  moved to the Hollisworks org.
- Two Auth0 tenants: 2nd Act dev-smmrfubsfscif3t1.us.auth0.com,
  Hollisworks dev-gy85vzuf6mruzv3j.us.auth0.com (getAuthClientForHost).
- Real (non-fixture) users today: jlarizza@culmina.io (super_admin, 2nd Act,
  roles admin), jlarizza@gmail.com (super_admin, 2nd Act, roles
  admin,org_admin), auth0|6a7c8b473069946d5a6d5400@placeholder.local
  (super_admin, Hollisworks org, no roles — Joe's Hollisworks login; its id
  be8165db-84d7-4e69-91d3-62f603530824 is reviewer_id on all 79
  note_gold_values), google-oauth2|114785352792351858712@placeholder.local
  (member, 2nd Act, no roles — Joe's Google login), jpl99172@gmail.com
  (org_admin, 2nd Act, auth0_sub NULL). Plus fixture users from old sprints.
- Joe is the only real person with a login; no member or client has access
  yet. That is why this can be fixed now without a migration plan for others.
- Verify scripts call the API in-process with stubbed tokens and rely on the
  UUID-sub path and on claims; they run with the same Doppler prd config as
  production.
- CLAUDE.md: super-admin bypass is checked FIRST via one shared helper; the
  Permission Envelope pattern; org_admin resolved by permission; never branch
  on users.role == 'org_admin'; every write path keeps users.role and the RBAC
  grant in lockstep (services.rbac grant/revoke helpers).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop, never start
a script in the background; run every command synchronously in the foreground.
Write the verify script but DO NOT RUN IT. Never print a secret, a token or a
tax ID. Column names from docs/schema_snapshot.sql / the live DB. Do not
change org isolation (RLS, get_org_id), the four permanently agent-unreachable
action classes, or any business logic beyond the gate. Do not delete any real
user or real data; retire, never delete. Do not change Auth0 configuration
(the operator does that).

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every FastAPI route (enumerate app.routes): method, path, router, the
      gate it applies today (token-claim helper / rbac / super admin / staff /
      none beyond auth), and whether it reads or writes. Write it to
      docs/AUTHZ1_ROUTES.md as a table. Mark [FIND] every write with no
      effective gate in production.
  1b. Every role and permission in the live roles / permissions /
      role_permissions tables, per org, and which real users hold what.
  1c. How "staff" is meant to be defined (investment_staff / admin / owner /
      super_admin; staff_assignments; teams) and where the DB records it.
  1d. Every place a verify script depends on the UUID-sub path, a roles or
      permissions claim, or DEV_USER_ID — so the test path can be kept
      explicitly, not by accident.
  1e. How /users/me builds the permissions the front end reads.

=== TASK 2: ONE PRINCIPAL, LOADED FROM THE DATABASE ===
Create services/authz.py: a Principal (user_id, org_id, is_super_admin,
is_staff, status, role names, permission set) loaded from the DB once per
request (FastAPI dependency, cached on request.state). is_staff comes from the
DB (role membership per 1c), never from token claims. Helpers:
require_authenticated, require_active (refuses a 'pending' or retired user),
require_permission(name), require_staff, require_super_admin — each Super
Admin first, then exact, with NO default-allow branch. 403 bodies name the
missing permission; 401 when there is no resolvable user. Reuse
services.rbac's queries; do not write a second resolver.

=== TASK 3: SEED ROLES SO NOTHING LEGITIMATE BREAKS ===
Before any gate flips: ensure every org has explicit roles carrying the
permissions its users legitimately need (a baseline 'member' role with the
member permissions the member-facing pages require; staff roles per 1c;
org_admin unchanged). Grant every real user the role matching their current
users.role. Report, per real user, effective permissions before and after; the
only intended loss is access that existed solely through a default-allow.

=== TASK 4: REPLACE EVERY GATE ===
  - Every token-claim check in the 11 routers → the Task 2 helper with the
    SAME permission name (or require_staff). services/permissions.py keeps
    get_user_id only where still needed; get_roles / get_permissions /
    is_staff / has_permission / require_permission / require_staff are
    removed or become thin wrappers over services.authz — no module may grant
    on an absent claim.
  - services.rbac.has_permission loses the "no roles → allow" branch.
  - The DEV_USER_ID fallback: 401 instead, except under the test flag (Task 6).
  - Document Search, semantic search, allocation lens and the assistant read
    is_staff from the Principal (fixes navregroup's "every real caller is
    staff" [FIND]).
  - Every write route found ungated in 1a gets the gate its feature implies
    (state it in AUTHZ1_ROUTES.md); document-review endpoints get a per-user
    visibility gate reusing navregroup's document_visible.

=== TASK 5: PROVISIONING AND DUPLICATE ACCOUNTS ===
ensure_user for an unknown sub, in order:
  1. Fetch the profile from the issuing tenant's /userinfo. If the email is
     verified and matches exactly one active user in the org that tenant maps
     to, LINK the new sub to that user (a user_identities table: user_id,
     auth0_sub, issuer, created_at; users.auth0_sub stays as the primary) and
     use that user.
  2. Otherwise create the user with status 'pending', no roles, real email if
     known. A pending user is authenticated but refused everything except
     /users/me and a front-end "Access pending — an administrator has been
     notified" page; org admins see pending users in People & Access and can
     approve (grant a role) or reject.
  3. Super admin is NEVER granted by issuer alone. A Hollisworks-issuer login
     becomes super admin only if its verified email is in the Doppler setting
     PLATFORM_STAFF_EMAILS (comma-separated; seed it in the code comment as
     jlarizza@gmail.com,jlarizza@culmina.io — the operator adds the secret).
     Otherwise it is pending in the Hollisworks org. Existing super admins are
     not demoted by this sprint.
  4. Existing duplicates: link Joe's Google login
     (google-oauth2|114785352792351858712) to jlarizza@gmail.com's 2nd Act user
     via user_identities when its verified email matches, and mark the
     placeholder member row status 'retired' (references kept; report every
     table that references it). Backfill the Hollisworks login's email from
     /userinfo when reachable. Do not merge rows across orgs.
  5. touch_last_login runs for every resolution path (fixes "Last sign-in:
     Never" on jlarizza@culmina.io).

=== TASK 6: KEEP THE TEST PATH EXPLICIT ===
The UUID-sub stub and any claim-based test identity work only when the process
sets AUTHZ_TEST_MODE=1 in os.environ before the app is imported (verify
scripts do this in-process); production never sets it, and the API logs a
warning at startup if it is set. Update the shared verify helpers so every
existing verify script sets it and seeds explicit roles for its fixture users
instead of relying on default-allow. List every script changed.

=== TASK 7: FRONT END ===
/users/me returns the Principal's effective permissions, staff flag and
status from Task 2. menuVisibility.canPerm loses the no-roles default-allow
(fail closed, Super Admin first). The pending page renders for status
'pending'. Write controls stay inside permissions.can_write with no truthy
fallback (CLAUDE.md envelope rule).

=== TASK 8: REAL PROOF ===
  - Reproduce first: before the change, on fixtures, a plain member with no
    roles performs a staff-only SPV write and a manage_deals Marketplace write
    through the API and succeeds; an unknown Hollisworks-issuer login is
    created super_admin.
  - After: the same identical requests are refused (403 naming the
    permission) and leave data unchanged (before/after re-read); staff and
    org admin with the right role succeed.
  - Route matrix: for EVERY route in 1a, the expected outcome per persona
    (anonymous, pending, member, staff, org admin, super admin, other-org
    user) is asserted from AUTHZ1_ROUTES.md — writes exercised with a fixture
    payload, reads checked for status and for data scoped to the caller.
  - Provisioning: verified-email match links; unverified or no match →
    pending, refused, visible to org admin; Hollisworks issuer with an
    allow-listed verified email → super admin, any other → pending.
  - Cross-org isolation unchanged, under app_service (rolbypassrls false),
    both directions.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and CLAUDE.md: the single services.authz path and
"no default-allow anywhere" as a rule; the route table; the provisioning
rules; PLATFORM_STAFF_EMAILS; AUTHZ_TEST_MODE; and the operator's Auth0 steps
(disable sign-ups and unused social connections on both tenants).

=== VERIFICATION: apps/api/scripts/verify_authz1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS. Mock Auth0 /userinfo. Wrap every section so an exception
is a [FAIL], never a crash; teardown at start and end in FK
child-before-parent order read from pg_constraint, then re-read every touched
table for its exact pre-test count. Fixture users created explicitly with the
auth0_sub the mocked token carries; teardown finds them by auth0_sub, email
pattern and user_identities.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Fixture values satisfy live CHECK and FK constraints (pg_constraint,
    contype ::text). Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; isolation under app_service with
    rolbypassrls = false asserted. An empty list never passes a check.
  - Never spawn a background process. Regression subprocesses get
    VERIFY_NESTED=1, run after teardown, 1200 s timeout; with VERIFY_NESTED=1
    this script skips its regression section with one '[SKIP] nested run' line.
  - Never touch real users' roles except the Task 3/5 changes, real
    note_gold_values, or real deals/SPVs/entities.

Assertions:
  [Y] Task 1 findings: docs/AUTHZ1_ROUTES.md lists every app route (count
      equals len(app.routes) minus static/docs routes) with its gate before
      and after; every ungated production write marked [FIND]
  [Y] No module grants on an absent claim: source scan of apps/api finds no
      "is None: return True" style default-allow in authorization helpers;
      services.rbac.has_permission has no no-roles branch
  [Y] Bug reproduced BEFORE (old helpers on fixtures): no-role member's SPV
      staff write and Marketplace manage_deals write succeed; unknown
      Hollisworks-issuer login created super_admin
  [Y] AFTER: identical requests refused 403 naming the permission, data
      unchanged (independent re-read); the right role succeeds
  [Y] Route matrix: every route × seven personas matches AUTHZ1_ROUTES.md
      expectations; zero unexpected 2xx
  [Y] is_staff comes from the DB: a member is not staff in Document Search,
      semantic search and the assistant; a staff fixture is
  [Y] Provisioning: verified email match → linked via user_identities;
      unverified / no match → pending, refused on a data route, listed for org
      admin approval; approval grants a role and admits; Hollisworks issuer +
      allow-listed verified email → super admin; otherwise pending
  [Y] Real users: each real user's effective permissions after == the Task 3
      report; Joe's accounts keep super admin; the Google placeholder row is
      retired and its sub linked to jlarizza@gmail.com; last_login_at set on
      every resolution path
  [Y] AUTHZ_TEST_MODE: UUID-sub stub refused without it, accepted with it;
      DEV_USER_ID fallback → 401 without it
  [Y] Front end: /users/me returns DB permissions and status; canPerm has no
      default-allow (node harness with a no-roles payload sees only
      always-visible items); pending page renders for status 'pending';
      npm run build exits 0
  [Y] Cross-org isolation unchanged in both directions
  [Y] Regression: verify_navregroup.py, verify_bulkfetchui2.py and the
      menu-visibility harness each run ONCE as flat subprocesses AFTER
      teardown with VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real note_gold_values unchanged (count and checksum)
