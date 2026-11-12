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
    POST /admin/counterparties/{id}/clock/advance           one US business day (services/us_business_days); cpsim03:
                                                             working orders re-evaluated, the response lists the fills;
                                                             cpsim04: transfers acknowledged / settled / returned and
                                                             settled fills' cash, listed too
    GET  /admin/counterparties/{id}/orders                  cpsim03: the orders received (read-only)
    GET  /admin/counterparties/{id}/fix/messages            cpsim05: the FIX session + message log (read-only)
    GET  /admin/counterparties/{id}/transfers               cpsim04: the transfers a custodian received (+ cash ledger)
    POST /admin/counterparties/{id}/transfers/{transfer_id}/consent   cpsim04 D8: "Record member consent"
    GET  /admin/counterparties/{id}/received                cpsim04: a BANK's received transfers (+ its links)
    POST /admin/counterparties/{id}/links                   cpsim04 D6: link a platform account to an account in a
                                                             BANK's book (portfolio.external_references, source
                                                             CPSIM_BANK, last-four references only)
    GET  /admin/counterparties/{id}/requests?limit=N        the request log (no bodies, no keys)
    POST /admin/counterparties/{id}/test-message            posts a sample envelope to the mirror IN-PROCESS with the
                                                             key the admin pastes (never stored) — no network

