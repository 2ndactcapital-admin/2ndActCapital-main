"""verify_prices2 — the nightly price refresh keeps the WHOLE coverage set current: the day's refresh resumes across
scheduler ticks (one portfolio.price_refresh_days row per day, a lease, progress) until every covered security is
refreshed for the day or the source's daily call budget is spent; priority + oldest-first under the budget; the
Security master line "covered N · refreshed today M · oldest price date D"; track1's warning per day, not per run.

    timeout 600 python3 apps/api/scripts/verify_prices2.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_prices2.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_prices2.py --section d      # seed + ONE section + teardown
    python3 apps/api/scripts/verify_prices2.py                              # everything + fast regressions

Sections: d day 1 / day 2 at scale (2,600 covered, budget 2,000: priority order across several ticks, one row a day,
progress, budget stop; day 2 starts with the 600 left, oldest first) · m multi-symbol call counting (a 50-per-call
stub: ⌈N/50⌉ requests = the call log = the day row) · x failures, deadline, race (a tick past its deadline stops
cleanly and the next resumes; a source failure is recorded and never raised into the tick; 3 in a row stop the day;
the job step still raises; two ticks at once → one works; the tick never starts a day; job switched off → nothing)
· r the REAL store end to end (8 securities, budget 5: prices stored for the 5, the screen numbers = SQL, day 2 takes
the 3 left first) · w render test (node) · j fast regressions (verify_prices1, verify_track1, verify_secmaster2 —
--self-checks-only, VERIFY_REGRESSIONS=fast).

NO NETWORK AND NO MODEL CALL: httpx's transport refuses and records; the price source is a STUB adapter
('zzp2_stub' / 'zzp2_stub50', an EodPriceProvider subclass) on eod.RecordedHttpGet — calls are counted by the same
HttpTransportBase every adapter uses. NEVER a real price row: every write names fixture securities ('ZZP2 VERIFY …',
tickers ZZP2…) and the stub provider keys; the real yahoo_chart call log, the real day rows and the real coverage set
are fingerprinted before and after.

THE STORE AT SCALE (sections d / m / x): 2,600 securities through the real ingest would write ~2,600 price series per
day; there the per-security STORE calls (writes.upsert_eod_bars, prices._context, prices._coverage,
writes.record_quality_event) are replaced by counters for the section, so the budget / deadline / batching / call
counting / day row / state table / order are all the real code. Section r runs the real store unchanged.

THE CLOCK: DAY1 = the last weekday on or before the database's UTC date; DAY2 = the next weekday. Every nightly /
tick gets ``today`` (or the day row's own day) and a FAKE monotonic clock that advances 1.4 s per request (1.0 s
throttle + ~0.4 s latency — tracking.nightly_capacity's own timing), so one 180 s slice asks ~150 securities, as in
production. THE BUDGET: prices._budget is pinned for the stub keys to (the section's budget − the stub's own calls in
portfolio.provider_call_log for that day) — the real day's call log never decides a result.

TEARDOWN (start and end): _verify_cleanup.cleanup on the operator DSN by fixture security ids, stub provider keys,
scopes 'zzp2…' and list keys 'zzp2_…'; zero fixture rows remain; real rows unchanged (count + checksum).
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import os
import json
import math
import pathlib
import re
import subprocess
import sys
import time
import traceback
import uuid
from datetime import date, timedelta
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "prices2"
REGRESSIONS = ["verify_prices1.py", "verify_track1.py", "verify_secmaster2.py"]
D = Decimal


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ═══ the fixture universe (pure — the self-checks simulate the same days) ════
LIST_SIZES = (("zzp2_core_etfs", 40), ("zzp2_fixed_income", 30), ("zzp2_commodities", 15),
              ("zzp2_key_benchmarks", 15), ("zzp2_sp500", 500), ("zzp2_russell2000", 2000))
FIX_LISTS = tuple(k for k, _ in LIST_SIZES)
N_D = sum(n for _, n in LIST_SIZES)       # 2,600
BUDGET_D = 2000
OVERLAP = 5                               # the first 5 core ETFs are ALSO Russell 2000 members (tier = the best list)


def d_lists() -> list[tuple[int, str]]:
    """(index, list_key) memberships of the 2,600 day fixtures, in index order."""
    out, i = [], 0
    for k, n in LIST_SIZES:
        out += [(j, k) for j in range(i, i + n)]
        i += n
    out += [(j, "zzp2_russell2000") for j in range(OVERLAP)]
    return out


def simulate(gsid_of: dict, budget: int, days: int = 2) -> list[list[str]]:
    """Pure: what each day asks for, using ONLY prices.tier_of + prices.refresh_order (no DB)."""
    from services.security_data import prices as P

    lists: dict = {}
    for i, k in d_lists():
        lists.setdefault(i, set()).add(k)
    items = []
    for i, g in gsid_of.items():
        it = P.Item(g, {"ticker": f"ZZP2{i:05d}"}, "USD", ["requested", "tracked"])
        it.tier = P.tier_of(it.sources, lists.get(i, ()), FIX_LISTS)
        items.append(it)
    last: dict = {}
    out = []
    for day in range(1, days + 1):
        dd = date(2026, 1, day)
        order = [it.gsid for it in P.refresh_order(items, last, {})][:budget]
        for g in order:
            last[g] = dd
        out.append(order)
    return out


# ═══ self-checks (no database) ══════════════════════════════════════════════
def self_static(check, find):
    notes = _read("docs/PRICES2_NOTES.md")
    for tok in ("128 securities per slice", "max_symbols_per_call = 1", "spark", "NOT built", "15 21 * * 1-5",
                "⌈2,600/2,000⌉", "Operator option", "daily_call_budget", "oldest-first", "ai_daily_runs"):
        check(tok in notes, f"PRICES2_NOTES names {tok!r} (discovery / capacity / the operator's budget option)")
    sql = _read("docs/prices2_part1.sql")
    for t in ("price_refresh_days", "price_refresh_state"):
        check(f"portfolio.{t}" in sql, f"migration creates portfolio.{t}")
    check(all(x in sql for x in ("FOR SELECT USING (true)", "FOR INSERT WITH CHECK", "FOR UPDATE USING",
                                 "FOR DELETE USING", "GRANT SELECT, INSERT, UPDATE, DELETE",
                                 "UNIQUE INDEX IF NOT EXISTS price_refresh_days_day_uq", "ON DELETE CASCADE")),
          "migration: a policy for every operation, app_service grants, ONE row per (scope, day), state cascades with "
          "its security")
    check("DROP TABLE" not in sql.upper() and "DROP COLUMN" not in sql.upper(), "migration is additive (no DROP)")
    from services import security_master_jobs as J
    from services.security_data import prices as P
    from services.security_data.providers.yahoo_chart import SPEC
    spec = J.JOBS["security_data_prices"]
    check((spec.cron, spec.timezone) == ("15 21 * * 1-5", "America/New_York"),
          "the prices job keeps its schedule: weekdays 21:15 New York (after the close and the NAVs)",
          f"{spec.cron} {spec.timezone}")
    check(SPEC.daily_call_budget == 2000 and SPEC.max_symbols_per_call == 1,
          "the free source's daily budget is NOT raised (2,000; Joe's call) and it stays one symbol per request",
          f"{SPEC.daily_call_budget} {SPEC.max_symbols_per_call}")
    check((P.GAP_FILL_DAYS, P.NIGHTLY_MAX_NEW, P.NIGHTLY_DEADLINE_S) == (10, 200, 180.0),
          "GAP_FILL_DAYS 10, NIGHTLY_MAX_NEW 200 (now per DAY), NIGHTLY_DEADLINE_S 180 (now per slice) unchanged")
    check(P.LIST_PRIORITY == ("core_etfs", "fixed_income", "commodities", "key_benchmarks", "sp500", "russell2000"),
          "list priority: core ETFs → fixed income → commodities → key benchmarks → S&P 500 → Russell 2000")
    from services.security_data import security_lists as SL
    try:
        keys = [d["key"] for d in SL.load_definitions()]
    except Exception as exc:  # noqa: BLE001
        keys = []
        find("the list data file did not load", str(exc))
    check(set(P.LIST_PRIORITY) == set(keys), "every ranked list key is a real curated list key (and every list is ranked)",
          f"{keys}")
    tick = _read("apps/api/workflow_scheduler_tick.py")
    m = re.search(r"try:\n\s+from services\.security_data\.prices import prices_tick\n\s+px = await prices_tick\(pool\)"
                  r"(.*?)except Exception", tick, flags=re.S)
    check(bool(m), "the scheduler tick calls prices_tick in its OWN try (a problem there stops no other step)")
    src = _read("apps/api/services/security_data/prices.py")
    body = src[src.index("async def prices_tick("):src.index("# ── PRICES2: the Security master screen")]
    check("except Exception as exc:" in body and not re.search(r"^\s*raise\b", body, flags=re.M)
          and "raise_errors=False" in body,
          "prices_tick catches everything and raises nothing (raise_errors=False into nightly)")
    check("VENDOR" not in src and not re.search(r"yahoo|tiingo|stooq|polygon", src, flags=re.I),
          "prices.py still names no vendor (PRICES1's rule)")
    check("verify_prices2.py" in _read("scripts/regression_set.txt"), "verify_prices2.py is in scripts/regression_set.txt")
    check((WEB_DIR / "tests" / "prices2" / "prices2.test.mjs").exists(), "the render test exists")
    rt = _read("apps/api/routers/security_master.py")
    check('"price_refresh": price_refresh' in rt and "P.refresh_status(conn)" in rt,
          "GET …/master/summary publishes price_refresh (behind its existing Super Admin gate)")


def self_order(check, find):
    from services.security_data import prices as P
    t = P.tier_of
    check(t(["position"]) == 0 and t(["model"]) == 1 and t(["replica_constituent"]) == 1 and t(["benchmark"]) == 2
          and t(["note"]) == 3, "org uses rank: held 0 → models / replicas 1 → benchmarks 2 → other org uses 3")
    check([t(["tracked"], [k]) for k in P.LIST_PRIORITY] == [10, 11, 12, 13, 14, 15] and t(["tracked"]) == 99
          and t(["requested"]) == 99, "lists rank 10..15 in the prompt's order; tracked in no ranked list / "
          "requested alone = 99")
    check(t(["position", "tracked"], ["russell2000"]) == 0 and t(["tracked"], ["russell2000", "core_etfs"]) == 10,
          "a security takes its BEST reason (held beats a list; core ETFs beats Russell 2000)")
    I = P.Item
    a = [I("g4", {}, None, ["tracked"], tier=15), I("g3", {}, None, ["tracked"], tier=10),
         I("g2", {}, None, ["benchmark"], tier=2), I("g1", {}, None, ["position"], tier=0)]
    o = [x.gsid for x in P.refresh_order(a, {}, {})]
    check(o == ["g1", "g2", "g3", "g4"], "nothing refreshed yet → pure priority order", f"{o}")
    d1, d0 = date(2026, 1, 2), date(2026, 1, 1)
    o = [x.gsid for x in P.refresh_order(a, {"g1": d1, "g2": d1, "g3": d1, "g4": d0}, {})]
    check(o == ["g1", "g2", "g4", "g3"], "the org uses go first EVERY day; among the rest the oldest refresh goes first "
          "(the Russell security left yesterday beats the core ETF done yesterday)", f"{o}")
    o = [x.gsid for x in P.refresh_order(a[:2], {"g4": d1, "g3": d1}, {"g4": d0, "g3": d1})]
    check(o == ["g3", "g4"] and [x.gsid for x in P.refresh_order(
        [I("b", {}, None, [], tier=99), I("a", {}, None, [], tier=99)], {}, {"a": d1, "b": d0})] == ["b", "a"],
          "same day + tier → the stalest stored close first (TRACK1), then gsid", f"{o}")
    gs = {i: f"{i:08d}-0000-0000-0000-000000000000" for i in range(N_D)}
    days = simulate(gs, BUDGET_D)
    inv = {g: i for i, g in gs.items()}
    day1 = [inv[g] for g in days[0]]
    left = sorted(set(range(N_D)) - set(day1))
    check(day1[:100] == list(range(100)) and len(day1) == 2000 and left == list(range(2000, 2600)),
          "pure simulation, 2,600 at 2,000: day 1 = core → fixed income → commodities → key benchmarks → S&P 500 → "
          "first 1,400 Russell; the 600 left are the Russell tail",
          f"{day1[:5]} left {left[:3]}..{left[-3:]} ({len(left)})")
    day2 = [inv[g] for g in days[1]]
    check(day2[:600] == left and day2[600:] == day1[:1400],
          "pure simulation, day 2: the 600 left go FIRST, then the 1,400 highest-priority of yesterday's")
    S["sim"] = days


def self_arith(check, find):
    from services.security_data import tracking as TR
    from services.security_data.providers.yahoo_chart import SPEC
    check(TR.nightly_capacity(SPEC, 180.0) == 128, "one slice's capacity is still 128 (now per tick, not per night)")
    e = TR.estimate_numbers(selected=2700, to_load=2650, coverage_after=2729, per_call=1, budget=2000, nightly_cap=128,
                            nightly_max_new=200)
    check(len(e["warnings"]) == 2 and e["refreshes_per_day"] == 2000 and e["refresh_every_days"] == 2
          and "about 128" not in " ".join(e["warnings"]) and "at most 2,000 securities current a day" in e["warnings"][1]
          and "every 2 days" in e["warnings"][1],
          "track1's warning states the DAILY capacity at the budget (2,000 a day; 2,729 → every 2 days), not 128 a run",
          f"{e['warnings']}")
    ok = TR.estimate_numbers(selected=10, to_load=10, coverage_after=1500, per_call=1, budget=2000, nightly_cap=128,
                             nightly_max_new=200)
    check(not ok["warnings"] and ok["refresh_every_days"] == 1,
          "1,500 covered at a 2,000 budget: no warning — every security every day (was 'every 12 nights')",
          f"{ok['warnings']}")
    n = TR.estimate_numbers(selected=2650, to_load=2650, coverage_after=2729, per_call=1, budget=2000, nightly_cap=128,
                            nightly_max_new=200, new_per_day=200)
    check(n["nights_nightly_only"] == math.ceil(2650 / 200), "new history through the nightly alone: 200 a DAY → "
          f"{math.ceil(2650 / 200)} days (was ⌈2,650/128⌉ = 21)", f"{n['nights_nightly_only']}")
    m = TR.estimate_numbers(selected=10, to_load=10, coverage_after=2729, per_call=50, budget=2000, nightly_cap=6400,
                            nightly_max_new=200)
    check(m["refreshes_per_day"] == 100000 and not m["warnings"], "a 50-per-call source at the same budget: 100,000 a "
          "day, no warning")


S: dict = {}


def self_checks(check, find):
    self_static(check, find)
    self_order(check, find)
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
from services import extraction  # noqa: E402
from services.database import get_pool  # noqa: E402
from services.security_data import prices as P  # noqa: E402
from services.security_data import writes as W  # noqa: E402
from services.security_data.providers.eod import (EodBar, EodLicence, EodPriceProvider, EodSeries,  # noqa: E402
                                                  EodSourceError, EodSpec, RecordedHttpGet)

NAME_PREFIX = "ZZP2 VERIFY"
TICKER_PREFIX = "ZZP2"
LIST_PREFIX = "zzp2"
SCOPE = "zzp2_verify"
STUB, STUB50 = "zzp2_stub", "zzp2_stub50"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
#: section → (first index, count) of its own fixture securities (disjoint: every section runs alone)
RANGES = {"d": (0, N_D), "m": (3000, 260), "x": (3300, 40), "r": (3400, 8)}

NETWORK_CALLS: list[str] = []
LLM_CALLS: list[str] = []
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


def last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


CLOCK: dict = {}


class FakeClock:
    """A monotonic clock that moves only when the stub source is asked (1.4 s a request)."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


