"""cpsim01 Task 3 — the simulator's own FastAPI sub-app, mounted at /cpsim on the existing API (main.py).

    GET  /cpsim/health                 no auth; {"status": "ok", "simulated": true}
    POST /cpsim/c/{slug}/mirror        Authorization: Bearer cpsim_live_…  — a properly formatted envelope in, the
                                       mirrored message out (same message_id, mirrored: true, received_at from the
                                       counterparty's clock); malformed → 422 with the schema errors; unknown slug →
                                       404; missing / wrong / revoked key, or a counterparty that is not active → 401.

Its OWN auth (cpsim/auth.py — its fake keys, never a Hollisworks session; main.py's auth middleware passes /cpsim
through untouched). Every response — success, refusal, 404, 405 — carries ``X-Counterparty-Simulated: true`` and every
request is written to ``cpsim.request_log`` (no bodies, no keys) by the ASGI wrapper below.
"""

from __future__ import annotations

import json
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cpsim import SIMULATED_HEADER, SIMULATED_VALUE
from cpsim import auth as A
from cpsim import db as DB
from cpsim import mirror as M
from cpsim import registry as R

LOG_KEY = "cpsim.log"


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

    return SimulatedMarker(sub)


#: the ONE instance main.py mounts (and the admin router's "Send test message" calls in-process)
app = create_app()
