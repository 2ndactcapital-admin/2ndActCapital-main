# Market Data — Design V1

Status: mkt01 built (registry, FRED adapter, historical backfill); verified
live, 57 passed, 0 failed. mkt02 built (adapter registry, Yahoo adapter,
nightly orchestrator, staleness report, Render cron entrypoint); verified
live, 47 passed, 0 failed. mkt03 built (the read API: catalog, series, grid,
correlations); verified live, 68 passed, 0 failed. mkt03b built (key dates,
regimes, personal dates, saved views: API, seeds, loader); its verify is
written but not yet run. Next: mkt04 (the UI).

UPDATE 2026-10-03 (mkt02): decisions 8–13 and the "Launch blockers" section
below are new. The Roadmap is rewritten: the planned "mkt02 = nightly cron +
non-FRED adapters" scope was split.

UPDATE 2026-10-03 (mkt03): decisions 14–18, "API contract", "Transform
definitions", "Correlation method" and "Caveats" are new. Launch blocker 2
is met at the API level. The Roadmap is rewritten again: mkt03 = this read
API; mkt03b = key dates and saved views; mkt04 = chart and grid UI.

UPDATE 2026-10-07 (mkt03b): decisions 20–24 and the section "Key dates,
regimes and saved views (mkt03b)" are new. `docs/market_data/KEY_DATES_SPEC_V1.md`
is the ORIGINAL spec for this work and is superseded wherever that section
says so (notably its §3.4: there is no `visibility` column and no current-user
RLS setting). [FIND] at mkt03b discovery, that spec file is NOT in this
repository; the section below is the record of what was built.

## What this is

Platform-wide history for market and macro indicators: S&P 500, Treasury
yields, payrolls, CPI, FX, oil, housing, and similar. It feeds a later
base-100 trend chart with a security overlay, and a grid view. The registry
of 75 series comes from a reviewed seed file,
`docs/market_data/market_indicator_registry_v1.json`.

## Decisions

### 1. Separate from security pricing, platform-global, no org_id

- The data lives in schema `market_data`, in three tables:
  `indicator_series` (the registry), `indicator_observations` (the values),
  and `indicator_ingest_runs` (the audit log).
- Indicator values are **never** written to
  `portfolio.securities_global_prices`. That table holds prices of holdable
  instruments. Most indicators can't be held: nobody owns CPI or payrolls.
  Mixing the two would let a macro series leak into position valuation.
- This is reference data, so it has no `org_id`. The RLS shape matches
  `portfolio.securities_global`:
  - `indicator_series` and `indicator_observations` are readable by everyone
    (`USING (true)`). Writes require
    `NULLIF(current_setting('app.is_super_admin', true), '') = 'true'`.
  - `indicator_ingest_runs` is super-admin only for every command, because it
    holds error text about platform operations.
- All writes run inside `services.database.platform_scope(conn)`. That sets
  `SET LOCAL` once per transaction. It is never a session-level `SET`.
- Observations follow CLAUDE.md Rule 3 (valid-axis restatement):
  - An unchanged value writes nothing.
  - A revised value closes the active row (`valid_to = now()`) and inserts the
    new value (`valid_from = now()`).
  - Active-row predicate: `valid_to IS NULL AND system_to IS NULL`. The
    partial unique index `uq_indicator_obs_point` enforces it.
- Values are `Decimal` end to end and are never floats.

### 2. Sec-master rule

- Macro indicators **never** go in the security master
  (`portfolio.securities_global`).
- Only investable benchmark series get linked, through
  `indicator_series.security_global_id`, in mkt03. The six are S&P 500,
  Nasdaq 100, Russell 2000, EFA, EEM, and gold. "Investable" means a real
  instrument (an index fund or ETF) that a member could actually hold, so a
  holding can be overlaid on its benchmark.
- In mkt01, `security_global_id` stays NULL on every row.

### 3. `default_transform` semantics

| value | use for | why |
|---|---|---|
| `rebase_100` | price-like series only (equity indexes, commodity prices, FX levels) | Base-100 compares growth paths. It only makes sense for a strictly positive level. |
| `level` | rates, spreads, and anything that can be zero or negative (yields, credit spreads, NFCI, curve slopes) | Rebasing a rate that crosses zero gives nonsense (division by ~0, sign flips). |
| `yoy_pct` | price indexes (CPI, PCE, PPI, ECI, house-price indexes) | People read inflation as a year-over-year rate, not as an index level. |
| `mom_pct` | allowed by the CHECK; unused in v1 | |

- Mixed frequencies render as **step lines**: a monthly value holds flat until
  the next print. The chart never interpolates between observations, because
  that would invent data points that were never published.
- `obs_date` is stored exactly as FRED gives it. FRED dates a monthly point on
  the 1st and a quarterly point on the quarter's first day. Nothing shifts it.

### 4. `license_class`

| value | meaning |
|---|---|
| `public_domain` | US government data (BLS, BEA, Census, Federal Reserve, Treasury). Free to redistribute. |
| `third_party_licensed` | Data FRED distributes but does not own: S&P / Dow Jones indexes, ICE BofA spreads, Case-Shiller, University of Michigan, and others. FRED's own terms pass the owner's restrictions through. |
| `unreviewed` | Nobody has checked yet. |

- **Open question (unresolved):** can a multi-tenant product show
  third-party-licensed series to members? Each tenant org is arguably a
  separate redistribution. FRED's API terms allow use, but they do not grant
  rights the original owner withheld.
- Until counsel answers this, mkt03 must not show a `third_party_licensed`
  series to tenant users without a decision on record.
- `validate` prints a `[FIND]` when FRED's notes mention copyright on a row the
  seed calls `public_domain`. It never changes `license_class` itself. That is
  a human decision.

### 5. FRED is authoritative for frequency

- `validate` maps FRED's `frequency_short` as follows: D→daily, W→weekly,
  M→monthly, Q→quarterly, SA→semiannual. Anything else becomes irregular.
- When the mapped value differs from the seed, `validate` overwrites the
  row's frequency and prints a `[FIND]`.
- After that, the registry loader keeps the row's frequency for a FRED row
  that has been validated. Without that rule, every run of `load` followed by
  `validate` would flip the value back and forth. To stop the `[FIND]`, fix
  the seed file to match.

### 6. Secret handling

- FRED takes the API key in the query string, so every request URL contains
  it. As a result, no URL, params dict, or upstream exception text is ever
  printed or stored.
