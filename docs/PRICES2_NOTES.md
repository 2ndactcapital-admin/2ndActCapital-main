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

## 3. What was built (Tasks 2–3)

* `services/security_data/prices.py`: `nightly()` is now ONE SLICE of the day (day mode) — `open_day` (one row per
  scope + day; a still-running row of an earlier day is closed `carried_over`), `claim_day` (lease; `busy` when held),
  `_slice` (coverage → skip refreshed today → `assign_tiers` → `refresh_order` → ingest in exact order, runs of gap
  fill / new history in chunks of 100, batch ≥ the source's per-call size → `mark_refreshed`), `_record_slice`
  (covered / refreshed READ BACK from the state table, calls, slices, status). New members' history is capped at
  `NIGHTLY_MAX_NEW` (200) per DAY across slices. The job step still raises on a source failure (PRICES1: the run
  holds); `prices_tick` passes `raise_errors=False` and never raises. `FAILED_SLICES_STOP = 3` consecutive failed
  slices stop the day `stopped_error`. One-off mode (`only_gsids` without `scope`) keeps PRICES1's behaviour and
  writes neither a day row nor state.
* `prices_tick` (in `workflow_scheduler_tick.py`, its own try, after the replica holdings step): continues the latest
  running `platform` day row, only while the job's scheduled trigger is active; never starts a day; a running row
  older than 3 days is closed `carried_over`. `PRICES_DAY_TICK=0` turns it off.
* Security master → summary: `GET …/master/summary` publishes `price_refresh` (`prices.refresh_status`): "covered N ·
  refreshed today M · oldest price date D" + the latest day rows; `PriceRefreshLine` renders the server's words only.
* `tracking.estimate_numbers`: the warning now states the DAILY capacity at the budget ("keeps at most 2,000
  securities current a day … each is refreshed every 2 days, oldest first — held, modelled and benchmark securities
  every day"); `refreshes_per_day` / `refresh_every_days` added; `estimate()` counts new history at 200 a DAY.
  `nightly_capacity` (128) is unchanged and now means one slice.

## 4. FINDs

1. **The real "Security data — prices" job is switched ON** (its trigger is active in the platform org), although
   PRICES1 seeded it OFF and `verify_prices1`'s real-rows section asserts OFF — that assertion will fail on its next
   full run because of an operator decision, not this sprint. Consequence: once this branch is deployed, the
   scheduler tick continues real days automatically.
2. **Deleting one security took ~15 s**: five foreign keys onto `portfolio.securities_global(id)` had no usable index
   (`securities_global_prices` 9.5 s — its only `global_security_id` index is partial; `security_reference_attributes`
   2.3 s; `index_levels` 1.9 s; `sec_13f_list` 0.8 s; `data_quality_events` 0.2 s). The first 2,600-security teardown
   timed out and left 2,600 fixture securities; `docs/prices2_part2.sql` (applied, CONCURRENTLY) adds plain indexes —
   100 deletes now take 0.3 s — and the leftovers were removed (0 remain).
3. **A state row written by a one-off run broke `verify_track1`'s teardown** (`_verify_cleanup` refuses an
   org-NULL row it reaches through an FK). Reproduced with `verify_track1 --section n` (teardown FAIL), fixed (one-off
   runs write no state), re-run 44/0.
4. **The order is exact per batch of 25**, not per request: `ingest` asks a batch's symbols sorted by symbol. The
   verify checks every batch is exactly the next 25 of the priority order.
5. A first implementation asked a chunk's gap fills before its new members (out of order) — caught by verify [R]
   (day 2 must take the 3 never-refreshed first) and fixed: runs of the same kind keep the exact order.

## 5. Verify (Task 4)

`apps/api/scripts/verify_prices2.py` (in `scripts/regression_set.txt`), run section by section (`timeout 600`):
self-checks 39/0; `--section d,x` 49/0; `--section m,r` 28/0;
`--section r` (with the one-off proof) 26/0; `--section w,j` 4/0 (render + fast regressions). One-offs:
`verify_prices1 --self-checks-only` 17/0, `verify_track1 --self-checks-only` 34/0, `verify_secmaster2
--self-checks-only` 18/0, `verify_track1 --section n` 44/0.

## 6. Operator note

* Deploy the API AND the scheduler cron (`workflow_scheduler_tick.py`) — the continuation lives in the tick.
  Schema: `_apply_prices2.py --all` and `--fk-indexes` are ALREADY APPLIED (idempotent if re-run).
* What Security master shows (summary → Prices): "covered N · refreshed today M · oldest price date D" and the
  latest day: "2026-11-13: 1,840 of 2,600 refreshed · 1,840 calls · Running" — then "Complete — every covered
  security refreshed", or "Stopped — the daily call budget is spent (the rest goes first tomorrow)" with how many
  were left, or "Stopped — the source failed" with the reason.
* With today's lists (602 covered) the whole set is refreshed every weekday within ~25 minutes of 21:15 New York.
  Once the Russell 2000 loads (~2,600) the 2,000-call budget refreshes 2,000 a day and the rest go first the next
  day (nothing older than 2 days; held / modelled / benchmark securities every day).
* The budget option (Joe's call, NOT done): raise `yahoo_chart`'s `daily_call_budget` in `portfolio.data_providers`
  (≈ 2,700 keeps ~2,600 current daily), or move to a licensed vendor with a multi-symbol endpoint (one adapter file —
  the refresh already batches by `max_symbols_per_call` and counts requests honestly). No code change for either.
* `PRICES_DAY_TICK=0` on the cron stops the continuation (the 21:15 job then does one slice a day, as before).

### 5a. FIX 1 — the 22:17:04–22:17:59 UTC read-only window (Supabase disk auto-grow)

Branch commits (real UTC) run 22:18:23 (Task 1) → 23:43:22; the only step near the window is the migration
(16a1bc6, committed 22:19:43). Re-checked 2026-11-13: `_apply_prices2.py --verify` → SCHEMA OK (both tables, RLS,
four policies each, app_service privileges, unique indexes; 0 rows in each); every PRICES2 index (5 part-1, 5
part-2 FK indexes) `indisvalid` AND `indisready` (10/10). Every one-off run was after 22:30. **None affected.**

### 5b. FIX 1 — proof completed (2026-11-13)

Sections already run one-off during the build leg (all six): d, x, m, r, w, j — none left to run. Re-run now:
`verify_prices2 --cleanup-only` 1/0 (zero fixture rows, nothing removed); `--self-checks-only` 39/0.
Fast-mode regressions (self-checks): `verify_prices1` 17/0, `verify_track1` 34/0, `verify_secmaster2` 18/0.
The full `verify_prices2.py` run is left to the runner (WSL memory) — this leg ran one-offs only.
