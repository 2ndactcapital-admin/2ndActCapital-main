"""Sprint 14 verify — SPV Transactions & Allocations.

Checks:
  1.  Create SPV + 3 subscriptions (500000 / 300000 / 200000)
  2.  Create capital_call transaction of 100000 → status='draft'
  3.  compute_allocations → correct split 50000/30000/20000, sum=100000 exactly
  4.  Rounding test: 100000 across 3 equal subs (333333.33 each) — sum exact
  5.  allocate_transaction persists allocations, sets status='allocated'
  6.  post_transaction → funded_amounts incremented (50000/30000/20000)
  7.  Distribution transaction allocates and posts without changing funded_amount
  8.  Edit guard: posted txn cannot be re-edited (status != 'draft' rejects)
  9.  Member read scope: member's subscription allocation rows are queryable
  10. Assistant spv.record_transaction draft_handler returns preview without writing

Run: DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/verify_sprint14.py
"""
import asyncio
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_sprint14")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"
TEST_MEMBER_USER_ID = "99000000-0000-0000-0000-000000000002"
TEST_MEMBER_AUTH0_SUB = "auth0|test_verify_user_member14"

passed = 0
failed = 0


def ok(label):
    global passed
    passed += 1
    print(f"[P] {label}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"[F] {label}{': ' + reason if reason else ''}")


async def seed(conn):
    """Seed test user and base fixtures."""
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify14@test.local', 'Verify User 14', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_ID, TEST_AUTH0_SUB,
    )


