-- altread1 part 1 — the smallest additions the documented Altruist read path needs (docs/altruist/ALTREAD1_NOTES.md).
--
-- public:
--   account_custodian_balances          D3 — the custodian's account_balance + its as-of date, per account and feed
--                                       source. VALID-axis (Rule 3): a changed balance closes the live row and inserts
--                                       a new one; one live row per (org, account, source_system). Deliberately NOT
--                                       account_balances_daily: that table is the treasury's cash source and D3 leaves
--                                       treasury unchanged.
--   altruist_connections.refresh_token_issued_at
--                                       D1 — when the stored refresh token was issued (connect, or a refresh that
--                                       returned a NEW refresh token). + 28 days = its documented expiry. NULL = a
--                                       connection made before this sprint (the service falls back to created_at).
--   account_lot_method_settings.lot_source
--                                       D4 — whose lots the tax engine reads for the account: 'hollisworks' (the
--                                       default — nothing changes for any existing row) or 'custodian'. Set through the
--                                       existing maker-checker lot-method change.
-- portfolio:
--   custodian_tax_lots                  D4 — custodian-sourced tax lots (one row per lot of the cost-basis feed), beside
--                                       portfolio.tax_lots, never in it (eight unfiltered readers + three FKs on
--                                       tax_lots — see the notes §1c). VALID-axis; one live row per
--                                       (org, account, source_system, external_id).
-- cpsim (cpsim_app only):
--   book_lots                           the simulated custodian's uploaded lots (D5); book_uploads_kind_chk + 'lots'.
--
-- RLS on every new public / portfolio table, one policy per operation (SELECT / INSERT / UPDATE / DELETE), every org
-- GUC read through NULLIF. Re-runnable: CREATE … IF NOT EXISTS; constraints and policies dropped and re-added by name.

BEGIN;

-- ── D3: the custodian balance ─────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.account_custodian_balances (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations (id),
  account_id       uuid NOT NULL REFERENCES public.accounts (id),
  source_system    text NOT NULL,
  environment      text NOT NULL,
  account_balance  numeric NOT NULL,
  as_of_date       date NOT NULL,
  custodian_status text,
  fetched_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz
);
ALTER TABLE public.account_custodian_balances DROP CONSTRAINT IF EXISTS account_custodian_balances_source_chk;
ALTER TABLE public.account_custodian_balances ADD CONSTRAINT account_custodian_balances_source_chk
  CHECK (source_system IN ('altruist', 'altruist_simulator'));
ALTER TABLE public.account_custodian_balances DROP CONSTRAINT IF EXISTS account_custodian_balances_env_chk;
ALTER TABLE public.account_custodian_balances ADD CONSTRAINT account_custodian_balances_env_chk
  CHECK (environment IN ('sandbox', 'production', 'simulator')
         AND ((environment = 'simulator') = (source_system = 'altruist_simulator')));
CREATE UNIQUE INDEX IF NOT EXISTS account_custodian_balances_live_uq
  ON public.account_custodian_balances (org_id, account_id, source_system) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS account_custodian_balances_acct_idx
  ON public.account_custodian_balances (org_id, account_id, valid_from DESC);

