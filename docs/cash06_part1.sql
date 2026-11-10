-- CASH06 part 1 — CASH RULES, stage A: standing rules that DRAFT payment instructions (cash07) for a person's approval.
-- Applied by apps/api/scripts/_apply_cash06.py --schema (operator DSN: DDL needs the table owner), in ONE transaction
-- (the apply script wraps it — no BEGIN / COMMIT here). Idempotent (IF NOT EXISTS / DROP … IF EXISTS + CREATE /
-- ON CONFLICT / NOT EXISTS).
--
-- WHY TWO NEW TABLES (docs/cash/CASH06_NOTES.md §4, Task 1):
--   public.cash_rules         the rule DEFINITION, versioned. workflow_triggers has no config / payload column (its 15
--                             columns are the schedule, the event type, the owner and the occurrence bookkeeping) and
--                             is updated in place by the scheduler's claim; a rule needs versions (an edit is a new
--                             draft version while the active one keeps running), maker-checker (S30 item type
--                             cash_rule, a FLOOR) and RLS for every operation. Each rule OWNS one workflow definition +
--                             one workflow trigger (created by services/cash_rules.py through the engine's own tables);
--                             the rule row points at both. SYSTEM-axis archive: the firings, the approval task,
--                             sod_decisions and the drafted payment instructions (purpose_ref) point at the ids.
--   public.cash_rule_firings  the idempotency CLAIM per (rule, period | incoming transaction), committed BEFORE any
--                             instruction is drafted, and the run history (firing → engine run → drafted instructions,
--                             or a hold with its reason). No existing key carries it: the scheduler's claim is per
--                             trigger occurrence, workflow_run_steps.idempotency_key per run step, an event delivery
--                             per event per workflow version — none survives a manual re-run of the same period.
--
-- Nothing here moves money. A firing calls services.payments.draft_and_submit: the instruction lands
-- pending_approval with its approval task; a DIFFERENT person (never the rule owner — the cash07 floor) approves it.
--
-- TEARDOWN GUC (documented): cash_rules.teardown = 'fixture', set LOCAL by a role that is NOT app_service
-- (public.cash_rules_teardown_allowed()) — the ONLY path past the delete guards of both tables. Verify scripts pass it to
-- scripts/_verify_cleanup.cleanup with payments.teardown and sod.teardown.
--
-- S30 FLOOR: item type cash_rule (floor class 'transfer' — a rule that drafts money movement IS money-movement
-- configuration) on the cash07 duties initiate_payment / approve_payment; the platform FLOOR rule + policy (never;
-- enforce_floor true). No org setting and no role relaxes it.
--
-- RLS: a policy for every operation on both tables, each reading the org GUC with NULLIF.
-- Writes no org row. Platform catalog rows only (the SoD item type and its two platform rules).

-- ── 0. The teardown guard ──────────────────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.cash_rules_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('cash_rules.teardown', true), '') = 'fixture' AND current_user <> 'app_service'
$$;

-- ── 1. SoD catalog: the cash_rule FLOOR item type (reuses cash07's duties) ─────────────────────────────────────────
INSERT INTO public.sod_item_types (item_type, label, description, maker_duty, checker_duty, floor_class,
                                   self_approval_capable, pre_s30_rule, site, sort_order) VALUES
  ('cash_rule', 'Cash rule', 'A standing cash rule that drafts payment instructions between a family''s own accounts (cash_rules, CASH06): creating, editing, activating or resuming one. A FLOOR item — money-movement configuration: no self-approval, ever; no organization setting and no role relaxes it, Super Admin included.', 'initiate_payment', 'approve_payment', 'transfer', false, 'maker_ne_checker', 'services/cash_rules.py', 142)
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
 WHERE t.item_type = 'cash_rule'
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind IN ('floor', 'default'));

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                              enforce_floor, description, status, version_no)
SELECT 'policy:' || t.item_type, 'platform', NULL, 'policy', t.item_type, 'same_item', 'block', 'never', true,
       'Platform policy for ' || lower(t.label) || ': a different person approves, always (CASH06 floor).', 'active', 1
  FROM public.sod_item_types t
 WHERE t.item_type = 'cash_rule'
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind = 'policy');

