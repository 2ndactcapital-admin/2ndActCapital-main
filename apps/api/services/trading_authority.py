"""SOC Phase 5 — Trading authority tiers + maker-checker enforcement.

Standalone governance layer for money-movement actions. Follows the SOC
structural pattern (see services/restricted_access.py): importable, side-effect
free at import time, exercised directly by verify_soc5.py, and HELD for manual
wiring into the assistant confirm flow at review — it does NOT alter any
existing endpoint's behavior on its own.

Discovery note (Task 1). The design brief assumed a dedicated Altruist/custodian
write-back subsystem with a Tier-1 status enum
(proposed -> approved -> dispatched -> awaiting-client-consent ->
acknowledged-at-custodian -> settled/rejected). That subsystem does NOT exist in
this codebase. The real thing that "already handles write-back actions" is the
assistant WRITE-action pipeline: a member proposes an action via
POST /assistant/message and it is executed via POST /assistant/confirm, which
records a row in ``assistant_activities`` (statuses: awaiting_review / done /
undone). Money-moving actions in that pipeline are ``spv.record_transaction``
(capital calls, distributions, fees) and ``spv.subscribe``. Before this phase
there was NO maker-checker anywhere and ``trading_authority_grants`` was
referenced by zero code. This module adds both, keyed to the real ledger.

Regulatory model (do not soften — a real custody-rule distinction):
  inquiry — view only; cannot propose any money movement.
  limited — may propose/initiate movement WITHIN an account; CANNOT direct funds
            to a third party; does NOT trigger custody.
  full    — may direct funds to ANY third party; TRIGGERS custody.

Maker-checker: for any money-movement action the proposer and approver MUST be
different people. Enforced here in code AND by the
``assistant_activities_maker_checker_chk`` CHECK constraint. This holds
regardless of role, permission set, or authority tier — even a 'full' user
cannot approve their own proposal.
"""
import json
from decimal import Decimal

INQUIRY = "inquiry"
LIMITED = "limited"
FULL = "full"

# Ordered tiers — a higher rank subsumes the authority of every lower one.
_RANK = {INQUIRY: 0, LIMITED: 1, FULL: 2}

# Money-movement assistant actions. ``third_party`` => the action can direct
# funds to a third party => it requires FULL authority (custody-triggering).
MONEY_MOVEMENT_ACTIONS: dict[str, dict] = {
    # distributions / fees pay out to investors and managers (third parties)
    "spv.record_transaction": {"third_party": True},
    # subscribing commits capital within the member's own investment context
    "spv.subscribe": {"third_party": False},
}


class AuthorityError(PermissionError):
    """Raised when a user's trading-authority tier is too low to PROPOSE."""


class MakerCheckerError(PermissionError):
    """Raised when the approver of a money-movement action equals the proposer."""


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------
def is_money_movement(action_key: str) -> bool:
    return action_key in MONEY_MOVEMENT_ACTIONS


def requires_full_authority(action_key: str) -> bool:
    """True when the action can direct funds to a third party (needs FULL)."""
    spec = MONEY_MOVEMENT_ACTIONS.get(action_key)
    return bool(spec and spec["third_party"])


def required_tier(action_key: str) -> str:
    """Minimum tier permitted to propose ``action_key``."""
    return FULL if requires_full_authority(action_key) else LIMITED


# ---------------------------------------------------------------------------
# Grant lookups
# ---------------------------------------------------------------------------
async def get_authority_tier(pool, org_id, entity_id, user_id) -> str | None:
    """The user's active tier for the entity, or None if no grant exists."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT authority_tier
            FROM trading_authority_grants
            WHERE org_id = $1 AND entity_id = $2 AND user_id = $3
            """,
            org_id, entity_id, user_id,
        )


def _tier_rank(tier: str | None) -> int:
    return _RANK.get(tier or "", -1)


