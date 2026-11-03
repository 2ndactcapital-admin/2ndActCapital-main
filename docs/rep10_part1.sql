-- REP10 — Replication Studio, sprint 10 (closes phase 2): tilts, completion
-- portfolios and overlap consolidation, charitable lot gifting, and the
-- backtest under the firm's hypothetical-performance policy.
--
-- Applied by apps/api/scripts/_apply_rep10.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- BI-TEMPORAL (Rule 3)
--   tilt_sets, charitable_donees, gift_proposals — SYSTEM axis: tilt lines,
--     gift lots, approval tasks and proposals point at their ids; the outgoing
--     state is copied to a fresh id (archived_from_id = live id, system_to =
--     now()) and the live row keeps its id (rep2 / rep9 precedent).
--   tilt_lines, gift_lots, completion_settings, security_factor_scores — VALID
--     axis: a change closes the live row (valid_to) and inserts the new one;
--     nothing references their ids.
--   hypothetical_performance_policies — rep7b's public.authority_change_guard,
--     reused unchanged (decision on the same id; supersession closes valid_to).
--   backtests — insert-only, immutable (a re-run is a new row).
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- THE CUSTODY CLIFF. Nothing here moves a security or money. A gift is a
-- record of what a PERSON is asked to do (a transfer letter the member signs
-- and the custodian acts on) and what a person reports (sent / received /
-- acknowledged). A backtest is HYPOTHETICAL by construction (hypothetical =
-- true is a CHECK, not a default).

-- ── 0. Permission: the hypothetical-performance policy (org_admin by default) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('manage_hypothetical_policy', 'hypothetical_policy', 'manage')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name = 'manage_hypothetical_policy'
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. security_data: the 'factor_score' data type (CSV upload + vendor slot only) ──
ALTER TABLE portfolio.data_providers DROP CONSTRAINT IF EXISTS data_providers_capabilities_chk;
ALTER TABLE portfolio.data_providers ADD CONSTRAINT data_providers_capabilities_chk CHECK (capabilities <@ ARRAY[
  'eod_price', 'corporate_action', 'fund_holdings', 'index_level', 'option_quote', 'fx', 'factor_score']::text[]);
ALTER TABLE portfolio.data_source_rank DROP CONSTRAINT IF EXISTS data_source_rank_type_chk;
ALTER TABLE portfolio.data_source_rank ADD CONSTRAINT data_source_rank_type_chk CHECK (data_type IN (
  'eod_price', 'corporate_action', 'fund_holdings', 'index_level', 'option_quote', 'fx', 'factor_score'));
ALTER TABLE portfolio.data_uploads DROP CONSTRAINT IF EXISTS data_uploads_type_chk;
ALTER TABLE portfolio.data_uploads ADD CONSTRAINT data_uploads_type_chk CHECK (data_type IN (
  'eod_price', 'corporate_action', 'index_level', 'fund_holdings', 'factor_score'));
ALTER TABLE portfolio.data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_type_chk;
ALTER TABLE portfolio.data_quality_events ADD CONSTRAINT data_quality_events_type_chk CHECK (data_type IN (
  'eod_price', 'corporate_action', 'fund_holdings', 'index_level', 'option_quote', 'fx', 'factor_score'));

