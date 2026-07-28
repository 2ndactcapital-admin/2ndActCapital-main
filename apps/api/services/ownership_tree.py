"""Ownership-tree graph builder (Sprint ownershiptreea — interactive graph).

Builds an interactive ownership/beneficiary tree for a focal entity, then
applies a visibility engine + the restricted-access wrapper on TOP so that a
node a viewer may not see (or an entire restricted subtree) is removed from the
data BEFORE it ever reaches the client.

This module is pure orchestration over EXISTING pieces — it introduces no new
schema and reimplements none of the SOC engines:

  * Graph edges      — ``entity_relationships`` (bitemporal), both
                       ``relationship_type = 'ownership'`` (carries
                       ``ownership_pct``) and ``'beneficiary'`` (no pct).
  * Staff visibility — ``services.staff_visibility.get_staff_visible_entity_ids``
                       (hierarchy + teams + assignment).
  * Member visibility— ``services.entity_graph.resolve_entity_set`` via the
                       user-keyed ``services.delegate_grants
                       .get_delegate_visible_entity_ids`` (ownership look-through
                       + beneficiary edges, scoped to the member's own grants).
  * Restricted filter— ``services.restricted_access.filter_restricted`` applied
                       as the FINAL step over whichever engine produced the set.

The two orchestrators (:func:`staff_tree`, :func:`member_tree`) never trust a
focal entity from the request for the member path — the member's own root
entities are derived from identity, and any explicit focal must fall inside the
member's already-restricted-filtered visible set or a ``PermissionError`` is
raised. A member therefore cannot reach another member's tree.
"""

from collections import deque
from decimal import Decimal

from services.delegate_grants import (
    get_delegate_visible_entity_ids,
    is_active_delegate,
)
from services.entity_graph import ORG_ID  # noqa: F401  (re-export parity)
from services.restricted_access import filter_restricted
from services.staff_visibility import get_staff_visible_entity_ids

EDGE_OWNERSHIP = "ownership"
EDGE_BENEFICIARY = "beneficiary"
DEFAULT_EDGE_TYPES = (EDGE_OWNERSHIP, EDGE_BENEFICIARY)
VALID_DIRECTIONS = ("owns", "owned_by")


def _pct_str(pct):
    return str(Decimal(str(pct))) if pct is not None else None


def parse_edge_types(raw) -> tuple:
    """Normalise a comma-separated ``edge_types`` param to a validated tuple.

    Unknown tokens are dropped; an empty/None input means "both". Order is
    fixed to ``DEFAULT_EDGE_TYPES`` so the query and legend stay stable.
    """
    if not raw:
        return DEFAULT_EDGE_TYPES
    if isinstance(raw, str):
        tokens = {t.strip() for t in raw.split(",") if t.strip()}
    else:
        tokens = {str(t).strip() for t in raw if str(t).strip()}
    kept = tuple(t for t in DEFAULT_EDGE_TYPES if t in tokens)
    return kept or DEFAULT_EDGE_TYPES


async def _fetch_entity(conn, org_id: str, entity_id: str):
    return await conn.fetchrow(
        """
        SELECT id, display_name, entity_type
        FROM entities
        WHERE id = $1 AND org_id = $2
        """,
        entity_id,
        org_id,
    )


async def build_tree(
    pool,
    org_id: str,
    root_id: str,
    *,
    direction: str = "owns",
    edge_types=DEFAULT_EDGE_TYPES,
    as_of: str | None = None,
    max_depth: int = 20,
) -> dict | None:
    """Build a nested ownership/beneficiary tree from ``root_id``.

    ``direction``:
      * ``"owns"``     — follow edges forward: children are entities the current
                          node owns / is a beneficiary of (``from → to``).
      * ``"owned_by"`` — reverse traversal: children are the current node's
                          owners / benefactors (``to → from``).

    ``edge_types`` restricts which ``relationship_type`` edges are walked.
    ``as_of`` (``YYYY-MM-DD``) selects a bitemporal valid-time slice; without it
    only the currently-active edges (``valid_to IS NULL``) are used. Cycle-safe
    per root-to-node path and bounded by ``max_depth``.

    Returns ``None`` if the focal entity does not exist in the org.
    """
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {VALID_DIRECTIONS}")
    edge_types = parse_edge_types(edge_types)

    # Column roles depend on traversal direction.
    if direction == "owns":
        match_col, next_col = "from_entity_id", "to_entity_id"
    else:
        match_col, next_col = "to_entity_id", "from_entity_id"

    if as_of:
        time_filter = (
            "AND er.valid_from <= $4::date "
            "AND (er.valid_to IS NULL OR er.valid_to > $4::date) "
            "AND er.system_to IS NULL"
        )
    else:
        time_filter = "AND er.valid_to IS NULL AND er.system_to IS NULL"

    query = f"""
        SELECT
            er.id              AS relationship_id,
            er.{next_col}      AS entity_id,
            er.relationship_type,
            er.ownership_pct,
            e.display_name,
            e.entity_type
        FROM entity_relationships er
        JOIN entities e
          ON e.id = er.{next_col}
         AND e.org_id = $1
        WHERE er.org_id = $1
          AND er.{match_col} = $2
          AND er.relationship_type = ANY($3::text[])
          {time_filter}
    """

    async with pool.acquire() as conn:
        root_row = await _fetch_entity(conn, org_id, root_id)
        if root_row is None:
            return None

        root_node = {
            "id": str(root_row["id"]),
            "display_name": root_row["display_name"],
            "entity_type": root_row["entity_type"],
            "edge_type": None,
            "ownership_pct": None,
            "relationship_id": None,
            "depth": 0,
            "children": [],
        }

        queue = deque([(root_node, 0, frozenset({root_id}))])
        while queue:
            node, depth, path = queue.popleft()
            if depth >= max_depth:
                continue

            args = [org_id, node["id"], list(edge_types)]
            if as_of:
                args.append(as_of)
            rows = await conn.fetch(query, *args)

            for row in rows:
                child_id = str(row["entity_id"])
                if child_id in path:
                    continue  # cycle guard
                is_ownership = row["relationship_type"] == EDGE_OWNERSHIP
                child = {
                    "id": child_id,
                    "display_name": row["display_name"],
                    "entity_type": row["entity_type"],
                    "edge_type": row["relationship_type"],
                    # Beneficiary edges carry no percentage.
                    "ownership_pct": _pct_str(row["ownership_pct"]) if is_ownership else None,
                    "relationship_id": str(row["relationship_id"]),
                    "depth": depth + 1,
                    "children": [],
                }
                node["children"].append(child)
                queue.append((child, depth + 1, path | {child_id}))

    return root_node


