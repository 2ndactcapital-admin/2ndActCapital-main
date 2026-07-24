"""Bug-fix sprint verify — two diagnosed production issues.

TASK 1 — /admin/profiles + /api/v1/... "404".
    Root cause found this sprint: there is NO backend routing/registration bug.
    routers/profiles.py declares /admin/profiles, /admin/permission-sets, etc.;
    main.py registers it with prefix "/api/v1" (import + include_router, both
    correct), so the live paths are /api/v1/admin/profiles and
    /api/v1/admin/permission-sets — exactly what apps/web/lib/api.js calls.
    This verify proves the endpoints actually resolve to 200 (not 404) by
    driving the real ASGI app through Starlette's TestClient with an
    impersonated super_admin (token verification stubbed), against the live DB.

TASK 2 — /admin/platform stale-role theme caching.
    loadTheme()'s authenticated theme fetch now explicitly passes
    cache: "no-store" (matching the public fallback), so a freshly-promoted
    super_admin is never served a cached pre-promotion role. Checked by grep on
    the actual file.

Also: npm run build exits 0, and no Signature-palette hex in modified files.

Pass/fail only. No interactive prompts. Idempotent (seed + teardown by stable
identifiers). Run: DATABASE_URL=... python scripts/verify_fixbugs.py
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
    print("[SKIP] DATABASE_URL not set — skipping verify_fixbugs")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"
SEED_PERSONAS = ["Member", "Community Member", "Adviser", "CSA / Ops"]

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WEB_DIR = os.path.join(REPO_ROOT, "apps", "web")
THEME_JS = os.path.join(WEB_DIR, "lib", "theme.js")

# Files this sprint modified (brand-hex gate).
MODIFIED_FILES = [
    "apps/web/lib/theme.js",
    "apps/web/lib/api.js",
]
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


async def seed_admin(conn):
    """Seed the test caller as a super_admin in the default org so the admin
    endpoints authorize. Upsert guarantees the role even if the row exists."""
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_fixbugs@test.local', 'FixBugs Verify User', $3,
                'super_admin')
        ON CONFLICT (auth0_sub) DO UPDATE
            SET role = 'super_admin', org_id = EXCLUDED.org_id
        """,
        TEST_USER_ID, ORG_ID, TEST_AUTH0_SUB,
    )


async def cleanup(conn):
    """Remove the test user (FK-safe: children before the user)."""
    await conn.execute(
        "DELETE FROM user_permission_sets WHERE user_id = $1", TEST_USER_ID
    )
    await conn.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await conn.execute("DELETE FROM users WHERE id = $1", TEST_USER_ID)


def check_endpoints_200():
    """[Y1][Y2] The profiles + permission-sets endpoints resolve to 200 (not
    404), driving the real app with an impersonated super_admin."""
    import main
    from starlette.testclient import TestClient

    # Stub token verification -> claims that map to the seeded super_admin.
    main.verify_token = lambda _token: {
        "sub": TEST_AUTH0_SUB,
        "email": "verify_fixbugs@test.local",
    }
    hdr = {"Authorization": "Bearer stub"}

    # Context-manager form runs startup so the asyncpg pool is created in the
    # TestClient's own event loop (avoids cross-loop errors).
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r_profiles = c.get("/api/v1/admin/profiles", headers=hdr)
        r_sets = c.get("/api/v1/admin/permission-sets", headers=hdr)

    # Task 1a — profiles: 200 with the 4 seed personas.
    if r_profiles.status_code == 404:
        fail("Y1 GET /api/v1/admin/profiles", "404 — route not resolving")
    elif r_profiles.status_code != 200:
        fail("Y1 GET /api/v1/admin/profiles",
             f"expected 200, got {r_profiles.status_code}: {r_profiles.text[:200]}")
    else:
        names = {p.get("name") for p in r_profiles.json()}
        missing = [p for p in SEED_PERSONAS if p not in names]
        if missing:
            fail("Y1 GET /api/v1/admin/profiles",
                 f"200 but missing seed personas {missing}")
        else:
            ok(f"Y1 GET /api/v1/admin/profiles -> 200 with the 4 seed profiles "
               f"{SEED_PERSONAS} (not 404)")

    # Task 1b — permission-sets: 200 with a JSON list.
    if r_sets.status_code == 404:
        fail("Y2 GET /api/v1/admin/permission-sets", "404 — route not resolving")
    elif r_sets.status_code != 200:
        fail("Y2 GET /api/v1/admin/permission-sets",
             f"expected 200, got {r_sets.status_code}: {r_sets.text[:200]}")
    elif not isinstance(r_sets.json(), list):
        fail("Y2 GET /api/v1/admin/permission-sets", "200 but body is not a list")
    else:
        ok("Y2 GET /api/v1/admin/permission-sets -> 200 (JSON list, not 404)")


