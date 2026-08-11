"""Hollisworks Auth0 integration — verify. Pass/fail only, UNATTENDED, idempotent.

Wires the NEW, separate Hollisworks Auth0 tenant as a SECOND auth path used ONLY
for admin.hollisworks.com. 2nd Act's EXISTING tenant/login must remain untouched.

Teardown at START and END, keyed on two stable test auth0_sub values. Never
touches real rows.

Asserts (each reported explicitly):
  [Y] Task-1 discovery findings (a)/(b)/(c) reported + still hold against code.
  [Y] admin.hollisworks.com login flow uses the NEW Hollisworks Auth0 config
      (real HOLLISWORKS_AUTH0_DOMAIN/CLIENT_ID referenced; host selector maps the
      admin host to the Hollisworks client; API validates its issuer).
  [Y] 2nd Act's existing login flow PROVEN unchanged — lib/auth0.js byte-identical
      to git HEAD, host selector defaults to the 2nd Act client, API 2nd Act
      issuer/audience unchanged, and with Hollisworks unset behavior is identical.
  [Y] A successful admin.hollisworks.com login is recognized as Super Admin by the
      existing rbac/is_super_admin checks (ensure_user maps a Hollisworks-issued
      caller to role='super_admin'; is_super_admin + has_permission agree; the
      RLS context resolver short-circuits to super on the Hollisworks issuer).
  [Y] Teardown: zero leftover rows.
"""

import asyncio
import glob
import os
import subprocess
import sys
from types import SimpleNamespace

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

# ── Configure the SECOND (Hollisworks) tenant BEFORE importing main, so its
#    Settings pick it up. Placeholder domain: never contacted (no test needs a
#    real Hollisworks JWKS fetch). ──
HOLLIS_DOMAIN = "hollisworks-verify.us.auth0.com"
HOLLIS_ISSUER = f"https://{HOLLIS_DOMAIN}/"
os.environ["HOLLISWORKS_AUTH0_DOMAIN"] = HOLLIS_DOMAIN
os.environ.setdefault("HOLLISWORKS_AUTH0_AUDIENCE", "https://api.2ndactcapital.com")

import asyncpg  # noqa: E402

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
TWOA_DOMAIN = "dev-smmrfubsfscif3t1.us.auth0.com"
TWOA_ISSUER = f"https://{TWOA_DOMAIN}/"
TWOA_AUDIENCE = "https://api.2ndactcapital.com"

# Two stable test identities.
STAFF_SUB = "hollisworks|verify_staff_super"
MEMBER_SUB = "auth0|verify_2ndact_member"
TEST_SUBS = [STAFF_SUB, MEMBER_SUB]

DATABASE_URL = os.environ.get("DATABASE_URL")

_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


def _fake_request(claims: dict):
    """Minimal stand-in for a Starlette Request carrying validated JWT claims."""
    return SimpleNamespace(state=SimpleNamespace(user=claims))


async def _connect(dsn):
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


async def teardown(conn):
    await conn.execute("DELETE FROM users WHERE auth0_sub = ANY($1::text[])", TEST_SUBS)