CLK = FakeClock()


# ═══ the stub source ════════════════════════════════════════════════════════
LIC = EodLicence(internal_use=True, member_display="not_allowed", redistribution=False,
                 retention_after_cancel="unknown", attribution="verify", terms_note="PRICES2 verify stub")
STUB_URL = "https://stub.invalid/eod"
MODE = {"fail": False}
REQUESTS: list[list[str]] = []           # the symbols of every request, in order


def _respond(url, params):
    return (403, "Forbidden") if MODE["fail"] else (200, "{}")


class _Stub(EodPriceProvider):
    def symbol_for(self, identifiers: dict):
        return identifiers.get("ticker")

    async def fetch_eod(self, symbols, start, end):
        per = self.spec.max_symbols_per_call
        out = {}
        for j in range(0, len(symbols), per):
            grp = list(symbols[j:j + per])
            status, _ = await self.http.get(STUB_URL, {"symbols": ",".join(grp)})
            CLK.t += 1.4
            REQUESTS.append(grp)
            if status != 200:
                raise EodSourceError(f"{self.key}: HTTP {status}")
            bars = [EodBar(day=d_, close=D("10.5") + D(k) / 100, currency="USD")
                    for k, d_ in enumerate(d_ for d_ in (end - timedelta(days=n) for n in (4, 3, 2, 1, 0))
                                           if d_.weekday() < 5 and d_ >= start)]
            for s_ in grp:
                out[s_] = EodSeries(s_, True, bars, "USD")
        return out