-- The canonical store for factor scores: one score per (security, factor, date, provider, org). Mixed scope like
-- portfolio.securities_global_prices: org_id NULL = a platform row (none are written — no provider), org_id set =
-- that org's own approved upload. A score is a number the FILE states; nothing here computes one.
CREATE TABLE IF NOT EXISTS portfolio.security_factor_scores (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  factor              text NOT NULL,
  score_date          date NOT NULL,
  score               numeric(20, 10) NOT NULL,
  provider_key        text NOT NULL,
  org_id              uuid REFERENCES public.organizations(id),
  upload_id           uuid,               -- no FK: data_uploads ids repeat across system-axis copies (as prices)
  licence             jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_flags       text[] NOT NULL DEFAULT '{}'::text[],
  as_of               timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT security_factor_scores_factor_chk CHECK (factor ~ '^[a-z][a-z0-9_]{0,39}$'),
  CONSTRAINT security_factor_scores_upload_chk CHECK (upload_id IS NULL OR (org_id IS NOT NULL
                                                      AND provider_key = 'csv_upload'))
);
CREATE UNIQUE INDEX IF NOT EXISTS security_factor_scores_live_uq ON portfolio.security_factor_scores (
  global_security_id, factor, score_date, provider_key, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS security_factor_scores_read_idx ON portfolio.security_factor_scores
  (global_security_id, factor, score_date DESC) WHERE valid_to IS NULL AND system_to IS NULL;

ALTER TABLE portfolio.security_factor_scores ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS security_factor_scores_read ON portfolio.security_factor_scores;
CREATE POLICY security_factor_scores_read ON portfolio.security_factor_scores FOR SELECT USING (
  org_id IS NULL OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS security_factor_scores_insert ON portfolio.security_factor_scores;
CREATE POLICY security_factor_scores_insert ON portfolio.security_factor_scores FOR INSERT WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
DROP POLICY IF EXISTS security_factor_scores_update ON portfolio.security_factor_scores;
CREATE POLICY security_factor_scores_update ON portfolio.security_factor_scores FOR UPDATE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid))
  WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
DROP POLICY IF EXISTS security_factor_scores_delete ON portfolio.security_factor_scores;
CREATE POLICY security_factor_scores_delete ON portfolio.security_factor_scores FOR DELETE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (org_id IS NOT NULL AND org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid));
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.security_factor_scores TO app_service;

-- csv_upload and the vendor slot gain the capability (valid-axis restatement of the provider rows), and the
-- platform rank names them: csv_upload 1, vendor_slot 2 (not connected — skipped until a contract exists).
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM portfolio.data_providers WHERE key IN ('csv_upload', 'vendor_slot') AND valid_to IS NULL
           AND system_to IS NULL AND NOT ('factor_score' = ANY (capabilities)) LOOP
    UPDATE portfolio.data_providers SET valid_to = now() WHERE id = r.id;
    INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                          lic_redistribution, retention_after_cancel, attribution, terms_note,
                                          confirmed_on, daily_call_budget, requires_entitlement)
    VALUES (r.key,
            CASE WHEN r.key = 'csv_upload' THEN 'File upload (prices, corporate actions, index levels, factor scores)'
                 ELSE r.name END,
            r.kind, r.status, r.capabilities || ARRAY['factor_score'], r.lic_internal_use, r.lic_member_display,
            r.lic_redistribution, r.retention_after_cancel, r.attribution, r.terms_note, r.confirmed_on,
            r.daily_call_budget, r.requires_entitlement);
  END LOOP;
END $$;

INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank)
SELECT v.* FROM (VALUES ('factor_score', 'csv_upload', 1), ('factor_score', 'vendor_slot', 2))
  AS v(data_type, provider_key, rank)
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank r WHERE r.owner_scope = 'platform'
                  AND r.data_type = v.data_type AND r.provider_key = v.provider_key AND r.valid_to IS NULL
                  AND r.system_to IS NULL);

