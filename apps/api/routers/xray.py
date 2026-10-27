"""REP7 — the portfolio X-ray (/xray/…). Looks through every fund a household
holds; marks the replicable ones; opens a join request in one click for a
MANAGED account. Nothing here trades.

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_replication + household access
        GET  /xray/households                    households the caller may see, with their accounts
        GET  /xray/households/{id}               the X-ray computed now (no write) + the recorded runs
        GET  /xray/runs/{id}                     one recorded run with its lines
  manage_replication + household access
        POST /xray/households/{id}/runs          record a run now (on demand)
  manage_proposals + household access
        POST /xray/households/{id}/join          rep5's join request for a managed account (the caller is the
                                                 maker; a second holder of approve_proposals approves it)
        POST /xray/households/{id}/one-off       rep7b: on an ADVISE account, a draft one-off proposal selling
                                                 the fund (the caller is the maker; approve_proposals decides)

Household access: Super Admin, staff and org admins (manage_org_settings) any household of the
org; anyone else
only a household with an account whose primary entity the member visibility
engine grants them (else 404 — ids outside the caller's scope are not
disclosed). Super Admin first (services.authz). org_id is only ever the
caller's (get_org_id); every request model forbids extras.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import account_models as AM
from services import human_tasks as HT
from services import rebalance_proposals as RP
from services import xray as XR
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["xray"])

VIEW = "view_replication"
RUN = "manage_replication"
JOIN = AM.MANAGE
ORG_ADMIN = "manage_org_settings"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JoinIn(_Body):
    account_id: UUID
    fund_security_id: UUID


class RunIn(_Body):
    pass


class OneOffIn(_Body):
    account_id: UUID
    fund_security_id: UUID
    executor_type: Literal["member", "other_adviser"] = "member"
    executor_name: str | None = Field(default=None, max_length=300)
    executor_contact: str | None = Field(default=None, max_length=500)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (XR.XrayNotFound, AM.AccountModelNotFound, RP.ProposalNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (AM.MakerCheckerViolation, HT.MakerCheckerError, PermissionError, HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (XR.XrayConflict, AM.AccountModelConflict, RP.ProposalConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (XR.XrayError, AM.AccountModelError, RP.ProposalError, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _permissions(principal) -> dict:
    has = (lambda p: principal.is_super_admin or p in principal.permissions)
    return {"can_read": has(VIEW), "can_write": has(RUN), "can_join": has(JOIN),
            "is_super_admin": principal.is_super_admin, "read_permission": VIEW, "write_permission": RUN,
            "join_permission": JOIN}


def _env(principal, **body) -> dict:
    perms = _permissions(principal)
    v = XR.vocabularies()
    v["editable"] = ["run"] if perms["can_write"] else []
    v["inline_editable"] = []
    return {**body, "permissions": perms, "vocabularies": v}


async def _visible_households(pool, principal, org_id) -> set[str] | None:
    """None = every household of the org (Super Admin, staff, or an org admin — manage_org_settings)."""
    if principal.is_super_admin or principal.is_staff or ORG_ADMIN in principal.permissions:
        return None
    from services.document_embedding import visible_entity_ids

    ents = [str(e) for e in await visible_entity_ids(pool, principal.org_id, principal.user_id, False)]
    if not ents:
        return set()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT household_id FROM public.accounts WHERE org_id = $1::uuid "
                                "AND household_id IS NOT NULL AND primary_entity_id = ANY($2::uuid[]) "
                                "AND system_to IS NULL AND valid_to IS NULL", str(org_id), ents)
    return {str(r["household_id"]) for r in rows}


async def _require_household(pool, principal, org_id, household_id) -> None:
    allowed = await _visible_households(pool, principal, org_id)
    if allowed is not None and str(household_id) not in allowed:
        raise HTTPException(status_code=404, detail="household not found")


def _join_actions(x: dict, perms: dict) -> None:
    """The server says which lines carry the button: a managed account, an
    approved model, a replicable verdict — and the caller may propose."""
    for f in x["funds"]:
        acts = []
        if perms["can_join"] and f["join"]["available"]:
            acts.append("open_join_request")
        elif perms["can_join"] and (f.get("one_off") or {}).get("available"):
            acts.append("propose_one_off")     # rep7b — an advise account: the one-off trade instead of the join
        f["actions"] = acts


@router.get("/xray/households")
async def households(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    allowed = await _visible_households(pool, principal, org_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT h.id, h.name, count(a.id) AS accounts, count(a.id) FILTER (WHERE a.is_held_away) AS held_away "
            "FROM public.households h JOIN public.accounts a ON a.household_id = h.id AND a.system_to IS NULL "
            "AND a.valid_to IS NULL WHERE h.org_id = $1::uuid GROUP BY h.id, h.name ORDER BY h.name", str(org_id))
    out = [{"id": str(r["id"]), "name": r["name"], "accounts": r["accounts"], "held_away": r["held_away"]}
           for r in rows if allowed is None or str(r["id"]) in allowed]
    return _env(principal, rows=out)


@router.get("/xray/households/{household_id}")
async def household_xray(request: Request, household_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    await _require_household(pool, principal, org_id, household_id)
    try:
        async with pool.acquire() as conn:
            x = await XR.compute(conn, org_id, household_id)
            runs = await XR.runs_for(conn, org_id, household_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    _join_actions(x, perms)
    return {**_env(principal, xray=x, runs=runs), "actions": ["run"] if perms["can_write"] else []}


@router.get("/xray/runs/{run_id}")
async def get_run(request: Request, run_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await conn.fetchrow(f"SELECT * FROM {XR.T_RUN} WHERE id = $1 AND org_id = $2::uuid AND system_to IS NULL",
                                  run_id, str(org_id))
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        lines = await conn.fetch(f"SELECT * FROM {XR.T_LINE} WHERE run_id = $1 AND system_to IS NULL "
                                 "ORDER BY line_kind, label", run_id)
    await _require_household(pool, principal, org_id, run["household_id"])
    return _env(principal, run={"id": str(run["id"]), "household_id": str(run["household_id"]),
                                "run_date": run["run_date"].isoformat(), "trigger": run["trigger"],
                                "summary": XR._loads(run["summary"]) or {},
                                "status_lines": XR._loads(run["status_lines"]) or []},
                lines=[{"id": str(r["id"]), "line_kind": r["line_kind"], "label": r["label"],
                        "account_id": str(r["account_id"]) if r["account_id"] else None,
                        "security_id": str(r["global_security_id"]) if r["global_security_id"] else None,
                        "fund_security_id": str(r["fund_security_id"]) if r["fund_security_id"] else None,
                        "market_value": XR._s(r["market_value"]), "as_of": XR._iso(r["as_of"]), "source": r["source"],
                        "weight": XR._s(r["weight"]), "weight_as_of": XR._iso(r["weight_as_of"]),
                        "exposure": XR._s(r["exposure"]), "sector": r["sector"], "replicable": r["replicable"],
                        "detail": XR._loads(r["detail"]) or {}, "status_lines": XR._loads(r["status_lines"]) or []}
                       for r in lines])


@router.post("/xray/households/{household_id}/runs", status_code=201)
async def record_run(request: Request, household_id: UUID, body: RunIn):
    principal = require_permission(request, RUN)
    org_id = get_org_id(request)
    pool = await get_pool()
    await _require_household(pool, principal, org_id, household_id)
    try:
        out = await XR.run(pool, org_id=org_id, household_id=household_id, trigger="on_demand",
                           actor_id=principal.user_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"run_id": out["run_id"], "household": out["household"]}


@router.post("/xray/households/{household_id}/join", status_code=201)
async def open_join(request: Request, household_id: UUID, body: JoinIn):
    principal = require_permission(request, JOIN)
    org_id = get_org_id(request)
    pool = await get_pool()
    await _require_household(pool, principal, org_id, household_id)
    try:
        row = await XR.open_join(pool, org_id=org_id, actor_id=principal.user_id, household_id=household_id,
                                 account_id=body.account_id, fund_security_id=body.fund_security_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"link_id": str(row["id"]), "status": row["status"], "account_id": str(row["account_id"]),
            "model_id": str(row["model_id"]), "task_id": (row.get("task") or {}).get("id")}


@router.post("/xray/households/{household_id}/one-off", status_code=201)
async def open_one_off(request: Request, household_id: UUID, body: OneOffIn):
    principal = require_permission(request, JOIN)
    org_id = get_org_id(request)
    pool = await get_pool()
    await _require_household(pool, principal, org_id, household_id)
    try:
        row = await XR.open_one_off(pool, org_id=org_id, actor_id=principal.user_id, household_id=household_id,
                                    account_id=body.account_id, fund_security_id=body.fund_security_id,
                                    executor_type=body.executor_type, executor_name=body.executor_name,
                                    executor_contact=body.executor_contact)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"proposal": RP.proposal_out(row)}
