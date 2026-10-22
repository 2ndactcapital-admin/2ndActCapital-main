-- bulkfetchui3.structural — the weekday automatic EDGAR index refresh.
--
-- The Render cron tick (apps/api/workflow_scheduler_tick.py) runs
-- services.edgar_index_schedule.maybe_refresh_index once per weekday after
-- 10:30 UTC and records a portfolio.edgar_pipeline_runs row with run_kind
-- 'refresh_index' and trigger_source 'schedule'.
--
-- 1. trigger_source gains 'schedule'. Every existing value is kept (extend,
--    never drop a value in use): nightly, manual, cli, verify + schedule.
-- 2. One scheduled refresh per UTC day, enforced by the database, not by the
--    tick: the tick INSERTs its run row with ON CONFLICT DO NOTHING against
--    this partial unique index, so a second tick, a restart or two overlapping
--    processes the same day insert nothing and do nothing. A FAILED run still
--    holds its day (the failure is on the row); tomorrow is a different key.

ALTER TABLE portfolio.edgar_pipeline_runs DROP CONSTRAINT IF EXISTS edgar_pipeline_runs_trigger_source_check;
ALTER TABLE portfolio.edgar_pipeline_runs ADD CONSTRAINT edgar_pipeline_runs_trigger_source_check
    CHECK (trigger_source = ANY (ARRAY['nightly'::text, 'manual'::text, 'cli'::text, 'verify'::text,
                                       'schedule'::text]));

CREATE UNIQUE INDEX IF NOT EXISTS edgar_pipeline_runs_schedule_day_uq
    ON portfolio.edgar_pipeline_runs ((details->>'schedule_date'))
    WHERE trigger_source = 'schedule' AND run_kind = 'refresh_index';
