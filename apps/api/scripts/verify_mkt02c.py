#!/usr/bin/env python3
"""verify_mkt02c — long history: S&P 500 splice, credit spread, note
underlyings, Yahoo units.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02c.py
    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02c.py --live

PHASE A (always) is self-contained. Every row it writes belongs to a fixture
series whose series_key starts with 'verify.mkt02c.', or to a batch this script
created. Every Yahoo adapter it builds runs over a FAKE transport and every
orchestrator run gets a fake-only registry and an explicit fixture-only
selection. It never touches a real series and never calls Yahoo or FRED. The
loader-linking cases temporarily link fixture series to real, currently
UNLINKED index securities (portfolio.* is only read); teardown removes them.

PHASE B (--live) checks the real data after the operator has run the steps
printed at the end of the mkt02c sprint (load --seed, validate, backfill,
splice, nightly).

Hydrates its own secrets from Doppler over HTTPS (scripts/_doppler_env.py), the
same helper the earlier verify scripts use. Does NOT chain any other verify.

Output: [PASS] / [FAIL] / [FIND] / [SKIP] lines, then
``TOTAL: <passed> passed, <failed> failed``. Exit 1 on any [FAIL]; exit 2 if
teardown leaves a fixture row behind.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(API_DIR))      # apps/api, for services.*
sys.path.insert(0, str(HERE.parent))  # scripts/, for _doppler_env + the operator scripts

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402
from services.market_data import adapters, ingest, nightly, registry, splice, yahoo  # noqa: E402
from services.market_data import read_service as svc  # noqa: E402
from services.market_data.adapters import FetchResult, FredAdapter, ValidateResult  # noqa: E402
from services.market_data.yahoo import YahooAdapter, YahooResponse  # noqa: E402
import market_data_ingest as mdi  # noqa: E402
import market_data_splice_history as msh  # noqa: E402

PREFIX = "verify.mkt02c."
SERIES_T = "market_data.indicator_series"
OBS_T = "market_data.indicator_observations"
RUNS_T = "market_data.indicator_ingest_runs"
UNTOUCHED = ("portfolio.securities_global", "portfolio.securities_global_prices", "public.fx_rates")
FAKE = "verifyfake02c"  # a source_provider only Phase A's fake registry knows
ADDITIONS_PATH = REPO_ROOT / "docs" / "market_data" / "market_indicator_registry_additions_v2.json"
ADDITION_KEYS = {"fred.baa10y", "yahoo.dji", "yahoo.ftse", "yahoo.stoxx50e", "yahoo.ssmi", "yahoo.axjo"}
EXPECTED_LINKS = {  # series_key → (security id, exact security name), as listed in the sprint's Task 2c
    "yahoo.dji": ("11d8d8dd-d712-4e65-adc3-235c24310548", "Dow Jones Industrial Average"),
    "yahoo.ftse": ("96a1895f-0f6f-40b8-9442-cb61663fa448", "FTSE 100 Index"),
    "yahoo.stoxx50e": ("dc89001d-e250-4abc-9265-b4d2e6c52009", "EURO STOXX 50 Index"),
    "yahoo.ssmi": ("803df7c0-56eb-4ed3-98a6-1649d69dfcc4", "Swiss Market Index"),
    "yahoo.axjo": ("0ccebe34-b939-4179-85f7-08c234a00ed7", "S&P/ASX 200 Index"),
}
# Yahoo series whose instrument is an index (meta.instrumentType 'INDEX').
INDEX_YAHOO_CODES = {"^RUT", "DX-Y.NYB", "^DJI", "^FTSE", "^STOXX50E", "^SSMI", "^AXJO"}
SP500_OWN_BASELINE = 2_514
D = Decimal

PASSED = 0
FAILED = 0
CREATED_BATCHES: list[uuid.UUID] = []
GUARDED = 0


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


def q6(x: Decimal) -> str:
    return format(x.quantize(D("0.000001"), rounding=ROUND_HALF_EVEN), "f")


# ── Fakes ───────────────────────────────────────────────────────────────────
class FakeYahooTransport:
    """Canned responses per request path; records every request."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[str] = []

    async def get(self, path, params):
        self.calls.append(path)
        behaviour = self.routes.get(path)
        if behaviour is None:
            return YahooResponse(500, "")
        return behaviour


class FakeAdapter:
    """In-memory adapter for the nightly-survival run."""

    def __init__(self, provider: str = FAKE):
        self.provider = provider
        self.data: dict[str, list] = {}

    def scrub(self, text):
        return str(text)

    async def aclose(self):
        return None

    async def fetch_series(self, row) -> FetchResult:
        return FetchResult(points=list(self.data[row["source_code"]]))

    async def validate_series(self, row) -> ValidateResult:
        return ValidateResult("active", fields={"units": "Index"})


NOW = datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc)  # after every fixture date: no bar is "today"


def fake_yahoo() -> tuple[YahooAdapter, FakeYahooTransport]:
    global GUARDED
    transport = FakeYahooTransport()
    adapter = YahooAdapter(transport, min_interval=0, sleep=nosleep, max_retries=0, now=lambda: NOW)
    if not isinstance(adapter._transport, FakeYahooTransport):
        raise SystemExit("[FAIL] Phase A built a Yahoo adapter over a REAL transport — aborting")
    GUARDED += 1
    return adapter, transport


def guard_registry(reg: dict) -> dict:
    global GUARDED
    for provider, adapter in reg.items():
        if isinstance(adapter, FredAdapter):
            raise SystemExit(f"[FAIL] Phase A registry for {provider!r} holds a REAL FredAdapter — aborting")
        if isinstance(adapter, YahooAdapter) and not isinstance(adapter._transport, FakeYahooTransport):
            raise SystemExit("[FAIL] Phase A registry holds a Yahoo adapter over a REAL transport — aborting")
    GUARDED += 1
    return reg


