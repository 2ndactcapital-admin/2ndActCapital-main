-- cpsim02 part 1 — the simulated custodian's BOOK, its OAuth grants, conformance runs, and the client-side enablers.
-- Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md. Notes: docs/cpsim/CPSIM02_NOTES.md (D1–D11).
--
-- cpsim schema (no org_id anywhere — a simulated counterparty is a PLATFORM object):
--   book_uploads        append-only upload log (who, when, file, sha256, kind, format, mode, counts, rejections).
--                       cpsim_app holds SELECT + INSERT only; a trigger refuses UPDATE / DELETE except an operator
--                       teardown by the table owner that sets cpsim.allow_teardown = 'fixture' (the shared
--                       _verify_cleanup convention).
--   book_households / book_accounts / book_positions / book_transactions
--                       the counterparty's custodian data. VALID-axis (Rule 3): an upload in REPLACE mode closes the
--                       live rows it replaces (valid_to = now()) and inserts the new ones; one live row per
--                       (counterparty, external_id) — partial unique indexes. A position / transaction names ONE
--                       security in portfolio.securities_global (security_id, read through cpsim_app's reference
--                       grant — no FK across schemas, by design: the book references public data by id only), or
--                       is a documented cash row (row_type 'cash', security_id NULL).
--   oauth_codes / oauth_tokens
--                       the simulated authorization server: codes single-use + short-lived, tokens cpsim_-prefixed and
--                       stored as sha256 only (+ last four), refresh tokens rotate.
--   conformance_runs    the last conformance-suite runs (the admin page shows the latest; the writer keeps 50).
--   counterparty_behaviour.webhook_url  the optional https override for webhook delivery (D10).
--   request_log.direction / latency_ms  outbound webhook deliveries are logged too (never the secret / signature).
-- public schema — exactly the client-side enablers D1/D2/D5 need, nothing else:
--   positions_source_chk                 + 'altruist_simulator' (the ledger tag of a simulator-fed row)
--   altruist_connections / altruist_oauth_states environment CHECKs + 'simulator'
--   cpsim_org_setting_guard()            also covers counterparty_sim.altruist_counterparty (a slug string)
--   counterparty_sim_may_connect(org, household)  a thin SECURITY DEFINER wrapper over cpsim.may_connect — the rule
--                                        exists ONCE; services (which hold no USAGE on schema cpsim) call this.
-- Re-runnable: CREATE … IF NOT EXISTS / OR REPLACE; constraints are dropped and re-added by name.

BEGIN;

-- ── the upload log (append-only) ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.book_uploads (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  uploaded_by      uuid,
  uploaded_at      timestamptz NOT NULL DEFAULT now(),
  file_name        text NOT NULL,
  sha256           text NOT NULL,
  kind             text NOT NULL,
  format           text NOT NULL,
  mode             text NOT NULL DEFAULT 'replace',
  rows_accepted    integer NOT NULL,
  rows_rejected    integer NOT NULL,
  rejections       jsonb NOT NULL DEFAULT '[]'::jsonb,
  accounts_named   text[] NOT NULL DEFAULT '{}',
  CONSTRAINT book_uploads_kind_chk CHECK (kind IN ('households', 'accounts', 'positions', 'transactions')),
  CONSTRAINT book_uploads_format_chk CHECK (format IN ('csv', 'json')),
  CONSTRAINT book_uploads_mode_chk CHECK (mode IN ('replace')),
  CONSTRAINT book_uploads_sha_chk CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT book_uploads_name_chk CHECK (length(btrim(file_name)) BETWEEN 1 AND 255),
  CONSTRAINT book_uploads_counts_chk CHECK (rows_accepted >= 0 AND rows_rejected >= 0),
  CONSTRAINT book_uploads_rejections_chk CHECK (jsonb_typeof(rejections) = 'array')
);
CREATE INDEX IF NOT EXISTS book_uploads_counterparty_idx ON cpsim.book_uploads (counterparty_id, uploaded_at DESC);

