"""verify_navregroup.py — NAVREGROUP: master pages with tabs from ONE menu
definition, plus the Document Search visibility fix and the VDR list gate.

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_navregroup.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on
any failure. Hydrates secrets from Doppler over HTTPS (_db_bootstrap).

WHAT IS PROVEN WHERE
  * Menu rules: the node harness (apps/api/scripts/menuvisibility_harness.mjs)
    imports the REAL lib/menuVisibility.mjs; nothing about a gate is
    re-implemented in Python. The baseline is regenerated from the PRE-sprint
    code at the pinned commit and must equal docs/NAVREGROUP_BASELINE.json.
  * Sidebar / tab bar / group landing: the node tests in apps/web/tests/nav render
    the REAL components from a fed /users/me payload. The group landing route's
    server component is EXECUTED in Node (session + /users/me stubbed): its
    redirect is Next's real NEXT_REDIRECT, its refusal is rendered markup. That
    is the "HTTP check" — no live Next server is started (this script never
    spawns a background process), and it says so in its labels.
  * Document Search and the VDR list: through the REAL FastAPI app (httpx
    ASGITransport, mocked token), on fixtures, as app_service (rolbypassrls =
    false, asserted). The leak is reproduced first with the pre-fix org-wide
    query (document_linkage.search_documents_org_wide, the old code path).

VHK2 (2026-11-03) — checks that froze LIVE state now assert a rule:
  * "every page has a 1a row" is navregroup's claim at its OWN merge (git only, also in --self-checks-only); today
    every page is a nav tab / landing / redirect / sub-page of a nav tab, or states a reason in NON_NAV_REASONS, and
    has a traced server gate.
  * "additions are exactly the listed intended ones" runs on the harness AS NAVREGROUP LEFT IT (harness_at); today
    every href added since the baseline must be admitted by its traced server gate (additions_ok).
  * A page's server gate is traced by navregroup_traced_gates.mjs: navregroup's hand row, else the
    AUTHZ1_ROUTES.json record of every API its page.js calls. The hand table alone said "refused" for every page a
    later sprint added — Super Admin included.
  * The six personas' permission lists were read on 2026-10-20; y_live_roles applies the same rule to every REAL
    role in the default org, read at run time.

FIXTURES — tagged, torn down at the START and at the END:
  * an org, users (auth0_sub = the literal sub the mocked token carries; teardown
    also finds them by sub and email pattern), two fixture roles in that org
    with role_permissions to existing permission names;
  * entities 'NAVREGROUP VERIFY …' (one access_restricted), delegate grants,
    staff assignments, one restricted-access allow-list row;
  * documents whose filename carries a unique token, one document_drop and one
    pending vdr_deal_proposals row.
Every id is a full UUID literal — never a slice of one. Real documents and real
note_gold_values are never touched (count + checksum before/after).

Teardown is FK child-before-parent from pg_constraint (contype::text), then every
touched table is re-read for its exact pre-test count. Regressions
(verify_bulkfetchui2.py, and the menu-visibility harness's pre-existing
assertions) run ONCE each as flat subprocesses with VERIFY_NESTED=1, only after
this script's own teardown left zero fixture rows.
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
import tempfile
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))

# ── VHK1: self-checks (git / route / changed-line), stdlib + git only, each ending at navregroup's OWN merge (a911ac7,
# content-identical to its branch tip e97718f — the old NAVREGROUP_REF), never the working tree. --self-checks-only
# runs just these, BEFORE the DB bootstrap — no Doppler, no database, no network, no npm.
import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)

SPRINT_KEY = "navregroup"
BASELINE_REF = "e581844"   # the commit this sprint started from (pinned: HEAD moves)
GATE_TOKENS = re.compile(r"require_permission|_require_\w+\(|has_permission|can_manage_org_settings|is_super_admin"
                         r"|require_staff|is_org_admin")
# VHK1 removed LATER_SPRINT_PAGE_CHANGES (usermgmt1 / bugfix1 page edits) and LATER_SPRINT_NEW_PAGES (bugfix1's
# comingSoon pages): both existed only because the page checks read the working tree. At navregroup's own merge
# neither later sprint's change exists, so neither list is needed.


def self_urls_unchanged(check, find):
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    stored = json.loads(VS.own_text(SPRINT_KEY, "docs/NAVREGROUP_BASELINE.json") or "{}")
    sample = sorted(set().union(*(stored.get("personas", {}).get(p, {}).get("reachable", [])
                                  for p in ("plain_member", "org_admin_manage_org_settings", "super_admin"))))
    changed_pages, existence_diff = [], []
    allowed_heading_edit = re.compile(r'^[+-](import \{ labelFor \} from "@/lib/menuVisibility";|'
                                      r'\s*<h1 className="text-3xl font-semibold text-navy">.*</h1>)$')
    for href in sample:
        if href == "/admin":
            continue     # rebuilt on purpose as the grouped overview — checked separately (full run)
        rel = f"apps/web/app{href}/page.js"
        before = VS.git_show(BASELINE_REF, rel)
        after = VS.git_show(end, rel)
        if (before is not None) != (after is not None):
            existence_diff.append(href)
            continue
        if before is None:
            continue
        code, diff, _ = VS.own_diff(BASELINE_REF, SPRINT_KEY, rel)
        lines = [ln for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
        if code != 0 or any(not allowed_heading_edit.match(ln) for ln in lines):
            changed_pages.append(href)
    check(len(sample) > 0 and not existence_diff,
          "every sampled URL resolves to a page at navregroup's end iff it did at the baseline (3 dead links stay 404)",
          f"{BASELINE_REF} → {how}; {len(sample)} URLs; differ {existence_diff}")
    check(not changed_pages, "no sampled page changed (navregroup's own diff) beyond its heading reading the "
          "definition's label", f"{BASELINE_REF} → {how}; {changed_pages}")
    drift = []
    _, listing, _ = VS.git("ls-tree", "--name-only", end, "apps/api/routers/")
    for rel in sorted(x for x in listing.splitlines() if x.endswith(".py")):
        before = VS.git_show(BASELINE_REF, rel)
        if before is None:
            continue
        own = VS.git_show(end, rel) or ""
        b, a = len(GATE_TOKENS.findall(before)), len(GATE_TOKENS.findall(own))
        expected = 1 if rel.endswith("/vdr.py") else 0
        if a - b != expected:
            drift.append(f"{rel.rsplit('/', 1)[-1]}: {b}->{a}")
    check(bool(listing.strip()) and not drift, "router gate calls unchanged (navregroup's own diff) except the one "
          "added on GET /vdr-proposals", f"{BASELINE_REF} → {how}; {drift}")


def self_pages_have_1a_rows(check, find):
    # VHK2: this check used to read TODAY's page tree against navregroup's 1a table, so every page a later sprint
    # added (REP1 → REP10, MARKETDATA1) failed it. Two rules replace it: navregroup's own claim at its own merge
    # (every page that existed then has a 1a row), and today's rule over every current page (today_pages below).
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    end_text = VS.own_text(SPRINT_KEY, "docs/NAVREGROUP_DISCOVERY.md") or ""
    _, listing, _ = VS.git("ls-tree", "-r", "--name-only", end, "apps/web/app/admin", "apps/web/app/portfolio")
    routes = []
    for rel in sorted(x for x in listing.splitlines() if re.search(r"/page\.jsx?$", x)):
        src = VS.git_show(end, rel) or ""
        route = "/" + rel[len("apps/web/app/"):].rsplit("/", 1)[0]
        if "GroupLanding" not in src:   # navregroup's own landing routes are documented in Task 2, not 1a
            routes.append(route)
    missing = [r for r in routes if f"| `{r}` |" not in end_text]
    check(len(routes) > 0 and not missing,
          "every page under app/admin/** and app/portfolio/** at navregroup's own end has a 1a row",
          f"{how}; {len(routes)} routes; missing {missing}")


def self_checks(check, find):
    self_urls_unchanged(check, find)
    self_pages_have_1a_rows(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"
DISCOVERY_DOC = REPO / "docs" / "NAVREGROUP_DISCOVERY.md"
BASELINE_JSON = REPO / "docs" / "NAVREGROUP_BASELINE.json"
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
SIDEBAR = WEB_DIR / "components" / "Sidebar.jsx"
MENU = WEB_DIR / "lib" / "menuVisibility.mjs"
HARNESS = HERE.parent / "menuvisibility_harness.mjs"
BASELINE_GEN = HERE.parent / "navregroup_baseline.mjs"

TAG = "navregroup-verify"
TOKEN = "nvrgrpverifytoken"          # in every fixture filename; asserted unused first
ENTITY_PREFIX = "NAVREGROUP VERIFY "
ORG = UUID("99000000-0000-0000-0000-0000000a7001")
U_MEMBER = UUID("99000000-0000-0000-0000-0000000a7011")
U_MEMBER2 = UUID("99000000-0000-0000-0000-0000000a7012")
U_STAFF = UUID("99000000-0000-0000-0000-0000000a7013")
U_SUPER = UUID("99000000-0000-0000-0000-0000000a7014")
U_DEALS = UUID("99000000-0000-0000-0000-0000000a7015")
FIXTURE_USERS = [U_MEMBER, U_MEMBER2, U_STAFF, U_SUPER, U_DEALS]
FIXTURE_EMAIL_LIKE = "navregroup_%@test.local"
E_MINE = UUID("99000000-0000-0000-0000-0000000a7021")
E_OTHER = UUID("99000000-0000-0000-0000-0000000a7022")
E_RESTR = UUID("99000000-0000-0000-0000-0000000a7023")
FIXTURE_ENTITIES = [E_MINE, E_OTHER, E_RESTR]
D_MINE = UUID("99000000-0000-0000-0000-0000000a7031")
D_OTHER = UUID("99000000-0000-0000-0000-0000000a7032")
D_GENERAL = UUID("99000000-0000-0000-0000-0000000a7033")
D_RESTR = UUID("99000000-0000-0000-0000-0000000a7034")
FIXTURE_DOCS = [D_MINE, D_OTHER, D_GENERAL, D_RESTR]
DROP = UUID("99000000-0000-0000-0000-0000000a7041")
PROPOSAL = UUID("99000000-0000-0000-0000-0000000a7042")
ROLE_MEMBER = UUID("99000000-0000-0000-0000-0000000a7051")
ROLE_DEALS = UUID("99000000-0000-0000-0000-0000000a7052")
HEADERS = {"Authorization": "Bearer verify-token"}

# Token claims per fixture user. Real API tokens carry NO roles/permissions
# claim (routers/entities.py org_id_from_claims); these claims are what makes the
# member-vs-staff path testable at all — see the [FIND] printed in that section.
CLAIMS = {
    U_MEMBER: {"roles": ["member"], "permissions": ["view_deals", "view_portfolio"]},
    U_MEMBER2: {"roles": ["member"], "permissions": ["view_deals", "view_portfolio"]},
    U_STAFF: {"roles": ["investment_staff"], "permissions": ["manage_deals", "view_portfolio"]},
    U_SUPER: {"roles": ["admin"], "permissions": ["manage_deals", "view_portfolio"]},
    U_DEALS: {"roles": ["member"], "permissions": ["manage_deals"]},
}

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} ({type(passed).__name__}), not a bool, for {label!r}")
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


def _sub(uid: UUID) -> str:
    return f"navregroup_{uid.hex}"


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy())
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"TIMEOUT after {timeout}s"
    except OSError as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


# ═══ RLS-scoped DB helpers (every read sets the platform RLS context) ═══════
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def rls_fetch(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(q, *a)


async def rls_row(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(q, *a)


async def rls_val(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(q, *a)


async def rls_exec(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.execute(q, *a)


async def check_defs(conn, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = $1::regclass AND contype::text = 'c'", table)
    return {r["conname"]: r["def"] for r in rows}


def allowed(defs: dict[str, str], column: str) -> set[str]:
    out: set[str] = set()
    for d in defs.values():
        if re.search(rf"\(\(?{column}\b", d) and ("ARRAY[" in d or " IN " in d):
            out |= set(re.findall(r"'([^']+)'::text", d))
    return out


# ═══ Teardown — scoped predicates (the shared _verify_cleanup); counts as the backstop ═══════
PREDICATES = {
    "public.vdr_deal_proposals": ("id = $1 OR org_id = $2", lambda: [PROPOSAL, ORG]),
    "public.document_drops": ("id = $1 OR org_id = $2", lambda: [DROP, ORG]),
    "public.documents": ("id = ANY($1::uuid[]) OR org_id = $2", lambda: [FIXTURE_DOCS, ORG]),
    "public.restricted_access_grants": ("org_id = $1 OR entity_id = ANY($2::uuid[])",
                                        lambda: [ORG, FIXTURE_ENTITIES]),
    "public.staff_assignments": ("org_id = $1 OR entity_id = ANY($2::uuid[])", lambda: [ORG, FIXTURE_ENTITIES]),
    "public.delegate_grants": ("org_id = $1 OR principal_entity_id = ANY($2::uuid[])",
                               lambda: [ORG, FIXTURE_ENTITIES]),
    "public.entities": ("id = ANY($1::uuid[]) OR org_id = $2", lambda: [FIXTURE_ENTITIES, ORG]),
}
TOUCHED = tuple(PREDICATES)
COUNTED = TOUCHED + ("public.users", "public.organizations", "public.roles", "public.user_roles",
                     "public.role_permissions", "portfolio.note_gold_values")


async def fixture_user_ids(conn) -> list:
    rows = await rls_fetch(
        conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    return list({r["id"] for r in rows} | set(FIXTURE_USERS))


async def fixture_counts(conn) -> dict[str, int]:
    out = {t: await rls_val(conn, f"SELECT count(*) FROM {t} WHERE {w}", *a())
           for t, (w, a) in PREDICATES.items()}
    out["public.users"] = await rls_val(
        conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    out["public.organizations"] = await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG)
    out["public.roles"] = await rls_val(conn, "SELECT count(*) FROM roles WHERE org_id = $1 OR id = ANY($2::uuid[])",
                                        ORG, [ROLE_MEMBER, ROLE_DEALS])
    out["public.entities.by_name"] = await rls_val(
        conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1", ENTITY_PREFIX + "%")
    return out


OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def operator_conn():
    from _db_connect import connect, resolve_dsn

    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(conn) -> list[str]:
    """VHK1: the shared scoped cleanup (_verify_cleanup) on the operator DSN — seeded by exactly the old teardown's
    predicates (PREDICATES, the fixture-named entities, audit / assistant rows of the fixture users, the fixture
    roles, users and org); the FK closure takes user_roles / role_permissions / notifications."""
    uids = await fixture_user_ids(conn)
    plan = {t: (w, a()) for t, (w, a) in PREDICATES.items()}
    plan["public.entities"] = ("id = ANY($1::uuid[]) OR org_id = $2 OR (display_name LIKE $3 AND org_id = $2)",
                               [FIXTURE_ENTITIES, ORG, ENTITY_PREFIX + "%"])
    plan["public.audit_log"] = ("org_id = $1 OR user_id = ANY($2::uuid[])", [ORG, uids])
    plan["public.assistant_activities"] = ("org_id = $1 OR user_id = ANY($2::uuid[])", [ORG, uids])
    plan["public.user_roles"] = ("user_id = ANY($1::uuid[]) OR role_id = ANY($2::uuid[])",
                                 [uids, [ROLE_MEMBER, ROLE_DEALS]])
    plan["public.roles"] = ("org_id = $1 OR id = ANY($2::uuid[])", [ORG, [ROLE_MEMBER, ROLE_DEALS]])
    plan["public.users"] = ("id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
                            [FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE])
    op = await operator_conn()
    try:
        async with op.transaction():       # the old teardown's super-admin GUC, LOCAL, around the cleanup's own tx
            await _super(op)
            res = await VC.cleanup(op, orgs=[ORG], users=uids, plan=plan, label="verify_navregroup")
        return res["log"]
    finally:
        await op.close()


async def cleanup_only(conn) -> int:
    """``--cleanup-only``: the same teardown as a real run, then the leftover check — no seeding, no checks, no
    regression, no npm."""
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown(conn)
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
    seconds = _time.monotonic() - t0
    left = sum((await fixture_counts(conn)).values())
    op = await operator_conn()
    try:
        left += await VC.leftover_count(op, orgs=[ORG], users=FIXTURE_USERS)
    finally:
        await op.close()
    print(f"CLEANUP_SECONDS: {seconds:.1f}")
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def table_counts(conn) -> dict[str, int]:
    return {t: await rls_val(conn, f"SELECT count(*) FROM {t}") for t in COUNTED}


async def gold_checksum(conn) -> tuple[int, str]:
    r = await rls_row(
        conn, """SELECT count(*) AS n,
                        md5(COALESCE(string_agg(id::text || field_key || COALESCE(value::text, '') || action
                                                || COALESCE(valid_to::text, '') || metadata::text, ',' ORDER BY id), '')) AS h
                   FROM portfolio.note_gold_values""")
    return int(r["n"]), r["h"]


async def real_docs_checksum(conn) -> tuple[int, str]:
    r = await rls_row(
        conn, """SELECT count(*) AS n,
                        md5(COALESCE(string_agg(id::text || original_filename || status || COALESCE(entity_id::text, ''),
                                                ',' ORDER BY id), '')) AS h
                   FROM documents WHERE org_id <> $1""", ORG)
    return int(r["n"]), r["h"]


# ═══ API through the real app (mocked token) ═══════════════════════════════
async def api(uid, method, path, body=None):
    import main
    sub = _sub(uid)
    claims = {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG), **CLAIMS[uid]}
    main.verify_token = lambda _t: dict(claims)
    kw = {"headers": HEADERS}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify") as client:
        res = await client.request(method, path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


async def seed(conn) -> None:
    role_ok = allowed(await check_defs(conn, "users"), "role")
    check(not role_ok or {"super_admin", "member"} <= role_ok, "fixture users.role values satisfy the live CHECK",
          f"{sorted(role_ok)}")
    scope_ok = allowed(await check_defs(conn, "delegate_grants"), "scope")
    scope = "view_only" if (not scope_ok or "view_only" in scope_ok) else sorted(scope_ok)[0]
    check(not scope_ok or scope in scope_ok, f"fixture delegate_grants.scope '{scope}' is a live CHECK value",
          f"{sorted(scope_ok)}")
    drop_ok = allowed(await check_defs(conn, "document_drops"), "status")
    drop_status = "completed" if (not drop_ok or "completed" in drop_ok) else sorted(drop_ok)[0]
    prop_ok = allowed(await check_defs(conn, "vdr_deal_proposals"), "status")
    check(not prop_ok or "pending" in prop_ok, "fixture vdr_deal_proposals.status 'pending' is a live CHECK value",
          f"{sorted(prop_ok)}")
    doc_status_ok = allowed(await check_defs(conn, "documents"), "status")
    doc_status = "dropped" if (not doc_status_ok or "dropped" in doc_status_ok) else sorted(doc_status_ok)[0]
    perm_ids = {r["name"]: r["id"] for r in await rls_fetch(
        conn, "SELECT id, name FROM permissions WHERE name = ANY($1::text[])", ["view_deals", "manage_deals"])}
    check(set(perm_ids) == {"view_deals", "manage_deals"}, "permissions view_deals and manage_deals exist",
          f"{sorted(perm_ids)}")
    clash = await rls_val(conn, "SELECT count(*) FROM documents WHERE original_filename ILIKE $1 AND org_id <> $2",
                          f"%{TOKEN}%", ORG)
    check(clash == 0, "no real document's filename contains the fixture token", f"{clash}")

    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "NAVREGROUP Verify Fixture Org", f"{ORG}-navregroup")
        for uid in FIXTURE_USERS:
            sub = _sub(uid)
            role = "super_admin" if uid == U_SUPER else "member"
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""", uid, ORG, f"{sub}@test.local", sub, sub, role)
        await conn.execute("INSERT INTO roles (id, org_id, name, description) VALUES ($1, $2, $3, $4), ($5, $2, $6, $4)",
                           ROLE_MEMBER, ORG, "navregroup_member", TAG, ROLE_DEALS, "navregroup_deals")
        await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2), ($3, $4)",
                           ROLE_MEMBER, perm_ids["view_deals"], ROLE_DEALS, perm_ids["manage_deals"])
        await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $3), ($2, $3), ($4, $5)",
                           U_MEMBER, U_MEMBER2, ROLE_MEMBER, U_DEALS, ROLE_DEALS)
        for eid, name, restricted in ((E_MINE, "Mine", False), (E_OTHER, "Other", False), (E_RESTR, "Restricted", True)):
            await conn.execute(
                """INSERT INTO entities (id, org_id, entity_type, display_name, notes, access_restricted)
                   VALUES ($1, $2, 'individual', $3, $4, $5)""", eid, ORG, ENTITY_PREFIX + name, TAG, restricted)
        for principal, delegate in ((E_MINE, U_MEMBER), (E_RESTR, U_MEMBER), (E_RESTR, U_MEMBER2)):
            await conn.execute(
                """INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope)
                   VALUES ($1, $2, $3, $4)""", ORG, principal, delegate, scope)
        for eid in FIXTURE_ENTITIES:
            await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                               "VALUES ($1, $2, $3, $4)", ORG, eid, U_STAFF, TAG)
        await conn.execute("INSERT INTO restricted_access_grants (org_id, entity_id, user_id, reason) "
                           "VALUES ($1, $2, $3, $4)", ORG, E_RESTR, U_MEMBER2, TAG)
        for did, eid, label in ((D_MINE, E_MINE, "mine"), (D_OTHER, E_OTHER, "other"),
                                (D_GENERAL, None, "general"), (D_RESTR, E_RESTR, "restricted")):
            await conn.execute(
                """INSERT INTO documents (id, org_id, entity_id, original_filename, status)
                   VALUES ($1, $2, $3, $4, $5)""", did, ORG, eid, f"{TOKEN}-{label}.pdf", doc_status)
        await conn.execute("INSERT INTO document_drops (id, org_id, status, file_count) VALUES ($1, $2, $3, 0)",
                           DROP, ORG, drop_status)
        await conn.execute(
            """INSERT INTO vdr_deal_proposals (id, org_id, document_drop_id, proposed_fields, status)
               VALUES ($1, $2, $3, $4::jsonb, 'pending')""", PROPOSAL, ORG, DROP, json.dumps({"name": TAG}))