- The httpx transport keeps only the exception's type name.
- Every message passes through `fred.scrub()`. It removes the literal key and
  any `api_key=…` fragment.
- `verify_mkt01.py` A10 proves this with a sentinel key, using fake transports
  built to echo the key back.

### 7. Error classes

- **Not found:** HTTP 404, or HTTP 400 *with* FRED's "series does not exist"
  message. This is the only case that sets `invalid_code`.
- **Transient:** 429, 5xx, or a timeout that survives 3 retries. Status stays
  unchanged and `last_error` is set.
- **Any other 4xx:** status stays unchanged. A bad API key is the main case:
  FRED also answers it with 400, so this rule stops a key typo from marking
  all 57 series invalid.

### 8. Provider-adapter registry (mkt02)

- A registry is a plain `dict[source_provider, adapter]`. It lives in
  `services/market_data/adapters.py`. The real registry contains exactly
  `fred` and `yahoo`.
- Adapter contract:
  - `fetch_series(row)` returns a `FetchResult`. Its `points` are
    `(obs_date, Decimal)` pairs, at most one per date. Missing and invalid
    points are already filtered out and counted (`skipped`, `rejected`).
    Raises on failure.
  - `validate_series(row)` returns a `ValidateResult` with one of three
    outcomes:
    - `active`, plus the registry fields the source knows (FRED: units,
      seasonal_adjustment, frequency; Yahoo: units only);
    - `invalid_code`, only when the source explicitly says the code does not
      exist;
    - `error` for anything else. This never changes `ingest_status`.
  - `scrub(text)` and `aclose()`.
- `market_data_ingest.py validate|backfill` dispatch through the registry
  with `--provider` (default `fred`, so mkt01 behaviour is unchanged).
- The orchestrator takes the registry and the series selection as arguments,
  so the verify passes fakes and fixture series only.
- `backfill` now filters on the provider. mkt01's version selected every
  active series regardless of provider. That was correct only while FRED was
  the only active provider; without the filter, the default run would have
  sent Yahoo tickers to FRED.

### 9. The nightly re-pulls FULL history

- Every night, every `active` series is fetched in full and diffed through
  the same write path `backfill` uses (`ingest.ingest_series`). There is one
  transaction per series inside `platform_scope()`.
- Why not a recent window: annual benchmark revisions (payrolls, GDP, price
  indexes) reach back years, and a window would miss them. One FRED request
  per series makes the full pull cheap. The diff writes only what changed,
  under Rule 3.
- One batch = one `batch_id`:
  - one `indicator_ingest_runs` row per series;
  - plus ONE summary row (`series_id` NULL) carrying the batch's row totals.
- The table has no column for series counts. The summary row's `error` text
  carries them in a fixed form:
  `batch summary: attempted=N succeeded=N partial=N failed=N skipped_no_adapter=N[; failed: keys]`.
- Summary status:
  - `success` if nothing failed;
  - `failed` if every attempted series failed;
  - otherwise `partial`.
- Series whose provider has no adapter (Shiller, World Bank, IMF, BIS, OECD
  until mkt02b) are skipped and counted. They are not failures.

### 10. Overlap: unique index plus diff-based writes, no lock

- Two runs over the same series are safe without a lock, by these rules:
  - **Close.** The UPDATE that closes a revised row repeats the active-row
    predicate (`valid_to IS NULL AND system_to IS NULL`) and returns the ids
    it really closed. Ids are locked in order to avoid deadlocks.
  - **Lost close.** A point whose close matched zero rows lost to a
    concurrent run. It is not inserted and counts as unchanged.
  - **Insert.** The INSERT uses
    `ON CONFLICT (series_id, obs_date) WHERE <active> DO NOTHING`. A point
    another run inserted first is skipped and counted as unchanged; it does
    not abort the series.
- Zero rows can only mean "lost the race" because `write_observations` /
  `apply_plan` first check that `app.is_super_admin` is `'true'` in the
  current transaction. Otherwise zero rows could equally be RLS silently
  refusing the UPDATE.
- Render never runs two instances of one cron service at once. The overlap
  case is a manual run during the nightly.

### 11. Staleness thresholds (FIRST-PASS)

| frequency | stale after |
|---|---|
| daily | 10 days |
| weekly | 21 days |
| monthly | 120 days |
| quarterly | 200 days |
| semiannual | 400 days |

- `per_meeting` and `irregular` are never flagged on age.
- A NULL `last_observation_date` on an active series is always flagged.
- These are first-pass values, to tune after the first live report. They are
  named constants in `services/market_data/staleness.py`.
- Staleness is a report printed as `[STALE]` lines. It is never a failure.

### 12. Alerting

- Task 1g found an existing platform-level alert mechanism:
  `services/workflow_todos.py`. It writes `member_todos` rows to the
  Hollisworks org's `manage_org_settings` holders, the same path the platform
  AI-ceiling alerts use. When there is no such holder, it writes a findable
  `audit_log` row instead.
- mkt02 adds ONE alert kind to it:
  `create_market_data_batch_failure_alert`, one per batch with failures,
  keyed on the batch id. The body has the batch id, the failed series keys,
  and their scrubbed errors. This is not a new alerting system.
- The Hollisworks org has had zero `manage_org_settings` holders. So today
  the alert most likely lands as an `audit_log` row
  (`action = 'market_data_batch_failed_alert_undelivered'`).
- The primary signal is the run log plus the non-zero exit code, which makes
  Render mark the run failed.
- The `org_id` on these alert rows belongs to the alert mechanism, not to
  market data. `market_data.*` still has no `org_id` anywhere.

### 13. Yahoo (mkt02)

- **Decision:** the six Yahoo series (^RUT, EFA, EEM, ACWX, GC=F, DX-Y.NYB)
  are enabled under the owner's **personal/internal-use assumption**. Yahoo's
  chart endpoint is unofficial and, as far as we know, licensed for personal
  use only. There are no paid customers and none are expected for a while.
  This is a recorded assumption, not a resolved licensing question. See
  "Launch blockers".
- Endpoint: v8 chart API, daily interval.
  - Maximum range is requested as `period1=0 … now`, not `range=max`.
    `range=max` can silently coarsen the bars, and the parser refuses any
    granularity other than `1d`.
  - Symbols are URL-encoded with `quote(symbol, safe='')`.
  - Requests send a browser User-Agent, are throttled to about 1 per second,
    and retry 429 / 5xx / timeouts 3 times with backoff.
