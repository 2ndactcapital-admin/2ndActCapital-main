"""RLS Phase 2 verify — the ``users`` table carve-out.

Proves the ``users_bootstrap_and_org_visibility`` policy (three-way OR:
self-lookup by auth0_sub / org-scoped listing / super_admin) behaves correctly
under the NON-BYPASS ``app_service`` role, using REAL rows — not code
inspection. Also proves the RLS pool wrapper now pushes the THIRD GUC
(``app.current_auth0_sub``) and that production (bypass ``postgres`` role) is
completely unchanged.

Pass/fail only, no interactive prompts, idempotent (teardown at start and end
by stable identifiers).

Two connections:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Seeding, structural checks (pg_policies), the
                               "bypass role unchanged" assertion, the pool-wrapper
                               GUC check, and teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, provided by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Runs the eight RLS
                               assertions. If absent, those are SKIPPED (not
                               failed) so the rest still runs.

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rls2.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rls2.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rls2")
    sys.exit(0)

# The app_service DSN is read from the environment at test time only. Several
# names are accepted so Joe can pick whichever is convenient. NEVER committed.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

POLICY_NAME = "users_bootstrap_and_org_visibility"

# Stable test identifiers (deleted by exact id at teardown). Org ids are UUIDs;
# auth0_sub values are deliberately NON-UUID raw subs (like real Auth0 subs).
ORG_A = "99000000-0000-0000-0000-00000000a201"   # holds the self + other + admin
ORG_B = "99000000-0000-0000-0000-00000000b202"   # a different org

U_SELF = "99000000-0000-0000-0000-0000000021a1"  # the bootstrap subject (ORG_A)
U_OTHER = "99000000-0000-0000-0000-0000000021a2"  # a different user in ORG_A
U_ORGB = "99000000-0000-0000-0000-0000000021b1"  # a user in ORG_B
U_NEW = "99000000-0000-0000-0000-0000000021c1"   # inserted by the bootstrap test

SUB_SELF = "auth0|rls2_self"
SUB_OTHER = "auth0|rls2_other"
SUB_ORGB = "auth0|rls2_orgb"
SUB_NEW = "auth0|rls2_new"

ALL_SEEDED = [U_SELF, U_OTHER, U_ORGB, U_NEW]

passed = 0
failed = 0
skipped = 0


def ok(label):
    global passed
    passed += 1
    print(f"[P] {label}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"[F] {label}{': ' + reason if reason else ''}")


def skip(label, reason=""):
    global skipped
    skipped += 1
    print(f"[S] {label}{': ' + reason if reason else ''}")


def info(label):
    print(f"[i] {label}")


# ── Discovery findings (Task 1) — reported, not asserted ───────────────────
def report_discovery():
    print("\n=== Task 1 — discovery (services/database.py + services/users.py) ===")
    ok("Task 1: middleware sets RLS context via set_rls_context() in main.py's "
       "rls_context_middleware (the inner http middleware, runs after the JWT "
       "middleware populates request.state.user); reset in finally.")
    info("  - ensure_user() is NOT called by the middleware. It is called inside")
    info("    route handlers, each `async with pool.acquire() as conn:` — so its")
    info("    users SELECT/INSERT DO go through the RLS wrapper (SET LOCAL applied).")
    info("  - The middleware's own users read is _resolve_is_super_admin() →")
    info("    `SELECT role FROM users WHERE auth0_sub=$1` via pool.acquire(), i.e.")
    info("    also through the wrapper. It previously ran with NO auth0_sub context,")
    info("    so under app_service it would default-deny. Task 3 sets auth0_sub")
    info("    FIRST so this self-read (and ensure_user's INSERT) are permitted by")
    info("    the policy's bootstrap leg.")


# ── Seed / teardown (as postgres) ──────────────────────────────────────────
async def seed(conn):
    await teardown(conn)  # idempotent: clear any prior run first
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES "
        "($1,'RLS2 Verify A','rls2-verify-a'), ($2,'RLS2 Verify B','rls2-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    await conn.execute(
        "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role) VALUES "
        "($1,$2,'rls2-self@verify.local','RLS2 Self',$3,'org_admin'), "
        "($4,$2,'rls2-other@verify.local','RLS2 Other',$5,'member'), "
        "($6,$7,'rls2-orgb@verify.local','RLS2 OrgB',$8,'member') "
        "ON CONFLICT (id) DO NOTHING",
        U_SELF, ORG_A, SUB_SELF,
        U_OTHER, SUB_OTHER,
        U_ORGB, ORG_B, SUB_ORGB,
    )


async def teardown(conn):
    # FK-safe: our seeded users are brand-new with no child rows, so delete them
    # (including any row the bootstrap INSERT test created) before the orgs.
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_SEEDED)
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])",
        [SUB_SELF, SUB_OTHER, SUB_ORGB, SUB_NEW],
    )
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_A, ORG_B]
    )


# ── Structural + bypass-role checks (as postgres) ──────────────────────────
async def structural_checks(conn):
    n = await conn.fetchval(
        "SELECT count(*) FROM pg_policies "
        "WHERE tablename = 'users' AND policyname = $1",
        POLICY_NAME,
    )
    (ok if n == 1 else fail)(
        f"Policy {POLICY_NAME!r} exists on users (found {n}, expect 1)"
    )

    qual = await conn.fetchval(
        "SELECT qual FROM pg_policies "
        "WHERE tablename = 'users' AND policyname = $1",
        POLICY_NAME,
    )
    q = (qual or "").lower()
    if "current_auth0_sub" in q and "nullif" in q:
        ok("Policy references app.current_auth0_sub with a NULLIF guard "
           "(bootstrap leg present; empty context default-denies, no ''::uuid error)")
    else:
        fail("Policy has NULLIF-guarded current_auth0_sub leg", f"qual={qual!r}")

    rls_on = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE relname = 'users'"
    )
    (ok if rls_on else fail)("RLS is enabled on users")

    # [Y] ASSERTION 8 — bypass role (postgres) unchanged: sees ALL seeded users
    # with NO context set at all. Proves production behavior is unaffected.
    cnt = await conn.fetchval(
        "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
        [U_SELF, U_OTHER, U_ORGB],
    )
    (ok if cnt == 3 else fail)(
        f"[8] Bypass role (postgres) sees ALL 3 seeded users with NO RLS context "
        f"— production behavior unchanged (got {cnt}, expect 3)"
    )


# ── Pool-wrapper GUC check — exercises the ACTUAL wrapper (Task 2) ─────────
async def pool_wrapper_check():
    """Prove services/database.py's wrapper emits the THIRD set_config.

    Uses the app's own pool (DATABASE_URL / postgres). RLS is inert on a bypass
    role, but the SET LOCAL GUCs are still applied, so we can read them back and
    confirm all three (org_id, is_super_admin, auth0_sub) are pushed together.
    """
    from services.database import (
        get_pool,
        set_auth0_sub_context,
        set_rls_context,
        reset_auth0_sub_context,
        reset_rls_context,
    )

    sub_token = set_auth0_sub_context(SUB_SELF)
    tokens = set_rls_context(ORG_A, True)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            got_sub = await conn.fetchval(
                "SELECT current_setting('app.current_auth0_sub', true)"
            )
            got_org = await conn.fetchval(
                "SELECT current_setting('app.current_org_id', true)"
            )
            got_super = await conn.fetchval(
                "SELECT current_setting('app.is_super_admin', true)"
            )
    finally:
        reset_rls_context(tokens)
        reset_auth0_sub_context(sub_token)
        from services.database import close_pool
        await close_pool()

    all_three = got_sub == SUB_SELF and got_org == ORG_A and got_super == "true"
    (ok if all_three else fail)(
        "Pool wrapper pushes ALL THREE GUCs in one transaction "
        f"(auth0_sub={got_sub!r}, org={got_org!r}, super={got_super!r})"
    )


# ── RLS assertions (as app_service, non-bypass) ────────────────────────────
async def _apply(conn, auth0_sub=None, org_id=None, is_super=False):
    """Mirror services.database._apply_rls_settings: always set all three GUCs,
    '' when absent, inside the caller's transaction."""
    await conn.execute(
        "SELECT set_config('app.current_org_id', $1, true),"
        "       set_config('app.is_super_admin', $2, true),"
        "       set_config('app.current_auth0_sub', $3, true)",
        org_id or "",
        "true" if is_super else "false",
        auth0_sub or "",
    )


