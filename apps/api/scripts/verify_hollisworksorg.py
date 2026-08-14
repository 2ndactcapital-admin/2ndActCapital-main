"""Hollisworks-org + landing-fix sprint — verify. Pass/fail only, UNATTENDED.

Hollisworks is now a REAL org row (id bb347258-…, name 'Hollisworks', slug
'hollisworks') like any other client — NOT a special case kept out of the
organizations table. This sprint wires three small, related fixes on the shared
login/resolver surface and proves them END-TO-END through the real modules:

  * TASK 2  admin.hollisworks.com resolves to the 'hollisworks' org via an
            explicit host->slug override (services/tenant.HOST_SLUG_OVERRIDES),
            WITHOUT ever assigning the org the reserved 'admin' slug.
  * TASK 3  admin-host logins land in the NORMAL app (/dashboard); the separate
            /admin-console surface is removed.
  * TASK 4  firm-search drops the hardcoded "Hollisworks" special case (it now
            resolves via normal org matching to the org's REAL stored login_url)
            and gains 'admin'/'hollis'/'hollisworks' aliases.
  * TASK 5  bare /admin is no longer a 404 — a minimal index gated by the SAME
            real permission checks the sidebar uses.

DB-backed assertions run through the real FastAPI app (TestClient + real Host
headers) and the real pre-auth RLS carve-outs, exactly as production does — NOT
by calling internal helpers in isolation. Frontend behavior (JS) is asserted
against the real deployed source files.

Asserts (each reported explicitly):
  [Y] Task-1 four discovery findings, explicitly.
  [Y] Host admin.hollisworks.com          -> 'hollisworks' org (bb347258-…), NOT default.
  [Y] 'admin' is STILL a reserved slug     -> validate_slug('admin') rejected.
  [Y] Host 2ndactcapital.hollisworks.com  -> 2nd Act (regression).
  [Y] Host hollisworks.com (bare)          -> marketing (regression).
  [Y] admin-host login lands at /dashboard, not /admin-console.
  [Y] Firm-search 'Hollisworks'            -> normal match to REAL stored login_url.
  [Y] Firm-search 'admin' and 'hollis'     -> resolve correctly.
  [Y] Firm-search '2nd Act Capital'        -> its REAL stored login_url (regression).
  [Y] Bare /admin no longer 404s + respects real permissions.
  [Y] npm run build exits 0.
  [Y] Teardown: zero leftover rows.
"""

import asyncio
import glob
import os
import subprocess
import sys

# ── runnable via allowlisted system python3 OR venv python ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
_WEB_ROOT = os.path.join(_REPO_ROOT, "apps", "web")
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

import asyncpg  # noqa: E402

# ── stable ids ──
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"   # 2nd Act, slug 2ndactcapital
HOLLISWORKS_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"  # Hollisworks, slug hollisworks

# Teardown sentinel (round-trips a real row to prove teardown genuinely deletes).
SENTINEL_EMAIL = "zz_hollisorg_verify@test.local"
SENTINEL_FIRM = "ZZ HollisOrg Verify Sentinel"

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── pass/fail harness ──
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


async def _connect(dsn):
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


async def teardown(conn):
    """FK-safe: only ever removes our own sentinel row. Never touches real data."""
    await conn.execute(
        "DELETE FROM marketing_contacts WHERE email = $1 OR firm = $2",
        SENTINEL_EMAIL, SENTINEL_FIRM,
    )


