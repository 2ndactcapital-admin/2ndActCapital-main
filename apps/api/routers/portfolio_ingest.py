"""Portfolio ingestion endpoints — Phase B, plus Phase C's rollup trigger.

Minimal by mandate: one file-upload endpoint, one precedence read, one honest
Altruist status probe, and (Phase C) one endpoint that rebuilds
``entity_holdings`` from the positions those endpoints wrote. No reconciliation
screen — that is later.

``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
route and is never accepted from a request body or read out of an uploaded
file. A tenant id supplied by an uploader would be a cross-tenant write anyone
with an upload form could reach.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import get_user_id
from services.portfolio_altruist import ALTRUIST_ENV_VARS, probe
from services.portfolio_assets import WRITE_PERMISSION
from services.portfolio_import import (
    ImportError_,
    import_positions_file,
)
from services.portfolio_precedence import (
    PRECEDENCE_SETTING_KEY,
    get_source_order,
    resolve_holding,
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
    file: UploadFile = File(...),
):
    """Import a reporting-tool holdings export (CSV or XLSX).

    ``as_of_date`` overrides the file's own date column and is required only
    when the file has none. Rows that cannot be understood are skipped and
    returned in ``errors`` — a single malformed line does not fail the import,
    and the response is a 201 describing what happened rather than a 400 that
    throws away four hundred good rows.
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
            )
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
    }


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
