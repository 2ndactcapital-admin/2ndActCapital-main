# Render Cron Setup — nightly market data refresh (mkt02)

**This document creates nothing.** It tells the operator how to create the
Render service by hand. The sprint that wrote it did not create a Render
service and did not edit `render.yaml`.

## What the job does

`apps/api/scripts/market_data_nightly.py` re-pulls the full history of every
`active` series in `market_data.indicator_series`, through the adapter
registry (FRED and Yahoo). It writes only what changed, records one batch in
`market_data.indicator_ingest_runs`, and prints a staleness report. It exits
non-zero if any series failed, so Render marks the run failed.

## Before you start

- Run these first, from a laptop, so the six Yahoo series are active:
  `market_data_ingest.py validate --provider yahoo`, then
  `backfill --provider yahoo`. The nightly only refreshes `active` series.
- `FRED_API_KEY` must exist in Doppler `hollisworks` / `prd`.

## Create the service

In the Render dashboard: **New → Cron Job**, connected to this repo.

| Setting | Value |
|---|---|
| Service type | **Cron Job** |
| Name | `2ndactcapital-market-data-nightly` (suggested) |
| Runtime | Python |
| Plan | Starter (the cheapest plan Render allows for a cron job) |
| Root directory | `apps/api` |
| Build command | `pip install -r requirements.txt` |
| Start command | `python scripts/market_data_nightly.py` |
| Schedule | `15 11 * * *` |

These match the two cron jobs already declared in `render.yaml`
(`2ndactcapital-workflow-scheduler`, `2ndactcapital-ai-spend-sync`): same
root directory, same build command, and a `python scripts/<file>.py` start
command.

**Schedule.** Render cron schedules are in **UTC** and cannot use a time
zone. 11:15 UTC is about 7:15am in New York in summer (EDT) and 6:15am in
winter (EST). That is after FRED has posted the prior day's figures.

## Environment variables

The service needs exactly these (names only):

| Name | Why |
|---|---|
| `DATABASE_URL` | the database connection (`services/database.py` reads only this) |
| `FRED_API_KEY` | 57 of the active series come from FRED |

Yahoo needs no key. The script reads `os.environ` only and does not hydrate
from Doppler itself. If either variable is missing, it prints the missing
**names** and exits 2.

**Use a Doppler sync. Do not set variables by hand.**

- **The cron service needs its OWN Doppler → Render sync.** A new Render
  service does not inherit the API service's sync. In Doppler (`hollisworks`
  → `prd` → Integrations), add a Render sync that targets this new service.
- **A sync can overwrite values already on a service.** This has happened
  twice on this project. Anything typed into the Render dashboard before the
  sync runs can be silently replaced, so set nothing by hand.

## Caveat: Yahoo from Render

Yahoo's endpoint is unofficial and may block datacenter IP addresses. The six
Yahoo series can work from your laptop and fail from Render (HTTP 401, 403 or
429). This does **not** affect FRED series: each series is isolated, and a
Yahoo failure only fails that series. It does make the run exit 1. It also
raises one in-app alert per failed batch. The alert goes to the Hollisworks
org's `manage_org_settings` holders, or to `audit_log` if there are none.

The first manual Render run is the test. If Yahoo is blocked there, record it
and decide whether to pause those six series (`ingest_status = 'paused'`).
Otherwise every night will exit 1.

## Post-setup checklist

1. **Variables arrived, by name only.** In the Render dashboard, open the
   service's Environment tab and confirm `DATABASE_URL` and `FRED_API_KEY`
   are listed. Do not reveal or copy the values.
2. **Trigger one manual run** with the service's "Trigger Run" button, then
   read the log. It should show `market data nightly: batch <uuid> ...
   status=success`, the totals, and any `[STALE]` lines. The run should exit
   with code 0.
3. **Confirm the batch in the database.** It needs super-admin scope, because
   `indicator_ingest_runs` is super-admin-only under RLS. For example, use
   the Supabase SQL editor as `postgres`:

   ```sql
   SELECT batch_id, status, rows_inserted, rows_revised, rows_unchanged, error, started_at
     FROM market_data.indicator_ingest_runs
    WHERE series_id IS NULL AND run_trigger = 'nightly'
    ORDER BY started_at DESC
    LIMIT 3;
   ```

   The newest row is the batch summary. Its `error` text holds the batch
   totals (`batch summary: attempted=… succeeded=… failed=…
   skipped_no_adapter=…`). The per-series rows share its `batch_id`.
4. Optional, for the record: add a block for this service to `render.yaml`.
   The manifest's own invariant says every service and variable should be
   declared there. This sprint was told not to edit it.