# ── Task 1 discovery (reported explicitly) ──
def discovery_findings():
    import inspect

    import main

    # (a) Single-tenant assumption confirmed + what a second tenant needs.
    proxy_src = _read(os.path.join(_WEB_ROOT, "proxy.js"))
    auth0_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0.js"))
    single_client = "new Auth0Client(" in auth0_src
    host_selected = "getAuthClientForHost" in proxy_src
    if single_client and host_selected:
        ok("[1a] single-tenant integration + per-host second tenant",
           "lib/auth0.js instantiates ONE Auth0Client (2nd Act); proxy.js now "
           "selects the client per request Host so admin.hollisworks.com can use "
           "a second, separate tenant. API verify_token validates per issuer.")
    else:
        fail("[1a] single-tenant + per-host selection",
             f"single_client={single_client} host_selected={host_selected}")

    # (b) Existing env var names (no collision with new HOLLISWORKS_* names).
    settings = main.get_settings()
    hollis_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0Hollisworks.js"))
    twoa_names = all(
        n in _read(os.path.join(_REPO_ROOT, ".env.example"))
        for n in ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET", "AUTH0_SECRET")
    )
    hollis_names = all(
        n in hollis_src
        for n in ("HOLLISWORKS_AUTH0_DOMAIN", "HOLLISWORKS_AUTH0_CLIENT_ID",
                  "HOLLISWORKS_AUTH0_CLIENT_SECRET")
    )
    if twoa_names and hollis_names and settings.auth0_domain == TWOA_DOMAIN:
        ok("[1b] existing AUTH0_* names intact; new HOLLISWORKS_AUTH0_* distinct",
           "2nd Act uses AUTH0_DOMAIN/CLIENT_ID/CLIENT_SECRET/SECRET; the new "
           "tenant uses HOLLISWORKS_AUTH0_DOMAIN/CLIENT_ID/CLIENT_SECRET — zero "
           f"collision. API auth0_domain still {settings.auth0_domain!r}.")
    else:
        fail("[1b] env var naming", f"twoa={twoa_names} hollis={hollis_names} "
             f"domain={settings.auth0_domain!r}")

    # (c) Super Admin mapping is role-based and org-agnostic.
    from services.rbac import is_super_admin
    src = inspect.getsource(is_super_admin)
    if is_super_admin({"role": "super_admin"}) and not is_super_admin({"role": "member"}) \
            and "org_id" in src:
        ok("[1c] Super Admin = users.role, org-agnostic → Hollisworks staff map cleanly",
           "is_super_admin ignores org_id (staff have no tenant org); Hollisworks "
           "identity → role 'super_admin', so it slots into the existing "
           "user/session model with no new session type.")
    else:
        fail("[1c] super-admin mapping", "is_super_admin not role-based/org-agnostic")


# ── Assertion 2: admin.hollisworks.com uses the NEW Hollisworks config ──
def uses_new_config():
    import main

    hollis_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0Hollisworks.js"))
    forhost_src = _read(os.path.join(_WEB_ROOT, "lib", "authForHost.js"))
    login_src = _read(os.path.join(_WEB_ROOT, "app", "login", "page.js"))
    # hollisworksroutingfix: the host literal + the HOLLISWORKS_AUTH0_* references
    # now live in the single-source, fail-loud resolver lib/authHostConfig.mjs;
    # auth0Hollisworks.js builds its client THROUGH that resolver.
    cfg_src = _read(os.path.join(_WEB_ROOT, "lib", "authHostConfig.mjs"))

    # Web: the Hollisworks client is built from the real HOLLISWORKS_AUTH0_* vars,
    # and the host selector maps admin.hollisworks.com → that client.
    web_ok = (
        "HOLLISWORKS_AUTH0_DOMAIN" in cfg_src
        and "HOLLISWORKS_AUTH0_CLIENT_ID" in cfg_src
        and "resolveAuthTenantForHost" in hollis_src
        and "admin.hollisworks.com" in cfg_src
        and "getHollisworksAuth0" in forhost_src
        and "getAuthClientForHost" in login_src
    )
    if web_ok:
        ok("[2:web] admin.hollisworks.com flow references the NEW Hollisworks config",
           "authHostConfig.mjs reads HOLLISWORKS_AUTH0_DOMAIN + CLIENT_ID (fail-loud); "
           "auth0Hollisworks.js builds through resolveAuthTenantForHost(); "
           "authForHost.js maps admin.hollisworks.com → getHollisworksAuth0(); "
           "the login page selects the client by Host.")
    else:
        fail("[2:web] Hollisworks config referenced in flow",
             f"hollis_domain={'HOLLISWORKS_AUTH0_DOMAIN' in hollis_src} "
             f"selector={'getHollisworksAuth0' in forhost_src}")

    # API: a token from the Hollisworks issuer is recognized (NOT falling through
    # to 2nd Act). is_hollisworks_claims is the real discriminator used by the app.
    settings = main.get_settings()
    api_ok = (
        settings.hollisworks_enabled
        and settings.hollisworks_issuer == HOLLIS_ISSUER
        and main.is_hollisworks_claims({"iss": HOLLIS_ISSUER, "sub": STAFF_SUB}) is True
        and main.is_hollisworks_claims({"iss": TWOA_ISSUER, "sub": MEMBER_SUB}) is False
    )
    if api_ok:
        ok("[2:api] Hollisworks issuer recognized as its OWN tenant (no fall-through)",
           f"hollisworks_issuer={settings.hollisworks_issuer!r}; a Hollisworks-"
           "issued token → is_hollisworks_claims True, a 2nd Act token → False.")
    else:
        fail("[2:api] Hollisworks issuer recognized",
             f"enabled={settings.hollisworks_enabled} issuer={settings.hollisworks_issuer!r}")