- JSON is parsed with `parse_float=Decimal`, so no float ever exists. Every
  value is quantized to 4 dp with ROUND_HALF_EVEN before diffing. Yahoo's
  binary noise (2180.969970703125 vs 2180.9700000001) would otherwise revise
  history every night.
- **Raw close, not adjusted close.** Adjusted close restates the whole
  history at every dividend, which would revise every ETF row each quarter.
- Dates are exchange-local, from `meta.gmtoffset`. That is the offset at
  fetch time; daily bars are stamped near the session open, so a DST
  difference does not cross midnight.
- A bar dated today or later is dropped as incomplete. A null close is
  skipped and counted. A repeated date keeps the last bar.
- Error classes:
  - Only Yahoo's explicit "Not Found" chart error sets `invalid_code`.
  - 401 / 403 / 429 / 5xx / timeout / an unparseable body are transient:
    status unchanged, `last_error` set.
  - A bare 404 without that error body is not proof, and is not treated as
    not-found.
- **Data caveats.** These are recorded here, not "fixed" in code:
  - GC=F is a continuous front-month futures series. It jumps at contract
    rolls.
  - Yahoo may block datacenter IP addresses, so these series can work from a
    laptop and fail from Render. Failure isolation means a Yahoo failure
    never affects FRED series.

### 14. Rebase on the client; grid and correlations on the server (mkt03)

- The chart rebases on the CLIENT while the user drags the anchor, from raw
  series data. So `GET /market/series` returns raw observations, optionally
  downsampled, and never transformed.
- The grid and correlations are computed on the SERVER, so each transform
  has one definition (`services/market_data/transforms.py`,
  `correlation.py`).

### 15. Values cross the API as strings (mkt03)

- Every observation and transformed value is exact Decimal text, never a
  JSON number. JSON numbers are IEEE doubles in every browser.
- Transforms are computed in Decimal and quantized to 6 dp, ROUND_HALF_EVEN.
- The one exception is the correlation coefficient. It is computed in
  float64, rounded to 4 dp, returned as a string, and never stored.
- Exponent notation is never emitted: a stored `0.000000123` is returned as
  `"0.000000123"`, not `"1.23E-7"`.

### 16. The catalog exposes `source_provider` and `license_class` (mkt03)

- Every indicator in `GET /market/catalog` carries both fields, and the
  vocabularies list the values present. This is launch blocker 2, met at the
  API level, so mkt04 (or later) can gate Yahoo-sourced and
  `third_party_licensed` series per tenant.
- **The API itself does not gate them.** At mkt03 discovery, 15 active
  series were `third_party_licensed` (10 FRED, 5 Yahoo) and 5 were
  `unreviewed` (4 FRED, 1 Yahoo). Any authenticated session can read them
  through the API. No UI exists yet. Decision 4 still applies: no tenant UI
  ships these series until the licensing decision is on record. The Yahoo
  launch blocker still applies.

### 17. Access: a valid session, decided in one function (mkt03)

- `services/market_data/access.py` `require_market_data_read` is the only
  gate. No existing permission clearly means "may read market data". The
  nearest were `view_dashboard` and `view_portfolio`, and neither is about
  platform reference data. So no permission was invented. The gate admits any
  valid, active, authenticated session, and the envelope publishes
  `read_permission: null`.
- To require a permission later, set `MARKET_DATA_READ_PERMISSION` to its
  name. The existing branch then resolves it through `rbac.has_permission`,
  which checks super admin first.
- Reads use the normal request connection (the RLS-aware pool), switched to
  `SET LOCAL transaction_read_only = on`, and never `platform_scope()`. The
  three tables read all have a global SELECT policy.
  `indicator_ingest_runs` is platform-only and is not exposed.

### 18. Colours are a server-side palette (mkt03)

- Category base colours: Equities #2B5F9E, Rates & credit #C8641E, Growth &
  labor #3F8A5F, Inflation #9B5A8A, Housing #6F5E4E, Commodities #17707A,
  FX #4A5568. Any other category (today: "Financial conditions") gets
  #64748B.
- Each series gets a shade of its category base. Convert the base to HSL,
  keep hue and saturation, and spread lightness evenly from L − 0.10 to
  L + 0.18 in sort_order. A category with one series uses the base colour.
  Index securities use a navy ramp from #1B2B4B, and structured notes a gold
  ramp from #C5A880.
- **Clamp interpretation:** the [0.28, 0.68] clamp is applied to the two ENDS
  of the range, and the shades are spread between them. Clamping each shade
  separately would put most of the 54 gold notes on L = 0.68, all the same
  colour.
- If two shades still round to the same hex, the later one steps by 1/510 in
  lightness until it is unique. The result is deterministic, so every call
  returns identical colours.
- Moving the bases into the config table is a separate, optional change.

### 19. Long history: splice, provenance, credit spread, underlyings (mkt02c)

