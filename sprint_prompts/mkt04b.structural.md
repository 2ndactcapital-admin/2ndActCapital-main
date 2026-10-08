MKT04B — MARKET INDICATORS CHART: ANCHOR BAR, REBASING, OVERLAYS. 6 tasks + verification.

mkt04a (merged) put the Market indicators page on main: eleven Next.js routes,
the selection panel and the Grid tab. The Chart tab still shows a placeholder.
This sprint builds the chart from the clickable mockup "Market Indicators —
Trend Explorer": a multi-line SVG chart with a draggable anchor bar that
re-measures every line from the chosen date, a log or linear axis, a labeled y
axis, hover with name and value, regime bands and key-date lines, a cohort band
with outlier highlighting, and the "Trends and outliers" table.

OUT OF SCOPE (do not build): the key-dates and "My dates" dropdowns, saved
views, the correlations panel (all mkt04c); per-tenant gating of restricted
series; structured-note prices; any backend change other than the one additive
field in Task 2.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- mkt04a layout: pure modules in apps/web/lib/market/ (catalogModel, gridRequest,
  gridView, marketDefaults, marketRoutes, selection, all .mjs, no React/DOM),
  components in apps/web/components/market/ (MarketIndicators, MarketIndicatorsView,
  SelectionPanel, GridPanel, MarketGridTable, marketStyles.mjs), tests in
  apps/web/tests/market/ using Node's built-in runner (node:test), a jsx loader
  helper, and the eleven route handlers under apps/web/app/api/market/. The page
  is /market. Its verify script is apps/api/scripts/verify_mkt04a.py (225 passed).
  Selection is held as [{kind, key}].
- Routes the chart uses (through the Next.js routes only): GET /api/market/series
  ?keys=&from=&to=&frequency= and GET /api/market/key-dates. Series response per
  key: series_key, native frequency, returned frequency, point_count,
  first_observation_date, last_observation_date, points as [date, "value"] pairs
  with STRING values, ascending. frequency means "at most this fine" (native,
  daily, weekly, monthly, quarterly): each point is the last observation in its
  period, reported with its real date; coarser series are never upsampled. At
  most 40 keys; a request over the server's point cap gets 422 with a message.
  GET key-dates returns key_dates [{slug, name, kind, start_date,
  start_precision, end_date, end_precision, source}] sorted by start_date,
  regimes [{regime_type, name, start_date, end_date}], data_range, permissions,
  vocabularies (kind and regime labels from the server) and limits.
- Catalog gives each series' color, name, category, units, default_transform and
  first/last observation dates; categories carry colors. Securities selectable
  today resolve to a linked series_key. A selected security is requested by its
  series_key.
- TRANSFORM DEFINITIONS are canonical in docs/MARKET_DATA_DESIGN_V1.md. As-of:
  the value of the last observation on or before the date. Anchor value v0 =
  as-of(anchor); if none, v0 = the first observation and the series is FLOATING.
  index = 100 * v / v0 (needs v0 > 0, else unavailable 'non_positive_anchor';
  a default_transform 'level' series still indexes but carries the warning
  'rate_like_series_indexed'). sigma = (v - v0) / sd where sd is the sample
  standard deviation over ALL of the series' active observations (full history,
  independent of anchor and window); sd null or 0 is 'zero_variance'.
- IMPORTANT GAP: the series endpoint does not return sd, and sd computed from a
  downsampled series differs slightly from the server's. Task 2 closes this.
- Design tokens and rules as in mkt04a: light theme only; Navy #1B2B4B, Gold
  #C5A880, app background #FAF9F6, white cards with a 1px #ece8dd hairline and 6px
  radius, text #0F172A / #334155 / #64748B, error #9B2335; Spectral headings,
  Hanken Grotesk body, 17px base; no emoji, no gradients, no heavy shadows.
  Rule 1: labels, colors, vocabularies and limits come from the server; Rule 5:
  client components call only the Next.js routes; a lost envelope fails CLOSED.
- Baselines (do NOT chain): verify_mkt04a 225, verify_mkt03 68, verify_mkt03b
  114, verify_mkt02 52, verify_mkt01 57, all 0 failed.

DECISIONS ALREADY MADE, do not relitigate:
- The chart is custom SVG, no charting library and no new dependency. The mockup's
  behaviors (anchor bar, rebasing, hover, bands) are bespoke.
- The browser rebases while the anchor moves (instant scrubbing) from the raw
  series data and the server's sd. The grid keeps using the server's computed
  values.
- Default resolution is MONTHLY for the whole span. A Resolution control offers
  the server's frequencies; a request the server refuses (point cap) shows the
  server's message. Day-level anchoring beyond the chosen resolution is a later
  refinement and is stated in the design doc.
- The chart offers two measures, Index (anchor = 100) and Sigma, taken from the
  server's mode vocabulary (other modes are not chart modes). Index defaults to a
  LOG axis, with Linear as an option; Sigma is linear.
