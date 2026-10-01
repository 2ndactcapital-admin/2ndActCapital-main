"""ensemblemodels.structural — super-admin control over the hazard ensemble's
three models (Review model 1, Review model 2, Comparison model).

    GET  /admin/ai/model-catalog                    union catalog (LiteLLM + judgment models)
    GET  /admin/ai/ensembles?task_key=...           active selection + full history
    POST /admin/ai/ensembles                        activate a new selection (new immutable row)

Mounted under /api/v1 (main.py), like every other /admin/* route. All three are
super_admin only: the ensemble governs GLOBAL structured-note data that no org
owns. The super_admin check goes through services.rbac.is_super_admin, the one
shared helper, checked before anything else.

Distinct from /admin/model-catalog (routers/org_settings.py), which is the
curated LiteLLM list orgs pick from. This catalog unions that curation with
the live /model/info versions and the judgment-model registry.
"""

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from services.ai_model_catalog import (
    CatalogUnavailableError,
    EnsembleConflictError,
    EnsembleValidationError,
    KNOWN_TASK_KEYS,
    activate_ensemble,
    get_catalog,
    list_ensembles,
)
from services.database import get_pool
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user

router = APIRouter(tags=["ai-ensembles"])

_PERMISSIONS = {
    "can_read": True,
    "can_write": True,
    "is_super_admin": True,
    "read_permission": "super_admin",
    "write_permission": "super_admin",
}


class EnsembleBody(BaseModel):
    # No org_id, ever (CLAUDE.md Rule 6) — extra='forbid' makes that mechanical.
    model_config = ConfigDict(extra="forbid")

    task_key: str
    review_model_1: str
    review_model_2: str
    comparison_model: str
    notes: str | None = None


async def _require_super_admin(conn, request: Request) -> dict:
    user_id = await ensure_user(conn, request)
    principal = await load_principal(conn, user_id)
    if principal is None:
        principal = {"id": user_id, "org_id": get_org_id(request), "role": None}
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return principal


@router.get("/admin/ai/model-catalog")
async def read_ai_model_catalog(request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_super_admin(conn, request)
        try:
            catalog = await get_catalog(conn)
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "rows": catalog,
        "permissions": _PERMISSIONS,
        "vocabularies": {
            "availability_states": ["available", "deprecated", "disabled"],
            "review_model_kinds": ["llm"],
            "comparison_model_kinds": ["llm", "judgment"],
        },
    }


@router.get("/admin/ai/ensembles")
async def read_ai_ensembles(request: Request, task_key: str = Query(...)):
    if task_key not in KNOWN_TASK_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown task_key '{task_key}'")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_super_admin(conn, request)
        history = await list_ensembles(conn, task_key)
    active = next((r for r in history if r["is_active"]), None)
    return {
        "task_key": task_key,
        "active": active,
        "rows": history,
        "permissions": _PERMISSIONS,
    }


@router.post("/admin/ai/ensembles", status_code=201)
async def create_ai_ensemble(request: Request, body: EnsembleBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _require_super_admin(conn, request)
        try:
            result = await activate_ensemble(
                conn,
                body.task_key,
                body.review_model_1,
                body.review_model_2,
                body.comparison_model,
                created_by=principal.get("id"),
                notes=body.notes,
            )
        except EnsembleValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EnsembleConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            # The app-layer gate passed but RLS refused: the request's RLS
            # context did not carry is_super_admin. Never report it as success.
            raise HTTPException(
                status_code=403,
                detail="Write refused by row-level security (no super-admin context)",
            ) from exc
    return {**result, "permissions": _PERMISSIONS}
