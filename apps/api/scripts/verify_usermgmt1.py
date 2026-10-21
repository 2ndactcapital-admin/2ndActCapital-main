"""verify_usermgmt1 — People & Access: one row per person, sign-in methods,
admin actions, pending contact email, test-account hygiene (USERMGMT1).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. Secrets are hydrated from Doppler over HTTPS (``_db_connect``)
BEFORE ``main`` is imported. AUTHZ_TEST_MODE=1 is set first (AUTHZ1 rule).

Auth0 is mocked ENTIRELY: the JWT leg (``main.verify_token`` maps the bearer
string to fixture claims), ``/userinfo`` (``services.users.
fetch_auth0_profile``) and ``/dbconnections/change_password``
(``services.user_admin.post_change_password`` records the request). SES is
never reached: resend is called with ``send_email: false`` and
``services.email.send_email`` is replaced by a recorder that fails the run if
called. Everything after the token — middleware, Principal, every gate and
handler — is the real app.

Fixtures live in two fixture orgs (99000000-…-0000000b1101 / b1102). Every
fixture user is created EXPLICITLY with the auth0_sub its mocked token carries
and ``is_test = true``, except the pending sign-ins, which the real
provisioning path creates (then ``services.test_accounts.mark_test_user``
flags them). Teardown runs at start and end, FK child-before-parent read from
pg_constraint, then every counted table is re-read for its exact pre-test
count, and the real (non-fixture) users are compared by count + checksum.

Sections (each wrapped: an exception is a [FAIL], never a crash):
  Task 1 findings · one row per person (reproduced first) · sign-in fields ·
  filters · password reset · resend invite · link pending · edit email ·
  approve/reject/retire gates · pending contact email · test hygiene ·
  front end · teardown · regression.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import tempfile
import traceback
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate  # noqa: E402

hydrate()  # BEFORE main is imported: Settings() reads HOLLISWORKS_AUTH0_* once

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

BEFORE_REF = "f115737"  # main before USERMGMT1
PURGE_RECORD = REPO / "docs" / "USERMGMT1_FIXTURE_PURGE.json"
TAG = "usermgmt1verify"
SUB_LIKE = f"%{TAG}%"
EMAIL_LIKE = f"{TAG}%"

ORG_A = UUID("99000000-0000-0000-0000-0000000b1101")
ORG_B = UUID("99000000-0000-0000-0000-0000000b1102")
FIXTURE_ORGS = [ORG_A, ORG_B]


def _u(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000b11{n:02d}")


U_ADMIN = _u(10)        # org admin: admin role (manage_members) + org_admin grant
U_MEMBER = _u(11)       # member role only — the refused caller
U_SUPER = _u(12)        # users.role super_admin
U_TWOROLES = _u(13)     # member + investment_staff; database login + a linked Google login
U_GOOGLE = _u(14)       # Google login only
U_PH = _u(15)           # database login, placeholder email
U_INVITED = _u(16)      # invited, never signed in, real email
U_INVITED2 = _u(17)     # invited, never signed in — link target with NULL sub
U_NOEMAIL = _u(18)      # never signed in, no real email
U_PLAT_PH = _u(19)      # Platform-tenant identity, placeholder email
U_PLAT_DB = _u(20)      # Platform-tenant database identity, real email
U_RETIRED = _u(21)      # status retired
U_DEACT = _u(22)        # is_active false
U_VISIBLE = _u(23)      # NOT is_test — proves the default list is not empty
U_RETIRE_ME = _u(24)    # retired by DELETE in the gate section
U_ORGADMIN_ONLY = _u(25)  # org_admin grant only (manage_org_settings) — view-only on edits
U_OTHER = _u(31)        # org B member
U_ORPHAN = _u(40)       # hygiene mechanism proof
U_ORPHAN2 = _u(41)

NAMES = {
    U_ADMIN: "admin", U_MEMBER: "member", U_SUPER: "super", U_TWOROLES: "tworoles", U_GOOGLE: "google",
    U_PH: "ph", U_INVITED: "invited", U_INVITED2: "invited2", U_NOEMAIL: "noemail", U_PLAT_PH: "platph",
    U_PLAT_DB: "platdb", U_RETIRED: "retired", U_DEACT: "deact", U_VISIBLE: "visible",
    U_RETIRE_ME: "retireme", U_ORGADMIN_ONLY: "orgadminonly", U_OTHER: "other", U_ORPHAN: "orphan",
    U_ORPHAN2: "orphan2",
}
FIXTURE_USERS = list(NAMES)

# Pending sign-ins, created by the REAL provisioning path from these subs.
P_LINK_PRIMARY = f"auth0|{TAG}-pending-a"     # → U_INVITED2 (target has no sub)
P_LINK_IDENTITY = f"auth0|{TAG}-pending-b"    # → U_MEMBER (target has a sub → user_identities)
P_REFUSED = f"auth0|{TAG}-pending-c"          # cross-org + non-super→super refused; super succeeds
P_APPROVE = f"auth0|{TAG}-pending-d"
P_REJECT = f"auth0|{TAG}-pending-e"
P_CONTACT = f"auth0|{TAG}-pending-f"          # pending page contact email
PENDING_SUBS = [P_LINK_PRIMARY, P_LINK_IDENTITY, P_REFUSED, P_APPROVE, P_REJECT, P_CONTACT]

COUNTED = ("public.users", "public.user_identities", "public.organizations", "public.roles",
           "public.user_roles", "public.role_permissions", "public.member_todos", "public.audit_log",
           "public.workflow_definitions", "public.workflow_versions")

REAL_USERS_SQL = (
    "SELECT count(*) AS n, md5(COALESCE(string_agg("
    "id::text || '|' || email || '|' || COALESCE(auth0_sub, '') || '|' || status || '|' || role || '|' || "
    "is_active::text || '|' || is_test::text || '|' || COALESCE(contact_email, ''), ',' ORDER BY id), '')) AS sum "
    "FROM users WHERE NOT is_test AND NOT (org_id = ANY($1::uuid[])) "
    "AND COALESCE(auth0_sub, '') NOT LIKE $2 AND email NOT LIKE $3"
)

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def sub_of(uid: UUID) -> str | None:
    if uid in (U_INVITED, U_INVITED2, U_NOEMAIL, U_PLAT_PH, U_PLAT_DB):
        return None
    if uid == U_GOOGLE:
        return f"google-oauth2|{TAG}-google"
    return f"auth0|{TAG}-{NAMES[uid]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{NAMES[uid]}@test.local"


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy())
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


# ═══ DB helpers (verify's own connection; RLS-scoped explicitly) ═══════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def real_users(conn) -> tuple[int, str]:
    row = await platform(conn, REAL_USERS_SQL, FIXTURE_ORGS, SUB_LIKE, EMAIL_LIKE, fetch="fetchrow")
    return int(row["n"]), row["sum"]


async def user_row(conn, uid):
    return await platform(conn, "SELECT * FROM users WHERE id = $1", uid, fetch="fetchrow")


async def id_for_sub(conn, sub):
    return await platform(conn, "SELECT id FROM users WHERE auth0_sub = $1", sub, fetch="fetchval")


async def audit_count(conn, action, resource_id) -> int:
    return int(await platform(
        conn, "SELECT count(*) FROM audit_log WHERE action = $1 AND resource_id = $2",
        action, resource_id, fetch="fetchval"))


_FK_CACHE: dict = {}


async def fk_children(conn, table: str):
    if table not in _FK_CACHE:
        _FK_CACHE[table] = await conn.fetch("""
            SELECT cn.nspname || '.' || cc.relname AS child,
                   array_agg(ca.attname ORDER BY k.ord) AS child_cols,
                   array_agg(pa.attname ORDER BY k.ord) AS parent_cols
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord) ON true
            JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
            JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.ck
            JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
            WHERE c.contype::text = 'f' AND c.confrelid = $1::regclass
            GROUP BY c.oid, cn.nspname, cc.relname""", table)
    return _FK_CACHE[table]


async def delete_tree(conn, table: str, where: str, params: list, depth=0, log=None, path=()):
    """Child-before-parent delete, FKs read from pg_constraint (recursively).
    A table already on the current path (self-reference / cycle) is skipped,
    so a cycle can only make the parent DELETE fail loudly."""
    if depth > 14:
        raise RuntimeError(f"FK recursion too deep at {table} via {' > '.join(path)}")
    path = path + (table,)
    for fk in await fk_children(conn, table):
        if fk["child"] in path:
            continue
        cc = ", ".join(f'"{c}"' for c in fk["child_cols"])
        pc = ", ".join(f'"{c}"' for c in fk["parent_cols"])
        await delete_tree(conn, fk["child"], f"({cc}) IN (SELECT {pc} FROM {table} WHERE {where})",
                          params, depth + 1, log, path)
    status = await conn.execute(f"DELETE FROM {table} WHERE {where}", *params)
    n = int(status.rsplit(" ", 1)[-1])
    if n and log is not None:
        log.append(f"{table}: {n}")


USER_WHERE = "(id = ANY($1::uuid[]) OR auth0_sub LIKE $2 OR email LIKE $3 OR org_id = ANY($4::uuid[]))"
# This run's own fixtures, NULL-safe so the NOT form keeps a row with no sub:
# seeded ids (incl. the retired deleted-*@deleted.invalid row), pending subs,
# and their <sub>@placeholder.local emails, which outlive a sub cleared by a link.
OWN_FIXTURE_WHERE = ("(id = ANY($1::uuid[]) OR COALESCE(auth0_sub, '') LIKE $2 OR email LIKE $2 "
                     "OR COALESCE(org_id = ANY($3::uuid[]), false))")


async def teardown(conn) -> list[str]:
    log: list[str] = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        ids = [r["id"] for r in await conn.fetch(
            f"SELECT id FROM users WHERE {USER_WHERE}", FIXTURE_USERS, SUB_LIKE, EMAIL_LIKE, FIXTURE_ORGS)]
        # Rows pointing at a fixture user WITHOUT an FK.
        await conn.execute("DELETE FROM member_todos WHERE related_type = 'users' AND related_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM audit_log WHERE resource_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
                           ids, FIXTURE_ORGS)
        await conn.execute("DELETE FROM user_identities WHERE auth0_sub LIKE $1 OR user_id = ANY($2::uuid[])",
                           SUB_LIKE, ids)
        await delete_tree(conn, "public.users", "id = ANY($1::uuid[])", [ids], log=log)
        await delete_tree(conn, "public.organizations", "id = ANY($1::uuid[])", [FIXTURE_ORGS], log=log)
    return log


async def leftovers(conn) -> int:
    n = await platform(conn, f"SELECT count(*) FROM users WHERE {USER_WHERE}", FIXTURE_USERS, SUB_LIKE,
                       EMAIL_LIKE, FIXTURE_ORGS, fetch="fetchval")
    n += await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                        fetch="fetchval")
    n += await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub LIKE $1", SUB_LIKE,
                        fetch="fetchval")
    n += await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS,
                        fetch="fetchval")
    return int(n)


def platform_issuer() -> str:
    from main import get_settings

    s = get_settings()
    return s.hollisworks_issuer if s.hollisworks_enabled else "https://dev-gy85vzuf6mruzv3j.us.auth0.com/"


async def seed(conn) -> dict:
    from services import rbac

    roles = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, name in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"USERMGMT1 Verify Org {name.upper()}", f"{TAG}-{name}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        for org, rname in ((ORG_A, "member"), (ORG_A, "investment_staff"), (ORG_A, "admin"), (ORG_B, "member")):
            desc, perms = C.BASELINE_ROLES[rname]
            rid = await rbac.ensure_role(conn, org, rname, desc)
            for p in perms:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid

        def email(uid):
            if uid == U_PH:
                return f"{sub_of(U_PH)}@placeholder.local"
            if uid == U_NOEMAIL:
                return f"{TAG}-noemail@placeholder.local"
            if uid == U_PLAT_PH:
                return f"auth0|{TAG}-platph@placeholder.local"
            if uid == U_VISIBLE:
                return f"{TAG}-visible@example.com"
            return email_of(uid)

        plain = [
            (U_ADMIN, ORG_A, "org_admin", "active", True), (U_MEMBER, ORG_A, "member", "active", True),
            (U_SUPER, ORG_A, "super_admin", "active", True), (U_TWOROLES, ORG_A, "member", "active", True),
            (U_GOOGLE, ORG_A, "member", "active", True), (U_PH, ORG_A, "member", "active", True),
            (U_NOEMAIL, ORG_A, "member", "active", True), (U_PLAT_PH, ORG_A, "member", "active", True),
            (U_PLAT_DB, ORG_A, "member", "active", True), (U_RETIRED, ORG_A, "member", "retired", True),
            (U_DEACT, ORG_A, "member", "active", False), (U_VISIBLE, ORG_A, "member", "active", True),
            (U_RETIRE_ME, ORG_A, "member", "active", True), (U_ORGADMIN_ONLY, ORG_A, "org_admin", "active", True),
            (U_OTHER, ORG_B, "member", "active", True),
        ]
        for uid, org, role, status, active in plain:
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                uid, org, email(uid), f"USERMGMT1 {NAMES[uid]}", sub_of(uid), role, status, active,
                uid != U_VISIBLE)
        for uid in (U_INVITED, U_INVITED2):
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test, "
                "invite_token, invite_status, invited_by, invited_at, invite_expires_at) "
                "VALUES ($1, $2, $3, $4, NULL, 'member', 'active', true, true, $5, 'pending', $6, now(), "
                "now() + interval '7 days')",
                uid, ORG_A, email(uid), f"USERMGMT1 {NAMES[uid]}", secrets.token_urlsafe(32), U_ADMIN)
        # Logins that are NOT the primary sub.
        await conn.execute(
            "INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via) VALUES "
            "($1, $2, $3, $4, NULL, 'verified_email'), ($5, $2, $6, $7, NULL, 'operator'), "
            "($8, $2, $9, $7, $10, 'operator')",
            U_TWOROLES, ORG_A, f"google-oauth2|{TAG}-tworoles", "https://dev-smmrfubsfscif3t1.us.auth0.com/",
            U_PLAT_PH, f"auth0|{TAG}-platph", platform_issuer(),
            U_PLAT_DB, f"auth0|{TAG}-platdb", email_of(U_PLAT_DB))
        grants = [(U_ADMIN, "admin"), (U_MEMBER, "member"), (U_TWOROLES, "member"),
                  (U_TWOROLES, "investment_staff"), (U_GOOGLE, "member"), (U_PH, "member"),
                  (U_VISIBLE, "member"), (U_RETIRE_ME, "member"), (U_DEACT, "member"), (U_INVITED, "member"),
                  (U_INVITED2, "member"), (U_PLAT_DB, "member")]
        for uid, rname in grants:
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(ORG_A, rname)])
        await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", U_OTHER, roles[(ORG_B, "member")])
        await rbac.grant_org_admin(conn, U_ADMIN, ORG_A)
        await rbac.grant_org_admin(conn, U_ORGADMIN_ONLY, ORG_A)
    return roles


# ═══ The real app, token stubbed by bearer string ══════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
PROFILES: dict[str, dict | None] = {}
RESET_CALLS: list[dict] = []
EMAIL_CALLS: list[dict] = []


def claims(uid_or_sub, org=None) -> dict:
    if isinstance(uid_or_sub, UUID):
        return {"sub": sub_of(uid_or_sub), "org_id": str(ORG_B if uid_or_sub == U_OTHER else ORG_A)}
    return {"sub": uid_or_sub, "org_id": str(org or ORG_A)}


def token_for(c: dict) -> str:
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = dict(c)
    return key


async def api(c, method, path, body=None, timeout=60.0):
    import main

    headers = {} if c is None else {"Authorization": f"Bearer {token_for(c)}"}
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def install_stubs():
    import main
    from services import email as email_service
    from services import user_admin
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def fake_profile(request, c):
        return PROFILES.get(c.get("sub"))

    users_mod.fetch_auth0_profile = fake_profile

    async def fake_change_password(url, body):
        RESET_CALLS.append({"url": url, "body": dict(body)})
        return 200

    user_admin.post_change_password = fake_change_password

    def no_email(**kw):
        EMAIL_CALLS.append(kw)
        raise email_service.EmailBlocked("verify: email must not be sent")

    email_service.send_email = no_email


def users_path(**params) -> str:
    q = "&".join(f"{k}={v}" for k, v in {"include_test": "true", "limit": 200, **params}.items())
    return f"/api/v1/admin/users?{q}"


def by_id(lst) -> dict:
    return {u["id"]: u for u in lst} if isinstance(lst, list) else {}


def detail(body) -> str:
    return json.dumps(body, ensure_ascii=False)


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — findings (invite flow, tenant client ids/connections, fixtures, mailer)")
    src_inv = (API_DIR / "services" / "invites.py").read_text(encoding="utf-8")
    src_enr = (API_DIR / "routers" / "enroll.py").read_text(encoding="utf-8")
    ok = ("invite_status, invited_by, invited_at, invite_expires_at" in src_inv
          and "SET auth0_sub = $2" in src_inv and "accept_invite" in src_enr)
    check(ok, "1a: an invite is a users row (auth0_sub NULL, invite_token); ONLY /enroll/accept writes auth0_sub")
    find("1a invite flow",
         "POST /admin/invites inserts a users row with auth0_sub NULL + invite_token/invite_status='pending'. "
         "The invitee's link opens /enroll; /enroll/accept (provisioning-exempt) writes auth0_sub onto THAT row. "
         "Signing in WITHOUT the link never touches the invite row: ensure_user finds no sub, /userinfo gives no "
         "VERIFIED email match, so a NEW pending placeholder row is created and stamped last_login_at — that is "
         "why the invited rows kept auth0_sub NULL while 'a' row had a last sign-in (it was the pending duplicate). "
         "jpl99172 was then linked by hand: its sub is set but invite_status still reads 'pending'.")
    names = {"AUTH0_CLIENT_ID": "2nd Act", "HOLLISWORKS_AUTH0_CLIENT_ID": "Platform"}
    for env, tenant in names.items():
        check(bool((os.environ.get(env) or "").strip()), f"1b: {env} ({tenant}) is present in Doppler (name only)")
    from services import user_admin

    for tenant, env in user_admin.CONNECTION_ENV.items():
        set_ = bool((os.environ.get(env) or "").strip())
        find(f"1b connection for {tenant}",
             f"{env} {'is set' if set_ else 'is NOT set — defaults to'} "
             f"{'' if set_ else user_admin.DEFAULT_DB_CONNECTION}")
    rows = await platform(conn, """
        SELECT u.id, u.email, u.auth0_sub, u.org_id,
               COALESCE(json_agg(json_build_object('auth0_sub', i.auth0_sub, 'issuer', i.issuer,
                                                   'linked_via', i.linked_via))
                        FILTER (WHERE i.id IS NOT NULL), '[]') AS idents
        FROM users u LEFT JOIN user_identities i ON i.user_id = u.id
        WHERE NOT u.is_test AND u.status = 'active' AND NOT (u.org_id = ANY($1::uuid[]))
          AND COALESCE(u.auth0_sub, '') NOT LIKE $2 AND u.email NOT LIKE $3
        GROUP BY u.id ORDER BY u.email""", FIXTURE_ORGS, SUB_LIKE, EMAIL_LIKE)
    from services import email as email_service

    for r in rows:
        idents = json.loads(r["idents"]) if isinstance(r["idents"], str) else r["idents"]
        methods = user_admin.sign_in_methods(org_id=r["org_id"], auth0_sub=r["auth0_sub"], identities=idents)
        find(f"1b real account {email_service.redact_email(r['email'])}",
             "; ".join(f"{m['tenant']} · {m['method']} ({m['linked_via']})" for m in methods) or "never signed in")
    check(len(rows) > 0, "1b: every active real account's sign-in method was derived from the DB", f"{len(rows)}")

    from _usermgmt1_hygiene import classify

    fixtures = [dict(r) for r in await platform(
        conn, "SELECT id::text AS id, email, org_id::text AS org_id, status FROM users "
              f"WHERE is_test AND NOT {OWN_FIXTURE_WHERE} ORDER BY email", FIXTURE_USERS, SUB_LIKE, FIXTURE_ORGS)]
    report = classify(fixtures)
    for u in report:
        hits = []  # every FK column on users (pg_constraint) that holds a row for this user
        for fk in await fk_children(conn, "public.users"):
            n = await platform(conn, f'SELECT count(*) FROM {fk["child"]} WHERE "{fk["child_cols"][0]}" = $1',
                               UUID(u["id"]), fetch="fetchval")
            if n:
                hits.append(f'{fk["child"]}.{fk["child_cols"][0]}={n}')
        find(f"1c {u['email']}", f"{u['verdict']}: {','.join(u['scripts']) or 'no script'}; refs {hits or 'none'}")
    check(all(u["scripts"] for u in report) or not report,
          "1c: every remaining @test.local user is traced to a live verify script", f"{len(report)} users")
    record = json.loads(PURGE_RECORD.read_text(encoding="utf-8"))
    check(len(record["orphans_removed"]) == record["deleted"]["public.users"] == 19,
          "1c: the 19 orphans found before the purge are on record with their scripts",
          f"{len(record['orphans_removed'])}")
    mailer = (API_DIR / "services" / "email.py").exists()
    check(mailer, "1d: an outgoing mailer exists (services/email.py, SES) — resend uses it, delivery reported")
    find("1d mailer", f"SES configured={email_service.email_configured()}; the invite path reports "
                      "'blocked' with the reason when SES refuses (Textract-only IAM user has no ses:SendEmail)")


async def y_one_row(conn):
    section("[Y] One row per person — reproduced on the OLD query first")
    code, out, _ = run_cmd(["git", "show", f"{BEFORE_REF}:apps/api/routers/admin.py"], REPO, timeout=60)
    m = re.search(r'async def list_users\(.*?rows = await conn\.fetch\(\s*f"""(.*?)"""', out, re.S)
    check(code == 0 and m is not None and "LEFT JOIN user_roles" in m.group(1),
          f"the {BEFORE_REF} list query JOINs user_roles (read from git)")
    if m:
        old_sql = (m.group(1).replace("{' AND '.join(conditions)}", "u.org_id = $1 AND u.id = $2")
                   .replace("${limit_pos}", "$3").replace("${offset_pos}", "$4"))
        old_rows = await scoped(conn, ORG_A, old_sql, ORG_A, U_TWOROLES, 50, 0)
        check(len(old_rows) == 2, "BEFORE: a user with 2 roles comes back as 2 rows", f"{len(old_rows)}")
    code, lst = await api(claims(U_ADMIN), "GET", users_path())
    rows = [u for u in lst if u.get("id") == str(U_TWOROLES)] if isinstance(lst, list) else []
    names = sorted(r["name"] for r in rows[0]["roles"]) if rows else []
    check(code == 200 and len(rows) == 1 and names == ["investment_staff", "member"],
          "AFTER: the same user is ONE row with a roles array of 2", f"HTTP {code}; {len(rows)} rows; {names}")
    ids = [u["id"] for u in lst] if isinstance(lst, list) else []
    check(len(ids) == len(set(ids)) and len(ids) > 0, "no person appears twice anywhere in the list", f"{len(ids)}")
    code, page = await api(claims(U_ADMIN), "GET", users_path(limit=3))
    check(code == 200 and isinstance(page, list) and len(page) == 3,
          "paging counts people (limit=3 → 3 distinct people)", f"{len(page) if isinstance(page, list) else page}")


async def y_fields(conn):
    section("[Y] sign_in / never_signed_in / email_is_placeholder / is_test per fixture kind")
    code, lst = await api(claims(U_ADMIN), "GET", users_path())
    u = by_id(lst)

    def m(uid):
        return [(s["tenant"], s["method"], s["linked_via"]) for s in u.get(str(uid), {}).get("sign_in", [])]

    check(code == 200 and m(U_TWOROLES) == [("2nd Act", "Email & password", "primary"),
                                            ("2nd Act", "Google", "verified_email")],
          "database primary + linked Google identity", f"{m(U_TWOROLES)}")
    check(m(U_GOOGLE) == [("2nd Act", "Google", "primary")], "Google-only login", f"{m(U_GOOGLE)}")
    check(m(U_PLAT_PH) == [("Platform", "Email & password", "operator")],
          "a Hollisworks-issued identity is labelled Platform", f"{m(U_PLAT_PH)}")
    inv = u.get(str(U_INVITED), {})
    check(inv.get("never_signed_in") is True and inv.get("sign_in") == [] and inv.get("email_is_placeholder") is False,
          "invited, never signed in: never_signed_in true, no sign-in, real email")
    check(u.get(str(U_TWOROLES), {}).get("never_signed_in") is False, "a signed-in user is not never_signed_in")
    check(u.get(str(U_PH), {}).get("email_is_placeholder") is True
          and u.get(str(U_NOEMAIL), {}).get("email_is_placeholder") is True
          and u.get(str(U_TWOROLES), {}).get("email_is_placeholder") is False,
          "email_is_placeholder: true for <sub>@placeholder.local rows, false for a real address")
    check(u.get(str(U_TWOROLES), {}).get("is_test") is True and u.get(str(U_VISIBLE), {}).get("is_test") is False,
          "is_test mirrors users.is_test (fixture true; the visible control false)")
    db = await platform(conn, "SELECT is_test FROM users WHERE id = $1", U_TWOROLES, fetch="fetchval")
    check(db is True, "the fixture row really carries is_test = true in the DB")


async def y_filters(conn):
    section("[Y] Filters — status, role; test and retired hidden by default, shown on request")
    code, dflt = await api(claims(U_ADMIN), "GET", "/api/v1/admin/users?limit=200")
    ids = set(by_id(dflt))
    check(code == 200 and ids == {str(U_VISIBLE)},
          "default: ORG A shows only the non-test person (every is_test fixture hidden)", f"{sorted(ids)}")
    code, with_test = await api(claims(U_ADMIN), "GET", users_path())
    wt = set(by_id(with_test))
    check(str(U_TWOROLES) in wt and str(U_VISIBLE) in wt and str(U_RETIRED) not in wt,
          "include_test shows fixtures; retired still hidden", f"{len(wt)}")
    code, with_ret = await api(claims(U_ADMIN), "GET", users_path(include_retired="true"))
    check(str(U_RETIRED) in by_id(with_ret), "include_retired shows the retired account")
    code, only_ret = await api(claims(U_ADMIN), "GET", users_path(status="retired"))
    sql = {str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT id FROM users WHERE org_id = $1 AND "
                                                          "(status = 'retired' OR email LIKE '%@deleted.invalid')", ORG_A)}
    check(code == 200 and set(by_id(only_ret)) == sql and len(sql) > 0,
          "status=retired equals the same predicate run in SQL", f"{len(sql)}")
    code, pend = await api(claims(U_ADMIN), "GET", users_path(status="pending"))
    sql = {str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT id FROM users WHERE org_id = $1 AND status = 'pending'",
                                              ORG_A)}
    got = set(by_id(pend))
    check(code == 200 and got == sql and 0 < len(got) < len(wt),
          "status=pending: non-empty, a strict subset, equal to SQL", f"{len(got)} of {len(wt)}")
    code, deact = await api(claims(U_ADMIN), "GET", users_path(status="deactivated"))
    check(code == 200 and set(by_id(deact)) == {str(U_DEACT)}, "status=deactivated → only the deactivated account")
    code, staff = await api(claims(U_ADMIN), "GET", users_path(role="investment_staff"))
    sql = {str(r["id"]) for r in await scoped(conn, ORG_A, """
        SELECT u.id FROM users u WHERE u.org_id = $1 AND NOT (u.status = 'retired')
          AND EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                      WHERE ur.user_id = u.id AND r.name = 'investment_staff')""", ORG_A)}
    check(code == 200 and set(by_id(staff)) == sql == {str(U_TWOROLES)},
          "role=investment_staff → exactly the SQL EXISTS set (and the person keeps BOTH roles)",
          f"{sorted(by_id(staff))}")
    code, bad = await api(claims(U_ADMIN), "GET", users_path(status="bogus"))
    check(code == 400, "an unknown status filter is a 400, not an empty list", f"HTTP {code}")


async def y_reset(conn):
    section("[Y] Password reset — the right tenant/client/connection/email; refusals")
    from main import get_settings
    from services import user_admin

    path = f"/api/v1/admin/users/{U_TWOROLES}/password-reset"
    before = await audit_count(conn, "send_password_reset", U_TWOROLES)
    RESET_CALLS.clear()
    code, body = await api(claims(U_MEMBER), "POST", path)
    check(code == 403 and "manage_members" in detail(body) and not RESET_CALLS
          and await audit_count(conn, "send_password_reset", U_TWOROLES) == before,
          "member: 403 on the identical request; Auth0 not called; no audit row", f"HTTP {code}")
    code, body = await api(claims(U_ADMIN), "POST", path)
    call = RESET_CALLS[0] if len(RESET_CALLS) == 1 else {}
    s = get_settings()
    conn_name = (os.environ.get("AUTH0_DB_CONNECTION") or "").strip() or user_admin.DEFAULT_DB_CONNECTION
    check(code == 200 and call.get("url") == f"https://{s.auth0_domain}/dbconnections/change_password",
          "org admin: ONE request to the 2nd Act tenant's /dbconnections/change_password", f"HTTP {code}")
    b = call.get("body", {})
    check(b.get("client_id") == (os.environ.get("AUTH0_CLIENT_ID") or "").strip() and bool(b.get("client_id"))
          and b.get("email") == email_of(U_TWOROLES) and b.get("connection") == conn_name and len(b) == 3,
          "body = {client_id: AUTH0_CLIENT_ID, email: the user's email, connection} (values compared, not printed)")
    check(await audit_count(conn, "send_password_reset", U_TWOROLES) == before + 1, "audit_log row on success")

    cases = [(U_GOOGLE, user_admin.REFUSE_GOOGLE, "Google login"),
             (U_PH, user_admin.REFUSE_NO_EMAIL, "placeholder email"),
             (U_PLAT_PH, user_admin.REFUSE_NO_EMAIL, "Platform placeholder"),
             (U_NOEMAIL, user_admin.REFUSE_NO_EMAIL, "never signed in, no email"),
             (U_INVITED, user_admin.REFUSE_NEVER_SIGNED_IN, "never signed in, real email")]
    for uid, sentence, label in cases:
        RESET_CALLS.clear()
        code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{uid}/password-reset")
        check(code == 409 and body.get("detail") == sentence and not RESET_CALLS
              and await audit_count(conn, "send_password_reset", uid) == 0,
              f"refused — {label}: the exact sentence, no Auth0 call, no audit", f"HTTP {code}: {body.get('detail')}")
    code, lst = await api(claims(U_ADMIN), "GET", users_path())
    check(by_id(lst).get(str(U_GOOGLE), {}).get("reset_password_refusal") == user_admin.REFUSE_GOOGLE
          and by_id(lst).get(str(U_TWOROLES), {}).get("reset_password_refusal") is None,
          "the list publishes the same refusal sentence per row (and none for a database login)")

    RESET_CALLS.clear()
    code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{U_PLAT_DB}/password-reset")
    if (os.environ.get("HOLLISWORKS_AUTH0_CLIENT_ID") or "").strip() and s.hollisworks_enabled:
        call = RESET_CALLS[0] if RESET_CALLS else {}
        check(code == 200 and call.get("url") == f"https://{s.hollisworks_auth0_domain}/dbconnections/change_password"
              and call.get("body", {}).get("client_id") == os.environ["HOLLISWORKS_AUTH0_CLIENT_ID"].strip(),
              "a Platform database identity resets on the Hollisworks tenant with ITS client id", f"HTTP {code}")
    else:
        check(code == 503 and "HOLLISWORKS" in detail(body) and not RESET_CALLS,
              "Platform reset with no Hollisworks client configured: 503 naming the variable", f"HTTP {code}")
    RESET_CALLS.clear()
    code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{U_SUPER}/password-reset")
    check(code == 403 and "Super Admin" in detail(body) and not RESET_CALLS,
          "org admin cannot reset a Super Admin's password", f"HTTP {code}")
    code, body = await api(claims(U_ORGADMIN_ONLY), "POST", path)
    check(code == 403, "org_admin without manage_members: 403 (same gate as every user edit)", f"HTTP {code}")


async def y_resend(conn):
    section("[Y] Resend invite — rotates invite_token; the new link works, the old one does not")
    old = await user_row(conn, U_INVITED)
    path = f"/api/v1/admin/users/{U_INVITED}/resend-invite"
    code, _ = await api(claims(U_MEMBER), "POST", path, {"send_email": False})
    same = await user_row(conn, U_INVITED)
    check(code == 403 and same["invite_token"] == old["invite_token"], "member: 403; token unchanged", f"HTTP {code}")
    before = await audit_count(conn, "resend_invite", U_INVITED)
    code, body = await api(claims(U_ADMIN), "POST", path, {"send_email": False})
    new = await user_row(conn, U_INVITED)
    check(bool(code == 200 and new["invite_token"] and new["invite_token"] != old["invite_token"]
               and new["invite_status"] == "pending" and new["auth0_sub"] is None),
          "org admin: a NEW token is stored (independent re-read); invite pending, still unclaimed", f"HTTP {code}")
    url = body.get("enrollment_url") or ""
    check(url.startswith("https://") and f"invite_token={new['invite_token']}" in url,
          "the returned enrollment link is absolute and carries the new token")
    check(body.get("email_delivery", {}).get("status") == "skipped" and not EMAIL_CALLS,
          "delivery reported honestly ('skipped' on request); SES never reached")
    code, v_new = await api(None, "GET", f"/api/v1/enroll/validate?invite_token={new['invite_token']}")
    code2, v_old = await api(None, "GET", f"/api/v1/enroll/validate?invite_token={old['invite_token']}")
    check(code == 200 and v_new.get("status") == "valid" and v_old.get("status") == "not_found",
          "the new link validates; the old link no longer does", f"{v_new.get('status')} / {v_old.get('status')}")
    rows = await platform(conn, "SELECT COALESCE(payload::text, '') AS n FROM audit_log "
                                "WHERE action = 'resend_invite' AND resource_id = $1", U_INVITED)
    check(len(rows) == before + 1 and all(new["invite_token"] not in r["n"] for r in rows),
          "one audit row on success, and it never contains the token")
    code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{U_TWOROLES}/resend-invite",
                           {"send_email": False})
    check(code == 409, "a person who already signed in has no invitation to resend", f"HTTP {code}")


async def y_link(conn):
    section("[Y] Link pending → existing person")
    pa = await id_for_sub(conn, P_LINK_PRIMARY)
    pb = await id_for_sub(conn, P_LINK_IDENTITY)
    pc = await id_for_sub(conn, P_REFUSED)
    check(all([pa, pb, pc]), "the pending fixtures were created by the real provisioning path", f"{pa} {pb} {pc}")
    todos = await platform(conn, "SELECT count(*) FROM member_todos WHERE related_id = $1 AND status = 'open'",
                           pa, fetch="fetchval")
    check(todos > 0, "the pending sign-in raised an open to-do for the org admins", f"{todos}")
    todo_link = await platform(conn, "SELECT action_key FROM member_todos WHERE related_id = $1 LIMIT 1", pa,
                               fetch="fetchval")
    check(todo_link == "/admin/users?status=pending", "the to-do links to People & Access filtered to pending",
          f"{todo_link}")

    path = f"/api/v1/admin/users/{pa}/link"
    code, _ = await api(claims(U_MEMBER), "POST", path, {"target_user_id": str(U_INVITED2)})
    row = await user_row(conn, pa)
    tgt = await user_row(conn, U_INVITED2)
    check(code == 403 and row["status"] == "pending" and row["auth0_sub"] == P_LINK_PRIMARY and tgt["auth0_sub"] is None,
          "member: 403; pending row and target unchanged", f"HTTP {code}")
    code, body = await api(claims(U_ADMIN), "POST", path, {"target_user_id": str(U_INVITED2)})
    row = await user_row(conn, pa)
    tgt = await user_row(conn, U_INVITED2)
    open_todos = await platform(conn, "SELECT count(*) FROM member_todos WHERE related_id = $1 AND status = 'open'",
                                pa, fetch="fetchval")
    check(code == 200 and tgt["auth0_sub"] == P_LINK_PRIMARY and tgt["invite_status"] == "accepted",
          "org admin: the login's sub moved onto the target (its auth0_sub was NULL); invite accepted", f"HTTP {code}")
    check(row["status"] == "retired" and row["auth0_sub"] is None, "the pending row is retired (kept, sub cleared)")
    check(open_todos == 0, "its pending to-dos are closed", f"{open_todos}")
    check(await audit_count(conn, "link_pending_login", pa) == 1
          and await audit_count(conn, "link_pending_login", U_INVITED2) == 1, "audited on both rows")
    code, me = await api(claims(P_LINK_PRIMARY), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_INVITED2) and me.get("status") == "active",
          "the login's next request resolves to the target (ensure_user), admitted", f"HTTP {code}: {me.get('id')}")

    code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{pb}/link",
                           {"target_user_id": str(U_MEMBER)})
    ident = await platform(conn, "SELECT user_id, linked_via FROM user_identities WHERE auth0_sub = $1",
                           P_LINK_IDENTITY, fetch="fetchrow")
    member_sub = (await user_row(conn, U_MEMBER))["auth0_sub"]
    check(code == 200 and ident is not None and ident["user_id"] == U_MEMBER and ident["linked_via"] == "operator"
          and member_sub == sub_of(U_MEMBER),
          "target already has a sub → a user_identities row, linked_via 'operator'; target's own sub untouched",
          f"HTTP {code}; {dict(ident) if ident else None}")
    code, me = await api(claims(P_LINK_IDENTITY), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_MEMBER), "that login now resolves to the existing member")

    pre = await user_row(conn, pc)
    code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{pc}/link",
                           {"target_user_id": str(U_OTHER)})
    post = await user_row(conn, pc)
    other = await user_row(conn, U_OTHER)
    check(code == 404 and post["status"] == "pending" and post["auth0_sub"] == pre["auth0_sub"]
          and other["auth0_sub"] == sub_of(U_OTHER),
          "cross-org target refused for an org admin (404 — RLS keeps the other org's user invisible); unchanged",
          f"HTTP {code}")
    code, body = await api(claims(U_SUPER), "POST", f"/api/v1/admin/users/{pc}/link",
                           {"target_user_id": str(U_OTHER)})
    post = await user_row(conn, pc)
    check(code == 400 and "same organization" in detail(body) and post["status"] == "pending",
          "cross-org refused even for a Super Admin who can see both (400); unchanged", f"HTTP {code}")
    code, body = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{pc}/link",
                           {"target_user_id": str(U_SUPER)})
    post = await user_row(conn, pc)
    n_ident = await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub = $1", P_REFUSED,
                             fetch="fetchval")
    check(code == 403 and "Super Admin" in detail(body) and post["status"] == "pending" and n_ident == 0,
          "non-super onto a Super Admin refused; nothing changed", f"HTTP {code}")
    code, body = await api(claims(U_SUPER), "POST", f"/api/v1/admin/users/{pc}/link",
                           {"target_user_id": str(U_SUPER)})
    n_ident = await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub = $1 AND user_id = $2",
                             P_REFUSED, U_SUPER, fetch="fetchval")
    check(code == 200 and n_ident == 1, "a Super Admin may link onto a Super Admin", f"HTTP {code}")


async def y_email(conn):
    section("[Y] Edit email — placeholder / never-signed-in only; unique case-insensitively; audited")
    path = f"/api/v1/admin/users/{U_PH}/email"
    new_email = f"{TAG}-newmail@test.local"
    old = (await user_row(conn, U_PH))["email"]
    code, _ = await api(claims(U_MEMBER), "PATCH", path, {"email": new_email})
    check(code == 403 and (await user_row(conn, U_PH))["email"] == old, "member: 403; email unchanged", f"HTTP {code}")
    code, body = await api(claims(U_ADMIN), "PATCH", path, {"email": "not-an-email"})
    check(code == 400 and (await user_row(conn, U_PH))["email"] == old, "invalid address: 400; unchanged")
    code, body = await api(claims(U_ADMIN), "PATCH", path, {"email": new_email})
    check(code == 200 and (await user_row(conn, U_PH))["email"] == new_email,
          "org admin: placeholder replaced (independent re-read)", f"HTTP {code}")
    check(await audit_count(conn, "update_user_email", U_PH) == 1, "audit_log row on success")
    noemail_old = (await user_row(conn, U_NOEMAIL))["email"]
    code, body = await api(claims(U_ADMIN), "PATCH", f"/api/v1/admin/users/{U_NOEMAIL}/email",
                           {"email": new_email.upper()})
    check(code == 409 and (await user_row(conn, U_NOEMAIL))["email"] == noemail_old,
          "the same address in another case is refused (409); unchanged", f"HTTP {code}")
    code, body = await api(claims(U_ADMIN), "PATCH", f"/api/v1/admin/users/{U_NOEMAIL}/email",
                           {"email": email_of(U_OTHER).upper()})
    check(code == 409 and (await user_row(conn, U_NOEMAIL))["email"] == noemail_old,
          "an address held in ANOTHER org is refused too (users.email is platform-wide)", f"HTTP {code}")
    tw_old = (await user_row(conn, U_TWOROLES))["email"]
    code, body = await api(claims(U_ADMIN), "PATCH", f"/api/v1/admin/users/{U_TWOROLES}/email",
                           {"email": f"{TAG}-other@test.local"})
    check(code == 409 and (await user_row(conn, U_TWOROLES))["email"] == tw_old,
          "a normal signed-in account is refused; unchanged", f"HTTP {code}")
    code, body = await api(claims(U_ADMIN), "PATCH", f"/api/v1/admin/users/{U_INVITED2}/email",
                           {"email": f"{TAG}-x@test.local"})
    check(code == 409, "after linking, the formerly never-signed-in target is a signed-in account (refused)",
          f"HTTP {code}")


async def y_gates(conn):
    section("[Y] Approve / reject / retire — org admin allowed, member 403, unchanged on refusal")
    pd = await id_for_sub(conn, P_APPROVE)
    pe = await id_for_sub(conn, P_REJECT)
    rid = await platform(conn, "SELECT id FROM roles WHERE org_id = $1 AND name = 'member'", ORG_A, fetch="fetchval")
    code, _ = await api(claims(U_MEMBER), "POST", f"/api/v1/admin/users/{pd}/approve", {"role_id": str(rid)})
    check(code == 403 and (await user_row(conn, pd))["status"] == "pending", "approve: member 403, still pending")
    code, _ = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{pd}/approve", {"role_id": str(rid)})
    check(code == 200 and (await user_row(conn, pd))["status"] == "active"
          and await audit_count(conn, "approve_pending_user", pd) == 1, "approve: org admin 200, admitted, audited")
    code, _ = await api(claims(U_MEMBER), "POST", f"/api/v1/admin/users/{pe}/reject")
    check(code == 403 and (await user_row(conn, pe))["status"] == "pending", "reject: member 403, still pending")
    code, _ = await api(claims(U_ADMIN), "POST", f"/api/v1/admin/users/{pe}/reject")
    check(code == 200 and (await user_row(conn, pe))["status"] == "rejected"
          and await audit_count(conn, "reject_pending_user", pe) == 1, "reject: org admin 200, rejected, audited")
    code, _ = await api(claims(U_MEMBER), "DELETE", f"/api/v1/admin/users/{U_RETIRE_ME}")
    check(code == 403 and (await user_row(conn, U_RETIRE_ME))["status"] == "active", "retire: member 403, unchanged")
    code, body = await api(claims(U_ADMIN), "DELETE", f"/api/v1/admin/users/{U_RETIRE_ME}")
    row = await user_row(conn, U_RETIRE_ME)
    check(code == 200 and row["status"] == "retired" and row["auth0_sub"] is None and body.get("hard_deleted") is False
          and await audit_count(conn, "anonymize_user", U_RETIRE_ME) == 1,
          "retire: org admin 200 — anonymized, status retired, row kept, audited", f"HTTP {code}")
    code, lst = await api(claims(U_ADMIN), "GET", users_path())
    check(str(U_RETIRE_ME) not in by_id(lst), "the retired account leaves the default list")

    section("[Y] The UI renders exactly the server's actions (real envelope → real component)")
    code_a, rows_admin = await api(claims(U_ADMIN), "GET", users_path())
    code_v, rows_view = await api(claims(U_ORGADMIN_ONLY), "GET", users_path())
    check(code_a == 200 and code_v == 200 and len(rows_view) > 0, "both callers can read the list", f"{code_a}/{code_v}")
    view_actions = {a for r in rows_view for a in r.get("actions", [])}
    check(view_actions <= {"approve", "reject"}, "org_admin without manage_members is published no edit action",
          f"{sorted(view_actions)}")
    rendered = {}
    for label, rows in (("admin", rows_admin), ("view", rows_view)):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(rows, fh)
            tmp = fh.name
        code, out, err = run_cmd(["node", "tests/admin/renderRows.mjs", tmp], WEB_DIR, timeout=180)
        os.unlink(tmp)
        rendered[label] = json.loads(out) if code == 0 and out.strip().startswith("{") else None
        check(rendered[label] is not None, f"the {label} envelope renders through the real components",
              err[-200:] if code else "")
    view = rendered.get("view") or {}
    check(bool(view) and all(set(a) <= {"approve", "reject"} for a in view.values()),
          "view-only envelope: no Manage / reset / resend / link / email control renders anywhere",
          f"{sorted({x for a in view.values() for x in a})}")
    admin = rendered.get("admin") or {}
    tw = admin.get(str(U_TWOROLES), [])
    gg = admin.get(str(U_GOOGLE), [])
    check("edit" in tw and "reset-password" in tw and "reset-password" not in gg,
          "admin envelope: Manage + reset render for a database login; the Google row renders the refusal instead",
          f"{tw} / {gg}")


async def y_contact(conn):
    section("[Y] Pending page — asks for an email when none is real; stored as contact_email, never linked")
    pf = await id_for_sub(conn, P_CONTACT)
    await platform(conn, "UPDATE users SET is_test = true WHERE id = $1", pf, fetch="execute")
    code, me = await api(claims(P_CONTACT), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("status") == "pending" and me.get("email_is_placeholder") is True
          and me.get("contact_email") is None, "/users/me tells the page there is no real email", f"HTTP {code}")
    code, body = await api(claims(P_CONTACT), "GET", "/api/v1/entities")
    check(code == 403, "the pending login is still refused on data routes", f"HTTP {code}")
    typed = email_of(U_TWOROLES)  # an EXISTING member's address — must link nothing
    code, me = await api(claims(P_CONTACT), "PUT", "/api/v1/users/me/contact-email", {"contact_email": typed})
    row = await user_row(conn, pf)
    check(code == 200 and row["contact_email"] == typed and row["status"] == "pending"
          and row["email"].endswith("@placeholder.local"),
          "stored as contact_email while pending; users.email untouched; still pending", f"HTTP {code}")
    code, me = await api(claims(P_CONTACT), "GET", "/api/v1/users/me")
    n_ident = await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub = $1", P_CONTACT,
                             fetch="fetchval")
    check(code == 200 and me.get("id") == str(pf) and n_ident == 0,
          "never used for linking: the next request is still the pending row, no identity created",
          f"{me.get('id')} vs {pf}")
    code, lst = await api(claims(U_ADMIN), "GET", users_path(status="pending"))
    shown = by_id(lst).get(str(pf), {})
    check(shown.get("contact_email") == typed, "the admins see the contact address on the pending row")
    code, body = await api(claims(U_MEMBER), "PUT", "/api/v1/users/me/contact-email", {"contact_email": typed})
    check(code == 409, "an account with a real email cannot set a contact address", f"HTTP {code}")
    match_rows = [u for u in by_id(lst).values() if (u.get("likely_match") or {}).get("id") == str(U_TWOROLES)]
    check(len(match_rows) > 0, "a pending row whose name/contact equals a member's email shows 'Looks like' that member",
          f"{len(match_rows)}")


async def y_hygiene(conn):
    section("[Y] Test hygiene — orphans removed, kept fixtures flagged, mechanism proven, real users untouched")
    from services import rbac, test_accounts as T

    record = json.loads(PURGE_RECORD.read_text(encoding="utf-8"))
    print(f"[INFO] purge on record (per table): {json.dumps(record['deleted'])}")
    check(record["planned"] == record["deleted"] and record["foreign_user_references_in_plan"] == [],
          "recorded purge: deleted == planned on every table; no row in the plan touched a non-orphan user")
    emails = [o["email"] for o in record["orphans_removed"]]
    left = await platform(conn, "SELECT count(*) FROM users WHERE email = ANY($1::text[])", emails, fetch="fetchval")
    check(left == 0, "zero of the 19 orphan fixture users remain", f"{left}")
    kept = await platform(conn, "SELECT count(*) FILTER (WHERE is_test), count(*) FROM users "
                                "WHERE email = ANY($1::text[])", record["kept_flagged_is_test"], fetch="fetchrow")
    check(kept[1] > 0 and kept[0] == kept[1], "every kept fixture is flagged is_test", f"{kept[0]}/{kept[1]}")
    untagged = await platform(conn, "SELECT count(*) FROM users WHERE email ILIKE '%@test.local' AND NOT is_test",
                              fetch="fetchval")
    check(untagged == 0, "no @test.local user anywhere is left un-flagged", f"{untagged}")

    from _usermgmt1_hygiene import classify

    fixtures = [dict(r) for r in await platform(
        conn, "SELECT id::text AS id, email, org_id::text AS org_id, status FROM users "
              f"WHERE is_test AND NOT {OWN_FIXTURE_WHERE}", FIXTURE_USERS, SUB_LIKE, FIXTURE_ORGS)]
    orphans_now = [u["email"] for u in classify(fixtures) if u["verdict"] == "orphan"]
    check(not orphans_now, "re-classifying today finds no orphan fixture left", f"{orphans_now}")

    # The mechanism on our own orphan: rows reached transitively, refused when
    # any row in the plan touches a user outside the set, exact counts.
    rid = await platform(conn, "SELECT id FROM roles WHERE org_id = $1 AND name = 'member'", ORG_A, fetch="fetchval")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid in (U_ORPHAN, U_ORPHAN2):
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, 'orphan', $4, 'member', 'active', true, true)",
                uid, ORG_A, email_of(uid), sub_of(uid))
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid)
        await rbac.grant_org_admin(conn, U_ORPHAN2, ORG_A)
        def_id = await conn.fetchval(
            "INSERT INTO workflow_definitions (org_id, name, created_by) VALUES ($1, $2, $3) RETURNING id",
            ORG_A, f"{TAG} orphan def", U_ORPHAN)
        await conn.execute(
            "INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, bpmn_xml, created_by) "
            "VALUES ($1, $2, 1, '<definitions/>', $3)", def_id, ORG_A, U_MEMBER)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        plan = await T.plan_purge(conn, [U_ORPHAN])
        blocking = await T.foreign_user_references(conn, plan, [U_ORPHAN])
    check("public.workflow_versions" in plan and any(b["table"] == "public.workflow_versions" for b in blocking),
          "a fixture's row that ANOTHER user also touched is found transitively and blocks the purge",
          f"{blocking}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("UPDATE workflow_versions SET created_by = $1 WHERE workflow_definition_id = $2",
                           U_ORPHAN2, def_id)
    before_real = await real_users(conn)
    deleted = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        plan = await T.plan_purge(conn, [U_ORPHAN, U_ORPHAN2])
        blocking = await T.foreign_user_references(conn, plan, [U_ORPHAN, U_ORPHAN2])
        if not blocking:
            deleted = await T.purge(conn, plan)
    print(f"[INFO] mechanism purge per table: {json.dumps(deleted)}")
    check(not blocking and deleted.get("public.users") == 2 and deleted.get("public.workflow_versions") == 1
          and deleted.get("public.workflow_definitions") == 1 and deleted.get("public.user_roles", 0) >= 2,
          "with every referenced user in the set: purged child-before-parent with exact per-table counts",
          f"{deleted}")
    gone = await platform(conn, "SELECT (SELECT count(*) FROM users WHERE id = ANY($1::uuid[])) + "
                                "(SELECT count(*) FROM workflow_definitions WHERE id = $2) + "
                                "(SELECT count(*) FROM user_roles WHERE user_id = ANY($1::uuid[]))",
                          [U_ORPHAN, U_ORPHAN2], def_id, fetch="fetchval")
    check(gone == 0, "zero rows referencing the purged fixtures remain", f"{gone}")
    check(await real_users(conn) == before_real, "real users unchanged by the purge (count + checksum)")


async def y_front_end(conn):
    section("[Y] Front end — node tests for the list and actions; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/admin/", "tests/nav/"], WEB_DIR, timeout=300)
    check(code == 0, "node --test tests/admin tests/nav passes", (out + err)[-300:] if code else "")
    for name in ("one row per person: two roles render as two chips on ONE row",
                 "a view-only row (actions: []) renders no control at all",
                 "pending with a placeholder email asks for a contact address"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — flat subprocesses AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1"}
    for script in ("verify_authz1.py", "verify_navregroup.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=1200, env=env)
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(m is not None and m.group(2) == "0" and code == 0, f"{script}: 0 FAIL",
              f"{m.group(0) if m else 'no TOTAL'}; {fails[:3]} {err[-200:] if not m else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def provision_pending(conn):
    """Each pending sub signs in once through the real middleware/provisioning."""
    from services import test_accounts

    for sub in PENDING_SUBS:
        # Unverified /userinfo carrying an existing member's address: the
        # address is taken, so the row gets a placeholder and the address lands
        # in full_name — the "name equals an existing email" match case.
        PROFILES[sub] = {"email": email_of(U_TWOROLES), "email_verified": False}
        code, me = await api(claims(sub), "GET", "/api/v1/users/me")
        uid = await id_for_sub(conn, sub)
        if uid is None or code != 200 or me.get("status") != "pending":
            raise RuntimeError(f"provisioning did not create a pending row for {sub}: HTTP {code} {me}")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await test_accounts.mark_test_user(conn, uid)
    check(True, "six pending sign-ins provisioned by the real path and flagged is_test")


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    install_stubs()
    pre = None
    real_before = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real_before = await real_users(conn)
        await run("seed", seed, conn)
        await run("provision pending", provision_pending, conn)
        await run("task 1", y_task1, conn)
        await run("one row", y_one_row, conn)
        await run("fields", y_fields, conn)
        await run("filters", y_filters, conn)
        await run("password reset", y_reset, conn)
        await run("resend invite", y_resend, conn)
        await run("link", y_link, conn)
        await run("edit email", y_email, conn)
        await run("contact email", y_contact, conn)
        await run("gates", y_gates, conn)
        await run("hygiene", y_hygiene, conn)
        await run("front end", y_front_end, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count")
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (users, orgs, identities, audit rows)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} back at its pre-test count", f"{pre[t]} → {post[t]}")
            if real_before is not None:
                check(await real_users(conn) == real_before, "real users unchanged (count + checksum)",
                      f"{real_before[0]} users")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
        finally:
            await conn.close()

    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run — regression section skipped")
    else:
        await run("regression", regression)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
