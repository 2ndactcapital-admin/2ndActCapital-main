MKT04A — MARKET INDICATORS PAGE: NEXT.JS ROUTES, PAGE SHELL, SELECTION AND GRID. 6 tasks + verification.

The market data backend is finished and on main: registry, nightly refresh, and
a read and personal-data API (catalog, series, grid, correlations, key dates,
custom dates, saved views). This sprint starts the front end with a thin,
complete vertical slice: the Next.js routes in front of the API, a new page,
the selection panel, and the GRID view. It proves the whole path (host-aware
login, server-side forward, permission envelope, server-driven labels) before
the chart is built in mkt04b. The chart, the key-date and saved-view controls
and the correlations panel come in mkt04b and mkt04c.

A clickable mockup, "Market Indicators — Trend Explorer", defines the intended
look and behavior. Its synthetic data and its single-file code are NOT to be
ported wholesale; follow the design intent and this prompt.

OUT OF SCOPE (do not build): the line chart, the anchor bar, hover tooltips,
regime bands, key-date or "My dates" dropdowns, saved-view controls, the
correlations panel; per-tenant gating of Yahoo or third-party-licensed series
(a separate sprint that must run before any external customer sees this page);
structured-note price history; any backend change.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Backend routes live under /api/v1 on the FastAPI service (the web app
  reaches them only server-side):
    GET  /market/catalog
    GET  /market/series?keys=a,b&from=&to=&frequency=
    POST /market/grid          {keys, anchor, end?, mode, frequency}
    POST /market/correlations  {focus_key, keys, anchor, end?, lag_months?, min_periods?}
    GET  /market/key-dates
    POST /market/key-dates/custom   {name, event_date}
    DELETE /market/key-dates/custom/{id}
    GET  /market/views
    POST /market/views         {name, config}
    PUT  /market/views/{id}    {name?, config?}
    DELETE /market/views/{id}
  All are gated by one session-only function (any valid login). org and user
  come ONLY from the session; the API rejects a body containing org_id or
  user_id with 422. Errors never echo the request body.
- Catalog shape: categories [{key, label, color, sort_order}]; indicators
  [{series_key, name, category, region, frequency, units, seasonal_adjustment,
  default_transform, color, source_provider, license_class, cost_tier,
  first_observation_date, last_observation_date, security_global_id,
  ingest_status}]; securities [{id, name, short_name, security_type,
  price_source, series_key, selectable, unselectable_reason}] (13 index and 54
  structured notes; today only 8 index securities are selectable and every note
  is unselectable with reason 'no_price_history'); permissions; vocabularies
  (transforms, frequencies, modes, license classes and source providers
  present). Colors come from the server.
- Grid response: rows newest-first, each {date, cells: {series_key: "value"}};
  per-series metadata {floating, warnings, unavailable_reason,
  last_observation_date}. Values are STRINGS (exact decimal text). Grid modes:
  index, sigma, level, default. Frequencies: weekly, monthly, quarterly (daily
  only for windows up to 400 days). At most 40 keys and 2,000 rows.
- Transform definitions are canonical in docs/MARKET_DATA_DESIGN_V1.md. The
  client must never re-derive grid values; the grid shows what the server
  returns. (mkt04b will rebase client-side for the chart and will be proven
  against server-generated golden vectors.)
- Platform rules (CLAUDE.md): Rule 1 all labels, vocabularies, categories and
  colors come from the server response, never hardcoded in the UI; Rule 5
  server components use the host-aware getAuthClientForHost(host), client
  components call Next.js API routes only and never FastAPI directly, and the
  forward adds no org_id; permission-gated UI renders its controls ONLY inside
  an envelope check with NO truthy fallback, so a lost envelope fails CLOSED.
- Design: light theme only, never dark. Tokens: Navy #1B2B4B, Gold #C5A880,
  Gold Light #E8D5A3, app background #FAF9F6, sidebar #F5F1EB, cards white with
  a 1px #ece8dd hairline and 6px radius, text #0F172A / #334155 / #64748B,
  border #E2E8F0, error #9B2335, success #2D6A4F. Headings in Spectral, body in
  Hanken Grotesk, base size 17px. Quiet, precise copy; no emoji; no gradients; no
  heavy shadows.
- Baselines for the Python verify scripts (do NOT chain them): mkt01 57, mkt02
  52, mkt03 68, mkt03b 114, all 0 failed.

DECISIONS ALREADY MADE, do not relitigate:
- The slice is internal-use only. Yahoo-sourced and third-party-licensed series
  are shown as the catalog returns them; the per-tenant gate is a later sprint.
  The page must display each series' source in the selection panel (provider
  name from the catalog) so the data's origin is never hidden.
- Selection state lives in the browser for now (React state in the page); it is
  structured exactly as the saved-view config's selection list (kind + key) so
  mkt04c can persist it without reshaping.
- Pure logic goes in plain modules with no React imports and no DOM access
  (grouping, selection operations, formatting, request building) so it can be
  tested with the repo's test runner or Node's built-in runner.