ALTER TABLE public.account_custodian_balances ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS account_custodian_balances_org_select ON public.account_custodian_balances;
DROP POLICY IF EXISTS account_custodian_balances_org_insert ON public.account_custodian_balances;
DROP POLICY IF EXISTS account_custodian_balances_org_update ON public.account_custodian_balances;
DROP POLICY IF EXISTS account_custodian_balances_org_delete ON public.account_custodian_balances;
CREATE POLICY account_custodian_balances_org_select ON public.account_custodian_balances FOR SELECT USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY account_custodian_balances_org_insert ON public.account_custodian_balances FOR INSERT WITH CHECK (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY account_custodian_balances_org_update ON public.account_custodian_balances FOR UPDATE USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY account_custodian_balances_org_delete ON public.account_custodian_balances FOR DELETE USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON public.account_custodian_balances TO app_service;
REVOKE ALL ON public.account_custodian_balances FROM anon, authenticated;

-- ── D1: when the refresh token was issued ─────────────────────────────────────────────────────────────────────
ALTER TABLE public.altruist_connections ADD COLUMN IF NOT EXISTS refresh_token_issued_at timestamptz;

-- ── D4: whose lots the tax engine reads ───────────────────────────────────────────────────────────────────────
ALTER TABLE public.account_lot_method_settings ADD COLUMN IF NOT EXISTS lot_source text NOT NULL DEFAULT 'hollisworks';
ALTER TABLE public.account_lot_method_settings DROP CONSTRAINT IF EXISTS account_lot_method_settings_lot_source_chk;
ALTER TABLE public.account_lot_method_settings ADD CONSTRAINT account_lot_method_settings_lot_source_chk
  CHECK (lot_source IN ('hollisworks', 'custodian'));

-- ── D4: custodian-sourced tax lots ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.custodian_tax_lots (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 uuid NOT NULL REFERENCES public.organizations (id),
  account_id             uuid NOT NULL REFERENCES public.accounts (id),
  source_system          text NOT NULL,
  environment            text NOT NULL,
  external_id            text NOT NULL,
  global_security_id     uuid,
  cusip                  text,
  symbol                 text,
  description            text,
  quantity               numeric NOT NULL,
  market_value           numeric,
  cost_basis_unadjusted  numeric,
  cost_basis_adjusted    numeric,
  unrealized_gain_loss   numeric,
  open_date              date,
  as_of_date             date NOT NULL,
  fetched_at             timestamptz NOT NULL DEFAULT now(),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz
);
ALTER TABLE portfolio.custodian_tax_lots DROP CONSTRAINT IF EXISTS custodian_tax_lots_source_chk;
ALTER TABLE portfolio.custodian_tax_lots ADD CONSTRAINT custodian_tax_lots_source_chk
  CHECK (source_system IN ('altruist', 'altruist_simulator'));
ALTER TABLE portfolio.custodian_tax_lots DROP CONSTRAINT IF EXISTS custodian_tax_lots_env_chk;
ALTER TABLE portfolio.custodian_tax_lots ADD CONSTRAINT custodian_tax_lots_env_chk
  CHECK (environment IN ('sandbox', 'production', 'simulator')
         AND ((environment = 'simulator') = (source_system = 'altruist_simulator')));
ALTER TABLE portfolio.custodian_tax_lots DROP CONSTRAINT IF EXISTS custodian_tax_lots_ext_chk;
ALTER TABLE portfolio.custodian_tax_lots ADD CONSTRAINT custodian_tax_lots_ext_chk
  CHECK (length(btrim(external_id)) BETWEEN 1 AND 200);
CREATE UNIQUE INDEX IF NOT EXISTS custodian_tax_lots_live_uq
  ON portfolio.custodian_tax_lots (org_id, account_id, source_system, external_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS custodian_tax_lots_acct_idx
  ON portfolio.custodian_tax_lots (org_id, account_id) WHERE valid_to IS NULL;

ALTER TABLE portfolio.custodian_tax_lots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS custodian_tax_lots_org_select ON portfolio.custodian_tax_lots;
DROP POLICY IF EXISTS custodian_tax_lots_org_insert ON portfolio.custodian_tax_lots;
DROP POLICY IF EXISTS custodian_tax_lots_org_update ON portfolio.custodian_tax_lots;
DROP POLICY IF EXISTS custodian_tax_lots_org_delete ON portfolio.custodian_tax_lots;
CREATE POLICY custodian_tax_lots_org_select ON portfolio.custodian_tax_lots FOR SELECT USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY custodian_tax_lots_org_insert ON portfolio.custodian_tax_lots FOR INSERT WITH CHECK (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY custodian_tax_lots_org_update ON portfolio.custodian_tax_lots FOR UPDATE USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
CREATE POLICY custodian_tax_lots_org_delete ON portfolio.custodian_tax_lots FOR DELETE USING (
  org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.custodian_tax_lots TO app_service;
REVOKE ALL ON portfolio.custodian_tax_lots FROM anon, authenticated;

-- ── cpsim: the simulated custodian's uploaded lots (cpsim_app only) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cpsim.book_lots (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  counterparty_id        uuid NOT NULL REFERENCES cpsim.counterparties (id),
  account_external_id    text NOT NULL,
  external_id            text NOT NULL,
  as_of                  date NOT NULL,
  security_id            uuid NOT NULL,
  cusip                  text,
  isin                   text,
  ticker                 text,
  name                   text NOT NULL,
  quantity               numeric NOT NULL,
  market_value           numeric,
  cost_basis_unadjusted  numeric NOT NULL,
  cost_basis_adjusted    numeric NOT NULL,
  open_date              date NOT NULL,
  currency_code          text NOT NULL,
  upload_id              uuid REFERENCES cpsim.book_uploads (id),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz
);
ALTER TABLE cpsim.book_lots DROP CONSTRAINT IF EXISTS book_lots_ext_chk;
ALTER TABLE cpsim.book_lots ADD CONSTRAINT book_lots_ext_chk CHECK (external_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$');
ALTER TABLE cpsim.book_lots DROP CONSTRAINT IF EXISTS book_lots_ccy_chk;
ALTER TABLE cpsim.book_lots ADD CONSTRAINT book_lots_ccy_chk CHECK (currency_code ~ '^[A-Z]{3}$');
ALTER TABLE cpsim.book_lots DROP CONSTRAINT IF EXISTS book_lots_qty_chk;
ALTER TABLE cpsim.book_lots ADD CONSTRAINT book_lots_qty_chk CHECK (quantity > 0);
ALTER TABLE cpsim.book_lots DROP CONSTRAINT IF EXISTS book_lots_dates_chk;
ALTER TABLE cpsim.book_lots ADD CONSTRAINT book_lots_dates_chk CHECK (open_date <= as_of);
CREATE UNIQUE INDEX IF NOT EXISTS book_lots_live_uq ON cpsim.book_lots (counterparty_id, external_id)
  WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS book_lots_acct_idx ON cpsim.book_lots (counterparty_id, account_external_id, as_of)
  WHERE valid_to IS NULL;

ALTER TABLE cpsim.book_uploads DROP CONSTRAINT IF EXISTS book_uploads_kind_chk;
ALTER TABLE cpsim.book_uploads ADD CONSTRAINT book_uploads_kind_chk
  CHECK (kind IN ('households', 'accounts', 'positions', 'transactions', 'lots'));

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'app_service', 'litellm_service'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON cpsim.book_lots FROM %I', r);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON cpsim.book_lots FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON cpsim.book_lots TO cpsim_app;

COMMIT;