-- ── 2. Tilt sets (ACCOUNT, versioned, maker-checker; system axis) ─────────
-- "The index plus my views": signed over- / underweights on securities and sectors, or a factor tilt, applied to
-- the account's target AFTER its exclusions and BEFORE trades (rebalance_proposals.target_for). One approved set
-- in effect per account; one open (draft / awaiting approval) at a time.
CREATE TABLE IF NOT EXISTS public.tilt_sets (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  account_id        uuid NOT NULL REFERENCES public.accounts(id),
  model_id          uuid NOT NULL REFERENCES portfolio.models(id),
  version_no        integer NOT NULL,
  name              text NOT NULL,
  reason            text NOT NULL,
  status            text NOT NULL DEFAULT 'draft',
  maker_id          uuid NOT NULL REFERENCES public.users(id),
  submitted_by      uuid REFERENCES public.users(id),
  submitted_at      timestamptz,
  approval_task_id  uuid REFERENCES public.workflow_run_steps(id),
  approved_by       uuid REFERENCES public.users(id),
  decided_at        timestamptz,
  decision_note     text,
  archived_reason   text,
  metrics           jsonb,
  archived_from_id  uuid REFERENCES public.tilt_sets(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT tilt_sets_name_chk CHECK (length(btrim(name)) > 0),
  CONSTRAINT tilt_sets_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT tilt_sets_version_chk CHECK (version_no >= 1),
  CONSTRAINT tilt_sets_status_chk CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected', 'archived')),
  CONSTRAINT tilt_sets_maker_checker_chk CHECK (approved_by IS NULL OR (approved_by <> maker_id
                                                AND approved_by IS DISTINCT FROM submitted_by)),
  CONSTRAINT tilt_sets_pending_chk CHECK (status <> 'pending_approval' OR (approval_task_id IS NOT NULL
                                          AND submitted_by IS NOT NULL AND submitted_at IS NOT NULL)),
  CONSTRAINT tilt_sets_decided_chk CHECK (status NOT IN ('approved', 'rejected')
                                          OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT tilt_sets_rejected_chk CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT tilt_sets_archived_chk CHECK (status <> 'archived' OR length(btrim(COALESCE(archived_reason, ''))) > 0),
  CONSTRAINT tilt_sets_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS tilt_sets_live_uq ON public.tilt_sets (account_id)
  WHERE status = 'approved' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tilt_sets_open_uq ON public.tilt_sets (account_id)
  WHERE status IN ('draft', 'pending_approval') AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tilt_sets_version_uq ON public.tilt_sets (account_id, version_no)
  WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tilt_sets_task_uq ON public.tilt_sets (approval_task_id)
  WHERE approval_task_id IS NOT NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS tilt_sets_org_idx ON public.tilt_sets (org_id, status) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.tilt_sets_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'submitted_by', 'submitted_at', 'approval_task_id', 'approved_by',
    'decided_at', 'decision_note', 'archived_reason', 'metrics', 'updated_at', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'tilt sets are never deleted (archive one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived tilt-set state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status IN ('rejected', 'archived') THEN
    RAISE EXCEPTION 'a % tilt set is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live tilt-set row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'a tilt set''s account, model, name and reason are fixed — make a new version'
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>archived', 'pending_approval>approved', 'pending_approval>rejected',
      'pending_approval>draft', 'approved>archived'])) THEN
    RAISE EXCEPTION 'illegal tilt set transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tilt_sets_guard_trg ON public.tilt_sets;
CREATE TRIGGER tilt_sets_guard_trg BEFORE UPDATE OR DELETE ON public.tilt_sets
  FOR EACH ROW EXECUTE FUNCTION public.tilt_sets_guard();