def fixture_only(keys: list[str]) -> list[str]:
    if not keys or any(not k.startswith(PREFIX) for k in keys):
        raise SystemExit(f"[FAIL] Phase A tried to touch a non-fixture series: {keys}")
    return keys


def ts(d: date) -> int:
    # 14:30 UTC = 09:30 in New York (gmtoffset -18000): the bar's local date is d.
    return int(datetime(d.year, d.month, d.day, 14, 30, tzinfo=timezone.utc).timestamp())


def chart_json(points: list[tuple[date, Decimal]], *, currency: str | None = "USD",
               instrument_type: str | None = None) -> str:
    """Chart payload TEXT, numbers spelled exactly as given."""
    meta = {"exchangeTimezoneName": "America/New_York", "gmtoffset": -18000, "dataGranularity": "1d"}
    if currency is not None:
        meta["currency"] = currency
    if instrument_type is not None:
        meta["instrumentType"] = instrument_type
    stamps = ",".join(str(ts(d)) for d, _ in points)
    closes = ",".join(str(v) for _, v in points)
    return ('{"chart":{"result":[{"meta":%s,"timestamp":[%s],"indicators":{"quote":[{"close":[%s]}]}}],'
            '"error":null}}' % (json.dumps(meta), stamps, closes))


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ── Fixture data ────────────────────────────────────────────────────────────
F0 = date(2024, 2, 1)
PRE_DATES = weekdays(date(2024, 1, 2), date(2024, 1, 31))     # strictly before F0
OWN_DATES = weekdays(F0, date(2024, 3, 29))                   # the series' own (NULL-provider) rows
OWN = [(d, D("4000.00") + i) for i, d in enumerate(OWN_DATES)]
PRE = [(d, D("3900.1234") + i) for i, d in enumerate(PRE_DATES)]
OVERLAP_OK = [(d, v + D("0.0049")) for d, v in OWN]           # every day within 0.02
# Gate fixture (mkt02c2): a ~262-day overlap, so ONE or TWO days beyond 0.02
# stay inside rule 2 (>= 99.0%) and rule 3 (0.5% of the series value) is
# proven on its own. With the 42-day OWN above, a single bad day already
# fails rule 2 and would prove nothing about rule 3. Ends before NOW.
GATE_F0 = date(2023, 6, 1)
GATE_PRE_DATES = weekdays(date(2023, 5, 1), date(2023, 5, 31))
GATE_OWN_DATES = weekdays(GATE_F0, date(2024, 5, 31))
GATE_OWN = [(d, D("4000.00") + i) for i, d in enumerate(GATE_OWN_DATES)]
GATE_PRE = [(d, D("3800.1234") + i) for i, d in enumerate(GATE_PRE_DATES)]
GATE_OK = [(d, v + D("0.0049")) for d, v in GATE_OWN]
SYMBOL = "VMKT02CSPX"
SYMBOL_PATH = yahoo.chart_path(SYMBOL)


def fixture_row(suffix, provider, code, status="active"):
    return {
        "series_key": PREFIX + suffix, "name": f"verify mkt02c {suffix}",
        "category": "Verify mkt02c", "region": "US", "frequency": "daily",
        "best_view": None, "default_transform": "level", "cost_tier": "free",
        "cost_note": None, "license_class": "public_domain", "source_provider": provider,
        "source_code": code, "source_url": None, "notes": "mkt02c verify fixture",
        "sort_order": -3000, "ingest_status": status,
    }


_SPECS = [
    ("splice_main", FAKE, "VMKT02CMAIN", "active"),
    ("splice_gate", FAKE, "VMKT02CGATE", "active"),
    ("splice_dry", FAKE, "VMKT02CDRY", "active"),
    ("prov", FAKE, "VMKT02CPROV", "paused"),
    ("link_ok", FAKE, "VMKT02CLOK", "paused"),
    ("link_badname", FAKE, "VMKT02CLBAD", "paused"),
    ("link_already", FAKE, "VMKT02CLALR", "paused"),
    ("link_dup", FAKE, "VMKT02CLDUP", "paused"),
    ("units_idx", "yahoo", "VMKT02CUIDX", "deferred"),
    ("units_etf", "yahoo", "VMKT02CUETF", "deferred"),
    ("units_none", "yahoo", "VMKT02CUNONE", "deferred"),
]
FIXTURES = [fixture_row(*s) for s in _SPECS]
K = {r["series_key"][len(PREFIX):]: r["series_key"] for r in FIXTURES}

FIXTURE_IDS = f"(SELECT id FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%')"
FIXTURE_RUNS = (f"(series_id IN {FIXTURE_IDS} OR batch_id IN (SELECT batch_id FROM {RUNS_T} "
                f"WHERE series_id IN {FIXTURE_IDS} AND batch_id IS NOT NULL) OR batch_id = ANY($1::uuid[]))")


# ── Reading helpers (independent `reader` connection) ───────────────────────
async def sid_of(reader, key):
    return await reader.fetchval(f"SELECT id FROM {SERIES_T} WHERE series_key = $1", key)


async def all_rows(reader, key) -> list[dict]:
    rows = await reader.fetch(
        f"SELECT o.id, o.obs_date, o.value, o.source_provider, o.valid_to, o.system_to FROM {OBS_T} o "
        f"JOIN {SERIES_T} s ON s.id = o.series_id WHERE s.series_key = $1 ORDER BY o.obs_date, o.valid_from", key)
    return [dict(r) for r in rows]


def active_of(rows: list[dict]) -> dict[date, dict]:
    return {r["obs_date"]: r for r in rows if r["valid_to"] is None and r["system_to"] is None}


async def run_rows(reader, key) -> list:
    async with platform_scope(reader):
        return await reader.fetch(
            f"SELECT r.* FROM {RUNS_T} r JOIN {SERIES_T} s ON s.id = r.series_id WHERE s.series_key = $1 "
            "ORDER BY r.started_at", key)


async def notes_of(reader, key) -> str:
    return await reader.fetchval(f"SELECT notes FROM {SERIES_T} WHERE series_key = $1", key) or ""


