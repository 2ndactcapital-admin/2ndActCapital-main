"""cpsim02 D11 — the Altruist-compatible CONFORMANCE SUITE (core).

One suite, two targets: the simulator (now) and Altruist's real sandbox (the day access arrives — any difference found
then is a SIMULATOR defect). Every check cites the contract row it tests (docs/cpsim/ALTRUIST_CONTRACT.md §6 items 1–7,
tables T1–T7) and drives the EXISTING client functions in services/altruist_oauth.py — ``build_authorize_url``,
``exchange_code_for_tokens``, ``refresh_access_token`` and ``fetch_resource`` (the one request path every ``call_*``
uses) — so client and server are proven to agree. It ALSO reads each raw response and pins the PRIMARY key names
(contract §6: alias keys are client tolerance, not contract).

No database, no simulator import: the target is a base URL + credentials (+ an optional transport). The runner
(scripts/cpsim_altruist_conformance.py) and the admin page (routers/admin_counterparties.py) supply them.

cpsim03 D10 — the ``orders`` group (O01–O13 + O15, contract §8 — every field ASSUMED), driven through the orders client
(services/altruist_trading.py ``post_order`` / ``get_order`` / ``delete_order``) and raw requests. Simulator ONLY: any
other target skips it with "no published Altruist orders contract". It PLACES ORDERS and so moves the target's book
(§8.5), so it runs only when the caller passes a fixture book (``orders=``): the in-process fixture run does; the admin
page's run against a live counterparty does not (it reports the group as not run, with that reason).

cpsim04 D11 — the ``transfers`` group (T01–T15, contract §9 — every field ASSUMED), driven through the transfers client
(services/altruist_transfers.py ``post_transfer`` / ``get_transfer`` / ``list_transfers`` / ``delete_transfer``) and
raw requests. Simulator ONLY ("no published Altruist money-movement contract" for any other target); it MOVES CASH, so
it runs only with a fixture (``transfers=``: two custodian accounts, a domestic and an international simulated bank).
The D2 feed types are pinned here too (T02: the served cash rows carry the §9.4 types the client maps).
"""

from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from services import altruist_oauth as AO

#: the types the client maps (services/altruist_positions_sync.TRANSACTION_TYPE_MAP): its 7 + cpsim04's 7 cash types
TXN_TYPES = ("buy", "sell", "dividend", "interest", "fee", "fee_expense", "adjustment",
             "transfer_in", "transfer_out", "ach_in", "ach_out", "wire_in", "wire_out", "ach_return")
POSITION_FIELDS = ("id", "cusip", "isin", "ticker", "name", "asset_type", "quantity", "market_value", "cost_basis",
                   "currency_code")
TRANSACTION_FIELDS = ("id", "type", "cusip", "isin", "ticker", "name", "trade_date", "settle_date", "quantity", "price",
                      "gross_amount", "fees", "taxes", "net_amount", "currency_code")
TOKEN_FIELDS = ("access_token", "refresh_token", "expires_in", "scope", "token_type")
PAGINATION_KEYS = ("next", "cursor", "next_cursor", "has_more", "links", "page", "total_pages")
ISO_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RX = re.compile(r"^-?\d+(\.\d+)?$")
SIM_HEADER = "x-counterparty-simulated"
DEFAULT_REDIRECT = "https://conformance.hollisworks.invalid/altruist/callback"


class Suite:
    def __init__(self):
        self.results: list[dict] = []

    def check(self, passed, cid: str, contract: str, label: str, detail: str = "") -> bool:
        ok = bool(passed)
        self.results.append({"id": cid, "contract": contract, "label": label, "passed": ok,
                             "detail": "" if ok else str(detail)[:400]})
        return ok

    def na(self, cid: str, contract: str, label: str, why: str) -> None:
        self.results.append({"id": cid, "contract": contract, "label": label, "passed": None, "detail": why})

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r["passed"] is False)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r["passed"] is True)


def _num_ok(v) -> bool:
    return v is None or isinstance(v, (int, float)) and not isinstance(v, bool) or (isinstance(v, str)
                                                                                    and bool(NUM_RX.match(v)))