def prune_tree(node: dict | None, allowed_ids: set) -> dict | None:
    """Return a copy of ``node`` keeping only subtrees rooted at allowed ids.

    A node whose id is not in ``allowed_ids`` is dropped ENTIRELY — the node and
    its whole subtree vanish (the child branch is unreachable through a hidden
    parent). This is what makes a restricted entity "fully hidden": once
    ``filter_restricted`` removes it from ``allowed_ids``, pruning erases the
    node and everything beneath it from the data returned to the client.
    """
    if node is None or node["id"] not in allowed_ids:
        return None
    kept = []
    for child in node.get("children", []):
        pruned = prune_tree(child, allowed_ids)
        if pruned is not None:
            kept.append(pruned)
    new_node = dict(node)
    new_node["children"] = kept
    return new_node


async def staff_tree(
    pool,
    org_id: str,
    root_id: str,
    user_id: str,
    *,
    direction: str = "owns",
    edge_types=DEFAULT_EDGE_TYPES,
    as_of: str | None = None,
    max_depth: int = 20,
) -> dict | None:
    """Focal tree for a STAFF viewer.

    Visible set = staff visibility engine (assignment + team + hierarchy), then
    ``filter_restricted`` on top. The built tree is pruned to that set, so a
    staff user without visibility into an entity never receives it, and a
    restricted entity off their allow-list is removed with its whole subtree.
    Returns ``None`` when the focal entity itself is not visible.
    """
    allowed = await get_staff_visible_entity_ids(pool, user_id, org_id)
    allowed = await filter_restricted(pool, allowed, user_id, org_id)
    tree = await build_tree(
        pool,
        org_id,
        root_id,
        direction=direction,
        edge_types=edge_types,
        as_of=as_of,
        max_depth=max_depth,
    )
    if tree is None:
        return None
    return prune_tree(tree, allowed)


async def get_member_root_entity_ids(pool, org_id: str, user_id: str) -> list[str]:
    """The member's OWN root entities, derived from identity (never a request).

    A member's roots are the ``principal_entity_id``s of their currently-active
    ``delegate_grants`` (``delegate_user_id = user_id``) — the same linkage the
    member-side visibility engine already consumes. Deterministic order (oldest
    grant first); duplicates collapsed.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM delegate_grants
            WHERE org_id = $1 AND delegate_user_id = $2
            ORDER BY granted_at ASC
            """,
            org_id,
            user_id,
        )
    roots: list[str] = []
    for r in rows:
        if not is_active_delegate(r):
            continue
        pid = str(r["principal_entity_id"])
        if pid not in roots:
            roots.append(pid)
    return roots


async def member_tree(
    pool,
    org_id: str,
    user_id: str,
    *,
    focal_id: str | None = None,
    direction: str = "owns",
    edge_types=DEFAULT_EDGE_TYPES,
    as_of: str | None = None,
    max_depth: int = 20,
) -> dict:
    """Focal tree for a MEMBER viewer, scoped to their own entitlement.

    Visible set = member visibility engine (``resolve_entity_set`` over the
    member's own grant principals, via ``get_delegate_visible_entity_ids``),
    then ``filter_restricted`` on top. The focal defaults to the member's own
    primary root; an explicit ``focal_id`` MUST fall inside the restricted-
    filtered visible set or a ``PermissionError`` is raised — a member can never
    aim this route at another member's tree.
    """
    roots = await get_member_root_entity_ids(pool, org_id, user_id)
    allowed = await get_delegate_visible_entity_ids(pool, org_id, user_id)
    allowed = await filter_restricted(pool, allowed, user_id, org_id)

    candidate = focal_id or (roots[0] if roots else None)
    if candidate is None:
        return {"focal_entity_id": None, "roots": roots, "tree": None}
    if candidate not in allowed:
        raise PermissionError(
            "Entity is not within your visible ownership tree"
        )

    tree = await build_tree(
        pool,
        org_id,
        candidate,
        direction=direction,
        edge_types=edge_types,
        as_of=as_of,
        max_depth=max_depth,
    )
    pruned = prune_tree(tree, allowed) if tree is not None else None
    return {"focal_entity_id": candidate, "roots": roots, "tree": pruned}
