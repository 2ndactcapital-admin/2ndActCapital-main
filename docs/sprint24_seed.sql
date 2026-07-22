-- Sprint 24 — white-label config seed.
--
-- This file is one of the two places literal 2nd Act brand values are allowed
-- to appear (the other is DEFAULT_SETTINGS in apps/api/services/org_settings.py).
-- It IS the seed data, not application logic.
--
-- org_settings is NOT bi-temporal: the natural key is (org_id, setting_key)
-- and writes are a plain upsert. setting_value is jsonb NOT NULL, so scalars
-- must be JSON-encoded ('"USD"'::jsonb, never 'USD').

-- ---------------------------------------------------------------------------
-- Orgs
-- ---------------------------------------------------------------------------
-- Ripasso is the platform org: Super Admins live here and are NOT scoped to
-- any client. 2nd Act Capital is client #1.
--
-- NOTE (applied 2026-07-22): Part 1 seeded the 23 settings onto a NEW org row
-- with slug '2nd-act-capital', while every existing user and all live data sit
-- on the default org 00000000-0000-0000-0000-000000000001 (slug
-- '2ndactcapital'). The settings were therefore copied onto the default org so
-- the running app actually reads its branding from the database instead of
-- silently falling back to DEFAULT_SETTINGS. The duplicate '2nd-act-capital'
-- org row is still present and should be reconciled — see the sprint report.

INSERT INTO organizations (name, slug)
VALUES ('Ripasso', 'ripasso-platform')
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2nd Act Capital branding (client #1)
-- ---------------------------------------------------------------------------
INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public)
SELECT
    o.id, v.setting_key, v.setting_value::jsonb, v.category, true
FROM organizations o
CROSS JOIN (VALUES
    -- branding — colours
    ('brand.color.navy',           '"#1B2B4B"', 'branding'),
    ('brand.color.gold',           '"#C5A880"', 'branding'),
    ('brand.color.gold_light',     '"#E8D5A3"', 'branding'),
    ('brand.color.slate_blue',     '"#9AA6BF"', 'branding'),
    ('brand.color.bg_app',         '"#FAF9F6"', 'branding'),
    ('brand.color.bg_sidebar',     '"#F5F1EB"', 'branding'),
    ('brand.color.bg_card',        '"#FFFFFF"', 'branding'),
    ('brand.color.text_primary',   '"#0F172A"', 'branding'),
    ('brand.color.text_secondary', '"#334155"', 'branding'),
    ('brand.color.text_muted',     '"#64748B"', 'branding'),
    ('brand.color.border',         '"#E2E8F0"', 'branding'),
    -- branding — identity
    ('brand.name',        '"2nd Act Capital"', 'branding'),
    ('brand.short_name',  '"2nd Act"',         'branding'),
    ('brand.logo_url',    '"/brand/wordmark/wordmark-navy-bg.svg"', 'branding'),
    ('brand.favicon_url', '"/brand/icon/favicon.svg"',              'branding'),
    -- branding — type
    ('brand.font.display', '"Spectral"',        'branding'),
    ('brand.font.body',    '"Hanken Grotesk"',  'branding'),
    -- footer
    ('footer.privacy_url',   '"/privacy"', 'footer'),
    ('footer.terms_url',     '"/terms"',   'footer'),
    ('footer.support_email', 'null',       'footer'),
    -- locale
    ('locale.base_currency', '"USD"', 'locale'),
    -- naming
    ('naming.member_label', '"Member"', 'naming'),
    ('naming.deal_label',   '"Deal"',   'naming')
) AS v(setting_key, setting_value, category)
WHERE o.id = '00000000-0000-0000-0000-000000000001'
ON CONFLICT (org_id, setting_key) DO UPDATE
    SET setting_value = EXCLUDED.setting_value,
        category      = EXCLUDED.category,
        updated_at    = now();

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
-- users.role stays free text — no CHECK constraint is added, because the
-- platform-wide role taxonomy is not finalised. Promote an operator with:
--
--   UPDATE users SET role = 'super_admin' WHERE email = '<you>';
--   UPDATE users SET role = 'org_admin'   WHERE email = '<client admin>';
