# BULKFETCH UI — Task 1 discovery (2026-10-18)

Read from the code on `bulkfetchui-work` and from the live database (project
`mmgwmcinimzuhargsazs`). The branch had been cut before the bulkfetch merge, so
`main` (4af0f9d, bulkfetch + fix1) was merged in first. Before that merge,
`apps/api/scripts/run_bulk_fetch.py` did not exist on this branch.

## 1a. run_bulk_fetch.py — what computes the scope, ETA, cost, and runs a fetch

`scripts/run_bulk_fetch.py` is a thin CLI (argparse, an asyncpg pool, an httpx
client and signal handling). **Every piece of logic is in `services/edgar_bulk.py`,
and the API and a worker loop can import all of it without the CLI:**

| Need | Function (services/edgar_bulk.py) | Notes |
|---|---|---|
| institution → filer entities | `institution_catalog`, `resolve_institutions` | issuer_group → every `structured_note_issuers` CIK (include_status from the source row) |
| scope per institution / entity / year | `scope_counts(conn, Scope)` | one query per institution over `_SCOPE_WHERE`, the same WHERE the claim uses |
| ETA and storage / cost | `estimate(n, rate, concurrency, peek, prelim_share, stored_bytes_now)` + `stored_bytes(conn)` | the larger of the rate bound and the latency bound (measured 1.91 s per filing serially) |
| the dry run | `dry_run(...)`, which prints | **this sprint factors out `plan_scope(...)`**, which returns the same numbers without printing. `dry_run` and the API preview both call it, so the screen and the CLI cannot disagree |
| index-behind refusal | `check_dates_indexed(conn, to)` | raises `BulkUsageError` naming `--refresh-index` |
| run a fetch | `Runner(pool, RunOptions, client=…).run()` | claims, peek, store, progress, run row |

**Reuse in the worker:** `RunOptions` gains `run_id`. When it is set, the Runner
*adopts* the queued row (`adopt_run_row`) instead of inserting one
(`create_run_row`). The Runner also gains two hooks: `on_progress(runner)`,
awaited every `PROGRESS_EVERY` = 100 filings, and `owns_run()`, which fences the
final write. `services/edgar_bulk_queue.run_options_from_params` turns a queued
row's params into the CLI's own `RunOptions`, so there is **no second
implementation**.

## 1b. The run record — portfolio.edgar_pipeline_runs

**The bulk Runner already writes `portfolio.edgar_pipeline_runs`**
(`create_run_row`: trigger_source `cli`, run_kind `fetch`, `details.bulk` /
`details.bulk_result`). No `edgar_fetch_runs` table exists. This sprint EXTENDS
`edgar_pipeline_runs`; a second run table would split the run history in two.

- Columns before: id, trigger_source, requested_by, workflow_run_id, status, stages, fetch_cap,
  runtime_cap_seconds, render_service_id, render_job_id, requested_at, started_at, finished_at,
  discovery_days, discovered_new, selected, not_selected, fetch_attempted, fetched, fetch_failed,
  ready_for_extraction, not_pricing_supplement, prefilter_skipped, bytes_uploaded, sec_requests,
  stop_reason, error, details, cohort_id, run_kind.
- CHECKs live: `status` ∈ {launching, launched, launch_failed, refused, running, succeeded, failed};
  `run_kind` ∈ {fetch, extract}; `trigger_source` ∈ {nightly, manual, cli, verify};
  `fetch_cap >= 0`; `runtime_cap_seconds > 0`.
- RLS on: `edgar_pipeline_runs_global_read` (SELECT true) plus super-admin INSERT / UPDATE / DELETE on
  `current_setting('app.is_super_admin', true) = 'true'`. `edgar_pipeline_lease` follows the same pattern.
- Rows today: 3 (succeeded / fetch / manual).
- **How progress was stored: only at the END.** The counters (fetched, fetch_failed, …,
  bytes_uploaded, sec_requests) and `details.bulk_result` (by_status, by_kind, failures[:50]) are
  written once, in the Runner's `finally`. While a run was going, progress existed only on stdout
  (`_maybe_progress`, every 100 filings). So this sprint adds `progress jsonb`, written every 100 filings
  and on finish (`edgar_bulk.progress_snapshot`: done, remaining, by_kind, by_status, failures,
  bytes, sec_requests, rate, ETA).

Migration `apps/api/migrations/bulkfetchui_queued_runs.sql` (applied live 2026-10-18):
- run_kind adds `bulk_fetch`, `refresh_index`;
- status adds `queued`, `stopped`, `cancelled` (every existing value kept);
- new columns: target, params, queued_at, worker_id, worker_host, heartbeat_at, stop_requested, progress;
- the partial index `edgar_pipeline_runs_queued_idx (target, queued_at) WHERE status = 'queued'`;
- `portfolio.edgar_fetch_workers` (worker heartbeats, same RLS set);
- `edgar_index_filings_selected_count_idx` (see 1a timing below).

**[FIND] target is nullable with no DB default.** Nightly and cohort runs (run_kind `fetch`, Render
one-off jobs) are not queued. A column default of 'laptop' would stamp every future nightly row
'laptop', which is wrong. The 'laptop' default is the API's. A CHECK makes target required for the
two queued kinds.

## 1c. --refresh-index — how it works, how long it takes

`edgar_bulk.refresh_index(conn, client, through=None)`:
1. Start from `last_indexed_date()` = `max(filing_date)`, INCLUSIVE. A day can be part-loaded, and
   inserts are ON CONFLICT DO NOTHING, so a re-read is a no-op.
