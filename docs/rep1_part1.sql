-- REP1 — Replication Studio, sprint 1: security registry, fund holdings,
-- fund terms extraction, the fix/edit terms flow.
--
-- Applied by apps/api/scripts/_apply_rep1.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- PLATFORM vs ORG (Joe, 2026-10-22)
--   Public source data is PLATFORM-scoped (owner_scope = 'platform', org_id
--   NULL, read by everyone, written only under app.is_super_admin):
--     replica_instrument_classes, replica_holdings_sources,
--     replica_holdings_snapshots, replica_holdings_rows,
--     replica_source_documents, replica_terms_field_registry,
--     replica_term_readings
--   Each tenant runs its own instance — ORG-scoped (org_id NOT NULL, the
--   NULLIF org-isolation policy on every operation):
--     replica_securities, replica_terms_versions, replica_terms_actions
--   Nothing org-scoped is referenced by a platform row, so the Hollisworks
--   platform org can later publish models as a sub-adviser from the same
--   platform rows without a migration.
--
-- BI-TEMPORAL (Rule 3). Every table carries valid_from/valid_to/system_from/
-- system_to. Platform source rows are immutable (a trigger refuses every
-- UPDATE except a retraction that only sets system_to). The org rows that are
-- FK targets (replica_securities, replica_terms_versions) are archived on the
-- SYSTEM axis: the outgoing state is copied to a new row (fresh id,
-- archived_from_id = the live id, system_to = now()) and the live row keeps
-- its id — the portfolio.assets precedent (services/portfolio_securities.py).
--
-- Every table name is schema-qualified: `portfolio` is not on app_service's
-- search_path.

-- ── 0. Shared guards ────────────────────────────────────────────────────────

-- The ONLY way a protected row is ever deleted: the verify's fixture teardown,
-- on the operator connection (never app_service), with the transaction-local
-- marker set. The dealroom1/2 privileged-path precedent.
CREATE OR REPLACE FUNCTION portfolio.replica_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('replica.teardown', true), '') = 'fixture'
     AND current_user <> 'app_service'
$$;

