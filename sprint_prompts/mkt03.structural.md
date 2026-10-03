MKT03 — MARKET DATA READ API: CATALOG, SERIES, GRID, CORRELATIONS. 6 tasks + verification.

mkt01 and mkt02 (merged, verified) built the market_data registry, FRED and
Yahoo adapters, a full backfill, and a nightly refresh. This sprint builds the
READ side the chart and grid will sit on: a catalog endpoint, a series endpoint,
a grid endpoint, and a correlations endpoint, all over data that already
exists. It writes no market data and adds no tables.

OUT OF SCOPE (later sprints, do not build): any UI or Next.js route (mkt04);
key dates and saved views (mkt03b; the key-dates design is in
docs/market_data/KEY_DATES_SPEC_V1.md if it has been added to the repo, but
nothing from it is built here); securities and structured-note price history;
correlation matrices and rolling correlation.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Data state: 75 registry series, 63 'active' (57 fred, 6 yahoo), 12 not
  active (6 'deferred', 6 'deferred_paid'). 313,156 active observations. The
  last two nightly batches each succeeded on all 63 series with 0 revisions.
  Baselines from the last runs: `verify_mkt01.py --live` = 57 passed, 0
  failed; `verify_mkt02.py --live` = 47 passed, 0 failed. Do NOT re-run or
  chain them inside your verify script; the operator re-runs them standalone.
- Part 1 is already applied and verified live: unique partial index
  uq_indicator_series_security on market_data.indicator_series
  (security_global_id) where it is not null, and three series linked to index
  securities: fred.sp500 -> "S&P 500 Index" (f15ba4cd-03ba-41b9-aa45-859bf80b2607),
  fred.nasdaq100 -> "Nasdaq-100 Index" (858a0465-2c59-43dc-b7b0-7c9f4c98f448),
  yahoo.rut -> "Russell 2000 Index" (69265688-90b2-4dfe-b3ac-9d0efb36140f).
  The other 10 index securities (Dow Jones Industrial Average, EURO STOXX 50,
  FTSE 100, MSCI EAFE, Nasdaq-100 Equal Weighted, Nasdaq-100 Technology Sector,
  S&P 500 Futures Excess Return, S&P/ASX 200, Swiss Market Index, TOPIX) have
  NO price source and stay unlinked. The 54 structured notes have none either.
  portfolio.securities_global has 67 rows and portfolio.securities_global_prices
  has 0. This sprint must not write to either table.
- market_data.indicator_series columns: id, series_key (unique), name, category,
  region, frequency (daily, weekly, monthly, quarterly, semiannual,
  per_meeting, irregular), units, seasonal_adjustment, best_view,
  default_transform (rebase_100, level, yoy_pct, mom_pct), cost_tier,
  cost_note, license_class (public_domain, third_party_licensed, unreviewed),
  source_provider, source_code, source_url, notes, sort_order,
  security_global_id, ingest_status (pending, active, invalid_code, paused,
  deferred, deferred_paid), last_validated_at, last_observation_date,
  last_error, created_at, updated_at. market_data.indicator_observations:
  id, series_id, obs_date, value numeric, valid_from, valid_to, system_from,
  system_to. Active-row predicate: valid_to IS NULL AND system_to IS NULL.
- RLS: indicator_series and indicator_observations are readable by every role
  (global read policy). indicator_ingest_runs is platform-only and is NOT
  exposed by this API. app_service has rolbypassrls = false. Read endpoints
  must run on the normal request connection and must NOT use platform_scope().
- Platform conventions (CLAUDE.md): Rule 1 labels and colors come from the
  server's response envelope, never the frontend; Rule 2 statement_cache_size=0;
  Rule 6 org_id/user come only from the verified session, never a body or
  path; Pydantic models use extra='forbid'; every permission-gated response
  carries a permissions envelope and vocabularies; client components never call
  FastAPI directly.
- The clickable mockup "Market Indicators — Trend Explorer" defines the
  intended UX. The TRANSFORM DEFINITIONS below are canonical where the mockup's
  synthetic code differs.

