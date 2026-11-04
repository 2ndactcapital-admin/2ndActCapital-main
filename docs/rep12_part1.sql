-- REP12 — Replication Studio, sprint 12: the note-versus-replica check across
-- NEW issues. Every new or restated live note-terms version is checked for every
-- org that uses replication: rep11's verdict, a DRAFT replica when the note is
-- eligible (never approved automatically), the comparison when quotes exist —
-- else exactly what is missing — and a row in the "New issues" feed. Alerts go to
-- internal staff with view_replication as system-opened human tasks.
--
-- Applied by apps/api/scripts/_apply_rep12.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / CREATE OR REPLACE.
-- THE MIGRATION WRITES NO ROWS.
--
-- BI-TEMPORAL (Rule 3)
--   note_checks          ORG. VALID axis between checks of one note: a new or
--                        restated terms version (or a changed underlying state)
--                        closes the live check (valid_to) and inserts a new one.
--                        SYSTEM axis within one check (the alert task and the
--                        review decision point at its id): a status / alert change
--                        copies the outgoing state to a fresh id
--                        (archived_from_id, system_to) and the live row keeps its id.
--   note_check_settings  ORG, VALID axis: a change closes the live row and inserts
--                        the new one (one live row per org).
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- rep11 ADDITIVE: note_replica_comparisons gains computed_source ('person' |
-- 'automatic_check'); computed_by is NULL exactly for an automatic check (the
-- check is a platform job, not a person). Every existing row is 'person' with a
-- computed_by, so the new CHECK is added VALID.
--
-- NO AUTOMATIC CONFIRMATION. Nothing here writes link_state; the only writer of
-- link_state = 'resolved' stays services.underlying_resolution.confirm_resolution
-- (a Super Admin's action). An automatic replica is a DRAFT; approval stays
-- rep11's maker-checker human task.

-- ── 1. rep11's comparisons: an automatic check computes one with no person ──
ALTER TABLE public.note_replica_comparisons ADD COLUMN IF NOT EXISTS computed_source text NOT NULL DEFAULT 'person';
ALTER TABLE public.note_replica_comparisons ALTER COLUMN computed_by DROP NOT NULL;
ALTER TABLE public.note_replica_comparisons DROP CONSTRAINT IF EXISTS note_replica_comparisons_source_chk;
ALTER TABLE public.note_replica_comparisons ADD CONSTRAINT note_replica_comparisons_source_chk CHECK (
  computed_source IN ('person', 'automatic_check')
  AND ((computed_source = 'person') = (computed_by IS NOT NULL)));

-- ── 2. The checks (ORG) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.note_checks (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  global_security_id   uuid NOT NULL REFERENCES portfolio.securities_global(id),
  note_terms_id        uuid NOT NULL REFERENCES portfolio.securities_global_note_terms(id),
  replicability_id     uuid NOT NULL REFERENCES portfolio.note_replicability(id),
  input_key            text NOT NULL,
  trigger              text NOT NULL,
  issued_on            date NOT NULL,
  issued_basis         text NOT NULL,
  verdict              text NOT NULL,
  mapping              text,
  verdict_snapshot     jsonb NOT NULL,
  underlying_state     text NOT NULL,
  replica_outcome      text NOT NULL,
  not_built_reasons    jsonb NOT NULL DEFAULT '[]'::jsonb,
  replica_id           uuid REFERENCES public.note_replicas(id),
  replica_notes        jsonb NOT NULL DEFAULT '[]'::jsonb,
  comparison_id        uuid REFERENCES public.note_replica_comparisons(id),
  pricing_status       text NOT NULL,
  embedded_fee_pct     numeric,
  missing              jsonb NOT NULL DEFAULT '[]'::jsonb,
  alert_reasons        jsonb NOT NULL DEFAULT '[]'::jsonb,
  alert_task_id        uuid REFERENCES public.workflow_run_steps(id),
  status               text NOT NULL DEFAULT 'new',
  reviewed_by          uuid REFERENCES public.users(id),
  reviewed_at          timestamptz,
  review_note          text,
  checked_at           timestamptz NOT NULL DEFAULT now(),
  archived_from_id     uuid REFERENCES public.note_checks(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT note_checks_trigger_chk CHECK (trigger IN ('extraction', 'correction', 'sweep', 'underlying_confirmed',
                                                         'manual')),
  CONSTRAINT note_checks_issued_basis_chk CHECK (issued_basis IN ('filing_date', 'first_extracted')),
  CONSTRAINT note_checks_verdict_chk CHECK (verdict IN ('static', 'approximate', 'not_replicable')),
  CONSTRAINT note_checks_mapping_chk CHECK ((verdict = 'not_replicable') = (mapping IS NULL)),
  CONSTRAINT note_checks_underlying_chk CHECK (underlying_state IN ('resolved', 'ambiguous', 'unresolved', 'multiple',
                                                                    'none')),
  CONSTRAINT note_checks_outcome_chk CHECK (
       (replica_outcome IN ('prepared', 'existing') AND replica_id IS NOT NULL)
    OR (replica_outcome = 'not_built' AND replica_id IS NULL AND jsonb_array_length(not_built_reasons) > 0)),
  CONSTRAINT note_checks_pricing_chk CHECK (
       (pricing_status = 'priced' AND comparison_id IS NOT NULL AND embedded_fee_pct IS NOT NULL
        AND jsonb_array_length(missing) = 0)
    OR (pricing_status = 'not_priced' AND embedded_fee_pct IS NULL AND jsonb_array_length(missing) > 0)
    OR (pricing_status = 'no_replica' AND replica_id IS NULL AND comparison_id IS NULL AND embedded_fee_pct IS NULL)),
  CONSTRAINT note_checks_status_chk CHECK (status IN ('new', 'reviewed', 'dismissed')),
  CONSTRAINT note_checks_review_chk CHECK (
       (status = 'new' AND reviewed_by IS NULL AND reviewed_at IS NULL)
    OR (status IN ('reviewed', 'dismissed') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  CONSTRAINT note_checks_dismiss_note_chk CHECK (status <> 'dismissed'
                                                 OR length(btrim(COALESCE(review_note, ''))) > 0),
  CONSTRAINT note_checks_json_chk CHECK (jsonb_typeof(verdict_snapshot) = 'object'
                                         AND jsonb_typeof(not_built_reasons) = 'array'
                                         AND jsonb_typeof(replica_notes) = 'array'
                                         AND jsonb_typeof(missing) = 'array' AND jsonb_typeof(alert_reasons) = 'array'),
  CONSTRAINT note_checks_key_chk CHECK (length(btrim(input_key)) > 0),
  CONSTRAINT note_checks_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one live check per (org, note); a re-run of an unchanged input finds it and writes nothing
CREATE UNIQUE INDEX IF NOT EXISTS note_checks_live_uq ON public.note_checks (org_id, global_security_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS note_checks_feed_idx ON public.note_checks (org_id, issued_on DESC)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS note_checks_terms_idx ON public.note_checks (note_terms_id);

CREATE OR REPLACE FUNCTION public.note_checks_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'reviewed_by', 'reviewed_at', 'review_note', 'alert_task_id',
                            'alert_reasons', 'system_from', 'updated_at'];
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'a note check is never deleted — a newer check closes it' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived note check state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live check row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  -- closing (valid_to) and nothing else: a newer check of the same note supersedes it
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  IF NEW.valid_to IS DISTINCT FROM OLD.valid_to THEN
    RAISE EXCEPTION 'a check is closed once, by itself (valid_to only)' USING ERRCODE = '42501';
  END IF;
  -- otherwise only the lifecycle (review decision, alert) changes; the check's findings are frozen
  IF (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'a note check''s findings are written once; only its review status and alert change'
      USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> NEW.status AND NOT ((OLD.status || '>' || NEW.status) = ANY (ARRAY['new>reviewed', 'new>dismissed']))
  THEN
    RAISE EXCEPTION 'illegal note check transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS note_checks_guard_trg ON public.note_checks;
CREATE TRIGGER note_checks_guard_trg BEFORE UPDATE OR DELETE ON public.note_checks
  FOR EACH ROW EXECUTE FUNCTION public.note_checks_guard();

-- ── 3. The org's settings (ORG; valid axis) ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.note_check_settings (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                    uuid NOT NULL REFERENCES public.organizations(id),
  enabled                   boolean NOT NULL DEFAULT true,
  fee_alert_threshold_pct   numeric,
  watch_archetypes          text[] NOT NULL DEFAULT '{}',
  watch_issuers             text[] NOT NULL DEFAULT '{}',
  watch_underlyings         text[] NOT NULL DEFAULT '{}',
  created_by                uuid NOT NULL REFERENCES public.users(id),
  created_at                timestamptz NOT NULL DEFAULT now(),
  valid_from                timestamptz NOT NULL DEFAULT now(),
  valid_to                  timestamptz,
  system_from               timestamptz NOT NULL DEFAULT now(),
  system_to                 timestamptz,
  CONSTRAINT note_check_settings_threshold_chk CHECK (fee_alert_threshold_pct IS NULL
                                                     OR (fee_alert_threshold_pct >= 0 AND fee_alert_threshold_pct <= 100)),
  CONSTRAINT note_check_settings_archetypes_chk CHECK (watch_archetypes <@ ARRAY['autocallable', 'buffered_note',
                                                       'digital', 'principal_protected', 'reverse_convertible',
                                                       'other']::text[]),
  CONSTRAINT note_check_settings_lists_chk CHECK (cardinality(watch_issuers) <= 50
                                                  AND cardinality(watch_underlyings) <= 50
                                                  AND array_position(watch_issuers, '') IS NULL
                                                  AND array_position(watch_underlyings, '') IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS note_check_settings_live_uq ON public.note_check_settings (org_id)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.note_check_settings_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'settings are never deleted — a change closes the live row' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a settings row is written once and only ever closed (valid_to)' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS note_check_settings_guard_trg ON public.note_check_settings;
CREATE TRIGGER note_check_settings_guard_trg BEFORE UPDATE OR DELETE ON public.note_check_settings
  FOR EACH ROW EXECUTE FUNCTION public.note_check_settings_guard();

-- ── 4. RLS — a policy for every operation each table takes ────────────────
-- The caller's own org (NULLIF — an unset org GUC matches nothing), or super admin.
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['note_checks', 'note_check_settings'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t, pred,
                   pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;
