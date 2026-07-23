GRID UX — MINI-SPRINT B: migrate existing screens onto the
DataGrid component built in mini-sprint A. Small, capped scope
— discover candidates, migrate the TWO best ones, no more.
Do NOT attempt to migrate every list/table screen in one pass —
that overreach is exactly what made S24 run long; this sprint
follows gridux_a's "one pilot at a time" pattern instead.

CONTEXT: apps/web/components/ui/DataGrid.jsx exists (mini-sprint
A) — TanStack Table for state (sort/filter/pagination/column-
visibility/column-order) + @dnd-kit for drag-reorder, styled via
var(--2a-*) tokens. Stable prop API: columnDefs, rowData, gridId,
onRowClick, selectedRowId, getRowId. Already piloted on
SPVLedgerClient.jsx's events list — do NOT touch that screen
again, it's done.

STANDING RULES: light theme (whites/creams) throughout; no
interactive prompts; Decimal for any money columns.

=== TASK 1: Discover real candidates — DO NOT ASSUME ===
Grep the codebase for plain hand-rolled <table> markup across
apps/web (excluding SPVLedgerClient.jsx, already migrated, and
DataGrid.jsx itself). Look specifically at:
  - The Marketplace deal list (apps/web/app/marketplace/page.js
    or similar — the list with Name/Asset Class/Stage/Target/
    Min/Return/Term/Score/Interest/Votes columns, confirmed to
    exist and be a plain table)
  - Any entity/CRM list view
  - Any member/user list view
  - Any other page rendering a real tabular list of records
Report every candidate found, with file path and a rough sense
of complexity (how many columns, any special cell rendering
like status pills, links, or action buttons).

=== TASK 2: Migrate the two best candidates ===
From Task 1's findings, pick the TWO screens that are (a) most
clearly a good fit — a real list of records with sortable/
filterable columns — and (b) reasonably similar in complexity to
what DataGrid already handles (don't pick something with deeply
nested or unusual rendering that would require extending
DataGrid itself; if nothing simple qualifies beyond marketplace,
migrate marketplace plus the next-clearest candidate).
For each of the two:
  - Replace the hand-rolled <table> with <DataGrid
    columnDefs={...} rowData={...} .../>
  - Preserve ALL existing functionality — every column, any
    existing sort/filter/link behavior, status pills, row-click
    navigation — nothing should look or behave differently to
    the user except gaining DataGrid's column-picker/reorder/
    quick-search on top.
  - Reuse cell-rendering patterns already established in
    DataGrid.jsx or SPVLedgerClient.jsx's migration (e.g. how
    status pills or clickable name cells were handled there) —
    do not invent a new pattern if one already exists.
If Task 1 finds FEWER than two good candidates, migrate however
many genuinely qualify and explicitly report why others were
skipped (too complex, not really tabular, etc.) rather than
forcing a poor fit.

=== VERIFICATION ===
Write apps/api/scripts/verify_gridux_b.py, same pattern as
gridux_a's verify — no DB assertions needed (frontend-only), but
still pass/fail only, no interactive prompts.

Assertions to include:
  [Y] Each migrated screen's file now imports and renders
      <DataGrid> (grep-based check)
  [Y] The old hand-rolled <table> markup is gone from each
      migrated file (not left dead/duplicated alongside DataGrid)
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex introduced in either
      migrated file (reuse brand_sweep_grep.sh's hex pattern,
      scoped to just these two files)
  [Y] Report which two (or fewer) screens were migrated and why,
      as part of the verify output

Report each assertion explicitly (pass/fail). Push when 100%
pass.
