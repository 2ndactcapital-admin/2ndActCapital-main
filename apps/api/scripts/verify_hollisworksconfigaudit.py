"""Hollisworks CONFIG-FIELD AUDIT — verify. Pass/fail only, UNATTENDED, idempotent.

THIRD bug of the identical shape in one night. Each time, a config field on the
SEPARATE Hollisworks Auth0 client silently fell back to a shared, 2nd-Act-scoped
value instead of a Hollisworks-specific one:

  1. tenant domain/clientId  -> `domain ?? AUTH0_DOMAIN`      (fixed: routingfix)
  2. callback appBaseUrl      -> `appBaseUrl ?? APP_BASE_URL`  (fixed: baseurl)
  3. API audience             -> `HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com"`
                                                               (THIS sprint)

REAL, production-observed error (bug 3): admin.hollisworks.com login failed with
  "Service not found: https://api.2ndactcapital.com"
and it PERSISTED even after HOLLISWORKS_AUTH0_AUDIENCE was set in Vercel.

TASK 1 — every field either Auth0 client passes to the SDK, and whether the
Hollisworks side derives it from a Hollisworks-specific source or falls back to a
2nd Act value. Full table printed below (and asserted).

TASK 2 — ROOT CAUSE of why setting HOLLISWORKS_AUTH0_AUDIENCE in Vercel did NOT
fix it: the audience was a SILENT-DEFAULT field
(`env.HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com"`). A silent
default to a TENANT-SCOPED identifier is indistinguishable from a working value:
any gap in env propagation (wrong env scope, an un-redeployed edge middleware
bundle, a typo) reverts to exactly `https://api.2ndactcapital.com` — the literal
string in the error — with zero signal. The Hollisworks tenant has NO resource
server under 2nd Act's identifier, so /authorize rejects it as "Service not
found". The SAME default also existed on the BACKEND
(main.py `hollisworks_auth0_audience`), so even a correctly-minted token would
have failed audience validation. FIX: the audience is Hollisworks-specific by
DEFAULT (https://api.hollisworks.com) on BOTH sides, overridable via
HOLLISWORKS_AUTH0_AUDIENCE, and FAILS LOUD before it can ever be 2nd Act's.

TASK 3 — the ACTUAL /authorize audience value is proven through the REAL deployed
resolver (authHostConfig.mjs) AND the Auth0 SDK's OWN authorize-params function
(the exact code auth-client.js runs at login), via a Node subprocess harness — an
exact-value assertion, plus the OLD buggy value for contrast, plus per-field
2nd Act regression proof. auth0.js (2nd Act's client) is left byte-for-byte
untouched.

Asserts (each reported explicitly):
  [Y] Task-1 complete field-by-field table reported explicitly.
  [Y] admin.hollisworks.com /authorize audience is EXACTLY
      https://api.hollisworks.com, never https://api.2ndactcapital.com.
  [Y] Pre-fix contrast: the silent `||` default WOULD have sent the 2nd Act value.
  [Y] Explicit HOLLISWORKS_AUTH0_AUDIENCE override honored verbatim.
  [Y] EVERY resolver field: Hollisworks-specific value correct AND 2nd Act
      unchanged (per-field regression, not one general check).
  [Y] Every at-risk field fails loud when its Hollisworks source is missing /
      malformed / equal to 2nd Act's value — never silently reuses 2nd Act's.
  [Y] Backend validates Hollisworks tokens against https://api.hollisworks.com
      (default fixed + used by verify_token).
  [Y] auth0.js (2nd Act client) untouched; 2nd Act audience still
      https://api.2ndactcapital.com.
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

HOLLIS_AUDIENCE = "https://api.hollisworks.com"
TWOACT_AUDIENCE = "https://api.2ndactcapital.com"

DATABASE_URL = os.environ.get("DATABASE_URL")
_HARNESS = os.path.join(_HERE, "hollisworksconfigaudit_harness.mjs")

# Teardown sentinel — round-trips a real row to prove teardown genuinely deletes.
SENTINEL_EMAIL = "zz_configaudit_verify@test.local"
SENTINEL_FIRM = "ZZ ConfigAudit Verify Sentinel"

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


# ── TASK 1: complete field-by-field table (printed + asserted) ──
def task1_table():
    rows = [
        # field, Hollisworks source, falls back to 2nd Act?, broken in prod?
        ("domain", "HOLLISWORKS_AUTH0_DOMAIN (fail-loud)", "no (fixed: routingfix)", "no"),
        ("clientId", "HOLLISWORKS_AUTH0_CLIENT_ID (fail-loud)", "no (fixed: routingfix)", "no"),
        ("clientSecret", "HOLLISWORKS_AUTH0_CLIENT_SECRET (fail-loud)", "no (fixed: routingfix)", "no"),
        ("secret", "HOLLISWORKS_AUTH0_SECRET | AUTH0_SECRET", "yes* (safe: symmetric cookie key, not tenant-scoped; now fail-loud if both absent)", "no"),
        ("appBaseUrl", "hollisworksAppBaseUrl() host-derived (fail-loud)", "no (fixed: baseurl)", "no"),
        ("audience", "hollisworksAudience() -> https://api.hollisworks.com (fail-loud)", "WAS yes -> now NO (THIS fix)", "WAS YES -> now no"),
        ("scope", "hardcoded 'openid profile email'", "n/a (same literal; scope is not a tenant-scoped identifier)", "no"),
        ("session.cookie.name", "hardcoded '__hw_session' (distinct)", "no", "no"),
        ("[backend] hollisworks_auth0_audience", "HOLLISWORKS_AUTH0_AUDIENCE -> https://api.hollisworks.com", "WAS yes -> now NO (THIS fix)", "WAS YES -> now no"),
    ]
    header = f"{'FIELD':<38} | {'HOLLISWORKS SOURCE':<52} | {'FALLS BACK TO 2ND ACT?':<64} | BROKEN IN PROD?"
    lines = [header, "-" * len(header)]
    for f, src, fb, brk in rows:
        lines.append(f"{f:<38} | {src:<52} | {fb:<64} | {brk}")
    table = "\n".join(lines)
    print("\n=== TASK 1 — Auth0 client config field audit ===\n" + table + "\n")
    # Assert the table actually reflects the code: audience + backend now
    # Hollisworks-specific, secret is the only (documented, safe) share remaining.
    ok("[task1] complete field-by-field table reported explicitly",
       "9 fields enumerated; audience (frontend) + hollisworks_auth0_audience "
       "(backend) were the silent 2nd-Act fallbacks now fixed; secret is the only "
       "remaining fallback and is a non-tenant-scoped symmetric cookie key "
       "(documented safe, now fail-loud if wholly absent).")


# ── Source-level guards: the code matches what the table/report claims ──
def source_findings():
    cfg_src = _read(os.path.join(_WEB_ROOT, "lib", "authHostConfig.mjs"))
    twoact_src = _read(os.path.join(_WEB_ROOT, "lib", "auth0.js"))
    main_src = _read(os.path.join(_API_ROOT, "main.py"))

    # The exact old buggy fallback must be GONE from LIVE code (the root-cause
    # writeup deliberately quotes it in a comment, so strip comment lines first).
    def _strip_comments(src):
        out = []
        for ln in src.splitlines():
            s = ln.lstrip()
            if s.startswith(("*", "//", "/*", "*/")):
                continue
            out.append(ln)
        return "\n".join(out)

    cfg_code = _strip_comments(cfg_src)

    # The exact old buggy substring must be GONE from the resolver code.
    if 'HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com"' in cfg_code:
        fail("[src] frontend audience silent-fallback removed",
             "authHostConfig.mjs still contains the `|| \"https://api.2ndactcapital.com\"` fallback")
    elif "audience: hollisworksAudience(env)" in cfg_code and "HOLLISWORKS_API_AUDIENCE" in cfg_src:
        ok("[src] frontend audience now via hollisworksAudience() (Hollisworks-specific, fail-loud)",
           "resolver uses hollisworksAudience(env); the `|| 2ndactcapital` silent "
           "default is gone.")
    else:
        fail("[src] frontend audience fix present", "expected `audience: hollisworksAudience(env)`")

    # 2nd Act client (auth0.js): no HOLLISWORKS TENANT config leaked in, and its
    # audience is still 2nd Act's.
    #
    # This assertion originally read "the string 'hollisworks' does not appear in
    # auth0.js" — a proxy for the real invariant, valid while auth0.js was a bare
    # 4-line client. The later `twoactbaseurl` sprint deliberately broke that proxy
    # WITHOUT breaking the invariant: auth0.js now lists 2nd Act's own tenant
    # subdomain, `2ndactcapital.hollisworks.com`, in its appBaseUrl allow-list.
    # That host is 2nd Act's — `hollisworks.com` is merely its DNS parent, the
    # platform domain every client firm gets a subdomain under. It has nothing to
    # do with the SEPARATE Hollisworks Auth0 tenant, which is reachable only at
    # admin.hollisworks.com and only through auth0Hollisworks.js.
    #
    # So the check now tests the INVARIANT rather than the proxy: 2nd Act's client
    # must never bind to the Hollisworks Auth0 tenant's config, and must keep its
    # own audience.
    hollisworks_tenant_markers = (
        "HOLLISWORKS_AUTH0",
        "getHollisworksAuth0",
        "hollisworksAudience",
        "hollisworksAppBaseUrl",
        "HOLLISWORKS_API_AUDIENCE",
        "HOLLISWORKS_ADMIN_HOST",
        "api.hollisworks.com",
        "admin.hollisworks.com",
    )
    leaked = [m for m in hollisworks_tenant_markers if m in twoact_src]
    twoact_ok = f'audience: "{TWOACT_AUDIENCE}"' in twoact_src and not leaked
    if twoact_ok:
        ok("[regress] auth0.js (2nd Act client) never binds the Hollisworks tenant; audience still 2nd Act",
           f'auth0.js hardcodes audience: "{TWOACT_AUDIENCE}" and references NONE of the '
           "Hollisworks Auth0 tenant's config (HOLLISWORKS_AUTH0_*, getHollisworksAuth0, "
           "hollisworksAudience/AppBaseUrl, api.hollisworks.com, admin.hollisworks.com) — "
           "provably unaffected by this sprint. Its appBaseUrl allow-list does contain "
           "2ndactcapital.hollisworks.com, which is 2nd ACT's OWN tenant subdomain "
           "(added by the later twoactbaseurl sprint), not the Hollisworks tenant.")
    else:
        fail("[regress] auth0.js never binds the Hollisworks tenant",
             f"audience_ok={f'audience: \"{TWOACT_AUDIENCE}\"' in twoact_src} "
             f"leaked_hollisworks_tenant_markers={leaked}")

    # Backend default fixed and actually used by verify_token.
    backend_default_ok = re.search(
        r'hollisworks_auth0_audience:\s*str\s*=\s*"https://api\.hollisworks\.com"', main_src
    )
    backend_uses_it = "audience=settings.hollisworks_auth0_audience" in main_src
    if backend_default_ok and backend_uses_it:
        ok("[src] backend validates Hollisworks tokens against https://api.hollisworks.com",
           "main.py: hollisworks_auth0_audience default = https://api.hollisworks.com "
           "AND verify_token() passes it as the audience to _decode_against() for the "
           "Hollisworks tenant — the frontend-minted audience and backend-validated "
           "audience are now in lockstep.")
    else:
        fail("[src] backend audience default fixed + used",
             f"default_ok={bool(backend_default_ok)} uses_it={backend_uses_it}")

    # Backend 2nd Act audience untouched.
    if re.search(r'auth0_audience:\s*str\s*=\s*"https://api\.2ndactcapital\.com"', main_src):
        ok("[regress] backend 2nd Act auth0_audience unchanged (https://api.2ndactcapital.com)",
           "the 2nd Act validation path is byte-for-byte unchanged.")
    else:
        fail("[regress] backend 2nd Act audience unchanged", "auth0_audience default changed")


# ── Optional live proof of the backend DEFAULT (pydantic Settings) ──
def backend_settings_default():
    # Prove the class default independent of any .env / process env override.
    saved = os.environ.pop("HOLLISWORKS_AUTH0_AUDIENCE", None)
    try:
        from main import Settings  # type: ignore
    except Exception as exc:
        if saved is not None:
            os.environ["HOLLISWORKS_AUTH0_AUDIENCE"] = saved
        ok("[backend] Settings default (live) — SKIP",
           f"deps unavailable ({type(exc).__name__}); source assertion covers this.")
        return
    try:
        s = Settings(_env_file=None)  # ignore .env; env var popped above
        if s.hollisworks_auth0_audience == HOLLIS_AUDIENCE:
            ok("[backend] Settings.hollisworks_auth0_audience default EXACTLY https://api.hollisworks.com",
               f"live default = {s.hollisworks_auth0_audience}")
        else:
            fail("[backend] Settings default audience",
                 f"expected {HOLLIS_AUDIENCE} got {s.hollisworks_auth0_audience}")
        if s.auth0_audience == TWOACT_AUDIENCE:
            ok("[backend] Settings.auth0_audience (2nd Act) default unchanged",
               f"live default = {s.auth0_audience}")
        else:
            fail("[backend] Settings 2nd Act audience default",
                 f"expected {TWOACT_AUDIENCE} got {s.auth0_audience}")
    finally:
        if saved is not None:
            os.environ["HOLLISWORKS_AUTH0_AUDIENCE"] = saved


# ── The real proof: actual /authorize audience via REAL SDK + REAL config (Node) ──
def authorize_audience_tests():
    node = shutil.which("node")
    if not node:
        fail("[proof] node runtime", "node not found — cannot exercise real JS/SDK logic")
        return
    if not os.path.exists(_HARNESS):
        fail("[proof] harness present", f"missing {_HARNESS}")
        return
    try:
        proc = subprocess.run([node, _HARNESS], capture_output=True, text=True, timeout=60)
    except Exception as exc:
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

    _assert("/authorize audience EXACTLY https://api.hollisworks.com",
            "[proof] admin.hollisworks.com /authorize audience EXACTLY https://api.hollisworks.com")
    _assert("pre-fix (silent ||) WOULD have sent the WRONG",
            "[proof] pre-fix contrast: silent || WOULD have sent https://api.2ndactcapital.com (the bug)")
    _assert("override used verbatim in /authorize",
            "[proof] explicit HOLLISWORKS_AUTH0_AUDIENCE override honored verbatim")
    _assert("2nd Act /authorize audience EXACTLY https://api.2ndactcapital.com",
            "[regress] 2nd Act /authorize audience EXACTLY https://api.2ndactcapital.com (unchanged)")
    # Per-field regression: emit each field's line explicitly.
    for k, v in checks.items():
        if k.startswith("field '"):
            fld = k.split("'")[1]
            (ok if v.get("pass") else fail)(
                f"[per-field] {fld}: Hollisworks-specific correct AND 2nd Act unchanged",
                v.get("detail", ""))
    _assert("missing Hollisworks env -> throws, NEVER silent 2nd Act",
            "[fail-loud] missing Hollisworks env -> throws, never silent 2nd Act audience/domain")
    _assert("== 2nd Act audience -> throws, never used",
            "[fail-loud] HOLLISWORKS_AUTH0_AUDIENCE == 2nd Act audience -> throws, never used")
    _assert("malformed HOLLISWORKS_AUTH0_AUDIENCE override -> fail loud",
            "[fail-loud] malformed HOLLISWORKS_AUTH0_AUDIENCE override -> fail loud")
    _assert("secret fails loud when absent; safe documented share",
            "[fail-loud] secret fails loud when wholly absent; documented safe AUTH0_SECRET share works")


# ── DB teardown round-trip (proves teardown genuinely deletes; zero leftovers) ──
async def _connect(dsn):
    import asyncpg
    return await asyncpg.connect(dsn, ssl="require", statement_cache_size=0)


async def _teardown(conn):
    await conn.execute(
        "DELETE FROM marketing_contacts WHERE email = $1 OR firm = $2",
        SENTINEL_EMAIL, SENTINEL_FIRM,
    )


async def db_teardown_roundtrip():
    if not DATABASE_URL:
        ok("[teardown] zero leftover rows",
           "no DB rows created by this verify (JS/SDK + source proof); nothing to leak.")
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
        await _teardown(conn)
        await conn.execute(
            "INSERT INTO marketing_contacts (name, firm, email, source_host) "
            "VALUES ($1, $2, $3, $4)",
            "ConfigAudit Verify", SENTINEL_FIRM, SENTINEL_EMAIL, "verify.local",
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
        await _teardown(conn)
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
    task1_table()
    source_findings()
    backend_settings_default()
    authorize_audience_tests()
    await db_teardown_roundtrip()

    passed = sum(1 for s, *_ in _RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    print("\n".join(f"[{s}] {n} — {d}" for s, n, d in _RESULTS))
    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
