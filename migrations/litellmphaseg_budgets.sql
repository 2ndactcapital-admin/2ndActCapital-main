-- LiteLLM Phase G — spend budgets: per-org monthly cap + a separate
-- Hollisworks-wide ceiling.
--
-- TASK 1 FINDINGS THIS SCHEMA IS BUILT ON (see docs/LITELLM_INTEGRATION_
-- DESIGN_V1.md §8 for the full writeup):
--
--   1a. LiteLLM's native max_budget (on /key/generate and /team/new) is a
--       real, live, CORE (non-Enterprise) feature — confirmed by creating
--       and deleting a real budgeted key and a real budgeted team against
--       the live proxy. It does NOT map onto this platform's architecture:
--       every real AI call (platform-sourced OR an org's own BYOK
--       deployment) authenticates to LiteLLM with the ONE shared
--       LITELLM_MASTER_KEY (D1a/D1b's own finding — org isolation is by
--       DEPLOYMENT, never by which key calls it), so there is no per-org
--       key or team on the wire to attach a native budget to. And even
--       where it COULD attach (the master key itself, for an aggregate
--       cap), LiteLLM's native at-cap behaviour is a hard rejection, not a
--       degrade — the wrong shape for this sprint's explicit "degrade,
--       never hard-stop" requirement regardless. Native budgets are
--       therefore NOT used anywhere in this design.
--   1b. Spend is cached in these application tables, refreshed from
--       LiteLLM's admin API on a schedule
--       (apps/api/scripts/sync_ai_spend.py, meant for a Render Cron Job —
--       not yet wired to Render, the same real, documented gap the
--       workflow scheduler's own tick has). The AI call path
--       (services.extraction._execute_chain) NEVER calls LiteLLM's admin
--       API itself — it only reads these cached numbers (services.
--       ai_budgets.is_org_over_cap / is_platform_over_ceiling, both
--       fail-open on any error). Staleness is bounded by however often the
--       sync job actually runs (target: 5 minutes) plus LiteLLM's own
--       spend-log flush lag (seconds, per design doc §14.1). Accepted
--       deliberately: a soft billing-threshold control tolerates being
--       several minutes behind reality far better than a synchronous
--       admin-API round trip on every single AI call would tolerate the
--       added latency and the new failure mode (what happens to an AI call
--       when LiteLLM's admin API is slow or down?).
--   1c. GET /global/spend/tags?start_date=X&end_date=Y (confirmed live, a
--       real CORE endpoint) returns spend aggregated PER TAG over a date
--       range — {"spend_per_tag": [{"name": "org:<uuid>", "spend": ...,
--       "log_count": ...}, ...]} — so D1b's attribution tags (org:<uuid>,
--       usage:hollisworks_platform, usage:platform_on_behalf_of_org) are
--       genuinely queryable in AGGREGATE, not merely per-row.
--       (/global/spend/report, LiteLLM's other aggregation endpoint, is
--       Enterprise-only on this self-hosted OSS instance — confirmed live,
--       HTTP 400 "You must be a LiteLLM Enterprise user".)
--   1d. Per-org budget CONFIG lives in org_settings (ai.budget.monthly_usd /
--       ai.budget.warning_pct) — the same per-org config convention every
--       other ai.* key already uses, which gets org_admin-can-write /
--       plain-member-403 for free from the EXISTING generic settings PUT
--       and manage_org_settings gate — no new write endpoint needed. The
--       Hollisworks-wide ceiling is genuinely platform-scoped (no org
--       axis), so it extends platform_ai_controls (Phase F's established
--       home for platform-scoped AI controls) with a second row, rather
--       than a third new table, for its config.
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool —
-- asyncpg's DATABASE_URL connects as app_service, which has no CREATE on
-- public, so this file is a record of what is deployed, not itself the
-- apply path.

BEGIN;

-- Generalize platform_ai_controls beyond a pure on/off flag (Phase F's
-- force_anthropic_bypass only ever needed `enabled`). These new columns are
-- NULL for that pre-existing row and stay unused by it — this table now
-- holds two conceptually different controls, and that is fine: "no org_id
-- column at all" is what makes a row platform-scoped, not any one column
-- being populated by every row.
ALTER TABLE platform_ai_controls
    ADD COLUMN numeric_value numeric(12,2),
    ADD COLUMN warning_pct numeric(5,2),
    ADD COLUMN period_start date,
    ADD COLUMN cached_spend_usd numeric(14,4),
    ADD COLUMN cache_updated_at timestamptz,
    ADD COLUMN warning_alerted_at timestamptz,
    ADD COLUMN cap_alerted_at timestamptz;

-- Default: no ceiling. `enabled = false` and `numeric_value IS NULL` both
-- say the same thing here (no ceiling configured) — nothing changes for the
-- platform until a super_admin deliberately sets one, matching the per-org
-- "no budget" default below exactly.
INSERT INTO platform_ai_controls (key, enabled) VALUES ('hollisworks_spend_ceiling', false);

-- Per-org cached spend for the CURRENT period only (one row per org — this
-- is a CACHE, not a ledger; LiteLLM's own spend log is the ledger). RLS: an
-- org may read/write only its OWN row (self-caching, when an org's own AI
-- call path opportunistically reads it), OR is_super_admin (the periodic
-- sync job, running under services.database.platform_scope, refreshing
-- every org in one pass). Per CLAUDE.md's "every operation needs its own
-- policy" lesson (platform_model_catalog's missing-UPDATE trap): all four
-- operations get a real policy.
CREATE TABLE org_ai_spend_cache (
    org_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    period_start date NOT NULL,
    spend_usd numeric(14,4) NOT NULL DEFAULT 0,
    cache_updated_at timestamptz NOT NULL DEFAULT now(),
    warning_alerted_at timestamptz,
    cap_alerted_at timestamptz
);

ALTER TABLE org_ai_spend_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_ai_spend_cache_select ON org_ai_spend_cache
    FOR SELECT USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE POLICY org_ai_spend_cache_insert ON org_ai_spend_cache
    FOR INSERT WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE POLICY org_ai_spend_cache_update ON org_ai_spend_cache
    FOR UPDATE USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    ) WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE POLICY org_ai_spend_cache_delete ON org_ai_spend_cache
    FOR DELETE USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON org_ai_spend_cache TO app_service;

COMMIT;
