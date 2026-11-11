"""cpsim02 — the simulated Altruist-compatible AUTHORIZATION SERVER (contract §2, §6 items 1–2). ALTREAD1: on Altruist's
PUBLISHED paths and parameters (docs/open-api-authentication, read 2026-11-11).

  authorize   GET  …/api/oauth2/authorize?client_id&scope=openid&redirect_uri&state  (response_type optional; when sent
              it must be ``code``)
              → 302 to ``redirect_uri?code=<code>&state=<echoed>``. ``client_id`` is the PUBLIC id of the counterparty's
              live api_key credential (shown on the admin page, D3); an unknown client or a redirect that is not an
              absolute https URL → 400 and nothing is stored.
  token       POST …/oauth2/token (form body; client_secret_post — never HTTP Basic); ``token_type=id`` required
              grant_type=authorization_code: code + redirect_uri + client_id + client_secret
              grant_type=refresh_token:      refresh_token + client_id + client_secret (redirect_uri accepted)
              → ``{access_token, refresh_token, expires_in, scope, token_type: "Bearer"}``; ≥ 400 on any bad input and
              nothing is persisted. Codes are single-use and live CODE_TTL; refresh tokens ROTATE (the old one and the
              grant's earlier access tokens are closed).

The client secret is the counterparty's ``cpsim_live_…`` key (cpsim01's salted-hash credential). Codes and tokens are
``cpsim_code_`` / ``cpsim_at_`` / ``cpsim_rt_`` + 40 hex (160 bits) and are stored as sha256 only (+ the last four) —
an unsalted digest is the lookup key, which is safe for a 160-bit random value. Nothing here ever logs a value.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

from cpsim import generators as G

CODE_PREFIX = "cpsim_code_"
ACCESS_PREFIX = "cpsim_at_"
REFRESH_PREFIX = "cpsim_rt_"
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=28)          # ALTREAD1: the documented refresh-token lifetime
GRANTS = ("authorization_code", "refresh_token")
AUTHORIZE_PARAMS = ("client_id", "redirect_uri", "state", "scope")      # documented; response_type optional
TOKEN_TYPE = "id"
SCOPE_RX = re.compile(r"^[a-z:_ ]{1,200}$")
UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_now = None   # injectable (tests): zero-argument callable returning an aware datetime


def set_now(fn) -> None:
    global _now
    _now = fn


def now() -> datetime:
    return _now() if _now is not None else datetime.now(timezone.utc)


class OAuthRefused(Exception):
    """A refusal: ``status`` (≥ 400), the OAuth ``error`` code, and the request-log auth outcome."""

    def __init__(self, status: int, error: str, outcome: str, description: str):
        super().__init__(description)
        self.status, self.error, self.outcome, self.description = status, error, outcome, description


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new(prefix: str) -> tuple[str, str, str]:
    body = secrets.token_hex(20)
    value = prefix + body
    return value, digest(value), body[-4:]


def valid_redirect(uri: str | None) -> bool:
    if not uri or len(uri) > 500 or any(c.isspace() for c in uri) or "#" in uri:
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname)


async def _client(conn, cp: dict, client_id: str | None) -> dict | None:
    """The live api_key credential whose public id is ``client_id``."""
    if not client_id or not UUID_RX.match(client_id):
        return None
    r = await conn.fetchrow(
        "SELECT * FROM cpsim.counterparty_credentials WHERE id = $1::uuid AND counterparty_id = $2::uuid AND kind = "
        "'api_key' AND rotated_at IS NULL AND revoked_at IS NULL", client_id, str(cp["id"]))
    return dict(r) if r else None


async def authorize(conn, cp: dict, params: dict) -> str:
    """Validate the 5 parameters, store a code, return the Location for the 302."""
    if cp["status"] != "active":
        raise OAuthRefused(401, "unauthorized_client", "disabled", "this simulated counterparty is not active")
    missing = [p for p in AUTHORIZE_PARAMS if not (params.get(p) or "").strip()]
    if missing:
        raise OAuthRefused(400, "invalid_request", "bad_request", f"missing parameter(s): {', '.join(missing)}")
    if (params.get("response_type") or "code") != "code":
        raise OAuthRefused(400, "unsupported_response_type", "bad_request", "response_type must be code")
    cred = await _client(conn, cp, params["client_id"].strip())
    if cred is None:
        raise OAuthRefused(400, "invalid_client", "unknown_client", "unknown client_id")
    uri = params["redirect_uri"].strip()
    if not valid_redirect(uri):
        raise OAuthRefused(400, "invalid_request", "bad_redirect", "redirect_uri must be an absolute https URL")
    scope = params["scope"].strip()
    if not SCOPE_RX.match(scope):
        raise OAuthRefused(400, "invalid_scope", "bad_request", "scope is not valid")
    state = params["state"]
    if len(state) > 500:
        raise OAuthRefused(400, "invalid_request", "bad_request", "state is too long")
    code, code_hash, _ = _new(CODE_PREFIX)
    t = now()
    await conn.execute(
        "INSERT INTO cpsim.oauth_codes (counterparty_id, credential_id, code_hash, redirect_uri, scope, created_at, "
        "expires_at) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)", str(cp["id"]), str(cred["id"]), code_hash, uri,
        scope, t, t + CODE_TTL)
    sep = "&" if "?" in uri else "?"
    return f"{uri}{sep}{urlencode({'code': code, 'state': state})}"


def parse_form(raw: bytes) -> dict:
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    except UnicodeDecodeError:
        return {}
    return {k: v[0] for k, v in parsed.items() if v}


async def _authenticate_client(conn, cp: dict, form: dict) -> dict:
    """client_id + client_secret from the BODY. The secret must be a cpsim_ key matching THIS client id's credential."""
    from cpsim import registry as R

    if cp["status"] != "active":
        raise OAuthRefused(401, "invalid_client", "disabled", "this simulated counterparty is not active")
    secret = form.get("client_secret")
    if not secret:
        raise OAuthRefused(401, "invalid_client", "missing", "client_secret is required (in the body)")
    if not G.is_simulator_secret(secret):
        raise OAuthRefused(401, "invalid_client", "not_simulator_key",
                           "not a simulator key (simulator keys start with cpsim_)")
    cred = await _client(conn, cp, (form.get("client_id") or "").strip())
    if cred is None:
        outcome = await R.authenticate(conn, cp, secret)
        raise OAuthRefused(401, "invalid_client", "unknown_client" if outcome == "ok" else outcome, "unknown client")
    if not G.matches(cred["salt"], cred["key_hash"], secret):
        outcome = await R.authenticate(conn, cp, secret)   # 'revoked' for a rotated key, else unknown
        raise OAuthRefused(401, "invalid_client", outcome if outcome != "ok" else "unknown_key",
                           "client authentication failed")
    return cred


