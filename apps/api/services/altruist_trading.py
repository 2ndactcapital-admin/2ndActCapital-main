"""cpsim03 — the Altruist-compatible ORDERS client (docs/cpsim/ALTRUIST_CONTRACT.md §8 — every field ASSUMED).

Altruist's real trading API has never been seen: there is no documented orders contract at all. So this client talks
ONLY to the ``simulator`` environment (cpsim02's third environment); asked for sandbox or production it refuses before
any request ("no published Altruist orders contract — the real Altruist route stays manual"). The real Altruist trade
route is unchanged: ``custodian_capabilities`` altruist.equity_trading_api is 'unknown' and the adapter routes manual.

Same conventions as the cpsim02 read client (services/altruist_oauth.py ``_call`` / ``fetch_resource``):
  * hosts resolved PER CALL for the org (``resolve_hosts`` — the allowed flag + counterparty re-checked every call) and
    ``check_destination`` (simulator credentials only to a simulator host);
  * ``Authorization: Bearer <access token>`` from ``require_active_connection`` (on-demand refresh) +
    ``Accept: application/json``; a JSON body on POST;
  * 15 s timeout; an optional ``transport`` on EVERY call, resolved through ``_transport_for`` exactly like ``_call``
    (the given transport, else the injected simulator transport — tests drive the simulator in-process);
  * any HTTP status ≥ 400 or a transport failure → ``AltruistOAuthError``; a refusal the caller must explain (a 409 on
    cancel, a 422 on a malformed order) is the subclass :class:`AltruistOrderRefused` carrying the status and the
    counterparty's own ``detail``.

THE CUSTODY CLIFF. This module sends exactly what its caller passes; it decides nothing. Its only caller is
services/custodian_execution.py, whose send / import / cancel are PERSON actions (manage_execution, a human actor),
never registered as an unattended verb, a workflow step or a trigger.
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

ORDERS_PATH = "/v2/orders"
TIMEOUT_S = 15.0
NO_CONTRACT = ("no published Altruist orders contract — the real Altruist route stays manual (orders are sent only to "
               "a simulated custodian)")


class AltruistOrderRefused(AltruistOAuthError):
    """The counterparty answered ≥ 400 with a reason the person should see (409 on cancel, 422 / 404 on an order)."""

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
        raise AltruistOrderRefused(f"{method} {url} was refused: HTTP {response.status_code}",
                                   status=response.status_code, detail=_detail(response))
    body = response.json()
    return response.status_code, body if isinstance(body, dict) else {}


# ── the four calls, given hosts + token (the conformance suite drives these directly) ──────────────────────────────
async def post_order(*, api_base: str, access_token: str, order: dict, timeout: float = TIMEOUT_S,
                     transport: Any = None) -> tuple[int, dict]:
    """POST /v2/orders → (HTTP status, the order). 201 a new order, 200 the same order on a repeat client_order_id."""
    status, body = await _request("POST", f"{api_base}{ORDERS_PATH}", access_token=access_token, json_body=order,
                                  timeout=timeout, transport=transport)
    return status, body.get("order") or {}


async def get_order(*, api_base: str, access_token: str, order_id: str, timeout: float = TIMEOUT_S,
                    transport: Any = None) -> dict:
    _, body = await _request("GET", f"{api_base}{ORDERS_PATH}/{order_id}", access_token=access_token,
                             timeout=timeout, transport=transport)
    return body.get("order") or {}


async def list_orders(*, api_base: str, access_token: str, account_id: str, timeout: float = TIMEOUT_S,
                      transport: Any = None) -> list[dict]:
    _, body = await _request("GET", f"{api_base}{ORDERS_PATH}", access_token=access_token,
                             params={"account_id": account_id}, timeout=timeout, transport=transport)
    rows = body.get("orders")
    return rows if isinstance(rows, list) else []


async def delete_order(*, api_base: str, access_token: str, order_id: str, timeout: float = TIMEOUT_S,
                       transport: Any = None) -> dict:
    _, body = await _request("DELETE", f"{api_base}{ORDERS_PATH}/{order_id}", access_token=access_token,
                             timeout=timeout, transport=transport)
    return body.get("order") or {}


# ── the org-scoped calls (what services/custodian_execution.py uses) ───────────────────────────────────────────────
async def _session(conn, *, org_id: str, environment: str) -> tuple[str, str]:
    if environment != SIMULATOR:
        raise AltruistOAuthError(NO_CONTRACT)
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    hosts = await resolve_hosts(conn, org_id=org_id, environment=environment)
    check_destination(environment, hosts)
    return hosts["api_base"], token


async def order_session(conn, *, org_id: str, environment: str = SIMULATOR) -> dict:
    """fix 1 — what an orders call needs (api base + access token), read on ``conn``, so the caller can RELEASE its
    connection (and the transaction the RLS pool opened on it) BEFORE the HTTP call. Same refusals as every call."""
    api_base, token = await _session(conn, org_id=org_id, environment=environment)
    return {"api_base": api_base, "access_token": token, "environment": environment}


async def submit_with_session(session: dict, *, order: dict, timeout: float = TIMEOUT_S,
                              transport: Any = None) -> tuple[int, dict]:
    """POST /v2/orders with a session from :func:`order_session` — no database connection held."""
    env = session["environment"]
    return await post_order(api_base=session["api_base"], access_token=session["access_token"], order=order,
                            timeout=timeout, transport=_transport_for(env, transport))


async def submit_order(conn, *, org_id: str, order: dict, environment: str = SIMULATOR, timeout: float = TIMEOUT_S,
                       transport: Any = None) -> tuple[int, dict]:
    session = await order_session(conn, org_id=org_id, environment=environment)
    return await submit_with_session(session, order=order, timeout=timeout, transport=transport)


async def fetch_order(conn, *, org_id: str, order_id: str, environment: str = SIMULATOR, timeout: float = TIMEOUT_S,
                      transport: Any = None) -> dict:
    api_base, token = await _session(conn, org_id=org_id, environment=environment)
    return await get_order(api_base=api_base, access_token=token, order_id=order_id, timeout=timeout,
                           transport=_transport_for(environment, transport))


async def fetch_orders(conn, *, org_id: str, account_id: str, environment: str = SIMULATOR,
                       timeout: float = TIMEOUT_S, transport: Any = None) -> list[dict]:
    api_base, token = await _session(conn, org_id=org_id, environment=environment)
    return await list_orders(api_base=api_base, access_token=token, account_id=account_id, timeout=timeout,
                             transport=_transport_for(environment, transport))


async def cancel_order(conn, *, org_id: str, order_id: str, environment: str = SIMULATOR, timeout: float = TIMEOUT_S,
                       transport: Any = None) -> dict:
    api_base, token = await _session(conn, org_id=org_id, environment=environment)
    return await delete_order(api_base=api_base, access_token=token, order_id=order_id, timeout=timeout,
                              transport=_transport_for(environment, transport))
