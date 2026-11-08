-- WFTEMPLATES1 — the WORKFLOW TEMPLATE LIBRARY (Workflow Manager Wave 2, S29a's last item).
--
-- A template is a plain-English description + a validated BPMN diagram that an org copies into itself as a NEW
-- draft workflow definition it then owns. PLATFORM templates are owned by the Hollisworks platform org (or, in a
-- verify, an is_test fixture org), drafted by one Super Admin and published by a DIFFERENT one; ORG templates are an
-- org's own saved workflow versions, visible only inside that org.
--
-- Applied by apps/api/scripts/_apply_wftemplates1.py --schema on the operator DSN (role postgres) in ONE
-- transaction. Idempotent. THE MIGRATION WRITES NO ROWS — the catalog starts empty; the operator loads the shipped
-- templates as drafts (scripts/load_workflow_templates.py) and a second Super Admin publishes each.
--
-- THE DATABASE BACKS THE RULES, not only the service:
--   * a template version's CONTENT (title, category, description, BPMN, derived steps, parameters, prerequisites)
--     never changes after insert; a PUBLISHED version may only be retired; rejected / retired are final;
--   * a platform version starts as a draft, and is published or rejected only under app.is_super_admin by someone
--     other than its drafter (maker-checker CHECK); an org version is saved published (inside its own org);
--   * a platform template's owner is the real platform org or an is_test org, and writing one needs app.is_super_admin;
--   * a tenant reads a PLATFORM template only once a version is published, only from an owner of its own kind
--     (a fixture owner never serves a real org and vice versa), and never another org's ORG template;
--   * a copy record is append-only; nothing here is ever deleted except on the documented fixture-teardown path
--     (workflow_templates.teardown = 'fixture', set LOCAL by a role that is not app_service).
--
-- BI-TEMPORAL (Rule 3). workflow_templates: identity only, never updated. workflow_template_versions: SYSTEM axis
-- (copies reference its id; a status change archives the outgoing state as a copy with archived_from_id, the live
-- row keeps its id). workflow_template_copies: append-only.
--
-- Provenance pointers to workflow_definitions / workflow_versions are plain uuids, NOT foreign keys: the existing
-- delete path for a never-run draft (routers.workflows.delete_workflow) must keep working, and a provenance row
-- outlives the definition it created ("copied from v2 into a workflow since deleted").
--
-- THE CUSTODY CLIFF. Nothing here trades, moves money, files or posts. A copy is a workflow DEFINITION with no
-- trigger — nothing runs it until someone with configure_workflow_triggers adds one on the existing Triggers screen.

-- ── 0. Helpers ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.workflow_template_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('workflow_templates.teardown', true), '') = 'fixture'
     AND current_user <> 'app_service'
$$;

CREATE OR REPLACE FUNCTION public.workflow_template_super() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
$$;

