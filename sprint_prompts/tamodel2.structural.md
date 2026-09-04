TA MODEL — SPRINT 2: ADMIN SETTINGS UX. 5 tasks + verification.
Sprint 1 is complete and merged (77/77) — real backend, real
endpoints, real bi-temporal persistence. This sprint builds the
screen letting an org admin edit modeling.ta.* settings. Same
established pattern as every prior UX sprint this project:
DataGrid/right-pane where a list view fits, server-published
permission envelope, no client-side fallback lists.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

CONFIRMED REAL FACTS FROM SPRINT 1, DO NOT RE-DERIVE:
- Real endpoints: GET /api/v1/modeling/ta/defaults (open to any
  authenticated org member — matches org_settings' own read-open
  convention) and PUT /api/v1/admin/modeling/ta/defaults (gated
  on can_manage_org_settings — proven: refuses a plain member,
  admits org_admin, on the byte-identical request).
- The 8 real strategy keys: buyout, growth_equity,
  venture_capital, real_estate, real_assets, private_credit,
  fund_of_funds, secondaries — genuinely new, not backed by any
  existing taxonomy field (confirmed by Sprint 1's own discovery).
- The 4 real settings: TA_STRATEGY_DEFAULTS_KEY (per-strategy bow/
  contribution_rate/fund_life_years), TA_PROJECTION_HORIZON_YEARS_KEY,
  TA_DEFAULT_PERIODS_PER_YEAR_KEY, TA_CALIBRATION_MIN_YEARS_KEY.
- The frequency-aware calibration floor is REAL and must be
  reflected honestly in this UI: changing periods_per_year changes
  the real minimum realized-history requirement (12 quarters, not
  a flat 3) — do not let the settings screen imply otherwise.
- All monetary/rate values are Decimal, stored as STRINGS in
  jsonb — the UI must never round-trip a rate through a JS float
  in a way that could re-serialize imprecisely.

STANDING RULES: no interactive prompts; light theme; reuse the
real, established permission-envelope pattern verbatim.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm the REAL current GET /api/v1/modeling/ta/defaults
      response shape exactly — per-strategy structure, field names,
      whether it already indicates which values are org-overridden
      vs. platform-default (an admin editing this needs to see
      which strategies they've actually touched vs. which are
      still inheriting the seed).
  1b. Confirm whether ANY existing admin settings screen in this
      codebase already edits an org_settings key with a similar
      shape (nested per-category values) — reuse that established
      UI pattern if one exists, rather than inventing a new one.
  1c. Confirm the real current PUT endpoint's validation — does it
      reuse ta_config's own real validation (bow > 0, rates in
      [0,1], etc.), or does the router re-implement checks
      separately? The UI's own client-side hints (not
      replacements for server validation) should match whichever
      is real.

=== TASK 2: THE SCREEN ===
A real admin settings editor for the 8 strategies × their
parameters (bow, contribution rate schedule, fund life years) plus
the 3 platform-level TA settings (horizon years, default periods
per year, calibration min years). Editing a strategy that has
never been overridden starts from the seed default (visibly
labeled as "platform default" vs. "your override" — reuse Task
1a's real indicator if one exists, add it to the response if
missing). Changing periods_per_year for a strategy should visibly
show the real, resulting minimum-calibration-periods requirement
(reuse the real minimum_realized_periods function's output, not a
re-derived value).

=== TASK 3: VALIDATION, SERVER-SOURCED ===
Every validation message the form shows comes from the real API's
actual 422 response — no client-side re-implementation of bow > 0,
rate ranges, or the frequency floor. Confirm this by checking the
form renders the API's own error text verbatim, per this project's
established pattern from the Scheduler CRUD UX sprint.

=== TASK 4: REAL PROOF ===
  - Screen loads real, live settings via the real GET endpoint —
    no mock data.
  - Editing and saving a strategy's bow factor through the UI
    produces a real PUT call, and a subsequent GET (real, not
    cached) reflects the change.
  - A validation error from the real API (e.g. bow <= 0) is
    surfaced to the user verbatim, not re-derived client-side.
  - A view-only member (no can_manage_org_settings) can see this
    screen's current values but the UI renders no save/edit
    controls, AND the API refuses a direct PUT attempt — both
    checked independently.
  - Cross-org: org B's screen never shows org A's overridden
    values.
  - npm run build exits 0.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and the TA brief's own phasing note:
Sprint 2 complete, Sprint 3 (commitment projection UX) next,
independent of this one.

=== VERIFICATION: apps/api/scripts/verify_tamodel2.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's three findings explicitly
  [Y] Real settings load via the real API, no mock data
  [Y] A UI-driven edit produces a real, persisted change,
      confirmed via a fresh GET
  [Y] A real 422 from the API is surfaced verbatim in the UI
  [Y] View-only: no write controls rendered AND API-level refusal
      on direct attempt, checked independently
  [Y] Cross-org isolation on the settings screen
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
