-- DEALROOM2 part 1 — Marketplace Deal Room, Phase 2 schema.
-- Additive. Applied by apps/api/scripts/_apply_dealroom2.py --schema on the
-- operator's postgres DSN (DDL needs the table owner; the Supabase MCP cancels
-- any migration text containing the word that relaxes a NOT NULL).
--
--   agreement_versions      every signable agreement as an immutable version
--   deal_agreements         + kind ('template' | 'upload'), title, document_id,
--                           file_sha256; template_id nullable for uploads
--   deal_signatures         + agreement_version_id, document_sha256,
--                           record_version (1 = Phase 1 canonical record,
--                           2 = Phase 2); template_id / version nullable
--   deal_access             + status 'resign_required', prior_level, resign_after
--   agreement_templates     + based_on_id, created_by, published_at/by; content
--                           of a non-draft template is immutable (trigger)
--   deal_master_ndas        a publisher's master NDA (+ selected deals)
--
-- The append-only rule on deal_signatures is unchanged: no UPDATE / DELETE
-- policy, the append-only trigger, the chain-head insert trigger. ADD COLUMN
-- with a constant default rewrites no row and fires no row trigger.

-- ── Deal agreements: template or uploaded PDF ───────────────────────────────
ALTER TABLE public.deal_agreements ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'template';
ALTER TABLE public.deal_agreements ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE public.deal_agreements ADD COLUMN IF NOT EXISTS document_id uuid REFERENCES public.deal_documents(id);
ALTER TABLE public.deal_agreements ADD COLUMN IF NOT EXISTS file_sha256 text;
ALTER TABLE public.deal_agreements ALTER COLUMN template_id DROP NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_agreements_kind_check') THEN
    ALTER TABLE public.deal_agreements ADD CONSTRAINT deal_agreements_kind_check
      CHECK (kind IN ('template', 'upload'));
    ALTER TABLE public.deal_agreements ADD CONSTRAINT deal_agreements_kind_shape_check
      CHECK ((kind = 'template' AND template_id IS NOT NULL)
             OR (kind = 'upload' AND template_id IS NULL AND document_id IS NOT NULL
                 AND length(btrim(coalesce(title, ''))) > 0 AND file_sha256 ~ '^[0-9a-f]{64}$'));
  END IF;
END $$;

