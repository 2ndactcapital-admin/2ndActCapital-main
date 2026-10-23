-- REP4 — Replication Studio, sprint 4: build a model from a fund. "Holdings of
-- <TICKER>" models built from rep1's approved terms and live holdings into
-- rep3's registry; the construction recipe of every such version; the
-- registry "find or build" key; immutable five-question viability reports; and
-- the rep3 vocabulary additions the build and the monitoring need.
--
-- Applied by apps/api/scripts/_apply_rep4.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE.
--
-- SCHEMA. Every table lives in `portfolio` (not on any role's search_path —
-- every reference in code is schema-qualified). All three new tables are
-- ORG-scoped: NULLIF org isolation (or the super-admin carve-out) on SELECT,
-- INSERT, UPDATE and DELETE.
--
-- BI-TEMPORAL (Rule 3)
--   replica_model_specs        — immutable (one per model version; a new
--                                 version gets a new spec). Never updated.
--   replica_registry_keys      — VALID axis: nothing references its id; a key
--                                 is retired by closing valid_to (the trigger
--                                 refuses any other change).
--   replica_viability_reports  — immutable: a report is what was computed, then.
--   A DELETE passes the guards only on the fixture-teardown path
--   (portfolio.model_teardown_allowed(): models.teardown = 'fixture', and never
--   as app_service).
--
-- THE CUSTODY CLIFF. Nothing here trades. A built model is a DRAFT version that
-- rep3's maker-checker human task approves; approval emits
-- 'model.version_approved' (rep5 turns it into PROPOSALS).

-- ── 0. rep3 vocabulary additions (DROP + re-ADD of named CHECKs) ───────────
-- model_versions.origin: 'fund_build' (a person built it from the fund),
-- 'fund_monitor' (monitoring drafted it from a new holdings snapshot).
ALTER TABLE portfolio.model_versions DROP CONSTRAINT IF EXISTS model_versions_origin_chk;
ALTER TABLE portfolio.model_versions ADD CONSTRAINT model_versions_origin_chk CHECK (origin IN (
  'upload', 'manual', 'library_copy', 'subscription', 'composite_follow', 'revision', 'fund_build', 'fund_monitor'));

-- model_events.action: the fund-build and monitoring log.
ALTER TABLE portfolio.model_events DROP CONSTRAINT IF EXISTS model_events_action_chk;
ALTER TABLE portfolio.model_events ADD CONSTRAINT model_events_action_chk CHECK (action IN (
  'create_model', 'edit_model', 'create_draft', 'edit_positions', 'edit_sleeves', 'submit', 'approve', 'reject',
  'abandon', 'save_mapping', 'publish', 'unpublish', 'retire_entry', 'agreement_confirmed', 'copy', 'subscribe',
  'subscription_draft', 'composite_follow_draft', 'follow_skipped',
  'fund_build_joined', 'holdings_monitor', 'terms_monitor', 'terms_review_done'));
-- every action is a person's, except the logged system imports and monitoring
ALTER TABLE portfolio.model_events DROP CONSTRAINT IF EXISTS model_events_human_chk;
ALTER TABLE portfolio.model_events ADD CONSTRAINT model_events_human_chk CHECK (
  actor_id IS NOT NULL OR action IN ('subscription_draft', 'composite_follow_draft', 'follow_skipped',
                                     'holdings_monitor', 'terms_monitor'));

-- Monitoring is idempotent IN THE DATABASE: one holdings_monitor event per
-- (model, snapshot) and one terms_monitor event per (model, terms version).
CREATE UNIQUE INDEX IF NOT EXISTS model_events_holdings_monitor_uq
  ON portfolio.model_events (model_id, ((detail->>'snapshot_id'))) WHERE action = 'holdings_monitor';
CREATE UNIQUE INDEX IF NOT EXISTS model_events_terms_monitor_uq
  ON portfolio.model_events (model_id, ((detail->>'terms_version_id'))) WHERE action = 'terms_monitor';

-- The rep1 disclosure line, stored on a fund-built model.
ALTER TABLE portfolio.models ADD COLUMN IF NOT EXISTS disclosure text;
ALTER TABLE portfolio.models DROP CONSTRAINT IF EXISTS models_disclosure_chk;
ALTER TABLE portfolio.models ADD CONSTRAINT models_disclosure_chk CHECK (disclosure IS NULL OR source = 'fund_replica');

