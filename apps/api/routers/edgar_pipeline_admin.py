"""EDGAR pipeline monitoring — SUPER ADMIN ONLY, not org-scoped.

    GET  /admin/edgar/filings          the manifest: server-side filter, sort, page
    GET  /admin/edgar/progress         counts by status per quarter + recent runs
    GET  /admin/edgar/issuers          issuer table + unlisted 424B2 filers
    PUT  /admin/edgar/issuers/{cik}    edit include_status / credit_entity / notes
    POST /admin/edgar/issuers          add an issuer (e.g. promote an unlisted filer)
    POST /admin/edgar/runs             "Run now": launch the same job, with a fetch cap

Everything here is GLOBAL public SEC reference data (the manifest, the issuer
table, the pipeline runs) — no org_id is read or accepted anywhere. The gate is
``services.rbac.is_super_admin`` on the caller's principal, the same shape as
routers/pricing_admin.py. Every response publishes the permission envelope;
``editable`` lists are empty arrays whenever the caller cannot write (never
reached today — a non-super-admin gets the 403 first — but the shape is the
contract the client renders from).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services import edgar_pipeline, edgar_pipeline_admin as admin
from services.database import get_pool
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user

router = APIRouter(tags=["admin", "edgar"])

PERMISSION = "super_admin"


async def _require_super_admin(request: Request) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id


def _permissions(can_write: bool) -> dict:
    return {
        "can_read": True,
        "can_write": can_write,
        "is_super_admin": True,
        "read_permission": PERMISSION,
        "write_permission": PERMISSION,
    }


def _bad_input(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ── Models (extra='forbid': no org_id, no stray fields) ─────────────────────
class IssuerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_status: str | None = None
    credit_entity: str | None = None
    notes: str | None = None


class IssuerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filer_cik: str
    issuer_group: str
    filer_name: str
    filer_role: str
    credit_entity: str
    include_status: str
    notes: str | None = None


class RunNow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fetch_cap: int = Field(ge=0, le=edgar_pipeline.MAX_FETCH_CAP)


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/admin/edgar/filings")
async def edgar_filings(
    request: Request,
    issuer_group: str | None = None,
    form_type: str | None = None,
    status: str | None = None,
    document_kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    quarter: str | None = None,
    sort: str = "filing_date",
    direction: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=admin.MAX_PAGE_SIZE),
):
    await _require_super_admin(request)
    filters = {
        "issuer_group": issuer_group, "form_type": form_type, "status": status,
        "document_kind": document_kind, "date_from": date_from, "date_to": date_to,
        "quarter": quarter,
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await admin.list_filings(
                conn, filters=filters, sort=sort, direction=direction,
                page=page, page_size=page_size,
            )
        except admin.EdgarAdminInputError as exc:
            raise _bad_input(exc) from exc
        groups = await admin.issuer_groups(conn)
        quarters = await admin.quarters(conn)
    return {
        **result,
        "filters": {k: v for k, v in filters.items() if v},
        "permissions": _permissions(can_write=False),
        "vocabularies": {
            "editable": [],
            "inline_editable": [],
            "statuses": admin.STATUS_LABELS,
            "document_kinds": admin.DOCUMENT_KIND_LABELS,
            "form_types": list(admin.FORM_TYPES),
            "issuer_groups": groups,
            "unlisted_group": admin.UNLISTED_GROUP,
            "quarters": quarters,
            "sortable": list(admin.SORT_COLUMNS),
            "max_page_size": admin.MAX_PAGE_SIZE,
        },
    }


@router.get("/admin/edgar/progress")
async def edgar_progress(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await admin.progress(conn)
    return {
        **result,
        "permissions": _permissions(can_write=True),
        "vocabularies": {
            "editable": [],
            "inline_editable": [],
            "statuses": admin.STATUS_LABELS,
            "run_now": {
                "default_fetch_cap": edgar_pipeline.fetch_cap_default(),
                "max_fetch_cap": edgar_pipeline.MAX_FETCH_CAP,
            },
        },
    }


@router.get("/admin/edgar/issuers")
async def edgar_issuers(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        issuers = await admin.list_issuers(conn)
        unlisted = await admin.unlisted_filers(conn)
    return {
        "rows": issuers,
        "unlisted_filers": unlisted,
        "permissions": _permissions(can_write=True),
        "vocabularies": {
            "editable": list(admin.ISSUER_EDITABLE),
            "inline_editable": [],
            "include_status": admin.INCLUDE_STATUS_LABELS,
            "filer_roles": admin.FILER_ROLE_LABELS,
        },
    }


# ── Writes ──────────────────────────────────────────────────────────────────
@router.put("/admin/edgar/issuers/{cik}")
async def edgar_update_issuer(cik: str, body: IssuerUpdate, request: Request):
    await _require_super_admin(request)
    changes = body.model_dump(exclude_unset=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await admin.update_issuer(conn, cik, changes)
        except admin.EdgarAdminInputError as exc:
            raise _bad_input(exc) from exc
    if not result:
        raise HTTPException(status_code=404, detail=f"issuer {cik} is not in the issuer table")
    return result


@router.post("/admin/edgar/issuers", status_code=201)
async def edgar_add_issuer(body: IssuerCreate, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await admin.add_issuer(conn, body.model_dump())
        except admin.EdgarAdminInputError as exc:
            raise _bad_input(exc) from exc


@router.post("/admin/edgar/runs", status_code=202)
async def edgar_run_now(body: RunNow, request: Request):
    """Launch the same job the nightly workflow launches. Returns at once; the
    run row (status launched / refused / launch_failed) is the answer."""
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await edgar_pipeline.launch_pipeline_run(
            conn, trigger_source="manual", requested_by=actor_id, fetch_cap=body.fetch_cap,
        )
    return {"run": row}
