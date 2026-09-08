"""Altruist Sprint 6 — Realtime API webhook receiver: signature verification,
event-id dedup, defensive payload dispatch.

[FIND] SIGNATURE SCHEME AND PAYLOAD SHAPE ARE UNCONFIRMED
────────────────────────────────────────────────────────────────────────────
Altruist's real Realtime API webhook signature scheme and event payload
field names have NOT been confirmed against real documentation or a live
sandbox this session — their developer docs block automated fetching, and
no real webhook has ever been received (re-confirmed live: no `ALTRUIST_*`
secrets exist in Doppler). This module implements a documented-industry-
standard best guess — HMAC-SHA256 over the raw request body, keyed by
``ALTRUIST_WEBHOOK_SECRET``, presented in a signature header — so the real,
secure PLUMBING (signature verification, event-id dedup, generic logging, a
pluggable dispatch point) is built and provably correct, but the exact
header name, event-id field, and account/household identifier field names
must be reconfirmed once Sprint 0 lands real Altruist docs or sandbox
access. Every "plausible field name" guess below is intentionally over-
inclusive rather than exact, and every unmatched shape falls back to logging
the raw payload rather than guessing a mapping — see ``process_event``.

WHY THIS TABLE, WHY is_super_admin
────────────────────────────────────────────────────────────────────────────
``public.altruist_webhook_events`` (applied directly this session, RLS
verified live) has no authenticated caller to derive org context from — the
caller is Altruist itself, verified only by HMAC signature. Its RLS policies
require ``app.is_super_admin = true`` for the UPDATE that resolves an
event's ``org_id`` from NULL (the SELECT/UPDATE USING clause has no other
way to see a NULL-org row), and the INSERT allows ``org_id IS NULL``
unconditionally — so this whole module always runs on a connection whose RLS
context was set via ``services.database.set_rls_context(None, True)`` by the
router, matching the same shape the login-bootstrap flow uses for its own
pre-org-resolution GUC (``app.current_auth0_sub``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from services.altruist_identity import RECORD_TYPE_ACCOUNT, SOURCE_SYSTEM_ALTRUIST, resolve_identity
from services.altruist_positions_sync import sync_resolved_accounts

SIGNATURE_HEADER = "X-Altruist-Signature"
SECRET_ENV_VAR = "ALTRUIST_WEBHOOK_SECRET"
DEFAULT_ENVIRONMENT = "sandbox"

TABLE_WEBHOOK_EVENTS = "public.altruist_webhook_events"
TABLE_EXTERNAL_REFERENCES = "portfolio.external_references"

# [FIND] Best-guess, unconfirmed plausible field names — checked in order,
# first match wins, both at the payload's top level and inside a nested
# "data" object (a common webhook-provider convention).
EVENT_ID_FIELDS = ("event_id", "id", "eventId", "webhook_id")
EVENT_TYPE_FIELDS = ("event_type", "type", "eventType")
IDENTIFIER_FIELDS = (
    "account_id", "accountId", "external_account_id",
    "household_id", "householdId",
    "resource_id", "resourceId",
)
ENVIRONMENT_FIELDS = ("environment", "env")


class InvalidSignature(Exception):
    """Signature missing, malformed, or not matching. The router turns every
    instance of this into an identical, generic 401 — never revealing which
    of the three it was."""


def verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """HMAC-SHA256 over the raw body, keyed by ALTRUIST_WEBHOOK_SECRET.

    [FIND] unconfirmed scheme — see module docstring. Uses
    ``hmac.compare_digest`` for constant-time comparison. An unconfigured
    secret is treated the same as an invalid signature (reject, not accept)
    — a webhook route with no secret configured must fail closed, not open.
    """
    secret = os.environ.get(SECRET_ENV_VAR)
    if not secret or not signature_header:
        raise InvalidSignature("missing signature header or unconfigured secret")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise InvalidSignature("signature does not match")


def _first_present(payload: dict, fields: tuple[str, ...]) -> Any:
    for f in fields:
        val = payload.get(f)
        if val:
            return val
    data = payload.get("data")
    if isinstance(data, dict):
        for f in fields:
            val = data.get(f)
            if val:
                return val
    return None


def extract_event_id(payload: dict) -> str:
    found = _first_present(payload, EVENT_ID_FIELDS)
    if found:
        return str(found)
    # [FIND] no id-shaped field found at all — fall back to a content hash
    # so the row is still dedup-able against a byte-identical resend, while
    # the "unidentified:" prefix makes clear this was NOT a real Altruist
    # event id, for whoever reads this table later.
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"unidentified:{digest}"


def extract_event_type(payload: dict) -> str | None:
    found = _first_present(payload, EVENT_TYPE_FIELDS)
    return str(found) if found else None


def extract_identifier(payload: dict) -> str | None:
    found = _first_present(payload, IDENTIFIER_FIELDS)
    return str(found) if found else None


def extract_environment(payload: dict) -> str:
    found = _first_present(payload, ENVIRONMENT_FIELDS)
    return str(found) if found else DEFAULT_ENVIRONMENT


async def record_event(
    conn, *, event_id: str, event_type: str | None, raw_payload: dict
) -> tuple[str, bool]:
    """Insert the raw event under org_id=NULL. Returns ``(row_id, inserted)``.

    ``ON CONFLICT (event_id) DO NOTHING`` IS the dedup mechanism — replaying
    an identical ``event_id`` is a no-op (no second row, no error), proven
    by ``inserted=False`` and the returned row id being the ORIGINAL row's.
    """
    row = await conn.fetchrow(
        f"""
        INSERT INTO {TABLE_WEBHOOK_EVENTS} (event_id, org_id, event_type, raw_payload)
        VALUES ($1, NULL, $2, $3::jsonb)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING id::text
        """,
        event_id, event_type, json.dumps(raw_payload, default=str),
    )
    if row is not None:
        return row["id"], True
    existing_id = await conn.fetchval(
        f"SELECT id::text FROM {TABLE_WEBHOOK_EVENTS} WHERE event_id = $1", event_id,
    )
    return existing_id, False


async def _lookup_org_for_identifier(conn, identifier: str) -> str | None:
    """Reverse crosswalk lookup: given an Altruist external account id, find
    which org it belongs to. Deliberately NOT scoped by org_id (unknown at
    this point) — relies on the caller's connection carrying
    ``is_super_admin=true`` RLS context to see across every org's rows. This
    is the ONLY source of truth for org_id on this path — the payload's own
    claimed org_id, if any, is never read or trusted (standing rule).
    """
    return await conn.fetchval(
        f"""
        SELECT org_id::text FROM {TABLE_EXTERNAL_REFERENCES}
        WHERE source_system = $1 AND external_id = $2 AND record_type = $3
        LIMIT 1
        """,
        SOURCE_SYSTEM_ALTRUIST, identifier, RECORD_TYPE_ACCOUNT,
    )


async def _mark_resolved(conn, event_row_id: str, org_id: str) -> None:
    await conn.execute(
        f"""
        UPDATE {TABLE_WEBHOOK_EVENTS}
        SET org_id = $1::uuid, processed_at = now()
        WHERE id = $2::uuid
        """,
        org_id, event_row_id,
    )


async def process_event(conn, *, payload: dict) -> dict:
    """The dedup -> lookup -> dispatch pipeline for one already-signature-
    verified webhook payload. Must run on a connection already carrying
    ``is_super_admin=true`` RLS context (see ``routers/altruist_webhook.py``).

    Never raises for a malformed/unrecognized payload shape or a sync
    failure — an unconfirmed real payload shape, or a downstream sync
    problem (e.g. no active connection for the resolved org), must still
    leave the event durably logged, not crash the endpoint.
    """
    if not isinstance(payload, dict):
        payload = {"_non_object_payload": payload}

    event_id = extract_event_id(payload)
    event_type = extract_event_type(payload)

    row_id, inserted = await record_event(
        conn, event_id=event_id, event_type=event_type, raw_payload=payload
    )
    if not inserted:
        return {"status": "duplicate", "event_id": event_id}

    identifier = extract_identifier(payload)
    if not identifier:
        return {
            "status": "logged_unrecognized",
            "event_id": event_id,
            "reason": "no plausible account/household identifier field in payload",
        }

    org_id = await _lookup_org_for_identifier(conn, identifier)
    if not org_id:
        return {
            "status": "logged_unrecognized",
            "event_id": event_id,
            "reason": "identifier not found in portfolio.external_references",
        }

    await _mark_resolved(conn, row_id, org_id)

    environment = extract_environment(payload)
    sync_summary: dict = {}
    try:
        await resolve_identity(conn, org_id=org_id, environment=environment)
        sync_result = await sync_resolved_accounts(conn, org_id=org_id, environment=environment)
        sync_summary = {"triggered": True, "accounts_synced": sync_result.accounts_synced}
    except Exception as exc:  # noqa: BLE001 - a sync failure must not fail webhook receipt
        sync_summary = {"triggered": False, "error": str(exc)}

    return {"status": "resolved", "event_id": event_id, "org_id": org_id, "sync": sync_summary}


__all__ = [
    "DEFAULT_ENVIRONMENT",
    "InvalidSignature",
    "SECRET_ENV_VAR",
    "SIGNATURE_HEADER",
    "TABLE_EXTERNAL_REFERENCES",
    "TABLE_WEBHOOK_EVENTS",
    "extract_environment",
    "extract_event_id",
    "extract_event_type",
    "extract_identifier",
    "process_event",
    "record_event",
    "verify_signature",
]
