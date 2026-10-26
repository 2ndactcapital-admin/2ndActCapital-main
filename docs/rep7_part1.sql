-- REP7 — Replication Studio, sprint 7: held-away accounts and the portfolio
-- X-ray. Read-only positions and lots for accounts held elsewhere (manual
-- entry, file upload, Chancery statements, an aggregator adapter interface —
-- none connected), every row with its source and as-of; held-away data feeding
-- household wash-sale control, the gain budget and the loss bank; a stale-data
-- confirmation task; and the X-ray (look-through of every fund a household
-- holds, the replicable ones marked, a join request in one click).
--
-- Applied by apps/api/scripts/_apply_rep7.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE / ON CONFLICT.
--
-- WHERE HELD-AWAY DATA LIVES (Task 1b). In portfolio.positions / tax_lots /
-- transactions, like every other account's — so every existing reader (rep5's
-- load_holdings / realized_ytd / household_transactions, rep6's wash-sale
-- context and loss bank) sees it with no second code path. Those tables carry
-- an as_of_date (positions only) and a free source_system, but nothing names
-- WHICH held-away source and WHICH approved import wrote a row. That
-- provenance lives in public.held_away_records — one row per position / lot /
-- transaction an approved import wrote: source, import, as-of. (A link table,
-- not new columns: the portfolio tables are shared by every subsystem and
-- carry no held-away semantics of their own.)
--
-- BI-TEMPORAL (Rule 3)
--   held_away_sources    — SYSTEM axis (imports, records and confirmations
--                          point at its id): a change archives a copy and
--                          keeps the id.
--   held_away_imports    — SYSTEM axis: draft → approved / rejected is a
--                          decision on the same id (records point at it).
--   held_away_records    — VALID axis: written once by an approval; a newer
--                          approved import closes the account's earlier
--                          position records (valid_to) — never deleted, so
--                          every earlier as-of version is kept.
--   stale_confirmations  — SYSTEM axis: pending → confirmed → expired /
--                          cancelled on the same id (proposals point at it).
--   xray_runs, xray_lines — VALID axis, written once, immutable.
--   A DELETE passes the guards only on the fixture-teardown path
--   (public.execution_teardown_allowed(), never as app_service).
--
-- THE CUSTODY CLIFF. Nothing here trades. Held-away accounts are never
-- traded: every trade path refuses them in code (services/held_away.py).

-- ── 0. Permissions (org_admin by default; rbac.ensure_org_admin_role too) ──
INSERT INTO public.permissions (name, resource, action) VALUES
  ('view_held_away', 'held_away', 'view'),
  ('manage_held_away', 'held_away', 'manage'),
  ('approve_held_away', 'held_away', 'approve')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name IN ('view_held_away', 'manage_held_away', 'approve_held_away')
WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

