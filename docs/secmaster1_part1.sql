-- SECMASTER1 — fill the security master from free public sources (part 1: schema).
--
-- Apply:  python3 apps/api/scripts/_apply_secmaster1.py --all   (operator DSN, one transaction, then --verify)
--
-- WHAT THIS ADDS
--   1. data type 'reference' on marketdata1's registry (providers' capabilities, the rank, quality events).
--   2. portfolio.security_reference_attributes — per security, per field, PER PROVIDER, bitemporal (valid axis;
--      nothing references its id). Read through the platform rank for 'reference': for each field the
--      highest-ranked provider that holds a value wins. Fields: exchange, cik, sector, industry, industry_group,
--      country, is_etf, fund_category, listing_status (listed | delisted | unknown), first_seen, last_seen,
--      absent_runs (the delisting counter: written only while a security is missing from its provider's file).
--   3. portfolio.security_master_source_files — one row per source file actually processed (provider, file key,
--      as_of, sha256, rows, status). "Same file twice writes nothing" and "a 13(f) quarter loads once" both key on
--      it. Insert-only.
--   4. portfolio.sec_13f_list — the SEC Official List of Section 13(f) Securities, one live row per
--      (quarter, cusip), with its mapping state and the reason it is not mapped. Valid axis.
--   5. portfolio.security_master_review — the review queue: a disagreement between a source and an existing row
--      (or between two sources) lands here; NOTHING is overwritten. Valid axis; a decision restates the row.
--   6. portfolio.openfigi_mappings — every OpenFIGI answer (hit or miss), insert-only: "only identifiers not
--      mapped in the last 30 days" reads it.
--   7. portfolio.security_master_checkpoints — the one-time load's --resume checkpoints (source + batch).
--      Insert-only.
--   8. Search indexes (pg_trgm) for the master at ~30k securities / ~100k identifiers.
--   9. Five provider rows with their licence facts, and the platform rank for 'reference'.
--
-- RLS. Every new table: a policy for SELECT, INSERT, UPDATE and DELETE. Reference attributes, source files and the
-- 13(f) list are platform rows (owner_scope 'platform', org_id NULL) READ BY EVERY ORG, written only under
-- app.is_super_admin (the job's platform transaction / a Super Admin). The review queue, the OpenFIGI log and the
-- checkpoints are read AND written only under app.is_super_admin. The NULLIF on the GUC is used everywhere.
--
-- THE MIGRATION WRITES NO SECURITY, IDENTIFIER OR ORG ROW: provider rows and the platform rank only.

-- ── 0. 'reference' on the registry's data-type CHECKs (additive: every existing literal kept) ──
ALTER TABLE portfolio.data_providers DROP CONSTRAINT IF EXISTS data_providers_capabilities_chk;
ALTER TABLE portfolio.data_providers ADD CONSTRAINT data_providers_capabilities_chk CHECK (capabilities <@ ARRAY[
  'eod_price', 'corporate_action', 'fund_holdings', 'index_level', 'option_quote', 'fx', 'factor_score',
  'reference']::text[]);
ALTER TABLE portfolio.data_source_rank DROP CONSTRAINT IF EXISTS data_source_rank_type_chk;
ALTER TABLE portfolio.data_source_rank ADD CONSTRAINT data_source_rank_type_chk CHECK (data_type IN (
  'eod_price', 'corporate_action', 'fund_holdings', 'index_level', 'option_quote', 'fx', 'factor_score', 'reference'));
ALTER TABLE portfolio.data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_type_chk;
ALTER TABLE portfolio.data_quality_events ADD CONSTRAINT data_quality_events_type_chk CHECK (data_type IN (
  'eod_price', 'corporate_action', 'fund_holdings', 'index_level', 'option_quote', 'fx', 'factor_score', 'reference'));

-- ── 1. Reference attributes ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.security_reference_attributes (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  field               text NOT NULL,
  value               text NOT NULL,
  provider_key        text NOT NULL,
  as_of               date NOT NULL,
  run_id              uuid,
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT security_reference_attributes_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_reference_attributes_field_chk CHECK (field IN ('exchange', 'cik', 'sector', 'industry',
    'industry_group', 'country', 'is_etf', 'fund_category', 'listing_status', 'first_seen', 'last_seen',
    'absent_runs')),
  CONSTRAINT security_reference_attributes_value_chk CHECK (length(btrim(value)) > 0),
  CONSTRAINT security_reference_attributes_listing_chk CHECK (field <> 'listing_status'
    OR value IN ('listed', 'delisted', 'unknown')),
  CONSTRAINT security_reference_attributes_bool_chk CHECK (field <> 'is_etf' OR value IN ('true', 'false')),
  CONSTRAINT security_reference_attributes_absent_chk CHECK (field <> 'absent_runs' OR value ~ '^[1-9][0-9]*$')
);
CREATE UNIQUE INDEX IF NOT EXISTS security_reference_attributes_live_uq ON portfolio.security_reference_attributes
  (global_security_id, field, provider_key) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS security_reference_attributes_field_idx ON portfolio.security_reference_attributes
  (provider_key, field, value) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 2. Source files processed ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.security_master_source_files (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_key   text NOT NULL,
  file_key       text NOT NULL,
  as_of          timestamptz NOT NULL,
  sha256         text NOT NULL,
  row_count      integer NOT NULL,
  status         text NOT NULL,
  summary        jsonb NOT NULL DEFAULT '{}'::jsonb,
  run_id         uuid,
  owner_scope    text NOT NULL DEFAULT 'platform',
  org_id         uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT security_master_source_files_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_master_source_files_status_chk CHECK (status IN ('complete', 'partial')),
  CONSTRAINT security_master_source_files_rows_chk CHECK (row_count >= 0),
  CONSTRAINT security_master_source_files_sha_chk CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
-- a file (provider, file key, content) is recorded complete at most once
CREATE UNIQUE INDEX IF NOT EXISTS security_master_source_files_complete_uq ON portfolio.security_master_source_files
  (provider_key, file_key, sha256) WHERE status = 'complete' AND valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS security_master_source_files_latest_idx ON portfolio.security_master_source_files
  (provider_key, file_key, as_of DESC);

-- ── 3. The SEC Official List of Section 13(f) Securities ─────────────────
CREATE TABLE IF NOT EXISTS portfolio.sec_13f_list (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  quarter              text NOT NULL,
  cusip                text NOT NULL,
  has_listed_options   boolean NOT NULL DEFAULT false,
  issuer               text NOT NULL,
  description          text NOT NULL DEFAULT '',
  status               text NOT NULL,
  is_option            boolean NOT NULL DEFAULT false,
  map_status           text NOT NULL DEFAULT 'pending',
  map_note             text,
  global_security_id   uuid REFERENCES portfolio.securities_global(id),
  owner_scope          text NOT NULL DEFAULT 'platform',
  org_id               uuid,
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT sec_13f_list_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT sec_13f_list_quarter_chk CHECK (quarter ~ '^[0-9]{4}q[1-4]$'),
  CONSTRAINT sec_13f_list_cusip_chk CHECK (cusip ~ '^[0-9A-Z*@#]{9}$'),
  CONSTRAINT sec_13f_list_status_chk CHECK (status IN ('unchanged', 'added', 'deleted')),
  CONSTRAINT sec_13f_list_map_status_chk CHECK (map_status IN ('pending', 'mapped', 'not_mapped', 'option_skipped')),
  CONSTRAINT sec_13f_list_mapped_chk CHECK ((map_status = 'mapped') = (global_security_id IS NOT NULL)),
  CONSTRAINT sec_13f_list_reason_chk CHECK (map_status <> 'not_mapped' OR map_note IS NOT NULL),
  CONSTRAINT sec_13f_list_option_chk CHECK (NOT is_option OR map_status = 'option_skipped')
);
CREATE UNIQUE INDEX IF NOT EXISTS sec_13f_list_live_uq ON portfolio.sec_13f_list (quarter, cusip)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS sec_13f_list_map_idx ON portfolio.sec_13f_list (quarter, map_status)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 4. Review queue ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.security_master_review (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind                 text NOT NULL,
  subject_key          text NOT NULL,
  field                text NOT NULL DEFAULT '',
  incoming_value       text NOT NULL DEFAULT '',
  existing_value       text,
  provider_key         text NOT NULL,
  global_security_id   uuid REFERENCES portfolio.securities_global(id),
  detail               jsonb NOT NULL DEFAULT '{}'::jsonb,
  status               text NOT NULL DEFAULT 'open',
  decided_by           uuid REFERENCES public.users(id),
  decided_at           timestamptz,
  decision_note        text,
  run_id               uuid,
  owner_scope          text NOT NULL DEFAULT 'platform',
  org_id               uuid,
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT security_master_review_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_master_review_kind_chk CHECK (kind IN ('ticker_conflict', 'ticker_cusip_conflict',
    'identifier_conflict', 'type_mismatch', 'exchange_mismatch', 'ambiguous_match', 'ticker_moved',
    '13f_deleted', '13f_conflict')),
  CONSTRAINT security_master_review_status_chk CHECK (status IN ('open', 'resolved', 'dismissed')),
  CONSTRAINT security_master_review_decision_chk CHECK ((status = 'open') = (decided_at IS NULL)),
  CONSTRAINT security_master_review_decider_chk CHECK (status = 'open' OR decided_by IS NOT NULL)
);
-- one OPEN item per (kind, subject, field, incoming value): a re-run of the same file adds nothing
CREATE UNIQUE INDEX IF NOT EXISTS security_master_review_open_uq ON portfolio.security_master_review
  (kind, subject_key, field, incoming_value) WHERE status = 'open' AND valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS security_master_review_status_idx ON portfolio.security_master_review (status, kind)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 5. OpenFIGI answers (insert-only) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.openfigi_mappings (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  id_type        text NOT NULL,
  id_value       text NOT NULL,
  exch_code      text NOT NULL DEFAULT '',
  ok             boolean NOT NULL,
  hits           integer NOT NULL DEFAULT 0,
  result         jsonb,
  warning        text,
  mapped_at      timestamptz NOT NULL DEFAULT now(),
  run_id         uuid,
  owner_scope    text NOT NULL DEFAULT 'platform',
  org_id         uuid,
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT openfigi_mappings_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT openfigi_mappings_type_chk CHECK (id_type IN ('ID_CUSIP', 'TICKER', 'ID_ISIN', 'COMPOSITE_ID_BB_GLOBAL')),
  CONSTRAINT openfigi_mappings_hits_chk CHECK (hits >= 0)
);
CREATE INDEX IF NOT EXISTS openfigi_mappings_key_idx ON portfolio.openfigi_mappings (id_type, id_value, mapped_at DESC);

-- ── 6. One-time load checkpoints (insert-only) ───────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.security_master_checkpoints (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  load_id        uuid NOT NULL,
  source         text NOT NULL,
  batch_no       integer NOT NULL,
  counts         jsonb NOT NULL DEFAULT '{}'::jsonb,
  done_at        timestamptz NOT NULL DEFAULT now(),
  owner_scope    text NOT NULL DEFAULT 'platform',
  org_id         uuid,
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT security_master_checkpoints_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_master_checkpoints_batch_chk CHECK (batch_no >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS security_master_checkpoints_uq ON portfolio.security_master_checkpoints
  (load_id, source, batch_no) WHERE valid_to IS NULL AND system_to IS NULL;

-- insert-only guard (marketdata1's function: refuses UPDATE / DELETE for app_service; the operator's teardown,
-- running as the table owner, passes)
DROP TRIGGER IF EXISTS openfigi_mappings_append_trg ON portfolio.openfigi_mappings;
CREATE TRIGGER openfigi_mappings_append_trg BEFORE UPDATE OR DELETE ON portfolio.openfigi_mappings
  FOR EACH ROW EXECUTE FUNCTION portfolio.security_data_append_only_guard();
DROP TRIGGER IF EXISTS security_master_checkpoints_append_trg ON portfolio.security_master_checkpoints;
CREATE TRIGGER security_master_checkpoints_append_trg BEFORE UPDATE OR DELETE ON portfolio.security_master_checkpoints
  FOR EACH ROW EXECUTE FUNCTION portfolio.security_data_append_only_guard();
DROP TRIGGER IF EXISTS security_master_source_files_append_trg ON portfolio.security_master_source_files;
CREATE TRIGGER security_master_source_files_append_trg BEFORE UPDATE OR DELETE ON portfolio.security_master_source_files
  FOR EACH ROW EXECUTE FUNCTION portfolio.security_data_append_only_guard();

-- ── 7. RLS: every operation on every new table ───────────────────────────
DO $$
DECLARE
  t text;
  plat text := 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true''';
BEGIN
  -- reference data: read by every org, written only under super admin
  FOREACH t IN ARRAY ARRAY['security_reference_attributes', 'security_master_source_files', 'sec_13f_list'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_read', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (org_id IS NULL OR (%s))', t || '_read', t, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK (%s)', t || '_super_admin_insert', t, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING (%s) WITH CHECK (%s)',
                   t || '_super_admin_update', t, plat, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING (%s)', t || '_super_admin_delete', t, plat);
  END LOOP;
  -- operational tables: Super Admin only, for every operation
  FOREACH t IN ARRAY ARRAY['security_master_review', 'openfigi_mappings', 'security_master_checkpoints'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (%s)', t || '_super_admin_select', t, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK (%s)', t || '_super_admin_insert', t, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING (%s) WITH CHECK (%s)',
                   t || '_super_admin_update', t, plat, plat);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING (%s)', t || '_super_admin_delete', t, plat);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['security_reference_attributes', 'security_master_source_files', 'sec_13f_list',
                           'security_master_review', 'openfigi_mappings', 'security_master_checkpoints'] LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;

-- ── 8. Search indexes (Task 1e) ──────────────────────────────────────────
-- list_global_securities searches name / short_name / identifier values with ILIKE '%term%'. At ~30k securities and
-- ~100k identifiers a btree cannot serve a leading wildcard; a trigram GIN can (pg_trgm handles ILIKE).
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
CREATE INDEX IF NOT EXISTS idx_sec_global_name_trgm ON portfolio.securities_global
  USING gin (name extensions.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sec_global_short_name_trgm ON portfolio.securities_global
  USING gin (short_name extensions.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sec_global_ident_value_trgm ON portfolio.securities_global_identifiers
  USING gin (id_value extensions.gin_trgm_ops);

-- ── 9. Providers (licence facts confirmed 2026-11-04) and the platform rank for 'reference' ──
INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget, requires_entitlement)
SELECT v.* FROM (VALUES
  ('nasdaq_trader', 'Nasdaq Trader symbol directory', 'public_api', 'active', ARRAY['reference'], true, 'allowed',
   false, 'keep', 'Source: Nasdaq Trader symbol directory',
   'nasdaqlisted.txt + otherlisted.txt (the US listed universe, exchange, ETF and test-issue flags). Free download '
   'under the nasdaqtrader.com site terms: used to maintain the security master; symbols, names and listing facts '
   'may be shown to members; the files themselves are not republished.', DATE '2026-11-04', 50, false),
  ('sec_tickers', 'SEC company tickers (CIK + exchange)', 'regulator_filing', 'active', ARRAY['reference'], true,
   'allowed', true, 'keep', 'Source: U.S. Securities and Exchange Commission',
   'sec.gov/files/company_tickers_exchange.json — U.S. government public record. Fetched under the SEC fair-access '
   'policy (declared User-Agent, at most 10 requests per second, through services/edgar_fetch).', DATE '2026-11-04',
   50, false),
  ('openfigi', 'OpenFIGI mapping API', 'public_api', 'active', ARRAY['reference'], true, 'allowed', false, 'keep',
   'Source: OpenFIGI (Bloomberg)',
   'FIGI, security type and ticker for a CUSIP or a ticker. FIGIs are an open standard and may be shown. The CUSIPs '
   'sent to it remain CUSIP Global Services'' licensed identifiers: mapping results are NOT a redistribution right. '
   'Without OPENFIGI_API_KEY: 10 jobs per request, 25 requests per minute; with it, 100 jobs per request.',
   DATE '2026-11-04', 2000, false),
  ('sec_13f_list', 'SEC Official List of Section 13(f) Securities', 'regulator_filing', 'active',
   ARRAY['reference'], true, 'own_holdings_only', false, 'keep', 'Source: SEC Official List of Section 13(f) Securities',
   'Quarterly list of 13(f) CUSIPs. The SEC publishes it, but CUSIP numbers belong to the American Bankers '
   'Association / CUSIP Global Services: used internally to identify securities; a CUSIP is shown to a member only '
   'for that member''s own holdings and is never redistributed.', DATE '2026-11-04', 20, false),
  ('finance_database', 'FinanceDatabase (sector, industry, country, fund category)', 'public_api', 'active',
   ARRAY['reference'], true, 'not_allowed', false, 'keep', 'Source: FinanceDatabase (github.com/JerBouma)',
   'Classification attributes from the FinanceDatabase CSVs on GitHub. The repository is MIT-licensed but the data '
   'is compiled from third-party sources: internal classification only — not shown to members, not redistributed.',
   DATE '2026-11-04', 200, false)
) AS v(key, name, kind, status, capabilities, lic_internal_use, lic_member_display, lic_redistribution,
       retention_after_cancel, attribution, terms_note, confirmed_on, daily_call_budget, requires_entitlement)
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers p WHERE p.key = v.key AND p.valid_to IS NULL
                  AND p.system_to IS NULL);

INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank)
SELECT v.* FROM (VALUES
  ('reference', 'nasdaq_trader', 1), ('reference', 'sec_tickers', 2), ('reference', 'openfigi', 3),
  ('reference', 'sec_13f_list', 4), ('reference', 'finance_database', 5)
) AS v(data_type, provider_key, rank)
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank r WHERE r.owner_scope = 'platform'
                  AND r.data_type = v.data_type AND r.provider_key = v.provider_key AND r.valid_to IS NULL
                  AND r.system_to IS NULL);
