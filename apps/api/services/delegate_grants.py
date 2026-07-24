"""SOC Phase 6 · Task 2 — Power of Attorney / Delegate grants.

Scoped, optionally time-bound or springing, and — critically — audited AS a
DELEGATED action: both the delegate who acted AND the principal on whose behalf
are captured, so an action is NEVER silently attributed to the principal alone.

This relationship is DISTINCT from ownership/beneficiary edges
(``entity_relationships``) and from staff-visibility (Phases 2/4). A delegate is
a member-side actor granted rights over ANOTHER member's entity.

Scopes (the DB CHECK ``delegate_grants_scope_check`` enforces the set):
  view_only — may SEE the principal's entities. Integrated with the real
              member-side visibility engine (``resolve_entity_set``) via
              ``get_delegate_visible_entity_ids`` below.
  transact  — may additionally PROPOSE actions on the principal's behalf. Those
              flow through the SAME ``assistant_activities`` maker-checker ledger
              a principal's own money-movement uses (SOC Phase 5): a delegate
              cannot unilaterally execute — a separate approver is still
              required. (transact subsumes view.)

Activation model:
  * A normal grant is active immediately, subject to any effective window.
  * A SPRINGING grant (``is_springing = true``) is INERT until ``activated_at``
    is set by an explicit activation action — ``activate_springing_delegate``,
    gated to Super Admin / Org Admin, who confirm the springing condition (e.g.
    incapacity) has actually been met. Setting ``effective_from`` is NOT enough;
    a springing grant with no ``activated_at`` is never active.

Structural SOC posture (Phases 2/4/5): standalone, importable, exercised by
``verify_soc6.py``, HELD for manual wiring — changes no existing endpoint's
behavior on its own. ``is_active_delegate`` is a PURE function so the five-state
truth table is testable without the DB. ``org_id`` is always caller-supplied
from a server-resolved value, never from a request body.
"""
import json
from datetime import datetime, timezone

from services.audit import write_audit_log
from services.entity_graph import resolve_entity_set
from services.rbac import is_org_admin, is_super_admin, load_principal

VIEW_ONLY = "view_only"
TRANSACT = "transact"
_VALID_SCOPES = (VIEW_ONLY, TRANSACT)


class DelegateError(PermissionError):
    """Raised when a delegate operation is not permitted or is malformed."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The pure active-state predicate (the five-state truth table)
# ---------------------------------------------------------------------------
def is_active_delegate(grant, now=None) -> bool:
    """Whether a ``delegate_grants`` row is active RIGHT NOW.

    Pure function over a row (asyncpg ``Record`` or ``dict``). Correctly handles
    all five states:

      revoked                     -> False   (``revoked_at`` is set)
      springing, not activated    -> False   (``is_springing`` and no ``activated_at``)
      not yet effective           -> False   (``now`` < ``effective_from``)
      expired                     -> False   (``now`` >= ``effective_until``)
      otherwise                   -> True    (incl. springing-and-activated, and
                                              any grant inside its effective window)
    """
    now = now or _utcnow()

    # 1. Revocation trumps everything.
    if grant["revoked_at"] is not None:
        return False

    # 2. A springing grant is inert until explicitly activated.
    if grant["is_springing"] and grant["activated_at"] is None:
        return False

    # 3. Effective window (either bound is optional).
    eff_from = grant["effective_from"]
    if eff_from is not None and now < eff_from:
        return False  # not yet effective
    eff_until = grant["effective_until"]
    if eff_until is not None and now >= eff_until:
        return False  # expired

    return True


# ---------------------------------------------------------------------------
# Mutators — grant / revoke / activate
# ---------------------------------------------------------------------------
async def grant_delegate(
    pool,
    org_id,
    *,
    principal_entity_id,
    scope,
    delegate_user_id=None,
    delegate_email=None,
    effective_from=None,
    effective_until=None,
    is_springing=False,
    granted_by=None,
) -> str:
    """Create a delegate grant. Returns the new grant id.

    ``scope`` must be ``view_only`` or ``transact`` (also enforced by the DB
    CHECK). A springing grant is created inert — it must be activated later.
    """
    if scope not in _VALID_SCOPES:
        raise DelegateError(f"invalid delegate scope {scope!r}")
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                """
                INSERT INTO delegate_grants
                    (org_id, principal_entity_id, delegate_user_id, delegate_email,
                     scope, effective_from, effective_until, is_springing, granted_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                org_id,
                principal_entity_id,
                delegate_user_id,
                delegate_email,
                scope,
                effective_from,
                effective_until,
                is_springing,
                granted_by,
            )
        )


async def revoke_delegate(pool, org_id, grant_id, revoked_by) -> None:
    """Revoke a delegate grant (idempotent — only closes an un-revoked row)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE delegate_grants
            SET revoked_at = now(), revoked_by = $3
            WHERE org_id = $1 AND id = $2 AND revoked_at IS NULL
            """,
            org_id,
            grant_id,
            revoked_by,
        )


