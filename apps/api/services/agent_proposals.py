"""Agentic substrate — maker-checker rule (agenticmakerchecker.structural),
plus disclosed self-approval under a genuinely empty checker set
(selfapproval.structural).

Builds the two genuinely-unblocked items from the 15-item agentic design
(``docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md``): a real review permission
that makes a role eligible to check a proposal, and a generic
``agent_proposals`` table recording who MADE a proposal. Eligibility to
CHECK a proposal is deliberately never stored — it is computed here, every
time, as: holds ``REVIEW_PERMISSION`` AND is not the proposal's own maker.

**selfapproval.structural** closes the resulting gap: excluding the maker
from `review_agent_proposals` holders can leave an EMPTY eligible-checker
set (a single-advisor org is the real, current case — see
``has_other_eligible_checker``), which would otherwise make a proposal
un-routable forever. The fix is ALLOW WITH DISCLOSURE, not escalation and
not an outright block: when no OTHER eligible checker exists in the
proposal's own org, the maker may check their own proposal, and the row
records ``self_approved = true`` plus a required ``self_approval_reason``.
This is gated on a COMPUTED emptiness check, never a caller-supplied flag —
a maker with an available reviewer is refused exactly as before. The
database backstops the disclosure half independently
(``agent_proposals_maker_checker_chk``): a raw UPDATE setting
``reviewed_by = proposed_by`` still fails unless the row also carries
``self_approved = true`` and a non-null ``self_approval_reason``.

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


class SelfApprovalReasonRequiredError(Exception):
    """Raised when a genuinely-empty-checker self-approval omits a reason.

    Reaching this branch already means: the reviewer holds
    ``REVIEW_PERMISSION`` and IS the maker, and no other eligible checker
    exists in this org — self-approval is legitimately available, but a
    reason is mandatory. The row is never written silently.
    """

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


async def _holds_review_permission(pool, user_id, org_id) -> bool:
    """True iff ``user_id`` holds ``REVIEW_PERMISSION`` (or is super_admin),
    with no opinion on whether they are also the proposal's maker.

    Extracted from ``is_eligible_reviewer`` so the self-approval path in
    ``review_proposal`` can require the SAME "holds the permission" half
    without also requiring "is not the maker" — self-approval relaxes only
    the latter, never the former (a maker who does not hold
    ``REVIEW_PERMISSION`` at all is refused regardless of how empty the
    checker set is).

    Deliberately does NOT reuse ``rbac.has_permission`` — that helper
    default-allows a user with zero role rows at all (single-admin
    safety, documented on ``has_permission`` itself), which is the wrong
    posture here: "a user without the permission is refused regardless" is
    a hard requirement of the maker-checker rule, not a bootstrap
    convenience. This composes the same underlying primitives
    (``load_principal``, ``is_super_admin``, ``get_user_permissions``)
    rather than re-implementing the permission lookup.
    """
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


async def is_eligible_reviewer(pool, org_id, user_id, maker_id) -> bool:
    """True iff ``user_id`` may check a proposal made by ``maker_id`` in
    ``org_id``: holds ``REVIEW_PERMISSION`` (or is super_admin) AND is not
    the maker.

    Assumes the ambient RLS context on ``pool`` is already the CANDIDATE
    reviewer's own (org_id + is_super_admin) — the same precondition every
    other ``services.rbac`` check makes; it does not switch context itself.
    """
    if str(user_id) == str(maker_id):
        return False
    return await _holds_review_permission(pool, user_id, org_id)


async def has_other_eligible_checker(pool, org_id, maker_id) -> bool:
    """True iff some user OTHER than ``maker_id`` holds ``REVIEW_PERMISSION``
    within ``org_id`` — the emptiness gate ``review_proposal`` uses to decide
    whether self-approval is even reachable.

    Deliberately scoped to ORG-level `review_agent_proposals` holders only —
    NOT ``is_super_admin`` — even though ``is_eligible_reviewer`` itself
    treats super_admin as universally eligible to check any org's proposal.
    Platform super_admin is a standing escape hatch (it can review a
    self-approved proposal same as any other, before or after the fact), not
    part of a specific org's own review capacity; folding it into this gate
    would make "genuinely empty" nearly unreachable in practice (both real
    super_admin accounts today sit in the SAME org as most real proposals)
    and would conflate a platform-operator bypass with an org's fiduciary
    staffing question — the two are answered by different people. This is
    also why the query never needs the ``app.is_super_admin`` RLS carve-out:
    ``roles``/``role_permissions``/``user_roles`` are already visible for the
    caller's own ambient ``app.current_org_id`` (the org the caller is
    already operating in when calling ``review_proposal``), the same
    ambient-context reliance ``get_user_permissions`` already makes.

    Relies on ``roles.org_id`` rather than joining through ``users`` — a
    review-permission grant is scoped by the ROLE's own org, not by
    re-deriving it from a user row, and avoids any question of whether the
    caller's RLS context can see other users' rows at all.
    """
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                JOIN role_permissions rp ON rp.role_id = r.id
                JOIN permissions p ON p.id = rp.permission_id
                WHERE r.org_id = $1::uuid
                  AND ur.user_id <> $2::uuid
                  AND p.name = $3
            )
            """,
            str(org_id), str(maker_id), REVIEW_PERMISSION,
        ))


