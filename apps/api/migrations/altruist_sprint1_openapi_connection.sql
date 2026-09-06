-- Altruist Sprint 1 — Open API connection scaffold
--
-- Two tables:
--   altruist_oauth_states  — short-lived CSRF state for the authorization-code
--                            redirect round-trip (single-use, TTL-bounded).
--   altruist_connections   — per-org, per-environment (sandbox/production)
--                            OAuth2 credential + token storage.
--
-- Conventions matched to the live schema (confirmed via docs/schema_snapshot.sql
-- and pg_policies against account_groups / account_group_members / portfolio.tax_lots,
-- Sprint 1 Task 1b):
--   * public.* tables default id to gen_random_uuid() (portfolio.* uses
--     uuid_generate_v4() instead — not used here since this is a public.* table).
--   * RLS policy naming: <table>_org_isolation, USING/WITH CHECK
--     (org_id = (nullif(current_setting('app.current_org_id', true), ''))::uuid
--      OR current_setting('app.is_super_admin', true) = 'true')
--   * Full bi-temporal columns (created_at, updated_at, valid_from, valid_to,
--     system_from, system_to) mirroring account_groups.
--
-- Bi-temporal axis choice for altruist_connections (Rule 3): nothing references
-- this table's id via FK yet, so valid-axis restatement would be the default per
-- CLAUDE.md. The sprint prompt explicitly specifies a partial-unique index on
-- (org_id, environment) WHERE system_to IS NULL, i.e. the system-time axis, for
-- a genuine reconnect/re-auth (a new physical credential set supersedes the old
-- row). Ordinary hourly token refresh does NOT create a new bi-temporal row
-- (impractical to version every hourly refresh) — it updates
-- access_token_encrypted / refresh_token_encrypted / token_expires_at /
-- last_refreshed_at IN PLACE on the current row, the same update-in-place
-- precedent already used for users.last_login_at. [FIND, recorded again in the
-- sprint log] Only a genuine reconnect (new client credentials, or moving from
-- needs_reauth back to connected via a fresh authorization-code grant) archives
-- the old row (system_to = now()) and inserts a new one.

CREATE TABLE IF NOT EXISTS altruist_oauth_states (
    id           uuid NOT NULL DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(id),
    environment  text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    state        text NOT NULL,
    initiated_by uuid,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    used_at      timestamptz,
    CONSTRAINT altruist_oauth_states_pkey PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS altruist_oauth_states_state_unique
    ON altruist_oauth_states (state);

ALTER TABLE altruist_oauth_states ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS altruist_oauth_states_org_isolation ON altruist_oauth_states;
CREATE POLICY altruist_oauth_states_org_isolation ON altruist_oauth_states
    USING (
        org_id = (nullif(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = (nullif(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE TABLE IF NOT EXISTS altruist_connections (
    id                       uuid NOT NULL DEFAULT gen_random_uuid(),
    org_id                   uuid NOT NULL REFERENCES organizations(id),
    environment              text NOT NULL CHECK (environment IN ('sandbox', 'production')),
    status                   text NOT NULL DEFAULT 'connected'
                                 CHECK (status IN ('connected', 'needs_reauth', 'disconnected')),
    client_id                text NOT NULL,
    -- Encrypted at the application layer (Fernet, see services/altruist_oauth.py)
    -- — never plaintext in Postgres, even transiently. *_last4 is a redaction-safe
    -- reference for audit/display only, never enough to reconstruct the secret.
    client_secret_encrypted  bytea NOT NULL,
    client_secret_last4      text,
    access_token_encrypted   bytea,
    access_token_last4       text,
    refresh_token_encrypted  bytea NOT NULL,
    refresh_token_last4      text,
    scope                    text,
    token_expires_at         timestamptz,
    last_refreshed_at        timestamptz,
    connected_by             uuid,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    valid_from               timestamptz NOT NULL DEFAULT now(),
    valid_to                 timestamptz,
    system_from              timestamptz NOT NULL DEFAULT now(),
    system_to                timestamptz,
    CONSTRAINT altruist_connections_pkey PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS altruist_connections_active_unique
    ON altruist_connections (org_id, environment)
    WHERE system_to IS NULL;

ALTER TABLE altruist_connections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS altruist_connections_org_isolation ON altruist_connections;
CREATE POLICY altruist_connections_org_isolation ON altruist_connections
    USING (
        org_id = (nullif(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = (nullif(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );
