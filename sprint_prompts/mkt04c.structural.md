MKT04C — KEY DATES, MY DATES, SAVED VIEWS AND THE CORRELATIONS PANEL. 6 tasks + verification.

mkt04a and mkt04b (merged) put the Market indicators page on main: the routes,
the selection panel, the Grid tab and the Chart tab with its anchor bar, hover,
regime bands and trends table. The backend for everything in this sprint already
exists and is verified. This sprint finishes the page: the key-dates dropdown,
"My dates" with add and delete, saved views, and the "What moves with it"
correlations panel, all as in the clickable mockup "Market Indicators — Trend
Explorer".

OUT OF SCOPE (do not build): any backend change; per-tenant gating of restricted
series (a separate sprint that must run before any external customer sees this
page); structured-note prices; a correlation matrix or rolling correlation;
organization-shared views or dates; editing the reference key dates or presets.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Routes (Next.js, already built): GET /api/market/key-dates, POST
  /api/market/key-dates/custom {name, event_date}, DELETE
  /api/market/key-dates/custom/{id}, GET /api/market/views, POST
  /api/market/views {name, config}, PUT /api/market/views/{id} {name?, config?},
  DELETE /api/market/views/{id}, POST /api/market/correlations {focus_key, keys,
  anchor, end?, lag_months?, min_periods?}.
- Key dates response: key_dates sorted by start_date, each {slug, name, kind,
  start_date, start_precision ('day'|'month'), end_date, end_precision, source};
  custom_dates (only the caller's, sorted by event_date) each {id, name,
  event_date, created_at}; regimes; data_range; permissions (can_read, can_write
  meaning "own rows"); vocabularies (kind labels) and limits (custom_dates_max,
  name_max). A month-precision date is stored as the first of its month.
- Custom-date errors come from the server with exact messages: 422 for a bad or
  empty name, a malformed date, a date outside the data range, or the limit; 409
  for a duplicate (same name, case-insensitive, same date). The UI shows the
  server's message verbatim and never re-implements the rules.
