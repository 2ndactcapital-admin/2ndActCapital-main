-- tiergating.structural: link a suspended Service Task to the agent_proposals
-- row created to gate it.
--
-- THE GAP THIS CLOSES (docs/WORKFLOW_WAVE2_DISCOVERY.md Task 5): the engine
-- read workflow_steps.autonomy_tier but never branched on it for a Service
-- Task — a Tier-1 verb invoked from a Service Task executed unattended, the
-- same instant a Tier-3 verb would. Suspension reuses agent_proposals for
-- maker-checker eligibility (holds review_agent_proposals AND is not the
-- maker, or disclosed self-approval under a genuinely empty checker set) —
-- see services.workflow_engine and services.agent_proposals. This column is
-- the ONLY schema change tiergating.structural needs: workflow_runs.status
-- and workflow_run_steps.status are both plain text with no CHECK constraint
-- (confirmed live), so the new values this sprint introduces
-- ('awaiting_approval', 'suspended', 'rejected') need no migration.
--
-- Nullable: every existing row, and every non-Tier-1 step (the large
-- majority), never suspends and so never gets one.
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool —
-- asyncpg's DATABASE_URL connects as app_service, which has no CREATE on
-- public, so this file is a record of what is deployed, not itself the
-- apply path.

BEGIN;

ALTER TABLE workflow_run_steps
    ADD COLUMN agent_proposal_id uuid REFERENCES agent_proposals(id);

CREATE INDEX workflow_run_steps_agent_proposal_id_idx
    ON workflow_run_steps (agent_proposal_id)
    WHERE agent_proposal_id IS NOT NULL;

COMMIT;
