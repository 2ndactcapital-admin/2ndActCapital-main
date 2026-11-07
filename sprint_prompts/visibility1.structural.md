VISIBILITY1 — team-based staff visibility, enforced: which staff see which
families. Today every internal user sees every family; the resolver exists
but almost nothing calls it. Joe's rules (2026-11-04): ORG ADMINS SEE EVERY
FAMILY IN THEIR ORG (like a super admin, scoped to the org); a family that
NO ONE is assigned to is visible ONLY to super admin, org admins and the
record's OWNER / CREATOR; every other staff member sees the families
assigned to them, to a team they are on, or to anyone who reports to them.
Plus the assignment screens, a "what will each person see" preview, and an
org switch so enforcement is turned on deliberately. 9 tasks + verification.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo + live DB 2026-11-04; Task 1
confirms signatures):
- services/staff_visibility.py: get_staff_visible_entity_ids(pool, user_id,
  org_id) = direct staff_assignments ∪ team assignments ∪ the assignments of
  everyone who reports to the user (users.manager_id, cycle-safe walk via
  get_report_user_ids); get_team_ids_for_users; get_all_org_entity_ids.
  super_admin bypass via load_principal / is_super_admin; org_admin is
  DELIBERATELY excluded today (docstring) — this sprint reverses that per
  Joe. Returns an EMPTY set for a user with no assignments (no org-wide
  default).
- Callers today: services/assistant_actions/queries.py, document_embedding.py
  (~:829), ownership_tree.py (~:261), restricted_access.py (staff side);
  portfolio_positions.py mentions it as "a separate feature" (not applied).
  verify_staffvisbypass.py, verify_ownershiptreea.py, verify_soc2/3/4/6,
  verify_rbacbypass, verify_assistantquery, verify_portfoliog reference it.
- Tables: staff_assignments (id, org_id, entity_id, assigned_to_user_id,
  assigned_to_team_id, role_label, assigned_at, assigned_by) — 2 rows;
  teams (id, org_id, name, description, created_at) — 1 row; team_members
  (team_id, user_id, added_at, added_by) — 1 row. entities has created_by and
  relationship_manager_id; households (created_by), household_memberships,
  accounts (household_id, primary_entity_id, created_by, advisor_of_record_id).
- authz: STAFF_ROLE_NAMES / is_staff = permission BYPASS (must not grow);
  is_internal / require_internal (staffroles1) = "may see staff data";
  bugfix2 scoped members to their own family (unchanged by this sprint);
  perms1: the Principal is ONE statement per request — the visible set must
  not add a query per row (compute once per request, reuse).
- crmdetail1 (merged first) added governed relationship types, liabilities
  and demo-data protection; the Olivo demo family (tag demo-olivo, household
  "Olivo Family") may exist in production — treat it as real data.
- Regression: this changes what every internal staff-data read returns →
  re-run verify_staffroles1.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Fail closed: a read whose
