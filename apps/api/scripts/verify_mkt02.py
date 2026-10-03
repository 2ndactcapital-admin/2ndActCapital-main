#!/usr/bin/env python3
"""verify_mkt02 — nightly market data refresh: adapter registry, Yahoo adapter,
nightly orchestrator, staleness report, cron entrypoint.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py
    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py --live

PHASE A (always) is self-contained. Every row it writes belongs to a fixture
series whose series_key starts with 'verify.mkt02.', or to a batch this
script created. Every registry it hands the orchestrator holds ONLY fake
adapters (or the real Yahoo adapter class over a FAKE transport) — never a
real FRED adapter — and every orchestrator run is given an explicit
fixture-only selection. No network.

PHASE B (--live) checks the real data after the operator has run
market_data_nightly.py TWICE.

Hydrates its own secrets from Doppler over HTTPS (scripts/_doppler_env.py),
the same helper verify_mkt01 uses. Does NOT chain verify_mkt01.

Output: [PASS] / [FAIL] / [FIND] / [SKIP] lines, then
``TOTAL: <passed> passed, <failed> failed``. Exit 1 on any [FAIL]; exit 2 if
teardown leaves a fixture row behind.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]
sys.path.insert(0, str(API_DIR))      # apps/api, for services.*
sys.path.insert(0, str(HERE.parent))  # scripts/, for _doppler_env + the operator scripts

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402
from services.market_data import adapters, ingest, nightly, registry, staleness, yahoo  # noqa: E402
from services.market_data.adapters import FetchResult, FredAdapter, ValidateResult  # noqa: E402
from services.market_data.yahoo import YahooAdapter, YahooResponse  # noqa: E402
import market_data_ingest as mdi  # noqa: E402
import market_data_nightly as mdn  # noqa: E402

PREFIX = "verify.mkt02."
SERIES_T = "market_data.indicator_series"
OBS_T = "market_data.indicator_observations"
RUNS_T = "market_data.indicator_ingest_runs"
UNTOUCHED = ("portfolio.securities_global", "portfolio.securities_global_prices", "public.fx_rates")
SENTINEL = f"SENTINELKEY{uuid.uuid4().hex}"
SENTINEL_PW = f"SENTINELPW{uuid.uuid4().hex[:12]}"
MKT01_ACTIVE_BASELINE = 265_737
FAKE = "verifyfake"            # a source_provider only Phase A's fake registry knows
NOADAPTER = "verifynoadapter"  # a source_provider no registry knows
REAL_SYMBOLS = {"^RUT": "%5ERUT", "EFA": "EFA", "EEM": "EEM", "ACWX": "ACWX",
                "GC=F": "GC%3DF", "DX-Y.NYB": "DX-Y.NYB"}

PASSED = 0
FAILED = 0
CREATED_BATCHES: list[uuid.UUID] = []
REGISTRIES_GUARDED = 0


def check(name: str, ok: bool, why: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"[PASS] {name} — {why}")
    else:
        FAILED += 1
        print(f"[FAIL] {name} — {why}" + (f" | {detail}" if detail else ""))
    return ok


def find(msg: str) -> None:
    print(f"[FIND] {msg}")


def skip(msg: str) -> None:
    print(f"[SKIP] {msg}")


async def nosleep(_seconds: float) -> None:
    return None


# ── Fakes ───────────────────────────────────────────────────────────────────
class FakeAdapter:
    """In-memory adapter. Its scrub is deliberately the IDENTITY: the
    orchestrator must make errors safe on its own, not trust the adapter."""

    def __init__(self, provider: str = FAKE, delay: float = 0.0):
        self.provider = provider
        self.data: dict[str, object] = {}
        self.delay = delay
        self.fetched: list[str] = []
        self.validated: list[str] = []
        self.raised: list[str] = []

    def scrub(self, text):
        return str(text)

    async def aclose(self):
        return None

    async def fetch_series(self, row) -> FetchResult:
        code = row["source_code"]
        self.fetched.append(code)
        if self.delay:
            await asyncio.sleep(self.delay)
        behaviour = self.data.get(code)
        if isinstance(behaviour, Exception):
            self.raised.append(str(behaviour))
            raise behaviour
        if behaviour is None:
            raise RuntimeError(f"no canned data for {code}")
        return FetchResult(points=list(behaviour))

    async def validate_series(self, row) -> ValidateResult:
        self.validated.append(row["source_code"])
        return ValidateResult("active", fields={"units": "Index"})


def guard(reg: dict) -> dict:
    """Every Phase A registry passes through here: no real FRED adapter, ever."""
    global REGISTRIES_GUARDED
    for provider, adapter in reg.items():
        if isinstance(adapter, FredAdapter):
            raise SystemExit(f"[FAIL] Phase A registry for {provider!r} holds a REAL FredAdapter — aborting")
        if isinstance(adapter, YahooAdapter) and not isinstance(adapter._transport, FakeYahooTransport):
            raise SystemExit("[FAIL] Phase A registry holds a Yahoo adapter over a REAL transport — aborting")
    REGISTRIES_GUARDED += 1
    return reg


def fixture_only(keys: list[str]) -> list[str]:
    if not keys or any(not k.startswith(PREFIX) for k in keys):
        raise SystemExit(f"[FAIL] Phase A tried to run the orchestrator on a non-fixture selection: {keys}")
    return keys


class FakeYahooTransport:
    """Canned responses per request path; records every request."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[tuple[str, dict]] = []

    async def get(self, path, params):
        self.calls.append((path, dict(params)))
        behaviour = self.routes.get(path)
        if behaviour is None:
            return YahooResponse(500, "")
        if callable(behaviour):
            return behaviour(params)
        return behaviour

    def attempts(self, path) -> int:
        return sum(1 for p, _ in self.calls if p == path)


def ts(d: date, hour: int = 14, minute: int = 30) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).timestamp())


def chart_json(timestamps, closes, adjcloses=None, *, gmtoffset=-18000, currency="USD",
               tz_name="America/New_York", granularity="1d") -> str:
    """Chart payload TEXT — numbers spelled exactly as given (binary noise included)."""
    def arr(values):
        return "[" + ",".join("null" if v is None else str(v) for v in values) + "]"
    adj = f',"adjclose":[{{"adjclose":{arr(adjcloses)}}}]' if adjcloses is not None else ""
    return ('{"chart":{"result":[{"meta":{"currency":"%s","exchangeTimezoneName":"%s",'
            '"gmtoffset":%d,"dataGranularity":"%s"},"timestamp":%s,'
            '"indicators":{"quote":[{"close":%s}]%s}}],"error":null}}'
            % (currency, tz_name, gmtoffset, granularity, arr(timestamps), arr(closes), adj))


NOT_FOUND_BODY = ('{"chart":{"result":null,"error":{"code":"Not Found",'
                  '"description":"No data found, symbol may be delisted"}}}')


# ── Fixtures ────────────────────────────────────────────────────────────────
def fixture_row(suffix, provider, code, status="active", *, frequency="daily", sort_order=0):
    return {
        "series_key": PREFIX + suffix, "name": f"verify mkt02 {suffix}",
        "category": "Verify", "region": "US", "frequency": frequency,
        "best_view": None, "default_transform": "level", "cost_tier": "free",
        "cost_note": None, "license_class": "public_domain", "source_provider": provider,
        "source_code": code, "source_url": None, "notes": "mkt02 verify fixture",
        "sort_order": sort_order, "ingest_status": status,
    }


