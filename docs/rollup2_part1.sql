-- ROLLUP2 Task 3 — the fund category → taxonomy key map (Super Admin overrides over the code default
-- services.fund_classification.DEFAULT_CATEGORY_MAP). PLATFORM scope: org_id is always NULL, owner_scope 'platform'
-- (CLAUDE.md Rule 6). Valid-axis versions (Rule 3): nothing references an id, so an edit closes the live row
-- (valid_to = now()) and inserts the new one. Ships EMPTY — the sprint writes no row.
-- RLS: every operation has a policy (CLAUDE.md "RLS: row not found"): read for any connection (platform reference
-- data, like security_reference_attributes' org_id IS NULL read), write for Super Admin only.

CREATE TABLE IF NOT EXISTS portfolio.fund_category_taxonomy (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_key  text NOT NULL,
    category      text NOT NULL,
    taxonomy_key  text,                                   -- NULL = deliberately no bucket (stays Unclassified)
    note          text,
    owner_scope   text NOT NULL DEFAULT 'platform',
    org_id        uuid,
    created_by    uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    valid_from    timestamptz NOT NULL DEFAULT now(),
    valid_to      timestamptz,
    system_from   timestamptz NOT NULL DEFAULT now(),
    system_to     timestamptz,
    CONSTRAINT fund_category_taxonomy_platform CHECK (owner_scope = 'platform' AND org_id IS NULL),
    CONSTRAINT fund_category_taxonomy_category_nonblank CHECK (btrim(category) <> '' AND btrim(provider_key) <> ''),
    CONSTRAINT fund_category_taxonomy_key_shape CHECK (taxonomy_key IS NULL OR taxonomy_key ~ '^taxonomy_(sc|mc|sub)_[0-9_]+$')
);

CREATE UNIQUE INDEX IF NOT EXISTS fund_category_taxonomy_live_uq
    ON portfolio.fund_category_taxonomy (provider_key, lower(category))
    WHERE valid_to IS NULL AND system_to IS NULL;

ALTER TABLE portfolio.fund_category_taxonomy ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio.fund_category_taxonomy FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fund_category_taxonomy_read ON portfolio.fund_category_taxonomy;
CREATE POLICY fund_category_taxonomy_read ON portfolio.fund_category_taxonomy FOR SELECT
    USING (org_id IS NULL OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS fund_category_taxonomy_super_admin_insert ON portfolio.fund_category_taxonomy;
CREATE POLICY fund_category_taxonomy_super_admin_insert ON portfolio.fund_category_taxonomy FOR INSERT
    WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS fund_category_taxonomy_super_admin_update ON portfolio.fund_category_taxonomy;
CREATE POLICY fund_category_taxonomy_super_admin_update ON portfolio.fund_category_taxonomy FOR UPDATE
    USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
    WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS fund_category_taxonomy_super_admin_delete ON portfolio.fund_category_taxonomy;
CREATE POLICY fund_category_taxonomy_super_admin_delete ON portfolio.fund_category_taxonomy FOR DELETE
    USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.fund_category_taxonomy TO app_service;
