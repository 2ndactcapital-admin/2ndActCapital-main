"""Altruist Open API — OAuth2 authorization-code flow + per-tenant credential
storage (Altruist Sprint 1), plus ``call_households``/``fetch_accounts_for_
household`` (Altruist Sprint 2) — the first two real resource calls built on
top of that connection.

WHAT THIS IS, AND WHAT IT IS NOT
────────────────────────────────────────────────────────────────────────────
This is the first real piece of Altruist Open API integration code. Everything
prior (``services/portfolio_altruist.py``, fee38's ``altruist_one.py``) either
reads a single global env-var credential or evaluates Altruist One heuristics —
neither stores a per-org, per-environment OAuth2 connection. This module does:

  * ``build_authorize_url`` + the state table — the redirect leg.
  * ``exchange_code_for_tokens`` — code-for-token exchange on callback.
  * ``refresh_access_token`` — rotates the access token before the 1-hour
    expiry, defensively handling both "Altruist issues a new refresh_token on
    refresh" and "it doesn't" (real response shape has never been observed —
    no sandbox credentials exist anywhere in this project's Doppler config,
    see docs/PROJECT_STATUS.md Sprint 1 entry).
  * Fernet encryption of every credential/token value at rest.

NO LIVE ALTRUIST CALL HAS EVER BEEN MADE FROM THIS MODULE. Every function that
builds and sends a real HTTP request (``exchange_code_for_tokens``,
``refresh_access_token``, ``call_households``, ``fetch_accounts_for_
household``) accepts no live-sandbox shortcuts — no credentials exist in this
project's Doppler config (Sprint 1, re-confirmed Sprint 2). ``call_households``
and ``fetch_accounts_for_household`` additionally take an optional ``transport``
so a caller (the verify script) can inject an ``httpx.MockTransport`` and
exercise the real request-building/response-parsing code path against a
synthetic, documented-shape payload — never a fabricated "live call succeeded"
claim.

ENCRYPTION — INTERIM, NOT THE INTENDED PRODUCTION PATH
────────────────────────────────────────────────────────────────────────────
[FIND] No existing encryption-at-rest helper exists anywhere in this codebase
(searched for "client_secret", "refresh_token", "secretsmanager", "kms",
"fernet", "vault" — the only hits were the single-tenant env-var probe in
portfolio_altruist.py). No AWS KMS key exists in this project's Doppler config
either. Per the sprint's own instructions, the documented interim fallback is
used here: application-layer Fernet symmetric encryption, keyed by
``ALTRUIST_TOKEN_ENCRYPTION_KEY`` (intended to be a Doppler secret in every
real deployment — it does not exist there yet, which is a real, separate
blocker from the sandbox-credential one; see PROJECT_STATUS.md). KMS envelope
encryption remains the intended production path and should replace this layer
without changing any caller — ``encrypt_secret``/``decrypt_secret`` are the
only two functions that would need to change.

OAUTH HOST / PATH ASSUMPTIONS — [FIND]
────────────────────────────────────────────────────────────────────────────
The sprint's CONFIRMED REAL FACTS give the sandbox/production API and login
hosts but not the exact authorize/token path segments (no live spec doc exists
in this repo — checked, see sprint log Task 1a). ``/oauth/authorize`` and
``/oauth/token`` on the login-portal host are the standard OAuth2
authorization-code convention and are used here as the best-available
assumption; they are isolated to ``_OAUTH_PATHS`` below so a single edit
corrects them once real sandbox docs/access are available.
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

# ── Environment / host configuration ───────────────────────────────────────
# Confirmed real facts (sprint prompt): sandbox host
# openapi.stage1.altruistnet.tech/altruist-open-api/ (login portal
# oauth.stage1.altruistnet.tech); production openapi.altruist.com/altruist-open-api/
# (login portal oauth.altruist.com). Access token expires in 1 hour; refresh
# token valid 30 days.
ALTRUIST_HOSTS: dict[str, dict[str, str]] = {
    "sandbox": {
        "api_base": "https://openapi.stage1.altruistnet.tech/altruist-open-api",
        "login_base": "https://oauth.stage1.altruistnet.tech",
    },
    "production": {
        "api_base": "https://openapi.altruist.com/altruist-open-api",
        "login_base": "https://oauth.altruist.com",
    },
}

# [FIND] path segments assumed per standard OAuth2 authorization-code
# convention — not confirmed against a live spec (none exists in-repo).
_OAUTH_PATHS = {
    "authorize": "/oauth/authorize",
    "token": "/oauth/token",
}

ACCESS_TOKEN_LIFETIME = timedelta(hours=1)
REFRESH_TOKEN_LIFETIME = timedelta(days=30)
STATE_TTL = timedelta(minutes=10)

ENVIRONMENTS = ("sandbox", "production")


class AltruistOAuthError(RuntimeError):
    """Raised for any OAuth flow failure (bad state, exchange failure, etc.)."""


class AltruistConfigError(RuntimeError):
    """Raised when required configuration (redirect URI, client id/secret,
    encryption key) is missing. Distinct from AltruistOAuthError so a caller
    can tell "this deployment is not configured" from "the flow itself failed".
    """


# ── Encryption ──────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    """Build the Fernet cipher from ALTRUIST_TOKEN_ENCRYPTION_KEY.

    Raises AltruistConfigError (never silently falls back to an insecure
    default) if the key is missing — a missing encryption key must fail loud,
    not write plaintext secrets to Postgres.
    """
    raw = os.environ.get("ALTRUIST_TOKEN_ENCRYPTION_KEY")
    if not raw:
        raise AltruistConfigError(
            "ALTRUIST_TOKEN_ENCRYPTION_KEY is not set — refusing to store an "
            "Altruist credential without application-layer encryption. This "
            "is a Doppler secret that does not yet exist in this project "
            "(see docs/PROJECT_STATUS.md, Altruist Sprint 1)."
        )
    # Accept either a ready-made urlsafe-base64 Fernet key, or derive one
    # deterministically from an arbitrary passphrase so a plain, human-typed
    # Doppler secret works without a separate "generate a Fernet key" step.
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception:
        digest = base64.urlsafe_b64encode(raw.encode("utf-8").ljust(32, b"0")[:32])
        return Fernet(digest)


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a credential/token value for storage. Never logs the input."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a stored credential/token value."""
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken as exc:
        raise AltruistOAuthError(
            "Stored Altruist credential could not be decrypted — the "
            "encryption key may have rotated without re-encrypting existing rows."
        ) from exc


