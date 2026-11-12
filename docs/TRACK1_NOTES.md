# TRACK1 — "Track this security"

Branch `track1-work` (builds on dailyops1, merged). Goal: a Super Admin adds ANY security-master security to the price
coverage set on purpose (one at a time, in bulk by a server-side filter, or by a named list), so its 5-year daily
history loads and the prices job keeps it current; and the platform's tracked universe is defined as six named lists.

## Task 1 — discovery (2026-11-12)

### What decides who gets prices today
- `services/security_data/prices.py` `coverage_set()` runs `COVERAGE_SQL` (position / model / replica /
  replica_constituent / option_underlying / benchmark) + `OPTIONAL_SOURCES` (note / substitution / backtest), each
  security tagged with its reasons. Benchmarks are the constant `BENCHMARK_TICKERS = ("SPY", "AGG", "ACWI")` — "no
  benchmark table exists". Live: 29 securities (given; not re-derived).
- `scripts/prices_backfill.py` loads N years for the coverage set (checkpointed `--resume`, the registry's daily call
  budget, one `security_data_runs` row per provider with `summary.job = 'prices_backfill'`).
- The platform job "Security data — prices" (`security_master_jobs._prices` → `prices.nightly`): gap fill (last 10
  days) for members that already have a series, then full history for up to `NIGHTLY_MAX_NEW` = 200 NEW members, inside
  `NIGHTLY_DEADLINE_S` = 180 s and the provider's daily budget; the rest next night.
- So a security only has to ENTER `coverage_set` — the backfill and the nightly already pick it up. No new job type is
  needed (design rule "no new job type if the existing platform job + backfill can do it": they can).

### [FIND] the free source's throughput bounds the tracked universe
`yahoo_chart` is per-symbol (`max_symbols_per_call = 1`), `min_interval_s = 1.0`, daily budget 2,000 calls. One call per
security per refresh, whatever the date range.
- Nightly capacity: 180 s / ~1.4 s ≈ **128 securities a night** (gap fill and new history together).
- Daily budget: at most **2,000 securities refreshed a day**, by any path.
- S&P 500 (~503) + Core ETFs + Fixed income + Commodities + Key benchmarks ≈ 650 securities: the backfill loads them in
  one day; the nightly alone refreshes each about every 5 nights.
- Russell 2000 (~2,000) on top: ≈ 2,700 tracked → the full history takes 2 days of budget through the backfill, but
  **keeping 2,700 current daily is impossible on this source** (2,700 calls > the 2,000 budget). A batch-symbol or paid
  vendor (the receptor contract's plug-in) is what lifts this — not a code change here.
- [FIND, fixed in Task 2] the nightly's gap fill walked the coverage set in gsid order and stopped at the deadline, so
  once the set outgrows ~128 the SAME first securities were refreshed every night and the rest never were; and the
  new-history phase ran only after the gap fill had used the whole deadline, so new members would never load. Fixed:
  the gap fill goes stalest-first (the oldest latest close first) and, while new members wait, takes at most half the
  deadline. The estimate the UI shows states the upkeep (calls a day to keep the set current) against the budget and
  the nightly capacity, and warns when either is exceeded.

### Existing constructs — extend or new?
| Construct | What it is | Fits "tracked"? | Fits "named list"? |
|---|---|---|---|
| `portfolio.security_price_coverage` | the OUTCOME of a fetch (has_series / no_public_source + reason), rewritten by every prices run via `writes.set_price_coverage` | No — intent would be overwritten by the next run's finding; status CHECK is the fetch result | No |
| `public.reference_data_lists` | picklist vocabularies (list_key + label, org / owner scope) | No | No — no members-as-securities, no source / as-of |
| `portfolio.sec_13f_list` | the SEC's quarterly 13(f) list (quarter, cusip, has_listed_options) | No | No — one fixed external list with its own columns |
| `portfolio.replica_holdings_snapshots` / `_rows` | one fund's holdings FILE (fund_global_security_id NOT NULL, file_sha256 NOT NULL, R2) | No | Not as storage (curated lists have no file); **REUSED as the source** for the index lists — `replica_holdings.choose_source` / `sec_fund_ids` / `fetch_holdings` / `replica_parsers.parse` / `resolve_rows` |
| `market_data.indicator_series` | FRED / Yahoo indicator series for the Market Indicators page, "separate from security pricing on purpose" | No | No — Treasury yields and commodity spot series already come from FRED here; the curated lists hold ETFs / ETPs, not those series (overlap noted in the data file) |
| `BENCHMARK_TICKERS` | a constant | — | Kept exactly as is (reason 'benchmark' unchanged); the "Key benchmarks" list includes the three |
| `underlying_index_registry` | does not exist (no such table in docs/schema_snapshot.sql) | — | — |
| `portfolio.security_data_runs` | the job history | — | **REUSED** for the loader's run rows and the nightly's tracked progress |
| `audit_log` (platform org, as BUGFIX4's schedule switch) | audit | **REUSED** for track / untrack / list loads | |

Decision: two NEW platform tables, shaped like the rest of `portfolio.*` (platform scope, org NULL, bitemporal, RLS
read-all / Super-Admin-write exactly like `security_price_coverage`):
- `portfolio.tracked_securities` — ONE live row per security (status `tracked` / `untracked`, who, why: a note and/or
  the list it came from). Untrack = valid-axis restatement (the live row closed, an `untracked` row inserted with who /
  why); history kept. `coverage_set` adds live `tracked` rows with reason **'tracked'**.
- `portfolio.security_lists` (+ `portfolio.security_list_members`) — a named list (key, name, source, as-of, misses)
  with its members, keyed by `list_key` (not the list row's id: a refresh restates the list row on the valid axis,
  so members must not FK its id). Members restated only where they change.

### Market Indicators [FIND]
The Market Indicators page's "selectable" means a linked `market_data.indicator_series` (8 today), NOT the price store.
Tracking a security loads `portfolio.securities_global_prices`; it does NOT make it chartable on that page. The
per-security "Track" there queues prices (and says so); linking a market series stays the market-data registry's job.

### UI surface
Super Admin → Security data → Security master (`components/securityData/SecurityMaster.jsx`; routes
`routers/security_master.py`, gate `require_super_admin`). The list behind the summary counts already filters by the ONE
predicate builder `security_master_summary._where` — TRACK1 adds `q` (dailyops1's search predicate: name / short name
contains, ticker / CUSIP prefix), `exchange`, `category` (fund_category), `tracked`, `in_list` to that SAME builder, so
a bulk action's count and the list it acts on can never disagree.
