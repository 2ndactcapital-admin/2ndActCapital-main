"""Portfolio ingestion endpoints — Phase B, plus Phase C's rollup trigger and
Phase E's tax-document chase list.

Minimal by mandate: one file-upload endpoint, one precedence read, one honest
Altruist status probe, (Phase C) one endpoint that rebuilds ``entity_holdings``
from the positions those endpoints wrote, and (Phase E) one read that answers
"who is missing a K-1". No reconciliation screen — that is later.

``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
route and is never accepted from a request body or read out of an uploaded
file. A tenant id supplied by an uploader would be a cross-tenant write anyone
with an upload form could reach.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import get_user_id
from services.portfolio_altruist import ALTRUIST_ENV_VARS, probe
from services.portfolio_assets import READ_PERMISSION, WRITE_PERMISSION
from services.portfolio_commitments import (
    CommitmentError,
    tax_chase_list,
    to_json,
)
from services.portfolio_import import (
    ImportError_,
    import_positions_file,
)
from services.portfolio_account_link import (
    AccountLinkError,
    list_account_link_exceptions,
    review_account_link_exception,
)
from services.portfolio_precedence import (
    PRECEDENCE_SETTING_KEY,
    HouseholdOverrideError,
    PrecedenceConfigError,
    clear_household_source_order,
    get_household_override,
    get_source_order,
    resolve_holding,
    set_household_source_order,
)
from services.portfolio_rollup import (
    ROLLUP_PERMISSION,
    RollupError,
    rollup_entity_holdings,
)
from services.rbac import require_permission

router = APIRouter(tags=["portfolio-ingest"])


@router.post("/portfolio/import/positions", status_code=201)
async def import_positions(
    request: Request,
    owner_entity_id: _uuid.UUID = Form(...),
    as_of_date: date | None = Form(default=None),
    account_id: _uuid.UUID | None = Form(default=None),
    file: UploadFile = File(...),
):
    """Import a reporting-tool holdings export (CSV or XLSX).

    ``as_of_date`` overrides the file's own date column and is required only
    when the file has none. Rows that cannot be understood are skipped and
    returned in ``errors`` — a single malformed line does not fail the import,
    and the response is a 201 describing what happened rather than a 400 that
    throws away four hundred good rows.

    ``account_id`` (fee32) is OPTIONAL and links every position this file writes
    to one custodial account. Omitted — the default, and what every caller
    before this sprint sends — the positions carry a NULL account, which is the
    correct state for a directly-held or SPV export. Supplied but belonging to
    another org: 400, before a single row is written. Supplied but naming an
    account the owner entity does not own: the import PROCEEDS and each position
    raises a reviewable exception readable at
    ``GET /portfolio/position-account-exceptions``.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    contents = await file.read()
    async with pool.acquire() as conn:
        try:
            result = await import_positions_file(
                conn,
                org_id=org_id,
                file_bytes=contents,
                filename=file.filename,
                owner_entity_id=str(owner_entity_id),
                as_of_date=as_of_date,
                account_id=str(account_id) if account_id else None,
            )
        except AccountLinkError as exc:
            # A cross-tenant / closed account, caught before any row was
            # written. 400 rather than 403: the caller named an account that is
            # not theirs to name, and the detail says which check refused it
            # without disclosing anything about the other tenant's account.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ImportError_ as exc:
            # The FILE was unusable — distinct from a bad row, and the only
            # case that is a 400.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "source_system": result.source_system,
        "total_rows": result.total_rows,
        "imported": result.imported,
        "skipped_duplicate": result.skipped_duplicate,
        "assets_created": result.assets_created,
        "assets_matched": result.assets_matched,
        "resolved_holdings": result.resolved_holdings,
        "position_ids": result.positions,
        "errors": [
            {"line": e.line, "reason": e.reason, "raw": e.raw} for e in result.errors
        ],
        "header_mapping": result.header_mapping,
    }