async def run(*, environment: str, hosts: dict, credentials: tuple[str, str], transport: Any = None,
              redirect_uri: str = DEFAULT_REDIRECT, scope: str = "accounts:read", timeout: float = 15.0,
              expect_simulated_header: bool | None = None, max_accounts: int = 25, orders: dict | None = None,
              transfers: dict | None = None) -> dict:
    """Run every check. Returns ``{"target", "passed", "failed", "results": [...]}``. Never raises for a server
    defect — a defect is a failed check; a client-side refusal (D1) is a failed check too."""
    S = Suite()
    sim = environment == AO.SIMULATOR if expect_simulated_header is None else expect_simulated_header
    login, api = hosts["login_base"], hosts["api_base"]
    client_id, secret = credentials
    seen_headers: list[tuple[str, str | None]] = []

    async def raw(method: str, url: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False) as c:
            r = await c.request(method, url, **kw)
        seen_headers.append((url.split("?")[0][-60:], r.headers.get(SIM_HEADER)))
        return r

    async def authorize(cid_: str | None = None) -> tuple[httpx.Response, str]:
        state = secrets.token_urlsafe(32)
        url = AO.build_authorize_url(environment=environment, state=state, scope=scope, hosts=hosts,
                                     credentials=(cid_ or client_id, secret), redirect_uri=redirect_uri)
        return await raw("GET", url), state

    def code_of(res: httpx.Response) -> tuple[str, str]:
        q = parse_qs(urlsplit(res.headers.get("location") or "").query)
        return (q.get("code") or [""])[0], (q.get("state") or [""])[0]

    # §6.1 / T1 — authorize
    try:
        res, state = await authorize()
        code, echoed = code_of(res)
        S.check(res.status_code == 302 and (res.headers.get("location") or "").startswith(redirect_uri) and code
                and echoed == state, "C01", "§6.1 / T1",
                "authorize: 302 to redirect_uri with a code and the state echoed",
                f"status {res.status_code}, location starts ok: "
                f"{(res.headers.get('location') or '').startswith(redirect_uri)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C01", "§6.1 / T1", "authorize: 302 to redirect_uri with a code and the state echoed",
                f"{type(exc).__name__}: {exc}")
        code = ""
    try:
        bad, _ = await authorize("00000000-0000-0000-0000-000000000000")
        S.check(bad.status_code >= 400 and "location" not in bad.headers, "C02", "§6.1 / T1 (client_id)",
                "authorize: an unknown client_id is refused (≥ 400, no redirect)", f"status {bad.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C02", "§6.1 / T1 (client_id)", "authorize: an unknown client_id is refused",
                f"{type(exc).__name__}: {exc}")

    # §6.2 / T2 — the client's own exchange, then a used code
    tokens = None
    try:
        tokens = await AO.exchange_code_for_tokens(environment=environment, code=code, hosts=hosts,
                                                   transport=transport, credentials=credentials,
                                                   redirect_uri=redirect_uri, timeout=timeout)
        S.check(bool(tokens.access_token and tokens.refresh_token and tokens.expires_in > 0), "C03", "§6.2 / T2",
                "token (authorization_code): the client's exchange_code_for_tokens parses the response")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C03", "§6.2 / T2", "token (authorization_code): the client's exchange_code_for_tokens "
                                           "parses the response", f"{type(exc).__name__}: {exc}")
    try:
        await AO.exchange_code_for_tokens(environment=environment, code=code, hosts=hosts, transport=transport,
                                          credentials=credentials, redirect_uri=redirect_uri, timeout=timeout)
        S.check(False, "C04", "§6.2 / T2", "token: a used authorization code is refused", "a used code was accepted")
    except AO.AltruistOAuthError:
        S.check(True, "C04", "§6.2 / T2", "token: a used authorization code is refused")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C04", "§6.2 / T2", "token: a used authorization code is refused", f"{type(exc).__name__}")

    # §6.2 / T2 — the raw token response, primary field names, token_type Bearer
    try:
        res2, _s = await authorize()
        code2, _e = code_of(res2)
        r = await raw("POST", f"{login}/oauth/token", data={
            "grant_type": "authorization_code", "code": code2, "redirect_uri": redirect_uri, "client_id": client_id,
            "client_secret": secret}, headers={"Accept": "application/json"})
        body = r.json() if r.status_code < 400 else {}
        S.check(r.status_code == 200 and all(k in body for k in TOKEN_FIELDS) and body.get("token_type") == "Bearer"
                and isinstance(body.get("expires_in"), int), "C05", "§6.2 / T2",
                "token response carries access_token, refresh_token, expires_in (int), scope, token_type Bearer",
                f"status {r.status_code}, keys {sorted(body)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C05", "§6.2 / T2", "token response primary fields", f"{type(exc).__name__}: {exc}")
    try:
        await AO.exchange_code_for_tokens(environment=environment, code="cpsim_code_" + "0" * 40, hosts=hosts,
                                          transport=transport, credentials=(client_id, secret + "x"),
                                          redirect_uri=redirect_uri, timeout=timeout)
        S.check(False, "C06", "§6.2 / T2 (client_secret)", "token: a wrong client_secret is refused", "accepted")
    except AO.AltruistOAuthError:
        S.check(True, "C06", "§6.2 / T2 (client_secret)", "token: a wrong client_secret is refused")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C06", "§6.2 / T2 (client_secret)", "token: a wrong client_secret is refused",
                f"{type(exc).__name__}")

    # §6.2 / T3 — refresh: the client's refresh_access_token; the old refresh token is spent
    access = tokens.access_token if tokens else ""
    if tokens:
        try:
            fresh = await AO.refresh_access_token(environment=environment, refresh_token=tokens.refresh_token,
                                                  hosts=hosts, transport=transport, credentials=credentials,
                                                  timeout=timeout)
            S.check(bool(fresh.access_token and fresh.access_token != tokens.access_token
                         and fresh.refresh_token != tokens.refresh_token), "C07", "§6.2 / T3",
                    "refresh: the client's refresh_access_token gets a new access AND refresh token (rotation)")
            access = fresh.access_token
            try:
                await AO.refresh_access_token(environment=environment, refresh_token=tokens.refresh_token,
                                              hosts=hosts, transport=transport, credentials=credentials,
                                              timeout=timeout)
                S.check(False, "C08", "§6.2 / T3", "refresh: a spent refresh token is refused", "accepted twice")
            except AO.AltruistOAuthError:
                S.check(True, "C08", "§6.2 / T3", "refresh: a spent refresh token is refused")
        except Exception as exc:  # noqa: BLE001
            S.check(False, "C07", "§6.2 / T3", "refresh rotates", f"{type(exc).__name__}: {exc}")
    else:
        S.check(False, "C07", "§6.2 / T3", "refresh rotates", "no tokens from C03")

    # §6.3–6.6 / T4–T7 — resources through the client's fetch_resource AND raw
    headers = {"Authorization": f"Bearer {access}", "Accept": "application/json"}

    async def both(resource: str, path: str, param_name: str | None, param: str | None):
        items = await AO.fetch_resource(api_base=api, resource=resource, access_token=access, param=param,
                                        timeout=timeout, transport=transport)
        r = await raw("GET", f"{api}{path}", params={param_name: param} if param_name else None, headers=headers)
        return items, r

    hh_items, accounts_all = [], []
    try:
        hh_items, r = await both("households", "/api/v2/households", None, None)
        body = r.json()
        rows = body.get("households") if isinstance(body, dict) else None
        S.check(r.status_code == 200 and isinstance(rows, list) and rows and all(
            isinstance(x.get("id"), str) and x["id"] and isinstance(x.get("name"), str) for x in rows)
            and len(rows) == len(hh_items), "C09", "§6.3 / T4",
            'households: {"households": [{id, name}]} (primary key), non-empty, the client reads every one',
            f"status {r.status_code}, keys {sorted(body) if isinstance(body, dict) else type(body).__name__}")
        S.check(not any(k in body for k in PAGINATION_KEYS), "C10", "§6 (one response)",
                "households: one response — no pagination keys", f"{[k for k in PAGINATION_KEYS if k in body]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C09", "§6.3 / T4", "households", f"{type(exc).__name__}: {exc}")
    acct_ok, acct_detail = True, ""
    for hh in hh_items:
        try:
            items, r = await both("accounts", "/v2/accounts", "household_id", hh["id"])
            body = r.json()
            rows = body.get("accounts") if isinstance(body, dict) else None
            good = r.status_code == 200 and isinstance(rows, list) and all(
                isinstance(x.get("id"), str) and x["id"] and isinstance(x.get("name"), str) for x in rows) \
                and len(rows) == len(items) and not any(k in body for k in PAGINATION_KEYS)
            if not good:
                acct_ok, acct_detail = False, f"household {hh['id']}: status {r.status_code}"
            accounts_all.extend(items)
        except Exception as exc:  # noqa: BLE001
            acct_ok, acct_detail = False, f"{type(exc).__name__}: {exc}"
    S.check(acct_ok and accounts_all, "C11", "§6.4 / T5",
            'accounts?household_id=: {"accounts": [{id, name}]} per household, one response', acct_detail or "none")

    pos_ok, pos_detail, txn_ok, txn_detail, ids_ok, dates_ok, link_ok, link_detail = (True, "", True, "", True, True,
                                                                                      True, "")
    for acct in accounts_all[:max_accounts]:
        try:
            p_items, r = await both("positions", "/v2/positions", "account_id", acct["id"])
            body = r.json()
            rows = body.get("positions") if isinstance(body, dict) else None
            if not (r.status_code == 200 and isinstance(rows, list) and len(rows) == len(p_items)):
                pos_ok, pos_detail = False, f"account {acct['id']}: status {r.status_code}, keys " \
                                            f"{sorted(body) if isinstance(body, dict) else '?'}"
                continue
            for x in rows:
                missing = [k for k in POSITION_FIELDS if k not in x]
                if missing or not all(_num_ok(x.get(k)) for k in ("quantity", "market_value", "cost_basis")):
                    pos_ok, pos_detail = False, f"account {acct['id']} position {x.get('id')}: missing primary " \
                                                f"field(s) {missing}" if missing else "a number is not a decimal"
                if not (isinstance(x.get("id"), str) and x["id"]):
                    ids_ok = False
            held = {(x.get("cusip") or x.get("isin") or x.get("ticker") or f"name:{(x.get('name') or '').lower()}")
                    for x in rows}
            held |= {v for x in rows for v in (x.get("cusip"), x.get("isin"), x.get("ticker")) if v}
            t_items, r = await both("transactions", "/v2/transactions", "account_id", acct["id"])
            body = r.json()
            rows_t = body.get("transactions") if isinstance(body, dict) else None
            if not (r.status_code == 200 and isinstance(rows_t, list) and len(rows_t) == len(t_items)):
                txn_ok, txn_detail = False, f"account {acct['id']}: status {r.status_code}"
                continue
            for x in rows_t:
                missing = [k for k in TRANSACTION_FIELDS if k not in x]
                if missing or x.get("type") not in TXN_TYPES:
                    txn_ok, txn_detail = False, f"transaction {x.get('id')}: missing {missing} or type " \
                                                f"{x.get('type')!r}"
                if not (isinstance(x.get("id"), str) and x["id"]):
                    ids_ok = False
                for dk in ("trade_date", "settle_date"):
                    v = x.get(dk)
                    if v is not None and not (isinstance(v, str) and ISO_RX.match(v) and date.fromisoformat(v)):
                        dates_ok = False
                key = x.get("cusip") or x.get("isin") or x.get("ticker") or f"name:{(x.get('name') or '').lower()}"
                if key not in held:
                    link_ok, link_detail = False, f"transaction {x.get('id')} names a security with no position"
        except Exception as exc:  # noqa: BLE001
            pos_ok, pos_detail = False, f"{type(exc).__name__}: {exc}"
    S.check(pos_ok, "C12", "§6.5 / T6", "positions?account_id=: {\"positions\": [...]} with every T6 primary field "
                                        "(id, cusip, isin, ticker, name, asset_type, quantity, market_value, "
                                        "cost_basis, currency_code); decimals as numbers or decimal strings",
            pos_detail)
    S.check(txn_ok, "C13", "§6.6 / T7", "transactions?account_id=: {\"transactions\": [...]} with every T7 primary "
                                        "field; type one of the client's 7", txn_detail)
    S.check(link_ok, "C14", "§6.6 / T7 (position rule)",
            "every transacted security also appears in the account's positions", link_detail)
    S.check(ids_ok, "C15", "§6 (explicit ids)", "every position and transaction carries an explicit id")
    S.check(dates_ok, "C16", "§6 (ISO dates)", "every date is ISO YYYY-MM-DD")

    # resource auth: no token / a junk token → refused
    try:
        r = await raw("GET", f"{api}/api/v2/households", headers={"Accept": "application/json"})
        r2 = await raw("GET", f"{api}/api/v2/households", headers={"Authorization": "Bearer not-a-token",
                                                                    "Accept": "application/json"})
        S.check(r.status_code == 401 and r2.status_code == 401, "C17", "§6.3 (Bearer check)",
                "a resource without a valid Bearer token is refused 401", f"{r.status_code}/{r2.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C17", "§6.3 (Bearer check)", "Bearer check", f"{type(exc).__name__}")

    skip = orders_group_skip(environment, orders)
    if skip:
        S.na("O00", "§8 (orders)", "orders group", skip)
    else:
        await _orders_group(S, raw=raw, api=api, access=access, credentials=credentials, transport=transport,
                            timeout=timeout, fx=orders)

    tskip = transfers_group_skip(environment, transfers)
    if tskip:
        S.na("T00", "§9 (transfers)", "transfers group", tskip)
    else:
        await _transfers_group(S, raw=raw, api=api, access=access, credentials=credentials, transport=transport,
                               timeout=timeout, fx=transfers)

    if sim:
        missing = [u for u, v in seen_headers if v != "true"]
        S.check(seen_headers and not missing, "C18", "§6 (SIMULATED header)",
                "every simulator response carries X-Counterparty-Simulated: true", f"missing on {missing[:5]}")
    else:
        S.na("C18", "§6 (SIMULATED header)", "SIMULATED header", "not applicable to a real counterparty")
    return {"environment": environment, "passed": S.passed, "failed": S.failed, "results": S.results}


