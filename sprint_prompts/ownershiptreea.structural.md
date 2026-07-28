OWNERSHIP TREE GRAPH — SPRINT A (interactive only). 4 tasks +
verification. Sprint B (printable export) is separate and comes
later, deliberately not bundled — do not build export in this
sprint.

CONTEXT: built entirely on EXISTING data — entity_relationships
(bitemporal: valid_from/valid_to, system_from/system_to;
from_entity_id/to_entity_id; ownership_pct nullable;
relationship_type free text, now includes both 'ownership' and
'beneficiary') and resolve_entity_set (the existing look-through
resolver, already proven cycle-safe and multi-hop-correct across
both edge types this session). NO schema changes in this sprint.

CRITICAL FIRST STEP — DO NOT SKIP: Sprint 15 built
HierarchyBuilder.jsx (an SVG node-link tree with pan/zoom and
collapsible nodes) somewhere in this codebase. Find it, read it,
and report its ACTUAL current capabilities before writing
anything new. If it's a solid foundation, GENERALIZE/EXTEND it
(same discipline as the DataGrid work, which extended an
existing pattern rather than reinventing one). If it's
inadequate or doesn't exist where expected, report that clearly
and build fresh — but do not assume either way without looking.

Also find and reuse the EXISTING bitemporal date-picker/time-
travel UI component from Sprint 18's ownership-editing feature —
do not build a second, different date-picker paradigm for this
graph's time-travel control.

REQUIRED DESIGN DECISIONS (already confirmed, build to these
exactly, do not re-litigate):
  - BOTH ownership and beneficiary edges shown by default,
    visually distinct (e.g. a different line style/color),
    with a legend and a filter to isolate one type. Ownership
    edges show the % on the label; beneficiary edges show no
    percentage.
  - Restricted-access entities (from the SOC restricted_access_
    grants/access_restricted flag work) are FULLY HIDDEN — the
    node and its ENTIRE subtree must not appear at all for a
    viewer not on the allow-list. Do not merely mask/blur —
    remove entirely from the rendered tree and from whatever
    data the frontend receives (never send hidden-entity data
    to the client just to hide it client-side).
  - BOTH a staff-facing route and a member-facing route, built
    together, sharing one underlying rendering component:
      * Staff route: data flows through the staff visibility
        engine (hierarchy + teams + assignment) — find the
        actual function/service this uses today (built in the
        SOC work) and call it, do not reimplement.
      * Member route: data flows through the member visibility
        engine (resolve_entity_set, ownership/beneficiary look-
        through) scoped to only what that member is entitled to
        see.
      * BOTH routes must apply the restricted-access filter on
        top, regardless of which engine supplied the underlying
        set.
  - Do NOT call resolve_entity_set (or the staff engine) raw
    without the restricted-access wrapper — find and reuse the
    existing filter_restricted-style function from the SOC
    Restricted-Access-Accounts work rather than reimplementing
    that check.

=== TASK 1: Discover, don't assume ===
  (a) Locate and read HierarchyBuilder.jsx (or confirm it
      doesn't exist / isn't usable) — report its real current
      state.
  (b) Locate the existing time-travel/bitemporal date-picker
      component from Sprint 18 — report where it lives and its
      real props/usage.
  (c) Locate the actual staff-visibility-engine function/service
      and the actual restricted-access filter function — report
      their real names/signatures, do not guess.
Report all three findings before proceeding to Task 2.

=== TASK 2: Build/extend the interactive tree component ===
Based on Task 1's findings, build or extend a single shared
component supporting:
  - Collapsible nodes (collapsed beyond 1-2 levels by default
    on a large tree)
  - Pan & zoom
  - Time-travel (reusing the Sprint-18 date-picker component)
  - Reverse/owned-by toggle (flips traversal direction: to_
    entity_id -> from_entity_id instead of forward)
  - Filter by entity type, relationship type (ownership vs.
    beneficiary), and minimum ownership-percentage threshold
  - Edge-type visual distinction + legend (per the required
    design decisions above)
  - Attio-style clickable nodes — each node navigates to that
    entity's own detail page

=== TASK 3: Staff-facing route ===
A screen (e.g. under an existing entity/CRM section) rendering
the component for a given focal entity, data sourced through the
staff visibility engine + restricted-access filter (found in
Task 1c).

=== TASK 4: Member-facing route ===
A screen in the member portal rendering the SAME component for
the logged-in member's own ownership tree, data sourced through
resolve_entity_set scoped to that member + the SAME restricted-
access filter. A member must never be able to reach another
member's tree through this route.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme (whites/creams, Navy #1B2B4B for standard
nodes, Gold #C5A880 for the focal entity) matching every other
screen. Do NOT build the print/export mode — Sprint B, later.

=== VERIFICATION ===
Write verify_ownershiptreea.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A staff user with visibility (via the staff engine) into
      an entity sees it in the staff-route tree
  [Y] A staff user WITHOUT visibility into an entity does NOT
      see it (confirms the staff engine is actually wired in,
      not bypassed)
  [Y] A member sees their OWN ownership tree via the member
      route, including both an ownership-reached and a
      beneficiary-reached entity
  [Y] A member CANNOT see a different member's tree via the
      member route
  [Y] A restricted-access entity does NOT appear in either
      route's rendered tree/data for a user not on its allow-
      list, even when that user would otherwise have visibility
      via the underlying engine (proves the restricted-access
      filter is actually applied on top, not skipped)
  [Y] A restricted-access entity DOES appear for a user who IS
      on its allow-list
  [Y] Reverse/owned-by toggle returns the correct inverted set
      for a known test fixture
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new file
  [Y] Teardown: zero leftover test rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this touches visibility/
security-adjacent logic (the restricted-access wrapper).