def last4(value: str) -> str:
    """Redaction-safe reference: the last 4 characters, never the full value."""
    if not value:
        return ""
    return value[-4:]


def redact(value: str | None) -> str:
    """Safe-for-logs representation of a secret — NEVER the value itself."""
    if not value:
        return "<empty>"
    return f"***{last4(value)}"


# ── State / CSRF handling (Task 1d — designed from scratch, no existing
#    bespoke pattern in this codebase; Auth0 is entirely SDK-managed) ───────

def generate_state() -> str:
    return secrets.token_urlsafe(32)


async def create_oauth_state(conn, *, org_id: str, environment: str, user_id: str | None) -> str:
    state = generate_state()
    expires_at = datetime.now(timezone.utc) + STATE_TTL
    await conn.execute(
        """
        INSERT INTO altruist_oauth_states (org_id, environment, state, initiated_by, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        org_id, environment, state, user_id, expires_at,
    )
    return state


async def consume_oauth_state(conn, *, org_id: str, state: str) -> dict:
    """Validate and single-use-consume a state token.

    Raises AltruistOAuthError if the state is missing, expired, already used,
    or scoped to a different org — the same error class for all of these on
    purpose: a CSRF check must not leak *which* reason it failed for.
    """
    row = await conn.fetchrow(
        """
        SELECT id, org_id, environment, expires_at, used_at
        FROM altruist_oauth_states
        WHERE state = $1 AND org_id = $2
        """,
        state, org_id,
    )
    if row is None:
        raise AltruistOAuthError("Invalid or unrecognized OAuth state parameter.")
    if row["used_at"] is not None:
        raise AltruistOAuthError("OAuth state has already been used.")
    if row["expires_at"] < datetime.now(timezone.utc):
        raise AltruistOAuthError("OAuth state has expired.")

    updated = await conn.fetchval(
        """
        UPDATE altruist_oauth_states SET used_at = now()
        WHERE id = $1 AND used_at IS NULL
        RETURNING id
        """,
        row["id"],
    )
    if updated is None:
        # Lost a race with a concurrent callback using the same state.
        raise AltruistOAuthError("OAuth state has already been used.")

    return {"environment": row["environment"]}


# ── Authorization-code flow ──────────────────────────────────────────────

def _redirect_uri() -> str:
    uri = os.environ.get("ALTRUIST_REDIRECT_URI")
    if not uri:
        raise AltruistConfigError(
            "ALTRUIST_REDIRECT_URI is not set. Altruist requires an exact, "
            "pre-registered HTTPS redirect URI (no localhost — local dev "
            "needs a tunnel)."
        )
    if not uri.startswith("https://"):
        raise AltruistConfigError(
            f"ALTRUIST_REDIRECT_URI must be HTTPS, got: {uri!r}"
        )
    return uri


def _client_credentials(environment: str) -> tuple[str, str]:
    prefix = "ALTRUIST_SANDBOX" if environment == "sandbox" else "ALTRUIST_PRODUCTION"
    client_id = os.environ.get(f"{prefix}_CLIENT_ID")
    client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AltruistConfigError(
            f"{prefix}_CLIENT_ID / {prefix}_CLIENT_SECRET are not configured. "
            "No Altruist sandbox or production credentials exist in this "
            "project's Doppler config as of Sprint 1 — this is the documented "
            "blocker, not a code defect."
        )
    return client_id, client_secret


def build_authorize_url(*, environment: str, state: str, scope: str = "accounts:read") -> str:
    if environment not in ENVIRONMENTS:
        raise ValueError(f"Unknown environment: {environment!r}")
    client_id, _ = _client_credentials(environment)
    redirect_uri = _redirect_uri()
    login_base = ALTRUIST_HOSTS[environment]["login_base"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scope,
    }
    return f"{login_base}{_OAUTH_PATHS['authorize']}?{urlencode(params)}"


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str | None = None


async def exchange_code_for_tokens(*, environment: str, code: str, timeout: float = 15.0) -> TokenResponse:
    """Exchange an authorization code for tokens. Makes a REAL HTTP call.

    Never called by the verify script against the live Altruist sandbox — no
    credentials exist. Exercised in verify with a monkeypatched transport
    instead (see verify script Task 4).
    """
    client_id, client_secret = _client_credentials(environment)
    redirect_uri = _redirect_uri()
    login_base = ALTRUIST_HOSTS[environment]["login_base"]
    url = f"{login_base}{_OAUTH_PATHS['token']}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(
                f"Token exchange request to {url} failed at the transport layer: "
                f"{type(exc).__name__}"
            ) from exc

    if response.status_code >= 400:
        raise AltruistOAuthError(
            f"Token exchange was refused: HTTP {response.status_code}"
        )
    body = response.json()
    return _parse_token_response(body)


async def refresh_access_token(*, environment: str, refresh_token: str, timeout: float = 15.0) -> TokenResponse:
    """Rotate the access token using the stored refresh token.

    Altruist's 30-day refresh tokens may or may not rotate on use — [FIND]
    this response shape has never been observed live (no sandbox credentials
    exist). Handled defensively via _parse_token_response: if the response
    includes a new refresh_token, the caller stores it; if not, the caller
    keeps the existing one (see persist_refresh below).
    """
    client_id, client_secret = _client_credentials(environment)
    login_base = ALTRUIST_HOSTS[environment]["login_base"]
    url = f"{login_base}{_OAUTH_PATHS['token']}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(
                f"Token refresh request to {url} failed at the transport layer: "
                f"{type(exc).__name__}"
            ) from exc

    if response.status_code >= 400:
        raise AltruistOAuthError(
            f"Token refresh was refused: HTTP {response.status_code}"
        )
    body = response.json()
    return _parse_token_response(body, fallback_refresh_token=refresh_token)


def _parse_token_response(body: dict[str, Any], fallback_refresh_token: str | None = None) -> TokenResponse:
    access_token = body.get("access_token")
    if not access_token:
        raise AltruistOAuthError("Token response missing access_token.")
    # Defensive handling of the "does the refresh response include a new
    # refresh_token?" open question (see module docstring).
    refresh_token = body.get("refresh_token") or fallback_refresh_token
    if not refresh_token:
        raise AltruistOAuthError("Token response missing refresh_token.")
    expires_in = int(body.get("expires_in") or int(ACCESS_TOKEN_LIFETIME.total_seconds()))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        scope=body.get("scope"),
    )


# ── Connection persistence ──────────────────────────────────────────────

async def get_active_connection(conn, *, org_id: str, environment: str) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, org_id, environment, status, client_id, client_secret_last4,
               access_token_last4, refresh_token_last4, scope,
               token_expires_at, last_refreshed_at, connected_by,
               created_at, updated_at
        FROM altruist_connections
        WHERE org_id = $1 AND environment = $2 AND system_to IS NULL
        """,
        org_id, environment,
    )
    return dict(row) if row else None


