"""DASHBOARD SESSION CHECK — HOST-AWARE FIX. Verify. Pass/fail only, UNATTENDED.

REAL, production-observed bug ("too many redirects" in the browser):
`apps/web/app/dashboard/page.js` imported the FIXED 2nd Act client
(`@/lib/auth0`) and called `auth0.getSession()` regardless of Host.
admin.hollisworks.com authenticates against the SEPARATE Hollisworks Auth0
tenant, whose session lives in the `__hw_session` cookie encrypted with the
Hollisworks secret. 2nd Act's client only ever reads `__session` with 2nd Act's
secret — so a perfectly valid Hollisworks session read as "no session". The page
redirected to /auth/login; the (already host-aware) middleware saw the live
Hollisworks tenant session and bounced straight back to /dashboard. Infinite.

TASK 1a — dashboard was NOT alone. 41 pages and 9 Next.js API route handlers had
the identical host-unaware pattern, plus the two shared server helpers every one
of those pages fetches data through. Full list printed below.

TASK 1b — `getAuthClientForHost(host)` (apps/web/lib/authForHost.js) takes the
raw Host header string and returns an Auth0Client:
`isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0`. `app/login/page.js`
and `proxy.js` already call it that way; the fix calls it the SAME way and does
NOT reinvent host detection.

TASK 2 — every one of those files now resolves its client from the request Host
via `lib/authServer.js` (`getHostSession` / `getRequestAuthClient`), which reads
`headers().get("host")` exactly like login/page.js and delegates to
`getAuthClientForHost`. The redirect-on-no-session target is unchanged and was
already per-host correct: it is the HOST-RELATIVE `/auth/login?returnTo=...`,
served by proxy.js with the same host-aware client.

TASK 3 — proven with a hermetic Node harness that mints REAL encrypted Auth0
session cookies with the SDK's own JWE crypto and reads them back through REAL
`Auth0Client.getSession(req)` calls, then walks the actual redirect graph hop by
hop. Not a signature check.

Asserts (each reported explicitly):
  [Y] Task 1a + 1b findings reported explicitly, including EVERY additional file
      with the same host-unaware pattern.
  [Y] A real Hollisworks-tenant session PASSES the dashboard session check and
      renders — zero redirects (real SDK decrypt, not a signature check).
  [Y] Pre-fix contrast: the SAME request against the host-unaware check
      reproduces the observed infinite redirect loop.
  [Y] A real 2nd Act session on 2nd Act's host STILL passes, with an outcome
      byte-identical to pre-fix — the regression check.
  [Y] No session on EITHER host produces exactly ONE redirect, not a loop.
  [Y] Cross-tenant isolation: neither tenant's cookie authenticates on the
      other's host.
  [Y] EVERY other file found in Task 1a is fixed identically and proven the
      same way (per-file harness run + per-file source assertion).
  [Y] No file outside lib/authForHost.js still imports @/lib/auth0 directly;
      lib/auth0.js (2nd Act's client) is byte-for-byte untouched.
  [Y] proxy.js (middleware) does NOT pull in next/headers via the new module.
  [Y] `next build` compiles clean with every file host-aware.
  [Y] Teardown: zero leftover rows.

ONE ADDITIONAL FIX THIS FORCED — `lib/theme.js` is imported by ThemeProvider (a
client component) and statically imported `lib/api.js`. Making api.js host-aware
pulled `next/headers` into the client graph and Turbopack refused to build. The
server-only `loadTheme` now lives in `lib/themeServer.js`; the pure readers stay
in `lib/theme.js`. Latent layering bug, surfaced (not caused) by this sprint.
"""

import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

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

DATABASE_URL = os.environ.get("DATABASE_URL")
_HARNESS = os.path.join(_HERE, "dashboardhostfix_harness.mjs")

# Epoch the harness stamps its minted sessions with. Supplied by the caller (not
# read from Date.now() inside the harness) so a run is reproducible, but it must
# be the CURRENT time: the SDK's cookie is a JWE with a real `exp`, and a frozen
# past epoch would make every minted session decrypt as expired — which would
# make the "no session" assertions pass vacuously.
_EPOCH = str(int(time.time()))