- Anchor is held as an ISO date. The anchor bar snaps to the chart's data
  periods. Keyboard: arrow keys move one period, Page Up/Down move twelve.
- Internal use only; Yahoo-sourced and third-party-licensed series appear as the
  catalog returns them (the gate is a later sprint).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. If you have no database access in this
environment, say so explicitly and STOP.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- You MAY run unit tests, lint, and the pure-Python golden-vector generator
  synchronously. Do NOT start the dev server, do NOT run the production build,
  and do NOT run the verify script: write it and STOP.
- The only backend change is the additive `stddev` field in Task 2. No DDL, no
  other backend edits, no edits to CLAUDE.md. Do not change existing pages or
  the Grid tab's behavior.
- Never expose a secret; never put org_id or user_id in a URL, body or state.
- No hardcoded label, category, color, vocabulary value, limit or message that
  the server supplies. Page chrome strings and the design-token hexes are fine.
- Chart numbers are floats for plotting only; the Grid tab still never parses
  values. Keep this distinction in the module names (rebase and scale modules
  are the only places that parse series strings).
- Pure logic in plain modules with no React and no DOM; components stay thin.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the mkt04a modules and components named above and report how the page
      holds state (selection, tab, controls), how it fetches, and where the Chart
      tab placeholder is rendered.
  1b. Read the backend series function (services/market_data/read_service.py and
      its router) and the transform functions, and report exactly where stddev is
      already computed for the grid so Task 2 can reuse it.
  1c. Check how verify_mkt03.py asserts the series response shape (strict key
      set, or per-field checks). Report any assertion that adding one field to
      each series object would break.
  1d. Find how the mkt04a tests load JSX components (the jsx loader) so chart
      components can be rendered in tests the same way.

=== TASK 2: THE sd FIELD (backend, additive) ===
  2a. Add `stddev` to each series object returned by GET /market/series: the
      sample standard deviation over all of the series' active observations, the
      same SQL expression the grid already uses, returned as a STRING (quantized
      like other transformed values: 6 decimal places, half-even), or null when
      undefined (fewer than two observations or zero variance).
  2b. Change nothing else in the response or the endpoint's behavior. If Task 1c
      found an assertion in verify_mkt03.py that this breaks, make the smallest
      edit that keeps what it proves (allow the extra key) and record it as a
      [FIND] with before and after; do not weaken any other assertion.
  2c. Document the field in docs/MARKET_DATA_DESIGN_V1.md under the API contract.

