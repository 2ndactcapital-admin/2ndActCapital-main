"""Altruist connection lifecycle — connect / callback / status / disconnect.

Altruist Sprint 4. Sprints 1-3 built the full engine
(``services/altruist_oauth.py``, ``services/altruist_identity.py``,
``services/altruist_positions_sync.py``) as service-layer Python only, called
directly by verify scripts — nothing was reachable over HTTP. This router
exposes exactly the connection lifecycle (not identity resolution or
positions/transactions sync, which stay internal for now): an admin/staff
caller can start the OAuth flow, complete it via the callback Altruist
redirects to, check status, and disconnect.

``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims via
``request.state.user``) on every route — never the request body or a path
parameter, matching ``custody_import.py``'s own rule for the same reason.
Every request body below is ``extra='forbid'`` so a body that even declares
an ``org_id`` field fails validation mechanically rather than by review habit.

Permission check mirrors ``custody_import.py`` exactly: ``services.
permissions.get_user_id`` (claims-derived, no DB round trip) feeds
``services.rbac.require_permission``/``has_permission`` (the database-backed
RBAC, with the shared ``is_super_admin`` bypass checked first inside
``rbac.has_permission`` itself) — not the ``services.users.ensure_user`` +
``admin.py``-style DB-lookup chain, since this is a custodian-connection
feature, the same shape as custody import, not a user-management one.
Two new permissions were added to the existing ``permissions`` catalog (data
only, no DDL — the catalog already grows incrementally per resource, e.g.
``spv``, ``workflows``): ``view_custody_connections`` / ``manage_custody_
connections``.
"""

from __future__ import annotations

import traceback

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

import services.altruist_oauth as altruist_oauth
from routers.entities import get_org_id
from services.altruist_identity import resolve_identity
from services.altruist_oauth import ENVIRONMENTS, AltruistConfigError, AltruistNotConnected, AltruistOAuthError
from services.altruist_positions_sync import sync_resolved_accounts
from services.database import get_pool
from services.permissions import get_user_id
from services.rbac import require_permission

router = APIRouter(tags=["altruist-connection"])

READ_PERMISSION = "view_custody_connections"
WRITE_PERMISSION = "manage_custody_connections"


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "sandbox"


class DisconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "sandbox"


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "sandbox"


def _validate_environment(environment: str) -> str:
    if environment not in ENVIRONMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown environment {environment!r}; expected one of {ENVIRONMENTS}",
        )
    return environment


async def _require(request: Request, permission: str) -> tuple[str, str]:
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    await require_permission(pool, user_id, org_id, permission)
    return user_id, org_id


def _status_payload(connection: dict | None, *, environment: str) -> dict:
    """The safe, public shape of a connection row — NEVER a raw token value.

    Only the columns ``get_active_connection`` already selects (it excludes
    every ``*_encrypted`` column at the query level) plus masked ``*_last4``
    reference markers — the same redaction convention this module already
    uses for logs (``altruist_oauth.redact``/``last4``).
    """
    if connection is None:
        return {
            "connected": False,
            "environment": environment,
            "status": None,
            "token_expires_at": None,
            "last_refreshed_at": None,
            "scope": None,
        }
    return {
        "connected": connection["status"] == "connected",
        "environment": connection["environment"],
        "status": connection["status"],
        "token_expires_at": connection["token_expires_at"],
        "last_refreshed_at": connection["last_refreshed_at"],
        "scope": connection["scope"],
        "client_id": connection["client_id"],
        "access_token_last4": connection["access_token_last4"],
        "refresh_token_last4": connection["refresh_token_last4"],
    }


def _sync_summary(environment: str, identity_result: dict, sync_result) -> dict:
    """Real counts pulled from what resolve_identity/sync_resolved_accounts
    actually reported — never guessed. ``sync_result`` is a
    ``altruist_positions_sync.SyncResult`` dataclass; per-account counters
    are summed across every account it synced this run.
    """
    return {
        "environment": environment,
        "households_seen": identity_result["households_seen"],
        "accounts_resolved": len(identity_result["resolved"]),
        "accounts_unmatched": len(identity_result["exceptions"]),
        "accounts_synced": sync_result.accounts_synced,
        "positions_created": sum(a.positions_created for a in sync_result.accounts),
        "positions_skipped_duplicate": sum(a.positions_skipped_duplicate for a in sync_result.accounts),
        "transactions_created": sum(a.transactions_created for a in sync_result.accounts),
        "transactions_skipped_duplicate": sum(a.transactions_skipped_duplicate for a in sync_result.accounts),
    }


async def _run_sync(conn, *, org_id: str, environment: str) -> dict:
    """The Sprint 5 entrypoint: resolve_identity then sync_resolved_accounts,
    end to end, for one org+environment. Shared by the HTTP endpoint below
    and the auto-trigger-on-connect background task, so both go through the
    identical code path. ``require_active_connection`` is called explicitly
    first so an org with no active connection fails fast, before either
    Sprint 2 or Sprint 3's own (redundant but harmless) internal guard would
    otherwise run — see altruist_positions_sync.sync_resolved_accounts'
    module note on why it cannot rely on being reached via resolve_identity.
    """
    await altruist_oauth.require_active_connection(conn, org_id=org_id, environment=environment)
    identity_result = await resolve_identity(conn, org_id=org_id, environment=environment)
    sync_result = await sync_resolved_accounts(conn, org_id=org_id, environment=environment)
    return _sync_summary(environment, identity_result, sync_result)