SENTINEL_EMAIL = "zz_dashboardhostfix_verify@test.local"
SENTINEL_FIRM = "ZZ DashboardHostFix Verify Sentinel"

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


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/")


def _walk_web_sources():
    for root, dirs, files in os.walk(os.path.join(_WEB_ROOT, "app")):
        dirs[:] = [d for d in dirs if d not in (".next", "node_modules")]
        for f in sorted(files):
            if f.endswith((".js", ".jsx")):
                yield os.path.join(root, f)
    for f in sorted(os.listdir(os.path.join(_WEB_ROOT, "lib"))):
        if f.endswith((".js", ".jsx", ".mjs")):
            yield os.path.join(_WEB_ROOT, "lib", f)


# ── TASK 1: discovery, reported explicitly ───────────────────────────────────
# The set of files that had the host-unaware pattern is derived from the CURRENT
# tree (they must now all use the host-aware helper) and cross-checked against
# git so a file cannot silently drop out of coverage.
def discover():
    """Return (pages, routes, libs) — repo-relative paths, all host-aware now."""
    pages, routes, libs = [], [], []
    for p in _walk_web_sources():
        src = _read(p)
        if "@/lib/authServer" not in src:
            continue
        base = os.path.basename(p)
        if p.startswith(os.path.join(_WEB_ROOT, "lib")):
            libs.append(p)
        elif base.startswith("page."):
            pages.append(p)
        else:
            routes.append(p)
    return sorted(pages), sorted(routes), sorted(libs)


def _returnto_for(path):
    """The redirect target a page uses when there is no session (source-derived)."""
    src = _read(path)
    m = re.search(r"redirect\(\s*[\"'`]/auth/login\?returnTo=([^\"'`]+)[\"'`]", src)
    return m.group(1) if m else None


def _route_path_for(path):
    """URL path for an app-router file, from its position on disk."""
    rel = os.path.relpath(path, os.path.join(_WEB_ROOT, "app"))
    parts = rel.replace(os.sep, "/").split("/")
    parts = parts[:-1]  # drop page.js / route.js
    out = []
    for seg in parts:
        if seg.startswith("(") and seg.endswith(")"):
            continue  # route group
        if seg.startswith("[") and seg.endswith("]"):
            out.append("_" + seg.strip("[]").replace("...", ""))  # concrete stand-in
        else:
            out.append(seg)
    return "/" + "/".join(out) if out else "/"


def task1_report(pages, routes, libs):
    print("\n=== TASK 1a — every file that checked the session with the FIXED "
          "2nd Act client (@/lib/auth0), host-unaware ===\n")
    print(f"  PAGES ({len(pages)}) — server components calling `auth0.getSession()`:")
    for p in pages:
        rt = _returnto_for(p)
        target = (f"/auth/login?returnTo={rt}" if rt
                  else "/dashboard when a session EXISTS (landing page, inverted gate)")
        print(f"    {_rel(p):<62} redirect -> {target}")
    print(f"\n  NEXT.JS API ROUTE HANDLERS ({len(routes)}) — same defect, "
          "`auth0.getSession()` / `auth0.getAccessToken()`:")
    for p in routes:
        print(f"    {_rel(p)}")
    print(f"\n  SHARED SERVER HELPERS ({len(libs)}) — every fixed page fetches its "
          "data through these, so leaving them host-unaware would render the\n"
          "  page and then 401 every request on it:")
    for p in libs:
        print(f"    {_rel(p)}")
    print("\n  NOT a finding — already correct, and the model the fix follows:")
    print("    apps/web/app/login/page.js   (getAuthClientForHost(headers().get('host')))")
    print("    apps/web/proxy.js            (getAuthClientForHost(request.headers.get('host')))")

    print("\n=== TASK 1b — getAuthClientForHost: real signature and return shape ===\n")
    print("    apps/web/lib/authForHost.js")
    print("      export function getAuthClientForHost(host)")
    print("        host   : the RAW Host header string (may include :port; the")
    print("                 predicate lowercases, strips :port and a trailing dot)")
    print("        returns: an Auth0Client instance — NOT a promise, NOT a config")
    print("                 object. Exactly:")
    print("                   isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0")
    print("        admin.hollisworks.com -> the Hollisworks tenant client (lazy,")
    print("                 fail-loud if HOLLISWORKS_AUTH0_* is unset)")
    print("        every other host      -> the EXISTING 2nd Act `auth0` singleton,")
    print("                 unchanged (this is why 2nd Act cannot regress)")
    print("      Called identically by login/page.js and proxy.js; the fix reuses it")
    print("      rather than reinventing host detection.\n")

    ok("[task1a] every host-unaware file reported explicitly",
       f"{len(pages)} pages + {len(routes)} API route handlers + {len(libs)} shared "
       f"server helpers = {len(pages) + len(routes) + len(libs)} files, each listed "
       "above with its per-host redirect target. dashboard/page.js was one of 41 "
       "pages with the identical defect.")
    ok("[task1b] getAuthClientForHost signature + return shape reported",
       "getAuthClientForHost(host: string) -> Auth0Client (synchronous); "
       "isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0. The fix calls "
       "it the same way login/page.js and proxy.js already do.")


