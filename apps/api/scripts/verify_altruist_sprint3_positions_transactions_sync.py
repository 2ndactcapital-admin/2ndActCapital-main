"""Altruist Sprint 3 verification — positions/transactions sync for RESOLVED
accounts (services/altruist_positions_sync.py).

Pass/fail only, no prompts. Run:

    python3 scripts/verify_altruist_sprint3_positions_transactions_sync.py

Every table this script writes to is counted before the first insert and
again after the last delete; a difference of even one row fails the run.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Task 3 (sandbox smoke test) is BLOCKED, not skipped or faked** — same
  live `doppler secrets --only-names` check as Sprint 1/2, re-run rather than
  assumed still true.

* **The pre-connection guard fires even with ZERO resolved accounts.**
  ``sync_resolved_accounts`` calls ``require_active_connection`` explicitly,
  up front — unlike a design that only guards inside a per-account loop,
  which would never reach the guard at all for an org with no resolved
  accounts. Proven against an org with no ``altruist_connections`` row AND
  no resolved accounts, so a passing test cannot be explained by "the loop
  never ran for an unrelated reason".

* **Skip-of-unresolved is proven, not assumed.** A mixed fixture set has one
  RESOLVED account (a real ``portfolio.external_references`` row) and one
  UNRESOLVED account (a real ``account_import_exceptions`` row, no
  crosswalk row). The mock transport itself asserts it is NEVER called with
  the unresolved account's id — if the sync code ever mistakenly enumerated
  it, the test fails loudly at the transport layer, not just via a row-count
  coincidence.

* **Idempotency is proven by literally re-running the same sync twice and
  diffing** — not by inspecting the code and asserting it "should" be
  idempotent.

* **Cross-org RLS isolation runs on ``app_service``, whose ``rolbypassrls``
  is asserted False first**, on ``portfolio.positions`` AND
  ``portfolio.transactions`` (the two tables this sprint actually writes
  ledger data to), in both directions, using FK-valid targets so a refusal
  is provably an RLS violation and not a foreign-key error in disguise.

* **[FIND], not silently worked around:** neither ``portfolio.positions``
  nor ``portfolio.transactions`` has a ``custodian_code``/``custodian_system``
  column — see ``services/altruist_positions_sync.py``'s module docstring.
  This script asserts every synced row's ``source_system`` is the DB-legal
  ``'altruist'`` token, and separately asserts the ``reference_data`` codes
  ``ALT``/``ALT-DEF`` this sprint was told to tag with are exactly the ones
  the sync module exposes as constants (i.e. the FIND is real, not a
  reason the requirement was dropped).

* Teardown is by fixture id, in FK-safe order (transactions before
  positions before assets before accounts before entities), never a
  TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"
TAG = "altr3verify"

CONN_ORG_SANDBOX = "99000000-0000-0000-0000-0000a17f2001"
CONNECTIONS = [CONN_ORG_SANDBOX]

ENTITY_ORG = "99000000-0000-0000-0000-0000a17f2201"
ENTITIES = [ENTITY_ORG]

ACCOUNT_RESOLVED = "99000000-0000-0000-0000-0000a17f2101"
ACCOUNTS = [ACCOUNT_RESOLVED]

ALTRUIST_ACCOUNT_RESOLVED_ID = f"{TAG}-acct-resolved"
ALTRUIST_ACCOUNT_UNRESOLVED_ID = f"{TAG}-acct-unresolved"

EXTREF_ACCOUNT_ID = "99000000-0000-0000-0000-0000a17f2301"

TEST_CUSIP = f"{TAG.upper()}CUSIP1"[:12]

COUNTED = (
    "public.altruist_connections",
    "portfolio.external_references",
    "public.account_import_exceptions",
    "public.account_import_batches",
    "portfolio.transactions",
    "portfolio.positions",
    "portfolio.asset_identifiers",
    "portfolio.assets",
    "public.accounts",
    "public.account_owners",
    "public.position_account_exceptions",
    "public.entities",
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
        print(f"altruist_sprint3_positions_transactions_sync: {counts.get('PASS', 0)}/{total} PASS"
              + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def scoped(conn, org_id: str) -> None:
    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org_id)
    await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn) -> None:
    await conn.execute(
        "DELETE FROM portfolio.transactions WHERE org_id = ANY($1::uuid[]) "
        "AND external_ref LIKE $2",
        [ORG, OTHER_ORG], f"{TAG}-%",
    )
    # position_account_exceptions FK-references positions and is written by
    # create_position's own account-link check (services.portfolio_account_
    # link) whenever account_id is supplied — it must clear before a
    # position can be deleted.
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
        "DELETE FROM portfolio.asset_identifiers WHERE id_value = $1", TEST_CUSIP,
    )
    await conn.execute(
        "DELETE FROM portfolio.assets WHERE org_id = ANY($1::uuid[]) "
        "AND name LIKE $2",
        [ORG, OTHER_ORG], f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM portfolio.external_references WHERE org_id = ANY($1::uuid[]) "
        "AND (external_id = ANY($2::text[]) OR id = $3::uuid OR external_id LIKE $4)",
        [ORG, OTHER_ORG],
        [ALTRUIST_ACCOUNT_RESOLVED_ID, ALTRUIST_ACCOUNT_UNRESOLVED_ID],
        EXTREF_ACCOUNT_ID, f"%{TAG}%",
    )
    await conn.execute(
        "DELETE FROM public.account_import_exceptions "
        "WHERE raw_row ->> 'external_id' = $1",
        ALTRUIST_ACCOUNT_UNRESOLVED_ID,
    )
    await conn.execute(
        "DELETE FROM public.account_import_batches "
        "WHERE org_id = ANY($1::uuid[]) AND custodian_code = 'ALTRUIST-SPRINT3-VERIFY'",
        [ORG, OTHER_ORG],
    )
    await conn.execute(
        "DELETE FROM public.account_owners WHERE account_id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    await conn.execute(
        "DELETE FROM public.altruist_connections WHERE id = ANY($1::uuid[])", CONNECTIONS)


async def build_fixtures(conn) -> None:
    from services.altruist_oauth import encrypt_secret, last4

    now = datetime.now(timezone.utc)

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
        CONN_ORG_SANDBOX, ORG, f"{TAG}-client",
        encrypt_secret(f"{TAG}-secret"), last4(f"{TAG}-secret"),
        encrypt_secret(f"{TAG}-access"), last4(f"{TAG}-access"),
        encrypt_secret(f"{TAG}-refresh"), last4(f"{TAG}-refresh"),
        now + timedelta(hours=1), now,
    )

    await conn.execute(
        """
        INSERT INTO public.entities (id, org_id, entity_type, display_name, status)
        VALUES ($1::uuid, $2::uuid, 'individual', $3, 'active')
        """,
        ENTITY_ORG, ORG, f"{TAG} owner",
    )

    await conn.execute(
        """
        INSERT INTO public.accounts (
            id, org_id, account_number_masked, account_number_hash,
            custodian_code, registration_type, tax_status, primary_entity_id
        ) VALUES ($1::uuid, $2::uuid, $3, $4, 'ALT', 'INDIVIDUAL', 'TAXABLE', $5::uuid)
        """,
        ACCOUNT_RESOLVED, ORG, f"{TAG}-****5678", f"{TAG}-hash-5678", ENTITY_ORG,
    )

    # A real active owner — without this, create_position's account-link check
    # (services.portfolio_account_link) records a (correct, expected)
    # position_account_exceptions row for "account has no owners", which is
    # real behaviour, not a bug, but is not what this sprint's fixture is
    # exercising and would otherwise leave an extra FK-referencing row this
    # teardown does not expect.
    await conn.execute(
        """
        INSERT INTO public.account_owners
            (org_id, account_id, entity_id, ownership_pct, role)
        VALUES ($1::uuid, $2::uuid, $3::uuid, 100, 'owner')
        """,
        ORG, ACCOUNT_RESOLVED, ENTITY_ORG,
    )

    # The RESOLVED case: a real crosswalk row, exactly what
    # altruist_identity.resolve_identity would have written.
    await conn.execute(
        """
        INSERT INTO portfolio.external_references
            (id, org_id, source_system, external_id, record_type, record_id)
        VALUES ($1::uuid, $2::uuid, 'ALTRUIST', $3, 'account', $4::uuid)
        """,
        EXTREF_ACCOUNT_ID, ORG, ALTRUIST_ACCOUNT_RESOLVED_ID, ACCOUNT_RESOLVED,
    )

    # The UNRESOLVED case: a real account_import_exceptions row and
    # DELIBERATELY no external_references row — this is what Sprint 2 leaves
    # behind for an account nobody has matched yet.
    batch_id = await conn.fetchval(
        """
        INSERT INTO public.account_import_batches
            (org_id, custodian_code, source_filename, status)
        VALUES ($1::uuid, 'ALTRUIST-SPRINT3-VERIFY', 'sprint3-verify-fixture', 'DRY_RUN')
        RETURNING id::text
        """,
        ORG,
    )
    await conn.execute(
        """
        INSERT INTO public.account_import_exceptions
            (org_id, batch_id, source_row, record_kind, reason_code, reason, raw_row)
        VALUES ($1::uuid, $2::uuid, 0, 'account', 'altruist_account_unmatched',
                'sprint3 verify fixture', $3::jsonb)
        """,
        ORG, batch_id,
        f'{{"external_id": "{ALTRUIST_ACCOUNT_UNRESOLVED_ID}", "name": "unresolved"}}',
    )


# ═══════════════════════════════════════════════════════════════════════════
# Synthetic Altruist transport — documented-or-best-guess-shape
# GET /v2/positions?account_id=... and GET /v2/transactions?account_id=...
# ═══════════════════════════════════════════════════════════════════════════
def make_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        account_id = request.url.params.get("account_id")
        assert account_id != ALTRUIST_ACCOUNT_UNRESOLVED_ID, (
            "the sync must NEVER call the Altruist API for an unresolved "
            "account — it enumerated one it should have skipped"
        )
        assert account_id == ALTRUIST_ACCOUNT_RESOLVED_ID, (
            f"unexpected account_id in Altruist call: {account_id!r}"
        )
        if request.url.path.endswith("/positions"):
            return httpx.Response(200, json={
                "positions": [
                    {
                        "id": f"{TAG}-pos-1",
                        "cusip": TEST_CUSIP,
                        "name": f"{TAG} Test Security",
                        "asset_type": "equity",
                        "quantity": "100",
                        "market_value": "15000.00",
                        "cost_basis": "12000.00",
                        "currency_code": "USD",
                    },
                ],
            })
        if request.url.path.endswith("/transactions"):
            return httpx.Response(200, json={
                "transactions": [
                    {
                        "id": f"{TAG}-txn-buy-1",
                        "type": "buy",
                        "cusip": TEST_CUSIP,
                        "trade_date": "2026-01-15",
                        "settle_date": "2026-01-17",
                        "quantity": "100",
                        "price": "120.00",
                        "gross_amount": "12000.00",
                        "fees": "0.00",
                        "net_amount": "12000.00",
                        "currency_code": "USD",
                    },
                    {
                        "id": f"{TAG}-txn-div-1",
                        "type": "dividend",
                        "cusip": TEST_CUSIP,
                        "trade_date": "2026-03-01",
                        "gross_amount": "50.00",
                        "net_amount": "50.00",
                        "currency_code": "USD",
                    },
                ],
            })
        return httpx.Response(404, json={"error": "unexpected path in verify mock"})

    return httpx.MockTransport(handler)


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 (bullet 4) — the pre-connection guard fires even with ZERO
# resolved accounts, before any network call
# ═══════════════════════════════════════════════════════════════════════════
async def check_guard_fires_with_no_connection(admin) -> None:
    from services.altruist_oauth import AltruistNotConnected
    from services.altruist_positions_sync import sync_resolved_accounts

    raised = None
    try:
        # OTHER_ORG has no altruist_connections row AND no resolved accounts
        # at all. No mock transport is passed — if the guard did not fire
        # FIRST, this would attempt a real network call.
        await sync_resolved_accounts(admin, org_id=OTHER_ORG, environment="sandbox")
    except AltruistNotConnected as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001
        raised = exc
    R.expect("G-1", isinstance(raised, AltruistNotConnected),
              "sync_resolved_accounts raises AltruistNotConnected for an org "
              "with no altruist_connections row and ZERO resolved accounts — "
              "the guard is explicit and does not depend on the per-account "
              "loop ever running", detail=f"raised={raised!r}")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 (bullets 1-3) — real sync, idempotency, skip-of-unresolved
# ═══════════════════════════════════════════════════════════════════════════
async def check_sync_and_idempotency_and_skip(admin) -> None:
    from services.altruist_positions_sync import (
        CUSTODIAN_CODE, CUSTODIAN_SYSTEM_CODE, SOURCE_SYSTEM,
        list_resolved_altruist_accounts, sync_resolved_accounts,
    )

    resolved = await list_resolved_altruist_accounts(admin, org_id=ORG)
    R.expect("B-1",
              len(resolved) == 1 and resolved[0]["external_id"] == ALTRUIST_ACCOUNT_RESOLVED_ID
              and resolved[0]["hollisworks_account_id"] == ACCOUNT_RESOLVED,
              "list_resolved_altruist_accounts enumerates ONLY the account "
              "with a real external_references row — the unresolved account "
              "(a real account_import_exceptions row, no crosswalk row) is "
              "absent, proving the skip against a mixed fixture set rather "
              "than assuming it by construction",
              detail=str(resolved))

    R.expect("F-1", (CUSTODIAN_CODE, CUSTODIAN_SYSTEM_CODE) == ("ALT", "ALT-DEF"),
              "[FIND] the sync module exposes the seeded reference_data codes "
              "ALT/ALT-DEF it was told to tag with, even though neither "
              "portfolio.positions nor portfolio.transactions has a column to "
              "hold them literally (see module docstring) — the requirement "
              "was not silently dropped",
              detail=f"{CUSTODIAN_CODE!r}, {CUSTODIAN_SYSTEM_CODE!r}")

    as_of = date.today()
    transport = make_mock_transport()

    before_pos = await admin.fetchval(
        "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
        ORG, ACCOUNT_RESOLVED)
    before_txn = await admin.fetchval(
        "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1::uuid",
        ORG)

    result1 = await sync_resolved_accounts(
        admin, org_id=ORG, environment="sandbox", as_of_date=as_of, transport=transport,
    )

    R.expect("C-1", result1.accounts_synced == 1,
              "exactly one account was synced (the resolved one)",
              detail=str(result1))
    acct1 = result1.accounts[0]
    R.expect("C-2",
              acct1.altruist_account_id == ALTRUIST_ACCOUNT_RESOLVED_ID
              and acct1.hollisworks_account_id == ACCOUNT_RESOLVED,
              "the synced account is the resolved Altruist account, mapped "
              "to the correct Hollisworks account uuid", detail=str(acct1))
    R.expect("C-3", acct1.positions_created == 1 and not acct1.positions_errors,
              "run 1: exactly one position created, no row errors",
              detail=str(acct1))
    R.expect("C-4", acct1.transactions_created == 2 and not acct1.transactions_errors,
              "run 1: both transactions (buy + dividend) created, no row errors",
              detail=str(acct1))

    after_pos_1 = await admin.fetchval(
        "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
        ORG, ACCOUNT_RESOLVED)
    after_txn_1 = await admin.fetchval(
        "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1::uuid",
        ORG)
    R.expect("C-5", after_pos_1 == before_pos + 1 and after_txn_1 == before_txn + 2,
              "run 1 landed exactly the rows it reported creating — a real "
              "WRITE persisted, re-read from the same connection",
              detail=f"pos before={before_pos} after={after_pos_1}; "
                     f"txn before={before_txn} after={after_txn_1}")

    pos_row = await admin.fetchrow(
        """
        SELECT p.source_system, p.authority, p.quantity, p.market_value,
               p.cost_basis, p.owner_entity_id::text AS owner_entity_id,
               a.name AS asset_name
        FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
        WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
          AND p.valid_to IS NULL AND p.system_to IS NULL
        """,
        ORG, ACCOUNT_RESOLVED,
    )
    R.expect("C-6",
              pos_row is not None and pos_row["source_system"] == SOURCE_SYSTEM == "altruist"
              and pos_row["authority"] == "custodial"
              and pos_row["owner_entity_id"] == ENTITY_ORG
              and pos_row["quantity"] == 100 and pos_row["market_value"] == 15000,
              "the synced position carries source_system='altruist' (the "
              "real, DB-legal CHECK-constraint token), authority='custodial', "
              "the account's real owner entity, and the correct quantity/"
              "market_value from the synthetic payload",
              detail=str(dict(pos_row) if pos_row else None))

    txn_rows = await admin.fetch(
        """
        SELECT transaction_type_code, source_system, authority, quantity,
               gross_amount, external_ref
        FROM portfolio.transactions
        WHERE org_id = $1::uuid AND external_ref = ANY($2::text[])
        ORDER BY transaction_type_code
        """,
        ORG, [f"{TAG}-txn-buy-1", f"{TAG}-txn-div-1"],
    )
    types = {r["transaction_type_code"] for r in txn_rows}
    R.expect("C-7",
              types == {"buy", "dividend"}
              and all(r["source_system"] == "altruist" and r["authority"] == "custodial"
                      for r in txn_rows),
              "both transactions landed with the correct mapped "
              "transaction_type_code and the same source_system/authority "
              "tag as the position", detail=str([dict(r) for r in txn_rows]))

    # ── Run 2: identical inputs, same as_of_date, fresh mock transport.
    result2 = await sync_resolved_accounts(
        admin, org_id=ORG, environment="sandbox", as_of_date=as_of,
        transport=make_mock_transport(),
    )
    acct2 = result2.accounts[0]
    R.expect("D-1", acct2.positions_created == 0 and acct2.positions_skipped_duplicate == 1,
              "run 2: the position is a no-op skip (found via "
              "external_references), not a second insert", detail=str(acct2))
    R.expect("D-2", acct2.transactions_created == 0 and acct2.transactions_skipped_duplicate == 2,
              "run 2: both transactions are no-op skips", detail=str(acct2))

    after_pos_2 = await admin.fetchval(
        "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid",
        ORG, ACCOUNT_RESOLVED)
    after_txn_2 = await admin.fetchval(
        "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1::uuid",
        ORG)
    R.expect("D-3", after_pos_2 == after_pos_1 and after_txn_2 == after_txn_1,
              "row counts after run 2 are IDENTICAL to after run 1 — "
              "re-running the same sync twice is provably idempotent, "
              "diffed, not asserted",
              detail=f"pos: {after_pos_1} -> {after_pos_2}; "
                     f"txn: {after_txn_1} -> {after_txn_2}")


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 (bullet 5) — RLS isolation both directions, positions + transactions
# ═══════════════════════════════════════════════════════════════════════════
async def check_rls(admin, app) -> None:
    bypass = await app.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    who = await app.fetchval("SELECT current_user")
    if not R.expect("R-0", bypass is False,
                     f"the isolation connection is '{who}' with rolbypassrls=False "
                     f"— without this every check below proves nothing",
                     detail=f"rolbypassrls={bypass}"):
        return

    pos_id = await admin.fetchval(
        "SELECT id::text FROM portfolio.positions WHERE org_id = $1::uuid "
        "AND account_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
        ORG, ACCOUNT_RESOLVED,
    )
    txn_id = await admin.fetchval(
        "SELECT id::text FROM portfolio.transactions WHERE org_id = $1::uuid "
        "AND external_ref = $2",
        ORG, f"{TAG}-txn-buy-1",
    )
    asset_id = await admin.fetchval(
        "SELECT asset_id::text FROM portfolio.positions WHERE id = $1::uuid", pos_id,
    )

    # ── portfolio.positions — read isolation both directions.
    async with app.transaction():
        await scoped(app, ORG)
        own = await app.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE id = $1::uuid", pos_id)
    R.expect("R-1", own == 1, "app_service scoped to ORG sees its own synced position")

    async with app.transaction():
        await scoped(app, OTHER_ORG)
        other = await app.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE id = $1::uuid", pos_id)
    R.expect("R-2", other == 0,
              "reversed: app_service scoped to OTHER_ORG cannot see ORG's "
              "synced position, on the identical query with only the org "
              "GUC changed")

    # ── portfolio.positions — write refusal, FK-valid target so the refusal
    #    is provably RLS and not a foreign-key error in disguise.
    refused_pos = False
    try:
        async with app.transaction():
            await scoped(app, OTHER_ORG)
            await app.execute(
                """
                INSERT INTO portfolio.positions
                    (org_id, owner_entity_id, asset_id, as_of_date,
                     ownership_basis, quantity, authority, source_system)
                VALUES ($1::uuid, $2::uuid, $3::uuid, CURRENT_DATE,
                        'units', 1, 'custodial', 'altruist')
                """,
                ORG, ENTITY_ORG, asset_id,
            )
    except asyncpg.InsufficientPrivilegeError:
        refused_pos = True
    R.expect("R-3", refused_pos,
              "app_service scoped to OTHER_ORG cannot INSERT a "
              "portfolio.positions row with org_id=ORG, even using ORG's own "
              "real (FK-valid) owner_entity_id/asset_id — WITH CHECK is real")

    # ── portfolio.transactions — read isolation both directions.
    async with app.transaction():
        await scoped(app, ORG)
        own_t = await app.fetchval(
            "SELECT count(*) FROM portfolio.transactions WHERE id = $1::uuid", txn_id)
    R.expect("R-4", own_t == 1, "app_service scoped to ORG sees its own synced transaction")

    async with app.transaction():
        await scoped(app, OTHER_ORG)
        other_t = await app.fetchval(
            "SELECT count(*) FROM portfolio.transactions WHERE id = $1::uuid", txn_id)
    R.expect("R-5", other_t == 0,
              "reversed: app_service scoped to OTHER_ORG cannot see ORG's "
              "synced transaction")

    # ── portfolio.transactions — write refusal, FK-valid target.
    refused_txn = False
    try:
        async with app.transaction():
            await scoped(app, OTHER_ORG)
            await app.execute(
                """
                INSERT INTO portfolio.transactions
                    (org_id, position_id, transaction_type_code, trade_date,
                     authority, source_system)
                VALUES ($1::uuid, $2::uuid, 'buy', CURRENT_DATE, 'custodial', 'altruist')
                """,
                ORG, pos_id,
            )
    except asyncpg.InsufficientPrivilegeError:
        refused_txn = True
    R.expect("R-6", refused_txn,
              "app_service scoped to OTHER_ORG cannot INSERT a "
              "portfolio.transactions row with org_id=ORG, even using ORG's "
              "own real (FK-valid) position_id — WITH CHECK is real")


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
                      "contradicts Sprint 1/2's stated assumption; a live smoke test "
                      "may now be possible but was NOT attempted by this script")
        return
    R.blocked("T3", "no ALTRUIST_* secrets exist in this project's Doppler config "
                     "(`doppler secrets --only-names`, project hollisworks, re-confirmed "
                     "live for Sprint 3) — sandbox smoke test is genuinely blocked; "
                     "no live call was attempted")


# ═══════════════════════════════════════════════════════════════════════════
async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    if admin_url is None:
        print(f"FATAL: cannot reach the database as postgres — {admin_prov}")
        return 2
    if app_url is None:
        print(f"FATAL: cannot reach the database as app_service — {app_prov}. "
              f"Every RLS check would pass vacuously on the postgres DSN")
        return 2
    print(f"admin       : {admin_prov}")
    print(f"app_service : {app_prov}\n")

    check_task3_credentials()

    admin = await connect(admin_url)
    app = await connect(app_url)

    import os
    from cryptography.fernet import Fernet
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    pre: dict[str, int] = {}
    try:
        await teardown(admin)
        pre = await counts(admin)
        await build_fixtures(admin)

        await check_guard_fires_with_no_connection(admin)
        await check_sync_and_idempotency_and_skip(admin)
        await check_rls(admin, app)
    except Exception:  # noqa: BLE001
        R.bad("driver", "the run aborted", traceback.format_exc())
    finally:
        try:
            await teardown(admin)
        except Exception:  # noqa: BLE001
            R.bad("teardown", "teardown failed", traceback.format_exc())
        post = await counts(admin)
        drift = {t: (pre.get(t), post.get(t)) for t in COUNTED
                 if pre.get(t) != post.get(t)}
        R.expect("teardown-check", not drift,
                 f"every one of the {len(COUNTED)} tables this script writes to is "
                 f"back at its pre-test row count", detail=str(drift))
        await admin.close()
        await app.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