# ---------------------------------------------------------------------------
# Enforcement primitives
# ---------------------------------------------------------------------------
async def assert_can_propose(pool, org_id, entity_id, user_id, action_key) -> str | None:
    """Reject (AuthorityError) unless the user's tier is high enough to PROPOSE
    this money-movement action for this entity.

    - inquiry (or no grant)        -> rejected for any money movement
    - limited                      -> may propose within-account movement only
    - full                         -> may propose third-party movement too

    Non-money actions are governed elsewhere; this returns None for them.
    Returns the caller's tier on success.
    """
    if not is_money_movement(action_key):
        return None
    tier = await get_authority_tier(pool, org_id, entity_id, user_id)
    needed = required_tier(action_key)
    if _tier_rank(tier) < _RANK[needed]:
        raise AuthorityError(
            f"tier {tier!r} cannot propose {action_key!r} for entity {entity_id} "
            f"(requires {needed!r} or higher)"
        )
    return tier


def assert_maker_checker(proposed_by, approved_by) -> None:
    """The hard maker-checker rule: approver must differ from proposer.

    Applies regardless of the approver's role, permission set, or tier — a
    'full'-authority user still cannot approve their own proposal.
    """
    if str(proposed_by) == str(approved_by):
        raise MakerCheckerError(
            "approver must differ from proposer (maker-checker): a user cannot "
            "approve their own money-movement proposal"
        )


# ---------------------------------------------------------------------------
# Money-movement ledger (proposed -> approved on assistant_activities)
# ---------------------------------------------------------------------------
async def propose_money_movement(
    pool, org_id, entity_id, user_id, action_key, *,
    amount=None, title=None, rationale=None, payload=None,
) -> str:
    """Enforce the tier gate, then record a PROPOSED money-movement activity.

    Writes an ``assistant_activities`` row with status 'proposed',
    proposed_by = the maker, and no approver yet. Returns the new activity id.
    Raises AuthorityError if the caller may not propose.
    """
    await assert_can_propose(pool, org_id, entity_id, user_id, action_key)

    pay = dict(payload or {})
    if amount is not None:
        # Rule: money as Decimal; store as a string to preserve precision in jsonb.
        pay["amount"] = str(Decimal(str(amount)))

    async with pool.acquire() as conn:
        activity_id = await conn.fetchval(
            """
            INSERT INTO assistant_activities
                (org_id, user_id, proposed_by, entity_id, action_key, title,
                 status, rationale, payload, reversible)
            VALUES ($1, $2, $2, $3, $4, $5, 'proposed', $6, $7::jsonb, false)
            RETURNING id
            """,
            org_id, user_id, entity_id, action_key,
            title or action_key, rationale, json.dumps(pay),
        )
    return str(activity_id)


async def approve_money_movement(pool, org_id, activity_id, approver_id) -> dict:
    """Enforce maker-checker + approver authority, then mark the activity APPROVED.

    Rejections:
      - MakerCheckerError if approver_id == the activity's proposed_by.
      - AuthorityError if the approver's own tier for the entity is below
        'limited' (an unauthorized user cannot be the checker).
    The DB CHECK constraint is a second, independent guard against
    self-approval. Returns the updated {id, status, approved_by}.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT proposed_by, entity_id, action_key, status
            FROM assistant_activities
            WHERE id = $1 AND org_id = $2
            """,
            activity_id, org_id,
        )
        if row is None:
            raise ValueError(f"money-movement activity {activity_id} not found")
        if row["status"] != "proposed":
            raise ValueError(
                f"activity {activity_id} is {row['status']!r}, not 'proposed'"
            )

        # Maker-checker first — independent of any tier the approver may hold.
        assert_maker_checker(row["proposed_by"], approver_id)

        # The checker must themselves be authorized (>= limited) for the entity.
        approver_tier = await conn.fetchval(
            """
            SELECT authority_tier
            FROM trading_authority_grants
            WHERE org_id = $1 AND entity_id = $2 AND user_id = $3
            """,
            org_id, row["entity_id"], approver_id,
        )
        if _tier_rank(approver_tier) < _RANK[LIMITED]:
            raise AuthorityError(
                f"approver tier {approver_tier!r} cannot approve money movement "
                f"(requires {LIMITED!r} or higher)"
            )

        updated = await conn.fetchrow(
            """
            UPDATE assistant_activities
            SET approved_by = $2, status = 'approved', updated_at = now()
            WHERE id = $1
            RETURNING id, status, approved_by
            """,
            activity_id, approver_id,
        )
    return {
        "id": str(updated["id"]),
        "status": updated["status"],
        "approved_by": str(updated["approved_by"]),
    }
