"""verify_dailyops1 — the two production defects that stopped the platform's daily work.

(A) The Market Indicators page showed "Market data could not be loaded." for everyone: GET /market/catalog listed the
    whole security master (~16,000 rows) and palette.ramp raised past ~300 shades of one colour. Fixed: the catalog
    lists only SELECTABLE securities, GET /market/securities/search finds the rest, palette.ramp never raises.
(B) The nightly AI extraction run stopped every night since 2026-11-05: the "no dollar cap" sentinel 1e9 overflowed
    portfolio.note_extraction_runs.spend_cap_usd numeric(12,6). Fixed: no cap = NULL; a cap that cannot be recorded
    is refused at the start with a reason. B3 (replica_terms_extraction never ran) = NO-WORK: no fund on any
    registry. Public-data guard: the daily run refuses a non-public-data job before selecting an item.

    timeout 600 python3 apps/api/scripts/verify_dailyops1.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_dailyops1.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_dailyops1.py --section b      # seed + ONE section + teardown
    python3 apps/api/scripts/verify_dailyops1.py                              # everything + fast regressions

Sections: a palette · b catalog over a >= 10,000-row fixture master · c search · d note run with no USD cap (real
_note_read, stub transport) + a cap above 10^6 refused · e the replica task (B3) · f the REAL active budgets honoured
exactly · g the public-data guard both ways · h READ-ONLY live checks · i route matrices CURRENT · j fast regressions.

NO MODEL CALL AND NO NETWORK: ``services.note_extraction.proxy._post`` (the one network function of every ensemble
call) is a fake that answers with usage EQUAL to the call's own reservation estimate; the proxy price list is a
fixture; R2 reads return fixture HTML; every httpx transport refuses and records. REAL: the app through an ASGI
transport (middleware, Principal, gates), services.ai_daily_runs.drive (runs, leases, the budget scope), the real
note job's read path (_note_read → runner.run_notes → cascade → store), the replica reader pair, ai_budget's ledger.

FIXTURES: orgs 99000000-0000-0000-0000-0000d0a1000{1,2} (is_test, asserted); users …d0a10011–13; securities named
'DAILYOPS1 FX …'; series keys 'dailyops1fx.*'; deployments 'dailyops1-fx-*'; task keys 'dailyops1_fx_*' (registered
as public-data task keys for this process only); reference filings with accession '9999999999-26-*'. The fixture
ensemble configs COPY the live active configs' budgets exactly (B4) onto fixture deployment names, so the real
ledger (gpt-5-mini / gpt-5-nano usage today) is never touched. THE CLOCK: the budget day is the database's UTC date,
read at each check; the daily run's slice clock is a fixed far deadline; daily_tick gets an explicit noon UTC.

TEARDOWN (start and end): _verify_cleanup.cleanup on the operator DSN keyed by the fixture ids/prefixes, then every
counted table at its exact pre-test count and every real row set unchanged (count + checksum).
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "dailyops1"
BEFORE_REF = "d0f7eb4"   # main @ 'merge cpsim06' — the dailyops1-work base
SEARCH_ROUTE = ("GET", "/api/v1/market/securities/search")
REGRESSIONS = ["verify_mkt03.py", "verify_mkt03b.py", "verify_mkt04a.py", "verify_mkt04b.py", "verify_mkt04c.py",
               "verify_secmaster2.py", "verify_prices1.py", "verify_aibudget1.py"]


# ═══ pure checks (also run by --self-checks-only) ═══════════════════════════
def palette_checks(check, find):
    """(a) palette at 13 / 187 / 917 / 6,133 / 20,000 — never raises, deterministic, distinct where it fits; the
    old code reproduced raising first."""
    from services.market_data import palette as P

    old_src = VS.git_show(BEFORE_REF, "apps/api/services/market_data/palette.py")
    ns: dict = {}
    raised = None
    if old_src:
        exec(compile(old_src, "palette@" + BEFORE_REF, "exec"), ns)  # noqa: S102 — our own file at the base commit
        try:
            ns["ramp"](P.NEUTRAL, 917)
        except RuntimeError as exc:
            raised = str(exc)
    check(raised is not None and "could not find a distinct shade" in raised,
          "REPRODUCED: the base commit's palette.ramp(NEUTRAL, 917) raises RuntimeError (the catalog's crash)",
          f"{raised}")
    bases = (P.NEUTRAL, P.INDEX_SECURITY_BASE, P.NOTE_SECURITY_BASE, *P.CATEGORY_BASE.values())
    for n in (13, 187, 917, 6133, 20000):
        errs, nondet, sizes = [], [], set()
        t0 = time.monotonic()
        for b in bases:
            try:
                a1, a2 = P.ramp(b, n), P.ramp(b, n)
            except Exception as exc:  # noqa: BLE001
                errs.append(f"{b}: {type(exc).__name__}")
                continue
            if a1 != a2 or len(a1) != n or not all(re.fullmatch(r"#[0-9A-F]{6}", c) for c in a1):
                nondet.append(b)
            sizes.add(len(set(a1)))
        secs = time.monotonic() - t0
        check(not errs and not nondet, f"palette.ramp(base, {n}) never raises, returns {n} valid #RRGGBB colours and "
              "is identical on a second call, for every base colour", f"errors={errs} nondeterministic={nondet} "
              f"{secs:.2f}s")
        if n <= 187:
            check(sizes == {n}, f"palette.ramp(base, {n}) is fully DISTINCT (it fits)", f"distinct sizes={sizes}")
        else:
            check(min(sizes) >= 2 and max(sizes) <= P.CYCLE_MAX,
                  f"palette.ramp(base, {n}) cycles deterministically through at most {P.CYCLE_MAX} distinct shades "
                  "(an 8-bit colour has ~300 per hue inside the clamp)", f"distinct sizes={sorted(sizes)}")
    if old_src:
        same = all(P.ramp(b, n) == ns["ramp"](b, n) for b in bases for n in (1, 2, 13, 54, 187))
        check(same, "every size that fitted before keeps EXACTLY its old colours (13, 54, 187 … on every base)")
    # where the old code worked, the new code is identical: the largest working size per base
    fits = []
    for b in (P.NEUTRAL, P.INDEX_SECURITY_BASE, P.NOTE_SECURITY_BASE):
        try:
            fits.append(len(set(P.ramp(b, 290))) == 290)
        except Exception:  # noqa: BLE001
            fits.append(False)
    check(all(fits), "290 shades still fit distinct on the security bases (distinct is kept wherever it fits)",
          f"{fits}")


def static_checks(check, find):
    rd = (API_DIR / "services" / "ai_daily_runs.py").read_text()
    check("1e9" not in re.sub(r'""".*?"""', "", rd, flags=re.S) and "return None if not raw" in rd,
          "ai_daily_runs.spend_cap(): no sentinel number; unset = None (no cap)")
    m = re.search(r"async def drive\(.*?\n(?=async def |def )", rd, flags=re.S)
    body = m.group(0) if m else ""
    i_ref, i_cfg, i_q = body.find("public_job_refusal("), body.find("job.load_config("), body.find("queue_items(")
    check(0 < i_ref < i_cfg < i_q, "drive() refuses a non-public-data job BEFORE it loads a config or selects an item",
          f"positions {i_ref}/{i_cfg}/{i_q}")
    check("retention_classification = 'public_reference'" in rd and "NOTE_PUBLIC_FORMS" in rd,
          "the note queue keeps only public_reference EDGAR filings (explicit, not only the table's CHECK)")
    st = (API_DIR / "services" / "note_extraction" / "store.py").read_text()
    check("checked_spend_cap(spend_cap_usd)" in st and "Decimal(str(spend_cap_usd))" not in st,
          "store.create_run validates every caller's cap before the INSERT (no raw Decimal(str(cap)))")
    sql = (REPO / "docs" / "dailyops1_part1.sql").read_text()
    check("ALTER COLUMN spend_cap_usd DROP NOT NULL" in sql and "DROP TABLE" not in sql.upper().replace("DROP NOT", ""),
          "docs/dailyops1_part1.sql relaxes NOT NULL only")
    rs = (API_DIR / "services" / "market_data" / "read_service.py").read_text()
    rr = (API_DIR / "services" / "market_data" / "read_repository.py").read_text()
    cat = re.search(r"async def catalog_securities.*?return", rr, flags=re.S).group(0)
    check("LEFT JOIN" not in cat and "JOIN market_data.indicator_series s" in cat,
          "catalog_securities lists only securities priced by an ACTIVE series (INNER JOIN)")
    import ast as _ast
    refs = [f"{n}:{getattr(node, 'lineno', '?')}" for n, src in (("read_repository", rr), ("read_service", rs))
            for node in _ast.walk(_ast.parse(src))
            if any(x.split(".")[-1] == "platform_scope" for x in (
                [a.name for a in node.names] if isinstance(node, (_ast.Import, _ast.ImportFrom)) else
                [node.id] if isinstance(node, _ast.Name) else [node.attr] if isinstance(node, _ast.Attribute) else []))]
    check(not refs, "the market read modules still never import or reference platform_scope (AST; docstrings "
          "excluded) — global SELECT policies only", f"{refs}")
    check("MAX_SEARCH_RESULTS = 25" in rs and "SEARCH_MIN_CHARS = 2" in rs,
          "search limits: at least 2 characters, at most 25 results (published in vocabularies.search)")


def route_matrix_checks(check, find):
    """(i) both route matrices CURRENT, and the new route has the catalog's own gate."""
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        p = subprocess.run([sys.executable, str(HERE / script), "--check"], cwd=REPO, capture_output=True, text=True,
                           timeout=600)
        check(p.returncode == 0 and "CURRENT" in p.stdout, f"{script} --check: CURRENT",
              (p.stdout + p.stderr)[-300:])
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == {SEARCH_ROUTE} and not o.changed and not o.removed,
          "this sprint's only new route is GET /market/securities/search; no existing gate changed",
          f"{o.where()}; new={sorted(mine)} changed={o.changed[:3]} removed={o.removed[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    s, c = by.get(SEARCH_ROUTE) or {}, by.get(("GET", "/api/v1/market/catalog")) or {}
    check(bool(s) and s.get("after") == c.get("after") and s.get("expect") == c.get("expect")
          and s.get("write") is False, "the search route has exactly the catalog's gate (read-only)",
          f"search={s.get('after')}/{s.get('expect')} catalog={c.get('after')}/{c.get('expect')}")
    drift = VS.still_gated(o, [k for k in o.new])
    check(not drift, "the new route still exists today with the same gate", f"{drift[:3]}")


