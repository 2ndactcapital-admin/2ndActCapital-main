"""Entity / CRM core endpoints.

All routes require a valid JWT (enforced by the global middleware in main.py)
and scope every query to the caller's org_id (read from JWT claims, falling
back to the default organization).
"""

from collections import deque
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from schemas.entities import (
    AttributeCreate,
    AttributeOut,
    EntityCreate,
    EntityDetail,
    EntityOut,
    EntityType,
    EntityUpdate,
    GraphEdge,
    GraphNode,
    OwnershipCreate,
    OwnershipGraph,
    OwnershipOut,
)
from services.audit import write_audit_log
from services.database import get_pool

router = APIRouter(tags=["entities"])

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

# Claim keys we accept for the caller's organization id.
ORG_ID_CLAIMS = (
    "org_id",
    "https://2ndactcapital.com/org_id",
    "https://api.2ndactcapital.com/org_id",
)

ENTITY_COLUMNS = (
    "id, org_id, entity_type, display_name, legal_name, tax_id, "
    "date_of_birth, country_of_formation, notes, valid_from, valid_to, "
    "system_from, system_to, created_at, updated_at"
)


def get_org_id(request: Request) -> str:
    """Resolve the caller's org_id from JWT claims, or the default org."""
    claims = getattr(request.state, "user", None) or {}
    for key in ORG_ID_CLAIMS:
        value = claims.get(key)
        if value:
            return value
    return DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
