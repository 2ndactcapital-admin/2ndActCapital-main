-- REP15 — Replication Studio phase 4 ends: PLATFORM-PUBLISHED MODELS AND TENANT SUBSCRIPTIONS.
--
-- A provider org (the Hollisworks platform org — or, in a verify, an is_test fixture org) publishes models as a
-- sub-adviser; tenant orgs subscribe, use a subscribed model's versions in their accounts, and are billed a
-- model-provider fee. EVERYTHING sits behind a PUBLISHING GATE that is OFF (no row = OFF) and can be opened only by
-- Super Admin after the registered adviser entity (legal name, SEC file / CRD number, registration status,
-- effective date) is recorded and its ADV Part 2A is a PUBLISHED disclosure-library version
-- (key model_provider.adv_2a, the provider org's own version).
--
-- Applied by apps/api/scripts/_apply_rep15.py --schema on the operator DSN (role postgres) in ONE transaction.
-- Idempotent. THE MIGRATION WRITES NO ROWS (no gate, no provider, no catalog row — every new table starts empty).
--
-- THE DATABASE BACKS THE GATE, not only the service:
--   * a catalog version can be INSERTED or PUBLISHED only while portfolio.model_publishing_open(provider) is true;
--   * a subscription can be REQUESTED or ACTIVATED only while it is open, and activation needs app.is_super_admin;
--   * a gate row can be OPENED only under app.is_super_admin, with a complete provider record and a published ADV;
--   * a provider org must be the Hollisworks platform org or an is_test org, and a tenant's is_test must match its
--     provider's — so a FIXTURE provider can never serve a REAL tenant (and vice versa).
--
-- ISOLATION. Tenants never read the provider's models / versions / positions (rep3's platform-shelf carve-out is
-- NOT used): they read portfolio.catalog_versions — a frozen snapshot of the model's TARGET WEIGHTS only — and only
-- while the provider's gate is open. The provider never reads a tenant's subscriptions, accounts, positions or
-- people: it reads two SECURITY DEFINER functions that return status, account COUNTS and model ASSETS per tenant per
-- catalog version, nothing else.
--
-- BI-TEMPORAL (Rule 3). model_providers, model_publishing_gates, catalog_models, catalog_versions,
-- provider_subscriptions: SYSTEM axis (other rows reference their ids; the live row keeps its id, the outgoing state
-- is copied with archived_from_id). provider_fee_accruals: VALID axis (a correction closes valid_to and inserts the
-- successor). Invoices, invoice lines and the event log: append-only. The GL is the existing posting API
-- (fn_post_journal_entry / fn_reverse_journal_entry) — a correction is a REVERSAL, never an edit.
--
-- THE CUSTODY CLIFF. Nothing here trades or moves money. Adopting a version only approves a model version in the
-- tenant's registry (rep3), which yields rebalance PROPOSALS (rep5).

-- ── 0. Shared helpers ──────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION portfolio.provider_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('models.teardown', true), '') = 'fixture'
     AND current_user <> 'app_service'
$$;

