"""CRMDETAIL1 Task 3 — typed detail fields per entity type (trust / foundation / LLC / LP / GP / family office).

STORAGE DECISION: ``public.entity_attributes`` rows under the reserved key prefix ``detail.`` with the typed key
registry in ``services.crm_vocab`` (label, type, choices, bounds). Why not a detail table: entity_attributes is
already bi-temporal, org-scoped and RLS'd (an ALL policy), the fields are sparse and differ per entity type, and a
registry gives every field its label and type through the API envelope without a migration per new field. The
generic Attributes list on the record excludes the ``detail.`` keys (routers.entities._fetch_attributes).

Writes are valid-time restatements per key (Rule 3): the live row is closed (``valid_to = now()``) and the new
value inserted; clearing a field closes it and inserts nothing; an unchanged value writes nothing.

``trust_type`` / ``charity_type`` also set ``entities.sub_type`` to the choice's label through the ONE entity write
path (services.entity_writes, system-axis archive). The state of formation is the record's own
``country_code`` / ``region_code`` (the Details form's Country / State of formation) — shown here, edited there.
"""

from __future__ import annotations

from uuid import UUID

from services import crm_vocab as V
from services.entity_writes import update_entity_row


class DetailError(ValueError):
    """Refused for a reason the caller can fix (422)."""


class DetailNotFound(LookupError):
    pass


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))


async def _entity(conn, org_id, entity_id):
    return await conn.fetchrow(
        "SELECT id, display_name, entity_type::text AS entity_type, sub_type, country_code, region_code "
        "FROM entities WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
        _u(entity_id), _u(org_id))


async def _live(conn, org_id, entity_id) -> dict:
    rows = await conn.fetch(
        "SELECT id, attribute_key, attribute_value, value_type, valid_from FROM entity_attributes "
        "WHERE entity_id = $1 AND org_id = $2::uuid AND attribute_key LIKE 'detail.%' "
        "AND valid_to IS NULL AND system_to IS NULL", _u(entity_id), _u(org_id))
    return {r["attribute_key"]: r for r in rows}


async def get_details(conn, org_id, entity_id) -> dict:
    ent = await _entity(conn, org_id, entity_id)
    if ent is None:
        raise DetailNotFound("Entity not found")
    live = await _live(conn, org_id, ent["id"])
    sponsor_ids = [live[f.attribute_key]["attribute_value"] for f in V.detail_fields_for(ent["entity_type"])
                   if f.type == "entity" and f.attribute_key in live]
    names = {}
    if sponsor_ids:
        names = {str(r["id"]): r["display_name"] for r in await conn.fetch(
            "SELECT id, display_name FROM entities WHERE id = ANY($1::uuid[]) AND org_id = $2::uuid "
            "AND valid_to IS NULL AND system_to IS NULL", [_u(x) for x in sponsor_ids], _u(org_id))}
    fields = []
    for f in V.detail_fields_for(ent["entity_type"]):
        if f.source == "column:region_code":
            value = ent["region_code"]
            display = " / ".join(x for x in (ent["country_code"], ent["region_code"]) if x) or None
        else:
            row = live.get(f.attribute_key)
            value = V.decode_detail(f, row["attribute_value"]) if row else None
            if f.type == "choice":
                display = V.choice_label(f, value)
            elif f.type == "boolean":
                display = None if value is None else ("Yes" if value else "No")
            elif f.type == "entity":
                display = names.get(str(value)) if value else None
            else:
                display = None if value is None else str(value)
        fields.append({**f.as_json(), "value": value, "display": display})
    return {"entity_id": str(ent["id"]), "entity_type": ent["entity_type"], "sub_type": ent["sub_type"],
            "fields": fields}


async def save_details(conn, org_id, actor_id, entity_id, values: dict) -> dict:
    """Validate EVERY submitted key first (nothing is written when one is refused), then restate the changed ones."""
    ent = await _entity(conn, org_id, entity_id)
    if ent is None:
        raise DetailNotFound("Entity not found")
    etype = ent["entity_type"]
    if not V.detail_fields_for(etype):
        raise DetailError(f"{V.a_type(etype)} record has no detail fields")
    if not isinstance(values, dict) or not values:
        raise DetailError("send the fields to save, e.g. {\"trust_type\": \"dynasty\"}")
    planned: list[tuple] = []
    errors: list[str] = []
    for key, raw in values.items():
        f = V.detail_field(etype, key)
        if f is None:
            errors.append(f"'{key}' is not a detail field of {V.a_type(etype)} record")
            continue
        if f.source != "attribute":
            errors.append(f"{f.label} is edited on the Details form (Country / State of formation)")
            continue
        try:
            stored = V.coerce_detail(f, raw)
        except V.VocabError as exc:
            errors.append(str(exc))
            continue
        if f.type == "entity" and stored is not None:
            other = await conn.fetchrow(
                "SELECT entity_type::text AS t FROM entities WHERE id = $1 AND org_id = $2::uuid "
                "AND valid_to IS NULL AND system_to IS NULL", _u(stored), _u(org_id))
            if other is None:
                errors.append(f"{f.label}: that record is not in this organisation's CRM")
                continue
            if f.entity_types is not None and other["t"] not in f.entity_types:
                errors.append(f"{f.label}: {V.a_type(other['t'])} record cannot be the {f.label.lower()}")
                continue
            if stored == str(ent["id"]):
                errors.append(f"{f.label}: a record cannot be its own {f.label.lower()}")
                continue
        planned.append((f, stored))
    if errors:
        raise DetailError("; ".join(errors))

    live = await _live(conn, org_id, ent["id"])
    changed: list[str] = []
    sub_type = None
    async with conn.transaction():
        for f, stored in planned:
            cur = live.get(f.attribute_key)
            if (cur["attribute_value"] if cur else None) == stored:
                continue
            if cur is not None:
                await conn.execute(
                    "UPDATE entity_attributes SET valid_to = now() WHERE id = $1 AND org_id = $2::uuid "
                    "AND valid_to IS NULL AND system_to IS NULL", cur["id"], _u(org_id))
            if stored is not None:
                await conn.execute(
                    "INSERT INTO entity_attributes (org_id, entity_id, attribute_key, attribute_value, value_type, "
                    "created_by) VALUES ($1::uuid, $2, $3, $4, $5, $6)",
                    _u(org_id), ent["id"], f.attribute_key, stored, V.value_type_of(f),
                    _u(actor_id) if actor_id else None)
            changed.append(f.key)
            if f.sets_sub_type:
                sub_type = V.choice_label(f, stored) if stored is not None else None
        if any(f.sets_sub_type and f.key in changed for f, _ in planned) and sub_type != ent["sub_type"]:
            await update_entity_row(conn, _u(org_id), ent["id"], {"sub_type": sub_type}, returning="id")
    out = await get_details(conn, org_id, ent["id"])
    out["changed"] = changed
    return out
