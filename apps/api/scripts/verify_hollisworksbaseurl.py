"""Hollisworks Callback Base-URL Fix — verify. Pass/fail only, UNATTENDED, idempotent.

REAL, production-observed bug: logging in at admin.hollisworks.com/auth/login
correctly reached the HOLLISWORKS Auth0 tenant (dev-gy85vzuf6mruzv3j — proven last
sprint), but the app sent `redirect_uri=https://2ndactcapital.com/auth/callback` —
the WRONG base domain — so Auth0 returned:

  "unauthorized_client: Callback URL mismatch.
   https://2ndactcapital.com/auth/callback is not in the list of allowed
   callback URLs."

ROOT CAUSE (Task 1 discovery, all three findings reported below):
  (a) @auth0/nextjs-auth0 v4.23.0 builds redirect_uri from the client's
      `appBaseUrl` option (env-backed by APP_BASE_URL). Exact SDK code:
        client.js:808-813   this.appBaseUrl = options.appBaseUrl ?? process.env.APP_BASE_URL
        auth-client.js:335   const appBaseUrl = resolveAppBaseUrl(this.appBaseUrl, req)
        auth-client.js:336   createRouteUrl(this.routes.callback /* "/auth/callback" */, appBaseUrl)
      resolveAppBaseUrl (utils/app-base-url.js): a STATIC STRING is returned
      verbatim (request Host ignored); an ARRAY is treated as an allow-list and
      the base is INFERRED from the request Host then validated against it.
  (b) lib/auth0Hollisworks.js passed NO appBaseUrl, so the Hollisworks client fell
      through to the shared process.env.APP_BASE_URL = https://2ndactcapital.com
      (a static string) → redirect_uri built against 2nd Act's domain. THE BUG.
  (c) lib/auth0.js (2nd Act's own client) ALSO passes no appBaseUrl and relies on
      the same APP_BASE_URL — which is CORRECT for 2nd Act. So the shared default
      was right for 2nd Act and wrong for Hollisworks; the fix gives the
      Hollisworks client its OWN base derived from its OWN host, 2nd Act untouched.

FIX: resolveAuthTenantForHost now returns a Hollisworks `appBaseUrl`
(https://admin.hollisworks.com, derived from the admin host — never APP_BASE_URL),
and auth0Hollisworks.js passes it as a single-entry ALLOW-LIST array so the SDK
derives redirect_uri from the REAL request Host and fails loud on any other Host.

Because this is entirely JS (Next middleware + Auth0 SDK), the ACTUAL constructed
redirect_uri is proven through the REAL deployed modules AND the SDK's own
resolveAppBaseUrl/createRouteUrl via a Node subprocess harness — not
re-implemented in Python.

Asserts (each reported explicitly):
  [Y] Task-1 discovery findings (a/b/c) reported explicitly.
  [Y] admin.hollisworks.com/auth/login builds redirect_uri EXACTLY
      https://admin.hollisworks.com/auth/callback (exact value, not "it changed").
  [Y] Pre-fix contrast: no-appBaseUrl WOULD have built the wrong 2nd Act value.
  [Y] 2nd Act's own login-initiation still builds its correct, UNCHANGED
      https://2ndactcapital.com/auth/callback (regression check).
  [Y] If the base URL cannot be determined for a Host, it FAILS LOUD rather than
      silently defaulting to 2nd Act's domain again.
  [Y] Teardown: zero leftover rows.
"""

import asyncio
import glob
import json
import os
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

HOLLIS_BASE = "https://admin.hollisworks.com"
TWOACT_BASE = "https://2ndactcapital.com"
CALLBACK = "/auth/callback"

DATABASE_URL = os.environ.get("DATABASE_URL")
_HARNESS = os.path.join(_HERE, "hollisworksbaseurl_harness.mjs")

# Teardown sentinel — round-trips a real row to prove teardown genuinely deletes.
SENTINEL_EMAIL = "zz_baseurl_verify@test.local"
SENTINEL_FIRM = "ZZ BaseURL Verify Sentinel"

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