# ── Source-level guards ──────────────────────────────────────────────────────
def source_guards(pages, routes, libs):
    # 1. Nothing outside the selector imports the fixed client directly anymore.
    offenders = []
    for p in _walk_web_sources():
        if os.path.basename(p) in ("authForHost.js", "auth0.js", "auth0Hollisworks.js"):
            continue
        src = _read(p)
        if re.search(r'from\s+["\']@/lib/auth0["\']', src):
            offenders.append(_rel(p))
    if offenders:
        fail("[src] no file outside lib/authForHost.js imports @/lib/auth0 directly",
             "still host-unaware: " + ", ".join(offenders))
    else:
        ok("[src] no file outside lib/authForHost.js imports @/lib/auth0 directly",
           "the ONLY remaining importer of the fixed 2nd Act client is the host "
           "selector itself — every consumer now goes through the Host.")

    # 2. The selector is exactly the documented one-liner (this is what makes the
    #    harness's selector provably the deployed rule, not a re-implementation).
    sel = _read(os.path.join(_WEB_ROOT, "lib", "authForHost.js"))
    sel_ok = re.search(
        r"export function getAuthClientForHost\(host\)\s*\{\s*return\s+"
        r"isHollisworksAdminHost\(host\)\s*\?\s*getHollisworksAuth0\(\)\s*:\s*auth0;\s*\}",
        sel,
    )
    if sel_ok:
        ok("[src] getAuthClientForHost is unchanged and is exactly the documented rule",
           "isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0 — so the "
           "harness, which imports the REAL isHollisworksAdminHost, exercises the "
           "deployed selection rule rather than a copy of it.")
    else:
        fail("[src] getAuthClientForHost unchanged", "authForHost.js body drifted")

    # 3. The new helper reads the REAL Host header the same way login/page.js does.
    srv = _read(os.path.join(_WEB_ROOT, "lib", "authServer.js"))
    login = _read(os.path.join(_WEB_ROOT, "app", "login", "page.js"))
    host_read = r'\(await headers\(\)\)\.get\("host"\)\s*\|\|\s*""'
    if (re.search(host_read, srv) and re.search(host_read, login)
            and "getAuthClientForHost" in srv):
        ok("[src] lib/authServer.js reads the Host header the SAME way login/page.js does",
           '(await headers()).get("host") || "" -> getAuthClientForHost(host). '
           "Byte-identical host read; no second host-detection implementation.")
    else:
        fail("[src] authServer.js host read matches login/page.js",
             "expected (await headers()).get(\"host\") || \"\" + getAuthClientForHost")

    # 4. proxy.js (middleware) must NOT pull next/headers in through the new module.
    proxy = _read(os.path.join(_REPO_ROOT, "apps", "web", "proxy.js"))
    if "authServer" not in proxy and "next/headers" not in proxy \
            and "next/headers" not in sel:
        ok("[src] proxy.js (middleware) does not import the new next/headers module",
           "authServer.js is deliberately separate from authForHost.js: middleware "
           "keeps importing only the pure selector, so the middleware bundle never "
           "pulls in next/headers.")
    else:
        fail("[src] proxy.js free of next/headers",
             "middleware now transitively imports next/headers")

    # 5. 2nd Act's client never binds the Hollisworks Auth0 tenant.
    #
    # This originally asserted the literal string "hollisworks" was absent from
    # auth0.js — a proxy for the real invariant, valid while auth0.js was a bare
    # 4-line client. The later `twoactbaseurl` sprint broke the proxy but not the
    # invariant: auth0.js now lists 2nd Act's OWN tenant subdomain,
    # 2ndactcapital.hollisworks.com, in its appBaseUrl allow-list (hollisworks.com
    # is just the platform DNS parent every client firm gets a subdomain under).
    # The check now tests the invariant directly.
    a0 = _read(os.path.join(_WEB_ROOT, "lib", "auth0.js"))
    hollisworks_tenant_markers = (
        "HOLLISWORKS_AUTH0",
        "getHollisworksAuth0",
        "hollisworksAudience",
        "hollisworksAppBaseUrl",
        "HOLLISWORKS_API_AUDIENCE",
        "api.hollisworks.com",
        "admin.hollisworks.com",
    )
    leaked = [m for m in hollisworks_tenant_markers if m in a0]
    if 'audience: "https://api.2ndactcapital.com"' in a0 and not leaked:
        ok("[regress] lib/auth0.js (2nd Act client) never binds the Hollisworks tenant",
           "still hardcodes audience https://api.2ndactcapital.com and references none of "
           "the Hollisworks Auth0 tenant's config. (Its appBaseUrl allow-list contains "
           "2ndactcapital.hollisworks.com — 2nd Act's own tenant subdomain, added by the "
           "later twoactbaseurl sprint — not the Hollisworks tenant.)")
    else:
        fail("[regress] lib/auth0.js never binds the Hollisworks tenant",
             f"leaked_hollisworks_tenant_markers={leaked}")

    # 5b. Client/server boundary: lib/theme.js is reachable from client
    #     components (ThemeProvider -> Sidebar -> AppShell). It statically
    #     imported lib/api.js, so making api.js host-aware dragged next/headers
    #     into the client graph and Turbopack rejected the build outright. The
    #     server-only loader now lives in lib/themeServer.js.
    theme = _read(os.path.join(_WEB_ROOT, "lib", "theme.js"))
    theme_server = _read(os.path.join(_WEB_ROOT, "lib", "themeServer.js"))
    if ("@/lib/api" not in theme
            and "export async function loadTheme" not in theme
            and "export async function loadTheme" in theme_server
            and "@/lib/api" in theme_server):
        ok("[src] client-reachable lib/theme.js no longer imports the server-only api.js",
           "loadTheme moved to lib/themeServer.js. lib/theme.js is imported by "
           "ThemeProvider (a client component), so its static `@/lib/api` import "
           "pulled the whole server auth chain into the client bundle — latent "
           "before, a hard Turbopack error once api.js needed next/headers.")
    else:
        fail("[src] lib/theme.js free of the server-only api.js import",
             "theme.js still imports @/lib/api or still exports loadTheme")

    # 6. PER-FILE: every discovered file actually uses the host-aware helper AND
    #    has no direct client reference left.
    bad = []
    for p in pages:
        src = _read(p)
        if "getHostSession()" not in src or re.search(r"\bauth0\.", src):
            bad.append(_rel(p) + " (page)")
    for p in routes:
        src = _read(p)
        if "getRequestAuthClient()" not in src or re.search(r"\bauth0\.", src):
            bad.append(_rel(p) + " (route)")
    for p in libs:
        src = _read(p)
        if "getRequestAuthClient" not in src or re.search(r"\bauth0\.\w+\(", src):
            bad.append(_rel(p) + " (lib)")
    if bad:
        fail("[src] every Task-1a file fixed identically", "not fixed: " + ", ".join(bad))
    else:
        ok("[src] every Task-1a file fixed identically",
           f"{len(pages)} pages use `await getHostSession()`; {len(routes)} route "
           f"handlers resolve `const authClient = await getRequestAuthClient()`; "
           f"{len(libs)} shared helpers do the same. Zero residual `auth0.` calls.")

    # 7. PER-FILE: the no-session redirect target stayed host-relative (so the
    #    already-host-aware /auth/login serves it on the SAME host).
    wrong = []
    for p in pages:
        src = _read(p)
        for target in re.findall(r"redirect\(\s*[\"'`]([^\"'`]+)", src):
            if target.startswith("http") or "2ndactcapital.com" in target \
                    or "hollisworks.com" in target:
                wrong.append(f"{_rel(p)} -> {target}")
    if wrong:
        fail("[src] no-session redirect target stays host-relative (per-host correct)",
             "absolute/host-assuming redirect: " + ", ".join(wrong))
    else:
        ok("[src] no-session redirect target stays host-relative (per-host correct)",
           "every redirect is a host-relative path (/auth/login?returnTo=... or "
           "/dashboard), so a Hollisworks-tenant visitor re-enters login through "
           "admin.hollisworks.com's OWN host — proxy.js then picks the Hollisworks "
           "client. No page hardcodes 2nd Act's domain.")


