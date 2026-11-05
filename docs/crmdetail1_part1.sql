-- CRMDETAIL1 — a CRM that can hold a real multigenerational family.
--
-- Applied by apps/api/scripts/_apply_crmdetail1.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction
-- (the script opens it; this file carries no BEGIN / COMMIT).
-- Idempotent: ADD COLUMN IF NOT EXISTS / CREATE ... IF NOT EXISTS /
-- CREATE OR REPLACE / DROP ... IF EXISTS + CREATE.
--
-- WHAT THIS DOES NOT DO
--   * No relationship-type table. The governed vocabulary is a code constant
--     (apps/api/services/crm_vocab.py), served to the page in the API envelope.
--     entity_relationships.relationship_type stays free text in the schema; the
--     API is the gate (refuses an unknown type or a disallowed pair).
--   * No detail-field table. Typed detail fields are entity_attributes rows
--     under the reserved key prefix 'detail.' with a typed key registry in the
--     same code constant (entity_attributes is already bi-temporal, org-scoped
--     and RLS'd, and the fields are sparse per entity type).
--   * NO DATA. No Olivo row, no liability row, no tag. HollisMuse enters the
--     demo family through the front end.
--
-- 1. People: maiden_name, date_of_death (entities is SYSTEM-axis: an edit
--    archives a copy and keeps the id — routers/entities.update_entity copies
--    both new columns into the archived version).
-- 2. portfolio.liabilities — VALID-axis (Rule 3): nothing references a
--    liability's id, so an edit closes the current row (valid_to) and inserts a
--    new version; lineage_id is the stable identity across versions; "end"
--    closes the current row and inserts nothing (history kept, never deleted).
-- 3. Permanent demo data: a BEFORE DELETE guard on the CRM tables refuses to
--    delete any row of a record tagged demo-* (entities.tags), or of a
--    household whose members are all demo-tagged. The only path past it is the
--    LOCAL GUC crm.demo_teardown = 'fixture' for a row of an is_test org (a
--    verify fixture). A real org's demo rows can never be deleted by any script.


-- ── 1. People ───────────────────────────────────────────────────────────────
ALTER TABLE public.entities ADD COLUMN IF NOT EXISTS maiden_name text;
ALTER TABLE public.entities ADD COLUMN IF NOT EXISTS date_of_death date;

-- ── 2. Liabilities ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio.liabilities (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lineage_id             uuid NOT NULL,
    org_id                 uuid NOT NULL REFERENCES public.organizations(id),
    owner_entity_id        uuid NOT NULL REFERENCES public.entities(id),
    lender_entity_id       uuid REFERENCES public.entities(id),
    lender_name            text,
    name                   text NOT NULL,
    kind                   text NOT NULL,
    original_amount        numeric(20, 2),
    current_balance        numeric(20, 2) NOT NULL,
    currency_code          text NOT NULL DEFAULT 'USD',
    as_of                  date NOT NULL,
    rate_type              text,
    rate_text              text,
    maturity               date,
    collateral_account_id  uuid REFERENCES public.accounts(id),
    collateral_asset_id    uuid REFERENCES portfolio.assets(id),
    collateral_text        text,
    notes                  text,
    change_reason          text,
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_to               timestamptz,
    system_from            timestamptz NOT NULL DEFAULT now(),
    system_to              timestamptz,
    created_by             uuid REFERENCES public.users(id),
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT liabilities_kind_chk CHECK (kind IN ('mortgage', 'securities_backed_line', 'margin', 'term_loan',
                                                    'asset_secured_line', 'personal_loan', 'other')),
    CONSTRAINT liabilities_rate_type_chk CHECK (rate_type IS NULL OR rate_type IN ('fixed', 'floating')),
    CONSTRAINT liabilities_balance_chk CHECK (current_balance >= 0),
    CONSTRAINT liabilities_original_chk CHECK (original_amount IS NULL OR original_amount >= 0),
    CONSTRAINT liabilities_name_chk CHECK (btrim(name) <> ''),
    CONSTRAINT liabilities_currency_chk CHECK (currency_code ~ '^[A-Z]{3}$'),
    CONSTRAINT liabilities_valid_order_chk CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT liabilities_not_self_lender_chk CHECK (lender_entity_id IS NULL OR lender_entity_id <> owner_entity_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS liabilities_lineage_live_uq
    ON portfolio.liabilities (lineage_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS liabilities_owner_live_idx
    ON portfolio.liabilities (org_id, owner_entity_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS liabilities_lineage_idx ON portfolio.liabilities (lineage_id);

ALTER TABLE portfolio.liabilities ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  EXECUTE 'DROP POLICY IF EXISTS liabilities_org_select ON portfolio.liabilities';
  EXECUTE format('CREATE POLICY liabilities_org_select ON portfolio.liabilities FOR SELECT USING %s', pred);
  EXECUTE 'DROP POLICY IF EXISTS liabilities_org_insert ON portfolio.liabilities';
  EXECUTE format('CREATE POLICY liabilities_org_insert ON portfolio.liabilities FOR INSERT WITH CHECK %s', pred);
  EXECUTE 'DROP POLICY IF EXISTS liabilities_org_update ON portfolio.liabilities';
  EXECUTE format('CREATE POLICY liabilities_org_update ON portfolio.liabilities FOR UPDATE USING %s WITH CHECK %s',
                 pred, pred);
  EXECUTE 'DROP POLICY IF EXISTS liabilities_org_delete ON portfolio.liabilities';
  EXECUTE format('CREATE POLICY liabilities_org_delete ON portfolio.liabilities FOR DELETE USING %s', pred);
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.liabilities TO app_service;

-- ── 3. Permanent demo data ──────────────────────────────────────────────────
-- SECURITY DEFINER: the lookups must see the entity / household whatever RLS
-- context the deleting connection carries (an app_service connection with no
-- org GUC would otherwise read zero rows and let the delete through).
CREATE OR REPLACE FUNCTION public.crm_is_demo_entity(eid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT eid IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.entities e
    WHERE e.id = eid AND EXISTS (SELECT 1 FROM unnest(e.tags) t WHERE t LIKE 'demo-%'))
$$;

CREATE OR REPLACE FUNCTION public.crm_is_demo_household(hid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT hid IS NOT NULL AND EXISTS (SELECT 1 FROM public.household_memberships m WHERE m.household_id = hid)
     AND NOT EXISTS (
       SELECT 1 FROM public.household_memberships m
       WHERE m.household_id = hid AND NOT public.crm_is_demo_entity(m.entity_id))
$$;

CREATE OR REPLACE FUNCTION public.crm_org_is_test(oid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT COALESCE((SELECT o.is_test FROM public.organizations o WHERE o.id = oid), false)
$$;

CREATE OR REPLACE FUNCTION public.crm_demo_delete_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  j jsonb := to_jsonb(OLD);
  demo boolean := false;
  org uuid;
BEGIN
  IF TG_TABLE_NAME = 'entities' THEN
    demo := EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(j->'tags', '[]'::jsonb)) t
                    WHERE t LIKE 'demo-%');
    org := (j->>'org_id')::uuid;
  ELSIF TG_TABLE_NAME = 'households' THEN
    demo := public.crm_is_demo_household((j->>'id')::uuid);
    org := (j->>'org_id')::uuid;
  ELSIF TG_TABLE_NAME = 'household_memberships' THEN
    demo := public.crm_is_demo_entity((j->>'entity_id')::uuid);
    org := (SELECT h.org_id FROM public.households h WHERE h.id = (j->>'household_id')::uuid);
    -- the CRM's own "remove from household" (services.households) sets this LOCAL GUC: staff may correct a
    -- demo household's membership in the app; no script ever sets it.
    IF demo AND current_setting('crm.demo_edit', true) = 'staff' THEN
      RETURN OLD;
    END IF;
  ELSE
    demo := public.crm_is_demo_entity(NULLIF(j->>'entity_id', '')::uuid)
         OR public.crm_is_demo_entity(NULLIF(j->>'from_entity_id', '')::uuid)
         OR public.crm_is_demo_entity(NULLIF(j->>'to_entity_id', '')::uuid)
         OR public.crm_is_demo_entity(NULLIF(j->>'owner_entity_id', '')::uuid)
         OR public.crm_is_demo_entity(NULLIF(j->>'parent_id', '')::uuid)
         OR public.crm_is_demo_entity(NULLIF(j->>'child_id', '')::uuid);
    org := NULLIF(j->>'org_id', '')::uuid;
  END IF;
  IF NOT demo THEN
    RETURN OLD;
  END IF;
  IF current_setting('crm.demo_teardown', true) = 'fixture' AND public.crm_org_is_test(org) THEN
    RETURN OLD;          -- a verify fixture in an is_test org, by the documented teardown path
  END IF;
  RAISE EXCEPTION 'crmdetail1: refusing to delete a row of %.% that belongs to permanent demo data (a record tagged demo-*)',
    TG_TABLE_SCHEMA, TG_TABLE_NAME
    USING ERRCODE = 'P0001', HINT = 'Demo records are never deleted; retire or edit them in the app instead.';
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['public.entities', 'public.households', 'public.household_memberships',
                           'public.entity_relationships', 'public.entity_attributes', 'public.entity_addresses',
                           'public.entity_notes', 'public.entity_ownership', 'portfolio.liabilities'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS crm_demo_delete_guard_trg ON %s', t);
    EXECUTE format('CREATE TRIGGER crm_demo_delete_guard_trg BEFORE DELETE ON %s '
                   'FOR EACH ROW EXECUTE FUNCTION public.crm_demo_delete_guard()', t);
  END LOOP;
END $$;