-- ── 1. The construction recipe of a fund-built model version ──────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_model_specs (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                     uuid NOT NULL REFERENCES public.organizations(id),
  model_id                   uuid NOT NULL REFERENCES portfolio.models(id),
  version_id                 uuid NOT NULL REFERENCES portfolio.model_versions(id),
  replica_security_id        uuid NOT NULL REFERENCES portfolio.replica_securities(id),
  terms_version_id           uuid NOT NULL REFERENCES portfolio.replica_terms_versions(id),
  snapshot_id                uuid NOT NULL REFERENCES portfolio.replica_holdings_snapshots(id),
  custodian_code             text NOT NULL,
  method                     text NOT NULL,
  top_n                      integer,
  min_weight                 numeric(14, 10) NOT NULL DEFAULT 0,
  cash_target                numeric(14, 10) NOT NULL DEFAULT 0,
  unresolved_policy          text NOT NULL,
  rebalance_frequency        text,
  rebalance_origin           text NOT NULL,
  drift_threshold            numeric(14, 10) NOT NULL,
  turnover_review_threshold  numeric(14, 10) NOT NULL,
  -- the deliberate differences from the fund, in plain words ({kind, text, detail})
  departures                 jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- snapshot rows not in the version, each with its reason
  dropped                    jsonb NOT NULL DEFAULT '[]'::jsonb,
  origin                     text NOT NULL,
  created_by                 uuid REFERENCES public.users(id),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  valid_from                 timestamptz NOT NULL DEFAULT now(),
  valid_to                   timestamptz,
  system_from                timestamptz NOT NULL DEFAULT now(),
  system_to                  timestamptz,
  CONSTRAINT replica_model_specs_method_chk CHECK (
    (method = 'full' AND top_n IS NULL) OR (method = 'top_n' AND top_n IS NOT NULL AND top_n >= 1)),
  CONSTRAINT replica_model_specs_custodian_chk CHECK (custodian_code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT replica_model_specs_min_weight_chk CHECK (min_weight >= 0 AND min_weight < 1),
  CONSTRAINT replica_model_specs_cash_chk CHECK (cash_target >= 0 AND cash_target < 1),
  CONSTRAINT replica_model_specs_policy_chk CHECK (unresolved_policy IN ('block', 'exclude_and_reweight')),
  CONSTRAINT replica_model_specs_rebalance_chk CHECK (rebalance_origin IN ('terms', 'deliberate', 'not_stated')),
  CONSTRAINT replica_model_specs_thresholds_chk CHECK (drift_threshold > 0 AND drift_threshold <= 1
                                                       AND turnover_review_threshold > 0
                                                       AND turnover_review_threshold <= 1),
  CONSTRAINT replica_model_specs_json_chk CHECK (jsonb_typeof(departures) = 'array' AND jsonb_typeof(dropped) = 'array'),
  CONSTRAINT replica_model_specs_origin_chk CHECK (origin IN ('fund_build', 'fund_monitor')),
  -- a person builds; monitoring (the system) drafts
  CONSTRAINT replica_model_specs_human_chk CHECK (origin <> 'fund_build' OR created_by IS NOT NULL)
);
-- one spec per model version
CREATE UNIQUE INDEX IF NOT EXISTS replica_model_specs_version_uq
  ON portfolio.replica_model_specs (version_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS replica_model_specs_model_idx
  ON portfolio.replica_model_specs (model_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS replica_model_specs_entry_idx
  ON portfolio.replica_model_specs (replica_security_id) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 2. The registry "find or build" key ────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_registry_keys (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  replica_security_id   uuid NOT NULL REFERENCES portfolio.replica_securities(id),
  custodian_code        text NOT NULL,
  method                text NOT NULL,
  top_n                 integer,
  model_id              uuid NOT NULL REFERENCES portfolio.models(id),
  created_by            uuid NOT NULL REFERENCES public.users(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT replica_registry_keys_method_chk CHECK (
    (method = 'full' AND top_n IS NULL) OR (method = 'top_n' AND top_n IS NOT NULL AND top_n >= 1)),
  CONSTRAINT replica_registry_keys_custodian_chk CHECK (custodian_code ~ '^[a-z][a-z0-9_]*$')
);
-- the key: one live model per (org, fund entry, custodian, method, top N)
CREATE UNIQUE INDEX IF NOT EXISTS replica_registry_keys_live_uq
  ON portfolio.replica_registry_keys (org_id, replica_security_id, custodian_code, method, COALESCE(top_n, 0))
  WHERE valid_to IS NULL AND system_to IS NULL;
-- and one live key per model
CREATE UNIQUE INDEX IF NOT EXISTS replica_registry_keys_model_uq
  ON portfolio.replica_registry_keys (model_id) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 3. Viability reports (immutable) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.replica_viability_reports (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  replica_security_id   uuid NOT NULL REFERENCES portfolio.replica_securities(id),
  custodian_code        text NOT NULL,
  account_size          numeric(20, 2) NOT NULL,
  method                text NOT NULL,
  top_n                 integer,
  snapshot_id           uuid REFERENCES portfolio.replica_holdings_snapshots(id),
  terms_version_id      uuid REFERENCES portfolio.replica_terms_versions(id),
  model_id              uuid REFERENCES portfolio.models(id),
  -- exactly five answers: {key, label, status yes|no|unknown, reason, inputs, missing}
  answers               jsonb NOT NULL,
  -- the fund-versus-replica cost comparison lines ({key, label, status, amount, rate, reason})
  cost_lines            jsonb NOT NULL DEFAULT '[]'::jsonb,
  overall               text NOT NULL,
  computed_by           uuid NOT NULL REFERENCES public.users(id),
  computed_at           timestamptz NOT NULL DEFAULT now(),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT replica_viability_reports_size_chk CHECK (account_size > 0),
  CONSTRAINT replica_viability_reports_method_chk CHECK (
    (method = 'full' AND top_n IS NULL) OR (method = 'top_n' AND top_n IS NOT NULL AND top_n >= 1)),
  CONSTRAINT replica_viability_reports_answers_chk CHECK (
    jsonb_typeof(answers) = 'array' AND jsonb_array_length(answers) = 5),
  CONSTRAINT replica_viability_reports_cost_chk CHECK (jsonb_typeof(cost_lines) = 'array'),
  CONSTRAINT replica_viability_reports_overall_chk CHECK (overall IN ('yes', 'no', 'unknown')),
  CONSTRAINT replica_viability_reports_custodian_chk CHECK (custodian_code ~ '^[a-z][a-z0-9_]*$')
);
CREATE INDEX IF NOT EXISTS replica_viability_reports_entry_idx
  ON portfolio.replica_viability_reports (replica_security_id, computed_at DESC);

-- ── 4. Guards ──────────────────────────────────────────────────────────────
-- Specs and reports never change; a DELETE only on the fixture-teardown path.
CREATE OR REPLACE FUNCTION portfolio.replica_model_immutable_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND portfolio.model_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '% rows are a permanent record and never change', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS replica_model_specs_immutable_trg ON portfolio.replica_model_specs;
CREATE TRIGGER replica_model_specs_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_model_specs
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_model_immutable_guard();
DROP TRIGGER IF EXISTS replica_viability_reports_immutable_trg ON portfolio.replica_viability_reports;
CREATE TRIGGER replica_viability_reports_immutable_trg BEFORE UPDATE OR DELETE ON portfolio.replica_viability_reports
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_model_immutable_guard();

-- Registry keys: only ever closed (valid_to), never edited; DELETE on teardown only.
CREATE OR REPLACE FUNCTION portfolio.replica_registry_keys_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF portfolio.model_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'registry keys are never deleted — close them (valid_to)' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a registry key is only ever closed (valid_to), never edited' USING ERRCODE = '42501';
END $$;
DROP TRIGGER IF EXISTS replica_registry_keys_guard_trg ON portfolio.replica_registry_keys;
CREATE TRIGGER replica_registry_keys_guard_trg BEFORE UPDATE OR DELETE ON portfolio.replica_registry_keys
  FOR EACH ROW EXECUTE FUNCTION portfolio.replica_registry_keys_guard();

-- ── 5. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['replica_model_specs', 'replica_registry_keys', 'replica_viability_reports'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING %s', t || '_org_select', t, pred);
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
