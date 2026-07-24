"""SOC Phase 6 · Task 3 — External Professional Access grants.

A TIME-BOXED, scoped, NON-PERSISTENT grant that lets an external professional
(a member's attorney, accountant, ...) — identified ONLY by email — reach a
specific entity, or a specific document when ``document_id`` is set. It creates
NO ``users`` row and NO persistent role: it is a standalone, checkable grant
record and nothing else. Access ends automatically at ``expires_at`` (REQUIRED)
or immediately on revoke.

This is DISTINCT from a delegate (a member-side actor with an account) and from
staff visibility — an external grantee never becomes a principal in the system.

Structural SOC posture (Phases 2/4/5): standalone, importable, exercised by
``verify_soc6.py``, HELD for manual wiring. ``org_id`` is always caller-supplied
from a server-resolved value, never from a request body.
"""
from datetime import datetime, timezone


class ExternalAccessError(ValueError):
    """Raised when an external-access grant is malformed (e.g. no expiry)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_active_external_grant(grant, now=None) -> bool:
    """True only for a grant that is neither revoked nor expired.

    Pure over a row (asyncpg ``Record`` or ``dict``):
        revoked   -> False   (``revoked_at`` is set)
        expired   -> False   (``now`` >= ``expires_at``)
        otherwise -> True
    """
    now = now or _utcnow()
    if grant["revoked_at"] is not None:
        return False
    expires_at = grant["expires_at"]
    if expires_at is not None and now >= expires_at:
        return False
    return True


async def grant_external_access(
    pool,
    org_id,
    *,
    entity_id,
    grantee_email,
    expires_at,
    grantee_name=None,
    scope_description=None,
    document_id=None,
    granted_by=None,
) -> str:
    """Create an external-access grant. ``expires_at`` is REQUIRED. Returns the id.

    When ``document_id`` is set the grant is scoped to that single document;
    otherwise it covers the entity.
    """
    if expires_at is None:
        raise ExternalAccessError(
            "expires_at is REQUIRED for an external access grant"
        )
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                """
                INSERT INTO external_access_grants
                    (org_id, entity_id, grantee_email, grantee_name,
                     scope_description, document_id, expires_at, granted_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                org_id,
                entity_id,
                grantee_email,
                grantee_name,
                scope_description,
                document_id,
                expires_at,
                granted_by,
            )
        )


async def revoke_external_access(pool, org_id, grant_id) -> None:
    """Revoke a grant (idempotent — only closes an un-revoked row)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE external_access_grants
            SET revoked_at = now()
            WHERE org_id = $1 AND id = $2 AND revoked_at IS NULL
            """,
            org_id,
            grant_id,
        )


async def check_external_access(
    pool, org_id, grantee_email, entity_id, *, document_id=None, now=None
) -> bool:
    """Whether ``grantee_email`` currently has an ACTIVE grant to ``entity_id``
    (and to ``document_id`` when the grant is document-scoped). Email is matched
    case-insensitively. No persistent identity is consulted — only grant rows.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM external_access_grants
            WHERE org_id = $1
              AND entity_id = $2
              AND lower(grantee_email) = lower($3)
            """,
            org_id,
            entity_id,
            grantee_email,
        )
    for r in rows:
        # A document-scoped grant only satisfies a request for THAT document.
        if (
            document_id is not None
            and r["document_id"] is not None
            and str(r["document_id"]) != str(document_id)
        ):
            continue
        if is_active_external_grant(r, now):
            return True
    return False
