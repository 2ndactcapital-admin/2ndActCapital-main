PORTFOLIO UX — SECURITIES/ASSETS GRID, WITH PERMISSIONS BUILT IN
FROM THE START. 6 tasks + verification. Same established pattern
as portfolioux1/portfolioux2 (DataGrid, right pane, EntityPicker
where relevant, server-published editable-field lists, bitemporal
restatement not overwrite) — read that shipped code and mirror it
exactly, do not re-derive.

GENUINELY DIFFERENT FROM POSITIONS/TRANSACTIONS: this screen spans
TWO scopes. portfolio.assets is tenant-owned (org-scoped, A2).
portfolio.securities_global (+ identifiers/prices/relationships,
A1) is the GLOBAL master — Super-Admin-write only, by real,
already-enforced RLS. An org admin can edit their OWN asset row
(name, taxonomy, valuation_method) but must NEVER be able to edit
global security data (CUSIP/identifiers, cross-tenant price
history) through this screen, even indirectly. This is a SECOND
permission boundary neither prior screen needed.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: org_id never from request body; Decimal for
monetary values; light theme; schema-qualify every portfolio.*
reference (not on search_path).

=== TASK 1: DISCOVER ===
  1a. Confirm whether REST endpoints exist for assets/
      securities_global (likely partial-to-none, same gap A2/A1
      had for positions/transactions before their own UX
      sprints). Report exactly what exists today.
  1b. Read portfolioux1/portfolioux2's REAL shipped routers +
      services + Grid/DetailPane components — this sprint mirrors
      that shape exactly, including the vocabularies envelope,
      server-published inline-editable lists, mode='before' float
      refusal, and DocumentsPanel embedding pattern.
  1c. Confirm the REAL current Profiles/Permission-Set vocabulary
      already established in portfolioux1/ux2 (view_portfolio /
      manage_portfolio) — this screen's TENANT-scoped view/write
      uses the SAME names, not new ones.
  1d. Confirm the REAL current mechanism distinguishing a Super-
      Admin-only write from an org-write (per A1's
      _require_super_admin / _SuperAdminWrite pattern, already
      proven in services/securities_global.py). This screen's
      global-field boundary reuses THIS exact pattern, not a new
      one.
  1e. Confirm which fields on a joined asset+global-security view
      genuinely originate from EACH table — report this precisely
      so the UI can correctly mark global-sourced fields as read-
      only for a non-super-admin, and correctly attribute an
      asset's own fields as org-editable.

=== TASK 2: API ===
Build REST endpoints: list assets (org-scoped, joined with their
linked global security's identity/price data where
global_security_id is set), detail (full asset + global security
detail + identifiers + latest price + resolved current value, per
A2's real resolver), create/correct an asset (org-scoped fields
only — name, asset_type, taxonomy_key, valuation_method,
include_in_performance; NEVER global_security_id's own attributes
directly). A SEPARATE, Super-Admin-gated path (reusing A1's real
pattern) for anyone who needs to edit the global security itself
— this is a genuinely different, narrower surface, not exposed to
org admins at all.

=== TASK 3: PERMISSIONS — enforced from the start, both
boundaries ===
  - TENANT boundary: view_portfolio / manage_portfolio, per Task
    1c, gating the org-scoped asset endpoints exactly as
    Positions/Transactions already do.
  - GLOBAL boundary: the Super-Admin-only path is refused (403)
    for anyone else, server-side, reusing Task 1d's real pattern.
  - Prove BOTH server-side AND UI-side independently: a view-only
    user sees the grid but no write controls; an org-admin
    (manage_portfolio) can edit their OWN asset's own fields but
    the UI does not even render controls for global-sourced
    fields, and attempting to hit that path directly is refused
    server-side regardless.
  - Super-admin bypasses both, following the established checked-
    first pattern.

=== TASK 4: THE GRID ===
Columns: asset name, asset type, taxonomy, valuation method,
current resolved value, linked global identifier (CUSIP/ticker,
read-only display), org. Sort/filter real. Row select opens the
right pane. Inline-editable limited to whatever the server
publishes (mirror the prior two screens' pattern — likely
taxonomy_key and similar low-risk fields).

=== TASK 5: RIGHT PANE ===
Full asset detail: org-owned fields (editable per permission),
global security identity/identifiers/latest price (read-only
display, clearly marked as platform-sourced, not org-editable),
resolved current value with governing valuation, and linked
source documents via the existing generic document-link
mechanism.

=== TASK 6: REAL PROOF ===
  - Grid loads real, org-scoped assets, correctly joined with
    global data where linked.
  - An org admin can edit their own asset's own field; the SAME
    admin cannot edit a global-sourced field, proven both by the
    UI not rendering the control AND the API refusing it directly.
  - A view-only user can read but not write, both boundaries.
  - Super-admin can reach the separate global-security path;
    an org admin cannot, even by calling the endpoint directly.
  - Cross-org isolation on the org-scoped endpoints, including
    under the real, non-bypassing app_service role.
  - npm run build exits 0.

=== VERIFICATION: apps/api/scripts/verify_portfolioux3.py ===
Pass/fail only. Real data, real teardown (before/after count
match).

Assertions:
  [Y] Report Task 1's five findings explicitly
  [Y] Real endpoints exist, org-scoped list is correctly joined
      with global data
  [Y] An org-write-permission user can edit their own asset's own
      field
  [Y] The SAME user is REFUSED editing a global-sourced field —
      both UI-hidden AND server-refused, checked independently
  [Y] A view-only user can read but is refused any write
  [Y] Only super-admin can reach the global-security write path;
      an org admin's direct API attempt is refused (403)
  [Y] Cross-org isolation, application-level AND under the real
      non-bypassing app_service role
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
