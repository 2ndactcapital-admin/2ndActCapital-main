from decimal import Decimal, getcontext
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


async def get_subtree(pool, org_id: str, root_entity_id: str, max_depth: int = 20) -> dict:
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
            root_entity_id,
            org_id,
        )
        if root_row is None:
            raise ValueError(f"Entity {root_entity_id} not found in org {org_id}")

        root_node = {
            "id": str(root_row["id"]),
            "display_name": root_row["display_name"],
            "entity_type": root_row["entity_type"],
            "ownership_pct": None,
            "depth": 0,
            "relationship_id": None,
            "children": [],
        }

        # BFS
        queue = deque()
        queue.append((root_node, 0, {root_entity_id}))

        while queue:
            current_node, depth, visited_path = queue.popleft()

            if depth >= max_depth:
                continue

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
                current_node["id"],
            )

            for row in rows:
                child_id = str(row["entity_id"])
                if child_id in visited_path:
                    # cycle guard
                    continue

                pct = row["ownership_pct"]
                child_node = {
                    "id": child_id,
                    "display_name": row["display_name"],
                    "entity_type": row["entity_type"],
                    "ownership_pct": str(Decimal(str(pct))) if pct is not None else None,
                    "depth": depth + 1,
                    "relationship_id": str(row["relationship_id"]),
                    "children": [],
                }
                current_node["children"].append(child_node)
                new_visited = visited_path | {child_id}
                queue.append((child_node, depth + 1, new_visited))

    return root_node


async def get_lookthrough(pool, org_id: str, root_entity_id: str) -> list[dict]:
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

            rows = await conn.fetch(
                """
                SELECT
                    er.to_entity_id  AS entity_id,
                    er.ownership_pct,
                    er.id            AS relationship_id,
                    e.display_name,
                    e.entity_type
                FROM entity_relationships er
                JOIN entities e
                  ON e.id = er.to_entity_id
                 AND e.org_id = $1
                WHERE er.org_id = $1
                  AND er.from_entity_id = $2
                  AND er.relationship_type = 'ownership'
                  AND er.valid_to IS NULL
                  AND er.system_to IS NULL
                  AND er.ownership_pct IS NOT NULL
                """,
                org_id,
                current_id,
            )

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


async def resolve_entity_set(pool, org_id: str, selector: dict) -> list[dict]:
    sel_type = selector.get("type")

    if sel_type == "entity":
        entity_id = selector["id"]
        return [{"entity_id": entity_id, "weight": "1.000000"}]

    elif sel_type == "subtree":
        root_id = selector["root_id"]
        result = [{"entity_id": root_id, "weight": "1.000000"}]
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
