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

OAUTH HOST / PATH — DOCUMENTED (ALTREAD1, developer.altruist.com read 2026-11-11)
────────────────────────────────────────────────────────────────────────────
docs/open-api-authentication: authorize = the LOGIN host + ``/api/oauth2/authorize``
(``client_id``, ``scope=openid``, ``redirect_uri``, ``state``); token = the API
host + ``/oauth2/token`` (``api_base`` already ends in ``/altruist-open-api``),
form body client_id, client_secret, code | refresh_token, redirect_uri,
grant_type and ``token_type=id``. Access token 1 h, refresh token 28 days.
The cpsim01-era assumptions (``/oauth/authorize`` + ``/oauth/token`` on the
login host, scope ``accounts:read``) are listed as corrections in
docs/altruist/ALTREAD1_NOTES.md §3.

READ PATH — DOCUMENTED (docs/endpoints, docs/json-code-examples): V2 resources
under ``api_base``, paged (``page`` / ``page_size``), enveloped
``{"paging": {...}, "data": {"<key>": [...]}}``, dates ``YYYY/MM/DD``, an
optional ``X-API-Request-ID`` per request. See ``fetch_pages`` below.
"""

from __future__ import annotations

import base64
import calendar
import json
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

# ── Environment / host configuration ───────────────────────────────────────
# Confirmed real facts (sprint prompt): sandbox host
# openapi.stage1.altruistnet.tech/altruist-open-api/ (login portal
# oauth.stage1.altruistnet.tech); production openapi.altruist.com/altruist-open-api/
# (login portal oauth.altruist.com). Access token expires in 1 hour; refresh
# token valid 28 days (docs/open-api-authentication — ALTREAD1; was 30, unused).
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

# DOCUMENTED (docs/open-api-authentication): authorize on the LOGIN host, token on the API host.
_OAUTH_PATHS = {
    "authorize": "/api/oauth2/authorize",
    "token": "/oauth2/token",
}
#: which host each OAuth path lives on (the token endpoint moved to the API host — correction #3)
_OAUTH_HOST = {"authorize": "login_base", "token": "api_base"}
DEFAULT_SCOPE = "openid"
#: sent on BOTH grants (docs/open-api-authentication)
TOKEN_TYPE_PARAM = "id"

ACCESS_TOKEN_LIFETIME = timedelta(hours=1)
REFRESH_TOKEN_LIFETIME = timedelta(days=28)
STATE_TTL = timedelta(minutes=10)

log = logging.getLogger("altruist.client")

#: test injection: a zero-argument callable returning an aware datetime (the refresh-expiry clock)
_clock: Any = None


def set_clock(fn) -> None:
    global _clock
    _clock = fn


def utcnow() -> datetime:
    return _clock() if _clock is not None else datetime.now(timezone.utc)

ENVIRONMENTS = ("sandbox", "production", "simulator")

# ── cpsim02 — the third environment: a SIMULATED Altruist-compatible counterparty ──────────────────────────────
# D1: every branch below is explicit — sandbox, production and simulator each name their own variables, and any other
# value raises (never a silent fall-through to production). D2: the simulator's hosts are resolved PER CALL from two
# Super-Admin-only org settings (the allowed flag and the counterparty slug) and CPSIM_BASE_URL (non-secret, https).
# D3: its client id is the counterparty credential's public id, its secret the counterparty's cpsim_ key — both from
# Doppler (ALTRUIST_SIMULATOR_CLIENT_ID / _CLIENT_SECRET). The simulator is reached over HTTP like any counterparty;
# this module never imports the simulator package.
SIMULATOR = "simulator"
REAL_ENVIRONMENTS = ("sandbox", "production")
SIMULATOR_SECRET_PREFIX = "cpsim_"
SIMULATOR_PATH_MARK = "/cpsim/c/"
CPSIM_BASE_URL_ENV = "CPSIM_BASE_URL"
SETTING_SIM_ALLOWED = "counterparty_sim.allowed"
SETTING_SIM_COUNTERPARTY = "counterparty_sim.altruist_counterparty"
_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
#: test injection ONLY for the simulator environment (the verify routes simulator calls in-process through an ASGI
#: transport). Sandbox and production never read it.
_simulator_transport: Any = None


class AltruistOAuthError(RuntimeError):
    """Raised for any OAuth flow failure (bad state, exchange failure, etc.)."""


class AltruistConfigError(RuntimeError):
    """Raised when required configuration (redirect URI, client id/secret,
    encryption key) is missing. Distinct from AltruistOAuthError so a caller
    can tell "this deployment is not configured" from "the flow itself failed".
    """


class AltruistPagingCeiling(RuntimeError):
    """ALTREAD1 D1 — a paged read hit the page ceiling: the read STOPPED there. ``partial`` holds the rows read so far,
    ``pages`` how many pages were requested. A caller records the ceiling (it never silently looks complete)."""

    def __init__(self, message: str, *, partial: list, pages: int):
        super().__init__(message)
        self.partial = partial
        self.pages = pages


class AltruistSimulatorRefused(RuntimeError):
    """cpsim02 — the simulator environment is not available to this org (the allowed flag is off, or no counterparty
    is configured). Raised BEFORE any call; the router answers 409 with the reason."""


def set_simulator_transport(transport: Any) -> None:
    """Test injection: route every SIMULATOR-environment call through ``transport`` (e.g. an in-process ASGI
    transport). Sandbox and production are never affected."""
    global _simulator_transport
    _simulator_transport = transport


def _transport_for(environment: str, transport: Any) -> Any:
    if transport is not None:
        return transport
    return _simulator_transport if environment == SIMULATOR else None


def is_simulator_host(hosts: dict) -> bool:
    return any(SIMULATOR_PATH_MARK in (hosts.get(k) or "") for k in ("api_base", "login_base"))


def simulator_hosts(slug: str) -> dict[str, str]:
    """D2 — ``CPSIM_BASE_URL`` + ``/cpsim/c/<slug>`` for both the API and the login base. https only."""
    base = (os.environ.get(CPSIM_BASE_URL_ENV) or "").strip().rstrip("/")
    if not base:
        raise AltruistConfigError(f"{CPSIM_BASE_URL_ENV} is not set — the simulator environment has no address.")
    if not base.startswith("https://"):
        raise AltruistConfigError(f"{CPSIM_BASE_URL_ENV} must be an https URL.")
    if not slug or set(slug) - _SLUG_CHARS:
        raise AltruistSimulatorRefused("the configured simulated counterparty slug is not valid")
    root = f"{base}{SIMULATOR_PATH_MARK}{slug}"
    return {"api_base": root, "login_base": root}


async def simulator_counterparty(conn, *, org_id: str) -> str:
    """The slug of the simulated counterparty this org's ``simulator`` environment talks to. Refuses (raises
    AltruistSimulatorRefused) when ``counterparty_sim.allowed`` is not true or no counterparty is configured."""
    rows = await conn.fetch(
        "SELECT setting_key, setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = ANY($2::text[])",
        org_id, [SETTING_SIM_ALLOWED, SETTING_SIM_COUNTERPARTY],
    )
    values = {}
    for r in rows:
        v = r["setting_value"]
        values[r["setting_key"]] = json.loads(v) if isinstance(v, str) else v
    if values.get(SETTING_SIM_ALLOWED) is not True:
        raise AltruistSimulatorRefused(
            "the simulator environment is not enabled for this organization (counterparty_sim.allowed is off)")
    slug = values.get(SETTING_SIM_COUNTERPARTY)
    if not isinstance(slug, str) or not slug:
        raise AltruistSimulatorRefused(
            "no simulated counterparty is configured for this organization (counterparty_sim.altruist_counterparty)")
    return slug


async def resolve_hosts(conn, *, org_id: str, environment: str) -> dict[str, str]:
    """The hosts for one call. Sandbox / production: the static table, exactly as before (no query). Simulator: the
    org's configured counterparty, re-checked on every call (turning the flag off stops the next call)."""
    if environment in REAL_ENVIRONMENTS:
        return ALTRUIST_HOSTS[environment]
    if environment == SIMULATOR:
        return simulator_hosts(await simulator_counterparty(conn, org_id=org_id))
    raise ValueError(f"Unknown environment: {environment!r}")


