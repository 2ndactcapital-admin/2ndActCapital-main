-- REP7B — Replication Studio, sprint 7b: account AUTHORITY and CUSTODY as two
-- separate settings. A household-level authority (read_only | advise |
-- managed) the adviser sets, overridable per account, approved by a second
-- person; a custody setting per account (integrated | other_custodian |
-- outside); rep7's single held-away guard re-keyed on authority; advise mode
-- (one-off proposals → member / other-adviser instructions + letter); managed
-- accounts at another custodian → MANUAL DVP settlement instructions (block
-- trading in an omnibus account, allocation and automated DVP are rep9).
--
-- Applied by apps/api/scripts/_apply_rep7b.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- THE MIGRATION WRITES NO ROWS (Task 1c). An account with no approved
-- settings row takes the migrated default, computed at read time by
-- services/authority.py:
--     is_held_away = true   → authority read_only, custody outside
--     is_held_away = false  → authority managed,   custody integrated
-- which is exactly rep7's behaviour (held-away = never traded; every other
-- account as before). Every live account in the database at the time of this
-- sprint sits in a leftover REP2 fixture org (is_test); writing a settings row
-- for one would put an FK in front of that script's own teardown.
--
-- BI-TEMPORAL (Rule 3)
--   household_authority, account_authority_settings — SYSTEM axis for the
--     decision (draft → approved / rejected on the same id: the approval task
--     points at it; the state before is archived as a copy), VALID axis for
--     supersession (a newer approved change closes the previous approved row's
--     valid_to — never deleted, so the history is every approved row).
--   instruction_settlements — VALID axis, written once with its instruction,
--     only ever closed.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- THE CUSTODY CLIFF. Nothing here trades or moves money. A DVP settlement
-- instruction is a document a person sends; nothing calls a custodian API.

-- ── 0. Permissions (org_admin by default; rbac.ensure_org_admin_role too) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_authority', 'authority', 'view'),
  ('manage_authority', 'authority', 'manage'),
  ('approve_authority', 'authority', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_authority', 'manage_authority', 'approve_authority')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. Household authority (the adviser's household-level option) ─────────
