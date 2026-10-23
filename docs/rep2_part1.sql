-- REP2 — Replication Studio, sprint 2: custodian capabilities, trade adapters,
-- governed human tasks, execution instructions.
--
-- Applied by apps/api/scripts/_apply_rep2.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- PLATFORM vs ORG
--   custodian_capabilities is PLATFORM-scoped (owner_scope = 'platform',
--   org_id NULL): read by everyone, written only under app.is_super_admin.
--   execution_instructions, execution_instruction_lots, execution_fills and
--   execution_instruction_events are ORG-scoped (NULLIF org isolation on
--   every operation).
--
-- BI-TEMPORAL (Rule 3)
--   custodian_capabilities: VALID axis — an edit closes the live row
--     (valid_to = now()) and inserts version_no + 1. Nothing references a
--     capability row's id, so a new id per version is safe.
--   execution_instructions: SYSTEM axis — lots, fills, events and the human
--     task all point at the instruction's id, so the outgoing state is copied
--     to a fresh id (archived_from_id = live id, system_to = now()) and the
--     live row keeps its id (the portfolio.assets / rep1 precedent).
--   execution_instruction_lots: VALID axis (a draft edit closes and re-adds).
--   execution_fills / execution_instruction_events: append-only, immutable.
--
-- THE CUSTODY CLIFF. Nothing in this schema places a trade or moves money.
-- An execution instruction is a record of what a PERSON is asked to do and
-- what a person reports they did.
--
-- Engine change (additive): workflow_steps.assigned_permission. NULL on every
-- existing step, and NULL means exactly the old behaviour (todos by assigned
-- role profile; any completer). Only rep2's system human-task definitions set it.

-- ── 0. Workflow engine: a permission-based assignee ────────────────────────
ALTER TABLE public.workflow_steps ADD COLUMN IF NOT EXISTS assigned_permission text;

-- ── 1. Shared guard: the only path past the immutability triggers ─────────
CREATE OR REPLACE FUNCTION public.execution_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('execution.teardown', true), '') = 'fixture'
     AND current_user <> 'app_service'
$$;

