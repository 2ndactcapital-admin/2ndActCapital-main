-- MARKETDATA1 — vendor-neutral security-data layer (part 1: schema).
--
-- Apply:  python3 apps/api/scripts/_apply_marketdata1.py --all   (operator DSN, one transaction)
--
-- WHAT THIS IS. One canonical store per data type, a provider registry with
-- licence facts on every provider, a rank per data type (platform default,
-- org override), per-day quality events and a per-provider call log. It is NOT
-- the mkt01–03 indicator store (portfolio.indicator_*): that stays separate on
-- purpose (MARKET_DATA_DESIGN_V1.md) and nothing here touches it.
--
-- THE STORES
--   eod_price         portfolio.securities_global_prices (existing, 0 rows) — + provider_key, as_of,
--                     quality_flags, licence snapshot, org_id (a csv upload's rows belong to the
--                     uploading org), upload_id. The live-point unique index gains provider_key so two
--                     sources can hold the same day (that is what cross-source disagreement compares).
--   corporate_action  portfolio.securities_global_corporate_actions (existing, Portfolio F, 0 rows) —
--                     + the same provider columns; action_type admits cash_dividend / stock_dividend.
--                     Phase F's apply still refuses every type it cannot apply (by name).
--   index_level       portfolio.index_levels (NEW).
--   fund_holdings     portfolio.replica_holdings_snapshots (rep1) — + provider_key, licence.
--   option_quote      portfolio.adviser_quotes (rep8) — unchanged; provider 'adviser_entry'.
--   eod_price (custodian) is never stored: it is READ from portfolio.positions (market value ÷ quantity
--                     on the position date) at the call, so it can never drift from the account.
--
-- TEMPORAL AXES (Rule 3)
--   data_providers, data_source_rank, data_provider_entitlements, index_levels,
--   data_quality_events          VALID axis (natural keys; nothing references their id).
--   data_uploads                 SYSTEM axis: the id is stable (store rows carry upload_id); a decision
--                                archives the pending row and inserts the decided one with the SAME id
--                                under a new system_from (the partial unique is on system_to IS NULL).
--   provider_call_log            INSERT-ONLY (trigger): budgets are a SUM over the day's rows.
--   security_data_runs           INSERT-ONLY: one summary row per run, written at the end.
--
-- RLS. Platform rows (owner_scope 'platform', org_id NULL) are read by everyone and written only under
-- app.is_super_admin. Org rows are org-scoped with NULLIF on the org GUC for EVERY operation. Every
-- table has a policy for SELECT, INSERT, UPDATE and DELETE.
--
-- THE MIGRATION WRITES NO ORG ROWS: platform providers, the platform rank, nothing else.

