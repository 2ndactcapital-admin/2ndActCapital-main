POSITIONS + TRANSACTIONS — PERMISSIONS RETROFIT. 5 tasks +
verification. portfolioux1 (Positions) and portfolioux2
(Transactions) shipped with NO permission gating at all — org
isolation only. portfolioux3 (Securities) shipped WITH real
view/write gating from day one, using view_portfolio /
manage_portfolio, checked both server-side and UI-side
independently, with the specific test-fixture trap already found
and solved (rbac.has_permission default-allows a zero-role user,
so a "view-only" fixture must be given a REAL deployed role and
its effective permissions asserted before use — do not repeat
this mistake).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: org_id never from request body; light theme;
schema-qualify every portfolio.* reference.

=== TASK 1: DISCOVER — read portfolioux3's REAL shipped
enforcement, do not re-derive it ===
  1a. Read the REAL current routers/portfolio_securities.py +
      services/portfolio_securities.py — the exact shape of the
      view_portfolio/manage_portfolio check at the router layer,
      and how the endpoint publishes can_read/can_write/editable
      fields in its response envelope (the "vocabularies +
      permissions" pattern Securities uses).
  1b. Read the REAL current SecuritiesGrid.jsx + AssetDetailPane.jsx
      — exactly how the frontend reads the server-published
      permission flags to decide which controls to render (no
      client-side fallback list, no `|| DEFAULTS`).
  1c. Confirm the REAL current routers/portfolio_positions.py +
      routers/portfolio_transactions.py — confirm they genuinely
      have ZERO permission checks today (org isolation only, per
      the merge history) before assuming what needs adding.
  1d. Confirm the real, deployed role→permission grants for
      view_portfolio and manage_portfolio (already known from
      portfolioux3: view=admin/advisor/investment_staff/member/
      super_admin; manage=admin/advisor/super_admin) — this
      sprint's test fixtures use the SAME real roles, not
      invented ones.
Report all four findings before proceeding.

=== TASK 2: FIX — Positions ===
Add view_portfolio/manage_portfolio gating to every endpoint in
routers/portfolio_positions.py, following portfolioux3's EXACT
pattern: the list/detail endpoints require view_portfolio; create/
correct require manage_portfolio; the response envelope publishes
can_read/can_write and the real editable-field list (empty array
for a view-only caller, per portfolioux3's "no client-side
fallback" discipline). Update PositionsGrid.jsx /
PositionDetailPane.jsx to read these published flags exactly as
SecuritiesGrid.jsx does — no independent permission logic in the
frontend.

=== TASK 3: FIX — Transactions ===
Identical treatment for routers/portfolio_transactions.py +
services/portfolio_transactions.py + TransactionsGrid.jsx +
TransactionDetailPane.jsx. The correction endpoint
(POST .../corrections) requires manage_portfolio — a view-only
user must be able to see the correction history but never create
one.

=== TASK 4: SUPER-ADMIN BYPASS, PROVEN THE SAME WAY ===
Both fixed routers must let super_admin bypass both permission
checks, using the SAME checked-first pattern already proven
everywhere else this session (rbac.is_super_admin checked before
any granular permission lookup). Prove it as its own explicit
assertion, not inferred from the write-permission tests passing.

=== TASK 5: REAL PROOF, BOTH SCREENS, BOTH DIRECTIONS ===
For EACH of Positions and Transactions, independently:
  - A view-only-permission user (real deployed role, per Task 1d
    — NOT a zero-role fixture, per the known has_permission trap)
    can read the grid and detail pane, but the server publishes
    an EMPTY editable list and REFUSES any write attempt (403,
    naming the required permission) — proven server-side.
  - The SAME user's UI genuinely renders no write controls —
    checked independently of the server-side block, per
    portfolioux3's dual-proof standard (a hidden button with an
    unprotected endpoint, or a protected endpoint behind a
    visible button, are both real bugs this sprint must rule
    out).
  - A manage_portfolio user can genuinely write.
  - super_admin bypasses both checks on both screens.
  - Cross-org isolation, already proven in ux1/ux2, is CONFIRMED
    UNCHANGED by this sprint — a regression check, not a new
    finding.

=== VERIFICATION: apps/api/scripts/verify_portfolioux4.py ===
Pass/fail only. Real data, real teardown (before/after count
match, these tables hold real rows).

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] POSITIONS: view-only reads but is refused write, server-
      side, with an empty published editable list
  [Y] POSITIONS: view-only's UI renders no write controls,
      checked independently
  [Y] POSITIONS: manage_portfolio user can genuinely write
  [Y] POSITIONS: super_admin bypasses both checks
  [Y] TRANSACTIONS: view-only reads but is refused write AND
      refused the correction endpoint specifically, server-side
  [Y] TRANSACTIONS: view-only's UI renders no write/correct
      controls, checked independently
  [Y] TRANSACTIONS: manage_portfolio user can genuinely write and
      correct
  [Y] TRANSACTIONS: super_admin bypasses both checks
  [Y] Cross-org isolation on both screens confirmed UNCHANGED
      (regression, not new)
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