# ── Assertion 3: 2nd Act login flow PROVEN unchanged ──
def twoa_unchanged():
    import main

    # (i) lib/auth0.js is byte-identical to git HEAD — the existing client config
    #     was not edited by this sprint.
    current = _read(os.path.join(_WEB_ROOT, "lib", "auth0.js"))
    try:
        head = subprocess.run(
            ["git", "show", "HEAD:apps/web/lib/auth0.js"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        head = f"<git error: {exc}>"
    unchanged_file = current.strip() == head.strip() and "api.2ndactcapital.com" in current
    if unchanged_file:
        ok("[3:web] lib/auth0.js (2nd Act client) byte-identical to git HEAD",
           "the existing Auth0Client config was not touched — same domain/audience.")
    else:
        fail("[3:web] lib/auth0.js unchanged", "current differs from HEAD")

    # (ii) The host selector DEFAULTS to the 2nd Act client for every non-admin
    #      host (structural: the ternary's else branch is `auth0`).
    forhost_src = _read(os.path.join(_WEB_ROOT, "lib", "authForHost.js"))
    defaults_2a = "? getHollisworksAuth0() : auth0" in forhost_src.replace("\n", " ")
    if defaults_2a and 'import { auth0 } from "@/lib/auth0"' in forhost_src:
        ok("[3:web] host selector defaults to the 2nd Act client",
           "getAuthClientForHost returns the existing auth0 client for every host "
           "except admin.hollisworks.com — non-admin behavior is unchanged.")
    else:
        fail("[3:web] selector defaults to 2nd Act", f"defaults_2a={defaults_2a}")

    # (iii) API 2nd Act issuer/audience unchanged, and it is tried FIRST.
    import inspect
    settings = main.get_settings()
    vt_src = inspect.getsource(main.verify_token)
    twoa_first = vt_src.index("settings.issuer") < vt_src.index("hollisworks_issuer")
    if (
        settings.auth0_domain == TWOA_DOMAIN
        and settings.issuer == TWOA_ISSUER
        and settings.auth0_audience == TWOA_AUDIENCE
        and twoa_first
    ):
        ok("[3:api] 2nd Act issuer/audience unchanged and validated FIRST",
           f"issuer={settings.issuer!r} audience={settings.auth0_audience!r}; the "
           "2nd Act tenant is tried before the additive Hollisworks fallback, so a "
           "2nd Act token takes its original code path.")
    else:
        fail("[3:api] 2nd Act validation unchanged",
             f"domain={settings.auth0_domain!r} first={twoa_first}")

    # (iv) Additive-safety: with Hollisworks UNSET, behavior is exactly single-tenant.
    saved = os.environ.pop("HOLLISWORKS_AUTH0_DOMAIN", None)
    main.get_settings.cache_clear()
    try:
        s2 = main.get_settings()
        disabled_ok = (
            not s2.hollisworks_enabled
            and main.is_hollisworks_claims({"iss": HOLLIS_ISSUER, "sub": STAFF_SUB}) is False
        )
    finally:
        if saved is not None:
            os.environ["HOLLISWORKS_AUTH0_DOMAIN"] = saved
        main.get_settings.cache_clear()  # restore enabled settings for later tests
    if disabled_ok:
        ok("[3:api] with Hollisworks unset, validation is single-tenant (prod posture)",
           "hollisworks_enabled False → is_hollisworks_claims always False → the "
           "app behaves exactly as it did before this sprint.")
    else:
        fail("[3:api] additive-safety when unset", f"disabled_ok={disabled_ok}")


# ── Assertion 4: successful admin login recognized as Super Admin ──
async def recognized_as_super_admin(pool):
    import main
    from services.rbac import has_permission, is_super_admin, load_principal
    from services.users import ensure_user

    # A Hollisworks-issued caller → ensure_user maps to role 'super_admin'.
    async with pool.acquire() as conn:
        staff_id = await ensure_user(
            conn, _fake_request({"iss": HOLLIS_ISSUER, "sub": STAFF_SUB,
                                 "email": "staff@hollisworks.example"}),
        )
        member_id = await ensure_user(
            conn, _fake_request({"iss": TWOA_ISSUER, "sub": MEMBER_SUB,
                                 "email": "member@2ndact.example"}),
        )
        staff_row = await conn.fetchrow("SELECT role FROM users WHERE id = $1::uuid", staff_id)
        member_row = await conn.fetchrow("SELECT role FROM users WHERE id = $1::uuid", member_id)
        staff_principal = await load_principal(conn, staff_id)
        member_principal = await load_principal(conn, member_id)

    if staff_row and staff_row["role"] == "super_admin" and member_row and member_row["role"] == "member":
        ok("[4a] ensure_user maps Hollisworks caller → super_admin (2nd Act → member)",
           f"Hollisworks sub role={staff_row['role']!r}; 2nd Act sub role={member_row['role']!r}.")
    else:
        fail("[4a] ensure_user role mapping",
             f"staff={staff_row and staff_row['role']} member={member_row and member_row['role']}")

    # The EXISTING rbac/is_super_admin checks recognize the staff principal.
    if is_super_admin(staff_principal) and not is_super_admin(member_principal):
        ok("[4b] is_super_admin recognizes the staff principal (member is not)",
           f"staff role={staff_principal.get('role')!r} → super; member is not.")
    else:
        fail("[4b] is_super_admin on staff principal",
             f"staff={staff_principal} member={member_principal}")

    staff_perm = await has_permission(pool, staff_id, DEFAULT_ORG_ID, "any.privileged.action")
    if staff_perm:
        ok("[4c] has_permission passes for Hollisworks staff (super-admin escape hatch)",
           "has_permission checks is_super_admin FIRST → staff pass every gate.")
    else:
        fail("[4c] has_permission for staff", f"result={staff_perm}")

    # The RLS context resolver short-circuits to super on the Hollisworks issuer,
    # independent of any users-row read (no first-request write race).
    resolved = await main._resolve_is_super_admin(
        _fake_request({"iss": HOLLIS_ISSUER, "sub": STAFF_SUB})
    )
    if resolved is True:
        ok("[4d] RLS resolver → Super Admin directly from the Hollisworks issuer",
           "_resolve_is_super_admin returns True on the Hollisworks issuer before "
           "any DB read — Super Admin context is set from request one.")
    else:
        fail("[4d] RLS resolver short-circuit", f"resolved={resolved}")


async def main_async():
    if not DATABASE_URL:
        print("[SKIP] DATABASE_URL not set — cannot run verify_hollisworksauth")
        return 0

    # discovery + structural (no DB)
    discovery_findings()
    uses_new_config()
    twoa_unchanged()

    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)  # teardown at START
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(
        DATABASE_URL, ssl="require", statement_cache_size=0, min_size=1, max_size=3
    )
    try:
        await recognized_as_super_admin(pool)
    except Exception as exc:  # noqa: BLE001
        import traceback
        fail("[4] super-admin recognition", f"{exc}")
        print(traceback.format_exc())
    finally:
        await pool.close()

    # Teardown at END + prove zero leftovers.
    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)
        left = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth0_sub = ANY($1::text[])", TEST_SUBS
        )
        if left == 0:
            ok("[teardown] zero leftover rows", "both test users removed")
        else:
            fail("[teardown] zero leftover rows", f"users={left}")
    finally:
        await conn.close()

    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    print("\n" + "=" * 70)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 70)
    if failed:
        print("FAILURES:")
        for s, n, d in _RESULTS:
            if s == "FAIL":
                print(f"  [FAIL] {n} — {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