-- Immutable platform rows: no UPDATE except a pure retraction (system_to
-- NULL -> a value, every other column unchanged); no DELETE except teardown.
-- DELETE returns OLD — returning NEW from a BEFORE DELETE trigger is NULL and
-- would silently skip the delete (fee36's lesson).
CREATE OR REPLACE FUNCTION portfolio.replica_immutable_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.replica_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'portfolio.% rows are immutable and are never deleted', TG_TABLE_NAME
      USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NULL AND NEW.system_to IS NOT NULL
     AND (to_jsonb(NEW) - 'system_to') = (to_jsonb(OLD) - 'system_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'portfolio.% rows are immutable: only a retraction (setting system_to) is allowed',
    TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

-- ── 1. Instrument classes (PLATFORM vocabulary) ─────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_instrument_classes (
  key           text PRIMARY KEY,
  label         text NOT NULL,
  description   text,
  enabled       boolean NOT NULL DEFAULT false,
  sort_order    integer NOT NULL DEFAULT 100,
  owner_scope   text NOT NULL DEFAULT 'platform',
  org_id        uuid,
  valid_from    timestamptz NOT NULL DEFAULT now(),
  valid_to      timestamptz,
  system_from   timestamptz NOT NULL DEFAULT now(),
  system_to     timestamptz,
  CONSTRAINT replica_instrument_classes_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_instrument_classes_key_chk CHECK (key ~ '^[a-z][a-z0-9_]*$')
);

INSERT INTO portfolio.replica_instrument_classes (key, label, description, enabled, sort_order) VALUES
  ('index_etf',          'Index ETF',              'An exchange-traded fund that tracks a published index.', true, 10),
  ('index_mutual_fund',  'Index mutual fund',      'An open-end mutual fund that tracks a published index.', true, 20),
  ('active_etf',         'Active ETF',             'Later phase — not yet supported.', false, 30),
  ('active_mutual_fund', 'Active mutual fund',     'Later phase — not yet supported.', false, 40),
  ('closed_end_fund',    'Closed-end fund',        'Later phase — not yet supported.', false, 50),
  ('fixed_income_fund',  'Fixed income index fund','Later phase — not yet supported.', false, 60)
ON CONFLICT (key) DO NOTHING;

-- ── 2. Holdings sources (PLATFORM) — parsers are code, which source a fund
--       uses is data ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_holdings_sources (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key                text NOT NULL,
  issuer             text NOT NULL,
  url_template       text,
  parser             text NOT NULL,
  format             text NOT NULL,
  active             boolean NOT NULL DEFAULT false,
  match_tickers      text[] NOT NULL DEFAULT '{}',
  priority           integer NOT NULL DEFAULT 100,
  terms_of_use_note  text,
  confirmation_note  text,
  confirmed_on       date,
  owner_scope        text NOT NULL DEFAULT 'platform',
  org_id             uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT replica_holdings_sources_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_holdings_sources_parser_chk CHECK (parser IN ('ssga_xlsx', 'invesco_csv', 'ishares_csv', 'sec_nport')),
  CONSTRAINT replica_holdings_sources_format_chk CHECK (format IN ('xlsx', 'csv', 'xml'))
);
CREATE UNIQUE INDEX IF NOT EXISTS replica_holdings_sources_key_live_uq
  ON portfolio.replica_holdings_sources (key) WHERE valid_to IS NULL AND system_to IS NULL;

INSERT INTO portfolio.replica_holdings_sources
  (key, issuer, url_template, parser, format, active, match_tickers, priority, terms_of_use_note, confirmation_note, confirmed_on)
SELECT * FROM (VALUES
  ('ssga_spdr', 'State Street Investment Management (SPDR)',
   'https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker_lower}.xlsx',
   'ssga_xlsx', 'xlsx', true, ARRAY['SPY', 'MDY', 'DIA']::text[], 10,
   'Holdings are published on ssga.com for investors. The site terms were NOT reviewed by rep1: confirm with counsel that automated daily retrieval and internal use are permitted, and that index-provider (S&P) constituent data may be stored. Never redistribute the raw file.',
   'rep1 Task 1b, 2026-10-22: one GET returned 200 xlsx (sheet "holdings"; header row 5: Name, Ticker, Identifier (CUSIP), SEDOL, Weight (percent), Sector, Shares Held, Local Currency; "Holdings: As of 21-Oct-2026"; 505 rows incl. US DOLLAR cash).',
   DATE '2026-10-22'),
  ('invesco', 'Invesco',
   'https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0?audienceType=Investor&action=download&ticker={ticker}',
   'invesco_csv', 'csv', false, ARRAY['QQQ', 'QQQM', 'RSP']::text[], 20,
   'Site terms NOT reviewed by rep1 — confirm automated access and storage are permitted before activating.',
   'rep1 Task 1b, 2026-10-22: the download URL answered HTTP 406 (empty body) to a non-browser client, twice. INACTIVE until an operator confirms a working request. The parser follows Invesco''s documented CSV columns (Fund Ticker, Security Identifier, Holding Ticker, Shares/Par Value, MarketValue, Weight, Name, Class of Shares, Sector, Date) — NOT confirmed against a live file. QQQ falls back to N-PORT (series S000101292).',
   NULL::date),
  ('ishares', 'BlackRock iShares',
   'https://www.ishares.com/us/products/{issuer_product_id}/{issuer_product_slug}/1467271812596.ajax?fileType=csv&fileName={ticker}_holdings&dataType=fund',
   'ishares_csv', 'csv', false, ARRAY[]::text[], 30,
   'Site terms NOT reviewed by rep1 — BlackRock''s site terms restrict automated collection; confirm before activating.',
   'rep1 Task 1b, 2026-10-22: the documented .ajax CSV URL for IVV answered 200 with content-type text/csv but a 2.3 MB HTML product page (a bot/JS gate), twice. INACTIVE. The parser follows the documented layout (9 preamble lines incl. "Fund Holdings as of", then Ticker, Name, Sector, Asset Class, Market Value, Weight (%), Notional Value, Quantity, Price, ...; no CUSIP column) — NOT confirmed against a live file. IVV falls back to N-PORT (series S000004310).',
   NULL::date),
  ('sec_nport', 'SEC EDGAR — Form N-PORT-P (any registered fund series)',
   'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sec_series_id}&type=NPORT-P&dateb=&owner=include&count=10&output=atom',
   'sec_nport', 'xml', true, ARRAY[]::text[], 1000,
   'Public SEC filing. SEC fair-access policy: declared User-Agent, at most 10 requests/second (services.edgar_fetch enforces both).',
   'rep1 Task 1b, 2026-10-22: the series-filtered atom feed for IVV (S000004310) returned 200; latest NPORT-P filed 2026-08-25. LAG: N-PORT is public only for each fiscal quarter''s third month, filed up to 60 days after month end — holdings are typically 2 to 5 months old. A fallback, never a daily source.',
   DATE '2026-10-22')
) AS v(key, issuer, url_template, parser, format, active, match_tickers, priority, terms_of_use_note, confirmation_note, confirmed_on)
WHERE NOT EXISTS (
  SELECT 1 FROM portfolio.replica_holdings_sources s
  WHERE s.key = v.key AND s.valid_to IS NULL AND s.system_to IS NULL
);

-- ── 3. The registry (ORG) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_securities (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  global_security_id   uuid NOT NULL REFERENCES portfolio.securities_global(id),
  instrument_class     text NOT NULL REFERENCES portfolio.replica_instrument_classes(key),
  ticker               text NOT NULL,
  cusip                text,
  isin                 text,
  fund_name            text,
  issuer               text,
  sec_cik              text,
  sec_series_id        text,
  sec_class_id         text,
  holdings_source_key  text NOT NULL,
  status               text NOT NULL DEFAULT 'draft',
  display_name         text NOT NULL,
  created_by           uuid REFERENCES public.users(id),
  archived_from_id     uuid REFERENCES portfolio.replica_securities(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT replica_securities_status_chk CHECK (status IN ('draft', 'active', 'retired')),
  -- Never a licensed index name as our product's name (Joe, 2026-10-22).
  CONSTRAINT replica_securities_display_name_chk CHECK (display_name LIKE 'Holdings of %'),
  CONSTRAINT replica_securities_ticker_chk CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  CONSTRAINT replica_securities_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS replica_securities_live_uq
  ON portfolio.replica_securities (org_id, global_security_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS replica_securities_org_idx ON portfolio.replica_securities (org_id, status);
CREATE INDEX IF NOT EXISTS replica_securities_global_idx ON portfolio.replica_securities (global_security_id);

-- ── 4. Holdings snapshots (PLATFORM, immutable) ─────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_holdings_snapshots (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fund_global_security_id uuid NOT NULL REFERENCES portfolio.securities_global(id),
  as_of                   date NOT NULL,
  source_key              text NOT NULL,
  source_url              text,
  accession_number        text,
  file_sha256             text NOT NULL,
  r2_key                  text,
  byte_size               bigint,
  row_count               integer NOT NULL,
  total_weight            numeric(20, 12),
  total_weight_ex_cash    numeric(20, 12),
  status                  text NOT NULL,
  validation              jsonb NOT NULL DEFAULT '{}'::jsonb,
  previous_snapshot_id    uuid REFERENCES portfolio.replica_holdings_snapshots(id),
  diff                    jsonb NOT NULL DEFAULT '{}'::jsonb,
  diff_exceeds_threshold  boolean NOT NULL DEFAULT false,
  fetched_at              timestamptz NOT NULL DEFAULT now(),
  owner_scope             text NOT NULL DEFAULT 'platform',
  org_id                  uuid,
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT replica_holdings_snapshots_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_holdings_snapshots_status_chk CHECK (status IN ('live', 'needs_review')),
  CONSTRAINT replica_holdings_snapshots_sha_chk CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT replica_holdings_snapshots_rows_chk CHECK (row_count >= 0),
  CONSTRAINT replica_holdings_snapshots_file_uq UNIQUE (fund_global_security_id, as_of, source_key, file_sha256)
);
CREATE INDEX IF NOT EXISTS replica_holdings_snapshots_fund_idx
  ON portfolio.replica_holdings_snapshots (fund_global_security_id, source_key, as_of DESC, fetched_at DESC);

-- ── 5. Holdings rows (PLATFORM, immutable) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_holdings_rows (
  id                              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_id                     uuid NOT NULL REFERENCES portfolio.replica_holdings_snapshots(id),
  row_num                         integer NOT NULL,
  raw_name                        text,
  raw_ticker                      text,
  raw_cusip                       text,
  raw_isin                        text,
  raw_sedol                       text,
  raw                             jsonb NOT NULL DEFAULT '{}'::jsonb,
  asset_type                      text NOT NULL,
  constituent_global_security_id  uuid REFERENCES portfolio.securities_global(id),
  resolution_state                text NOT NULL,
  resolved_via                    text,
  relationship_id                 uuid REFERENCES portfolio.securities_global_relationships(id),
  weight                          numeric(20, 12),
  shares                          numeric(28, 6),
  market_value                    numeric(28, 4),
  currency                        text,
  owner_scope                     text NOT NULL DEFAULT 'platform',
  org_id                          uuid,
  valid_from                      timestamptz NOT NULL DEFAULT now(),
  valid_to                        timestamptz,
  system_from                     timestamptz NOT NULL DEFAULT now(),
  system_to                       timestamptz,
  CONSTRAINT replica_holdings_rows_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_holdings_rows_asset_type_chk CHECK (asset_type IN ('equity', 'cash', 'future', 'other')),
  CONSTRAINT replica_holdings_rows_state_chk CHECK (resolution_state IN ('resolved', 'unresolved', 'not_applicable')),
  CONSTRAINT replica_holdings_rows_via_chk CHECK (resolved_via IS NULL OR resolved_via IN ('cusip', 'isin', 'ticker', 'sedol')),
  -- resolved <=> a constituent; never a guess without an identifier match
  CONSTRAINT replica_holdings_rows_resolved_chk CHECK (
    (resolution_state = 'resolved') = (constituent_global_security_id IS NOT NULL)
    AND (resolution_state <> 'resolved' OR resolved_via IS NOT NULL)),
  -- unresolved => queued on the shared relationships table (proposal pattern)
  CONSTRAINT replica_holdings_rows_queued_chk CHECK (resolution_state <> 'unresolved' OR relationship_id IS NOT NULL),
  CONSTRAINT replica_holdings_rows_row_uq UNIQUE (snapshot_id, row_num)
);
CREATE INDEX IF NOT EXISTS replica_holdings_rows_constituent_idx
  ON portfolio.replica_holdings_rows (constituent_global_security_id) WHERE constituent_global_security_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS replica_holdings_rows_rel_idx
  ON portfolio.replica_holdings_rows (relationship_id) WHERE relationship_id IS NOT NULL;

-- ── 6. Source documents (PLATFORM, immutable) — prospectuses ───────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_source_documents (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fund_global_security_id uuid NOT NULL REFERENCES portfolio.securities_global(id),
  doc_kind                text NOT NULL,
  form_type               text NOT NULL,
  accession_number        text,
  filing_date             date,
  primary_document        text,
  source_url              text NOT NULL,
  r2_key                  text,
  sha256                  text NOT NULL,
  byte_size               bigint,
  extracted_text          text,
  fetched_at              timestamptz NOT NULL DEFAULT now(),
  owner_scope             text NOT NULL DEFAULT 'platform',
  org_id                  uuid,
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT replica_source_documents_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_source_documents_kind_chk CHECK (doc_kind IN ('summary_prospectus', 'prospectus')),
  CONSTRAINT replica_source_documents_form_chk CHECK (form_type IN ('497K', '485BPOS', '497')),
  CONSTRAINT replica_source_documents_sha_chk CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT replica_source_documents_file_uq UNIQUE (fund_global_security_id, sha256)
);

-- ── 7. Fund terms field registry (PLATFORM) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_terms_field_registry (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  field_key          text NOT NULL,
  display_label      text NOT NULL,
  data_type          text NOT NULL,
  section            text,
  sort_order         integer,
  unit               text,
  enum_values        text[],
  synonyms           text[] NOT NULL DEFAULT '{}',
  trap_rule          text,
  extraction_method  text NOT NULL DEFAULT 'model',
  is_critical        boolean NOT NULL DEFAULT false,
  disclosure_only    boolean NOT NULL DEFAULT false,
  description        text NOT NULL,
  value_shape        text NOT NULL DEFAULT 'scalar',
  retired_at         timestamptz,
  owner_scope        text NOT NULL DEFAULT 'platform',
  org_id             uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT replica_terms_field_registry_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_terms_field_registry_type_chk CHECK (data_type IN ('text', 'numeric', 'boolean', 'date')),
  CONSTRAINT replica_terms_field_registry_method_chk CHECK (extraction_method IN ('model', 'data')),
  CONSTRAINT replica_terms_field_registry_shape_chk CHECK (value_shape = 'scalar'),
  CONSTRAINT replica_terms_field_registry_key_chk CHECK (field_key ~ '^[a-z][a-z0-9_]*$')
);
CREATE UNIQUE INDEX IF NOT EXISTS replica_terms_field_registry_key_live_uq
  ON portfolio.replica_terms_field_registry (field_key) WHERE valid_to IS NULL AND system_to IS NULL;

INSERT INTO portfolio.replica_terms_field_registry
  (field_key, display_label, data_type, section, sort_order, unit, enum_values, synonyms, trap_rule,
   extraction_method, is_critical, disclosure_only, description)
SELECT * FROM (VALUES
  ('fund_name', 'Fund name', 'text', 'identity', 10, NULL, NULL::text[], ARRAY['name of the fund']::text[], NULL,
   'model', false, false, 'The fund''s full legal name as it appears on the prospectus cover.'),
  ('ticker', 'Ticker', 'text', 'identity', 20, NULL, NULL, ARRAY[]::text[], NULL,
   'data', false, false, 'Exchange ticker of the share class (from the registry / SEC ticker file).'),
  ('issuer', 'Issuer', 'text', 'identity', 30, NULL, NULL, ARRAY['sponsor', 'adviser']::text[], NULL,
   'data', false, false, 'The fund''s sponsor or registrant (from the registry / SEC).'),
  ('instrument_class', 'Instrument class', 'text', 'identity', 40, NULL, NULL, ARRAY[]::text[], NULL,
   'data', false, false, 'Index ETF or index mutual fund (from the registry entry).'),
  ('sec_cik', 'SEC CIK', 'text', 'identity', 50, NULL, NULL, ARRAY[]::text[], NULL,
   'data', false, false, 'SEC Central Index Key of the registrant (company_tickers_mf.json).'),
  ('sec_series_id', 'SEC series id', 'text', 'identity', 51, NULL, NULL, ARRAY[]::text[], NULL,
   'data', false, false, 'SEC series identifier (S000…).'),
  ('sec_class_id', 'SEC class id', 'text', 'identity', 52, NULL, NULL, ARRAY[]::text[], NULL,
   'data', false, false, 'SEC share-class identifier (C000…).'),
  ('index_name', 'Index (disclosed term)', 'text', 'index', 100, NULL, NULL,
   ARRAY['underlying index', 'the index', 'benchmark index']::text[],
   'Report the index the fund seeks to track, verbatim. This is a disclosed term only — it is never used as the name of anything we build.',
   'model', true, true, 'The name of the index the fund seeks to track (its "Underlying Index").'),
  ('investment_objective', 'Investment objective', 'text', 'index', 110, NULL, NULL,
   ARRAY['investment objective', 'seeks to']::text[], NULL,
   'model', false, false, 'The fund''s stated investment objective, one sentence.'),
  ('weighting_method', 'Weighting method', 'text', 'index', 120, NULL,
   ARRAY['market_cap', 'float_adjusted_market_cap', 'modified_market_cap', 'equal', 'price', 'fundamental', 'other']::text[],
   ARRAY['capitalization-weighted', 'float-adjusted', 'modified market capitalization', 'equal-weighted']::text[],
   'A "modified" market-cap index caps single weights — that is modified_market_cap, not market_cap.',
   'model', true, false, 'How the index weights its constituents.'),
  ('rebalance_frequency', 'Rebalance frequency', 'text', 'index', 130, NULL,
   ARRAY['daily', 'monthly', 'quarterly', 'semi_annual', 'annual', 'other']::text[],
   ARRAY['rebalanced', 'rebalancing']::text[],
   'Rebalancing (weights reset) and reconstitution (membership reviewed) can differ — report rebalancing here.',
   'model', true, false, 'How often the index (and so the fund) rebalances.'),
  ('reconstitution_schedule', 'Reconstitution schedule', 'text', 'index', 140, NULL, NULL,
   ARRAY['reconstituted', 'reconstitution', 'annual review']::text[], NULL,
   'model', false, false, 'When the index membership is reviewed / reconstituted, in words (e.g. "annually in December").'),
  ('holdings_count_target', 'Constituent count (stated)', 'numeric', 'index', 150, 'count', NULL,
   ARRAY['number of securities', 'composed of']::text[], NULL,
   'model', false, false, 'The number of securities the index holds, if the prospectus states one.'),
  ('holdings_count_latest', 'Constituent count (latest holdings)', 'numeric', 'index', 151, 'count', NULL,
   ARRAY[]::text[], NULL,
   'data', false, false, 'Number of non-cash rows in the latest live holdings snapshot.'),
  ('expense_ratio_gross_pct', 'Expense ratio — gross', 'numeric', 'costs', 200, 'percent', NULL,
   ARRAY['total annual fund operating expenses']::text[],
   'Percent as a number (0.20 means 0.20%). Gross is BEFORE any fee waiver or expense reimbursement.',
   'model', true, false, 'Total annual fund operating expenses before waivers, percent.'),
  ('expense_ratio_net_pct', 'Expense ratio — net', 'numeric', 'costs', 210, 'percent', NULL,
   ARRAY['after fee waiver', 'net expenses']::text[],
   'Percent as a number. Net is AFTER waivers/reimbursements; equal to gross when there is no waiver.',
   'model', true, false, 'Total annual fund operating expenses after waivers, percent.'),
  ('distribution_frequency', 'Distribution frequency', 'text', 'distributions', 300, NULL,
   ARRAY['monthly', 'quarterly', 'semi_annual', 'annual', 'other']::text[],
   ARRAY['dividends', 'distributions']::text[], NULL,
   'model', false, false, 'How often the fund pays dividends / distributions.'),
  ('securities_lending', 'Securities lending', 'boolean', 'operations', 310, NULL, NULL,
   ARRAY['lending portfolio securities', 'securities lending']::text[],
   'true only when the fund itself lends portfolio securities.',
   'model', false, false, 'Whether the fund lends its portfolio securities.'),
  ('inception_date', 'Inception date', 'date', 'dates', 400, NULL, NULL,
   ARRAY['commenced operations', 'inception']::text[], NULL,
   'model', false, false, 'The date the fund (share class) commenced operations.'),
  ('prospectus_date', 'Prospectus date', 'date', 'dates', 410, NULL, NULL,
   ARRAY['prospectus dated', 'summary prospectus dated']::text[], NULL,
   'model', false, false, 'The date of the prospectus / summary prospectus read.')
) AS v(field_key, display_label, data_type, section, sort_order, unit, enum_values, synonyms, trap_rule,
       extraction_method, is_critical, disclosure_only, description)
WHERE NOT EXISTS (
  SELECT 1 FROM portfolio.replica_terms_field_registry r
  WHERE r.field_key = v.field_key AND r.valid_to IS NULL AND r.system_to IS NULL
);

-- ── 8. Term readings (PLATFORM, immutable) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_term_readings (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fund_global_security_id uuid NOT NULL REFERENCES portfolio.securities_global(id),
  source_document_id      uuid REFERENCES portfolio.replica_source_documents(id),
  extraction_run_id       uuid NOT NULL,
  field_key               text NOT NULL,
  value                   jsonb,
  value_normalized        text,
  source                  text NOT NULL,
  status                  text NOT NULL DEFAULT 'ok',
  error                   text,
  data_source             text,
  deployment_name         text,
  provider_model          text,
  proxy_model_id          text,
  ensemble_config_id      uuid REFERENCES public.ai_ensemble_configs(id),
  prompt_version          text,
  prompt_prefix_hash      text,
  source_quote            text,
  quote_verified          boolean,
  value_in_quote          boolean,
  raw_char_start          integer,
  raw_char_end            integer,
  input_tokens            integer,
  output_tokens           integer,
  cost_usd                numeric(14, 8),
  latency_ms              integer,
  call_id                 text,
  metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at              timestamptz NOT NULL DEFAULT now(),
  owner_scope             text NOT NULL DEFAULT 'platform',
  org_id                  uuid,
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT replica_term_readings_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_term_readings_source_chk CHECK (source IN ('model_1', 'model_2', 'data')),
  CONSTRAINT replica_term_readings_status_chk CHECK (status IN ('ok', 'failed', 'model_mismatch', 'invalid')),
  CONSTRAINT replica_term_readings_field_chk CHECK (btrim(field_key) <> ''),
  CONSTRAINT replica_term_readings_data_chk CHECK (source <> 'data' OR (deployment_name IS NULL AND data_source IS NOT NULL)),
  CONSTRAINT replica_term_readings_model_chk CHECK (source = 'data' OR deployment_name IS NOT NULL),
  CONSTRAINT replica_term_readings_span_chk CHECK (
    (raw_char_start IS NULL AND raw_char_end IS NULL) OR (raw_char_start >= 0 AND raw_char_end >= raw_char_start))
);
CREATE INDEX IF NOT EXISTS replica_term_readings_fund_idx
  ON portfolio.replica_term_readings (fund_global_security_id, extraction_run_id);
CREATE INDEX IF NOT EXISTS replica_term_readings_doc_idx
  ON portfolio.replica_term_readings (source_document_id) WHERE source_document_id IS NOT NULL;

-- ── 9. Terms versions (ORG) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_terms_versions (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 uuid NOT NULL REFERENCES public.organizations(id),
  replica_security_id    uuid NOT NULL REFERENCES portfolio.replica_securities(id),
  version_no             integer NOT NULL,
  status                 text NOT NULL DEFAULT 'draft',
  term_values            jsonb NOT NULL DEFAULT '{}'::jsonb,
  departures             jsonb NOT NULL DEFAULT '[]'::jsonb,
  needs_review_fields    text[] NOT NULL DEFAULT '{}',
  extraction             jsonb NOT NULL DEFAULT '{}'::jsonb,
  maker_ids              uuid[] NOT NULL DEFAULT '{}',
  created_by             uuid REFERENCES public.users(id),
  checker_id             uuid REFERENCES public.users(id),
  approved_at            timestamptz,
  rejected_at            timestamptz,
  decision_note          text,
  supersedes_version_id  uuid REFERENCES portfolio.replica_terms_versions(id),
  archived_from_id       uuid REFERENCES portfolio.replica_terms_versions(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz,
  system_from            timestamptz NOT NULL DEFAULT now(),
  system_to              timestamptz,
  CONSTRAINT replica_terms_versions_status_chk CHECK (status IN ('draft', 'approved', 'superseded', 'rejected')),
  CONSTRAINT replica_terms_versions_no_chk CHECK (version_no > 0),
  CONSTRAINT replica_terms_versions_values_chk CHECK (jsonb_typeof(term_values) = 'object'),
  CONSTRAINT replica_terms_versions_departures_chk CHECK (jsonb_typeof(departures) = 'array'),
  CONSTRAINT replica_terms_versions_approved_chk CHECK (
    status NOT IN ('approved', 'superseded') OR (checker_id IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT replica_terms_versions_rejected_chk CHECK (status <> 'rejected' OR (checker_id IS NOT NULL AND rejected_at IS NOT NULL)),
  -- maker-checker in the database: the checker is never one of the makers
  CONSTRAINT replica_terms_versions_maker_checker_chk CHECK (checker_id IS NULL OR NOT (checker_id = ANY (maker_ids))),
  CONSTRAINT replica_terms_versions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS replica_terms_versions_one_approved_uq
  ON portfolio.replica_terms_versions (replica_security_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS replica_terms_versions_one_draft_uq
  ON portfolio.replica_terms_versions (replica_security_id)
  WHERE status = 'draft' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS replica_terms_versions_no_uq
  ON portfolio.replica_terms_versions (replica_security_id, version_no)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS replica_terms_versions_org_idx ON portfolio.replica_terms_versions (org_id);

-- Approved versions are never edited. A draft is edited (system-axis archive
-- first). approved -> superseded is the ONLY change an approved row takes, and
-- it changes nothing but status. Archive copies and superseded/rejected rows
-- take no change at all.
CREATE OR REPLACE FUNCTION portfolio.replica_terms_version_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.replica_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'replica terms versions are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived replica terms version is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('superseded', 'rejected') THEN
    RAISE EXCEPTION 'a % replica terms version is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'approved' THEN
    IF NEW.status = 'superseded'
       AND (to_jsonb(NEW) - 'status' - 'system_from') = (to_jsonb(OLD) - 'status' - 'system_from') THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'an approved replica terms version is never edited (only superseded by approving a new one)'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

-- ── 10. Human-action log (ORG, append-only) ─────────────────────────────────
-- One row per human action; its id is the source_id of the domain_events row
-- (source_type 'replica_terms_action') that records it as a workflow event.
CREATE TABLE IF NOT EXISTS portfolio.replica_terms_actions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  replica_security_id  uuid NOT NULL REFERENCES portfolio.replica_securities(id),
  terms_version_id     uuid REFERENCES portfolio.replica_terms_versions(id),
  action               text NOT NULL,
  field_key            text,
  before_value         jsonb,
  after_value          jsonb,
  reason               text,
  actor_id             uuid REFERENCES public.users(id),
  correction_id        uuid REFERENCES public.document_field_corrections(id),
  detail               jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT replica_terms_actions_action_chk CHECK (action IN
    ('add_security', 'extract', 'correct', 'change', 'approve', 'reject', 'refresh_holdings', 'holdings_review')),
  CONSTRAINT replica_terms_actions_reason_chk CHECK (action <> 'change' OR length(btrim(COALESCE(reason, ''))) > 0),
  CONSTRAINT replica_terms_actions_correction_chk CHECK (action <> 'correct' OR correction_id IS NOT NULL),
  CONSTRAINT replica_terms_actions_human_chk CHECK (
    action NOT IN ('correct', 'change', 'approve', 'reject') OR actor_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS replica_terms_actions_version_idx ON portfolio.replica_terms_actions (terms_version_id);
CREATE INDEX IF NOT EXISTS replica_terms_actions_security_idx ON portfolio.replica_terms_actions (replica_security_id, created_at);


-- ── 10b. Daily holdings refresh log (PLATFORM, append-only) ───────────────
-- The weekday refresh (services.replica_schedule) writes a 'claim' row first —
-- the partial unique index on run_date is what makes it once per day, by the
-- database — and a 'finish' row (status + per-fund results) at the end. Two
-- rows instead of an UPDATE keeps it append-only (Rule 3).
CREATE TABLE IF NOT EXISTS portfolio.replica_refresh_events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_date        date NOT NULL,
  trigger_source  text NOT NULL,
  phase           text NOT NULL,
  claim_id        uuid REFERENCES portfolio.replica_refresh_events(id),
  status          text NOT NULL,
  host            text,
  results         jsonb NOT NULL DEFAULT '[]'::jsonb,
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  owner_scope     text NOT NULL DEFAULT 'platform',
  org_id          uuid,
  valid_from      timestamptz NOT NULL DEFAULT now(),
  valid_to        timestamptz,
  system_from     timestamptz NOT NULL DEFAULT now(),
  system_to       timestamptz,
  CONSTRAINT replica_refresh_events_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT replica_refresh_events_trigger_chk CHECK (trigger_source IN ('schedule', 'manual')),
  CONSTRAINT replica_refresh_events_phase_chk CHECK (phase IN ('claim', 'finish')),
  CONSTRAINT replica_refresh_events_status_chk CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
  CONSTRAINT replica_refresh_events_shape_chk CHECK (
    (phase = 'claim' AND claim_id IS NULL AND status = 'running')
    OR (phase = 'finish' AND claim_id IS NOT NULL AND status <> 'running'))
);
CREATE UNIQUE INDEX IF NOT EXISTS replica_refresh_events_day_uq
  ON portfolio.replica_refresh_events (run_date) WHERE trigger_source = 'schedule' AND phase = 'claim';
CREATE UNIQUE INDEX IF NOT EXISTS replica_refresh_events_finish_uq
  ON portfolio.replica_refresh_events (claim_id) WHERE phase = 'finish';

-- ── 11. Triggers ────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS replica_holdings_snapshots_immutable_trg ON portfolio.replica_holdings_snapshots;
CREATE TRIGGER replica_holdings_snapshots_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_holdings_snapshots
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_immutable_guard();
DROP TRIGGER IF EXISTS replica_holdings_rows_immutable_trg ON portfolio.replica_holdings_rows;
CREATE TRIGGER replica_holdings_rows_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_holdings_rows
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_immutable_guard();
DROP TRIGGER IF EXISTS replica_source_documents_immutable_trg ON portfolio.replica_source_documents;
CREATE TRIGGER replica_source_documents_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_source_documents
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_immutable_guard();
DROP TRIGGER IF EXISTS replica_term_readings_immutable_trg ON portfolio.replica_term_readings;
CREATE TRIGGER replica_term_readings_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_term_readings
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_immutable_guard();
DROP TRIGGER IF EXISTS replica_terms_actions_immutable_trg ON portfolio.replica_terms_actions;
CREATE TRIGGER replica_terms_actions_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_terms_actions
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_immutable_guard();
DROP TRIGGER IF EXISTS replica_refresh_events_immutable_trg ON portfolio.replica_refresh_events;
CREATE TRIGGER replica_refresh_events_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_refresh_events
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_immutable_guard();
DROP TRIGGER IF EXISTS replica_terms_versions_guard_trg ON portfolio.replica_terms_versions;
CREATE TRIGGER replica_terms_versions_guard_trg BEFORE UPDATE OR DELETE ON portfolio.replica_terms_versions
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_terms_version_guard();

-- ── 12. RLS — a policy for every operation each table takes ────────────────
-- PLATFORM tables: read by all; INSERT / UPDATE / DELETE under super admin
-- (UPDATE / DELETE are further limited by the immutability triggers).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['replica_instrument_classes', 'replica_holdings_sources', 'replica_holdings_snapshots',
                           'replica_holdings_rows', 'replica_source_documents', 'replica_terms_field_registry',
                           'replica_term_readings', 'replica_refresh_events'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_global_read', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (true)', t || '_global_read', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK '
                   '(NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_insert', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING '
                   '(NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'') WITH CHECK '
                   '(NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_update', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING '
                   '(NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_delete', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;

-- ORG tables: the caller's own org (NULLIF — an unset org GUC matches
-- nothing), or super admin, on every operation.
DO $$
DECLARE t text; op text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['replica_securities', 'replica_terms_versions', 'replica_terms_actions'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    FOREACH op IN ARRAY ARRAY['select', 'insert', 'update', 'delete'] LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_' || op, t);
      IF op = 'select' THEN
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING %s', t || '_org_select', t, pred);
      ELSIF op = 'insert' THEN
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
      ELSIF op = 'update' THEN
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t, pred, pred);
      ELSE
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
      END IF;
    END LOOP;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;

-- ── 13. document_field_corrections: target_type 'replica_terms' ─────────────
-- A CORRECTION (the read was wrong) of a draft fund-terms field. Org-scoped
-- (org_id NOT NULL, document_id NULL, target_id = the terms version), so the
-- existing org-isolation policy covers it and the global-read policy (which
-- lists note_terms / template_proposal only) does not. Both CHECKs widen to a
-- superset — every existing row still satisfies them.
ALTER TABLE public.document_field_corrections DROP CONSTRAINT IF EXISTS document_field_corrections_target_type_chk;
ALTER TABLE public.document_field_corrections ADD CONSTRAINT document_field_corrections_target_type_chk
  CHECK (target_type = ANY (ARRAY['document'::text, 'note_terms'::text, 'template_proposal'::text,
                                  'FEE_SCHEDULE_SPEC'::text, 'replica_terms'::text]));
ALTER TABLE public.document_field_corrections DROP CONSTRAINT IF EXISTS document_field_corrections_document_pairing_chk;
ALTER TABLE public.document_field_corrections ADD CONSTRAINT document_field_corrections_document_pairing_chk
  CHECK (((target_type = 'document'::text) AND (document_id IS NOT NULL) AND (org_id IS NOT NULL))
      OR ((target_type = ANY (ARRAY['note_terms'::text, 'template_proposal'::text])) AND (org_id IS NULL))
      OR ((target_type = 'FEE_SCHEDULE_SPEC'::text) AND (org_id IS NOT NULL) AND (document_id IS NULL))
      OR ((target_type = 'replica_terms'::text) AND (org_id IS NOT NULL) AND (document_id IS NULL)));

-- ── 14. Permissions — org_admin by default; Super Admin via "Super Admin
--        first" (no grant row needed); no other role ───────────────────────
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_replication', 'replication', 'view'),
  ('manage_replication', 'replication', 'manage'),
  ('approve_replication', 'replication', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_replication', 'manage_replication', 'approve_replication')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;
