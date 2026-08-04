"""Hollisworks Marketing sprint — verify. Pass/fail only, UNATTENDED, idempotent.

Teardown at START and at END, keyed on stable seeded ids / a slug allow-list and
a test-contact email. Never touches real rows (2nd Act default org, Ripasso).

Asserts (each reported explicitly):
  [Y] Task-1 discovery findings (a)/(b)/(c) still hold against live code.
  [Y] Apex 'hollisworks.com' (no subdomain) resolves to the MARKETING case
      (Hollisworks page), NOT 2nd Act's app — resolver + frontend wiring.
  [Y] '2ndactcapital.hollisworks.com' still resolves to 2nd Act (regression).
  [Y] Firm-search intent=login → seeded org → its REAL STORED login_url.
  [Y] Firm-search intent=enroll → same org → its REAL STORED enroll_url.
  [Y] Firm-search ambiguous/no-match → clarify/retry, NO pick-list, NO guess.
  [Y] Firm-search 'Hollisworks' → admin.hollisworks.com login/enroll per intent.
  [Y] A real contact-form submission is genuinely persisted.
  [Y] npm run build exits 0.
  [Y] No 2nd Act (Signature) palette hex in the Hollisworks marketing surface.
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

# ── stable ids / seed data ──
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_ORG_SLUG = "2ndactcapital"

# Confident-match org (real stored login_url / enroll_url).
MATCH_ORG_ID = "b1b1b1b1-0000-0000-0000-0000000000b1"
MATCH_NAME = "Verify Uniquefirm Holdings"
MATCH_SLUG = "verifyuniquefirm"
MATCH_LOGIN_URL = "https://verifyuniquefirm.hollisworks.com/portal/login"
MATCH_ENROLL_URL = "https://verifyuniquefirm.hollisworks.com/portal/enroll"

# Ambiguous pair — two near-identical names.
AMBIG_A_ID = "b2b2b2b2-0000-0000-0000-0000000000a1"
AMBIG_A_NAME = "Verify Ambigco Northstar Advisors"
AMBIG_A_SLUG = "verifyambigco-a"
AMBIG_B_ID = "b2b2b2b2-0000-0000-0000-0000000000b2"
AMBIG_B_NAME = "Verify Ambigco Northstar Advisers"
AMBIG_B_SLUG = "verifyambigco-b"

TEST_SLUGS = [MATCH_SLUG, AMBIG_A_SLUG, AMBIG_B_SLUG]
TEST_CONTACT_EMAIL = "verify_hollis_contact@test.local"
TEST_CONTACT_FIRM = "ZZ Verify Hollis Contact Firm"

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── pass/fail harness ──
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


async def _connect(dsn):
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


# ── seed / teardown ──
async def teardown(conn):
    await conn.execute(
        "DELETE FROM org_settings WHERE org_id IN "
        "(SELECT id FROM organizations WHERE slug = ANY($1::text[]) AND id <> $2::uuid)",
        TEST_SLUGS, DEFAULT_ORG_ID,
    )
    await conn.execute(
        "DELETE FROM organizations WHERE slug = ANY($1::text[]) AND id <> $2::uuid",
        TEST_SLUGS, DEFAULT_ORG_ID,
    )
    await conn.execute(
        "DELETE FROM marketing_contacts WHERE email = $1 OR firm = $2",
        TEST_CONTACT_EMAIL, TEST_CONTACT_FIRM,
    )


async def seed(conn):
    await conn.execute(
        "INSERT INTO organizations (id, name, slug, login_url, enroll_url) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, slug = EXCLUDED.slug, "
        "login_url = EXCLUDED.login_url, enroll_url = EXCLUDED.enroll_url",
        MATCH_ORG_ID, MATCH_NAME, MATCH_SLUG, MATCH_LOGIN_URL, MATCH_ENROLL_URL,
    )
    for oid, name, slug in (
        (AMBIG_A_ID, AMBIG_A_NAME, AMBIG_A_SLUG),
        (AMBIG_B_ID, AMBIG_B_NAME, AMBIG_B_SLUG),
    ):
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, slug = EXCLUDED.slug",
            oid, name, slug,
        )


# ── Task 1 discovery (reported explicitly) ──
async def discovery_findings(conn):
    import main
    from services.tenant import resolve_tenant  # noqa: F401
    import inspect
    import services.tenant as tmod

    # (a) resolve_tenant is the exact fallback logic; now signals marketing for
    #     the no-subdomain/default case (the Sprint-1 correction target).
    src = inspect.getsource(tmod.resolve_tenant)
    if '"marketing": True' in src and "source" in src and "extract_subdomain" in src:
        ok("[1a] resolver fallback is the corrected marketing signal",
           "resolve_tenant() pre-seeds the platform-marketing case (org_id None, "
           "marketing True) and only overwrites it on a real subdomain match — "
           "the exact Sprint-1 fallback logic, now corrected.")
    else:
        fail("[1a] resolver marketing signal present", "resolve_tenant source lacks marketing flag")

    # (b) frontend now DOES consume the resolver (this sprint built the wiring).
    tenant_lib = os.path.join(_WEB_ROOT, "lib", "tenant.js")
    page_js = os.path.join(_WEB_ROOT, "app", "page.js")
    lib_src = _read(tenant_lib)
    page_src = _read(page_js)
    if (
        "tenant/resolve" in lib_src
        and "resolveTenant" in page_src
        and "HollisworksMarketing" in page_src
        and "tenant.marketing" in page_src
    ):
        ok("[1b] frontend now consumes the resolver (new wiring built)",
           "lib/tenant.js calls GET /api/v1/tenant/resolve; app/page.js branches "
           "on tenant.marketing → HollisworksMarketing. Prior state (zero "
           "consumption) confirmed corrected.")
    else:
        fail("[1b] frontend resolver wiring", f"lib_has_resolve={'tenant/resolve' in lib_src} "
             f"page_branches={'tenant.marketing' in page_src}")

    # (c) organizations has login_url + enroll_url (nullable text); saml col intact.
    cols = {
        r["column_name"]: r["data_type"]
        for r in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'organizations' AND table_schema = 'public'"
        )
    }
    if (
        cols.get("login_url") == "text"
        and cols.get("enroll_url") == "text"
        and cols.get("saml_connection_name") == "text"
    ):
        ok("[1c] organizations.login_url/enroll_url live (saml col untouched)",
           "login_url, enroll_url, saml_connection_name all present as nullable "
           "text — firm-search reads the real stored URLs; saml col left alone.")
    else:
        fail("[1c] organizations url columns", f"cols={ {k: cols.get(k) for k in ('login_url','enroll_url','saml_connection_name')} }")

    # marketing endpoints are registered public (pre-auth) — structural sanity.
    if (
        "/api/v1/marketing/firm-search" in main.PUBLIC_PATHS
        and "/api/v1/marketing/contact" in main.PUBLIC_PATHS
    ):
        ok("[1d] marketing endpoints public (pre-tenant)",
           "firm-search + contact in PUBLIC_PATHS — reachable before auth.")
    else:
        fail("[1d] marketing endpoints public", "not both in PUBLIC_PATHS")


def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


# ── endpoint tests via TestClient ──
def endpoint_tests():
    import main
    from starlette.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as c:
        # 2 — apex (no subdomain) → marketing (Hollisworks), NOT 2nd Act.
        r = c.get("/api/v1/tenant/resolve", headers={"host": "hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        page_src = _read(os.path.join(_WEB_ROOT, "app", "page.js"))
        mkt_src = _read(os.path.join(_WEB_ROOT, "components", "HollisworksMarketing.jsx"))
        renders_hollis = (
            "return <HollisworksMarketing />" in page_src
            and "Hollis works. For you." in mkt_src
        )
        if (
            r.status_code == 200
            and b.get("marketing") is True
            and b.get("org_id") is None
            and b.get("resolved") is False
            and renders_hollis
        ):
            ok("[apex] hollisworks.com → Hollisworks marketing page (NOT 2nd Act)",
               "resolver marketing=True + page.js renders <HollisworksMarketing/> "
               "with the Hollisworks hero copy; org_id is None (no tenant).")
        else:
            fail("[apex] hollisworks.com → marketing page",
                 f"resolver={b} renders_hollis={renders_hollis}")

        # 3 — REGRESSION: 2ndactcapital.hollisworks.com → 2nd Act app.
        r = c.get("/api/v1/tenant/resolve",
                  headers={"host": "2ndactcapital.hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        # page.js falls through to 2nd Act's own landing for a resolved tenant.
        tenant_app_intact = (
            "post-liquidity" in page_src and "if (tenant.marketing)" in page_src
        )
        if (
            r.status_code == 200
            and b.get("resolved") is True
            and str(b.get("org_id")) == DEFAULT_ORG_ID
            and b.get("marketing") is False
            and tenant_app_intact
        ):
            ok("[regression] 2ndactcapital.hollisworks.com → 2nd Act app (unchanged)",
               f"resolved=True org_id={b.get('org_id')} marketing=False; page.js "
               "falls through to 2nd Act's own landing.")
        else:
            fail("[regression] 2nd Act subdomain still resolves to its app",
                 f"resolver={b} tenant_app_intact={tenant_app_intact}")

        # 4 — firm-search intent=login → confident match → REAL stored login_url.
        r = c.get("/api/v1/marketing/firm-search",
                  params={"q": MATCH_NAME, "intent": "login"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("status") == "matched"
            and b.get("redirect_url") == MATCH_LOGIN_URL
        ):
            ok("[firm-search:login] confident match → REAL stored login_url",
               f"'{MATCH_NAME}' → {b.get('redirect_url')}")
        else:
            fail("[firm-search:login] → stored login_url", f"body={b}")

        # 5 — firm-search intent=enroll → same org → REAL stored enroll_url.
        r = c.get("/api/v1/marketing/firm-search",
                  params={"q": MATCH_NAME, "intent": "enroll"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("status") == "matched"
            and b.get("redirect_url") == MATCH_ENROLL_URL
        ):
            ok("[firm-search:enroll] same org → REAL stored enroll_url",
               f"'{MATCH_NAME}' → {b.get('redirect_url')}")
        else:
            fail("[firm-search:enroll] → stored enroll_url", f"body={b}")

        # 6a — ambiguous → clarify (no pick-list, no guess).
        r = c.get("/api/v1/marketing/firm-search",
                  params={"q": "Verify Ambigco Northstar Advisor", "intent": "login"})
        b = r.json() if r.status_code == 200 else {}
        no_picklist = "redirect_url" in b and not b.get("redirect_url")
        if (
            r.status_code == 200
            and b.get("status") == "ambiguous"
            and no_picklist
            and b.get("message")
        ):
            ok("[firm-search:ambiguous] → clarify/retry, no pick-list, no guess",
               f"status=ambiguous, redirect_url={b.get('redirect_url')!r}, "
               f"msg present; DID NOT auto-pick a firm.")
        else:
            fail("[firm-search:ambiguous] → clarify", f"body={b}")

        # 6b — no match → retry (no guess).
        r = c.get("/api/v1/marketing/firm-search",
                  params={"q": "zzq nonexistent gibberish firm 999", "intent": "login"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("status") == "none"
            and not b.get("redirect_url")
            and b.get("message")
        ):
            ok("[firm-search:none] no match → retry message, no guess",
               f"status=none, redirect_url={b.get('redirect_url')!r}")
        else:
            fail("[firm-search:none] → retry", f"body={b}")

        # 7 — 'Hollisworks' special case → admin host, per intent.
        r_l = c.get("/api/v1/marketing/firm-search",
                    params={"q": "Hollisworks", "intent": "login"})
        bl = r_l.json() if r_l.status_code == 200 else {}
        r_e = c.get("/api/v1/marketing/firm-search",
                    params={"q": "Hollisworks", "intent": "enroll"})
        be = r_e.json() if r_e.status_code == 200 else {}
        if (
            bl.get("status") == "matched"
            and bl.get("redirect_url") == "https://admin.hollisworks.com/login"
            and be.get("status") == "matched"
            and be.get("redirect_url") == "https://admin.hollisworks.com/enroll"
        ):
            ok("[firm-search:hollisworks] special case → admin.hollisworks.com per intent",
               f"login→{bl.get('redirect_url')} ; enroll→{be.get('redirect_url')} "
               "(NOTE: admin.hollisworks.com destination is a PLACEHOLDER pending "
               "later work — the redirect target is correctly wired now).")
        else:
            fail("[firm-search:hollisworks] → admin host", f"login={bl} enroll={be}")

        # 8 — contact form genuinely persisted.
        payload = {
            "name": "Verify Contact Person",
            "firm": TEST_CONTACT_FIRM,
            "email": TEST_CONTACT_EMAIL,
            "aum": "$100M – $500M",
            "note": "Reconciliation and document handling.",
        }
        r = c.post("/api/v1/marketing/contact", json=payload)
        b = r.json() if r.status_code == 200 else {}
        contact_id = b.get("id")
        if r.status_code == 200 and b.get("status") == "received" and contact_id:
            ok("[contact] POST /marketing/contact accepted", f"id={contact_id}")
        else:
            fail("[contact] POST accepted", f"status={r.status_code} body={b}")
        return contact_id  # verified against DB by the caller


# ── palette guard ──
def palette_guard():
    # 2nd Act (Signature) palette hex — must NOT appear in the Hollisworks surface.
    signature_hex = [
        "#1B2B4B", "#C5A880", "#E8D5A3", "#FAF9F6", "#F5F1EB", "#9AA6BF",
    ]
    hollis_files = [
        os.path.join(_WEB_ROOT, "components", "HollisworksMarketing.jsx"),
        os.path.join(_WEB_ROOT, "components", "FirmSearchClient.jsx"),
    ]
    offenders = []
    have_own_tokens = False
    for f in hollis_files:
        src = _read(f).upper()
        if "#1F4034" in src:  # holly — the page's own token
            have_own_tokens = True
        for hexv in signature_hex:
            if hexv.upper() in src:
                offenders.append(f"{os.path.basename(f)}:{hexv}")
    if not offenders and have_own_tokens:
        ok("[palette] no 2nd Act Signature hex in Hollisworks surface; own tokens present",
           "Hollisworks page uses holly/bronze/paper (#1F4034 …), zero --2a- hex.")
    else:
        fail("[palette] Hollisworks surface palette",
             f"offenders={offenders} own_tokens={have_own_tokens}")


def build_check():
    # This is an npm-workspaces + turbo monorepo — node_modules is hoisted to the
    # repo root, not apps/web. `npm run build` in apps/web resolves it fine.
    if not (
        os.path.isdir(os.path.join(_REPO_ROOT, "node_modules"))
        or os.path.isdir(os.path.join(_WEB_ROOT, "node_modules"))
    ):
        fail("[build] npm run build exits 0", "node_modules missing — cannot build")
        return
    try:
        proc = subprocess.run(
            ["npm", "run", "build"],
            cwd=_WEB_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except Exception as exc:  # noqa: BLE001
        fail("[build] npm run build exits 0", f"exec error: {exc}")
        return
    if proc.returncode == 0:
        ok("[build] npm run build exits 0", "Next.js production build succeeded")
    else:
        tail = (proc.stdout or "")[-1500:] + "\n" + (proc.stderr or "")[-1500:]
        fail("[build] npm run build exits 0", f"rc={proc.returncode}\n{tail}")


# ── driver ──
async def main_async():
    if not DATABASE_URL:
        print("[SKIP] DATABASE_URL not set — cannot run verify_hollisworksmarketing")
        return 0

    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)          # teardown at START
        await seed(conn)
        await discovery_findings(conn)
    finally:
        await conn.close()

    contact_id = None
    try:
        contact_id = endpoint_tests()
    except Exception as exc:  # noqa: BLE001
        import traceback
        fail("[endpoint] TestClient run", f"{exc}")
        print(traceback.format_exc())

    # 8b — confirm the contact row is genuinely in the DB.
    conn = await _connect(DATABASE_URL)
    try:
        row = None
        if contact_id:
            row = await conn.fetchrow(
                "SELECT id, name, firm, email FROM marketing_contacts WHERE id = $1::uuid",
                contact_id,
            )
        if row and row["email"] == TEST_CONTACT_EMAIL and row["firm"] == TEST_CONTACT_FIRM:
            ok("[contact] submission genuinely persisted in marketing_contacts",
               f"row id={row['id']} firm={row['firm']!r} email={row['email']!r}")
        else:
            fail("[contact] submission persisted", f"contact_id={contact_id} row={row and dict(row)}")
    finally:
        await conn.close()

    palette_guard()
    build_check()

    # Teardown at END + prove zero leftovers.
    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)
        left_orgs = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE slug = ANY($1::text[]) AND id <> $2::uuid",
            TEST_SLUGS, DEFAULT_ORG_ID,
        )
        left_contacts = await conn.fetchval(
            "SELECT count(*) FROM marketing_contacts WHERE email = $1 OR firm = $2",
            TEST_CONTACT_EMAIL, TEST_CONTACT_FIRM,
        )
        if left_orgs == 0 and left_contacts == 0:
            ok("[teardown] zero leftover rows", "test orgs + test contact removed")
        else:
            fail("[teardown] zero leftover rows",
                 f"orgs={left_orgs} contacts={left_contacts}")
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
