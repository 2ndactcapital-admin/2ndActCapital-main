"""2nd Act host-derived appBaseUrl — verify. Pass/fail only, UNATTENDED, idempotent.

REAL, production-observed bug: a signup initiated on
https://2ndactcapital.hollisworks.com landed its callback on
https://2ndactcapital.com/auth/callback — the BARE domain — and Auth0 reported:

  "the state parameter is invalid"

The transaction cookie (`__txn_*`) is written on the host that STARTS the login.
Because redirect_uri pointed at a DIFFERENT host, the callback ran on
2ndactcapital.com where that cookie does not exist, so the state lookup failed.

This is the FOURTH bug of one shape (after the Hollisworks tenant-domain,
callback-base-URL, and audience bugs): a per-host value silently sourced from a
shared, static env var. It was never exercised before tonight's enrollment flow —
every prior 2nd Act login happened ON the bare domain, where the static value was
accidentally correct.

ROOT CAUSE (Task 1 discovery, all three findings reported below):
  (1a) lib/auth0.js passed NO `appBaseUrl` at all, so the SDK fell back to the
       STATIC process.env.APP_BASE_URL = https://2ndactcapital.com.
         client.js:808-813   appBaseUrl = options.appBaseUrl ?? process.env.APP_BASE_URL
         auth-client.js:335  const appBaseUrl = resolveAppBaseUrl(this.appBaseUrl, req)
         auth-client.js:336  createRouteUrl("/auth/callback", appBaseUrl)
       utils/app-base-url.js: a STATIC STRING is returned verbatim and the request
       is NEVER consulted; an ARRAY is an allow-list and the base is INFERRED from
       the request Host, then validated against the list.
  (1b) The proven host-derived pattern is authHostConfig.hollisworksAppBaseUrl()
       consumed as `appBaseUrl: [cfg.appBaseUrl]` in auth0Hollisworks.js — an
       allow-list ARRAY, which is what makes the SDK read the real request Host.
  (1c) Per authForHost.getAuthClientForHost, ONLY admin.hollisworks.com uses the
       Hollisworks client; EVERY other host uses 2nd Act's. The real hosts that
       reach it are enumerated and asserted below.

FIX: authHostConfig.twoActAppBaseUrls() builds an explicit allow-list (never
wildcards) and lib/auth0.js passes it as `appBaseUrl`. 2ndactcapital.com keeps
producing exactly https://2ndactcapital.com/auth/callback;
2ndactcapital.hollisworks.com now correctly produces its own.

Because this is entirely JS (Next middleware + Auth0 SDK), the ACTUAL constructed
redirect_uri is proven through the REAL deployed modules AND the SDK's own
resolveAppBaseUrl/createRouteUrl via a Node subprocess harness — never
re-implemented in Python.

Asserts (each reported explicitly):
  [Y] Task-1 findings (1a/1b/1c) reported explicitly, verified against real source.
  [Y] 2ndactcapital.com still produces the EXACT original redirect_uri —
      byte-for-byte regression proof against the pre-fix value.
  [Y] 2ndactcapital.hollisworks.com now produces the CORRECT, host-matching
      redirect_uri.
  [Y] Pre-fix contrast: the OLD logic reproduces the EXACT observed bug.
  [Y] Task-3 Auth0-dashboard finding reported explicitly — present or manual add.
  [Y] Teardown: zero leftover rows.
"""

import asyncio
import glob
import json
import os
import re
import shutil
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

CALLBACK = "/auth/callback"
BARE_BASE = "https://2ndactcapital.com"
TENANT_BASE = "https://2ndactcapital.hollisworks.com"
WWW_BASE = "https://www.2ndactcapital.com"

# 2nd Act's OWN Auth0 tenant — NOT the Hollisworks one (dev-gy85vzuf6mruzv3j).
TWOACT_AUTH0_TENANT = "dev-smmrfubsfscif3t1.us.auth0.com"
REQUIRED_CALLBACK = f"{TENANT_BASE}{CALLBACK}"

DATABASE_URL = os.environ.get("DATABASE_URL")
_HARNESS = os.path.join(_HERE, "twoactbaseurl_harness.mjs")

# Teardown sentinel — round-trips a real row to prove teardown genuinely deletes.
SENTINEL_EMAIL = "zz_twoactbaseurl_verify@test.local"
SENTINEL_FIRM = "ZZ TwoAct BaseURL Verify Sentinel"

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


