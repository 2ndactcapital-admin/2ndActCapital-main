-- CASH07 part 1 — PAYMENT INSTRUCTIONS (stage A, manual): the governed way to move cash between a family's OWN
-- accounts. Applied by apps/api/scripts/_apply_cash07.py --schema (operator DSN: DDL needs the table owner), in ONE
-- transaction (the apply script wraps it — no BEGIN / COMMIT here). Idempotent (IF NOT EXISTS / DROP … IF EXISTS +
-- CREATE / ON CONFLICT / NOT EXISTS).
--
-- WHY NEW TABLES (docs/cash/CASH07_NOTES.md §2, decided 2026-11-08): execution_instructions' CHECKs protect every
-- trade (side buy / sell, exactly one of quantity / notional, a global security, an executor) — a payment fits none of
-- them, and widening them would weaken the trade path (rep14's section351_transfers is the precedent). So:
--   public.payment_instructions  the SAME governance spine as execution_instructions: maker_ids + approved_by ∉
--                                maker_ids (CHECK), a status graph enforced by a trigger, frozen terms once submitted,
--                                sent / reconcile / cancel / expire columns, SYSTEM-axis archive (the events, the
--                                approval task and sod_decisions all point at the id, so the live row keeps it).
--   public.payment_events        append-only: ONE row per transition, written BY THE DATABASE (the transition
--                                trigger) — a status change that does not name its actor (payments.actor_id, set
--                                LOCAL by services/payments.py) violates payment_events_human_chk and is refused, so
--                                no direct UPDATE can move money state without a person on record. Non-status actions
--                                (edit, sheet, unmatched alert) are inserted by the service.
--
-- TEARDOWN GUC (documented): payments.teardown = 'fixture', set LOCAL by a role that is NOT app_service
-- (public.payments_teardown_allowed()) — the ONLY path past the delete / immutability guards of both tables. Verify
-- scripts pass it to scripts/_verify_cleanup.cleanup with sod.teardown and execution.teardown.
--
-- SAME OWNER ONLY (stage A): a BEFORE INSERT OR UPDATE trigger refuses any live row whose two accounts are not
-- (a) two different accounts of this org, (b) both in the instruction's household, (c) owned by the same entity
-- (accounts.primary_entity_id) with the same live account_owners entity set. Anything else: "third-party payments
-- come later (cash08)".
--
-- S30 FLOOR: item type payment_instruction (floor class 'transfer') with duties initiate_payment / approve_payment;
-- the platform FLOOR rule + policy (never; enforce_floor true). No org setting and no role relaxes it.
--
-- RLS: a policy for every operation on both tables, each reading the org GUC with NULLIF.
-- Writes no org row. Platform catalog rows only (permissions, SoD catalog, custodian capabilities).

-- ── 0. The teardown guard ──────────────────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.payments_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('payments.teardown', true), '') = 'fixture' AND current_user <> 'app_service'
$$;

-- ── 1. Permissions (org_admin by default; services/rbac.ORG_ADMIN_DEFAULT_PERMISSIONS keeps new orgs in step) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('initiate_payment', 'payments', 'initiate'),
  ('approve_payment', 'payments', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM public.roles r
  JOIN public.permissions p ON p.name IN ('initiate_payment', 'approve_payment')
 WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 2. SoD catalog: the payment_instruction FLOOR item type ───────────────────────────────────────────────────────
INSERT INTO public.sod_duties (duty_key, label, description, kind, permissions, sort_order) VALUES
  ('initiate_payment', 'Initiate a payment', 'Drafts, edits or submits a payment instruction between a family''s own accounts (payment_instructions).', 'make', '{initiate_payment}', 140),
  ('approve_payment', 'Approve a payment', 'Approves a submitted payment instruction; a different person from every maker, always.', 'check', '{approve_payment}', 141)
ON CONFLICT (duty_key) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description, kind = EXCLUDED.kind,
  permissions = EXCLUDED.permissions, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_item_types (item_type, label, description, maker_duty, checker_duty, floor_class,
                                   self_approval_capable, pre_s30_rule, site, sort_order) VALUES
  ('payment_instruction', 'Payment instruction', 'Money movement between a family''s own accounts (payment_instructions, CASH07). A FLOOR item: no self-approval, ever — no organization setting and no role relaxes it, Super Admin included.', 'initiate_payment', 'approve_payment', 'transfer', false, 'maker_ne_checker', 'services/payments.py', 140)
ON CONFLICT (item_type) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description,
  maker_duty = EXCLUDED.maker_duty, checker_duty = EXCLUDED.checker_duty, floor_class = EXCLUDED.floor_class,
  self_approval_capable = EXCLUDED.self_approval_capable, pre_s30_rule = EXCLUDED.pre_s30_rule,
  site = EXCLUDED.site, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, maker_duty, checker_duty, scope,
                              severity, description, status, version_no)