-- ── 1. Providers ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.data_providers (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key                      text NOT NULL,
  name                     text NOT NULL,
  kind                     text NOT NULL,
  status                   text NOT NULL DEFAULT 'not_connected',
  capabilities             text[] NOT NULL DEFAULT '{}',
  lic_internal_use         boolean NOT NULL DEFAULT false,
  lic_member_display       text NOT NULL DEFAULT 'not_allowed',
  lic_redistribution       boolean NOT NULL DEFAULT false,
  retention_after_cancel   text NOT NULL DEFAULT 'unknown',
  attribution              text,
  terms_note               text,
  confirmed_on             date,
  daily_call_budget        integer,
  requires_entitlement     boolean NOT NULL DEFAULT false,
  owner_scope              text NOT NULL DEFAULT 'platform',
  org_id                   uuid,
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT data_providers_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT data_providers_key_chk CHECK (key ~ '^[a-z][a-z0-9_]{1,40}$'),
  CONSTRAINT data_providers_kind_chk CHECK (kind IN ('custodian', 'regulator_filing', 'issuer_file', 'public_api',
                                                     'csv_upload', 'adviser_entry', 'vendor')),
  CONSTRAINT data_providers_status_chk CHECK (status IN ('active', 'not_connected', 'retired')),
  CONSTRAINT data_providers_capabilities_chk CHECK (capabilities <@ ARRAY['eod_price', 'corporate_action',
                                                     'fund_holdings', 'index_level', 'option_quote', 'fx']::text[]),
  CONSTRAINT data_providers_member_display_chk CHECK (lic_member_display IN ('allowed', 'own_holdings_only',
                                                                             'not_allowed')),
  CONSTRAINT data_providers_retention_chk CHECK (retention_after_cancel IN ('keep', 'delete', 'unknown',
                                                                            'not_applicable')),
  CONSTRAINT data_providers_budget_chk CHECK (daily_call_budget IS NULL OR daily_call_budget >= 0),
  -- a member may only see what the licence allows internally first
  CONSTRAINT data_providers_display_needs_internal_chk CHECK (lic_member_display = 'not_allowed' OR lic_internal_use),
  CONSTRAINT data_providers_redistribution_chk CHECK (NOT lic_redistribution OR lic_member_display = 'allowed')
);
CREATE UNIQUE INDEX IF NOT EXISTS data_providers_key_live_uq ON portfolio.data_providers (key)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 2. Rank per data type (platform default; an org override replaces the list for that type) ──
CREATE TABLE IF NOT EXISTS portfolio.data_source_rank (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  data_type      text NOT NULL,
  provider_key   text NOT NULL,
  rank           integer NOT NULL,
  owner_scope    text NOT NULL DEFAULT 'platform',
  org_id         uuid REFERENCES public.organizations(id),
  set_by         uuid REFERENCES public.users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT data_source_rank_scope_chk CHECK ((owner_scope = 'platform' AND org_id IS NULL)
                                               OR (owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT data_source_rank_type_chk CHECK (data_type IN ('eod_price', 'corporate_action', 'fund_holdings',
                                                            'index_level', 'option_quote', 'fx')),
  CONSTRAINT data_source_rank_rank_chk CHECK (rank BETWEEN 1 AND 100)
);
CREATE UNIQUE INDEX IF NOT EXISTS data_source_rank_provider_live_uq ON portfolio.data_source_rank
  (owner_scope, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), data_type, provider_key)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS data_source_rank_position_live_uq ON portfolio.data_source_rank
  (owner_scope, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), data_type, rank)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 3. Entitlements: an org reads a requires_entitlement provider's data only with a live row ──
