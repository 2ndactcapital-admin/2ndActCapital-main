"""verify_previewperffix1.py — PREVIEWPERF FIX 1: the ~1.7 s verify_bulkfetchui spent OUTSIDE preview().

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_previewperffix1.py [--skip-bulkfetchui]

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL', exits non-zero on any failure.

DISCOVERY (Task 1, in the sprint's report and PROJECT_STATUS): verify_bulkfetchui's own call path costs the same as
previewperf's harness (client 0.2 ms, JSON parse 0.1 ms; ~680 ms e2e alone) — the 2.2 s was ~20 sequential round
trips x a laptop->pooler RTT that drifts 40 -> 110-210 ms on its own (seen on an idle connection while the process
slept). The time is real per-request cost, so the fix is product code: the middleware Principal took 11-12 statements;
it now takes 6 (BEGIN, set_config, ONE resolve+load statement, the throttled touch, COMMIT, pool reset). No cache.

  (0) --self-checks-only: own diff = authz.py, users.py, this verify, status doc, regression list; no DDL; no cache.
  (1) no live bulk worker (edgar_fetch_workers heartbeats in WORKER_LOST_SECONDS).
  (2) EQUIVALENCE — every users row in the database: the new one-statement load_principal == the pre-fix 4-query
      construction (rbac.fetch_role_names / fetch_permission_names / fetch_all_permission_names — unchanged helpers,
      the exact pre-fix code path), field for field.
  (3) THROUGH THE APP (real middleware, real RLS pool), a fixture user resolved by sub (not the UUID stub):
      principal phase statements <= 6 (11 before) and resolved_via 'sub'; a linked second sub resolves via
      'identity' to the same user; a placeholder-email row falls back (None from the fast path); and every
      grant change is visible on the VERY NEXT request, both ways: org_admin granted -> role + manage_org_settings,
      revoked -> gone; super_admin set -> is_super + whole catalog, unset -> gone; status pending -> 403
      'Access pending', active -> admitted again.
  (4) TIMING — the bulkfetchui JPMorgan 2025 preview through the app, best of 3 (load rule as bulkfetchui), with
      the RTT median printed; e2e < 2 s (limit fixed) and principal <= 6 statements.
  (5) verify_bulkfetchui.py in full (VERIFY_NESTED=1) -> exit 0, 0 FAIL. Skipped with --skip-bulkfetchui.

FIXTURES: one org (FIX_ORG) and two users (subs 'previewperffix1_*'); torn down at start and end by the shared scoped
cleanup (_verify_cleanup) on the operator DSN; users/organizations/roles/user_roles/user_identities counts checked
equal before/after. Real users are only READ (section 2, under the platform carve-out).
"""
from __future__ import annotations

import os

os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import pathlib
import re
import statistics
import subprocess
import sys
import time
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

import _verify_cleanup as VC  # noqa: E402
import _verify_self as VS  # noqa: E402

SPRINT_KEY = "previewperf fix 1"
BEFORE_REF = "ca39a17"   # previewperf
OWN_FILES = {
    "apps/api/services/authz.py", "apps/api/services/users.py", "apps/api/scripts/verify_previewperffix1.py",
    "docs/PROJECT_STATUS.md", "scripts/regression_set.txt", "sprint_prompts/previewperf.fix1.structural.md",
    # CATCHALL1 Task 8 — the fix's own commit 58a6217 is "… ; ignore Claude outputs/" (its .gitignore line)
    ".gitignore",
}


def self_checks(check, find) -> None:
    code, out, err = VS.own_diff(BEFORE_REF, SPRINT_KEY, args=("--name-only",))
    files = {f for f in out.split() if f}
    extra = sorted(f for f in files if f not in OWN_FILES and not f.startswith("Claude outputs/"))
    check(code == 0 and not extra, "the fix's own diff touches only authz.py, users.py, its verify, the status doc "
          "and the regression list", f"exit {code} {err.strip()[:200]}; unexpected {extra}")
    code, diff, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "apps/api/services")
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    ddl = [ln for ln in added if re.search(r"\b(CREATE|DROP|ALTER)\s+(UNIQUE\s+)?(INDEX|TABLE)\b", ln, re.I)]
    check(code == 0 and not ddl, "no DDL in the fix's service diff", f"{ddl[:3]}")
    cache = [ln for ln in added if re.search(r"\b(ttl|lru_cache|_CACHE\b|monotonic\(\))", ln, re.I)]
    check(code == 0 and not cache, "no Principal cache was added (fewer round trips, read fresh every request)",
          f"{cache[:3]}")
    src = VS.own_text(SPRINT_KEY, "apps/api/services/authz.py") or ""
    check("PRINCIPAL_COLUMNS" in src and "fetch_role_names" not in src.split("def load_principal", 1)[-1][:900],
          "at the fix's end, load_principal reads one statement (PRINCIPAL_COLUMNS), not the rbac helpers")


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

