"""cpsim02 D10 — the simulated custodian SENDS webhooks, signed the way the receiver verifies (contract §4, §6 item 7).

  body       ``{event_id, event_type, account_id, environment: "simulator", occurred_at}`` (compact JSON, the exact bytes
             that are signed)
  header     ``X-Altruist-Signature`` = lowercase hex HMAC-SHA256(secret, body)
  secret     ``ALTRUIST_SIMULATOR_WEBHOOK_SECRET`` (Doppler; must start ``cpsim_whsec_``), read by THIS package from its
             own environment — never from services/. Missing or malformed → nothing is sent; the refusal is recorded
             (request_log auth_outcome 'unsigned', status UNSIGNED_STATUS), never raised.
  target     ``CPSIM_BASE_URL`` + RECEIVER_PATH, unless the counterparty stores an https override (behaviour.webhook_url)
  delivery   one attempt + up to RETRIES retries with backoff, on a transport error or a 5xx; every attempt is a
             request_log row (direction 'out', status, latency) — never the secret, never the signature.
  when       after an accepted positions / transactions upload, one event per affected account — ONLY when the
             behaviour row has webhooks_enabled; and on demand from the admin page ("Send test event", same flag).
  cpsim03    ORDER events (contract §8.4): ``order.updated`` (accepted / working / rejected / cancelled) and
             ``order.filled`` (a new execution), body ``{event_id, event_type, order_id, client_order_id, account_id,
             environment: "simulator", occurred_at}``, same secret, signature, retries and log; webhooks on only
             (:func:`after_orders`). The receiver treats them as a notification — it never books a fill.

``set_transport`` / ``set_sleep`` are test injection points (the verify posts in-process to the real receiver through an
ASGI transport); production leaves both unset.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from cpsim import db as DB
from cpsim import registry as R

SECRET_ENV = "ALTRUIST_SIMULATOR_WEBHOOK_SECRET"
SECRET_PREFIX = "cpsim_whsec_"
BASE_ENV = "CPSIM_BASE_URL"
RECEIVER_PATH = "/api/v1/altruist/webhook"
SIGNATURE_HEADER = "X-Altruist-Signature"
EVENT_TYPES = {"positions": "account.positions_updated", "transactions": "account.transactions_updated"}
ORDER_EVENT_TYPES = ("order.updated", "order.filled")
RETRIES = 2
BACKOFF_S = (0.5, 1.5)
TIMEOUT_S = 15.0
UNSIGNED_STATUS = 412        # recorded, never sent: no (valid) secret
TRANSPORT_ERROR_STATUS = 599  # recorded: the request never got an HTTP answer

_transport = None
_sleep = asyncio.sleep


def set_transport(t) -> None:
    global _transport
    _transport = t


def set_sleep(fn) -> None:
    global _sleep
    _sleep = fn or asyncio.sleep


def secret() -> str | None:
    s = os.environ.get(SECRET_ENV) or ""
    return s if s.startswith(SECRET_PREFIX) and len(s) > len(SECRET_PREFIX) + 15 else None


def sign(key: str, body: bytes) -> str:
    return hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def target(behaviour: dict | None) -> str | None:
    override = (behaviour or {}).get("webhook_url")
    if override:
        return override if override.startswith("https://") else None
    base = (os.environ.get(BASE_ENV) or "").rstrip("/")
    return base + RECEIVER_PATH if base.startswith("https://") else None


def event_body(event_type: str, account_id: str, occurred_at: datetime | None = None, order: dict | None = None) -> bytes:
    doc = {"event_id": f"cpsim-evt-{uuid.uuid4().hex}", "event_type": event_type}
    if order is not None:       # cpsim03 — an order event names the order (contract §8.4)
        doc.update({"order_id": order["order_id"], "client_order_id": order["client_order_id"]})
    doc.update({"account_id": account_id, "environment": "simulator",
                "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")})
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


async def _log(counterparty_id, slug, path, status, outcome, event_type, latency_ms) -> None:
    try:
        async with DB.tx() as conn:
            await R.log_request(conn, counterparty_id=counterparty_id, slug=slug, method="POST", path=path,
                                status=status, auth_outcome=outcome, message_type=event_type, direction="out",
                                latency_ms=latency_ms)
    except Exception as exc:  # noqa: BLE001 — logging never changes the outcome; never prints a value
        print(f"[cpsim] outbound log write failed: {type(exc).__name__}")


async def send_event(*, counterparty_id, slug: str, account_id: str, event_type: str, behaviour: dict | None,
                     order: dict | None = None) -> dict:
    """One event, signed, with retries. Returns {event_id, event_type, account_id, status, attempts, outcome}."""
    url = target(behaviour)
    key = secret()
    body = event_body(event_type, account_id, order=order)
    event_id = json.loads(body)["event_id"]
    path = urlsplit(url).path if url else RECEIVER_PATH
    if key is None or url is None:
        reason = "no simulator webhook secret configured" if key is None else "no https webhook target configured"
        await _log(counterparty_id, slug, path, UNSIGNED_STATUS, "unsigned", event_type, 0)
        return {"event_id": event_id, "event_type": event_type, "account_id": account_id, "status": None,
                "attempts": 0, "outcome": "not_sent", "reason": reason}
    headers = {SIGNATURE_HEADER: sign(key, body), "Content-Type": "application/json"}
    status, attempts = None, 0
    async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=_transport) as client:
        for attempt in range(1 + RETRIES):
            attempts += 1
            t0 = time.monotonic()
            try:
                res = await client.post(url, content=body, headers=headers)
                status = res.status_code
            except httpx.HTTPError:
                status = TRANSPORT_ERROR_STATUS
            ms = int((time.monotonic() - t0) * 1000)
            ok = status is not None and status < 500
            await _log(counterparty_id, slug, path, status, "delivered" if ok else "delivery_failed", event_type, ms)
            if ok:
                break
            if attempt < RETRIES:
                await _sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
    return {"event_id": event_id, "event_type": event_type, "account_id": account_id, "status": status,
            "attempts": attempts, "outcome": "delivered" if status is not None and status < 300 else "failed"}


async def after_upload(*, counterparty_id, slug: str, kind: str, accounts: list[str], behaviour: dict | None) -> list:
    """D10: after an ACCEPTED positions / transactions upload, one event per affected account — webhooks on only."""
    if not (behaviour or {}).get("webhooks_enabled") or kind not in EVENT_TYPES:
        return []
    return [await send_event(counterparty_id=counterparty_id, slug=slug, account_id=a, event_type=EVENT_TYPES[kind],
                             behaviour=behaviour) for a in accounts]


async def after_orders(*, counterparty_id, slug: str, items: list[tuple[dict, list[str]]], behaviour: dict | None) -> list:
    """cpsim03 — one signed event per (order, event type), webhooks on only. ``items`` = [(order dict with order_id /
    client_order_id / account_id, [event types])]."""
    if not (behaviour or {}).get("webhooks_enabled"):
        return []
    out = []
    for order, types in items:
        for t in types:
            if t in ORDER_EVENT_TYPES:
                out.append(await send_event(counterparty_id=counterparty_id, slug=slug, account_id=order["account_id"],
                                            event_type=t, behaviour=behaviour, order=order))
    return out