CREATE TABLE IF NOT EXISTS portfolio.data_provider_entitlements (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES public.organizations(id),
  provider_key   text NOT NULL,
  note           text,
  granted_by     uuid REFERENCES public.users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS data_provider_entitlements_live_uq ON portfolio.data_provider_entitlements
  (org_id, provider_key) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 4. CSV uploads (org; maker-checker; nothing counts until approved) ─────
CREATE TABLE IF NOT EXISTS portfolio.data_uploads (
  id               uuid NOT NULL DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  data_type        text NOT NULL,
  file_name        text NOT NULL,
  file_sha256      text NOT NULL,
  mapping          jsonb NOT NULL DEFAULT '{}'::jsonb,
  licence          jsonb NOT NULL,
  row_count        integer NOT NULL,
  accepted_count   integer NOT NULL,
  rejects          jsonb NOT NULL DEFAULT '[]'::jsonb,
  staged_rows      jsonb NOT NULL DEFAULT '[]'::jsonb,
  status           text NOT NULL DEFAULT 'pending_approval',
  uploaded_by      uuid NOT NULL REFERENCES public.users(id),
  decided_by       uuid REFERENCES public.users(id),
  decided_at       timestamptz,
  decision_note    text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT data_uploads_pkey PRIMARY KEY (id, system_from),
  CONSTRAINT data_uploads_type_chk CHECK (data_type IN ('eod_price', 'corporate_action', 'index_level',
                                                        'fund_holdings')),
  CONSTRAINT data_uploads_status_chk CHECK (status IN ('pending_approval', 'approved', 'rejected')),
  CONSTRAINT data_uploads_counts_chk CHECK (accepted_count >= 0 AND accepted_count <= row_count),
  CONSTRAINT data_uploads_licence_chk CHECK (licence ? 'internal_use' AND licence ? 'member_display'
                                             AND licence ? 'redistribution' AND licence ? 'retention_after_cancel'),
  CONSTRAINT data_uploads_maker_checker_chk CHECK (decided_by IS NULL OR decided_by <> uploaded_by),
  CONSTRAINT data_uploads_decided_chk CHECK ((status = 'pending_approval') = (decided_by IS NULL)
                                             AND (status = 'pending_approval') = (decided_at IS NULL)),
  CONSTRAINT data_uploads_reject_note_chk CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS data_uploads_live_uq ON portfolio.data_uploads (id) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS data_uploads_file_live_uq ON portfolio.data_uploads (org_id, data_type, file_sha256)
  WHERE system_to IS NULL AND status <> 'rejected';

-- ── 5. Canonical stores: provider columns ────────────────────────────────
ALTER TABLE portfolio.securities_global_prices
  ADD COLUMN IF NOT EXISTS provider_key  text,
  ADD COLUMN IF NOT EXISTS as_of         timestamptz,
  ADD COLUMN IF NOT EXISTS quality_flags text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS licence       jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS org_id        uuid REFERENCES public.organizations(id),
  ADD COLUMN IF NOT EXISTS upload_id     uuid;
-- the table holds 0 rows (checked by --verify); widening the point key is safe and nothing names it in
-- an ON CONFLICT (grep: services/securities_global.add_price is a plain INSERT).
DROP INDEX IF EXISTS portfolio.uq_sec_global_price_point;
CREATE UNIQUE INDEX IF NOT EXISTS uq_sec_global_price_point ON portfolio.securities_global_prices
  (global_security_id, price_date, price_type, COALESCE(provider_key, ''),
   COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE system_to IS NULL AND valid_to IS NULL;
ALTER TABLE portfolio.securities_global_prices DROP CONSTRAINT IF EXISTS sec_global_price_upload_chk;
ALTER TABLE portfolio.securities_global_prices ADD CONSTRAINT sec_global_price_upload_chk
  CHECK ((upload_id IS NULL) OR (org_id IS NOT NULL AND provider_key = 'csv_upload'));
ALTER TABLE portfolio.securities_global_prices DROP CONSTRAINT IF EXISTS sec_global_price_positive_chk;
ALTER TABLE portfolio.securities_global_prices ADD CONSTRAINT sec_global_price_positive_chk CHECK (price > 0);

ALTER TABLE portfolio.securities_global_corporate_actions
  ADD COLUMN IF NOT EXISTS provider_key  text,
  ADD COLUMN IF NOT EXISTS as_of         timestamptz,
  ADD COLUMN IF NOT EXISTS quality_flags text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS licence       jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS org_id        uuid REFERENCES public.organizations(id),
  ADD COLUMN IF NOT EXISTS upload_id     uuid;
ALTER TABLE portfolio.securities_global_corporate_actions DROP CONSTRAINT IF EXISTS corp_actions_type_chk;
ALTER TABLE portfolio.securities_global_corporate_actions ADD CONSTRAINT corp_actions_type_chk CHECK (action_type = ANY
  (ARRAY['split', 'reverse_split', 'spinoff', 'merger', 'name_change', 'cusip_change', 'tender', 'delisting',
         'cash_dividend', 'stock_dividend']));
ALTER TABLE portfolio.securities_global_corporate_actions DROP CONSTRAINT IF EXISTS corp_actions_upload_chk;
ALTER TABLE portfolio.securities_global_corporate_actions ADD CONSTRAINT corp_actions_upload_chk
  CHECK ((upload_id IS NULL) OR (org_id IS NOT NULL AND provider_key = 'csv_upload'));

ALTER TABLE portfolio.replica_holdings_snapshots
  ADD COLUMN IF NOT EXISTS provider_key text,
  ADD COLUMN IF NOT EXISTS licence      jsonb NOT NULL DEFAULT '{}'::jsonb;

-- the price / corporate-action read policies were `true`: an org's own CSV rows are now org-scoped.
DROP POLICY IF EXISTS securities_global_prices_global_read ON portfolio.securities_global_prices;
CREATE POLICY securities_global_prices_global_read ON portfolio.securities_global_prices FOR SELECT USING (
  org_id IS NULL OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS securities_global_prices_super_admin_insert ON portfolio.securities_global_prices;
CREATE POLICY securities_global_prices_super_admin_insert ON portfolio.securities_global_prices FOR INSERT WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
DROP POLICY IF EXISTS securities_global_prices_super_admin_update ON portfolio.securities_global_prices;
CREATE POLICY securities_global_prices_super_admin_update ON portfolio.securities_global_prices FOR UPDATE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid))
  WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));