# ═══ Sections ══════════════════════════════════════════════════════════════
def y_task1() -> None:
    section("[Y] Task 1 — findings reported: every route, its API, its real server gate; baseline JSON written")
    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    heads = re.findall(r"^## (1[a-d])\.", text, re.M)
    check(heads == ["1a", "1b", "1c", "1d"], "docs/NAVREGROUP_DISCOVERY.md reports 1a–1d in order", f"{heads}")
    self_pages_have_1a_rows(check, find)
    today_pages()
    rows = [ln for ln in text.splitlines() if ln.startswith("| `/")]
    no_gate = [ln for ln in rows if re.search(r"\bNONE\b", ln) and "[FIND]" not in ln]
    check(not no_gate, "every 1a row whose API has NO gate is marked [FIND]", f"{no_gate[:2]}")
    check(text.count("[FIND]") >= 5, "the [FIND]s are reported", f"{text.count('[FIND]')}")
    code, out, err = run_cmd(["node", str(BASELINE_GEN), BASELINE_REF], REPO, timeout=120)
    try:
        fresh = json.loads(out) if code == 0 else None
        stored = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        fresh, stored = None, {"error": str(exc)}
    check(fresh is not None and fresh == stored,
          f"docs/NAVREGROUP_BASELINE.json equals the baseline regenerated from the pre-sprint code at {BASELINE_REF}",
          (err or "")[-200:])
    personas = (stored or {}).get("personas") or {}
    check(len(personas) == 6 and all(len(v.get("reachable") or []) > 0 for v in personas.values()),
          "the baseline covers all six personas, each with a non-empty reachable set",
          json.dumps({k: len(v.get("reachable") or []) for k, v in personas.items()}))


