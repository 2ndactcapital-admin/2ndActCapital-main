"""verify_authz1 — one DB-backed, fail-closed authorization path (AUTHZ1).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. Secrets are hydrated from Doppler over HTTPS
(``_db_connect``). Auth0 ``/userinfo`` is MOCKED (``services.users.
fetch_auth0_profile``); the JWT signature leg is stubbed (``main.verify_token``
maps the bearer string to fixture claims), so everything after it — the
middleware, the Principal, every gate, every handler — is the real app.

Fixtures live in two fixture orgs (99000000-…-0000000a2101 / a2102) plus the
rows the provisioning proofs must create in the REAL Hollisworks org (found by
auth0_sub 'authz1verify|%'). Teardown runs at start and end: FK
child-before-parent, read from pg_constraint, then every counted table is
re-read for its exact pre-test count. Real note_gold_values are checksummed.

Sections (each wrapped: an exception is a [FAIL], never a crash):
  Task 1 route doc · no default-allow scan · bug reproduced BEFORE · AFTER ·
  route matrix (7 personas) · is_staff from the DB · provisioning · real users ·
  AUTHZ_TEST_MODE · front end · cross-org isolation · teardown · regression.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import types
from types import SimpleNamespace
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, app_service_dsn, connect, hydrate  # noqa: E402

hydrate()  # BEFORE main is imported: Settings() reads HOLLISWORKS_AUTH0_* once

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

BEFORE_REF = "a911ac7"
ROUTES_MD = REPO / "docs" / "AUTHZ1_ROUTES.md"
ROUTES_JSON = REPO / "docs" / "AUTHZ1_ROUTES.json"
REAL_USERS_JSON = REPO / "docs" / "AUTHZ1_REAL_USERS.json"
HOLLISWORKS_ORG = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")
SUB_PREFIX = "authz1verify|"
EMAIL_LIKE = "authz1verify%"

ORG_A = UUID("99000000-0000-0000-0000-0000000a2101")
ORG_B = UUID("99000000-0000-0000-0000-0000000a2102")
FIXTURE_ORGS = [ORG_A, ORG_B]
U_MEMBER = UUID("99000000-0000-0000-0000-0000000a2111")
U_STAFF = UUID("99000000-0000-0000-0000-0000000a2112")
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000000a2113")
U_SUPER = UUID("99000000-0000-0000-0000-0000000a2114")
U_NOROLE = UUID("99000000-0000-0000-0000-0000000a2115")
U_PENDING = UUID("99000000-0000-0000-0000-0000000a2116")
U_DUP = UUID("99000000-0000-0000-0000-0000000a2117")
U_OTHER = UUID("99000000-0000-0000-0000-0000000a2121")
FIXTURE_USERS = [U_MEMBER, U_STAFF, U_ORGADMIN, U_SUPER, U_NOROLE, U_PENDING, U_DUP, U_OTHER]
NAMES = {U_MEMBER: "member", U_STAFF: "staff", U_ORGADMIN: "orgadmin", U_SUPER: "super",
         U_NOROLE: "norole", U_PENDING: "pending", U_DUP: "dup", U_OTHER: "other"}
E_A = UUID("99000000-0000-0000-0000-0000000a2131")
E_B = UUID("99000000-0000-0000-0000-0000000a2132")
DEAL_A = UUID("99000000-0000-0000-0000-0000000a2141")
SPV_A = UUID("99000000-0000-0000-0000-0000000a2142")
MARK_A = "AUTHZ1VERIFY-A-MARKER"
MARK_B = "AUTHZ1VERIFY-B-SECRET"
MISSING = "99000000-0000-0000-0000-0000000a21ff"
GATE_DETAILS = ("Permission required", "Staff access required", "Super Admin required",
                "Admin access required", "Access pending")

COUNTED = ("public.users", "public.user_identities", "public.organizations", "public.roles",
           "public.user_roles", "public.role_permissions", "public.member_todos", "public.audit_log",
           "public.entities", "public.deals", "public.spvs", "public.delegate_grants",
           "public.assistant_conversations", "portfolio.note_gold_values")

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


def sub_of(uid: UUID) -> str:
    return SUB_PREFIX + NAMES[uid]


def email_of(uid: UUID) -> str:
    return f"authz1verify-{NAMES[uid]}@test.local"


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


def git_show(path: str) -> str | None:
    code, out, _ = run_cmd(["git", "show", f"{BEFORE_REF}:{path}"], REPO, timeout=60)
    return out if code == 0 else None


# ═══ DB helpers (verify's own connection; RLS-scoped explicitly) ═══════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch", sub=None):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', $2, true)", str(org) if org else "", sub or "")
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval"))
    out["gold_checksum"] = await platform(
        conn, "SELECT md5(COALESCE(string_agg(t::text, ',' ORDER BY t.id), '')) FROM portfolio.note_gold_values t",
        fetch="fetchval")
    return out


_FK_CACHE: dict = {}


async def fk_children(conn, table: str):
    if table not in _FK_CACHE:
        _FK_CACHE[table] = await _fk_children(conn, table)
    return _FK_CACHE[table]


async def _fk_children(conn, table: str):
    return await conn.fetch("""
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


async def delete_tree(conn, table: str, where: str, params: list, depth=0, log=None, path=()):
    """Child-before-parent delete of rows matching ``where``, FKs read from
    pg_constraint (recursively). Self-references and FK cycles (a table
    already on the current path) are skipped; nothing is deleted on a cycle
    edge, so a cycle can only make the parent DELETE fail loudly, never
    delete more than the fixture rows."""
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


USER_WHERE = ("(id = ANY($1::uuid[]) OR auth0_sub LIKE $2 OR email LIKE $3 "
              "OR auth0_sub = ANY($4::text[]))")


async def teardown(conn) -> list[str]:
    log: list[str] = []
    uuid_subs = [str(u) for u in FIXTURE_USERS]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        ids = [r["id"] for r in await conn.fetch(
            f"SELECT id FROM users WHERE {USER_WHERE}", FIXTURE_USERS, SUB_PREFIX + "%", EMAIL_LIKE, uuid_subs)]
        # Rows that point at a fixture user WITHOUT an FK (alerts in real orgs).
        await conn.execute("DELETE FROM member_todos WHERE related_type = 'users' AND related_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM audit_log WHERE resource_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM user_identities WHERE auth0_sub LIKE $1 OR user_id = ANY($2::uuid[])",
                           SUB_PREFIX + "%", ids)
        await delete_tree(conn, "public.users", "id = ANY($1::uuid[])", [ids], log=log)
        await delete_tree(conn, "public.organizations", "id = ANY($1::uuid[])", [FIXTURE_ORGS], log=log)
    return log


