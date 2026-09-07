"""Altruist Sprint 5 verification — sync orchestration: a real, triggerable
``POST /api/v1/altruist/sync`` endpoint (identity resolution + positions/
transactions sync, end to end, for the caller's org) plus an automatic
trigger right after a successful OAuth callback stores a new connection.

Pass/fail only, no prompts. Run:

    doppler run -- python3 scripts/verify_altruist_sprint5_sync_orchestration.py

Every table this script writes to is counted before the first insert and
again after the last delete; a difference of even one row fails the run.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Everything drives the real ASGI app** (``starlette.testclient.TestClient``
  against ``main.app``), exactly like Sprint 4 — this sprint's whole point is
  the HTTP surface + auto-trigger wiring, not re-proving Sprint 2/3's
  function-level parsing/idempotency guarantees (already proven there).

* **The monkeypatch targets the NAMES BOUND in the CALLING modules, not
  ``services.altruist_oauth`` itself** — the same lesson Sprint 4's own
  script documents for ``exchange_code_for_tokens``. ``services.
  altruist_identity`` does ``from services.altruist_oauth import
  call_households, fetch_accounts_for_household`` and ``services.
  altruist_positions_sync`` does the equivalent for ``call_positions``/
  ``call_transactions`` — both bare imports. Patching ``services.
  altruist_oauth.call_households`` would be a silent no-op; this script
  patches ``services.altruist_identity.call_households`` (and the sibling
  three) directly, restored in a ``finally``.

* **Three independent household/account fixtures, keyed by (org_id,
  environment)**, so the SAME fake dispatch function serves: ORG+sandbox
  (permission + idempotency proof, includes one matched + one deliberately
  UNMATCHED account so the summary's ``accounts_unmatched`` field is
  genuinely exercised), ORG+production (the auto-trigger-on-connect round
  trip — a distinct slot so it can't collide with the pre-seeded sandbox
  connection or its already-synced rows), and OTHER_ORG+sandbox (cross-org
  isolation). Each resolves to its OWN pre-seeded Hollisworks account via
  its own ``portfolio.external_references`` row — identity resolution never
  auto-creates a Hollisworks account (Sprint 2), so "already resolved" must
  be represented by a pre-existing crosswalk row, exactly like Sprint 3's
  own fixture.

* **The "no active connection" case is reproduced first, before any fix is
  proven** — ORG+production genuinely has no ``altruist_connections`` row
  until the round-trip test creates one later in the run; calling
  ``POST /altruist/sync`` against it first proves the clean 409, and the
  call log confirms zero household calls were attempted.

* **Refusal is proven with a paired control**, and **cross-org isolation is
  proven by an independent re-read**, matching Sprint 3/4's own convention.

* **Idempotency is proven by literally calling the real endpoint twice and
  diffing real DB row counts** — not by inspecting the code and asserting it
  "should" be idempotent.

* **The auto-trigger is proven by the call log, not just a 200 response.**
  ``CALL_LOG["households"]`` recording ``(ORG, "production")`` is what
  proves the background task actually ran ``resolve_identity`` — a bare 200
  from the callback endpoint would pass even if the background task were
  never wired up at all.

* **Regression, Task 4's last bullet**: Sprints 1-4's own verify scripts are
  re-run as real subprocesses and their exit codes checked — including
  proving the Sprint 1 one-line fix (Task 2 of this sprint) actually holds.

* Teardown is by fixture id / content marker, in FK-safe order, never a
  TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
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
TAG = "altr5verify"

import os  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("ALTRUIST_REDIRECT_URI", "https://app.example.test/altruist/callback")
os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_ID", f"{TAG}-sandbox-client-id")
os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_SECRET", f"{TAG}-sandbox-client-secret")
os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_ID", f"{TAG}-production-client-id")
os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_SECRET", f"{TAG}-production-client-secret")

# ── fixture principals ──────────────────────────────────────────────────────
ADMIN_SUB = f"{TAG}|admin"          # org=ORG,       RBAC role 'admin'  -> manage_custody_connections
NOPERMS_SUB = f"{TAG}|noperm"       # org=ORG,       RBAC role 'member' -> no permission
ORGB_SUB = f"{TAG}|orgb"            # org=OTHER_ORG, RBAC role 'admin'  -> manage_custody_connections, wrong org

ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
NOPERMS_USER_ID = str(uuid5(NAMESPACE_URL, NOPERMS_SUB))
ORGB_USER_ID = str(uuid5(NAMESPACE_URL, ORGB_SUB))
USERS = [ADMIN_USER_ID, NOPERMS_USER_ID, ORGB_USER_ID]

# ── fixture altruist_connections — production slot is DELIBERATELY absent ──
CONN_ORG_SANDBOX = "99000000-0000-0000-0000-0000a17f5001"
CONN_OTHERORG_SANDBOX = "99000000-0000-0000-0000-0000a17f5002"
FIXTURE_CONNECTIONS = [CONN_ORG_SANDBOX, CONN_OTHERORG_SANDBOX]
RUNTIME_CONNECTIONS: list[str] = []
RUNTIME_STATE_VALUES: list[str] = []

# ── fixture entities/accounts — three independent resolved accounts ────────
ENTITY_A = "99000000-0000-0000-0000-0000a17f5201"   # ORG / sandbox slot
ENTITY_RT = "99000000-0000-0000-0000-0000a17f5202"  # ORG / production (round trip) slot
ENTITY_B = "99000000-0000-0000-0000-0000a17f5203"   # OTHER_ORG / sandbox slot
ENTITIES = [ENTITY_A, ENTITY_RT, ENTITY_B]

ACCOUNT_A = "99000000-0000-0000-0000-0000a17f5101"
ACCOUNT_RT = "99000000-0000-0000-0000-0000a17f5102"
ACCOUNT_B = "99000000-0000-0000-0000-0000a17f5103"
ACCOUNTS = [ACCOUNT_A, ACCOUNT_RT, ACCOUNT_B]

EXTREF_A = "99000000-0000-0000-0000-0000a17f5301"
EXTREF_RT = "99000000-0000-0000-0000-0000a17f5302"
EXTREF_B = "99000000-0000-0000-0000-0000a17f5303"

ALTRUIST_HOUSEHOLD_A = f"{TAG}-hh-a"
ALTRUIST_HOUSEHOLD_RT = f"{TAG}-hh-rt"
ALTRUIST_HOUSEHOLD_B = f"{TAG}-hh-b"

ALTRUIST_ACCOUNT_A = f"{TAG}-acct-a"
ALTRUIST_ACCOUNT_A_UNMATCHED = f"{TAG}-acct-a-unmatched"
ALTRUIST_ACCOUNT_RT = f"{TAG}-acct-rt"
ALTRUIST_ACCOUNT_B = f"{TAG}-acct-b"

CUSIP_A = "ALT5VERA001"
CUSIP_RT = "ALT5VERRT01"
CUSIP_B = "ALT5VERB001"
CUSIPS = [CUSIP_A, CUSIP_RT, CUSIP_B]

COUNTED = (
    "public.altruist_connections",
    "public.altruist_oauth_states",
    "public.users",
    "public.user_roles",
    "public.entities",
    "public.accounts",
    "public.account_owners",
    "portfolio.external_references",
    "public.account_import_batches",
    "public.account_import_exceptions",
    "portfolio.positions",
    "portfolio.transactions",
    "portfolio.asset_identifiers",
    "portfolio.assets",
    "public.position_account_exceptions",
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
        print(f"altruist_sprint5_sync_orchestration: {counts.get('PASS', 0)}/{total} PASS"
              + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn) -> None:
    await conn.execute(
        "DELETE FROM portfolio.transactions WHERE org_id = ANY($1::uuid[]) "
        "AND external_ref LIKE $2",
        [ORG, OTHER_ORG], f"{TAG}-%",
    )
    await conn.execute(
        "DELETE FROM public.position_account_exceptions WHERE position_id IN "
        "(SELECT id FROM portfolio.positions WHERE account_id = ANY($1::uuid[]))",
        ACCOUNTS,
    )
    await conn.execute(
        "DELETE FROM portfolio.positions WHERE org_id = ANY($1::uuid[]) "
        "AND account_id = ANY($2::uuid[])",
        [ORG, OTHER_ORG], ACCOUNTS,
    )
    await conn.execute(
        "DELETE FROM portfolio.asset_identifiers WHERE id_value = ANY($1::text[])", CUSIPS,
    )
    await conn.execute(
        "DELETE FROM portfolio.assets WHERE org_id = ANY($1::uuid[]) AND name LIKE $2",
        [ORG, OTHER_ORG], f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM portfolio.external_references WHERE id = ANY($1::uuid[]) "
        "OR external_id = ANY($2::text[])",
        [EXTREF_A, EXTREF_RT, EXTREF_B],
        [ALTRUIST_ACCOUNT_A, ALTRUIST_ACCOUNT_A_UNMATCHED, ALTRUIST_ACCOUNT_RT, ALTRUIST_ACCOUNT_B],
    )
    # sync_resolved_accounts (Sprint 3) also writes NEW external_references
    # rows for every position/transaction it syncs (record_type='position'/
    # 'transaction', external_id like 'pos:<TAG>-...:<date>' / 'txn:<TAG>-...')
    # — these are not covered by the account-crosswalk cleanup above and
    # would otherwise leak across runs, making a LATER run's first sync see
    # a stale "already synced" match and silently skip a real insert.
    await conn.execute(
        "DELETE FROM portfolio.external_references WHERE org_id = ANY($1::uuid[]) "
        "AND record_type IN ('position', 'transaction') AND external_id LIKE $2",
        [ORG, OTHER_ORG], f"%{TAG}%",
    )
    await conn.execute(
        "DELETE FROM public.account_import_exceptions WHERE raw_row ->> 'external_id' = ANY($1::text[])",
        [ALTRUIST_ACCOUNT_A_UNMATCHED],
    )
    await conn.execute(
        "DELETE FROM public.account_import_batches WHERE org_id = ANY($1::uuid[]) "
        "AND custodian_code = 'ALTRUIST'",
        [ORG, OTHER_ORG],
    )
    await conn.execute(
        "DELETE FROM public.account_owners WHERE account_id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    conn_ids = FIXTURE_CONNECTIONS + RUNTIME_CONNECTIONS
    if conn_ids:
        await conn.execute(
            "DELETE FROM public.altruist_connections WHERE id = ANY($1::uuid[])", conn_ids)
    if RUNTIME_STATE_VALUES:
        await conn.execute(
            "DELETE FROM public.altruist_oauth_states WHERE state = ANY($1::text[])",
            RUNTIME_STATE_VALUES,
        )
    await conn.execute(
        "DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", USERS)
    await conn.execute(
        "DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def seed_users(conn) -> None:
    for user_id, org, sub, role_name in (
        (ADMIN_USER_ID, ORG, ADMIN_SUB, "admin"),
        (NOPERMS_USER_ID, ORG, NOPERMS_SUB, "member"),
        (ORGB_USER_ID, OTHER_ORG, ORGB_SUB, "admin"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify Altruist Sprint5', $4, 'member', true)
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
    for cid, org, client_id, access_tok, refresh_tok in (
        (CONN_ORG_SANDBOX, ORG, f"{TAG}-client-org", f"{TAG}-access-org", f"{TAG}-refresh-org"),
        (CONN_OTHERORG_SANDBOX, OTHER_ORG, f"{TAG}-client-otherorg",
         f"{TAG}-access-otherorg", f"{TAG}-refresh-otherorg"),
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
                $1::uuid, $2::uuid, 'sandbox', 'connected', $3,
                $4, $5, $6, $7, $8, $9,
                'accounts:read', $10, $11, NULL
            )
            """,
            cid, org, client_id,
            encrypt_secret(f"{TAG}-secret"), last4(f"{TAG}-secret"),
            encrypt_secret(access_tok), last4(access_tok),
            encrypt_secret(refresh_tok), last4(refresh_tok),
            now + timedelta(hours=1), now,
        )