NO_ORDERS_CONTRACT = "no published Altruist orders contract"
NOT_A_FIXTURE = ("not run: the orders group places orders and moves the book, so it runs only against a fixture book "
                 "(the in-process run)")


def orders_group_skip(environment: str, orders: dict | None) -> str | None:
    """Why the orders group does not run against this target (None → it runs). A real target: there is no published
    Altruist orders contract to conform to."""
    if environment != AO.SIMULATOR:
        return NO_ORDERS_CONTRACT
    if orders is None:
        return NOT_A_FIXTURE
    return None
ORDER_FIELDS = ("order_id", "client_order_id", "account_id", "symbol", "cusip", "side", "quantity", "notional",
                "order_type", "limit_price", "time_in_force", "status", "reject_reason", "filled_quantity",
                "average_price", "executions", "trade_date", "created_at", "updated_at")
EXEC_FIELDS = ("exec_id", "quantity", "price", "executed_at", "trade_date", "settle_date")
ORDER_STATUSES = ("accepted", "working", "partially_filled", "filled", "rejected", "cancelled")
TS_RX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


async def _orders_group(S: Suite, *, raw, api: str, access: str, credentials, transport, timeout: float, fx: dict):
    """O01–O13 + O15 (contract §8). ``fx`` = {account_id, cusip, held} — a fixture account holding ``held`` shares of a
    priced equity / fund security named by ``cusip``."""
    from decimal import Decimal

    from services import altruist_trading as AT
    from services.us_business_days import is_business_day

    acct, cusip, held = fx["account_id"], fx["cusip"], Decimal(str(fx["held"]))
    tag = secrets.token_hex(4)
    hdr = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
    kw = {"api_base": api, "access_token": access, "timeout": timeout, "transport": transport}
    buy = {"client_order_id": f"conf-{tag}-buy", "account_id": acct, "cusip": cusip, "side": "buy", "quantity": "2",
           "order_type": "market", "time_in_force": "gtc"}
    first = {}
    try:
        status, first = await AT.post_order(order=buy, **kw)
        S.check(status == 201 and all(k in first for k in ORDER_FIELDS) and first.get("status") in ORDER_STATUSES
                and first.get("client_order_id") == buy["client_order_id"]
                and str(first.get("order_id", "")).startswith("sim-o-"), "O01", "§8.1 O-1 / §8.3",
                "POST /v2/orders (the client's post_order): 201, the order with every §8.3 field",
                f"status {status}, missing {[k for k in ORDER_FIELDS if k not in first]}")
        ex = first.get("executions") or []
        good = first.get("status") == "filled" and len(ex) == 1 and all(k in ex[0] for k in EXEC_FIELDS) and all(
            isinstance(ex[0].get(k), str) and NUM_RX.match(ex[0][k]) for k in ("quantity", "price")) \
            and Decimal(ex[0]["quantity"]) == Decimal("2") and first.get("filled_quantity") == "2"
        sd = ex[0].get("settle_date") if ex else None
        td = ex[0].get("trade_date") if ex else None
        dates = bool(td and sd and ISO_RX.match(td) and ISO_RX.match(sd) and date.fromisoformat(sd) >
                     date.fromisoformat(td) and is_business_day(date.fromisoformat(sd))
                     and TS_RX.match(str(ex[0].get("executed_at") or "")))
        S.check(good and dates, "O02", "§8.3 executions / §8.5",
                "a market buy (accept) fills in full: one execution {exec_id, quantity, price, executed_at, "
                "trade_date, settle_date}, decimals as strings, settle a later US business day, ISO UTC time",
                f"status {first.get('status')}, executions {ex}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O01", "§8.1 O-1 / §8.3", "POST /v2/orders", f"{type(exc).__name__}: {exc}")
    try:
        status, again = await AT.post_order(order={**buy, "quantity": "3"}, **kw)
        r = await raw("GET", f"{api}/v2/orders", params={"account_id": acct}, headers=hdr)
        same = [o for o in (r.json().get("orders") or []) if o.get("client_order_id") == buy["client_order_id"]]
        S.check(status == 200 and again.get("order_id") == first.get("order_id") and again.get("filled_quantity") == "2"
                and len(same) == 1, "O03", "§8.1 O-1 (idempotent)",
                "a repeat POST with the same client_order_id returns the SAME order (200) — never a second one",
                f"status {status}, same id {again.get('order_id') == first.get('order_id')}, orders {len(same)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O03", "§8.1 O-1 (idempotent)", "idempotent POST", f"{type(exc).__name__}: {exc}")
    try:
        # cpsim03 fix 2 — the repeat rule pinned on a body that differs in EVERY order field: a repeat POST with the same
        # client_order_id returns the ORIGINAL order unchanged, 200, whatever the body — and still exactly one order
        other = {"client_order_id": buy["client_order_id"], "account_id": acct, "cusip": cusip, "side": "sell",
                 "quantity": "1", "order_type": "limit", "limit_price": "0.01", "time_in_force": "day"}
        status, again = await AT.post_order(order=other, **kw)
        got = await AT.get_order(order_id=first.get("order_id", "x"), **kw)
        r = await raw("GET", f"{api}/v2/orders", params={"account_id": acct}, headers=hdr)
        same = [o for o in (r.json().get("orders") or []) if o.get("client_order_id") == buy["client_order_id"]]
        orig = {k: first.get(k) for k in ORDER_FIELDS}
        diff = [k for k in ORDER_FIELDS if again.get(k) != orig[k] or got.get(k) != orig[k]]
        S.check(bool(first) and status == 200 and not diff and len(same) == 1, "O15", "§8.1 O-1 (repeat rule)",
                "a repeat POST with the same client_order_id but a DIFFERENT body (sell / limit / day / qty 1) returns "
                "the original order unchanged (every §8.3 field), 200 — and there is still exactly one order",
                f"status {status}, changed {diff}, orders {len(same)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O15", "§8.1 O-1 (repeat rule)", "different-body repeat POST", f"{type(exc).__name__}: {exc}")
    try:
        got = await AT.get_order(order_id=first.get("order_id", "x"), **kw)
        S.check(got.get("order_id") == first.get("order_id") and got.get("status") == first.get("status"), "O04",
                "§8.1 O-2", "GET /v2/orders/{order_id} (the client's get_order) returns the order", f"{got}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O04", "§8.1 O-2", "GET one order", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("GET", f"{api}/v2/orders", params={"account_id": acct}, headers=hdr)
        body = r.json()
        r400 = await raw("GET", f"{api}/v2/orders", headers=hdr)
        S.check(r.status_code == 200 and isinstance(body.get("orders"), list) and body["orders"]
                and not any(k in body for k in PAGINATION_KEYS) and r400.status_code == 400, "O05", "§8.1 O-3",
                'GET /v2/orders?account_id= → {"orders": [...]} in one response; without account_id → 400',
                f"{r.status_code}/{r400.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O05", "§8.1 O-3", "list orders", f"{type(exc).__name__}: {exc}")
    try:
        bad = {**buy, "client_order_id": f"conf-{tag}-float", "quantity": 2.5}
        r = await raw("POST", f"{api}/v2/orders", json=bad, headers=hdr)
        body = r.json()
        r2 = await raw("GET", f"{api}/v2/orders", params={"account_id": acct}, headers=hdr)
        made = [o for o in (r2.json().get("orders") or []) if o.get("client_order_id") == bad["client_order_id"]]
        S.check(r.status_code == 422 and any(e.get("field") == "quantity" for e in body.get("errors") or [])
                and not made, "O06", "§8.2 (decimal strings)",
                "a JSON-float quantity is refused 422 naming the field; no order is created",
                f"status {r.status_code}, errors {body.get('errors')}, created {len(made)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O06", "§8.2 (decimal strings)", "float refused", f"{type(exc).__name__}: {exc}")
    try:
        lim = {**buy, "client_order_id": f"conf-{tag}-limit", "order_type": "limit", "limit_price": "0.01"}
        _s, working = await AT.post_order(order=lim, **kw)
        cancelled = await AT.delete_order(order_id=working.get("order_id", "x"), **kw)
        try:
            await AT.delete_order(order_id=working.get("order_id", "x"), **kw)
            twice = None
        except AT.AltruistOrderRefused as e:
            twice = e.status
        S.check(working.get("status") == "working" and not working.get("executions") and cancelled.get("status")
                == "cancelled" and twice == 409, "O07", "§8.1 O-4 / §8.5 (limit)",
                "an unmarketable limit buy is working; DELETE (the client's delete_order) cancels it; a second "
                "DELETE is 409", f"{working.get('status')} → {cancelled.get('status')}, second {twice}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O07", "§8.1 O-4 / §8.5 (limit)", "cancel a working order", f"{type(exc).__name__}: {exc}")
    try:
        await AT.delete_order(order_id=first.get("order_id", "x"), **kw)
        S.check(False, "O08", "§8.1 O-4 (filled)", "DELETE a filled order is refused 409", "accepted")
    except AT.AltruistOrderRefused as e:
        S.check(e.status == 409 and "filled" in (e.detail or ""), "O08", "§8.1 O-4 (filled)",
                "DELETE a filled order is refused 409, the reason naming its status", f"{e.status} {e.detail}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O08", "§8.1 O-4 (filled)", "DELETE filled", f"{type(exc).__name__}: {exc}")
    try:
        sell = {**buy, "client_order_id": f"conf-{tag}-sell", "side": "sell", "quantity": str(held + 1000)}
        _s, rej = await AT.post_order(order=sell, **kw)
        S.check(rej.get("status") == "rejected" and rej.get("reject_reason") == "insufficient position"
                and not rej.get("executions"), "O09", "§8.5 (insufficient position)",
                "a sell above the position is rejected 'insufficient position' (never below zero)", f"{rej}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O09", "§8.5 (insufficient position)", "oversell", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("POST", f"{api}/v2/orders", json={**buy, "client_order_id": f"conf-{tag}-noauth"},
                      headers={"Accept": "application/json"})
        r2 = await raw("POST", f"{api}/v2/orders", json={**buy, "client_order_id": f"conf-{tag}-key"},
                       headers={"Authorization": f"Bearer {credentials[1]}", "Accept": "application/json"})
        S.check(r.status_code == 401 and r2.status_code == 401, "O10", "§8.1 O-5",
                "orders need an ACCESS token: none → 401, the cpsim_ client key itself → 401",
                f"{r.status_code}/{r2.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O10", "§8.1 O-5", "orders auth", f"{type(exc).__name__}")
    try:
        r = await raw("GET", f"{api}/v2/orders/sim-o-0000000000000000", headers=hdr)
        S.check(r.status_code == 404, "O11", "§8.1 O-2 (404)", "an unknown order id is 404", f"{r.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O11", "§8.1 O-2 (404)", "unknown order", f"{type(exc).__name__}")
    try:
        ex = (first.get("executions") or [{}])[0]
        rp = await raw("GET", f"{api}/v2/positions", params={"account_id": acct}, headers=hdr)
        rt = await raw("GET", f"{api}/v2/transactions", params={"account_id": acct}, headers=hdr)
        qty = sum((Decimal(p["quantity"]) for p in rp.json().get("positions") or [] if p.get("cusip") == cusip),
                  Decimal(0))
        txn = [t for t in rt.json().get("transactions") or [] if t.get("id") == f"sim-x-{ex.get('exec_id')}"]
        S.check(qty == held + Decimal("2") and len(txn) == 1 and txn[0].get("type") == "buy"
                and txn[0].get("quantity") == "2", "O12", "§8.5 (book moves for fills)",
                "the fill moved the book: the position is held + 2 and the buy transaction sim-x-<exec_id> is served",
                f"position {qty} (held {held}), transactions {txn}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O12", "§8.5 (book moves for fills)", "book moved", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("GET", f"{api}/v2/orders/{first.get('order_id', 'x')}", headers=hdr)
        S.check(r.headers.get(SIM_HEADER) == "true", "O13", "§8.1 O-6", "order responses carry "
                "X-Counterparty-Simulated: true", f"{r.headers.get(SIM_HEADER)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "O13", "§8.1 O-6", "SIMULATED header", f"{type(exc).__name__}")



# ── cpsim04 D11: the transfers group (contract §9 — every field ASSUMED) ─────────────────────────────────────────────
NO_TRANSFERS_CONTRACT = "no published Altruist money-movement contract"
NOT_A_TRANSFER_FIXTURE = ("not run: the transfers group moves cash, so it runs only against a fixture book and fixture "
                          "banks (the in-process run)")
TRANSFER_FIELDS = ("transfer_id", "client_transfer_id", "from_account_id", "to_account_id", "to_bank", "amount",
                   "currency", "rail", "value_date", "status", "reject_reason", "return_code", "return_reason",
                   "received_on", "acknowledged_on", "settle_on", "settled_on", "returned_on", "events", "created_at",
                   "updated_at")
TRANSFER_STATUSES = ("received", "pending_consent", "acknowledged", "settled", "rejected", "returned", "cancelled")
CASH_TYPES = ("transfer_in", "transfer_out", "ach_in", "ach_out", "wire_in", "wire_out", "ach_return")


def transfers_group_skip(environment: str, transfers: dict | None) -> str | None:
    """Why the transfers group does not run against this target (None → it runs)."""
    if environment != AO.SIMULATOR:
        return NO_TRANSFERS_CONTRACT
    if transfers is None:
        return NOT_A_TRANSFER_FIXTURE
    return None


async def _transfers_group(S: Suite, *, raw, api: str, access: str, credentials, transport, timeout: float, fx: dict):
    """T01–T15. ``fx`` = {from, to (both accounts of this custodian; ``from`` holds ``cash`` USD in its cash row),
    cash, value_date, domestic: {slug, account_id, ach4, wire4}, international: {slug, account_id, bic, iban4}}."""
    from decimal import Decimal

    from services import altruist_positions_sync as APS
    from services import altruist_transfers as AT

    tag = secrets.token_hex(4)
    hdr = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
    kw = {"api_base": api, "access_token": access, "timeout": timeout, "transport": transport}
    src, dst, vd = fx["from"], fx["to"], fx["value_date"]
    dom, intl = fx["domestic"], fx["international"]

    async def cash(account: str) -> Decimal:
        r = await raw("GET", f"{api}/v2/positions", params={"account_id": account}, headers=hdr)
        return sum((Decimal(p["market_value"]) for p in r.json().get("positions") or [] if not p.get("cusip")
                    and not p.get("isin") and not p.get("ticker") and (p.get("asset_type") in (None, "cash"))),
                   Decimal(0))

    def body(cid: str, **over) -> dict:
        b = {"client_transfer_id": f"conf-{tag}-{cid}", "from_account_id": src, "amount": "100.00", "currency": "USD",
             "rail": "journal", "value_date": vd, "to_account_id": dst}
        b.update(over)
        return {k: v for k, v in b.items() if v is not None}

    first, before_src, before_dst = {}, None, None
    try:
        before_src, before_dst = await cash(src), await cash(dst)
        status, first = await AT.post_transfer(transfer=body("journal"), **kw)
        missing = [k for k in TRANSFER_FIELDS if k not in first]
        S.check(status == 201 and not missing and first.get("status") == "settled" and first.get("amount") == "100.00"
                and str(first.get("transfer_id", "")).startswith("sim-t-")
                and [e.get("status") for e in first.get("events") or []] == ["received", "acknowledged", "settled"]
                and first.get("settled_on") == vd, "T01", "§9.1 T-1 / §9.3 / §9.6",
                "POST a journal → 201 with every §9.3 field; same-day value date: received → acknowledged → settled, "
                "each an event", f"status {status}, missing {missing}, transfer {first}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T01", "§9.1 T-1 / §9.3 / §9.6", "POST a journal", f"{type(exc).__name__}: {exc}")
    try:
        after_src, after_dst = await cash(src), await cash(dst)
        rt = await raw("GET", f"{api}/v2/transactions", params={"account_id": src}, headers=hdr)
        rd = await raw("GET", f"{api}/v2/transactions", params={"account_id": dst}, headers=hdr)
        tid = first.get("transfer_id")
        out_ = [t for t in rt.json().get("transactions") or [] if t.get("id") == f"{tid}-out"]
        in_ = [t for t in rd.json().get("transactions") or [] if t.get("id") == f"{tid}-in"]
        mapped = all(t.get("type") in APS.TRANSACTION_TYPE_MAP for t in out_ + in_)
        S.check(after_src == before_src - Decimal("100") and after_dst == before_dst + Decimal("100") and len(out_) == 1
                and len(in_) == 1 and out_[0].get("type") == "transfer_out" and in_[0].get("type") == "transfer_in"
                and out_[0].get("net_amount") == "100" and in_[0].get("net_amount") == "100" and mapped
                and APS.TRANSACTION_TYPE_MAP["transfer_out"] == "sell" and APS.TRANSACTION_TYPE_MAP["transfer_in"]
                == "buy", "T02", "§9.4 / D2 (cash ledger + feed types)",
                "the journal moved the cash rows exactly (−100 / +100) and the feed serves transfer_out / transfer_in "
                "(explicit ids <transfer>-out / -in) — types the client maps (sell / buy)",
                f"src {before_src}→{after_src}, dst {before_dst}→{after_dst}, out {out_}, in {in_}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T02", "§9.4 / D2", "cash ledger + feed", f"{type(exc).__name__}: {exc}")
    try:
        status, again = await AT.post_transfer(transfer=body("journal", amount="999.00", rail="ach", to_account_id=None,
                                                             to_bank={"institution_slug": dom["slug"],
                                                                      "account_id": dom["account_id"],
                                                                      "routing_last4": dom["ach4"]}), **kw)
        listed = await AT.list_transfers(account_id=src, **kw)
        same = [t for t in listed if t.get("client_transfer_id") == f"conf-{tag}-journal"]
        S.check(status == 200 and again.get("transfer_id") == first.get("transfer_id") and again.get("amount")
                == "100.00" and again.get("rail") == "journal" and len(same) == 1, "T03", "§9.1 T-1 (repeat rule)",
                "a repeat POST with a DIFFERENT body → 200, the ORIGINAL transfer unchanged, one transfer",
                f"status {status}, {again.get('amount')} {again.get('rail')}, {len(same)} transfers")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T03", "§9.1 T-1 (repeat rule)", "repeat POST", f"{type(exc).__name__}: {exc}")
    try:
        got = await AT.get_transfer(transfer_id=first.get("transfer_id", "x"), **kw)
        S.check(got.get("transfer_id") == first.get("transfer_id") and got.get("status") == "settled", "T04",
                "§9.1 T-2", "GET one transfer", f"{got}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T04", "§9.1 T-2", "GET one transfer", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("GET", f"{api}/v2/transfers", params={"account_id": dst}, headers=hdr)
        b = r.json()
        r400 = await raw("GET", f"{api}/v2/transfers", headers=hdr)
        S.check(r.status_code == 200 and any(t.get("transfer_id") == first.get("transfer_id") for t in b.get("transfers")
                                             or []) and not any(k in b for k in PAGINATION_KEYS)
                and r400.status_code == 400, "T05", "§9.1 T-3",
                "list by account (either leg; the destination finds it), one response, 400 without account_id",
                f"{r.status_code} / {r400.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T05", "§9.1 T-3", "list transfers", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("POST", f"{api}/v2/transfers", json=body("float", amount=100.0), headers=hdr)
        made = [t for t in await AT.list_transfers(account_id=src, **kw)
                if t.get("client_transfer_id") == f"conf-{tag}-float"]
        errs = r.json().get("errors") or []
        S.check(r.status_code == 422 and any(e.get("field") == "amount" for e in errs) and not made, "T06",
                "§9.2 (decimal strings)", "a JSON-number amount → 422 naming the field, nothing created",
                f"{r.status_code} {errs}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T06", "§9.2 (decimal strings)", "float refused", f"{type(exc).__name__}: {exc}")
    try:
        s0 = await cash(src)
        status, t = await AT.post_transfer(transfer=body("ach", rail="ach", to_account_id=None,
                                                         to_bank={"institution_slug": dom["slug"],
                                                                  "account_id": dom["account_id"],
                                                                  "routing_last4": dom["ach4"]}), **kw)
        s1 = await cash(src)
        S.check(status == 201 and t.get("status") == "acknowledged" and t.get("settle_on") and t["settle_on"] > vd
                and s1 == s0 - Decimal("100") and (t.get("to_bank") or {}).get("routing_last4") == dom["ach4"], "T07",
                "§9.6 (ACH to a domestic bank)", "an ACH to a domestic bank with its ACH routing reference → acknowledged, "
                "the source debited, settlement the next business day", f"{t}, cash {s0}→{s1}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T07", "§9.6 (ACH)", "ACH to a domestic bank", f"{type(exc).__name__}: {exc}")
    try:
        s0 = await cash(src)
        wrong = "0000" if dom["wire4"] != "0000" else "1111"
        status, t = await AT.post_transfer(transfer=body("wire-bad", rail="wire", to_account_id=None,
                                                         to_bank={"institution_slug": dom["slug"],
                                                                  "account_id": dom["account_id"],
                                                                  "routing_last4": wrong}), **kw)
        s1 = await cash(src)
        S.check(status == 201 and t.get("status") == "rejected" and "routing number does not match"
                in (t.get("reject_reason") or "") and s1 == s0, "T08", "§9.6 / D6 (the bank's check)",
                "a wire with the wrong routing reference → rejected with the receiving bank's reason; nothing debited",
                f"{t.get('status')} {t.get('reject_reason')}, cash {s0}→{s1}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T08", "§9.6 / D6", "wrong routing", f"{type(exc).__name__}: {exc}")
    try:
        status, t = await AT.post_transfer(transfer=body("intl", rail="wire", to_account_id=None,
                                                         to_bank={"institution_slug": intl["slug"],
                                                                  "account_id": intl["account_id"], "bic": intl["bic"],
                                                                  "iban_last4": intl["iban4"]}), **kw)
        r = await raw("POST", f"{api}/v2/transfers", headers=hdr, json=body(
            "intl-ach", rail="ach", to_account_id=None, to_bank={"institution_slug": intl["slug"],
                                                                 "account_id": intl["account_id"], "bic": intl["bic"],
                                                                 "iban_last4": intl["iban4"]}))
        S.check(status == 201 and t.get("status") == "settled" and r.status_code == 422, "T09",
                "§9.2 / §9.6 (international wire)", "a wire to an international bank with its BIC + IBAN reference "
                "settles on the value date; an ACH to it is refused 422", f"{t.get('status')} {t.get('reject_reason')}"
                f" / {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T09", "§9.6 (international wire)", "international wire", f"{type(exc).__name__}: {exc}")
    try:
        bad4 = "ZZZZ" if intl["iban4"] != "ZZZZ" else "YYYY"
        status, t = await AT.post_transfer(transfer=body("intl-bad", rail="wire", to_account_id=None,
                                                         to_bank={"institution_slug": intl["slug"],
                                                                  "account_id": intl["account_id"], "bic": intl["bic"],
                                                                  "iban_last4": bad4}), **kw)
        S.check(t.get("status") == "rejected" and "IBAN does not match" in (t.get("reject_reason") or ""), "T10",
                "§9.6 / D6 (IBAN)", "a wrong IBAN reference → rejected with the bank's reason", f"{t}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T10", "§9.6 / D6 (IBAN)", "wrong IBAN", f"{type(exc).__name__}: {exc}")
    try:
        s0 = await cash(src)
        status, t = await AT.post_transfer(transfer=body("nsf", amount=format((s0 + Decimal("1")).quantize(
            Decimal("0.01")), "f")), **kw)
        S.check(t.get("status") == "rejected" and t.get("reject_reason") == "insufficient funds"
                and await cash(src) == s0, "T11", "§9.6 (insufficient funds)",
                "more than the cash row holds → rejected 'insufficient funds', nothing moved", f"{t}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T11", "§9.6 (insufficient funds)", "insufficient funds", f"{type(exc).__name__}: {exc}")
    try:
        try:
            await AT.delete_transfer(transfer_id=first.get("transfer_id", "x"), **kw)
            settled_409 = False
        except AT.AltruistTransferRefused as e:
            settled_409 = e.status == 409 and "settled" in (e.detail or "")
        r = await raw("DELETE", f"{api}/v2/transfers/sim-t-{'0' * 16}", headers=hdr)
        S.check(settled_409 and r.status_code == 404, "T12", "§9.1 T-4 / T-2 (404)",
                "DELETE a settled transfer → 409 naming its status; an unknown transfer → 404", f"{r.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T12", "§9.1 T-4", "cancel refused", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("GET", f"{api}/v2/transfers", params={"account_id": src}, headers={"Accept": "application/json"})
        r2 = await raw("GET", f"{api}/v2/transfers", params={"account_id": src},
                       headers={"Authorization": f"Bearer {credentials[1]}", "Accept": "application/json"})
        S.check(r.status_code == 401 and r2.status_code == 401, "T13", "§9.1 T-5",
                "transfers need an ACCESS token (none / the cpsim_ key → 401)", f"{r.status_code}/{r2.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T13", "§9.1 T-5", "transfers auth", f"{type(exc).__name__}")
    try:
        r = await raw("POST", f"{api}/v2/transfers", headers=hdr, json=body(
            "fullaba", rail="ach", to_account_id=None, to_bank={"institution_slug": dom["slug"],
                                                                "account_id": dom["account_id"],
                                                                "routing_last4": "991234567"}))
        errs = r.json().get("errors") or []
        S.check(r.status_code == 422 and any(e.get("field") == "to_bank.routing_last4" for e in errs), "T14",
                "§9.2 (references only)", "a full routing number where the last four belong → 422 naming the field",
                f"{r.status_code} {errs}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T14", "§9.2 (references only)", "full number refused", f"{type(exc).__name__}")
    try:
        r = await raw("GET", f"{api}/v2/transfers/{first.get('transfer_id', 'x')}", headers=hdr)
        S.check(r.headers.get(SIM_HEADER) == "true", "T15", "§9.1 T-6", "transfer responses carry "
                "X-Counterparty-Simulated: true", f"{r.headers.get(SIM_HEADER)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "T15", "§9.1 T-6", "SIMULATED header", f"{type(exc).__name__}")
