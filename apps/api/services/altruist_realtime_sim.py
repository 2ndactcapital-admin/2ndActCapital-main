"""cpsim06 D4 — the client for the SIMULATED Realtime-API-shaped account opening (docs/cpsim/CPSIM06_NOTES.md §3 —
every path and field ASSUMED; the real Realtime API's paths are not published and Hollisworks has NO Realtime access).

It talks ONLY to the ``simulator`` environment. Asked for sandbox or production, it refuses before any request with
:data:`NO_REALTIME`. No real Realtime client exists.

Conventions (the cpsim02–05 clients'):
  * hosts resolved PER CALL for the org (``simulator_counterparty`` re-checks counterparty_sim.allowed + the configured
    counterparty every call) and ``check_destination`` (simulator credentials only to a simulator host, only cpsim_
    secrets). :func:`session` reads on the caller's connection, so the caller RELEASES it before any HTTP call;
  * the OAuth 2.0 PASSWORD grant with the simulator client (``ALTRUIST_SIMULATOR_CLIENT_ID`` / ``_CLIENT_SECRET``) on
    behalf of a simulator user (``ALTRUIST_SIMULATOR_REALTIME_USERNAME`` / ``_PASSWORD`` — the password must be a cpsim_
    value). A process-level token cache honours the published rules: an access token is reused until 5 minutes (less a
    skew) after issue; then the REFRESH token is used until its FIXED 10-hour window ends (refreshing never extends
    it); then a new password grant;
  * the ``transport`` is threaded on EVERY call through ``altruist_oauth._transport_for`` (tests drive the simulator
    in-process); 15 s timeout;
  * ``RateLimit-*`` headers are read on every answer; a 429 waits ``Retry-After`` seconds (injectable sleep) and retries
    ONCE; a second 429 → :class:`RealtimeRateLimited`;
  * HTTP ≥ 400 → :class:`RealtimeRefused` (status, the counterparty's detail and its problem list).

Never logs or prints a request body, a password, a token or a tax id. A tax id travels only inside a contact body.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from services.altruist_oauth import (
    SIMULATOR,
    SIMULATOR_SECRET_PREFIX,
    AltruistConfigError,
    AltruistOAuthError,
    _transport_for,
    check_destination,
    client_credentials,
    simulator_counterparty,
    simulator_hosts,
)

NO_REALTIME = "account opening needs Altruist Realtime API access (not granted)"
USERNAME_ENV = "ALTRUIST_SIMULATOR_REALTIME_USERNAME"
PASSWORD_ENV = "ALTRUIST_SIMULATOR_REALTIME_PASSWORD"
TOKEN_PATH = "/realtime/realms/altruist/protocol/openid-connect/token"
API_PATH = "/realtime/api"
TIMEOUT_S = 15.0
SKEW = timedelta(seconds=10)
MAX_RETRY_AFTER_S = 60


class RealtimeRefused(AltruistOAuthError):
    def __init__(self, message: str, *, status: int, detail: str | None = None, problems: list | None = None):
        super().__init__(message)
        self.status, self.detail, self.problems = status, detail, problems or []


class RealtimeRateLimited(RealtimeRefused):
    pass


# ── injectable clock / sleep (tests) ────────────────────────────────────────────────────────────────────────────────
_now = None
_sleep = None


def set_now(fn) -> None:
    global _now
    _now = fn


def now() -> datetime:
    return _now() if _now is not None else datetime.now(timezone.utc)


def set_sleep(fn) -> None:
    global _sleep
    _sleep = fn


async def _wait(seconds: float) -> None:
    if _sleep is not None:
        r = _sleep(seconds)
        if asyncio.iscoroutine(r):
            await r
        return
    await asyncio.sleep(seconds)


# ── the token cache: (token url, client id, username) → tokens ──────────────────────────────────────────────────────
_cache: dict[tuple, dict] = {}
#: what the last calls did (the verify reads it; never a token value)
events: list[dict] = []


def clear_cache() -> None:
    _cache.clear()
    events.clear()


def user_credentials() -> tuple[str, str]:
    username = (os.environ.get(USERNAME_ENV) or "").strip()
    password = os.environ.get(PASSWORD_ENV) or ""
    if not username or not password:
        raise AltruistConfigError(f"{USERNAME_ENV} / {PASSWORD_ENV} are not configured (a simulator user created on "
                                  "Admin → Counterparties → Realtime)")
    if not password.startswith(SIMULATOR_SECRET_PREFIX):
        raise AltruistConfigError(f"{PASSWORD_ENV} must be a simulator password (cpsim_ prefix) — refusing to send it")
    return username, password


async def session(conn, *, org_id: str, environment: str = SIMULATOR) -> dict:
    """What a Realtime call needs, read on ``conn`` (the caller releases it before HTTP). Refuses sandbox /
    production (no Realtime access), an org without the simulator flag or counterparty, and any host that is not a
    simulator host."""
    if environment != SIMULATOR:
        raise AltruistOAuthError(NO_REALTIME)
    slug = await simulator_counterparty(conn, org_id=str(org_id))
    hosts = simulator_hosts(slug)
    client_id, client_secret = client_credentials(SIMULATOR)
    check_destination(SIMULATOR, hosts, client_secret)
    root = hosts["api_base"]
    return {"environment": SIMULATOR, "slug": slug, "token_url": root + TOKEN_PATH, "api_base": root + API_PATH,
            "client_id": client_id}


def _headers(access: str | None = None, *, form: bool = False, body: bool = False) -> dict:
    h = {"Accept": "application/json"}
    if access:
        h["Authorization"] = f"Bearer {access}"
    if form:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body:
        h["Content-Type"] = "application/json"
    return h


def rate_limit(response) -> dict:
    return {k: response.headers.get(k) for k in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset",
                                                  "Retry-After") if response.headers.get(k) is not None}


async def _post_form(url: str, form: dict, *, environment: str, transport: Any) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=_transport_for(environment, transport)) as client:
        try:
            return await client.post(url, content=urlencode(form), headers=_headers(form=True))
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(f"POST token failed at the transport layer: {type(exc).__name__}") from exc


def _client_creds(sess: dict) -> tuple[str, str]:
    """The session's own in-memory client credentials (the conformance fixture's), else Doppler's."""
    if sess.get("client_secret"):
        if not str(sess["client_secret"]).startswith(SIMULATOR_SECRET_PREFIX):
            raise AltruistConfigError("refusing to send a simulator client secret that lacks the cpsim_ prefix")
        return sess["client_id"], sess["client_secret"]
    return client_credentials(SIMULATOR)


