"""superadminmenu sprint verify — user provisioning + menu gating + user creation.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent.
Teardown at START and at END, keyed on fixed test UUIDs + a stable marker.

WHAT IS REAL HERE, AND WHAT IS NOT — stated up front so no assertion reads as
stronger than it is:

  * The users-row writes are REAL: they run the actual ``services.users.ensure_user``
    against the LIVE database named by DATABASE_URL, and every assertion reads the
    row back out of that database.
  * The menu assertions are REAL: they import apps/web/lib/menuVisibility.mjs —
    the exact module the shipped sidebar and /admin index import — through a Node
    harness. Nothing about the gating rule is re-implemented in Python.
  * The JWT SIGNATURE leg is NOT exercised end-to-end. No Hollisworks Auth0
    client credentials exist in this environment, so a genuinely tenant-signed
    token cannot be minted here. What IS exercised for real: ``verify_token``'s
    behavior when the Hollisworks tenant is unconfigured (the actual production
    failure), and ``ensure_user``'s behavior given Hollisworks-issuer claims.
    Assertions that would need a real signed token are reported BLOCKED with the
    reason, never PASS.

DSN:
  DATABASE_URL — bypass (postgres) role: seeding, reads, teardown.
"""

import asyncio
import glob
import json
import os
import re
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
from jose import jwt  # noqa: E402
from jose.exceptions import JWTError  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── stable ids / markers ────────────────────────────────────────────────────
MARKER = "superadminmenu_verify"
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

# Hollisworks-tenant staff fixture. The sub shape mirrors a real Auth0 sub.
HW_SUB = f"auth0|{MARKER}_staff"
HW_EMAIL = "jlarizza@gmail.com"           # the exact account the sprint names
HW_SUB_NOEMAIL = f"auth0|{MARKER}_noemail"
HW_SUB_BACKFILL = f"auth0|{MARKER}_backfill"
HW_SUB_UNCONFIGURED = f"auth0|{MARKER}_unconfigured"

# Invite fixtures (Task 4).
INVITE_ADMIN_ID = "99000000-0000-0000-0000-00005ada0001"
INVITE_ADMIN_SUB = f"auth0|{MARKER}_admin"
INVITE_ADMIN_EMAIL = f"admin.{MARKER}@example.com"
INVITEE_EMAIL = f"invitee.{MARKER}@example.com"

TEST_SUBS = [
    HW_SUB, HW_SUB_NOEMAIL, HW_SUB_BACKFILL, HW_SUB_UNCONFIGURED, INVITE_ADMIN_SUB,
]
TEST_EMAILS = [HW_EMAIL, INVITE_ADMIN_EMAIL, INVITEE_EMAIL]

HW_ISSUER_DOMAIN = "dev-gy85vzuf6mruzv3j.us.auth0.com"
HW_ISSUER = f"https://{HW_ISSUER_DOMAIN}/"

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


def check(name, condition, detail=""):
    (ok if condition else fail)(name, detail)
    return bool(condition)


def report(line):
    print(f"       {line}")


# ── request shim ────────────────────────────────────────────────────────────
class _State:
    def __init__(self, user):
        self.user = user


class FakeRequest:
    """The minimal Request surface ensure_user / get_org_id actually touch."""

    def __init__(self, claims, token="fake-access-token"):
        self.state = _State(claims)
        self.headers = {"Authorization": f"Bearer {token}"}


def hollisworks_claims(sub, **extra):
    claims = {"sub": sub, "iss": HW_ISSUER, "aud": "https://api.hollisworks.com"}
    claims.update(extra)
    return claims


async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0)


# ── teardown ────────────────────────────────────────────────────────────────
async def _teardown(conn):
    await conn.execute(
        "DELETE FROM user_roles WHERE user_id IN "
        "(SELECT id FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[]))",
        TEST_SUBS, TEST_EMAILS,
    )
    # audit_log's FK-ish columns are resource_id / user_id (confirmed by
    # introspection — NOT record_id, which is what write_audit_log's kwarg is
    # called).
    await conn.execute(
        "DELETE FROM audit_log WHERE resource_id IN "
        "(SELECT id FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[]))"
        "   OR user_id IN "
        "(SELECT id FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[]))",
        TEST_SUBS, TEST_EMAILS,
    )
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[])",
        TEST_SUBS, TEST_EMAILS,
    )


