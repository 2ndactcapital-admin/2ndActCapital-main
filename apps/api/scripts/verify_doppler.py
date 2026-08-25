"""verify_doppler.py — Doppler secrets-management migration verifier.

Pass/fail output only. No interactive prompts. Idempotent. Read-only: it seeds
nothing, writes nothing to Postgres, and mutates no platform configuration.

SECRET SAFETY (assertion [6], enforced structurally rather than by discipline):
this script NEVER prints a secret value. It reports NAMES, and it reports
whether a real use of a value SUCCEEDED. Every line it emits is additionally
run through a scrubber (`_emit`) that refuses to print any string containing a
known secret value, so a future edit that carelessly interpolates one is caught
at output time rather than in a committed log. The final assertion re-scans the
script's own captured output against every secret value it has seen.

HONEST GATING: legs that cannot be executed report [BLOCKED] and force a
non-zero exit. They are never reported as PASS, and never simulated. The
migration is not done just because this file exists.

Assertions
  [1] Task 1's four discovery findings are reported explicitly (names only).
  [2] A real DB-dependent request succeeds via the Doppler-sourced value on
      Render.
  [3] A real frontend request succeeds via the Doppler-sourced value on Vercel.
  [4] `doppler run --` locally connects using the same credential Render uses,
      proven by a successful real query — NOT by comparing values.
  [5] render.yaml's AWS_* / VOYAGE_API_KEY gap is closed.
  [6] No real secret value appears anywhere in this script's own output.

Config (environment, or apps/api/.env as a fallback):
  DOPPLER_TOKEN            — service token; presence enables the Doppler legs.
  DATABASE_URL             — Postgres (PgBouncer; statement_cache_size=0).
  APP_SERVICE_DATABASE_URL — non-bypass 'app_service' role DSN.
  RENDER_API_URL           — base URL of the deployed API service, for [2].
  VERCEL_APP_URL           — base URL of the deployed frontend, for [3].
                             Defaults to https://2ndactcapital.com.
"""

import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_API_ROOT, "..", ".."))
for _py in ("python3.14", "python3.13", "python3.12", "python3.11"):
    _sp = os.path.join(_API_ROOT, "venv", "lib", _py, "site-packages")
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
sys.path.insert(0, _API_ROOT)

RENDER_YAML = os.path.join(_REPO_ROOT, "render.yaml")
DEFAULT_VERCEL_APP_URL = "https://2ndactcapital.com"

# Runtime vars whose absence from render.yaml was the Task 1c gap.
GAP_KEYS_REQUIRED = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "VOYAGE_API_KEY",
)
# Deliberately excluded from render.yaml; declaring them is a REGRESSION, not a
# fix. APP_BASE_URL/WEB_BASE_URL on the API service reintroduce the cross-tenant
# invite-URL leak; ALTRUIST_* has no credentials.
GAP_KEYS_FORBIDDEN_ON_API = (
    "APP_BASE_URL",
    "WEB_BASE_URL",
    "ALTRUIST_BASE_URL",
    "ALTRUIST_CLIENT_ID",
    "ALTRUIST_CLIENT_SECRET",
)

# ---------------------------------------------------------------------------
# Secret-safe output
# ---------------------------------------------------------------------------

_SECRET_VALUES: set[str] = set()
_TRANSCRIPT: list[str] = []


def _register_secret(value):
    """Remember a value so the scrubber can refuse to print it.

    Short values are ignored: a 2-character 'secret' would blacklist ordinary
    substrings and turn every line into [REDACTED], which hides real output
    instead of protecting anything.
    """
    if value and isinstance(value, str) and len(value.strip()) >= 8:
        _SECRET_VALUES.add(value.strip())


