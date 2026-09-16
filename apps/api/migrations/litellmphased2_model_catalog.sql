-- LiteLLM Phase D2 — model pick-list.
--
-- platform_model_catalog: Hollisworks-curated, platform-wide (no org_id — this
-- IS the platform scope, per CLAUDE.md Rule 6's owner_scope convention, mirrored
-- here as "no org_id column at all" since every row is unconditionally platform,
-- never per-org). Writes restricted to super_admin at the RLS layer (defense in
-- depth) AND the app layer (services.model_catalog).
--
-- org_model_selections: which of the curated models one org has authorised.
-- Row PRESENCE = authorised; there is no separate active/inactive flag — an
-- org admin removing a model deletes the row (Rule 3 bi-temporal restatement
-- does not apply here, same precedent as user_roles/role_permissions: a plain
-- grant table, not a valued-history table).
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool
-- (litellmphased2 sprint) — asyncpg's DATABASE_URL/APP_SERVICE_DATABASE_URL
-- both connect as app_service, which has no CREATE on public (confirmed live),
-- so this file is a record of what is deployed, not itself the apply path.

BEGIN;

CREATE TABLE platform_model_catalog (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id text NOT NULL UNIQUE,
    display_name text NOT NULL,
    provider text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid
);

ALTER TABLE platform_model_catalog ENABLE ROW LEVEL SECURITY;

-- Every org member reads the SAME curated list (it is not org-scoped) — the
-- org-level restriction lives one table over, in org_model_selections.
CREATE POLICY platform_model_catalog_read ON platform_model_catalog
    FOR SELECT USING (true);

CREATE POLICY platform_model_catalog_insert ON platform_model_catalog
    FOR INSERT WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY platform_model_catalog_delete ON platform_model_catalog
    FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');

CREATE TABLE org_model_selections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES organizations(id),
    model_id text NOT NULL REFERENCES platform_model_catalog(model_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid,
    UNIQUE (org_id, model_id)
);

ALTER TABLE org_model_selections ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_model_selections_isolation ON org_model_selections
    FOR ALL USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON platform_model_catalog TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON org_model_selections TO app_service;

-- Seed the real, currently-in-use model strings (org_settings.DEFAULT_SETTINGS
-- + the only non-default row any org has ever written — see
-- docs/LITELLM_PHASE_D_DISCOVERY.md Task 3c) — never a fictitious model.
INSERT INTO platform_model_catalog (model_id, display_name, provider) VALUES
    ('claude-sonnet-4-6', 'Claude Sonnet', 'anthropic'),
    ('claude-haiku-4-5-20251001', 'Claude Haiku', 'anthropic'),
    ('voyage-3.5', 'Voyage 3.5', 'voyage');

COMMIT;