DROP POLICY IF EXISTS corp_actions_global_read ON portfolio.securities_global_corporate_actions;
CREATE POLICY corp_actions_global_read ON portfolio.securities_global_corporate_actions FOR SELECT USING (
  org_id IS NULL OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS corp_actions_super_admin_insert ON portfolio.securities_global_corporate_actions;
CREATE POLICY corp_actions_super_admin_insert ON portfolio.securities_global_corporate_actions FOR INSERT WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
DROP POLICY IF EXISTS corp_actions_super_admin_update ON portfolio.securities_global_corporate_actions;
CREATE POLICY corp_actions_super_admin_update ON portfolio.securities_global_corporate_actions FOR UPDATE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid))
  WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));

-- ── 6. Index levels (NEW store) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.index_levels (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  index_key           text NOT NULL,
  global_security_id  uuid REFERENCES portfolio.securities_global(id),
  level_date          date NOT NULL,
  value               numeric NOT NULL,
  provider_key        text NOT NULL,
  as_of               timestamptz NOT NULL DEFAULT now(),
  quality_flags       text[] NOT NULL DEFAULT '{}',
  licence             jsonb NOT NULL DEFAULT '{}'::jsonb,
  org_id              uuid REFERENCES public.organizations(id),
  upload_id           uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT index_levels_key_chk CHECK (index_key ~ '^[A-Za-z0-9_.^:-]{1,60}$'),
  CONSTRAINT index_levels_upload_chk CHECK ((upload_id IS NULL) OR (org_id IS NOT NULL AND provider_key = 'csv_upload'))
);
CREATE UNIQUE INDEX IF NOT EXISTS index_levels_point_live_uq ON portfolio.index_levels
  (index_key, level_date, provider_key, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS index_levels_lookup_idx ON portfolio.index_levels (index_key, level_date DESC)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 7. Quality events (per item and day) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.data_quality_events (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid REFERENCES public.organizations(id),
  owner_scope         text NOT NULL DEFAULT 'platform',
  data_type           text NOT NULL,
  subject_key         text NOT NULL,
  global_security_id  uuid REFERENCES portfolio.securities_global(id),
  event_date          date NOT NULL,
  kind                text NOT NULL,
  provider_key        text NOT NULL DEFAULT '',
  detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
  run_id              uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT data_quality_events_scope_chk CHECK ((owner_scope = 'platform' AND org_id IS NULL)
                                                  OR (owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT data_quality_events_type_chk CHECK (data_type IN ('eod_price', 'corporate_action', 'fund_holdings',
                                                               'index_level', 'option_quote', 'fx')),
  CONSTRAINT data_quality_events_kind_chk CHECK (kind IN ('stale', 'gap', 'outlier', 'disagreement',
                                                          'budget_exhausted'))
);
CREATE UNIQUE INDEX IF NOT EXISTS data_quality_events_live_uq ON portfolio.data_quality_events
  (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), data_type, subject_key, event_date, kind,
   provider_key) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 8. Provider call log (insert-only; budgets are a SUM per provider and day) ─
CREATE TABLE IF NOT EXISTS portfolio.provider_call_log (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_key   text NOT NULL,
  call_date      date NOT NULL DEFAULT (now() AT TIME ZONE 'utc')::date,
  calls          integer NOT NULL,
  data_type      text,
  ok             boolean NOT NULL,
  error          text,
  run_id         uuid,
  owner_scope    text NOT NULL DEFAULT 'platform',
  org_id         uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT provider_call_log_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT provider_call_log_calls_chk CHECK (calls >= 0)
);
CREATE INDEX IF NOT EXISTS provider_call_log_day_idx ON portfolio.provider_call_log (provider_key, call_date);

-- ── 9. Nightly summary rows ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.security_data_runs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_trigger    text NOT NULL,
  status         text NOT NULL,
  started_at     timestamptz NOT NULL,
  finished_at    timestamptz NOT NULL,
  summary        jsonb NOT NULL DEFAULT '{}'::jsonb,
  owner_scope    text NOT NULL DEFAULT 'platform',
  org_id         uuid,
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT security_data_runs_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_data_runs_status_chk CHECK (status IN ('success', 'partial', 'failed')),
  CONSTRAINT security_data_runs_trigger_chk CHECK (run_trigger IN ('scheduled', 'manual', 'test'))
);

-- insert-only guard for the two append tables (teardown runs as the table owner, which the guard allows)
CREATE OR REPLACE FUNCTION portfolio.security_data_append_only_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF current_user = 'app_service' THEN
    RAISE EXCEPTION '% is append-only — % refused', TG_TABLE_NAME, TG_OP USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS provider_call_log_append_trg ON portfolio.provider_call_log;
CREATE TRIGGER provider_call_log_append_trg BEFORE UPDATE OR DELETE ON portfolio.provider_call_log
  FOR EACH ROW EXECUTE FUNCTION portfolio.security_data_append_only_guard();
DROP TRIGGER IF EXISTS security_data_runs_append_trg ON portfolio.security_data_runs;
CREATE TRIGGER security_data_runs_append_trg BEFORE UPDATE OR DELETE ON portfolio.security_data_runs
  FOR EACH ROW EXECUTE FUNCTION portfolio.security_data_append_only_guard();

-- ── 10. RLS: every operation on every new table ──────────────────────────
DO $$
DECLARE
  t text;
  plat text := 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true''';
  org text := 'org_id = NULLIF(current_setting(''app.current_org_id'', true), '''')::uuid';
BEGIN
  -- platform tables: read by all, written under super admin
  FOREACH t IN ARRAY ARRAY['data_providers', 'provider_call_log', 'security_data_runs'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_read', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (true)', t || '_read', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK (%s)', t || '_super_admin_insert', t, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING (%s) WITH CHECK (%s)', t || '_super_admin_update',
                   t, plat, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING (%s)', t || '_super_admin_delete', t, plat);
  END LOOP;
  -- org tables: own org on every operation (super admin carve-out, as every org table here)
  FOREACH t IN ARRAY ARRAY['data_provider_entitlements', 'data_uploads'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING ((%s) OR (%s))', t || '_org_select', t, org, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK ((%s) OR (%s))', t || '_org_insert', t,
                   CASE WHEN t = 'data_provider_entitlements' THEN plat ELSE org END, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING ((%s) OR (%s)) WITH CHECK ((%s) OR (%s))',
                   t || '_org_update', t, CASE WHEN t = 'data_provider_entitlements' THEN plat ELSE org END, plat,
                   CASE WHEN t = 'data_provider_entitlements' THEN plat ELSE org END, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING ((%s) OR (%s))', t || '_org_delete', t,
                   CASE WHEN t = 'data_provider_entitlements' THEN plat ELSE org END, plat);
  END LOOP;
  -- mixed tables: platform rows read by all + written under super admin; org rows own org on every operation
  FOREACH t IN ARRAY ARRAY['data_source_rank', 'index_levels', 'data_quality_events'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (org_id IS NULL OR (%s) OR (%s))',
                   t || '_select', t, org, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK ((%s) OR (org_id IS NOT NULL AND %s))',
                   t || '_insert', t, plat, org);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING ((%s) OR (org_id IS NOT NULL AND %s)) '
                   'WITH CHECK ((%s) OR (org_id IS NOT NULL AND %s))', t || '_update', t, plat, org, plat, org);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING ((%s) OR (org_id IS NOT NULL AND %s))',
                   t || '_delete', t, plat, org);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['data_providers', 'data_source_rank', 'data_provider_entitlements', 'data_uploads',
                           'index_levels', 'data_quality_events', 'provider_call_log', 'security_data_runs'] LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;