async def seed_own_rows(conn, key, points) -> None:
    sid = await sid_of(conn, key)
    async with platform_scope(conn):
        await conn.execute(
            f"""INSERT INTO {OBS_T} (series_id, obs_date, value, valid_from)
                SELECT $1::uuid, t.d, t.v, now() FROM unnest($2::date[], $3::numeric[]) AS t(d, v)""",
            sid, [d for d, _ in points], [v for _, v in points])


async def do_splice(conn, key, payload_points, *, dry_run=False) -> splice.SpliceResult:
    adapter, transport = fake_yahoo()
    transport.routes[SYMBOL_PATH] = YahooResponse(200, chart_json(payload_points))
    return await splice.splice_history(conn, adapter, fixture_only([key])[0], SYMBOL, dry_run=dry_run)


async def run_script(conn, key, payload_points, *, dry_run=False) -> tuple[int, str]:
    """The operator script's own run(): exit code + its output."""
    adapter, transport = fake_yahoo()
    transport.routes[SYMBOL_PATH] = YahooResponse(200, chart_json(payload_points))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = await msh.run(conn, adapter, fixture_only([key])[0], SYMBOL, dry_run)
    return code, buf.getvalue()


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
    # Observations, then runs, then series — the order the sprint names, and a
    # valid topological order of the graph above.
    if OBS_T in order and RUNS_T in order and order.index(OBS_T) > order.index(RUNS_T):
        order.remove(OBS_T)
        order.insert(order.index(RUNS_T), OBS_T)
    return order


async def delete_fixtures(conn, order) -> dict[str, int]:
    """By fixture tag only — never a TRUNCATE. Runs go by fixture series_id AND
    by any batch_id this script created (summary rows have series_id NULL)."""
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
# PHASE A — splice
# ═══════════════════════════════════════════════════════════════════════════
async def a_splice_main(conn, reader) -> None:
    key = K["splice_main"]
    await seed_own_rows(conn, key, OWN)
    own_before = [r for r in await all_rows(reader, key) if r["source_provider"] is None]

    # S1 — insert
    res = await do_splice(conn, key, PRE + OVERLAP_OK)
    rows = await all_rows(reader, key)
    act = active_of(rows)
    pre_written = {d: r for d, r in act.items() if d < F0}
    on_after = [r for r in rows if r["obs_date"] >= F0]
    runs = await run_rows(reader, key)
    notes = await notes_of(reader, key)
    sentence = splice.notes_sentence(F0, SYMBOL)
    check("S1 splice inserts exactly the fetched dates strictly before F0, all source_provider 'yahoo'; nothing on or "
          "after F0 is written; one 'manual' run row with the counts; the provenance sentence is appended",
          res.status == "ok" and res.f0 == F0 and res.inserted == len(PRE) and res.revised == 0
          and set(pre_written) == set(PRE_DATES)
          and all(r["source_provider"] == "yahoo" and r["value"] == dict(PRE)[d] for d, r in pre_written.items())
          and len(on_after) == len(OWN) and all(r["source_provider"] is None for r in on_after)
          and len(runs) == 1 and runs[0]["run_trigger"] == "manual" and runs[0]["status"] == "success"
          and runs[0]["rows_inserted"] == len(PRE) and notes.count(sentence) == 1
          and res.earliest == PRE_DATES[0],
          "the splice must add only the older history, tagged per row, and never write into FRED's own range",
          f"status={res.status} f0={res.f0} ins={res.inserted} pre={len(pre_written)}/{len(PRE)} "
          f"after={len(on_after)}/{len(OWN)} runs={[(r['run_trigger'], r['rows_inserted']) for r in runs]} "
          f"earliest={res.earliest}")

    # S4 — idempotency, revision of a Yahoo row, NULL-provider rows untouchable
    count_before = len(rows)
    res2 = await do_splice(conn, key, PRE + OVERLAP_OK)
    count2 = len(await all_rows(reader, key))
    changed_pre = PRE_DATES[5]
    changed_own = OWN_DATES[3]
    payload3 = [(d, v + D("1.0000") if d == changed_pre else v) for d, v in PRE] + \
               [(d, v + D("0.0150") if d == changed_own else v) for d, v in OVERLAP_OK]
    own_row_before = active_of(await all_rows(reader, key))[changed_own]
    res3 = await do_splice(conn, key, payload3)
    rows3 = await all_rows(reader, key)
    hist = [r for r in rows3 if r["obs_date"] == changed_pre]
    hist_active = [r for r in hist if r["valid_to"] is None and r["system_to"] is None]
    own_row_after = active_of(rows3)[changed_own]
    own_hist = [r for r in rows3 if r["obs_date"] == changed_own]
    notes3 = await notes_of(reader, key)
    runs3 = await run_rows(reader, key)
    check("S4 a second identical run writes nothing; a changed Yahoo value for a Yahoo-sourced date revises exactly that "
          "row (old closed, one active, two rows of history, both 'yahoo'); a changed value on a NULL-provider date is "
          "never touched; the notes sentence stays single",
          res2.status == "ok" and (res2.inserted, res2.revised) == (0, 0) and res2.unchanged == len(PRE)
          and count2 == count_before
          and res3.status == "ok" and (res3.inserted, res3.revised) == (0, 1)
          and len(hist) == 2 and len(hist_active) == 1
          and hist_active[0]["value"] == dict(PRE)[changed_pre] + D("1.0000")
          and all(r["source_provider"] == "yahoo" for r in hist)
          and own_row_after["id"] == own_row_before["id"] and own_row_after["value"] == own_row_before["value"]
          and own_row_after["source_provider"] is None and len(own_hist) == 1
          and notes3.count(splice.notes_sentence(F0, SYMBOL)) == 1 and len(runs3) == 3,
          "re-running must be a no-op, and a revision may only ever replace a row this script owns",
          f"run2={(res2.inserted, res2.revised, res2.unchanged)} counts={count_before}->{count2} "
          f"run3={(res3.inserted, res3.revised)} hist={[(r['value'], r['valid_to'] is None) for r in hist]} "
          f"own={own_row_before['value']}->{own_row_after['value']} runs={len(runs3)}")

    # S2 — FRED (NULL-provider) rows untouched across every run above
    own_after = [r for r in await all_rows(reader, key) if r["source_provider"] is None]
    sig = lambda rs: sorted((str(r["id"]), r["obs_date"], r["value"], r["valid_to"], r["system_to"]) for r in rs)  # noqa: E731
    check("S2 every NULL-provider (own-source) row has the same id, value, provider and active state before and after "
          "three splice runs",
          sig(own_before) == sig(own_after) and len(own_after) == len(OWN),
          "FRED stays the source of record: the splice may never revise, close or delete one of its rows",
          f"before={len(own_before)} after={len(own_after)}")

    # S6 — nightly survival
    spliced_before = sig([r for r in active_of(await all_rows(reader, key)).values() if r["source_provider"] == "yahoo"])
    fake = FakeAdapter()
    new_day = date(2024, 4, 1)
    fake.data["VMKT02CMAIN"] = OWN[-5:] + [(new_day, D("4100.00"))]  # only RECENT points, like FRED's window
    result = await nightly.run_nightly(conn, guard_registry({FAKE: fake}), fixture_only([key]), "nightly")
    CREATED_BATCHES.append(result.batch_id)
    act6 = active_of(await all_rows(reader, key))
    spliced_after = sig([r for r in act6.values() if r["source_provider"] == "yahoo"])
    per = result.series[0] if result.series else None
    check("S6 the production nightly orchestrator, fed only recent points, leaves every pre-F0 Yahoo row active and "
          "unchanged; zero revisions; the batch succeeds",
          result.status == "success" and per is not None and per.revised == 0 and per.inserted == 1
          and spliced_before == spliced_after and len(spliced_after) == len(PRE) and new_day in act6,
          "this is the proof that the nightly refresh does not erase the splice (it never closes a date absent "
          "from a fetch)",
          f"status={result.status} per={per} spliced={len(spliced_before)}->{len(spliced_after)}")

    # S8 — downstream read
    q = svc.SeriesQuery([key], None, None, "native")
    body = await svc.read_series(reader, q)
    series = body.get("series", [])
    pts = series[0]["points"] if series else []
    dates = [p[0] for p in pts]
    n_active = len(act6)
    check("S8 the mkt03 series read function returns the spliced fixture as ONE ordered series whose first point is "
          "the earliest spliced date",
          len(series) == 1 and dates == sorted(set(dates)) and len(pts) == n_active
          and dates[0] == PRE_DATES[0].isoformat() and pts[0][1] == format(PRE[0][1], "f")
          and series[0]["first_observation_date"] == PRE_DATES[0].isoformat(),
          "consumers must see one continuous line, not two series or a gap at F0",
          f"series={len(series)} points={len(pts)} active={n_active} first={pts[0] if pts else None}")


