# Security data layer — design (MARKETDATA1, 2026-10-28)

One vendor-neutral layer for **security** data: end-of-day prices, corporate
actions, fund holdings snapshots, index levels / rates, option quotes (fx is a
reserved type with no source yet). Readers ask for a **data type**; they never
name a vendor. No paid vendor is connected. A vendor is a plug-in behind the
same interface, switched on by configuration (Joe, 2026-10-28: do not
architect around one vendor, do not pay for one until a real use case needs it).

This is **separate on purpose** from the mkt01–03 indicator history
(`services/market_data/`, `docs/MARKET_DATA_DESIGN_V1.md`): that store, its read
gate (`market_data/access.py`) and its nightly (`hollisworks-market-data-nightly`)
are unchanged. The layer reuses `adapters.scrub_error`, `fred.FredClient`
(injectable transport, `Throttle`, retry statuses) and
`ingest.parse_observations` as-is.

## Code

| Module | Role |
|---|---|
| `services/security_data/layer.py` | `get`, `get_bulk`, `history` — THE read path (the rank walk) |
| `services/security_data/registry.py` | providers, ranks, entitlements, **the licence rule** |
| `services/security_data/settings.py` | staleness per type, disagreement / outlier / gap settings |
| `services/security_data/quality.py` | pure quality rules (both edges provable without a DB) |
| `services/security_data/writes.py` | Rule-3 writes, call log, budgets, quality events |
| `services/security_data/uploads.py` | CSV uploads (rep3 machinery, maker-checker) |
| `services/security_data/nightly.py` + `scripts/security_data_nightly.py` | the nightly run |
| `services/security_data/admin.py` + `routers/security_data.py` | admin screens' API |
| `services/security_data/providers/<key>.py` | one module per provider |

## Stores (schema `docs/marketdata1_part1.sql`, applied)