- The page is a thin client over the server: no client-side data transforms in
  this sprint beyond display formatting.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. If you have no database access in this
environment, say so explicitly and STOP.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- You MAY run unit tests and lint synchronously while developing. Do NOT start
  the dev server, do NOT run the production build, and do NOT run the verify
  script. Write the verify script and STOP; the operator runs the build and the
  verify.
- No backend or database changes. No DDL. Do not edit CLAUDE.md. Do not touch
  any existing page's behavior except to add the one navigation entry.
- Never expose a secret. Never put org_id or user_id in a URL, a body, or client
  state. Route handlers forward the caller's JSON body unchanged apart from
  validating that it parses; they never add identity fields.
- No new dependency unless Task 1 proves none of the repo's existing ones can do
  the job; if you add one, record the reason in the design doc.
- Do not hardcode any label, category, color, vocabulary value, limit or message
  that the server supplies. Display strings that are purely page chrome (the
  page title, button captions) are allowed.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read apps/web: the framework version, App Router layout, whether the code
      is JavaScript or TypeScript, the styling approach (Tailwind configuration,
      CSS variables, a design-token file), the fonts already loaded, and the
      test runner and scripts in package.json (build, lint, test, typecheck).
  1b. Find how the app does host-aware authentication: proxy.js and
      getAuthClientForHost(host). Find how an EXISTING Next.js route handler
      forwards to FastAPI server-side (token handling, base URL variable name,
      error propagation). Mirror that pattern exactly and report the file you
      are copying from.
  1c. Find how an existing permission-gated page renders its envelope (rows,
      permissions, vocabularies) including the fail-closed handling, and
      whether there is a shared data-grid component (DataGrid.jsx or similar)
      and a shared page layout. Report whether the grid component can render
      string cells with a server-defined column list and sticky headers; reuse
      it if it can, otherwise build a small table in the same visual style and
      say why.
  1d. Find how navigation is defined (config-driven or hardcoded) and what
      permission or condition shows an entry. The new entry must be visible to
      any signed-in user, matching the backend's session-only gate; report the
      mechanism.
  1e. Find how earlier UI sprints verified their work (how a component's render
      logic is fed a real envelope in a test; how static checks were written)
      and mirror it. Report what the existing verify scripts shell out to.
  1f. Check whether the repo already has a date-input convention (a shared date
      picker) and whether apps/web has any existing /market route that would
      collide.

=== TASK 2: NEXT.JS ROUTES ===
Create route handlers for all eleven backend routes listed in CONFIRMED REAL
FACTS under a single path prefix (suggested /api/market/...), using the pattern
from 1b:
  - Host-aware authentication on every handler. No session -> the same
    unauthenticated response existing handlers use. The caller's identity is
    carried by the existing server-side forward, never by a field.
  - The client query string for GET series is passed through; bodies for POST
    and PUT are forwarded unchanged after confirming they parse as JSON.
  - Status codes and the backend's error messages are passed back to the client
    unchanged. A backend 401 or 403 is not rewritten into a 200.
  - No caching of any response (these include per-user data). No logging of
    bodies or tokens.
  - A small shared helper does the forward so the eleven handlers stay thin.

=== TASK 3: PAGE SHELL AND NAVIGATION ===
  3a. Add the page at the route 1f says is free (suggested /market), titled
      "Market indicators", in the app's standard layout, with the one
      navigation entry from 1d.
  3b. On load the page fetches the catalog through the Next.js route. While it
      loads show the app's standard loading state; on error show the standard
      error state with the server's message. If the response has no
      permissions envelope, show an error state and NO controls (fail closed).
  3c. The page has two tabs, Chart and Grid. The Chart tab shows a short quiet
      placeholder ("The chart arrives in the next release.") and nothing else;
      the Grid tab is built in Task 5. The tab switch is the page's only
      navigation state.