def node_checks(check, find):
    p = subprocess.run(["node", "--test", "tests/market/dailyops1Render.test.mjs", "tests/market/marketRoutes.test.mjs"],
                       cwd=WEB_DIR, capture_output=True, text=True, timeout=600)
    m = re.search(r"^# pass (\d+)$", p.stdout, flags=re.M)
    f = re.search(r"^# fail (\d+)$", p.stdout, flags=re.M)
    check(p.returncode == 0 and m is not None and f is not None and f.group(1) == "0" and int(m.group(1)) >= 8,
          "render tests (node --test): large master loads, unpriced search result disabled with its reason, the "
          "server's error shown, fail closed; the forward table's twelve routes",
          f"pass={m.group(1) if m else None} fail={f.group(1) if f else None} {p.stderr[-200:]}")


def self_checks(check, find):
    palette_checks(check, find)
    static_checks(check, find)
    route_matrix_checks(check, find)
    node_checks(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path.
import _verify_total as VT  # noqa: E402
import _verify_roles as VR  # noqa: E402
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import main  # noqa: E402
import services.model_catalog as MC  # noqa: E402
from services import ai_budget as AB  # noqa: E402
from services import ai_daily_runs as DR  # noqa: E402
from services import held_away as HA  # noqa: E402
from services import rbac  # noqa: E402
from services import replica_terms as RT  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import get_pool  # noqa: E402
from services.market_data import read_repository as MREPO  # noqa: E402
from services.market_data import read_service as MSVC  # noqa: E402
from services.note_extraction import documents as DOCS  # noqa: E402
from services.note_extraction import proxy as PX  # noqa: E402
from services.note_extraction import spend as SP  # noqa: E402
from services.note_extraction import store as NSTORE  # noqa: E402

TAG = "dailyops1verify"
FX = "dailyops1-fx-"           # deployments
FXT = "dailyops1_fx_"          # task keys
SEC_PREFIX = "DAILYOPS1 FX"
SERIES_PREFIX = "dailyops1fx."
ACC_PREFIX = "9999999999-26-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
N_MASTER = 10_000
N_DOCS = 3


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000d0a1{n:04d}")


ORG, ORG2 = _id(1), _id(2)
U_READ, U_READ2, U_PEND = _id(11), _id(12), _id(13)
USERS = {U_READ: ("reader", ORG, "active"), U_READ2: ("reader2", ORG2, "active"), U_PEND: ("pending", ORG, "pending")}
CFG_IDS = {k: _id(100 + i) for i, k in enumerate(("note_run", "note_m1", "note_m2", "rep_run", "rep_m1", "rep_m2"))}
T = {k: f"{FXT}{k}" for k in CFG_IDS}            # fixture task keys
DEP = {k: (f"{FX}{k}-1", f"{FX}{k}-2") for k in CFG_IDS}
T_NONPUB = f"{FXT}not_public"
FAKE_PRICE = Decimal("0.000001")

S: dict = {}
NETWORK_CALLS: list[str] = []
CALL_LOG: list[dict] = []
RES_LOG: list[tuple[str, int]] = []
_n_pass = 0
_n_fail = 0


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


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def db_today(conn) -> date:
    return await conn.fetchval(f"SELECT {AB.BUDGET_DAY_SQL}")


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    return {
        "ensemble_configs": await fingerprint(conn, "SELECT * FROM ai_ensemble_configs WHERE NOT starts_with(task_key, $1)",
                                              FXT),
        "model_catalog": await fingerprint(conn, "SELECT * FROM platform_model_catalog"),
        "ledger": await fingerprint(conn, "SELECT * FROM ai_daily_usage WHERE NOT starts_with(deployment, $1)", FX),
        "daily_runs": await fingerprint(conn, "SELECT id, task_key, status, documents_done FROM ai_daily_runs WHERE NOT "
                                              "starts_with(task_key, $1) AND (started_by IS NULL OR NOT started_by = "
                                              "ANY($2::uuid[]))", FXT, list(USERS)),
        "note_runs": await fingerprint(conn, "SELECT r.id, r.status, r.spend_cap_usd FROM portfolio.note_extraction_runs r "
                                             "WHERE r.ensemble_config_id IS NULL OR NOT r.ensemble_config_id = "
                                             "ANY($1::uuid[])", list(CFG_IDS.values())),
        "securities": await fingerprint(conn, "SELECT id, name, security_type FROM portfolio.securities_global WHERE NOT "
                                              "starts_with(name, $1)", SEC_PREFIX),
        "series": await fingerprint(conn, "SELECT id, series_key, ingest_status, security_global_id FROM "
                                          "market_data.indicator_series WHERE NOT starts_with(series_key, $1)",
                                    SERIES_PREFIX),
        "filings": await fingerprint(conn, "SELECT id, accession_number FROM portfolio.reference_filings WHERE NOT "
                                           "starts_with(accession_number, $1)", ACC_PREFIX),
        "replica_securities": await fingerprint(conn, "SELECT id FROM portfolio.replica_securities WHERE NOT org_id = "
                                                      "ANY($1::uuid[])", [ORG, ORG2]),
    }


COUNTED = {
    "public.ai_ensemble_configs": "SELECT count(*) FROM ai_ensemble_configs",
    "public.ai_daily_usage": "SELECT count(*) FROM ai_daily_usage",
    "public.ai_daily_runs": "SELECT count(*) FROM ai_daily_runs",
    "portfolio.note_extraction_runs": "SELECT count(*) FROM portfolio.note_extraction_runs",
    "portfolio.note_extraction_staging": "SELECT count(*) FROM portfolio.note_extraction_staging",
    "portfolio.note_term_readings": "SELECT count(*) FROM portfolio.note_term_readings",
    "portfolio.reference_filings": "SELECT count(*) FROM portfolio.reference_filings",
    "portfolio.securities_global": "SELECT count(*) FROM portfolio.securities_global",
    "portfolio.securities_global_identifiers": "SELECT count(*) FROM portfolio.securities_global_identifiers",
    "market_data.indicator_series": "SELECT count(*) FROM market_data.indicator_series",
    "portfolio.replica_securities": "SELECT count(*) FROM portfolio.replica_securities",
    "public.organizations": "SELECT count(*) FROM organizations",
    "public.users": "SELECT count(*) FROM users",
}


async def counts(conn) -> dict:
    """Exact table counts. The ledger/runs tables are live-written by the real cron; their counts are taken over
    every NON-fixture row so a real nightly write during the run is not mistaken for a leak."""
    out = {t: int(await platform(conn, q, fetch="fetchval")) for t, q in COUNTED.items()}
    out["public.ai_daily_usage"] = int(await platform(conn, "SELECT count(*) FROM ai_daily_usage WHERE "
                                                            "starts_with(deployment, $1)", FX, fetch="fetchval"))
    out["public.ai_daily_runs"] = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE "
                                                           "starts_with(task_key, $1) OR started_by = ANY($2::uuid[])",
                                                     FXT, list(USERS), fetch="fetchval"))
    return out


