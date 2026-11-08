-- S29B — Workflow Manager Wave 4: unattended write steps under S30's segregation of duties.
--
-- Applied with apps/api/scripts/_apply_s29b.py --all (operator postgres DSN; one transaction; idempotent).
--
--   0. Task 0 — the role-grant floor ON for every organization: a NEW VERSION (v2) of the platform policy
--      policy:role_grant with enforce_floor = true. v1 is retired (valid_to), never edited; the change is audited.
--   1. SoD catalog: two duties pairs + two item types (workflow_autonomy_setting, workflow_autonomy_optin), each with
--      a platform default rule and a platform policy (maker ≠ checker, no self-approval).
--   2. public.workflow_autonomy_settings — the ONE governed, versioned store for the org switch, the kill switches,
--      the limits, the shadow-run requirement and each workflow version's opt-in. (Why a new table: org_settings is
--      a flat key/value row updated IN PLACE, with no history and no maker / checker pair, so it cannot carry a
--      maker-checker switch under Rule 3 — see docs/WORKFLOW_AUTONOMY.md.)
--   3. Columns on the EXISTING run / step rows: shadow results, the unattended marker, the delegate, the idempotency
--      key, the shadow review.
--   4. domain_event_deliveries: workflow_version_id + a claim status so one event starts at most one run per
--      workflow version (a unique index, not a read-then-write), and RATE_LIMITED.

-- ── 0. Task 0: the role-grant floor ON (platform policy v2) ────────────────
DO $$
DECLARE
  cur record;
  new_id uuid;
  ts timestamptz := clock_timestamp();
BEGIN
  SELECT id, version_no, enforce_floor, self_approval INTO cur
    FROM public.sod_rules
   WHERE org_id IS NULL AND rule_key = 'policy:role_grant' AND status = 'active' AND valid_to IS NULL;
  IF cur.id IS NULL THEN
    RAISE EXCEPTION 'S29B: no active platform policy:role_grant (S30 docs/s30_part1.sql not applied?)';
  END IF;
  IF cur.enforce_floor THEN
    RAISE NOTICE 'S29B: policy:role_grant v% already enforces the floor — nothing to do', cur.version_no;
    RETURN;
  END IF;
  UPDATE public.sod_rules SET status = 'retired', valid_to = ts WHERE id = cur.id;
  INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                                enforce_floor, description, status, version_no, valid_from, decided_at, decision_note)
  VALUES ('policy:role_grant', 'platform', NULL, 'policy', 'role_grant', 'same_item', 'block', 'never', true,
          'Platform policy for role or permission-set grants: no one grants themselves a role, a profile or a '
          || 'permission set, from any login, Super Admin included (S29B Task 0 — required for security exams; an '
          || 'organization cannot turn it off).',
          'active', cur.version_no + 1, ts, ts, 'S29B Task 0 (Joe, 2026-11-08): the role-grant floor is ON everywhere')
  RETURNING id INTO new_id;
  INSERT INTO public.audit_log (org_id, user_id, action, resource_type, resource_id, payload)
  VALUES ('bb347258-8f28-4f49-8cc9-e29ccad82884', NULL, 'sod.platform_rule_versioned', 'sod_rules', new_id,
          jsonb_build_object('rule_key', 'policy:role_grant', 'from_version', cur.version_no,
                             'to_version', cur.version_no + 1, 'enforce_floor', jsonb_build_array(false, true),
                             'replaced_rule_id', cur.id, 'sprint', 's29b', 'reason',
                             'the self-grant floor is ON for every organization and not relaxable'));
END $$;