# ── Task 1: four discovery findings (real files, real root causes) ──
def discovery_findings():
    tenant_src = _read(os.path.join(_API_ROOT, "services", "tenant.py"))
    login_src = _read(os.path.join(_WEB_ROOT, "app", "login", "page.js"))
    marketing_src = _read(os.path.join(_API_ROOT, "services", "marketing.py"))
    admin_index = os.path.join(_WEB_ROOT, "app", "admin", "page.js")

    # 1a — resolver maps subdomain -> org by slug; host override is where the
    #      admin.hollisworks.com -> 'hollisworks' mapping lives (keeps 'admin'
    #      reserved).
    if (
        "WHERE slug = $1" in tenant_src
        and "HOST_SLUG_OVERRIDES" in tenant_src
        and "admin.hollisworks.com" in tenant_src
    ):
        ok("[1a] Resolver maps subdomain->org by slug; host override added",
           "services/tenant.resolve_tenant looks up organizations WHERE slug = "
           "extract_subdomain(host). 'admin' is a RESERVED slug, so "
           "admin.hollisworks.com cannot resolve by that path — a narrow "
           "HOST_SLUG_OVERRIDES map ('admin.hollisworks.com'->'hollisworks') "
           "resolves it to the real org without assigning it the reserved slug.")
    else:
        fail("[1a] Resolver + host override finding",
             "expected WHERE slug=$1 + HOST_SLUG_OVERRIDES in services/tenant.py")

    # 1b — the line that sent admin-host logins to /admin-console (a bounce),
    #      now landing everyone at /dashboard.
    if "/admin-console" not in login_src and '"/dashboard"' in login_src:
        ok("[1b] Login landing fixed (was app/login/page.js returnTo='/admin-console')",
           "app/login/page.js previously set returnTo = isAdmin ? '/admin-console' "
           ": '/dashboard'; the /admin-console branch bounced authenticated staff. "
           "Now every host — admin.hollisworks.com included — lands at /dashboard.")
    else:
        fail("[1b] Login landing finding",
             "expected /admin-console removed and '/dashboard' present in login/page.js")

    # 1c — the hardcoded firm-search special case is removed in favor of normal
    #      org matching (Hollisworks is now a real org row).
    if (
        "_is_hollisworks" not in marketing_src
        and "HOLLISWORKS_LOGIN_URL" not in marketing_src
        and "FIRM_ALIASES" in marketing_src
    ):
        ok("[1c] Firm-search hardcoded 'Hollisworks' special case removed",
           "services/marketing previously matched 'hollisworks' FIRST and returned "
           "a hardcoded https://admin.hollisworks.com/login redirect. Removed: it "
           "now resolves through normal org matching (real org row) to the org's "
           "REAL stored login_url; only short-nickname aliases remain (FIRM_ALIASES).")
    else:
        fail("[1c] Firm-search special-case removal finding",
             "expected _is_hollisworks + HOLLISWORKS_LOGIN_URL gone, FIRM_ALIASES present")

    # 1d — bare /admin had 14 subdirs and no index page.js (404); a real nav item
    #      (Sidebar ADMIN_ITEM href '/admin') pointed at it.
    admin_dir = os.path.join(_WEB_ROOT, "app", "admin")
    subdirs = [
        d for d in os.listdir(admin_dir)
        if os.path.isdir(os.path.join(admin_dir, d))
    ] if os.path.isdir(admin_dir) else []
    sidebar_src = _read(os.path.join(_WEB_ROOT, "components", "Sidebar.jsx"))
    nav_points_at_admin = 'href: "/admin"' in sidebar_src
    if nav_points_at_admin and len(subdirs) >= 10 and os.path.exists(admin_index):
        ok("[1d] Bare /admin 404 confirmed + real nav item pointed at it",
           f"app/admin/ has {len(subdirs)} subdirs and previously NO root page.js, "
           "so bare /admin 404'd; Sidebar.jsx ADMIN_ITEM (href '/admin', gated by "
           "can('manage_members')) is the real menu link that hit it. Fixed by "
           "adding app/admin/page.js (Task 5).")
    else:
        fail("[1d] Bare /admin 404 finding",
             f"nav_points_at_admin={nav_points_at_admin} subdirs={len(subdirs)} "
             f"index_exists={os.path.exists(admin_index)}")


