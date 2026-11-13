"""verify_nav2 — NAV2: the selected entity follows you, and Ctrl K finds anything (2026-11-13).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch is stubbed, the assistant's model call is stubbed (it RECORDS the system prompt and the tool
list it was given and answers end_turn), and httpx's network transport refuses.

The real app against the live database, in TWO fixture orgs (99000000-…-000000710001 / 710002), ``is_test = true``
(asserted), as are the fixture users. Org A: Super Admin, org admin, support staff S1 (assigned family F1 = p1),
an advisor (internal, no assignment), a member (delegate grant on p2); entities p1, p2, p3 (unassigned); staff
visibility ENFORCED in org A (through the real PUT). Org B: an org admin and entity pb. Positions POS1 (owner p1) and
POS2 (owner p2). Real orgs are only read. Teardown at start and end by fixture id (_verify_cleanup).

What it proves
  * ENTITY SEARCH (the chip's and Ctrl K's source) returns only what each persona may see — member: own family;
    support staff: their families; advisor (no assignment): none; org admin / Super Admin: the org; other org: its own —
    and for every persona × fixture entity, "in the search" == "GET /entities/{id} answers 200" (one gate).
  * NO LEAK — a forbidden id and a missing id give byte-identical answers on the chip's lookup, the positions owner
    filter, /portfolio/allocations and the allocation lens; the Next lookup route collapses every failure to one 404.
  * FILTERING SCREENS — positions?owner_entity_id returns exactly the unfiltered rows whose owner is that entity
    (non-empty, a strict subset, POS2 excluded) and the same set as SQL on the same predicate; ANDed with the
    caller's scope (support staff filtering on an invisible entity gets zero rows); transactions / allocation lens
    answer for a visible entity.
  * ASSISTANT — "Ask about" context: a visible entity reaches the model's system prompt as one context line; a
    forbidden, missing or malformed id yields the SAME prompt as no context at all; the tool list given to the model
    is identical with and without context (the server-side read for that person is unchanged).
  * STATIC (--self-checks-only) — no FastAPI route added / changed / removed; no page added or removed; the menu
    module byte-identical to nav1 (no gate touched); the one new Next route; the screen table covers every book
    screen and NAV2_NOTES lists each; the regression set lists this script; PROJECT_STATUS has the NAV2 line.
  * (full run) node tests tests/nav2 + tests/nav1; authz1_routes.py --check and visibility1_routes.py --check CURRENT;
    fast-mode regressions: verify_nav1, verify_visibility1, verify_authz1 (each --self-checks-only, nested).

--self-checks-only: git / static checks only (stdlib + git; no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"

import asyncio
import inspect
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

SPRINT_KEY = "nav2"
BEFORE_REF = "80a4d18"   # nav2-work base: "merge nav1"
NEW_NEXT_ROUTES = {"apps/web/app/api/entities/[id]/route.js"}
MENU = "apps/web/lib/menuVisibility.mjs"


# ═══ Self-checks (stdlib + git) ═══════════════════════════════════════════════════════════════════════════════
def _git(*args) -> tuple[int, str]:
    code, out, _err = VS.git(*args)
    return code, out


def self_checks(check, find):
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    find("nav2's own end point", how)
    ok, why = VS.own_base_ok(BEFORE_REF, SPRINT_KEY)
    check(bool(ok), f"BEFORE_REF {BEFORE_REF} lies on nav2's own line", why)

    # no FastAPI route added / changed / removed
    code, diff = _git("diff", f"{BEFORE_REF}..{end}", "--unified=0", "--", "apps/api/routers", "apps/api/main.py")
    route_lines = [ln for ln in diff.splitlines() if re.match(r"^[+-]\s*@(router|app)\.(get|post|put|patch|delete)", ln)]
    check(bool(code == 0 and not route_lines), "no FastAPI route decorator added, changed or removed", f"{route_lines[:4]}")
    code, names = _git("diff", "--name-only", f"{BEFORE_REF}..{end}", "--", "apps/api/routers")
    check(bool(code == 0 and names.split() == ["apps/api/routers/assistant.py"]),
          "the only router touched is assistant.py (the context note)", names.strip())

    # pages: none added / removed / renamed; Next API routes: only the chip's lookup added
    code, st = _git("diff", "--name-status", f"{BEFORE_REF}..{end}", "--", "apps/web/app")
    rows = [ln.split("\t") for ln in st.splitlines() if ln.strip()]
    pages_ad = [r for r in rows if r[0][0] in "ADR" and r[-1].endswith("page.js")]
    check(bool(code == 0 and not pages_ad), "no page added, removed or renamed (no route URL changed)", f"{pages_ad}")
    added_api = {r[-1] for r in rows if r[0] == "A" and "/app/api/" in r[-1]}
    check(added_api == NEW_NEXT_ROUTES, "the only new Next API route is the chip's lookup (app/api/entities/[id])",
          f"{sorted(added_api)}")
    removed_api = [r for r in rows if r[0][0] in "DR" and "/app/api/" in r[-1]]
    check(not removed_api, "no Next API route removed or renamed", f"{removed_api}")

    # the menu (every gate) byte-identical to nav1's end
    code, d = _git("diff", "--stat", f"{BEFORE_REF}..{end}", "--", MENU)
    check(bool(code == 0 and not d.strip()), "lib/menuVisibility.mjs byte-identical to its pre-NAV2 state (no gate touched)",
          d.strip())

    def text(path):
        return VS.own_text(SPRINT_KEY, path) or ""

    route = text("apps/web/app/api/entities/[id]/route.js")
    check(bool("forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}`)" in route
               and "status: 404" in route and "reduceLookup(res.ok, data)" in route),
          "the lookup route forwards to the EXISTING GET /entities/{id} and collapses every failure to one 404")
    screens = text("apps/web/lib/entityScreens.mjs")
    hrefs = re.findall(r'^\s*"(/[^"]+)":\s*\{', screens, re.M)
    want = {"/portfolio", "/portfolio/positions", "/portfolio/transactions", "/portfolio/securities",
            "/portfolio/commitments", "/portfolio/households", "/portfolio/allocation", "/portfolio/ownership-tree",
            "/portfolio/approvals", "/portfolio/models", "/portfolio/replication/catalog", "/portfolio/replication",
            "/portfolio/replication/tilts", "/portfolio/replication/backtest", "/portfolio/accounts",
            "/portfolio/tax", "/portfolio/replication/section351", "/portfolio/blocks", "/portfolio/instructions",
            "/portfolio/replication/notes", "/portfolio/replication/notes/new-issues", "/treasury", "/documents",
            "/documents/semantic"}   # nav1's Cross Book / Model Portfolios / Structured Notes / Documents tabs + Treasury
    check(set(hrefs) == want, "the screen table decides every book screen (by hand list from nav1's groups)",
          f"missing {sorted(want - set(hrefs))} extra {sorted(set(hrefs) - want)}")
    notes = text("docs/NAV2_NOTES.md")
    missing_notes = [h for h in want if f"`{h}`" not in notes]
    check(not missing_notes, "docs/NAV2_NOTES.md's per-screen table lists every screen", f"{missing_notes}")
    check("no gap" in notes.lower() or "no fix needed" in notes.lower(),
          "NAV2_NOTES records the CRITICAL entity-search visibility check and its result")
    reg = (REPO / "scripts" / "regression_set.txt").read_text(encoding="utf-8")
    check(bool(re.search(r"^verify_nav2\.py$", reg, re.M)), "scripts/regression_set.txt lists verify_nav2.py")
    status = text("docs/PROJECT_STATUS.md")
    check(bool(re.search(r"^NAV2 \(2026-11-13", status, re.M)), "docs/PROJECT_STATUS.md carries the NAV2 line")
    asst = text("apps/api/routers/assistant.py")
    check(bool("await require_entity_access(request, pool, entity_id)" in asst
               and "context_note = await entity_context_note(request, pool, org_id, body.context_ref)" in asst),
          "the assistant's context note is gated by the one entity gate (require_entity_access)")
    code, d = _git("diff", "--unified=0", f"{BEFORE_REF}..{end}", "--", "apps/api/routers/assistant.py")
    removed = [ln[1:].strip() for ln in d.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    check(removed == ["api_messages, tool_specs, action_map, pool, user_id, org_id, caller_is_staff"],
          "assistant.py: the only line removed is the _run_loop call's argument line (tools / RBAC untouched)",
          f"{removed}")
    shell = text("apps/web/components/AppShell.jsx")
    check("<EntityScreen>{children}</EntityScreen>" in shell and "<EntityContextSync />" in shell,
          "AppShell carries the one context (EntityScreen around every page, the remembered-selection sync)")


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

import asyncpg  # noqa: E402,F401
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import close_pool  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "nav2verify"
NAME = "N2V "              # reserved fixture name prefix (asserted to match no real row)


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000071{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
ORGADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                            "manage_portfolio"})
SUPPORT_WANTED = frozenset({"manage_members", "view_members", "view_portfolio", "manage_portfolio"})
ADVISOR_WANTED = frozenset({"view_members", "view_portfolio"})
U_SUPER, U_OA, U_S1, U_ADV, U_MEMBER, U_OB = (_id(n) for n in range(10, 16))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_S1: (ORG_A, "member", "support", "support_staff", SUPPORT_WANTED),
    U_ADV: (ORG_A, "member", "advisor", "advisor", ADVISOR_WANTED),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OB: (ORG_B, "org_admin", "obadmin", "org_admin", ORGADMIN_PERMS),
}
E = {
    "p1": (_id(30), ORG_A, "individual", "Aurelio Person"),
    "p2": (_id(31), ORG_A, "individual", "Bramble Person"),
    "p3": (_id(32), ORG_A, "individual", "Quillon Person"),
    "pb": (_id(33), ORG_B, "individual", "Orgbee Person"),
}
H1, H2, H3, HB = _id(60), _id(61), _id(62), _id(63)
HHS = [H1, H2, H3, HB]
ASSET1, ASSET2, POS1, POS2 = _id(80), _id(81), _id(82), _id(83)
AS_OF = date(2026, 11, 4)
MISSING = _id(9999)   # never seeded
#: who must see which fixture entity (by hand, from the rules — never from the code under test)
EXPECT = {
    U_SUPER: {"p1", "p2", "p3"},
    U_OA: {"p1", "p2", "p3"},
    U_S1: {"p1"},          # enforcing org: their assigned family only
    U_ADV: set(),          # enforcing org, no assignment: nothing
    U_MEMBER: {"p2"},      # the member's own family (delegate grant)
    U_OB: {"pb"},          # the other org: its own only
}
COUNTED = ("public.users", "public.organizations", "public.entities", "public.households",
           "public.household_memberships", "public.staff_assignments", "public.org_settings", "public.role_permissions",
           "public.user_roles", "public.delegate_grants", "public.assistant_conversations", "portfolio.assets",
           "portfolio.positions")

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


def who(uid) -> str:
    return USERS[uid][2]


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:600]


async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, VSC.count_sql(t, FIXTURE_ORGS, list(USERS)), fetch="fetchval")) for t in COUNTED}


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


def _plan() -> dict:
    users = list(USERS)
    return {
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        "public.household_memberships": ("household_id = ANY($1::uuid[])", [HHS]),
        "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                    [FIXTURE_ORGS]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
    }


async def teardown(_conn=None) -> list[str]:
    op = await operator_conn()
    try:
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               label="verify_nav2")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [HHS])])
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


# ═══ Catalog + seed ═════════════════════════════════════════════════════════════════════════════════════════════
VALUES: dict[str, str] = {}


def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


async def _pick(conn, table: str, col: str, prefer: str) -> str:
    rows = await platform(conn, "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = "
                                "$1::regclass AND contype::text = 'c'", table)
    defs = [r["d"] for r in rows if re.search(rf"\b{col}\b", r["d"])]
    vals = _allowed(defs[0]) if defs else []
    return prefer if (not vals or prefer in vals) else vals[0]


async def y_catalog(conn):
    section("[Y] Constraint values read from the catalog; reserved fixture prefixes match no real row")
    VALUES["asset_class"] = await _pick(conn, "portfolio.assets", "asset_class", "financial")
    VALUES["authority"] = await _pick(conn, "portfolio.positions", "authority", "manual")
    VALUES["source_system"] = await _pick(conn, "portfolio.positions", "source_system", "manual")
    VALUES["delegate_scope"] = await _pick(conn, "public.delegate_grants", "scope", "view_only")
    find("seeded with catalog-legal values", f"{VALUES}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($3::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND NOT (id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $5 AND NOT "
                                "(id = ANY($3::uuid[])))", NAME + "%", f"%{TAG}%", FIXTURE_ORGS, list(USERS),
                          f"{TAG}%", fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (names, subs, slugs) match no real row", f"{real}")
    ids = await platform(conn, "SELECT count(*) FROM entities WHERE id::text LIKE '99000000-0000-0000-0000-00000071%' "
                               "AND NOT (org_id = ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval")
    check(ids == 0, "the reserved fixture id range matches no real row", f"{ids}")


async def seed(conn) -> None:
    roles: dict[tuple, UUID] = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"NAV2 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        role_perms: dict[str, set] = {}
        for _uid, (_org, _acct, _nm, rname, wanted) in USERS.items():
            role_perms.setdefault(rname, set()).update(wanted)
        dropped = {r: sorted(p - set(perm_ids)) for r, p in role_perms.items() if p - set(perm_ids)}
        if dropped:
            find("permissions not in the catalog (not granted)", f"{dropped}")
        for org in FIXTURE_ORGS:
            for rname, perms in role_perms.items():
                rid = await rbac.ensure_role(conn, org, rname, f"NAV2 fixture {rname}")
                for p in perms & set(perm_ids):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, f"{TAG}-{nm}@test.local", f"{NAME}{nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for _k, (i, org, etype, name) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) "
                               "VALUES ($1, $2, $3, 'active', $4::entity_type)", i, org, NAME + name, etype)
        for hh, org, nm, k in ((H1, ORG_A, "Aurelio Household", "p1"), (H2, ORG_A, "Bramble Household", "p2"),
                               (H3, ORG_A, "Quillon Household", "p3"), (HB, ORG_B, "Orgbee Household", "pb")):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org, NAME + nm)
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh,
                               eid(k))
        for aid, nm in ((ASSET1, "Aurelio Holding"), (ASSET2, "Bramble Holding")):
            await conn.execute(
                "INSERT INTO portfolio.assets (id, org_id, name, asset_class, asset_type, ownership_basis, "
                "valuation_method) VALUES ($1, $2, $3, $4, 'equity', 'value', 'market_price')", aid, ORG_A,
                NAME + nm, VALUES["asset_class"])
        for pid, k, aid in ((POS1, "p1", ASSET1), (POS2, "p2", ASSET2)):
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "market_value, authority, source_system) VALUES ($1, $2, $3, $4, $5, 'value', 125000, $6, $7)",
                pid, ORG_A, eid(k), aid, AS_OF, VALUES["authority"], VALUES["source_system"])
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from) VALUES ($1, $2, $3, $4, now() - interval '1 day')", ORG_A, eid("p2"),
                           U_MEMBER, VALUES["delegate_scope"])
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) VALUES "
                           "($1, $2, $3, 'Lead adviser')", ORG_A, eid("p1"), U_S1)
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
MODEL_CALLS: list = []


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(uid, method, path, body=None, timeout=180.0):
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
    try:
        parsed = json.loads(res.text) if res.text else {}
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def install_stubs():
    """Each stub matches the CURRENT signature of what it replaces (read at install time)."""
    import main
    from routers import assistant as A

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    real = inspect.signature(A.call_claude_with_tools).parameters
    for name in ("system", "messages", "tools"):
        if name not in real:
            raise RuntimeError(f"call_claude_with_tools has no {name!r} parameter: {list(real)}")

    async def recorded_model(*args, **kwargs):
        MODEL_CALLS.append({"system": kwargs.get("system"), "tools": kwargs.get("tools")})
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "noted"}]}

    A.call_claude_with_tools = recorded_model

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_nav2: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


async def y_switch(conn):
    section("[Y] Staff visibility enforced in org A through the real switch (org admin, confirm)")
    c, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement", {"enforced": True, "confirm": True})
    check(bool(c == 200 and b.get("enforced") is True), "org A's staff visibility is ON (fixture org only)",
          f"{c} {dumps(b)}")


def ids_in(body, key="id") -> set:
    rows = body if isinstance(body, list) else (body.get("items") or body.get("rows") or body.get("positions")
                                                 or body.get("transactions") or [])
    return {str(r.get(key)) for r in rows if isinstance(r, dict) and r.get(key) is not None}


async def search_seen(uid) -> set:
    c, b = await api(uid, "GET", f"/api/v1/entities/search?q={NAME.strip()}&page_size=100&include_operational=true")
    if c != 200:
        raise RuntimeError(f"entity search for {who(uid)}: {c} {dumps(b)}")
    got = ids_in(b)
    return {k for k, v in E.items() if str(v[0]) in got}


async def y_search(conn):
    section("[Y] Entity search — each persona sees exactly what it may (chip + Ctrl K source); one gate with GET")
    for uid, want in EXPECT.items():
        seen = await search_seen(uid)
        check(seen == want, f"entity search · {who(uid)}: sees exactly {sorted(want) or 'nothing'}",
              f"saw {sorted(seen)}")
        mismatched = []
        for k in E:
            c, _b = await api(uid, "GET", f"/api/v1/entities/{eid(k)}")
            if (c == 200) != (k in seen):
                mismatched.append((k, c))
        check(not mismatched, f"entity search · {who(uid)}: in the search ⇔ GET /entities/{{id}} answers 200",
              f"{mismatched}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/entities/search?q=Aurelio&page_size=100&include_operational=true")
    check(bool(c == 200 and str(eid("p1")) not in ids_in(b)),
          "a member searching another family's NAME gets nothing of it", f"{c} {dumps(b)}")


#: per persona, a real fixture entity it may NOT see (by hand, from EXPECT)
FORBIDDEN = {U_MEMBER: "p1", U_OB: "p1", U_ADV: "p1", U_S1: "p2"}


async def same_answer(uid, path_for, label):
    assert FORBIDDEN[uid] not in EXPECT[uid]
    c1, b1 = await api(uid, "GET", path_for(eid(FORBIDDEN[uid])))
    c2, b2 = await api(uid, "GET", path_for(MISSING))
    check(bool(c1 == c2 and b1 == b2), f"no leak · {who(uid)} · {label}: forbidden id ≡ missing id",
          f"{c1} {dumps(b1)} | {c2} {dumps(b2)}")
    return c1, b1


async def y_no_leak(conn):
    section("[Y] No leak — a forbidden id answers exactly like a missing one (chip lookup + every filtering API)")
    for uid in (U_MEMBER, U_OB, U_ADV, U_S1):
        await same_answer(uid, lambda i: f"/api/v1/entities/{i}", "GET /entities/{id} (the chip's lookup)")
        c, b = await same_answer(uid, lambda i: f"/api/v1/portfolio/positions?owner_entity_id={i}",
                                 "positions?owner_entity_id")
        check(bool(c != 200 or not ids_in(b)), f"no leak · {who(uid)} · positions: a forbidden owner gives no row",
              f"{c} {sorted(ids_in(b))}")
        await same_answer(uid, lambda i: f"/api/v1/allocation-lens?selector_type=entity&entity_id={i}",
                          "allocation-lens?entity_id")
        await same_answer(uid, lambda i: f"/api/v1/portfolio/allocations?entity_id={i}",
                          "/portfolio/allocations?entity_id")


async def y_filters(conn):
    section("[Y] Filtering screens — only that entity's rows, equal to the scoped API and to SQL")
    c, allb = await api(U_OA, "GET", "/api/v1/portfolio/positions?limit=500")
    all_ids = ids_in(allb)
    check(bool(c == 200 and {str(POS1), str(POS2)} <= all_ids), "positions unfiltered (org admin) holds POS1 and POS2",
          f"{c} {sorted(all_ids)[:6]}")
    c, fb = await api(U_OA, "GET", f"/api/v1/portfolio/positions?limit=500&owner_entity_id={eid('p1')}")
    f_ids = ids_in(fb)
    owners = {str(r.get("owner_entity_id")) for r in (fb.get("positions") or [])}
    check(bool(c == 200 and f_ids and f_ids < all_ids), "positions?owner_entity_id=p1: non-empty and a STRICT subset",
          f"{c} {len(f_ids)}/{len(all_ids)}")
    check(bool(owners == {str(eid("p1"))} and str(POS1) in f_ids and str(POS2) not in f_ids),
          "positions?owner_entity_id=p1: every row is p1's; POS1 in, POS2 out", f"{owners}")
    same_pred = {str(r.get("id")) for r in (allb.get("positions") or []) if str(r.get("owner_entity_id")) == str(eid("p1"))}
    check(f_ids == same_pred, "positions filtered ≡ the unfiltered scoped API's rows with owner p1", f"{f_ids ^ same_pred}")
    sql = {str(r["id"]) for r in await platform(conn, "SELECT id FROM portfolio.positions WHERE org_id = $1 AND "
                                                      "owner_entity_id = $2", ORG_A, eid("p1"))}
    check(f_ids <= sql and str(POS1) in sql, "positions filtered ⊆ SQL on the same predicate (current rows only)",
          f"{f_ids - sql}")
    c, sb = await api(U_S1, "GET", f"/api/v1/portfolio/positions?limit=500&owner_entity_id={eid('p2')}")
    check(bool(c == 200 and not ids_in(sb)), "support staff filtering on a family outside their scope: zero rows (the "
          "filter is ANDed with the scope, never widens)", f"{c} {sorted(ids_in(sb))}")
    c, sb = await api(U_S1, "GET", f"/api/v1/portfolio/positions?limit=500&owner_entity_id={eid('p1')}")
    check(bool(c == 200 and ids_in(sb) == {str(POS1)}), "support staff filtering on their own family: exactly POS1",
          f"{c} {sorted(ids_in(sb))}")
    c, mb = await api(U_MEMBER, "GET", f"/api/v1/portfolio/positions?limit=500&owner_entity_id={eid('p2')}")
    check(bool(c == 200 and ids_in(mb) == {str(POS2)}), "member filtering on their own entity: exactly POS2",
          f"{c} {sorted(ids_in(mb))}")
    c, tb = await api(U_OA, "GET", f"/api/v1/portfolio/transactions?owner_entity_id={eid('p1')}")
    rows = tb.get("transactions") or tb.get("rows") or []
    check(c == 200, "transactions?owner_entity_id answers for a visible entity", f"{c} {dumps(tb)}")
    if rows:
        bad = [r for r in rows if str(r.get("owner_entity_id")) != str(eid("p1"))]
        check(not bad, "transactions?owner_entity_id=p1: every row is p1's", f"{bad[:2]}")
    else:
        find("transactions: no fixture transaction seeded (a transaction needs a definition / position chain) — the "
             "owner filter is proven on positions, which share the scope path (_entity_scope)")
    c, lb = await api(U_OA, "GET", f"/api/v1/allocation-lens?selector_type=entity&entity_id={eid('p1')}")
    check(c == 200, "allocation lens: a visible entity answers 200 (org admin, p1)", f"{c} {dumps(lb)}")
    c, lb = await api(U_MEMBER, "GET", f"/api/v1/allocation-lens?selector_type=entity&entity_id={eid('p2')}")
    check(c == 200, "allocation lens: the member's own entity answers 200", f"{c} {dumps(lb)}")


async def ask(uid, ctx):
    n0 = len(MODEL_CALLS)
    c, b = await api(uid, "POST", "/api/v1/assistant/message", {"message": "How is this one doing?", "context_ref": ctx})
    if c != 200 or len(MODEL_CALLS) != n0 + 1:
        raise RuntimeError(f"assistant message for {who(uid)}: {c} {dumps(b)} (model calls {len(MODEL_CALLS) - n0})")
    return MODEL_CALLS[-1]


def tool_names(call) -> list:
    return sorted(t.get("name") for t in (call["tools"] or []))


async def y_assistant(conn):
    section("[Y] Assistant — the entity is context only for someone who may see it; the read path is unchanged")
    base = await ask(U_MEMBER, None)
    own = await ask(U_MEMBER, {"type": "entity", "id": str(eid("p2")), "entity_type": "individual"})
    forbidden = await ask(U_MEMBER, {"type": "entity", "id": str(eid("p1"))})
    missing = await ask(U_MEMBER, {"type": "entity", "id": str(MISSING)})
    malformed = await ask(U_MEMBER, {"type": "entity", "id": "not-a-uuid"})
    check(bool(NAME + "Bramble Person" in own["system"] and str(eid("p2")) in own["system"]),
          "member + own entity: the system prompt names it (one context line)")
    check(bool(forbidden["system"] == missing["system"] == malformed["system"] == base["system"]),
          "member + another family's id ≡ a missing id ≡ a malformed id ≡ no context (the same prompt, byte for byte)")
    check(NAME + "Aurelio" not in forbidden["system"], "the forbidden entity's name never reaches the model")
    check(bool(tool_names(base) == tool_names(own) == tool_names(forbidden)),
          "the tools offered to the model are identical with and without context (server-side read unchanged)",
          f"{tool_names(base)} vs {tool_names(own)}")
    sa = await ask(U_SUPER, {"type": "entity", "id": str(eid("p1"))})
    sa_base = await ask(U_SUPER, None)
    check(NAME + "Aurelio Person" in sa["system"], "Super Admin + p1: the context line names p1")
    check(tool_names(sa) == tool_names(sa_base), "Super Admin: identical tools with and without context")
    ob = await ask(U_OB, {"type": "entity", "id": str(eid("p1"))})
    ob_base = await ask(U_OB, None)
    check(ob["system"] == ob_base["system"], "the other org's admin + org A's entity: no context (same as none)")
    s1 = await ask(U_S1, {"type": "entity", "id": str(eid("p2"))})
    s1_base = await ask(U_S1, None)
    check(s1["system"] == s1_base["system"], "support staff + a family outside their scope: no context")
    s1_own = await ask(U_S1, {"type": "entity", "id": str(eid("p1"))})
    if NAME + "Aurelio" in s1_own["system"]:
        find("support staff + their own assigned family: context line present")
    else:
        find("support staff + their own assigned family: NO context line — require_entity_access gives an internal "
             "non-staff role its unscoped read on GET only, so a POST takes the member path (STAFFROLES1). More "
             "restrictive, never wider; recorded in NAV2_NOTES")


async def y_routes(conn):
    section("[Y] Route matrices CURRENT (no route gained a parameter — nothing regenerated)")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: CURRENT", (out + err)[-200:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "visibility1_routes.py --check: CURRENT", (out + err)[-200:])


def y_node():
    section("[Y] Node render tests — tests/nav2 (NAV2's own) + tests/nav1 + tests/nav (the shell)")
    code, out, err = run_cmd(["node", "--test", "tests/nav2/", "tests/nav1/", "tests/nav/"], WEB_DIR, timeout=600)
    m = re.search(r"^# fail (\d+)", out, re.M)
    p = re.search(r"^# pass (\d+)", out, re.M)
    check(bool(code == 0 and m and m.group(1) == "0" and p and int(p.group(1)) > 0),
          f"node --test tests/nav2 tests/nav1 tests/nav: {p.group(1) if p else '?'} pass, 0 fail",
          (out + err)[-300:] if code else "")


def regression():
    section("[Y] Regressions (VERIFY_REGRESSIONS fast: nested --self-checks-only) — nav1, visibility1, authz1")
    for name in ("verify_nav1.py", "verify_visibility1.py", "verify_authz1.py"):
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
        await run("switch", y_switch, conn)
        await run("search", y_search, conn)
        await run("no leak", y_no_leak, conn)
        await run("filters", y_filters, conn)
        await run("assistant", y_assistant, conn)
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