-- Lines: VALID axis while the set is a draft (an edit closes the live line and inserts the new one); frozen
-- once the set is submitted. delta is a signed FRACTION of the account (0.01 = one percentage point).
CREATE TABLE IF NOT EXISTS public.tilt_lines (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES public.organizations(id),
  tilt_set_id   uuid NOT NULL REFERENCES public.tilt_sets(id),
  line_no       integer NOT NULL,
  kind          text NOT NULL,
  subject       text NOT NULL,
  subject_label text,
  delta         numeric(14, 10) NOT NULL,
  reason        text NOT NULL,
  created_by    uuid NOT NULL REFERENCES public.users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  valid_from    timestamptz NOT NULL DEFAULT now(),
  valid_to      timestamptz,
  system_from   timestamptz NOT NULL DEFAULT now(),
  system_to     timestamptz,
  CONSTRAINT tilt_lines_kind_chk CHECK (kind IN ('security', 'sector', 'factor')),
  CONSTRAINT tilt_lines_subject_chk CHECK (length(btrim(subject)) > 0),
  CONSTRAINT tilt_lines_delta_chk CHECK (delta <> 0 AND delta > -1 AND delta < 1),
  CONSTRAINT tilt_lines_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT tilt_lines_line_chk CHECK (line_no >= 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS tilt_lines_live_uq ON public.tilt_lines (tilt_set_id, kind, subject)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS tilt_lines_set_idx ON public.tilt_lines (tilt_set_id) WHERE valid_to IS NULL;

CREATE OR REPLACE FUNCTION public.tilt_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'tilt lines are never deleted (close one instead)' USING ERRCODE = '42501';
  END IF;
  SELECT status INTO st FROM public.tilt_sets WHERE id = NEW.tilt_set_id AND system_to IS NULL;
  IF st IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'tilt lines change only while their set is a draft (it is %)', COALESCE(st, 'missing')
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    RETURN NEW;
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a tilt line is closed, never edited — close it and add the new one' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS tilt_lines_guard_trg ON public.tilt_lines;
CREATE TRIGGER tilt_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON public.tilt_lines
  FOR EACH ROW EXECUTE FUNCTION public.tilt_lines_guard();

-- ── 3. Completion settings (ACCOUNT, valid axis) ───────────────────────────
-- With count_elsewhere on, a MANAGED account's replica target subtracts the household's exposure held elsewhere
-- (the X-ray's per-account look-through) so the HOUSEHOLD total matches the model; never negative.
CREATE TABLE IF NOT EXISTS public.completion_settings (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  account_id          uuid NOT NULL REFERENCES public.accounts(id),
  count_elsewhere     boolean NOT NULL DEFAULT false,
  count_authorities   text[] NOT NULL DEFAULT ARRAY['read_only', 'advise']::text[],   -- managed is opt-in (two
                                                                               -- managed completions would net each other)
  stale_after_days    integer,
  reason              text NOT NULL,
  set_by              uuid NOT NULL REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT completion_settings_authorities_chk CHECK (
    cardinality(count_authorities) >= 1
    AND count_authorities <@ ARRAY['read_only', 'advise', 'managed']::text[]),
  CONSTRAINT completion_settings_stale_chk CHECK (stale_after_days IS NULL OR stale_after_days BETWEEN 1 AND 366),
  CONSTRAINT completion_settings_reason_chk CHECK (length(btrim(reason)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS completion_settings_live_uq ON public.completion_settings (account_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
ALTER TABLE public.completion_settings ALTER COLUMN count_authorities SET DEFAULT ARRAY['read_only', 'advise']::text[];

CREATE OR REPLACE FUNCTION public.rep10_valid_axis_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted (close one instead)', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a % row is closed, never edited — close it and insert the new one', TG_TABLE_NAME
    USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS completion_settings_guard_trg ON public.completion_settings;
CREATE TRIGGER completion_settings_guard_trg BEFORE UPDATE OR DELETE ON public.completion_settings
  FOR EACH ROW EXECUTE FUNCTION public.rep10_valid_axis_guard();

-- ── 4. Charitable donees (ORG, system axis) ────────────────────────────────
-- The receiving side of an in-kind gift. The donee's account reference is stored MASKED (+ a salted hash for a
-- later match, rep2's AccountNumber) — never the full number.
CREATE TABLE IF NOT EXISTS public.charitable_donees (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 uuid NOT NULL REFERENCES public.organizations(id),
  name                   text NOT NULL,
  kind                   text NOT NULL,
  ein                    text NOT NULL,
  receiving_broker       text NOT NULL,
  dtc_participant        text NOT NULL,
  account_number_masked  text NOT NULL,
  account_number_hash    text NOT NULL,
  contact                text,
  notes                  text,
  status                 text NOT NULL DEFAULT 'active',
  created_by             uuid NOT NULL REFERENCES public.users(id),
  archived_from_id       uuid REFERENCES public.charitable_donees(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  valid_from             timestamptz NOT NULL DEFAULT now(),
  valid_to               timestamptz,
  system_from            timestamptz NOT NULL DEFAULT now(),
  system_to              timestamptz,
  CONSTRAINT charitable_donees_name_chk CHECK (length(btrim(name)) > 0),
  CONSTRAINT charitable_donees_kind_chk CHECK (kind IN ('public_charity', 'daf', 'private_foundation')),
  CONSTRAINT charitable_donees_ein_chk CHECK (ein ~ '^[0-9]{2}-[0-9]{7}$'),
  CONSTRAINT charitable_donees_broker_chk CHECK (length(btrim(receiving_broker)) > 0),
  CONSTRAINT charitable_donees_dtc_chk CHECK (dtc_participant ~ '^[0-9]{4}$'),
  CONSTRAINT charitable_donees_masked_chk CHECK (account_number_masked ~ '^\*{2,}[0-9A-Za-z]{0,4}$'),
  CONSTRAINT charitable_donees_hash_chk CHECK (length(account_number_hash) >= 32),
  CONSTRAINT charitable_donees_status_chk CHECK (status IN ('active', 'retired')),
  CONSTRAINT charitable_donees_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS charitable_donees_org_idx ON public.charitable_donees (org_id) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.charitable_donees_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'donees are never deleted (retire one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived donee state is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id
     OR NEW.org_id <> OLD.org_id OR NEW.created_by <> OLD.created_by THEN
    RAISE EXCEPTION 'the live donee row keeps its id and org; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'retired' THEN
    RAISE EXCEPTION 'a retired donee is immutable' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS charitable_donees_guard_trg ON public.charitable_donees;
CREATE TRIGGER charitable_donees_guard_trg BEFORE UPDATE OR DELETE ON public.charitable_donees
  FOR EACH ROW EXECUTE FUNCTION public.charitable_donees_guard();

-- ── 5. Gift proposals (ORG, system axis, maker-checker) ────────────────────
-- The inputs the comparison uses (AGI estimate, itemizes, prior carryforward) are entered ON the proposal, each
-- with its source and as-of — never read from a profile that does not hold them. Delivery is MANUAL, always.
CREATE TABLE IF NOT EXISTS public.gift_proposals (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES public.organizations(id),
  household_id             uuid REFERENCES public.households(id),
  subject_type             text NOT NULL,
  subject_id               uuid NOT NULL,
  member_entity_id         uuid NOT NULL REFERENCES public.entities(id),
  donee_id                 uuid NOT NULL REFERENCES public.charitable_donees(id),
  donee_kind               text NOT NULL,
  tax_year                 integer NOT NULL,
  target_amount            numeric(20, 2),
  agi_estimate             numeric(20, 2),
  agi_source               text,
  agi_as_of                date,
  itemizes                 text NOT NULL,
  itemizes_source          text NOT NULL,
  itemizes_as_of           date NOT NULL,
  prior_carryforward       numeric(20, 2) NOT NULL DEFAULT 0,
  prior_carryforward_source text NOT NULL,
  prior_carryforward_as_of date NOT NULL,
  rebuy                    text NOT NULL DEFAULT 'none',
  comparison               jsonb NOT NULL,
  rates                    jsonb NOT NULL,
  status                   text NOT NULL DEFAULT 'draft',
  maker_id                 uuid NOT NULL REFERENCES public.users(id),
  submitted_by             uuid REFERENCES public.users(id),
  submitted_at             timestamptz,
  approval_task_id         uuid REFERENCES public.workflow_run_steps(id),
  approved_by              uuid REFERENCES public.users(id),
  approved_at              timestamptz,
  decision_note            text,
  sent_by                  uuid REFERENCES public.users(id),
  sent_at                  timestamptz,
  sent_channel             text,
  received_on              date,
  received_by              uuid REFERENCES public.users(id),
  received_note            text,
  acknowledged_on          date,
  acknowledged_by          uuid REFERENCES public.users(id),
  acknowledgement_ref      text,
  rebuy_proposal_ids       uuid[] NOT NULL DEFAULT '{}'::uuid[],
  cancelled_by             uuid REFERENCES public.users(id),
  cancelled_at             timestamptz,
  cancel_reason            text,
  notes                    text,
  archived_from_id         uuid REFERENCES public.gift_proposals(id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  valid_from               timestamptz NOT NULL DEFAULT now(),
  valid_to                 timestamptz,
  system_from              timestamptz NOT NULL DEFAULT now(),
  system_to                timestamptz,
  CONSTRAINT gift_proposals_subject_chk CHECK (subject_type IN ('household', 'entity')),
  CONSTRAINT gift_proposals_donee_kind_chk CHECK (donee_kind IN ('public_charity', 'daf', 'private_foundation')),
  CONSTRAINT gift_proposals_year_chk CHECK (tax_year BETWEEN 2000 AND 2200),
  CONSTRAINT gift_proposals_target_chk CHECK (target_amount IS NULL OR target_amount > 0),
  CONSTRAINT gift_proposals_agi_chk CHECK ((agi_estimate IS NULL AND agi_source IS NULL AND agi_as_of IS NULL)
    OR (agi_estimate > 0 AND length(btrim(COALESCE(agi_source, ''))) > 0 AND agi_as_of IS NOT NULL)),
  CONSTRAINT gift_proposals_itemizes_chk CHECK (itemizes IN ('yes', 'no')),
  CONSTRAINT gift_proposals_inputs_chk CHECK (length(btrim(itemizes_source)) > 0
                                              AND length(btrim(prior_carryforward_source)) > 0),
  CONSTRAINT gift_proposals_carryforward_chk CHECK (prior_carryforward >= 0),
  CONSTRAINT gift_proposals_rebuy_chk CHECK (rebuy IN ('none', 'same_security', 'replica_consistent')),
  CONSTRAINT gift_proposals_status_chk CHECK (status IN ('draft', 'pending_approval', 'approved', 'sent', 'received',
                                                         'acknowledged', 'cancelled')),
  CONSTRAINT gift_proposals_maker_checker_chk CHECK (approved_by IS NULL OR (approved_by <> maker_id
                                                     AND approved_by IS DISTINCT FROM submitted_by)),
  CONSTRAINT gift_proposals_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT gift_proposals_approved_chk CHECK (status IN ('draft', 'pending_approval', 'cancelled')
                                                OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT gift_proposals_sent_chk CHECK (status NOT IN ('sent', 'received', 'acknowledged')
    OR (sent_by IS NOT NULL AND sent_at IS NOT NULL AND length(btrim(COALESCE(sent_channel, ''))) > 0)),
  -- the gift date is the date the donee's account RECEIVED the shares — entered by a person
  CONSTRAINT gift_proposals_received_chk CHECK (status NOT IN ('received', 'acknowledged')
                                                OR (received_on IS NOT NULL AND received_by IS NOT NULL)),
  CONSTRAINT gift_proposals_ack_chk CHECK (status <> 'acknowledged'
                                           OR (acknowledged_on IS NOT NULL AND acknowledged_by IS NOT NULL)),
  CONSTRAINT gift_proposals_cancelled_chk CHECK (status <> 'cancelled'
    OR (cancelled_at IS NOT NULL AND length(btrim(COALESCE(cancel_reason, ''))) > 0)),
  CONSTRAINT gift_proposals_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS gift_proposals_org_idx ON public.gift_proposals (org_id, status) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS gift_proposals_task_uq ON public.gift_proposals (approval_task_id)
  WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

CREATE OR REPLACE FUNCTION public.gift_proposals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  lifecycle text[] := ARRAY['status', 'submitted_by', 'submitted_at', 'approval_task_id', 'approved_by',
    'approved_at', 'decision_note', 'sent_by', 'sent_at', 'sent_channel', 'received_on', 'received_by',
    'received_note', 'acknowledged_on', 'acknowledged_by', 'acknowledgement_ref', 'rebuy_proposal_ids',
    'cancelled_by', 'cancelled_at', 'cancel_reason', 'updated_at', 'system_from'];
  edge text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'gift proposals are never deleted (cancel one instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived gift-proposal state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'cancelled' THEN
    RAISE EXCEPTION 'a cancelled gift proposal is immutable' USING ERRCODE = '42501';
  END IF;
  IF NEW.system_to IS NOT NULL OR NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live gift-proposal row keeps its id; archive by copying it' USING ERRCODE = '42501';
  END IF;
  IF (to_jsonb(NEW) - lifecycle) <> (to_jsonb(OLD) - lifecycle) THEN
    RAISE EXCEPTION 'a gift proposal''s terms are fixed when it is made — cancel it and make a new one'
      USING ERRCODE = '42501';
  END IF;
  edge := OLD.status || '>' || NEW.status;
  IF OLD.status <> NEW.status AND NOT (edge = ANY (ARRAY[
      'draft>pending_approval', 'draft>cancelled', 'pending_approval>approved', 'pending_approval>draft',
      'pending_approval>cancelled', 'approved>sent', 'approved>cancelled', 'sent>received', 'sent>cancelled',
      'received>acknowledged'])) THEN
    RAISE EXCEPTION 'illegal gift proposal transition % -> %', OLD.status, NEW.status USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS gift_proposals_guard_trg ON public.gift_proposals;
CREATE TRIGGER gift_proposals_guard_trg BEFORE UPDATE OR DELETE ON public.gift_proposals
  FOR EACH ROW EXECUTE FUNCTION public.gift_proposals_guard();

-- Lots given: VALID axis (the FMV at receipt is a restatement: close + insert).
CREATE TABLE IF NOT EXISTS public.gift_lots (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  gift_proposal_id     uuid NOT NULL REFERENCES public.gift_proposals(id),
  account_id           uuid NOT NULL REFERENCES public.accounts(id),
  tax_lot_id           uuid NOT NULL REFERENCES portfolio.tax_lots(id),
  global_security_id   uuid NOT NULL REFERENCES portfolio.securities_global(id),
  quantity             numeric(28, 8) NOT NULL,
  unit_cost            numeric(28, 8) NOT NULL,
  acquired_date        date NOT NULL,
  holding_period       text NOT NULL,
  price_at_proposal    numeric(28, 8) NOT NULL,
  price_as_of          date NOT NULL,
  price_source         text NOT NULL,
  fmv_at_proposal      numeric(20, 2) NOT NULL,
  gain_at_proposal     numeric(20, 2) NOT NULL,
  fmv_at_receipt       numeric(20, 2),
  receipt_price        numeric(28, 8),
  receipt_price_source text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT gift_lots_quantity_chk CHECK (quantity > 0),
  CONSTRAINT gift_lots_period_chk CHECK (holding_period = 'long'),
  CONSTRAINT gift_lots_price_chk CHECK (price_at_proposal > 0),
  CONSTRAINT gift_lots_gain_chk CHECK (gain_at_proposal > 0),
  CONSTRAINT gift_lots_receipt_chk CHECK ((fmv_at_receipt IS NULL AND receipt_price IS NULL
                                           AND receipt_price_source IS NULL)
    OR (fmv_at_receipt > 0 AND receipt_price > 0 AND length(btrim(COALESCE(receipt_price_source, ''))) > 0))
);
CREATE UNIQUE INDEX IF NOT EXISTS gift_lots_live_uq ON public.gift_lots (gift_proposal_id, tax_lot_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
DROP TRIGGER IF EXISTS gift_lots_guard_trg ON public.gift_lots;
CREATE TRIGGER gift_lots_guard_trg BEFORE UPDATE OR DELETE ON public.gift_lots
  FOR EACH ROW EXECUTE FUNCTION public.rep10_valid_axis_guard();

-- ── 6. Hypothetical-performance policy (ORG, versioned, second person) ─────
-- The firm's own words (SEC Marketing Rule — counsel's item), who may see a backtest, and the disclosure every
-- backtest screen, PDF and envelope carries. With no approved policy: staff only.
CREATE TABLE IF NOT EXISTS public.hypothetical_performance_policies (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organizations(id),
  version_no         integer NOT NULL,
  policy_text        text NOT NULL,
  audience           text NOT NULL,
  disclosure_text    text NOT NULL,
  reason             text NOT NULL,
  status             text NOT NULL DEFAULT 'draft',
  maker_id           uuid NOT NULL REFERENCES public.users(id),
  approved_by        uuid REFERENCES public.users(id),
  approval_task_id   uuid REFERENCES public.workflow_run_steps(id),
  decided_at         timestamptz,
  decision_note      text,
  superseded_reason  text,
  archived_from_id   uuid REFERENCES public.hypothetical_performance_policies(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  valid_from         timestamptz NOT NULL DEFAULT now(),
  valid_to           timestamptz,
  system_from        timestamptz NOT NULL DEFAULT now(),
  system_to          timestamptz,
  CONSTRAINT hypothetical_policies_text_chk CHECK (length(btrim(policy_text)) > 0),
  CONSTRAINT hypothetical_policies_audience_chk CHECK (audience IN ('staff_only', 'staff_and_members')),
  CONSTRAINT hypothetical_policies_disclosure_chk CHECK (length(btrim(disclosure_text)) > 0),
  CONSTRAINT hypothetical_policies_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT hypothetical_policies_version_chk CHECK (version_no >= 1),
  CONSTRAINT hypothetical_policies_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT hypothetical_policies_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT hypothetical_policies_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                      OR system_to IS NOT NULL),
  CONSTRAINT hypothetical_policies_decided_chk CHECK (status = 'draft' OR (approved_by IS NOT NULL
                                                                           AND decided_at IS NOT NULL)),
  CONSTRAINT hypothetical_policies_rejected_chk CHECK (status <> 'rejected'
                                                       OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT hypothetical_policies_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT hypothetical_policies_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS hypothetical_policies_live_uq ON public.hypothetical_performance_policies (org_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS hypothetical_policies_pending_uq ON public.hypothetical_performance_policies (org_id)
  WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS hypothetical_policies_version_uq
  ON public.hypothetical_performance_policies (org_id, version_no) WHERE system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS hypothetical_policies_task_uq ON public.hypothetical_performance_policies
  (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;
DROP TRIGGER IF EXISTS hypothetical_policies_guard_trg ON public.hypothetical_performance_policies;
CREATE TRIGGER hypothetical_policies_guard_trg BEFORE UPDATE OR DELETE ON public.hypothetical_performance_policies
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();

-- ── 7. Backtests (ORG, insert-only, HYPOTHETICAL by CHECK) ─────────────────
CREATE TABLE IF NOT EXISTS public.backtests (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  model_id            uuid NOT NULL REFERENCES portfolio.models(id),
  fund_security_id    uuid NOT NULL REFERENCES portfolio.securities_global(id),
  household_id        uuid REFERENCES public.households(id),
  window_preset       text NOT NULL,
  start_date          date NOT NULL,
  end_date            date NOT NULL,
  settings            jsonb NOT NULL,
  coverage            jsonb NOT NULL,
  status              text NOT NULL,
  result              jsonb,
  not_estimated_reason text,
  hypothetical        boolean NOT NULL DEFAULT true,
  policy_id           uuid REFERENCES public.hypothetical_performance_policies(id),
  created_by          uuid NOT NULL REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT backtests_window_chk CHECK (window_preset IN ('2008', '2020', '2022', 'custom')),
  CONSTRAINT backtests_dates_chk CHECK (end_date > start_date),
  CONSTRAINT backtests_status_chk CHECK (status IN ('computed', 'not_estimated')),
  -- every backtest is hypothetical performance — a CHECK, not a default
  CONSTRAINT backtests_hypothetical_chk CHECK (hypothetical = true),
  -- coverage first: a not_estimated run carries no figures, only its reason and the coverage found
  CONSTRAINT backtests_result_chk CHECK ((status = 'computed' AND result IS NOT NULL AND not_estimated_reason IS NULL)
    OR (status = 'not_estimated' AND result IS NULL AND length(btrim(COALESCE(not_estimated_reason, ''))) > 0))
);
CREATE INDEX IF NOT EXISTS backtests_org_idx ON public.backtests (org_id, created_at DESC) WHERE system_to IS NULL;

CREATE OR REPLACE FUNCTION public.backtests_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND public.execution_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'a backtest is a fixed record — run a new one' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS backtests_immutable_trg ON public.backtests;
CREATE TRIGGER backtests_immutable_trg BEFORE UPDATE OR DELETE ON public.backtests
  FOR EACH ROW EXECUTE FUNCTION public.backtests_immutable();

-- ── 8. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['tilt_sets', 'tilt_lines', 'completion_settings', 'charitable_donees', 'gift_proposals',
                           'gift_lots', 'hypothetical_performance_policies', 'backtests'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t,
                   pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;
