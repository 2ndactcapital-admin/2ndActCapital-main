"""cpsim02 D11 — the Altruist-compatible CONFORMANCE SUITE (core).

One suite, two targets: the simulator (now) and Altruist's real sandbox (the day access arrives — any difference found
then is a SIMULATOR defect). Every check cites the contract row it tests (docs/cpsim/ALTRUIST_CONTRACT.md §6 items 1–7,
tables T1–T7) and drives the EXISTING client functions in services/altruist_oauth.py — ``build_authorize_url``,
``exchange_code_for_tokens``, ``refresh_access_token`` and ``fetch_resource`` (the one request path every ``call_*``
uses) — so client and server are proven to agree. It ALSO reads each raw response and pins the PRIMARY key names
(contract §6: alias keys are client tolerance, not contract).

No database, no simulator import: the target is a base URL + credentials (+ an optional transport). The runner
(scripts/cpsim_altruist_conformance.py) and the admin page (routers/admin_counterparties.py) supply them.
"""

from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from services import altruist_oauth as AO

TXN_TYPES = ("buy", "sell", "dividend", "interest", "fee", "fee_expense", "adjustment")
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
              expect_simulated_header: bool | None = None, max_accounts: int = 25) -> dict:
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

    if sim:
        missing = [u for u, v in seen_headers if v != "true"]
        S.check(seen_headers and not missing, "C18", "§6 (SIMULATED header)",
                "every simulator response carries X-Counterparty-Simulated: true", f"missing on {missing[:5]}")
    else:
        S.na("C18", "§6 (SIMULATED header)", "SIMULATED header", "not applicable to a real counterparty")
    return {"environment": environment, "passed": S.passed, "failed": S.failed, "results": S.results}
