-- PORTENTRY1 — close the Olivo entry-plan gaps crmdetail1 named (GAP-CRM-01…06, GAP-PORT-01…05).
--
-- Applied by apps/api/scripts/_apply_portentry1.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction
-- (the script opens it; this file carries no BEGIN / COMMIT).
-- Idempotent: ADD COLUMN IF NOT EXISTS / CREATE ... IF NOT EXISTS /
-- CREATE OR REPLACE / DROP POLICY IF EXISTS + CREATE / ON CONFLICT DO NOTHING.
--
-- WHAT THIS DOES NOT DO
--   * NO Olivo data. HollisMuse enters the family through the front end; the
--     integrated-account import FILE is generated, never loaded here.
--   * Nothing in portfolio.securities_global (the platform master). An
--     unmapped line is an ORG-scoped manual asset (portfolio.assets with
--     global_security_id NULL).
--   * No change to STAFF_ROLE_NAMES / is_staff (the bypass set).
--   * No existing entities.status value is rewritten: an 'active' record may
--     be a client or a contact, and nothing on the row says which.
--
-- 1. CRM vocabularies in public.config (Rule 1): entity statuses (adds client,
--    contact), marital statuses, sub-types per entity type. Labels live here;
--    the API serves them in its envelope. crm_seed_org_config(org) copies the
--    default org's rows to an org that has none (the deal room's pattern).
-- 2. edit_crm_contact — notes / addresses / phones / emails only — granted to
--    every support_staff role and the 'CSA / Ops' profile. Never to a client
--    role (the BUGFIX2 client rule also strips it from any profile a member
--    holds: a member's profile keys are intersected with the member role's).
-- 3. portfolio.assets: manual_terms (coupon / maturity / option terms / chain
--    for an org-scoped manual asset) and notes (a direct asset's notes).
-- 4. portfolio.asset_relinks — the REPORT of a manual asset relinked to the
--    platform master once one of its identifiers resolves (never silent).
-- 5. portfolio.provisional_imports + portfolio.provisional_positions — the
--    one-time position import for an INTEGRATED account awaiting its feed:
--    a draft (preview), a second person's approval, rows marked provisional
--    (authority 'stated'), superseded by the custodian feed.
-- 6. portfolio.rollup_requests — the debounced family rollup queue (net worth
--    stays current after a write, scoped to the family, never org-wide).
--
-- Bi-temporal (Rule 3) on every new table: valid_from / valid_to /
-- system_from / system_to. RLS: a policy per operation, NULLIF on the org GUC.


-- ── 1. CRM vocabularies ─────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.crm_seed_org_config(p_org uuid)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE n integer;
BEGIN
  IF NOT (p_org = NULLIF(current_setting('app.current_org_id', true), '')::uuid
          OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true') THEN
    RAISE EXCEPTION 'crm_seed_org_config: org context does not match';
  END IF;
  INSERT INTO public.config (org_id, config_key, config_value, value_type, category, display_order, is_active)
  SELECT p_org, c.config_key, c.config_value, c.value_type, c.category, c.display_order, c.is_active
  FROM public.config c
  WHERE c.org_id = '00000000-0000-0000-0000-000000000001'::uuid
    AND c.category IN ('crm_entity_statuses', 'crm_marital_statuses', 'crm_sub_types')
  ON CONFLICT (org_id, config_key) DO NOTHING;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;
REVOKE ALL ON FUNCTION public.crm_seed_org_config(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_seed_org_config(uuid) TO app_service;

-- value_type carries the vocabulary role: 'selectable' rows are offered on the
-- forms; 'system' rows (deceased — set by a date of death) are shown, never
-- offered. Sub-type keys are crm_subtype.<entity_type>.<key>.
INSERT INTO public.config (org_id, config_key, config_value, value_type, category, display_order)
SELECT o.id, v.k, v.label, v.vt, v.cat, v.ord
FROM public.organizations o
CROSS JOIN (VALUES
  ('crm_status_prospect', 'Prospect', 'selectable', 'crm_entity_statuses', 1),
  ('crm_status_client', 'Client', 'selectable', 'crm_entity_statuses', 2),
  ('crm_status_contact', 'Contact', 'selectable', 'crm_entity_statuses', 3),
  ('crm_status_active', 'Active', 'selectable', 'crm_entity_statuses', 4),
  ('crm_status_inactive', 'Inactive', 'selectable', 'crm_entity_statuses', 5),
  ('crm_status_archived', 'Archived', 'selectable', 'crm_entity_statuses', 6),
  ('crm_status_deceased', 'Deceased', 'system', 'crm_entity_statuses', 7),

  ('crm_marital_single', 'Single', 'selectable', 'crm_marital_statuses', 1),
  ('crm_marital_married', 'Married', 'selectable', 'crm_marital_statuses', 2),
  ('crm_marital_divorced', 'Divorced', 'selectable', 'crm_marital_statuses', 3),
  ('crm_marital_widowed', 'Widowed', 'selectable', 'crm_marital_statuses', 4),
  ('crm_marital_separated', 'Separated', 'selectable', 'crm_marital_statuses', 5),
  ('crm_marital_domestic_partnership', 'Domestic partnership', 'selectable', 'crm_marital_statuses', 6),

  ('crm_subtype.llc.holding', 'Holding', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.llc.real_estate', 'Real estate', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.llc.operating', 'Operating', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.llc.collectibles', 'Collectibles', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.llc.investment', 'Investment', 'selectable', 'crm_sub_types', 5),
  ('crm_subtype.llc.other', 'Other', 'selectable', 'crm_sub_types', 6),

  -- trust: crmdetail1's trust_type labels (trust_type sets sub_type to its label) + the three the old list had
  ('crm_subtype.trust.revocable', 'Revocable', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.trust.irrevocable', 'Irrevocable', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.trust.dynasty', 'Dynasty', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.trust.slat', 'SLAT', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.trust.grat', 'GRAT', 'selectable', 'crm_sub_types', 5),
  ('crm_subtype.trust.bypass', 'Bypass (credit shelter)', 'selectable', 'crm_sub_types', 6),
  ('crm_subtype.trust.2503c', '2503(c)', 'selectable', 'crm_sub_types', 7),
  ('crm_subtype.trust.ilit', 'ILIT', 'selectable', 'crm_sub_types', 8),
  ('crm_subtype.trust.crut', 'CRUT', 'selectable', 'crm_sub_types', 9),
  ('crm_subtype.trust.crat', 'CRAT', 'selectable', 'crm_sub_types', 10),
  ('crm_subtype.trust.clat', 'CLAT', 'selectable', 'crm_sub_types', 11),
  ('crm_subtype.trust.idgt', 'IDGT', 'selectable', 'crm_sub_types', 12),
  ('crm_subtype.trust.qprt', 'QPRT', 'selectable', 'crm_sub_types', 13),
  ('crm_subtype.trust.charitable', 'Charitable', 'selectable', 'crm_sub_types', 14),
  ('crm_subtype.trust.qtip', 'QTIP', 'selectable', 'crm_sub_types', 15),
  ('crm_subtype.trust.land_trust', 'Land Trust', 'selectable', 'crm_sub_types', 16),
  ('crm_subtype.trust.other', 'Other', 'selectable', 'crm_sub_types', 17),

  -- foundation: crmdetail1's charity_type labels (charity_type sets sub_type to its label)
  ('crm_subtype.foundation.private_foundation', 'Private foundation', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.foundation.daf', 'Donor-advised fund', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.foundation.public_charity', 'Public charity', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.foundation.supporting_org', 'Supporting organization', 'selectable', 'crm_sub_types', 4),

  ('crm_subtype.corporation.c_corp', 'C corporation', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.corporation.s_corp', 'S corporation', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.corporation.operating', 'Operating company', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.corporation.holding', 'Holding company', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.corporation.professional', 'Professional corporation', 'selectable', 'crm_sub_types', 5),
  ('crm_subtype.corporation.other', 'Other', 'selectable', 'crm_sub_types', 6),

  -- the rest of the old hard-coded list, now data
  ('crm_subtype.lp.family_lp', 'Family LP', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.lp.fund_lp', 'Fund LP', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.lp.other', 'Other', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.gp.family_gp', 'Family GP', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.gp.fund_gp', 'Fund GP', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.gp.other', 'Other', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.family_office.single_family', 'Single-family', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.family_office.multi_family', 'Multi-family', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.corp_uk.ltd', 'Ltd', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.corp_uk.plc', 'PLC', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.corp_uk.llp', 'LLP', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.corp_uk.other', 'Other', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.corp_eu.gmbh', 'GmbH', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.corp_eu.sa', 'SA', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.corp_eu.sas', 'SAS', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.corp_eu.bv', 'BV', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.corp_eu.nv', 'NV', 'selectable', 'crm_sub_types', 5),
  ('crm_subtype.corp_eu.spa', 'SpA', 'selectable', 'crm_sub_types', 6),
  ('crm_subtype.corp_eu.other', 'Other', 'selectable', 'crm_sub_types', 7),
  ('crm_subtype.corp_cayman.exempted_company', 'Exempted Company', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.corp_cayman.llc', 'LLC', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.corp_cayman.exempted_lp', 'Exempted LP', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.corp_cayman.other', 'Other', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.corp_luxembourg.sarl', 'SARL', 'selectable', 'crm_sub_types', 1),
  ('crm_subtype.corp_luxembourg.sa', 'SA', 'selectable', 'crm_sub_types', 2),
  ('crm_subtype.corp_luxembourg.scsp', 'SCSp', 'selectable', 'crm_sub_types', 3),
  ('crm_subtype.corp_luxembourg.sca', 'SCA', 'selectable', 'crm_sub_types', 4),
  ('crm_subtype.corp_luxembourg.other', 'Other', 'selectable', 'crm_sub_types', 5)
) AS v(k, label, vt, cat, ord)
WHERE NOT o.is_test
ON CONFLICT (org_id, config_key) DO NOTHING;


-- ── 2. edit_crm_contact ─────────────────────────────────────────────────────
INSERT INTO public.permissions (name, resource, action)
VALUES ('edit_crm_contact', 'crm_contact', 'edit')
ON CONFLICT (name) DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM public.roles r CROSS JOIN public.permissions p
WHERE r.name = 'support_staff' AND p.name = 'edit_crm_contact'
ON CONFLICT DO NOTHING;

INSERT INTO public.profile_permissions (org_id, profile_id, permission_key)
SELECT pr.org_id, pr.id, 'edit_crm_contact' FROM public.profiles pr
WHERE pr.name = 'CSA / Ops'
  AND NOT EXISTS (SELECT 1 FROM public.profile_permissions pp
                  WHERE pp.profile_id = pr.id AND pp.permission_key = 'edit_crm_contact');


-- ── 3. Manual assets: terms + notes ─────────────────────────────────────────
ALTER TABLE portfolio.assets ADD COLUMN IF NOT EXISTS manual_terms jsonb;
ALTER TABLE portfolio.assets ADD COLUMN IF NOT EXISTS notes text;


-- ── 4. Relink report ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.asset_relinks (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 uuid NOT NULL REFERENCES public.organizations(id),
    asset_id               uuid NOT NULL REFERENCES portfolio.assets(id),
    global_security_id     uuid NOT NULL REFERENCES portfolio.securities_global(id),
    id_type                text NOT NULL,
    id_value               text NOT NULL,
    asset_name             text NOT NULL,
    security_name          text,
    relinked_by            uuid REFERENCES public.users(id),
    trigger                text NOT NULL DEFAULT 'manual',
    acknowledged_by        uuid REFERENCES public.users(id),
    acknowledged_at        timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_to               timestamptz,
    system_from            timestamptz NOT NULL DEFAULT now(),
    system_to              timestamptz,
    CONSTRAINT asset_relinks_trigger_chk CHECK (trigger IN ('manual', 'nightly', 'entry')),
    CONSTRAINT asset_relinks_valid_order_chk CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS asset_relinks_asset_live_uq
    ON portfolio.asset_relinks (asset_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS asset_relinks_org_idx ON portfolio.asset_relinks (org_id, created_at DESC);


-- ── 5. Integrated-account one-time import ───────────────────────────────────
-- SYSTEM axis on provisional_imports (the id is kept — the task and the
-- provisional_positions rows point at it): a decision archives a copy
-- (archived_from_id, system_to) and updates the live row.
CREATE TABLE IF NOT EXISTS portfolio.provisional_imports (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 uuid NOT NULL REFERENCES public.organizations(id),
    file_name              text,
    file_sha256            text NOT NULL,
    as_of                  date NOT NULL,
    status                 text NOT NULL DEFAULT 'draft',
    rows_read              integer NOT NULL DEFAULT 0,
    rows_accepted          integer NOT NULL DEFAULT 0,
    rows_rejected          integer NOT NULL DEFAULT 0,
    preview                jsonb NOT NULL DEFAULT '{}'::jsonb,
    totals                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    entered_by             uuid NOT NULL REFERENCES public.users(id),
    approved_by            uuid REFERENCES public.users(id),
    approval_task_id       uuid,
    decided_at             timestamptz,
    decision_note          text,
    notes                  text,
    archived_from_id       uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_to               timestamptz,
    system_from            timestamptz NOT NULL DEFAULT now(),
    system_to              timestamptz,
    CONSTRAINT provisional_imports_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
    CONSTRAINT provisional_imports_checker_chk CHECK (approved_by IS NULL OR approved_by <> entered_by),
    CONSTRAINT provisional_imports_valid_order_chk CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS provisional_imports_org_idx ON portfolio.provisional_imports (org_id, created_at DESC);

-- VALID axis on provisional_positions: superseding a row closes it
-- (valid_to, superseded_by_source, superseded_reason); nothing is deleted.
CREATE TABLE IF NOT EXISTS portfolio.provisional_positions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 uuid NOT NULL REFERENCES public.organizations(id),
    import_id              uuid NOT NULL REFERENCES portfolio.provisional_imports(id),
    account_id             uuid NOT NULL REFERENCES public.accounts(id),
    position_id            uuid NOT NULL REFERENCES portfolio.positions(id),
    as_of                  date NOT NULL,
    superseded_by_source   text,
    superseded_reason      text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_to               timestamptz,
    system_from            timestamptz NOT NULL DEFAULT now(),
    system_to              timestamptz,
    CONSTRAINT provisional_positions_valid_order_chk CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS provisional_positions_account_live_idx
    ON portfolio.provisional_positions (org_id, account_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS provisional_positions_import_idx ON portfolio.provisional_positions (import_id);


-- ── 6. Debounced family rollup queue ────────────────────────────────────────
-- One PENDING request per (org, family_key); a write within the debounce
-- window moves run_after forward instead of queueing a second run. A
-- request's lifecycle (pending → running → done / failed) is a status on the
-- live row — the queue is operational state, not a business fact; finished
-- requests are kept (valid_to set when done) as the run log.
CREATE TABLE IF NOT EXISTS portfolio.rollup_requests (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 uuid NOT NULL REFERENCES public.organizations(id),
    family_key             text NOT NULL,
    entity_ids             uuid[] NOT NULL,
    reason                 text NOT NULL,
    status                 text NOT NULL DEFAULT 'pending',
    requested_by           uuid REFERENCES public.users(id),
    requested_at           timestamptz NOT NULL DEFAULT now(),
    run_after              timestamptz NOT NULL DEFAULT now(),
    started_at             timestamptz,
    finished_at            timestamptz,
    as_of_date             date,
    result                 jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_to               timestamptz,
    system_from            timestamptz NOT NULL DEFAULT now(),
    system_to              timestamptz,
    CONSTRAINT rollup_requests_status_chk CHECK (status IN ('pending', 'running', 'done', 'failed')),
    CONSTRAINT rollup_requests_scope_chk CHECK (cardinality(entity_ids) >= 1),
    CONSTRAINT rollup_requests_valid_order_chk CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS rollup_requests_pending_uq
    ON portfolio.rollup_requests (org_id, family_key) WHERE status = 'pending' AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS rollup_requests_due_idx
    ON portfolio.rollup_requests (run_after) WHERE status = 'pending' AND system_to IS NULL;


-- ── RLS: a policy per operation on every new table ─────────────────────────
DO $$
DECLARE
  pred text := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
               'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  t text;
  short text;
BEGIN
  FOREACH t IN ARRAY ARRAY['asset_relinks', 'provisional_imports', 'provisional_positions', 'rollup_requests'] LOOP
    short := t;
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', short || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING %s', short || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', short || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK %s', short || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', short || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING %s WITH CHECK %s', short || '_org_update', t,
                   pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', short || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING %s', short || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;
