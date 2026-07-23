"""SOC Phase 4 verify — Restricted-access accounts: ONE filter wrapping BOTH engines.

Proves the unified restriction filter
(``services.restricted_access.filter_restricted``) removes a restricted entity
from BOTH the staff-visibility engine's result set (Phase 2) AND the member
visibility engine's result set (resolve_entity_set) using a SINGLE
implementation — and that an explicitly allow-listed user still sees it. Also
proves the Super-Admin-only audited mutators, and that a non-Super-Admin is
rejected.

SAFETY / SCOPE (SOC Phase 4, same as Phase 2): the filter is standalone and NOT
wired into any endpoint's enforcement path. This script only exercises the
callable functions directly.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Run: DATABASE_URL=... python scripts/verify_soc4.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soc4")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test users (deleted by exact id at teardown).
U_SUPER = "99000000-0000-0000-0000-0000000004a1"     # role super_admin (actor)
U_OUTSIDER = "99000000-0000-0000-0000-0000000004a2"  # member, NOT allow-listed
U_GRANTED = "99000000-0000-0000-0000-0000000004a3"   # member, on the allow-list
U_NONADMIN = "99000000-0000-0000-0000-0000000004a4"  # member, tries to mutate
ALL_TEST_USERS = [U_SUPER, U_OUTSIDER, U_GRANTED, U_NONADMIN]

TEST_ENTITY_PREFIX = "SOC4Verify"

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
    # Child rows referencing the test entities / users first.
    await conn.execute(
        """
        DELETE FROM restricted_access_audit
        WHERE org_id = $1
          AND (entity_id IN (SELECT id FROM entities
                             WHERE org_id = $1 AND display_name LIKE $2)
               OR performed_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM restricted_access_grants
        WHERE org_id = $1
          AND (entity_id IN (SELECT id FROM entities
                             WHERE org_id = $1 AND display_name LIKE $2)
               OR user_id = ANY($3::uuid[])
               OR granted_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
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
    await conn.execute(
        """
        DELETE FROM entity_relationships
        WHERE org_id = $1
          AND (from_entity_id IN (SELECT id FROM entities
                                  WHERE org_id = $1 AND display_name LIKE $2)
               OR to_entity_id IN (SELECT id FROM entities
                                   WHERE org_id = $1 AND display_name LIKE $2))
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
          + (SELECT count(*) FROM entities
                WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM restricted_access_grants
                WHERE org_id = $2
                  AND (user_id = ANY($1::uuid[])
                       OR granted_by = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM restricted_access_audit
                WHERE org_id = $2
                  AND (performed_by = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM staff_assignments
                WHERE org_id = $2
                  AND (assigned_to_user_id = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
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
        f"soc4_{tag}@test.local", f"SOC4 {tag}", f"auth0|test_soc4_{tag}", role,
    )


async def seed_entity(conn, tag) -> str:
    return str(await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', $2) RETURNING id
        """,
        ORG_ID, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.restricted_access import (
        filter_restricted,
        set_restricted,
        grant_restricted_access,
        revoke_restricted_access,
        ACTION_RESTRICT,
        ACTION_UNRESTRICT,
        ACTION_GRANT,
        ACTION_REVOKE,
    )
    from services.staff_visibility import get_staff_visible_entity_ids
    from services.entity_graph import resolve_entity_set

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Seed users --------------------------------------------------
        async with pool.acquire() as conn:
            await seed_user(conn, U_SUPER, "super", "super_admin")
            await seed_user(conn, U_OUTSIDER, "outsider", "member")
            await seed_user(conn, U_GRANTED, "granted", "member")
            await seed_user(conn, U_NONADMIN, "nonadmin", "member")

        # ---- Seed entities + relationships -------------------------------
        #   e_restricted — the entity that gets flagged restricted. Reachable
        #     by U_OUTSIDER via BOTH a staff_assignment (staff engine) AND an
        #     ownership edge from e_root (member engine).
        #   e_normal — never restricted; must always pass through the filter.
        async with pool.acquire() as conn:
            e_restricted = await seed_entity(conn, "RestrictedEntity")
            e_normal = await seed_entity(conn, "NormalEntity")
            e_root = await seed_entity(conn, "RootOwnerEntity")

            # Staff path: U_OUTSIDER is directly assigned e_restricted, so the
            # staff engine would normally return it for them.
            await conn.execute(
                """
                INSERT INTO staff_assignments
                    (org_id, entity_id, assigned_to_user_id, role_label)
                VALUES ($1, $2, $3, 'SOC4 verify')
                """,
                ORG_ID, e_restricted, U_OUTSIDER,
            )
            await conn.execute(
                """
                INSERT INTO staff_assignments
                    (org_id, entity_id, assigned_to_user_id, role_label)
                VALUES ($1, $2, $3, 'SOC4 verify')
                """,
                ORG_ID, e_normal, U_OUTSIDER,
            )
            # Member path: e_root owns e_restricted 100%, so resolve_entity_set
            # (subtree from e_root) would normally return e_restricted.
            await conn.execute(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct)
                VALUES ($1, $2, $3, 'ownership', 100)
                """,
                ORG_ID, e_root, e_restricted,
            )

        # ------------------------------------------------------------------
        # Assertion 1: schema — access_restricted col + both tables exist
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            has_col = int(await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'entities'
                  AND column_name = 'access_restricted'
                """
            ))
            regs = await conn.fetchrow(
                """
                SELECT to_regclass('public.restricted_access_grants') AS grants,
                       to_regclass('public.restricted_access_audit')  AS audit
                """
            )
        if has_col == 1 and regs["grants"] and regs["audit"]:
            ok("Assertion 1: entities.access_restricted + restricted_access_grants "
               "+ restricted_access_audit exist matching the snapshot")
        else:
            fail("Assertion 1: schema missing",
                 f"has_col={has_col}, grants={regs['grants']}, audit={regs['audit']}")

        # ---- Flag e_restricted as restricted (via the Super Admin mutator) --
        await set_restricted(pool, e_restricted, True, U_SUPER, notes="verify")

        # ------------------------------------------------------------------
        # Assertion 2: filter REMOVES the restricted entity from a STAFF set
        # ------------------------------------------------------------------
        staff_set = await get_staff_visible_entity_ids(pool, U_OUTSIDER, ORG_ID)
        staff_includes = e_restricted in staff_set  # engine returns it normally
        staff_filtered = await filter_restricted(
            pool, staff_set, U_OUTSIDER, ORG_ID
        )
        if (staff_includes and e_restricted not in staff_filtered
                and e_normal in staff_filtered):
            ok("Assertion 2: staff engine returns the entity, but filter_restricted "
               "EXCLUDES it for an off-allow-list user (normal entity still passes)")
        else:
            fail("Assertion 2: staff-side filtering wrong",
                 f"engine_had_it={staff_includes}, "
                 f"filtered_out={e_restricted not in staff_filtered}, "
                 f"normal_kept={e_normal in staff_filtered}")

        # ------------------------------------------------------------------
        # Assertion 3: SAME filter REMOVES the SAME entity from a MEMBER set
        # ------------------------------------------------------------------
        member_rows = await resolve_entity_set(
            pool, ORG_ID, {"type": "subtree", "root_id": e_root}
        )
        member_set = {r["entity_id"] for r in member_rows}
        member_includes = e_restricted in member_set  # engine returns it normally
        member_filtered = await filter_restricted(
            pool, member_set, U_OUTSIDER, ORG_ID
        )
        if member_includes and e_restricted not in member_filtered:
            ok("Assertion 3: member engine (resolve_entity_set/ownership) returns the "
               "SAME entity, and the SAME filter EXCLUDES it — one filter wraps both")
        else:
            fail("Assertion 3: member-side filtering wrong",
                 f"engine_had_it={member_includes}, "
                 f"filtered_out={e_restricted not in member_filtered}")

        # ------------------------------------------------------------------
        # Assertion 4: a user WITH a grant DOES see it through the filter
        # ------------------------------------------------------------------
        await grant_restricted_access(
            pool, e_restricted, U_GRANTED, U_SUPER, "verify grant"
        )
        # Feed BOTH engines' sets through the filter for the granted user.
        granted_staff = await filter_restricted(
            pool, staff_set | {e_restricted}, U_GRANTED, ORG_ID
        )
        granted_member = await filter_restricted(
            pool, member_set, U_GRANTED, ORG_ID
        )
        if e_restricted in granted_staff and e_restricted in granted_member:
            ok("Assertion 4: a user WITH a restricted_access_grants row still sees "
               "the entity through filter_restricted (both engines' sets)")
        else:
            fail("Assertion 4: granted user cannot see restricted entity",
                 f"staff={e_restricted in granted_staff}, "
                 f"member={e_restricted in granted_member}")

        # ------------------------------------------------------------------
        # Assertion 5: each mutator writes an audit row (correct action + actor)
        # ------------------------------------------------------------------
        # Exercise all four actions freshly and read the audit trail.
        await set_restricted(pool, e_normal, True, U_SUPER)          # restrict
        await set_restricted(pool, e_normal, False, U_SUPER)         # unrestrict
        await grant_restricted_access(pool, e_normal, U_GRANTED, U_SUPER, "g")
        await revoke_restricted_access(pool, e_normal, U_GRANTED, U_SUPER)
        async with pool.acquire() as conn:
            audit_rows = await conn.fetch(
                """
                SELECT action, performed_by
                FROM restricted_access_audit
                WHERE org_id = $1 AND entity_id = $2
                ORDER BY performed_at
                """,
                ORG_ID, e_normal,
            )
        actions = [r["action"] for r in audit_rows]
        actors_ok = all(str(r["performed_by"]) == U_SUPER for r in audit_rows)
        expected = {ACTION_RESTRICT, ACTION_UNRESTRICT, ACTION_GRANT, ACTION_REVOKE}
        if expected.issubset(set(actions)) and actors_ok:
            ok("Assertion 5: set_restricted / grant / revoke each wrote an audit row "
               f"with the correct action + actor (actions={actions})")
        else:
            fail("Assertion 5: audit rows wrong",
                 f"actions={actions}, actors_ok={actors_ok}")

        # ------------------------------------------------------------------
        # Assertion 6: a non-Super-Admin is rejected by every mutator
        # ------------------------------------------------------------------
        rejected = {"set": False, "grant": False, "revoke": False}
        try:
            await set_restricted(pool, e_restricted, False, U_NONADMIN)
        except PermissionError:
            rejected["set"] = True
        try:
            await grant_restricted_access(
                pool, e_restricted, U_OUTSIDER, U_NONADMIN, "nope"
            )
        except PermissionError:
            rejected["grant"] = True
        try:
            await revoke_restricted_access(
                pool, e_restricted, U_GRANTED, U_NONADMIN
            )
        except PermissionError:
            rejected["revoke"] = True
        if all(rejected.values()):
            ok("Assertion 6: a non-Super-Admin calling set_restricted / grant / "
               "revoke is rejected (PermissionError on all three)")
        else:
            fail("Assertion 6: non-Super-Admin not rejected", f"{rejected}")

        # Sanity: the failed non-admin mutations changed nothing.
        async with pool.acquire() as conn:
            still_restricted = await conn.fetchval(
                "SELECT access_restricted FROM entities WHERE id = $1", e_restricted
            )
            grant_still_there = await conn.fetchval(
                """
                SELECT 1 FROM restricted_access_grants
                WHERE entity_id = $1 AND user_id = $2
                """,
                e_restricted, U_GRANTED,
            )
        if still_restricted and grant_still_there:
            ok("Assertion 6b: rejected non-admin calls left the flag and grant intact")
        else:
            fail("Assertion 6b: non-admin call mutated state",
                 f"still_restricted={still_restricted}, "
                 f"grant_still_there={bool(grant_still_there)}")

        # ------------------------------------------------------------------
        # Assertion 7: teardown leaves zero leftover rows
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
    print(f"SOC Phase 4: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