HYDRATE_ERROR = hydrate()

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import main  # noqa: E402
from services import authz, rbac  # noqa: E402
from services import edgar_bulk as eb  # noqa: E402
from services import edgar_bulk_queue as bulkq  # noqa: E402
from services import users as su  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"   # as verify_bulkfetchui's teardown
FIX_ORG = UUID("99000000-0000-0000-0000-0000ff1f0001")
U_MAIN = UUID("99000000-0000-0000-0000-0000ff1f0011")
U_PLACE = UUID("99000000-0000-0000-0000-0000ff1f0012")
SUB_MAIN = "previewperffix1_main"
SUB_LINKED = "previewperffix1_linked"
SUB_PLACE = "previewperffix1_place"
SUB_LIKE = "previewperffix1_%"
PREVIEW_URL = "/api/v1/admin/edgar/bulk/preview"
HEADERS = {"Authorization": "Bearer verify-token"}
LIMIT_SECONDS = 2.0                 # bulkfetchui's limit — fixed, never raised
LOAD_PER_CPU = 0.75
LOAD_WAIT_SECONDS = 30
LOAD_ROUNDS = 5
PRINCIPAL_MAX_STATEMENTS = 6        # 11 (sub) / 12 (UUID stub) before
SUB_TIMEOUT = 1800
COUNTED = ("users", "organizations", "roles", "user_roles", "user_identities", "role_permissions")

_n_pass = 0
_n_fail = 0
STMTS = {"n": 0}
PHASES: dict[str, tuple[float, int]] = {}
CAPTURED: list = []


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def load_per_cpu() -> float:
    try:
        return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except OSError:
        return 0.0


async def scoped(conn, sql: str, *args, many: bool = True):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await (conn.fetch if many else conn.fetchval)(sql, *args)


def instrument() -> None:
    for name in ("execute", "fetch", "fetchrow", "fetchval"):
        orig = getattr(asyncpg.Connection, name)

        def wrap(orig):
            async def counted(self, *a, **k):
                STMTS["n"] += 1
                return await orig(self, *a, **k)
            return counted
        setattr(asyncpg.Connection, name, wrap(orig))
    orig_resolve = su.resolve_request_principal

    async def timed(request):
        t, n0 = time.perf_counter(), STMTS["n"]
        p = None
        try:
            p = await orig_resolve(request)
            return p
        finally:
            PHASES["principal"] = ((time.perf_counter() - t) * 1000, STMTS["n"] - n0)
            CAPTURED.append(p)
    su.resolve_request_principal = timed
    orig_preview = bulkq.preview

    async def timed_preview(*a, **k):
        t, n0 = time.perf_counter(), STMTS["n"]
        try:
            return await orig_preview(*a, **k)
        finally:
            PHASES["preview()"] = ((time.perf_counter() - t) * 1000, STMTS["n"] - n0)
    bulkq.preview = timed_preview


async def api(sub: str, method: str, path: str, body=None):
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(FIX_ORG)}
    CAPTURED.clear()
    PHASES.clear()
    kw = {"headers": HEADERS} | ({"json": body} if body is not None else {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify") as client:
        res = await client.request(method, path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed, (CAPTURED[-1] if CAPTURED else None)


# ── fixtures ─────────────────────────────────────────────────────────────────

async def operator() -> asyncpg.Connection:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN: {how}")
    return await connect(dsn)


async def teardown() -> None:
    op = await operator()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await VC.cleanup(op, orgs=[FIX_ORG], users=[U_MAIN, U_PLACE], user_sub_like=SUB_LIKE,
                             label="verify_previewperffix1")
    finally:
        await op.close()


async def leftovers() -> int:
    op = await operator()
    try:
        return await VC.leftover_count(op, orgs=[FIX_ORG], users=[U_MAIN, U_PLACE], user_sub_like=SUB_LIKE)
    finally:
        await op.close()


async def counts(conn) -> dict:
    return {t: await scoped(conn, f"SELECT count(*) FROM public.{t}", many=False) for t in COUNTED}


async def seed() -> None:
    op = await operator()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await op.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                             FIX_ORG, "PREVIEWPERF FIX 1 Verify Org", f"{FIX_ORG}-previewperffix1")
            await op.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active, status)
                   VALUES ($1, $2, $3, 'PPF1 main', $4, 'member', true, 'active')""",
                U_MAIN, FIX_ORG, f"{SUB_MAIN}@test.local", SUB_MAIN)
            await op.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active, status)
                   VALUES ($1, $2, $3, 'PPF1 placeholder', $4, 'member', true, 'active')""",
                U_PLACE, FIX_ORG, su.placeholder_email(SUB_PLACE), SUB_PLACE)
            await op.execute(
                """INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via)
                   VALUES ($1, $2, $3, 'https://verify.test/', $4, 'verified_email')""",
                U_MAIN, FIX_ORG, SUB_LINKED, f"{SUB_MAIN}@test.local")
    finally:
        await op.close()


