-- LiteLLM seed-chain fix (litellmseedfix.structural) — realign
-- platform_model_catalog.model_id (and org_settings.DEFAULT_SETTINGS,
-- services/org_settings.py, a code change not a migration) onto the live
-- hollisworks-litellm proxy's REGISTERED DEPLOYMENT `model_name` values,
-- not the upstream provider's dated/versioned model id.
--
-- THE BUG: 'claude-sonnet-4-6' and 'claude-haiku-4-5-20251001' were never
-- registered deployment names on the proxy — only 'claude-sonnet' was (and
-- 'voyage-3.5', which already matched by coincidence). Any AI call that fell
-- through to the seeded default chain (none ever did in production — every
-- real call site passed an explicit model= override) would have received
-- LiteLLM's own "Invalid model name" HTTP 400. A 'claude-haiku' deployment
-- did not exist AT ALL prior to this sprint.
--
-- THE FIX (two parts, this file is the data half):
--   1. A NEW 'claude-haiku' deployment was registered live on the proxy via
--      POST /model/new (litellm_params.model =
--      'anthropic/claude-haiku-4-5-20251001', api_key =
--      'os.environ/ANTHROPIC_API_KEY' — the same env-var-indirection shape
--      the pre-existing 'claude-sonnet' deployment uses). Kept on Haiku
--      rather than collapsed onto Sonnet: Haiku is ~20x cheaper per token
--      and document_classifier/default are high-volume call paths — see
--      org_settings.py DEFAULT_SETTINGS' own comment for the full
--      justification.
--   2. platform_model_catalog's two Anthropic rows are renamed here from the
--      upstream id to the deployment name, so the catalog (the D2 picker),
--      org_settings (the resolver's actual seed values) and the live proxy
--      all agree on the same identifier space. org_model_selections had
--      ZERO rows at the time of this migration (confirmed live) so a plain
--      DELETE+INSERT is safe — no FK-referencing row needed to move with it.
--
-- Applied live via direct asyncpg as app_service (DML only — no DDL, no
-- schema change; app_service already holds INSERT/DELETE on this table from
-- the original litellmphased2_model_catalog.sql GRANT). This file is a
-- record of what is deployed, not itself the apply path — same convention
-- litellmphased2_model_catalog.sql's own header states.

BEGIN;

DELETE FROM platform_model_catalog
WHERE model_id IN ('claude-sonnet-4-6', 'claude-haiku-4-5-20251001');

INSERT INTO platform_model_catalog (model_id, display_name, provider) VALUES
    ('claude-sonnet', 'Claude Sonnet', 'anthropic'),
    ('claude-haiku', 'Claude Haiku', 'anthropic');

-- voyage-3.5 already matched the proxy's registered deployment name and is
-- left untouched.

COMMIT;
