-- DISCLOSURES1 — one disclosure library for the whole platform.
--
-- Applied by apps/api/scripts/_apply_disclosures1.py --schema (one transaction, the operator DSN) and read back by
-- --verify. Idempotent. THE ONLY ROWS IT WRITES are two permissions (view_disclosures, manage_disclosures) and
-- their grant to every EXISTING org_admin role. No disclosure, version or file is created here: the platform
-- defaults are loaded by the operator (apps/api/scripts/load_disclosure_defaults.py, --dry-run first).
--
--   1. public.disclosures              — the library's catalog: one row per KEY (platform-defined; read by every
--                                        org; written only under app.is_super_admin). System-axis archival.
--   2. public.disclosure_versions      — each version is a FILE in R2 (key, hash, size, type) plus its plain-text
--                                        summary (what a screen shows inline), its effective date, status
--                                        (draft / published / retired), drafter and publisher (maker-checker:
--                                        CHECK published_by <> drafted_by). Scope platform → org → household →
--                                        account. The id is STABLE (acknowledgements reference it) — every status
--                                        change archives the prior state on the SYSTEM axis (archived_from_id).
--   3. public.disclosure_acknowledgements — who acknowledged which VERSION (id + content hash, a composite FK so
--                                        the pair can never disagree), for what subject, from which screen, when.
--                                        Insert-only.
--
-- Why public, not portfolio: the library spans backtests, prices, tax, models and proposals, and every comparable
-- shared platform + org table (custodian_capabilities, borrow_rates, hypothetical_performance_policies) is in
-- public with the NULLIF org policy (docs/DISCLOSURES1_DISCOVERY.md).
--
-- RLS: a policy for EVERY operation of every table. Platform rows (org_id NULL) are read by all and written only
-- under app.is_super_admin; org rows only by their own org (NULLIF on the org GUC). Deletes pass the guards only
-- on the fixture-teardown path (public.execution_teardown_allowed(), never as app_service).