DECISIONS ALREADY MADE, do not relitigate:
- The chart rebases on the CLIENT while the user drags the anchor, from raw
  series data. So the series endpoint returns raw (optionally downsampled)
  observations. The grid and correlations endpoints are computed SERVER-side so
  there is one definition of each transform.
- Values cross the API as STRINGS (exact Decimal text). Transforms are
  computed in Decimal and returned quantized to 6 decimal places (half-even).
  Correlation coefficients are the one exception: computed in float64, rounded
  to 4 decimal places, returned as strings, and never stored.
- Unlinked and note securities appear in the catalog but are not selectable.
- Category colors are a server-side palette keyed by category (the API response
  is the source of truth, which satisfies Rule 1). Moving them to the config
  table later is a separate, optional change.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. ONE exception: if you have no database
access in this environment, say so explicitly and STOP — never report
completion from an environment that cannot prove anything.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- Decimal-only arithmetic for transforms; Decimal(str) at every parse boundary;
  never float except for the correlation coefficient as stated above.
- statement_cache_size=0 on EVERY asyncpg connection and pool.
- Schema-qualify everything (market_data.*, portfolio.*).
- No DDL, no writes to any market_data table, no writes to portfolio.*, no
  platform_scope() in read paths, no org_id anywhere in a request.
- Do not create Next.js routes or any UI. Do not edit CLAUDE.md. Do not edit
  mkt01/mkt02 modules except to import from them.
- Do NOT run the verify script and make no live external calls from this
  session. Write the verify script and STOP; the operator runs it. Read-only
  database discovery is fine.
- NEVER print or log a secret. Error text returned to callers must not echo the
  request body.
- If live DDL or data differs from CONFIRMED REAL FACTS, report [FIND] and STOP.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Query the live database and confirm CONFIRMED REAL FACTS: active series
      count and providers, active observation count (a larger count is fine if
      the nightly has run since; report it), the unique index, the three
      links, and that the 10 other index securities are unlinked.
  1b. Find how existing FastAPI routers are organized and registered, how a
      route obtains the verified org and user from the session
      (get_org_id(request) and its sibling for the user), and how an existing
      read endpoint publishes its permissions envelope and vocabularies. Mirror
      the closest existing pattern exactly.
  1c. Find the permission model (public.permissions, permission_sets,
      profile_permissions, role_permissions, user_permission_sets) and the one
      shared helper that checks a permission, including the super-admin bypass
      that is checked first. Decide which EXISTING permission best fits "may
      read market data". If one clearly fits, use it. If none clearly fits, do
      NOT invent one: gate on a valid authenticated session only, publish
      read_permission = null in the envelope, and record a [FIND] naming the
      candidates. The gate must live in ONE function so adding a permission
      later is a one-line change.
  1d. Find how existing verify scripts prove API-level behavior (permission
      gates, 403s, request validation): in-process ASGI client, dependency
      overrides, or direct service calls. Use the same mechanism so the proofs
      below run the same way.
  1e. Determine which securities_global rows the catalog should list: active
      rows only (valid_to IS NULL AND system_to IS NULL) with merged_into_id
      IS NULL. Report the counts by security_type (expected 13 index and 54
      structured_note).
  1f. Report the number of active observations per series for the 63 active
      series and the largest, so the point-count cap in Task 3 is set from
      real numbers.