async def review_proposal(
    pool,
    conn,
    org_id: str,
    proposal_id: str,
    *,
    reviewed_by: str,
    decision: str,
    review_notes: str | None = None,
    self_approval_reason: str | None = None,
) -> dict[str, Any]:
    """Close out a proposal — approve or reject — in one statement.

    Refuses (Python-level, before the UPDATE runs) when the reviewer lacks
    ``REVIEW_PERMISSION``, or is the maker AND another eligible checker
    exists; the database's own CHECK constraint is the backstop for the
    self-check half, not the only line of defense.

    When the reviewer IS the maker, self-approval is permitted ONLY when
    ``has_other_eligible_checker`` finds the org's checker set genuinely
    empty (never because ``self_approval_reason`` was merely supplied — that
    argument only supplies the disclosure text once emptiness is already
    established, it is not itself a bypass). ``self_approval_reason`` is
    then mandatory and the row is stamped ``self_approved = true``.
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

    is_self = str(proposal["proposed_by"]) == str(reviewed_by)
    self_approved = False

    if is_self:
        if not await _holds_review_permission(pool, reviewed_by, org_id):
            raise NotEligibleError(
                f"user {reviewed_by} does not hold {REVIEW_PERMISSION!r}",
                proposal_id=proposal_id, user_id=str(reviewed_by),
            )
        if await has_other_eligible_checker(pool, org_id, proposal["proposed_by"]):
            raise MakerCheckerError(
                f"user {reviewed_by} made proposal {proposal_id} and cannot also "
                "check it — another eligible checker exists in this org",
                proposal_id=proposal_id, user_id=str(reviewed_by),
            )
        if not self_approval_reason or not self_approval_reason.strip():
            raise SelfApprovalReasonRequiredError(
                f"proposal {proposal_id} has no other eligible checker in "
                "this org; self-approval requires self_approval_reason",
                proposal_id=proposal_id, user_id=str(reviewed_by),
            )
        self_approved = True
    else:
        if not await is_eligible_reviewer(pool, org_id, reviewed_by, proposal["proposed_by"]):
            raise NotEligibleError(
                f"user {reviewed_by} does not hold {REVIEW_PERMISSION!r}",
                proposal_id=proposal_id, user_id=str(reviewed_by),
            )

    row = await conn.fetchrow(
        """
        UPDATE agent_proposals
        SET status = $3, reviewed_by = $4::uuid, reviewed_at = now(), review_notes = $5,
            self_approved = $6, self_approval_reason = $7
        WHERE id = $1::uuid AND org_id = $2::uuid
        RETURNING id::text AS id, status, reviewed_by::text AS reviewed_by,
                  proposed_by::text AS proposed_by, reviewed_at,
                  self_approved, self_approval_reason
        """,
        proposal_id, org_id, decision, reviewed_by, review_notes,
        self_approved, self_approval_reason if self_approved else None,
    )
    return dict(row)
