-- REP14 — Replication Studio phase 4: the SECTION 351 EXIT (planning side, manual-first).
--
-- Applied by apps/api/scripts/_apply_rep14.py --schema (one transaction, the operator DSN) and read back by
-- --verify. Idempotent. IT WRITES NO ROWS: every table starts empty; the defaults (sponsor eligibility rules, the
-- "harvesting has run dry" thresholds) are stated in services/section351.py and used until an org saves its own.
--
--   1. public.section351_sponsor_profiles — the third-party ETF sponsor (name, adviser / distributor, contact,
--      launch windows) and ITS eligibility rules (owner types eligible / sponsor review / refused, U.S. persons
--      only, ineligible asset classes, the cash rule). Org admin edits; every edit is a NEW VERSION (valid axis,
--      rule 3: the old version is closed, never changed — a plan points at the exact version row it used).
--   2. public.section351_settings — the "harvesting has run dry" thresholds per org, versioned the same way.
--   3. public.section351_asset_classes — classifications the security master does not carry (government
--      security, restricted, illiquid, crypto, physical commodity, commodity pool, grantor trust, partnership
--      interest, derivative), entered by a person with a source and an as-of. Platform rows (Super Admin) are read
--      by every org; org rows are org-isolated.
--   4. public.section351_plans — the pool (system axis: the id is stable — accounts, steps, basis records and
--      transfers point at it). draft → ready → pending_approval → approved → transferring → completed | cancelled.
--   5. public.section351_plan_accounts — each contributing account: owner type + eligibility, the adviser's U.S.
--      person confirmation, the lots contributed, the 25 / 50 test at the plan's valuation date, the member's
--      written consent (document, date, signer), the member's tax adviser's confirmation (recorded, never inferred)
--      and any cash to the member (boot). System axis (the id is the disclosure acknowledgement's subject).
--   6. public.section351_basis_records — the transfer agent's basis record (CSV + PDF in the document vault), one
--      VERSION per export (valid axis), with its hashes and totals.
--   7. public.section351_tracker_steps — the 6–9 month project tracker: the standard steps, each with an owner, a
--      due date, a status and its human task on the workflow engine. System axis.
--   8. public.section351_transfers — the new kind of execution instruction, "in-kind transfer out": one per
--      contributed lot, created at the plan's approval (maker-checker), routed manual (or refused by an adapter
--      that does not declare in_kind_transfers), sent, then confirmed — the lot closes BY TRANSFER (no realised
--      gain) and the ETF lot carries its basis and acquisition date. Its own table: rep2's instructions are trades
--      (side / amount / price-kind CHECKs, fills); a transfer has no price and no fill.
--
-- RLS: org isolation with NULLIF on every operation of every org table; section351_asset_classes platform rows are
-- read by all and written only under app.is_super_admin. DELETE only on the fixture-teardown path
-- (public.execution_teardown_allowed(): GUC execution.teardown = 'fixture' and never app_service).