@router.get("/entities", response_model=list[EntityOut])
async def list_entities(
    request: Request,
    type: EntityType | None = None,
    search: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    org_id = get_org_id(request)
    conditions = [
        "org_id = $1",
        "valid_to IS NULL",
        "system_to IS NULL",
    ]
    params: list = [org_id]

    if type is not None:
        params.append(type.value)
        conditions.append(f"entity_type = ${len(params)}")
    if search:
        params.append(f"%{search}%")
        conditions.append(f"display_name ILIKE ${len(params)}")

    params.append(limit)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    query = (
        f"SELECT {ENTITY_COLUMNS} FROM entities "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY display_name ASC "
        f"LIMIT ${limit_pos} OFFSET ${offset_pos}"
    )

    pool = await get_pool()
    rows = await pool.fetch(query, *params)
    return [EntityOut(**dict(r)) for r in rows]


@router.post("/entities", response_model=EntityOut, status_code=201)
async def create_entity(request: Request, body: EntityCreate):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO entities (
                    org_id, entity_type, display_name, legal_name, tax_id,
                    date_of_birth, country_of_formation, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING {ENTITY_COLUMNS}
                """,
                org_id,
                body.entity_type.value,
                body.display_name,
                body.legal_name,
                body.tax_id,
                body.date_of_birth,
                body.country_of_formation,
                body.notes,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="create",
                table_name="entities",
                record_id=row["id"],
                new=dict(row),
            )
    return EntityOut(**dict(row))


# ---------------------------------------------------------------------------
# Single entity
# ---------------------------------------------------------------------------
async def _fetch_active_entity(conn, org_id: str, entity_id: UUID):
    return await conn.fetchrow(
        f"""
        SELECT {ENTITY_COLUMNS} FROM entities
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id,
        org_id,
    )


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await _fetch_active_entity(conn, org_id, entity_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        attributes = await conn.fetch(
            """
            SELECT id, entity_id, attribute_key, attribute_value, value_type,
                   created_at
            FROM entity_attributes
            WHERE entity_id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            ORDER BY attribute_key
            """,
            entity_id,
            org_id,
        )
        owners = await conn.fetch(
            """
            SELECT o.id, o.parent_id, o.child_id, o.ownership_pct,
                   o.ownership_type, p.display_name AS parent_name,
                   c.display_name AS child_name
            FROM entity_ownership o
            JOIN entities p ON p.id = o.parent_id
            JOIN entities c ON c.id = o.child_id
            WHERE o.child_id = $1 AND o.org_id = $2
              AND o.valid_to IS NULL AND o.system_to IS NULL
            ORDER BY o.ownership_pct DESC
            """,
            entity_id,
            org_id,
        )
        holdings = await conn.fetch(
            """
            SELECT o.id, o.parent_id, o.child_id, o.ownership_pct,
                   o.ownership_type, p.display_name AS parent_name,
                   c.display_name AS child_name
            FROM entity_ownership o
            JOIN entities p ON p.id = o.parent_id
            JOIN entities c ON c.id = o.child_id
            WHERE o.parent_id = $1 AND o.org_id = $2
              AND o.valid_to IS NULL AND o.system_to IS NULL
            ORDER BY o.ownership_pct DESC
            """,
            entity_id,
            org_id,
        )

    return EntityDetail(
        entity=EntityOut(**dict(row)),
        attributes=[AttributeOut(**dict(a)) for a in attributes],
        owners=[OwnershipOut(**dict(o)) for o in owners],
        holdings=[OwnershipOut(**dict(h)) for h in holdings],
    )


@router.put("/entities/{entity_id}", response_model=EntityOut)
async def update_entity(request: Request, entity_id: UUID, body: EntityUpdate):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _fetch_active_entity(conn, org_id, entity_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Entity not found")

            updates = body.model_dump(exclude_unset=True)
            # Normalize enum to its value for SQL.
            if isinstance(updates.get("entity_type"), EntityType):
                updates["entity_type"] = updates["entity_type"].value

            # Bi-temporal, FK-safe: archive the prior version as a new row with
            # system_to = now(), then update the live row (stable id) in place
            # with a fresh system_from.
            await conn.execute(
                """
                INSERT INTO entities (
                    org_id, entity_type, display_name, legal_name, tax_id,
                    date_of_birth, country_of_formation, notes,
                    valid_from, valid_to, system_from, system_to,
                    created_by, created_at, updated_at
                )
                SELECT org_id, entity_type, display_name, legal_name, tax_id,
                       date_of_birth, country_of_formation, notes,
                       valid_from, valid_to, system_from, now(),
                       created_by, created_at, updated_at
                FROM entities
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                entity_id,
                org_id,
            )

            editable = (
                "entity_type",
                "display_name",
                "legal_name",
                "tax_id",
                "date_of_birth",
                "country_of_formation",
                "notes",
            )
            set_clauses = ["system_from = now()", "updated_at = now()"]
            params: list = [entity_id, org_id]
            for field in editable:
                if field in updates:
                    params.append(updates[field])
                    set_clauses.append(f"{field} = ${len(params)}")

            updated = await conn.fetchrow(
                f"""
                UPDATE entities SET {', '.join(set_clauses)}
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                RETURNING {ENTITY_COLUMNS}
                """,
                *params,
            )

            await write_audit_log(
                conn,
                org_id=org_id,
                action="update",
                table_name="entities",
                record_id=entity_id,
                old=dict(current),
                new=dict(updated),
            )
    return EntityOut(**dict(updated))


# ---------------------------------------------------------------------------
# Attributes (supports inline "add attribute" on the detail page)
# ---------------------------------------------------------------------------
@router.post(
    "/entities/{entity_id}/attributes", response_model=AttributeOut, status_code=201
)
async def add_attribute(request: Request, entity_id: UUID, body: AttributeCreate):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity = await _fetch_active_entity(conn, org_id, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        row = await conn.fetchrow(
            """
            INSERT INTO entity_attributes (
                org_id, entity_id, attribute_key, attribute_value, value_type
            ) VALUES ($1, $2, $3, $4, $5)
            RETURNING id, entity_id, attribute_key, attribute_value, value_type,
                      created_at
            """,
            org_id,
            entity_id,
            body.attribute_key,
            body.attribute_value,
            body.value_type,
        )
    return AttributeOut(**dict(row))


# ---------------------------------------------------------------------------
# Ownership graph
# ---------------------------------------------------------------------------
@router.get("/entities/{entity_id}/ownership-graph", response_model=OwnershipGraph)
async def ownership_graph(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    max_depth = 5

    depths: dict[UUID, int] = {entity_id: 0}
    edges: dict[UUID, GraphEdge] = {}  # keyed by ownership row id to dedup

    async with pool.acquire() as conn:
        root = await _fetch_active_entity(conn, org_id, entity_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        queue: deque[UUID] = deque([entity_id])
        while queue:
            node = queue.popleft()
            node_depth = depths[node]
            if node_depth >= max_depth:
                continue

            rows = await conn.fetch(
                """
                SELECT id, parent_id, child_id, ownership_pct, ownership_type
                FROM entity_ownership
                WHERE (parent_id = $1 OR child_id = $1) AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                node,
                org_id,
            )
            for r in rows:
                edges.setdefault(
                    r["id"],
                    GraphEdge(
                        parent_id=r["parent_id"],
                        child_id=r["child_id"],
                        ownership_pct=float(r["ownership_pct"]),
                        ownership_type=r["ownership_type"],
                    ),
                )
                neighbor = r["child_id"] if r["parent_id"] == node else r["parent_id"]
                if neighbor not in depths:
                    depths[neighbor] = node_depth + 1
                    queue.append(neighbor)

        node_rows = await conn.fetch(
            """
            SELECT id, display_name, entity_type FROM entities
            WHERE id = ANY($1::uuid[]) AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            list(depths.keys()),
            org_id,
        )

    nodes = [
        GraphNode(
            id=r["id"],
            display_name=r["display_name"],
            entity_type=r["entity_type"],
            depth=depths[r["id"]],
        )
        for r in node_rows
    ]
    return OwnershipGraph(root_id=entity_id, nodes=nodes, edges=list(edges.values()))


# ---------------------------------------------------------------------------
# Ownership relationships
# ---------------------------------------------------------------------------
@router.post("/entity-ownership", response_model=OwnershipOut, status_code=201)
async def create_ownership(request: Request, body: OwnershipCreate):
    org_id = get_org_id(request)
    if body.parent_id == body.child_id:
        raise HTTPException(status_code=400, detail="An entity cannot own itself")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                """
                SELECT COALESCE(SUM(ownership_pct), 0)
                FROM entity_ownership
                WHERE child_id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                body.child_id,
                org_id,
            )
            if float(existing) + body.ownership_pct > 100:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Ownership for this entity would total "
                        f"{float(existing) + body.ownership_pct:.4f}%, exceeding 100%"
                    ),
                )

            row = await conn.fetchrow(
                """
                INSERT INTO entity_ownership (
                    org_id, parent_id, child_id, ownership_pct, ownership_type
                ) VALUES ($1, $2, $3, $4, $5)
                RETURNING id, parent_id, child_id, ownership_pct, ownership_type
                """,
                org_id,
                body.parent_id,
                body.child_id,
                body.ownership_pct,
                body.ownership_type,
            )
    return OwnershipOut(**dict(row))