=== TASK 3: PURE CHART MODULES ===
In apps/web/lib/market/ (names are suggestions), each with node:test tests:
  - rebase: asOf(points, date), the index and sigma transforms exactly per the
    canonical definitions, floating detection, unavailable reasons
    ('non_positive_anchor', 'zero_variance') and the rate-like warning; the sd
    comes from the server's field, never computed in the browser.
  - scales: the vertical range for log and linear (auto-fit to the visible lines
    with 6% padding, clamped to 1/100..100 for log index, wider for linear index,
    +-6 for sigma), "nice" tick generation (log ticks from a fixed ladder pruned to
    at most eight; linear ticks by step), horizontal ticks every 5 years (finer
    when the span is short), the pixel mapping both ways.
  - hitTest: given pixel coordinates and the plotted series, find the nearest line
    point within 18 pixels (same rule as the mockup) at the nearest period index,
    with the display-scale correction (rendered width / logical width).
  - overlays: cohort band quantiles (25th, 50th, 75th across series with values at
    each period, needing at least three), outlier fences at the latest date
    (25th/75th quartile with a 0.75 * IQR margin and a floor of 4% of the axis
    span), the "big move" dots (|period change| / its own standard deviation
    above a threshold, top three per series), and the 12-month direction arrow.
  - labelLayout: end-of-line labels spaced at least 14 pixels apart, clamped
    inside the plot, preserving order.
  - chartModel: from the selection, series responses, catalog and settings
    (anchor, measure, scale, overlays), produce everything the SVG needs: lines
    (points string, color, width, opacity, dash), ticks, axis title, bands,
    key-date lines, dots, labels, notices, floating/unavailable flags, and the
    summary-table rows. The axis title is assembled from the server's measure
    label plus the anchor and scale ("Index, anchor date = 100 (log scale)" style
    text uses the server's label for the measure).

=== TASK 4: COMPONENTS ===
Replace the Chart tab placeholder with the chart, keeping the Grid tab untouched:
  4a. Controls bar: Measure (the two chart measures, labels from the server),
      Axis (Log, Linear; disabled in Sigma), Resolution (server frequencies,
      default monthly), Overlays (Regimes & events, Cohort band, Highlight
      outliers) as toggles, and the quick buttons "Start of data" and "5 years
      ago". The overlay toggles default on.
  4b. The SVG chart sized to its container with a ResizeObserver (plot area with
      left axis labels, a rotated y-axis title, right-hand end labels, bottom year
      ticks). Lines are hairlines (1px; 2.2px for securities; 1.7px for outliers
      when highlighting is on; 2.8px for the hovered line), dashed when floating,
      faded when highlighting is on and the series sits inside the pack, and the
      hovered line is drawn last with all others dimmed.
  4c. Anchor bar: a draggable vertical bar with a flag reading "Anchor · Mon YYYY"
      (flipped to the left near the right edge), plus a native range input beneath
      the plot (aria-label "Anchor date") bound to the same value so keyboard and
      screen-reader use work. Dragging recomputes lines on animation frames, not
      on every pointer event.
  4d. Hover: a transparent overlay uses hitTest; show a dot, a thin guide line and
      a small card with the series name, the date, the measured value ("Index
      118.4 · +18.4% vs anchor" or "+1.30σ vs anchor") and, for series whose
      default_transform is level, the raw value with the catalog's units.
  4e. Overlays: regime bands and key-date lines from GET /api/market/key-dates
      (fetched once, with the page's fail-closed envelope rule), under the
      "Regimes & events" toggle; the cohort band and median; outlier highlighting
      and big-move dots; a small legend row.
  4f. Notices under the chart, built from the server's warning codes and the
      floating flags (text via the mkt04a vocabText helper, falling back to the
      code text): rate-like series shown as ratios, non-positive anchors, series
      starting after the anchor.
  4g. The "Trends and outliers" table under the chart: series ordered by distance
      from the median at the latest date, columns Series, Group, Since anchor,
      12 mo, Status (Outlier above pack, Outlier below pack, In pack, or "Too few
      series" under five series).
  4h. Data loading: one GET series request for the selected keys at the chosen
      resolution, debounced, newest response wins, a refused request shows the
      server's message, nothing is requested for an empty selection, and an
      envelope-less response fails closed with no chart.

=== TASK 5: REAL PROOF ===
Write proofs into apps/api/scripts/verify_mkt04b.py and the node test suites:
  - Golden equivalence: write a pure-Python generator (reusing the production
    transforms module, no database, no network) that emits
    apps/web/tests/market/golden_rebase.json from canned series covering: anchor
    on an observation, anchor between observations, anchor before the first
    observation (floating), non-positive anchor, a zero-variance series, a
    rate-like series, index and sigma. The JS tests assert the browser's rebase
    matches every golden value to within 5e-7 (the server's 6-decimal rounding).
    You MAY run the generator; commit its output.
  - Scales, ticks, hitTest, overlays, labelLayout: known-answer tests including
    the display-scale correction, the 18-pixel threshold, the pack fences, label
    spacing and ordering, and the log tick ladder pruning.
  - chartModel: floating series dashed; unavailable series excluded with a notice;
    the axis title; the hovered series drawn last; highlighting fades only
    in-pack lines; legend and notices text come from the server vocabularies.
  - Components render (via the jsx loader): the chart renders for a normal
    envelope; an envelope-less series response renders the error state and no
    chart; the anchor input's keyboard stepping moves one period, Page Up/Down
    twelve; the overlay toggles show and hide their elements.
  - Request behavior: one request for a burst of control changes, newest wins,
    keys over the server limit are blocked before sending, the frequency list is
    the server's.
  - No hardcoding, no float parsing in the Grid modules, no client call to the
    FastAPI base URL, no dark mode, gradients, shadows or emoji, all as the mkt04a
    static scans (extend them to the new files).
  - Backend field (Phase B, behind --live): for three real series, the series
    function's stddev equals SQL stddev_samp quantized to 6 decimal places; a
    series with fewer than two points returns null.
  - Build: `npm run lint` clean on files this sprint touched and the production
    build exits 0 (the verify script runs both; this is their first execution).
  - Scope: changes confined to apps/web, the series-function field and its
    docs, verify_mkt04b.py, the one verify_mkt03.py edit if Task 1c required it,
    docs, and the sprint files.

=== TASK 6: DOCS ===
Add an mkt04b entry to docs/PROJECT_STATUS.md; append `UPDATE <today>` under the
mkt04a entry noting the chart is built and mkt04c (key dates, saved views,
correlations) remains. Add a "Chart" section to docs/MARKET_DATA_DESIGN_V1.md:
the client-side rebasing decision and why sd comes from the server, the golden-
vector check, the default monthly resolution and what it means for day-precision
key dates, the two chart measures, the log default, and the hit-test and anchor
behaviors. Do not edit CLAUDE.md.

=== VERIFICATION: apps/api/scripts/verify_mkt04b.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. It may shell out to npm and node and read the database in Phase B.
Hydrate secrets internally using the same helper the earlier verify scripts use
(Phase B only). Output lines begin with [PASS], [FAIL], [FIND], or [SKIP]; the
last line is `TOTAL: <passed> passed, <failed> failed`. Exit non-zero on any
[FAIL]. Every assertion states why it matters.

When finished, STOP and print exactly these operator commands, in this order:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04b.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04a.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