async def mutate(sql: str, *args) -> None:
    op = await operator()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await op.execute(sql, *args)
    finally:
        await op.close()


async def rbac_call(fn, *args) -> None:
    op = await operator()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await fn(op, *args)
    finally:
        await op.close()


# ── sections ─────────────────────────────────────────────────────────────────

async def y_worker(conn) -> bool:
    section("[Y] (1) No live bulk worker")
    live = await scoped(conn, f"""SELECT worker_id FROM portfolio.edgar_fetch_workers WHERE stopped_at IS NULL
                                   AND heartbeat_at >= now() - interval '{bulkq.WORKER_LOST_SECONDS} seconds'""")
    return check(not live, "no edgar_fetch_workers heartbeat (timing and the full bulkfetchui run need it off)",
                 f"{[r['worker_id'] for r in live]}")


async def old_principal(conn, user_id):
    """The PRE-FIX authz.load_principal, verbatim in logic (four statements via the unchanged rbac helpers)."""
    row = await conn.fetchrow("SELECT id, org_id, email, role, status, is_active FROM users WHERE id = $1", user_id)
    if row is None:
        return None
    org_id = str(row["org_id"])
    status = row["status"] or authz.STATUS_ACTIVE
    is_active = bool(row["is_active"])
    admitted = status == authz.STATUS_ACTIVE and is_active
    roles, perms, is_super = [], set(), False
    if admitted:
        is_super = rbac.is_super_admin(dict(row))
        roles = await rbac.fetch_role_names(conn, row["id"], org_id)
        perms = await rbac.fetch_permission_names(conn, row["id"], org_id)
        if is_super:
            perms |= await rbac.fetch_all_permission_names(conn)
    return authz.Principal(user_id=str(row["id"]), org_id=org_id, email=row["email"], account_role=row["role"],
                           status=status, is_active=is_active, is_super_admin=is_super,
                           is_staff=bool(admitted and (is_super or authz.STAFF_ROLE_NAMES.intersection(roles))),
                           roles=tuple(roles), permissions=frozenset(perms), resolved_via="sub")


async def y_equivalence(conn) -> None:
    section("[Y] (2) Every users row: one-statement load_principal == the pre-fix 4-query construction")
    ids = [r["id"] for r in await scoped(conn, "SELECT id FROM users ORDER BY id")]
    diffs, n_roles, n_super, n_unadmitted = [], 0, 0, 0
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid in ids:
            new = await authz.load_principal(conn, uid)
            old = await old_principal(conn, uid)
            if new != old:
                diffs.append(str(uid))
            n_roles += bool(new and new.roles)
            n_super += bool(new and new.is_super_admin)
            n_unadmitted += bool(new and not new.admitted)
    check(bool(ids) and not diffs, f"{len(ids)} users: new == old field for field (incl. roles order, permission "
          "set, super catalog, staff flag)",
          f"{len(diffs)} differ {diffs[:5]}; {n_roles} with roles, {n_super} super, {n_unadmitted} not admitted")
    check(n_roles > 0 and n_super > 0 and n_unadmitted > 0,
          "the comparison covered users with roles, super admins and non-admitted rows (not vacuous)",
          f"{n_roles} / {n_super} / {n_unadmitted}")


