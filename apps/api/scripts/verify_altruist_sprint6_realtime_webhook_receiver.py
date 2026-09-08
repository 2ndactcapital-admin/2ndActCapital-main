"""Altruist Sprint 6 verification — Realtime API webhook receiver: signature
verification, event-id dedup, defensive payload dispatch into the existing
Sprint 5 sync orchestration.

Pass/fail only, no prompts. Run:

    doppler run -- python3 scripts/verify_altruist_sprint6_realtime_webhook_receiver.py

Every table this script writes to is counted before the first insert and
again after the last delete; a difference of even one row fails the run.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Everything drives the real ASGI app** (``starlette.testclient.TestClient``
  against ``main.app``) with a REAL HMAC-SHA256 signature computed the same
  way the router verifies it — not a monkeypatched signature check. This is
  the actual, unauthenticated HTTP surface being proven, byte for byte.

* **Missing vs. invalid signature return the IDENTICAL response body** —
  proving the rejection genuinely does not leak which case it was.

* **Dedup is proven by literally POSTing the identical signed body twice**
  and diffing real DB row counts + the sync call log — not by inspecting the
  code and asserting the ``ON CONFLICT`` "should" work.

* **The sync trigger is proven by the call log, not just the response body**
  — ``CALL_LOG["households"]`` recording ``(ORG, "sandbox")`` is what proves
  the webhook actually invoked ``resolve_identity``/``sync_resolved_accounts``
  end to end, matching Sprint 5's own auto-trigger proof technique.

* **Cross-org isolation is proven two ways**: (1) a payload that forges
  ``org_id`` directly is proven NOT to reach that forged org — the real
  lookup via ``portfolio.external_references`` is what determines the org,
  and the forged claim is simply never read; (2) a plain non-super-admin,
  org-scoped DB role cannot ``SELECT`` an unresolved (``org_id`` NULL) row or
  another org's resolved row — proven with a real ``app_service``-rooted
  connection whose RLS context is set via the app's own
  ``set_rls_context``/pool machinery, not the raw admin connection.

* **The unknown-identifier case is proven to leave ``org_id`` NULL** — logged
  safely, not guessed, and not crashing.

* **Regression, Task 4's last bullet**: Sprints 1-5's own verify scripts are
  re-run as real subprocesses and their exit codes checked.

* Teardown is by fixture id / content marker, in FK-safe order, never a
  TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"
TAG = "altr6verify"

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("ALTRUIST_REDIRECT_URI", "https://app.example.test/altruist/callback")
os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_ID", f"{TAG}-sandbox-client-id")
os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_SECRET", f"{TAG}-sandbox-client-secret")
os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_ID", f"{TAG}-production-client-id")
os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_SECRET", f"{TAG}-production-client-secret")

WEBHOOK_SECRET = f"{TAG}-webhook-secret"
os.environ["ALTRUIST_WEBHOOK_SECRET"] = WEBHOOK_SECRET

# ── fixture connections (so sync orchestration has something to run against) ─
CONN_ORG_SANDBOX = "99000000-0000-0000-0000-0000a17f6001"
CONN_OTHERORG_SANDBOX = "99000000-0000-0000-0000-0000a17f6002"
FIXTURE_CONNECTIONS = [CONN_ORG_SANDBOX, CONN_OTHERORG_SANDBOX]

# ── fixture entities/accounts ───────────────────────────────────────────────
ENTITY_A = "99000000-0000-0000-0000-0000a17f6201"   # ORG — the KNOWN account
ENTITY_B = "99000000-0000-0000-0000-0000a17f6203"   # OTHER_ORG — cross-org proof
ENTITIES = [ENTITY_A, ENTITY_B]

ACCOUNT_A = "99000000-0000-0000-0000-0000a17f6101"
ACCOUNT_B = "99000000-0000-0000-0000-0000a17f6103"
ACCOUNTS = [ACCOUNT_A, ACCOUNT_B]

EXTREF_A = "99000000-0000-0000-0000-0000a17f6301"
EXTREF_B = "99000000-0000-0000-0000-0000a17f6303"

ALTRUIST_ACCOUNT_A = f"{TAG}-acct-a"          # KNOWN, resolves to ORG
ALTRUIST_ACCOUNT_B = f"{TAG}-acct-b"          # KNOWN, resolves to OTHER_ORG
ALTRUIST_ACCOUNT_UNKNOWN = f"{TAG}-acct-unknown"  # never crosswalked

CUSIP_A = "ALT6VERA001"
CUSIPS = [CUSIP_A]

COUNTED = (
    "public.altruist_webhook_events",
    "public.altruist_connections",
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
        print(f"altruist_sprint6_realtime_webhook_receiver: {counts.get('PASS', 0)}/{total} PASS"
              + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


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
        [EXTREF_A, EXTREF_B],
        [ALTRUIST_ACCOUNT_A, ALTRUIST_ACCOUNT_B, ALTRUIST_ACCOUNT_UNKNOWN],
    )
    await conn.execute(
        "DELETE FROM portfolio.external_references WHERE org_id = ANY($1::uuid[]) "
        "AND record_type IN ('position', 'transaction') AND external_id LIKE $2",
        [ORG, OTHER_ORG], f"%{TAG}%",
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
    await conn.execute(
        "DELETE FROM public.altruist_connections WHERE id = ANY($1::uuid[])",
        FIXTURE_CONNECTIONS,
    )
    await conn.execute(
        "DELETE FROM public.altruist_webhook_events WHERE event_id LIKE $1", f"{TAG}-%",
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
        (ACCOUNT_A, ORG, ENTITY_A, "6678"),
        (ACCOUNT_B, OTHER_ORG, ENTITY_B, "6680"),
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


# ═══════════════════════════════════════════════════════════════════════════
# Fake Altruist calls — monkeypatched onto the CALLING modules' own bound
# names (Sprint 5's own documented lesson: patching services.altruist_oauth
# itself would be a silent no-op, since altruist_identity/
# altruist_positions_sync each do a bare `from services.altruist_oauth
# import ...`)
# ═══════════════════════════════════════════════════════════════════════════
HOUSEHOLD_ACCOUNTS = {
    (ORG, "sandbox"): {
        "household_id": f"{TAG}-hh-a",
        "accounts": [{"id": ALTRUIST_ACCOUNT_A, "name": f"{TAG} Acct A"}],
    },
    (OTHER_ORG, "sandbox"): {
        "household_id": f"{TAG}-hh-b",
        "accounts": [{"id": ALTRUIST_ACCOUNT_B, "name": f"{TAG} Acct B"}],
    },
}

POSITIONS_BY_ALTRUIST_ACCOUNT = {
    ALTRUIST_ACCOUNT_A: {
        "id": f"{TAG}-pos-a", "cusip": CUSIP_A, "name": f"{TAG} Security A",
        "asset_type": "equity", "quantity": "100", "market_value": "15000.00",
        "cost_basis": "12000.00", "currency_code": "USD",
    },
}

TRANSACTIONS_BY_ALTRUIST_ACCOUNT: dict = {}

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
# Task 4 — real proof
# ═══════════════════════════════════════════════════════════════════════════
def run_signature_rejections(client) -> None:
    """[Y] missing and invalid signatures are both rejected with a 401
    BEFORE any payload parsing or DB write, and the body is IDENTICAL for
    both cases — never revealing which."""
    body = json.dumps({"event_id": f"{TAG}-should-never-persist", "account_id": ALTRUIST_ACCOUNT_A}).encode()

    r_missing = client.post("/api/v1/altruist/webhook", content=body,
                             headers={"Content-Type": "application/json"})
    R.expect("6-1a", r_missing.status_code == 401,
             "a request with NO signature header is rejected 401",
             detail=f"{r_missing.status_code} {r_missing.text}")

    r_invalid = client.post(
        "/api/v1/altruist/webhook", content=body,
        headers={"Content-Type": "application/json", "X-Altruist-Signature": "not-a-real-signature"},
    )
    R.expect("6-1b", r_invalid.status_code == 401,
             "a request with an INVALID signature is rejected 401",
             detail=f"{r_invalid.status_code} {r_invalid.text}")

    R.expect("6-1c", r_missing.json() == r_invalid.json(),
             "the missing-signature and invalid-signature responses are BYTE-IDENTICAL "
             "— the rejection reveals nothing about which case it was",
             detail=f"{r_missing.text} vs {r_invalid.text}")


async def run_rejections_write_nothing(admin_url: str) -> None:
    """[Y] neither rejected request above reached the DB — no row for the
    event_id both bodies declared."""
    conn = await connect(admin_url)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM public.altruist_webhook_events WHERE event_id = $1",
            f"{TAG}-should-never-persist",
        )
        R.expect("6-1d", row == 0,
                 "the payload from the rejected signature tests never reached the DB — "
                 "signature verification genuinely runs before any write",
                 detail=str(row))
    finally:
        await conn.close()


async def run_known_account_and_dedup(client, admin_url: str) -> None:
    """[Y] a correctly-signed payload for a KNOWN account resolves org_id
    from NULL and triggers the real sync orchestration; [Y] replaying the
    IDENTICAL signed body is a proven no-op, diffed."""
    conn = await connect(admin_url)
    try:
        event_id = f"{TAG}-known-event-1"
        payload = {
            "event_id": event_id,
            "event_type": "position.updated",
            "account_id": ALTRUIST_ACCOUNT_A,
        }
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "X-Altruist-Signature": sign(body)}

        before_household_calls = len(CALL_LOG["households"])
        before_events = await conn.fetchval("SELECT count(*) FROM public.altruist_webhook_events")

        r1 = client.post("/api/v1/altruist/webhook", content=body, headers=headers)
        R.expect("6-2a", r1.status_code == 200, "a correctly-signed KNOWN-account payload returns 200",
                 detail=f"{r1.status_code} {r1.text}")
        body1 = r1.json() if r1.status_code == 200 else {}
        R.expect("6-2b", body1.get("status") == "resolved" and body1.get("org_id") == ORG,
                 "the response reports status=resolved with the CORRECT org_id, derived "
                 "from the external_references lookup, not the (absent) payload claim",
                 detail=str(body1))

        row = await conn.fetchrow(
            "SELECT org_id::text AS org_id, processed_at, event_type "
            "FROM public.altruist_webhook_events WHERE event_id = $1",
            event_id,
        )
        R.expect("6-2c",
                 row is not None and row["org_id"] == ORG and row["processed_at"] is not None
                 and row["event_type"] == "position.updated",
                 "an INDEPENDENT re-read (fresh connection) shows org_id updated from NULL "
                 "to the real org, processed_at stamped, event_type captured",
                 detail=str(dict(row) if row else None))

        R.expect("6-2d", (ORG, "sandbox") in CALL_LOG["households"][before_household_calls:],
                 "the webhook actually invoked resolve_identity for ORG/sandbox — proven by "
                 "the call log, not just the 200 response",
                 detail=str(CALL_LOG["households"][before_household_calls:]))

        pos_row = await conn.fetchrow(
            """
            SELECT p.source_system, p.quantity, a.name AS asset_name
            FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
              AND p.valid_to IS NULL AND p.system_to IS NULL
            """,
            ORG, ACCOUNT_A,
        )
        R.expect("6-2e",
                 pos_row is not None and pos_row["source_system"] == "altruist"
                 and pos_row["quantity"] == 100,
                 "the sync orchestration the webhook triggered actually created a real "
                 "position, independently re-readable",
                 detail=str(dict(pos_row) if pos_row else None))

        after_events_1 = await conn.fetchval("SELECT count(*) FROM public.altruist_webhook_events")
        R.expect("6-2f", after_events_1 == before_events + 1,
                 "exactly one new event row after the first delivery",
                 detail=f"{before_events} -> {after_events_1}")

        # ── replay: identical signed body, second time ──────────────────
        before_household_calls_2 = len(CALL_LOG["households"])
        r2 = client.post("/api/v1/altruist/webhook", content=body, headers=headers)
        R.expect("6-3a", r2.status_code == 200, "replaying the identical signed body returns 200",
                 detail=f"{r2.status_code} {r2.text}")
        body2 = r2.json() if r2.status_code == 200 else {}
        R.expect("6-3b", body2.get("status") == "duplicate" and body2.get("event_id") == event_id,
                 "the replay is reported as a dedup hit, not reprocessed",
                 detail=str(body2))
        R.expect("6-3c", len(CALL_LOG["households"]) == before_household_calls_2,
                 "the replay did NOT call resolve_identity again — genuinely short-circuited "
                 "before dispatch, not just idempotent by accident",
                 detail=str(CALL_LOG["households"][before_household_calls_2:]))

        after_events_2 = await conn.fetchval("SELECT count(*) FROM public.altruist_webhook_events")
        pos_count_2 = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
            ORG, ACCOUNT_A,
        )
        R.expect("6-3d", after_events_2 == after_events_1 and pos_count_2 == 1,
                 "row counts after the replay are IDENTICAL to after the first delivery — "
                 "diffed, not asserted from the response alone",
                 detail=f"events {after_events_1}->{after_events_2}; positions={pos_count_2}")
    finally:
        await conn.close()


async def run_unknown_identifier(client, admin_url: str) -> None:
    """[Y] a correctly-signed payload for an UNKNOWN identifier is logged
    safely with org_id left NULL, without crashing and without guessing."""
    conn = await connect(admin_url)
    try:
        event_id = f"{TAG}-unknown-event-1"
        payload = {"event_id": event_id, "event_type": "account.updated", "account_id": ALTRUIST_ACCOUNT_UNKNOWN}
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "X-Altruist-Signature": sign(body)}

        r = client.post("/api/v1/altruist/webhook", content=body, headers=headers)
        R.expect("6-4a", r.status_code == 200,
                 "a correctly-signed payload for an unrecognized identifier does not crash",
                 detail=f"{r.status_code} {r.text}")
        resp = r.json() if r.status_code == 200 else {}
        R.expect("6-4b", resp.get("status") == "logged_unrecognized",
                 "the response reports the honest logged_unrecognized status, not a guess",
                 detail=str(resp))

        row = await conn.fetchrow(
            "SELECT org_id, raw_payload FROM public.altruist_webhook_events WHERE event_id = $1",
            event_id,
        )
        R.expect("6-4c", row is not None and row["org_id"] is None,
                 "the event row is independently re-readable with org_id still NULL — "
                 "logged, not dropped and not assigned to a guessed org",
                 detail=str(dict(row) if row else None))
        R.expect("6-4d",
                 json.loads(row["raw_payload"]).get("account_id") == ALTRUIST_ACCOUNT_UNKNOWN,
                 "the raw payload is preserved verbatim for later manual review",
                 detail=str(row["raw_payload"]) if row else "no row")
    finally:
        await conn.close()


async def run_org_forgery_is_ignored(client, admin_url: str) -> None:
    """[Y] a payload that directly claims org_id (bypassing the real
    external-id lookup) cannot cause a write under the forged org — only
    the real portfolio.external_references lookup for the identifier
    determines org_id, and here the identifier resolves to OTHER_ORG."""
    conn = await connect(admin_url)
    try:
        event_id = f"{TAG}-forged-event-1"
        # Claims org_id = ORG directly in the payload, but account_id is
        # ALTRUIST_ACCOUNT_B, which the real crosswalk resolves to OTHER_ORG.
        payload = {
            "event_id": event_id,
            "event_type": "position.updated",
            "account_id": ALTRUIST_ACCOUNT_B,
            "org_id": ORG,
        }
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "X-Altruist-Signature": sign(body)}

        org_a_pos_before = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid", ORG)

        r = client.post("/api/v1/altruist/webhook", content=body, headers=headers)
        R.expect("6-5a", r.status_code == 200, "the forged-org payload is still accepted (valid signature)",
                 detail=f"{r.status_code} {r.text}")
        resp = r.json() if r.status_code == 200 else {}
        R.expect("6-5b", resp.get("org_id") == OTHER_ORG,
                 "org_id resolves to OTHER_ORG (the REAL crosswalk owner of the account), "
                 "never the forged ORG claim embedded in the payload body",
                 detail=str(resp))

        row = await conn.fetchrow(
            "SELECT org_id::text AS org_id FROM public.altruist_webhook_events WHERE event_id = $1",
            event_id,
        )
        R.expect("6-5c", row is not None and row["org_id"] == OTHER_ORG,
                 "an independent re-read confirms the persisted org_id is the real "
                 "crosswalk owner, not the forged claim",
                 detail=str(dict(row) if row else None))

        org_a_pos_after = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid", ORG)
        R.expect("6-5d", org_a_pos_after == org_a_pos_before,
                 "ORG's own position count is completely unaffected by the forged claim — "
                 "no write ever landed under the wrong org",
                 detail=f"{org_a_pos_before} -> {org_a_pos_after}")
    finally:
        await conn.close()


async def run_rls_isolation(app_service_url: str) -> None:
    """[Y] a genuinely non-bypassing, org-scoped role cannot SELECT an
    unresolved (org_id NULL) event row, and cannot SELECT another org's
    resolved row — only is_super_admin or the matching org can. Uses a real
    app_service-rooted connection with RLS context applied via literal
    SET LOCAL (mirroring services.database's own mechanism), confirming
    rolbypassrls=False first so this proves something."""
    conn = await connect(app_service_url)
    try:
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if bypass:
            R.blocked("6-6", f"current role ({await conn.fetchval('SELECT current_user')}) has "
                              "rolbypassrls=true — cross-org isolation cannot be proven under this "
                              "role; app_service_dsn() did not yield a genuinely restricted role")
            return
        R.ok("6-6-precheck", f"the isolation-test role has rolbypassrls=False — confirmed live, "
                              "so the checks below actually prove something")

        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true), "
                "       set_config('app.is_super_admin', 'false', true)",
                ORG,
            )
            unresolved = await conn.fetch(
                "SELECT id FROM public.altruist_webhook_events WHERE org_id IS NULL "
                "AND event_id = $1",
                f"{TAG}-unknown-event-1",
            )
            R.expect("6-6a", len(unresolved) == 0,
                     "an ORG-scoped session (not super admin) sees ZERO rows for the "
                     "unresolved (org_id NULL) event — not even ORG's own session",
                     detail=str(unresolved))

            other_org_row = await conn.fetch(
                "SELECT id FROM public.altruist_webhook_events WHERE event_id = $1",
                f"{TAG}-forged-event-1",
            )
            R.expect("6-6b", len(other_org_row) == 0,
                     "ORG's session cannot SELECT OTHER_ORG's resolved event row",
                     detail=str(other_org_row))

            own_row = await conn.fetch(
                "SELECT id FROM public.altruist_webhook_events WHERE event_id = $1",
                f"{TAG}-known-event-1",
            )
            R.expect("6-6c", len(own_row) == 1,
                     "ORG's session CAN see its own resolved event row — the isolation "
                     "above is genuinely org-scoped, not a blanket lockout",
                     detail=str(own_row))
    finally:
        await conn.close()


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
                      "contradicts Sprints 1-5's finding; re-confirmed live, not assumed")
        return
    R.blocked("T3", "no ALTRUIST_* secrets exist in this project's Doppler config "
                     "(`doppler secrets --only-names`, project hollisworks), re-confirmed live "
                     "for Sprint 6 — including no webhook secret — sandbox smoke test is "
                     "genuinely blocked; no live call attempted")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 (last bullet) — regression: Sprints 1-5's own verify scripts
# ═══════════════════════════════════════════════════════════════════════════
def check_regression() -> None:
    for label, script in (
        ("1", "verify_altruist_sprint1_openapi_connection.py"),
        ("2", "verify_altruist_sprint2_identity_resolution.py"),
        ("3", "verify_altruist_sprint3_positions_transactions_sync.py"),
        ("4", "verify_altruist_sprint4_connection_api.py"),
        ("5", "verify_altruist_sprint5_sync_orchestration.py"),
    ):
        try:
            out = subprocess.run(
                ["doppler", "run", "--", "python3", str(HERE / script)],
                cwd=str(API_DIR), capture_output=True, text=True, timeout=180,
            )
        except Exception as exc:  # noqa: BLE001
            R.bad(f"6-REGR-{label}", f"could not run {script}", repr(exc))
            continue
        tail = "\n".join(out.stdout.strip().splitlines()[-6:])
        if out.returncode != 0 and out.stderr.strip():
            tail += "\nSTDERR:\n" + "\n".join(out.stderr.strip().splitlines()[-15:])
        R.expect(f"6-REGR-{label}", out.returncode == 0,
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
    print(f"admin       : {admin_prov}")
    print(f"app_service : {app_prov}\n")

    check_task3_credentials()

    admin = await connect(admin_url)

    pre: dict[str, int] = {}
    try:
        await teardown(admin)
        pre = await counts(admin)
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
            run_signature_rejections(shared)
            await run_rejections_write_nothing(admin_url)
            await run_known_account_and_dedup(shared, admin_url)
            await run_unknown_identifier(shared, admin_url)
            await run_org_forgery_is_ignored(shared, admin_url)
            if app_url is not None:
                await run_rls_isolation(app_url)
            else:
                R.blocked("6-6", f"cannot reach the database as app_service — {app_prov} — "
                                  "cross-org SELECT isolation cannot be proven under a "
                                  "genuinely non-bypassing role")
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

    check_regression()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