**Why.** Real data showed that some key series do not reach back far enough
for the key-dates chart. FRED serves a ROLLING window for some licensed
series: about ten years for S&P 500 (`fred.sp500` started 2016-10-03), and
only from October 2023 for the two ICE BofA credit spreads. A 2000 anchor on
the S&P therefore "floated" (decision 14's floating rule) instead of landing
on real history.

**The splice — one series, older history from Yahoo.**
- There is ONE series, `fred.sp500`, not two. Consumers see one continuous
  line. FRED stays the source of record and the nightly source. Yahoo
  (`^GSPC`) supplies only the history before FRED's first observation.
- Boundary **F0** = the earliest ACTIVE observation whose `source_provider`
  IS NULL (the series' own source). Spliced rows never define it, so a
  re-run computes the same F0.
- The fetch goes through the existing Yahoo adapter with a synthetic series
  row, so it uses the same parsing as every Yahoo series: Decimal only,
  4 dp ROUND_HALF_EVEN, raw close, incomplete-bar exclusion. No `^GSPC`
  registry series exists.
- **Overlap gate, before any write** (rule revised in mkt02c2). On every
  date >= F0 that both the fetch and the series' own rows carry, the gate
  passes only if ALL hold:
  1. at least 20 days are compared. Zero overlap would otherwise pass the
     percentage test vacuously;
  2. at least 99.0% of days differ by <= 0.02 (FRED stores 2 dp, Yahoo 4).
     This is the main protection against splicing a different index, a
     futures contract or a differently-scaled series: a wrong series fails
     it on most days, not one;
  3. no day differs by more than 0.5% of the series' own value, i.e.
     `|yahoo - series| / |series| <= 0.005`. This catches a catastrophic
     single-day break. A zero series value (impossible for an index) fails
     the gate with a message rather than raising.
  The thresholds are module constants in `splice.py` (`MIN_OVERLAP_DAYS`,
  `OVERLAP_MIN_PERCENT`, `OVERLAP_TOLERANCE`, `OVERLAP_MAX_RELATIVE`). Days
  beyond 0.02 are printed as [FIND] (worst ten) whether the gate passes or
  fails. On a pass the output says how many were tolerated. If the gate
  fails, nothing is written, the gate line names the failed rule(s) (and
  the offending day for rule 3), and the script exits 1.
  - Rule 3 replaced an absolute "no day may differ by more than 1.00",
    which is far too tight for an index near 5,000 (see the data-quality
    note below).
- **Data-quality note — FRED vs Yahoo on `fred.sp500` (live dry run,
  2026-10-04).** 2,514 overlap days (2016-10-03 to 2026-10-02), 99.76%
  within 0.02. Two isolated single-day disagreements between two feeds of
  the SAME index: 2021-08-11 (FRED 4447.7, Yahoo 4442.4102, about 0.12%)
  and 2019-08-12 (FRED 2883.75, Yahoo 2882.70, about 0.04%). The next worst
  was 0.13 (2020-05-11), then nothing above 0.04. The old 1.00 rule refused
  the splice on these two days alone. On both, `fred.sp500` keeps its own
  FRED value. **A day-level discrepancy inside FRED's own range (on or
  after F0) is reported, but never overwritten:** the splice writes only
  dates strictly before F0, whatever the gate finds.
- **Insert-only before F0.** A date before F0 with no active row is inserted
  with `source_provider = 'yahoo'`. An active `'yahoo'` row whose value
  changed gets the Rule 3 revision (close it, insert the new value, still
  `'yahoo'`). A FRED row (NULL provider), or a row from any other provider,
  is NEVER revised, closed or deleted by the splice. Dates >= F0 are never
  written. A re-run is a no-op.
- One transaction inside `platform_scope()`. It also appends one sentence to
  the series' notes (once) and writes one `indicator_ingest_runs` row with
  `run_trigger = 'manual'`. It never changes `ingest_status`.
- Code: `services/market_data/splice.py`, operator script
  `scripts/market_data_splice_history.py [--series] [--yahoo-symbol]
  [--dry-run]`. Exit 0 success, 1 gate or fetch failure, 2 configuration.
- Caveat: `fred.sp500`'s notes are a loader DEFINITION field. A later
  `market_data_ingest.py load` (v1 seed) rewrites them and drops the splice
  sentence. Re-running the splice puts the sentence back and writes no
  observations.

**Row-level provenance.** `market_data.indicator_observations.source_provider`
(migration `mkt02c_observation_source_provider`): nullable, CHECKed against
`fred | yahoo | shiller | worldbank | imf | bis | oecd | manual`. NULL means
"the series' own `source_provider`". Every row written before mkt02c is NULL,
and the ingest path still writes NULL. Only the splice writes `'yahoo'`.
Provenance is recorded per ROW, not per series, because one series can now
mix sources (see Launch blocker 4).

**The nightly never erases the splice.** Confirmed by reading the write path
(mkt02c Task 1b) and proven in `verify_mkt02c` (S6): `ingest.plan_writes`
walks only the points a fetch RETURNS. A date absent from a fetch is never
read, closed or deleted. FRED's rolling window moving forward therefore
leaves older rows alone, both spliced rows and FRED's own.

**Credit-spread substitute.** `fred.baa10y` (Moody's Baa corporate yield
minus the 10-year Treasury, FRED `BAA10Y`, daily) is a long-history
credit-stress proxy alongside the ICE BofA spreads. Its `license_class` is
`'unreviewed'` until someone checks Moody's terms on FRED (decision 4).

**Structured-note underlyings.** Five Yahoo index series, each linked
through `security_global_id` to its index security by the loader's new link
step (exact id + exact name + type `index` + live + not already linked, or a
[FIND] and no link):

| series_key | Yahoo | security |
|---|---|---|
| yahoo.dji | ^DJI | Dow Jones Industrial Average |
| yahoo.ftse | ^FTSE | FTSE 100 Index |
| yahoo.stoxx50e | ^STOXX50E | EURO STOXX 50 Index |
| yahoo.ssmi | ^SSMI | Swiss Market Index |
| yahoo.axjo | ^AXJO | S&P/ASX 200 Index |

They load from `docs/market_data/market_indicator_registry_additions_v2.json`
(`market_data_ingest.py load --seed …`). The v1 seed is not edited, so
verify_mkt01's v1 structural checks stay valid. **Still no price source:**
TOPIX (its Yahoo ticker is not confirmed), MSCI EAFE, the two Nasdaq-100
variants (Equal Weighted, Technology Sector) and S&P 500 Futures Excess
Return.

**Yahoo units.** When Yahoo's `meta.instrumentType` is `INDEX`, `validate`
stores units `'index points'`. For any other type, or a missing one, it
stores the currency code (the previous behavior). The real index series are
^RUT, DX-Y.NYB and the five above. `validate --provider yahoo` refreshes the
units on every Yahoo series.

### 20. Per-user privacy is enforced in the service layer (mkt03b)

- The platform's RLS session settings are `app.current_org_id` and
  `app.is_super_admin`. A third, `app.current_auth0_sub`, exists but is used
  only by the `users` table's bootstrap policy (so a brand-new user can read
  and insert their own row); no other policy reads it, and nothing maps it to
  a `users.id`. So no policy can say "only the caller's own rows".
- `market_data.user_key_dates` and `market_data.saved_views` therefore follow
  the established user-scoped pattern (`member_todos`,
  `user_notification_preferences`): RLS isolates the ORG, and every query in
  `services/market_data/key_dates.py` and `saved_views.py` ALSO filters by
  `user_id`. Both ids come only from the verified session: `get_org_id` (via
  the gate) and `services.users.ensure_user`.
- The spec's `visibility` column was deliberately not built. Org-shared views
  or dates are out of scope; the column can be added later.
- Next candidate: see "Next candidates" — a current-user setting would let the
  database enforce this.

### 21. Saved views store resolved anchors and stable keys (mkt03b)