NODE_RESULTS: dict = {}


def run_node_tests() -> str:
    code, out, err = run_cmd(["node", "--test", "tests/nav/"], WEB_DIR, timeout=300)
    NODE_RESULTS["code"], NODE_RESULTS["out"] = code, out + err
    return out + err


def node_test_ok(name_fragment: str) -> bool:
    out = NODE_RESULTS.get("out", "")
    return re.search(rf"^ok \d+ - .*{re.escape(name_fragment)}", out, re.M) is not None


def y_sidebar_source() -> None:
    section("[Y] Sidebar.jsx keeps no item list and imports its items from menuVisibility.mjs")
    src = SIDEBAR.read_text(encoding="utf-8")
    check('from "@/lib/menuVisibility"' in src and "visibleSections" in src,
          "Sidebar.jsx imports visibleSections from lib/menuVisibility")
    check(re.search(r'href:\s*"', src) is None, "Sidebar.jsx contains no hard-coded href entry")
    check(re.search(r"NAV_ITEMS|\w+_ITEM\s*=|\bcan\(\"|canAccess\(", src) is None,
          "Sidebar.jsx has no item constants and no second gate check")
    run_node_tests()
    check(NODE_RESULTS.get("code") == 0, "node --test tests/nav/ passes",
          NODE_RESULTS.get("out", "")[-300:] if NODE_RESULTS.get("code") else "")
    for persona in ("plain_member", "staff_member", "org_admin_manage_members", "org_admin_manage_org_settings",
                    "super_admin", "no_roles"):
        check(node_test_ok(f"sidebar renders the expected entries for {persona}"),
              f"node render test: the real SidebarNav shows the expected entries for {persona}")