async def _get_active_connection_row_full(conn, *, org_id: str, environment: str):
    """Internal: includes the encrypted columns. Never returned from an API response."""
    return await conn.fetchrow(
        """
        SELECT * FROM altruist_connections
        WHERE org_id = $1 AND environment = $2 AND system_to IS NULL
        """,
        org_id, environment,
    )


async def store_new_connection(
    conn,
    *,
    org_id: str,
    environment: str,
    client_id: str,
    client_secret: str,
    tokens: TokenResponse,
    connected_by: str | None,
) -> str:
    """Create (or replace, via system-axis archival) the active connection row.

    A genuine reconnect archives the prior row (system_to = now()) and inserts
    a new one — see the migration's comment for why this is system-time, not
    valid-time, restatement: it models "a new physical credential set supersedes
    the old one", not "we learned the old fact was wrong".
    """
    existing = await _get_active_connection_row_full(conn, org_id=org_id, environment=environment)
    if existing is not None:
        await conn.execute(
            "UPDATE altruist_connections SET system_to = now() WHERE id = $1",
            existing["id"],
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.expires_in)
    new_id = await conn.fetchval(
        """
        INSERT INTO altruist_connections (
            org_id, environment, status, client_id,
            client_secret_encrypted, client_secret_last4,
            access_token_encrypted, access_token_last4,
            refresh_token_encrypted, refresh_token_last4,
            scope, token_expires_at, last_refreshed_at, connected_by
        ) VALUES (
            $1, $2, 'connected', $3,
            $4, $5,
            $6, $7,
            $8, $9,
            $10, $11, now(), $12
        )
        RETURNING id
        """,
        org_id, environment, client_id,
        encrypt_secret(client_secret), last4(client_secret),
        encrypt_secret(tokens.access_token), last4(tokens.access_token),
        encrypt_secret(tokens.refresh_token), last4(tokens.refresh_token),
        tokens.scope, expires_at, connected_by,
    )
    return str(new_id)