async def leftovers(conn) -> int:
    n = await platform(conn, f"SELECT count(*) FROM users WHERE {USER_WHERE}", FIXTURE_USERS,
                       SUB_PREFIX + "%", EMAIL_LIKE, [str(u) for u in FIXTURE_USERS], fetch="fetchval")
    n += await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                        fetch="fetchval")
    n += await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub LIKE $1", SUB_PREFIX + "%",
                        fetch="fetchval")
    return int(n)


async def seed(conn) -> dict:
    from services import rbac

    roles = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, name in ((ORG_A, "A"), (ORG_B, "B")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"AUTHZ1 Verify Org {name}", f"authz1verify-{name.lower()}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        for org in FIXTURE_ORGS:
            for rname in ("member", "investment_staff"):
                desc, perms = C.BASELINE_ROLES[rname]
                rid = await rbac.ensure_role(conn, org, rname, desc)
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        rows = [(U_MEMBER, ORG_A, "member", "active"), (U_STAFF, ORG_A, "member", "active"),
                (U_ORGADMIN, ORG_A, "org_admin", "active"), (U_SUPER, ORG_A, "super_admin", "active"),
                (U_NOROLE, ORG_A, "member", "active"), (U_PENDING, ORG_A, "member", "pending"),
                (U_OTHER, ORG_B, "member", "active")]
        for uid, org, role, status in rows:
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, true)",
                uid, org, email_of(uid), f"AUTHZ1 {NAMES[uid]}", sub_of(uid), role, status)
        # The duplicate: a placeholder row like the real Google one.
        await conn.execute(
            "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
            "VALUES ($1, $2, $3, 'Member', $4, 'member', 'active', true)",
            U_DUP, ORG_A, f"{sub_of(U_DUP)}@placeholder.local", sub_of(U_DUP))
        await conn.execute("INSERT INTO user_roles VALUES ($1, $2)", U_MEMBER, roles[(ORG_A, "member")])
        await conn.execute("INSERT INTO user_roles VALUES ($1, $2)", U_DUP, roles[(ORG_A, "member")])
        await conn.execute("INSERT INTO user_roles VALUES ($1, $2)", U_STAFF, roles[(ORG_A, "investment_staff")])
        await conn.execute("INSERT INTO user_roles VALUES ($1, $2)", U_OTHER, roles[(ORG_B, "member")])
        await rbac.grant_org_admin(conn, U_ORGADMIN, ORG_A)
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES "
                           "($1, $2, 'individual', $3), ($4, $5, 'individual', $6)",
                           E_A, ORG_A, MARK_A, E_B, ORG_B, MARK_B)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, E_A, U_MEMBER)
        await conn.execute("INSERT INTO deals (id, org_id, name) VALUES ($1, $2, $3)", DEAL_A, ORG_A,
                           "AUTHZ1 verify deal")
        await conn.execute("INSERT INTO spvs (id, org_id, deal_id, name) VALUES ($1, $2, $3, $4)",
                           SPV_A, ORG_A, DEAL_A, "AUTHZ1 verify SPV")
    return roles


# ═══ The real app, token stubbed by bearer string ══════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
PROFILES: dict[str, dict | None] = {}


def claims_for(uid: UUID) -> dict:
    org = ORG_B if uid == U_OTHER else ORG_A
    return {"sub": sub_of(uid), "org_id": str(org)}


