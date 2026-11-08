-- S30 — SEGREGATION OF DUTIES: one duty model, one maker-checker rule.
--
-- Applied by apps/api/scripts/_apply_s30.py --schema on the operator DSN (role postgres) in ONE transaction.
-- Idempotent. The migration writes PLATFORM catalog rows only (owner_scope 'platform', org_id NULL): the duty list,
-- the item types, the floor rules, the platform default rules and the platform default policies — which EQUAL the
-- rules in force before S30 (Task 7). It writes no org row.
--
--   sod_duties        a duty per action class (propose_trade, approve_trade, …) and the permissions that grant it
--   sod_item_types    every item type an approval site decides (the inventory of docs/S30_DISCOVERY.md 1a): its
--                     maker duty, checker duty, floor class (trade / transfer / disclosure / role_grant / NULL) and
--                     whether the site can record a disclosed self-approval at all
--   sod_rules         VERSIONED rules (valid-time axis, Rule 3): platform FLOOR rules (never relaxed, never
--                     self-approved), platform DEFAULT rules, platform DEFAULT POLICIES (self-approval +
--                     enforce_floor per item type) and an org's own stricter rules / policy overrides. Every edit
--                     is a draft decided by a DIFFERENT person (maker-checker CHECK); platform rows only under
--                     app.is_super_admin.
--   sod_decisions     the append-only record of every real check decision (allowed / refused / self-approved),
--                     with the rule that decided it — the self-approval log and the refused-checks report
--   user_person_links a retired login row → the person it belongs to (admin link_pending_login); with the
--                     duplicate-merge rule (a retired row whose sub is another user's linked identity) this is the
--                     "same person" test — two logins of one person are ONE person.
--
-- RLS on every table for every operation (SELECT / INSERT / UPDATE / DELETE), each reading the org GUC with NULLIF.

-- ── 0. Helpers ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.sod_super() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), 'false') = 'true'
$$;

CREATE OR REPLACE FUNCTION public.sod_org() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
$$;

-- The documented teardown path for the append-only decision log: set LOCAL by a role that is not app_service.
CREATE OR REPLACE FUNCTION public.sod_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('sod.teardown', true), '') = 'fixture' AND current_user <> 'app_service'
$$;

-- ── 1. Duties ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.sod_duties (
  duty_key     text PRIMARY KEY,
  label        text NOT NULL,
  description  text NOT NULL,
  kind         text NOT NULL,
  permissions  text[] NOT NULL DEFAULT '{}',
  sort_order   integer NOT NULL DEFAULT 100,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sod_duties_kind_chk CHECK (kind IN ('make', 'check', 'administer')),
  CONSTRAINT sod_duties_key_chk CHECK (duty_key ~ '^[a-z][a-z0-9_]*$')
);

