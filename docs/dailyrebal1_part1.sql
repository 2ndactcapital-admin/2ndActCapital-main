-- DAILYREBAL1 part 1 — the daily scheduled rebalancer's day row + the 'scheduled' proposal trigger.
-- Additive: one NEW org table; the trigger CHECK on public.rebalance_proposals WIDENED by one value (every existing
-- value kept — no row can fail it). Writes NO proposal, account, org or setting row.
-- Applied by apps/api/scripts/_apply_dailyrebal1.py --schema on the operator DSN (the table owner). Idempotent.
-- (no BEGIN / COMMIT here: the apply script runs the whole file in ONE transaction)
--
-- OPERATIONAL STATE, updated in place (like portfolio.price_refresh_days / public.ai_daily_runs): one row per org per
-- New York business day — readiness, a lease, counts, per-account results with reasons, errors. Not reference data;
-- the proposals it makes are the bi-temporal record (public.rebalance_proposals).

-- 1. ONE row per (org, run_day).
CREATE TABLE IF NOT EXISTS public.rebalance_schedule_days (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  run_day               date NOT NULL,
  status                text NOT NULL DEFAULT 'waiting',
  is_fixture            boolean NOT NULL DEFAULT false,
  run_time_local        text NOT NULL,
  prices_day            date,
  prices_status         text,
  partial_prices        boolean NOT NULL DEFAULT false,
  oldest_price_date     date,
  nightly_ok            boolean NOT NULL DEFAULT false,
  waiting_reason        text,
  checked               integer NOT NULL DEFAULT 0,
  proposed              integer NOT NULL DEFAULT 0,
  skipped               integer NOT NULL DEFAULT 0,
  failed                integer NOT NULL DEFAULT 0,
  results               jsonb NOT NULL DEFAULT '[]'::jsonb,
  errors                jsonb NOT NULL DEFAULT '[]'::jsonb,
  todo_ids              uuid[] NOT NULL DEFAULT '{}'::uuid[],
  lease_until           timestamptz,
  attempts              integer NOT NULL DEFAULT 0,
  first_seen_at         timestamptz NOT NULL DEFAULT now(),
  started_at            timestamptz,
  finished_at           timestamptz,
  CONSTRAINT rebalance_schedule_days_status_chk CHECK (status IN ('waiting', 'running', 'completed', 'failed')),
  CONSTRAINT rebalance_schedule_days_finished_chk CHECK (
    (status IN ('completed', 'failed')) = (finished_at IS NOT NULL)),
  CONSTRAINT rebalance_schedule_days_partial_chk CHECK (NOT partial_prices OR status <> 'waiting'),
  CONSTRAINT rebalance_schedule_days_counts_chk CHECK (checked >= 0 AND proposed >= 0 AND skipped >= 0 AND failed >= 0
                                                       AND attempts >= 0),
  CONSTRAINT rebalance_schedule_days_json_chk CHECK (jsonb_typeof(results) = 'array' AND jsonb_typeof(errors) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS rebalance_schedule_days_day_uq ON public.rebalance_schedule_days (org_id, run_day);
CREATE INDEX IF NOT EXISTS rebalance_schedule_days_open_ix ON public.rebalance_schedule_days (run_day)
    WHERE status IN ('waiting', 'running');

-- 2. RLS — every operation (CLAUDE.md: a policy for every operation the table will ever take), the org GUC NULLIF'd,
--    plus the platform-scope carve-out the daily tick's org scan uses.
ALTER TABLE public.rebalance_schedule_days ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE op text;
BEGIN
  FOREACH op IN ARRAY ARRAY['select', 'insert', 'update', 'delete'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.rebalance_schedule_days', 'rebalance_schedule_days_org_' || op);
  END LOOP;
END $$;
CREATE POLICY rebalance_schedule_days_org_select ON public.rebalance_schedule_days FOR SELECT USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY rebalance_schedule_days_org_insert ON public.rebalance_schedule_days FOR INSERT WITH CHECK (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY rebalance_schedule_days_org_update ON public.rebalance_schedule_days FOR UPDATE USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY rebalance_schedule_days_org_delete ON public.rebalance_schedule_days FOR DELETE USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON public.rebalance_schedule_days TO app_service;

-- 3. The proposal trigger vocabulary: + 'scheduled' (the daily rebalancer's frequency-due proposals). Widened only.
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_trigger_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_trigger_chk CHECK (trigger = ANY (ARRAY[
  'join'::text, 'model_change'::text, 'drift'::text, 'cash_flow'::text, 'manual'::text, 'harvest'::text,
  'cash_waterfall'::text, 'scheduled'::text]));
