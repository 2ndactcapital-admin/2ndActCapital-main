"""CPSIM01 Task 5 — Admin → Counterparties (Super Admin only): the registry of SIMULATED counterparties.

Every route: ``require_super_admin`` FIRST (services.authz — the one gate), then one transaction on the app pool in which
the cpsim work runs AS role ``cpsim_app`` (cpsim.db.as_simulator) and the audit row is written as the login role after
``SET LOCAL ROLE NONE`` — the change and its ``counterparty.*`` audit row commit together or not at all.

    GET  /admin/counterparties                              list + vocabularies (institution types, statuses, …)
    POST /admin/counterparties                              create (draft) — the key (and a FIX password) shown ONCE
    GET  /admin/counterparties/org-access                   every org with counterparty_sim.allowed
    PUT  /admin/counterparties/org-access/{target_org_id}   switch it (audited counterparty.org_setting_changed)
    GET  /admin/counterparties/{id}                         detail (connection, FIX, bank, behaviour, clock, history)
    PUT  /admin/counterparties/{id}                         edit name / notes (system-axis history)
    POST /admin/counterparties/{id}/activate | /disable
    POST /admin/counterparties/{id}/key/generate | /key/rotate | /key/revoke      (a new key shown ONCE)
    POST /admin/counterparties/{id}/bank/regenerate
    PUT  /admin/counterparties/{id}/fix                     (rotate_password → a new password shown ONCE)
    PUT  /admin/counterparties/{id}/behaviour
    POST /admin/counterparties/{id}/clock/advance           one US business day (services/us_business_days)
    GET  /admin/counterparties/{id}/requests?limit=N        the request log (no bodies, no keys)
    POST /admin/counterparties/{id}/test-message            posts a sample envelope to the mirror IN-PROCESS with the
                                                             key the admin pastes (never stored) — no network

``target_org_id`` names the org a Super Admin is switching, a platform action (like /orgs/{org_id}/settings) — never the
caller's own tenant scope, which still comes only from the verified session. Every response says ``simulated: true``.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from cpsim import db as SDB
from cpsim import mirror as SM
from cpsim import registry as R
from services.authz import require_super_admin
from services.database import get_pool

router = APIRouter(tags=["admin-counterparties"])
_P = "/admin/counterparties"
SETTING = R.SETTING_ALLOWED
STATUS_LABELS = {"draft": "Draft", "active": "Active", "disabled": "Disabled"}
OUTCOME_LABELS = {"accept": "Accept", "reject": "Reject with reason", "partial": "Partial", "delay": "Delay"}
GROUP_LABELS = {"connection": "Connection", "fix": "FIX session", "bank_domestic": "Bank details (domestic)",
                "bank_international": "Bank details (international)", "behaviour": "Behaviour", "clock": "Clock"}
TABS = [("connection", "Connection"), ("fix", "FIX"), ("bank", "Bank details"), ("behaviour", "Behaviour"),
        ("clock", "Clock"), ("activity", "Activity"), ("import_export", "Import / Export")]
EDITABLE = ["name", "notes", "status", "key", "bank_details", "fix_session", "behaviour", "clock", "org_access"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIn(_Body):
    name: str = Field(min_length=1, max_length=200)
    institution_type: str = Field(min_length=2, max_length=40)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")


class EditIn(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class FixIn(_Body):
    begin_string: Literal["FIX.4.2", "FIX.4.4", "FIXT.1.1"] | None = None
    sender_comp_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    target_comp_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    host: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.-]{1,253}$")
    port: int | None = Field(default=None, ge=1, le=65535)
    heartbt_int: int | None = Field(default=None, ge=1, le=3600)
    reset_on_logon: bool | None = None
    username: str | None = Field(default=None, max_length=64)
    rotate_password: bool = False


class BehaviourIn(_Body):
    default_outcome: Literal["accept", "reject", "partial", "delay"] | None = None
    reject_reason: str | None = Field(default=None, max_length=500)
    partial_pct: float | None = Field(default=None, gt=0, lt=100)
    delay_seconds: int | None = Field(default=None, ge=0, le=86400)
    ach_return_code: str | None = Field(default=None, pattern=r"^R[0-9]{2}$")
    ach_return_after_days: int | None = Field(default=None, ge=0, le=60)
    webhooks_enabled: bool | None = None


class OrgAccessIn(_Body):
    allowed: bool


class TestMessageIn(_Body):
    key: str = Field(min_length=1, max_length=200)
    message_type: str = Field(default="test.ping", pattern=r"^[a-z][a-z0-9.]{1,79}$")


def _envelope(principal, body: dict, types: list[dict] | None = None) -> dict:
    su = bool(principal.is_super_admin)
    perms = {"can_read": su, "can_write": su, "is_super_admin": su,
             "read_permission": "super_admin", "write_permission": "super_admin"}
    vocab = {
        "institution_types": [{"key": t["key"], "label": t["label"], "field_groups": list(t["field_groups"]),
                               "description": t["description"]} for t in (types or [])],
        "field_groups": [{"key": k, "label": v} for k, v in GROUP_LABELS.items()],
        "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
        "outcomes": [{"key": k, "label": v} for k, v in OUTCOME_LABELS.items()] if su else [],
        "begin_strings": list(R.BEGIN_STRINGS) if su else [],
        "tabs": [{"key": k, "label": v} for k, v in TABS],
        "editable": list(EDITABLE) if su else [], "inline_editable": [],
    }
    return jsonable_encoder({**body, "simulated": True, "permissions": perms, "vocabularies": vocab})


async def _audit(conn, principal, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES ($1::uuid, "
        "$2::uuid, $3, $4, $5::uuid, $6::jsonb)", str(principal.org_id), str(principal.user_id), action, resource_type,
        str(resource_id), json.dumps(jsonable_encoder(payload)))


async def _sim(request: Request, fn, *, action: str | None = None, resource=None, audit_payload=None):
    """Super Admin → one transaction: ``fn(conn)`` as cpsim_app, then the audit row as the login role."""
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await SDB.as_simulator(conn)
            out = await fn(conn)
            types = await R.institution_types(conn)
            await SDB.leave_simulator(conn)
            if action:
                rid = resource(out) if callable(resource) else resource
                payload = audit_payload(out) if callable(audit_payload) else (audit_payload or {})
                await _audit(conn, principal, action, "counterparty", rid, {**payload, "simulated": True})
    except R.RegistryError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return principal, out, types


# ── list / create ───────────────────────────────────────────────────────────────────────────────────────────────
@router.get(_P)
async def list_counterparties(request: Request):
    principal, rows, types = await _sim(request, R.list_counterparties)
    return _envelope(principal, {"rows": rows}, types)


@router.post(_P)
async def create_counterparty(request: Request, body: CreateIn):
    async def fn(conn):
        return await R.create(conn, name=body.name, institution_type=body.institution_type, slug=body.slug,
                              actor=request.state.principal.user_id)

    principal, out, types = await _sim(
        request, fn, action="counterparty.created", resource=lambda o: o["id"],
        audit_payload=lambda o: {"slug": o["slug"], "name": body.name, "institution_type": body.institution_type,
                                 "issued": sorted(o["issued"])})
    # the generated secrets: in THIS response only — never stored, never logged, never retrievable again
    return _envelope(principal, {"id": out["id"], "slug": out["slug"], "issued": out["issued"],
                                 "shown_once": True}, types)


# ── org access (the setting) ────────────────────────────────────────────────────────────────────────────────────
@router.get(f"{_P}/org-access")
async def org_access(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT o.id, o.name, o.slug, o.is_test, COALESCE(s.setting_value = 'true'::jsonb, false) AS allowed, "
            "s.updated_at, s.updated_by FROM organizations o LEFT JOIN org_settings s ON s.org_id = o.id AND "
            "s.setting_key = $1 ORDER BY o.is_test, o.name", SETTING)]
    return _envelope(principal, {"rows": rows, "setting_key": SETTING, "default": False})


@router.put(f"{_P}/org-access/{{target_org_id}}")
async def set_org_access(request: Request, target_org_id: UUID, body: OrgAccessIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM organizations WHERE id = $1::uuid)", str(target_org_id)):
            raise HTTPException(status_code=404, detail="no such organization")
        before = await conn.fetchval("SELECT setting_value = 'true'::jsonb FROM org_settings WHERE org_id = $1::uuid "
                                     "AND setting_key = $2", str(target_org_id), SETTING)
        await conn.execute(
            "INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public, updated_by, updated_at) "
            "VALUES ($1::uuid, $2, $3::jsonb, 'counterparty_sim', false, $4::uuid, now()) ON CONFLICT (org_id, "
            "setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()", str(target_org_id), SETTING, json.dumps(bool(body.allowed)), str(principal.user_id))
        await conn.execute(
            "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES ($1::uuid, "
            "$2::uuid, 'counterparty.org_setting_changed', 'org_settings', $3::uuid, $4::jsonb)", str(principal.org_id),
            str(principal.user_id), str(target_org_id),
            json.dumps({"setting_key": SETTING, "before": bool(before), "after": bool(body.allowed)}))
    return _envelope(principal, {"org_id": str(target_org_id), "allowed": bool(body.allowed), "before": bool(before)})


# ── one counterparty ────────────────────────────────────────────────────────────────────────────────────────────
@router.get(f"{_P}/{{counterparty_id}}")
async def get_counterparty(request: Request, counterparty_id: UUID):
    principal, out, types = await _sim(request, lambda c: R.detail(c, counterparty_id))
    base = str(request.base_url).rstrip("/")
    out["destination_url"] = base + out["counterparty"]["destination_path"]
    out["mirror_url"] = out["destination_url"] + "/mirror"
    return _envelope(principal, out, types)


@router.put(f"{_P}/{{counterparty_id}}")
async def edit_counterparty(request: Request, counterparty_id: UUID, body: EditIn):
    async def fn(c):
        return await R.edit(c, counterparty_id, name=body.name, notes=body.notes, actor=request.state.principal.user_id)
    principal, out, types = await _sim(request, fn, action="counterparty.edited", resource=counterparty_id,
                                       audit_payload=lambda o: {k: o.get(k) for k in ("changed", "before", "after")})
    return _envelope(principal, out, types)


async def _status(request: Request, counterparty_id: UUID, status: str, action: str):
    async def fn(c):
        return await R.set_status(c, counterparty_id, status, request.state.principal.user_id)
    principal, out, types = await _sim(request, fn, action=action, resource=counterparty_id, audit_payload=lambda o: o)
    return _envelope(principal, out, types)


@router.post(f"{_P}/{{counterparty_id}}/activate")
async def activate_counterparty(request: Request, counterparty_id: UUID):
    return await _status(request, counterparty_id, "active", "counterparty.activated")


@router.post(f"{_P}/{{counterparty_id}}/disable")
async def disable_counterparty(request: Request, counterparty_id: UUID):
    return await _status(request, counterparty_id, "disabled", "counterparty.disabled")


def _no_secret(o: dict) -> dict:
    return {k: v for k, v in o.items() if k != "issued"}


@router.post(f"{_P}/{{counterparty_id}}/key/generate")
async def generate_key(request: Request, counterparty_id: UUID):
    principal, out, types = await _sim(
        request, lambda c: R.generate_key(c, counterparty_id, request.state.principal.user_id),
        action="counterparty.key_generated", resource=counterparty_id, audit_payload=_no_secret)
    return _envelope(principal, {**out, "shown_once": True}, types)


@router.post(f"{_P}/{{counterparty_id}}/key/rotate")
async def rotate_key(request: Request, counterparty_id: UUID):
    principal, out, types = await _sim(
        request, lambda c: R.rotate_key(c, counterparty_id, request.state.principal.user_id),
        action="counterparty.key_rotated", resource=counterparty_id, audit_payload=_no_secret)
    return _envelope(principal, {**out, "shown_once": True}, types)


@router.post(f"{_P}/{{counterparty_id}}/key/revoke")
async def revoke_key(request: Request, counterparty_id: UUID):
    principal, out, types = await _sim(
        request, lambda c: R.revoke_key(c, counterparty_id, request.state.principal.user_id),
        action="counterparty.key_revoked", resource=counterparty_id, audit_payload=lambda o: o)
    return _envelope(principal, out, types)


@router.post(f"{_P}/{{counterparty_id}}/bank/regenerate")
async def regenerate_bank(request: Request, counterparty_id: UUID):
    principal, out, types = await _sim(
        request, lambda c: R.regenerate_bank(c, counterparty_id, request.state.principal.user_id),
        action="counterparty.bank_regenerated", resource=counterparty_id, audit_payload=lambda o: o)
    return _envelope(principal, out, types)


@router.put(f"{_P}/{{counterparty_id}}/fix")
async def set_fix(request: Request, counterparty_id: UUID, body: FixIn):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "rotate_password"}

    async def fn(c):
        return await R.set_fix(c, counterparty_id, fields, request.state.principal.user_id,
                               rotate_password=body.rotate_password)
    principal, out, types = await _sim(
        request, fn, action="counterparty.fix_updated", resource=counterparty_id,
        audit_payload=lambda o: {"before": o["before"], "after": o["after"], "password_rotated": bool(o["issued"])})
    return _envelope(principal, {**out, "shown_once": bool(out["issued"])}, types)


@router.put(f"{_P}/{{counterparty_id}}/behaviour")
async def set_behaviour(request: Request, counterparty_id: UUID, body: BehaviourIn):
    fields = body.model_dump(exclude_unset=True)

    async def fn(c):
        return await R.set_behaviour(c, counterparty_id, fields, request.state.principal.user_id)
    principal, out, types = await _sim(request, fn, action="counterparty.behaviour_updated", resource=counterparty_id,
                                       audit_payload=lambda o: o)
    return _envelope(principal, out, types)


@router.post(f"{_P}/{{counterparty_id}}/clock/advance")
async def advance_clock(request: Request, counterparty_id: UUID):
    principal, out, types = await _sim(
        request, lambda c: R.advance_clock(c, counterparty_id, request.state.principal.user_id),
        action="counterparty.clock_advanced", resource=counterparty_id, audit_payload=lambda o: o)
    return _envelope(principal, out, types)


@router.get(f"{_P}/{{counterparty_id}}/requests")
async def request_log(request: Request, counterparty_id: UUID, limit: int = 50):
    lim = max(1, min(int(limit), 500))

    async def fn(c):
        await R._require(c, counterparty_id)
        return await R.request_log(c, counterparty_id, lim)
    principal, rows, types = await _sim(request, fn)
    return _envelope(principal, {"rows": rows, "limit": lim}, types)


@router.post(f"{_P}/{{counterparty_id}}/test-message")
async def test_message(request: Request, counterparty_id: UUID, body: TestMessageIn):
    """Posts a sample envelope to this counterparty's mirror through the mounted sub-app IN-PROCESS (ASGI transport —
    no network), authenticated with the key the admin pastes. The key is never stored or logged."""
    principal, cp, types = await _sim(request, lambda c: R._require(c, counterparty_id))
    from cpsim.app import app as cpsim_app

    envelope = SM.sample_envelope(body.message_type)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cpsim_app), base_url="http://cpsim.internal",
                                 timeout=30.0) as client:
        res = await client.post(f"/c/{cp['slug']}/mirror", json=envelope,
                                headers={"Authorization": f"Bearer {body.key}"})
    try:
        reply = res.json()
    except ValueError:
        reply = {"raw": res.text[:2000]}
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _audit(conn, principal, "counterparty.test_message", "counterparty", counterparty_id,
                     {"status": res.status_code, "message_id": envelope["message_id"], "simulated": True})
    return _envelope(principal, {"sent": envelope, "status": res.status_code, "reply": reply,
                                 "simulated_header": res.headers.get("x-counterparty-simulated")}, types)
