PORTFOLIO UX — POSITIONS GRID (flagship screen). 5 tasks +
verification. Addepar-style: a dense, tight, sortable/filterable
grid as the main view, a right-side detail pane on row selection,
inline editing where safe, minimal clicks — no wizards, no
multi-step modals for routine edits. This establishes the pattern
Transactions and Securities screens will reuse next.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

STANDING RULES: org_id never from request body; Decimal for any
monetary value; light theme (2nd Act Signature palette); schema-
qualify every portfolio.* reference (portfolio is NOT on
search_path — confirmed repeatedly, do not rediscover this).

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm whether REST endpoints already exist wrapping
      portfolio_assets.py's create_position/record_transaction/
      resolve_current_value, or whether only the Python service
      functions exist with no router. If missing, this task list
      includes building them — confirm the real gap before
      assuming either way.
  1b. Read the REAL current DataGrid.jsx — its actual props,
      column-definition shape, sort/filter/inline-edit support,
      and how it was wired into the SPV ledger / DealsTable /
      EntityTable. This sprint reuses it, not a new grid library.
  1c. Confirm whether a reusable "right-pane detail drawer"
      pattern already exists anywhere in apps/web (Chancery's
      DocumentsPanel is embedded across pages — check if ITS
      shell/panel mechanism is generic enough to reuse, or if a
      new one is needed).
  1d. Confirm the real, current entity-picker/owner-selection
      component already used elsewhere (CRM, SPV subscriptions)
      — a position's owner_entity_id needs the SAME picker, not a
      new one.

=== TASK 2: API — whatever Task 1a found missing ===
Build real REST endpoints for: listing positions (filterable by
owner, asset, taxonomy, as_of_date, source_system, superseded
state), creating/editing a position (respecting the real
ownership-basis contract — units/percent/value, enforced the same
way portfolio_assets.py already does), and reading a position's
resolved current value + valuation history.

=== TASK 3: THE GRID ===
A real Positions screen: DataGrid.jsx driven, columns for asset
name, owner, taxonomy, quantity/pct/value (whichever the basis
uses), current value, authority, source, as_of_date. Real column
sort/filter. Selecting a row opens the right-pane detail (Task 4)
— no navigation away from the grid. Inline-editable cells where
safe (e.g. taxonomy_key reassignment); anything requiring the
ownership-basis validation opens in the right pane instead of an
inline cell, since that needs real validation feedback.

=== TASK 4: THE RIGHT PANE ===
Selecting a position shows: full asset detail, current resolved
value with its governing valuation (status/date, per A2's real
ladder), transaction history for that position, and — where
linked — the source document via the REAL existing document-
linking mechanism (Phase D's link_portfolio_document /
DocumentsPanel), so a number can be clicked through to its source
without leaving this screen.

=== TASK 5: REAL PROOF ===
  - The grid loads real, live positions (seeded or existing),
    correctly filtered by the caller's own org.
  - Sorting and filtering work against real data, not a stub.
  - An inline edit persists and is reflected on reload.
  - Selecting a row shows the right pane with REAL resolved
    value + valuation history + transaction history, matching
    what a direct query of the same position returns.
  - Cross-org isolation: an org cannot see another org's
    positions in the grid, tested against the real app_service
    connection.

=== VERIFICATION: apps/api/scripts/verify_portfolioux1.py (or
the real appropriate location if this is frontend-only — confirm
in Task 1) ===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Real API endpoints exist and are used by the frontend (not
      mocked data)
  [Y] Grid renders real positions, correctly org-scoped
  [Y] Sort/filter genuinely operate on real data
  [Y] An inline edit round-trips through the real backend and
      persists
  [Y] The right pane shows real resolved value + valuation
      history + transaction history for a selected position
  [Y] A linked source document is reachable from the right pane
  [Y] Cross-org isolation on the new endpoints
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