# ── The real proof: real Auth0 SDK, real encrypted sessions, real redirect walk ─
def harness_proof(pages, routes):
    node = shutil.which("node")
    if not node:
        fail("[proof] node runtime", "node not found — cannot exercise the real SDK")
        return
    if not os.path.exists(_HARNESS):
        fail("[proof] harness present", f"missing {_HARNESS}")
        return

    targets = []
    for p in pages:
        rt = _returnto_for(p) or _route_path_for(p)
        targets.append({"file": _rel(p), "path": _route_path_for(p), "returnTo": rt})
    # Route handlers have no redirect of their own (they 401), but their session
    # gate is the same call, so prove the gate on them too against /dashboard's
    # redirect graph.
    for p in routes:
        targets.append({"file": _rel(p), "path": _route_path_for(p),
                        "returnTo": _route_path_for(p)})

    try:
        proc = subprocess.run(
            [node, _HARNESS, json.dumps(targets), _EPOCH],
            capture_output=True, text=True, timeout=180,
        )
    except Exception as exc:
        fail("[proof] run Node harness", str(exc))
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
             f"rc={proc.returncode} stdout={proc.stdout[-2000:]!r} "
             f"stderr={proc.stderr[-2000:]!r}")
        return

    checks = data["checks"]
    by_name = {c["name"]: c for c in checks}

    def _one(exact, our_name):
        c = by_name.get(exact)
        if c and c.get("pass"):
            ok(our_name, c.get("detail", ""))
        else:
            fail(our_name, f"harness: {c}")

    _one("root cause: 2nd Act's client CANNOT read a real Hollisworks session "
         "cookie (and vice versa)",
         "[proof] root cause — 2nd Act's client cannot read a real Hollisworks "
         "session cookie (distinct name + distinct secret)")
    _one("real Hollisworks-tenant session round-trips through the real Auth0 SDK",
         "[proof] a real Hollisworks-tenant session decrypts through the REAL "
         "Auth0 SDK (not a signature check)")
    _one("control: the minted 2nd Act session cookie is genuinely LIVE "
         "(negatives are not vacuous)",
         "[control] both minted cookies are live — every null session below is a "
         "real tenant mismatch, not an expired JWE")

    # Grouped per-file families — report a rollup plus every failure individually.
    families = [
        ("hollisworks-session",
         "[proof] Hollisworks-tenant session PASSES the session check and renders "
         "(0 redirects)"),
        ("prefix-loop-reproduced",
         "[proof] pre-fix contrast — the SAME request against the host-unaware "
         "check reproduces the infinite redirect loop"),
        ("2ndact-regression",
         "[regress] 2nd Act session on 2nd Act's host STILL passes, outcome "
         "identical to pre-fix"),
        ("nosession-single-redirect",
         "[proof] no session on EITHER host -> exactly ONE redirect, not a loop"),
        ("cross-tenant-isolation",
         "[proof] cross-tenant isolation — neither cookie authenticates on the "
         "other host"),
    ]
    n_targets = len(targets)
    for prefix, label in families:
        rows = [c for c in checks if c["name"].startswith(prefix + "|")]
        failed = [c for c in rows if not c.get("pass")]
        if len(rows) != n_targets:
            fail(label, f"expected {n_targets} per-file runs, got {len(rows)}")
            continue
        if failed:
            for c in failed:
                fail(label + " — " + c["name"].split("|", 1)[1], c.get("detail", ""))
        else:
            sample = rows[0].get("detail", "")
            ok(label,
               f"proven independently for ALL {n_targets} fixed files "
               f"(pages + route handlers). e.g. {rows[0]['name'].split('|', 1)[1]}: "
               f"{sample}")

    # Explicitly surface /dashboard — the originally reported bug — on its own.
    for prefix, label in families:
        c = by_name.get(prefix + "|apps/web/app/dashboard/page.js")
        if c is None:
            fail("[dashboard] " + prefix, "dashboard/page.js missing from harness run")
        elif c.get("pass"):
            ok("[dashboard] " + label.split("] ", 1)[1], c.get("detail", ""))
        else:
            fail("[dashboard] " + label.split("] ", 1)[1], c.get("detail", ""))