_SPECS = [
    # orchestrator bookkeeping + insert / identical / revise
    ("nb1", FAKE, "VMKT02NB1", "active"), ("nb2", FAKE, "VMKT02NB2", "active"),
    ("nb3", FAKE, "VMKT02NB3", "active"),
    # failure isolation
    ("nf_ok1", FAKE, "VMKT02NFOK1", "active"), ("nf_bad", FAKE, "VMKT02NFBAD", "active"),
    ("nf_ok2", FAKE, "VMKT02NFOK2", "active"),
    ("nall1", FAKE, "VMKT02NALL1", "active"), ("nall2", FAKE, "VMKT02NALL2", "active"),
    # unregistered provider
    ("nskip_ok", FAKE, "VMKT02NSKIPOK", "active"), ("nskip", NOADAPTER, "VMKT02NSKIP", "active"),
    # overlap + lost race
    ("nov1", FAKE, "VMKT02NOV1", "active"), ("nov2", FAKE, "VMKT02NOV2", "active"),
    ("nrace", FAKE, "VMKT02NRACE", "active"),
    # secret + entrypoint
    ("nsecret", FAKE, "VMKT02NSECRET", "active"), ("nentry_ok", FAKE, "VMKT02NENTRY", "active"),
    # Yahoo adapter (real class, fake transport) through the orchestrator
    ("nyahoo", "yahoo", "VMKT02NOISE", "active"),
    # Yahoo validate classification — seeded 'deferred' like the six real rows
    ("y_found", "yahoo", "VMKT02FOUND", "deferred"), ("y_missing", "yahoo", "VMKT02MISSING", "deferred"),
    ("y_401", "yahoo", "VMKT02Y401", "deferred"), ("y_403", "yahoo", "VMKT02Y403", "deferred"),
    ("y_429", "yahoo", "VMKT02Y429", "deferred"), ("y_5xx", "yahoo", "VMKT02Y5XX", "deferred"),
    ("y_timeout", "yahoo", "VMKT02YTIMEOUT", "deferred"), ("y_malformed", "yahoo", "VMKT02YMALFORMED", "deferred"),
    # --provider dispatch
    ("d_fred", "fred", "VMKT02DFRED", "pending"), ("d_yahoo", "yahoo", "VMKT02DYAHOO", "deferred"),
    ("d_fred_act", "fred", "VMKT02DFREDACT", "active"), ("d_yahoo_act", "yahoo", "VMKT02DYAHOOACT", "active"),
]
FIXTURES = [fixture_row(s, p, c, st, sort_order=-2000 + i) for i, (s, p, c, st) in enumerate(_SPECS)]
K = {r["series_key"][len(PREFIX):]: r["series_key"] for r in FIXTURES}

FIXTURE_IDS = f"(SELECT id FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%')"
FIXTURE_RUNS = (f"(series_id IN {FIXTURE_IDS} OR batch_id IN (SELECT batch_id FROM {RUNS_T} "
                f"WHERE series_id IN {FIXTURE_IDS} AND batch_id IS NOT NULL) OR batch_id = ANY($1::uuid[]))")


def pts(n: int, start: date = date(2024, 1, 1), base: int = 1) -> list[tuple[date, Decimal]]:
    return [(start + timedelta(days=i), Decimal(base + i) + Decimal("0.25")) for i in range(n)]


# ── Reading helpers (all on the independent `reader` connection) ────────────
async def sid_of(reader, key):
    return await reader.fetchval(f"SELECT id FROM {SERIES_T} WHERE series_key = $1", key)


async def series_row(reader, key):
    return await reader.fetchrow(f"SELECT * FROM {SERIES_T} WHERE series_key = $1", key)


async def active_map(reader, key) -> dict:
    rows = await reader.fetch(
        f"SELECT o.obs_date, o.value FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id "
        "WHERE s.series_key = $1 AND o.valid_to IS NULL AND o.system_to IS NULL", key)
    return {r["obs_date"]: r["value"] for r in rows}


async def history(reader, key) -> list[tuple]:
    rows = await reader.fetch(
        f"SELECT o.id, o.obs_date, o.value, o.valid_from, o.valid_to, o.system_to FROM {OBS_T} o "
        f"JOIN {SERIES_T} s ON s.id = o.series_id WHERE s.series_key = $1 ORDER BY o.obs_date, o.valid_from", key)
    return [tuple(r) for r in rows]


async def obs_count(reader, keys) -> int:
    return await reader.fetchval(
        f"SELECT count(*) FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id "
        "WHERE s.series_key = ANY($1::text[])", list(keys))


async def batch_runs(reader, batch_id) -> list:
    async with platform_scope(reader):
        return await reader.fetch(
            f"SELECT r.*, s.series_key FROM {RUNS_T} r LEFT JOIN {SERIES_T} s ON s.id = r.series_id "
            "WHERE r.batch_id = $1 ORDER BY r.series_id NULLS LAST", batch_id)


async def duplicate_active(reader) -> int:
    return await reader.fetchval(f"""SELECT count(*) FROM (SELECT series_id, obs_date FROM {OBS_T}
        WHERE series_id IN {FIXTURE_IDS} AND valid_to IS NULL AND system_to IS NULL
        GROUP BY 1, 2 HAVING count(*) > 1) d""")


async def nightly_run(conn, reg, keys, trigger="nightly") -> nightly.NightlyResult:
    result = await nightly.run_nightly(conn, guard(reg), fixture_only(keys), trigger)
    CREATED_BATCHES.append(result.batch_id)
    return result


def parse_summary(text: str | None) -> dict:
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", (text or "").split(";")[0])}


# ── Counting helpers ────────────────────────────────────────────────────────
async def fixture_counts(conn) -> dict[str, int]:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(f"SELECT count(*) FROM {RUNS_T} WHERE {FIXTURE_RUNS}", CREATED_BATCHES),
        }


async def real_counts(conn) -> dict[str, int]:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key NOT LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id NOT IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(
                f"SELECT count(*) FROM {RUNS_T} WHERE NOT COALESCE({FIXTURE_RUNS}, false)", CREATED_BATCHES),
        }


async def untouched_counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in UNTOUCHED}


# ── Teardown ────────────────────────────────────────────────────────────────
async def fk_delete_order(conn) -> list[str]:
    """Child-before-parent over the three market_data tables, DERIVED from
    information_schema rather than assumed."""
    rows = await conn.fetch("""
        SELECT ch.table_schema || '.' || ch.table_name AS child,
               pa.table_schema || '.' || pa.table_name AS parent
          FROM information_schema.referential_constraints rc
          JOIN information_schema.table_constraints ch
            ON ch.constraint_schema = rc.constraint_schema AND ch.constraint_name = rc.constraint_name
          JOIN information_schema.table_constraints pa
            ON pa.constraint_schema = rc.unique_constraint_schema
           AND pa.constraint_name = rc.unique_constraint_name
         WHERE pa.table_schema = 'market_data'""")
    tables = {SERIES_T, OBS_T, RUNS_T}
    outside_refs = [(r["child"], r["parent"]) for r in rows if r["child"] not in tables]
    if outside_refs:
        raise SystemExit(f"[FAIL] teardown: tables outside market_data reference it: {outside_refs}")
    children = {t: {r["child"] for r in rows if r["parent"] == t and r["child"] != t} for t in tables}
    order: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not (children[t] & remaining))
        if not ready:
            raise SystemExit(f"[FAIL] teardown: FK cycle among {remaining}")
        order.extend(ready)
        remaining -= set(ready)
    # Observations first, then runs, then series — the order the sprint names,
    # and a valid topological order of the graph above.
    if OBS_T in order and RUNS_T in order and order.index(OBS_T) > order.index(RUNS_T):
        order.remove(OBS_T)
        order.insert(order.index(RUNS_T), OBS_T)
    return order


async def delete_fixtures(conn, order) -> dict[str, int]:
    """By fixture tag only — never a TRUNCATE. Runs go by fixture series_id AND
    by batch_id (summary rows have series_id NULL)."""
    where = {
        OBS_T: (f"series_id IN {FIXTURE_IDS}", []),
        RUNS_T: (FIXTURE_RUNS, [CREATED_BATCHES]),
        SERIES_T: (f"series_key LIKE '{PREFIX}%'", []),
    }
    deleted = {}
    async with platform_scope(conn):
        for table in order:
            clause, args = where[table]
            status = await conn.execute(f"DELETE FROM {table} WHERE {clause}", *args)
            deleted[table] = int(status.split()[-1])
    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A — Yahoo adapter, no database