visibility cannot be decided is refused. A refused direct URL returns the
same not-found response as a missing record (never reveal that a family
exists). Do not change real users' roles, teams or assignments in data, and
do NOT turn enforcement on for any real org — the operator does that after
reviewing the preview. Light/cream UI with the existing tokens. Commit ALL
sprint files (excluding sprint_prompts/logs) on the current branch at the end
with message "visibility1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The unit of visibility: what "a family" is in the data (household,
      its member entities, the legal entities they own through the
      ownership graph, their accounts, positions, documents, notes,
      liabilities). Propose the closure rule: assignment to any one of them
      grants the whole family. Say how an entity in two households is
      handled.
  1b. "Owner / creator": which column(s) mean it today (entities.created_by,
      relationship_manager_id, households.created_by, accounts) and the rule
      for a family (any member record's owner? the household creator?).
  1c. Every internal staff-data READ route (bugfix2's 73-route
      classification + staffroles1's require_internal routes + anything
      later: replication, held-away, X-ray, instructions, blocks, gifts,
      tax, documents / Chancery, notes, compliance tab, liabilities,
      households, CRM list / search / detail / graph, reports and exports,
      the assistant and agents, notifications / todos that name a family).
      Table: route → family-scoped? → how it will filter.
  1d. Writes: which staff writes must also require visibility (editing a
      family you cannot see must be refused).
  1e. Existing UI for teams, team membership, managers and assignments.
  1f. Where to compute the visible set once per request (Principal-side
      cache vs request state) without breaking previewperf's statement
      budget.

=== TASK 2: THE RULE ===
visible_family_ids(principal, org): super admin → all; org admin of that
org → all in the org; member → own family (bugfix2, unchanged); other
internal staff → assigned (user ∪ team ∪ reports, transitively) ∪ families
they own / created that have NO assignment. (Joe: unassigned → super admin,
org admin, owner / creator only.) Decide and STATE whether an owner keeps
visibility after the family is assigned to someone else — default: yes if
they are the relationship manager, no if they were only the creator; record
it in PROJECT_STATUS as a decision for Joe. The family closure from 1a.
Org switch org_settings 'staff_visibility.enforced' (default false): when
false, behaviour is exactly today's (every internal user sees all) and the
preview still works.

=== TASK 3: ENFORCEMENT ===
Apply the rule at every 1c read and 1d write, in one shared helper (no
per-route reimplementation): lists and search filter; detail / child routes
return not-found; counts, totals, dashboards and exports only include
visible families; the assistant, agents and document search use the same
set; notifications / todos about a family go only to people who can see it.
Existing callers (assistant queries, document embedding, ownership tree,
restricted_access) switch to the new rule.

=== TASK 4: ASSIGNMENT SCREENS ===
On a household / CRM record: "Team" panel — assigned users and teams with a
role label (e.g. lead adviser, associate, service), add / remove (audited),
owner shown. Admin → Teams: create teams, add / remove members, set a user's
manager. Admin → Unassigned families: list with owner, bulk-assign. Who may
assign: org admin, and staff with manage_members on families they can see.

=== TASK 5: PREVIEW AND SWITCH ===
Admin → Staff visibility: for each internal user, how many families they
would see with enforcement on, which ones, and why (assignment / team /
report / owner); a warning listing staff who would see none; the switch
(org admin only), audited, with the preview shown on the confirm step.

=== TASK 6: PERFORMANCE ===
The visible set is computed once per request; a list route adds one filter,
not a query per row. Report the statement count before / after on the CRM
list, households and one portfolio route.

=== TASK 7: DOCS ===
docs/STAFF_VISIBILITY.md: the rule, the closure, owner semantics, what is
enforced where, how to turn it on (assign first, check the preview, then
switch), and how the Olivo demo should be assigned for demos.

=== TASK 8: REAL PROOF ===
Fixture org with: super admin, org admin, two support staff (S1 on team T,
S2 not), a manager M with S1 reporting to M, and three families: F1 assigned
to S1, F2 assigned to team T, F3 unassigned and created by S2.
  - Switch OFF: every internal user sees F1–F3 (today's behaviour, identical
    responses to before the sprint).
  - Switch ON: super admin and org admin see F1–F3; S1 sees F1, F2; M sees
    F1, F2 (via S1); S2 sees F3 only (owner of unassigned); after F3 is
    assigned to S1, S2's access follows the stated owner rule.
  - Family closure: S1 sees F1's legal entities, accounts, positions,
    documents, notes and liabilities; S2 gets not-found on each, identical
    to a missing id (same status and body shape).
  - Writes refused on an invisible family (note, address, account, proposal).
  - Lists, search, counts, exports, assistant answers and document search
    exclude invisible families (seed a distinctive name and prove it never
    appears).
  - A member still sees only their own family; org isolation both directions
    (rolbypassrls = false).
  - Assignment screens: add / remove audited; only permitted roles can
    assign (identical request refused / admitted).
  - Preview matches what each fixture user actually sees after switching on.
  - Statement counts (Task 6) within budget; Principal still one statement.
  - Real orgs: enforcement still OFF; real assignments unchanged (count +
    checksum).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_staffroles1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (the rule, the owner decision, the switch is OFF,
operator steps: create teams, assign families incl. the Olivo demo, review
the preview, switch on); add verify_visibility1.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_visibility1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture org,
users, teams, team_members, staff_assignments, entities, households,
accounts, documents, notes, liabilities, org_settings rows); re-read every
touched table for its exact pre-test count. Fixture orgs and users is_test =
true (assert it). Read UNIQUE / CHECK / FK constraints from the catalog
before seeding. Reserved fixture prefixes that match no real row (assert it).
Support --self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` (and any list / dict value) in bool(...).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates (users.last_login_at), and name them.
  - Time the server, not the operator's network (docs/VERIFY_RULES.md).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1f and the route table
  [Y] The rule: super / org admin all; assigned ∪ team ∪ reports; unassigned
      → owner only; owner decision stated
  [Y] Switch off = today's behaviour exactly; switch on = the rule
  [Y] Family closure; not-found indistinguishable from missing
  [Y] Writes refused on invisible families
  [Y] Lists, search, counts, exports, assistant, documents filtered
  [Y] Assignment screens, preview, switch (org admin only, audited)
  [Y] Statement budget; members and org isolation unchanged
  [Y] Real orgs still OFF; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_staffroles1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
