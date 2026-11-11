"""cpsim04 — the Altruist-compatible TRANSFERS client (docs/cpsim/ALTRUIST_CONTRACT.md §9 — every field ASSUMED).

Altruist's real money-movement API has never been seen: there is no documented transfers contract at all. So this
client talks ONLY to the ``simulator`` environment (cpsim02's third environment); asked for sandbox or production it
refuses before any request ("no published Altruist money-movement contract — the real Altruist route stays manual").
The real Altruist payment route is unchanged: ``custodian_capabilities`` altruist ach / wire / journal 'unknown',
payment_status_feed 'no'; the manual payment sheet.

The cpsim03 orders client's conventions, call for call (services/altruist_trading.py):
  * hosts resolved PER CALL for the org (``resolve_hosts`` — the allowed flag + counterparty re-checked every call) and
    ``check_destination`` (simulator credentials only to a simulator host);
  * ``Authorization: Bearer <access token>`` from ``require_active_connection`` + ``Accept: application/json``; a JSON
    body on POST;
  * 15 s timeout; an optional ``transport`` on EVERY call, resolved through ``_transport_for`` (the given transport,
    else the injected simulator transport — tests drive the simulator in-process);
  * any HTTP status ≥ 400 or a transport failure → ``AltruistOAuthError``; a refusal the person must see is the
    subclass :class:`AltruistTransferRefused` (status + the counterparty's own ``detail``);
  * :func:`transfer_session` reads what a call needs on the caller's connection so the caller can RELEASE it (and the
    transaction the RLS pool opened on it) BEFORE the HTTP call (cpsim03 fix 1).

THE CUSTODY CLIFF. This module sends exactly what its caller passes; it decides nothing. Its only caller is
services/payment_custodian.py, whose Send / Refresh are PERSON actions (initiate_payment / approve_payment, a human
actor), never registered as an unattended verb, a workflow step or a trigger.
"""

from __future__ import annotations

from typing import Any

import httpx

from services.altruist_oauth import (
    SIMULATOR,
    AltruistOAuthError,
    _transport_for,
    check_destination,
    get_decrypted_access_token,
    require_active_connection,
    resolve_hosts,
)

TRANSFERS_PATH = "/v2/transfers"
TIMEOUT_S = 15.0
NO_CONTRACT = ("no published Altruist money-movement contract — the real Altruist route stays manual (transfers are "
               "sent only to a simulated custodian)")


class AltruistTransferRefused(AltruistOAuthError):
    """The counterparty answered ≥ 400 with a reason the person should see (422 malformed, 404, 409 on cancel)."""

    def __init__(self, message: str, *, status: int, detail: str | None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def _headers(access_token: str, *, body: bool = False) -> dict:
    h = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if body:
        h["Content-Type"] = "application/json"
    return h


def _detail(response) -> str | None:
    try:
        d = response.json()
    except ValueError:
        return None
    return str(d.get("detail"))[:500] if isinstance(d, dict) and d.get("detail") else None


async def _request(method: str, url: str, *, access_token: str, json_body: dict | None = None,
                   params: dict | None = None, timeout: float = TIMEOUT_S, transport: Any = None) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            response = await client.request(method, url, headers=_headers(access_token, body=json_body is not None),
                                            json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(f"{method} {url} failed at the transport layer: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise AltruistTransferRefused(f"{method} {url} was refused: HTTP {response.status_code}",
                                      status=response.status_code, detail=_detail(response))
    body = response.json()
    return response.status_code, body if isinstance(body, dict) else {}


# ── the four calls, given hosts + token (the conformance suite drives these directly) ──────────────────────────────
async def post_transfer(*, api_base: str, access_token: str, transfer: dict, timeout: float = TIMEOUT_S,
                        transport: Any = None) -> tuple[int, dict]:
    """POST /v2/transfers → (HTTP status, the transfer). 201 new, 200 the same transfer on a repeat client id."""
    status, body = await _request("POST", f"{api_base}{TRANSFERS_PATH}", access_token=access_token,
                                  json_body=transfer, timeout=timeout, transport=transport)
    return status, body.get("transfer") or {}


async def get_transfer(*, api_base: str, access_token: str, transfer_id: str, timeout: float = TIMEOUT_S,
                       transport: Any = None) -> dict:
    _, body = await _request("GET", f"{api_base}{TRANSFERS_PATH}/{transfer_id}", access_token=access_token,
                             timeout=timeout, transport=transport)
    return body.get("transfer") or {}


async def list_transfers(*, api_base: str, access_token: str, account_id: str, timeout: float = TIMEOUT_S,
                         transport: Any = None) -> list[dict]:
    _, body = await _request("GET", f"{api_base}{TRANSFERS_PATH}", access_token=access_token,
                             params={"account_id": account_id}, timeout=timeout, transport=transport)
    rows = body.get("transfers")
    return rows if isinstance(rows, list) else []


async def delete_transfer(*, api_base: str, access_token: str, transfer_id: str, timeout: float = TIMEOUT_S,
                          transport: Any = None) -> dict:
    _, body = await _request("DELETE", f"{api_base}{TRANSFERS_PATH}/{transfer_id}", access_token=access_token,
                             timeout=timeout, transport=transport)
    return body.get("transfer") or {}


# ── the org-scoped calls (what services/payment_custodian.py uses) ─────────────────────────────────────────────────
async def transfer_session(conn, *, org_id: str, environment: str = SIMULATOR) -> dict:
    """What a transfers call needs (api base + access token), read on ``conn`` — the caller then RELEASES its
    connection before the HTTP call. Refuses any environment but the simulator before anything is read."""
    if environment != SIMULATOR:
        raise AltruistOAuthError(NO_CONTRACT)
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    hosts = await resolve_hosts(conn, org_id=org_id, environment=environment)
    check_destination(environment, hosts)
    return {"api_base": hosts["api_base"], "access_token": token, "environment": environment}


async def submit_with_session(session: dict, *, transfer: dict, timeout: float = TIMEOUT_S,
                              transport: Any = None) -> tuple[int, dict]:
    env = session["environment"]
    return await post_transfer(api_base=session["api_base"], access_token=session["access_token"], transfer=transfer,
                               timeout=timeout, transport=_transport_for(env, transport))


async def fetch_with_session(session: dict, *, transfer_id: str, timeout: float = TIMEOUT_S,
                             transport: Any = None) -> dict:
    env = session["environment"]
    return await get_transfer(api_base=session["api_base"], access_token=session["access_token"],
                              transfer_id=transfer_id, timeout=timeout, transport=_transport_for(env, transport))


async def fetch_transfer(conn, *, org_id: str, transfer_id: str, environment: str = SIMULATOR,
                         timeout: float = TIMEOUT_S, transport: Any = None) -> dict:
    session = await transfer_session(conn, org_id=org_id, environment=environment)
    return await fetch_with_session(session, transfer_id=transfer_id, timeout=timeout, transport=transport)