async def persist_refresh(conn, *, connection_id: str, tokens: TokenResponse) -> None:
    """Update-in-place: an hourly token refresh does not version the row
    bi-temporally (see migration comment) — it updates the current row's
    token columns and last_refreshed_at.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.expires_in)
    await conn.execute(
        """
        UPDATE altruist_connections
        SET access_token_encrypted = $2,
            access_token_last4 = $3,
            refresh_token_encrypted = $4,
            refresh_token_last4 = $5,
            token_expires_at = $6,
            last_refreshed_at = now(),
            status = 'connected',
            updated_at = now()
        WHERE id = $1
        """,
        connection_id,
        encrypt_secret(tokens.access_token), last4(tokens.access_token),
        encrypt_secret(tokens.refresh_token), last4(tokens.refresh_token),
        expires_at,
    )


async def get_decrypted_refresh_token(conn, *, connection_id: str) -> str:
    row = await conn.fetchrow(
        "SELECT refresh_token_encrypted FROM altruist_connections WHERE id = $1",
        connection_id,
    )
    if row is None:
        raise AltruistOAuthError(f"No altruist_connections row with id={connection_id}")
    return decrypt_secret(row["refresh_token_encrypted"])


async def get_decrypted_access_token(conn, *, connection_id: str) -> str:
    row = await conn.fetchrow(
        "SELECT access_token_encrypted FROM altruist_connections WHERE id = $1",
        connection_id,
    )
    if row is None:
        raise AltruistOAuthError(f"No altruist_connections row with id={connection_id}")
    if row["access_token_encrypted"] is None:
        raise AltruistOAuthError(
            f"altruist_connections row id={connection_id} has no access token stored."
        )
    return decrypt_secret(row["access_token_encrypted"])


class AltruistNotConnected(RuntimeError):
    """Raised by the pre-connection guard: no active connection row exists.

    A distinct type (not a bare ValueError) so a caller making a real Altruist
    API call can catch specifically "not configured" and refuse BEFORE ever
    reaching the network — see Task 4's proof of the pre-connection guard.
    """


async def require_active_connection(conn, *, org_id: str, environment: str) -> dict:
    """Guard used by any future Altruist API call site (e.g. GET /v2/households).

    Raises AltruistNotConnected if no connection row exists for this org+environment,
    or if the connection's status is not 'connected' — the call must never reach
    the network in either case.
    """
    row = await get_active_connection(conn, org_id=org_id, environment=environment)
    if row is None:
        raise AltruistNotConnected(
            f"No Altruist connection configured for this organization "
            f"(environment={environment!r}). Connect via the authorize flow first."
        )
    if row["status"] != "connected":
        raise AltruistNotConnected(
            f"Altruist connection exists but status={row['status']!r} "
            f"(environment={environment!r}) — re-authorization required."
        )
    return row


# [FIND] Altruist Sprint 2 — path segments as documented, flagged inconsistent
# in the source Guides (households under /api/v2/, accounts under /v2/,
# neither confirmed against a live /reference page in this environment).
# Isolated here for the same one-edit-fixes-it reason as _OAUTH_PATHS above.
#
# [FIND] Altruist Sprint 3 — ``positions``/``transactions`` are NOT confirmed
# against any live spec either (checked this repo's docs/ for an existing
# Altruist API reference — none exists, same absence Sprint 1/2 found for the
# OAuth and households/accounts paths). No sandbox access exists to confirm
# against a live /reference page. Falls back to the same ``/v2/<resource>``
# convention ``accounts`` already uses, with the resource scoped by
# ``account_id`` as a query parameter — a guess, not a confirmed fact, and
# isolated here for the same one-edit-fixes-it reason as everything else in
# this dict.
_API_PATHS = {
    "households": "/api/v2/households",
    "accounts": "/v2/accounts",
    "positions": "/v2/positions",
    "transactions": "/v2/transactions",
}


def _extract_list(body: Any, *keys: str) -> list[dict]:
    """Altruist's real list-envelope shape has never been observed (no sandbox
    access — see module docstring). Defensively accept a bare JSON list, or
    the first of ``keys`` present on an object body, mirroring
    ``_parse_token_response``'s defensive field lookups above. [FIND] to
    correct once a real response is observed.
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if isinstance(value, list):
                return value
    raise AltruistOAuthError(
        f"Unexpected response shape from Altruist (expected a list or one of "
        f"{keys!r} to be a list): {type(body).__name__}"
    )