- A view stores its anchor RESOLVED — `{"type": "date", "value": …}` or
  `{"type": "relative", "years": N}` — never a reference to a key date, so
  deleting a personal date can never break a view.
- Series are referenced by stable key: `series_key` for indicators, the
  `portfolio.securities_global` id for securities.

### 22. Stale selections are flagged, never removed (mkt03b)

If a referenced series stops being ACTIVE (or a security stops being
selectable), `GET /market/views` returns the view unchanged and lists that key
in the view's `unavailable` array. The stored config is not modified: a read
never writes. The member decides whether to edit the view.

### 23. Platform presets are seed-only (mkt03b)

Presets are `owner_scope = 'platform'` rows with NULL `org_id` and `user_id`.
They are written only by `scripts/market_data_seed_reference.py load` (inside
`platform_scope()`). Every API write filters `owner_scope = 'user'`, so a
preset id answers 404 to PUT and DELETE; the RLS write policies refuse them as
well for any non-super-admin context.

### 24. Fed tightening dates are approximate (mkt03b)

The eight `fed_tightening` regimes in `docs/market_data/market_regimes_v1.json`
(`source = 'fomc_curated'`) were seeded exactly as the owner supplied them.
They are APPROXIMATE month boundaries and must be verified against the FOMC
record by the owner before any external member sees them. The eight
recessions (`source = 'nber'`) follow the NBER chronology at month
granularity, also seeded exactly as supplied.

## Launch blockers

Before ANY external customer sees this platform:

1. **Yahoo series must be replaced with a licensed source, or removed.**
   They run today only under the personal/internal-use assumption in
   decision 13.
2. **The mkt03 API must expose `source_provider`** on every series it
   returns, so Yahoo-sourced (and other restricted) series can be gated per
   tenant. *Met at the API level by mkt03 (decision 16). The gating itself
   is still unbuilt and belongs to the UI (mkt04).*
3. The `third_party_licensed` question in decision 4 is still open.
4. **The S&P 500 history before F0 is Yahoo-sourced** (decision 19), so
   blocker 1 covers it too, even though it sits inside a FRED series. mkt04's
   per-tenant gate must be able to hide Yahoo-sourced ROWS, not only
   Yahoo-provider series. That is why provenance is recorded per row in
   `indicator_observations.source_provider`. The five note underlyings
   (decision 19) are ordinary Yahoo series and fall under blocker 1 directly.
5. **Saved views may reference Yahoo-sourced series** (the presets do:
   `yahoo.gc_f`, `yahoo.dji`, and `fred.sp500` before 2016). Storing a key
   is not displaying data, so mkt03b adds no new exposure, but blockers 1 and
   4 and mkt04's per-tenant gating apply to anything a view opens. A view
   whose series a tenant may not see must open with those series hidden or
   flagged, not silently plotted.

## API contract (mkt03)

All four endpoints are under `/api/v1/market`. They are read-only and gated by
`require_market_data_read`. No request may carry an org or user: bodies use
`extra='forbid'`, the series endpoint rejects any unknown query parameter, and
org and user come only from the verified session. Every response carries
`permissions` (`can_read`, `can_write: false`, `is_super_admin`,
`read_permission: null`, `write_permission: null`) and `vocabularies`
(`editable: []`, `inline_editable: []`, modes, transforms, frequencies,
grid_frequencies, limits).

Client components never call these endpoints directly. They go through a
Next.js API route, which mkt04 builds.

- **`GET /market/catalog`** (no parameters) returns:
  - `categories[]`: {key, label, color, sort_order}, ordered by the first
    sort_order in each category.
  - `indicators[]`: one per ACTIVE series, with series_key, name, category,
    category_key, region, frequency, units, seasonal_adjustment,
    default_transform, color, source_provider, license_class, cost_tier,
    first_observation_date, last_observation_date, security_global_id,
    ingest_status, sort_order.
  - `securities[]`: every active, non-merged `portfolio.securities_global`
    row, with id, name, short_name, security_type, price_source
    (`indicator_series` | `none`), series_key, selectable,
    unselectable_reason (`no_price_history`), color.
  - `vocabularies.license_classes` and `vocabularies.source_providers`: the
    values present.
- **`GET /market/series?keys=a,b&from=YYYY-MM-DD&to=YYYY-MM-DD&frequency=native|daily|weekly|monthly|quarterly`**
  - Returns, per key: series_key, native_frequency, frequency (as returned),
    point_count, first_observation_date, last_observation_date (whole
    history), and points as `[date, "value"]`, ascending.
  - `frequency` defaults to `native` and means AT MOST THIS FINE (see
    Resampling).
  - A security is selected only through its linked series_key.
- **`POST /market/grid`** with body `{keys, anchor, end?, mode, frequency}`:
  - mode is one of `index | sigma | level | default`; frequency is one of
    `daily | weekly | monthly | quarterly`; end defaults to today (UTC).
  - Rows are the period ends in [anchor, end], newest first, plus `end`
    itself when it is not a period end, so the newest row shows the latest
    data. That row has `is_period_end: false`. Daily rows are weekdays.
  - Each row is `{date, is_period_end, cells: {series_key: "value" | null}}`.
  - Per-series metadata: applied_transform, floating,
    anchor_observation_date, anchor_value, warnings,
    unavailable_reason, first_observation_date, last_observation_date.
  - Warnings: `rate_like_series_indexed`, and
    `native_frequency_coarser_than_grid` (cells are carried forward by the
    as-of rule).
  - Unavailable reasons: `non_positive_anchor`, `zero_variance`,
    `no_observations`.
- **`POST /market/correlations`** with body `{focus_key, keys, anchor, end?, lag_months = 0 (−24..24), min_periods = 24 (12..120)}`:
  - Returns, per candidate: series_key, r (string | null), n, frequency,
    overlap_from, overlap_to, unavailable_reason, change_method ({focus,
    candidate}: `log` | `diff`), warnings.
  - The focus is excluded even when listed. Results are sorted by |r|
    descending, nulls last, then by series_key.
  - The response includes the window used, `effective_lag_months`, and the
    lag convention.
  - Unavailable reasons: `insufficient_overlap`, `zero_variance`,
    `unsupported_frequency`, `lag_not_multiple_of_period`.
  - Warning: `promoted_to_monthly_for_lag`.

**Limits** (`read_service.py`):

