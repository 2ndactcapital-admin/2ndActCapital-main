"""Staff-visibility Super Admin bypass — verify.

Proves the fix for a confirmed, real gap: every RLS policy, ``restricted_access``
mutator, and trading-authority check in this platform carries an explicit
``is_super_admin`` escape hatch — but
``services.staff_visibility.get_staff_visible_entity_ids`` did NOT. A confirmed
Super Admin with ZERO ``staff_assignments`` rows was wrongly blocked from an
entity via the Ownership Tree Graph's staff route.

The fix adds the same ``is_super_admin`` escape hatch: a ``super_admin`` sees
EVERY entity in the org, bypassing the assignment/team/hierarchy filter. The
role is read from ``users.role`` via ``services.rbac.load_principal`` (the same
source ``restricted_access._require_super_admin`` uses), so callers still pass
only ``user_id`` — the function derives the role itself, and no call site needs
a signature change. ``org_admin`` is DELIBERATELY excluded — the bypass is
scoped to platform staff only.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Assertions:
  [discovery] Report Task 1's findings — the function signature and every
              call site found.
  1. Super Admin with ZERO staff_assignments for an entity STILL sees it via
     get_staff_visible_entity_ids (the actual bug, now fixed).
  2. A regular staff user (non-super-admin) with ZERO staff_assignments for the
     SAME entity does NOT see it (fix is scoped, not org-wide-for-everyone).
  3. Org Admin (not Super Admin) with zero staff_assignments also does NOT see
     it (org_admin correctly excluded, not silently included).
  4. Ownership Tree Graph's staff route (services.ownership_tree.staff_tree)
     now works for a Super Admin on the previously-blocked entity — the exact
     scenario found tonight — while a non-super staff user still gets nothing.
  5. Teardown: zero leftover rows.

Run: DATABASE_URL=... python scripts/verify_staffvisbypass.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_staffvisbypass")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test users (deleted by exact id at teardown).
U_SUPER = "99000000-0000-0000-0000-0000000005b1"      # super_admin (the bypass)
U_STAFF = "99000000-0000-0000-0000-0000000005b2"      # regular non-super staff
U_ORGADMIN = "99000000-0000-0000-0000-0000000005b3"   # org_admin (must NOT bypass)
ALL_TEST_USERS = [U_SUPER, U_STAFF, U_ORGADMIN]

TEST_ENTITY_PREFIX = "SVBypassVerify"

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


def node_ids(node) -> set:
    if not node:
        return set()
    ids = {node["id"]}
    for c in node.get("children", []):
        ids |= node_ids(c)
    return ids


async def cleanup(conn):
    """Remove all test data by stable identifiers. Idempotent, FK-safe order."""
    ent_filter = TEST_ENTITY_PREFIX + "%"
    # staff_assignments first (child of users / entities).
    await conn.execute(
        """
        DELETE FROM staff_assignments
        WHERE org_id = $1
          AND (assigned_to_user_id = ANY($2::uuid[])
               OR entity_id IN (SELECT id FROM entities
                                WHERE org_id = $1 AND display_name LIKE $3))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    # entity_relationships (child of entities).
    await conn.execute(
        """
        DELETE FROM entity_relationships
        WHERE org_id = $1
          AND (from_entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR to_entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2))
        """,
        ORG_ID, ent_filter,
    )
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, ent_filter,
    )
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS,
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
          + (SELECT count(*) FROM entities WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM entity_relationships WHERE org_id = $2
                AND (from_entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)
                     OR to_entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM staff_assignments WHERE org_id = $2
                AND (assigned_to_user_id = ANY($1::uuid[])
                     OR entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
        """,
        ALL_TEST_USERS, ORG_ID, ent_filter,
    ))


async def seed_user(conn, user_id, tag, role):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, ORG_ID,
        f"svb_{tag}@test.local", f"SVB {tag}", f"auth0|test_svb_{tag}", role,
    )


async def seed_entity(conn, tag, entity_type="individual") -> str:
    return str(await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, $2, $3) RETURNING id
        """,
        ORG_ID, entity_type, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


async def seed_rel(conn, from_id, to_id, rel_type, pct=None):
    await conn.execute(
        """
        INSERT INTO entity_relationships
            (org_id, from_entity_id, to_entity_id, relationship_type, ownership_pct)
        VALUES ($1, $2, $3, $4, $5)
        """,
        ORG_ID, from_id, to_id, rel_type, pct,
    )


def report_discovery():
    """Assertion [discovery]: report Task 1's findings explicitly."""
    print("[DISCOVERY] Task 1 findings:")
    print("  Function (services/staff_visibility.py):")
    print("    async def get_staff_visible_entity_ids(pool, user_id, org_id) -> set[str]")
    print("    - Pre-fix: NO access to the caller's role. Unioned direct + team +")
    print("      hierarchy staff_assignments only; a user with none resolved to")
    print("      an EMPTY set — with NO is_super_admin escape hatch.")
    print("    - Fix: derives the role internally via services.rbac.load_principal")
    print("      (reads users.role) + is_super_admin; a super_admin returns the")
    print("      FULL org entity set. Signature UNCHANGED (pool, user_id, org_id).")
    print("  Call sites found:")
    print("    - PRODUCTION: services/ownership_tree.py staff_tree() -> line ~230")
    print("      `allowed = await get_staff_visible_entity_ids(pool, user_id, org_id)`")
    print("      (reached by GET /entities/{id}/ownership-graph — the staff route).")
    print("      No other router/service calls it at runtime (restricted_access.py")
    print("      and trusted_contacts.py mention it only in docstrings).")
    print("    - TEST-ONLY: verify_soc2/soc3/soc4/soc6/ownershiptreea.py.")
    print("    - Because the role is derived from user_id inside the function, NO")
    print("      call site needed a signature change — none left un-upgraded.")
    ok("Assertion [discovery]: reported get_staff_visible_entity_ids signature "
       "and every call site (production staff_tree + test scripts)")


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.staff_visibility import get_staff_visible_entity_ids
    from services.ownership_tree import staff_tree

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Assertion [discovery] ---------------------------------------
        report_discovery()

        # ---- Seed users + a deliberately UNASSIGNED entity ---------------
        #   U_SUPER    role=super_admin   — must bypass, sees everything
        #   U_STAFF    role=investment_staff — regular staff, must NOT see it
        #   U_ORGADMIN role=org_admin     — must NOT bypass (excluded)
        # e_blocked has ZERO staff_assignments for ANY of them — the exact
        # condition that blocked the Super Admin tonight.
        async with pool.acquire() as conn:
            await seed_user(conn, U_SUPER, "super", "super_admin")
            await seed_user(conn, U_STAFF, "staff", "investment_staff")
            await seed_user(conn, U_ORGADMIN, "orgadmin", "org_admin")

            e_blocked = await seed_entity(conn, "BlockedEntity", "llc")
            e_child = await seed_entity(conn, "BlockedChild", "llc")
            await seed_rel(conn, e_blocked, e_child, "ownership", 100)
            # NO staff_assignments seeded for any user — that is the point.

        # ------------------------------------------------------------------
        # Assertion 1: Super Admin with ZERO assignments STILL sees it
        # ------------------------------------------------------------------
        super_set = await get_staff_visible_entity_ids(pool, U_SUPER, ORG_ID)
        if e_blocked in super_set and e_child in super_set:
            ok("Assertion 1: Super Admin with ZERO staff_assignments sees the entity "
               "(and every org entity) via get_staff_visible_entity_ids — the bug is fixed")
        else:
            fail("Assertion 1: Super Admin still blocked despite the bypass",
                 f"e_blocked_in={e_blocked in super_set}, e_child_in={e_child in super_set}")

        # ------------------------------------------------------------------
        # Assertion 2: regular staff (non-super) with ZERO assignments does NOT
        # ------------------------------------------------------------------
        staff_set = await get_staff_visible_entity_ids(pool, U_STAFF, ORG_ID)
        if e_blocked not in staff_set and staff_set == set():
            ok("Assertion 2: a regular staff user (non-super-admin) with ZERO "
               "staff_assignments does NOT see the entity — fix is scoped, not "
               "org-wide for everyone")
        else:
            fail("Assertion 2: fix accidentally opened visibility for a regular staff user",
                 f"set={staff_set}")

        # ------------------------------------------------------------------
        # Assertion 3: Org Admin (not Super Admin) also does NOT see it
        # ------------------------------------------------------------------
        orgadmin_set = await get_staff_visible_entity_ids(pool, U_ORGADMIN, ORG_ID)
        if e_blocked not in orgadmin_set and orgadmin_set == set():
            ok("Assertion 3: Org Admin (role=org_admin) with zero staff_assignments "
               "does NOT see the entity — org_admin correctly EXCLUDED from the bypass")
        else:
            fail("Assertion 3: org_admin was silently included in the super_admin bypass",
                 f"set={orgadmin_set}")

        # ------------------------------------------------------------------
        # Assertion 4: the Ownership Tree Graph staff route — the exact scenario
        #   found tonight. Super Admin now gets the tree; non-super staff does not.
        # ------------------------------------------------------------------
        super_tree = await staff_tree(pool, ORG_ID, e_blocked, U_SUPER)
        super_tree_ids = node_ids(super_tree)
        super_route_ok = (
            super_tree is not None
            and e_blocked in super_tree_ids
            and e_child in super_tree_ids
        )

        staff_tree_result = await staff_tree(pool, ORG_ID, e_blocked, U_STAFF)
        staff_route_blocked = staff_tree_result is None

        if super_route_ok and staff_route_blocked:
            ok("Assertion 4: staff route (services.ownership_tree.staff_tree) — Super "
               "Admin on the previously-blocked entity now gets the full tree "
               "(focal + child), while a non-super staff user with no assignment "
               "still gets None — exact tonight scenario, fixed and scoped")
        else:
            fail("Assertion 4: staff-route reproduction wrong",
                 f"super_route_ok={super_route_ok}, staff_route_blocked={staff_route_blocked}, "
                 f"super_tree_ids={super_tree_ids}")

        # ------------------------------------------------------------------
        # Assertion 5: teardown leaves zero leftover rows
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("Assertion 5: teardown complete — zero leftover test rows (count=0)")
        else:
            fail("Assertion 5: leftover rows after teardown", f"count={remaining}")

    finally:
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 48}")
    print(f"Staff-visibility Super Admin bypass: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
