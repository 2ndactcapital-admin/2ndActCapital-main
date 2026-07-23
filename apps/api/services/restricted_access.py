"""Restricted-access accounts — the single unified visibility filter (SOC Phase 4).

Some entities are *restricted*: their very existence must be hidden from search
and list results for anyone not explicitly allow-listed, regardless of how they
would otherwise become visible.

CRITICAL DESIGN (per the SOC spec): the restriction check is ONE filter that
wraps BOTH visibility engines rather than being reimplemented inside each:

  * staff side  — ``services.staff_visibility.get_staff_visible_entity_ids``
                  (hierarchy / team / direct assignment)
  * member side — ``services.entity_graph.resolve_entity_set``
                  (ownership look-through / beneficiary edges)

Either engine produces its normal result set; ``filter_restricted`` is then
applied to that set as a FINAL step. A restricted entity is dropped for a
would-be viewer who is off the allow-list whether they are staff or member —
same filter, one implementation. A viewer WITH an explicit row in
``restricted_access_grants`` keeps seeing the entity through the filter.

SAFETY / SCOPE (SOC Phase 4, same posture as Phase 2): this module is a
STANDALONE, callable, testable filter. It is NOT wired into any endpoint's
enforcement path this phase — switching an endpoint to gate on it is a
deliberate, separate, later decision (production is the only environment).

The Super-Admin-only mutators below (``set_restricted`` /
``grant_restricted_access`` / ``revoke_restricted_access``) each write a row to
``restricted_access_audit`` — that dedicated audit table, not bi-temporal
versioning of the entity row, is the history mechanism for this operational
security flag (mirroring how the flexible in-place flags elsewhere are handled).
"""

from services.rbac import is_super_admin, load_principal

# Audit actions written to restricted_access_audit.action.
ACTION_RESTRICT = "restrict"
ACTION_UNRESTRICT = "unrestrict"
ACTION_GRANT = "grant_access"
ACTION_REVOKE = "revoke_access"


# ==========================================================================
# TASK 1 — The unified restriction filter (wraps BOTH engines)
# ==========================================================================
async def filter_restricted(pool, entity_ids, user_id, org_id) -> set:
    """Remove restricted entities ``user_id`` is not allow-listed for.

    Given ``entity_ids`` — a set that EITHER visibility engine (staff OR member)
    would otherwise return — drop every entity whose ``access_restricted`` is
    true UNLESS ``user_id`` has a matching row in ``restricted_access_grants``.
    Non-restricted entities always pass through untouched.

    ``pool`` is first for consistency with the other resolvers
    (``get_staff_visible_entity_ids(pool, ...)``). This is a pure read; it never
    mutates and is meant to be called AFTER an engine produces its result set.
    """
    ids = {str(e) for e in entity_ids}
    if not ids:
        return set()

    ids_list = list(ids)
    async with pool.acquire() as conn:
        # Which of these are actually restricted (scoped to org).
        restricted_rows = await conn.fetch(
            """
            SELECT id
            FROM entities
            WHERE org_id = $1
              AND id = ANY($2::uuid[])
              AND access_restricted = true
            """,
            org_id,
            ids_list,
        )
        restricted = {str(r["id"]) for r in restricted_rows}

        if not restricted:
            # Nothing restricted in this set — no filtering needed.
            return ids

        # Of the restricted ones, which has this user been explicitly granted.
        grant_rows = await conn.fetch(
            """
            SELECT entity_id
            FROM restricted_access_grants
            WHERE org_id = $1
              AND user_id = $2
              AND entity_id = ANY($3::uuid[])
            """,
            org_id,
            user_id,
            list(restricted),
        )
        granted = {str(r["entity_id"]) for r in grant_rows}

    # Drop restricted entities the user is NOT allow-listed for.
    blocked = restricted - granted
    return ids - blocked


# ==========================================================================
# TASK 2 — Super-Admin-only mutators, each audited
# ==========================================================================
async def _require_super_admin(pool, by_user_id):
    """Raise PermissionError unless ``by_user_id`` is a Super Admin.

    Reuses the canonical ``services.rbac.is_super_admin`` check against the
    principal loaded from ``users.role`` — the same gate the org-admin
    endpoints use. Kept in the service (not just the router) so enforcement is
    testable without going through HTTP.
    """
    async with pool.acquire() as conn:
        principal = await load_principal(conn, by_user_id)
    if not is_super_admin(principal):
        raise PermissionError("Super Admin access required")


async def _entity_org_id(conn, entity_id):
    """The entity's own org_id (server-side truth — never trust a request body)."""
    org_id = await conn.fetchval(
        "SELECT org_id FROM entities WHERE id = $1", entity_id
    )
    if org_id is None:
        raise ValueError(f"Entity {entity_id} not found")
    return org_id


async def _write_audit(conn, org_id, entity_id, action, by_user_id, notes=None):
    await conn.execute(
        """
        INSERT INTO restricted_access_audit
            (org_id, entity_id, action, performed_by, notes)
        VALUES ($1, $2, $3, $4, $5)
        """,
        org_id,
        entity_id,
        action,
        by_user_id,
        notes,
    )


async def set_restricted(pool, entity_id, restricted: bool, by_user_id, *, notes=None):
    """Flip ``entities.access_restricted`` and write an audit row. Super Admin only.

    org_id is resolved from the entity itself, never from a caller-supplied body.
    """
    await _require_super_admin(pool, by_user_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            org_id = await _entity_org_id(conn, entity_id)
            await conn.execute(
                "UPDATE entities SET access_restricted = $2 WHERE id = $1",
                entity_id,
                restricted,
            )
            action = ACTION_RESTRICT if restricted else ACTION_UNRESTRICT
            await _write_audit(conn, org_id, entity_id, action, by_user_id, notes)
    return {"entity_id": str(entity_id), "access_restricted": restricted}


async def grant_restricted_access(pool, entity_id, user_id, by_user_id, reason=None):
    """Add ``user_id`` to an entity's allow-list and audit it. Super Admin only."""
    await _require_super_admin(pool, by_user_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            org_id = await _entity_org_id(conn, entity_id)
            await conn.execute(
                """
                INSERT INTO restricted_access_grants
                    (org_id, entity_id, user_id, granted_by, reason)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (entity_id, user_id)
                DO UPDATE SET granted_by = EXCLUDED.granted_by,
                             reason = EXCLUDED.reason,
                             granted_at = now()
                """,
                org_id,
                entity_id,
                user_id,
                by_user_id,
                reason,
            )
            await _write_audit(
                conn, org_id, entity_id, ACTION_GRANT, by_user_id, reason
            )
    return {"entity_id": str(entity_id), "user_id": str(user_id), "granted": True}


async def revoke_restricted_access(pool, entity_id, user_id, by_user_id, reason=None):
    """Remove ``user_id`` from an entity's allow-list and audit it. Super Admin only."""
    await _require_super_admin(pool, by_user_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            org_id = await _entity_org_id(conn, entity_id)
            await conn.execute(
                """
                DELETE FROM restricted_access_grants
                WHERE entity_id = $1 AND user_id = $2
                """,
                entity_id,
                user_id,
            )
            await _write_audit(
                conn, org_id, entity_id, ACTION_REVOKE, by_user_id, reason
            )
    return {"entity_id": str(entity_id), "user_id": str(user_id), "granted": False}
