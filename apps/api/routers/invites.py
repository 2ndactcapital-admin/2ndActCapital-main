"""Admin invite endpoints (Multi-tenant Sprint 2).

An Org Admin (or Super Admin) provisions a pending member account; the invitee
later enrolls with the returned token. Gated by the same ``manage_members``
permission as the rest of member management (which already grants Super Admin a
first-checked bypass — see services.rbac.has_permission).

``org_id`` is ALWAYS taken from the request context (get_org_id), NEVER from the
request body — standing multi-tenant rule. Combined with the ``users`` RLS
policy, this makes cross-org invite creation/listing/revocation impossible.

Email delivery is NOT performed here: the SES credential gate failed this sprint
(the Textract IAM user has no SES permission), so ``enrollment_url`` is returned
to the admin for manual sharing. When SES is provisioned, Task 3 wires a send
call at the marked point in ``create_invite_endpoint``.
"""

import os
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.invites import (
    ALLOWED_INVITE_ROLES,
    create_invite,
    revoke_invite,
)
from services.rbac import require_permission
from services.users import ensure_user

router = APIRouter(tags=["invites"])


def _enrollment_url(token: str) -> str:
    """Build the enrollment link for an invite token.

    Base comes from WEB_BASE_URL / APP_BASE_URL when set; otherwise a relative
    path so the value is still usable (and never a wrong hardcoded host).
    """
    base = (os.environ.get("WEB_BASE_URL") or os.environ.get("APP_BASE_URL") or "").rstrip("/")
    return f"{base}/enroll?invite_token={token}"


class InviteCreateRequest(BaseModel):
    email: str
    full_name: str | None = None
    role: str = "member"


class InviteResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None = None
    role: str | None = None
    invite_status: str | None = None
    invite_token: str | None = None
    enrollment_url: str | None = None
    invited_by: UUID | None = None
    invited_at: str | None = None
    invite_expires_at: str | None = None


async def _require_manage_members(request: Request) -> tuple[str, str]:
    """Resolve the caller, enforce ``manage_members``, return (actor_id, org_id)."""
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
    await require_permission(pool, actor_id, org_id, "manage_members")
    return actor_id, org_id


@router.post("/admin/invites", response_model=InviteResponse, status_code=201)
async def create_invite_endpoint(request: Request, body: InviteCreateRequest):
    actor_id, org_id = await _require_manage_members(request)

    role = (body.role or "member").strip()
    if role not in ALLOWED_INVITE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {', '.join(ALLOWED_INVITE_ROLES)}",
        )
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await create_invite(
                conn,
                org_id=org_id,
                email=email,
                full_name=(body.full_name or None),
                role=role,
                invited_by=actor_id,
            )
        except asyncpg.UniqueViolationError:
            # Email already exists (this org or, unseen under RLS, another org),
            # or a token collision. Either way the invite cannot be created.
            raise HTTPException(
                status_code=409,
                detail="A user with this email already exists",
            )

        await write_audit_log(
            conn,
            org_id=org_id,
            action="create_invite",
            table_name="users",
            record_id=row["id"],
            new={
                "email": email,
                "role": role,
                "invited_by": str(actor_id),
                "invite_status": "pending",
            },
            actor=actor_id,
        )

    # --- Task 3 hook (BLOCKED — SES gate failed): once SES is provisioned, send
    # the invite email here, e.g. await send_invite_email(email, token). ---

    token = row["invite_token"]
    return InviteResponse(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        invite_status=row["invite_status"],
        invite_token=token,
        enrollment_url=_enrollment_url(token),
        invited_by=row["invited_by"],
        invited_at=str(row["invited_at"]) if row["invited_at"] else None,
        invite_expires_at=str(row["invite_expires_at"]) if row["invite_expires_at"] else None,
    )


@router.get("/admin/invites", response_model=list[InviteResponse])
async def list_invites(
    request: Request,
    status: str | None = Query(None, description="Filter by invite_status"),
):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()

    conditions = ["org_id = $1", "invite_status IS NOT NULL"]
    params: list = [org_id]
    if status:
        params.append(status)
        conditions.append(f"invite_status = ${len(params)}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, email, full_name, role, invite_status, invite_token,
                   invited_by, invited_at, invite_expires_at
            FROM users
            WHERE {' AND '.join(conditions)}
            ORDER BY invited_at DESC NULLS LAST
            """,
            *params,
        )

    return [
        InviteResponse(
            id=r["id"],
            email=r["email"],
            full_name=r["full_name"],
            role=r["role"],
            invite_status=r["invite_status"],
            invite_token=r["invite_token"],
            enrollment_url=_enrollment_url(r["invite_token"]) if r["invite_token"] else None,
            invited_by=r["invited_by"],
            invited_at=str(r["invited_at"]) if r["invited_at"] else None,
            invite_expires_at=str(r["invite_expires_at"]) if r["invite_expires_at"] else None,
        )
        for r in rows
    ]


@router.post("/admin/invites/{invite_id}/revoke", response_model=InviteResponse)
async def revoke_invite_endpoint(request: Request, invite_id: UUID):
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await revoke_invite(conn, org_id=org_id, invite_id=str(invite_id))
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="No pending invite with that id in this org",
            )
        await write_audit_log(
            conn,
            org_id=org_id,
            action="revoke_invite",
            table_name="users",
            record_id=row["id"],
            new={"invite_status": "revoked", "revoked_by": str(actor_id)},
            actor=actor_id,
        )

    return InviteResponse(
        id=row["id"],
        email=row["email"],
        invite_status=row["invite_status"],
    )