-- ── 1. Held-away sources ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.held_away_sources (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  account_id        uuid NOT NULL REFERENCES public.accounts(id),
  kind              text NOT NULL,
  provider          text,
  status            text NOT NULL DEFAULT 'active',
  last_as_of        date,
  stale_after_days  integer,
  notes             text,
  created_by        uuid REFERENCES public.users(id),
  archived_from_id  uuid REFERENCES public.held_away_sources(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT held_away_sources_kind_chk CHECK (kind IN ('manual', 'file', 'statement', 'aggregator', 'custodian_feed')),
  -- a provider names an aggregator / feed; manual, file and statement have none
  CONSTRAINT held_away_sources_provider_chk CHECK (
    (kind IN ('manual', 'file', 'statement') AND provider IS NULL)
    OR (kind IN ('aggregator', 'custodian_feed') AND provider IS NOT NULL AND length(btrim(provider)) > 0)),
  CONSTRAINT held_away_sources_status_chk CHECK (status IN ('active', 'not_connected', 'retired')),
  -- nothing is connected: an aggregator / feed source is 'not_connected' until a provider client exists
  CONSTRAINT held_away_sources_connected_chk CHECK (kind NOT IN ('aggregator', 'custodian_feed')
                                                    OR status IN ('not_connected', 'retired')),
  CONSTRAINT held_away_sources_stale_chk CHECK (stale_after_days IS NULL OR stale_after_days BETWEEN 1 AND 3650),
  CONSTRAINT held_away_sources_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one live source per account + kind + provider
CREATE UNIQUE INDEX IF NOT EXISTS held_away_sources_live_uq
  ON public.held_away_sources (org_id, account_id, kind, COALESCE(provider, ''))
  WHERE status <> 'retired' AND valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS held_away_sources_account_idx
  ON public.held_away_sources (account_id) WHERE system_to IS NULL;

-- ── 2. Imports (one row per load; only an APPROVED import changes positions) ──
CREATE TABLE IF NOT EXISTS public.held_away_imports (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  account_id        uuid NOT NULL REFERENCES public.accounts(id),
  source_id         uuid NOT NULL REFERENCES public.held_away_sources(id),
  kind              text NOT NULL,
  as_of             date NOT NULL,
  document_id       uuid REFERENCES public.documents(id),
  file_name         text,
  file_sha256       text,
  mapping           jsonb NOT NULL DEFAULT '{}'::jsonb,
  rows_read         integer NOT NULL DEFAULT 0,
  rows_accepted     integer NOT NULL DEFAULT 0,
  rows_rejected     integer NOT NULL DEFAULT 0,
  rejects           jsonb NOT NULL DEFAULT '[]'::jsonb,
  payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
  extraction        jsonb NOT NULL DEFAULT '{}'::jsonb,
  status            text NOT NULL DEFAULT 'draft',
  entered_by        uuid NOT NULL REFERENCES public.users(id),
  approved_by       uuid REFERENCES public.users(id),
  approval_task_id  uuid REFERENCES public.workflow_run_steps(id),
  decided_at        timestamptz,
  decision_note     text,
  notes             text,
  archived_from_id  uuid REFERENCES public.held_away_imports(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT held_away_imports_kind_chk CHECK (kind IN ('manual', 'file', 'statement', 'aggregator', 'custodian_feed')),
  CONSTRAINT held_away_imports_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT held_away_imports_counts_chk CHECK (rows_read >= 0 AND rows_accepted >= 0 AND rows_rejected >= 0
                                                 AND rows_accepted + rows_rejected <= rows_read),
  CONSTRAINT held_away_imports_json_chk CHECK (jsonb_typeof(mapping) = 'object' AND jsonb_typeof(rejects) = 'array'
                                               AND jsonb_typeof(payload) = 'object'
                                               AND jsonb_typeof(extraction) = 'object'),
  -- a statement import names its Chancery document; a file import names its file
  CONSTRAINT held_away_imports_ref_chk CHECK ((kind <> 'statement' OR document_id IS NOT NULL)
                                              AND (kind <> 'file' OR file_name IS NOT NULL)),
  -- maker-checker: the person who entered the import never approves it
  CONSTRAINT held_away_imports_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> entered_by),
  CONSTRAINT held_away_imports_decided_chk CHECK (status = 'draft'
                                                  OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT held_away_imports_rejected_chk CHECK (status <> 'rejected'
                                                   OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT held_away_imports_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS held_away_imports_task_uq
  ON public.held_away_imports (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS held_away_imports_account_idx
  ON public.held_away_imports (account_id, as_of) WHERE system_to IS NULL;

-- ── 3. Provenance of every row a held-away import wrote ────────────────────
CREATE TABLE IF NOT EXISTS public.held_away_records (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES public.organizations(id),
  account_id           uuid NOT NULL REFERENCES public.accounts(id),
  source_id            uuid NOT NULL REFERENCES public.held_away_sources(id),
  import_id            uuid NOT NULL REFERENCES public.held_away_imports(id),
  record_type          text NOT NULL,
  record_id            uuid NOT NULL,
  as_of                date NOT NULL,
  -- a held-away transaction that IS a trade the adviser declared earlier
  -- (rep6 declared_outside_trades): the declared row then counts no more
  matched_declared_id  uuid REFERENCES public.declared_outside_trades(id),
  superseded_reason    text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  valid_from           timestamptz NOT NULL DEFAULT now(),
  valid_to             timestamptz,
  system_from          timestamptz NOT NULL DEFAULT now(),
  system_to            timestamptz,
  CONSTRAINT held_away_records_type_chk CHECK (record_type IN ('position', 'tax_lot', 'transaction')),
  CONSTRAINT held_away_records_match_chk CHECK (matched_declared_id IS NULL OR record_type = 'transaction'),
  CONSTRAINT held_away_records_closed_chk CHECK (valid_to IS NULL OR length(btrim(COALESCE(superseded_reason, ''))) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS held_away_records_record_uq
  ON public.held_away_records (record_type, record_id) WHERE system_to IS NULL;
-- a declared trade is matched by at most one live held-away transaction
CREATE UNIQUE INDEX IF NOT EXISTS held_away_records_declared_uq
  ON public.held_away_records (matched_declared_id)
  WHERE matched_declared_id IS NOT NULL AND valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS held_away_records_account_idx
  ON public.held_away_records (account_id, record_type) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 4. Stale-data confirmations (a human task) ─────────────────────────────
CREATE TABLE IF NOT EXISTS public.stale_confirmations (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id),
  account_id        uuid NOT NULL REFERENCES public.accounts(id),
  as_of_confirmed   date NOT NULL,
  purpose           text NOT NULL,
  proposal_id       uuid REFERENCES public.rebalance_proposals(id),
  status            text NOT NULL DEFAULT 'pending',
  requested_by      uuid REFERENCES public.users(id),
  task_id           uuid REFERENCES public.workflow_run_steps(id),
  confirmed_by      uuid REFERENCES public.users(id),
  confirmed_at      timestamptz,
  expires_on        date,
  closed_reason     text,
  note              text,
  archived_from_id  uuid REFERENCES public.stale_confirmations(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT stale_confirmations_purpose_chk CHECK (purpose IN ('proposal', 'xray', 'general')),
  CONSTRAINT stale_confirmations_proposal_chk CHECK (purpose <> 'proposal' OR proposal_id IS NOT NULL),
  CONSTRAINT stale_confirmations_status_chk CHECK (status IN ('pending', 'confirmed', 'expired', 'cancelled')),
  CONSTRAINT stale_confirmations_confirmed_chk CHECK (status <> 'confirmed'
                                                      OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL
                                                          AND expires_on IS NOT NULL)),
  CONSTRAINT stale_confirmations_closed_chk CHECK (status NOT IN ('expired', 'cancelled')
                                                   OR length(btrim(COALESCE(closed_reason, ''))) > 0),
  CONSTRAINT stale_confirmations_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
-- one open confirmation per account, purpose and proposal
CREATE UNIQUE INDEX IF NOT EXISTS stale_confirmations_open_uq
  ON public.stale_confirmations (org_id, account_id, purpose, COALESCE(proposal_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE status IN ('pending', 'confirmed') AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS stale_confirmations_task_uq
  ON public.stale_confirmations (task_id) WHERE task_id IS NOT NULL AND system_to IS NULL;

-- ── 5. X-ray runs and lines ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.xray_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES public.organizations(id),
  household_id  uuid NOT NULL REFERENCES public.households(id),
  run_date      date NOT NULL,
  trigger       text NOT NULL,
  created_by    uuid REFERENCES public.users(id),
  settings      jsonb NOT NULL DEFAULT '{}'::jsonb,
  summary       jsonb NOT NULL DEFAULT '{}'::jsonb,
  status_lines  jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  valid_from    timestamptz NOT NULL DEFAULT now(),
  valid_to      timestamptz,
  system_from   timestamptz NOT NULL DEFAULT now(),
  system_to     timestamptz,
  CONSTRAINT xray_runs_trigger_chk CHECK (trigger IN ('on_demand', 'scheduler')),
  CONSTRAINT xray_runs_maker_chk CHECK (trigger <> 'on_demand' OR created_by IS NOT NULL),
  CONSTRAINT xray_runs_json_chk CHECK (jsonb_typeof(settings) = 'object' AND jsonb_typeof(summary) = 'object'
                                       AND jsonb_typeof(status_lines) = 'array')
);
-- the daily sweep records one run per household and day
CREATE UNIQUE INDEX IF NOT EXISTS xray_runs_daily_uq
  ON public.xray_runs (org_id, household_id, run_date)
  WHERE trigger = 'scheduler' AND valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS xray_runs_household_idx
  ON public.xray_runs (household_id, created_at DESC) WHERE system_to IS NULL;

CREATE TABLE IF NOT EXISTS public.xray_lines (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  run_id              uuid NOT NULL REFERENCES public.xray_runs(id),
  line_kind           text NOT NULL,
  account_id          uuid REFERENCES public.accounts(id),
  global_security_id  uuid REFERENCES portfolio.securities_global(id),
  fund_security_id    uuid REFERENCES portfolio.securities_global(id),
  label               text NOT NULL,
  market_value        numeric(24, 2),
  as_of               date,
  source              text,
  weight              numeric(18, 10),
  weight_as_of        date,
  exposure            numeric(24, 2),
  sector              text,
  replicable          boolean,
  detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
  status_lines        jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  -- holding: one account line; constituent: a look-through slice of a fund
  -- holding (or a single stock passing through); fund: the per-fund analysis
  CONSTRAINT xray_lines_kind_chk CHECK (line_kind IN ('holding', 'constituent', 'fund')),
  CONSTRAINT xray_lines_weight_chk CHECK (weight IS NULL OR (weight >= 0 AND weight <= 1)),
  CONSTRAINT xray_lines_json_chk CHECK (jsonb_typeof(detail) = 'object' AND jsonb_typeof(status_lines) = 'array')
);
CREATE INDEX IF NOT EXISTS xray_lines_run_idx ON public.xray_lines (run_id, line_kind);

-- ── 6. Guards ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.held_away_system_axis_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived % state is immutable', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  IF NEW.archived_from_id IS DISTINCT FROM OLD.archived_from_id THEN
    RAISE EXCEPTION 'the live % row keeps its id; archive by copying it', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.held_away_imports_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  decision text[] := ARRAY['status', 'approved_by', 'approval_task_id', 'decided_at', 'decision_note', 'system_to',
    'system_from'];
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'held-away imports are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived import state is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'a % import is immutable', OLD.status USING ERRCODE = '42501';
  END IF;
  -- what was read never changes: a corrected load is a new import
  IF (to_jsonb(NEW) - decision) <> (to_jsonb(OLD) - decision) THEN
    RAISE EXCEPTION 'an import''s rows are fixed when it is loaded — load a new one' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.held_away_records_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'held-away records are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to', 'superseded_reason'])
         = (to_jsonb(OLD) - ARRAY['valid_to', 'superseded_reason']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a held-away record is only ever closed (superseded), never edited' USING ERRCODE = '42501';
END $$;

CREATE OR REPLACE FUNCTION public.xray_immutable_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND public.execution_teardown_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'an X-ray run is written once and never changed' USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS held_away_sources_guard_trg ON public.held_away_sources;
CREATE TRIGGER held_away_sources_guard_trg BEFORE UPDATE OR DELETE ON public.held_away_sources
  FOR EACH ROW EXECUTE FUNCTION public.held_away_system_axis_guard();
DROP TRIGGER IF EXISTS stale_confirmations_guard_trg ON public.stale_confirmations;
CREATE TRIGGER stale_confirmations_guard_trg BEFORE UPDATE OR DELETE ON public.stale_confirmations
  FOR EACH ROW EXECUTE FUNCTION public.held_away_system_axis_guard();
DROP TRIGGER IF EXISTS held_away_imports_guard_trg ON public.held_away_imports;
CREATE TRIGGER held_away_imports_guard_trg BEFORE UPDATE OR DELETE ON public.held_away_imports
  FOR EACH ROW EXECUTE FUNCTION public.held_away_imports_guard();
DROP TRIGGER IF EXISTS held_away_records_guard_trg ON public.held_away_records;
CREATE TRIGGER held_away_records_guard_trg BEFORE UPDATE OR DELETE ON public.held_away_records
  FOR EACH ROW EXECUTE FUNCTION public.held_away_records_guard();
DROP TRIGGER IF EXISTS xray_runs_guard_trg ON public.xray_runs;
CREATE TRIGGER xray_runs_guard_trg BEFORE UPDATE OR DELETE ON public.xray_runs
  FOR EACH ROW EXECUTE FUNCTION public.xray_immutable_guard();
DROP TRIGGER IF EXISTS xray_lines_guard_trg ON public.xray_lines;
CREATE TRIGGER xray_lines_guard_trg BEFORE UPDATE OR DELETE ON public.xray_lines
  FOR EACH ROW EXECUTE FUNCTION public.xray_immutable_guard();

-- ── 7. Loss bank: held-away realized results are their own source ──────────
ALTER TABLE public.loss_bank_entries DROP CONSTRAINT IF EXISTS loss_bank_entries_source_chk;
ALTER TABLE public.loss_bank_entries ADD CONSTRAINT loss_bank_entries_source_chk
  CHECK (source IN ('lot_close', 'fill', 'carryforward', 'declared_outside', 'held_away'));

-- ── 8. RLS — a policy for every operation each table takes ────────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['held_away_sources', 'held_away_imports', 'held_away_records', 'stale_confirmations',
                           'xray_runs', 'xray_lines'] LOOP
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
