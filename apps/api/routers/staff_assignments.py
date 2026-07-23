"""Admin endpoints: staff teams + entity assignments (SOC Phase 2).

Lets an Org Admin populate the data the unified staff-visibility resolver
reads: create teams, add/remove team members, and assign a user OR a team to
an entity with a role label.

SCOPE / SAFETY (SOC Phase 2): these endpoints only CREATE assignment records.
They do NOT change any existing endpoint's visibility/authorization behavior
and do NOT import or invoke the resolver. Wiring the resolver into enforcement
is a separate, later decision.

Gated by the ``manage_members`` permission (DB-backed RBAC), same as the
member-management admin endpoints. ``org_id`` is always resolved server-side
(never from the request body).
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, model_validator

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.rbac import require_permission
from services.users import ensure_user

router = APIRouter(tags=["admin", "staff-assignments"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class TeamMember(BaseModel):
    user_id: UUID
    full_name: str | None = None
    email: str | None = None


class Team(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    members: list[TeamMember] = []


class TeamCreate(BaseModel):
    name: str
    description: str | None = None


class MemberAdd(BaseModel):
    user_id: UUID


class AssignmentCreate(BaseModel):
    entity_id: UUID
    assigned_to_user_id: UUID | None = None
    assigned_to_team_id: UUID | None = None
    role_label: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self):
        has_user = self.assigned_to_user_id is not None
        has_team = self.assigned_to_team_id is not None
        if has_user == has_team:
            raise ValueError(
                "Provide exactly one of assigned_to_user_id or assigned_to_team_id"
            )
        return self


class Assignment(BaseModel):
    id: UUID
    entity_id: UUID
    entity_name: str | None = None
    assigned_to_user_id: UUID | None = None
    assigned_to_user_name: str | None = None
    assigned_to_team_id: UUID | None = None
    assigned_to_team_name: str | None = None
    role_label: str | None = None


# --------------------------------------------------------------------------
# Auth helper
# --------------------------------------------------------------------------
async def _require_manage_members(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
    await require_permission(pool, actor_id, org_id, "manage_members")
    return actor_id, org_id


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------
@router.get("/admin/staff/teams", response_model=list[Team])
async def list_teams(request: Request):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.name, t.description,
                   tm.user_id, u.full_name, u.email
            FROM teams t
            LEFT JOIN team_members tm ON tm.team_id = t.id
            LEFT JOIN users u ON u.id = tm.user_id
            WHERE t.org_id = $1
            ORDER BY t.name, u.full_name NULLS LAST
            """,
            org_id,
        )
    teams: dict[str, Team] = {}
    for r in rows:
        tid = str(r["id"])
        if tid not in teams:
            teams[tid] = Team(
                id=r["id"], name=r["name"], description=r["description"], members=[]
            )
        if r["user_id"] is not None:
            teams[tid].members.append(
                TeamMember(
                    user_id=r["user_id"],
                    full_name=r["full_name"],
                    email=r["email"],
                )
            )
    return list(teams.values())


@router.post("/admin/staff/teams", response_model=Team, status_code=201)
async def create_team(request: Request, body: TeamCreate):
    actor_id, org_id = await _require_manage_members(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Team name is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM teams WHERE org_id = $1 AND name = $2", org_id, name
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="A team with that name exists")
        team_id = await conn.fetchval(
            """
            INSERT INTO teams (org_id, name, description)
            VALUES ($1, $2, $3) RETURNING id
            """,
            org_id, name, body.description,
        )
        await write_audit_log(
            conn, org_id=org_id, action="create_team", table_name="teams",
            record_id=team_id,
            new={"name": name, "created_by": str(actor_id)}, actor=actor_id,
        )
    return Team(id=team_id, name=name, description=body.description, members=[])


@router.post("/admin/staff/teams/{team_id}/members", status_code=201)
async def add_member(request: Request, team_id: UUID, body: MemberAdd):
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        team = await conn.fetchval(
            "SELECT id FROM teams WHERE id = $1 AND org_id = $2", team_id, org_id
        )
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        user = await conn.fetchval(
            "SELECT id FROM users WHERE id = $1 AND org_id = $2",
            body.user_id, org_id,
        )
        if user is None:
            raise HTTPException(status_code=404, detail="User not found in org")
        await conn.execute(
            """
            INSERT INTO team_members (team_id, user_id, added_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (team_id, user_id) DO NOTHING
            """,
            team_id, body.user_id, actor_id,
        )
    return {"ok": True}