def _scrub(text):
    for v in _SECRET_VALUES:
        if v in text:
            text = text.replace(v, "<REDACTED>")
    # Belt and braces: a Postgres DSN password, even one never registered.
    text = re.sub(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+@", r"\1<REDACTED>@", text)
    return text


def _emit(line):
    line = _scrub(str(line))
    _TRANSCRIPT.append(line)
    print(line, flush=True)


def _ok(msg):
    _emit(f"[PASS] {msg}")


def _fail(msg):
    _emit(f"[FAIL] {msg}")


def _blocked(msg):
    _emit(f"[BLOCKED] {msg}")


def _info(msg):
    _emit(f"        {msg}")


def _head(msg):
    _emit("")
    _emit(msg)


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------

_DOTENV_NAMES: set[str] = set()


def _load_dotenv():
    """Populate os.environ from apps/api/.env for keys not already set.

    Records the NAMES it saw (never the values) and registers the values with
    the scrubber.
    """
    envp = os.path.join(_API_ROOT, ".env")
    if not os.path.exists(envp):
        return
    with open(envp) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            _DOTENV_NAMES.add(k)
            _register_secret(v)
            os.environ.setdefault(k, v)


def _doppler_bin():
    return shutil.which("doppler") or (
        os.path.expanduser("~/.local/bin/doppler")
        if os.path.exists(os.path.expanduser("~/.local/bin/doppler"))
        else None
    )


# ---------------------------------------------------------------------------
# Assertion 1 — Task 1 discovery findings, names only
# ---------------------------------------------------------------------------


def _parse_render_yaml():
    """Return {service_name: set(env keys)} without requiring PyYAML."""
    declared, svc = {}, None
    with open(RENDER_YAML) as fh:
        for line in fh:
            if re.match(r"\s*#", line):
                continue
            n = re.match(r"\s*name:\s*(\S+)", line)
            k = re.match(r"\s*-\s*key:\s*([A-Z_][A-Z0-9_]*)", line)
            if n:
                svc = n.group(1)
                declared.setdefault(svc, set())
            elif k and svc:
                declared[svc].add(k.group(1))
    return declared


def assert_1_discovery(declared):
    """[1] Report Task 1's four findings explicitly — NAMES only, never values."""
    _head("=== [1] Task 1 discovery findings (names only) ===")
    ok = True

    # 1a — API service inventory.
    api_keys = sorted(declared.get("2ndactcapital-api", set()))
    _info(f"1a. API secrets in use ({len(api_keys)} declared in render.yaml):")
    for k in api_keys:
        _info(f"      {k}")
    _info("    local apps/api/.env names: " + ", ".join(sorted(_DOTENV_NAMES) or ["<none>"]))
    _info("    tooling-only (never deployed): APP_SERVICE_DATABASE_URL, "
          "SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, AUTH0_MGMT_CLIENT_ID, "
          "AUTH0_MGMT_CLIENT_SECRET, R2_SOURCE_BUCKET")
    if not api_keys:
        _fail("1a. no API env keys parsed from render.yaml")
        ok = False

    # 1b — frontend inventory.
    web_keys = sorted(declared.get("2ndactcapital-web", set()))
    _info(f"1b. Frontend secrets ({len(web_keys)}):")
    for k in web_keys:
        _info(f"      {k}")
    if "NEXT_PUBLIC_API_URL" not in web_keys:
        _fail("1b. NEXT_PUBLIC_API_URL missing from the frontend inventory")
        ok = False
    else:
        _info("    NEXT_PUBLIC_API_URL is BUILD-time (inlined into the client "
              "bundle) — a Doppler change needs a redeploy, not a restart.")

    # 1c — declared-vs-used gap.
    gap = [k for k in GAP_KEYS_REQUIRED if k not in declared.get("2ndactcapital-api", set())]
    if gap:
        _fail(f"1c. render.yaml gap still OPEN: {', '.join(gap)}")
        ok = False
    else:
        _info("1c. render.yaml declared-vs-used gap: CLOSED "
              f"({', '.join(GAP_KEYS_REQUIRED)}, EDGAR_USER_AGENT added).")

    # 1d — Doppler CLI installability / token-based fetch.
    dbin = _doppler_bin()
    if dbin:
        try:
            ver = subprocess.run([dbin, "--version"], capture_output=True,
                                 text=True, timeout=30).stdout.strip()
            _info(f"1d. Doppler CLI: INSTALLED ({ver}) at {dbin}")
        except Exception as exc:
            _info(f"1d. Doppler CLI present but not runnable: {type(exc).__name__}")
    else:
        _info("1d. Doppler CLI: NOT INSTALLED "
              "(installable — github.com/DopplerHQ/cli releases, verified reachable)")
    _info("    DOPPLER_TOKEN: " +
          ("PRESENT (value never read or printed)" if os.environ.get("DOPPLER_TOKEN")
           else "ABSENT"))
    _info("    A token-scoped fetch is testable without exposure via "
          "`doppler secrets --only-names`, which returns NAMES only.")

    if ok:
        _ok("[1] Task 1 findings reported (1a/1b/1c/1d), names only, no values.")
    return ok


# ---------------------------------------------------------------------------
# Assertion 2 — live DB-dependent request on Render
# ---------------------------------------------------------------------------


def _http(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "verify_doppler/1.0"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}".encode()


def assert_2_render_db():
    """[2] A real DB-dependent request succeeds via the Doppler value on Render."""
    _head("=== [2] Live DB-dependent request on Render (Doppler-sourced) ===")

    base = os.environ.get("RENDER_API_URL", "").rstrip("/")
    if not base:
        _blocked("[2] RENDER_API_URL not set — the deployed API host is unknown "
                 "to this environment, so no live request can be aimed at it.")
        _info("    Note: https://api.2ndactcapital.com is an Auth0 AUDIENCE "
              "identifier, not a resolvable host (DNS: NXDOMAIN). Do not use it "
              "as an API base URL.")
        return False

    if not os.environ.get("DOPPLER_TOKEN"):
        _blocked("[2] Cannot attribute a live response to a DOPPLER-SOURCED value: "
                 "no DOPPLER_TOKEN, so the Render integration's state is "
                 "unverifiable. A 200 here would only prove the service is up, "
                 "not that Doppler supplied DATABASE_URL.")
        return False

    # A DB-dependent endpoint: it must touch Postgres, so a 200 proves the
    # DATABASE_URL the service is running with actually connects.
    status, body = _http(f"{base}/api/v1/theme")
    if status == 200:
        _ok(f"[2] {base}/api/v1/theme -> 200; the response is read from the "
            "config table, so DATABASE_URL resolved and connected.")
        return True
    _fail(f"[2] {base}/api/v1/theme -> {status}. "
          f"{_scrub(body[:160].decode('utf-8', 'replace'))}")
    return False


# ---------------------------------------------------------------------------
# Assertion 3 — live frontend request on Vercel
# ---------------------------------------------------------------------------


def assert_3_vercel_frontend():
    """[3] A real frontend request succeeds via the Doppler value on Vercel."""
    _head("=== [3] Live frontend request on Vercel (Doppler-sourced) ===")

    base = os.environ.get("VERCEL_APP_URL", DEFAULT_VERCEL_APP_URL).rstrip("/")
    status, body = _http(base)
    if status != 200:
        _fail(f"[3] {base} -> {status}")
        return False

    html = body.decode("utf-8", "replace")
    _info(f"    {base} -> 200 ({len(html)} bytes)")

    if not os.environ.get("DOPPLER_TOKEN"):
        _blocked("[3] The frontend is live and serving, but this cannot be "
                 "attributed to a DOPPLER-SOURCED NEXT_PUBLIC_API_URL: with no "
                 "DOPPLER_TOKEN the Vercel integration's state is unverifiable. "
                 "Rendering proves the current build works, not its source.")
        _info("    Additionally: NEXT_PUBLIC_API_URL is inlined at BUILD time, so "
              "even a correct integration only takes effect after a redeploy — "
              "this assertion must run against a build made post-integration.")
        return False

    # The root layout fetches the tenant theme server-side through
    # NEXT_PUBLIC_API_URL. Tenant-specific values in the HTML therefore prove
    # that fetch reached the API.
    if "--2a-navy" in html and "1B2B4B" in html:
        _ok(f"[3] {base} rendered tenant theme values server-side — the layout's "
            "fetch through NEXT_PUBLIC_API_URL reached the API.")
        return True
    _fail("[3] page rendered but carries no tenant theme values; the server-side "
          "theme fetch through NEXT_PUBLIC_API_URL did not succeed.")
    return False


# ---------------------------------------------------------------------------
# Assertion 4 — `doppler run --` locally, proven by a real query
# ---------------------------------------------------------------------------


async def _try_connect(dsn):
    """Return (ok, detail). Detail NEVER contains the DSN."""
    try:
        import asyncpg
    except ImportError:
        return False, "asyncpg unavailable"
    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=30)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {_scrub(str(exc))[:120]}"
    try:
        who = await conn.fetchval("SELECT current_user")
        n = await conn.fetchval("SELECT count(*) FROM organizations")
        return True, f"role={who} organizations={n}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {_scrub(str(exc))[:120]}"
    finally:
        await conn.close()