-- ── 2. A drafted instruction names its rule (payment_instructions.purpose_ref_type 'cash_rule') ───────────────────
ALTER TABLE public.payment_instructions DROP CONSTRAINT IF EXISTS payment_instructions_purpose_ref_chk;
ALTER TABLE public.payment_instructions ADD CONSTRAINT payment_instructions_purpose_ref_chk CHECK (
  (purpose_ref_type IS NULL AND purpose_ref_id IS NULL)
  OR (purpose_ref_type IN ('cash_obligation', 'cash_proposal', 'cash_rule') AND purpose_ref_id IS NOT NULL));

-- ── 3. Cash rules (ORG, versioned, system-axis) ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.cash_rules (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL REFERENCES public.organizations(id),
  rule_id                 uuid NOT NULL,                 -- the rule's stable identity across its versions
  version_no              integer NOT NULL DEFAULT 1,
  household_id            uuid NOT NULL REFERENCES public.households(id),
  name                    text NOT NULL,
  rule_type               text NOT NULL,
  source_account_id       uuid NOT NULL REFERENCES public.accounts(id),
  destinations            jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{account_id, pct | amount}] (same owner only)
  basis                   text,
  condition               text,
  target_amount           numeric,
  supporting_account_id   uuid REFERENCES public.accounts(id),
  frequency               text NOT NULL,
  day_of_week             integer,
  day_of_month            integer,
  rail                    text NOT NULL,
  cap_per_firing          numeric NOT NULL,
  max_per_day             integer NOT NULL DEFAULT 1,
  stage                   text NOT NULL DEFAULT 'A',
  status                  text NOT NULL DEFAULT 'draft',
  pending_action          text,
  resume_requested_by     uuid REFERENCES public.users(id),
  resume_requested_at     timestamptz,
  owner_id                uuid NOT NULL REFERENCES public.users(id),
  maker_ids               uuid[] NOT NULL,
  approval_task_id        uuid,
  submitted_by            uuid REFERENCES public.users(id),
  submitted_at            timestamptz,
  approved_by             uuid REFERENCES public.users(id),
  approved_at             timestamptz,
  decision_note           text,
  rejected_by             uuid REFERENCES public.users(id),
  rejected_at             timestamptz,
  reject_reason           text,
  paused_by               uuid REFERENCES public.users(id),
  paused_at               timestamptz,
  pause_reason            text,
  retired_by              uuid REFERENCES public.users(id),
  retired_at              timestamptz,
  retire_reason           text,
  superseded_by           uuid,
  workflow_definition_id  uuid REFERENCES public.workflow_definitions(id),
  workflow_version_id     uuid REFERENCES public.workflow_versions(id),
  trigger_id              uuid REFERENCES public.workflow_triggers(id),
  reason                  text,
  created_by              uuid REFERENCES public.users(id),
  archived_from_id        uuid REFERENCES public.cash_rules(id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT cash_rules_type_chk CHECK (rule_type IN ('distribute', 'maintain_target', 'recurring',
                                                       'redirect_incoming')),
  CONSTRAINT cash_rules_basis_chk CHECK (basis IS NULL OR basis IN ('pct_balance', 'fixed', 'pct_incoming')),
  CONSTRAINT cash_rules_condition_chk CHECK (condition IS NULL OR condition IN ('below', 'above')),
  CONSTRAINT cash_rules_frequency_chk CHECK (frequency IN ('daily', 'business_days', 'weekly', 'monthly',
                                                           'on_incoming')),
  CONSTRAINT cash_rules_dow_chk CHECK (day_of_week IS NULL OR day_of_week BETWEEN 1 AND 5),
  CONSTRAINT cash_rules_dom_chk CHECK (day_of_month IS NULL OR day_of_month BETWEEN 1 AND 28),
  CONSTRAINT cash_rules_rail_chk CHECK (rail IN ('ach', 'wire', 'journal', 'check')),
  CONSTRAINT cash_rules_cap_chk CHECK (cap_per_firing >= 0.01),
  CONSTRAINT cash_rules_per_day_chk CHECK (max_per_day BETWEEN 1 AND 20),
  CONSTRAINT cash_rules_stage_chk CHECK (stage = 'A'),          -- stages B / C need Altruist write + counsel
  CONSTRAINT cash_rules_status_chk CHECK (status IN ('draft', 'pending_approval', 'active', 'paused', 'superseded',
                                                     'retired')),
  -- a version awaiting approval to ACTIVATE is pending_approval; a paused version awaiting approval to RESUME stays
  -- paused (it never runs while the request is open) with pending_action 'resume' and its task
  CONSTRAINT cash_rules_pending_action_chk CHECK (
    (status = 'pending_approval' AND pending_action = 'activate' AND approval_task_id IS NOT NULL)
    OR (status = 'paused' AND pending_action = 'resume' AND approval_task_id IS NOT NULL
        AND resume_requested_by IS NOT NULL)
    OR (status <> 'pending_approval' AND pending_action IS NULL)),
  CONSTRAINT cash_rules_resume_checker_chk CHECK (status <> 'active' OR resume_requested_by IS NULL
                                                  OR approved_by IS DISTINCT FROM resume_requested_by),
  CONSTRAINT cash_rules_name_chk CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  CONSTRAINT cash_rules_destinations_chk CHECK (jsonb_typeof(destinations) = 'array'
                                                AND jsonb_array_length(destinations) <= 5),
  -- the shape of each type (the service checks every rule first, with reasons)
  CONSTRAINT cash_rules_shape_chk CHECK (
    (rule_type = 'distribute' AND basis IS NOT NULL AND jsonb_array_length(destinations) >= 1
       AND ((basis = 'pct_incoming') = (frequency = 'on_incoming')))
    OR (rule_type = 'maintain_target' AND condition IS NOT NULL AND target_amount IS NOT NULL AND target_amount >= 0
       AND supporting_account_id IS NOT NULL AND supporting_account_id <> source_account_id
       AND frequency <> 'on_incoming' AND jsonb_array_length(destinations) = 0)
    OR (rule_type = 'recurring' AND basis = 'fixed' AND jsonb_array_length(destinations) = 1
       AND frequency <> 'on_incoming')
    OR (rule_type = 'redirect_incoming' AND basis = 'pct_incoming' AND jsonb_array_length(destinations) = 1
       AND frequency = 'on_incoming')),
  CONSTRAINT cash_rules_schedule_chk CHECK ((frequency = 'weekly') = (day_of_week IS NOT NULL)
                                            AND (frequency = 'monthly') = (day_of_month IS NOT NULL)),
  -- the owner is a maker of every version; nobody who made a version approves it (the floor, in the database)
  CONSTRAINT cash_rules_owner_maker_chk CHECK (owner_id = ANY (maker_ids)),
  CONSTRAINT cash_rules_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT cash_rules_active_chk CHECK (status NOT IN ('active', 'paused', 'superseded')
                                          OR (approved_by IS NOT NULL AND approved_at IS NOT NULL
                                              AND trigger_id IS NOT NULL)),
  CONSTRAINT cash_rules_paused_chk CHECK (status <> 'paused' OR (paused_by IS NOT NULL AND pause_reason IS NOT NULL)),
  CONSTRAINT cash_rules_retired_chk CHECK (status <> 'retired' OR (retired_by IS NOT NULL AND retire_reason IS NOT NULL)),
  CONSTRAINT cash_rules_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS cash_rules_org_idx ON public.cash_rules (org_id, status) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS cash_rules_household_idx ON public.cash_rules (household_id) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS cash_rules_trigger_idx ON public.cash_rules (trigger_id) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cash_rules_version_live_uq
  ON public.cash_rules (rule_id, version_no) WHERE system_to IS NULL;
-- at most ONE running version (active or paused) and ONE open version (draft or awaiting approval) per rule
CREATE UNIQUE INDEX IF NOT EXISTS cash_rules_running_uq
  ON public.cash_rules (rule_id) WHERE system_to IS NULL AND status IN ('active', 'paused');
CREATE UNIQUE INDEX IF NOT EXISTS cash_rules_open_uq
  ON public.cash_rules (rule_id) WHERE system_to IS NULL AND status IN ('draft', 'pending_approval');
CREATE UNIQUE INDEX IF NOT EXISTS cash_rules_task_live_uq
  ON public.cash_rules (approval_task_id) WHERE system_to IS NULL AND approval_task_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.cash_rules_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.cash_rules_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'cash rules are never deleted (retire one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived cash rule version is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('superseded', 'retired') THEN
    RAISE EXCEPTION 'a % cash rule version is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
     OR NEW.rule_id <> OLD.rule_id OR NEW.version_no <> OLD.version_no OR NEW.owner_id <> OLD.owner_id THEN
    RAISE EXCEPTION 'the live cash rule version keeps its id, rule, version and owner; archive by copying it'
      USING ERRCODE = '42501';
  END IF;
  -- the terms are frozen once submitted: only a draft's terms change
  IF OLD.status <> 'draft' AND (
       NEW.rule_type, NEW.source_account_id, NEW.destinations, NEW.basis, NEW.condition, NEW.target_amount,
       NEW.supporting_account_id, NEW.frequency, NEW.day_of_week, NEW.day_of_month, NEW.rail, NEW.cap_per_firing,
       NEW.max_per_day, NEW.household_id, NEW.name)
     IS DISTINCT FROM (
       OLD.rule_type, OLD.source_account_id, OLD.destinations, OLD.basis, OLD.condition, OLD.target_amount,
       OLD.supporting_account_id, OLD.frequency, OLD.day_of_week, OLD.day_of_month, OLD.rail, OLD.cap_per_firing,
       OLD.max_per_day, OLD.household_id, OLD.name) THEN
    RAISE EXCEPTION 'cash rule terms are frozen once submitted (status %)', OLD.status USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> NEW.status AND NOT ((OLD.status || '>' || NEW.status) = ANY (ARRAY[
      'draft>pending_approval', 'draft>retired',
      'pending_approval>active', 'pending_approval>draft',
      'active>paused', 'active>superseded', 'active>retired',
      'paused>active', 'paused>superseded', 'paused>retired'])) THEN
    RAISE EXCEPTION 'illegal cash rule transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cash_rules_guard_trg ON public.cash_rules;
CREATE TRIGGER cash_rules_guard_trg BEFORE UPDATE OR DELETE ON public.cash_rules
  FOR EACH ROW EXECUTE FUNCTION public.cash_rules_guard();

-- SAME OWNER, in the database: every destination (and a target rule's supporting account) is a live account of this
-- org, in the rule's household, owned by the source account's entity with the same live account_owners set.
CREATE OR REPLACE FUNCTION public.cash_rules_same_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  s record;
  d record;
  s_owners uuid[];
  d_owners uuid[];
  other uuid;
BEGIN
  IF NEW.system_to IS NOT NULL THEN
    RETURN NEW;   -- an archived copy of a row that already passed
  END IF;
  SELECT id, org_id, household_id, primary_entity_id INTO s FROM public.accounts
   WHERE id = NEW.source_account_id AND system_to IS NULL AND valid_to IS NULL;
  IF s.id IS NULL OR s.org_id <> NEW.org_id OR s.household_id IS DISTINCT FROM NEW.household_id THEN
    RAISE EXCEPTION 'cash rule: the source must be a live account of this household' USING ERRCODE = '23514';
  END IF;
  SELECT COALESCE(array_agg(DISTINCT entity_id ORDER BY entity_id), '{}') INTO s_owners FROM public.account_owners
   WHERE account_id = s.id AND valid_to IS NULL AND system_to IS NULL;
  FOR other IN
    SELECT (x->>'account_id')::uuid FROM jsonb_array_elements(NEW.destinations) x
    UNION ALL SELECT NEW.supporting_account_id WHERE NEW.supporting_account_id IS NOT NULL
  LOOP
    SELECT id, org_id, household_id, primary_entity_id INTO d FROM public.accounts
     WHERE id = other AND system_to IS NULL AND valid_to IS NULL;
    IF d.id IS NULL OR d.org_id <> NEW.org_id OR d.id = s.id THEN
      RAISE EXCEPTION 'cash rule: every destination is another live account of this organization'
        USING ERRCODE = '23514';
    END IF;
    SELECT COALESCE(array_agg(DISTINCT entity_id ORDER BY entity_id), '{}') INTO d_owners FROM public.account_owners
     WHERE account_id = d.id AND valid_to IS NULL AND system_to IS NULL;
    IF d.household_id IS DISTINCT FROM NEW.household_id OR s.primary_entity_id IS DISTINCT FROM d.primary_entity_id
       OR s_owners IS DISTINCT FROM d_owners THEN
      RAISE EXCEPTION 'third-party payments come later: a rule''s destinations must have the same owner'
        USING ERRCODE = '23514';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cash_rules_same_owner_trg ON public.cash_rules;
CREATE TRIGGER cash_rules_same_owner_trg BEFORE INSERT OR UPDATE ON public.cash_rules
  FOR EACH ROW EXECUTE FUNCTION public.cash_rules_same_owner();

-- ── 4. Cash rule firings (ORG, the idempotency claim + run history) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.cash_rule_firings (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  rule_id           uuid NOT NULL,
  rule_version_id   uuid NOT NULL REFERENCES public.cash_rules(id),
  period_key        text NOT NULL,
  trigger_type      text NOT NULL,
  workflow_run_id   uuid,
  owner_id          uuid NOT NULL REFERENCES public.users(id),
  evaluated_on      date NOT NULL,
  outcome           text NOT NULL DEFAULT 'firing',
  reason            text,
  drafts            jsonb NOT NULL DEFAULT '[]'::jsonb,
  holds             jsonb NOT NULL DEFAULT '[]'::jsonb,
  instruction_ids   uuid[] NOT NULL DEFAULT '{}'::uuid[],
  created_at        timestamptz NOT NULL DEFAULT now(),
  completed_at      timestamptz,
  CONSTRAINT cash_rule_firings_outcome_chk CHECK (outcome IN ('firing', 'drafted', 'held', 'partly_held', 'skipped')),
  CONSTRAINT cash_rule_firings_trigger_chk CHECK (trigger_type IN ('scheduled', 'event', 'manual')),
  CONSTRAINT cash_rule_firings_period_chk CHECK (period_key ~ '^(d:\d{4}-\d{2}-\d{2}|w:\d{4}-W\d{2}|m:\d{4}-\d{2}|txn:[0-9a-f-]{36})$'),
  CONSTRAINT cash_rule_firings_done_chk CHECK ((outcome = 'firing') = (completed_at IS NULL)),
  CONSTRAINT cash_rule_firings_reason_chk CHECK (outcome IN ('firing', 'drafted') OR length(btrim(COALESCE(reason, ''))) > 0),
  CONSTRAINT cash_rule_firings_json_chk CHECK (jsonb_typeof(drafts) = 'array' AND jsonb_typeof(holds) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS cash_rule_firings_period_uq ON public.cash_rule_firings (org_id, rule_id, period_key);
CREATE INDEX IF NOT EXISTS cash_rule_firings_rule_idx ON public.cash_rule_firings (rule_id, created_at DESC);

-- a firing is claimed once ('firing'), finished once (one UPDATE to its outcome); never edited after, never deleted
CREATE OR REPLACE FUNCTION public.cash_rule_firings_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.cash_rules_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'cash rule firings are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.outcome <> 'firing' THEN
    RAISE EXCEPTION 'a finished cash rule firing is immutable' USING ERRCODE = '42501';
  END IF;
  IF (NEW.id, NEW.org_id, NEW.rule_id, NEW.rule_version_id, NEW.period_key, NEW.trigger_type, NEW.owner_id,
      NEW.evaluated_on, NEW.created_at) IS DISTINCT FROM
     (OLD.id, OLD.org_id, OLD.rule_id, OLD.rule_version_id, OLD.period_key, OLD.trigger_type, OLD.owner_id,
      OLD.evaluated_on, OLD.created_at) THEN
    RAISE EXCEPTION 'a cash rule firing''s claim is immutable — only its outcome is recorded' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cash_rule_firings_guard_trg ON public.cash_rule_firings;
CREATE TRIGGER cash_rule_firings_guard_trg BEFORE UPDATE OR DELETE ON public.cash_rule_firings
  FOR EACH ROW EXECUTE FUNCTION public.cash_rule_firings_guard();

-- ── 5. RLS — a policy for every operation each table takes ─────────────────────────────────────────────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['cash_rules', 'cash_rule_firings'] LOOP
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
