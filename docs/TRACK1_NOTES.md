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

## Task 2 — schema + service (2026-11-12)
- `docs/track1_part1.sql` — APPLIED (`scripts/_apply_track1.py --all` → SCHEMA OK; all three tables empty).
- `services/security_data/tracking.py` — track / untrack (ids), by filter (THE master predicates,
  `security_master_summary._where`, re-evaluated inside the write and required to equal the confirmed count), by
  named list; estimate; progress; list upsert. One `audit_log` row per action on the platform org
  (`security_data.tracked` / `security_data.untracked` / `security_data.list_loaded`), the BUGFIX4 pattern.
- Cap **3,000** per action (track and untrack): the largest intended list (Russell 2000, ~2,000) fits; 3,000 is 1.5 days
  of the free source's budget and already beyond what it can keep current daily; the broad filters a slip would select
  (all equities 8,666 · all funds 6,133) are refused with the count. An EMPTY filter is refused (the whole master).
- `prices.coverage_set` reads live `tracked` rows as the reason **'tracked'** (an `OPTIONAL_SOURCES` entry — read when
  the table exists; platform-wide, the test-org rule does not apply). Existing reasons untouched.
- `prices.nightly`: gap fill stalest-first; while new members wait it takes at most half the remaining deadline.
  `summary.tracked_progress` ("1,240 of 2,700 loaded") on the nightly and the backfill's run rows.
- Routes (`routers/security_master.py`, all `require_super_admin` first): GET `/tracked`, POST `/tracked/estimate`,
  `/tracked/track`, `/tracked/untrack`; the master list gains `q`, `exchange`, `category`, `tracked`, `in_list` and per-row
  `tracked` / `has_price`. `GET /market/securities/search` gains a `tracking` block (can_track = Super Admin, else the
  disabled reason; which results are tracked; the note that tracking does not by itself make a security selectable
  there).

## Task 3 — list loader (2026-11-12)
- Data file `docs/security_lists/security_lists_v1.json` (DRAFTED 2026-11-12, `reviewed_on: null` — operator review owed):
  Core ETFs 42 · Fixed income 27 · Commodities 12 · Key benchmarks 16 (incl. SPY / AGG / ACWI) · S&P 500 (SPY via
  ssga_spdr) · Russell 2000 (IWM via sec_nport). Each list's `overlap_note` names the FRED / Market Indicators series it
  does NOT duplicate (Treasury yields, TIPS real yield, credit spreads, WTI / Brent / Henry Hub / gold / copper spot,
  index levels).
- Resolution: `services/security_data/security_lists.py`; operator CLI `scripts/security_lists_load.py`.
- READ-ONLY dry run against the live master (2026-11-12, no network, nothing written):
  Core ETFs 42/42 · Fixed income 27/27 · Commodities 12/12 · Key benchmarks 16/16 (0 misses on the curated lists) ·
  S&P 500 **503 resolved, 1 miss** (`2602335D CONTRA HOLOGIC` — a contra / CVR line, correctly a miss) from the live
  Replication Studio SPY snapshot as of 2026-11-11 · Russell 2000: "would fetch IWM's holdings from sec_nport" (no
  snapshot; network = the real run). All lists: 562 distinct; 539 need history = 539 calls = 1 day at 2,000/day; upkeep
  572 calls / day — WARNING: the nightly refreshes ~128 a night, each refreshed every 5 nights (run the backfill to
  catch up).
- [FIND] the first dry run had **31** S&P 500 misses: non-US-domiciled constituents (Linde, Accenture, Chubb, Medtronic,
  Eaton …) carry G- / N- CUSIPs the master lacks, and Replication Studio's resolver uses the ticker only when a row has
  NO strong identifier. Lists now fall back to the ticker when the strong id misses (ambiguous tickers still refused);
  30 recovered. Replication Studio's own (stricter, replica-safe) rule is unchanged.
- [FIND] the master's 13 `index` securities carry Bloomberg-style tickers (SPX, RTY, NDX …) the free source cannot
  price, so "Key benchmarks" tracks ETF proxies.
