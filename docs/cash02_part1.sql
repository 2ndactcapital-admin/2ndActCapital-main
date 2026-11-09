-- CASH02 part 1 — cash POLICIES (the three sleeves' boundaries, per household or per entity), their SoD item type and
-- permission, and the move of the minimum coverage into the policy.
--
-- WHY A NEW TABLE (docs/cash/CASH02_NOTES.md §8): no existing table carries a versioned, maker-checker, one-active-per-
-- scope cash policy. org_settings is updated in place and keyed by org only; cash_coverage_minimums (cash01) held one
-- number with no approval; household_authority / workflow_autonomy_settings are other subjects. The shape copies
-- household_authority (status, system-axis archive, approval task) and execution_instructions' maker_ids CHECK.
--
-- SLEEVE TARGETS ARE NOT STORED: services/cash_policy.py derives them at read time from (policy, cash01 schedule). What
-- the checker was asked to approve is the human task's own payload (derived as of submit) — no snapshot column.
--
-- BI-TEMPORAL (Rule 3). The row's id is referenced (the approval task's subject, sod_decisions.item_id), so a change
-- of status or a draft edit is a SYSTEM-axis archive: a copy of the live row is inserted with system_to = now() and
-- archived_from_id = the id, then the live row (same id) is updated with system_from = now(). The VALID axis is the
-- policy's time in force: valid_from = activation, valid_to = retirement. A new version is a new row (version_no + 1).
--
-- RLS: a policy for every operation, in this migration. Idempotent except the guarded DROP in §4.

-- ── 0. Permission (author AND approve; SoD makes the approver a different person) ────────────────────────────────
INSERT INTO public.permissions (name, resource, action) VALUES ('manage_cash_policy', 'cash_policy', 'manage')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM public.roles r
  JOIN public.permissions p ON p.name = 'manage_cash_policy'
 WHERE r.name IN ('org_admin', 'advisor', 'admin')
ON CONFLICT DO NOTHING;

-- ── 1. SoD catalog: the cash_policy item type (the S29B shape) ───────────────────────────────────────────────────
INSERT INTO public.sod_duties (duty_key, label, description, kind, permissions, sort_order) VALUES
  ('set_cash_policy', 'Draft a cash policy', 'Drafts or edits a household''s / entity''s cash policy (the Near-term / Reserve / Long-term sleeve boundaries, rungs, objective and minimum coverage) and submits it for approval.', 'make', '{manage_cash_policy}', 130),
  ('approve_cash_policy', 'Approve a cash policy', 'Decides a submitted cash policy; approving makes it the active policy of its household or entity.', 'check', '{manage_cash_policy}', 131)
ON CONFLICT (duty_key) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description, kind = EXCLUDED.kind,
  permissions = EXCLUDED.permissions, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_item_types (item_type, label, description, maker_duty, checker_duty, floor_class,
                                   self_approval_capable, pre_s30_rule, site, sort_order) VALUES
  ('cash_policy', 'Cash policy', 'A household''s or entity''s cash policy — sleeve boundaries in months of obligations, rung count, objective and minimum coverage (cash_policies). A target, never a trade.', 'set_cash_policy', 'approve_cash_policy', NULL, false, 'maker_ne_checker', 'services/cash_policy.py', 130)
ON CONFLICT (item_type) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description,
  maker_duty = EXCLUDED.maker_duty, checker_duty = EXCLUDED.checker_duty, floor_class = EXCLUDED.floor_class,
  self_approval_capable = EXCLUDED.self_approval_capable, pre_s30_rule = EXCLUDED.pre_s30_rule,
  site = EXCLUDED.site, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, maker_duty, checker_duty, scope,
                              severity, description, status, version_no)
SELECT 'default:' || t.item_type, 'platform', NULL, 'default', t.item_type, t.maker_duty, t.checker_duty,
       'same_item', 'block',
       'The same person may not ' || lower(m.label) || ' and ' || lower(c.label) || ' on the same '
         || lower(t.label) || '.', 'active', 1
  FROM public.sod_item_types t
  JOIN public.sod_duties m ON m.duty_key = t.maker_duty
  JOIN public.sod_duties c ON c.duty_key = t.checker_duty
 WHERE t.item_type = 'cash_policy'
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind IN ('floor', 'default'));

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                              enforce_floor, description, status, version_no)
SELECT 'policy:' || t.item_type, 'platform', NULL, 'policy', t.item_type, 'same_item', 'block', 'never', true,
       'Platform policy for ' || lower(t.label) || ': a different person decides (CASH02).', 'active', 1
  FROM public.sod_item_types t
 WHERE t.item_type = 'cash_policy'
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind = 'policy');