-- ── 11. Seed: the free providers + the vendor slot, and the platform rank ─
-- Licence facts as confirmed today (2026-10-28). Custodian valuations: the member's own account data —
-- internal use, shown to that member only. SEC filings: public record. Issuer files: the issuer's site
-- terms (internal analysis; not republished). FRED: series-level terms vary — Treasury/H.15 rates are
-- public domain; internal use only until a series is checked. CSV uploads: the uploader enters the
-- licence per file (the provider row is a ceiling of nothing — the upload's own licence governs).
-- Adviser entry: the adviser's own quote, internal only. Vendor: not connected, no rights until a contract.
INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget, requires_entitlement)
SELECT v.* FROM (VALUES
  ('custodian', 'Custodian account valuations', 'custodian', 'active', ARRAY['eod_price'], true, 'own_holdings_only',
   false, 'not_applicable', NULL::text,
   'The custodian''s own daily valuation of a position (market value ÷ quantity on the position date). Internal use '
   'for the org; shown to a member only for that member''s own holdings.', DATE '2026-10-28', NULL::integer, false),
  ('sec_nport', 'SEC EDGAR — Form N-PORT-P', 'regulator_filing', 'active', ARRAY['fund_holdings'], true, 'allowed',
   false, 'keep', 'Source: SEC EDGAR, Form N-PORT-P',
   'Public regulatory filing; holdings are months old by design (60-day lag).', DATE '2026-10-28', 2000, false),
  ('ssga_spdr', 'State Street (SPDR) daily holdings file', 'issuer_file', 'active', ARRAY['fund_holdings'], true,
   'not_allowed', false, 'unknown', 'Source: State Street Investment Management',
   'Issuer website file; used for internal portfolio construction only — not shown to members or republished '
   'until the site terms are confirmed for display.', DATE '2026-10-28', 200, false),
  ('fred', 'FRED (St. Louis Fed) — rates and Treasury curves', 'public_api', 'active', ARRAY['index_level'], true,
   'not_allowed', false, 'keep', 'Source: FRED, Federal Reserve Bank of St. Louis',
   'Uses the existing FRED_API_KEY (no new key). Series terms vary; internal use until a series is checked.',
   DATE '2026-10-28', 1000, false),
  ('csv_upload', 'File upload (prices, corporate actions, index levels)', 'csv_upload', 'active',
   ARRAY['eod_price', 'corporate_action', 'index_level'], true, 'allowed', false, 'unknown', NULL,
   'The uploader enters the licence for each file; the file''s own licence governs every value from it, and a '
   'value counts only after a second person approves the upload.', DATE '2026-10-28', NULL, false),
  ('adviser_entry', 'Adviser-entered quotes', 'adviser_entry', 'active',
   ARRAY['option_quote', 'index_level', 'eod_price'], true, 'not_allowed', false, 'not_applicable', NULL,
   'rep8''s adviser quotes (premium, index level, underlying price, implied vol) with their source text and as-of.',
   DATE '2026-10-28', NULL, false),
  ('vendor_slot', 'Vendor slot (not connected)', 'vendor', 'not_connected',
   ARRAY['eod_price', 'corporate_action', 'index_level', 'option_quote', 'fx'], false, 'not_allowed', false,
   'unknown', NULL,
   'The shape a paid vendor plugs into. No contract, no rights, no network — it returns nothing until a real vendor '
   'module replaces it and its row is set active with the contract''s licence facts.', NULL, 0, true)
) AS v(key, name, kind, status, capabilities, lic_internal_use, lic_member_display, lic_redistribution,
       retention_after_cancel, attribution, terms_note, confirmed_on, daily_call_budget, requires_entitlement)
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers p WHERE p.key = v.key AND p.valid_to IS NULL
                  AND p.system_to IS NULL);

INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank)
SELECT v.* FROM (VALUES
  ('eod_price', 'custodian', 1), ('eod_price', 'csv_upload', 2), ('eod_price', 'adviser_entry', 3),
  ('corporate_action', 'csv_upload', 1),
  ('fund_holdings', 'ssga_spdr', 1), ('fund_holdings', 'sec_nport', 2),
  ('index_level', 'fred', 1), ('index_level', 'csv_upload', 2), ('index_level', 'adviser_entry', 3),
  ('option_quote', 'adviser_entry', 1)
) AS v(data_type, provider_key, rank)
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank r WHERE r.owner_scope = 'platform'
                  AND r.data_type = v.data_type AND r.provider_key = v.provider_key AND r.valid_to IS NULL
                  AND r.system_to IS NULL);

-- rep1 snapshots already on record were fetched through the provider named by their source_key
UPDATE portfolio.replica_holdings_snapshots s SET provider_key = s.source_key
 WHERE s.provider_key IS NULL AND s.source_key IN ('sec_nport', 'ssga_spdr');

-- ── 12. Prices already written with no provider (A1's securities_global.add_price — a super admin's manual
-- entry — and rep4's verify fixtures) are the 'platform_entry' provider: readable through the rank, internal
-- use only, never shown to members. add_price now stamps provider_key = 'platform_entry'; a NULL provider_key
-- row is read as platform_entry by the store reader.
INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget, requires_entitlement)
SELECT 'platform_entry', 'Platform-entered prices (security master)', 'adviser_entry', 'active', ARRAY['eod_price'],
       true, 'not_allowed', false, 'not_applicable', NULL,
       'A price a Super Admin entered in the security master (securities_global.add_price). Internal use only.',
       DATE '2026-10-28', NULL, false
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers WHERE key = 'platform_entry' AND valid_to IS NULL
                  AND system_to IS NULL);
INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank)
SELECT 'eod_price', 'platform_entry', 4
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'eod_price'
                  AND provider_key = 'platform_entry' AND valid_to IS NULL AND system_to IS NULL);

-- ── 13. Fund holdings are not accepted from a file this sprint (rep1's holdings store is platform-only, so an
-- org's file would publish to every org): csv_upload leaves the fund_holdings rank and its capabilities. A
-- valid-axis restatement of rows a first apply of this file wrote; a no-op on a fresh database.
DO $$
DECLARE r record;
BEGIN
  IF EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'fund_holdings'
             AND provider_key = 'csv_upload' AND valid_to IS NULL AND system_to IS NULL) THEN
    UPDATE portfolio.data_source_rank SET valid_to = now() WHERE owner_scope = 'platform' AND data_type = 'fund_holdings'
       AND valid_to IS NULL AND system_to IS NULL;
    INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank)
    VALUES ('fund_holdings', 'ssga_spdr', 1), ('fund_holdings', 'sec_nport', 2);
  END IF;
  SELECT * INTO r FROM portfolio.data_providers WHERE key = 'csv_upload' AND valid_to IS NULL AND system_to IS NULL;
  IF FOUND AND 'fund_holdings' = ANY (r.capabilities) THEN
    UPDATE portfolio.data_providers SET valid_to = now() WHERE id = r.id;
    INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                          lic_redistribution, retention_after_cancel, attribution, terms_note,
                                          confirmed_on, daily_call_budget, requires_entitlement)
    VALUES (r.key, 'File upload (prices, corporate actions, index levels)', r.kind, r.status,
            ARRAY['eod_price', 'corporate_action', 'index_level'], r.lic_internal_use, r.lic_member_display,
            r.lic_redistribution, r.retention_after_cancel, r.attribution, r.terms_note, r.confirmed_on,
            r.daily_call_budget, r.requires_entitlement);
  END IF;
END $$;
