-- REP3 — Replication Studio, sprint 3: model management. One model registry
-- for every source (upload, library copy, library subscription, fund replica,
-- composite), versions with maker-checker approval on the governed human-task
-- path, a permanent upload log, saved column mappings, a model library
-- (platform + org shelves) and multi-sleeve (composite) models.
--
-- Applied by apps/api/scripts/_apply_rep3.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- SCHEMA. Every table lives in `portfolio` (not on any role's search_path —
-- every reference in code is schema-qualified).
--
-- PLATFORM vs ORG
--   model_library_entries has two shelves in one table: shelf 'platform'
--   (owner_scope 'platform', org_id NULL) is read by every org and written
--   only under app.is_super_admin; shelf 'org' (owner_scope 'org', org_id set)
--   is read and written only by its own org. Every other table is ORG-scoped
--   (NULLIF org isolation on every operation). A PLATFORM entry's content is
--   the approved version of a model owned by the Hollisworks platform org; the
--   SELECT carve-out on models / model_versions / positions / sleeves admits
--   exactly that content (live approved version of a published platform
--   entry's model) to every org, read-only, via two SECURITY DEFINER helpers.
--
-- BI-TEMPORAL (Rule 3)
--   models, model_versions, model_library_entries, model_subscriptions:
--     SYSTEM axis — versions, positions, sleeves, uploads, subscriptions and
--     library entries all point at these ids, so the outgoing state is copied
--     to a fresh id (archived_from_id = live id, system_to = now()) and the live
--     row keeps its id (the portfolio.assets / rep1 / rep2 precedent).
--   model_version_positions, model_sleeves, model_column_mappings: VALID axis —
--     nothing references their ids; an edit closes the live row (valid_to) and
--     inserts its successor.
--   model_uploads, model_upload_events, model_events: append-only, immutable.
--
-- THE CUSTODY CLIFF. Approving a model version never trades. It emits the
-- domain event 'model.version_approved' (rep5 turns it into rebalance
-- PROPOSALS). Nothing in this schema places a trade or moves money.

-- ── 0. Shared guard: the only path past the immutability triggers ─────────
CREATE OR REPLACE FUNCTION portfolio.model_teardown_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('models.teardown', true), '') = 'fixture'
     AND current_user <> 'app_service'
$$;

