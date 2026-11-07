-- REP13 — Replication Studio phase 4: the tax-aware LONG/SHORT extension (130/30, 150/50).
--
-- Applied by apps/api/scripts/_apply_rep13.py --schema (one transaction, the operator DSN) and read back by
-- --verify. Idempotent. THE ONLY ROWS IT WRITES are the platform capability records of the three new capability
-- keys for the two custodians already on record ('altruist', 'manual'), each 'unknown' (= NOT declared — the one
-- rule of services.custodian_capabilities.available). No other existing row changes.
--
--   1. custodian_capabilities — new keys stock_borrow, margin_initial_pct, margin_maintenance_pct (margin and
--      short_selling were already keys since rep2, every stored row 'unknown'). The two *_pct keys hold the
--      custodian's DECLARED requirement as a fraction ('0.5000') or 'unknown'.
--   2. public.suitability_records — the member's suitability profile record (record_kind 'margin_agreement':
--      date + document link), maker-checker, system-axis archival (the id is stable: a human task names it).
--   3. portfolio.models — kind long_only | long_short, gross long / gross short (net 100 %), the signal source
--      (sub-adviser) and the as-of of its weights, names never shorted by the model. The "Holdings of" prefix
--      (rep3 reserved it for fund replicas) is REQUIRED of a long/short model (Joe: every model "Holdings of …").
--   4. portfolio.model_version_positions — a weight may be negative (−100 % … 100 %); a trigger admits a negative
--      weight only on a long/short model's version; a short row carries no drift band.
--   5. public.long_short_settings — per account: extension level, tracking-error limit, maximum short per name,
--      borrow-cost ceiling, exclusions. Draft → saved; rows written once and only ever closed (history).
--   6. public.borrow_rates — the entered borrow-rate table (no live borrow feed exists): platform rows (Super
--      Admin) and org rows, per security with an as-of; optional short rebate.
--   7. public.long_short_proposals — one per long/short proposal: eligibility at build, both books, borrow and
--      the cost comparison, tracking error, the §1259 blocks, harvest, disclosures, margin, the member summary,
--      blocking codes and the adviser's acknowledgement.
--   8. Side vocabulary — 'sell_short' and 'buy_to_cover' on proposal trades (with short_leg), execution
--      instructions (with short_terms) and trade blocks.
--
-- RLS: org isolation with NULLIF on every operation of every new org table; platform rows read by all and
-- written only under app.is_super_admin. Deletes only on the fixture-teardown path (execution.teardown).

-- ── 1. Capabilities ─────────────────────────────────────────────────────────
ALTER TABLE public.custodian_capabilities DROP CONSTRAINT IF EXISTS custodian_capabilities_key_chk;
ALTER TABLE public.custodian_capabilities ADD CONSTRAINT custodian_capabilities_key_chk CHECK (capability_key IN (
  'fractional_shares', 'equity_trading_api', 'model_api', 'trade_list_upload', 'options_level', 'flex_options',
  'index_options', 'margin', 'short_selling', 'in_kind_transfers', 'custodian_tax_loss_harvesting',
  'lot_level_cost_basis_feed', 'held_away_aggregation', 'stock_borrow', 'margin_initial_pct',
  'margin_maintenance_pct'));
ALTER TABLE public.custodian_capabilities DROP CONSTRAINT IF EXISTS custodian_capabilities_value_chk;
ALTER TABLE public.custodian_capabilities ADD CONSTRAINT custodian_capabilities_value_chk CHECK (
  (capability_key = 'options_level' AND value IN ('0', '1', '2', '3', '4', 'unknown'))
  OR (capability_key IN ('margin_initial_pct', 'margin_maintenance_pct')
      AND (value = 'unknown' OR value ~ '^(0\.[0-9]{1,4}|1\.0{1,4}|1)$'))
  OR (capability_key NOT IN ('options_level', 'margin_initial_pct', 'margin_maintenance_pct')
      AND value IN ('yes', 'no', 'unknown')));

INSERT INTO public.custodian_capabilities (custodian_code, capability_key, value, evidence_kind, evidence, as_of,
                                           version_no, note)
SELECT c.code, k.key, 'unknown', 'assumed',
       CASE WHEN c.code = 'manual' THEN 'depends on the account and the executor'
            ELSE 'assumed — nothing on record (Altruist has not said it supports short selling or stock borrow)' END,
       DATE '2026-11-07', 1, 'rep13 — new capability key, not declared'