async def seed_accounts(conn) -> None:
    for entity_id, org, name in (
        (ENTITY_A, ORG, f"{TAG} owner A"),
        (ENTITY_RT, ORG, f"{TAG} owner RT"),
        (ENTITY_B, OTHER_ORG, f"{TAG} owner B"),
    ):
        await conn.execute(
            """
            INSERT INTO public.entities (id, org_id, entity_type, display_name, status)
            VALUES ($1::uuid, $2::uuid, 'individual', $3, 'active')
            """,
            entity_id, org, name,
        )

    for account_id, org, entity_id, suffix in (
        (ACCOUNT_A, ORG, ENTITY_A, "5678"),
        (ACCOUNT_RT, ORG, ENTITY_RT, "5679"),
        (ACCOUNT_B, OTHER_ORG, ENTITY_B, "5680"),
    ):
        await conn.execute(
            """
            INSERT INTO public.accounts (
                id, org_id, account_number_masked, account_number_hash,
                custodian_code, registration_type, tax_status, primary_entity_id
            ) VALUES ($1::uuid, $2::uuid, $3, $4, 'ALT', 'INDIVIDUAL', 'TAXABLE', $5::uuid)
            """,
            account_id, org, f"{TAG}-****{suffix}", f"{TAG}-hash-{suffix}", entity_id,
        )
        await conn.execute(
            """
            INSERT INTO public.account_owners
                (org_id, account_id, entity_id, ownership_pct, role)
            VALUES ($1::uuid, $2::uuid, $3::uuid, 100, 'owner')
            """,
            org, account_id, entity_id,
        )

    # Pre-existing crosswalk rows — identity resolution never auto-creates a
    # Hollisworks account (Sprint 2); "already resolved" must be represented
    # by a real portfolio.external_references row, exactly like Sprint 3's
    # own fixture. EXTREF_RT is DELIBERATELY NOT seeded here — sync_
    # resolved_accounts enumerates ALL of an org's resolved accounts
    # regardless of which environment resolved them (the crosswalk table
    # isn't environment-scoped), so seeding it up front would make ORG's
    # very first sandbox sync also pick up the round-trip account. It is
    # inserted immediately before the round-trip test instead — see
    # ``insert_extref_rt`` below.
    for extref_id, org, altruist_id, account_id in (
        (EXTREF_A, ORG, ALTRUIST_ACCOUNT_A, ACCOUNT_A),
        (EXTREF_B, OTHER_ORG, ALTRUIST_ACCOUNT_B, ACCOUNT_B),
    ):
        await conn.execute(
            """
            INSERT INTO portfolio.external_references
                (id, org_id, source_system, external_id, record_type, record_id)
            VALUES ($1::uuid, $2::uuid, 'ALTRUIST', $3, 'account', $4::uuid)
            """,
            extref_id, org, altruist_id, account_id,
        )


