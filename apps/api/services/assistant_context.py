"""NAV2 — the assistant's entity context (the entity the person selected with the chip / Ctrl K).

Content only, never a gate: POST /assistant/message is answered either way; this decides only whether the model's
system prompt gains one line naming the selected entity. Kept out of routers/assistant.py on purpose — the route
record (authz1_routes.py, which follows calls by name within a router) must keep saying what the route enforces:
nothing beyond auth; every tool keeps its own gate and visibility.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

_ENTITY_CONTEXT_TYPES = ("entity",)


def _clean_name(name) -> str:
    """A display name safe to quote in the system prompt: one line, no quotes, at most 120 characters."""
    return " ".join(str(name or "").replace('"', "'").split())[:120]


async def entity_context_note(request: Request, pool, org_id, context_ref: dict | None) -> str | None:
    """NAV2 — the assistant's entity context. ``context_ref`` = the EXISTING request shape {type, id} (+ entity_type,
    ignored: the type is read from the database). Only an entity THIS person may see yields a note — checked with
    the one entity gate (``services.authz.require_entity_access``: staff any entity in the org, subject to VISIBILITY1;
    a member only their own family). Missing, forbidden or malformed → None, the same silence for each, so the
    reply never discloses existence. The note grants nothing: every tool keeps its own gates and visibility."""
    from uuid import UUID

    from services.authz import require_entity_access

    if not isinstance(context_ref, dict) or context_ref.get("type") not in _ENTITY_CONTEXT_TYPES:
        return None
    try:
        entity_id = UUID(str(context_ref.get("id")))
    except (TypeError, ValueError):
        return None
    try:
        await require_entity_access(request, pool, entity_id)
    except HTTPException:
        return None
    row = await pool.fetchrow(
        "SELECT display_name, entity_type::text AS entity_type FROM entities "
        "WHERE org_id = $1 AND id = $2 AND valid_to IS NULL AND system_to IS NULL",
        org_id, entity_id)
    if row is None:
        return None
    kind = (row["entity_type"] or "entity").replace("_", " ")
    return (f'Context: the person has selected the {kind} "{_clean_name(row["display_name"])}" (entity id '
            f"{entity_id}) in the app. When they say \"this\", \"this client\" or similar, they mean it. This is "
            "context only — your tools still return only what this person may see.")
