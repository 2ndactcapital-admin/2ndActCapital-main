"""REP4 — Replication Studio: build a model from a fund (/replication/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_replication   GET  /replication/registry/{id}/model                the Model tab: sources, the entry's
                                                                         fund models, reports, monitoring
                     POST /replication/registry/{id}/viability            compute + store a viability report
                     GET  /replication/registry/{id}/viability            the entry's reports, newest first
                     GET  /replication/registry/{id}/viability/{rid}      one report
                     GET  /replication/registry/{id}/models/find          the live model for a registry key
  manage_models      POST /replication/registry/{id}/models/preview       what a build would create (writes nothing)
  + view_replication POST /replication/registry/{id}/models               find or build: the existing model, or a
                                                                         new "Holdings of" model + DRAFT version
  view_models        GET  /replication/models/{model_id}/spec              a fund model's specs + monitoring log

A viability report is computed AND stored (immutable) — hence a POST with the
read gate: it changes nothing but its own report row.

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; request models forbid extras.
Another org's entry, report or model is a 404. THE CUSTODY CLIFF: nothing here
trades — a build is a draft that rep3's maker-checker task approves.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import human_tasks as HT
from services import models as M
from services import replica_models as RM
from services import replica_terms as T
from services import replica_viability as RV
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["replication-models"])

VIEW = "view_replication"
BUILD = M.MANAGE
VIEW_MODELS = M.VIEW
BUILD_FIELDS = ["custodian_code", "method", "top_n", "min_weight_pct", "cash_target_pct", "unresolved_policy",
                "rebalance_frequency", "rebalance_reason", "drift_threshold_pct", "turnover_review_threshold_pct",
                "description"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ViabilityIn(_Body):
    custodian_code: str = Field(min_length=1, max_length=40)
    account_size: str | None = Field(default=None, max_length=40)
    method: str = Field(default="full", max_length=10)
    top_n: int | None = Field(default=None, ge=1, le=100000)


class BuildIn(_Body):
    custodian_code: str = Field(min_length=1, max_length=40)
    method: str = Field(default="full", max_length=10)
    top_n: int | None = Field(default=None, ge=1, le=100000)
    min_weight_pct: str | None = Field(default=None, max_length=40)
    cash_target_pct: str | None = Field(default=None, max_length=40)
    unresolved_policy: str = Field(default="block", max_length=30)
    rebalance_frequency: str | None = Field(default=None, max_length=40)
    rebalance_reason: str | None = Field(default=None, max_length=2000)
    drift_threshold_pct: str | None = Field(default=None, max_length=40)
    turnover_review_threshold_pct: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=4000)


def _raise(exc: Exception):
    if isinstance(exc, RM.BuildPrecondition):
        raise HTTPException(status_code=409, detail={"message": str(exc), "missing": exc.missing}) from exc
    if isinstance(exc, (M.ModelNotFound, T.ReplicaNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (M.ModelConflict, T.ReplicaConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (M.MakerCheckerViolation, M.ModelPermissionError, HT.AgentActorRefused)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, M.ModelError):
        raise HTTPException(status_code=422, detail={"message": str(exc), "messages": exc.messages}) from exc
    raise exc


def _permissions(principal) -> dict:
    su = principal.is_super_admin
    perms = principal.permissions
    return {
        "can_read": su or VIEW in perms,
        "can_build": su or (BUILD in perms and VIEW in perms),
        "can_view_models": su or VIEW_MODELS in perms,
        "is_super_admin": su,
        "read_permission": VIEW, "write_permission": BUILD, "model_read_permission": VIEW_MODELS,
    }


async def _vocab(conn, perms: dict) -> dict:
    reg = {r["field_key"]: r for r in await T.load_registry(conn)}
    freq = reg.get("rebalance_frequency") or {}
    return {
        **RM.vocabularies(), **RV.vocabularies(), "events": M.vocabularies()["events"],
        "rebalance_frequencies": [{"key": v, "label": v.replace("_", " ").capitalize()}
                                  for v in (freq.get("enum_values") or [])],
        "editable": list(BUILD_FIELDS) if perms["can_build"] else [],
    }


def _params(body: BuildIn) -> RM.BuildParams:
    return RM.params_from_input(
        custodian_code=body.custodian_code, method=body.method, top_n=body.top_n, min_weight_pct=body.min_weight_pct,
        cash_target_pct=body.cash_target_pct, unresolved_policy=body.unresolved_policy,
        rebalance_frequency=body.rebalance_frequency, rebalance_reason=body.rebalance_reason,
        drift_threshold_pct=body.drift_threshold_pct, turnover_review_threshold_pct=body.turnover_review_threshold_pct)


@router.get("/replication/registry/{entry_id}/model")
async def model_tab(request: Request, entry_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            src = await RM.load_sources(conn, org_id, entry_id)
        except Exception as exc:  # noqa: BLE001
            _raise(exc)
        size, size_origin = await RV.setting(conn, org_id, "replication.viability.default_account_size")
        stale_days, _ = await RV.setting(conn, org_id, "replication.viability.nport_stale_days")
        drift, _ = await RM._setting(conn, org_id, RM.DRIFT_SETTING, RM.DEFAULT_DRIFT)
        turnover, _ = await RM._setting(conn, org_id, RM.TURNOVER_SETTING, RM.DEFAULT_TURNOVER)
        models = await RM.models_of_entry(conn, org_id, src.entry["id"])
        reports = await RV.list_reports(conn, org_id, src.entry["id"], limit=10)
        events = await RM.monitoring_events(conn, org_id, src.entry["id"])
        vocab = await _vocab(conn, perms)
    snap = src.snapshot
    age = (RV._today() - snap["as_of"]).days if snap else None
    e = src.entry
    return {
        "entry": {"id": str(e["id"]), "ticker": e["ticker"], "display_name": e["display_name"],
                  "instrument_class": e["instrument_class"], "disclosure": T.disclosure_for(e["ticker"])},
        "permissions": perms,
        "vocabularies": vocab,
        "sources": {
            "terms_version": {"id": str(src.terms["id"]), "version_no": src.terms["version_no"],
                              "approved_at": M._iso(src.terms["approved_at"])} if src.terms else None,
            "snapshot": {"id": str(snap["id"]), "as_of": snap["as_of"].isoformat(), "source_key": snap["source_key"],
                         "age_days": age, "row_count": snap["row_count"],
                         "nport_stale": snap["source_key"] == "sec_nport" and age > int(stale_days)} if snap else None,
            "missing": src.missing(),
        },
        "defaults": {"account_size": str(size), "account_size_origin": size_origin,
                     "drift_threshold": str(drift), "turnover_review_threshold": str(turnover),
                     "method": "full", "unresolved_policy": "block", "custodian_code": "altruist"},
        "models": models,
        "reports": reports,
        "monitoring": events,
    }


@router.post("/replication/registry/{entry_id}/viability", status_code=201)
async def compute_viability(request: Request, entry_id: str, body: ViabilityIn):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        return await RV.compute(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                                custodian_code=body.custodian_code, account_size=body.account_size,
                                method=body.method, top_n=body.top_n)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/replication/registry/{entry_id}/viability")
async def list_viability(request: Request, entry_id: str, limit: int = Query(default=50, ge=1, le=500)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            entry = await T.load_entry(conn, org_id, entry_id)
        except Exception as exc:  # noqa: BLE001
            _raise(exc)
        rows = await RV.list_reports(conn, org_id, entry["id"], limit=limit)
        vocab = await _vocab(conn, _permissions(principal))
    return {"rows": rows, "permissions": _permissions(principal), "vocabularies": vocab}


@router.get("/replication/registry/{entry_id}/viability/{report_id}")
async def get_viability(request: Request, entry_id: str, report_id: str):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            entry = await T.load_entry(conn, org_id, entry_id)
            return await RV.get_report(conn, org_id, entry["id"], report_id)
        except Exception as exc:  # noqa: BLE001
            _raise(exc)


@router.get("/replication/registry/{entry_id}/models/find")
async def find_model(request: Request, entry_id: str, custodian_code: str = Query(min_length=1, max_length=40),
                     method: str = Query(default="full", max_length=10),
                     top_n: int | None = Query(default=None, ge=1, le=100000)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        p = RM.params_from_input(custodian_code=custodian_code, method=method, top_n=top_n)
        async with pool.acquire() as conn:
            entry = await T.load_entry(conn, org_id, entry_id)
            found = await RM.find_model(conn, org_id, entry["id"], p)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"model": found, "permissions": _permissions(principal)}


@router.post("/replication/registry/{entry_id}/models/preview")
async def preview_model(request: Request, entry_id: str, body: BuildIn):
    require_permission(request, BUILD)
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        return await RM.preview(pool, org_id=org_id, entry_id=entry_id, p=_params(body))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/replication/registry/{entry_id}/models", status_code=201)
async def build_model(request: Request, entry_id: str, body: BuildIn):
    principal = require_permission(request, BUILD)
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await RM.build_model(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                                   p=_params(body), description=body.description)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    if out.get("joined"):
        # the key already has a model: "run in the same fund" — nothing was created
        return JSONResponse(status_code=200, content=jsonable_encoder(out))
    return out


@router.get("/replication/models/{model_id}/spec")
async def model_spec(request: Request, model_id: str):
    principal = require_permission(request, VIEW_MODELS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            out = await RM.spec_detail(conn, org_id, model_id)
        except Exception as exc:  # noqa: BLE001
            _raise(exc)
        vocab = await _vocab(conn, _permissions(principal))
    return {**out, "permissions": _permissions(principal), "vocabularies": vocab}
