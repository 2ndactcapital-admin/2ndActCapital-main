"""REP3 — Replication Studio: model management (/models).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_models     GET   /models                                     list + envelope
                  GET   /models/template                            .xlsx / .csv template (server field vocabulary)
                  GET   /models/uploads                             the upload log
                  GET   /models/uploads/export.csv                  the upload log, every row (formula-safe CSV)
                  GET   /models/mappings                            saved provider column mappings
                  GET   /models/library                             platform shelf + our shelf
                  GET   /models/{id}                                one model: versions, positions, uploads, log
                  GET   /models/{id}/versions/{vid}                 one version: positions, sleeves, look-through
                  GET   /models/{id}/versions/{vid}/diff            its diff against the version it would supersede
  manage_models   POST  /models                                     a composite (sleeves) model
                  PATCH /models/{id}                                name, description, currency, default fee schedule, retire
                  POST  /models/{id}/drafts                         a new draft from the approved version
                  POST  /models/{id}/versions/{vid}/positions       edit a draft's positions (upload validation)
                  PUT   /models/{id}/versions/{vid}/sleeves         set a composite draft's sleeves
                  POST  /models/{id}/versions/{vid}/submit          open the approval task
                  POST  /models/{id}/versions/{vid}/abandon         abandon a draft (logged; upload → abandoned)
                  POST  /models/uploads                             receive a file (multipart) — always logged
                  POST  /models/uploads/{uid}/preview               sheet → header → mapping → validation report
                  POST  /models/uploads/{uid}/confirm               create the draft (or log 'rejected', 422)
                  POST  /models/uploads/{uid}/abandon
                  POST  /models/mappings                            save a provider mapping (logged)
                  POST  /models/library                             publish (platform shelf: Super Admin; org shelf:
                                                                    an org admin — services.models re-checks)
                  POST  /models/library/{eid}/status                unpublish / retire (same rule)
                  POST  /models/library/{eid}/agreement             "agreement on file" (an org admin)
                  POST  /models/library/{eid}/copy                  an independent org model
                  POST  /models/library/{eid}/subscribe             an org model that follows the entry
  approve_models  POST  /models/{id}/versions/{vid}/approve         (never for a maker of the version)
                  POST  /models/{id}/versions/{vid}/reject          (a note is required; never for a maker)

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; request models forbid extras.
Another org's model, version, upload or org-shelf entry is a 404.
THE CUSTODY CLIFF: nothing here trades — approval emits model.version_approved.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import human_tasks as HT
from services import model_upload as U
from services import models as M
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["models"])

VIEW = M.VIEW
MANAGE = M.MANAGE
APPROVE = M.APPROVE
EDITABLE_FIELDS = ["raw_ticker", "raw_cusip", "raw_isin", "raw_sedol", "raw_name", "weight_pct", "asset_class",
                   "sleeve_label", "drift_min_pct", "drift_max_pct", "alternates", "restrictions", "notes", "is_cash"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SleeveIn(_Body):
    child_model_id: str = Field(min_length=1, max_length=64)
    weight_pct: str = Field(min_length=1, max_length=40)
    follow: str = Field(default="live", max_length=10)
    pinned_version_id: str | None = Field(default=None, max_length=64)


class CompositeCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    base_currency: str = Field(default="USD", max_length=3)
    sleeves: list[SleeveIn] = Field(min_length=1, max_length=50)


class ModelPatch(_Body):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    base_currency: str | None = Field(default=None, max_length=3)
    default_fee_schedule_id: str | None = Field(default=None, max_length=64)
    clear_fee_schedule: bool = False
    status: str | None = Field(default=None, max_length=20)


class PositionChange(_Body):
    op: str = Field(pattern="^(upsert|remove)$")
    row_num: int | None = Field(default=None, ge=1, le=100000)
    raw_ticker: str | None = Field(default=None, max_length=40)
    raw_cusip: str | None = Field(default=None, max_length=20)
    raw_isin: str | None = Field(default=None, max_length=20)
    raw_sedol: str | None = Field(default=None, max_length=20)
    raw_name: str | None = Field(default=None, max_length=300)
    weight_pct: str | None = Field(default=None, max_length=40)
    asset_class: str | None = Field(default=None, max_length=100)
    sleeve_label: str | None = Field(default=None, max_length=100)
    drift_min_pct: str | None = Field(default=None, max_length=40)
    drift_max_pct: str | None = Field(default=None, max_length=40)
    alternates: list[str] | None = Field(default=None, max_length=20)
    restrictions: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)
    is_cash: bool | None = None


class PositionEdit(_Body):
    changes: list[PositionChange] = Field(min_length=1, max_length=500)


class SleevesPut(_Body):
    sleeves: list[SleeveIn] = Field(min_length=1, max_length=50)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class UploadStep(_Body):
    sheet: str | None = Field(default=None, max_length=200)
    header_row: int | None = Field(default=None, ge=1, le=U.HEADER_SCAN_ROWS * 10)
    mapping: dict[str, str] | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    fill_with_cash: bool = False
    model_id: str | None = Field(default=None, max_length=64)


class UploadConfirm(UploadStep):
    model_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class MappingSave(_Body):
    provider_name: str = Field(min_length=1, max_length=200)
    mapping: dict[str, str]
    sheet_hint: str | None = Field(default=None, max_length=200)
    header_row_hint: int | None = Field(default=None, ge=1, le=300)
    upload_id: str | None = Field(default=None, max_length=64)


class Publish(_Body):
    model_id: str = Field(min_length=1, max_length=64)
    shelf: str = Field(pattern="^(platform|org)$")
    title: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    update_cadence: str = Field(min_length=1, max_length=20)
    licensing_note: str | None = Field(default=None, max_length=2000)
    requires_agreement: bool = False


class EntryStatus(_Body):
    status: str = Field(pattern="^(unpublished|retired)$")
    reason: str = Field(min_length=1, max_length=2000)


class Agreement(_Body):
    note: str | None = Field(default=None, max_length=2000)


class LoadFromLibrary(_Body):
    name: str | None = Field(default=None, max_length=200)


def _raise(exc: Exception):
    if isinstance(exc, M.ModelNotFound) or isinstance(exc, HT.HumanTaskNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, M.ModelConflict) or isinstance(exc, HT.HumanTaskConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (M.MakerCheckerViolation, M.ModelPermissionError, HT.AgentActorRefused)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, M.ModelError):
        raise HTTPException(status_code=422, detail={"message": str(exc), "messages": exc.messages}) from exc
    if isinstance(exc, U.UploadRefused):
        raise HTTPException(status_code=422, detail={"message": exc.text, "messages": [
            U.Message(exc.code, "error", exc.text).as_dict()]}) from exc
    if isinstance(exc, HT.HumanTaskError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _permissions(principal) -> dict:
    sa = principal.is_super_admin
    perms = principal.permissions
    can_write = sa or MANAGE in perms
    org_admin = sa or M.ORG_ADMIN_PERMISSION in perms
    return {
        "can_read": sa or VIEW in perms, "can_write": can_write, "can_approve": sa or APPROVE in perms,
        "is_super_admin": sa, "is_org_admin": org_admin,
        "can_publish_org": can_write and org_admin, "can_publish_platform": can_write and sa,
        "can_confirm_agreement": can_write and org_admin,
        "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
    }


def _vocab(perms: dict) -> dict:
    return {**M.vocabularies(), "editable": EDITABLE_FIELDS if perms["can_write"] else [],
            "inline_editable": EDITABLE_FIELDS if perms["can_write"] else []}


def _version_actions(v: dict | None, principal, perms: dict) -> list[str]:
    if not v:
        return []
    acts: list[str] = []
    maker = str(principal.user_id) in (v.get("maker_ids") or [])
    if v["status"] == "draft" and perms["can_write"]:
        acts += ["edit", "submit", "abandon"]
    if v["status"] == "submitted":
        if perms["can_write"]:
            acts.append("abandon")
        if perms["can_approve"] and not maker:
            acts += ["approve", "reject"]
    return acts


def _version_envelope(detail: dict, principal, perms: dict) -> dict:
    v = detail["version"]
    return {**detail, "actions": _version_actions(v, principal, perms),
            "is_maker": str(principal.user_id) in (v.get("maker_ids") or [])}


# ── list / create ───────────────────────────────────────────────────────────
@router.get("/models")
async def list_models(request: Request, source: str | None = Query(default=None, max_length=40),
                      status: str | None = Query(default=None, max_length=20),
                      q: str | None = Query(default=None, max_length=200)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await M.list_models(conn, org_id, source=source, status=status, q=q)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms),
            "actions": ["upload", "load_from_library", "create_composite"] if perms["can_write"] else []}


@router.post("/models", status_code=201)
async def create_composite(request: Request, body: CompositeCreate):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        model, version = await M.create_composite(pool, org_id=org_id, actor_id=principal.user_id, name=body.name,
                                                  description=body.description, base_currency=body.base_currency,
                                                  sleeves=[s.model_dump() for s in body.sleeves])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"model": M.model_out(model), "version": M.version_out(version)}


# ── template ────────────────────────────────────────────────────────────────
@router.get("/models/template")
async def template(request: Request, format: str = Query(default="xlsx", pattern="^(xlsx|csv)$")):
    require_permission(request, VIEW)
    if format == "csv":
        return Response(U.template_csv(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="model_template.csv"'})
    return Response(U.template_xlsx(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="model_template.xlsx"'})


# ── uploads (the log) ───────────────────────────────────────────────────────
@router.get("/models/uploads")
async def upload_log(request: Request, outcome: str | None = Query(default=None, max_length=20),
                     provider: str | None = Query(default=None, max_length=200),
                     model_id: str | None = Query(default=None, max_length=64),
                     since: date | None = None, until: date | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await M.upload_log(conn, org_id, outcome=outcome, provider=provider, model_id=model_id,
                                      since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/models/uploads/export.csv")
async def upload_log_export(request: Request):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await M.upload_log(conn, org_id, limit=None)
    return Response(M.upload_log_csv(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="model_upload_log.csv"'})


async def _read_capped(file: UploadFile) -> tuple[bytes, int, str]:
    """The first MAX_BYTES + 1 bytes, plus the TRUE size and SHA-256 of the
    whole stream (hashed chunk by chunk) — an oversize file is logged with its
    real facts, never held in memory whole."""
    h = hashlib.sha256()
    kept = bytearray()
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        h.update(chunk)
        if len(kept) <= U.MAX_BYTES:
            kept.extend(chunk[: U.MAX_BYTES + 1 - len(kept)])
    return bytes(kept), total, h.hexdigest()


@router.post("/models/uploads", status_code=201)
async def receive_upload(request: Request, file: UploadFile = File(...),
                         provider_name: str | None = Form(default=None, max_length=200),
                         source: str = Form(default="manual", max_length=20),
                         model_id: str | None = Form(default=None, max_length=64)):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    data, size, sha = await _read_capped(file)
    pool = await get_pool()
    try:
        out = await M.receive_upload(pool, org_id=org_id, actor_id=principal.user_id, data=data,
                                     filename=file.filename or "upload", content_type=file.content_type,
                                     provider_name=provider_name, source=source, model_id=model_id,
                                     size_bytes=size, sha256_hex=sha)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    if out.get("refused"):
        raise HTTPException(status_code=422, detail={"message": out["refused"]["text"], "messages": [out["refused"]],
                                                     "upload": out["upload"]})
    return out


@router.post("/models/uploads/{upload_id}/preview")
async def preview_upload(request: Request, upload_id: str, body: UploadStep):
    require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        return await M.preview_upload(pool, org_id=org_id, upload_id=upload_id, sheet=body.sheet,
                                      header_row=body.header_row, mapping=body.mapping,
                                      provider_name=body.provider_name, fill_with_cash=body.fill_with_cash,
                                      model_id=body.model_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/models/uploads/{upload_id}/confirm", status_code=201)
async def confirm_upload(request: Request, upload_id: str, body: UploadConfirm):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await M.confirm_upload(pool, org_id=org_id, actor_id=principal.user_id, upload_id=upload_id,
                                     sheet=body.sheet, header_row=body.header_row, mapping=body.mapping,
                                     provider_name=body.provider_name, fill_with_cash=body.fill_with_cash,
                                     model_id=body.model_id, model_name=body.model_name, description=body.description)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"upload": out["upload"], "model": M.model_out(out["model"]), "version": M.version_out(out["version"]),
            "report": out["report"]}


@router.post("/models/uploads/{upload_id}/abandon")
async def abandon_upload(request: Request, upload_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        return {"upload": await M.abandon_upload(pool, org_id=org_id, actor_id=principal.user_id, upload_id=upload_id,
                                                 reason=body.reason)}
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


# ── mappings ────────────────────────────────────────────────────────────────
@router.get("/models/mappings")
async def list_mappings(request: Request):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM {M.T_MAP} WHERE org_id = $1::uuid AND valid_to IS NULL AND "
                                "system_to IS NULL ORDER BY provider_name", str(org_id))
    return {"rows": [M._mapping_out({**dict(r), "mapping": M._loads(r["mapping"])}) for r in rows]}


@router.post("/models/mappings")
async def save_mapping(request: Request, body: MappingSave):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        return {"mapping": await M.save_mapping(pool, org_id=org_id, actor_id=principal.user_id,
                                                provider_name=body.provider_name, mapping=body.mapping,
                                                sheet_hint=body.sheet_hint, header_row_hint=body.header_row_hint,
                                                upload_id=body.upload_id)}
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


# ── library ─────────────────────────────────────────────────────────────────
@router.get("/models/library")
async def library(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await M.list_library(conn, org_id)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms)}


@router.post("/models/library", status_code=201)
async def publish(request: Request, body: Publish):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        entry = await M.publish_entry(pool, org_id=org_id, actor_id=principal.user_id,
                                      is_super_admin=principal.is_super_admin, is_org_admin=perms["is_org_admin"],
                                      model_id=body.model_id, shelf=body.shelf, title=body.title,
                                      provider=body.provider, description=body.description,
                                      update_cadence=body.update_cadence, licensing_note=body.licensing_note,
                                      requires_agreement=body.requires_agreement)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"entry": {"id": str(entry["id"]), "shelf": entry["shelf"], "title": entry["title"],
                      "status": entry["status"]}}


@router.post("/models/library/{entry_id}/status")
async def entry_status(request: Request, entry_id: str, body: EntryStatus):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        e = await M.change_entry_status(pool, org_id=org_id, actor_id=principal.user_id,
                                        is_super_admin=principal.is_super_admin, is_org_admin=perms["is_org_admin"],
                                        entry_id=entry_id, status=body.status, reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"entry": {"id": str(e["id"]), "status": e["status"]}}


@router.post("/models/library/{entry_id}/agreement")
async def agreement(request: Request, entry_id: str, body: Agreement | None = None):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        return await M.confirm_agreement(pool, org_id=org_id, actor_id=principal.user_id,
                                         is_super_admin=principal.is_super_admin, is_org_admin=perms["is_org_admin"],
                                         entry_id=entry_id, note=body.note if body else None)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


async def _load(request: Request, entry_id: str, body: LoadFromLibrary | None, subscribe: bool):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        fn = M.subscribe_entry if subscribe else M.copy_entry
        out = await fn(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                       name=body.name if body else None)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"model": M.model_out(out["model"]), "version": M.version_out(out["version"])}


@router.post("/models/library/{entry_id}/copy", status_code=201)
async def copy_entry(request: Request, entry_id: str, body: LoadFromLibrary | None = None):
    return await _load(request, entry_id, body, False)


@router.post("/models/library/{entry_id}/subscribe", status_code=201)
async def subscribe_entry(request: Request, entry_id: str, body: LoadFromLibrary | None = None):
    return await _load(request, entry_id, body, True)


# ── one model ───────────────────────────────────────────────────────────────
@router.get("/models/{model_id}")
async def model_detail(request: Request, model_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await M.model_detail(conn, org_id, model_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    m = d["model"]
    has_open = d["open"] is not None
    has_live = d["live"] is not None
    acts: list[str] = []
    if perms["can_write"]:
        if has_live and not has_open:
            acts.append("new_draft")
        if m["source"] == "upload" and not has_open:
            acts.append("upload_version")
        if m["status"] != "retired" and not has_open:
            acts.append("retire")
        acts.append("edit_details")
        published = any(e["status"] == "published" for e in d["library_entries"])
        if has_live and not published and m["source"] not in ("library_copy", "library_subscription"):
            if perms["can_publish_org"]:
                acts.append("publish_org")
            if perms["can_publish_platform"] and str(org_id) == M.PLATFORM_ORG_ID:
                acts.append("publish_platform")
    return {**d, "live": _version_envelope(d["live"], principal, perms) if d["live"] else None,
            "open": _version_envelope(d["open"], principal, perms) if d["open"] else None,
            "actions": acts, "permissions": perms, "vocabularies": _vocab(perms)}


@router.patch("/models/{model_id}")
async def update_model(request: Request, model_id: str, body: ModelPatch):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    changes: dict[str, Any] = {k: v for k, v in body.model_dump(exclude_unset=True).items()
                               if k != "clear_fee_schedule" and v is not None}
    if body.clear_fee_schedule:
        changes["default_fee_schedule_id"] = None
    pool = await get_pool()
    try:
        m = await M.update_model(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id, changes=changes)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"model": M.model_out(m)}


@router.post("/models/{model_id}/drafts", status_code=201)
async def new_draft(request: Request, model_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        v = await M.new_draft(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": M.version_out(v)}


@router.get("/models/{model_id}/versions/{version_id}")
async def version_detail(request: Request, model_id: str, version_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await M.version_detail(conn, org_id, model_id, version_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {**_version_envelope(d, principal, perms), "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/models/{model_id}/versions/{version_id}/diff")
async def version_diff(request: Request, model_id: str, version_id: str):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            v = await M.load_version(conn, org_id, model_id, version_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version_id": str(v["id"]), "version_no": v["version_no"], "diff": M._loads(v["diff"]) or {}}


@router.post("/models/{model_id}/versions/{version_id}/positions")
async def edit_positions(request: Request, model_id: str, version_id: str, body: PositionEdit):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        v, notes = await M.edit_positions(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id,
                                          version_id=version_id,
                                          changes=[c.model_dump(exclude_unset=True) for c in body.changes])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": M.version_out(v), "messages": notes}


@router.put("/models/{model_id}/versions/{version_id}/sleeves")
async def set_sleeves(request: Request, model_id: str, version_id: str, body: SleevesPut):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        v = await M.set_sleeves(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id,
                                version_id=version_id, sleeves=[s.model_dump() for s in body.sleeves])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": M.version_out(v)}


@router.post("/models/{model_id}/versions/{version_id}/submit")
async def submit(request: Request, model_id: str, version_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        v = await M.submit(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id, version_id=version_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": M.version_out(v)}


async def _decide(request: Request, model_id: str, version_id: str, body: Decision | None, outcome: str):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        v = await M.decide(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id, version_id=version_id,
                           outcome=outcome, note=body.note if body else None)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": M.version_out(v)}


@router.post("/models/{model_id}/versions/{version_id}/approve")
async def approve(request: Request, model_id: str, version_id: str, body: Decision | None = None):
    return await _decide(request, model_id, version_id, body, "approve")


@router.post("/models/{model_id}/versions/{version_id}/reject")
async def reject(request: Request, model_id: str, version_id: str, body: Decision | None = None):
    return await _decide(request, model_id, version_id, body, "reject")


@router.post("/models/{model_id}/versions/{version_id}/abandon")
async def abandon(request: Request, model_id: str, version_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        v = await M.abandon(pool, org_id=org_id, actor_id=principal.user_id, model_id=model_id,
                            version_id=version_id, reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": M.version_out(v)}