def _spec(key, per):
    return EodSpec(key=key, name=f"PRICES2 verify stub ({per}/call)", keyed_by="ticker", max_symbols_per_call=per,
                   daily_call_budget=None, min_interval_s=0.0, history_years=5, gives_adjustment=False,
                   secret_env=None, licence=LIC)


class Stub1(_Stub):
    spec = _spec(STUB, 1)


class Stub50(_Stub):
    spec = _spec(STUB50, 50)


PROVIDERS = {STUB: Stub1(), STUB50: Stub50()}
BUDGETS: dict = {}
_REAL_BUDGET = P._budget


async def pinned_budget(pool, key, today):
    if key not in BUDGETS:
        raise AssertionError(f"verify_prices2: a budget was asked for a non-fixture source {key}")
    async with pool.acquire() as conn:
        return max(0, BUDGETS[key] - await W.calls_used(conn, key, today))


STORE: dict = {"upserts": 0}
_REAL = {}


def fast_store(on: bool) -> None:
    """Sections d / m / x: the per-security store calls become counters (see the docstring)."""
    if on:
        _REAL.update(upsert=W.upsert_eod_bars, ctx=P._context, cov=P._coverage, qe=W.record_quality_event,
                     latest=P.latest_dates)

        async def upsert(conn, gsid, bars, **kw):
            STORE["upserts"] += 1
            return {"inserted": len(bars)}

        async def ctx(conn, key, gsids, start):
            return {}

        async def cov(*a, **k):
            return None

        async def qe(*a, **k):
            return "x"

        async def latest(conn, key, gsids):   # every day fixture already holds a series (the gap-fill path)
            return {g: CLOCK["day1"] - timedelta(days=3) for g in gsids}

        W.upsert_eod_bars, P._context, P._coverage, W.record_quality_event = upsert, ctx, cov, qe
        P.latest_dates = latest
    elif _REAL:
        W.upsert_eod_bars, P._context, P._coverage = _REAL["upsert"], _REAL["ctx"], _REAL["cov"]
        W.record_quality_event, P.latest_dates = _REAL["qe"], _REAL["latest"]
        _REAL.clear()


def install_stubs():
    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_prices2: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    for name in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        if hasattr(extraction, name):
            def make(n):
                async def no_llm(*a, **k):
                    LLM_CALLS.append(n)
                    raise AssertionError(f"verify_prices2: {n} was called")
                return no_llm
            setattr(extraction, name, make(name))
    P.TRANSPORT_FACTORY[STUB] = lambda: RecordedHttpGet(responder=_respond)
    P.TRANSPORT_FACTORY[STUB50] = lambda: RecordedHttpGet(responder=_respond)
    P._budget = pinned_budget


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
    import services.database as DBM
    p, DBM._pool = DBM._pool, None
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