-- ── 1. Sponsor profiles ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_sponsor_profiles (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL REFERENCES public.organizations(id),
  profile_key             uuid NOT NULL,
  version_no              integer NOT NULL,
  name                    text NOT NULL,
  adviser_name            text,
  distributor_name        text,
  contact                 text,
  launch_windows          jsonb NOT NULL DEFAULT '[]'::jsonb,
  eligible_owner_types    text[] NOT NULL,
  review_owner_types      text[] NOT NULL,
  us_persons_only         boolean NOT NULL DEFAULT true,
  ineligible_assets       text[] NOT NULL,
  cash_rule               text NOT NULL DEFAULT 'not_contributed',
  note                    text,
  status                  text NOT NULL DEFAULT 'active',
  created_by              uuid NOT NULL REFERENCES public.users(id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  valid_from              timestamptz NOT NULL DEFAULT now(),
  valid_to                timestamptz,
  system_from             timestamptz NOT NULL DEFAULT now(),
  system_to               timestamptz,
  CONSTRAINT section351_sponsor_profiles_version_chk CHECK (version_no >= 1),
  CONSTRAINT section351_sponsor_profiles_name_chk CHECK (length(btrim(name)) > 0),
  CONSTRAINT section351_sponsor_profiles_status_chk CHECK (status IN ('active', 'retired')),
  CONSTRAINT section351_sponsor_profiles_cash_chk CHECK (cash_rule IN ('not_contributed', 'contributed')),
  CONSTRAINT section351_sponsor_profiles_owner_chk CHECK (
    eligible_owner_types <@ ARRAY['individual', 'joint', 'trust', 's_corp', 'partnership']::text[]
    AND review_owner_types <@ ARRAY['individual', 'joint', 'trust', 's_corp', 'partnership']::text[]
    AND NOT (eligible_owner_types && review_owner_types)),
  CONSTRAINT section351_sponsor_profiles_assets_chk CHECK (
    ineligible_assets <@ ARRAY['restricted', 'illiquid', 'derivative', 'crypto', 'physical_commodity',
                               'commodity_pool', 'grantor_trust', 'partnership_interest']::text[]),
  CONSTRAINT section351_sponsor_profiles_windows_chk CHECK (jsonb_typeof(launch_windows) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_sponsor_profiles_version_uq
  ON public.section351_sponsor_profiles (org_id, profile_key, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS section351_sponsor_profiles_live_uq
  ON public.section351_sponsor_profiles (org_id, profile_key) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 2. "Harvesting has run dry" thresholds ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_settings (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                     uuid NOT NULL REFERENCES public.organizations(id),
  version_no                 integer NOT NULL,
  max_harvestable_loss_pct   numeric NOT NULL,
  min_unrealized_gain_pct    numeric NOT NULL,
  min_years_since_inception  numeric NOT NULL,
  note                       text,
  created_by                 uuid NOT NULL REFERENCES public.users(id),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  valid_from                 timestamptz NOT NULL DEFAULT now(),
  valid_to                   timestamptz,
  system_from                timestamptz NOT NULL DEFAULT now(),
  system_to                  timestamptz,
  CONSTRAINT section351_settings_version_chk CHECK (version_no >= 1),
  CONSTRAINT section351_settings_range_chk CHECK (
    max_harvestable_loss_pct >= 0 AND max_harvestable_loss_pct <= 1
    AND min_unrealized_gain_pct >= 0 AND min_unrealized_gain_pct <= 10
    AND min_years_since_inception >= 0 AND min_years_since_inception <= 50)
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_settings_version_uq ON public.section351_settings (org_id, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS section351_settings_live_uq
  ON public.section351_settings (org_id) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 3. Asset classes the master does not carry ─────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_asset_classes (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_scope         text NOT NULL,
  org_id              uuid REFERENCES public.organizations(id),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  asset_class         text NOT NULL,
  source              text NOT NULL,
  as_of               date NOT NULL,
  entered_by          uuid NOT NULL REFERENCES public.users(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT section351_asset_classes_scope_chk CHECK (
    (owner_scope = 'platform' AND org_id IS NULL) OR (owner_scope = 'org' AND org_id IS NOT NULL)),
  CONSTRAINT section351_asset_classes_class_chk CHECK (asset_class IN (
    'government', 'restricted', 'illiquid', 'derivative', 'crypto', 'physical_commodity', 'commodity_pool',
    'grantor_trust', 'partnership_interest')),
  CONSTRAINT section351_asset_classes_source_chk CHECK (length(btrim(source)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_asset_classes_live_uq
  ON public.section351_asset_classes (owner_scope, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid),
                                      global_security_id, asset_class)
  WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 4. Plans (the pool) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_plans (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                    uuid NOT NULL REFERENCES public.organizations(id),
  name                      text NOT NULL,
  target_etf                text NOT NULL,
  sponsor_trust             text,
  sponsor_profile_id        uuid NOT NULL REFERENCES public.section351_sponsor_profiles(id),
  sponsor_profile_version   integer NOT NULL,
  valuation_date            date NOT NULL,
  seed_capital              numeric NOT NULL DEFAULT 0,
  seed_note                 text,
  etf_custodian_account     text,
  status                    text NOT NULL DEFAULT 'draft',
  control_result            jsonb NOT NULL DEFAULT '{}'::jsonb,
  readiness                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  ready_by                  uuid REFERENCES public.users(id),
  ready_at                  timestamptz,
  maker_id                  uuid NOT NULL REFERENCES public.users(id),
  submitted_by              uuid REFERENCES public.users(id),
  submitted_at              timestamptz,
  approval_task_id          uuid,
  approved_by               uuid REFERENCES public.users(id),
  approved_at               timestamptz,
  decision_note             text,
  completed_at              timestamptz,
  cancelled_by              uuid REFERENCES public.users(id),
  cancelled_at              timestamptz,
  cancel_reason             text,
  archived_from_id          uuid,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  valid_from                timestamptz NOT NULL DEFAULT now(),
  valid_to                  timestamptz,
  system_from               timestamptz NOT NULL DEFAULT now(),
  system_to                 timestamptz,
  CONSTRAINT section351_plans_name_chk CHECK (length(btrim(name)) > 0 AND length(btrim(target_etf)) > 0),
  CONSTRAINT section351_plans_status_chk CHECK (status IN ('draft', 'ready', 'pending_approval', 'approved',
                                                           'transferring', 'completed', 'cancelled')),
  CONSTRAINT section351_plans_seed_chk CHECK (seed_capital >= 0),
  CONSTRAINT section351_plans_ready_chk CHECK (status IN ('draft', 'cancelled')
                                               OR (ready_by IS NOT NULL AND ready_at IS NOT NULL)),
  CONSTRAINT section351_plans_pending_chk CHECK (status <> 'pending_approval' OR approval_task_id IS NOT NULL),
  CONSTRAINT section351_plans_approved_chk CHECK (status NOT IN ('approved', 'transferring', 'completed')
                                                  OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT section351_plans_maker_checker_chk CHECK (approved_by IS NULL OR (approved_by <> maker_id
                                                       AND approved_by IS DISTINCT FROM submitted_by)),
  CONSTRAINT section351_plans_cancelled_chk CHECK (status <> 'cancelled' OR (cancelled_by IS NOT NULL
                                                   AND cancelled_at IS NOT NULL
                                                   AND length(btrim(COALESCE(cancel_reason, ''))) > 0)),
  CONSTRAINT section351_plans_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);

-- ── 5. Contributing accounts ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_plan_accounts (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                    uuid NOT NULL REFERENCES public.organizations(id),
  plan_id                   uuid NOT NULL REFERENCES public.section351_plans(id),
  account_id                uuid NOT NULL REFERENCES public.accounts(id),
  owner_type                text NOT NULL,
  eligibility               jsonb NOT NULL DEFAULT '{}'::jsonb,
  us_person                 text NOT NULL DEFAULT 'unconfirmed',
  us_person_source          text,
  us_person_as_of           date,
  lot_ids                   uuid[],
  test_result               jsonb NOT NULL DEFAULT '{}'::jsonb,
  test_verdict              text NOT NULL DEFAULT 'not_run',
  contributed_value         numeric,
  consent_document_id       uuid REFERENCES public.documents(id),
  consent_signed_on         date,
  consent_signer            text,
  consent_recorded_by       uuid REFERENCES public.users(id),
  tax_adviser_name          text,
  tax_adviser_confirmed_on  date,
  tax_adviser_note          text,
  tax_adviser_recorded_by   uuid REFERENCES public.users(id),
  cash_to_member            numeric NOT NULL DEFAULT 0,
  status                    text NOT NULL DEFAULT 'active',
  etf_global_security_id    uuid REFERENCES portfolio.securities_global(id),
  etf_shares                numeric,
  received_on               date,
  confirmed_by              uuid REFERENCES public.users(id),
  confirmed_at              timestamptz,
  removed_reason            text,
  added_by                  uuid NOT NULL REFERENCES public.users(id),
  archived_from_id          uuid,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  valid_from                timestamptz NOT NULL DEFAULT now(),
  valid_to                  timestamptz,
  system_from               timestamptz NOT NULL DEFAULT now(),
  system_to                 timestamptz,
  CONSTRAINT section351_plan_accounts_owner_chk CHECK (owner_type IN (
    'individual', 'joint', 'trust', 's_corp', 'partnership', 'c_corp', 'non_us', 'retirement', 'other')),
  CONSTRAINT section351_plan_accounts_us_chk CHECK (us_person IN ('unconfirmed', 'yes', 'no')
    AND (us_person = 'unconfirmed' OR (length(btrim(COALESCE(us_person_source, ''))) > 0
                                       AND us_person_as_of IS NOT NULL))),
  CONSTRAINT section351_plan_accounts_verdict_chk CHECK (test_verdict IN ('not_run', 'pass', 'fail',
                                                                         'holdings_needed')),
  CONSTRAINT section351_plan_accounts_consent_chk CHECK (
    (consent_document_id IS NULL AND consent_signed_on IS NULL AND consent_signer IS NULL)
    OR (consent_document_id IS NOT NULL AND consent_signed_on IS NOT NULL
        AND length(btrim(COALESCE(consent_signer, ''))) > 0 AND consent_recorded_by IS NOT NULL)),
  CONSTRAINT section351_plan_accounts_tax_adviser_chk CHECK (
    (tax_adviser_confirmed_on IS NULL AND tax_adviser_name IS NULL)
    OR (tax_adviser_confirmed_on IS NOT NULL AND length(btrim(COALESCE(tax_adviser_name, ''))) > 0
        AND tax_adviser_recorded_by IS NOT NULL)),
  CONSTRAINT section351_plan_accounts_cash_chk CHECK (cash_to_member >= 0),
  CONSTRAINT section351_plan_accounts_status_chk CHECK (status IN ('active', 'removed', 'transferred')),
  CONSTRAINT section351_plan_accounts_removed_chk CHECK (status <> 'removed'
                                                         OR length(btrim(COALESCE(removed_reason, ''))) > 0),
  CONSTRAINT section351_plan_accounts_transferred_chk CHECK (status <> 'transferred' OR (
    etf_global_security_id IS NOT NULL AND etf_shares > 0 AND received_on IS NOT NULL AND confirmed_by IS NOT NULL
    AND confirmed_at IS NOT NULL)),
  CONSTRAINT section351_plan_accounts_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_plan_accounts_live_uq
  ON public.section351_plan_accounts (plan_id, account_id)
  WHERE system_to IS NULL AND valid_to IS NULL AND status <> 'removed';

-- ── 6. Basis records (the transfer agent's file) ───────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_basis_records (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  plan_id             uuid NOT NULL REFERENCES public.section351_plans(id),
  version_no          integer NOT NULL,
  csv_document_id     uuid NOT NULL REFERENCES public.documents(id),
  csv_sha256          text NOT NULL,
  pdf_document_id     uuid NOT NULL REFERENCES public.documents(id),
  pdf_sha256          text NOT NULL,
  totals              jsonb NOT NULL,
  lines               jsonb NOT NULL,
  created_by          uuid NOT NULL REFERENCES public.users(id),
  delivered_on        date,
  delivered_note      text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT section351_basis_records_version_chk CHECK (version_no >= 1),
  CONSTRAINT section351_basis_records_sha_chk CHECK (csv_sha256 ~ '^[0-9a-f]{64}$' AND pdf_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT section351_basis_records_json_chk CHECK (jsonb_typeof(totals) = 'object'
                                                      AND jsonb_typeof(lines) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_basis_records_version_uq
  ON public.section351_basis_records (plan_id, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS section351_basis_records_live_uq
  ON public.section351_basis_records (plan_id) WHERE valid_to IS NULL AND system_to IS NULL;

-- ── 7. Project tracker ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_tracker_steps (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  plan_id             uuid NOT NULL REFERENCES public.section351_plans(id),
  step_key            text NOT NULL,
  seq                 integer NOT NULL,
  owner_id            uuid REFERENCES public.users(id),
  due_on              date,
  status              text NOT NULL DEFAULT 'open',
  task_id             uuid,
  done_by             uuid REFERENCES public.users(id),
  done_at             timestamptz,
  note                text,
  archived_from_id    uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT section351_tracker_steps_key_chk CHECK (step_key IN (
    'counsel_engaged', 'sponsor_selected', 'custodian_confirms', 'valuation_date_set', 'consents',
    'basis_record_delivered', 'transfer', 'confirmation')),
  CONSTRAINT section351_tracker_steps_status_chk CHECK (status IN ('open', 'done', 'cancelled')),
  CONSTRAINT section351_tracker_steps_done_chk CHECK (status <> 'done' OR (done_by IS NOT NULL AND done_at IS NOT NULL)),
  CONSTRAINT section351_tracker_steps_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_tracker_steps_live_uq
  ON public.section351_tracker_steps (plan_id, step_key) WHERE system_to IS NULL AND valid_to IS NULL;

-- ── 8. In-kind transfer instructions ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.section351_transfers (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id),
  plan_id             uuid NOT NULL REFERENCES public.section351_plans(id),
  plan_account_id     uuid NOT NULL REFERENCES public.section351_plan_accounts(id),
  account_id          uuid NOT NULL REFERENCES public.accounts(id),
  tax_lot_id          uuid NOT NULL REFERENCES portfolio.tax_lots(id),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  quantity            numeric NOT NULL,
  basis               numeric NOT NULL,
  acquired_date       date NOT NULL,
  fmv                 numeric NOT NULL,
  destination         text NOT NULL,
  kind                text NOT NULL DEFAULT 'in_kind_transfer_out',
  status              text NOT NULL DEFAULT 'approved',
  routed_via          text,
  routing_reason      text,
  maker_ids           uuid[] NOT NULL,
  approved_by         uuid NOT NULL REFERENCES public.users(id),
  approved_at         timestamptz NOT NULL,
  sent_by             uuid REFERENCES public.users(id),
  sent_at             timestamptz,
  sent_channel        text,
  confirmed_by        uuid REFERENCES public.users(id),
  confirmed_at        timestamptz,
  received_on         date,
  closed_lot_id       uuid REFERENCES portfolio.tax_lots(id),
  etf_lot_id          uuid REFERENCES portfolio.tax_lots(id),
  etf_shares          numeric,
  realized_gain       numeric,
  cancelled_by        uuid REFERENCES public.users(id),
  cancelled_at        timestamptz,
  cancel_reason       text,
  archived_from_id    uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT section351_transfers_kind_chk CHECK (kind = 'in_kind_transfer_out'),
  CONSTRAINT section351_transfers_status_chk CHECK (status IN ('approved', 'sent', 'confirmed', 'cancelled')),
  CONSTRAINT section351_transfers_amount_chk CHECK (quantity > 0 AND basis >= 0 AND fmv >= 0),
  CONSTRAINT section351_transfers_maker_checker_chk CHECK (NOT (approved_by = ANY (maker_ids))),
  CONSTRAINT section351_transfers_sent_chk CHECK (status NOT IN ('sent', 'confirmed') OR (
    sent_by IS NOT NULL AND sent_at IS NOT NULL AND routed_via IS NOT NULL
    AND length(btrim(COALESCE(sent_channel, ''))) > 0)),
  CONSTRAINT section351_transfers_confirmed_chk CHECK (status <> 'confirmed' OR (
    confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND received_on IS NOT NULL AND closed_lot_id IS NOT NULL
    AND etf_lot_id IS NOT NULL AND etf_shares > 0 AND realized_gain = 0)),
  CONSTRAINT section351_transfers_cancelled_chk CHECK (status <> 'cancelled' OR (
    cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL AND length(btrim(COALESCE(cancel_reason, ''))) > 0)),
  CONSTRAINT section351_transfers_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS section351_transfers_lot_uq
  ON public.section351_transfers (plan_id, tax_lot_id) WHERE system_to IS NULL AND valid_to IS NULL
  AND status <> 'cancelled';

-- ── 9. Guards: never deleted (fixture teardown only); an archived row never changes ───────────────────────────
CREATE OR REPLACE FUNCTION public.section351_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Section 351 records are never deleted — a version is closed or a status recorded'
      USING ERRCODE = '42501';
  END IF;
  IF OLD.system_to IS NOT NULL THEN
    RAISE EXCEPTION 'an archived Section 351 record never changes' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NOT NULL THEN
    RAISE EXCEPTION 'a closed Section 351 version never changes — write a new version' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

-- versioned tables: a live row is only ever CLOSED (valid_to), nothing else changes (rule 3, valid axis)
CREATE OR REPLACE FUNCTION public.section351_version_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.execution_teardown_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Section 351 versions are never deleted' USING ERRCODE = '42501';
  END IF;
  IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['valid_to']) = (to_jsonb(OLD) - ARRAY['valid_to']) THEN
    RETURN NEW;
  END IF;
  -- the basis record's delivery is recorded once, on the live version
  IF TG_TABLE_NAME = 'section351_basis_records' AND OLD.valid_to IS NULL AND NEW.valid_to IS NULL
     AND OLD.delivered_on IS NULL AND NEW.delivered_on IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['delivered_on', 'delivered_note'])
         = (to_jsonb(OLD) - ARRAY['delivered_on', 'delivered_note']) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'a Section 351 version is fixed when written — save a new version' USING ERRCODE = '42501';
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['section351_plans', 'section351_plan_accounts', 'section351_tracker_steps',
                           'section351_transfers'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_guard_trg', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW '
                   'EXECUTE FUNCTION public.section351_guard()', t || '_guard_trg', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['section351_sponsor_profiles', 'section351_settings', 'section351_asset_classes',
                           'section351_basis_records'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_guard_trg', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW '
                   'EXECUTE FUNCTION public.section351_version_guard()', t || '_guard_trg', t);
  END LOOP;
END $$;

-- ── 10. RLS — a policy for every operation each table takes ───────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['section351_sponsor_profiles', 'section351_settings', 'section351_plans',
                           'section351_plan_accounts', 'section351_basis_records', 'section351_tracker_steps',
                           'section351_transfers'] LOOP
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

ALTER TABLE public.section351_asset_classes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS section351_asset_classes_select ON public.section351_asset_classes;
CREATE POLICY section351_asset_classes_select ON public.section351_asset_classes FOR SELECT USING (
  owner_scope = 'platform'
  OR org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
  OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS section351_asset_classes_insert ON public.section351_asset_classes;
CREATE POLICY section351_asset_classes_insert ON public.section351_asset_classes FOR INSERT WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid));
DROP POLICY IF EXISTS section351_asset_classes_update ON public.section351_asset_classes;
CREATE POLICY section351_asset_classes_update ON public.section351_asset_classes FOR UPDATE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid))
  WITH CHECK (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid));
DROP POLICY IF EXISTS section351_asset_classes_delete ON public.section351_asset_classes;
CREATE POLICY section351_asset_classes_delete ON public.section351_asset_classes FOR DELETE USING (
  NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  OR (owner_scope = 'org' AND org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid));
GRANT SELECT, INSERT, UPDATE, DELETE ON public.section351_asset_classes TO app_service;
