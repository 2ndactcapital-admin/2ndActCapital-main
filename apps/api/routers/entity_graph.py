"""Entity hierarchy / graph endpoints (Sprint 15 + Sprint 18).

Routes:
  GET    /entities/{entity_id}/tree                  — subtree (all auth)
  GET    /entities/{entity_id}/lookthrough            — look-through (all auth)
  GET    /entities/{entity_id}/relationships          — parents + children (all auth)
  POST   /entity-relationships                        — create relationship (staff)
  PATCH  /entity-relationships/{rel_id}              — amend relationship (staff)
  DELETE /entity-relationships/{rel_id}              — soft-delete relationship (staff)
  POST   /entity-groups                               — create group (staff)
  POST   /entity-groups/{group_id}/members            — add member (staff)
  DELETE /entity-groups/{group_id}/members/{entity_id} — remove member (staff)
  GET    /entity-groups                               — list groups (all auth)
  GET    /entity-groups/{group_id}                    — group detail + members (all auth)

  Sprint 18 — Ownership Editing & Time-Travel:
  GET    /entities/{entity_id}/ownership              — both-sides view + as_of (all auth)
  POST   /entities/{entity_id}/ownership              — create ownership edge (staff)
  PATCH  /entity-relationships/{rel_id}/ownership     — amend pct/note (staff)
  DELETE /entity-relationships/{rel_id}/ownership     — soft-delete + log (staff)
  GET    /entities/{entity_id}/ownership/history      — change log newest-first (all auth)
"""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.entity_graph import (
    detect_cycle,
    get_children,
    get_lookthrough,
    get_parents,
    get_subtree,
)
from services.permissions import require_staff
from services.users import ensure_user

