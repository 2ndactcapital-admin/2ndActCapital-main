"""BUGFIX1 (CRM-02) — ONE read of an entity's current ownership edges.

Ownership is written to TWO tables: `entity_ownership` (parent_id/child_id —
POST /entities/ownership, the seed data, the Overview graph, /full) and
`entity_relationships` with relationship_type='ownership'
(from_entity_id/to_entity_id — the Ownership tab's add/edit/delete). Each
screen read only one, so James Hargrove's Overview showed Hargrove Capital LLC
40% / Hargrove Family Trust 60% (entity_ownership) while his Ownership tab said
"no ownership data" (entity_relationships: none). Every CRM ownership read now
goes through `ownership_edges`, which returns the current edges of BOTH tables,
one per (parent, child) pair — the entity_relationships row wins when both
exist, because that is the one the tab can edit. Each edge says which table it
came from so the UI only offers edit/delete on rows its endpoints can change.
Consolidating the two tables into one is a separate data-model change, recorded
in docs/PROJECT_STATUS.md, not done here.
"""
from __future__ import annotations

_SQL = """
    SELECT r.id, r.from_entity_id AS parent_id, r.to_entity_id AS child_id,
           r.ownership_pct, 'equity'::text AS ownership_type, r.notes,
           r.effective_date, r.change_reason, 'entity_relationships' AS source
    FROM entity_relationships r
    WHERE r.org_id = $1 AND r.relationship_type = 'ownership'
      AND r.valid_to IS NULL AND r.system_to IS NULL
      AND (r.from_entity_id = ANY($2::uuid[]) OR r.to_entity_id = ANY($2::uuid[]))
    UNION ALL
    SELECT o.id, o.parent_id, o.child_id, o.ownership_pct, o.ownership_type, NULL::text,
           NULL::date, NULL::text, 'entity_ownership'
    FROM entity_ownership o
    WHERE o.org_id = $1 AND o.valid_to IS NULL AND o.system_to IS NULL
      AND (o.parent_id = ANY($2::uuid[]) OR o.child_id = ANY($2::uuid[]))
"""


async def ownership_edges(conn, org_id, entity_ids) -> list[dict]:
    """Current ownership edges touching any of `entity_ids`, deduplicated."""
    ids = [i for i in entity_ids if i]
    if not ids:
        return []
    rows = await conn.fetch(_SQL, org_id, ids)
    best: dict = {}
    for r in rows:
        key = (r["parent_id"], r["child_id"])
        if key not in best or r["source"] == "entity_relationships":
            best[key] = dict(r)
    return list(best.values())