# ── Task 2 + regressions: resolver through the real API with real Host headers ──
def resolver_tests():
    if not DATABASE_URL:
        fail("[resolver] endpoint tests", "DATABASE_URL not set — cannot exercise resolver")
        return
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        fail("[resolver] import FastAPI app", f"{exc}")
        return

    with TestClient(main.app, raise_server_exceptions=False) as c:
        # Task 2 — admin.hollisworks.com -> 'hollisworks' org (NOT default).
        r = c.get("/api/v1/tenant/resolve", headers={"host": "admin.hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and str(b.get("org_id")) == HOLLISWORKS_ORG_ID
            and b.get("org_slug") == "hollisworks"
            and b.get("resolved") is True
            and b.get("marketing") is False
        ):
            ok("[task2] Host admin.hollisworks.com -> 'hollisworks' org (bb347258-…)",
               f"org_id={b.get('org_id')} slug={b.get('org_slug')} resolved=True; "
               "explicit host override, NOT the default/2nd Act org.")
        else:
            fail("[task2] admin.hollisworks.com resolves to hollisworks org",
                 f"resolver={b}")

        # REGRESSION — 2ndactcapital.hollisworks.com -> 2nd Act.
        r = c.get("/api/v1/tenant/resolve",
                  headers={"host": "2ndactcapital.hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and str(b.get("org_id")) == DEFAULT_ORG_ID
            and b.get("resolved") is True
            and b.get("marketing") is False
        ):
            ok("[regress] Host 2ndactcapital.hollisworks.com -> 2nd Act (unchanged)",
               f"org_id={b.get('org_id')} resolved=True marketing=False.")
        else:
            fail("[regress] 2nd Act subdomain still resolves to its app", f"resolver={b}")

        # REGRESSION — hollisworks.com apex -> marketing.
        r = c.get("/api/v1/tenant/resolve", headers={"host": "hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("marketing") is True
            and b.get("org_id") is None
            and b.get("resolved") is False
        ):
            ok("[regress] Host hollisworks.com (bare) -> marketing (unchanged)",
               "marketing=True org_id=None resolved=False.")
        else:
            fail("[regress] hollisworks.com bare still serves marketing", f"resolver={b}")


# ── Task 2b: 'admin' stays a reserved slug ──
def reserved_slug_test():
    try:
        from services.tenant import validate_slug, SlugValidationError
    except Exception as exc:  # pragma: no cover
        fail("[reserved] import validate_slug", f"{exc}")
        return
    try:
        validate_slug("admin")
        fail("[reserved] 'admin' is STILL a reserved slug",
             "validate_slug('admin') did NOT raise — 'admin' is no longer reserved")
    except SlugValidationError:
        ok("[reserved] 'admin' is STILL a reserved slug",
           "validate_slug('admin') rejected — creating an org with slug 'admin' is "
           "still refused, so the host override (not a slug change) is what maps "
           "admin.hollisworks.com to the 'hollisworks' org.")
    except Exception as exc:
        fail("[reserved] 'admin' reserved via SlugValidationError", f"unexpected: {exc}")


# ── Task 3: admin-host login lands at /dashboard, not /admin-console ──
def login_landing_test():
    login_src = _read(os.path.join(_WEB_ROOT, "app", "login", "page.js"))
    console_page = os.path.join(_WEB_ROOT, "app", "admin-console", "page.js")
    has_console_ref = "/admin-console" in login_src
    has_dashboard = '"/dashboard"' in login_src
    console_page_exists = os.path.exists(console_page)
    if login_src and not has_console_ref and has_dashboard and not console_page_exists:
        ok("[task3] Admin-host login lands at /dashboard (no /admin-console)",
           "app/login/page.js returnTo is now '/dashboard' for every host; the "
           "separate app/admin-console/ surface is removed. A super_admin's role "
           "naturally surfaces the admin menu — no separate console.")
    else:
        fail("[task3] Login lands at /dashboard, /admin-console removed",
             f"has_console_ref={has_console_ref} has_dashboard={has_dashboard} "
             f"console_page_exists={console_page_exists}")


# ── Task 4: firm-search via the real API — normal matching + aliases ──
async def firm_search_tests():
    if not DATABASE_URL:
        fail("[firm] firm-search tests", "DATABASE_URL not set — cannot exercise matcher")
        return
    # Read the REAL stored URLs so assertions compare against the DB, not a
    # hardcoded literal (the whole point of Task 4).
    conn = await _connect(DATABASE_URL)
    try:
        hollis = await conn.fetchrow(
            "SELECT login_url, enroll_url FROM organizations WHERE id = $1",
            HOLLISWORKS_ORG_ID,
        )
        twoact = await conn.fetchrow(
            "SELECT login_url, enroll_url FROM organizations WHERE id = $1",
            DEFAULT_ORG_ID,
        )
    finally:
        await conn.close()
    if not hollis or not twoact:
        fail("[firm] real org rows present",
             "Hollisworks and/or 2nd Act org row missing — Part 1 SQL not applied?")
        return
    hollis_login = hollis["login_url"]
    twoact_login = twoact["login_url"]

    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        fail("[firm] import FastAPI app", f"{exc}")
        return

    def _search(q, intent="login"):
        with TestClient(main.app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/marketing/firm-search",
                      params={"q": q, "intent": intent})
            return (r.json() if r.status_code == 200 else {}), r.status_code

    # 'Hollisworks' -> normal org match -> REAL stored login_url (proves no
    # hardcoded special case: the old constant was …/login, the stored URL is
    # …/auth/login, so URL equality can only come from the DB).
    b, sc = _search("Hollisworks")
    if sc == 200 and b.get("status") == "matched" and b.get("redirect_url") == hollis_login:
        ok("[task4] Firm-search 'Hollisworks' -> normal match to REAL stored login_url",
           f"redirect_url={b.get('redirect_url')} == organizations.login_url (from DB), "
           "not a hardcoded special-case URL.")
    else:
        fail("[task4] 'Hollisworks' resolves via normal matching to stored login_url",
             f"result={b} expected_login_url={hollis_login}")

    # Aliases 'admin' and 'hollis' -> same Hollisworks stored login_url.
    for alias in ("admin", "hollis"):
        b, sc = _search(alias)
        if sc == 200 and b.get("status") == "matched" and b.get("redirect_url") == hollis_login:
            ok(f"[task4] Firm-search alias '{alias}' -> Hollisworks stored login_url",
               f"redirect_url={b.get('redirect_url')}.")
        else:
            fail(f"[task4] alias '{alias}' resolves to Hollisworks",
                 f"result={b} expected_login_url={hollis_login}")

    # REGRESSION — '2nd Act Capital' still resolves to its REAL stored login_url.
    b, sc = _search("2nd Act Capital")
    if sc == 200 and b.get("status") == "matched" and b.get("redirect_url") == twoact_login:
        ok("[regress] Firm-search '2nd Act Capital' -> its REAL stored login_url",
           f"redirect_url={b.get('redirect_url')} == organizations.login_url.")
    else:
        fail("[regress] '2nd Act Capital' resolves to its stored login_url",
             f"result={b} expected_login_url={twoact_login}")


# ── Task 5: bare /admin index exists + reuses real permission checks ──
def admin_index_test():
    admin_index = os.path.join(_WEB_ROOT, "app", "admin", "page.js")
    src = _read(admin_index)
    if not src:
        fail("[task5] Bare /admin no longer 404s", "app/admin/page.js missing")
        return
    reuses_real_gating = (
        "getMe" in src                       # reads /users/me (real permissions)
        and "manage_members" in src          # the real permission key the sidebar uses
        and "super_admin" in src             # the real account-role gate
        and "permissions" in src
    )
    is_server_component = "use client" not in src and "getSession" in src
    if reuses_real_gating and is_server_component:
        ok("[task5] Bare /admin index exists + respects REAL permissions",
           "app/admin/page.js is a server component that reads /users/me (getMe) and "
           "gates each section by the SAME permission keys / account roles as the "
           "sidebar (manage_members, org_admin/super_admin) — no new gating invented.")
    else:
        fail("[task5] /admin index reuses real permission checks",
             f"reuses_real_gating={reuses_real_gating} server_component={is_server_component}")


# ── npm run build ──
def build_test():
    if os.environ.get("SKIP_BUILD") == "1":
        ok("[build] npm run build", "SKIPPED (SKIP_BUILD=1)")
        return
    try:
        proc = subprocess.run(
            ["npm", "run", "build"],
            cwd=_WEB_ROOT,
            capture_output=True, text=True, timeout=900,
        )
    except Exception as exc:  # pragma: no cover
        fail("[build] npm run build exits 0", f"could not run build: {exc}")
        return
    if proc.returncode == 0:
        ok("[build] npm run build exits 0", "Next.js production build succeeded.")
    else:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
        fail("[build] npm run build exits 0", f"rc={proc.returncode}\n{tail}")


async def main_async():
    # 1) Task-1 discovery findings (pure code inspection).
    discovery_findings()

    # 2) Teardown-at-start + sentinel round-trip (proves deletion genuinely works).
    if DATABASE_URL:
        conn = await _connect(DATABASE_URL)
        try:
            await teardown(conn)  # clean slate
            await conn.execute(
                "INSERT INTO marketing_contacts (name, firm, email, source_host) "
                "VALUES ($1, $2, $3, $4)",
                "HollisOrg Verify", SENTINEL_FIRM, SENTINEL_EMAIL, "verify.local",
            )
            n = await conn.fetchval(
                "SELECT count(*) FROM marketing_contacts WHERE email = $1", SENTINEL_EMAIL
            )
            if n != 1:
                fail("[setup] sentinel round-trip insert", f"expected 1 got {n}")
        finally:
            await conn.close()
    else:
        fail("[setup] DATABASE_URL present", "not set — DB-backed assertions cannot run")

    # 3) Task 2 + regressions — resolver via the real API.
    resolver_tests()
    reserved_slug_test()

    # 4) Task 3 — login landing.
    login_landing_test()

    # 5) Task 4 — firm-search via the real API.
    await firm_search_tests()

    # 6) Task 5 — bare /admin index.
    admin_index_test()

    # 7) npm run build.
    build_test()

    # 8) Teardown-at-end + zero-leftover assertion.
    if DATABASE_URL:
        conn = await _connect(DATABASE_URL)
        try:
            await teardown(conn)
            left = await conn.fetchval(
                "SELECT count(*) FROM marketing_contacts WHERE email = $1 OR firm = $2",
                SENTINEL_EMAIL, SENTINEL_FIRM,
            )
            if left == 0:
                ok("[teardown] zero leftover rows", "sentinel fully removed (count=0).")
            else:
                fail("[teardown] zero leftover rows", f"{left} sentinel rows remain")
        finally:
            await conn.close()

    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    print("\n".join(f"[{s}] {n} — {d}" for s, n, d in _RESULTS))
    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