cpsim02 — the BOOK (the counterparty's custodian data), audited ``counterparty.book_*``:
    GET  /admin/counterparties/{id}/book                    live counts + the upload history + vocabularies
    GET  /admin/counterparties/{id}/book/template?kind=&format=csv|json   the template (columns, one example row)
    POST /admin/counterparties/{id}/book/preview            multipart file + kind → the validation report (writes
                                                             nothing but its audit row)
    POST /admin/counterparties/{id}/book/accept             the SAME file re-posted → replace the named accounts' rows,
                                                             log the upload, fire the D10 webhooks
    GET  /admin/counterparties/{id}/book/export?kind=&format=csv | ?format=json   the import columns (a round trip) /
                                                             the API's own response shapes
  Files are parsed HERE (CSV utf-8 with a header row, or JSON: a list of objects or {"rows": [...]}) and handed to the
  package as plain rows (the package parses no file format). Limit: MAX_BOOK_BYTES, refused 413 before decoding.

``target_org_id`` names the org a Super Admin is switching, a platform action (like /orgs/{org_id}/settings) — never the
caller's own tenant scope, which still comes only from the verified session. Every response says ``simulated: true``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from cpsim import book as SB
from cpsim import db as SDB
from cpsim import mirror as SM
from cpsim import orders as SO
from cpsim import ledger as LG
from cpsim import registry as R
from cpsim import transfers as ST
from services import altruist_oauth as AO
from services.authz import require_super_admin
from services.custody.base import parse_text
from services.database import get_pool

router = APIRouter(tags=["admin-counterparties"])
_P = "/admin/counterparties"
SETTING = R.SETTING_ALLOWED
STATUS_LABELS = {"draft": "Draft", "active": "Active", "disabled": "Disabled"}
OUTCOME_LABELS = {"accept": "Accept", "reject": "Reject with reason", "partial": "Partial", "delay": "Delay"}
GROUP_LABELS = {"connection": "Connection", "fix": "FIX session", "bank_domestic": "Bank details (domestic)",
                "bank_international": "Bank details (international)", "behaviour": "Behaviour", "clock": "Clock"}
TABS = [("connection", "Connection"), ("fix", "FIX"), ("bank", "Bank details"), ("behaviour", "Behaviour"),
        ("clock", "Clock"), ("activity", "Activity"), ("import_export", "Import / Export"),
        ("connections", "Connections"), ("webhooks", "Webhooks"), ("conformance", "Conformance"), ("orders", "Orders"),
        ("transfers", "Transfers"), ("received", "Received")]
#: cpsim02 — the tabs that need a BOOK (a REST + OAuth custodian type); the page enables them per type from this list
BOOK_TABS = ["import_export", "connections", "webhooks", "conformance", "orders", "transfers"]
#: cpsim04 D6 — a simulated BANK keeps a book too (cash accounts) and shows what it received
BANK_TABS = ["import_export", "received"]
TRANSFER_STATUS_LABELS = {"received": "Received", "pending_consent": "Awaiting the member's consent",
                          "acknowledged": "Acknowledged", "settled": "Settled", "rejected": "Rejected",
                          "returned": "Returned", "cancelled": "Cancelled"}
#: cpsim04 — what v1 of the simulated money movement does NOT do (shown on the Transfers tab, CPSIM_ADMIN.md)
TRANSFERS_V1_LIMITS = ["USD only; journals, ACH and wires — no checks, no third-party payees.",
                       "Fills move cash on their settle date only for an account whose book has a cash row.",
                       "A later book upload replaces the account and so overwrites its cash ledger."]
#: cpsim03 — what v1 of the simulated order book does NOT do (shown on the Orders tab, CPSIM_ADMIN.md)
ORDERS_V1_LIMITS = ["Fills move positions and add buy / sell transactions; a fill's cash moves on its settle date "
                    "(cpsim04) — no corporate actions and no fees (v2).", "A later book upload replaces the account and so overwrites fills.",
                    "Day orders are not expired; a REST order's partial remainder stays working until it is cancelled (a FIX "
                    "order's remainder fills at the next clock advance — cpsim05)."]
EDITABLE = ["name", "notes", "status", "key", "bank_details", "fix_session", "behaviour", "clock", "org_access",
            "book", "connections", "webhooks", "conformance"]
#: a BOOK file, not a custodian export: refused before decoding (routers/custody_import.py's _read_upload shape)
MAX_BOOK_BYTES = 2 * 1024 * 1024
BOOK_KIND_LABELS = {"households": "Households", "accounts": "Accounts", "positions": "Positions (holdings and values)",
                    "transactions": "Transactions", "lots": "Tax lots (cost basis)"}


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
    webhook_url: str | None = Field(default=None, max_length=500, pattern=r"^https://[^\s#?]+$")
    consent_required: bool | None = None      # cpsim04 D8


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
        "book_tabs": list(BOOK_TABS),
        "book_institution_types": [t["key"] for t in (types or []) if t.get("protocol") == "rest_oauth"],
        "bank_tabs": list(BANK_TABS),
        "bank_institution_types": [t["key"] for t in (types or []) if t.get("protocol") in ("bank_wire_ach",
                                                                                              "bank_swift")],
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
    """One US business day — and (cpsim03 D6) every working order re-evaluated at the new date's price, in the SAME
    transaction; the response (and its audit row) says what the advance filled. Order webhooks after commit."""
    from datetime import date as _date

    async def fn(c):
        moved = await R.advance_clock(c, counterparty_id, request.state.principal.user_id)
        day = _date.fromisoformat(moved["to"])
        filled = await SO.reevaluate(c, counterparty_id, day)
        beh = await R.behaviour(c, counterparty_id) or {}
        transfers = await ST.advance(c, counterparty_id, day, beh)        # cpsim04 D3: settle / return on the clock
        fills_cash = await LG.settle_fills(c, counterparty_id, day)       # cpsim04 D3: a settled fill's cash
        cp = await R._require(c, counterparty_id)
        return {**moved, "filled": filled, "transfers": transfers, "fills_cash": fills_cash, "_slug": cp["slug"],
                "_behaviour": beh}
    principal, out, types = await _sim(
        request, fn, action="counterparty.clock_advanced", resource=counterparty_id,
        audit_payload=lambda o: {"from": o["from"], "to": o["to"],
                                 "filled": [{k: f[k] for k in ("order_id", "status", "executions")} for f in o["filled"]],
                                 "transfers": [{k: t[k] for k in ("transfer_id", "from", "status", "return_code")}
                                               for t in o["transfers"]],
                                 "fills_cash": [{k: f[k] for k in ("exec_id", "kind", "amount")}
                                                for f in o["fills_cash"]]})
    slug, beh = out.pop("_slug"), out.pop("_behaviour")
    items = [({"order_id": f["order_id"], "client_order_id": f["client_order_id"], "account_id": f["account_id"]},
              ["order.filled"] if f["executions"] else ["order.updated"]) for f in out["filled"]]
    from cpsim import webhooks as SW

    if items:
        await SW.after_orders(counterparty_id=counterparty_id, slug=slug, items=items, behaviour=beh)
    if out["transfers"]:
        await SW.after_transfers(counterparty_id=counterparty_id, slug=slug, behaviour=beh,
                                 items=[{k: t[k] for k in ("transfer_id", "client_transfer_id", "account_id")}
                                        for t in out["transfers"]])
    return _envelope(principal, out, types)


@router.get(f"{_P}/{{counterparty_id}}/orders")
async def orders(request: Request, counterparty_id: UUID, limit: int = 100):
    """cpsim03 — the Orders tab (read-only): the orders this counterparty received, their outcome and executions."""
    lim = max(1, min(int(limit), 500))

    async def fn(c):
        await R._require(c, counterparty_id)
        return {"rows": await SO.list_recent(c, counterparty_id, lim), "limit": lim,
                "status_labels": [{"key": k, "label": v} for k, v in SO.STATUS_LABELS.items()],
                "outcome_labels": [{"key": k, "label": v} for k, v in OUTCOME_LABELS.items()],
                "v1_limits": ORDERS_V1_LIMITS}
    principal, out, types = await _sim(request, fn)
    return _envelope(principal, out, types)


@router.get(f"{_P}/{{counterparty_id}}/fix/messages")
async def fix_messages(request: Request, counterparty_id: UUID, limit: int = 200):
    """cpsim05 — the FIX tab (read-only): the live session fields and the message log (newest first, every message in
    and out, readable tag / name / value). One query for the log (no per-row reads). No secret is ever stored."""
    from cpsim import fix as FX

    lim = max(1, min(int(limit), 1000))

    async def fn(c):
        await R._require(c, counterparty_id)
        rows = await FX.list_messages(c, counterparty_id, lim)
        for r in rows:
            r["fields"] = FX.readable(r["raw"])
        return {"session": await FX.session(c, counterparty_id), "rows": rows, "limit": lim,
                "message_names": [{"key": k, "label": v} for k, v in FX.MSG_NAMES.items()],
                "notes": ["SIMULATED — FIX 4.4 tag=value carried over HTTP: one message in, the reply message(s) out. "
                          "No FIX session, logon, heartbeat or sequence recovery; MsgSeqNum is recorded, not enforced.",
                          "No Altruist FIX contract has been seen — this is a simulated counterparty only."]}
    principal, out, types = await _sim(request, fn)
    return _envelope(principal, out, types)


# ── cpsim04: Transfers (a custodian), Received + links (a bank), Record member consent ─────────────────────────────
def _transfer_vocab() -> dict:
    return {"status_labels": [{"key": k, "label": v} for k, v in TRANSFER_STATUS_LABELS.items()],
            "v1_limits": TRANSFERS_V1_LIMITS}


@router.get(f"{_P}/{{counterparty_id}}/transfers")
async def transfers(request: Request, counterparty_id: UUID, limit: int = 100):
    """The Transfers tab (a custodian): what it received, each transfer's events, and the cash ledger's last moves.
    Bank legs show the bank's slug and REFERENCES only (last four digits)."""
    lim = max(1, min(int(limit), 500))

    async def fn(c):
        await R._require(c, counterparty_id)
        return {"rows": await ST.list_recent(c, counterparty_id, lim), "limit": lim,
                "cash_movements": await LG.movements(c, counterparty_id, None, lim),
                "behaviour": {"consent_required": bool((await R.behaviour(c, counterparty_id) or {})
                                                       .get("consent_required"))},
                **_transfer_vocab()}
    principal, out, types = await _sim(request, fn)
    return _envelope(principal, out, types)


@router.post(f"{_P}/{{counterparty_id}}/transfers/{{transfer_id}}/consent")
async def record_consent(request: Request, counterparty_id: UUID, transfer_id: str):
    """D8 — the custodian's e-consent screen, simulated: a transfer awaiting the member's consent is acknowledged now
    (the bank check and the debit run), then settles on the clock. Audited; ``transfer.updated`` after commit."""
    from datetime import date as _date

    if not re.match(r"^sim-t-[0-9a-f]{16}$", transfer_id or ""):
        raise HTTPException(status_code=404, detail="no such transfer at this simulated custodian")

    async def fn(c):
        cp = await R._require(c, counterparty_id)
        beh = await R.behaviour(c, counterparty_id) or {}
        day = await R.business_date(c, counterparty_id) or R.today()
        try:
            t = await ST.record_consent(c, counterparty_id, transfer_id, day if isinstance(day, _date) else R.today(),
                                        beh)
        except ST.TransferError as exc:
            raise R.Conflict(str(exc)) if exc.status == 409 else R.NotFound(str(exc)) from exc
        return {"transfer": t, "_slug": cp["slug"], "_behaviour": beh}
    principal, out, types = await _sim(request, fn, action="counterparty.transfer_consent_recorded",
                                       resource=counterparty_id,
                                       audit_payload=lambda o: {"transfer_id": transfer_id,
                                                                "status": o["transfer"]["status"]})
    slug, beh = out.pop("_slug"), out.pop("_behaviour")
    from cpsim import webhooks as SW

    t = out["transfer"]
    await SW.after_transfers(counterparty_id=counterparty_id, slug=slug, behaviour=beh,
                             items=[{"transfer_id": t["transfer_id"], "client_transfer_id": t["client_transfer_id"],
                                     "account_id": t["from_account_id"]}])
    return _envelope(principal, out, types)


async def _bank(c, counterparty_id) -> dict:
    cp = await R._require(c, counterparty_id)
    if cp["institution_type"] not in ST.BANK_TYPES:
        raise R.RegistryError("only a simulated bank receives transfers and holds linked accounts")
    return cp


@router.get(f"{_P}/{{counterparty_id}}/received")
async def received(request: Request, counterparty_id: UUID, limit: int = 100):
    """The Received tab (a bank): every transfer addressed to it, its book's cash ledger, and the platform accounts
    linked to its book accounts (masks only)."""
    from services import payment_custodian as PC

    lim = max(1, min(int(limit), 500))

    async def fn(c):
        cp = await _bank(c, counterparty_id)
        return {"slug": cp["slug"], "rows": await ST.list_received(c, cp["slug"], lim), "limit": lim,
                "cash_movements": await LG.movements(c, counterparty_id, None, lim), **_transfer_vocab()}
    principal, out, types = await _sim(request, fn)
    pool = await get_pool()
    async with pool.acquire() as conn:
        out["links"] = await PC.bank_links(conn, out["slug"])
    return _envelope(principal, out, types)


class LinkIn(_Body):
    target_org_id: UUID
    account_id: UUID
    bank_account_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


@router.post(f"{_P}/{{counterparty_id}}/links")
async def link_account(request: Request, counterparty_id: UUID, body: LinkIn):
    """D6 — crosswalk a platform account (an ALLOWED org; a real org's demo household only) to an account in this
    BANK's book: ``portfolio.external_references`` source ``CPSIM_BANK``, written in the target org's RLS context
    through services.portfolio_assets.upsert_external_reference. The external id carries the bank's REFERENCES —
    the last four digits of its ACH / wire routing numbers (domestic) or of its IBAN plus its BIC (international) —
    never a full number. Audited."""
    from services import payment_custodian as PC

    async def fn(c):
        cp = await _bank(c, counterparty_id)
        if cp["status"] != "active":
            raise R.Conflict("activate the bank before linking accounts to it")
        if not await SB.account_exists(c, counterparty_id, body.bank_account_id):
            raise R.NotFound("no such account in this bank's book")
        kind = ST.BANK_TYPES[cp["institution_type"]]
        d = await c.fetchrow("SELECT aba_wire, aba_ach, bic, iban FROM cpsim.counterparty_bank_details WHERE "
                             "counterparty_id = $1::uuid AND kind = $2 AND valid_to IS NULL", str(counterparty_id), kind)
        if d is None:
            raise R.Conflict("the bank has no routing details — regenerate them first")
        refs = ({"ach4": ST.last4(d["aba_ach"]), "wire4": ST.last4(d["aba_wire"])} if kind == "domestic" else
                {"iban4": ST.last4(d["iban"]), "bic": (d["bic"] or "").upper()})
        return {"slug": cp["slug"], "kind": kind, "refs": refs}
    principal, out, types = await _sim(request, fn)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _allowed_org(conn, body.target_org_id)
        hh = await conn.fetchval("SELECT household_id FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid "
                                 "AND system_to IS NULL AND valid_to IS NULL", str(body.account_id),
                                 str(body.target_org_id))
        if hh is None:
            raise HTTPException(status_code=404, detail="no such account in that organization")
        if not await conn.fetchval("SELECT public.counterparty_sim_may_connect($1::uuid, $2::uuid)",
                                   str(body.target_org_id), str(hh)):
            raise HTTPException(status_code=409, detail="not a demo household: in a real organization only a household "
                                                        "tagged demo may use a simulated counterparty")
    external_id = PC.bank_xwalk_id(out["slug"], body.bank_account_id, out["refs"])

    async def go():
        from services.portfolio_assets import upsert_external_reference

        async with pool.acquire() as c2:
            return await upsert_external_reference(c2, org_id=str(body.target_org_id), source_system=PC.XWALK_BANK,
                                                   external_id=external_id, record_type="account",
                                                   record_id=str(body.account_id))
    ref_id = await _as_target(body.target_org_id, go)
    async with pool.acquire() as conn:
        await _audit(conn, principal, "counterparty.bank_account_linked", "counterparty", counterparty_id,
                     {"target_org_id": str(body.target_org_id), "account_id": str(body.account_id),
                      "bank_account_id": body.bank_account_id, "reference_id": ref_id, "simulated": True})
    return _envelope(principal, {"linked": True, "reference_id": ref_id, "bank": out["slug"],
                                 "bank_account_id": body.bank_account_id,
                                 "references": PC.mask_refs(out["refs"])}, types)


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


# ── cpsim02: the book ───────────────────────────────────────────────────────────────────────────────────────────
def _book_vocab() -> dict:
    return {"kinds": [{"key": k, "label": BOOK_KIND_LABELS[k], "columns": list(SB.COLUMNS[k]),
                       "required": list(SB.REQUIRED[k])} for k in SB.KINDS],
            "reasons": [{"key": k, "label": v} for k, v in SB.REASONS.items()],
            "formats": ["csv", "json"], "mode": "replace", "max_bytes": MAX_BOOK_BYTES, "max_rows": SB.MAX_ROWS}


def _kind(kind: str) -> str:
    if kind not in SB.KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(SB.KINDS)}")
    return kind