async def close_conn(c, timeout: float = 30) -> None:
    if c is None:
        return
    try:
        await asyncio.wait_for(c.close(), timeout)
    except Exception:  # noqa: BLE001
        try:
            c.terminate()
        except Exception:  # noqa: BLE001
            pass


async def close_app_pool() -> None:
    import services.database as DBM
    p, DBM._pool = DBM._pool, None
    await close_conn(p)


def _plan() -> dict:
    return {
        "public.ai_ensemble_configs": ("starts_with(task_key, $1) OR id = ANY($2::uuid[])", [FXT, list(CFG_IDS.values())]),
        "public.ai_daily_runs": ("starts_with(task_key, $1) OR started_by = ANY($2::uuid[])", [FXT, list(USERS)]),
        "public.ai_daily_usage": ("starts_with(deployment, $1)", [FX]),
        "portfolio.reference_filings": ("starts_with(accession_number, $1)", [ACC_PREFIX]),
        "market_data.indicator_series": ("starts_with(series_key, $1)", [SERIES_PREFIX]),
        "portfolio.securities_global": ("starts_with(name, $1)", [SEC_PREFIX]),
    }


_FK_CHILDREN_SQL = """
    SELECT cn.nspname AS s, cc.relname AS t, a.attname AS c
      FROM pg_constraint k JOIN pg_class cc ON cc.oid = k.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
      JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey)
     WHERE k.contype = 'f' AND k.confrelid = 'portfolio.securities_global'::regclass"""


async def drop_fixture_master(op) -> int:
    """The 10,000 fixture securities, fast. A plain DELETE fires ~40 FK checks per row (~70 ms/row, ~12 min). In ONE
    transaction: (1) this script's own child rows (identifiers, series) are deleted normally; (2) EVERY FK child column
    of securities_global (read from pg_constraint) is proven to hold NO reference to a fixture security — otherwise
    nothing is deleted here and the normal FK-checked cleanup handles it; (3) only then are the fixture rows
    (name prefix AND id) deleted with triggers off (SET LOCAL session_replication_role = replica — transaction-local,
    never a session SET under the pooler). securities_global has no user triggers of its own."""
    async with op.transaction():
        await op.execute("SET LOCAL statement_timeout = '300s'")
        await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        ids = [r["id"] for r in await op.fetch(
            "SELECT id FROM portfolio.securities_global WHERE starts_with(name, $1)", SEC_PREFIX)]
        if not ids:
            return 0
        await op.execute("DELETE FROM portfolio.securities_global_identifiers WHERE global_security_id = ANY($1::uuid[])",
                         ids)
        await op.execute("DELETE FROM market_data.indicator_series WHERE starts_with(series_key, $1) AND "
                         "security_global_id = ANY($2::uuid[])", SERIES_PREFIX, ids)
        own_trg = await op.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = "
                                    "'portfolio.securities_global'::regclass AND NOT tgisinternal")
        if int(own_trg):
            return 0
        for r in await op.fetch(_FK_CHILDREN_SQL):
            if await op.fetchval(f'SELECT EXISTS (SELECT 1 FROM "{r["s"]}"."{r["t"]}" WHERE "{r["c"]}" = '
                                 f'ANY($1::uuid[]))', ids):
                print(f"[INFO] fixture securities still referenced by {r['s']}.{r['t']}.{r['c']} — normal cleanup")
                return 0
        await op.execute("SET LOCAL session_replication_role = replica")
        status = await op.execute("DELETE FROM portfolio.securities_global WHERE id = ANY($1::uuid[]) AND "
                                  "starts_with(name, $2)", ids, SEC_PREFIX)
        return int(status.split()[-1])


async def teardown() -> list[str]:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the teardown: {how}")
    op = await connect(dsn)
    try:
        n = await drop_fixture_master(op)
        if n:
            print(f"[INFO] fixture security master removed: {n} rows (FK children proven empty first)")
        res = await VC.cleanup(op, orgs=[ORG, ORG2], users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               label="verify_dailyops1")
        return res["log"]
    finally:
        await close_conn(op)


async def teardown_retrying() -> list[str]:
    try:
        return await teardown()
    except (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
            ConnectionError) as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; retrying once")
        return await teardown()


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [[ORG, ORG2]]),
        ("SELECT count(*) FROM ai_ensemble_configs WHERE starts_with(task_key, $1) OR id = ANY($2::uuid[])",
         [FXT, list(CFG_IDS.values())]),
        ("SELECT count(*) FROM ai_daily_runs WHERE starts_with(task_key, $1) OR started_by = ANY($2::uuid[])",
         [FXT, list(USERS)]),
        ("SELECT count(*) FROM ai_daily_usage WHERE starts_with(deployment, $1)", [FX]),
        ("SELECT count(*) FROM portfolio.reference_filings WHERE starts_with(accession_number, $1)", [ACC_PREFIX]),
        ("SELECT count(*) FROM market_data.indicator_series WHERE starts_with(series_key, $1)", [SERIES_PREFIX]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE starts_with(name, $1)", [SEC_PREFIX]),
        ("SELECT count(*) FROM portfolio.note_extraction_runs WHERE ensemble_config_id = ANY($1::uuid[])",
         [list(CFG_IDS.values())]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ App access + stubs ═════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def sub_of(uid) -> str:
    return f"auth0|{TAG}-{USERS[uid][0]}"


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][1])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, *, params=None, timeout=120.0):
    headers = {"User-Agent": "verify_dailyops1"}
    if uid is not None:
        headers["Authorization"] = f"Bearer {token_for(uid)}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        t0 = time.monotonic()
        res = await client.request(method, path, headers=headers, params=params)
        secs = time.monotonic() - t0
    try:
        body = json.loads(res.content.decode("utf-8") or "{}")
    except ValueError:
        body = {"raw": res.content[:300].decode("utf-8", "replace")}
    return res.status_code, body, secs, res.content


def _usage_of(body: dict) -> tuple[int, int]:
    """The stub's usage = the call's own reservation estimate: ceil(prompt chars / 4) + max_tokens."""
    mt = AB.body_max_tokens(body) or AB.default_max_tokens()
    return (AB.prompt_chars_of(body.get("messages")) + 3) // 4, mt


async def fake_post(path: str, body: dict, timeout: float):
    model = body.get("model")
    tin, tout = _usage_of(body)
    CALL_LOG.append({"path": path, "model": model, "in": tin, "out": tout})
    resp = {"model": model, "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": tin, "completion_tokens": tout}}
    return PX.ProxyResponse(200, resp, json.dumps(resp), {"x-litellm-attempted-fallbacks": "0"}, 3)


def fake_catalog(*names) -> dict:
    return {n: PX.Deployment(name=n, deployment_id=None, upstream=f"fixture/{n}", price_key=n,
                             input_cost_per_token=float(FAKE_PRICE), output_cost_per_token=float(FAKE_PRICE),
                             cache_read_cost_per_token=None, supports_response_schema=False, max_input_tokens=None)
            for n in names}