HARNESS_DATA: dict = {}


def load_harness() -> dict:
    code, out, err = run_cmd(["node", str(HARNESS)], REPO, timeout=120)
    try:
        HARNESS_DATA.update(json.loads(out))
    except ValueError:
        HARNESS_DATA["error"] = (err or out)[-300:]
    return HARNESS_DATA


HARNESS_FILES = ("apps/api/scripts/menuvisibility_harness.mjs", "apps/api/scripts/navregroup_personas.mjs",
                 "apps/api/scripts/navregroup_server_gates.mjs", "apps/web/lib/menuVisibility.mjs",
                 "docs/NAVREGROUP_BASELINE.json")


def harness_at(ref: str) -> dict:
    """VHK2: the harness exactly as a commit left it — its five files written from git into a temp tree and run with
    node. navregroup's 'additions are exactly the listed intended ones' is a claim about navregroup's OWN work, so it
    is checked at navregroup's own merge; today's tree is held to the rules in y_server_gates instead."""
    with tempfile.TemporaryDirectory(prefix="navregroup-at-") as tmp:
        for rel in HARNESS_FILES:
            src = VS.git_show(ref, rel)
            if src is None:
                return {"error": f"{rel} absent at {ref}"}
            dest = pathlib.Path(tmp) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src, encoding="utf-8")
        code, out, err = run_cmd(["node", str(pathlib.Path(tmp) / HARNESS_FILES[0])], tmp, timeout=120)
    try:
        return json.loads(out)
    except ValueError:
        return {"error": (err or out)[-300:]}


