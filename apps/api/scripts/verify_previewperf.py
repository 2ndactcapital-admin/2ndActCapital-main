"""verify_previewperf — PREVIEWPERF: the EDGAR bulk-download live preview is under
bulkfetchui's 2 s limit again, with the same answers. The limit is NOT raised.

WRITTEN, NOT RUN by the sprint. The operator runs (laptop bulk worker STOPPED):

    doppler run -p hollisworks -c prd -- apps/api/venv/bin/python -u apps/api/scripts/verify_previewperf.py \
        2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^\\[SKIP\\]|^TOTAL"

Pass/fail only, no prompts, no network beyond Doppler hydration (HTTPS) and the
database. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure.

THE CAUSE (measured 2026-11-03, docs/PROJECT_STATUS.md "PREVIEWPERF"): not the
SQL — the preview's SQL is ~25 ms in total (the scope count is a bitmap scan of
edgar_index_filings_selected_count_idx, ~20 ms). It was ROUND TRIPS: preview()
ran 28 statements (7 platform_scope blocks: savepoint, set_config, prepare +
execute, release — one per lookup and one per institution), the route's gate
re-loaded the Principal the middleware already had (5 more), at 23–86 ms per
round trip from the laptop to Supabase. THE FIX: one snapshot statement + one
all-institution count inside ONE platform_scope (5 statements), and the gate
reads the cached Principal (0). NO INDEX was added — none would help: the one
scan is already index-served and its OR branches rule out an index-only plan.

WHAT IT PROVES
  (1) no laptop bulk worker is heartbeating (else ONE [FAIL] naming it, and
      nothing timed);
  (2) the index the preview's count relies on (selected_count_idx) is valid and
      ready and the count's plan uses it; this sprint created no index (none
      named *previewperf*);
  (3) the preview for JPMorgan 2025 and two other institution-years, through
      the real app, equals a direct SQL count (total, per year, per filer);
      AND every institution × every indexed year (plus all institutions
      together, and a preliminaries / peek-off variant) is IDENTICAL, minus
      elapsed_ms, to the PRE-SPRINT code — services/edgar_bulk.py and
      edgar_bulk_queue.py loaded from BEFORE_REF with `git show`, run side by
      side on the same live rows;
  (4) best of 3 (warm), server-side elapsed_ms AND end to end both < 2 s, with
      bulkfetchui's load rule (VERIFY_RULES rule 7); preview() issues at most
      PREVIEW_MAX_STATEMENTS statements and the gate none. The caller is a real
      admitted super admin through the middleware (AUTHZ_TEST_MODE UUID stub);
      services.users.touch_last_login is swapped for a read-only statement of
      the same shape so the real users row is not stamped — proven by its
      last_login_at before/after;
  (5) verify_bulkfetchui.py in full as a subprocess (VERIFY_NESTED=1, 1800 s)
      → exit 0, 0 FAIL. With VERIFY_NESTED=1: one '[SKIP] nested run'.

--self-checks-only: this sprint's own diff (BEFORE_REF → its end) touches only
the listed files and holds no DDL (stdlib + git). --cleanup-only: PREVIEWPERF
seeds nothing; prints CLEANUP_SECONDS 0, LEFTOVERS: 0, TOTAL: 1 PASS.
"""

from __future__ import annotations

import os

os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import pathlib
import re
import subprocess
import sys
import time
import types
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

import _verify_cleanup as VC  # noqa: E402,F401  (every regression-set script imports it; PREVIEWPERF has no fixtures)
import _verify_self as VS  # noqa: E402

SPRINT_KEY = "previewperf"
BEFORE_REF = "d9afcea"   # previewperf-work base: main @ "merge vhk2 fix 1"
OWN_FILES = {
    "apps/api/services/edgar_bulk.py", "apps/api/services/edgar_bulk_queue.py",
    "apps/api/routers/edgar_pipeline_admin.py", "apps/api/scripts/verify_previewperf.py",
    "docs/PROJECT_STATUS.md", "scripts/regression_set.txt", "sprint_prompts/previewperf.structural.md",
}