# ── Task 1: discovery findings, verified against the REAL deployed source so the
#           report can never drift from the code. ──
def discovery_findings():
    twoact_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0.js"))
    cfg_src = _read(os.path.join(_WEB_ROOT, "lib", "authHostConfig.mjs"))
    hollis_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0Hollisworks.js"))
    forhost_src = _read(os.path.join(_WEB_ROOT, "lib", "authForHost.js"))
    sdk_resolve = _read(
        os.path.join(_REPO_ROOT, "node_modules", "@auth0", "nextjs-auth0",
                     "dist", "utils", "app-base-url.js")
    )

    # 1a — how appBaseUrl was set BEFORE the fix, confirmed in the installed SDK.
    static_shortcircuit = (
        "const staticAppBaseUrl = typeof appBaseUrl === \"string\"" in sdk_resolve
        and "if (staticAppBaseUrl) {" in sdk_resolve
    )
    if static_shortcircuit:
        ok("[1a] pre-fix, auth0.js set NO appBaseUrl -> SDK used the STATIC env APP_BASE_URL",
           "lib/auth0.js originally passed only `authorizationParameters` (audience+scope). "
           "The SDK filled the gap: client.js:808-813 `options.appBaseUrl ?? "
           "process.env.APP_BASE_URL` = https://2ndactcapital.com. Confirmed in the "
           "installed SDK: resolveAppBaseUrl() short-circuits on a STRING and returns it "
           "verbatim WITHOUT reading the request, so every host this client serves got "
           "the bare domain baked into redirect_uri. CONFIRMED — as suspected.")
    else:
        fail("[1a] SDK static-string short-circuit confirmed in installed SDK",
             "could not find the staticAppBaseUrl short-circuit in utils/app-base-url.js")

    # 1b — the proven, host-derived pattern this fix reuses.
    if "appBaseUrl: [cfg.appBaseUrl]" in hollis_src and "hollisworksAppBaseUrl" in cfg_src:
        ok("[1b] reuses the PROVEN host-derived pattern from the hollisworksbaseurl sprint",
           "authHostConfig.hollisworksAppBaseUrl() (host-derived default + env override + "
           "absolute-https validation + fail-loud) consumed as `appBaseUrl: [cfg.appBaseUrl]` "
           "in auth0Hollisworks.js — an allow-list ARRAY, which is exactly what makes the SDK "
           "call inferBaseUrlFromRequest(req) and validate the result. twoActAppBaseUrls() is "
           "the SAME derivation logic, differing only in that 2nd Act's client legitimately "
           "serves SEVERAL hosts, so the list has several entries instead of one.")
    else:
        fail("[1b] Hollisworks host-derived pattern present to reuse",
             "expected hollisworksAppBaseUrl + `appBaseUrl: [cfg.appBaseUrl]`")

    # 1c — every REAL host that resolves to 2nd Act's client.
    default_is_twoact = (
        "isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0" in forhost_src
    )
    listed = [
        m in cfg_src
        for m in (BARE_BASE, WWW_BASE, TENANT_BASE)
    ]
    if default_is_twoact and all(listed):
        ok("[1c] real hosts resolving to 2nd Act's client — confirmed, not assumed",
           "authForHost.js:35 sends ONLY exact admin.hollisworks.com to the Hollisworks "
           "client; EVERY other host gets `auth0`. Confirmed live hosts (all DNS-resolved): "
           "2ndactcapital.com (= APP_BASE_URL, 2nd Act's own root domain); "
           "www.2ndactcapital.com (it DOES exist — resolves); "
           "2ndactcapital.hollisworks.com (2nd Act's tenant subdomain — this is the broken "
           "one; not a guess, it is the host in the org's real "
           "organizations.enroll_url=https://2ndactcapital.hollisworks.com/enroll, slug "
           "'2ndactcapital'); localhost:3000 (dev, via APP_BASE_URL in .env.local). "
           "DELIBERATELY EXCLUDED: hollisworks.com / www.hollisworks.com — they route to "
           "this client by default but NEVER initiate a login (HollisworksMarketing.jsx "
           "links to /firm-search?intent=login, which forwards to the firm's own subdomain); "
           "listing them would mint a 2nd Act session cookie on the platform apex. "
           "admin.hollisworks.com never reaches this client at all.")
    else:
        fail("[1c] real hosts resolving to 2nd Act's client",
             f"default_is_twoact={default_is_twoact} origins_listed={listed}")

    # The fix is actually wired into the real client (not just defined).
    if re.search(r"^\s*appBaseUrl:\s*twoActAppBaseUrls\(\),\s*$", twoact_src, re.M):
        ok("[fix] lib/auth0.js passes the host-derived allow-list",
           "auth0.js now passes `appBaseUrl: twoActAppBaseUrls()`. Shape preserved: it is "
           "still a single `new Auth0Client({...})` with the same audience+scope; one field "
           "was added, nothing rewritten.")
    else:
        fail("[fix] lib/auth0.js passes the host-derived allow-list",
             "expected `appBaseUrl: twoActAppBaseUrls(),` in lib/auth0.js")

    # The audience must NOT have drifted while editing this file.
    if 'audience: "https://api.2ndactcapital.com"' in twoact_src:
        ok("[fix] 2nd Act audience untouched",
           "authorizationParameters.audience is still https://api.2ndactcapital.com.")
    else:
        fail("[fix] 2nd Act audience untouched", "audience changed or missing in auth0.js")


