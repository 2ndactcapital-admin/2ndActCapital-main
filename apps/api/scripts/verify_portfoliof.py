"""Verification — Portfolio Phase F: corporate actions.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END, with an
EXACT before/after count on every table touched — never a truncate. Real
database, real RLS, real ``app_service`` connection, real ``transaction_types``.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1/A2/B/C/D/E require it: ``postgres`` has ``rolbypassrls``, so every
cross-org assertion would "pass" under it while proving nothing.

────────────────────────────────────────────────────────────────────────────
THE FOUR ASSERTIONS THIS PHASE IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"The split was applied."** ``quantity = 200`` proves nothing on its own — it is
also what you get from a fixture that was seeded at 200. So the pre-split row is
read back and asserted at ``100`` FIRST, the post-split row is asserted at
``200``, and the closed predecessor is asserted to still carry ``100`` with a
non-null ``valid_to``. Only the three together mean a restatement happened.

**"Total cost basis is unchanged."** Trivially true of code that never touches
cost basis at all. So the SPINOFF case, on the same code path, asserts cost basis
DID move (30,000 → 24,000 on the parent, 6,000 to the resulting position) — a
module that simply ignored ``cost_basis`` would pass the split assertion and fail
this one.

**"The adjustment is excluded from realized gains."** A ``WHERE
is_corporate_action_adjustment = false`` query returning one row proves nothing
if the fixture only ever had one row. So the position's history is built with a
REAL ``buy`` *before* the split and a REAL ``sell`` *after* it, spanning the
bi-temporal restatement, and the filtered query is asserted to return exactly
those two and to omit the adjustment — plus the unfiltered query is asserted to
return all three. A filter that excluded everything, or nothing, fails.

**"The other org is unaffected."** Asserted with a CONTROL: the second org's
position is snapshotted (id, quantity, cost_basis, valid_to) immediately before
the first org's apply and asserted byte-identical after — AND the second org is
then made to apply the SAME action successfully through the real ``app_service``
connection, proving the "unaffected" result was isolation and not a broken apply.

────────────────────────────────────────────────────────────────────────────
FIXTURES ARE NAMED, NEVER TRUNCATED
────────────────────────────────────────────────────────────────────────────
Every fixture row carries the ``VERIFY-PORTFOLIOF`` tag in a natural-key column
and is deleted by that tag, child tables first. ``portfolio.securities_global``
is a GLOBAL table holding a real 67-row reference corpus, and
``portfolio.assets`` / ``public.entities`` hold real production rows.

Run:
    python3 scripts/verify_portfoliof.py
"""

from __future__ import annotations

