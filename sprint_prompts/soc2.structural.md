SOC PHASE 2 — Staff visibility: hierarchy + teams + assignment,
one unified resolver. 3 tasks + verification. Phase 2 of 6 in
the larger SOC/RBAC design.

CRITICAL SAFETY CONSTRAINT: this sprint is ADDITIVE AND
STANDALONE ONLY. Do NOT modify any existing endpoint's
authorization/visibility behavior. Do NOT wire this resolver
into any current route as an enforcement gate. There is no
staging environment — this is production — and staff currently
have some existing (possibly org-wide, unrestricted) visibility
pattern that must NOT change as a side effect of this sprint.
Building and proving the resolver correct is the whole scope;
actually switching any endpoint to USE it for enforcement is a
deliberate, separate, later decision Joe will make explicitly.
If you find yourself modifying an existing router's query to
add a visibility filter, STOP — that is out of scope here.

CONTEXT: teams/team_members/staff_assignments tables and
users.manager_id already exist (Part 1 SQL applied).
staff_assignments.assigned_to_user_id XOR assigned_to_team_id
(exactly one set) — the same table serves both individual
per-relationship assignment and team-based assignment.

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: Discover current staff-visibility behavior ===
Before building anything: does ANY existing endpoint currently
restrict which staff can see which entity/deal/spv within an
org, or is visibility currently effectively org-wide for any
authenticated staff user (no restriction beyond org_id itself)?
Check a representative sample (entity list/search, deal list,
spv list endpoints). Report clearly: is today's real-world
behavior "any staff member in the org sees everything in the
org," or is there already some existing restriction mechanism
this phase needs to be aware of / not conflict with?

=== TASK 2: Build the unified staff-visibility resolver ===
Build a standalone function/service (e.g.
apps/api/services/staff_visibility.py):
get_staff_visible_entity_ids(user_id, org_id) -> set of entity
IDs visible to this staff user, combining:
  - Direct assignment: entities in staff_assignments where
    assigned_to_user_id = user_id
  - Team assignment: entities in staff_assignments where
    assigned_to_team_id is a team this user belongs to (via
    team_members)
  - Hierarchy: walk users.manager_id to find all users who
    report to this user (directly or transitively — handle
    cycles defensively, same discipline as the existing entity-
    graph cycle detection), then include THEIR direct + team
    assignments too
This function should be CALLABLE and TESTABLE but not called
from anywhere in the existing request-handling path yet (per
the safety constraint above — this is a standalone service for
now).

=== TASK 3: Minimal admin UI to create assignments ===
A simple screen (or extend an existing admin screen) for
Org Admin to: create a team, add/remove members, and assign a
user or team to an entity with a role_label. This just needs
to let the DATA get populated — it does not need to be
polished. No visibility ENFORCEMENT change, just the ability to
create the assignment records the resolver will read.

=== VERIFICATION ===
Write verify_soc2.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] teams / team_members / staff_assignments / users.manager_id
      exist matching the snapshot, CHECK constraint enforced
      (both or neither of assigned_to_user_id/team_id rejected)
  [Y] Direct assignment: a user assigned to an entity appears in
      get_staff_visible_entity_ids for that user
  [Y] Team assignment: a user who is a member of a team assigned
      to an entity sees it via the resolver
  [Y] Hierarchy: a manager sees an entity assigned to their
      direct report, AND to a report's report (transitive,
      2+ levels)
  [Y] A user with NO assignments, NOT on a relevant team, and
      NOT a manager of anyone with access returns an EMPTY set
      for that entity (confirms the resolver actually restricts,
      not just always-true)
  [Y] EXISTING endpoint behavior UNCHANGED — hit the same
      representative endpoint checked in Task 1 with a test
      user and confirm it behaves exactly as it did before this
      sprint (still not using the new resolver for enforcement)
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass. In your final summary, explicitly restate what Task 1
found about current real-world staff-visibility behavior, since
that finding matters for deciding HOW/WHEN to actually wire this
resolver into enforcement in a future sprint.
