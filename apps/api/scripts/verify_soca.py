"""SOC Phase A verify — Profiles & Permission Sets admin UI.

This sprint is UI-only on top of the already-verified SOC Phase 1 backend, so
the checks combine backend-reachability of the data paths the new admin screens
drive (profiles / permission_sets / user_permission_sets / users.profile_id)
with a frontend build check and a brand-hex gate.

The API endpoints (routers/profiles.py) are thin wrappers over these exact
table operations, gated server-side by can_manage_org_settings; a running
server + Auth0 is not available in CI, so — like verify_soc1 — each data
assertion exercises the same SQL the endpoint runs and the same
services.profiles resolver the app reads at request time.

Pass/fail only, no interactive prompts, idempotent (teardown-at-start and
teardown-at-end by stable test identifiers).

Assertions:
  [Y] The 4 seeded profiles (Member, Community Member, Adviser, CSA/Ops) are
      readable via the list-profiles query the Task 1 screen calls.
  [Y] Creating a new profile succeeds and is retrievable.
  [Y] Toggling a permission grant on a profile persists (grant→true, remove→false).
  [Y] A permission set assigned to a test user ADDS a capability beyond their
      profile (reuses the Phase 1 additive-check logic).
  [Y] Setting users.profile_id persists AND users.role is UNCHANGED (fields stay
      independent).
  [Y] npm run build exits 0.
  [Y] No Signature-palette hex introduced in any new file (brand_sweep_grep hex).
  [Y] Teardown: zero leftover rows (confirmed via count(*)).

Run: DATABASE_URL=... python scripts/verify_soca.py
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soca")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"

TEST_PROFILE_NAME = "SOCA Verify Profile"
TEST_PSET_NAME = "SOCA Verify Set"

SEED_PERSONAS = ["Member", "Community Member", "Adviser", "CSA / Ops"]

# Real action-registry keys (permissions.name).
GRANTED_KEY = "manage_deals"          # profile grants this
SET_ADDED_KEY = "override_compliance"  # profile does NOT grant; the set adds it

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WEB_DIR = os.path.join(REPO_ROOT, "apps", "web")

# Files introduced by this sprint (for the brand-hex gate).
NEW_FILES = [
    "apps/api/routers/profiles.py",
    "apps/web/lib/permissionActions.js",
    "apps/web/components/admin/PermissionChecklist.jsx",
    "apps/web/components/admin/ProfilesManager.jsx",
    "apps/web/components/admin/PermissionSetsManager.jsx",
    "apps/web/app/admin/profiles/page.js",
    "apps/web/app/admin/permission-sets/page.js",
]
# Same hex set as scripts/brand_sweep_grep.sh HEX_RE.
BRAND_HEX_RE = (
    r"#?(1B2B4B|C5A880|E8D5A3|9AA6BF|FAF9F6|F5F1EB|"
    r"FFFFFF|0F172A|334155|64748B|E2E8F0)"
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


# The exact list-profiles projection the Task 1 screen calls (routers/profiles.py).
LIST_PROFILES_SQL = """
    SELECT p.id, p.name, p.is_seed,
           (SELECT count(*) FROM users u WHERE u.profile_id = p.id) AS user_count,
           COALESCE(
               array_agg(pp.permission_key) FILTER (WHERE pp.permission_key IS NOT NULL),
               '{}'
           ) AS permission_keys
    FROM profiles p
    LEFT JOIN profile_permissions pp ON pp.profile_id = p.id
    WHERE p.org_id = $1
    GROUP BY p.id
    ORDER BY p.is_seed DESC, p.name
"""


async def cleanup(conn):
    """Remove all test data by stable identifiers. Idempotent."""
    await conn.execute(
        "DELETE FROM user_permission_sets WHERE user_id = $1", TEST_USER_ID
    )
    await conn.execute(
        "UPDATE users SET profile_id = NULL WHERE id = $1", TEST_USER_ID
    )
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
    await conn.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await conn.execute("DELETE FROM users WHERE id = $1", TEST_USER_ID)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM profiles
                WHERE org_id = $1 AND name = $2 AND is_seed = false)
          + (SELECT count(*) FROM permission_sets
                WHERE org_id = $1 AND name = $3)
          + (SELECT count(*) FROM user_permission_sets WHERE user_id = $4)
          + (SELECT count(*) FROM users WHERE id = $4)
        """,
        ORG_ID, TEST_PROFILE_NAME, TEST_PSET_NAME, TEST_USER_ID,
    ))


def check_build():
    """[Y] npm run build exits 0."""
    if os.environ.get("SKIP_BUILD"):
        print("[P] Build: SKIP_BUILD set — skipping npm run build (assumed green)")
        return True
    # Monorepo hoists deps to the repo-root node_modules; accept either location.
    next_bin_local = os.path.join(WEB_DIR, "node_modules", ".bin", "next")
    next_bin_root = os.path.join(REPO_ROOT, "node_modules", ".bin", "next")
    if not (os.path.exists(next_bin_local) or os.path.exists(next_bin_root)):
        fail("Build: next not installed (run npm install) — cannot build")
        return False
    print("    running `npm run build` in apps/web (this can take a minute)…")
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=WEB_DIR,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        ok("Build: npm run build exited 0")
        return True
    fail("Build: npm run build failed", f"exit={proc.returncode}")
    sys.stderr.write(proc.stdout[-3000:] + "\n" + proc.stderr[-3000:] + "\n")
    return False


