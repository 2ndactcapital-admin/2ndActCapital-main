ORG_ADMIN ROLE RECONCILIATION. 7 tasks + verification. Makes
org_admin a real role in the RBAC permissions system, migrates
every existing holder without losing access, and switches alert
routing and org-admin page access to resolve by PERMISSION rather
than by a free-text string on the user row.

THE CENTRAL RISK, STATED UP FRONT: org_admin is the access gate
for org-level admin pages. If this migration misses even one
user, that person silently loses admin access to their own org.
Every step must be reversible and every existing holder must be
proven migrated BEFORE anything starts reading the new path.

CONFIRMED REAL FACTS FROM PRIOR DISCOVERY, DO NOT RE-DERIVE:
- There are currently THREE different "org admin" concepts with
  ZERO mapping between them:
  (1) users.role = 'org_admin' — free text, NO CHECK constraint,
      NO FK. This is what create_held_run_alerts actually fans
      out to today, and what gates org-level admin pages.
  (2) The `roles` table — the real RBAC axis. 'org_admin' is NOT
      in it.
  (3) An "Org Admin" profile on the SOC/profiles axis, created by
      the workflowpermsfix sprint.
- DECIDED, DO NOT RE-LITIGATE: org_admin belongs on the ROLE axis,
  not profiles. Roles model AUTHORITY (who is accountable for this
  org); profiles model JOB FUNCTION (Adviser, CSA/Ops,
  Compliance) and are already load-bearing for UDF field-level
  security. Adding an authority concept to the profile axis would
  muddle both.
- DECIDED: alerting and page access must resolve by PERMISSION,
  not by role NAME. "Who holds this permission in this org?" — so
  an org can later grant alert visibility or a subset of admin
  access to someone who is not a full admin, without touching the
  alerting or routing code.
- DECIDED: users.role stays READABLE until nothing depends on it.
  A hard cutover on a column this widely read is the riskier path.
- create_held_run_alerts' real recipient rule today: the run's
  started_by user, plus every users.role='org_admin' in that org,
  deduped via SELECT-then-upsert on (user_id, org_id, source,
  related_type, related_id).
- member_todos.user_id is NOT NULL with an FK to users. An alert
  for an org with zero resolved recipients currently writes
  NOTHING and fails silently. The Hollisworks tenant's own org
  (bb347258-8f28-4f49-8cc9-e29ccad82884) has ZERO org_admin rows
  right now — this is a real, live instance of that bug.
- has_permission DEFAULT-ALLOWS a user with zero role rows. Any
  test fixture must be given real, deployed roles or the test
  proves nothing.
- RLS is now genuinely enforced (app_service, rolbypassrls=false).
  Direct table access needs set_rls_context or you get SILENT
  denials — zero rows, no error.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO
background-process notification mechanism in this tool — nothing
will ever notify you that a script finished. Never wait for one.
Run every script SYNCHRONOUSLY in the foreground and read its
output directly. org_id is NEVER read from a request body or path
parameter — only from the caller's verified session context.

=== TASK 1: DISCOVER — the real blast radius ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Every real user row with role='org_admin' today: count,
      and which orgs they belong to. Also report every OTHER
      distinct value in users.role, with counts — the migration
      must not assume org_admin is the only value that matters.
  1b. Every place in deployed code that reads users.role — every
      router, service, middleware, and frontend file. For each,
      report whether it checks for 'org_admin' specifically or
      branches on other values too. This is the list that must
      eventually migrate; report it completely.
  1c. The real current `roles` table contents and the real
      `role_permissions` grants, plus the real `permissions`
      catalog. Report whether a permission suitable for
      "org-level admin access" already exists (e.g.
      manage_org_settings) or whether one must be created.
  1d. Confirm which real endpoints/pages are gated on org-admin
      status today, and HOW they check it — the same
      users.role string, a permission, or something else.
      Report any inconsistency between them.
  1e. Report whether an org exists today with users but ZERO
      org_admin — the silent-no-recipient case. Name them.

