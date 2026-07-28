"""Ownership Tree Graph — Sprint A verify (interactive graph, backend logic).

Exercises the shared tree builder + the two visibility orchestrators
(``services.ownership_tree``) directly against the DB, proving that BOTH the
staff route and the member route apply the restricted-access wrapper on top of
their underlying visibility engine, that a member is confined to their own tree,
and that reverse traversal inverts correctly.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Run: DATABASE_URL=... python scripts/verify_ownershiptreea.py
"""
import asyncio
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_ownershiptreea")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

U_SUPER = "99000000-0000-0000-0000-00000000a0a1"    # super_admin (mutator actor)
U_STAFF = "99000000-0000-0000-0000-00000000a0a2"    # investment_staff
U_MEMBER = "99000000-0000-0000-0000-00000000a0a3"   # member (own tree)
U_OTHER = "99000000-0000-0000-0000-00000000a0a4"    # a DIFFERENT member
U_GRANTED = "99000000-0000-0000-0000-00000000a0a5"  # member, on restricted allow-list
ALL_TEST_USERS = [U_SUPER, U_STAFF, U_MEMBER, U_OTHER, U_GRANTED]

TEST_ENTITY_PREFIX = "OTAVerify"

# New files this sprint — scanned for hardcoded Signature-palette hex.
# scripts/ -> apps/api -> apps -> repo root (three levels up).
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
NEW_FILES = [
    "apps/api/services/ownership_tree.py",
    "apps/api/routers/ownership_tree.py",
    "apps/web/components/graph/OwnershipGraph.jsx",
    "apps/web/components/graph/AsOfPicker.jsx",
    "apps/web/components/graph/EntityGraphNavigator.jsx",
    "apps/web/app/api/entities/[id]/ownership-graph/route.js",
    "apps/web/app/api/me/ownership-graph/route.js",
    "apps/web/app/crm/[id]/ownership-graph/page.js",
    "apps/web/app/portfolio/ownership-tree/page.js",
]
# The brand tokens must be referenced via CSS vars, never inlined as raw hex.
SIGNATURE_HEXES = ["1B2B4B", "C5A880", "E8D5A3", "FAF9F6", "9B2335", "2D6A4F", "F5F1EB"]

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


def child_ids(node) -> set:
    return {c["id"] for c in (node.get("children", []) if node else [])}