# ═══════════════════════════════════════════════════════════════════════════
NOW = datetime(2024, 3, 8, 15, 0, tzinfo=timezone.utc)  # 10:00 in New York, 00:00 Mar 9 in Tokyo


def a_yahoo_parsing() -> None:
    # NY (-5h): 02:00 UTC on Mar 5 is 21:00 on Mar 4 locally.
    ny = yahoo.loads(chart_json([ts(date(2024, 3, 5), 2, 0), ts(date(2024, 3, 6), 14)], ["10.5", "11.5"]))
    p_ny = yahoo.parse_chart(ny, "X", now=NOW)
    # Tokyo (+9h): 20:00 UTC on Mar 4 is 05:00 on Mar 5 locally.
    tk = yahoo.loads(chart_json([ts(date(2024, 3, 4), 20, 0)], ["7"], gmtoffset=32400, tz_name="Asia/Tokyo"))
    p_tk = yahoo.parse_chart(tk, "X", now=NOW)
    check("Y1 epoch → exchange-LOCAL date via meta.gmtoffset (UTC Mar 5 02:00 → NY Mar 4; UTC Mar 4 20:00 → Tokyo Mar 5)",
          [d for d, _ in p_ny.points] == [date(2024, 3, 4), date(2024, 3, 6)]
          and [d for d, _ in p_tk.points] == [date(2024, 3, 5)],
          "a UTC date would shift every bar near midnight onto the wrong trading day and misalign the chart",
          f"ny={p_ny.points} tokyo={p_tk.points}")

    payload = yahoo.loads(chart_json(
        [ts(date(2024, 3, 4)), ts(date(2024, 3, 5)), ts(date(2024, 3, 6)), ts(date(2024, 3, 6), 15),
         ts(date(2024, 3, 7)), ts(date(2024, 3, 8)), ts(date(2024, 3, 9))],
        ["1", None, "10", "11", "NaN", "5", "6"]))
    p = yahoo.parse_chart(payload, "X", now=NOW)
    got = dict(p.points)
    check("Y2 null closes (and NaN) are skipped and COUNTED, never stored",
          p.skipped_null == 2 and date(2024, 3, 5) not in got and date(2024, 3, 7) not in got,
          "a null stored as 0 would draw a false crash; counting it keeps the gap visible",
          f"skipped={p.skipped_null} dates={sorted(got)}")
    check("Y3 a bar dated today (exchange-local) or later is DROPPED as an incomplete intraday bar",
          date(2024, 3, 8) not in got and date(2024, 3, 9) not in got and p.dropped_incomplete == 2,
          "futures trade nearly around the clock; today's bar would be revised tomorrow, every day",
          f"dropped={p.dropped_incomplete} dates={sorted(got)}")
    check("Y4 a repeated date keeps the LAST bar (11, not 10) and yields one point",
          got.get(date(2024, 3, 6)) == Decimal("11.0000") and p.duplicates_replaced == 1
          and len([d for d, _ in p.points if d == date(2024, 3, 6)]) == 1,
          "two points for one date would violate uq_indicator_obs_point and abort the whole series",
          f"value={got.get(date(2024, 3, 6))} replaced={p.duplicates_replaced}")

    text = chart_json([ts(date(2024, 3, 4))], ["2180.969970703125"], ["2100.123456789"])
    tree = yahoo.loads(text)
    floats: list = []

    def walk(node):
        if isinstance(node, float):
            floats.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(tree)
    plain_has_float = isinstance(json.loads(text)["chart"]["result"][0]["indicators"]["quote"][0]["close"][0], float)
    refused = False
    try:
        yahoo.normalize(2180.97)  # a float must be refused outright
    except ValueError:
        refused = True
    pp = yahoo.parse_chart(tree, "X", now=NOW)
    check("Y5 every value is a Decimal and NO float exists anywhere in the parsed payload; a float is refused",
          not floats and plain_has_float and refused and all(isinstance(v, Decimal) for _, v in pp.points),
          "plain json.loads makes a float (shown here, so the check is not vacuous); one float hop and "
          "equality-based diffing breaks", f"floats={floats[:3]} refused={refused}")


def a_yahoo_noise_pure() -> None:
    a = yahoo.normalize(Decimal("2180.969970703125"))
    b = yahoo.normalize(Decimal("2180.9700000001"))
    check("Y6a 2180.969970703125 and 2180.9700000001 normalize to the IDENTICAL value 2180.9700",
          a == b and str(a) == str(b) == "2180.9700",
          "Yahoo's binary noise differs between calls; without 4dp HALF_EVEN quantizing, every night would revise history",
          f"{a} vs {b}")


async def a_yahoo_transport() -> None:
    fake = FakeYahooTransport()
    adapter = YahooAdapter(fake, min_interval=0, sleep=nosleep, now=lambda: NOW)
    body = chart_json([ts(date(2024, 3, 4))], ["100.5"], ["90.25"])
    for path in REAL_SYMBOLS.values():
        fake.routes[CHART_PREFIX + path] = YahooResponse(200, body)
    results = {}
    for sym in REAL_SYMBOLS:
        results[sym] = await adapter.fetch_series({"series_key": sym, "source_code": sym})
    paths = [p for p, _ in fake.calls]
    expected = [CHART_PREFIX + e for e in REAL_SYMBOLS.values()]

    seen: list[tuple[bytes, str]] = []

    def handler(request: httpx.Request):
        seen.append((request.url.raw_path.split(b"?")[0], request.headers.get("user-agent", "")))
        return httpx.Response(200, text=body)
    real = YahooAdapter(yahoo.HttpxTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler))),
                        min_interval=0, sleep=nosleep, now=lambda: NOW)
    for sym in REAL_SYMBOLS:
        await real.fetch_series({"series_key": sym, "source_code": sym})
    await real.aclose()
    wire = [p.decode() for p, _ in seen]
    check("Y8 all six real symbols encode correctly: ^RUT→%5ERUT, GC=F→GC%3DF, DX-Y.NYB unchanged "
          "(fake transport's captured path AND the real httpx transport's wire path), browser User-Agent sent",
          paths == expected and wire == expected and all("Mozilla/5.0" in ua for _, ua in seen),
          "an unencoded '^' or '=' is a different (or invalid) URL; Yahoo refuses obvious script clients",
          f"fake={paths} wire={wire}")
    check("Y7 a payload carrying BOTH close and adjclose stores the RAW close (100.5000), never adjclose (90.25)",
          all([v for _, v in r.points] == [Decimal("100.5000")] for r in results.values()),
          "adjusted close restates the whole history at every dividend — every ETF row would be revised each quarter",
          str({k: r.points for k, r in list(results.items())[:2]}))


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A — registry, dispatch, validate classification (database)
# ═══════════════════════════════════════════════════════════════════════════
CHART_PREFIX = yahoo.CHART_PATH_PREFIX


async def a_registry_contract() -> None:
    reg = adapters.build_registry({"FRED_API_KEY": SENTINEL})
    try:
        ok = (set(reg) == {"fred", "yahoo"} and isinstance(reg["fred"], FredAdapter)
              and isinstance(reg["yahoo"], YahooAdapter))
    finally:
        await adapters.close_registry(reg)
    missing = None
    try:
        adapters.build_registry({})
    except adapters.MissingConfiguration as exc:
        missing = str(exc)
    check("R1 the REAL registry contains exactly 'fred' and 'yahoo' (constructed, never called); without "
          "FRED_API_KEY it refuses, naming the variable",
          ok and missing == "FRED_API_KEY",
          "an unregistered provider is skipped by the nightly; a missing registration would silently stop a whole source",
          f"keys={sorted(reg)} missing={missing!r}")


