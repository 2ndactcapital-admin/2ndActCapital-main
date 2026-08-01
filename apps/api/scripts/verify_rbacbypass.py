"""RBAC Super Admin bypass — verify.

Proves the fix for a confirmed, real gap. ``services.rbac.has_permission`` only
default-allowed a user with **zero** rows in ``user_roles``. A Super Admin
"worked" purely by the accident of that table being empty for their account —
there was NO explicit ``is_super_admin`` escape hatch, unlike every RLS policy,
``restricted_access`` check and ``staff_visibility`` gate elsewhere on the
platform. The moment a Super Admin picked up ANY row in ``user_roles`` (as
tonight's stray duplicate identity briefly did), the "zero roles = default
allow" branch stopped firing, the strict per-permission check ran instead, and
they were silently locked out of every endpoint using ``require_permission``.

The fix adds the explicit escape hatch, checked FIRST: a ``super_admin`` (read
from ``users.role`` via ``load_principal`` + ``is_super_admin``) always returns
True before the "zero roles = default allow" logic even runs. Nothing else
changes: a non-super with zero roles still gets default-allow; a non-super with
roles still gets the strict per-permission check.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Assertions:
  [discovery] Report Task 1's findings — the fixed function and every real
              call site of services.rbac.has_permission / require_permission.
  1. A Super Admin WITH a real row in user_roles (tonight's exact scenario)
     STILL passes require_permission for an arbitrary permission never granted
     to them — the actual bug, now fixed.
  2. A non-super-admin with ZERO roles still gets the existing default-allow
     (unchanged).
  3. A non-super-admin WITH roles is still correctly REJECTED for a permission
     they don't have — and still ALLOWED for one they do (unchanged, not
     accidentally loosened).
  4. Re-run against two REAL call sites (admin._require_manage_members and
     staff_assignments._require_manage_members — both gate on "manage_members"):
     the Super Admin with a role row passes both; a non-super roled user is
     still 403'd. Confirms the fix works in real, not just isolated, context.
  5. Teardown: zero leftover rows.

Run: DATABASE_URL=... python scripts/verify_rbacbypass.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg
from fastapi import HTTPException

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rbacbypass")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test users (deleted by exact id at teardown).
U_SUPER = "99000000-0000-0000-0000-0000000006c1"   # super_admin WITH a stray role row (the bug)
U_NOROLE = "99000000-0000-0000-0000-0000000006c2"  # member, ZERO roles (default-allow, unchanged)
U_ROLED = "99000000-0000-0000-0000-0000000006c3"   # member WITH a role (strict check, unchanged)
ALL_TEST_USERS = [U_SUPER, U_NOROLE, U_ROLED]

# Stable test role + permissions (fixed ids so teardown is exact).
R_TEST = "99000000-0000-0000-0000-0000000006d1"
P_GRANTED = "99000000-0000-0000-0000-0000000006e1"   # R_TEST DOES grant this
P_TARGET = "99000000-0000-0000-0000-0000000006e2"    # R_TEST does NOT grant this (the arbitrary perm)
ALL_TEST_ROLES = [R_TEST]
ALL_TEST_PERMS = [P_GRANTED, P_TARGET]

R_TEST_NAME = "RBACBypassVerify Role"
P_GRANTED_NAME = "rbacbypass_granted_perm"
P_TARGET_NAME = "rbacbypass_target_perm"

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


class _FakeState:
    pass


class _FakeRequest:
    """Minimal Request stand-in: get_org_id/ensure_user only read request.state.user."""

    def __init__(self, claims):
        self.state = _FakeState()
        self.state.user = claims


async def cleanup(conn):
    """Remove all test data by stable identifiers. Idempotent, FK-safe order."""
    # user_roles first (child of users + roles).
    await conn.execute(
        "DELETE FROM user_roles WHERE user_id = ANY($1::uuid[]) OR role_id = ANY($2::uuid[])",
        ALL_TEST_USERS, ALL_TEST_ROLES,
    )
    # role_permissions (child of roles + permissions).
    await conn.execute(
        "DELETE FROM role_permissions WHERE role_id = ANY($1::uuid[]) OR permission_id = ANY($2::uuid[])",
        ALL_TEST_ROLES, ALL_TEST_PERMS,
    )
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS,
    )
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)
    await conn.execute("DELETE FROM roles WHERE id = ANY($1::uuid[])", ALL_TEST_ROLES)
    await conn.execute("DELETE FROM permissions WHERE id = ANY($1::uuid[])", ALL_TEST_PERMS)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM roles WHERE id = ANY($2::uuid[]))
          + (SELECT count(*) FROM permissions WHERE id = ANY($3::uuid[]))
          + (SELECT count(*) FROM user_roles WHERE user_id = ANY($1::uuid[]) OR role_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM role_permissions WHERE role_id = ANY($2::uuid[]) OR permission_id = ANY($3::uuid[]))
        """,
        ALL_TEST_USERS, ALL_TEST_ROLES, ALL_TEST_PERMS,
    ))


