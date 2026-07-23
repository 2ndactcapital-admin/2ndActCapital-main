"""SOC Phase 1 verify — Profiles, Permission Sets, Beneficiary edges.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Assertions:
  1. profiles / permission_sets / user_permission_sets exist; users.profile_id exists.
  2. profile_permissions / permission_set_permissions exist with the real
     action-registry permission format: a flat `permission_key text` column.
  3. 2nd Act's org has seed profiles for the confirmed personas
     (Member, Community Member, Adviser, CSA / Ops). Mesh End User is NOT
     seeded (no-Mesh rescope); Admin is NOT seeded (handled by users.role).
  4. A user assigned a profile resolves a granted permission True and a
     non-granted permission False.
  5. A permission set ADDS a capability beyond the profile's base grants.
  6. A 'beneficiary' relationship edge with null ownership_pct inserts OK.
  7. resolve_entity_set includes an entity reached via a beneficiary edge in
     the member's visible set, the same way an ownership edge would.
  8. Teardown leaves zero leftover rows (confirmed via count(*)).

Run: DATABASE_URL=... python scripts/verify_soc1.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soc1")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"

# Stable test-data identifiers (used for teardown at start and end).
TEST_PROFILE_NAME = "SOC1 Verify Profile"
TEST_PSET_NAME = "SOC1 Verify Set"
TEST_ENTITY_PREFIX = "SOC1Verify"

SEED_PERSONAS = ["Member", "Community Member", "Adviser", "CSA / Ops"]

# The profile grants this, but NOT the "denied" key below.
GRANTED_KEY = "manage_deals"
DENIED_KEY = "override_compliance"
# The permission set adds this on top of the profile.
SET_ADDED_KEY = "override_compliance"

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
    """Remove all test data by stable identifiers. Idempotent."""
    # Entities + their relationships (both directions).
    await conn.execute(
        """
        DELETE FROM entity_relationships
        WHERE from_entity_id IN (
                SELECT id FROM entities
                WHERE org_id = $1 AND display_name LIKE $2)
           OR to_entity_id IN (
                SELECT id FROM entities
                WHERE org_id = $1 AND display_name LIKE $2)
        """,
        ORG_ID, TEST_ENTITY_PREFIX + "%",
    )
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, TEST_ENTITY_PREFIX + "%",
    )

    # Detach test user from any profile / permission sets before deletion.
    await conn.execute(
        "DELETE FROM user_permission_sets WHERE user_id = $1", TEST_USER_ID,
    )
    await conn.execute(
        "UPDATE users SET profile_id = NULL WHERE id = $1", TEST_USER_ID,
    )

    # Test permission set (cascade clears permission_set_permissions).
    await conn.execute(
        """
        DELETE FROM permission_set_permissions
        WHERE permission_set_id IN (
            SELECT id FROM permission_sets WHERE org_id = $1 AND name = $2)
        """,
        ORG_ID, TEST_PSET_NAME,
    )
    await conn.execute(
        "DELETE FROM permission_sets WHERE org_id = $1 AND name = $2",
        ORG_ID, TEST_PSET_NAME,
    )

    # Test profile (non-seed; cascade clears profile_permissions).
    await conn.execute(
        """
        DELETE FROM profile_permissions
        WHERE profile_id IN (
            SELECT id FROM profiles
            WHERE org_id = $1 AND name = $2 AND is_seed = false)
        """,
        ORG_ID, TEST_PROFILE_NAME,
    )
    await conn.execute(
        "DELETE FROM profiles WHERE org_id = $1 AND name = $2 AND is_seed = false",
        ORG_ID, TEST_PROFILE_NAME,
    )

    # Test user.
    await conn.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await conn.execute("DELETE FROM member_todos WHERE user_id = $1", TEST_USER_ID)
    await conn.execute("DELETE FROM users WHERE id = $1", TEST_USER_ID)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM profiles
                WHERE org_id = $1 AND name = $2 AND is_seed = false)
          + (SELECT count(*) FROM permission_sets
                WHERE org_id = $1 AND name = $3)
          + (SELECT count(*) FROM entities
                WHERE org_id = $1 AND display_name LIKE $4)
          + (SELECT count(*) FROM entity_relationships
                WHERE from_entity_id IN (
                    SELECT id FROM entities
                    WHERE org_id = $1 AND display_name LIKE $4))
          + (SELECT count(*) FROM user_permission_sets WHERE user_id = $5)
          + (SELECT count(*) FROM users WHERE id = $5)
        """,
        ORG_ID, TEST_PROFILE_NAME, TEST_PSET_NAME,
        TEST_ENTITY_PREFIX + "%", TEST_USER_ID,
    ))


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.profiles import user_has_permission
    from services.entity_graph import resolve_entity_set

    try:
        # Teardown-at-start.
        async with pool.acquire() as conn:
            await cleanup(conn)

        # Seed test user.
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, 'verify_soc1@test.local', 'SOC1 Verify User', $3, 'member')
                ON CONFLICT (auth0_sub) DO NOTHING
                """,
                TEST_USER_ID, ORG_ID, TEST_AUTH0_SUB,
            )

        # ------------------------------------------------------------------
        # Check 1: base structural tables exist + users.profile_id column
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            regs = await conn.fetchrow(
                """
                SELECT to_regclass('public.profiles')            AS profiles,
                       to_regclass('public.permission_sets')      AS permission_sets,
                       to_regclass('public.user_permission_sets') AS user_permission_sets
                """
            )
            has_profile_id = await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'users'
                  AND column_name = 'profile_id'
                """
            )
        if (regs["profiles"] and regs["permission_sets"]
                and regs["user_permission_sets"] and int(has_profile_id) == 1):
            ok("Check 1: profiles / permission_sets / user_permission_sets + users.profile_id exist")
        else:
            fail("Check 1: base structure missing",
                 f"regs={dict(regs)}, users.profile_id={has_profile_id}")

        # ------------------------------------------------------------------
        # Check 2: junction tables exist with flat `permission_key text`
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            cols = await conn.fetch(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN ('profile_permissions', 'permission_set_permissions')
                """
            )
        by_table = {}
        for c in cols:
            by_table.setdefault(c["table_name"], {})[c["column_name"]] = c["data_type"]
        pp = by_table.get("profile_permissions", {})
        psp = by_table.get("permission_set_permissions", {})
        pp_ok = (pp.get("permission_key") == "text" and "profile_id" in pp
                 and "org_id" in pp)
        psp_ok = (psp.get("permission_key") == "text" and "permission_set_id" in psp
                  and "org_id" in psp)
        if pp_ok and psp_ok:
            ok("Check 2: profile_permissions & permission_set_permissions have "
               "flat permission_key text (matches action-registry required_permission format)")
        else:
            fail("Check 2: junction table shape wrong",
                 f"profile_permissions={pp}, permission_set_permissions={psp}")

        # ------------------------------------------------------------------
        # Check 3: seed personas present; Mesh End User absent
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            seed_rows = await conn.fetch(
                """
                SELECT name FROM profiles
                WHERE org_id = $1 AND is_seed = true
                """,
                ORG_ID,
            )
        seed_names = {r["name"] for r in seed_rows}
        missing = [p for p in SEED_PERSONAS if p not in seed_names]
        mesh_present = "Mesh End User" in seed_names
        admin_present = "Admin" in seed_names
        if not missing and not mesh_present and not admin_present:
            ok(f"Check 3: seed personas present {SEED_PERSONAS}; "
               f"Mesh End User NOT seeded (no-Mesh rescope), Admin NOT seeded "
               f"(handled by users.role)")
        else:
            fail("Check 3: seed personas wrong",
                 f"missing={missing}, mesh_present={mesh_present}, admin_present={admin_present}")

        # ------------------------------------------------------------------
        # Set up a test profile: grants GRANTED_KEY, not DENIED_KEY.
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            profile_id = await conn.fetchval(
                """
                INSERT INTO profiles (org_id, name, description, is_seed)
                VALUES ($1, $2, 'verify profile', false)
                RETURNING id
                """,
                ORG_ID, TEST_PROFILE_NAME,
            )
            await conn.execute(
                """
                INSERT INTO profile_permissions (org_id, profile_id, permission_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (profile_id, permission_key) DO NOTHING
                """,
                ORG_ID, profile_id, GRANTED_KEY,
            )
            await conn.execute(
                "UPDATE users SET profile_id = $1 WHERE id = $2",
                profile_id, TEST_USER_ID,
            )

        # ------------------------------------------------------------------
        # Check 4: profile resolves granted True, non-granted False
        # ------------------------------------------------------------------
        granted = await user_has_permission(pool, TEST_USER_ID, GRANTED_KEY)
        denied = await user_has_permission(pool, TEST_USER_ID, DENIED_KEY)
        if granted is True and denied is False:
            ok(f"Check 4: profile resolves '{GRANTED_KEY}'=True, '{DENIED_KEY}'=False")
        else:
            fail("Check 4: profile permission resolution wrong",
                 f"{GRANTED_KEY}={granted}, {DENIED_KEY}={denied}")

        # ------------------------------------------------------------------
        # Check 5: permission set ADDS a capability beyond the profile
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            pset_id = await conn.fetchval(
                """
                INSERT INTO permission_sets (org_id, name, description)
                VALUES ($1, $2, 'verify set')
                RETURNING id
                """,
                ORG_ID, TEST_PSET_NAME,
            )
            await conn.execute(
                """
                INSERT INTO permission_set_permissions (org_id, permission_set_id, permission_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (permission_set_id, permission_key) DO NOTHING
                """,
                ORG_ID, pset_id, SET_ADDED_KEY,
            )
            await conn.execute(
                """
                INSERT INTO user_permission_sets (user_id, permission_set_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, permission_set_id) DO NOTHING
                """,
                TEST_USER_ID, pset_id,
            )
        after_set = await user_has_permission(pool, TEST_USER_ID, SET_ADDED_KEY)
        # It must have been False before (profile alone) and True now.
        if denied is False and after_set is True:
            ok(f"Check 5: permission set ADDS '{SET_ADDED_KEY}' beyond profile "
               f"(was False via profile, now True)")
        else:
            fail("Check 5: permission set add wrong",
                 f"before(profile)={denied}, after(set)={after_set}")

        # ------------------------------------------------------------------
        # Build test entities for the beneficiary checks.
        #   member --beneficiary--> trust --ownership 50%--> llc
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            member_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'individual', $2) RETURNING id
                """,
                ORG_ID, TEST_ENTITY_PREFIX + " Member",
            ))
            trust_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'trust', $2) RETURNING id
                """,
                ORG_ID, TEST_ENTITY_PREFIX + " Trust",
            ))
            llc_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'llc', $2) RETURNING id
                """,
                ORG_ID, TEST_ENTITY_PREFIX + " LLC",
            ))
            # ownership: trust -> llc 50%
            await conn.execute(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id, relationship_type,
                     ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', 50, $4)
                """,
                ORG_ID, trust_id, llc_id, TEST_USER_ID,
            )

        # ------------------------------------------------------------------
        # Check 6: 'beneficiary' edge with null ownership_pct inserts OK
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            ben_id = await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id, relationship_type,
                     ownership_pct, created_by)
                VALUES ($1, $2, $3, 'beneficiary', NULL, $4)
                RETURNING id
                """,
                ORG_ID, member_id, trust_id, TEST_USER_ID,
            )
            ben_pct = await conn.fetchval(
                "SELECT ownership_pct FROM entity_relationships WHERE id = $1", ben_id,
            )
        if ben_id is not None and ben_pct is None:
            ok("Check 6: 'beneficiary' edge created with null ownership_pct")
        else:
            fail("Check 6: beneficiary insert wrong", f"id={ben_id}, pct={ben_pct}")

        # ------------------------------------------------------------------
        # Check 7: resolve_entity_set includes beneficiary-reached entity,
        #          same look-through as ownership (trust + its owned llc).
        # ------------------------------------------------------------------
        entity_set = await resolve_entity_set(
            pool, ORG_ID, {"type": "subtree", "root_id": member_id}
        )
        ids = {e["entity_id"] for e in entity_set}
        # member has no ownership edges — without beneficiary look-through,
        # trust and llc would be absent.
        if trust_id in ids and llc_id in ids and member_id in ids:
            ok("Check 7: resolve_entity_set(subtree, member) includes beneficiary "
               "target (trust) AND its ownership descendant (llc) — same look-through as owner")
        else:
            fail("Check 7: beneficiary look-through wrong",
                 f"member_in={member_id in ids}, trust_in={trust_id in ids}, "
                 f"llc_in={llc_id in ids}, set={entity_set}")

        # ------------------------------------------------------------------
        # Check 8: teardown leaves zero leftover rows
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("Check 8: teardown complete — zero leftover test rows (count=0)")
        else:
            fail("Check 8: leftover rows after teardown", f"count={remaining}")

    finally:
        # Best-effort final teardown even on exception.
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 44}")
    print(f"SOC Phase 1: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
