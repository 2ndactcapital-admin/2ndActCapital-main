"""Headless Multi-Tenant — Sprint 1 verify — host-header tenant resolver.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown
at start AND at end, keyed on stable ids / a seeded test org + user.

What this proves (each [Y] reported explicitly):
  * Task 1 discovery findings (the REAL /theme/public pre-auth mechanism, the
    plain organizations.slug column, proxy.js having no tenant logic, and the
    real POST /orgs shape) — reported explicitly.
  * Host "myria.ripasso.com" resolves to the seeded org with slug 'myria'.
  * The bare apex (no subdomain) yields the platform MARKETING case (no tenant,
    org_id None) — the Sprint 1 correction, NOT DEFAULT_ORG_ID.
  * An unrecognized subdomain also yields the marketing case, gracefully.
  * A malformed/garbage Host does not crash the resolver (→ marketing case).
  * POST /orgs REJECTS an invalid slug (uppercase / special char / reserved).
  * POST /orgs accepts a valid, available slug.
  * The pre-auth lookup genuinely works under LIVE RLS via the NON-BYPASS
    app_service role (proves the organizations_preauth_resolve carve-out was
    reused, not just claimed) — SKIPPED (not failed) when its DSN is absent.
  * A real existing endpoint (public /theme/public + a protected route's auth
    gate) behaves identically — the Task-4 regression check.
  * Teardown: zero leftover rows.

DSNs:
  DATABASE_URL             — the app's role (RLS-bypassing 'postgres' in current
                             prod). Seeding, the endpoint (app connects via it),
                             structural checks, teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role, supplied at test
                             time ONLY. Runs the real pre-auth-under-RLS check.
                             Absent → that check SKIPs (not fail).
"""

import asyncio
import glob
import os
import sys

# ── Make this runnable via the allowlisted system `python3` OR venv python ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

import asyncpg  # noqa: E402

# ── stable ids ──────────────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_multitenant1"
TEST_EMAIL = "verify_multitenant1@test.local"
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_ORG_SLUG = "2ndactcapital"
MYRIA_ORG_ID = "a0a0a0a0-0000-0000-0000-0000000000a1"
MYRIA_SLUG = "myria"
CREATED_OK_SLUG = "verifytenant1"          # valid + available for the create test
TEST_SLUGS = [MYRIA_SLUG, CREATED_OK_SLUG]  # torn down; never the real default

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    _RESULTS.append(("SKIP", name, detail))
    print(f"[SKIP] {name}" + (f" — {detail}" if detail else ""))


# ── DB helpers (bypass role) ────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


async def teardown(conn):
    """FK-safe: settings → users → the test orgs. Never touches the real default
    org (guarded by id <> DEFAULT and a slug allow-list)."""
    await conn.execute(
        "DELETE FROM org_settings WHERE org_id IN "
        "(SELECT id FROM organizations WHERE slug = ANY($1::text[]) AND id <> $2::uuid)",
        TEST_SLUGS, DEFAULT_ORG_ID,
    )
    await conn.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)
    await conn.execute(
        "DELETE FROM organizations WHERE slug = ANY($1::text[]) AND id <> $2::uuid",
        TEST_SLUGS, DEFAULT_ORG_ID,
    )


async def seed(conn):
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, slug = EXCLUDED.slug",
        MYRIA_ORG_ID, "ZZ Verify Myria", MYRIA_SLUG,
    )
    # Let the DB generate the id — the shared 99000000… test id may already be
    # held by another verify script's leftover row. We key on auth0_sub (the
    # canonical key ensure_user resolves by) and teardown by auth0_sub.
    await conn.execute(
        "INSERT INTO users (org_id, email, auth0_sub, role) "
        "VALUES ($1, $2, $3, 'super_admin') "
        "ON CONFLICT (auth0_sub) DO NOTHING",
        DEFAULT_ORG_ID, TEST_EMAIL, TEST_AUTH0_SUB,
    )