-- ── 2. Custodian capabilities (PLATFORM, versioned) ────────────────────────
CREATE TABLE IF NOT EXISTS public.custodian_capabilities (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  custodian_code  text NOT NULL,
  capability_key  text NOT NULL,
  value           text NOT NULL,
  evidence_kind   text NOT NULL,
  evidence        text NOT NULL,
  as_of           date NOT NULL,
  version_no      integer NOT NULL,
  recorded_by     uuid REFERENCES public.users(id),
  note            text,
  owner_scope     text NOT NULL DEFAULT 'platform',
  org_id          uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  valid_from      timestamptz NOT NULL DEFAULT now(),
  valid_to        timestamptz,
  system_from     timestamptz NOT NULL DEFAULT now(),
  system_to       timestamptz,
  CONSTRAINT custodian_capabilities_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT custodian_capabilities_code_chk CHECK (custodian_code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT custodian_capabilities_key_chk CHECK (capability_key IN (
    'fractional_shares', 'equity_trading_api', 'model_api', 'trade_list_upload', 'options_level',
    'flex_options', 'index_options', 'margin', 'short_selling', 'in_kind_transfers',
    'custodian_tax_loss_harvesting', 'lot_level_cost_basis_feed', 'held_away_aggregation')),
  CONSTRAINT custodian_capabilities_value_chk CHECK (
    (capability_key = 'options_level' AND value IN ('0', '1', '2', '3', '4', 'unknown'))
    OR (capability_key <> 'options_level' AND value IN ('yes', 'no', 'unknown'))),
  CONSTRAINT custodian_capabilities_evidence_kind_chk CHECK (evidence_kind IN ('document', 'url', 'assumed')),
  CONSTRAINT custodian_capabilities_evidence_chk CHECK (length(btrim(evidence)) > 0),
  CONSTRAINT custodian_capabilities_url_chk CHECK (evidence_kind <> 'url' OR evidence ~ '^https?://'),
  CONSTRAINT custodian_capabilities_version_chk CHECK (version_no >= 1),
  -- a human records every version after the seed
  CONSTRAINT custodian_capabilities_recorded_by_chk CHECK (version_no = 1 OR recorded_by IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS custodian_capabilities_live_uq
  ON public.custodian_capabilities (custodian_code, capability_key) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS custodian_capabilities_version_uq
  ON public.custodian_capabilities (custodian_code, capability_key, version_no) WHERE system_to IS NULL;

-- The only change a capability row ever takes is being closed (valid_to set,
-- everything else identical). DELETE only on the fixture-teardown path.
CREATE OR REPLACE FUNCTION public.custodian_capabilities_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'custodian capability versions are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'custodian capability versions are immutable: an edit closes this version and records a new one'
    USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS custodian_capabilities_guard_trg ON public.custodian_capabilities;
CREATE TRIGGER custodian_capabilities_guard_trg BEFORE UPDATE OR DELETE ON public.custodian_capabilities
  FOR EACH ROW EXECUTE FUNCTION public.custodian_capabilities_guard();

-- Seed (version 1). Altruist: every capability 'unknown' — rep2 Task 1d found
-- no evidence that establishes any of them for an account (integration sprints
-- 1-6 ran against mocks; the OAuth scope is accounts:read; no trade endpoint
-- exists). Where something IS on record it is cited, and it still does not
-- establish the capability. Manual: every trade is an execution instruction
-- carried out by a person, so every system trading interface is 'no'.
INSERT INTO public.custodian_capabilities
  (custodian_code, capability_key, value, evidence_kind, evidence, as_of, version_no, note)
SELECT v.code, v.key, v.value, v.kind, v.evidence, DATE '2026-10-22', 1, v.note
FROM (VALUES
  ('altruist', 'fractional_shares', 'unknown', 'assumed', 'assumed — no Altruist document or payload on record states it', NULL),
  ('altruist', 'equity_trading_api', 'unknown', 'document',
   'apps/api/services/altruist_oauth.py: build_authorize_url requests scope accounts:read; no trade or order endpoint exists in any Altruist module; no live Altruist call has ever been made',
   'Unknown is treated as NOT available: every Altruist trade becomes an execution instruction.'),
  ('altruist', 'model_api', 'unknown', 'assumed', 'assumed — no Altruist model API document on record', NULL),
  ('altruist', 'trade_list_upload', 'unknown', 'assumed', 'assumed — no Altruist trade-file document on record', NULL),
  ('altruist', 'options_level', 'unknown', 'document',
   'apps/api/services/altruist_oauth.py: the mocked account payload (_normalize_account) carries id and name only — no options approval level', NULL),
  ('altruist', 'flex_options', 'unknown', 'assumed', 'assumed — nothing on record', NULL),
  ('altruist', 'index_options', 'unknown', 'assumed', 'assumed — nothing on record', NULL),
  ('altruist', 'margin', 'unknown', 'url', 'https://www.altruist.com/pricing',
   'The fee37 rate card (cost_schedules ALTRUIST_MARGIN_SPREAD_*, unverified per fee37 F6) shows Altruist sells margin as a product; it says nothing about whether a given account is approved for it.'),
  ('altruist', 'short_selling', 'unknown', 'assumed', 'assumed — nothing on record', NULL),
  ('altruist', 'in_kind_transfers', 'unknown', 'assumed', 'assumed — nothing on record', NULL),
  ('altruist', 'custodian_tax_loss_harvesting', 'unknown', 'url', 'https://www.altruist.com/pricing',
   'fee38 records an Altruist One tax-loss-harvesting benefit as an UNVERIFIED estimate (provider_benefit_schedules); not established for any account.'),
  ('altruist', 'lot_level_cost_basis_feed', 'unknown', 'document',
   'apps/api/services/altruist_oauth.py: _normalize_position reads a position-level cost_basis only (field names unconfirmed); no lot-level field in any mocked payload', NULL),
  ('altruist', 'held_away_aggregation', 'unknown', 'assumed', 'assumed — nothing on record', NULL),
  ('manual', 'fractional_shares', 'unknown', 'assumed', 'depends on the person or firm carrying out the instruction', NULL),
  ('manual', 'equity_trading_api', 'no', 'document', 'docs/REPLICATION_STUDIO.md (rep2): the manual route turns every trade into an execution instruction; nothing is sent by the system', NULL),
  ('manual', 'model_api', 'no', 'document', 'docs/REPLICATION_STUDIO.md (rep2): manual route — no system interface', NULL),
  ('manual', 'trade_list_upload', 'no', 'document', 'docs/REPLICATION_STUDIO.md (rep2): manual route — no system interface', NULL),
  ('manual', 'options_level', 'unknown', 'assumed', 'depends on the account and the executor', NULL),
  ('manual', 'flex_options', 'unknown', 'assumed', 'depends on the account and the executor', NULL),
  ('manual', 'index_options', 'unknown', 'assumed', 'depends on the account and the executor', NULL),
  ('manual', 'margin', 'unknown', 'assumed', 'depends on the account', NULL),
  ('manual', 'short_selling', 'unknown', 'assumed', 'depends on the account and the executor', NULL),
  ('manual', 'in_kind_transfers', 'unknown', 'assumed', 'depends on the custodian holding the account', NULL),
  ('manual', 'custodian_tax_loss_harvesting', 'no', 'document', 'docs/REPLICATION_STUDIO.md (rep2): manual route — no custodian service', NULL),
  ('manual', 'lot_level_cost_basis_feed', 'no', 'document', 'docs/REPLICATION_STUDIO.md (rep2): manual route — no data feed', NULL),
  ('manual', 'held_away_aggregation', 'no', 'document', 'docs/REPLICATION_STUDIO.md (rep2): manual route — no data feed (rep7 adds held-away accounts)', NULL)
) AS v(code, key, value, kind, evidence, note)
WHERE NOT EXISTS (
  SELECT 1 FROM public.custodian_capabilities c
  WHERE c.custodian_code = v.code AND c.capability_key = v.key AND c.system_to IS NULL);

-- ── 3. Execution instructions (ORG, system-axis) ──────────────────────────
CREATE TABLE IF NOT EXISTS public.execution_instructions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  account_id            uuid NOT NULL REFERENCES public.accounts(id),
  global_security_id    uuid NOT NULL REFERENCES portfolio.securities_global(id),
  side                  text NOT NULL,
  quantity              numeric(28, 8),
  notional              numeric(20, 4),
  price_kind            text NOT NULL DEFAULT 'market',
  limit_price           numeric(20, 6),
  range_low             numeric(20, 6),
  range_high            numeric(20, 6),
  reference_price       numeric(20, 6),
  reference_at          timestamptz,
  wash_sale_notes       text,
  executor_type         text NOT NULL,
  executor_name         text,
  executor_contact      text,
  execute_by            date NOT NULL,
  if_not_filled         text NOT NULL,
  source                text NOT NULL DEFAULT 'manual',
  source_ref            uuid,
  routed_via            text NOT NULL,
  routing_reason        text NOT NULL,
  notes                 text,
  status                text NOT NULL DEFAULT 'draft',
  maker_id              uuid NOT NULL REFERENCES public.users(id),
  maker_ids             uuid[] NOT NULL,
  approval_task_id      uuid REFERENCES public.workflow_run_steps(id),
  approved_by           uuid REFERENCES public.users(id),
  approved_at           timestamptz,
  sent_by               uuid REFERENCES public.users(id),
  sent_at               timestamptz,
  sent_channel          text,
  sent_note             text,
  reconciled_by         uuid REFERENCES public.users(id),
  reconciled_at         timestamptz,
  reconcile_method      text,
  reconcile_reference   text,
  reconcile_note        text,
  closed_by             uuid REFERENCES public.users(id),
  closed_at             timestamptz,
  cancelled_by          uuid REFERENCES public.users(id),
  cancelled_at          timestamptz,
  cancel_reason         text,
  expired_at            timestamptz,
  expiry_reason         text,
  archived_from_id      uuid REFERENCES public.execution_instructions(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT execution_instructions_side_chk CHECK (side IN ('buy', 'sell')),
  CONSTRAINT execution_instructions_amount_chk CHECK (
    (quantity IS NOT NULL AND notional IS NULL AND quantity > 0)
    OR (quantity IS NULL AND notional IS NOT NULL AND notional > 0)),
  CONSTRAINT execution_instructions_price_chk CHECK (
    (price_kind = 'market' AND limit_price IS NULL AND range_low IS NULL AND range_high IS NULL)
    OR (price_kind = 'limit' AND limit_price > 0 AND range_low IS NULL AND range_high IS NULL)
    OR (price_kind = 'range' AND limit_price IS NULL AND range_low > 0 AND range_high >= range_low
        AND reference_price > 0 AND reference_at IS NOT NULL)),
  CONSTRAINT execution_instructions_executor_chk CHECK (
    executor_type IN ('custodian_desk', 'broker', 'member', 'other_adviser', 'trader')),
  CONSTRAINT execution_instructions_if_not_filled_chk CHECK (if_not_filled IN ('cancel', 'carry', 'ask')),
  CONSTRAINT execution_instructions_source_chk CHECK (
    (source = 'manual' AND source_ref IS NULL) OR (source = 'proposal' AND source_ref IS NOT NULL)),
  CONSTRAINT execution_instructions_status_chk CHECK (status IN (
    'draft', 'pending_approval', 'approved', 'sent', 'partially_filled', 'filled', 'reconciled', 'closed',
    'cancelled', 'expired')),
  CONSTRAINT execution_instructions_makers_chk CHECK (maker_id = ANY (maker_ids)),
  -- maker-checker in the database: the approver is never one of the makers
  CONSTRAINT execution_instructions_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT execution_instructions_approved_chk CHECK (
    status IN ('draft', 'pending_approval') OR (status IN ('cancelled', 'expired'))
    OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT execution_instructions_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT execution_instructions_sent_chk CHECK (
    status NOT IN ('sent', 'partially_filled', 'filled', 'reconciled', 'closed')
    OR (sent_by IS NOT NULL AND sent_at IS NOT NULL AND length(btrim(COALESCE(sent_channel, ''))) > 0)),
  CONSTRAINT execution_instructions_reconciled_chk CHECK (
    status NOT IN ('reconciled', 'closed')
    OR (reconciled_by IS NOT NULL AND reconciled_at IS NOT NULL AND (
        (reconcile_method = 'custodian_data' AND length(btrim(COALESCE(reconcile_reference, ''))) > 0)
        OR (reconcile_method = 'manual' AND length(btrim(COALESCE(reconcile_note, ''))) > 0)))),
  CONSTRAINT execution_instructions_closed_chk CHECK (status <> 'closed' OR (closed_by IS NOT NULL AND closed_at IS NOT NULL)),
  CONSTRAINT execution_instructions_cancelled_chk CHECK (
    status <> 'cancelled' OR (cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL
                              AND length(btrim(COALESCE(cancel_reason, ''))) > 0)),
  CONSTRAINT execution_instructions_expired_chk CHECK (
    status <> 'expired' OR (expired_at IS NOT NULL AND length(btrim(COALESCE(expiry_reason, ''))) > 0)),
  CONSTRAINT execution_instructions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS execution_instructions_org_idx
  ON public.execution_instructions (org_id, status) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS execution_instructions_account_idx
  ON public.execution_instructions (account_id) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS execution_instructions_task_live_uq
  ON public.execution_instructions (approval_task_id) WHERE system_to IS NULL AND approval_task_id IS NOT NULL;

-- Status graph + "content is frozen once the draft is submitted", in the
-- database (the service enforces it first, with a clearer message).
CREATE OR REPLACE FUNCTION public.execution_instructions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'maker_ids', 'approval_task_id', 'approved_by', 'approved_at', 'sent_by',
    'sent_at', 'sent_channel', 'sent_note', 'reconciled_by', 'reconciled_at', 'reconcile_method',
    'reconcile_reference', 'reconcile_note', 'closed_by', 'closed_at', 'cancelled_by', 'cancelled_at',
    'cancel_reason', 'expired_at', 'expiry_reason', 'execute_by', 'updated_at', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'execution instructions are never deleted (cancel one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived execution instruction state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('closed', 'cancelled', 'expired') THEN
    RAISE EXCEPTION 'a % execution instruction is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live instruction row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' AND (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'execution instruction terms are frozen once submitted (status %)', OLD.status
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>cancelled', 'draft>expired',
      'pending_approval>approved', 'pending_approval>draft', 'pending_approval>cancelled', 'pending_approval>expired',
      'approved>sent', 'approved>cancelled', 'approved>expired',
      'sent>partially_filled', 'sent>filled', 'sent>cancelled', 'sent>expired',
      'partially_filled>filled', 'partially_filled>cancelled', 'partially_filled>expired',
      'filled>reconciled', 'reconciled>closed'])) THEN
    RAISE EXCEPTION 'illegal execution instruction transition % -> %', OLD.status, NEW.status
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS execution_instructions_guard_trg ON public.execution_instructions;
CREATE TRIGGER execution_instructions_guard_trg BEFORE UPDATE OR DELETE ON public.execution_instructions
  FOR EACH ROW EXECUTE FUNCTION public.execution_instructions_guard();

-- ── 4. Lot instructions (ORG, valid-axis) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS public.execution_instruction_lots (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organizations(id),
  instruction_id  uuid NOT NULL REFERENCES public.execution_instructions(id),
  tax_lot_id      uuid NOT NULL REFERENCES portfolio.tax_lots(id),
  quantity        numeric(28, 8) NOT NULL,
  created_by      uuid NOT NULL REFERENCES public.users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  valid_from      timestamptz NOT NULL DEFAULT now(),
  valid_to        timestamptz,
  system_from     timestamptz NOT NULL DEFAULT now(),
  system_to       timestamptz,
  CONSTRAINT execution_instruction_lots_quantity_chk CHECK (quantity > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS execution_instruction_lots_live_uq
  ON public.execution_instruction_lots (instruction_id, tax_lot_id) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.execution_instruction_lots_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'lot instructions are never deleted' USING ERRCODE = '42501';
  END IF;
  SELECT status INTO st FROM public.execution_instructions WHERE id = NEW.instruction_id;
  IF st IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'lot instructions change only while the instruction is a draft (status %)', st
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a lot instruction is only ever closed (valid_to), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS execution_instruction_lots_guard_trg ON public.execution_instruction_lots;
CREATE TRIGGER execution_instruction_lots_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.execution_instruction_lots
  FOR EACH ROW EXECUTE FUNCTION public.execution_instruction_lots_guard();

-- ── 5. Fills (ORG, append-only) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.execution_fills (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organizations(id),
  instruction_id  uuid NOT NULL REFERENCES public.execution_instructions(id),
  quantity        numeric(28, 8) NOT NULL,
  price           numeric(20, 6) NOT NULL,
  filled_at       timestamptz NOT NULL,
  lots            jsonb NOT NULL DEFAULT '[]'::jsonb,
  completes       boolean NOT NULL DEFAULT false,
  note            text,
  entered_by      uuid NOT NULL REFERENCES public.users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  valid_from      timestamptz NOT NULL DEFAULT now(),
  valid_to        timestamptz,
  system_from     timestamptz NOT NULL DEFAULT now(),
  system_to       timestamptz,
  CONSTRAINT execution_fills_quantity_chk CHECK (quantity > 0),
  CONSTRAINT execution_fills_price_chk CHECK (price > 0),
  CONSTRAINT execution_fills_lots_chk CHECK (jsonb_typeof(lots) = 'array')
);
CREATE INDEX IF NOT EXISTS execution_fills_instruction_idx ON public.execution_fills (instruction_id);

CREATE OR REPLACE FUNCTION public.execution_fills_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'fills are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT status INTO st FROM public.execution_instructions WHERE id = NEW.instruction_id;
    IF st IS NULL OR st NOT IN ('sent', 'partially_filled') THEN
      RAISE EXCEPTION 'a fill is entered only after the instruction is sent (status %)', st USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'fills are immutable' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS execution_fills_guard_trg ON public.execution_fills;
CREATE TRIGGER execution_fills_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.execution_fills
  FOR EACH ROW EXECUTE FUNCTION public.execution_fills_guard();

-- ── 6. Instruction event log (ORG, append-only) ────────────────────────────
-- One row per transition or logged human action; its id is the source_id of
-- the domain_events row (source_type 'execution_instruction_event').
CREATE TABLE IF NOT EXISTS public.execution_instruction_events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organizations(id),
  instruction_id  uuid NOT NULL REFERENCES public.execution_instructions(id),
  action          text NOT NULL,
  from_status     text,
  to_status       text NOT NULL,
  actor_id        uuid REFERENCES public.users(id),
  detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  valid_from      timestamptz NOT NULL DEFAULT now(),
  valid_to        timestamptz,
  system_from     timestamptz NOT NULL DEFAULT now(),
  system_to       timestamptz,
  CONSTRAINT execution_instruction_events_action_chk CHECK (action IN (
    'create', 'edit', 'submit', 'approve', 'reject', 'mark_sent', 'fill', 'reconcile', 'close', 'cancel',
    'expire', 'carry', 'ask', 'letter')),
  -- every action is a person's, except the logged expiry-time handling
  CONSTRAINT execution_instruction_events_human_chk CHECK (
    actor_id IS NOT NULL OR action IN ('expire', 'carry', 'ask')),
  CONSTRAINT execution_instruction_events_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS execution_instruction_events_instruction_idx
  ON public.execution_instruction_events (instruction_id, created_at);

CREATE OR REPLACE FUNCTION public.execution_events_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND public.execution_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'execution instruction events are immutable' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS execution_instruction_events_immutable_trg ON public.execution_instruction_events;
CREATE TRIGGER execution_instruction_events_immutable_trg BEFORE UPDATE OR DELETE ON public.execution_instruction_events
  FOR EACH ROW EXECUTE FUNCTION public.execution_events_immutable();

-- ── 7. RLS — a policy for every operation each table takes ────────────────
ALTER TABLE public.custodian_capabilities ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS custodian_capabilities_global_read ON public.custodian_capabilities;
CREATE POLICY custodian_capabilities_global_read ON public.custodian_capabilities FOR SELECT USING (true);
DROP POLICY IF EXISTS custodian_capabilities_super_admin_insert ON public.custodian_capabilities;
CREATE POLICY custodian_capabilities_super_admin_insert ON public.custodian_capabilities FOR INSERT
  WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS custodian_capabilities_super_admin_update ON public.custodian_capabilities;
CREATE POLICY custodian_capabilities_super_admin_update ON public.custodian_capabilities FOR UPDATE
  USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
  WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS custodian_capabilities_super_admin_delete ON public.custodian_capabilities;
CREATE POLICY custodian_capabilities_super_admin_delete ON public.custodian_capabilities FOR DELETE
  USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON public.custodian_capabilities TO app_service;

DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['execution_instructions', 'execution_instruction_lots', 'execution_fills',
                           'execution_instruction_events'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t, pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;

-- ── 8. Permissions — org_admin by default (and, via
--        rbac.ensure_org_admin_role, every NEW org's org_admin) ────────────
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_execution', 'execution', 'view'),
  ('manage_execution', 'execution', 'manage'),
  ('approve_execution', 'execution', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_execution', 'manage_execution', 'approve_execution')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;