async def _run_auto_sync(org_id: str, environment: str) -> None:
    """Background task fired right after a new connection is stored (see the
    callback route below). Matches the established BackgroundTasks pattern in
    routers/entities.py and routers/investment_profile.py: acquires its own
    pool connection (the request's own connection is already released by the
    time this runs) and never raises — a sync failure must not be visible to,
    or retried by, the caller who merely connected.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _run_sync(conn, org_id=org_id, environment=environment)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"ERROR in altruist auto-sync (org_id={org_id}, environment={environment}): {exc}")
        print(traceback.format_exc())


@router.post("/altruist/connect")
async def altruist_connect(request: Request, body: ConnectRequest):
    """Start the OAuth flow: create a state row, return the authorize URL.

    The frontend redirects the user's browser to ``authorize_url`` — Altruist
    redirects back to the callback route below with ``code``/``state``.
    """
    environment = _validate_environment(body.environment)
    user_id, org_id = await _require(request, WRITE_PERMISSION)

    pool = await get_pool()
    async with pool.acquire() as conn:
        state = await altruist_oauth.create_oauth_state(conn, org_id=org_id, environment=environment, user_id=user_id)

    try:
        authorize_url = altruist_oauth.build_authorize_url(environment=environment, state=state)
    except AltruistConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"authorize_url": authorize_url, "environment": environment}


@router.get("/altruist/callback")
async def altruist_callback(
    request: Request, background_tasks: BackgroundTasks, code: str = Query(...), state: str = Query(...)
):
    """The route Altruist redirects back to after the user authorizes.

    Consumes the state (single-use, org-scoped — rejects missing/expired/
    used/cross-org, all as the same ``AltruistOAuthError`` so a CSRF check
    never leaks which reason it failed for), exchanges the code for tokens,
    and persists the new connection.

    Once the connection is stored, identity resolution + positions/
    transactions sync fire automatically in the background (Sprint 5) — a
    newly-connected org is not left empty until someone remembers to trigger
    a manual sync. Scheduled after the ``async with pool.acquire()`` block
    below has already released its connection back to the pool, so the
    background task's own ``pool.acquire()`` never contends with it. A sync
    failure here is swallowed inside ``_run_auto_sync`` and never affects
    this response — the connection is already correctly stored by that point
    regardless of how the sync goes.
    """
    user_id, org_id = await _require(request, WRITE_PERMISSION)

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            consumed = await altruist_oauth.consume_oauth_state(conn, org_id=org_id, state=state)
        except AltruistOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        environment = consumed["environment"]

        try:
            tokens = await altruist_oauth.exchange_code_for_tokens(environment=environment, code=code)
        except AltruistConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except AltruistOAuthError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        client_id, client_secret = altruist_oauth.client_credentials(environment)
        connection_id = await altruist_oauth.store_new_connection(
            conn,
            org_id=org_id,
            environment=environment,
            client_id=client_id,
            client_secret=client_secret,
            tokens=tokens,
            connected_by=user_id,
        )

    background_tasks.add_task(_run_auto_sync, org_id, environment)
    return {"connected": True, "environment": environment, "connection_id": connection_id}


@router.get("/altruist/status")
async def altruist_status(request: Request, environment: str = Query("sandbox")):
    """Whether the caller's org has an active Altruist connection.

    Opportunistically refreshes an expiring-soon token before responding (see
    ``ensure_fresh_token`` — the interim, on-demand refresh mechanism; no
    generic recurring-job scheduler exists in this app yet to run this on a
    timer, see ``services/altruist_oauth.py`` module notes). Never returns a
    raw token value in any field.
    """
    environment = _validate_environment(environment)
    _user_id, org_id = await _require(request, READ_PERMISSION)

    pool = await get_pool()
    async with pool.acquire() as conn:
        connection = await altruist_oauth.get_active_connection(conn, org_id=org_id, environment=environment)
        if connection is not None and connection["status"] == "connected":
            try:
                connection = await altruist_oauth.ensure_fresh_token(
                    conn, org_id=org_id, environment=environment, connection=connection
                )
            except (AltruistOAuthError, AltruistConfigError):
                # A failed just-in-time refresh must not turn a status check
                # into a 500 — the connection row itself still reflects the
                # true, current (if stale) state.
                connection = await altruist_oauth.get_active_connection(conn, org_id=org_id, environment=environment)

    return _status_payload(connection, environment=environment)


@router.post("/altruist/disconnect")
async def altruist_disconnect(request: Request, body: DisconnectRequest):
    """Close the caller's org's active connection. Bitemporal close, never a hard delete."""
    environment = _validate_environment(body.environment)
    _user_id, org_id = await _require(request, WRITE_PERMISSION)

    pool = await get_pool()
    async with pool.acquire() as conn:
        closed = await altruist_oauth.close_connection(conn, org_id=org_id, environment=environment)

    return {"disconnected": closed, "environment": environment}


@router.post("/altruist/sync")
async def altruist_sync(request: Request, body: SyncRequest):
    """Admin-triggered "sync now": resolve_identity then sync_resolved_accounts
    end to end for the caller's org (Sprint 5). Reuses ``manage_custody_
    connections`` — the same permission connect/disconnect already require —
    since triggering a sync is an operational action on the same custody-
    connection resource, not a distinct capability (see this app's existing
    2-tier view/manage convention per resource; ``workflows`` is the only
    resource with a finer split, and that's for genuinely distinct
    capabilities this is not).

    Idempotent regardless of how many times it's called — proven at the
    function level in Sprint 3, re-proven here at the HTTP layer. Refuses
    cleanly (409, never a raw 500) when the org has no active connection —
    ``_run_sync`` calls ``require_active_connection`` before any network call.
    """
    environment = _validate_environment(body.environment)
    _user_id, org_id = await _require(request, WRITE_PERMISSION)

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            summary = await _run_sync(conn, org_id=org_id, environment=environment)
        except AltruistNotConnected as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return summary
