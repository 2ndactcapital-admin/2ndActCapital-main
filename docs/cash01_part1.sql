-- CASH01 part 1 — the obligations ledger (stored items only) and per-entity / per-household coverage minimums.
--
-- WHY NEW TABLES (docs/cash/CASH00_DISCOVERY.md 1e): no existing table carries a dated FUTURE outflow. portfolio.
-- transactions / spv_transactions record what HAPPENED; portfolio.commitments carries an unfunded AMOUNT, not dates;
-- portfolio.liabilities carries a balance and free-text terms, not a schedule; member_todos are reminders, not money.
-- DERIVED obligations (TA-projected calls, debt service from liabilities, trust / charitable payments from CRM terms)
-- are NEVER rows here — services/cash_obligations.py computes them at read time from their source.
--
-- Bi-temporal (Rule 3), VALID axis: nothing references a cash_obligations id (an alert to-do keys on lineage_id), so a
-- revision closes the live row (valid_to = now()) and inserts a new one with the SAME lineage_id; ending closes it
-- (end_reason) and inserts nothing. Recurrences are an RRULE expanded at read time, never stored as rows.
--
-- RLS: a policy for EVERY operation each table takes, in this migration. Idempotent.

CREATE TABLE IF NOT EXISTS public.cash_obligations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lineage_id       uuid NOT NULL,
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  entity_id        uuid NOT NULL REFERENCES public.entities(id),
  source_type      text NOT NULL,
  name             text NOT NULL,
  amount           numeric(20,2) NOT NULL,
  currency_code    text NOT NULL DEFAULT 'USD',
  due_date         date NOT NULL,
  rrule            text,
  end_date         date,
  certainty        text NOT NULL,
  source_ref_type  text,
  source_ref_id    uuid,
  owner_user_id    uuid REFERENCES public.users(id),
  notes            text,
  external_key     text,
  as_of            date NOT NULL DEFAULT CURRENT_DATE,
  change_reason    text,
  end_reason       text,
  created_by       uuid REFERENCES public.users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT cash_obligations_source_type_chk CHECK (source_type IN (
    'capital_call_noticed', 'debt_service', 'tax_estimate', 'insurance_premium', 'trust_payment',
    'charitable_payout', 'property_carrying', 'tuition', 'lifestyle_recurring', 'one_off', 'vendor_invoice')),
  CONSTRAINT cash_obligations_certainty_chk CHECK (certainty IN ('firm', 'estimated')),
  CONSTRAINT cash_obligations_amount_chk CHECK (amount >= 0 AND amount <= 1000000000000),
  CONSTRAINT cash_obligations_currency_chk CHECK (currency_code ~ '^[A-Z]{3}$'),
  CONSTRAINT cash_obligations_end_chk CHECK (end_date IS NULL OR end_date >= due_date),
  CONSTRAINT cash_obligations_rrule_chk CHECK (rrule IS NULL OR rrule ~ '^FREQ=(MONTHLY|YEARLY|WEEKLY)(;[A-Z]+=[A-Z0-9,+-]+)*$'),
  CONSTRAINT cash_obligations_ref_chk CHECK (
    (source_ref_type IS NULL AND source_ref_id IS NULL)
    OR (source_ref_type IN ('commitment', 'liability', 'document', 'entity', 'spv_subscription') AND source_ref_id IS NOT NULL)),
  -- a noticed call names its commitment when it matched one; a debt-service schedule names its liability
  CONSTRAINT cash_obligations_debt_ref_chk CHECK (source_type <> 'debt_service' OR source_ref_type = 'liability'),
  CONSTRAINT cash_obligations_notice_ref_chk CHECK (source_type <> 'capital_call_noticed'
    OR source_ref_type IS NULL OR source_ref_type IN ('commitment', 'spv_subscription')),
  CONSTRAINT cash_obligations_invoice_ref_chk CHECK (source_type <> 'vendor_invoice' OR source_ref_type = 'document'),
  CONSTRAINT cash_obligations_name_chk CHECK (length(btrim(name)) BETWEEN 1 AND 300),
  CONSTRAINT cash_obligations_end_reason_chk CHECK (end_reason IS NULL OR valid_to IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS cash_obligations_entity_idx ON public.cash_obligations (org_id, entity_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS cash_obligations_lineage_idx ON public.cash_obligations (lineage_id);
-- one LIVE version per lineage
CREATE UNIQUE INDEX IF NOT EXISTS cash_obligations_live_uq ON public.cash_obligations (lineage_id)
  WHERE valid_to IS NULL AND system_to IS NULL;
-- an operator loader's idempotency key (the Olivo loader: 'demo-olivo:<row key>'): one live row per key per org
CREATE UNIQUE INDEX IF NOT EXISTS cash_obligations_external_key_uq ON public.cash_obligations (org_id, external_key)
  WHERE external_key IS NOT NULL AND valid_to IS NULL AND system_to IS NULL;

-- the minimum months of coverage, per ENTITY or per HOUSEHOLD (default 6 when no row). Valid axis, same rule.
CREATE TABLE IF NOT EXISTS public.cash_coverage_minimums (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organizations(id),
  scope_type       text NOT NULL,
  entity_id        uuid REFERENCES public.entities(id),
  household_id     uuid REFERENCES public.households(id),
  min_months       numeric(6,2) NOT NULL,
  reason           text,
  created_by       uuid REFERENCES public.users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  valid_from       timestamptz NOT NULL DEFAULT now(),
  valid_to         timestamptz,
  system_from      timestamptz NOT NULL DEFAULT now(),
  system_to        timestamptz,
  CONSTRAINT cash_coverage_minimums_scope_chk CHECK (
    (scope_type = 'entity' AND entity_id IS NOT NULL AND household_id IS NULL)
    OR (scope_type = 'household' AND household_id IS NOT NULL AND entity_id IS NULL)),
  CONSTRAINT cash_coverage_minimums_months_chk CHECK (min_months >= 0 AND min_months <= 120)
);
CREATE UNIQUE INDEX IF NOT EXISTS cash_coverage_minimums_entity_uq ON public.cash_coverage_minimums (entity_id)
  WHERE scope_type = 'entity' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cash_coverage_minimums_household_uq ON public.cash_coverage_minimums (household_id)
  WHERE scope_type = 'household' AND valid_to IS NULL AND system_to IS NULL;

-- demo data is permanent: the crmdetail1 delete guard (entity_id = a demo-* record → refused outside the fixture path)
DROP TRIGGER IF EXISTS crm_demo_delete_guard_trg ON public.cash_obligations;
CREATE TRIGGER crm_demo_delete_guard_trg BEFORE DELETE ON public.cash_obligations
  FOR EACH ROW EXECUTE FUNCTION public.crm_demo_delete_guard();
DROP TRIGGER IF EXISTS crm_demo_delete_guard_trg ON public.cash_coverage_minimums;
CREATE TRIGGER crm_demo_delete_guard_trg BEFORE DELETE ON public.cash_coverage_minimums
  FOR EACH ROW EXECUTE FUNCTION public.crm_demo_delete_guard();

-- RLS — a policy for every operation each new table takes
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['cash_obligations', 'cash_coverage_minimums'] LOOP
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
