-- REP11 — Replication Studio, sprint 11 (phase 3 starts: STRUCTURED NOTES).
-- Note replication from the 424B2 terms: a platform replicability verdict per
-- live note-terms row, org-scoped replicas built from rep8's options machinery
-- (underlying + listed / FLEX options, plus a Treasury leg where the note
-- promises principal), and insert-only note-versus-replica comparisons (the
-- embedded-fee ESTIMATE, only when every quote exists).
--
-- Applied by apps/api/scripts/_apply_rep11.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / CREATE OR REPLACE.
--
-- THE MIGRATION WRITES NO ROWS. Note terms are PUBLIC platform data and are
-- only READ here; verdicts are written by the verdict job (a platform job under
-- app.is_super_admin), replicas and comparisons by a person in their own org.
--
-- BI-TEMPORAL (Rule 3)
--   note_replicability        PLATFORM, VALID axis: a recompute (the note's live
--                             terms changed, or the rule version did) closes the
--                             live row (valid_to) and inserts the new verdict;
--                             nothing else ever changes a row.
--   note_replicas             ORG, SYSTEM axis (legs, comparisons and the
--                             approval task point at its id): every change copies
--                             the outgoing state to a fresh id (archived_from_id,
--                             system_to) and the live row keeps its id.
--   note_replica_legs         ORG, VALID axis: written once on a DRAFT, only ever
--                             closed (a rebuild closes them and writes new legs).
--   note_replica_comparisons  ORG, INSERT-ONLY: a new pricing is a new row.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- THE CUSTODY CLIFF. Nothing here trades. A replica's legs are manual
-- instructions a person carries out; this migration creates no execution path.

-- ── 1. Adviser quotes: the note's price and a Treasury yield ──────────────
-- rep8's insert-only adviser_quotes gains two kinds. 'note_price' is the note's
-- price in percent of par (e.g. 98.75) on a structured_note security;
-- 'treasury_yield' is a yield in percent a year (annual compounding, e.g. 4.25)
-- on a fixed_income security. Existing rows keep their kinds (the CHECK is
-- re-added without NOT VALID, so the ALTER fails if any row would not satisfy it).
ALTER TABLE portfolio.adviser_quotes DROP CONSTRAINT IF EXISTS adviser_quotes_kind_chk;
ALTER TABLE portfolio.adviser_quotes ADD CONSTRAINT adviser_quotes_kind_chk CHECK (kind IN (
  'premium', 'index_level', 'underlying_price', 'implied_vol', 'note_price', 'treasury_yield'));

CREATE OR REPLACE FUNCTION portfolio.adviser_quotes_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  st text;
  is_option boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'quotes are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'a quote is never updated in place — enter a new quote' USING ERRCODE = '42501';
  END IF;
  SELECT security_type INTO st FROM portfolio.securities_global WHERE id = NEW.global_security_id;
  is_option := st = 'option' AND EXISTS (SELECT 1 FROM portfolio.option_contracts c
                                         WHERE c.global_security_id = NEW.global_security_id);
  IF NEW.kind IN ('premium', 'implied_vol') AND NOT is_option THEN
    RAISE EXCEPTION 'a % quote is for an option contract', NEW.kind USING ERRCODE = '23514';
  END IF;
  IF NEW.kind = 'index_level' AND st IS DISTINCT FROM 'index' THEN
    RAISE EXCEPTION 'an index level quote is for an index' USING ERRCODE = '23514';
  END IF;
  IF NEW.kind = 'underlying_price' AND (st IS NULL OR st IN ('option', 'index', 'structured_note')) THEN
    RAISE EXCEPTION 'an underlying price quote is for a stock or fund' USING ERRCODE = '23514';
  END IF;
  IF NEW.kind = 'note_price' AND st IS DISTINCT FROM 'structured_note' THEN
    RAISE EXCEPTION 'a note price quote is for a structured note' USING ERRCODE = '23514';
  END IF;
  IF NEW.kind = 'treasury_yield' AND st IS DISTINCT FROM 'fixed_income' THEN
    RAISE EXCEPTION 'a Treasury yield quote is for a fixed-income security' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

