"""modelresearch.structural — the Model Research grid (read-only).

    GET /admin/model-research?refresh=false    rows + source metadata + envelope

Mounted under /api/v1 (main.py). Access is super_admin or the holder of
``manage_org_settings`` in their OWN org, resolved through
``services.rbac.can_manage_org_settings`` (super-admin first, then the
permission). Never a role string. A member is refused with 403 here; the
menu hiding the link is a separate, client-side proof.

The org checked is the caller's own, from their verified session
(``get_org_id``/principal). There is no org_id parameter. This endpoint writes nothing, and
``refresh=true`` only re-reads the sources: it never asks the proxy to reload
its price list (that is the separate ``litellm.reload_model_cost_map`` action).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from routers.entities import get_org_id
from services.database import get_pool
from services.model_research import ModelResearchSourceError, build_research
from services.rbac import ORG_ADMIN_PERMISSION, can_manage_org_settings, is_super_admin, load_principal
from services.users import ensure_user

router = APIRouter(tags=["model-research"])


def _permissions(super_admin: bool) -> dict:
    return {
        "can_read": True,
        "can_write": False,  # read-only page: there is no write to grant
        "is_super_admin": super_admin,
        "read_permission": ORG_ADMIN_PERMISSION,
        "write_permission": None,
    }


@router.get("/admin/model-research")
async def read_model_research(request: Request, refresh: bool = Query(False)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        principal = await load_principal(conn, user_id)
    if principal is None:
        principal = {"id": user_id, "org_id": get_org_id(request), "role": None}

    if not await can_manage_org_settings(pool, principal, principal["org_id"]):
        raise HTTPException(
            status_code=403,
            detail=f"Permission required: {ORG_ADMIN_PERMISSION} (or Super Admin)",
        )

    async with pool.acquire() as conn:
        try:
            result = await build_research(conn, refresh=refresh)
        except ModelResearchSourceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**result, "permissions": _permissions(is_super_admin(principal))}