def assert_4_doppler_run_local():
    """[4] `doppler run --` connects with the SAME credential Render uses.

    Proven by executing a real query through the injected value — never by
    reading and comparing the values, which would require handling them.
    """
    _head("=== [4] Local `doppler run --` connects (real query, no value compare) ===")

    dbin = _doppler_bin()
    if not dbin:
        _blocked("[4] Doppler CLI not installed — cannot run `doppler run --`.")
        return False
    if not os.environ.get("DOPPLER_TOKEN"):
        _blocked("[4] No DOPPLER_TOKEN and no configured Doppler scope. "
                 "`doppler run --` cannot fetch a config, so the drift fix is "
                 "NOT yet in force.")
        # Report the interim state honestly — this is the failure the sprint
        # exists to fix, and it is still live.
        dsn = os.environ.get("APP_SERVICE_DATABASE_URL")
        if dsn:
            ok, detail = asyncio.run(_try_connect(dsn))
            if ok:
                _info(f"    interim: local APP_SERVICE_DATABASE_URL connects ({detail})")
            else:
                _info(f"    interim: local APP_SERVICE_DATABASE_URL FAILS TO CONNECT "
                      f"— {detail}")
                _info("    This is the exact recurring drift: the variable is SET, so "
                      "every `if APP_SERVICE_DATABASE_URL:` guard passes, but the "
                      "CONNECTION fails and callers fall back to SET LOCAL ROLE — "
                      "a weaker test reported as green.")
        return False

    # Run a real query inside `doppler run --`, so the DSN only ever exists in
    # the child process's environment and is never read by this script.
    child = (
        "import asyncio,os,sys\n"
        f"sys.path[:0]={sys.path[:4]!r}\n"
        "import asyncpg\n"
        "async def m():\n"
        "    d=os.environ.get('DATABASE_URL')\n"
        "    if not d: print('NO_DATABASE_URL'); return\n"
        "    c=await asyncpg.connect(d,statement_cache_size=0,timeout=30)\n"
        "    print('OK', await c.fetchval('select current_user'),"
        " await c.fetchval('select count(*) from organizations'))\n"
        "    await c.close()\n"
        "asyncio.run(m())\n"
    )
    try:
        r = subprocess.run([dbin, "run", "--", sys.executable, "-c", child],
                           capture_output=True, text=True, timeout=180)
    except Exception as exc:
        _fail(f"[4] `doppler run --` raised {type(exc).__name__}")
        return False

    out = _scrub((r.stdout or "").strip())
    if r.returncode == 0 and out.startswith("OK"):
        _ok(f"[4] `doppler run -- python3` executed a real query against the "
            f"Doppler-injected DATABASE_URL ({out}). Same config Render's "
            "integration reads, so a local copy cannot drift from it.")
        return True
    _fail(f"[4] `doppler run --` rc={r.returncode} out={out[:160]} "
          f"err={_scrub((r.stderr or '')[:160])}")
    return False


