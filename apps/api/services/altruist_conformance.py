"""cpsim02 D11 — the Altruist-compatible CONFORMANCE SUITE (core). ALTREAD1 D5 — every READ check (C00–C17) is
re-pinned to Altruist's PUBLISHED Open API (developer.altruist.com, read 2026-11-11): each row's ``contract`` names the
DOCUMENTED page it tests (open-api-authentication, endpoints + the resource pages, json-code-examples); the paging,
envelope, date-format, cost-basis and transactions-window checks are new. The orders (§8) and transfers (§9) groups
are unchanged checks of ASSUMED contracts (not part of Altruist's published API — Realtime / FIX); they observe the
book through the documented read paths now.

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

import json
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from services import altruist_oauth as AO
from services import altruist_positions_sync as APS

#: the DOCUMENTED pages each read check cites (developer.altruist.com/docs, read 2026-11-11)
DOC_AUTH = "DOCUMENTED open-api-authentication"
DOC_ENDPOINTS = "DOCUMENTED endpoints"
DOC_JSON = "DOCUMENTED json-code-examples"
#: the types the client maps (services/altruist_positions_sync.TRANSACTION_TYPE_MAP): its 7 + cpsim04's 7 cash types
TXN_TYPES = tuple(APS.TRANSACTION_TYPE_MAP)
HOUSEHOLD_FIELDS = ("id", "name")
ACCOUNT_FIELDS = ("id", "rep_code", "account_number", "account_type", "account_name", "status", "date_open",
                  "date_closed", "as_of_date", "account_balance")
ACCOUNT_STATUSES = ("ACTIVE", "REJECTED", "CLOSED", "RESTRICTED", "PENDING")
POSITION_FIELDS = ("id", "cusip", "symbol", "description", "long_short_indicator", "currency_code", "quantity",
                   "as_of_date", "security_type", "market_value")
LOT_FIELDS = ("id", "cusip", "symbol", "description", "quantity", "market_value", "cost_basis_unadjusted",
              "cost_basis_adjusted", "unrealized_gain_loss", "as_of_date", "open_date")
TRANSACTION_FIELDS = ("id", "cusip", "symbol", "security_type", "quantity", "price", "amount", "interest",
                      "currency_code", "transaction_type", "transaction_subtype", "transaction_description",
                      "cancellation_reversal_flag", "transaction_fees", "broker_fees", "other_fees", "TEFRA_withheld",
                      "state_tax_withheld", "as_of_date", "transaction_date", "settle_date")
TOKEN_FIELDS = ("access_token", "refresh_token", "expires_in", "token_type")
#: the ASSUMED orders / transfers lists answer in ONE response (contract §8 / §9) — none of these keys
PAGINATION_KEYS = ("next", "cursor", "next_cursor", "has_more", "links", "page", "total_pages")
ISO_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOC_DATE_RX = re.compile(r"^\d{4}/\d{2}/\d{2}$")
NUM_RX = re.compile(r"^-?\d+(\.\d+)?$")
SIM_HEADER = "x-counterparty-simulated"
#: the label every orders / transfers row carries — not part of Altruist's published API
ASSUMED = "ASSUMED (not published — Realtime / FIX)"
DEFAULT_REDIRECT = "https://conformance.hollisworks.invalid/altruist/callback"


class Suite:
    def __init__(self):
        self.results: list[dict] = []

    @staticmethod
    def _label(cid: str, contract: str) -> str:
        """ALTREAD1 D5 — the orders (O…) and transfers (T…) groups test ASSUMED contracts: say so on every row.
        cpsim05 — the fix (F…) group carries its own label: simulated FIX, no Altruist FIX contract seen."""
        if cid[:1] == "F":
            return contract if contract.startswith(FIX_CONTRACT) else f"{FIX_CONTRACT} {contract}"
        if cid[:1] in ("O", "T") and not contract.startswith(ASSUMED):
            return f"{ASSUMED} {contract}"
        return contract

    def check(self, passed, cid: str, contract: str, label: str, detail: str = "") -> bool:
        ok = bool(passed)
        self.results.append({"id": cid, "contract": self._label(cid, contract), "label": label, "passed": ok,
                             "detail": "" if ok else str(detail)[:400]})
        return ok

    def na(self, cid: str, contract: str, label: str, why: str) -> None:
        self.results.append({"id": cid, "contract": self._label(cid, contract), "label": label, "passed": None,
                             "detail": why})

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
              redirect_uri: str = DEFAULT_REDIRECT, scope: str = AO.DEFAULT_SCOPE, timeout: float = 15.0,
              expect_simulated_header: bool | None = None, max_accounts: int = 25, orders: dict | None = None,
              transfers: dict | None = None, today: date | None = None, lookback_days: int = 400,
              fix: dict | None = None) -> dict:
    """Run every check. Returns ``{"target", "passed", "failed", "results": [...]}``. Never raises for a server
    defect — a defect is a failed check; a client-side refusal (D1) is a failed check too. ``today`` (the target's
    business date when known) ends the transactions window checked by C14b; it starts ``lookback_days`` earlier."""
    S = Suite()
    end = today or date.today()
    window = (end - timedelta(days=lookback_days), end)
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

    # ── AUTH (DOCUMENTED — docs/open-api-authentication) ────────────────────────────────────────────────────────
    try:
        state0 = secrets.token_urlsafe(32)
        built = AO.build_authorize_url(environment=environment, state=state0, scope=scope, hosts=hosts,
                                       credentials=(client_id, secret), redirect_uri=redirect_uri)
        q = parse_qs(urlsplit(built).query)
        S.check(built.startswith(f"{login}/api/oauth2/authorize?") and q.get("scope") == ["openid"]
                and q.get("state") == [state0] and q.get("client_id") == [client_id]
                and q.get("redirect_uri") == [redirect_uri], "C00", f"{DOC_AUTH} (authorize URL)",
                "the client's authorize URL: the login host + /api/oauth2/authorize, scope openid, client_id, "
                "redirect_uri, state", f"path {urlsplit(built).path}, scope {q.get('scope')}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C00", f"{DOC_AUTH} (authorize URL)", "the client's authorize URL", f"{type(exc).__name__}: {exc}")
    try:
        res, state = await authorize()
        code, echoed = code_of(res)
        S.check(res.status_code == 302 and (res.headers.get("location") or "").startswith(redirect_uri) and code
                and echoed == state, "C01", f"{DOC_AUTH} (authorize)",
                "authorize: 302 to redirect_uri with a code and the state echoed",
                f"status {res.status_code}, location starts ok: "
                f"{(res.headers.get('location') or '').startswith(redirect_uri)}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C01", f"{DOC_AUTH} (authorize)", "authorize: 302 to redirect_uri with a code and the state "
                "echoed", f"{type(exc).__name__}: {exc}")
        code = ""
    try:
        bad, _ = await authorize("00000000-0000-0000-0000-000000000000")
        S.check(bad.status_code >= 400 and "location" not in bad.headers, "C02", f"{DOC_AUTH} (client_id)",
                "authorize: an unknown client_id is refused (≥ 400, no redirect)", f"status {bad.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C02", f"{DOC_AUTH} (client_id)", "authorize: an unknown client_id is refused",
                f"{type(exc).__name__}: {exc}")

    tokens = None
    try:
        tokens = await AO.exchange_code_for_tokens(environment=environment, code=code, hosts=hosts,
                                                   transport=transport, credentials=credentials,
                                                   redirect_uri=redirect_uri, timeout=timeout)
        S.check(bool(tokens.access_token and tokens.refresh_token and tokens.expires_in > 0)
                and AO.oauth_url(hosts, "token") == f"{api}/oauth2/token", "C03", f"{DOC_AUTH} (token)",
                "token (authorization_code): the client posts to the API host + /oauth2/token and parses the response")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C03", f"{DOC_AUTH} (token)", "token (authorization_code): the client's exchange parses the "
                "response", f"{type(exc).__name__}: {exc}")
    try:
        await AO.exchange_code_for_tokens(environment=environment, code=code, hosts=hosts, transport=transport,
                                          credentials=credentials, redirect_uri=redirect_uri, timeout=timeout)
        S.check(False, "C04", f"{DOC_AUTH} (token)", "token: a used authorization code is refused",
                "a used code was accepted")
    except AO.AltruistOAuthError:
        S.check(True, "C04", f"{DOC_AUTH} (token)", "token: a used authorization code is refused")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C04", f"{DOC_AUTH} (token)", "token: a used authorization code is refused",
                f"{type(exc).__name__}")
    try:
        res2, _s = await authorize()
        code2, _e = code_of(res2)
        form = {"grant_type": "authorization_code", "code": code2, "redirect_uri": redirect_uri,
                "client_id": client_id, "client_secret": secret, "token_type": "id"}
        r = await raw("POST", f"{api}/oauth2/token", data=form, headers={"Accept": "application/json"})
        body = r.json() if r.status_code < 400 else {}
        S.check(r.status_code == 200 and all(k in body for k in TOKEN_FIELDS)
                and isinstance(body.get("expires_in"), int), "C05", f"{DOC_AUTH} (token response)",
                "token response (POST {api}/oauth2/token, token_type=id) carries access_token, refresh_token, "
                "expires_in (int), token_type", f"status {r.status_code}, keys {sorted(body)}")
        if sim:
            res3, _s3 = await authorize()
            code3, _e3 = code_of(res3)
            r3 = await raw("POST", f"{api}/oauth2/token", data={**form, "code": code3, "token_type": ""},
                           headers={"Accept": "application/json"})
            S.check(r3.status_code == 400, "C05b", f"{DOC_AUTH} (token_type=id)",
                    "the simulator refuses a token request without token_type=id (the documented body)",
                    f"status {r3.status_code}")
        else:
            S.na("C05b", f"{DOC_AUTH} (token_type=id)", "token_type=id enforced", "simulator-only strictness")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C05", f"{DOC_AUTH} (token response)", "token response primary fields",
                f"{type(exc).__name__}: {exc}")
    try:
        await AO.exchange_code_for_tokens(environment=environment, code="cpsim_code_" + "0" * 40, hosts=hosts,
                                          transport=transport, credentials=(client_id, secret + "x"),
                                          redirect_uri=redirect_uri, timeout=timeout)
        S.check(False, "C06", f"{DOC_AUTH} (client_secret)", "token: a wrong client_secret is refused", "accepted")
    except AO.AltruistOAuthError:
        S.check(True, "C06", f"{DOC_AUTH} (client_secret)", "token: a wrong client_secret is refused")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C06", f"{DOC_AUTH} (client_secret)", "token: a wrong client_secret is refused",
                f"{type(exc).__name__}")

    access = tokens.access_token if tokens else ""
    if tokens:
        try:
            fresh = await AO.refresh_access_token(environment=environment, refresh_token=tokens.refresh_token,
                                                  hosts=hosts, transport=transport, credentials=credentials,
                                                  timeout=timeout, redirect_uri=redirect_uri)
            S.check(bool(fresh.access_token and fresh.access_token != tokens.access_token), "C07",
                    f"{DOC_AUTH} (refresh_token grant)",
                    "refresh (grant_type refresh_token, token_type=id): a new access token")
            access = fresh.access_token
            if sim:
                try:
                    await AO.refresh_access_token(environment=environment, refresh_token=tokens.refresh_token,
                                                  hosts=hosts, transport=transport, credentials=credentials,
                                                  timeout=timeout)
                    S.check(False, "C08", "(simulator) refresh rotation", "refresh: a spent refresh token is refused",
                            "accepted twice")
                except AO.AltruistOAuthError:
                    S.check(True, "C08", "(simulator) refresh rotation", "refresh: a spent refresh token is refused")
            else:
                S.na("C08", "(rotation)", "refresh rotation", "Altruist does not document whether refresh tokens rotate")
        except Exception as exc:  # noqa: BLE001
            S.check(False, "C07", f"{DOC_AUTH} (refresh_token grant)", "refresh", f"{type(exc).__name__}: {exc}")
    else:
        S.check(False, "C07", f"{DOC_AUTH} (refresh_token grant)", "refresh", "no tokens from C03")

    # ── READ PATH (DOCUMENTED — docs/endpoints, the resource pages, docs/json-code-examples) ────────────────────────
    headers = {"Authorization": f"Bearer {access}", "Accept": "application/json"}

    async def raw_get(path: str, params: dict | None = None) -> tuple[httpx.Response, Any]:
        r = await raw("GET", f"{api}{path}", params=params, headers=headers)
        try:
            return r, r.json()
        except ValueError:
            return r, None

    def envelope_ok(body, key: str, echo: tuple[str, str] | None = None) -> str:
        """'' when the body is the documented envelope, else what is wrong."""
        if not isinstance(body, dict):
            return f"body is {type(body).__name__}"
        pg, data = body.get("paging"), body.get("data")
        if not isinstance(pg, dict) or not all(isinstance(pg.get(k), int) for k in ("page", "page_size",
                                                                                     "total_pages")):
            return f"paging {pg!r} lacks page / page_size / total_pages"
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            return f"data.{key} is not a list (keys {sorted(body)})"
        if echo and str(data.get(echo[0])) != str(echo[1]):
            return f"data.{echo[0]} = {data.get(echo[0])!r}, expected {echo[1]!r}"
        return ""

    def dates_ok(rows: list[dict], fields: tuple[str, ...]) -> str:
        for x in rows:
            for f in fields:
                v = x.get(f)
                if v is None:
                    continue
                if not (isinstance(v, str) and DOC_DATE_RX.match(v)):
                    return f"{x.get('id')}.{f} = {v!r} is not YYYY/MM/DD"
                try:
                    AO.parse_altruist_date(v)
                except ValueError as exc:
                    return str(exc)
        return ""

    def fields_ok(rows: list[dict], fields: tuple[str, ...], nums: tuple[str, ...]) -> str:
        for x in rows:
            missing = [k for k in fields if k not in x]
            if missing:
                return f"{x.get('id')}: missing {missing}"
            if not (isinstance(x.get("id"), str) and x["id"]):
                return "a row without an explicit string id"
            bad = [k for k in nums if not _num_ok(x.get(k))]
            if bad:
                return f"{x.get('id')}: not a decimal {bad}"
        return ""

    hh_items, accounts_all = [], []
    try:
        hh_items = await AO.fetch_resource(api_base=api, resource="households", access_token=access, timeout=timeout,
                                           transport=transport)
        r, body = await raw_get(AO.HOUSEHOLDS_PATH, {"page": 0, "page_size": 1000})
        why = envelope_ok(body, "households") or fields_ok((body.get("data") or {}).get("households") or [],
                                                            HOUSEHOLD_FIELDS, ())
        rows = ((body or {}).get("data") or {}).get("households") or []
        S.check(r.status_code == 200 and not why and rows and len(rows) == len(hh_items), "C09",
                f"{DOC_ENDPOINTS} households (GET {AO.HOUSEHOLDS_PATH})",
                'households: {"paging": {page, page_size, total_pages}, "data": {"households": [{id, name}]}}, '
                "non-empty, the client reads every one", f"status {r.status_code}: {why}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C09", f"{DOC_ENDPOINTS} households", "households", f"{type(exc).__name__}: {exc}")

    # paging: several pages at page_size 1, an empty page past the end, the client reads the same set as one big page
    try:
        r0, b0 = await raw_get(AO.HOUSEHOLDS_PATH, {"page": 0, "page_size": 1})
        pg = (b0 or {}).get("paging") or {}
        total = pg.get("total_pages") if isinstance(pg.get("total_pages"), int) else 0
        base = 1 if pg.get("page") == 1 else 0
        if total < 2:
            S.na("C10", f"{DOC_JSON} (paging)", "paging over several pages", "the target has fewer than 2 households")
        else:
            seen = []
            for i in range(total):
                _r, bi = await raw_get(AO.HOUSEHOLDS_PATH, {"page": i + base, "page_size": 1})
                seen.extend(x["id"] for x in ((bi or {}).get("data") or {}).get("households") or [])
            _r, past = await raw_get(AO.HOUSEHOLDS_PATH, {"page": total + base, "page_size": 1})
            empty = ((past or {}).get("data") or {}).get("households")
            small = await AO.fetch_pages(api_base=api, resource="households", access_token=access, page_size=1,
                                         timeout=timeout, transport=transport)
            S.check(sorted(seen) == sorted({h["id"] for h in hh_items}) and len(seen) == len(set(seen))
                    and empty == [] and sorted(i["id"] for i in small.items) == sorted(seen)
                    and small.pages >= total, "C10", f"{DOC_JSON} (paging)",
                    "paging: page_size 1 walks total_pages distinct pages (base read from paging.page), the page past "
                    "the end is empty, and the client's paged read returns the same set",
                    f"total {total}, base {base}, seen {len(seen)}, empty {empty!r}, client {len(small.items)}")
        r_big, _b = await raw_get(AO.HOUSEHOLDS_PATH, {"page": 0, "page_size": 1001})
        if sim:
            S.check(r_big.status_code == 400, "C10b", f"{DOC_ENDPOINTS} (page_size max 1000)",
                    "the simulator refuses page_size above the documented maximum", f"status {r_big.status_code}")
        else:
            S.na("C10b", f"{DOC_ENDPOINTS} (page_size max 1000)", "page_size max", "simulator-only strictness")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C10", f"{DOC_JSON} (paging)", "paging", f"{type(exc).__name__}: {exc}")

    acct_ok, acct_detail = True, ""
    for hh in hh_items:
        try:
            items = await AO.fetch_resource(api_base=api, resource="accounts", access_token=access, param=hh["id"],
                                            timeout=timeout, transport=transport)
            r, body = await raw_get("/v2/accounts", {"household_id": hh["id"], "page": 0, "page_size": 1000})
            rows = ((body or {}).get("data") or {}).get("accounts") or []
            why = envelope_ok(body, "accounts", ("household_id", hh["id"])) or fields_ok(
                rows, ACCOUNT_FIELDS, ("account_balance",)) or dates_ok(rows, ("as_of_date", "date_open",
                                                                               "date_closed"))
            if r.status_code != 200 or why or len(rows) != len(items) or any(
                    x.get("status") not in ACCOUNT_STATUSES for x in rows):
                acct_ok, acct_detail = False, f"household {hh['id']}: status {r.status_code}: {why or 'status / count'}"
            accounts_all.extend(items)
        except Exception as exc:  # noqa: BLE001
            acct_ok, acct_detail = False, f"{type(exc).__name__}: {exc}"
    S.check(acct_ok and accounts_all, "C11", f"{DOC_ENDPOINTS} accounts (GET /v2/accounts?household_id=)",
            "accounts: data.household_id + data.accounts with every documented field (id, rep_code, account_number, "
            "account_type, account_name, status ∈ ACTIVE/REJECTED/CLOSED/RESTRICTED/PENDING, date_open, date_closed, "
            "as_of_date, account_balance); the client reads every one", acct_detail or "none")

    pos_ok, pos_detail, lot_ok, lot_detail, txn_ok, txn_detail = True, "", True, "", True, ""
    link_ok, link_detail, date_ok, date_detail, win_ok, win_detail = True, "", True, "", True, ""
    for acct in accounts_all[:max_accounts]:
        aid = acct["id"]
        enc = quote(aid, safe="")
        try:
            p_items = await AO.fetch_resource(api_base=api, resource="positions", access_token=access, param=aid,
                                              timeout=timeout, transport=transport)
            r, body = await raw_get(f"/v2/accounts/{enc}/positions/", {"page": 0, "page_size": 1000})
            rows = ((body or {}).get("data") or {}).get("positions") or []
            why = envelope_ok(body, "positions", ("account_id", aid)) or fields_ok(rows, POSITION_FIELDS,
                                                                                    ("quantity", "market_value"))
            if r.status_code != 200 or why or len(rows) != len(p_items):
                pos_ok, pos_detail = False, f"account {aid}: status {r.status_code}: {why or 'count'}"
                continue
            d = dates_ok(rows, ("as_of_date",))
            if d:
                date_ok, date_detail = False, d
            held = {v for x in rows for v in (x.get("cusip"), x.get("symbol")) if v}
            held |= {f"name:{(x.get('description') or '').lower()}" for x in rows}

            l_items = await AO.fetch_resource(api_base=api, resource="cost_basis", access_token=access, param=aid,
                                              timeout=timeout, transport=transport)
            r, body = await raw_get(f"/v2/accounts/{enc}/cost-basis/", {"page": 0, "page_size": 1000})
            rows_l = ((body or {}).get("data") or {}).get("cost_basis") or []
            why = envelope_ok(body, "cost_basis", ("account_id", aid)) or fields_ok(
                rows_l, LOT_FIELDS, ("quantity", "market_value", "cost_basis_unadjusted", "cost_basis_adjusted",
                                     "unrealized_gain_loss"))
            if r.status_code != 200 or why or len(rows_l) != len(l_items):
                lot_ok, lot_detail = False, f"account {aid}: status {r.status_code}: {why or 'count'}"
            d = dates_ok(rows_l, ("as_of_date", "open_date"))
            if d:
                date_ok, date_detail = False, d

            t_items = await AO.fetch_resource(api_base=api, resource="transactions", access_token=access, param=aid,
                                              timeout=timeout, transport=transport,
                                              query={"date_from": window[0].isoformat(),
                                                     "date_to": window[1].isoformat()})
            r, body = await raw_get(f"/v2/accounts/{enc}/transactions", {
                "page": 0, "page_size": 500, "date_from": window[0].isoformat(), "date_to": window[1].isoformat()})
            rows_t = ((body or {}).get("data") or {}).get("transactions") or []
            why = envelope_ok(body, "transactions", ("account_id", aid)) or fields_ok(
                rows_t, TRANSACTION_FIELDS, ("quantity", "price", "amount", "interest", *AO.FEE_FIELDS,
                                             *AO.TAX_FIELDS))
            if r.status_code != 200 or why or len(rows_t) != len(t_items):
                txn_ok, txn_detail = False, f"account {aid}: status {r.status_code}: {why or 'count'}"
                continue
            for x in rows_t:
                if (x.get("transaction_type") or "").strip().lower() not in APS.TRANSACTION_TYPE_MAP:
                    txn_ok, txn_detail = False, f"transaction {x.get('id')}: type {x.get('transaction_type')!r} " \
                                                f"is not one the client maps"
                key_hits = {x.get("cusip"), x.get("symbol")} - {None, ""}
                if not (key_hits & held or f"name:{(x.get('transaction_description') or '').lower()}" in held
                        or (x.get("security_type") or "").upper() == "CASH"):
                    link_ok, link_detail = False, f"transaction {x.get('id')} names a security with no position"
                td = AO.parse_altruist_date(x.get("transaction_date")) if x.get("transaction_date") else None
                if td is None or td < window[0] or td > window[1]:
                    win_ok, win_detail = False, f"transaction {x.get('id')} on {x.get('transaction_date')} is " \
                                                f"outside {window[0]}..{window[1]}"
            d = dates_ok(rows_t, ("as_of_date", "transaction_date", "settle_date"))
            if d:
                date_ok, date_detail = False, d
            # the default window (no date_from / date_to) is the past month — never more
            _r, dflt = await raw_get(f"/v2/accounts/{enc}/transactions", {"page": 0, "page_size": 500})
            lo = AO.add_months(window[1], -1)
            for x in ((dflt or {}).get("data") or {}).get("transactions") or []:
                td = AO.parse_altruist_date(x.get("transaction_date")) if x.get("transaction_date") else None
                if td is None or td < lo:
                    win_ok, win_detail = False, f"default window served {x.get('transaction_date')} (before {lo})"
        except Exception as exc:  # noqa: BLE001
            pos_ok, pos_detail = False, f"{type(exc).__name__}: {exc}"
    S.check(pos_ok, "C12", f"{DOC_ENDPOINTS} positions (GET /v2/accounts/{{account_id}}/positions/)",
            "positions: data.account_id + data.positions with every documented field (id, cusip, symbol, description, "
            "long_short_indicator, currency_code, quantity, as_of_date, security_type, market_value); the client reads "
            "every one", pos_detail)
    S.check(lot_ok, "C13", f"{DOC_ENDPOINTS} cost basis (GET /v2/accounts/{{account_id}}/cost-basis/)",
            "cost basis: data.cost_basis, one row per tax lot with every documented field (id, cusip, symbol, "
            "description, quantity, market_value, cost_basis_unadjusted, cost_basis_adjusted, unrealized_gain_loss, "
            "as_of_date, open_date)", lot_detail)
    S.check(txn_ok, "C14", f"{DOC_ENDPOINTS} transactions (GET /v2/accounts/{{account_id}}/transactions)",
            "transactions: data.transactions with every documented field (incl. transaction_type, transaction_date, "
            "settle_date, transaction_fees / broker_fees / other_fees, cancellation_reversal_flag); every type one "
            "the client maps", txn_detail)
    S.check(win_ok, "C14b", f"{DOC_ENDPOINTS} transactions (date_from / date_to)",
            "transactions honour date_from / date_to (every row inside the window) and the default window is the past "
            "month", win_detail)
    S.check(link_ok, "C15", "client rule (a transaction needs a position — ALTRUIST_CONTRACT §3.5)",
            "every transacted security also appears in the account's positions (cash rows exempt)", link_detail)
    S.check(date_ok, "C16", f"{DOC_JSON} (dates YYYY/MM/DD)",
            "every response date is YYYY/MM/DD and the client parses it", date_detail)

    try:
        r = await raw("GET", f"{api}{AO.HOUSEHOLDS_PATH}", headers={"Accept": "application/json"})
        r2 = await raw("GET", f"{api}{AO.HOUSEHOLDS_PATH}", headers={"Authorization": "Bearer not-a-token",
                                                                      "Accept": "application/json"})
        S.check(r.status_code == 401 and r2.status_code == 401, "C17", f"{DOC_ENDPOINTS} (Authorization header)",
                "a resource without a valid access token is refused 401", f"{r.status_code}/{r2.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "C17", f"{DOC_ENDPOINTS} (Authorization header)", "Bearer check", f"{type(exc).__name__}")

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

    fskip = fix_group_skip(environment, fix)
    if fskip:
        S.na("F00", FIX_CONTRACT, "fix group", fskip)
    else:
        await _fix_group(S, raw=raw, api=api, access=access, fx=fix)

    if sim:
        missing = [u for u, v in seen_headers if v != "true"]
        S.check(seen_headers and not missing, "C18", "§6 (SIMULATED header)",
                "every simulator response carries X-Counterparty-Simulated: true", f"missing on {missing[:5]}")
    else:
        S.na("C18", "§6 (SIMULATED header)", "SIMULATED header", "not applicable to a real counterparty")
    return {"environment": environment, "passed": S.passed, "failed": S.failed, "results": S.results}


async def _book_rows(raw, api: str, hdr: dict, account: str, tail: str, key: str, size: int) -> list[dict]:
    """The book as the ASSUMED groups observe it — through the DOCUMENTED read path (ALTREAD1): every page of
    /v2/accounts/{id}/<tail> (``data.<key>``); a short or empty page ends the read."""
    rows: list[dict] = []
    for page in range(50):
        r = await raw("GET", f"{api}/v2/accounts/{quote(account, safe='')}{tail}",
                      params={"page": page, "page_size": size}, headers=hdr)
        body = r.json() if r.status_code == 200 else {}
        chunk = ((body or {}).get("data") or {}).get(key) or []
        rows.extend(chunk)
        if len(chunk) < size:
            break
    return rows


async def _book_positions(raw, api: str, hdr: dict, account: str) -> list[dict]:
    return await _book_rows(raw, api, hdr, account, "/positions/", "positions", 1000)


async def _book_transactions(raw, api: str, hdr: dict, account: str) -> list[dict]:
    """The documented default window (the past month) covers the fills / legs a group just made on the clock date."""
    return await _book_rows(raw, api, hdr, account, "/transactions", "transactions", 500)


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
        rp = await _book_positions(raw, api, hdr, acct)
        rt = await _book_transactions(raw, api, hdr, acct)
        qty = sum((Decimal(p["quantity"]) for p in rp if p.get("cusip") == cusip), Decimal(0))
        txn = [t for t in rt if t.get("id") == f"sim-x-{ex.get('exec_id')}"]
        S.check(qty == held + Decimal("2") and len(txn) == 1 and (txn[0].get("transaction_type") or "").lower() == "buy"
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

    from services import altruist_transfers as AT

    tag = secrets.token_hex(4)
    hdr = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
    kw = {"api_base": api, "access_token": access, "timeout": timeout, "transport": transport}
    src, dst, vd = fx["from"], fx["to"], fx["value_date"]
    dom, intl = fx["domestic"], fx["international"]

    async def cash(account: str) -> Decimal:
        return sum((Decimal(p["market_value"]) for p in await _book_positions(raw, api, hdr, account)
                    if not p.get("cusip") and not p.get("symbol") and (p.get("security_type") or "").upper() == "CASH"),
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
        tid = first.get("transfer_id")
        out_ = [t for t in await _book_transactions(raw, api, hdr, src) if t.get("id") == f"{tid}-out"]
        in_ = [t for t in await _book_transactions(raw, api, hdr, dst) if t.get("id") == f"{tid}-in"]
        typ = (lambda t: (t.get("transaction_type") or "").lower())
        mapped = all(typ(t) in APS.TRANSACTION_TYPE_MAP for t in out_ + in_)
        S.check(after_src == before_src - Decimal("100") and after_dst == before_dst + Decimal("100") and len(out_) == 1
                and len(in_) == 1 and typ(out_[0]) == "transfer_out" and typ(in_[0]) == "transfer_in"
                and out_[0].get("amount") == "100" and in_[0].get("amount") == "100" and mapped
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


# ── cpsim05: the fix group (simulated FIX — no Altruist FIX contract has been seen) ─────────────────────────────────
FIX_CONTRACT = "simulated FIX — no Altruist FIX contract seen (FIX 4.4 over HTTP, CPSIM05_NOTES §3)"
NOT_A_FIX_FIXTURE = ("not run: the fix group places FIX orders, moves the book and advances the clock, so it runs only "
                     "against a fixture counterparty (the in-process run)")


def fix_group_skip(environment: str, fix: dict | None) -> str | None:
    """Why the fix group does not run (None → it runs). A real target: there is no Altruist FIX contract at all."""
    if environment != AO.SIMULATOR:
        return "no Altruist FIX contract has been seen — the fix group runs against a simulated counterparty only"
    if fix is None:
        return NOT_A_FIX_FIXTURE
    return None


async def _fix_group(S: Suite, *, raw, api: str, access: str, fx: dict):
    """F01–F19. ``fx`` = {account_id, account_2, cusip, held, sender, target, behaviour(async fn(dict)),
    advance(async fn())} — the behaviour / clock callbacks are the runner's (a fixture counterparty's admin acts).
    Every message is built and every reply parsed by the CLIENT's own codec (services.fix_sim_client), so this group
    also proves the two independent codecs (client / simulator) agree on BodyLength and CheckSum."""
    from services import fix_sim_client as FC

    sec = {"identifiers": {"cusip": fx["cusip"]}}
    hdr = {"Authorization": f"Bearer {access}", "Content-Type": "text/plain"}
    seq = {"n": 0}
    tag = secrets.token_hex(3)

    async def post(msg: str, headers=None) -> tuple[int, list]:
        r = await raw("POST", f"{api}/fix", content=msg.encode("utf-8"), headers=headers or hdr)
        return r.status_code, [line for line in r.text.split("\n") if line.strip()] if r.status_code == 200 else []

    async def send(mt: str, fields, *, delim=FC.PIPE, sender=None) -> tuple[int, list, list]:
        seq["n"] += 1
        msg = FC.build(mt, fields, sender=sender or fx["sender"], target=fx["target"], seq=seq["n"], delim=delim)
        code, lines = await post(msg)
        return code, lines, [FC.parse(x) for x in lines]

    def last(replies, tag_):
        return FC.first(replies[-1], tag_) if replies else None

    def nos(cl, side="buy", qty="2", **kw):
        return FC.new_order_single(cl_ord_id=cl, account=kw.pop("account", fx["account_id"]), side=side, sec=sec,
                                   quantity=qty, **kw)

    await fx["behaviour"]({"default_outcome": "accept"})
    buy_id = f"fixconf-{tag}-buy"
    try:
        code, lines, rep = await send("D", nos(buy_id))
        s1 = FC.summarize(rep)
        swapped = all(FC.first(f, 49) == fx["target"] and FC.first(f, 56) == fx["sender"] for f in rep)
        S.check(code == 200 and len(rep) >= 2 and FC.first(rep[0], 150) == "0" and len(s1["trades"]) == 1
                and s1["status"] == "filled" and Decimal(s1["trades"][0]["price"]) > 0 and swapped
                and s1["order_id"].startswith("sim-o-"), "F01", "D → 8",
                "NewOrderSingle (market) → ExecutionReport New (150=0) then one Trade (150=F, 39=2) at the platform "
                "close; every reply passes the client's BodyLength + CheckSum; 49/56 swapped back",
                f"{code} {lines[:3]}")
        exec_id = s1["trades"][0]["exec_id"] if s1["trades"] else None
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F01", "D → 8", "NewOrderSingle", f"{type(exc).__name__}: {exc}")
        s1, exec_id = {"order_id": None, "trades": []}, None
    try:
        code, lines, rep = await send("D", nos(buy_id, qty="7"))
        s2 = FC.summarize(rep)
        S.check(code == 200 and s2["order_id"] == s1["order_id"] and [t["exec_id"] for t in s2["trades"]] == [exec_id]
                and last(rep, 150) == "I", "F02", "D → 8 (idempotent ClOrdID)",
                "a repeated ClOrdID (a different quantity) → the SAME order's reports + a 150=I status, never a second "
                "order", f"{lines[:3]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F02", "D → 8 (idempotent ClOrdID)", "repeat", f"{type(exc).__name__}: {exc}")
    try:
        code, lines, rep = await send("H", FC.status_request(cl_ord_id=buy_id, order_id=s1["order_id"], side="buy"))
        s3 = FC.summarize(rep)
        S.check(code == 200 and s3["order_id"] == s1["order_id"] and len(s3["trades"]) == 1 and last(rep, 150) == "I"
                and s3["status"] == "filled", "F03", "H → 8", "OrderStatusRequest → every trade report + 150=I status",
                f"{lines[:3]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F03", "H → 8", "status", f"{type(exc).__name__}: {exc}")
    lim_id = f"fixconf-{tag}-lim"
    try:
        code, lines, rep = await send("D", nos(lim_id, price_kind="limit", limit_price="0.01"))
        s4 = FC.summarize(rep)
        S.check(code == 200 and len(rep) == 1 and FC.first(rep[0], 39) == "0" and not s4["trades"], "F04",
                "D → 8 (limit)", "an unmarketable limit → New (39=0) only, nothing filled", f"{lines[:3]}")
        code, lines, rep = await send("F", FC.cancel_request(cl_ord_id=lim_id + "-x", orig_cl_ord_id=lim_id,
                                                             side="buy", sec=sec, quantity="2"))
        ok1 = code == 200 and FC.first(rep[0], 35) == "8" and FC.first(rep[0], 150) == "4" and \
            FC.first(rep[0], 39) == "4"
        code, lines, rep = await send("F", FC.cancel_request(cl_ord_id=lim_id + "-y", orig_cl_ord_id=lim_id,
                                                             side="buy", sec=sec, quantity="2"))
        S.check(ok1 and FC.first(rep[0], 35) == "9" and FC.first(rep[0], 102) == "0", "F05", "F → 8 / 9",
                "OrderCancelRequest of a working order → 150=4 / 39=4; a second cancel → OrderCancelReject 102=0",
                f"{lines[:2]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F05", "F → 8 / 9", "cancel", f"{type(exc).__name__}: {exc}")
    try:
        code, lines, rep = await send("F", FC.cancel_request(cl_ord_id=f"fixconf-{tag}-u", orig_cl_ord_id="nope-" + tag,
                                                             side="buy", sec=sec, quantity="1"))
        S.check(code == 200 and FC.first(rep[0], 35) == "9" and FC.first(rep[0], 102) == "1", "F06", "F → 9",
                "cancelling an unknown order → OrderCancelReject 102=1", f"{lines[:2]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F06", "F → 9", "unknown cancel", f"{type(exc).__name__}: {exc}")
    try:
        code, lines, rep = await send("D", nos(f"fixconf-{tag}-over", side="sell", qty="1000000"))
        s7 = FC.summarize(rep)
        S.check(code == 200 and s7["status"] == "rejected" and "insufficient position" in (s7["reject_reason"] or ""),
                "F07", "D → 8 (rejected)", "an oversell → 39=8 with 58 'insufficient position'", f"{lines[:2]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F07", "D → 8 (rejected)", "oversell", f"{type(exc).__name__}: {exc}")
    try:
        f8 = [(t, ("5" if t == 54 else v)) for t, v in nos(f"fixconf-{tag}-short", side="sell", qty="1")]
        code, lines, rep = await send("D", f8)
        S.check(code == 200 and FC.first(rep[0], 39) == "8" and FC.first(rep[0], 37) == "NONE"
                and "no shorting" in (FC.first(rep[0], 58) or ""), "F08", "D → 8 (54=5)",
                "SELL SHORT (54=5) → rejected 'no shorting declared', no order created (OrderID NONE)", f"{lines[:2]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F08", "D → 8 (54=5)", "short", f"{type(exc).__name__}: {exc}")

    async def reject_of(msg: str) -> list:
        code, lines = await post(msg)
        return [FC.parse(x) for x in lines] if code == 200 else []
    good = FC.build("H", [(11, buy_id)], sender=fx["sender"], target=fx["target"], seq=900, delim=FC.PIPE)
    cs = good.split("|10=")[1][:3]
    try:
        rep = await reject_of(good.replace(f"|10={cs}", f"|10={(int(cs) + 1) % 256:03d}"))
        S.check(len(rep) == 1 and FC.first(rep[0], 35) == "3" and FC.first(rep[0], 371) == "10"
                and "CheckSum" in (FC.first(rep[0], 58) or ""), "F09", "3 (CheckSum)",
                "a wrong CheckSum → Reject (35=3) naming tag 10, HTTP 200 (never a 500)", f"{rep[:1]}")
        n9 = good.split("|9=")[1].split("|")[0]
        rep = await reject_of(good.replace(f"|9={n9}|", f"|9={int(n9) + 3}|", 1))
        S.check(len(rep) == 1 and FC.first(rep[0], 35) == "3" and FC.first(rep[0], 371) == "9", "F10", "3 (BodyLength)",
                "a wrong BodyLength → Reject naming tag 9", f"{rep[:1]}")
        _, _, rep = await send("H", [(11, buy_id)], sender="SOMEONE_ELSE")
        S.check(len(rep) == 1 and FC.first(rep[0], 35) == "3" and FC.first(rep[0], 373) == "9", "F11", "3 (CompID)",
                "an unknown SenderCompID → Reject 373=9 (CompID problem)", f"{rep[:1]}")
        _, _, rep = await send("D", [(t, v) for t, v in nos(f"fixconf-{tag}-miss") if t != 54])
        S.check(len(rep) == 1 and FC.first(rep[0], 35) == "3" and FC.first(rep[0], 371) == "54"
                and FC.first(rep[0], 373) == "1", "F12", "3 (required tag)",
                "a NewOrderSingle without Side (54) → Reject 373=1 naming tag 54", f"{rep[:1]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F09", "3", "Reject", f"{type(exc).__name__}: {exc}")
    try:
        code, lines, rep = await send("H", FC.status_request(cl_ord_id=buy_id), delim=FC.SOH)
        S.check(code == 200 and lines and all(FC.SOH in x and "|" not in x for x in lines) and
                FC.summarize(rep)["order_id"] == s1["order_id"], "F13", "delimiters",
                "SOH and '|' both accepted; the reply uses the request's delimiter", f"{[x[:40] for x in lines[:1]]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F13", "delimiters", "SOH", f"{type(exc).__name__}: {exc}")
    try:
        await fx["behaviour"]({"default_outcome": "partial", "partial_pct": 50})
        pid = f"fixconf-{tag}-part"
        _, lines, rep = await send("D", nos(pid, qty="4"))
        a = FC.summarize(rep)
        await fx["behaviour"]({"default_outcome": "delay", "delay_seconds": 0})
        did = f"fixconf-{tag}-delay"
        _, lines2, rep2 = await send("D", nos(did, qty="2"))
        b = FC.summarize(rep2)
        await fx["advance"]()
        _, _, rp = await send("H", FC.status_request(cl_ord_id=pid))
        _, _, rd = await send("H", FC.status_request(cl_ord_id=did))
        pa, da = FC.summarize(rp), FC.summarize(rd)
        S.check(a["status"] == "partially_filled" and len(a["trades"]) == 1 and pa["status"] == "filled"
                and len(pa["trades"]) == 2 and sum(Decimal(t["quantity"]) for t in pa["trades"]) == 4, "F14",
                "D → 8 (partial, advance)", "partial → 39=1 with one trade; after a clock advance the rest fills "
                "(39=2, two trades totalling the order)", f"{lines[:2]} {pa}")
        S.check(b["status"] == "new" and not b["trades"] and da["status"] == "filled" and len(da["trades"]) == 1,
                "F15", "D → 8 (delay, advance)", "delay → New (39=0), nothing filled; filled after a clock advance",
                f"{lines2[:2]} {da}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F14", "D → 8 (partial / delay)", "advance", f"{type(exc).__name__}: {exc}")
    finally:
        await fx["behaviour"]({"default_outcome": "accept"})
    try:
        bid = f"fixconf-{tag}-blk"
        _, lines, rep = await send("D", nos(bid, qty="2", account="CUSTODIAN-DESK", block=True))
        bs = FC.summarize(rep)
        legs = [(fx["account_id"], "1"), (fx["account_2"], "1")]
        alloc = FC.allocation_instruction(alloc_id=bid + "-a", cl_ord_id=bid, order_id=bs["order_id"], side="buy",
                                          sec=sec, quantity="2", avg_price=bs["trades"][0]["price"],
                                          trade_date="20261111", legs=legs)
        _, _, ra = await send("J", alloc)
        ack = FC.summarize(ra)["alloc"] or {}
        _, _, ra2 = await send("J", alloc)
        ack2 = FC.summarize(ra2)["alloc"] or {}
        S.check(bs["status"] == "filled" and ack.get("accepted") and set(ack.get("legs", {})) == {a for a, _ in legs}
                and all(v.startswith("sim-a-") for v in ack["legs"].values()) and ack2.get("accepted")
                and ack2.get("legs") == ack.get("legs"), "F16", "D (block) + J → P",
                "a block order (591) fills without an account; AllocationInstruction → Ack 87=0 naming each "
                "account's booked transaction (467); a repeat AllocID → the same Ack, nothing booked twice",
                f"{lines[:2]} {ack} {ack2}")
        hdr_ = {"Accept": "application/json"}
        moved = []
        for a, _ in legs:
            moved.append(any(t.get("external_id", t.get("id")) == ack["legs"][a] or ack["legs"][a] in json.dumps(t)
                             for t in await _book_transactions(raw, api, {**hdr_, "Authorization": f"Bearer {access}"},
                                                              a)))
        S.check(all(moved), "F17", "J → book", "each allocation leg is in its account's transactions feed",
                f"{moved}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F16", "D (block) + J → P", "block", f"{type(exc).__name__}: {exc}")
    try:
        r = await raw("POST", f"{api}/fix", content=b"8=FIX.4.4|", headers={"Content-Type": "text/plain"})
        S.check(r.status_code == 401, "F18", "auth", "the FIX endpoint needs an ACCESS token (none → 401)",
                f"{r.status_code}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F18", "auth", "401", f"{type(exc).__name__}: {exc}")
    try:
        code, lines = await post("this is not FIX")
        rep = [FC.parse(x) for x in lines]
        S.check(code == 200 and len(rep) == 1 and FC.first(rep[0], 35) == "3", "F19", "3 (malformed)",
                "a body that is not FIX at all → a Reject (35=3), HTTP 200 — never a 500", f"{code} {lines[:1]}")
    except Exception as exc:  # noqa: BLE001
        S.check(False, "F19", "3 (malformed)", "garbage", f"{type(exc).__name__}: {exc}")
