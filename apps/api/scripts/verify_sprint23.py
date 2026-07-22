"""verify_sprint23.py — Sprint 23 Investment / Class restructure

Column names are taken directly from docs/schema_snapshot.sql.

Assertions:
   1. spvs.class_label column exists (nullable text).
   2. spvs_deal_class_label_uniq exists and is UNIQUE on (deal_id, class_label).
   3. First SPV under a deal may be created without a class_label.
   4. Second SPV WITHOUT a class_label is rejected by the API layer (HTTP 400).
   5. Second SPV with a DUPLICATE class_label raises (DB unique index).
   6. Second SPV with a DISTINCT class_label is accepted.
   7. Investment roll-up: total_committed == sum of both classes' commitments.
   8. Investment roll-up: per-class breakdown matches each class individually.
   9. Roll-up of called / distributed / fees / net matches the per-SPV ledger math.
  10. Roll-up numbers are Decimal, and sum exactly (no float drift).
  11. Teardown: zero leftover rows in every table this run touched.

The API-layer assertions call routers.spv.create_spv directly with the request
helpers stubbed, so what is exercised is the real handler — including its
ClassLabelError -> HTTP 400 mapping — not a re-implementation of the rule.

Schema facts used here:
  spvs: id, org_id, deal_id (NOT NULL), name, spv_status (NOT 'status'),
    carry_pct, mgmt_fee_pct, close_date, class_label
  spv_subscriptions: spv_id, entity_id (NOT NULL), commitment_amount (NOT NULL),
    funded_amount (NOT NULL DEFAULT 0), valid_to (NULL = live row)
  spv_transactions: spv_id, txn_type, txn_date, amount, status
    ('posted' rows only feed the summary), transaction_type_id (nullable —
    NULL rows fall back to legacy txn_type matching)
  spv_status_history: spv_id, to_status (NOT NULL), changed_by
  deals: id, org_id, name — roll-up reads require valid_to/system_to IS NULL
  users: id, org_id (NOT NULL), auth0_sub, email, role
  entities: id, org_id, display_name (NOT NULL), entity_type
"""
import asyncio
import os
import sys
import uuid
from datetime import date
from decimal import Decimal

import asyncpg

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from fastapi import HTTPException  # noqa: E402

import routers.spv as spv_router  # noqa: E402
from schemas.spv import SPVCreate  # noqa: E402
from services.spv_classes import suggest_next_label  # noqa: E402
from services.spv_rollup import deal_rollup, spv_totals  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")

TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
ORG_ID = "00000000-0000-0000-0000-000000000001"

PASS = "\033[32m[Y]\033[0m"
FAIL = "\033[31m[N]\033[0m"

results = []


def record(label, ok, note=""):
    results.append((label, ok, note))
    icon = PASS if ok else FAIL
    suffix = f"  ({note})" if note else ""
    print(f"  {icon} {label}{suffix}")