| Limit | Value | Basis |
|---|---|---|
| Keys per request (series, grid, correlation candidates) | 40 | |
| Points per series | 30,000 | The largest active series, fred.dff, had 26,391 at discovery |
| Points per series request | 300,000 | Just under the whole active table (313,156) |
| Grid rows | 2,000 | |
| Daily grid window | 400 days | |
| Request body | 64 KiB | |

**Refusals.** Every refusal is a 422 of the form
`{"detail": {"message", "errors": [{loc, type, msg}]}}`. Unknown or
non-active keys answer `{"detail": {"message", "unknown_keys", "inactive_keys"}}`,
which list only the offending keys.

Errors never echo the request: not the value, and not the name of an
undeclared field. That is why bodies are parsed by the service and not by
FastAPI. FastAPI's default 422 returns pydantic's `input`, which echoes the
caller's own data.

## Key dates, regimes and saved views (mkt03b)

### Tables (schema `market_data`, migration `mkt03b_key_dates_regimes_views`)

| Table | Holds | Key | RLS |
|---|---|---|---|
| `key_dates` | 32 crises and turning points | `slug` UNIQUE | global read; writes only when `app.is_super_admin = 'true'` |
| `regimes` | 8 NBER recessions, 8 Fed tightening cycles | UNIQUE `(regime_type, start_date)` | global read; writes super-admin only |
| `user_key_dates` | a member's own dates ("My dates") | UNIQUE `(org_id, user_id, lower(name), event_date)` | ONE org-isolation policy for all commands |
| `saved_views` | platform presets + members' own views | user: UNIQUE `(org_id, user_id, lower(name))`; platform: UNIQUE `(lower(name))` | read: platform rows + own org; write: `owner_scope = 'user'` rows of own org (or super admin) |

CHECKs: a key date's `end_date >= start_date`; `end_date` and `end_precision`
both null or both set; a month-precision `start_date` is the 1st. Regime
`end_date >= start_date`. Personal names are trimmed (`name = btrim(name)`),
1–60 characters for dates and 1–80 for views. A platform view has NULL
`org_id` and `user_id`; a user view has both. `config` is a JSON object of at
most 20,000 bytes (`octet_length(config::text)`). No table has a trigger:
`updated_at` is set by the code that writes. Both personal tables are hard
delete (they are bookmarks, not records).

### Seeds and loader

- `docs/market_data/market_key_dates_v1.json` (32: 22 `owner_list`, 10
  `suggested`), `market_regimes_v1.json` (16), `market_view_presets_v1.json`
  (4). Each is `{"version": 1, "<list>": [...]}`.
- `apps/api/scripts/market_data_seed_reference.py load [--dry-run]` calls
  `services/market_data/reference_seed.load_reference(conn, key_dates=,
  regimes=, views=, dry_run=)`, which takes ROWS (tests pass fixtures).
  Upserts: key dates on `slug`, regimes on `(regime_type, start_date)`,
  presets on `lower(name)`. Only data fields and `updated_at` change, and
  only when a value differs (`IS DISTINCT FROM`), so a re-run writes nothing.
  `is_active` and `notes` are never written on an existing row. Nothing is
  ever deleted. A preset failing the config schema, or naming a key that is
  not an ACTIVE series or selectable security, is skipped with a `[FIND]`.
- The loader is the only mkt03b code that uses `platform_scope()`.

### API (under `/api/v1/market`, gate `require_market_data_read`)

| Route | Body | Answers |
|---|---|---|
| `GET /key-dates` | — | `key_dates` (active, by `start_date`), `custom_dates` (own only, by `event_date`; `[]` when none), `regimes` (active, by `start_date`), `data_range {first, last}`, `permissions`, `vocabularies {kinds, regime_types}`, `limits {custom_dates_max: 50, name_max: 60}` |
| `POST /key-dates/custom` | `{name, event_date}` | 201 + the saved row |
| `DELETE /key-dates/custom/{id}` | — | 200 `{"deleted": true}`; 404 otherwise |
| `GET /views` | — | `presets`, `views` (own only), each `{id, name, config, created_at, updated_at, unavailable}`; `permissions`; `vocabularies {modes, scales, anchor_types, selection_kinds}`; `limits {views_max: 50, name_max: 80, selection_max: 40, config_max_bytes: 20000, relative_years: [1, 60]}` |
| `POST /views` | `{name, config}` | 201 + the saved view |
| `PUT /views/{id}` | `{name?, config?}` (at least one) | 200 + the saved view |
| `DELETE /views/{id}` | — | 200 `{"deleted": true}`; 404 otherwise |

- `permissions`: `can_read: true`, `can_write: true` (meaning "your own
  rows"), `is_super_admin`, `read_permission: null`, `write_permission: null`
  — the same single session gate as the read API; no permission is invented.
- 404 is one answer for a missing id, a malformed id, another user's row,
  another org's row and a platform preset: the caller learns nothing about
  which.
- Errors are `{"detail": {"message", "errors"?, ...}}` and never echo the
  request: bodies are parsed raw with `extra='forbid'` at every level, and an
  undeclared field (e.g. `org_id`, `user_id`) is reported by its PARENT object,
  never by name. pydantic's discriminator message (which quotes the caller's
  `type`) is replaced with fixed text.
- "The available data range" is the earliest first and latest last active
  observation across ACTIVE series, read per request through the mkt03
  repository. A personal date is accepted from the first day of the first
  month to the last day of the last month. [FIND] at mkt03b discovery the
  range began 1919-01-01 (an early macro series), not 1970-01-02.
- Writes take a transaction-scoped advisory lock per (table, org, user), so two
  concurrent saves cannot both pass the 50-row limit.

### Validation messages (exact)

| Case | Status | Message |
|---|---|---|
| date name empty / whitespace / missing | 422 | Enter a name for this date. |
| date name over 60 characters | 422 | Names can be up to 60 characters. |
| any control character in a name (dates and views) | 422 | Names cannot contain control characters. |
| date missing or malformed | 422 | Pick a date. |
| date outside the data range | 422 | That date is outside the available data (Jan 1919 to Oct 2026). — the real range, as `Mon YYYY` |
| same name (any case) on the same date | 409 | You already saved that name on that date. |
| the 51st date | 422 | You can save up to 50 dates. Delete one first. |
| view name empty / missing | 422 | Enter a name for this view. |
| view name over 80 characters | 422 | Names can be up to 80 characters. |
| same view name (any case), on POST or a PUT rename | 409 | You already have a view with that name. |
| the 51st view | 422 | You can save up to 50 views. Delete one first. |
| PUT with neither field | 422 | Send a name, a config, or both. |
| config fails a shape rule | 422 | The view's settings are not valid. (+ `errors[].loc` naming the field) |
| a key not active / not selectable | 422 | Every selection must be an active series or a selectable security. (+ `unknown_keys`, `inactive_keys`, `unselectable_keys`: offending keys only) |

