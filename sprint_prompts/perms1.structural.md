PERMS1 — one source of truth for a user's permissions, plus the HollisMuse
re-run findings. The platform has TWO permission systems that disagree: the
authz Principal reads only ROLE permissions, while the Profiles / Permission
Sets model (SOC/RBAC phases) holds other grants that the Principal ignores —
so the org admin's workflow permissions (granted on the "Org Admin"
profile) never take effect, and the org_admin role lacks basic read
permissions the support-staff and member roles have. Unify them safely, then
fix the remaining defects, and build Joe's decisions on deal and household
creation. 9 tasks + verification.

Context: HollisMuse re-ran plan v3 on 2026-11-04 01:35–04:00 ET (report in
the project as the re-run final report). 11 prior findings FIXED, including
all three member-privacy Criticals. NEW High HELD-10: org admin is denied
view_portfolio on Positions / Commitments while the member holds it.
Still-open High WF-02 / Medium NAV-10: org admin 2 refused on every workflow
route. Others below.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB 2026-11-04 + repo; Task 1
confirms):
- Role permissions (role_permissions → permissions.name), org 2nd Act:
  org_admin (27): approve_/manage_/view_ for authority, blocks, execution,
  held_away, models, proposals, replication, tax; manage_hypothetical_policy,
  manage_org_settings, review_agent_proposals — NO view_portfolio, NO
  view_members, NO view_workflow_runs / author_workflows /
  configure_workflow_triggers, NO view_dashboard, NO view_deals.
  support_staff (8): review_agent_proposals, view_community, view_compliance,
  view_dashboard, view_deals, view_members, view_portfolio,
  view_workflow_runs. member (10): indicate_interest, submit_deal,
  view_community, view_dashboard, view_deals, view_insurance,
  view_marketplace, view_portfolio, view_spv, vote_deal.
- Profile permissions (profile_permissions.permission_key):
  "Org Admin": author_workflows, configure_workflow_triggers,
  view_workflow_runs. "CSA / Ops": manage_documents, staff, view_dashboard,
  view_deals, view_marketplace, view_members, view_portfolio,
  view_workflow_runs. "Adviser": manage_deals, manage_members,
  manage_portfolio, score_deal, staff, view_dashboard, view_deals,
  view_marketplace, view_members, view_portfolio, view_workflow_runs,
  vote_deal. "Member": member keys. Note the profile key "staff".
- services/authz.py: the Principal is built in ONE statement
  (PRINCIPAL_COLUMNS, previewperf fix 1) from user_roles → role_permissions
  only; is_staff = super OR STAFF_ROLE_NAMES (investment_staff, admin,
  super_admin, owner) — the permission BYPASS; is_internal (staffroles1) =
  any internal role — "may see staff data"; bugfix2: a staff-only
  permission set held by a member grants nothing.
- services/profiles.py: get_profile_permissions(pool, profile_id) and an
  effective-permissions function (~:75) that merges profile + permission-set
  keys — used by some code paths, not by the Principal (Task 1: which).
- Test accounts: jpl99172 and jpl991723 = org_admin role + "Org Admin"
  profile; jpl991722 = support_staff role + "CSA / Ops" profile; jpl991721 =
  member role + "Member" profile, no permission sets.