@router.get("/portfolio/precedence")
async def read_precedence(request: Request):
    """The org's source-precedence order, and whether it is their own.

    ``is_default`` is reported rather than inferred by the client: an org that
    saved an order identical to the platform default has still configured it.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        order = await get_source_order(conn, org_id)
    return {
        "setting_key": PRECEDENCE_SETTING_KEY,
        "order": list(order.order),
        "is_default": order.is_default,
        "invalid_reason": order.invalid_reason,
    }


@router.post("/portfolio/precedence/resolve")
async def resolve_precedence_endpoint(
    request: Request,
    owner_entity_id: _uuid.UUID = Form(...),
    asset_id: _uuid.UUID = Form(...),
    as_of_date: date = Form(...),
):
    """Re-resolve one holding key. Marks losers, clears a stale winner.

    Exposed because precedence has to be re-runnable independently of an
    import: an org that re-orders its sources needs the existing rows re-marked,
    and nothing else in the system would ever trigger that.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        outcome = await resolve_holding(
            conn, org_id,
            owner_entity_id=str(owner_entity_id),
            asset_id=str(asset_id),
            as_of_date=as_of_date,
        )
    if outcome is None:
        raise HTTPException(
            status_code=404,
            detail="no current positions for that owner / asset / as-of date",
        )
    return {
        "winner_position_id": outcome.winner_position_id,
        "winner_source_system": outcome.winner_source_system,
        "losers": [
            {
                "position_id": c.position_id,
                "source_system": c.source_system,
                "superseded_by_source": c.superseded_by_source,
            }
            for c in outcome.losers
        ],
        "order": list(outcome.order),
        "order_is_default": outcome.order_is_default,
        "rows_marked": outcome.rows_marked,
        "rows_cleared": outcome.rows_cleared,
        # fee32. WHICH of the three levels governed, not just what the order
        # was. "Addepar won" and "Addepar won because this household overrides
        # the firm's order" are different answers, and only the second one
        # tells an operator where to go to change it.
        "order_origin": outcome.order_origin,
        "household_id": outcome.household_id,
        "household_reason": outcome.household_reason,
    }


# ── fee32: household precedence overrides ───────────────────────────────────