def y_persona_snapshot() -> None:
    section("[Y] Persona snapshot — at navregroup's own merge: baseline + exactly the intended additions; today: "
            "only the stated removals, no side door, every addition admitted by its traced gate")
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    at_end = (harness_at(end).get("navregroup") or {})
    end_personas = at_end.get("personas") or []
    check(len(end_personas) == 6, f"the harness as navregroup left it runs ({how})", f"{len(end_personas)}")
    for p in end_personas:
        check(p.get("none_removed") is True, f"{p['name']} at navregroup's end: no href removed",
              f"removed {p.get('removed')}")
        check(p.get("additions_exact") is True and len(p.get("intended") or []) > 0,
              f"{p['name']} at navregroup's end: the additions are exactly the listed intended ones",
              f"added {p.get('added')}; intended {p.get('intended')}")
        check(p.get("additions_refused") == [] and p.get("new_refusals") == [],
              f"{p['name']} at navregroup's end: no addition and no new href refused by its server",
              f"{p.get('additions_refused')} {p.get('new_refusals')}")
    nav = (HARNESS_DATA or {}).get("navregroup")
    if not check(isinstance(nav, dict), "the harness ran and returned its navregroup block",
                 HARNESS_DATA.get("error", "")):
        return
    check(nav.get("baseline_ref") == BASELINE_REF, "the harness compares against the pinned baseline",
          f"{nav.get('baseline_ref')}")
    personas = nav.get("personas") or []
    check(len(personas) == 6, "six personas compared", f"{len(personas)}")
    for p in personas:
        check(p["removals_exact"] is True,
              f"{p['name']}: no href removed except the stated removals (AUTHZ1 default-allow, BUGFIX1 coming-soon)",
              f"removed {p['removed']}; stated {p['authz1_removals']}")
        # VHK2: was "the additions are exactly the listed intended ones" over TODAY's tree — every later sprint's
        # new tab failed it. The rule: every href added since the baseline is admitted by its traced server gate.
        check(p.get("additions_ok") is True,
              f"{p['name']}: every href added since the baseline has a traced server gate that admits it",
              f"added {p['added']}; refused {p['additions_refused']}; untraced {p['unknown_server_gate']}")
        check(p["after_equals_sidebar_tabs"] is True,
              f"{p['name']}: everything reachable is rendered by the sidebar model (no side door)")


def y_server_gates() -> None:
    section("[Y] Every visible href is allowed by its page's real server gate (no new refusal)")
    nav = (HARNESS_DATA or {}).get("navregroup") or {}
    for p in nav.get("personas") or []:
        check(p["unknown_server_gate"] == [], f"{p['name']}: every visible href has a traced server gate",
              f"{p['unknown_server_gate']}")
        check(p["additions_refused"] == [], f"{p['name']}: no ADDED href is refused by its server",
              f"{p['additions_refused']}")
        check(p["new_refusals"] == [], f"{p['name']}: no href is refused that was not already refused before",
              f"{p['new_refusals']}")
        if p["refused_after"]:
            find(f"{p['name']}: pre-existing menu/server mismatches carried over unchanged (no href may disappear)",
                 ", ".join(p["refused_after"]))
    # The gate table is test data: spot-check the entries this sprint relies on
    # against the real router source, so the table cannot drift silently.
    src = {f: (API_DIR / "routers" / f).read_text(encoding="utf-8")
           for f in ("billing_groups.py", "custody_import.py", "workflows.py", "vdr.py")}
    lv = re.search(r"async def list_vdr_proposals\(.*?\n(?=@router|\Z)", src["vdr.py"], re.S)
    billing_svc = (API_DIR / "services" / "billing_groups.py").read_text(encoding="utf-8")
    custody_svc = (API_DIR / "services" / "custody" / "importer.py").read_text(encoding="utf-8")
    check(re.search(r'READ_PERMISSION\s*=\s*"view_portfolio"', billing_svc) is not None
          and re.search(r'READ_PERMISSION\s*=\s*"view_portfolio"', custody_svc) is not None
          and "READ_PERMISSION" in src["custody_import.py"],
          "billing groups / custody import read gates are view_portfolio (as tabled)")
    check(all(k in src["workflows.py"] for k in ("view_workflow_runs", "configure_workflow_triggers",
                                                   "author_workflows", "user_has_permission")),
          "workflow gates are the profile-axis keys (as tabled)")
    check(lv is not None and 'rbac.require_permission(pool, user_id, org_id, "manage_deals")' in lv.group(0),
          "GET /vdr-proposals gates on rbac manage_deals (as tabled)")


NAV_TODAY_MJS = HERE.parent / "navregroup_today.mjs"
DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")

# VHK2: a page under app/admin/** or app/portfolio/** that is not a nav tab, a group landing, a redirect or a
# sub-page of a nav tab must state why it has no nav row. A new page without a nav row and without a reason here
# fails today_pages — the rule, not a frozen list of other sprints' pages.
NON_NAV_REASONS = {
    "/portfolio/households": "REP7 household X-ray index — no nav row; reachable by URL only ([FIND]: no link to it "
                             "from any nav tab)",
    "/portfolio/households/[id]": "REP7 household detail (X-ray / authority / lot method / consolidate tabs), "
                                  "linked from /portfolio/households",
    "/portfolio/proposals/[id]": "REP5 proposal detail, linked from Accounts (components/proposals/*)",
}


def run_today(extra: dict | None = None) -> dict:
    args = ["node", str(NAV_TODAY_MJS)]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(extra or {}, fh)
        path = fh.name
    try:
        code, out, err = run_cmd(args + [path], REPO, timeout=120)
    finally:
        os.unlink(path)
    try:
        return json.loads(out)
    except ValueError:
        return {"error": (err or out)[-300:]}


def today_pages() -> None:
    """VHK2: every CURRENT page has a nav row (or is a landing / redirect / sub-page of a nav tab, or states a
    non-nav reason) and every page that serves data has a traced server gate."""
    d = run_today()
    pages = d.get("pages") or []
    if not check(len(pages) > 0, "today's page tree under app/admin/** and app/portfolio/** is classified",
                 d.get("error", "")):
        return
    unexplained = [p["href"] for p in pages if p["kind"] == "other" and p["href"] not in NON_NAV_REASONS]
    check(not unexplained, "every current page is a nav tab, a group landing, a redirect, a sub-page of a nav tab, "
          "or states a non-nav reason", f"{len(pages)} pages; unexplained {unexplained}")
    hrefs = {p["href"]: p for p in pages}
    stale = [h for h in NON_NAV_REASONS if h not in hrefs or hrefs[h]["kind"] != "other"]
    check(not stale, "every stated non-nav reason names a page that exists today without a nav row", f"{stale}")
    untraced = [p["href"] for p in pages if p["kind"] in ("nav", "sub", "other") and not p["traced"]]
    check(not untraced, "every nav / sub / non-nav page has a traced server gate (hand row or its page.js API's "
          "AUTHZ1_ROUTES.json record)", f"{untraced}")
    for h, why in NON_NAV_REASONS.items():
        if "[FIND]" in why and h in hrefs:
            find(f"{h}: {why.split(' — ')[0]}", why)