SELECT 'floor:' || t.item_type, 'platform', NULL, 'floor', t.item_type, t.maker_duty, t.checker_duty,
       'same_item', 'block',
       'The same person may not ' || lower(m.label) || ' and ' || lower(c.label) || ' on the same '
         || lower(t.label) || '.', 'active', 1
  FROM public.sod_item_types t
  JOIN public.sod_duties m ON m.duty_key = t.maker_duty
  JOIN public.sod_duties c ON c.duty_key = t.checker_duty
 WHERE t.item_type = 'payment_instruction'
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind IN ('floor', 'default'));

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                              enforce_floor, description, status, version_no)
SELECT 'policy:' || t.item_type, 'platform', NULL, 'policy', t.item_type, 'same_item', 'block', 'never', true,
       'Platform policy for ' || lower(t.label) || ': a different person approves, always (CASH07 floor).', 'active', 1
  FROM public.sod_item_types t
 WHERE t.item_type = 'payment_instruction'
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind = 'policy');

-- ── 3. Custodian capabilities: the payment rails + the status feed + the manual sheet ─────────────────────────────
ALTER TABLE public.custodian_capabilities DROP CONSTRAINT IF EXISTS custodian_capabilities_key_chk;
ALTER TABLE public.custodian_capabilities ADD CONSTRAINT custodian_capabilities_key_chk CHECK (capability_key IN (
  'fractional_shares', 'equity_trading_api', 'model_api', 'trade_list_upload', 'options_level', 'flex_options',
  'index_options', 'margin', 'short_selling', 'in_kind_transfers', 'custodian_tax_loss_harvesting',
  'lot_level_cost_basis_feed', 'held_away_aggregation', 'stock_borrow', 'margin_initial_pct',
  'margin_maintenance_pct', 'ach', 'wire', 'journal', 'check', 'payment_status_feed', 'payment_sheet'));

-- Seed (version 1). Altruist: as cash00 1f found — the read integration (OAuth scope accounts:read) returns positions
-- and balances, no write endpoint, no payment status; NO document establishes any rail, so every rail is 'unknown'
-- (= not available). Manual: the manual route sends nothing by itself — every rail is 'no' for the route; the payment
-- sheet is the one thing it does. A real custodian's rails are recorded by a Super Admin with evidence (operator step).
INSERT INTO public.custodian_capabilities
  (custodian_code, capability_key, value, evidence_kind, evidence, as_of, version_no, note)
