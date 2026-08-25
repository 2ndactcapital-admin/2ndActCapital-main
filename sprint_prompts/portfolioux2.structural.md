PORTFOLIO UX — TRANSACTIONS GRID. 5 tasks + verification. Same
established pattern as portfolioux1 (PositionsGrid): DataGrid.jsx
reused, right-pane detail, EntityPicker for owner filter, server-
published inline-editable field list, bitemporal restatement (not
overwrite) on edit. Do not re-derive these patterns — read
portfolioux1's real, shipped code and follow it exactly.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: org_id never from request body; Decimal for
monetary values; light theme; schema-qualify every portfolio.*
reference (not on search_path).

=== TASK 1: DISCOVER ===
  1a. Confirm whether REST endpoints exist for transactions (very
      likely not — record_transaction is service-only, same gap
      portfolioux1 found for positions). Confirm real update
      semantics: does an edit path exist, or only insert?
  1b. Read portfolioux1's REAL shipped routers/portfolio_
      positions.py + services/portfolio_positions.py + 
      PositionsGrid.jsx + PositionDetailPane.jsx — this sprint
      mirrors that shape exactly for transactions.
  1c. Confirm the real transaction_type_code validation (market
      compatibility with the position's asset, per Phase E) and
      is_corporate_action_adjustment's real meaning (Phase F) —
      the grid must respect both, not re-derive them.
  1d. Confirm whether a transaction, once recorded, is currently
      editable at all in the service layer, or append-only. If
      append-only, this sprint builds a CORRECTION path (a new
      offsetting/superseding transaction), not an in-place edit —
      report which is true before building either.
Report all four findings before proceeding.

=== TASK 2: API ===
Build REST endpoints mirroring portfolioux1's shape: list
(filterable by position_id, asset, owner, transaction_type_code,
trade_date range, is_corporate_action_adjustment, source_system),
detail, create. Editing follows whatever Task 1d found — a real
correction/restatement mechanism, not a silent in-place UPDATE.

=== TASK 3: THE GRID ===
DataGrid-driven. Columns: trade date, asset, owner, type (label,
not raw code), quantity/price or amount as applicable, net
amount, source, and a clear visual marker for
is_corporate_action_adjustment rows (per Phase F: these must
never look like an ordinary trade). Sort/filter real. Row select
opens the right pane. Inline-editable fields limited to whatever
the server publishes as safe (mirror positions' pattern —
probably very few, given ledger-entry semantics).

=== TASK 4: RIGHT PANE ===
Full transaction detail, the position it belongs to (linked,
clickable through to the Positions screen), and — where linked —
source document via the existing document-linking mechanism
(same RECORD_TYPE dispatch pattern already proven generic in
portfolioux1's bonus finding).

=== TASK 5: REAL PROOF ===
  - Grid loads real transactions, correctly org-scoped.
  - Filtering by is_corporate_action_adjustment genuinely narrows
    (prove both directions — with and without).
  - Filtering by transaction_type_code narrows correctly.
  - A correction/edit follows the real Task-1d mechanism, proven
    against the live database, and the original row remains
    intact/reachable per whatever pattern was found.
  - Cross-org isolation on every new endpoint, including under
    the real, non-bypassing app_service role (not just
    application-level).
  - npm run build exits 0.

=== VERIFICATION: apps/api/scripts/verify_portfolioux2.py ===
Pass/fail only. Real data, real teardown (before/after count
match, not truncate — these tables hold real rows).

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Real endpoints exist and are used by the frontend — no
      mock data
  [Y] Grid renders real, org-scoped transactions
  [Y] is_corporate_action_adjustment filter narrows correctly,
      both states proven
  [Y] transaction_type_code filter narrows correctly
  [Y] A correction/edit works per the real Task-1d mechanism,
      original row still reachable
  [Y] The right pane links back to the owning position and any
      linked source document
  [Y] Cross-org isolation, application-level AND under the real
      non-bypassing app_service role
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