async def _issue(conn, cp: dict, cred: dict, grant_id: str, scope: str) -> dict:
    t = now()
    access, a_hash, a_last4 = _new(ACCESS_PREFIX)
    refresh, r_hash, r_last4 = _new(REFRESH_PREFIX)
    await conn.execute(
        "INSERT INTO cpsim.oauth_tokens (counterparty_id, credential_id, grant_id, kind, token_prefix, token_hash, "
        "last4, scope, created_at, expires_at) VALUES ($1::uuid, $2::uuid, $3::uuid, 'access', $4, $5, $6, $7, $8, $9)",
        str(cp["id"]), str(cred["id"]), grant_id, ACCESS_PREFIX, a_hash, a_last4, scope, t, t + ACCESS_TTL)
    rid = await conn.fetchval(
        "INSERT INTO cpsim.oauth_tokens (counterparty_id, credential_id, grant_id, kind, token_prefix, token_hash, "
        "last4, scope, created_at, expires_at) VALUES ($1::uuid, $2::uuid, $3::uuid, 'refresh', $4, $5, $6, $7, $8, "
        "$9) RETURNING id", str(cp["id"]), str(cred["id"]), grant_id, REFRESH_PREFIX, r_hash, r_last4, scope, t,
        t + REFRESH_TTL)
    return {"body": {"access_token": access, "refresh_token": refresh, "expires_in": int(ACCESS_TTL.total_seconds()),
                     "scope": scope, "token_type": "Bearer"}, "refresh_id": str(rid)}