router = APIRouter(tags=["entity_graph"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class RelationshipCreate(BaseModel):
    from_entity_id: UUID
    to_entity_id: UUID
    relationship_type: str = "ownership"
    ownership_pct: Optional[float] = None
    notes: Optional[str] = None


class RelationshipPatch(BaseModel):
    ownership_pct: Optional[float] = None
    notes: Optional[str] = None
    relationship_type: Optional[str] = None


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class GroupMemberAdd(BaseModel):
    entity_id: UUID


class OwnershipCreate(BaseModel):
    direction: str  # 'owns' | 'owned_by'
    counterparty_id: UUID
    ownership_pct: Optional[Decimal] = None
    effective_date: Optional[str] = None  # YYYY-MM-DD
    note: Optional[str] = None
    change_reason: Optional[str] = None


class OwnershipPatch(BaseModel):
    ownership_pct: Optional[Decimal] = None
    note: Optional[str] = None
    effective_date: Optional[str] = None
    change_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel_row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("id", "org_id", "from_entity_id", "to_entity_id", "created_by"):
        if key in d and d[key] is not None:
            d[key] = str(d[key])
    if "ownership_pct" in d and d["ownership_pct"] is not None:
        d["ownership_pct"] = float(d["ownership_pct"])
    if "effective_date" in d and d["effective_date"] is not None:
        d["effective_date"] = str(d["effective_date"])
    return d


# ---------------------------------------------------------------------------
# Read endpoints (no staff required)
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/tree")
async def get_entity_tree(entity_id: UUID, request: Request):
    pool = await get_pool()
    org_id = get_org_id(request)
    try:
        tree = await get_subtree(pool, org_id, str(entity_id), max_depth=20)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return tree


@router.get("/entities/{entity_id}/lookthrough")
async def get_entity_lookthrough(entity_id: UUID, request: Request):
    pool = await get_pool()
    org_id = get_org_id(request)
    try:
        lookthrough = await get_lookthrough(pool, org_id, str(entity_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"root_entity_id": str(entity_id), "lookthrough": lookthrough}


@router.get("/entities/{entity_id}/relationships")
async def get_entity_relationships(entity_id: UUID, request: Request):
    pool = await get_pool()
    org_id = get_org_id(request)
    children = await get_children(pool, org_id, str(entity_id))
    parents = await get_parents(pool, org_id, str(entity_id))
    return {
        "entity_id": str(entity_id),
        "parents": parents,
        "children": children,
    }


# ---------------------------------------------------------------------------
# Relationship write endpoints (staff required)
# ---------------------------------------------------------------------------


@router.post("/entity-relationships", status_code=201)
async def create_relationship(body: RelationshipCreate, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    from_id = str(body.from_entity_id)
    to_id = str(body.to_entity_id)

    if from_id == to_id:
        raise HTTPException(status_code=400, detail="An entity cannot own itself")

    # Only 'ownership' edges carry/validate a percentage. Other types such as
    # 'beneficiary' (SOC Phase 1) are accepted with a null ownership_pct — a
    # beneficiary confers visibility, not an economic ownership share.
    if (
        body.relationship_type == "ownership"
        and body.ownership_pct is not None
        and not (0 <= body.ownership_pct <= 100)
    ):
        raise HTTPException(status_code=400, detail="ownership_pct must be 0-100")

    # Cycle check (uses its own connection from pool)
    has_cycle = await detect_cycle(pool, org_id, from_id, to_id)
    if has_cycle:
        raise HTTPException(
            status_code=400,
            detail="Adding this relationship would create a cycle",
        )

    async with pool.acquire() as conn:
        ownership_warning = False
        if body.relationship_type == "ownership" and body.ownership_pct is not None:
            existing_sum = await conn.fetchval(
                """
                SELECT COALESCE(SUM(ownership_pct), 0)
                FROM entity_relationships
                WHERE to_entity_id = $1
                  AND org_id = $2
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                body.to_entity_id,
                org_id,
            )
            if float(existing_sum) + body.ownership_pct > 100:
                ownership_warning = True

        user_id = await ensure_user(conn, request)

        row = await conn.fetchrow(
            """
            INSERT INTO entity_relationships
                (org_id, from_entity_id, to_entity_id, relationship_type,
                 notes, ownership_pct, valid_from, system_from, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, now(), now(), $7)
            RETURNING id, org_id, from_entity_id, to_entity_id, relationship_type,
                      notes, ownership_pct, valid_from, valid_to,
                      system_from, system_to, created_by, created_at
            """,
            org_id,
            body.from_entity_id,
            body.to_entity_id,
            body.relationship_type,
            body.notes,
            body.ownership_pct,
            user_id,
        )

    result = _rel_row_to_dict(row)
    if ownership_warning:
        result["ownership_warning"] = True

    await write_audit_log(
        org_id=org_id,
        action="create_relationship",
        table_name="entity_relationships",
        record_id=result["id"],
        new=result,
        actor=user_id,
    )

    return result


@router.patch("/entity-relationships/{rel_id}")
async def amend_relationship(rel_id: UUID, body: RelationshipPatch, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            """
            SELECT id, org_id, from_entity_id, to_entity_id, relationship_type,
                   notes, ownership_pct, valid_from, valid_to,
                   system_from, system_to, created_by, created_at
            FROM entity_relationships
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            rel_id,
            org_id,
        )
        if not current:
            raise HTTPException(status_code=404, detail="Relationship not found or already deleted")

        new_rel_type = body.relationship_type if body.relationship_type is not None else current["relationship_type"]
        new_ownership_pct = body.ownership_pct if body.ownership_pct is not None else (
            float(current["ownership_pct"]) if current["ownership_pct"] is not None else None
        )
        new_notes = body.notes if body.notes is not None else current["notes"]

        if (
            new_rel_type == "ownership"
            and new_ownership_pct is not None
            and not (0 <= new_ownership_pct <= 100)
        ):
            raise HTTPException(status_code=400, detail="ownership_pct must be 0-100")

        user_id = await ensure_user(conn, request)

        async with conn.transaction():
            # Close old row (four-timestamp bi-temporal)
            await conn.execute(
                """
                UPDATE entity_relationships
                SET valid_to = now(), system_to = now()
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                rel_id,
                org_id,
            )

            # Insert new row
            new_row = await conn.fetchrow(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id, relationship_type,
                     notes, ownership_pct, valid_from, system_from, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, now(), now(), $7)
                RETURNING id, org_id, from_entity_id, to_entity_id, relationship_type,
                          notes, ownership_pct, valid_from, valid_to,
                          system_from, system_to, created_by, created_at
                """,
                org_id,
                current["from_entity_id"],
                current["to_entity_id"],
                new_rel_type,
                new_notes,
                new_ownership_pct,
                user_id,
            )

    result = _rel_row_to_dict(new_row)

    await write_audit_log(
        org_id=org_id,
        action="amend_relationship",
        table_name="entity_relationships",
        record_id=result["id"],
        old=_rel_row_to_dict(current),
        new=result,
        actor=user_id,
    )

    return result


@router.delete("/entity-relationships/{rel_id}", status_code=204)
async def delete_relationship(rel_id: UUID, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id FROM entity_relationships
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            rel_id,
            org_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Relationship not found or already deleted")

        user_id = await ensure_user(conn, request)

        await conn.execute(
            """
            UPDATE entity_relationships
            SET valid_to = now(), system_to = now()
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            rel_id,
            org_id,
        )

    await write_audit_log(
        org_id=org_id,
        action="delete_relationship",
        table_name="entity_relationships",
        record_id=str(rel_id),
        actor=user_id,
    )


# ---------------------------------------------------------------------------
# Entity group endpoints
# ---------------------------------------------------------------------------


@router.post("/entity-groups", status_code=201)
async def create_entity_group(body: GroupCreate, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

        row = await conn.fetchrow(
            """
            INSERT INTO entity_groups (org_id, name, description, created_by)
            VALUES ($1, $2, $3, $4)
            RETURNING id, org_id, name, description, created_by, created_at, updated_at
            """,
            UUID(org_id),
            body.name,
            body.description,
            UUID(user_id),
        )

    result = {k: (str(v) if isinstance(v, UUID) else v) for k, v in dict(row).items()}
    return result


@router.post("/entity-groups/{group_id}/members", status_code=201)
async def add_group_member(group_id: UUID, body: GroupMemberAdd, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        group = await conn.fetchrow(
            "SELECT id FROM entity_groups WHERE id = $1 AND org_id = $2",
            group_id,
            UUID(org_id),
        )
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        user_id = await ensure_user(conn, request)

        await conn.execute(
            """
            INSERT INTO entity_group_members (org_id, group_id, entity_id, added_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (group_id, entity_id) DO NOTHING
            """,
            UUID(org_id),
            group_id,
            body.entity_id,
            UUID(user_id),
        )

    return {"group_id": str(group_id), "entity_id": str(body.entity_id)}


@router.delete("/entity-groups/{group_id}/members/{entity_id}", status_code=204)
async def remove_group_member(group_id: UUID, entity_id: UUID, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM entity_group_members
            WHERE group_id = $1 AND entity_id = $2 AND org_id = $3
            """,
            group_id,
            entity_id,
            UUID(org_id),
        )


@router.get("/entity-groups")
async def list_entity_groups(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT g.id, g.name, g.description, g.created_at,
                   COUNT(m.entity_id) AS member_count
            FROM entity_groups g
            LEFT JOIN entity_group_members m
              ON m.group_id = g.id AND m.org_id = g.org_id
            WHERE g.org_id = $1
            GROUP BY g.id
            ORDER BY g.created_at DESC
            """,
            UUID(org_id),
        )

    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["member_count"] = int(d["member_count"])
        result.append(d)
    return result


@router.get("/entity-groups/{group_id}")
async def get_entity_group(group_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        group = await conn.fetchrow(
            """
            SELECT id, name, description, created_by, created_at, updated_at
            FROM entity_groups
            WHERE id = $1 AND org_id = $2
            """,
            group_id,
            UUID(org_id),
        )
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        members = await conn.fetch(
            """
            SELECT m.entity_id, e.display_name, e.entity_type, m.added_at
            FROM entity_group_members m
            JOIN entities e ON e.id = m.entity_id
            WHERE m.group_id = $1 AND m.org_id = $2
            ORDER BY m.added_at ASC
            """,
            group_id,
            UUID(org_id),
        )

    member_list = []
    for m in members:
        md = dict(m)
        md["entity_id"] = str(md["entity_id"])
        member_list.append(md)

    g = dict(group)
    g["id"] = str(g["id"])
    if g.get("created_by"):
        g["created_by"] = str(g["created_by"])
    g["members"] = member_list
    return g


# ---------------------------------------------------------------------------
# Ownership endpoints — Sprint 18
# ---------------------------------------------------------------------------

_OWN_FIELDS = (
    "r.id, r.from_entity_id, r.to_entity_id, r.ownership_pct, "
    "r.notes, r.effective_date, r.change_reason, r.valid_from"
)


async def _log_ownership_change(
    conn, org_id, relationship_id, from_id, to_id,
    prior_pct, new_pct, change_reason, changed_by
):
    """Insert into ownership_change_log. Gracefully skips on schema mismatch."""
    try:
        await conn.execute(
            """
            INSERT INTO ownership_change_log
                (org_id, relationship_id, from_entity_id, to_entity_id,
                 prior_pct, new_pct, change_reason, changed_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            org_id, relationship_id, from_id, to_id,
            prior_pct, new_pct, change_reason, changed_by,
        )
    except Exception:
        pass  # non-fatal — main transaction already committed


def _ownership_row(row, counterparty_row) -> dict:
    d = {
        "relationship_id": str(row["id"]),
        "ownership_pct": float(row["ownership_pct"]) if row["ownership_pct"] is not None else None,
        "notes": row["notes"],
        "effective_date": str(row["effective_date"]) if row["effective_date"] else None,
        "change_reason": row.get("change_reason"),
        "valid_from": row["valid_from"].isoformat() if row.get("valid_from") else None,
    }
    if counterparty_row:
        d["counterparty"] = {
            "id": str(counterparty_row["id"]),
            "display_name": counterparty_row["display_name"],
            "entity_type": counterparty_row["entity_type"],
        }
    return d


@router.get("/entities/{entity_id}/ownership")
async def get_entity_ownership(
    entity_id: UUID,
    request: Request,
    as_of: Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    org_id = get_org_id(request)
    pool = await get_pool()

    # Default: current active rows. With as_of: valid-time slice.
    if as_of:
        time_filter = (
            "AND r.valid_from <= $3::date "
            "AND (r.valid_to IS NULL OR r.valid_to > $3::date) "
            "AND r.system_to IS NULL"
        )
        as_of_param = as_of
    else:
        time_filter = "AND r.valid_to IS NULL AND r.system_to IS NULL"
        as_of_param = None

    async with pool.acquire() as conn:
        owns_rows = await conn.fetch(
            f"""
            SELECT {_OWN_FIELDS},
                   e.id AS cp_id, e.display_name, e.entity_type
            FROM entity_relationships r
            JOIN entities e ON e.id = r.to_entity_id
            WHERE r.from_entity_id = $1
              AND r.org_id = $2
              AND r.relationship_type = 'ownership'
              {time_filter}
            ORDER BY e.display_name
            """,
            entity_id,
            org_id,
            *([as_of_param] if as_of_param else []),
        )

        owned_by_rows = await conn.fetch(
            f"""
            SELECT {_OWN_FIELDS},
                   e.id AS cp_id, e.display_name, e.entity_type
            FROM entity_relationships r
            JOIN entities e ON e.id = r.from_entity_id
            WHERE r.to_entity_id = $1
              AND r.org_id = $2
              AND r.relationship_type = 'ownership'
              {time_filter}
            ORDER BY e.display_name
            """,
            entity_id,
            org_id,
            *([as_of_param] if as_of_param else []),
        )

    def _to_item(row):
        cp = {
            "id": str(row["cp_id"]),
            "display_name": row["display_name"],
            "entity_type": row["entity_type"],
        }
        return {
            "relationship_id": str(row["id"]),
            "counterparty": cp,
            "ownership_pct": float(row["ownership_pct"]) if row["ownership_pct"] is not None else None,
            "notes": row["notes"],
            "effective_date": str(row["effective_date"]) if row.get("effective_date") else None,
            "change_reason": row.get("change_reason"),
        }

    owned_by_list = [_to_item(r) for r in owned_by_rows]
    total_pct = sum(
        (i["ownership_pct"] or 0.0) for i in owned_by_list
    )

    return {
        "entity_id": str(entity_id),
        "as_of": as_of,
        "owns": [_to_item(r) for r in owns_rows],
        "owned_by": owned_by_list,
        "owned_by_total_pct": round(total_pct, 6),
    }


@router.post("/entities/{entity_id}/ownership", status_code=201)
async def create_ownership(
    entity_id: UUID,
    body: OwnershipCreate,
    request: Request,
):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    if body.direction not in ("owns", "owned_by"):
        raise HTTPException(status_code=400, detail="direction must be 'owns' or 'owned_by'")

    counterparty_id = str(body.counterparty_id)
    this_id = str(entity_id)

    if this_id == counterparty_id:
        raise HTTPException(status_code=400, detail="An entity cannot own itself")

    if body.ownership_pct is not None and not (0 <= body.ownership_pct <= 100):
        raise HTTPException(status_code=400, detail="ownership_pct must be 0–100")

    if body.direction == "owns":
        from_id, to_id = this_id, counterparty_id
    else:
        from_id, to_id = counterparty_id, this_id

    has_cycle = await detect_cycle(pool, org_id, from_id, to_id)
    if has_cycle:
        raise HTTPException(
            status_code=400,
            detail="Adding this relationship would create a cycle",
        )

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

        row = await conn.fetchrow(
            """
            INSERT INTO entity_relationships
                (org_id, from_entity_id, to_entity_id, relationship_type,
                 notes, ownership_pct, effective_date, change_reason,
                 change_source_type, valid_from, system_from, created_by)
            VALUES ($1, $2, $3, 'ownership', $4, $5, $6::date, $7,
                    'api', now(), now(), $8)
            RETURNING id, org_id, from_entity_id, to_entity_id, relationship_type,
                      notes, ownership_pct, effective_date, change_reason,
                      valid_from, valid_to, system_from, system_to, created_by, created_at
            """,
            org_id,
            UUID(from_id),
            UUID(to_id),
            body.note,
            body.ownership_pct,
            body.effective_date,
            body.change_reason or "initial",
            user_id,
        )

        new_rel_id = row["id"]
        await _log_ownership_change(
            conn, org_id, new_rel_id,
            UUID(from_id), UUID(to_id),
            None, body.ownership_pct,
            body.change_reason or "initial",
            UUID(user_id),
        )

    result = _rel_row_to_dict(row)
    await write_audit_log(
        org_id=org_id,
        action="create_ownership",
        table_name="entity_relationships",
        record_id=result["id"],
        new=result,
        actor=user_id,
    )
    return result


@router.patch("/entity-relationships/{rel_id}/ownership")
async def amend_ownership(
    rel_id: UUID,
    body: OwnershipPatch,
    request: Request,
):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            """
            SELECT id, org_id, from_entity_id, to_entity_id, relationship_type,
                   notes, ownership_pct, effective_date, change_reason,
                   valid_from, valid_to, system_from, system_to, created_by, created_at
            FROM entity_relationships
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            rel_id,
            org_id,
        )
        if not current:
            raise HTTPException(status_code=404, detail="Relationship not found or already closed")
        if current["relationship_type"] != "ownership":
            raise HTTPException(status_code=400, detail="Relationship is not type 'ownership'")

        prior_pct = current["ownership_pct"]
        new_pct = body.ownership_pct if body.ownership_pct is not None else prior_pct
        new_notes = body.note if body.note is not None else current["notes"]
        new_effective_date = body.effective_date if body.effective_date is not None else (
            str(current["effective_date"]) if current["effective_date"] else None
        )
        new_change_reason = body.change_reason or "manual_edit"

        if new_pct is not None and not (0 <= new_pct <= 100):
            raise HTTPException(status_code=400, detail="ownership_pct must be 0–100")

        user_id = await ensure_user(conn, request)

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE entity_relationships
                SET valid_to = now(), system_to = now()
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                rel_id,
                org_id,
            )

            new_row = await conn.fetchrow(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id, relationship_type,
                     notes, ownership_pct, effective_date, change_reason,
                     change_source_type, valid_from, system_from, created_by)
                VALUES ($1, $2, $3, 'ownership', $4, $5, $6::date, $7,
                        'api', now(), now(), $8)
                RETURNING id, org_id, from_entity_id, to_entity_id, relationship_type,
                          notes, ownership_pct, effective_date, change_reason,
                          valid_from, valid_to, system_from, system_to, created_by, created_at
                """,
                org_id,
                current["from_entity_id"],
                current["to_entity_id"],
                new_notes,
                new_pct,
                new_effective_date,
                new_change_reason,
                user_id,
            )

            await _log_ownership_change(
                conn, org_id, new_row["id"],
                current["from_entity_id"], current["to_entity_id"],
                prior_pct, new_pct,
                new_change_reason,
                UUID(user_id),
            )

    result = _rel_row_to_dict(new_row)
    await write_audit_log(
        org_id=org_id,
        action="amend_ownership",
        table_name="entity_relationships",
        record_id=result["id"],
        old=_rel_row_to_dict(current),
        new=result,
        actor=user_id,
    )
    return result


@router.delete("/entity-relationships/{rel_id}/ownership", status_code=204)
async def delete_ownership(rel_id: UUID, request: Request):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id, from_entity_id, to_entity_id, ownership_pct
            FROM entity_relationships
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            rel_id,
            org_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Relationship not found or already deleted")

        user_id = await ensure_user(conn, request)

        await conn.execute(
            """
            UPDATE entity_relationships
            SET valid_to = now(), system_to = now()
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            rel_id,
            org_id,
        )

        await _log_ownership_change(
            conn, org_id, rel_id,
            existing["from_entity_id"], existing["to_entity_id"],
            existing["ownership_pct"], Decimal("0"),
            "deleted",
            UUID(user_id),
        )

    await write_audit_log(
        org_id=org_id,
        action="delete_ownership",
        table_name="entity_relationships",
        record_id=str(rel_id),
        actor=user_id,
    )


@router.get("/entities/{entity_id}/ownership/history")
async def get_ownership_history(entity_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, relationship_id, from_entity_id, to_entity_id,
                   prior_pct, new_pct, change_reason, changed_by, changed_at
            FROM ownership_change_log
            WHERE (from_entity_id = $1 OR to_entity_id = $1)
              AND org_id = $2
            ORDER BY changed_at DESC
            LIMIT 200
            """,
            entity_id,
            org_id,
        )

    result = []
    for row in rows:
        d = dict(row)
        for key in ("id", "relationship_id", "from_entity_id", "to_entity_id", "changed_by"):
            if d.get(key) is not None:
                d[key] = str(d[key])
        if d.get("prior_pct") is not None:
            d["prior_pct"] = float(d["prior_pct"])
        if d.get("new_pct") is not None:
            d["new_pct"] = float(d["new_pct"])
        if d.get("changed_at"):
            d["changed_at"] = d["changed_at"].isoformat()
        result.append(d)

    return {"entity_id": str(entity_id), "history": result}
