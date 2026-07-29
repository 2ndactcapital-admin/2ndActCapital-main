-- Sprint 27 (TaskRouter) — per-org ORDERED fallback CHAIN seed.
--
-- Mini-bedrock stored a single-value ai.model.fallback that NO code ever
-- consumed. Sprint 27 upgrades that to a real ordered chain the central
-- resolver actually walks: try the primary model; on failure try the next
-- model in the chain; continue until one succeeds or the chain is exhausted.
--
-- This is one of the two places a literal model string is allowed to appear
-- (the other is DEFAULT_SETTINGS in apps/api/services/org_settings.py). It IS
-- the seed data, not application logic.
--
-- org_settings is NOT bi-temporal: the natural key is (org_id, setting_key)
-- and writes are a plain upsert. setting_value is jsonb NOT NULL — here the
-- value is a JSON ARRAY of model strings, ordered primary-first.
--
-- Behaviour preservation: the default org's existing single fallback value was
-- "claude-haiku-4-5-20251001", so we seed a ONE-ITEM array with exactly that
-- value. The primary (ai.model.default) is also haiku, so the resolved attempt
-- order dedupes to a single haiku call — identical to today's behaviour, no
-- change to what actually gets called for existing functionality.
--
-- is_public = false: model/provider config is backend-only and must never be
-- served to the unauthenticated login screen (like the other ai.model.* keys).

INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public)
SELECT
    '00000000-0000-0000-0000-000000000001'::uuid,
    v.setting_key, v.setting_value::jsonb, v.category, false
FROM (VALUES
    ('ai.model.fallback_chain', '["claude-haiku-4-5-20251001"]', 'ai')
) AS v(setting_key, setting_value, category)
ON CONFLICT (org_id, setting_key) DO UPDATE
    SET setting_value = EXCLUDED.setting_value,
        category      = EXCLUDED.category,
        updated_at    = now();
