TA MODEL — SPRINT 3: COMMITMENT PROJECTION UX. 5 tasks +
verification. Sprints 1-2 are complete and merged: real backend,
real admin settings screen. This sprint builds the member/staff-
facing view — showing a real commitment's projected cash flows,
not editing platform configuration.

CONFIRMED REAL FACTS FROM SPRINTS 1-2, DO NOT RE-DERIVE:
- GET /api/v1/modeling/ta/projection/{commitment_id} is real,
  proven end-to-end against a real commitment, current_nav
  reflects the real position's market_value.
- POST /api/v1/modeling/ta/projection/preview exists — an
  UNSAVED, non-persisted computation (proven by row-count
  before/after in Sprint 1). This is the "what if" tool: vary an
  assumption, see the effect, nothing written.
- Every period's monetary field is a JSON STRING (Decimal-as-
  string) — this screen is likely READ-ONLY display, but the same
  discipline applies: never coerce through a JS float, never
  format with naive parseFloat.
- Projected cash flows are NEVER persisted server-side — this
  screen must not attempt to build any "saved projection history"
  feature; there is nothing to page through, only a live
  computation against current inputs.
- The real permission-envelope pattern (Workflow Triggers /
  TA Settings shape) is the established convention — reuse
  verbatim.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; light theme; Decimal-
as-string discipline throughout.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm whether a real, existing Commitments list/detail
      screen already exists in apps/web (portfolio.commitments is
      real and populated per Sprint 1's own discovery). If one
      exists, this sprint ADDS a projection tab/panel to it rather
      than building a new standalone screen — confirm and follow
      whatever's real.
  1b. Confirm the REAL permission gating a commitment record
      today (likely view_portfolio, per the established portfolio
      permission pattern — confirm the actual gate on whatever
      commitment-reading endpoint already exists, do not assume
      a TA-specific permission is needed).
  1c. Confirm the exact real response shape of BOTH
      GET /projection/{commitment_id} and
      POST /projection/preview — specifically what parameters the
      preview endpoint accepts as overrides (bow, growth_rate,
      contribution schedule?) versus what the saved GET uses
      (whatever's actually configured/calibrated for that
      commitment via Sprint 1/2's real settings).
  1d. Confirm whether a real charting/visualization library is
      already a dependency in apps/web (check package.json) —
      reuse whatever's established, do not introduce a new
      charting library unilaterally.

=== TASK 2: THE SCREEN ===
A real projection view for one commitment: a chart showing
contributions/distributions/NAV across the projection horizon (per
Task 1d's real library), plus a table of the same data by period
or by year (reuse the design's own by_year() aggregation if it's
exposed by the API, rather than re-aggregating client-side). A
real "what if" panel using the live preview endpoint — adjusting
bow/growth/periods_per_year and seeing the projection update
WITHOUT saving, clearly labeled as a preview, not the commitment's
actual configured projection.

=== TASK 3: DECIMAL DISCIPLINE IN DISPLAY ===
Every monetary value rendered on this screen is formatted from the
API's own string value directly — confirm no parseFloat/Number()
coercion happens anywhere in the rendering path before display
formatting. Percentages/rates same discipline.

=== TASK 4: REAL PROOF ===
  - A real commitment's real, saved projection renders end-to-end
    through the real API — chart and table both reflect the same
    real data.
  - The preview tool genuinely computes a different result when
    an assumption is changed (e.g., a higher bow visibly defers
    distributions in the rendered output) — proven against the
    real preview endpoint, not a client-side recomputation.
  - Confirmed: using the preview tool does NOT persist anything —
    real row-count check before/after, matching Sprint 1's own
    proof shape.
  - No JS float coercion anywhere in the monetary rendering path —
    proven by a value that would visibly corrupt if it were
    (e.g., a value whose string form would round differently
    through a float).
  - Permission: reuses the REAL existing commitment-read
    permission (per Task 1b) — a caller without it is refused the
    projection view, proven server-side and by absent UI.
  - Cross-org: org B cannot view org A's commitment's projection
    (404, not org A's data).
  - npm run build exits 0.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and the TA brief's phasing note:
Sprint 3 complete, Sprint 4 (calibration UX + obligation ledger
integration) next.

=== VERIFICATION: apps/api/scripts/verify_tamodel3.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A real commitment's real projection renders end-to-end,
      chart and table consistent with each other
  [Y] The preview tool produces a genuinely different result on a
      changed assumption, via the real endpoint
  [Y] Preview confirmed non-persisting, real row-count check
  [Y] No float-coercion corruption anywhere in the monetary
      rendering path
  [Y] The real, existing commitment-read permission gates this
      screen — proven server-side and by UI absence
  [Y] Cross-org isolation (404, not data leakage)
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