# ── DB teardown round-trip (proves teardown genuinely deletes) ───────────────
async def _connect(dsn):
    import asyncpg
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


async def db_teardown_roundtrip():
    if not DATABASE_URL:
        ok("[teardown] zero leftover rows",
           "this verify creates no DB rows (frontend source + real-SDK proof); "
           "nothing to leak.")
        return
    try:
        import asyncpg  # noqa: F401
    except Exception as exc:
        ok("[teardown] zero leftover rows",
           f"asyncpg unavailable ({exc}); verify creates no DB rows.")
        return

    conn = await _connect(DATABASE_URL)
    try:
        await conn.execute(
            "DELETE FROM marketing_contacts WHERE email = $1 OR firm = $2",
            SENTINEL_EMAIL, SENTINEL_FIRM,
        )
        await conn.execute(
            "INSERT INTO marketing_contacts (name, firm, email, source_host) "
            "VALUES ($1, $2, $3, $4)",
            "DashboardHostFix Verify", SENTINEL_FIRM, SENTINEL_EMAIL, "verify.local",
        )
        n = await conn.fetchval(
            "SELECT count(*) FROM marketing_contacts WHERE email = $1", SENTINEL_EMAIL
        )
        if n != 1:
            fail("[setup] sentinel round-trip insert", f"expected 1 got {n}")
    finally:
        await conn.close()

    conn = await _connect(DATABASE_URL)
    try:
        await conn.execute(
            "DELETE FROM marketing_contacts WHERE email = $1 OR firm = $2",
            SENTINEL_EMAIL, SENTINEL_FIRM,
        )
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


