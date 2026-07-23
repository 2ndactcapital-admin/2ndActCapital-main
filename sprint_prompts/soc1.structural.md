SOC PHASE 1 — Profiles, Permission Sets, Beneficiary Edges.
3 tasks + verification. This is Phase 1 of 6 in a larger SOC/
RBAC design (see 2nd_Act_SOC_Access_Control_Design.docx for
full context if present in the repo/docs — if not present,
proceed from this prompt alone). Do NOT build Phases 2-6
(staff visibility hierarchy/teams, households, restricted-
access, trading authority, member-side relationships) in this
sprint — later phases, separately scoped.

CONTEXT: profiles/permission_sets/user_permission_sets tables
and users.profile_id already exist (Part 1 SQL applied). Super
Admin and Org Admin remain handled via the EXISTING users.role
field + is_super_admin/is_org_admin helpers in services/rbac.py
— UNTOUCHED by this sprint. profile_id is a NEW, separate,
additive layer for everyone else (org-defined personas), not a
replacement for role.

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: Discover, don't guess ===
Before writing any permission-linking code:
  (a) Find the Sprint-11 action registry — grep for "action
      registry," "ACTION_REGISTRY," or similar across apps/api/
      services/ and apps/api/routers/. Report its ACTUAL shape
      (a DB table? a Python dict/constant? something else?) and
      the real format of an individual permission entry (e.g.
      a string key, an object with read/write + autonomy level).
  (b) Search the codebase and any docs/ files for the "seven
      platform personas" (RIA Client/Member, Community Member,
      Mesh End User, Adviser, CSA/Ops, Admin) — report whether
      these exist anywhere as actual code/data, or are purely a
      design-doc concept not yet implemented.
  (c) Given Mesh is confirmed out of scope (no-Mesh rescope),
      the "Mesh End User" persona is likely moot — confirm and
      report; do not seed it as a profile unless it genuinely
      still applies to something in the live app.
Report findings before proceeding to Task 2.

=== TASK 2: Build the permission-linking tables + seed profiles ===
Based on Task 1's findings:
  - Create profile_permissions and permission_set_permissions
    junction tables, with a permission-key column shaped to
    match the REAL action registry format discovered in Task 1
    (not a guessed shape).
  - Seed 2nd Act's org (00000000-0000-0000-0000-000000000001)
    with profiles for whichever platform personas genuinely
    apply (per Task 1c) — mark these is_seed = true.
  - Each seeded profile should be granted a sensible starting
    set of permissions from the real action registry, matching
    what that persona would plausibly need (e.g. an "Adviser"
    profile gets read/write on client-facing actions; "CSA/Ops"
    gets a narrower set) — use judgment, this is a reasonable
    starting bundle an Org Admin can edit later, not a precise
    final answer.
  - Build a minimal service (e.g. profiles.py) with functions to
    check whether a user (via their profile + any permission
    sets) has a given permission from the action registry.

=== TASK 3: Beneficiary relationship_type ===
entity_relationships.relationship_type is unconstrained free
text; ownership_pct is already nullable. No schema change needed.
  - Update wherever entity ownership relationships are created/
    edited (API + UI) to allow 'beneficiary' as a relationship_
    type choice, with ownership_pct left null/not required for
    that type.
  - Update resolve_entity_set (find its actual location first)
    to treat relationship_type = 'beneficiary' as conferring
    visibility the SAME WAY ownership does — a member with a
    beneficiary edge to an entity should see it via the same
    look-through mechanism as an owner, regardless of null
    ownership_pct.

=== VERIFICATION ===
Write verify_soc1.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] profiles / permission_sets / user_permission_sets /
      users.profile_id exist matching the snapshot
  [Y] profile_permissions / permission_set_permissions exist
      with columns matching the REAL action registry format
      discovered in Task 1
  [Y] 2nd Act's org has seed profiles for the personas confirmed
      still relevant (report which personas were seeded and why
      Mesh End User was or wasn't included)
  [Y] A test user assigned a profile correctly resolves whether
      they have a given permission (both a permission the
      profile grants, and one it doesn't)
  [Y] A test user's permission set correctly ADDS a capability
      beyond their profile's base grants
  [Y] Creating a 'beneficiary' relationship_type edge succeeds
      with null ownership_pct
  [Y] resolve_entity_set includes an entity reached via a
      beneficiary edge in a member's visible set, same as it
      would for an ownership edge
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass.

