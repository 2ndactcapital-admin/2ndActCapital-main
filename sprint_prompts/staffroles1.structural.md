STAFFROLES1 — split "staff" into two separate ideas: WHO MAY SEE STAFF DATA
(every internal role of the firm — org admin, support staff, adviser,
compliance …) and WHO SKIPS PERMISSION CHECKS (unchanged: today's
STAFF_ROLE_NAMES). After bugfix2, org admins and support staff are refused
on the staff-only read routes (CRM and others) because neither role counts as
staff; simply adding them to STAFF_ROLE_NAMES would hand them every
permission (approvals included). 6 tasks + verification.

Context: bugfix2 (merged as "merge bugfix2") gated 73 ungated read routes:
24 staff-only via require_staff, 3 staff-or-org-admin, 18 member-scoped …;
member isolation is proven. Joe set the test accounts on 2026-11-04 (DB):
jpl991723 = org admin (role org_admin, profile Org Admin), jpl991722 =
support staff (role support_staff, profile CSA / Ops), jpl991721 = member
(role member, profile Member, no permission sets). The next HollisMuse pass
uses them, so org admin and support staff must be able to do their jobs and
no more.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after the bugfix2 merge; Task 1
confirms):
- services/authz.py:49 STAFF_ROLE_NAMES = {"investment_staff", "admin",
  "super_admin", "owner"}; :174 Principal.is_staff = admitted AND (super OR
  a STAFF_ROLE_NAMES role); require_staff (:242) admits super or is_staff;
  require_staff_or_permission (:249/:253) admits super OR is_staff OR the
  permission; further is_staff short-circuits at :283, :306, :346 (the last
  also accepts rbac.ORG_ADMIN_PERMISSION). is_staff therefore BYPASSES
  permission checks.
- routers using require_staff: 20 files.
- apps/web/lib/menuVisibility.mjs: isStaffStrict(me) = super admin OR
  me.is_staff === true (mirrors require_staff); an org-admin branch exists
  (Joe 2026-10-23).
- Roles in org 2nd Act (name — permissions — holders): admin 30/2,
  advisor 12/0, compliance 1/0, fund_finance 1/0, investment_committee 1/0,
  investment_staff 13/0, ir_member_relations 0/0, member 10/5,
  member_manager 5/0, next_gen 2/0, org_admin 27/3, super_admin 32/2,
  support_staff 8/2, plus two udf*verify_noperms fixture roles.
- PROJECT_STATUS (bugfix2): REP-01 / DEAL-01 — creating entities/households
  and deals is "staff / super admin only BY DESIGN".
- Client roles (NOT internal): member, next_gen (and any community/member
  profile). Every other non-fixture role above is an internal firm role.
- vhk1/vhk2: apps/api/scripts/_verify_self.py; docs/VERIFY_RULES.md; every
  verify supports --self-checks-only and --cleanup-only.
- Regression: this changes who passes require_staff → re-run
  verify_bugfix2.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; never start a script in the background; write the verify but DO
NOT RUN IT; no network; never print a secret. NEVER widen the permission
bypass: the set of roles that skip permission checks stays exactly
STAFF_ROLE_NAMES. Never let a client role (member, next_gen) see staff data.
Do not change real users' roles in data. Fail closed for an unknown role.
Light/cream UI. Commit ALL sprint files (excluding sprint_prompts/logs) on
the current branch at the end with message "staffroles1: <one line per
change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Every use of is_staff / require_staff / isStaffStrict (API and web):
      for each, is it answering "may this person SEE staff data / staff
      screens" (→ internal) or "may this person skip a permission check /
      act" (→ bypass)? Table: file, line, meaning.
  1b. The 24 staff-only read routes from bugfix2 and the 3
      staff-or-org-admin ones: which permission (if any) each should also
      require for a non-bypass internal role (e.g. CRM read: view_crm /
      view_members if such permissions exist) — or "any internal role".
  1c. What each internal role's permissions let it do today, and which
      screens org_admin and support_staff need for their jobs (CRM read,
      portfolio views, instructions, documents, workflows run console …)
      versus approvals they must NOT get (proposals, instructions, blocks,
      policies, authority, fee runs).

=== TASK 2: THE TWO CONCEPTS ===
  - services/authz.py: INTERNAL_ROLE_NAMES (every internal firm role; client
    roles excluded; fixture roles excluded) and Principal.is_internal
    (admitted AND (super OR an internal role)). is_staff unchanged
    (bypass). require_internal(request) for staff-DATA reads.
  - /users/me returns is_internal alongside is_staff; menuVisibility gains
    isInternalStrict for staff screens, keeping isStaffStrict only where
    1a says it means bypass.

=== TASK 3: APPLY ===
Each 1a "see" use moves to is_internal / require_internal (plus the
permission 1b names, if any, for non-bypass roles); each "act/bypass" use is
left as is. Approvals and writes keep their permission checks — an internal
role without the permission is refused. AUTHZ1_ROUTES.json updated for every
changed route; authz1_routes.py --check passes.

=== TASK 4: STAFF VISIBILITY NOTE ===
Staff-visibility enforcement (staff_assignments) stays OFF; say so in
PROJECT_STATUS — every internal role sees all of the org's families, as staff
did before.

=== TASK 5: REAL PROOF ===
  - For each of the 24 + 3 routes: org_admin fixture → 200 (or the 1b
    permission decides), support_staff fixture → 200 / 403 per 1b, member
    fixture → 403/404, super → 200 — on identical requests.
  - Bypass unchanged: an org_admin / support_staff fixture WITHOUT
    approve_* is refused on approving a proposal, an instruction, a model
    and a policy; an investment_staff fixture behaves as before.
  - Menu node tests: org_admin and support_staff see the staff screens 1c
    lists; a member sees none; super unchanged.
  - Unknown role → treated as not internal.
  - Org isolation both directions; own routes via _verify_self;
    authz1_routes.py --check CURRENT.
  - Regression: verify_bugfix2.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 6: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (the two concepts, the role table, what org admin and
support staff can now see and still cannot approve); add verify_staffroles1.py
to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_staffroles1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count. Fixture org and users is_test =
true (assert it): one fixture user per role tested (super, investment_staff,
org_admin, support_staff, advisor, member). Support --self-checks-only and
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
teardown, 2700 s; with VERIFY_NESTED=1 skip it with one '[SKIP] nested run'.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates (users.last_login_at), and name them.
  - A failing check's detail prints the response body.
  - Never touch real users or roles: count + checksum before and after.

Assertions:
  [Y] Task 1 table (see vs bypass for every use)
  [Y] is_internal / require_internal; is_staff (bypass) unchanged
  [Y] Every staff-only route: org_admin / support_staff / member / super
      both ways per 1b
  [Y] Approvals still refused for internal roles without the permission
  [Y] Menu per role; unknown role not internal
  [Y] Org isolation; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_bugfix2.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