def _normalize_household(raw: dict) -> dict:
    return {
        "id": str(raw.get("id") or raw.get("household_id") or ""),
        "name": raw.get("name") or raw.get("household_name") or "",
        "raw": raw,
    }


def _normalize_account(raw: dict) -> dict:
    return {
        "id": str(raw.get("id") or raw.get("account_id") or ""),
        "name": raw.get("name") or raw.get("account_name") or raw.get("nickname") or "",
        "raw": raw,
    }


def _normalize_position(raw: dict) -> dict:
    """A single Altruist position, in the documented-or-best-guess shape.

    Field names are unconfirmed (same [FIND] as the path itself) — every
    candidate key is a defensive guess, exactly as ``_normalize_account``
    already does for ``id``/``name``. ``raw`` is kept whole so a caller can
    recover a field this function did not anticipate.
    """
    return {
        "id": raw.get("id") or raw.get("position_id"),
        "cusip": raw.get("cusip"),
        "isin": raw.get("isin"),
        "ticker": raw.get("ticker") or raw.get("symbol"),
        "name": raw.get("name") or raw.get("security_name") or raw.get("description"),
        "asset_type": raw.get("asset_type") or raw.get("security_type"),
        "quantity": raw.get("quantity") or raw.get("units"),
        "market_value": raw.get("market_value") or raw.get("value"),
        "cost_basis": raw.get("cost_basis"),
        "currency_code": raw.get("currency_code") or raw.get("currency"),
        "raw": raw,
    }


def _normalize_transaction(raw: dict) -> dict:
    """A single Altruist transaction, in the documented-or-best-guess shape.

    Same defensive-field-lookup approach as ``_normalize_position`` — see its
    docstring. ``type`` is left as whatever string Altruist sends; mapping it
    onto this codebase's ``transaction_type_code`` vocabulary is the caller's
    job (``altruist_positions_sync``), not this normalizer's.
    """
    return {
        "id": raw.get("id") or raw.get("transaction_id"),
        "type": raw.get("type") or raw.get("transaction_type") or raw.get("activity_type"),
        "cusip": raw.get("cusip"),
        "isin": raw.get("isin"),
        "ticker": raw.get("ticker") or raw.get("symbol"),
        "name": raw.get("name") or raw.get("security_name") or raw.get("description"),
        "trade_date": raw.get("trade_date") or raw.get("date"),
        "settle_date": raw.get("settle_date"),
        "quantity": raw.get("quantity") or raw.get("units"),
        "price": raw.get("price"),
        "gross_amount": raw.get("gross_amount") or raw.get("amount"),
        "fees": raw.get("fees"),
        "taxes": raw.get("taxes"),
        "net_amount": raw.get("net_amount"),
        "currency_code": raw.get("currency_code") or raw.get("currency"),
        "raw": raw,
    }