async def insert_extref_rt(admin_url: str) -> None:
    conn = await connect(admin_url)
    try:
        await conn.execute(
            """
            INSERT INTO portfolio.external_references
                (id, org_id, source_system, external_id, record_type, record_id)
            VALUES ($1::uuid, $2::uuid, 'ALTRUIST', $3, 'account', $4::uuid)
            """,
            EXTREF_RT, ORG, ALTRUIST_ACCOUNT_RT, ACCOUNT_RT,
        )
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Fake Altruist calls — monkeypatched onto the CALLING modules' own bound
# names (see module docstring), keyed by (org_id, environment)
# ═══════════════════════════════════════════════════════════════════════════
HOUSEHOLD_ACCOUNTS = {
    (ORG, "sandbox"): {
        "household_id": ALTRUIST_HOUSEHOLD_A,
        "accounts": [
            {"id": ALTRUIST_ACCOUNT_A, "name": f"{TAG} Acct A"},
            {"id": ALTRUIST_ACCOUNT_A_UNMATCHED, "name": f"{TAG} Acct A Unmatched"},
        ],
    },
    (ORG, "production"): {
        "household_id": ALTRUIST_HOUSEHOLD_RT,
        "accounts": [{"id": ALTRUIST_ACCOUNT_RT, "name": f"{TAG} Acct RT"}],
    },
    (OTHER_ORG, "sandbox"): {
        "household_id": ALTRUIST_HOUSEHOLD_B,
        "accounts": [{"id": ALTRUIST_ACCOUNT_B, "name": f"{TAG} Acct B"}],
    },
}