ALL_DEPS = [d for pair in DEP.values() for d in pair]
FIXTURE_HTML = ("<html><body><p>Pricing supplement. DAILYOPS1 verify fixture. The notes are linked to the S&amp;P 500 "
                "Index. Pricing date: October 1, 2026. Maturity date: October 1, 2027. Principal amount: $1,000 "
                "per note.</p></body></html>").encode()


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile
    PX._post = fake_post
    PX.deployment_catalog = lambda: fake_catalog(*ALL_DEPS)

    def fake_download(r2_key: str, bucket: str) -> bytes:
        if not str(r2_key).startswith("dailyops1-fx/"):
            NETWORK_CALLS.append(f"r2:{r2_key}")
            raise AssertionError(f"verify_dailyops1: a real R2 read was attempted: {r2_key}")
        return FIXTURE_HTML

    DOCS._download = fake_download
    orig_reserve = AB.reserve

    async def logging_reserve(pool, items, *, task_key=None):
        out = await orig_reserve(pool, items, task_key=task_key)
        for dep, toks, _d, _l in items:
            RES_LOG.append((dep, int(toks)))
        return out

    AB.reserve = logging_reserve
    # The fixture daily jobs stand in for the two public-data jobs; T_NONPUB deliberately does NOT.
    MC.PUBLIC_DATA_TASK_KEYS = frozenset({*MC.PUBLIC_DATA_TASK_KEYS, *T.values()})

    async def refuse_async(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_dailyops1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_async


# ═══ Seed ═══════════════════════════════════════════════════════════════════
async def live_active_configs(conn) -> dict:
    rows = await platform(conn, "SELECT * FROM ai_ensemble_configs WHERE is_active AND task_key = ANY($1::text[])",
                          ["note_terms_extraction", "replica_terms_extraction", "statement_positions"])
    return {r["task_key"]: dict(r) for r in rows}


async def seed(conn) -> None:
    live = await live_active_configs(conn)
    S["live"] = live
    ok_live = all(k in live for k in ("note_terms_extraction", "replica_terms_extraction"))
    check(ok_live, "the live active configs for both public-data jobs were read (their budgets are copied, not typed)",
          f"{sorted(live)}")
    if not ok_live:
        return
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG, "A"), (ORG2, "B")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"DAILYOPS1 Verify Org {nm}", f"{TAG}-org-{nm.lower()}")
        for uid, (nm, org, status) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, 'org_admin', $6, true, true)", uid, org, f"{TAG}-{nm}@test.local",
                f"DAILYOPS1 {nm}", sub_of(uid), status)
            await rbac.grant_org_admin(conn, uid, org)
        # ── the fixture security master: N_MASTER rows, three priced by an active fixture series ──
        await conn.execute(
            """INSERT INTO portfolio.securities_global (name, security_type)
               SELECT $1 || ' SEC ' || lpad(g::text, 5, '0'),
                      CASE WHEN g % 10 < 6 THEN 'equity' WHEN g % 10 < 9 THEN 'fund' ELSE 'other' END
                 FROM generate_series(1, $2) g""", SEC_PREFIX, N_MASTER)
        priced = await conn.fetch(
            "SELECT id, security_type FROM portfolio.securities_global WHERE name = ANY($1::text[]) ORDER BY name",
            [f"{SEC_PREFIX} SEC 00010", f"{SEC_PREFIX} SEC 00020", f"{SEC_PREFIX} SEC 00006"])
        S["priced"] = {str(r["id"]): f"{SERIES_PREFIX}p{i}" for i, r in enumerate(priced)}
        for i, r in enumerate(priced):
            await conn.execute(
                """INSERT INTO market_data.indicator_series (series_key, name, category, region, frequency,
                       default_transform, cost_tier, license_class, source_provider, source_code, notes, sort_order,
                       ingest_status, security_global_id)
                   VALUES ($1, $2, 'Equities', 'US', 'daily', 'rebase_100', 'free', 'public_domain', 'verifyfake',
                           $3, 'dailyops1 verify fixture', $4, 'active', $5)""",
                f"{SERIES_PREFIX}p{i}", f"DAILYOPS1 fixture series {i}", f"VDO1{i}", -5000 + i, r["id"])
        unpriced = await conn.fetchval("SELECT id FROM portfolio.securities_global WHERE name = $1",
                                       f"{SEC_PREFIX} SEC 00001")
        S["unpriced"] = str(unpriced)
        await conn.execute(
            "INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value, is_primary) "
            "VALUES ($1, 'ticker', 'ZZDOPSU', true), ($2, 'ticker', 'ZZDOPSP', true)", unpriced, priced[0]["id"])
        S["priced_ticker_id"] = str(priced[0]["id"])
        # ── fixture reference filings (the note job's documents; NOT in the EDGAR manifest, so no real queue sees them)
        S["filings"] = []
        for i in range(1, N_DOCS * 3 + 1):
            fid = await conn.fetchval(
                """INSERT INTO portfolio.reference_filings (cik, filer_name, form_type, accession_number, filing_date,
                       primary_document, source_url, r2_key, extraction_status, content_encoding)
                   VALUES ('0', 'DAILYOPS1 FX ISSUER', '424B2', $1, CURRENT_DATE, $2, $3, $4, 'fetched', 'identity')
                   RETURNING id::text""",
                f"{ACC_PREFIX}{i:06d}", f"dailyops1-fx-{i}.htm",
                f"https://www.sec.gov/Archives/edgar/data/0/dailyops1-fx-{i}.htm", f"dailyops1-fx/{i}.htm")
            S["filings"].append(fid)
        # ── fixture ensemble configs: the LIVE budgets, fixture deployment names ──
        for k, cid in CFG_IDS.items():
            src = live["note_terms_extraction" if k.startswith("note") else "replica_terms_extraction"]
            m1, m2 = DEP[k]
            await conn.execute(
                """INSERT INTO ai_ensemble_configs (id, task_key, model_1, model_1_version, model_2, model_2_version,
                       system_one_model, system_one_model_version, is_active, activated_at, notes, model_1_budget_kind,
                       model_1_budget_value, model_2_budget_kind, model_2_budget_value)
                   VALUES ($1, $2, $3, 'fx', $4, 'fx', $5, 'fx', true, now(), 'verify_dailyops1 fixture', $6, $7, $8,
                           $9)""",
                cid, T[k], m1, m2, src["system_one_model"], src["model_1_budget_kind"], src["model_1_budget_value"],
                src["model_2_budget_kind"], src["model_2_budget_value"])
    org = await platform(conn, "SELECT bool_and(is_test) FROM organizations WHERE id = ANY($1::uuid[])", [ORG, ORG2],
                         fetch="fetchval")
    users = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                           fetch="fetchval")
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE starts_with(name, $1)", SEC_PREFIX,
                       fetch="fetchval")
    check(org is True and int(users) == len(USERS) and int(n) == N_MASTER,
          f"fixture orgs and users are is_test; the fixture master has {N_MASTER:,} rows", f"{org} {users} {n}")
    S["seeded"] = org is True and int(n) == N_MASTER
    await VR.check_roles_as_meant(conn, check, {U_READ: (ORG, {"org_admin"}), U_READ2: (ORG2, {"org_admin"})})


# ═══ (b) catalog ════════════════════════════════════════════════════════════
async def selectable_sql(conn) -> set[str]:
    rows = await platform(conn, "SELECT DISTINCT g.id::text FROM portfolio.securities_global g JOIN "
                                "market_data.indicator_series s ON s.security_global_id = g.id AND s.ingest_status = "
                                "'active' WHERE g.valid_to IS NULL AND g.system_to IS NULL AND g.merged_into_id IS NULL")
    return {r["id"] for r in rows}


async def y_catalog(conn):
    section(f"[B] GET /market/catalog over a fixture master of {N_MASTER:,} rows (+ the live master)")
    master = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE valid_to IS NULL AND "
                                      "system_to IS NULL AND merged_into_id IS NULL", fetch="fetchval"))
    check(master >= N_MASTER, f"the security master read by the catalog holds >= {N_MASTER:,} active rows",
          f"{master:,}")
    st0, _b0, s0, _ = await api(U_READ, "GET", "/api/v1/market/catalog")      # warm
    st, body, secs, raw = await api(U_READ, "GET", "/api/v1/market/catalog")
    check(st == 200 and isinstance(body, dict) and "securities" in body,
          "the catalog answers 200 with a JSON body (it raised a 500 before the fix)", f"{st0}/{st} {str(body)[:200]}")
    check(secs < 2.0, "the catalog answers in under 2 s (warm)", f"{secs:.2f}s (first call {s0:.2f}s)")
    perms, vocab = body.get("permissions") or {}, body.get("vocabularies") or {}
    check(perms.get("can_read") is True and perms.get("can_write") is False and "is_super_admin" in perms
          and isinstance(vocab, dict) and vocab.get("editable") == [] and vocab.get("inline_editable") == [],
          "the permissions envelope and vocabularies are present (fail-closed contract unchanged)", f"{perms}")
    secs_l = body.get("securities") or []
    ids = {s["id"] for s in secs_l}
    want = await selectable_sql(conn)
    check(ids == want and len(secs_l) >= len(want),
          "the catalog lists EXACTLY the selectable securities (SQL: active series linked), no more",
          f"listed={len(ids)} sql={len(want)} extra={list(ids - want)[:3]} missing={list(want - ids)[:3]}")
    check(all(s["selectable"] is True and s["unselectable_reason"] is None and s["series_key"] for s in secs_l),
          "every listed security is selectable with its series key")
    check(set(S["priced"]) <= ids and S["unpriced"] not in ids,
          "the three priced fixtures are listed; the 9,997 unpriced fixtures are not", f"{len(ids)}")
    check(all(re.fullmatch(r"#[0-9A-F]{6}", s.get("color") or "") for s in secs_l),
          "every listed security carries a server colour")
    st2, body2, _, _ = await api(U_READ, "GET", "/api/v1/market/catalog")
    check({s["id"]: s["color"] for s in secs_l} == {s["id"]: s["color"] for s in body2.get("securities") or []},
          "security colours are identical across calls")
    check(vocab.get("search") == {"min_chars": 2, "max_chars": 100, "max_results": 25},
          "the catalog publishes the search limits (the page reads them; nothing hard-coded)", f"{vocab.get('search')}")
    check(b"org_id" not in raw, "the catalog carries no org_id")
    st3, b3, _, _ = await api(U_READ, "GET", "/api/v1/market/catalog", params={"x": "1"})
    check(st3 == 422 and "securities" not in b3, "the catalog still refuses any query parameter", f"{st3}")