async def a_dispatch(conn, reader) -> None:
    parser = mdi.build_parser()
    defaults = (parser.parse_args(["validate"]).provider, parser.parse_args(["backfill"]).provider,
                parser.parse_args(["validate", "--provider", "yahoo"]).provider)
    fake_fred, fake_yahoo = FakeAdapter("fred"), FakeAdapter("yahoo")
    guard({"fred": fake_fred, "yahoo": fake_yahoo})
    for code in ("VMKT02DFRED", "VMKT02DFREDACT"):
        fake_fred.data[code] = pts(2)
    for code in ("VMKT02DYAHOO", "VMKT02DYAHOOACT"):
        fake_yahoo.data[code] = pts(2)
    keys = [K["d_fred"], K["d_yahoo"], K["d_fred_act"], K["d_yahoo_act"]]
    with contextlib.redirect_stdout(io.StringIO()):
        await mdi.run_validate(conn, fake_fred, keys)          # no provider → default
        await mdi.run_backfill(conn, fake_fred, keys)
    fred_v, fred_b = list(fake_fred.validated), list(fake_fred.fetched)
    with contextlib.redirect_stdout(io.StringIO()):
        await mdi.run_validate(conn, fake_yahoo, keys, "yahoo")
        await mdi.run_backfill(conn, fake_yahoo, keys, "yahoo")
    check("D1 no --provider flag → 'fred'; validate/backfill then select ONLY fred rows; --provider yahoo selects "
          "ONLY yahoo rows (validate also picks up 'deferred')",
          defaults == ("fred", "fred", "yahoo")
          # backfill sees both rows of its provider: validate just made the pending/deferred one active
          and sorted(fred_v) == ["VMKT02DFRED", "VMKT02DFREDACT"] and sorted(fred_b) == ["VMKT02DFRED", "VMKT02DFREDACT"]
          and sorted(fake_yahoo.validated) == ["VMKT02DYAHOO", "VMKT02DYAHOOACT"]
          and sorted(fake_yahoo.fetched) == ["VMKT02DYAHOO", "VMKT02DYAHOOACT"],
          "mkt01's verify and operator habits run without the flag; sending a Yahoo ticker to FRED (or the reverse) "
          "would fail or mis-mark every row",
          f"defaults={defaults} fred v={fred_v} b={fred_b} yahoo v={fake_yahoo.validated} b={fake_yahoo.fetched}")
    yrow = await series_row(reader, K["d_yahoo"])
    async with platform_scope(reader):
        trig = sorted(r["run_trigger"] for r in await reader.fetch(
            f"SELECT run_trigger FROM {RUNS_T} WHERE series_id = $1", await sid_of(reader, K["d_yahoo_act"])))
    check("D2 `validate --provider yahoo` turns a 'deferred' Yahoo row active; runs are logged as 'validate'/'backfill'",
          yrow["ingest_status"] == "active" and trig == ["backfill", "validate"],
          "this is the only path by which the six real Yahoo rows become active for the nightly",
          f"status={yrow['ingest_status']} triggers={trig}")


async def a_yahoo_classification(conn, reader) -> None:
    fake = FakeYahooTransport()
    adapter = guard({"yahoo": YahooAdapter(fake, min_interval=0, sleep=nosleep, now=lambda: NOW)})["yahoo"]
    r = lambda code: CHART_PREFIX + code  # noqa: E731
    fake.routes[r("VMKT02FOUND")] = YahooResponse(200, chart_json([ts(date(2024, 3, 4))], ["5"], currency="USD"))
    fake.routes[r("VMKT02MISSING")] = YahooResponse(404, NOT_FOUND_BODY)
    fake.routes[r("VMKT02Y401")] = YahooResponse(401, '{"finance":{"error":{"code":"Unauthorized"}}}')
    fake.routes[r("VMKT02Y403")] = YahooResponse(403, "<html>Forbidden</html>")
    fake.routes[r("VMKT02Y429")] = YahooResponse(429, "Too Many Requests")
    fake.routes[r("VMKT02Y5XX")] = YahooResponse(503, "")

    def timeout(_params):
        raise yahoo.TransportError("ReadTimeout")
    fake.routes[r("VMKT02YTIMEOUT")] = timeout
    fake.routes[r("VMKT02YMALFORMED")] = YahooResponse(200, "<html>not json at all")
    keys = [K[k] for k in ("y_found", "y_missing", "y_401", "y_403", "y_429", "y_5xx", "y_timeout", "y_malformed")]
    with contextlib.redirect_stdout(io.StringIO()):
        await mdi.run_validate(conn, adapter, keys, "yahoo")
    rows = {k: await series_row(reader, K[k]) for k in
            ("y_found", "y_missing", "y_401", "y_403", "y_429", "y_5xx", "y_timeout", "y_malformed")}
    f = rows["y_found"]
    check("Y9a validate success → 'active', units = meta currency (USD), last_validated_at set, last_error NULL, "
          "frequency untouched",
          f["ingest_status"] == "active" and f["units"] == "USD" and f["last_validated_at"] is not None
          and f["last_error"] is None and f["frequency"] == "daily",
          "units come only from validation; Yahoo knows the currency, not a frequency", str(dict(f)))
    m = rows["y_missing"]
    check("Y9b Yahoo's explicit 'Not Found' chart error → invalid_code with a last_error",
          m["ingest_status"] == "invalid_code" and "not found" in (m["last_error"] or "").lower(),
          "a ticker Yahoo genuinely does not know must be visibly quarantined",
          f"status={m['ingest_status']} error={m['last_error']!r}")
    transient = {k: (rows[k]["ingest_status"], bool(rows[k]["last_error"]))
                 for k in ("y_401", "y_403", "y_429", "y_5xx", "y_timeout", "y_malformed")}
    attempts = {"401": fake.attempts(r("VMKT02Y401")), "429": fake.attempts(r("VMKT02Y429")),
                "5xx": fake.attempts(r("VMKT02Y5XX")), "timeout": fake.attempts(r("VMKT02YTIMEOUT"))}
    check("Y9c HTTP 401, 403, 429, 5xx, a timeout and a malformed body each leave ingest_status 'deferred' with "
          "last_error set (429/5xx/timeout retried: 4 attempts; 401 not retried)",
          all(v == ("deferred", True) for v in transient.values())
          and attempts == {"401": 1, "429": 4, "5xx": 4, "timeout": 4},
          "Yahoo blocks datacenter IPs with 401/403 — that says nothing about the symbol; transient must NEVER be invalid",
          f"{transient} attempts={attempts}")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A — orchestrator (database)
# ═══════════════════════════════════════════════════════════════════════════
async def a_bookkeeping_and_diff(conn, reader) -> None:
    fake = FakeAdapter()
    data = {"VMKT02NB1": pts(5), "VMKT02NB2": pts(3, base=10), "VMKT02NB3": pts(4, base=100)}
    fake.data.update(data)
    keys = [K["nb1"], K["nb2"], K["nb3"]]
    r1 = await nightly_run(conn, {FAKE: fake}, keys)
    runs = await batch_runs(reader, r1.batch_id)
    per = [x for x in runs if x["series_id"] is not None]
    summ = [x for x in runs if x["series_id"] is None]
    s = summ[0] if len(summ) == 1 else None
    totals = parse_summary(s["error"] if s else None)
    sums = tuple(sum(x[c] for x in per) for c in ("rows_inserted", "rows_revised", "rows_unchanged"))
    check("N1 bookkeeping: 3 fixture series → 3 per-series rows + exactly ONE summary row (series_id NULL), one "
          "shared batch_id, all run_trigger='nightly'; summary totals = sum of per-series rows",
          len(per) == 3 and len(summ) == 1 and {x["batch_id"] for x in runs} == {r1.batch_id}
          and {x["run_trigger"] for x in runs} == {"nightly"}
          and s is not None and (s["rows_inserted"], s["rows_revised"], s["rows_unchanged"]) == sums
          and totals.get("attempted") == 3 and totals.get("succeeded") == 3 and totals.get("failed") == 0
          and totals.get("skipped_no_adapter") == 0 and s["status"] == "success"
          and all(x["started_at"] <= x["finished_at"] for x in runs),
          "the batch is how the operator (and Phase B) audits one night; totals that disagree with their rows lie",
          f"per={len(per)} summary={len(summ)} sums={sums} summary_row={dict(s) if s else None}")

    count1 = await obs_count(reader, keys)
    r2 = await nightly_run(conn, {FAKE: fake}, keys)
    count2 = await obs_count(reader, keys)
    changed_date = data["VMKT02NB1"][2][0]
    fake.data["VMKT02NB1"] = [(d, Decimal("999.5") if d == changed_date else v) for d, v in data["VMKT02NB1"]]
    r3 = await nightly_run(conn, {FAKE: fake}, keys)
    hist = [h for h in await history(reader, K["nb1"]) if h[1] == changed_date]
    active_rows = [h for h in hist if h[4] is None and h[5] is None]
    closed = [h for h in hist if h[4] is not None]
    check("N2 first run inserts 12; an IDENTICAL second run writes nothing (row count unchanged); one changed value "
          "revises exactly ONE point (old row valid_to set, one active row, two history rows) — re-read independently",
          r1.rows_inserted == 12 and count1 == 12
          and (r2.rows_inserted, r2.rows_revised, r2.rows_unchanged) == (0, 0, 12) and count2 == count1
          and (r3.rows_inserted, r3.rows_revised) == (0, 1) and len(hist) == 2 and len(active_rows) == 1
          and active_rows[0][2] == Decimal("999.5") and len(closed) == 1 and closed[0][4] == active_rows[0][3]
          and await obs_count(reader, keys) == count1 + 1,
          "re-pulling full history every night is only safe if unchanged history writes nothing and a revision "
          "keeps what was shown before (Rule 3)",
          f"r1={r1.rows_inserted} r2={(r2.rows_inserted, r2.rows_revised, r2.rows_unchanged)} "
          f"r3={(r3.rows_inserted, r3.rows_revised)} history={hist}")