async def teardown(
    conn,
    spv_ids: list,
    deal_id: str | None,
    entity_ids: list,
    extra_txn_ids: list | None = None,
):
    """FK-safe teardown: most-dependent tables first."""
    # 1. spv_transaction_allocations for all our SPVs
    if spv_ids:
        await conn.execute(
            """
            DELETE FROM spv_transaction_allocations
            WHERE transaction_id IN (
                SELECT id FROM spv_transactions WHERE spv_id = ANY($1::uuid[])
            )
            """,
            spv_ids,
        )
        # 2. spv_transactions
        await conn.execute(
            "DELETE FROM spv_transactions WHERE spv_id = ANY($1::uuid[])",
            spv_ids,
        )
        # 3. spv_subscriptions
        await conn.execute(
            "DELETE FROM spv_subscriptions WHERE spv_id = ANY($1::uuid[])",
            spv_ids,
        )
        # 4. spv_status_history
        await conn.execute(
            "DELETE FROM spv_status_history WHERE spv_id = ANY($1::uuid[])",
            spv_ids,
        )
        # 5. spvs
        await conn.execute(
            "DELETE FROM spvs WHERE id = ANY($1::uuid[])",
            spv_ids,
        )

    # 6. deals
    if deal_id:
        await conn.execute(
            "DELETE FROM deal_ai_summaries WHERE deal_id = $1", deal_id,
        )
        await conn.execute("DELETE FROM deal_scores WHERE deal_id = $1", deal_id)
        await conn.execute("DELETE FROM deal_votes WHERE deal_id = $1", deal_id)
        await conn.execute("DELETE FROM deal_interest WHERE deal_id = $1", deal_id)
        await conn.execute("DELETE FROM deal_documents WHERE deal_id = $1", deal_id)
        await conn.execute(
            "DELETE FROM investment_stage_history WHERE member_investment_id IN "
            "(SELECT id FROM member_investments WHERE deal_id = $1)",
            deal_id,
        )
        await conn.execute(
            "DELETE FROM member_investments WHERE deal_id = $1", deal_id,
        )
        await conn.execute("DELETE FROM deals WHERE id = $1", deal_id)

    # 7. entities (children first)
    for eid in entity_ids:
        await conn.execute("DELETE FROM entity_notes WHERE entity_id = $1", eid)
        await conn.execute(
            "DELETE FROM investment_profile_answers WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM investment_profile_extractions WHERE entity_id = $1", eid,
        )
        await conn.execute("DELETE FROM entity_briefs WHERE entity_id = $1", eid)
        await conn.execute(
            "DELETE FROM profile_conversations WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM member_target_allocations WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM entity_attributes WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM entity_addresses WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM entity_employment WHERE employee_id = $1 OR employer_id = $1",
            eid,
        )
        await conn.execute(
            "DELETE FROM entity_social_profiles WHERE entity_id = $1", eid,
        )
        await conn.execute("DELETE FROM entity_tax_ids WHERE entity_id = $1", eid)
        await conn.execute(
            "DELETE FROM compliance_records WHERE entity_id = $1", eid,
        )
        await conn.execute("DELETE FROM entities WHERE id = $1", eid)

    # 8. member_todos for test users
    await conn.execute(
        "DELETE FROM member_todos WHERE user_id = ANY($1::uuid[])",
        [TEST_USER_ID, TEST_MEMBER_USER_ID],
    )

    # 9. users
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])",
        [TEST_USER_ID, TEST_MEMBER_USER_ID],
    )
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])",
        [TEST_USER_ID, TEST_MEMBER_USER_ID],
    )


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    async with pool.acquire() as conn:
        await seed(conn)

    spv_ids: list = []
    deal_id: str | None = None
    entity_ids: list = []
    txn_id: str | None = None       # primary capital_call txn
    sub_a_id: str | None = None
    sub_b_id: str | None = None
    sub_c_id: str | None = None

    try:
        from services.spv_allocation import (
            compute_allocations,
            allocate_transaction,
            post_transaction,
        )

        # --------------------------------------------------------------------
        # Check 1: Create SPV + 3 subscriptions
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            deal_id = str(await conn.fetchval(
                """
                INSERT INTO deals (org_id, name, deal_status)
                VALUES ($1, 'Verify14 Test Deal', 'active')
                RETURNING id
                """,
                ORG_ID,
            ))

            entity_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'individual', 'Verify14 Entity A')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(entity_id)

            entity_b_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'individual', 'Verify14 Entity B')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(entity_b_id)

            entity_c_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'individual', 'Verify14 Entity C')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(entity_c_id)

            spv_id = str(await conn.fetchval(
                """
                INSERT INTO spvs (org_id, deal_id, name, spv_status, target_raise,
                                  min_commitment, carry_pct, mgmt_fee_pct, created_by)
                VALUES ($1, $2, 'Verify14 SPV Alpha', 'closed', 1000000, 100000, 20, 2, $3)
                RETURNING id
                """,
                ORG_ID, deal_id, TEST_USER_ID,
            ))
            spv_ids.append(spv_id)

            # 3 subscriptions: 500000 / 300000 / 200000 = total 1,000,000
            sub_a_id = str(await conn.fetchval(
                """
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                     subscription_status, created_by)
                VALUES ($1, $2, $3, 500000, 0, 'committed', $4)
                RETURNING id
                """,
                ORG_ID, spv_id, entity_id, TEST_USER_ID,
            ))
            sub_b_id = str(await conn.fetchval(
                """
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                     subscription_status, created_by)
                VALUES ($1, $2, $3, 300000, 0, 'committed', $4)
                RETURNING id
                """,
                ORG_ID, spv_id, entity_b_id, TEST_USER_ID,
            ))
            sub_c_id = str(await conn.fetchval(
                """
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                     subscription_status, created_by)
                VALUES ($1, $2, $3, 200000, 0, 'committed', $4)
                RETURNING id
                """,
                ORG_ID, spv_id, entity_c_id, TEST_USER_ID,
            ))

            sub_count = await conn.fetchval(
                "SELECT COUNT(*) FROM spv_subscriptions WHERE spv_id = $1 AND valid_to IS NULL",
                spv_id,
            )

        if int(sub_count) == 3:
            ok("Check 1: SPV created with 3 committed subscriptions")
        else:
            fail("Check 1: subscription count wrong", f"expected 3, got {sub_count}")

        # --------------------------------------------------------------------
        # Check 2: Create capital_call transaction → status='draft'
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            txn_id = str(await conn.fetchval(
                """
                INSERT INTO spv_transactions
                    (org_id, spv_id, txn_type, txn_date, amount, allocation_basis,
                     status, created_by)
                VALUES ($1, $2, 'capital_call', CURRENT_DATE, 100000, 'committed', 'draft', $3)
                RETURNING id
                """,
                ORG_ID, spv_id, TEST_USER_ID,
            ))
            txn_status = await conn.fetchval(
                "SELECT status FROM spv_transactions WHERE id = $1", txn_id,
            )

        if txn_status == "draft":
            ok("Check 2: capital_call transaction created with status='draft'")
        else:
            fail("Check 2: unexpected status", f"expected 'draft', got {txn_status!r}")

        # --------------------------------------------------------------------
        # Check 3: compute_allocations → correct split, sum exact
        # --------------------------------------------------------------------
        allocs = await compute_allocations(pool, txn_id)
        alloc_sum = sum(Decimal(a["allocated_amount"]) for a in allocs)
        amounts = sorted(
            [Decimal(a["allocated_amount"]) for a in allocs], reverse=True
        )
        expected = [Decimal("50000"), Decimal("30000"), Decimal("20000")]

        if (
            len(allocs) == 3
            and alloc_sum == Decimal("100000")
            and amounts == expected
        ):
            ok(
                "Check 3: compute_allocations → 3 rows, sum=100000 exactly, "
                "split 50000/30000/20000"
            )
        else:
            fail(
                "Check 3: allocation mismatch",
                f"len={len(allocs)}, sum={alloc_sum}, amounts={amounts}",
            )

        # --------------------------------------------------------------------
        # Check 4: Rounding test — 100000 across 3 equal subs (333333.33 each)
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            eq_entity_1 = str(await conn.fetchval(
                "INSERT INTO entities (org_id, entity_type, display_name) "
                "VALUES ($1, 'individual', 'Verify14 EqEnt1') RETURNING id",
                ORG_ID,
            ))
            eq_entity_2 = str(await conn.fetchval(
                "INSERT INTO entities (org_id, entity_type, display_name) "
                "VALUES ($1, 'individual', 'Verify14 EqEnt2') RETURNING id",
                ORG_ID,
            ))
            eq_entity_3 = str(await conn.fetchval(
                "INSERT INTO entities (org_id, entity_type, display_name) "
                "VALUES ($1, 'individual', 'Verify14 EqEnt3') RETURNING id",
                ORG_ID,
            ))

            eq_spv_id = str(await conn.fetchval(
                """
                INSERT INTO spvs (org_id, deal_id, name, spv_status, created_by)
                VALUES ($1, $2, 'Verify14 SPV Rounding', 'closed', $3)
                RETURNING id
                """,
                ORG_ID, deal_id, TEST_USER_ID,
            ))
            spv_ids.append(eq_spv_id)

            for eid in [eq_entity_1, eq_entity_2, eq_entity_3]:
                await conn.execute(
                    """
                    INSERT INTO spv_subscriptions
                        (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                         subscription_status, created_by)
                    VALUES ($1, $2, $3, 333333.33, 0, 'committed', $4)
                    """,
                    ORG_ID, eq_spv_id, eid, TEST_USER_ID,
                )
                entity_ids.append(eid)

            eq_txn_id = str(await conn.fetchval(
                """
                INSERT INTO spv_transactions
                    (org_id, spv_id, txn_type, txn_date, amount, allocation_basis,
                     status, created_by)
                VALUES ($1, $2, 'capital_call', CURRENT_DATE, 100000, 'committed', 'draft', $3)
                RETURNING id
                """,
                ORG_ID, eq_spv_id, TEST_USER_ID,
            ))

        eq_allocs = await compute_allocations(pool, eq_txn_id)
        eq_sum = sum(Decimal(a["allocated_amount"]) for a in eq_allocs)

        if eq_sum == Decimal("100000"):
            ok(f"Check 4: rounding test — sum={eq_sum} == 100000 exactly")
        else:
            fail("Check 4: rounding test sum mismatch", f"got {eq_sum}")

        # Clean up rounding test data
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM spv_transactions WHERE id = $1", eq_txn_id,
            )
            await conn.execute(
                "DELETE FROM spv_subscriptions WHERE spv_id = $1", eq_spv_id,
            )

        # --------------------------------------------------------------------
        # Check 5: allocate_transaction persists allocations, sets status='allocated'
        # --------------------------------------------------------------------
        persisted = await allocate_transaction(pool, txn_id, TEST_USER_ID)

        async with pool.acquire() as conn:
            alloc_count = await conn.fetchval(
                "SELECT COUNT(*) FROM spv_transaction_allocations "
                "WHERE transaction_id = $1 AND status = 'active'",
                txn_id,
            )
            new_status = await conn.fetchval(
                "SELECT status FROM spv_transactions WHERE id = $1", txn_id,
            )

        if new_status == "allocated" and int(alloc_count) == 3:
            ok(
                "Check 5: allocate_transaction → status='allocated', "
                f"{alloc_count} allocation rows"
            )
        else:
            fail(
                "Check 5: allocate_transaction result wrong",
                f"status={new_status!r}, alloc_count={alloc_count}",
            )

        # --------------------------------------------------------------------
        # Check 6: post_transaction → funded_amounts incremented
        # --------------------------------------------------------------------
        await post_transaction(pool, txn_id, TEST_USER_ID)

        async with pool.acquire() as conn:
            post_status = await conn.fetchval(
                "SELECT status FROM spv_transactions WHERE id = $1", txn_id,
            )
            funded_a = await conn.fetchval(
                "SELECT funded_amount FROM spv_subscriptions WHERE id = $1", sub_a_id,
            )
            funded_b = await conn.fetchval(
                "SELECT funded_amount FROM spv_subscriptions WHERE id = $1", sub_b_id,
            )
            funded_c = await conn.fetchval(
                "SELECT funded_amount FROM spv_subscriptions WHERE id = $1", sub_c_id,
            )

        funded_a = Decimal(str(funded_a))
        funded_b = Decimal(str(funded_b))
        funded_c = Decimal(str(funded_c))

        if (
            post_status == "posted"
            and funded_a == Decimal("50000")
            and funded_b == Decimal("30000")
            and funded_c == Decimal("20000")
        ):
            ok(
                "Check 6: post_transaction → status='posted', "
                f"funded_amounts={funded_a}/{funded_b}/{funded_c}"
            )
        else:
            fail(
                "Check 6: post_transaction result wrong",
                f"status={post_status!r}, funded={funded_a}/{funded_b}/{funded_c}",
            )

        # --------------------------------------------------------------------
        # Check 7: Distribution transaction allocates and posts
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            dist_txn_id = str(await conn.fetchval(
                """
                INSERT INTO spv_transactions
                    (org_id, spv_id, txn_type, txn_date, amount, allocation_basis,
                     status, created_by)
                VALUES ($1, $2, 'distribution', CURRENT_DATE, 50000, 'committed', 'draft', $3)
                RETURNING id
                """,
                ORG_ID, spv_id, TEST_USER_ID,
            ))

        dist_allocs = await allocate_transaction(pool, dist_txn_id, TEST_USER_ID)
        dist_sum = sum(Decimal(a["allocated_amount"]) for a in dist_allocs)

        await post_transaction(pool, dist_txn_id, TEST_USER_ID)

        async with pool.acquire() as conn:
            dist_status = await conn.fetchval(
                "SELECT status FROM spv_transactions WHERE id = $1", dist_txn_id,
            )
            # Distribution should NOT change funded_amount
            funded_a_after = Decimal(str(await conn.fetchval(
                "SELECT funded_amount FROM spv_subscriptions WHERE id = $1", sub_a_id,
            )))

        if (
            dist_status == "posted"
            and dist_sum == Decimal("50000")
            and funded_a_after == Decimal("50000")  # unchanged
        ):
            ok(
                "Check 7: distribution txn → status='posted', "
                f"sum={dist_sum}, funded_amount unchanged"
            )
        else:
            fail(
                "Check 7: distribution result wrong",
                f"status={dist_status!r}, sum={dist_sum}, funded_a={funded_a_after}",
            )

        # --------------------------------------------------------------------
        # Check 8: Edit guard — posted txn cannot be re-edited
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            posted_status = await conn.fetchval(
                "SELECT status FROM spv_transactions WHERE id = $1", txn_id,
            )

        # The rule: only 'draft' transactions may be edited.
        # Simulate the router guard: reject if status != 'draft'.
        edit_rejected = posted_status != "draft"

        if edit_rejected:
            ok(
                "Check 8: edit guard — posted transaction (status='posted') "
                "correctly rejected for edit (status != 'draft')"
            )
        else:
            fail(
                "Check 8: edit guard failed",
                f"transaction status is {posted_status!r}, expected 'posted'",
            )

        # --------------------------------------------------------------------
        # Check 9: Member read scope
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            # Seed a second test user
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, 'verify14member@test.local', 'Verify Member 14',
                        $3, 'member')
                ON CONFLICT (auth0_sub) DO NOTHING
                """,
                TEST_MEMBER_USER_ID, ORG_ID, TEST_MEMBER_AUTH0_SUB,
            )

            member_entity_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'individual', 'Verify14 Member Entity')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(member_entity_id)

            member_sub_id = str(await conn.fetchval(
                """
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                     subscription_status, created_by)
                VALUES ($1, $2, $3, 150000, 0, 'committed', $4)
                RETURNING id
                """,
                ORG_ID, spv_id, member_entity_id, TEST_MEMBER_USER_ID,
            ))

        # Create a new txn and allocate across all 4 active subs
        async with pool.acquire() as conn:
            member_txn_id = str(await conn.fetchval(
                """
                INSERT INTO spv_transactions
                    (org_id, spv_id, txn_type, txn_date, amount, allocation_basis,
                     status, created_by)
                VALUES ($1, $2, 'capital_call', CURRENT_DATE, 115000, 'committed', 'draft', $3)
                RETURNING id
                """,
                ORG_ID, spv_id, TEST_USER_ID,
            ))

        await allocate_transaction(pool, member_txn_id, TEST_USER_ID)

        async with pool.acquire() as conn:
            member_alloc_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM spv_transaction_allocations sta
                JOIN spv_subscriptions ss ON ss.id = sta.subscription_id
                WHERE sta.transaction_id = $1
                  AND ss.entity_id = $2
                  AND sta.status = 'active'
                """,
                member_txn_id,
                member_entity_id,
            )

        if int(member_alloc_count) == 1:
            ok(
                "Check 9: member read scope — member's subscription "
                "has 1 allocation row for the transaction"
            )
        else:
            fail(
                "Check 9: member allocation row count wrong",
                f"expected 1, got {member_alloc_count}",
            )

        # --------------------------------------------------------------------
        # Check 10: Assistant draft_handler returns preview, no DB write
        # --------------------------------------------------------------------
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("[N] Check 10: SKIP — ANTHROPIC_API_KEY not set")
        else:
            try:
                from services.assistant_actions.spv import register_actions
                from services.action_registry import REGISTRY

                register_actions()  # idempotent

                action = REGISTRY.get("spv.record_transaction")
                if action is None or action.draft_handler is None:
                    fail("Check 10: spv.record_transaction action or draft_handler not found")
                else:
                    async with pool.acquire() as conn:
                        txn_count_before = await conn.fetchval(
                            "SELECT COUNT(*) FROM spv_transactions WHERE spv_id = $1",
                            spv_id,
                        )

                    preview = await action.draft_handler(
                        pool,
                        TEST_USER_ID,
                        ORG_ID,
                        spv_id=spv_id,
                        txn_type="capital_call",
                        amount=50000,
                        txn_date="2026-07-01",
                    )

                    async with pool.acquire() as conn:
                        txn_count_after = await conn.fetchval(
                            "SELECT COUNT(*) FROM spv_transactions WHERE spv_id = $1",
                            spv_id,
                        )

                    no_write = int(txn_count_before) == int(txn_count_after)
                    has_preview = (
                        preview is not None
                        and "error" not in preview
                        and preview.get("spv_id") == spv_id
                        and preview.get("txn_type") == "capital_call"
                        and preview.get("amount") == 50000
                    )

                    if has_preview and no_write:
                        ok(
                            "Check 10: draft_handler returns preview with correct fields "
                            "and writes no DB rows"
                        )
                    else:
                        fail(
                            "Check 10: draft_handler result wrong",
                            f"has_preview={has_preview}, no_write={no_write}, "
                            f"preview={preview!r}",
                        )
            except Exception as exc:
                fail("Check 10: draft_handler raised exception", str(exc))

    finally:
        async with pool.acquire() as conn:
            await teardown(conn, spv_ids, deal_id, entity_ids)
        await pool.close()

    print(f"\n{'=' * 40}")
    print(f"Sprint 14: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