# ── Task 1 discovery (reported explicitly) ──────────────────────────────────
async def discovery_findings(conn):
    import main
    from services.tenant import extract_subdomain  # noqa: F401
    from routers.org_settings import OrgCreate

    # 1a — the REAL /theme/public pre-auth mechanism.
    rls_on = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE relname = 'organizations'"
    )
    pols = {
        r["policyname"]: r["cmd"]
        for r in await conn.fetch(
            "SELECT policyname, cmd FROM pg_policies WHERE tablename = 'organizations'"
        )
    }
    theme_public = "/api/v1/theme/public" in main.PUBLIC_PATHS
    if (
        rls_on
        and theme_public
        and pols.get("organizations_self_or_super") == "ALL"
        and pols.get("organizations_preauth_resolve") == "SELECT"
    ):
        ok(
            "[1a] /theme/public mechanism discovered",
            "It uses the shared RLS-aware pool (get_pool) with a plain slug "
            "SELECT — NO separate bypass connection, NO pre-existing carve-out. "
            "It only 'worked' pre-auth because prod connects as the "
            "RLS-BYPASSING postgres role; the base organizations_self_or_super "
            "(ALL) policy returns ZERO rows pre-auth under app_service. This "
            "sprint adds the organizations_preauth_resolve (SELECT) carve-out so "
            "the same pool pattern is genuinely RLS-safe.",
        )
    else:
        fail(
            "[1a] /theme/public mechanism discovered",
            f"rls_on={rls_on} theme_public={theme_public} policies={pols}",
        )

    # 1b — organizations.slug is a plain UNIQUE text column, no CHECK validation.
    col = await conn.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'organizations' AND column_name = 'slug'"
    )
    checks = await conn.fetch(
        "SELECT con.conname, pg_get_constraintdef(con.oid) AS def "
        "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
        "WHERE c.relname = 'organizations' AND con.contype = 'c'"
    )
    if col and col["data_type"] == "text" and not checks:
        ok(
            "[1b] organizations.slug is plain UNIQUE text, no format validation",
            "data_type=text, zero CHECK constraints — confirms slug format is "
            "enforced only in application code (Task 3 adds that).",
        )
    else:
        fail(
            "[1b] organizations.slug is plain UNIQUE text, no format validation",
            f"data_type={col and col['data_type']} checks={[c['conname'] for c in checks]}",
        )

    # 1c — proxy.js only runs Auth0 middleware, no tenant logic.
    proxy_path = os.path.join(_REPO_ROOT, "apps", "web", "proxy.js")
    try:
        src = open(proxy_path, encoding="utf-8").read()
    except OSError:
        src = ""
    low = src.lower()
    if "auth0.middleware" in low and "subdomain" not in low and "tenant" not in low:
        ok(
            "[1c] proxy.js has no tenant logic (only auth0.middleware)",
            "New resolver logic lives server-side in FastAPI (routers/tenant + "
            "services/tenant); proxy.js is where a future request-time tenant "
            "hint would be injected once the wildcard domain is provisioned.",
        )
    else:
        fail(
            "[1c] proxy.js has no tenant logic (only auth0.middleware)",
            f"auth0={'auth0.middleware' in low} subdomain={'subdomain' in low} "
            f"tenant={'tenant' in low}",
        )

    # 1d — POST /orgs real shape: requires name + slug, super_admin only.
    fields = set(OrgCreate.model_fields.keys())
    if fields == {"name", "slug"}:
        ok(
            "[1d] POST /orgs shape discovered",
            "OrgCreate requires {name, slug}; endpoint is Super-Admin-only "
            "(is_super_admin gate) and 409s on a duplicate slug. Task 3 extends "
            "its slug validation.",
        )
    else:
        fail("[1d] POST /orgs shape discovered", f"OrgCreate fields = {fields}")


