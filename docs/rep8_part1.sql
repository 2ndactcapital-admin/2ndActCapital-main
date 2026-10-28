-- REP8 — Replication Studio, sprint 8 (phase 2 starts): the OPTIONS SLEEVE.
-- Buffer (defined-outcome) ETFs and covered-call / income ETFs replicated as
-- the underlying plus an options package; option contracts in the security
-- master; adviser-entered quotes (nothing fetched, guessed or defaulted); the
-- options TAX GATE's stored results; outcome-period rolls and expiry
-- settlement entries. Execution is manual instructions only.
--
-- Applied by apps/api/scripts/_apply_rep8.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT /
-- WHERE NOT EXISTS.
--
-- THE MIGRATION WRITES NO ORG ROWS. Platform seeds only: two instrument
-- classes and the buffer / income terms fields.
--
-- BI-TEMPORAL (Rule 3)
--   option_contracts      PLATFORM; a contract's terms never change (they ARE its
--                         identity — terms_key); written once, only closed.
--   option_packages       SYSTEM axis (FK target of legs, gate results and
--                         settlement entries): every change archives the outgoing
--                         state as a copy (archived_from_id, system_to) and the
--                         live row keeps its id; a roll is a NEW package
--                         (rolled_from_id), the old one becomes 'rolled'.
--   option_package_legs   VALID axis: written once, only ever closed.
--   adviser_quotes        INSERT-ONLY: a new quote is a new row; never updated.
--   options_tax_gate_results  VALID axis: a newer gate run closes the earlier rows.
--   option_settlement_entries SYSTEM-ish: pending → confirmed on the same id
--                         (the confirmation task points at it), nothing else.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- THE CUSTODY CLIFF. Nothing here trades. An options instruction is a manual
-- execution instruction a person carries out (CHECK: option_terms only on
-- routed_via 'manual').

-- ── 1. Security master: an option is a security ───────────────────────────
ALTER TABLE portfolio.securities_global DROP CONSTRAINT IF EXISTS securities_global_type_chk;
ALTER TABLE portfolio.securities_global ADD CONSTRAINT securities_global_type_chk CHECK (security_type = ANY (ARRAY[
  'equity', 'fixed_income', 'fund', 'private_fund', 'structured_note', 'index', 'real_asset', 'cash', 'other',
  'option']));

-- ── 2. Instrument classes (platform) ──────────────────────────────────────
INSERT INTO portfolio.replica_instrument_classes (key, label, description, enabled, sort_order)
VALUES
  ('buffer_etf', 'Buffer (defined-outcome) ETF',
   'Underlying plus a four-leg options package over an outcome period (cap, buffer). Options trades are manual '
   'execution instructions.', true, 30),
  ('income_etf', 'Covered-call / income ETF',
   'Underlying plus written calls by a rule (strike % out of the money or delta, tenor, coverage). Options trades '
   'are manual execution instructions.', true, 40)
ON CONFLICT (key) DO NOTHING;

-- ── 3. Terms registry: class-scoped fields (NULL = every class, as before) ─
ALTER TABLE portfolio.replica_terms_field_registry ADD COLUMN IF NOT EXISTS instrument_classes text[];

INSERT INTO portfolio.replica_terms_field_registry
  (field_key, display_label, data_type, section, sort_order, unit, enum_values, extraction_method, is_critical,
   disclosure_only, description, instrument_classes)
SELECT v.field_key, v.display_label, v.data_type, v.section, v.sort_order, v.unit, v.enum_values, 'model',
       v.is_critical, false, v.description, v.classes