SELECT v.code, v.key, v.value, v.kind, v.evidence, DATE '2026-11-09', 1, v.note
FROM (VALUES
  ('altruist', 'ach', 'unknown', 'document',
   'docs/cash/CASH00_DISCOVERY.md 1f: the read integration returns positions and balances only; no Altruist document on record states ACH support for an account',
   'Unknown is not available: no Altruist account can be a payment source until a Super Admin records evidence.'),
  ('altruist', 'wire', 'unknown', 'document', 'docs/cash/CASH00_DISCOVERY.md 1f: no Altruist document on record states wire support', NULL),
  ('altruist', 'journal', 'unknown', 'document', 'docs/cash/CASH00_DISCOVERY.md 1f: no Altruist document on record states journals between accounts', NULL),
  ('altruist', 'check', 'unknown', 'assumed', 'assumed — nothing on record', NULL),
  ('altruist', 'payment_status_feed', 'no', 'document',
   'apps/api/services/altruist_oauth.py: build_authorize_url requests scope accounts:read; no payment or transfer status endpoint exists in any Altruist module',
   'Payment status is confirmed by hand (and matched against the transaction feed at reconciliation).'),
  ('manual', 'ach', 'no', 'document', 'docs/cash/CASH_PAYMENTS.md (cash07): the manual route sends nothing — a person sends the payment sheet; the rail is the source custodian''s', NULL),
  ('manual', 'wire', 'no', 'document', 'docs/cash/CASH_PAYMENTS.md (cash07): manual route — no system interface', NULL),
  ('manual', 'journal', 'no', 'document', 'docs/cash/CASH_PAYMENTS.md (cash07): manual route — no system interface', NULL),
  ('manual', 'check', 'no', 'document', 'docs/cash/CASH_PAYMENTS.md (cash07): manual route — no system interface', NULL),
  ('manual', 'payment_status_feed', 'no', 'document', 'docs/cash/CASH_PAYMENTS.md (cash07): manual route — no data feed; status is confirmed by hand', NULL),
  ('manual', 'payment_sheet', 'yes', 'document', 'apps/api/services/payment_adapters.py (cash07): ManualPaymentAdapter renders the printable payment sheet', NULL)
) AS v(code, key, value, kind, evidence, note)
WHERE NOT EXISTS (
  SELECT 1 FROM public.custodian_capabilities c
  WHERE c.custodian_code = v.code AND c.capability_key = v.key AND c.system_to IS NULL);