-- ── 2. The policy table ──────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.cash_policies (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  scope_type           text NOT NULL,
  household_id         uuid REFERENCES public.households(id),
  entity_id            uuid REFERENCES public.entities(id),
  version_no           integer NOT NULL,
  near_months          integer NOT NULL,
  reserve_months       integer NOT NULL,
  horizon_months       integer NOT NULL DEFAULT 24,
  rung_count           integer NOT NULL DEFAULT 8,
  buffer_months        numeric(5,2) NOT NULL DEFAULT 0,
  objective            text NOT NULL,
  objective_score      integer NOT NULL DEFAULT 3,
  preset               text NOT NULL,
  min_coverage_months  numeric(6,2) NOT NULL DEFAULT 6,
  status               text NOT NULL DEFAULT 'draft',
  maker_ids            uuid[] NOT NULL,
  created_by           uuid NOT NULL REFERENCES public.users(id),
  reason               text,
  questionnaire        jsonb,
  submitted_by         uuid REFERENCES public.users(id),
  submitted_at         timestamptz,
  approval_task_id     uuid REFERENCES public.workflow_run_steps(id),
  approved_by          uuid REFERENCES public.users(id),
  approved_at          timestamptz,
  decision_note        text,
  rejected_by          uuid REFERENCES public.users(id),
  rejected_at          timestamptz,
  reject_reason        text,
  retired_at           timestamptz,
  retired_reason       text,
  superseded_by        uuid REFERENCES public.cash_policies(id),
  archived_from_id     uuid REFERENCES public.cash_policies(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT cash_policies_scope_chk CHECK (
    (scope_type = 'household' AND household_id IS NOT NULL AND entity_id IS NULL)
    OR (scope_type = 'entity' AND entity_id IS NOT NULL AND household_id IS NULL)),
  -- two boundaries in months of obligations: Near-term [0, N), Reserve [N, R), Long-term [R, horizon)
  CONSTRAINT cash_policies_boundaries_chk CHECK (near_months >= 0 AND near_months <= reserve_months
                                                 AND reserve_months <= 120),
  CONSTRAINT cash_policies_horizon_chk CHECK (horizon_months BETWEEN 1 AND 120),
  CONSTRAINT cash_policies_rungs_chk CHECK (rung_count BETWEEN 1 AND 52),
  CONSTRAINT cash_policies_buffer_chk CHECK (buffer_months >= 0 AND buffer_months <= 12),
  CONSTRAINT cash_policies_min_chk CHECK (min_coverage_months >= 0 AND min_coverage_months <= 120),
  CONSTRAINT cash_policies_objective_chk CHECK (objective_score BETWEEN 1 AND 5 AND objective =
    CASE WHEN objective_score <= 2 THEN 'stability' WHEN objective_score = 3 THEN 'balanced' ELSE 'yield' END),
  CONSTRAINT cash_policies_preset_chk CHECK (preset IN ('stay_liquid', 'balanced', 'recommended', 'custom')),
  CONSTRAINT cash_policies_status_chk CHECK (status IN ('draft', 'pending_approval', 'active', 'retired', 'rejected')),
  CONSTRAINT cash_policies_version_chk CHECK (version_no >= 1),
  CONSTRAINT cash_policies_makers_chk CHECK (cardinality(maker_ids) >= 1 AND created_by = ANY (maker_ids)),
  -- maker-checker IN THE DATABASE (the execution-instruction constraint style): the approver is never a maker
  CONSTRAINT cash_policies_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT cash_policies_pending_chk CHECK (status <> 'pending_approval'
    OR (approval_task_id IS NOT NULL AND submitted_at IS NOT NULL AND submitted_by IS NOT NULL)),
  CONSTRAINT cash_policies_approved_chk CHECK (status NOT IN ('active', 'retired')
    OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approval_task_id IS NOT NULL)),
  CONSTRAINT cash_policies_rejected_chk CHECK (status <> 'rejected'
    OR (rejected_by IS NOT NULL AND rejected_at IS NOT NULL AND length(btrim(COALESCE(reject_reason, ''))) > 0)),
  CONSTRAINT cash_policies_retired_chk CHECK (status <> 'retired' OR (retired_at IS NOT NULL AND valid_to IS NOT NULL)),
  CONSTRAINT cash_policies_live_valid_chk CHECK (system_to IS NOT NULL OR status = 'retired' OR valid_to IS NULL),
  CONSTRAINT cash_policies_archive_chk CHECK ((archived_from_id IS NULL) = (system_to IS NULL))
);