async def y_live_roles(conn) -> None:
    """VHK2: the six personas carry permission lists read on 2026-10-20; later sprints granted real roles more
    (org_admin 2 → 27). The same rule over every REAL role in the default org, read now: each visible href has a
    traced gate that admits the role, except the mismatches navregroup recorded as pre-existing [FIND]s."""
    section("[Y] Every real role's visible hrefs are admitted by their traced server gates (read now, not frozen)")
    from services.authz import INTERNAL_ROLE_NAMES, STAFF_ROLE_NAMES
    rows = await rls_fetch(conn, """SELECT r.name, array_agg(DISTINCT p.name ORDER BY p.name) AS perms
        FROM roles r JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id
        WHERE r.org_id = $1 GROUP BY r.name ORDER BY r.name""", DEFAULT_ORG)
    extra = {}
    for r in rows:
        name = r["name"]
        account_role = "super_admin" if name == "super_admin" else ("org_admin" if name == "org_admin" else "member")
        extra[f"live:{name}"] = {"account_role": account_role, "role": name, "roles": [name], "status": "active",
                                 "permissions": list(r["perms"]), "is_staff": name in STAFF_ROLE_NAMES,
                                 "is_internal": name in INTERNAL_ROLE_NAMES}   # STAFFROLES1 (/users/me)
    nav = (HARNESS_DATA or {}).get("navregroup") or {}
    pre_existing = set().union(*[set(p.get("refused_before") or []) for p in nav.get("personas") or []])
    d = run_today(extra)
    live = [p for p in d.get("personas") or [] if not p["fixture"]]
    VS.when_empty(live, "every real role: each visible href has a traced gate that admits it (beyond navregroup's "
                  "pre-existing [FIND]s)",
                  lambda p: not p["untraced"] and set(p["refused"]) <= pre_existing, check=check, find=find,
                  detail=lambda bad: str([(p["name"], p["untraced"], sorted(set(p["refused"]) - pre_existing))
                                          for p in bad]))
    carried = sorted({h for p in live for h in p["refused"]})
    if carried:
        find("real roles: navregroup's pre-existing menu/server mismatches still shown", ", ".join(carried))


def y_groups() -> None:
    section("[Y] Groups — shown iff ≥1 visible tab; landing = first visible tab; no tab → refusal, not a loop")
    nav = (HARNESS_DATA or {}).get("navregroup") or {}
    groups = nav.get("groups") or []
    check(len(groups) > 0, "group × persona matrix computed", f"{len(groups)} rows")
    bad = [g for g in groups if not (g["shown_iff_tab"] and g["landing_is_first"] and g["decision_ok"]
                                     and g["redirect_target_is_tab"])]
    check(len(groups) > 0 and not bad, "every group × persona obeys shown-iff-tab / landing-first / decision",
          f"{bad[:3]}")
    refusals = nav.get("refusal_pairs") or []
    check(len(refusals) > 0, "at least one persona has a group with no visible tab (the refusal case is exercised)",
          f"{len(refusals)} e.g. {refusals[:3]}")
    lit = nav.get("landing_is_tab_groups") or []
    check(len(lit) > 0 and all(x["ok"] for x in lit),
          "groups whose landing is a tab href (no route of their own) always land on that tab", json.dumps(lit))
    check(all(nav.get(k) is True for k in ("every_item_has_section", "every_group_item_known", "every_group_has_tab")),
          "every item has a section; every group id is known; every group has a tab")
    landings = nav.get("group_landings") or {}
    tab_hrefs = set((nav.get("labels") or {}).keys())
    routed = []
    for gid, path in sorted(landings.items()):
        if path in tab_hrefs:
            continue
        page = WEB_DIR / "app" / path.lstrip("/") / "page.js"
        routed.append(path)
        txt = page.read_text(encoding="utf-8") if page.exists() else ""
        check("GroupLanding" in txt and f'groupId="{gid}"' in txt,
              f"landing route {path} exists and renders GroupLanding for {gid}")
    check(len(routed) >= 8, "every group with its own landing has a route", f"{routed}")
    for name in ("group landing redirects to the first visible tab",
                 "group landing with no visible tab renders the refusal, not a redirect",
                 "group landing without a session goes to login"):
        check(node_test_ok(name), f"executed in Node (no live server): {name}")


def git_show(path: str) -> str | None:
    code, out, _ = run_cmd(["git", "show", f"{BASELINE_REF}:{path}"], REPO, timeout=60)
    return out if code == 0 else None


def y_urls_unchanged() -> None:
    section("[Y] Every pre-existing URL keeps its page and its server gate (member, org admin, super admin)")
    self_urls_unchanged(check, find)
    admin = (REPO / "apps/web/app/admin/page.js").read_text(encoding="utf-8")
    check('const session = await getHostSession();' in admin
          and 'if (!session) redirect("/auth/login?returnTo=/admin");' in admin,
          "/admin (rebuilt as the grouped overview) keeps the same session gate and URL")


