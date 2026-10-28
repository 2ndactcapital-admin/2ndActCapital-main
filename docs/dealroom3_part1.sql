-- DEALROOM3 part 1 — Marketplace Deal Room, Phase 3 schema (control and insight).
-- Additive. Applied by apps/api/scripts/_apply_dealroom3.py through Doppler on
-- the operator's postgres DSN (DDL needs the table owner), in ONE transaction.
-- Idempotent: every object is IF NOT EXISTS / guarded / CREATE OR REPLACE.
--
-- WHAT EXISTING DEALS KEEP (today's behaviour, by construction):
--   * deal_room_settings.downloads is added with DEFAULT 'allowed' and
--     watermark with DEFAULT 'off' — ADD COLUMN fills every EXISTING row with
--     that constant, so all 9 live deals keep downloads allowed and NO
--     watermark: their investors keep today's signed-URL download until a
--     manager turns watermarking on. ONLY THEN is the column default switched
--     to 'on', so every deal room created from now on starts watermarked (the
--     spec's default). access_period 'none', offering_type 'not_set' — today's
--     behaviour.
--   * deal_access: two new statuses (expired, paused) and two nullable columns;
--     no existing row is rewritten (0 rows live at discovery).
--
--   deal_room_settings    + downloads, watermark, access_period (+ until /
--                           days), expiry_notice_days, notify_publisher_on,
--                           offering_type
--   deal_documents        + share_unwatermarked (+ by / at)
--   deal_access           + statuses expired / paused (same CHECK names), access_ends_on,
--                           paused_reason; prior_level allowed on paused
--   deal_room_activity    APPEND-ONLY activity log (never deleted when access
--                           ends or the deal closes)
--   deal_room_watermarks  APPEND-ONLY: one row per watermarked rendition — the
--                           id printed on the page traces a leak to a person
--   deal_room_questions   Q&A questions (system-axis: id stable, history rows)
--   deal_room_answers     answers (system-axis)
--   deal_room_advisors    an investor's advisors (valid-axis)
--   deal_room_return_notices  return-or-destroy notices on revocation (valid-axis)
--   crm_relationship_dates    when the firm's relationship with a CRM entity
--                           began (valid-axis) — the 506(b) DRAFT rule's input
--   deal_room_folder_open     + an ACTIVE advisor is evaluated as their investor
--   deal_room_offering_open   NEW: the 506(b) DRAFT teaser rule

-- ── Settings ────────────────────────────────────────────────────────────────
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS downloads text NOT NULL DEFAULT 'allowed';
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS watermark text NOT NULL DEFAULT 'off';
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS access_period text NOT NULL DEFAULT 'none';
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS access_until date;
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS access_days integer;
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS expiry_notice_days integer NOT NULL DEFAULT 7;
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS notify_publisher_on text[] NOT NULL
  DEFAULT ARRAY['request', 'signature', 'question']::text[];
ALTER TABLE public.deal_room_settings ADD COLUMN IF NOT EXISTS offering_type text NOT NULL DEFAULT 'not_set';
-- Existing rows now hold watermark 'off' (filled above); new rooms default ON.
ALTER TABLE public.deal_room_settings ALTER COLUMN watermark SET DEFAULT 'on';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_room_settings_downloads_check') THEN
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_downloads_check
      CHECK (downloads IN ('allowed', 'view_only'));
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_watermark_check
      CHECK (watermark IN ('on', 'off'));
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_access_period_check
      CHECK (access_period IN ('none', 'until_date', 'days_after_approval'));
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_access_period_shape_check
      CHECK ((access_period = 'none' AND access_until IS NULL AND access_days IS NULL)
             OR (access_period = 'until_date' AND access_until IS NOT NULL AND access_days IS NULL)
             OR (access_period = 'days_after_approval' AND access_days IS NOT NULL AND access_days BETWEEN 1 AND 3650
                 AND access_until IS NULL));
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_expiry_notice_check
      CHECK (expiry_notice_days BETWEEN 0 AND 90);
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_notify_check
      CHECK (notify_publisher_on <@ ARRAY['request', 'signature', 'first_folder_open', 'question']::text[]);
    ALTER TABLE public.deal_room_settings ADD CONSTRAINT deal_room_settings_offering_type_check
      CHECK (offering_type IN ('not_set', 'rule_506b', 'rule_506c', 'other'));
  END IF;
END $$;

-- ── Documents: the manager's "share unwatermarked" mark ─────────────────────
ALTER TABLE public.deal_documents ADD COLUMN IF NOT EXISTS share_unwatermarked boolean NOT NULL DEFAULT false;
ALTER TABLE public.deal_documents ADD COLUMN IF NOT EXISTS share_unwatermarked_by uuid REFERENCES public.users(id);
ALTER TABLE public.deal_documents ADD COLUMN IF NOT EXISTS share_unwatermarked_at timestamptz;

-- ── Access: expired / paused ────────────────────────────────────────────────
ALTER TABLE public.deal_access ADD COLUMN IF NOT EXISTS access_ends_on date;
ALTER TABLE public.deal_access ADD COLUMN IF NOT EXISTS paused_reason text;
-- The two constraint NAMES are kept (deal_access_status_v2_check,
-- deal_access_prior_level_check — verify_dealroom2 reads them by name); only
-- their bodies widen. (A first apply named them _v3 / _prior_level_v2; the
-- renames below bring such a database back to the kept names.)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_access_status_v3_check') THEN
    ALTER TABLE public.deal_access DROP CONSTRAINT IF EXISTS deal_access_status_v2_check;
    ALTER TABLE public.deal_access RENAME CONSTRAINT deal_access_status_v3_check TO deal_access_status_v2_check;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_access_prior_level_v2_check') THEN
    ALTER TABLE public.deal_access DROP CONSTRAINT IF EXISTS deal_access_prior_level_check;
    ALTER TABLE public.deal_access RENAME CONSTRAINT deal_access_prior_level_v2_check TO deal_access_prior_level_check;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_access_status_v2_check'
                 AND pg_get_constraintdef(oid) LIKE '%paused%') THEN
    ALTER TABLE public.deal_access DROP CONSTRAINT IF EXISTS deal_access_status_v2_check;
    ALTER TABLE public.deal_access ADD CONSTRAINT deal_access_status_v2_check
      CHECK (status IN ('viewing', 'requested', 'not_eligible', 'signed_awaiting', 'granted', 'declined',
                        'revoked', 'resign_required', 'expired', 'paused'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_access_prior_level_check'
                 AND pg_get_constraintdef(oid) LIKE '%paused%') THEN
    ALTER TABLE public.deal_access DROP CONSTRAINT IF EXISTS deal_access_prior_level_check;
    ALTER TABLE public.deal_access ADD CONSTRAINT deal_access_prior_level_check
      CHECK (prior_level IS NULL OR (status IN ('resign_required', 'paused')
                                     AND prior_level IN ('after_agreements', 'after_approval')));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deal_access_paused_reason_check') THEN
    ALTER TABLE public.deal_access ADD CONSTRAINT deal_access_paused_reason_check
      CHECK ((status = 'paused') = (paused_reason IS NOT NULL));
  END IF;
END $$;

-- ── Advisors (valid-axis: every change restates; one live row per advisor+deal)
CREATE TABLE IF NOT EXISTS public.deal_room_advisors (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  deal_id            uuid NOT NULL REFERENCES public.deals(id),
  access_user_id     uuid NOT NULL REFERENCES public.users(id),     -- the investor
  investor_entity_id uuid REFERENCES public.entities(id),
  advisor_user_id    uuid NOT NULL REFERENCES public.users(id),
  name               text NOT NULL CHECK (length(btrim(name)) >= 2),
  firm               text,
  role               text NOT NULL CHECK (role IN ('lawyer', 'accountant', 'financial_adviser', 'other')),
  email              text NOT NULL,
  status             text NOT NULL CHECK (status IN ('invited', 'active', 'revoked')),
  ack_typed_name     text,
  ack_consent        boolean,
  ack_text_sha256    text CHECK (ack_text_sha256 IS NULL OR ack_text_sha256 ~ '^[0-9a-f]{64}$'),
  acknowledged_at    timestamptz,
  invited_by         uuid REFERENCES public.users(id),
  revoked_by         uuid REFERENCES public.users(id),
  revoked_reason     text,
  created_by         uuid REFERENCES public.users(id),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  CHECK (advisor_user_id <> access_user_id),
  CHECK ((status = 'active') = (acknowledged_at IS NOT NULL AND ack_consent IS TRUE
                                AND length(btrim(coalesce(ack_typed_name, ''))) >= 2) OR status = 'revoked'),
  CHECK (status <> 'revoked' OR length(btrim(coalesce(revoked_reason, ''))) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_room_advisors_live_uq
  ON public.deal_room_advisors (deal_id, advisor_user_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS deal_room_advisors_investor_idx
  ON public.deal_room_advisors (deal_id, access_user_id) WHERE valid_to IS NULL;

-- ── Q&A (system-axis: the id is stable — answers and merges point at it) ────
CREATE TABLE IF NOT EXISTS public.deal_room_questions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  deal_id          uuid NOT NULL REFERENCES public.deals(id),
  folder_id        uuid REFERENCES public.deal_folders(id),        -- NULL = general
  asked_by         uuid NOT NULL REFERENCES public.users(id),
  entity_id        uuid REFERENCES public.entities(id),
  body             text NOT NULL CHECK (length(btrim(body)) >= 3),
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'withdrawn', 'merged')),
  merged_into      uuid REFERENCES public.deal_room_questions(id),
  status_reason    text,
  status_by        uuid REFERENCES public.users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  history_of       uuid REFERENCES public.deal_room_questions(id),
  CHECK ((status = 'merged') = (merged_into IS NOT NULL)),
  CHECK (status NOT IN ('withdrawn', 'merged') OR length(btrim(coalesce(status_reason, ''))) > 0),
  CHECK ((system_to IS NULL) = (history_of IS NULL))
);
CREATE INDEX IF NOT EXISTS deal_room_questions_deal_idx ON public.deal_room_questions (deal_id) WHERE system_to IS NULL;

CREATE TABLE IF NOT EXISTS public.deal_room_answers (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  deal_id          uuid NOT NULL REFERENCES public.deals(id),
  question_id      uuid NOT NULL REFERENCES public.deal_room_questions(id),
  body             text NOT NULL CHECK (length(btrim(body)) >= 2),
  answered_by      uuid NOT NULL REFERENCES public.users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  history_of       uuid REFERENCES public.deal_room_answers(id),
  CHECK ((system_to IS NULL) = (history_of IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_room_answers_live_uq
  ON public.deal_room_answers (question_id) WHERE system_to IS NULL;

-- ── Return-or-destroy notices (valid-axis; one live row per revoked access) ──
CREATE TABLE IF NOT EXISTS public.deal_room_return_notices (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  deal_id          uuid NOT NULL REFERENCES public.deals(id),
  access_id        uuid NOT NULL REFERENCES public.deal_access(id),   -- the revoked row
  user_id          uuid NOT NULL REFERENCES public.users(id),
  entity_id        uuid REFERENCES public.entities(id),
  notice_text      text NOT NULL,
  notice_sha256    text NOT NULL CHECK (notice_sha256 ~ '^[0-9a-f]{64}$'),
  sent_by          uuid REFERENCES public.users(id),
  sent_at          timestamptz NOT NULL DEFAULT now(),
  acknowledged_at  timestamptz,
  ack_typed_name   text,
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  CHECK ((acknowledged_at IS NULL) = (ack_typed_name IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS deal_room_return_notices_live_uq
  ON public.deal_room_return_notices (access_id) WHERE valid_to IS NULL;

-- ── CRM: when the firm's relationship with an entity began (506(b) input) ───
CREATE TABLE IF NOT EXISTS public.crm_relationship_dates (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  entity_id        uuid NOT NULL REFERENCES public.entities(id),
  established_on   date NOT NULL,
  basis            text,
  recorded_by      uuid REFERENCES public.users(id),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_relationship_dates_live_uq
  ON public.crm_relationship_dates (entity_id) WHERE valid_to IS NULL;

-- ── Activity log + watermark register: APPEND-ONLY ──────────────────────────
CREATE TABLE IF NOT EXISTS public.deal_room_watermarks (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),   -- printed on every page
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  deal_id              uuid NOT NULL REFERENCES public.deals(id),
  document_id          uuid NOT NULL REFERENCES public.deal_documents(id),
  user_id              uuid NOT NULL REFERENCES public.users(id),
  entity_id            uuid REFERENCES public.entities(id),
  on_behalf_of_user_id uuid REFERENCES public.users(id),              -- an advisor's investor
  kind                 text NOT NULL CHECK (kind IN ('download', 'page')),
  page                 integer CHECK (page IS NULL OR page >= 1),
  source_sha256        text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  stamp_text           text NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((kind = 'page') = (page IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS deal_room_watermarks_doc_idx ON public.deal_room_watermarks (document_id);

CREATE TABLE IF NOT EXISTS public.deal_room_activity (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  deal_id              uuid NOT NULL REFERENCES public.deals(id),
  user_id              uuid NOT NULL REFERENCES public.users(id),
  entity_id            uuid REFERENCES public.entities(id),
  on_behalf_of_user_id uuid REFERENCES public.users(id),
  folder_id            uuid REFERENCES public.deal_folders(id),
  document_id          uuid REFERENCES public.deal_documents(id),
  action               text NOT NULL CHECK (action IN ('room_open', 'folder_open', 'view_page', 'download',
                                                       'question', 'answer_read')),
  page                 integer CHECK (page IS NULL OR page >= 1),
  watermark_id         uuid REFERENCES public.deal_room_watermarks(id),
  detail               jsonb,
  at                   timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (action <> 'view_page' OR (document_id IS NOT NULL AND page IS NOT NULL)),
  CHECK (action <> 'download' OR document_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS deal_room_activity_deal_idx ON public.deal_room_activity (deal_id, user_id, at);

-- Append-only guard for both logs. Refuses every UPDATE / DELETE, except the
-- verify fixture teardown: a NON-app_service session (the operator DSN) that
-- sets dealroom.activity_teardown = 'fixture' in its transaction, and only for
-- rows of an organizations.is_test org. Real rows are never removable.
CREATE OR REPLACE FUNCTION public.deal_room_log_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND current_user <> 'app_service'
     AND current_setting('dealroom.activity_teardown', true) = 'fixture'
     AND EXISTS (SELECT 1 FROM public.organizations o WHERE o.id = OLD.org_id AND o.is_test) THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END
$$;
CREATE OR REPLACE FUNCTION public.deal_room_log_no_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: TRUNCATE refused', TG_TABLE_NAME;
END
$$;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deal_room_activity', 'deal_room_watermarks'] LOOP
    EXECUTE format('CREATE OR REPLACE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
                   'FOR EACH ROW EXECUTE FUNCTION public.deal_room_log_append_only()', t || '_append_only', t);
    EXECUTE format('CREATE OR REPLACE TRIGGER %I BEFORE TRUNCATE ON public.%I '
                   'FOR EACH STATEMENT EXECUTE FUNCTION public.deal_room_log_no_truncate()', t || '_no_truncate', t);
  END LOOP;
END $$;

-- ── RLS ────────────────────────────────────────────────────────────────────
-- Read / write tables: one org-isolation policy per operation (SELECT, INSERT,
-- UPDATE, DELETE — each written out, so no operation silently matches zero
-- rows for want of a policy). The two logs: SELECT + INSERT only (no UPDATE /
-- DELETE policy → app_service matches zero rows; the trigger raises on any row
-- a privileged session reaches).
DO $$
DECLARE t text; op text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deal_room_advisors', 'deal_room_questions', 'deal_room_answers',
                           'deal_room_return_notices', 'crm_relationship_dates',
                           'deal_room_activity', 'deal_room_watermarks'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    FOREACH op IN ARRAY CASE WHEN t IN ('deal_room_activity', 'deal_room_watermarks')
                             THEN ARRAY['select', 'insert'] ELSE ARRAY['select', 'insert', 'update', 'delete'] END LOOP
      IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t
                     AND policyname = t || '_' || op) THEN
        IF op = 'insert' THEN
          EXECUTE format($p$CREATE POLICY %I ON public.%I FOR INSERT
            WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                        OR current_setting('app.is_super_admin', true) = 'true')$p$, t || '_' || op, t);
        ELSIF op = 'update' THEN
          EXECUTE format($p$CREATE POLICY %I ON public.%I FOR UPDATE
            USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                   OR current_setting('app.is_super_admin', true) = 'true')
            WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                        OR current_setting('app.is_super_admin', true) = 'true')$p$, t || '_' || op, t);
        ELSE
          EXECUTE format($p$CREATE POLICY %I ON public.%I FOR %s
            USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                   OR current_setting('app.is_super_admin', true) = 'true')$p$, t || '_' || op, t, upper(op));
        END IF;
      END IF;
    END LOOP;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;
REVOKE TRUNCATE ON public.deal_room_activity, public.deal_room_watermarks
  FROM anon, authenticated, service_role, app_service;
REVOKE UPDATE, DELETE ON public.deal_room_activity, public.deal_room_watermarks FROM anon, authenticated;

-- ── The ONE folder rule, advisor-aware ─────────────────────────────────────
-- Unchanged for investors. NEW: an ACTIVE (acknowledged) advisor of an
-- investor on this deal is evaluated AS that investor — the investor's live
-- row must be 'granted' (so revoked / expired / paused / resign_required close
-- the advisor's folders in the same statement), at the investor's level, with
-- the investor's named-folder grants. Never wider than the investor.
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
  subj AS (
    SELECT p_user AS uid
    UNION
    SELECT ad.access_user_id FROM public.deal_room_advisors ad, eff
    WHERE ad.deal_id = eff.deal_id AND ad.advisor_user_id = p_user AND ad.status = 'active'
      AND ad.valid_to IS NULL
  ),
  acc AS (
    SELECT a.user_id, a.level_granted FROM public.deal_access a, eff, subj
    WHERE a.deal_id = eff.deal_id AND a.user_id = subj.uid AND a.valid_to IS NULL AND a.status = 'granted'
  )
  SELECT CASE
    WHEN (SELECT rank FROM eff) IS NULL THEN false
    WHEN (SELECT rank FROM eff) = 0 THEN true
    WHEN NOT EXISTS (SELECT 1 FROM acc) THEN false
    WHEN (SELECT rank FROM eff) = 1 THEN true
    WHEN (SELECT rank FROM eff) = 2 THEN EXISTS (SELECT 1 FROM acc WHERE level_granted = 'after_approval')
    ELSE EXISTS (SELECT 1 FROM public.deal_folder_named n JOIN chain c ON c.id = n.folder_id
                 JOIN acc ON acc.user_id = n.user_id WHERE n.removed_at IS NULL)
  END
$$;
REVOKE ALL ON FUNCTION public.deal_room_folder_open(uuid, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.deal_room_folder_open(uuid, uuid) TO app_service;

-- ── 506(b) teaser rule (DRAFT — pending counsel review) ─────────────────────
-- True unless the deal's offering_type is rule_506b. Under 506(b): the deal's
-- publisher users and delegates always; anyone else only when their
-- relationship with the firm was established strictly BEFORE the deal's
-- publication date. The subject is the user (and, for an advisor, the investor
-- they advise); their relationship date is the EARLIEST over every entity they
-- may act for (active delegate grants) of crm_relationship_dates.established_on,
-- else that entity's created_at date (the day the firm first recorded them).
-- No entity → no relationship → closed. SECURITY INVOKER: RLS scopes it.
CREATE OR REPLACE FUNCTION public.deal_room_offering_open(p_deal uuid, p_user uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  WITH s AS (
    SELECT offering_type FROM public.deal_room_settings WHERE deal_id = p_deal AND valid_to IS NULL
  ),
  d AS (
    SELECT id, published_at, publisher_entity_id FROM public.deals
    WHERE id = p_deal AND valid_to IS NULL AND system_to IS NULL
  ),
  subj AS (
    SELECT p_user AS uid
    UNION
    SELECT ad.access_user_id FROM public.deal_room_advisors ad
    WHERE ad.deal_id = p_deal AND ad.advisor_user_id = p_user AND ad.status IN ('invited', 'active')
      AND ad.valid_to IS NULL
  ),
  ents AS (
    SELECT DISTINCT g.principal_entity_id AS eid FROM public.delegate_grants g JOIN subj ON g.delegate_user_id = subj.uid
    WHERE g.revoked_at IS NULL AND (NOT g.is_springing OR g.activated_at IS NOT NULL)
      AND (g.effective_from IS NULL OR g.effective_from <= now())
      AND (g.effective_until IS NULL OR g.effective_until > now())
  ),
  est AS (
    SELECT min(COALESCE((SELECT r.established_on FROM public.crm_relationship_dates r
                         WHERE r.entity_id = e.id AND r.valid_to IS NULL), e.created_at::date)) AS on_date
    FROM public.entities e JOIN ents ON ents.eid = e.id
    WHERE e.valid_to IS NULL AND e.system_to IS NULL
  )
  SELECT CASE
    WHEN COALESCE((SELECT offering_type FROM s), 'not_set') <> 'rule_506b' THEN true
    WHEN EXISTS (SELECT 1 FROM public.entity_publisher_users pu JOIN d ON pu.entity_id = d.publisher_entity_id
                 JOIN public.entity_publishers p ON p.entity_id = pu.entity_id AND p.revoked_at IS NULL
                 WHERE pu.user_id = p_user AND pu.revoked_at IS NULL) THEN true
    WHEN EXISTS (SELECT 1 FROM public.deal_delegates dd WHERE dd.deal_id = p_deal AND dd.user_id = p_user
                 AND dd.revoked_at IS NULL) THEN true
    ELSE COALESCE((SELECT on_date FROM est) < COALESCE((SELECT published_at FROM d), now())::date, false)
  END
$$;
REVOKE ALL ON FUNCTION public.deal_room_offering_open(uuid, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.deal_room_offering_open(uuid, uuid) TO app_service;

-- ── Labels (Rule 1) — every org, plus the default org the seed copies from ──
INSERT INTO public.config (org_id, config_key, config_value, category, display_order)
SELECT o.id, v.k, v.label, v.cat, v.ord
FROM public.organizations o
CROSS JOIN (VALUES
  ('deal_room_status_expired', 'Access expired', 'deal_room_access_statuses', 9),
  ('deal_room_status_paused', 'Paused — eligibility lapsed', 'deal_room_access_statuses', 10),
  ('deal_room_lock_expired', 'Your access period for this deal has ended. Ask the publisher to extend it.', 'deal_room_lock_reasons', 9),
  ('deal_room_lock_paused', 'Your access is paused because your investing entity''s eligibility has lapsed. It reopens once the record is updated.', 'deal_room_lock_reasons', 10),
  ('deal_room_downloads_allowed', 'Downloads allowed', 'deal_room_downloads', 1),
  ('deal_room_downloads_view_only', 'View only — no downloads', 'deal_room_downloads', 2),
  ('deal_room_watermark_on', 'Watermark every copy', 'deal_room_watermark', 1),
  ('deal_room_watermark_off', 'No watermark', 'deal_room_watermark', 2),
  ('deal_room_access_period_none', 'No end date', 'deal_room_access_periods', 1),
  ('deal_room_access_period_until_date', 'Until a date', 'deal_room_access_periods', 2),
  ('deal_room_access_period_days_after_approval', 'A number of days after approval', 'deal_room_access_periods', 3),
  ('deal_room_notify_request', 'A new access request', 'deal_room_notify_events', 1),
  ('deal_room_notify_signature', 'An agreement is signed', 'deal_room_notify_events', 2),
  ('deal_room_notify_first_folder_open', 'An investor first opens a folder', 'deal_room_notify_events', 3),
  ('deal_room_notify_question', 'A question is asked', 'deal_room_notify_events', 4),
  ('deal_room_offering_not_set', 'Not set', 'deal_room_offering_types', 1),
  ('deal_room_offering_rule_506b', 'Rule 506(b) — DRAFT, pending counsel review', 'deal_room_offering_types', 2),
  ('deal_room_offering_rule_506c', 'Rule 506(c) — DRAFT, pending counsel review', 'deal_room_offering_types', 3),
  ('deal_room_offering_other', 'Other', 'deal_room_offering_types', 4),
  ('deal_room_advisor_role_lawyer', 'Lawyer', 'deal_room_advisor_roles', 1),
  ('deal_room_advisor_role_accountant', 'Accountant', 'deal_room_advisor_roles', 2),
  ('deal_room_advisor_role_financial_adviser', 'Financial adviser', 'deal_room_advisor_roles', 3),
  ('deal_room_advisor_role_other', 'Other', 'deal_room_advisor_roles', 4),
  ('deal_room_advisor_status_invited', 'Invited — acknowledgement pending', 'deal_room_advisor_statuses', 1),
  ('deal_room_advisor_status_active', 'Active', 'deal_room_advisor_statuses', 2),
  ('deal_room_advisor_status_revoked', 'Removed', 'deal_room_advisor_statuses', 3),
  ('deal_room_question_status_open', 'Open', 'deal_room_question_statuses', 1),
  ('deal_room_question_status_answered', 'Answered', 'deal_room_question_statuses', 2),
  ('deal_room_question_status_withdrawn', 'Withdrawn', 'deal_room_question_statuses', 3),
  ('deal_room_question_status_merged', 'Merged into another question', 'deal_room_question_statuses', 4),
  ('deal_room_activity_room_open', 'Opened the room', 'deal_room_activity_actions', 1),
  ('deal_room_activity_folder_open', 'Opened a folder', 'deal_room_activity_actions', 2),
  ('deal_room_activity_view_page', 'Viewed a page', 'deal_room_activity_actions', 3),
  ('deal_room_activity_download', 'Downloaded', 'deal_room_activity_actions', 4),
  ('deal_room_activity_question', 'Asked a question', 'deal_room_activity_actions', 5),
  ('deal_room_activity_answer_read', 'Read an answer', 'deal_room_activity_actions', 6)
) AS v(k, label, cat, ord)
ON CONFLICT (org_id, config_key) DO NOTHING;
