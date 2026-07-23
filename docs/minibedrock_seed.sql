-- mini-bedrock — config-driven AI model seed.
--
-- Which model the platform calls is now a CONFIG value per org, not a
-- hardcoded string scattered through the codebase. This file is one of the two
-- places a literal model string is allowed to appear (the other is
-- DEFAULT_SETTINGS in apps/api/services/org_settings.py). It IS the seed data,
-- not application logic.
--
-- org_settings is NOT bi-temporal: the natural key is (org_id, setting_key)
-- and writes are a plain upsert. setting_value is jsonb NOT NULL, so scalars
-- must be JSON-encoded ('"anthropic"'::jsonb, never 'anthropic').
--
-- Values preserve the EXACT models the code called before this sprint:
--   ai.model.default   = claude-haiku-4-5-20251001  (extraction, briefs, summaries)
--   ai.model.provider  = anthropic
--   ai.model.fallback  = claude-haiku-4-5-20251001  (no prior fallback pattern existed)
--   ai.model.assistant = claude-sonnet-4-6          (tool-using assistant / narration)
--
-- is_public = false: model/provider config is backend-only and must never be
-- served to the unauthenticated login screen (unlike the brand settings).
-- Seeded onto the real default org where all live data sits.

INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public)
SELECT
    '00000000-0000-0000-0000-000000000001'::uuid,
    v.setting_key, v.setting_value::jsonb, v.category, false
FROM (VALUES
    ('ai.model.default',   '"claude-haiku-4-5-20251001"', 'ai'),
    ('ai.model.provider',  '"anthropic"',                 'ai'),
    ('ai.model.fallback',  '"claude-haiku-4-5-20251001"', 'ai'),
    ('ai.model.assistant', '"claude-sonnet-4-6"',         'ai')
) AS v(setting_key, setting_value, category)
ON CONFLICT (org_id, setting_key) DO UPDATE
    SET setting_value = EXCLUDED.setting_value,
        category      = EXCLUDED.category,
        updated_at    = now();
