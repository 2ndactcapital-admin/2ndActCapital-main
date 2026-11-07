"""ROLLUP2 Task 4 — two live ownership percentages for the same owner / owned entity.

How one arises: ``POST /entities/{id}/ownership`` never looked for a live edge on the same pair, so recording a NEW
percentage by "adding" it (instead of editing the existing one) left both live; and the second ownership table
(``entity_ownership``) can hold a row for a pair ``entity_relationships`` also holds. Every net-worth reader sums the
live edges (services.networth.shares, capped at 1), so a duplicate silently inflates a look-through share.

Live Olivo, 2026-11-06 (docs/ROLLUP2_DISCOVERY.md 1d): ZERO duplicate pairs in either table. The "Art Holdings 60.00 %
AND 15.00 % for Dominic Revocable Trust" Muse saw is two DIFFERENT owners on the look-through tree — Dominic's trust
owns 15 % directly and Olivo Holdings LP (which the trust also owns) owns 60 % — drawn nested without saying "via";
the tree now labels the indirect entry (components/crm/OwnershipTree.jsx).

The rule going forward (routers/entity_graph.create_ownership): a new percentage for a pair that already has a live
edge is a NEW VERSION — the prior one archived (valid_to = system_to = now(), the same as the PATCH amend), the
change logged — unless the caller explicitly records a second, separate holding (``separate_holding: true``).

This module: :func:`find` (the Super Admin report and the repair's input), :func:`for_entity` (the panel warning),
:func:`repair_plan` / :func:`apply_repair` (scripts/repair_ownership_duplicates.py — survivor = the NEWEST live
entry; the others archived, audited; idempotent). Runs under the caller's org RLS context.
"""

from __future__ import annotations

import json
from uuid import UUID

EDGES_SQL = """
    SELECT 'entity_relationships' AS source, r.id, r.from_entity_id AS parent_id, r.to_entity_id AS child_id,
           r.ownership_pct, r.valid_from, r.created_at, r.change_reason AS note
      FROM entity_relationships r
     WHERE r.org_id = $1::uuid AND r.relationship_type = 'ownership' AND r.valid_to IS NULL AND r.system_to IS NULL
    UNION ALL
    SELECT 'entity_ownership', o.id, o.parent_id, o.child_id, o.ownership_pct, o.valid_from, o.valid_from, NULL
      FROM entity_ownership o
     WHERE o.org_id = $1::uuid AND o.valid_to IS NULL AND o.system_to IS NULL
"""


def _iso(v):
    return v.isoformat() if v is not None else None


def group(rows: list[dict]) -> list[dict]:
    """Pure: the pairs with more than one live entry; entries newest first; the survivor = the newest
    (valid_from, then created_at, then an entity_relationships row — the editable table — before entity_ownership)."""
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault((str(r["parent_id"]), str(r["child_id"])), []).append(r)
    out = []
    for (p, c), es in sorted(by.items()):
        if len(es) < 2:
            continue
        es = sorted(es, key=lambda e: (e["valid_from"], e["created_at"], e["source"] == "entity_relationships",
                                       str(e["id"])), reverse=True)
        out.append({"parent_id": p, "child_id": c, "survivor": str(es[0]["id"]),
                    "entries": [{"id": str(e["id"]), "source": e["source"],
                                 "ownership_pct": format(e["ownership_pct"], "f") if e["ownership_pct"] is not None
                                 else None, "valid_from": _iso(e["valid_from"]), "note": e["note"],
                                 "newer": i == 0} for i, e in enumerate(es)],
                    "percentages": sorted({format(e["ownership_pct"], "f") for e in es
                                           if e["ownership_pct"] is not None})})
    return out


async def find(conn, org_id) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(EDGES_SQL, str(org_id))]
    pairs = group(rows)
    ids = sorted({x for p in pairs for x in (p["parent_id"], p["child_id"])})
    names = {str(r["id"]): r["display_name"] for r in await conn.fetch(
        "SELECT id, display_name FROM entities WHERE org_id = $1::uuid AND id = ANY($2::uuid[])", str(org_id),
        ids)} if ids else {}
    for p in pairs:
        p["owner_name"], p["owned_name"] = names.get(p["parent_id"]), names.get(p["child_id"])
    return pairs


async def for_entity(conn, org_id, entity_id) -> list[dict]:
    """The duplicate pairs this entity is on either side of — the Ownership panel's warning."""
    e = str(entity_id)
    return [p for p in await find(conn, org_id) if e in (p["parent_id"], p["child_id"])]


def warning_text(p: dict) -> str:
    pcts = " and ".join(f"{x} %" for x in p["percentages"]) or "no percentage"
    return (f"{p.get('owner_name') or p['parent_id']} has {len(p['entries'])} live ownership entries in "
            f"{p.get('owned_name') or p['child_id']} ({pcts}). Net worth adds them together. Edit the correct one "
            "and remove the other, or ask an operator to run the duplicate repair.")


def repair_plan(pairs: list[dict]) -> list[dict]:
    """Pure: for each pair, the entries to archive (every one but the survivor)."""
    return [{**p, "archive": [e for e in p["entries"] if e["id"] != p["survivor"]]} for p in pairs]


async def apply_repair(conn, org_id, plan: list[dict], *, actor_id=None) -> int:
    """Archive every non-survivor (valid_to = system_to = now(), as the PATCH amend archives a version), log the
    change in ownership_change_log and audit_log. Runs in ONE transaction under the org's context. Returns rows
    archived. Idempotent: an entry already archived is not matched (``valid_to IS NULL`` guard)."""
    org = str(org_id)
    n = 0
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org)
        for p in plan:
            for e in p["archive"]:
                tbl = "entity_relationships" if e["source"] == "entity_relationships" else "entity_ownership"
                done = await conn.fetchval(
                    f"UPDATE {tbl} SET valid_to = now(), system_to = now() WHERE id = $1::uuid AND org_id = $2::uuid "
                    "AND valid_to IS NULL AND system_to IS NULL RETURNING id", e["id"], org)
                if done is None:
                    continue
                n += 1
                if tbl == "entity_relationships":
                    await conn.execute(
                        "INSERT INTO ownership_change_log (org_id, relationship_id, from_entity_id, to_entity_id, "
                        "prior_pct, new_pct, change_reason, changed_by) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, "
                        "$5, NULL, $6, $7::uuid)", org, e["id"], p["parent_id"], p["child_id"],
                        e["ownership_pct"], f"rollup2 duplicate repair: superseded by {p['survivor']}",
                        str(actor_id) if actor_id else None)
                await conn.execute(
                    "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                    "VALUES ($1::uuid, $2::uuid, 'repair_ownership_duplicate', $3, $4::uuid, $5::jsonb)",
                    org, str(actor_id) if actor_id else None, tbl, e["id"],
                    json.dumps({"archived": e, "survivor": p["survivor"], "pair": [p["parent_id"], p["child_id"]]}))
    return n


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))
