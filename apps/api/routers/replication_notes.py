"""REP11 — Replication Studio: STRUCTURED NOTES (/replication/notes/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_replication    GET  /replication/notes                              every note with extracted terms (filter:
                                                                           verdict / archetype / issuer / underlying)
                      GET  /replication/notes/{note_id}                    one note: terms + filing link, verdict and
                                                                           reasons, the at-expiry payoff overlay, the
                                                                           org's replicas, differences
                      GET  /replication/notes/replicas/{replica_id}        one replica: legs (manual instructions),
                                                                           computed vs stated terms, payoff, pricing
  manage_replication  POST /replication/notes/verdicts/run                 recompute the platform verdicts (the job;
                                                                           idempotent, derived from public terms)
                      POST /replication/notes/{note_id}/replicas           build a DRAFT replica (refused, nothing
                                                                           written, for a not-replicable note, terms
                                                                           that need review, an unconfirmed underlying)
                      POST /replication/notes/replicas/{replica_id}/price  price now: a new comparison (estimate) or
                                                                           "not priced — enter a quote" naming the gap
                      POST /replication/notes/replicas/{replica_id}/submit open the approval task
                      POST /replication/notes/replicas/{replica_id}/archive
  approve_replication POST /replication/notes/replicas/{replica_id}/approve  (never a maker)
                      POST /replication/notes/replicas/{replica_id}/reject   (a note)

Replica cost and embedded-fee figures are hypothetical: staff (Super Admin, a staff role, an org admin) always see
them; anyone else only under an approved hypothetical-performance policy whose audience is staff_and_members
(rep10), with its disclosure. The verdict and the stated terms are factual and always shown. Super Admin first
(services.authz). org_id is only ever the caller's (get_org_id); request models forbid extras; amounts travel as
decimal strings. Another org's replica is a 404. Nothing here trades — every leg is a manual instruction.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import human_tasks as HT
from services import hypothetical_policy as HP
from services import note_replication as N
from services import options_sleeve as OS
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["replication-notes"])

VIEW, MANAGE, APPROVE = N.VIEW, N.MANAGE, N.APPROVE
ORG_ADMIN = "manage_org_settings"
BUILD_FIELDS = ["broad_based_index", "is_flex", "exercise_style", "settlement", "multiplier", "start_level_quote_id",
                "treasury_global_security_id"]
PRICE_FIELDS = ["start_level_quote_id", "treasury_global_security_id"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildIn(_Body):
    broad_based_index: bool | None = None
    is_flex: bool = True
    exercise_style: str = Field(default="european", max_length=20)
    settlement: str | None = Field(default=None, max_length=20)
    multiplier: str = Field(default="100", max_length=20)
    start_level_quote_id: str | None = Field(default=None, max_length=64)
    treasury_global_security_id: str | None = Field(default=None, max_length=64)


class PriceIn(_Body):
    start_level_quote_id: str | None = Field(default=None, max_length=64)
    treasury_global_security_id: str | None = Field(default=None, max_length=64)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class ArchiveIn(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class RunIn(_Body):
    pass


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (N.NoteNotFound, OS.OptionsNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (N.MakerCheckerViolation, OS.MakerCheckerViolation, HT.MakerCheckerError,
                        HT.HumanTaskPermissionError, HT.AgentActorRefused, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, N.NoteRefused):
        raise HTTPException(status_code=409, detail={"message": str(exc), "reasons": exc.reasons}) from exc
    if isinstance(exc, (N.NoteConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError)):
        raise HTTPException(status_code=422, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (N.NoteError, OS.OptionsError, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _permissions(principal) -> dict:
    su = principal.is_super_admin
    perms = principal.permissions
    return {"can_read": su or VIEW in perms, "can_write": su or MANAGE in perms, "can_approve": su or APPROVE in perms,
            "is_super_admin": su, "is_staff_view": _staff(principal), "read_permission": VIEW,
            "write_permission": MANAGE, "approve_permission": APPROVE}


def _staff(principal) -> bool:
    """Who sees hypothetical figures without a member policy: Super Admin, a staff role, an org admin."""
    return bool(principal.is_super_admin or principal.is_internal or ORG_ADMIN in principal.permissions)  # STAFFROLES1 — visibility (see), not a bypass


def _envelope(principal, body: dict) -> dict:
    perms = _permissions(principal)
    v = N.vocabularies()
    v["editable"] = list(BUILD_FIELDS) if perms["can_write"] else []
    v["price_editable"] = list(PRICE_FIELDS) if perms["can_write"] else []
    v["inline_editable"] = []
    return jsonable_encoder({**body, "permissions": perms, "vocabularies": v})


async def _gate(conn, org_id) -> dict:
    return await HP.gate_for(conn, org_id)


async def _replica_env(conn, principal, org_id, replica_id) -> dict:
    d = await N.replica_detail(conn, org_id, replica_id)
    perms = _permissions(principal)
    task = d.get("task")
    can_decide = bool(task and task.get("status") == "open" and HT.can_decide(
        task, user_id=principal.user_id, permissions=principal.permissions, is_super_admin=principal.is_super_admin))
    r = d["replica"]
    d["actions"] = N.actions_for(r, can_manage=perms["can_write"], can_approve=perms["can_approve"] and can_decide,
                                 user_id=principal.user_id, needs_=d["needs"])
    gate = await _gate(conn, org_id)
    d = N.strip_comparison(d, staff=_staff(principal), gate=gate)
    d["hypothetical_gate"] = gate
    return d


@router.get("/replication/notes")
async def list_notes(request: Request, verdict: str | None = Query(default=None, max_length=40),
                     archetype: str | None = Query(default=None, max_length=40),
                     issuer: str | None = Query(default=None, max_length=200),
                     underlying: str | None = Query(default=None, max_length=200)):
    principal = require_permission(request, VIEW)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await N.list_notes(conn, verdict=verdict, archetype=archetype, issuer=issuer, underlying=underlying)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    counts = {k: sum(1 for r in rows if (r["verdict"] or {}).get("verdict") == k) for k in N.VERDICT_LABELS}
    perms = _permissions(principal)
    return _envelope(principal, {"rows": rows, "counts": counts,
                                 "unassessed": sum(1 for r in rows if r["verdict"] is None),
                                 "actions": ["run_verdicts"] if perms["can_write"] else []})


@router.post("/replication/notes/verdicts/run")
async def run_verdicts(request: Request, body: RunIn | None = None):
    principal = require_permission(request, MANAGE)
    try:
        out = await N.run_verdicts(await get_pool())
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"result": out})


@router.get("/replication/notes/replicas/{replica_id}")
async def get_replica(request: Request, replica_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, replica_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.get("/replication/notes/{note_id}")
async def get_note(request: Request, note_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await N.note_detail(conn, org_id, note_id)
            gate = await _gate(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    d["actions"] = ["build"] if (perms["can_write"] and d["buildable"]["ok"]
                                 and not any(r["status"] in ("draft", "pending_approval") for r in d["replicas"])) else []
    d["hypothetical_gate"] = gate
    return _envelope(principal, d)


@router.post("/replication/notes/{note_id}/replicas", status_code=201)
async def build_replica(request: Request, note_id: UUID, body: BuildIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        d = await N.build(pool, org_id=org_id, actor_id=principal.user_id, note_id=note_id, **body.model_dump())
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, d["replica"]["id"])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.post("/replication/notes/replicas/{replica_id}/price", status_code=201)
async def price_replica(request: Request, replica_id: UUID, body: PriceIn | None = None):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    b = body or PriceIn()
    try:
        cmp_ = await N.price(pool, org_id=org_id, actor_id=principal.user_id, replica_id=replica_id,
                             start_level_quote_id=b.start_level_quote_id,
                             treasury_global_security_id=b.treasury_global_security_id)
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, replica_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    d["priced"] = {"id": cmp_["id"], "status": cmp_["status"]}
    return _envelope(principal, d)


@router.post("/replication/notes/replicas/{replica_id}/submit")
async def submit_replica(request: Request, replica_id: UUID):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        await N.submit(pool, org_id=org_id, actor_id=principal.user_id, replica_id=replica_id)
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, replica_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.post("/replication/notes/replicas/{replica_id}/archive")
async def archive_replica(request: Request, replica_id: UUID, body: ArchiveIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        await N.archive(pool, org_id=org_id, actor_id=principal.user_id, replica_id=replica_id, reason=body.reason)
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, replica_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.post("/replication/notes/replicas/{replica_id}/approve")
async def approve_replica(request: Request, replica_id: UUID, body: Decision | None = None):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        await N.decide(pool, org_id=org_id, actor_id=principal.user_id, replica_id=replica_id, outcome="approve",
                       note=(body.note if body else None))
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, replica_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.post("/replication/notes/replicas/{replica_id}/reject")
async def reject_replica(request: Request, replica_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        await N.decide(pool, org_id=org_id, actor_id=principal.user_id, replica_id=replica_id, outcome="reject",
                       note=body.note)
        async with pool.acquire() as conn:
            d = await _replica_env(conn, principal, org_id, replica_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)