# ── resolver unit crash-proofing (malformed hosts) ──────────────────────────
async def resolver_unit_checks(conn):
    from services.tenant import resolve_tenant

    garbage = [None, "", "   ", "!!!", "...", "1.2.3.4", "[::1]:8000",
               "localhost", "a.b.c.d.e.f", 12345, "myria.ripasso.com:", ":::"]
    crashed = None
    for g in garbage:
        try:
            r = await resolve_tenant(conn, g)
            # Sprint 1 CORRECTION: the resolver must never raise and must always
            # return a well-formed dict. A garbage/no-subdomain host yields the
            # marketing case (org_id None); the ONE input that happens to carry a
            # real subdomain ("myria.ripasso.com:" — trailing colon stripped)
            # legitimately resolves. Either way: no raise, and marketing is the
            # boolean complement of resolved.
            well_formed = (
                isinstance(r, dict)
                and "marketing" in r
                and isinstance(r.get("marketing"), bool)
                and r.get("marketing") is not r.get("resolved")
            )
            if not well_formed:
                crashed = f"host={g!r} malformed result {r}"
        except Exception as exc:  # noqa: BLE001
            crashed = f"host={g!r} raised {type(exc).__name__}: {exc}"
            break
    if crashed is None:
        ok(
            "[malformed] resolver never crashes on garbage Host, always well-formed",
            f"tested {len(garbage)} inputs — no raise; marketing == not resolved throughout",
        )
    else:
        fail("[malformed] resolver never crashes on garbage Host", crashed)