async def y_app(conn) -> None:
    section("[Y] (3) Through the app: statements, both resolution paths, the fallback, every change next request")
    code, body, p = await api(SUB_MAIN, "GET", "/api/v1/users/me")
    n = PHASES.get("principal", (0, 99))[1]
    check(p is not None and p.user_id == str(U_MAIN) and p.resolved_via == "sub" and n <= PRINCIPAL_MAX_STATEMENTS,
          f"a primary sub resolves in <= {PRINCIPAL_MAX_STATEMENTS} statements (11 before)",
          f"HTTP {code}; {n} statements; via {getattr(p, 'resolved_via', None)}")
    code, _, p2 = await api(SUB_LINKED, "GET", "/api/v1/users/me")
    n2 = PHASES.get("principal", (0, 99))[1]
    check(p2 is not None and p2.user_id == str(U_MAIN) and p2.resolved_via == "identity"
          and n2 <= PRINCIPAL_MAX_STATEMENTS,
          "a linked second sub resolves via 'identity' to the same user, same statement budget",
          f"HTTP {code}; {n2} statements; {getattr(p2, 'user_id', None)} via {getattr(p2, 'resolved_via', None)}")

    class _Req:
        class state:  # noqa: N801
            user = {"sub": SUB_PLACE, "org_id": str(FIX_ORG)}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        fast = await su._fast_principal(conn, _Req)
    check(fast is None, "a placeholder-email row still owed its /userinfo attempt falls back to the full path",
          f"{fast}")

    # every grant change is visible on the very next request, both ways
    await rbac_call(rbac.grant_org_admin, U_MAIN, FIX_ORG)
    _, _, pa = await api(SUB_MAIN, "GET", "/api/v1/users/me")
    await rbac_call(rbac.revoke_org_admin, U_MAIN, FIX_ORG)
    _, _, pb = await api(SUB_MAIN, "GET", "/api/v1/users/me")
    check(pa is not None and "org_admin" in pa.roles and pa.has(rbac.ORG_ADMIN_PERMISSION)
          and pb is not None and "org_admin" not in pb.roles and not pb.has(rbac.ORG_ADMIN_PERMISSION),
          "org_admin granted -> role + manage_org_settings on the next request; revoked -> both gone on the next",
          f"granted {getattr(pa, 'roles', None)}; revoked {getattr(pb, 'roles', None)}")
    catalog = {r["name"] for r in await scoped(conn, "SELECT name FROM permissions")}
    await mutate("UPDATE users SET role = 'super_admin' WHERE id = $1", U_MAIN)
    _, _, pc = await api(SUB_MAIN, "GET", "/api/v1/users/me")
    await mutate("UPDATE users SET role = 'member' WHERE id = $1", U_MAIN)
    _, _, pd = await api(SUB_MAIN, "GET", "/api/v1/users/me")
    check(pc is not None and pc.is_super_admin and set(pc.permissions) == catalog and pd is not None
          and not pd.is_super_admin and not pd.permissions,
          "super_admin set -> is_super + the whole catalog on the next request; unset -> neither on the next",
          f"set {getattr(pc, 'is_super_admin', None)} {len(getattr(pc, 'permissions', ()))}/{len(catalog)}; "
          f"unset {getattr(pd, 'is_super_admin', None)} {len(getattr(pd, 'permissions', ()))}")
    await mutate("UPDATE users SET status = 'pending' WHERE id = $1", U_MAIN)
    code_p, body_p, _ = await api(SUB_MAIN, "POST", PREVIEW_URL, {})
    await mutate("UPDATE users SET status = 'active' WHERE id = $1", U_MAIN)
    code_a, body_a, pe = await api(SUB_MAIN, "POST", PREVIEW_URL, {})
    check(code_p == 403 and body_p.get("detail") == authz.ACCESS_PENDING_DETAIL and pe is not None and pe.admitted
          and body_a.get("detail") != authz.ACCESS_PENDING_DETAIL,
          "status pending -> 403 'Access pending' on the next request; active -> admitted on the next",
          f"pending HTTP {code_p} {body_p.get('detail')!r}; active HTTP {code_a} admitted {getattr(pe, 'admitted', None)}")


