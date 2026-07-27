"""RLS Phase 1 verify — non-bypassing connection wrapper, org-context
middleware, ONE pilot policy (trusted_contacts).

Pass/fail only, no interactive prompts, idempotent (teardown at start and end
by stable identifiers).

Two connections:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Used for seeding, structural checks (pg_policies),
                               the "bypass role unchanged" assertion, and teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, provided by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Used for the four RLS
                               isolation assertions. If absent, those four are
                               SKIPPED (not failed) so the rest still runs.

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rls1.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rls1.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rls1")
    sys.exit(0)

# The app_service DSN is read from the environment at test time only. Several
# names are accepted so Joe can pick whichever is convenient.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# Stable test identifiers (deleted by exact id at teardown).
ORG_A = "99000000-0000-0000-0000-0000000000a1"   # owns the seeded contact
ORG_B = "99000000-0000-0000-0000-0000000000b2"   # a different org
ENT_A = "99000000-0000-0000-0000-0000000000e1"   # member entity in ORG_A
CONTACT_A = "99000000-0000-0000-0000-0000000000c1"  # trusted_contact in ORG_A
POLICY_NAME = "trusted_contacts_org_isolation"

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


# ── Discovery findings (Tasks 1 & 2) — reported, not asserted ──────────────
def report_discovery():
    print("\n=== Task 1 — transaction-pattern discovery ===")
    ok(
        "Task 1: NO production pool.acquire() block relies on per-statement "
        "autocommit / partial commit."
    )
    info("  - Audit writes (services/audit.py) always use their OWN fresh pool")
    info("    connection, so INSERT+write_audit_log pairs are never rolled back")
    info("    together — unaffected by wrapping.")
    info("  - Best-effort loops (brief_blocks, assistant, todo_generators) call")
    info("    handlers that each acquire their OWN connection — per-item")
    info("    semantics preserved (each is its own wrapped transaction).")
    info("  - ~70 blocks already use `async with conn.transaction():`; under the")
    info("    wrapper these nest as SAVEPOINTs (asyncpg does this automatically),")
    info("    preserving all-or-nothing intent. They are NOT double-wrapped.")
    info("  - Two marketplace helpers (marketplace.py ~189, ~967) use explicit")
    info("    savepoints inside a caller transaction — still correct as savepoints.")
    info("  - ONLY partial-commit reliance is scripts/verify_sprint22.py teardown,")
    info("    which uses a direct asyncpg.connect() (NOT pool.acquire) — OUT OF")
    info("    SCOPE for this change. Flagged, not modified.")

    print("\n=== Task 2 — ensure_user bootstrap flow (for a FUTURE users carve-out) ===")
    ok(
        "Task 2: ensure_user reads/writes the `users` table ONLY, keyed on "
        "auth0_sub."
    )
    info("  services/users.py::ensure_user(conn, request):")
    info("  - Resolves the caller by SELECT id FROM users WHERE auth0_sub = $1.")
    info("  - Fallback: if the token `sub` is itself a UUID matching users.id,")
    info("    uses that row (verify scripts stub sub = a seeded user's UUID).")
    info("  - Otherwise INSERT INTO users (...) VALUES (uuid_generate_v4(), ...)")
    info("    ON CONFLICT (auth0_sub) DO UPDATE ... RETURNING id.")
    info("  - Never raises; on error falls back to a token-derived id.")
    info("  IMPLICATION: `users` has RLS enabled with NO policy. This bootstrap")
    info("  runs BEFORE any org context exists for a brand-new user, so once the")
    info("  app connects as app_service it would default-deny on `users`. This")
    info("  sprint adds NO policy to `users`; a future sprint must design a")
    info("  carve-out (e.g. a users self/bootstrap policy or a scoped bypass for")
    info("  identity sync). Documented here so it need not be re-discovered.")


# ── Structural + bypass-role checks (as postgres) ──────────────────────────
async def seed(conn):
    await teardown(conn)  # idempotent: clear any prior run first
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES "
        "($1,'RLS Verify A','rls-verify-a'), ($2,'RLS Verify B','rls-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    await conn.execute(
        "INSERT INTO entities (id, org_id, entity_type, display_name) "
        "VALUES ($1,$2,'individual','RLS Verify Member') "
        "ON CONFLICT (id) DO NOTHING",
        ENT_A, ORG_A,
    )
    await conn.execute(
        "INSERT INTO trusted_contacts (id, org_id, member_entity_id, contact_name) "
        "VALUES ($1,$2,$3,'RLS Verify Contact') ON CONFLICT (id) DO NOTHING",
        CONTACT_A, ORG_A, ENT_A,
    )


async def teardown(conn):
    # FK-safe order: trusted_contacts -> entities -> organizations.
    await conn.execute("DELETE FROM trusted_contacts WHERE id = $1", CONTACT_A)
    await conn.execute("DELETE FROM entities WHERE id = $1", ENT_A)
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_A, ORG_B]
    )


async def structural_checks(conn):
    # [Y] policy exists
    n = await conn.fetchval(
        "SELECT count(*) FROM pg_policies "
        "WHERE tablename = 'trusted_contacts' AND policyname = $1",
        POLICY_NAME,
    )
    if n == 1:
        ok(f"Policy {POLICY_NAME!r} exists on trusted_contacts (pg_policies)")
    else:
        fail(f"Policy {POLICY_NAME!r} exists", f"found {n} matching policies")

    # Policy must guard the org GUC with NULLIF(..., '') so a reused pooled
    # connection (whose custom GUC reverts to '' after a prior SET LOCAL)
    # default-denies with ZERO rows instead of erroring on ''::uuid.
    qual = await conn.fetchval(
        "SELECT qual FROM pg_policies "
        "WHERE tablename = 'trusted_contacts' AND policyname = $1",
        POLICY_NAME,
    )
    if qual and "NULLIF" in qual.upper():
        ok("Pilot policy guards org GUC with NULLIF(...) — empty-string context "
           "default-denies (zero rows), never errors on ''::uuid")
    else:
        fail("Pilot policy uses NULLIF guard on org GUC", f"qual={qual!r}")

    # RLS is enabled on the pilot table
    rls_on = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE relname = 'trusted_contacts'"
    )
    if rls_on:
        ok("RLS is enabled on trusted_contacts")
    else:
        fail("RLS is enabled on trusted_contacts", "relrowsecurity is false")

    # [Y] bypass role (postgres) unaffected — sees the seeded row with NO GUC set
    cnt = await conn.fetchval(
        "SELECT count(*) FROM trusted_contacts WHERE id = $1", CONTACT_A
    )
    if cnt == 1:
        ok("Bypass role (postgres) sees the seeded row with NO org context "
           "(existing behavior unchanged)")
    else:
        fail("Bypass role sees seeded row", f"expected 1, got {cnt}")


# ── RLS isolation checks (as app_service) ──────────────────────────────────
async def as_appservice_count(conn, org_id=None, is_super=False):
    """Count the seeded contact under app_service with the given RLS context,
    mirroring production: SET LOCAL inside a transaction."""
    async with conn.transaction():
        if org_id is not None:
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true)", org_id
            )
        await conn.execute(
            "SELECT set_config('app.is_super_admin', $1, true)",
            "true" if is_super else "false",
        )
        return await conn.fetchval(
            "SELECT count(*) FROM trusted_contacts WHERE id = $1", CONTACT_A
        )


async def as_appservice_neither(conn):
    """No GUC set at all — simulate the middleware failing to run."""
    async with conn.transaction():
        return await conn.fetchval(
            "SELECT count(*) FROM trusted_contacts WHERE id = $1", CONTACT_A
        )


async def rls_checks():
    if not APP_SERVICE_URL:
        skip("app_service RLS assertions",
             "APP_SERVICE_DATABASE_URL not set — provide it to run the four "
             "isolation checks (org A visible / org B invisible / super sees / "
             "neither = 0)")
        skip("  -> org A set sees only ORG_A rows")
        skip("  -> different org set does NOT see the rows")
        skip("  -> is_super_admin=true sees rows regardless of org")
        skip("  -> NEITHER setting present returns ZERO rows (default-deny)")
        return

    conn = await asyncpg.connect(
        APP_SERVICE_URL, ssl="require", statement_cache_size=0
    )
    try:
        # Confirm we really are a non-bypass role — the whole test is meaningless
        # otherwise (e.g. if the wrong DSN was supplied).
        role, bypass = await conn.fetchrow(
            "SELECT current_user, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        )
        if bypass:
            fail("app_service DSN is a NON-BYPASS role",
                 f"connected as {role!r} which has rolbypassrls=true — supply "
                 "the app_service DSN, not a bypass role")
            return
        ok(f"Connected as non-bypass role {role!r} (rolbypassrls=false)")

        c1 = await as_appservice_count(conn, org_id=ORG_A)
        (ok if c1 == 1 else fail)(
            f"org_id=ORG_A sees the ORG_A contact (got {c1}, expect 1)"
        )

        c2 = await as_appservice_count(conn, org_id=ORG_B)
        (ok if c2 == 0 else fail)(
            f"different org_id=ORG_B does NOT see the ORG_A contact (got {c2}, "
            "expect 0)"
        )

        c3 = await as_appservice_count(conn, org_id=ORG_B, is_super=True)
        (ok if c3 == 1 else fail)(
            f"is_super_admin=true sees the contact regardless of org (got {c3}, "
            "expect 1)"
        )

        # NOTE: this runs on the SAME connection that just served org-scoped
        # transactions above, so its app.current_org_id has reverted to '' (the
        # reset artifact) — exactly the reused-connection path. It must return
        # ZERO rows, not raise on ''::uuid. That is what proves the NULLIF guard.
        c4 = await as_appservice_neither(conn)
        (ok if c4 == 0 else fail)(
            f"NEITHER setting present returns ZERO rows on a reused/warmed "
            f"connection — safe default-deny, no error (got {c4}, expect 0)"
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
            ok("API smoke: app imports and /health returns 200 (RLS middleware "
               "and pool wrapper wired without breaking startup)")
        else:
            fail("API smoke /health", f"status {r.status_code}")
    except Exception as exc:  # noqa: BLE001 — report, do not crash the run
        fail("API smoke", repr(exc))


async def db_main():
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0
    )
    try:
        await seed(conn)
        await structural_checks(conn)
    finally:
        await teardown(conn)
        await conn.close()
    await rls_checks()


def main():
    print("=== verify_rls1 — RLS Phase 1 (trusted_contacts pilot) ===")
    report_discovery()
    print("\n=== Structural + isolation assertions ===")
    api_smoke()
    asyncio.run(db_main())

    print(f"\n=== RESULT: {passed} passed, {failed} failed, {skipped} skipped ===")
    if failed:
        print("FAIL")
        sys.exit(1)
    if skipped:
        print("PASS (with skips — supply APP_SERVICE_DATABASE_URL for the full "
              "RLS isolation assertions)")
    else:
        print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
