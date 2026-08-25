"""enrollurl sprint verify — fully-qualified invite URL + a real /enroll page.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent: teardown
runs at START and at END, keyed on fixed test UUIDs and a stable marker.

WHAT IS REAL HERE, AND WHAT IS NOT — stated up front so no assertion reads as
stronger than it is.

  REAL:
  * Every invite is created by driving the ACTUAL ``POST /api/v1/admin/invites``
    endpoint through Starlette's TestClient against the LIVE database named by
    DATABASE_URL. Nothing about URL construction is re-implemented here.
  * Every enrollment step drives the ACTUAL ``GET /api/v1/enroll/validate`` and
    ``POST /api/v1/enroll/accept`` endpoints, and every claim about a row is read
    back out of that same live database afterwards.
  * The enrollment-page messages are REAL: the harness imports
    apps/web/lib/enrollFlow.mjs — the exact module app/enroll/page.js and
    app/enroll/complete/page.js import — through Node. The expired-vs-used
    distinction is proven by calling the function, not by grepping for a string.
  * The Task-1a finding is proven against git history: the pre-sprint file is
    read with ``git show`` and asserted to contain the relative construction, so
    "the bug was real" is measured rather than asserted.

  NOT REAL (and never reported as PASS):
  * The Auth0 JWT SIGNATURE leg. ``main.verify_token`` is stubbed, because no
    Auth0 client credentials for either tenant exist in this environment. What
    IS exercised is everything downstream of a validated token: the middleware,
    org resolution, RLS context, the routers and the SQL. An assertion that
    would need a genuinely tenant-signed token is reported BLOCKED.
  * Browser rendering. The page COMPONENTS are not rendered; their decision
    logic is (via the harness), and the data they render comes from endpoints
    driven for real here.

DSN:
  DATABASE_URL — bypass (postgres) role: seeding, reads, teardown.
"""

import asyncio
import glob
import json
import os
import subprocess
import sys

# ── Make runnable via allowlisted system python3 OR venv python ─────────────
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

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── stable ids / markers ────────────────────────────────────────────────────
MARKER = "enrollurl_verify"

ORG_2A = "00000000-0000-0000-0000-000000000001"   # 2nd Act Capital (live)
ORG_HW = "bb347258-8f28-4f49-8cc9-e29ccad82884"   # Hollisworks (live)

HOST_2A = "2ndactcapital.hollisworks.com"
HOST_HW = "admin.hollisworks.com"

ADMIN_2A_ID = "99000000-0000-0000-0000-0000e0110001"
ADMIN_HW_ID = "99000000-0000-0000-0000-0000e0110002"
ADMIN_2A_SUB = f"auth0|{MARKER}_admin_2a"
ADMIN_HW_SUB = f"auth0|{MARKER}_admin_hw"
ADMIN_2A_EMAIL = f"admin.2a.{MARKER}@example.com"
ADMIN_HW_EMAIL = f"admin.hw.{MARKER}@example.com"

# Enrolling identities (the subs Auth0 would mint on signup).
SUB_ENROLLEE = f"auth0|{MARKER}_enrollee"
SUB_EXPIRED = f"auth0|{MARKER}_expired_try"
SUB_SECOND = f"auth0|{MARKER}_second_try"
SUB_CROSS = f"auth0|{MARKER}_cross_org"

TEST_USER_IDS = [ADMIN_2A_ID, ADMIN_HW_ID]
TEST_SUBS = [
    ADMIN_2A_SUB, ADMIN_HW_SUB, SUB_ENROLLEE, SUB_EXPIRED, SUB_SECOND, SUB_CROSS,
]

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def blocked(name, detail=""):
    _RESULTS.append(("BLOCKED", name, detail))
    print(f"[BLOCKED] {name}" + (f" — {detail}" if detail else ""))


def info(line):
    print(f"       {line}")


# ── teardown ────────────────────────────────────────────────────────────────
async def teardown(conn):
    """Remove every row this script can create. FK-safe order.

    ``audit_log.user_id`` and ``users.invited_by`` both reference ``users(id)``,
    so audit rows go first, then invitees (which point at an admin via
    invited_by), then the admins themselves.
    """
    await conn.execute(
        """
        DELETE FROM audit_log
        WHERE user_id IN (
            SELECT id FROM users
            WHERE email LIKE '%' || $1 || '%' OR auth0_sub = ANY($2::text[])
        )
        OR resource_id IN (
            SELECT id FROM users
            WHERE email LIKE '%' || $1 || '%' OR auth0_sub = ANY($2::text[])
        )
        """,
        MARKER, TEST_SUBS,
    )
    # Invitees first (they carry invited_by -> admin), then the admins.
    await conn.execute(
        """
        DELETE FROM users
        WHERE (email LIKE '%' || $1 || '%' OR auth0_sub = ANY($2::text[]))
          AND id <> ALL($3::uuid[])
        """,
        MARKER, TEST_SUBS, TEST_USER_IDS,
    )
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", TEST_USER_IDS)