@router.get("/portfolio/precedence/households/{household_id}")
async def read_household_precedence(request: Request, household_id: _uuid.UUID):
    """One household's active precedence override, or ``null``.

    ``null`` means the household resolves through the org's own order — which is
    reported alongside, so a screen can show what it WOULD fall back to without
    a second call. That difference is the whole decision an operator is making.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

    async with pool.acquire() as conn:
        override = await get_household_override(conn, org_id, str(household_id))
        org_order = await get_source_order(conn, org_id)
    return {
        "household_id": str(household_id),
        "override": override,
        "org_order": list(org_order.order),
        "org_order_is_default": org_order.is_default,
        "setting_key": PRECEDENCE_SETTING_KEY,
    }


@router.put("/portfolio/precedence/households/{household_id}")
async def write_household_precedence(
    request: Request,
    household_id: _uuid.UUID,
    source_order: list[str] = Form(...),
    reason: str = Form(...),
):
    """Create or replace a household's precedence override.

    ``approved_by`` is NOT accepted from the body — it is the caller's own
    verified user id. An approver a request could name is not an approver.

    The previous override, if any, is closed on the system axis and kept: a
    changed override is a new policy decision with its own approver, not a
    correction of the old one, and a reconciliation screen needs the old row to
    explain last quarter's winner.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        try:
            override_id = await set_household_source_order(
                conn, org_id,
                household_id=str(household_id),
                source_order=source_order,
                reason=reason,
                approved_by=user_id,
            )
        except (HouseholdOverrideError, PrecedenceConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        override = await get_household_override(conn, org_id, str(household_id))
    return {"override_id": override_id, "override": override}


@router.delete("/portfolio/precedence/households/{household_id}")
async def delete_household_precedence(request: Request, household_id: _uuid.UUID):
    """Retire a household's override. 404 if it had none.

    After this the household resolves exactly as a household that never had one
    — through the org setting, then the platform default.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        cleared = await clear_household_source_order(
            conn, org_id, household_id=str(household_id)
        )
    if not cleared:
        raise HTTPException(
            status_code=404,
            detail=f"household {household_id} has no active precedence override",
        )
    return {"household_id": str(household_id), "cleared": True}


# ── fee32: the position/account linkage review list ─────────────────────────


@router.get("/portfolio/position-account-exceptions")
async def read_position_account_exceptions(
    request: Request,
    include_reviewed: bool = Query(
        default=False,
        description="Include exceptions a reviewer has already closed.",
    ),
    position_id: _uuid.UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Positions written with an ``account_id`` their owner does not own.

    These rows are the point of the linkage check: the position WAS written, so
    nothing was lost, and it is listed here so the mismatch is reviewable rather
    than silently accepted. An empty list is the healthy state, not a missing
    feature.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

    async with pool.acquire() as conn:
        return await list_account_link_exceptions(
            conn, org_id,
            include_reviewed=include_reviewed,
            position_id=str(position_id) if position_id else None,
            limit=limit,
            offset=offset,
        )


@router.post("/portfolio/position-account-exceptions/{exception_id}/review")
async def review_position_account_exception(
    request: Request, exception_id: _uuid.UUID
):
    """Close one linkage exception. 404 if it was already closed or not this org's.

    Closing records that a human looked; it corrects nothing. The fix is an edit
    to the position's ``account_id`` or to the account's owners, and either of
    those re-raising the mismatch is a NEW finding the partial unique index
    deliberately allows to be recorded again.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        closed = await review_account_link_exception(
            conn, org_id, exception_id=str(exception_id), reviewed_by=user_id
        )
    if not closed:
        raise HTTPException(
            status_code=404,
            detail=(
                f"exception {exception_id} is not an open exception in this org"
            ),
        )
    return {"exception_id": str(exception_id), "reviewed": True}


@router.get("/portfolio/altruist/status")
async def altruist_status(request: Request):
    """The real state of the Altruist integration. Currently: blocked.

    A live probe rather than a stored flag. ``attempted`` distinguishes "no
    credentials, nothing tried" from "credentials present, real call refused" —
    a provisioning gap and a partner-access problem are different findings and
    go to different people.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    gate = await probe()
    return {
        "ok": gate.ok,
        "call_attempted": gate.attempted,
        "reason": gate.reason,
        "required_env_vars": list(ALTRUIST_ENV_VARS),
        "missing_env_vars": list(gate.missing_vars),
        "status_code": gate.status_code,
    }


# ── Phase C: the rollup trigger ─────────────────────────────────────────────


@router.post("/portfolio/rollup")
async def trigger_rollup(
    request: Request,
    as_of_date: date = Form(...),
):
    """Rebuild ``entity_holdings`` for the caller's org as of ``as_of_date``.

    This is the endpoint that finally puts data in front of the Sprint 21
    sunburst: ``services.allocation_lens`` reads ``entity_holdings`` and has had
    no writer since it shipped.

    ``as_of_date`` is REQUIRED rather than defaulted to today. A rollup labels
    every bucket it writes with that date and ``allocation_lens`` picks the
    latest bucket on or before the date it is asked about, so a mistaken
    default would stamp a quarter-end position set with today's date and shadow
    the real one. The caller knows which date they are closing; the server does
    not.

    Gated on ``manage_portfolio`` — Phase B's portfolio-write permission, via
    the same ``require_permission`` call every other write on this router uses.
    A rollup rewrites the numbers every member's allocation view is drawn from,
    which is a write however much it reads.

    Deliberately synchronous. The work is proportional to an org's position
    count, the caller wants to know it actually happened, and a 202 with no
    result would hand back "accepted" for a rollup that then found nothing to
    value.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, ROLLUP_PERMISSION)

    async with pool.acquire() as conn:
        try:
            result = await rollup_entity_holdings(
                conn, org_id=org_id, as_of_date=as_of_date
            )
        except RollupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result.as_dict()


# ── Phase E: the tax-document chase list ────────────────────────────────────


@router.get("/portfolio/tax-chase")
async def tax_chase(request: Request, tax_year: int = Query(...)):
    """Every commitment for ``tax_year`` still missing its tax document.

    The "who is missing a K-1" list. Three states, proven distinct in
    `verify_portfolioe.py`: `tax_doc_expected = false` never appears at any
    status; `tax_doc_status = 'received'` is off the list; everything else that
    expects a document is on it.

    ``tax_year`` is REQUIRED and not defaulted to the prior calendar year. It is
    the second key column of ``idx_commitments_tax_chase``, and — more to the
    point — a chase list is worked against a filing deadline the caller knows
    and the server does not. In January, "last year" is two different years to
    two different people in the same office.

    Gated on ``view_portfolio``, not ``manage_portfolio``: this reads and writes
    nothing. Which commitments are outstanding is exactly what an administrator
    chasing documents needs and is not a portfolio-management action.

    Monetary values are serialised as STRINGS by ``to_json``. A float here would
    be a rounding error introduced at the last layer, after the figures survived
    the database and the service as exact Decimals.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            rows = await tax_chase_list(conn, org_id=org_id, tax_year=tax_year)
        except CommitmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "tax_year": tax_year,
        "count": len(rows),
        "commitments": [to_json(r) for r in rows],
    }
