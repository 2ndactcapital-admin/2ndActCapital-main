"""REP12 — Replication Studio: the note-versus-replica check across NEW issues (/replication/notes/checks/…) and the
faster underlying confirmation queue (/replication/notes/underlyings/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  internal + view_replication   GET  /replication/notes/checks                  "New issues": the org's live checks,
                                                                               newest first or by the embedded-fee
                                                                               estimate (unpriced last); filters
                                     GET  /replication/notes/checks/settings      the org's alert threshold + watches
                                     GET  /replication/notes/checks/{check_id}    one check + the note's check history
  manage_replication            POST /replication/notes/checks/run              check one note (or the org) now
                                POST /replication/notes/checks/settings         change the threshold / watches
                                POST /replication/notes/checks/{check_id}/review  reviewed | dismissed (a note)
  super admin                   GET  /replication/notes/underlyings             the underlying queue, grouped by
                                                                               proposal, counts per state
                                POST /replication/notes/underlyings/confirm     one-click / multi-confirm: ONE
                                                                               confirm_resolution per edge, as the
                                                                               caller; the notes are re-checked
                                POST /replication/notes/underlyings/{relationship_id}/reject

Staff-data reads go through ``require_internal_permission`` (STAFFROLES1): a client role is refused even when it
holds view_replication. Writes keep their permission. org_id is only ever the caller's (get_org_id); request models
forbid extras; amounts travel as decimal strings. MUST be included BEFORE routers.replication_notes: its
``/replication/notes/{note_id}`` would otherwise swallow ``/replication/notes/checks`` (a 422, not a fall-through).

NO AUTOMATIC CONFIRMATION, NO AUTOMATIC APPROVAL: the check prepares DRAFT replicas; an underlying is confirmed only
by the Super Admin who clicks, through services.underlying_resolution.confirm_resolution.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import human_tasks as HT
from services import note_checks as NC
from services import note_replication as N
from services.authz import require_internal_permission, require_permission, require_super_admin
from services.database import get_pool
from services.underlying_resolution import UnderlyingResolutionError, UnderlyingResolutionPermissionError

router = APIRouter(tags=["replication-note-checks"])

VIEW, MANAGE = NC.VIEW, NC.MANAGE
SETTINGS_FIELDS = ["enabled", "fee_alert_threshold_pct", "watch_archetypes", "watch_issuers", "watch_underlyings"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunIn(_Body):
    note_id: UUID | None = None


class SettingsIn(_Body):
    enabled: bool = True
    fee_alert_threshold_pct: str | None = Field(default=None, max_length=20)
    watch_archetypes: list[str] = Field(default_factory=list, max_length=10)
    watch_issuers: list[str] = Field(default_factory=list, max_length=50)
    watch_underlyings: list[str] = Field(default_factory=list, max_length=50)


class ReviewIn(_Body):
    outcome: str = Field(max_length=20)
    note: str | None = Field(default=None, max_length=2000)


class ConfirmIn(_Body):
    relationship_ids: list[UUID] = Field(min_length=1, max_length=200)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (NC.CheckNotFound, N.NoteNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (HT.HumanTaskPermissionError, HT.AgentActorRefused, UnderlyingResolutionPermissionError,
                        PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (NC.CheckConflict, HT.HumanTaskConflict, N.NoteConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError)):
        raise HTTPException(status_code=422, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (NC.CheckError, N.NoteError, HT.HumanTaskError, UnderlyingResolutionError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _permissions(principal) -> dict:
    su = principal.is_super_admin
    perms = principal.permissions
    return {"can_read": True, "can_write": su or MANAGE in perms, "can_review": su or MANAGE in perms,
            "can_confirm_underlyings": su, "is_super_admin": su, "read_permission": VIEW, "write_permission": MANAGE,
            "underlying_permission": "super_admin"}


def _envelope(principal, body: dict) -> dict:
    perms = _permissions(principal)
    v = NC.vocabularies()
    v["editable"] = list(SETTINGS_FIELDS) if perms["can_write"] else []
    v["inline_editable"] = []
    return jsonable_encoder({**body, "permissions": perms, "vocabularies": v})


def _row_actions(rec: dict, perms: dict) -> list[str]:
    return ["review", "dismiss"] if (perms["can_review"] and rec["status"] == "new" and rec["is_live"]) else []


@router.get("/replication/notes/checks")
async def list_checks(request: Request, verdict: str | None = Query(default=None, max_length=40),
                      archetype: str | None = Query(default=None, max_length=40),
                      issuer: str | None = Query(default=None, max_length=200),
                      underlying: str | None = Query(default=None, max_length=200),
                      date_from: date | None = None, date_to: date | None = None,
                      status: str | None = Query(default=None, max_length=20),
                      sort: str = Query(default="newest", max_length=20)):
    principal = require_internal_permission(request, "view_replication")
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await NC.feed(conn, org_id, verdict=verdict, archetype=archetype, issuer=issuer,
                                 underlying=underlying, date_from=date_from, date_to=date_to, status=status, sort=sort)
            settings = await NC.settings_of(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    for r in rows:
        r["actions"] = _row_actions(r, perms)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in NC.STATUS_LABELS}
    return _envelope(principal, {"rows": rows, "counts": counts, "sort": sort,
                                 "settings": NC.settings_out(settings),
                                 "actions": ["run"] if perms["can_write"] else []})


@router.get("/replication/notes/checks/settings")
async def get_settings(request: Request):
    principal = require_internal_permission(request, "view_replication")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        s = await NC.settings_of(conn, org_id)
    return _envelope(principal, {"settings": NC.settings_out(s)})


@router.post("/replication/notes/checks/settings")
async def put_settings(request: Request, body: SettingsIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        s = await NC.put_settings(pool, org_id=org_id, actor_id=principal.user_id, **body.model_dump())
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"settings": NC.settings_out(s)})


@router.post("/replication/notes/checks/run")
async def run_checks(request: Request, body: RunIn | None = None):
    """Check now, in the caller's own org only: one note (body.note_id) or every note (the sweep, scoped)."""
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    b = body or RunIn()
    try:
        if b.note_id:
            res = await NC.recheck_notes(pool, [b.note_id], trigger="manual", orgs=[org_id], include_test_orgs=True)
            out = {"result": res}
        else:
            out = {"result": await NC.sweep(pool, include_test_orgs=True, orgs=[org_id])}
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, out)


