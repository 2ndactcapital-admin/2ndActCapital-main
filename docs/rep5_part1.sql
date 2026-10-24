-- REP5 — Replication Studio, sprint 5: accounts join models, the transition
-- analysis of what an account already holds, rebalance PROPOSALS from four
-- triggers (+ manual), proposal approval on human tasks, and approved
-- proposals turned into execution instructions through rep2.
--
-- Applied by apps/api/scripts/_apply_rep5.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- SCHEMA. Every new table lives in `public` (beside public.accounts and
-- public.execution_instructions) and is ORG-scoped: NULLIF org isolation (or
-- the super-admin carve-out) on SELECT, INSERT, UPDATE and DELETE.
--
-- BI-TEMPORAL (Rule 3)
--   account_model_links        — BOTH axes. A settings change (or a model
--                                 change, or an end) is a NEW version row (its
--                                 own id, version_no + 1, same link_group_id);
--                                 the version it replaces is closed on the
--                                 VALID axis when the change is approved.
--                                 A version's own decision (pending → active /
--                                 rejected, active → superseded / ended) is a
--                                 SYSTEM-axis archive: proposals point at the
--                                 version id, so the id is kept.
--   rebalance_proposals        — SYSTEM axis (trades, instructions and the
--                                 approval task point at the id).
--   rebalance_proposal_trades  — VALID axis: a trade is never edited; linking
--                                 it to its instruction closes the row and
--                                 inserts its successor.
--   account_transition_plans   — VALID axis: a new plan closes the current one.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(): execution.teardown = 'fixture', never
--   as app_service) — the same switch rep2 uses.
--
-- REP2 CHANGES (Task 1d — instructions ALREADY approved upstream)
--   * execution_instructions gets a BEFORE INSERT guard: a live row is
--     inserted as 'draft', or as 'approved' ONLY when it is sourced from a
--     rebalance proposal that is approved, for the same org and account, whose
--     human maker is the instruction's maker and whose checker is its
--     approved_by. Every rep2 CHECK (makers_chk, maker_checker_chk,
--     approved_chk) is unchanged and still applies.
--   * execution_instruction_lots_guard: a lot may be inserted into an
--     'approved' proposal-sourced instruction only in the transaction that
--     created that instruction (created_at = now()); every other rule as before.
--
-- THE CUSTODY CLIFF. Nothing here trades. A proposal is a recommendation; an
-- approved proposal becomes execution instructions a person carries out.