-- ── 4. Payment instructions (ORG, system-axis) ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.payment_instructions (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                     uuid NOT NULL REFERENCES public.organizations(id),
  household_id               uuid NOT NULL REFERENCES public.households(id),
  source_account_id          uuid NOT NULL REFERENCES public.accounts(id),
  destination_account_id     uuid NOT NULL REFERENCES public.accounts(id),
  amount                     numeric(20, 2) NOT NULL,
  currency_code              text NOT NULL DEFAULT 'USD',
  rail                       text NOT NULL,
  value_date                 date NOT NULL,
  purpose                    text NOT NULL,
  purpose_ref_type           text,
  purpose_ref_id             uuid,
  status                     text NOT NULL DEFAULT 'draft',
  maker_id                   uuid NOT NULL REFERENCES public.users(id),
  maker_ids                  uuid[] NOT NULL,
  approval_task_id           uuid REFERENCES public.workflow_run_steps(id),
  approved_by                uuid REFERENCES public.users(id),
  approved_at                timestamptz,
  adapter                    text,
  sent_by                    uuid REFERENCES public.users(id),
  sent_at                    timestamptz,
  sent_channel               text,
  sent_note                  text,
  external_ref               text,
  consent_requested_at       timestamptz,
  acknowledged_by            uuid REFERENCES public.users(id),
  acknowledged_at            timestamptz,
  settled_by                 uuid REFERENCES public.users(id),
  settled_at                 timestamptz,
  settled_on                 date,
  provisional_until          date,
  custodian_rejected_reason  text,
  return_code                text,
  return_reason              text,
  returned_by                uuid REFERENCES public.users(id),
  returned_at                timestamptz,
  reconciled_by              uuid REFERENCES public.users(id),
  reconciled_at              timestamptz,
  reconcile_method           text,
  reconcile_reference        text,
  reconcile_note             text,
  reconciled_transaction_ids uuid[] NOT NULL DEFAULT '{}',
  closed_by                  uuid REFERENCES public.users(id),
  closed_at                  timestamptz,
  cancelled_by               uuid REFERENCES public.users(id),
  cancelled_at               timestamptz,
  cancel_reason              text,
  expired_at                 timestamptz,
  expiry_reason              text,
  archived_from_id           uuid REFERENCES public.payment_instructions(id),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  valid_from                 timestamptz NOT NULL DEFAULT now(),
  valid_to                   timestamptz,
  system_from                timestamptz NOT NULL DEFAULT now(),
  system_to                  timestamptz,
  CONSTRAINT payment_instructions_amount_chk CHECK (amount > 0),
  CONSTRAINT payment_instructions_currency_chk CHECK (currency_code = 'USD'),
  CONSTRAINT payment_instructions_rail_chk CHECK (rail IN ('ach', 'wire', 'journal', 'check')),
  CONSTRAINT payment_instructions_accounts_chk CHECK (source_account_id <> destination_account_id),
  CONSTRAINT payment_instructions_purpose_chk CHECK (length(btrim(purpose)) > 0),
  CONSTRAINT payment_instructions_purpose_ref_chk CHECK (
    (purpose_ref_type IS NULL AND purpose_ref_id IS NULL)
    OR (purpose_ref_type IN ('cash_obligation', 'cash_proposal') AND purpose_ref_id IS NOT NULL)),
  CONSTRAINT payment_instructions_status_chk CHECK (status IN (
    'draft', 'pending_approval', 'approved', 'sent', 'awaiting_client_consent', 'acknowledged', 'settled',
    'reconciled', 'closed', 'rejected', 'returned', 'cancelled', 'expired')),
  CONSTRAINT payment_instructions_makers_chk CHECK (maker_id = ANY (maker_ids)),
  -- maker-checker in the database: the approver is never one of the makers (the S30 floor's backstop)
  CONSTRAINT payment_instructions_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT payment_instructions_approved_chk CHECK (
    status IN ('draft', 'pending_approval', 'cancelled', 'expired')
    OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT payment_instructions_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT payment_instructions_sent_chk CHECK (
    status IN ('draft', 'pending_approval', 'approved', 'cancelled', 'expired')
    OR (sent_by IS NOT NULL AND sent_at IS NOT NULL AND length(btrim(COALESCE(sent_channel, ''))) > 0
        AND length(btrim(COALESCE(adapter, ''))) > 0)),
  CONSTRAINT payment_instructions_acknowledged_chk CHECK (
    status NOT IN ('acknowledged', 'settled', 'reconciled', 'closed')
    OR (acknowledged_by IS NOT NULL AND acknowledged_at IS NOT NULL)),
  CONSTRAINT payment_instructions_settled_chk CHECK (
    status NOT IN ('settled', 'reconciled', 'closed')
    OR (settled_by IS NOT NULL AND settled_at IS NOT NULL AND settled_on IS NOT NULL)),
  -- ACH settles PROVISIONALLY: the return window's end is recorded with the settlement
  CONSTRAINT payment_instructions_provisional_chk CHECK (
    settled_on IS NULL OR ((rail = 'ach') = (provisional_until IS NOT NULL))),
  CONSTRAINT payment_instructions_rejected_chk CHECK (
    status <> 'rejected' OR length(btrim(COALESCE(custodian_rejected_reason, ''))) > 0),
  -- a return is an ACH return code (R01 …) after settlement
  CONSTRAINT payment_instructions_returned_chk CHECK (
    status <> 'returned' OR (rail = 'ach' AND settled_on IS NOT NULL AND return_code ~ '^R[0-9]{2}$'
                             AND returned_by IS NOT NULL AND returned_at IS NOT NULL)),
  CONSTRAINT payment_instructions_reconciled_chk CHECK (
    status NOT IN ('reconciled', 'closed')
    OR (reconciled_by IS NOT NULL AND reconciled_at IS NOT NULL AND (
        (reconcile_method = 'custodian_data' AND length(btrim(COALESCE(reconcile_reference, ''))) > 0
         AND cardinality(reconciled_transaction_ids) > 0)
        OR (reconcile_method = 'manual' AND length(btrim(COALESCE(reconcile_note, ''))) > 0)))),
  -- a MANUAL reconciliation is confirmed by a different person from the one who sent it
  CONSTRAINT payment_instructions_reconciler_chk CHECK (
    reconcile_method IS DISTINCT FROM 'manual' OR reconciled_by IS DISTINCT FROM sent_by),
  CONSTRAINT payment_instructions_closed_chk CHECK (status <> 'closed' OR (closed_by IS NOT NULL AND closed_at IS NOT NULL)),
  CONSTRAINT payment_instructions_cancelled_chk CHECK (
    status <> 'cancelled' OR (cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL
                              AND length(btrim(COALESCE(cancel_reason, ''))) > 0)),
  CONSTRAINT payment_instructions_expired_chk CHECK (
    status <> 'expired' OR (expired_at IS NOT NULL AND length(btrim(COALESCE(expiry_reason, ''))) > 0)),
  CONSTRAINT payment_instructions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS payment_instructions_org_idx
  ON public.payment_instructions (org_id, status) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS payment_instructions_household_idx
  ON public.payment_instructions (household_id) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS payment_instructions_source_idx
  ON public.payment_instructions (source_account_id) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS payment_instructions_task_live_uq
  ON public.payment_instructions (approval_task_id) WHERE system_to IS NULL AND approval_task_id IS NOT NULL;

-- SAME OWNER, in the database (the service refuses first, with the same words).
CREATE OR REPLACE FUNCTION public.payment_instructions_same_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  s record;
  d record;
  s_owners uuid[];
  d_owners uuid[];
BEGIN
  IF NEW.system_to IS NOT NULL THEN
    RETURN NEW;   -- an archived copy of a row that already passed
  END IF;
  SELECT id, org_id, household_id, primary_entity_id INTO s FROM public.accounts
   WHERE id = NEW.source_account_id AND system_to IS NULL AND valid_to IS NULL;
  SELECT id, org_id, household_id, primary_entity_id INTO d FROM public.accounts
   WHERE id = NEW.destination_account_id AND system_to IS NULL AND valid_to IS NULL;
  IF s.id IS NULL OR d.id IS NULL OR s.org_id <> NEW.org_id OR d.org_id <> NEW.org_id THEN
    RAISE EXCEPTION 'payment instruction: both accounts must be live accounts of this organization'
      USING ERRCODE = '23514';
  END IF;
  IF s.household_id IS DISTINCT FROM NEW.household_id OR d.household_id IS DISTINCT FROM NEW.household_id THEN
    RAISE EXCEPTION 'third-party payments come later: both accounts must belong to this household'
      USING ERRCODE = '23514';
  END IF;
  SELECT COALESCE(array_agg(DISTINCT entity_id ORDER BY entity_id), '{}') INTO s_owners FROM public.account_owners
   WHERE account_id = s.id AND valid_to IS NULL AND system_to IS NULL;
  SELECT COALESCE(array_agg(DISTINCT entity_id ORDER BY entity_id), '{}') INTO d_owners FROM public.account_owners
   WHERE account_id = d.id AND valid_to IS NULL AND system_to IS NULL;
  IF s.primary_entity_id IS DISTINCT FROM d.primary_entity_id OR s_owners IS DISTINCT FROM d_owners THEN
    RAISE EXCEPTION 'third-party payments come later: the two accounts must have the same owner'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS payment_instructions_same_owner_trg ON public.payment_instructions;
CREATE TRIGGER payment_instructions_same_owner_trg BEFORE INSERT OR UPDATE ON public.payment_instructions
  FOR EACH ROW EXECUTE FUNCTION public.payment_instructions_same_owner();

-- Status graph + "terms are frozen once submitted", in the database (the service enforces it first).
CREATE OR REPLACE FUNCTION public.payment_instructions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'maker_ids', 'approval_task_id', 'approved_by', 'approved_at', 'adapter',
    'sent_by', 'sent_at', 'sent_channel', 'sent_note', 'external_ref', 'consent_requested_at', 'acknowledged_by',
    'acknowledged_at', 'settled_by', 'settled_at', 'settled_on', 'provisional_until', 'custodian_rejected_reason',
    'return_code', 'return_reason', 'returned_by', 'returned_at', 'reconciled_by', 'reconciled_at',
    'reconcile_method', 'reconcile_reference', 'reconcile_note', 'reconciled_transaction_ids', 'closed_by',
    'closed_at', 'cancelled_by', 'cancelled_at', 'cancel_reason', 'expired_at', 'expiry_reason', 'updated_at',
    'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.payments_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'payment instructions are never deleted (cancel one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived payment instruction state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('closed', 'cancelled', 'expired', 'rejected', 'returned') THEN
    RAISE EXCEPTION 'a % payment instruction is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live payment instruction keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' AND (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'payment instruction terms are frozen once submitted (status %)', OLD.status
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>cancelled', 'draft>expired',
      'pending_approval>approved', 'pending_approval>draft', 'pending_approval>cancelled', 'pending_approval>expired',
      'approved>sent', 'approved>cancelled', 'approved>expired',
      'sent>awaiting_client_consent', 'sent>acknowledged', 'sent>rejected', 'sent>cancelled',
      'awaiting_client_consent>acknowledged', 'awaiting_client_consent>rejected',
      'awaiting_client_consent>cancelled', 'awaiting_client_consent>expired',
      'acknowledged>settled', 'acknowledged>rejected',
      'settled>reconciled', 'settled>returned',
      'reconciled>closed', 'reconciled>returned'])) THEN
    RAISE EXCEPTION 'illegal payment instruction transition % -> %', OLD.status, NEW.status
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS payment_instructions_guard_trg ON public.payment_instructions;
CREATE TRIGGER payment_instructions_guard_trg BEFORE UPDATE OR DELETE ON public.payment_instructions
  FOR EACH ROW EXECUTE FUNCTION public.payment_instructions_guard();

-- ── 5. Payment events (ORG, append-only) ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.payment_events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organizations(id),
  payment_id      uuid NOT NULL REFERENCES public.payment_instructions(id),
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
  CONSTRAINT payment_events_action_chk CHECK (action IN (
    'create', 'edit', 'submit', 'approve', 'reject', 'mark_sent', 'consent_requested', 'acknowledge', 'settle',
    'custodian_reject', 'return', 'reconcile', 'close', 'cancel', 'expire', 'sheet', 'unmatched_alert',
    'transition')),
  -- every action is a person's, except the logged sweeps (expiry, the unmatched-settlement alert). A status change
  -- that names no actor ('transition', the trigger's default) is refused here — no direct UPDATE moves money state.
  CONSTRAINT payment_events_human_chk CHECK (actor_id IS NOT NULL OR action IN ('expire', 'unmatched_alert')),
  CONSTRAINT payment_events_status_chk CHECK (to_status IN (
    'draft', 'pending_approval', 'approved', 'sent', 'awaiting_client_consent', 'acknowledged', 'settled',
    'reconciled', 'closed', 'rejected', 'returned', 'cancelled', 'expired')),
  CONSTRAINT payment_events_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS payment_events_payment_idx ON public.payment_events (payment_id, created_at);

CREATE OR REPLACE FUNCTION public.payment_events_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND public.payments_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'payment events are immutable (append-only)' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS payment_events_immutable_trg ON public.payment_events;
CREATE TRIGGER payment_events_immutable_trg BEFORE UPDATE OR DELETE ON public.payment_events
  FOR EACH ROW EXECUTE FUNCTION public.payment_events_immutable();

-- ONE event row per transition, written by the database. The service names the action, the actor, the detail and the
-- event id with set_config(…, true) (transaction-local); with none of them a status change records action
-- 'transition' with no actor — which payment_events_human_chk refuses, rolling the UPDATE back.
CREATE OR REPLACE FUNCTION public.payment_instructions_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_id uuid := COALESCE(NULLIF(current_setting('payments.event_id', true), '')::uuid, gen_random_uuid());
  v_action text := COALESCE(NULLIF(current_setting('payments.action', true), ''), 'transition');
  v_actor uuid := NULLIF(current_setting('payments.actor_id', true), '')::uuid;
  v_detail jsonb := COALESCE(NULLIF(current_setting('payments.detail', true), '')::jsonb, '{}'::jsonb);
BEGIN
  IF NEW.system_to IS NOT NULL THEN
    RETURN NULL;   -- an archived copy: not a transition
  END IF;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO public.payment_events (id, org_id, payment_id, action, from_status, to_status, actor_id, detail)
    VALUES (v_id, NEW.org_id, NEW.id, 'create', NULL, NEW.status, COALESCE(v_actor, NEW.maker_id), v_detail);
  ELSIF OLD.status IS DISTINCT FROM NEW.status THEN
    INSERT INTO public.payment_events (id, org_id, payment_id, action, from_status, to_status, actor_id, detail)
    VALUES (v_id, NEW.org_id, NEW.id, v_action, OLD.status, NEW.status, v_actor, v_detail);
  END IF;
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS payment_instructions_event_trg ON public.payment_instructions;
CREATE TRIGGER payment_instructions_event_trg AFTER INSERT OR UPDATE ON public.payment_instructions
  FOR EACH ROW EXECUTE FUNCTION public.payment_instructions_event();

-- ── 6. RLS — a policy for every operation each table takes ─────────────────────────────────────────────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['payment_instructions', 'payment_events'] LOOP
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