# ---------------------------------------------------------------------------
# Assertion 5 — render.yaml gap closed
# ---------------------------------------------------------------------------


def assert_5_render_yaml_gap(declared):
    """[5] render.yaml's AWS_* / VOYAGE_API_KEY gap is closed."""
    _head("=== [5] render.yaml AWS_* / VOYAGE_API_KEY gap ===")
    api = declared.get("2ndactcapital-api", set())
    ok = True

    missing = [k for k in GAP_KEYS_REQUIRED if k not in api]
    if missing:
        _fail(f"[5] still missing from render.yaml: {', '.join(missing)}")
        ok = False
    else:
        _info("    declared: " + ", ".join(GAP_KEYS_REQUIRED))

    regressions = [k for k in GAP_KEYS_FORBIDDEN_ON_API if k in api]
    if regressions:
        _fail(f"[5] REGRESSION — these must NOT be declared on the API service: "
              f"{', '.join(regressions)}. APP_BASE_URL/WEB_BASE_URL reintroduce "
              "the cross-tenant invite-URL leak; ALTRUIST_* has no credentials.")
        ok = False
    else:
        _info("    correctly absent: " + ", ".join(GAP_KEYS_FORBIDDEN_ON_API))

    if ok:
        _ok("[5] render.yaml gap closed, with no over-declaration regression.")
    return ok


# ---------------------------------------------------------------------------
# Assertion 6 — no secret value in this script's own output
# ---------------------------------------------------------------------------