async def a_failure_isolation(conn, reader) -> None:
    fake = FakeAdapter()
    for code in ("VMKT02NFOK1", "VMKT02NFBAD", "VMKT02NFOK2"):
        fake.data[code] = pts(3)
    keys = [K["nf_ok1"], K["nf_bad"], K["nf_ok2"]]
    await nightly_run(conn, {FAKE: fake}, keys)
    before_bad = await history(reader, K["nf_bad"])
    before_row = await series_row(reader, K["nf_bad"])
    fake.data["VMKT02NFOK1"] = pts(4)
    fake.data["VMKT02NFOK2"] = pts(4)
    fake.data["VMKT02NFBAD"] = RuntimeError(
        f"upstream exploded mid-fetch: GET https://api.stlouisfed.org/fred/series/observations"
        f"?series_id=VMKT02NFBAD&api_key={SENTINEL} (key {SENTINEL})")
    r = await nightly_run(conn, {FAKE: fake}, keys)
    after_bad = await history(reader, K["nf_bad"])
    after_row = await series_row(reader, K["nf_bad"])
    ok1, ok2 = await active_map(reader, K["nf_ok1"]), await active_map(reader, K["nf_ok2"])
    summ = [x for x in await batch_runs(reader, r.batch_id) if x["series_id"] is None]
    err = after_row["last_error"] or ""
    check("N3 one series raising mid-fetch: the others (before AND after it) still persist their new point; the failed "
          "series' data is unchanged (before/after); its last_error is set and scrubbed; summary 'partial'; result "
          "lists its series_key",
          len(ok1) == 4 and len(ok2) == 4 and after_bad == before_bad and len(before_bad) == 3
          and "RuntimeError" in err and SENTINEL not in err
          and len(summ) == 1 and summ[0]["status"] == "partial" and r.status == "partial"
          and r.failed_series == [K["nf_bad"]] and r.failed == 1 and r.succeeded == 2
          and len(fake.raised) == 1 and SENTINEL in fake.raised[0],
          "one bad source at 2am must not leave 56 other series stale, and must not damage its own history",
          f"ok={len(ok1)}/{len(ok2)} bad_unchanged={after_bad == before_bad} err={err!r} "
          f"status={r.status} failed={r.failed_series}")
    check("N4a a failed fetch never changes ingest_status and never deletes data",
          before_row["ingest_status"] == after_row["ingest_status"] == "active" and len(after_bad) == len(before_bad),
          "a transient outage must not demote a working series or erase what it already had",
          f"status {before_row['ingest_status']}->{after_row['ingest_status']} rows {len(before_bad)}->{len(after_bad)}")

    fake_all = FakeAdapter()
    fake_all.data["VMKT02NALL1"] = RuntimeError("source down")
    fake_all.data["VMKT02NALL2"] = TimeoutError("source timed out")
    ra = await nightly_run(conn, {FAKE: fake_all}, [K["nall1"], K["nall2"]])
    summ_all = [x for x in await batch_runs(reader, ra.batch_id) if x["series_id"] is None]
    check("N3b the same run with EVERY series failing → summary status 'failed'",
          ra.status == "failed" and len(summ_all) == 1 and summ_all[0]["status"] == "failed"
          and sorted(ra.failed_series) == sorted([K["nall1"], K["nall2"]]),
          "'partial' would understate a night where nothing at all refreshed",
          f"status={ra.status} summary={[x['status'] for x in summ_all]}")


async def a_yahoo_through_orchestrator(conn, reader) -> None:
    key = K["nyahoo"]
    fake = FakeYahooTransport()
    reg = guard({"yahoo": YahooAdapter(fake, min_interval=0, sleep=nosleep)})
    days = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
    path = CHART_PREFIX + "VMKT02NOISE"
    fake.routes[path] = YahooResponse(200, chart_json(
        [ts(d) for d in days], ["2180.969970703125", "2190.5", "2201.25"], ["2100.1", "2110.2", "2120.3"]))
    r1 = await nightly_run(conn, reg, [key])
    fake.routes[path] = YahooResponse(200, chart_json(
        [ts(d) for d in days], ["2180.9700000001", "2190.50000000003", "2201.2499999999"], ["1", "2", "3"]))
    count1 = await obs_count(reader, [key])
    r2 = await nightly_run(conn, reg, [key])
    stored = await active_map(reader, key)
    check("Y6b re-running over NOISY data revises nothing: second Yahoo run 0 inserted, 0 revised, row count unchanged; "
          "stored values are the raw closes at 4dp",
          r1.rows_inserted == 3 and (r2.rows_inserted, r2.rows_revised, r2.rows_unchanged) == (0, 0, 3)
          and await obs_count(reader, [key]) == count1 == 3
          and stored == {days[0]: Decimal("2180.9700"), days[1]: Decimal("2190.5000"), days[2]: Decimal("2201.2500")},
          "noise-driven revisions would bloat history nightly and make every revision report meaningless",
          f"r1={r1.rows_inserted} r2={(r2.rows_inserted, r2.rows_revised, r2.rows_unchanged)} stored={stored}")

    before = await history(reader, key)
    fake.routes[path] = YahooResponse(503, "")
    r3 = await nightly_run(conn, reg, [key])
    row = await series_row(reader, key)
    check("N4b a TRANSIENT Yahoo failure (persistent 503) inside the nightly: series failed, ingest_status still "
          "'active', data unchanged, last_error set",
          r3.failed_series == [key] and row["ingest_status"] == "active" and await history(reader, key) == before
          and "503" in (row["last_error"] or ""),
          "Yahoo may block Render's IPs any night; that must cost one night's refresh, not the series",
          f"failed={r3.failed_series} status={row['ingest_status']} error={row['last_error']!r}")


async def a_unregistered(conn, reader) -> None:
    fake = FakeAdapter()
    fake.data["VMKT02NSKIPOK"] = pts(2)
    r = await nightly_run(conn, {FAKE: fake}, [K["nskip_ok"], K["nskip"]])
    runs = await batch_runs(reader, r.batch_id)
    summ = [x for x in runs if x["series_id"] is None]
    skipped_rows = [x for x in runs if x["series_key"] == K["nskip"]]
    check("N5 a series whose source_provider has no adapter is SKIPPED: counted in skipped_no_adapter, absent from "
          "failed, no run row or data, and the summary stays 'success'",
          r.skipped_no_adapter == 1 and K["nskip"] in r.skipped_series and K["nskip"] not in r.failed_series
          and r.failed == 0 and r.status == "success" and len(summ) == 1 and summ[0]["status"] == "success"
          and parse_summary(summ[0]["error"]).get("skipped_no_adapter") == 1 and not skipped_rows
          and await obs_count(reader, [K["nskip"]]) == 0,
          "Shiller/World Bank/IMF/BIS/OECD have no adapter until mkt02b; they must not page anyone nightly",
          f"skipped={r.skipped_no_adapter} status={r.status} summary={[dict(x) for x in summ]}")


