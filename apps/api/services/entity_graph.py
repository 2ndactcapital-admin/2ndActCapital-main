from decimal import Decimal, getcontext
from uuid import UUID
from collections import deque

getcontext().prec = 28

ORG_ID = "00000000-0000-0000-0000-000000000001"


async def get_children(pool, org_id: str, entity_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                er.id            AS relationship_id,
                er.to_entity_id  AS entity_id,
                e.display_name,
                e.entity_type,
                er.ownership_pct
            FROM entity_relationships er
            JOIN entities e
              ON e.id = er.to_entity_id
             AND e.org_id = $1
            WHERE er.org_id = $1
              AND er.from_entity_id = $2
              AND er.relationship_type = 'ownership'
              AND er.valid_to IS NULL
              AND er.system_to IS NULL
            """,
            org_id,
            entity_id,
        )
    result = []
    for row in rows:
        pct = row["ownership_pct"]
        result.append(
            {
                "relationship_id": str(row["relationship_id"]),
                "entity_id": str(row["entity_id"]),
                "display_name": row["display_name"],
                "entity_type": row["entity_type"],
                "ownership_pct": str(Decimal(str(pct))) if pct is not None else None,
            }
        )
    return result


async def get_parents(pool, org_id: str, entity_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                er.id              AS relationship_id,
                er.from_entity_id  AS entity_id,
                e.display_name,
                e.entity_type,
                er.ownership_pct
            FROM entity_relationships er
            JOIN entities e
              ON e.id = er.from_entity_id
             AND e.org_id = $1
            WHERE er.org_id = $1
              AND er.to_entity_id = $2
              AND er.relationship_type = 'ownership'
              AND er.valid_to IS NULL
              AND er.system_to IS NULL
            """,
            org_id,
            entity_id,
        )
    result = []
    for row in rows:
        pct = row["ownership_pct"]
        result.append(
            {
                "relationship_id": str(row["relationship_id"]),
                "entity_id": str(row["entity_id"]),
                "display_name": row["display_name"],
                "entity_type": row["entity_type"],
                "ownership_pct": str(Decimal(str(pct))) if pct is not None else None,
            }
        )
    return result


async def _edges_and_names(conn, org_id, ids):
    """PERMS1 (WF-03) — the current ownership edges touching ``ids`` from BOTH ownership tables
    (services.ownership_read.ownership_edges — the read the CRM detail page uses), plus the names of every
    entity those edges reach (current rows of the org only)."""
    from services.ownership_read import ownership_edges

    edges = await ownership_edges(conn, org_id, list(ids))
    other = {str(e["parent_id"]) for e in edges} | {str(e["child_id"]) for e in edges}
    rows = await conn.fetch(
        "SELECT id, display_name, entity_type FROM entities WHERE org_id = $1 AND id = ANY($2::uuid[]) "
        "AND valid_to IS NULL AND system_to IS NULL", org_id, [UUID(x) for x in other]) if other else []
    return edges, {str(r["id"]): r for r in rows}


def _pct(v):
    return str(Decimal(str(v))) if v is not None else None