async def activate_springing_delegate(pool, org_id, grant_id, by_user_id) -> None:
    """Activate a springing grant by setting ``activated_at`` — the explicit
    confirmation that the springing condition (e.g. incapacity) has been met.

    Gated to Super Admin / Org Admin: they carry the responsibility of confirming
    the condition. Non-springing, revoked, or missing grants are rejected.
    """
    async with pool.acquire() as conn:
        principal = await load_principal(conn, by_user_id)
        if not (is_super_admin(principal) or is_org_admin(principal, org_id)):
            raise DelegateError(
                "Super Admin or Org Admin required to activate a springing delegate"
            )
        row = await conn.fetchrow(
            """
            SELECT is_springing, revoked_at, activated_at
            FROM delegate_grants
            WHERE org_id = $1 AND id = $2
            """,
            org_id,
            grant_id,
        )
        if row is None:
            raise DelegateError(f"delegate grant {grant_id} not found")
        if not row["is_springing"]:
            raise DelegateError("grant is not springing; nothing to activate")
        if row["revoked_at"] is not None:
            raise DelegateError("cannot activate a revoked grant")
        await conn.execute(
            "UPDATE delegate_grants SET activated_at = now() WHERE org_id = $1 AND id = $2",
            org_id,
            grant_id,
        )


# ---------------------------------------------------------------------------
# Lookups + visibility integration (Task 2: view_only -> resolve_entity_set)
# ---------------------------------------------------------------------------
async def _active_grants_for_delegate(pool, org_id, delegate_user_id, *, scopes=None):
    """All currently-active grants (by ``is_active_delegate``) held BY a delegate,
    optionally filtered to a set of scopes."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM delegate_grants
            WHERE org_id = $1 AND delegate_user_id = $2
            """,
            org_id,
            delegate_user_id,
        )
    active = [r for r in rows if is_active_delegate(r)]
    if scopes is not None:
        active = [r for r in active if r["scope"] in scopes]
    return active


async def get_delegate_visible_entity_ids(pool, org_id, delegate_user_id) -> set:
    """Entity ids a delegate may VIEW.

    For every principal who has an ACTIVE grant (``view_only`` or ``transact`` —
    transact subsumes view) to this delegate, run the principal's entity through
    the real member-side visibility engine, ``resolve_entity_set`` (subtree: the
    principal's own entity + ownership look-through + beneficiary edges), and
    union the results. This is the integration point that lets a delegate
    actually see what they're authorized to see, reusing the SAME engine members
    use rather than a parallel one.
    """
    grants = await _active_grants_for_delegate(pool, org_id, delegate_user_id)
    visible: set = set()
    for g in grants:
        resolved = await resolve_entity_set(
            pool,
            org_id,
            {"type": "subtree", "root_id": str(g["principal_entity_id"])},
        )
        for item in resolved:
            visible.add(str(item["entity_id"]))
    return visible


# ---------------------------------------------------------------------------
# Task 2 core: log an action AS a delegated action (both actors captured)
# ---------------------------------------------------------------------------
async def record_delegated_action(
    pool,
    org_id,
    *,
    principal_entity_id,
    delegate_user_id,
    action_key,
    title=None,
    rationale=None,
    payload=None,
    require_active_transact=True,
) -> str:
    """Record an action a delegate takes ON BEHALF OF a principal, AS a delegated
    action. Returns the new ``assistant_activities`` id.

    The row natively carries BOTH parties:
        user_id / proposed_by = delegate_user_id    (who actually acted)
        entity_id             = principal_entity_id  (on whose behalf)
    and the payload is stamped with an explicit ``acting_as='delegate'`` marker
    plus both ids — the action is NEVER attributed to the principal alone.

    Status is ``'proposed'``: a delegate cannot unilaterally execute. The action
    enters the same maker-checker ledger a principal's own action would (SOC
    Phase 5), so a separate approver is still required. The event is also mirrored
    to ``audit_log`` with the actor set to the delegate.

    When ``require_active_transact`` is true, an active ``transact`` grant from
    this delegate to this principal must exist, else ``DelegateError``.
    """
    grant = None
    if require_active_transact:
        candidates = await _active_grants_for_delegate(
            pool, org_id, delegate_user_id, scopes={TRANSACT}
        )
        candidates = [
            g
            for g in candidates
            if str(g["principal_entity_id"]) == str(principal_entity_id)
        ]
        if not candidates:
            raise DelegateError(
                "no active 'transact' delegate grant for this delegate/principal"
            )
        grant = candidates[0]

    pay = dict(payload or {})
    pay["acting_as"] = "delegate"
    pay["delegate_user_id"] = str(delegate_user_id)
    pay["principal_entity_id"] = str(principal_entity_id)
    if grant is not None:
        pay["delegate_grant_id"] = str(grant["id"])

    async with pool.acquire() as conn:
        activity_id = await conn.fetchval(
            """
            INSERT INTO assistant_activities
                (org_id, user_id, proposed_by, entity_id, action_key, title,
                 status, rationale, payload, reversible)
            VALUES ($1, $2, $2, $3, $4, $5, 'proposed', $6, $7::jsonb, false)
            RETURNING id
            """,
            org_id,
            delegate_user_id,
            principal_entity_id,
            action_key,
            title or action_key,
            rationale,
            json.dumps(pay),
        )

    # Mirror to audit_log with the DELEGATE as actor and the delegation explicit
    # in the payload — never implicit.
    await write_audit_log(
        org_id=org_id,
        action=f"delegated_action:{action_key}",
        table_name="assistant_activities",
        record_id=str(activity_id),
        new={
            "acting_as": "delegate",
            "delegate_user_id": str(delegate_user_id),
            "principal_entity_id": str(principal_entity_id),
            "action_key": action_key,
        },
        actor=delegate_user_id,
    )
    return str(activity_id)