async def y_docsearch(conn, url) -> None:
    section("[Y] Document Search — leak reproduced with the old org-wide query, closed by the caller-scoped one")
    from services import document_linkage as dl
    from services.database import get_pool, reset_rls_context, set_rls_context
    # Independent re-read: a fresh connection sees all four fixture documents with
    # their entities, so a document missing below was FILTERED, not absent.
    c2 = await asyncpg.connect(url, statement_cache_size=0)
    try:
        rows = await rls_fetch(c2, "SELECT id, entity_id FROM documents WHERE id = ANY($1::uuid[])", FIXTURE_DOCS)
    finally:
        await c2.close()
    got = {r["id"]: r["entity_id"] for r in rows}
    check(got == {D_MINE: E_MINE, D_OTHER: E_OTHER, D_GENERAL: None, D_RESTR: E_RESTR},
          "independent re-read: the four fixture documents exist with their entities", f"{len(got)} rows")

    # The old code path on the app's own RLS pool, org context set exactly as the
    # endpoint's middleware sets it (not the platform bypass).
    pool = await get_pool()
    tokens = set_rls_context(str(ORG), False)
    try:
        async with pool.acquire() as c:
            before = await dl.search_documents_org_wide(c, str(ORG), TOKEN, limit=50)
    finally:
        reset_rls_context(tokens)
    before_ids = {UUID(r["document_id"]) for r in before}
    check(before_ids == set(FIXTURE_DOCS),
          "BEFORE (old org-wide query, any caller): all four documents returned — the leak, reproduced",
          f"{len(before_ids)}")
    check(D_OTHER in before_ids and D_RESTR in before_ids,
          "BEFORE: a member would see another entity's document and a restricted entity's document")
    old_src = git_show("apps/api/routers/document_links.py") or ""
    check("dl.search_documents(conn, org_id, q, limit=limit)" in old_src,
          f"the pre-fix endpoint at {BASELINE_REF} called the org-only search with no caller")

    # Expected sets from the SAME predicates run directly in SQL (not the app code).
    member_expected = {r["id"] for r in await rls_fetch(
        conn, """SELECT d.id FROM documents d JOIN entities e ON e.id = d.entity_id
                  WHERE d.id = ANY($1::uuid[])
                    AND d.entity_id IN (SELECT principal_entity_id FROM delegate_grants
                                         WHERE delegate_user_id = $2 AND revoked_at IS NULL)
                    AND (NOT e.access_restricted OR EXISTS (SELECT 1 FROM restricted_access_grants g
                                                            WHERE g.entity_id = e.id AND g.user_id = $2))""",
        FIXTURE_DOCS, U_MEMBER)}
    check(member_expected == {D_MINE}, "direct SQL: the member may see exactly their own entity's document",
          f"{member_expected}")

    results = {}
    for uid in (U_MEMBER, U_MEMBER2, U_STAFF, U_SUPER):
        code, body = await api(uid, "GET", f"/api/v1/document-search?q={TOKEN}")
        results[uid] = (code, {UUID(r["document_id"]) for r in (body or {}).get("results") or []})
    m_code, m_ids = results[U_MEMBER]
    check(m_code == 200 and len(m_ids) > 0 and m_ids == member_expected,
          "AFTER: the member sees ONLY the documents direct SQL says they can see", f"HTTP {m_code}: {m_ids}")
    check(D_OTHER not in m_ids and D_GENERAL not in m_ids,
          "AFTER: another entity's document and an org-general document are hidden from the member")
    check(D_RESTR not in m_ids, "AFTER: the restricted entity's document is hidden from the non-allow-listed member")
    m2_code, m2_ids = results[U_MEMBER2]
    check(m2_code == 200 and m2_ids == {D_RESTR},
          "AFTER: the allow-listed member sees the restricted entity's document (and only it)",
          f"HTTP {m2_code}: {m2_ids}")
    s_code, s_ids = results[U_STAFF]
    check(s_code == 200 and s_ids == before_ids - {D_RESTR},
          "AFTER: staff (assigned to every fixture entity) unchanged apart from the restricted document",
          f"HTTP {s_code}: {s_ids}")
    sa_code, sa_ids = results[U_SUPER]
    check(sa_code == 200 and (before_ids - {D_RESTR}) <= sa_ids,
          "AFTER: super admin still sees every non-restricted document", f"HTTP {sa_code}: {sa_ids}")
    find("restricted documents are now hidden from non-allow-listed staff and Super Admin in keyword search",
         f"super admin sees D_RESTR: {D_RESTR in sa_ids} — filter_restricted has no Super Admin bypass; "
         "semantic search behaves the same")
    check(m_ids < s_ids, "identical request, different caller: the member's set is a strict, non-empty subset of staff's",
          f"{len(m_ids)} < {len(s_ids)}")
    find("AUTHZ1: is_staff now comes from the DB Principal (role membership), not a token claim",
         "before AUTHZ1 real tokens carried no roles claim and every real caller took the staff path")


async def y_vdr() -> None:
    section("[Y] VDR proposals list — member 403, manage_deals 200, identical request")
    old_src = git_show("apps/api/routers/vdr.py") or ""
    old_list = re.search(r"async def list_vdr_proposals\(.*?\n(?=@router|\Z)", old_src, re.S)
    check(old_list is not None and "require_permission" not in old_list.group(0),
          f"BEFORE ({BASELINE_REF}): GET /vdr-proposals had no permission check — reproduced from the source")
    path = "/api/v1/vdr-proposals"
    m_code, m_body = await api(U_MEMBER, "GET", path)
    d_code, d_body = await api(U_DEALS, "GET", path)
    check(m_code == 403 and "manage_deals" in json.dumps(m_body),
          "member (role grants view_deals only): 403 naming manage_deals", f"HTTP {m_code}: {m_body}")
    ids = [p.get("id") for p in (d_body or {}).get("proposals") or []]
    check(d_code == 200 and str(PROPOSAL) in ids,
          "manage_deals holder: 200 and the fixture proposal is listed", f"HTTP {d_code}: {len(ids)} proposals")
    sa_code, _ = await api(U_SUPER, "GET", path)
    check(sa_code == 200, "super admin: 200 (checked first)", f"HTTP {sa_code}")


def y_labels() -> None:
    section("[Y] Labels come only from the definition; /admin landing shows grouped cards")
    menu_src = MENU.read_text(encoding="utf-8")
    for new in ('"Gold answers"', '"Accuracy results"', '"Review queue"', '"Teams & Coverage"', '"Tenants"',
                'label: "Portfolio"'):
        check(new in menu_src, f"the definition carries {new}")
    old_labels = ["Note Gold Set", "Extraction Results", "Note Terms Review", "Staff Visibility", "Platform Settings"]
    hits = []
    for root in ("components", "app"):
        for f in (WEB_DIR / root).rglob("*.js*"):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for lbl in old_labels:
                if re.search(rf">\s*{re.escape(lbl)}\s*<|label:\s*\"{re.escape(lbl)}\"", txt):
                    hits.append(f"{f.relative_to(WEB_DIR)}: {lbl}")
            if re.search(r'label:\s*"Investments",\s*href:\s*"/portfolio"', txt):
                hits.append(f"{f.relative_to(WEB_DIR)}: Investments nav item")
    check(not hits, "no renamed label survives as a hard-coded heading or nav label in a component", f"{hits[:4]}")
    for page, href in (("admin/note-extraction/gold", "/admin/note-extraction/gold"),
                       ("admin/staff-visibility", "/admin/staff-visibility"), ("admin/platform", "/admin/platform")):
        txt = (WEB_DIR / "app" / page / "page.js").read_text(encoding="utf-8")
        check(f'labelFor("{href}")' in txt, f"{page} heading reads its label from the definition")
    admin = (WEB_DIR / "app" / "admin" / "page.js").read_text(encoding="utf-8")
    check("visibleAdminGroups" in admin and "SECTION_DESCRIPTIONS" not in admin and "g.description" in admin
          and "g.tabs.map" in admin, "/admin renders one card per visible group with its description and tab links")