2. For every weekday through `through` (default yesterday, UTC), call
   `edgar_pipeline.discover_day`. That is ONE SEC request per day:
   `daily-index/{yyyy}/QTR{q}/master.{yyyymmdd}.idx`. A 403/404 counts as "no index that day".
3. Run `select_stage(accessions=<the rows this refresh inserted>)`, so the active policy applies to
   the new rows only.

**Cost today:** the index ends 2026-10-01 (a Thursday). 2026-10-01 .. 2026-10-17 is **12 weekday
index files = 12 SEC requests**, under 2 s at 8/s, plus the inserts and the selection over roughly
a few thousand new 424B2 rows. That is seconds to about a minute, not hours. It was NOT measured
live, because this sprint makes no SEC request. The CLI's `--refresh-index --dry-run` prints the
span and the file count. It is therefore queued as its own run kind, `refresh_index`, with params
`{to}`, executed by the same worker. `refresh_index` loops the days internally, so a stop request
is honoured only between runs, not mid-refresh. It is short enough that this does not matter.

## 1d. The EDGAR Pipeline page — tabs, routing, super-admin gate

- **Route:** `apps/web/app/admin/edgar-pipeline/page.js`, a server component. Host-aware
  `getHostSession()`; it redirects to login with no session. It fetches the first filings page
  server-side; on a 403 it renders "Super Admin access required."
- **Tabs:** client state (`useState`) in `components/admin/EdgarPipelineMonitor.jsx`'s `TABS`
  array. There is no URL routing per tab. Before this sprint the tabs were Filings, Progress,
  Cohorts (`EdgarCohorts.jsx`) and Issuers. This sprint adds **Bulk download**
  (`components/admin/EdgarBulkDownload.jsx`).
- **Data path:** server actions in `lib/edgarPipelineActions.js` → `lib/api.js` `fetchAPI`
  (server-side bearer) → FastAPI. The client never calls FastAPI directly, and there is no org_id
  anywhere.
- **API gate:** `routers/edgar_pipeline_admin.py` `_require_super_admin(request)` →
  `ensure_user` → `rbac.load_principal` → `rbac.is_super_admin(principal)`, else **403 "Super Admin
  access required"**. Every new `/admin/edgar/bulk/*` endpoint calls it first, before any work.
- **Envelope:** `permissions` (`can_write`) + `vocabularies`. The tab's write controls render only
  under `permissions.can_write === true` (`lib/edgarBulkDownload.mjs` `canWrite`).

## 1e. What the API can learn about Render without changing anything

- **Doppler names (values never read):** of the 42 secrets in `hollisworks/prd`, only
  `RENDER_API_KEY` and `EDGAR_USER_AGENT` match RENDER / WORKER / EDGAR / BULK. **No worker service
  id is configured anywhere.** (`EDGAR_PIPELINE_RENDER_SERVICE_ID` is not set either; the nightly
  launcher resolves the workflow-scheduler cron by name.)
- **The read-only call:** `GET https://api.render.com/v1/services/{serviceId}` with
  `Authorization: Bearer $RENDER_API_KEY`. It returns `type` (must be `background_worker`) and
  `suspended` (must be `not_suspended`). Nothing else is needed.
- **Detection used:** the new name `EDGAR_BULK_RENDER_WORKER_SERVICE_ID` (declared in render.yaml on
  the API service, `sync: false`, unset). Unset → "not set up", and a Render queue request is a 409
  with nothing queued. Set → one GET (cached 60 s) plus a Render worker heartbeat in
  `edgar_fetch_workers` within 5 minutes → "online". **No Render call is made in this sprint, and
  no service is created, deployed or modified.**

## Measured: preview and institutions speed (live, read-only EXPLAIN ANALYZE, 2026-10-18)

- **Preview, JPMorgan 2025 selected 424B2** (`scope_counts`' query): **82.7 ms** execution, bitmap
  scan on `edgar_index_filings_primary_issuer_idx`, 18,860 rows. Six banks over two years is about
  12 such queries, roughly 1 s worst case. The verify measures the end-to-end API time.
- **Institutions catalog** (selected counts per CIK per year, every issuer): **10,114 ms** as a
  sequential scan of all 716,953 rows. After the new partial index
  `edgar_index_filings_selected_count_idx (primary_issuer_cik, form_type, filing_date) WHERE
  pipeline_status = 'selected'` it was still **6,534 ms** (537,027 heap fetches: visibility map not
  set). After a plain `VACUUM (ANALYZE)` it is **299 ms** (index-only scan, 0 heap fetches). A live
  bulk run's claims keep dirtying pages, so the endpoint also caches for 60 s.

## Findings

- **[FIND] The lease-based rate division is computed ONCE, at start.** `Runner.run` divides
  `--max-rps` by the number of live leases when it starts and never re-divides. If run A starts
  alone at 8/s and run B joins, B takes 4/s while A keeps 8/s: 12/s combined. This is why queued
  runs execute **one at a time across all workers** (claim refused while any queued-kind run is
  running with a fresh heartbeat, under one advisory lock), not "two workers share 8/s". A manual
  CLI run started next to a worker run still has the pre-existing gap. That is unchanged and
  operator-controlled.
- **[FIND] The CLI closes an operator-stopped run as 'succeeded'** (with stop_reason "stopped by
  operator"), and a 50-consecutive-failure stop the same way. Kept for the CLI. A queued run
  distinguishes them (`Runner._final_status`): operator stop → `stopped`, failure stop → `failed`.
- **[FIND] `_blocking_run` (nightly launch guard) counts 'running' rows** requested in the last 30
  minutes. A worker that claims a queued run within 30 minutes of it being queued makes a nightly
  "Run now" in that window come back `refused`. CLI 'running' rows already behaved this way. A
  'queued' row never blocks.
