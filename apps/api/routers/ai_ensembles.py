"""ensemblesystemone.structural — the ensemble picker and the System One
model catalog.

    GET    /admin/ai/ensembles?task_key=...                 picker envelope + active + history (super_admin)
    POST   /admin/ai/ensembles                              activate a new immutable config (super_admin)
    GET    /admin/ai/jobs                                   every ensemble job, its label and data kind (super_admin)
    GET    /admin/ai/daily-runs                             today's run per daily job + the last 30 days (super_admin)
    POST   /admin/ai/daily-runs/{task_key}/run              "run now": same rules, same budget (super_admin)
    GET    /admin/system-one-catalog                        System One catalog (any authenticated user; can_write = super_admin)
    POST   /admin/system-one-catalog                        add an entry (super_admin)
    DELETE /admin/system-one-catalog/{key}                  remove an entry (super_admin)
    PUT    /admin/system-one-catalog/{key}/availability     deprecated / disabled by hand (super_admin)
    PUT    /admin/system-one-catalog/{key}/default          make this entry the default (super_admin)
    POST   /admin/system-one-catalog/{key}/verify           run the real availability check (super_admin)

Mounted under /api/v1 (main.py). The System One catalog sits alongside
/admin/model-catalog (routers/org_settings.py), which stays the ONE LLM
catalog — this router builds no second LLM list. Super-admin is checked
through services.rbac.is_super_admin, the one shared helper, before anything
else. Request bodies never carry an org_id (extra='forbid'): both tables are
platform-wide, because the note-term corpus is global.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from services import ai_budget
from services import ai_daily_runs as DR
from services.ai_ensembles import (
    EnsembleConflictError,
    EnsembleValidationError,
    KNOWN_TASK_KEYS,
    TASK_ORDER,
    activate_ensemble,
    get_picker,
    task_info,
)
from services.database import get_pool
from services.authz import require_staff_or_permission
from services.rbac import is_super_admin, load_principal
from services.system_one import (
    MANUAL_AVAILABILITY,
    SystemOneCatalogError,
    add_system_one_model,
    list_system_one_models,
    remove_system_one_model,
    set_system_one_availability,
    set_system_one_default,
    verify_system_one_model,
)
from services.users import ensure_user

router = APIRouter(tags=["ai-ensembles"])


class EnsembleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_key: str
    model_1: str
    model_2: str
    system_one_model: str
    notes: str | None = None
    # aibudget1 — each model's daily budget, chosen right after the model.
    # Omitted kind = the model's default (unlimited unless it is a data-sharing model).
    model_1_budget_kind: str | None = None
    model_1_budget_value: int | float | str | None = None
    model_2_budget_kind: str | None = None
    model_2_budget_value: int | float | str | None = None


class SystemOneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str
    provider: str
    model_route: str
    notes: str | None = None


class SystemOneAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: str


async def _principal(conn, request: Request) -> dict:
    user_id = await ensure_user(conn, request)
    principal = await load_principal(conn, user_id)
    if principal is None:
        principal = {"id": user_id, "org_id": get_org_id(request), "role": None}
    return principal


async def _require_super_admin(conn, request: Request) -> dict:
    principal = await _principal(conn, request)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return principal


def _permissions(super_admin: bool) -> dict:
    return {
        "can_read": True,
        "can_write": super_admin,
        "is_super_admin": super_admin,
        "read_permission": "authenticated",
        "write_permission": "super_admin",
    }


def _rls_refusal(exc: Exception) -> HTTPException:
    # The app-layer gate passed but RLS refused: the request's context did not
    # carry is_super_admin. Never reported as success.
    return HTTPException(
        status_code=403,
        detail="Write refused by row-level security (no super-admin context)",
    )


# ── Ensemble picker ─────────────────────────────────────────────────────────

@router.get("/admin/ai/ensembles")
async def read_ai_ensembles(request: Request, task_key: str = Query(...)):
    if task_key not in KNOWN_TASK_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown task_key '{task_key}'")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_super_admin(conn, request)
        picker = await get_picker(conn, task_key)
    return {**picker, "permissions": _permissions(True)}


@router.post("/admin/ai/ensembles", status_code=201)
async def create_ai_ensemble(request: Request, body: EnsembleBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _require_super_admin(conn, request)
        try:
            result = await activate_ensemble(
                conn, body.task_key, body.model_1, body.model_2, body.system_one_model,
                created_by=principal.get("id"), notes=body.notes,
                model_1_budget_kind=body.model_1_budget_kind, model_1_budget_value=body.model_1_budget_value,
                model_2_budget_kind=body.model_2_budget_kind, model_2_budget_value=body.model_2_budget_value,
            )
        except EnsembleValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EnsembleConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            raise _rls_refusal(exc) from exc
    return {**result, "permissions": _permissions(True)}


# ── aibudget1: jobs and the daily document-reading runs ─────────────────────

def _ordered_task_keys() -> list[str]:
    return [k for k in TASK_ORDER if k in KNOWN_TASK_KEYS] + sorted(k for k in KNOWN_TASK_KEYS if k not in TASK_ORDER)


@router.get("/admin/ai/jobs")
async def read_ai_jobs(request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_super_admin(conn, request)
    return {
        "rows": [{**task_info(k), "daily": k in DR.JOBS and k not in DR.NEVER_SCHEDULED}
                 for k in _ordered_task_keys()],
        "permissions": _permissions(True),
        "day_boundary": {**ai_budget.DAY_BOUNDARY, "today": ai_budget.utc_today().isoformat()},
    }


@router.get("/admin/ai/daily-runs")
async def read_ai_daily_runs(request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_super_admin(conn, request)
    rows = await DR.overview(pool)
    return {
        "rows": rows,
        "permissions": _permissions(True),
        "vocabularies": {
            "statuses": [{"key": k, "label": v} for k, v in DR.STATUS_LABELS.items()],
            "triggers": [{"key": k, "label": v} for k, v in DR.TRIGGER_LABELS.items()],
            "budget_kinds": [dict(k) for k in ai_budget.BUDGET_KIND_OPTIONS],
            "never_scheduled": [task_info(k) for k in DR.NEVER_SCHEDULED],
        },
        "day_boundary": {**ai_budget.DAY_BOUNDARY, "today": ai_budget.utc_today().isoformat()},
        "schedule": {"starts_after_utc": "%02d:%02d" % DR.start_after(), "enabled": DR.auto_enabled(),
                     "history_days": DR.HISTORY_DAYS},
    }


@router.post("/admin/ai/daily-runs/{task_key}/run", status_code=202)
async def run_ai_daily_job_now(request: Request, task_key: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _require_super_admin(conn, request)
    if task_key not in DR.JOBS or task_key in DR.NEVER_SCHEDULED:
        raise HTTPException(status_code=422, detail=f"'{task_key}' is not a daily document job "
                                                    f"({', '.join(sorted(DR.JOBS))})")
    cfg = await DR.JOBS[task_key].load_config(pool)
    if cfg is None:
        raise HTTPException(status_code=409, detail=f"no active ensemble config for '{task_key}' — assign "
                                                    "Model 1 and Model 2 first")
    try:
        run = await DR.run_now(pool, task_key, started_by=principal.get("id"))
    except DR.RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run": run, "permissions": _permissions(True)}


# ── System One catalog ──────────────────────────────────────────────────────

@router.get("/admin/system-one-catalog")
async def read_system_one_catalog(request: Request):
    require_staff_or_permission(request, "manage_org_settings")  # BUGFIX2 — staff or org admin (settings screens)
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        rows = await list_system_one_models(conn)
    sa = is_super_admin(principal)
    return {
        "rows": rows,
        "permissions": _permissions(sa),
        "vocabularies": {
            # 'available' is never settable by hand — only Verify now earns it.
            "manual_availability": list(MANUAL_AVAILABILITY) if sa else [],
            "editable": ["availability", "is_default"] if sa else [],
        },
    }


async def _catalog_write(request: Request, fn, *args, **kwargs):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_super_admin(conn, request)
        try:
            return await fn(conn, *args, **kwargs)
        except SystemOneCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            raise _rls_refusal(exc) from exc
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except asyncpg.CheckViolationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/system-one-catalog", status_code=201)
async def create_system_one_entry(request: Request, body: SystemOneCreate):
    return await _catalog_write(
        request, add_system_one_model,
        key=body.key, display_name=body.display_name, provider=body.provider,
        model_route=body.model_route, notes=body.notes,
    )


@router.delete("/admin/system-one-catalog/{key}")
async def delete_system_one_entry(request: Request, key: str):
    await _catalog_write(request, remove_system_one_model, key)
    return {"key": key, "removed": True}


@router.put("/admin/system-one-catalog/{key}/availability")
async def put_system_one_availability(request: Request, key: str, body: SystemOneAvailability):
    return await _catalog_write(request, set_system_one_availability, key, body.availability)


@router.put("/admin/system-one-catalog/{key}/default")
async def put_system_one_default(request: Request, key: str):
    return await _catalog_write(request, set_system_one_default, key)


@router.post("/admin/system-one-catalog/{key}/verify")
async def verify_system_one_entry(request: Request, key: str):
    return await _catalog_write(request, verify_system_one_model, key)
