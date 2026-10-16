-- bulkfetch.structural — Part B (Task 9): registry DML only, no DDL.
--
-- 1. agent_commission_pct's DISPLAY label. The key is unchanged (renaming a
--    registry key in place breaks the legacy extractor's fixed columns — see
--    notefields_registry_v3.sql); only what the reviewer reads changes. The
--    filing calls it the "underwriting discount" on most bank covers, and the
--    reviewer could not find it under "Agent Commission %".
UPDATE portfolio.note_terms_field_registry
   SET display_label = 'Underwriting discount / agent commission'
 WHERE field_key = 'agent_commission_pct';

-- 2. Calculated dollar totals. extraction_method 'derived' (the registry's
--    derived_chk demands a non-empty derived_from): computed by
--    services.note_extraction.derive.dollar_totals from issue_size x pct / 100,
--    range ends separately. Shown in the economics section as CALCULATED —
--    the review API refuses a gold write to either key.
INSERT INTO portfolio.note_terms_field_registry
    (field_key, display_label, data_type, description, section, sort_order, is_critical, value_shape,
     unit, synonyms, extraction_method, derived_from)
VALUES
    ('total_fees_usd', 'Total fees ($, calculated)', 'numeric',
     'Calculated, never extracted: issue_size x total_fees_pct / 100, each end of the fee range computed '
     'separately. Compared with the dollar total the filing''s cover table states, when it states one.',
     'economics', 710, false, 'range', 'usd', '{}', 'derived', ARRAY['issue_size', 'total_fees_pct']),
    ('proceeds_to_issuer_usd', 'Proceeds to issuer ($, calculated)', 'numeric',
     'Calculated, never extracted: issue_size x proceeds_to_issuer_pct / 100, each end of the range computed '
     'separately. Compared with the dollar total the filing''s cover table states, when it states one.',
     'economics', 711, false, 'range', 'usd', '{}', 'derived', ARRAY['issue_size', 'proceeds_to_issuer_pct'])
ON CONFLICT (field_key) DO NOTHING;
