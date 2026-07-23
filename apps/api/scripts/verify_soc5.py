"""SOC Phase 5 verify — Trading authority tiers + maker-checker.

Exercises services.trading_authority directly (SOC structural pattern: the
enforcement is a standalone importable service, HELD for wiring into the
assistant confirm flow at manual review). Also proves the database-level
guards: the trading_authority_grants tier CHECK and the
assistant_activities_maker_checker_chk (approved_by <> proposed_by) CHECK.

TASK 1 DISCOVERY (reported, not assumed):
  The design brief's Altruist/custodian money-movement subsystem with a
  Tier-1 status enum (proposed->approved->dispatched->awaiting-client-consent->
  acknowledged-at-custodian->settled/rejected) DOES NOT EXIST in this codebase.
  The real thing that already handles custodian-style write-back actions is the
  assistant WRITE-action pipeline: a member proposes via POST /assistant/message
  and executes via POST /assistant/confirm, which writes an assistant_activities
  row (real statuses: awaiting_review / done / undone). The money-moving actions
  there are spv.record_transaction and spv.subscribe. Before this phase there was
  NO maker-checker anywhere and trading_authority_grants was referenced by zero
  code. This phase adds proposed_by/approved_by/entity_id to assistant_activities
  (the real ledger), a maker-checker CHECK, and the enforcement engine.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable identifiers).

Run: DATABASE_URL=... python scripts/verify_soc5.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soc5")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test users (deleted by exact id at teardown).
U_INQUIRY = "99000000-0000-0000-0000-0000000005a1"   # tier 'inquiry'
U_LIMITED = "99000000-0000-0000-0000-0000000005a2"   # tier 'limited'
U_FULL = "99000000-0000-0000-0000-0000000005a3"      # tier 'full' (proposer)
U_APPROVER = "99000000-0000-0000-0000-0000000005a4"  # tier 'full' (checker)
ALL_TEST_USERS = [U_INQUIRY, U_LIMITED, U_FULL, U_APPROVER]

TEST_ENTITY_PREFIX = "SOC5Verify"

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


async def cleanup(conn):
    """Remove all test data by stable identifiers. Idempotent, FK-safe order."""
    ent_filter = TEST_ENTITY_PREFIX + "%"
    # Money-movement ledger rows first (reference entities + users).
    await conn.execute(
        """
        DELETE FROM assistant_activities
        WHERE org_id = $1
          AND (user_id = ANY($2::uuid[])
               OR proposed_by = ANY($2::uuid[])
               OR approved_by = ANY($2::uuid[])
               OR entity_id IN (SELECT id FROM entities
                                WHERE org_id = $1 AND display_name LIKE $3))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    await conn.execute(
        """
        DELETE FROM trading_authority_grants
        WHERE org_id = $1
          AND (user_id = ANY($2::uuid[])
               OR granted_by = ANY($2::uuid[])
               OR entity_id IN (SELECT id FROM entities
                                WHERE org_id = $1 AND display_name LIKE $3))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, ent_filter,
    )
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS,
    )


async def leftover_count(conn) -> int:
    ent_filter = TEST_ENTITY_PREFIX + "%"
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities
                WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM trading_authority_grants
                WHERE org_id = $2
                  AND (user_id = ANY($1::uuid[])
                       OR granted_by = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM assistant_activities
                WHERE org_id = $2
                  AND (user_id = ANY($1::uuid[])
                       OR proposed_by = ANY($1::uuid[])
                       OR approved_by = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
        """,
        ALL_TEST_USERS, ORG_ID, ent_filter,
    ))


async def seed_user(conn, user_id, tag):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, ORG_ID,
        f"soc5_{tag}@test.local", f"SOC5 {tag}", f"auth0|test_soc5_{tag}",
    )