def token_for(claims: dict) -> str:
    key = "t" + str(abs(hash(json.dumps(claims, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = dict(claims)
    return key


async def api(claims, method, path, body=None, timeout=45.0):
    import main

    headers = {} if claims is None else {"Authorization": f"Bearer {token_for(claims)}"}
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
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def fake_profile(request, claims):
        return PROFILES.get(claims.get("sub"))

    users_mod.fetch_auth0_profile = fake_profile


# ═══ Sections ══════════════════════════════════════════════════════════════
def y_task1():
    section("[Y] Task 1 — docs/AUTHZ1_ROUTES.md lists every app route with its gate before and after")
    import authz1_routes as R

    routes, rows = R.build()
    data = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    n_docs = sum(1 for r in routes if r.get("kind") == "docs")
    md = ROUTES_MD.read_text(encoding="utf-8")
    md_rows = [ln for ln in md.splitlines() if re.match(r"^\| [A-Z,]+ \| `/", ln)]
    check(len(rows) == len(routes) - n_docs and len(md_rows) == len(rows) > 0,
          "the doc has one row per app route (flattened app.routes minus the docs routes)",
          f"{len(md_rows)} rows; {len(routes)} routes − {n_docs} docs")
    check(len(data["routes"]) == len(rows), "AUTHZ1_ROUTES.json covers the same routes", f"{len(data['routes'])}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=300)
    check(code == 0 and "CURRENT" in out, "the route doc is current with the code (regenerated == committed)",
          (out + err)[-200:])
    for ln in md_rows:
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) < 9:
            check(False, "every route row has 8 columns", ln[:120])
            return
    open_writes = [r for r in data["routes"] if r["write"] and not r["public"]
                   and r["before_effect"].startswith("OPEN")]
    unmarked = [r["path"] for r in open_writes
                if not any(f"`{r['path']}`" in ln and "[FIND]" in ln and ",".join(r["methods"]) in ln
                           for ln in md_rows)]
    check(len(open_writes) > 0 and not unmarked,
          "every write with no effective production gate is marked [FIND]",
          f"{len(open_writes)} open writes; unmarked {unmarked[:3]}")
    still_open = [f"{','.join(r['methods'])} {r['path']}" for r in data["routes"]
                  if r["write"] and not r["public"] and r["after"] == "none beyond auth" and not r["note"]]
    check(not still_open, "every write left at 'active account only' states why (self-scoped / public)",
          f"{still_open[:4]}")


def y_no_default_allow():
    section("[Y] No module grants on an absent claim or an absent role")
    import inspect

    from services import authz, permissions, rbac

    hits = []
    for mod in (authz, permissions, rbac):
        src = inspect.getsource(mod)
        if re.search(r"is None:\s*\n\s*return True", src):
            hits.append(f"{mod.__name__}: 'is None: return True'")
        if re.search(r"if not (roles|perms|permissions)\b.*:\s*\n\s*return True", src):
            hits.append(f"{mod.__name__}: 'if not roles: return True'")
        if "_has_any_role" in src or "_NO_ROLES" in src:
            hits.append(f"{mod.__name__}: no-roles sentinel")
    # Repo-wide: any authorization-shaped helper with an absent-claim allow.
    for f in list((API_DIR / "routers").glob("*.py")) + list((API_DIR / "services").rglob("*.py")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\n(?:async )?def (has_permission|is_staff|can_\w+|require_\w+|is_super_admin)\([^)]*\)[^:]*:"
                             r"(?P<body>(?:\n(?:    .*|\s*))+)", src):
            body = m.group("body")
            if re.search(r"(claim|roles|permissions|perms)\w* is None:\s*\n\s*return True", body):
                hits.append(f"{f.name}:{m.group(1)}")
    check(not hits, "source scan: no default-allow in any authorization helper", f"{hits}")
    src = inspect.getsource(rbac.has_permission)
    check("_has_any_role" not in src and "no roles" not in src.lower().split('"""')[-1],
          "services.rbac.has_permission has no no-roles branch")
    psrc = inspect.getsource(permissions)
    check("services.authz" in psrc and "get_roles" not in psrc and "_first_list_claim" not in psrc,
          "services.permissions is a thin shim over services.authz (no claim readers left)")
    menu = (WEB_DIR / "lib" / "menuVisibility.mjs").read_text(encoding="utf-8")
    canperm = re.search(r"export function canPerm\(me, permission\) \{(.*?)\n\}", menu, re.S)
    check(canperm is not None and "roles.length === 0" not in canperm.group(1)
          and "isSuperAdmin(me)" in canperm.group(1),
          "menuVisibility.canPerm: Super Admin first, no role-less default-allow")


def _load_old(name: str, rel: str):
    src = git_show(rel)
    if src is None:
        raise RuntimeError(f"cannot read {rel} at {BEFORE_REF}")
    mod = types.ModuleType(name)
    mod.__file__ = f"<{BEFORE_REF}:{rel}>"
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


async def y_reproduce(conn):
    section(f"[Y] Bug reproduced BEFORE (the {BEFORE_REF} helpers, on fixtures)")
    import routers.marketplace as mkt
    import routers.spv as spv
    from services.database import get_pool, reset_rls_context, set_rls_context

    old_perm = _load_old("authz1_old_permissions", "apps/api/services/permissions.py")
    old_rbac = _load_old("authz1_old_rbac", "apps/api/services/rbac.py")
    check("return True" in (git_show("apps/api/services/permissions.py") or "")
          and old_perm.has_permission(SimpleNamespace(state=SimpleNamespace(user={"sub": "x"})), "manage_deals"),
          "old services.permissions.has_permission allows when the token has no permissions claim")

    norole = claims_for(U_NOROLE)
    new_spv, new_mkt = spv.require_permission, mkt.require_permission
    spv.require_permission = old_perm.require_permission
    mkt.require_permission = old_perm.require_permission
    try:
        code, body = await api(norole, "PATCH", f"/api/v1/spvs/{SPV_A}", {"name": "AUTHZ1 BEFORE spv write"})
        name = await platform(conn, "SELECT name FROM spvs WHERE id = $1", SPV_A, fetch="fetchval")
        check(code == 200 and name == "AUTHZ1 BEFORE spv write",
              "BEFORE: a member with NO roles performs the staff-only SPV write (manage_deals) and it persists",
              f"HTTP {code}; re-read name {name!r}")
        code, body = await api(norole, "PUT", f"/api/v1/deals/{DEAL_A}", {"description": "AUTHZ1 BEFORE deal write"})
        desc = await platform(conn, "SELECT description FROM deals WHERE id = $1", DEAL_A, fetch="fetchval")
        check(code == 200 and desc == "AUTHZ1 BEFORE deal write",
              "BEFORE: the same member performs a Marketplace manage_deals write and it persists",
              f"HTTP {code}; re-read description {desc!r}")
    finally:
        spv.require_permission, mkt.require_permission = new_spv, new_mkt

    pool = await get_pool()
    tokens = set_rls_context(str(ORG_A), False)
    try:
        allowed = await old_rbac.has_permission(pool, str(U_NOROLE), str(ORG_A), "manage_deals")
    finally:
        reset_rls_context(tokens)
    check(allowed is True, "BEFORE: old services.rbac.has_permission default-allows the role-less member")

    old_users = _load_old("authz1_old_users", "apps/api/services/users.py")
    import main

    hw_claims = {"sub": SUB_PREFIX + "hw-old", "iss": main.get_settings().hollisworks_issuer}
    req = SimpleNamespace(state=SimpleNamespace(user=hw_claims), headers={})
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        uid = await old_users.ensure_user(conn, req)
    row = await platform(conn, "SELECT role, org_id FROM users WHERE auth0_sub = $1", hw_claims["sub"],
                         fetch="fetchrow")
    check(row is not None and row["role"] == "super_admin" and row["org_id"] == HOLLISWORKS_ORG,
          "BEFORE: an unknown Hollisworks-issuer login is created super_admin by the old ensure_user",
          f"{dict(row) if row else None} (id {uid})")


async def y_after(conn):
    section("[Y] AFTER — identical requests refused 403 naming the permission; data unchanged; the right role succeeds")
    norole = claims_for(U_NOROLE)
    before = await platform(conn, "SELECT name FROM spvs WHERE id = $1", SPV_A, fetch="fetchval")
    code, body = await api(norole, "PATCH", f"/api/v1/spvs/{SPV_A}", {"name": "AUTHZ1 AFTER spv write"})
    after = await platform(conn, "SELECT name FROM spvs WHERE id = $1", SPV_A, fetch="fetchval")
    check(code == 403 and "manage_deals" in json.dumps(body, ensure_ascii=False), "AFTER: the role-less member's SPV write → 403 naming manage_deals",
          f"HTTP {code}: {body}")
    check(after == before, "AFTER: independent re-read — the SPV is unchanged", f"{before!r} → {after!r}")

    mbefore = await platform(conn, "SELECT description FROM deals WHERE id = $1", DEAL_A, fetch="fetchval")
    code, body = await api(norole, "PUT", f"/api/v1/deals/{DEAL_A}", {"description": "AUTHZ1 AFTER deal write"})
    mafter = await platform(conn, "SELECT description FROM deals WHERE id = $1", DEAL_A, fetch="fetchval")
    check(code == 403 and "manage_deals" in json.dumps(body, ensure_ascii=False),
          "AFTER: the same Marketplace write → 403 naming manage_deals", f"HTTP {code}: {body}")
    check(mafter == mbefore, "AFTER: independent re-read — the deal is unchanged")

    code, body = await api(claims_for(U_MEMBER), "PATCH", f"/api/v1/spvs/{SPV_A}", {"name": "AUTHZ1 member try"})
    check(code == 403 and "manage_deals" in json.dumps(body, ensure_ascii=False),
          "AFTER: a member WITH the member role (no manage_deals) is refused too", f"HTTP {code}")
    code, body = await api(claims_for(U_STAFF), "PATCH", f"/api/v1/spvs/{SPV_A}", {"name": "AUTHZ1 staff write"})
    name = await platform(conn, "SELECT name FROM spvs WHERE id = $1", SPV_A, fetch="fetchval")
    check(code == 200 and name == "AUTHZ1 staff write",
          "AFTER: investment_staff (holds manage_deals) makes the identical SPV write; it persists", f"HTTP {code}")
    code, body = await api(claims_for(U_STAFF), "PUT", f"/api/v1/deals/{DEAL_A}", {"description": "AUTHZ1 staff deal"})
    check(code == 200, "AFTER: investment_staff makes the identical Marketplace write", f"HTTP {code}")
    code, body = await api(claims_for(U_SUPER), "PATCH", f"/api/v1/spvs/{SPV_A}", {"name": "AUTHZ1 super write"})
    check(code == 200, "AFTER: Super Admin (no roles at all) passes first", f"HTTP {code}")
    code, body = await api(claims_for(U_ORGADMIN), "GET", "/api/v1/admin/users?status=pending")
    check(code == 200, "AFTER: the org admin (manage_org_settings) reads People & Access pending list", f"HTTP {code}")
    code, body = await api(claims_for(U_MEMBER), "GET", "/api/v1/admin/users?status=pending")
    check(code == 403 and "manage_members" in json.dumps(body, ensure_ascii=False), "AFTER: a plain member is refused that list",
          f"HTTP {code}")


# ── Route matrix ──────────────────────────────────────────────────────────
PERSONA_USERS = {"pending": U_PENDING, "member": U_MEMBER, "staff": U_STAFF, "org_admin": U_ORGADMIN,
                 "super_admin": U_SUPER, "other_org": U_OTHER}
PERSONA_PERMS = {
    "member": set(C.MEMBER_PERMS), "other_org": set(C.MEMBER_PERMS),
    "staff": set(C.INVESTMENT_STAFF_PERMS), "org_admin": {"manage_org_settings", "review_agent_proposals"},
}


def fill(path: str) -> str:
    path = re.sub(r"\{org_id\}", str(ORG_A), path)
    return re.sub(r"\{[^}]+\}", MISSING, path)


def expectation(persona: str, rule: dict, method: str) -> str:
    """'deny' | 'allow' | 'loose' (only the global rules are asserted)."""
    kind = rule["rule"]
    if persona == "super_admin":
        return "allow"
    if kind in ("mixed", "workflow") or (kind == "org_admin" and method == "GET"):
        return "loose"
    if kind == "super":
        return "deny"
    if kind == "perm":
        return "allow" if PERSONA_PERMS[persona] & set(rule["any_of"]) else "deny"
    if kind == "staff":
        return "allow" if persona == "staff" else "deny"
    if kind == "org_admin":
        return "allow" if persona == "org_admin" else "deny"
    return "allow"   # active / scoped / self


async def y_matrix(conn):
    section("[Y] Route matrix — every route × seven personas against AUTHZ1_ROUTES.json")
    from services.authz import ACCESS_PENDING_DETAIL

    data = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    sem = asyncio.Semaphore(6)
    bad = {p: [] for p in ("anonymous", *PERSONA_USERS)}
    exercised = {p: 0 for p in bad}

    async def one(persona, method, path, rule, public):
        claims = None if persona == "anonymous" else claims_for(PERSONA_USERS[persona])
        exp = None
        if persona == "anonymous":
            if public:
                return
            exp = "anon"
        elif persona == "pending":
            # Public paths and the provisioning-exempt invite claim are open by design.
            if public or path == "/api/v1/enroll/accept":
                return
            # A self-scoped route a pending login may reach (rule "self", e.g. USERMGMT1's
            # PUT /users/me/contact-email): a write is never fired; anonymous is still 401.
            exp = "me" if (rule.get("pending_allowed") or (path == "/api/v1/users/me" and method == "GET")) \
                else "pending"
        else:
            if public:
                return
            exp = expectation(persona, rule, method)
        if method != "GET" and exp in ("allow", "loose", "me"):
            return   # never fire a write the persona may legitimately perform
        async with sem:
            code, body = await api(claims, method, fill(path), {} if method != "GET" else None, timeout=40)
        exercised[persona] += 1
        text = json.dumps(body, ensure_ascii=False)
        label = f"{method} {path} → {code}"
        if exp == "anon" and code != 401:
            bad[persona].append(label)
        elif exp == "pending" and not (code == 403 and ACCESS_PENDING_DETAIL in text):
            bad[persona].append(label)
        elif exp == "me" and code != 200:
            bad[persona].append(label)
        elif exp == "deny":
            if 200 <= code < 300:
                bad[persona].append("unexpected 2xx " + label)
            elif code == 403 and rule["rule"] == "perm" and not any(p in text for p in rule["any_of"]):
                bad[persona].append("403 not naming the permission " + label)
        elif exp in ("allow", "loose"):
            if code == 401 or (code == 403 and ACCESS_PENDING_DETAIL in text):
                bad[persona].append("refused by auth " + label)
            elif exp == "allow" and code == 403 and any(d in text for d in GATE_DETAILS):
                bad[persona].append("gate refused an allowed route " + label)
            if 200 <= code < 300 and persona != "super_admin":
                leak = MARK_B if persona != "other_org" else MARK_A
                if leak in text:
                    bad[persona].append("other-org data in " + label)

    tasks = []
    for r in data["routes"]:
        for method in r["methods"]:
            for persona in bad:
                tasks.append(one(persona, method, r["path"], r["expect"], r["public"]))
    await asyncio.gather(*tasks)
    for persona, offenders in bad.items():
        check(exercised[persona] > 0 and not offenders,
              f"route matrix — {persona}: every exercised route matches AUTHZ1_ROUTES.json (zero unexpected 2xx)",
              f"{exercised[persona]} requests; {len(offenders)} mismatches: {offenders[:6]}")
    find("the matrix fires a write only where the persona is expected to be refused",
         "allowed writes are proven by the targeted AFTER / provisioning sections, never with a blind payload")


async def y_staff(conn):
    section("[Y] is_staff comes from the DB — member is not staff, the staff fixture is")
    import routers.assistant as assistant_router
    from services import document_embedding, document_linkage

    seen: dict = {}
    orig_dl, orig_sem, orig_loop = (document_linkage.search_documents, document_embedding.semantic_search,
                                    assistant_router._run_loop)

    async def cap_dl(conn_, org_id, q, *a, **kw):
        seen.setdefault("search", []).append(kw.get("is_staff"))
        return []

    async def cap_sem(pool, org_id, user_id, query, *, is_staff=True, limit=20):
        seen.setdefault("semantic", []).append(is_staff)
        return []

    async def cap_loop(api_messages, tool_specs, action_map, pool, user_id, org_id, caller_is_staff):
        seen.setdefault("assistant", []).append(caller_is_staff)
        return {"new_messages": [], "message": "", "disclosures": [], "render": None, "proposed_action": None}

    document_linkage.search_documents = cap_dl
    document_embedding.semantic_search = cap_sem
    assistant_router._run_loop = cap_loop
    try:
        for uid in (U_MEMBER, U_STAFF):
            await api(claims_for(uid), "GET", "/api/v1/document-search?q=authz1")
            # BUGFIX1: semantic search also runs the keyword search (its "not yet
            # indexed" list) with the same flag — record that call separately so
            # "search" stays one entry per Document Search request.
            n0 = len(seen.get("search", []))
            await api(claims_for(uid), "GET", "/api/v1/semantic-search?q=authz1")
            seen.setdefault("semantic_keyword", []).extend(seen.get("search", [])[n0:])
            del seen.setdefault("search", [])[n0:]
            await api(claims_for(uid), "POST", "/api/v1/assistant/message", {"message": "authz1 verify"})
    finally:
        document_linkage.search_documents, document_embedding.semantic_search = orig_dl, orig_sem
        assistant_router._run_loop = orig_loop
    for key, label in (("search", "Document Search"), ("semantic", "semantic search"),
                       ("semantic_keyword", "semantic search's keyword pass"), ("assistant", "the assistant")):
        check(seen.get(key) == [False, True], f"{label}: member → not staff, investment_staff → staff",
              f"{seen.get(key)}")
    code, me = await api(claims_for(U_MEMBER), "GET", "/api/v1/users/me")
    code2, me2 = await api(claims_for(U_STAFF), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("is_staff") is False and code2 == 200 and me2.get("is_staff") is True,
          "/users/me publishes the same DB staff flag", f"{me.get('is_staff')} / {me2.get('is_staff')}")


async def y_provisioning(conn, roles):
    section("[Y] Provisioning — link by verified email, else pending; super admin only via PLATFORM_STAFF_EMAILS")
    import main
    from services import users as users_mod
    from services.authz import ACCESS_PENDING_DETAIL

    hw_iss = main.get_settings().hollisworks_issuer
    users_mod._userinfo_attempted.clear()

    async def last_login(uid):
        return await platform(conn, "SELECT last_login_at FROM users WHERE id = $1", uid, fetch="fetchval")

    # identity / sub / test_uuid paths stamp last_login_at
    await platform(conn, "UPDATE users SET last_login_at = NULL WHERE id = ANY($1::uuid[])", FIXTURE_USERS,
                   fetch="execute")
    code, me = await api(claims_for(U_MEMBER), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_MEMBER) and await last_login(U_MEMBER) is not None,
          "primary-sub path: resolves the user and stamps last_login_at")
    code, me = await api({"sub": str(U_NOROLE), "org_id": str(ORG_A)}, "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_NOROLE) and await last_login(U_NOROLE) is not None,
          "test-mode UUID-sub path: resolves and stamps last_login_at")

    # 1. verified email match → linked
    s_link = SUB_PREFIX + "second-login"
    PROFILES[s_link] = {"email": email_of(U_MEMBER).upper(), "email_verified": True, "name": "Second"}
    await platform(conn, "UPDATE users SET last_login_at = NULL WHERE id = $1", U_MEMBER, fetch="execute")
    code, me = await api({"sub": s_link, "org_id": str(ORG_A)}, "GET", "/api/v1/users/me")
    ident = await platform(conn, "SELECT user_id, linked_via, org_id FROM user_identities WHERE auth0_sub = $1",
                           s_link, fetch="fetchrow")
    created = await platform(conn, "SELECT count(*) FROM users WHERE auth0_sub = $1", s_link, fetch="fetchval")
    check(code == 200 and me.get("id") == str(U_MEMBER) and ident is not None and ident["user_id"] == U_MEMBER
          and ident["linked_via"] == "verified_email" and created == 0,
          "verified email match → linked via user_identities to the existing user; no new users row",
          f"HTTP {code}; {dict(ident) if ident else None}; new rows {created}")
    check(await last_login(U_MEMBER) is not None, "linked path stamps last_login_at")
    code, me = await api({"sub": s_link, "org_id": str(ORG_A)}, "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_MEMBER), "the linked sub resolves again via user_identities")

    # 2. unverified → pending, refused, listed, alerted
    s_unv = SUB_PREFIX + "unverified"
    PROFILES[s_unv] = {"email": email_of(U_STAFF), "email_verified": False}
    code, me = await api({"sub": s_unv, "org_id": str(ORG_A)}, "GET", "/api/v1/users/me")
    pend = await platform(conn, "SELECT id, status, role, email FROM users WHERE auth0_sub = $1", s_unv,
                          fetch="fetchrow")
    check(code == 200 and me.get("status") == "pending" and pend is not None and pend["status"] == "pending"
          and me.get("permissions") == [] and pend["id"] != U_STAFF,
          "unverified email (even an existing member's) → a NEW pending user with no permissions",
          f"HTTP {code}; {dict(pend) if pend else None}")
    code, body = await api({"sub": s_unv, "org_id": str(ORG_A)}, "GET", "/api/v1/entities")
    check(code == 403 and ACCESS_PENDING_DETAIL in json.dumps(body, ensure_ascii=False), "pending user refused on a data route",
          f"HTTP {code}")
    code, lst = await api(claims_for(U_ORGADMIN), "GET", "/api/v1/admin/users?status=pending")
    ids = [u.get("id") for u in lst] if isinstance(lst, list) else []
    check(code == 200 and pend is not None and str(pend["id"]) in ids,
          "the pending user is listed for the org admin's approval", f"HTTP {code}: {len(ids)}")
    todo = await platform(conn, "SELECT count(*) FROM member_todos WHERE related_id = $1 AND user_id = $2 "
                          "AND status = 'open'", pend["id"] if pend else None, U_ORGADMIN, fetch="fetchval")
    check(todo == 1, "the org admin was notified (one open member_todos alert)", f"{todo}")

    # 3. no match → pending; then approve admits
    s_nm = SUB_PREFIX + "nomatch"
    PROFILES[s_nm] = {"email": "authz1verify-nomatch@test.local", "email_verified": True}
    code, me = await api({"sub": s_nm, "org_id": str(ORG_A)}, "GET", "/api/v1/users/me")
    nm = await platform(conn, "SELECT id, status, email FROM users WHERE auth0_sub = $1", s_nm, fetch="fetchrow")
    check(nm is not None and nm["status"] == "pending" and nm["email"] == "authz1verify-nomatch@test.local",
          "verified email with no match → pending, real email kept", f"{dict(nm) if nm else None}")
    code, body = await api(claims_for(U_MEMBER), "POST", f"/api/v1/admin/users/{nm['id']}/approve",
                           {"role_id": str(roles[(ORG_A, 'member')])})
    st = await platform(conn, "SELECT status FROM users WHERE id = $1", nm["id"], fetch="fetchval")
    check(code == 403 and st == "pending", "a plain member cannot approve (403, still pending)", f"HTTP {code}")
    code, body = await api(claims_for(U_ORGADMIN), "POST", f"/api/v1/admin/users/{nm['id']}/approve",
                           {"role_id": str(roles[(ORG_A, 'member')])})
    row = await platform(conn, "SELECT u.status, array_agg(r.name) AS roles FROM users u "
                         "LEFT JOIN user_roles ur ON ur.user_id = u.id LEFT JOIN roles r ON r.id = ur.role_id "
                         "WHERE u.id = $1 GROUP BY u.status", nm["id"], fetch="fetchrow")
    check(code == 200 and row["status"] == "active" and row["roles"] == ["member"],
          "approval grants the role and admits (independent re-read)", f"HTTP {code}: {dict(row)}")
    code, body = await api({"sub": s_nm, "org_id": str(ORG_A)}, "GET", "/api/v1/entities")
    check(code == 200, "the approved user is admitted on the same data route", f"HTTP {code}")
    open_todos = await platform(conn, "SELECT count(*) FROM member_todos WHERE related_id = $1 AND status = 'open'",
                                nm["id"], fetch="fetchval")
    check(open_todos == 0, "approval closes the pending alert", f"{open_todos}")

    # reject
    code, body = await api(claims_for(U_ORGADMIN), "POST", f"/api/v1/admin/users/{pend['id']}/reject")
    st = await platform(conn, "SELECT status FROM users WHERE id = $1", pend["id"], fetch="fetchval")
    code2, body2 = await api({"sub": s_unv, "org_id": str(ORG_A)}, "GET", "/api/v1/entities")
    check(code == 200 and st == "rejected" and code2 == 403, "reject keeps the row, status 'rejected', still refused",
          f"HTTP {code}/{code2}; {st}")

    # 4. Hollisworks issuer
    prior = os.environ.get("PLATFORM_STAFF_EMAILS")
    os.environ["PLATFORM_STAFF_EMAILS"] = (prior + "," if prior else "") + "authz1verify-hwstaff@test.local"
    try:
        s_hw = SUB_PREFIX + "hw-staff"
        PROFILES[s_hw] = {"email": "authz1verify-hwstaff@test.local", "email_verified": True}
        code, me = await api({"sub": s_hw, "iss": hw_iss}, "GET", "/api/v1/users/me")
        r = await platform(conn, "SELECT role, status, org_id FROM users WHERE auth0_sub = $1", s_hw, fetch="fetchrow")
        check(code == 200 and me.get("is_super_admin") is True and r is not None and r["role"] == "super_admin"
              and r["status"] == "active" and r["org_id"] == HOLLISWORKS_ORG,
              "Hollisworks issuer + allow-listed VERIFIED email → super admin in the Hollisworks org",
              f"HTTP {code}; {dict(r) if r else None}")
        s_hwu = SUB_PREFIX + "hw-unverified"
        PROFILES[s_hwu] = {"email": "authz1verify-hwstaff2@test.local", "email_verified": False}
        os.environ["PLATFORM_STAFF_EMAILS"] += ",authz1verify-hwstaff2@test.local"
        code, me = await api({"sub": s_hwu, "iss": hw_iss}, "GET", "/api/v1/users/me")
        check(code == 200 and me.get("status") == "pending" and me.get("is_super_admin") is False,
              "Hollisworks issuer + allow-listed but UNVERIFIED email → pending, not super", f"{me.get('status')}")
        s_hwo = SUB_PREFIX + "hw-other"
        PROFILES[s_hwo] = {"email": "authz1verify-hwother@test.local", "email_verified": True}
        code, me = await api({"sub": s_hwo, "iss": hw_iss}, "GET", "/api/v1/users/me")
        r = await platform(conn, "SELECT role, status FROM users WHERE auth0_sub = $1", s_hwo, fetch="fetchrow")
        check(code == 200 and r is not None and r["status"] == "pending" and r["role"] == "member"
              and me.get("is_super_admin") is False,
              "any other Hollisworks-issuer login → pending member, never super admin by issuer",
              f"{dict(r) if r else None}")
        code, body = await api({"sub": s_hwo, "iss": hw_iss}, "GET", "/api/v1/admin/orgs")
        check(code == 403, "that pending Hollisworks login is refused on a platform route", f"HTTP {code}")
    finally:
        if prior is None:
            os.environ.pop("PLATFORM_STAFF_EMAILS", None)
        else:
            os.environ["PLATFORM_STAFF_EMAILS"] = prior

    # 5. duplicate merge (the real Google placeholder's shape)
    s_dup = sub_of(U_DUP)
    PROFILES[s_dup] = {"email": email_of(U_STAFF), "email_verified": True}
    users_mod._userinfo_attempted.discard(s_dup)
    await platform(conn, "UPDATE users SET last_login_at = NULL WHERE id = $1", U_STAFF, fetch="execute")
    code, me = await api(claims_for(U_DUP), "GET", "/api/v1/users/me")
    dup = await platform(conn, "SELECT status FROM users WHERE id = $1", U_DUP, fetch="fetchval")
    ident = await platform(conn, "SELECT user_id, linked_via FROM user_identities WHERE auth0_sub = $1", s_dup,
                           fetch="fetchrow")
    refs = await platform(conn, "SELECT count(*) FROM user_roles WHERE user_id = $1", U_DUP, fetch="fetchval")
    check(code == 200 and me.get("id") == str(U_STAFF) and dup == "retired" and ident is not None
          and ident["user_id"] == U_STAFF and ident["linked_via"] == "duplicate_merge",
          "a placeholder duplicate with a verified matching email → sub linked, placeholder row RETIRED",
          f"HTTP {code}; status {dup}; {dict(ident) if ident else None}")
    check(refs == 1, "the retired row's references are kept (not deleted)", f"user_roles {refs}")
    check(await last_login(U_STAFF) is not None, "merge path stamps last_login_at on the surviving user")
    code, me = await api(claims_for(U_DUP), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_STAFF), "the merged sub keeps resolving to the surviving user")


async def y_real_users(conn):
    section("[Y] Real users — effective permissions == the Task 3 report; Joe keeps super admin")
    from services.authz import load_principal

    report = json.loads(REAL_USERS_JSON.read_text(encoding="utf-8"))
    now = None
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        now = await C.effective_report(conn, legacy=False)
        principals = {}
        for email, r in now.items():
            p = await load_principal(conn, r["user_id"])
            principals[email] = p
    users = report["users"]
    for email, r in users.items():
        cur = now.get(email)
        p = principals.get(email)
        if email == "google-oauth2|114785352792351858712@placeholder.local" and cur and cur["status"] == "retired":
            check(p is not None and not p.admitted and p.permissions == frozenset(),
                  f"{email}: retired → no effective permissions")
            continue
        check(cur is not None and cur["effective"] == r["effective_after"]
              and p is not None and sorted(p.permissions) == r["effective_after"],
              f"{email}: effective permissions (report recompute AND services.authz Principal) == Task 3 report",
              f"{len(r['effective_after'])} expected; now {len(cur['effective']) if cur else None}")
        lost = r["lost"]
        check(not lost or r["lost_only_via_default_allow"], f"{email}: anything lost existed only through default-allow",
              f"lost {len(lost)}")
    for uid in ("06fe93ed-bf7e-51d1-adc3-3023e959e382", "f46eb620-f03c-49ab-b946-eefa621022f7",
                "be8165db-84d7-4e69-91d3-62f603530824"):
        p = next((pp for pp in principals.values() if pp and pp.user_id == uid), None)
        check(p is not None and p.is_super_admin is True and p.admitted, f"Joe's account {uid} keeps super admin")
    g = await platform(conn, "SELECT u.status, i.user_id AS linked_to FROM users u LEFT JOIN user_identities i "
                       "ON i.auth0_sub = u.auth0_sub WHERE u.auth0_sub = $1",
                       "google-oauth2|114785352792351858712", fetch="fetchrow")
    gmail = UUID("f46eb620-f03c-49ab-b946-eefa621022f7")
    linked = g is not None and g["status"] == "retired" and g["linked_to"] == gmail
    awaiting = g is not None and g["status"] == "active" and g["linked_to"] is None
    check(linked or awaiting,
          "Google placeholder: retired AND its sub linked to jlarizza@gmail.com — or still awaiting its next sign-in",
          f"{dict(g) if g else None}")
    if awaiting:
        find("the Google login has not signed in since AUTHZ1 deployed",
             "the merge needs that login's VERIFIED email from /userinfo (no Management API grant on either tenant); "
             "it happens on its next sign-in — the identical path is proven on the U_DUP fixture above. "
             "Operator alternative: _apply_authz1.py --operator-link-google")
    hw = await platform(conn, "SELECT email FROM users WHERE id = $1", UUID("be8165db-84d7-4e69-91d3-62f603530824"),
                        fetch="fetchval")
    if hw and hw.endswith("@placeholder.local"):
        find("Joe's Hollisworks login still carries its placeholder email",
             "back-fill needs /userinfo on its next sign-in; if the address is jlarizza@gmail.com it is already taken "
             "by the 2nd Act row (users.email is UNIQUE platform-wide), so it stays a placeholder by design")
    refs = report.get("google_placeholder_references") or {}
    check(isinstance(refs, dict) and "public.user_roles.user_id" in refs,
          "every table referencing the Google placeholder row is reported", f"{refs}")


async def y_test_mode(conn):
    section("[Y] AUTHZ_TEST_MODE — UUID-sub stub and DEV_USER_ID only under the flag")
    from fastapi import HTTPException

    from services import authz, permissions

    check(authz.TEST_MODE is True, "this process set AUTHZ_TEST_MODE=1 before import")
    claims = {"sub": str(U_MEMBER), "org_id": str(ORG_A)}
    authz.TEST_MODE = False
    try:
        code, me = await api(claims, "GET", "/api/v1/users/me")
        check(not (code == 200 and me.get("id") == str(U_MEMBER)),
              "without the flag a UUID sub does NOT resolve to that users.id (it is an unknown login → pending)",
              f"HTTP {code}; status {me.get('status') if isinstance(me, dict) else None}")
        req = SimpleNamespace(state=SimpleNamespace(user={}), headers={})
        try:
            permissions.get_user_id(req)
            ok = False
        except HTTPException as exc:
            ok = exc.status_code == 401
        check(ok, "without the flag the DEV_USER_ID fallback is a 401")
        code, body = await api({"org_id": str(ORG_A)}, "GET", "/api/v1/entities")
        check(code == 401, "a token with no resolvable user → 401 from the middleware", f"HTTP {code}")
    finally:
        authz.TEST_MODE = True
    await platform(conn, "DELETE FROM member_todos WHERE related_id IN (SELECT id FROM users WHERE auth0_sub = $1)",
                   str(U_MEMBER), fetch="execute")
    code, me = await api({"sub": str(U_NOROLE), "org_id": str(ORG_A)}, "GET", "/api/v1/users/me")
    check(code == 200 and me.get("id") == str(U_NOROLE), "with the flag the UUID-sub stub resolves")
    req = SimpleNamespace(state=SimpleNamespace(user={}), headers={})
    check(permissions.get_user_id(req) == permissions.DEV_USER_ID, "with the flag DEV_USER_ID is returned")
    import inspect

    import main

    check("AUTHZ_TEST_MODE=1 is set" in inspect.getsource(main._startup),
          "the API logs a warning at startup when the flag is set")
    changed = [p for p in HERE.glob("verify_*.py") if 'environ["AUTHZ_TEST_MODE"] = "1"' in p.read_text(encoding="utf-8")]
    total = list(HERE.glob("verify_*.py"))
    check(len(changed) == len(total) > 0, "every verify script sets the flag before importing the app",
          f"{len(changed)}/{len(total)}")


async def y_front_end(conn):
    section("[Y] Front end — /users/me from the Principal; canPerm fails closed; pending page; build")
    code, me = await api(claims_for(U_MEMBER), "GET", "/api/v1/users/me")
    check(code == 200 and sorted(me.get("permissions") or []) == sorted(C.MEMBER_PERMS)
          and me.get("status") == "active" and me.get("is_super_admin") is False,
          "/users/me returns the DB permission set, status and flags", f"{me.get('status')}")
    code, me = await api(claims_for(U_PENDING), "GET", "/api/v1/users/me")
    check(code == 200 and me.get("status") == "pending" and me.get("permissions") == [],
          "/users/me for a pending account: 200, status 'pending', no permissions")
    code, out, err = run_cmd(["node", "--test", "tests/nav/"], WEB_DIR, timeout=300)
    check(code == 0, "node --test tests/nav (incl. accessPending.test.mjs) passes", (out + err)[-300:] if code else "")
    for name in ("pending page renders for status 'pending'",
                 "canPerm has no default-allow: a role-less payload sees only always-visible items"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


async def y_isolation():
    section("[Y] Cross-org isolation unchanged — app_service, rolbypassrls false, both directions")
    dsn, how = await app_service_dsn()
    if not dsn:
        dsn, how = await admin_dsn()
    c = await connect(dsn)
    try:
        if await c.fetchval("SELECT current_user") != "app_service":
            await c.execute("SET ROLE app_service")
        bypass = await c.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(await c.fetchval("SELECT current_user") == "app_service" and bypass is False,
              "the isolation connection is app_service with rolbypassrls = false", f"{how}; bypass={bypass}")
        a = {r["id"] for r in await scoped(c, ORG_A, "SELECT id FROM entities WHERE id = ANY($1::uuid[])", [E_A, E_B])}
        b = {r["id"] for r in await scoped(c, ORG_B, "SELECT id FROM entities WHERE id = ANY($1::uuid[])", [E_A, E_B])}
        check(a == {E_A} and b == {E_B}, "entities: org A sees only A's, org B only B's", f"{a} / {b}")
        ia = await scoped(c, ORG_A, "SELECT count(*) FROM user_identities WHERE org_id = $1", ORG_A, fetch="fetchval")
        ib = await scoped(c, ORG_B, "SELECT count(*) FROM user_identities WHERE org_id = $1", ORG_A, fetch="fetchval")
        check(ia > 0 and ib == 0, "user_identities: org A's links are invisible from org B", f"{ia} / {ib}")
        ua = await scoped(c, ORG_B, "SELECT count(*) FROM users WHERE id = $1", U_MEMBER, fetch="fetchval")
        check(ua == 0, "users: org B cannot read an org A user")
    finally:
        await c.close()
    code, _ = await api(claims_for(U_OTHER), "GET", f"/api/v1/entities/{E_A}")
    code2, _ = await api(claims_for(U_MEMBER), "GET", f"/api/v1/entities/{E_B}")
    code3, ok = await api(claims_for(U_MEMBER), "GET", f"/api/v1/entities/{E_A}")
    check(code == 404 and code2 == 404 and code3 == 200,
          "through the API: each org's user gets 404 for the other org's entity, 200 for their own",
          f"{code}/{code2}/{code3}")


def regression():
    section("[Y] Regression — flat subprocesses AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1"}
    for script in ("verify_navregroup.py", "verify_bulkfetchui2.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=1200, env=env)
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(m is not None and m.group(2) == "0" and code == 0, f"{script}: 0 FAIL",
              f"{m.group(0) if m else 'no TOTAL'}; {fails[:3]} {err[-200:] if not m else ''}")
    code, out, err = run_cmd(["node", str(HERE / "menuvisibility_harness.mjs")], REPO, timeout=120, env=env)
    try:
        d = json.loads(out)
        regs = d["regressions"]
        ok = all(r["unchanged"] or r.get("authz1_expected") for r in regs)
        personas_ok = all(p["removals_exact"] and p["additions_exact"] and not p["new_refusals"]
                          for p in d["navregroup"]["personas"])
        check(code == 0 and ok and personas_ok,
              "menu-visibility harness: every persona unchanged except AUTHZ1's exact default-allow removal",
              f"{[(r['name'], r['lost']) for r in regs if not r['unchanged']]}")
    except Exception as exc:  # noqa: BLE001
        check(False, "menu-visibility harness output parses", f"{exc}: {(err or out)[-200:]}")


# Operator convenience: AUTHZ1_ONLY="task 1,route matrix" runs just those
# sections (seed + teardown always run). Unset = everything.
_ONLY = {x.strip() for x in os.environ.get("AUTHZ1_ONLY", "").split(",") if x.strip()}


async def run(name, fn, *args):
    if _ONLY and name not in _ONLY and name not in ("pre-teardown", "seed"):
        print(f"[SKIP] {name} (AUTHZ1_ONLY)")
        return
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


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
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        roles = {}

        async def do_seed():
            roles.update(await seed(conn))

        await run("seed", do_seed)
        await run("task 1", y_task1)
        await run("no default-allow", y_no_default_allow)
        await run("reproduce", y_reproduce, conn)
        await run("after", y_after, conn)
        await run("is_staff", y_staff, conn)
        if roles:
            await run("provisioning", y_provisioning, conn, roles)
        await run("real users", y_real_users, conn)
        await run("test mode", y_test_mode, conn)
        await run("isolation", y_isolation)
        await run("route matrix", y_matrix, conn)
        await run("front end", y_front_end, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count")
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (users by sub/email/id, orgs, identities)", f"{left}")
            post = await counts(conn)
            for t in COUNTED:
                check(post[t] == pre[t], f"{t} back at its pre-test count", f"{pre[t]} → {post[t]}")
            check(post["gold_checksum"] == pre["gold_checksum"], "real note_gold_values unchanged (count and checksum)")
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