async def a_gate(conn, reader) -> None:
    key = K["splice_gate"]
    await seed_own_rows(conn, key, GATE_OWN)

    async def state():
        return (len(await all_rows(reader, key)), len(await run_rows(reader, key)), await notes_of(reader, key))

    base = await state()
    # > 1% of overlap days off by more than 0.02 (5 of ~262 days, each 0.05 — far inside 0.5%)
    bad_idx = {4, 54, 104, 154, 204}
    bad_share = [(d, v + D("0.05") if i in bad_idx else v) for i, (d, v) in enumerate(GATE_OWN)]
    code_a, out_a = await run_script(conn, key, GATE_PRE + bad_share)
    after_a = await state()
    # exactly one day off by more than 0.5% of the series value (25.00 on ~4007 ≈ 0.62%); every
    # other day exact, so rule 2 holds (1 of ~262 beyond 0.02) and only rule 3 can refuse
    bad_day = GATE_OWN_DATES[7]
    bad_one = [(d, v + D("25.00") if i == 7 else v) for i, (d, v) in enumerate(GATE_OWN)]
    code_b, out_b = await run_script(conn, key, GATE_PRE + bad_one)
    after_b = await state()
    gate_line_b = next((ln for ln in out_b.splitlines() if ln.startswith("OVERLAP GATE FAILED")), "")
    # no overlap at all — must not pass vacuously
    code_d, out_d = await run_script(conn, key, GATE_PRE)
    after_d = await state()
    check("S3a overlap gate REFUSES when more than 1% of overlap days differ by more than 0.02 (rule 2): exit 1, "
          "zero writes, worst days printed as [FIND]",
          code_a == 1 and after_a == base and "OVERLAP GATE FAILED" in out_a and "rule 2" in out_a
          and "rule 3" not in out_a.split("OVERLAP GATE FAILED")[-1] and out_a.count("[FIND]") == len(bad_idx),
          "a differently-scaled or different index must never be spliced on",
          f"exit={code_a} state={base}->{after_a}")
    check("S3b overlap gate REFUSES when one overlap day differs by more than 0.5% of the series value (rule 3), "
          "although rule 2 holds: exit 1, zero writes, the offending day named",
          code_b == 1 and after_b == base and "rule 3" in gate_line_b and "rule 2" not in gate_line_b
          and bad_day.isoformat() in gate_line_b and "25.00" in gate_line_b,
          "one catastrophic break is enough to show the sources disagree, even when every other day agrees",
          f"exit={code_b} state={base}->{after_b} gate={gate_line_b!r}")
    check(f"S3d overlap gate REFUSES with no overlap at all (needs >= {splice.MIN_OVERLAP_DAYS} days): exit 1, zero writes",
          code_d == 1 and after_d == base and "overlapping day" in out_d and "rule 1" in out_d,
          "zero comparisons would otherwise pass the percentage test vacuously", f"exit={code_d}")
    code_c, out_c = await run_script(conn, key, GATE_PRE + GATE_OK)
    act = active_of(await all_rows(reader, key))
    check("S3c the SAME fixture with a payload within tolerance passes: exit 0, every pre-F0 date inserted",
          code_c == 0 and len([d for d in act if d < GATE_F0]) == len(GATE_PRE) and "100.00%" in out_c
          and "OVERLAP GATE PASSED" in out_c and "[FIND]" not in out_c,
          "the gate must let the genuine series through, or it is just a wall", f"exit={code_c}")