POSITIONS_BY_ALTRUIST_ACCOUNT = {
    ALTRUIST_ACCOUNT_A: {
        "id": f"{TAG}-pos-a", "cusip": CUSIP_A, "name": f"{TAG} Security A",
        "asset_type": "equity", "quantity": "100", "market_value": "15000.00",
        "cost_basis": "12000.00", "currency_code": "USD",
    },
    ALTRUIST_ACCOUNT_RT: {
        "id": f"{TAG}-pos-rt", "cusip": CUSIP_RT, "name": f"{TAG} Security RT",
        "asset_type": "equity", "quantity": "200", "market_value": "30000.00",
        "cost_basis": "24000.00", "currency_code": "USD",
    },
    ALTRUIST_ACCOUNT_B: {
        "id": f"{TAG}-pos-b", "cusip": CUSIP_B, "name": f"{TAG} Security B",
        "asset_type": "equity", "quantity": "50", "market_value": "7500.00",
        "cost_basis": "6000.00", "currency_code": "USD",
    },
}

TRANSACTIONS_BY_ALTRUIST_ACCOUNT = {
    ALTRUIST_ACCOUNT_A: [{
        "id": f"{TAG}-txn-a-buy", "type": "buy", "cusip": CUSIP_A,
        "trade_date": "2026-01-15", "settle_date": "2026-01-17",
        "quantity": "100", "price": "120.00", "gross_amount": "12000.00",
        "fees": "0.00", "net_amount": "12000.00", "currency_code": "USD",
    }],
    ALTRUIST_ACCOUNT_RT: [{
        "id": f"{TAG}-txn-rt-buy", "type": "buy", "cusip": CUSIP_RT,
        "trade_date": "2026-01-15", "settle_date": "2026-01-17",
        "quantity": "200", "price": "120.00", "gross_amount": "24000.00",
        "fees": "0.00", "net_amount": "24000.00", "currency_code": "USD",
    }],
    ALTRUIST_ACCOUNT_B: [{
        "id": f"{TAG}-txn-b-buy", "type": "buy", "cusip": CUSIP_B,
        "trade_date": "2026-01-15", "settle_date": "2026-01-17",
        "quantity": "50", "price": "120.00", "gross_amount": "6000.00",
        "fees": "0.00", "net_amount": "6000.00", "currency_code": "USD",
    }],
}

CALL_LOG: dict[str, list] = {"households": [], "accounts": [], "positions": [], "transactions": []}


async def fake_call_households(conn, *, org_id, environment, transport=None, timeout=15.0):
    CALL_LOG["households"].append((org_id, environment))
    data = HOUSEHOLD_ACCOUNTS.get((org_id, environment))
    if data is None:
        return []
    return [{"id": data["household_id"], "name": f"{TAG} household"}]


async def fake_fetch_accounts_for_household(conn, *, org_id, environment, household_id, transport=None, timeout=15.0):
    CALL_LOG["accounts"].append((org_id, environment, household_id))
    data = HOUSEHOLD_ACCOUNTS.get((org_id, environment))
    if data is None or data["household_id"] != household_id:
        return []
    return list(data["accounts"])


async def fake_call_positions(conn, *, org_id, environment, account_id, transport=None, timeout=15.0):
    CALL_LOG["positions"].append((org_id, environment, account_id))
    row = POSITIONS_BY_ALTRUIST_ACCOUNT.get(account_id)
    return [row] if row else []


async def fake_call_transactions(conn, *, org_id, environment, account_id, transport=None, timeout=15.0):
    CALL_LOG["transactions"].append((org_id, environment, account_id))
    return [dict(r) for r in TRANSACTIONS_BY_ALTRUIST_ACCOUNT.get(account_id, [])]