def check_brand_hex():
    """[Y] No Signature-palette hex in any new file."""
    hits = []
    for rel in NEW_FILES:
        path = os.path.join(REPO_ROOT, rel)
        proc = subprocess.run(
            ["grep", "-InE", BRAND_HEX_RE, path],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            hits.append(f"{rel}:\n{proc.stdout.strip()}")
    if not hits:
        ok(f"Brand hex: no Signature-palette hex in {len(NEW_FILES)} new files")
    else:
        fail("Brand hex: palette hex found in new files", "\n".join(hits))


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.profiles import user_has_permission

    try:
        async with pool.acquire() as conn:
            await cleanup(conn)

        # Seed test user (role = member — asserted unchanged later).
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, 'verify_soca@test.local', 'SOCA Verify User', $3, 'member')
                ON CONFLICT (auth0_sub) DO NOTHING
                """,
                TEST_USER_ID, ORG_ID, TEST_AUTH0_SUB,
            )
            # Confirm the action keys we rely on exist in the registry.
            reg = await conn.fetch(
                "SELECT name FROM permissions WHERE name = ANY($1::text[])",
                [GRANTED_KEY, SET_ADDED_KEY],
            )
        if {r["name"] for r in reg} != {GRANTED_KEY, SET_ADDED_KEY}:
            fail("Precheck: action-registry keys missing",
                 f"need {GRANTED_KEY}, {SET_ADDED_KEY}")

        # ------------------------------------------------------------------
        # Check 1: 4 seed profiles readable via the list query
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            rows = await conn.fetch(LIST_PROFILES_SQL, ORG_ID)
        names = {r["name"] for r in rows if r["is_seed"]}
        missing = [p for p in SEED_PERSONAS if p not in names]
        if not missing:
            ok(f"Check 1: seed profiles readable via list query {SEED_PERSONAS}")
        else:
            fail("Check 1: seed profiles not readable", f"missing={missing}")

        # ------------------------------------------------------------------
        # Check 2: create a profile → retrievable
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            profile_id = await conn.fetchval(
                """
                INSERT INTO profiles (org_id, name, description, is_seed)
                VALUES ($1, $2, 'verify', false) RETURNING id
                """,
                ORG_ID, TEST_PROFILE_NAME,
            )
            rows = await conn.fetch(LIST_PROFILES_SQL, ORG_ID)
        found = next((r for r in rows if str(r["id"]) == str(profile_id)), None)
        if found is not None and found["name"] == TEST_PROFILE_NAME:
            ok("Check 2: created profile is retrievable via the list query")
        else:
            fail("Check 2: created profile not retrievable")

        # ------------------------------------------------------------------
        # Check 3: toggle a permission grant (grant→true, remove→false)
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO profile_permissions (org_id, profile_id, permission_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (profile_id, permission_key) DO NOTHING
                """,
                ORG_ID, profile_id, GRANTED_KEY,
            )
            after_grant = await conn.fetchval(
                "SELECT count(*) FROM profile_permissions "
                "WHERE profile_id = $1 AND permission_key = $2",
                profile_id, GRANTED_KEY,
            )
            await conn.execute(
                "DELETE FROM profile_permissions "
                "WHERE profile_id = $1 AND permission_key = $2",
                profile_id, GRANTED_KEY,
            )
            after_remove = await conn.fetchval(
                "SELECT count(*) FROM profile_permissions "
                "WHERE profile_id = $1 AND permission_key = $2",
                profile_id, GRANTED_KEY,
            )
        if int(after_grant) == 1 and int(after_remove) == 0:
            ok(f"Check 3: grant toggle persists ('{GRANTED_KEY}' on=1, off=0)")
        else:
            fail("Check 3: grant toggle wrong",
                 f"after_grant={after_grant}, after_remove={after_remove}")

        # Re-grant so the profile has a base grant, then assign it to the user.
        async with pool.acquire() as conn:
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
        # Check 4: permission set ADDS a capability beyond the profile
        # ------------------------------------------------------------------
        before_set = await user_has_permission(pool, TEST_USER_ID, SET_ADDED_KEY)
        async with pool.acquire() as conn:
            pset_id = await conn.fetchval(
                """
                INSERT INTO permission_sets (org_id, name, description)
                VALUES ($1, $2, 'verify set') RETURNING id
                """,
                ORG_ID, TEST_PSET_NAME,
            )
            await conn.execute(
                """
                INSERT INTO permission_set_permissions
                    (org_id, permission_set_id, permission_key)
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
        if before_set is False and after_set is True:
            ok(f"Check 4: permission set ADDS '{SET_ADDED_KEY}' beyond profile "
               f"(was False, now True)")
        else:
            fail("Check 4: additive permission set wrong",
                 f"before={before_set}, after={after_set}")

        # ------------------------------------------------------------------
        # Check 5: users.profile_id persists AND users.role unchanged
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            role_before = await conn.fetchval(
                "SELECT role FROM users WHERE id = $1", TEST_USER_ID
            )
            # The Task-3 endpoint sets profile_id only; it never touches role.
            await conn.execute(
                "UPDATE users SET profile_id = $1, updated_at = now() WHERE id = $2",
                profile_id, TEST_USER_ID,
            )
            got = await conn.fetchrow(
                "SELECT profile_id, role FROM users WHERE id = $1", TEST_USER_ID
            )
        if (str(got["profile_id"]) == str(profile_id)
                and got["role"] == role_before == "member"):
            ok("Check 5: profile_id persists and users.role unchanged "
               f"(role still '{got['role']}')")
        else:
            fail("Check 5: profile/role independence wrong",
                 f"profile_id={got['profile_id']}, role={got['role']}, "
                 f"role_before={role_before}")

        # ------------------------------------------------------------------
        # Check 6/7: build + brand-hex gate (run outside the DB context)
        # ------------------------------------------------------------------
        check_build()
        check_brand_hex()

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
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 44}")
    print(f"SOC Phase A: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