@router.delete("/admin/staff/teams/{team_id}/members/{user_id}", status_code=200)
async def remove_member(request: Request, team_id: UUID, user_id: UUID):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        team = await conn.fetchval(
            "SELECT id FROM teams WHERE id = $1 AND org_id = $2", team_id, org_id
        )
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        await conn.execute(
            "DELETE FROM team_members WHERE team_id = $1 AND user_id = $2",
            team_id, user_id,
        )
    return {"ok": True}


# --------------------------------------------------------------------------
# Assignments
# --------------------------------------------------------------------------
@router.get("/admin/staff/assignments", response_model=list[Assignment])
async def list_assignments(request: Request):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sa.id, sa.entity_id, e.display_name AS entity_name,
                   sa.assigned_to_user_id, u.full_name AS user_name,
                   sa.assigned_to_team_id, t.name AS team_name,
                   sa.role_label
            FROM staff_assignments sa
            LEFT JOIN entities e ON e.id = sa.entity_id
            LEFT JOIN users u ON u.id = sa.assigned_to_user_id
            LEFT JOIN teams t ON t.id = sa.assigned_to_team_id
            WHERE sa.org_id = $1
            ORDER BY sa.assigned_at DESC
            """,
            org_id,
        )
    return [
        Assignment(
            id=r["id"],
            entity_id=r["entity_id"],
            entity_name=r["entity_name"],
            assigned_to_user_id=r["assigned_to_user_id"],
            assigned_to_user_name=r["user_name"],
            assigned_to_team_id=r["assigned_to_team_id"],
            assigned_to_team_name=r["team_name"],
            role_label=r["role_label"],
        )
        for r in rows
    ]


@router.post("/admin/staff/assignments", response_model=Assignment, status_code=201)
async def create_assignment(request: Request, body: AssignmentCreate):
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT id, display_name FROM entities WHERE id = $1 AND org_id = $2",
            body.entity_id, org_id,
        )
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found in org")

        user_name = None
        team_name = None
        if body.assigned_to_user_id is not None:
            user_name = await conn.fetchval(
                "SELECT full_name FROM users WHERE id = $1 AND org_id = $2",
                body.assigned_to_user_id, org_id,
            )
            if user_name is None and not await conn.fetchval(
                "SELECT 1 FROM users WHERE id = $1 AND org_id = $2",
                body.assigned_to_user_id, org_id,
            ):
                raise HTTPException(status_code=404, detail="User not found in org")
        else:
            team_row = await conn.fetchrow(
                "SELECT name FROM teams WHERE id = $1 AND org_id = $2",
                body.assigned_to_team_id, org_id,
            )
            if team_row is None:
                raise HTTPException(status_code=404, detail="Team not found in org")
            team_name = team_row["name"]

        assignment_id = await conn.fetchval(
            """
            INSERT INTO staff_assignments
                (org_id, entity_id, assigned_to_user_id, assigned_to_team_id,
                 role_label, assigned_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            org_id, body.entity_id, body.assigned_to_user_id,
            body.assigned_to_team_id, body.role_label, actor_id,
        )
        await write_audit_log(
            conn, org_id=org_id, action="create_staff_assignment",
            table_name="staff_assignments", record_id=assignment_id,
            new={
                "entity_id": str(body.entity_id),
                "assigned_to_user_id": str(body.assigned_to_user_id)
                if body.assigned_to_user_id else None,
                "assigned_to_team_id": str(body.assigned_to_team_id)
                if body.assigned_to_team_id else None,
                "role_label": body.role_label,
                "assigned_by": str(actor_id),
            },
            actor=actor_id,
        )
    return Assignment(
        id=assignment_id,
        entity_id=body.entity_id,
        entity_name=entity["display_name"],
        assigned_to_user_id=body.assigned_to_user_id,
        assigned_to_user_name=user_name,
        assigned_to_team_id=body.assigned_to_team_id,
        assigned_to_team_name=team_name,
        role_label=body.role_label,
    )


@router.delete("/admin/staff/assignments/{assignment_id}", status_code=200)
async def delete_assignment(request: Request, assignment_id: UUID):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM staff_assignments WHERE id = $1 AND org_id = $2 RETURNING id",
            assignment_id, org_id,
        )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"ok": True}
