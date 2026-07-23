"""SOC Phase 3 verify — Households: flexible rollup vs strict primary household.

Proves the household service (``services.households``):
  * flexible MANY-TO-MANY membership (an entity in several households),
  * strict single-value primary household (at most one, assignment REPLACES),
  * Task 2 flexible rollup summing across all members (Decimal-exact),
  * Task 3 strict primary aggregate that never double-counts, and
  * that household membership grants NO staff visibility (Phase 2 resolver
    result is unchanged by household operations).

Pass/fail only, no interactive prompts, idempotent (teardown-at-start AND
teardown-at-end by stable identifiers).

Run: DATABASE_URL=... python scripts/verify_soc3.py
"""
import asyncio
import glob
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soc3")
    sys.exit(0)

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTERS_DIR = os.path.join(API_DIR, "routers")

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test staff user (deleted by exact id at teardown).
U_STAFF = "99000000-0000-0000-0000-0000000003a1"
ALL_TEST_USERS = [U_STAFF]

TEST_ENTITY_PREFIX = "SOC3Verify"
TEST_HH_PREFIX = "SOC3 Verify HH"

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
    test_ent = TEST_ENTITY_PREFIX + "%"
    test_hh = TEST_HH_PREFIX + "%"
    # staff_assignments referencing test users or test entities.
    await conn.execute(
        """
        DELETE FROM staff_assignments
        WHERE org_id = $1
          AND (assigned_to_user_id = ANY($2::uuid[])
               OR entity_id IN (SELECT id FROM entities
                    WHERE org_id = $1 AND display_name LIKE $3))
        """,
        ORG_ID, ALL_TEST_USERS, test_ent,
    )
    # household_memberships (child of households / entities).
    await conn.execute(
        """
        DELETE FROM household_memberships
        WHERE household_id IN (SELECT id FROM households
                WHERE org_id = $1 AND name LIKE $2)
           OR entity_id IN (SELECT id FROM entities
                WHERE org_id = $1 AND display_name LIKE $3)
        """,
        ORG_ID, test_hh, test_ent,
    )
    # entity_holdings (child of entities).
    await conn.execute(
        """
        DELETE FROM entity_holdings
        WHERE org_id = $1 AND entity_id IN (SELECT id FROM entities
                WHERE org_id = $1 AND display_name LIKE $2)
        """,
        ORG_ID, test_ent,
    )
    # Detach strict primary FK (points entities -> test households) before delete.
    await conn.execute(
        """
        UPDATE entities SET primary_household_id = NULL
        WHERE org_id = $1 AND primary_household_id IN (
            SELECT id FROM households WHERE org_id = $1 AND name LIKE $2)
        """,
        ORG_ID, test_hh,
    )
    # households.
    await conn.execute(
        "DELETE FROM households WHERE org_id = $1 AND name LIKE $2", ORG_ID, test_hh,
    )
    # entities.
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, test_ent,
    )
    # users.
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS,
    )
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS,
    )


async def leftover_count(conn) -> int:
    test_ent = TEST_ENTITY_PREFIX + "%"
    test_hh = TEST_HH_PREFIX + "%"
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities
                WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM households WHERE org_id = $2 AND name LIKE $4)
          + (SELECT count(*) FROM household_memberships
                WHERE household_id IN (SELECT id FROM households
                        WHERE org_id = $2 AND name LIKE $4)
                   OR entity_id IN (SELECT id FROM entities
                        WHERE org_id = $2 AND display_name LIKE $3))
          + (SELECT count(*) FROM entity_holdings
                WHERE org_id = $2 AND entity_id IN (SELECT id FROM entities
                        WHERE org_id = $2 AND display_name LIKE $3))
          + (SELECT count(*) FROM staff_assignments
                WHERE org_id = $2 AND assigned_to_user_id = ANY($1::uuid[]))
        """,
        ALL_TEST_USERS, ORG_ID, test_ent, test_hh,
    ))


async def seed_entity(conn, tag) -> str:
    return str(await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', $2) RETURNING id
        """,
        ORG_ID, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


async def seed_household(conn, tag) -> str:
    return str(await conn.fetchval(
        "INSERT INTO households (org_id, name) VALUES ($1, $2) RETURNING id",
        ORG_ID, f"{TEST_HH_PREFIX} {tag}",
    ))


async def add_holding(conn, entity_id, key, value: Decimal, as_of):
    await conn.execute(
        """
        INSERT INTO entity_holdings (org_id, entity_id, taxonomy_key, market_value,
                                     as_of_date)
        VALUES ($1, $2, $3, $4, $5)
        """,
        ORG_ID, entity_id, key, value, as_of,
    )


