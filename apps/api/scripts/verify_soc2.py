"""SOC Phase 2 verify — Staff visibility: hierarchy + teams + assignment.

Proves the unified staff-visibility resolver
(``services.staff_visibility.get_staff_visible_entity_ids``) is correct AND
that existing endpoint behavior is UNCHANGED (the resolver is standalone and
not wired into any enforcement path this phase).

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Assertions:
  1. teams / team_members / staff_assignments exist + users.manager_id column;
     the XOR CHECK constraint rejects BOTH-set and NEITHER-set assignments.
  2. Direct assignment: a user assigned to an entity sees it via the resolver.
  3. Team assignment: a member of a team assigned to an entity sees it.
  4. Hierarchy: a manager sees an entity assigned to a direct report AND to a
     report's report (transitive, 2+ levels) — and the walk is cycle-safe.
  5. A user with NO assignments, on no relevant team, and managing no one with
     access resolves to an EMPTY set (the resolver actually restricts).
  6. EXISTING endpoint behavior UNCHANGED: no router imports the resolver, and
     the real GET /entities WHERE clause still returns entities org-wide for a
     staff user whose resolver set is empty (enforcement not switched over).
  7. Teardown: zero leftover rows (confirmed via count(*)).

Run: DATABASE_URL=... python scripts/verify_soc2.py
"""
import asyncio
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soc2")
    sys.exit(0)

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTERS_DIR = os.path.join(API_DIR, "routers")

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test users (deleted by exact id at teardown).
U_MGR = "99000000-0000-0000-0000-0000000002a1"       # top of the chain
U_REPORT = "99000000-0000-0000-0000-0000000002a2"    # reports to U_MGR
U_SUBREPORT = "99000000-0000-0000-0000-0000000002a3"  # reports to U_REPORT
U_DIRECT = "99000000-0000-0000-0000-0000000002a4"    # has a direct assignment
U_TEAM = "99000000-0000-0000-0000-0000000002a5"      # member of the test team
U_ISOLATED = "99000000-0000-0000-0000-0000000002a6"  # nothing at all
ALL_TEST_USERS = [U_MGR, U_REPORT, U_SUBREPORT, U_DIRECT, U_TEAM, U_ISOLATED]

TEST_ENTITY_PREFIX = "SOC2Verify"
TEST_TEAM_NAME = "SOC2 Verify Team"