def _static_hosts(environment: str, hosts: dict | None) -> dict[str, str]:
    if hosts is not None:
        return hosts
    if environment in REAL_ENVIRONMENTS:
        return ALTRUIST_HOSTS[environment]
    if environment == SIMULATOR:
        raise AltruistSimulatorRefused("the simulator environment needs its hosts resolved for the org first")
    raise ValueError(f"Unknown environment: {environment!r}")


def check_destination(environment: str, hosts: dict, client_secret: str | None = None) -> None:
    """D1 — no cross-environment credentials, in either direction:
    a simulator environment sends only to a simulator host, and only a ``cpsim_`` secret;
    sandbox / production never send to a simulator host, and never a ``cpsim_`` secret."""
    sim_host = is_simulator_host(hosts)
    if environment == SIMULATOR:
        if not sim_host:
            raise AltruistConfigError("refusing to send simulator credentials to a non-simulator host")
        if client_secret is not None and not client_secret.startswith(SIMULATOR_SECRET_PREFIX):
            raise AltruistConfigError(
                "refusing to send a simulator client secret that lacks the cpsim_ prefix")
        return
    if environment not in REAL_ENVIRONMENTS:
        raise ValueError(f"Unknown environment: {environment!r}")
    if sim_host:
        raise AltruistConfigError(f"refusing to send {environment} credentials to a simulator host")
    if client_secret is not None and client_secret.startswith(SIMULATOR_SECRET_PREFIX):
        raise AltruistConfigError(f"refusing to send a simulator (cpsim_) secret as {environment} credentials")


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


def client_credentials(environment: str) -> tuple[str, str]:
    # cpsim02 D1 — explicit branches; production is `== "production"` EXACTLY, anything unknown raises.
    if environment == "sandbox":
        prefix = "ALTRUIST_SANDBOX"
    elif environment == "production":
        prefix = "ALTRUIST_PRODUCTION"
    elif environment == SIMULATOR:
        prefix = "ALTRUIST_SIMULATOR"
    else:
        raise AltruistConfigError(f"Unknown Altruist environment {environment!r} — no credentials are configured "
                                  f"for it (never a fallback to production).")
    client_id = os.environ.get(f"{prefix}_CLIENT_ID")
    client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AltruistConfigError(
            f"{prefix}_CLIENT_ID / {prefix}_CLIENT_SECRET are not configured. "
            "No Altruist sandbox or production credentials exist in this "
            "project's Doppler config as of Sprint 1 — this is the documented "
            "blocker, not a code defect."
        )
    if environment == SIMULATOR and not client_secret.startswith(SIMULATOR_SECRET_PREFIX):
        raise AltruistConfigError(
            "ALTRUIST_SIMULATOR_CLIENT_SECRET must be a simulator key (cpsim_ prefix) — refusing to send it")
    if environment != SIMULATOR and client_secret.startswith(SIMULATOR_SECRET_PREFIX):
        raise AltruistConfigError(f"{prefix}_CLIENT_SECRET is a simulator key (cpsim_) — refusing to send it")
    return client_id, client_secret