# ═══ (c) search ═════════════════════════════════════════════════════════════
async def y_search(conn):
    section("[C] GET /market/securities/search — gate, refusals, <= 25 results, the unselectable reason, isolation")
    path = "/api/v1/market/securities/search"
    for name, uid, want in (("anonymous", None, 401), ("a pending (unapproved) user", U_PEND, 403)):
        st_s, _, _, _ = await api(uid, "GET", path, params={"q": SEC_PREFIX})
        st_c, _, _, _ = await api(uid, "GET", "/api/v1/market/catalog")
        check(st_s == want and st_c == want, f"{name}: the search answers {want}, exactly like the catalog",
              f"search={st_s} catalog={st_c}")
    bad = [("no q", {}), ("1 character", {"q": "a"}), ("only spaces", {"q": "   "}), ("101 characters", {"q": "x" * 101}),
           ("an extra parameter", {"q": "abc", "org_id": "SECRETVALUE"}), ("q twice", [("q", "abc"), ("q", "abd")]),
           ("a control character", {"q": "ab\x01c"})]
    for name, params in bad:
        st, body, _, raw = await api(U_READ, "GET", path, params=params)
        check(st == 422 and "SECRETVALUE" not in raw.decode("utf-8", "replace") and "xxxxxxxxxx" not in
              raw.decode("utf-8", "replace") and "securities" not in body,
              f"refused: {name} → 422, the value never echoed, no data", f"{st} {str(body)[:160]}")
    st, body, secs, raw = await api(U_READ, "GET", path, params={"q": SEC_PREFIX.lower()})
    res = body.get("securities") or []
    check(st == 200 and len(res) == 25 and body.get("result_count") == 25 and body.get("max_results") == 25,
          f"a query matching {N_MASTER:,} rows returns exactly 25 (the cap)", f"{st} {len(res)} {secs:.2f}s")
    perms = body.get("permissions") or {}
    check(perms.get("can_read") is True and perms.get("can_write") is False
          and (body.get("vocabularies") or {}).get("editable") == [],
          "the search carries the same permissions envelope and vocabularies as the catalog", f"{perms}")
    first3 = [r["id"] for r in res[:3]]
    check(set(first3) == set(S["priced"]) and all(r["selectable"] for r in res[:3]),
          "priced (selectable) matches come first", f"{[r['name'] for r in res[:4]]}")
    rest = res[3:]
    check(bool(rest) and all(r["selectable"] is False and r["unselectable_reason"] == "no_price_history"
                             and r["series_key"] is None and r["price_source"] == "none" for r in rest),
          "every match without price history comes back selectable:false with reason 'no_price_history'",
          f"{len(rest)}")
    st, body, _, _ = await api(U_READ, "GET", path, params={"q": "zzdopsu"})
    r = body.get("securities") or []
    check(st == 200 and [x["id"] for x in r] == [S["unpriced"]] and r[0]["ticker"] == "ZZDOPSU"
          and r[0]["selectable"] is False and r[0]["unselectable_reason"] == "no_price_history",
          "a ticker search (case-insensitive prefix) finds the unpriced fixture, unselectable with the reason",
          f"{st} {r[:1]}")
    st, body, _, _ = await api(U_READ, "GET", path, params={"q": "ZZDOPSP"})
    r = body.get("securities") or []
    cat = {s["id"]: s for s in (await api(U_READ, "GET", "/api/v1/market/catalog"))[1].get("securities") or []}
    check(st == 200 and len(r) == 1 and r[0]["selectable"] is True
          and r[0]["color"] == cat.get(S["priced_ticker_id"], {}).get("color"),
          "a priced search result is selectable and has exactly its catalog colour", f"{r[:1]}")
    st, body, _, _ = await api(U_READ, "GET", path, params={"q": "%_"})
    lit = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE valid_to IS NULL AND "
                                   "system_to IS NULL AND merged_into_id IS NULL AND (strpos(name, '%_') > 0 OR "
                                   "strpos(coalesce(short_name, ''), '%_') > 0)", fetch="fetchval"))
    check(st == 200 and body.get("result_count") == min(lit, 25) and body.get("result_count") < 25,
          "LIKE wildcards in q are matched literally (q='%_' is not 'match everything')",
          f"api={body.get('result_count')} literal-in-sql={lit}")
    n_like = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE valid_to IS NULL AND "
                                      "system_to IS NULL AND merged_into_id IS NULL AND name ILIKE $1",
                                f"%{SEC_PREFIX} SEC 0001%", fetch="fetchval"))
    st, body, _, _ = await api(U_READ, "GET", path, params={"q": f"{SEC_PREFIX} SEC 0001"})
    got = {x["name"] for x in body.get("securities") or []}
    check(st == 200 and 0 < n_like < 25 and len(got) == n_like
          and all(f"{SEC_PREFIX} SEC 0001" in n for n in got),
          "the search narrows: the same predicate in SQL gives the same rows (inclusion AND exclusion)",
          f"sql={n_like} api={len(got)}")
    a = (await api(U_READ, "GET", path, params={"q": SEC_PREFIX}))[3]
    b = (await api(U_READ2, "GET", path, params={"q": SEC_PREFIX}))[3]
    ja, jb = json.loads(a), json.loads(b)
    ja.pop("permissions", None), jb.pop("permissions", None)
    check(ja == jb and b"org_id" not in a and str(ORG).encode() not in a and str(ORG2).encode() not in b,
          "other-org isolation unchanged: platform reference data, identical for two orgs, no org id in the answer")


# ═══ the daily-run machinery ════════════════════════════════════════════════
async def config_row(pool, task_key):
    async with AB._platform_tx(pool) as conn:
        row = await conn.fetchrow("SELECT e.*, s.model_route AS jev_route FROM ai_ensemble_configs e JOIN "
                                  "ai_system_one_models s ON s.key = e.system_one_model WHERE e.task_key = $1 "
                                  "AND e.is_active", task_key)
    return dict(row) if row else None


def note_job(key: str, docs: list[str]) -> DR.Job:
    """The REAL note job's read path (DR._note_read: run_notes → cascade → store) over fixture filings."""
    done: set = set()

    async def load_config(pool):
        return await config_row(pool, T[key])

    async def queue(pool, *, exclude=(), limit=DR.BATCH, include_test_orgs=False, cfg=None):
        return [{"id": d} for d in docs if d not in done and d not in exclude][:limit]

    async def read_batch(pool, run, cfg, items, stop_when):
        res = await DR._note_read(pool, run, cfg, items, stop_when)
        # what run_notes staged is what the real queue would no longer offer
        async with AB._platform_tx(pool) as c:
            staged = {r["f"] for r in await c.fetch(
                "SELECT reference_filing_id::text AS f FROM portfolio.note_extraction_staging WHERE "
                "reference_filing_id = ANY($1::uuid[])", docs)}
        done.update(staged)
        return res

    return DR.Job(T[key], f"fixture {key}", load_config, queue, read_batch, catalog_loader=lambda: fake_catalog(*ALL_DEPS))


def replica_job(key: str, funds: list[str], log: list) -> DR.Job:
    """The replica job's reading: the REAL reader pair (replica_terms.read_pair) per fund, in the run's scope."""
    done: set = set()

    async def load_config(pool):
        return await config_row(pool, T[key])

    async def queue(pool, *, exclude=(), limit=DR.BATCH, include_test_orgs=False, cfg=None):
        return [{"id": f} for f in funds if f not in done and f not in exclude][:limit]

    async def read_batch(pool, run, cfg, items, stop_when):
        res = DR.BatchResult()
        async with pool.acquire() as c2:
            specs = RT.build_specs(await RT.load_registry(c2))
        msgs, ph = RT.build_messages(specs, "Fund prospectus text. DAILYOPS1 fixture. " * 40, fund_label="ZZDO1",
                                     form_type="497K")
        saved = RT.TASK_KEY
        RT.TASK_KEY = T[key]           # read_pair reuses the daily run's own budget scope (it keys on the task)
        try:
            for it in items:
                if stop_when():
                    break
                try:
                    await RT.read_pair(cfg, [cfg["model_1"], cfg["model_2"]], specs, msgs, ph,
                                       fake_catalog(cfg["model_1"], cfg["model_2"]))
                except AB.BudgetReached as exc:
                    res.budget = exc
                    break
                done.add(it["id"])
                log.append(it["id"])
                res.done += 1
        finally:
            RT.TASK_KEY = saved
        return res

    return DR.Job(T[key], f"fixture {key}", load_config, queue, read_batch, catalog_loader=lambda: fake_catalog(*ALL_DEPS))