=== TASK 2: SHARED CORE AND CATALOG ENDPOINT ===
New package modules under services/market_data/ (names are suggestions; follow
the repo's layout): repository (read queries), transforms (pure functions),
resample (pure functions), query-level service functions, and the router.

  2a. Repository: read the active observations for a series list and window
      using the request connection. One query per call over series_id = ANY($1),
      ordered by series_id, obs_date. Also expose per-series first and last
      observation dates, and stddev_samp(value) over all active observations
      (computed in SQL, numeric).
  2b. Pure functions, no database, fully unit-testable, implementing the
      TRANSFORM DEFINITIONS block below exactly: as_of, index, sigma, level,
      yoy, mom, resample, coarser_frequency.
  2c. GET /market/catalog returning:
        - categories: [{key, label, color, sort_order}] from the distinct
          categories of ACTIVE series, in a stable order.
        - indicators: one object per active series: series_key, name, category,
          region, frequency, units, seasonal_adjustment, default_transform,
          color, source_provider, license_class, cost_tier,
          first_observation_date, last_observation_date, security_global_id
          (or null), ingest_status.
        - securities: every active, non-merged securities_global row:
          id, name, short_name, security_type, price_source ('indicator_series'
          when a series links to it, otherwise 'none'), series_key (or null),
          selectable (true only when linked), unselectable_reason
          ('no_price_history' otherwise).
        - permissions and vocabularies per the platform pattern (vocabularies:
          transforms, frequencies, modes, and the license_class and
          source_provider values present).
  2d. Colors: a server-side palette keyed by category. Use these base colors:
      Equities #2B5F9E, Rates & credit #C8641E, Growth & labor #3F8A5F,
      Inflation #9B5A8A, Housing #6F5E4E, Commodities #17707A, FX #4A5568;
      any other category gets a neutral #64748B. The category color is the base.
      Each series in a category gets a deterministic shade: convert the base to
      HSL, keep hue and saturation, and spread lightness evenly from (base
      lightness minus 0.10) to (base lightness plus 0.18) across the category's
      series in sort_order, clamped to 0.28 through 0.68, returned as #RRGGBB.
      A category with one series uses the base color. Index securities use a
      navy ramp from #1B2B4B and structured notes a gold ramp from #C5A880,
      same rule. The colors must be distinct within a category and identical on
      every call.

=== TASK 3: SERIES AND GRID ENDPOINTS ===
  3a. GET /market/series?keys=<comma list>&from=<date>&to=<date>&frequency=<f>
      Returns, per key: series_key, native frequency, returned frequency,
      point_count, first_observation_date, last_observation_date, and points as
      [date, value] pairs with string values, ascending. frequency is one of
      native (default), daily, weekly, monthly, quarterly and means "at most
      this fine"; see RESAMPLING. Keys must be active registry series; a
      security selection is accepted only through its linked series_key.
  3b. POST /market/grid with body {keys, anchor, end (optional), mode,
      frequency}. mode is one of index, sigma, level, default. Returns rows at
      the period ends of the requested frequency (monthly or quarterly or
      weekly; daily allowed only when the window is at most 400 days) between
      anchor and end, each cell the transformed as-of value of one series, plus
      per-series metadata: floating (true when the anchor precedes the series'
      first observation), warnings, unavailable_reason when the transform cannot
      be computed, and last_observation_date. Rows newest-first.
  3c. Request validation: models use extra='forbid'; org_id or user fields
      anywhere in a body are rejected (422) and nothing is echoed back. Limits:
      at most 40 keys; at most the point cap derived in 1f for series; at most
      2,000 grid rows; dates must parse; anchor must not be after end; unknown
      or non-active keys answer 422 listing only the offending keys; an empty
      keys list answers 422. Errors never echo the request body.
  3d. Both endpoints are read-only on the request connection (no
      platform_scope) and gated by the single gate function from 1c.

=== TASK 4: CORRELATIONS ENDPOINT ===
  4a. POST /market/correlations with body {focus_key, keys, anchor, end
      (optional), lag_months (integer, -24 to 24, default 0), min_periods
      (integer 12 to 120, default 24)}. Returns, per candidate key (the focus
      itself is excluded, even if listed in keys): series_key, r (string or
      null), n (number of overlapping periods used), frequency (the coarser
      frequency used for this pair), overlap_from, overlap_to, and
      unavailable_reason when r is null. Sorted by absolute r descending;
      nulls last. Include the window actually used and the effective lag.
  4b. Method, exactly as defined in CORRELATION METHOD below. Pearson r over
      changes, never levels.
  4c. Same validation rules, limits (at most 40 candidate keys), gating, and
      read-only behavior as Task 3.

=== TASK 5: REAL PROOF (written into the verify script, NOT executed by you) ===
Phase A is self-contained and always runs. It creates FIXTURE series whose
series_key starts with 'verify.mkt03.', inserts their observations inside
platform_scope() for test setup only, and exercises the production service
functions and routes. Fixture values are chosen so every expected number is
known exactly in advance. Teardown rules are at the end.
  - Catalog: indicators equals the active series count read at runtime; every
    entry has a valid #RRGGBB color; colors are distinct within each category
    and identical across two calls; securities lists the count from 1e; the
    three linked index securities are selectable with their series_key; every
    other security is unselectable with reason 'no_price_history'; the
    permissions envelope and vocabularies are present.
  - as-of and anchor: an anchor on a day with no observation uses the last
    observation on or before it; an anchor before a series' first observation
    sets floating = true and measures from the first observation.
  - Index: values match 100 * v / v0 exactly (Decimal equality at 6 dp); a
    non-positive v0 gives unavailable_reason 'non_positive_anchor'; a
    default_transform = level series indexed in 'index' mode carries the
    warning 'rate_like_series_indexed'.
  - Sigma: matches (v - v0) / stddev_samp computed independently in SQL on the
    fixture; a zero-variance fixture gives 'zero_variance'.
  - yoy and mom in 'default' mode match hand-computed values and are null when
    the earlier point does not exist.
  - Resampling: monthly takes the last observation in each calendar month and
    reports its true obs_date; a quarterly fixture requested monthly is NOT
    upsampled; weekly uses ISO weeks.
  - Grid: row dates are period ends; cells before a series' first observation
    are null; carry-forward uses the as-of rule; rows are newest-first; the row
    cap and key cap are enforced.
  - Correlations: fixture b with changes exactly 2x fixture a's gives r =
    "1.0000"; fixture c with changes exactly the negative of a's gives
    "-1.0000"; a deterministic uncorrelated-by-construction pair matches an
    independently computed value within 0.0001; fewer than min_periods
    overlapping periods gives null with reason 'insufficient_overlap'; a
    fixture shifted by k months gives r = "1.0000" at the matching lag and a
    lower value at lag 0; a daily-versus-quarterly pair reports frequency
    'quarterly'; a series with non-positive values uses differences instead of
    log changes; the focus is excluded from its own results; ordering is by
    absolute r with nulls last.
  - Exactness: '4.123456789' round-trips through the series endpoint as the
    identical string; no JSON number appears where a string is specified.
  - Permission gate on the IDENTICAL request, both ways: refused for the wrong
    caller, admitted for the right one, using whichever gate Task 1c settled on
    (a caller without the permission, or an unauthenticated caller when the
    gate is session-only).
  - Validation: org_id in a body is rejected with 422 and not echoed; unknown
    key, non-active key (use a real 'deferred' series), empty keys, 41 keys,
    anchor after end, and a malformed date are all rejected with the specified
    status and no body echo.
  - Least privilege: the read modules never import platform_scope (static
    check), and the endpoints succeed on a connection with rolbypassrls = false
    and no super-admin context (asserted explicitly).
  - Existing data untouched: row counts of market_data.indicator_series,
    indicator_observations, indicator_ingest_runs, portfolio.securities_global,
    portfolio.securities_global_prices, and public.fx_rates are captured at
    start and equal at end, excluding fixture rows.
  - Teardown deletes fixtures by tag in strict FK order (observations, then
    series), confirmed against information_schema, and independently re-reads
    every table to confirm non-fixture counts are exactly as before. Fail
    loudly with SystemExit(2) if any fixture row remains. Never TRUNCATE.
Phase B runs ONLY with `--live` and needs no operator pre-steps.
  - Catalog active count equals the live active count; the three links are
    present.
  - fred.sp500 with anchor 2000-03-10, mode index: the value at the anchor is
    exactly 100, and the value at the latest date equals 100 * latest / anchor
    computed independently in SQL.
  - One real series in sigma mode matches SQL stddev_samp.
  - Correlations with focus fred.sp500 against at least five real indicators
    return r in [-1, 1] with n >= 24, or an explicit unavailable_reason.
  - [FIND], not failure: report the sp500 vs nasdaq100 correlation (expected
    high); the elapsed time of a 12-series full-history series request, a
    grid request, and a correlations request; and the largest response size.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md with an mkt03 entry. Under the existing mkt01 and
mkt02 entries append `UPDATE 2026-10-03` noting the remaining plan: mkt03 = this
read API; mkt03b = key dates and saved views; mkt04 = chart and grid UI.
Update docs/MARKET_DATA_DESIGN_V1.md with: the API contract (endpoints, bodies,
limits), the TRANSFORM DEFINITIONS and CORRELATION METHOD below verbatim, the
decision to rebase on the client and compute grid and correlations on the
server, the string-value convention, the catalog's exposure of source_provider
and license_class (so mkt04 or later can gate Yahoo per tenant; the Yahoo
launch blocker stands), and these caveats: correlations use period dates not
publication dates, so lead/lag results are indicative; correlating levels is
spurious, which is why this endpoint only uses changes; correlations are
unstable across regimes. Add a "Next candidates" note that the 10 unlinked
index securities (the underlyings of the structured notes) could be added as
registry series from a free source, and that structured notes need a price
source before the overlay can plot them. Do not edit CLAUDE.md; list any lines
you think it should gain at the end of PROJECT_STATUS.

=== TRANSFORM DEFINITIONS (canonical) ===
- Observations of a series: its active rows, ascending by obs_date.
- as_of(series, date): the value of the last observation with obs_date <= date;
  none if date is before the first observation.
- Anchor value v0 = as_of(series, anchor). If none, v0 = the first observation
  and the series is floating = true.
- index: 100 * v / v0. Requires v0 > 0, otherwise unavailable_reason
  'non_positive_anchor'. A series whose default_transform is 'level' still
  indexes when v0 > 0 but carries the warning 'rate_like_series_indexed'.
- sigma: (v - v0) / sd, where sd = stddev_samp over ALL active observations of
  the series (full history, independent of the anchor and window). sd null or 0
  gives 'zero_variance'.
- level: v unchanged.
- default: apply each series' own default_transform: rebase_100 -> index;
  level -> level; yoy_pct -> 100 * (v / v12 - 1) where v12 = as_of(series, date
  minus 12 months), null when none; mom_pct -> same with 1 month.
- All transformed values are Decimal, quantized to 6 decimal places with
  ROUND_HALF_EVEN, returned as strings.
- RESAMPLING: frequency means "at most this fine". Each returned point is the
  LAST observation in its calendar period (ISO week, month, quarter), reported
  with its actual obs_date. A series with a coarser native frequency is
  returned natively and never upsampled or interpolated. Frequency order, fine
  to coarse: daily, weekly, monthly, quarterly. For this purpose treat
  per_meeting and irregular as monthly; semiannual is unsupported for
  correlations ('unsupported_frequency').

=== CORRELATION METHOD (canonical) ===
For the focus F and each candidate C:
  1. Pair frequency = the COARSER of the two series' effective frequencies.
  2. Reduce both to that frequency (last observation per period), then keep the
     window [anchor, end].
  3. Apply the lag: shift C by lag_months so that a positive lag means C leads
     F (C's value at period t is paired with F's value at period t + lag).
  4. Changes: for each series, if all of its values in the window are > 0 use
     the log difference ln(v_t / v_{t-1}); otherwise use the simple difference
     v_t - v_{t-1}.
  5. Use only periods where both changes exist. n = their count. If
     n < min_periods, r = null with reason 'insufficient_overlap'.
  6. r = Pearson correlation of the paired changes, computed in float64,
     rounded to 4 decimal places, returned as a string. Zero variance in either
     change series gives null with reason 'zero_variance'.

=== VERIFICATION: apps/api/scripts/verify_mkt03.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. Hydrate secrets internally using the same helper the mkt01 and mkt02
verify scripts use. Output lines begin with [PASS], [FAIL], [FIND], or [SKIP];
the last line is `TOTAL: <passed> passed, <failed> failed`. Exit non-zero on
any [FAIL]. Every assertion states why it matters, not just what it checked.
One assertion per bullet in Task 5, Phase A always, Phase B behind --live.

When finished, STOP and print exactly these operator commands:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