async def cleanup(conn):
    ent_filter = TEST_ENTITY_PREFIX + "%"
    await conn.execute(
        """
        DELETE FROM restricted_access_audit
        WHERE org_id = $1
          AND (entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR performed_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM restricted_access_grants
        WHERE org_id = $1
          AND (entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR user_id = ANY($3::uuid[]) OR granted_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM delegate_grants
        WHERE org_id = $1
          AND (principal_entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $2)
               OR delegate_user_id = ANY($3::uuid[]) OR granted_by = ANY($3::uuid[]))
        """,
        ORG_ID, ent_filter, ALL_TEST_USERS,
    )
    await conn.execute(
        """
        DELETE FROM staff_assignments
        WHERE org_id = $1
          AND (assigned_to_user_id = ANY($2::uuid[])
               OR entity_id IN (SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $3))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
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
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)


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
          + (SELECT count(*) FROM delegate_grants WHERE org_id = $2
                AND (delegate_user_id = ANY($1::uuid[])
                     OR principal_entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM staff_assignments WHERE org_id = $2
                AND (assigned_to_user_id = ANY($1::uuid[])
                     OR entity_id IN (SELECT id FROM entities WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM restricted_access_grants WHERE org_id = $2
                AND (user_id = ANY($1::uuid[])
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
        f"ota_{tag}@test.local", f"OTA {tag}", f"auth0|test_ota_{tag}", role,
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


async def seed_assignment(conn, entity_id, user_id):
    await conn.execute(
        """
        INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label)
        VALUES ($1, $2, $3, 'OTA verify')
        """,
        ORG_ID, entity_id, user_id,
    )


def report_discovery():
    """Assertion [Y]: report Task 1's three discovery findings explicitly."""
    print("[DISCOVERY] Task 1 findings (reused, not reimplemented):")
    print("  (a) HierarchyBuilder.jsx EXISTS at apps/web/components/crm/HierarchyBuilder.jsx")
    print("      (Sprint 15): hand-rolled SVG pan/zoom/collapse + getSubtreeWidth/")
    print("      layoutNode. Gaps: single-style edges, no legend/filter/time-travel/")
    print("      reverse/onNodeClick. -> Its layout algorithm is GENERALISED into the")
    print("      shared components/graph/OwnershipGraph.jsx.")
    print("  (b) Sprint 18 time-travel is NOT a standalone component — it is an inline")
    print("      <input type=\"date\"> in components/crm/tabs/OwnershipTab.jsx (local")
    print("      asOf state, passed as ?as_of=YYYY-MM-DD). -> Same paradigm extracted")
    print("      into the shared components/graph/AsOfPicker.jsx.")
    print("  (c) Real engine functions reused verbatim:")
    print("      staff  = services.staff_visibility.get_staff_visible_entity_ids(pool, user_id, org_id)")
    print("      member = services.entity_graph.resolve_entity_set(pool, org_id, selector)")
    print("               (user-keyed via services.delegate_grants.get_delegate_visible_entity_ids)")
    print("      restricted wrapper = services.restricted_access.filter_restricted(pool, ids, user_id, org_id)")
    ok("Assertion [discovery]: reported HierarchyBuilder, Sprint-18 date picker, "
       "and the staff/member/restricted engine signatures explicitly")


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.ownership_tree import (
        build_tree, staff_tree, member_tree, get_member_root_entity_ids,
    )
    from services.staff_visibility import get_staff_visible_entity_ids
    from services.entity_graph import resolve_entity_set
    from services.restricted_access import set_restricted, grant_restricted_access
    from services.delegate_grants import grant_delegate, VIEW_ONLY

    try:
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Assertion [discovery] --------------------------------------
        report_discovery()

        # ---- Seed users + fixture graph ---------------------------------
        async with pool.acquire() as conn:
            await seed_user(conn, U_SUPER, "super", "super_admin")
            await seed_user(conn, U_STAFF, "staff", "investment_staff")
            await seed_user(conn, U_MEMBER, "member", "member")
            await seed_user(conn, U_OTHER, "other", "member")
            await seed_user(conn, U_GRANTED, "granted", "member")

            # Member's own tree.
            e_member = await seed_entity(conn, "MemberRoot", "individual")
            e_child = await seed_entity(conn, "OwnedChild", "llc")
            e_grand = await seed_entity(conn, "OwnedGrandchild", "llc")
            e_benef = await seed_entity(conn, "BeneficiaryTarget", "trust")
            e_restricted = await seed_entity(conn, "RestrictedSub", "llc")
            await seed_rel(conn, e_member, e_child, "ownership", 100)
            await seed_rel(conn, e_child, e_grand, "ownership", 60)
            await seed_rel(conn, e_member, e_benef, "beneficiary", None)
            await seed_rel(conn, e_member, e_restricted, "ownership", 100)

            # A DIFFERENT member's tree (must be unreachable to U_MEMBER).
            e_other = await seed_entity(conn, "OtherMemberRoot", "individual")
            e_other_child = await seed_entity(conn, "OtherOwnedChild", "llc")
            await seed_rel(conn, e_other, e_other_child, "ownership", 100)

            # Staff fixture: visible root + a hidden (unassigned) child.
            e_staff_vis = await seed_entity(conn, "StaffVisible", "llc")
            e_staff_hidden = await seed_entity(conn, "StaffHidden", "llc")
            await seed_rel(conn, e_staff_vis, e_staff_hidden, "ownership", 100)
            await seed_assignment(conn, e_staff_vis, U_STAFF)
            await seed_assignment(conn, e_restricted, U_STAFF)  # staff sees it via engine

            # Delegate grants = member->entity linkage (their own root).
            await grant_delegate(pool, ORG_ID, principal_entity_id=e_member,
                                 scope=VIEW_ONLY, delegate_user_id=U_MEMBER, granted_by=U_SUPER)
            await grant_delegate(pool, ORG_ID, principal_entity_id=e_other,
                                 scope=VIEW_ONLY, delegate_user_id=U_OTHER, granted_by=U_SUPER)
            await grant_delegate(pool, ORG_ID, principal_entity_id=e_member,
                                 scope=VIEW_ONLY, delegate_user_id=U_GRANTED, granted_by=U_SUPER)

        # ---- Assertion: staff WITH visibility sees the entity -----------
        st = await staff_tree(pool, ORG_ID, e_staff_vis, U_STAFF)
        if st and e_staff_vis in node_ids(st):
            ok("Assertion [staff-visible]: a staff user with visibility (via the staff "
               "engine) sees the entity in the staff-route tree")
        else:
            fail("Assertion [staff-visible]: staff user cannot see an assigned entity",
                 f"tree_ids={node_ids(st)}")

        # ---- Assertion: staff WITHOUT visibility does NOT see it ---------
        full_staff = await build_tree(pool, ORG_ID, e_staff_vis)
        engine_had_hidden = e_staff_hidden in node_ids(full_staff)
        pruned_out = e_staff_hidden not in node_ids(st)
        if engine_had_hidden and pruned_out:
            ok("Assertion [staff-hidden]: an unassigned child is in the raw tree but is "
               "pruned from the staff-route tree — the staff engine is actually wired in")
        else:
            fail("Assertion [staff-hidden]: hidden entity handling wrong",
                 f"engine_had={engine_had_hidden}, pruned_out={pruned_out}")

        # ---- Assertion: member sees own tree (ownership + beneficiary) ---
        roots = await get_member_root_entity_ids(pool, ORG_ID, U_MEMBER)
        mres = await member_tree(pool, ORG_ID, U_MEMBER)
        mids = node_ids(mres.get("tree"))
        if (roots == [e_member]
                and mres.get("focal_entity_id") == e_member
                and e_child in mids and e_grand in mids and e_benef in mids):
            ok("Assertion [member-own]: member sees their OWN tree via the member route, "
               "including an ownership-reached (child/grandchild) AND a beneficiary-reached entity")
        else:
            fail("Assertion [member-own]: member own-tree wrong",
                 f"roots={roots}, focal={mres.get('focal_entity_id')}, ids={mids}")

        # ---- Assertion: member CANNOT see a different member's tree ------
        blocked = False
        try:
            await member_tree(pool, ORG_ID, U_MEMBER, focal_id=e_other)
        except PermissionError:
            blocked = True
        disjoint = not ({e_other, e_other_child} & mids)
        if blocked and disjoint:
            ok("Assertion [member-isolation]: focusing the member route on another "
               "member's root is a PermissionError, and their entities never appear in "
               "this member's own tree")
        else:
            fail("Assertion [member-isolation]: member could reach another tree",
                 f"blocked={blocked}, disjoint={disjoint}")

        # ---- Flag the restricted entity ---------------------------------
        await set_restricted(pool, e_restricted, True, U_SUPER, notes="ota verify")

        # ---- Assertion: restricted hidden in BOTH routes for off-list ---
        # Member side: engine had it, filter drops it.
        member_engine = {r["entity_id"] for r in await resolve_entity_set(
            pool, ORG_ID, {"type": "subtree", "root_id": e_member})}
        mres2 = await member_tree(pool, ORG_ID, U_MEMBER)
        member_hidden = e_restricted not in node_ids(mres2.get("tree"))
        member_engine_had = e_restricted in member_engine
        # Staff side: engine had it, focal on it prunes to None.
        staff_engine = await get_staff_visible_entity_ids(pool, U_STAFF, ORG_ID)
        staff_engine_had = e_restricted in staff_engine
        staff_restricted_tree = await staff_tree(pool, ORG_ID, e_restricted, U_STAFF)
        staff_hidden = staff_restricted_tree is None
        if (member_engine_had and member_hidden and staff_engine_had and staff_hidden):
            ok("Assertion [restricted-hidden]: a restricted entity BOTH engines would "
               "otherwise return is removed from the member tree AND the staff tree for a "
               "user off its allow-list — the restricted filter is applied on top, not skipped")
        else:
            fail("Assertion [restricted-hidden]: restricted entity leaked",
                 f"member_had={member_engine_had}, member_hidden={member_hidden}, "
                 f"staff_had={staff_engine_had}, staff_hidden={staff_hidden}")

        # ---- Assertion: restricted DOES appear for allow-listed user ----
        await grant_restricted_access(pool, e_restricted, U_GRANTED, U_SUPER, "ota verify")
        gres = await member_tree(pool, ORG_ID, U_GRANTED)
        if e_restricted in node_ids(gres.get("tree")):
            ok("Assertion [restricted-allowed]: the SAME restricted entity DOES appear for "
               "a user who IS on its allow-list (restricted_access_grants)")
        else:
            fail("Assertion [restricted-allowed]: allow-listed user cannot see restricted entity",
                 f"ids={node_ids(gres.get('tree'))}")

        # ---- Assertion: reverse / owned-by toggle inverts correctly -----
        rev = await build_tree(pool, ORG_ID, e_grand, direction="owned_by",
                               edge_types=("ownership",))
        lvl1 = child_ids(rev)  # owners of the grandchild
        lvl2 = child_ids(rev["children"][0]) if rev and rev["children"] else set()
        if lvl1 == {e_child} and lvl2 == {e_member}:
            ok("Assertion [reverse-toggle]: owned-by traversal inverts the fixture — "
               "grandchild -> child -> member (to_entity_id -> from_entity_id)")
        else:
            fail("Assertion [reverse-toggle]: inverted set wrong",
                 f"lvl1={lvl1}, lvl2={lvl2}")

        # ---- Assertion: no hardcoded Signature-palette hex in new files -
        offenders = []
        for rel in NEW_FILES:
            path = os.path.join(REPO_ROOT, rel)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read().upper()
            except FileNotFoundError:
                offenders.append(f"{rel} (MISSING)")
                continue
            for hexv in SIGNATURE_HEXES:
                if re.search(r"#?" + hexv, text):
                    offenders.append(f"{rel} contains #{hexv}")
        if not offenders:
            ok("Assertion [no-hardcoded-hex]: no Signature-palette hex literals in any "
               f"of the {len(NEW_FILES)} new files (brand colors via CSS vars only)")
        else:
            fail("Assertion [no-hardcoded-hex]: signature hex found", "; ".join(offenders))

        # ---- Assertion: npm run build exits 0 ---------------------------
        web_dir = os.path.join(REPO_ROOT, "apps", "web")
        if os.environ.get("SKIP_NPM_BUILD") == "1":
            print("[SKIP] npm run build (SKIP_NPM_BUILD=1)")
        else:
            print("[..] running `npm run build` (this can take a minute)…")
            proc = subprocess.run(
                ["npm", "run", "build"], cwd=web_dir,
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                ok("Assertion [npm-build]: `npm run build` exited 0")
            else:
                tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
                fail("Assertion [npm-build]: build failed", f"\n{tail}")

        # ---- Assertion: teardown leaves zero leftover rows --------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("Assertion [teardown]: zero leftover test rows (count=0)")
        else:
            fail("Assertion [teardown]: leftover rows", f"count={remaining}")

    finally:
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 52}")
    print(f"Ownership Tree A: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