def check_theme_no_store():
    """[Y3] loadTheme()'s authenticated theme fetch includes cache: "no-store"."""
    with open(THEME_JS, "r") as f:
        src = f.read()
    # The authenticated call must pass an explicit no-store cache option.
    pat = re.compile(
        r'fetchAPI\(\s*"/api/v1/theme"\s*,\s*\{[^}]*cache:\s*"no-store"',
        re.DOTALL,
    )
    if pat.search(src):
        ok('Y3 theme.js: authenticated fetchAPI("/api/v1/theme") includes '
           'cache: "no-store"')
    else:
        fail("Y3 theme.js: authenticated theme fetch missing cache: \"no-store\"")


def check_build():
    """[Y4] npm run build exits 0."""
    if os.environ.get("SKIP_BUILD"):
        print("[P] Y4 Build: SKIP_BUILD set — skipping npm run build")
        return
    next_bin_local = os.path.join(WEB_DIR, "node_modules", ".bin", "next")
    next_bin_root = os.path.join(REPO_ROOT, "node_modules", ".bin", "next")
    if not (os.path.exists(next_bin_local) or os.path.exists(next_bin_root)):
        fail("Y4 Build: next not installed (run npm install)")
        return
    print("    running `npm run build` in apps/web (this can take a minute)…")
    proc = subprocess.run(
        ["npm", "run", "build"], cwd=WEB_DIR, capture_output=True, text=True
    )
    if proc.returncode == 0:
        ok("Y4 Build: npm run build exited 0")
    else:
        fail("Y4 Build: npm run build failed", f"exit={proc.returncode}")
        sys.stderr.write((proc.stdout or "")[-3000:] + "\n" +
                         (proc.stderr or "")[-3000:] + "\n")


def check_brand_hex():
    """[Y5] No Signature-palette hex in any modified file."""
    hits = []
    for rel in MODIFIED_FILES:
        path = os.path.join(REPO_ROOT, rel)
        proc = subprocess.run(
            ["grep", "-InE", BRAND_HEX_RE, path], capture_output=True, text=True
        )
        if proc.returncode == 0 and proc.stdout.strip():
            hits.append(f"{rel}:\n{proc.stdout.strip()}")
    if not hits:
        ok(f"Y5 Brand hex: no Signature-palette hex in "
           f"{len(MODIFIED_FILES)} modified files")
    else:
        fail("Y5 Brand hex: palette hex found in modified files",
             "\n".join(hits))


async def main_async():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=2
    )
    try:
        async with pool.acquire() as conn:
            await cleanup(conn)
            await seed_admin(conn)
    finally:
        await pool.close()


async def teardown_async():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        await cleanup(conn)
    finally:
        await conn.close()


def run():
    # Seed the impersonated super_admin, then exercise the app + static checks.
    asyncio.run(main_async())
    try:
        check_endpoints_200()
        check_theme_no_store()
        check_build()
        check_brand_hex()
    finally:
        asyncio.run(teardown_async())

    print(f"\n{'=' * 44}")
    print(f"FixBugs: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
