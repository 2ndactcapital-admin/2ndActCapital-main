ORG_ADMIN WRITE PATHS — CLOSE THE INVITE GAP. 5 tasks +
verification. Small, contained follow-up to orgadminrole.structural
(35/35, merged). That sprint migrated every EXISTING org_admin to a
real RBAC grant and switched all gates to permission-based
resolution. It deliberately did NOT intercept FUTURE writes — this
sprint does.

THE LIVE BREAK THIS FIXES: services/invites.py and
UserManagement.jsx still write users.role='org_admin' directly,
with no corresponding user_roles grant. The next org admin invited
or promoted through the UI will appear as an admin in the user list
and be REFUSED at every org-admin page and endpoint, because the
gates now resolve by permission, not by the role string. This is a
real, current defect introduced by the gate migration, not a latent
inconsistency.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- org_admin exists as a real row in `roles`
  (id fb2b9fa9-6189-4224-b365-322bdce758ef, org 2nd Act) and holds
  the manage_org_settings permission on role_permissions.
- can_manage_org_settings (services/rbac.py:209) is now
  `async def can_manage_org_settings(pool, user, org_id)` — all
  five production callers are migrated and pass `await` + pool.
  Any NEW caller must too. A missed `await` FAILS OPEN: `not
  <coroutine>` is always False, so the gate silently passes for
  everyone.
- users.role is still READ by the call sites orgadminrole's Task 1b
  enumerated, and must NOT be dropped or stopped being written
  until those are migrated. This sprint keeps writing it AND adds
  the grant — additive, not a cutover.
- has_permission DEFAULT-ALLOWS a user with zero role rows. Any
  test fixture must hold real, deployed roles or the test proves
  nothing.
- RLS is genuinely enforced (app_service, rolbypassrls=false).
  Direct table access needs set_rls_context or you get SILENT
  denials — zero rows, no error.
- Real current state: 2nd Act has 30 users, exactly 1 org_admin
  (jpl99172@gmail.com). Hollisworks has 2 users and ZERO
  org_admin — its alerts currently land in audit_log rather than
  reaching a person.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO
background-process notification mechanism in this tool — nothing
will ever notify you that a script finished. Never wait for one.
Run every script SYNCHRONOUSLY in the foreground and read its
output directly. org_id is NEVER read from a request body or path
parameter — only from the caller's verified session context.

=== TASK 1: DISCOVER — every write path, not just the two named ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Every place in deployed code that WRITES users.role — any
      value, not only 'org_admin'. Include invites.py,
      UserManagement.jsx, and anything else: enrollment, admin
      user edit, seeding, a migration script still in use. Report
      each with its real file and line.
  1b. For each write path found: does it currently create any
      user_roles grant at all? Report per path. Some may already
      do this correctly — do not assume all are broken.
  1c. Report the real, existing pattern this codebase uses to
      grant a role to a user (the exact function or query the
      Task 3 migration used, or whatever else already exists).
      Reuse it; do not write a second mechanism.
  1d. Confirm what happens TODAY if someone is invited as
      org_admin: walk the real code path and report exactly where
      they would be refused, so the fix can be proven against the
      real failure rather than a hypothetical one.

=== TASK 2: FIX THE WRITE PATHS ===
For every path found in Task 1a that sets a role WITHOUT the
corresponding grant: add the grant, using Task 1c's real existing
mechanism. Keep writing users.role as well — this is additive, per
CONFIRMED REAL FACTS. The role string and the grant must stay in
lockstep from now on.
Handle role CHANGES too, not just creation: if a user is promoted
to org_admin the grant must be added, and if they are demoted AWAY
from org_admin the grant must be REMOVED. A promotion path that
grants but never revokes is a privilege ratchet.

=== TASK 3: BACKFILL ANY DRIFT THAT ALREADY EXISTS ===
Check whether any user currently has a role string without its
matching grant, or a grant without the matching string, in EITHER
direction. Report the real count. If any exist, reconcile them —
and report exactly which users and which direction, rather than
silently fixing.

=== TASK 4: REAL PROOF ===
  - Report Task 1's four findings explicitly.
  - A user invited as org_admin through the REAL invite path ends
    up with BOTH users.role='org_admin' AND a real user_roles
    grant — proven by reading both back independently.
  - That same user can actually REACH a real org-admin endpoint
    through the real ASGI app — this is the assertion that proves
    the live break is closed, so prove it end-to-end, not by
    inspecting grants.
  - A user promoted to org_admin via the user-management path gets
    the grant.
  - A user DEMOTED away from org_admin LOSES the grant — proven,
    and proven they can no longer reach the endpoint.
  - A non-admin invited normally gets NO org_admin grant.
  - Cross-org: an org_admin invited into org A gets a grant scoped
    to org A only, and cannot reach org B's admin endpoints.
  - Zero drift remains after Task 3 — no role string without its
    grant, and no grant without its string, in either direction.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: the invite/promotion gap is closed.
Record explicitly which users.role READ paths still remain from
orgadminrole's Task 1b list, so the remaining migration stays
scoped and visible. Note whether the Hollisworks org still has
zero manage_org_settings holders.

=== VERIFICATION: apps/api/scripts/verify_orgadminwrites.py ===
Pass/fail only. MUST hydrate its own secrets from Doppler over
HTTPS at startup (the verify_rlscutover.py / verify_orgadminrole.py
pattern — run_sprint.sh's Step 3 does NOT use doppler run --).

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Invited org_admin has BOTH the role string and a real grant
  [Y] That user reaches a real org-admin endpoint through the real
      app
  [Y] Promotion adds the grant
  [Y] Demotion REMOVES the grant and access is genuinely lost
  [Y] A normal invite creates no org_admin grant
  [Y] Cross-org: grant is scoped to the inviting org only
  [Y] Zero string/grant drift in either direction
  [Y] Teardown: zero leftover rows