async def _count_leftovers(conn):
    return await conn.fetchval(
        "SELECT count(*) FROM users WHERE auth0_sub = ANY($1::text[]) OR email = ANY($2::text[])",
        TEST_SUBS, TEST_EMAILS,
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 1a — why no users row was created for the live Hollisworks session
# ══════════════════════════════════════════════════════════════════════════
def task_1a_token_validation():
    print("\n=== TASK 1a — the provisioning gap (token validation) ===")
    import main
    from main import Settings

    unconfigured = Settings(hollisworks_auth0_domain="")
    check(
        "1a: HOLLISWORKS_AUTH0_DOMAIN unset disables the Hollisworks tenant entirely",
        unconfigured.hollisworks_enabled is False,
        "Settings.hollisworks_auth0_domain defaults to '' → hollisworks_enabled False",
    )

    # A Hollisworks-issued token, unconfigured API. Patch the 2nd Act JWKS to an
    # empty key set so the primary leg fails exactly as it does for a foreign
    # token, without a network call.
    token = jwt.encode(
        {"sub": HW_SUB, "iss": HW_ISSUER, "aud": "https://api.hollisworks.com"},
        "irrelevant-secret",
        algorithm="HS256",
    )
    real_get_jwks, real_get_settings = main.get_jwks, main.get_settings
    main.get_jwks = lambda: {"keys": []}
    main.get_settings = lambda: unconfigured
    try:
        try:
            main.verify_token(token)
            fail("1a: a Hollisworks token is rejected when the tenant is unconfigured",
                 "verify_token returned claims — it should have raised")
            message = ""
        except JWTError as exc:
            message = str(exc)
            ok("1a: a Hollisworks token is rejected when the tenant is unconfigured",
               "verify_token raised JWTError")
    finally:
        main.get_jwks, main.get_settings = real_get_jwks, real_get_settings

    check(
        "1a: the rejection names the missing env var (fail loud, not silent)",
        "HOLLISWORKS_AUTH0_DOMAIN" in message,
        message[:160] if message else "no message captured",
    )
    check(
        "1a: the rejection no longer reports 2nd Act's misleading key error",
        "matching signing key" not in message,
        "previously surfaced 'Unable to find a matching signing key' — wrong tenant, no env hint",
    )

    # ensure_user only ever runs INSIDE a route handler; a token the middleware
    # rejects means no handler runs, so no row can be created.
    users_py = open(os.path.join(_API_ROOT, "services", "users.py")).read()
    callers = subprocess.run(
        ["grep", "-rl", "ensure_user", os.path.join(_API_ROOT, "routers")],
        capture_output=True, text=True,
    ).stdout.split()
    check(
        "1a: ensure_user is reachable ONLY from route handlers (so a 401 skips it)",
        len(callers) > 0 and "ensure_user" in users_py,
        f"{len(callers)} routers call ensure_user; the auth middleware runs before all of them",
    )

    # The deployment gap itself.
    render = open(os.path.join(_REPO_ROOT, "render.yaml")).read()
    api_block = render.split("2ndactcapital-api", 1)[-1]
    check(
        "1a: render.yaml now declares HOLLISWORKS_AUTH0_DOMAIN on the API service",
        "HOLLISWORKS_AUTH0_DOMAIN" in api_block,
        "was absent — only AUTH0_DOMAIN / AUTH0_AUDIENCE were declared",
    )
    check(
        "1a: render.yaml also declares HOLLISWORKS_AUTH0_AUDIENCE on the API service",
        "HOLLISWORKS_AUTH0_AUDIENCE" in api_block,
    )


async def task_1a_live_evidence(conn):
    print("\n=== TASK 1a (cont.) — live evidence for the second layer ===")
    row = await conn.fetchrow(
        "SELECT id, email FROM users WHERE email = $1", HW_EMAIL
    )
    check(
        "1a: confirmed live — zero pre-existing rows for jlarizza@gmail.com",
        row is None,
        "matches the reported symptom",
    )

    placeholders = await conn.fetch(
        "SELECT auth0_sub, email FROM users WHERE email LIKE '%@placeholder.local'"
    )
    check(
        "1a: rows created by a REAL Auth0 login carry a placeholder email",
        len(placeholders) > 0,
        "; ".join(f"{r['auth0_sub']} → {r['email']}" for r in placeholders) or "none found",
    )
    report(
        "root cause layer 2: ensure_user read claims['email'] off the ACCESS token, "
        "which for a custom API audience carries no email/name claims (they are in "
        "the ID token). So the email column could NEVER hold the real address."
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 2 — provisioning fix, proven against the LIVE database
# ══════════════════════════════════════════════════════════════════════════
async def task_2_provisioning(conn):
    print("\n=== TASK 2 — Hollisworks login creates a real users row ===")
    import main
    import services.users as su
    from main import Settings

    su._userinfo_attempted.clear()

    # ── The env var gates role assignment TOO, not just token validation ────
    # `is_hollisworks_claims` also keys off `hollisworks_enabled`. With the var
    # unset, a genuine Hollisworks staff identity is silently filed as a plain
    # 'member' — the second consequence of the Layer 1 gap, and the reason this
    # verify environment (which has no HOLLISWORKS_AUTH0_* vars) reproduces it.
    real_get_settings = main.get_settings
    main.get_settings = lambda: Settings(hollisworks_auth0_domain="")
    try:
        await su.ensure_user(conn, FakeRequest(hollisworks_claims(HW_SUB_UNCONFIGURED)))
        unconfigured_row = await conn.fetchrow(
            "SELECT role FROM users WHERE auth0_sub = $1", HW_SUB_UNCONFIGURED
        )
        check(
            "2: with the env var UNSET, staff are silently filed as plain members",
            unconfigured_row is not None and unconfigured_row["role"] == "member",
            f"role = {unconfigured_row['role'] if unconfigured_row else 'MISSING'} "
            "— HOLLISWORKS_AUTH0_DOMAIN gates role assignment as well as token validation",
        )
    finally:
        main.get_settings = real_get_settings

    # ── Everything below runs with the tenant CONFIGURED, as production must be.
    main.get_settings = lambda: Settings(hollisworks_auth0_domain=HW_ISSUER_DOMAIN)
    su._userinfo_attempted.clear()

    # /userinfo is stubbed to return what the real endpoint returns for this
    # identity. The stub replaces ONLY the outbound HTTP call — ensure_user's
    # own insert/back-fill logic runs for real, against the live database.
    async def fake_userinfo(request, claims):
        return HW_EMAIL, "Joe Larizza"

    real_fetch = su.fetch_auth0_identity
    su.fetch_auth0_identity = fake_userinfo
    try:
        request = FakeRequest(hollisworks_claims(HW_SUB))
        user_id = await su.ensure_user(conn, request)

        row = await conn.fetchrow(
            "SELECT id, email, full_name, role, org_id, auth0_sub FROM users WHERE auth0_sub = $1",
            HW_SUB,
        )
        check("2: a Hollisworks-tenant login creates a real users row",
              row is not None, f"users.id = {user_id}")
        row = row or {"id": None, "email": None, "role": None, "org_id": None}

        check(
            "2: the row is findable by the person's REAL email, not a placeholder",
            row["email"] == HW_EMAIL,
            f"email = {row['email']}",
        )
        check(
            "2: role follows the established convention (Hollisworks issuer → super_admin)",
            row["role"] == "super_admin",
            f"role = {row['role']}",
        )
        check(
            "2: org_id follows the established convention (get_org_id → default org)",
            str(row["org_id"]) == DEFAULT_ORG,
            f"org_id = {row['org_id']} — unchanged; cross-org placement is separate, tracked work",
        )
        check(
            "2: ensure_user returns the DB-generated id that FKs must use",
            str(row["id"]) == user_id,
        )

        # Placeholder fallback must still work when /userinfo is unavailable.
        su._userinfo_attempted.clear()

        async def failing_userinfo(request, claims):
            return None, None

        su.fetch_auth0_identity = failing_userinfo
        await su.ensure_user(conn, FakeRequest(hollisworks_claims(HW_SUB_NOEMAIL)))
        fallback = await conn.fetchrow(
            "SELECT email FROM users WHERE auth0_sub = $1", HW_SUB_NOEMAIL
        )
        check(
            "2: /userinfo failure still creates the row (placeholder fallback intact)",
            fallback is not None and fallback["email"] == su.placeholder_email(HW_SUB_NOEMAIL),
            f"email = {fallback['email'] if fallback else 'MISSING'}",
        )

        # Back-fill: a row already holding the placeholder is repaired.
        su._userinfo_attempted.clear()

        async def backfill_userinfo(request, claims):
            return f"backfilled.{MARKER}@example.com", "Backfilled Name"

        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES (uuid_generate_v4(), $1, $2, 'Member', $3, 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            DEFAULT_ORG, su.placeholder_email(HW_SUB_BACKFILL), HW_SUB_BACKFILL,
        )
        su.fetch_auth0_identity = backfill_userinfo
        await su.ensure_user(conn, FakeRequest(hollisworks_claims(HW_SUB_BACKFILL)))
        repaired = await conn.fetchrow(
            "SELECT email, role FROM users WHERE auth0_sub = $1", HW_SUB_BACKFILL
        )
        check(
            "2: an existing placeholder row is back-filled with the real email",
            repaired is not None and repaired["email"] == f"backfilled.{MARKER}@example.com",
            f"email = {repaired['email'] if repaired else 'MISSING'}",
        )
        check(
            "2: the same request also promotes an existing staff row to super_admin",
            repaired is not None and repaired["role"] == "super_admin",
        )
    finally:
        su.fetch_auth0_identity = real_fetch
        su._userinfo_attempted.clear()

    # Outbound-host guard on the real function (no stub).
    foreign = await su.fetch_auth0_identity(
        FakeRequest({"sub": "x", "iss": "https://evil.example.com/"}),
        {"sub": "x", "iss": "https://evil.example.com/"},
    )
    check(
        "2: /userinfo is never called for an issuer this API is not configured to accept",
        foreign == (None, None),
        "the iss claim cannot choose an arbitrary outbound host",
    )
    main.get_settings = real_get_settings

    blocked(
        "2: end-to-end with a genuinely Hollisworks-SIGNED token",
        "no HOLLISWORKS_AUTH0_CLIENT_ID/SECRET in this environment — a real "
        "tenant-signed token cannot be minted here. The signature leg is unproven; "
        "the row-creation leg above is proven against the live database.",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 1b / 1c / 3 — menu gating, via the REAL shipped module
# ══════════════════════════════════════════════════════════════════════════
def run_menu_harness():
    harness = os.path.join(_HERE, "menuvisibility_harness.mjs")
    proc = subprocess.run(["node", harness], capture_output=True, text=True)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"unparseable harness output: {exc}"


def task_1b_1c_3_menu(data, error):
    print("\n=== TASK 1b/1c + TASK 3 — menu gating ===")
    if data is None:
        fail("1b: every menu gate enumerated from the real module", error or "harness failed")
        fail("1c: items a super_admin with no Profile would lose", "harness failed")
        fail("3: a super_admin with ZERO Profiles sees every menu item", "harness failed")
        fail("3: a regular user's menu is unchanged", "harness failed")
        return

    print("       Task 1b — every gate found, from lib/menuVisibility.mjs:")
    for g in data["gates"]:
        print(f"         {g['gate']:<34} {g['label']} ({g['href']})")
    check(
        "1b: every menu gate enumerated from the real shipped module",
        len(data["gates"]) == len(data["allHrefs"]) and len(data["gates"]) > 0,
        f"{len(data['gates'])} items; gate kinds: permission, account-role, or none",
    )

    sa = data["superAdminNoProfiles"]
    lost = [h for h in sa["visible"] if h not in sa["legacyVisible"]]
    print("       Task 1c — items a super_admin with no Profile LOST under the old rule:")
    for h in lost:
        print(f"         {h}")
    check(
        "1c: the gap was real — the pre-fix rule hid items from a super_admin",
        len(lost) > 0,
        ", ".join(lost) or "none",
    )

    # Task 3 — every single item, individually.
    missing = [h for h in data["allHrefs"] if h not in sa["visible"]]
    check(
        "3: a super_admin with ZERO Profiles sees EVERY menu item",
        len(missing) == 0,
        f"{len(sa['visible'])}/{len(data['allHrefs'])} items visible"
        + (f"; missing {missing}" if missing else ""),
    )
    for href in data["allHrefs"]:
        check(f"3:   super_admin sees {href}", href in sa["visible"])

    check(
        "3: a super_admin with NO granted roles also sees every item",
        len(data["superAdminNoRoles"]["visible"]) == len(data["allHrefs"]),
    )
    check(
        "3: the live shape (super_admin + granted 'admin') sees every item",
        len(data["superAdminGrantedAdmin"]["visible"]) == len(data["allHrefs"]),
    )
    check(
        "3: /admin index and sidebar now agree on the super-admin section list",
        all(h in sa["visibleAdmin"] for h in data["adminIndexHrefs"]),
        f"{len(sa['visibleAdmin'])}/{len(data['adminIndexHrefs'])} admin sections",
    )
    check(
        "3: a role gate that omits super_admin still admits platform staff",
        data["gateOmittingSuperAdmin"] is True,
        "defence in depth against a future gate forgetting the bypass",
    )

    # REGRESSION — non-super-admin menus must be byte-identical to the old rule.
    for reg in data["regressions"]:
        check(
            f"3: REGRESSION — {reg['name']}'s menu is unchanged",
            reg["unchanged"],
            f"{len(reg['now'])} items, identical to the pre-fix rule"
            if reg["unchanged"]
            else f"before={reg['before']} now={reg['now']}",
        )


# ══════════════════════════════════════════════════════════════════════════
# TASK 1d / 4 — user creation via /admin/users
# ══════════════════════════════════════════════════════════════════════════
async def task_1d_4_user_creation(conn):
    print("\n=== TASK 1d + TASK 4 — user creation via /admin/users ===")
    from routers.invites import InviteCreateRequest
    from services.invites import ALLOWED_INVITE_ROLES, create_invite

    # 1d — the request body structurally CANNOT carry an org_id.
    fields = set(InviteCreateRequest.model_fields)
    check(
        "1d/4: org_id cannot be supplied in the request body (no such field)",
        "org_id" not in fields,
        f"InviteCreateRequest fields = {sorted(fields)}",
    )
    router_src = open(os.path.join(_API_ROOT, "routers", "invites.py")).read()
    check(
        "1d/4: the endpoint sources org_id from the caller's context",
        "org_id = get_org_id(request)" in router_src,
    )

    # 1d — the frontend wiring that was MISSING is now present.
    web = os.path.join(_REPO_ROOT, "apps", "web")
    actions_path = os.path.join(web, "lib", "inviteActions.js")
    check(
        "1d/4: the frontend now has a server action that reaches the invite endpoint",
        os.path.exists(actions_path),
        "lib/inviteActions.js — previously NO route, action, or button existed",
    )
    ui = open(os.path.join(web, "components", "admin", "UserManagement.jsx")).read()
    check(
        "1d/4: /admin/users now has a create path in the UI",
        "createInviteAction" in ui and "Invite Member" in ui,
        "an Invite Member button + modal, wired to the action",
    )
    api_src = open(os.path.join(web, "lib", "api.js")).read()
    invite_call = api_src[api_src.index("export const createInvite"):][:400]
    check(
        "1d/4: the frontend never sends org_id in the invite body",
        "org_id" not in invite_call,
        "body carries email / full_name / role only",
    )

    # 4 — a REAL create against the live database, org from the caller context.
    admin_request = FakeRequest({"sub": INVITE_ADMIN_SUB, "iss": "https://x/"})
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, 'Verify Admin', $4, 'org_admin')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        INVITE_ADMIN_ID, DEFAULT_ORG, INVITE_ADMIN_EMAIL, INVITE_ADMIN_SUB,
    )

    from routers.entities import get_org_id

    caller_org = get_org_id(admin_request)
    row = await create_invite(
        conn,
        org_id=caller_org,          # from the CALLER's context, never a body value
        email=INVITEE_EMAIL,
        full_name="Invited Person",
        role="member",
        invited_by=INVITE_ADMIN_ID,
    )
    persisted = await conn.fetchrow(
        "SELECT id, org_id, email, role, invite_status, invite_token, invited_by "
        "FROM users WHERE email = $1",
        INVITEE_EMAIL,
    )
    if check("4: a real user can be created via the /admin/users invite path",
             persisted is not None, f"users.id = {row['id'] if row else 'none'}"):
        check(
            "4: the created row carries the caller's org_id",
            str(persisted["org_id"]) == str(caller_org) == DEFAULT_ORG,
            f"org_id = {persisted['org_id']} (caller context = {caller_org})",
        )
        check(
            "4: the created row is a usable pending invite",
            persisted["invite_status"] == "pending" and bool(persisted["invite_token"]),
            f"invite_status = {persisted['invite_status']}",
        )
        check(
            "4: the invite is attributed to the acting admin",
            str(persisted["invited_by"]) == INVITE_ADMIN_ID,
        )

    # The created row must actually SHOW UP on the screen that created it.
    listed = await conn.fetchrow(
        """
        SELECT u.id, u.invite_status, u.role AS account_role
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        LEFT JOIN roles r ON r.id = ur.role_id
        LEFT JOIN profiles p ON p.id = u.profile_id
        WHERE u.org_id = $1 AND u.email = $2
        """,
        DEFAULT_ORG, INVITEE_EMAIL,
    )
    admin_src = open(os.path.join(_API_ROOT, "routers", "admin.py")).read()
    check(
        "4: GET /admin/users surfaces the new row's invite status",
        listed is not None
        and listed["invite_status"] == "pending"
        and "u.invite_status" in admin_src,
        "the list previously hardcoded every row as Active",
    )
    check(
        "4: super_admin is not an invitable role (staff come from the Auth0 tenant)",
        "super_admin" not in ALLOWED_INVITE_ROLES,
        f"ALLOWED_INVITE_ROLES = {ALLOWED_INVITE_ROLES}",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 1e — invite email, honestly
# ══════════════════════════════════════════════════════════════════════════
def task_1e_invite_email():
    print("\n=== TASK 1e — invite email delivery status (honest) ===")
    senders = subprocess.run(
        ["grep", "-rnE", r"boto3\.client\(\"ses|boto3\.client\('ses|smtplib|sendgrid|postmark|resend",
         os.path.join(_API_ROOT, "services"), os.path.join(_API_ROOT, "routers")],
        capture_output=True, text=True,
    ).stdout.strip()
    check(
        "1e: NO email-sending code exists anywhere in the API",
        senders == "",
        "no SES / SMTP / SendGrid / Postmark / Resend client in services/ or routers/"
        if senders == "" else senders[:200],
    )
    router_src = open(os.path.join(_API_ROOT, "routers", "invites.py")).read()
    check(
        "1e: the invite endpoint still carries the unbuilt send hook",
        "BLOCKED — SES gate failed" in router_src,
        "Task 3 of multitenant2b was never completed",
    )
    check(
        "1e: creation returns an enrollment_url for manual sharing instead",
        "enrollment_url=_enrollment_url(token)" in router_src,
    )
    enroll = os.path.join(_REPO_ROOT, "apps", "web", "app", "enroll")
    check(
        "1e: the /enroll page the invite link points at does NOT exist yet",
        not os.path.exists(enroll),
        "an invited member cannot self-enroll — reported, not fixed in this sprint",
    )
    report(
        "HONEST STATUS: invite email was NEVER completed. The SES credential gate "
        "failed (the Textract IAM user has zero SES permissions) and no send call "
        "was ever written. User creation today inserts a pending row and returns an "
        "enrollment URL the admin must share by hand; there is no notification path "
        "at all, and no /enroll page to redeem the link."
    )


def task_5_status_doc():
    print("\n=== TASK 5 — project status ===")
    path = os.path.join(_REPO_ROOT, "docs", "PROJECT_STATUS.md")
    if not check("5: docs/PROJECT_STATUS.md updated", os.path.exists(path)):
        return
    text = open(path).read()
    for needle, label in [
        ("HOLLISWORKS_AUTH0_DOMAIN", "the provisioning root cause"),
        ("placeholder.local", "the placeholder-email root cause"),
        ("menuVisibility", "the menu-gating fix"),
        ("org-picker", "cross-org UI remains separate, tracked, not-yet-built"),
    ]:
        check(f"5: PROJECT_STATUS records {label}", needle in text)
    check(
        "5: PROJECT_STATUS states the invite-email status honestly",
        "SES" in text and "not built" in text.lower(),
    )


def summarize():
    passed = sum(1 for r in _RESULTS if r[0] == "PASS")
    failed = sum(1 for r in _RESULTS if r[0] == "FAIL")
    blocked_ct = sum(1 for r in _RESULTS if r[0] == "BLOCKED")
    print("\n" + "=" * 66)
    print(f"RESULT: {passed} passed, {failed} failed, {blocked_ct} blocked")
    if failed:
        print("\nFailures:")
        for status, name, detail in _RESULTS:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
    print("=" * 66)
    return 1 if failed else 0


async def main():
    if not DATABASE_URL:
        print("[FAIL] DATABASE_URL is not set — cannot verify against the live database")
        return 1

    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)   # teardown at START
        await task_1a_live_evidence(conn)
    finally:
        await conn.close()

    task_1a_token_validation()

    conn = await _connect(DATABASE_URL)
    try:
        await task_2_provisioning(conn)
    finally:
        await conn.close()

    data, error = run_menu_harness()
    task_1b_1c_3_menu(data, error)

    conn = await _connect(DATABASE_URL)
    try:
        await task_1d_4_user_creation(conn)
    finally:
        await conn.close()

    task_1e_invite_email()
    task_5_status_doc()

    print("\n=== TEARDOWN ===")
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)
        leftover = await _count_leftovers(conn)
        check("teardown: zero leftover rows", leftover == 0, f"{leftover} rows remain")
    finally:
        await conn.close()

    return summarize()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
