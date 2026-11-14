"""verify_udfui1 — UDFUI1: the custom-fields builder + the relationship-status clean-up script (2026-11-13).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch is stubbed and httpx's network transport refuses. No model call.

The real app against the live database, in TWO fixture orgs (99000000-…-000000730001 / 730002), ``is_test = true``
(asserted), as are the fixture users. Org A: an org admin (manage_portfolio + manage_org_settings + manage_members +
create_tags), an advisor (profile ADV), support staff (permission set OPS), a dual user (profile ADV AND set OPS), a
member; entities p1 / p2 (custom-field targets) and the STATUS fixtures sa1 / sa2 ('active'), sc ('contact'),
sp ('prospect'). Org B: an org admin and sb ('active' — proves --org scoping). Real orgs are only read; no real
entity's status is changed. Teardown at start and end by fixture id (_verify_cleanup), counts compared.

What it proves
  * GATE — every builder read (admin definitions / tabs, audit, tab grants, preview) is 403 for the advisor, support
    staff and a member (no manage_portfolio) and 200 for the org admin with can_write true; sampled builder writes
    refuse the advisor with 403 naming manage_portfolio; org B's admin never sees org A's fields; the page renders no
    builder without can_write (node test).
  * ROUND TRIP — create → edit → deactivate → reactivate → delete (refused while a value exists: 409 with the
    references; allowed for a field with none) → undelete, each visible in the admin list's status AND on nav3's
    record-page reads (GET /udf/tabs, /udf/layouts, /udf/values, /udf/definitions) for the advisor with the right
    access; every step leaves its udf_definition_audit row (who / kind), served by GET …/audit.
  * PERMISSIONS — tab / field grants by profile and permission set (the existing PUT routes, listed back by the GET
    routes); GET /udf/preview for EACH persona = resolve_tab_visibility / resolve_field_access_bulk called directly
    (most restrictive wins for the dual user) = what that persona's own reads show (hidden fields absent, a hidden tab
    403, read-only items flagged).
  * LAYOUT — sections reorder (PUT …/sections/order) and items reorder (PATCH display_order) round-trip; removing a
    section with items is refused (409); tab order (PATCH display_order) and the tab lifecycle round-trip; deleting a
    tab with a non-empty layout is refused (409).
  * TAGS — assign, merge (POST …/merge): targets repointed, the merged code gone from the vocabulary.
  * RECORDS — GET /udf/records/entity shows p1's values; the CSV export carries the same values and row count.
  * STATUS — the vocabulary no longer OFFERS 'active' (selectable false, retired true) but validation still ACCEPTS
    it (POST and PUT /entities); an unknown status is refused (422); the clean-up script's dry run (--org A) lists
    exactly the 'active' rows (= SQL) and changes nothing; the real run moves them to 'client' through the entity
    write path — same id, an archived version with the old status, an audit_log row — 'contact' / 'prospect'
    untouched, org B untouched; a re-run finds nothing; the real rows' status counts are unchanged.
  * STATIC (--self-checks-only) — the six new routes only, no route removed; every builder control maps to a route
    with the expected gate in AUTHZ1_ROUTES.json; the forwarder's allow-list; the menu entry + gate; the vocabulary
    rule; the script uses the write path; notes / regression set / PROJECT_STATUS.
  * (full run) node tests tests/udfui1 + nav3 + nav1; authz1 / visibility1 matrices CURRENT; fast regressions
    verify_nav3 (the udf00…udf02b verifies are not nestable — recorded as [FIND], as NAV3 did).

--self-checks-only: git / static checks only (stdlib + git; no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import csv
import inspect
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(_site))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)
import _verify_roles as VR  # noqa: E402
import _verify_scope as VSC  # noqa: E402  (stdlib only)

SPRINT_KEY = "udfui1"
BEFORE_REF = "4ec3aef"   # udfui1-work base: "merge nav3"
NEW_ROUTES = {
    ("GET", "/udf/admin/definitions"), ("GET", "/udf/admin/tabs"), ("GET", "/udf/definitions/{definition_id}/audit"),
    ("GET", "/udf/tabs/{tab_id}/permissions"), ("GET", "/udf/preview"), ("PUT", "/udf/layouts/{tab_id}/sections/order"),
}
#: the gate each builder action's route must carry in docs/AUTHZ1_ROUTES.md ("after" column), by hand
BUILDER_READ_GATE = "authz manage_portfolio; internal"
WRITE_GATE = "authz manage_portfolio"
GATE_BY_ACTION = {
    "listDefinitions": BUILDER_READ_GATE, "definitionAudit": BUILDER_READ_GATE, "listTabs": BUILDER_READ_GATE,
    "tabGrants": BUILDER_READ_GATE, "preview": BUILDER_READ_GATE, "fieldGrants": WRITE_GATE,
    "layout": "authz view_portfolio", "tagVocabulary": "authz view_portfolio",
    "records": "authz view_portfolio; internal", "exportRecords": "authz view_portfolio; internal",
}
MENU = "apps/web/lib/menuVisibility.mjs"


def _git(*args):
    return VS.git(*args)[:2]


def self_checks(check, find):
    ok, why = VS.own_base_ok(BEFORE_REF, SPRINT_KEY)
    check(ok, f"BEFORE_REF {BEFORE_REF} lies on the sprint's own line", why)
    code, d, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "apps/api/routers", args=("-U0",))
    deco = r'^([+-])@router\.(get|post|put|patch|delete)\("([^"]+)"'
    added = {(m.group(2).upper(), m.group(3)) for ln in d.splitlines() if (m := re.match(deco, ln)) and m.group(1) == "+"}
    removed = {(m.group(2).upper(), m.group(3)) for ln in d.splitlines() if (m := re.match(deco, ln)) and m.group(1) == "-"}
    check(bool(code == 0 and not removed), "no FastAPI route removed or changed", f"{sorted(removed)}")
    check(added == NEW_ROUTES, "exactly the six builder routes added (5 reads + sections/order)", f"{sorted(added)}")

    def text(path):
        return VS.own_text(SPRINT_KEY, path) or ""

    lib = text("apps/web/lib/udfBuilder.mjs")
    acts = dict(re.findall(r'^\s+(\w+): \["(?:GET|POST|PUT|PATCH|DELETE)", "([^"]+)"\]', lib, re.M))
    methods = dict(re.findall(r'^\s+(\w+): \["(GET|POST|PUT|PATCH|DELETE)", "[^"]+"\]', lib, re.M))
    check(len(acts) >= 30, "lib/udfBuilder.mjs lists every builder control's route", f"{len(acts)}")
    authz = json.loads(text("docs/AUTHZ1_ROUTES.json") or "{}")
    by_route = {}
    for r in authz.get("routes", []):
        for m in r.get("methods") or []:
            by_route[(m, r["path"])] = r
    md = text("docs/AUTHZ1_ROUTES.md")
    missing, wrong = [], []
    for action, tpl in acts.items():
        path = "/api/v1/udf/" + re.sub(r":(\w+)", "{X}", tpl)
        hit = [k for k in by_route if k[0] == methods[action]
               and re.sub(r"\{[^}]+\}", "{X}", k[1]) == path]
        if not hit:
            missing.append(f"{action} {methods[action]} {path}")
            continue
        real = hit[0][1]
        want = GATE_BY_ACTION.get(action, WRITE_GATE)
        line = next((ln for ln in md.splitlines() if f"| {methods[action]} | `{real}` |" in ln), "")
        if f"| {want} |" not in line:
            wrong.append(f"{action}: {line.split('|')[7].strip() if line.count('|') > 7 else line!r} ≠ {want}")
    check(not missing, "every builder control maps to a real FastAPI route", f"{missing}")
    check(not wrong, "every builder control's route carries its expected gate (AUTHZ1_ROUTES.md after-column)",
          f"{wrong}")
    fwd = text("apps/web/app/api/udf/builder/[...path]/route.js")
    check(bool("isForwardable(request.method, segs)" in fwd and "stripOrg(raw)" in fwd),
          "the one Next forwarder forwards only UDF_ROUTES and strips org_id")
    menu = text(MENU)
    check(bool('export const GATE_UDF_BUILDER = { perm: "manage_portfolio", strict: true };' in menu
               and 'tab("org_admin", "settings", 5, "/admin/custom-fields", "Custom fields & tabs", GATE_UDF_BUILDER, true)'
               in menu), "menu: Firm settings → Custom fields & tabs, gate manage_portfolio (strict)")
    code, md2, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, MENU, args=("-U0",))
    changed = [ln for ln in md2.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    check(bool(code == 0 and not [ln for ln in changed if ln.startswith("-")]),
          "menuVisibility.mjs: lines only added (no existing item or gate changed)", f"{changed[:4]}")
    cfg = text("apps/api/services/crm_config.py")
    check(bool('RETIRED_STATUSES = ("active",)' in cfg and 'if s["selectable"] or s.get("retired")' in cfg
               and '"selectable": vt == SELECTABLE and not retired' in cfg),
          "crm_config: 'active' retired — not offered, still accepted")
    scr = text("apps/api/scripts/relationship_status_cleanup.py")
    check(bool("update_entity_row(" in scr and "write_audit_log(" in scr and "platform_scope(" in scr
               and "set_rls_context(" in scr and not re.search(r"UPDATE\s+entities", scr, re.I)
               and "--dry-run" in scr and "'contact'" in scr),
          "the clean-up script uses the entity write path + audit, per-org RLS; no bare UPDATE entities")
    notes = text("docs/UDFUI1_NOTES.md")
    check(bool("## 1. Discovery" in notes and "## 3. Findings" in notes and "manage_portfolio" in notes),
          "docs/UDFUI1_NOTES.md carries discovery, decisions and findings")
    reg = (REPO / "scripts" / "regression_set.txt").read_text(encoding="utf-8")
    check(bool(re.search(r"^verify_udfui1\.py$", reg, re.M)), "scripts/regression_set.txt lists verify_udfui1.py")
    status = text("docs/PROJECT_STATUS.md")
    check(bool(re.search(r"^UDFUI1 \(2026-11-13", status, re.M)), "docs/PROJECT_STATUS.md carries the UDFUI1 line")
    check(bool(text("apps/web/tests/udfui1/udfui1.test.mjs")), "node render tests exist (tests/udfui1)")


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


if __name__ == "__main__" and VS.SELF_CHECKS_FLAG in sys.argv:
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import close_pool  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "udfui1verify"
NAME = "UU1V "


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000073{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
ADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                         "manage_portfolio", "create_tags"})
STAFF_PERMS = frozenset({"view_members", "view_portfolio"})
U_OA, U_ADV, U_S1, U_DUAL, U_MEMBER, U_OB = (_id(n) for n in range(10, 16))
USERS = {
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ADMIN_PERMS),
    U_ADV: (ORG_A, "member", "advisor", "advisor", STAFF_PERMS),
    U_S1: (ORG_A, "member", "support", "support_staff", STAFF_PERMS),
    U_DUAL: (ORG_A, "member", "dual", "advisor", STAFF_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OB: (ORG_B, "org_admin", "obadmin", "org_admin", ADMIN_PERMS),
}
E = {
    "p1": (_id(30), ORG_A, "individual", "Aster Person", "client"),
    "p2": (_id(31), ORG_A, "individual", "Birch Person", "prospect"),
    "sa1": (_id(40), ORG_A, "individual", "Cedar Active", "active"),
    "sa2": (_id(41), ORG_A, "llc", "Cedar Holdings Active", "active"),
    "sc": (_id(42), ORG_A, "individual", "Dogwood Contact", "contact"),
    "sp": (_id(43), ORG_A, "individual", "Elm Prospect", "prospect"),
    "sb": (_id(44), ORG_B, "individual", "Fir OrgB Active", "active"),
}
PROFILE_ADV, PSET_OPS = _id(90), _id(91)
COUNTED = ("public.users", "public.organizations", "public.entities", "public.role_permissions", "public.user_roles",
           "public.profiles", "public.permission_sets", "public.user_permission_sets", "public.audit_log",
           "portfolio.udf_definitions", "portfolio.udf_definition_audit", "portfolio.udf_values", "portfolio.udf_tabs",
           "portfolio.udf_tab_permissions", "portfolio.udf_field_permissions", "portfolio.udf_layouts",
           "portfolio.udf_layout_sections", "portfolio.udf_layout_items", "portfolio.udf_tag_assignments")

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""))
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
    return f"auth0|{TAG}-{USERS[uid][2]}"


def eid(k: str) -> UUID:
    return E[k][0]


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:600]


async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED:
        try:
            out[t] = int(await platform(conn, VSC.count_sql(t, FIXTURE_ORGS, list(USERS)), fetch="fetchval"))
        except Exception as exc:  # noqa: BLE001
            out[t] = f"unreadable: {type(exc).__name__}"
    return out


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


def _plan() -> dict:
    users = list(USERS)
    return {
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                    [FIXTURE_ORGS]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
        "public.user_permission_sets": ("user_id = ANY($1::uuid[])", [users]),
    }


async def teardown(_conn=None) -> list[str]:
    op = await operator_conn()
    try:
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               label="verify_udfui1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.user_permission_sets WHERE user_id = ANY($1::uuid[])", [list(USERS)])])
    finally:
        await op.close()


async def cleanup_only(conn) -> int:
    log: list[str] = []
    try:
        log = await teardown(conn)
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


# ═══ Seed ═══════════════════════════════════════════════════════════════════════════════════════════════════════
REAL_STATUS: dict = {}


async def real_status_counts(conn) -> dict:
    return {r["status"]: int(r["n"]) for r in await platform(
        conn, "SELECT status, count(*) AS n FROM entities WHERE NOT (org_id = ANY($1::uuid[])) GROUP BY status",
        FIXTURE_ORGS)}


async def y_catalog(conn):
    section("[Y] Reserved fixture prefixes match no real row; the real status counts (before)")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($3::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND NOT (id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $5 AND NOT "
                                "(id = ANY($3::uuid[])))", NAME + "%", f"%{TAG}%", FIXTURE_ORGS, list(USERS),
                          f"{TAG}%", fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (names, subs, slugs) match no real row", f"{real}")
    ids = await platform(conn, "SELECT count(*) FROM entities WHERE id::text LIKE '99000000-0000-0000-0000-00000073%' "
                               "AND NOT (org_id = ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval")
    check(ids == 0, "the reserved fixture id range matches no real row", f"{ids}")
    REAL_STATUS.update(await real_status_counts(conn))
    find("real entities by status (all rows incl. history, outside the fixture orgs) — before", f"{REAL_STATUS}")


async def seed(conn) -> None:
    roles: dict[tuple, UUID] = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"UDFUI1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        role_perms: dict[str, set] = {}
        for _uid, (_org, _acct, _nm, rname, wanted) in USERS.items():
            role_perms.setdefault(rname, set()).update(wanted)
        dropped = {r: sorted(p - set(perm_ids)) for r, p in role_perms.items() if p - set(perm_ids)}
        if dropped:
            find("permissions not in the catalog (not granted)", f"{dropped}")
        for org in FIXTURE_ORGS:
            for rname, perms in role_perms.items():
                rid = await rbac.ensure_role(conn, org, rname, f"UDFUI1 fixture {rname}")
                for p in perms & set(perm_ids):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        await conn.execute("INSERT INTO profiles (id, org_id, name) VALUES ($1, $2, $3)", PROFILE_ADV, ORG_A,
                           NAME + "Advisor profile")
        await conn.execute("INSERT INTO permission_sets (id, org_id, name) VALUES ($1, $2, $3)", PSET_OPS, ORG_A,
                           NAME + "Operations set")
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test, "
                "profile_id) VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true, $7)",
                uid, org, f"{TAG}-{nm}@test.local", f"{NAME}{nm.title()}", sub_of(uid), account_role,
                PROFILE_ADV if uid in (U_ADV, U_DUAL) else None)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for uid in (U_S1, U_DUAL):
            await conn.execute("INSERT INTO user_permission_sets (user_id, permission_set_id) VALUES ($1, $2)",
                               uid, PSET_OPS)
        for _k, (i, org, etype, name, status) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) "
                               "VALUES ($1, $2, $3, $4, $5::entity_type)", i, org, NAME + name, status, etype)
    await VR.check_roles_as_meant(conn, check, {uid: {"org": org, "roles": {rname}, "has": perms}
                                               for uid, (org, _a, _n, rname, perms) in USERS.items()})


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test; the verify connection does not bypass RLS")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == len(FIXTURE_ORGS) and u == len(USERS)), "every fixture org and user is is_test = true", f"{o}/{u}")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(uid, method, path, body=None, timeout=180.0, raw=False):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    if raw:
        return res.status_code, res.text
    try:
        parsed = json.loads(res.text) if res.text else {}
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def install_stubs():
    import main

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_udfui1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


V1 = "/api/v1/udf"
S: dict = {}  # ids created along the way


def ids_of(body) -> set:
    return {str(r.get("id")) for r in (body or {}).get("rows") or [] if isinstance(r, dict)}


def admin_row(body, def_id):
    return next((r for r in (body or {}).get("rows") or [] if r.get("id") == def_id), None)


async def mkdef(uid, key, label, data_type="text", type_params=None):
    c, b = await api(uid, "POST", f"{V1}/definitions", {
        "owner_scope": "org", "applies_to": "entity", "field_key": key, "api_name": key, "label": NAME + label,
        "data_type": data_type, "type_params": {"length": 255} if type_params is None else type_params})
    return c, b, str(b.get("id") or "")


# ── Gate ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_gate(conn):
    section("[Y] Gate — builder reads 403 without manage_portfolio; 200 + can_write for the org admin")
    c, b, S["seed_def"] = await mkdef(U_OA, "uu1_seed", "Seed field")
    check(c == 201, "org admin creates a field (POST /udf/definitions)", f"{c} {dumps(b)}")
    c, t = await api(U_OA, "POST", f"{V1}/tabs", {"applies_to": "entity", "label": NAME + "Seed tab", "api_name": "uu1_seed_tab"})
    S["seed_tab"] = str((t.get("tab") or {}).get("id") or "")
    check(c == 201, "org admin creates a tab", f"{c} {dumps(t)}")
    reads = {"admin definitions": f"{V1}/admin/definitions", "admin tabs": f"{V1}/admin/tabs?applies_to=entity",
             "audit": f"{V1}/definitions/{S['seed_def']}/audit", "tab grants": f"{V1}/tabs/{S['seed_tab']}/permissions",
             "preview": f"{V1}/preview?user_id={U_ADV}&target_type=entity"}
    for name, path in reads.items():
        c, b = await api(U_OA, "GET", path)
        check(c == 200, f"org admin: GET {name} → 200", f"{c} {dumps(b)}")
        for uid, who in ((U_ADV, "advisor"), (U_S1, "support staff"), (U_MEMBER, "member")):
            c, b = await api(uid, "GET", path)
            check(c == 403, f"{who}: GET {name} → 403", f"{c} {dumps(b)}")
    for path in (f"{V1}/admin/definitions", f"{V1}/admin/tabs"):
        c, b = await api(U_OA, "GET", path)
        p = (b or {}).get("permissions") or {}
        check(bool(p.get("can_write") is True and (b.get("vocabularies") or {}).get("labels")),
              f"{path.rsplit('/', 1)[-1]}: envelope can_write true + server-published labels", f"{dumps(p)}")
    writes = [("POST", f"{V1}/definitions", {"owner_scope": "org", "applies_to": "entity", "field_key": "uu1_no",
                                             "label": "x", "data_type": "text", "type_params": {"length": 5}}),
              ("PATCH", f"{V1}/tabs/{S['seed_tab']}", {"label": "x"}),
              ("PUT", f"{V1}/layouts/{S['seed_tab']}/sections/order", {"section_ids": []}),
              ("POST", f"{V1}/definitions/{S['seed_def']}/deactivate", None),
              ("PUT", f"{V1}/fields/{S['seed_def']}/permissions", {"profile_id": str(PROFILE_ADV), "access": "read"})]
    for m, path, body in writes:
        c, b = await api(U_ADV, m, path, body)
        check(bool(c == 403 and "manage_portfolio" in json.dumps(b)), f"advisor {m} {path.split('/udf/')[1]} → 403 "
              "naming manage_portfolio", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "GET", f"{V1}/admin/definitions")
    check(bool(admin_row(b, S["seed_def"]) and admin_row(b, S["seed_def"])["status"] == "active"),
          "the refused deactivate left the field active")
    c, ob = await api(U_OB, "GET", f"{V1}/admin/definitions")
    check(bool(c == 200 and S["seed_def"] not in ids_of(ob)), "org B's admin never sees org A's field", f"{c}")
    c, ob = await api(U_OB, "GET", f"{V1}/preview?user_id={U_ADV}&target_type=entity")
    check(c == 422, "org B's admin cannot preview an org A person (422: not a person of this org)", f"{c} {dumps(ob)}")
    c, ob = await api(U_OB, "GET", f"{V1}/tabs/{S['seed_tab']}/permissions")
    check(c in (403, 404), "org B's admin cannot read org A's tab grants", f"{c}")


# ── Round trip ───────────────────────────────────────────────────────────────────────────────────────────────
async def y_round_trip(conn):
    section("[Y] Round trip — create → edit → deactivate → reactivate → delete → undelete, on the record page reads")
    c, b, rt = await mkdef(U_OA, "uu1_rt", "Round trip")
    c2, b2, tmp = await mkdef(U_OA, "uu1_tmp", "Temporary")
    check(bool(c == 201 and c2 == 201), "two fields created", f"{c} {c2}")
    S["rt"], S["tmp"] = rt, tmp
    c, t = await api(U_OA, "POST", f"{V1}/tabs", {"applies_to": "entity", "label": NAME + "Planning", "api_name": "uu1_planning"})
    tab = str((t.get("tab") or {}).get("id"))
    S["t1"] = tab
    c, sec = await api(U_OA, "POST", f"{V1}/layouts/{tab}/sections", {"title": "Main"})
    sid = str((sec.get("section") or {}).get("id"))
    S["t1_sec"] = sid
    for i, d in enumerate((rt, tmp)):
        c, it = await api(U_OA, "POST", f"{V1}/layouts/{tab}/sections/{sid}/items", {"definition_id": d, "display_order": i})
        check(c == 201, f"field placed on the tab ({i})", f"{c} {dumps(it)}")

    async def adv_sees(def_id):
        c1, dl = await api(U_ADV, "GET", f"{V1}/definitions?target_type=entity")
        c2, lay = await api(U_ADV, "GET", f"{V1}/layouts/{tab}")
        items = {i.get("definition_id"): i for s in (lay or {}).get("sections") or [] for i in s.get("items") or []}
        return def_id in ids_of(dl), items.get(def_id)

    async def status_of(def_id):
        _, b = await api(U_OA, "GET", f"{V1}/admin/definitions?target_type=entity")
        r = admin_row(b, def_id)
        return (r or {}).get("status"), r

    st, row = await status_of(rt)
    in_defs, item = await adv_sees(rt)
    check(bool(st == "active" and row["placements"] and row["placements"][0]["tab_id"] == tab),
          "admin list: active, placed on Planning › Main", f"{st} {dumps(row)}")
    c, tl = await api(U_ADV, "GET", f"{V1}/tabs?applies_to=entity")
    check(bool(c == 200 and tab in ids_of(tl) and in_defs and item and item.get("label") == NAME + "Round trip"),
          "record page (advisor): the tab, the field in GET /udf/definitions and in the tab's layout", f"{dumps(item)}")
    c, e = await api(U_OA, "PATCH", f"{V1}/definitions/{rt}", {"label": NAME + "Round trip renamed", "help_text": "Why"})
    check(bool(c == 200 and (e.get("definition") or {}).get("label") == NAME + "Round trip renamed"), "edit (PATCH)",
          f"{c} {dumps(e)}")
    _, item = await adv_sees(rt)
    check(bool(item and item.get("label") == NAME + "Round trip renamed"), "the record page shows the new label")
    c, w = await api(U_OA, "PUT", f"{V1}/values/entity/{eid('p1')}", {"definition_id": rt, "value": "kept"})
    check(c == 200, "a value on p1 (PUT /udf/values)", f"{c} {dumps(w)}")
    st, row = await status_of(rt)
    check(bool(row and row["used_by"] == 1), "admin list: used by 1 record", f"{dumps(row)}")
    c, _ = await api(U_OA, "POST", f"{V1}/definitions/{rt}/deactivate")
    st, _row = await status_of(rt)
    in_defs, item = await adv_sees(rt)
    check(bool(c == 200 and st == "deactivated" and not in_defs), "deactivate: status deactivated; gone from the "
          "record page's definitions", f"{c} {st} {in_defs}")
    if item:
        find("a DEACTIVATED field's layout item is still returned by GET /udf/layouts (get_resolved_layout filters "
             "deleted_at only, not is_active) — engine behaviour, unchanged", f"{dumps(item)[:120]}")
    c, w = await api(U_OA, "PUT", f"{V1}/values/entity/{eid('p1')}", {"definition_id": rt, "value": "no"})
    check(c == 422, "a deactivated field takes no new value (422)", f"{c} {dumps(w)}")
    c, _ = await api(U_OA, "POST", f"{V1}/definitions/{rt}/reactivate")
    st, _row = await status_of(rt)
    in_defs, _ = await adv_sees(rt)
    check(bool(c == 200 and st == "active" and in_defs), "reactivate: active again, back on the record page")
    c, d = await api(U_OA, "DELETE", f"{V1}/definitions/{rt}")
    refs = ((d or {}).get("detail") or {}).get("references") or {}
    check(bool(c == 409 and refs.get("udf_values", 0) >= 1 and "Deactivate it instead" in json.dumps(d)),
          "delete refused while a value exists: 409 with references (the reason the builder shows)", f"{c} {dumps(d)}")
    st, _ = await status_of(rt)
    check(st == "active", "the refused delete changed nothing")
    c, d = await api(U_OA, "DELETE", f"{V1}/definitions/{tmp}")
    st, row = await status_of(tmp)
    in_defs, _ = await adv_sees(tmp)
    check(bool(c == 200 and st == "deleted" and not in_defs), "delete (no values): status deleted (undeletable); gone "
          "from the record page's definitions", f"{c} {st}")
    c, _ = await api(U_OA, "POST", f"{V1}/definitions/{tmp}/undelete")
    st, _ = await status_of(tmp)
    in_defs, _ = await adv_sees(tmp)
    check(bool(c == 200 and st == "active" and in_defs), "undelete: active again, back on the record page", f"{c} {st}")
    c, a = await api(U_OA, "GET", f"{V1}/definitions/{rt}/audit")
    kinds = [r["change_kind"] for r in reversed((a or {}).get("rows") or [])]
    who = {r.get("changed_by") for r in (a or {}).get("rows") or []}
    check(bool(c == 200 and kinds == ["create", "update", "deactivate", "reactivate"] and who == {str(U_OA)}),
          "audit (GET …/audit): create, update, deactivate, reactivate — each by the org admin", f"{kinds} {who}")
    sql = [r["change_kind"] for r in await platform(conn, "SELECT change_kind FROM portfolio.udf_definition_audit WHERE "
                                                          "definition_id = $1 ORDER BY changed_at, id", UUID(rt))]
    check(kinds == sql, "the audit route returns exactly the stored rows (= SQL)", f"{kinds} vs {sql}")
    e = next((r for r in (a or {}).get("rows") or [] if r["change_kind"] == "update"), {})
    check(bool("label" in (e.get("changed_fields") or []) and "help_text" in (e.get("changed_fields") or [])),
          "the edit's audit row names the changed fields (label, help_text)", f"{dumps(e)[:200]}")
    c, a2 = await api(U_OA, "GET", f"{V1}/definitions/{tmp}/audit")
    k2 = [r["change_kind"] for r in reversed((a2 or {}).get("rows") or [])]
    check(k2[-2:] == ["soft_delete", "reactivate"], "delete / undelete audited (undelete is stored as 'reactivate')",
          f"{k2}")


# ── Permissions + preview ────────────────────────────────────────────────────────────────────────────────────
async def direct_resolution(uid, def_ids, tab_ids, placed):
    from services.database import get_pool, reset_rls_context, set_rls_context
    from services.portfolio_udf_field_permissions import resolve_field_access_bulk
    from services.portfolio_udf_tabs import resolve_tab_visibility

    tokens = set_rls_context(ORG_A, False)
    try:
        pool = await get_pool()
        async with pool.acquire() as c:
            tabs = {t: await resolve_tab_visibility(c, tab_id=t, org_id=str(ORG_A), user_id=str(uid)) for t in tab_ids}
            no_tab = await resolve_field_access_bulk(c, definition_ids=def_ids, tab_id=None, org_id=str(ORG_A),
                                                     user_id=str(uid))
            on_tab = {t: await resolve_field_access_bulk(c, definition_ids=ids, tab_id=t, org_id=str(ORG_A),
                                                         user_id=str(uid)) for t, ids in placed.items()}
    finally:
        reset_rls_context(tokens)
    return tabs, no_tab, on_tab


async def y_permissions(conn):
    section("[Y] Permissions — grants by profile / permission set; preview = the existing resolution = each persona's view")
    c, b, f_read = await mkdef(U_OA, "uu1_read", "Read for advisors")
    c2, b2, f_hid = await mkdef(U_OA, "uu1_hidden", "Hidden for ops")
    c3, t2b = await api(U_OA, "POST", f"{V1}/tabs", {"applies_to": "entity", "label": NAME + "Ops hidden", "api_name": "uu1_ops"})
    t2 = str((t2b.get("tab") or {}).get("id"))
    t1, sid = S["t1"], S["t1_sec"]
    for d in (f_read, f_hid):
        await api(U_OA, "POST", f"{V1}/layouts/{t1}/sections/{sid}/items", {"definition_id": d, "display_order": 5})
    c4, s2 = await api(U_OA, "POST", f"{V1}/layouts/{t2}/sections", {"title": "Ops"})
    s2id = str((s2.get("section") or {}).get("id"))
    await api(U_OA, "POST", f"{V1}/layouts/{t2}/sections/{s2id}/items", {"definition_id": S["seed_def"]})
    grants = [("fields", f_read, {"profile_id": str(PROFILE_ADV), "access": "read"}),
              ("fields", f_read, {"permission_set_id": str(PSET_OPS), "access": "edit"}),
              ("fields", f_hid, {"permission_set_id": str(PSET_OPS), "access": "hidden"}),
              ("tabs", t2, {"permission_set_id": str(PSET_OPS), "is_visible": False})]
    for kind, sid_, body in grants:
        c, g = await api(U_OA, "PUT", f"{V1}/{kind}/{sid_}/permissions", body)
        check(c == 200, f"grant {kind[:-1]} {list(body.items())}", f"{c} {dumps(g)}")
    c, fg = await api(U_OA, "GET", f"{V1}/fields/{f_read}/permissions")
    got = {(r.get("profile_id"), r.get("permission_set_id"), r.get("access")) for r in (fg or {}).get("rows") or []}
    check(got == {(str(PROFILE_ADV), None, "read"), (None, str(PSET_OPS), "edit")}, "field grants listed back (GET)", f"{got}")
    c, tg = await api(U_OA, "GET", f"{V1}/tabs/{t2}/permissions")
    got = {(r.get("permission_set_id"), r.get("is_visible")) for r in (tg or {}).get("rows") or []}
    check(got == {(str(PSET_OPS), False)}, "tab grants listed back (GET /udf/tabs/{id}/permissions)", f"{got}")
    expect = {  # by hand: most restrictive wins; no grant = visible / edit
        U_ADV: {"t2": True, f_read: "read", f_hid: "edit"},
        U_S1: {"t2": False, f_read: "edit", f_hid: "hidden"},
        U_DUAL: {"t2": False, f_read: "read", f_hid: "hidden"},
        U_OA: {"t2": True, f_read: "edit", f_hid: "edit"},
        U_MEMBER: {"t2": True, f_read: "edit", f_hid: "edit"},
    }
    for uid, want in expect.items():
        name = USERS[uid][2]
        c, pv = await api(U_OA, "GET", f"{V1}/preview?user_id={uid}&target_type=entity")
        if not check(c == 200, f"preview as {name}: 200", f"{c} {dumps(pv)}"):
            continue
        ptabs = {t["tab_id"]: t["visible"] for t in pv.get("tabs") or []}
        pfields = {f["definition_id"]: f for f in pv.get("fields") or []}
        def_ids = list(pfields)
        placed: dict = {}
        for f in pfields.values():
            for t in f["on_tabs"]:
                placed.setdefault(t["tab_id"], []).append(f["definition_id"])
        dtabs, dno, don = await direct_resolution(uid, def_ids, list(ptabs), placed)
        check(ptabs == dtabs, f"preview as {name}: tabs = resolve_tab_visibility", f"{ptabs} vs {dtabs}")
        check({d: f["access"] for d, f in pfields.items()} == dno,
              f"preview as {name}: fields = resolve_field_access_bulk (no tab)")
        pon = {t: {f["definition_id"]: x["access"] for f in pfields.values() for x in f["on_tabs"] if x["tab_id"] == t}
               for t in placed}
        check(pon == don, f"preview as {name}: per-tab access = resolve_field_access_bulk(tab_id)", f"{pon} vs {don}")
        hand = {"t2": ptabs.get(t2), f_read: (pfields.get(f_read) or {}).get("access"),
                f_hid: (pfields.get(f_hid) or {}).get("access")}
        check(hand == want, f"preview as {name}: = the hand-written expectation {want}", f"{hand}")
        if uid in (U_ADV, U_S1, U_DUAL):
            c, dl = await api(uid, "GET", f"{V1}/definitions?target_type=entity")
            own = ids_of(dl)
            hidden = {d for d, f in pfields.items() if f["access"] == "hidden"}
            check(bool(c == 200 and not (own & hidden) and (set(pfields) - hidden) <= own),
                  f"{name}'s own GET /udf/definitions: exactly the preview's non-hidden fields", f"{own & hidden}")
            c, lay = await api(uid, "GET", f"{V1}/layouts/{t2}")
            check(c == (200 if want["t2"] else 403), f"{name}'s own GET /udf/layouts (Ops tab): "
                  f"{'200' if want['t2'] else '403 (hidden)'}", f"{c}")
            c, lay = await api(uid, "GET", f"{V1}/layouts/{t1}")
            items = {i["definition_id"]: i for s in (lay or {}).get("sections") or [] for i in s.get("items") or []}
            for d in (f_read, f_hid):
                acc = next((x["access"] for x in (pfields.get(d) or {}).get("on_tabs") or [] if x["tab_id"] == t1),
                           "hidden")
                shown = "hidden" if d not in items else ("read" if items[d]["is_read_only"] else "edit")
                check(shown == acc, f"{name}'s record page (Planning): {d[:8]} {shown} = preview {acc}")


# ── Layout ───────────────────────────────────────────────────────────────────────────────────────────────────
def section_order(lay):
    return [s["id"] for s in (lay or {}).get("sections") or []]


async def y_layout(conn):
    section("[Y] Layout — sections / items reorder round-trip; refusals; tab order + lifecycle")
    body = {"applies_to": "entity", "label": NAME + "Layout", "api_name": "uu1_layout"}
    c, tb = await api(U_OA, "POST", f"{V1}/tabs", body)
    check(bool(c == 422 and "max_custom_tabs" in json.dumps(tb)), "a 4th active tab is refused by the engine's cap "
          "(crm.udf.max_custom_tabs = 3) — the reason the builder shows", f"{c} {dumps(tb)}")
    c, _ = await api(U_OA, "POST", f"{V1}/tabs/{S['seed_tab']}/deactivate")
    c, tb = await api(U_OA, "POST", f"{V1}/tabs", body)
    tab = str((tb.get("tab") or {}).get("id") or "")
    if not check(bool(c == 201 and tab), "a deactivated tab frees its slot: the Layout tab is created", f"{c} {dumps(tb)}"):
        return
    secs = []
    for title in ("One", "Two", "Three"):
        c, s = await api(U_OA, "POST", f"{V1}/layouts/{tab}/sections", {"title": title, "display_order": len(secs)})
        secs.append(str((s.get("section") or {}).get("id")))
    c, lay = await api(U_OA, "GET", f"{V1}/layouts/{tab}")
    check(section_order(lay) == secs, "three sections in creation order", f"{section_order(lay)}")
    rev = list(reversed(secs))
    c, r = await api(U_OA, "PUT", f"{V1}/layouts/{tab}/sections/order", {"section_ids": rev})
    c2, lay = await api(U_OA, "GET", f"{V1}/layouts/{tab}")
    check(bool(c == 200 and section_order(lay) == rev), "PUT …/sections/order reversed → GET shows the new order",
          f"{c} {dumps(r)}")
    c, r = await api(U_OA, "PUT", f"{V1}/layouts/{tab}/sections/order", {"section_ids": rev[:2]})
    check(c == 422, "an order that is not exactly the layout's sections is refused (422)", f"{c}")
    items = []
    for d in (S["rt"], S["tmp"]):
        c, it = await api(U_OA, "POST", f"{V1}/layouts/{tab}/sections/{secs[0]}/items", {"definition_id": d,
                                                                                         "display_order": len(items)})
        items.append(str((it.get("item") or {}).get("id")))
    for idx, iid in enumerate(reversed(items)):
        await api(U_OA, "PATCH", f"{V1}/layouts/{tab}/items/{iid}", {"display_order": idx})
    c, lay = await api(U_OA, "GET", f"{V1}/layouts/{tab}")
    got = [i["id"] for s in lay.get("sections") or [] if s["id"] == secs[0] for i in s.get("items") or []]
    check(got == list(reversed(items)), "items reordered (PATCH display_order) → GET shows the new order", f"{got}")
    c, d = await api(U_OA, "DELETE", f"{V1}/layouts/{tab}/sections/{secs[0]}")
    check(bool(c == 409 and "udf_layout_items" in json.dumps(d)), "removing a section with items: 409 with the reason",
          f"{c} {dumps(d)}")
    c, d = await api(U_OA, "DELETE", f"{V1}/tabs/{tab}")
    check(c == 409, "deleting a tab with a non-empty layout: 409", f"{c}")
    for iid in items:
        c, _ = await api(U_OA, "DELETE", f"{V1}/layouts/{tab}/items/{iid}")
    c, d = await api(U_OA, "DELETE", f"{V1}/layouts/{tab}/sections/{secs[0]}")
    c2, lay = await api(U_OA, "GET", f"{V1}/layouts/{tab}")
    check(bool(c == 200 and secs[0] not in section_order(lay)), "items removed, then the empty section removed")
    for s in secs[1:]:
        await api(U_OA, "DELETE", f"{V1}/layouts/{tab}/sections/{s}")
    c, b = await api(U_OA, "PATCH", f"{V1}/tabs/{tab}", {"display_order": 0})
    c2, b2 = await api(U_OA, "PATCH", f"{V1}/tabs/{S['t1']}", {"display_order": 1, "label": NAME + "Planning"})
    c3, at = await api(U_OA, "GET", f"{V1}/admin/tabs?applies_to=entity")
    order = {r["id"]: r["display_order"] for r in at.get("rows") or []}
    check(bool(c == 200 and c2 == 200 and order.get(tab) == 0 and order.get(S["t1"]) == 1),
          "tab order: PATCH display_order round-trips (admin tabs list)", f"{c} {c2} {order}")
    for step, want in (("deactivate", "deactivated"), ("reactivate", "active")):
        c, _ = await api(U_OA, "POST", f"{V1}/tabs/{tab}/{step}")
        c2, at = await api(U_OA, "GET", f"{V1}/admin/tabs?applies_to=entity")
        st = next((r["status"] for r in at.get("rows") or [] if r["id"] == tab), None)
        check(bool(c == 200 and st == want), f"tab {step} → {want}", f"{c} {st}")
    c, _ = await api(U_OA, "DELETE", f"{V1}/tabs/{tab}")
    c2, at = await api(U_OA, "GET", f"{V1}/admin/tabs?applies_to=entity")
    st = next((r["status"] for r in at.get("rows") or [] if r["id"] == tab), None)
    c3, vis = await api(U_ADV, "GET", f"{V1}/tabs?applies_to=entity")
    check(bool(c == 200 and st == "deleted" and tab not in ids_of(vis)), "empty tab deleted: admin list 'deleted', gone "
          "from the record page", f"{c} {st}")
    c, _ = await api(U_OA, "POST", f"{V1}/tabs/{tab}/undelete")
    c2, at = await api(U_OA, "GET", f"{V1}/admin/tabs?applies_to=entity")
    st = next((r["status"] for r in at.get("rows") or [] if r["id"] == tab), None)
    check(bool(c == 200 and st == "active"), "tab undeleted → active", f"{c} {st}")


# ── Tags ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_tags(conn):
    section("[Y] Tags — assign, merge, vocabulary")
    c, b, ft = await mkdef(U_OA, "uu1_tags", "Interests", data_type="tags", type_params={})
    if not check(c == 201, "tags field created", f"{c} {dumps(b)}"):
        return
    for k, codes in (("p1", ["alpha", "beta"]), ("p2", ["beta"])):
        c, r = await api(U_OA, "PUT", f"{V1}/tags/{ft}/{eid(k)}", {"codes": codes})
        check(c == 200, f"tags on {k}: {codes}", f"{c} {dumps(r)}")
    c, v = await api(U_OA, "GET", f"{V1}/tags/{ft}")
    vocab = {r["tag_code"]: r["n"] for r in (v or {}).get("rows") or []}
    check(vocab == {"alpha": 1, "beta": 2}, "vocabulary: alpha 1, beta 2", f"{vocab}")
    c, m = await api(U_ADV, "POST", f"{V1}/tags/{ft}/merge", {"from_code": "beta", "into_code": "alpha"})
    check(bool(c == 403 and "manage_portfolio" in json.dumps(m)), "advisor's merge refused (403 naming manage_portfolio)")
    c, m = await api(U_OA, "POST", f"{V1}/tags/{ft}/merge", {"from_code": "beta", "into_code": "alpha"})
    c2, v = await api(U_OA, "GET", f"{V1}/tags/{ft}")
    vocab = {r["tag_code"]: r["n"] for r in (v or {}).get("rows") or []}
    check(bool(c == 200 and m.get("targets_repointed") == 2 and vocab == {"alpha": 2}),
          "merge beta → alpha: 2 targets repointed; beta gone, alpha on both", f"{c} {dumps(m)} {vocab}")
    S["tags"] = ft


# ── Records + export ─────────────────────────────────────────────────────────────────────────────────────────
async def y_records(conn):
    section("[Y] Records grid + export = the route")
    c, rec = await api(U_OA, "GET", f"{V1}/records/entity?limit=500")
    rows = {str(r.get("id")): r for r in (rec or {}).get("rows") or []}
    p1 = rows.get(str(eid("p1"))) or {}
    cols = {c_["definition_id"] for c_ in (rec or {}).get("columns") or []}
    check(bool(c == 200 and S["rt"] in cols and (p1.get("udf_values") or {}).get(S["rt"]) == "kept"),
          "GET /udf/records/entity: p1 carries its value in the Round trip column", f"{c} {dumps(p1)[:200]}")
    check(bool((p1.get("udf_values") or {}).get(S.get("tags", "")) == ["alpha"]), "…and its tags after the merge",
          f"{dumps(p1.get('udf_values'))}")
    c, text = await api(U_OA, "GET", f"{V1}/records/entity/export", raw=True)
    reader = list(csv.DictReader(io.StringIO(text))) if c == 200 else []
    p1csv = next((r for r in reader if r.get("target_id") == str(eid("p1"))), {})
    check(bool(c == 200 and p1csv.get("uu1_rt") == "kept"), "export CSV: p1's Round trip value = the grid's",
          f"{c} {dumps(p1csv)[:200]}")
    check(len(reader) == int((rec or {}).get("total_count") or -1), "export rows = the grid's total_count",
          f"{len(reader)} vs {(rec or {}).get('total_count')}")
    c, _ = await api(U_MEMBER, "GET", f"{V1}/records/entity")
    check(c == 403, "a member is refused the records grid (403)", f"{c}")


# ── Status ───────────────────────────────────────────────────────────────────────────────────────────────────
async def live_status(conn, keys):
    return {k: await platform(conn, "SELECT status FROM entities WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL",
                              eid(k), fetch="fetchval") for k in keys}


async def y_status(conn):
    section("[Y] Relationship status — 'active' not offered, still accepted; the clean-up script on fixtures only")
    import relationship_status_cleanup as RSC

    c, v = await api(U_OA, "GET", "/api/v1/crm/vocabularies")
    st = {s["value"]: s for s in ((v or {}).get("vocabularies") or {}).get("entity_statuses") or []}
    check(bool(c == 200 and st.get("active", {}).get("selectable") is False and st["active"].get("retired") is True
               and st.get("contact", {}).get("selectable") is True and st.get("client", {}).get("selectable") is True),
          "vocabulary: 'active' selectable false + retired true; 'contact' and 'client' still offered", f"{dumps(st)}")
    labels = {k: st.get(k, {}).get("label") for k in ("client", "prospect", "contact", "partner", "former_client", "deceased")}
    check(labels == {"client": "Client", "prospect": "Prospect", "contact": "Contact", "partner": "Partner",
                     "former_client": "Former client", "deceased": "Deceased"}, "readable labels from config", f"{labels}")
    c, b = await api(U_OA, "POST", "/api/v1/entities", {"entity_type": "llc", "display_name": NAME + "Posted Active",
                                                        "status": "active"})
    posted = str(b.get("id") or "")
    check(bool(c in (200, 201) and b.get("status") == "active"), "validation still ACCEPTS 'active' on create",
          f"{c} {dumps(b)}")
    c, b = await api(U_OA, "PUT", f"/api/v1/entities/{eid('sp')}", {"status": "active"})
    check(bool(c == 200 and b.get("status") == "active"), "validation still ACCEPTS a change to 'active'", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "PUT", f"/api/v1/entities/{eid('sp')}", {"status": "prospect"})
    check(c == 200, "…and back to prospect", f"{c}")
    c, b = await api(U_OA, "POST", "/api/v1/entities", {"entity_type": "llc", "display_name": NAME + "Bogus",
                                                        "status": "bogus_status"})
    check(c == 422, "an unknown status is refused (422)", f"{c}")

    expected = {str(r["id"]) for r in await platform(
        conn, "SELECT id FROM entities WHERE org_id = $1 AND status = 'active' AND valid_to IS NULL AND system_to IS NULL",
        ORG_A)}
    check(expected == {str(eid("sa1")), str(eid("sa2")), posted}, "SQL: org A's live 'active' rows = sa1, sa2 + the "
          "posted one", f"{expected}")
    before = await live_status(conn, ("sa1", "sa2", "sc", "sp", "sb"))
    hist_before = await platform(conn, "SELECT count(*) FROM entities WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS,
                                 fetch="fetchval")
    lines: list[str] = []
    dry = await RSC.run(dry_run=True, org_ids=[ORG_A], out=lines.append)
    check(set(dry["found"]) == expected, "dry run (--org A) lists exactly the 'active' rows (= SQL)", f"{dry}")
    check(bool(all(any(i in ln for ln in lines) for i in expected) and lines[0].startswith("DRY RUN")),
          "dry run prints each entity", f"{lines[:3]}")
    after = await live_status(conn, ("sa1", "sa2", "sc", "sp", "sb"))
    hist_after = await platform(conn, "SELECT count(*) FROM entities WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS,
                                fetch="fetchval")
    check(bool(after == before and hist_after == hist_before and not dry["moved"]), "dry run changed nothing")
    real = await RSC.run(dry_run=False, org_ids=[ORG_A], out=lines.append)
    check(bool(set(real["moved"]) == expected and not real["remaining"]), "real run (--org A): every 'active' row moved; "
          "none remaining", f"{real}")
    now = await live_status(conn, ("sa1", "sa2", "sc", "sp", "sb"))
    check(bool(now["sa1"] == now["sa2"] == "client"), "sa1, sa2 → client", f"{now}")
    check(bool(now["sc"] == "contact" and now["sp"] == "prospect"), "'contact' and 'prospect' untouched", f"{now}")
    check(now["sb"] == "active", "org B (not in --org) untouched", f"{now}")
    for i in expected:
        rows = await platform(conn, "SELECT status, system_to IS NULL AS live FROM entities WHERE id = $1 AND org_id = $2",
                              UUID(i), ORG_A)
        live = [r for r in rows if r["live"]]
        arch = await platform(conn, "SELECT count(*) FROM entities WHERE org_id = $1 AND system_to IS NOT NULL AND "
                                    "status = 'active' AND display_name = (SELECT display_name FROM entities WHERE id = $2 "
                                    "AND system_to IS NULL)", ORG_A, UUID(i), fetch="fetchval")
        aud = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = 'status_cleanup' AND "
                                   "resource_id = $2", ORG_A, UUID(i), fetch="fetchval")
        check(bool(len(live) == 1 and live[0]["status"] == "client" and arch >= 1 and aud == 1),
              f"{i[-4:]}: same id now client, an archived version keeps 'active' (system axis), one audit row",
              f"live {[dict(r) for r in live]} archived {arch} audit {aud}")
    again = await RSC.run(dry_run=False, org_ids=[ORG_A], out=lines.append)
    check(bool(not again["found"] and not again["moved"]), "a re-run finds nothing (idempotent)", f"{again}")
    check(RSC.parse_args(["--dry-run", "--org", str(ORG_A)]).dry_run is True and RSC.parse_args([]).dry_run is False,
          "CLI: --dry-run lists, no flag = the real run")
    real_now = await real_status_counts(conn)
    check(real_now == REAL_STATUS, "the REAL rows' status counts are unchanged (no real entity touched)",
          f"{REAL_STATUS} → {real_now}")


async def y_routes(conn):
    section("[Y] Route matrices CURRENT")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: CURRENT", (out + err)[-200:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "visibility1_routes.py --check: CURRENT", (out + err)[-200:])


def y_node():
    section("[Y] Node render tests — tests/udfui1 + nav3 + nav1")
    code, out, err = run_cmd(["node", "--test", "tests/udfui1/", "tests/nav3/", "tests/nav1/"], WEB_DIR, timeout=600)
    m = re.search(r"^# fail (\d+)", out, re.M)
    p = re.search(r"^# pass (\d+)", out, re.M)
    check(bool(code == 0 and m and m.group(1) == "0" and p and int(p.group(1)) > 0),
          f"node --test tests/udfui1 nav3 nav1: {p.group(1) if p else '?'} pass, 0 fail", (out + err)[-300:] if code else "")


REGRESSIONS = ("verify_nav3.py",)
UDF_VERIFIES = ("verify_udf00.py", "verify_udf01a.py", "verify_udf01b.py", "verify_udf01c.py", "verify_udf02a.py",
                "verify_udf02b.py")


def regression():
    section("[Y] Regressions (fast: nested --self-checks-only)")
    for name in UDF_VERIFIES:
        if not (HERE / name).exists():
            find(f"{name}: does not exist")
            continue
        has = VT.has_self_checks_only(HERE / name)
        find(f"{name}: not nested", "no --self-checks-only (fast mode cannot nest it); its full run is not run here — "
             "NAV3 recorded it stops in its own users-fixture INSERT (RLS), pre-existing" if not has
             else "has --self-checks-only now: add it to REGRESSIONS")
    for name in REGRESSIONS:
        t = time.monotonic()
        nt, out, err, code = VT.run_nested(HERE / name, cwd=REPO, timeout=2700)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(bool(nt.ok), f"{name}: 0 FAIL (its OWN final TOTAL line)",
              f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}; {time.monotonic() - t:.0f}s")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            return await res
        return res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}; today {date.today().isoformat()}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        await run("catalog", y_catalog, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        await run("gate", y_gate, conn)
        await run("round trip", y_round_trip, conn)
        await run("permissions", y_permissions, conn)
        await run("layout", y_layout, conn)
        await run("tags", y_tags, conn)
        await run("records", y_records, conn)
        await run("status", y_status, conn)
        section("[Y] Static — the sprint's own change set")
        await run("static", self_checks, check, find)
        await run("routes", y_routes, conn)
        await run("node", y_node)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count")
        try:
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
        finally:
            await conn.close()

    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        await run("regression", regression)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