def counted_plan(secs: list) -> dict:
    return {
        "portfolio.price_refresh_state": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.price_refresh_days": ("scope LIKE $1", [f"{LIST_PREFIX}%"]),
        "portfolio.provider_call_log": ("provider_key LIKE $1", [f"{LIST_PREFIX}%"]),
        "portfolio.tracked_securities": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.security_list_members": ("list_key LIKE $1 OR global_security_id = ANY($2::uuid[])",
                                            [f"{LIST_PREFIX}%", secs]),
        "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[]) OR provider_key LIKE $2",
                                               [secs, f"{LIST_PREFIX}%"]),
        "portfolio.security_price_coverage": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.data_quality_events": ("global_security_id = ANY($1::uuid[]) OR provider_key LIKE $2",
                                          [secs, f"{LIST_PREFIX}%"]),
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
    }


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        # 2,600 fixture securities: the FK checks of one DELETE on securities_global outlast the default timeout
        await op.execute("SET statement_timeout = '540s'")
        secs = [r[0] for r in await op.fetch(
            "SELECT id FROM portfolio.securities_global WHERE name LIKE $1 UNION SELECT global_security_id FROM "
            "portfolio.securities_global_identifiers WHERE id_type = 'ticker' AND id_value LIKE $2",
            f"{NAME_PREFIX}%", f"{TICKER_PREFIX}%")]
        res = await VC.cleanup(op, orgs=[], users=[], plan=counted_plan(secs), gucs=(), label="verify_prices2")
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
    for t, (pred, args) in counted_plan(secs).items():
        n = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
        if n:
            out[t] = n
    return out


async def coverage_fp(conn) -> tuple[int, str]:
    c = sorted((it.gsid, tuple(it.sources)) for it in await P.coverage_set(conn))
    return len(c), hashlib.md5(json.dumps(c).encode()).hexdigest()


async def real_fingerprints(conn) -> dict:
    return {
        "real price_refresh_days": await fingerprint(
            conn, "SELECT id, scope, price_day, status, refreshed, calls FROM portfolio.price_refresh_days WHERE scope "
                  "NOT LIKE $1", f"{LIST_PREFIX}%"),
        "real price_refresh_state": await fingerprint(
            conn, "SELECT s.global_security_id, s.last_refreshed_on FROM portfolio.price_refresh_state s JOIN "
                  "portfolio.securities_global g ON g.id = s.global_security_id WHERE g.name NOT LIKE $1",
            f"{NAME_PREFIX}%"),
        "real yahoo_chart call log": await fingerprint(
            conn, "SELECT id, call_date, calls FROM portfolio.provider_call_log WHERE provider_key = 'yahoo_chart'"),
        "real tracked_securities": await fingerprint(
            conn, "SELECT t.id, t.status, t.valid_to FROM portfolio.tracked_securities t JOIN portfolio.securities_global "
                  "g ON g.id = t.global_security_id WHERE g.name NOT LIKE $1", f"{NAME_PREFIX}%"),
        "real coverage set (gsid + reasons)": await coverage_fp(conn),
    }


# ═══ seed ═══════════════════════════════════════════════════════════════════
async def guard_prefix(conn) -> bool:
    n_t = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_type = 'ticker' "
                               "AND id_value LIKE $1", f"{TICKER_PREFIX}%", fetch="fetchval")
    n_n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                         fetch="fetchval")
    n_l = await platform(conn, "SELECT count(*) FROM portfolio.security_list_members WHERE list_key LIKE $1",
                         f"{LIST_PREFIX}%", fetch="fetchval")
    n_p = await platform(conn, "SELECT (SELECT count(*) FROM portfolio.provider_call_log WHERE provider_key LIKE $1) + "
                               "(SELECT count(*) FROM portfolio.price_refresh_days WHERE scope LIKE $1)",
                         f"{LIST_PREFIX}%", fetch="fetchval")
    return check(int(n_t) == 0 and int(n_n) == 0 and int(n_l) == 0 and int(n_p) == 0,
                 "reserved fixture prefixes (tickers ZZP2…, names 'ZZP2 VERIFY', lists / scopes / sources zzp2…) "
                 "match no real row", f"tickers {n_t}, names {n_n}, lists {n_l}, logs/days {n_p}")


def gid(i: int) -> str:
    return S["gid"][i]


async def seed(conn, sections: set) -> None:
    idx = sorted({i for k in sections for i in range(RANGES[k][0], RANGES[k][0] + RANGES[k][1])})
    ids = [str(uuid.uuid4()) for _ in idx]
    S["gid"] = dict(zip(idx, ids))
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "INSERT INTO portfolio.securities_global (id, canonical_id, name, security_type, currency_code, "
            "price_coverage) SELECT g, g, $2 || ' ' || i, 'equity', 'USD', 'unknown' FROM unnest($1::uuid[], $3::int[]) "
            "AS u(g, i)", ids, NAME_PREFIX, idx)
        await conn.execute(
            "INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value, is_primary) "
            "SELECT g, 'ticker', $2 || lpad(i::text, 5, '0'), true FROM unnest($1::uuid[], $3::int[]) AS u(g, i)",
            ids, TICKER_PREFIX, idx)
        if "d" in sections:
            mem = d_lists()
            await conn.execute(
                "INSERT INTO portfolio.security_list_members (list_key, global_security_id, ticker, matched_by) "
                "SELECT k, g, t, 'ticker' FROM unnest($1::text[], $2::uuid[], $3::text[]) AS u(k, g, t)",
                [k for _, k in mem], [gid(i) for i, _ in mem], [f"{TICKER_PREFIX}{i:05d}" for i, _ in mem])
            first = {}
            for i, k in mem:
                first.setdefault(i, k)
            await conn.execute(
                "INSERT INTO portfolio.tracked_securities (global_security_id, status, list_key, note) SELECT g, "
                "'tracked', k, 'PRICES2 verify fixture' FROM unnest($1::uuid[], $2::text[]) AS u(g, k)",
                [gid(i) for i in sorted(first)], [first[i] for i in sorted(first)])
    n = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1",
                           f"{NAME_PREFIX}%", fetch="fetchval"))
    S["seeded"] = check(n == len(idx), f"seeded {len(idx):,} fixture securities (one bulk statement each table)",
                        f"{n}")


def rng(k) -> list[str]:
    a, n = RANGES[k]
    return [gid(i) for i in range(a, a + n)]


def sym_index(sym: str) -> int:
    return int(sym[len(TICKER_PREFIX):])