- Views response: presets (platform, read-only) and views (the caller's own),
  each {id, name, config, created_at, updated_at, unavailable: [keys]}; limits
  views_max and name_max; vocabularies (modes, scales). View config (v 1):
  {"v":1, "selection":[{"kind":"indicator"|"security","key":...}],
  "anchor":{"type":"date","value":"YYYY-MM-DD"}|{"type":"relative","years":N},
  "end": null|same shape, "mode":"index"|"sigma"|"default"|"level",
  "scale":"log"|"linear", "overlays":{"events":bool,"band":bool,"emphasis":bool}}.
  Saved views store the RESOLVED anchor date (or "N years ago"), never a reference
  to a key date. A selection whose series is no longer active comes back listed
  in `unavailable`, left in the config.
- Correlations: POST body {focus_key, keys (at most 40), anchor, end?, lag_months
  -24..24 default 0, min_periods 12..120 default 24}. Response: window and
  effective lag, and per candidate {series_key, r (string or null), n, frequency
  (the coarser of the pair), overlap_from, overlap_to, unavailable_reason}
  sorted by absolute r, nulls last; the focus is excluded. r is computed on
  CHANGES at the pair's coarser frequency, never on levels. It can take a few
  seconds.
- mkt04b layout to extend: pure modules in apps/web/lib/market/ (chartModel,
  chartRequest, rebase, scales, hitTest, overlays, labelLayout, chartContract,
  plus mkt04a's catalogModel, selection, gridRequest, gridView, marketDefaults,
  marketRoutes); components in apps/web/components/market/ (ChartPanel, ChartView,
  MarketChart, AnchorSlider, TrendsTable, MarketIndicatorsView, SelectionPanel,
  GridPanel, MarketGridTable); tests in apps/web/tests/market/ using Node's
  built-in runner; verify scripts verify_mkt04a.py and verify_mkt04b.py.
- Design and platform rules as in mkt04a and mkt04b: light theme only; Navy
  #1B2B4B, Gold #C5A880, app background #FAF9F6, white cards with a 1px #ece8dd
  hairline and 6px radius, text #0F172A / #334155 / #64748B, error #9B2335; Spectral
  headings, Hanken Grotesk body, 17px base; no emoji, no gradients, no heavy
  shadows. Rule 1: every label, vocabulary value, limit and message the server
  supplies comes from the server, never typed in the UI. Rule 5: client
  components call only the Next.js routes. A lost envelope fails CLOSED; write
  controls (add, delete, save, update) render only inside an explicit
  permissions.can_write === true check with no truthy fallback.
- Baselines (do NOT chain): verify_mkt04b 277, verify_mkt04a (re-pinned in
  mkt04b), verify_mkt03 68, all 0 failed.

DECISIONS ALREADY MADE, do not relitigate:
- Picking a key date moves the anchor to its start date. A range entry (one with
  an end date) also shows two buttons, "Start of period" and "End of period"
  (chrome text); Start is the default and every new pick resets to Start. The
  chart's anchor snaps to its data periods, so at the default monthly resolution
  a day-precision date lands on its month; the dropdown label still shows the
  exact date.
- A range entry shades its span on the chart in a distinct light blue
  (rgba(43,95,158,0.16)) whenever it is selected, even if the "Regimes & events"
  overlay is off. Personal dates draw as darker vertical lines than the
  reference key dates.
- Selecting a key date or a personal date clears when the user drags the anchor,
  uses the quick buttons, or loads a view; the anchor flag then shows only the
  date. While a selected date matches the anchor, the flag reads "Anchor · Mon
  YYYY · Name" (with "(end)" appended for an end-of-period anchor).
- Key dates and My dates are TWO dropdowns, so personal dates never mix with the
  curated list. Deleting a personal date asks "Delete "<name>" from your dates?"
  with confirm and cancel buttons.
- Views: presets and the caller's own views appear in a "Saved views" card in
  the left column. Clicking one loads it. A user view can be Updated (PUT with the
  current settings) or Deleted (with a confirm step). Presets show no update or
  delete control. Saving a new view POSTs the current settings under a name the
  user types; a 409 or 422 shows the server's message and keeps the name field.
- Loading a view with unavailable keys skips only those keys and shows a quiet
  notice naming them; the stored view is never modified by loading it.
- A view's measure is the chart measure (Index or Sigma). If a stored mode is
  not a chart measure ('default', 'level'), loading keeps the current chart
  measure and notes it. The Grid tab's own controls are not part of a view.
- Correlations panel: a "What moves with it" card under the chart. The focus is
  chosen from the currently selected series (indicators and linked securities);
  candidates are the other selected series. Window = the chart's anchor to its
  end; lag control -24..24; min_periods left at the server default. It requests
  after the anchor stops moving (debounced), shows a loading state, discards stale
  responses, and fails closed on an envelope-less response. Results show the name,
  a bar of |r| (navy for positive, error red for negative), the r string exactly
  as returned, n, and the frequency used; a null r shows the server's reason
  text. A quiet footnote says changes, not levels, are compared.
- Internal use only, as before: Yahoo-sourced and third-party-licensed series
  appear as the catalog returns them.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. If you have no database access in this
environment, say so explicitly and STOP.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- You MAY run unit tests and lint synchronously. Do NOT start the dev server, do
  NOT run the production build, and do NOT run the verify script: write it and
  STOP.
- No backend change, no database change, no edits to CLAUDE.md. Do not change the
  Grid tab's behavior.
- Never expose a secret. Never put org_id or user_id in a URL, body or state.
- No hardcoded label, vocabulary value, limit or server message. The strings that
  may be typed are page chrome (button captions, field labels, the section
  titles) and the design-token hexes.
- Pure logic in plain modules with no React and no DOM; components stay thin. Raw
  series strings are parsed only in rebase.mjs and scales.mjs (the mkt04b rule);
  correlation r strings are displayed as returned, never parsed.
- Edits to mkt04a and mkt04b files are allowed only where this sprint's features
  require them (lifting state, adding the new panels to the Chart tab and the
  left column). Do not weaken any existing proof.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read how the page holds state today: where the selection lives, where the
      chart's settings (anchor, measure, scale, resolution, overlays) live, how
      ChartPanel fetches key-dates, and how the Grid tab gets its own controls.
      Decide how to lift the chart settings and the selection into one place so a
      saved view can set both, and report the plan.
  1b. Read ChartModel's inputs and AnchorSlider to find how the anchor is snapped
      to periods and how to set it from a date programmatically.
  1c. Check how the existing mkt04a/b render tests load components (the jsx loader)
      so the new components are tested the same way.
  1d. Check the backend contract for views, custom dates and correlations by
      reading the routers and read services (read-only), and report any
      difference from CONFIRMED REAL FACTS.

=== TASK 2: PURE MODULES ===
In apps/web/lib/market/ with node:test tests:
  - keyDatesModel: build dropdown options (label "Mon D, YYYY · Name"; month
    precision "Mon YYYY"; ranges "start – end · Name"), map a date to the chart's
    period index with clamping, the Start/End selection state, the anchor flag
    text, the selected-period band geometry input, chronological order as
    returned, personal dates in their own list.
  - customDates: form state, enabling rules (the Save button needs a non-empty
    trimmed name and a date; every other rule is the server's), message handling
    (show the server message and keep the form), optimistic-free flow: the list
    updates from the server response only.
  - viewsModel: build a config from the current state; apply a config to the state
    (selection, resolved anchor, end, measure, scale, overlays) including
    "relative years" resolved against a supplied "today", the unavailable-key
    handling and the non-chart-measure note; the config built is exactly the
    canonical schema with no extra fields.
  - correlationModel: choose focus and candidates from the selection, build the
    request (focus excluded from keys, at most the server's key limit, lag within
    -24..24), debounce and newest-wins, format rows (name from the catalog, |r|
    bar width from the r string via a single display helper, sign class, n,
    frequency), keep server order, null r with its reason text.

=== TASK 3: KEY DATES AND MY DATES UI ===
  3a. Below the chart: the Key dates dropdown and, for a range entry, the Start of
      period / End of period buttons; the My dates dropdown (disabled with
      "None saved yet" when empty), Add (opens an inline form with Name, Date,
      Save date, Cancel; the Name input maxLength comes from the server's limit),
      and Delete (visible only when a personal date is selected; click shows the
      confirm row). Controls that write render only inside can_write === true.
  3b. Picking a date moves the anchor (through the state lifted in Task 1), shows
      the anchor flag text, and draws the selected-period band. Personal dates
      draw as darker lines under the existing "Regimes & events" toggle.
  3c. Saving a personal date selects it and moves the anchor to it. After a
      delete the list refreshes from the server.
  3d. The legend row gains "My date" and "Selected period" entries when relevant.

=== TASK 4: SAVED VIEWS UI ===
  4a. A "Saved views" card at the top of the left column: presets then the
      caller's views, each a button that loads it (the active view is marked); user
      views have a small delete control with the confirm step; the active user
      view has an Update button.
  4b. A name field and Save button create a view from the current state (POST);
      errors show the server's message inline.
  4c. Loading follows the decisions above, including the unavailable-key and
      non-chart-measure notices.
  4d. Views load once on page entry through the Next.js route with the same
      fail-closed rule; a failure to load views never blocks the chart or the grid.

=== TASK 5: CORRELATIONS PANEL ===
  5a. The "What moves with it" card under the chart and above the trends table:
      focus dropdown, lag control, results list, footnote, loading and error
      states, empty state when fewer than two series are selected.
  5b. Requests follow the decisions above; the key limit comes from the server's
      vocabularies; an envelope-less response shows an error and no results.

=== TASK 6: REAL PROOF AND DOCS ===
Write apps/api/scripts/verify_mkt04c.py and the node suites. Proofs, each with a
stated reason:
  - Pure modules: known-answer tests for every function in Task 2, including the
    relative-year resolution on a leap day, month-precision labels, the Start/End
    flag text, a config round-trip (build then apply gives the same state), the
    exact canonical config schema, unavailable keys skipped without changing the
    stored config, non-chart measures noted, and correlation requests that exclude
    the focus and respect the lag and key limits.
  - Components (via the jsx loader): both dropdowns render the right options; the
    Start/End buttons appear only for a range entry and reset on a new pick; the
    selected-period band shows when overlays are off; the delete confirm requires
    two steps; the add form shows the server's message and keeps its inputs on a
    422 or 409; with can_write false (or no envelope) no add, delete, save or
    update control renders; presets show no update or delete control; the
    correlations card shows loading, results, nulls with reasons, and fails
    closed.
  - Requests: one request per burst for correlations, stale responses discarded,
    nothing sent with fewer than two series, views and key dates fail closed on a
    missing envelope.
  - Static scans extended to the new files: no hardcoded label, vocabulary value,
    limit or server message; no client call outside /api/market/*; no org_id or
    user_id; no parsing of r strings; parsing of series strings only in the two
    pinned modules; light theme, no gradients, heavy shadows or emoji.
  - Scope: changes confined to apps/web, verify_mkt04c.py, docs and the sprint
    files; no backend file changed.
  - Build: npm run lint clean on files touched by this sprint, and the production
    build exits 0 (the verify script runs both).
  Docs: add an mkt04c entry to docs/PROJECT_STATUS.md; append `UPDATE <today>`
  under the mkt04b entry saying the page is feature-complete for internal use and
  listing what remains before any external customer (the per-tenant gate for
  restricted series). Add a "Key dates, views and correlations (front end)"
  section to docs/MARKET_DATA_DESIGN_V1.md covering the decisions above. Do not
  edit CLAUDE.md.

=== VERIFICATION: apps/api/scripts/verify_mkt04c.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT run
it. It may shell out to npm and node. Output lines begin with [PASS], [FAIL],
[FIND], or [SKIP]; the last line is `TOTAL: <passed> passed, <failed> failed`.
Exit non-zero on any [FAIL]. Every assertion states why it matters. If it needs to
re-pin an earlier verify script's scope checks (as mkt04b did for mkt04a), do so
minimally and record it as a [FIND].

When finished, STOP and print exactly these operator commands, in this order:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04c.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04b.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04a.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