class _FakePool:
    """Minimal stand-in for the asyncpg pool: hands back the one test conn."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _install_router_stubs(conn):
    """Point routers.spv at the test connection; return the originals."""
    pool = _FakePool(conn)

    async def _get_pool():
        return pool

    async def _ensure_user(_conn, _request):
        return TEST_USER_ID

    async def _write_audit_log(*_args, **_kwargs):
        return None

    original = {
        "get_pool": spv_router.get_pool,
        "ensure_user": spv_router.ensure_user,
        "get_org_id": spv_router.get_org_id,
        "require_permission": spv_router.require_permission,
        "write_audit_log": spv_router.write_audit_log,
    }
    spv_router.get_pool = _get_pool
    spv_router.ensure_user = _ensure_user
    spv_router.get_org_id = lambda _request: ORG_ID
    spv_router.require_permission = lambda _request, _perm: None
    spv_router.write_audit_log = _write_audit_log
    return original


def _restore_router_stubs(original):
    for name, fn in original.items():
        setattr(spv_router, name, fn)


async def main():
    if not DATABASE_URL:
        print("[N] SKIP — DATABASE_URL not set")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    deal_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    spv_ids: list[str] = []
    original_stubs = None

    # ── Teardown-at-start: a previous crashed run must not colour this one.
    async def teardown():
        if spv_ids:
            await conn.execute(
                "DELETE FROM spv_transactions WHERE spv_id = ANY($1::uuid[])", spv_ids
            )
            await conn.execute(
                "DELETE FROM spv_subscriptions WHERE spv_id = ANY($1::uuid[])", spv_ids
            )
            await conn.execute(
                "DELETE FROM spv_status_history WHERE spv_id = ANY($1::uuid[])", spv_ids
            )
        # SPVs are found by deal, so a run that died before recording an id
        # still gets cleaned up.
        await conn.execute("DELETE FROM spv_transactions WHERE spv_id IN "
                           "(SELECT id FROM spvs WHERE deal_id = $1::uuid)", deal_id)
        await conn.execute("DELETE FROM spv_subscriptions WHERE spv_id IN "
                           "(SELECT id FROM spvs WHERE deal_id = $1::uuid)", deal_id)
        await conn.execute("DELETE FROM spv_status_history WHERE spv_id IN "
                           "(SELECT id FROM spvs WHERE deal_id = $1::uuid)", deal_id)
        await conn.execute("DELETE FROM spvs WHERE deal_id = $1::uuid", deal_id)
        await conn.execute("DELETE FROM deals WHERE id = $1::uuid", deal_id)
        await conn.execute("DELETE FROM entities WHERE id = $1::uuid", entity_id)
        await conn.execute("DELETE FROM users WHERE id = $1::uuid", TEST_USER_ID)

    await teardown()

    try:
        # ── Fixtures ───────────────────────────────────────────────────────
        await conn.execute(
            """
            INSERT INTO users (id, org_id, auth0_sub, email, role)
            VALUES ($1::uuid, $2::uuid, 'auth0|test_verify_sprint23',
                    'test_sprint23@example.com', 'staff')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            TEST_USER_ID, ORG_ID,
        )
        await conn.execute(
            """
            INSERT INTO deals (id, org_id, name)
            VALUES ($1::uuid, $2::uuid, 'Test Class Investment Sprint23')
            ON CONFLICT (id) DO NOTHING
            """,
            deal_id, ORG_ID,
        )
        await conn.execute(
            """
            INSERT INTO entities (id, org_id, display_name, entity_type)
            VALUES ($1::uuid, $2::uuid, 'Test Subscriber Sprint23', 'individual')
            ON CONFLICT (id) DO NOTHING
            """,
            entity_id, ORG_ID,
        )

        # ── Assertion 1: class_label column ────────────────────────────────
        col = await conn.fetchrow(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'spvs'
              AND column_name = 'class_label'
            """
        )
        record(
            "spvs.class_label exists (nullable text)",
            col is not None and col["data_type"] == "text" and col["is_nullable"] == "YES",
            f"{dict(col) if col else 'missing'}",
        )

        # ── Assertion 2: unique index on (deal_id, class_label) ────────────
        idx = await conn.fetchrow(
            """
            SELECT i.indisunique,
                   array_agg(a.attname ORDER BY k.ord) AS cols
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE c.relname = 'spvs_deal_class_label_uniq'
            GROUP BY i.indisunique
            """
        )
        idx_ok = (
            idx is not None
            and idx["indisunique"]
            and list(idx["cols"]) == ["deal_id", "class_label"]
        )
        record(
            "spvs_deal_class_label_uniq is UNIQUE on (deal_id, class_label)",
            idx_ok,
            f"cols={list(idx['cols']) if idx else 'missing'}",
        )

        # ── API-layer assertions ───────────────────────────────────────────
        original_stubs = _install_router_stubs(conn)
        request = object()  # stubs ignore it

        # Assertion 3: first SPV, no class_label — allowed.
        spv_a = await spv_router.create_spv(
            request,
            SPVCreate(
                name="Sprint23 Test SPV",
                deal_id=uuid.UUID(deal_id),
                carry_pct=20,
                mgmt_fee_pct=2,
                close_date=date(2026, 9, 30),
            ),
        )
        spv_ids.append(str(spv_a.id))
        record(
            "First SPV under a deal accepted without class_label",
            spv_a.class_label is None,
            f"class_label={spv_a.class_label!r}",
        )

        # Assertion 4: second SPV without class_label — rejected with 400.
        rejected_status = None
        rejected_detail = ""
        try:
            await spv_router.create_spv(
                request,
                SPVCreate(name="Sprint23 Test SPV II", deal_id=uuid.UUID(deal_id)),
            )
        except HTTPException as exc:
            rejected_status = exc.status_code
            rejected_detail = str(exc.detail)
        record(
            "Second SPV WITHOUT class_label rejected by API (400)",
            rejected_status == 400 and "class label" in rejected_detail.lower(),
            f"status={rejected_status}",
        )

        # A blank / whitespace-only label must not slip past the guard either.
        blank_status = None
        try:
            await spv_router.create_spv(
                request,
                SPVCreate(
                    name="Sprint23 Test SPV Blank",
                    deal_id=uuid.UUID(deal_id),
                    class_label="   ",
                ),
            )
        except HTTPException as exc:
            blank_status = exc.status_code
        record(
            "Second SPV with blank class_label rejected by API (400)",
            blank_status == 400,
            f"status={blank_status}",
        )

        # Backfill Class A onto the first SPV so the duplicate test has a target.
        await conn.execute(
            "UPDATE spvs SET class_label = 'A' WHERE id = $1::uuid", spv_a.id
        )

        # Assertion 5: duplicate class_label under the same deal — DB raises.
        dup_raised = False
        dup_detail = ""
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO spvs (org_id, deal_id, name, spv_status, class_label)
                    VALUES ($1::uuid, $2::uuid, 'Sprint23 Dup Class', 'forming', 'A')
                    """,
                    ORG_ID, deal_id,
                )
        except asyncpg.UniqueViolationError as exc:
            dup_raised = True
            dup_detail = getattr(exc, "constraint_name", "") or "unique violation"
        record(
            "Duplicate class_label on same deal_id raises (DB constraint)",
            dup_raised,
            dup_detail,
        )

        # Assertion 6: distinct class_label — accepted.
        suggested = suggest_next_label(["A"])
        spv_b = await spv_router.create_spv(
            request,
            SPVCreate(
                name="Sprint23 Test SPV",
                deal_id=uuid.UUID(deal_id),
                class_label=suggested,
                carry_pct=15,
                mgmt_fee_pct=1,
                close_date=date(2026, 12, 31),
            ),
        )
        spv_ids.append(str(spv_b.id))
        record(
            "Second SPV with distinct class_label accepted",
            spv_b.class_label == "B" and suggested == "B",
            f"suggested={suggested} stored={spv_b.class_label!r}",
        )

        _restore_router_stubs(original_stubs)
        original_stubs = None

        # ── Roll-up fixtures: cents that a float sum would not preserve ────
        commit_a = Decimal("1000000.10")
        commit_b = Decimal("2000000.20")
        expected_committed = commit_a + commit_b  # 3000000.30

        for sid, amount in ((spv_a.id, commit_a), (spv_b.id, commit_b)):
            await conn.execute(
                """
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                     subscription_status)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, 'signed')
                """,
                ORG_ID, sid, entity_id, amount, amount / 2,
            )

        # Posted transactions — transaction_type_id NULL exercises the legacy
        # txn_type fallback the Sprint 14 summary still supports.
        called_a, dist_a, fee_a = Decimal("400000.05"), Decimal("50000.01"), Decimal("2500.07")
        called_b, dist_b, fee_b = Decimal("800000.03"), Decimal("70000.09"), Decimal("1500.11")
        txns = [
            (spv_a.id, "capital_call", called_a),
            (spv_a.id, "distribution", dist_a),
            (spv_a.id, "fee", fee_a),
            (spv_b.id, "capital_call", called_b),
            (spv_b.id, "distribution", dist_b),
            (spv_b.id, "fee", fee_b),
        ]
        for sid, txn_type, amount in txns:
            await conn.execute(
                """
                INSERT INTO spv_transactions
                    (org_id, spv_id, txn_type, txn_date, amount, status, posted_at)
                VALUES ($1::uuid, $2::uuid, $3, '2026-05-01', $4, 'posted', now())
                """,
                ORG_ID, sid, txn_type, amount,
            )
        # A draft transaction must be excluded from every total.
        await conn.execute(
            """
            INSERT INTO spv_transactions
                (org_id, spv_id, txn_type, txn_date, amount, status)
            VALUES ($1::uuid, $2::uuid, 'capital_call', '2026-05-02', 999999.99, 'draft')
            """,
            ORG_ID, spv_a.id,
        )

        rollup = await deal_rollup(conn, ORG_ID, uuid.UUID(deal_id))
        totals = rollup["totals"]
        by_label = {c["class_label"]: c for c in rollup["classes"]}

        # ── Assertion 7: investment-level committed ────────────────────────
        record(
            "Roll-up total_committed == sum of both classes",
            totals["total_committed"] == expected_committed
            and rollup["class_count"] == 2,
            f"{totals['total_committed']} == {expected_committed}",
        )

        # ── Assertion 8: per-class breakdown ───────────────────────────────
        per_class_ok = (
            set(by_label) == {"A", "B"}
            and by_label["A"]["total_committed"] == commit_a
            and by_label["B"]["total_committed"] == commit_b
            and by_label["A"]["carry_pct"] == Decimal("20")
            and by_label["B"]["carry_pct"] == Decimal("15")
            and by_label["A"]["close_date"] == date(2026, 9, 30)
            and by_label["B"]["close_date"] == date(2026, 12, 31)
        )
        record(
            "Per-class breakdown matches each class individually",
            per_class_ok,
            f"A={by_label.get('A', {}).get('total_committed')} "
            f"B={by_label.get('B', {}).get('total_committed')}",
        )

        # ── Assertion 9: called / distributed / fees / net ─────────────────
        expected_called = called_a + called_b
        expected_dist = dist_a + dist_b
        expected_fees = fee_a + fee_b
        expected_net = expected_called - expected_dist - expected_fees
        cash_ok = (
            totals["total_called"] == expected_called
            and totals["total_distributed"] == expected_dist
            and totals["total_fees"] == expected_fees
            and totals["net"] == expected_net
        )
        record(
            "Roll-up called/distributed/fees/net aggregate correctly "
            "(draft txn excluded)",
            cash_ok,
            f"called={totals['total_called']} net={totals['net']}",
        )

        # The aggregate must equal the sum of the per-SPV ledger math, since
        # both come from services.spv_rollup.
        ledger_a = await spv_totals(conn, ORG_ID, spv_a.id)
        ledger_b = await spv_totals(conn, ORG_ID, spv_b.id)
        record(
            "Roll-up equals sum of the per-SPV ledger totals",
            ledger_a["total_called"] + ledger_b["total_called"] == totals["total_called"]
            and ledger_a["net"] + ledger_b["net"] == totals["net"],
            f"{ledger_a['net']} + {ledger_b['net']} == {totals['net']}",
        )

        # ── Assertion 10: Decimal precision, no float drift ────────────────
        all_money = [v for k, v in totals.items()]
        types_ok = all(isinstance(v, Decimal) for v in all_money)
        # Exact-cents equality is the real test: the float route drifts here.
        float_sum = float(commit_a) + float(commit_b)
        exact_ok = totals["total_committed"] == Decimal("3000000.30")
        record(
            "Roll-up totals are Decimal and exact to the cent",
            types_ok and exact_ok,
            f"decimal={totals['total_committed']} float={float_sum!r}",
        )

    finally:
        if original_stubs is not None:
            _restore_router_stubs(original_stubs)

        teardown_failed = False
        try:
            await teardown()
        except Exception as te:
            teardown_failed = True
            print(f"\n  [teardown] FATAL: {te}", file=sys.stderr)

        # ── Assertion 11: zero leftover rows in every table touched ────────
        try:
            counts = {}
            counts["spvs"] = await conn.fetchval(
                "SELECT count(*) FROM spvs WHERE deal_id = $1::uuid", deal_id
            )
            for table in ("spv_transactions", "spv_subscriptions", "spv_status_history"):
                counts[table] = await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE spv_id = ANY($1::uuid[])",
                    spv_ids or [str(uuid.uuid4())],
                )
            counts["deals"] = await conn.fetchval(
                "SELECT count(*) FROM deals WHERE id = $1::uuid", deal_id
            )
            counts["entities"] = await conn.fetchval(
                "SELECT count(*) FROM entities WHERE id = $1::uuid", entity_id
            )
            counts["users"] = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = $1::uuid", TEST_USER_ID
            )

            # No trigger or constraint should have been altered by this run:
            # confirm the unique index is still present and every trigger on
            # spvs is enabled ('O').
            idx_still_there = await conn.fetchval(
                "SELECT count(*) FROM pg_class WHERE relname = 'spvs_deal_class_label_uniq'"
            )
            trigger_rows = await conn.fetch(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid = 'public.spvs'::regclass AND tgisinternal = false"
            )

            def _tg_enabled(v) -> str:
                # asyncpg returns pg "char" as bytes; normalise to str.
                return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)

            triggers_ok = all(_tg_enabled(r["tgenabled"]) == "O" for r in trigger_rows)
            clean_ok = (
                all(c == 0 for c in counts.values())
                and idx_still_there == 1
                and triggers_ok
                and not teardown_failed
            )
            record(
                "Teardown: zero leftover rows; index and triggers intact",
                clean_ok,
                ", ".join(f"{k}={v}" for k, v in counts.items())
                + f", index={idx_still_there}, triggers_enabled={triggers_ok}",
            )
        except Exception as te:
            record("Teardown: zero leftover rows; index and triggers intact", False, str(te))

        await conn.close()

        if teardown_failed:
            print(
                "\n  [FATAL] Teardown incomplete — test data left in database. "
                "Fix manually before re-running.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # ── Summary ────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  {passed}/{total} assertions passed")
    if passed < total:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