async def day_row(conn, scope, day):
    r = await platform(conn, "SELECT * FROM portfolio.price_refresh_days WHERE scope = $1 AND price_day = $2", scope, day,
                       fetch="fetchrow")
    return P.day_out(r) if r else None


async def calls_logged(conn, key, day) -> int:
    return int(await platform(conn, "SELECT COALESCE(sum(calls), 0) FROM portfolio.provider_call_log WHERE provider_key "
                                    "= $1 AND call_date = $2", key, day, fetch="fetchval"))


async def state_on(conn, gsids, day) -> int:
    return int(await platform(conn, "SELECT count(*) FROM portfolio.price_refresh_state WHERE global_security_id = "
                                    "ANY($1::uuid[]) AND last_refreshed_on = $2", gsids, day, fetch="fetchval"))


def kw(gsids, key=STUB, **extra) -> dict:
    return {"only_gsids": gsids, "keys": [key], "providers": PROVIDERS, "list_priority": FIX_LISTS, **extra}


async def run_day(pool, scope, day, gsids, *, key=STUB, max_ticks=40, **extra):
    """The job's first slice (as the 21:15 trigger runs it), then scheduler ticks until the day is no longer running.
    Returns (first summary, [tick results], requests asked)."""
    r0 = len(REQUESTS)
    first = await P.nightly(pool, today=day, scope=scope, clock=CLK, **kw(gsids, key, **extra))
    ticks = []
    for _ in range(max_ticks):
        t = await P.prices_tick(pool, scope=scope, check_trigger=False, nightly_kwargs=kw(gsids, key, **extra),
                                clock=CLK)
        ticks.append(t)
        if t.get("action") in ("idle", "failed"):
            break
    return first, ticks, [s_ for req in REQUESTS[r0:] for s_ in req]