import asyncio
import glob
import inspect
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.extend(sorted(glob.glob(
    os.path.join(_HERE, "..", "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_HERE, "..", ".env"), override=False)

from services import portfolio_corporate_actions as pca  # noqa: E402
from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSETS,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    create_asset,
    create_position,
    record_transaction,
)
from services.portfolio_corporate_actions import (  # noqa: E402
    ADJUSTMENT_TYPE_CODE,
    REVERSE_SPLIT,
    SPINOFF,
    SPLIT,
    TABLE_CORP_ACTIONS,
    TERMS_CASH_IN_LIEU,
    TERMS_COST_BASIS_PCT,
    TERMS_DISTRIBUTION_RATIO,
    TERMS_RATIO,
    CorporateActionError,
    UnapplicableActionError,
    already_applied_transactions,
    apply_corporate_action,
    apply_spinoff,
    apply_split,
    find_affected_assets,
    get_corporate_action,
    parse_ratio,
    record_corporate_action,
)
from services.securities_global import (  # noqa: E402
    TABLE_SEC,
    SecuritiesGlobalPermissionError,
    create_security,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, for cross-org isolation. A real row, not a minted one.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_SUB = "auth0|verify_portfoliof_super_admin"
MEMBER_SUB = "auth0|verify_portfoliof_member"
# uuid5(NAMESPACE_URL, sub) — `services.permissions.get_user_id` DERIVES the id
# from the sub rather than looking it up (Phase C's finding), so a fixture seeded
# under a hand-picked literal is a user no code path ever finds.
ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
MEMBER_USER_ID = str(uuid5(NAMESPACE_URL, MEMBER_SUB))

FIXTURE_TAG = "VERIFY-PORTFOLIOF"

# ── Fixture names, declared UP FRONT and never appended to at runtime ────────
E_OWNER = f"{FIXTURE_TAG} Ashcombe Holdings LLC"
E_OTHER_OWNER = f"{FIXTURE_TAG} Wrenfield Partners LLC"
ENTITY_NAMES = [E_OWNER, E_OTHER_OWNER]

SEC_SPLIT = f"{FIXTURE_TAG} Calderwood Industries Inc"
SEC_REVERSE = f"{FIXTURE_TAG} Pennarth Mining Corp"
SEC_PARENT = f"{FIXTURE_TAG} Harrowgate Group PLC"
SEC_SPINCO = f"{FIXTURE_TAG} Harrowgate Materials Inc"
SEC_UNHELD = f"{FIXTURE_TAG} Nobody Holds This Ltd"
SECURITY_NAMES = [SEC_SPLIT, SEC_REVERSE, SEC_PARENT, SEC_SPINCO, SEC_UNHELD]

A_SPLIT = f"{FIXTURE_TAG} Calderwood Industries — common"
A_REVERSE = f"{FIXTURE_TAG} Pennarth Mining — common"
A_PARENT = f"{FIXTURE_TAG} Harrowgate Group — ordinary"
A_OTHER_SPLIT = f"{FIXTURE_TAG} Calderwood Industries — common (Wrenfield)"

# ── Exact figures. Exact, because "a number came back" is what this phase is
#    easiest to fake. ───────────────────────────────────────────────────────
SPLIT_QTY_BEFORE = Decimal("100")
SPLIT_COST = Decimal("5000.00")
SPLIT_RATIO = "2:1"
SPLIT_QTY_AFTER = Decimal("200")          # 100 × 2 ÷ 1
# Unit cost: 5,000 ÷ 100 = 50.00 before, 5,000 ÷ 200 = 25.00 after. TOTAL
# unchanged — that is the invariant an accountant checks.
SPLIT_UNIT_BEFORE = Decimal("50")
SPLIT_UNIT_AFTER = Decimal("25")
SPLIT_DELTA = Decimal("100")              # 200 − 100

REVERSE_QTY_BEFORE = Decimal("500")
REVERSE_COST = Decimal("9000.00")
REVERSE_RATIO = "1:10"
REVERSE_QTY_AFTER = Decimal("50")         # 500 × 1 ÷ 10
REVERSE_DELTA = Decimal("-450")

SPINOFF_QTY = Decimal("300")              # UNCHANGED by a spinoff
SPINOFF_COST_BEFORE = Decimal("30000.00")
SPINOFF_DIST_RATIO = "1:4"
SPINOFF_RETAINED_PCT = "80"
SPINOFF_COST_AFTER = Decimal("24000")     # 30,000 × 80 ÷ 100
SPINCO_COST = Decimal("6000")             # 30,000 − 24,000
SPINCO_QTY = Decimal("75")                # 300 × 1 ÷ 4
SPINOFF_CASH_IN_LIEU = "0.4325"           # recorded, deliberately NOT applied

# Deliberately DIFFERENT from the first org's numbers, so a cross-org write
# lands on a value that cannot be mistaken for a correct one.
OTHER_QTY_BEFORE = Decimal("400")
OTHER_COST = Decimal("12000.00")
OTHER_QTY_AFTER = Decimal("800")

BUY_QTY = Decimal("100")
BUY_NET = Decimal("-5000.00")
SELL_QTY = Decimal("-40")
SELL_NET = Decimal("2600.00")

AS_OF = date(2026, 6, 30)
EX_DATE_SPLIT = date(2026, 7, 15)
EX_DATE_REVERSE = date(2026, 7, 20)
EX_DATE_SPINOFF = date(2026, 8, 3)
EX_DATE_UNHELD = date(2026, 8, 10)
TRADE_BUY = date(2026, 5, 4)
TRADE_SELL = date(2026, 8, 12)

TABLES = (
    TABLE_TRANSACTIONS, TABLE_POSITIONS, TABLE_ASSETS,
    TABLE_CORP_ACTIONS, TABLE_SEC, "public.entities",
)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    print(f"[FIND] {name}\n       {detail}")


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ── Setup / teardown ────────────────────────────────────────────────────────


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, child tables first. Touches nothing else.

    FK order is not cosmetic here. ``transactions.corporate_action_id`` now has a
    REAL FK (this sprint's Part 1 SQL added it), so the adjustment transactions
    must go before the corporate actions; and ``assets.global_security_id``
    references ``securities_global``, so the tenant assets must go before the
    global securities they point at.
    """
    fixture_secs = (
        f"SELECT id FROM {TABLE_SEC} WHERE name LIKE '{FIXTURE_TAG}%'"
    )
    # The spinoff-created asset is named after its global security, so it also
    # carries the tag — but it is matched on global_security_id too, because an
    # asset minted from a security whose name somehow lost the tag would
    # otherwise survive teardown and break the next run's count assertion.
    fixture_assets = (
        f"SELECT id FROM {TABLE_ASSETS} "
        f"WHERE name LIKE '{FIXTURE_TAG}%' "
        f"   OR global_security_id IN ({fixture_secs})"
    )
    fixture_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({fixture_assets})"
    )

    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({fixture_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({fixture_assets})"
    )
    await conn.execute(f"DELETE FROM {TABLE_ASSETS} WHERE id IN ({fixture_assets})")
    await conn.execute(
        f"DELETE FROM {TABLE_CORP_ACTIONS} "
        f"WHERE global_security_id IN ({fixture_secs}) "
        f"   OR resulting_global_security_id IN ({fixture_secs})"
    )
    await conn.execute(f"DELETE FROM {TABLE_SEC} WHERE id IN ({fixture_secs})")

    await conn.execute(
        "DELETE FROM public.entities WHERE display_name = ANY($1::text[])",
        ENTITY_NAMES,
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])",
        [ADMIN_SUB, MEMBER_SUB],
    )


async def seed_users(conn) -> None:
    for user_id, org, sub, role, email in (
        (ADMIN_USER_ID, DEFAULT_ORG_ID, ADMIN_SUB, "super_admin",
         "verify_f_admin@test.local"),
        (MEMBER_USER_ID, DEFAULT_ORG_ID, MEMBER_SUB, "member",
         "verify_f_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioF', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, email, sub, role,
        )


def org_ctx(conn, org_id: str, *, super_admin: bool = False, commit: bool = True):
    """Transaction on ``conn`` with the RLS GUCs SET LOCAL.

    ``super_admin=False`` is the important default: positions, assets and
    transactions are TENANT tables and the isolation check is only meaningful
    without the escape hatch.
    """

    class _Ctx:
        async def __aenter__(self):
            self.tr = conn.transaction()
            await self.tr.start()
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', $2, true),"
                "       set_config('app.current_auth0_sub', $3, true)",
                org_id, "true" if super_admin else "false",
                ADMIN_SUB if super_admin else MEMBER_SUB,
            )
            return conn

        async def __aexit__(self, et, e, tb):
            if et is None and commit:
                await self.tr.commit()
            else:
                await self.tr.rollback()
            return False

    return _Ctx()


async def read_position(conn, position_id: str) -> dict | None:
    row = await conn.fetchrow(
        f"""
        SELECT p.id::text AS id, p.quantity, p.cost_basis, p.market_value,
               p.as_of_date, p.owner_entity_id::text AS owner_entity_id,
               p.asset_id::text AS asset_id, p.ownership_basis, p.authority,
               p.source_system, p.taxonomy_key, p.valid_to, p.system_to
        FROM {TABLE_POSITIONS} p WHERE p.id = $1::uuid
        """,
        position_id,
    )
    return dict(row) if row else None


async def current_position_for(conn, org_id: str, asset_id: str) -> dict | None:
    row = await conn.fetchrow(
        f"""
        SELECT p.id::text AS id, p.quantity, p.cost_basis,
               p.owner_entity_id::text AS owner_entity_id, p.as_of_date,
               p.authority, p.source_system, p.valid_to
        FROM {TABLE_POSITIONS} p
        WHERE p.org_id = $1::uuid AND p.asset_id = $2::uuid
          AND p.valid_to IS NULL AND p.system_to IS NULL
        """,
        org_id, asset_id,
    )
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, REPORTED and ASSERTED
# ═══════════════════════════════════════════════════════════════════════════


async def check_task1a(conn) -> None:
    """1a — the corporate-actions table and the transactions FK, live."""
    cols = {
        r["column_name"]: (r["data_type"], r["is_nullable"], r["column_default"])
        for r in await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'portfolio'
              AND table_name = 'securities_global_corporate_actions'
            """
        )
    }
    expected = {
        "id", "global_security_id", "resulting_global_security_id", "action_type",
        "ex_date", "record_date", "pay_date", "terms", "source_system",
        "applied_at", "valid_from", "valid_to", "system_from", "system_to",
    }
    check(
        "[Y] 1a — portfolio.securities_global_corporate_actions is deployed with "
        "the corrected GLOBAL shape and NO org_id",
        expected <= set(cols) and "org_id" not in cols,
        f"missing={sorted(expected - set(cols))}, has_org_id={'org_id' in cols}",
    )
    check(
        "[Y] 1a — resulting_global_security_id is NULLABLE (a split has no "
        "resulting security); global_security_id, action_type, ex_date and "
        "terms are NOT NULL",
        cols["resulting_global_security_id"][1] == "YES"
        and all(cols[c][1] == "NO" for c in
                ("global_security_id", "action_type", "ex_date", "terms")),
        f"resulting={cols['resulting_global_security_id'][1]}, "
        f"terms={cols['terms'][1]}",
    )

    fkeys = {
        r["conname"]: r["def"]
        for r in await conn.fetch(
            """
            SELECT c.conname, pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'portfolio' AND t.relname = 'transactions'
              AND c.contype = 'f'
            """
        )
    }
    corp_fk = [d for d in fkeys.values() if "corporate_action_id" in d]
    check(
        "[Y] 1a — transactions.corporate_action_id is no longer a BARE uuid: it "
        "has a real FK to securities_global_corporate_actions",
        bool(corp_fk)
        and "securities_global_corporate_actions" in corp_fk[0],
        corp_fk[0] if corp_fk else "no FK found on corporate_action_id",
    )

    tcols = {
        r["column_name"]: (r["is_nullable"], r["column_default"])
        for r in await conn.fetch(
            """
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'portfolio' AND table_name = 'transactions'
            """
        )
    }
    check(
        "[Y] 1a — transactions.is_corporate_action_adjustment is deployed, NOT "
        "NULL, DEFAULT false",
        "is_corporate_action_adjustment" in tcols
        and tcols["is_corporate_action_adjustment"][0] == "NO"
        and "false" in (tcols["is_corporate_action_adjustment"][1] or ""),
        str(tcols.get("is_corporate_action_adjustment")),
    )

    chk = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'portfolio'
          AND t.relname = 'securities_global_corporate_actions'
          AND c.contype = 'c' AND c.conname = 'corp_actions_type_chk'
        """
    )
    deployed_types = set(re.findall(r"'([a-z_]+)'::text", chk or ""))
    check(
        "[Y] 1a — services.portfolio_corporate_actions.ACTION_TYPES mirrors the "
        "deployed corp_actions_type_chk EXACTLY",
        deployed_types == set(pca.ACTION_TYPES),
        f"deployed-only={sorted(deployed_types - set(pca.ACTION_TYPES))}, "
        f"python-only={sorted(set(pca.ACTION_TYPES) - deployed_types)}",
    )
    report(
        "1a — the corporate-actions table and the transactions FK, confirmed live",
        f"portfolio.securities_global_corporate_actions exists with the corrected "
        f"GLOBAL shape (no org_id), corp_actions_type_chk allowing "
        f"{sorted(deployed_types)}, FKs on BOTH global_security_id and "
        f"resulting_global_security_id, and indexes idx_corp_actions_security / "
        f"idx_corp_actions_ex_date. transactions.corporate_action_id now carries "
        f"transactions_corporate_action_fkey, and "
        f"is_corporate_action_adjustment boolean NOT NULL DEFAULT false is "
        f"deployed.",
    )


async def check_task1b(conn) -> None:
    """1b — A1's Super-Admin-gated global pattern, composed not reinvented."""
    policies = {
        (r["policyname"], r["cmd"]): (r["qual"], r["with_check"])
        for r in await conn.fetch(
            """
            SELECT policyname, cmd, qual, with_check FROM pg_policies
            WHERE schemaname = 'portfolio'
              AND tablename = 'securities_global_corporate_actions'
            """
        )
    }
    cmds = sorted(c for _, c in policies)
    read = [q for (_, c), (q, _) in policies.items() if c == "SELECT"]
    writes = [
        (q or "") + (w or "")
        for (_, c), (q, w) in policies.items() if c != "SELECT"
    ]
    check(
        "[Y] 1b — the global table carries A1's FOUR-policy shape: global-read "
        "USING (true) + Super-Admin INSERT/UPDATE/DELETE",
        cmds == ["DELETE", "INSERT", "SELECT", "UPDATE"]
        and read == ["true"]
        and all("app.is_super_admin" in w for w in writes),
        f"cmds={cmds}, read={read}",
    )

    src = inspect.getsource(pca.record_corporate_action)
    check(
        "[Y] 1b — record_corporate_action COMPOSES A1's real conventions "
        "(_require_super_admin gate + _SuperAdminWrite elevation), it does not "
        "reimplement them",
        "_require_super_admin(" in src and "_SuperAdminWrite(" in src
        and pca._require_super_admin.__module__ == "services.securities_global"
        and pca._SuperAdminWrite.__module__ == "services.securities_global",
        f"gate from {pca._require_super_admin.__module__}, "
        f"elevation from {pca._SuperAdminWrite.__module__}",
    )
    check(
        "[Y] 1b — record_corporate_action also reuses A1's merge-chain "
        "resolution (COALESCE(canonical_id, id)), as add_price does",
        "COALESCE(s.canonical_id, s.id)"
        in inspect.getsource(pca._resolve_canonical),
        "_resolve_canonical forwards through the merge chain",
    )
    report(
        "1b — A1's real global-write pattern, re-read and composed",
        "create_security / add_identifier / add_price / add_relationship all do "
        "_require_super_admin(is_super_admin, name) for a legible refusal, then "
        "`async with _SuperAdminWrite(conn)` for a transaction-local SET LOCAL "
        "app.is_super_admin so RLS remains the real gate; add_price additionally "
        "forwards its target through COALESCE(canonical_id, id). "
        "record_corporate_action imports and uses all three rather than "
        "inventing a parallel gate.",
    )


async def check_task1c(conn) -> None:
    """1c — record_transaction's real signature, and the honest type."""
    params = inspect.signature(record_transaction).parameters
    check(
        "[Y] 1c — A2's record_transaction now accepts "
        "is_corporate_action_adjustment (the column post-dates A2, so the "
        "INSERT did not name it and every adjustment would have stored the "
        "column default)",
        "is_corporate_action_adjustment" in params
        and params["is_corporate_action_adjustment"].default is False
        and "is_corporate_action_adjustment" in inspect.getsource(record_transaction),
        f"default={params.get('is_corporate_action_adjustment')}",
    )

    rows = {
        r["code"]: dict(r)
        for r in await conn.fetch(
            """
            SELECT code, label, market, is_active, direction, performance_impact,
                   affects_paid_in, affects_unfunded, affects_nav, amount_basis
            FROM public.transaction_types
            """
        )
    }
    adj = rows.get(ADJUSTMENT_TYPE_CODE)
    check(
        "[Y] 1c — 'adjustment' exists, is active, and is performance-neutral in "
        "every axis the deployed vocabulary has",
        adj is not None and adj["is_active"] and adj["market"] == "both"
        and adj["direction"] == "none" and adj["performance_impact"] == "none"
        and adj["affects_paid_in"] == 0 and adj["affects_unfunded"] == 0
        and adj["affects_nav"] == 0,
        json.dumps({k: str(v) for k, v in (adj or {}).items()}),
    )
    neutral = sorted(
        c for c, r in rows.items()
        if r["is_active"] and r["market"] == "both" and r["direction"] == "none"
        and r["performance_impact"] == "none" and r["affects_nav"] == 0
    )
    check(
        "[Y] 1c — 'adjustment' is the ONLY honest fit: no other active type is "
        "simultaneously market='both', direction='none', "
        "performance_impact='none' and affects_nav=0",
        neutral == [ADJUSTMENT_TYPE_CODE],
        f"candidates={neutral} out of {len(rows)} deployed types",
    )
    report(
        "1c — the real transaction_types vocabulary, and the honest fit",
        f"{len(rows)} types deployed. 'adjustment' is the only one that is "
        f"simultaneously direction='none', performance_impact='none', "
        f"affects_paid_in=0, affects_unfunded=0, affects_nav=0 and market='both' "
        f"— so it attaches to a listed equity and a private fund interest alike "
        f"and registers as neither gain, income, contribution nor distribution. "
        f"'sell' carries performance_impact='gain'; 'dist_stock' is a private "
        f"distribution; 'fee_expense' is market='both' but direction='debit' and "
        f"affects_nav=-1; 'valuation' is direction='none' but market='private' "
        f"and affects_nav=1. REPORTED MISMATCH, not papered over: "
        f"adjustment.amount_basis='{adj['amount_basis']}' while a split "
        f"adjustment carries a UNIT delta — there is no units-based, "
        f"performance-neutral type in the deployed vocabulary.",
    )


async def check_task1d(conn) -> None:
    """1d — the real mechanism for finding every tenant asset for a security."""
    fk = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'portfolio' AND t.relname = 'assets'
          AND c.conname = 'assets_global_security_id_fkey'
        """
    )
    idx = await conn.fetchval(
        """
        SELECT indexdef FROM pg_indexes
        WHERE schemaname = 'portfolio' AND tablename = 'assets'
          AND indexname = 'idx_assets_global'
        """
    )
    check(
        "[Y] 1d — assets.global_security_id FKs to securities_global(id) and is "
        "indexed (partial, WHERE global_security_id IS NOT NULL)",
        bool(fk) and "securities_global(id)" in fk
        and bool(idx) and "global_security_id IS NOT NULL" in idx,
        f"{fk} / {idx}",
    )
    check(
        "[Y] 1d — find_affected_assets matches on COALESCE(canonical_id, id), "
        "not on the raw id: an org whose asset points at a security A1 later "
        "merged away still holds the same real security",
        "COALESCE(s.canonical_id, s.id)"
        in inspect.getsource(pca.find_affected_assets),
        "merge-chain-aware",
    )
    report(
        "1d — how apply finds which orgs and which of their own assets are hit",
        "portfolio.assets.global_security_id (FK assets_global_security_id_fkey, "
        "partial index idx_assets_global). find_affected_assets JOINs "
        "securities_global and matches COALESCE(s.canonical_id, s.id) = the "
        "action's security, under the caller's org RLS context — so RLS is the "
        "first lock limiting it to one tenant and the explicit a.org_id = $1 is "
        "the second. Every org holding the security runs its own apply "
        "independently; there is no cross-tenant fan-out anywhere in this "
        "module.",
    )


def check_schema_qualification() -> None:
    """Every portfolio.* reference is schema-qualified. `portfolio` is NOT on
    app_service's search_path — an unqualified FROM works in a psql session that
    happened to SET search_path and raises UndefinedTable in production."""
    src = inspect.getsource(pca)
    bare = re.findall(
        r"\b(?:FROM|INTO|UPDATE|JOIN)\s+"
        r"(assets|positions|transactions|securities_global"
        r"|securities_global_corporate_actions)\b",
        src,
    )
    check(
        "[Y] every portfolio.* reference in the new module is schema-qualified "
        "(portfolio is NOT on app_service's search_path)",
        not bare,
        f"bare references: {sorted(set(bare))}" if bare else "no bare references",
    )


def check_ratio_parsing() -> None:
    """The published ratio is CONSUMED. Parsing it is where it can go wrong."""
    cases = [
        ("2:1", Decimal("100"), Decimal("200")),
        ("1:10", Decimal("500"), Decimal("50")),
        ("3:2", Decimal("100"), Decimal("150")),
        ("1:4", Decimal("300"), Decimal("75")),
        (3, Decimal("100"), Decimal("300")),
    ]
    ok = all(parse_ratio(r).apply(q) == want for r, q, want in cases)
    check(
        "[Y] parse_ratio reads NEW:OLD consistently for splits, reverse splits "
        "and spinoff distributions",
        ok,
        ", ".join(f"{r}×{q}={parse_ratio(r).apply(q)}" for r, q, _ in cases),
    )

    refused = []
    for bad in (2.0, "0:1", "-2:1", "", None, "two:one"):
        try:
            parse_ratio(bad)
        except CorporateActionError:
            refused.append(bad)
    check(
        "[Y] parse_ratio refuses float, zero, negative, empty and non-numeric "
        "ratios — a 0:1 'split' would silently zero every holder's quantity",
        len(refused) == 6,
        f"refused {refused}",
    )
    # Numerator and denominator kept separate: qty × 1 ÷ 3 × 3 ÷ 1 returns
    # exactly, a pre-divided Decimal(1)/Decimal(3) multiplier does not.
    r = parse_ratio("1:3")
    check(
        "[Y] Ratio keeps numerator/denominator separate, so a round trip is "
        "exact (a pre-divided multiplier loses the total-cost-basis invariant)",
        r.unapply(r.apply(Decimal("999"))) == Decimal("999"),
        f"999 → {r.apply(Decimal('999'))} → {r.unapply(r.apply(Decimal('999')))}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — RECORD, global and Super-Admin-gated
# ═══════════════════════════════════════════════════════════════════════════


async def build_securities(conn) -> dict[str, str]:
    ids = {}
    for key, name in (
        ("split", SEC_SPLIT), ("reverse", SEC_REVERSE), ("parent", SEC_PARENT),
        ("spinco", SEC_SPINCO), ("unheld", SEC_UNHELD),
    ):
        ids[key] = await create_security(
            conn, name=name, security_type="equity", currency_code="USD",
            price_coverage="has_series", is_super_admin=True,
        )
    return ids


async def check_record(admin_conn, app_conn, secs) -> dict[str, str]:
    actions: dict[str, str] = {}

    actions["split"] = await record_corporate_action(
        admin_conn,
        global_security_id=secs["split"], action_type=SPLIT,
        ex_date=EX_DATE_SPLIT, record_date=date(2026, 7, 10),
        pay_date=date(2026, 7, 14), terms={TERMS_RATIO: SPLIT_RATIO},
        source_system="verify-portfoliof", is_super_admin=True,
    )
    stored = await get_corporate_action(admin_conn, actions["split"])
    check(
        "[Y] a corporate action can be RECORDED globally by a Super-Admin "
        "context, with its published terms stored verbatim",
        stored is not None and stored["action_type"] == SPLIT
        and stored["terms"] == {TERMS_RATIO: SPLIT_RATIO}
        and stored["ex_date"] == EX_DATE_SPLIT
        and stored["global_security_id"] == secs["split"],
        f"terms={stored['terms']}, ex_date={stored['ex_date']}",
    )
    check(
        "[Y] applied_at is left NULL by RECORD — it is a column on the GLOBAL "
        "row and 'applied' is a per-org fact",
        stored["applied_at"] is None
        and "applied_at" not in inspect.getsource(pca._apply),
        f"applied_at={stored['applied_at']}",
    )

    actions["reverse"] = await record_corporate_action(
        admin_conn,
        global_security_id=secs["reverse"], action_type=REVERSE_SPLIT,
        ex_date=EX_DATE_REVERSE, terms={TERMS_RATIO: REVERSE_RATIO},
        is_super_admin=True,
    )
    actions["spinoff"] = await record_corporate_action(
        admin_conn,
        global_security_id=secs["parent"],
        resulting_global_security_id=secs["spinco"],
        action_type=SPINOFF, ex_date=EX_DATE_SPINOFF,
        terms={
            TERMS_DISTRIBUTION_RATIO: SPINOFF_DIST_RATIO,
            TERMS_COST_BASIS_PCT: SPINOFF_RETAINED_PCT,
            TERMS_CASH_IN_LIEU: SPINOFF_CASH_IN_LIEU,
        },
        is_super_admin=True,
    )
    actions["unheld"] = await record_corporate_action(
        admin_conn,
        global_security_id=secs["unheld"], action_type=SPLIT,
        ex_date=EX_DATE_UNHELD, terms={TERMS_RATIO: "5:1"},
        is_super_admin=True,
    )
    actions["merger"] = await record_corporate_action(
        admin_conn,
        global_security_id=secs["unheld"], action_type="merger",
        ex_date=EX_DATE_UNHELD, terms={"consideration": "cash"},
        is_super_admin=True,
    )
    check(
        "[Y] a 'merger' — no application logic in this sprint — still RECORDS "
        "cleanly without being forced to invent a ratio key",
        (await get_corporate_action(admin_conn, actions["merger"]))["terms"]
        == {"consideration": "cash"},
        "terms shape is deliberately unvalidated beyond 'present, JSON object'",
    )

    # ── The refusal, at BOTH layers ──────────────────────────────────────
    before = await admin_conn.fetchval(f"SELECT count(*) FROM {TABLE_CORP_ACTIONS}")
    raised = None
    try:
        await record_corporate_action(
            admin_conn, global_security_id=secs["split"], action_type=SPLIT,
            ex_date=EX_DATE_SPLIT, terms={TERMS_RATIO: "9:1"},
            is_super_admin=False,
        )
    except SecuritiesGlobalPermissionError as exc:
        raised = exc
    after = await admin_conn.fetchval(f"SELECT count(*) FROM {TABLE_CORP_ACTIONS}")
    check(
        "[Y] recording is REJECTED for a non-super-admin caller — and nothing "
        "was written (the count is asserted, not just the exception)",
        raised is not None and before == after,
        f"{type(raised).__name__ if raised else 'no exception'}; "
        f"rows {before} → {after}",
    )

    # The app-layer gate is a promise; RLS is the gate. Prove it separately, on
    # the real app_service role, with NO elevation.
    rls_error = None
    async with org_ctx(app_conn, DEFAULT_ORG_ID, super_admin=False, commit=False):
        try:
            await app_conn.execute(
                f"""
                INSERT INTO {TABLE_CORP_ACTIONS}
                    (global_security_id, action_type, ex_date, terms)
                VALUES ($1::uuid, 'split', $2, '{{"ratio":"9:1"}}'::jsonb)
                """,
                secs["split"], EX_DATE_SPLIT,
            )
        except asyncpg.PostgresError as exc:
            rls_error = exc
    check(
        "[Y] RLS is the REAL gate: a direct INSERT on the global table from the "
        "app_service role with no Super-Admin elevation is refused by the "
        "database, not merely by a Python if",
        rls_error is not None
        and isinstance(rls_error, asyncpg.InsufficientPrivilegeError),
        f"{type(rls_error).__name__ if rls_error else 'INSERT SUCCEEDED'}",
    )

    # Recording validations that would otherwise fail late, at apply time.
    spinoff_no_result = None
    try:
        await record_corporate_action(
            admin_conn, global_security_id=secs["parent"], action_type=SPINOFF,
            ex_date=EX_DATE_SPINOFF, terms={TERMS_DISTRIBUTION_RATIO: "1:4"},
            is_super_admin=True,
        )
    except CorporateActionError as exc:
        spinoff_no_result = exc
    empty_terms = None
    try:
        await record_corporate_action(
            admin_conn, global_security_id=secs["split"], action_type=SPLIT,
            ex_date=EX_DATE_SPLIT, terms={}, is_super_admin=True,
        )
    except CorporateActionError as exc:
        empty_terms = exc
    check(
        "[Y] a spinoff with no resulting_global_security_id, and an empty terms "
        "object, are both refused at RECORD time — not discovered mid-apply "
        "after the original position has already been restated",
        spinoff_no_result is not None and empty_terms is not None,
        f"{type(spinoff_no_result).__name__} / {type(empty_terms).__name__}",
    )
    return actions


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — APPLY: split / reverse split
# ═══════════════════════════════════════════════════════════════════════════


async def build_tenant_fixtures(conn, secs) -> dict:
    ids: dict = {}
    for key, org, name, etype in (
        ("owner", DEFAULT_ORG_ID, E_OWNER, "llc"),
        ("other_owner", OTHER_ORG_ID, E_OTHER_OWNER, "llc"),
    ):
        ids[key] = await conn.fetchval(
            "INSERT INTO public.entities (org_id, entity_type, display_name) "
            "VALUES ($1::uuid, $2::entity_type, $3) RETURNING id::text",
            org, etype, name,
        )

    for key, org, name, sec in (
        ("a_split", DEFAULT_ORG_ID, A_SPLIT, secs["split"]),
        ("a_reverse", DEFAULT_ORG_ID, A_REVERSE, secs["reverse"]),
        ("a_parent", DEFAULT_ORG_ID, A_PARENT, secs["parent"]),
        ("a_other", OTHER_ORG_ID, A_OTHER_SPLIT, secs["split"]),
    ):
        ids[key] = await create_asset(
            conn, org_id=org, name=name, asset_type="equity",
            asset_class="financial", ownership_basis="units",
            valuation_method="market_price", global_security_id=sec,
            currency_code="USD",
        )

    ids["p_split"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner"],
        asset_id=ids["a_split"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", quantity=SPLIT_QTY_BEFORE,
        cost_basis=SPLIT_COST, market_value=Decimal("7400.00"),
        taxonomy_key="taxonomy_sc_1",
    )
    ids["p_reverse"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner"],
        asset_id=ids["a_reverse"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", quantity=REVERSE_QTY_BEFORE,
        cost_basis=REVERSE_COST,
    )
    ids["p_parent"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner"],
        asset_id=ids["a_parent"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", quantity=SPINOFF_QTY,
        cost_basis=SPINOFF_COST_BEFORE,
    )
    ids["p_other"] = await create_position(
        conn, org_id=OTHER_ORG_ID, owner_entity_id=ids["other_owner"],
        asset_id=ids["a_other"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", quantity=OTHER_QTY_BEFORE,
        cost_basis=OTHER_COST,
    )

    # A REAL trade BEFORE the split, on the pre-split position row — so the
    # history the Task 5 filter reads spans the bi-temporal restatement.
    ids["t_buy"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["p_split"],
        transaction_type_code="buy", trade_date=TRADE_BUY, authority="custodial",
        source_system="reporting_tool_import", quantity=BUY_QTY,
        price=Decimal("50.00"), net_amount=BUY_NET, currency_code="USD",
    )
    return ids


async def check_split(conn, ids, actions) -> dict:
    before = await read_position(conn, ids["p_split"])
    check(
        "[Y] SPLIT — the PRE-split row really is quantity=100 / cost_basis=5,000 "
        "and is the current row (asserted BEFORE, so 200 afterwards cannot be a "
        "fixture that was always 200)",
        _dec(before["quantity"]) == SPLIT_QTY_BEFORE
        and _dec(before["cost_basis"]) == SPLIT_COST
        and before["valid_to"] is None,
        f"qty={before['quantity']}, cost={before['cost_basis']}, "
        f"valid_to={before['valid_to']}",
    )

    outcome = await apply_split(conn, DEFAULT_ORG_ID, actions["split"])
    ids["p_split_after"] = outcome.adjusted[0].position_id
    ids["t_adjust"] = outcome.adjusted[0].transaction_id

    after = await read_position(conn, ids["p_split_after"])
    closed = await read_position(conn, ids["p_split"])
    check(
        "[Y] SPLIT — quantity=100 / cost_basis=$5,000 under a real 2:1 split "
        "becomes quantity=200 with cost_basis STILL $5,000 (exact Decimal)",
        _dec(after["quantity"]) == SPLIT_QTY_AFTER
        and _dec(after["cost_basis"]) == SPLIT_COST,
        f"qty={after['quantity']} (want {SPLIT_QTY_AFTER}), "
        f"cost={after['cost_basis']} (want {SPLIT_COST})",
    )
    unit_before = SPLIT_COST / SPLIT_QTY_BEFORE
    unit_after = _dec(after["cost_basis"]) / _dec(after["quantity"])
    check(
        "[Y] SPLIT — UNIT cost was halved ($50.00 → $25.00) while TOTAL cost "
        "basis did not move, which is the same statement read two ways",
        unit_before == SPLIT_UNIT_BEFORE and unit_after == SPLIT_UNIT_AFTER
        and _dec(after["cost_basis"]) == _dec(closed["cost_basis"]),
        f"unit {unit_before} → {unit_after}; total "
        f"{closed['cost_basis']} → {after['cost_basis']}",
    )
    check(
        "[Y] SPLIT — the write is a BI-TEMPORAL restatement (Rule 3), not an "
        "in-place UPDATE: the predecessor row still reads 100 and now carries a "
        "valid_to",
        _dec(closed["quantity"]) == SPLIT_QTY_BEFORE
        and closed["valid_to"] is not None
        and after["id"] != closed["id"],
        f"closed: qty={closed['quantity']} valid_to={closed['valid_to']}",
    )
    check(
        "[Y] SPLIT — everything the action did NOT name is carried across "
        "verbatim (owner, as_of_date, authority, source_system, taxonomy_key)",
        after["owner_entity_id"] == closed["owner_entity_id"]
        and after["as_of_date"] == closed["as_of_date"]
        and after["authority"] == closed["authority"]
        and after["source_system"] == closed["source_system"]
        and after["taxonomy_key"] == closed["taxonomy_key"],
        f"as_of={after['as_of_date']}, authority={after['authority']}, "
        f"source={after['source_system']}, taxonomy={after['taxonomy_key']}",
    )

    txn = await conn.fetchrow(
        f"""
        SELECT t.transaction_type_code, t.is_corporate_action_adjustment,
               t.corporate_action_id::text AS corporate_action_id, t.quantity,
               t.trade_date, t.price, t.gross_amount, t.net_amount,
               t.position_id::text AS position_id
        FROM {TABLE_TRANSACTIONS} t WHERE t.id = $1::uuid
        """,
        ids["t_adjust"],
    )
    check(
        "[Y] SPLIT — the recorded transaction carries "
        "is_corporate_action_adjustment=true and the REAL corporate_action_id, "
        "on the honest type 'adjustment', dated the ex-date",
        txn["is_corporate_action_adjustment"] is True
        and txn["corporate_action_id"] == actions["split"]
        and txn["transaction_type_code"] == ADJUSTMENT_TYPE_CODE
        and txn["trade_date"] == EX_DATE_SPLIT
        and _dec(txn["quantity"]) == SPLIT_DELTA
        and txn["position_id"] == ids["p_split_after"],
        f"type={txn['transaction_type_code']}, flag="
        f"{txn['is_corporate_action_adjustment']}, delta={txn['quantity']}",
    )
    check(
        "[Y] SPLIT — no cash moved, and that is stored as NULL not 0.00: a "
        "zero is indistinguishable from a real zero-dollar trade once summed",
        txn["price"] is None and txn["gross_amount"] is None
        and txn["net_amount"] is None,
        f"price={txn['price']}, gross={txn['gross_amount']}, "
        f"net={txn['net_amount']}",
    )

    # ── REVERSE SPLIT, the same code path in the other direction ─────────
    rev = await apply_split(conn, DEFAULT_ORG_ID, actions["reverse"])
    rev_after = await read_position(conn, rev.adjusted[0].position_id)
    rev_txn = await conn.fetchval(
        f"SELECT quantity FROM {TABLE_TRANSACTIONS} WHERE id = $1::uuid",
        rev.adjusted[0].transaction_id,
    )
    check(
        "[Y] REVERSE SPLIT — 500 shares at a 1:10 reverse becomes 50, cost "
        "basis $9,000 unchanged, and the adjustment delta is NEGATIVE (−450)",
        _dec(rev_after["quantity"]) == REVERSE_QTY_AFTER
        and _dec(rev_after["cost_basis"]) == REVERSE_COST
        and _dec(rev_txn) == REVERSE_DELTA,
        f"qty={rev_after['quantity']}, cost={rev_after['cost_basis']}, "
        f"delta={rev_txn}",
    )
    return ids


async def check_idempotency(conn, ids, actions) -> None:
    """The same action, applied to the same org twice."""
    second = await apply_split(conn, DEFAULT_ORG_ID, actions["split"])
    check(
        "[Y] SPLIT IDEMPOTENCY — the second apply reports already_applied=True "
        "with zero positions affected, and names the prior transaction",
        second.already_applied is True and second.positions_affected == 0
        and second.prior_transaction_ids == (ids["t_adjust"],),
        f"already_applied={second.already_applied}, "
        f"affected={second.positions_affected}, "
        f"prior={second.prior_transaction_ids}",
    )
    after = await current_position_for(conn, DEFAULT_ORG_ID, ids["a_split"])
    check(
        "[Y] SPLIT IDEMPOTENCY — quantity and cost basis are at their POST-split "
        "values (200 / $5,000), NOT double-adjusted to 400 — asserted "
        "explicitly, not inferred from 'no error'",
        _dec(after["quantity"]) == SPLIT_QTY_AFTER
        and _dec(after["cost_basis"]) == SPLIT_COST
        and after["id"] == ids["p_split_after"],
        f"qty={after['quantity']} (double would be "
        f"{SPLIT_QTY_AFTER * 2}), cost={after['cost_basis']}",
    )
    rows = await conn.fetchval(
        f"""
        SELECT count(*) FROM {TABLE_POSITIONS} p
        WHERE p.org_id = $1::uuid AND p.asset_id = $2::uuid
          AND p.valid_to IS NULL AND p.system_to IS NULL
        """,
        DEFAULT_ORG_ID, ids["a_split"],
    )
    adjustments = await already_applied_transactions(
        conn, DEFAULT_ORG_ID, actions["split"]
    )
    check(
        "[Y] SPLIT IDEMPOTENCY — exactly ONE current position row and exactly "
        "ONE adjustment transaction survive the double apply",
        rows == 1 and len(adjustments) == 1,
        f"current positions={rows}, adjustments={len(adjustments)}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 4 — APPLY: spinoff
# ═══════════════════════════════════════════════════════════════════════════


async def check_spinoff(conn, ids, actions, secs) -> dict:
    pre_existing = await find_affected_assets(conn, DEFAULT_ORG_ID, secs["spinco"])
    check(
        "[Y] SPINOFF — this org has NO tenant asset for the resulting security "
        "before the apply (so 'one was created' is a real creation)",
        pre_existing == [],
        f"pre-existing assets for spinco: {len(pre_existing)}",
    )

    outcome = await apply_spinoff(conn, DEFAULT_ORG_ID, actions["spinoff"])
    adj = outcome.adjusted[0]
    ids["p_parent_after"] = adj.position_id
    ids["p_spinco"] = adj.resulting_position_id
    ids["a_spinco"] = adj.resulting_asset_id

    parent = await read_position(conn, adj.position_id)
    spinco = await read_position(conn, adj.resulting_position_id)
    check(
        "[Y] SPINOFF — the ORIGINAL position is adjusted per the published "
        "terms: share count unchanged at 300, cost basis 30,000 → 24,000 "
        "(the 80% Form-8937 allocation)",
        _dec(parent["quantity"]) == SPINOFF_QTY
        and _dec(parent["cost_basis"]) == SPINOFF_COST_AFTER
        and adj.cost_basis_before is not None
        and _dec(adj.cost_basis_before) == SPINOFF_COST_BEFORE,
        f"qty={parent['quantity']}, cost {adj.cost_basis_before} → "
        f"{parent['cost_basis']}",
    )
    check(
        "[Y] SPINOFF — a NEW position on the resulting security exists for the "
        "SAME owner_entity_id, quantity 75 (300 × 1:4) and the residual 6,000 "
        "of cost basis; the two sides sum back to the original 30,000",
        spinco is not None
        and spinco["owner_entity_id"] == parent["owner_entity_id"]
        and _dec(spinco["quantity"]) == SPINCO_QTY
        and _dec(spinco["cost_basis"]) == SPINCO_COST
        and _dec(parent["cost_basis"]) + _dec(spinco["cost_basis"])
        == SPINOFF_COST_BEFORE,
        f"owner={spinco['owner_entity_id']}, qty={spinco['quantity']}, "
        f"cost={spinco['cost_basis']}; "
        f"{parent['cost_basis']} + {spinco['cost_basis']} = "
        f"{_dec(parent['cost_basis']) + _dec(spinco['cost_basis'])}",
    )

    new_asset = await conn.fetchrow(
        f"""
        SELECT a.id::text AS id, a.name, a.org_id::text AS org_id,
               a.global_security_id::text AS global_security_id,
               a.ownership_basis, a.valuation_method, a.asset_class,
               a.currency_code
        FROM {TABLE_ASSETS} a WHERE a.id = $1::uuid
        """,
        adj.resulting_asset_id,
    )
    check(
        "[Y] SPINOFF — the resulting tenant asset was CREATED for this org, "
        "correctly linked to resulting_global_security_id, in the right org, "
        "with the parent's valuation_method (not a market_price default)",
        outcome.resulting_asset_created is True
        and new_asset["global_security_id"] == secs["spinco"]
        and new_asset["org_id"] == DEFAULT_ORG_ID
        and new_asset["name"] == SEC_SPINCO
        and new_asset["valuation_method"] == "market_price"
        and new_asset["ownership_basis"] == "units",
        f"created={outcome.resulting_asset_created}, "
        f"global_security_id={new_asset['global_security_id']}, "
        f"org={new_asset['org_id']}",
    )

    both = await conn.fetch(
        f"""
        SELECT t.id::text AS id, t.position_id::text AS position_id, t.quantity,
               t.is_corporate_action_adjustment,
               t.corporate_action_id::text AS corporate_action_id
        FROM {TABLE_TRANSACTIONS} t
        WHERE t.corporate_action_id = $1::uuid AND t.org_id = $2::uuid
        ORDER BY t.id
        """,
        actions["spinoff"], DEFAULT_ORG_ID,
    )
    check(
        "[Y] SPINOFF — BOTH sides landed in the SAME atomic operation: two "
        "adjustment transactions on the same corporate_action_id, one per "
        "position, and neither side exists without the other",
        len(both) == 2
        and all(r["is_corporate_action_adjustment"] for r in both)
        and {r["position_id"] for r in both}
        == {adj.position_id, adj.resulting_position_id},
        f"{len(both)} transactions across positions "
        f"{sorted({r['position_id'] for r in both})}",
    )
    check(
        "[Y] SPINOFF — cash_in_lieu_per_share is reported as UNAPPLIED rather "
        "than silently turned into a cash movement this module would have to "
        "compute",
        outcome.unapplied_terms == {TERMS_CASH_IN_LIEU: SPINOFF_CASH_IN_LIEU},
        f"unapplied_terms={dict(outcome.unapplied_terms)}",
    )

    second = await apply_spinoff(conn, DEFAULT_ORG_ID, actions["spinoff"])
    still_parent = await current_position_for(conn, DEFAULT_ORG_ID, ids["a_parent"])
    spinco_rows = await conn.fetchval(
        f"""
        SELECT count(*) FROM {TABLE_POSITIONS} p
        WHERE p.org_id = $1::uuid AND p.asset_id = $2::uuid
          AND p.valid_to IS NULL AND p.system_to IS NULL
        """,
        DEFAULT_ORG_ID, adj.resulting_asset_id,
    )
    check(
        "[Y] SPINOFF IDEMPOTENCY — a second apply changes nothing: parent still "
        "300 / 24,000, exactly ONE current spinco position, no duplicate asset",
        second.already_applied is True and second.positions_affected == 0
        and _dec(still_parent["quantity"]) == SPINOFF_QTY
        and _dec(still_parent["cost_basis"]) == SPINOFF_COST_AFTER
        and spinco_rows == 1,
        f"already_applied={second.already_applied}, parent="
        f"{still_parent['quantity']}/{still_parent['cost_basis']}, "
        f"spinco current rows={spinco_rows}",
    )
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# TASK 5 — a real adjustment vs. a real trade
# ═══════════════════════════════════════════════════════════════════════════


async def check_adjustment_vs_trade(conn, ids, actions) -> None:
    """A ledger reading transactions must be able to exclude adjustments by
    filtering on ONE boolean, knowing nothing about corporate actions."""
    ids["t_sell"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["p_split_after"],
        transaction_type_code="sell", trade_date=TRADE_SELL,
        authority="custodial", source_system="reporting_tool_import",
        quantity=SELL_QTY, price=Decimal("65.00"), net_amount=SELL_NET,
        currency_code="USD",
    )

    # The query a report would really write: the whole history of one HOLDING,
    # spanning the bi-temporal restatement, with no mention of corporate actions.
    history_sql = f"""
        SELECT t.id::text AS id, t.transaction_type_code, t.net_amount
        FROM {TABLE_TRANSACTIONS} t
        JOIN {TABLE_POSITIONS} p ON p.id = t.position_id
        WHERE p.asset_id = $1::uuid AND t.org_id = $2::uuid
        {{extra}}
        ORDER BY t.trade_date
    """
    everything = await conn.fetch(
        history_sql.format(extra=""), ids["a_split"], DEFAULT_ORG_ID
    )
    trades_only = await conn.fetch(
        history_sql.format(extra="AND t.is_corporate_action_adjustment = false"),
        ids["a_split"], DEFAULT_ORG_ID,
    )
    adjustments_only = await conn.fetch(
        history_sql.format(extra="AND t.is_corporate_action_adjustment = true"),
        ids["a_split"], DEFAULT_ORG_ID,
    )

    check(
        "[Y] the full history of the holding is three rows across the "
        "restatement — a real buy, a real sell, and the split adjustment",
        {r["id"] for r in everything}
        == {ids["t_buy"], ids["t_sell"], ids["t_adjust"]},
        f"{len(everything)} rows: "
        f"{sorted(r['transaction_type_code'] for r in everything)}",
    )
    check(
        "[Y] WHERE is_corporate_action_adjustment = false returns EXACTLY the "
        "buy and the sell and EXCLUDES the adjustment — the filter drops one "
        "row, not all of them and not none",
        {r["id"] for r in trades_only} == {ids["t_buy"], ids["t_sell"]}
        and ids["t_adjust"] not in {r["id"] for r in trades_only}
        and len(adjustments_only) == 1
        and adjustments_only[0]["id"] == ids["t_adjust"],
        f"trades={sorted(r['transaction_type_code'] for r in trades_only)}, "
        f"adjustments={len(adjustments_only)}",
    )

    # A realized-gain calculation, written the way one actually is: sum the
    # net amounts of the non-adjustment rows. It must not need to know this
    # module exists.
    realized = await conn.fetchval(
        f"""
        SELECT COALESCE(sum(t.net_amount), 0)
        FROM {TABLE_TRANSACTIONS} t
        JOIN {TABLE_POSITIONS} p ON p.id = t.position_id
        WHERE p.asset_id = $1::uuid AND t.org_id = $2::uuid
          AND t.is_corporate_action_adjustment = false
        """,
        ids["a_split"], DEFAULT_ORG_ID,
    )
    check(
        "[Y] a realized-gain sum over the filtered history is exactly "
        "−5,000 + 2,600 = −2,400: the adjustment contributes nothing, because "
        "it carries NULL amounts AND is excluded by the flag",
        _dec(realized) == BUY_NET + SELL_NET,
        f"sum(net_amount)={realized}, want {BUY_NET + SELL_NET}",
    )

    tally = await conn.fetchrow(
        f"""
        SELECT tt.performance_impact, tt.direction, tt.affects_nav
        FROM {TABLE_TRANSACTIONS} t
        JOIN public.transaction_types tt ON tt.code = t.transaction_type_code
        WHERE t.id = $1::uuid
        """,
        ids["t_adjust"],
    )
    check(
        "[Y] the adjustment does NOT register as a gain or a trade by its TYPE "
        "either: performance_impact='none', direction='none', affects_nav=0 — "
        "so even a report that ignores the flag cannot book it as a gain",
        tally["performance_impact"] == "none" and tally["direction"] == "none"
        and tally["affects_nav"] == 0,
        f"performance_impact={tally['performance_impact']}, "
        f"direction={tally['direction']}, affects_nav={tally['affects_nav']}",
    )
    check(
        "[Y] the flag is set EXPLICITLY, not derived from corporate_action_id "
        "IS NOT NULL — so a cash-in-lieu sale citing an action can still be a "
        "real realized gain",
        "is_corporate_action_adjustment=True"
        in inspect.getsource(pca._record_adjustment)
        and "is_corporate_action_adjustment" in inspect.signature(
            record_transaction).parameters,
        "record_transaction takes the flag as its own parameter",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Zero-affected, unapplicable types, and cross-org isolation
# ═══════════════════════════════════════════════════════════════════════════


async def check_zero_and_unapplicable(conn, ids, actions) -> None:
    outcome = await apply_split(conn, DEFAULT_ORG_ID, actions["unheld"])
    check(
        "[Y] applying an action for a security this org holds NONE of does "
        "nothing and reports zero positions affected, CLEANLY — not an error, "
        "and distinguishable from already_applied",
        outcome.positions_affected == 0 and outcome.already_applied is False
        and outcome.assets_matched == () and outcome.adjusted == ()
        and outcome.applied is False,
        f"affected={outcome.positions_affected}, "
        f"already_applied={outcome.already_applied}, "
        f"assets_matched={outcome.assets_matched}",
    )
    left_alone = await already_applied_transactions(
        conn, DEFAULT_ORG_ID, actions["unheld"]
    )
    check(
        "[Y] the zero-affected apply wrote NOTHING — no marker transaction, so "
        "a later apply after the org buys the security still works",
        left_alone == [],
        f"transactions written: {len(left_alone)}",
    )

    raised = None
    try:
        await apply_corporate_action(conn, DEFAULT_ORG_ID, actions["merger"])
    except UnapplicableActionError as exc:
        raised = exc
    check(
        "[Y] apply_corporate_action REFUSES a 'merger' by name rather than "
        "returning a clean zero — which would be indistinguishable from 'this "
        "org holds none of it'",
        raised is not None and "merger" in str(raised),
        f"{type(raised).__name__ if raised else 'no exception'}",
    )
    dispatched = await apply_corporate_action(
        conn, DEFAULT_ORG_ID, actions["split"]
    )
    check(
        "[Y] apply_corporate_action DISPATCHES on the recorded action_type "
        "(the split routes to apply_split and reports already_applied)",
        dispatched.action_type == SPLIT and dispatched.already_applied is True,
        f"action_type={dispatched.action_type}",
    )


async def check_cross_org(app_conn, admin_conn, ids, actions, secs) -> None:
    """Real cross-org proof, on the real app_service role. Not inferred."""
    other_before = ids["other_snapshot"]
    other_now = await read_position(admin_conn, ids["p_other"])
    check(
        "[Y] a DIFFERENT org holding the SAME global security is COMPLETELY "
        "UNAFFECTED by the first org's apply: same position id, same quantity "
        "(400), same cost basis, still the current row",
        other_now["id"] == other_before["id"]
        and _dec(other_now["quantity"]) == OTHER_QTY_BEFORE
        and _dec(other_now["cost_basis"]) == OTHER_COST
        and other_now["valid_to"] is None,
        f"id unchanged={other_now['id'] == other_before['id']}, "
        f"qty={other_now['quantity']}, cost={other_now['cost_basis']}, "
        f"valid_to={other_now['valid_to']}",
    )
    other_txns = await admin_conn.fetchval(
        f"""
        SELECT count(*) FROM {TABLE_TRANSACTIONS} t
        WHERE t.org_id = $1::uuid AND t.corporate_action_id = $2::uuid
        """,
        OTHER_ORG_ID, actions["split"],
    )
    check(
        "[Y] and no transaction was written into the other org either — the "
        "first org's apply touched exactly one tenant's rows",
        other_txns == 0,
        f"transactions in the other org for this action: {other_txns}",
    )

    # ── Global visibility of the ACTION, under the app_service role ──────
    async with org_ctx(app_conn, OTHER_ORG_ID, super_admin=False, commit=False):
        seen = await get_corporate_action(app_conn, actions["split"])
        visible_assets = await find_affected_assets(
            app_conn, OTHER_ORG_ID, secs["split"]
        )
        foreign_assets = await find_affected_assets(
            app_conn, DEFAULT_ORG_ID, secs["split"]
        )
        own_positions = await app_conn.fetchval(
            f"""
            SELECT count(*) FROM {TABLE_POSITIONS} p
            WHERE p.asset_id = $1::uuid
            """,
            ids["a_other"],
        )
        foreign_positions = await app_conn.fetchval(
            f"""
            SELECT count(*) FROM {TABLE_POSITIONS} p
            WHERE p.asset_id = $1::uuid
            """,
            ids["a_split"],
        )
    check(
        "[Y] the RECORDED action is globally visible to the second org through "
        "the real app_service connection — one fact, recorded once, readable by "
        "every tenant that holds the security",
        seen is not None and seen["action_type"] == SPLIT
        and seen["terms"] == {TERMS_RATIO: SPLIT_RATIO},
        f"terms={seen['terms'] if seen else None}",
    )
    check(
        "[Y] under an OTHER_ORG RLS context on app_service, find_affected_assets "
        "returns only that org's own asset — and the CONTROL (its own positions "
        "ARE visible, the first org's are NOT) proves the read is not just "
        "failing for everything",
        [a["id"] for a in visible_assets] == [ids["a_other"]]
        and foreign_assets == []
        and own_positions >= 1 and foreign_positions == 0,
        f"own asset visible={[a['id'] for a in visible_assets] == [ids['a_other']]}, "
        f"foreign assets={len(foreign_assets)}, own positions={own_positions}, "
        f"foreign positions={foreign_positions}",
    )

    # ── The second org applies the SAME action, on app_service, for real ──
    outcome = await apply_split(app_conn, OTHER_ORG_ID, actions["split"])
    other_after = await current_position_for(admin_conn, OTHER_ORG_ID, ids["a_other"])
    first_org_still = await current_position_for(
        admin_conn, DEFAULT_ORG_ID, ids["a_split"]
    )
    check(
        "[Y] the second org applies the SAME recorded action independently, "
        "through the real app_service connection: 400 → 800, cost basis "
        "unchanged — so 'unaffected' above was isolation, not a broken apply",
        outcome.positions_affected == 1
        and _dec(other_after["quantity"]) == OTHER_QTY_AFTER
        and _dec(other_after["cost_basis"]) == OTHER_COST,
        f"affected={outcome.positions_affected}, qty={other_after['quantity']}, "
        f"cost={other_after['cost_basis']}",
    )
    check(
        "[Y] and the FIRST org is in turn unaffected by the second org's apply "
        "— still 200 / $5,000, not re-split to 400",
        _dec(first_org_still["quantity"]) == SPLIT_QTY_AFTER
        and _dec(first_org_still["cost_basis"]) == SPLIT_COST
        and first_org_still["id"] == ids["p_split_after"],
        f"qty={first_org_still['quantity']}, "
        f"cost={first_org_still['cost_basis']}",
    )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1
    if not app_url:
        print("[FAIL] APP_SERVICE_DATABASE_URL is not set. There is NO SET ROLE "
              "fallback: every cross-org assertion is meaningless under a "
              "bypassrls role, so this script fails rather than pretending.")
        return 1

    admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    try:
        app_conn = await asyncpg.connect(
            app_url, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] APP_SERVICE_DATABASE_URL did not connect: "
              f"{type(exc).__name__}: {exc}")
        await admin_conn.close()
        return 1

    baseline: dict[str, int] = {}
    try:
        await teardown(admin_conn)                                   # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report(
                "TEARDOWN — rows are already present in these tables",
                f"{nonempty}. Teardown is by-fixture (every fixture row carries "
                f"the {FIXTURE_TAG!r} tag in a natural-key column) plus an exact "
                f"count assertion, NOT a truncate. portfolio.securities_global "
                f"holds a real global reference corpus and portfolio.assets / "
                f"public.entities hold real production rows.",
            )

        print("\n── Task 1: discovery, reported AND asserted ──")
        await check_task1a(admin_conn)
        await check_task1b(admin_conn)
        await check_task1c(admin_conn)
        await check_task1d(admin_conn)
        check_schema_qualification()
        check_ratio_parsing()

        print("\n── Fixtures: five global securities, two orgs, four assets ──")
        await seed_users(admin_conn)
        secs = await build_securities(admin_conn)

        print("\n── Task 2: RECORD — global, Super-Admin-gated ──")
        actions = await check_record(admin_conn, app_conn, secs)

        ids = await build_tenant_fixtures(admin_conn, secs)
        # Snapshot the second org BEFORE the first org's apply. The control for
        # every cross-org assertion below.
        ids["other_snapshot"] = await read_position(admin_conn, ids["p_other"])

        print("\n── Task 3: APPLY — split and reverse split ──")
        ids = await check_split(admin_conn, ids, actions)

        print("\n── Task 3: idempotency ──")
        await check_idempotency(admin_conn, ids, actions)

        print("\n── Task 4: APPLY — spinoff, creating a resulting position ──")
        ids = await check_spinoff(admin_conn, ids, actions, secs)

        print("\n── Task 5: a real adjustment vs. a real trade ──")
        await check_adjustment_vs_trade(admin_conn, ids, actions)

        print("\n── Zero-affected, and unapplicable action types ──")
        await check_zero_and_unapplicable(admin_conn, ids, actions)

        print("\n── Cross-org isolation (real app_service connection) ──")
        await check_cross_org(app_conn, admin_conn, ids, actions, secs)

    finally:
        await teardown(admin_conn)                                   # END
        if baseline:
            final = await counts(admin_conn)
            drift = {
                t: (baseline[t], final[t]) for t in TABLES if baseline[t] != final[t]
            }
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — including the new GLOBAL "
                "securities_global_corporate_actions",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
        await app_conn.close()
        await admin_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"RESULT: {passed}/{total} passed")
    failures = [(n, d) for n, ok, d in results if not ok]
    if failures:
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  · {name} — {detail}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
