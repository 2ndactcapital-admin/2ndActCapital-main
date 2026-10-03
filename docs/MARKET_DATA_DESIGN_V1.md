# Market Data — Design V1

Status: mkt01 built (registry, FRED adapter, historical backfill). The
operator has not run the ingest or the verify yet. Next: mkt02, then mkt03.

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

## Roadmap

- **mkt02** — a nightly Render cron for the incremental fetch. It also adds
  adapters for the non-FRED sources: Yahoo, Shiller, World Bank, IMF, BIS,
  OECD. Those cover the 12 `deferred` series; the 6 `deferred_paid` series
  wait on a cost decision. EDGAR pipeline A showed that a Service Task which
  raises blocks every later night, so per-series failures must stay
  per-series, as they already are in `backfill`.
- **mkt03** — an API that publishes the permission envelope. It adds the
  base-100 chart with security overlay and a grid. It also links the six
  benchmark series to the security master, and resolves the licensing
  question before any `third_party_licensed` series is shown to tenants.
