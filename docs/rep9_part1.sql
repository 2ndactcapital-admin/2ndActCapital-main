-- REP9 — Replication Studio, sprint 9: block trading, allocation and DVP
-- settlement, manual-first.
--
-- Approved account instructions (rep2) aggregated into a BLOCK under a written
-- allocation policy fixed before execution; the block executed by a broker /
-- desk / trader of the adviser's choice through the firm's executing (omnibus)
-- account; fills at the block level, the average price computed; each
-- account's allocation booked on its own instruction at the average price and
-- settled DVP to its own custodian; reconciliation; corrections only through a
-- recorded, second-person-approved correction.
--
-- Applied by apps/api/scripts/_apply_rep9.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- NOTHING IS SENT ELECTRONICALLY. route = 'manual' is the only route the
-- database admits (trade_blocks_route_chk) until a FIX router is contracted
-- and connected — a migration widens that CHECK on that day, never before.
--
-- BI-TEMPORAL (Rule 3)
--   execution_accounts, trade_blocks, block_allocations — SYSTEM axis: blocks
--     point at the executing account, allocations and fills point at the
--     block, settlements point at the allocation; the outgoing state is copied
--     to a fresh id (archived_from_id = live id, system_to = now()) and the
--     live row keeps its id (rep2's execution_instructions precedent).
--   allocation_policies, allocation_corrections — SYSTEM axis for the
--     decision (draft → approved / rejected on the same id: the approval task
--     points at it) and VALID axis for supersession (a newer approved policy
--     closes the previous one's valid_to) — rep7b's public.authority_change_guard,
--     reused unchanged.
--   block_fills, block_events — append-only, immutable.
--   block_charges — VALID axis: a wrong charge is closed with a reason, never
--     edited or deleted.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- THE CUSTODY CLIFF. Nothing here places a trade or moves money. A block, its
-- order execution sheet and its settlement detail are records of what a PERSON
-- is asked to do and what a person reports.

-- ── 0. Permissions (org_admin by default; rbac.ensure_org_admin_role too) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_blocks', 'blocks', 'view'),
  ('manage_blocks', 'blocks', 'manage'),
  ('approve_blocks', 'blocks', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_blocks', 'manage_blocks', 'approve_blocks')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. Executing / omnibus accounts at brokers (ORG, system axis) ─────────
-- An ORG-level record of the firm's own account at a broker. Never a member
-- account: no FK to public.accounts, and the reference is stored MASKED only.
CREATE TABLE IF NOT EXISTS public.execution_accounts (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                    uuid NOT NULL REFERENCES public.organizations(id),
  broker_name               text NOT NULL,
  account_reference_masked  text NOT NULL,
  dvp_capable               boolean NOT NULL,
  contact                   text,
  notes                     text,
  status                    text NOT NULL DEFAULT 'active',
  created_by                uuid NOT NULL REFERENCES public.users(id),
  archived_from_id          uuid REFERENCES public.execution_accounts(id),
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  valid_from                timestamptz NOT NULL DEFAULT now(),
  valid_to                  timestamptz,
  system_from               timestamptz NOT NULL DEFAULT now(),
  system_to                 timestamptz,
  CONSTRAINT execution_accounts_broker_chk CHECK (length(btrim(broker_name)) > 0),
  CONSTRAINT execution_accounts_masked_chk CHECK (account_reference_masked ~ '^\*{2,}[0-9A-Za-z]{0,4}$'),
  CONSTRAINT execution_accounts_status_chk CHECK (status IN ('active', 'retired')),
  CONSTRAINT execution_accounts_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS execution_accounts_org_idx ON public.execution_accounts (org_id) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.execution_accounts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'executing accounts are never deleted (retire one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived executing-account state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
     OR NEW.org_id <> OLD.org_id OR NEW.created_by <> OLD.created_by THEN
    RAISE EXCEPTION 'the live executing-account row keeps its id and org; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'retired' THEN
    RAISE EXCEPTION 'a retired executing account is immutable' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS execution_accounts_guard_trg ON public.execution_accounts;
CREATE TRIGGER execution_accounts_guard_trg BEFORE UPDATE OR DELETE ON public.execution_accounts
  FOR EACH ROW EXECUTE FUNCTION public.execution_accounts_guard();

-- ── 2. Allocation policies (ORG, versioned, approved by a second person) ──
-- The policy TEXT is the firm's own words (with its ADV reference); the code
-- enforces only its mechanics: method, minimum allocation, rounding rule,
-- partial-fill rule. The compliance questions (the written policy itself, its
-- ADV disclosure) stay open for the firm and counsel.
CREATE TABLE IF NOT EXISTS public.allocation_policies (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  version_no          integer NOT NULL,
  method              text NOT NULL DEFAULT 'pro_rata_requested',
  minimum_allocation  numeric(28, 8) NOT NULL DEFAULT 0,
  rounding_rule       text NOT NULL DEFAULT 'largest_remainder_then_account',
  partial_fill_rule   text NOT NULL DEFAULT 'pro_rata_allocated',
  policy_text         text NOT NULL,
  adv_reference       text,
  reason              text NOT NULL,
  status              text NOT NULL DEFAULT 'draft',
  maker_id            uuid NOT NULL REFERENCES public.users(id),
  approved_by         uuid REFERENCES public.users(id),
  approval_task_id    uuid REFERENCES public.workflow_run_steps(id),
  decided_at          timestamptz,
  decision_note       text,
  superseded_reason   text,
  archived_from_id    uuid REFERENCES public.allocation_policies(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT allocation_policies_method_chk CHECK (method IN ('pro_rata_requested')),
  CONSTRAINT allocation_policies_rounding_chk CHECK (rounding_rule IN ('largest_remainder_then_account')),
  CONSTRAINT allocation_policies_partial_chk CHECK (partial_fill_rule IN ('pro_rata_allocated')),
  CONSTRAINT allocation_policies_minimum_chk CHECK (minimum_allocation >= 0),
  CONSTRAINT allocation_policies_text_chk CHECK (length(btrim(policy_text)) > 0),
  CONSTRAINT allocation_policies_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT allocation_policies_version_chk CHECK (version_no >= 1),
  CONSTRAINT allocation_policies_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT allocation_policies_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT allocation_policies_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                    OR system_to IS NOT NULL),
  CONSTRAINT allocation_policies_decided_chk CHECK (status = 'draft' OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT allocation_policies_rejected_chk CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT allocation_policies_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT allocation_policies_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one live approved policy per org; one awaiting approval at a time; versions unique
CREATE UNIQUE INDEX IF NOT EXISTS allocation_policies_live_uq ON public.allocation_policies (org_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS allocation_policies_pending_uq ON public.allocation_policies (org_id)
  WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS allocation_policies_version_uq ON public.allocation_policies (org_id, version_no)
  WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS allocation_policies_task_uq ON public.allocation_policies (approval_task_id)
  WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

DROP TRIGGER IF EXISTS allocation_policies_guard_trg ON public.allocation_policies;
CREATE TRIGGER allocation_policies_guard_trg BEFORE UPDATE OR DELETE ON public.allocation_policies
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();

-- ── 3. Trade blocks (ORG, system axis, maker-checker) ──────────────────────
CREATE TABLE IF NOT EXISTS public.trade_blocks (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                     uuid NOT NULL REFERENCES public.organizations(id),
  global_security_id         uuid NOT NULL REFERENCES portfolio.securities_global(id),
  side                       text NOT NULL,
  total_quantity             numeric(28, 8) NOT NULL,
  executor_type              text NOT NULL,
  executor_name              text,
  executor_contact           text,
  execution_account_id       uuid REFERENCES public.execution_accounts(id),
  route                      text NOT NULL DEFAULT 'manual',
  automated_route_available  boolean NOT NULL DEFAULT false,
  manual_reason              text,
  price_kind                 text NOT NULL DEFAULT 'market',
  limit_price                numeric(20, 6),
  range_low                  numeric(20, 6),
  range_high                 numeric(20, 6),
  reference_price            numeric(20, 6),
  reference_at               timestamptz,
  reference_source           text,
  reference_provider         text,
  reference_as_of            date,
  execute_by                 date NOT NULL,
  if_not_filled              text NOT NULL,
  policy_id                  uuid NOT NULL REFERENCES public.allocation_policies(id),
  cross_flag                 boolean NOT NULL DEFAULT false,
  cross_reason               text,
  firm_contact               text,
  notes                      text,
  status                     text NOT NULL DEFAULT 'draft',
  maker_id                   uuid NOT NULL REFERENCES public.users(id),
  maker_ids                  uuid[] NOT NULL,
  approval_task_id           uuid REFERENCES public.workflow_run_steps(id),
  approved_by                uuid REFERENCES public.users(id),
  approved_at                timestamptz,
  sent_by                    uuid REFERENCES public.users(id),
  sent_at                    timestamptz,
  sent_channel               text,
  sent_note                  text,
  filled_quantity            numeric(28, 8) NOT NULL DEFAULT 0,
  average_price              numeric(20, 6),
  allocated_by               uuid REFERENCES public.users(id),
  allocated_at               timestamptz,
  remainder_handling         text,
  settled_at                 timestamptz,
  closed_by                  uuid REFERENCES public.users(id),
  closed_at                  timestamptz,
  cancelled_by               uuid REFERENCES public.users(id),
  cancelled_at               timestamptz,
  cancel_reason              text,
  archived_from_id           uuid REFERENCES public.trade_blocks(id),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  valid_from                 timestamptz NOT NULL DEFAULT now(),
  valid_to                   timestamptz,
  system_from                timestamptz NOT NULL DEFAULT now(),
  system_to                  timestamptz,
  CONSTRAINT trade_blocks_side_chk CHECK (side IN ('buy', 'sell')),
  CONSTRAINT trade_blocks_quantity_chk CHECK (total_quantity > 0),
  CONSTRAINT trade_blocks_executor_chk CHECK (executor_type IN ('broker', 'trader', 'custodian_desk')),
  -- a broker / trader block executes in the firm's executing (omnibus) account
  CONSTRAINT trade_blocks_execution_account_chk CHECK (executor_type = 'custodian_desk' OR execution_account_id IS NOT NULL),
  -- manual is the ONLY admitted route until a FIX router is connected (a migration widens this on that day)
  CONSTRAINT trade_blocks_route_chk CHECK (route = 'manual'),
  CONSTRAINT trade_blocks_manual_reason_chk CHECK (NOT automated_route_available OR route <> 'manual'
                                                   OR length(btrim(COALESCE(manual_reason, ''))) > 0),
  CONSTRAINT trade_blocks_price_chk CHECK (
    (price_kind = 'market' AND limit_price IS NULL AND range_low IS NULL AND range_high IS NULL)
    OR (price_kind = 'limit' AND limit_price > 0 AND range_low IS NULL AND range_high IS NULL)
    OR (price_kind = 'range' AND limit_price IS NULL AND range_low > 0 AND range_high >= range_low
        AND reference_price > 0)),
  -- a reference price is never invented: it carries its source, provider and as-of
  CONSTRAINT trade_blocks_reference_chk CHECK (
    (reference_price IS NULL AND reference_source IS NULL AND reference_provider IS NULL AND reference_as_of IS NULL)
    OR (reference_price > 0 AND reference_source IN ('security_data', 'adviser') AND reference_provider IS NOT NULL
        AND reference_as_of IS NOT NULL)),
  CONSTRAINT trade_blocks_if_not_filled_chk CHECK (if_not_filled IN ('cancel', 'carry', 'ask')),
  CONSTRAINT trade_blocks_cross_chk CHECK (NOT cross_flag OR length(btrim(COALESCE(cross_reason, ''))) > 0),
  CONSTRAINT trade_blocks_status_chk CHECK (status IN (
    'draft', 'pending_approval', 'approved', 'sent', 'partially_filled', 'filled', 'allocated', 'settled', 'closed',
    'cancelled')),
  CONSTRAINT trade_blocks_makers_chk CHECK (maker_id = ANY (maker_ids)),
  CONSTRAINT trade_blocks_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT trade_blocks_approved_chk CHECK (
    status IN ('draft', 'pending_approval', 'cancelled') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT trade_blocks_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT trade_blocks_sent_chk CHECK (
    status NOT IN ('sent', 'partially_filled', 'filled', 'allocated', 'settled', 'closed')
    OR (sent_by IS NOT NULL AND sent_at IS NOT NULL AND length(btrim(COALESCE(sent_channel, ''))) > 0)),
  CONSTRAINT trade_blocks_filled_chk CHECK (filled_quantity >= 0 AND filled_quantity <= total_quantity),
  CONSTRAINT trade_blocks_allocated_chk CHECK (
    status NOT IN ('allocated', 'settled', 'closed')
    OR (allocated_by IS NOT NULL AND allocated_at IS NOT NULL AND average_price > 0 AND filled_quantity > 0)),
  CONSTRAINT trade_blocks_closed_chk CHECK (status <> 'closed' OR (closed_by IS NOT NULL AND closed_at IS NOT NULL)),
  CONSTRAINT trade_blocks_cancelled_chk CHECK (
    status <> 'cancelled' OR (cancelled_at IS NOT NULL AND length(btrim(COALESCE(cancel_reason, ''))) > 0)),
  CONSTRAINT trade_blocks_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS trade_blocks_org_idx ON public.trade_blocks (org_id, status) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS trade_blocks_task_live_uq
  ON public.trade_blocks (approval_task_id) WHERE system_to IS NULL AND approval_task_id IS NOT NULL;

-- status graph + "terms frozen once submitted", in the database (the service enforces it first)
CREATE OR REPLACE FUNCTION public.trade_blocks_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'maker_ids', 'approval_task_id', 'approved_by', 'approved_at', 'sent_by',
    'sent_at', 'sent_channel', 'sent_note', 'filled_quantity', 'average_price', 'allocated_by', 'allocated_at',
    'remainder_handling', 'settled_at', 'closed_by', 'closed_at', 'cancelled_by', 'cancelled_at', 'cancel_reason',
    'execute_by', 'updated_at', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'trade blocks are never deleted (cancel one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived trade block state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('closed', 'cancelled') THEN
    RAISE EXCEPTION 'a % trade block is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live block row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'trade block terms are fixed when the block is built (status %) — cancel it and build a new one',
      OLD.status USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>cancelled',
      'pending_approval>approved', 'pending_approval>draft', 'pending_approval>cancelled',
      'approved>sent', 'approved>cancelled',
      'sent>partially_filled', 'sent>filled', 'sent>cancelled',
      'partially_filled>filled', 'partially_filled>allocated',
      'filled>allocated', 'allocated>settled', 'settled>closed'])) THEN
    RAISE EXCEPTION 'illegal trade block transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trade_blocks_guard_trg ON public.trade_blocks;
CREATE TRIGGER trade_blocks_guard_trg BEFORE UPDATE OR DELETE ON public.trade_blocks
  FOR EACH ROW EXECUTE FUNCTION public.trade_blocks_guard();

-- ── 4. Allocation corrections (ORG, decided by a second person) ───────────
-- Declared before block_allocations (which points at the correction it last applied).
CREATE TABLE IF NOT EXISTS public.allocation_corrections (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  block_id           uuid NOT NULL REFERENCES public.trade_blocks(id),
  changes            jsonb NOT NULL,
  reason             text NOT NULL,
  status             text NOT NULL DEFAULT 'draft',
  maker_id           uuid NOT NULL REFERENCES public.users(id),
  approved_by        uuid REFERENCES public.users(id),
  approval_task_id   uuid REFERENCES public.workflow_run_steps(id),
  decided_at         timestamptz,
  decision_note      text,
  superseded_reason  text,
  archived_from_id   uuid REFERENCES public.allocation_corrections(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT allocation_corrections_changes_chk CHECK (jsonb_typeof(changes) = 'array' AND jsonb_array_length(changes) > 0),
  CONSTRAINT allocation_corrections_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT allocation_corrections_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT allocation_corrections_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT allocation_corrections_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                       OR system_to IS NOT NULL),
  CONSTRAINT allocation_corrections_decided_chk CHECK (status = 'draft'
                                                       OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT allocation_corrections_rejected_chk CHECK (status <> 'rejected'
                                                        OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT allocation_corrections_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT allocation_corrections_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one correction awaiting approval per block at a time
CREATE UNIQUE INDEX IF NOT EXISTS allocation_corrections_pending_uq ON public.allocation_corrections (block_id)
  WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS allocation_corrections_task_uq ON public.allocation_corrections (approval_task_id)
  WHERE approval_task_id IS NOT NULL AND system_to IS NULL;
DROP TRIGGER IF EXISTS allocation_corrections_guard_trg ON public.allocation_corrections;
CREATE TRIGGER allocation_corrections_guard_trg BEFORE UPDATE OR DELETE ON public.allocation_corrections
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();

-- ── 5. Block allocations (ORG, system axis) ───────────────────────────────
-- One row per account instruction in the block. requested / allocated are
-- FIXED when the block is approved; final is set once at allocation and
-- changes after that only with a newly approved correction (correction_id).
CREATE TABLE IF NOT EXISTS public.block_allocations (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL REFERENCES public.organizations(id),
  block_id                uuid NOT NULL REFERENCES public.trade_blocks(id),
  instruction_id          uuid NOT NULL REFERENCES public.execution_instructions(id),
  account_id              uuid NOT NULL REFERENCES public.accounts(id),
  requested_quantity      numeric(28, 8) NOT NULL,
  allocated_quantity      numeric(28, 8) NOT NULL,
  quantity_unit           numeric(28, 8) NOT NULL,
  final_quantity          numeric(28, 8),
  average_price           numeric(20, 6),
  zero_reason             text,
  settlement_kind         text NOT NULL,
  receiving_custodian     text NOT NULL,
  settlement_id           uuid REFERENCES public.instruction_settlements(id),
  settlement_status       text,
  settlement_method       text,
  settlement_reference    text,
  settlement_note         text,
  fail_reason             text,
  settlement_updated_by   uuid REFERENCES public.users(id),
  settlement_updated_at   timestamptz,
  correction_id           uuid REFERENCES public.allocation_corrections(id),
  status                  text NOT NULL DEFAULT 'planned',
  created_by              uuid NOT NULL REFERENCES public.users(id),
  archived_from_id        uuid REFERENCES public.block_allocations(id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT block_allocations_requested_chk CHECK (requested_quantity > 0),
  CONSTRAINT block_allocations_allocated_chk CHECK (allocated_quantity > 0 AND allocated_quantity <= requested_quantity),
  CONSTRAINT block_allocations_unit_chk CHECK (quantity_unit IN (1, 0.00000001)),
  CONSTRAINT block_allocations_final_chk CHECK (final_quantity IS NULL
                                                OR (final_quantity >= 0 AND final_quantity <= allocated_quantity)),
  CONSTRAINT block_allocations_zero_chk CHECK (final_quantity IS NULL OR final_quantity > 0
                                               OR length(btrim(COALESCE(zero_reason, ''))) > 0),
  CONSTRAINT block_allocations_settlement_kind_chk CHECK (settlement_kind IN ('integrated', 'dvp')),
  CONSTRAINT block_allocations_settlement_status_chk CHECK (settlement_status IS NULL OR settlement_status IN (
    'pending', 'affirmed', 'settled', 'failed', 'cancelled')),
  CONSTRAINT block_allocations_settlement_method_chk CHECK (settlement_method IS NULL
                                                            OR settlement_method IN ('manual', 'custodian_data')),
  CONSTRAINT block_allocations_failed_chk CHECK (settlement_status IS DISTINCT FROM 'failed'
                                                 OR length(btrim(COALESCE(fail_reason, ''))) > 0),
  CONSTRAINT block_allocations_settled_chk CHECK (settlement_status IS DISTINCT FROM 'settled' OR (
    (settlement_method = 'manual' AND length(btrim(COALESCE(settlement_note, ''))) > 0)
    OR (settlement_method = 'custodian_data' AND length(btrim(COALESCE(settlement_reference, ''))) > 0))),
  -- a DVP allocation that received shares settles through its rep7b settlement instruction
  CONSTRAINT block_allocations_dvp_chk CHECK (settlement_kind <> 'dvp' OR status <> 'allocated'
                                              OR COALESCE(final_quantity, 0) = 0 OR settlement_id IS NOT NULL),
  CONSTRAINT block_allocations_status_chk CHECK (status IN ('planned', 'fixed', 'allocated', 'cancelled')),
  CONSTRAINT block_allocations_allocated_state_chk CHECK (status <> 'allocated'
                                                          OR (final_quantity IS NOT NULL AND settlement_status IS NOT NULL
                                                              AND (final_quantity = 0 OR average_price > 0))),
  CONSTRAINT block_allocations_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS block_allocations_live_uq ON public.block_allocations (block_id, instruction_id)
  WHERE system_to IS NULL;
-- an instruction sits in at most ONE open (planned / fixed) allocation at a time
CREATE UNIQUE INDEX IF NOT EXISTS block_allocations_open_instruction_uq ON public.block_allocations (instruction_id)
  WHERE system_to IS NULL AND status IN ('planned', 'fixed');
CREATE INDEX IF NOT EXISTS block_allocations_block_idx ON public.block_allocations (block_id) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.block_allocations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  fixed_cols text[] := ARRAY['org_id', 'block_id', 'instruction_id', 'account_id', 'requested_quantity',
    'quantity_unit', 'settlement_kind', 'created_by', 'created_at'];
  ins record;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'block allocations are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT account_id, org_id INTO ins FROM public.execution_instructions WHERE id = NEW.instruction_id
      AND system_to IS NULL;
    IF ins IS NULL OR ins.account_id <> NEW.account_id OR ins.org_id <> NEW.org_id THEN
      RAISE EXCEPTION 'an allocation names its instruction''s own account and org' USING ERRCODE = '42501';
    END IF;
    IF NEW.archived_from_id IS NULL AND NEW.status <> 'planned' THEN
      RAISE EXCEPTION 'a new allocation starts planned' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived allocation state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live allocation row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF (SELECT bool_or((to_jsonb(NEW) -> c) IS DISTINCT FROM (to_jsonb(OLD) -> c)) FROM unnest(fixed_cols) c) THEN
    RAISE EXCEPTION 'an allocation''s account, instruction and requested quantity never change' USING ERRCODE = '42501';
  END IF;
  -- FIXED at approval: once out of 'planned', the pre-trade allocation never changes
  IF OLD.status <> 'planned' AND NEW.allocated_quantity <> OLD.allocated_quantity THEN
    RAISE EXCEPTION 'the pre-trade allocation is fixed at approval' USING ERRCODE = '42501';
  END IF;
  -- final: set once at allocation; afterwards only with a NEW approved correction
  IF OLD.final_quantity IS NOT NULL AND NEW.final_quantity IS DISTINCT FROM OLD.final_quantity
     AND (NEW.correction_id IS NULL OR NEW.correction_id IS NOT DISTINCT FROM OLD.correction_id
          OR NOT EXISTS (SELECT 1 FROM public.allocation_corrections c WHERE c.id = NEW.correction_id
                         AND c.status = 'approved' AND c.block_id = NEW.block_id)) THEN
    RAISE EXCEPTION 'an allocation changes after it is booked only through an approved correction (reason + a second person)'
      USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'cancelled' THEN
    RAISE EXCEPTION 'a cancelled allocation is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> NEW.status AND NOT ((OLD.status || '>' || NEW.status) = ANY (ARRAY[
      'planned>fixed', 'planned>cancelled', 'fixed>allocated', 'fixed>cancelled'])) THEN
    RAISE EXCEPTION 'illegal allocation transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS block_allocations_guard_trg ON public.block_allocations;
CREATE TRIGGER block_allocations_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.block_allocations
  FOR EACH ROW EXECUTE FUNCTION public.block_allocations_guard();

-- ── 6. Block fills (ORG, append-only) ─────────────────────────────────────
-- The average price is COMPUTED from these rows, never entered.
CREATE TABLE IF NOT EXISTS public.block_fills (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES public.organizations(id),
  block_id      uuid NOT NULL REFERENCES public.trade_blocks(id),
  quantity      numeric(28, 8) NOT NULL,
  price         numeric(20, 6) NOT NULL,
  filled_at     timestamptz NOT NULL,
  venue         text,
  note          text,
  entered_by    uuid NOT NULL REFERENCES public.users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  valid_from    timestamptz NOT NULL DEFAULT now(),
  valid_to      timestamptz,
  system_from   timestamptz NOT NULL DEFAULT now(),
  system_to     timestamptz,
  CONSTRAINT block_fills_quantity_chk CHECK (quantity > 0),
  CONSTRAINT block_fills_price_chk CHECK (price > 0)
);
CREATE INDEX IF NOT EXISTS block_fills_block_idx ON public.block_fills (block_id);

CREATE OR REPLACE FUNCTION public.block_fills_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'block fills are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT status INTO st FROM public.trade_blocks WHERE id = NEW.block_id AND system_to IS NULL;
    IF st IS NULL OR st NOT IN ('sent', 'partially_filled') THEN
      RAISE EXCEPTION 'a block fill is entered only after the block is sent (status %)', st USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'block fills are immutable' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS block_fills_guard_trg ON public.block_fills;
CREATE TRIGGER block_fills_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.block_fills
  FOR EACH ROW EXECUTE FUNCTION public.block_fills_guard();

-- ── 7. Commissions and fees per block (ORG, valid axis — closed, never edited) ──
CREATE TABLE IF NOT EXISTS public.block_charges (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES public.organizations(id),
  block_id       uuid NOT NULL REFERENCES public.trade_blocks(id),
  kind           text NOT NULL,
  amount         numeric(20, 4) NOT NULL,
  currency_code  text NOT NULL DEFAULT 'USD',
  payee          text,
  trade_away     boolean NOT NULL DEFAULT false,
  note           text,
  entered_by     uuid NOT NULL REFERENCES public.users(id),
  closed_reason  text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT block_charges_kind_chk CHECK (kind IN ('commission', 'fee', 'other')),
  CONSTRAINT block_charges_amount_chk CHECK (amount >= 0),
  CONSTRAINT block_charges_currency_chk CHECK (currency_code ~ '^[A-Z]{3}$'),
  CONSTRAINT block_charges_closed_chk CHECK (valid_to IS NULL OR length(btrim(COALESCE(closed_reason, ''))) > 0)
);
CREATE INDEX IF NOT EXISTS block_charges_block_idx ON public.block_charges (block_id);

CREATE OR REPLACE FUNCTION public.block_charges_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'block charges are never deleted (close one with a reason)' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to', 'closed_reason']) = (to_jsonb(OLD) - ARRAY['valid_to', 'closed_reason']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a block charge is only ever closed (with a reason), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS block_charges_guard_trg ON public.block_charges;
CREATE TRIGGER block_charges_guard_trg BEFORE UPDATE OR DELETE ON public.block_charges
  FOR EACH ROW EXECUTE FUNCTION public.block_charges_guard();

-- ── 8. Block event log (ORG, append-only) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.block_events (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES public.organizations(id),
  block_id       uuid NOT NULL REFERENCES public.trade_blocks(id),
  allocation_id  uuid REFERENCES public.block_allocations(id),
  action         text NOT NULL,
  from_status    text,
  to_status      text NOT NULL,
  actor_id       uuid REFERENCES public.users(id),
  detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT block_events_action_chk CHECK (action IN (
    'create', 'submit', 'approve', 'reject', 'mark_sent', 'fill', 'allocate', 'settlement', 'settlement_fail',
    'correction_request', 'correction_approve', 'correction_reject', 'charge', 'charge_close', 'close', 'cancel',
    'sheet', 'expire', 'carry', 'ask')),
  -- every action is a person's, except the logged execute-by handling
  CONSTRAINT block_events_human_chk CHECK (actor_id IS NOT NULL OR action IN ('expire', 'carry', 'ask')),
  CONSTRAINT block_events_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS block_events_block_idx ON public.block_events (block_id, created_at);
DROP TRIGGER IF EXISTS block_events_immutable_trg ON public.block_events;
CREATE TRIGGER block_events_immutable_trg BEFORE UPDATE OR DELETE ON public.block_events
  FOR EACH ROW EXECUTE FUNCTION public.execution_events_immutable();

-- ── 9. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['execution_accounts', 'allocation_policies', 'trade_blocks', 'allocation_corrections',
                           'block_allocations', 'block_fills', 'block_charges', 'block_events'] LOOP
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