-- ── 2. The verdict (PLATFORM; valid axis) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.note_replicability (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  note_terms_id       uuid NOT NULL REFERENCES portfolio.securities_global_note_terms(id),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  terms_fingerprint   text NOT NULL,
  rule_version        text NOT NULL,
  verdict             text NOT NULL,
  mapping             text,
  reasons             jsonb NOT NULL DEFAULT '[]'::jsonb,
  spec                jsonb,
  is_preliminary      boolean NOT NULL,
  needs_review        boolean NOT NULL,
  review_reasons      jsonb NOT NULL DEFAULT '[]'::jsonb,
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  computed_at         timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT note_replicability_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT note_replicability_verdict_chk CHECK (verdict IN ('static', 'approximate', 'not_replicable')),
  CONSTRAINT note_replicability_mapping_chk CHECK (
       (verdict = 'not_replicable' AND mapping IS NULL)
    OR (verdict <> 'not_replicable' AND mapping IN ('buffer_package', 'principal_protected', 'digital_call_spread',
                                                    'reverse_convertible'))),
  CONSTRAINT note_replicability_reasons_chk CHECK (jsonb_typeof(reasons) = 'array' AND jsonb_array_length(reasons) > 0),
  CONSTRAINT note_replicability_review_chk CHECK (jsonb_typeof(review_reasons) = 'array'
                                                  AND (needs_review = (jsonb_array_length(review_reasons) > 0))),
  CONSTRAINT note_replicability_spec_chk CHECK ((verdict = 'not_replicable') = (spec IS NULL)),
  CONSTRAINT note_replicability_fp_chk CHECK (length(btrim(terms_fingerprint)) > 0 AND length(btrim(rule_version)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS note_replicability_live_uq ON portfolio.note_replicability (note_terms_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS note_replicability_security_idx ON portfolio.note_replicability (global_security_id)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION portfolio.note_replicability_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'a verdict is never deleted — a recompute closes it' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a verdict is written once and only ever closed (valid_to)' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS note_replicability_guard_trg ON portfolio.note_replicability;
CREATE TRIGGER note_replicability_guard_trg BEFORE UPDATE OR DELETE ON portfolio.note_replicability
  FOR EACH ROW EXECUTE FUNCTION portfolio.note_replicability_guard();

-- ── 3. Replicas (ORG; system axis) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.note_replicas (
  id                             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                         uuid NOT NULL REFERENCES public.organizations(id),
  global_security_id             uuid NOT NULL REFERENCES portfolio.securities_global(id),
  note_terms_id                  uuid NOT NULL REFERENCES portfolio.securities_global_note_terms(id),
  replicability_id               uuid NOT NULL REFERENCES portfolio.note_replicability(id),
  verdict                        text NOT NULL,
  mapping                        text NOT NULL,
  spec                           jsonb NOT NULL,
  underlying_global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  outcome_start                  date NOT NULL,
  outcome_end                    date NOT NULL,
  start_level                    numeric,
  start_level_quote_id           uuid REFERENCES portfolio.adviser_quotes(id),
  treasury_face_pct              numeric,
  treasury_maturity              date,
  treasury_global_security_id    uuid REFERENCES portfolio.securities_global(id),
  broad_based_index              boolean NOT NULL,
  exercise_style                 text NOT NULL,
  settlement                     text NOT NULL,
  is_flex                        boolean NOT NULL,
  multiplier                     numeric NOT NULL,
  digital_spread_pct             numeric,
  computed                       jsonb NOT NULL DEFAULT '{}'::jsonb,
  approximation                  jsonb,
  status                         text NOT NULL DEFAULT 'draft',
  maker_ids                      uuid[] NOT NULL DEFAULT '{}',
  checker_id                     uuid REFERENCES public.users(id),
  approval_task_id               uuid REFERENCES public.workflow_run_steps(id),
  decided_at                     timestamptz,
  decision_note                  text,
  status_reason                  text,
  created_by                     uuid REFERENCES public.users(id),
  archived_from_id               uuid REFERENCES public.note_replicas(id),
  created_at                     timestamptz NOT NULL DEFAULT now(),
  updated_at                     timestamptz NOT NULL DEFAULT now(),
  valid_from                     timestamptz NOT NULL DEFAULT now(),
  valid_to                       timestamptz,
  system_from                    timestamptz NOT NULL DEFAULT now(),
  system_to                      timestamptz,
  CONSTRAINT note_replicas_verdict_chk CHECK (verdict IN ('static', 'approximate')),
  CONSTRAINT note_replicas_mapping_chk CHECK (mapping IN ('buffer_package', 'principal_protected',
                                                          'digital_call_spread', 'reverse_convertible')),
  CONSTRAINT note_replicas_status_chk CHECK (status IN ('draft', 'pending_approval', 'approved', 'archived')),
  CONSTRAINT note_replicas_period_chk CHECK (outcome_end > outcome_start),
  CONSTRAINT note_replicas_level_chk CHECK (start_level IS NULL OR start_level > 0),
  CONSTRAINT note_replicas_treasury_chk CHECK ((treasury_face_pct IS NULL) = (treasury_maturity IS NULL)
                                               AND (treasury_face_pct IS NULL OR treasury_face_pct > 0)),
  CONSTRAINT note_replicas_treasury_mapping_chk CHECK ((mapping = 'buffer_package') = (treasury_face_pct IS NULL)),
  CONSTRAINT note_replicas_style_chk CHECK (exercise_style IN ('european', 'american')),
  CONSTRAINT note_replicas_settlement_chk CHECK (settlement IN ('cash', 'physical')),
  CONSTRAINT note_replicas_multiplier_chk CHECK (multiplier > 0),
  CONSTRAINT note_replicas_spread_chk CHECK (digital_spread_pct IS NULL OR (digital_spread_pct > 0
                                                                            AND digital_spread_pct < 1)),
  CONSTRAINT note_replicas_maker_checker_chk CHECK (checker_id IS NULL OR NOT (checker_id = ANY (maker_ids))),
  CONSTRAINT note_replicas_approved_chk CHECK (status <> 'approved' OR system_to IS NOT NULL
                                              OR (checker_id IS NOT NULL AND decided_at IS NOT NULL
                                                  AND start_level IS NOT NULL)),
  CONSTRAINT note_replicas_pending_chk CHECK (status <> 'pending_approval' OR system_to IS NOT NULL
                                             OR approval_task_id IS NOT NULL),
  CONSTRAINT note_replicas_archived_reason_chk CHECK (status <> 'archived' OR system_to IS NOT NULL
                                                     OR length(btrim(COALESCE(status_reason, ''))) > 0),
  CONSTRAINT note_replicas_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS note_replicas_open_uq ON public.note_replicas (org_id, global_security_id)
  WHERE status IN ('draft', 'pending_approval') AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS note_replicas_approved_uq ON public.note_replicas (org_id, global_security_id)
  WHERE status = 'approved' AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS note_replicas_note_idx ON public.note_replicas (org_id, global_security_id)
  WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.note_replicas_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'checker_id', 'approval_task_id', 'decided_at', 'decision_note',
                            'status_reason', 'system_from', 'updated_at', 'maker_ids'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'note replicas are never deleted — archive them' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived note replica state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live replica row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' AND (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'a note replica''s terms are frozen once submitted (status %)', OLD.status
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY['draft>pending_approval', 'pending_approval>approved',
                                                         'pending_approval>draft', 'approved>archived',
                                                         'draft>archived'])) THEN
    RAISE EXCEPTION 'illegal note replica transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS note_replicas_guard_trg ON public.note_replicas;
CREATE TRIGGER note_replicas_guard_trg BEFORE UPDATE OR DELETE ON public.note_replicas
  FOR EACH ROW EXECUTE FUNCTION public.note_replicas_guard();

-- ── 4. Replica legs (ORG; valid axis) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.note_replica_legs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  replica_id          uuid NOT NULL REFERENCES public.note_replicas(id),
  leg_no              integer NOT NULL,
  kind                text NOT NULL,
  purpose             text NOT NULL,
  option_type         text,
  direction           text NOT NULL,
  ratio               numeric NOT NULL,
  strike_pct          numeric,
  strike_level        numeric,
  expiry              date,
  contract_id         uuid REFERENCES portfolio.option_contracts(id),
  face_pct            numeric,
  maturity            date,
  created_by          uuid REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT note_replica_legs_kind_chk CHECK (kind IN ('underlying', 'option', 'treasury')),
  CONSTRAINT note_replica_legs_direction_chk CHECK (direction IN ('long', 'short')),
  CONSTRAINT note_replica_legs_ratio_chk CHECK (ratio > 0),
  CONSTRAINT note_replica_legs_shape_chk CHECK (
       (kind = 'underlying' AND direction = 'long' AND option_type IS NULL AND strike_pct IS NULL
        AND strike_level IS NULL AND expiry IS NULL AND contract_id IS NULL AND face_pct IS NULL AND maturity IS NULL)
    OR (kind = 'treasury' AND direction = 'long' AND option_type IS NULL AND strike_pct IS NULL
        AND strike_level IS NULL AND contract_id IS NULL AND face_pct > 0 AND maturity IS NOT NULL)
    OR (kind = 'option' AND option_type IN ('call', 'put') AND strike_pct > 0 AND expiry IS NOT NULL
        AND face_pct IS NULL AND maturity IS NULL)),
  CONSTRAINT note_replica_legs_level_chk CHECK (strike_level IS NULL OR strike_level > 0),
  CONSTRAINT note_replica_legs_contract_chk CHECK (contract_id IS NULL OR strike_level IS NOT NULL),
  CONSTRAINT note_replica_legs_no_chk CHECK (leg_no >= 1),
  CONSTRAINT note_replica_legs_purpose_chk CHECK (length(btrim(purpose)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS note_replica_legs_live_uq ON public.note_replica_legs (replica_id, leg_no)
  WHERE valid_to IS NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.note_replica_legs_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'replica legs are never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT EXISTS (SELECT 1 FROM public.note_replicas r WHERE r.id = NEW.replica_id AND r.org_id = NEW.org_id
                   AND r.system_to IS NULL AND r.status = 'draft') THEN
      RAISE EXCEPTION 'legs are written only on a live DRAFT replica of the same org' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a replica leg is written once and only ever closed' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS note_replica_legs_guard_trg ON public.note_replica_legs;
CREATE TRIGGER note_replica_legs_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.note_replica_legs
  FOR EACH ROW EXECUTE FUNCTION public.note_replica_legs_guard();

-- ── 5. Note-versus-replica comparisons (ORG; insert-only) ─────────────────
CREATE TABLE IF NOT EXISTS public.note_replica_comparisons (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  replica_id            uuid NOT NULL REFERENCES public.note_replicas(id),
  global_security_id    uuid NOT NULL REFERENCES portfolio.securities_global(id),
  status                text NOT NULL,
  note_price_pct        numeric,
  note_price_quote_id   uuid REFERENCES portfolio.adviser_quotes(id),
  replica_cost_pct      numeric,
  embedded_fee_pct      numeric,
  inputs                jsonb NOT NULL DEFAULT '[]'::jsonb,
  missing               jsonb NOT NULL DEFAULT '[]'::jsonb,
  differences           jsonb NOT NULL DEFAULT '[]'::jsonb,
  method                text NOT NULL,
  as_of                 timestamptz,
  computed_by           uuid NOT NULL REFERENCES public.users(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT note_replica_comparisons_status_chk CHECK (status IN ('priced', 'not_priced')),
  CONSTRAINT note_replica_comparisons_priced_chk CHECK (
       (status = 'priced' AND note_price_pct > 0 AND replica_cost_pct IS NOT NULL AND embedded_fee_pct IS NOT NULL
        AND as_of IS NOT NULL AND jsonb_array_length(missing) = 0)
    OR (status = 'not_priced' AND embedded_fee_pct IS NULL AND replica_cost_pct IS NULL
        AND jsonb_array_length(missing) > 0)),
  CONSTRAINT note_replica_comparisons_fee_chk CHECK (embedded_fee_pct IS NULL
                                                     OR embedded_fee_pct = note_price_pct - replica_cost_pct),
  CONSTRAINT note_replica_comparisons_json_chk CHECK (jsonb_typeof(inputs) = 'array' AND jsonb_typeof(missing) = 'array'
                                                     AND jsonb_typeof(differences) = 'array'),
  CONSTRAINT note_replica_comparisons_method_chk CHECK (length(btrim(method)) > 0)
);
CREATE INDEX IF NOT EXISTS note_replica_comparisons_replica_idx ON public.note_replica_comparisons
  (replica_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.note_replica_comparisons_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'a comparison is never deleted' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'a comparison is never updated — price again for a new one' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.note_replicas r WHERE r.id = NEW.replica_id AND r.org_id = NEW.org_id
                 AND r.system_to IS NULL) THEN
    RAISE EXCEPTION 'a comparison is for a live replica of the same org' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS note_replica_comparisons_guard_trg ON public.note_replica_comparisons;
CREATE TRIGGER note_replica_comparisons_guard_trg BEFORE INSERT OR UPDATE OR DELETE
  ON public.note_replica_comparisons FOR EACH ROW EXECUTE FUNCTION public.note_replica_comparisons_guard();

-- ── 6. RLS — a policy for every operation each table takes ────────────────
-- PLATFORM: note_replicability — read by all; written only under super admin (the verdict job).
ALTER TABLE portfolio.note_replicability ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS note_replicability_global_read ON portfolio.note_replicability;
CREATE POLICY note_replicability_global_read ON portfolio.note_replicability FOR SELECT USING (true);
DROP POLICY IF EXISTS note_replicability_super_admin_insert ON portfolio.note_replicability;
CREATE POLICY note_replicability_super_admin_insert ON portfolio.note_replicability FOR INSERT WITH CHECK
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS note_replicability_super_admin_update ON portfolio.note_replicability;
CREATE POLICY note_replicability_super_admin_update ON portfolio.note_replicability FOR UPDATE USING
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS note_replicability_super_admin_delete ON portfolio.note_replicability;
CREATE POLICY note_replicability_super_admin_delete ON portfolio.note_replicability FOR DELETE USING
  (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.note_replicability TO app_service;

-- ORG tables: the caller's own org (NULLIF — an unset org GUC matches nothing), or super admin.
DO $$
DECLARE t text; s text; pred text; full_name text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH full_name IN ARRAY ARRAY['public.note_replicas', 'public.note_replica_legs',
                                   'public.note_replica_comparisons'] LOOP
    s := split_part(full_name, '.', 1);
    t := split_part(full_name, '.', 2);
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', s, t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_select', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR SELECT USING %s', t || '_org_select', s, t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_insert', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR INSERT WITH CHECK %s', t || '_org_insert', s, t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_update', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', s, t, pred,
                   pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', t || '_org_delete', s, t);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR DELETE USING %s', t || '_org_delete', s, t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO app_service', s, t);
  END LOOP;
END $$;