async def a_dry_run(conn, reader) -> None:
    key = K["splice_dry"]
    await seed_own_rows(conn, key, OWN)
    before = (len(await all_rows(reader, key)), len(await run_rows(reader, key)), await notes_of(reader, key))
    code, out = await run_script(conn, key, PRE + OVERLAP_OK, dry_run=True)
    after = (len(await all_rows(reader, key)), len(await run_rows(reader, key)), await notes_of(reader, key))
    check("S5 a dry run runs the gate and reports the counts (candidates, overlap days, planned inserts) but writes "
          "nothing — row counts, run rows and notes identical",
          code == 0 and before == after and "DRY RUN — nothing written" in out
          and f"candidate pre-F0 points: {len(PRE)}" in out and f"overlap days compared: {len(OWN)}" in out
          and f"rows inserted {len(PRE)}" in out,
          "the operator's --dry-run must be a faithful preview with zero side effects",
          f"exit={code} before={before[:2]} after={after[:2]}")


async def a_provenance(conn, reader) -> None:
    sid = await sid_of(reader, K["prov"])
    results = {}
    for label, provider, d in (("bogus", "bogus", date(2024, 1, 2)), ("null", None, date(2024, 1, 3)),
                               ("yahoo", "yahoo", date(2024, 1, 4))):
        try:
            async with platform_scope(conn):
                await conn.execute(f"INSERT INTO {OBS_T} (series_id, obs_date, value, source_provider) "
                                   "VALUES ($1, $2, 1, $3)", sid, d, provider)
            results[label] = "accepted"
        except asyncpg.CheckViolationError:
            results[label] = "check_violation"
        except Exception as exc:  # noqa: BLE001
            results[label] = type(exc).__name__
    stored = {r["source_provider"] for r in await all_rows(reader, K["prov"])}
    check("S7 observations.source_provider CHECK: 'bogus' is rejected; NULL and 'yahoo' are accepted (re-read)",
          results == {"bogus": "check_violation", "null": "accepted", "yahoo": "accepted"} and stored == {None, "yahoo"},
          "row-level provenance is only trustworthy if the database refuses an unknown provider",
          f"{results} stored={stored}")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A — loader
# ═══════════════════════════════════════════════════════════════════════════
async def a_loader_seed(conn, reader) -> None:
    args = mdi.build_parser().parse_args(["load"])
    default_path = mdi.resolve_seed_path(args.seed)
    check("L1 `load` with no --seed still reads the v1 seed",
          args.seed is None and default_path == registry.SEED_PATH
          and registry.SEED_PATH.name == "market_indicator_registry_v1.json"
          and mdi.resolve_seed_path("docs/market_data/market_indicator_registry_additions_v2.json").resolve()
          == ADDITIONS_PATH.resolve(),
          "existing behavior and verify_mkt01's v1 structural checks depend on the default",
          f"seed={args.seed} default={default_path}")

    doc = json.loads(ADDITIONS_PATH.read_text(encoding="utf-8"))
    rows = doc.get("series", [])
    keys = [r["series_key"] for r in rows]
    v1_keys = {r["series_key"] for r in registry.load_seed_file()}
    links = {r["series_key"]: (r.get("security_global_id"), r.get("security_name")) for r in rows
             if r["source_provider"] == "yahoo"}
    live = {str(r["id"]): (r["name"], r["security_type"], r["live"]) for r in await reader.fetch(
        "SELECT id, name, security_type, (valid_to IS NULL AND system_to IS NULL AND merged_into_id IS NULL) AS live "
        "FROM portfolio.securities_global WHERE id = ANY($1::uuid[])",
        [uuid.UUID(v[0]) for v in EXPECTED_LINKS.values()])}
    live_ok = all(live.get(i) == (n, "index", True) for i, n in EXPECTED_LINKS.values())
    check("L2 the additions file: version 2, six unique keys absent from v1; the five Yahoo rows carry exactly the "
          "listed security ids and names, each a live 'index' security of that exact name",
          doc.get("version") == 2 and len(keys) == 6 and set(keys) == ADDITION_KEYS and not (set(keys) & v1_keys)
          and links == EXPECTED_LINKS and live_ok,
          "a wrong id or name would link a note underlying to the wrong price line",
          f"keys={keys} overlap_v1={set(keys) & v1_keys} live={live}")

    # Load the file's rows under fixture keys (link fields dropped, so no real security changes hands).
    fixture_rows = []
    for r in rows:
        fr = {k: v for k, v in r.items() if k not in registry.LINK_FIELDS}
        fr["series_key"] = PREFIX + "add." + r["series_key"]
        fixture_rows.append(fr)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"version": 2, "series": fixture_rows}, fh)
        tmp = fh.name
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r1 = await mdi.run_load(conn, None, tmp)
        stamps1 = {r["series_key"]: r["updated_at"] for r in await reader.fetch(
            f"SELECT series_key, updated_at FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}add.%'")}
        with contextlib.redirect_stdout(io.StringIO()):
            r2 = await mdi.run_load(conn, None, tmp)
        stored = {r["series_key"]: dict(r) for r in await reader.fetch(
            f"SELECT * FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}add.%'")}
    finally:
        os.unlink(tmp)
    same = all(all(stored.get(fr["series_key"], {}).get(f) == fr[f] for f in (*registry.DEFINITION_FIELDS, "ingest_status"))
               for fr in fixture_rows)
    stamps2 = {k: v["updated_at"] for k, v in stored.items()}
    check("L3 every additions value passes the live CHECK constraints (all six rows insert and read back identical); "
          "a second load changes nothing (0 inserted, 0 updated, updated_at untouched)",
          r1 is not None and (r1.inserted, r1.updated) == (6, 0) and same
          and r2 is not None and (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 6) and stamps1 == stamps2,
          "the operator's `load --seed` must succeed once and be a no-op after",
          f"r1={r1} r2={r2} same={same}")