# ═══ sections ═══════════════════════════════════════════════════════════════
async def y_days(conn):
    section("[D] 2,600 covered at a 2,000 budget — day 1 in priority order across several ticks; day 2 starts with the "
            "600 left")
    pool = await get_pool()
    day1, day2 = CLOCK["day1"], CLOCK["day2"]
    ids = rng("d")
    BUDGETS[STUB] = BUDGET_D
    fast_store(True)
    try:
        items = await P.coverage_set(conn, only_gsids=ids)
        await P.assign_tiers(conn, items, list_priority=FIX_LISTS)
        tiers = {it.gsid: it.tier for it in items}
        check(len(items) == N_D and all("tracked" in it.sources for it in items),
              f"the coverage set holds all {N_D:,} fixtures with the reason 'tracked'", f"{len(items)}")
        want_t: dict = {}
        for i, k in d_lists():
            want_t[i] = min(want_t.get(i, 99), 10 + FIX_LISTS.index(k))
        bad = [i for i in range(N_D) if tiers.get(gid(i)) != want_t[i]]
        check(not bad, "assign_tiers reads the lists from the database: each security's tier = its best list "
              "(the 5 core ETFs also in the Russell 2000 stay core)", f"{bad[:5]}")
        # the expected order, from the same pure rule the self-checks simulate, on the REAL gsids
        exp1, exp2 = simulate({i: gid(i) for i in range(N_D)}, BUDGET_D)
        first, ticks, asked = await run_day(pool, SCOPE, day1, ids)
        row = await day_row(conn, SCOPE, day1)
        n_rows = int(await platform(conn, "SELECT count(*) FROM portfolio.price_refresh_days WHERE scope = $1 AND "
                                          "price_day = $2", SCOPE, day1, fetch="fetchval"))
        asked_g = [gid(sym_index(s_)) for s_ in asked]
        progress = [first["day"]["progress_label"]] + [t.get("progress") for t in ticks if t.get("progress")]
        refreshed_seq = [int(p.split(" of ")[0].replace(",", "")) for p in progress]
        check(n_rows == 1 and row is not None, "ONE day row for the day (scope + price_day), however many ticks",
              f"{n_rows}")
        slices = row["slices"] if row else 0
        check(slices >= 3 and first["day"]["refreshed"] < BUDGET_D,
              f"the day ran across several ticks: {slices} slices (the job's first slice refreshed "
              f"{first['day']['refreshed']}, inside its 180 s deadline on the 1.4 s-a-request clock)",
              f"{slices} {first['day']}")
        check(refreshed_seq == sorted(refreshed_seq) and len(set(refreshed_seq)) == len(refreshed_seq)
              and refreshed_seq[-1] == BUDGET_D, "progress recorded on the row after every slice, rising to 2,000",
              f"{progress[:4]}…{progress[-2:]}")
        # the order is decided per BATCH of 25 (ingest asks a batch's symbols sorted by symbol): every batch is
        # exactly the next 25 of the priority order
        bad1 = [i for i in range(0, BUDGET_D, 25) if set(asked_g[i:i + 25]) != set(exp1[i:i + 25])]
        check(len(asked_g) == BUDGET_D and not bad1 and set(asked_g) == set(exp1),
              "day 1 asked EXACTLY the priority order, batch by batch: core ETFs → fixed income → commodities → key "
              "benchmarks → S&P 500 → Russell 2000 (first 1,400)", f"bad batches at {bad1[:5]}; {len(asked_g)} asked")
        check(len(asked) == len(set(asked)) == BUDGET_D, "no security asked twice in the day; 2,000 requests in total",
              f"{len(asked)} / {len(set(asked))}")
        logged = await calls_logged(conn, STUB, day1)
        check(logged == BUDGET_D and row["calls"] == BUDGET_D, "calls recorded honestly: the call log for the day = the "
              "day row = the requests the source saw = 2,000 (the budget, never exceeded)",
              f"log {logged}, row {row['calls'] if row else None}")
        check(bool(row and row["status"] == "stopped_budget" and row["refreshed"] == BUDGET_D and row["covered"] == N_D
                   and row["progress_label"] == "2,000 of 2,600 refreshed" and "600" in (row["stop_detail"] or "")),
              "the day stops 'stopped_budget': '2,000 of 2,600 refreshed', 600 named as going first tomorrow",
              f"{ {k: row.get(k) for k in ('status', 'refreshed', 'covered', 'stop_detail')} if row else None}")
        on1 = await state_on(conn, ids, day1)
        check(on1 == BUDGET_D, "the state table says 2,000 refreshed on day 1 (read back, SQL)", f"{on1}")
        r_before = len(REQUESTS)
        again = await P.prices_tick(pool, scope=SCOPE, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
        again2 = await P.nightly(pool, today=day1, scope=SCOPE, clock=CLK, **kw(ids))
        check(again.get("action") == "idle" and again2.get("action") == "day_finished" and len(REQUESTS) == r_before,
              "after the stop: a further tick is idle and a second job start the same day does nothing (0 requests)",
              f"{again} {again2.get('action')}")
        st = await P.refresh_status(conn, scope=SCOPE, only_gsids=ids, today=day1)
        sql_ref = await state_on(conn, ids, day1)
        check(st["covered"] == N_D and st["refreshed_today"] == sql_ref == BUDGET_D
              and st["label"].startswith("covered 2,600 · refreshed today 2,000 · oldest price date")
              and st["days"] and st["days"][0]["progress_label"] == "2,000 of 2,600 refreshed",
              "the screen numbers: covered 2,600 · refreshed today 2,000 (= SQL) + the day's progress line",
              f"{st['label']} {st['days'][:1]}")
        left = set(ids) - set(asked_g)
        # ── day 2
        first2, ticks2, asked2 = await run_day(pool, SCOPE, day2, ids)
        a2 = [gid(sym_index(s_)) for s_ in asked2]
        row1 = await day_row(conn, SCOPE, day1)
        row2 = await day_row(conn, SCOPE, day2)
        check(set(a2[:600]) == left and len(left) == 600,
              "day 2 STARTS with the 600 not refreshed on day 1 (oldest first)", f"{len(left)} {len(set(a2[:600]) & left)}")
        bad2 = [i for i in range(0, BUDGET_D, 25) if set(a2[i:i + 25]) != set(exp2[i:i + 25])]
        check(len(a2) == BUDGET_D and not bad2, "day 2 = the 600 left, then the 1,400 highest-priority of day 1's (the "
              "simulated order, batch by batch)", f"bad batches at {bad2[:5]}")
        check(bool(row1 and row1["status"] == "stopped_budget" and row2 and row2["status"] == "stopped_budget"
                   and row2["calls"] == BUDGET_D and row2["refreshed"] == BUDGET_D),
              "day 1's row is untouched; day 2 has its OWN row (2,000 calls, 2,000 refreshed)",
              f"{row1 and row1['status']} {row2 and (row2['status'], row2['calls'], row2['refreshed'])}")
        oldest = await platform(conn, "SELECT min(last_refreshed_on), count(*) FROM portfolio.price_refresh_state WHERE "
                                      "global_security_id = ANY($1::uuid[])", ids, fetch="fetchrow")
        check(oldest[0] == day1 and int(oldest[1]) == N_D,
              "after day 2 every one of the 2,600 has been refreshed, none longer ago than day 1", f"{tuple(oldest)}")
    finally:
        fast_store(False)


async def y_multi(conn):
    section("[M] Multi-symbol path: a 50-per-call source is asked ⌈N/50⌉ times and the calls are recorded honestly")
    pool = await get_pool()
    day = CLOCK["day1"]
    ids = rng("m")
    BUDGETS[STUB50] = 2000
    fast_store(True)
    try:
        r0 = len(REQUESTS)
        s = await P.nightly(pool, today=day, scope=SCOPE + "_m", clock=CLK, deadline_s=10_000, **kw(ids, STUB50))
        reqs = REQUESTS[r0:]
        row = s.get("day") or {}
        logged = await calls_logged(conn, STUB50, day)
        want = math.ceil(len(ids) / 50)
        check(len(reqs) == want == logged == row.get("calls") == s["providers"][STUB50]["calls"]
              and sum(len(r) for r in reqs) == len(ids) and max(len(r) for r in reqs) == 50,
              f"260 securities at 50 a request = {want} requests = the call log = the day row (not 260)",
              f"requests {len(reqs)}, log {logged}, row {row.get('calls')}, sizes {[len(r) for r in reqs]}")
        check(row.get("status") == "complete" and row.get("refreshed") == len(ids),
              "every covered security refreshed → the day is 'complete'", f"{row.get('status')} {row.get('refreshed')}")
        BUDGETS[STUB50] = logged + 3      # three more requests today
        scope2 = SCOPE + "_m2"
        r1 = len(REQUESTS)
        await platform(conn, "DELETE FROM portfolio.price_refresh_state WHERE global_security_id = ANY($1::uuid[])", ids,
                       fetch="execute")
        s2 = await P.nightly(pool, today=day, scope=scope2, clock=CLK, deadline_s=10_000, **kw(ids, STUB50))
        row2 = s2.get("day") or {}
        check(len(REQUESTS) - r1 == 3 and row2.get("refreshed") == 150 and row2.get("status") == "stopped_budget",
              "a budget of 3 calls at 50 a call refreshes 150 and stops 'stopped_budget'",
              f"{len(REQUESTS) - r1} {row2.get('refreshed')} {row2.get('status')}")
    finally:
        fast_store(False)


async def y_failures(conn):
    section("[X] Deadline, failure, race: a tick past its deadline stops cleanly and the next resumes; a failure is "
            "recorded, never raised into the tick")
    pool = await get_pool()
    day = CLOCK["day1"]
    ids = rng("x")
    BUDGETS[STUB] = 1_000_000
    sc = SCOPE + "_x"
    fast_store(True)
    try:
        n0 = int(await platform(conn, "SELECT count(*) FROM portfolio.price_refresh_days WHERE scope = $1", sc,
                                fetch="fetchval"))
        t0 = await P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
        n1 = int(await platform(conn, "SELECT count(*) FROM portfolio.price_refresh_days WHERE scope = $1", sc,
                                fetch="fetchval"))
        check(t0.get("action") == "idle" and n0 == n1 == 0, "the tick never STARTS a day (only the 21:15 job does)",
              f"{t0} {n0}->{n1}")
        trig = await P.job_trigger_active(conn)
        if trig:
            find("the REAL 'Security data — prices' job is switched ON — the job-off proof is skipped")
        else:
            r0 = len(REQUESTS)
            t = await P.prices_tick(pool, scope=sc, nightly_kwargs=kw(ids), clock=CLK)
            check(t.get("action") == "job_off" and len(REQUESTS) == r0,
                  "the real job is switched OFF → the tick does nothing (0 requests)", f"{t}")
        # deadline: the job's first slice gets 10 s — one batch of 25 (the deadline is checked between batches)
        r0 = len(REQUESTS)
        s = await P.nightly(pool, today=day, scope=sc, clock=CLK, deadline_s=10, **kw(ids))
        got1 = [r[0] for r in REQUESTS[r0:]]
        row = s["day"]
        check(len(got1) == 25 and row["status"] == "running" and row["refreshed"] == 25 and not s["errors"]
              and row["lease_until"] is None,
              "a slice past its deadline stops cleanly: 25 of 40 refreshed, no error, lease released, still 'running'",
              f"{len(got1)} {row['status']} {row['refreshed']} {s['errors']}")
        # race: someone holds the lease → a tick is 'busy' and asks nothing
        await P.claim_day(pool, row["id"])
        r1 = len(REQUESTS)
        busy = await P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
        check(busy.get("action") == "busy" and len(REQUESTS) == r1,
              "while another slice holds the lease, a tick does NOTHING ('busy', 0 requests) — never two at once",
              f"{busy}")
        await platform(conn, "UPDATE portfolio.price_refresh_days SET lease_until = NULL WHERE id = $1::uuid", row["id"],
                       fetch="execute")
        # failure: the source refuses → recorded on the row, returned, NOT raised
        MODE["fail"] = True
        r2 = len(REQUESTS)
        f1 = await P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
        rowf = await day_row(conn, sc, day)
        check(f1.get("errors") and "HTTP 403" in f1["errors"][0] and rowf["consecutive_failures"] == 1
              and rowf["status"] == "running" and any("HTTP 403" in e["error"] for e in rowf["errors"])
              and len(REQUESTS) - r2 == 1,
              "a source failure in a tick: returned (not raised), recorded on the day row, the day still 'running'",
              f"{f1} {rowf['consecutive_failures']} {rowf['status']}")
        fl = int(await platform(conn, "SELECT count(*) FROM portfolio.provider_call_log WHERE provider_key = $1 AND "
                                      "call_date = $2 AND NOT ok AND error LIKE '%HTTP 403%'", STUB, day, fetch="fetchval"))
        check(fl == 1, "…and the failed call is in the call log (ok = false, with the reason)", f"{fl}")
        # the next tick resumes where it stopped (no repeat), and resets the failure count
        MODE["fail"] = False
        r3 = len(REQUESTS)
        ok = await P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
        got2 = [r[0] for r in REQUESTS[r3:]]
        rowo = await day_row(conn, sc, day)
        check(ok.get("status") == "complete" and rowo["consecutive_failures"] == 0 and len(got2) == 15
              and not set(got2) & set(got1) and rowo["refreshed"] == 40,
              "the next tick RESUMES: the other 15 (none of the first 25 again), failures reset, day 'complete'",
              f"{ok} {len(got2)} {rowo['refreshed']}")
        # three failed slices in a row stop the day
        day_b = next_weekday(CLOCK["day2"])
        MODE["fail"] = True
        try:
            raised = None
            try:
                await P.nightly(pool, today=day_b, scope=sc, clock=CLK, **kw(ids))
            except P.PricesJobError as exc:
                raised = str(exc)
            check(bool(raised and "HTTP 403" in raised),
                  "the JOB step still raises on a source failure (the engine holds the run — PRICES1 unchanged)",
                  f"{raised}")
            outs = [await P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
                    for _ in range(3)]
        finally:
            MODE["fail"] = False
        rowb = await day_row(conn, sc, day_b)
        check(rowb["status"] == "stopped_error" and rowb["consecutive_failures"] == 3 and "HTTP 403" in
              (rowb["stop_detail"] or "") and outs[-1].get("action") == "idle",
              "3 failed slices in a row → 'stopped_error' with the reason (no retry every 5 minutes forever)",
              f"{rowb['status']} {rowb['consecutive_failures']} {[o.get('action') for o in outs]}")
        # an unexpected error inside the slice: recorded, the tick returns
        day_c = next_weekday(day_b)
        await P.nightly(pool, today=day_c, scope=sc, clock=CLK, deadline_s=0, **kw(ids))
        real_cov = P.coverage_set

        async def boom(*a, **k):
            raise RuntimeError("verify: coverage read failed")
        P.coverage_set = boom
        try:
            out = await P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK)
        finally:
            P.coverage_set = real_cov
        rowc = await day_row(conn, sc, day_c)
        check(out.get("action") == "failed" and any("RuntimeError" in e["error"] for e in rowc["errors"])
              and rowc["status"] == "running" and rowc["lease_until"] is None,
              "an unexpected error in a slice: the tick returns 'failed' (never raises), the row records it and its "
              "lease is released", f"{out} {rowc['errors'][-1:]}")
        # a new day closes yesterday's still-running row
        day_d = next_weekday(day_c)
        await P.nightly(pool, today=day_d, scope=sc, clock=CLK, deadline_s=0, **kw(ids))
        rowc2 = await day_row(conn, sc, day_c)
        check(rowc2["status"] == "carried_over", "a new day closes the previous still-running row 'carried_over'",
              f"{rowc2['status']}")
        # two ticks at once: exactly one works
        r4 = len(REQUESTS)
        a_, b_ = await asyncio.gather(
            P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK),
            P.prices_tick(pool, scope=sc, check_trigger=False, nightly_kwargs=kw(ids), clock=CLK))
        acts = sorted([a_.get("action"), b_.get("action")])
        asked = [r[0] for r in REQUESTS[r4:]]
        check("busy" in acts and len(asked) == len(set(asked)),
              "two ticks at the same moment: one does the slice, the other is 'busy'; nothing asked twice",
              f"{acts} {len(asked)}")
    finally:
        fast_store(False)
        MODE["fail"] = False


