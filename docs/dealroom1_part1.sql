-- DEALROOM1 part 1 — Marketplace Deal Room, Phase 1 schema.
-- Additive only. Applied live via the Supabase MCP (2026-10-22); the data
-- backfill (publishers, folders, document mapping) is a separate, reported
-- step: apps/api/scripts/_apply_dealroom1.py → docs/DEALROOM1_MIGRATION.json.
--
-- Every org-scoped table carries the ONE org-isolation policy shape used
-- everywhere (NULLIF on the org GUC, OR the super-admin carve-out), for ALL
-- operations — except deal_signatures, which is append-only: SELECT and INSERT
-- policies only (no UPDATE / DELETE policy, so app_service matches zero rows),
-- plus a trigger that refuses UPDATE / DELETE / TRUNCATE outright. The only
-- way a signature row is ever removed or altered is the fixture-teardown path:
-- a NON-app_service session (the operator's postgres DSN, which bypasses RLS)
-- that sets dealroom.signature_teardown = 'fixture' in its transaction, and
-- only on rows in the 'test' chain (signers whose users.is_test is true).
-- Production-chain rows cannot be changed by anyone short of the table owner
-- disabling the trigger.

-- ── Publishers (a CRM capability on an entity) ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.entity_publishers (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations(id),
  entity_id   uuid NOT NULL REFERENCES public.entities(id),
  is_house    boolean NOT NULL DEFAULT false,
  note        text,
  granted_by  uuid REFERENCES public.users(id),
  granted_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at  timestamptz,
  revoked_by  uuid REFERENCES public.users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS entity_publishers_active_uq
  ON public.entity_publishers (entity_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS entity_publishers_house_uq
  ON public.entity_publishers (org_id) WHERE is_house AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS public.entity_publisher_users (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations(id),
  entity_id   uuid NOT NULL REFERENCES public.entities(id),
  user_id     uuid NOT NULL REFERENCES public.users(id),
  granted_by  uuid REFERENCES public.users(id),
  granted_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at  timestamptz,
  revoked_by  uuid REFERENCES public.users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS entity_publisher_users_active_uq
  ON public.entity_publisher_users (entity_id, user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS entity_publisher_users_user_idx
  ON public.entity_publisher_users (user_id) WHERE revoked_at IS NULL;

ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS publisher_entity_id uuid
  REFERENCES public.entities(id);

-- ── Deal-scoped delegates ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.deal_delegates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations(id),
  deal_id     uuid NOT NULL REFERENCES public.deals(id),
  user_id     uuid NOT NULL REFERENCES public.users(id),
  granted_by  uuid REFERENCES public.users(id),
  granted_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at  timestamptz,
  revoked_by  uuid REFERENCES public.users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_delegates_active_uq
  ON public.deal_delegates (deal_id, user_id) WHERE revoked_at IS NULL;

-- ── Folders (system-axis: the id is stable, history rows carry history_of) ──
CREATE TABLE IF NOT EXISTS public.deal_folders (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES public.organizations(id),
  deal_id       uuid NOT NULL REFERENCES public.deals(id),
  key           text NOT NULL,
  label         text,
  sort_order    integer NOT NULL,
  access_level  text NOT NULL CHECK (access_level IN
                  ('public', 'after_agreements', 'after_approval', 'named_only')),
  parent_id     uuid REFERENCES public.deal_folders(id),
  is_standard   boolean NOT NULL DEFAULT false,
  created_by    uuid REFERENCES public.users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  system_from   timestamptz NOT NULL DEFAULT now(),
  system_to     timestamptz,
  history_of    uuid REFERENCES public.deal_folders(id),
  CHECK (parent_id IS NULL OR NOT is_standard),
  CHECK (is_standard OR label IS NOT NULL OR history_of IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_folders_live_key_uq
  ON public.deal_folders (deal_id, key) WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS deal_folders_deal_idx ON public.deal_folders (deal_id);

CREATE TABLE IF NOT EXISTS public.deal_folder_named (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organizations(id),
  folder_id   uuid NOT NULL REFERENCES public.deal_folders(id),
  user_id     uuid NOT NULL REFERENCES public.users(id),
  added_by    uuid REFERENCES public.users(id),
  added_at    timestamptz NOT NULL DEFAULT now(),
  removed_at  timestamptz,
  removed_by  uuid REFERENCES public.users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_folder_named_active_uq
  ON public.deal_folder_named (folder_id, user_id) WHERE removed_at IS NULL;

ALTER TABLE public.deal_documents ADD COLUMN IF NOT EXISTS folder_id uuid
  REFERENCES public.deal_folders(id);
ALTER TABLE public.deal_documents ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE public.deal_documents ADD COLUMN IF NOT EXISTS deleted_by uuid
  REFERENCES public.users(id);

-- ── Deal-room settings (valid-axis: one live row per deal) ─────────────────
CREATE TABLE IF NOT EXISTS public.deal_room_settings (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  deal_id           uuid NOT NULL REFERENCES public.deals(id),
  eligibility_rule  text NOT NULL DEFAULT 'accredited' CHECK (eligibility_rule IN
                      ('none', 'accredited', 'qualified_purchaser', 'qualified_client')),
  approval_mode     text NOT NULL DEFAULT 'automatic' CHECK (approval_mode IN
                      ('automatic', 'publisher')),
  sponsor_display   text NOT NULL DEFAULT 'name' CHECK (sponsor_display IN ('name', 'code_name')),
  code_name         text,
  created_by        uuid REFERENCES public.users(id),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  CHECK (sponsor_display = 'name' OR length(btrim(coalesce(code_name, ''))) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_room_settings_live_uq
  ON public.deal_room_settings (deal_id) WHERE valid_to IS NULL;

-- ── Agreement templates (platform rows have org_id NULL) ───────────────────
CREATE TABLE IF NOT EXISTS public.agreement_templates (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key            text NOT NULL,
  version        integer NOT NULL CHECK (version >= 1),
  title          text NOT NULL,
  body           text NOT NULL,
  fields         jsonb NOT NULL DEFAULT '[]'::jsonb,
  scope          text NOT NULL CHECK (scope IN ('platform', 'org')),
  org_id         uuid REFERENCES public.organizations(id),
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
  source_path    text,
  source_sha256  text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK ((scope = 'platform') = (org_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS agreement_templates_key_version_uq
  ON public.agreement_templates
  (key, version, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- ── A deal's required agreements (system-axis; id stable for signatures) ───
CREATE TABLE IF NOT EXISTS public.deal_agreements (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  deal_id              uuid NOT NULL REFERENCES public.deals(id),
  template_id          uuid NOT NULL REFERENCES public.agreement_templates(id),
  sort_order           integer NOT NULL,
  field_values         jsonb NOT NULL DEFAULT '{}'::jsonb,
  exhibit_document_id  uuid REFERENCES public.deal_documents(id),
  status               text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'withdrawn')),
  published_at         timestamptz,
  published_by         uuid REFERENCES public.users(id),
  created_by           uuid REFERENCES public.users(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  history_of           uuid REFERENCES public.deal_agreements(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_agreements_live_template_uq
  ON public.deal_agreements (deal_id, template_id)
  WHERE system_to IS NULL AND status <> 'withdrawn';

-- ── Signatures (append-only, hash-chained) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.deal_signatures (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 uuid NOT NULL REFERENCES public.organizations(id),
  deal_id                uuid NOT NULL REFERENCES public.deals(id),
  deal_agreement_id      uuid NOT NULL REFERENCES public.deal_agreements(id),
  template_id            uuid NOT NULL REFERENCES public.agreement_templates(id),
  template_version       integer NOT NULL,
  rendered_text          text NOT NULL,
  rendered_text_sha256   text NOT NULL CHECK (rendered_text_sha256 ~ '^[0-9a-f]{64}$'),
  user_id                uuid NOT NULL REFERENCES public.users(id),
  entity_id              uuid NOT NULL REFERENCES public.entities(id),
  typed_name             text NOT NULL CHECK (length(btrim(typed_name)) >= 2),
  consent                boolean NOT NULL CHECK (consent),
  signed_at              timestamptz NOT NULL,
  ip                     text,
  user_agent             text,
  chain                  text NOT NULL CHECK (chain IN ('platform', 'test')),
  chain_seq              bigint NOT NULL CHECK (chain_seq >= 1),
  prev_signature_sha256  text CHECK (prev_signature_sha256 IS NULL OR prev_signature_sha256 ~ '^[0-9a-f]{64}$'),
  record_sha256          text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK ((chain_seq = 1) = (prev_signature_sha256 IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_signatures_chain_seq_uq ON public.deal_signatures (chain, chain_seq);
CREATE UNIQUE INDEX IF NOT EXISTS deal_signatures_record_uq ON public.deal_signatures (record_sha256);
CREATE UNIQUE INDEX IF NOT EXISTS deal_signatures_no_fork_uq
  ON public.deal_signatures (chain, COALESCE(prev_signature_sha256, 'genesis'));
CREATE INDEX IF NOT EXISTS deal_signatures_deal_user_idx ON public.deal_signatures (deal_id, user_id);

-- ── Access requests and decisions (valid-axis history) ─────────────────────
CREATE TABLE IF NOT EXISTS public.deal_access (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES public.organizations(id),
  deal_id        uuid NOT NULL REFERENCES public.deals(id),
  user_id        uuid NOT NULL REFERENCES public.users(id),
  entity_id      uuid REFERENCES public.entities(id),
  status         text NOT NULL CHECK (status IN ('viewing', 'requested', 'not_eligible',
                   'signed_awaiting', 'granted', 'declined', 'revoked')),
  level_granted  text CHECK (level_granted IS NULL OR level_granted IN ('after_agreements', 'after_approval')),
  eligibility    jsonb,
  decided_by     uuid REFERENCES public.users(id),
  decided_at     timestamptz,
  reason         text,
  created_by     uuid REFERENCES public.users(id),
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  CHECK ((status = 'granted') = (level_granted IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_access_live_uq
  ON public.deal_access (deal_id, user_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS deal_access_deal_idx ON public.deal_access (deal_id) WHERE valid_to IS NULL;

-- ── RLS ────────────────────────────────────────────────────────────────────
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['entity_publishers', 'entity_publisher_users', 'deal_delegates',
                           'deal_folders', 'deal_folder_named', 'deal_room_settings',
                           'deal_agreements', 'deal_access']
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

ALTER TABLE public.agreement_templates ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'agreement_templates'
                 AND policyname = 'agreement_templates_read') THEN
    CREATE POLICY agreement_templates_read ON public.agreement_templates FOR SELECT
      USING (org_id IS NULL
             OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
             OR current_setting('app.is_super_admin', true) = 'true');
    -- Platform templates are written only under the super-admin carve-out;
    -- org-scope templates (Phase 2) by their own org.
    CREATE POLICY agreement_templates_insert ON public.agreement_templates FOR INSERT
      WITH CHECK (current_setting('app.is_super_admin', true) = 'true'
                  OR (org_id IS NOT NULL
                      AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
    CREATE POLICY agreement_templates_update ON public.agreement_templates FOR UPDATE
      USING (current_setting('app.is_super_admin', true) = 'true'
             OR (org_id IS NOT NULL
                 AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid))
      WITH CHECK (current_setting('app.is_super_admin', true) = 'true'
             OR (org_id IS NOT NULL
                 AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
    CREATE POLICY agreement_templates_delete ON public.agreement_templates FOR DELETE
      USING (current_setting('app.is_super_admin', true) = 'true'
             OR (org_id IS NOT NULL
                 AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.agreement_templates TO app_service;

-- deal_signatures: SELECT + INSERT only. No UPDATE / DELETE policy exists, so
-- an UPDATE or DELETE by app_service matches zero rows (silently — the trigger
-- below makes any row it DOES reach raise).
ALTER TABLE public.deal_signatures ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'deal_signatures'
                 AND policyname = 'deal_signatures_read') THEN
    CREATE POLICY deal_signatures_read ON public.deal_signatures FOR SELECT
      USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
             OR current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY deal_signatures_insert ON public.deal_signatures FOR INSERT
      WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                  OR current_setting('app.is_super_admin', true) = 'true');
  END IF;
END $$;
-- app_service keeps the UPDATE / DELETE *privilege* on purpose: the refusal must
-- come from the two layers this sprint specifies (no RLS policy → zero rows;
-- the trigger → an exception), each provable on its own. A privilege REVOKE
-- would fail first and hide both. TRUNCATE (which skips RLS and row triggers)
-- is held by nobody but the owner, and a statement trigger refuses it too.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.deal_signatures TO app_service;
REVOKE TRUNCATE ON public.deal_signatures FROM anon, authenticated, service_role, app_service;
REVOKE UPDATE, DELETE ON public.deal_signatures FROM anon, authenticated;

-- Append-only guard. Refuses every UPDATE / DELETE, except the fixture
-- teardown path described at the top of this file.
CREATE OR REPLACE FUNCTION public.deal_signatures_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE')
     AND current_setting('dealroom.signature_teardown', true) = 'fixture'
     AND session_user <> 'app_service'
     AND OLD.chain = 'test' THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  RAISE EXCEPTION 'deal_signatures is append-only: % refused', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE OR REPLACE TRIGGER deal_signatures_append_only
  BEFORE UPDATE OR DELETE ON public.deal_signatures
  FOR EACH ROW EXECUTE FUNCTION public.deal_signatures_append_only();

CREATE OR REPLACE FUNCTION public.deal_signatures_no_truncate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  RAISE EXCEPTION 'deal_signatures is append-only: TRUNCATE refused'
    USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE OR REPLACE TRIGGER deal_signatures_no_truncate
  BEFORE TRUNCATE ON public.deal_signatures
  FOR EACH STATEMENT EXECUTE FUNCTION public.deal_signatures_no_truncate();

-- Chain head (platform-wide, so it must read across orgs: SECURITY DEFINER,
-- returns only the head's sequence number and hash). The transaction-scoped
-- advisory lock serialises signers on the same chain until COMMIT.
CREATE OR REPLACE FUNCTION public.deal_signatures_chain_head(p_chain text)
RETURNS TABLE (seq bigint, sha text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF p_chain NOT IN ('platform', 'test') THEN
    RAISE EXCEPTION 'unknown signature chain %', p_chain;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('deal_signatures:' || p_chain));
  RETURN QUERY
    SELECT s.chain_seq, s.record_sha256 FROM public.deal_signatures s
    WHERE s.chain = p_chain ORDER BY s.chain_seq DESC LIMIT 1;
END
$$;

-- Insert-time linkage check: the row must extend the CURRENT head of its chain,
-- and its chain must match the signer's users.is_test.
CREATE OR REPLACE FUNCTION public.deal_signatures_link_check()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  head_seq bigint;
  head_sha text;
  signer_is_test boolean;
  want_chain text;
BEGIN
  SELECT coalesce(u.is_test, false) INTO signer_is_test FROM public.users u WHERE u.id = NEW.user_id;
  -- (plpgsql reads an IF condition up to its first THEN, so no CASE inside it)
  want_chain := 'platform';
  IF coalesce(signer_is_test, false) THEN
    want_chain := 'test';
  END IF;
  IF NEW.chain <> want_chain THEN
    RAISE EXCEPTION 'signature chain % does not match the signer', NEW.chain;
  END IF;
  SELECT s.chain_seq, s.record_sha256 INTO head_seq, head_sha FROM public.deal_signatures s
  WHERE s.chain = NEW.chain ORDER BY s.chain_seq DESC LIMIT 1;
  IF NEW.chain_seq <> coalesce(head_seq, 0) + 1
     OR NEW.prev_signature_sha256 IS DISTINCT FROM head_sha THEN
    RAISE EXCEPTION 'signature does not extend the head of chain %', NEW.chain
      USING ERRCODE = 'serialization_failure';
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER deal_signatures_link_check
  BEFORE INSERT ON public.deal_signatures
  FOR EACH ROW EXECUTE FUNCTION public.deal_signatures_link_check();

-- Copy the deal-room config rows (labels) from the default org to an org that
-- has none (a new org, or a verify fixture org). The org GUC must name p_org.
CREATE OR REPLACE FUNCTION public.deal_room_seed_org_config(p_org uuid)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE n integer;
BEGIN
  IF NOT (p_org = NULLIF(current_setting('app.current_org_id', true), '')::uuid
          OR current_setting('app.is_super_admin', true) = 'true') THEN
    RAISE EXCEPTION 'deal_room_seed_org_config: org context does not match';
  END IF;
  INSERT INTO public.config (org_id, config_key, config_value, value_type, category, display_order, is_active)
  SELECT p_org, c.config_key, c.config_value, c.value_type, c.category, c.display_order, c.is_active
  FROM public.config c
  WHERE c.org_id = '00000000-0000-0000-0000-000000000001'::uuid AND c.category LIKE 'deal\_room\_%'
  ON CONFLICT (org_id, config_key) DO NOTHING;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;

REVOKE ALL ON FUNCTION public.deal_signatures_chain_head(text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.deal_room_seed_org_config(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.deal_signatures_chain_head(text) TO app_service;
GRANT EXECUTE ON FUNCTION public.deal_room_seed_org_config(uuid) TO app_service;

-- ── Labels (Rule 1) — seeded for every existing org ────────────────────────
INSERT INTO public.config (org_id, config_key, config_value, category, display_order)
SELECT o.id, v.k, v.label, v.cat, v.ord
FROM public.organizations o
CROSS JOIN (VALUES
  ('deal_room_folder_01_overview', '01 Overview', 'deal_room_folders', 1),
  ('deal_room_folder_02_offering_memorandum', '02 Offering Memorandum', 'deal_room_folders', 2),
  ('deal_room_folder_03_presentations', '03 Presentations', 'deal_room_folders', 3),
  ('deal_room_folder_04_financials', '04 Financials', 'deal_room_folders', 4),
  ('deal_room_folder_05_legal_structure', '05 Legal & Structure', 'deal_room_folders', 5),
  ('deal_room_folder_06_due_diligence', '06 Due Diligence', 'deal_room_folders', 6),
  ('deal_room_folder_07_qa', '07 Q&A', 'deal_room_folders', 7),
  ('deal_room_folder_08_subscription', '08 Subscription', 'deal_room_folders', 8),
  ('deal_room_folder_09_other', '09 Other', 'deal_room_folders', 9),
  ('deal_room_folder_09_other_restricted', 'Restricted (moved from the previous document list)', 'deal_room_folders', 10),
  ('deal_room_level_public', 'Public', 'deal_room_access_levels', 1),
  ('deal_room_level_after_agreements', 'After agreements are signed', 'deal_room_access_levels', 2),
  ('deal_room_level_after_approval', 'After approval', 'deal_room_access_levels', 3),
  ('deal_room_level_named_only', 'Named investors only', 'deal_room_access_levels', 4),
  ('deal_room_lock_view', 'Request access to open the data room.', 'deal_room_lock_reasons', 1),
  ('deal_room_lock_after_agreements', 'Opens once you have signed the deal''s agreements.', 'deal_room_lock_reasons', 2),
  ('deal_room_lock_after_approval', 'Opens once the publisher approves your access to this folder.', 'deal_room_lock_reasons', 3),
  ('deal_room_lock_named_only', 'Open only to investors the publisher names on this folder.', 'deal_room_lock_reasons', 4),
  ('deal_room_lock_not_eligible', 'Your investing entity does not yet meet this deal''s eligibility rule.', 'deal_room_lock_reasons', 5),
  ('deal_room_lock_declined', 'The publisher has declined access to this deal.', 'deal_room_lock_reasons', 6),
  ('deal_room_lock_revoked', 'Your access to this deal has been withdrawn.', 'deal_room_lock_reasons', 7),
  ('deal_room_status_viewing', 'Viewing', 'deal_room_access_statuses', 1),
  ('deal_room_status_requested', 'Requested', 'deal_room_access_statuses', 2),
  ('deal_room_status_not_eligible', 'Not eligible', 'deal_room_access_statuses', 3),
  ('deal_room_status_signed_awaiting', 'Signed, awaiting approval', 'deal_room_access_statuses', 4),
  ('deal_room_status_granted', 'Granted', 'deal_room_access_statuses', 5),
  ('deal_room_status_declined', 'Declined', 'deal_room_access_statuses', 6),
  ('deal_room_status_revoked', 'Revoked', 'deal_room_access_statuses', 7),
  ('deal_room_rule_none', 'No eligibility rule', 'deal_room_eligibility_rules', 1),
  ('deal_room_rule_accredited', 'Accredited investor', 'deal_room_eligibility_rules', 2),
  ('deal_room_rule_qualified_purchaser', 'Qualified purchaser', 'deal_room_eligibility_rules', 3),
  ('deal_room_rule_qualified_client', 'Qualified client', 'deal_room_eligibility_rules', 4),
  ('deal_room_approval_automatic', 'Automatic once every agreement is signed', 'deal_room_approval_modes', 1),
  ('deal_room_approval_publisher', 'The publisher approves each investor', 'deal_room_approval_modes', 2),
  ('deal_room_sponsor_name', 'Show the sponsor''s name', 'deal_room_sponsor_display', 1),
  ('deal_room_sponsor_code_name', 'Show a code name', 'deal_room_sponsor_display', 2)
) AS v(k, label, cat, ord)
ON CONFLICT (org_id, config_key) DO NOTHING;

-- ── The ONE folder-access rule (investor side) ─────────────────────────────
-- True when p_user may read documents filed in p_folder BY INVESTOR ACCESS
-- alone. Publisher / delegate / staff / org-admin / super-admin rights are the
-- caller's to check first (they open every folder). The deal room listing,
-- the download route, GET /deals/{id} and Document Search all call this, so
-- there is one definition. SECURITY INVOKER: RLS still scopes every read.
--   effective level = the most restrictive level on the folder's parent chain
--   public            → open
--   anything else     → the caller's LIVE deal_access row must be 'granted'
--   after_agreements  → granted at any level
--   after_approval    → granted at level 'after_approval'
--   named_only        → granted AND named on the folder or an ancestor
CREATE OR REPLACE FUNCTION public.deal_room_folder_open(p_folder uuid, p_user uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  WITH RECURSIVE chain AS (
    SELECT f.id, f.parent_id, f.deal_id, f.access_level, 0 AS depth
    FROM public.deal_folders f WHERE f.id = p_folder AND f.system_to IS NULL
    UNION ALL
    SELECT p.id, p.parent_id, p.deal_id, p.access_level, c.depth + 1
    FROM public.deal_folders p JOIN chain c ON p.id = c.parent_id
    WHERE p.system_to IS NULL AND c.depth < 8
  ),
  eff AS (
    SELECT max(CASE access_level WHEN 'public' THEN 0 WHEN 'after_agreements' THEN 1
                                 WHEN 'after_approval' THEN 2 ELSE 3 END) AS rank,
           min(deal_id::text)::uuid AS deal_id
    FROM chain
  ),
  acc AS (
    SELECT a.status, a.level_granted FROM public.deal_access a, eff
    WHERE a.deal_id = eff.deal_id AND a.user_id = p_user AND a.valid_to IS NULL
  )
  SELECT CASE
    WHEN (SELECT rank FROM eff) IS NULL THEN false
    WHEN (SELECT rank FROM eff) = 0 THEN true
    WHEN NOT EXISTS (SELECT 1 FROM acc WHERE status = 'granted') THEN false
    WHEN (SELECT rank FROM eff) = 1 THEN true
    WHEN (SELECT rank FROM eff) = 2 THEN EXISTS (SELECT 1 FROM acc WHERE level_granted = 'after_approval')
    ELSE EXISTS (SELECT 1 FROM public.deal_folder_named n JOIN chain c ON c.id = n.folder_id
                 WHERE n.user_id = p_user AND n.removed_at IS NULL)
  END
$$;
REVOKE ALL ON FUNCTION public.deal_room_folder_open(uuid, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.deal_room_folder_open(uuid, uuid) TO app_service;
