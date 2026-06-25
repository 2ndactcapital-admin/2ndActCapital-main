"""Admin endpoints: member management and role assignment (Sprint 9).

Gated by the ``manage_members`` permission (DB-backed RBAC). Role changes are
written to the ``user_roles`` join table and recorded in the audit log.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.rbac import require_permission
from services.users import ensure_user

router = APIRouter(tags=["admin"])


class RoleOption(BaseModel):
    id: UUID
    name: str


class AdminUser(BaseModel):
    id: UUID
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    role_id: UUID | None = None
    created_at: str | None = None


class RoleAssignRequest(BaseModel):
    role_id: UUID


async def _require_manage_members(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
    await require_permission(pool, actor_id, org_id, "manage_members")
    return actor_id, org_id


@router.get("/admin/roles", response_model=list[RoleOption])
async def list_roles(request: Request):
    await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name FROM roles ORDER BY name")
    return [RoleOption(**dict(r)) for r in rows]


@router.get("/admin/users", response_model=list[AdminUser])
async def list_users(
    request: Request,
    search: str | None = None,
    role: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _, org_id = await _require_manage_members(request)

    conditions = ["u.org_id = $1"]
    params: list = [org_id]
    if search:
        params.append(f"%{search}%")
        conditions.append(
            f"(u.full_name ILIKE ${len(params)} OR u.email ILIKE ${len(params)})"
        )
    if role:
        params.append(role)
        conditions.append(f"r.name = ${len(params)}")

    params.append(limit)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.id, u.email, u.full_name, u.created_at,
                   r.id AS role_id, r.name AS role
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE {' AND '.join(conditions)}
            ORDER BY u.full_name NULLS LAST, u.email
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            """,
            *params,
        )
    return [
        AdminUser(
            id=r["id"],
            email=r["email"],
            full_name=r["full_name"],
            role=r["role"],
            role_id=r["role_id"],
            created_at=str(r["created_at"]) if r["created_at"] else None,
        )
        for r in rows
    ]


@router.put("/admin/users/{user_id}/role", response_model=AdminUser)
async def assign_role(request: Request, user_id: UUID, body: RoleAssignRequest):
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                "SELECT id, email, full_name FROM users WHERE id = $1 AND org_id = $2",
                user_id, org_id,
            )
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")

            role = await conn.fetchrow(
                "SELECT id, name FROM roles WHERE id = $1", body.role_id
            )
            if role is None:
                raise HTTPException(status_code=400, detail="Unknown role")

            await conn.execute("DELETE FROM user_roles WHERE user_id = $1", user_id)
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)",
                user_id, body.role_id,
            )

        await write_audit_log(
            conn,
            org_id=org_id,
            action="assign_role",
            table_name="user_roles",
            record_id=user_id,
            new={"user_id": str(user_id), "role_id": str(body.role_id),
                 "role": role["name"], "assigned_by": str(actor_id)},
            actor=actor_id,
        )

    return AdminUser(
        id=target["id"],
        email=target["email"],
        full_name=target["full_name"],
        role=role["name"],
        role_id=role["id"],
    )
