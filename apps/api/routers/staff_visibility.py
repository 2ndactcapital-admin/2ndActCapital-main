"""VISIBILITY1 — Admin → Staff visibility (preview + the switch), Unassigned families (bulk assign) and the
Team panel on a household / CRM record.

Gates (services.authz, Super Admin first):
  * preview, switch                → org admin (manage_org_settings). The switch is the ONLY writer of
                                     org_settings 'staff_visibility.enforced' (the generic settings route refuses
                                     the key) and is audited with the preview the admin confirmed.
  * unassigned list, bulk assign,  → org admin or manage_members; a restricted caller (an enforcing org, not org
    Team panel reads / writes        admin) sees and assigns only families they can see — the app-wide path guard
                                     404s another family's id before these handlers run.
Every response is a permission envelope (permissions + vocabularies; ``editable`` empty for a view-only caller).
``org_id`` is only ever the caller's own (get_org_id); no request model declares one (extra='forbid').
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator

from routers.entities import get_org_id
from services import family_visibility as FV
from services.audit import write_audit_log
from services.authz import require_any_permission, require_permission
from services.database import get_pool
from services.org_settings import category_for
from services.rbac import ORG_ADMIN_PERMISSION

router = APIRouter(tags=["admin", "staff-visibility"])

ASSIGN_PERMISSIONS = ("manage_members", ORG_ADMIN_PERMISSION)


class EnforcementSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enforced: bool
    confirm: bool = False
    #: the number of staff the admin saw would see NO family — re-checked server-side, so a stale confirm step
    #: (someone was unassigned meanwhile) is refused rather than switched blind.
    acknowledged_sees_none: int | None = None


class TeamAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assigned_to_user_id: UUID | None = None
    assigned_to_team_id: UUID | None = None
    role_label: str | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.assigned_to_user_id is None) == (self.assigned_to_team_id is None):
            raise ValueError("Provide exactly one of assigned_to_user_id or assigned_to_team_id")
        return self


class BulkAssign(TeamAssign):
    family_keys: list[str]


def _envelope_perms(request: Request, *, can_write: bool) -> dict:
    from services.authz import principal_of

    p = principal_of(request)
    return {"can_read": True, "can_write": bool(can_write), "is_super_admin": bool(p and p.is_super_admin),
            "read_permission": "manage_members", "write_permission": "manage_members"}


async def _role_labels(conn, org_id) -> list[str]:
    raw = await conn.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = $2",
                              UUID(str(org_id)), FV.ROLE_LABELS_KEY)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw) and raw:
        return raw
    return list(FV.DEFAULT_ROLE_LABELS)


def _vocab(role_labels, can_write) -> dict:
    return {"role_labels": role_labels, "reason_labels": FV.REASON_LABELS,
            "editable": ["assignments"] if can_write else [], "inline_editable": []}


# ── Preview + switch ───────────────────────────────────────────────────────────────────────────────────────────
@router.get("/admin/staff-visibility")
async def get_preview(request: Request):
    """For each internal user: how many families they would see with enforcement ON, which, and why; who would
    see none; the switch's current state."""
    require_permission(request, ORG_ADMIN_PERMISSION)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        enforced = await FV.enforced(conn, org_id)
        prev = await FV.preview(conn, org_id)
    return {"enforced": enforced, **prev,
            "permissions": {**_envelope_perms(request, can_write=True), "read_permission": ORG_ADMIN_PERMISSION,
                            "write_permission": ORG_ADMIN_PERMISSION},
            "vocabularies": {"reason_labels": FV.REASON_LABELS, "editable": ["enforced"], "inline_editable": []}}


