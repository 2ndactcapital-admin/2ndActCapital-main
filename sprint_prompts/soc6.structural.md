SOC PHASE 6 (FINAL) — Member-side relationships: Trusted
Contact, Power of Attorney / Delegate, External Professional
Access. 3 tasks + verification. Phase 6 of 6 — the last SOC
phase before Joe's consolidated smoke test across all 6.

CONTEXT: trusted_contacts, delegate_grants, and
external_access_grants already exist (Part 1 SQL applied). These
are member-side relationships DISTINCT from ownership/beneficiary
(entity_relationships) and distinct from staff visibility
(Phases 2/4) — do not conflate any of these three with the
existing entity graph or staff-visibility mechanisms.

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: Trusted Contact — notify-only, no data access ===
Build minimal CRUD (create/list/remove a trusted contact for a
member entity). CRITICAL: a trusted contact must NEVER appear
in, or be checked by, any visibility-granting code path
(staff_visibility.py, resolve_entity_set, filter_restricted).
This is a "who to call," not a "who can see" relationship —
verify no visibility function references this table at all.

=== TASK 2: Power of Attorney / Delegate — scoped, time-bound,
audited AS delegated ===
Build:
  - Grant/revoke a delegate relationship, with scope (view_only
    vs transact) and OPTIONAL time-bounding (effective_from/
    effective_until) or springing (is_springing = true, meaning
    it is NOT active until activated_at is set by an explicit
    activation action — e.g. by Super Admin or Org Admin,
    confirming the springing condition like incapacity has been
    met)
  - A function is_active_delegate(delegate_grants row) -> bool
    that correctly handles: not yet effective, expired,
    springing-but-not-activated, springing-and-activated,
    revoked
  - CRITICAL: any action taken by a delegate must be logged
    distinctly AS a delegated action — capture BOTH the
    delegate_user_id (who actually acted) and the
    principal_entity_id (on whose behalf) — never silently
    attribute the action to the principal alone. Reuse the
    existing audit_log pattern but ensure the delegate
    relationship is explicit in what gets logged, not implicit.
  - A delegate with view_only scope granted VIEW access to the
    principal's entity (integrate with resolve_entity_set or a
    similar mechanism so a delegate can actually see what
    they're authorized to see) — but transact scope requires
    separate handling per whatever the codebase's real action-
    execution path looks like (discover before assuming — this
    may need to hook into the same assistant_activities/maker-
    checker mechanism from Phase 5 if the delegate is proposing
    an action on the principal's behalf)

=== TASK 3: External Professional Access — expiring, scoped,
non-persistent ===
Build:
  - Grant access to a specific entity (or specific document, if
    document_id is set) to an external email address, with a
    REQUIRED expiration (expires_at) and human-readable
    scope_description
  - A check function is_active_external_grant(...) that
    correctly excludes expired or revoked grants
  - This grant does NOT create a users row or persistent role —
    it is a standalone, checkable grant record only

=== VERIFICATION ===
Write verify_soc6.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] trusted_contacts / delegate_grants / external_access_grants
      exist matching the snapshot, scope CHECK constraint on
      delegate_grants rejects an invalid value
  [Y] A trusted contact does NOT appear in or affect
      resolve_entity_set / staff_visibility results for anyone
      (confirms notify-only, zero data-access leakage)
  [Y] is_active_delegate correctly returns True/False across ALL
      five states: not-yet-effective, expired, springing-not-
      activated, springing-and-activated, revoked
  [Y] A view_only delegate correctly gains visibility into the
      principal's entity via the integration point built in
      Task 2
  [Y] An action taken by a delegate is logged with BOTH the
      delegate's own user_id AND the principal entity_id
      explicitly captured — not attributed to the principal alone
  [Y] is_active_external_grant correctly excludes an expired
      grant and a revoked grant, but includes an active one
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass. In your final summary, note that this is the LAST of 6 SOC
phases — Joe will do one consolidated smoke test across all 6
rather than per-phase testing.