# The exact WHERE clause used by GET /entities (routers/entities.py:135-137),
# replicated to prove the existing endpoint path is still org-wide and NOT
# gated by the new resolver.
EXISTING_LIST_ENTITIES_WHERE = (
    "org_id = $1 AND valid_to IS NULL AND system_to IS NULL AND is_active = true"
)

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
    # staff_assignments first (child of teams / users / entities).
    await conn.execute(
        """
        DELETE FROM staff_assignments
        WHERE org_id = $1
          AND (
                assigned_to_user_id = ANY($2::uuid[])
             OR assigned_to_team_id IN (
                    SELECT id FROM teams WHERE org_id = $1 AND name = $3)
             OR entity_id IN (
                    SELECT id FROM entities
                    WHERE org_id = $1 AND display_name LIKE $4)
          )
        """,
        ORG_ID, ALL_TEST_USERS, TEST_TEAM_NAME, TEST_ENTITY_PREFIX + "%",
    )
    # team_members (child of teams / users).
    await conn.execute(
        """
        DELETE FROM team_members
        WHERE team_id IN (SELECT id FROM teams WHERE org_id = $1 AND name = $2)
           OR user_id = ANY($3::uuid[])
        """,
        ORG_ID, TEST_TEAM_NAME, ALL_TEST_USERS,
    )
    # teams.
    await conn.execute(
        "DELETE FROM teams WHERE org_id = $1 AND name = $2", ORG_ID, TEST_TEAM_NAME,
    )
    # entities (staff_assignments referencing them are gone).
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, TEST_ENTITY_PREFIX + "%",
    )
    # Break the self-referential manager_id FK before deleting users.
    await conn.execute(
        "UPDATE users SET manager_id = NULL WHERE id = ANY($1::uuid[])",
        ALL_TEST_USERS,
    )
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS,
    )
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS,
    )


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities
                WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM teams WHERE org_id = $2 AND name = $4)
          + (SELECT count(*) FROM team_members
                WHERE team_id IN (SELECT id FROM teams WHERE org_id = $2 AND name = $4)
                   OR user_id = ANY($1::uuid[]))
          + (SELECT count(*) FROM staff_assignments
                WHERE org_id = $2
                  AND (assigned_to_user_id = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
        """,
        ALL_TEST_USERS, ORG_ID, TEST_ENTITY_PREFIX + "%", TEST_TEAM_NAME,
    ))


async def seed_user(conn, user_id, tag):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, ORG_ID,
        f"soc2_{tag}@test.local", f"SOC2 {tag}", f"auth0|test_soc2_{tag}",
    )


async def seed_entity(conn, tag) -> str:
    return str(await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', $2) RETURNING id
        """,
        ORG_ID, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


async def assign_user(conn, entity_id, user_id):
    await conn.execute(
        """
        INSERT INTO staff_assignments
            (org_id, entity_id, assigned_to_user_id, role_label)
        VALUES ($1, $2, $3, 'SOC2 verify')
        """,
        ORG_ID, entity_id, user_id,
    )


async def assign_team(conn, entity_id, team_id):
    await conn.execute(
        """
        INSERT INTO staff_assignments
            (org_id, entity_id, assigned_to_team_id, role_label)
        VALUES ($1, $2, $3, 'SOC2 verify')
        """,
        ORG_ID, entity_id, team_id,
    )


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.staff_visibility import get_staff_visible_entity_ids

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Seed users + hierarchy --------------------------------------
        #   U_MGR
        #    └─ U_REPORT            (manager_id = U_MGR)
        #        └─ U_SUBREPORT     (manager_id = U_REPORT)   ← 2 levels down
        #   U_DIRECT, U_TEAM, U_ISOLATED are standalone.
        async with pool.acquire() as conn:
            await seed_user(conn, U_MGR, "mgr")
            await seed_user(conn, U_REPORT, "report")
            await seed_user(conn, U_SUBREPORT, "subreport")
            await seed_user(conn, U_DIRECT, "direct")
            await seed_user(conn, U_TEAM, "team")
            await seed_user(conn, U_ISOLATED, "isolated")
            await conn.execute(
                "UPDATE users SET manager_id = $1 WHERE id = $2", U_MGR, U_REPORT)
            await conn.execute(
                "UPDATE users SET manager_id = $1 WHERE id = $2", U_REPORT, U_SUBREPORT)

        # ---- Seed entities + team + assignments --------------------------
        async with pool.acquire() as conn:
            e_direct = await seed_entity(conn, "DirectEntity")
            e_team = await seed_entity(conn, "TeamEntity")
            e_report = await seed_entity(conn, "ReportEntity")
            e_subreport = await seed_entity(conn, "SubReportEntity")
            e_unassigned = await seed_entity(conn, "UnassignedEntity")

            team_id = str(await conn.fetchval(
                "INSERT INTO teams (org_id, name, description) "
                "VALUES ($1, $2, 'SOC2 verify team') RETURNING id",
                ORG_ID, TEST_TEAM_NAME,
            ))
            await conn.execute(
                "INSERT INTO team_members (team_id, user_id) VALUES ($1, $2)",
                team_id, U_TEAM,
            )

            await assign_user(conn, e_direct, U_DIRECT)
            await assign_team(conn, e_team, team_id)
            await assign_user(conn, e_report, U_REPORT)
            await assign_user(conn, e_subreport, U_SUBREPORT)
            # e_unassigned deliberately has NO assignment.

        # ------------------------------------------------------------------
        # Assertion 1: structure exists + XOR CHECK constraint enforced
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            regs = await conn.fetchrow(
                """
                SELECT to_regclass('public.teams')             AS teams,
                       to_regclass('public.team_members')       AS team_members,
                       to_regclass('public.staff_assignments')  AS staff_assignments
                """
            )
            has_manager_id = int(await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'users'
                  AND column_name = 'manager_id'
                """
            ))

        struct_ok = bool(regs["teams"] and regs["team_members"]
                         and regs["staff_assignments"] and has_manager_id == 1)

        # BOTH targets set → must be rejected by the CHECK.
        both_rejected = False
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO staff_assignments
                        (org_id, entity_id, assigned_to_user_id, assigned_to_team_id)
                    VALUES ($1, $2, $3, $4)
                    """,
                    ORG_ID, e_unassigned, U_DIRECT, team_id,
                )
        except asyncpg.exceptions.CheckViolationError:
            both_rejected = True

        # NEITHER target set → must be rejected by the CHECK.
        neither_rejected = False
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO staff_assignments (org_id, entity_id)
                    VALUES ($1, $2)
                    """,
                    ORG_ID, e_unassigned,
                )
        except asyncpg.exceptions.CheckViolationError:
            neither_rejected = True

        if struct_ok and both_rejected and neither_rejected:
            ok("Assertion 1: teams/team_members/staff_assignments + users.manager_id "
               "exist; XOR CHECK rejects both-set and neither-set assignments")
        else:
            fail("Assertion 1: structure / CHECK constraint wrong",
                 f"struct_ok={struct_ok}, both_rejected={both_rejected}, "
                 f"neither_rejected={neither_rejected}")

        # ------------------------------------------------------------------
        # Assertion 2: direct assignment visible via resolver
        # ------------------------------------------------------------------
        direct_set = await get_staff_visible_entity_ids(pool, U_DIRECT, ORG_ID)
        if e_direct in direct_set:
            ok("Assertion 2: direct assignment — U_DIRECT sees its assigned entity")
        else:
            fail("Assertion 2: direct assignment not visible", f"set={direct_set}")

        # ------------------------------------------------------------------
        # Assertion 3: team assignment visible via resolver
        # ------------------------------------------------------------------
        team_set = await get_staff_visible_entity_ids(pool, U_TEAM, ORG_ID)
        if e_team in team_set:
            ok("Assertion 3: team assignment — U_TEAM (team member) sees the "
               "entity assigned to their team")
        else:
            fail("Assertion 3: team assignment not visible", f"set={team_set}")

        # ------------------------------------------------------------------
        # Assertion 4: hierarchy — manager sees reports' entities (transitive)
        # ------------------------------------------------------------------
        mgr_set = await get_staff_visible_entity_ids(pool, U_MGR, ORG_ID)
        sees_report = e_report in mgr_set          # direct report (1 level)
        sees_subreport = e_subreport in mgr_set    # report's report (2 levels)
        # Restriction sanity: manager must NOT see an unrelated user's entity.
        not_sees_unrelated = e_direct not in mgr_set
        if sees_report and sees_subreport and not_sees_unrelated:
            ok("Assertion 4: hierarchy — U_MGR sees entity of direct report AND "
               "report's report (transitive 2 levels), but not an unrelated user's")
        else:
            fail("Assertion 4: hierarchy resolution wrong",
                 f"sees_report={sees_report}, sees_subreport={sees_subreport}, "
                 f"not_sees_unrelated={not_sees_unrelated}")

        # ---- Cycle safety: a back-edge in the manager chain must not loop --
        cycle_safe = False
        try:
            async with pool.acquire() as conn:
                # Introduce U_MGR.manager_id = U_SUBREPORT →
                # U_MGR → (reports) U_REPORT → U_SUBREPORT → (manager) U_MGR ...
                await conn.execute(
                    "UPDATE users SET manager_id = $1 WHERE id = $2",
                    U_SUBREPORT, U_MGR,
                )
            cyc_set = await asyncio.wait_for(
                get_staff_visible_entity_ids(pool, U_MGR, ORG_ID), timeout=15,
            )
            # Still resolves (terminates) and still includes the reports' entities.
            cycle_safe = e_report in cyc_set and e_subreport in cyc_set
        except asyncio.TimeoutError:
            cycle_safe = False
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET manager_id = NULL WHERE id = $1", U_MGR)
        if cycle_safe:
            ok("Assertion 4b: hierarchy walk is cycle-safe — a manager_id "
               "back-edge terminates and still resolves reports' entities")
        else:
            fail("Assertion 4b: cycle in manager chain not handled defensively")

        # ------------------------------------------------------------------
        # Assertion 5: isolated user resolves to an EMPTY set
        # ------------------------------------------------------------------
        isolated_set = await get_staff_visible_entity_ids(pool, U_ISOLATED, ORG_ID)
        if isolated_set == set():
            ok("Assertion 5: isolated user (no assignment, no team, manages no one) "
               "→ EMPTY set (resolver genuinely restricts)")
        else:
            fail("Assertion 5: isolated user set not empty", f"set={isolated_set}")

        # ------------------------------------------------------------------
        # Assertion 6: EXISTING endpoint behavior UNCHANGED
        #   (a) no router wires in the resolver, and
        #   (b) the real GET /entities WHERE clause still returns entities
        #       org-wide — including for U_ISOLATED, whose resolver set is empty.
        # ------------------------------------------------------------------
        wired_in = []
        for path in glob.glob(os.path.join(ROUTERS_DIR, "*.py")):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            if "staff_visibility" in src or "get_staff_visible_entity_ids" in src:
                wired_in.append(os.path.basename(path))

        async with pool.acquire() as conn:
            existing_rows = await conn.fetch(
                f"SELECT id FROM entities WHERE {EXISTING_LIST_ENTITIES_WHERE} "
                f"AND display_name LIKE $2",
                ORG_ID, TEST_ENTITY_PREFIX + "%",
            )
        existing_ids = {str(r["id"]) for r in existing_rows}
        # The unassigned entity is invisible to U_ISOLATED via the resolver,
        # yet the existing org-wide query still returns it — proving the
        # endpoint is NOT gated by the resolver.
        endpoint_orgwide = (
            e_unassigned in existing_ids
            and e_unassigned not in isolated_set
            and e_direct in existing_ids
        )
        if not wired_in and endpoint_orgwide:
            ok("Assertion 6: existing endpoint UNCHANGED — no router imports the "
               "resolver; GET /entities WHERE still returns entities org-wide "
               "(incl. one the resolver hides from an empty-set user)")
        else:
            fail("Assertion 6: existing endpoint behavior changed / resolver wired in",
                 f"wired_in={wired_in}, endpoint_orgwide={endpoint_orgwide}")

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
    print(f"SOC Phase 2: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
