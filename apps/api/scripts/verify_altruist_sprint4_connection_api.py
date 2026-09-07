"""Altruist Sprint 4 verification — connection lifecycle wired into real HTTP
endpoints (``routers/altruist_connection.py``) + automatic token refresh.

Pass/fail only, no prompts. Run:

    doppler run -- python3 scripts/verify_altruist_sprint4_connection_api.py

Every table this script writes to is counted before the first insert and
again after the last delete; a difference of even one row fails the run.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Everything drives the real ASGI app** (``starlette.testclient.TestClient``
  against ``main.app``), never the service functions directly — this sprint's
  whole point is proving the HTTP surface, not re-proving Sprint 1's function-
  level guarantees. Only ``main.verify_token`` is stubbed (token *signature*
  validation) — the RLS-context middleware, the active-account gate, and
  ``rbac.require_permission`` all run for real. One shared ``TestClient``,
  entered as a context manager for the whole pass (``portfolio_ux3``'s own
  documented reason: a fresh client per call gets a fresh event loop, and the
  app's connection pool is a module global bound to whichever loop created it
  — request 2 onward would fail with "Event loop is closed").

* **The zero-roles-default-allow trap (found in UX3, fixed in UX4) is not
  repeated here.** A fixture with NO ``user_roles`` row would silently hold
  every permission (single-admin bootstrap posture) and every refusal
  assertion below would pass for the wrong reason. ``NOPERMS`` is given a
  REAL role (``member``) that genuinely does not carry the two new
  permissions; ``ADMIN``/``ORGB`` are given the REAL, already-deployed
  ``admin`` role, which this sprint grants the two new permissions to
  (data-only catalog addition, no DDL — see ``services/altruist_oauth.py``
  and PROJECT_STATUS.md).

* **The exchange_code_for_tokens monkeypatch targets the MODULE, not the
  router's imported name.** ``routers/altruist_connection.py`` calls
  ``altruist_oauth.exchange_code_for_tokens(...)`` via a qualified module
  reference specifically so ``services.altruist_oauth.exchange_code_for_tokens
  = fake`` (this script) actually reaches the router's call site — importing
  the bare name into the router would have bound it at import time and made
  this exact monkeypatch a silent no-op.

* **Refusal is proven with a paired control.** Every 403 assertion below is
  matched with the identical request succeeding once the caller's permission
  changes — a refusal-only test cannot distinguish "the gate works" from
  "the endpoint is broken for everyone".

* **Cross-org isolation is proven by an independent re-read**, not by
  trusting the acting caller's own response: after ORG B's caller calls
  disconnect on their own org, ORG A's connection row is re-read directly
  from Postgres (not through the API) and confirmed untouched.

* **The refresh proof is a real re-read, not "the call didn't error"**: a
  connection is fixtured with a token expiring in under the refresh
  threshold; hitting the status endpoint (which calls ``ensure_fresh_token``)
  with ``refresh_access_token`` monkeypatched to a synthetic response, then a
  brand-new independent connection re-reads ``token_expires_at``/
  ``last_refreshed_at`` and confirms both advanced.

* Teardown is by fixture id (or fixture ``state`` value, for oauth_states
  rows the API itself creates with a server-generated id), in FK-safe order,
  never a TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from uuid import NAMESPACE_URL, uuid5

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"
TAG = "altr4verify"

# No real values exist in Doppler for any of these (re-confirmed live in Task
# 3 below) — synthetic, session-local values are sufficient to exercise the
# app-level plumbing (encryption, config lookup, URL building) without a live
# Altruist call, exactly like Sprint 1's encryption-key stub.
import base64  # noqa: E402
import os  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("ALTRUIST_REDIRECT_URI", "https://app.example.test/altruist/callback")
os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_ID", f"{TAG}-sandbox-client-id")
os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_SECRET", f"{TAG}-sandbox-client-secret")
os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_ID", f"{TAG}-production-client-id")
os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_SECRET", f"{TAG}-production-client-secret")

# ── fixture principals ──────────────────────────────────────────────────────
ADMIN_SUB = f"{TAG}|admin"          # org=ORG,       RBAC role 'admin'  → BOTH permissions
NOPERMS_SUB = f"{TAG}|noperm"       # org=ORG,       RBAC role 'member' → neither permission
ORGB_SUB = f"{TAG}|orgb"            # org=OTHER_ORG, RBAC role 'admin'  → BOTH, but wrong org

ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
NOPERMS_USER_ID = str(uuid5(NAMESPACE_URL, NOPERMS_SUB))
ORGB_USER_ID = str(uuid5(NAMESPACE_URL, ORGB_SUB))
USERS = [ADMIN_USER_ID, NOPERMS_USER_ID, ORGB_USER_ID]

# ── fixture connections (pre-seeded directly via SQL, like Sprint 1) ───────
CONN_ORG_SANDBOX = "99000000-0000-0000-0000-0000a17f4001"        # ORG, sandbox — status/perm/isolation reads
CONN_OTHERORG_SANDBOX = "99000000-0000-0000-0000-0000a17f4002"   # OTHER_ORG, sandbox — isolation counterpart
CONN_ORG_PRODUCTION_EXPIRING = "99000000-0000-0000-0000-0000a17f4003"  # ORG, production — refresh proof
FIXTURE_CONNECTIONS = [CONN_ORG_SANDBOX, CONN_OTHERORG_SANDBOX, CONN_ORG_PRODUCTION_EXPIRING]
# Populated at runtime with any connection id the API itself creates
# (the round-trip test) so teardown catches it too.
RUNTIME_CONNECTIONS: list[str] = []

ACCESS_ORG = f"{TAG}-access-org-9c1"
REFRESH_ORG = f"{TAG}-refresh-org-9c1"
CLIENT_SECRET_ORG = f"{TAG}-clientsecret-org-9c1"
ACCESS_OTHERORG = f"{TAG}-access-otherorg-2b4"
REFRESH_OTHERORG = f"{TAG}-refresh-otherorg-2b4"
ACCESS_EXPIRING = f"{TAG}-access-expiring-7e0"
REFRESH_EXPIRING = f"{TAG}-refresh-expiring-7e0"
RAW_SECRETS = [
    ACCESS_ORG, REFRESH_ORG, CLIENT_SECRET_ORG,
    ACCESS_OTHERORG, REFRESH_OTHERORG,
    ACCESS_EXPIRING, REFRESH_EXPIRING,
]

# ── fixture oauth_states (pre-seeded, deterministic ids) ────────────────────
ST_EXPIRED_ID = "99000000-0000-0000-0000-0000a17f4011"
ST_USED_ID = "99000000-0000-0000-0000-0000a17f4012"
ST_XORG_ID = "99000000-0000-0000-0000-0000a17f4013"
FIXTURE_STATE_IDS = [ST_EXPIRED_ID, ST_USED_ID, ST_XORG_ID]

TOK_EXPIRED = f"{TAG}_expired"
TOK_USED = f"{TAG}_used"
TOK_XORG = f"{TAG}_xorg"
TOK_MISSING = f"{TAG}_never_inserted"

# States the API itself creates during the run (connect() responses) — no
# fixed id, tracked by their `state` string value for teardown.
RUNTIME_STATE_VALUES: list[str] = []

COUNTED = (
    "public.altruist_connections",
    "public.altruist_oauth_states",
    "public.users",
    "public.user_roles",
)


# ═══════════════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════════════
class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def ok(self, ref, msg):
        self.rows.append(("PASS", ref, msg))
        print(f"[PASS] {ref}  {msg}")

    def bad(self, ref, msg, detail=""):
        self.rows.append(("FAIL", ref, f"{msg} — {detail}" if detail else msg))
        print(f"[FAIL] {ref}  {msg}" + (f"\n         {detail}" if detail else ""))

    def find(self, ref, msg):
        self.rows.append(("FIND", ref, msg))
        print(f"[FIND] {ref}  {msg}")

    def blocked(self, ref, msg):
        self.rows.append(("BLOCKED", ref, msg))
        print(f"[BLOCKED] {ref}  {msg}")

    def expect(self, ref, condition, msg, detail=""):
        if condition:
            self.ok(ref, msg)
        else:
            self.bad(ref, msg, detail)
        return bool(condition)

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def summary(self):
        counts: dict[str, int] = {}
        for kind, _, _ in self.rows:
            counts[kind] = counts.get(kind, 0) + 1
        total = len(self.rows)
        print("\n" + "=" * 78)
        print(f"altruist_sprint4_connection_api: {counts.get('PASS', 0)}/{total} PASS"
              + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn) -> None:
    conn_ids = FIXTURE_CONNECTIONS + RUNTIME_CONNECTIONS
    if conn_ids:
        await conn.execute(
            "DELETE FROM public.altruist_connections WHERE id = ANY($1::uuid[])", conn_ids)
    state_ids = FIXTURE_STATE_IDS
    if state_ids:
        await conn.execute(
            "DELETE FROM public.altruist_oauth_states WHERE id = ANY($1::uuid[])", state_ids)
    state_values = RUNTIME_STATE_VALUES
    if state_values:
        await conn.execute(
            "DELETE FROM public.altruist_oauth_states WHERE state = ANY($1::text[])", state_values)
    await conn.execute(
        "DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", USERS)
    await conn.execute(
        "DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def seed_users(conn) -> None:
    """Three principals. Each gets a REAL role grant — never zero roles,
    which would default-allow (the UX3 trap; see module docstring).
    """
    for user_id, org, sub, role_name in (
        (ADMIN_USER_ID, ORG, ADMIN_SUB, "admin"),
        (NOPERMS_USER_ID, ORG, NOPERMS_SUB, "member"),
        (ORGB_USER_ID, OTHER_ORG, ORGB_SUB, "admin"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify Altruist Sprint4', $4, 'member', true)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, f"{sub.split('|')[-1]}@test.local", sub,
        )
        await conn.execute(
            """
            INSERT INTO public.user_roles (user_id, role_id)
            SELECT $1::uuid, r.id FROM public.roles r WHERE r.name = $2
            ON CONFLICT DO NOTHING
            """,
            user_id, role_name,
        )


async def seed_connections(conn) -> None:
    from services.altruist_oauth import encrypt_secret, last4

    now = datetime.now(timezone.utc)

    for cid, org, environment, client_id, access_tok, refresh_tok, expires_at in (
        (CONN_ORG_SANDBOX, ORG, "sandbox", f"{TAG}-client-org",
         ACCESS_ORG, REFRESH_ORG, now + timedelta(hours=1)),
        (CONN_OTHERORG_SANDBOX, OTHER_ORG, "sandbox", f"{TAG}-client-otherorg",
         ACCESS_OTHERORG, REFRESH_OTHERORG, now + timedelta(hours=1)),
        (CONN_ORG_PRODUCTION_EXPIRING, ORG, "production", f"{TAG}-client-expiring",
         ACCESS_EXPIRING, REFRESH_EXPIRING, now + timedelta(minutes=2)),
    ):
        await conn.execute(
            """
            INSERT INTO public.altruist_connections (
                id, org_id, environment, status, client_id,
                client_secret_encrypted, client_secret_last4,
                access_token_encrypted, access_token_last4,
                refresh_token_encrypted, refresh_token_last4,
                scope, token_expires_at, last_refreshed_at, connected_by
            ) VALUES (
                $1::uuid, $2::uuid, $3, 'connected', $4,
                $5, $6, $7, $8, $9, $10,
                'accounts:read', $11, $12, NULL
            )
            """,
            cid, org, environment, client_id,
            encrypt_secret(CLIENT_SECRET_ORG), last4(CLIENT_SECRET_ORG),
            encrypt_secret(access_tok), last4(access_tok),
            encrypt_secret(refresh_tok), last4(refresh_tok),
            expires_at, now,
        )


async def seed_states(conn) -> None:
    now = datetime.now(timezone.utc)

    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4)""",
        ST_EXPIRED_ID, ORG, TOK_EXPIRED, now - timedelta(minutes=1))

    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at, used_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4, $5)""",
        ST_USED_ID, ORG, TOK_USED, now + timedelta(minutes=10), now - timedelta(minutes=1))

    await conn.execute(
        """INSERT INTO public.altruist_oauth_states
             (id, org_id, environment, state, initiated_by, expires_at)
           VALUES ($1::uuid, $2::uuid, 'sandbox', $3, NULL, $4)""",
        ST_XORG_ID, OTHER_ORG, TOK_XORG, now + timedelta(minutes=10))


# ═══════════════════════════════════════════════════════════════════════════
# Task 3 — sandbox smoke test: confirm no credentials, do not attempt a call
# ═══════════════════════════════════════════════════════════════════════════
def check_task3_credentials() -> None:
    try:
        out = subprocess.run(
            ["doppler", "secrets", "--only-names"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        R.blocked("T3", f"could not run `doppler secrets --only-names`: {exc!r} — "
                         "sandbox smoke test cannot be attempted or refuted")
        return
    if out.returncode != 0:
        R.blocked("T3", f"`doppler secrets --only-names` exited {out.returncode}: "
                         f"{out.stderr.strip()} — sandbox smoke test cannot be attempted")
        return
    altruist_names = [ln.strip() for ln in out.stdout.splitlines() if "ALTRUIST" in ln.upper()]
    if altruist_names:
        R.find("T3", f"ALTRUIST-named secrets DO exist in Doppler: {altruist_names} — "
                      "this contradicts Sprints 1-3's finding; re-confirmed live, not assumed")
        return
    R.blocked("T3", "no ALTRUIST_* secrets exist in this project's Doppler config "
                     "(`doppler secrets --only-names`, project hollisworks), re-confirmed "
                     "live as Sprints 1-3 found — sandbox smoke test genuinely blocked; "
                     "no live call attempted")


# ═══════════════════════════════════════════════════════════════════════════
# HTTP harness — one shared TestClient, identity switched per call
# ═══════════════════════════════════════════════════════════════════════════
class _Principal:
    """Drives the real ASGI app as one specific user.

    See ``verify_portfolioux3.py``'s own ``_Principal`` for why every
    principal must share ONE ``TestClient`` entered as a context manager —
    the app's connection pool is a module global bound to whichever event
    loop created it, and a fresh ``TestClient`` per call gets a fresh loop.
    """

    __slots__ = ("client", "org_id", "sub")

    def __init__(self, client, org_id: str, sub: str):
        self.client = client
        self.org_id = org_id
        self.sub = sub

    def _become(self) -> None:
        import main

        sub, org_id = self.sub, self.org_id
        main.verify_token = lambda _token: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }

    def get(self, url, **kw):
        self._become()
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        self._become()
        return self.client.post(url, **kw)


HEADERS = {"Authorization": "Bearer verify-token"}


def _no_raw_tokens(body: dict) -> bool:
    forbidden_keys = {"access_token", "refresh_token", "client_secret",
                       "access_token_encrypted", "refresh_token_encrypted",
                       "client_secret_encrypted"}
    if forbidden_keys & set(body.keys()):
        return False
    serialized = json.dumps(body)
    return not any(secret in serialized for secret in RAW_SECRETS)


def run_http_checks(client) -> None:
    admin = _Principal(client, ORG, ADMIN_SUB)
    noperms = _Principal(client, ORG, NOPERMS_SUB)
    orgb = _Principal(client, OTHER_ORG, ORGB_SUB)

    # ── [Y] 4-1: connection-status body never carries a raw token value ────
    r = admin.get("/api/v1/altruist/status", params={"environment": "sandbox"}, headers=HEADERS)
    body = r.json() if r.status_code == 200 else {}
    R.expect("4-1a", r.status_code == 200, "status(sandbox) as ADMIN returns 200",
             detail=f"{r.status_code} {r.text}")
    R.expect("4-1b", body.get("connected") is True and body.get("environment") == "sandbox",
             "status reports the pre-seeded ORG/sandbox connection as connected",
             detail=str(body))
    R.expect("4-1c", _no_raw_tokens(body),
             "the response body contains NO raw token/secret value in any field, "
             "checked by parsing the actual JSON, not by eye",
             detail=str(body))

    # ── [Y] 4-2: permission refusal + control, connect + disconnect ────────
    r_noperm_connect = noperms.post(
        "/api/v1/altruist/connect", json={"environment": "sandbox"}, headers=HEADERS)
    R.expect("4-2a", r_noperm_connect.status_code == 403,
             "a caller WITHOUT manage_custody_connections is refused on /altruist/connect",
             detail=f"{r_noperm_connect.status_code} {r_noperm_connect.text}")

    r_noperm_disconnect = noperms.post(
        "/api/v1/altruist/disconnect", json={"environment": "sandbox"}, headers=HEADERS)
    R.expect("4-2b", r_noperm_disconnect.status_code == 403,
             "the SAME caller is refused on /altruist/disconnect",
             detail=f"{r_noperm_disconnect.status_code} {r_noperm_disconnect.text}")

    still_connected = admin.get(
        "/api/v1/altruist/status", params={"environment": "sandbox"}, headers=HEADERS).json()
    R.expect("4-2c", still_connected.get("connected") is True,
             "the refused disconnect attempt had NO effect — the connection is still "
             "connected, on an independent status read",
             detail=str(still_connected))

    r_admin_disconnect = admin.post(
        "/api/v1/altruist/disconnect", json={"environment": "sandbox"}, headers=HEADERS)
    R.expect("4-2d", r_admin_disconnect.status_code == 200
              and r_admin_disconnect.json().get("disconnected") is True,
              "the IDENTICAL request succeeds once the caller holds "
              "manage_custody_connections — only the caller changed",
              detail=f"{r_admin_disconnect.status_code} {r_admin_disconnect.text}")

    now_disconnected = admin.get(
        "/api/v1/altruist/status", params={"environment": "sandbox"}, headers=HEADERS).json()
    R.expect("4-2e", now_disconnected.get("connected") is False,
             "status now reports disconnected after the successful disconnect",
             detail=str(now_disconnected))

    # ── [Y] 4-3: cross-org isolation — status AND disconnect ────────────────
    orgb_status = orgb.get(
        "/api/v1/altruist/status", params={"environment": "sandbox"}, headers=HEADERS).json()
    R.expect("4-3a", orgb_status.get("connected") is True
              and orgb_status.get("client_id") == f"{TAG}-client-otherorg",
              "org B's caller sees ITS OWN connection (distinct client_id marker), "
              "not org A's — same request shape, different caller org",
              detail=str(orgb_status))

    r_orgb_disconnect = orgb.post(
        "/api/v1/altruist/disconnect", json={"environment": "sandbox"}, headers=HEADERS)
    R.expect("4-3b", r_orgb_disconnect.status_code == 200
              and r_orgb_disconnect.json().get("disconnected") is True,
              "org B's caller CAN disconnect — proving the isolation below is about "
              "ORG SCOPE, not merely 'this caller can never disconnect anything'",
              detail=f"{r_orgb_disconnect.status_code} {r_orgb_disconnect.text}")


def run_round_trip(client) -> None:
    """[Y] 4-4: initiate connect -> synthetic callback -> status connected ->
    disconnect -> status disconnected, all through real HTTP. By this point
    ORG/sandbox has no active row (closed in 4-2), so this exercises a clean
    reconnect in the same environment slot.
    """
    import services.altruist_oauth as altruist_oauth
    from services.altruist_oauth import TokenResponse

    admin = _Principal(client, ORG, ADMIN_SUB)

    new_access = f"{TAG}-roundtrip-access-4f2"
    new_refresh = f"{TAG}-roundtrip-refresh-4f2"
    RAW_SECRETS.extend([new_access, new_refresh])

    async def fake_exchange(*, environment: str, code: str, timeout: float = 15.0):
        assert environment == "sandbox"
        assert code == "synthetic-auth-code"
        return TokenResponse(access_token=new_access, refresh_token=new_refresh,
                              expires_in=3600, scope="accounts:read")

    original_exchange = altruist_oauth.exchange_code_for_tokens
    altruist_oauth.exchange_code_for_tokens = fake_exchange
    try:
        r_connect = admin.post(
            "/api/v1/altruist/connect", json={"environment": "sandbox"}, headers=HEADERS)
        R.expect("4-4a", r_connect.status_code == 200 and "authorize_url" in r_connect.json(),
                 "POST /altruist/connect returns an authorize_url",
                 detail=f"{r_connect.status_code} {r_connect.text}")

        authorize_url = r_connect.json().get("authorize_url", "")
        state_value = parse_qs(urlparse(authorize_url).query).get("state", [None])[0]
        R.expect("4-4b", bool(state_value), "the authorize_url carries a real state parameter",
                 detail=authorize_url)
        if state_value:
            RUNTIME_STATE_VALUES.append(state_value)

        r_callback = admin.get(
            "/api/v1/altruist/callback",
            params={"code": "synthetic-auth-code", "state": state_value},
            headers=HEADERS,
        )
        R.expect("4-4c", r_callback.status_code == 200 and r_callback.json().get("connected") is True,
                 "GET /altruist/callback with a valid state + monkeypatched token exchange "
                 "succeeds", detail=f"{r_callback.status_code} {r_callback.text}")
        new_conn_id = r_callback.json().get("connection_id")
        if new_conn_id:
            RUNTIME_CONNECTIONS.append(new_conn_id)

        status_after_connect = admin.get(
            "/api/v1/altruist/status", params={"environment": "sandbox"}, headers=HEADERS).json()
        R.expect("4-4d", status_after_connect.get("connected") is True,
                 "status reports connected immediately after the callback",
                 detail=str(status_after_connect))

        r_disconnect = admin.post(
            "/api/v1/altruist/disconnect", json={"environment": "sandbox"}, headers=HEADERS)
        R.expect("4-4e", r_disconnect.status_code == 200 and r_disconnect.json().get("disconnected") is True,
                 "disconnect succeeds on the freshly-created connection",
                 detail=f"{r_disconnect.status_code} {r_disconnect.text}")

        status_after_disconnect = admin.get(
            "/api/v1/altruist/status", params={"environment": "sandbox"}, headers=HEADERS).json()
        R.expect("4-4f", status_after_disconnect.get("connected") is False,
                 "status reports disconnected after the full round trip",
                 detail=str(status_after_disconnect))
    finally:
        altruist_oauth.exchange_code_for_tokens = original_exchange


def run_state_rejection_cases(client) -> None:
    """[Y] 4-5: missing/expired/used/cross-org state, all through the REAL
    HTTP callback route, same request shape, differing only in `state`.
    """
    admin = _Principal(client, ORG, ADMIN_SUB)
    statuses: dict[str, int] = {}

    for label, token in (
        ("missing", TOK_MISSING),
        ("expired", TOK_EXPIRED),
        ("used", TOK_USED),
        ("xorg", TOK_XORG),
    ):
        r = admin.get(
            "/api/v1/altruist/callback",
            params={"code": "irrelevant-code", "state": token},
            headers=HEADERS,
        )
        statuses[label] = r.status_code
        R.expect(f"4-5-{label}", r.status_code == 400,
                 f"the real HTTP callback route rejects a {label} state (400, not 200)",
                 detail=f"{r.status_code} {r.text}")

    R.expect("4-5-uniform", len(set(statuses.values())) == 1,
              "all four rejection cases produce the identical HTTP status — a CSRF "
              "check must not leak which reason it failed for", detail=str(statuses))


async def run_refresh_proof(client, admin_url: str) -> None:
    """[Y] 4-6: a connection expiring in under the refresh threshold gets
    refreshed by hitting the status endpoint, confirmed by an INDEPENDENT
    re-read — not just "the call didn't error".
    """
    import services.altruist_oauth as altruist_oauth
    from services.altruist_oauth import TokenResponse

    admin = _Principal(client, ORG, ADMIN_SUB)

    independent_before = await connect(admin_url)
    try:
        before = await independent_before.fetchrow(
            """SELECT token_expires_at, last_refreshed_at, access_token_last4
               FROM public.altruist_connections WHERE id = $1::uuid""",
            CONN_ORG_PRODUCTION_EXPIRING)
    finally:
        await independent_before.close()

    new_access = f"{TAG}-refreshed-access-8d1"
    new_refresh = f"{TAG}-refreshed-refresh-8d1"
    RAW_SECRETS.extend([new_access, new_refresh])

    refresh_calls: list[str] = []

    async def fake_refresh(*, environment: str, refresh_token: str, timeout: float = 15.0):
        refresh_calls.append(refresh_token)
        return TokenResponse(access_token=new_access, refresh_token=new_refresh,
                              expires_in=3600, scope="accounts:read")

    original_refresh = altruist_oauth.refresh_access_token
    altruist_oauth.refresh_access_token = fake_refresh
    try:
        r = admin.get(
            "/api/v1/altruist/status", params={"environment": "production"}, headers=HEADERS)
        R.expect("4-6a", r.status_code == 200,
                 "status(production) on the near-expiry fixture returns 200",
                 detail=f"{r.status_code} {r.text}")
        R.expect("4-6b", len(refresh_calls) == 1 and refresh_calls[0] == REFRESH_EXPIRING,
                 "refresh_access_token was called exactly once, with the connection's "
                 "OWN decrypted refresh token — proving ensure_fresh_token actually ran, "
                 "not merely that the call succeeded", detail=str(refresh_calls))
    finally:
        altruist_oauth.refresh_access_token = original_refresh

    independent_after = await connect(admin_url)
    try:
        after = await independent_after.fetchrow(
            """SELECT token_expires_at, last_refreshed_at, access_token_last4
               FROM public.altruist_connections WHERE id = $1::uuid""",
            CONN_ORG_PRODUCTION_EXPIRING)
    finally:
        await independent_after.close()

    from services.altruist_oauth import last4
    R.expect("4-6c", after is not None and before is not None
              and after["token_expires_at"] > before["token_expires_at"]
              and after["last_refreshed_at"] > before["last_refreshed_at"],
              "an INDEPENDENT re-read (a brand new connection) confirms "
              "token_expires_at and last_refreshed_at both advanced",
              detail=f"before={dict(before) if before else None} "
                     f"after={dict(after) if after else None}")
    R.expect("4-6d", after is not None and after["access_token_last4"] == last4(new_access),
              "the independent re-read shows the NEW access token's last4 marker",
              detail=str(dict(after) if after else None))


# ═══════════════════════════════════════════════════════════════════════════
async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    if admin_url is None:
        print(f"FATAL: cannot reach the database as postgres — {admin_prov}")
        return 2
    if app_url is None:
        print(f"FATAL: cannot reach the database as app_service — {app_prov}")
        return 2
    print(f"admin       : {admin_prov}")
    print(f"app_service : {app_prov}\n")

    check_task3_credentials()

    admin = await connect(admin_url)

    pre: dict[str, int] = {}
    try:
        await teardown(admin)
        pre = await counts(admin)
        await seed_users(admin)
        await seed_connections(admin)
        await seed_states(admin)

        import main as app_main
        from starlette.testclient import TestClient

        shared = TestClient(app_main.app, raise_server_exceptions=False)
        shared.__enter__()
        try:
            run_http_checks(shared)
            run_round_trip(shared)
            run_state_rejection_cases(shared)
            await run_refresh_proof(shared, admin_url)
        finally:
            shared.__exit__(None, None, None)
    except Exception:  # noqa: BLE001
        R.bad("driver", "the run aborted", traceback.format_exc())
    finally:
        try:
            await teardown(admin)
        except Exception:  # noqa: BLE001
            R.bad("teardown", "teardown failed", traceback.format_exc())
        post = await counts(admin)
        drift = {t: (pre.get(t), post.get(t)) for t in COUNTED if pre.get(t) != post.get(t)}
        R.expect("teardown-check", not drift,
                 f"every one of the {len(COUNTED)} tables this script writes to is "
                 f"back at its pre-test row count", detail=str(drift))
        await admin.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