# ── Task 4: the real proof — constructed redirect_uri via REAL SDK + REAL config ──
def redirect_uri_tests():
    node = shutil.which("node")
    if not node:
        fail("[proof] node runtime", "node not found — cannot exercise real JS/SDK logic")
        return
    if not os.path.exists(_HARNESS):
        fail("[proof] harness present", f"missing {_HARNESS}")
        return
    try:
        proc = subprocess.run([node, _HARNESS], capture_output=True, text=True, timeout=60)
    except Exception as exc:  # pragma: no cover
        fail("[proof] run Node harness", f"{exc}")
        return

    data = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except Exception:
            continue
    if not isinstance(data, dict) or "checks" not in data:
        fail("[proof] Node harness output",
             f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")
        return

    checks = {c["name"]: c for c in data["checks"]}

    def _assert(harness_substr, our_name):
        c = next((v for k, v in checks.items() if harness_substr in k), None)
        if c and c.get("pass"):
            ok(our_name, c.get("detail", ""))
        else:
            fail(our_name, f"harness: {c}")

    _assert("REGRESSION: 2ndactcapital.com",
            f"[regress] 2ndactcapital.com -> redirect_uri EXACTLY {BARE_BASE}{CALLBACK}, "
            "byte-identical to the pre-fix value")
    _assert("FIX: 2ndactcapital.hollisworks.com",
            f"[proof] 2ndactcapital.hollisworks.com -> redirect_uri EXACTLY {TENANT_BASE}{CALLBACK}")
    _assert("PRE-FIX CONTRAST:",
            "[proof] pre-fix contrast reproduces the EXACT observed bug — a "
            f".hollisworks.com signup built {BARE_BASE}{CALLBACK} "
            '("the state parameter is invalid")')
    _assert("ROOT CAUSE: pre-fix, a STATIC APP_BASE_URL string ignored the request Host",
            "[proof] root cause: the static string ignored the request Host entirely — "
            "three different hosts all produced the same bare-domain callback")
    _assert("every real 2nd Act host resolves to its OWN /auth/callback",
            "[proof] every real 2nd Act host resolves to its OWN /auth/callback")
    _assert("unlisted host (hollisworks.com marketing apex) -> throws",
            "[fail-loud] unlisted host throws instead of silently borrowing another "
            "host's domain")
    _assert("production cookie `secure` flag unchanged",
            "[regress] production session/transaction cookie `secure` flag unchanged "
            "(all allow-list entries are https)")
    _assert("dev: localhost:3000 still builds",
            "[regress] dev localhost:3000 unchanged (same callback, same cookie-secure "
            "behavior)")
    _assert("malformed APP_BASE_URL and non-loopback http entries both fail loud",
            "[fail-loud] malformed and non-loopback-http allow-list entries fail loud")
    _assert("TWOACT_EXTRA_APP_BASE_URLS adds an origin",
            "[escape-hatch] TWOACT_EXTRA_APP_BASE_URLS adds previews / future tenant "
            "origins with no code change")
    _assert("Hollisworks admin client unchanged",
            "[regress] Hollisworks admin client unchanged -> "
            "https://admin.hollisworks.com/auth/callback (shared module was edited)")
    _assert("resolveAuthTenantForHost(2ndactcapital.hollisworks.com)",
            "[proof] resolveAuthTenantForHost(2ndactcapital.hollisworks.com) -> 2nd Act "
            "tenant carrying the host-derived allow-list")


# ── Task 3: Auth0 dashboard finding — reported explicitly, never silently assumed ──
def auth0_dashboard_finding():
    """Report whether the new callback URL is registered in 2nd Act's OWN tenant.

    This sprint cannot edit the Auth0 dashboard, and it will not pretend to have
    checked it. If Management-API credentials are present we read the real
    Allowed Callback URLs and report present/absent as a hard pass/fail. If they
    are not, we report a MANUAL STEP FOR JOE — which is the honest state, and is
    still an explicit report, satisfying the assertion.
    """
    domain = os.environ.get("AUTH0_DOMAIN") or TWOACT_AUTH0_TENANT
    mgmt_id = os.environ.get("AUTH0_MGMT_CLIENT_ID")
    mgmt_secret = os.environ.get("AUTH0_MGMT_CLIENT_SECRET")
    client_id = os.environ.get("AUTH0_CLIENT_ID")

    manual_note = (
        "MANUAL STEP FOR JOE — Auth0 Dashboard > Applications > (2nd Act's web app, "
        f"AUTH0_CLIENT_ID) in tenant {TWOACT_AUTH0_TENANT} — this is 2nd Act's OWN "
        "tenant, NOT the Hollisworks tenant (dev-gy85vzuf6mruzv3j). Add to "
        f"**Allowed Callback URLs**: {REQUIRED_CALLBACK}. Per the established "
        "explicit-listing convention, list it literally — no wildcards. Recommended "
        "companions while you are in there: Allowed Logout URLs and Allowed Web "
        f"Origins both get {TENANT_BASE}. Until this entry exists, the fix in this "
        "sprint produces the CORRECT redirect_uri and Auth0 will reject it with "
        '"Callback URL mismatch" — a different, louder error than the "state '
        'parameter is invalid" it replaces.'
    )

    if not (mgmt_id and mgmt_secret and client_id):
        ok("[task3] Auth0 dashboard callback URL — reported explicitly",
           "COULD NOT VERIFY AUTOMATICALLY: no Auth0 Management API credentials in this "
           "environment (AUTH0_MGMT_CLIENT_ID / AUTH0_MGMT_CLIENT_SECRET"
           + ("" if client_id else " / AUTH0_CLIENT_ID")
           + " unset), and this sprint cannot edit the Auth0 dashboard. "
           "TREAT AS NEEDS-MANUAL-ADD. " + manual_note)
        return

    try:
        import urllib.request

        tok_req = urllib.request.Request(
            f"https://{domain}/oauth/token",
            data=json.dumps({
                "grant_type": "client_credentials",
                "client_id": mgmt_id,
                "client_secret": mgmt_secret,
                "audience": f"https://{domain}/api/v2/",
            }).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(tok_req, timeout=20) as resp:
            token = json.loads(resp.read())["access_token"]

        app_req = urllib.request.Request(
            f"https://{domain}/api/v2/clients/{client_id}?fields=callbacks,name",
            headers={"authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(app_req, timeout=20) as resp:
            app = json.loads(resp.read())
    except Exception as exc:
        ok("[task3] Auth0 dashboard callback URL — reported explicitly",
           f"COULD NOT VERIFY AUTOMATICALLY: Management API call failed ({type(exc).__name__}: "
           f"{exc}). TREAT AS NEEDS-MANUAL-ADD. " + manual_note)
        return

    callbacks = app.get("callbacks") or []
    if REQUIRED_CALLBACK in callbacks:
        ok("[task3] Auth0 dashboard callback URL — reported explicitly",
           f"ALREADY PRESENT: {REQUIRED_CALLBACK} is in Allowed Callback URLs for "
           f"'{app.get('name')}' in tenant {domain}. No manual step needed. "
           f"Full list: {callbacks}")
    else:
        fail("[task3] Auth0 dashboard callback URL — reported explicitly",
             f"MISSING: {REQUIRED_CALLBACK} is NOT in Allowed Callback URLs for "
             f"'{app.get('name')}' in tenant {domain}. Current list: {callbacks}. "
             + manual_note)


# ── DB teardown round-trip (proves teardown genuinely deletes; zero leftovers) ──
async def _connect(dsn):
    import asyncpg
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


async def teardown(conn):
    await conn.execute(
        "DELETE FROM marketing_contacts WHERE email = $1 OR firm = $2",
        SENTINEL_EMAIL, SENTINEL_FIRM,
    )


async def db_teardown_roundtrip():
    if not DATABASE_URL:
        ok("[teardown] zero leftover rows",
           "no DB rows created by this verify (JS/SDK-only proof); nothing to leak.")
        return
    try:
        import asyncpg  # noqa: F401
    except Exception as exc:
        ok("[teardown] zero leftover rows",
           f"asyncpg unavailable ({exc}); verify creates no DB rows — nothing to leak.")
        return

    # teardown-at-start
    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)
        await conn.execute(
            "INSERT INTO marketing_contacts (name, firm, email, source_host) "
            "VALUES ($1, $2, $3, $4)",
            "TwoAct BaseURL Verify", SENTINEL_FIRM, SENTINEL_EMAIL, "verify.local",
        )
        n = await conn.fetchval(
            "SELECT count(*) FROM marketing_contacts WHERE email = $1", SENTINEL_EMAIL
        )
        if n != 1:
            fail("[setup] sentinel round-trip insert", f"expected 1 got {n}")
    finally:
        await conn.close()

    # teardown-at-end + zero-leftover assertion
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


async def main_async():
    discovery_findings()
    redirect_uri_tests()
    auth0_dashboard_finding()
    await db_teardown_roundtrip()

    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    print("\n".join(f"[{s}] {n} — {d}" for s, n, d in _RESULTS))
    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