# ═══════════════════════════════════════════════════════════════════════════
# HTTP harness — one shared TestClient, identity switched per call
# ═══════════════════════════════════════════════════════════════════════════
class _Principal:
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


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 — real proof
# ═══════════════════════════════════════════════════════════════════════════
async def run_no_connection_case(client, admin_url: str) -> None:
    """[Y] the failure state is reproduced FIRST: ORG+production has no
    altruist_connections row at all yet (the round-trip test creates one
    later) — a clean 409, never a 500, and no network call attempted."""
    admin = _Principal(client, ORG, ADMIN_SUB)
    before_calls = len(CALL_LOG["households"])

    r = admin.post("/api/v1/altruist/sync", json={"environment": "production"}, headers=HEADERS)
    R.expect("5-1a", r.status_code == 409,
             "POST /altruist/sync for an org+environment with no active connection "
             "returns a clean 409, not a raw 500", detail=f"{r.status_code} {r.text}")
    R.expect("5-1b", "connect" in r.text.lower() or "no altruist connection" in r.text.lower(),
             "the 409 body names the real problem (no connection / must connect first)",
             detail=r.text)
    R.expect("5-1c", len(CALL_LOG["households"]) == before_calls,
             "no household call was attempted — the guard fired before any network call",
             detail=f"before={before_calls} after={len(CALL_LOG['households'])}")


def run_permission_checks(client) -> None:
    """[Y] a caller without manage_custody_connections is refused on the
    IDENTICAL request an admin caller succeeds on."""
    admin = _Principal(client, ORG, ADMIN_SUB)
    noperms = _Principal(client, ORG, NOPERMS_SUB)

    r_refused = noperms.post(
        "/api/v1/altruist/sync", json={"environment": "sandbox"}, headers=HEADERS)
    R.expect("5-2a", r_refused.status_code == 403,
             "a caller WITHOUT manage_custody_connections is refused on /altruist/sync",
             detail=f"{r_refused.status_code} {r_refused.text}")


async def run_sync_and_idempotency(client, admin_url: str) -> None:
    """[Y] the real round trip through /altruist/sync for ORG/sandbox:
    resolved account + unmatched account + positions + transactions, then
    calling it AGAIN produces identical DB row counts, diffed."""
    admin = _Principal(client, ORG, ADMIN_SUB)

    conn = await connect(admin_url)
    try:
        before_pos = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
            ORG, ACCOUNT_A)
        before_txn = await conn.fetchval(
            "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1::uuid AND external_ref = $2",
            ORG, f"{TAG}-txn-a-buy")

        r1 = admin.post("/api/v1/altruist/sync", json={"environment": "sandbox"}, headers=HEADERS)
        R.expect("5-3a", r1.status_code == 200, "run 1: POST /altruist/sync succeeds",
                 detail=f"{r1.status_code} {r1.text}")
        body1 = r1.json() if r1.status_code == 200 else {}
        R.expect("5-3b",
                 body1.get("households_seen") == 1 and body1.get("accounts_resolved") == 1
                 and body1.get("accounts_unmatched") == 1 and body1.get("accounts_synced") == 1,
                 "run 1: the response reports real counts — 1 household, 1 resolved account, "
                 "1 unmatched account, 1 account synced", detail=str(body1))
        R.expect("5-3c",
                 body1.get("positions_created") == 1 and body1.get("positions_skipped_duplicate") == 0
                 and body1.get("transactions_created") == 1
                 and body1.get("transactions_skipped_duplicate") == 0,
                 "run 1: exactly one position and one transaction created, nothing skipped",
                 detail=str(body1))

        after_pos_1 = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
            ORG, ACCOUNT_A)
        after_txn_1 = await conn.fetchval(
            "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1::uuid AND external_ref = $2",
            ORG, f"{TAG}-txn-a-buy")
        R.expect("5-3d", after_pos_1 == before_pos + 1 and after_txn_1 == before_txn + 1,
                 "run 1's reported counts match what actually persisted — an INDEPENDENT "
                 "re-read confirms the real write", detail=f"pos {before_pos}->{after_pos_1}, "
                 f"txn {before_txn}->{after_txn_1}")

        pos_row = await conn.fetchrow(
            """
            SELECT p.source_system, p.authority, p.quantity, p.market_value, a.name AS asset_name
            FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
              AND p.valid_to IS NULL AND p.system_to IS NULL
            """,
            ORG, ACCOUNT_A,
        )
        R.expect("5-3e",
                 pos_row is not None and pos_row["source_system"] == "altruist"
                 and pos_row["quantity"] == 100 and pos_row["market_value"] == 15000,
                 "the synced position carries the correct source_system + values from "
                 "the synthetic payload", detail=str(dict(pos_row) if pos_row else None))

        exc_row = await conn.fetchrow(
            "SELECT reason_code FROM public.account_import_exceptions "
            "WHERE org_id = $1::uuid AND raw_row ->> 'external_id' = $2",
            ORG, ALTRUIST_ACCOUNT_A_UNMATCHED,
        )
        R.expect("5-3f", exc_row is not None and exc_row["reason_code"] == "altruist_account_unmatched",
                 "the deliberately-unmatched account landed a real "
                 "account_import_exceptions row, not silently dropped",
                 detail=str(dict(exc_row) if exc_row else None))

        # ── the same caller succeeds where NOPERMS was refused — the paired control.
        R.expect("5-2b", r1.status_code == 200,
                 "the IDENTICAL /altruist/sync request succeeds once the caller holds "
                 "manage_custody_connections — only the caller changed vs [5-2a]")

        # ── run 2: identical request, idempotency proven by a real diff.
        r2 = admin.post("/api/v1/altruist/sync", json={"environment": "sandbox"}, headers=HEADERS)
        R.expect("5-4a", r2.status_code == 200, "run 2: POST /altruist/sync succeeds again",
                 detail=f"{r2.status_code} {r2.text}")
        body2 = r2.json() if r2.status_code == 200 else {}
        R.expect("5-4b",
                 body2.get("positions_created") == 0 and body2.get("positions_skipped_duplicate") == 1
                 and body2.get("transactions_created") == 0
                 and body2.get("transactions_skipped_duplicate") == 1,
                 "run 2: both the position and the transaction are no-op skips, not "
                 "second inserts", detail=str(body2))
        R.expect("5-4c",
                 body2.get("accounts_resolved") == 1 and body2.get("accounts_unmatched") == 1,
                 "run 2: identity resolution counts are unchanged — the same accounts, "
                 "re-confirmed not re-created", detail=str(body2))

        after_pos_2 = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
            ORG, ACCOUNT_A)
        after_txn_2 = await conn.fetchval(
            "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1::uuid AND external_ref = $2",
            ORG, f"{TAG}-txn-a-buy")
        exc_count_2 = await conn.fetchval(
            "SELECT count(*) FROM public.account_import_exceptions WHERE org_id = $1::uuid "
            "AND raw_row ->> 'external_id' = $2",
            ORG, ALTRUIST_ACCOUNT_A_UNMATCHED,
        )
        R.expect("5-4d",
                 after_pos_2 == after_pos_1 and after_txn_2 == after_txn_1 and exc_count_2 == 1,
                 "row counts after run 2 are IDENTICAL to after run 1 — calling the real "
                 "endpoint twice in a row is provably idempotent, diffed not asserted",
                 detail=f"pos {after_pos_1}->{after_pos_2}; txn {after_txn_1}->{after_txn_2}; "
                 f"exceptions={exc_count_2}")
    finally:
        await conn.close()