### Config schema (canonical; `extra='forbid'` at every level)

```json
{"v": 1,
 "selection": [{"kind": "indicator" | "security", "key": "<series_key | securities_global id>"}],
 "anchor": {"type": "date", "value": "YYYY-MM-DD"} | {"type": "relative", "years": N},
 "end": null | <same shape as anchor>,
 "mode": "index" | "sigma" | "default" | "level",
 "scale": "log" | "linear",
 "overlays": {"events": bool, "band": bool, "emphasis": bool}}
```

Rules: 1–40 selections, no duplicate `(kind, key)`; an indicator key must be an
ACTIVE registry series and a security key a canonical-lowercase
`securities_global` id the catalog marks selectable (priced by an active
series); relative `years` 1–60; dates must parse; when both are dates the
anchor must not be after the end; `end` is required and may be null. Size is
checked first, as Postgres measures it. The stored config is the validated
canonical form, which equals the input for any valid config.

## Front end (mkt04a)

The first UI slice: `/market` ("Market indicators") in `apps/web`, with the
selection panel and the Grid tab. The chart is mkt04b; key dates, saved views
and correlations are mkt04c.

**Routes.** Every backend route has a Next.js route under `/api/market/...`
(`catalog`, `series`, `grid`, `correlations`, `key-dates`,
`key-dates/custom`, `key-dates/custom/[id]`, `views`, `views/[id]`; eleven
handlers). The table and the forward live in `lib/market/marketRoutes.mjs`
(pure, testable with a faked session and backend); `lib/marketForward.js`
binds it to the host-aware `getRequestAuthClient`. Each handler checks the
session (401 `{"error": "Unauthorized"}` without one, backend never called),
attaches the access token, passes the query string through for `series`
only, forwards a POST/PUT body as the caller's own text after checking it
parses, and returns the backend's status and body unchanged with
`Cache-Control: no-store`. Nothing is logged. No handler reads or adds an org
or user; a body that carries one reaches the API, which refuses it (422).

**Fail closed.** `lib/market/catalogModel.mjs` `interpretCatalog` returns
`ready` only for a response whose `permissions.can_read === true` and which
has a `vocabularies` object. Anything else is an error state, and the
selection panel and grid controls exist only in the ready branch of
`MarketIndicatorsView`. A grid response without its envelope is also shown as
an error, not a table. There is no truthy fallback anywhere.

**Selection model.** React state in the page, held as `[{kind, key}]` —
`indicator` + series_key, or `security` + securities_global id — exactly the
saved-view config's `selection`, so mkt04c can persist it unchanged.
`lib/market/selection.mjs` does all grouping, chip state (all / some / none),
toggling and the limit (`vocabularies.limits.max_keys`, counted per
selection; a catalog without it allows nothing). Unselectable securities and
notes can never enter the list. A security is requested through its linked
series_key; an indicator and the security it prices are requested once.