async def a_loader_links(conn, reader) -> None:
    secs = await reader.fetch(
        """SELECT g.id, g.name FROM portfolio.securities_global g
            WHERE g.security_type = 'index' AND g.valid_to IS NULL AND g.system_to IS NULL
              AND g.merged_into_id IS NULL AND g.id <> ALL($1::uuid[])
              AND NOT EXISTS (SELECT 1 FROM market_data.indicator_series s WHERE s.security_global_id = g.id)
            ORDER BY g.name, g.id LIMIT 2""",
        [uuid.UUID(v[0]) for v in EXPECTED_LINKS.values()])
    if len(secs) < 2:
        check("LK linking cases need two live, unlinked index securities", False,
              "the four linking cases cannot be proven without them", f"found={len(secs)}")
        return
    (p_id, p_name), (q_id, q_name) = (secs[0]["id"], secs[0]["name"]), (secs[1]["id"], secs[1]["name"])
    already_sid = await sid_of(reader, K["link_already"])
    async with platform_scope(conn):
        await conn.execute(f"UPDATE {SERIES_T} SET security_global_id = $2 WHERE id = $1", already_sid, q_id)

    def row(suffix, sec_id, name):
        r = fixture_row(suffix, FAKE, _code[suffix], "paused")
        r["security_global_id"] = str(sec_id)
        r["security_name"] = name
        return r
    _code = {s: c for s, _, c, _ in _SPECS}
    seed = [row("link_ok", p_id, p_name), row("link_badname", q_id, "Wrong name for verify"),
            row("link_already", p_id, p_name), row("link_dup", p_id, p_name)]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"version": 2, "series": seed}, fh)
        tmp = fh.name
    buf = io.StringIO()
    crashed = None
    try:
        with contextlib.redirect_stdout(buf):
            res = await mdi.run_load(conn, None, tmp)
    except Exception as exc:  # noqa: BLE001
        res, crashed = None, f"{type(exc).__name__}: {exc}"
    finally:
        os.unlink(tmp)
    out = buf.getvalue()
    links = {r["series_key"]: r["security_global_id"] for r in await reader.fetch(
        f"SELECT series_key, security_global_id FROM {SERIES_T} WHERE series_key = ANY($1::text[])",
        [K["link_ok"], K["link_badname"], K["link_already"], K["link_dup"]])}
    finds = [ln for ln in out.splitlines() if ln.startswith("[FIND]")]

    def find_for(suffix):
        return next((f for f in finds if K[suffix] + ":" in f), "")
    check("LK1 a matching id + exact name + type 'index' links the series (re-read)",
          crashed is None and links.get(K["link_ok"]) == p_id and not find_for("link_ok"),
          "the happy path is how the five note underlyings get their price line", f"link={links.get(K['link_ok'])}")
    check("LK2 a wrong security name does NOT link and prints a [FIND] naming the series",
          links.get(K["link_badname"]) is None and "named" in find_for("link_badname"),
          "a name mismatch means the id may be wrong; linking anyway would mislabel a price line",
          f"link={links.get(K['link_badname'])} find={find_for('link_badname')!r}")
    check("LK3 an already-linked series is NOT overwritten (still points at its original security) and a [FIND] says so",
          links.get(K["link_already"]) == q_id and "never overwritten" in find_for("link_already"),
          "an existing link may have been set deliberately; the loader must never silently move it",
          f"link={links.get(K['link_already'])} find={find_for('link_already')!r}")
    check("LK4 a link that would violate uq_indicator_series_security is skipped with a [FIND], not a crash",
          crashed is None and res is not None and links.get(K["link_dup"]) is None
          and "uq_indicator_series_security" in find_for("link_dup"),
          "one bad seed row must not abort the whole load", f"crashed={crashed} find={find_for('link_dup')!r}")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A — Yahoo units
# ═══════════════════════════════════════════════════════════════════════════
async def a_units(conn, reader) -> None:
    adapter, transport = fake_yahoo()
    pts = [(date(2024, 3, 4), D("5"))]
    transport.routes[yahoo.chart_path("VMKT02CUIDX")] = YahooResponse(200, chart_json(pts, instrument_type="INDEX"))
    transport.routes[yahoo.chart_path("VMKT02CUETF")] = YahooResponse(200, chart_json(pts, instrument_type="ETF"))
    transport.routes[yahoo.chart_path("VMKT02CUNONE")] = YahooResponse(200, chart_json(pts))
    keys = fixture_only([K["units_idx"], K["units_etf"], K["units_none"]])
    with contextlib.redirect_stdout(io.StringIO()):
        await mdi.run_validate(conn, adapter, keys, "yahoo")
    units = {r["series_key"]: (r["units"], r["ingest_status"]) for r in await reader.fetch(
        f"SELECT series_key, units, ingest_status FROM {SERIES_T} WHERE series_key = ANY($1::text[])", keys)}
    check("U1 Yahoo validate stores units 'index points' for instrumentType INDEX, the currency code for ETF and for a "
          "missing instrumentType (re-read from the registry)",
          units == {K["units_idx"]: ("index points", "active"), K["units_etf"]: ("USD", "active"),
                    K["units_none"]: ("USD", "active")},
          "an index level is not an amount of dollars; everything else keeps today's behavior", str(units))