async def run_cross_org_isolation(client, admin_url: str) -> None:
    """[Y] org B's caller syncing their own org cannot affect or read org A's
    resolved accounts/positions/transactions — same endpoint, only the
    caller's org changes."""
    orgb = _Principal(client, OTHER_ORG, ORGB_SUB)

    conn = await connect(admin_url)
    try:
        org_a_pos_before = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
            ORG, ACCOUNT_A)
        org_a_txn_before = await conn.fetchval(
            "SELECT count(*) FROM portfolio.transactions t "
            "JOIN portfolio.positions p ON p.id = t.position_id "
            "WHERE t.org_id = $1::uuid AND p.account_id = $2::uuid",
            ORG, ACCOUNT_A)

        r = orgb.post("/api/v1/altruist/sync", json={"environment": "sandbox"}, headers=HEADERS)
        R.expect("5-5a", r.status_code == 200, "org B's own caller syncs org B's own connection",
                 detail=f"{r.status_code} {r.text}")
        body = r.json() if r.status_code == 200 else {}
        R.expect("5-5b", body.get("accounts_resolved") == 1 and body.get("accounts_synced") == 1
                  and body.get("positions_created") == 1 and body.get("transactions_created") == 1,
                  "org B's sync resolves and syncs ONLY org B's own account", detail=str(body))

        org_b_pos = await conn.fetchrow(
            """
            SELECT p.org_id::text AS org_id, a.name AS asset_name
            FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
              AND p.valid_to IS NULL AND p.system_to IS NULL
            """,
            OTHER_ORG, ACCOUNT_B,
        )
        R.expect("5-5c", org_b_pos is not None and org_b_pos["org_id"] == OTHER_ORG,
                  "org B's synced position is independently re-readable under org B's own org_id",
                  detail=str(dict(org_b_pos) if org_b_pos else None))

        org_a_pos_after = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
            ORG, ACCOUNT_A)
        org_a_txn_after = await conn.fetchval(
            "SELECT count(*) FROM portfolio.transactions t "
            "JOIN portfolio.positions p ON p.id = t.position_id "
            "WHERE t.org_id = $1::uuid AND p.account_id = $2::uuid",
            ORG, ACCOUNT_A)
        R.expect("5-5d", org_a_pos_after == org_a_pos_before and org_a_txn_after == org_a_txn_before,
                  "org A's own position/transaction counts are UNCHANGED by org B's sync — "
                  "an independent re-read, not org B's own response",
                  detail=f"pos {org_a_pos_before}->{org_a_pos_after}; "
                  f"txn {org_a_txn_before}->{org_a_txn_after}")

        leaked = await conn.fetchval(
            """
            SELECT count(*) FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
            WHERE p.org_id = $1::uuid AND a.name LIKE $2
            """,
            ORG, f"%Security B%",
        )
        R.expect("5-5e", leaked == 0,
                  "org B's security never appears under org A's org_id — no content leakage",
                  detail=str(leaked))
    finally:
        await conn.close()