=== TASK 4: SELECTION PANEL ===
A left column, as in the mockup:
  - Category chips, each a BULK selector: clicking selects or clears every
    selectable indicator in that category; a chip shows an "all" state, a
    "some" state, or none. Chip color comes from the catalog.
  - Indicators, grouped under their category label, each row a checkbox with
    the series' color swatch, its name, and a small tag showing its source
    provider (as the catalog names it) and, when license_class is not
    public_domain, a quiet marker whose text comes from the server's
    vocabulary.
  - Securities: the catalog's index securities. Selectable ones are normal rows
    (selection by the linked series_key, shown with the security's name);
    unselectable ones are listed disabled with the server's reason text.
  - Structured notes: same rule, all disabled today. Show the count and a
    collapsed list so 54 rows do not dominate the panel.
  - A "Clear all" action. A selection counter with the 40-series limit from the
    server's vocabularies or limits; selecting past 40 is blocked with a quiet
    inline message.
  - Selection is held as [{kind, key}] where kind is 'indicator' or 'security'
    and key is the series_key (indicator) or the securities_global id
    (security), matching the saved-view config exactly.
  - All grouping, counting and toggling lives in a pure module.

=== TASK 5: GRID VIEW ===
  5a. Controls above the grid: Measure (mode) and Frequency, both option lists
      taken from the server's vocabularies; Anchor as a date input with the quick
      buttons "Start of data" (the earliest first_observation_date among the
      selected series, from the catalog) and "5 years ago"; an optional End
      date. Defaults: mode 'default', frequency 'monthly', anchor 5 years ago.
  5b. Selecting series or changing a control requests POST /market/grid through
      the Next.js route (debounced so rapid clicks send one request) and renders
      the response. No request is sent for an empty selection. A request that
      is refused (422) shows the server's message inline. Only the newest
      response is rendered if requests overlap.
  5c. Table: first column the date (newest first, as returned), then one column
      per selected series with the series' color swatch and name in the header,
      sticky header, horizontal scroll inside the table container so the page
      never scrolls sideways, right-aligned tabular numerals. Cells are the
      server's strings shown as returned (insert thousands separators for
      display only if that can be done without parsing to a float; otherwise
      show as returned). Null cells show an em dash.
  5d. Per-series flags from the grid metadata: a series that is floating gets a
      dashed header underline and a quiet note "starts after the anchor"; a
      warning shows as an icon-free inline note whose text is the server's
      warning code mapped through a server-supplied vocabulary if one exists,
      otherwise the code text itself; an unavailable_reason shows in place of
      the column's values with the reason text.
  5e. Empty and edge states: nothing selected ("Select indicators to see their
      values."), no rows, and a single selected series all render cleanly.

=== TASK 6: REAL PROOF, DOCS ===
Write the proofs into apps/api/scripts/verify_mkt04a.py (see below) and update
docs: add an mkt04a entry to docs/PROJECT_STATUS.md; append `UPDATE <today>`
under the mkt03b entry saying the front end has started with mkt04a, followed by
mkt04b (chart) and mkt04c (key dates, saved views, correlations); add a
"Front end" section to docs/MARKET_DATA_DESIGN_V1.md covering the route prefix,
the fail-closed envelope handling, the selection model (kind + key), what is
rendered from the server versus chrome, and the open launch blocker that
per-tenant gating of Yahoo and third-party-licensed series is still unbuilt.

PROOFS the verify script must contain (static checks read the source tree; unit
tests run the repo's runner synchronously; assertions state why they matter):
  - Build: `npm run lint` (and the typecheck if the repo has one) pass, and the
    production build passes. The verify script runs these, so the operator's run
    is the first time the build executes.
  - Route handlers: each of the eleven exists, authenticates with the host-aware
    helper, forwards to the correct backend path and method, passes status and
    error text through unchanged, sets no-store caching, and never reads or adds
    org_id or user_id (static scan plus handler tests with a faked backend and a
    faked session). With no session each returns the unauthenticated response and
    never calls the backend.
  - Fail closed: feeding the page's render logic an envelope-less catalog
    response yields the error state and renders NO selection or grid controls;
    feeding it a normal envelope renders them.
  - No hardcoding: a static scan finds no category, color hex from the server's
    palette, vocabulary value or limit literal in the new UI code (the design
    token hexes in the style layer are allowed), and no client component imports
    or calls the FastAPI base URL directly.
  - Selection logic (pure module): bulk category toggle selects only selectable
    indicators and reports all/some/none correctly; toggling is reversible; the
    40-series limit blocks the 41st with the message; unselectable securities
    and notes can never enter the selection; the selection list is exactly
    [{kind, key}] with no extra fields.
  - Grid request building (pure module): the body contains exactly the fields
    the API accepts and nothing else; an empty selection builds no request;
    debounce emits one request for a rapid burst; an older response arriving
    after a newer one is discarded.
  - Grid rendering: null cells render an em dash; floating and unavailable
    series render their notes; string values are rendered without being parsed to
    floats (a value with six decimals round-trips visibly unchanged).
  - Navigation: the new entry is present and visible to a signed-in user under
    the mechanism found in 1d.
  - Nothing else changed: git shows modifications only in apps/web, apps/api/
    scripts/verify_mkt04a.py, docs, and the sprint files (a static check of the
    diff against main).

=== VERIFICATION: apps/api/scripts/verify_mkt04a.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. It may shell out to npm/node for lint, tests and the build (the
operator's run is the first execution). Hydrate secrets internally only if a
check needs them (none is expected). Output lines begin with [PASS], [FAIL],
[FIND], or [SKIP]; the last line is `TOTAL: <passed> passed, <failed> failed`.
Exit non-zero on any [FAIL]. Every assertion states why it matters.

When finished, STOP and print exactly these operator commands, in this order,
then this manual checklist:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04a.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  cd apps/web && doppler run -- npm run dev
Manual checklist (open the page signed in): the page loads with the catalog;
category chips select and clear groups; the Grid tab shows values after you pick
a few indicators; changing Measure, Frequency and Anchor changes the grid; the
notes appear disabled with their reason; resizing the window never scrolls the
page sideways; signing out and visiting the page sends you to login.