def assert_6_no_secret_leak():
    """[6] Re-scan everything emitted against every secret value seen.

    A pure "did anything leak?" scan passes vacuously in two ways: if no
    secrets were ever registered there is nothing to find, and if the scrubber
    is broken the scan cannot tell. Both are checked first.
    """
    _head("=== [6] Secret-value leak scan over this script's own output ===")

    # Negative control A — the scan is only meaningful if it has real values to
    # look for. Zero registered secrets means "nothing was loaded", not "clean".
    if not _SECRET_VALUES:
        _fail("[6] no secret values were registered, so the leak scan proves "
              "nothing. Expected at least DATABASE_URL from the environment or "
              "apps/api/.env.")
        return False

    # Negative control B — prove the scrubber actually redacts. Feed it a real
    # registered value plus a synthetic DSN and confirm neither survives. If
    # this fails, every [PASS] above was reported through a broken filter.
    probe_secret = max(_SECRET_VALUES, key=len)
    probe = _scrub(f"canary {probe_secret} postgresql://u:hunter2@h:5432/db")
    if probe_secret in probe:
        _fail("[6] scrubber FAILED its negative control — a registered secret "
              "value survived _scrub(). The leak scan below cannot be trusted.")
        return False
    if "hunter2" in probe:
        _fail("[6] scrubber FAILED its DSN negative control — an unregistered "
              "DSN password survived _scrub().")
        return False
    _info("    negative control: scrubber redacted both a registered value and "
          "an unregistered DSN password")

    transcript = "\n".join(_TRANSCRIPT)

    leaked = [v for v in _SECRET_VALUES if v in transcript]
    if leaked:
        # Report the COUNT and the offending variable names — never the values.
        names = sorted(
            k for k in _DOTENV_NAMES
            if os.environ.get(k) and os.environ.get(k).strip() in leaked
        )
        _fail(f"[6] {len(leaked)} secret value(s) appeared in output "
              f"(variables: {', '.join(names) or 'unknown'})")
        return False

    dsn_like = re.findall(r"postgres(?:ql)?://[^:\s]+:(?!<REDACTED>)[^@\s]+@", transcript)
    if dsn_like:
        _fail(f"[6] {len(dsn_like)} unredacted DSN password(s) in output")
        return False

    _info(f"    scanned {len(_TRANSCRIPT)} emitted lines against "
          f"{len(_SECRET_VALUES)} known secret values")
    _ok("[6] No real secret value appears in this script's output or logs.")
    return True


# ---------------------------------------------------------------------------


def main():
    _load_dotenv()
    for name in ("DATABASE_URL", "APP_SERVICE_DATABASE_URL", "DOPPLER_TOKEN",
                 "ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "AWS_SECRET_ACCESS_KEY",
                 "AWS_ACCESS_KEY_ID", "SUPABASE_SERVICE_ROLE_KEY"):
        _register_secret(os.environ.get(name))

    _emit("verify_doppler.py — Doppler secrets migration verifier")
    _emit("Secrets are referenced by NAME only. No value is ever printed.")

    if not os.path.exists(RENDER_YAML):
        _fail(f"render.yaml not found at {RENDER_YAML}")
        return 1
    declared = _parse_render_yaml()

    results = [
        ("[1] Task 1 findings reported", assert_1_discovery(declared)),
        ("[2] Render DB request via Doppler", assert_2_render_db()),
        ("[3] Vercel frontend request via Doppler", assert_3_vercel_frontend()),
        ("[4] local `doppler run --` connects", assert_4_doppler_run_local()),
        ("[5] render.yaml gap closed", assert_5_render_yaml_gap(declared)),
    ]
    # [6] runs last: it scans everything the earlier assertions emitted.
    results.append(("[6] no secret value in output", assert_6_no_secret_leak()))

    _head("=== SUMMARY ===")
    passed = sum(1 for _, r in results if r)
    for label, r in results:
        _emit(f"  {'PASS   ' if r else 'BLOCKED'}  {label}")
    _emit(f"  {passed}/{len(results)} assertions passed")

    if passed != len(results):
        _emit("")
        _emit("NOT COMPLETE. The Doppler migration is not finished — see")
        _emit("docs/DEVELOPMENT_ENVIRONMENT.md §7 for what is missing.")
        _emit("Blocked legs are reported as BLOCKED, never as PASS.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