async def get_subtree(pool, org_id: str, root_entity_id: str, max_depth: int = 20) -> dict:
    """The entity's ownership tree: ``children`` = what it owns (downward), and — PERMS1 (WF-03) — ``owners`` =
    who owns it (upward), each owner with its own ``owners``, and ``effective_pct`` = that owner's look-through
    share of the root (product of the percentages along the path, 0–100). Both directions read BOTH ownership
    tables through ``ownership_edges``, so the hierarchy graph shows the same owners and edges as the detail
    page (it used to read entity_relationships only, downward only → Hargrove Capital LLC showed a lone root)."""
    async with pool.acquire() as conn:
        root_row = await conn.fetchrow(
            """
            SELECT id, display_name, entity_type
            FROM entities
            WHERE id = $1
              AND org_id = $2
              AND valid_to IS NULL
              AND system_to IS NULL
            """,
            UUID(str(root_entity_id)),
            UUID(str(org_id)),
        )
        if root_row is None:
            raise ValueError(f"Entity {root_entity_id} not found in org {org_id}")

        root_id = str(root_row["id"])
        root_node = {
            "id": root_id,
            "display_name": root_row["display_name"],
            "entity_type": root_row["entity_type"],
            "ownership_pct": None,
            "depth": 0,
            "relationship_id": None,
            "children": [],
            "owners": [],
        }

        # Downward BFS (what the root owns).
        queue = deque([(root_node, 0, frozenset([root_id]))])
        while queue:
            current, depth, path = queue.popleft()
            if depth >= max_depth:
                continue
            edges, names = await _edges_and_names(conn, UUID(str(org_id)), [UUID(current["id"])])
            for e in sorted(edges, key=lambda x: str(x["child_id"])):
                if str(e["parent_id"]) != current["id"]:
                    continue
                cid = str(e["child_id"])
                if cid in path or cid not in names:
                    continue  # cycle guard / entity not current
                child = {
                    "id": cid,
                    "display_name": names[cid]["display_name"],
                    "entity_type": names[cid]["entity_type"],
                    "ownership_pct": _pct(e["ownership_pct"]),
                    "depth": depth + 1,
                    "relationship_id": str(e["id"]) if e["source"] == "entity_relationships" else None,
                    "source": e["source"],
                    "children": [],
                }
                current["children"].append(child)
                queue.append((child, depth + 1, path | {cid}))

        # Upward BFS (who owns the root) — PERMS1 (WF-03).
        queue = deque([(root_node, 0, frozenset([root_id]), Decimal("100"))])
        while queue:
            current, depth, path, share = queue.popleft()
            if depth >= max_depth:
                continue
            edges, names = await _edges_and_names(conn, UUID(str(org_id)), [UUID(current["id"])])
            for e in sorted(edges, key=lambda x: str(x["parent_id"])):
                if str(e["child_id"]) != current["id"]:
                    continue
                oid = str(e["parent_id"])
                if oid in path or oid not in names:
                    continue
                pct = e["ownership_pct"]
                eff = (share * Decimal(str(pct)) / Decimal("100")) if pct is not None else None
                owner = {
                    "id": oid,
                    "display_name": names[oid]["display_name"],
                    "entity_type": names[oid]["entity_type"],
                    "ownership_pct": _pct(pct),
                    "effective_pct": f"{eff:.6f}" if eff is not None else None,
                    "depth": -(depth + 1),
                    "relationship_id": str(e["id"]) if e["source"] == "entity_relationships" else None,
                    "source": e["source"],
                    "owners": [],
                }
                current["owners"].append(owner)
                if eff is not None:
                    queue.append((owner, depth + 1, path | {oid}, eff))
                else:
                    queue.append((owner, depth + 1, path | {oid}, Decimal("0")))

    return root_node


async def get_lookthrough(pool, org_id: str, root_entity_id: str, *, both_tables: bool = False) -> list[dict]:
    """Downward effective percentages. ``both_tables`` (PERMS1, WF-03 — the hierarchy graph only) reads BOTH
    ownership tables like the detail page; the default stays entity_relationships only, because
    portfolio_rollup builds entity_holdings from this function and a silent change of its input is out of scope
    here (recorded in PROJECT_STATUS)."""
    async with pool.acquire() as conn:
        # Fetch root entity to confirm it exists
        root_row = await conn.fetchrow(
            """
            SELECT id, display_name, entity_type
            FROM entities
            WHERE id = $1
              AND org_id = $2
            """,
            root_entity_id,
            org_id,
        )
        if root_row is None:
            raise ValueError(f"Entity {root_entity_id} not found in org {org_id}")

        # Accumulate effective percentages by descendant entity_id
        effective: dict[str, Decimal] = {}
        entity_info: dict[str, dict] = {}

        # BFS: each queue item is (entity_id, cumulative_pct, visited_path)
        queue = deque()
        queue.append((root_entity_id, Decimal("1"), frozenset([root_entity_id])))

        while queue:
            current_id, cumulative_pct, visited_path = queue.popleft()

            if not both_tables:
                rows = await conn.fetch(
                    """
                    SELECT er.to_entity_id AS entity_id, er.ownership_pct, er.id AS relationship_id,
                           e.display_name, e.entity_type
                    FROM entity_relationships er
                    JOIN entities e ON e.id = er.to_entity_id AND e.org_id = $1
                    WHERE er.org_id = $1 AND er.from_entity_id = $2 AND er.relationship_type = 'ownership'
                      AND er.valid_to IS NULL AND er.system_to IS NULL AND er.ownership_pct IS NOT NULL
                    """,
                    org_id,
                    current_id,
                )
            else:
                # PERMS1 (WF-03) — both ownership tables (ownership_edges), downward, percentage required.
                edges, names = await _edges_and_names(conn, UUID(str(org_id)), [UUID(str(current_id))])
                rows = [
                    {"entity_id": e["child_id"], "ownership_pct": e["ownership_pct"], "relationship_id": e["id"],
                 "display_name": names[str(e["child_id"])]["display_name"],
                 "entity_type": names[str(e["child_id"])]["entity_type"]}
                for e in edges
                if str(e["parent_id"]) == str(current_id) and e["ownership_pct"] is not None
                and str(e["child_id"]) in names
            ]

            for row in rows:
                child_id = str(row["entity_id"])
                if child_id in visited_path:
                    continue

                edge_pct = Decimal(str(row["ownership_pct"])) / Decimal("100")
                child_effective = cumulative_pct * edge_pct

                if child_id not in effective:
                    effective[child_id] = Decimal("0")
                effective[child_id] += child_effective

                entity_info[child_id] = {
                    "display_name": row["display_name"],
                    "entity_type": row["entity_type"],
                }

                new_visited = visited_path | {child_id}
                queue.append((child_id, child_effective, new_visited))

    result = []
    for entity_id, eff_pct in effective.items():
        info = entity_info[entity_id]
        result.append(
            {
                "entity_id": entity_id,
                "display_name": info["display_name"],
                "entity_type": info["entity_type"],
                "effective_pct": f"{eff_pct:.6f}",
            }
        )
    return result