-- ── 0. Permissions (org_admin by default) ───────────────────────────────────
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_disclosures', 'disclosures', 'view'),
  ('manage_disclosures', 'disclosures', 'manage')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_disclosures', 'manage_disclosures')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. The catalog ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.disclosures (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key                text NOT NULL,
  title              text NOT NULL,
  category           text NOT NULL,
  audience           text NOT NULL,
  description        text,
  created_by         uuid REFERENCES public.users(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  archived_from_id   uuid REFERENCES public.disclosures(id),
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT disclosures_key_chk CHECK (key ~ '^[a-z0-9_]+(\.[a-z0-9_]+)+$' AND length(key) <= 120),
  CONSTRAINT disclosures_title_chk CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  CONSTRAINT disclosures_category_chk CHECK (category IN ('risk', 'hypothetical', 'tax', 'licence', 'product',
                                                          'other')),
  CONSTRAINT disclosures_audience_chk CHECK (audience IN ('staff', 'member', 'both')),
  CONSTRAINT disclosures_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS disclosures_key_live_uq ON public.disclosures (key) WHERE system_to IS NULL;

-- ── 2. Versions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.disclosure_versions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  disclosure_id      uuid NOT NULL REFERENCES public.disclosures(id),
  scope              text NOT NULL,
  org_id             uuid REFERENCES public.organizations(id),
  household_id       uuid REFERENCES public.households(id),
  account_id         uuid REFERENCES public.accounts(id),
  version_no         integer NOT NULL,
  r2_key             text NOT NULL,
  r2_bucket          text,
  file_name          text NOT NULL,
  content_type       text NOT NULL,
  size_bytes         integer NOT NULL,
  content_sha256     text NOT NULL,
  summary            text NOT NULL,
  effective_from     date NOT NULL,
  effective_to       date,
  status             text NOT NULL DEFAULT 'draft',
  drafted_by         uuid NOT NULL REFERENCES public.users(id),
  drafted_at         timestamptz NOT NULL DEFAULT now(),
  published_by       uuid REFERENCES public.users(id),
  published_at       timestamptz,
  retired_by         uuid REFERENCES public.users(id),
  retired_at         timestamptz,
  retire_reason      text,
  note               text,
  archived_from_id   uuid REFERENCES public.disclosure_versions(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT disclosure_versions_id_hash_uq UNIQUE (id, content_sha256),
  CONSTRAINT disclosure_versions_scope_chk CHECK (
       (scope = 'platform'  AND org_id IS NULL AND household_id IS NULL AND account_id IS NULL)
    OR (scope = 'org'       AND org_id IS NOT NULL AND household_id IS NULL AND account_id IS NULL)
    OR (scope = 'household' AND org_id IS NOT NULL AND household_id IS NOT NULL AND account_id IS NULL)
    OR (scope = 'account'   AND org_id IS NOT NULL AND household_id IS NULL AND account_id IS NOT NULL)),
  CONSTRAINT disclosure_versions_version_chk CHECK (version_no >= 1),
  CONSTRAINT disclosure_versions_key_chk CHECK (r2_key LIKE 'disclosures/%' AND length(r2_key) <= 600),
  CONSTRAINT disclosure_versions_type_chk CHECK (content_type IN (
    'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/markdown', 'text/html')),
  CONSTRAINT disclosure_versions_size_chk CHECK (size_bytes > 0 AND size_bytes <= 5242880),
  CONSTRAINT disclosure_versions_hash_chk CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT disclosure_versions_summary_chk CHECK (length(btrim(summary)) BETWEEN 1 AND 6000),
  CONSTRAINT disclosure_versions_status_chk CHECK (status IN ('draft', 'published', 'retired')),
  CONSTRAINT disclosure_versions_maker_checker_chk CHECK (published_by IS NULL OR published_by <> drafted_by),
  CONSTRAINT disclosure_versions_published_chk CHECK (status = 'draft' OR status = 'retired'
                                                      OR (published_by IS NOT NULL AND published_at IS NOT NULL)),
  CONSTRAINT disclosure_versions_draft_chk CHECK (status <> 'draft' OR (published_by IS NULL AND retired_by IS NULL
                                                                        AND effective_to IS NULL)),
  CONSTRAINT disclosure_versions_retired_chk CHECK (status <> 'retired' OR (retired_by IS NOT NULL
                                                    AND retired_at IS NOT NULL
                                                    AND length(btrim(COALESCE(retire_reason, ''))) > 0)),
  CONSTRAINT disclosure_versions_dates_chk CHECK (effective_to IS NULL OR effective_to >= effective_from),
  CONSTRAINT disclosure_versions_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS disclosure_versions_no_uq ON public.disclosure_versions (
  disclosure_id, scope, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid),
  COALESCE(household_id, '00000000-0000-0000-0000-000000000000'::uuid),
  COALESCE(account_id, '00000000-0000-0000-0000-000000000000'::uuid), version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS disclosure_versions_r2_key_uq ON public.disclosure_versions (r2_key)
  WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS disclosure_versions_resolve_idx ON public.disclosure_versions (disclosure_id, scope,
  org_id) WHERE system_to IS NULL;

-- A version is a fixed record: only its decision columns change (draft → published → retired; a draft may be
-- withdrawn = retired), only on the live row, and only after the prior state was copied (system axis).
CREATE OR REPLACE FUNCTION public.disclosure_versions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'published_by', 'published_at', 'retired_by', 'retired_at', 'retire_reason',
                           'effective_to', 'system_from'];
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'disclosure versions are never deleted — retire one' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived disclosure version is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live disclosure version keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'a disclosure version''s file, summary and dates are fixed — upload a new version'
      USING ERRCODE = '42501';
  END IF;
  IF NOT ((OLD.status = 'draft' AND NEW.status IN ('published', 'retired'))
          OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
    RAISE EXCEPTION 'illegal disclosure version transition % → %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS disclosure_versions_guard_trg ON public.disclosure_versions;
CREATE TRIGGER disclosure_versions_guard_trg BEFORE UPDATE OR DELETE ON public.disclosure_versions
  FOR EACH ROW EXECUTE FUNCTION public.disclosure_versions_guard();

-- The catalog: title / audience / category edits archive the prior row; the key never changes.
CREATE OR REPLACE FUNCTION public.disclosures_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'disclosure keys are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived disclosure row is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.key <> OLD.key OR NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
     OR NEW.id <> OLD.id THEN
    RAISE EXCEPTION 'a disclosure key is fixed; archive by copying the row' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS disclosures_guard_trg ON public.disclosures;
CREATE TRIGGER disclosures_guard_trg BEFORE UPDATE OR DELETE ON public.disclosures
  FOR EACH ROW EXECUTE FUNCTION public.disclosures_guard();

-- ── 3. Acknowledgements (insert-only) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.disclosure_acknowledgements (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  disclosure_id      uuid NOT NULL REFERENCES public.disclosures(id),
  version_id         uuid NOT NULL,
  content_sha256     text NOT NULL,
  user_id            uuid NOT NULL REFERENCES public.users(id),
  subject_type       text NOT NULL,
  subject_id         uuid,
  account_id         uuid REFERENCES public.accounts(id),
  screen             text NOT NULL,
  acknowledged_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT disclosure_ack_version_fk FOREIGN KEY (version_id, content_sha256)
    REFERENCES public.disclosure_versions (id, content_sha256),
  CONSTRAINT disclosure_ack_subject_chk CHECK (subject_type IN ('long_short_proposal', 'section351_plan', 'account',
                                                                'backtest', 'proposal', 'disclosure')),
  CONSTRAINT disclosure_ack_screen_chk CHECK (length(btrim(screen)) BETWEEN 1 AND 200)
);
CREATE INDEX IF NOT EXISTS disclosure_ack_lookup_idx ON public.disclosure_acknowledgements (org_id, disclosure_id,
  subject_type, subject_id, user_id);

CREATE OR REPLACE FUNCTION public.disclosure_ack_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND public.execution_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'a disclosure acknowledgement is a fixed record' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS disclosure_ack_guard_trg ON public.disclosure_acknowledgements;
CREATE TRIGGER disclosure_ack_guard_trg BEFORE UPDATE OR DELETE ON public.disclosure_acknowledgements
  FOR EACH ROW EXECUTE FUNCTION public.disclosure_ack_guard();

-- ── 4. RLS — a policy for every operation of every table ────────────────────
ALTER TABLE public.disclosures ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS disclosures_select ON public.disclosures;
CREATE POLICY disclosures_select ON public.disclosures FOR SELECT USING (true);
DROP POLICY IF EXISTS disclosures_insert ON public.disclosures;
CREATE POLICY disclosures_insert ON public.disclosures FOR INSERT
  WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS disclosures_update ON public.disclosures;
CREATE POLICY disclosures_update ON public.disclosures FOR UPDATE
  USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
  WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS disclosures_delete ON public.disclosures;
CREATE POLICY disclosures_delete ON public.disclosures FOR DELETE
  USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

ALTER TABLE public.disclosure_versions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS disclosure_versions_select ON public.disclosure_versions;
CREATE POLICY disclosure_versions_select ON public.disclosure_versions FOR SELECT USING (
  org_id IS NULL
  OR org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS disclosure_versions_insert ON public.disclosure_versions;
CREATE POLICY disclosure_versions_insert ON public.disclosure_versions FOR INSERT WITH CHECK (
  (org_id IS NOT NULL AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS disclosure_versions_update ON public.disclosure_versions;
CREATE POLICY disclosure_versions_update ON public.disclosure_versions FOR UPDATE USING (
  (org_id IS NOT NULL AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK (
  (org_id IS NOT NULL AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS disclosure_versions_delete ON public.disclosure_versions;
CREATE POLICY disclosure_versions_delete ON public.disclosure_versions FOR DELETE USING (
  (org_id IS NOT NULL AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid)
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

DO $$
DECLARE pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  EXECUTE 'ALTER TABLE public.disclosure_acknowledgements ENABLE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS disclosure_ack_select ON public.disclosure_acknowledgements';
  EXECUTE format('CREATE POLICY disclosure_ack_select ON public.disclosure_acknowledgements FOR SELECT USING %s', pred);
  EXECUTE 'DROP POLICY IF EXISTS disclosure_ack_insert ON public.disclosure_acknowledgements';
  EXECUTE format('CREATE POLICY disclosure_ack_insert ON public.disclosure_acknowledgements FOR INSERT '
                 'WITH CHECK %s', pred);
  EXECUTE 'DROP POLICY IF EXISTS disclosure_ack_update ON public.disclosure_acknowledgements';
  EXECUTE format('CREATE POLICY disclosure_ack_update ON public.disclosure_acknowledgements FOR UPDATE USING %s '
                 'WITH CHECK %s', pred, pred);
  EXECUTE 'DROP POLICY IF EXISTS disclosure_ack_delete ON public.disclosure_acknowledgements';
  EXECUTE format('CREATE POLICY disclosure_ack_delete ON public.disclosure_acknowledgements FOR DELETE USING %s',
                 pred);
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.disclosures TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.disclosure_versions TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.disclosure_acknowledgements TO app_service;