async def y_real(conn):
    section("[R] The REAL store end to end: 8 securities, budget 5 — prices stored for the 5, the screen = SQL; day 2 "
            "takes the 3 left first")
    pool = await get_pool()
    day1, day2 = CLOCK["day1"], CLOCK["day2"]
    ids = rng("r")
    sc = SCOPE + "_r"
    BUDGETS[STUB] = 5
    calls_before = await calls_logged(conn, STUB, day1)
    BUDGETS[STUB] = calls_before + 5
    r0 = len(REQUESTS)
    s = await P.nightly(pool, today=day1, scope=sc, clock=CLK, deadline_s=10_000, **kw(ids))
    got = [gid(sym_index(r[0])) for r in REQUESTS[r0:]]
    rows = await platform(conn, "SELECT global_security_id::text AS g, count(*) AS n, max(price_date) AS d FROM "
                                "portfolio.securities_global_prices WHERE global_security_id = ANY($1::uuid[]) AND "
                                "provider_key = $2 AND org_id IS NULL AND valid_to IS NULL AND system_to IS NULL GROUP BY 1",
                          ids, STUB)
    stored = {r["g"] for r in rows}
    row = s["day"]
    check(len(got) == 5 and stored == set(got) and row["status"] == "stopped_budget" and row["refreshed"] == 5
          and s["providers"][STUB]["new_history"]["inserted"] > 0,
          "day 1: exactly 5 asked, prices STORED for exactly those 5 (real store), the day stops at the budget",
          f"{len(got)} stored {len(stored)} {row['status']} {row['refreshed']}")
    st = await P.refresh_status(conn, scope=sc, only_gsids=ids, today=day1)
    sql_old = min(r["d"] for r in rows) if rows else None
    check(st["covered"] == 8 and st["refreshed_today"] == 5 and st["oldest_price_date"] == (sql_old.isoformat()
                                                                                            if sql_old else None)
          and st["without_prices"] == 3
          and st["label"] == f"covered 8 · refreshed today 5 · oldest price date {sql_old.isoformat() if sql_old else '—'}",
          "the screen line = SQL: covered 8 · refreshed today 5 · oldest price date = min of the latest closes; 3 hold "
          "none yet", f"{st['label']} sql={sql_old}")
    BUDGETS[STUB] = (await calls_logged(conn, STUB, day2)) + 5
    r1 = len(REQUESTS)
    s2 = await P.nightly(pool, today=day2, scope=sc, clock=CLK, deadline_s=10_000, **kw(ids))
    got2 = [gid(sym_index(r[0])) for r in REQUESTS[r1:]]
    check(set(got2[:3]) == set(ids) - set(got) and len(got2) == 5 and s2["day"]["status"] == "stopped_budget",
          "day 2 asks the 3 never refreshed FIRST, then 2 of day 1's", f"{len(got2)}")
    # one-off mode (no scope — the operator's --gsid, PRICES1 / TRACK1 verifies): no day row, no state written
    fp0 = await fingerprint(conn, "SELECT global_security_id, last_refreshed_on, last_day_id FROM "
                                  "portfolio.price_refresh_state WHERE global_security_id = ANY($1::uuid[])", ids)
    nd0 = int(await platform(conn, "SELECT count(*) FROM portfolio.price_refresh_days WHERE scope LIKE $1",
                             f"{LIST_PREFIX}%", fetch="fetchval"))
    BUDGETS[STUB] = (await calls_logged(conn, STUB, day2)) + 8
    r2 = len(REQUESTS)
    s3 = await P.nightly(pool, today=day2, clock=CLK, deadline_s=10_000, **kw(ids))
    fp1 = await fingerprint(conn, "SELECT global_security_id, last_refreshed_on, last_day_id FROM "
                                  "portfolio.price_refresh_state WHERE global_security_id = ANY($1::uuid[])", ids)
    nd1 = int(await platform(conn, "SELECT count(*) FROM portfolio.price_refresh_days WHERE scope LIKE $1",
                             f"{LIST_PREFIX}%", fetch="fetchval"))
    check(len(REQUESTS) - r2 == 8 and "day" not in s3 and fp0 == fp1 and nd0 == nd1,
          "a ONE-OFF run (no scope) refreshes every requested security (all 8, even those done today) and writes no "
          "day row and no state (verify_track1's org-keyed teardown refused a state row — reproduced, fixed)",
          f"{len(REQUESTS) - r2} {'day' in s3} {fp0 == fp1} {nd0}->{nd1}")