-- ── 1. SoD catalog for the two new decisions ───────────────────────────────
INSERT INTO public.sod_duties (duty_key, label, description, kind, permissions, sort_order) VALUES
  ('set_workflow_autonomy', 'Change workflow autonomy settings', 'Asks to turn unattended workflow writes on, or to raise a limit, for an organization.', 'make', '{manage_org_settings}', 120),
  ('approve_workflow_autonomy', 'Approve workflow autonomy settings', 'Decides a request to turn unattended workflow writes on or raise a limit.', 'check', '{manage_org_settings}', 121),
  ('request_unattended_workflow', 'Ask for a workflow to run unattended', 'Asks for a workflow version to run its write steps unattended after its shadow period.', 'make', '{author_workflows}', 122),
  ('approve_unattended_workflow', 'Approve a workflow to run unattended', 'Signs off a workflow version to run its write steps unattended.', 'check', '{author_workflows,manage_org_settings}', 123)
ON CONFLICT (duty_key) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description, kind = EXCLUDED.kind,
  permissions = EXCLUDED.permissions, sort_order = EXCLUDED.sort_order;

INSERT INTO public.sod_item_types (item_type, label, description, maker_duty, checker_duty, floor_class,
                                   self_approval_capable, pre_s30_rule, site, sort_order) VALUES
  ('workflow_autonomy_setting', 'Workflow autonomy setting', 'Turning unattended workflow writes on for an organization, or raising one of its limits (workflow_autonomy_settings).', 'set_workflow_autonomy', 'approve_workflow_autonomy', NULL, false, 'maker_ne_checker', 'services/workflow_autonomy.py', 120),
  ('workflow_autonomy_optin', 'Unattended workflow opt-in', 'A workflow version''s sign-off to run its write steps unattended (workflow_autonomy_settings, version_autonomy).', 'request_unattended_workflow', 'approve_unattended_workflow', NULL, false, 'maker_ne_checker', 'services/workflow_autonomy.py', 121)
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
 WHERE t.item_type IN ('workflow_autonomy_setting', 'workflow_autonomy_optin')
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind IN ('floor', 'default'));

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                              enforce_floor, description, status, version_no)
SELECT 'policy:' || t.item_type, 'platform', NULL, 'policy', t.item_type, 'same_item', 'block', 'never', true,
       'Platform policy for ' || lower(t.label) || ': a different person decides (S29B).', 'active', 1
  FROM public.sod_item_types t
 WHERE t.item_type IN ('workflow_autonomy_setting', 'workflow_autonomy_optin')
   AND NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind = 'policy');

