-- PORTENTRY2 Part 1 — additive only (applied by the sprint through the Supabase MCP, 2026-11-05).
--
-- 1. Account vocabularies in public.config (Rule 1: labels are data, never in the page):
--      account_registration_types   acct_reg_<key>   individual, joint, trust, llc, lp, corporation, foundation,
--                                                    ira, roth_ira, 401k_rollover, 529, daf, insurance, other
--      account_tax_statuses         acct_tax_<key>   taxable, tax_deferred, tax_exempt
--    Seeded for the default org and every org that already holds CRM config rows; crm_seed_org_config copies
--    them to any other org on first read (the same pattern as the CRM vocabularies, portentry1_part1.sql).
-- 2. entity_addresses.note — an optional free-text note on an address (MUSE-OLIVO-C-01).
-- Nothing is dropped, nothing existing is rewritten.

INSERT INTO public.config (org_id, config_key, config_value, value_type, category, display_order, is_active)
SELECT o.org_id, v.k, v.label, 'selectable', v.cat, v.ord, true
FROM (SELECT DISTINCT org_id FROM public.config WHERE category = 'crm_entity_statuses'
      UNION SELECT '00000000-0000-0000-0000-000000000001'::uuid) o
CROSS JOIN (VALUES
  ('account_registration_types', 'acct_reg_individual',    'Individual',          10),
  ('account_registration_types', 'acct_reg_joint',         'Joint',               20),
  ('account_registration_types', 'acct_reg_trust',         'Trust',               30),
  ('account_registration_types', 'acct_reg_llc',           'LLC',                 40),
  ('account_registration_types', 'acct_reg_lp',            'Limited partnership', 50),
  ('account_registration_types', 'acct_reg_corporation',   'Corporation',         60),
  ('account_registration_types', 'acct_reg_foundation',    'Foundation',          70),
  ('account_registration_types', 'acct_reg_ira',           'IRA',                 80),
  ('account_registration_types', 'acct_reg_roth_ira',      'Roth IRA',            90),
  ('account_registration_types', 'acct_reg_401k_rollover', '401(k) rollover',    100),
  ('account_registration_types', 'acct_reg_529',           '529 plan',           110),
  ('account_registration_types', 'acct_reg_daf',           'Donor-advised fund', 120),
  ('account_registration_types', 'acct_reg_insurance',     'Insurance',          130),
  ('account_registration_types', 'acct_reg_other',         'Other',              140),
  ('account_tax_statuses',       'acct_tax_taxable',       'Taxable',             10),
  ('account_tax_statuses',       'acct_tax_tax_deferred',  'Tax-deferred',        20),
  ('account_tax_statuses',       'acct_tax_tax_exempt',    'Tax-exempt',          30)
) AS v(cat, k, label, ord)
ON CONFLICT (org_id, config_key) DO NOTHING;

CREATE OR REPLACE FUNCTION public.crm_seed_org_config(p_org uuid)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
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
    AND c.category IN ('crm_entity_statuses', 'crm_marital_statuses', 'crm_sub_types',
                       'account_registration_types', 'account_tax_statuses')
  ON CONFLICT (org_id, config_key) DO NOTHING;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$function$;

ALTER TABLE public.entity_addresses ADD COLUMN IF NOT EXISTS note text;