- Other open findings (IDs from the Muse report): WF-06 Delete buttons now on
  EVERY workflow row, including the system workflow
  "system:human_task:approve_models v1", with no confirmation visible from
  the library view. WF-03 the ownership hierarchy graph still shows only the
  root node in both look-through states (owners Hargrove Family Trust 60%,
  James Hargrove 40% missing). REP-10 re-uploading a model CSV is blocked as
  a duplicate because the Nov 3 upload sits "In progress" with no linked
  model (the model was removed by bugfix2's clean-up) — five "Duplicate" log
  entries were created 2026-11-04 07:23 UTC. WF-04 no fee screens reachable:
  no Fees menu item; /admin/fees 404 (fee pages exist: /fee-schedules,
  /admin/billing, /admin/billing-groups — Task 1 finds what an org admin
  should reach). WF-05 the workflow generator sometimes emits invalid BPMN
  for vague descriptions, then validation rejects it.
- Test rows to remove: workflow "MUSE – workflow" v1
  c76c0067-dc07-415b-93fb-ad55e81357b5 (draft, no runs); the five
  "Duplicate" MUSE-model.csv upload-log entries of 2026-11-04 07:23 UTC and
  the orphaned Nov 3 "In progress" MUSE-model.csv upload (the log says
  "Nothing is ever removed": if an immutability trigger protects it, use the
  documented privileged path only for these test rows, by id, or mark them
  test and report — never weaken the trigger).
- Joe's product decisions (2026-11-04) — BUILD 1 and 2, RECORD 3 and 4:
  (1) DEAL-01: org admins CAN create deals (today super admin only).
  (2) REP-01: households are created by internal staff holding
      manage_members (org admin gets it) from Portfolio → Households
      ("New household": name, members linked to CRM entities); accounts are
      added to a household manually (custodian, masked number, authority /
      custody) or by the existing custody import. No household creation for
      members.
  (3) NAV-03/06/07: org admins DO see price sources and the hypothetical-
      performance policy — keep as is (record as decided).
  (4) PRIV-06: team-based staff visibility (services/staff_visibility.py —
      staff_assignments by user or team, team_members, users.manager_id
      hierarchy, SOC Phase 2) is built but NOT enforced; enforcing it is the
      NEXT sprint (visibility1) — do not change visibility here, but do not
      add any new org-wide staff read that would bypass it later (route new
      reads through one helper Task 1 names).
- Regression: this changes how the Principal's permissions are built →
  re-run verify_staffroles1.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; never start a script in the background; write the verify but DO
NOT RUN IT; no network; never print a secret. The permission BYPASS set
(STAFF_ROLE_NAMES / is_staff) must not grow — no profile key, including
"staff", may grant the bypass or is_staff. A client role (member, next_gen)
never gains a staff-only permission from a profile or set (bugfix2 rule).
Keep the Principal one statement per request (previewperf fix 1). Do not
change real users' roles or profiles in data. Fail closed. Light/cream UI.
Commit ALL sprint files (excluding sprint_prompts/logs) on the current branch
at the end with message "perms1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Every place permissions are computed or checked: the Principal,
      services/profiles.py's effective permissions, menu visibility
      (/users/me), any route reading profile_permissions directly — which
      source each uses. Table.
  1b. What the profile key "staff" does today, anywhere.
  1c. Which permissions the workflow routes, Positions / Commitments,
      CRM reads (staffroles1) and fee pages require.
  1d. Fee screens: which pages exist, their gates, their menu entries, and
      why an org admin sees none.
  1e. WF-03: why the hierarchy graph omits owners (data path vs render).
  1f. REP-10: the upload's lifecycle states and why one stays "In progress"
      when its model is removed; the duplicate check's key.

=== TASK 2: ONE SOURCE OF PERMISSIONS ===
The Principal's permissions = role permissions ∪ the user's profile
permissions ∪ their permission sets' permissions, computed in the same
single statement; then the bugfix2 rule removes staff-only keys for client
roles; the key "staff" is ignored for is_staff (and maps to nothing, or to
is_internal only if 1b shows that was its meaning — say which). Every 1a
caller that computes permissions separately uses the Principal instead (or
the same function), so there is one answer everywhere.

=== TASK 3: ORG ADMIN BASELINE ===
Grant the org_admin role the read permissions its job needs and the
support-staff role already has (view_portfolio, view_members,
view_dashboard, view_deals, view_workflow_runs) and the workflow
permissions on its profile (author_workflows, configure_workflow_triggers),
in rbac.ensure_org_admin_role and a migration for existing org_admin roles.
Also grant manage_deals (deal creation, decision 1) and manage_members
(households, decision 2) to org_admin. No new approve_* grants. List the
before / after.

=== TASK 3b: DEAL AND HOUSEHOLD CREATION (decisions 1 and 2) ===
Deals: the existing create-deal flow is reachable by an org admin holding
manage_deals (Marketplace "New deal"), with the same draft → publish rules
and maker-checker the super-admin flow has; members never see drafts
(bugfix2). Households: Portfolio → Households "New household" for internal
roles with manage_members — name, link member entities from CRM; on the
household, "Add account" (custodian, masked account number + hash as rep2,
authority / custody per rep7b) — the existing custody import keeps working.
Both write audit rows; both refused for members and for internal roles
without the permission.

=== TASK 4: DEFECTS ===
WF-06: system workflows have no Delete; Delete only on a draft with no runs,
behind a confirmation naming the workflow; the API refuses deleting a
system workflow or one with runs. WF-03: the graph shows owners and edges
matching the detail page in both look-through states. REP-10: a removed
model's upload is closed (not "In progress"); re-uploading the same bytes is
allowed when no live model references the earlier upload (the duplicate
guard still blocks a true duplicate). WF-04: org admins reach the fee
screens their permissions allow, from a Fees (or Billing) menu entry; a
missing page returns the app's not-found page, not a 404 dead end from a
menu link. WF-05: the generator retries once with the validator's error,
then shows the reason instead of saving invalid BPMN.

=== TASK 5: REMOVE THE TEST ROWS === exactly those in the facts, by id,
printed first; zero remain; nothing else touched (count + checksum).

=== TASK 6: REAL PROOF ===
  - Before the fix (fixture org): an org_admin fixture with the "Org Admin"
    profile is refused on a workflow route and on Positions; after: admitted
    — identical requests.
  - A member fixture holding a profile with staff-only keys and the key
    "staff" gains NO staff-only permission, is_staff = false, is_internal =
    false.
  - A support-staff fixture: is_staff false; admitted on its reads; refused
    on approvals.
  - The bypass set unchanged (code scan + an investment_staff fixture
    behaves as before).
  - One answer everywhere: for each fixture user, the Principal's
    permissions = the profiles service's effective permissions = /users/me.
  - Deal creation: an org_admin fixture with manage_deals creates a draft
    deal; a member and an internal role without manage_deals are refused;
    the draft is invisible to members.
  - Households: an org_admin fixture creates a household and adds an account
    (masked number stored with its hash, never the full number on screen);
    a member and an internal role without manage_members are refused.
  - Each Task 4 fix proven (system workflow Delete refused by the API; draft
    delete with confirmation; graph nodes; re-upload allowed vs true
    duplicate blocked; fee menu for org admin; generator reason).
  - Principal still one statement per request (statement count).
  - Test rows removed; real rows unchanged.
  - Org isolation both directions; own routes via _verify_self;
    authz1_routes.py --check CURRENT.
  - Regression: verify_staffroles1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 7: PRODUCT DECISIONS === record Joe's four decisions in
PROJECT_STATUS: (1) and (2) built here, (3) kept as is, (4) next sprint
visibility1.

=== TASK 8: UPDATE PROJECT STATUS === docs/PROJECT_STATUS.md (one source of
permissions, the org_admin baseline before/after, the fixes); add
verify_perms1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_perms1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture profiles
and permission sets included); re-read every touched table for its exact
pre-test count. Fixture org and users is_test = true (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run'.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` (and any list / dict value) in bool(...).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns real
    activity updates (users.last_login_at), and name them.
  - A failing check's detail prints the response body.
  - Never touch real users, roles, profiles or permission sets except the
    Task 3 migration's grants to org_admin roles: count + checksum before and
    after everything else.

Assertions:
  [Y] Task 1 table and findings 1a–1f
  [Y] One source of permissions; one answer everywhere
  [Y] Client roles gain no staff-only keys; "staff" key grants no bypass
  [Y] Org admin baseline: workflow routes and Positions admitted; no new
      approvals
  [Y] Bypass set unchanged; Principal one statement
  [Y] Deal creation and household / account creation per decisions 1–2
  [Y] WF-06, WF-03, REP-10, WF-04, WF-05 fixes
  [Y] Test rows removed; real rows unchanged
  [Y] Org isolation; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_staffroles1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