async def leftover_count(conn) -> int:
    return await conn.fetchval(
        """
        SELECT count(*) FROM users
        WHERE email LIKE '%' || $1 || '%'
           OR auth0_sub = ANY($2::text[])
           OR id = ANY($3::uuid[])
        """,
        MARKER, TEST_SUBS, TEST_USER_IDS,
    )


async def seed_admins(conn):
    """Two real admin rows, one per live org.

    role='org_admin' with NO user_roles rows: services.rbac.has_permission
    default-allows a user who holds no roles (the documented single-admin
    posture), so manage_members passes WITHOUT making these super_admins. That
    matters for the cross-org assertions — a super_admin bypass would make them
    pass for the wrong reason.
    """
    for uid, org, sub, email in (
        (ADMIN_2A_ID, ORG_2A, ADMIN_2A_SUB, ADMIN_2A_EMAIL),
        (ADMIN_HW_ID, ORG_HW, ADMIN_HW_SUB, ADMIN_HW_EMAIL),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, $4, $5, 'org_admin')
            ON CONFLICT (id) DO NOTHING
            """,
            uid, org, email, f"Verify Admin {MARKER}", sub,
        )


# ── the app, with only the JWT SIGNATURE stubbed ────────────────────────────
_CLAIMS = {"sub": ADMIN_2A_SUB, "org_id": ORG_2A}


def _install_app():
    import main
    from starlette.testclient import TestClient

    # ONLY the signature check is replaced. Everything downstream — the RLS
    # context middleware, get_org_id, ensure_user, require_permission, the
    # routers and all SQL — runs exactly as deployed.
    main.verify_token = lambda _t: dict(_CLAIMS)
    return main, TestClient


def _as(sub, org_id):
    """Switch the identity the stubbed verify_token will report."""
    _CLAIMS.clear()
    _CLAIMS.update({"sub": sub, "org_id": org_id})


# ── main ────────────────────────────────────────────────────────────────────
async def run():
    if not DATABASE_URL:
        fail("env", "DATABASE_URL is not set — cannot verify anything against the DB")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        await teardown(conn)  # teardown-at-START
        await seed_admins(conn)

        await task1_findings(conn)
        state = await task2_urls(conn)
        await task3_enrollment(conn, state)
        await task4_expired(conn, state)
        await task5_already_accepted(conn, state)
        await task6_cross_org(conn, state)

        # Teardown at END, then prove it left nothing behind.
        await teardown(conn)
        left = await leftover_count(conn)
        if left == 0:
            ok("T7: teardown leaves zero leftover rows", "0 rows match the test marker/ids")
        else:
            fail("T7: teardown leaves zero leftover rows", f"{left} rows remain")
    finally:
        await conn.close()
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass


# ── TASK 1 — the three discovery findings, each measured ────────────────────
async def task1_findings(conn):
    print("\n=== TASK 1 — discovery findings (each asserted, not just stated) ===")

    # 1a. Where the bare relative URL was constructed. Proven against the
    #     PRE-SPRINT file in git, so this is history, not a claim.
    print("\n[1a] The bare relative enrollment_url was built in "
          "apps/api/routers/invites.py::_enrollment_url — NOT in services/invites.py.")
    try:
        old = subprocess.run(
            ["git", "show", "HEAD:apps/api/routers/invites.py"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        old = ""
        info(f"git show failed: {exc}")

    had_relative = (
        "def _enrollment_url" in old
        and 'WEB_BASE_URL' in old
        and 'f"{base}/enroll?invite_token={token}"' in old
    )
    used_twice = old.count("_enrollment_url(") >= 3  # def + 2 call sites
    if had_relative and used_twice:
        ok("T1a: pre-sprint code built a relative URL from unset env vars",
           "routers/invites.py::_enrollment_url, used at BOTH the create and list sites")
        info("      base = WEB_BASE_URL or APP_BASE_URL or ''  ->  '/enroll?invite_token=...'")
        info("      Neither env var is set in production, so base was always ''.")
        info("      APP_BASE_URL would have been WORSE than unset: it is a single shared")
        info("      value pointing at 2nd Act, so a Hollisworks invite would have been")
        info("      handed the wrong tenant's domain.")
    else:
        fail("T1a: pre-sprint code built a relative URL from unset env vars",
             f"git HEAD copy did not match expectations (relative={had_relative}, "
             f"two_sites={used_twice})")

    # And the fix: the shipped router no longer consults env vars at all.
    with open(os.path.join(_API_ROOT, "routers", "invites.py")) as fh:
        current_router = fh.read()
    if "WEB_BASE_URL" not in current_router and "APP_BASE_URL" not in current_router:
        ok("T1a: shipped router no longer consults WEB_BASE_URL/APP_BASE_URL",
           "the URL is built per-org instead")
    else:
        fail("T1a: shipped router no longer consults WEB_BASE_URL/APP_BASE_URL",
             "env-var fallback still present")

    # 1b. organizations.enroll_url — real, live, populated, and its real format.
    print("\n[1b] organizations.enroll_url IS populated for both live orgs — this is "
          "what the fix builds from.")
    rows = await conn.fetch(
        "SELECT id, name, slug, login_url, enroll_url FROM organizations "
        "WHERE id = ANY($1::uuid[]) ORDER BY name",
        [ORG_2A, ORG_HW],
    )
    for r in rows:
        info(f"{r['name']!r} (slug={r['slug']!r}) enroll_url={r['enroll_url']!r}")

    by_id = {str(r["id"]): r for r in rows}
    exp = {
        ORG_2A: f"https://{HOST_2A}/enroll",
        ORG_HW: f"https://{HOST_HW}/enroll",
    }
    bad = [
        f"{by_id[o]['slug'] if o in by_id else o}: {by_id[o]['enroll_url'] if o in by_id else 'MISSING'}"
        for o in exp
        if o not in by_id or by_id[o]["enroll_url"] != exp[o]
    ]
    if not bad:
        ok("T1b: both live orgs carry an absolute https .../enroll enroll_url",
           f"2nd Act -> {exp[ORG_2A]} ; Hollisworks -> {exp[ORG_HW]}")
        info("      NOTE: Hollisworks' row previously held a COPY of its login_url")
        info("      (https://admin.hollisworks.com/auth/login). Building from that")
        info("      faithfully would have skipped the enrollment page entirely, so the")
        info("      row was corrected to .../enroll as part of this sprint.")
    else:
        fail("T1b: both live orgs carry an absolute https .../enroll enroll_url",
             "; ".join(bad))

    # 1c. The real Auth0 signup mechanism available today.
    print("\n[1c] Enrollment = the normal per-tenant Auth0 signup flow, selected by "
          "HOST — no new tenant plumbing needed.")
    proxy_path = os.path.join(_REPO_ROOT, "apps", "web", "proxy.js")
    with open(proxy_path) as fh:
        proxy_src = fh.read()
    host_routed = (
        "getAuthClientForHost" in proxy_src
        and 'request.headers.get("host")' in proxy_src
        and "authClient.middleware(request)" in proxy_src
    )
    if host_routed:
        ok("T1c: /auth/* is already tenant-routed by Host in proxy.js",
           "getAuthClientForHost(request.headers.get('host')) -> authClient.middleware")
        info("      2ndactcapital.hollisworks.com -> the 2nd Act Auth0 tenant")
        info("      admin.hollisworks.com          -> the separate Hollisworks tenant")
        info("      Because the invite link is built from the org's OWN enroll_url, the")
        info("      invitee is already on their org's host, so the hand-off is a plain")
        info("      RELATIVE redirect to /auth/login?screen_hint=signup&login_hint=...")
        info("      and the correct tenant follows from the host — no tenant identifier")
        info("      ever travels in a URL where it could be tampered with.")
    else:
        fail("T1c: /auth/* is already tenant-routed by Host in proxy.js",
             "proxy.js does not route the auth client by Host as expected")

    # The hand-off really is relative — measured through the shipped module.
    h = node_harness()
    if h is None:
        fail("T1c: hand-off URL is relative (tenant follows from Host)",
             "node harness unavailable")
    else:
        url = h.get("signup_url") or ""
        if url.startswith("/auth/login?") and "screen_hint=signup" in url and "://" not in url:
            ok("T1c: hand-off URL is relative and opens Auth0 on Sign Up", url[:96])
        else:
            fail("T1c: hand-off URL is relative and opens Auth0 on Sign Up", url)

    blocked("T1c: end-to-end Auth0 signup against a live tenant",
            "no Auth0 client credentials for either tenant in this environment; "
            "the token-validated half is exercised with verify_token stubbed")


_HARNESS_CACHE = {}


def node_harness():
    """Run the Node harness over apps/web/lib/enrollFlow.mjs; return its JSON."""
    if "data" in _HARNESS_CACHE:
        return _HARNESS_CACHE["data"]
    try:
        proc = subprocess.run(
            ["node", os.path.join(_HERE, "enrollflow_harness.mjs")],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
        if data is None:
            print(f"       node harness failed: rc={proc.returncode} {proc.stderr[:300]}")
    except Exception as exc:  # noqa: BLE001
        print(f"       node harness error: {exc}")
        data = None
    _HARNESS_CACHE["data"] = data
    return data


# ── TASK 2 — fully-qualified URL from the org's own enroll_url ──────────────
async def task2_urls(conn):
    print("\n=== TASK 2 — the returned enrollment URL ===")
    main, TestClient = _install_app()
    state = {}

    def drive():
        out = {}
        with TestClient(main.app, raise_server_exceptions=False) as c:
            hdr = {"Authorization": "Bearer stub"}
            _as(ADMIN_2A_SUB, ORG_2A)
            out["create_2a"] = c.post(
                "/api/v1/admin/invites", headers=hdr,
                json={"email": f"invitee.2a.{MARKER}@example.com",
                      "full_name": "Invitee 2A", "role": "member"},
            )
            out["list_2a"] = c.get("/api/v1/admin/invites", headers=hdr)
            _as(ADMIN_HW_SUB, ORG_HW)
            out["create_hw"] = c.post(
                "/api/v1/admin/invites", headers=hdr,
                json={"email": f"invitee.hw.{MARKER}@example.com",
                      "full_name": "Invitee HW", "role": "member"},
            )
            # Extra invites used by the expired / cross-org tests.
            _as(ADMIN_2A_SUB, ORG_2A)
            out["create_expired"] = c.post(
                "/api/v1/admin/invites", headers=hdr,
                json={"email": f"invitee.exp.{MARKER}@example.com", "role": "member"},
            )
        return out

    out = await asyncio.to_thread(drive)

    r = out["create_2a"]
    body = r.json() if r.status_code == 201 else {}
    url = body.get("enrollment_url") or ""
    token = body.get("invite_token") or ""
    state["token_2a"] = token
    state["invite_2a_id"] = body.get("id")
    state["email_2a"] = body.get("email")

    if r.status_code != 201:
        fail("T2: create_invite returns a fully-qualified URL",
             f"HTTP {r.status_code}: {r.text[:200]}")
        return state

    expected = f"https://{HOST_2A}/enroll?invite_token={token}"
    checks = {
        "absolute": url.startswith("https://"),
        "not_relative": not url.startswith("/"),
        "org_host": f"//{HOST_2A}/" in url,
        "exact": url == expected,
    }
    if all(checks.values()):
        ok("T2: create_invite returns a fully-qualified URL on the org's real subdomain", url)
    else:
        fail("T2: create_invite returns a fully-qualified URL on the org's real subdomain",
             f"{url!r} — {checks}")

    # It is derived from the STORED enroll_url, not a hardcoded constant.
    stored = await conn.fetchval("SELECT enroll_url FROM organizations WHERE id = $1", ORG_2A)
    if url.startswith(stored + "?"):
        ok("T2: the URL is built from organizations.enroll_url", f"base={stored!r}")
    else:
        fail("T2: the URL is built from organizations.enroll_url",
             f"stored={stored!r} url={url!r}")

    # The listing endpoint had the IDENTICAL bug — check it too.
    lst = out["list_2a"]
    if lst.status_code == 200:
        urls = [i.get("enrollment_url") for i in lst.json() if i.get("invite_token")]
        rel = [u for u in urls if not (u or "").startswith("https://")]
        if urls and not rel:
            ok("T2: GET /admin/invites also returns fully-qualified URLs",
               f"{len(urls)} invite(s), all absolute")
        else:
            fail("T2: GET /admin/invites also returns fully-qualified URLs",
                 f"relative/missing: {rel[:3]}")
    else:
        fail("T2: GET /admin/invites also returns fully-qualified URLs",
             f"HTTP {lst.status_code}")

    # Hollisworks org -> its OWN host (the shared-env bug would show up here).
    rhw = out["create_hw"]
    hw_body = rhw.json() if rhw.status_code == 201 else {}
    state["token_hw"] = hw_body.get("invite_token") or ""
    state["invite_hw_id"] = hw_body.get("id")
    hw_url = hw_body.get("enrollment_url") or ""
    if rhw.status_code == 201 and hw_url == f"https://{HOST_HW}/enroll?invite_token={state['token_hw']}":
        ok("T2: a second org's invite uses THAT org's subdomain", hw_url)
    else:
        fail("T2: a second org's invite uses THAT org's subdomain",
             f"HTTP {rhw.status_code} url={hw_url!r}")

    rexp = out["create_expired"]
    exp_body = rexp.json() if rexp.status_code == 201 else {}
    state["token_expired"] = exp_body.get("invite_token") or ""
    state["invite_expired_id"] = exp_body.get("id")

    # Unit-level: the builder REFUSES to emit a relative link rather than
    # degrading — the degradation is the bug.
    from services.invites import EnrollmentUrlError, build_enrollment_url

    refused = []
    for bad in (None, "", "/enroll", "enroll?x=1", "ftp://x/enroll"):
        try:
            got = build_enrollment_url(bad, "TOK")
            refused.append(f"{bad!r} -> {got!r} (NOT refused)")
        except EnrollmentUrlError:
            pass
    derived = build_enrollment_url(None, "TOK", slug="acme")
    merged = build_enrollment_url("https://x.example.com/enroll?ref=a", "TOK")
    if (
        not refused
        and derived == "https://acme.hollisworks.com/enroll?invite_token=TOK"
        and merged == "https://x.example.com/enroll?ref=a&invite_token=TOK"
    ):
        ok("T2: builder fails loud on a non-absolute base and merges an existing query",
           f"slug-derived={derived} ; merged={merged}")
    else:
        fail("T2: builder fails loud on a non-absolute base and merges an existing query",
             f"not refused: {refused}; derived={derived!r}; merged={merged!r}")

    return state


# ── TASK 3 — a real, valid token walks through /enroll ──────────────────────
async def task3_enrollment(conn, state):
    print("\n=== TASK 3/4 — a real valid token completes enrollment ===")
    token = state.get("token_2a")
    if not token:
        fail("T3: valid token completes enrollment", "no invite token from Task 2")
        return
    main, TestClient = _install_app()

    def drive():
        out = {}
        with TestClient(main.app, raise_server_exceptions=False) as c:
            # The page's first call — PUBLIC, no Authorization header at all.
            out["validate"] = c.get(
                "/api/v1/enroll/validate",
                params={"invite_token": token, "host": HOST_2A},
            )
            # Then the post-Auth0 claim, as the NEW identity.
            _as(SUB_ENROLLEE, ORG_2A)
            out["accept"] = c.post(
                "/api/v1/enroll/accept",
                headers={"Authorization": "Bearer stub"},
                json={"invite_token": token, "host": HOST_2A},
            )
        return out

    out = await asyncio.to_thread(drive)

    v = out["validate"]
    vb = v.json() if v.status_code == 200 else {}
    if v.status_code == 200 and vb.get("status") == "valid" and vb.get("valid") is True:
        ok("T3: /enroll/validate accepts a real token WITHOUT a bearer token (pre-auth)",
           f"org={vb.get('org_name')!r} email={vb.get('email')!r}")
    else:
        fail("T3: /enroll/validate accepts a real token WITHOUT a bearer token (pre-auth)",
             f"HTTP {v.status_code} body={vb}")

    a = out["accept"]
    ab = a.json() if a.content else {}
    if a.status_code == 200 and ab.get("status") == "valid":
        ok("T3: /enroll/accept completes enrollment", f"user_id={ab.get('user_id')}")
    else:
        fail("T3: /enroll/accept completes enrollment", f"HTTP {a.status_code} body={ab}")

    # The proof that matters: read the row back out of the live database.
    row = await conn.fetchrow(
        "SELECT id, org_id, email, full_name, role, auth0_sub, invite_status "
        "FROM users WHERE invite_token = $1",
        token,
    )
    if row is None:
        fail("T3: the pending row was updated (accepted + auth0_sub linked)", "row vanished")
        return
    checks = {
        "invite_status": row["invite_status"] == "accepted",
        "auth0_sub": row["auth0_sub"] == SUB_ENROLLEE,
        "org_unchanged": str(row["org_id"]) == ORG_2A,
        "email_unchanged": row["email"] == state.get("email_2a"),
        "role_unchanged": row["role"] == "member",
    }
    if all(checks.values()):
        ok("T3: the pending row was updated (accepted + auth0_sub linked)",
           f"invite_status={row['invite_status']!r} auth0_sub={row['auth0_sub']!r} "
           f"org/email/role preserved")
    else:
        fail("T3: the pending row was updated (accepted + auth0_sub linked)", str(checks))

    # MATCH, DON'T DUPLICATE — exactly one row for that sub.
    n = await conn.fetchval("SELECT count(*) FROM users WHERE auth0_sub = $1", SUB_ENROLLEE)
    if n == 1:
        ok("T3: match-don't-duplicate — exactly ONE users row for the new auth0_sub",
           "the pending row was claimed, not shadowed by a second row")
    else:
        fail("T3: match-don't-duplicate — exactly ONE users row for the new auth0_sub",
             f"{n} rows carry that sub")

    # The page really does offer the signup hand-off for a valid token.
    h = node_harness()
    if h and h["presentations"]["valid"]["action"] == "signup":
        ok("T3: the /enroll page offers the Auth0 signup hand-off for a valid token",
           f"action={h['presentations']['valid']['action']!r} "
           f"label={h['presentations']['valid']['actionLabel']!r}")
    else:
        fail("T3: the /enroll page offers the Auth0 signup hand-off for a valid token",
             "harness unavailable or action != signup")

    # And the page files that render it actually exist.
    missing = [
        p for p in (
            "apps/web/app/enroll/page.js",
            "apps/web/app/enroll/complete/page.js",
            "apps/web/components/EnrollShell.jsx",
            "apps/web/lib/enrollFlow.mjs",
        )
        if not os.path.exists(os.path.join(_REPO_ROOT, p))
    ]
    if not missing:
        ok("T3: the /enroll route exists", "page.js + complete/page.js + shell + flow module")
    else:
        fail("T3: the /enroll route exists", f"missing: {missing}")


# ── TASK 4a — an expired token ──────────────────────────────────────────────
async def task4_expired(conn, state):
    print("\n=== TASK 4 — an expired token shows a clear, distinct message ===")
    token = state.get("token_expired")
    if not token:
        fail("T4: expired token", "no invite token from Task 2")
        return

    # Age it out using the DATABASE clock, the same clock the check uses.
    await conn.execute(
        "UPDATE users SET invite_expires_at = now() - interval '1 day' WHERE invite_token = $1",
        token,
    )
    main, TestClient = _install_app()

    def drive():
        out = {}
        with TestClient(main.app, raise_server_exceptions=False) as c:
            out["validate"] = c.get("/api/v1/enroll/validate",
                                    params={"invite_token": token, "host": HOST_2A})
            _as(SUB_EXPIRED, ORG_2A)
            out["accept"] = c.post(
                "/api/v1/enroll/accept",
                headers={"Authorization": "Bearer stub"},
                json={"invite_token": token, "host": HOST_2A},
            )
        return out

    out = await asyncio.to_thread(drive)
    vb = out["validate"].json()
    state["msg_expired"] = vb.get("message")

    if vb.get("status") == "expired" and vb.get("valid") is False and vb.get("message"):
        ok("T4: an expired token reports status='expired' with its own message",
           vb["message"][:110])
    else:
        fail("T4: an expired token reports status='expired' with its own message", str(vb))

    a = out["accept"]
    ab = a.json() if a.content else {}
    if a.status_code == 400 and ab.get("status") == "expired":
        ok("T4: /enroll/accept REFUSES an expired token", f"HTTP 400 — {ab.get('message','')[:80]}")
    else:
        fail("T4: /enroll/accept REFUSES an expired token", f"HTTP {a.status_code} body={ab}")

    # Nothing was written.
    row = await conn.fetchrow(
        "SELECT auth0_sub, invite_status FROM users WHERE invite_token = $1", token)
    if row and row["auth0_sub"] is None and row["invite_status"] == "pending":
        ok("T4: the expired invite row is untouched by the refused attempt",
           "auth0_sub still NULL, invite_status still 'pending'")
    else:
        fail("T4: the expired invite row is untouched by the refused attempt", str(dict(row or {})))

    h = node_harness()
    if h:
        p = h["presentations"]["expired"]
        if p["title"] and p["action"] == "none" and p["tone"] == "error":
            ok("T4: the page renders an expired-specific heading and offers no dead button",
               f"{p['title']!r}")
        else:
            fail("T4: the page renders an expired-specific heading and offers no dead button", str(p))


# ── TASK 4b — an already-accepted token ─────────────────────────────────────
async def task5_already_accepted(conn, state):
    print("\n=== TASK 4 — an already-used token shows a clear, DISTINCT message ===")
    token = state.get("token_2a")
    if not token:
        fail("T5: already-accepted token", "no invite token from Task 2")
        return
    main, TestClient = _install_app()

    def drive():
        out = {}
        with TestClient(main.app, raise_server_exceptions=False) as c:
            out["validate"] = c.get("/api/v1/enroll/validate",
                                    params={"invite_token": token, "host": HOST_2A})
            # A DIFFERENT identity trying to reuse the spent link.
            _as(SUB_SECOND, ORG_2A)
            out["accept"] = c.post(
                "/api/v1/enroll/accept",
                headers={"Authorization": "Bearer stub"},
                json={"invite_token": token, "host": HOST_2A},
            )
        return out

    out = await asyncio.to_thread(drive)
    vb = out["validate"].json()
    msg_accepted = vb.get("message")

    if vb.get("status") == "accepted" and vb.get("valid") is False and msg_accepted:
        ok("T5: an already-used token reports status='accepted' with its own message",
           msg_accepted[:110])
    else:
        fail("T5: an already-used token reports status='accepted' with its own message", str(vb))

    # DISTINCT, not just non-empty — the whole point of the requirement.
    msg_expired = state.get("msg_expired")
    if msg_accepted and msg_expired and msg_accepted != msg_expired:
        ok("T5: the expired and already-used messages are DISTINCT",
           "the two states give opposite advice (get a new invite vs. just sign in)")
    else:
        fail("T5: the expired and already-used messages are DISTINCT",
             f"expired={msg_expired!r} accepted={msg_accepted!r}")

    a = out["accept"]
    ab = a.json() if a.content else {}
    if a.status_code == 409 and ab.get("status") == "accepted":
        ok("T5: /enroll/accept REFUSES a spent token", f"HTTP 409 — {ab.get('message','')[:80]}")
    else:
        fail("T5: /enroll/accept REFUSES a spent token", f"HTTP {a.status_code} body={ab}")

    # The first enrollee keeps the account — a second person cannot take it over.
    row = await conn.fetchrow(
        "SELECT auth0_sub FROM users WHERE invite_token = $1", token)
    if row and row["auth0_sub"] == SUB_ENROLLEE:
        ok("T5: the spent invite still belongs to the first enrollee",
           "the second identity did not take over the row")
    else:
        fail("T5: the spent invite still belongs to the first enrollee", str(dict(row or {})))

    n = await conn.fetchval("SELECT count(*) FROM users WHERE auth0_sub = $1", SUB_SECOND)
    if n == 0:
        ok("T5: the refused second attempt created no user row", "0 rows for that sub")
    else:
        fail("T5: the refused second attempt created no user row", f"{n} rows")

    h = node_harness()
    if h:
        pa, pe = h["presentations"]["accepted"], h["presentations"]["expired"]
        if pa["title"] != pe["title"] and pa["action"] == "login" and pe["action"] == "none":
            ok("T5: the page's used-vs-expired states differ in heading AND next step",
               f"used -> {pa['actionLabel']!r}; expired -> no action")
        else:
            fail("T5: the page's used-vs-expired states differ in heading AND next step",
                 f"{pa} vs {pe}")


# ── TASK 4c — cross-org ─────────────────────────────────────────────────────
async def task6_cross_org(conn, state):
    print("\n=== TASK 4 — cross-org: generation and redemption are org-bound ===")
    token_hw = state.get("token_hw")
    invite_hw_id = state.get("invite_hw_id")
    if not token_hw:
        fail("T6: cross-org", "no Hollisworks invite from Task 2")
        return
    main, TestClient = _install_app()

    def drive():
        out = {}
        with TestClient(main.app, raise_server_exceptions=False) as c:
            hdr = {"Authorization": "Bearer stub"}
            # (a) An admin cannot mint a link into ANOTHER org by putting an
            #     org_id in the body — org_id comes from the caller's context.
            _as(ADMIN_2A_SUB, ORG_2A)
            out["body_org"] = c.post(
                "/api/v1/admin/invites", headers=hdr,
                json={"email": f"invitee.bodyorg.{MARKER}@example.com",
                      "role": "member", "org_id": ORG_HW},
            )
            # (b) Org A's admin cannot revoke org B's invite.
            out["revoke"] = c.post(
                f"/api/v1/admin/invites/{invite_hw_id}/revoke", headers=hdr)
            # (c) Org B's token opened on org A's host.
            out["validate_wrong_host"] = c.get(
                "/api/v1/enroll/validate",
                params={"invite_token": token_hw, "host": HOST_2A})
            # (d) …and refused at the write.
            _as(SUB_CROSS, ORG_2A)
            out["accept_wrong_host"] = c.post(
                "/api/v1/enroll/accept", headers=hdr,
                json={"invite_token": token_hw, "host": HOST_2A})
            # (e) The real boundary: even with the caller CLAIMING org A, a
            #     successful redemption binds to the token's OWN org.
            out["accept_right"] = c.post(
                "/api/v1/enroll/accept", headers=hdr,
                json={"invite_token": token_hw, "host": HOST_HW})
        return out

    out = await asyncio.to_thread(drive)

    r = out["body_org"]
    if r.status_code == 201:
        created = await conn.fetchrow(
            "SELECT org_id FROM users WHERE id = $1", r.json()["id"])
        url = r.json().get("enrollment_url") or ""
        if str(created["org_id"]) == ORG_2A and f"//{HOST_2A}/" in url:
            ok("T6: an org_id in the request body is IGNORED — the invite lands in the "
               "caller's own org", f"org_id={created['org_id']} url={url[:64]}…")
        else:
            fail("T6: an org_id in the request body is IGNORED",
                 f"org_id={created['org_id']} url={url!r}")
    else:
        fail("T6: an org_id in the request body is IGNORED", f"HTTP {r.status_code}")

    rv = out["revoke"]
    if rv.status_code == 404:
        ok("T6: org A's admin cannot revoke org B's invite", "HTTP 404")
    else:
        fail("T6: org A's admin cannot revoke org B's invite", f"HTTP {rv.status_code}")

    vw = out["validate_wrong_host"]
    vwb = vw.json()
    if vwb.get("status") == "wrong_tenant" and vwb.get("valid") is False:
        ok("T6: org B's token opened on org A's host reports 'wrong_tenant'",
           vwb.get("message", "")[:100])
    else:
        fail("T6: org B's token opened on org A's host reports 'wrong_tenant'", str(vwb))

    aw = out["accept_wrong_host"]
    awb = aw.json() if aw.content else {}
    if aw.status_code == 403 and awb.get("status") == "wrong_tenant":
        ok("T6: redeeming org B's token on org A's host is REFUSED", "HTTP 403")
    else:
        fail("T6: redeeming org B's token on org A's host is REFUSED",
             f"HTTP {aw.status_code} body={awb}")

    ar = out["accept_right"]
    arb = ar.json() if ar.content else {}
    row = await conn.fetchrow(
        "SELECT org_id, auth0_sub, invite_status FROM users WHERE invite_token = $1", token_hw)
    # The caller's claims said org_id=ORG_2A throughout; the row must still be
    # Hollisworks', because the org comes from the invite, never the caller.
    if (
        ar.status_code == 200
        and row is not None
        and str(row["org_id"]) == ORG_HW
        and row["auth0_sub"] == SUB_CROSS
        and row["invite_status"] == "accepted"
    ):
        ok("T6: redemption binds the account to the TOKEN's org, not the caller's claimed org",
           f"caller claimed org_id={ORG_2A}, row org_id={row['org_id']} (Hollisworks)")
    else:
        fail("T6: redemption binds the account to the TOKEN's org, not the caller's claimed org",
             f"HTTP {ar.status_code} body={arb} row={dict(row or {})}")


def summarize():
    p = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    f = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    b = sum(1 for s, _, _ in _RESULTS if s == "BLOCKED")
    print("\n" + "=" * 72)
    print(f"enrollurl verify: {p} passed, {f} failed, {b} blocked")
    if f:
        print("\nFailures:")
        for s, n, d in _RESULTS:
            if s == "FAIL":
                print(f"  - {n}: {d}")
    print("=" * 72)
    return 1 if f else 0


if __name__ == "__main__":
    asyncio.run(run())
    sys.exit(summarize())