async def a_overlap(conn, reader, dsn) -> None:
    keys = [K["nov1"], K["nov2"]]
    seed = FakeAdapter()
    seed.data["VMKT02NOV1"] = pts(6)
    seed.data["VMKT02NOV2"] = pts(6, base=50)
    await nightly_run(conn, {FAKE: seed}, keys)

    new1 = [(d, v + Decimal("1") if i % 2 == 0 else v) for i, (d, v) in enumerate(pts(6))] + \
        [(date(2024, 1, 7), Decimal("70.5")), (date(2024, 1, 8), Decimal("71.5"))]
    new2 = [(d, v + Decimal("2") if i < 3 else v) for i, (d, v) in enumerate(pts(6, base=50))] + \
        [(date(2024, 1, 7), Decimal("80.5"))]
    a, b = FakeAdapter(delay=0.2), FakeAdapter(delay=0.2)
    for f in (a, b):
        f.data["VMKT02NOV1"] = list(new1)
        f.data["VMKT02NOV2"] = list(new2)
    c2 = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    c3 = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        results = await asyncio.gather(
            nightly.run_nightly(c2, guard({FAKE: a}), fixture_only(keys)),
            nightly.run_nightly(c3, guard({FAKE: b}), fixture_only(keys)),
            return_exceptions=True)
    finally:
        await c2.close()
        await c3.close()
    crashed = [x for x in results if isinstance(x, BaseException)]
    ok = [x for x in results if not isinstance(x, BaseException)]
    CREATED_BATCHES.extend(x.batch_id for x in ok)
    dups = await duplicate_active(reader)
    final1, final2 = await active_map(reader, K["nov1"]), await active_map(reader, K["nov2"])
    handled = [s for x in ok for s in x.series if s.status == "failed"]
    for s in handled:
        find(f"overlap: {s.series_key} lost to the concurrent run as a HANDLED failure: {s.error}")
    both_ok = not handled and len(ok) == 2
    per_series = {}
    for s in [s for x in ok for s in x.series]:
        t = per_series.setdefault(s.series_key, [0, 0, 0])
        t[0] += s.inserted
        t[1] += s.revised
        t[2] += s.lost_races
    exactly_once = (not both_ok) or (per_series.get(K["nov1"], [0])[:2] == [2, 3]
                                     and per_series.get(K["nov2"], [0])[:2] == [1, 3])
    check("N6a two orchestrator runs CONCURRENTLY over the same fixture series: no crash, exactly one active row per "
          "(series, date), final values = the new data, and every point written exactly once (losing writes counted "
          "unchanged) or the loser logged as a handled failure",
          not crashed and len(ok) == 2 and dups == 0 and final1 == dict(new1) and final2 == dict(new2) and exactly_once,
          "a manual run during the nightly (or a slow night overlapping the next) must never duplicate or crash",
          f"crashed={[type(x).__name__ for x in crashed]} dups={dups} per_series(ins,rev,lost)={per_series} "
          f"handled={len(handled)}")

    # The lost-race path, directly: close the active row between the read and the write.
    key = K["nrace"]
    sid = await sid_of(reader, key)
    d0, d_new = date(2024, 1, 1), date(2024, 2, 1)
    seed2 = FakeAdapter()
    seed2.data["VMKT02NRACE"] = [(d0, Decimal("1"))]
    await nightly_run(conn, {FAKE: seed2}, [key])
    other = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        async with platform_scope(conn):
            plan = ingest.plan_writes(await ingest.read_active(conn, sid),
                                      [(d0, Decimal("2")), (d_new, Decimal("5"))])
            async with platform_scope(other):  # the winner: closes d0's row and writes both points, COMMITTED
                won = await ingest.write_observations(other, sid, [(d0, Decimal("3")), (d_new, Decimal("6"))])
            counts = await ingest.apply_plan(conn, sid, plan)
    finally:
        await other.close()
    hist = await history(reader, key)
    final = await active_map(reader, key)
    values = sorted(h[2] for h in hist)
    check("N6b lost race, directly: the row is closed by another committed writer between our read and our write → "
          "our close matches 0 rows, NO insert happens, both points count as unchanged; the winner's values stand",
          len(plan.revisions) == 1 and len(plan.inserts) == 1 and (won.revised, won.inserted) == (1, 1)
          and (counts.inserted, counts.revised, counts.unchanged, counts.lost_races) == (0, 0, 2, 2)
          and final == {d0: Decimal("3"), d_new: Decimal("6")} and values == [Decimal("1"), Decimal("3"), Decimal("6")]
          and await duplicate_active(reader) == 0,
          "inserting after a close that matched nothing would create a second active value — the exact bug the "
          "unique index exists to catch, here avoided rather than crashed into",
          f"plan=rev{len(plan.revisions)}/ins{len(plan.inserts)} ours={counts} winner={won} final={final} rows={values}")


def a_staleness() -> None:
    today = date(2026, 10, 3)
    bad = []
    for freq, limit in staleness.STALENESS_LIMIT_DAYS.items():
        inside = staleness.stale_series([{"series_key": "in", "frequency": freq,
                                          "last_observation_date": today - timedelta(days=limit)}], today)
        outside = staleness.stale_series([{"series_key": "out", "frequency": freq,
                                           "last_observation_date": today - timedelta(days=limit + 1)}], today)
        if inside or [s.series_key for s in outside] != ["out"]:
            bad.append((freq, limit, inside, outside))
    nulls = staleness.stale_series([
        {"series_key": "n_daily", "frequency": "daily", "last_observation_date": None, "ingest_status": "active"},
        {"series_key": "n_meeting", "frequency": "per_meeting", "last_observation_date": None},
        {"series_key": "n_deferred", "frequency": "daily", "last_observation_date": None, "ingest_status": "deferred"},
    ], today)
    never = staleness.stale_series([
        {"series_key": "pm", "frequency": "per_meeting", "last_observation_date": date(1990, 1, 1)},
        {"series_key": "ir", "frequency": "irregular", "last_observation_date": date(1990, 1, 1)},
    ], today)
    check("S1 staleness boundaries: for daily 10 / weekly 21 / monthly 120 / quarterly 200 / semiannual 400 days, "
          "exactly-the-limit is NOT stale and limit+1 IS; NULL last date on an active series is always flagged; "
          "per_meeting and irregular are never flagged on age",
          not bad and staleness.STALENESS_LIMIT_DAYS == {"daily": 10, "weekly": 21, "monthly": 120,
                                                         "quarterly": 200, "semiannual": 400}
          and sorted(s.series_key for s in nulls) == ["n_daily", "n_meeting"] and never == [],
          "an off-by-one here either cries wolf every Monday or hides a dead series for a week",
          f"bad={bad} nulls={[s.series_key for s in nulls]} never={never}")