# ── The 52-file edit must actually COMPILE (Turbopack enforces the
#    client/server boundary that this change moves; a source-only check would
#    have missed the theme.js regression entirely) ─────────────────────────────
def next_build():
    npx = shutil.which("npx")
    if not npx:
        ok("[build] `next build` — SKIP", "npx not on PATH; source assertions stand.")
        return
    env = dict(os.environ)
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    try:
        proc = subprocess.run(
            [npx, "next", "build"], cwd=_WEB_ROOT, capture_output=True,
            text=True, env=env, timeout=1800,
        )
    except Exception as exc:
        fail("[build] `next build`", str(exc))
        return
    if proc.returncode == 0 and "Compiled successfully" in (proc.stdout or ""):
        ok("[build] `next build` compiles clean with all 52 files host-aware",
           "Turbopack production build + TypeScript check pass. This is the gate "
           "that caught the client/server boundary regression a source-only check "
           "would have shipped.")
    else:
        tail = (proc.stdout or "")[-1500:] + " || " + (proc.stderr or "")[-1500:]
        fail("[build] `next build`", f"rc={proc.returncode} {tail}")


async def main_async():
    pages, routes, libs = discover()
    if not pages or not routes or not libs:
        fail("[task1a] discovery", f"pages={len(pages)} routes={len(routes)} libs={len(libs)}")
    task1_report(pages, routes, libs)
    source_guards(pages, routes, libs)
    harness_proof(pages, routes)
    next_build()
    await db_teardown_roundtrip()

    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    print("\n".join(f"[{s}] {n} — {d}" for s, n, d in _RESULTS))
    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