async def y_timing(conn) -> None:
    section("[Y] (4) bulkfetchui's live preview through the app: best of 3 < 2 s (limit fixed)")
    await mutate("UPDATE users SET role = 'super_admin' WHERE id = $1", U_MAIN)
    try:
        last = await eb.last_indexed_date(conn)
        body = {"banks": ["JPMorgan"], "from": "2025-01-01", "to": min(last, date(2025, 12, 31)).isoformat(),
                "kinds": ["final"], "peek": True}             # verify_bulkfetchui's REAL_GROUP / REAL_YEAR body
        await api(SUB_MAIN, "POST", PREVIEW_URL, body)       # warm the app pool
        rows, loads = [], []
        for rnd in range(LOAD_ROUNDS):
            rtt = []
            for _ in range(10):
                t = time.perf_counter()
                await conn.fetchval("SELECT 1")
                rtt.append((time.perf_counter() - t) * 1000)
            rows = []
            for _ in range(3):
                n0, t0 = STMTS["n"], time.perf_counter()
                code, p, _ = await api(SUB_MAIN, "POST", PREVIEW_URL, body)
                rows.append({"e2e": (time.perf_counter() - t0) * 1000, "server": p.get("elapsed_ms"), "code": code,
                             "stmts": STMTS["n"] - n0, "phases": dict(PHASES)})
            load = load_per_cpu()
            loads.append(round(load, 2))
            best = min(r["e2e"] for r in rows)
            print(f"    round {rnd + 1}: best e2e {best:.0f} ms; RTT median {statistics.median(rtt):.0f} ms; "
                  f"1-min load per CPU {load:.2f}")
            if best < LIMIT_SECONDS * 1000 or load <= LOAD_PER_CPU:
                break
            await asyncio.sleep(LOAD_WAIT_SECONDS)
        table = "; ".join(f"[e2e {r['e2e']:.0f} ms, server {r['server']} ms, HTTP {r['code']}, {r['stmts']} stmts: "
                          + ", ".join(f"{k} {v[0]:.0f} ms/{v[1]}" for k, v in r["phases"].items()) + "]" for r in rows)
        print(f"    timings: {table}")
        best = min(r["e2e"] for r in rows)
        pr = [r["phases"].get("principal", (0, 99))[1] for r in rows]
        check(all(r["code"] == 200 for r in rows), "every timed preview returned 200", table)
        check(max(pr) <= PRINCIPAL_MAX_STATEMENTS, f"middleware principal <= {PRINCIPAL_MAX_STATEMENTS} statements "
              "(11 before)", f"{pr}")
        check(best < LIMIT_SECONDS * 1000, f"end to end best of 3 < {LIMIT_SECONDS:g} s",
              f"best {best:.0f} ms; load by round {loads}")
    finally:
        await mutate("UPDATE users SET role = 'member' WHERE id = $1", U_MAIN)


def y_bulkfetchui_full(worker_ok: bool) -> None:
    section("[Y] (5) verify_bulkfetchui.py in full → 0 FAIL")
    if "--skip-bulkfetchui" in sys.argv or os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] verify_bulkfetchui.py full run not started (--skip-bulkfetchui / VERIFY_NESTED=1)")
        return
    if not worker_ok:
        check(False, "verify_bulkfetchui.py full run", "not started: a bulk worker is heartbeating (see (1))")
        return
    t0 = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-u", str(HERE / "verify_bulkfetchui.py")], cwd=str(REPO),
                           capture_output=True, text=True, timeout=SUB_TIMEOUT, stdin=subprocess.DEVNULL,
                           env=os.environ | {"VERIFY_NESTED": "1"})
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = 124, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), \
            f"timeout after {SUB_TIMEOUT}s"
    m = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    timing = [ln.strip() for ln in out.splitlines() if "live preview" in ln or ln.strip().startswith("round ")]
    print(f"[INFO] verify_bulkfetchui.py: exit {code}, {time.monotonic() - t0:.0f} s, {m[-1] if m else 'no TOTAL'}")
    for ln in timing:
        print(f"    {ln}")
    check(code == 0 and bool(m) and m[-1][1] == "0", "verify_bulkfetchui.py full run: exit 0, 0 FAIL "
          "(includes 'live preview returns under 2 s')", f"exit {code}; {fails[:5]}; {(out + err)[-600:]}")


async def run() -> None:
    print(f"[INFO] verify_previewperffix1 — base {BEFORE_REF}; Doppler hydration: {HYDRATE_ERROR or 'ok'}")
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working database DSN", how)
        return
    conn = await connect(dsn)
    worker_ok = False
    before = None
    try:
        await teardown()
        before = await counts(conn)
        worker_ok = await y_worker(conn)
        await y_equivalence(conn)
        instrument()
        await seed()
        await y_app(conn)
        if worker_ok:
            await y_timing(conn)
        else:
            print("[INFO] (4) not run beside a live worker")
    finally:
        section("[Y] Teardown: zero fixture rows; counted tables at their pre-test counts")
        try:
            await teardown()
            left = await leftovers()
            check(left == 0, "zero fixture rows remain", f"{left}")
            if before is not None:
                after = await counts(conn)
                check(after == before, "users/organizations/roles/user_roles/user_identities/role_permissions "
                      "counts equal before and after", f"{before} -> {after}")
        finally:
            await conn.close()
    y_bulkfetchui_full(worker_ok)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify_previewperffix1 raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    sys.exit(0 if _n_fail == 0 else 1)