FROM (VALUES
  ('underlying_reference', 'Underlying (index or fund)', 'text', 'options', 500, NULL, NULL::text[], true,
   'The index or fund whose return the outcome or the written calls are measured on, as named in the prospectus.',
   ARRAY['buffer_etf', 'income_etf']),
  ('outcome_period_start', 'Outcome period start', 'date', 'options', 510, NULL, NULL, true,
   'The first day of the current outcome period.', ARRAY['buffer_etf']),
  ('outcome_period_end', 'Outcome period end', 'date', 'options', 520, NULL, NULL, true,
   'The last day of the current outcome period (the options'' expiry).', ARRAY['buffer_etf']),
  ('cap_pct', 'Cap (gross, %)', 'numeric', 'options', 530, 'percent', NULL, true,
   'The maximum gross return over the outcome period, in percent (e.g. 15 for 15%).', ARRAY['buffer_etf']),
  ('buffer_pct', 'Buffer (%)', 'numeric', 'options', 540, 'percent', NULL, true,
   'The first losses absorbed over the outcome period, in percent (e.g. 10 for 10%).', ARRAY['buffer_etf']),
  ('floor_pct', 'Floor (%)', 'numeric', 'options', 550, 'percent', NULL, false,
   'The worst stated outcome, in percent, when the fund states one.', ARRAY['buffer_etf']),
  ('leg_long_put_strike_pct', 'Leg: long put strike (% of start)', 'numeric', 'options', 560, 'percent', NULL, true,
   'Strike of the purchased put as a percent of the outcome-period start level (typically 100).',
   ARRAY['buffer_etf']),
  ('leg_short_put_strike_pct', 'Leg: short put strike (% of start)', 'numeric', 'options', 570, 'percent', NULL,
   true, 'Strike of the written put that ends the buffer, as a percent of the start level (e.g. 90).',
   ARRAY['buffer_etf']),
  ('leg_short_call_strike_pct', 'Leg: short call strike (% of start)', 'numeric', 'options', 580, 'percent', NULL,
   true, 'Strike of the written call that sets the cap, as a percent of the start level (e.g. 115).',
   ARRAY['buffer_etf']),
  ('option_exercise_style', 'Option exercise style', 'text', 'options', 590, NULL,
   ARRAY['european', 'american'], false, 'Exercise style of the fund''s options (FLEX index options are European).',
   ARRAY['buffer_etf', 'income_etf']),
  ('call_strike_rule', 'Call-writing rule', 'text', 'options', 600, NULL, ARRAY['pct_otm', 'delta', 'other'], true,
   'How the written call''s strike is chosen: a percent out of the money, a delta, or another rule.',
   ARRAY['income_etf']),
  ('call_strike_pct_otm', 'Call strike (% out of the money)', 'numeric', 'options', 610, 'percent', NULL, false,
   'When the rule is percent out of the money: how far above the current level, in percent.', ARRAY['income_etf']),
  ('call_delta', 'Call delta', 'numeric', 'options', 620, NULL, NULL, false,
   'When the rule is delta: the target delta of the written call (e.g. 0.30).', ARRAY['income_etf']),
  ('call_tenor_days', 'Call tenor (days)', 'numeric', 'options', 630, 'days', NULL, true,
   'Days to expiry of each written call when it is written.', ARRAY['income_etf']),
  ('call_coverage_pct', 'Call coverage (%)', 'numeric', 'options', 640, 'percent', NULL, true,
   'The share of the portfolio the written calls cover, in percent.', ARRAY['income_etf'])
) AS v(field_key, display_label, data_type, section, sort_order, unit, enum_values, is_critical, description, classes)
WHERE NOT EXISTS (SELECT 1 FROM portfolio.replica_terms_field_registry r WHERE r.field_key = v.field_key
                  AND r.valid_to IS NULL AND r.system_to IS NULL);