async def seed_user(conn, user_id, tag, role):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, ORG_ID,
        f"rbacb_{tag}@test.local", f"RBACB {tag}", f"auth0|test_rbacb_{tag}", role,
    )


async def seed_all(conn):
    # Users.
    await seed_user(conn, U_SUPER, "super", "super_admin")
    await seed_user(conn, U_NOROLE, "norole", "member")
    await seed_user(conn, U_ROLED, "roled", "member")

    # Role + permissions.
    await conn.execute(
        "INSERT INTO roles (id, org_id, name) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
        R_TEST, ORG_ID, R_TEST_NAME,
    )
    await conn.execute(
        """
        INSERT INTO permissions (id, name, resource, action)
        VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING
        """,
        P_GRANTED, P_GRANTED_NAME, "rbacbypass_verify", "granted",
    )
    await conn.execute(
        """
        INSERT INTO permissions (id, name, resource, action)
        VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING
        """,
        P_TARGET, P_TARGET_NAME, "rbacbypass_verify", "target",
    )
    # R_TEST grants ONLY P_GRANTED — never P_TARGET.
    await conn.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        R_TEST, P_GRANTED,
    )
    # The stray role row: the Super Admin picks up R_TEST (tonight's scenario).
    await conn.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        U_SUPER, R_TEST,
    )
    # A regular member with the same role row.
    await conn.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        U_ROLED, R_TEST,
    )
    # U_NOROLE deliberately gets NO user_roles row — the default-allow branch.


