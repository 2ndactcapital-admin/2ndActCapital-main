# PRICES1 — discovery (2026-11-07)

End-of-day price history through the vendor-neutral security-data layer. Repo at `prices1-work` (base `9049690`,
merge secmaster2) and the live database, read-only, before any change.

## 1a. The provider contract today (`services/security_data/providers/base.py`)

* `BaseProvider`: `key`, `capabilities` (data types it can answer), `fetches` (types the marketdata1 nightly refreshes
  over the network), `read(conn, ctx, data_type, subjects, *, end, start=None)` (DB only — the rank walk's input),
  `fetch(pool, data_type, subjects, *, today, budget_left) -> FetchOutcome(calls, written, errors, skipped,
  budget_exhausted)`, `scrub(text)`, `aclose()`.
* Transport injection: `HttpTransportBase(transport, min_interval, max_retries, sleep)` — anything with
  `async get(url, params) -> (status, body)`; throttled (`Throttle`), retried on `RETRY_STATUSES`; `transport=None`
  raises `ProviderNotConnected`. `providers/__init__.build_providers(transports=…)` injects one per key; verifies pass
  recorded fixtures. The reference-data sources (secmaster1/2) use a different boundary
  (`reference_common.SourceTransport.lines()` / `FixtureTransport`).
* Budget / call log: `writes.budget_left(conn, provider_row, day)` from `data_providers.daily_call_budget` minus
  `provider_call_log` for the day; `writes.record_calls(...)` (insert-only). The nightly passes items a provider could
  not fetch to the next fetching provider in the rank.
* `vendor_stub.VendorStubProvider('vendor_slot')`: `connected = False`, no network, in the `factor_score` rank only;
  `admin.update_provider` refuses to set a not-connected module active.
* **Gaps for an EOD price adapter / history backfill** (what PRICES1 adds): there was NO price-fetching method at all
  — `fetch()` is "refresh what you cover today" and `nightly.coverage()` returns `[]` for `eod_price`; no declared
  identifier (ticker / FIGI / CUSIP) an adapter keys by; no date range (start, end) for history; no bar shape (open /
  high / low / adjusted close / volume, currency); no statement of adjusted vs unadjusted; licence facts lived only in
  the registry row, not beside the code that must honour them; `writes.upsert_prices` restates on the VALID axis
  row by row (the task asks for a new SYSTEM version, and a 5-year backfill needs set writes); no quality rule for a
  zero / negative close or a currency mismatch (`data_quality_events_kind_chk` had five kinds); no "why" for
  `price_coverage`; no checkpoint / resume; no run row per backfill.

## 1b. Candidate FREE end-of-day sources (small samples, 2026-11-07)

Doppler (project `hollisworks`, config `prd`), checked BY NAME only: `TIINGO_API_KEY` absent, `POLYGON_API_KEY`
absent, `ALPHAVANTAGE_API_KEY` / `ALPHA_VANTAGE_API_KEY` absent, `NASDAQ_DATA_LINK_API_KEY` / `QUANDL_API_KEY` absent.
**`FMP_API_KEY` IS present** [FIND] — not used (FMP is the deferred paid vendor; no call was made to it).

| Source | Sample (no key unless stated) | Coverage | History | Limits | Key | Bulk | Adjusted? | Terms |
|---|---|---|---|---|---|---|---|---|
| **Stooq** (`stooq.com/q/d/l/?s=aapl.us&i=d`, bulk `/db/h/`) | HTTP 200 **HTML: a JavaScript proof-of-work bot wall** ("This site requires JavaScript to verify your browser") for AAPL, VFIAX, SPY | US stocks / ETFs; some funds | decades | n/a | no | daily files — behind the same wall | split-adjusted | personal use. **Not usable** unattended (getting past the wall would be circumventing a bot check — not done) |
| **Yahoo** chart (`query1.finance.yahoo.com/v8/finance/chart/…`; the market-indicators adapter's endpoint) | 200 JSON: AAPL (equity), VFIAX (mutual fund, one NAV a day, volume 0), SPAXX (money market, close 1.0, open 0.0), NVDA 2024-06 (split 10:1 in `events.splits`), KO (dividend in `events.dividends`); unknown symbol → **404** `{"code":"Not Found","description":"No data found, symbol may be delisted"}` | US stocks, ETFs, mutual funds, money-market | to the listing date (`firstTradeDate`) | unofficial, none published (~1 req/s used) | no | no (one symbol per request) | `close` is **split-adjusted** (NVDA 2024-06-07 reads 120.89, traded 1208.88); `adjclose` = split + dividend | Yahoo terms: **personal, non-commercial use**; no redistribution |
| **Tiingo** free (`api.tiingo.com/tiingo/daily/aapl/prices`) | 403 `{"detail":"Please supply a token"}` | US stocks, ETFs, mutual funds | 30+ years | ~1,000 req/day, 500 distinct symbols/month (free tier, published) | **yes** | no | raw close **and** adjClose + splitFactor + divCash | free = personal / internal; display or redistribution needs the commercial licence |
| **Alpha Vantage** free (`TIME_SERIES_DAILY&apikey=demo`) | 200 JSON for IBM with the demo key only (compact = 100 days) | US stocks / ETFs (funds partial) | full history and adjusted series are premium | **25 requests/day** | yes | no | free daily = unadjusted only | free tier, attribution requested; display terms restrictive |
| **Polygon** free (`/v2/aggs/ticker/AAPL/range/1/day/…`) | 401 `"API Key was not provided"` | US stocks / ETFs; no mutual funds | 2 years | 5 calls/min | yes | grouped daily (one call per day for the whole market) | both (`adjusted=` flag) | free tier: personal, non-commercial |
| **Nasdaq Data Link** (`WIKI/PRICES`) | 403 `"A valid API key is required"` (datasets API: an Incapsula bot page) | WIKI US EOD — **frozen since 2018** | to 2018 | — | yes | bulk download | both | free sets are stale; current EOD is paid |

**Recommendation — primary: Yahoo chart (`yahoo_chart`); fallback: Tiingo free (`tiingo`, written, NOT CONNECTED
until its key exists).** Yahoo is the only keyless source that works today, reaches mutual-fund and money-market NAVs
(the platform holds many funds) and gives a full history in one request per symbol. Tiingo is the cheapest honest
fallback: raw + adjusted closes, funds, a published free budget; it needs only a key in Doppler and its registry row
switched on. Stooq is behind a bot wall; Alpha Vantage's 25/day cannot backfill; Polygon has no funds and 2 years;
Nasdaq Data Link's free EOD stopped in 2018.

**Licence facts recorded** (`docs/prices1_part1.sql`, `portfolio.data_providers`):

| Key | Status | Internal use | Member display | Redistribution | Retention after cancel | Attribution | Budget/day |
|---|---|---|---|---|---|---|---|
| yahoo_chart | active | yes — the owner's recorded internal-use assumption (Joe, 2026-10-03, the market-indicators module) | **not_allowed** | no | unknown | Prices: Yahoo Finance | 2000 |
| tiingo | not_connected | yes | **not_allowed** | no | delete | Prices: Tiingo | 900 |

Daily update time (for the job's schedule): US closes are final after 16:00 New York; mutual funds strike NAVs after
the close and publish ~18:00–20:00 New York. The job runs **weekdays 21:15 New York = 01:15 UTC (summer) / 02:15 UTC
(winter)**.

## 1c. Who needs prices — the coverage set (live, 2026-11-07)

Sources (`services/security_data/prices.py::coverage_set`): securities held in a live position
(`portfolio.positions` → `portfolio.assets.global_security_id`), in a live model version
(`portfolio.model_version_positions`), a replica registry entry (`portfolio.replica_securities`) and its live
holdings snapshot's constituents (`portfolio.replica_holdings_rows`), option packages / contracts
(`portfolio.option_packages`, `portfolio.option_contracts`), note replicas (`public.note_replicas`), substitution
candidates (`public.substitution_set_candidates`), backtest funds (`public.backtests`), plus the platform benchmarks
(SPY, AGG, ACWI — **no benchmark table and no watch-list table exist**; the only "watch" columns are text arrays of
archetypes / issuers / underlyings, not securities). Test orgs are left out.

**Today: 29 securities** — 28 held in live positions (all in 2nd Act Capital, all with a ticker: AAPL, AGG, BND,
BRK.B, EFA, GOOGL, IWM, MSFT, NVDA, PJAN, QQQ, SCHB, SCHD, SCHF, SCHP, SCHR, SCMB, SGOV, SPY, VEA, VEU, VOO, VT,
VTEB, VTI, VTIP, VWO, VXUS; 2 of them also benchmarks) + 1 benchmark not held (ACWI). Models, replicas, snapshots,
option contracts, note replicas, substitution candidates and backtests: **0 rows each**. Not "all 16k": 29 of 15,970.
`prices_backfill.py --dry-run` (real run of the dry run, no network): 29 securities, 29 calls, ~41 s, 1 day at the
budget. `securities_global_prices` = 0 rows; every security `price_coverage = 'unknown'`.

## 1d. Every price reader and how it asks

Every reader outside `services/security_data/` reads through the layer with `purpose = "internal"`; none read
`securities_global_prices` directly (the security-master summary only counts EXISTS; `securities_global.add_price`
only inserts).

| Reader | How it asks | With a series (before PRICES1) | Without |
|---|---|---|---|
| Household / entity roll-up (`services/networth.value_lines` → `portfolio_rollup.position_current_value`) | **never asked the layer** | ignored: entered value ("Entered value (as of its statement date)") | entered value / valuation ladder / "No value — not counted" |
| X-ray (`services/xray._fund_analysis`) | `SD.get(... account_ids=[account], max_age_days=36500)` for provenance only | **hard-wired** `tracking_error` / `harvest_benefit` = "not estimated — needs prices" | same |
| Tracking error (tilts, `tilts.estimate_te` → `rep10_common.longest_history` → `SD.history`) | history, internal | computes (≥ 60 obs, ≥ 95 % weight) | "not estimated — needs prices (…% of weight covered)" |
| Backtest (`services/backtest.run` → `histories` → `longest_history`) | history, internal | computes (harvest simulated) | "… — the fund has N price(s) in the window" |
| Harvest (`services/harvest`, `rebalance_proposals._unheld_prices`) | `get_bulk`, internal (unheld only) | unheld securities priced; held = position mv ÷ qty | "needs a price"; TE cost note hard-wired |
| Drift / proposals (`account_models.load_holdings`, `rebalance_engine.max_drift`) | **never asked the layer** for held | position mv ÷ qty | "cannot value" |
| Replica viability Q3 (`replica_viability.latest_prices`) | `get_bulk`, internal | computes | "unknown — needs prices for n of m constituents" |
| Blocks, gifts | `SD.get`, internal | computes | "not priced" / "not estimated — needs prices" |
| Loss bank, options basis risk, notes | adviser quotes / fills, not the layer | hard-wired "needs prices" lines | same |
| SSVI surface | `yfinance` live spot, not the layer | — | — [FIND: a second, direct Yahoo reader outside the layer; untouched] |

PRICES1 changes the four that ignored a series: the roll-up (Task 7: price when a series exists, else entered value),
drift (a fresher close values the holding), the X-ray's tracking error and harvest value. The loss-bank / options /
notes lines are out of scope (they are not eod_price readers).