-- ── 4. Option contracts (PLATFORM reference data) ─────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.option_contracts (
  id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  global_security_id            uuid NOT NULL REFERENCES portfolio.securities_global(id),
  underlying_global_security_id uuid NOT NULL REFERENCES portfolio.securities_global(id),
  underlying_kind               text NOT NULL,
  option_type                   text NOT NULL,
  strike                        numeric NOT NULL,
  expiry                        date NOT NULL,
  exercise_style                text NOT NULL,
  settlement                    text NOT NULL,
  is_flex                       boolean NOT NULL,
  multiplier                    numeric NOT NULL,
  occ_symbol                    text,
  exchange_listed               boolean NOT NULL DEFAULT true,
  broad_based_index             boolean NOT NULL DEFAULT false,
  is_mini                       boolean NOT NULL DEFAULT false,
  mini_ratio                    numeric,
  terms_key                     text NOT NULL,
  owner_scope                   text NOT NULL DEFAULT 'platform',
  org_id                        uuid,
  created_by                    uuid REFERENCES public.users(id),
  created_at                    timestamptz NOT NULL DEFAULT now(),
  valid_from                    timestamptz NOT NULL DEFAULT now(),
  valid_to                      timestamptz,
  system_from                   timestamptz NOT NULL DEFAULT now(),
  system_to                     timestamptz,
  CONSTRAINT option_contracts_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT option_contracts_underlying_kind_chk CHECK (underlying_kind IN ('index', 'equity', 'fund')),
  CONSTRAINT option_contracts_type_chk CHECK (option_type IN ('call', 'put')),
  CONSTRAINT option_contracts_style_chk CHECK (exercise_style IN ('european', 'american')),
  CONSTRAINT option_contracts_settlement_chk CHECK (settlement IN ('cash', 'physical')),
  CONSTRAINT option_contracts_strike_chk CHECK (strike > 0),
  CONSTRAINT option_contracts_multiplier_chk CHECK (multiplier > 0),
  CONSTRAINT option_contracts_occ_chk CHECK (occ_symbol IS NULL OR (length(btrim(occ_symbol)) BETWEEN 1 AND 21
                                                                    AND NOT is_flex)),
  CONSTRAINT option_contracts_broad_chk CHECK (NOT broad_based_index OR underlying_kind = 'index'),
  CONSTRAINT option_contracts_cash_index_chk CHECK (settlement = 'physical' OR underlying_kind = 'index'),
  CONSTRAINT option_contracts_mini_chk CHECK ((is_mini AND mini_ratio > 0 AND mini_ratio < 1)
                                              OR (NOT is_mini AND mini_ratio IS NULL)),
  CONSTRAINT option_contracts_terms_key_chk CHECK (length(btrim(terms_key)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS option_contracts_terms_uq ON portfolio.option_contracts (terms_key)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS option_contracts_security_uq ON portfolio.option_contracts (global_security_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS option_contracts_underlying_idx ON portfolio.option_contracts
  (underlying_global_security_id) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.option_contracts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'option contracts are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT EXISTS (SELECT 1 FROM portfolio.securities_global g WHERE g.id = NEW.global_security_id
                   AND g.security_type = 'option') THEN
      RAISE EXCEPTION 'an option contract''s security is of type option' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'an option contract''s terms are its identity: written once, only ever closed'
    USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS option_contracts_guard_trg ON portfolio.option_contracts;
CREATE TRIGGER option_contracts_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.option_contracts
  FOR EACH ROW EXECUTE FUNCTION portfolio.option_contracts_guard();

-- ── 5. Adviser quotes (ORG; insert-only) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.adviser_quotes (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  global_security_id uuid NOT NULL REFERENCES portfolio.securities_global(id),
  kind               text NOT NULL,
  value              numeric NOT NULL,
  source             text NOT NULL,
  as_of              timestamptz NOT NULL,
  entered_by         uuid NOT NULL REFERENCES public.users(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT adviser_quotes_kind_chk CHECK (kind IN ('premium', 'index_level', 'underlying_price', 'implied_vol')),
  CONSTRAINT adviser_quotes_value_chk CHECK (value > 0),
  CONSTRAINT adviser_quotes_source_chk CHECK (length(btrim(source)) > 0),
  CONSTRAINT adviser_quotes_as_of_chk CHECK (as_of <= created_at + interval '5 minutes')
);
CREATE INDEX IF NOT EXISTS adviser_quotes_lookup_idx ON portfolio.adviser_quotes
  (org_id, global_security_id, kind, as_of DESC) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.adviser_quotes_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  st text;
  is_option boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'quotes are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'a quote is never updated in place — enter a new quote' USING ERRCODE = '42501';
  END IF;
  SELECT security_type INTO st FROM portfolio.securities_global WHERE id = NEW.global_security_id;
  is_option := st = 'option' AND EXISTS (SELECT 1 FROM portfolio.option_contracts c
                                         WHERE c.global_security_id = NEW.global_security_id);
  IF NEW.kind IN ('premium', 'implied_vol') AND NOT is_option THEN
    RAISE EXCEPTION 'a % quote is for an option contract', NEW.kind USING ERRCODE = '23514';
  END IF;
  IF NEW.kind = 'index_level' AND st IS DISTINCT FROM 'index' THEN
    RAISE EXCEPTION 'an index level quote is for an index' USING ERRCODE = '23514';
  END IF;
  IF NEW.kind = 'underlying_price' AND (st IS NULL OR st IN ('option', 'index')) THEN
    RAISE EXCEPTION 'an underlying price quote is for a stock or fund' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS adviser_quotes_guard_trg ON portfolio.adviser_quotes;
CREATE TRIGGER adviser_quotes_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.adviser_quotes
  FOR EACH ROW EXECUTE FUNCTION portfolio.adviser_quotes_guard();

-- ── 6. Option packages (ORG; system axis) and their legs (valid axis) ─────
CREATE TABLE IF NOT EXISTS portfolio.option_packages (
  id                             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                         uuid NOT NULL REFERENCES public.organizations(id),
  replica_security_id            uuid NOT NULL REFERENCES portfolio.replica_securities(id),
  model_id                       uuid NOT NULL REFERENCES portfolio.models(id),
  model_version_id               uuid REFERENCES portfolio.model_versions(id),
  terms_version_id               uuid REFERENCES portfolio.replica_terms_versions(id),
  package_no                     integer NOT NULL,
  kind                           text NOT NULL,
  core_source                    text NOT NULL,
  core_model_id                  uuid REFERENCES portfolio.models(id),
  underlying_global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  outcome_start                  date NOT NULL,
  outcome_end                    date NOT NULL,
  start_level                    numeric,
  start_level_quote_id           uuid REFERENCES portfolio.adviser_quotes(id),
  cap_pct                        numeric,
  buffer_pct                     numeric,
  floor_pct                      numeric,
  stated_cap_pct                 numeric,
  stated_buffer_pct              numeric,
  stated_floor_pct               numeric,
  call_rule                      text,
  call_otm_pct                   numeric,
  call_delta                     numeric,
  tenor_days                     integer,
  coverage_pct                   numeric,
  status                         text NOT NULL DEFAULT 'draft',
  rolled_from_id                 uuid REFERENCES portfolio.option_packages(id),
  notes                          text,
  maker_ids                      uuid[] NOT NULL DEFAULT '{}',
  checker_id                     uuid REFERENCES public.users(id),
  approval_task_id               uuid REFERENCES public.workflow_run_steps(id),
  decided_at                     timestamptz,
  decision_note                  text,
  status_reason                  text,
  created_by                     uuid REFERENCES public.users(id),
  archived_from_id               uuid REFERENCES portfolio.option_packages(id),
  created_at                     timestamptz NOT NULL DEFAULT now(),
  valid_from                     timestamptz NOT NULL DEFAULT now(),
  valid_to                       timestamptz,
  system_from                    timestamptz NOT NULL DEFAULT now(),
  system_to                      timestamptz,
  CONSTRAINT option_packages_kind_chk CHECK (kind IN ('buffer', 'income')),
  CONSTRAINT option_packages_core_chk CHECK (core_source IN ('index_replica', 'etf_itself')),
  CONSTRAINT option_packages_status_chk CHECK (status IN ('draft', 'approved', 'rejected', 'rolled', 'expired')),
  CONSTRAINT option_packages_period_chk CHECK (outcome_end > outcome_start),
  CONSTRAINT option_packages_no_chk CHECK (package_no >= 1),
  CONSTRAINT option_packages_level_chk CHECK (start_level IS NULL OR start_level > 0),
  CONSTRAINT option_packages_buffer_terms_chk CHECK (kind <> 'buffer' OR (call_rule IS NULL AND coverage_pct IS NULL)),
  CONSTRAINT option_packages_income_terms_chk CHECK (kind <> 'income' OR (
      call_rule IN ('pct_otm', 'delta') AND tenor_days > 0 AND coverage_pct > 0 AND coverage_pct <= 1
      AND (call_rule <> 'pct_otm' OR call_otm_pct >= 0) AND (call_rule <> 'delta' OR (call_delta > 0 AND call_delta < 1))
      AND cap_pct IS NULL AND buffer_pct IS NULL)),
  CONSTRAINT option_packages_maker_checker_chk CHECK (checker_id IS NULL OR NOT (checker_id = ANY (maker_ids))),
  CONSTRAINT option_packages_decided_chk CHECK (status IN ('draft') OR system_to IS NOT NULL
                                                OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT option_packages_approved_priced_chk CHECK (status <> 'approved' OR system_to IS NOT NULL
                                                        OR start_level IS NOT NULL),
  CONSTRAINT option_packages_rejected_chk CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT option_packages_reason_chk CHECK (status NOT IN ('rolled', 'expired')
                                               OR length(btrim(COALESCE(status_reason, ''))) > 0),
  CONSTRAINT option_packages_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS option_packages_live_approved_uq ON portfolio.option_packages (model_id)
  WHERE status = 'approved' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS option_packages_open_draft_uq ON portfolio.option_packages (model_id)
  WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS option_packages_no_uq ON portfolio.option_packages (model_id, package_no)
  WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS option_packages_roll_uq ON portfolio.option_packages (rolled_from_id)
  WHERE rolled_from_id IS NOT NULL AND system_to IS NULL AND status IN ('draft', 'approved');

CREATE OR REPLACE FUNCTION portfolio.option_packages_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'checker_id', 'approval_task_id', 'decided_at', 'decision_note',
                            'status_reason', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'option packages are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived option package state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live package row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' AND (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'an option package''s terms are frozen once decided (status %)', OLD.status
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY['draft>approved', 'draft>rejected', 'approved>rolled',
                                                         'approved>expired'])) THEN
    RAISE EXCEPTION 'illegal option package transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS option_packages_guard_trg ON portfolio.option_packages;
CREATE TRIGGER option_packages_guard_trg BEFORE UPDATE OR DELETE ON portfolio.option_packages
  FOR EACH ROW EXECUTE FUNCTION portfolio.option_packages_guard();

CREATE TABLE IF NOT EXISTS portfolio.option_package_legs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  package_id          uuid NOT NULL REFERENCES portfolio.option_packages(id),
  leg_no              integer NOT NULL,
  role                text NOT NULL,
  option_type         text,
  direction           text NOT NULL,
  ratio               numeric NOT NULL,
  strike_pct          numeric,
  strike_level        numeric,
  expiry              date,
  contract_id         uuid REFERENCES portfolio.option_contracts(id),
  exercise_style      text,
  settlement          text,
  is_flex             boolean,
  multiplier          numeric,
  created_by          uuid REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT option_package_legs_role_chk CHECK (role IN ('underlying', 'long_put_atm', 'short_put_buffer_end',
                                                          'short_call_cap', 'short_call_income', 'long_put_tail')),
  CONSTRAINT option_package_legs_type_chk CHECK (option_type IS NULL OR option_type IN ('call', 'put')),
  CONSTRAINT option_package_legs_direction_chk CHECK (direction IN ('long', 'short')),
  CONSTRAINT option_package_legs_shape_chk CHECK (
       (role = 'underlying' AND option_type IS NULL AND direction = 'long' AND strike_pct IS NULL
        AND strike_level IS NULL AND contract_id IS NULL)
    OR (role IN ('long_put_atm', 'long_put_tail') AND option_type = 'put' AND direction = 'long')
    OR (role = 'short_put_buffer_end' AND option_type = 'put' AND direction = 'short')
    OR (role IN ('short_call_cap', 'short_call_income') AND option_type = 'call' AND direction = 'short')),
  CONSTRAINT option_package_legs_option_chk CHECK (role = 'underlying' OR (strike_pct > 0 AND expiry IS NOT NULL
      AND exercise_style IN ('european', 'american') AND settlement IN ('cash', 'physical') AND is_flex IS NOT NULL
      AND multiplier > 0)),
  CONSTRAINT option_package_legs_ratio_chk CHECK (ratio > 0),
  CONSTRAINT option_package_legs_level_chk CHECK (strike_level IS NULL OR strike_level > 0),
  CONSTRAINT option_package_legs_contract_chk CHECK (contract_id IS NULL OR strike_level IS NOT NULL),
  CONSTRAINT option_package_legs_no_chk CHECK (leg_no >= 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS option_package_legs_live_uq ON portfolio.option_package_legs (package_id, leg_no)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.option_package_legs_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'package legs are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT EXISTS (SELECT 1 FROM portfolio.option_packages p WHERE p.id = NEW.package_id AND p.org_id = NEW.org_id
                   AND p.system_to IS NULL AND p.status = 'draft') THEN
      RAISE EXCEPTION 'legs are written only on a live DRAFT package of the same org' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a package leg is written once and only ever closed' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS option_package_legs_guard_trg ON portfolio.option_package_legs;
CREATE TRIGGER option_package_legs_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.option_package_legs
  FOR EACH ROW EXECUTE FUNCTION portfolio.option_package_legs_guard();

-- ── 7. rep5 additions: options proposals and option trade lines ───────────
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_kind_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_kind_chk CHECK (
     (proposal_kind = 'model' AND link_id IS NOT NULL AND model_id IS NOT NULL AND model_version_id IS NOT NULL)
  OR (proposal_kind = 'one_off' AND trigger = 'manual' AND maker_kind = 'user' AND link_id IS NULL
      AND model_id IS NULL AND model_version_id IS NULL)
  OR (proposal_kind = 'options' AND trigger = 'manual' AND maker_kind = 'user' AND model_id IS NOT NULL
      AND (trigger_ref ? 'option_package_id')));

ALTER TABLE public.rebalance_proposal_trades ADD COLUMN IF NOT EXISTS option_leg jsonb;
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_option_leg_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_option_leg_chk CHECK (
  option_leg IS NULL OR (jsonb_typeof(option_leg) = 'object' AND option_leg ? 'role'
                         AND (option_leg ->> 'position_effect') IN ('open', 'close') AND quantity IS NOT NULL));
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_price_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_price_chk CHECK (
     (price_source IN ('account_position', 'security_price') AND price > 0 AND price_as_of IS NOT NULL)
  OR (price_source = 'none' AND price IS NULL AND notional IS NOT NULL)
  OR (price_source = 'adviser_quote' AND price > 0 AND price_as_of IS NOT NULL AND option_leg IS NOT NULL)
  OR (price_source = 'none' AND price IS NULL AND option_leg IS NOT NULL));

-- ── 8. rep2 addition: an instruction can carry an option leg — manual only ─
ALTER TABLE public.execution_instructions ADD COLUMN IF NOT EXISTS option_terms jsonb;
ALTER TABLE public.execution_instructions DROP CONSTRAINT IF EXISTS execution_instructions_option_terms_chk;
ALTER TABLE public.execution_instructions ADD CONSTRAINT execution_instructions_option_terms_chk CHECK (
  option_terms IS NULL OR (jsonb_typeof(option_terms) = 'object' AND routed_via = 'manual' AND quantity IS NOT NULL
                           AND (option_terms ->> 'position_effect') IN ('open', 'close')
                           AND executor_type IN ('custodian_desk', 'broker', 'trader')));

-- ── 9. Tax-gate results (ORG; valid axis) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.options_tax_gate_results (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  proposal_id      uuid NOT NULL REFERENCES public.rebalance_proposals(id),
  run_id           uuid NOT NULL,
  trade_id         uuid REFERENCES public.rebalance_proposal_trades(id),
  leg_role         text,
  rule             text NOT NULL,
  outcome          text NOT NULL,
  reason           text NOT NULL,
  pending_counsel  boolean NOT NULL DEFAULT false,
  inputs           jsonb NOT NULL DEFAULT '{}'::jsonb,
  evaluated_by     uuid REFERENCES public.users(id),
  evaluated_at     timestamptz NOT NULL DEFAULT now(),
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT options_tax_gate_results_rule_chk CHECK (rule IN ('section_1256', 'wash_sale', 'cash_secured_put',
                                                               'straddle', 'constructive_sale',
                                                               'qualified_covered_call')),
  CONSTRAINT options_tax_gate_results_outcome_chk CHECK (outcome IN ('pass', 'block', 'flag')),
  CONSTRAINT options_tax_gate_results_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT options_tax_gate_results_inputs_chk CHECK (jsonb_typeof(inputs) = 'object'),
  CONSTRAINT options_tax_gate_results_role_chk CHECK (leg_role IS NULL OR leg_role IN ('underlying', 'long_put_atm',
      'short_put_buffer_end', 'short_call_cap', 'short_call_income', 'long_put_tail', 'core_sale'))
);
CREATE INDEX IF NOT EXISTS options_tax_gate_results_proposal_idx ON public.options_tax_gate_results (proposal_id)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.options_tax_gate_results_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'tax-gate results are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT EXISTS (SELECT 1 FROM public.rebalance_proposals p WHERE p.id = NEW.proposal_id AND p.org_id = NEW.org_id
                   AND p.system_to IS NULL AND p.proposal_kind = 'options') THEN
      RAISE EXCEPTION 'a tax-gate result belongs to a live options proposal of the same org' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a tax-gate result is written once and only ever closed by a newer run' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS options_tax_gate_results_guard_trg ON public.options_tax_gate_results;
CREATE TRIGGER options_tax_gate_results_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.options_tax_gate_results
  FOR EACH ROW EXECUTE FUNCTION public.options_tax_gate_results_guard();

-- ── 10. Expiry settlement entries (ORG) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS public.option_settlement_entries (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  package_id               uuid NOT NULL REFERENCES portfolio.option_packages(id),
  leg_id                   uuid NOT NULL REFERENCES portfolio.option_package_legs(id),
  contract_id              uuid REFERENCES portfolio.option_contracts(id),
  account_id               uuid REFERENCES public.accounts(id),
  expiry                   date NOT NULL,
  settlement_level         numeric,
  settlement_quote_id      uuid REFERENCES portfolio.adviser_quotes(id),
  intrinsic_per_contract   numeric,
  contracts                numeric,
  amount                   numeric,
  status                   text NOT NULL DEFAULT 'pending',
  confirm_task_id          uuid REFERENCES public.workflow_run_steps(id),
  confirmed_by             uuid REFERENCES public.users(id),
  confirmed_at             timestamptz,
  note                     text,
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT option_settlement_entries_status_chk CHECK (status IN ('pending', 'confirmed')),
  CONSTRAINT option_settlement_entries_confirmed_chk CHECK (status <> 'confirmed'
                                                            OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
  CONSTRAINT option_settlement_entries_priced_chk CHECK ((settlement_level IS NULL) = (intrinsic_per_contract IS NULL)),
  CONSTRAINT option_settlement_entries_amount_chk CHECK (amount IS NULL OR (intrinsic_per_contract IS NOT NULL
                                                                            AND contracts IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS option_settlement_entries_live_uq ON public.option_settlement_entries
  (leg_id, COALESCE(account_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.option_settlement_entries_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'settlement entries are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'pending' AND (
       (NEW.status = 'confirmed' AND (to_jsonb(NEW) - ARRAY['status', 'confirmed_by', 'confirmed_at', 'note',
                                                            'system_from'])
                                     = (to_jsonb(OLD) - ARRAY['status', 'confirmed_by', 'confirmed_at', 'note',
                                                              'system_from']))
    OR (NEW.status = 'pending' AND (to_jsonb(NEW) - ARRAY['confirm_task_id'])
                                   = (to_jsonb(OLD) - ARRAY['confirm_task_id']))) THEN
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a settlement entry is only confirmed (or closed), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS option_settlement_entries_guard_trg ON public.option_settlement_entries;
CREATE TRIGGER option_settlement_entries_guard_trg BEFORE UPDATE OR DELETE ON public.option_settlement_entries
  FOR EACH ROW EXECUTE FUNCTION public.option_settlement_entries_guard();

-- ── 11. RLS — a policy for every operation each table takes ──────────────
-- PLATFORM: option_contracts — read by all; written under super admin (the app writes it as a platform job).
ALTER TABLE portfolio.option_contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS option_contracts_global_read ON portfolio.option_contracts;
CREATE POLICY option_contracts_global_read ON portfolio.option_contracts FOR SELECT USING (true);
DROP POLICY IF EXISTS option_contracts_super_admin_insert ON portfolio.option_contracts;
CREATE POLICY option_contracts_super_admin_insert ON portfolio.option_contracts FOR INSERT WITH CHECK
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS option_contracts_super_admin_update ON portfolio.option_contracts;
CREATE POLICY option_contracts_super_admin_update ON portfolio.option_contracts FOR UPDATE USING
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS option_contracts_super_admin_delete ON portfolio.option_contracts;
CREATE POLICY option_contracts_super_admin_delete ON portfolio.option_contracts FOR DELETE USING
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.option_contracts TO app_service;

-- ORG tables: the caller's own org (NULLIF — an unset org GUC matches nothing), or super admin.
DO $$
DECLARE t text; s text; pred text; full_name text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH full_name IN ARRAY ARRAY['portfolio.adviser_quotes', 'portfolio.option_packages',
                                   'portfolio.option_package_legs', 'public.options_tax_gate_results',
                                   'public.option_settlement_entries'] LOOP
    s := split_part(full_name, '.', 1);
    t := split_part(full_name, '.', 2);
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', s, t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_select', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR SELECT USING %s', t || '_org_select', s, t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_insert', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR INSERT WITH CHECK %s', t || '_org_insert', s, t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_update', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', s, t, pred,
                   pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_delete', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR DELETE USING %s', t || '_org_delete', s, t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO app_service', s, t);
  END LOOP;
END $$;