-- ── 0. Permissions (org_admin by default; rbac.ensure_org_admin_role too) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_proposals', 'proposals', 'view'),
  ('manage_proposals', 'proposals', 'manage'),
  ('approve_proposals', 'proposals', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_proposals', 'manage_proposals', 'approve_proposals')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. Account → model links (versioned settings) ──────────────────────────
CREATE TABLE IF NOT EXISTS public.account_model_links (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  account_id               uuid NOT NULL REFERENCES public.accounts(id),
  model_id                 uuid NOT NULL REFERENCES portfolio.models(id),
  follow                   text NOT NULL DEFAULT 'live',
  link_group_id            uuid NOT NULL,
  version_no               integer NOT NULL DEFAULT 1,
  change_kind              text NOT NULL,
  replaces_link_id         uuid REFERENCES public.account_model_links(id),
  status                   text NOT NULL DEFAULT 'pending',
  advice_only              boolean NOT NULL DEFAULT false,
  cash_floor               numeric(14, 10) NOT NULL DEFAULT 0,
  drift_threshold          numeric(14, 10),
  rebalance_frequency      text,
  exclusions               jsonb NOT NULL DEFAULT '[]'::jsonb,
  exclusion_policy         text NOT NULL DEFAULT 'pro_rata',
  gain_budget              numeric(20, 2),
  avoid_short_term         boolean NOT NULL DEFAULT true,
  short_term_wait_days     integer NOT NULL DEFAULT 30,
  harvesting_engine        text NOT NULL DEFAULT 'hollisworks',
  fee_schedule_override_id uuid REFERENCES public.fee_schedules(id),
  proposed_fee             jsonb NOT NULL DEFAULT '{}'::jsonb,
  fee_assignment_id        uuid REFERENCES public.fee_assignments(id),
  fee_exclusion_ids        uuid[] NOT NULL DEFAULT '{}',
  maker_id                 uuid NOT NULL REFERENCES public.users(id),
  checker_id               uuid REFERENCES public.users(id),
  approval_task_id         uuid REFERENCES public.workflow_run_steps(id),
  decision_note            text,
  decided_at               timestamptz,
  joined_at                timestamptz,
  ended_at                 timestamptz,
  end_reason               text,
  notes                    text,
  archived_from_id         uuid REFERENCES public.account_model_links(id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT account_model_links_follow_chk CHECK (follow = 'live'),
  CONSTRAINT account_model_links_change_chk CHECK (change_kind IN ('join', 'settings', 'change_model', 'end')),
  CONSTRAINT account_model_links_status_chk CHECK (status IN ('pending', 'active', 'ended', 'rejected', 'superseded')),
  CONSTRAINT account_model_links_version_chk CHECK (version_no >= 1),
  CONSTRAINT account_model_links_cash_floor_chk CHECK (cash_floor >= 0 AND cash_floor < 1),
  CONSTRAINT account_model_links_drift_chk CHECK (drift_threshold IS NULL OR (drift_threshold > 0 AND drift_threshold <= 1)),
  CONSTRAINT account_model_links_frequency_chk CHECK (rebalance_frequency IS NULL OR rebalance_frequency IN (
    'daily', 'weekly', 'monthly', 'quarterly', 'semiannually', 'annually')),
  CONSTRAINT account_model_links_exclusions_chk CHECK (jsonb_typeof(exclusions) = 'array'),
  CONSTRAINT account_model_links_policy_chk CHECK (exclusion_policy IN ('pro_rata', 'to_cash', 'use_alternates')),
  CONSTRAINT account_model_links_budget_chk CHECK (gain_budget IS NULL OR gain_budget >= 0),
  CONSTRAINT account_model_links_wait_chk CHECK (short_term_wait_days BETWEEN 0 AND 366),
  CONSTRAINT account_model_links_engine_chk CHECK (harvesting_engine IN ('hollisworks', 'custodian')),
  CONSTRAINT account_model_links_fee_chk CHECK (jsonb_typeof(proposed_fee) = 'object'),
  -- maker-checker in the database: the approver is never the maker
  CONSTRAINT account_model_links_maker_checker_chk CHECK (checker_id IS NULL OR checker_id <> maker_id),
  CONSTRAINT account_model_links_pending_chk CHECK (status <> 'pending' OR approval_task_id IS NOT NULL
                                                    OR system_to IS NOT NULL),
  CONSTRAINT account_model_links_decided_chk CHECK (status NOT IN ('active', 'rejected', 'superseded', 'ended')
                                                    OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT account_model_links_active_chk CHECK (status NOT IN ('active', 'superseded', 'ended')
                                                   OR joined_at IS NOT NULL),
  CONSTRAINT account_model_links_rejected_chk CHECK (status <> 'rejected'
                                                     OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT account_model_links_ended_chk CHECK (status <> 'ended'
                                                  OR (ended_at IS NOT NULL AND length(btrim(COALESCE(end_reason, ''))) > 0)),
  CONSTRAINT account_model_links_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one ACTIVE link and one PENDING change per account (live rows only)
CREATE UNIQUE INDEX IF NOT EXISTS account_model_links_active_uq
  ON public.account_model_links (account_id) WHERE status = 'active' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_model_links_pending_uq
  ON public.account_model_links (account_id) WHERE status = 'pending' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_model_links_version_uq
  ON public.account_model_links (link_group_id, version_no) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS account_model_links_model_idx
  ON public.account_model_links (model_id) WHERE status = 'active' AND valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.account_model_links_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'checker_id', 'decision_note', 'decided_at', 'joined_at', 'ended_at',
    'end_reason', 'approval_task_id', 'fee_assignment_id', 'fee_exclusion_ids', 'valid_to', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'account model links are never deleted (end the link instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived account model link state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live link row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NOT NULL THEN
    RAISE EXCEPTION 'a closed account model link version is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('ended', 'rejected', 'superseded') THEN
    RAISE EXCEPTION 'a % account model link is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  -- the settings of a version never change: a change is a new version
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'account model link settings are versioned: a change is a new version, not an edit'
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'pending>active', 'pending>rejected', 'pending>ended', 'active>superseded', 'active>ended'])) THEN
    RAISE EXCEPTION 'illegal account model link transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS account_model_links_guard_trg ON public.account_model_links;
CREATE TRIGGER account_model_links_guard_trg BEFORE UPDATE OR DELETE ON public.account_model_links
  FOR EACH ROW EXECUTE FUNCTION public.account_model_links_guard();

-- ── 2. Rebalance proposals ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.rebalance_proposals (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  account_id            uuid NOT NULL REFERENCES public.accounts(id),
  link_id               uuid NOT NULL REFERENCES public.account_model_links(id),
  model_id              uuid NOT NULL REFERENCES portfolio.models(id),
  model_version_id      uuid NOT NULL REFERENCES portfolio.model_versions(id),
  trigger               text NOT NULL,
  trigger_ref           jsonb NOT NULL DEFAULT '{}'::jsonb,
  status                text NOT NULL DEFAULT 'draft',
  advice_only           boolean NOT NULL DEFAULT false,
  valuation_as_of       date,
  account_value         numeric(28, 8) NOT NULL DEFAULT 0,
  cash_value            numeric(28, 8) NOT NULL DEFAULT 0,
  withdrawal_amount     numeric(28, 8),
  target_weights        jsonb NOT NULL DEFAULT '[]'::jsonb,
  before_weights        jsonb NOT NULL DEFAULT '[]'::jsonb,
  after_weights         jsonb NOT NULL DEFAULT '[]'::jsonb,
  drift_before          numeric(14, 10),
  drift_after           numeric(14, 10),
  est_st_gain           numeric(28, 8) NOT NULL DEFAULT 0,
  est_lt_gain           numeric(28, 8) NOT NULL DEFAULT 0,
  gain_budget           numeric(20, 2),
  gain_realized_ytd     numeric(28, 8) NOT NULL DEFAULT 0,
  warnings              jsonb NOT NULL DEFAULT '[]'::jsonb,
  sell_later            jsonb NOT NULL DEFAULT '[]'::jsonb,
  expires_at            timestamptz NOT NULL,
  maker_kind            text NOT NULL,
  maker_id              uuid REFERENCES public.users(id),
  checker_id            uuid REFERENCES public.users(id),
  approval_task_id      uuid REFERENCES public.workflow_run_steps(id),
  submitted_at          timestamptz,
  decision_note         text,
  decided_at            timestamptz,
  superseded_by_id      uuid REFERENCES public.rebalance_proposals(id),
  status_reason         text,
  execution             jsonb NOT NULL DEFAULT '{}'::jsonb,
  executed_at           timestamptz,
  archived_from_id      uuid REFERENCES public.rebalance_proposals(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT rebalance_proposals_trigger_chk CHECK (trigger IN ('join', 'model_change', 'drift', 'cash_flow', 'manual')),
  CONSTRAINT rebalance_proposals_status_chk CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected',
                                                             'expired', 'superseded', 'executed')),
  CONSTRAINT rebalance_proposals_maker_kind_chk CHECK ((maker_kind = 'user' AND maker_id IS NOT NULL)
                                                       OR (maker_kind = 'system' AND maker_id IS NULL)),
  CONSTRAINT rebalance_proposals_maker_checker_chk CHECK (checker_id IS NULL OR maker_id IS NULL OR checker_id <> maker_id),
  CONSTRAINT rebalance_proposals_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT rebalance_proposals_decided_chk CHECK (status NOT IN ('approved', 'executed', 'rejected')
                                                    OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT rebalance_proposals_rejected_chk CHECK (status <> 'rejected'
                                                     OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT rebalance_proposals_reason_chk CHECK (status NOT IN ('expired', 'superseded')
                                                   OR length(btrim(COALESCE(status_reason, ''))) > 0),
  CONSTRAINT rebalance_proposals_executed_chk CHECK (status <> 'executed'
                                                     OR (executed_at IS NOT NULL AND length(btrim(COALESCE(status_reason, ''))) > 0)),
  CONSTRAINT rebalance_proposals_withdrawal_chk CHECK (withdrawal_amount IS NULL OR withdrawal_amount > 0),
  CONSTRAINT rebalance_proposals_json_chk CHECK (jsonb_typeof(target_weights) = 'array'
                                                 AND jsonb_typeof(before_weights) = 'array'
                                                 AND jsonb_typeof(after_weights) = 'array'
                                                 AND jsonb_typeof(warnings) = 'array'
                                                 AND jsonb_typeof(sell_later) = 'array'
                                                 AND jsonb_typeof(trigger_ref) = 'object'
                                                 AND jsonb_typeof(execution) = 'object'),
  CONSTRAINT rebalance_proposals_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one OPEN proposal per account (draft, awaiting approval, or approved and executing)
-- superseding names its successor in the same transaction that inserts it
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_superseded_by_id_fkey;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_superseded_by_id_fkey
  FOREIGN KEY (superseded_by_id) REFERENCES public.rebalance_proposals(id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX IF NOT EXISTS rebalance_proposals_open_uq
  ON public.rebalance_proposals (account_id)
  WHERE status IN ('draft', 'pending_approval', 'approved') AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS rebalance_proposals_task_uq
  ON public.rebalance_proposals (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS rebalance_proposals_account_idx
  ON public.rebalance_proposals (account_id, created_at DESC) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.rebalance_proposals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'checker_id', 'approval_task_id', 'submitted_at', 'decision_note',
    'decided_at', 'superseded_by_id', 'status_reason', 'execution', 'executed_at', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'rebalance proposals are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived rebalance proposal state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live proposal row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('rejected', 'expired', 'superseded', 'executed') THEN
    RAISE EXCEPTION 'a % rebalance proposal is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  -- what was proposed never changes: only the lifecycle columns move
  IF (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'a rebalance proposal''s content is fixed when it is generated — generate a new one'
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>superseded', 'draft>expired',
      'pending_approval>approved', 'pending_approval>rejected', 'pending_approval>superseded',
      'pending_approval>expired', 'approved>executed'])) THEN
    RAISE EXCEPTION 'illegal rebalance proposal transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS rebalance_proposals_guard_trg ON public.rebalance_proposals;
CREATE TRIGGER rebalance_proposals_guard_trg BEFORE UPDATE OR DELETE ON public.rebalance_proposals
  FOR EACH ROW EXECUTE FUNCTION public.rebalance_proposals_guard();

-- ── 3. Proposal trades ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.rebalance_proposal_trades (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  proposal_id         uuid NOT NULL REFERENCES public.rebalance_proposals(id),
  line_no             integer NOT NULL,
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  side                text NOT NULL,
  quantity            numeric(28, 8),
  notional            numeric(28, 2),
  price               numeric(28, 8),
  price_source        text NOT NULL,
  price_as_of         date,
  lots                jsonb NOT NULL DEFAULT '[]'::jsonb,
  est_st_gain         numeric(28, 8) NOT NULL DEFAULT 0,
  est_lt_gain         numeric(28, 8) NOT NULL DEFAULT 0,
  reason              text NOT NULL,
  wash_sale_flag      jsonb,
  instruction_id      uuid REFERENCES public.execution_instructions(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT rebalance_proposal_trades_line_chk CHECK (line_no >= 1),
  CONSTRAINT rebalance_proposal_trades_side_chk CHECK (side IN ('buy', 'sell')),
  CONSTRAINT rebalance_proposal_trades_amount_chk CHECK (
    (quantity IS NOT NULL AND notional IS NULL AND quantity > 0)
    OR (quantity IS NULL AND notional IS NOT NULL AND notional > 0)),
  -- a sale is always a quantity of shares the account holds; only a buy is ever by notional
  CONSTRAINT rebalance_proposal_trades_sell_qty_chk CHECK (side = 'buy' OR quantity IS NOT NULL),
  CONSTRAINT rebalance_proposal_trades_price_chk CHECK (
    (price_source IN ('account_position', 'security_price') AND price > 0 AND price_as_of IS NOT NULL)
    OR (price_source = 'none' AND price IS NULL AND notional IS NOT NULL)),
  CONSTRAINT rebalance_proposal_trades_lots_chk CHECK (jsonb_typeof(lots) = 'array'
                                                       AND (side = 'sell' OR jsonb_array_length(lots) = 0)),
  CONSTRAINT rebalance_proposal_trades_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT rebalance_proposal_trades_flag_chk CHECK (wash_sale_flag IS NULL OR jsonb_typeof(wash_sale_flag) = 'object')
);
CREATE UNIQUE INDEX IF NOT EXISTS rebalance_proposal_trades_line_uq
  ON public.rebalance_proposal_trades (proposal_id, line_no) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.rebalance_proposal_trades_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'proposal trades are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a proposal trade is only ever closed (valid_to), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS rebalance_proposal_trades_guard_trg ON public.rebalance_proposal_trades;
CREATE TRIGGER rebalance_proposal_trades_guard_trg BEFORE UPDATE OR DELETE ON public.rebalance_proposal_trades
  FOR EACH ROW EXECUTE FUNCTION public.rebalance_proposal_trades_guard();

-- ── 4. Transition plans ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.account_transition_plans (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  account_id           uuid NOT NULL REFERENCES public.accounts(id),
  link_id              uuid NOT NULL REFERENCES public.account_model_links(id),
  proposal_id          uuid REFERENCES public.rebalance_proposals(id),
  valuation_as_of      date,
  holdings             jsonb NOT NULL DEFAULT '[]'::jsonb,
  plan_by_year         jsonb NOT NULL DEFAULT '[]'::jsonb,
  total_embedded_gain  numeric(28, 8) NOT NULL DEFAULT 0,
  gain_budget          numeric(20, 2),
  years_to_complete    integer,
  created_by           uuid REFERENCES public.users(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT account_transition_plans_json_chk CHECK (jsonb_typeof(holdings) = 'array'
                                                      AND jsonb_typeof(plan_by_year) = 'array'),
  CONSTRAINT account_transition_plans_years_chk CHECK (years_to_complete IS NULL OR years_to_complete >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_transition_plans_current_uq
  ON public.account_transition_plans (account_id) WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.account_transition_plans_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'transition plans are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a transition plan is only ever closed (valid_to), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS account_transition_plans_guard_trg ON public.account_transition_plans;
CREATE TRIGGER account_transition_plans_guard_trg BEFORE UPDATE OR DELETE ON public.account_transition_plans
  FOR EACH ROW EXECUTE FUNCTION public.account_transition_plans_guard();

-- ── 5. rep2 (Task 1d): instructions approved upstream by a proposal ────────
CREATE OR REPLACE FUNCTION public.execution_instructions_insert_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- an archive copy (system_to set) is how every rep2 change is recorded
  IF NEW.system_to IS NOT NULL THEN
    RETURN NEW;
  END IF;
  IF NEW.status = 'draft' THEN
    RETURN NEW;
  END IF;
  IF NEW.status = 'approved' AND NEW.source = 'proposal' AND EXISTS (
       SELECT 1 FROM public.rebalance_proposals p
       WHERE p.id = NEW.source_ref AND p.org_id = NEW.org_id AND p.account_id = NEW.account_id
         AND p.system_to IS NULL AND p.status = 'approved' AND p.maker_kind = 'user'
         AND p.maker_id = NEW.maker_id AND p.checker_id = NEW.approved_by) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'an execution instruction starts as a draft, or approved only from an approved rebalance '
    'proposal whose maker and checker it carries' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS execution_instructions_insert_guard_trg ON public.execution_instructions;
CREATE TRIGGER execution_instructions_insert_guard_trg BEFORE INSERT ON public.execution_instructions
  FOR EACH ROW EXECUTE FUNCTION public.execution_instructions_insert_guard();

CREATE OR REPLACE FUNCTION public.execution_instruction_lots_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE st text; src text; made timestamptz;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'lot instructions are never deleted' USING ERRCODE = '42501';
  END IF;
  SELECT status, source, created_at INTO st, src, made FROM public.execution_instructions
   WHERE id = NEW.instruction_id AND system_to IS NULL;
  -- rep5: an instruction approved upstream (a rebalance proposal) takes its
  -- lots in the transaction that created it, and never again
  IF TG_OP = 'INSERT' AND st = 'approved' AND src = 'proposal' AND made = now() THEN
    RETURN NEW;
  END IF;
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

-- ── 6. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['account_model_links', 'rebalance_proposals', 'rebalance_proposal_trades',
                           'account_transition_plans'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t,
                   pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;
