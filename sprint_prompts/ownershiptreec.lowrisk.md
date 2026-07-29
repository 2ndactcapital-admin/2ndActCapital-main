OWNERSHIP TREE GRAPH — PHASE C (CRM integration + walk
navigation). 3 tasks + verification. Pure UI/navigation
integration — no schema changes, no changes to the underlying
visibility engines (staff_visibility/resolve_entity_set/
restricted_access) built in Sprints A/B.

CONTEXT: OwnershipGraph.jsx, AsOfPicker.jsx, and
EntityGraphNavigator.jsx already exist and work (Sprints A/B).
The staff-facing route already exists at
apps/web/app/crm/[id]/ownership-graph/page.js but is NOT
currently surfaced anywhere in the entity's normal CRM page —
reachable only by direct URL. Sprint 18's OwnershipTab.jsx
(inline ownership editing + time-travel date picker) already
exists on the entity CRM page and is the natural home for this.

STANDING RULES: no interactive prompts; light theme matching
every other screen.

=== TASK 1: Discover, don't assume ===
  (a) Read OwnershipTab.jsx's real current structure (Sprint 18)
      — confirm where/how it renders within the entity CRM page.
  (b) Read OwnershipGraph.jsx's real props/embeddable interface
      (Sprint A) — confirm whether it can be embedded directly
      inline, or whether it expects a full-page layout.
Report both findings before proceeding.

=== TASK 2: Surface the graph from the existing Ownership tab ===
Based on Task 1's findings, embed the OwnershipGraph component
directly within OwnershipTab.jsx (preferred, if Task 1b confirms
it embeds cleanly) OR add a clearly-visible, one-click link/sub-
tab to the existing standalone route (if embedding isn't clean).
Either way: a user on an entity's normal CRM page must be able to
reach its ownership graph in one click, not by knowing a URL.

=== TASK 3: "Walking" navigation — smart landing on click ===
When a node in the graph is clicked (EntityGraphNavigator),
navigating to that entity's CRM page must land the user with the
Ownership tab/graph ALREADY active/visible — not just the
generic entity page requiring a second click to find it again.
Also confirm the reverse/owned-by toggle remains a single click
at each stop (already built in Sprint A — just confirm it's
still easily reachable in this embedded context, don't rebuild
it).

STANDING NOTE (not part of this sprint, do not fix here): a
separate, already-identified gap exists where Super Admin does
not bypass staff_visibility.get_staff_visible_entity_ids the way
every other part of this platform's convention does (every RLS
policy, restricted_access, trading_authority all include an
explicit is_super_admin escape hatch). This is being tracked as
its own separate fix — do not bundle it into this sprint.

=== VERIFICATION ===
Write verify_ownershiptreec.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end. Since this is primarily UI, combine a reachability check
with a build check.

Assertions:
  [Y] Report Task 1's two discovery findings explicitly
  [Y] The Ownership tab's rendered output includes the graph (or
      a working link to it) — confirm via a DOM/build-level check
      appropriate to what Task 2 actually built
  [Y] Clicking a node's generated link/route correctly targets
      the destination entity's Ownership tab specifically, not
      just its generic CRM page
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new/modified file
  [Y] Teardown: zero leftover test rows

Report each assertion explicitly. Push when 100% pass.
