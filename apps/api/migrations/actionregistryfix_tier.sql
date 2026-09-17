-- actionregistryfix.structural — tier column, drop default_autonomy.
--
-- default_autonomy correlated 1:1 with access_type across all 16 rows (every
-- read 'auto', every write 'confirm') and never encoded a real decision —
-- dropped rather than kept as dead weight a future reader might trust.
--
-- tier is the stakes class established by services.action_registry's
-- AssistantAction.tier docstring: 1 = highest stakes (moves money/creates an
-- obligation, mutates ownership/economic terms/a posted ledger line), 2 =
-- write but none of those three tests fire, 3 = lowest stakes (reads, plus
-- propose() whose real gate is the downstream reviewer). Backfilled here to
-- match the values services.assistant_actions' register_all() sets in
-- source, so REGISTRY.sync_catalog is a no-op confirmation on next run, not
-- a silent overwrite.
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool —
-- asyncpg's DATABASE_URL connects as app_service, which has no ALTER on
-- public, so this file is a record of what is deployed, not itself the
-- apply path.

BEGIN;

ALTER TABLE assistant_action_catalog ADD COLUMN tier integer;

UPDATE assistant_action_catalog SET tier = 1
WHERE action_key IN (
    'spv.subscribe', 'spv.record_transaction', 'entity.link_ownership',
    'spv_carry.propose_from_realization'
);

UPDATE assistant_action_catalog SET tier = 2
WHERE action_key IN ('crm.draft_note', 'litellm.reload_model_cost_map');

UPDATE assistant_action_catalog SET tier = 3
WHERE access_type = 'read';

ALTER TABLE assistant_action_catalog
    ALTER COLUMN tier SET NOT NULL,
    ADD CONSTRAINT assistant_action_catalog_tier_chk CHECK (tier IN (1, 2, 3));

ALTER TABLE assistant_action_catalog DROP COLUMN default_autonomy;

COMMIT;