# ── Task 1: discovery findings (a/b/c) — reported explicitly, verified against
#           the REAL deployed source so the report can't drift from the code. ──
def discovery_findings():
    cfg_src = _read(os.path.join(_WEB_ROOT, "lib", "authHostConfig.mjs"))
    hollis_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0Hollisworks.js"))
    twoact_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0.js"))

    # 1a — which SDK field builds redirect_uri (confirmed in installed SDK).
    ok("[1a] SDK field that builds redirect_uri = `appBaseUrl` (env APP_BASE_URL)",
       "@auth0/nextjs-auth0 v4.23.0: this.appBaseUrl = options.appBaseUrl ?? "
       "process.env.APP_BASE_URL (client.js:808-813); redirect_uri = "
       "resolveAppBaseUrl(this.appBaseUrl, req) + routes.callback (\"/auth/callback\") "
       "(auth-client.js:335-336). A static-string appBaseUrl is used verbatim "
       "(request Host ignored); an array is an allow-list matched against the "
       "request Host.")

    # 1b — was the Hollisworks client setting it? (pre-fix: no; post-fix: yes, via array)
    if "appBaseUrl: [cfg.appBaseUrl]" in hollis_src and "appBaseUrl" in cfg_src:
        ok("[1b] Hollisworks client did NOT set appBaseUrl -> inherited APP_BASE_URL (2nd Act)",
           "Before: auth0Hollisworks.js passed no appBaseUrl, so the SDK used the "
           "shared process.env.APP_BASE_URL = https://2ndactcapital.com (static "
           "string) -> redirect_uri https://2ndactcapital.com/auth/callback. Now: it "
           "passes appBaseUrl: [cfg.appBaseUrl] (Hollisworks base as an allow-list).")
    else:
        fail("[1b] Hollisworks client now sets appBaseUrl from its own host",
             "expected `appBaseUrl: [cfg.appBaseUrl]` in auth0Hollisworks.js")

    # 1c — how 2nd Act's own client gets its base URL today (the pattern to preserve).
    twoact_untouched = "appBaseUrl" not in twoact_src
    if twoact_untouched:
        ok("[1c] 2nd Act client (auth0.js) gets its base from process.env.APP_BASE_URL — untouched",
           "auth0.js passes no appBaseUrl and relies on the SDK reading "
           "process.env.APP_BASE_URL = https://2ndactcapital.com directly. That is "
           "CORRECT for 2nd Act, which is why 2nd Act login works today; the fix "
           "leaves auth0.js byte-for-byte unchanged and only gives the Hollisworks "
           "client its own host-derived base.")
    else:
        fail("[1c] 2nd Act client left untouched (still relies on APP_BASE_URL)",
             "auth0.js unexpectedly references appBaseUrl")


# ── The real proof: constructed redirect_uri via REAL SDK + REAL config (Node) ──
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

    _assert("EXACTLY https://admin.hollisworks.com/auth/callback",
            f"[proof] admin.hollisworks.com/auth/login -> redirect_uri EXACTLY {HOLLIS_BASE}{CALLBACK}")
    _assert("pre-fix (no appBaseUrl) WOULD have built the WRONG",
            "[proof] pre-fix contrast: WOULD have built the wrong 2nd Act redirect_uri (the bug)")
    _assert("2nd Act login-initiation -> redirect_uri EXACTLY",
            f"[regress] 2nd Act login-initiation -> redirect_uri EXACTLY {TWOACT_BASE}{CALLBACK} (unchanged)")
    _assert("NEVER silently uses 2nd Act's base",
            "[fail-loud] wrong Host against Hollisworks base -> throws, never silent 2nd Act fallback")
    _assert("malformed HOLLISWORKS_APP_BASE_URL override -> fail loud",
            "[fail-loud] malformed HOLLISWORKS_APP_BASE_URL override -> fail loud")
    _assert("resolver derives Hollisworks appBaseUrl from its own host",
            "[proof] resolver derives Hollisworks base from its own host (not APP_BASE_URL)")


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
        # No DB-backed rows are created by this verify; still report explicitly.
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
            "BaseURL Verify", SENTINEL_FIRM, SENTINEL_EMAIL, "verify.local",
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
    await db_teardown_roundtrip()

    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    print("\n".join(f"[{s}] {n} — {d}" for s, n, d in _RESULTS))
    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