def _user(sess: dict) -> tuple[str, str]:
    if sess.get("username") and sess.get("password"):
        if not str(sess["password"]).startswith(SIMULATOR_SECRET_PREFIX):
            raise AltruistConfigError("refusing to send a simulator password that lacks the cpsim_ prefix")
        return sess["username"], sess["password"]
    return user_credentials()


async def _grant(sess: dict, form: dict, transport: Any) -> dict:
    client_id, client_secret = _client_creds(sess)
    r = await _post_form(sess["token_url"], {**form, "client_id": client_id, "client_secret": client_secret},
                         environment=sess["environment"], transport=transport)
    if r.status_code >= 400:
        try:
            d = r.json()
        except ValueError:
            d = {}
        raise RealtimeRefused(f"the token request was refused: HTTP {r.status_code}", status=r.status_code,
                              detail=str(d.get("error_description") or d.get("error") or "")[:300])
    d = r.json()
    t = now()
    return {"access": d["access_token"], "access_exp": t + timedelta(seconds=int(d["expires_in"])),
            "refresh": d.get("refresh_token"),
            "refresh_exp": t + timedelta(seconds=int(d.get("refresh_expires_in") or 0))}


async def access_token(sess: dict, *, transport: Any = None, force_new: bool = False) -> str:
    """A live access token for ``sess``: cached → refresh (inside the fixed window) → password grant."""
    username, password = _user(sess)
    key = (sess["token_url"], sess["client_id"], username)
    c = _cache.get(key)
    t = now()
    if c and not force_new and c["access_exp"] - SKEW > t:
        events.append({"step": "cached"})
        return c["access"]
    if c and c.get("refresh") and c["refresh_exp"] - SKEW > t:
        try:
            nc = await _grant(sess, {"grant_type": "refresh_token", "refresh_token": c["refresh"]}, transport)
            _cache[key] = nc
            events.append({"step": "refresh"})
            return nc["access"]
        except RealtimeRefused:
            events.append({"step": "refresh_refused"})
    nc = await _grant(sess, {"grant_type": "password", "username": username, "password": password}, transport)
    _cache[key] = nc
    events.append({"step": "password"})
    return nc["access"]