| Data type | Store |
|---|---|
| eod_price | `portfolio.securities_global_prices` (+ provider_key, as_of, quality_flags, licence, org_id, upload_id; the live-point unique gains provider + org) |
| corporate_action | `portfolio.securities_global_corporate_actions` (Portfolio F's table + the same columns; `cash_dividend` / `stock_dividend` admitted — recorded, never applied) |
| index_level | `portfolio.index_levels` (new) |
| fund_holdings | `portfolio.replica_holdings_snapshots` (rep1; + provider_key, licence) |
| option_quote | `portfolio.adviser_quotes` (rep8, unchanged) |
| eod_price (custodian) | never stored — read from `portfolio.positions` at the call |

Registry tables: `data_providers` (platform), `data_source_rank` (platform
default + org override), `data_provider_entitlements` (org), `data_uploads`
(org, system axis), `data_quality_events`, `provider_call_log` (insert-only),
`security_data_runs` (insert-only summary). Every table has a policy for every
operation; an org's uploaded rows are visible to that org only.

## Providers today

| Key | Kind | Types | Internal | Members | Notes |
|---|---|---|---|---|---|
| custodian | custodian | eod_price | yes | own holdings only | market value ÷ quantity on the position date |
| csv_upload | csv_upload | eod_price, corporate_action, index_level | per file | per file | the uploader's licence governs |
| adviser_entry | adviser_entry | option_quote, index_level, eod_price | yes | no | rep8 quotes (premium / index_level / underlying_price) |
| platform_entry | adviser_entry | eod_price | yes | no | A1 `add_price`; rows with no provider read as this |
| ssga_spdr | issuer_file | fund_holdings | yes | no | rep1 parser re-homed |
| sec_nport | regulator_filing | fund_holdings | yes | yes | rep1 N-PORT re-homed |
| fred | public_api | index_level | yes | no | Treasury curve + fed funds, existing FRED key |
| vendor_slot | vendor | (all) | no | no | **not connected**, no network, in no rank |
| yahoo_chart | public_api | eod_price | yes | **no** | PRICES1 primary free EOD source (Yahoo terms: personal use — internal under the owner's assumption; a launch blocker) |
| tiingo | public_api | eod_price | yes | no | PRICES1 fallback — **not connected** until `TIINGO_API_KEY` exists |
| treasury_fiscal | public_api | index_level, reference | yes | yes | CASH03 — Treasury Fiscal Data auctions (bills / notes: CUSIP, maturity, auction yield); public domain; **not connected** until a Super Admin switches it on |
| sec_nmfp | regulator_filing | index_level, reference | yes | yes | CASH03 — SEC Form N-MFP (money-market fund 7-day yields, WAM / WAL, category) via the EDGAR index + edgar_fetch; **not connected** until switched on |

Platform rank: eod_price custodian → csv_upload → adviser_entry → platform_entry → yahoo_chart → tiingo (PRICES1);
corporate_action csv_upload; fund_holdings ssga_spdr → sec_nport; index_level
fred → csv_upload → adviser_entry → treasury_fiscal → sec_nmfp (CASH03); option_quote adviser_entry; reference … →
sec_mutual_funds → treasury_fiscal → sec_nmfp (CASH03).

## The rank walk

For each subject, providers in rank order (the org's override for that type
if it has one, else the platform's). A provider is **skipped**, with its reason
in `tried`, when: not active; needs an entitlement the org lacks; no module or
capability; its licence (or the value's own licence) does not allow the
purpose; or its value is stale. The first allowed, fresh value wins. Nothing →
`found=False`, `missing` names the data type and every source's reason. **A
price is never invented.** `history()` returns one source's series, never a
splice of several.

## The licence rule

`purpose` is `internal` (adviser analysis) or `member_display`. Internal needs
`internal_use`. Member display needs `member_display = allowed`, or
`own_holdings_only` **and** a value the provider marked as the viewing member's
own holding (the custodian marks it only when the read is scoped to that
member's `account_ids`; unscoped member reads get nothing). A file upload's
value is judged by the file's licence. Recorded per provider: internal use,
member display, redistribution, retention after cancellation, attribution,
terms note + confirmed_on, daily call budget.

## Quality rules

- **stale**: age > the type's limit (eod_price / index_level 5 days, option_quote 1,
  fund_holdings 35; corporate actions never). Exactly at the limit is fresh.
  Setting `security_data.stale_days.<type>`.
- **gap**: more than `security_data.gap_weekdays` (3) weekdays with no point
  between two points.
- **outlier**: |daily move| > `security_data.outlier_multiple` (10) × the median
  |daily move|, with at least 20 moves.
- **disagreement**: another allowed source's value for the same date differs
  from the winner by more than `security_data.disagreement_pct` (2%). The
  winner is kept and flagged.
- **budget_exhausted**: a provider's calls today reached its daily budget. The
  nightly passes the items it could not fetch to the next fetching provider in
  the rank.

## The nightly

`services/security_data/nightly.run_nightly`: refresh within budgets → platform series
quality → org quality for securities on open proposals (test orgs skipped) →
to-dos for stale / disagreeing items → one summary row. Fund holdings stay on
rep1's own daily schedule. **Scheduled since SECMASTER1 as the platform job
`system:Security data — nightly`** (workflow engine, weekdays 19:30 New York) — no
Render cron; `scripts/security_data_nightly.py` remains for a manual run.

## Add a vendor — the 5 steps

(For an END-OF-DAY PRICE vendor use the receptor contract and its checklist below — "PRICES1 — the price receptor".
These five steps are the general shape for the other data types.)

1. **Module**: copy `providers/vendor_stub.py` to `providers/<key>.py`. Implement
   `read()` over the canonical stores (`providers/store.py`, `provider_key = <key>`)
   and `fetch()` over `HttpTransportBase` (the transport is injected; scrub every
   error). Set `connected = True`.
2. **Code registry**: add one line to `providers/__init__.py::build_providers`.
   Put its secret in Doppler, and add the variable name to `adapters.SECRET_ENV_NAMES`
   so it is scrubbed.
3. **Registry row**: insert a `data_providers` row with kind `vendor`, the contract's
   licence facts (internal use, member display, redistribution, retention after
   cancellation, attribution, terms note, confirmed_on), the daily call budget, and
   `requires_entitlement = true`. Then add `data_provider_entitlements` rows for the
   orgs the contract covers.
4. **Rank entry**: Super Admin → Security data → set the platform rank for each type it
   supplies. Orgs may reorder only within that rank.
5. **Prove it**: a verify with recorded fixtures through the injected transport: the
   rank walk picks it up, its licence rule holds for both purposes, and
   budget fallback works. Then switch its status to `active` (the API refuses
   this while the module is not connected).

No reader changes. Readers ask for the data type.

## Reference data — the security master (SECMASTER1, 2026-11-04)

Data type **`reference`**: per-security facts (exchange, cik, sector, industry, industry_group, country, is_etf,
fund_category, listing_status, first_seen, last_seen) stored per (security, field, PROVIDER) in
`portfolio.security_reference_attributes` and read through the rank walk (`security_master_load.read_reference`,
subject `<gsid>:<field>`; never stale on age). Identifiers stay in `securities_global_identifiers`. Schema:
`docs/secmaster1_part1.sql`; discovery: `docs/SECMASTER1_DISCOVERY.md`.

### The five providers (rank for `reference`, in order)

| Key | Source | Supplies | Internal | Members | Redistribution | Budget / day |
|---|---|---|---|---|---|---|
| nasdaq_trader | nasdaqlisted.txt + otherlisted.txt | the listed universe, exchange, ETF flag, listing status | yes | allowed | no | 50 |
| sec_tickers | sec.gov company_tickers_exchange.json | CIK, exchange | yes | allowed | yes (US public record) | 50 |
| openfigi | api.openfigi.com /v3/mapping | FIGI, type, ticker for a CUSIP | yes | allowed | **no** (CUSIP inputs) | 2000 |
| sec_13f_list | SEC Official List of Section 13(f) Securities | CUSIPs (quarterly) | yes | **own holdings only** | **no** (CUSIP rights) | 20 |
| finance_database | github.com/JerBouma/FinanceDatabase CSVs | sector, industry, group, country, fund category | yes | **not allowed** | **no** | 200 |

CUSIP (and the ISIN that embeds it) is CUSIP Global Services' licensed identifier: `identifiers_for_display`
hides both from a member except for their own holdings, by THE licence rule against the 13(f) provider row.
Every sec.gov request goes through `services/edgar_fetch` (declared User-Agent, 10/s). OpenFIGI uses
`OPENFIGI_API_KEY` when Doppler has it (100 jobs / request) and the keyless limit otherwise (10 jobs, 25 / min).

### The matcher (`services/security_master_load.py`)

CUSIP → FIGI (→ ISIN) → ticker + exchange FAMILY (exactly one holder) → new. One transaction per batch, bulk
writes. Nothing existing is overwritten: a disagreement is one open item in `security_master_review`
(ticker_conflict, ambiguous_match, ticker_cusip_conflict, identifier_conflict, type_mismatch, exchange_mismatch,
ticker_moved, 13f_deleted). A ticker that moves to a new CUSIP: the old identifier is retired (valid_to), the new
CUSIP's security holds it. ISIN = 'US' + CUSIP + check digit for a numeric-first CUSIP. Delisting: absent from 3
DISTINCT Nasdaq Trader files → `listing_status` delisted (never deleted; a file newly missing > 20% and > 25
securities is treated as truncated). FinanceDatabase never creates a security. A second run on the same files writes
nothing. A review decision records the decision only; the fix is made on the security.

### The jobs (workflow engine, platform org = Hollisworks; `services/security_master_jobs.py`)

| Workflow | Schedule (America/New_York) | Steps |
|---|---|---|
| system:Security master — nightly | weekdays 18:45 | universe (Nasdaq + SEC → matcher → delisting), openfigi (new identifiers, ≤ 40 requests) |
| system:Security master — 13F list | daily 07:30 | load a new quarter once; map pending CUSIPs (≤ 40 requests) |
| system:Security master — attributes | Saturdays 09:00 | FinanceDatabase |
| system:Security data — nightly | weekdays 19:30 | marketdata1's nightly |

Fired by the 5-minute scheduler tick; each step bounded (200 s, 2000 new securities per source per run, the rest
next run); a failure HOLDS the run and alerts the platform Super Admin; a held run blocks its trigger until a Super
Admin closes it (Security master screen / `POST /admin/security-data/master/runs/{id}/close`). Every step writes a
`security_data_runs` row (`summary.job`) and returns its counts + review items as step output. Run now: Super Admin,
Security master screen or Runs screen. The actions require `run_platform_jobs` — not in the permissions catalog, so
only a Super Admin passes and the assistant never lists them — and refuse to run outside the platform org.

### Operator steps

1. Optional: `OPENFIGI_API_KEY` in Doppler (faster).
2. After deploy: `python3 apps/api/scripts/_apply_secmaster1.py --activate-jobs` (triggers seeded inactive).
3. `secmaster_initial_load.py --dry-run`, then the real load (`--resume` after a crash; `--source X` for one stage).
4. Work the review queue. 5. marketdata1's pending Render-cron step is no longer needed.

## Reference data — SECMASTER2 (2026-11-07): mutual funds, FinanceDatabase fixed, sharper categories, the summary

Schema `docs/secmaster2_part1.sql` (APPLIED 2026-11-07, `scripts/_apply_secmaster2.py --all`); discovery
`docs/SECMASTER2_DISCOVERY.md`.

### The sixth provider (rank 6 for `reference`)

| Key | Source | Supplies | Internal | Members | Redistribution | Budget / day |
|---|---|---|---|---|---|---|
| sec_mutual_funds | sec.gov company_tickers_mf.json | one security per share class: ticker, `sec_series_id`, `sec_class_id`, CIK, `fund_structure` | yes | allowed | yes (US public record) | 20 |

* An existing ticker is MATCHED on the US composite family (`exchange` token 'US', written only on a security this
  source creates) — the file also lists the ETF classes of open-end funds (VTI, VOO, BND, QQQ); their SEC ids are
  added, their listing exchange stays the listing source's. A NEW security is created only for a mutual-fund ticker
  (five letters ending X; `…XX` = money market); other absent classes are unmapped with the reason. A ticker held on
  OTC or by an index → `ticker_conflict` review. Type `fund` (the CHECK's pooled-fund type); the structure is the
  attribute `fund_structure` (mutual_fund / money_market / etf). A new class is named from SEC facts only
  ("FXAIX (SEC share class C000100045)") — FinanceDatabase names are internal-only by licence; a member's asset keeps
  its own name. `sec_class_id` is unique per live row (partial unique index).
* Job **system:Security data — mutual funds** — monthly, the 3rd at 08:00 New York, step `security_master.mutual_funds`
  (≤ 5,000 new per run). Seeded **inactive**; the Super Admin switches it on (Security master screen). secmaster1's
  `_apply_secmaster1.py --activate-jobs` activates only its own four jobs.

### FinanceDatabase (the attributes job's 404)

`etfs/ASE.csv` is not published (the held run of 2026-11-06). `US_ETF_FILES` is now what the repository publishes
(PCX, NGM, NMS, BTS, NYQ, NCM); `funds/NAS.csv` (US mutual funds) is read as kind `funds`; `category_group` is kept as
`fund_category_group` beside `fund_category`. A configured file that 404s, a missing column, or every file parsing to
zero rows raises `SourceFormatChanged` naming the file and the fix: the run HOLDS and the Super Admin is alerted.

### Fund categories (`services/fund_classification.py`)

The map is looked up as "<group> / <category>", then "<group> / *", then the category (Fixed Income / Emerging
Markets → Emerging Market Debt; Derivatives / * → no bucket). An equity key the category leaves coarse
(taxonomy_sc_1, or a US cap key with no region) is split by the fund's own NAME: US ("U.S.", "S&P 500", "Total Stock
Market", "Russell" …) → taxonomy_mc_1_1 / _1_2 by cap; international developed ("International", "EAFE", "Developed
Markets") → taxonomy_mc_1_3 (small cap → sub_1_3_5); global ex-US ("ex US", "Total International", "All World ex") →
taxonomy_sub_1_3_4; "Emerging" → taxonomy_mc_1_4; "Total World" / "Global" → stays taxonomy_sc_1. Every proposal
carries the reason; a contradiction (bitcoin-named, "Corporate Bonds") is REVIEW. `backfill_taxonomy_keys.py --funds`
prints `REFINE old -> new | reason` for an already-keyed fund whose key is an ancestor of the sharper one; applied
only with `--refine`.

### The load summary

GET `/admin/security-data/master/summary` (Super Admin): per type (funds by structure), per identifier type (with
"missing"), per provider, attribute coverage per field, price coverage, the open review queue, the last run per job
— one query per section. Each count carries the filter that lists it: GET `/admin/security-data/master/securities`
(security_type, fund_structure, id_type, missing_id_type, provider, field, missing_field, price_coverage, has_price;
`limit` ≤ 500, `offset`) — the SAME predicates, so a count and its list never disagree. The screen links each number
to its list.

### SECMASTER2 operator steps (in order)

1. **Close the held attributes run** — Security master → Platform jobs → "Close held run" on
   `12611760-722c-40b0-872c-a84fdc8f82a6` (or POST `/admin/security-data/master/runs/{id}/close`). close the held attributes run first: until then it blocks the trigger.
2. Attributes re-load: `doppler run -- apps/api/venv/bin/python apps/api/scripts/secmaster_initial_load.py --source finance_database --dry-run`,
   then the same without `--dry-run` (`--resume` after a crash). Adds the category group and the mutual-fund categories.
3. Mutual-fund load: `... secmaster_initial_load.py --source sec_mutual_funds --dry-run --would-relink 00000000-0000-0000-0000-000000000001`,
   then `... --source sec_mutual_funds` (expect ≈ 22,789 new, 4,609 matched, 1 conflict, 1,247 unmapped).
   Then step 2's real load once more, so the new mutual-fund classes get their FinanceDatabase categories.
4. Categories: `doppler run -- python3 apps/api/scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds --dry-run`,
   then without `--dry-run` (applies `apply` only), then with `--refine` (applies the REFINE lines).
5. Let the nightly relink link the mutual funds (~07:00 UTC; or POST `/portfolio/manual-assets/relink` as an org
   admin) and read its report (People & Access → manual assets → relinks; `portfolio.asset_relinks`). Then re-run
   step 4 (`--funds --dry-run`, then real) so the newly linked funds get their category keys.
6. Household **Recalculate** (or wait for the nightly roll-up).
7. Optional: switch on "Security data — mutual funds" (monthly) on the Security master screen.

## PRICES1 (2026-11-07) — the price receptor, the first free source, the prices job

Discovery `docs/PRICES1_DISCOVERY.md`; schema `docs/prices1_part1.sql` (APPLIED 2026-11-07,
`scripts/_apply_prices1.py --all`: SCHEMA OK).

### The receptor — `providers/eod.py` (THE end-of-day plug-in contract)

An EOD adapter is an `EodPriceProvider` subclass with:

* `spec: EodSpec` — `key` (its data_providers row and every stored price's provider_key), `name`, `keyed_by`
  (`ticker` | `figi` | `cusip` — the security-master identifier it asks by), `max_symbols_per_call`,
  `daily_call_budget`, `min_interval_s`, `history_years`, `gives_adjustment`, `secret_env` (the Doppler name; None =
  keyless) and `licence: EodLicence` (internal use, member display, redistribution, retention after cancellation,
  attribution, terms note — the facts its registry row records).
* `symbol_for(identifiers) -> str | None` — None = it cannot ask for this security (→ `no_public_source`, reason).
* `async fetch_eod(symbols, start, end) -> {symbol: EodSeries}` — network ONLY through `self.http`
  (`HttpTransportBase` over the INJECTED transport); one `EodSeries(found, bars, currency, reason, dropped, events)`
  per requested symbol; `EodBar(day, close = UNADJUSTED close, currency, open?, high?, low?, volume?,
  adjusted_close?, adjustment_factor?)` ascending inside [start, end]; an unknown symbol is `found=False` with the
  source's own reason; a failure of the request as a whole raises `EodSourceError` (the run holds).
* free from the base: `read()` over the canonical store, `capabilities = {eod_price}`, `connected` (keyless, or its
  secret present — the provider admin refuses to activate a not-connected adapter), `scrub()`.
* `contract_problems(adapter)` is the static half of the contract tests; `RecordedHttpGet` the fake transport;
  `RealHttpGet` the production one (the adapter's own headers).

### The free adapters

| Key | Source | Stores | Licence (enforced) |
|---|---|---|---|
| yahoo_chart (primary, active) | `query1.finance.yahoo.com/v8/finance/chart/<ticker>` — keyless; stocks, ETFs, mutual funds, money market; history to listing | the UNADJUSTED close (Yahoo's close is split-adjusted: every request runs to now and later splits in `events.splits` are undone), open / high / low / volume in `bar`, `adjustment_factor` = adjclose ÷ close (6 dp, as of first store) | internal only; member display **not_allowed**; no redistribution; attribution "Prices: Yahoo Finance"; budget 2,000/day |
| tiingo (fallback, not connected) | `api.tiingo.com/tiingo/daily/<ticker>/prices`, key in the `Authorization` header | raw close + adjClose → factor | internal only; not_allowed; retention delete; budget 900/day |

### Store, quality, coverage (`services/security_data/prices.py`, `writes.upsert_eod_bars`, `writes.set_price_coverage`)

* Platform rows (org NULL) in `securities_global_prices`, idempotent by (security, date, provider): an equal close
  writes nothing; a changed close is a **new SYSTEM version** (the live row gets `system_to`). One transaction per
  batch of 25 securities; one batch in memory.
* Quality — never silently stored: zero / negative close → `invalid_value` event, not stored; source currency ≠ the
  security's → `currency_mismatch` event, nothing stored; spike → `outlier` event + the bar stored flagged `outlier`;
  more than 3 weekdays missing → `gap` event; latest bar older than 5 days → `stale` event.
* Coverage: `securities_global.price_coverage` (system-axis archive, id kept) + `portfolio.security_price_coverage`
  (the reason, valid axis): `has_series` "N daily closes A..B from <provider>"; `no_public_source` with the source's
  own answer ("Yahoo has no data for X: …", "the master holds no ticker for it"). A transport failure changes nothing.
* The coverage set (1c): live positions, live model versions, replica entries + their live snapshot constituents,
  option packages / contracts, note replicas, substitution candidates, backtest funds, benchmarks SPY / AGG / ACWI;
  test orgs left out. 29 securities on 2026-11-07.

### Backfill (operator) and the prices job

* `scripts/prices_backfill.py` — `--dry-run` (counts, estimated calls / minutes, days at the budget; no network, no
  write), `--years N` (default 5), `--resume` (checkpoint `scripts/.prices_backfill.<provider>.json` after every
  committed batch), `--provider`, `--batch`, `--gsid`, `--no-budget`; a progress line per batch, a summary per
  provider, one `security_data_runs` row per provider (`summary.job = 'prices_backfill'`); what the first source
  does not find goes to the next.
* Job **system:Security data — prices** (`security_master_jobs.JOBS["security_data_prices"]`, step
  `security_data.prices`): weekdays 21:15 New York (01:15 / 02:15 UTC). Rank order with fallback; gap fill (last 10
  days) for every covered security, full history for up to 200 NEW members a night, within each source's budget and
  a 180 s deadline — the rest next night. A source failure RAISES → the run HOLDS and the Super Admin is alerted.
  Seeded **inactive**; shown on the Security master screen's job switches.

### Readers (Task 7)

All through the layer, never naming a vendor. Roll-up (`networth.value_lines`): a listed units position with an
allowed close NEWER than its own as-of is quantity × close (basis "price", the source and as-of on the line); a
custodian winner is the entered value itself; otherwise bugfix4's entered value. The roll-up reads with the MEMBER
licence by default (members see these figures), so Yahoo never moves a member's number. Drift / proposals
(`account_models.load_holdings`): a fresher close (internal) values the holding, `price_source = 'security_price'`.
X-ray: tracking error vs the approved replica (`tilts.estimate_te`) and the harvest value of switching (the net loss by
character at the latest MARKET close — `layer.get_market`, custodian excluded — × the profile's rates) are computed
with a series, "needs prices" without; a figure whose source forbids member display is hidden from a member and
labelled "internal" for staff (`xray.member_price_filter`, also on stored runs). `GET /security-data/lookup`: a caller
who is not staff is always answered for member display; staff see `display: "internal"`. Tilts, backtest, viability,
blocks, gifts and unheld-security pricing already computed from a series — unchanged.

### Plug FMP (or any vendor) in later

1. `providers/<key>.py`: copy `vendor_stub.VendorTemplateProvider`; set its `EodSpec` (key, keyed_by, budget, the
   CONTRACT's licence facts, `secret_env = "FMP_API_KEY"`) and rewrite `parse` / the request for the vendor's format.
2. One line in `providers/__init__.py::build_providers`; the secret's name in `adapters.SECRET_ENV_NAMES` (FMP's is).
3. One `data_providers` row (`eod.registry_row(spec, status='not_connected')` gives the columns) and its
   `data_source_rank` position for `eod_price` (ahead of yahoo_chart once licensed for members).
4. Prove it: recorded responses through `RecordedHttpGet`, the same contract tests verify_prices1 runs on the
   template; then switch the row to active. Nothing else changes.

### PRICES1 operator steps (in order)

1. Deploy the branch (schema already applied; the job is seeded OFF).
2. `doppler run -- apps/api/venv/bin/python apps/api/scripts/prices_backfill.py --dry-run` (expect ≈ 29 securities,
   29 calls, under a minute).
3. The real run: `doppler run -- apps/api/venv/bin/python apps/api/scripts/prices_backfill.py` (`--resume` after a
   crash). Read the per-provider summary: found / not found, events, coverage.
4. Switch the nightly job on: Security master → Platform jobs → "Security data — prices" → On.
5. Optional: add `TIINGO_API_KEY` to Doppler and set the tiingo row active (the fallback).

## CASH03 (2026-11-09) — two free cash-instrument sources: Treasury Fiscal Data and SEC N-MFP

Registered by `docs/cash03_part1.sql` with licence facts, **switched OFF** (`not_connected`). Reference:
`docs/cash/CASH_LADDER.md` ("Providers and cadence") and `docs/cash/CASH03_NOTES.md` §2–§4.

- **Code.**
  - `services/market_data/treasury_fiscal.py`: parser, injected `SourceTransport`, `refresh` and
    `TreasuryFiscalProvider`.
  - `services/edgar_nmfp.py`: the N-MFP extractor on the EDGAR pipeline (`edgar_index.parse_index_lines(forms=…)`,
    sec.gov through `edgar_fetch._get`) and `NmfpProvider`.
  - Both are in `providers/__init__.build_providers()`.
- **Stores.** No new table.
  - Issues and fund classes go to the security master through `security_master_load.Matcher`. Both are creators;
    N-MFP creates by SEC class id (`CREATE_BY_CLASS_ID`). They bring five new reference fields: `maturity_date`,
    `issue_date`, `security_term`, `treasury_type`, `mmf_category`.
  - Yields go to `portfolio.index_levels`: `tfd:<cusip>:yield`, `nmfp:<class>:7d`, `nmfp:<series>:wam|wal`.
- **Cadence.** The nightly's new `cash_sources` step: Treasury daily, N-MFP monthly from the 8th. It acts only on
  ACTIVE providers. Neither module has `fetches`, so marketdata1's refresh never calls them.
- **Staleness.** `nightly.stale_limit`: Treasury 10 days, N-MFP 45 days; every other provider keeps its data type's
  default.
- **Fixtures.** `apps/api/tests/fixtures/cash03/`, behind `scripts/_cash03_fixtures.py`.

### CASH03 operator steps (in order)

1. Switch the providers on: Admin → Security data → Providers → `treasury_fiscal`, `sec_nmfp` → Active (Super Admin).
2. `doppler run -- python3 apps/api/scripts/load_cash_market_data.py --dry-run`. A real fetch, printed; nothing
   written. Read each parse, above all the first real N-MFP filing's element names and yield scale.
3. `doppler run -- python3 apps/api/scripts/load_cash_market_data.py --real`. N-MFP reads the CIKs of the
   money-market classes already in the master (from the `sec_mutual_funds` sync), or `--cik …`.
4. The nightly then keeps both current.
