"""REST endpoints for the profitability P&L — fee39.

``org_id`` comes from ``routers.entities.get_org_id`` (the caller's own verified
session context) on every route and is NEVER accepted from a request body or a
query parameter. Every model here sets ``extra='forbid'`` and declares no
``org_id`` field, matching fee34's router.

This router is READ-ONLY. There is no write endpoint at all, because there is
no such thing as editing a P&L: revenue arrives from ``fee_run_lines`` when a
run posts, costs arrive from fee37's cost engine, and a screen that let anyone
adjust either would be the end of the numbers meaning anything. The permission
envelope still publishes ``can_write`` — as ``False``, always, with empty
``editable``/``inline_editable`` lists — because the frontend contract is that
the envelope is present and complete, not that it is present only when
interesting.

Reads require ``view_portfolio``, the same read permission fee34 uses. No new
permission name is invented here.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from routers.entities import get_org_id
from services.database import get_pool
from services.fee_schedules import READ_PERMISSION, WRITE_PERMISSION
from services.permissions import get_user_id
from services.profitability import (
    CUT_KINDS,
    PNL_COST_LINES,
    PNL_LINE_ORDER,
    PRODUCT_TYPE_TO_REVENUE_TYPE,
    RANK_KEYS,
    RANK_NET_PROFIT,
    COST_BANDS,
    Cut,
    InvalidCutError,
    ProfitabilityError,
    households_by_margin,
    profit_and_loss,
)
from services.rbac import has_permission, is_super_admin, load_principal, require_permission

router = APIRouter(prefix="/profitability", tags=["profitability"])


async def _gate(request: Request) -> tuple[str, str, Any]:
    """``(org_id, user_id, pool)`` with the read permission enforced.

    ``rbac.require_permission`` raises 403 naming the permission and checks
    Super Admin FIRST inside that one shared helper.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)
    return org_id, user_id, pool


async def _envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    """The permission envelope. ``can_write`` is always False — see the module
    docstring; nothing on this surface is writable by anyone, super admin
    included, because there is no write endpoint to be entitled to."""
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    # Resolved even though it cannot grant anything here, so the envelope
    # reports the caller's real standing rather than a hardcoded shape.
    holds_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    return {
        "can_read": True,
        "can_write": False,
        "holds_write_permission": bool(holds_write),
        "is_super_admin": bool(is_super_admin(principal)),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies() -> dict[str, Any]:
    """Every label and ordering the screen needs, from the server. Rule 1.

    ``line_order`` in particular: the P&L's line sequence is a decision, and a
    frontend that hardcoded it would hold a second copy of that decision, free
    to drift from the one the service applies.
    """
    return {
        "line_order": [
            {"key": key, "label": label, "is_cost": key in PNL_COST_LINES}
            for key, label in PNL_LINE_ORDER
        ],
        "cut_kinds": list(CUT_KINDS),
        "rank_keys": list(RANK_KEYS),
        "cost_bands": {band: list(types) for band, types in COST_BANDS.items()},
        "product_type_revenue_type": dict(PRODUCT_TYPE_TO_REVENUE_TYPE),
        "editable": [],
        "inline_editable": [],
    }


def _build_cut(
    kind: str,
    account_id: str | None,
    account_ids: list[str] | None,
    household_id: str | None,
    household_ids: list[str] | None,
    billing_group_id: str | None,
    advisor_id: str | None,
    product_type: str | None,
) -> Cut:
    """Turn query parameters into a validated :class:`Cut`.

    Each cut reads only its own parameters. A caller that sends
    ``kind=ACCOUNT&household_id=…`` gets the account cut and the household id
    is ignored rather than silently ANDed in — one cut at a time is the whole
    contract, and quietly honouring a second filter would produce a number
    nobody asked for under a label that says otherwise.
    """
    try:
        if kind == "ACCOUNT":
            return Cut.account(account_id or "")
        if kind == "ACCOUNTS":
            return Cut.accounts(account_ids or [])
        if kind == "HOUSEHOLD":
            return Cut.household(household_id or "")
        if kind == "HOUSEHOLDS":
            return Cut.households(household_ids or [])
        if kind == "BILLING_GROUP":
            return Cut.billing_group(billing_group_id or "")
        if kind == "ADVISOR":
            return Cut.advisor(advisor_id or "")
        if kind == "PRODUCT_TYPE":
            return Cut.product_type(product_type or "")
        return Cut.firm()
    except InvalidCutError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": exc.code, "message": str(exc), **exc.context},
        ) from exc


@router.get("/pnl")
async def get_pnl(
    request: Request,
    kind: Literal[
        "ACCOUNT", "ACCOUNTS", "HOUSEHOLD", "HOUSEHOLDS",
        "BILLING_GROUP", "ADVISOR", "PRODUCT_TYPE", "FIRM",
    ] = Query("FIRM"),
    account_id: str | None = Query(None),
    account_ids: list[str] | None = Query(None),
    household_id: str | None = Query(None),
    household_ids: list[str] | None = Query(None),
    billing_group_id: str | None = Query(None),
    advisor_id: str | None = Query(None),
    product_type: str | None = Query(None),
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
) -> dict[str, Any]:
    """The standard seven-line P&L for one of the eight cuts."""
    org_id, user_id, pool = await _gate(request)
    cut = _build_cut(
        kind, account_id, account_ids, household_id, household_ids,
        billing_group_id, advisor_id, product_type,
    )
    try:
        async with pool.acquire() as conn:
            pnl = await profit_and_loss(
                conn, org_id, cut,
                period_start=period_start, period_end=period_end,
            )
    except ProfitabilityError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": exc.code, "message": str(exc), **exc.context},
        ) from exc
    return {
        "pnl": pnl.as_dict(),
        "permissions": await _envelope(pool, user_id, org_id),
        "vocabularies": _vocabularies(),
    }


@router.get("/households-by-margin")
async def get_households_by_margin(
    request: Request,
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
    rank_by: Literal["net_profit", "margin_pct"] = Query(RANK_NET_PROFIT),
    limit: int | None = Query(None, ge=1, le=500),
    include_unhoused: bool = Query(False),
) -> dict[str, Any]:
    """Households ranked worst margin first — the metric that changes behaviour.

    Not "top clients". The list is deliberately ordered so the conversation
    starts with the relationships that are not paying for themselves.
    """
    org_id, user_id, pool = await _gate(request)
    async with pool.acquire() as conn:
        ranked = await households_by_margin(
            conn, org_id,
            period_start=period_start, period_end=period_end,
            rank_by=rank_by, limit=limit, include_unhoused=include_unhoused,
        )
    return {
        "rows": [
            {
                "household_id": h.household_id,
                "household_name": h.household_name,
                "margin_pct": h.margin_pct,
                "lines": h.lines(),
                **{key: getattr(h, key) for key, _ in PNL_LINE_ORDER},
            }
            for h in ranked
        ],
        "rank_by": rank_by,
        "permissions": await _envelope(pool, user_id, org_id),
        "vocabularies": _vocabularies(),
    }