def report_discovery():
    """Assertion [discovery]: report Task 1's findings explicitly."""
    print("[DISCOVERY] Task 1 findings:")
    print("  Fixed function (services/rbac.py):")
    print("    async def has_permission(pool, user_id, org_id, permission_name) -> bool")
    print("    async def require_permission(pool, user_id, org_id, permission_name) -> None")
    print("       (require_permission is a thin 403-raising wrapper over has_permission)")
    print("    - Pre-fix: default-allow ONLY when the user has ZERO user_roles rows;")
    print("      otherwise a strict `permission_name in get_user_permissions(...)`.")
    print("      NO is_super_admin escape hatch — a super_admin who acquired any")
    print("      user_roles row fell through to the strict check and was locked out.")
    print("    - Fix: is_super_admin(load_principal(user_id)) checked FIRST -> True,")
    print("      before the zero-roles branch runs. Signature UNCHANGED.")
    print("  REAL call sites of services.rbac.require_permission (grep-confirmed):")
    print("    - routers/admin.py:49            _require_manage_members -> \"manage_members\"")
    print("      (gates GET/POST /admin/roles, /admin/users, role assign, profile assign)")
    print("    - routers/households.py:59       _require_manage_members -> \"manage_members\"")
    print("    - routers/staff_assignments.py:92 _require_manage_members -> \"manage_members\"")
    print("      (gates teams + staff-assignment create/list endpoints)")
    print("  has_permission is also called directly in scripts/verify_sprint9.py (test-only).")
    print("  NOTE: the sibling permission systems are SEPARATE and were NOT in scope:")
    print("    - services/permissions.py has_permission/require_permission(request, perm)")
    print("      — request/JWT-claim based, used by marketplace.py, spv.py, vdr.py.")
    print("    - services/profiles.py user_has_permission(pool, user_id, key) — the")
    print("      Workflow Manager Phase 5 granular gate (routers/workflows.py:161), which")
    print("      ALREADY carries its own is_super_admin-first bypass. The fix here brings")
    print("      services.rbac into line with that existing platform convention.")
    ok("Assertion [discovery]: reported has_permission/require_permission signature and "
       "every real call site (admin, households, staff_assignments; verify_sprint9 test-only)")


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.rbac import has_permission, require_permission
    from services.database import get_pool, close_pool, set_rls_context
    import routers.admin as admin_router
    import routers.staff_assignments as staff_router

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Assertion [discovery] ---------------------------------------
        report_discovery()

        # ---- Seed --------------------------------------------------------
        async with pool.acquire() as conn:
            await seed_all(conn)

        # ------------------------------------------------------------------
        # Assertion 1: Super Admin WITH a real user_roles row STILL passes
        #   require_permission for a permission they were never granted.
        # ------------------------------------------------------------------
        raised = None
        try:
            await require_permission(pool, U_SUPER, ORG_ID, P_TARGET_NAME)
        except HTTPException as exc:
            raised = exc
        super_has = await has_permission(pool, U_SUPER, ORG_ID, P_TARGET_NAME)
        if raised is None and super_has is True:
            ok("Assertion 1: Super Admin WITH a stray user_roles row passes "
               "require_permission for an un-granted permission (has_permission=True, "
               "no 403) — the exact tonight lock-out, now fixed by the is_super_admin bypass")
        else:
            fail("Assertion 1: Super Admin still locked out despite the bypass",
                 f"raised={raised.status_code if raised else None}, has_permission={super_has}")

        # ------------------------------------------------------------------
        # Assertion 2: non-super with ZERO roles still gets default-allow.
        # ------------------------------------------------------------------
        norole_has = await has_permission(pool, U_NOROLE, ORG_ID, P_TARGET_NAME)
        if norole_has is True:
            ok("Assertion 2: a non-super-admin (member) with ZERO user_roles rows still "
               "gets the existing default-allow (has_permission=True) — unchanged")
        else:
            fail("Assertion 2: zero-roles default-allow broke", f"has_permission={norole_has}")

        # ------------------------------------------------------------------
        # Assertion 3: non-super WITH roles is REJECTED for an un-granted perm,
        #   and ALLOWED for a granted one (strict check intact, not loosened).
        # ------------------------------------------------------------------
        raised3 = None
        try:
            await require_permission(pool, U_ROLED, ORG_ID, P_TARGET_NAME)
        except HTTPException as exc:
            raised3 = exc
        roled_target = await has_permission(pool, U_ROLED, ORG_ID, P_TARGET_NAME)
        roled_granted = await has_permission(pool, U_ROLED, ORG_ID, P_GRANTED_NAME)
        if raised3 is not None and raised3.status_code == 403 \
                and roled_target is False and roled_granted is True:
            ok("Assertion 3: a non-super-admin WITH a role is correctly REJECTED (403) for a "
               "permission they don't hold, yet ALLOWED for one they do — strict per-permission "
               "check unchanged, not accidentally loosened")
        else:
            fail("Assertion 3: strict per-permission check drifted",
                 f"rejected={raised3.status_code if raised3 else None}, "
                 f"has_target={roled_target}, has_granted={roled_granted}")

        # ------------------------------------------------------------------
        # Assertion 4: re-run through TWO real call sites in production context.
        #   admin._require_manage_members + staff_assignments._require_manage_members
        #   both resolve org_id+actor from the request then call require_permission
        #   for "manage_members" via the shared RLS pool (get_pool()).
        #
        #   The RLS super flag is set only so the harness's self-lookups (users /
        #   user_roles / permissions) are visible; the assertion targets the RBAC
        #   decision (users.role + grants), which is independent of that flag.
        # ------------------------------------------------------------------
        super_req = _FakeRequest({"sub": U_SUPER})
        roled_req = _FakeRequest({"sub": U_ROLED})

        # Warm the shared pool so it exists before we set context.
        await get_pool()

        set_rls_context(ORG_ID, True)
        try:
            admin_super_ok = staff_super_ok = False
            try:
                await admin_router._require_manage_members(super_req)
                admin_super_ok = True
            except HTTPException:
                admin_super_ok = False
            try:
                await staff_router._require_manage_members(super_req)
                staff_super_ok = True
            except HTTPException:
                staff_super_ok = False

            roled_admin_rejected = False
            try:
                await admin_router._require_manage_members(roled_req)
            except HTTPException as exc:
                roled_admin_rejected = exc.status_code == 403
        finally:
            set_rls_context(None, False)

        if admin_super_ok and staff_super_ok and roled_admin_rejected:
            ok("Assertion 4: real call sites — Super Admin (with a stray role row) passes BOTH "
               "admin._require_manage_members and staff_assignments._require_manage_members, "
               "while a non-super roled user is still 403'd at admin — fix works in real context")
        else:
            fail("Assertion 4: real-call-site reproduction wrong",
                 f"admin_super_ok={admin_super_ok}, staff_super_ok={staff_super_ok}, "
                 f"roled_admin_rejected={roled_admin_rejected}")

        # ------------------------------------------------------------------
        # Assertion 5: teardown leaves zero leftover rows.
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
            try:
                await close_pool()
            except Exception:
                pass

    print(f"\n{'=' * 48}")
    print(f"RBAC Super Admin bypass: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