CREATE OR REPLACE FUNCTION cpsim.book_uploads_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  -- operator teardown of fixture rows only: the TABLE OWNER (the migration role — never cpsim_app, which holds no
  -- DELETE here anyway) with an explicit switch. (Supabase's postgres is not a superuser, so rolsuper cannot be used.)
  IF TG_OP = 'DELETE'
     AND COALESCE(current_setting('cpsim.allow_teardown', true), '') = 'fixture'
     AND current_user = (SELECT r.rolname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE c.oid = TG_RELID)
  THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'cpsim02: the book upload log is append-only (% refused)', TG_OP USING ERRCODE = '42501';
END $$;
CREATE OR REPLACE TRIGGER book_uploads_append_only_trg BEFORE UPDATE OR DELETE ON cpsim.book_uploads
  FOR EACH ROW EXECUTE FUNCTION cpsim.book_uploads_append_only();

-- ── the book (VALID axis; one live row per counterparty + external id) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.book_households (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  external_id      text NOT NULL,
  name             text NOT NULL,
  upload_id        uuid REFERENCES cpsim.book_uploads (id),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  CONSTRAINT book_households_ext_chk CHECK (external_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT book_households_name_chk CHECK (length(btrim(name)) BETWEEN 1 AND 200)
);
CREATE UNIQUE INDEX IF NOT EXISTS book_households_live_uq ON cpsim.book_households (counterparty_id, external_id)
  WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS cpsim.book_accounts (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id        uuid NOT NULL REFERENCES cpsim.counterparties (id),
  external_id            text NOT NULL,
  household_external_id  text NOT NULL,
  name                   text NOT NULL,
  account_type           text,
  upload_id              uuid REFERENCES cpsim.book_uploads (id),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz,
  CONSTRAINT book_accounts_ext_chk CHECK (external_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT book_accounts_hh_chk CHECK (household_external_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT book_accounts_name_chk CHECK (length(btrim(name)) BETWEEN 1 AND 200),
  CONSTRAINT book_accounts_type_chk CHECK (account_type IS NULL OR length(account_type) <= 60)
);
CREATE UNIQUE INDEX IF NOT EXISTS book_accounts_live_uq ON cpsim.book_accounts (counterparty_id, external_id)
  WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS book_accounts_hh_idx ON cpsim.book_accounts (counterparty_id, household_external_id)
  WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS cpsim.book_positions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id      uuid NOT NULL REFERENCES cpsim.counterparties (id),
  account_external_id  text NOT NULL,
  external_id          text NOT NULL,
  as_of                date NOT NULL,
  row_type             text NOT NULL DEFAULT 'security',
  security_id          uuid,
  cusip                text,
  isin                 text,
  ticker               text,
  name                 text NOT NULL,
  asset_type           text,
  quantity             numeric,
  market_value         numeric NOT NULL,
  cost_basis           numeric,
  currency_code        text NOT NULL,
  price_source         text NOT NULL,
  upload_id            uuid REFERENCES cpsim.book_uploads (id),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  CONSTRAINT book_positions_ext_chk CHECK (external_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT book_positions_row_type_chk CHECK (row_type IN ('security', 'cash')),
  CONSTRAINT book_positions_security_chk CHECK ((row_type = 'security') = (security_id IS NOT NULL)),
  CONSTRAINT book_positions_ccy_chk CHECK (currency_code ~ '^[A-Z]{3}$'),
  CONSTRAINT book_positions_price_source_chk CHECK (price_source IN ('uploaded', 'platform_price', 'cash')),
  CONSTRAINT book_positions_qty_chk CHECK (row_type = 'cash' OR quantity IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS book_positions_live_uq ON cpsim.book_positions (counterparty_id, external_id)
  WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS book_positions_acct_idx ON cpsim.book_positions (counterparty_id, account_external_id, as_of)
  WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS cpsim.book_transactions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id      uuid NOT NULL REFERENCES cpsim.counterparties (id),
  account_external_id  text NOT NULL,
  external_id          text NOT NULL,
  type                 text NOT NULL,
  trade_date           date NOT NULL,
  settle_date          date,
  row_type             text NOT NULL DEFAULT 'security',
  security_id          uuid,
  cusip                text,
  isin                 text,
  ticker               text,
  name                 text,
  quantity             numeric,
  price                numeric,
  gross_amount         numeric,
  fees                 numeric,
  taxes                numeric,
  net_amount           numeric,
  currency_code        text NOT NULL,
  upload_id            uuid REFERENCES cpsim.book_uploads (id),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  CONSTRAINT book_transactions_ext_chk CHECK (external_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$'),
  CONSTRAINT book_transactions_type_chk CHECK (type IN ('buy', 'sell', 'dividend', 'interest', 'fee', 'fee_expense',
                                                        'adjustment')),
  CONSTRAINT book_transactions_row_type_chk CHECK (row_type IN ('security', 'cash')),
  CONSTRAINT book_transactions_security_chk CHECK ((row_type = 'security') = (security_id IS NOT NULL)),
  CONSTRAINT book_transactions_ccy_chk CHECK (currency_code ~ '^[A-Z]{3}$'),
  CONSTRAINT book_transactions_dates_chk CHECK (settle_date IS NULL OR settle_date >= trade_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS book_transactions_live_uq ON cpsim.book_transactions (counterparty_id, external_id)
  WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS book_transactions_acct_idx ON cpsim.book_transactions
  (counterparty_id, account_external_id, trade_date) WHERE valid_to IS NULL;

-- ── the simulated authorization server ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.oauth_codes (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  credential_id    uuid NOT NULL REFERENCES cpsim.counterparty_credentials (id),
  code_hash        text NOT NULL,
  redirect_uri     text NOT NULL,
  scope            text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  used_at          timestamptz,
  CONSTRAINT oauth_codes_hash_chk CHECK (code_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT oauth_codes_redirect_chk CHECK (redirect_uri ~ '^https://[^\s#]+$' AND length(redirect_uri) <= 500),
  CONSTRAINT oauth_codes_scope_chk CHECK (scope ~ '^[a-z:_ ]{1,200}$'),
  CONSTRAINT oauth_codes_expiry_chk CHECK (expires_at > created_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS oauth_codes_hash_uq ON cpsim.oauth_codes (code_hash);

CREATE TABLE IF NOT EXISTS cpsim.oauth_tokens (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid NOT NULL REFERENCES cpsim.counterparties (id),
  credential_id    uuid NOT NULL REFERENCES cpsim.counterparty_credentials (id),
  grant_id         uuid NOT NULL REFERENCES cpsim.oauth_codes (id),
  kind             text NOT NULL,
  token_prefix     text NOT NULL,
  token_hash       text NOT NULL,
  last4            text NOT NULL,
  scope            text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  rotated_at       timestamptz,
  revoked_at       timestamptz,
  replaced_by      uuid REFERENCES cpsim.oauth_tokens (id),
  CONSTRAINT oauth_tokens_kind_chk CHECK (kind IN ('access', 'refresh')),
  CONSTRAINT oauth_tokens_prefix_chk CHECK ((kind = 'access' AND token_prefix = 'cpsim_at_')
                                            OR (kind = 'refresh' AND token_prefix = 'cpsim_rt_')),
  CONSTRAINT oauth_tokens_hash_chk CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT oauth_tokens_last4_chk CHECK (last4 ~ '^[0-9a-f]{4}$'),
  CONSTRAINT oauth_tokens_expiry_chk CHECK (expires_at > created_at),
  CONSTRAINT oauth_tokens_close_chk CHECK (rotated_at IS NULL OR kind = 'refresh')
);
CREATE UNIQUE INDEX IF NOT EXISTS oauth_tokens_hash_uq ON cpsim.oauth_tokens (token_hash);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant_idx ON cpsim.oauth_tokens (grant_id);

-- ── conformance runs ─────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.conformance_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id  uuid REFERENCES cpsim.counterparties (id),
  target           text NOT NULL,
  run_by           uuid,
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  passed           integer NOT NULL DEFAULT 0,
  failed           integer NOT NULL DEFAULT 0,
  results          jsonb NOT NULL DEFAULT '[]'::jsonb,
  CONSTRAINT conformance_runs_target_chk CHECK (target IN ('simulator', 'sandbox')),
  CONSTRAINT conformance_runs_counts_chk CHECK (passed >= 0 AND failed >= 0),
  CONSTRAINT conformance_runs_results_chk CHECK (jsonb_typeof(results) = 'array')
);
CREATE INDEX IF NOT EXISTS conformance_runs_cp_idx ON cpsim.conformance_runs (counterparty_id, started_at DESC);

-- ── webhook override (D10) and outbound deliveries in the request log ───────────────────────────────────────
ALTER TABLE cpsim.counterparty_behaviour ADD COLUMN IF NOT EXISTS webhook_url text;
ALTER TABLE cpsim.counterparty_behaviour DROP CONSTRAINT IF EXISTS counterparty_behaviour_webhook_url_chk;
ALTER TABLE cpsim.counterparty_behaviour ADD CONSTRAINT counterparty_behaviour_webhook_url_chk CHECK (
  webhook_url IS NULL OR (webhook_url ~ '^https://[^\s#?]+$' AND length(webhook_url) <= 500));

ALTER TABLE cpsim.request_log ADD COLUMN IF NOT EXISTS direction text NOT NULL DEFAULT 'in';
ALTER TABLE cpsim.request_log ADD COLUMN IF NOT EXISTS latency_ms integer;
ALTER TABLE cpsim.request_log DROP CONSTRAINT IF EXISTS request_log_direction_chk;
ALTER TABLE cpsim.request_log ADD CONSTRAINT request_log_direction_chk CHECK (direction IN ('in', 'out'));
ALTER TABLE cpsim.request_log DROP CONSTRAINT IF EXISTS request_log_latency_chk;
ALTER TABLE cpsim.request_log ADD CONSTRAINT request_log_latency_chk CHECK (latency_ms IS NULL OR latency_ms >= 0);
ALTER TABLE cpsim.request_log DROP CONSTRAINT IF EXISTS request_log_auth_chk;
ALTER TABLE cpsim.request_log ADD CONSTRAINT request_log_auth_chk CHECK (auth_outcome IN (
  'not_required', 'ok', 'missing', 'malformed', 'not_simulator_key', 'unknown_key', 'revoked', 'disabled',
  'unknown_counterparty',
  -- cpsim02: the authorization server, access tokens, behaviour, outbound webhooks
  'bad_request', 'unknown_client', 'bad_redirect', 'bad_grant', 'token_unknown', 'token_expired', 'token_revoked',
  'behaviour_reject', 'delivered', 'delivery_failed', 'unsigned'));

-- ── privileges: the new cpsim tables to cpsim_app only (the documented set) ─────────────────────────────────
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON cpsim.book_uploads, cpsim.book_households, cpsim.book_accounts, '
                     'cpsim.book_positions, cpsim.book_transactions, cpsim.oauth_codes, cpsim.oauth_tokens, '
                     'cpsim.conformance_runs FROM %I', r);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON cpsim.book_uploads, cpsim.book_households, cpsim.book_accounts, cpsim.book_positions,
  cpsim.book_transactions, cpsim.oauth_codes, cpsim.oauth_tokens, cpsim.conformance_runs FROM PUBLIC;
GRANT SELECT, INSERT ON cpsim.book_uploads TO cpsim_app;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON cpsim.book_uploads FROM cpsim_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON cpsim.book_households, cpsim.book_accounts, cpsim.book_positions,
  cpsim.book_transactions, cpsim.oauth_codes, cpsim.oauth_tokens, cpsim.conformance_runs TO cpsim_app;
REVOKE ALL ON FUNCTION cpsim.book_uploads_append_only() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cpsim.book_uploads_append_only() TO cpsim_app;

-- ── public: the ledger tag of a simulator-fed row (positions; transactions are checked in Python) ───────────
ALTER TABLE portfolio.positions DROP CONSTRAINT IF EXISTS positions_source_chk;
ALTER TABLE portfolio.positions ADD CONSTRAINT positions_source_chk CHECK (source_system = ANY (ARRAY[
  'reporting_tool_bd'::text, 'reporting_tool_addepar'::text, 'reporting_tool_orion'::text, 'reporting_tool_apx'::text,
  'reporting_tool_import'::text, 'altruist'::text, 'spv_subscriptions'::text, 'chancery'::text, 'manual'::text,
  'altruist_simulator'::text]));

-- ── public: the third environment ───────────────────────────────────────────────────────────────────────────
ALTER TABLE public.altruist_connections DROP CONSTRAINT IF EXISTS altruist_connections_environment_check;
ALTER TABLE public.altruist_connections ADD CONSTRAINT altruist_connections_environment_check CHECK (
  environment = ANY (ARRAY['sandbox'::text, 'production'::text, 'simulator'::text]));
ALTER TABLE public.altruist_oauth_states DROP CONSTRAINT IF EXISTS altruist_oauth_states_environment_check;
ALTER TABLE public.altruist_oauth_states ADD CONSTRAINT altruist_oauth_states_environment_check CHECK (
  environment = ANY (ARRAY['sandbox'::text, 'production'::text, 'simulator'::text]));

-- ── public: the org-setting guard covers the counterparty key too ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.cpsim_org_setting_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.setting_key IN ('counterparty_sim.allowed', 'counterparty_sim.altruist_counterparty')
     OR (TG_OP = 'UPDATE' AND OLD.setting_key IN ('counterparty_sim.allowed', 'counterparty_sim.altruist_counterparty'))
  THEN
    IF COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), 'false') <> 'true' THEN
      RAISE EXCEPTION 'cpsim: % is set only by a Super Admin (Admin → Counterparties)', NEW.setting_key
        USING ERRCODE = '42501';
    END IF;
    IF NEW.setting_key = 'counterparty_sim.allowed' AND jsonb_typeof(NEW.setting_value) <> 'boolean' THEN
      RAISE EXCEPTION 'cpsim01: counterparty_sim.allowed must be true or false' USING ERRCODE = '23514';
    END IF;
    IF NEW.setting_key = 'counterparty_sim.altruist_counterparty' AND NOT (
         jsonb_typeof(NEW.setting_value) = 'null'
         OR (jsonb_typeof(NEW.setting_value) = 'string'
             AND (NEW.setting_value #>> '{}') ~ '^[a-z0-9][a-z0-9-]{1,62}$')) THEN
      RAISE EXCEPTION 'cpsim02: counterparty_sim.altruist_counterparty must be a counterparty slug (or null)'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
-- (the trigger cpsim_org_setting_guard_trg from cpsim01 already calls this function; unchanged)

-- ── public: ONE definer wrapper over the cpsim01 rule (services hold no USAGE on schema cpsim) ─────────────
CREATE OR REPLACE FUNCTION public.counterparty_sim_may_connect(p_org uuid, p_household uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT cpsim.may_connect(p_org, p_household)
$$;
COMMENT ON FUNCTION public.counterparty_sim_may_connect(uuid, uuid) IS
  'cpsim02 — the Altruist-compatible client asks whether an org/household may use a simulated counterparty. A thin '
  'wrapper: the rule itself is cpsim.may_connect (cpsim01).';
REVOKE ALL ON FUNCTION public.counterparty_sim_may_connect(uuid, uuid) FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_service') THEN
    GRANT EXECUTE ON FUNCTION public.counterparty_sim_may_connect(uuid, uuid) TO app_service;
  END IF;
END $$;

COMMIT;
