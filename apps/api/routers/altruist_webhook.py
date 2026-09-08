"""Altruist Sprint 6 — Realtime API webhook receiver: ``POST /altruist/webhook``.

The first genuinely unauthenticated, SIGNATURE-VERIFIED inbound route in this
app. Every other entry in ``main.py``'s ``PUBLIC_PATHS`` is pre-auth but
UNSIGNED (public metadata reads, a single-use invite token) — this route is
called by Altruist itself, with no Auth0 session to check, so it is gated
entirely by ``services.altruist_webhook.verify_signature`` instead of
``rbac.require_permission``. See that module's docstring for the full
[FIND] on the unconfirmed signature scheme and payload shape.

Because ``public.altruist_webhook_events``' RLS policies require
``app.is_super_admin = true`` for the row this route eventually UPDATEs
(resolving ``org_id`` from NULL — there is no authenticated org to derive
that context from any other way), this route explicitly raises
``is_super_admin`` on its own DB session via
``services.database.set_rls_context(None, True)`` before touching the pool,
and resets it in a ``finally`` — the same shape the login-bootstrap flow
uses for its own pre-org-resolution GUC.

Standing rule, enforced here: signature verification happens BEFORE any
payload parsing or DB write, and a missing/invalid signature returns the
IDENTICAL generic 401 body either way — never revealing which.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.altruist_webhook import InvalidSignature, SIGNATURE_HEADER, process_event, verify_signature
from services.database import get_pool, reset_rls_context, set_rls_context

router = APIRouter(tags=["altruist-webhook"])

_GENERIC_REJECTION = {"detail": "Invalid signature"}


@router.post("/altruist/webhook")
async def altruist_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)

    try:
        verify_signature(raw_body, signature)
    except InvalidSignature:
        return JSONResponse(status_code=401, content=_GENERIC_REJECTION)

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {"_unparseable_body": True}

    tokens = set_rls_context(None, True)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await process_event(conn, payload=payload)
    finally:
        reset_rls_context(tokens)

    return result
