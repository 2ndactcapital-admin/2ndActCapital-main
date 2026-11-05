"""CRMDETAIL1 Task 2 — governed relationships on a CRM record (the Relationships panel).

Every relationship row is one ``public.entity_relationships`` row: FROM is <type> TO. This module never touches an
``ownership`` row (those are entered on the Ownership tab and read by the ownership readers, which filter
``relationship_type = 'ownership'`` explicitly) — every statement here carries ``relationship_type <> 'ownership'``.

Bi-temporal (Rule 3, valid axis): adding inserts a live row; ENDING sets ``valid_to`` and leaves the row in place —
it is listed as history, never deleted. ``entity_rel_active_unique`` (from, to, type) WHERE valid_to IS NULL is the
live duplicate guard; for a symmetric type the reverse pair is refused here too.

All reads and writes go through the caller's RLS pool connection (``services.database``), so the org GUC is set on
every statement; ``org_id`` is always the caller's verified org, never a request field.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from services import crm_vocab as V

OWNERSHIP = "ownership"

_ROW_SQL = """
    SELECT r.id, r.from_entity_id, r.to_entity_id, r.relationship_type, r.notes, r.effective_date,
           r.change_reason, r.valid_from, r.valid_to, r.created_by,
           f.display_name AS from_name, f.entity_type::text AS from_type,
           t.display_name AS to_name, t.entity_type::text AS to_type
    FROM entity_relationships r
    JOIN entities f ON f.id = r.from_entity_id AND f.org_id = r.org_id AND f.valid_to IS NULL AND f.system_to IS NULL
    JOIN entities t ON t.id = r.to_entity_id AND t.org_id = r.org_id AND t.valid_to IS NULL AND t.system_to IS NULL
    WHERE r.org_id = $1::uuid AND r.relationship_type <> 'ownership' AND r.system_to IS NULL
"""


class RelationshipError(ValueError):
    """Refused for a reason the caller can fix (422)."""


class RelationshipConflict(RelationshipError):
    """Already exists (409)."""


class RelationshipNotFound(LookupError):
    """No live, non-ownership relationship with that id in the caller's org (404)."""


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))


def _view(row, this_id: UUID) -> dict:
    """One row as seen from ``this_id``: the label from this record's side, the other record."""
    rel = V.RELATIONSHIP_TYPES.get(row["relationship_type"])
    outgoing = row["from_entity_id"] == this_id
    label = (rel.label if outgoing else rel.reverse_label) if rel else row["relationship_type"]
    other = ("to" if outgoing else "from")
    return {
        "id": str(row["id"]),
        "relationship_type": row["relationship_type"],
        "known_type": rel is not None,
        "group": rel.group if rel else "other",
        "direction": "outgoing" if outgoing else "incoming",
        "label": label,
        "other_entity_id": str(row[f"{other}_entity_id"]),
        "other_name": row[f"{other}_name"],
        "other_type": row[f"{other}_type"],
        "from_entity_id": str(row["from_entity_id"]),
        "to_entity_id": str(row["to_entity_id"]),
        "notes": row["notes"],
        "effective_date": row["effective_date"].isoformat() if row["effective_date"] else None,
        "change_reason": row["change_reason"],
        "valid_from": row["valid_from"].isoformat() if row["valid_from"] else None,
        "valid_to": row["valid_to"].isoformat() if row["valid_to"] else None,
        "ended": row["valid_to"] is not None,
    }


async def _entity(conn, org_id, entity_id):
    return await conn.fetchrow(
        "SELECT id, display_name, entity_type::text AS entity_type FROM entities WHERE id = $1 AND org_id = $2::uuid "
        "AND valid_to IS NULL AND system_to IS NULL", _u(entity_id), _u(org_id))