async def run_auto_trigger_round_trip(client, admin_url: str) -> None:
    """[Y] connect -> synthetic callback (monkeypatched token exchange +
    monkeypatched households/accounts/positions/transactions) -> auto-sync
    fires in the background -> both an explicit /altruist/sync call's
    response AND an independent DB re-read show the correct resolved
    account, positions, transactions — all on the ORG+production slot,
    which had NO connection at all until this test creates one."""
    import services.altruist_oauth as altruist_oauth
    from services.altruist_oauth import TokenResponse

    admin = _Principal(client, ORG, ADMIN_SUB)

    # Simulates an admin having already linked this Altruist account to a
    # Hollisworks account before enabling the connection — inserted only now
    # (not in the shared fixture setup) so ORG's earlier sandbox sync tests
    # above are not affected by an extra resolved account (see seed_accounts'
    # module note: sync_resolved_accounts enumerates ALL of an org's
    # resolved accounts, not just the ones from the environment being synced).
    await insert_extref_rt(admin_url)

    new_access = f"{TAG}-rt-access"
    new_refresh = f"{TAG}-rt-refresh"

    async def fake_exchange(*, environment: str, code: str, timeout: float = 15.0):
        assert environment == "production"
        assert code == "synthetic-auth-code-rt"
        return TokenResponse(access_token=new_access, refresh_token=new_refresh,
                              expires_in=3600, scope="accounts:read")

    original_exchange = altruist_oauth.exchange_code_for_tokens
    altruist_oauth.exchange_code_for_tokens = fake_exchange
    before_household_calls = len(CALL_LOG["households"])
    try:
        r_connect = admin.post(
            "/api/v1/altruist/connect", json={"environment": "production"}, headers=HEADERS)
        R.expect("5-6a", r_connect.status_code == 200 and "authorize_url" in r_connect.json(),
                 "POST /altruist/connect (production) returns an authorize_url",
                 detail=f"{r_connect.status_code} {r_connect.text}")

        authorize_url = r_connect.json().get("authorize_url", "")
        state_value = parse_qs(urlparse(authorize_url).query).get("state", [None])[0]
        if state_value:
            RUNTIME_STATE_VALUES.append(state_value)

        r_callback = admin.get(
            "/api/v1/altruist/callback",
            params={"code": "synthetic-auth-code-rt", "state": state_value},
            headers=HEADERS,
        )
        R.expect("5-6b", r_callback.status_code == 200 and r_callback.json().get("connected") is True,
                 "GET /altruist/callback with a valid state + monkeypatched token exchange succeeds",
                 detail=f"{r_callback.status_code} {r_callback.text}")
        new_conn_id = r_callback.json().get("connection_id")
        if new_conn_id:
            RUNTIME_CONNECTIONS.append(new_conn_id)

        # TestClient runs BackgroundTasks synchronously within the ASGI call
        # (in-process transport) — by the time the callback response has
        # returned, the auto-sync background task has already completed.
        R.expect("5-6c", (ORG, "production") in CALL_LOG["households"][before_household_calls:],
                 "the auto-sync background task actually called resolve_identity for "
                 "ORG/production — proven by the call log, not just a 200 response",
                 detail=str(CALL_LOG["households"][before_household_calls:]))
    finally:
        altruist_oauth.exchange_code_for_tokens = original_exchange

    conn = await connect(admin_url)
    try:
        pos_row = await conn.fetchrow(
            """
            SELECT p.source_system, p.quantity, p.market_value, a.name AS asset_name
            FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
              AND p.valid_to IS NULL AND p.system_to IS NULL
            """,
            ORG, ACCOUNT_RT,
        )
        R.expect("5-6d",
                 pos_row is not None and pos_row["source_system"] == "altruist"
                 and pos_row["quantity"] == 200 and pos_row["market_value"] == 30000
                 and pos_row["asset_name"] == f"{TAG} Security RT",
                 "an INDEPENDENT re-read (fresh connection, not through the API) shows "
                 "the auto-triggered sync's correct position, without ever calling "
                 "/altruist/sync directly", detail=str(dict(pos_row) if pos_row else None))

        txn_row = await conn.fetchrow(
            "SELECT transaction_type_code, gross_amount FROM portfolio.transactions "
            "WHERE org_id = $1::uuid AND external_ref = $2",
            ORG, f"{TAG}-txn-rt-buy",
        )
        R.expect("5-6e",
                 txn_row is not None and txn_row["transaction_type_code"] == "buy"
                 and txn_row["gross_amount"] == 24000,
                 "the auto-triggered sync's transaction is independently re-readable",
                 detail=str(dict(txn_row) if txn_row else None))
    finally:
        await conn.close()

    # Calling /altruist/sync explicitly afterward is idempotent — its own
    # response reflects the state the auto-trigger already produced, tying
    # the endpoint's response back to the independent re-read above.
    r_explicit = admin.post("/api/v1/altruist/sync", json={"environment": "production"}, headers=HEADERS)
    R.expect("5-6f", r_explicit.status_code == 200, "explicit /altruist/sync on the same slot succeeds",
             detail=f"{r_explicit.status_code} {r_explicit.text}")
    body = r_explicit.json() if r_explicit.status_code == 200 else {}
    # accounts_synced is 2 here (ACCOUNT_A + ACCOUNT_RT), not 1 — see [FIND]
    # below: sync_resolved_accounts enumerates ALL of the org's resolved
    # accounts regardless of which environment resolved them, so ACCOUNT_A
    # (resolved earlier, under sandbox) is swept in by this production-slot
    # call too. Both are correctly skip-duplicates by this point.
    R.expect("5-6g",
             body.get("accounts_resolved") == 1 and body.get("accounts_synced") == 2
             and body.get("positions_created") == 0
             and body.get("positions_skipped_duplicate") == 2
             and body.get("transactions_created") == 0
             and body.get("transactions_skipped_duplicate") == 2,
             "the sync endpoint's own response confirms the round-trip account is "
             "already resolved and its position/transaction are skip-duplicates (not "
             "re-created) — the explicit call and the auto-trigger agree",
             detail=str(body))
    R.find("5-6-note",
           "[FIND] accounts_synced/positions/transactions on this call cover BOTH "
           "ACCOUNT_A (resolved earlier under the sandbox slot) and ACCOUNT_RT "
           "(resolved here) — sync_resolved_accounts (Sprint 3) enumerates ALL of an "
           "org's resolved accounts from portfolio.external_references, which is not "
           "environment-scoped. Calling /altruist/sync for one environment re-syncs "
           "every resolved account for the org, not just the ones tied to that "
           "connection's environment. Real (harmless, since skip-duplicate makes it a "
           "no-op for already-synced accounts) but worth knowing before assuming the "
           "summary is scoped to one environment's accounts.")


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
                      "contradicts Sprints 1-4's finding; re-confirmed live, not assumed")
        return
    R.blocked("T3", "no ALTRUIST_* secrets exist in this project's Doppler config "
                     "(`doppler secrets --only-names`, project hollisworks), re-confirmed live "
                     "for Sprint 5 — sandbox smoke test is genuinely blocked; no live call attempted")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 (last bullet) — regression: Sprints 1-4's own verify scripts