CREATE TABLE IF NOT EXISTS public.household_authority (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  household_id      uuid NOT NULL REFERENCES public.households(id),
  authority         text NOT NULL,
  effective_from    date NOT NULL,
  reason            text NOT NULL,
  status            text NOT NULL DEFAULT 'draft',
  maker_id          uuid NOT NULL REFERENCES public.users(id),
  approved_by       uuid REFERENCES public.users(id),
  approval_task_id  uuid REFERENCES public.workflow_run_steps(id),
  decided_at        timestamptz,
  decision_note     text,
  superseded_reason text,
  archived_from_id  uuid REFERENCES public.household_authority(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT household_authority_authority_chk CHECK (authority IN ('read_only', 'advise', 'managed')),
  CONSTRAINT household_authority_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT household_authority_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT household_authority_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT household_authority_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                    OR system_to IS NOT NULL),
  CONSTRAINT household_authority_decided_chk CHECK (status = 'draft'
                                                    OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT household_authority_rejected_chk CHECK (status <> 'rejected'
                                                     OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT household_authority_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT household_authority_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one live approved row per household; one change awaiting approval at a time
CREATE UNIQUE INDEX IF NOT EXISTS household_authority_live_uq ON public.household_authority (household_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS household_authority_pending_uq ON public.household_authority (household_id)
  WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS household_authority_task_uq ON public.household_authority (approval_task_id)
  WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

-- ── 2. Account authority override + custody ───────────────────────────────
-- A separate versioned table, not columns on public.accounts: the custody
-- importer restates accounts rows on the valid axis from the custodian file,
-- and a setting a person approved must not depend on the importer carrying it.
CREATE TABLE IF NOT EXISTS public.account_authority_settings (
  id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                        uuid NOT NULL REFERENCES public.organizations(id),
  account_id                    uuid NOT NULL REFERENCES public.accounts(id),
  authority_override            text,          -- NULL = the household's authority
  custody                       text NOT NULL,
  receiving_custodian_name      text,
  receiving_participant_number  text,          -- DTC / participant number
  receiving_agent_bank          text,
  receiving_account_masked      text,          -- the account at the receiving custodian, MASKED only
  contra_broker                 text,          -- the executing / contra broker
  effective_from                date NOT NULL,
  reason                        text NOT NULL,
  status                        text NOT NULL DEFAULT 'draft',
  maker_id                      uuid NOT NULL REFERENCES public.users(id),
  approved_by                   uuid REFERENCES public.users(id),
  approval_task_id              uuid REFERENCES public.workflow_run_steps(id),
  decided_at                    timestamptz,
  decision_note                 text,
  superseded_reason             text,
  archived_from_id              uuid REFERENCES public.account_authority_settings(id),
  created_at                    timestamptz NOT NULL DEFAULT now(),
  valid_from                    timestamptz NOT NULL DEFAULT now(),
  valid_to                      timestamptz,
  system_from                   timestamptz NOT NULL DEFAULT now(),
  system_to                     timestamptz,
  CONSTRAINT account_authority_settings_authority_chk CHECK (authority_override IS NULL
                                                             OR authority_override IN ('read_only', 'advise', 'managed')),
  CONSTRAINT account_authority_settings_custody_chk CHECK (custody IN ('integrated', 'other_custodian', 'outside')),
  -- managed needs a custodian: managed + outside is refused as a setting
  CONSTRAINT account_authority_settings_managed_outside_chk CHECK (NOT (authority_override = 'managed'
                                                                        AND custody = 'outside')),
  -- receiving-custodian details exist only for an account at another custodian, and there the name and the
  -- participant number are required (a DVP settlement instruction cannot be written without them)
  CONSTRAINT account_authority_settings_receiving_chk CHECK (
    (custody = 'other_custodian' AND length(btrim(COALESCE(receiving_custodian_name, ''))) > 0
       AND receiving_participant_number IS NOT NULL)
    OR (custody <> 'other_custodian' AND receiving_custodian_name IS NULL AND receiving_participant_number IS NULL
        AND receiving_agent_bank IS NULL AND receiving_account_masked IS NULL AND contra_broker IS NULL)),
  CONSTRAINT account_authority_settings_participant_chk CHECK (receiving_participant_number IS NULL
                                                               OR receiving_participant_number ~ '^[0-9]{1,5}$'),
  CONSTRAINT account_authority_settings_masked_chk CHECK (receiving_account_masked IS NULL
                                                          OR receiving_account_masked ~ '^\*{2,}[0-9A-Za-z]{0,4}$'),
  CONSTRAINT account_authority_settings_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT account_authority_settings_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT account_authority_settings_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT account_authority_settings_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                           OR system_to IS NOT NULL),
  CONSTRAINT account_authority_settings_decided_chk CHECK (status = 'draft'
                                                           OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT account_authority_settings_rejected_chk CHECK (status <> 'rejected'
                                                            OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT account_authority_settings_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT account_authority_settings_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_authority_settings_live_uq ON public.account_authority_settings (account_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_authority_settings_pending_uq
  ON public.account_authority_settings (account_id) WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_authority_settings_task_uq
  ON public.account_authority_settings (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

-- ── 3. DVP settlement detail on an instruction (managed, other custodian) ──
CREATE TABLE IF NOT EXISTS public.instruction_settlements (
  id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                        uuid NOT NULL REFERENCES public.organizations(id),
  instruction_id                uuid NOT NULL REFERENCES public.execution_instructions(id),
  account_id                    uuid NOT NULL REFERENCES public.accounts(id),
  settings_id                   uuid REFERENCES public.account_authority_settings(id),
  settlement_type               text NOT NULL DEFAULT 'dvp',
  authority                     text NOT NULL,
  custody                       text NOT NULL,
  executor_type                 text NOT NULL,
  receiving_custodian_name      text NOT NULL,
  receiving_participant_number  text NOT NULL,
  receiving_agent_bank          text,
  receiving_account_masked      text,
  contra_broker                 text,
  trade_date                    date NOT NULL,
  settlement_date               date NOT NULL,
  calendar_basis                text NOT NULL,
  created_by                    uuid NOT NULL REFERENCES public.users(id),
  created_at                    timestamptz NOT NULL DEFAULT now(),
  valid_from                    timestamptz NOT NULL DEFAULT now(),
  valid_to                      timestamptz,
  system_from                   timestamptz NOT NULL DEFAULT now(),
  system_to                     timestamptz,
  CONSTRAINT instruction_settlements_type_chk CHECK (settlement_type = 'dvp'),
  -- advise / read_only never carry settlement detail: only a MANAGED account at ANOTHER custodian settles DVP
  CONSTRAINT instruction_settlements_authority_chk CHECK (authority = 'managed' AND custody = 'other_custodian'),
  CONSTRAINT instruction_settlements_executor_chk CHECK (executor_type IN ('broker', 'trader')),
  CONSTRAINT instruction_settlements_date_chk CHECK (settlement_date > trade_date),
  CONSTRAINT instruction_settlements_calendar_chk CHECK (calendar_basis IN ('weekends_only')),
  CONSTRAINT instruction_settlements_participant_chk CHECK (receiving_participant_number ~ '^[0-9]{1,5}$'),
  CONSTRAINT instruction_settlements_masked_chk CHECK (receiving_account_masked IS NULL
                                                       OR receiving_account_masked ~ '^\*{2,}[0-9A-Za-z]{0,4}$'),
  CONSTRAINT instruction_settlements_name_chk CHECK (length(btrim(receiving_custodian_name)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS instruction_settlements_live_uq ON public.instruction_settlements (instruction_id)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 4. One-off proposals (advise mode): a manual proposal with no model ───
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS proposal_kind text NOT NULL DEFAULT 'model';
ALTER TABLE public.rebalance_proposals ALTER COLUMN link_id DROP NOT NULL;
ALTER TABLE public.rebalance_proposals ALTER COLUMN model_id DROP NOT NULL;
ALTER TABLE public.rebalance_proposals ALTER COLUMN model_version_id DROP NOT NULL;
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_kind_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_kind_chk CHECK (
  (proposal_kind = 'model' AND link_id IS NOT NULL AND model_id IS NOT NULL AND model_version_id IS NOT NULL)
  OR (proposal_kind = 'one_off' AND trigger = 'manual' AND maker_kind = 'user'
      AND link_id IS NULL AND model_id IS NULL AND model_version_id IS NULL));

-- ── 5. Guards ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.authority_change_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'approved_by', 'approval_task_id', 'decided_at', 'decision_note',
    'system_from'];
  closing text[] := ARRAY['valid_to', 'superseded_reason'];
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived % state is immutable', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live % row keeps its id; archive by copying it', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  -- an approved row is only ever closed (superseded by a newer approved change)
  IF OLD.status = 'approved' THEN
    IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL AND (to_jsonb(NEW) - closing) = (to_jsonb(OLD) - closing) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'an approved % row is only ever closed by a newer approved change', TG_TABLE_NAME
      USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'rejected' THEN
    RAISE EXCEPTION 'a rejected % row is immutable', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  -- a draft is decided, never edited: a corrected request is a new one
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'a % request is fixed when it is made — make a new one', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF NEW.status NOT IN ('draft', 'approved', 'rejected') OR (OLD.status = 'draft' AND NEW.status = 'draft'
      AND NEW.approved_by IS NOT NULL) THEN
    RAISE EXCEPTION 'illegal % transition', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS household_authority_guard_trg ON public.household_authority;
CREATE TRIGGER household_authority_guard_trg BEFORE UPDATE OR DELETE ON public.household_authority
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();
DROP TRIGGER IF EXISTS account_authority_settings_guard_trg ON public.account_authority_settings;
CREATE TRIGGER account_authority_settings_guard_trg BEFORE UPDATE OR DELETE ON public.account_authority_settings
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();

CREATE OR REPLACE FUNCTION public.instruction_settlements_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  ins record;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'settlement instructions are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT account_id, executor_type, org_id INTO ins FROM public.execution_instructions
      WHERE id = NEW.instruction_id AND system_to IS NULL;
    IF ins IS NULL OR ins.account_id <> NEW.account_id OR ins.org_id <> NEW.org_id
       OR ins.executor_type <> NEW.executor_type THEN
      RAISE EXCEPTION 'a settlement instruction names its instruction''s own account, org and executor'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a settlement instruction is written once and only ever closed' USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS instruction_settlements_guard_trg ON public.instruction_settlements;
CREATE TRIGGER instruction_settlements_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.instruction_settlements
  FOR EACH ROW EXECUTE FUNCTION public.instruction_settlements_guard();

-- ── 6. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['household_authority', 'account_authority_settings', 'instruction_settlements'] LOOP
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
