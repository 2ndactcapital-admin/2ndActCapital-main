-- PRICES2 part 1 — the resumable daily price refresh (platform scope).
-- Additive: two NEW platform tables. Writes NO security, price or org row.
-- Applied by apps/api/scripts/_apply_prices2.py --schema on the operator DSN (the table owner). Idempotent.
-- (no BEGIN / COMMIT here: _apply_prices2.py runs the whole file in ONE transaction)
--
-- OPERATIONAL STATE, updated in place (like public.ai_daily_runs): a day's progress and a lease, and each
-- security's last refresh day. Not reference data — the prices themselves stay bi-temporal in
-- portfolio.securities_global_prices; the job history stays in portfolio.security_data_runs.

-- 1. ONE row per (scope, price_day): the day's refresh, continued tick after tick until complete or the budget is spent.
--    scope 'platform' = the scheduled job; a verify uses its own 'zzp2…' scope.
CREATE TABLE IF NOT EXISTS portfolio.price_refresh_days (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope                 text NOT NULL DEFAULT 'platform',
  price_day             date NOT NULL,
  status                text NOT NULL DEFAULT 'running',
  covered               integer NOT NULL DEFAULT 0,
  refreshed             integer NOT NULL DEFAULT 0,
  calls                 integer NOT NULL DEFAULT 0,
  slices                integer NOT NULL DEFAULT 0,
  deferred              integer NOT NULL DEFAULT 0,
  new_loaded            integer NOT NULL DEFAULT 0,
  consecutive_failures  integer NOT NULL DEFAULT 0,
  errors                jsonb NOT NULL DEFAULT '[]'::jsonb,
  summary               jsonb NOT NULL DEFAULT '{}'::jsonb,
  stop_detail           text,
  workflow_run_id       uuid,
  lease_until           timestamptz,
  started_at            timestamptz NOT NULL DEFAULT now(),
  heartbeat_at          timestamptz,
  finished_at           timestamptz,
  owner_scope           text NOT NULL DEFAULT 'platform',
  org_id                uuid,
  CONSTRAINT price_refresh_days_status_chk CHECK (status IN ('running', 'complete', 'stopped_budget',
                                                             'stopped_error', 'carried_over')),
  CONSTRAINT price_refresh_days_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT price_refresh_days_counts_chk CHECK (covered >= 0 AND refreshed >= 0 AND calls >= 0 AND slices >= 0
                                                  AND deferred >= 0 AND new_loaded >= 0 AND consecutive_failures >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS price_refresh_days_day_uq ON portfolio.price_refresh_days (scope, price_day);
CREATE INDEX IF NOT EXISTS price_refresh_days_running_ix ON portfolio.price_refresh_days (scope)
    WHERE status = 'running';

-- 2. ONE row per security: the price day it was last refreshed (found, or answered "no data" by every source).
CREATE TABLE IF NOT EXISTS portfolio.price_refresh_state (
  global_security_id    uuid PRIMARY KEY REFERENCES portfolio.securities_global(id) ON DELETE CASCADE,
  last_refreshed_on     date NOT NULL,
  last_outcome          text NOT NULL,
  last_provider         text,
  last_day_id           uuid,
  updated_at            timestamptz NOT NULL DEFAULT now(),
  owner_scope           text NOT NULL DEFAULT 'platform',
  org_id                uuid,
  CONSTRAINT price_refresh_state_outcome_chk CHECK (last_outcome IN ('found', 'not_found', 'no_symbol')),
  CONSTRAINT price_refresh_state_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL)
);
CREATE INDEX IF NOT EXISTS price_refresh_state_day_ix ON portfolio.price_refresh_state (last_refreshed_on);

-- 3. RLS — every operation, like portfolio.tracked_securities (CLAUDE.md: a policy for every operation the table takes)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['price_refresh_days', 'price_refresh_state'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_read', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (true)', t || '_read', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_insert', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'') WITH CHECK ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_update', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_delete', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;