async def _read_book(file: UploadFile, fmt: str | None) -> tuple[list[dict], str, str, str]:
    """(rows, format, sha256, file name). Parsed in the ROUTER — the package receives plain rows."""
    raw = await file.read(MAX_BOOK_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(raw) > MAX_BOOK_BYTES:
        raise HTTPException(status_code=413, detail=f"a book file is limited to {MAX_BOOK_BYTES // 1024} KB")
    name = (file.filename or "upload").strip()[:255] or "upload"
    f = (fmt or ("json" if name.lower().endswith(".json") else "csv")).lower()
    if f not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be csv or json")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="the file is not UTF-8 text") from exc
    if f == "json":
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="the file is not valid JSON") from exc
        rows = doc.get("rows") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise HTTPException(status_code=400, detail='JSON must be a list of rows or {"rows": [...]}')
    else:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="the CSV has no header row")
        rows = [{(k or "").strip(): ("" if v is None else (parse_text(v) or "")) for k, v in r.items()
                 if (k or "").strip()} for r in reader]
    if len(rows) > SB.MAX_ROWS:
        raise HTTPException(status_code=413, detail=f"at most {SB.MAX_ROWS} rows per upload")
    return rows, f, hashlib.sha256(raw).hexdigest(), name


def _book_err(fn):
    async def wrapped(conn):
        try:
            return await fn(conn)
        except SB.BookError as exc:
            raise R.RegistryError(str(exc)) from exc
    return wrapped