async def phase_a(conn, reader) -> None:
    order = await fk_delete_order(reader)
    leftover = await delete_fixtures(conn, order)
    if any(leftover.values()):
        find(f"removed fixture rows left by an earlier interrupted run: {leftover}")
    base_untouched = await untouched_counts(reader)
    base_real = await real_counts(reader)
    try:
        col = await reader.fetchval(
            "SELECT data_type FROM information_schema.columns WHERE table_schema='market_data' "
            "AND table_name='indicator_observations' AND column_name='source_provider'")
        check("A0 indicator_observations.source_provider (text) exists — the Part 1 migration is applied",
              col == "text", "every provenance assertion below depends on it", f"data_type={col}")
        async with platform_scope(conn):
            await registry.load_registry(conn, FIXTURES)
        await a_splice_main(conn, reader)
        await a_gate(conn, reader)
        await a_dry_run(conn, reader)
        await a_provenance(conn, reader)
        await a_loader_seed(conn, reader)
        await a_loader_links(conn, reader)
        await a_units(conn, reader)
        check("A.guard every Yahoo adapter and registry Phase A built used fakes only",
              GUARDED >= 8, "Phase A must never call Yahoo or FRED, or touch a real series", f"guarded={GUARDED}")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — reported, then teardown still runs
        check("A.x Phase A ran to completion", False, "an unexpected exception aborted the remaining assertions",
              adapters.scrub_error(f"{type(exc).__name__}: {exc}"))
    finally:
        expected_order = order.index(OBS_T) < order.index(RUNS_T) < order.index(SERIES_T)
        deleted = await delete_fixtures(conn, order)
        remaining = await fixture_counts(reader)
        end_real = await real_counts(reader)
        end_untouched = await untouched_counts(reader)
        check("T1 existing tables untouched: portfolio.securities_global, securities_global_prices, public.fx_rates",
              end_untouched == base_untouched,
              "this sprint links series to securities by writing market_data only — portfolio.* is read-only here",
              f"before={base_untouched} after={end_untouched}")
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
              True, "a leftover fixture would surface in real charts, and a leftover link would block a real one")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — live data
# ═══════════════════════════════════════════════════════════════════════════
async def _series(reader, key):
    return await reader.fetchrow(f"SELECT * FROM {SERIES_T} WHERE series_key = $1", key)


async def _longest_gap(reader, sid, since: date):
    return await reader.fetchrow(
        f"""SELECT obs_date, prev, obs_date - prev AS gap FROM (
               SELECT obs_date, lag(obs_date) OVER (ORDER BY obs_date) AS prev FROM {OBS_T}
                WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL AND obs_date >= $2) x
             WHERE prev IS NOT NULL ORDER BY gap DESC, obs_date LIMIT 1""", sid, since)


