-- NAV3 Task 5 — extend the relationship-status vocabulary with 'partner' and 'former_client'.
-- entities.status has NO CHECK / enum: it is enforced by services/crm_config.check_entity_values against the org's
-- public.config rows (category 'crm_entity_statuses', key 'crm_status_<key>'). crm_seed_org_config copies the default
-- org's rows into an org that has none, so adding them to every org that already has the category covers new orgs too.
-- Idempotent (ON CONFLICT DO NOTHING). NO entities row is read or rewritten.
INSERT INTO public.config (org_id, config_key, config_value, value_type, category, display_order, is_active)
SELECT o.org_id, v.config_key, v.config_value, 'selectable', 'crm_entity_statuses', v.display_order, true
  FROM (SELECT DISTINCT org_id FROM public.config WHERE category = 'crm_entity_statuses') o
 CROSS JOIN (VALUES ('crm_status_partner', 'Partner', 8),
                    ('crm_status_former_client', 'Former client', 9)) AS v(config_key, config_value, display_order)
ON CONFLICT (org_id, config_key) DO NOTHING;