async def get_beneficiary_visible(pool, org_id: str, root_entity_id: str) -> set[str]:
    """Entity ids visible to ``root_entity_id`` through beneficiary edges.

    A ``relationship_type = 'beneficiary'`` edge (from the member entity to the
    entity they benefit from) confers visibility the SAME WAY ownership does —
    even though ``ownership_pct`` is null for beneficiary edges (SOC Phase 1).
    From each entity the root is a beneficiary of, visibility flows down that
    entity's ownership subtree as well, mirroring an owner's look-through.
    """
    async with pool.acquire() as conn:
        targets = await conn.fetch(
            """
            SELECT er.to_entity_id
            FROM entity_relationships er
            WHERE er.org_id = $1
              AND er.from_entity_id = $2
              AND er.relationship_type = 'beneficiary'
              AND er.valid_to IS NULL
              AND er.system_to IS NULL
            """,
            org_id,
            root_entity_id,
        )

    visible: set[str] = set()
    for t in targets:
        target_id = str(t["to_entity_id"])
        if target_id in visible:
            continue
        visible.add(target_id)
        # Look through the beneficiary target's ownership subtree too.
        for d in await get_lookthrough(pool, org_id, target_id):
            visible.add(d["entity_id"])
    return visible


async def resolve_entity_set(pool, org_id: str, selector: dict) -> list[dict]:
    sel_type = selector.get("type")

    if sel_type == "entity":
        entity_id = selector["id"]
        return [{"entity_id": entity_id, "weight": "1.000000"}]

    elif sel_type == "subtree":
        root_id = selector["root_id"]
        result = [{"entity_id": root_id, "weight": "1.000000"}]
        seen = {root_id}
        descendants = await get_lookthrough(pool, org_id, root_id)
        for d in descendants:
            # effective_pct from get_lookthrough is already a 0–1 fraction
            weight = Decimal(d["effective_pct"])
            result.append(
                {
                    "entity_id": d["entity_id"],
                    "weight": f"{weight:.6f}",
                }
            )
            seen.add(d["entity_id"])
        # Beneficiary edges confer visibility the same way ownership does. They
        # carry no ownership_pct, so they enter the visible set at full weight
        # (1.0) rather than an economic fraction.
        for ent_id in await get_beneficiary_visible(pool, org_id, root_id):
            if ent_id not in seen:
                result.append({"entity_id": ent_id, "weight": "1.000000"})
                seen.add(ent_id)
        return result

    elif sel_type == "group":
        group_id = selector["group_id"]
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT entity_id
                FROM entity_group_members
                WHERE org_id = $1
                  AND group_id = $2
                """,
                org_id,
                group_id,
            )
        return [
            {"entity_id": str(row["entity_id"]), "weight": "1.000000"}
            for row in rows
        ]

    elif sel_type == "all":
        # Top-level entities: entities that have no active ownership parent
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.id, e.display_name, e.entity_type
                FROM entities e
                WHERE e.org_id = $1
                  AND e.valid_to IS NULL
                  AND e.system_to IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM entity_relationships er
                      WHERE er.org_id = $1
                        AND er.to_entity_id = e.id
                        AND er.relationship_type = 'ownership'
                        AND er.valid_to IS NULL
                        AND er.system_to IS NULL
                  )
                """,
                org_id,
            )
        return [
            {"entity_id": str(row["id"]), "weight": "1.000000"}
            for row in rows
        ]

    else:
        raise ValueError(f"Unknown selector type: {sel_type!r}")


async def detect_cycle(pool, org_id: str, from_id: str, to_id: str) -> bool:
    # Self-loop
    if from_id == to_id:
        return True

    # Would adding from_id -> to_id create a cycle?
    # A cycle exists if from_id is already a descendant of to_id.
    # BFS from to_id following ownership edges downward (to_id's children subtree).
    async with pool.acquire() as conn:
        visited = set()
        queue = deque([to_id])

        while queue:
            current_id = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            rows = await conn.fetch(
                """
                SELECT to_entity_id
                FROM entity_relationships
                WHERE org_id = $1
                  AND from_entity_id = $2
                  AND relationship_type = 'ownership'
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                org_id,
                current_id,
            )

            for row in rows:
                child_id = str(row["to_entity_id"])
                if child_id == from_id:
                    return True
                if child_id not in visited:
                    queue.append(child_id)

    return False