-- ── Agreement versions (immutable) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.agreement_versions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  deal_id              uuid NOT NULL REFERENCES public.deals(id),
  deal_agreement_id    uuid NOT NULL REFERENCES public.deal_agreements(id),
  version              integer NOT NULL CHECK (version >= 1),
  kind                 text NOT NULL CHECK (kind IN ('template', 'upload')),
  title                text NOT NULL,
  template_id          uuid REFERENCES public.agreement_templates(id),
  template_key         text,
  template_version     integer,
  template_status      text,
  body                 text,
  fields               jsonb,
  field_values         jsonb NOT NULL DEFAULT '{}'::jsonb,
  exhibit_document_id  uuid REFERENCES public.deal_documents(id),
  exhibit_sha256       text CHECK (exhibit_sha256 IS NULL OR exhibit_sha256 ~ '^[0-9a-f]{64}$'),
  document_id          uuid REFERENCES public.deal_documents(id),
  file_name            text,
  file_sha256          text CHECK (file_sha256 IS NULL OR file_sha256 ~ '^[0-9a-f]{64}$'),
  page_count           integer CHECK (page_count IS NULL OR page_count >= 1),
  resign_policy        text NOT NULL CHECK (resign_policy IN ('initial', 'keep', 'resign')),
  created_by           uuid REFERENCES public.users(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  CHECK ((version = 1) = (resign_policy = 'initial')),
  CHECK ((kind = 'template' AND template_id IS NOT NULL AND body IS NOT NULL AND fields IS NOT NULL
          AND document_id IS NULL)
         OR (kind = 'upload' AND template_id IS NULL AND document_id IS NOT NULL AND file_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS agreement_versions_agreement_version_uq
  ON public.agreement_versions (deal_agreement_id, version);
CREATE INDEX IF NOT EXISTS agreement_versions_deal_idx ON public.agreement_versions (deal_id);

ALTER TABLE public.agreement_versions ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agreement_versions'
                 AND policyname = 'agreement_versions_read') THEN
    CREATE POLICY agreement_versions_read ON public.agreement_versions FOR SELECT
      USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
             OR current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY agreement_versions_insert ON public.agreement_versions FOR INSERT
      WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                  OR current_setting('app.is_super_admin', true) = 'true');
  END IF;
END $$;
-- Same two refusal layers as deal_signatures: no UPDATE / DELETE policy, and a
-- trigger. The single exception is the fixture teardown (non-app_service
-- session + dealroom.signature_teardown = 'fixture'), DELETE only.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.agreement_versions TO app_service;
REVOKE TRUNCATE ON public.agreement_versions FROM anon, authenticated, service_role, app_service;
REVOKE UPDATE, DELETE ON public.agreement_versions FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.agreement_versions_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_setting('dealroom.signature_teardown', true) = 'fixture'
     AND session_user <> 'app_service' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'agreement_versions is immutable: % refused', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE OR REPLACE TRIGGER agreement_versions_immutable
  BEFORE UPDATE OR DELETE ON public.agreement_versions
  FOR EACH ROW EXECUTE FUNCTION public.agreement_versions_immutable();

-- ── Signatures: point at the exact version and its document hash ───────────
ALTER TABLE public.deal_signatures ADD COLUMN IF NOT EXISTS agreement_version_id uuid
  REFERENCES public.agreement_versions(id);
ALTER TABLE public.deal_signatures ADD COLUMN IF NOT EXISTS document_sha256 text;
ALTER TABLE public.deal_signatures ADD COLUMN IF NOT EXISTS record_version smallint NOT NULL DEFAULT 1;
ALTER TABLE public.deal_signatures ALTER COLUMN template_id DROP NOT NULL;
ALTER TABLE public.deal_signatures ALTER COLUMN template_version DROP NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_signatures_record_version_check') THEN
    ALTER TABLE public.deal_signatures ADD CONSTRAINT deal_signatures_record_version_check
      CHECK ((record_version = 1 AND template_id IS NOT NULL AND template_version IS NOT NULL
              AND agreement_version_id IS NULL AND document_sha256 IS NULL)
             OR (record_version = 2 AND agreement_version_id IS NOT NULL
                 AND document_sha256 ~ '^[0-9a-f]{64}$'));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS deal_signatures_agreement_entity_idx
  ON public.deal_signatures (deal_agreement_id, entity_id);

-- ── Access: re-signing ─────────────────────────────────────────────────────
ALTER TABLE public.deal_access ADD COLUMN IF NOT EXISTS prior_level text;
ALTER TABLE public.deal_access ADD COLUMN IF NOT EXISTS resign_after timestamptz;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_access_status_v2_check') THEN
    ALTER TABLE public.deal_access DROP CONSTRAINT IF EXISTS deal_access_status_check;
    ALTER TABLE public.deal_access ADD CONSTRAINT deal_access_status_v2_check
      CHECK (status IN ('viewing', 'requested', 'not_eligible', 'signed_awaiting', 'granted', 'declined',
                        'revoked', 'resign_required'));
    ALTER TABLE public.deal_access ADD CONSTRAINT deal_access_prior_level_check
      CHECK (prior_level IS NULL OR (status = 'resign_required'
                                     AND prior_level IN ('after_agreements', 'after_approval')));
  END IF;
END $$;

-- ── Templates: firm library lineage + immutability once out of draft ───────
ALTER TABLE public.agreement_templates ADD COLUMN IF NOT EXISTS based_on_id uuid
  REFERENCES public.agreement_templates(id);
ALTER TABLE public.agreement_templates ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES public.users(id);
ALTER TABLE public.agreement_templates ADD COLUMN IF NOT EXISTS published_at timestamptz;
ALTER TABLE public.agreement_templates ADD COLUMN IF NOT EXISTS published_by uuid REFERENCES public.users(id);

-- key / version / scope / org never change; a non-draft template's content
-- never changes (only its status may move: active → retired). A non-draft row
-- is never deleted, except by the fixture teardown path.
CREATE OR REPLACE FUNCTION public.agreement_templates_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status = 'draft'
       OR (current_setting('dealroom.signature_teardown', true) = 'fixture' AND session_user <> 'app_service') THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'agreement template % v% is % and is kept forever', OLD.key, OLD.version, OLD.status
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.key IS DISTINCT FROM OLD.key OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.scope IS DISTINCT FROM OLD.scope OR NEW.org_id IS DISTINCT FROM OLD.org_id THEN
    RAISE EXCEPTION 'agreement template identity (key, version, scope, org) is fixed'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status <> 'draft' AND (NEW.title IS DISTINCT FROM OLD.title OR NEW.body IS DISTINCT FROM OLD.body
                                OR NEW.fields IS DISTINCT FROM OLD.fields) THEN
    RAISE EXCEPTION 'agreement template % v% is % — its text is fixed; create a new version', OLD.key,
      OLD.version, OLD.status USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'retired' AND NEW.status <> 'retired' THEN
    RAISE EXCEPTION 'a retired template stays retired' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;
CREATE OR REPLACE TRIGGER agreement_templates_guard
  BEFORE UPDATE OR DELETE ON public.agreement_templates
  FOR EACH ROW EXECUTE FUNCTION public.agreement_templates_guard();

-- ── Master NDA (one live per publisher) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.deal_master_ndas (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  publisher_entity_id   uuid NOT NULL REFERENCES public.entities(id),
  deal_agreement_id     uuid NOT NULL REFERENCES public.deal_agreements(id),
  scope                 text NOT NULL CHECK (scope IN ('all', 'selected')),
  accepted_min_version  integer CHECK (accepted_min_version IS NULL OR accepted_min_version >= 1),
  created_by            uuid REFERENCES public.users(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  revoked_at            timestamptz,
  revoked_by            uuid REFERENCES public.users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_master_ndas_live_uq
  ON public.deal_master_ndas (publisher_entity_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS public.deal_master_nda_deals (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations(id),
  master_id   uuid NOT NULL REFERENCES public.deal_master_ndas(id),
  deal_id     uuid NOT NULL REFERENCES public.deals(id),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_master_nda_deals_uq ON public.deal_master_nda_deals (master_id, deal_id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deal_master_ndas', 'deal_master_nda_deals']
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t
                   AND policyname = t || '_org_isolation') THEN
      EXECUTE format($p$CREATE POLICY %I ON public.%I FOR ALL
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
               OR current_setting('app.is_super_admin', true) = 'true')
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
               OR current_setting('app.is_super_admin', true) = 'true')$p$, t || '_org_isolation', t);
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;

-- ── Labels (Rule 1) — every existing org; new orgs copy them from the default ─
INSERT INTO public.config (org_id, config_key, config_value, category, display_order)
SELECT o.id, v.k, v.label, v.cat, v.ord
FROM public.organizations o
CROSS JOIN (VALUES
  ('deal_room_status_resign_required', 'Re-signing required', 'deal_room_access_statuses', 8),
  ('deal_room_lock_resign_required', 'Opens again once you sign the updated agreement.', 'deal_room_lock_reasons', 8),
  ('deal_room_resign_keep', 'Keep existing signers — new investors sign the new version', 'deal_room_resign_policies', 1),
  ('deal_room_resign_resign', 'Require re-signing — existing signers lose access above public until they sign',
   'deal_room_resign_policies', 2),
  ('deal_room_master_scope_all', 'All of this publisher''s deals', 'deal_room_master_scopes', 1),
  ('deal_room_master_scope_selected', 'Selected deals', 'deal_room_master_scopes', 2),
  ('deal_room_kind_template', 'Template', 'deal_room_agreement_kinds', 1),
  ('deal_room_kind_upload', 'Uploaded PDF (no fill-in fields)', 'deal_room_agreement_kinds', 2)
) AS v(k, label, cat, ord)
ON CONFLICT (org_id, config_key) DO NOTHING;