=== TASK 2: CREATE THE ROLE AND ITS PERMISSION ===
Add org_admin as a real row in `roles`. Create or reuse a real
permission for org-level admin access per Task 1c's finding —
prefer reusing an existing, suitable permission over inventing a
new one; if creating one, follow the established naming
convention exactly. Grant it to the org_admin role on the
role_permissions axis. Do NOT touch the profiles axis.

=== TASK 3: MIGRATE EVERY EXISTING HOLDER — ADDITIVE, REVERSIBLE
===
For every user found in Task 1a with role='org_admin', create the
corresponding real user_roles grant. This is PURELY ADDITIVE —
users.role is NOT cleared, NOT modified, and remains fully
readable. Nothing switches to reading the new path in this task.
Prove every single holder migrated: the count of new user_roles
grants must exactly equal the count from Task 1a, with no user
missed and no user granted who should not have been.

=== TASK 4: SWITCH THE READERS — PERMISSION-BASED ===
Only after Task 3 is proven complete: change alert recipient
resolution and org-admin page access to resolve by PERMISSION
(via the real rbac.has_permission path), not by the users.role
string. Follow the real existing permission-check pattern used
elsewhere in this codebase — do not invent a second mechanism.
Every previously-working access path must still work for every
previously-authorized user.

=== TASK 5: FIX THE SILENT-NO-RECIPIENT BUG ===
An alert whose recipient set resolves to empty currently writes
nothing and fails silently. Make this fail LOUDLY and visibly
instead — the specific mechanism is yours to choose, but it must
leave a real, findable record that an alert could not be
delivered and why. Prove it with the real Hollisworks-tenant org
case from Task 1e.

=== TASK 6: REAL PROOF ===
  - Report Task 1's five findings explicitly.
  - Every user who had role='org_admin' before this sprint has a
    real user_roles grant afterward — count-for-count, with the
    specific user ids listed.
  - NOBODY gained org-admin access who did not have it before —
    prove the reverse direction too, not just the forward one.
  - A real org_admin can still reach every org-level admin page
    and endpoint they could reach before, proven through the real
    ASGI app, not by inspecting grants.
  - A real non-admin in the same org is still refused those same
    pages — proven on the IDENTICAL request, and with a fixture
    user holding REAL deployed roles (not zero roles, which
    default-allows).
  - Alert recipient resolution by permission returns the SAME set
    as the old users.role query did, for an org that has admins —
    proven set-to-set, not by count alone.
  - An org with zero resolved recipients now fails loudly and
    leaves a findable record — proven with the real Hollisworks
    tenant org.
  - Cross-org: an org_admin of org A is NOT a recipient for org
    B's alerts, and cannot reach org B's admin pages.
  - users.role is still readable and still populated — this
    sprint did not clear it.

=== TASK 7: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and CLAUDE.md: org_admin is now a
real role resolved by permission. Record explicitly that
users.role is still READ by the call sites Task 1b found, that
those remain to be migrated, and that the column must not be
dropped until they are. List them by name so the follow-up work
is scoped, not vague.

=== VERIFICATION: apps/api/scripts/verify_orgadminrole.py ===
Pass/fail only. MUST hydrate its own secrets from Doppler over
HTTPS at startup (the verify_rlscutover.py / verify_tamodel1.py
pattern — run_sprint.sh's Step 3 does NOT use doppler run --).

Assertions:
  [Y] Report Task 1's five findings explicitly
  [Y] Every prior org_admin has a real user_roles grant —
      count-for-count, ids listed
  [Y] Nobody gained access who did not have it before
  [Y] A real org_admin reaches every previously-reachable admin
      page, proven through the real app
  [Y] A real non-admin with REAL deployed roles is still refused,
      identical request
  [Y] Permission-based recipient resolution matches the old
      users.role set exactly, set-to-set
  [Y] An org with zero recipients fails loudly with a findable
      record
  [Y] Cross-org isolation on both alerts and admin page access
  [Y] users.role still readable and populated
  [Y] Teardown: zero leftover rows