FROM (VALUES ('altruist'), ('manual')) AS c(code)
CROSS JOIN (VALUES ('stock_borrow'), ('margin_initial_pct'), ('margin_maintenance_pct')) AS k(key)
WHERE NOT EXISTS (SELECT 1 FROM public.custodian_capabilities x WHERE x.custodian_code = c.code
                    AND x.capability_key = k.key AND x.system_to IS NULL);

-- ── 2. Suitability records (the margin agreement) ──────────────────────────
CREATE TABLE IF NOT EXISTS public.suitability_records (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  subject_type       text NOT NULL,
  subject_id         uuid NOT NULL,
  record_kind        text NOT NULL,
  agreement_date     date NOT NULL,
  document_id        uuid NOT NULL REFERENCES public.documents(id),
  note               text,
  status             text NOT NULL,
  maker_id           uuid NOT NULL REFERENCES public.users(id),
  approval_task_id   uuid,
  approved_by        uuid REFERENCES public.users(id),
  approved_at        timestamptz,
  decision_note      text,
  removed_by         uuid REFERENCES public.users(id),
  removed_at         timestamptz,
  removed_reason     text,
  archived_from_id   uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT suitability_records_subject_chk CHECK (subject_type IN ('household', 'entity')),
  CONSTRAINT suitability_records_kind_chk CHECK (record_kind IN ('margin_agreement')),
  CONSTRAINT suitability_records_status_chk CHECK (status IN ('pending_approval', 'approved', 'rejected', 'removed')),
  CONSTRAINT suitability_records_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT suitability_records_approved_chk CHECK (status NOT IN ('approved', 'removed')
                                                     OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT suitability_records_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT suitability_records_rejected_chk CHECK (status <> 'rejected'
                                                     OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT suitability_records_removed_chk CHECK (status <> 'removed' OR (removed_by IS NOT NULL
                                                    AND removed_at IS NOT NULL
                                                    AND length(btrim(COALESCE(removed_reason, ''))) > 0)),
  CONSTRAINT suitability_records_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS suitability_records_live_uq
  ON public.suitability_records (org_id, subject_type, subject_id, record_kind)
  WHERE valid_to IS NULL AND system_to IS NULL AND status IN ('pending_approval', 'approved');

CREATE OR REPLACE FUNCTION public.suitability_records_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'suitability records are never deleted — a removal is recorded' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived suitability record never changes' USING ERRCODE = '42501';
  END IF;
  -- what was recorded (the subject, the date, the document) never changes; only the decision / removal / closing
  IF (to_jsonb(NEW) - ARRAY['status', 'approval_task_id', 'approved_by', 'approved_at', 'decision_note',
                            'removed_by', 'removed_at', 'removed_reason', 'valid_to', 'system_to'])
     <> (to_jsonb(OLD) - ARRAY['status', 'approval_task_id', 'approved_by', 'approved_at', 'decision_note',
                               'removed_by', 'removed_at', 'removed_reason', 'valid_to', 'system_to']) THEN
    RAISE EXCEPTION 'a suitability record''s subject, date and document never change — record a new one'
      USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('rejected', 'removed') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'a % suitability record is final', OLD.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS suitability_records_guard_trg ON public.suitability_records;
CREATE TRIGGER suitability_records_guard_trg BEFORE UPDATE OR DELETE ON public.suitability_records
  FOR EACH ROW EXECUTE FUNCTION public.suitability_records_guard();

-- ── 3. Models: kind long_short ─────────────────────────────────────────────
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'long_only';
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS gross_long numeric;
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS gross_short numeric;
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS signal_source text;
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS signal_as_of date;
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS short_exclusions jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_kind_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_kind_chk CHECK (kind IN ('long_only', 'long_short'));
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_long_short_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_long_short_chk CHECK (
  (kind = 'long_only' AND gross_long IS NULL AND gross_short IS NULL AND signal_source IS NULL
   AND signal_as_of IS NULL AND short_exclusions = '[]'::jsonb)
  OR (kind = 'long_short' AND source = 'upload' AND gross_short > 0 AND gross_short <= 0.5
      AND gross_long = 1 + gross_short AND length(btrim(COALESCE(signal_source, ''))) > 0
      AND signal_as_of IS NOT NULL AND jsonb_typeof(short_exclusions) = 'array'));
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_reserved_name_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_reserved_name_chk CHECK (
  source = 'fund_replica' OR kind = 'long_short' OR lower(btrim(name)) !~~ 'holdings of%');
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_long_short_name_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_long_short_name_chk CHECK (
  kind <> 'long_short' OR lower(btrim(name)) ~~ 'holdings of %');

-- ── 4. Model positions: negative weights on a long/short model only ────────
ALTER TABLE portfolio.model_version_positions DROP CONSTRAINT IF EXISTS model_version_positions_weight_chk;
ALTER TABLE portfolio.model_version_positions ADD CONSTRAINT model_version_positions_weight_chk CHECK (
  target_weight >= -1 AND target_weight <= 1);
ALTER TABLE portfolio.model_version_positions DROP CONSTRAINT IF EXISTS model_version_positions_band_chk;
ALTER TABLE portfolio.model_version_positions ADD CONSTRAINT model_version_positions_band_chk CHECK (
  (target_weight < 0 AND drift_min IS NULL AND drift_max IS NULL)
  OR (target_weight >= 0
      AND (drift_min IS NULL OR (drift_min >= 0 AND drift_min <= target_weight))
      AND (drift_max IS NULL OR (drift_max <= 1 AND drift_max >= target_weight))));

CREATE OR REPLACE FUNCTION portfolio.model_version_positions_short_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE k text;
BEGIN
  IF NEW.target_weight < 0 THEN
    SELECT m.kind INTO k FROM portfolio.model_versions v JOIN portfolio.models m ON m.id = v.model_id
      WHERE v.id = NEW.version_id;
    IF k IS DISTINCT FROM 'long_short' THEN
      RAISE EXCEPTION 'a negative target weight belongs only to a long/short model (row %)', NEW.row_num
        USING ERRCODE = '23514';
    END IF;
    IF NEW.resolution_state <> 'resolved' THEN
      RAISE EXCEPTION 'a short row (row %) must be a resolved security', NEW.row_num USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS model_version_positions_short_guard_trg ON portfolio.model_version_positions;
CREATE TRIGGER model_version_positions_short_guard_trg BEFORE INSERT OR UPDATE ON portfolio.model_version_positions
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_version_positions_short_guard();

-- ── 5. Account long/short settings ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.long_short_settings (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  account_id               uuid NOT NULL REFERENCES public.accounts(id),
  status                   text NOT NULL,
  version_no               integer NOT NULL,
  extension                text NOT NULL,
  gross_long               numeric NOT NULL,
  gross_short              numeric NOT NULL,
  tracking_error_limit     numeric NOT NULL,
  max_short_per_name       numeric NOT NULL,
  borrow_cost_ceiling_bps  numeric NOT NULL,
  exclusions               jsonb NOT NULL DEFAULT '[]'::jsonb,
  note                     text,
  created_by               uuid NOT NULL REFERENCES public.users(id),
  saved_by                 uuid REFERENCES public.users(id),
  saved_at                 timestamptz,
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT long_short_settings_status_chk CHECK (status IN ('draft', 'saved')),
  CONSTRAINT long_short_settings_saved_chk CHECK (status <> 'saved' OR (saved_by IS NOT NULL AND saved_at IS NOT NULL)),
  CONSTRAINT long_short_settings_version_chk CHECK (version_no >= 1),
  CONSTRAINT long_short_settings_extension_chk CHECK (
    (extension = '130_30' AND gross_long = 1.30 AND gross_short = 0.30)
    OR (extension = '150_50' AND gross_long = 1.50 AND gross_short = 0.50)
    OR (extension = 'custom' AND gross_short > 0 AND gross_short <= 0.5 AND gross_long = 1 + gross_short)),
  CONSTRAINT long_short_settings_te_chk CHECK (tracking_error_limit > 0 AND tracking_error_limit <= 1),
  CONSTRAINT long_short_settings_max_short_chk CHECK (max_short_per_name > 0 AND max_short_per_name <= 0.5),
  CONSTRAINT long_short_settings_ceiling_chk CHECK (borrow_cost_ceiling_bps >= 0 AND borrow_cost_ceiling_bps <= 10000),
  CONSTRAINT long_short_settings_exclusions_chk CHECK (jsonb_typeof(exclusions) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS long_short_settings_live_uq ON public.long_short_settings (account_id, status)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS long_short_settings_version_uq
  ON public.long_short_settings (account_id, version_no) WHERE status = 'saved' AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.long_short_once_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted — a change closes the live row', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% rows are written once and only ever closed (valid_to)', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS long_short_settings_guard_trg ON public.long_short_settings;
CREATE TRIGGER long_short_settings_guard_trg BEFORE UPDATE OR DELETE ON public.long_short_settings
  FOR EACH ROW EXECUTE FUNCTION public.long_short_once_guard();

-- ── 6. Borrow rates (entered; no live borrow feed exists) ──────────────────
CREATE TABLE IF NOT EXISTS public.borrow_rates (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_scope         text NOT NULL,
  org_id              uuid REFERENCES public.organizations(id),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  rate_bps            numeric NOT NULL,
  rebate_bps          numeric,
  as_of               date NOT NULL,
  source              text NOT NULL,
  entered_by          uuid NOT NULL REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT borrow_rates_scope_chk CHECK ((owner_scope = 'platform' AND org_id IS NULL)
                                           OR (owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT borrow_rates_rate_chk CHECK (rate_bps >= 0 AND rate_bps <= 100000),
  CONSTRAINT borrow_rates_rebate_chk CHECK (rebate_bps IS NULL OR (rebate_bps >= 0 AND rebate_bps <= 10000)),
  CONSTRAINT borrow_rates_source_chk CHECK (length(btrim(source)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS borrow_rates_live_uq
  ON public.borrow_rates (owner_scope, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid),
                          global_security_id, as_of)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS borrow_rates_security_idx ON public.borrow_rates (global_security_id, as_of DESC)
  WHERE valid_to IS NULL AND system_to IS NULL;
DROP TRIGGER IF EXISTS borrow_rates_guard_trg ON public.borrow_rates;
CREATE TRIGGER borrow_rates_guard_trg BEFORE UPDATE OR DELETE ON public.borrow_rates
  FOR EACH ROW EXECUTE FUNCTION public.long_short_once_guard();

-- ── 7. Long/short proposal details ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.long_short_proposals (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  proposal_id         uuid NOT NULL REFERENCES public.rebalance_proposals(id),
  account_id          uuid NOT NULL REFERENCES public.accounts(id),
  model_id            uuid NOT NULL,
  model_version_id    uuid NOT NULL,
  settings_id         uuid NOT NULL REFERENCES public.long_short_settings(id),
  extension           text NOT NULL,
  gross_long          numeric NOT NULL,
  gross_short         numeric NOT NULL,
  capital             numeric NOT NULL,
  long_value_after    numeric NOT NULL,
  short_value_after   numeric NOT NULL,
  eligibility         jsonb NOT NULL,
  borrow              jsonb NOT NULL,
  cost_comparison     jsonb NOT NULL,
  tracking_error      jsonb NOT NULL,
  constructive_sale   jsonb NOT NULL DEFAULT '[]'::jsonb,
  harvest             jsonb NOT NULL DEFAULT '[]'::jsonb,
  skipped             jsonb NOT NULL DEFAULT '[]'::jsonb,
  disclosures         jsonb NOT NULL,
  margin              jsonb NOT NULL,
  tax_estimate        jsonb NOT NULL,
  member_summary      text NOT NULL,
  blocking            jsonb NOT NULL DEFAULT '[]'::jsonb,
  acknowledged_by     uuid REFERENCES public.users(id),
  acknowledged_at     timestamptz,
  acknowledgement     jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT long_short_proposals_json_chk CHECK (
    jsonb_typeof(eligibility) = 'object' AND jsonb_typeof(borrow) = 'object'
    AND jsonb_typeof(cost_comparison) = 'array' AND jsonb_typeof(tracking_error) = 'object'
    AND jsonb_typeof(constructive_sale) = 'array' AND jsonb_typeof(harvest) = 'array'
    AND jsonb_typeof(skipped) = 'array' AND jsonb_typeof(disclosures) = 'object'
    AND jsonb_typeof(margin) = 'object' AND jsonb_typeof(tax_estimate) = 'object'
    AND jsonb_typeof(blocking) = 'array'),
  CONSTRAINT long_short_proposals_ack_chk CHECK ((acknowledged_by IS NULL) = (acknowledged_at IS NULL)
                                                 AND (acknowledged_by IS NULL) = (acknowledgement IS NULL)),
  CONSTRAINT long_short_proposals_summary_chk CHECK (length(btrim(member_summary)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS long_short_proposals_live_uq ON public.long_short_proposals (proposal_id)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.long_short_proposals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'long/short proposal details are never deleted' USING ERRCODE = '42501';
  END IF;
  -- the content is fixed at generation; the acknowledgement is recorded ONCE; a row is otherwise only closed
  IF OLD.acknowledged_by IS NULL AND NEW.acknowledged_by IS NOT NULL AND OLD.valid_to IS NULL
     AND NEW.valid_to IS NULL
     AND (to_jsonb(NEW) - ARRAY['acknowledged_by', 'acknowledged_at', 'acknowledgement'])
         = (to_jsonb(OLD) - ARRAY['acknowledged_by', 'acknowledged_at', 'acknowledgement']) THEN
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'long/short proposal details are fixed at generation (the acknowledgement is recorded once)'
    USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS long_short_proposals_guard_trg ON public.long_short_proposals;
CREATE TRIGGER long_short_proposals_guard_trg BEFORE UPDATE OR DELETE ON public.long_short_proposals
  FOR EACH ROW EXECUTE FUNCTION public.long_short_proposals_guard();

-- ── 8. Side vocabulary: sell_short / buy_to_cover ──────────────────────────
ALTER TABLE public.rebalance_proposal_trades ADD COLUMN IF NOT EXISTS short_leg jsonb;
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_side_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_side_chk CHECK (
  side IN ('buy', 'sell', 'sell_short', 'buy_to_cover'));
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_short_leg_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_short_leg_chk CHECK (
  ((side IN ('sell_short', 'buy_to_cover')) = (short_leg IS NOT NULL))
  AND (short_leg IS NULL OR (jsonb_typeof(short_leg) = 'object'
       AND (short_leg ->> 'position_effect') IN ('open', 'close') AND quantity IS NOT NULL)));

ALTER TABLE public.execution_instructions ADD COLUMN IF NOT EXISTS short_terms jsonb;
ALTER TABLE public.execution_instructions DROP CONSTRAINT IF EXISTS execution_instructions_side_chk;
ALTER TABLE public.execution_instructions ADD CONSTRAINT execution_instructions_side_chk CHECK (
  side IN ('buy', 'sell', 'sell_short', 'buy_to_cover'));
ALTER TABLE public.execution_instructions DROP CONSTRAINT IF EXISTS execution_instructions_short_terms_chk;
ALTER TABLE public.execution_instructions ADD CONSTRAINT execution_instructions_short_terms_chk CHECK (
  ((side IN ('sell_short', 'buy_to_cover')) = (short_terms IS NOT NULL))
  AND (short_terms IS NULL OR (jsonb_typeof(short_terms) = 'object'
       AND (short_terms ->> 'position_effect') IN ('open', 'close') AND quantity IS NOT NULL)));

ALTER TABLE public.trade_blocks DROP CONSTRAINT IF EXISTS trade_blocks_side_chk;
ALTER TABLE public.trade_blocks ADD CONSTRAINT trade_blocks_side_chk CHECK (
  side IN ('buy', 'sell', 'sell_short', 'buy_to_cover'));

-- ── 9. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['suitability_records', 'long_short_settings', 'long_short_proposals'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t, pred,
                   pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;

-- borrow_rates: platform rows read by every org, written only by Super Admin; org rows org-isolated.
ALTER TABLE public.borrow_rates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS borrow_rates_select ON public.borrow_rates;
CREATE POLICY borrow_rates_select ON public.borrow_rates FOR SELECT USING (
  owner_scope = 'platform'
  OR org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS borrow_rates_insert ON public.borrow_rates;
CREATE POLICY borrow_rates_insert ON public.borrow_rates FOR INSERT WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid));
DROP POLICY IF EXISTS borrow_rates_update ON public.borrow_rates;
CREATE POLICY borrow_rates_update ON public.borrow_rates FOR UPDATE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid))
  WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid));
DROP POLICY IF EXISTS borrow_rates_delete ON public.borrow_rates;
CREATE POLICY borrow_rates_delete ON public.borrow_rates FOR DELETE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid));
GRANT SELECT, INSERT, UPDATE, DELETE ON public.borrow_rates TO app_service;
