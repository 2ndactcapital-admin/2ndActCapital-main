-- LiteLLM Phase F — Hollisworks-only force-Anthropic emergency bypass.
--
-- platform_ai_controls: a genuinely PLATFORM-SCOPED table (CLAUDE.md Rule 6 —
-- no org_id column at all, same convention as platform_model_catalog:
-- "the table has no org axis" IS the platform scope, since org_settings
-- itself has no owner_scope column and no platform-scope row is possible
-- there — see services/litellm_credentials.py's own docstring note on this).
-- One row per named control; today only 'force_anthropic_bypass' exists.
-- Reads are open to EVERY caller (USING (true)) because every single AI text
-- call site must read this at call time regardless of who is calling
-- (background jobs, cron, an ordinary org member's request) — the org-level
-- restriction is not relevant here, there is no org-level axis to restrict.
-- Writes are super_admin only, enforced BOTH at the RLS layer (defense in
-- depth) AND the app layer (services.platform_ai_controls / the router).
--
-- Per CLAUDE.md's "RLS policies are per-OPERATION" lesson (platform_model_
-- catalog shipped with 3 policies and its first real UPDATE silently matched
-- zero rows): all four operations get a policy now, even though only
-- SELECT/UPDATE are used by this sprint's own code — INSERT/DELETE exist so
-- a future control row, or a future teardown, is never a second silent gap.
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool
-- (litellmphasef sprint) — asyncpg's DATABASE_URL connects as app_service,
-- which has no CREATE on public, so this file is a record of what is
-- deployed, not itself the apply path.

BEGIN;

CREATE TABLE platform_ai_controls (
    key text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid
);

ALTER TABLE platform_ai_controls ENABLE ROW LEVEL SECURITY;

CREATE POLICY platform_ai_controls_read ON platform_ai_controls
    FOR SELECT USING (true);

CREATE POLICY platform_ai_controls_insert ON platform_ai_controls
    FOR INSERT WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY platform_ai_controls_update ON platform_ai_controls
    FOR UPDATE USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY platform_ai_controls_delete ON platform_ai_controls
    FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON platform_ai_controls TO app_service;

-- Default OFF — no org's behaviour changes until a super_admin deliberately
-- flips this.
INSERT INTO platform_ai_controls (key, enabled) VALUES ('force_anthropic_bypass', false);

-- ai_decision_log observability (Task 3): while LiteLLM is bypassed (for ANY
-- reason — the pre-existing LITELLM_ROUTING_DISABLED env var, an
-- unconfigured proxy, or this sprint's new platform toggle), LiteLLM's own
-- spend log never sees the call. ai_decision_log is the one record that
-- survives independently of LiteLLM, so every row now says whether ITS OWN
-- call was LiteLLM-routed, not just what TaskRouter decided. Two columns,
-- not a schema-breaking rework of the 12-column shape Phase B/D1b/D2 all
-- deliberately preserved — both default to the pre-Phase-F value (false /
-- NULL) so every existing row, and every INSERT that does not thread the
-- new kwargs (document_embedding.py's pre-Phase-F call sites), is
-- unaffected.
ALTER TABLE ai_decision_log
    ADD COLUMN litellm_bypassed boolean NOT NULL DEFAULT false,
    ADD COLUMN bypass_reason text;

COMMIT;