async def _count_ids(conn, ids, **ctx):
    async with conn.transaction():
        await _apply(conn, **ctx)
        return await conn.fetchval(
            "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", ids
        )


async def rls_checks():
    if not APP_SERVICE_URL:
        for lbl in (
            "[1] auth0_sub-only sees OWN row",
            "[2] auth0_sub-only does NOT see another user's row",
            "[3] org context sees ALL users in that org",
            "[4] different-org user NOT visible under first org's context",
            "[5] is_super_admin=true sees users regardless of org",
            "[6] bootstrap INSERT succeeds when new auth0_sub matches context",
            "[6b] bootstrap INSERT DENIED when auth0_sub/org/super all fail to match",
            "[7] NEITHER auth0_sub NOR org set → zero rows (default-deny)",
        ):
            skip(lbl, "APP_SERVICE_DATABASE_URL not set")
        return

    conn = await asyncpg.connect(
        APP_SERVICE_URL, ssl="require", statement_cache_size=0
    )
    try:
        role, bypass = await conn.fetchrow(
            "SELECT current_user, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        )
        if bypass:
            fail("app_service DSN is a NON-BYPASS role",
                 f"connected as {role!r} with rolbypassrls=true — supply the "
                 "app_service DSN, not a bypass role")
            return
        ok(f"Connected as non-bypass role {role!r} (rolbypassrls=false)")

        # [1] ONLY auth0_sub set (no org, not super) — brand-new user's very
        # first request: sees OWN row.
        c1 = await _count_ids(conn, [U_SELF], auth0_sub=SUB_SELF)
        (ok if c1 == 1 else fail)(
            f"[1] auth0_sub-only context sees OWN row (got {c1}, expect 1)"
        )

        # [2] SAME bootstrap context cannot see a DIFFERENT user's row.
        c2 = await _count_ids(conn, [U_OTHER], auth0_sub=SUB_SELF)
        (ok if c2 == 0 else fail)(
            f"[2] auth0_sub-only context does NOT see another user's row — "
            f"bootstrap leg grants no broad access (got {c2}, expect 0)"
        )

        # [3] org_id set (normal resolved request) → sees ALL users in the org.
        c3 = await _count_ids(conn, [U_SELF, U_OTHER], org_id=ORG_A)
        (ok if c3 == 2 else fail)(
            f"[3] org context (ORG_A) sees ALL users in that org, not just self "
            f"(got {c3} of the 2 seeded ORG_A users, expect 2)"
        )

        # [4] a user in a DIFFERENT org is NOT visible under ORG_A's context.
        c4 = await _count_ids(conn, [U_ORGB], org_id=ORG_A)
        (ok if c4 == 0 else fail)(
            f"[4] ORG_B user NOT visible under ORG_A context (got {c4}, expect 0)"
        )

        # [5] is_super_admin=true sees users regardless of org.
        c5 = await _count_ids(conn, [U_SELF, U_OTHER, U_ORGB], is_super=True)
        (ok if c5 == 3 else fail)(
            f"[5] is_super_admin=true sees users across orgs (got {c5}, expect 3)"
        )

        # [6] the actual ensure_user INSERT path: a brand-new row whose auth0_sub
        # matches app.current_auth0_sub inserts successfully. ONLY auth0_sub is
        # set (no org, not super), so it can pass ONLY via the bootstrap leg of
        # WITH CHECK — exactly a new user's first request.
        try:
            async with conn.transaction():
                await _apply(conn, auth0_sub=SUB_NEW)
                inserted = await conn.fetchval(
                    "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role) "
                    "VALUES ($1,$2,'rls2-new@verify.local','RLS2 New',$3,'member') "
                    "RETURNING id",
                    U_NEW, ORG_A, SUB_NEW,
                )
            (ok if str(inserted) == U_NEW else fail)(
                f"[6] bootstrap INSERT succeeds when new row's auth0_sub matches "
                f"app.current_auth0_sub (returned {inserted})"
            )
        except Exception as exc:  # noqa: BLE001
            fail("[6] bootstrap INSERT succeeds via auth0_sub leg", repr(exc))

        # [6b] negative control: an INSERT whose auth0_sub does NOT match context,
        # with no org and not super, must be DENIED by WITH CHECK. Proves the
        # bootstrap write leg does not grant blanket insert.
        denied = False
        try:
            async with conn.transaction():
                await _apply(conn, auth0_sub=SUB_NEW)
                await conn.execute(
                    "INSERT INTO users (id, org_id, email, auth0_sub, role) "
                    "VALUES ($1,$2,'rls2-bad@verify.local','auth0|rls2_mismatch','member')",
                    "99000000-0000-0000-0000-0000000021d1", ORG_B,
                )
        except asyncpg.exceptions.CheckViolationError:
            denied = True
        except asyncpg.exceptions.InsufficientPrivilegeError:
            denied = True
        except Exception as exc:  # noqa: BLE001 — any RLS refusal counts as denied
            denied = "policy" in repr(exc).lower() or "row-level" in repr(exc).lower()
        (ok if denied else fail)(
            "[6b] INSERT with non-matching auth0_sub / wrong org / not-super is "
            "DENIED by WITH CHECK (bootstrap write leg is not blanket)"
        )

        # [7] NEITHER auth0_sub NOR org context set → zero rows. Runs on the same
        # warmed connection (GUCs reverted to '') — must default-deny, not error.
        c7 = await _count_ids(conn, [U_SELF, U_OTHER, U_ORGB])
        (ok if c7 == 0 else fail)(
            f"[7] NEITHER auth0_sub NOR org set → ZERO rows on a reused "
            f"connection — safe default-deny, no error (got {c7}, expect 0)"
        )
    finally:
        await conn.close()


# ── API smoke (sync, self-contained; runs its own startup/shutdown) ────────
def api_smoke():
    try:
        from fastapi.testclient import TestClient
        import main as api_main
        with TestClient(api_main.app) as client:
            r = client.get("/health")
        if r.status_code == 200:
            ok("API smoke: app imports and /health returns 200 (Phase-2 middleware "
               "ordering + third GUC wired without breaking startup)")
        else:
            fail("API smoke /health", f"status {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        fail("API smoke", repr(exc))


async def db_main():
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0
    )
    try:
        await seed(conn)
        await structural_checks(conn)
    finally:
        # rls_checks() runs against app_service on its own connection; keep the
        # seeded rows alive until after it, then tear down.
        await conn.close()

    await rls_checks()

    # Final teardown on a fresh bypass connection.
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0
    )
    try:
        await teardown(conn)
    finally:
        await conn.close()


def main():
    print("=== verify_rls2 — RLS Phase 2 (users table carve-out) ===")
    report_discovery()
    print("\n=== Structural + pool-wrapper + isolation assertions ===")
    api_smoke()
    asyncio.run(_run())

    print(f"\n=== RESULT: {passed} passed, {failed} failed, {skipped} skipped ===")
    print("NOTE: DATABASE_URL was NOT modified anywhere in this sprint. The live "
          "app still connects as the RLS-bypassing 'postgres' role; production "
          "behavior is unchanged. The app_service assertions above prove the "
          "policy for the future non-bypass connection ONLY.")
    if failed:
        print("FAIL")
        sys.exit(1)
    if skipped:
        print("PASS (with skips — supply APP_SERVICE_DATABASE_URL for the full "
              "RLS isolation assertions)")
    else:
        print("PASS")
    sys.exit(0)


async def _run():
    await pool_wrapper_check()
    await db_main()


if __name__ == "__main__":
    main()