def y_build() -> None:
    section("[Y] npm run build exits 0; node tests for the harness and the Sidebar pass")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")
    check(NODE_RESULTS.get("code") == 0, "node tests (tests/nav) pass")
    code2, out2, err2 = run_cmd(["npm", "test"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out2 + err2, re.M)
    check(code2 == 0 and m is not None and m.group(1) == "0", "the full web node suite passes",
          (m.group(0) if m else (out2 + err2)[-300:]))
    check("error" not in HARNESS_DATA and "navregroup" in HARNESS_DATA, "the menu-visibility harness ran")


def y_status_doc() -> None:
    section("[Y] PROJECT_STATUS records the regroup")
    text = STATUS_DOC.read_text(encoding="utf-8")
    for needle in ("navregroup", "Sidebar must never hard-code", "NAVREGROUP_BASELINE.json", "Document Search",
                   "vdr-proposals", "[FIND]"):
        check(needle in text, f"PROJECT_STATUS covers {needle}")


# ═══ Regression ════════════════════════════════════════════════════════════
def regression_harness_existing() -> None:
    """The harness's PRE-EXISTING assertions (verify_superadminmenu's menu section),
    over a fresh flat run of the harness."""
    section("[Y] Regression: the menu-visibility harness's existing assertions — ONCE, a flat subprocess")
    code, out, err = run_cmd(["node", str(HARNESS)], REPO, timeout=120,
                             env=os.environ | {"VERIFY_NESTED": "1"})
    try:
        d = json.loads(out)
    except ValueError:
        check(False, "harness output parses", (err or out)[-200:])
        return
    sa = d["superAdminNoProfiles"]
    check(len(d["gates"]) == len(d["allHrefs"]) > 0, "every menu gate enumerated")
    # VHK2 FIX 1: BUGFIX1's comingSoon items are hidden from EVERY menu on purpose (no page exists), so "every item"
    # means every item that is not comingSoon — and the comingSoon ones must be hidden from Super Admin too.
    soon = d.get("comingSoon")
    shown = [h for h in d["allHrefs"] if h not in (soon or [])]
    sas = {"no_profiles": sa["visible"], "no_roles": d["superAdminNoRoles"]["visible"],
           "granted_admin": d["superAdminGrantedAdmin"]["visible"]}
    check(isinstance(soon, list) and all(h in sa["visible"] for h in shown),
          "a super_admin with ZERO Profiles sees every item (every item that is not comingSoon)",
          f"missing {[h for h in shown if h not in sa['visible']]}")
    check(all(sorted(v) == sorted(shown) for k, v in sas.items() if k != "no_profiles")
          and all(h in sas["no_roles"] and h in sas["granted_admin"] for h in shown),
          "super_admin with no roles / granted admin sees every item (every item that is not comingSoon)",
          f"{ {k: sorted(set(shown) - set(v)) for k, v in sas.items()} }")
    check(not any(h in v for v in sas.values() for h in (soon or [])),
          "BUGFIX1's comingSoon items are hidden from every Super Admin menu", f"comingSoon {soon}")
    check(all(h in sa["visibleAdmin"] for h in d["adminIndexHrefs"]), "/admin index agrees for super admin")
    check(d["gateOmittingSuperAdmin"] is True, "a role gate omitting super_admin still admits platform staff")
    for reg in d["regressions"]:
        check(reg["unchanged"] is True or reg.get("authz1_expected") is True,
              f"REGRESSION — {reg['name']}'s menu matches the legacy rule (or differs by exactly AUTHZ1's "
              "default-allow removal)", f"lost {reg.get('lost')}; expected (derived) {reg.get('expected_loss')}; "
              f"AUTHZ1's hand list inside it: {reg.get('hand_floor')}")


def regression_bulkfetchui2() -> None:
    script = "verify_bulkfetchui2.py"
    section(f"[Y] Regression: {script} — ONCE, a flat subprocess, AFTER this script's own teardown")
    code, out, err = run_cmd([sys.executable, str(HERE.parent / script)], REPO, timeout=1200,
                             env=os.environ | {"VERIFY_NESTED": "1"})
    text = out + err
    try:
        log_dir = REPO / "sprint_prompts" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "verify_navregroup.regression.verify_bulkfetchui2.out").write_text(text)
    except OSError:
        pass
    nt = VT.nested_total(text, code)
    fails = [ln for ln in text.splitlines() if ln.startswith("[FAIL]")]
    check(nt.ok, f"{script} still reports 0 FAIL",
          (nt.detail() if nt.ok else f"{nt.detail()}; {text[-300:]}") + (f"; {fails[:3]}" if fails else ""))


def y_regression() -> None:
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
        return
    regression_harness_existing()
    regression_bulkfetchui2()


# ═══ Main ══════════════════════════════════════════════════════════════════
async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = url
    conn = await asyncpg.connect(url, statement_cache_size=0)
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre_counts = gold_before = docs_before = None

    async def run(name, fn, *a):
        try:
            res = fn(*a)
            if asyncio.iscoroutine(res):
                res = await res
            return res
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")
            return None

    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the verify connection's role does NOT bypass RLS (and the API pool uses the same URL)",
              f"{await conn.fetchval('SELECT current_user')}")
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "start-of-run teardown", f"{type(exc).__name__}: {exc}")
        left0 = await fixture_counts(conn)
        check(sum(left0.values()) == 0, "no fixture rows before the run", json.dumps(left0))
        pre_counts = await table_counts(conn)
        gold_before = await gold_checksum(conn)
        docs_before = await real_docs_checksum(conn)

        await run("task 1", y_task1)
        await run("sidebar source + node tests", y_sidebar_source)
        await run("harness", load_harness)
        await run("persona snapshot", y_persona_snapshot)
        await run("server gates", y_server_gates)
        await run("live roles", y_live_roles, conn)
        await run("groups", y_groups)
        await run("urls unchanged", y_urls_unchanged)
        await run("seed", seed, conn)
        await run("document search", y_docsearch, conn, url)
        await run("vdr", y_vdr)
        await run("labels", y_labels)
        await run("build", y_build)
        await run("status doc", y_status_doc)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        section("[Y] Teardown: zero fixture rows; every touched table at its pre-test count; real data untouched")
        teardown_clean = False
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            teardown_clean = sum(left.values()) == 0
            check(teardown_clean, "zero fixture rows remain", json.dumps(left))
            if pre_counts is not None:
                post = await table_counts(conn)
                diff = {t: (pre_counts[t], post[t]) for t in COUNTED if pre_counts[t] != post[t]}
                check(not diff, "every touched table is back at its exact pre-test count", json.dumps(diff))
                teardown_clean = teardown_clean and not diff
            else:
                check(False, "pre-test counts were taken")
                teardown_clean = False
            gold_after = await gold_checksum(conn)
            check(gold_before is not None and gold_after == gold_before,
                  "existing note_gold_values unchanged (count and checksum)", f"{gold_before} -> {gold_after}")
            docs_after = await real_docs_checksum(conn)
            check(docs_before is not None and docs_after == docs_before,
                  "real documents (every other org) unchanged (count and checksum)", f"{docs_before} -> {docs_after}")
        except Exception as exc:  # noqa: BLE001
            check(False, "end-of-run teardown", f"{type(exc).__name__}: {exc}")
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        await conn.close()
        if teardown_clean:
            y_regression()
        else:
            check(False, "regression skipped: this run's own teardown did not leave zero fixture rows")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