async def a_entrypoint_and_secrets(conn, reader, dsn) -> list[str]:
    """Exit codes, the alert hook, and the runtime no-secret proof via the REAL entrypoint."""
    captured: list[str] = []

    # Missing-env path.
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code_missing = await mdn.run({"DATABASE_URL": f"postgresql://u:{SENTINEL_PW}@db.invalid/x"})
    out_none = io.StringIO()
    with contextlib.redirect_stdout(out_none):
        code_none = await mdn.run({})
    captured += [out.getvalue(), out_none.getvalue()]
    R = nightly.NightlyResult
    decisions = (mdn.exit_code(R(uuid.uuid4(), "nightly", attempted=5, succeeded=5, skipped_no_adapter=3)),
                 mdn.exit_code(R(uuid.uuid4(), "nightly", attempted=5, succeeded=4, failed=1)),
                 mdn.exit_code(None))

    # Real entrypoint, fixture selection, fake registry, fake alert sink.
    alerts: list[nightly.NightlyResult] = []

    async def fake_alert(_conn, result):
        alerts.append(result)

    fake = FakeAdapter()
    fake.data["VMKT02NENTRY"] = pts(2)
    fake.data["VMKT02NSECRET"] = RuntimeError(
        f"GET https://api.stlouisfed.org/fred/series/observations?series_id=X&api_key={SENTINEL} "
        f"failed; postgresql://app_service:{SENTINEL_PW}@db.example/postgres; key={SENTINEL}")
    env = {"DATABASE_URL": dsn, "FRED_API_KEY": SENTINEL}
    out_fail, err_fail = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_fail), contextlib.redirect_stderr(err_fail):
        code_fail = await mdn.run(env, registry=guard({FAKE: fake}),
                                  series_selection=fixture_only([K["nentry_ok"], K["nsecret"]]), alert=fake_alert)
    alerts_after_fail = len(alerts)
    out_ok = io.StringIO()
    with contextlib.redirect_stdout(out_ok):
        code_ok = await mdn.run(env, registry=guard({FAKE: fake}),
                                series_selection=fixture_only([K["nentry_ok"]]), alert=fake_alert)
    CREATED_BATCHES.extend(a.batch_id for a in alerts)
    for text in (out_fail.getvalue(), out_ok.getvalue()):
        for m in re.findall(r"batch ([0-9a-f-]{36})", text):
            CREATED_BATCHES.append(uuid.UUID(m))
    captured += [out_fail.getvalue(), err_fail.getvalue(), out_ok.getvalue()]

    check("E1 exit decision: 0 for all-success (skipped_no_adapter included), 1 for any failure, 1 for a crashed "
          "batch; the missing-env path returns 2 and names the variable NAMES only",
          decisions == (0, 1, 1) and code_missing == 2 and code_none == 2
          and "FRED_API_KEY" in out.getvalue() and "DATABASE_URL" not in out.getvalue()
          and "DATABASE_URL" in out_none.getvalue() and "FRED_API_KEY" in out_none.getvalue()
          and SENTINEL_PW not in out.getvalue()
          and code_fail == 1 and code_ok == 0,
          "a non-zero exit is what makes Render mark the run failed — the primary alarm for a nightly job",
          f"decisions={decisions} missing={code_missing}/{code_none} fail={code_fail} ok={code_ok} "
          f"msg={out.getvalue().strip()!r}")
    fail_alert = alerts[0] if alerts else None
    detail = ""
    if fail_alert is not None:
        from services.workflow_todos import market_data_failure_detail
        detail = market_data_failure_detail(fail_alert.batch_id, fail_alert.failures)
    check("E2 one alert per FAILED batch (none for a clean batch), carrying the batch id, the failed series_key and "
          "its scrubbed error",
          alerts_after_fail == 1 and len(alerts) == 1 and fail_alert.failed_series == [K["nsecret"]]
          and str(fail_alert.batch_id) in detail and K["nsecret"] in detail and SENTINEL not in detail
          and "RuntimeError" in detail,
          "Task 1g found a platform alert mechanism (member_todos via workflow_todos); a clean night must not raise one",
          f"alerts={len(alerts)} detail={detail[:200]!r}")
    return captured + [detail, repr(alerts)]


async def a_no_secret(reader, captured: list[str]) -> None:
    async with platform_scope(reader):
        stored = [r["e"] for r in await reader.fetch(
            f"SELECT last_error AS e FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%' AND last_error IS NOT NULL "
            f"UNION ALL SELECT error FROM {RUNS_T} WHERE {FIXTURE_RUNS} AND error IS NOT NULL", CREATED_BATCHES)]
    secret_row = await series_row(reader, K["nsecret"])
    haystack = "\n".join(captured + stored)
    exercised = bool(secret_row["last_error"]) and "RuntimeError" in (secret_row["last_error"] or "")
    check("NS1 a fake adapter (identity scrub) raising an exception containing a sentinel API key and DB password: "
          "the sentinels appear in NO stdout, stderr, stored last_error/error, alert text or result object",
          exercised and SENTINEL not in haystack and SENTINEL_PW not in haystack,
          "FRED takes its key in the query string; the orchestrator must make errors safe even when an adapter does not",
          f"exercised={exercised} key_leaked={SENTINEL in haystack} pw_leaked={SENTINEL_PW in haystack} "
          f"stored_sample={(secret_row['last_error'] or '')[:160]!r}")

    forbidden_names = {"url", "params", "query", "api_key", "request", "response"}
    forbidden_attrs = {"url", "params", "api_key", "_api_key", "request"}
    files = [API_DIR / "services" / "market_data" / f for f in
             ("adapters.py", "yahoo.py", "nightly.py", "staleness.py", "ingest.py")] + \
        [HERE.parent / "market_data_nightly.py", HERE.parent / "market_data_ingest.py"]
    offenders = []
    outputs = 0
    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            is_out = (isinstance(fn, ast.Name) and fn.id == "print") or (
                isinstance(fn, ast.Attribute) and fn.attr in {"debug", "info", "warning", "error",
                                                              "exception", "critical", "log", "write"})
            if not is_out:
                continue
            outputs += 1
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                for sub in ast.walk(arg):
                    if (isinstance(sub, ast.Name) and sub.id in forbidden_names) or \
                       (isinstance(sub, ast.Attribute) and sub.attr in forbidden_attrs):
                        offenders.append(f"{path.name}:{node.lineno}")
    check("NS2 static: no print/log call in the new/changed modules formats a URL, params, request or key",
          not offenders and outputs > 0 and all(p.exists() for p in files),
          "the runtime proof covers the paths the fakes reach; this covers every output call", str(offenders))


async def phase_a(conn, reader, dsn) -> None:
    order = await fk_delete_order(reader)
    leftover = await delete_fixtures(conn, order)
    if any(leftover.values()):
        find(f"removed fixture rows left by an earlier interrupted run: {leftover}")
    base_untouched = await untouched_counts(reader)
    base_real = await real_counts(reader)

    saved_key = os.environ.get("FRED_API_KEY")
    os.environ["FRED_API_KEY"] = SENTINEL  # as on Render: the real key is in the environment
    try:
        col = await reader.fetchval(
            "SELECT data_type FROM information_schema.columns WHERE table_schema='market_data' "
            "AND table_name='indicator_ingest_runs' AND column_name='batch_id'")
        idx = await reader.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='market_data' AND indexname='idx_indicator_runs_batch'")
        check("A0 indicator_ingest_runs.batch_id (uuid) and partial index idx_indicator_runs_batch exist",
              col == "uuid" and bool(idx) and "batch_id IS NOT NULL" in (idx or ""),
              "every batch assertion below depends on the Part 1 DDL", f"col={col} idx={idx}")

        a_yahoo_parsing()
        a_yahoo_noise_pure()
        await a_yahoo_transport()
        await a_registry_contract()

        async with platform_scope(conn):
            await registry.load_registry(conn, FIXTURES)
        await a_dispatch(conn, reader)
        await a_yahoo_classification(conn, reader)
        await a_bookkeeping_and_diff(conn, reader)
        await a_failure_isolation(conn, reader)
        await a_yahoo_through_orchestrator(conn, reader)
        await a_unregistered(conn, reader)
        await a_overlap(conn, reader, dsn)
        a_staleness()
        captured = await a_entrypoint_and_secrets(conn, reader, dsn)
        await a_no_secret(reader, captured)
        check("A.guard every registry Phase A handed out held only fakes (no real FRED adapter, no real Yahoo transport)",
              REGISTRIES_GUARDED >= 10,
              "Phase A must never run the orchestrator against real sources or real series", f"guarded={REGISTRIES_GUARDED}")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — reported, then teardown still runs
        check("A.x Phase A ran to completion", False, "an unexpected exception aborted the remaining assertions",
              adapters.scrub_error(f"{type(exc).__name__}: {exc}"))
    finally:
        if saved_key is None:
            os.environ.pop("FRED_API_KEY", None)
        else:
            os.environ["FRED_API_KEY"] = saved_key
        expected_order = (order.index(OBS_T) < order.index(RUNS_T) < order.index(SERIES_T))
        deleted = await delete_fixtures(conn, order)
        remaining = await fixture_counts(reader)
        end_real = await real_counts(reader)
        end_untouched = await untouched_counts(reader)
        check("T1 existing tables untouched: portfolio.securities_global, securities_global_prices, public.fx_rates",
              end_untouched == base_untouched,
              "market data must never write security pricing or FX", f"before={base_untouched} after={end_untouched}")
        check("T2 teardown order observations → ingest_runs → series, derived from information_schema",
              expected_order, "a parent deleted first fails on the FK (or, with a cascade added later, deletes more "
              "than intended)", f"order={order} deleted={deleted}")
        check("T3 after teardown, NON-fixture row counts in all three market_data tables are exactly as before",
              end_real == base_real, "proves Phase A wrote and removed only its own rows — never a TRUNCATE",
              f"before={base_real} after={end_real}")
        if any(remaining.values()):
            print(f"[FAIL] T4 fixture rows remain after teardown: {remaining}")
            raise SystemExit(2)
        check("T4 zero fixture rows (series, observations, runs incl. summary rows) remain — re-read independently",
              True, "a leftover fixture would surface in real charts and in Phase B's batch counts")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — live data, after the operator ran market_data_nightly.py TWICE