@router.get("/replication/notes/checks/{check_id}")
async def get_check(request: Request, check_id: UUID):
    principal = require_internal_permission(request, "view_replication")
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT * FROM {NC.T_CHECK} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                      "system_to IS NULL", str(check_id), str(org_id))
            if row is None:
                raise NC.CheckNotFound("check not found")
            rec = NC.check_out(dict(row))
            history = await NC.check_history(conn, org_id, row["global_security_id"])
            task = await HT.get_task(conn, org_id, row["alert_task_id"]) if row["alert_task_id"] else None
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    rec["actions"] = _row_actions(rec, _permissions(principal))
    return _envelope(principal, {"check": rec, "history": history, "alert_task": task})


@router.post("/replication/notes/checks/{check_id}/review")
async def review_check(request: Request, check_id: UUID, body: ReviewIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        rec = await NC.review(pool, org_id=org_id, actor_id=principal.user_id, check_id=check_id,
                              outcome=body.outcome, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    rec["actions"] = _row_actions(rec, _permissions(principal))
    return _envelope(principal, {"check": rec})


@router.get("/replication/notes/underlyings")
async def underlying_queue(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = await NC.underlying_queue(conn)
    return _envelope(principal, q)


@router.post("/replication/notes/underlyings/confirm")
async def confirm_underlyings(request: Request, body: ConfirmIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        out = await NC.confirm_edges(pool, actor_id=principal.user_id, is_super_admin=principal.is_super_admin,
                                     relationship_ids=[str(x) for x in body.relationship_ids])
        async with pool.acquire() as conn:
            q = await NC.underlying_queue(conn)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {**out, "queue": q})


@router.post("/replication/notes/underlyings/{relationship_id}/reject")
async def reject_underlying(request: Request, relationship_id: UUID):
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        out = await NC.reject_edge(pool, actor_id=principal.user_id, is_super_admin=principal.is_super_admin,
                                   relationship_id=str(relationship_id))
        async with pool.acquire() as conn:
            q = await NC.underlying_queue(conn)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {**out, "queue": q})