async def run():
    import datetime

    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services import households as hh
    from services.staff_visibility import get_staff_visible_entity_ids

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ==================================================================
        # Assertion 1: structure exists matching the snapshot
        # ==================================================================
        async with pool.acquire() as conn:
            regs = await conn.fetchrow(
                """
                SELECT to_regclass('public.households')            AS households,
                       to_regclass('public.household_memberships')  AS memberships
                """
            )
            has_primary_col = int(await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'entities'
                  AND column_name = 'primary_household_id'
                """
            ))
            # household_memberships PK is (household_id, entity_id) — the many-to-many key.
            mm_pk = int(await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.key_column_usage
                WHERE table_schema = 'public'
                  AND constraint_name = 'household_memberships_pkey'
                  AND column_name IN ('household_id', 'entity_id')
                """
            ))
        if regs["households"] and regs["memberships"] and has_primary_col == 1 and mm_pk == 2:
            ok("Assertion 1: households / household_memberships / "
               "entities.primary_household_id exist matching the snapshot")
        else:
            fail("Assertion 1: structure mismatch",
                 f"households={regs['households']}, memberships={regs['memberships']}, "
                 f"primary_col={has_primary_col}, mm_pk_cols={mm_pk}")

        # ---- Seed entities + households ----------------------------------
        async with pool.acquire() as conn:
            e_a = await seed_entity(conn, "EntityA")
            e_b = await seed_entity(conn, "EntityB")
            e_c = await seed_entity(conn, "EntityC")  # the overlap entity
            h1 = await seed_household(conn, "One")
            h2 = await seed_household(conn, "Two")
            h3 = await seed_household(conn, "Three")

            # Holdings — Decimal, with an older + newer snapshot to prove the
            # rollup takes the LATEST snapshot per (entity, taxonomy_key).
            await add_holding(conn, e_a, "taxonomy_sc_1", Decimal("999.99"), yesterday)
            await add_holding(conn, e_a, "taxonomy_sc_1", Decimal("100.25"), today)   # latest
            await add_holding(conn, e_a, "taxonomy_sc_2", Decimal("50.50"), today)
            await add_holding(conn, e_b, "taxonomy_sc_1", Decimal("200.00"), today)
            await add_holding(conn, e_c, "taxonomy_sc_1", Decimal("77.77"), today)

        # ---- Baseline staff visibility (BEFORE any household membership) --
        # Seed a staff user with one real assignment so the resolver set is a
        # meaningful, non-empty baseline.
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, $3, $4, $5, 'member')
                ON CONFLICT (auth0_sub) DO NOTHING
                """,
                U_STAFF, ORG_ID, "soc3_staff@test.local", "SOC3 Staff",
                "auth0|test_soc3_staff",
            )
            await conn.execute(
                """
                INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id,
                                               role_label)
                VALUES ($1, $2, $3, 'SOC3 verify')
                """,
                ORG_ID, e_a, U_STAFF,
            )
        baseline_vis = await get_staff_visible_entity_ids(pool, U_STAFF, ORG_ID)

        # ==================================================================
        # Assertion 2: an entity can belong to MULTIPLE households at once
        # ==================================================================
        async with pool.acquire() as conn:
            await hh.add_entity_to_household(conn, ORG_ID, h1, e_a)
            await hh.add_entity_to_household(conn, ORG_ID, h1, e_b)
            await hh.add_entity_to_household(conn, ORG_ID, h2, e_a)  # e_a in H1 AND H2
            # overlap entity e_c: flexible member of BOTH H1 and H2.
            await hh.add_entity_to_household(conn, ORG_ID, h1, e_c)
            await hh.add_entity_to_household(conn, ORG_ID, h2, e_c)
            a_households = await hh.list_households_for_entity(conn, ORG_ID, e_a)
        a_hids = {str(r["id"]) for r in a_households}
        if h1 in a_hids and h2 in a_hids and len(a_hids) == 2:
            ok("Assertion 2: many-to-many — entity A belongs to 2 households "
               "simultaneously (H1 + H2)")
        else:
            fail("Assertion 2: entity not in multiple households", f"households={a_hids}")

        # ==================================================================
        # Assertion 3: at most ONE primary — second set REPLACES (single column)
        # ==================================================================
        async with pool.acquire() as conn:
            await hh.set_primary_household(conn, ORG_ID, e_a, h1)
            first = await conn.fetchval(
                "SELECT primary_household_id FROM entities WHERE id = $1", e_a)
            await hh.set_primary_household(conn, ORG_ID, e_a, h2)  # replace
            second = await conn.fetchval(
                "SELECT primary_household_id FROM entities WHERE id = $1", e_a)
            # There is exactly ONE entities row for e_a (no duplicate record).
            entity_row_count = await conn.fetchval(
                "SELECT count(*) FROM entities WHERE id = $1", e_a)
            prim = await hh.get_primary_household(conn, ORG_ID, e_a)
        replaced = (str(first) == h1 and str(second) == h2
                    and int(entity_row_count) == 1
                    and prim is not None and str(prim["id"]) == h2)
        if replaced:
            ok("Assertion 3: primary household is single-valued — reassigning "
               "REPLACES (H1→H2) via a single-column update, no duplicate record")
        else:
            fail("Assertion 3: primary not replaced cleanly",
                 f"first={first}, second={second}, rows={entity_row_count}, prim={prim}")

        # ==================================================================
        # Assertion 4: Task 2 flexible rollup sums ALL members, Decimal-exact
        # ==================================================================
        # H1 flexible members = {e_a, e_b, e_c}.
        #   e_a latest holdings: 100.25 (sc_1, newer beats 999.99) + 50.50 = 150.75
        #   e_b: 200.00
        #   e_c: 77.77
        #   expected = 428.52
        async with pool.acquire() as conn:
            roll = await hh.household_rollup(conn, ORG_ID, h1)
        expected = Decimal("428.52")
        rollup_ok = (
            isinstance(roll["total_holdings_value"], Decimal)
            and roll["total_holdings_value"] == expected
            and roll["member_count"] == 3
            and set(roll["member_entity_ids"]) == {e_a, e_b, e_c}
        )
        if rollup_ok:
            ok("Assertion 4: flexible rollup sums latest holdings across all 3 "
               f"members Decimal-exact (= {expected}, older snapshot ignored)")
        else:
            fail("Assertion 4: flexible rollup wrong",
                 f"got={roll['total_holdings_value']!r} "
                 f"(type {type(roll['total_holdings_value']).__name__}), "
                 f"members={roll['member_count']}, expected={expected}")

        # ==================================================================
        # Assertion 5: Task 3 strict primary aggregate does NOT double-count
        # ==================================================================
        # e_c is a flexible member of BOTH H1 and H2, but its ONE primary is H3.
        async with pool.acquire() as conn:
            await hh.set_primary_household(conn, ORG_ID, e_c, h3)
            # Flexible rollups DO both include e_c (double counting if summed):
            roll_h1 = await hh.household_rollup(conn, ORG_ID, h1)
            roll_h2 = await hh.household_rollup(conn, ORG_ID, h2)
            # Strict primary aggregate for H3 = {e_c} exactly once.
            strict_h3 = await hh.primary_household_networth(conn, ORG_ID, h3)
            # Org-wide strict partition: e_c must appear in exactly ONE group.
            org_strict = await hh.primary_household_networth(conn, ORG_ID, None)

        c_in_both_flexible = (e_c in roll_h1["member_entity_ids"]
                              and e_c in roll_h2["member_entity_ids"])
        strict_counts_once = (
            strict_h3["member_count"] == 1
            and strict_h3["member_entity_ids"] == [e_c]
            and strict_h3["total_holdings_value"] == Decimal("77.77")
        )
        groups_with_c = [g for g in org_strict if e_c in g.get("member_entity_ids", [])]
        partition_once = len(groups_with_c) == 1 and groups_with_c[0]["household_id"] == h3
        if c_in_both_flexible and strict_counts_once and partition_once:
            ok("Assertion 5: entity C is in 2 flexible households yet the strict "
               "primary aggregate counts it EXACTLY once (only under H3 = 77.77)")
        else:
            fail("Assertion 5: strict primary aggregate double-counts / wrong",
                 f"c_in_both_flexible={c_in_both_flexible}, "
                 f"strict_counts_once={strict_counts_once}, partition_once={partition_once}")

        # ==================================================================
        # Assertion 6: household ops do NOT change staff visibility
        # ==================================================================
        # Every household + membership + primary op above has now run. Recompute.
        after_vis = await get_staff_visible_entity_ids(pool, U_STAFF, ORG_ID)
        # The resolver must ALSO not be wired into households code (belt & braces).
        wired_in = "staff_visibility" in open(
            os.path.join(API_DIR, "services", "households.py"), encoding="utf-8"
        ).read()
        wired_router = "staff_visibility" in open(
            os.path.join(ROUTERS_DIR, "households.py"), encoding="utf-8"
        ).read()
        # e_b and e_c share households with e_a but were never assigned to U_STAFF,
        # so they must NOT have leaked into visibility.
        no_leak = e_b not in after_vis and e_c not in after_vis
        if after_vis == baseline_vis and no_leak and not wired_in and not wired_router:
            ok("Assertion 6: creating households + adding entities did NOT change "
               "get_staff_visible_entity_ids — membership alone grants nothing")
        else:
            fail("Assertion 6: household membership changed staff visibility",
                 f"baseline={baseline_vis}, after={after_vis}, no_leak={no_leak}, "
                 f"wired_service={wired_in}, wired_router={wired_router}")

        # ==================================================================
        # Assertion 7: teardown leaves zero leftover rows
        # ==================================================================
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
    print(f"SOC Phase 3: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