@router.get(f"{_P}/{{counterparty_id}}/book")
async def book_summary(request: Request, counterparty_id: UUID):
    async def fn(c):
        await R._require(c, counterparty_id)
        return {"counts": await SB.counts(c, counterparty_id), "uploads": await SB.uploads(c, counterparty_id, 50),
                "business_date": await R.business_date(c, counterparty_id)}
    principal, out, types = await _sim(request, fn)
    env = _envelope(principal, out, types)
    env["vocabularies"]["book"] = _book_vocab()
    return env


@router.get(f"{_P}/{{counterparty_id}}/book/template")
async def book_template(request: Request, counterparty_id: UUID, kind: str, format: str = "csv"):  # noqa: A002
    _kind(kind)
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be csv or json")
    principal, _cp, _types = await _sim(request, lambda c: R._require(c, counterparty_id),
                                        action="counterparty.book_template_downloaded", resource=counterparty_id,
                                        audit_payload={"kind": kind, "format": format})
    t = SB.template(kind)
    if format == "json":
        return Response(json.dumps({"kind": kind, "rows": [t["example"]], "columns": t["columns"],
                                    "required": t["required"], "simulated": True}, indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="cpsim_{kind}_template.json"'})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=t["columns"], lineterminator="\n")
    w.writeheader()
    w.writerow(t["example"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="cpsim_{kind}_template.csv"'})


@router.post(f"{_P}/{{counterparty_id}}/book/preview")
async def book_preview(request: Request, counterparty_id: UUID, kind: str = Form(...), file: UploadFile = File(...),
                       format: str | None = Form(default=None)):  # noqa: A002
    require_super_admin(request)
    _kind(kind)
    rows, fmt_, sha, name = await _read_book(file, format)

    async def fn(c):
        await R._require(c, counterparty_id)
        return await SB.validate(c, counterparty_id, kind, rows)
    principal, out, types = await _sim(
        request, _book_err(fn), action="counterparty.book_previewed", resource=counterparty_id,
        audit_payload=lambda o: {"kind": kind, "file_name": name, "sha256": sha, "format": fmt_, **o["counts"]})
    out = {**out, "file_name": name, "sha256": sha, "format": fmt_}
    env = _envelope(principal, out, types)
    env["vocabularies"]["book"] = _book_vocab()
    return env


@router.post(f"{_P}/{{counterparty_id}}/book/accept")
async def book_accept(request: Request, counterparty_id: UUID, kind: str = Form(...), file: UploadFile = File(...),
                      format: str | None = Form(default=None)):  # noqa: A002
    require_super_admin(request)
    _kind(kind)
    rows, fmt_, sha, name = await _read_book(file, format)

    async def fn(c):
        cp = await R._require(c, counterparty_id)
        res = await SB.accept(c, counterparty_id, kind, rows, file_name=name, sha256=sha, fmt_=fmt_,
                              actor=request.state.principal.user_id)
        return {**res, "slug": cp["slug"], "behaviour": await R.behaviour(c, counterparty_id)}
    principal, out, types = await _sim(
        request, _book_err(fn), action="counterparty.book_accepted", resource=counterparty_id,
        audit_payload=lambda o: {"kind": kind, "file_name": name, "sha256": sha, "format": fmt_,
                                 "upload_id": o["upload_id"], **o["counts"],
                                 "accounts_affected": o["accounts_affected"]})
    # D10 — AFTER the book committed: one signed event per affected account (only when webhooks are on)
    from cpsim import webhooks as SW

    beh = out.pop("behaviour") or {}
    deliveries = await SW.after_upload(counterparty_id=counterparty_id, slug=out["slug"], kind=kind,
                                       accounts=out["accounts_affected"], behaviour=beh)
    out["webhooks"] = deliveries
    return _envelope(principal, out, types)


@router.get(f"{_P}/{{counterparty_id}}/book/export")
async def book_export(request: Request, counterparty_id: UUID, format: str = "csv",  # noqa: A002
                      kind: str | None = None):
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be csv or json")
    if format == "csv":
        _kind(kind or "")

    async def fn(c):
        await R._require(c, counterparty_id)
        if format == "csv":
            return await SB.export_rows(c, counterparty_id, kind)
        clock = await R.business_date(c, counterparty_id) or R.today()
        return await SB.export_api(c, counterparty_id, clock)
    principal, out, _types = await _sim(request, _book_err(fn), action="counterparty.book_exported",
                                        resource=counterparty_id, audit_payload={"kind": kind, "format": format})
    if format == "json":
        return Response(json.dumps(jsonable_encoder({**out, "simulated": True}), indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="cpsim_book.json"'})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(SB.COLUMNS[kind]), lineterminator="\n")
    w.writeheader()
    for r in out:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in SB.COLUMNS[kind]})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="cpsim_{kind}.csv"'})


# ── cpsim02 D10: webhooks — "Send test event" (the flag and the override URL live on PUT /behaviour) ───────────────
class WebhookTestIn(_Body):
    account_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    event_type: Literal["account.positions_updated", "account.transactions_updated"] = "account.positions_updated"


@router.post(f"{_P}/{{counterparty_id}}/webhooks/test")
async def webhook_test(request: Request, counterparty_id: UUID, body: WebhookTestIn):
    """One signed event for an account in the book, through the same sender an accepted upload uses. Honours the
    webhooks flag (off → 409, nothing sent). Every attempt is in the request log (direction out)."""
    async def fn(c):
        cp = await R._require(c, counterparty_id)
        known = await SB.account_exists(c, counterparty_id, body.account_id)
        return {"slug": cp["slug"], "known": known, "behaviour": await R.behaviour(c, counterparty_id) or {}}
    principal, out, types = await _sim(request, fn)
    if not out["known"]:
        raise HTTPException(status_code=404, detail="no such account in this counterparty's book")
    if not out["behaviour"].get("webhooks_enabled"):
        raise HTTPException(status_code=409, detail="webhooks are off for this counterparty (Behaviour tab)")
    from cpsim import webhooks as SW

    delivery = await SW.send_event(counterparty_id=counterparty_id, slug=out["slug"], account_id=body.account_id,
                                   event_type=body.event_type, behaviour=out["behaviour"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _audit(conn, principal, "counterparty.webhook_test_sent", "counterparty", counterparty_id,
                     {k: delivery.get(k) for k in ("event_id", "event_type", "account_id", "status", "attempts",
                                                   "outcome")} | {"simulated": True})
    return _envelope(principal, {"delivery": delivery}, types)


# ── cpsim02: Connections — which allowed orgs point at this counterparty; connect (D4) / sync through the SAME
#    functions the /altruist routes run (routers.altruist_connection), in the TARGET org's RLS context ─────────────
SETTING_COUNTERPARTY = R.SETTING_COUNTERPARTY


class PointIn(_Body):
    point: bool


@router.get(f"{_P}/{{counterparty_id}}/connections")
async def connections(request: Request, counterparty_id: UUID):
    principal, cp, types = await _sim(request, lambda c: R._require(c, counterparty_id))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT o.id, o.name, o.is_test, (c.setting_value #>> '{}') AS counterparty_slug, "
            "ac.status AS connection_status, ac.last_refreshed_at, ac.token_expires_at, ac.created_at AS connected_at, "
            "ac.refresh_token_issued_at, ac.created_at "
            "FROM organizations o JOIN org_settings a ON a.org_id = o.id AND a.setting_key = $1 "
            "AND a.setting_value = 'true'::jsonb LEFT JOIN org_settings c ON c.org_id = o.id AND c.setting_key = $2 "
            "LEFT JOIN altruist_connections ac ON ac.org_id = o.id AND ac.environment = 'simulator' "
            "AND ac.system_to IS NULL ORDER BY o.is_test DESC, o.name", SETTING, SETTING_COUNTERPARTY)]
        for r in rows:
            r["points_here"] = r["counterparty_slug"] == cp["slug"]
            # ALTREAD1 D1 — the refresh token's documented 28-day expiry (the connection status shows it)
            has_conn = r["connection_status"] is not None
            r["refresh_token_expires_at"] = AO.refresh_token_expires_at(r) if has_conn else None
            r["refresh_token_expired"] = bool(has_conn and AO.refresh_token_expired(r))
            r.pop("refresh_token_issued_at", None)
            r.pop("created_at", None)
            last = await conn.fetchrow(
                "SELECT payload, created_at FROM audit_log WHERE action = 'counterparty.connection_synced' AND "
                "resource_type = 'counterparty' AND resource_id = $1::uuid AND payload->>'target_org_id' = $2 "
                "ORDER BY created_at DESC LIMIT 1", str(counterparty_id), str(r["id"]))
            r["last_sync"] = ({**(json.loads(last["payload"]) if isinstance(last["payload"], str) else last["payload"]),
                               "at": last["created_at"]} if last else None)
    return _envelope(principal, {"rows": rows, "slug": cp["slug"], "setting_key": SETTING_COUNTERPARTY}, types)


async def _allowed_org(conn, target_org_id: UUID) -> None:
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM organizations WHERE id = $1::uuid)", str(target_org_id)):
        raise HTTPException(status_code=404, detail="no such organization")
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM org_settings WHERE org_id = $1::uuid AND setting_key = "
                               "$2 AND setting_value = 'true'::jsonb)", str(target_org_id), SETTING):
        raise HTTPException(status_code=409, detail="counterparty_sim.allowed is off for this organization — switch it "
                                                    "on first (Org access)")


@router.put(f"{_P}/{{counterparty_id}}/connections/{{target_org_id}}")
async def point_org(request: Request, counterparty_id: UUID, target_org_id: UUID, body: PointIn):
    """Point (or un-point) an ALLOWED org's "simulator" environment at this counterparty (the org setting
    counterparty_sim.altruist_counterparty — Super Admin only; the database refuses it otherwise)."""
    principal, cp, types = await _sim(request, lambda c: R._require(c, counterparty_id))
    if cp["status"] != "active" and body.point:
        raise HTTPException(status_code=409, detail="activate the counterparty before pointing an org at it")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _allowed_org(conn, target_org_id)
        before = await conn.fetchval("SELECT setting_value #>> '{}' FROM org_settings WHERE org_id = $1::uuid AND "
                                     "setting_key = $2", str(target_org_id), SETTING_COUNTERPARTY)
        value = json.dumps(cp["slug"] if body.point else None)
        await conn.execute(
            "INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public, updated_by, updated_at) "
            "VALUES ($1::uuid, $2, $3::jsonb, 'counterparty_sim', false, $4::uuid, now()) ON CONFLICT (org_id, "
            "setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()", str(target_org_id), SETTING_COUNTERPARTY, value, str(principal.user_id))
        await _audit(conn, principal, "counterparty.connection_pointed", "counterparty", counterparty_id,
                     {"target_org_id": str(target_org_id), "setting_key": SETTING_COUNTERPARTY, "before": before,
                      "after": cp["slug"] if body.point else None, "simulated": True})
    return _envelope(principal, {"org_id": str(target_org_id), "counterparty_slug": cp["slug"] if body.point else None,
                                 "before": before}, types)


async def _as_target(target_org_id: UUID, fn):
    """Run ``fn()`` in the TARGET org's RLS context (Super Admin carve-out kept) — the same functions the /altruist
    routes call, never a direct write to a portfolio table from here."""
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(str(target_org_id), True)
    try:
        return await fn()
    finally:
        reset_rls_context(tokens)


@router.post(f"{_P}/{{counterparty_id}}/connections/{{target_org_id}}/connect")
async def connect_org(request: Request, counterparty_id: UUID, target_org_id: UUID):
    """D4 for the chosen org: server-to-server authorize → the shared callback handling → then one sync inline."""
    from fastapi import BackgroundTasks

    import routers.altruist_connection as AC

    principal, cp, types = await _sim(request, lambda c: R._require(c, counterparty_id))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _allowed_org(conn, target_org_id)
    deferred = BackgroundTasks()       # the auto-sync is run inline below (in the target's context), not deferred

    async def go():
        out = await AC._connect_simulator(org_id=str(target_org_id), user_id=str(principal.user_id),
                                          background_tasks=deferred)
        new_txns: list = []
        async with pool.acquire() as c2:
            out["sync"] = await AC._run_sync(c2, org_id=str(target_org_id), environment="simulator",
                                             collect=new_txns)
        await AC._publish_credits(str(target_org_id), new_txns)
        return out
    out = await _as_target(target_org_id, go)
    async with pool.acquire() as conn:
        await _audit(conn, principal, "counterparty.connection_connected", "counterparty", counterparty_id,
                     {"target_org_id": str(target_org_id), "connection_id": out.get("connection_id"),
                      "authorized": out.get("authorized"), "simulated": True})
        await _audit(conn, principal, "counterparty.connection_synced", "counterparty", counterparty_id,
                     {"target_org_id": str(target_org_id), **(out.get("sync") or {}), "simulated": True})
    return _envelope(principal, out, types)


@router.post(f"{_P}/{{counterparty_id}}/connections/{{target_org_id}}/sync")
async def sync_org(request: Request, counterparty_id: UUID, target_org_id: UUID):
    import routers.altruist_connection as AC
    from services.altruist_oauth import AltruistNotConnected, AltruistSimulatorRefused

    principal, cp, types = await _sim(request, lambda c: R._require(c, counterparty_id))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _allowed_org(conn, target_org_id)

    async def go():
        await AC._require_simulator(str(target_org_id))
        new_txns: list = []
        async with pool.acquire() as c2:
            try:
                summary = await AC._run_sync(c2, org_id=str(target_org_id), environment="simulator",
                                             collect=new_txns)
            except (AltruistNotConnected, AltruistSimulatorRefused) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        await AC._publish_credits(str(target_org_id), new_txns)
        return summary
    summary = await _as_target(target_org_id, go)
    async with pool.acquire() as conn:
        await _audit(conn, principal, "counterparty.connection_synced", "counterparty", counterparty_id,
                     {"target_org_id": str(target_org_id), **summary, "simulated": True})
    return _envelope(principal, {"sync": summary}, types)


# ── cpsim02 D11: Conformance — run the suite against THIS counterparty, in-process; the last runs ─────────────────
CONFORMANCE_KEEP = 50


class ConformanceIn(_Body):
    key: str = Field(min_length=1, max_length=200)


@router.get(f"{_P}/{{counterparty_id}}/conformance")
async def conformance_runs(request: Request, counterparty_id: UUID):
    async def fn(c):
        await R._require(c, counterparty_id)
        return [dict(r) for r in await c.fetch(
            "SELECT id, target, run_by, started_at, finished_at, passed, failed, results FROM cpsim.conformance_runs "
            "WHERE counterparty_id = $1::uuid ORDER BY started_at DESC LIMIT 20", str(counterparty_id))]
    principal, rows, types = await _sim(request, fn)
    for r in rows:
        if isinstance(r.get("results"), str):
            r["results"] = json.loads(r["results"])
    return _envelope(principal, {"rows": rows, "last": rows[0] if rows else None}, types)


@router.post(f"{_P}/{{counterparty_id}}/conformance")
async def run_conformance(request: Request, counterparty_id: UUID, body: ConformanceIn):
    """The suite (services/altruist_conformance.py) against this counterparty through the mounted sub-app IN-PROCESS
    (no network), with the key the admin pastes (never stored) as client_secret and the live credential's public id as
    client_id. The run is stored (the last CONFORMANCE_KEEP) and audited."""
    from datetime import datetime, timezone

    from starlette.applications import Starlette
    from starlette.routing import Mount

    from cpsim.app import app as cpsim_app
    from services import altruist_conformance as ACF

    async def fn(c):
        cp = await R._require(c, counterparty_id)
        cid = await c.fetchval("SELECT id::text FROM cpsim.counterparty_credentials WHERE counterparty_id = $1::uuid "
                               "AND kind = 'api_key' AND rotated_at IS NULL AND revoked_at IS NULL", str(counterparty_id))
        return {"slug": cp["slug"], "status": cp["status"], "client_id": cid,
                "clock": await R.business_date(c, counterparty_id)}
    principal, cp, types = await _sim(request, fn)
    if not cp["client_id"] or cp["status"] != "active":
        raise HTTPException(status_code=409, detail="the counterparty needs a live key and to be active")
    started = datetime.now(timezone.utc)
    root = "https://cpsim.internal/cpsim/c/" + cp["slug"]
    transport = httpx.ASGITransport(app=Starlette(routes=[Mount("/cpsim", app=cpsim_app)]))
    out = await ACF.run(environment="simulator", hosts={"api_base": root, "login_base": root},
                        credentials=(cp["client_id"], body.key), transport=transport, today=cp["clock"])

    async def store(c):
        rid = await c.fetchval(
            "INSERT INTO cpsim.conformance_runs (counterparty_id, target, run_by, started_at, finished_at, passed, "
            "failed, results) VALUES ($1::uuid, 'simulator', $2::uuid, $3, now(), $4, $5, $6::jsonb) RETURNING id",
            str(counterparty_id), str(principal.user_id), started, out["passed"], out["failed"],
            json.dumps(out["results"], default=str))
        await c.execute("DELETE FROM cpsim.conformance_runs WHERE counterparty_id = $1::uuid AND id NOT IN (SELECT id "
                        "FROM cpsim.conformance_runs WHERE counterparty_id = $1::uuid ORDER BY started_at DESC "
                        "LIMIT $2)", str(counterparty_id), CONFORMANCE_KEEP)
        return str(rid)
    principal, run_id, types = await _sim(request, store, action="counterparty.conformance_run",
                                          resource=counterparty_id,
                                          audit_payload=lambda rid: {"run_id": rid, "passed": out["passed"],
                                                                     "failed": out["failed"]})
    return _envelope(principal, {"run_id": run_id, **out}, types)
