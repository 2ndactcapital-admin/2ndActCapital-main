"""REP15 — platform-published models and tenant subscriptions. services.model_provider holds the rules; every route
names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it). Endpoint functions carry an ``mp_`` prefix so
the route scanner never confuses one with a service function of the same name.

TENANT (/model-catalog)
  view_models          GET  /model-catalog                                  the catalog + this org's subscriptions
                       GET  /model-catalog/subscriptions                    this org's subscriptions (model, accounts)
                       GET  /model-catalog/fees                             invoices this org owes
  manage_org_settings  POST /model-catalog/{catalog_model_id}/acknowledge   acknowledge the agreement in force
                       POST /model-catalog/{catalog_model_id}/subscribe     request a subscription (the maker)
                       POST /model-catalog/subscriptions/{id}/end           end this org's subscription
  manage_models        POST /model-catalog/subscriptions/{id}/adopt         submit a provided version (the maker;
                                                                            rep3's approval task decides it)
                       POST /model-catalog/subscriptions/{id}/refresh       import the latest published version
                       POST /model-catalog/subscriptions/{id}/copy          copy into this org's own registry

PROVIDER (/model-provider) — the provider org is the CALLER's own org
  view_models          GET  /model-provider/subscribers                     status, account counts, model assets —
                                                                            nothing else of any tenant
  Super Admin          GET  /model-provider                                 the provider record, the gate, readiness
                       PUT  /model-provider/provider                        the provider record
                       POST /model-provider/gate/open | /gate/close
                       GET  /model-provider/publishing                      models, publishable marks, versions
                       POST /model-provider/models/{model_id}/publishable
                       POST /model-provider/catalog-models/{id}/withdraw
                       POST /model-provider/catalog-models/{id}/publish     request publication (the maker)
                       POST /model-provider/catalog-versions/{id}/decide    publish / reject (a different person)
                       POST /model-provider/catalog-versions/{id}/retire
                       POST /model-provider/subscriptions/{id}/decide       approve / reject a tenant's request
                       POST /model-provider/subscriptions/{id}/suspend | resume | end
                       POST /model-provider/fees/accrue
                       GET  /model-provider/fees/invoices
                       POST /model-provider/fees/invoices
                       POST /model-provider/fees/invoices/{id}/correct      a reversal, never an edit

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value;
every request model forbids extras. Amounts travel as decimal STRINGS; a JSON float is a 422. While the gate is
OFF every publish / subscribe / adopt / copy / fee write answers 409 with the gate's reason.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import disclosures as DL
from services import human_tasks as HT
from services import model_provider as PM
from services import models as M
from services.authz import require_permission, require_super_admin
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["model_provider"])

VIEW, MANAGE, APPROVE, ADMIN = PM.VIEW, PM.MANAGE, PM.APPROVE, PM.ORG_ADMIN


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderIn(_Body):
    legal_name: str | None = Field(default=None, max_length=200)
    sec_file_number: str | None = Field(default=None, max_length=20)
    crd_number: str | None = Field(default=None, max_length=12)
    registration_status: Literal["not_registered", "pending", "registered", "withdrawn"] = "not_registered"
    effective_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ReasonIn(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class PublishableIn(_Body):
    default_fee_bps: Decimal
    construction_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("default_fee_bps", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class PublishIn(_Body):
    version_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    construction_notes: str = Field(min_length=1, max_length=4000)
    holdings_as_of: date
    fee_bps: Decimal | None = None

    @field_validator("fee_bps", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class DecideIn(_Body):
    outcome: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class AckIn(_Body):
    version_id: UUID


class AdoptIn(_Body):
    version_id: UUID


class CopyIn(_Body):
    name: str = Field(min_length=1, max_length=200)


class AccrueIn(_Body):
    day_from: date
    day_to: date


class InvoiceIn(_Body):
    subscription_id: UUID
    period_start: date
    period_end: date


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (PM.ProviderNotFound, M.ModelNotFound, HT.HumanTaskNotFound, DL.DisclosureNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (PM.MakerCheckerViolation, M.MakerCheckerViolation, HT.MakerCheckerError, PermissionError,
                        HT.HumanTaskPermissionError, HT.AgentActorRefused)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (PM.ProviderConflict, M.ModelConflict, HT.HumanTaskConflict, DL.DisclosureConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (PM.ProviderError, M.ModelError, HT.HumanTaskError, DL.DisclosureError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


async def _act(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


def _permissions(principal) -> dict:
    sa = principal.is_super_admin
    return {"can_read": sa or VIEW in principal.permissions, "can_write": sa or MANAGE in principal.permissions,
            "can_approve": sa or APPROVE in principal.permissions, "can_admin": sa or ADMIN in principal.permissions,
            "is_super_admin": sa, "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
            "admin_permission": ADMIN}


def _env(principal, body: dict) -> dict:
    perms = _permissions(principal)
    return {**body, "permissions": perms,
            "vocabularies": PM.vocabularies(can_write=perms["can_write"] is True,
                                            can_approve=perms["can_approve"] is True,
                                            can_admin=perms["can_admin"] is True,
                                            is_super_admin=perms["is_super_admin"] is True)}


# ═══ Tenant ═════════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/model-catalog")
async def mp_catalog(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            items = await PM.catalog(conn, org_id)
            for c in items:
                c["agreement"] = await PM.agreement_state(conn, org_id=org_id, user_id=principal.user_id,
                                                          catalog_model_id=c["catalog_model_id"])
            return {"catalog": items, "subscriptions": await PM.tenant_subscriptions(conn, org_id)}

    return _env(principal, await _act(go()))


@router.get("/model-catalog/subscriptions")
async def mp_subscriptions(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            return {"subscriptions": await PM.tenant_subscriptions(conn, org_id)}

    return _env(principal, await _act(go()))


@router.get("/model-catalog/fees")
async def mp_tenant_fees(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            return {"invoices": [i for i in await PM.invoices(conn, org_id) if i["tenant_org_id"] == str(org_id)]}

    return _env(principal, await _act(go()))


@router.post("/model-catalog/{catalog_model_id}/acknowledge")
async def mp_acknowledge(catalog_model_id: UUID, body: AckIn, request: Request):
    principal = require_permission(request, ADMIN)
    pool = await get_pool()
    return await _act(PM.acknowledge_agreement(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                               catalog_model_id=catalog_model_id, version_id=body.version_id))


@router.post("/model-catalog/{catalog_model_id}/subscribe", status_code=201)
async def mp_subscribe(catalog_model_id: UUID, request: Request):
    principal = require_permission(request, ADMIN)
    pool = await get_pool()
    return await _act(PM.request_subscription(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                              catalog_model_id=catalog_model_id))


@router.post("/model-catalog/subscriptions/{subscription_id}/end")
async def mp_tenant_end(subscription_id: UUID, body: ReasonIn, request: Request):
    principal = require_permission(request, ADMIN)
    pool = await get_pool()
    return await _act(PM.change_subscription(pool, acting_org=get_org_id(request), actor_id=principal.user_id,
                                             subscription_id=subscription_id, action="end", reason=body.reason,
                                             is_super_admin=principal.is_super_admin))


@router.post("/model-catalog/subscriptions/{subscription_id}/adopt")
async def mp_adopt(subscription_id: UUID, body: AdoptIn, request: Request):
    principal = require_permission(request, MANAGE)
    pool = await get_pool()
    return await _act(PM.adopt(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                               subscription_id=subscription_id, version_id=body.version_id))


@router.post("/model-catalog/subscriptions/{subscription_id}/refresh")
async def mp_refresh(subscription_id: UUID, request: Request):
    principal = require_permission(request, MANAGE)
    pool = await get_pool()
    return await _act(PM.refresh(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                 subscription_id=subscription_id))


@router.post("/model-catalog/subscriptions/{subscription_id}/copy", status_code=201)
async def mp_copy(subscription_id: UUID, body: CopyIn, request: Request):
    principal = require_permission(request, MANAGE)
    pool = await get_pool()
    return await _act(PM.copy_model(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                    subscription_id=subscription_id, name=body.name))


# ═══ Provider ═══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/model-provider/subscribers")
async def mp_subscribers(request: Request, day: date | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            await PM.require_provider_org(conn, org_id)
            return await PM.subscribers(conn, org_id, day)

    return _env(principal, await _act(go()))


@router.get("/model-provider")
async def mp_provider_state(request: Request):
    principal = require_super_admin(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            await PM.require_provider_org(conn, org_id)
            return await PM.provider_state(conn, org_id)

    return _env(principal, await _act(go()))


@router.put("/model-provider/provider")
async def mp_save_provider(body: ProviderIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.save_provider(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                       data=body.model_dump()))


@router.post("/model-provider/gate/open")
async def mp_gate_open(body: ReasonIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.open_gate(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                   reason=body.reason))


@router.post("/model-provider/gate/close")
async def mp_gate_close(body: ReasonIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.close_gate(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                    reason=body.reason))


@router.get("/model-provider/publishing")
async def mp_publishing(request: Request):
    principal = require_super_admin(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            await PM.require_provider_org(conn, org_id)
            return await PM.publishing(conn, org_id)

    return _env(principal, await _act(go()))


@router.post("/model-provider/models/{model_id}/publishable", status_code=201)
async def mp_publishable(model_id: UUID, body: PublishableIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.mark_publishable(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                          model_id=model_id, default_fee_bps=body.default_fee_bps,
                                          construction_notes=body.construction_notes))


@router.post("/model-provider/catalog-models/{catalog_model_id}/withdraw")
async def mp_withdraw(catalog_model_id: UUID, body: ReasonIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.withdraw_publishable(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                              catalog_model_id=catalog_model_id, reason=body.reason))


@router.post("/model-provider/catalog-models/{catalog_model_id}/publish", status_code=201)
async def mp_request_publish(catalog_model_id: UUID, body: PublishIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.request_publish(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                         catalog_model_id=catalog_model_id, version_id=body.version_id,
                                         name=body.name, description=body.description,
                                         construction_notes=body.construction_notes,
                                         holdings_as_of=body.holdings_as_of, fee_bps=body.fee_bps))


@router.post("/model-provider/catalog-versions/{catalog_version_id}/decide")
async def mp_decide_publish(catalog_version_id: UUID, body: DecideIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.decide_publish(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                        catalog_version_id=catalog_version_id, outcome=body.outcome, note=body.note))


@router.post("/model-provider/catalog-versions/{catalog_version_id}/retire")
async def mp_retire(catalog_version_id: UUID, body: ReasonIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.retire_version(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                        catalog_version_id=catalog_version_id, reason=body.reason))


@router.post("/model-provider/subscriptions/{subscription_id}/decide")
async def mp_decide_subscription(subscription_id: UUID, body: DecideIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.decide_subscription(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                             subscription_id=subscription_id, outcome=body.outcome, note=body.note))


async def _change(request: Request, subscription_id: UUID, action: str, reason: str):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.change_subscription(pool, acting_org=get_org_id(request), actor_id=principal.user_id,
                                             subscription_id=subscription_id, action=action, reason=reason,
                                             is_super_admin=principal.is_super_admin))


@router.post("/model-provider/subscriptions/{subscription_id}/suspend")
async def mp_suspend(subscription_id: UUID, body: ReasonIn, request: Request):
    require_super_admin(request)
    return await _change(request, subscription_id, "suspend", body.reason)


@router.post("/model-provider/subscriptions/{subscription_id}/resume")
async def mp_resume(subscription_id: UUID, body: ReasonIn, request: Request):
    require_super_admin(request)
    return await _change(request, subscription_id, "resume", body.reason)


@router.post("/model-provider/subscriptions/{subscription_id}/end")
async def mp_provider_end(subscription_id: UUID, body: ReasonIn, request: Request):
    require_super_admin(request)
    return await _change(request, subscription_id, "end", body.reason)


@router.post("/model-provider/fees/accrue")
async def mp_accrue(body: AccrueIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.accrue(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                day_from=body.day_from, day_to=body.day_to))


@router.get("/model-provider/fees/invoices")
async def mp_invoices(request: Request):
    principal = require_super_admin(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async def go():
        async with pool.acquire() as conn:
            return {"invoices": [i for i in await PM.invoices(conn, org_id) if i["provider_org_id"] == str(org_id)]}

    return _env(principal, await _act(go()))


@router.post("/model-provider/fees/invoices", status_code=201)
async def mp_invoice(body: InvoiceIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.invoice(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                 subscription_id=body.subscription_id, period_start=body.period_start,
                                 period_end=body.period_end))


@router.post("/model-provider/fees/invoices/{invoice_id}/correct", status_code=201)
async def mp_correct(invoice_id: UUID, body: ReasonIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(PM.correct_invoice(pool, provider_org=get_org_id(request), actor_id=principal.user_id,
                                         invoice_id=invoice_id, reason=body.reason))