def far_clock():
    """The slice clock: a fixed origin far before the deadline, so a slice never ends on time in this verify."""
    return 0.0


async def run_job(pool, key: str, job: DR.Job) -> dict:
    run = await DR.create_run(pool, T[key], trigger="manual", started_by=U_READ)
    out = await DR.drive(pool, run["id"], jobs={T[key]: job}, deadline=10 ** 9, clock=far_clock)
    return out


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def run_row(conn, run_id) -> dict | None:
    r = await platform(conn, "SELECT * FROM ai_daily_runs WHERE id = $1::uuid", str(run_id), fetch="fetchrow")
    if not r:
        return None
    d = dict(r)
    for k in ("usage", "failed_items"):
        d[k] = _j(d.get(k))
    return d


async def ledger(conn, dep) -> dict | None:
    r = await platform(conn, f"SELECT * FROM ai_daily_usage WHERE budget_day = {AB.BUDGET_DAY_SQL} AND deployment = $1",
                       dep, fetch="fetchrow")
    return dict(r) if r else None


def used(row) -> int:
    return int(row["input_tokens"]) + int(row["output_tokens"]) if row else 0


def calls_to(dep: str) -> int:
    return sum(1 for c in CALL_LOG if c["model"] == dep)


def recorded_per_call(dep: str) -> list[int]:
    return [c["in"] + c["out"] for c in CALL_LOG if c["model"] == dep]


def reserved_per_call(dep: str) -> list[int]:
    return [t for d, t in RES_LOG if d == dep]


# ═══ (d) the note run with no USD cap ══════════════════════════════════════
async def y_note_nocap(conn):
    section("[D] The note job: no USD cap → NULL, the run completes (real _note_read, stub transport); a cap that "
            "cannot be recorded is refused at the start")
    # REPRODUCE the original failure: the old sentinel cannot be stored in the column.
    err = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await conn.execute("INSERT INTO portfolio.note_extraction_runs (run_kind, status, config, spend_cap_usd) "
                               "VALUES ('verify', 'running', '{}'::jsonb, $1)", Decimal(str(1e9)))
            raise RuntimeError("rollback")
    except asyncpg.NumericValueOutOfRangeError as exc:
        err = exc
    except RuntimeError:
        err = None
    check(err is not None and "precision 12, scale 6" in str(err),
          "REPRODUCED: the old sentinel 1e9 overflows spend_cap_usd numeric(12,6) (the nightly stop_detail)",
          f"{type(err).__name__ if err else None}: {str(err)[:120] if err else ''}")
    os.environ.pop(DR.CAP_ENV, None)
    check(DR.spend_cap() is None, f"{DR.CAP_ENV} unset → spend_cap() is None (no cap), not a sentinel number")
    col = await platform(conn, "SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'portfolio' "
                               "AND table_name = 'note_extraction_runs' AND column_name = 'spend_cap_usd'",
                         fetch="fetchval")
    check(col == "YES", "docs/dailyops1_part1.sql is applied: spend_cap_usd allows NULL", f"{col}")
    pool = await get_pool()
    docs = S["filings"][:N_DOCS]
    n0 = len(CALL_LOG)
    out = await run_job(pool, "note_run", note_job("note_run", docs))
    row = await run_row(conn, out.get("id"))
    S["note_run_row"] = row
    check(row is not None and row["status"] == DR.FINISHED and row["documents_done"] == N_DOCS
          and row["documents_failed"] == 0 and row["stop_detail"] is None,
          f"the daily note run with the REAL budgets and NO cap completes (finished_queue_empty, {N_DOCS} documents)",
          f"{(row or {}).get('status')} done={(row or {}).get('documents_done')} {(row or {}).get('stop_detail')}")
    nrows = await platform(conn, "SELECT id, status, spend_cap_usd, notes_done, config FROM "
                                 "portfolio.note_extraction_runs WHERE ensemble_config_id = $1", CFG_IDS["note_run"])
    check(len(nrows) >= 1 and all(r["spend_cap_usd"] is None for r in nrows)
          and sum(int(r["notes_done"]) for r in nrows) == N_DOCS and all(r["status"] == "completed" for r in nrows)
          and all((_j(r["config"]) or {}).get("daily_run_id") == str(row["id"]) for r in nrows),
          "its note_extraction_runs row is written with spend_cap_usd NULL and completes (the row that overflowed)",
          f"{[(r['status'], r['spend_cap_usd'], r['notes_done']) for r in nrows]}")
    staged = int(await platform(conn, "SELECT count(DISTINCT reference_filing_id) FROM portfolio.note_extraction_staging "
                                      "WHERE reference_filing_id = ANY($1::uuid[])", docs, fetch="fetchval"))
    check(staged == N_DOCS, f"all {N_DOCS} fixture documents were extracted (staged)", f"{staged}")
    m1, m2 = DEP["note_run"]
    u = row["usage"] or {}
    l1, l2 = await ledger(conn, m1), await ledger(conn, m2)
    check(set(u) == {m1, m2} and all(u[d]["calls"] >= N_DOCS for d in (m1, m2))
          and used(l1) == u[m1]["input_tokens"] + u[m1]["output_tokens"]
          and used(l2) == u[m2]["input_tokens"] + u[m2]["output_tokens"]
          and int(l1["calls"]) == u[m1]["calls"] and int(l2["calls"]) == u[m2]["calls"],
          "usage is recorded per model on the run and matches the shared ledger exactly", f"{u}")
    check(calls_to(m1) == calls_to(m2) == N_DOCS and len(CALL_LOG) - n0 == 2 * N_DOCS,
          "one call per model per document (never a one-model reading)", f"{calls_to(m1)}/{calls_to(m2)}")
    # a configured cap above 10^6 is refused at the start with a clear reason
    os.environ[DR.CAP_ENV] = "1000001"
    try:
        n1 = len(CALL_LOG)
        nr0 = int(await platform(conn, "SELECT count(*) FROM portfolio.note_extraction_runs WHERE ensemble_config_id = $1",
                                 CFG_IDS["note_m1"], fetch="fetchval"))
        out = await run_job(pool, "note_m1", note_job("note_m1", S["filings"][N_DOCS:2 * N_DOCS]))
        row = await run_row(conn, out.get("id"))
        nr1 = int(await platform(conn, "SELECT count(*) FROM portfolio.note_extraction_runs WHERE ensemble_config_id = $1",
                                 CFG_IDS["note_m1"], fetch="fetchval"))
        check(row is not None and row["status"] == DR.STOPPED_ERROR and row["documents_done"] == 0
              and "refused at start" in (row["stop_detail"] or "") and "999999.999999" in (row["stop_detail"] or "")
              and DR.CAP_ENV in (row["stop_detail"] or "") and len(CALL_LOG) == n1 and nr1 == nr0,
              f"{DR.CAP_ENV}=1000001 → stopped_error at the start with a clear reason; no model call, no run row",
              f"{(row or {}).get('stop_detail')}")
    finally:
        os.environ.pop(DR.CAP_ENV, None)
    for v, ok in (("999999.999999", True), ("25", True), ("1e9", False), ("nan", False), ("-1", False), ("abc", False)):
        try:
            SP.checked_spend_cap(v)
            got = True
        except SP.SpendCapConfigError:
            got = False
        check(got is ok, f"cap {v!r}: {'accepted' if ok else 'refused with SpendCapConfigError'} (store.create_run, "
              "every caller)")
    err = None
    try:
        await NSTORE.create_run(conn, run_kind="verify", spend_cap_usd=1e9, config={})
    except SP.SpendCapConfigError as exc:
        err = exc
    check(err is not None and "numeric(12,6)" in str(err), "store.create_run refuses 1e9 BEFORE the INSERT (the "
          "prefill / pilot / eval writers share it)", f"{err}")