def y_render():
    section("[W] Render test (node --test tests/prices2/ + the security-master suites)")
    p = subprocess.run(["node", "--test", "tests/prices2/", "tests/track1/", "tests/secmaster2/"], cwd=WEB_DIR,
                       capture_output=True, text=True, timeout=600)
    m = re.search(r"^# pass (\d+)$", p.stdout, flags=re.M)
    f = re.search(r"^# fail (\d+)$", p.stdout, flags=re.M)
    check(p.returncode == 0 and m is not None and f is not None and f.group(1) == "0" and int(m.group(1)) >= 3,
          "render: the summary shows the server's 'covered · refreshed today · oldest price date' line and the day's "
          "progress; no line without the envelope; track1 / secmaster2 suites still pass",
          f"pass={m.group(1) if m else None} fail={f.group(1) if f else None} {p.stderr[-300:]}")


def regression():
    section("[J] Regressions — each ONCE, flat, after teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


# ═══ driver ═════════════════════════════════════════════════════════════════
SECTIONS = {"d": "days", "m": "multi", "x": "failures", "r": "real store", "w": "render", "j": "regressions"}


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
    CLOCK["day1"] = last_weekday(db_today)
    CLOCK["day2"] = next_weekday(CLOCK["day1"])
    print(f"[INFO] database via {how}; DAY1 = {CLOCK['day1']} DAY2 = {CLOCK['day2']} (DB UTC date → weekdays)")
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    only = None
    if "--section" in sys.argv:
        only = {x.strip() for x in sys.argv[sys.argv.index("--section") + 1].lower().split(",") if x.strip()}
        if not only or not only <= set(SECTIONS):
            print(f"[FAIL] unknown section(s) {sorted(only - set(SECTIONS))}; one of {sorted(SECTIONS)}")
            print("TOTAL: 0 PASS, 1 FAIL")
            return 1
    want = (lambda k: True) if only is None else (lambda k: k in only)
    db_sections = {k for k in ("d", "m", "x", "r") if want(k)}
    if only is not None and "w" in only:
        y_render()
    if not db_sections:
        await close_conn(conn)
        if only is not None and "j" in only:
            regression()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    role = await platform(conn, "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = current_user",
                          fetch="fetchrow")
    check(bool(role and role["rolbypassrls"] is False), "this script's connection role does NOT bypass RLS",
          f"{dict(role) if role else None}")
    pre = real = None
    try:
        await run("pre-teardown", teardown_retrying)
        left0 = await leftovers(conn)
        check(not left0, "pre-run cleanup: zero fixture rows before seeding", f"{left0}")
        if not await guard_prefix(conn):
            check(False, "seeding needs the reserved prefixes free", "every fixture section skipped")
        else:
            real = await real_fingerprints(conn)
            pre = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {p}", *a, fetch="fetchval"))
                   for t, (p, a) in counted_plan([]).items()}
            await run("seed", seed, conn, db_sections)
            if S.get("seeded"):
                for k, fn in (("d", y_days), ("m", y_multi), ("x", y_failures), ("r", y_real)):
                    if k in db_sections:
                        await run(SECTIONS[k], fn, conn)
        if only is None:
            y_render()
        check(NETWORK_CALLS == [], "no network call was attempted", f"{NETWORK_CALLS[:3]}")
        check(LLM_CALLS == [], "no LLM helper was called", f"{LLM_CALLS[:3]}")
    finally:
        section("Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
        fast_store(False)
        P._budget = _REAL_BUDGET
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(not left, "zero fixture rows remain", f"{left}")
            if pre is not None:
                post = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {p}", *a, fetch="fetchval"))
                        for t, (p, a) in counted_plan([]).items()}
                for t in pre:
                    check(post[t] == pre[t], f"{t} at its pre-test count (by fixture keys)", f"{pre[t]} → {post[t]}")
            if real is not None:
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"{k} unchanged (count + checksum)", f"{real[k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
    if only is None:
        if os.environ.get("VERIFY_NESTED") == "1":
            print("[SKIP] nested run")
        else:
            regression()
    elif "j" in only:
        regression()
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