def self_checks(check, find) -> None:
    code, out, err = VS.own_diff(BEFORE_REF, SPRINT_KEY, args=("--name-only",))
    files = {f for f in out.split() if f}
    extra = sorted(f for f in files if f not in OWN_FILES and not f.startswith("Claude outputs/"))
    check(code == 0 and not extra, "PREVIEWPERF's own diff touches only the preview code, its verify, the status "
          "doc and the regression list", f"exit {code} {err.strip()[:200]}; unexpected {extra}")
    code, diff, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY)
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    ddl = [ln for ln in added if re.search(r"\b(CREATE|DROP|ALTER)\s+(UNIQUE\s+)?(INDEX|TABLE)\b", ln, re.I)
           and "verify_previewperf" not in ln and "re.search" not in ln]
    check(code == 0 and not ddl, "no DDL in the sprint's own diff (the fix is a rewrite, not an index)", f"{ddl[:3]}")
    end_src = VS.own_text(SPRINT_KEY, "apps/api/services/edgar_bulk_queue.py") or ""
    check("PREVIEW_SNAPSHOT_SQL" in end_src and "count_scope(conn, scope)" in end_src,
          "at the sprint's end, preview() reads one snapshot and one all-institution count",
          "PREVIEW_SNAPSHOT_SQL / count_scope missing from edgar_bulk_queue.py at the end ref")


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

if __name__ == "__main__" and "--cleanup-only" in sys.argv:
    print("[INFO] PREVIEWPERF seeds no fixtures: nothing to clean")
    print("CLEANUP_SECONDS: 0.0")
    print("LEFTOVERS: 0")
    print("[PASS] --cleanup-only: zero fixture rows remain (none are ever created)")
    print("\nTOTAL: 1 PASS, 0 FAIL")
    sys.exit(0)

from _db_connect import admin_dsn, connect, hydrate  # noqa: E402

HYDRATE_ERROR = hydrate()

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import main  # noqa: E402
import routers.edgar_pipeline_admin as epa  # noqa: E402
from services import edgar_bulk as eb  # noqa: E402
from services import edgar_bulk_queue as bulkq  # noqa: E402
from services import users as su  # noqa: E402

PREVIEW_URL = "/api/v1/admin/edgar/bulk/preview"
HEADERS = {"Authorization": "Bearer verify-token"}
LIMIT_SECONDS = 2.0                 # bulkfetchui's limit — fixed, never raised
LOAD_PER_CPU = 0.75                 # bulkfetchui's load rule
LOAD_WAIT_SECONDS = 30
LOAD_ROUNDS = 5
PREVIEW_MAX_STATEMENTS = 6          # 5 today: savepoint, set_config, snapshot, count, release
SUB_TIMEOUT = 1800
TIMED_CASES = [("JPMorgan", 2025), ("Citigroup", 2024), ("Goldman Sachs", 2023)]

_n_pass = 0
_n_fail = 0
STMTS = {"n": 0}
PHASES: dict[str, tuple[float, int]] = {}


def check(passed: bool, label: str, detail: str = "") -> bool:
    global _n_pass, _n_fail
    ok = bool(passed)
    if ok:
        _n_pass += 1
        print(f"[PASS] {label}", flush=True)
    else:
        _n_fail += 1
        print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f" — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def tail(text: str, n: int = 600) -> str:
    return text[-n:].replace("\n", " | ")


def load_per_cpu() -> float:
    try:
        return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except OSError:
        return 0.0