**Server versus chrome.** From the API: category labels, order and colours;
series and security names and colours; source provider (shown on every row);
the license marker (the class's code text, shown for every class except
`public_domain`, and only when the catalog's `license_classes` lists it);
unselectable reasons; the Measure and Frequency option lists
(`modes`, `grid_frequencies`); the selection limit; every grid value, date,
floating flag, warning and unavailable reason; every error message. The API
publishes no label for license classes, warnings or reasons, so those show as
their code text with underscores as spaces. Chrome: the page title, tab
names, control captions, "Start of data", "5 years ago", the empty-state and
placeholder lines, "starts after the anchor", and the limit sentence (with
the server's number). The defaults the sprint fixed (mode `default`,
frequency `monthly`, anchor five years back, the `public_domain` exception,
the two selection kinds) live in `lib/market/marketDefaults.mjs` alone and
fall back to the server's first option if the server stops offering them.

**Grid.** One POST per burst of changes (300 ms debounce), none for an empty
selection, and only the newest response renders. Cells are the server's
strings; the only change is comma grouping of the integer digits, done on the
text (no number conversion). Null is an em dash. Rows keep the server's order.
The table scrolls inside its own container (sticky header and date column),
so the page never scrolls sideways. `components/ui/DataGrid.jsx` is not used:
it sorts, filters and paginates client-side.

**Tests.** `apps/web/tests/market/*.test.mjs` (node:test, `npm test`) import
the real modules and render the real components with `react-dom/server`.
`tests/market/jsxLoader.mjs` compiles JSX with the `typescript` package that
is already installed (transitive); no dependency was added.

**Open launch blocker.** Per-tenant gating of Yahoo-sourced and
`third_party_licensed` (and `unreviewed`) series is still unbuilt. The page
shows every series the catalog returns, with its source, for internal use
only. It must not reach an external customer until that gate exists
(launch blockers 1-5 above).

## Transform definitions

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

Implementation notes (mkt03):
- "date minus N months" clamps the day: Mar 31 minus 1 month is Feb 28 or 29.
- A yoy or mom whose earlier value is exactly 0 is null, not an error.

## Correlation method

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

Implementation notes (mkt03):
- v_{t-1} is the previous point of the reduced, windowed series. For daily
  data that spans weekends and holidays.
- A lag in calendar months must land on whole periods:
  - A daily or weekly pair with a non-zero lag runs MONTHLY instead (warning
    `promoted_to_monthly_for_lag`).
  - A quarterly pair needs a lag that is a multiple of 3 (else
    `lag_not_multiple_of_period`).
- Changes are computed in Decimal (ln included). Only the coefficient is
  float64.

## Caveats

- **Correlations use period dates, not publication dates.** A monthly series
  dated the 1st may be published weeks later. Lead/lag results are
  indicative, not tradeable.
- **Correlating levels is spurious.** Two trending series correlate in level
  whatever their relationship. That is why this endpoint only correlates
  changes.
- **Correlations are unstable across regimes.** A coefficient over one window
  says little about another. The anchor and end are the caller's choice for
  exactly that reason.

## Code

- `apps/api/services/market_data/registry.py` — seed loader. It updates
  definition fields only and never deletes a row.
- `apps/api/services/market_data/fred.py` — the client. It has an injectable
  transport, throttles to 0.6s spacing (~100 requests/min), and retries
  3 times with backoff.
- `apps/api/services/market_data/ingest.py` — `validate`, `backfill`,
  `write_observations`.
- `apps/api/scripts/market_data_ingest.py` — the operator script:
  `load | validate | backfill | all [--series KEY]`.
- `apps/api/scripts/verify_mkt01.py` — Phase A (fixtures plus a fake FRED)
  always runs. Phase B runs with `--live`.
- `apps/api/services/market_data/adapters.py` — the adapter registry,
  `FredAdapter`, `scrub_error` (the orchestrator's own scrubbing, independent
  of any adapter's).
- `apps/api/services/market_data/yahoo.py` — the Yahoo adapter, with an
  injectable transport.
- `apps/api/services/market_data/nightly.py` — `run_nightly(conn, registry,
  series_selection=None, trigger='nightly')`.
- `apps/api/services/market_data/staleness.py` — `stale_series(rows, today)`.
- `apps/api/scripts/market_data_nightly.py` — the Render cron entrypoint.
  Exit code 0 means all succeeded, 1 means a failure or crash, 2 means a
  missing variable.
- `apps/api/scripts/verify_mkt02.py` — Phase A (fixtures plus fake
  adapters) always runs. Phase B (`--live`) runs after two nightly runs.
- `docs/market_data/RENDER_CRON_SETUP.md` — operator setup for the cron
  service.
- `apps/api/services/market_data/read_repository.py`, `transforms.py`,
  `resample.py`, `correlation.py`, `palette.py`, `access.py`,
  `read_service.py`, and `apps/api/routers/market_data.py` — the mkt03 read
  API. The pure modules have no database access.
- `apps/api/scripts/verify_mkt03.py` — Phase A (fixtures `verify.mkt03.*`,
  through the real ASGI app) always runs. Phase B (`--live`) reads the real
  data.
- `apps/api/services/market_data/splice.py` and
  `apps/api/scripts/market_data_splice_history.py` — the long-history splice
  (decision 19).
- `docs/market_data/market_indicator_registry_additions_v2.json` — the
  mkt02c additions, loaded with `market_data_ingest.py load --seed <path>`.
  The loader's link step is `registry.link_security`.
- `apps/api/scripts/verify_mkt02c.py` — Phase A (fixtures `verify.mkt02c.*`,
  fake transports) always runs. Phase B (`--live`) runs after the operator
  steps.
- `apps/api/services/market_data/key_dates.py`, `saved_views.py`,
  `personal.py` and `apps/api/routers/market_data_personal.py` — the mkt03b
  personal and reference API. They never import `platform_scope`.
- `apps/api/services/market_data/reference_seed.py` and
  `apps/api/scripts/market_data_seed_reference.py` — the reference loader.
- `docs/market_data/market_key_dates_v1.json`, `market_regimes_v1.json`,
  `market_view_presets_v1.json` — the seeds.
- `apps/api/scripts/verify_mkt03b.py` — Phase A (fixture orgs, users, series
  and rows `verify.mkt03b.*`, through the real ASGI app) always runs. Phase B
  (`--live`) runs after the loader.

## Roadmap

- **mkt02** (built) — the nightly refresh: adapter registry, orchestrator,
  staleness report, Render cron entrypoint, and the Yahoo adapter.
  - The planned "mkt02 = nightly cron + non-FRED adapters" scope was split.
  - EIA was dropped, because FRED already carries WTI, Brent and Henry Hub.
  - EDGAR pipeline A showed that a failure which raises blocks every later
    night. Here, per-series failures stay per-series.
- **mkt02b** — adapters for Shiller, World Bank, IMF, BIS and OECD (one
  `deferred` series each). Built after this cron is proven live. Until then,
  the nightly skips them as `skipped_no_adapter`. The 6 `deferred_paid`
  series wait on a cost decision.
- **mkt03** (built) — the read API: catalog, series, grid, correlations
  (decisions 14–18, "API contract"). Part 1, applied before the sprint,
  linked three benchmark series to the security master: fred.sp500,
  fred.nasdaq100 and yahoo.rut. The other three of decision 2's "six" (EFA,
  EEM, gold) have no matching `securities_global` row, so they stay
  unlinked.
- **mkt02c** (built) — long history (decision 19): the S&P 500 splice from
  Yahoo, row-level provenance, `fred.baa10y`, five note-underlying Yahoo
  series linked to their securities, and Yahoo index units.
- **mkt03b** (built) — key dates, regimes, personal dates and saved views:
  API, seeds and loader (decisions 20–24, "Key dates, regimes and saved
  views"). The original spec, `docs/market_data/KEY_DATES_SPEC_V1.md`, is
  superseded where that section says so.
- **mkt04** — the chart (base-100 with security overlay) and the grid UI,
  plus the Next.js routes in front of this API. mkt04 also gates
  `third_party_licensed`, `unreviewed` and Yahoo series per tenant
  (decisions 4 and 16).
  - **mkt04a** (built) — the Next.js routes, the `/market` page, the
    selection panel and the grid ("Front end (mkt04a)").
  - **mkt04b** — the chart. **mkt04c** — key dates, saved views,
    correlations. The per-tenant gate is a separate sprint, still unbuilt.

## Next candidates

- **A current-user RLS setting (from mkt03b, decision 20).** Today per-user
  privacy on `user_key_dates` and `saved_views` rests on the service layer's
  `user_id` filter, with RLS only isolating the org. If the request pipeline
  set an `app.current_user_id` GUC (resolved from the verified session, the
  same way `app.current_org_id` is), each policy could add
  `user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid`
  and the database would enforce it — a missed filter in a future endpoint
  would then fail closed instead of leaking a colleague's bookmarks. That is a
  platform-wide change (every user-scoped table, the middleware and
  `platform_scope`), so it was not made here.

- The unlinked index securities are underlyings of the structured notes.
  mkt02c added and linked five of them (decision 19). Still without a price
  source: TOPIX (Yahoo ticker not confirmed), MSCI EAFE, Nasdaq-100 Equal
  Weighted, Nasdaq-100 Technology Sector, S&P 500 Futures Excess Return.
  Adding a seed row with `security_global_id` + `security_name` is enough;
  the catalog then marks them selectable with no code change.
- The 54 structured notes need a price source before the overlay can plot
  them. Today `portfolio.securities_global_prices` has 0 rows.
