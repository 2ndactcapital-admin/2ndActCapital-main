-- edgarops1.structural — the nightly download of NEW selected filings.
--
-- The Render cron tick (apps/api/workflow_scheduler_tick.py) queues the day's
-- list as ONE portfolio.edgar_pipeline_runs row: run_kind 'bulk_fetch',
-- trigger_source 'schedule', details.auto_download.date = the UTC day
-- (services/edgar_auto_download.py). One per day, enforced by the database:
-- the INSERT is ON CONFLICT DO NOTHING against this partial unique index, so a
-- second tick, a restart or two overlapping processes queue nothing. Same
-- pattern as edgar_pipeline_runs_schedule_day_uq (bulkfetchui3). No column,
-- CHECK or policy changes: every value used already passes the existing CHECKs
-- (run_kind bulk_fetch, trigger_source schedule, target render/laptop, status
-- queued/running/succeeded/failed/stopped/cancelled).

CREATE UNIQUE INDEX IF NOT EXISTS edgar_pipeline_runs_auto_download_day_uq
    ON portfolio.edgar_pipeline_runs (((details->'auto_download'->>'date')))
    WHERE trigger_source = 'schedule' AND run_kind = 'bulk_fetch'
      AND (details->'auto_download'->>'date') IS NOT NULL;

-- The day's list reads "selected AND loaded_at >= the switch-on cutoff". With no
-- index on loaded_at that scanned every selected row (measured 2026-11-13: 2.9 s
-- warm, 8.4 s cold over 524,442 selected rows). loaded_at is set once at INSERT
-- and never updated, so this index costs nothing on the status updates.
CREATE INDEX IF NOT EXISTS edgar_index_filings_loaded_at_idx
    ON portfolio.edgar_index_filings (loaded_at);