@router.put("/admin/staff-visibility/enforcement")
async def set_enforcement(request: Request, body: EnforcementSet):
    """Turn enforcement on / off for the caller's own org. Org admin only; ``confirm`` required; turning it ON
    re-checks the preview's "sees none" count against what the admin acknowledged; audited."""
    p = require_permission(request, ORG_ADMIN_PERMISSION)
    org_id = get_org_id(request)
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Confirm the change after reviewing the preview")
    pool = await get_pool()
    async with pool.acquire() as conn:
        before = await FV.enforced(conn, org_id)
        prev = await FV.preview(conn, org_id)
        none = len(prev["sees_none"])
        if body.enforced and body.acknowledged_sees_none is not None and body.acknowledged_sees_none != none:
            raise HTTPException(status_code=409, detail=f"The preview changed: {none} staff would now see no "
                                                        "family. Review it again before switching on.")
        await conn.execute(
            """
            INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public, updated_by, updated_at)
            VALUES ($1, $2, $3::jsonb, $5, false, $4, now())
            ON CONFLICT (org_id, setting_key) DO UPDATE
               SET setting_value = EXCLUDED.setting_value, updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            UUID(str(org_id)), FV.SETTING_KEY, json.dumps(bool(body.enforced)), UUID(p.user_id),
            category_for(FV.SETTING_KEY),
        )
        after = await FV.enforced(conn, org_id)
    await write_audit_log(
        None, org_id=org_id, action="staff_visibility.enforcement", table_name="org_settings", record_id=None,
        old={"enforced": before}, new={"enforced": after, "sees_none": prev["sees_none"],
                                       "families_total": prev["families_total"],
                                       "unassigned_count": prev["unassigned_count"]},
        actor=p.user_id,
    )
    return {"enforced": after, "previous": before, **prev}


# ── Unassigned families + bulk assign ──────────────────────────────────────────────────────────────────────────
async def _graph(request, pool, org_id):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(str(org_id), False)
    try:
        async with pool.acquire() as conn:
            return await FV.load_graph(conn, org_id), await _role_labels(conn, org_id)
    finally:
        reset_rls_context(tokens)


@router.get("/admin/staff-visibility/unassigned")
async def list_unassigned(request: Request):
    require_any_permission(request, ASSIGN_PERMISSIONS)
    org_id = get_org_id(request)
    pool = await get_pool()
    scope = await FV.scope_for_request(request, pool)
    graph, labels = await _graph(request, pool, org_id)
    return {"rows": FV.unassigned(graph, scope), "permissions": _envelope_perms(request, can_write=True),
            "vocabularies": _vocab(labels, True)}


async def _insert_assignment(conn, org_id, actor_id, entity_id, body: TeamAssign) -> str:
    if body.assigned_to_user_id is not None:
        if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1 AND org_id = $2", body.assigned_to_user_id,
                                   UUID(str(org_id))):
            raise HTTPException(status_code=404, detail="User not found in org")
    elif not await conn.fetchval("SELECT 1 FROM teams WHERE id = $1 AND org_id = $2", body.assigned_to_team_id,
                                 UUID(str(org_id))):
        raise HTTPException(status_code=404, detail="Team not found in org")
    aid = await conn.fetchval(
        "INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, assigned_to_team_id, role_label, "
        "assigned_by) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        UUID(str(org_id)), UUID(str(entity_id)), body.assigned_to_user_id, body.assigned_to_team_id,
        (body.role_label or "").strip() or None, UUID(str(actor_id)),
    )
    await write_audit_log(
        conn, org_id=org_id, action="create_staff_assignment", table_name="staff_assignments", record_id=aid,
        new={"entity_id": str(entity_id),
             "assigned_to_user_id": str(body.assigned_to_user_id) if body.assigned_to_user_id else None,
             "assigned_to_team_id": str(body.assigned_to_team_id) if body.assigned_to_team_id else None,
             "role_label": body.role_label, "assigned_by": str(actor_id)},
        actor=actor_id,
    )
    return str(aid)


@router.post("/admin/staff-visibility/bulk-assign", status_code=201)
async def bulk_assign(request: Request, body: BulkAssign):
    p = require_any_permission(request, ASSIGN_PERMISSIONS)
    org_id = get_org_id(request)
    pool = await get_pool()
    scope = await FV.scope_for_request(request, pool)
    graph, _ = await _graph(request, pool, org_id)
    keys = list(dict.fromkeys(body.family_keys))
    if not keys:
        raise HTTPException(status_code=422, detail="Choose at least one family")
    for k in keys:   # every family must exist in the org AND be visible — the same 404 for both
        if k not in graph.families or (scope is not None and k not in scope.family_keys):
            raise HTTPException(status_code=404, detail="Family not found")
        if not graph.families[k].anchor_entity_id:
            raise HTTPException(status_code=422, detail=f"{graph.families[k].name} has no member to assign")
    created = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for k in keys:
                created.append({"family_key": k, "assignment_id": await _insert_assignment(
                    conn, org_id, p.user_id, graph.families[k].anchor_entity_id, body)})
    return {"created": created}


# ── Team panel (household / CRM record) ────────────────────────────────────────────────────────────────────────
def _family_key_for(graph, *, entity_id=None, household_id=None) -> str:
    if household_id is not None:
        key = f"household:{household_id}"
        if key not in graph.families:
            raise HTTPException(status_code=404, detail="Household not found")
        return key
    keys, _extra = graph.grant(str(entity_id))
    if str(entity_id) not in graph.entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if len(keys) == 1:
        return next(iter(keys))
    fams = sorted(graph.families_of.get(str(entity_id), set()))
    if len(keys) > 1:
        return sorted(keys)[0]
    if fams:
        return fams[0]
    raise HTTPException(status_code=404, detail="Entity not found")


async def _panel(request, *, entity_id=None, household_id=None) -> dict:
    from services.authz import principal_of

    p = require_any_permission(request, ("manage_members", ORG_ADMIN_PERMISSION, "view_members"))
    org_id = get_org_id(request)
    pool = await get_pool()
    graph, labels = await _graph(request, pool, org_id)
    key = _family_key_for(graph, entity_id=entity_id, household_id=household_id)
    fam = graph.families[key]
    own = graph.owners(key)
    name = lambda uid: (graph.users.get(uid) or {}).get("name") or (graph.users.get(uid) or {}).get("email")
    can_write = bool(p.is_super_admin or "manage_members" in p.permissions
                     or ORG_ADMIN_PERMISSION in p.permissions)
    shared = entity_id is not None and len(graph.families_of.get(str(entity_id), set())) > 1 \
        and not graph.member_of.get(str(entity_id))
    return {"family": FV.family_row(graph, key), "shared_record": bool(shared),
            "assigned": key in graph.assigned_keys(),
            "owner": {"created_by": [{"user_id": u, "name": name(u)} for u in own["created_by"]],
                      "relationship_managers": [{"user_id": u, "name": name(u)}
                                                for u in own["relationship_managers"]]},
            "assignments": FV.family_assignments(graph, key),
            "enforced": bool(principal_of(request).visibility_enforced),
            "permissions": _envelope_perms(request, can_write=can_write),
            "vocabularies": _vocab(labels, can_write)}


@router.get("/staff-visibility/entities/{entity_id}/team")
async def entity_team(request: Request, entity_id: UUID):
    return await _panel(request, entity_id=entity_id)


@router.get("/staff-visibility/households/{household_id}/team")
async def household_team(request: Request, household_id: UUID):
    return await _panel(request, household_id=household_id)


async def _add(request, body: TeamAssign, *, entity_id=None, household_id=None):
    p = require_any_permission(request, ASSIGN_PERMISSIONS)
    org_id = get_org_id(request)
    pool = await get_pool()
    graph, _ = await _graph(request, pool, org_id)
    key = _family_key_for(graph, entity_id=entity_id, household_id=household_id)
    anchor = str(entity_id) if entity_id is not None else graph.families[key].anchor_entity_id
    if not anchor:
        raise HTTPException(status_code=422, detail="This household has no member to assign")
    async with pool.acquire() as conn:
        aid = await _insert_assignment(conn, org_id, p.user_id, anchor, body)
    return {"assignment_id": aid, "family_key": key}


@router.post("/staff-visibility/entities/{entity_id}/team", status_code=201)
async def add_entity_team(request: Request, entity_id: UUID, body: TeamAssign):
    return await _add(request, body, entity_id=entity_id)


@router.post("/staff-visibility/households/{household_id}/team", status_code=201)
async def add_household_team(request: Request, household_id: UUID, body: TeamAssign):
    return await _add(request, body, household_id=household_id)


@router.delete("/staff-visibility/assignments/{assignment_id}")
async def remove_team(request: Request, assignment_id: UUID):
    from routers.staff_assignments import remove_assignment

    p = require_any_permission(request, ASSIGN_PERMISSIONS)
    pool = await get_pool()
    return await remove_assignment(request, pool, get_org_id(request), p.user_id, assignment_id)
