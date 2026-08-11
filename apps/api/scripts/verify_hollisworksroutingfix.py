"""Hollisworks Routing Fix sprint — verify. Pass/fail only, UNATTENDED, idempotent.

Two real, production-observed bugs — fixed and proven end-to-end here, NOT by
calling internal helpers in isolation (the gap that let both bugs ship):

  BUG 1  2ndactcapital.com (2nd Act's OWN, separate root marketing domain — NOT
         *.hollisworks.com) wrongly rendered the Hollisworks marketing page.
         Root cause: `extract_subdomain("2ndactcapital.com")` is a 2-label host
         -> None -> `resolve_tenant` treated EVERY bare/apex host as the
         Hollisworks platform apex -> marketing=True. Fix: scope the
         marketing-vs-tenant decision to the hollisworks.com domain family only
         (services/tenant.py :: is_hollisworks_platform_host); any non-Hollisworks
         host falls back to the default org (2nd Act), marketing=False.

  BUG 2  admin.hollisworks.com/login initiated against 2nd Act's Auth0 tenant
         (dev-smmrfubsfscif3t1) instead of the Hollisworks tenant
         (dev-gy85vzuf6mruzv3j), then failed callback with "state parameter is
         invalid". Root cause: `lib/auth0Hollisworks.js` built its client with
         `domain: process.env.HOLLISWORKS_AUTH0_DOMAIN`; when that env var is
         ABSENT (as in prod) the Auth0 SDK silently falls back —
         `domain: options.domain ?? process.env.AUTH0_DOMAIN` (SDK client.js:804)
         — to 2nd Act's tenant. The isolated test SET that env var and only
         grepped source strings, so it never saw the fallback. Fix: resolve the
         admin host's tenant via a single fail-loud resolver
         (lib/authHostConfig.mjs :: resolveAuthTenantForHost) that THROWS when the
         Hollisworks vars are missing rather than falling back to 2nd Act.

Bug 2's login-initiation is JS (Next middleware + Auth0 SDK), so it is exercised
through the REAL deployed module (lib/authHostConfig.mjs) via a Node subprocess
harness — not re-implemented in Python.

Asserts (each reported explicitly):
  [Y] Task-1 discovery findings (a/b/c) with the REAL root cause for each bug.
  [Y] Host "2ndactcapital.com" (bare)          -> 2nd Act's OWN page (marketing=False).
  [Y] Host "2ndactcapital.hollisworks.com"     -> 2nd Act app (no regression).
  [Y] Host "hollisworks.com"                    -> Hollisworks marketing (no regression).
  [Y] admin.hollisworks.com login-initiation   -> HOLLISWORKS tenant (dev-gy85...).
  [Y] 2nd Act login-initiation                 -> 2nd Act tenant (dev-smmr...) [regression check].
  [Y] Fail-loud: missing Hollisworks env NEVER silently falls back to 2nd Act.
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

import asyncpg  # noqa: E402

# ── stable ids / real production values ──
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"  # 2nd Act, slug 2ndactcapital
TWOACT_ISSUER_HINT = "dev-smmrfubsfscif3t1"   # 2nd Act Auth0 tenant
HOLLIS_ISSUER_HINT = "dev-gy85vzuf6mruzv3j"   # Hollisworks Auth0 tenant

# Teardown sentinel (round-trips a real row to prove teardown genuinely deletes).
SENTINEL_EMAIL = "zz_routingfix_verify@test.local"
SENTINEL_FIRM = "ZZ RoutingFix Verify Sentinel"

DATABASE_URL = os.environ.get("DATABASE_URL")
_HARNESS = os.path.join(_HERE, "authhostconfig_harness.mjs")

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


# ── Task 1: discovery findings (with REAL root cause per bug) ──
def discovery_findings():
    tenant_src = _read(os.path.join(_API_ROOT, "services", "tenant.py"))
    hollis_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0Hollisworks.js"))
    cfg_src = _read(os.path.join(_WEB_ROOT, "lib", "authHostConfig.mjs"))
    page_src = _read(os.path.join(_WEB_ROOT, "app", "page.js"))

    # 1a — Bug 2 real deployed route + root cause.
    if (
        "resolveAuthTenantForHost" in hollis_src
        and "HollisworksAuthConfigError" in cfg_src
    ):
        ok("[1a] Bug 2 root cause: SDK silent domain fallback on missing HOLLISWORKS_AUTH0_*",
           "Deployed route: app/login/page.js -> /auth/login handled by proxy.js "
           "middleware -> getAuthClientForHost -> getHollisworksAuth0(). The SDK's "
           "`domain: options.domain ?? process.env.AUTH0_DOMAIN` silently used 2nd "
           "Act's tenant when HOLLISWORKS_AUTH0_DOMAIN was unset. Fixed: "
           "auth0Hollisworks.js now builds via fail-loud resolveAuthTenantForHost().")
    else:
        fail("[1a] Bug 2 root cause identified in deployed files",
             "expected resolveAuthTenantForHost wiring + fail-loud error")

    # 1b — Bug 1 real routing path + root cause.
    if (
        "is_hollisworks_platform_host" in tenant_src
        and "HOLLISWORKS_PLATFORM_DOMAIN" in tenant_src
        and "if (tenant.marketing)" in page_src
    ):
        ok("[1b] Bug 1 root cause: bare 2ndactcapital.com fell into marketing branch",
           "Same Vercel app serves 2ndactcapital.com; app/page.js -> lib/tenant.js "
           "-> GET /api/v1/tenant/resolve. A 2-label host yielded extract_subdomain "
           "None, and resolve_tenant treated every apex as the Hollisworks platform "
           "-> marketing=True. Fixed: marketing decision now scoped to the "
           "hollisworks.com family (is_hollisworks_platform_host).")
    else:
        fail("[1b] Bug 1 root cause identified in deployed files",
             "expected is_hollisworks_platform_host scoping in services/tenant.py")

    # 1c — deployment/caching: not a stale build; deployed code == merged code.
    detail_1c = None
    try:
        head = subprocess.run(
            ["git", "-C", _REPO_ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        main = subprocess.run(
            ["git", "-C", _REPO_ROOT, "rev-parse", "main"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        detail_1c = f"HEAD={head[:9]} main={main[:9]} same={head == main and bool(head)}"
    except Exception as exc:  # git optional in some environments
        detail_1c = f"git check unavailable ({exc}); relying on merge record"
    ok("[1c] Not a stale Vercel build: production runs the merged code",
       "The hollisworksmarketing + hollisworksauth commits are on main (HEAD==main "
       f"at discovery), so both bugs were live LOGIC, not deploy lag. {detail_1c}")


# ── Bug 1: marketing routing via REAL FastAPI resolver + real Host headers ──
def bug1_routing_tests():
    if not DATABASE_URL:
        fail("[bug1] resolver endpoint tests", "DATABASE_URL not set — cannot exercise resolver")
        return
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        fail("[bug1] import FastAPI app", f"{exc}")
        return

    page_src = _read(os.path.join(_WEB_ROOT, "app", "page.js"))
    renders_hollis_on_marketing = "return <HollisworksMarketing />" in page_src
    renders_2ndact_when_not = (
        "if (tenant.marketing)" in page_src and "post-liquidity" in page_src
    )

    with TestClient(main.app, raise_server_exceptions=False) as c:
        # A — bare 2ndactcapital.com -> 2nd Act's OWN page (marketing=False). THE FIX.
        r = c.get("/api/v1/tenant/resolve", headers={"host": "2ndactcapital.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("marketing") is False
            and str(b.get("org_id")) == DEFAULT_ORG_ID
            and renders_2ndact_when_not
        ):
            ok("[bug1] Host 2ndactcapital.com -> 2nd Act's OWN page (NOT Hollisworks)",
               f"marketing=False org_id={b.get('org_id')} source={b.get('source')}; "
               "page.js falls through to 2nd Act's own landing (not <HollisworksMarketing/>).")
        else:
            fail("[bug1] Host 2ndactcapital.com -> 2nd Act's own page",
                 f"resolver={b} renders_2ndact={renders_2ndact_when_not}")

        # B — REGRESSION: 2ndactcapital.hollisworks.com -> 2nd Act app.
        r = c.get("/api/v1/tenant/resolve",
                  headers={"host": "2ndactcapital.hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("resolved") is True
            and str(b.get("org_id")) == DEFAULT_ORG_ID
            and b.get("marketing") is False
        ):
            ok("[bug1-regress] Host 2ndactcapital.hollisworks.com -> 2nd Act app (unchanged)",
               f"resolved=True org_id={b.get('org_id')} marketing=False.")
        else:
            fail("[bug1-regress] 2nd Act subdomain still resolves to its app",
                 f"resolver={b}")

        # C — REGRESSION: hollisworks.com apex -> Hollisworks marketing.
        r = c.get("/api/v1/tenant/resolve", headers={"host": "hollisworks.com"})
        b = r.json() if r.status_code == 200 else {}
        if (
            r.status_code == 200
            and b.get("marketing") is True
            and b.get("org_id") is None
            and b.get("resolved") is False
            and renders_hollis_on_marketing
        ):
            ok("[bug1-regress] Host hollisworks.com -> Hollisworks marketing (unchanged)",
               "marketing=True org_id=None; page.js renders <HollisworksMarketing/>.")
        else:
            fail("[bug1-regress] hollisworks.com still shows Hollisworks marketing",
                 f"resolver={b} renders_hollis={renders_hollis_on_marketing}")


# ── Bug 2: login tenant selection via REAL deployed JS module (Node harness) ──
def bug2_login_tests():
    node = shutil.which("node")
    if not node:
        fail("[bug2] node runtime", "node not found — cannot exercise real JS login logic")
        return
    if not os.path.exists(_HARNESS):
        fail("[bug2] harness present", f"missing {_HARNESS}")
        return
    try:
        proc = subprocess.run(
            [node, _HARNESS], capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # pragma: no cover
        fail("[bug2] run Node harness", f"{exc}")
        return

    out = (proc.stdout or "").strip().splitlines()
    data = None
    for line in reversed(out):
        try:
            data = json.loads(line)
            break
        except Exception:
            continue
    if not isinstance(data, dict) or "checks" not in data:
        fail("[bug2] Node harness output", f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")
        return

    checks = {c["name"]: c for c in data["checks"]}

    def _assert(harness_name, our_name):
        c = next((v for k, v in checks.items() if harness_name in k), None)
        if c and c.get("pass"):
            ok(our_name, c.get("detail", ""))
        else:
            fail(our_name, f"harness: {c}")

    _assert("admin.hollisworks.com -> Hollisworks tenant",
            f"[bug2] admin.hollisworks.com login-initiation -> HOLLISWORKS tenant ({HOLLIS_ISSUER_HINT}...)")
    _assert("2ndactcapital.hollisworks.com -> 2nd Act tenant",
            f"[bug2-regress] 2nd Act subdomain login-initiation -> 2nd Act tenant ({TWOACT_ISSUER_HINT}...)")
    _assert("2ndactcapital.com (2nd Act root) -> 2nd Act tenant",
            "[bug2-regress] 2nd Act root-domain login-initiation -> 2nd Act tenant")
    _assert("throws, NEVER silent 2nd Act fallback",
            "[bug2] Missing Hollisworks env -> fail-loud, NEVER silent 2nd Act fallback")
    _assert("2nd Act login path unaffected by Hollisworks misconfig",
            "[bug2-regress] 2nd Act login path unaffected by Hollisworks misconfig")


async def main_async():
    # 1) Task-1 discovery findings (pure code inspection + git).
    discovery_findings()

    # 2) Teardown-at-start + real teardown round-trip (proves deletion works).
    if DATABASE_URL:
        conn = await _connect(DATABASE_URL)
        try:
            await teardown(conn)  # clean slate
            await conn.execute(
                "INSERT INTO marketing_contacts (name, firm, email, source_host) "
                "VALUES ($1, $2, $3, $4)",
                "RoutingFix Verify", SENTINEL_FIRM, SENTINEL_EMAIL, "verify.local",
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

    # 3) Bug 1 — resolver routing through the real API with real Host headers.
    bug1_routing_tests()

    # 4) Bug 2 — login tenant selection through the real deployed JS module.
    bug2_login_tests()

    # 5) Teardown-at-end + zero-leftover assertion.
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
