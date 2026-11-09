"""CASH01 — Household → Treasury: the obligations ledger and cash coverage. Read-only on money.

    GET  /api/v1/cash/households                                   the households the caller may open (Treasury)
    GET  /api/v1/cash/households/{household_id}/treasury           coverage + obligations envelope
    POST /api/v1/cash/households/{household_id}/obligations        add a manual obligation        (manage_portfolio)
    POST /api/v1/cash/obligations/{obligation_id}/revise           a new version (bi-temporal)    (manage_portfolio)
    POST /api/v1/cash/obligations/{obligation_id}/end              end it (kept as history)       (manage_portfolio)
    POST /api/v1/cash/households/{household_id}/notices            record a capital-call notice   (manage_portfolio)
    PUT  /api/v1/cash/households/{household_id}/minimums           minimum months (entity / hh)   (manage_portfolio)
    POST /api/v1/cash/households/{household_id}/alerts/sync        open / close the coverage to-dos (manage_portfolio)

GATES (services.authz, one Principal; Super Admin first). READ: ``view_portfolio`` (a member holds it) or
``manage_portfolio``, then the ONE visibility rule ``authz.member_entity_scope`` — a member reads only their own
family (another family's household is a 404, the same as a missing one), a restricted staff caller of an enforcing
org only their families (VISIBILITY1 also guards the ``household_id`` path parameter app-wide). A member's envelope is
read-only (no write flag, ``editable`` EMPTY) and carries the estimate labels from the disclosure library. WRITE:
``require_internal_permission(manage_portfolio)`` — the portfolio author permission reused; a client role is refused
even if it ever held it; maker-checker is not required for an obligation record (it moves nothing) — every write is
audited. ``org_id`` comes from the verified session only (Rule 6); every body model forbids extra fields.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from services import authz
from services import cash_coverage as CC
from services import cash_obligations as OB
from services.audit import write_audit_log
from services.authz import require_internal_permission
from services.database import get_pool
from services.family_visibility import require_visible, visible_household_ids

router = APIRouter(tags=["cash"])

READ_PERMISSIONS = (CC.READ_PERMISSION, CC.WRITE_PERMISSION)

#: "today" — injectable (verify_cash01 fixes it); None = the real date
_CLOCK: dict = {"fn": None}


def set_clock(fn) -> None:
    _CLOCK["fn"] = fn


def today() -> date:
    return _CLOCK["fn"]() if _CLOCK["fn"] else date.today()


class ObligationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: UUID
    source_type: str
    name: str
    amount: str
    currency_code: str | None = None
    due_date: date
    rrule: str | None = None
    end_date: date | None = None
    certainty: str
    source_ref_type: str | None = None
    source_ref_id: UUID | None = None
    owner_user_id: UUID | None = None
    notes: str | None = None


class ReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any]
    reason: str | None = None


class EndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


class NoticeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: UUID
    commitment_id: UUID | None = None
    name: str
    amount: str
    due_date: date
    notes: str | None = None


class MinimumIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: str
    entity_id: UUID | None = None
    months: str | None = None
    reason: str | None = None


def _can_write(p) -> bool:
    """The exact rule of require_internal_permission(manage_portfolio), for the envelope (fails closed)."""
    if p is None or not p.admitted or not p.is_active:
        return False
    if p.is_super_admin or p.is_staff:
        return True
    return bool(p.is_internal and CC.WRITE_PERMISSION in p.permissions)


def _err(exc: Exception):
    if isinstance(exc, OB.ObligationNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


async def _household(conn, org_id, household_id) -> str:
    name = await conn.fetchval("SELECT name FROM households WHERE id = $1::uuid AND org_id = $2::uuid",
                               str(household_id), str(org_id))
    if name is None:
        raise HTTPException(status_code=404, detail="household not found")
    return name


async def _visible_members(conn, org_id, household_id, scope: set | None) -> set[str]:
    from services import household_total as HT

    members = set(await HT.household_members(conn, org_id, household_id))
    if scope is not None:
        members &= {str(x) for x in scope}
        if not members:
            raise HTTPException(status_code=404, detail="household not found")
    return members


async def _reach(conn, org_id, household_id) -> set[str]:
    """Every entity the household reaches (its members and what they own) — the payers an obligation may name."""
    from services import household_total as HT
    from services import networth as NW

    members = set(await HT.household_members(conn, org_id, household_id))
    return set(NW.shares(members, await NW.load_edges(conn, org_id)))


async def _recipients(pool, org_id) -> list[str]:
    from services.rbac import get_users_with_permission

    return list(await get_users_with_permission(pool, org_id, CC.WRITE_PERMISSION))


async def _sync(pool, conn, org_id, household_id) -> dict:
    report = await CC.household_report(conn, org_id, household_id, today=today())
    return await CC.sync_alerts(conn, org_id=org_id, household_id=household_id, report=report,
                                recipients=await _recipients(pool, org_id))


@router.get("/cash/households")
async def cash_households_route(request: Request):
    """The households the caller may open on Treasury (a member: their own family's; staff: every visible one)."""
    authz.require_any_permission(request, READ_PERMISSIONS)
    org_id = get_org_id(request)
    pool = await get_pool()
    scope = await authz.member_entity_scope(request, pool)
    async with pool.acquire() as conn:
        if scope is None:
            rows = await conn.fetch("SELECT id::text AS id, name FROM households WHERE org_id = $1::uuid ORDER BY name",
                                    str(org_id))
        else:
            rows = await conn.fetch(
                "SELECT DISTINCT h.id::text AS id, h.name FROM households h JOIN household_memberships m ON "
                "m.household_id = h.id WHERE h.org_id = $1::uuid AND m.entity_id = ANY($2::uuid[]) ORDER BY h.name",
                str(org_id), authz.scope_ids(scope))
    vis = await visible_household_ids(request, pool)
    out = [dict(r) for r in rows if vis is None or r["id"] in vis]
    return {"households": out, "permissions": {"can_read": True, "can_write": _can_write(authz.principal_of(request)),
                                               "read_permission": CC.READ_PERMISSION,
                                               "write_permission": CC.WRITE_PERMISSION}}


@router.get("/cash/households/{household_id}/treasury")
async def treasury_route(request: Request, household_id: UUID,
                         horizon_months: int = Query(default=OB.DEFAULT_HORIZON_MONTHS, ge=1, le=OB.MAX_HORIZON_MONTHS)):
    p = authz.require_any_permission(request, READ_PERMISSIONS)
    from services import disclosures as D

    org_id = get_org_id(request)
    pool = await get_pool()
    scope = await authz.member_entity_scope(request, pool)
    member = authz.is_member_principal(p)
    can_write = _can_write(p) and not member
    t = today()
    async with pool.acquire() as conn:
        name = await _household(conn, org_id, household_id)
        await _visible_members(conn, org_id, household_id, scope)
        report = await CC.household_report(conn, org_id, household_id, today=t, member_scope=scope,
                                           horizon_months=horizon_months)
        labels = {k: D.public(await D.resolve_safe(conn, k, org_id=org_id, household_id=household_id))
                  for k in (CC.ESTIMATE_KEY, "tax.estimate_label")}
        stored = []
        if can_write:
            stored = [OB.row_json(r) for r in await OB.stored_rows(conn, org_id, [e["entity_id"]
                                                                                   for e in report.entities])]
    names = report.names
    lines = [ln.to_json(names) for ln in report.lines]
    actions = ["add_obligation", "record_notice", "set_minimum", "sync_alerts"] if can_write else []
    return {
        "household": {"id": str(household_id), "name": name},
        "today": t.isoformat(), "horizon_months": horizon_months, "until": report.until.isoformat(),
        "coverage": report.household, "entities": report.entities, "obligations": lines,
        "calendar": CC.calendar_months(report.lines, names, t, min(horizon_months, 24)),
        "not_estimated": [{**x, "payer_name": names.get(x["payer_entity_id"]),
                           "text": f"not estimated — needs {x['needs']}"} for x in report.not_estimated],
        "lines_of_credit": [{**x, "payer_name": names.get(x["payer_entity_id"])} for x in report.lines_of_credit],
        "unmatched_notices": report.unmatched_notices if not member else [],
        "stored": stored,
        "labels": labels,
        "member_view": member,
        "actions": actions,
        "permissions": {"can_read": True, "can_write": can_write, "is_super_admin": bool(p.is_super_admin),
                        "read_permission": CC.READ_PERMISSION, "write_permission": CC.WRITE_PERMISSION},
        "vocabularies": {**OB.vocabularies(can_write=can_write), **CC.vocabularies(),
                         "payers": [{"entity_id": e["entity_id"], "display_name": e["display_name"]}
                                    for e in report.entities] if can_write else []},
    }


@router.post("/cash/households/{household_id}/obligations", status_code=201)
async def create_obligation_route(request: Request, household_id: UUID, body: ObligationIn):
    p = require_internal_permission(request, "manage_portfolio")  # = CC.WRITE_PERMISSION
    org_id = get_org_id(request)
    pool = await get_pool()
    await require_visible(request, pool, entity_ids=[body.entity_id], household_ids=[household_id])
    data = body.model_dump(exclude_none=True)
    if data.get("source_type") == "capital_call_noticed":
        raise HTTPException(status_code=422, detail="record a capital-call notice on its own form (it is matched to "
                                                    "a commitment)")
    async with pool.acquire() as conn:
        await _household(conn, org_id, household_id)
        if str(body.entity_id) not in await _reach(conn, org_id, household_id):
            raise HTTPException(status_code=422, detail="the payer is not an entity of this household")
        try:
            async with conn.transaction():
                row = await OB.create(conn, org_id=org_id, actor_id=p.user_id, data=data, today=today(),
                                      allowed_types=OB.STORED_SOURCE_TYPES - {"capital_call_noticed"})
        except (OB.ObligationError, OB.ObligationNotFound) as exc:
            raise _err(exc) from exc
        await write_audit_log(org_id=org_id, action="cash.obligation.created", table_name="cash_obligations",
                              record_id=row["lineage_id"], new=row, actor=p.user_id)
        alerts = await _sync(pool, conn, org_id, household_id)
    return {"obligation": row, "alerts": alerts}


async def _obligation_household(conn, org_id, entity_id) -> list[str]:
    return [str(r["household_id"]) for r in await conn.fetch(
        "SELECT m.household_id FROM household_memberships m JOIN households h ON h.id = m.household_id "
        "WHERE m.entity_id = $1 AND h.org_id = $2::uuid", entity_id, str(org_id))]


@router.post("/cash/obligations/{obligation_id}/revise")
async def revise_obligation_route(request: Request, obligation_id: UUID, body: ReviseIn):
    p = require_internal_permission(request, "manage_portfolio")  # = CC.WRITE_PERMISSION
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cur = await OB.live(conn, org_id, obligation_id)
        if cur is None:
            raise HTTPException(status_code=404, detail="Obligation not found, or already ended")
        await require_visible(request, pool, entity_ids=[cur["entity_id"]])
        try:
            row = await OB.revise(conn, org_id=org_id, actor_id=p.user_id, obligation_id=obligation_id,
                                  changes=body.changes, today=today(), reason=body.reason)
        except (OB.ObligationError, OB.ObligationNotFound) as exc:
            raise _err(exc) from exc
        await write_audit_log(org_id=org_id, action="cash.obligation.revised", table_name="cash_obligations",
                              record_id=row["lineage_id"], old=OB.row_json(cur), new=row, actor=p.user_id)
        alerts = [await _sync(pool, conn, org_id, h) for h in await _obligation_household(conn, org_id,
                                                                                          cur["entity_id"])]
    return {"obligation": row, "alerts": alerts}


@router.post("/cash/obligations/{obligation_id}/end")
async def end_obligation_route(request: Request, obligation_id: UUID, body: EndIn):
    p = require_internal_permission(request, "manage_portfolio")  # = CC.WRITE_PERMISSION
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cur = await OB.live(conn, org_id, obligation_id)
        if cur is None:
            raise HTTPException(status_code=404, detail="Obligation not found, or already ended")
        await require_visible(request, pool, entity_ids=[cur["entity_id"]])
        try:
            row = await OB.end(conn, org_id=org_id, obligation_id=obligation_id, reason=body.reason)
        except OB.ObligationNotFound as exc:
            raise _err(exc) from exc
        await write_audit_log(org_id=org_id, action="cash.obligation.ended", table_name="cash_obligations",
                              record_id=row["lineage_id"], old=OB.row_json(cur), new=row, actor=p.user_id)
        alerts = [await _sync(pool, conn, org_id, h) for h in await _obligation_household(conn, org_id,
                                                                                          cur["entity_id"])]
    return {"obligation": row, "alerts": alerts}


@router.post("/cash/households/{household_id}/notices", status_code=201)
async def record_notice_route(request: Request, household_id: UUID, body: NoticeIn):
    """A capital-call notice: matched to the named commitment, or — none named — to the payer's ONE live commitment
    with enough unfunded; otherwise recorded UNMATCHED (it counts, and one to-do asks for the match)."""
    p = require_internal_permission(request, "manage_portfolio")  # = CC.WRITE_PERMISSION
    org_id = get_org_id(request)
    pool = await get_pool()
    await require_visible(request, pool, entity_ids=[body.entity_id], household_ids=[household_id])
    async with pool.acquire() as conn:
        await _household(conn, org_id, household_id)
        if str(body.entity_id) not in await _reach(conn, org_id, household_id):
            raise HTTPException(status_code=422, detail="the payer is not an entity of this household")
        try:
            amount = OB.parse_money(body.amount)
        except OB.ObligationError as exc:
            raise _err(exc) from exc
        cands = [r["id"] for r in await conn.fetch(
            "SELECT c.id FROM portfolio.commitments c JOIN portfolio.positions p ON p.id = c.position_id AND "
            "p.org_id = c.org_id WHERE c.org_id = $1::uuid AND p.owner_entity_id = $2 AND c.valid_to IS NULL AND "
            "c.system_to IS NULL AND p.valid_to IS NULL AND p.system_to IS NULL AND (c.id = $3::uuid OR ($3::uuid IS "
            "NULL AND COALESCE(c.unfunded, c.commitment_amount - c.called_to_date) >= $4::numeric))",
            str(org_id), body.entity_id, str(body.commitment_id) if body.commitment_id else None, amount)]
        if body.commitment_id is not None and not cands:
            raise HTTPException(status_code=422, detail="that commitment is not a live commitment of the payer")
        matched = cands[0] if len(cands) == 1 else None
        data = {"entity_id": str(body.entity_id), "source_type": "capital_call_noticed", "name": body.name,
                "amount": body.amount, "due_date": body.due_date, "certainty": "firm", "notes": body.notes}
        if matched is not None:
            data |= {"source_ref_type": "commitment", "source_ref_id": str(matched)}
        try:
            async with conn.transaction():
                row = await OB.create(conn, org_id=org_id, actor_id=p.user_id, data=data, today=today(),
                                      reason="capital-call notice recorded on Treasury")
        except (OB.ObligationError, OB.ObligationNotFound) as exc:
            raise _err(exc) from exc
        await write_audit_log(org_id=org_id, action="cash.notice.recorded", table_name="cash_obligations",
                              record_id=row["lineage_id"], new={**row, "matched": matched is not None},
                              actor=p.user_id)
        alerts = await _sync(pool, conn, org_id, household_id)
    return {"obligation": row, "matched_commitment_id": str(matched) if matched else None,
            "candidates": len(cands), "alerts": alerts}


@router.put("/cash/households/{household_id}/minimums")
async def set_minimum_route(request: Request, household_id: UUID, body: MinimumIn):
    p = require_internal_permission(request, "manage_portfolio")  # = CC.WRITE_PERMISSION
    org_id = get_org_id(request)
    pool = await get_pool()
    await require_visible(request, pool, entity_ids=[body.entity_id] if body.entity_id else [],
                          household_ids=[household_id])
    async with pool.acquire() as conn:
        await _household(conn, org_id, household_id)
        if body.scope_type == "entity":
            if body.entity_id is None or str(body.entity_id) not in await _reach(conn, org_id, household_id):
                raise HTTPException(status_code=422, detail="name an entity of this household")
            sid = body.entity_id
        elif body.scope_type == "household":
            if body.entity_id is not None:
                raise HTTPException(status_code=422, detail="a household minimum names no entity")
            sid = household_id
        else:
            raise HTTPException(status_code=422, detail="the minimum is set for an entity or a household")
        try:
            out = await CC.set_minimum(conn, org_id=org_id, actor_id=p.user_id, scope_type=body.scope_type,
                                       scope_id=sid, months=body.months, reason=body.reason)
        except OB.ObligationError as exc:
            raise _err(exc) from exc
        await write_audit_log(org_id=org_id, action="cash.minimum.set", table_name="cash_coverage_minimums",
                              record_id=str(sid), new=out, actor=p.user_id)
        alerts = await _sync(pool, conn, org_id, household_id)
    return {"minimum": out, "alerts": alerts}


@router.post("/cash/households/{household_id}/alerts/sync")
async def sync_alerts_route(request: Request, household_id: UUID):
    require_internal_permission(request, "manage_portfolio")  # = CC.WRITE_PERMISSION
    org_id = get_org_id(request)
    pool = await get_pool()
    await require_visible(request, pool, household_ids=[household_id])
    async with pool.acquire() as conn:
        await _household(conn, org_id, household_id)
        return {"alerts": await _sync(pool, conn, org_id, household_id)}