async def seed_entity(conn, tag) -> str:
    return str(await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', $2) RETURNING id
        """,
        ORG_ID, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


async def grant_tier(conn, entity_id, user_id, tier):
    await conn.execute(
        """
        INSERT INTO trading_authority_grants
            (org_id, entity_id, user_id, authority_tier, granted_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (entity_id, user_id) DO UPDATE
            SET authority_tier = EXCLUDED.authority_tier
        """,
        ORG_ID, entity_id, user_id, tier, U_FULL,
    )


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.trading_authority import (
        propose_money_movement,
        approve_money_movement,
        AuthorityError,
        MakerCheckerError,
    )

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Seed --------------------------------------------------------
        async with pool.acquire() as conn:
            await seed_user(conn, U_INQUIRY, "inquiry")
            await seed_user(conn, U_LIMITED, "limited")
            await seed_user(conn, U_FULL, "full")
            await seed_user(conn, U_APPROVER, "approver")
            entity_id = await seed_entity(conn, "Account")
            await grant_tier(conn, entity_id, U_INQUIRY, "inquiry")
            await grant_tier(conn, entity_id, U_LIMITED, "limited")
            await grant_tier(conn, entity_id, U_FULL, "full")
            await grant_tier(conn, entity_id, U_APPROVER, "full")

        # ------------------------------------------------------------------
        # Assertion 1: trading_authority_grants exists + tier CHECK rejects
        #              an invalid value.
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            reg = await conn.fetchval(
                "SELECT to_regclass('public.trading_authority_grants')"
            )
            check_rejected = False
            try:
                await conn.execute(
                    """
                    INSERT INTO trading_authority_grants
                        (org_id, entity_id, user_id, authority_tier)
                    VALUES ($1, $2, $3, 'bogus_tier')
                    """,
                    ORG_ID, entity_id, U_FULL,
                )
            except asyncpg.exceptions.CheckViolationError:
                check_rejected = True
        if reg and check_rejected:
            ok("Assertion 1: trading_authority_grants exists and its authority_tier "
               "CHECK rejects an invalid tier value ('bogus_tier')")
        else:
            fail("Assertion 1: schema/CHECK wrong",
                 f"regclass={reg}, invalid_tier_rejected={check_rejected}")

        # ------------------------------------------------------------------
        # Assertion 2: Report Task 1's findings on the real money-movement
        #              implementation (table / columns / prior enforcement).
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            aa_cols = {
                r["column_name"]
                for r in await conn.fetch(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='assistant_activities'
                    """
                )
            }
            maker_checker_chk = await conn.fetchval(
                """
                SELECT count(*) FROM pg_constraint
                WHERE conname = 'assistant_activities_maker_checker_chk'
                """
            )
        print("    --- Task 1 findings ---")
        print("    Real write-back ledger: assistant_activities (not an Altruist/")
        print("      custodian table — that subsystem does not exist).")
        print("    Real statuses: awaiting_review / done / undone (the design's")
        print("      proposed->dispatched->settled enum is NOT present).")
        print("    Governance columns added this phase: proposed_by, approved_by,")
        print(f"      entity_id present = "
              f"{ {'proposed_by','approved_by','entity_id'} <= aa_cols }")
        print("    Prior maker-checker enforcement: NONE (added this phase).")
        print("    trading_authority checked at propose time: YES (this phase).")
        if {"proposed_by", "approved_by", "entity_id"} <= aa_cols and maker_checker_chk == 1:
            ok("Assertion 2: Task 1 findings reported; real ledger "
               "(assistant_activities) now carries proposed_by/approved_by/entity_id "
               "and the maker-checker CHECK constraint exists")
        else:
            fail("Assertion 2: ledger governance columns/constraint missing",
                 f"cols={sorted(aa_cols & {'proposed_by','approved_by','entity_id'})}, "
                 f"maker_checker_chk={maker_checker_chk}")

        # ------------------------------------------------------------------
        # Assertion 3: maker-checker — approving where approved_by = proposed_by
        #              is REJECTED (both at the app layer and the DB CHECK).
        # ------------------------------------------------------------------
        mm_id = await propose_money_movement(
            pool, ORG_ID, entity_id, U_FULL, "spv.subscribe",
            amount="10000.00", title="SOC5 self-approve attempt",
        )
        app_rejected = False
        try:
            await approve_money_movement(pool, ORG_ID, mm_id, U_FULL)
        except MakerCheckerError:
            app_rejected = True

        db_rejected = False
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    "UPDATE assistant_activities SET approved_by = proposed_by "
                    "WHERE id = $1",
                    mm_id,
                )
            except asyncpg.exceptions.CheckViolationError:
                db_rejected = True
        if app_rejected and db_rejected:
            ok("Assertion 3: self-approval (approved_by = proposed_by) is REJECTED — "
               "MakerCheckerError at the service layer AND CheckViolation at the DB "
               "(even though the proposer holds 'full' authority)")
        else:
            fail("Assertion 3: self-approval not rejected",
                 f"app_rejected={app_rejected}, db_rejected={db_rejected}")

        # ------------------------------------------------------------------
        # Assertion 4: maker-checker — approving with a DIFFERENT approver
        #              SUCCEEDS (the check is not overly broad).
        # ------------------------------------------------------------------
        approved = await approve_money_movement(pool, ORG_ID, mm_id, U_APPROVER)
        async with pool.acquire() as conn:
            final = await conn.fetchrow(
                "SELECT status, proposed_by, approved_by "
                "FROM assistant_activities WHERE id = $1",
                mm_id,
            )
        if (approved["status"] == "approved"
                and str(final["proposed_by"]) == U_FULL
                and str(final["approved_by"]) == U_APPROVER):
            ok("Assertion 4: approval by a DIFFERENT user (approved_by != proposed_by) "
               "SUCCEEDS — activity status is 'approved'")
        else:
            fail("Assertion 4: distinct-approver path did not succeed",
                 f"status={approved.get('status')}, proposed_by={final['proposed_by']}, "
                 f"approved_by={final['approved_by']}")

        # ------------------------------------------------------------------
        # Assertion 5: an 'inquiry' user proposing a money-movement action is
        #              REJECTED.
        # ------------------------------------------------------------------
        inquiry_rejected = False
        try:
            await propose_money_movement(
                pool, ORG_ID, entity_id, U_INQUIRY, "spv.subscribe", amount="500",
            )
        except AuthorityError:
            inquiry_rejected = True
        if inquiry_rejected:
            ok("Assertion 5: an 'inquiry'-tier user proposing a money-movement action "
               "is REJECTED (AuthorityError)")
        else:
            fail("Assertion 5: inquiry-tier propose was not rejected")

        # ------------------------------------------------------------------
        # Assertion 6: a 'limited' (and 'full') user CAN propose.
        # ------------------------------------------------------------------
        limited_id = None
        full_third_party_id = None
        limited_ok = False
        full_ok = False
        limited_blocked_third_party = False
        try:
            # 'limited' may propose a within-account movement (spv.subscribe).
            limited_id = await propose_money_movement(
                pool, ORG_ID, entity_id, U_LIMITED, "spv.subscribe", amount="2500",
            )
            limited_ok = limited_id is not None
        except AuthorityError:
            limited_ok = False
        try:
            # 'full' may propose a third-party movement (spv.record_transaction).
            full_third_party_id = await propose_money_movement(
                pool, ORG_ID, entity_id, U_FULL, "spv.record_transaction",
                amount="7500",
            )
            full_ok = full_third_party_id is not None
        except AuthorityError:
            full_ok = False
        try:
            # ...but 'limited' may NOT direct funds to a third party.
            await propose_money_movement(
                pool, ORG_ID, entity_id, U_LIMITED, "spv.record_transaction",
                amount="1",
            )
        except AuthorityError:
            limited_blocked_third_party = True
        if limited_ok and full_ok and limited_blocked_third_party:
            ok("Assertion 6: 'limited' CAN propose within-account movement and 'full' "
               "CAN propose third-party movement; 'limited' is correctly blocked from "
               "third-party movement (custody distinction holds)")
        else:
            fail("Assertion 6: tier propose gating wrong",
                 f"limited_ok={limited_ok}, full_ok={full_ok}, "
                 f"limited_blocked_third_party={limited_blocked_third_party}")

        # ------------------------------------------------------------------
        # Assertion 7: teardown leaves zero leftover rows.
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("Assertion 7: teardown complete — zero leftover test rows (count=0)")
        else:
            fail("Assertion 7: leftover rows after teardown", f"count={remaining}")

    finally:
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 48}")
    print(f"SOC Phase 5: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