def build_authorize_url(*, environment: str, state: str, scope: str = DEFAULT_SCOPE,
                        hosts: dict | None = None, credentials: tuple[str, str] | None = None,
                        redirect_uri: str | None = None) -> str:
    """``hosts`` / ``credentials`` / ``redirect_uri`` are cpsim02 overrides (the simulator's per-org hosts; the
    conformance suite's pasted key). Omitted, everything is read exactly as before."""
    if environment not in ENVIRONMENTS:
        raise ValueError(f"Unknown environment: {environment!r}")
    client_id, client_secret = credentials or client_credentials(environment)
    redirect_uri = redirect_uri or _redirect_uri()
    resolved = _static_hosts(environment, hosts)
    check_destination(environment, resolved, client_secret)
    # DOCUMENTED parameters: client_id, scope (openid), redirect_uri, state. response_type=code is the OAuth 2.0
    # authorization-code request's own parameter — not shown on the page, kept (a standard server requires it).
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scope,
    }
    return f"{oauth_url(resolved, 'authorize')}?{urlencode(params)}"


def oauth_url(hosts: dict, which: str) -> str:
    """The documented OAuth URL: authorize on the login host, token on the API host."""
    return f"{hosts[_OAUTH_HOST[which]]}{_OAUTH_PATHS[which]}"


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str | None = None
    #: ALTREAD1 — True when the response carried a refresh token (a NEW one: its 28-day clock restarts). False only for
    #: a refresh response that omitted it (the stored one is kept, and so is its issue time).
    refresh_issued: bool = True


async def exchange_code_for_tokens(*, environment: str, code: str, timeout: float = 15.0,
                                   hosts: dict | None = None, transport: Any = None,
                                   credentials: tuple[str, str] | None = None,
                                   redirect_uri: str | None = None) -> TokenResponse:
    """Exchange an authorization code for tokens. Makes a REAL HTTP call.

    Never called by the verify script against the live Altruist sandbox — no
    credentials exist. Exercised in verify with a monkeypatched transport
    instead (see verify script Task 4).
    """
    client_id, client_secret = credentials or client_credentials(environment)
    redirect_uri = redirect_uri or _redirect_uri()
    resolved = _static_hosts(environment, hosts)
    check_destination(environment, resolved, client_secret)
    url = oauth_url(resolved, "token")

    async with httpx.AsyncClient(timeout=timeout, transport=_transport_for(environment, transport)) as client:
        try:
            response = await client.post(
                url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "token_type": TOKEN_TYPE_PARAM,
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


async def refresh_access_token(*, environment: str, refresh_token: str, timeout: float = 15.0,
                               hosts: dict | None = None, transport: Any = None,
                               credentials: tuple[str, str] | None = None,
                               redirect_uri: str | None = None) -> TokenResponse:
    """Rotate the access token using the stored refresh token.

    Altruist's 28-day refresh tokens may or may not rotate on use — [FIND]
    this response shape has never been observed live (no sandbox credentials
    exist). Handled defensively via _parse_token_response: if the response
    includes a new refresh_token, the caller stores it; if not, the caller
    keeps the existing one (see persist_refresh below).
    """
    client_id, client_secret = credentials or client_credentials(environment)
    resolved = _static_hosts(environment, hosts)
    check_destination(environment, resolved, client_secret)
    url = oauth_url(resolved, "token")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_type": TOKEN_TYPE_PARAM,
    }
    # the documented body lists redirect_uri for the refresh grant too — sent when one is configured / given
    uri = redirect_uri or os.environ.get("ALTRUIST_REDIRECT_URI")
    if uri:
        data["redirect_uri"] = uri

    async with httpx.AsyncClient(timeout=timeout, transport=_transport_for(environment, transport)) as client:
        try:
            response = await client.post(
                url,
                data=data,
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
        refresh_issued=bool(body.get("refresh_token")),
    )


# ── Connection persistence ──────────────────────────────────────────────