# ═══ (e) the replica task (B3) ══════════════════════════════════════════════
async def y_replica(conn):
    section("[E] replica_terms_extraction — B3 is NO-WORK (no fund on any registry); the job runs when there is work")
    pool = await get_pool()
    real = int(await platform(conn, "SELECT count(*) FROM portfolio.replica_securities s JOIN organizations o ON o.id = "
                                    "s.org_id WHERE NOT o.is_test AND s.valid_to IS NULL AND s.system_to IS NULL AND "
                                    "s.status IN ('active', 'draft')", fetch="fetchval"))
    q = await DR.queue_items(DR.JOBS["replica_terms_extraction"], pool, limit=5)
    cfg = await DR.JOBS["replica_terms_extraction"].load_config(pool)
    if real == 0:
        check(q == [] and cfg is not None and cfg["model_1"] == "gpt-5-mini",
              "B3 asserted: the live replica queue is EMPTY because no real org has a live registry fund — the config "
              "loads, the job is registered; daily_tick returns queue_empty and records nothing", f"queue={q}")
        find("B3 = NO-WORK, not code: portfolio.replica_securities has no live fund in any real org; the job runs the "
             "first night after a fund is added to a Replication Studio registry")
    else:
        find(f"B3 changed since discovery: {real} live registry funds now exist — the live queue holds {len(q)}")
        check(len(q) > 0, "with live registry funds the replica queue is non-empty")
    if real == 0 and q == []:   # only then can the real tick write nothing (it would start a REAL run otherwise)
        t = await DR.daily_tick(pool, now_utc=datetime.now(timezone.utc).replace(hour=12, minute=0),
                                jobs={"replica_terms_extraction": DR.JOBS["replica_terms_extraction"]})
        rj = (t.get("jobs") or {}).get("replica_terms_extraction") or {}
        check(rj.get("action") == "queue_empty", "daily_tick (noon UTC, live state) → replica 'queue_empty', no row",
              f"{t}")
    # with work, the job runs under its own budgets (fixture config = the live replica budgets)
    log: list = []
    out = await run_job(pool, "rep_run", replica_job("rep_run", ["f1", "f2", "f3"], log))
    row = await run_row(conn, out.get("id"))
    m1, m2 = DEP["rep_run"]
    u = (row or {}).get("usage") or {}
    check(row is not None and row["status"] == DR.FINISHED and row["documents_done"] == 3 and log == ["f1", "f2", "f3"]
          and set(u) == {m1, m2} and u[m1]["calls"] == 3 and u[m2]["calls"] == 3,
          "the replica job, given work, reads every fund under its own budgets (finished_queue_empty, usage per model)",
          f"{(row or {}).get('status')} {log} {u}")


# ═══ (f) budgets honoured exactly ═══════════════════════════════════════════
async def exact_budget(conn, key: str, slot: int, job_factory, unit_from: str):
    """Preload the target model's ledger so exactly ONE more document fits; the run must read one document, stop
    on THAT model with THAT budget, and never cross it."""
    pool = await get_pool()
    src = S["live"]["note_terms_extraction" if key.startswith("note") else "replica_terms_extraction"]
    budget = Decimal(src[f"model_{slot}_budget_value"])
    target, other = DEP[key][slot - 1], DEP[key][2 - slot]
    ref = DEP[unit_from][slot - 1]
    rec, res_ = recorded_per_call(ref), reserved_per_call(ref)
    if not rec or not res_ or len(set(rec)) != 1 or len(set(res_)) != 1:
        check(False, f"{key}: the per-call usage is known from the completing run", f"rec={set(rec)} res={set(res_)}")
        return
    E, R = rec[0], res_[0]
    used0 = int(budget) - R - E // 2
    await platform(conn, f"INSERT INTO ai_daily_usage (budget_day, deployment, input_tokens) VALUES "
                         f"({AB.BUDGET_DAY_SQL}, $1, $2)", target, used0, fetch="execute")
    c_t, c_o = calls_to(target), calls_to(other)
    out = await run_job(pool, key, job_factory())
    row = await run_row(conn, out.get("id"))
    lt, lo = await ledger(conn, target), await ledger(conn, other)
    ok = (row is not None and row["status"] == DR.STOPPED_BUDGET and row["documents_done"] == 1
          and row["stopped_model"] == target and Decimal(row["stopped_budget_value"]) == budget
          and row["stopped_budget_kind"] == "tokens_per_day")
    check(ok, f"{key}: the run reads ONE document, then stops_budget on {target} at its live budget "
          f"{int(budget):,} tokens/day", f"{(row or {}).get('status')} done={(row or {}).get('documents_done')} "
          f"model={(row or {}).get('stopped_model')} value={(row or {}).get('stopped_budget_value')}")
    final = used(lt)
    check(final == used0 + E and final <= int(budget) and final + R > int(budget)
          and Decimal(row["stopped_used"]) == Decimal(final) and int(lt["reserved_tokens"]) == 0,
          f"{key}: honoured EXACTLY — used {final:,} <= {int(budget):,}, and the next call ({R:,}) would cross it; "
          "nothing left reserved", f"used0={used0:,} E={E:,} R={R:,} final={final:,} stopped_used={row['stopped_used']}")
    check(calls_to(target) - c_t == 1 and calls_to(other) - c_o == 1 and used(lo) == E,
          f"{key}: the stopping document made NO call on either model (all-or-nothing pair)",
          f"target +{calls_to(target) - c_t}, other +{calls_to(other) - c_o}")


async def y_budgets(conn):
    section("[F] The REAL active budgets (B4) honoured exactly: note 1,500,000 + 500,000; replica 250,000 + 250,000")
    live = S["live"]
    got = {k: (live[k]["model_1"], live[k]["model_1_budget_kind"], int(live[k]["model_1_budget_value"]),
               live[k]["model_2"], live[k]["model_2_budget_kind"], int(live[k]["model_2_budget_value"]))
           for k in ("note_terms_extraction", "replica_terms_extraction")}
    check(got == {"note_terms_extraction": ("gpt-5-mini", "tokens_per_day", 1_500_000, "gpt-5-nano", "tokens_per_day",
                                            500_000),
                  "replica_terms_extraction": ("gpt-5-mini", "tokens_per_day", 250_000, "gpt-5-nano", "tokens_per_day",
                                               250_000)},
          "the live active configs are exactly B4 (read, never written)", f"{got}")
    total = sum(v[2] + v[5] for v in got.values())
    check(total == 2_500_000, "the two OpenAI jobs' budgets total 2,500,000 tokens/day", f"{total:,}")
    if S.get("note_run_row") is None:
        check(False, "section d ran first (its completing run gives the per-call usage)")
        return
    for key, slot in (("note_m1", 1), ("note_m2", 2)):
        base = {"note_m1": N_DOCS, "note_m2": 2 * N_DOCS}[key]
        await exact_budget(conn, key, slot, lambda b=base: note_job(key, S["filings"][b:b + N_DOCS]), "note_run")
    for key, slot in (("rep_m1", 1), ("rep_m2", 2)):
        await exact_budget(conn, key, slot, lambda k=key: replica_job(k, ["g1", "g2", "g3"], []), "rep_run")