-- ── 1. Models (ORG, system axis) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.models (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  name                     text NOT NULL,
  description              text,
  source                   text NOT NULL,
  status                   text NOT NULL DEFAULT 'draft',
  base_currency            text NOT NULL DEFAULT 'USD',
  default_fee_schedule_id  uuid REFERENCES public.fee_schedules(id),
  replica_security_id      uuid REFERENCES portfolio.replica_securities(id),
  library_entry_id         uuid,
  provider_name            text,
  created_by               uuid REFERENCES public.users(id),
  archived_from_id         uuid REFERENCES portfolio.models(id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT models_source_chk CHECK (source IN ('upload', 'library_copy', 'library_subscription', 'fund_replica',
                                                 'composite')),
  CONSTRAINT models_status_chk CHECK (status IN ('draft', 'active', 'retired')),
  CONSTRAINT models_name_chk CHECK (length(btrim(name)) BETWEEN 1 AND 200),
  -- "Holdings of" is RESERVED for models built from a fund (rep4) or a 13F filer (later)
  CONSTRAINT models_reserved_name_chk CHECK (source = 'fund_replica'
                                             OR lower(btrim(name)) NOT LIKE 'holdings of%'),
  CONSTRAINT models_currency_chk CHECK (base_currency ~ '^[A-Z]{3}$'),
  CONSTRAINT models_library_chk CHECK (
    (source IN ('library_copy', 'library_subscription')) = (library_entry_id IS NOT NULL)),
  CONSTRAINT models_replica_chk CHECK (replica_security_id IS NULL OR source = 'fund_replica'),
  CONSTRAINT models_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS models_live_name_uq
  ON portfolio.models (org_id, lower(btrim(name))) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS models_org_idx ON portfolio.models (org_id) WHERE system_to IS NULL;

-- ── 2. Uploads — THE LOG (ORG, append-only, never deleted) ─────────────────
-- One row per file received, written once with its receipt facts. Everything
-- that happens to it afterwards (parsed, mapping saved, draft created,
-- rejected, abandoned, failed) is a model_upload_events row; the log's current
-- outcome is the latest outcome event (or the receipt outcome).
CREATE TABLE IF NOT EXISTS portfolio.model_uploads (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  model_id         uuid REFERENCES portfolio.models(id),
  file_name        text NOT NULL,
  size_bytes       bigint NOT NULL,
  sha256           text NOT NULL,
  r2_key           text,
  content_type     text,
  file_kind        text NOT NULL,
  uploaded_by      uuid REFERENCES public.users(id),
  uploaded_at      timestamptz NOT NULL DEFAULT now(),
  source           text NOT NULL DEFAULT 'manual',
  provider_name    text,
  outcome          text NOT NULL,
  duplicate_of     uuid REFERENCES portfolio.model_uploads(id),
  messages         jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT model_uploads_sha_chk CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT model_uploads_size_chk CHECK (size_bytes >= 0),
  CONSTRAINT model_uploads_kind_chk CHECK (file_kind IN ('xlsx', 'xls', 'csv', 'refused')),
  CONSTRAINT model_uploads_source_chk CHECK (source IN ('manual', 'provider_email', 'provider_feed', 'library_publish')),
  CONSTRAINT model_uploads_outcome_chk CHECK (outcome IN ('received', 'duplicate', 'failed')),
  CONSTRAINT model_uploads_duplicate_chk CHECK ((outcome = 'duplicate') = (duplicate_of IS NOT NULL)),
  CONSTRAINT model_uploads_stored_chk CHECK (outcome <> 'received' OR r2_key IS NOT NULL),
  CONSTRAINT model_uploads_messages_chk CHECK (jsonb_typeof(messages) = 'array'),
  CONSTRAINT model_uploads_human_chk CHECK (source <> 'manual' OR uploaded_by IS NOT NULL)
);
-- one stored original per (org, content): the same bytes again are a duplicate row, never a second object
CREATE UNIQUE INDEX IF NOT EXISTS model_uploads_stored_uq
  ON portfolio.model_uploads (org_id, sha256) WHERE outcome = 'received';
CREATE INDEX IF NOT EXISTS model_uploads_org_idx ON portfolio.model_uploads (org_id, uploaded_at DESC);

-- ── 3. Column mappings (ORG, valid axis, versioned per provider) ───────────
CREATE TABLE IF NOT EXISTS portfolio.model_column_mappings (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  provider_name    text NOT NULL,
  provider_key     text NOT NULL,
  mapping          jsonb NOT NULL,
  sheet_hint       text,
  header_row_hint  integer,
  version_no       integer NOT NULL,
  created_by       uuid NOT NULL REFERENCES public.users(id),
  last_used_at     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT model_column_mappings_key_chk CHECK (provider_key = lower(btrim(provider_name)) AND length(provider_key) > 0),
  CONSTRAINT model_column_mappings_mapping_chk CHECK (jsonb_typeof(mapping) = 'object'),
  CONSTRAINT model_column_mappings_version_chk CHECK (version_no >= 1),
  CONSTRAINT model_column_mappings_header_chk CHECK (header_row_hint IS NULL OR header_row_hint >= 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_column_mappings_live_uq
  ON portfolio.model_column_mappings (org_id, provider_key) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS model_column_mappings_version_uq
  ON portfolio.model_column_mappings (org_id, provider_key, version_no) WHERE system_to IS NULL;

-- ── 4. Versions (ORG, system axis) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_versions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  model_id           uuid NOT NULL REFERENCES portfolio.models(id),
  version_no         integer NOT NULL,
  status             text NOT NULL DEFAULT 'draft',
  origin             text NOT NULL,
  origin_ref         uuid,
  maker_ids          uuid[] NOT NULL DEFAULT '{}',
  checker_id         uuid REFERENCES public.users(id),
  approved_at        timestamptz,
  submitted_by       uuid REFERENCES public.users(id),
  submitted_at       timestamptz,
  approval_task_id   uuid REFERENCES public.workflow_run_steps(id),
  upload_id          uuid REFERENCES portfolio.model_uploads(id),
  parent_version_id  uuid REFERENCES portfolio.model_versions(id),
  notes              text,
  cash_target        numeric(14, 10),
  totals             jsonb NOT NULL DEFAULT '{}'::jsonb,
  diff               jsonb NOT NULL DEFAULT '{}'::jsonb,
  tradability        jsonb NOT NULL DEFAULT '[]'::jsonb,
  decision_note      text,
  rejected_by        uuid REFERENCES public.users(id),
  rejected_at        timestamptz,
  abandoned_by       uuid REFERENCES public.users(id),
  abandoned_at       timestamptz,
  abandon_reason     text,
  created_by         uuid REFERENCES public.users(id),
  archived_from_id   uuid REFERENCES portfolio.model_versions(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  -- 'submitted' is the frozen state while the approval task is open (an
  -- approver must decide exactly what was submitted); 'rejected' is reserved —
  -- in rep3 a rejection returns the version to 'draft' with the note recorded.
  CONSTRAINT model_versions_status_chk CHECK (status IN ('draft', 'submitted', 'approved', 'superseded', 'rejected',
                                                        'abandoned')),
  CONSTRAINT model_versions_origin_chk CHECK (origin IN ('upload', 'manual', 'library_copy', 'subscription',
                                                        'composite_follow', 'revision')),
  CONSTRAINT model_versions_version_chk CHECK (version_no >= 1),
  -- maker-checker in the database: the approver is never one of the makers
  CONSTRAINT model_versions_maker_checker_chk CHECK (checker_id IS NULL OR NOT (checker_id = ANY (maker_ids))),
  CONSTRAINT model_versions_approved_chk CHECK (
    status NOT IN ('approved', 'superseded') OR (checker_id IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT model_versions_submitted_chk CHECK (status <> 'submitted' OR approval_task_id IS NOT NULL),
  CONSTRAINT model_versions_abandoned_chk CHECK (
    status <> 'abandoned' OR (abandoned_at IS NOT NULL AND length(btrim(COALESCE(abandon_reason, ''))) > 0)),
  CONSTRAINT model_versions_cash_chk CHECK (cash_target IS NULL OR (cash_target >= 0 AND cash_target <= 1)),
  CONSTRAINT model_versions_json_chk CHECK (jsonb_typeof(totals) = 'object' AND jsonb_typeof(diff) = 'object'
                                            AND jsonb_typeof(tradability) = 'array'),
  CONSTRAINT model_versions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one approved version live per model; one open (draft or submitted) version per model
CREATE UNIQUE INDEX IF NOT EXISTS model_versions_approved_live_uq
  ON portfolio.model_versions (model_id) WHERE status = 'approved' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS model_versions_open_live_uq
  ON portfolio.model_versions (model_id) WHERE status IN ('draft', 'submitted') AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS model_versions_no_uq
  ON portfolio.model_versions (model_id, version_no) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS model_versions_task_idx
  ON portfolio.model_versions (approval_task_id) WHERE system_to IS NULL AND approval_task_id IS NOT NULL;

-- An approved (or superseded / abandoned / rejected) version never changes.
-- The single exception is approved → superseded with nothing else touched.
-- An archived state is immutable. The live row keeps its id.
CREATE OR REPLACE FUNCTION portfolio.model_versions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.model_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'model versions are never deleted (abandon a draft instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived model version state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live model version keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'approved' THEN
    IF NEW.status = 'superseded'
       AND (to_jsonb(NEW) - ARRAY['status', 'system_from']) = (to_jsonb(OLD) - ARRAY['status', 'system_from']) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'an approved model version is never changed — start a new draft' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('superseded', 'abandoned', 'rejected') THEN
    RAISE EXCEPTION 'a % model version is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>submitted', 'draft>abandoned', 'submitted>approved', 'submitted>draft', 'submitted>abandoned'])) THEN
    RAISE EXCEPTION 'illegal model version transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  -- content is frozen while submitted: only the decision columns move
  IF OLD.status = 'submitted' AND (to_jsonb(NEW) - ARRAY['status', 'checker_id', 'approved_at', 'approval_task_id',
        'decision_note', 'rejected_by', 'rejected_at', 'abandoned_by', 'abandoned_at', 'abandon_reason',
        'system_from']) <> (to_jsonb(OLD) - ARRAY['status', 'checker_id', 'approved_at', 'approval_task_id',
        'decision_note', 'rejected_by', 'rejected_at', 'abandoned_by', 'abandoned_at', 'abandon_reason',
        'system_from']) THEN
    RAISE EXCEPTION 'a submitted model version is frozen until it is approved or returned to draft'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS model_versions_guard_trg ON portfolio.model_versions;
CREATE TRIGGER model_versions_guard_trg BEFORE UPDATE OR DELETE ON portfolio.model_versions
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_versions_guard();

-- ── 5. Positions of a version (ORG, valid axis) ────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_version_positions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  version_id          uuid NOT NULL REFERENCES portfolio.model_versions(id),
  row_num             integer NOT NULL,
  global_security_id  uuid REFERENCES portfolio.securities_global(id),
  raw_ticker          text,
  raw_cusip           text,
  raw_isin            text,
  raw_sedol           text,
  raw_name            text,
  target_weight       numeric(14, 10) NOT NULL,
  asset_class         text,
  sleeve_label        text,
  drift_min           numeric(14, 10),
  drift_max           numeric(14, 10),
  alternates          jsonb NOT NULL DEFAULT '[]'::jsonb,
  restrictions        text,
  notes               text,
  resolution_state    text NOT NULL,
  resolution_note     text,
  created_by          uuid REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT model_version_positions_weight_chk CHECK (target_weight >= 0 AND target_weight <= 1),
  CONSTRAINT model_version_positions_band_chk CHECK (
    (drift_min IS NULL OR (drift_min >= 0 AND drift_min <= target_weight))
    AND (drift_max IS NULL OR (drift_max <= 1 AND drift_max >= target_weight))),
  CONSTRAINT model_version_positions_state_chk CHECK (
    (resolution_state = 'resolved' AND global_security_id IS NOT NULL)
    OR (resolution_state IN ('unresolved', 'cash') AND global_security_id IS NULL)),
  CONSTRAINT model_version_positions_row_chk CHECK (row_num >= 1),
  CONSTRAINT model_version_positions_alternates_chk CHECK (jsonb_typeof(alternates) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS model_version_positions_row_uq
  ON portfolio.model_version_positions (version_id, row_num) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS model_version_positions_security_uq
  ON portfolio.model_version_positions (version_id, global_security_id)
  WHERE valid_to IS NULL AND system_to IS NULL AND global_security_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS model_version_positions_cash_uq
  ON portfolio.model_version_positions (version_id)
  WHERE valid_to IS NULL AND system_to IS NULL AND resolution_state = 'cash';
CREATE INDEX IF NOT EXISTS model_version_positions_version_idx
  ON portfolio.model_version_positions (version_id) WHERE valid_to IS NULL AND system_to IS NULL;

-- Positions and sleeves change only while their version is a live DRAFT, and
-- only by being added or closed (valid_to) — never edited in place.
CREATE OR REPLACE FUNCTION portfolio.model_version_children_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  st text;
  vid uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.model_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'model version rows are never deleted — close them (valid_to)' USING ERRCODE = '42501';
  END IF;
  IF TG_TABLE_NAME = 'model_sleeves' THEN
    vid := NEW.parent_version_id;
  ELSE
    vid := NEW.version_id;
  END IF;
  SELECT status INTO st FROM portfolio.model_versions WHERE id = vid AND system_to IS NULL;
  IF st IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'a model version''s rows change only while it is a draft (status %)', COALESCE(st, 'missing')
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a model version row is only ever closed (valid_to), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS model_version_positions_guard_trg ON portfolio.model_version_positions;
CREATE TRIGGER model_version_positions_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.model_version_positions
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_version_children_guard();

-- ── 6. Sleeves of a composite version (ORG, valid axis) ────────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_sleeves (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                     uuid NOT NULL REFERENCES public.organizations(id),
  parent_version_id          uuid NOT NULL REFERENCES portfolio.model_versions(id),
  child_model_id             uuid NOT NULL REFERENCES portfolio.models(id),
  weight                     numeric(14, 10) NOT NULL,
  follow                     text NOT NULL,
  pinned_version_id          uuid REFERENCES portfolio.model_versions(id),
  -- the child version this sleeve resolves to: the pinned one, or (follow
  -- 'live') the child's approved version when this draft was written
  resolved_child_version_id  uuid NOT NULL REFERENCES portfolio.model_versions(id),
  created_by                 uuid REFERENCES public.users(id),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  valid_from                 timestamptz NOT NULL DEFAULT now(),
  valid_to                   timestamptz,
  system_from                timestamptz NOT NULL DEFAULT now(),
  system_to                  timestamptz,
  CONSTRAINT model_sleeves_weight_chk CHECK (weight > 0 AND weight <= 1),
  CONSTRAINT model_sleeves_follow_chk CHECK (
    (follow = 'live' AND pinned_version_id IS NULL)
    OR (follow = 'pinned' AND pinned_version_id IS NOT NULL AND resolved_child_version_id = pinned_version_id))
);
CREATE UNIQUE INDEX IF NOT EXISTS model_sleeves_live_uq
  ON portfolio.model_sleeves (parent_version_id, child_model_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS model_sleeves_child_idx
  ON portfolio.model_sleeves (child_model_id) WHERE valid_to IS NULL AND system_to IS NULL;
DROP TRIGGER IF EXISTS model_sleeves_guard_trg ON portfolio.model_sleeves;
CREATE TRIGGER model_sleeves_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON portfolio.model_sleeves
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_version_children_guard();

-- ── 7. Upload events (ORG, append-only) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_upload_events (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  upload_id        uuid NOT NULL REFERENCES portfolio.model_uploads(id),
  event            text NOT NULL,
  model_id         uuid REFERENCES portfolio.models(id),
  version_id       uuid REFERENCES portfolio.model_versions(id),
  mapping_id       uuid REFERENCES portfolio.model_column_mappings(id),
  sheet_name       text,
  header_row       integer,
  weight_basis     text,
  rows_read        integer,
  rows_resolved    integer,
  rows_unresolved  integer,
  rows_cash        integer,
  messages         jsonb NOT NULL DEFAULT '[]'::jsonb,
  reason           text,
  actor_id         uuid REFERENCES public.users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT model_upload_events_event_chk CHECK (event IN ('parsed', 'mapping_saved', 'draft_created', 'rejected',
                                                            'abandoned', 'failed')),
  CONSTRAINT model_upload_events_basis_chk CHECK (weight_basis IS NULL OR weight_basis IN ('percent', 'fraction')),
  CONSTRAINT model_upload_events_messages_chk CHECK (jsonb_typeof(messages) = 'array'),
  CONSTRAINT model_upload_events_draft_chk CHECK (event <> 'draft_created' OR version_id IS NOT NULL),
  CONSTRAINT model_upload_events_reason_chk CHECK (event <> 'abandoned' OR length(btrim(COALESCE(reason, ''))) > 0)
);
CREATE INDEX IF NOT EXISTS model_upload_events_upload_idx ON portfolio.model_upload_events (upload_id, created_at);

-- ── 8. Library entries (PLATFORM or ORG shelf, system axis) ────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_library_entries (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shelf               text NOT NULL,
  owner_scope         text NOT NULL,
  org_id              uuid REFERENCES public.organizations(id),
  model_id            uuid NOT NULL REFERENCES portfolio.models(id),
  title               text NOT NULL,
  provider            text NOT NULL,
  description         text,
  update_cadence      text NOT NULL,
  licensing_note      text,
  requires_agreement  boolean NOT NULL DEFAULT false,
  published_by        uuid NOT NULL REFERENCES public.users(id),
  published_at        timestamptz NOT NULL DEFAULT now(),
  status              text NOT NULL DEFAULT 'published',
  status_changed_by   uuid REFERENCES public.users(id),
  status_changed_at   timestamptz,
  status_reason       text,
  archived_from_id    uuid REFERENCES portfolio.model_library_entries(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT model_library_entries_shelf_chk CHECK (
    (shelf = 'platform' AND owner_scope = 'platform' AND org_id IS NULL)
    OR (shelf = 'org' AND owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT model_library_entries_status_chk CHECK (status IN ('published', 'unpublished', 'retired')),
  CONSTRAINT model_library_entries_cadence_chk CHECK (update_cadence IN ('daily', 'weekly', 'monthly', 'quarterly',
                                                                         'semiannual', 'annual', 'ad_hoc')),
  CONSTRAINT model_library_entries_title_chk CHECK (length(btrim(title)) > 0 AND length(btrim(provider)) > 0),
  CONSTRAINT model_library_entries_status_by_chk CHECK (
    status = 'published' OR (status_changed_by IS NOT NULL AND status_changed_at IS NOT NULL)),
  CONSTRAINT model_library_entries_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_library_entries_model_live_uq
  ON portfolio.model_library_entries (model_id) WHERE status = 'published' AND system_to IS NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'models_library_entry_fk') THEN
    ALTER TABLE portfolio.models ADD CONSTRAINT models_library_entry_fk
      FOREIGN KEY (library_entry_id) REFERENCES portfolio.model_library_entries(id);
  END IF;
END $$;

-- ── 9. Subscriptions (ORG, system axis) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.model_subscriptions (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  model_id                 uuid NOT NULL REFERENCES portfolio.models(id),
  library_entry_id         uuid NOT NULL REFERENCES portfolio.model_library_entries(id),
  last_applied_version_id  uuid REFERENCES portfolio.model_versions(id),
  status                   text NOT NULL DEFAULT 'active',
  created_by               uuid REFERENCES public.users(id),
  archived_from_id         uuid REFERENCES portfolio.model_subscriptions(id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT model_subscriptions_status_chk CHECK (status IN ('active', 'paused', 'ended')),
  CONSTRAINT model_subscriptions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_subscriptions_model_live_uq
  ON portfolio.model_subscriptions (model_id) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS model_subscriptions_entry_idx
  ON portfolio.model_subscriptions (library_entry_id) WHERE system_to IS NULL AND status = 'active';

-- ── 10. Model event log (ORG, append-only) ─────────────────────────────────
-- One row per logged human (or system-import) action; its id is the
-- source_id of the domain_events row (source_type 'model_event').
CREATE TABLE IF NOT EXISTS portfolio.model_events (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  model_id          uuid REFERENCES portfolio.models(id),
  version_id        uuid REFERENCES portfolio.model_versions(id),
  library_entry_id  uuid REFERENCES portfolio.model_library_entries(id),
  upload_id         uuid REFERENCES portfolio.model_uploads(id),
  action            text NOT NULL,
  actor_id          uuid REFERENCES public.users(id),
  detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT model_events_action_chk CHECK (action IN (
    'create_model', 'edit_model', 'create_draft', 'edit_positions', 'edit_sleeves', 'submit', 'approve', 'reject',
    'abandon', 'save_mapping', 'publish', 'unpublish', 'retire_entry', 'agreement_confirmed', 'copy', 'subscribe',
    'subscription_draft', 'composite_follow_draft', 'follow_skipped')),
  -- every action is a person's, except the logged system imports
  CONSTRAINT model_events_human_chk CHECK (
    actor_id IS NOT NULL OR action IN ('subscription_draft', 'composite_follow_draft', 'follow_skipped')),
  CONSTRAINT model_events_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS model_events_model_idx ON portfolio.model_events (model_id, created_at);
CREATE INDEX IF NOT EXISTS model_events_entry_idx ON portfolio.model_events (org_id, library_entry_id, action);

-- ── 11. Immutability guards for the append-only tables + the rest ──────────
CREATE OR REPLACE FUNCTION portfolio.model_append_only_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND portfolio.model_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '% rows are a permanent record and never change', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS model_uploads_immutable_trg ON portfolio.model_uploads;
CREATE TRIGGER model_uploads_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.model_uploads
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_append_only_guard();
DROP TRIGGER IF EXISTS model_upload_events_immutable_trg ON portfolio.model_upload_events;
CREATE TRIGGER model_upload_events_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.model_upload_events
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_append_only_guard();
DROP TRIGGER IF EXISTS model_events_immutable_trg ON portfolio.model_events;
CREATE TRIGGER model_events_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.model_events
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_append_only_guard();

-- System-axis tables: the live row keeps its id; an archived state never
-- changes; DELETE only on the fixture-teardown path.
CREATE OR REPLACE FUNCTION portfolio.model_system_axis_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.model_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted (retire / end them instead)', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived % state is immutable', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live % row keeps its id; archive by copying it', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS models_guard_trg ON portfolio.models;
CREATE TRIGGER models_guard_trg BEFORE UPDATE OR DELETE ON portfolio.models
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_system_axis_guard();
DROP TRIGGER IF EXISTS model_library_entries_guard_trg ON portfolio.model_library_entries;
CREATE TRIGGER model_library_entries_guard_trg BEFORE UPDATE OR DELETE ON portfolio.model_library_entries
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_system_axis_guard();
DROP TRIGGER IF EXISTS model_subscriptions_guard_trg ON portfolio.model_subscriptions;
CREATE TRIGGER model_subscriptions_guard_trg BEFORE UPDATE OR DELETE ON portfolio.model_subscriptions
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_system_axis_guard();

-- Mappings: closed (valid_to) or touched (last_used_at alone) — never edited.
CREATE OR REPLACE FUNCTION portfolio.model_column_mappings_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.model_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'column mapping versions are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NULL
     AND (to_jsonb(NEW) - 'last_used_at') = (to_jsonb(OLD) - 'last_used_at') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a column mapping version is immutable: saving writes the next version' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS model_column_mappings_guard_trg ON portfolio.model_column_mappings;
CREATE TRIGGER model_column_mappings_guard_trg BEFORE UPDATE OR DELETE ON portfolio.model_column_mappings
  FOR EACH ROW EXECUTE FUNCTION portfolio.model_column_mappings_guard();

-- ── 12. Platform-content carve-out helpers (SECURITY DEFINER, boolean only) ─
-- Answer one question each and return nothing else: is this model the source
-- of a live PUBLISHED PLATFORM library entry / is this version that model's
-- live APPROVED version. Owned by postgres; search_path pinned.
CREATE OR REPLACE FUNCTION portfolio.model_is_platform_published(p_model_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, portfolio AS $$
  SELECT EXISTS (SELECT 1 FROM portfolio.model_library_entries e
                 WHERE e.model_id = p_model_id AND e.shelf = 'platform' AND e.status = 'published'
                   AND e.valid_to IS NULL AND e.system_to IS NULL)
$$;
CREATE OR REPLACE FUNCTION portfolio.model_version_is_platform_readable(p_version_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, portfolio AS $$
  SELECT EXISTS (SELECT 1 FROM portfolio.model_versions v
                 JOIN portfolio.model_library_entries e ON e.model_id = v.model_id
                 WHERE v.id = p_version_id AND v.status = 'approved' AND v.system_to IS NULL
                   AND e.shelf = 'platform' AND e.status = 'published' AND e.valid_to IS NULL AND e.system_to IS NULL)
$$;
-- How many live models (in any org) were loaded from an entry — a count only.
CREATE OR REPLACE FUNCTION portfolio.model_library_usage(p_entry_id uuid) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, portfolio AS $$
  SELECT count(*)::integer FROM portfolio.models m
  WHERE m.library_entry_id = p_entry_id AND m.valid_to IS NULL AND m.system_to IS NULL
$$;
REVOKE ALL ON FUNCTION portfolio.model_is_platform_published(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.model_version_is_platform_readable(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION portfolio.model_library_usage(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION portfolio.model_is_platform_published(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.model_version_is_platform_readable(uuid) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.model_library_usage(uuid) TO app_service;

-- ── 13. RLS — a policy for every operation each table takes ───────────────
DO $$
DECLARE t text; pred text; readp text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['models', 'model_uploads', 'model_column_mappings', 'model_versions',
                           'model_version_positions', 'model_sleeves', 'model_upload_events', 'model_subscriptions',
                           'model_events'] LOOP
    readp := pred;
    IF t = 'models' THEN
      readp := '(' || pred || ' OR portfolio.model_is_platform_published(id))';
    ELSIF t = 'model_versions' THEN
      readp := '(' || pred || ' OR portfolio.model_version_is_platform_readable(id))';
    ELSIF t = 'model_version_positions' THEN
      readp := '(' || pred || ' OR portfolio.model_version_is_platform_readable(version_id))';
    ELSIF t = 'model_sleeves' THEN
      readp := '(' || pred || ' OR portfolio.model_version_is_platform_readable(parent_version_id))';
    END IF;
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING %s', t || '_org_select', t, readp);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t,
                   pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;

ALTER TABLE portfolio.model_library_entries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_library_entries_select ON portfolio.model_library_entries;
CREATE POLICY model_library_entries_select ON portfolio.model_library_entries FOR SELECT USING (
  shelf = 'platform'
  OR org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS model_library_entries_insert ON portfolio.model_library_entries;
CREATE POLICY model_library_entries_insert ON portfolio.model_library_entries FOR INSERT WITH CHECK (
  (shelf = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS model_library_entries_update ON portfolio.model_library_entries;
CREATE POLICY model_library_entries_update ON portfolio.model_library_entries FOR UPDATE USING (
  (shelf = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
  WITH CHECK (
  (shelf = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS model_library_entries_delete ON portfolio.model_library_entries;
CREATE POLICY model_library_entries_delete ON portfolio.model_library_entries FOR DELETE USING (
  (shelf = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.model_library_entries TO app_service;

-- ── 14. Permissions — org_admin by default (and, via
--        rbac.ensure_org_admin_role, every NEW org's org_admin) ────────────
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_models', 'models', 'view'),
  ('manage_models', 'models', 'manage'),
  ('approve_models', 'models', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_models', 'manage_models', 'approve_models')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;
