-- Self-approval under a genuinely empty checker set (selfapproval.structural).
--
-- THE PROBLEM: agenticmakerchecker.structural's maker-checker rule (a user
-- may check a proposal iff they hold review_agent_proposals AND are not its
-- own maker) is correct, but it makes a single-privileged-user org UNABLE TO
-- EVER APPROVE ANYTHING once the maker IS that org's only holder of
-- review_agent_proposals: excluding the maker leaves zero eligible
-- checkers, and the proposal can never be routed.
--
-- THE DECISION (already made, not re-litigated here): ALLOW WITH
-- DISCLOSURE. When the eligible-checker set is genuinely empty after
-- excluding the maker, permit the self-approval and RECORD it as one —
-- never silently. Escalating to org_admin was rejected (in a lean org that
-- is frequently the same human wearing a second hat — theatrical
-- separation, not real separation of duties). Blocking outright was
-- rejected as unusable for a single-advisor tenant.
--
-- THE SHAPE: agent_proposals_maker_checker_chk goes from an unconditional
-- refusal to a CONDITIONAL one — a raw UPDATE setting
-- reviewed_by = proposed_by still fails UNLESS the row also carries
-- self_approved = true AND a non-null self_approval_reason. The guarantee
-- (a self-check can never slip through silently, even bypassing
-- services.agent_proposals entirely) survives; only the deliberate,
-- disclosed case opens. The emptiness computation itself
-- (services.agent_proposals.has_other_eligible_checker) is an
-- application-layer gate, not something this CHECK constraint can express —
-- the constraint's job is only to make sure the disclosure fields are
-- genuinely set whenever a self-check is recorded, never to decide whether
-- self-approval was actually warranted.
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool —
-- asyncpg's DATABASE_URL connects as app_service, which has no CREATE on
-- public, so this file is a record of what is deployed, not itself the
-- apply path.

BEGIN;

ALTER TABLE agent_proposals
    ADD COLUMN self_approved boolean NOT NULL DEFAULT false,
    ADD COLUMN self_approval_reason text;

ALTER TABLE agent_proposals
    DROP CONSTRAINT agent_proposals_maker_checker_chk;

ALTER TABLE agent_proposals
    ADD CONSTRAINT agent_proposals_maker_checker_chk
    CHECK (
        reviewed_by IS NULL
        OR reviewed_by <> proposed_by
        OR (self_approved = true AND self_approval_reason IS NOT NULL)
    );

COMMIT;
