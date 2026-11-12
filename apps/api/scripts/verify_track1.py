"""verify_track1 — "Track this security": any security-master security into the price coverage set on purpose (one at a
time, in bulk by a server-side filter, or by named list), coverage reason 'tracked', the operator's list loader.

    timeout 600 python3 apps/api/scripts/verify_track1.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_track1.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_track1.py --section t       # seed + ONE section + teardown
    python3 apps/api/scripts/verify_track1.py                               # everything + fast regressions

Sections: l READ-ONLY live (before seeding: today's coverage set = 29, no 'tracked' reason) · g gates (every write 403
for a non-Super-Admin, nothing written) · t track / untrack / coverage / history / audit · f bulk by filter (count =
the filter's own count, inclusion AND exclusion, confirmed count, the cap — a real 8,000+ filter and a lowered cap) ·
e estimate arithmetic · d list loader (dry run on a fixture list: hits / misses / ambiguous, no network, nothing
written; a real curated load; re-run restates nothing; the index ticker fallback) · b backfill dry run counts tracked
securities · n the nightly picks tracked securities up (stub provider), stalest-first, progress · m the Market
Indicators search's tracking block · r RLS · w render tests (node) · j fast regressions (verify_prices1,
verify_secmaster2, verify_dailyops1 — --self-checks-only, VERIFY_REGRESSIONS=fast).

NO NETWORK AND NO MODEL CALL: httpx's transport refuses and records; the price source runs on eod.RecordedHttpGet with
SYNTHETIC Yahoo-format bars for fixture tickers only; the LLM helpers are stubbed and must never be called. NEVER tracks
a real security: every write names fixture ids (asserted before seeding); the one real-data bulk request (security_type
= equity) is a REFUSAL, proven to write nothing.

THE CLOCK: TODAY = the last weekday on or before the database's UTC date, read once at start and passed explicitly to
every nightly / backfill call; the Yahoo adapter's now is NOW (TODAY 22:00 UTC). THE BUDGET: prices._budget is pinned
per section (100 calls; 1 call for the stalest-first proof) so the real day's call log never decides a result.

FIXTURES: orgs 99000000-0000-0000-0000-0000007a1001..2 (is_test, asserted; ORG_P is the fixture PLATFORM org —
security_master_jobs.PLATFORM_ORG_ID points at it for this process, so every audit row lands there); users …7a1101–103;
securities named 'ZZT1 VERIFY …' with tickers ZZT…; list keys 'zzt1_…'; reference attributes from provider 'zzt1_fx'.

TEARDOWN (start and end): _verify_cleanup.cleanup on the operator DSN by fixture org / user / security ids, list keys and
run ids; every counted table at its exact pre-test count; real rows (tracked securities, lists, members, the coverage
set with its reasons, the real provider call log) unchanged by count + checksum.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "track1"
BEFORE_REF = "5bdde6e"   # track1-work base: main @ 'chore: refresh schema snapshot' (dailyops1 merged)
P_ = "/api/v1/admin/security-data/master"
NEW_ROUTES = {("GET", f"{P_}/tracked"), ("POST", f"{P_}/tracked/estimate"), ("POST", f"{P_}/tracked/track"),
              ("POST", f"{P_}/tracked/untrack")}
REGRESSIONS = ["verify_prices1.py", "verify_secmaster2.py", "verify_dailyops1.py"]
LIVE_COVERAGE = 29       # the coverage set on 2026-11-12 (prompt fact; read-only check)


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ═══ self-checks (no database) ══════════════════════════════════════════════
def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == NEW_ROUTES and not o.changed and not o.removed,
          "track1's new routes are exactly the four /tracked routes; no existing gate changed or removed",
          f"{o.where()}; new={sorted(mine)} changed={o.changed[:3]} removed={o.removed[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    gates = {k: (by.get(k) or {}).get("after") for k in NEW_ROUTES}
    check(all(g == "super_admin" for g in gates.values()), "every new route is gated Super Admin", f"{gates}")
    drift = VS.still_gated(o, list(o.new))
    check(not drift, "the new routes still exist today with the same gate", f"{drift[:3]}")
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        p = subprocess.run([sys.executable, str(HERE / script), "--check"], cwd=REPO, capture_output=True, text=True,
                           timeout=600)
        check(p.returncode == 0 and "CURRENT" in p.stdout, f"{script} --check: CURRENT", (p.stdout + p.stderr)[-300:])


def self_static(check, find):
    notes = _read("docs/TRACK1_NOTES.md")
    for tok in ("security_price_coverage", "reference_data_lists", "sec_13f_list", "replica_holdings_snapshots",
                "indicator_series", "underlying_index_registry", "BENCHMARK_TICKERS", "3,000", "128 securities a night",
                "stalest-first", "ticker", "Bloomberg-style"):
        check(tok in notes, f"TRACK1_NOTES names {tok!r} (extend-vs-new evidence / findings)")
    sql = _read("docs/track1_part1.sql")
    for t in ("tracked_securities", "security_lists", "security_list_members"):
        check(f"portfolio.{t}" in sql, f"migration creates portfolio.{t}")
    check(all(x in sql for x in ("FOR SELECT USING (true)", "FOR INSERT WITH CHECK", "FOR UPDATE USING",
                                 "FOR DELETE USING", "GRANT SELECT, INSERT, UPDATE, DELETE")),
          "migration: a policy for every operation + app_service grants (like security_price_coverage)")
    old = VS.git_show(BEFORE_REF, "apps/api/services/security_data/prices.py") or ""
    from services.security_data import prices as P
    m = re.search(r'COVERAGE_SQL = f"""(.*?)"""', old, flags=re.S)
    cur = re.search(r'COVERAGE_SQL = f"""(.*?)"""', _read("apps/api/services/security_data/prices.py"), flags=re.S)
    check(bool(m and cur and m.group(1) == cur.group(1)), "COVERAGE_SQL is byte-identical to the base (existing reasons "
          "untouched; 'tracked' is an OPTIONAL_SOURCES entry)")
    check(P.BENCHMARK_TICKERS == ("SPY", "AGG", "ACWI"), "BENCHMARK_TICKERS unchanged", f"{P.BENCHMARK_TICKERS}")
    check(any(t == "portfolio.tracked_securities" and s == "tracked" for t, s, _ in P.OPTIONAL_SOURCES),
          "coverage_set reads portfolio.tracked_securities as the reason 'tracked'")
    from services.security_data import security_lists as SL
    try:
        defs = SL.load_definitions()
        keys = [d["key"] for d in defs]
        ok = keys == ["sp500", "russell2000", "core_etfs", "fixed_income", "commodities", "key_benchmarks"]
    except SL.ListFileError as exc:
        defs, keys, ok = [], [], False
        find("the data file did not validate", str(exc))
    check(ok, "the curated data file validates and defines the six lists", f"{keys}")
    by = {d["key"]: d for d in defs}
    if ok:
        kb = {m["ticker"] for m in by["key_benchmarks"]["members"]}
        check({"SPY", "AGG", "ACWI"} <= kb, "Key benchmarks includes the existing BENCHMARK_TICKERS")
        sectors = {m["ticker"] for m in by["core_etfs"]["members"]} & {"XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP",
                                                                         "XLRE", "XLU", "XLV", "XLY"}
        check(len(sectors) == 11, "Core ETFs holds the 11 Select Sector SPDRs", f"{sorted(sectors)}")
        check(all(str(d.get("overlap_note") or "").strip() for d in defs),
              "every list states its overlap with Market Indicators (FRED / Yahoo series not duplicated)")
        check(by["sp500"]["source"] == {"kind": "index_holdings", "fund_ticker": "SPY", "holdings_source": "ssga_spdr"}
              and by["russell2000"]["source"]["holdings_source"] == "sec_nport",
              "S&P 500 via SPY (ssga_spdr); Russell 2000 via IWM's N-PORT")


def self_arith(check, find):
    from services.security_data import tracking as TR
    from services.security_data.providers.yahoo_chart import SPEC

    cap = TR.nightly_capacity(SPEC, 180.0)
    check(cap == 128, "nightly capacity at Yahoo's spec: floor(180 / (1.0 + 0.4)) × 1 = 128", f"{cap}")
    e = TR.estimate_numbers(selected=2700, to_load=2650, coverage_after=2729, per_call=1, budget=2000, nightly_cap=128,
                            nightly_max_new=200)
    want = {"estimated_calls": 2650, "days_at_budget": math.ceil(2650 / 2000), "nights_nightly_only": math.ceil(2650 / 128),
            "upkeep_calls_per_day": 2729}
    check(all(e[k] == v for k, v in want.items()) and len(e["warnings"]) == 2,
          "estimate arithmetic by hand: 2,650 calls = 2 days at 2,000; 21 nights at 128; upkeep 2,729 > budget AND "
          "> nightly capacity → two warnings", f"{ {k: e[k] for k in want} } {e['warnings']}")
    z = TR.estimate_numbers(selected=5, to_load=0, coverage_after=34, per_call=1, budget=2000, nightly_cap=128,
                            nightly_max_new=200)
    check(z["estimated_calls"] == 0 and z["days_at_budget"] == 0 and z["nights_nightly_only"] == 0 and not z["warnings"],
          "nothing to load → 0 calls / 0 days / 0 nights, no warning", f"{z}")
    b = TR.estimate_numbers(selected=50, to_load=50, coverage_after=79, per_call=10, budget=None, nightly_cap=1280,
                            nightly_max_new=200)
    check(b["estimated_calls"] == 5 and b["days_at_budget"] is None, "a batch source (10 / call, no budget): 5 calls, "
          "no day estimate", f"{b}")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)
    self_arith(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import main  # noqa: E402
from services import extraction  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.database import get_pool, platform_scope  # noqa: E402
from services.security_data import layer as L  # noqa: E402
from services.security_data import prices as P  # noqa: E402
from services.security_data import security_lists as SL  # noqa: E402
from services.security_data import tracking as TR  # noqa: E402
from services.security_data.providers.eod import RecordedHttpGet  # noqa: E402
from services import replica_parsers as RP  # noqa: E402

TAG = "track1verify"
TICKER_PREFIX = "ZZT"
NAME_PREFIX = "ZZT1 VERIFY"
LIST_PREFIX = "zzt1"
ATTR_PROVIDER = "zzt1_fx"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
D = Decimal


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000007a1{n:03d}")


ORG_P, ORG_A = _id(1), _id(2)
FIXTURE_ORGS = [ORG_P, ORG_A]
U_SUPER, U_ADMIN_A, U_MEMBER_A = _id(101), _id(102), _id(103)
USERS = {U_SUPER: (ORG_P, "super_admin", "super", "super_admin", "TRACK1 Super Admin"),
         U_ADMIN_A: (ORG_A, "org_admin", "admin-a", "org_admin", "TRACK1 Org Admin A"),
         U_MEMBER_A: (ORG_A, "member", "member-a", "zzt1_viewer", "TRACK1 Member A")}
#: fixture securities: key → (ticker | None, type)
SECS = {**{f"E{i}": (f"ZZT{c}", "equity") for i, c in zip(range(1, 7), "ABCDEF")},
        **{f"F{i}": (f"ZZTF{i}", "fund") for i in range(1, 4)},
        "AMB1": ("ZZTAMB", "equity"), "AMB2": ("ZZTAMB", "equity"), "NT": (None, "equity")}

S: dict = {}
NETWORK_CALLS: list[str] = []
LLM_CALLS: list[str] = []
RUN_IDS: list[UUID] = []
SYN: dict = {}
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def weekdays_back(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


CLOCK: dict = {}


def sec(k) -> str:
    return str(S["sec"][k])


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


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
    import services.database as DB
    p, DB._pool = DB._pool, None
    await close_conn(p)


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def fixture_secs(conn) -> list:
    rows = await platform(conn, "SELECT id FROM portfolio.securities_global WHERE name LIKE $1 UNION SELECT "
                                "global_security_id FROM portfolio.securities_global_identifiers WHERE id_type = 'ticker' "
                                "AND id_value LIKE $2", f"{NAME_PREFIX}%", f"{TICKER_PREFIX}%")
    return [r[0] for r in rows]


def counted_plan(secs: list, runs: list) -> dict:
    run_txt = [str(r) for r in runs]
    return {
        "portfolio.tracked_securities": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.security_list_members": ("list_key LIKE $1 OR global_security_id = ANY($2::uuid[])",
                                            [f"{LIST_PREFIX}%", secs]),
        "portfolio.security_lists": ("list_key LIKE $1", [f"{LIST_PREFIX}%"]),
        "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.security_price_coverage": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.data_quality_events": ("global_security_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])",
                                          [secs, runs]),
        "portfolio.provider_call_log": ("run_id = ANY($1::uuid[])", [runs]),
        "portfolio.security_data_runs": ("id = ANY($1::uuid[]) OR summary->>'run_id' = ANY($2::text[]) OR "
                                         "(summary->>'job' = 'security_lists_load' AND summary::text LIKE $3)",
                                         [runs, run_txt, f"%\"{LIST_PREFIX}_%"]),
        "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[]) OR provider_key = $2",
                                                    [secs, ATTR_PROVIDER]),
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, list(USERS)]),
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
    }


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs = [r[0] for r in await op.fetch(
            "SELECT id FROM portfolio.securities_global WHERE name LIKE $1 UNION SELECT global_security_id FROM "
            "portfolio.securities_global_identifiers WHERE id_type = 'ticker' AND id_value LIKE $2",
            f"{NAME_PREFIX}%", f"{TICKER_PREFIX}%")]
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%",
                               plan=counted_plan(secs, list(RUN_IDS)), gucs=(), label="verify_track1")
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


async def leftovers(conn) -> dict:
    secs = await fixture_secs(conn)
    out = {}
    for t, (pred, args) in counted_plan(secs, list(RUN_IDS)).items():
        n = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
        if n:
            out[t] = n
    n = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                           fetch="fetchval"))
    if n:
        out["organizations"] = n
    return out


async def coverage_fp(conn) -> list:
    return sorted((it.gsid, tuple(it.sources)) for it in await P.coverage_set(conn))


async def real_fingerprints(conn) -> dict:
    secs = await fixture_secs(conn)
    return {
        "real tracked_securities": await fingerprint(
            conn, "SELECT id, global_security_id, status, valid_to FROM portfolio.tracked_securities WHERE NOT "
                  "global_security_id = ANY($1::uuid[])", secs),
        "real security_lists": await fingerprint(
            conn, "SELECT id, list_key, valid_to FROM portfolio.security_lists WHERE list_key NOT LIKE $1",
            f"{LIST_PREFIX}%"),
        "real security_list_members": await fingerprint(
            conn, "SELECT id, list_key, valid_to FROM portfolio.security_list_members WHERE list_key NOT LIKE $1",
            f"{LIST_PREFIX}%"),
        "real coverage set (gsid + reasons)": (len(c := await coverage_fp(conn)),
                                               hashlib.md5(json.dumps(c).encode()).hexdigest()),
    }


# ═══ app access + stubs ═════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=600.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_track1"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
    try:
        return res.status_code, json.loads(res.content.decode("utf-8") or "{}")
    except ValueError:
        return res.status_code, {"raw": res.content[:300].decode("utf-8", "replace")}


_REAL_BUDGET = P._budget


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_track1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    for name in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        if hasattr(extraction, name):
            def make(n):
                async def no_llm(*a, **k):
                    LLM_CALLS.append(n)
                    raise AssertionError(f"verify_track1: {n} was called")
                return no_llm
            setattr(extraction, name, make(name))
    register_all()
    J.PLATFORM_ORG_ID = ORG_P            # platform_org() reads it at call time: audit rows land in the fixture org
    P.TRANSPORT_FACTORY.clear()


def pin_budget(n: int | None):
    async def fixed(pool, key, today):
        return n
    P._budget = fixed


def yahoo_body(sym: str) -> tuple[int, str]:
    e = SYN.get(sym)
    if e is None:
        return 404, json.dumps({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}})
    ts = [int(datetime(d_.year, d_.month, d_.day, 14, 30, tzinfo=timezone.utc).timestamp()) for d_, _ in e]
    res = {"meta": {"currency": "USD", "symbol": sym, "gmtoffset": 0, "dataGranularity": "1d"}, "timestamp": ts,
           "indicators": {"quote": [{"close": [str(v) for _, v in e], "open": [str(v) for _, v in e],
                                     "high": [str(v) for _, v in e], "low": [str(v) for _, v in e],
                                     "volume": [1000 for _ in e]}],
                          "adjclose": [{"adjclose": [str(v) for _, v in e]}]}}
    return 200, re.sub(r'"(-?\d+(?:\.\d+)?)"', r"\1", json.dumps({"chart": {"result": [res], "error": None}}))


def recorder() -> RecordedHttpGet:
    t = RecordedHttpGet(responder=lambda url, params: yahoo_body(url.rsplit("/", 1)[-1]))
    S.setdefault("transports", []).append(t)
    return t


def symbols_seen() -> list[str]:
    return [u.rsplit("/", 1)[-1] for t in S.get("transports", []) for u, _ in t.seen]


# ═══ seed ═══════════════════════════════════════════════════════════════════
async def guard_prefix(conn) -> bool:
    n_t = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_type = 'ticker' "
                               "AND id_value LIKE $1", f"{TICKER_PREFIX}%", fetch="fetchval")
    n_n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                         fetch="fetchval")
    n_l = await platform(conn, "SELECT count(*) FROM portfolio.security_lists WHERE list_key LIKE $1",
                         f"{LIST_PREFIX}%", fetch="fetchval")
    n_o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                         fetch="fetchval")
    return check(int(n_t) == 0 and int(n_n) == 0 and int(n_l) == 0 and int(n_o) == 0,
                 "reserved fixture prefixes (tickers ZZT…, names 'ZZT1 VERIFY', lists zzt1_…, fixture orgs) match no "
                 "real row", f"tickers {n_t}, names {n_n}, lists {n_l}, orgs {n_o}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "platform"), (ORG_A, "a")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"TRACK1 Verify {nm}", f"{TAG}-{nm}")
        rid_view = await rbac.ensure_role(conn, ORG_A, "zzt1_viewer", "TRACK1 verify — no permissions")
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{nm}@test.local", full,
                sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname == "zzt1_viewer":
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid_view)
            else:
                await rbac.grant_named_role(conn, uid, org, rname)
        for key, (t, ty) in SECS.items():
            sid = await SG.create_security(conn, name=f"{NAME_PREFIX} {key}", security_type=ty, currency_code="USD",
                                           is_super_admin=True)
            if t:
                await conn.execute(
                    "INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value, "
                    "is_primary) VALUES ($1::uuid, 'ticker', $2, true)", str(sid), t)
            S.setdefault("sec", {})[key] = UUID(str(sid))
        for k in ("E5", "E6"):        # two fixtures on a fixture exchange (the exchange filter's proof)
            await conn.execute(
                "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, provider_key, "
                "as_of) VALUES ($1, 'exchange', 'ZZTX', $2, $3)", S["sec"][k], ATTR_PROVIDER, CLOCK["today"])
    from services import authz as AZ
    flags = await platform(conn, "SELECT (SELECT bool_and(is_test) FROM organizations WHERE id = ANY($1::uuid[])) AS o, "
                                 "(SELECT bool_and(is_test) FROM users WHERE id = ANY($2::uuid[])) AS u",
                           FIXTURE_ORGS, list(USERS), fetch="fetchrow")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        prs = {uid: await AZ.load_principal(conn, uid) for uid in USERS}
    roles_ok = bool(prs[U_SUPER] and prs[U_SUPER].is_super_admin and prs[U_ADMIN_A] and not prs[U_ADMIN_A].is_super_admin
                    and prs[U_MEMBER_A] and not prs[U_MEMBER_A].is_super_admin)
    S["seeded"] = check(bool(flags["o"] is True and flags["u"] is True and len(S.get("sec", {})) == len(SECS) and roles_ok),
                        "fixture orgs / users is_test (asserted); 12 fixture securities; Super Admin is one, the org "
                        "admin and the member are not", f"{dict(flags)} {len(S.get('sec', {}))} roles_ok={roles_ok}")


async def tracked_rows(conn, keys=None) -> list:
    ids = [S["sec"][k] for k in (keys or SECS)]
    return await platform(conn, "SELECT global_security_id, status, list_key, note, actor_id, valid_to FROM "
                                "portfolio.tracked_securities WHERE global_security_id = ANY($1::uuid[]) ORDER BY "
                                "global_security_id, system_from, valid_from", ids)


async def live_status(conn) -> dict:
    inv = {v: k for k, v in S["sec"].items()}
    rows = await platform(conn, "SELECT global_security_id, status FROM portfolio.tracked_securities WHERE "
                                "global_security_id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                          list(S["sec"].values()))
    return {inv[r[0]]: r[1] for r in rows}


async def audit_counts(conn) -> dict:
    rows = await platform(conn, "SELECT action, count(*) AS n FROM audit_log WHERE org_id = $1 AND action LIKE "
                                "'security_data.%' GROUP BY action", ORG_P)
    return {r["action"]: int(r["n"]) for r in rows}


async def all_tracked_count(conn) -> int:
    return int(await platform(conn, "SELECT count(*) FROM portfolio.tracked_securities", fetch="fetchval"))


# ═══ sections ═══════════════════════════════════════════════════════════════
async def y_live(conn):
    section("[L] READ-ONLY live — today's coverage set (before anything is seeded)")
    items = await P.coverage_set(conn)
    tally: dict = {}
    for it in items:
        for s_ in it.sources:
            tally[s_] = tally.get(s_, 0) + 1
    check(len(items) == LIVE_COVERAGE, f"today's coverage set = {LIVE_COVERAGE} (unchanged by the sprint)",
          f"{len(items)} {tally}")
    check("tracked" not in tally, "no real security is tracked: the reason 'tracked' does not appear", f"{tally}")
    n = await all_tracked_count(conn)
    check(n == 0, "portfolio.tracked_securities holds no row (the sprint tracked nothing real)", f"{n}")
    nl = int(await platform(conn, "SELECT count(*) FROM portfolio.security_lists", fetch="fetchval"))
    check(nl == 0, "portfolio.security_lists holds no row (members are loaded by the operator, never the sprint)", f"{nl}")
    find("today's coverage reasons", json.dumps(tally, sort_keys=True))


async def y_gates(conn):
    section("[G] Gates — every TRACK1 route refuses a non-Super-Admin (403), nothing written")
    before = await all_tracked_count(conn)
    aud0 = await audit_counts(conn)
    body = {"ids": [sec("E1")], "note": "gate probe", "expected_total": 1}
    for uid, who in ((U_ADMIN_A, "org admin"), (U_MEMBER_A, "member")):
        for method, path, b in (("GET", f"{P_}/tracked", None),
                                ("POST", f"{P_}/tracked/estimate", {"ids": [sec("E1")]}),
                                ("POST", f"{P_}/tracked/track", body),
                                ("POST", f"{P_}/tracked/track", {"filters": {"q": NAME_PREFIX}, "note": "x"}),
                                ("POST", f"{P_}/tracked/track", {"list_key": f"{LIST_PREFIX}_fx"}),
                                ("POST", f"{P_}/tracked/untrack", {**body, "note": "x"}),
                                ("GET", f"{P_}/securities?tracked=true", None)):
            c, o = await api(uid, method, path, b)
            check(c == 403, f"{who}: {method} {path.replace(P_, '…')} → 403", f"{c} {str(o)[:160]}")
    check(await all_tracked_count(conn) == before and await audit_counts(conn) == aud0,
          "after every refusal: tracked rows and audit rows unchanged (before/after)")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {**body, "org_id": str(ORG_A)})
    check(c == 422, "a body carrying org_id is refused (extra='forbid'); tracked securities are platform-wide", f"{c}")
    c, o = await api(U_SUPER, "GET", f"{P_}/tracked")
    pm = o.get("permissions") or {}
    check(c == 200 and pm.get("can_track") is True and pm.get("can_untrack") is True and
          o.get("vocabularies", {}).get("track_cap") == TR.TRACK_CAP and "tracked" in o["vocabularies"]["filters"],
          "Super Admin: GET /tracked 200 with can_track / can_untrack true, the cap and the filter names",
          f"{c} {pm}")


async def y_track(conn):
    section("[T] Track / untrack — coverage reason 'tracked', history kept, audited")
    aud0 = await audit_counts(conn)
    cov0 = {it.gsid: it.sources for it in await P.coverage_set(conn)}
    check(sec("E1") not in cov0 and sec("E2") not in cov0, "before: E1 / E2 are not in the coverage set")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"ids": [sec("E1"), sec("E2")]})
    check(c == 422 and await live_status(conn) == {}, "track without a note (and no list) → 422, nothing written",
          f"{c} {o}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"ids": [sec("E1"), sec("E2")], "note": "  ",
                                                               "expected_total": 3})
    check(c == 409 and await live_status(conn) == {}, "a confirmed count that does not match the ids → 409, nothing "
          "written", f"{c} {o}")
    ghost = str(uuid.uuid4())
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"ids": [sec("E1"), sec("E2"), ghost],
                                                               "note": "core watch", "expected_total": 3})
    r = o.get("result") or {}
    check(c == 200 and r.get("tracked") == 2 and r.get("already_tracked") == 0 and r.get("unknown") == [ghost],
          "track E1 + E2 + an unknown id → 2 tracked, the unknown id REPORTED (never invented)", f"{c} {r}")
    rows = await tracked_rows(conn, ["E1", "E2"])
    check(len(rows) == 2 and all(x["status"] == "tracked" and x["note"] == "core watch" and x["actor_id"] == U_SUPER
                                 and x["valid_to"] is None for x in rows),
          "re-read: one live 'tracked' row each, with who (the Super Admin) and why (the note)", f"{rows}")
    n_ghost = int(await platform(conn, "SELECT count(*) FROM portfolio.tracked_securities WHERE global_security_id = "
                                       "$1::uuid", ghost, fetch="fetchval"))
    check(n_ghost == 0, "no row for the unknown id")
    cov = {it.gsid: it.sources for it in await P.coverage_set(conn)}
    check("tracked" in cov.get(sec("E1"), []) and "tracked" in cov.get(sec("E2"), []),
          "coverage_set now includes E1 and E2 with the reason 'tracked'", f"{cov.get(sec('E1'))} {cov.get(sec('E2'))}")
    other = {g: s for g, s in cov.items() if g not in (sec("E1"), sec("E2"))}
    check(other == cov0, "every other member of the coverage set and its reasons unchanged (positions / benchmarks …)")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"ids": [sec("E1"), sec("E2")], "note": "again"})
    r = o.get("result") or {}
    check(c == 200 and r.get("tracked") == 0 and r.get("already_tracked") == 2 and len(await tracked_rows(conn, ["E1", "E2"])) == 2,
          "tracking again: 0 tracked, 2 already tracked, still ONE row each (no duplicate)", f"{r}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/untrack", {"ids": [sec("E1")]})
    check(c == 422, "untrack without a note → 422", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/untrack", {"ids": [sec("E1"), sec("E3")], "note": "no longer needed",
                                                                 "expected_total": 2})
    r = o.get("result") or {}
    check(c == 200 and r.get("untracked") == 1 and r.get("not_tracked") == 1, "untrack E1 (+ E3, never tracked) → 1 "
          "untracked, 1 not tracked", f"{c} {r}")
    rows = await tracked_rows(conn, ["E1"])
    check(len(rows) == 2 and rows[0]["status"] == "tracked" and rows[0]["valid_to"] is not None and
          rows[1]["status"] == "untracked" and rows[1]["valid_to"] is None and rows[1]["note"] == "no longer needed"
          and rows[1]["actor_id"] == U_SUPER,
          "history kept: the 'tracked' row closed (valid_to set), a live 'untracked' row with who and why",
          f"{[dict(x) for x in rows]}")
    cov = {it.gsid: it.sources for it in await P.coverage_set(conn)}
    check(sec("E1") not in cov and "tracked" in cov.get(sec("E2"), []), "coverage_set: E1 excluded, E2 still tracked")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"ids": [sec("E1")], "note": "back again"})
    rows = await tracked_rows(conn, ["E1"])
    check(c == 200 and len(rows) == 3 and [x["status"] for x in rows] == ["tracked", "untracked", "tracked"] and
          sum(1 for x in rows if x["valid_to"] is None) == 1, "re-tracking restates again: 3 rows, exactly one live",
          f"{[(x['status'], x['valid_to']) for x in rows]}")
    aud = await audit_counts(conn)
    d_t = aud.get("security_data.tracked", 0) - aud0.get("security_data.tracked", 0)
    d_u = aud.get("security_data.untracked", 0) - aud0.get("security_data.untracked", 0)
    check(d_t == 3 and d_u == 1, "audit_log: one 'security_data.tracked' row per track action (3) and one "
          "'security_data.untracked' (1), on the platform org, refusals unaudited", f"{aud0} → {aud}")
    row = await platform(conn, "SELECT user_id, payload FROM audit_log WHERE org_id = $1 AND action = "
                               "'security_data.untracked' ORDER BY created_at DESC LIMIT 1", ORG_P, fetch="fetchrow")
    pl = json.loads(row["payload"]) if row and isinstance(row["payload"], str) else (row["payload"] if row else {})
    check(bool(row and row["user_id"] == U_SUPER and pl.get("ids") == [sec("E1")] and pl.get("note") == "no longer needed"),
          "the audit row names the actor, the ids and the note", f"{row and dict(row)}")


async def y_filter(conn):
    section("[F] Bulk by filter — the master's own predicates; count = the filter's count; inclusion AND exclusion; cap")
    funds = {"q": NAME_PREFIX, "security_type": "fund"}
    sql_n = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global s WHERE s.valid_to IS NULL AND "
                                     "s.system_to IS NULL AND s.merged_into_id IS NULL AND s.security_type = 'fund' AND "
                                     "s.name ILIKE $1", f"%{NAME_PREFIX}%", fetch="fetchval"))
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/estimate", {"filters": funds})
    c2, l2 = await api(U_SUPER, "GET", f"{P_}/securities?q={NAME_PREFIX.replace(' ', '%20')}&security_type=fund")
    check(c == 200 and o.get("total") == sql_n == 3 and l2.get("total") == 3 and o.get("over_cap") is False and
          "ids" not in o, "estimate: the filter's count = the list's total = the same predicate in SQL (3); no ids "
          "returned", f"{c} {o.get('total')} sql={sql_n} list={l2.get('total')}")
    before = await live_status(conn)
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"filters": funds, "note": "fund sleeve", "expected_total": 2})
    check(c == 409 and "3" in str(o) and await live_status(conn) == before,
          "a stale confirmed count (2 ≠ 3) → 409 naming the count; nothing written", f"{c} {o}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"filters": funds, "note": "fund sleeve", "expected_total": 3})
    st = await live_status(conn)
    check(c == 200 and (o.get("result") or {}).get("tracked") == 3 and all(st.get(k) == "tracked" for k in ("F1", "F2", "F3")),
          "track by filter (confirmed 3) → F1..F3 tracked", f"{c} {o.get('result')}")
    eq_untouched = all(st.get(k) == before.get(k) for k in SECS if SECS[k][1] == "equity")
    check(eq_untouched, "EXCLUSION: no fixture equity changed by the fund filter", f"{before} → {st}")
    q = NAME_PREFIX.replace(" ", "%20")
    _, yes = await api(U_SUPER, "GET", f"{P_}/securities?q={q}&tracked=true")
    _, no = await api(U_SUPER, "GET", f"{P_}/securities?q={q}&tracked=false")
    _, all_ = await api(U_SUPER, "GET", f"{P_}/securities?q={q}")
    n_tracked = sum(1 for v in st.values() if v == "tracked")
    ids_yes = {r["id"] for r in yes.get("rows", [])}
    check(yes.get("total") == n_tracked and yes.get("total", 0) + no.get("total", 0) == all_.get("total") == len(SECS)
          and ids_yes == {sec(k) for k, v in st.items() if v == "tracked"} and all(r.get("tracked") is True for r in yes["rows"])
          and all(r.get("tracked") is False for r in no.get("rows", [])),
          "the tracked filter: tracked=true is exactly the tracked fixtures, tracked=false the rest, together all 12",
          f"yes={yes.get('total')} no={no.get('total')} all={all_.get('total')} tracked={n_tracked}")
    _, ex = await api(U_SUPER, "GET", f"{P_}/securities?exchange=ZZTX")
    check({r["id"] for r in ex.get("rows", [])} == {sec("E5"), sec("E6")} and ex.get("total") == 2,
          "the exchange filter (reference attribute) selects exactly the two fixtures on it", f"{ex.get('total')}")
    _, tk = await api(U_SUPER, "GET", f"{P_}/securities?q=ZZTAM")
    check({r["id"] for r in tk.get("rows", [])} == {sec("AMB1"), sec("AMB2")}, "q matches a ticker prefix (dailyops1's "
          "search predicate)", f"{[r['name'] for r in tk.get('rows', [])]}")
    # the cap — a REAL filter far above it (refused, nothing written), then a lowered cap on fixtures
    n_eq = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global s WHERE s.valid_to IS NULL AND "
                                    "s.system_to IS NULL AND s.merged_into_id IS NULL AND s.security_type = 'equity'",
                              fetch="fetchval"))
    t0 = await all_tracked_count(conn)
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/estimate", {"filters": {"security_type": "equity"}})
    check(c == 200 and o.get("total") == n_eq and o.get("over_cap") is True and o.get("estimate") is None and n_eq > TR.TRACK_CAP,
          f"estimate on every equity ({n_eq:,}): over_cap true, the count = SQL's, no estimate, no ids", f"{c} {o.get('total')}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"filters": {"security_type": "equity"}, "note": "x",
                                                               "expected_total": n_eq})
    check(c == 409 and f"{n_eq:,}" in str(o) and f"{TR.TRACK_CAP:,}" in str(o) and await all_tracked_count(conn) == t0,
          "track every equity → 409 naming the count and the cap; tracked rows unchanged (no real security tracked)",
          f"{c} {o}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"filters": {"q": " "}, "note": "x"})
    check(c in (409, 422) and await all_tracked_count(conn) == t0, "an empty filter → refused (it would select the "
          "whole master)", f"{c} {o}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"filters": {"org_id": "x"}, "note": "x"})
    check(c == 422, "an unknown filter name → 422", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"ids": [str(uuid.uuid4()) for _ in range(3001)], "note": "x"})
    check(c == 422 and await all_tracked_count(conn) == t0, "3,001 ids → 422 (the cap on ids)", f"{c}")
    saved = TR.TRACK_CAP
    TR.TRACK_CAP = 2
    try:
        c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"filters": {"q": NAME_PREFIX}, "note": "x",
                                                                   "expected_total": len(SECS)})
        check(c == 409 and "at most 2" in str(o) and await all_tracked_count(conn) == t0,
              "with the cap lowered to 2: the 12-fixture filter → 409 'at most 2', nothing written", f"{c} {o}")
    finally:
        TR.TRACK_CAP = saved
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/untrack", {"filters": funds, "note": "sleeve closed",
                                                                 "expected_total": 3})
    st = await live_status(conn)
    check(c == 200 and (o.get("result") or {}).get("untracked") == 3 and all(st.get(k) == "untracked" for k in ("F1", "F2", "F3")),
          "untrack by filter (confirmed 3) → F1..F3 untracked", f"{c} {o.get('result')}")


async def y_estimate(conn):
    section("[E] Estimate — calls / days at the budget / nights / upkeep, recomputed by hand")
    ids = [sec("E3"), sec("E4"), sec("NT")]
    cov = {it.gsid for it in await P.coverage_set(conn)}
    budget = int(await platform(conn, "SELECT daily_call_budget FROM portfolio.data_providers WHERE key = 'yahoo_chart' "
                                      "AND valid_to IS NULL AND system_to IS NULL", fetch="fetchval"))
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/estimate", {"ids": ids})
    e = o.get("estimate") or {}
    exp_after = len(cov | set(ids))
    want = {"provider": "yahoo_chart", "selected": 3, "to_load": 3, "estimated_calls": 3, "daily_budget": budget,
            "days_at_budget": math.ceil(3 / budget), "nights_nightly_only": 1, "coverage_now": len(cov),
            "coverage_after": exp_after, "upkeep_calls_per_day": exp_after, "nightly_capacity": 128}
    bad = {k: (e.get(k), v) for k, v in want.items() if e.get(k) != v}
    check(c == 200 and o.get("total") == 3 and not bad, "estimate for 3 unpriced, uncovered fixtures = the hand "
          "computation (3 calls, 1 day at the real budget, 1 night, coverage +3, upkeep = coverage)", f"{c} {bad}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/estimate", {"ids": [sec("E2")], "removing": True})
    e = o.get("estimate") or {}
    check(c == 200 and e.get("coverage_after") == e.get("coverage_now", 0) - 1 and e.get("to_load") == 0,
          "untrack estimate for E2 (tracked, no other reason): coverage −1, nothing to load", f"{e}")


async def _load_mod():
    spec = importlib.util.spec_from_file_location("security_lists_load_v", HERE / "security_lists_load.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture_list_file() -> pathlib.Path:
    doc = {"version": 1, "lists": [
        {"key": f"{LIST_PREFIX}_fx", "name": "ZZT1 fixture list", "source": {"kind": "curated"}, "overlap_note": "none",
         "members": [{"ticker": "ZZTC", "description": "fixture C"}, {"ticker": "ZZTD", "description": "fixture D"},
                     {"ticker": "ZZTAMB", "description": "two securities share this ticker"},
                     {"ticker": "ZZTNOPE", "description": "not in the master"}]},
        {"key": f"{LIST_PREFIX}_idx", "name": "ZZT1 fixture index", "source": {"kind": "index_holdings",
                                                                               "fund_ticker": "ZZTF1",
                                                                               "holdings_source": "sec_nport"}}]}
    p = pathlib.Path(tempfile.mkdtemp(prefix="track1_")) / "lists.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


async def y_lists(conn):
    section("[D] List loader — dry run (no network, nothing written), a real curated load, re-run, ticker fallback")
    mod = await _load_mod()
    path = fixture_list_file()
    pool = await get_pool()
    out: list[str] = []
    snap0 = (await all_tracked_count(conn), await fingerprint(conn, "SELECT * FROM portfolio.security_lists"),
             await fingerprint(conn, "SELECT * FROM portfolio.security_list_members"))
    net0 = len(NETWORK_CALLS)
    args = argparse.Namespace(lists=None, all=True, dry_run=True, file=str(path), no_track=False, show_misses=25)
    rep = await mod.run(args, pool=pool, printer=out.append)
    fx = rep["lists"].get(f"{LIST_PREFIX}_fx") or {}
    misses = {m.get("ticker"): m["reason"] for m in fx.get("miss_list", [])}
    check(fx.get("members") == 2 and fx.get("misses") == 2 and "ambiguous" in misses.get("ZZTAMB", "") and
          misses.get("ZZTNOPE") == "not in the security master" and fx.get("not_yet_tracked") == 2,
          "dry run: 2 hits (ZZTC, ZZTD); ZZTNOPE reported missing; ZZTAMB reported AMBIGUOUS (two securities) — "
          "never picked, never created", f"{fx.get('members')} {misses}")
    est = fx.get("estimate") or {}
    check(est.get("to_load") == 2 and est.get("estimated_calls") == 2, "dry run estimate: 2 to load = 2 calls", f"{est}")
    ix = rep["lists"].get(f"{LIST_PREFIX}_idx") or {}
    check(ix.get("members") == 0 and "would fetch" in str(ix.get("note")) and len(NETWORK_CALLS) == net0,
          "dry run, index list without a snapshot: says where the real run would fetch from; NO network call",
          f"{ix} net={NETWORK_CALLS[net0:]}")
    snap1 = (await all_tracked_count(conn), await fingerprint(conn, "SELECT * FROM portfolio.security_lists"),
             await fingerprint(conn, "SELECT * FROM portfolio.security_list_members"))
    check(snap1 == snap0 and rep.get("exit") == 0, "dry run wrote NOTHING (tracked / lists / members fingerprints equal)",
          f"{snap0} → {snap1}")
    check(any("dry run: nothing fetched, nothing written" in x for x in out), "the dry run says so")
    # the real load of the curated list (no network needed)
    aud0 = await audit_counts(conn)
    args = argparse.Namespace(lists=[f"{LIST_PREFIX}_fx"], all=False, dry_run=False, file=str(path), no_track=False,
                              show_misses=25)
    rep = await mod.run(args, pool=pool, printer=out.append)
    w = (rep["lists"].get(f"{LIST_PREFIX}_fx") or {}).get("written") or {}
    lst = await platform(conn, "SELECT * FROM portfolio.security_lists WHERE list_key = $1 AND valid_to IS NULL AND "
                               "system_to IS NULL", f"{LIST_PREFIX}_fx", fetch="fetchrow")
    mem = await platform(conn, "SELECT global_security_id, matched_by FROM portfolio.security_list_members WHERE "
                               "list_key = $1 AND valid_to IS NULL AND system_to IS NULL", f"{LIST_PREFIX}_fx")
    st = await live_status(conn)
    trk = await platform(conn, "SELECT global_security_id, list_key FROM portfolio.tracked_securities WHERE "
                               "global_security_id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                         [S["sec"]["E3"], S["sec"]["E4"]])
    check(rep.get("exit") == 0 and lst is not None and lst["member_count"] == 2 and lst["source_kind"] == "curated" and
          len(json.loads(lst["misses"]) if isinstance(lst["misses"], str) else lst["misses"]) == 2,
          "real load: the list row (2 members, 2 misses recorded, curated)", f"{rep.get('exit')} {lst and dict(lst)}")
    check({r[0] for r in mem} == {S["sec"]["E3"], S["sec"]["E4"]} and all(r[1] == "ticker" for r in mem),
          "members = E3, E4 (matched by ticker)")
    check(st.get("E3") == "tracked" and st.get("E4") == "tracked" and all(r[1] == f"{LIST_PREFIX}_fx" for r in trk),
          "the members are tracked, each tracked row naming the list", f"{st} {[tuple(r) for r in trk]}")
    aud = await audit_counts(conn)
    check(aud.get("security_data.list_loaded", 0) - aud0.get("security_data.list_loaded", 0) == 1 and
          aud.get("security_data.tracked", 0) - aud0.get("security_data.tracked", 0) == 1,
          "audited: one list_loaded + one tracked row", f"{aud0} → {aud}")
    run_row = await platform(conn, "SELECT id, status, summary FROM portfolio.security_data_runs WHERE summary->>'job' = "
                                   "'security_lists_load' AND summary::text LIKE $1", f"%{LIST_PREFIX}_fx%")
    for r in run_row:
        RUN_IDS.append(r["id"])
    check(len(run_row) == 1 and run_row[0]["status"] == "success", "one job-history row (security_lists_load, success)",
          f"{[(r['status']) for r in run_row]}")
    rep2 = await mod.run(args, pool=pool, printer=out.append)
    w2 = (rep2["lists"].get(f"{LIST_PREFIX}_fx") or {}).get("written") or {}
    n_rows = int(await platform(conn, "SELECT count(*) FROM portfolio.security_lists WHERE list_key = $1",
                                f"{LIST_PREFIX}_fx", fetch="fetchval"))
    check((w2.get("stored") or {}).get("list_restated") is False and (w2.get("stored") or {}).get("joined") == 0 and
          (w2.get("tracked") or {}).get("already_tracked") == 2 and n_rows == 1,
          "re-run: nothing restated (one list row), 0 joined, both already tracked", f"{w2} rows={n_rows}")
    for r in await platform(conn, "SELECT id FROM portfolio.security_data_runs WHERE summary->>'job' = "
                                  "'security_lists_load' AND summary::text LIKE $1", f"%{LIST_PREFIX}_fx%"):
        if r["id"] not in RUN_IDS:
            RUN_IDS.append(r["id"])
    c, o = await api(U_SUPER, "GET", f"{P_}/tracked")
    row = next((x for x in o.get("lists", []) if x["list_key"] == f"{LIST_PREFIX}_fx"), {})
    check(c == 200 and row.get("member_count") == 2 and row.get("tracked") == 2 and row.get("miss_count") == 2,
          "GET /tracked lists the fixture list: 2 members, 2 tracked, 2 not found", f"{row}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/estimate", {"list_key": f"{LIST_PREFIX}_fx"})
    check(c == 200 and o.get("total") == 2, "Track list…: the estimate counts the list's members (2)", f"{c} {o.get('total')}")
    c, o = await api(U_SUPER, "POST", f"{P_}/tracked/track", {"list_key": f"{LIST_PREFIX}_fx", "expected_total": 2})
    check(c == 200 and (o.get("result") or {}).get("already_tracked") == 2, "Track list… (confirmed 2): both already "
          "tracked — no duplicate", f"{c} {o.get('result')}")
    # the index ticker fallback (rows as a holdings file gives them; no network)
    rows = [RP.HoldingRow(row_num=1, name="Foreign-domiciled plc", ticker="ZZTE", cusip="G0000ZZT1", isin=None,
                          sedol=None, weight=D("0.01"), shares=None, market_value=None, currency="USD", asset_type="equity"),
            RP.HoldingRow(row_num=2, name="Cash", ticker=None, cusip=None, isin=None, sedol=None, weight=D("0.001"),
                          shares=None, market_value=None, currency="USD", asset_type="cash"),
            RP.HoldingRow(row_num=3, name="Nobody", ticker="ZZTNOPE", cusip="G0000ZZT2", isin=None, sedol=None,
                          weight=None, shares=None, market_value=None, currency="USD", asset_type="equity"),
            RP.HoldingRow(row_num=4, name="Ambiguous", ticker="ZZTAMB", cusip=None, isin=None, sedol=None, weight=None,
                          shares=None, market_value=None, currency="USD", asset_type="equity")]
    r = SL.Resolved("zzt1_probe", "probe", None, "index_holdings", "probe", None)
    async with pool.acquire() as c_:
        async with platform_scope(c_):
            await SL._members_from_rows(c_, r, rows)
    check([m["gsid"] for m in r.members] == [sec("E5")] and r.members[0]["matched_by"] == "ticker" and
          r.ticker_fallback == 1 and {m.get("ticker") for m in r.misses} == {"ZZTNOPE", "ZZTAMB"},
          "index rows: an unknown CUSIP falls back to a known ticker (1); cash skipped; unknown and ambiguous → misses",
          f"{r.members} {r.misses} fb={r.ticker_fallback}")
    bad = pathlib.Path(tempfile.mkdtemp(prefix="track1_")) / "bad.json"
    bad.write_text(json.dumps({"lists": [{"key": "Bad Key", "name": "", "source": {"kind": "curated"},
                                          "members": [{"ticker": "A", "description": ""}, {"ticker": "A",
                                                                                          "description": "x"}]}]}))
    try:
        SL.load_definitions(bad)
        msg = ""
    except SL.ListFileError as exc:
        msg = str(exc)
    check(all(x in msg for x in ("key must match", "no name", "a one-line description", "A listed twice")),
          "a bad data file is refused naming every problem", msg[:300])


async def y_backfill(conn):
    section("[B] The prices backfill dry run counts tracked securities")
    items = await P.coverage_set(conn)
    st = await live_status(conn)
    n_tr = sum(1 for v in st.values() if v == "tracked")
    by: dict = {}
    for it in items:
        for s_ in it.sources:
            by[s_] = by.get(s_, 0) + 1
    check(by.get("tracked") == n_tr and n_tr > 0, f"coverage_set reasons: 'tracked' = the live tracked fixtures ({n_tr})",
          f"{by}")
    prov = L.code_registry()["yahoo_chart"]
    net0 = len(NETWORK_CALLS)
    res = await P.backfill(await get_pool(), prov, items, dry_run=True, today=CLOCK["today"], respect_budget=False)
    check(res.get("dry_run") is True and res.get("coverage_set") == len(items) and res.get("estimated_calls") == len(items)
          and len(NETWORK_CALLS) == net0, "backfill --dry-run plans the whole coverage set incl. the tracked ones "
          "(calls = securities at 1 / call), no network", f"{ {k: res.get(k) for k in ('coverage_set', 'estimated_calls')} }")


async def y_nightly(conn):
    section("[N] The nightly prices job picks tracked securities up (stub provider); stalest-first; progress")
    today = CLOCK["today"]
    a, b = sorted(["E1", "E2"], key=lambda k: sec(k), reverse=True)     # a = the LARGER gsid
    SYN.clear()
    SYN[SECS[a][0]] = [(d_, D("10") + D(i) / 100) for i, d_ in enumerate(weekdays_back(today - timedelta(days=21), 20))]
    # b ends the weekday BEFORE today (the adapter may hold back today's bar before the close — real time)
    SYN[SECS[b][0]] = [(d_, D("20") + D(i) / 100) for i, d_ in
                       enumerate(weekdays_back(last_weekday(today - timedelta(days=1)), 20))]
    st = await live_status(conn)
    check(st.get("E1") == "tracked" and st.get("E2") == "tracked", "set-up: E1 and E2 are tracked", f"{st}")
    P.TRANSPORT_FACTORY["yahoo_chart"] = recorder
    pin_budget(100)
    try:
        rid = uuid.uuid4()
        RUN_IDS.append(rid)
        items = {it.gsid: it.sources for it in await P.coverage_set(conn)}
        check("tracked" in items.get(sec(a), []) and "tracked" in items.get(sec(b), []),
              "the nightly's own coverage read (coverage_set) holds both with the reason 'tracked'")
        summ = await P.nightly(await get_pool(), today=today, run_id=rid, only_gsids=[sec(a), sec(b)],
                               keys=["yahoo_chart"])
        nh = summ["providers"]["yahoo_chart"]["new_history"]
        n_a = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE global_security_id = "
                                       "$1 AND provider_key = 'yahoo_chart' AND valid_to IS NULL AND system_to IS NULL",
                                 S["sec"][a], fetch="fetchval"))
        n_b = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE global_security_id = "
                                       "$1 AND provider_key = 'yahoo_chart' AND valid_to IS NULL AND system_to IS NULL",
                                 S["sec"][b], fetch="fetchval"))
        check(nh.get("found") == 2 and n_a == 20 and n_b == 20, "first night: full history for both new tracked members "
              "(20 closes each stored)", f"{nh} a={n_a} b={n_b}")
        tp = summ.get("tracked_progress") or {}
        exp = int(await platform(conn, "SELECT count(*) FROM portfolio.tracked_securities t WHERE t.status = 'tracked' AND "
                                       "t.valid_to IS NULL AND t.system_to IS NULL AND EXISTS (SELECT 1 FROM "
                                       "portfolio.securities_global_prices p WHERE p.global_security_id = "
                                       "t.global_security_id AND p.org_id IS NULL AND p.price_type = 'close' AND "
                                       "p.valid_to IS NULL AND p.system_to IS NULL)", fetch="fetchval"))
        check(tp.get("loaded") == exp and tp.get("label") == f"{exp:,} of {tp.get('tracked', 0):,} loaded" and exp >= 2,
              "the job summary carries the progress line ('N of M loaded') = SQL", f"{tp} sql={exp}")
        # stalest-first: a's latest close is ~3 weeks old, b's is today; ONE call of budget
        S["transports"] = []
        pin_budget(1)
        rid2 = uuid.uuid4()
        RUN_IDS.append(rid2)
        await P.nightly(await get_pool(), today=today, run_id=rid2, only_gsids=[sec(a), sec(b)], keys=["yahoo_chart"])
        seen = symbols_seen()
        old_order = sorted([sec(a), sec(b)])[0]
        check(old_order == sec(b), "REPRODUCED: the old gsid order would have refreshed b (the FRESH one) first",
              f"{old_order}")
        check(seen == [SECS[a][0]], "FIXED: with one call of budget the gap fill refreshes the STALEST (a), not the "
              "first by gsid", f"{seen}")
    finally:
        P.TRANSPORT_FACTORY.clear()
        P._budget = _REAL_BUDGET


async def y_market(conn):
    section("[M] Market Indicators search — the tracking block (Super Admin may Track; others see why not)")
    path = f"/api/v1/market/securities/search?q={NAME_PREFIX.replace(' ', '%20')}"
    st = await live_status(conn)
    c, o = await api(U_SUPER, "GET", path)
    t = o.get("tracking") or {}
    ids = {s_["id"] for s_ in o.get("securities", [])}
    want_tracked = {sec(k) for k, v in st.items() if v == "tracked"} & ids
    check(c == 200 and t.get("can_track") is True and t.get("disabled_reason") is None and set(t.get("tracked", [])) ==
          want_tracked and "market series" in str(t.get("note")), "Super Admin: can_track, which results are tracked "
          "(= SQL), and the note that tracking does not by itself make a security selectable", f"{c} {t}")
    c, o = await api(U_MEMBER_A, "GET", path)
    t = o.get("tracking") or {}
    check(c == 200 and t.get("can_track") is False and "Super Admin" in str(t.get("disabled_reason")),
          "a member: can_track false with the disabled reason", f"{c} {t}")


async def y_rls(conn):
    section("[R] RLS — readable by all, writable only under app.is_super_admin")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "the verify's connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
    for t in ("tracked_securities", "security_lists", "security_list_members"):
        err = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                                   "set_config('app.current_org_id', $1, true)", str(ORG_A))
                await conn.fetchval(f"SELECT count(*) FROM portfolio.{t}")
                if t == "tracked_securities":
                    await conn.execute("INSERT INTO portfolio.tracked_securities (global_security_id, status, note) "
                                       "VALUES ($1, 'tracked', 'rls probe')", S["sec"]["E6"])
                elif t == "security_lists":
                    await conn.execute("INSERT INTO portfolio.security_lists (list_key, name, source_kind) VALUES "
                                       "('zzt1_rls', 'x', 'curated')")
                else:
                    await conn.execute("INSERT INTO portfolio.security_list_members (list_key, global_security_id, "
                                       "matched_by) VALUES ('zzt1_rls', $1, 'ticker')", S["sec"]["E6"])
        except asyncpg.PostgresError as exc:
            err = type(exc).__name__
        check(err == "InsufficientPrivilegeError",
              f"{t}: an INSERT without app.is_super_admin is refused by RLS", f"{err}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
        n = await conn.fetchval("WITH u AS (UPDATE portfolio.tracked_securities SET note = 'x' WHERE global_security_id "
                                "= $1 RETURNING 1) SELECT count(*) FROM u", S["sec"]["E2"])
        seen = await conn.fetchval("SELECT count(*) FROM portfolio.tracked_securities WHERE global_security_id = $1",
                                   S["sec"]["E2"])
    check(int(n) == 0 and int(seen) >= 1, "without it: the rows are READABLE but an UPDATE matches zero rows",
          f"updated {n}, visible {seen}")


def y_render():
    section("[W] Render tests (node --test tests/track1/ + the market and security-master suites)")
    p = subprocess.run(["node", "--test", "tests/track1/", "tests/market/dailyops1Render.test.mjs",
                        "tests/secmaster2/", "tests/catchall1/"], cwd=WEB_DIR, capture_output=True, text=True,
                       timeout=600)
    m = re.search(r"^# pass (\d+)$", p.stdout, flags=re.M)
    f = re.search(r"^# fail (\d+)$", p.stdout, flags=re.M)
    check(p.returncode == 0 and m is not None and f is not None and f.group(1) == "0" and int(m.group(1)) >= 7,
          "render tests: the Tracked view (fails closed), confirm text from the server's estimate, runBulk, the market "
          "Track offer; dailyops1 / secmaster2 / catchall1 suites still pass",
          f"pass={m.group(1) if m else None} fail={f.group(1) if f else None} {p.stderr[-300:]}")


def regression():
    section("[J] Regressions — each ONCE, flat, after teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


# ═══ driver ═════════════════════════════════════════════════════════════════
SECTIONS = {"l": "live", "g": "gates", "t": "track", "f": "filter", "e": "estimate", "d": "lists", "b": "backfill",
            "n": "nightly", "m": "market", "r": "rls", "w": "render", "j": "regressions"}
#: a section's set-up: what must have run before it (each section can run alone with `--section`)
NEEDS = {"f": ("t",), "e": ("t",), "b": ("t",), "n": ("t",), "m": ("t",), "r": ("t",)}


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
    check(not left, "--cleanup-only: zero fixture rows remain", f"{left}")
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
    conn = await connect(dsn)
    db_today = await conn.fetchval("SELECT (now() AT TIME ZONE 'UTC')::date")
    CLOCK["today"] = last_weekday(db_today)
    print(f"[INFO] database via {how}; TODAY = {CLOCK['today'].isoformat()} (the database's UTC date, last weekday)")
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
    want = (lambda k: True) if only is None else (lambda k: k == only or k in NEEDS.get(only, ()))
    if only == "w":
        y_render()
    if only in ("w", "j"):
        await close_conn(conn)
        if only == "j":
            regression()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    pre = real = None
    try:
        await run("pre-teardown", teardown_retrying)
        left0 = await leftovers(conn)
        check(not left0, "pre-run cleanup: zero fixture rows before seeding", f"{left0}")
        if want("l"):
            await run("live", y_live, conn)
        if not await guard_prefix(conn):
            check(False, "seeding needs the reserved prefixes free", "every fixture section skipped")
        else:
            secs0 = await fixture_secs(conn)
            pre = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {p}", *a, fetch="fetchval"))
                   for t, (p, a) in counted_plan(secs0, []).items()}
            real = await real_fingerprints(conn)
            await run("seed", seed, conn)
            if not S.get("seeded"):
                check(False, "seed completed", "every fixture section skipped")
            else:
                for k, fn in (("g", y_gates), ("t", y_track), ("f", y_filter), ("e", y_estimate), ("d", y_lists),
                              ("b", y_backfill), ("n", y_nightly), ("m", y_market), ("r", y_rls)):
                    if want(k):
                        await run(SECTIONS[k], fn, conn)
        if only is None:
            y_render()
        check(NETWORK_CALLS == [], "no network call was attempted", f"{NETWORK_CALLS[:3]}")
        check(LLM_CALLS == [], "no LLM helper was called", f"{LLM_CALLS[:3]}")
    finally:
        section("Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(not left, "zero fixture rows remain", f"{left}")
            if pre is not None:
                secs1 = await fixture_secs(conn)
                post = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {p}", *a, fetch="fetchval"))
                        for t, (p, a) in counted_plan(secs1, []).items()}
                for t in pre:
                    check(post[t] == pre[t], f"{t} at its pre-test count (by fixture keys)", f"{pre[t]} → {post[t]}")
            if real is not None:
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"{k} unchanged after teardown (count + checksum)",
                          f"{real[k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
    if only is None:
        if os.environ.get("VERIFY_NESTED") == "1":
            print("[SKIP] nested run")
        else:
            regression()
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
