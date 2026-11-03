-- AIBUDGET1 part 1 — daily model budgets on the ensemble assignment, the shared
-- per-model daily usage ledger, and the daily document-reading runs.
--
-- Applied by apps/api/scripts/_apply_aibudget1.py on the operator DSN, in ONE
-- transaction. Idempotent (IF NOT EXISTS / DROP ... IF EXISTS before CREATE for
-- policies and constraints). Writes NO business rows.
--
-- DESIGN (Task 2, from Task 1's findings)
--   * The budget is part of the assignment: four columns on ai_ensemble_configs.
--     The existing ai_ensemble_configs_retire_only trigger compares
--     to_jsonb(NEW) - is_active - retired_at with OLD, so the new columns are
--     immutable with no change to the trigger: a budget change is a NEW config
--     row through activate_ensemble, the old row retired in the same
--     transaction. A separate versioned budget table would need its own
--     immutability, its own history join and its own one-active rule — the
--     config row already has all three.
--   * ai_daily_usage is a TABLE, not a view over the readings tables: Task 1b
--     found a call path that records no tokens anywhere (held_away's statement
--     reader), and a view cannot hold an in-flight reservation.
--     One row per (budget_day, deployment): the provider's daily allowance is
--     shared by every job that calls that deployment. A running counter of a
--     physical quantity, not a restatable business record: updated in place by
--     an atomic reserve (row lock) and a settle; a past day's row is never
--     touched again. budget_day is the UTC date at the call.
--   * ai_daily_runs: one row per run of a document job. One SCHEDULED run per
--     job per budget day and at most one RUNNING run per job, both enforced by
--     partial unique indexes. Run rows are operational state (status, progress,
--     usage) updated in place while running, like portfolio.note_extraction_runs.
--   * Platform-wide (no org_id), like ai_ensemble_configs: the readings these
--     jobs make are global corpora. Every operation needs app.is_super_admin.

-- ── 1. Budgets on the assignment ───────────────────────────────────────────
ALTER TABLE public.ai_ensemble_configs
    ADD COLUMN IF NOT EXISTS model_1_budget_kind  text NOT NULL DEFAULT 'unlimited',
    ADD COLUMN IF NOT EXISTS model_1_budget_value numeric(20,2),
    ADD COLUMN IF NOT EXISTS model_2_budget_kind  text NOT NULL DEFAULT 'unlimited',
    ADD COLUMN IF NOT EXISTS model_2_budget_value numeric(20,2);

ALTER TABLE public.ai_ensemble_configs DROP CONSTRAINT IF EXISTS ai_ensemble_configs_budget_kind_chk;
ALTER TABLE public.ai_ensemble_configs ADD CONSTRAINT ai_ensemble_configs_budget_kind_chk CHECK (
    model_1_budget_kind IN ('unlimited', 'tokens_per_day', 'usd_per_day')
    AND model_2_budget_kind IN ('unlimited', 'tokens_per_day', 'usd_per_day'));

-- value NULL iff unlimited
ALTER TABLE public.ai_ensemble_configs DROP CONSTRAINT IF EXISTS ai_ensemble_configs_budget_value_null_chk;
ALTER TABLE public.ai_ensemble_configs ADD CONSTRAINT ai_ensemble_configs_budget_value_null_chk CHECK (
    (model_1_budget_kind = 'unlimited') = (model_1_budget_value IS NULL)
    AND (model_2_budget_kind = 'unlimited') = (model_2_budget_value IS NULL));

-- positive otherwise
ALTER TABLE public.ai_ensemble_configs DROP CONSTRAINT IF EXISTS ai_ensemble_configs_budget_value_positive_chk;
ALTER TABLE public.ai_ensemble_configs ADD CONSTRAINT ai_ensemble_configs_budget_value_positive_chk CHECK (
    (model_1_budget_value IS NULL OR model_1_budget_value > 0)
    AND (model_2_budget_value IS NULL OR model_2_budget_value > 0));

-- a token allowance is a whole number of tokens
ALTER TABLE public.ai_ensemble_configs DROP CONSTRAINT IF EXISTS ai_ensemble_configs_budget_tokens_whole_chk;
ALTER TABLE public.ai_ensemble_configs ADD CONSTRAINT ai_ensemble_configs_budget_tokens_whole_chk CHECK (
    (model_1_budget_kind <> 'tokens_per_day' OR model_1_budget_value = trunc(model_1_budget_value))
    AND (model_2_budget_kind <> 'tokens_per_day' OR model_2_budget_value = trunc(model_2_budget_value)));

-- ── 2. The shared per-model daily usage ledger ─────────────────────────────
CREATE TABLE IF NOT EXISTS public.ai_daily_usage (
    budget_day       date          NOT NULL,
    deployment       text          NOT NULL,
    input_tokens     bigint        NOT NULL DEFAULT 0,
    output_tokens    bigint        NOT NULL DEFAULT 0,
    cost_usd         numeric(18,8) NOT NULL DEFAULT 0,
    reserved_tokens  bigint        NOT NULL DEFAULT 0,
    reserved_usd     numeric(18,8) NOT NULL DEFAULT 0,
    calls            integer       NOT NULL DEFAULT 0,
    created_at       timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ai_daily_usage_pkey PRIMARY KEY (budget_day, deployment),
    CONSTRAINT ai_daily_usage_deployment_chk CHECK (btrim(deployment) <> ''),
    CONSTRAINT ai_daily_usage_nonneg_chk CHECK (
        input_tokens >= 0 AND output_tokens >= 0 AND cost_usd >= 0
        AND reserved_tokens >= 0 AND reserved_usd >= 0 AND calls >= 0)
);

-- ── 3. Daily document-reading runs ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ai_daily_runs (
    id                    uuid        NOT NULL DEFAULT gen_random_uuid(),
    task_key              text        NOT NULL,
    budget_day            date        NOT NULL,
    trigger               text        NOT NULL,
    status                text        NOT NULL DEFAULT 'running',
    ensemble_config_id    uuid,
    model_1               text,
    model_2               text,
    stopped_model         text,
    stopped_budget_kind   text,
    stopped_budget_value  numeric(20,2),
    stopped_used          numeric(20,8),
    stop_detail           text,
    documents_done        integer     NOT NULL DEFAULT 0,
    documents_failed      integer     NOT NULL DEFAULT 0,
    failed_items          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    usage                 jsonb       NOT NULL DEFAULT '{}'::jsonb,
    started_by            uuid,
    started_at            timestamptz NOT NULL DEFAULT now(),
    finished_at           timestamptz,
    heartbeat_at          timestamptz,
    lease_until           timestamptz,
    CONSTRAINT ai_daily_runs_pkey PRIMARY KEY (id),
    CONSTRAINT ai_daily_runs_ensemble_config_id_fkey FOREIGN KEY (ensemble_config_id)
        REFERENCES public.ai_ensemble_configs(id),
    CONSTRAINT ai_daily_runs_started_by_fkey FOREIGN KEY (started_by) REFERENCES public.users(id),
    CONSTRAINT ai_daily_runs_task_chk CHECK (btrim(task_key) <> ''),
    CONSTRAINT ai_daily_runs_trigger_chk CHECK (trigger IN ('scheduled', 'manual')),
    CONSTRAINT ai_daily_runs_status_chk CHECK (
        status IN ('running', 'finished_queue_empty', 'stopped_budget', 'stopped_error')),
    CONSTRAINT ai_daily_runs_finished_chk CHECK ((status = 'running') = (finished_at IS NULL)),
    CONSTRAINT ai_daily_runs_budget_stop_chk CHECK (
        (status = 'stopped_budget') = (stopped_model IS NOT NULL)),
    CONSTRAINT ai_daily_runs_stop_kind_chk CHECK (
        stopped_budget_kind IS NULL OR stopped_budget_kind IN ('tokens_per_day', 'usd_per_day')),
    CONSTRAINT ai_daily_runs_counts_chk CHECK (documents_done >= 0 AND documents_failed >= 0),
    CONSTRAINT ai_daily_runs_manual_by_chk CHECK (trigger = 'scheduled' OR started_by IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS ai_daily_runs_one_scheduled_per_day
    ON public.ai_daily_runs (task_key, budget_day) WHERE trigger = 'scheduled';
CREATE UNIQUE INDEX IF NOT EXISTS ai_daily_runs_one_running_per_task
    ON public.ai_daily_runs (task_key) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS ai_daily_runs_task_day
    ON public.ai_daily_runs (task_key, budget_day DESC, started_at DESC);

-- ── 4. RLS: a policy for every operation, super-admin context only ─────────
ALTER TABLE public.ai_daily_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_daily_runs  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ai_daily_usage_read   ON public.ai_daily_usage;
DROP POLICY IF EXISTS ai_daily_usage_insert ON public.ai_daily_usage;
DROP POLICY IF EXISTS ai_daily_usage_update ON public.ai_daily_usage;
DROP POLICY IF EXISTS ai_daily_usage_delete ON public.ai_daily_usage;
CREATE POLICY ai_daily_usage_read ON public.ai_daily_usage FOR SELECT
    USING (current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY ai_daily_usage_insert ON public.ai_daily_usage FOR INSERT
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY ai_daily_usage_update ON public.ai_daily_usage FOR UPDATE
    USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY ai_daily_usage_delete ON public.ai_daily_usage FOR DELETE
    USING (current_setting('app.is_super_admin', true) = 'true');

DROP POLICY IF EXISTS ai_daily_runs_read   ON public.ai_daily_runs;
DROP POLICY IF EXISTS ai_daily_runs_insert ON public.ai_daily_runs;
DROP POLICY IF EXISTS ai_daily_runs_update ON public.ai_daily_runs;
DROP POLICY IF EXISTS ai_daily_runs_delete ON public.ai_daily_runs;
CREATE POLICY ai_daily_runs_read ON public.ai_daily_runs FOR SELECT
    USING (current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY ai_daily_runs_insert ON public.ai_daily_runs FOR INSERT
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY ai_daily_runs_update ON public.ai_daily_runs FOR UPDATE
    USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY ai_daily_runs_delete ON public.ai_daily_runs FOR DELETE
    USING (current_setting('app.is_super_admin', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_daily_usage TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_daily_runs  TO app_service;
