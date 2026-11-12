# PRICES2 — the nightly price refresh keeps the whole coverage set current

Branch `prices2-work`. Sprint prompt: `sprint_prompts/prices2.structural.md`.

## 1. Discovery (Task 1)

### 1a. What runs today

* `services/security_data/prices.py` `nightly()` — ONE pass per call: the coverage set (`coverage_set`), per adapter in
  the platform rank (`eod_rank`): gap fill (`GAP_FILL_DAYS = 10` calendar days) for every covered security that has a
  stored close (stalest-first since TRACK1), full history (`DEFAULT_YEARS = 5`) for up to `NIGHTLY_MAX_NEW = 200` new
  members, all inside `NIGHTLY_DEADLINE_S = 180` s and the adapter's daily budget (`_budget` → `writes.budget_left`,
  i.e. `data_providers.daily_call_budget` − today's `provider_call_log` sum). What is not reached is simply dropped
  until the next night. A source failure raises `PricesJobError` after storing what it could.
* The job: `security_master_jobs.JOBS["security_data_prices"]` → action `security_data.prices` → `_prices` →
  `P.nightly(ctx.pool, run_id=…)`. A workflow Service Task inside the 200 s step deadline (`STEP_DEADLINE_S`). It
  RAISES on a source failure → the engine HOLDS the run and alerts the Super Admin; a held run blocks the trigger.
* The schedule (KEPT): cron `15 21 * * 1-5`, America/New_York — weekdays 21:15 New York (01:15 / 02:15 UTC), after
  the 16:00 close and after mutual-fund NAVs publish. Seeded INACTIVE (`_apply_prices1.py --seed-jobs`); the
  operator switches it on.
* The scheduler: `workflow_scheduler_tick.py`, a Render cron every 5 minutes. A scheduled trigger fires ONCE per
  cron occurrence, so today the price job gets exactly one 180 s slice per weekday. The tick entrypoint already
  hosts "once per day, resumable" steps after the workflow tick — the closest pattern is AIBUDGET1's
  `services/ai_daily_runs.py` (one run row per job per day with a partial unique index, a lease claimed by each tick,
  a time slice per tick, progress on the row, `daily_tick` never raises). EDGAR's index refresh (bulkfetchui3)
  shows the once-per-day unique index.
* The provider: `providers/yahoo_chart.py` — `query1.finance.yahoo.com/v8/finance/chart/<symbol>`,
  `max_symbols_per_call = 1`, `min_interval_s = 1.0`, `daily_call_budget = 2000` (registry). One HTTP request per
  security; `provider.calls` counts HTTP requests and `ingest()` already batches symbols by
  `spec.max_symbols_per_call`.
* `tracking.py` (TRACK1): `nightly_capacity(spec, 180) = floor(180 / (1.0 + 0.4)) × 1 = 128`; `estimate_numbers` warns
  "the nightly job refreshes about 128 securities a night; N covered means each is refreshed every ⌈N/128⌉ nights".

### 1b. Multi-symbol requests — NOT available (FIND)

The Yahoo chart endpoint the adapter uses takes ONE symbol per request, and the adapter exposes nothing else
(`max_symbols_per_call = 1`; Tiingo's adapter is also 1). Yahoo does have an unofficial multi-symbol "spark"
endpoint, but it returns closes only — no open / high / low / volume, no `adjclose`, and no `events.splits` — so the
adapter could not restore the UNADJUSTED close it stores (close × Π later splits, its documented contract) nor the
adjustment factor. Using it would store split-adjusted closes beside unadjusted ones. It was also never recorded
(this sprint has no network), so it is NOT built. What IS true and is proven by the verify: the refresh path batches
by `spec.max_symbols_per_call` and records calls as HTTP requests (`provider.calls`), so a licensed vendor with a
multi-symbol endpoint (one adapter file) gets ⌈N / per-call⌉ calls with no further change.

### 1c. Capacity — before / after

Per call ≈ 1.0 s minimum interval + ~0.4 s latency = 1.4 s; one slice = 180 s → **128 securities per slice**.

| | before (one slice per weekday) | after (slices continue every 5-minute tick) |
|---|---|---|
| refreshes per day | 128 | min(covered, daily budget 2,000) |
| 602 covered (today's lists) | each every ⌈602/128⌉ = **5** nights | **all 602 every day**, ⌈602/128⌉ = 5 ticks ≈ 25 min after 21:15 NY |
| ~2,600 covered (with the Russell 2000) | each every ⌈2,600/128⌉ = **21** nights | 2,000 a day (the budget) in ⌈2,000/128⌉ = 16 ticks ≈ 80 min; 600 carried to tomorrow and done FIRST (oldest-first) → nothing older than **2** days (⌈2,600/2,000⌉); held / model / replica / benchmark securities every day |
| binding limit | the 180 s deadline | the provider's daily call budget |

Calls used by new members' full history (≤ 200 a day) and by an operator backfill on the same day come out of the same
2,000-call budget, so on such a day fewer gap fills fit; the carry-over covers them the next day.

**Operator option (NOT done — Joe's call):** raise `yahoo_chart`'s `daily_call_budget` in `portfolio.data_providers`
(e.g. 3,000 would keep ~2,600 current every day at ~110 min of ticks), or move to a licensed vendor with a
multi-symbol endpoint. The code needs no change for either.

## 2. Design (Task 2)

* `portfolio.price_refresh_days` — ONE row per (scope, price_day): status `running` → `complete` (every covered
  security refreshed for the day) / `stopped_budget` (the daily budget is spent; the rest carries over) /
  `stopped_error` (3 consecutive failed slices) / `carried_over` (a new day started while it still ran). A
  lease (`lease_until`) claimed by `UPDATE … WHERE lease_until IS NULL OR lease_until < now()` — two ticks never
  race. Progress columns: covered, refreshed, calls, slices, deferred, errors.
* `portfolio.price_refresh_state` — one row per security: `last_refreshed_on` (the price day it was last refreshed
  — found, or answered "no data" by every source), outcome, provider, run. Operational queue state (like
  `public.ai_daily_runs`), updated in place; `ON DELETE CASCADE` so a verify's fixture securities clean up with it.
* Order (pure `refresh_order`): group (org uses: held positions → models / replicas → benchmarks → other org uses
  first, every day) → `last_refreshed_on` oldest-first (never-refreshed first) → priority tier (core ETFs → fixed
  income → commodities → key benchmarks → S&P 500 → Russell 2000 → other tracked) → latest stored close (stalest
  first, TRACK1) → gsid.
* Start: the 21:15 New York trigger (unchanged) runs `nightly()`, which opens the day's row and does the first slice.
  Continue: a new step in `workflow_scheduler_tick.py` (`prices_tick`) continues the latest `running` day row,
  one slice per tick, only while the job's scheduled trigger is active; it never starts a day and never raises.
* `nightly(only_gsids=…)` without a `scope` (the operator's `--gsid`, PRICES1 / TRACK1 verifies) keeps its old
  one-pass behaviour: no day row, every requested security refreshed.
