-- Agentic substrate — maker-checker rule + escalation enum wiring.
--
-- Builds the two genuinely-unblocked items from the 15-item agentic design
-- (docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md, docs/AGENTIC_SUBSTRATE_
-- DISCOVERY.md): a real review PERMISSION that makes a role eligible to
-- check a proposal, and a generic proposal table recording who MADE a
-- proposal so eligibility can be COMPUTED (permission held AND not the
-- maker) rather than stored as a fixed reviewer assignment. Item #3
-- (capability annotation on the action registry) is explicitly NOT in
-- scope — its vocabulary was never defined.
--
-- TASK 1 FINDINGS THIS SCHEMA IS BUILT ON:
--
--   1a. permissions.name convention is verb_resource (e.g. view_workflow_
--       runs, manage_org_settings), backed by (resource, action) — confirmed
--       live against all 32 real rows. No existing permission covers
--       "may check an agent's proposal" — review_agent_proposals is new.
--   1b. No generic agent-proposal table exists. Three real candidates were
--       already ruled out by the prior agenticdiscovery.lowrisk sprint
--       (docs/AGENTIC_SUBSTRATE_DISCOVERY.md Task 4): assistant_activities
--       (right shape — proposed_by/approved_by + a maker-checker CHECK —
--       but purpose-built for a member confirming their OWN assistant's
--       action, zero live rows, HELD/unwired), member_todos (the only one
--       with real data, but a flat per-user queue with no reviewer-
--       eligibility concept), workflow_run_steps (same shape as
--       assistant_activities, zero rows). None encodes "eligible to check X
--       iff holds permission Y and isn't the maker" — agent_proposals is a
--       new, minimal table for that, reusing assistant_activities'
--       proven proposed_by/reviewed_by + CHECK-constraint maker-checker
--       shape rather than inventing a new one.
--   1c. compliance_sr / compliance_jr have exactly one real code reference
--       (routers/marketplace.py's _safe_notify_roles compliance-override
--       notification list) and, confirmed live, ZERO role_permissions
--       grants and ZERO user_roles holders on both — safe to delete outright
--       once the code reference is repointed at `compliance` (done in the
--       same commit as this migration, not here — this file is schema-only).
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool —
-- asyncpg's DATABASE_URL connects as app_service, which has no CREATE on
-- public, so this file is a record of what is deployed, not itself the
-- apply path.

BEGIN;

-- ── Task 2: consolidate compliance_sr / compliance_jr into `compliance` ────
--
-- Additive-then-remove, not a migration of grants: both source roles are
-- confirmed live to have zero permissions and zero holders, so there is
-- nothing to carry over.
INSERT INTO roles (org_id, name, description)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'compliance',
    'Compliance Analyst — reviews compliance-adjacent agent proposals. '
    'Consolidated from compliance_sr/compliance_jr (agenticmakerchecker.'
    'structural): both were confirmed live to hold zero permissions and '
    'zero holders, so the split added distinction without substance.'
)
ON CONFLICT (org_id, name) DO NOTHING;

DELETE FROM roles
WHERE org_id = '00000000-0000-0000-0000-000000000001'
  AND name IN ('compliance_sr', 'compliance_jr');

-- ── Task 3: the review permission ──────────────────────────────────────────
--
-- Holding this permission is necessary, but never sufficient by itself, to
-- check a proposal — services.agent_proposals.is_eligible_reviewer also
-- requires the checker not be the row's own maker. That second half is
-- COMPUTED at check time (Task 4), never stored as a column here.
INSERT INTO permissions (name, resource, action)
VALUES ('review_agent_proposals', 'agent_proposals', 'review')
ON CONFLICT (name) DO NOTHING;

-- Granted to exactly the six real, non-empty reviewer roles the seven-agent
-- design names (Hollis maps to no role here — it never proposes, so it has
-- no reason to check anything either). Deal & SPV and Fund Admin & Billing
-- both route to fund_finance by design (routing default, not a boundary);
-- granting the permission once to that role covers both.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.org_id = '00000000-0000-0000-0000-000000000001'
  AND r.name IN (
      'support_staff', 'advisor', 'investment_committee', 'fund_finance',
      'compliance', 'org_admin'
  )
  AND p.name = 'review_agent_proposals'
ON CONFLICT DO NOTHING;

-- ── Task 4 + 5: the proposal table + the maker-checker rule + escalation ───
--
-- Generic payload + object_type shape (per agentic design item #1: "confirm
-- the proposed-state table can carry every object type an agent would
-- produce ... or whether it needs a generic payload + object_type shape" —
-- it does). No agent_key CHECK/FK: no agent-definition table exists yet
-- (that's Workflow Manager Wave 2 scope), so agent_key is documented, not
-- enforced, against the seven names in docs/PROJECT_STATUS.md.
--
-- escalation_reason uses the EXISTING enum (created earlier, verified
-- unwired until now) — nullable, because the ordinary propose-then-check
-- flow is not an escalation; it is set only when an agent run couldn't
-- complete autonomously and is handing off with a reason.
CREATE TABLE agent_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES organizations(id),
    agent_key text NOT NULL,
    object_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending',
    proposed_by uuid NOT NULL REFERENCES users(id),
    reviewed_by uuid REFERENCES users(id),
    reviewed_at timestamptz,
    review_notes text,
    escalation_reason escalation_reason,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- Mirrors assistant_activities_maker_checker_chk exactly: the database
    -- refuses a self-check independently of any application code path, so
    -- bypassing services.agent_proposals does not bypass the rule.
    CONSTRAINT agent_proposals_maker_checker_chk
        CHECK (reviewed_by IS NULL OR reviewed_by <> proposed_by)
);

CREATE INDEX agent_proposals_org_status_idx ON agent_proposals (org_id, status);

ALTER TABLE agent_proposals ENABLE ROW LEVEL SECURITY;

-- Per CLAUDE.md's platform_model_catalog lesson: a policy for every
-- operation this table will ever take, not just the ones this sprint
-- exercises.
CREATE POLICY agent_proposals_select ON agent_proposals
    FOR SELECT USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE POLICY agent_proposals_insert ON agent_proposals
    FOR INSERT WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE POLICY agent_proposals_update ON agent_proposals
    FOR UPDATE USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    ) WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

CREATE POLICY agent_proposals_delete ON agent_proposals
    FOR DELETE USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON agent_proposals TO app_service;

COMMIT;