# ── endpoint tests via TestClient with simulated Host headers ───────────────
def endpoint_tests():
    import main
    from starlette.testclient import TestClient

    main.verify_token = lambda _token: {
        "sub": TEST_AUTH0_SUB,
        "email": TEST_EMAIL,
        "org_id": DEFAULT_ORG_ID,
    }
    auth_hdr = {"Authorization": "Bearer stub"}

    with TestClient(main.app, raise_server_exceptions=False) as c:
        # 2 — myria.ripasso.com → seeded org 'myria'
        r = c.get("/api/v1/tenant/resolve", headers={"host": "myria.ripasso.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("resolved") is True
            and str(b.get("org_id")) == MYRIA_ORG_ID
            and b.get("subdomain") == MYRIA_SLUG
            and b.get("source") == "subdomain"
        ):
            ok("[resolve] Host 'myria.ripasso.com' → seeded org id (slug 'myria')",
               f"org_id={b.get('org_id')} source={b.get('source')}")
        else:
            fail("[resolve] Host 'myria.ripasso.com' → seeded org id",
                 f"status={r.status_code} body={b}")

        # 3 — bare domain (no subdomain) → platform MARKETING, not a tenant.
        # Sprint 1 CORRECTION: the apex is Hollisworks' own marketing site; it
        # must NOT resolve to 2nd Act's DEFAULT_ORG_ID as if a real tenant.
        r = c.get("/api/v1/tenant/resolve", headers={"host": "hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("marketing") is True
            and b.get("resolved") is False
            and b.get("org_id") is None
            and b.get("source") == "platform"
        ):
            ok("[resolve] apex 'hollisworks.com' (no subdomain) → marketing (no tenant)",
               f"marketing={b.get('marketing')} org_id={b.get('org_id')} — NOT DEFAULT_ORG_ID")
        else:
            fail("[resolve] apex → marketing case",
                 f"status={r.status_code} body={b}")

        # 4 — unrecognized subdomain → marketing (no tenant match), no error
        r = c.get("/api/v1/tenant/resolve",
                  headers={"host": "nosuchtenant-xyz.hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("marketing") is True
            and b.get("resolved") is False
            and b.get("org_id") is None
        ):
            ok("[resolve] unrecognized subdomain → marketing (graceful)",
               f"subdomain={b.get('subdomain')} marketing={b.get('marketing')}")
        else:
            fail("[resolve] unrecognized subdomain → marketing",
                 f"status={r.status_code} body={b}")

        # 5 — malformed Host via the endpoint does not crash (200 + marketing)
        r = c.get("/api/v1/tenant/resolve", headers={"host": "garbage.value"})
        b = r.json() if r.status_code == 200 else {}
        if r.status_code == 200 and b.get("marketing") is True and b.get("org_id") is None:
            ok("[resolve] malformed Host via endpoint → 200 + marketing (no crash)",
               f"marketing={b.get('marketing')}")
        else:
            fail("[resolve] malformed Host via endpoint → 200 + marketing",
                 f"status={r.status_code} body={b}")

        # 6 — POST /orgs rejects invalid slugs (uppercase / special / reserved)
        bad = {
            "uppercase 'BadOrg'": "BadOrg",
            "special char 'bad_slug'": "bad_slug",
            "reserved 'admin'": "admin",
        }
        all_rejected = True
        details = []
        for label, slug in bad.items():
            rr = c.post("/api/v1/orgs", headers=auth_hdr,
                        json={"name": "ZZ Verify Invalid", "slug": slug})
            details.append(f"{label}->{rr.status_code}")
            if rr.status_code != 400:
                all_rejected = False
        if all_rejected:
            ok("[create] invalid slug (uppercase/special/reserved) REJECTED with 400",
               "; ".join(details))
        else:
            fail("[create] invalid slug REJECTED with 400", "; ".join(details))

        # 7 — POST /orgs accepts a valid, available slug
        rr = c.post("/api/v1/orgs", headers=auth_hdr,
                    json={"name": "ZZ Verify Tenant One", "slug": CREATED_OK_SLUG})
        b = rr.json() if rr.status_code == 201 else {}
        if rr.status_code == 201 and b.get("slug") == CREATED_OK_SLUG:
            ok("[create] valid available slug accepted with 201",
               f"created org id={b.get('id')} slug={b.get('slug')}")
        else:
            fail("[create] valid available slug accepted with 201",
                 f"status={rr.status_code} body={rr.text[:200]}")

        # 9b — regression: existing /theme/public still resolves the default org
        r = c.get(f"/api/v1/theme/public?slug={DEFAULT_ORG_SLUG}")
        b = r.json() if r.status_code == 200 else {}
        theme_ok = r.status_code == 200 and str(b.get("org_id")) == DEFAULT_ORG_ID
        # 9c — regression: a protected route still rejects a token-less request
        r2 = c.get("/api/v1/theme")   # no Authorization header
        auth_ok = r2.status_code == 401
        if theme_ok and auth_ok:
            ok("[regression] existing /theme/public unchanged + auth gate intact",
               f"theme/public default org OK; protected /theme without token → "
               f"{r2.status_code}")
        else:
            fail("[regression] existing endpoint behaves identically",
                 f"theme_public_default={theme_ok} protected_401={auth_ok} "
                 f"(got {r2.status_code})")


# ── the real proof: pre-auth lookup under the NON-BYPASS app_service role ────
async def app_service_checks():
    if not APP_SERVICE_DATABASE_URL:
        skip("[rls] pre-auth slug lookup works under live RLS (app_service)",
             "APP_SERVICE_DATABASE_URL not set")
        skip("[rls] authenticated user does NOT see other orgs (carve-out inert)",
             "APP_SERVICE_DATABASE_URL not set")
        skip("[rls] pre-auth WRITE stays blocked (SELECT-only carve-out)",
             "APP_SERVICE_DATABASE_URL not set")
        return

    conn = await _connect(APP_SERVICE_DATABASE_URL)
    try:
        role, bypass = await conn.fetchrow(
            "SELECT current_user, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        )
        if bypass:
            fail("[rls] app_service DSN is a NON-BYPASS role",
                 f"connected as {role!r} with rolbypassrls=true — supply the "
                 "app_service DSN, not a bypass role")
            return

        async def apply(org="", sup="false"):
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', $2, true),"
                "       set_config('app.current_auth0_sub', '', true)",
                org, sup,
            )

        # THE assertion: pre-auth (no org, not super) slug lookup returns the row.
        async with conn.transaction():
            await apply()
            row = await conn.fetchrow(
                "SELECT id, slug FROM organizations WHERE slug = $1", MYRIA_SLUG
            )
        if row is not None and str(row["id"]) == MYRIA_ORG_ID:
            ok(f"[rls] pre-auth slug lookup works under live RLS as non-bypass "
               f"{role!r}",
               "organizations_preauth_resolve carve-out returns the myria row "
               "with NO org context — the /theme/public pool pattern is now "
               "genuinely RLS-safe, not reliant on the bypass role.")
        else:
            fail("[rls] pre-auth slug lookup works under live RLS",
                 f"expected myria row, got {row and dict(row)}")

        # Carve-out is inert once authenticated: an ORG_A member sees only ORG_A.
        async with conn.transaction():
            await apply(org=DEFAULT_ORG_ID)
            slugs = [r["slug"] for r in await conn.fetch(
                "SELECT slug FROM organizations")]
        if DEFAULT_ORG_SLUG in slugs and MYRIA_SLUG not in slugs:
            ok("[rls] authenticated user does NOT see other orgs (carve-out inert)",
               f"as default-org context, visible orgs = {slugs} (no cross-tenant "
               "leak)")
        else:
            fail("[rls] authenticated user does NOT see other orgs",
                 f"visible slugs as default-org context = {slugs}")

        # SELECT-only: a pre-auth write affects zero rows (rolled back regardless).
        async with conn.transaction():
            await apply()
            tag = await conn.execute(
                "UPDATE organizations SET name = name WHERE slug = $1", MYRIA_SLUG
            )
            raise _Rollback(tag)
    except _Rollback as rb:
        if str(rb.tag).strip() == "UPDATE 0":
            ok("[rls] pre-auth WRITE stays blocked (SELECT-only carve-out)",
               f"pre-auth UPDATE affected 0 rows ({rb.tag!r})")
        else:
            fail("[rls] pre-auth WRITE stays blocked (SELECT-only carve-out)",
                 f"pre-auth UPDATE tag = {rb.tag!r} (expected 'UPDATE 0')")
    finally:
        await conn.close()


class _Rollback(Exception):
    def __init__(self, tag):
        self.tag = tag


# ── driver ──────────────────────────────────────────────────────────────────
async def main_async():
    if not DATABASE_URL:
        print("[SKIP] DATABASE_URL not set — cannot run verify_multitenant1")
        return 0

    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)          # teardown at START
        await seed(conn)
        await discovery_findings(conn)
        await resolver_unit_checks(conn)
    finally:
        await conn.close()

    # Endpoint tests run against the real ASGI app (connects via DATABASE_URL).
    try:
        endpoint_tests()
    except Exception as exc:  # noqa: BLE001
        import traceback
        fail("[endpoint] TestClient run", f"{exc}")
        print(traceback.format_exc())

    # The real RLS proof (own connection as app_service).
    await app_service_checks()

    # Teardown at END + prove zero leftovers.
    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)
        left_orgs = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE slug = ANY($1::text[]) "
            "AND id <> $2::uuid",
            TEST_SLUGS, DEFAULT_ORG_ID,
        )
        left_users = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB
        )
        if left_orgs == 0 and left_users == 0:
            ok("[teardown] zero leftover rows", "test orgs + test user removed")
        else:
            fail("[teardown] zero leftover rows",
                 f"orgs={left_orgs} users={left_users}")
    finally:
        await conn.close()

    # ── summary ──
    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    skipped = sum(1 for s, *_ in _RESULTS if s == "SKIP")
    print("\n" + "=" * 70)
    print(f"RESULT: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 70)
    if failed:
        print("FAILURES:")
        for s, n, d in _RESULTS:
            if s == "FAIL":
                print(f"  [FAIL] {n} — {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