async def token(conn, cp: dict, form: dict) -> dict:
    """The token endpoint. Every check runs BEFORE any write, so a refusal persists nothing."""
    grant = form.get("grant_type")
    if grant not in GRANTS:
        raise OAuthRefused(400, "unsupported_grant_type", "bad_grant", "grant_type must be authorization_code or "
                                                                       "refresh_token")
    if form.get("token_type") != TOKEN_TYPE:
        raise OAuthRefused(400, "invalid_request", "bad_request", "token_type must be id")
    cred = await _authenticate_client(conn, cp, form)
    t = now()
    if grant == "authorization_code":
        code = form.get("code") or ""
        row = await conn.fetchrow("SELECT * FROM cpsim.oauth_codes WHERE code_hash = $1 AND counterparty_id = $2::uuid",
                                  digest(code), str(cp["id"])) if code.startswith(CODE_PREFIX) else None
        if row is None or str(row["credential_id"]) != str(cred["id"]):
            raise OAuthRefused(400, "invalid_grant", "bad_grant", "unknown authorization code")
        if row["used_at"] is not None:
            raise OAuthRefused(400, "invalid_grant", "bad_grant", "authorization code already used")
        if row["expires_at"] <= t:
            raise OAuthRefused(400, "invalid_grant", "bad_grant", "authorization code expired")
        if not hmac.compare_digest(row["redirect_uri"], form.get("redirect_uri") or ""):
            raise OAuthRefused(400, "invalid_grant", "bad_redirect", "redirect_uri does not match the authorize request")
        claimed = await conn.fetchval("UPDATE cpsim.oauth_codes SET used_at = $2 WHERE id = $1::uuid AND used_at IS "
                                      "NULL RETURNING id", str(row["id"]), t)
        if claimed is None:
            raise OAuthRefused(400, "invalid_grant", "bad_grant", "authorization code already used")
        return (await _issue(conn, cp, cred, str(row["id"]), row["scope"]))["body"]
    # refresh_token
    rt = form.get("refresh_token") or ""
    row = await conn.fetchrow("SELECT * FROM cpsim.oauth_tokens WHERE token_hash = $1 AND counterparty_id = $2::uuid "
                              "AND kind = 'refresh'", digest(rt), str(cp["id"])) if rt.startswith(REFRESH_PREFIX) else None
    if row is None or str(row["credential_id"]) != str(cred["id"]):
        raise OAuthRefused(400, "invalid_grant", "token_unknown", "unknown refresh token")
    if row["rotated_at"] is not None or row["revoked_at"] is not None:
        raise OAuthRefused(400, "invalid_grant", "token_revoked", "refresh token already used or revoked")
    if row["expires_at"] <= t:
        raise OAuthRefused(400, "invalid_grant", "token_expired", "refresh token expired")
    claimed = await conn.fetchval("UPDATE cpsim.oauth_tokens SET rotated_at = $2 WHERE id = $1::uuid AND rotated_at IS "
                                  "NULL AND revoked_at IS NULL RETURNING id", str(row["id"]), t)
    if claimed is None:
        raise OAuthRefused(400, "invalid_grant", "token_revoked", "refresh token already used")
    await conn.execute("UPDATE cpsim.oauth_tokens SET revoked_at = $2 WHERE grant_id = $1::uuid AND kind = 'access' "
                       "AND revoked_at IS NULL", str(row["grant_id"]), t)
    issued = await _issue(conn, cp, cred, str(row["grant_id"]), row["scope"])
    await conn.execute("UPDATE cpsim.oauth_tokens SET replaced_by = $2::uuid WHERE id = $1::uuid", str(row["id"]),
                       issued["refresh_id"])
    return issued["body"]


async def check_access(conn, cp: dict, presented: str | None) -> str:
    """The resource-call auth outcome for a Bearer value: 'ok' or the refusal reason. The cpsim_ key itself is NOT an
    access token (it authenticates only the token endpoint)."""
    if presented is None:
        return "missing"
    if presented == "":
        return "malformed"
    if not G.is_simulator_secret(presented):
        return "not_simulator_key"
    if not presented.startswith(ACCESS_PREFIX):
        return "token_unknown"
    row = await conn.fetchrow("SELECT expires_at, revoked_at FROM cpsim.oauth_tokens WHERE token_hash = $1 AND "
                              "counterparty_id = $2::uuid AND kind = 'access'", digest(presented), str(cp["id"]))
    if row is None:
        return "token_unknown"
    if row["revoked_at"] is not None:
        return "token_revoked"
    if row["expires_at"] <= now():
        return "token_expired"
    if cp["status"] != "active":
        return "disabled"
    return "ok"


async def revoke_all(conn, counterparty_id) -> int:
    """Admin: close every live token of the counterparty (e.g. after rotating its key)."""
    r = await conn.execute("UPDATE cpsim.oauth_tokens SET revoked_at = $2 WHERE counterparty_id = $1::uuid AND "
                           "revoked_at IS NULL AND rotated_at IS NULL", str(counterparty_id), now())
    return int(r.split()[-1]) if r else 0
