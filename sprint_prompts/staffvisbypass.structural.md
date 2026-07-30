STAFF VISIBILITY — SUPER ADMIN BYPASS FIX. 2 tasks +
verification. A confirmed, real gap found tonight: every RLS
policy, restricted_access, and trading_authority check in this
platform includes an explicit is_super_admin escape hatch —
staff_visibility.get_staff_visible_entity_ids does not. A
confirmed Super Admin was blocked from seeing an entity via the
Ownership Tree Graph's staff route purely because
staff_assignments had no row for them.

STANDING RULES: org_id never from request body; no interactive
prompts.

=== TASK 1: Discover, don't assume ===
Read the REAL, current get_staff_visible_entity_ids (services/
staff_visibility.py) — its exact signature, how (or whether) it
currently has any access to the calling user's role, and every
call site that uses it (Ownership Tree Graph's staff route is
one; check whether anything else calls it too). Report all of
this before changing anything.

=== TASK 2: Add the bypass, matching platform convention exactly
===
Based on Task 1's findings, add an is_super_admin bypass to
get_staff_visible_entity_ids: if the calling user's role is
super_admin, return the FULL set of entity IDs in the given org
(bypassing the hierarchy/team/assignment filter entirely) — same
semantic as every other is_super_admin escape hatch in this
platform. Org_admin is explicitly NOT included in this bypass —
scope the fix to exactly the stated gap, do not expand it.
Update every call site found in Task 1 to pass whatever the
function now needs to determine this (a role/principal, not just
user_id, if that's not already available) — do not leave any
caller silently un-upgraded.

=== VERIFICATION ===
Write verify_staffvisbypass.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's findings explicitly (function signature,
      all call sites found)
  [Y] A Super Admin with ZERO staff_assignments rows for a given
      entity STILL sees it via get_staff_visible_entity_ids
      (the actual bug just found, now fixed)
  [Y] A regular staff user (non-super-admin) with ZERO
      staff_assignments rows for that SAME entity does NOT see it
      — confirms the fix is scoped correctly, not accidentally
      opening visibility for everyone
  [Y] Org Admin (not Super Admin) with zero staff_assignments
      also does NOT see it — confirms org_admin was correctly
      excluded from this bypass, not silently included
  [Y] The Ownership Tree Graph's staff route specifically now
      works correctly for a Super Admin on a previously-blocked
      entity (re-run the exact scenario found tonight)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass.