-- A provider org is the Hollisworks platform org or an is_test fixture org — nothing else.
CREATE OR REPLACE FUNCTION portfolio.model_provider_org_allowed(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT p_org = 'bb347258-8f28-4f49-8cc9-e29ccad82884'::uuid
      OR EXISTS (SELECT 1 FROM public.organizations o WHERE o.id = p_org AND o.is_test)
$$;

-- A tenant may deal with a provider only if it is a different org with the SAME is_test flag.
CREATE OR REPLACE FUNCTION portfolio.provider_tenant_allowed(p_provider uuid, p_tenant uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT p_provider IS DISTINCT FROM p_tenant
     AND portfolio.model_provider_org_allowed(p_provider)
     AND (SELECT o.is_test FROM public.organizations o WHERE o.id = p_provider)
         IS NOT DISTINCT FROM (SELECT o.is_test FROM public.organizations o WHERE o.id = p_tenant)
     AND EXISTS (SELECT 1 FROM public.organizations o WHERE o.id = p_tenant)
$$;

-- ── 1. The provider record (PLATFORM setting, system axis) ─────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_providers (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_org_id      uuid NOT NULL REFERENCES public.organizations(id),
  legal_name           text,
  sec_file_number      text,
  crd_number           text,
  registration_status  text NOT NULL DEFAULT 'not_registered',
  effective_date       date,
  adv_key              text NOT NULL DEFAULT 'model_provider.adv_2a',
  ledger_org_id        uuid NOT NULL REFERENCES public.organizations(id),
  notes                text,
  created_by           uuid NOT NULL REFERENCES public.users(id),
  updated_by           uuid NOT NULL REFERENCES public.users(id),
  archived_from_id     uuid REFERENCES portfolio.model_providers(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT model_providers_status_chk CHECK (registration_status IN ('not_registered', 'pending', 'registered',
                                                                       'withdrawn')),
  CONSTRAINT model_providers_sec_chk CHECK (sec_file_number IS NULL OR sec_file_number ~ '^80[12]-[0-9]{4,6}$'),
  CONSTRAINT model_providers_crd_chk CHECK (crd_number IS NULL OR crd_number ~ '^[0-9]{1,8}$'),
  CONSTRAINT model_providers_name_chk CHECK (legal_name IS NULL OR length(btrim(legal_name)) BETWEEN 2 AND 200),
  CONSTRAINT model_providers_adv_key_chk CHECK (adv_key = 'model_provider.adv_2a'),
  CONSTRAINT model_providers_registered_chk CHECK (registration_status <> 'registered' OR (
    legal_name IS NOT NULL AND effective_date IS NOT NULL AND (sec_file_number IS NOT NULL OR crd_number IS NOT NULL))),
  CONSTRAINT model_providers_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_providers_live_uq
  ON portfolio.model_providers (provider_org_id) WHERE system_to IS NULL;

-- Complete = registered, named, numbered, effective on or before today.
CREATE OR REPLACE FUNCTION portfolio.model_provider_complete(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, portfolio AS $$
  SELECT EXISTS (SELECT 1 FROM portfolio.model_providers p
                 WHERE p.provider_org_id = p_org AND p.system_to IS NULL AND p.registration_status = 'registered'
                   AND p.legal_name IS NOT NULL AND p.effective_date IS NOT NULL AND p.effective_date <= current_date
                   AND (p.sec_file_number IS NOT NULL OR p.crd_number IS NOT NULL))
$$;

CREATE OR REPLACE FUNCTION portfolio.model_providers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.provider_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a model provider record is never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.system_to IS NOT NULL THEN
      RAISE EXCEPTION 'an archived provider record state is immutable' USING ERRCODE = '42501';
    END IF;
    IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
       OR NEW.provider_org_id IS DISTINCT FROM OLD.provider_org_id THEN
      RAISE EXCEPTION 'the live provider record keeps its id and its org; archive by copying it' USING ERRCODE = '42501';
    END IF;
  END IF;
  IF NEW.system_to IS NULL AND NOT portfolio.model_provider_org_allowed(NEW.provider_org_id) THEN
    RAISE EXCEPTION 'a model provider is the Hollisworks platform org (or a test org) — not org %', NEW.provider_org_id
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS model_providers_guard_trg ON portfolio.model_providers;
CREATE TRIGGER model_providers_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.model_providers
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_providers_guard();

-- ── 2. The publishing gate (PLATFORM setting, system axis; no row = OFF) ───
CREATE TABLE IF NOT EXISTS portfolio.model_publishing_gates (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_org_id     uuid NOT NULL REFERENCES public.organizations(id),
  is_open             boolean NOT NULL DEFAULT false,
  provider_record_id  uuid REFERENCES portfolio.model_providers(id),
  provider_snapshot   jsonb NOT NULL DEFAULT '{}'::jsonb,
  adv_version_id      uuid,
  adv_sha256          text,
  changed_by          uuid NOT NULL REFERENCES public.users(id),
  changed_at          timestamptz NOT NULL DEFAULT now(),
  reason              text NOT NULL,
  archived_from_id    uuid REFERENCES portfolio.model_publishing_gates(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT model_publishing_gates_adv_fk FOREIGN KEY (adv_version_id, adv_sha256)
    REFERENCES public.disclosure_versions (id, content_sha256),
  CONSTRAINT model_publishing_gates_open_chk CHECK (NOT is_open OR (provider_record_id IS NOT NULL
    AND adv_version_id IS NOT NULL AND adv_sha256 IS NOT NULL)),
  CONSTRAINT model_publishing_gates_reason_chk CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
  CONSTRAINT model_publishing_gates_snapshot_chk CHECK (jsonb_typeof(provider_snapshot) = 'object'),
  CONSTRAINT model_publishing_gates_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_publishing_gates_live_uq
  ON portfolio.model_publishing_gates (provider_org_id) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.model_publishing_open(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, portfolio AS $$
  SELECT EXISTS (SELECT 1 FROM portfolio.model_publishing_gates g
                 WHERE g.provider_org_id = p_org AND g.system_to IS NULL AND g.is_open)
$$;

-- Opening: Super Admin (the GUC), a complete provider record, the provider's OWN published ADV of the right key.
CREATE OR REPLACE FUNCTION portfolio.model_publishing_gates_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, portfolio AS $$
DECLARE ok boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.provider_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a publishing gate record is never deleted — close the gate instead' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.system_to IS NOT NULL THEN
      RAISE EXCEPTION 'an archived publishing gate state is immutable' USING ERRCODE = '42501';
    END IF;
    IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
       OR NEW.provider_org_id IS DISTINCT FROM OLD.provider_org_id THEN
      RAISE EXCEPTION 'the live gate row keeps its id and its org; archive by copying it' USING ERRCODE = '42501';
    END IF;
  END IF;
  IF NEW.system_to IS NOT NULL THEN
    RETURN NEW;            -- an archived copy of the outgoing state
  END IF;
  IF NOT portfolio.model_provider_org_allowed(NEW.provider_org_id) THEN
    RAISE EXCEPTION 'a publishing gate belongs to the Hollisworks platform org (or a test org)' USING ERRCODE = '42501';
  END IF;
  IF NEW.is_open THEN
    IF COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), '') <> 'true' THEN
      RAISE EXCEPTION 'only Super Admin opens the model publishing gate' USING ERRCODE = '42501';
    END IF;
    IF NOT portfolio.model_provider_complete(NEW.provider_org_id) THEN
      RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required (the provider record '
                      'is incomplete)' USING ERRCODE = '42501';
    END IF;
    SELECT EXISTS (SELECT 1 FROM public.disclosure_versions v JOIN public.disclosures d ON d.id = v.disclosure_id
                   JOIN portfolio.model_providers p ON p.id = NEW.provider_record_id AND p.system_to IS NULL
                   WHERE v.id = NEW.adv_version_id AND v.content_sha256 = NEW.adv_sha256 AND v.system_to IS NULL
                     AND v.status = 'published' AND v.published_by IS NOT NULL AND v.scope = 'org'
                     AND v.org_id = NEW.provider_org_id AND d.key = p.adv_key AND d.system_to IS NULL
                     AND p.provider_org_id = NEW.provider_org_id
                     AND v.effective_from <= current_date
                     AND (v.effective_to IS NULL OR current_date < v.effective_to)) INTO ok;
    IF NOT ok THEN
      RAISE EXCEPTION 'model publishing is not enabled — the provider''s ADV Part 2A (model_provider.adv_2a) must be a '
                      'published library version in force' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS model_publishing_gates_guard_trg ON portfolio.model_publishing_gates;
CREATE TRIGGER model_publishing_gates_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.model_publishing_gates
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_publishing_gates_guard();

-- ── 3. Publishable models (PROVIDER org, system axis) ──────────────────────
CREATE TABLE IF NOT EXISTS portfolio.catalog_models (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_org_id     uuid NOT NULL REFERENCES public.organizations(id),
  model_id            uuid NOT NULL REFERENCES portfolio.models(id),
  status              text NOT NULL DEFAULT 'publishable',
  default_fee_bps     numeric(9, 4) NOT NULL,
  construction_notes  text,
  created_by          uuid NOT NULL REFERENCES public.users(id),
  status_changed_by   uuid REFERENCES public.users(id),
  status_changed_at   timestamptz,
  status_reason       text,
  archived_from_id    uuid REFERENCES portfolio.catalog_models(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT catalog_models_status_chk CHECK (status IN ('publishable', 'withdrawn')),
  CONSTRAINT catalog_models_fee_chk CHECK (default_fee_bps > 0 AND default_fee_bps <= 300),
  CONSTRAINT catalog_models_withdrawn_chk CHECK (status <> 'withdrawn' OR (status_changed_by IS NOT NULL
    AND status_changed_at IS NOT NULL AND length(btrim(COALESCE(status_reason, ''))) > 0)),
  CONSTRAINT catalog_models_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS catalog_models_model_live_uq
  ON portfolio.catalog_models (model_id) WHERE system_to IS NULL;

-- ── 4. Published catalog versions (PROVIDER org; immutable once published) ─
CREATE TABLE IF NOT EXISTS portfolio.catalog_versions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_org_id      uuid NOT NULL REFERENCES public.organizations(id),
  catalog_model_id     uuid NOT NULL REFERENCES portfolio.catalog_models(id),
  model_id             uuid NOT NULL REFERENCES portfolio.models(id),
  model_version_id     uuid NOT NULL REFERENCES portfolio.model_versions(id),
  catalog_version_no   integer NOT NULL,
  status               text NOT NULL DEFAULT 'pending',
  name                 text NOT NULL,
  description          text NOT NULL,
  kind                 text NOT NULL,
  construction_notes   text NOT NULL,
  holdings_as_of       date NOT NULL,
  fee_bps              numeric(9, 4) NOT NULL,
  provider_legal_name  text NOT NULL,
  weights              jsonb NOT NULL,
  weights_sha256       text NOT NULL,
  version_maker_ids    uuid[] NOT NULL DEFAULT '{}',
  requested_by         uuid NOT NULL REFERENCES public.users(id),
  requested_at         timestamptz NOT NULL DEFAULT now(),
  approval_task_id     uuid REFERENCES public.workflow_run_steps(id),
  checker_id           uuid REFERENCES public.users(id),
  published_at         timestamptz,
  decision_note        text,
  rejected_at          timestamptz,
  retired_by           uuid REFERENCES public.users(id),
  retired_at           timestamptz,
  retire_reason        text,
  archived_from_id     uuid REFERENCES portfolio.catalog_versions(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT catalog_versions_status_chk CHECK (status IN ('pending', 'published', 'rejected', 'retired')),
  -- Joe's naming rule: every model name starts "Holdings of"; the description says "replicates the holdings of …"
  CONSTRAINT catalog_versions_name_chk CHECK (lower(btrim(name)) LIKE 'holdings of %' AND length(btrim(name)) <= 200),
  CONSTRAINT catalog_versions_description_chk CHECK (lower(description) LIKE '%replicates the holdings of %'),
  CONSTRAINT catalog_versions_kind_chk CHECK (kind IN ('long_only', 'long_short')),
  CONSTRAINT catalog_versions_notes_chk CHECK (length(btrim(construction_notes)) BETWEEN 1 AND 4000),
  CONSTRAINT catalog_versions_fee_chk CHECK (fee_bps > 0 AND fee_bps <= 300),
  CONSTRAINT catalog_versions_no_chk CHECK (catalog_version_no >= 1),
  CONSTRAINT catalog_versions_weights_chk CHECK (jsonb_typeof(weights) = 'array' AND jsonb_array_length(weights) > 0),
  CONSTRAINT catalog_versions_sha_chk CHECK (weights_sha256 ~ '^[0-9a-f]{64}$'),
  -- maker-checker in the database: the publisher is neither the requester nor anyone who made the model version
  CONSTRAINT catalog_versions_maker_checker_chk CHECK (checker_id IS NULL OR (checker_id <> requested_by
    AND NOT (checker_id = ANY (version_maker_ids)))),
  CONSTRAINT catalog_versions_pending_chk CHECK (status <> 'pending' OR approval_task_id IS NOT NULL),
  CONSTRAINT catalog_versions_published_chk CHECK (status NOT IN ('published', 'retired')
    OR (checker_id IS NOT NULL AND published_at IS NOT NULL)),
  CONSTRAINT catalog_versions_rejected_chk CHECK (status <> 'rejected' OR (checker_id IS NOT NULL
    AND rejected_at IS NOT NULL AND length(btrim(COALESCE(decision_note, ''))) > 0)),
  CONSTRAINT catalog_versions_retired_chk CHECK (status <> 'retired' OR (retired_by IS NOT NULL AND retired_at IS NOT NULL
    AND length(btrim(COALESCE(retire_reason, ''))) > 0)),
  CONSTRAINT catalog_versions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS catalog_versions_model_version_uq
  ON portfolio.catalog_versions (model_version_id) WHERE status IN ('pending', 'published', 'retired') AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS catalog_versions_no_uq
  ON portfolio.catalog_versions (catalog_model_id, catalog_version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS catalog_versions_pending_uq
  ON portfolio.catalog_versions (catalog_model_id) WHERE status = 'pending' AND system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.catalog_versions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE frozen text[] := ARRAY['provider_org_id', 'catalog_model_id', 'model_id', 'model_version_id',
  'catalog_version_no', 'name', 'description', 'kind', 'construction_notes', 'holdings_as_of', 'fee_bps',
  'provider_legal_name', 'weights', 'weights_sha256', 'version_maker_ids', 'requested_by', 'requested_at'];
  k text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.provider_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a catalog version is never deleted — retire it' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.system_to IS NULL THEN
      IF NEW.status <> 'pending' THEN
        RAISE EXCEPTION 'a catalog version starts pending (maker-checker publishes it)' USING ERRCODE = '42501';
      END IF;
      IF NOT portfolio.model_publishing_open(NEW.provider_org_id) THEN
        RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required'
          USING ERRCODE = '42501';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived catalog version state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live catalog version keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  FOREACH k IN ARRAY frozen LOOP
    IF (to_jsonb(NEW) -> k) IS DISTINCT FROM (to_jsonb(OLD) -> k) THEN
      RAISE EXCEPTION 'a catalog version''s content is immutable (% changed)', k USING ERRCODE = '42501';
    END IF;
  END LOOP;
  IF OLD.status IN ('rejected', 'retired') THEN
    RAISE EXCEPTION 'a % catalog version is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'published' THEN
    IF NEW.status = 'retired' AND (to_jsonb(NEW) - ARRAY['status', 'retired_by', 'retired_at', 'retire_reason',
         'system_from']) = (to_jsonb(OLD) - ARRAY['status', 'retired_by', 'retired_at', 'retire_reason',
         'system_from']) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'a published catalog version is immutable (it may only be retired)' USING ERRCODE = '42501';
  END IF;
  -- pending → published / rejected
  IF NEW.status NOT IN ('pending', 'published', 'rejected') THEN
    RAISE EXCEPTION 'illegal catalog version transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  IF NEW.status = 'published' AND NOT portfolio.model_publishing_open(NEW.provider_org_id) THEN
    RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS catalog_versions_guard_trg ON portfolio.catalog_versions;
CREATE TRIGGER catalog_versions_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.catalog_versions
  FOR EACH ROW EXECUTE FUNCTION portfolio.catalog_versions_guard();

-- A tenant reads the catalog only while the provider's gate is open, and only from a provider of its own kind.
CREATE OR REPLACE FUNCTION portfolio.catalog_visible(p_provider uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, portfolio AS $$
  SELECT portfolio.model_publishing_open(p_provider)
     AND portfolio.provider_tenant_allowed(p_provider,
                                           (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
$$;

-- While a provider's gate is closed its catalog is invisible to tenants (RLS). So that a tenant's refusal names the
-- gate (not a bare 404), this returns ONLY the provider org of a catalog model — and only to a tenant that provider
-- could serve. Nothing else of the provider's is readable through it.
CREATE OR REPLACE FUNCTION portfolio.catalog_model_provider(p_catalog_model uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, portfolio AS $$
  SELECT c.provider_org_id FROM portfolio.catalog_models c
   WHERE c.id = p_catalog_model AND c.system_to IS NULL
     AND portfolio.provider_tenant_allowed(c.provider_org_id,
                                           (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
$$;
REVOKE ALL ON FUNCTION portfolio.catalog_model_provider(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION portfolio.catalog_model_provider(uuid) TO app_service;

-- ── 5. Subscriptions (TENANT org, system axis) ─────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.provider_subscriptions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  provider_org_id       uuid NOT NULL REFERENCES public.organizations(id),
  catalog_model_id      uuid NOT NULL REFERENCES portfolio.catalog_models(id),
  status                text NOT NULL DEFAULT 'requested',
  mirror_model_id       uuid REFERENCES portfolio.models(id),
  agreement_version_id  uuid NOT NULL,
  agreement_sha256      text NOT NULL,
  agreement_scope       text NOT NULL,
  acknowledgement_id    uuid NOT NULL REFERENCES public.disclosure_acknowledgements(id),
  requested_by          uuid NOT NULL REFERENCES public.users(id),
  requested_at          timestamptz NOT NULL DEFAULT now(),
  approval_task_id      uuid REFERENCES public.workflow_run_steps(id),
  approved_by           uuid REFERENCES public.users(id),
  approved_at           timestamptz,
  decision_note         text,
  started_on            date,
  suspended_by          uuid REFERENCES public.users(id),
  suspended_at          timestamptz,
  suspend_reason        text,
  ended_by              uuid REFERENCES public.users(id),
  ended_at              timestamptz,
  ended_on              date,
  end_reason            text,
  archived_from_id      uuid REFERENCES portfolio.provider_subscriptions(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT provider_subscriptions_agreement_fk FOREIGN KEY (agreement_version_id, agreement_sha256)
    REFERENCES public.disclosure_versions (id, content_sha256),
  CONSTRAINT provider_subscriptions_status_chk CHECK (status IN ('requested', 'active', 'suspended', 'ended',
                                                                 'rejected')),
  CONSTRAINT provider_subscriptions_scope_chk CHECK (agreement_scope IN ('platform', 'org')),
  CONSTRAINT provider_subscriptions_orgs_chk CHECK (org_id <> provider_org_id),
  -- maker-checker ACROSS the two orgs: the platform-side approver is never the tenant's requester
  CONSTRAINT provider_subscriptions_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> requested_by),
  CONSTRAINT provider_subscriptions_requested_chk CHECK (status <> 'requested' OR approval_task_id IS NOT NULL
                                                         OR system_to IS NOT NULL),
  CONSTRAINT provider_subscriptions_active_chk CHECK (status NOT IN ('active', 'suspended') OR (approved_by IS NOT NULL
    AND approved_at IS NOT NULL AND started_on IS NOT NULL AND mirror_model_id IS NOT NULL)),
  CONSTRAINT provider_subscriptions_suspended_chk CHECK (status <> 'suspended' OR (suspended_by IS NOT NULL
    AND suspended_at IS NOT NULL AND length(btrim(COALESCE(suspend_reason, ''))) > 0)),
  CONSTRAINT provider_subscriptions_ended_chk CHECK (status <> 'ended' OR (ended_by IS NOT NULL AND ended_at IS NOT NULL
    AND ended_on IS NOT NULL AND length(btrim(COALESCE(end_reason, ''))) > 0)),
  CONSTRAINT provider_subscriptions_rejected_chk CHECK (status <> 'rejected' OR (approved_by IS NULL
    AND length(btrim(COALESCE(decision_note, ''))) > 0)),
  CONSTRAINT provider_subscriptions_dates_chk CHECK (ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on),
  CONSTRAINT provider_subscriptions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS provider_subscriptions_open_uq
  ON portfolio.provider_subscriptions (org_id, catalog_model_id)
  WHERE status IN ('requested', 'active', 'suspended') AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS provider_subscriptions_provider_idx
  ON portfolio.provider_subscriptions (provider_org_id, status) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.provider_subscriptions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.provider_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a subscription is never deleted — end it' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.system_to IS NULL THEN
      IF NEW.status <> 'requested' THEN
        RAISE EXCEPTION 'a subscription starts as a request' USING ERRCODE = '42501';
      END IF;
      IF NOT portfolio.provider_tenant_allowed(NEW.provider_org_id, NEW.org_id) THEN
        RAISE EXCEPTION 'this org may not subscribe to that provider (a test provider serves only test orgs)'
          USING ERRCODE = '42501';
      END IF;
      IF NOT portfolio.model_publishing_open(NEW.provider_org_id) THEN
        RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required'
          USING ERRCODE = '42501';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived subscription state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
     OR NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.provider_org_id IS DISTINCT FROM OLD.provider_org_id
     OR NEW.catalog_model_id IS DISTINCT FROM OLD.catalog_model_id
     OR NEW.agreement_version_id IS DISTINCT FROM OLD.agreement_version_id
     OR NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
    RAISE EXCEPTION 'a subscription keeps its id, its orgs, its model, its agreement and its requester'
      USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('ended', 'rejected') THEN
    RAISE EXCEPTION 'an % subscription is final', OLD.status USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY['requested>active', 'requested>rejected', 'requested>ended',
      'active>suspended', 'active>ended', 'suspended>active', 'suspended>ended'])) THEN
    RAISE EXCEPTION 'illegal subscription transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  IF NEW.status = 'active' AND OLD.status <> 'active' THEN
    IF COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), '') <> 'true' THEN
      RAISE EXCEPTION 'a platform Super Admin approves a subscription' USING ERRCODE = '42501';
    END IF;
    IF NOT portfolio.model_publishing_open(NEW.provider_org_id) THEN
      RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required'
        USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS provider_subscriptions_guard_trg ON portfolio.provider_subscriptions;
CREATE TRIGGER provider_subscriptions_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.provider_subscriptions
  FOR EACH ROW EXECUTE FUNCTION portfolio.provider_subscriptions_guard();

-- ── 6. The registry learns two sources: 'provided' (read-only mirror) and 'provided_copy' ─
-- No FOREIGN KEY on these two columns, deliberately: models → provider_subscriptions → catalog_models → models (and
-- models → catalog_versions → model_versions → models) would be table-level FK CYCLES, which no child-first delete
-- (the fixture teardown, a future org purge) can order. The trigger models_provided_refs_guard below checks both
-- references on every insert / update instead (same org, the right source, a live row).
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS provided_subscription_id uuid;
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS copied_from_catalog_version_id uuid;
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_provided_subscription_id_fkey;
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_copied_from_catalog_version_id_fkey;
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_source_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_source_chk CHECK (source IN ('upload', 'library_copy',
  'library_subscription', 'fund_replica', 'composite', 'provided', 'provided_copy'));
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_reserved_name_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_reserved_name_chk CHECK (source IN ('fund_replica', 'provided',
  'provided_copy') OR kind = 'long_short' OR lower(btrim(name)) NOT LIKE 'holdings of%');
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_provided_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_provided_chk CHECK (
  (source = 'provided') = (provided_subscription_id IS NOT NULL)
  AND (source = 'provided_copy') = (copied_from_catalog_version_id IS NOT NULL)
  AND (source NOT IN ('provided', 'provided_copy') OR lower(btrim(name)) LIKE 'holdings of %'));
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_long_short_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_long_short_chk CHECK (
  ((kind = 'long_only') AND (gross_long IS NULL) AND (gross_short IS NULL) AND (signal_source IS NULL)
   AND (signal_as_of IS NULL) AND (short_exclusions = '[]'::jsonb))
  OR ((kind = 'long_short') AND (source IN ('upload', 'provided', 'provided_copy')) AND (gross_short > 0)
      AND (gross_short <= 0.5) AND (gross_long = (1 + gross_short))
      AND (length(btrim(COALESCE(signal_source, ''))) > 0) AND (signal_as_of IS NOT NULL)
      AND (jsonb_typeof(short_exclusions) = 'array')));
CREATE OR REPLACE FUNCTION portfolio.models_provided_refs_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, portfolio AS $$
BEGIN
  IF NEW.system_to IS NOT NULL THEN
    RETURN NEW;            -- an archived copy repeats the live row's (already checked) references
  END IF;
  IF NEW.provided_subscription_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM portfolio.provider_subscriptions s WHERE s.id = NEW.provided_subscription_id
          AND s.org_id = NEW.org_id AND s.system_to IS NULL) THEN
    RAISE EXCEPTION 'a provided model links to a live subscription of its own org' USING ERRCODE = '23503';
  END IF;
  IF NEW.copied_from_catalog_version_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM portfolio.catalog_versions c WHERE c.id = NEW.copied_from_catalog_version_id
          AND c.status IN ('published', 'retired') AND c.system_to IS NULL) THEN
    RAISE EXCEPTION 'a copy names a published catalog version' USING ERRCODE = '23503';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS models_provided_refs_trg ON portfolio.models;
CREATE TRIGGER models_provided_refs_trg BEFORE INSERT OR UPDATE ON portfolio.models
  FOR EACH ROW EXECUTE FUNCTION portfolio.models_provided_refs_guard();
CREATE UNIQUE INDEX IF NOT EXISTS models_provided_live_uq
  ON portfolio.models (provided_subscription_id) WHERE provided_subscription_id IS NOT NULL AND system_to IS NULL;

ALTER TABLE portfolio.model_events DROP CONSTRAINT IF EXISTS model_events_action_chk;
ALTER TABLE portfolio.model_events ADD CONSTRAINT model_events_action_chk CHECK (action IN (
  'create_model', 'edit_model', 'create_draft', 'edit_positions', 'edit_sleeves', 'submit', 'approve', 'reject',
  'abandon', 'save_mapping', 'publish', 'unpublish', 'retire_entry', 'agreement_confirmed', 'copy', 'subscribe',
  'subscription_draft', 'composite_follow_draft', 'follow_skipped', 'fund_build_joined', 'holdings_monitor',
  'terms_monitor', 'terms_review_done', 'provided_link', 'provided_version_draft', 'provided_copy',
  'provided_preapproved'));
ALTER TABLE portfolio.model_events DROP CONSTRAINT IF EXISTS model_events_human_chk;
ALTER TABLE portfolio.model_events ADD CONSTRAINT model_events_human_chk CHECK (actor_id IS NOT NULL OR action IN (
  'subscription_draft', 'composite_follow_draft', 'follow_skipped', 'holdings_monitor', 'terms_monitor',
  'provided_version_draft', 'provided_link'));

-- A provided (mirror) model is READ-ONLY to its tenant: its versions are only ever the platform's imports
-- (origin 'subscription'), and its rows are written only by the import path (GUC models.provided_import = 'on',
-- set LOCAL by services.model_provider for exactly that statement's transaction).
CREATE OR REPLACE FUNCTION portfolio.provided_model_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE src text; vid uuid; mid uuid;
BEGIN
  -- rep15 fix 1: one branch per table. PL/pgSQL resolves EVERY NEW.<field> in an expression against the firing
  -- table's row type, so a single CASE naming both parent_version_id and version_id raised 42703 ("record new has
  -- no field ...") on EVERY insert into model_version_positions / model_sleeves — every org's upload confirm.
  IF TG_TABLE_NAME = 'model_versions' THEN
    mid := NEW.model_id;
  ELSIF TG_TABLE_NAME = 'model_sleeves' THEN
    vid := NEW.parent_version_id;
    SELECT model_id INTO mid FROM portfolio.model_versions WHERE id = vid AND system_to IS NULL;
  ELSE
    vid := NEW.version_id;
    SELECT model_id INTO mid FROM portfolio.model_versions WHERE id = vid AND system_to IS NULL;
  END IF;
  SELECT source INTO src FROM portfolio.models WHERE id = mid AND system_to IS NULL;
  IF src IS DISTINCT FROM 'provided' THEN
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'model_versions' THEN
    IF TG_OP = 'INSERT' AND NEW.system_to IS NULL AND NEW.origin <> 'subscription' THEN
      RAISE EXCEPTION 'a provided model is read-only — its versions come only from the provider (copy it to change it)'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'model_sleeves' THEN
    RAISE EXCEPTION 'a provided model has no sleeves of its own' USING ERRCODE = '42501';
  END IF;
  IF COALESCE(current_setting('models.provided_import', true), '') <> 'on' THEN
    RAISE EXCEPTION 'a provided model is read-only — its positions come only from the provider (copy it to change it)'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS model_versions_provided_trg ON portfolio.model_versions;
CREATE TRIGGER model_versions_provided_trg BEFORE INSERT ON portfolio.model_versions
  FOR EACH ROW EXECUTE FUNCTION portfolio.provided_model_guard();
DROP TRIGGER IF EXISTS model_version_positions_provided_trg ON portfolio.model_version_positions;
CREATE TRIGGER model_version_positions_provided_trg BEFORE INSERT OR UPDATE ON portfolio.model_version_positions
  FOR EACH ROW EXECUTE FUNCTION portfolio.provided_model_guard();
DROP TRIGGER IF EXISTS model_sleeves_provided_trg ON portfolio.model_sleeves;
CREATE TRIGGER model_sleeves_provided_trg BEFORE INSERT OR UPDATE ON portfolio.model_sleeves
  FOR EACH ROW EXECUTE FUNCTION portfolio.provided_model_guard();

-- ── 7. What the PROVIDER may read of its tenants — status, counts, assets, nothing else ─
-- Both functions answer only for the caller's OWN provider org (or Super Admin) and return no member, account,
-- position, household or person id or name. The tenant's org NAME is returned (it is the counterparty).
CREATE OR REPLACE FUNCTION portfolio.provider_subscription_status(p_provider uuid)
RETURNS TABLE (subscription_id uuid, tenant_org_id uuid, tenant_name text, catalog_model_id uuid, status text,
               requested_at timestamptz, started_on date, ended_on date, suspended_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, portfolio AS $$
  SELECT s.id, s.org_id, o.name, s.catalog_model_id, s.status, s.requested_at, s.started_on, s.ended_on,
         s.suspended_at
    FROM portfolio.provider_subscriptions s JOIN public.organizations o ON o.id = s.org_id
   WHERE s.provider_org_id = p_provider AND s.system_to IS NULL
     AND (p_provider = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
          OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
   ORDER BY o.name, s.requested_at
$$;

-- THE ASSET DEFINITION (one place): on day d, for each tenant account linked to the subscription's mirror model
-- on d (a link row active / superseded / ended whose joined_at <= end of d and that had not ended / been
-- superseded by the start of d), the account's MODEL ASSETS = the sum of market_value of its live position rows,
-- each asset at its latest as_of_date <= d (cash positions included; account_balances_daily cash excluded). The
-- account's catalog version on d = the mirror version live (approved at or before the end of d) on d.
CREATE OR REPLACE FUNCTION portfolio.provider_model_usage(p_provider uuid, p_day date)
RETURNS TABLE (tenant_org_id uuid, subscription_id uuid, catalog_model_id uuid, catalog_version_id uuid,
               account_count integer, model_assets numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, portfolio AS $$
  WITH caller AS (
    SELECT (p_provider = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
            OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true') AS ok
  ), subs AS (
    SELECT s.id, s.org_id, s.catalog_model_id, s.mirror_model_id
      FROM portfolio.provider_subscriptions s, caller
     WHERE caller.ok AND s.provider_org_id = p_provider AND s.system_to IS NULL
       AND s.mirror_model_id IS NOT NULL AND s.started_on <= p_day AND (s.ended_on IS NULL OR s.ended_on > p_day)
  ), accts AS (
    SELECT DISTINCT s.id AS sub_id, l.account_id
      FROM subs s JOIN public.account_model_links l ON l.org_id = s.org_id AND l.model_id = s.mirror_model_id
     WHERE l.system_to IS NULL AND l.status IN ('active', 'superseded', 'ended') AND l.joined_at IS NOT NULL
       AND l.joined_at < (p_day + 1)::timestamptz
       AND COALESCE(l.ended_at, l.valid_to, 'infinity'::timestamptz) > p_day::timestamptz
  ), ver AS (
    SELECT s.id AS sub_id, (SELECT cv.id FROM portfolio.model_versions v
                              JOIN portfolio.catalog_versions cv ON cv.model_version_id = v.origin_ref
                                AND cv.system_to IS NULL AND cv.provider_org_id = p_provider
                             WHERE v.model_id = s.mirror_model_id AND v.system_to IS NULL
                               AND v.status IN ('approved', 'superseded') AND v.approved_at < (p_day + 1)::timestamptz
                             ORDER BY v.approved_at DESC LIMIT 1) AS cv_id
      FROM subs s
  ), mv AS (
    SELECT a.sub_id, a.account_id, COALESCE(sum(p.market_value), 0) AS assets
      FROM accts a
      LEFT JOIN LATERAL (
        SELECT DISTINCT ON (pp.asset_id) pp.market_value
          FROM portfolio.positions pp
         WHERE pp.account_id = a.account_id AND pp.valid_to IS NULL AND pp.system_to IS NULL
           AND pp.as_of_date <= p_day
         ORDER BY pp.asset_id, pp.as_of_date DESC) p ON true
     GROUP BY a.sub_id, a.account_id
  )
  SELECT s.org_id, s.id, s.catalog_model_id, v.cv_id, count(mv.account_id)::integer,
         COALESCE(sum(mv.assets), 0)::numeric
    FROM subs s JOIN ver v ON v.sub_id = s.id LEFT JOIN mv ON mv.sub_id = s.id
   WHERE v.cv_id IS NOT NULL
   GROUP BY s.org_id, s.id, s.catalog_model_id, v.cv_id
$$;

-- ── 8. The fee: daily accruals (VALID axis), invoices + lines (append-only) ─
CREATE TABLE IF NOT EXISTS portfolio.provider_fee_accruals (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_org_id     uuid NOT NULL REFERENCES public.organizations(id),
  tenant_org_id       uuid NOT NULL REFERENCES public.organizations(id),
  subscription_id     uuid NOT NULL REFERENCES portfolio.provider_subscriptions(id),
  catalog_model_id    uuid NOT NULL REFERENCES portfolio.catalog_models(id),
  catalog_version_id  uuid NOT NULL REFERENCES portfolio.catalog_versions(id),
  accrual_date        date NOT NULL,
  account_count       integer NOT NULL,
  model_assets        numeric(24, 2) NOT NULL,
  fee_bps             numeric(9, 4) NOT NULL,
  day_basis           integer NOT NULL DEFAULT 365,
  daily_fee           numeric(28, 10) NOT NULL,
  supersedes_id       uuid REFERENCES portfolio.provider_fee_accruals(id),
  correction_reason   text,
  computed_by         uuid REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT provider_fee_accruals_assets_chk CHECK (model_assets >= 0 AND account_count >= 0),
  CONSTRAINT provider_fee_accruals_basis_chk CHECK (day_basis = 365),
  CONSTRAINT provider_fee_accruals_fee_chk CHECK (fee_bps > 0 AND fee_bps <= 300),
  -- the accrual IS the formula: bps × assets / (10 000 × 365), to 10 places
  CONSTRAINT provider_fee_accruals_formula_chk CHECK (daily_fee = round(model_assets * fee_bps
                                                                         / (10000 * day_basis), 10)),
  CONSTRAINT provider_fee_accruals_correction_chk CHECK (supersedes_id IS NULL
    OR length(btrim(COALESCE(correction_reason, ''))) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS provider_fee_accruals_live_uq
  ON portfolio.provider_fee_accruals (subscription_id, catalog_version_id, accrual_date)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.provider_fee_accruals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.provider_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a fee accrual is never deleted — restate it' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT portfolio.model_publishing_open(NEW.provider_org_id) THEN
      RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a fee accrual is only ever closed (valid_to) and restated, never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS provider_fee_accruals_guard_trg ON portfolio.provider_fee_accruals;
CREATE TRIGGER provider_fee_accruals_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.provider_fee_accruals
  FOR EACH ROW EXECUTE FUNCTION portfolio.provider_fee_accruals_guard();

CREATE TABLE IF NOT EXISTS portfolio.provider_fee_invoices (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_org_id     uuid NOT NULL REFERENCES public.organizations(id),
  tenant_org_id       uuid NOT NULL REFERENCES public.organizations(id),
  subscription_id     uuid NOT NULL REFERENCES portfolio.provider_subscriptions(id),
  ledger_org_id       uuid NOT NULL REFERENCES public.organizations(id),
  kind                text NOT NULL,
  period_start        date NOT NULL,
  period_end          date NOT NULL,
  amount              numeric(20, 2) NOT NULL,
  reverses_invoice_id uuid REFERENCES portfolio.provider_fee_invoices(id),
  journal_entry_id    uuid NOT NULL REFERENCES public.journal_entries(id),
  reason              text,
  issued_by           uuid NOT NULL REFERENCES public.users(id),
  issued_at           timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT provider_fee_invoices_kind_chk CHECK (kind IN ('invoice', 'reversal')),
  CONSTRAINT provider_fee_invoices_period_chk CHECK (period_end >= period_start),
  CONSTRAINT provider_fee_invoices_amount_chk CHECK (amount > 0),
  CONSTRAINT provider_fee_invoices_reversal_chk CHECK ((kind = 'reversal') = (reverses_invoice_id IS NOT NULL)
    AND (kind <> 'reversal' OR length(btrim(COALESCE(reason, ''))) > 0))
);
CREATE UNIQUE INDEX IF NOT EXISTS provider_fee_invoices_reversal_uq
  ON portfolio.provider_fee_invoices (reverses_invoice_id) WHERE reverses_invoice_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS provider_fee_invoices_journal_uq
  ON portfolio.provider_fee_invoices (journal_entry_id);

CREATE TABLE IF NOT EXISTS portfolio.provider_fee_invoice_lines (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  invoice_id             uuid NOT NULL REFERENCES portfolio.provider_fee_invoices(id),
  provider_org_id        uuid NOT NULL REFERENCES public.organizations(id),
  tenant_org_id          uuid NOT NULL REFERENCES public.organizations(id),
  line_no                integer NOT NULL,
  label                  text NOT NULL,
  catalog_model_id       uuid NOT NULL REFERENCES portfolio.catalog_models(id),
  catalog_version_id     uuid NOT NULL REFERENCES portfolio.catalog_versions(id),
  fee_bps                numeric(9, 4) NOT NULL,
  days                   integer NOT NULL,
  sum_daily_assets       numeric(28, 2) NOT NULL,
  average_daily_assets   numeric(28, 6) NOT NULL,
  amount                 numeric(20, 2) NOT NULL,
  accrual_ids            uuid[] NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz,
  system_from            timestamptz NOT NULL DEFAULT now(),
  system_to              timestamptz,
  -- the separate revenue line the spec names
  CONSTRAINT provider_fee_invoice_lines_label_chk CHECK (label = 'Model-provider fee'),
  CONSTRAINT provider_fee_invoice_lines_days_chk CHECK (days >= 1),
  CONSTRAINT provider_fee_invoice_lines_amount_chk CHECK (amount >= 0 AND sum_daily_assets >= 0),
  CONSTRAINT provider_fee_invoice_lines_accruals_chk CHECK (cardinality(accrual_ids) >= 1),
  CONSTRAINT provider_fee_invoice_lines_line_uq UNIQUE (invoice_id, line_no)
);

CREATE OR REPLACE FUNCTION portfolio.provider_fee_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND portfolio.provider_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT portfolio.model_publishing_open(NEW.provider_org_id) THEN
      RAISE EXCEPTION 'model publishing is not enabled — a registered adviser entity is required'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% rows are a permanent record and never change (correct by reversal)', TG_TABLE_NAME
    USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS provider_fee_invoices_guard_trg ON portfolio.provider_fee_invoices;
CREATE TRIGGER provider_fee_invoices_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.provider_fee_invoices
  FOR EACH ROW EXECUTE FUNCTION portfolio.provider_fee_append_only();
DROP TRIGGER IF EXISTS provider_fee_invoice_lines_guard_trg ON portfolio.provider_fee_invoice_lines;
CREATE TRIGGER provider_fee_invoice_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE
  ON portfolio.provider_fee_invoice_lines FOR EACH ROW EXECUTE FUNCTION portfolio.provider_fee_append_only();

-- ── 9. The event log (append-only; one row per logged action, in the ACTING org) ─
CREATE TABLE IF NOT EXISTS portfolio.model_provider_events (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  provider_org_id  uuid NOT NULL REFERENCES public.organizations(id),
  tenant_org_id    uuid REFERENCES public.organizations(id),
  action           text NOT NULL,
  actor_id         uuid REFERENCES public.users(id),
  subject_type     text NOT NULL,
  subject_id       uuid,
  detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT model_provider_events_action_chk CHECK (action IN (
    'provider_saved', 'gate_opened', 'gate_closed', 'gate_refused', 'model_publishable', 'model_withdrawn',
    'publish_requested', 'version_published', 'version_rejected', 'version_retired', 'subscription_requested',
    'subscription_approved', 'subscription_rejected', 'subscription_suspended', 'subscription_resumed',
    'subscription_ended', 'version_available', 'adoption_requested', 'adoption_preapproved', 'copied',
    'move_task_opened', 'accrued', 'accrual_restated', 'invoiced', 'invoice_reversed')),
  CONSTRAINT model_provider_events_subject_chk CHECK (subject_type IN ('provider', 'gate', 'catalog_model',
    'catalog_version', 'subscription', 'model', 'accrual', 'invoice', 'account_link')),
  CONSTRAINT model_provider_events_human_chk CHECK (actor_id IS NOT NULL OR action IN ('version_available',
    'move_task_opened', 'accrued', 'adoption_preapproved')),
  CONSTRAINT model_provider_events_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS model_provider_events_org_idx ON portfolio.model_provider_events (org_id, created_at);

CREATE OR REPLACE FUNCTION portfolio.model_provider_events_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND portfolio.provider_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'model provider events are a permanent record' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS model_provider_events_guard_trg ON portfolio.model_provider_events;
CREATE TRIGGER model_provider_events_guard_trg BEFORE UPDATE OR DELETE ON portfolio.model_provider_events
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_provider_events_guard();

-- ── 10. Generic system-axis guard for catalog_models ───────────────────────
CREATE OR REPLACE FUNCTION portfolio.catalog_models_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.provider_teardown_allowed() THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'a publishable model record is never deleted — withdraw it' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.system_to IS NULL AND NOT EXISTS (SELECT 1 FROM portfolio.models m WHERE m.id = NEW.model_id
                                               AND m.org_id = NEW.provider_org_id AND m.system_to IS NULL) THEN
      RAISE EXCEPTION 'only the provider org''s own models are publishable' USING ERRCODE = '42501';
    END IF;
    IF NEW.system_to IS NULL AND NOT portfolio.model_provider_org_allowed(NEW.provider_org_id) THEN
      RAISE EXCEPTION 'a publishable model belongs to the Hollisworks platform org (or a test org)'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived publishable-model state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
     OR NEW.model_id IS DISTINCT FROM OLD.model_id OR NEW.provider_org_id IS DISTINCT FROM OLD.provider_org_id THEN
    RAISE EXCEPTION 'the live publishable-model row keeps its id, model and org' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS catalog_models_guard_trg ON portfolio.catalog_models;
CREATE TRIGGER catalog_models_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.catalog_models
  FOR EACH ROW EXECUTE FUNCTION portfolio.catalog_models_guard();

-- ── 11. The GL posted-lines guard learns the documented FIXTURE teardown path ─
-- Unchanged for every real org. Only a DELETE, only with ledger.teardown = 'fixture' set by a role that is not
-- app_service, and only for an entry of an is_test org, passes. (fee43's verify disabled the trigger with ALTER
-- TABLE; this is the narrower path every later verify uses.)
CREATE OR REPLACE FUNCTION public.fn_guard_posted_lines() RETURNS trigger
LANGUAGE plpgsql AS $function$
DECLARE v_posted timestamptz;
BEGIN
  IF TG_OP = 'DELETE' AND COALESCE(current_setting('ledger.teardown', true), '') = 'fixture'
     AND current_user <> 'app_service'
     AND EXISTS (SELECT 1 FROM journal_entries je JOIN organizations o ON o.id = je.org_id
                 WHERE je.id = OLD.entry_id AND o.is_test) THEN
    RETURN OLD;
  END IF;
  SELECT posted_at INTO v_posted FROM journal_entries
   WHERE id = COALESCE(NEW.entry_id, OLD.entry_id);
  IF v_posted IS NOT NULL THEN
    RAISE EXCEPTION 'Cannot modify lines of posted entry — reverse instead';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $function$;

-- ── 12. Disclosure acknowledgements learn the subscription subject ─────────
ALTER TABLE public.disclosure_acknowledgements DROP CONSTRAINT IF EXISTS disclosure_ack_subject_chk;
ALTER TABLE public.disclosure_acknowledgements ADD CONSTRAINT disclosure_ack_subject_chk CHECK (subject_type IN (
  'long_short_proposal', 'section351_plan', 'account', 'backtest', 'proposal', 'disclosure', 'model_subscription'));

-- ── 13. RLS — a policy for every operation of every new table ──────────────
DO $$
DECLARE t text; own text; sa text; pred text;
BEGIN
  sa := 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true''';
  own := '(NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid';

  -- platform settings: the provider org reads its own; only Super Admin writes
  FOREACH t IN ARRAY ARRAY['model_providers', 'model_publishing_gates'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (provider_org_id = %s OR %s)',
                   t || '_select', t, own, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK (%s)', t || '_insert', t, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING (%s) WITH CHECK (%s)', t || '_update', t, sa, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING (%s)', t || '_delete', t, sa);
  END LOOP;

  -- provider-org tables
  pred := format('(provider_org_id = %s OR %s)', own, sa);
  FOREACH t IN ARRAY ARRAY['catalog_models', 'catalog_versions'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_select', t);
    IF t = 'catalog_versions' THEN
      EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (%s OR (status IN (''published'', '
                     '''retired'') AND portfolio.catalog_visible(provider_org_id)))', t || '_select', t, pred);
    ELSE
      EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (%s OR (status = ''publishable'' AND '
                     'portfolio.catalog_visible(provider_org_id)))', t || '_select', t, pred);
    END IF;
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK %s', t || '_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING %s WITH CHECK %s', t || '_update', t, pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING %s', t || '_delete', t, pred);
  END LOOP;

  -- the tenant's own subscriptions (the provider reads status through provider_subscription_status only)
  pred := format('(org_id = %s OR %s)', own, sa);
  FOREACH t IN ARRAY ARRAY['provider_subscriptions', 'model_provider_events'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING %s', t || '_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK %s', t || '_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING %s WITH CHECK %s', t || '_update', t, pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING %s', t || '_delete', t, pred);
  END LOOP;

  -- the fee: both parties READ (aggregates only); only the platform-scope engine writes
  pred := format('(provider_org_id = %s OR tenant_org_id = %s OR %s)', own, own, sa);
  FOREACH t IN ARRAY ARRAY['provider_fee_accruals', 'provider_fee_invoices', 'provider_fee_invoice_lines'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING %s', t || '_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK (%s)', t || '_insert', t, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING (%s) WITH CHECK (%s)', t || '_update', t, sa, sa);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING (%s)', t || '_delete', t, sa);
  END LOOP;

  FOREACH t IN ARRAY ARRAY['model_providers', 'model_publishing_gates', 'catalog_models', 'catalog_versions',
                           'provider_subscriptions', 'model_provider_events', 'provider_fee_accruals',
                           'provider_fee_invoices', 'provider_fee_invoice_lines'] LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;

REVOKE ALL ON FUNCTION portfolio.model_provider_org_allowed(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.provider_tenant_allowed(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.model_provider_complete(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.model_publishing_open(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.catalog_visible(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.provider_subscription_status(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.provider_model_usage(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION portfolio.model_provider_org_allowed(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.provider_tenant_allowed(uuid, uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.model_provider_complete(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.model_publishing_open(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.catalog_visible(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.provider_subscription_status(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.provider_model_usage(uuid, date) TO app_service;

-- ── 14. Permissions (granted to NO role: the platform side is Super Admin only) ─
INSERT INTO public.permissions (name, resource, action) VALUES
  ('publish_provided_models', 'model_provider', 'publish'),
  ('approve_provided_subscriptions', 'model_provider', 'approve')
ON CONFLICT (name) DO NOTHING;