-- ── 2. The governed autonomy settings (versioned, maker-checker) ───────────
CREATE TABLE IF NOT EXISTS public.workflow_autonomy_settings (
  id                   uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
  owner_scope          text NOT NULL,
  org_id               uuid REFERENCES public.organizations(id),
  workflow_version_id  uuid REFERENCES public.workflow_versions(id),
  setting_key          text NOT NULL,
  setting_value        jsonb NOT NULL,
  requires_checker     boolean NOT NULL,
  reason               text NOT NULL,
  status               text NOT NULL DEFAULT 'draft',
  version_no           integer NOT NULL DEFAULT 1,
  maker_id             uuid REFERENCES public.users(id),
  checker_id           uuid REFERENCES public.users(id),
  decided_at           timestamptz,
  decision_note        text,
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT was_scope_chk CHECK ((owner_scope = 'platform' AND org_id IS NULL AND workflow_version_id IS NULL)
                                  OR (owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT was_key_chk CHECK (setting_key IN ('autonomy', 'kill_switch', 'limits', 'shadow_runs_required',
                                                'version_autonomy')),
  -- the platform carries only its kill switch; a version opt-in is the only key that names a version
  CONSTRAINT was_key_scope_chk CHECK ((owner_scope <> 'platform' OR setting_key = 'kill_switch')
                                      AND ((setting_key = 'version_autonomy') = (workflow_version_id IS NOT NULL))),
  CONSTRAINT was_status_chk CHECK (status IN ('draft', 'active', 'rejected', 'retired')),
  CONSTRAINT was_maker_checker_chk CHECK (checker_id IS NULL OR maker_id IS NULL OR checker_id <> maker_id),
  -- a loosening change (requires_checker) is never active without a checker
  CONSTRAINT was_checked_chk CHECK (status IN ('draft', 'rejected') OR NOT requires_checker OR checker_id IS NOT NULL),
  -- belt and braces for the two switches that matter most: autonomy ON and a version ON always carry a checker
  CONSTRAINT was_on_checked_chk CHECK (status IN ('draft', 'rejected') OR checker_id IS NOT NULL
                                       OR NOT ((setting_key = 'autonomy' AND setting_value = 'true'::jsonb)
                                               OR (setting_key = 'version_autonomy'
                                                   AND setting_value ->> 'state' = 'on'))),
  CONSTRAINT was_rejected_chk CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT was_reason_chk CHECK (length(btrim(reason)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS was_active_uq ON public.workflow_autonomy_settings
  (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid),
   COALESCE(workflow_version_id, '00000000-0000-0000-0000-000000000000'::uuid), setting_key)
  WHERE status = 'active' AND valid_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS was_draft_uq ON public.workflow_autonomy_settings
  (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid),
   COALESCE(workflow_version_id, '00000000-0000-0000-0000-000000000000'::uuid), setting_key)
  WHERE status = 'draft';
CREATE INDEX IF NOT EXISTS was_org_idx ON public.workflow_autonomy_settings (org_id, setting_key, status);

-- content never changes; draft → active / rejected; active → retired (closing valid_to); decided rows are final
CREATE OR REPLACE FUNCTION public.workflow_autonomy_settings_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.sod_teardown_allowed() OR OLD.status = 'draft' THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'workflow_autonomy_settings: a decided setting version is never deleted (%, %)',
      OLD.setting_key, OLD.status;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.owner_scope = 'platform' AND NOT public.sod_super() AND current_user = 'app_service' THEN
      RAISE EXCEPTION 'workflow_autonomy_settings: a platform setting is written only by a Super Admin';
    END IF;
    RETURN NEW;
  END IF;
  IF (NEW.owner_scope, NEW.org_id, NEW.workflow_version_id, NEW.setting_key, NEW.setting_value, NEW.requires_checker,
      NEW.reason, NEW.version_no, NEW.maker_id, NEW.valid_from, NEW.created_at)
     IS DISTINCT FROM
     (OLD.owner_scope, OLD.org_id, OLD.workflow_version_id, OLD.setting_key, OLD.setting_value, OLD.requires_checker,
      OLD.reason, OLD.version_no, OLD.maker_id, OLD.valid_from, OLD.created_at) THEN
    RAISE EXCEPTION 'workflow_autonomy_settings: a setting version''s content never changes — add a new version';
  END IF;
  IF OLD.status = 'draft' AND NEW.status IN ('active', 'rejected') THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'active' AND NEW.status = 'retired' AND OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (NEW.checker_id, NEW.decided_at, NEW.decision_note) IS NOT DISTINCT FROM
         (OLD.checker_id, OLD.decided_at, OLD.decision_note) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'workflow_autonomy_settings: % → % is not a permitted change', OLD.status, NEW.status;
END $$;

DROP TRIGGER IF EXISTS workflow_autonomy_settings_guard_trg ON public.workflow_autonomy_settings;
CREATE TRIGGER workflow_autonomy_settings_guard_trg BEFORE INSERT OR UPDATE OR DELETE
  ON public.workflow_autonomy_settings FOR EACH ROW EXECUTE FUNCTION public.workflow_autonomy_settings_guard();

DO $$
DECLARE
  t text := 'workflow_autonomy_settings';
  own text := '(NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid';
  sa  text := '(COALESCE(NULLIF(current_setting(''app.is_super_admin'', true), ''''), ''false'') = ''true'')';
BEGIN
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  -- platform rows (the kill switch) are read by every org (the engine checks them); org rows only in their org
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING ((org_id IS NULL AND %s IS NOT NULL) OR org_id = %s OR %s)',
                 t || '_select', t, own, own, sa);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK (org_id = %s OR (org_id IS NULL AND %s))',
                 t || '_insert', t, own, sa);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING (org_id = %s OR (org_id IS NULL AND %s)) WITH CHECK (org_id = %s OR (org_id IS NULL AND %s))',
                 t || '_update', t, own, sa, own, sa);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_delete', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING (org_id = %s OR (org_id IS NULL AND %s))',
                 t || '_delete', t, own, sa);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
END $$;

-- ── 3. The existing run / step rows carry the autonomy record ──────────────
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS autonomy_mode text;
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS shadow_review_outcome text;
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS shadow_reviewed_by uuid REFERENCES public.users(id);
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS shadow_reviewed_at timestamptz;
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS shadow_review_note text;
DO $$ BEGIN
  ALTER TABLE public.workflow_runs ADD CONSTRAINT workflow_runs_autonomy_mode_chk
    CHECK (autonomy_mode IS NULL OR autonomy_mode IN ('shadow', 'unattended'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE public.workflow_runs ADD CONSTRAINT workflow_runs_shadow_review_chk
    CHECK ((shadow_review_outcome IS NULL AND shadow_reviewed_by IS NULL AND shadow_reviewed_at IS NULL)
           OR (shadow_review_outcome IN ('clean', 'not_clean') AND shadow_reviewed_by IS NOT NULL
               AND shadow_reviewed_at IS NOT NULL AND autonomy_mode = 'shadow'
               AND (shadow_review_outcome = 'clean' OR length(btrim(COALESCE(shadow_review_note, ''))) > 0)));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE public.workflow_run_steps ADD COLUMN IF NOT EXISTS autonomy_mode text;
ALTER TABLE public.workflow_run_steps ADD COLUMN IF NOT EXISTS delegate_for uuid REFERENCES public.users(id);
ALTER TABLE public.workflow_run_steps ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE public.workflow_run_steps ADD COLUMN IF NOT EXISTS autonomy_claimed_at timestamptz;
DO $$ BEGIN
  ALTER TABLE public.workflow_run_steps ADD CONSTRAINT workflow_run_steps_autonomy_mode_chk
    CHECK (autonomy_mode IS NULL OR autonomy_mode IN ('shadow', 'unattended', 'platform_job', 'held_for_person'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  -- an unattended write always names the person it ran for, and its idempotency key
  ALTER TABLE public.workflow_run_steps ADD CONSTRAINT workflow_run_steps_unattended_chk
    CHECK (autonomy_mode IS DISTINCT FROM 'unattended'
           OR (delegate_for IS NOT NULL AND idempotency_key IS NOT NULL AND autonomy_claimed_at IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE UNIQUE INDEX IF NOT EXISTS workflow_run_steps_idempotency_uq ON public.workflow_run_steps (idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS workflow_run_steps_unattended_idx ON public.workflow_run_steps (org_id, autonomy_claimed_at)
  WHERE autonomy_mode = 'unattended';

-- ── 4. Events: one run per event per workflow version; rate limit ──────────
ALTER TABLE public.domain_event_deliveries ADD COLUMN IF NOT EXISTS workflow_version_id uuid
  REFERENCES public.workflow_versions(id);
ALTER TABLE public.domain_event_deliveries DROP CONSTRAINT IF EXISTS domain_event_deliveries_status_check;
ALTER TABLE public.domain_event_deliveries ADD CONSTRAINT domain_event_deliveries_status_check
  CHECK (status = ANY (ARRAY['DELIVERED'::text, 'FAILED'::text, 'STARTING'::text, 'RATE_LIMITED'::text]));
CREATE UNIQUE INDEX IF NOT EXISTS domain_event_deliveries_event_version_uq
  ON public.domain_event_deliveries (domain_event_id, workflow_version_id)
  WHERE workflow_version_id IS NOT NULL AND status IN ('STARTING', 'DELIVERED');
-- the rate limit counts a workflow VERSION's event runs in the last hour (any of its triggers)
DROP INDEX IF EXISTS public.domain_event_deliveries_rate_idx;
CREATE INDEX IF NOT EXISTS domain_event_deliveries_version_rate_idx
  ON public.domain_event_deliveries (workflow_version_id, created_at) WHERE status IN ('STARTING', 'DELIVERED');