async def call_households(
    conn,
    *,
    org_id: str,
    environment: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET /api/v2/households — the households visible to this connection.

    Runs ``require_active_connection`` first, exactly as the Sprint 1 stub
    did, so a caller with no active connection never reaches the network.

    ``transport`` is an optional ``httpx.BaseTransport`` — production callers
    never pass it (a real ``httpx.AsyncClient`` opens a real socket); the
    verify script passes an ``httpx.MockTransport`` so this exact code path
    (URL construction, bearer header, JSON parsing) runs against a synthetic,
    documented-shape response instead of a live call.
    """
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    access_token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    api_base = ALTRUIST_HOSTS[environment]["api_base"]
    url = f"{api_base}{_API_PATHS['households']}"

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(
                f"GET {url} failed at the transport layer: {type(exc).__name__}"
            ) from exc

    if response.status_code >= 400:
        raise AltruistOAuthError(
            f"GET {url} was refused: HTTP {response.status_code}"
        )
    body = response.json()
    return [_normalize_household(h) for h in _extract_list(body, "households", "data", "results")]


async def fetch_accounts_for_household(
    conn,
    *,
    org_id: str,
    environment: str,
    household_id: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET /v2/accounts?household_id=... — the accounts under one household.

    Same guard-first, injectable-transport shape as ``call_households`` — see
    its docstring.
    """
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    access_token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    api_base = ALTRUIST_HOSTS[environment]["api_base"]
    url = f"{api_base}{_API_PATHS['accounts']}"

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            response = await client.get(
                url,
                params={"household_id": household_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(
                f"GET {url} failed at the transport layer: {type(exc).__name__}"
            ) from exc

    if response.status_code >= 400:
        raise AltruistOAuthError(
            f"GET {url} was refused: HTTP {response.status_code}"
        )
    body = response.json()
    return [_normalize_account(a) for a in _extract_list(body, "accounts", "data", "results")]


async def call_positions(
    conn,
    *,
    org_id: str,
    environment: str,
    account_id: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET /v2/positions?account_id=... — one account's current positions.

    Sprint 3. Same guard-first, injectable-transport shape as
    ``fetch_accounts_for_household`` — see its docstring. The path itself is
    an unconfirmed [FIND]; see ``_API_PATHS``.
    """
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    access_token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    api_base = ALTRUIST_HOSTS[environment]["api_base"]
    url = f"{api_base}{_API_PATHS['positions']}"

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            response = await client.get(
                url,
                params={"account_id": account_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(
                f"GET {url} failed at the transport layer: {type(exc).__name__}"
            ) from exc

    if response.status_code >= 400:
        raise AltruistOAuthError(
            f"GET {url} was refused: HTTP {response.status_code}"
        )
    body = response.json()
    return [_normalize_position(p) for p in _extract_list(body, "positions", "data", "results")]


async def call_transactions(
    conn,
    *,
    org_id: str,
    environment: str,
    account_id: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET /v2/transactions?account_id=... — one account's transaction history.

    Sprint 3. Same guard-first, injectable-transport shape as
    ``fetch_accounts_for_household`` — see its docstring. The path itself is
    an unconfirmed [FIND]; see ``_API_PATHS``.
    """
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    access_token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    api_base = ALTRUIST_HOSTS[environment]["api_base"]
    url = f"{api_base}{_API_PATHS['transactions']}"

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            response = await client.get(
                url,
                params={"account_id": account_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(
                f"GET {url} failed at the transport layer: {type(exc).__name__}"
            ) from exc

    if response.status_code >= 400:
        raise AltruistOAuthError(
            f"GET {url} was refused: HTTP {response.status_code}"
        )
    body = response.json()
    return [_normalize_transaction(t) for t in _extract_list(body, "transactions", "data", "results")]