-- ── 2. Item types ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.sod_item_types (
  item_type              text PRIMARY KEY,
  label                  text NOT NULL,
  description            text NOT NULL,
  maker_duty             text NOT NULL REFERENCES public.sod_duties(duty_key),
  checker_duty           text NOT NULL REFERENCES public.sod_duties(duty_key),
  floor_class            text,
  self_approval_capable  boolean NOT NULL DEFAULT false,
  pre_s30_rule           text NOT NULL DEFAULT 'maker_ne_checker',
  site                   text NOT NULL,
  sort_order             integer NOT NULL DEFAULT 100,
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sod_item_types_key_chk CHECK (item_type ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT sod_item_types_floor_chk CHECK (floor_class IS NULL OR floor_class IN
                                             ('trade', 'transfer', 'disclosure', 'role_grant')),
  -- a floor item can never be self-approved, so it can never be self-approval capable
  CONSTRAINT sod_item_types_floor_selfapproval_chk CHECK (floor_class IS NULL OR NOT self_approval_capable),
  CONSTRAINT sod_item_types_duties_chk CHECK (maker_duty <> checker_duty),
  CONSTRAINT sod_item_types_pre_chk CHECK (pre_s30_rule IN ('maker_ne_checker', 'none'))
);

-- ── 3. Rules (versioned, maker-checker) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.sod_rules (
  id             uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
  rule_key       text NOT NULL,
  owner_scope    text NOT NULL,
  org_id         uuid REFERENCES public.organizations(id),
  rule_kind      text NOT NULL,
  item_type      text NOT NULL REFERENCES public.sod_item_types(item_type),
  maker_duty     text REFERENCES public.sod_duties(duty_key),
  checker_duty   text REFERENCES public.sod_duties(duty_key),
  scope          text NOT NULL DEFAULT 'same_item',
  period_days    integer,
  severity       text NOT NULL DEFAULT 'block',
  self_approval  text,
  enforce_floor  boolean,
  description    text NOT NULL,
  retires        boolean NOT NULL DEFAULT false,
  status         text NOT NULL DEFAULT 'draft',
  version_no     integer NOT NULL DEFAULT 1,
  drafted_by     uuid REFERENCES public.users(id),
  checker_id     uuid REFERENCES public.users(id),
  decided_at     timestamptz,
  decision_note  text,
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sod_rules_scope_owner_chk CHECK ((owner_scope = 'platform' AND org_id IS NULL)
                                              OR (owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT sod_rules_kind_chk CHECK (rule_kind IN ('floor', 'default', 'policy', 'org')),
  CONSTRAINT sod_rules_kind_owner_chk CHECK ((rule_kind IN ('floor', 'default') AND owner_scope = 'platform')
                                             OR (rule_kind = 'org' AND owner_scope = 'org')
                                             OR rule_kind = 'policy'),
  CONSTRAINT sod_rules_scope_chk CHECK (scope IN ('same_item', 'same_household', 'same_period')),
  CONSTRAINT sod_rules_period_chk CHECK ((scope = 'same_item' AND period_days IS NULL)
                                         OR (scope <> 'same_item' AND period_days BETWEEN 1 AND 3660)),
  CONSTRAINT sod_rules_severity_chk CHECK (severity IN ('block', 'warn')),
  CONSTRAINT sod_rules_selfapproval_chk CHECK (self_approval IS NULL OR self_approval IN ('never', 'when_no_other_checker')),
  -- a FLOOR rule blocks, on the same item, and carries no self-approval
  CONSTRAINT sod_rules_floor_chk CHECK (rule_kind <> 'floor' OR (severity = 'block' AND scope = 'same_item'
                                                                  AND self_approval IS NULL AND enforce_floor IS NULL)),
  -- a policy row carries the two switches and no duty pair; every other row carries a duty pair and no switch
  CONSTRAINT sod_rules_policy_shape_chk CHECK (
    (rule_kind = 'policy' AND self_approval IS NOT NULL AND enforce_floor IS NOT NULL
       AND maker_duty IS NULL AND checker_duty IS NULL AND scope = 'same_item')
    OR (rule_kind <> 'policy' AND maker_duty IS NOT NULL AND checker_duty IS NOT NULL
       AND self_approval IS NULL AND enforce_floor IS NULL)),
  CONSTRAINT sod_rules_status_chk CHECK (status IN ('draft', 'active', 'rejected', 'retired')),
  CONSTRAINT sod_rules_maker_checker_chk CHECK (checker_id IS NULL OR drafted_by IS NULL OR checker_id <> drafted_by),
  CONSTRAINT sod_rules_decided_chk CHECK (status = 'draft' OR drafted_by IS NULL
                                          OR (checker_id IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT sod_rules_rejected_chk CHECK (status <> 'rejected'
                                           OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT sod_rules_description_chk CHECK (length(btrim(description)) > 0),
  CONSTRAINT sod_rules_key_chk CHECK (rule_key ~ '^[a-z][a-z0-9_:.]*$'),
  -- a floor rule, a platform default rule and a policy are never retired away (a policy is replaced by a version)
  CONSTRAINT sod_rules_retires_chk CHECK (NOT retires OR rule_kind = 'org')
);

-- one ACTIVE version per (owner, rule_key); at most one open draft per (owner, rule_key)
CREATE UNIQUE INDEX IF NOT EXISTS sod_rules_active_uq
  ON public.sod_rules (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), rule_key)
  WHERE status = 'active' AND valid_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS sod_rules_draft_uq
  ON public.sod_rules (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), rule_key)
  WHERE status = 'draft';
CREATE UNIQUE INDEX IF NOT EXISTS sod_rules_version_uq
  ON public.sod_rules (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), rule_key, version_no)
  WHERE status IN ('active', 'retired') AND NOT retires;
CREATE INDEX IF NOT EXISTS sod_rules_item_idx ON public.sod_rules (item_type, status) WHERE valid_to IS NULL;

-- The CONTENT of a rule version never changes (a change is a new version, Rule 3). An UPDATE may only move status
-- draft → active / rejected, or close an active version (valid_to + status 'retired'); decided rows are final.
CREATE OR REPLACE FUNCTION public.sod_rules_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.sod_teardown_allowed() OR OLD.status = 'draft' THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'sod_rules: a decided rule version is never deleted (rule %, status %)', OLD.rule_key, OLD.status;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.owner_scope = 'platform' AND NOT public.sod_super() AND current_user = 'app_service' THEN
      RAISE EXCEPTION 'sod_rules: a platform rule is written only by a Super Admin';
    END IF;
    RETURN NEW;
  END IF;
  -- UPDATE
  IF (NEW.rule_key, NEW.owner_scope, NEW.org_id, NEW.rule_kind, NEW.item_type, NEW.maker_duty, NEW.checker_duty,
      NEW.scope, NEW.period_days, NEW.severity, NEW.self_approval, NEW.enforce_floor, NEW.description,
      NEW.retires, NEW.version_no, NEW.drafted_by, NEW.valid_from, NEW.created_at)
     IS DISTINCT FROM
     (OLD.rule_key, OLD.owner_scope, OLD.org_id, OLD.rule_kind, OLD.item_type, OLD.maker_duty, OLD.checker_duty,
      OLD.scope, OLD.period_days, OLD.severity, OLD.self_approval, OLD.enforce_floor, OLD.description,
      OLD.retires, OLD.version_no, OLD.drafted_by, OLD.valid_from, OLD.created_at) THEN
    RAISE EXCEPTION 'sod_rules: a rule version''s content never changes — draft a new version';
  END IF;
  IF OLD.status = 'draft' AND (NEW.status IN ('active', 'rejected')
                               OR (NEW.status = 'retired' AND OLD.retires AND NEW.valid_to IS NOT NULL)) THEN
    IF OLD.owner_scope = 'platform' AND NOT public.sod_super() AND current_user = 'app_service' THEN
      RAISE EXCEPTION 'sod_rules: a platform rule is decided only by a Super Admin';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.status = 'active' AND NEW.status = 'retired' AND NEW.valid_to IS NOT NULL AND OLD.valid_to IS NULL
     AND (NEW.checker_id, NEW.decided_at, NEW.decision_note) IS NOT DISTINCT FROM
         (OLD.checker_id, OLD.decided_at, OLD.decision_note) THEN
    IF OLD.owner_scope = 'platform' AND NOT public.sod_super() AND current_user = 'app_service' THEN
      RAISE EXCEPTION 'sod_rules: a platform rule is retired only by a Super Admin';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'sod_rules: % → % is not a permitted change', OLD.status, NEW.status;
END $$;

DROP TRIGGER IF EXISTS sod_rules_guard_trg ON public.sod_rules;
CREATE TRIGGER sod_rules_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.sod_rules
  FOR EACH ROW EXECUTE FUNCTION public.sod_rules_guard();

-- ── 4. Decisions (append-only) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.sod_decisions (
  id               uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  item_type        text NOT NULL REFERENCES public.sod_item_types(item_type),
  item_id          text,
  household_id     uuid,
  site             text NOT NULL,
  -- plain uuids, NOT foreign keys: the decision log outlives any login row (users are retired, never deleted), and a
  -- verify's fixture teardown must never follow a fixture user into another org's decision rows
  checker_id       uuid,
  checker_person   uuid,
  maker_ids        uuid[] NOT NULL DEFAULT '{}',
  maker_persons    uuid[] NOT NULL DEFAULT '{}',
  outcome          text NOT NULL,
  rule_id          uuid REFERENCES public.sod_rules(id),
  rule_key         text,
  reason           text NOT NULL,
  disclosure       text,
  warnings         jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sod_decisions_outcome_chk CHECK (outcome IN ('allowed', 'refused', 'self_approved')),
  CONSTRAINT sod_decisions_selfapproval_chk CHECK (outcome <> 'self_approved'
                                                   OR length(btrim(COALESCE(disclosure, ''))) > 0),
  CONSTRAINT sod_decisions_reason_chk CHECK (length(btrim(reason)) > 0)
);
ALTER TABLE public.sod_decisions DROP CONSTRAINT IF EXISTS sod_decisions_checker_id_fkey;
ALTER TABLE public.sod_decisions DROP CONSTRAINT IF EXISTS sod_decisions_checker_person_fkey;
CREATE INDEX IF NOT EXISTS sod_decisions_org_time_idx ON public.sod_decisions (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS sod_decisions_household_idx ON public.sod_decisions (org_id, household_id, created_at DESC)
  WHERE household_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS sod_decisions_item_idx ON public.sod_decisions (org_id, item_type, item_id);

CREATE OR REPLACE FUNCTION public.sod_decisions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF public.sod_teardown_allowed() THEN
    RETURN COALESCE(OLD, NEW);
  END IF;
  RAISE EXCEPTION 'sod_decisions is append-only (% refused)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS sod_decisions_guard_trg ON public.sod_decisions;
CREATE TRIGGER sod_decisions_guard_trg BEFORE UPDATE OR DELETE ON public.sod_decisions
  FOR EACH ROW EXECUTE FUNCTION public.sod_decisions_guard();

-- ── 5. Person links ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_person_links (
  user_id         uuid PRIMARY KEY REFERENCES public.users(id),
  person_user_id  uuid NOT NULL REFERENCES public.users(id),
  org_id          uuid NOT NULL REFERENCES public.organizations(id),
  linked_via      text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT user_person_links_self_chk CHECK (user_id <> person_user_id),
  CONSTRAINT user_person_links_via_chk CHECK (linked_via IN ('operator', 'duplicate_merge'))
);
CREATE INDEX IF NOT EXISTS user_person_links_person_idx ON public.user_person_links (person_user_id);

-- ── 6. RLS — every table, every operation ──────────────────────────────────
DO $$
DECLARE
  t text;
  own text := '(NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid';
  sa  text := '(COALESCE(NULLIF(current_setting(''app.is_super_admin'', true), ''''), ''false'') = ''true'')';
BEGIN
  -- the two global catalogs: readable by any caller with an org context; written only by a Super Admin
  FOREACH t IN ARRAY ARRAY['sod_duties', 'sod_item_types'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING (%s IS NOT NULL OR %s)', t || '_select', t, own, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK (%s AND %s IS NOT NULL OR %s)',
                   t || '_insert', t, sa, own, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING (%s AND %s IS NOT NULL OR %s) WITH CHECK (%s)',
                   t || '_update', t, sa, own, sa, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING (%s AND %s IS NOT NULL OR %s)',
                   t || '_delete', t, sa, own, sa);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;

  -- rules: platform rows readable by every org, written under the Super Admin GUC; org rows only in their org
  t := 'sod_rules';
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING ((org_id IS NULL AND %s IS NOT NULL) OR org_id = %s OR %s)',
                 t || '_select', t, own, own, sa);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK ((org_id IS NULL AND %s AND %s IS NOT NULL) OR org_id = %s OR (org_id IS NULL AND %s))',
                 t || '_insert', t, sa, own, own, sa);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING ((org_id IS NULL AND %s AND %s IS NOT NULL) OR org_id = %s OR (org_id IS NULL AND %s)) WITH CHECK ((org_id IS NULL AND %s) OR org_id = %s)',
                 t || '_update', t, sa, own, own, sa, sa, own);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_delete', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING ((org_id IS NULL AND %s AND %s IS NOT NULL) OR org_id = %s OR (org_id IS NULL AND %s))',
                 t || '_delete', t, sa, own, own, sa);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);

  -- decisions + person links: org rows only (a Super Admin reads across orgs)
  FOREACH t IN ARRAY ARRAY['sod_decisions', 'user_person_links'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING (org_id = %s OR %s)', t || '_select', t, own, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK (org_id = %s OR %s)', t || '_insert', t, own, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING (org_id = %s OR %s) WITH CHECK (org_id = %s OR %s)',
                   t || '_update', t, own, sa, own, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING (org_id = %s OR %s)', t || '_delete', t, own, sa);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;

-- ── 7. Task 8 — a trigger names its maker ──────────────────────────────────
-- workflow_triggers.created_by IS the trigger's owner (the maker of every run it fires — workflow_scheduler._fire
-- and domain_events already pass it as started_by). Configuring a trigger requires one from S30 on (the router
-- refuses a create without it); existing rows are left as they are (one live event trigger has none — reported).
COMMENT ON COLUMN public.workflow_triggers.created_by IS
  'S30: the trigger''s OWNER — the named maker of every run it fires; a Tier-1 step of that run is checked against it.';

-- ── 8. Seed: the platform catalog (owner_scope 'platform', org_id NULL) ─────
-- Duties. kind 'make' = proposes / enters / drafts; 'check' = approves / publishes / decides; 'administer' = grants.
INSERT INTO public.sod_duties (duty_key, label, description, kind, permissions, sort_order) VALUES
  ('propose_agent_output', 'Propose agent output', 'Records an agent''s proposed output for review (agent_proposals.proposed_by).', 'make', '{}', 10),
  ('review_agent_output', 'Review agent output', 'Approves or rejects an agent proposal.', 'check', '{review_agent_proposals}', 11),
  ('run_workflow', 'Start or own a workflow run', 'Starts a run, or owns the trigger that starts it (the named maker of every step it reaches).', 'make', '{author_workflows,configure_workflow_triggers}', 20),
  ('approve_workflow_step', 'Approve a Tier-1 workflow step', 'Approves a suspended Tier-1 Service Task before it runs.', 'check', '{review_agent_proposals}', 21),
  ('complete_workflow_task', 'Complete a workflow user task', 'Completes a BPMN User Task assigned to a permission or profile.', 'check', '{view_workflow_runs}', 22),
  ('open_task', 'Open a governed task', 'Opens a governed human task (services.human_tasks) for a subject.', 'make', '{}', 23),
  ('decide_task', 'Decide a governed task', 'Decides a governed human task.', 'check', '{}', 24),
  ('propose_trade', 'Propose a trade', 'Builds, edits or submits a trade: a rebalance proposal, a trade block, an execution instruction or an allocation correction.', 'make', '{manage_proposals,manage_blocks,manage_execution}', 30),
  ('approve_trade', 'Approve a trade', 'Approves a trade before it goes to a custodian.', 'check', '{approve_proposals,approve_blocks,approve_execution}', 31),
  ('propose_transfer', 'Propose a transfer', 'Proposes a gift, a Section 351 contribution or a money movement.', 'make', '{manage_tax,manage_proposals}', 32),
  ('approve_transfer', 'Approve a transfer', 'Approves a gift, a Section 351 plan or a money movement.', 'check', '{approve_tax,approve_proposals}', 33),
  ('propose_model_assignment', 'Propose a model assignment', 'Asks for an account''s model link to change.', 'make', '{manage_models,manage_proposals}', 40),
  ('approve_model_assignment', 'Approve a model assignment', 'Decides an account''s model link change.', 'check', '{approve_proposals}', 41),
  ('author_model', 'Author a model version', 'Uploads, edits or submits a model version.', 'make', '{manage_models}', 42),
  ('publish_model', 'Approve a model version', 'Approves a model version for use.', 'check', '{approve_models}', 43),
  ('author_provider_model', 'Author a provided model', 'Requests or edits a provider catalog version.', 'make', '{manage_models}', 44),
  ('publish_provider_model', 'Publish a provided model', 'Publishes a provider catalog version (Super Admin).', 'check', '{publish_provided_models}', 45),
  ('request_subscription', 'Request a model subscription', 'Requests a subscription to a provided model.', 'make', '{manage_models}', 46),
  ('approve_subscription', 'Approve a model subscription', 'Approves a subscription to a provided model (Super Admin).', 'check', '{approve_provided_subscriptions}', 47),
  ('set_authority', 'Set authority', 'Asks for a household or account authority / custody change.', 'make', '{manage_authority}', 50),
  ('approve_authority', 'Approve authority', 'Decides an authority / custody change.', 'check', '{approve_authority}', 51),
  ('set_lot_method', 'Set a lot method', 'Asks for a household or account lot-method change.', 'make', '{manage_proposals}', 52),
  ('approve_lot_method', 'Approve a lot method', 'Decides a lot-method change.', 'check', '{approve_proposals}', 53),
  ('set_allocation_policy', 'Set an allocation policy', 'Drafts a block allocation policy version.', 'make', '{manage_blocks}', 54),
  ('approve_allocation_policy', 'Approve an allocation policy', 'Decides a block allocation policy version.', 'check', '{approve_blocks}', 55),
  ('enter_suitability', 'Enter a suitability record', 'Enters a margin agreement / suitability record.', 'make', '{manage_proposals}', 56),
  ('approve_suitability', 'Approve a suitability record', 'Decides a margin agreement / suitability record.', 'check', '{approve_proposals}', 57),
  ('enter_held_away', 'Enter held-away data', 'Enters a held-away import.', 'make', '{manage_held_away}', 60),
  ('approve_held_away', 'Approve held-away data', 'Approves a held-away import.', 'check', '{approve_held_away}', 61),
  ('enter_portfolio_import', 'Enter an integrated import', 'Enters an integrated-account position import.', 'make', '{manage_portfolio}', 62),
  ('approve_portfolio_import', 'Approve an integrated import', 'Approves an integrated-account position import.', 'check', '{manage_portfolio}', 63),
  ('upload_security_data', 'Upload security data', 'Uploads a security-data file.', 'make', '{manage_org_settings}', 64),
  ('approve_security_data', 'Approve security data', 'Decides a security-data upload.', 'check', '{manage_org_settings}', 65),
  ('enter_tax_input', 'Enter a tax input', 'Enters a tax profile, a loss carryforward, a substitution set or denial.', 'make', '{manage_tax}', 70),
  ('approve_tax_input', 'Approve a tax input', 'Decides a tax profile, loss carryforward, substitution set or denial.', 'check', '{approve_tax}', 71),
  ('edit_hypothetical_policy', 'Edit the hypothetical-performance policy', 'Drafts a hypothetical-performance policy version.', 'make', '{manage_hypothetical_policy}', 72),
  ('approve_hypothetical_policy', 'Approve the hypothetical-performance policy', 'Decides a hypothetical-performance policy version.', 'check', '{manage_hypothetical_policy}', 73),
  ('propose_replication', 'Propose a replication item', 'Builds a note replica, option package, tilt set or replica terms draft.', 'make', '{manage_replication}', 80),
  ('approve_replication', 'Approve a replication item', 'Decides a note replica, option package, tilt set or replica terms draft.', 'check', '{approve_replication}', 81),
  ('author_workflow', 'Author a workflow template', 'Drafts a platform workflow template version.', 'make', '{author_workflows}', 90),
  ('publish_template', 'Publish a workflow template', 'Publishes a platform workflow template version (Super Admin).', 'check', '{}', 91),
  ('draft_disclosure', 'Draft a disclosure', 'Uploads a disclosure version (a draft).', 'make', '{manage_disclosures}', 92),
  ('publish_disclosure', 'Publish a disclosure', 'Publishes a disclosure version.', 'check', '{manage_disclosures}', 93),
  ('propose_billing', 'Propose a billing run', 'Proposes a fee or carry run.', 'make', '{manage_billing}', 94),
  ('approve_billing', 'Approve billing', 'Approves a fee or carry run.', 'check', '{manage_billing}', 95),
  ('manage_roles', 'Grant a role or permission set', 'Grants a role or a permission set to a person.', 'administer', '{manage_members,manage_roles}', 100),
  ('receive_role', 'Receive a role or permission set', 'Is the person a role or permission set is granted to.', 'check', '{}', 101),
  ('edit_sod_rules', 'Edit segregation-of-duties rules', 'Drafts an organization''s segregation-of-duties rule or policy.', 'make', '{manage_org_settings}', 110),
  ('approve_sod_rules', 'Approve segregation-of-duties rules', 'Decides a draft segregation-of-duties rule or policy.', 'check', '{manage_org_settings}', 111)
ON CONFLICT (duty_key) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description,
  kind = EXCLUDED.kind, permissions = EXCLUDED.permissions, sort_order = EXCLUDED.sort_order;

-- Item types — the inventory of docs/S30_DISCOVERY.md 1a, one row per kind of item an approval site decides.
INSERT INTO public.sod_item_types (item_type, label, description, maker_duty, checker_duty, floor_class,
                                   self_approval_capable, pre_s30_rule, site, sort_order) VALUES
  ('agent_proposal', 'Agent proposal', 'An agent''s proposed output (agent_proposals).', 'propose_agent_output', 'review_agent_output', NULL, true, 'maker_ne_checker', 'services/agent_proposals.py', 10),
  ('workflow_step', 'Tier-1 workflow step', 'A suspended Tier-1 Service Task (agent_proposals via workflow_engine.resolve_tier_approval).', 'run_workflow', 'approve_workflow_step', NULL, true, 'maker_ne_checker', 'services/workflow_engine.py', 11),
  ('workflow_user_task', 'Workflow user task', 'A BPMN User Task of a workflow run (workflow_engine.complete_user_task).', 'run_workflow', 'complete_workflow_task', NULL, false, 'maker_ne_checker', 'services/workflow_engine.py', 12),
  ('human_task', 'Governed task', 'A governed human task of a kind with no item type of its own (services.human_tasks).', 'open_task', 'decide_task', NULL, false, 'maker_ne_checker', 'services/human_tasks.py', 13),
  ('trade_block', 'Trade block', 'A block of trades (trade_blocks).', 'propose_trade', 'approve_trade', 'trade', false, 'maker_ne_checker', 'services/blocks.py', 20),
  ('allocation_correction', 'Block allocation correction', 'A correction to a block''s allocation (allocation_corrections).', 'propose_trade', 'approve_trade', 'trade', false, 'maker_ne_checker', 'services/blocks.py', 21),
  ('execution_instruction', 'Execution instruction', 'An instruction to a custodian (execution_instructions).', 'propose_trade', 'approve_trade', 'trade', false, 'maker_ne_checker', 'services/execution.py', 22),
  ('rebalance_proposal', 'Rebalance proposal', 'A proposed rebalance (rebalance_proposals).', 'propose_trade', 'approve_trade', 'trade', false, 'maker_ne_checker', 'services/rebalance_proposals.py', 23),
  ('gift_proposal', 'Gift proposal', 'A charitable gift proposal (gift_proposals).', 'propose_transfer', 'approve_transfer', 'transfer', false, 'maker_ne_checker', 'services/gifts.py', 30),
  ('section351_plan', 'Section 351 plan', 'A Section 351 contribution plan (section351_plans).', 'propose_transfer', 'approve_transfer', 'transfer', false, 'maker_ne_checker', 'services/section351.py', 31),
  ('money_movement', 'Money movement', 'A money-movement activity (assistant_activities via services.trading_authority).', 'propose_transfer', 'approve_transfer', 'transfer', false, 'maker_ne_checker', 'services/trading_authority.py', 32),
  ('disclosure_version', 'Disclosure version', 'A disclosure version to publish (disclosure_versions).', 'draft_disclosure', 'publish_disclosure', 'disclosure', false, 'maker_ne_checker', 'services/disclosures.py', 40),
  ('role_grant', 'Role or permission-set grant', 'A role or permission set granted to a person (user_roles, user_permission_sets).', 'manage_roles', 'receive_role', 'role_grant', false, 'none', 'routers/admin.py, routers/profiles.py', 41),
  ('account_model_link', 'Account model link', 'An account''s model link change (account_model_links).', 'propose_model_assignment', 'approve_model_assignment', NULL, false, 'maker_ne_checker', 'services/account_models.py', 50),
  ('model_version', 'Model version', 'A model version (portfolio.model_versions).', 'author_model', 'publish_model', NULL, false, 'maker_ne_checker', 'services/models.py', 51),
  ('provider_catalog_version', 'Provided model catalog version', 'A provider catalog version (portfolio.catalog_versions).', 'author_provider_model', 'publish_provider_model', NULL, false, 'maker_ne_checker', 'services/model_provider.py', 52),
  ('provider_subscription', 'Provided model subscription', 'A subscription to a provided model (portfolio.provider_subscriptions).', 'request_subscription', 'approve_subscription', NULL, false, 'maker_ne_checker', 'services/model_provider.py', 53),
  ('authority_change', 'Authority change', 'A household authority or account authority / custody change.', 'set_authority', 'approve_authority', NULL, false, 'maker_ne_checker', 'services/authority.py', 60),
  ('lot_method_change', 'Lot-method change', 'A household or account lot-method change.', 'set_lot_method', 'approve_lot_method', NULL, false, 'maker_ne_checker', 'services/lot_method.py', 61),
  ('allocation_policy', 'Allocation policy', 'A block allocation policy version (allocation_policies).', 'set_allocation_policy', 'approve_allocation_policy', NULL, false, 'maker_ne_checker', 'services/blocks.py', 62),
  ('margin_agreement', 'Margin agreement', 'A margin agreement / suitability record (suitability_records).', 'enter_suitability', 'approve_suitability', NULL, false, 'maker_ne_checker', 'services/long_short.py', 63),
  ('held_away_import', 'Held-away import', 'A held-away import (held_away_imports).', 'enter_held_away', 'approve_held_away', NULL, false, 'maker_ne_checker', 'services/held_away.py', 70),
  ('portfolio_import', 'Integrated import', 'An integrated-account position import (portfolio.provisional_imports).', 'enter_portfolio_import', 'approve_portfolio_import', NULL, false, 'maker_ne_checker', 'services/integrated_import.py', 71),
  ('data_upload', 'Security-data upload', 'A security-data upload (portfolio.data_uploads).', 'upload_security_data', 'approve_security_data', NULL, false, 'maker_ne_checker', 'services/security_data/uploads.py', 72),
  ('tax_profile', 'Tax profile', 'A tax profile (tax_profiles).', 'enter_tax_input', 'approve_tax_input', NULL, false, 'maker_ne_checker', 'services/tax_profiles.py', 80),
  ('loss_carryforward', 'Loss carryforward', 'A loss carryforward (loss_carryforwards).', 'enter_tax_input', 'approve_tax_input', NULL, false, 'maker_ne_checker', 'services/tax_profiles.py', 81),
  ('substitution_set', 'Substitution set', 'A substitution set (substitution_sets).', 'enter_tax_input', 'approve_tax_input', NULL, false, 'maker_ne_checker', 'services/substitutions.py', 82),
  ('substitution_denial', 'Substitution denial', 'A substitution denial (substitution_denials).', 'enter_tax_input', 'approve_tax_input', NULL, false, 'maker_ne_checker', 'services/substitutions.py', 83),
  ('hypothetical_policy', 'Hypothetical-performance policy', 'A hypothetical-performance policy version.', 'edit_hypothetical_policy', 'approve_hypothetical_policy', NULL, false, 'maker_ne_checker', 'services/hypothetical_policy.py', 84),
  ('note_replica', 'Note replica', 'A note replica (note_replicas).', 'propose_replication', 'approve_replication', NULL, false, 'maker_ne_checker', 'services/note_replication.py', 90),
  ('option_package', 'Option package', 'An option package (portfolio.option_packages).', 'propose_replication', 'approve_replication', NULL, false, 'maker_ne_checker', 'services/options_sleeve.py', 91),
  ('tilt_set', 'Tilt set', 'A tilt set (tilt_sets).', 'propose_replication', 'approve_replication', NULL, false, 'maker_ne_checker', 'services/tilts.py', 92),
  ('replica_terms_version', 'Replica terms draft', 'A replica terms version draft (portfolio.replica_terms_versions).', 'propose_replication', 'approve_replication', NULL, false, 'maker_ne_checker', 'services/replica_terms.py', 93),
  ('workflow_template_version', 'Platform workflow template', 'A platform workflow template version (workflow_template_versions).', 'author_workflow', 'publish_template', NULL, false, 'maker_ne_checker', 'services/workflow_templates.py', 94),
  ('fee_run', 'Fee run', 'A fee run gate (assistant_activities via services.fee_runs).', 'propose_billing', 'approve_billing', NULL, false, 'maker_ne_checker', 'services/fee_runs.py', 95),
  ('carry_run', 'Carry run', 'An SPV carry run gate (assistant_activities via services.spv_carry_runs).', 'propose_billing', 'approve_billing', NULL, false, 'maker_ne_checker', 'services/spv_carry_runs.py', 96),
  ('sod_rule', 'Segregation-of-duties rule (organization)', 'A draft organization rule or policy (sod_rules).', 'edit_sod_rules', 'approve_sod_rules', NULL, false, 'maker_ne_checker', 'services/sod_admin.py', 110),
  ('sod_platform_rule', 'Segregation-of-duties rule (platform)', 'A draft platform floor / default rule or policy (sod_rules, Super Admin).', 'edit_sod_rules', 'approve_sod_rules', NULL, false, 'maker_ne_checker', 'services/sod_admin.py', 111)
ON CONFLICT (item_type) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description,
  maker_duty = EXCLUDED.maker_duty, checker_duty = EXCLUDED.checker_duty, floor_class = EXCLUDED.floor_class,
  self_approval_capable = EXCLUDED.self_approval_capable, pre_s30_rule = EXCLUDED.pre_s30_rule,
  site = EXCLUDED.site, sort_order = EXCLUDED.sort_order;

-- Platform rules: a FLOOR rule for every floor item type, a DEFAULT rule for every other item type, and a DEFAULT
-- POLICY for every item type — EQUAL to the rules in force before S30 (Task 7):
--   self_approval  'when_no_other_checker' only where selfapproval.structural already allowed it (agent proposals,
--                  Tier-1 workflow steps); 'never' everywhere else
--   enforce_floor  true where the pre-S30 rule already refused the maker (every site); false for role grants, where
--                  no rule existed (an admin could change their own role) — reported, behind the switch
INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, maker_duty, checker_duty, scope,
                              severity, description, status, version_no)
SELECT CASE WHEN t.floor_class IS NOT NULL THEN 'floor:' ELSE 'default:' END || t.item_type, 'platform', NULL,
       CASE WHEN t.floor_class IS NOT NULL THEN 'floor' ELSE 'default' END, t.item_type, t.maker_duty, t.checker_duty,
       'same_item', 'block',
       'The same person may not ' || lower(m.label) || ' and ' || lower(c.label) || ' on the same '
         || lower(t.label) || '.', 'active', 1
  FROM public.sod_item_types t
  JOIN public.sod_duties m ON m.duty_key = t.maker_duty
  JOIN public.sod_duties c ON c.duty_key = t.checker_duty
 WHERE NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind IN ('floor', 'default'));

INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, self_approval,
                              enforce_floor, description, status, version_no)
SELECT 'policy:' || t.item_type, 'platform', NULL, 'policy', t.item_type, 'same_item', 'block',
       CASE WHEN t.item_type IN ('agent_proposal', 'workflow_step') THEN 'when_no_other_checker' ELSE 'never' END,
       t.pre_s30_rule = 'maker_ne_checker',
       'Platform default policy for ' || lower(t.label) || ' (equal to the rule in force before S30).', 'active', 1
  FROM public.sod_item_types t
 WHERE NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE r.org_id IS NULL AND r.item_type = t.item_type
                      AND r.rule_kind = 'policy');