-- A platform template's owner is the Hollisworks platform org or an is_test fixture org — nothing else.
CREATE OR REPLACE FUNCTION public.workflow_template_platform_org_allowed(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT p_org = 'bb347258-8f28-4f49-8cc9-e29ccad82884'::uuid
      OR EXISTS (SELECT 1 FROM public.organizations o WHERE o.id = p_org AND o.is_test)
$$;

-- May the CURRENT org read platform templates owned by p_owner? A different org, an allowed owner, the same is_test
-- flag on both (rep15's provider_tenant_allowed rule).
CREATE OR REPLACE FUNCTION public.workflow_template_visible(p_owner uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT p_owner IS DISTINCT FROM (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
     AND public.workflow_template_platform_org_allowed(p_owner)
     AND EXISTS (SELECT 1 FROM public.organizations o
                  WHERE o.id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
     AND (SELECT o.is_test FROM public.organizations o WHERE o.id = p_owner)
         IS NOT DISTINCT FROM
         (SELECT o.is_test FROM public.organizations o
           WHERE o.id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
$$;

-- ── 1. Templates (identity only) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.workflow_templates (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_org_id   uuid NOT NULL REFERENCES public.organizations(id),
  scope          text NOT NULL,
  template_key   text NOT NULL,
  created_by     uuid NOT NULL REFERENCES public.users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  system_from    timestamptz NOT NULL DEFAULT now(),
  system_to      timestamptz,
  CONSTRAINT workflow_templates_scope_chk CHECK (scope IN ('platform', 'org')),
  CONSTRAINT workflow_templates_key_chk CHECK (template_key ~ '^[a-z][a-z0-9_]{2,63}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_templates_key_live_uq
  ON public.workflow_templates (owner_org_id, template_key) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.workflow_templates_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.workflow_template_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a workflow template is never deleted — retire its versions' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'a workflow template''s identity (owner, scope, key) is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.scope = 'platform' THEN
    IF NOT public.workflow_template_platform_org_allowed(NEW.owner_org_id) THEN
      RAISE EXCEPTION 'a platform workflow template belongs to the Hollisworks platform org (or a test org)'
        USING ERRCODE = '42501';
    END IF;
    IF NOT public.workflow_template_super() THEN
      RAISE EXCEPTION 'only Super Admin drafts a platform workflow template' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS workflow_templates_guard_trg ON public.workflow_templates;
CREATE TRIGGER workflow_templates_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.workflow_templates
  FOR EACH ROW EXECUTE FUNCTION public.workflow_templates_guard();

-- ── 2. Template versions (system axis; content immutable) ──────────────────
CREATE TABLE IF NOT EXISTS public.workflow_template_versions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  template_id           uuid NOT NULL REFERENCES public.workflow_templates(id),
  owner_org_id          uuid NOT NULL REFERENCES public.organizations(id),
  scope                 text NOT NULL,
  version_no            integer NOT NULL,
  status                text NOT NULL DEFAULT 'draft',
  title                 text NOT NULL,
  category              text NOT NULL,
  description           text NOT NULL,
  bpmn_xml              text NOT NULL,
  bpmn_sha256           text NOT NULL,
  steps                 jsonb NOT NULL,
  parameters            jsonb NOT NULL DEFAULT '[]'::jsonb,
  prerequisites         jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_definition_id  uuid,
  source_version_id     uuid,
  drafted_by            uuid NOT NULL REFERENCES public.users(id),
  drafted_at            timestamptz NOT NULL DEFAULT now(),
  checker_id            uuid REFERENCES public.users(id),
  published_at          timestamptz,
  rejected_at           timestamptz,
  decision_note         text,
  retired_by            uuid REFERENCES public.users(id),
  retired_at            timestamptz,
  retire_reason         text,
  archived_from_id      uuid REFERENCES public.workflow_template_versions(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT wtv_scope_chk CHECK (scope IN ('platform', 'org')),
  CONSTRAINT wtv_status_chk CHECK (status IN ('draft', 'published', 'rejected', 'retired')),
  CONSTRAINT wtv_category_chk CHECK (category IN ('onboarding', 'reviews_compliance', 'trading_rebalancing',
    'cash_transfers', 'documents', 'alternatives', 'tax', 'client_service')),
  CONSTRAINT wtv_version_no_chk CHECK (version_no >= 1),
  CONSTRAINT wtv_title_chk CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  CONSTRAINT wtv_description_chk CHECK (length(btrim(description)) BETWEEN 1 AND 8000),
  CONSTRAINT wtv_bpmn_chk CHECK (length(bpmn_xml) BETWEEN 1 AND 200000),
  CONSTRAINT wtv_sha_chk CHECK (bpmn_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT wtv_steps_chk CHECK (jsonb_typeof(steps) = 'array' AND jsonb_array_length(steps) > 0),
  CONSTRAINT wtv_parameters_chk CHECK (jsonb_typeof(parameters) = 'array'),
  CONSTRAINT wtv_prerequisites_chk CHECK (jsonb_typeof(prerequisites) = 'array'),
  -- maker-checker in the database: whoever publishes or rejects a version is not the person who drafted it
  CONSTRAINT wtv_maker_checker_chk CHECK (checker_id IS NULL OR checker_id <> drafted_by),
  -- a PLATFORM version reaches published / retired only through a checker; an ORG version is saved published
  CONSTRAINT wtv_published_chk CHECK (status NOT IN ('published', 'retired')
    OR (published_at IS NOT NULL AND (scope = 'org' OR checker_id IS NOT NULL))),
  CONSTRAINT wtv_org_no_draft_chk CHECK (scope = 'platform' OR status IN ('published', 'retired')),
  CONSTRAINT wtv_rejected_chk CHECK (status <> 'rejected' OR (checker_id IS NOT NULL AND rejected_at IS NOT NULL
    AND length(btrim(COALESCE(decision_note, ''))) > 0)),
  CONSTRAINT wtv_retired_chk CHECK (status <> 'retired' OR (retired_by IS NOT NULL AND retired_at IS NOT NULL
    AND length(btrim(COALESCE(retire_reason, ''))) > 0)),
  CONSTRAINT wtv_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_template_versions_no_uq
  ON public.workflow_template_versions (template_id, version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS workflow_template_versions_draft_uq
  ON public.workflow_template_versions (template_id) WHERE status = 'draft' AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS workflow_template_versions_owner_idx
  ON public.workflow_template_versions (owner_org_id, status) WHERE system_to IS NULL;

-- Has the template a published (or retired) version? Used by the template row's SELECT policy without recursing
-- into the versions table's own policy.
CREATE OR REPLACE FUNCTION public.workflow_template_published(p_template uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT EXISTS (SELECT 1 FROM public.workflow_template_versions v
                  WHERE v.template_id = p_template AND v.system_to IS NULL
                    AND v.status IN ('published', 'retired'))
$$;

CREATE OR REPLACE FUNCTION public.workflow_template_versions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE frozen text[] := ARRAY['template_id', 'owner_org_id', 'scope', 'version_no', 'title', 'category',
  'description', 'bpmn_xml', 'bpmn_sha256', 'steps', 'parameters', 'prerequisites', 'source_definition_id',
  'source_version_id', 'drafted_by', 'drafted_at', 'created_at'];
  k text; t record;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.workflow_template_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a workflow template version is never deleted — retire it' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.system_to IS NOT NULL THEN
      RETURN NEW;  -- an archive copy of an outgoing state (system axis)
    END IF;
    SELECT owner_org_id, scope INTO t FROM public.workflow_templates WHERE id = NEW.template_id;
    IF NOT FOUND OR t.owner_org_id IS DISTINCT FROM NEW.owner_org_id OR t.scope IS DISTINCT FROM NEW.scope THEN
      RAISE EXCEPTION 'a template version carries its template''s owner and scope' USING ERRCODE = '42501';
    END IF;
    IF NEW.scope = 'platform' THEN
      IF NEW.status <> 'draft' OR NEW.checker_id IS NOT NULL OR NEW.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'a platform template version starts as a draft (a second Super Admin publishes it)'
          USING ERRCODE = '42501';
      END IF;
      IF NOT public.workflow_template_super() THEN
        RAISE EXCEPTION 'only Super Admin drafts a platform workflow template' USING ERRCODE = '42501';
      END IF;
    ELSIF NEW.status <> 'published' OR NEW.checker_id IS NOT NULL THEN
      RAISE EXCEPTION 'an org template version is saved published, inside its own org' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  -- UPDATE
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived template version state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live template version keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  FOREACH k IN ARRAY frozen LOOP
    IF (to_jsonb(NEW) -> k) IS DISTINCT FROM (to_jsonb(OLD) -> k) THEN
      RAISE EXCEPTION 'a template version''s content is immutable (% changed)', k USING ERRCODE = '42501';
    END IF;
  END LOOP;
  IF OLD.status IN ('rejected', 'retired') THEN
    RAISE EXCEPTION 'a % template version is final', OLD.status USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'published' THEN
    IF NEW.status = 'retired' AND (to_jsonb(NEW) - ARRAY['status', 'retired_by', 'retired_at', 'retire_reason',
         'system_from']) = (to_jsonb(OLD) - ARRAY['status', 'retired_by', 'retired_at', 'retire_reason',
         'system_from']) THEN
      IF NEW.scope = 'platform' AND NOT public.workflow_template_super() THEN
        RAISE EXCEPTION 'only Super Admin retires a platform template version' USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'a published template version is immutable (it may only be retired)' USING ERRCODE = '42501';
  END IF;
  -- draft → published / rejected (platform only — an org version is never a draft)
  IF NEW.status NOT IN ('published', 'rejected') THEN
    RAISE EXCEPTION 'illegal template version transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  IF NOT public.workflow_template_super() THEN
    RAISE EXCEPTION 'only Super Admin publishes a platform workflow template' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS workflow_template_versions_guard_trg ON public.workflow_template_versions;
CREATE TRIGGER workflow_template_versions_guard_trg
  BEFORE INSERT OR UPDATE OR DELETE ON public.workflow_template_versions
  FOR EACH ROW EXECUTE FUNCTION public.workflow_template_versions_guard();

-- ── 3. Copies (provenance; append-only) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.workflow_template_copies (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL REFERENCES public.organizations(id),
  template_id             uuid NOT NULL REFERENCES public.workflow_templates(id),
  template_version_id     uuid NOT NULL REFERENCES public.workflow_template_versions(id),
  template_owner_org_id   uuid NOT NULL REFERENCES public.organizations(id),
  template_version_no     integer NOT NULL,
  workflow_definition_id  uuid NOT NULL,
  workflow_version_id     uuid NOT NULL,
  parameter_values        jsonb NOT NULL DEFAULT '{}'::jsonb,
  copied_by               uuid NOT NULL REFERENCES public.users(id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT wtc_values_chk CHECK (jsonb_typeof(parameter_values) = 'object'),
  CONSTRAINT wtc_version_no_chk CHECK (template_version_no >= 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_template_copies_definition_uq
  ON public.workflow_template_copies (workflow_definition_id);
CREATE INDEX IF NOT EXISTS workflow_template_copies_org_idx ON public.workflow_template_copies (org_id, template_id);

CREATE OR REPLACE FUNCTION public.workflow_template_copies_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v record;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.workflow_template_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a template copy record is permanent' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'a template copy record is append-only' USING ERRCODE = '42501';
  END IF;
  -- read under the INVOKER's RLS: the copying org must be able to see the version it copies
  SELECT template_id, owner_org_id, scope, status, version_no INTO v
    FROM public.workflow_template_versions WHERE id = NEW.template_version_id AND system_to IS NULL;
  IF NOT FOUND OR v.template_id IS DISTINCT FROM NEW.template_id OR v.owner_org_id IS DISTINCT FROM
     NEW.template_owner_org_id OR v.version_no IS DISTINCT FROM NEW.template_version_no THEN
    RAISE EXCEPTION 'a copy names a template version its org can read' USING ERRCODE = '23503';
  END IF;
  IF v.status <> 'published' THEN
    RAISE EXCEPTION 'only a published template version is copied (this one is %)', v.status USING ERRCODE = '42501';
  END IF;
  IF v.scope = 'org' AND v.owner_org_id IS DISTINCT FROM NEW.org_id THEN
    RAISE EXCEPTION 'an org template is copied only inside its own org' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS workflow_template_copies_guard_trg ON public.workflow_template_copies;
CREATE TRIGGER workflow_template_copies_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.workflow_template_copies
  FOR EACH ROW EXECUTE FUNCTION public.workflow_template_copies_guard();

-- Usage for the platform screen: COUNTS ONLY (copies, distinct orgs) per template version of one owner — no org id,
-- no definition, no person. Empty unless the caller is under the Super Admin GUC.
CREATE OR REPLACE FUNCTION public.workflow_template_usage(p_owner uuid)
RETURNS TABLE (template_id uuid, template_version_id uuid, copies bigint, orgs bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT c.template_id, c.template_version_id, count(*)::bigint, count(DISTINCT c.org_id)::bigint
    FROM public.workflow_template_copies c
   WHERE c.template_owner_org_id = p_owner
     AND NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
   GROUP BY c.template_id, c.template_version_id
$$;

-- ── 4. RLS — a policy for every operation of every new table ───────────────
DO $$
DECLARE t text; own text; sa text; pred text;
BEGIN
  sa := 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true''';
  own := '(NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid';

  pred := format('(owner_org_id = %s OR %s)', own, sa);
  FOREACH t IN ARRAY ARRAY['workflow_templates', 'workflow_template_versions'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
    IF t = 'workflow_template_versions' THEN
      EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING (%s OR (scope = ''platform'' AND status IN '
                     '(''published'', ''retired'') AND public.workflow_template_visible(owner_org_id)))',
                     t || '_select', t, pred);
    ELSE
      EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING (%s OR (scope = ''platform'' AND '
                     'public.workflow_template_visible(owner_org_id) AND public.workflow_template_published(id)))',
                     t || '_select', t, pred);
    END IF;
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_update', t, pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_delete', t, pred);
  END LOOP;

  t := 'workflow_template_copies';
  pred := format('(org_id = %s OR %s)', own, sa);
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_select', t, pred);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_insert', t, pred);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_update', t, pred, pred);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_delete', t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_delete', t, pred);

  FOREACH t IN ARRAY ARRAY['workflow_templates', 'workflow_template_versions', 'workflow_template_copies'] LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;

REVOKE ALL ON FUNCTION public.workflow_template_platform_org_allowed(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.workflow_template_visible(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.workflow_template_published(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.workflow_template_usage(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.workflow_template_platform_org_allowed(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION public.workflow_template_visible(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION public.workflow_template_published(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION public.workflow_template_usage(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION public.workflow_template_teardown_allowed() TO app_service;
GRANT EXECUTE ON FUNCTION public.workflow_template_super() TO app_service;
