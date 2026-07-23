SOC PHASE 3 — Households: flexible rollup groups + strict
primary household. 3 tasks + verification. Phase 3 of 6.

CONTEXT: households, household_memberships, and
entities.primary_household_id already exist (Part 1 SQL
applied). household_memberships is MANY-TO-MANY (an entity can
be in multiple households). primary_household_id is a single
nullable FK — AT MOST ONE per entity, used for non-overlapping
billing/net-worth calculations.

CONFIRMED DESIGN DECISION (do not deviate): household
membership does NOT automatically grant staff visibility. A
household created purely for a reporting rollup must not
silently expose its members to whoever can see that household —
visibility into a household's entities remains its own
SEPARATE, explicit grant (which may apply in bulk to everything
currently in the household at grant time, but is never automatic
just from membership existing).

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: Household CRUD + membership management ===
Build a minimal service/endpoints:
  - Create/rename/delete a household
  - Add/remove an entity to/from a household (many-to-many)
  - Set/clear an entity's primary_household_id (single-value)
  - List all households an entity belongs to, and separately,
    its primary household specifically (these are different
    queries — do not conflate)

=== TASK 2: Rollup calculation using existing patterns ===
Build a rollup function that, given a household_id, aggregates
across ALL member entities (via household_memberships, the
many-to-many table) — reuse the SAME aggregation approach
already used for the S23 investment-level roll-up
(spv_rollup.py) rather than inventing new aggregation logic:
sum whatever the relevant financial figures are across the
household's member entities (e.g. total committed/called/
distributed if applicable, or total holdings value via
resolve_entity_set/entity_holdings if that's the more relevant
aggregate — check what makes sense given what entities actually
track). Confirm Decimal precision throughout, no float drift.

=== TASK 3: Net-worth/billing view using the STRICT primary
household ===
Build a SEPARATE view/query specifically for aggregate net-worth
or billing purposes, grouped by primary_household_id ONLY (never
by the flexible many-to-many memberships) — this is what
guarantees no double-counting, since an entity has at most one
primary household. Make the distinction between Task 2's
flexible rollup and Task 3's strict primary-household aggregate
clearly separate in code (different function names, clear
docstrings on why they differ) so a future developer doesn't
accidentally use the wrong one for a billing calculation.

=== VERIFICATION ===
Write verify_soc3.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] households / household_memberships / entities.
      primary_household_id exist matching the snapshot
  [Y] An entity can belong to MULTIPLE households simultaneously
      (many-to-many proven)
  [Y] An entity has AT MOST ONE primary_household_id (attempting
      a second primary assignment REPLACES, doesn't duplicate —
      confirm this is a simple single-column update, not
      creating a conflicting second record)
  [Y] Task 2's flexible rollup correctly sums across ALL
      household_memberships for a household with 2+ member
      entities, Decimal-exact
  [Y] Task 3's strict primary-household aggregate does NOT
      double-count an entity that belongs to multiple flexible
      households but has only ONE primary — confirm by creating
      an entity in 2 flexible households + 1 primary, and
      showing the primary-based aggregate counts it exactly once
  [Y] Creating a household and adding entities to it does NOT
      change any staff member's result from
      get_staff_visible_entity_ids (from Phase 2) — household
      membership alone grants nothing
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass.