# ═══ (g) the public-data guard ══════════════════════════════════════════════
async def y_guard(conn):
    section("[G] The public-data guard, both ways")
    pool = await get_pool()
    # positive: the REAL note queue (read-only) offers only public EDGAR filings
    q = await DR.queue_items(DR.JOBS["note_terms_extraction"], pool, limit=DR.BATCH)
    rows = await platform(conn, "SELECT id::text, form_type, retention_classification, source_url FROM "
                                "portfolio.reference_filings WHERE id = ANY($1::uuid[])", [i["id"] for i in q])
    check(len(rows) == len(q) and all(r["form_type"] in DR.NOTE_PUBLIC_FORMS and r["retention_classification"] ==
                                      "public_reference" and r["source_url"].startswith("https://www.sec.gov/")
                                      for r in rows),
          "the real note queue offers only public EDGAR filings (424B2/FWP, public_reference, sec.gov source)",
          f"{len(q)} items")
    if not q:
        find("the live note queue is empty right now — the rule above held vacuously for the live rows")
    cons = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid IN "
              "('portfolio.reference_filings'::regclass, 'portfolio.replica_source_documents'::regclass)")}
    check("public_reference" in cons.get("reference_filings_retention_check", "")
          and "owner_scope = 'platform'" in cons.get("replica_source_documents_platform_chk", "")
          and "org_id IS NULL" in cons.get("replica_source_documents_platform_chk", "")
          and "497K" in cons.get("replica_source_documents_form_chk", ""),
          "the two corpora are public by the database itself: note filings CHECK public_reference; prospectuses CHECK "
          "platform-owned (org_id NULL) EDGAR forms")
    # negative: a non-public job is refused before any item is selected
    for tk in ("statement_positions", T_NONPUB):
        selected: list = []

        async def load_config(pool, tk=tk):
            selected.append("config")
            return {"id": None}

        async def queue(pool, **kw):
            selected.append("queue")
            return [{"id": "client-doc"}]

        async def read_batch(*a, **kw):
            selected.append("read")
            return DR.BatchResult(done=1)

        job = DR.Job(tk, "must never run", load_config, queue, read_batch)
        run = await DR.create_run(pool, tk, trigger="manual", started_by=U_READ)
        out = await DR.drive(pool, run["id"], jobs={tk: job}, deadline=10 ** 9, clock=far_clock)
        row = await run_row(conn, run["id"])
        check(row["status"] == DR.STOPPED_ERROR and "not a public-data job" in (row["stop_detail"] or "")
              and selected == [] and row["documents_done"] == 0,
              f"drive() refuses '{tk}' before loading a config or selecting a single item", f"{row['stop_detail']}")
        t = await DR.daily_tick(pool, now_utc=datetime.now(timezone.utc).replace(hour=12, minute=0), jobs={tk: job})
        act = ((t.get("jobs") or {}).get(tk) or {}).get("action")
        check(act in ("never_scheduled", "not_public_data") and selected == [],
              f"daily_tick never schedules '{tk}'", f"{act}")
    # statement_positions can never route to an OpenAI (data-sharing) model
    flagged = await MC.public_data_only_model_ids(conn)
    check({"gpt-5-mini", "gpt-5-nano"} <= flagged, "the live catalog flags both OpenAI models public_data_only",
          f"{sorted(flagged)}")
    for model in ("gpt-5-mini", "gpt-5-nano"):
        for org in (None, ORG):
            err = None
            try:
                await MC.assert_model_allowed_for_task(conn, model, task_key="statement_positions", org_id=org)
            except MC.PublicDataOnlyError as exc:
                err = exc
            check(err is not None, f"{model} is refused for statement_positions (org {'none' if org is None else 'set'})")
        err = None
        try:
            await HA._refuse_sharing_models(conn, [model], ORG)
        except HA.HeldAwayConflict as exc:
            err = exc
        check(err is not None and "never sent to a data-sharing model" in str(err),
              f"the statement reader refuses {model} at the call", f"{err}")
        for tk in ("note_terms_extraction", "replica_terms_extraction"):
            await MC.assert_model_allowed_for_task(conn, model, task_key=tk, org_id=None)
        check(True, f"{model} is allowed for the two public-data jobs with no org (the other direction)")
    stm = S["live"].get("statement_positions")
    check(stm is not None and stm["model_1"] not in flagged and stm["model_2"] not in flagged,
          "the live statement_positions config uses no data-sharing model", f"{(stm or {}).get('model_1')}/"
          f"{(stm or {}).get('model_2')}")
    check("statement_positions" in DR.NEVER_SCHEDULED and "statement_positions" not in DR.JOBS
          and "statement_positions" not in MC.PUBLIC_DATA_TASK_KEYS,
          "statement_positions is never a daily job and never a public-data task")


# ═══ (h) READ-ONLY live checks ══════════════════════════════════════════════
async def y_live(conn):
    section("[H] READ-ONLY on the live database: the real catalog builds; selectable = linked-to-active-series count")
    async with conn.transaction():
        await MREPO.make_read_only(conn)
        err = None
        body = None
        t0 = time.monotonic()
        try:
            body = await MSVC.build_catalog(conn)
        except Exception as exc:  # noqa: BLE001
            err = exc
        secs = time.monotonic() - t0
    check(err is None and body is not None, "the real catalog builds without raising (it raised RuntimeError before)",
          f"{type(err).__name__ if err else 'ok'} {secs:.2f}s")
    want = await selectable_sql(conn)
    real = {s["id"] for s in (body or {}).get("securities", []) if not s["name"].startswith(SEC_PREFIX)}
    want_real = {i for i in want if i not in S.get("priced", {})}
    check(bool(body) and real == want_real, "its selectable count equals the securities linked to an active series",
          f"catalog={len(real)} sql={len(want_real)}")
    master = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE valid_to IS NULL AND "
                                      "system_to IS NULL AND merged_into_id IS NULL AND NOT starts_with(name, $1)",
                                SEC_PREFIX, fetch="fetchval"))
    find(f"live security master: {master:,} active rows; the catalog now sends {len(real)} (selectable only)")


async def y_routes(conn):
    section("[I] Route matrices CURRENT; the new route has the catalog's gate")
    route_matrix_checks(check, find)


# ═══ (j) regressions ════════════════════════════════════════════════════════
# verify_mkt04a's three navigation checks went stale when navregroup (e97718f, 2026-10-20) moved the menu to ONE
# definition (lib/menuVisibility.mjs: main(50, "/market", "Market Indicators", …)); they fail identically at this
# sprint's base. They are reported here as pre-existing — never counted as a pass of this sprint's work — and only
# while this sprint leaves the two files they read untouched.
PREEXISTING = {"verify_mkt04a.py": ({"Sidebar NAV_ITEMS carries the /market entry",
                                     "NAV_ITEMS renders unfiltered for every signed-in user",
                                     "lib/menuVisibility.mjs MENU_ITEMS lists /market with gate null"},
                                    ("apps/web/components/Sidebar.jsx", "apps/web/lib/menuVisibility.mjs"))}


def regression():
    section("[J] Regressions — each ONCE, flat, after teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        known, files = PREEXISTING.get(script, (set(), ()))
        labels = {re.sub(r"^\[FAIL\]\s*", "", ln.strip()).split(" — ")[0].split("  — ")[0] for ln in fails}
        if known and fails and labels <= known:
            rc, diff, _ = VS.git("diff", "--name-only", BEFORE_REF, "HEAD", "--", *files)
            untouched = rc == 0 and not diff.strip()
            find(f"{script}: {len(fails)} PRE-EXISTING FAIL(s) (navregroup moved the menu; stale since 2026-10-20)",
                 "; ".join(sorted(labels)))
            check(untouched and t.line is not None and t.failed == len(fails),
                  f"{script}: every FAIL is a known pre-existing one, in files this sprint never touched",
                  f"{t.detail()}; untouched={untouched}")
            continue
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


# ═══ driver ═════════════════════════════════════════════════════════════════
SECTIONS = {"a": None, "b": "catalog", "c": "search", "d": "note", "e": "replica", "f": "budgets", "g": "guard",
            "h": "live", "i": "routes", "j": "regressions"}


async def run(name, fn, *args):
    t0 = time.monotonic()
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"section '{name}' raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"[INFO] {name}: {time.monotonic() - t0:.1f}s", flush=True)


async def cleanup_only(conn) -> int:
    log = await teardown_retrying()
    left = await leftovers(conn)
    print(f"[INFO] cleanup removed: {log}")
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    await close_conn(conn)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


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
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    only = None
    if "--section" in sys.argv:
        only = sys.argv[sys.argv.index("--section") + 1].lower()
        if only not in SECTIONS:
            print(f"[FAIL] unknown section {only!r}; one of {sorted(SECTIONS)}")
            print("TOTAL: 0 PASS, 1 FAIL")
            return 1
    want = (lambda k: True) if only is None else (lambda k: k == only or (only == "f" and k in ("d", "e")))
    if only is None or only == "a":
        section("[A] palette")
        palette_checks(check, find)
    if only is None or only == "i":
        await run("routes", y_routes, conn)
    if only in ("a", "i", "j"):
        await close_conn(conn)
        if only == "j":
            regression()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    pre = real = None
    saved_keys = MC.PUBLIC_DATA_TASK_KEYS
    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the app connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        if want("h"):
            await run("live (before seeding)", y_live, conn)
        await run("seed", seed, conn)
        if not S.get("seeded"):
            check(False, "seed completed", "every fixture section skipped")
        else:
            for k, name, fn in (("b", "catalog", y_catalog), ("c", "search", y_search), ("d", "note", y_note_nocap),
                                ("e", "replica", y_replica), ("f", "budgets", y_budgets), ("g", "guard", y_guard)):
                if want(k):
                    await run(name, fn, conn)
        check(NETWORK_CALLS == [], "no network call (and no real R2 read) was attempted", f"{NETWORK_CALLS[:3]}")
        check(all(c["path"] == "/v1/chat/completions" and (c["model"] or "").startswith(FX) for c in CALL_LOG),
              "every model call went to the FAKE caller with a fixture deployment (no real model, no real ledger)",
              f"{len(CALL_LOG)} fake calls")
    finally:
        if pre is not None:
            section("Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
            await close_app_pool()
            try:
                log = await teardown_retrying()
                print(f"[INFO] teardown removed: {log}")
                left = await leftovers(conn)
                check(left == 0, "zero fixture rows remain", f"{left}")
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)",
                          f"{real[k][0]} → {after[k][0]}")
            except Exception as exc:  # noqa: BLE001
                check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
        MC.PUBLIC_DATA_TASK_KEYS = saved_keys
        await close_conn(conn)
    if only is None:
        if os.environ.get("VERIFY_NESTED") == "1":
            print("[SKIP] nested run")
        else:
            try:
                regression()
            except Exception as exc:  # noqa: BLE001
                check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