async def list_for_entity(conn, org_id, entity_id) -> dict:
    """{"current": [...], "history": [...]} — both directions, every non-ownership type, from this record's side."""
    eid = _u(entity_id)
    if await _entity(conn, org_id, eid) is None:
        raise RelationshipNotFound("Entity not found")
    rows = await conn.fetch(_ROW_SQL + " AND (r.from_entity_id = $2 OR r.to_entity_id = $2) "
                            "ORDER BY r.valid_to NULLS FIRST, r.relationship_type, r.valid_from", _u(org_id), eid)
    views = [_view(r, eid) for r in rows]
    order = {k: i for i, (k, _) in enumerate(V.RELATIONSHIP_GROUPS)}
    views.sort(key=lambda v: (order.get(v["group"], 99), v["label"], v["other_name"] or ""))
    return {"current": [v for v in views if not v["ended"]], "history": [v for v in views if v["ended"]]}


async def create(conn, *, org_id, actor_id, entity_id, relationship_type: str, other_entity_id, direction: str,
                 notes: str | None = None, effective_date: date | None = None) -> dict:
    """Add one governed relationship from this record's side. ``direction`` 'outgoing' = this record is FROM."""
    if direction not in ("outgoing", "incoming"):
        raise RelationshipError("direction is 'outgoing' (this record is the first party) or 'incoming'")
    this = await _entity(conn, org_id, entity_id)
    other = await _entity(conn, org_id, other_entity_id)
    if this is None or other is None:
        raise RelationshipNotFound("Entity not found")
    frm, to = (this, other) if direction == "outgoing" else (other, this)
    if (relationship_type or "").strip() == OWNERSHIP:
        raise RelationshipError("ownership (with its percentage) is entered on the Ownership tab")
    try:
        rel = V.check_relationship(relationship_type, frm["entity_type"], to["entity_type"],
                                   from_id=frm["id"], to_id=to["id"])
    except V.VocabError as exc:
        raise RelationshipError(str(exc)) from None
    pairs = [(frm["id"], to["id"])] + ([(to["id"], frm["id"])] if rel.symmetric else [])
    for a, b in pairs:
        dup = await conn.fetchval(
            "SELECT 1 FROM entity_relationships WHERE org_id = $1::uuid AND from_entity_id = $2 AND "
            "to_entity_id = $3 AND relationship_type = $4 AND valid_to IS NULL AND system_to IS NULL",
            _u(org_id), a, b, rel.key)
        if dup:
            raise RelationshipConflict(f"{frm['display_name']} is already recorded as {rel.label.lower()} "
                                       f"{to['display_name']}")
    note = (notes or "").strip() or None
    row = await conn.fetchrow(
        """
        INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, notes,
                                          effective_date, change_reason, change_source_type, created_by)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, 'added on the Relationships panel', 'crm_relationships_panel', $7)
        RETURNING id
        """,
        _u(org_id), frm["id"], to["id"], rel.key, note, effective_date, _u(actor_id) if actor_id else None)
    created = await conn.fetchrow(_ROW_SQL + " AND r.id = $2", _u(org_id), row["id"])
    return _view(created, this["id"])


async def end(conn, *, org_id, rel_id, reason: str | None = None) -> dict:
    """End a live non-ownership relationship: ``valid_to = now()``; the row stays (history), never deleted."""
    row = await conn.fetchrow(
        "UPDATE entity_relationships SET valid_to = now(), change_reason = COALESCE($3::text, change_reason) "
        "WHERE id = $1 AND org_id = $2::uuid AND relationship_type <> 'ownership' AND valid_to IS NULL "
        "AND system_to IS NULL RETURNING id, from_entity_id",
        _u(rel_id), _u(org_id), ((reason or "").strip() or "ended on the Relationships panel"))
    if row is None:
        raise RelationshipNotFound("Relationship not found, already ended, or an ownership edge (Ownership tab)")
    ended = await conn.fetchrow(_ROW_SQL + " AND r.id = $2", _u(org_id), row["id"])
    return _view(ended, row["from_entity_id"])
