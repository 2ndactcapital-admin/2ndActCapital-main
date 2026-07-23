SOC PHASE 4 — Restricted-access accounts. 3 tasks +
verification. Phase 4 of 6.

CONTEXT: entities.access_restricted (boolean flag) and
restricted_access_grants (explicit allow-list, entity_id +
user_id, UNIQUE per pair) and restricted_access_audit already
exist (Part 1 SQL applied). This phase builds on Phase 2's
staff_visibility.py resolver (get_staff_visible_entity_ids) and
the member-side resolve_entity_set — BOTH already exist and are
proven correct in isolation from Phases 2/3.

CRITICAL DESIGN REQUIREMENT (per the SOC spec, do not deviate):
the restricted-access check must be a SINGLE filter that wraps
BOTH the staff visibility engine (Phase 2) and the member
visibility engine (resolve_entity_set), applied BEFORE either
engine's normal resolution logic runs — NOT implemented
separately inside each. A restricted entity must be excluded
from search/list results the same way regardless of whether the
would-be-viewer is staff (who might otherwise see it via
hierarchy/team/assignment) or a member (who might otherwise see
it via ownership/beneficiary in the graph). Existence itself
must be hidden — a restricted entity should not appear in search
results or list views for anyone off the allow-list, not just be
blocked at a detail page.

SAME SAFETY CONSTRAINT AS PHASE 2: build this as an additive,
callable, TESTABLE filter/wrapper function. Do NOT wire it into
any existing endpoint's actual enforcement yet — that remains a
deliberate, later, separate decision (same reasoning as Phase 2:
no staging environment, production is the only environment).

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: The unified restriction filter ===
Build a single function (e.g. apps/api/services/
restricted_access.py):
  filter_restricted(entity_ids: set, user_id, org_id) -> set
Given a set of entity IDs that either visibility engine (staff
OR member) would otherwise return, this function removes any
entity where access_restricted = true UNLESS the given user_id
has an explicit row in restricted_access_grants for that
entity. This function is meant to be called as a final step
AFTER either engine produces its normal result set — a wrapper,
not a rewrite of either engine.

=== TASK 2: Restrict/unrestrict + allow-list management,
audited ===
Build minimal service functions (Super Admin only):
  - set_restricted(entity_id, restricted: bool, by_user_id) —
    flips the flag, writes a row to restricted_access_audit
  - grant_restricted_access(entity_id, user_id, by_user_id,
    reason) / revoke_restricted_access(...) — manages the
    allow-list, each change also writes to
    restricted_access_audit
Confirm only Super Admin can call these (reuse the existing
is_super_admin check from services/rbac.py).

=== TASK 3: Minimal admin UI ===
A simple screen or extension to an existing admin screen where
Super Admin can flag/unflag an entity as restricted and manage
its allow-list. Functional, not polished — this just needs to
let the data get populated so the filter has something real to
test against beyond raw SQL fixtures.

=== VERIFICATION ===
Write verify_soc4.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] entities.access_restricted / restricted_access_grants /
      restricted_access_audit exist matching the snapshot
  [Y] filter_restricted correctly REMOVES a restricted entity
      from a staff-visible set (simulate an entity Phase 2's
      resolver would normally include, flag it restricted,
      confirm filter_restricted excludes it for a user NOT on
      the allow-list)
  [Y] filter_restricted correctly REMOVES the SAME restricted
      entity from a member-visible set too (simulate via
      resolve_entity_set/ownership, same entity, confirm it's
      excluded for a member not on the allow-list) — proving
      ONE filter genuinely wraps BOTH engines, not two separate
      implementations
  [Y] A user WITH an explicit restricted_access_grants row DOES
      see the entity through filter_restricted, despite the
      restriction
  [Y] set_restricted / grant_restricted_access / revoke_
      restricted_access each write a row to
      restricted_access_audit with the correct action + actor
  [Y] A non-Super-Admin user calling set_restricted or grant/
      revoke is rejected
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass.