async def scoped(conn, sql: str, *args, many: bool = True):
    """A read under the platform carve-out, in its own transaction (SET LOCAL)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await (conn.fetch if many else conn.fetchval)(sql, *args)


# ── instrumentation (statement counter, phase timers, read-only login touch) ──

def instrument() -> None:
    for name in ("execute", "fetch", "fetchrow", "fetchval"):
        orig = getattr(asyncpg.Connection, name)

        def wrap(orig):
            async def counted(self, *a, **k):
                STMTS["n"] += 1
                return await orig(self, *a, **k)
            return counted
        setattr(asyncpg.Connection, name, wrap(orig))

    def timed(label, fn):
        async def run(*a, **k):
            t, n0 = time.perf_counter(), STMTS["n"]
            try:
                return await fn(*a, **k)
            finally:
                PHASES[label] = ((time.perf_counter() - t) * 1000, STMTS["n"] - n0)
        return run

    async def touch_readonly(conn, user_id):
        # Same shape as the real throttled UPDATE (one statement, one uuid argument); writes nothing.
        await conn.execute("SELECT 1 WHERE $1::uuid IS NOT NULL", user_id)
        return False

    su.touch_last_login = touch_readonly
    su.resolve_request_principal = timed("middleware principal", su.resolve_request_principal)
    epa._require_super_admin = timed("route gate", epa._require_super_admin)
    bulkq.preview = timed("preview()", bulkq.preview)


async def api(sub: str, org_id: str, body: dict):
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": org_id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify") as client:
        res = await client.post(PREVIEW_URL, json=body, headers=HEADERS)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed, len(res.content)


def body_for(group: str, year: int, last: date) -> dict:
    return {"banks": [group], "from": f"{year}-01-01", "to": min(date(year, 12, 31), last).isoformat(),
            "kinds": ["final"], "peek": True}


# ── the pre-sprint code, loaded from BEFORE_REF ───────────────────────────────

def load_old_modules():
    """services/edgar_bulk.py and edgar_bulk_queue.py exactly as BEFORE_REF had them, as separate modules (the
    old queue module bound to the OLD edgar_bulk). Returns the old queue module, or raises."""
    srcs = {}
    for name in ("edgar_bulk", "edgar_bulk_queue"):
        code, out, err = VS.git("show", f"{BEFORE_REF}:apps/api/services/{name}.py")
        if code != 0:
            raise RuntimeError(f"git show {BEFORE_REF}:{name}.py failed: {err.strip()[:200]}")
        srcs[name] = out
    old_eb = types.ModuleType("services.edgar_bulk_previewperf_before")
    old_eb.__file__ = str(API_DIR / "services" / "edgar_bulk.py")
    sys.modules[old_eb.__name__] = old_eb
    exec(compile(srcs["edgar_bulk"], f"{BEFORE_REF}:edgar_bulk.py", "exec"), old_eb.__dict__)
    q_src = srcs["edgar_bulk_queue"].replace("from services import edgar_bulk as eb\n",
                                             f"import {old_eb.__name__} as eb\n", 1)
    if q_src == srcs["edgar_bulk_queue"]:
        raise RuntimeError("the old edgar_bulk_queue.py no longer imports edgar_bulk the expected way")
    old_q = types.ModuleType("services.edgar_bulk_queue_previewperf_before")
    old_q.__file__ = str(API_DIR / "services" / "edgar_bulk_queue.py")
    sys.modules[old_q.__name__] = old_q
    exec(compile(q_src, f"{BEFORE_REF}:edgar_bulk_queue.py", "exec"), old_q.__dict__)
    return old_q


def strip(p: dict) -> str:
    return json.dumps({k: v for k, v in p.items() if k != "elapsed_ms"}, sort_keys=True, default=str)


# ── (1) worker ───────────────────────────────────────────────────────────────

async def y_worker(conn) -> bool:
    section("[Y] (1) No laptop bulk worker is heartbeating")
    rows = await scoped(conn, """SELECT worker_id, host, heartbeat_at FROM portfolio.edgar_fetch_workers
                                  WHERE target = 'laptop' AND stopped_at IS NULL
                                    AND heartbeat_at >= now() - interval '120 seconds'""")
    names = [f"{r['worker_id']} on {r['host']} (heartbeat {r['heartbeat_at']:%H:%M:%S})" for r in rows]
    return check(not rows, "no laptop bulk worker heartbeating in the last 120 s (its claims would dirty the "
                 "visibility map and load the database during the timings)",
                 f"stop it first (tmux attach -t bulkworker, Ctrl-C): {names}")


# ── (2) index ────────────────────────────────────────────────────────────────

async def y_index(conn) -> None:
    section("[Y] (2) The count's index is valid and used; this sprint created no index")
    rows = await scoped(conn, """SELECT c.relname, i.indisvalid, i.indisready FROM pg_index i
                                   JOIN pg_class c ON c.oid = i.indexrelid
                                  WHERE i.indrelid = 'portfolio.edgar_index_filings'::regclass""")
    by = {r["relname"]: r for r in rows}
    sel = by.get("edgar_index_filings_selected_count_idx")
    check(sel is not None and sel["indisvalid"] and sel["indisready"],
          "edgar_index_filings_selected_count_idx exists, valid and ready", f"{dict(sel) if sel else 'absent'}")
    bad = [r["relname"] for r in rows if not (r["indisvalid"] and r["indisready"])]
    check(not bad, "every index on edgar_index_filings is valid and ready", f"invalid: {bad}")
    mine = [n for n in by if "previewperf" in n]
    check(not mine, "no index was created by PREVIEWPERF (the fix is a round-trip rewrite)", f"{mine}")
    src = await eb.load_source(conn, eb.DEFAULT_SOURCE)
    cat = await eb.institution_catalog(conn, src)
    scope = eb.Scope(src, [cat["JPMorgan"]], date(2025, 1, 1), date(2025, 12, 31))
    plan = await scoped(conn, f"""EXPLAIN SELECT f.primary_issuer_cik, extract(year FROM f.filing_date)::int, count(*)
                                    FROM portfolio.edgar_index_filings f WHERE {eb._SCOPE_WHERE} GROUP BY 1, 2""",
                        *eb._scope_args(scope, cat["JPMorgan"].ciks))
    text = "\n".join(r[0] for r in plan)
    check("edgar_index_filings_selected_count_idx" in text, "the JPMorgan 2025 scope count is served by "
          "selected_count_idx", tail(text, 500))


# ── (3) answers ──────────────────────────────────────────────────────────────

async def direct_counts(conn, src, ciks: list[str], d0: date, d1: date):
    rows = await scoped(conn, """SELECT primary_issuer_cik AS cik, extract(year FROM filing_date)::int AS y,
                                        count(*)::int AS n
                                   FROM portfolio.edgar_index_filings
                                  WHERE primary_issuer_cik = ANY($1::text[]) AND form_type = ANY($2::text[])
                                    AND pipeline_status = 'selected' AND filing_date BETWEEN $3 AND $4
                                  GROUP BY 1, 2""", ciks, list(src.form_types), d0, d1)
    by_cik: dict[str, int] = {c: 0 for c in ciks}
    by_year: dict[str, int] = {}
    for r in rows:
        by_cik[r["cik"]] += r["n"]
        by_year[str(r["y"])] = by_year.get(str(r["y"]), 0) + r["n"]
    return sum(by_cik.values()), by_year, by_cik


async def y_answers_vs_sql(conn, user) -> None:
    section("[Y] (3a) The preview through the app equals a direct SQL count")
    src = await eb.load_source(conn, eb.DEFAULT_SOURCE)
    cat = await eb.institution_catalog(conn, src)
    last = await eb.last_indexed_date(conn)
    for group, year in TIMED_CASES:
        if group not in cat:
            find(f"{group} is not in the catalog today", "skipping its SQL equality (JPMorgan is asserted)")
            continue
        body = body_for(group, year, last)
        code, p, _ = await api(user["sub"], user["org"], body)
        inst = next((i for i in p.get("institutions") or [] if i["name"] == group), {})
        api_cik = {f["cik"]: f["count"] for f in inst.get("filers") or []}
        n, by_year, by_cik = await direct_counts(conn, src, cat[group].ciks, date.fromisoformat(body["from"]),
                                                 date.fromisoformat(body["to"]))
        if n == 0:
            find(f"no real selected {group} {year} filings today", "equality still asserted (0 = 0)")
        check(code == 200 and p.get("total") == n and inst.get("years") == by_year and api_cik == by_cik,
              f"preview {group} {year} equals SQL: total, per year, per filer",
              f"HTTP {code}: total {p.get('total')} vs {n}; years {inst.get('years')} vs {by_year}; "
              f"filers {api_cik} vs {by_cik}")


async def y_answers_vs_before(conn) -> None:
    section(f"[Y] (3b) Every institution-year: the new preview is IDENTICAL to the code at {BEFORE_REF}")
    try:
        old_q = load_old_modules()
    except Exception as exc:  # noqa: BLE001
        check(False, f"the pre-sprint modules load from {BEFORE_REF}", f"{type(exc).__name__}: {exc}")
        return
    src = await eb.load_source(conn, eb.DEFAULT_SOURCE)
    groups = sorted(await eb.institution_catalog(conn, src))
    span = await scoped(conn, "SELECT min(filing_date), max(filing_date) FROM portfolio.edgar_index_filings")
    first, last = span[0][0], span[0][1]
    cases = [{"banks": [g], "from": f"{y}-01-01", "to": min(date(y, 12, 31), last).isoformat(), "kinds": ["final"],
              "peek": True} for g in groups for y in range(first.year, last.year + 1)]
    cases += [{"banks": groups, "from": first.isoformat(), "to": last.isoformat()},
              {"banks": groups, "from": f"{last.year - 1}-01-01", "to": f"{last.year - 1}-12-31"},
              {"banks": groups[:2], "from": f"{last.year - 1}-01-01", "to": last.isoformat(),
               "kinds": ["final", "preliminary"]},
              {"banks": groups[-2:], "from": f"{last.year - 2}-01-01", "to": f"{last.year - 1}-06-30", "peek": False,
               "max_rps": 4, "concurrency": 2}]
    diffs, nonzero, t_old, t_new = [], 0, 0.0, 0.0
    for raw in cases:
        for attempt in range(2):             # a live index refresh between the two reads gets one retry
            t = time.perf_counter()
            before = await old_q.preview(conn, dict(raw))
            t_old += time.perf_counter() - t
            t = time.perf_counter()
            after = await bulkq.preview(conn, dict(raw))
            t_new += time.perf_counter() - t
            if strip(before) == strip(after):
                break
        else:
            diffs.append((raw, strip(before)[:300], strip(after)[:300]))
        nonzero += 1 if after.get("total") else 0
    print(f"    {len(cases)} previews ({len(groups)} institutions × {first.year}–{last.year} + 4 multi-institution "
          f"/ option variants), {nonzero} non-empty; summed time old {t_old:.1f} s, new {t_new:.1f} s")
    check(not diffs and nonzero > 0, f"all {len(cases)} previews identical to {BEFORE_REF}'s code (JSON minus "
          "elapsed_ms) — totals, years, filers, estimates, params, last indexed date", f"{len(diffs)} differ: {diffs[:2]}")


# ── (4) timing ───────────────────────────────────────────────────────────────

async def y_timing(conn, user) -> None:
    section("[Y] (4) Best of 3, warm: server-side and end to end under 2 s (limit fixed)")
    last = await eb.last_indexed_date(conn)
    body = body_for("JPMorgan", 2025, last)
    stamp_before = await scoped(conn, "SELECT last_login_at FROM users WHERE id = $1::uuid", user["sub"], many=False)
    await api(user["sub"], user["org"], body)                          # warm the app pool
    rows, loads = [], []
    for rnd in range(LOAD_ROUNDS):
        rows = []
        for _ in range(3):
            PHASES.clear()
            n0, t0 = STMTS["n"], time.perf_counter()
            code, p, size = await api(user["sub"], user["org"], body)
            rows.append({"e2e": (time.perf_counter() - t0) * 1000, "server": p.get("elapsed_ms"), "code": code,
                         "total": p.get("total"), "stmts": STMTS["n"] - n0, "bytes": size, "phases": dict(PHASES)})
        load = load_per_cpu()
        loads.append(round(load, 2))
        best_e2e = min(r["e2e"] for r in rows)
        best_srv = min(r["server"] or 1e9 for r in rows)
        print(f"    round {rnd + 1}: best e2e {best_e2e:.0f} ms, server {best_srv} ms; 1-min load per CPU {load:.2f}")
        if (best_e2e < LIMIT_SECONDS * 1000 and best_srv < LIMIT_SECONDS * 1000) or load <= LOAD_PER_CPU:
            break
        await asyncio.sleep(LOAD_WAIT_SECONDS)
    table = "; ".join(
        f"[e2e {r['e2e']:.0f} ms, server {r['server']} ms, HTTP {r['code']}, {r['stmts']} stmts, {r['bytes']} B: "
        + ", ".join(f"{k} {v[0]:.0f} ms/{v[1]} stmts" for k, v in r["phases"].items()) + "]" for r in rows)
    print(f"    timings: {table}")
    best_e2e = min(r["e2e"] for r in rows)
    best_srv = min(r["server"] or 1e9 for r in rows)
    check(all(r["code"] == 200 for r in rows), "every timed preview returned 200", table)
    check(best_srv < LIMIT_SECONDS * 1000, f"server-side elapsed_ms best of 3 < {LIMIT_SECONDS:g} s",
          f"best {best_srv} ms; load by round {loads}; {table}")
    check(best_e2e < LIMIT_SECONDS * 1000, f"end to end best of 3 < {LIMIT_SECONDS:g} s",
          f"best {best_e2e:.0f} ms; load by round {loads}; {table}")
    pv = [r["phases"].get("preview()", (0, 99))[1] for r in rows]
    gate = [r["phases"].get("route gate", (0, 99))[1] for r in rows]
    check(max(pv) <= PREVIEW_MAX_STATEMENTS, f"preview() issues at most {PREVIEW_MAX_STATEMENTS} statements "
          "(28 before PREVIEWPERF)", f"{pv}; {table}")
    check(max(gate) == 0, "the route gate reads the cached Principal: 0 statements (5 before)", f"{gate}; {table}")
    stamp_after = await scoped(conn, "SELECT last_login_at FROM users WHERE id = $1::uuid", user["sub"], many=False)
    check(stamp_before == stamp_after, "the real super admin's last_login_at is unchanged (read-only timing)",
          f"{stamp_before} -> {stamp_after}")


# ── (5) full bulkfetchui ─────────────────────────────────────────────────────

def y_bulkfetchui_full(worker_ok: bool) -> None:
    section("[Y] (5) verify_bulkfetchui.py in full → 0 FAIL")
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run — verify_bulkfetchui.py full run not started (VERIFY_NESTED=1)")
        return
    if not worker_ok:
        check(False, "verify_bulkfetchui.py full run", "not started: a laptop bulk worker is heartbeating (see (1))")
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
          "(includes 'live preview returns under 2 s')", f"exit {code}; {fails[:5]}; {timing}; {tail(out + err)}")


async def run() -> None:
    print(f"[INFO] verify_previewperf — base {BEFORE_REF}; Doppler hydration: {HYDRATE_ERROR or 'ok'}")
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working database DSN", how)
        return
    conn = await connect(dsn)
    try:
        print(f"[INFO] connected via {how} as {await conn.fetchval('SELECT current_user')}")
        worker_ok = await y_worker(conn)
        await y_index(conn)
        instrument()
        supers = await scoped(conn, """SELECT id::text AS sub, org_id::text AS org FROM users
                                        WHERE role = 'super_admin' AND status = 'active' AND is_active
                                        ORDER BY created_at, id""")
        user = dict(supers[0]) if supers else None
        if user is None:
            find("no admitted real super admin today", "the app-level checks (3a) and (4) need one; (5)'s fixture "
                 "super admin still times the preview")
        if worker_ok:
            if user:
                await y_answers_vs_sql(conn, user)
            await y_answers_vs_before(conn)
            if user:
                await y_timing(conn, user)
        else:
            print("[INFO] (3)–(5) not run beside a live worker")
    finally:
        await conn.close()
    y_bulkfetchui_full(worker_ok)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        check(False, "verify_previewperf raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    sys.exit(0 if _n_fail == 0 else 1)
