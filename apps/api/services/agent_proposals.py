"""Agentic substrate — maker-checker rule (agenticmakerchecker.structural).

Builds the two genuinely-unblocked items from the 15-item agentic design
(``docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md``): a real review permission
that makes a role eligible to check a proposal, and a generic
``agent_proposals`` table recording who MADE a proposal. Eligibility to
CHECK a proposal is deliberately never stored — it is computed here, every
time, as: holds ``REVIEW_PERMISSION`` AND is not the proposal's own maker.

This is substrate, not a running agent: no agent-run table exists yet
(Workflow Manager Wave 2, which would give an agent a workflow instance to
execute as, is unbuilt), so nothing calls ``create_proposal`` from a live
agent today. The seven agents and their routing-default reviewer role
(display only — never a constraint on who may actually check) are:

    Document & Custodial Ops -> support_staff
    Portfolio & Suitability  -> advisor
    Deal & SPV               -> investment_committee
    Fund Admin & Billing     -> fund_finance
    Compliance Analyst       -> compliance
    Authoring                -> org_admin
    Hollis                   -> none, read-only, never proposes

The reviewer roles above are themselves confirmed empty of both permissions
and holders except advisor/support_staff/org_admin — this mechanism is
UNEXERCISED in production until staffing catches up. See
``docs/PROJECT_STATUS.md`` for the live accounting.
"""
from __future__ import annotations

import json
from typing import Any

from services.rbac import get_user_permissions, is_super_admin, load_principal

REVIEW_PERMISSION = "review_agent_proposals"

# Documented, not enforced (no agent_key CHECK/FK — no agent-definition table
# exists yet). Kept here so a future agent-registry table has one obvious
# place to reconcile against.
AGENT_KEYS = (
    "document_custodial_ops",
    "portfolio_suitability",
    "deal_spv",
    "fund_admin_billing",
    "compliance_analyst",
    "authoring",
    "hollis",
)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


class MakerCheckerError(Exception):
    """Raised when a proposal's own maker attempts to check it.

    The database refuses this independently
    (``agent_proposals_maker_checker_chk``), so bypassing this function does
    not bypass the rule — this exists to give a clear, specific error before
    that CHECK constraint would otherwise raise a generic asyncpg error.
    """

    def __init__(self, message: str, *, proposal_id: str, user_id: str):
        super().__init__(message)
        self.proposal_id = proposal_id
        self.user_id = user_id


class NotEligibleError(Exception):
    """Raised when the checker does not hold ``REVIEW_PERMISSION``."""

    def __init__(self, message: str, *, proposal_id: str, user_id: str):
        super().__init__(message)
        self.proposal_id = proposal_id
        self.user_id = user_id


async def create_proposal(
    conn,
    org_id: str,
    *,
    agent_key: str,
    object_type: str,
    payload: dict[str, Any],
    proposed_by: str,
    escalation_reason: str | None = None,
) -> str:
    """Record an agent's proposed output. Always lands in Tier-1 — never a
    domain table — per the agents-propose/code-disposes platform invariant.

    ``payload`` is JSON-encoded before binding: this pool registers no jsonb
    codec (confirmed against ``services.database`` and every other jsonb
    writer in this codebase, e.g. ``services.domain_events.encode_payload``),
    so asyncpg needs a string for a ``::jsonb`` parameter, never a raw dict.
    """
    return await conn.fetchval(
        """
        INSERT INTO agent_proposals
            (org_id, agent_key, object_type, payload, proposed_by, escalation_reason)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        RETURNING id::text
        """,
        org_id, agent_key, object_type, json.dumps(payload or {}), proposed_by, escalation_reason,
    )


async def is_eligible_reviewer(pool, org_id, user_id, maker_id) -> bool:
    """True iff ``user_id`` may check a proposal made by ``maker_id`` in
    ``org_id``: holds ``REVIEW_PERMISSION`` (or is super_admin) AND is not
    the maker.

    Deliberately does NOT reuse ``rbac.has_permission`` — that helper
    default-allows a user with zero role rows at all (single-admin
    safety, documented on ``has_permission`` itself), which is the wrong
    posture here: "a user without the permission is refused regardless" is
    a hard requirement of the maker-checker rule, not a bootstrap
    convenience. This composes the same underlying primitives
    (``load_principal``, ``is_super_admin``, ``get_user_permissions``)
    rather than re-implementing the permission lookup.

    Assumes the ambient RLS context on ``pool`` is already the CANDIDATE
    reviewer's own (org_id + is_super_admin) — the same precondition every
    other ``services.rbac`` check makes; it does not switch context itself.
    """
    if str(user_id) == str(maker_id):
        return False
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    if principal is None:
        return False
    if is_super_admin(principal):
        return True
    if principal["org_id"] != str(org_id):
        return False
    perms = await get_user_permissions(pool, user_id, org_id)
    return REVIEW_PERMISSION in perms


async def review_proposal(
    pool,
    conn,
    org_id: str,
    proposal_id: str,
    *,
    reviewed_by: str,
    decision: str,
    review_notes: str | None = None,
) -> dict[str, Any]:
    """Close out a proposal — approve or reject — in one statement.

    Refuses (Python-level, before the UPDATE runs) when the reviewer is the
    maker or lacks ``REVIEW_PERMISSION``; the database's own CHECK
    constraint is the backstop for the first of those two, not the only
    line of defense.
    """
    if decision not in (STATUS_APPROVED, STATUS_REJECTED):
        raise ValueError(f"decision must be {STATUS_APPROVED!r} or {STATUS_REJECTED!r}")

    proposal = await conn.fetchrow(
        "SELECT id::text AS id, proposed_by::text AS proposed_by, status "
        "FROM agent_proposals WHERE id = $1::uuid AND org_id = $2::uuid",
        proposal_id, org_id,
    )
    if proposal is None:
        raise ValueError(f"agent_proposals row {proposal_id} not found in org {org_id}")

    if str(proposal["proposed_by"]) == str(reviewed_by):
        raise MakerCheckerError(
            f"user {reviewed_by} made proposal {proposal_id} and cannot also check it",
            proposal_id=proposal_id, user_id=str(reviewed_by),
        )
    if not await is_eligible_reviewer(pool, org_id, reviewed_by, proposal["proposed_by"]):
        raise NotEligibleError(
            f"user {reviewed_by} does not hold {REVIEW_PERMISSION!r}",
            proposal_id=proposal_id, user_id=str(reviewed_by),
        )

    row = await conn.fetchrow(
        """
        UPDATE agent_proposals
        SET status = $3, reviewed_by = $4::uuid, reviewed_at = now(), review_notes = $5
        WHERE id = $1::uuid AND org_id = $2::uuid
        RETURNING id::text AS id, status, reviewed_by::text AS reviewed_by,
                  proposed_by::text AS proposed_by, reviewed_at
        """,
        proposal_id, org_id, decision, reviewed_by, review_notes,
    )
    return dict(row)
