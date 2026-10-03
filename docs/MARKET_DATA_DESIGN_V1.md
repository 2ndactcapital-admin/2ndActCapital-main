# Market Data — Design V1

Status: mkt01 built (registry, FRED adapter, historical backfill); verified
live, 57 passed, 0 failed. mkt02 built (adapter registry, Yahoo adapter,
nightly orchestrator, staleness report, Render cron entrypoint); its verify
is written but not yet run. Next: mkt02b, then mkt03.

UPDATE 2026-10-03 (mkt02): decisions 8–13 and the "Launch blockers" section
below are new. The Roadmap is rewritten: the planned "mkt02 = nightly cron +
non-FRED adapters" scope was split.

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

## Launch blockers

Before ANY external customer sees this platform:

1. **Yahoo series must be replaced with a licensed source, or removed.**
   They run today only under the personal/internal-use assumption in
   decision 13.
2. **The mkt03 API must expose `source_provider`** on every series it
   returns, so Yahoo-sourced (and other restricted) series can be gated per
   tenant.
3. The `third_party_licensed` question in decision 4 is still open.

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
- **mkt03** — an API that publishes the permission envelope AND
  `source_provider` (launch blocker 2). It adds the base-100 chart with
  security overlay and a grid. It also links the six benchmark series to the
  security master, and resolves the licensing questions before any
  `third_party_licensed` or Yahoo series is shown to tenants.