# ═══════════════════════════════════════════════════════════════════════════
async def phase_b(reader) -> None:
    notfix = f"series_key NOT LIKE '{PREFIX}%'"
    async with platform_scope(reader):
        summaries = await reader.fetch(
            f"SELECT * FROM {RUNS_T} WHERE series_id IS NULL AND run_trigger = 'nightly' AND batch_id IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 2")
        batches = []
        for s in summaries:
            rows = await reader.fetch(
                f"SELECT r.*, s.series_key, s.source_provider FROM {RUNS_T} r "
                f"LEFT JOIN {SERIES_T} s ON s.id = r.series_id WHERE r.batch_id = $1", s["batch_id"])
            batches.append((s, rows))
    expected = await reader.fetchval(
        f"SELECT count(*) FROM {SERIES_T} WHERE {notfix} AND ingest_status = 'active' "
        "AND source_provider = ANY($1::text[])", list(adapters.REAL_PROVIDERS))

    if len(batches) < 2:
        check("B1 the two most recent nightly batches exist", False,
              "Phase B needs the operator to have run market_data_nightly.py twice", f"found={len(batches)}")
        return
    shape = []
    failures = []
    for s, rows in batches:
        summ = [r for r in rows if r["series_id"] is None]
        per = [r for r in rows if r["series_id"] is not None]
        shape.append((str(s["batch_id"])[:8], len(summ), len(per)))
        failures += [f"{r['series_key']}: {r['status']}: {r['error']}" for r in per if r["status"] != "success"]
    check("B1 each of the two latest nightly batches has exactly ONE summary row and one per-series row per active "
          "series with a registered adapter (count read at runtime)",
          all(n_s == 1 and n_p == expected for _, n_s, n_p in shape),
          "a missing per-series row is a series the nightly silently skipped", f"expected={expected} batches={shape}")
    for f in failures:
        print(f"[FIND] nightly per-series row not success — {f}")
    check("B2 every per-series row in both batches is 'success'",
          not failures, "a failure here is a real series that did not refresh (its scrubbed error is printed above)",
          f"{len(failures)} non-success rows")

    latest_rows = batches[0][1]
    latest_per = [r for r in latest_rows if r["series_id"] is not None]
    revised = sum(r["rows_revised"] for r in latest_per)
    too_many = [(r["series_key"], r["rows_inserted"]) for r in latest_per if r["rows_inserted"] > 2]
    check("B3 the second (latest) batch revised nothing and inserted at most 2 rows per series",
          revised == 0 and not too_many,
          "two runs minutes apart cannot legitimately differ more; more means the diff is not idempotent (e.g. noise)",
          f"revised={revised} over_2={too_many[:10]}")

    dups = await reader.fetchval(f"""SELECT count(*) FROM (SELECT series_id, obs_date FROM {OBS_T}
        WHERE valid_to IS NULL AND system_to IS NULL GROUP BY 1, 2 HAVING count(*) > 1) d""")
    check("B4 no (series_id, obs_date) has more than one active row", dups == 0,
          "two active values for one date double-count in every reader", f"duplicates={dups}")

    stats = await reader.fetch(f"""
        SELECT s.series_key, s.source_provider, s.ingest_status, s.frequency, s.last_observation_date, s.last_error,
               (SELECT count(*) FROM {OBS_T} o WHERE o.series_id = s.id
                  AND o.valid_to IS NULL AND o.system_to IS NULL) AS n,
               (SELECT max(obs_date) FROM {OBS_T} o WHERE o.series_id = s.id
                  AND o.valid_to IS NULL AND o.system_to IS NULL) AS maxd
          FROM {SERIES_T} s WHERE {notfix}""")
    active = [r for r in stats if r["ingest_status"] == "active"]
    drift = [(r["series_key"], r["last_observation_date"], r["maxd"]) for r in active
             if r["last_observation_date"] != r["maxd"]]
    check("B5 for every active series last_observation_date = max(obs_date), re-read independently",
          len(active) > 0 and not drift, "the staleness report and the chart both trust last_observation_date",
          f"active={len(active)} drift={drift[:5]}")

    total = await reader.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE valid_to IS NULL AND system_to IS NULL")
    check(f"B6 total active observations >= {MKT01_ACTIVE_BASELINE:,} (the mkt01 baseline)",
          total >= MKT01_ACTIVE_BASELINE,
          "revisions replace rows one for one, so the active count can only stay or grow", f"active={total:,}")

    for r in sorted((r for r in stats if r["source_provider"] == "yahoo"), key=lambda r: r["series_key"]):
        if r["ingest_status"] == "active":
            check(f"B7 Yahoo {r['series_key']} is active with observations and last_observation_date = max(obs_date)",
                  r["n"] > 0 and r["last_observation_date"] == r["maxd"],
                  "an active Yahoo series must meet every guarantee a FRED series does",
                  f"n={r['n']} last={r['last_observation_date']} max={r['maxd']}")
        else:
            find(f"Yahoo {r['series_key']} is '{r['ingest_status']}' (not active): {r['last_error'] or 'no error recorded'}"
                 " — reporting only; the endpoint is unofficial and outside this sprint's control")

    stale = staleness.stale_series(active, datetime.now(timezone.utc).date())
    by_freq: dict[str, int] = {}
    for s in stale:
        by_freq[s.frequency] = by_freq.get(s.frequency, 0) + 1
    print(f"[INFO] stale active series by frequency (first-pass limits): {by_freq or 'none'}")
    for s in stale:
        find(f"stale: {s.series_key} ({s.frequency}) last={s.last_observation_date or '-'} — {s.reason}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="also run Phase B against the real data (after two nightly runs)")
    args = parser.parse_args()

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to the ambient environment")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        check("DB DATABASE_URL present", False, "nothing can be proven without the database")
        print(f"TOTAL: {PASSED} passed, {FAILED} failed")
        return 1
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
        reader = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        check("DB connect", False, "nothing can be proven without the database", type(exc).__name__)
        print(f"TOTAL: {PASSED} passed, {FAILED} failed")
        return 1
    try:
        await phase_a(conn, reader, dsn)
        if args.live:
            await phase_b(reader)
        else:
            skip("Phase B (live nightly batches) was not run — pass --live after running market_data_nightly.py twice")
    finally:
        await conn.close()
        await reader.close()
    print(f"TOTAL: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