# ═══════════════════════════════════════════════════════════════════════════
def check_regression() -> None:
    for label, script in (
        ("1", "verify_altruist_sprint1_openapi_connection.py"),
        ("2", "verify_altruist_sprint2_identity_resolution.py"),
        ("3", "verify_altruist_sprint3_positions_transactions_sync.py"),
        ("4", "verify_altruist_sprint4_connection_api.py"),
    ):
        try:
            out = subprocess.run(
                ["doppler", "run", "--", "python3", str(HERE / script)],
                cwd=str(API_DIR), capture_output=True, text=True, timeout=180,
            )
        except Exception as exc:  # noqa: BLE001
            R.bad(f"5-REGR-{label}", f"could not run {script}", repr(exc))
            continue
        tail = "\n".join(out.stdout.strip().splitlines()[-6:])
        if out.returncode != 0 and out.stderr.strip():
            tail += "\nSTDERR:\n" + "\n".join(out.stderr.strip().splitlines()[-15:])
        R.expect(f"5-REGR-{label}", out.returncode == 0,
                 f"Sprint {label}'s own verify script ({script}) reports clean on "
                 f"unmodified main + this sprint's changes",
                 detail=tail)


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
        await seed_accounts(admin)

        import services.altruist_identity as altruist_identity
        import services.altruist_positions_sync as altruist_positions_sync

        original_call_households = altruist_identity.call_households
        original_fetch_accounts = altruist_identity.fetch_accounts_for_household
        original_call_positions = altruist_positions_sync.call_positions
        original_call_transactions = altruist_positions_sync.call_transactions

        altruist_identity.call_households = fake_call_households
        altruist_identity.fetch_accounts_for_household = fake_fetch_accounts_for_household
        altruist_positions_sync.call_positions = fake_call_positions
        altruist_positions_sync.call_transactions = fake_call_transactions

        import main as app_main
        from starlette.testclient import TestClient

        shared = TestClient(app_main.app, raise_server_exceptions=False)
        shared.__enter__()
        try:
            await run_no_connection_case(shared, admin_url)
            run_permission_checks(shared)
            await run_sync_and_idempotency(shared, admin_url)
            await run_cross_org_isolation(shared, admin_url)
            await run_auto_trigger_round_trip(shared, admin_url)
        finally:
            shared.__exit__(None, None, None)
            altruist_identity.call_households = original_call_households
            altruist_identity.fetch_accounts_for_household = original_fetch_accounts
            altruist_positions_sync.call_positions = original_call_positions
            altruist_positions_sync.call_transactions = original_call_transactions
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

    # Run AFTER this sprint's own fixtures are torn down — regression should
    # observe Sprints 1-4's own scripts against a clean DB, not while this
    # sprint's own leftover rows are still live.
    check_regression()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