async def phase_b(reader) -> None:
    sp = await _series(reader, "fred.sp500")
    if sp is None:
        check("B0 fred.sp500 exists", False, "every S&P assertion needs it")
        return
    act = "series_id = $1 AND valid_to IS NULL AND system_to IS NULL"
    first = await reader.fetchval(f"SELECT min(obs_date) FROM {OBS_T} WHERE {act}", sp["id"])
    own_first = await reader.fetchval(f"SELECT min(obs_date) FROM {OBS_T} WHERE {act} AND source_provider IS NULL",
                                      sp["id"])
    check("B1a fred.sp500's earliest active observation is before 1990-01-01",
          first is not None and first < date(1990, 1, 1),
          "the key-dates chart needs S&P history back past 2000; FRED alone starts in 2016", f"earliest={first}")
    gap = await _longest_gap(reader, sp["id"], date(1960, 1, 1))
    gap_days = gap["gap"] if gap else None
    find(f"fred.sp500 longest gap since 1960: {gap_days} days ({gap['prev'] if gap else '-'} → "
         f"{gap['obs_date'] if gap else '-'})")
    check("B1b fred.sp500 has no gap longer than 10 days after 1960-01-01",
          gap_days is not None and gap_days <= 10,
          "a hole at the splice seam or in Yahoo's history would distort every return across it", f"longest={gap_days}")
    split = await reader.fetchrow(
        f"""SELECT count(*) FILTER (WHERE obs_date < $2 AND source_provider = 'yahoo') AS pre_yahoo,
                   count(*) FILTER (WHERE obs_date < $2 AND source_provider IS DISTINCT FROM 'yahoo') AS pre_other,
                   count(*) FILTER (WHERE obs_date >= $2 AND source_provider IS NOT NULL) AS post_tagged,
                   count(*) FILTER (WHERE source_provider IS NULL) AS own
              FROM {OBS_T} WHERE {act}""", sp["id"], own_first)
    check("B1c every fred.sp500 row before its earliest NULL-provider row is 'yahoo' and none on or after it is tagged",
          own_first is not None and split["pre_yahoo"] > 0 and split["pre_other"] == 0 and split["post_tagged"] == 0,
          "provenance must be exact per row so mkt04 can hide Yahoo-sourced rows", f"own_first={own_first} {dict(split)}")
    check(f"B1d fred.sp500 still has >= {SP500_OWN_BASELINE:,} NULL-provider (FRED) rows",
          split["own"] >= SP500_OWN_BASELINE, "the splice must never have removed a FRED row", f"own={split['own']}")

    anchor = date(2000, 3, 10)
    asof = await reader.fetchrow(
        f"SELECT obs_date, value FROM {OBS_T} WHERE {act} AND obs_date <= $2 ORDER BY obs_date DESC LIMIT 1",
        sp["id"], anchor)
    latest = await reader.fetchrow(f"SELECT obs_date, value FROM {OBS_T} WHERE {act} ORDER BY obs_date DESC LIMIT 1",
                                   sp["id"])
    try:
        g1 = await svc.build_grid(reader, svc.GridRequest(["fred.sp500"], anchor, anchor, "index", "monthly"))
        g2 = await svc.build_grid(reader, svc.GridRequest(["fred.sp500"], anchor, latest["obs_date"], "index", "monthly"))
        m1 = g1["series"][0]
        at_anchor = g1["rows"][0]["cells"]["fred.sp500"]
        at_latest = g2["rows"][0]["cells"]["fred.sp500"]
        exp = q6(100 * latest["value"] / asof["value"]) if asof else None
        ok = (asof is not None and m1["floating"] is False and m1["anchor_observation_date"] == asof["obs_date"].isoformat()
              and at_anchor == "100.000000" and at_latest == exp)
        detail = f"meta={m1} at_anchor={at_anchor} at_latest={at_latest} sql={exp}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, adapters.scrub_error(f"{type(exc).__name__}: {exc}")
    check("B2 production grid: fred.sp500 indexed from 2000-03-10 does NOT float, is exactly 100 at the anchor, and its "
          "latest value equals 100 * latest / anchor computed in SQL",
          ok, "the reason for the splice: the 2000 anchor now lands on real history", detail)

    baa = await _series(reader, "fred.baa10y")
    if baa is not None and baa["ingest_status"] == "active":
        st = await reader.fetchrow(f"SELECT min(obs_date) AS first, count(*) AS n FROM {OBS_T} WHERE {act}", baa["id"])
        check("B3 fred.baa10y is active with first observation before 1990-01-01 and more than 8,000 rows",
              st["first"] is not None and st["first"] < date(1990, 1, 1) and st["n"] > 8000,
              "the long-history credit spread exists so stress before Oct 2023 is visible", f"{dict(st)}")
    else:
        find(f"fred.baa10y is {baa['ingest_status'] if baa else 'absent'}: "
             f"{(baa['last_error'] if baa else None) or 'no error recorded'} — reporting only")

    for key, (sec_id, _name) in EXPECTED_LINKS.items():
        s = await _series(reader, key)
        if s is None or s["ingest_status"] != "active":
            find(f"{key} is {s['ingest_status'] if s else 'absent'}: {(s['last_error'] if s else None) or 'no error'}"
                 " — reporting only; the endpoint is unofficial")
            if s is not None:
                check(f"B4 {key} is linked to its security even while not active",
                      str(s["security_global_id"]) == sec_id, "the link comes from the load, not from Yahoo",
                      f"link={s['security_global_id']}")
            continue
        st = await reader.fetchrow(
            f"""SELECT count(*) AS n, max(obs_date) AS maxd,
                       (SELECT count(*) FROM (SELECT obs_date FROM {OBS_T} WHERE {act}
                          GROUP BY obs_date HAVING count(*) > 1) d) AS dups
                  FROM {OBS_T} WHERE {act}""", s["id"])
        g = await _longest_gap(reader, s["id"], date(1900, 1, 1))
        find(f"{key} longest gap: {g['gap'] if g else '-'} days ({g['prev'] if g else '-'} → {g['obs_date'] if g else '-'})")
        check(f"B4 {key} is active with observations, linked to its security, last_observation_date = max(obs_date), "
              "no duplicate active dates",
              st["n"] > 0 and str(s["security_global_id"]) == sec_id and s["last_observation_date"] == st["maxd"]
              and st["dups"] == 0,
              "an active note underlying must meet every guarantee any other series does",
              f"n={st['n']} link={s['security_global_id']} last={s['last_observation_date']} max={st['maxd']}")

    yrows = await reader.fetch(f"SELECT series_key, source_code, units, ingest_status FROM {SERIES_T} "
                               "WHERE source_provider = 'yahoo' AND ingest_status = 'active' ORDER BY series_key")
    bad_index = [(r["series_key"], r["units"]) for r in yrows
                 if r["source_code"] in INDEX_YAHOO_CODES and r["units"] != yahoo.INDEX_UNITS]
    bad_other = [(r["series_key"], r["units"]) for r in yrows
                 if r["source_code"] not in INDEX_YAHOO_CODES and r["units"] == yahoo.INDEX_UNITS]
    for k, u in bad_index + bad_other:
        find(f"units exception: {k} shows {u!r}")
    check("B5 every active Yahoo index series shows 'index points'; no non-index Yahoo series does",
          any(r["source_code"] in INDEX_YAHOO_CODES for r in yrows) and not bad_index and not bad_other,
          "units are a label members read on the chart; ETFs and futures keep their currency",
          f"index_wrong={bad_index} other_wrong={bad_other}")

    cat = await svc.build_catalog(reader)
    secs = {s["id"]: s for s in cat["securities"]}
    expected_sel = {}
    for key, (sec_id, _n) in EXPECTED_LINKS.items():
        s = await _series(reader, key)
        if s is not None and s["ingest_status"] == "active":
            expected_sel[sec_id] = key
    if not expected_sel:
        skip("B6 none of the five new Yahoo underlyings is active, so none can be selectable (see the [FIND]s above)")
    else:
        check("B6 the mkt03 catalog lists every new ACTIVE linked underlying as selectable with its series_key",
              all(secs.get(i, {}).get("selectable") is True and secs[i].get("series_key") == k
                  for i, k in expected_sel.items()),
              "the overlay picker is how members plot a note's underlying",
              json.dumps({i: (secs.get(i, {}).get("selectable"), secs.get(i, {}).get("series_key"))
                          for i in expected_sel}))

    counts = {r["ingest_status"]: r["n"] for r in await reader.fetch(
        f"SELECT ingest_status, count(*) AS n FROM {SERIES_T} WHERE series_key NOT LIKE '{PREFIX}%' "
        "GROUP BY 1 ORDER BY 1")}
    total = sum(counts.values())
    print(f"[INFO] registry by ingest_status: {counts}")
    check("B7 registry size is 81 (75 v1 + 6 additions)", total == 81,
          "the additions loaded exactly once, and nothing else appeared", f"total={total} {counts}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="also run Phase B against the real data")
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
        await phase_a(conn, reader)
        if args.live:
            await phase_b(reader)
        else:
            skip("Phase B (live data) was not run — pass --live after the operator steps")
    finally:
        await conn.close()
        await reader.close()
    print(f"TOTAL: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