CREATE INDEX IF NOT EXISTS cash_policies_scope_idx ON public.cash_policies (org_id, scope_type, household_id, entity_id)
  WHERE system_to IS NULL;
-- ONE ACTIVE POLICY PER SCOPE
CREATE UNIQUE INDEX IF NOT EXISTS cash_policies_active_household_uq ON public.cash_policies (household_id)
  WHERE scope_type = 'household' AND status = 'active' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cash_policies_active_entity_uq ON public.cash_policies (entity_id)
  WHERE scope_type = 'entity' AND status = 'active' AND system_to IS NULL;
-- one OPEN version (draft or awaiting approval) per scope
CREATE UNIQUE INDEX IF NOT EXISTS cash_policies_open_household_uq ON public.cash_policies (household_id)
  WHERE scope_type = 'household' AND status IN ('draft', 'pending_approval') AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cash_policies_open_entity_uq ON public.cash_policies (entity_id)
  WHERE scope_type = 'entity' AND status IN ('draft', 'pending_approval') AND system_to IS NULL;
-- version numbers are unique per scope among live rows
CREATE UNIQUE INDEX IF NOT EXISTS cash_policies_version_uq ON public.cash_policies
  (scope_type, COALESCE(household_id, entity_id), version_no) WHERE system_to IS NULL;

-- demo data is permanent: a policy of a demo-* household or entity is never deleted outside the fixture path
CREATE OR REPLACE FUNCTION public.cash_policies_demo_guard() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
BEGIN
  IF (OLD.household_id IS NOT NULL AND public.crm_is_demo_household(OLD.household_id))
     OR (OLD.entity_id IS NOT NULL AND public.crm_is_demo_entity(OLD.entity_id)) THEN
    IF current_setting('crm.demo_teardown', true) = 'fixture' AND public.crm_org_is_test(OLD.org_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'cash02: refusing to delete a cash policy that belongs to permanent demo data (a record tagged demo-*)'
      USING ERRCODE = 'P0001', HINT = 'Demo policies are retired by a newer approved version, never deleted.';
  END IF;
  RETURN OLD;
END $$;
DROP TRIGGER IF EXISTS cash_policies_demo_guard_trg ON public.cash_policies;
CREATE TRIGGER cash_policies_demo_guard_trg BEFORE DELETE ON public.cash_policies
  FOR EACH ROW EXECUTE FUNCTION public.cash_policies_demo_guard();

-- ── 3. RLS — a policy for every operation ────────────────────────────────────────────────────────────────────────
DO $$
DECLARE pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  ALTER TABLE public.cash_policies ENABLE ROW LEVEL SECURITY;
  DROP POLICY IF EXISTS cash_policies_org_select ON public.cash_policies;
  EXECUTE format('CREATE POLICY cash_policies_org_select ON public.cash_policies FOR SELECT USING %s', pred);
  DROP POLICY IF EXISTS cash_policies_org_insert ON public.cash_policies;
  EXECUTE format('CREATE POLICY cash_policies_org_insert ON public.cash_policies FOR INSERT WITH CHECK %s', pred);
  DROP POLICY IF EXISTS cash_policies_org_update ON public.cash_policies;
  EXECUTE format('CREATE POLICY cash_policies_org_update ON public.cash_policies FOR UPDATE USING %s WITH CHECK %s',
                 pred, pred);
  DROP POLICY IF EXISTS cash_policies_org_delete ON public.cash_policies;
  EXECUTE format('CREATE POLICY cash_policies_org_delete ON public.cash_policies FOR DELETE USING %s', pred);
  GRANT SELECT, INSERT, UPDATE, DELETE ON public.cash_policies TO app_service;
END $$;

-- ── 4. min_coverage_months moves into the policy: cash01's cash_coverage_minimums is retired ──────────────────────
-- Its ONE home is now cash_policies.min_coverage_months of the scope's ACTIVE policy (services.cash_coverage.minimums).
-- A minimum is never copied in as an ACTIVE policy (that would skip the checker): if any row exists, this migration
-- stops and the operator re-enters it as a policy draft for a second person to approve. Live at cash02: 0 rows ever.
DO $$
BEGIN
  IF to_regclass('public.cash_coverage_minimums') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM public.cash_coverage_minimums) THEN
      RAISE EXCEPTION 'cash02: cash_coverage_minimums holds rows — re-enter each minimum as a cash policy draft and '
                      'have it approved, then re-run (the minimum is never copied in as an active policy)';
    END IF;
    DROP TABLE public.cash_coverage_minimums;
  END IF;
END $$;