async def get_active_connection(conn, *, org_id: str, environment: str) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, org_id, environment, status, client_id, client_secret_last4,
               access_token_last4, refresh_token_last4, scope,
               token_expires_at, last_refreshed_at, connected_by,
               created_at, updated_at, refresh_token_issued_at
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
            scope, token_expires_at, last_refreshed_at, connected_by,
            refresh_token_issued_at
        ) VALUES (
            $1, $2, 'connected', $3,
            $4, $5,
            $6, $7,
            $8, $9,
            $10, $11, now(), $12,
            now()
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
            updated_at = now(),
            refresh_token_issued_at = CASE WHEN $7 THEN now() ELSE refresh_token_issued_at END
        WHERE id = $1
        """,
        connection_id,
        encrypt_secret(tokens.access_token), last4(tokens.access_token),
        encrypt_secret(tokens.refresh_token), last4(tokens.refresh_token),
        expires_at, bool(tokens.refresh_issued),
    )


# ── ALTREAD1 D1 — the refresh token's documented 28-day lifetime ────────────
def refresh_token_expires_at(connection: dict | None) -> datetime | None:
    """When the stored refresh token expires: its issue time + 28 days. A connection made before ALTREAD1 has no issue
    time; ``created_at`` is used — conservative, it can only flag EARLY (a reconnect fixes it), never late."""
    if not connection:
        return None
    issued = connection.get("refresh_token_issued_at") or connection.get("created_at")
    return issued + REFRESH_TOKEN_LIFETIME if issued else None


def refresh_token_expired(connection: dict | None, now: datetime | None = None) -> bool:
    exp = refresh_token_expires_at(connection)
    return bool(exp is not None and exp <= (now or utcnow()))


class AltruistRefreshExpired(RuntimeError):
    """The connection's refresh token is past its documented 28-day lifetime — re-authorization is required. Raised by
    ``require_active_connection`` before any call (a subclass of ``AltruistNotConnected`` is set below, so every
    existing 409 handler answers it with this reason)."""


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


class AltruistRefreshExpiredError(AltruistNotConnected, AltruistRefreshExpired):
    """ALTREAD1 — the refresh token expired: an ``AltruistNotConnected`` (every router already answers 409 with the
    reason) that is also an ``AltruistRefreshExpired``."""


async def close_connection(conn, *, org_id: str, environment: str) -> bool:
    """Disconnect: bitemporal close of the active row, never a hard delete.

    Matches ``store_new_connection``'s own archival convention for this table
    (system-axis: the row's id is kept stable, only ``system_to`` changes) —
    that convention was fixed by Sprint 1, not chosen here. Returns False (a
    no-op, not an error) if no connection was active — disconnecting twice is
    not a failure.
    """
    closed_id = await conn.fetchval(
        """
        UPDATE altruist_connections
        SET status = 'disconnected', updated_at = now(), system_to = now()
        WHERE org_id = $1 AND environment = $2 AND system_to IS NULL
        RETURNING id
        """,
        org_id, environment,
    )
    return closed_id is not None


# ── Automatic token refresh — INTERIM, ON-DEMAND (Sprint 4 Task 1c/2) ───────
#
# [FIND] No generic recurring-job/scheduler mechanism exists in this app that
# a timer-based Altruist refresh could hook into. The one real cron process,
# ``workflow_scheduler_tick.py`` (Render cron, every 5 minutes), is
# purpose-built to scan `workflow_triggers` for BPMN workflow runs — it has no
# generic "run this job" table or registry a different subsystem could
# register into. Building a second, Altruist-only Render cron service was
# judged out of scope for a sprint with no live sandbox credentials to
# actually validate a refresh against. Per the sprint's own standing rule,
# this ships the authorized interim answer instead: a just-in-time check,
# run at every real choke point a token is about to be used — inside
# ``require_active_connection`` (every future live Altruist API call already
# goes through it) and inside the connection-status endpoint. Swapping this
# for a real timer later needs no caller change: ``ensure_fresh_token`` is the
# only function that would move.

DEFAULT_REFRESH_THRESHOLD = timedelta(minutes=5)


async def ensure_fresh_token(
    conn,
    *,
    org_id: str,
    environment: str,
    connection: dict,
    threshold: timedelta = DEFAULT_REFRESH_THRESHOLD,
) -> dict:
    """Refresh the access token in place if it is expired or expiring soon.

    A no-op (no network call at all) unless ``token_expires_at`` is within
    ``threshold`` of now — every Sprint 1-3 fixture's token expires an hour
    out, so this never fires for their verify scripts. If the refresh call
    itself fails (e.g. still-unconfigured sandbox credentials) and the token
    has NOT yet actually expired, the failure is swallowed and the still-valid
    token is returned as-is — a transient refresh failure must not turn a
    working connection into a hard failure. If the token HAS already expired,
    the failure is re-raised: there is no valid token left to fall back to.
    """
    expires_at = connection.get("token_expires_at")
    if expires_at is None:
        return connection
    now = datetime.now(timezone.utc)
    if expires_at - now > threshold:
        return connection

    try:
        refresh_token = await get_decrypted_refresh_token(conn, connection_id=connection["id"])
        if environment == SIMULATOR:
            hosts = await resolve_hosts(conn, org_id=org_id, environment=environment)
            tokens = await refresh_access_token(environment=environment, refresh_token=refresh_token, hosts=hosts)
        else:
            tokens = await refresh_access_token(environment=environment, refresh_token=refresh_token)
    except (AltruistOAuthError, AltruistConfigError, AltruistSimulatorRefused):
        if expires_at <= now:
            raise
        return connection

    await persist_refresh(conn, connection_id=connection["id"], tokens=tokens)
    refreshed = await get_active_connection(conn, org_id=org_id, environment=environment)
    return refreshed or connection


async def require_active_connection(
    conn,
    *,
    org_id: str,
    environment: str,
    auto_refresh: bool = True,
    refresh_threshold: timedelta = DEFAULT_REFRESH_THRESHOLD,
) -> dict:
    """Guard used by any future Altruist API call site (e.g. GET /v2/households).

    Raises AltruistNotConnected if no connection row exists for this org+environment,
    or if the connection's status is not 'connected' — the call must never reach
    the network in either case. Also runs the on-demand refresh check (see
    ``ensure_fresh_token`` above) by default, so a caller reaching this guard
    never proceeds with a token that is about to expire — pass
    ``auto_refresh=False`` to opt out (e.g. the OAuth flow's own bookkeeping).
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
    if refresh_token_expired(row):
        exp = refresh_token_expires_at(row)
        raise AltruistRefreshExpiredError(
            f"The Altruist refresh token expired on {exp.date().isoformat()} (the documented 28-day lifetime, "
            f"environment={environment!r}) — reconnect to authorize again. Nothing was fetched."
        )
    if auto_refresh:
        row = await ensure_fresh_token(
            conn, org_id=org_id, environment=environment, connection=row, threshold=refresh_threshold
        )
    return row


# ── ALTREAD1 D1 — the documented read path ──────────────────────────────────
#
# DOCUMENTED (developer.altruist.com docs/endpoints + resource pages, read 2026-11-11). V2 only — the V1 paths
# (/api/v1/households, /api/v1/accounts/{household_id}, /api/v1/positions/{account_id}, /api/v1/cost-basis/{account_id})
# are deprecating and never used. The households path is ONE constant: the endpoints index says /api/v2/households,
# the households page itself shows /v2/households — the portal is inconsistent (ALTRUIST_CONTRACT.md §3.1).
HOUSEHOLDS_PATH = "/api/v2/households"
_API_PATHS = {
    "households": HOUSEHOLDS_PATH,
    "accounts": "/v2/accounts",
    "positions": "/v2/accounts/{account_id}/positions/",
    "cost_basis": "/v2/accounts/{account_id}/cost-basis/",
    "transactions": "/v2/accounts/{account_id}/transactions",
}
#: the documented page_size maximum per resource (default 100 if not sent); the client always asks for the max
PAGE_SIZE_MAX = {"households": 1000, "accounts": 1000, "positions": 1000, "cost_basis": 1000, "transactions": 500}
#: the hard ceiling on pages for ONE paged read: hitting it STOPS the read and is recorded (never silently "complete")
MAX_PAGES = 200
REQUEST_ID_HEADER = "X-API-Request-ID"
#: transactions: the first sync's look-back, the later syncs' overlap, the window walked (≤ 1 calendar month)
LOOKBACK_MONTHS = 13
OVERLAP_BUSINESS_DAYS = 5

_DATE_RX = re.compile(r"^(\d{4})([/-])(\d{2})\2(\d{2})$")
_TS_RX = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


def parse_altruist_date(value: Any) -> date | None:
    """DOCUMENTED dates are ``YYYY/MM/DD`` (json-code-examples); ISO ``YYYY-MM-DD`` (and an ISO timestamp's date part)
    is accepted too. ``None`` / blank → None. Anything else raises ``ValueError`` — a per-row error, never a guess."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"date {value!r} is not a string")
    s = value.strip()
    if not s:
        return None
    m = _DATE_RX.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        except ValueError as exc:
            raise ValueError(f"date {s!r} is not a real calendar date") from exc
    m = _TS_RX.match(s)
    if m:
        return date.fromisoformat(m.group(1))
    raise ValueError(f"date {s!r} is neither YYYY/MM/DD nor YYYY-MM-DD")


def to_decimal(value: Any, what: str = "value") -> Decimal | None:
    """A JSON number or decimal string → Decimal (exact; a float goes through repr). None / blank → None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what} {value!r} is not a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{what} {value!r} is not a number") from exc


def sum_parts(raw: dict, keys: tuple[str, ...], what: str) -> Decimal | None:
    """The documented split amounts (e.g. transaction_fees + broker_fees + other_fees), summed EXACTLY. None when none
    of the keys is present (so an old fixture's single field can stand in); a missing / blank part counts as 0."""
    if not any(k in raw for k in keys):
        return None
    total = Decimal(0)
    for k in keys:
        v = to_decimal(raw.get(k), f"{what}.{k}")
        total += v or Decimal(0)
    return total


def mask_number(value: Any) -> str | None:
    """An account number reduced to its last four (never stored, logged or returned whole)."""
    if value is None:
        return None
    s = str(value).strip()
    return s[-4:] if s else None


def _extract_envelope(body: Any, key: str) -> tuple[list[dict], dict | None, str, dict]:
    """``(items, paging, form, data_object)`` from one response body.

    DOCUMENTED (json-code-examples): ``{"paging": {page, page_size, total_pages}, "data": {"<key>": [...], …}}``.
    Tolerated for old fixtures: a bare list, ``{"<key>": [...]}``, ``{"data": [...]}``, ``{"results": [...]}``.
    Anything else → ``AltruistOAuthError`` naming what was expected."""
    if isinstance(body, list):
        return body, None, "list", {}
    if isinstance(body, dict):
        paging = body.get("paging") if isinstance(body.get("paging"), dict) else None
        data = body.get("data")
        if isinstance(data, dict):
            value = data.get(key)
            if isinstance(value, list):
                return value, paging, "documented", data
            raise AltruistOAuthError(
                f"Unexpected response shape from Altruist: data.{key} is "
                f"{'missing' if key not in data else type(value).__name__}, expected a list (documented envelope "
                f"{{\"paging\": …, \"data\": {{\"{key}\": [...]}}}})")
        if isinstance(data, list):
            return data, paging, "data_list", {}
        value = body.get(key)
        if isinstance(value, list):
            return value, paging, "flat", {}
        if isinstance(body.get("results"), list):
            return body["results"], paging, "results", {}
    raise AltruistOAuthError(
        f"Unexpected response shape from Altruist: expected the documented envelope "
        f"{{\"paging\": …, \"data\": {{\"{key}\": [...]}}}} (or a list), got {type(body).__name__}"
        + (f" with keys {sorted(body)[:8]}" if isinstance(body, dict) else ""))


def _extract_list(body: Any, *keys: str) -> list[dict]:
    """Kept for callers of the cpsim02-era name: the items of ``keys[0]`` (envelope rules as above)."""
    return _extract_envelope(body, keys[0])[0]


def _normalize_household(raw: dict) -> dict:
    return {
        "id": str(raw.get("id") or raw.get("household_id") or ""),
        "name": raw.get("name") or raw.get("household_name") or "",
        "raw": raw,
    }


def _normalize_account(raw: dict) -> dict:
    """DOCUMENTED account fields: id, rep_code, account_number, account_type, account_name, status, date_open,
    date_closed, as_of_date, account_balance. The account number is reduced to its last four HERE — the normalized row
    (and its ``raw``) never carries the full number."""
    safe = dict(raw)
    if "account_number" in safe:
        safe["account_number"] = f"***{mask_number(safe['account_number']) or ''}"
    return {
        "id": str(raw.get("id") or raw.get("account_id") or ""),
        "name": raw.get("account_name") or raw.get("name") or raw.get("nickname") or "",
        "account_type": raw.get("account_type"),
        "status": raw.get("status"),
        "rep_code": raw.get("rep_code"),
        "account_number_last4": mask_number(raw.get("account_number")),
        "account_balance": raw.get("account_balance"),
        "as_of_date": raw.get("as_of_date"),
        "date_open": raw.get("date_open"),
        "date_closed": raw.get("date_closed"),
        "raw": safe,
    }


def _normalize_position(raw: dict) -> dict:
    """DOCUMENTED position fields: id, cusip, symbol, description, long_short_indicator, currency_code, quantity,
    as_of_date, security_type, market_value. The cpsim01-era aliases (ticker, name, asset_type, …) stay as fallbacks
    for old fixtures; ``raw`` is kept whole."""
    return {
        "id": raw.get("id") or raw.get("position_id"),
        "cusip": raw.get("cusip"),
        "isin": raw.get("isin"),
        "ticker": raw.get("symbol") or raw.get("ticker"),
        "name": raw.get("description") or raw.get("name") or raw.get("security_name"),
        "asset_type": raw.get("security_type") or raw.get("asset_type"),
        "long_short_indicator": raw.get("long_short_indicator"),
        "quantity": raw.get("quantity") if raw.get("quantity") is not None else raw.get("units"),
        "market_value": raw.get("market_value") if raw.get("market_value") is not None else raw.get("value"),
        "cost_basis": raw.get("cost_basis"),
        "currency_code": raw.get("currency_code") or raw.get("currency"),
        "as_of_date": raw.get("as_of_date") or raw.get("as_of"),
        "raw": raw,
    }


def _normalize_lot(raw: dict) -> dict:
    """DOCUMENTED cost-basis fields (one row per TAX LOT): id, cusip, symbol, description, quantity, market_value,
    cost_basis_unadjusted, cost_basis_adjusted, unrealized_gain_loss, as_of_date, open_date."""
    return {
        "id": raw.get("id"),
        "cusip": raw.get("cusip"),
        "ticker": raw.get("symbol"),
        "name": raw.get("description"),
        "quantity": raw.get("quantity"),
        "market_value": raw.get("market_value"),
        "cost_basis_unadjusted": raw.get("cost_basis_unadjusted"),
        "cost_basis_adjusted": raw.get("cost_basis_adjusted"),
        "unrealized_gain_loss": raw.get("unrealized_gain_loss"),
        "as_of_date": raw.get("as_of_date"),
        "open_date": raw.get("open_date"),
        "raw": raw,
    }


FEE_FIELDS = ("transaction_fees", "broker_fees", "other_fees")
TAX_FIELDS = ("TEFRA_withheld", "state_tax_withheld")


def _normalize_transaction(raw: dict) -> dict:
    """DOCUMENTED transaction fields: id, cusip, symbol, security_type, quantity, price, amount, interest,
    currency_code, transaction_type, transaction_subtype, transaction_description, cancellation_reversal_flag,
    transaction_fees, broker_fees, other_fees, TEFRA_withheld, state_tax_withheld, as_of_date, transaction_date,
    settle_date. Values stay as sent (strings / numbers); dates and amounts are parsed by the sync so a bad one is a
    per-row error. ``fees`` / ``taxes`` are the documented parts summed exactly (an old fixture's single field when
    none of the parts is present). ``type`` is left as Altruist sends it — mapping is the caller's job."""
    fees = sum_parts(raw, FEE_FIELDS, "fees")
    taxes = sum_parts(raw, TAX_FIELDS, "taxes")
    return {
        "id": raw.get("id") or raw.get("transaction_id"),
        "type": raw.get("transaction_type") or raw.get("type") or raw.get("activity_type"),
        "subtype": raw.get("transaction_subtype"),
        "description": raw.get("transaction_description") or raw.get("description"),
        "cusip": raw.get("cusip"),
        "isin": raw.get("isin"),
        "ticker": raw.get("symbol") or raw.get("ticker"),
        "name": raw.get("name") or raw.get("security_name"),
        "security_type": raw.get("security_type"),
        "trade_date": raw.get("transaction_date") or raw.get("trade_date") or raw.get("date"),
        "settle_date": raw.get("settle_date"),
        "as_of_date": raw.get("as_of_date"),
        "quantity": raw.get("quantity") if raw.get("quantity") is not None else raw.get("units"),
        "price": raw.get("price"),
        "gross_amount": raw.get("amount") if raw.get("amount") is not None else raw.get("gross_amount"),
        "interest": raw.get("interest"),
        "fees": fees if fees is not None else raw.get("fees"),
        "taxes": taxes if taxes is not None else raw.get("taxes"),
        "net_amount": raw.get("net_amount"),
        "currency_code": raw.get("currency_code") or raw.get("currency"),
        "cancellation_reversal_flag": raw.get("cancellation_reversal_flag"),
        "raw": raw,
    }


#: resource → (path key, the query / path parameter, envelope list key, the data.<id> echo key, normalizer)
_RESOURCES = {
    "households": ("households", None, "households", None, _normalize_household),
    "accounts": ("accounts", "household_id", "accounts", "household_id", _normalize_account),
    "positions": ("positions", "{account_id}", "positions", "account_id", _normalize_position),
    "cost_basis": ("cost_basis", "{account_id}", "cost_basis", "account_id", _normalize_lot),
    "transactions": ("transactions", "{account_id}", "transactions", "account_id", _normalize_transaction),
}


@dataclass
class FetchResult:
    """One paged read: the normalized rows (deduped by id), how many pages were requested, the X-API-Request-ID of
    each request, whether the ceiling stopped it, the page base the server used (0 / 1 / None unknown) and the
    envelope form it answered in ('documented' | 'flat' | 'list' | 'data_list' | 'results')."""
    items: list[dict] = field(default_factory=list)
    pages: int = 0
    request_ids: list[str] = field(default_factory=list)
    ceiling_hit: bool = False
    page_base: int | None = None
    envelope: str | None = None
    url: str = ""


def resource_url(api_base: str, resource: str, param: str | None) -> tuple[str, dict]:
    """(url, base query) for one resource. An account id is a PATH segment (URL-quoted), a household id a query."""
    path_key, param_spec, _key, _echo, _norm = _RESOURCES[resource]
    path = _API_PATHS[path_key]
    if param_spec == "{account_id}":
        if not param:
            raise ValueError(f"{resource} needs an account id")
        return f"{api_base}{path.format(account_id=quote(str(param), safe=''))}", {}
    if param_spec is None:
        return f"{api_base}{path}", {}
    return f"{api_base}{path}", ({param_spec: param} if param is not None else {})


def _item_key(item: dict) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    rid = raw.get("id") if isinstance(raw, dict) else None
    return f"id:{rid}" if rid not in (None, "") else "row:" + json.dumps(raw, sort_keys=True, default=str)


async def fetch_pages(
    *,
    api_base: str,
    resource: str,
    access_token: str,
    param: str | None = None,
    query: dict | None = None,
    page_size: int | None = None,
    max_pages: int = MAX_PAGES,
    timeout: float = 15.0,
    transport: Any = None,
) -> FetchResult:
    """GET every page of one resource (D1).

    Each request: ``Authorization: Bearer <token>``, ``Accept: application/json``, a fresh ``X-API-Request-ID`` (uuid4,
    logged with method / path / status — never a token), ``page`` + ``page_size`` (the documented max unless given).
    Stops on: an empty page; the server's ``paging.total_pages`` reached in ITS OWN page base (the first
    ``paging.page`` echo decides 0 or 1 — the docs' example says 1 while the parameter defaults to 0); a page that adds
    no new id (a server ignoring ``page``; one such page right after the first is allowed — the base-1 probe); the
    ``max_pages`` ceiling (``ceiling_hit``). A base-1 server answering page 0 empty is retried at page 1. ``≥ 400`` and
    transport failures raise ``AltruistOAuthError``; a body that is not the documented envelope (or a tolerated old
    form) raises it too, naming what was expected."""
    path_key, _spec, list_key, echo_key, normalize = _RESOURCES[resource]
    url, base_q = resource_url(api_base, resource, param)
    size = int(page_size or PAGE_SIZE_MAX[path_key])
    if size < 1 or size > PAGE_SIZE_MAX[path_key]:
        raise ValueError(f"page_size {size} is outside 1..{PAGE_SIZE_MAX[path_key]} for {resource}")
    out = FetchResult(url=url)
    seen: set[str] = set()
    page = 0
    retried_zero = False
    dup_probe_used = False
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        while True:
            if out.pages >= max_pages:
                out.ceiling_hit = True
                log.warning("altruist GET %s stopped at the page ceiling (%s pages)", url, out.pages)
                break
            rid = str(uuid.uuid4())
            params = {**base_q, **(query or {}), "page": page, "page_size": size}
            try:
                response = await client.get(url, params=params, headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    REQUEST_ID_HEADER: rid,
                })
            except httpx.HTTPError as exc:
                raise AltruistOAuthError(
                    f"GET {url} failed at the transport layer: {type(exc).__name__} (request id {rid})") from exc
            out.pages += 1
            out.request_ids.append(rid)
            log.info("altruist GET %s page=%s status=%s request_id=%s", url, page, response.status_code, rid)
            if response.status_code >= 400:
                raise AltruistOAuthError(
                    f"GET {url} was refused: HTTP {response.status_code} (request id {rid})")
            try:
                body = response.json()
            except ValueError as exc:
                raise AltruistOAuthError(f"GET {url} answered a body that is not JSON (request id {rid})") from exc
            items, paging, form, data = _extract_envelope(body, list_key)
            out.envelope = out.envelope or form
            if echo_key and param is not None and data.get(echo_key) not in (None, "") \
                    and str(data.get(echo_key)) != str(param):
                raise AltruistOAuthError(
                    f"GET {url}: the response names {echo_key} {data.get(echo_key)!r}, not the one requested "
                    f"(request id {rid})")
            echoed = paging.get("page") if paging else None
            echoed = echoed if isinstance(echoed, int) and not isinstance(echoed, bool) else None
            total = paging.get("total_pages") if paging else None
            total = total if isinstance(total, int) and not isinstance(total, bool) else None
            if out.page_base is None and echoed is not None:
                out.page_base = 1 if (page == 0 and echoed == 1) else (0 if page == 0 and echoed == 0 else None)
            if not items:
                if page == 0 and not retried_zero and (total or 0) >= 1 and not seen:
                    retried_zero = True       # a base-1 server that answers page 0 empty
                    out.page_base = 1
                    page = 1
                    continue
                break
            new = 0
            for it in items:
                if not isinstance(it, dict):
                    raise AltruistOAuthError(f"GET {url}: a {list_key} entry is not an object (request id {rid})")
                n = normalize(it)
                k = _item_key(n)
                if k in seen:
                    continue
                seen.add(k)
                out.items.append(n)
                new += 1
            if new == 0:
                if out.pages == 2 and not dup_probe_used:
                    dup_probe_used = True     # page 0 and page 1 were the same page: a base-1 server, no echo
                    page += 1
                    continue
                break
            current = echoed if echoed is not None else page
            if total is not None and echoed is not None:
                base = out.page_base if out.page_base is not None else 0
                if current >= total - 1 + base:
                    break
            page = current + 1
    return out


async def fetch_resource(
    *,
    api_base: str,
    resource: str,
    access_token: str,
    param: str | None = None,
    timeout: float = 15.0,
    transport: Any = None,
    query: dict | None = None,
    page_size: int | None = None,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """Every row of one resource (``fetch_pages``), normalized. Used by every ``call_*`` below and by the conformance
    suite. A read stopped by the page ceiling raises ``AltruistPagingCeiling`` carrying the partial rows."""
    res = await fetch_pages(api_base=api_base, resource=resource, access_token=access_token, param=param,
                            query=query, page_size=page_size, max_pages=max_pages, timeout=timeout,
                            transport=transport)
    if res.ceiling_hit:
        raise AltruistPagingCeiling(
            f"GET {res.url} stopped at the page ceiling ({res.pages} pages, {len(res.items)} rows read) — the read is "
            f"incomplete", partial=res.items, pages=res.pages)
    return res.items


async def _call(conn, *, org_id: str, environment: str, resource: str, param: str | None, timeout: float,
                transport: Any, query: dict | None = None) -> list[dict]:
    connection = await require_active_connection(conn, org_id=org_id, environment=environment)
    access_token = await get_decrypted_access_token(conn, connection_id=connection["id"])
    hosts = await resolve_hosts(conn, org_id=org_id, environment=environment)
    if environment == SIMULATOR:
        check_destination(environment, hosts)
    return await fetch_resource(api_base=hosts["api_base"], resource=resource, access_token=access_token,
                                param=param, timeout=timeout, transport=_transport_for(environment, transport),
                                query=query)


async def call_households(
    conn,
    *,
    org_id: str,
    environment: str,
    timeout: float = 15.0,
    transport: Any = None,
    name_pattern: str | None = None,
) -> list[dict]:
    """GET {api_base}/api/v2/households (every page) — the households visible to this connection. ``name_pattern`` is
    the documented optional filter.

    Runs ``require_active_connection`` first, so a caller with no active connection (or an expired refresh token)
    never reaches the network. ``transport`` is an optional ``httpx`` transport — production callers never pass it."""
    return await _call(conn, org_id=org_id, environment=environment, resource="households", param=None,
                       timeout=timeout, transport=transport,
                       query={"name_pattern": name_pattern} if name_pattern else None)


async def fetch_accounts_for_household(
    conn,
    *,
    org_id: str,
    environment: str,
    household_id: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET {api_base}/v2/accounts?household_id=… (every page) — the accounts under one household, each with its
    documented account_balance / as_of_date / status (the account number reduced to its last four)."""
    return await _call(conn, org_id=org_id, environment=environment, resource="accounts", param=household_id,
                       timeout=timeout, transport=transport)


async def call_positions(
    conn,
    *,
    org_id: str,
    environment: str,
    account_id: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET {api_base}/v2/accounts/{account_id}/positions/ (every page) — one account's positions (prior business day,
    end of day)."""
    return await _call(conn, org_id=org_id, environment=environment, resource="positions", param=account_id,
                       timeout=timeout, transport=transport)


async def call_cost_basis(
    conn,
    *,
    org_id: str,
    environment: str,
    account_id: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> list[dict]:
    """GET {api_base}/v2/accounts/{account_id}/cost-basis/ (every page) — one account's TAX LOTS (D4)."""
    return await _call(conn, org_id=org_id, environment=environment, resource="cost_basis", param=account_id,
                       timeout=timeout, transport=transport)


async def call_transactions(
    conn,
    *,
    org_id: str,
    environment: str,
    account_id: str,
    timeout: float = 15.0,
    transport: Any = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """GET {api_base}/v2/accounts/{account_id}/transactions?date_from=…&date_to=… (every page, page_size 500). Without
    a window the server's documented default applies (the past month) — the sync always sends one
    (``transaction_windows``). Dates are sent ISO ``YYYY-MM-DD``."""
    query = {}
    if date_from is not None:
        query["date_from"] = date_from.isoformat()
    if date_to is not None:
        query["date_to"] = date_to.isoformat()
    return await _call(conn, org_id=org_id, environment=environment, resource="transactions", param=account_id,
                       timeout=timeout, transport=transport, query=query or None)


def add_months(d: date, months: int) -> date:
    """``d`` moved by whole calendar months (the day clamped to the target month's length)."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def transaction_windows(start: date, end: date) -> list[tuple[date, date]]:
    """``[start, end]`` cut into consecutive windows of at most one calendar month each, oldest first, covering every
    day exactly once."""
    if start > end:
        return []
    out = []
    lo = start
    while lo <= end:
        hi = min(add_months(lo, 1) - timedelta(days=1), end)
        out.append((lo, hi))
        lo = hi + timedelta(days=1)
    return out


def minus_business_days(d: date, n: int) -> date:
    """``d`` moved back ``n`` US business days (services.us_business_days). Each step goes back at least one day:
    ``prior_business_day`` returns its argument when that is already a business day, so it must be given the day
    BEFORE (otherwise the loop never moves — found by verify_altread1)."""
    from services.us_business_days import prior_business_day

    out = d
    for _ in range(n):
        out = prior_business_day(out - timedelta(days=1))
    return out


def transactions_start(as_of: date, last_synced: date | None) -> date:
    """D1: the first sync looks back LOOKBACK_MONTHS; a later one starts at the last synced date minus
    OVERLAP_BUSINESS_DAYS business days (re-reading the overlap is harmless — rows are deduped by id)."""
    if last_synced is None:
        return add_months(as_of, -LOOKBACK_MONTHS)
    return minus_business_days(last_synced, OVERLAP_BUSINESS_DAYS)