async def call(sess: dict, method: str, path: str, *, json_body: dict | None = None, transport: Any = None) -> dict:
    """ONE Realtime call → ``{status, body, rate_limit}``. 429 → wait Retry-After, retry once. 401 on a cached token →
    a fresh token, retry once. ≥ 400 otherwise → RealtimeRefused (a 422 carries the problem list)."""
    url = sess["api_base"] + path
    retried_429 = retried_401 = False
    force_new = False
    while True:
        token = await access_token(sess, transport=transport, force_new=force_new)
        async with httpx.AsyncClient(timeout=TIMEOUT_S,
                                     transport=_transport_for(sess["environment"], transport)) as client:
            try:
                r = await client.request(method, url, headers=_headers(token, body=json_body is not None),
                                         json=json_body)
            except httpx.HTTPError as exc:
                raise AltruistOAuthError(f"{method} {path} failed at the transport layer: "
                                         f"{type(exc).__name__}") from exc
        rl = rate_limit(r)
        if r.status_code == 429:
            events.append({"step": "429", "retry_after": rl.get("Retry-After")})
            if retried_429:
                raise RealtimeRateLimited(f"{method} {path}: rate limited twice", status=429,
                                          detail="rate limit exceeded")
            retried_429 = True
            try:
                wait = min(max(float(rl.get("Retry-After") or 1), 0.0), MAX_RETRY_AFTER_S)
            except ValueError:
                wait = 1.0
            await _wait(wait)
            continue
        if r.status_code == 401 and not retried_401:
            retried_401, force_new = True, True
            events.append({"step": "401_retry"})
            continue
        try:
            body = r.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        if r.status_code >= 400 and r.status_code != 422:
            raise RealtimeRefused(f"{method} {path} was refused: HTTP {r.status_code}", status=r.status_code,
                                  detail=str(body.get("detail") or "")[:500], problems=body.get("problems"))
        return {"status": r.status_code, "body": body, "rate_limit": rl}


# ── the resources (every path ASSUMED) ──────────────────────────────────────────────────────────────────────────────
async def household(sess, name: str, *, transport: Any = None) -> dict:
    r = await call(sess, "POST", "/households", json_body={"name": name}, transport=transport)
    _raise_422(r, "households")
    return r["body"]["household"]


async def contact(sess, body: dict, *, transport: Any = None) -> dict:
    r = await call(sess, "POST", "/contacts", json_body=body, transport=transport)
    _raise_422(r, "contacts")
    return r["body"]["contact"]


async def draft(sess, body: dict, *, transport: Any = None) -> dict:
    r = await call(sess, "POST", "/account-drafts", json_body=body, transport=transport)
    _raise_422(r, "account-drafts")
    return r["body"]["draft"]


async def validate(sess, draft_id: str, *, transport: Any = None) -> dict:
    r = await call(sess, "POST", f"/account-drafts/{draft_id}/validate", transport=transport)
    _raise_422(r, "validate")
    return r["body"]


async def submit(sess, draft_id: str, *, transport: Any = None) -> dict:
    """→ ``{status, opened: bool, account, draft, problems}``. 201 opened, 200 the same account (repeat), 422 refused."""
    r = await call(sess, "POST", f"/account-drafts/{draft_id}/submit", transport=transport)
    b = r["body"]
    return {"status": r["status"], "opened": r["status"] in (200, 201) and bool(b.get("account")),
            "account": b.get("account"), "draft": b.get("draft"), "problems": b.get("problems") or [],
            "rate_limit": r["rate_limit"]}


def _raise_422(r: dict, what: str) -> None:
    if r["status"] == 422:
        b = r["body"]
        raise RealtimeRefused(f"{what} was refused: HTTP 422", status=422, detail=str(b.get("detail") or "")[:500],
                              problems=b.get("problems"))
