-- bulkfetchui.structural — QUEUED bulk-download runs, executed by a separate worker.
--
-- The API never runs a download itself (Render free plan: spins down, kills
-- long jobs). The "Bulk download" screen QUEUES a row here; a worker process
-- (`run_bulk_fetch.py --worker --target laptop|render`) claims the oldest queued
-- row for its target with FOR UPDATE SKIP LOCKED and runs it through the SAME
-- services.edgar_bulk.Runner the CLI uses.
--
-- WHY EXTEND edgar_pipeline_runs (Task 1b) rather than add edgar_fetch_runs:
-- it already IS the run record — the bulk CLI's Runner writes its rows here
-- (trigger_source 'cli', details.bulk / details.bulk_result), and it already has
-- requested_by, started_at, finished_at, error, the per-status counters and the
-- global-read / super-admin-write RLS. A second run table would split the
-- "Recent runs" history in two.
--
-- Every existing CHECK value is kept (extend, never drop a value in use):
--   run_kind: fetch, extract                     + bulk_fetch, refresh_index
--   status:   launching, launched, launch_failed,
--             refused, running, succeeded, failed + queued, stopped, cancelled
--
-- target is NULLABLE with no column default ON PURPOSE: the nightly / cohort
-- runs (run_kind 'fetch', Render one-off jobs) predate it and are not queued,
-- and a DB default of 'laptop' would stamp every future nightly row 'laptop' —
-- wrong. The 'laptop' default lives in the API (POST /admin/edgar/bulk/runs);
-- a CHECK makes target REQUIRED for the two queued kinds.

ALTER TABLE portfolio.edgar_pipeline_runs DROP CONSTRAINT IF EXISTS edgar_pipeline_runs_run_kind_check;
ALTER TABLE portfolio.edgar_pipeline_runs ADD CONSTRAINT edgar_pipeline_runs_run_kind_check
    CHECK (run_kind = ANY (ARRAY['fetch'::text, 'extract'::text, 'bulk_fetch'::text, 'refresh_index'::text]));

ALTER TABLE portfolio.edgar_pipeline_runs DROP CONSTRAINT IF EXISTS edgar_pipeline_runs_status_check;
ALTER TABLE portfolio.edgar_pipeline_runs ADD CONSTRAINT edgar_pipeline_runs_status_check
    CHECK (status = ANY (ARRAY['launching'::text, 'launched'::text, 'launch_failed'::text, 'refused'::text,
                               'running'::text, 'succeeded'::text, 'failed'::text,
                               'queued'::text, 'stopped'::text, 'cancelled'::text]));

ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS target text;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS params jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS queued_at timestamptz;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS worker_id text;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS worker_host text;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS stop_requested boolean NOT NULL DEFAULT false;
ALTER TABLE portfolio.edgar_pipeline_runs ADD COLUMN IF NOT EXISTS progress jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE portfolio.edgar_pipeline_runs DROP CONSTRAINT IF EXISTS edgar_pipeline_runs_target_check;
ALTER TABLE portfolio.edgar_pipeline_runs ADD CONSTRAINT edgar_pipeline_runs_target_check
    CHECK (target IS NULL OR target = ANY (ARRAY['laptop'::text, 'render'::text]));
ALTER TABLE portfolio.edgar_pipeline_runs DROP CONSTRAINT IF EXISTS edgar_pipeline_runs_queued_target_check;
ALTER TABLE portfolio.edgar_pipeline_runs ADD CONSTRAINT edgar_pipeline_runs_queued_target_check
    CHECK (run_kind NOT IN ('bulk_fetch', 'refresh_index') OR target IS NOT NULL);

-- The worker's claim: oldest queued run for its target.
CREATE INDEX IF NOT EXISTS edgar_pipeline_runs_queued_idx
    ON portfolio.edgar_pipeline_runs (target, queued_at) WHERE status = 'queued';
-- "Is a queued-kind run running anywhere?" (one bulk run at a time across workers)
CREATE INDEX IF NOT EXISTS edgar_pipeline_runs_running_kind_idx
    ON portfolio.edgar_pipeline_runs (run_kind, heartbeat_at) WHERE status = 'running';

-- Worker heartbeats: one row per worker process. GET /admin/edgar/bulk/workers
-- reads the newest heartbeat per target. Global, super-admin write — the same
-- policy set as edgar_pipeline_runs.
CREATE TABLE IF NOT EXISTS portfolio.edgar_fetch_workers (
    worker_id       text PRIMARY KEY CHECK (btrim(worker_id) <> ''),
    target          text NOT NULL CHECK (target IN ('laptop', 'render')),
    host            text NOT NULL,
    pid             integer,
    started_at      timestamptz NOT NULL DEFAULT now(),
    heartbeat_at    timestamptz NOT NULL DEFAULT now(),
    current_run_id  uuid REFERENCES portfolio.edgar_pipeline_runs(id) ON DELETE SET NULL,
    stopped_at      timestamptz,
    details         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS edgar_fetch_workers_target_heartbeat_idx
    ON portfolio.edgar_fetch_workers (target, heartbeat_at DESC);

ALTER TABLE portfolio.edgar_fetch_workers ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = 'edgar_fetch_workers'
                 AND policyname = 'edgar_fetch_workers_global_read') THEN
    CREATE POLICY edgar_fetch_workers_global_read ON portfolio.edgar_fetch_workers FOR SELECT USING (true);
    CREATE POLICY edgar_fetch_workers_super_admin_insert ON portfolio.edgar_fetch_workers FOR INSERT
        WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY edgar_fetch_workers_super_admin_update ON portfolio.edgar_fetch_workers FOR UPDATE
        USING (current_setting('app.is_super_admin', true) = 'true')
        WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY edgar_fetch_workers_super_admin_delete ON portfolio.edgar_fetch_workers FOR DELETE
        USING (current_setting('app.is_super_admin', true) = 'true');
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_fetch_workers TO app_service;

-- Preview / institutions speed (Task 4: under 2 s on the live 717K table).
-- GET /admin/edgar/bulk/institutions counts SELECTED rows per filer CIK per year
-- across every issuer; without this it is a 10.1 s sequential scan (measured
-- 2026-10-18, EXPLAIN ANALYZE). A narrow partial index on exactly the counted
-- rows turns it into an index-only scan.
CREATE INDEX IF NOT EXISTS edgar_index_filings_selected_count_idx
    ON portfolio.edgar_index_filings (primary_issuer_cik, form_type, filing_date)
    WHERE pipeline_status = 'selected';
