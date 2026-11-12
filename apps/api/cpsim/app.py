"""cpsim01 Task 3 — the simulator's own FastAPI sub-app, mounted at /cpsim on the existing API (main.py).

    GET  /cpsim/health                 no auth; {"status": "ok", "simulated": true}

cpsim02 → ALTREAD1 — the Altruist-compatible read API at Altruist's PUBLISHED paths and shapes (developer.altruist.com,
read 2026-11-11; contract §2–§3, §6), under /cpsim/c/{slug} (which is both the login and the API base):
    GET  …/api/oauth2/authorize        302 → redirect_uri?code=…&state=… (cpsim/oauth.py); scope openid
    POST …/oauth2/token                form body; authorization_code / refresh_token; token_type=id; the cpsim_ key in
                                       the BODY
    GET  …/api/v2/households?name_pattern=                          data.households  [{id, name}]
    GET  …/v2/accounts?household_id=                                data.accounts    (+ data.household_id)
    GET  …/v2/accounts/{account_id}/positions/                      data.positions   (+ data.account_id) — the latest
                                                                    snapshot on or before the clock
    GET  …/v2/accounts/{account_id}/cost-basis/                     data.cost_basis  — one row per tax lot
    GET  …/v2/accounts/{account_id}/transactions?date_from=&date_to= data.transactions — default the past month, never
                                                                    after the clock
  Every list is PAGED: ``page`` (default 0) + ``page_size`` (default 100; max 1000, transactions 500 — over the max →
  400) → ``{"paging": {page, page_size, total_pages}, "data": {...}}``. Page base 0 by default; ``set_page_base(1)`` is a
  test injection (a page-0 request is then served as page 1 and echoed as 1 — the docs' own example). Dates
  YYYY/MM/DD. Prior-business-day data is the book on the counterparty's clock date.

cpsim03 — the orders API (contract §8, every field ASSUMED), same access-token auth:
    POST   …/v2/orders                201 a new order / 200 the SAME order on a repeat client_order_id; 422 malformed
    GET    …/v2/orders?account_id=    {"orders": [...]}
    GET    …/v2/orders/{order_id}     {"order": {...}}
    DELETE …/v2/orders/{order_id}     cancel while working; 409 once filled / rejected / cancelled
  The outcome comes from the behaviour row (cpsim/orders.py, D6); fills move the book (D7); order webhooks (D9).

cpsim05 — FIX 4.4 over HTTP (docs/cpsim/CPSIM05_NOTES.md D1 / D2), same access-token auth:
    POST   …/fix                          ONE tag=value message (SOH or '|'); the reply message(s), newline separated.
                                          D NewOrderSingle / F OrderCancelRequest / H OrderStatusRequest /
                                          J AllocationInstruction → 8 / 9 / P; malformed → 3 Reject (HTTP 200).
                                          Outcomes are cpsim03's (cpsim/orders.py); every message logged (cpsim/fix.py).

cpsim04 — the transfers API (contract §9, every field ASSUMED), same access-token auth:
    POST   …/v2/transfers                 201 a new transfer / 200 the SAME transfer on a repeat client_transfer_id
    GET    …/v2/transfers?account_id=     {"transfers": [...]} — either leg; events loaded in ONE query
    GET    …/v2/transfers/{transfer_id}   {"transfer": {...}}
    DELETE …/v2/transfers/{transfer_id}   cancel while received / pending_consent; 409 otherwise
  The lifecycle (cpsim/transfers.py): behaviour reject / consent / delay at receipt, the receiving bank's check, the
  cash ledger (cpsim/ledger.py), settlement and ACH returns on the clock; ``transfer.updated`` webhooks after commit.
  Resources take an ACCESS token (``Bearer cpsim_at_…``); the cpsim_ key authenticates only the token endpoint.
  Orders / transfers (ASSUMED — not part of Altruist's published API) keep their cpsim03 / cpsim04 shapes. Behaviour (D9): reject → 503 with the reason;
  delay → sleep min(delay_seconds, MAX_DELAY_S) before answering; partial / ACH do not apply to reads.

cpsim06 — the Realtime-API-shaped ACCOUNT OPENING (docs/cpsim/CPSIM06_NOTES.md D1–D3; every path / field ASSUMED):
    POST   …/realtime/realms/altruist/protocol/openid-connect/token   password grant / refresh_token (form body):
                                          access 5 min; refresh on a FIXED 10 h window, the original stays valid
    POST   …/realtime/api/households                 create, or reuse by exact name (the book's own household)
    POST   …/realtime/api/contacts                   create / restate (client_reference); tax id → last four + salted
                                                     hash; no date of birth stored
    POST   …/realtime/api/account-drafts             create / restate while a draft (client_reference)
    GET    …/realtime/api/account-drafts/{id}
    POST   …/realtime/api/account-drafts/{id}/validate   every problem; opens nothing
    POST   …/realtime/api/account-drafts/{id}/submit     in good order → OPENED at once (ACTIVE, date_open = the clock,
                                                     CPSIM number, in the book + the /v2/accounts feed); repeat → same
  Realtime access tokens (``Bearer cpsim_rtat_…``) only; RateLimit-* headers on every answer; 429 + Retry-After only by
  the injected test behaviour (cpsim.realtime.force_429).

    POST /cpsim/c/{slug}/mirror        Authorization: Bearer cpsim_live_…  — a properly formatted envelope in, the
                                       mirrored message out (same message_id, mirrored: true, received_at from the
                                       counterparty's clock); malformed → 422 with the schema errors; unknown slug →
                                       404; missing / wrong / revoked key, or a counterparty that is not active → 401.

Its OWN auth (cpsim/auth.py — its fake keys, never a Hollisworks session; main.py's auth middleware passes /cpsim
through untouched). Every response — success, refusal, 404, 405 — carries ``X-Counterparty-Simulated: true`` and every
request is written to ``cpsim.request_log`` (no bodies, no keys) by the ASGI wrapper below.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from cpsim import SIMULATED_HEADER, SIMULATED_VALUE
from cpsim import auth as A
from cpsim import db as DB
from cpsim import book as B
from cpsim import mirror as M
from cpsim import oauth as O
from cpsim import orders as SO
from cpsim import registry as R
from cpsim import transfers as ST

LOG_KEY = "cpsim.log"
MAX_DELAY_S = 20            # D9: a delay over the client's 15 s timeout exercises it; never longer than this
#: ALTREAD1 — the documented page_size maxima per resource kind; the documented default page size
PAGE_SIZE_MAX = {"households": 1000, "accounts": 1000, "positions": 1000, "cost_basis": 1000, "transactions": 500}
PAGE_SIZE_DEFAULT = 100
_page_base = 0


def set_page_base(base: int) -> None:
    """Test injection: serve pages 0-based (default) or 1-based (the docs' example echoes page 1)."""
    global _page_base
    if base not in (0, 1):
        raise ValueError("page base is 0 or 1")
    _page_base = base


def page_base() -> int:
    return _page_base


REFUSALS = {**A.REFUSALS,
            "token_unknown": "unknown access token", "token_expired": "the access token has expired",
            "token_revoked": "the access token has been revoked"}


def _out(status: int, body: dict, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={**body, "simulated": True}, headers=headers or {})


class SimulatedMarker:
    """Pure ASGI wrapper: stamps the SIMULATED header on EVERY response and writes one request_log row per request
    (counterparty / auth outcome / shape filled in by the handler through ``scope['cpsim.log']``)."""

    def __init__(self, app):
        self.app = app          # the inner FastAPI (scripts/authz1_routes.py lists its routes through this)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope[LOG_KEY] = {"auth_outcome": "not_required"}
        status = {"code": 500}

        async def send_marked(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                hdrs = [(k, v) for k, v in message.get("headers", []) if k.lower() != SIMULATED_HEADER.lower().encode()]
                hdrs.append((SIMULATED_HEADER.lower().encode(), SIMULATED_VALUE.encode()))
                message = {**message, "headers": hdrs}
            await send(message)

        try:
            await self.app(scope, receive, send_marked)
        finally:
            await _write_log(scope, status["code"])


async def _write_log(scope, status: int) -> None:
    entry = scope.get(LOG_KEY) or {}
    try:
        async with DB.tx() as conn:
            await R.log_request(conn, counterparty_id=entry.get("counterparty_id"), slug=entry.get("slug"),
                                method=scope.get("method", "GET"), path=entry.get("path") or scope.get("path", "/"),
                                status=status, auth_outcome=entry.get("auth_outcome", "not_required"),
                                shape_valid=entry.get("shape_valid"), error_count=entry.get("error_count", 0),
                                message_type=entry.get("message_type"))
    except Exception as exc:  # noqa: BLE001 — logging never changes the answer; the failure is printed, never the key
        print(f"[cpsim] request_log write failed: {type(exc).__name__}")


def create_app() -> FastAPI:
    sub = FastAPI(title="Counterparty Simulator — SIMULATED", docs_url=None, redoc_url=None, openapi_url=None)

    @sub.get("/health")
    async def health():
        return _out(200, {"status": "ok", "service": "cpsim"})

    @sub.post("/c/{slug}/mirror")
    async def mirror_endpoint(slug: str, request: Request):
        log = request.scope.setdefault(LOG_KEY, {})
        log.update({"slug": slug[:64], "path": f"/cpsim/c/{slug[:64]}/mirror"})
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                outcome = await R.authenticate(conn, cp, A.bearer(request.headers.get("authorization")))
                log["auth_outcome"] = outcome
                if outcome != "ok":
                    return _out(401, {"detail": A.REFUSALS.get(outcome, "not authorised")},
                                {"WWW-Authenticate": 'Bearer realm="cpsim"'})
                typ = await R.institution_type(conn, cp["institution_type"])
                bdate = await R.business_date(conn, cp["id"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        raw = await request.body()
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            doc = None
            errors = [{"path": "$", "message": "body is not JSON"}]
        else:
            errors = M.validate(M.load_schema(typ["envelope_schema"]), doc) if doc is not None else [
                {"path": "$", "message": "body is required"}]
        log["shape_valid"] = not errors
        log["error_count"] = len(errors)
        if isinstance(doc, dict) and isinstance(doc.get("message_type"), str):
            log["message_type"] = doc["message_type"][:80]
        if errors:
            return _out(422, {"detail": "malformed message", "errors": errors})
        return _out(200, M.mirror(doc, cp["slug"], bdate))

    # ── cpsim02: the Altruist-compatible OAuth + read API ───────────────────────────────────────────────────────
    def _log(request: Request, slug: str, tail: str, message_type: str) -> dict:
        log = request.scope.setdefault(LOG_KEY, {})
        log.update({"slug": slug[:64], "path": f"/cpsim/c/{slug[:64]}{tail}", "message_type": message_type})
        return log

    @sub.get("/c/{slug}/api/oauth2/authorize")
    async def oauth_authorize(slug: str, request: Request):
        log = _log(request, slug, "/api/oauth2/authorize", "altruist.oauth.authorize")
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                try:
                    location = await O.authorize(conn, cp, dict(request.query_params))
                except O.OAuthRefused as r:
                    log["auth_outcome"] = r.outcome
                    return _out(r.status, {"error": r.error, "error_description": r.description})
                log["auth_outcome"] = "ok"
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        return Response(status_code=302, headers={"Location": location, "Cache-Control": "no-store"})

    @sub.post("/c/{slug}/oauth2/token")
    async def oauth_token(slug: str, request: Request):
        log = _log(request, slug, "/oauth2/token", "altruist.oauth.token")
        form = O.parse_form(await request.body())
        log["message_type"] = f"altruist.oauth.token.{form.get('grant_type')}" if form.get("grant_type") in O.GRANTS \
            else "altruist.oauth.token"
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                try:
                    body = await O.token(conn, cp, form)
                except O.OAuthRefused as r:
                    log["auth_outcome"] = r.outcome
                    raise _Refusal(r) from r
                log["auth_outcome"] = "ok"
        except _Refusal as rr:       # the transaction rolled back: a refusal persists nothing
            r = rr.r
            return _out(r.status, {"error": r.error, "error_description": r.description}, {"Cache-Control": "no-store"})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        return _out(200, body, {"Cache-Control": "no-store", "Pragma": "no-cache"})

    def _paging(request: Request, kind: str):
        """(page, page_size) from the query, or a 400 JSONResponse. Documented: page default 0, page_size default
        100, max per kind."""
        q = request.query_params
        try:
            page = int(q.get("page", "0"))
            size = int(q.get("page_size", str(PAGE_SIZE_DEFAULT)))
        except ValueError:
            return _out(400, {"detail": "page and page_size must be integers"})
        if page < 0:
            return _out(400, {"detail": "page must be 0 or more"})
        if size < 1 or size > PAGE_SIZE_MAX[kind]:
            return _out(400, {"detail": f"page_size must be between 1 and {PAGE_SIZE_MAX[kind]} for {kind}"})
        return page, size

    def _iso_param(request: Request, name: str):
        v = request.query_params.get(name)
        if v is None or v == "":
            return None
        try:
            return date.fromisoformat(v) if len(v) == 10 else None
        except ValueError:
            return None

    async def _resource(slug: str, request: Request, tail: str, kind: str, fetch, echo: dict | None = None):
        log = _log(request, slug, tail, f"altruist.{kind}")
        delay = 0
        paged = _paging(request, kind)
        if isinstance(paged, JSONResponse):
            return paged
        page, size = paged
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                outcome = await O.check_access(conn, cp, A.bearer(request.headers.get("authorization")))
                log["auth_outcome"] = outcome
                if outcome != "ok":
                    return _out(401, {"detail": REFUSALS.get(outcome, "not authorised")},
                                {"WWW-Authenticate": 'Bearer realm="cpsim", error="invalid_token"'})
                beh = await R.behaviour(conn, cp["id"]) or {}
                if beh.get("default_outcome") == "reject":
                    log["auth_outcome"] = "behaviour_reject"
                    return _out(503, {"detail": beh.get("reject_reason") or "rejected by the simulated custodian",
                                      "behaviour": "reject"})
                if beh.get("default_outcome") == "delay":
                    delay = min(int(beh.get("delay_seconds") or 0), MAX_DELAY_S)
                clock = await R.business_date(conn, cp["id"]) or R.today()
                result = await fetch(conn, cp, clock)
        except B.BookError as exc:
            return _out(exc.status, {"detail": str(exc)})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        if isinstance(result, JSONResponse):
            return result
        if delay:
            await asyncio.sleep(delay)
        base = _page_base
        index = max(page - base, 0)           # base 1: a page-0 request is page 1 (the docs' example)
        echoed = index + base
        total = -(-len(result) // size)
        chunk = result[index * size:(index + 1) * size]
        return _out(200, {"paging": {"page": echoed, "page_size": size, "total_pages": total},
                          "data": {**(echo or {}), kind: chunk}})

    @sub.get("/c/{slug}/api/v2/households")
    async def households(slug: str, request: Request, name_pattern: str | None = None):
        async def fetch(conn, cp, clock):
            return await B.households(conn, cp["id"], name_pattern)
        return await _resource(slug, request, "/api/v2/households", "households", fetch)

    @sub.get("/c/{slug}/v2/accounts")
    async def accounts(slug: str, request: Request, household_id: str | None = None):
        async def fetch(conn, cp, clock):
            if not household_id:
                return _out(400, {"detail": "household_id is required"})
            return await B.accounts(conn, cp["id"], household_id, clock)
        return await _resource(slug, request, "/v2/accounts", "accounts", fetch, echo={"household_id": household_id})

    def _account_route(kind: str, tail: str, serve, windowed: bool = False):
        async def handler(slug: str, account_id: str, request: Request):
            async def fetch(conn, cp, clock):
                if not await B.account_exists(conn, cp["id"], account_id):
                    return _out(404, {"detail": "no such account in this simulated custodian's book"})
                if windowed:
                    lo, hi = _iso_param(request, "date_from"), _iso_param(request, "date_to")
                    for name, val in (("date_from", lo), ("date_to", hi)):
                        if request.query_params.get(name) and val is None:
                            return _out(400, {"detail": f"{name} must be YYYY-MM-DD"})
                    return await serve(conn, cp["id"], account_id, clock, lo, hi)
                return await serve(conn, cp["id"], account_id, clock)
            return await _resource(slug, request, f"/v2/accounts/{{account_id}}{tail}", kind, fetch,
                                   echo={"account_id": account_id})
        handler.__name__ = f"{kind}_endpoint"
        return handler

    sub.add_api_route("/c/{slug}/v2/accounts/{account_id}/positions/",
                      _account_route("positions", "/positions/", B.positions), methods=["GET"])
    sub.add_api_route("/c/{slug}/v2/accounts/{account_id}/cost-basis/",
                      _account_route("cost_basis", "/cost-basis/", B.lots), methods=["GET"])
    sub.add_api_route("/c/{slug}/v2/accounts/{account_id}/transactions",
                      _account_route("transactions", "/transactions", B.transactions, windowed=True), methods=["GET"])

    # ── cpsim03: the orders API (contract §8 — every field ASSUMED) ─────────────────────────────────────────────
    async def _orders(slug: str, request: Request, tail: str, message_type: str, work, *, transfers: bool = False):
        """Access-token auth as the read API; then ``work(conn, cp, clock, behaviour)`` in ONE transaction. Behaviour
        decides an order's OUTCOME (D6) — it never turns an orders call into a 503, and delay_seconds stays a
        read-API setting. Webhooks (D9) are scheduled after the transaction commits and delivered outside the
        request (fix 1: never inline)."""
        log = _log(request, slug, tail, message_type)
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                outcome = await O.check_access(conn, cp, A.bearer(request.headers.get("authorization")))
                log["auth_outcome"] = outcome
                if outcome != "ok":
                    return _out(401, {"detail": REFUSALS.get(outcome, "not authorised")},
                                {"WWW-Authenticate": 'Bearer realm="cpsim", error="invalid_token"'})
                beh = await R.behaviour(conn, cp["id"]) or {}
                clock = await R.business_date(conn, cp["id"]) or R.today()
                status, body, events = await work(conn, cp, clock, beh)
        except (SO.OrderError, ST.TransferError) as exc:
            log["shape_valid"] = exc.status != 422
            log["error_count"] = len(exc.errors)
            return _out(exc.status, {"detail": str(exc), **({"errors": exc.errors} if exc.errors else {})})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        log["shape_valid"] = True
        if events:
            from cpsim import webhooks as W

            # fix 1: delivered AFTER the transaction committed and OUTSIDE this request — the response never waits
            if transfers:
                W.schedule_transfers(counterparty_id=cp["id"], slug=cp["slug"], items=events, behaviour=beh)
            else:
                W.schedule_orders(counterparty_id=cp["id"], slug=cp["slug"], items=events, behaviour=beh)
        return _out(status, body)

    @sub.post("/c/{slug}/v2/orders")
    async def order_create(slug: str, request: Request):
        raw = await request.body()
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            doc = None

        async def work(conn, cp, clock, beh):
            order, created, events = await SO.submit(conn, cp, doc, clock=clock, behaviour=beh)
            return (201 if created else 200), {"order": order}, ([(order, events)] if events else [])
        return await _orders(slug, request, "/v2/orders", "altruist.orders.create", work)

    @sub.get("/c/{slug}/v2/orders")
    async def order_list(slug: str, request: Request, account_id: str | None = None):
        async def work(conn, cp, clock, beh):
            if not account_id:
                return 400, {"detail": "account_id is required"}, []
            if not await B.account_exists(conn, cp["id"], account_id):
                return 404, {"detail": "no such account in this simulated custodian's book"}, []
            return 200, {"orders": await SO.list_for_account(conn, cp["id"], account_id)}, []
        return await _orders(slug, request, "/v2/orders", "altruist.orders.list", work)

    @sub.get("/c/{slug}/v2/orders/{order_id}")
    async def order_get(slug: str, order_id: str, request: Request):
        async def work(conn, cp, clock, beh):
            return 200, {"order": await SO.get(conn, cp["id"], order_id[:80])}, []
        return await _orders(slug, request, "/v2/orders/{order_id}", "altruist.orders.get", work)

    @sub.delete("/c/{slug}/v2/orders/{order_id}")
    async def order_cancel(slug: str, order_id: str, request: Request):
        async def work(conn, cp, clock, beh):
            order = await SO.cancel(conn, cp, order_id[:80])
            return 200, {"order": order}, [(order, ["order.updated"])]
        return await _orders(slug, request, "/v2/orders/{order_id}", "altruist.orders.cancel", work)

    # ── cpsim04: the transfers API (contract §9 — every field ASSUMED) ─────────────────────────────────────────
    def _tevent(t: dict) -> dict:
        return {"transfer_id": t["transfer_id"], "client_transfer_id": t["client_transfer_id"],
                "account_id": t["from_account_id"]}

    @sub.post("/c/{slug}/v2/transfers")
    async def transfer_create(slug: str, request: Request):
        raw = await request.body()
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            doc = None

        async def work(conn, cp, clock, beh):
            t, created, events = await ST.submit(conn, cp, doc, clock=clock, behaviour=beh)
            return (201 if created else 200), {"transfer": t}, ([_tevent(t)] if events else [])
        return await _orders(slug, request, "/v2/transfers", "altruist.transfers.create", work, transfers=True)

    @sub.get("/c/{slug}/v2/transfers")
    async def transfer_list(slug: str, request: Request, account_id: str | None = None):
        async def work(conn, cp, clock, beh):
            if not account_id:
                return 400, {"detail": "account_id is required"}, []
            if not await B.account_exists(conn, cp["id"], account_id):
                return 404, {"detail": "no such account in this simulated custodian's book"}, []
            return 200, {"transfers": await ST.list_for_account(conn, cp["id"], account_id)}, []
        return await _orders(slug, request, "/v2/transfers", "altruist.transfers.list", work, transfers=True)

    @sub.get("/c/{slug}/v2/transfers/{transfer_id}")
    async def transfer_get(slug: str, transfer_id: str, request: Request):
        async def work(conn, cp, clock, beh):
            return 200, {"transfer": await ST.get(conn, cp["id"], transfer_id[:80])}, []
        return await _orders(slug, request, "/v2/transfers/{transfer_id}", "altruist.transfers.get", work,
                             transfers=True)

    @sub.delete("/c/{slug}/v2/transfers/{transfer_id}")
    async def transfer_cancel(slug: str, transfer_id: str, request: Request):
        async def work(conn, cp, clock, beh):
            t = await ST.cancel(conn, cp, transfer_id[:80], clock)
            return 200, {"transfer": t}, [_tevent(t)]
        return await _orders(slug, request, "/v2/transfers/{transfer_id}", "altruist.transfers.cancel", work,
                             transfers=True)

    # ── cpsim05: FIX 4.4 over HTTP (docs/cpsim/CPSIM05_NOTES.md D1 / D2) ───────────────────────────────────────
    @sub.post("/c/{slug}/fix")
    async def fix_message(slug: str, request: Request):
        """ONE FIX message in (SOH or '|'), the reply message(s) out, newline separated, same delimiter. Access-token
        auth as the orders API. A malformed message → a Reject (35=3), HTTP 200 — never a 500. Every message in and
        out logged (cpsim.fix_messages). No webhooks: the reply carries the ExecutionReports."""
        from cpsim import fix as FX

        log = _log(request, slug, "/fix", "fix")
        raw = (await request.body()).decode("utf-8", errors="replace")
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                outcome = await O.check_access(conn, cp, A.bearer(request.headers.get("authorization")))
                log["auth_outcome"] = outcome
                if outcome != "ok":
                    return _out(401, {"detail": REFUSALS.get(outcome, "not authorised")},
                                {"WWW-Authenticate": 'Bearer realm="cpsim", error="invalid_token"'})
                sess = await FX.session(conn, cp["id"])
                beh = await R.behaviour(conn, cp["id"]) or {}
                clock = await R.business_date(conn, cp["id"]) or R.today()
                delim = FX.SOH if FX.SOH in raw else FX.PIPE
                fields: list = []
                valid, why = False, None
                try:
                    delim, fields = FX.parse(raw)
                    log["message_type"] = f"fix.{FX.MSG_NAMES.get(FX.first(fields, 35) or '', 'unknown')}"
                    FX.check_session(fields, sess)
                    async with conn.transaction():      # a refusal mid-message rolls back everything it wrote
                        out = await FX.handle(conn, cp, fields, clock=clock, behaviour=beh)
                    valid = True
                except FX.FixReject as exc:
                    why = exc.text
                    out = [FX.reject(exc.text, ref_seq=FX.first(fields, 34) if fields else None, ref_tag=exc.ref_tag,
                                     ref_msg_type=FX.first(fields, 35) if fields else None, reason=exc.reason)]
                me = sess["target_comp_id"] if sess else (FX.first(fields, 56) if fields else None) or slug.upper()
                them = sess["sender_comp_id"] if sess else (FX.first(fields, 49) if fields else None) or "UNKNOWN"
                base = int(await conn.fetchval("SELECT count(*) FROM cpsim.fix_messages WHERE counterparty_id = "
                                               "$1::uuid AND direction = 'out'", str(cp["id"])))
                replies = [FX.encode(mt, f, sender=me, target=them, seq=base + i, delim=delim)
                           for i, (mt, f) in enumerate(out, 1)]
                await FX.log_exchange(conn, counterparty_id=cp["id"], raw_in=raw or "[empty]", replies=replies,
                                      valid=valid, reject_reason=why)
        except Exception:  # noqa: BLE001 — a simulator fault (never a malformed message: that is a Reject above)
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        log["shape_valid"] = valid
        log["error_count"] = 0 if valid else 1
        return Response("\n".join(replies), status_code=200, media_type="text/plain; charset=utf-8")

    # ── cpsim06: the Realtime-API-shaped account opening (docs/cpsim/CPSIM06_NOTES.md D1–D3; every path ASSUMED) ──
    from cpsim import realtime as RT

    RT_BASE = "/c/{slug}/realtime"

    @sub.post(RT_BASE + "/realms/altruist/protocol/openid-connect/token")
    async def realtime_token(slug: str, request: Request):
        """Password grant (+ refresh_token): access 5 min, refresh on a FIXED 10 h window. Form-encoded body; the
        client secret is the counterparty's cpsim_ key. A refusal persists nothing."""
        log = _log(request, slug, "/realtime/realms/altruist/protocol/openid-connect/token", "realtime.token")
        form = O.parse_form(await request.body())
        if form.get("grant_type") in RT.GRANTS:
            log["message_type"] = f"realtime.token.{form['grant_type']}"
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"})
                log["counterparty_id"] = cp["id"]
                try:
                    body = await RT.token(conn, cp, form)
                except O.OAuthRefused as r:
                    log["auth_outcome"] = r.outcome
                    raise _Refusal(r) from r
                log["auth_outcome"] = "ok"
        except _Refusal as rr:
            r = rr.r
            return _out(r.status, {"error": r.error, "error_description": r.description}, {"Cache-Control": "no-store"})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"})
        return _out(200, body, {"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def _realtime(slug: str, request: Request, tail: str, message_type: str, kind: str, work):
        """Realtime access-token auth, then ``work(conn, cp, clock, doc)`` → (status, body) in ONE transaction. Every
        answer carries RateLimit-* headers; the injected test behaviour answers 429 + Retry-After. A RealtimeError
        rolls back everything the request wrote."""
        log = _log(request, slug, tail, message_type)
        headers = RT.rate_headers(kind)
        wait = RT.take_429()
        if wait is not None:
            log["auth_outcome"] = "behaviour_reject"     # the injected test behaviour (request_log_auth_chk's value)
            return _out(429, {"detail": "rate limit exceeded"}, {**headers, "RateLimit-Remaining": "0",
                                                                  "Retry-After": str(wait)})
        doc = None
        if request.method == "POST":
            raw = await request.body()
            try:
                doc = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                return _out(400, {"detail": "body is not JSON"}, headers)
        try:
            async with DB.tx() as conn:
                cp = await R.live_by_slug(conn, slug)
                if cp is None:
                    log["auth_outcome"] = "unknown_counterparty"
                    return _out(404, {"detail": "no such simulated counterparty"}, headers)
                log["counterparty_id"] = cp["id"]
                outcome = await RT.check_access(conn, cp, A.bearer(request.headers.get("authorization")))
                log["auth_outcome"] = outcome
                if outcome != "ok":
                    return _out(401, {"detail": REFUSALS.get(outcome, "not authorised")},
                                {**headers, "WWW-Authenticate": 'Bearer realm="altruist", error="invalid_token"'})
                clock = await R.business_date(conn, cp["id"]) or R.today()
                status, body = await work(conn, cp, clock, doc)
        except RT.RealtimeError as exc:
            log["shape_valid"] = False
            log["error_count"] = len(exc.problems) or 1
            return _out(exc.status, {"detail": str(exc), **({"problems": exc.problems} if exc.problems else {})},
                        headers)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _out(500, {"detail": "simulator error"}, headers)
        probs = body.get("problems") if isinstance(body, dict) else None
        log["shape_valid"] = status < 400
        log["error_count"] = len(probs or [])
        return _out(status, body, headers)

    @sub.post(RT_BASE + "/api/households")
    async def realtime_household(slug: str, request: Request):
        async def work(conn, cp, clock, doc):
            status, hh = await RT.household(conn, cp["id"], (doc or {}).get("name") if isinstance(doc, dict) else None)
            return status, {"household": hh}
        return await _realtime(slug, request, "/realtime/api/households", "realtime.households.create", "write", work)

    @sub.post(RT_BASE + "/api/contacts")
    async def realtime_contact(slug: str, request: Request):
        async def work(conn, cp, clock, doc):
            status, c = await RT.upsert_contact(conn, cp["id"], doc, clock)
            return status, {"contact": c}
        return await _realtime(slug, request, "/realtime/api/contacts", "realtime.contacts.create", "write", work)

    @sub.post(RT_BASE + "/api/account-drafts")
    async def realtime_draft(slug: str, request: Request):
        async def work(conn, cp, clock, doc):
            status, d = await RT.upsert_draft(conn, cp["id"], doc)
            return status, {"draft": d}
        return await _realtime(slug, request, "/realtime/api/account-drafts", "realtime.drafts.create", "write", work)

    @sub.get(RT_BASE + "/api/account-drafts/{draft_id}")
    async def realtime_draft_get(slug: str, draft_id: str, request: Request):
        async def work(conn, cp, clock, doc):
            return 200, {"draft": await RT.get_draft(conn, cp["id"], draft_id)}
        return await _realtime(slug, request, "/realtime/api/account-drafts/{draft_id}", "realtime.drafts.get", "read",
                               work)

    @sub.post(RT_BASE + "/api/account-drafts/{draft_id}/validate")
    async def realtime_validate(slug: str, draft_id: str, request: Request):
        async def work(conn, cp, clock, doc):
            return 200, await RT.check(conn, cp["id"], draft_id, clock)
        return await _realtime(slug, request, "/realtime/api/account-drafts/{draft_id}/validate",
                               "realtime.drafts.validate", "write", work)

    @sub.post(RT_BASE + "/api/account-drafts/{draft_id}/submit")
    async def realtime_submit(slug: str, draft_id: str, request: Request):
        async def work(conn, cp, clock, doc):
            return await RT.submit(conn, cp["id"], draft_id, clock)
        return await _realtime(slug, request, "/realtime/api/account-drafts/{draft_id}/submit",
                               "realtime.drafts.submit", "write", work)

    return SimulatedMarker(sub)


class _Refusal(Exception):
    def __init__(self, r):
        super().__init__(r.description)
        self.r = r


#: the ONE instance main.py mounts (and the admin router's "Send test message" calls in-process)
app = create_app()
