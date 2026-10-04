#!/usr/bin/env python3
"""verify_mkt03 — market data READ API: catalog, series, grid, correlations.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py
    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py --live

PHASE A (always) is self-contained. It creates FIXTURE series whose
series_key starts with 'verify.mkt03.', inserts their observations inside
platform_scope() — for TEST SETUP ONLY; the production read path never uses
it — and drives the production service functions directly and the real routes
through the real ASGI app (TestClient, ``main.verify_token`` replaced, so the
JWT middleware, the RLS context middleware and the active-account gate all
run as in production). Every expected number is computed in this script from
the fixture definitions, independently of services.market_data.transforms.

PHASE B (--live) reads the real data. It needs no operator pre-steps.

Hydrates secrets from Doppler over HTTPS (scripts/_doppler_env.py), the same
helper verify_mkt01 and verify_mkt02 use. Does NOT chain either of them.

Output: [PASS] / [FAIL] / [FIND] / [SKIP] lines, then
``TOTAL: <passed> passed, <failed> failed``. Exit 1 on any [FAIL]; exit 2 if
teardown leaves a fixture row behind.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]
sys.path.insert(0, str(API_DIR))      # apps/api, for services.* and main
sys.path.insert(0, str(HERE.parent))  # scripts/, for _doppler_env

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402  (fixture SETUP/TEARDOWN only)
from services.market_data import read_service as svc  # noqa: E402

PREFIX = "verify.mkt03."
CATEGORY = "Verify mkt03"
SERIES_T = "market_data.indicator_series"
OBS_T = "market_data.indicator_observations"
RUNS_T = "market_data.indicator_ingest_runs"
UNTOUCHED = ("portfolio.securities_global", "portfolio.securities_global_prices", "public.fx_rates")
# The three links mkt03 shipped with — they must stay linked and selectable.
BASELINE_LINKS = {
    "f15ba4cd-03ba-41b9-aa45-859bf80b2607": "fred.sp500",
    "858a0465-2c59-43dc-b7b0-7c9f4c98f448": "fred.nasdaq100",
    "69265688-90b2-4dfe-b3ac-9d0efb36140f": "yahoo.rut",
}
# Every live link, read at runtime by load_links() (mkt02c added five more):
# {security id: series_key} for non-fixture ACTIVE series with a security —
# exactly the rows the catalog treats as a price source.
LINKS: dict[str, str] = dict(BASELINE_LINKS)


async def load_links(reader) -> None:
    rows = await reader.fetch(
        f"SELECT security_global_id, series_key FROM {SERIES_T} WHERE security_global_id IS NOT NULL "
        f"AND ingest_status = 'active' AND series_key NOT LIKE '{PREFIX}%'")
    LINKS.clear()
    LINKS.update({str(r["security_global_id"]): r["series_key"] for r in rows})
READ_MODULES = [
    API_DIR / "services/market_data/read_repository.py",
    API_DIR / "services/market_data/read_service.py",
    API_DIR / "services/market_data/transforms.py",
    API_DIR / "services/market_data/resample.py",
    API_DIR / "services/market_data/correlation.py",
    API_DIR / "services/market_data/palette.py",
    API_DIR / "services/market_data/access.py",
    API_DIR / "routers/market_data.py",
]
SUB = f"verify-mkt03|{uuid.uuid4().hex}"
SENTINEL = f"SENTINEL{uuid.uuid4().hex[:16]}"
HEADERS = {"Authorization": "Bearer verify-token"}
BASE = "/api/v1/market"
HEX_RE = re.compile(r"^#[0-9A-F]{6}$")

PASSED = 0
FAILED = 0


def check(name: str, ok: bool, why: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"[PASS] {name} — {why}")
    else:
        FAILED += 1
        print(f"[FAIL] {name} — {why}" + (f" | {detail}" if detail else ""))
    return bool(ok)


def find(msg: str) -> None:
    print(f"[FIND] {msg}")


def skip(msg: str) -> None:
    print(f"[SKIP] {msg}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ═══════════════════════════════════════════════════════════════════════════
# Independent arithmetic (does NOT import services.market_data.transforms)
# ═══════════════════════════════════════════════════════════════════════════
D = Decimal


def q6(x: Decimal) -> str:
    return format(x.quantize(D("0.000001"), rounding=ROUND_HALF_EVEN), "f")


def ref_as_of(points, d: date):
    best = None
    for pd, pv in points:  # linear scan on purpose: a different algorithm from bisect
        if pd <= d:
            best = pv
    return best


def ref_months_back(d: date, n: int) -> date:
    import calendar
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_from(start: date, n: int) -> list[date]:
    out = []
    y, m = start.year, start.month
    for _ in range(n):
        out.append(date(y, m, start.day))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.isoweekday() <= 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — every expected value is known from these definitions
# ═══════════════════════════════════════════════════════════════════════════
FIX: dict[str, dict] = {}


def fx(suffix: str, frequency: str, transform: str, points) -> None:
    FIX[suffix] = {"key": PREFIX + suffix, "frequency": frequency, "default_transform": transform,
                   "points": [(d, D(str(v))) for d, v in points]}


def K(suffix: str) -> str:
    return PREFIX + suffix


fx("m_idx", "monthly", "rebase_100", [(d, 100 + 5 * i) for i, d in enumerate(months_from(date(2020, 1, 15), 24))])
fx("m_neg", "monthly", "rebase_100", list(zip(months_from(date(2020, 1, 15), 12), [-5] + [10 * i for i in range(1, 12)])))
fx("m_lvl", "monthly", "level", [(d, D("2.5") + D("0.25") * i) for i, d in enumerate(months_from(date(2020, 1, 15), 12))])
SIG = ["1.5", "2.25", "4", "3.5", "7.125", "6", "5.5", "9", "8.25", "10"]
fx("m_sig", "monthly", "level", list(zip(months_from(date(2020, 1, 15), 10), SIG)))
fx("m_zero", "monthly", "level", [(d, 5) for d in months_from(date(2020, 1, 15), 8)])
fx("m_yoy", "monthly", "yoy_pct", [(d, 200 + 3 * i) for i, d in enumerate(months_from(date(2019, 1, 1), 24))])
fx("m_mom", "monthly", "mom_pct", [(d, 80 + 2 * i) for i, d in enumerate(months_from(date(2019, 1, 1), 24))])
fx("d_res", "daily", "level", [(d, D(i) + D("0.5")) for i, d in enumerate(weekdays(date(2021, 1, 4), date(2021, 3, 31)))])
fx("d_week", "daily", "level", [(date(2020, 12, 28) + timedelta(days=i), i) for i in range(21)])
fx("q_nat", "quarterly", "level", [(date(2020, 1, 1), 1), (date(2020, 4, 1), 2), (date(2020, 7, 1), 3), (date(2020, 10, 1), 4)])
fx("x_exact", "daily", "level", [(date(2022, 3, 1), "4.123456789"), (date(2022, 3, 2), "0.000000123"),
                                 (date(2022, 3, 3), "100")])
# Correlation fixtures — monthly on the 1st from 2015-01-01.
_DD = [((t * 37) % 11) - 5 for t in range(60)]
_A: list[Decimal] = []
_v = D(-1000)
for _t in range(60):
    _v += _DD[_t]
    _A.append(_v)
_M60 = months_from(date(2015, 1, 1), 60)
fx("c_a", "monthly", "level", list(zip(_M60, _A)))                         # all negative → diffs
fx("c_b", "monthly", "level", list(zip(_M60, [2 * a for a in _A])))        # changes exactly 2× a's
fx("c_c", "monthly", "level", list(zip(_M60, [-a - 3000 for a in _A])))    # changes exactly −a's, still negative
fx("c_lead", "monthly", "level", list(zip(_M60[:57], _A[3:])))             # leads a by 3 months
fx("c_short", "monthly", "level", list(zip(months_from(date(2016, 1, 1), 15),
                                           [-50 - ((t * 7) % 5) for t in range(15)])))
_WP = [1, 1, -1, -1] * 15
_WQ = [1, -1, -1, 1] * 15   # orthogonal to _WP, both zero-mean: r = 0 by construction


def _cum(start: int, steps) -> list[int]:
    out = [start]
    for s in steps:
        out.append(out[-1] + s)
    return out


_M61 = months_from(date(2015, 1, 1), 61)
fx("c_p", "monthly", "level", list(zip(_M61, _cum(-500, _WP))))
fx("c_q", "monthly", "level", list(zip(_M61, _cum(-500, _WQ))))
_LOGA = [D(100 + 7 * ((t * 13) % 5) + t) for t in range(60)]
fx("c_loga", "monthly", "level", list(zip(_M60, _LOGA)))
fx("c_logb", "monthly", "level", list(zip(_M60, [a * a for a in _LOGA])))   # log changes exactly 2×
fx("d_corr", "daily", "level", [(d, 1000 + (i * 37) % 101) for i, d in enumerate(weekdays(date(2015, 1, 1), date(2019, 12, 31)))])
fx("q_corr", "quarterly", "level", list(zip(months_from(date(2015, 1, 1), 60)[::3], [500 + (t * 17) % 23 for t in range(20)])))

CORR_ANCHOR, CORR_END = "2015-01-01", "2020-12-31"


async def insert_fixtures(conn) -> None:
    async with platform_scope(conn):
        for i, (suffix, f) in enumerate(FIX.items()):
            sid = await conn.fetchval(
                f"""INSERT INTO {SERIES_T} (series_key, name, category, region, frequency, default_transform,
                        cost_tier, license_class, source_provider, source_code, notes, sort_order, ingest_status)
                    VALUES ($1, $2, $3, 'US', $4, $5, 'free', 'public_domain', 'verifyfake', $6,
                            'mkt03 verify fixture', $7, 'active') RETURNING id""",
                f["key"], f"verify mkt03 {suffix}", CATEGORY, f["frequency"], f["default_transform"],
                f"VMKT03{i}", -3000 + i)
            await conn.execute(
                f"""INSERT INTO {OBS_T} (series_id, obs_date, value)
                    SELECT $1, d, v FROM unnest($2::date[], $3::numeric[]) AS t(d, v)""",
                sid, [d for d, _ in f["points"]], [v for _, v in f["points"]])


# ═══════════════════════════════════════════════════════════════════════════
# Counting + teardown
# ═══════════════════════════════════════════════════════════════════════════
FIXTURE_IDS = f"(SELECT id FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%')"


async def fixture_counts(conn) -> dict:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(f"SELECT count(*) FROM {RUNS_T} WHERE series_id IN {FIXTURE_IDS}"),
        }


async def real_counts(conn) -> dict:
    async with platform_scope(conn):  # indicator_ingest_runs is platform-only
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key NOT LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id NOT IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(
                f"SELECT count(*) FROM {RUNS_T} WHERE series_id IS NULL OR series_id NOT IN {FIXTURE_IDS}"),
        }


async def untouched_counts(conn) -> dict:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in UNTOUCHED}


async def fk_delete_order(conn) -> list[str]:
    """Child-before-parent over the market_data tables, DERIVED from
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
    outside = [(r["child"], r["parent"]) for r in rows if r["child"] not in tables]
    if outside:
        raise SystemExit(f"[FAIL] teardown: tables outside market_data reference it: {outside}")
    children = {t: {r["child"] for r in rows if r["parent"] == t and r["child"] != t} for t in tables}
    order: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not (children[t] & remaining))
        if not ready:
            raise SystemExit(f"[FAIL] teardown: FK cycle among {remaining}")
        order.extend(ready)
        remaining -= set(ready)
    return order


async def delete_fixtures(conn, order) -> dict:
    """By fixture tag only — never a TRUNCATE."""
    where = {OBS_T: f"series_id IN {FIXTURE_IDS}", RUNS_T: f"series_id IN {FIXTURE_IDS}",
             SERIES_T: f"series_key LIKE '{PREFIX}%'"}
    deleted = {}
    async with platform_scope(conn):
        for table in order:
            status = await conn.execute(f"DELETE FROM {table} WHERE {where[table]}")
            deleted[table] = int(status.split()[-1])
    return deleted


async def users_row_for_sub(conn) -> int:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_auth0_sub', $1, true)", SUB)
        return await conn.fetchval("SELECT count(*) FROM users WHERE auth0_sub = $1", SUB)


# ═══════════════════════════════════════════════════════════════════════════
# HTTP through the real app — ONE TestClient for the whole pass
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class Resp:
    status: int
    text: str
    body: object
    elapsed: float
    size: int


def run_http(plan: list[tuple]) -> dict[str, Resp]:
    """Sync (TestClient is sync); run off the main loop. One client, entered
    once, so the app's module-global pool lives on ONE event loop for the
    whole pass (a client per request kills the pool after request 1)."""
    import main
    from services.action_registry import REGISTRY
    from starlette.testclient import TestClient

    async def _noop_sync(pool, org_id):  # startup would otherwise write the real default org's catalog
        return None

    REGISTRY.sync_catalog = _noop_sync
    main.verify_token = lambda _t: {"sub": SUB, "email": f"{SUB}@test.local"}
    out: dict[str, Resp] = {}
    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        for name, method, path, payload, auth in plan:
            headers = dict(HEADERS) if auth else {}
            t0 = time.perf_counter()
            if method == "GET":
                res = client.get(path, params=payload, headers=headers)
            else:
                if isinstance(payload, (bytes, str)):
                    res = client.post(path, content=payload, headers={**headers, "Content-Type": "application/json"})
                else:
                    res = client.post(path, json=payload, headers=headers)
            elapsed = time.perf_counter() - t0
            try:
                body = json.loads(res.text)
            except ValueError:
                body = None
            out[name] = Resp(res.status_code, res.text, body, elapsed, len(res.content))
    finally:
        client.__exit__(None, None, None)
    return out


def grid_body(keys, anchor, end, mode, frequency, **extra):
    b = {"keys": keys, "anchor": anchor, "mode": mode, "frequency": frequency, **extra}
    if end is not None:
        b["end"] = end
    return b


def corr_body(focus, keys, anchor=CORR_ANCHOR, end=CORR_END, **extra):
    return {"focus_key": focus, "keys": keys, "anchor": anchor, "end": end, **extra}


def col(resp: Resp, key: str) -> dict:
    for s in (resp.body or {}).get("series", []):
        if s["series_key"] == key:
            return s
    return {}


def cells(resp: Resp, key: str) -> dict[str, object]:
    return {r["date"]: r["cells"].get(key) for r in (resp.body or {}).get("rows", [])}


def result(resp: Resp, key: str) -> dict:
    for r in (resp.body or {}).get("results", []):
        if r["series_key"] == key:
            return r
    return {}


def no_echo(resp: Resp, *needles: str) -> bool:
    return all(n not in resp.text for n in needles)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A
# ═══════════════════════════════════════════════════════════════════════════
def phase_a_plan(deferred_key: str) -> list[tuple]:
    G, S, C = f"{BASE}/grid", f"{BASE}/series", f"{BASE}/correlations"
    plan = [
        ("cat1", "GET", f"{BASE}/catalog", None, True),
        ("cat2", "GET", f"{BASE}/catalog", None, True),
        # as-of + floating + index
        ("g_asof", "POST", G, grid_body([K("m_idx")], "2020-03-01", "2020-06-30", "index", "monthly"), True),
        ("g_float", "POST", G, grid_body([K("m_idx"), K("m_neg"), K("m_lvl"), K("q_nat")],
                                         "2019-11-10", "2020-04-20", "index", "monthly"), True),
        ("g_sigma", "POST", G, grid_body([K("m_sig"), K("m_zero")], "2020-02-01", "2020-10-31", "sigma", "monthly"), True),
        ("g_default", "POST", G, grid_body([K("m_yoy"), K("m_mom"), K("m_idx")], "2019-01-01", "2020-12-31",
                                           "default", "monthly"), True),
        ("g_rowcap", "POST", G, grid_body([K("m_idx")], "1960-01-01", "2020-01-01", "index", "weekly"), True),
        ("g_daily_long", "POST", G, grid_body([K("d_res")], "2020-01-01", "2021-02-05", "level", "daily"), True),
        ("g_daily_ok", "POST", G, grid_body([K("d_res")], "2020-01-01", "2021-02-03", "level", "daily"), True),
        # resampling + exactness
        ("s_month", "GET", S, {"keys": K("d_res"), "frequency": "monthly"}, True),
        ("s_quarter", "GET", S, {"keys": K("q_nat"), "frequency": "monthly"}, True),
        ("s_week", "GET", S, {"keys": K("d_week"), "frequency": "weekly"}, True),
        ("s_exact", "GET", S, {"keys": K("x_exact")}, True),
        # correlations
        ("c_order", "POST", C, corr_body(K("c_a"), [K("c_short"), K("c_lead"), K("c_b"), K("c_c"), K("c_p"), K("c_a")]), True),
        ("c_pq", "POST", C, corr_body(K("c_p"), [K("c_q")]), True),
        ("c_lag3", "POST", C, corr_body(K("c_a"), [K("c_lead")], lag_months=3), True),
        ("c_lag0", "POST", C, corr_body(K("c_a"), [K("c_lead")], lag_months=0), True),
        ("c_dq", "POST", C, corr_body(K("d_corr"), [K("q_corr")], end="2019-12-31", min_periods=12), True),
        ("c_log", "POST", C, corr_body(K("c_loga"), [K("c_logb")]), True),
        # validation — every refusal must not echo the request
        ("v_org", "POST", G, grid_body([K("m_idx")], "2020-01-01", None, "index", "monthly", org_id=SENTINEL), True),
        ("v_user", "POST", C, corr_body(K("c_a"), [K("c_b")], user_id=SENTINEL), True),
        ("v_org_q", "GET", S, {"keys": K("m_idx"), "org_id": SENTINEL}, True),
        ("v_unknown", "POST", G, grid_body([K("m_idx"), K("nosuchseries")], "2020-01-01", None, "index", "monthly"), True),
        ("v_inactive", "GET", S, {"keys": deferred_key}, True),
        ("v_empty", "POST", G, grid_body([], "2020-01-01", None, "index", "monthly"), True),
        ("v_41", "POST", G, grid_body([K(f"zz{i}{SENTINEL.lower()}") for i in range(41)], "2020-01-01", None,
                                      "index", "monthly"), True),
        ("v_41s", "GET", S, {"keys": ",".join(K(f"zz{i}") for i in range(41))}, True),
        ("v_41c", "POST", C, corr_body(K("c_a"), [K(f"zz{i}") for i in range(41)]), True),
        ("v_order", "POST", G, grid_body([K("m_idx")], "2021-01-01", "2020-01-01", "index", "monthly"), True),
        ("v_baddate", "POST", G, grid_body([K("m_idx")], "2020-13-45", None, "index", "monthly"), True),
        ("v_baddate_s", "GET", S, {"keys": K("m_idx"), "from": "20x0-01-01"}, True),
        ("v_lag", "POST", C, corr_body(K("c_a"), [K("c_b")], lag_months=25), True),
        ("v_json", "POST", G, f'{{"keys": ["{SENTINEL}"', True),
    ]
    # permission gate: the IDENTICAL request without and with a session
    gate = [
        ("cat", "GET", f"{BASE}/catalog", None),
        ("ser", "GET", S, {"keys": K("m_idx")}),
        ("grid", "POST", G, grid_body([K("m_idx")], "2020-03-01", "2020-06-30", "index", "monthly")),
        ("corr", "POST", C, corr_body(K("c_a"), [K("c_b")])),
    ]
    for name, method, path, payload in gate:
        plan.append((f"gate_anon_{name}", method, path, payload, False))
        plan.append((f"gate_auth_{name}", method, path, payload, True))
    return plan


def a_catalog(R, live_active: int, sec_expected: int) -> None:
    section("Catalog")
    c1, c2 = R["cat1"], R["cat2"]
    body = c1.body or {}
    inds = body.get("indicators", [])
    check("C1 catalog indicators = the active series count read at runtime",
          c1.status == 200 and len(inds) == live_active,
          "the catalog is the chart's only source of selectable series; a missing or extra one is a wrong menu",
          f"status={c1.status} indicators={len(inds)} active={live_active}")
    bad = [i["series_key"] for i in inds if not HEX_RE.match(i.get("color") or "")]
    check("C2 every indicator carries a valid #RRGGBB colour", not bad and bool(inds),
          "Rule 1: colours come from the server; the client has no fallback to paint with", f"bad={bad[:5]}")
    by_cat: dict[str, list[str]] = {}
    for i in inds:
        by_cat.setdefault(i["category"], []).append(i["color"])
    dup = {c: v for c, v in by_cat.items() if len(set(v)) != len(v)}
    check("C3 colours are distinct within every category", not dup,
          "two series in one category with the same colour cannot be told apart on the chart", f"dupes={list(dup)}")
    colors1 = {i["series_key"]: i["color"] for i in inds}
    colors2 = {i["series_key"]: i["color"] for i in (c2.body or {}).get("indicators", [])}
    sc1 = {s["id"]: s["color"] for s in body.get("securities", [])}
    sc2 = {s["id"]: s["color"] for s in (c2.body or {}).get("securities", [])}
    check("C4 colours are identical across two calls (series and securities)",
          colors1 == colors2 and sc1 == sc2 and bool(colors1),
          "a colour that changes between page loads re-paints a member's saved chart")
    secs = body.get("securities", [])
    check("C5 securities lists every active, non-merged security-master row",
          len(secs) == sec_expected, "the overlay picker must show the whole master, selectable or not",
          f"listed={len(secs)} expected={sec_expected}")
    by_id = {s["id"]: s for s in secs}
    linked_ok = all(by_id.get(i, {}).get("selectable") is True and by_id[i].get("series_key") == k
                    and by_id[i].get("price_source") == "indicator_series" for i, k in LINKS.items())
    linked_ok = linked_ok and all(LINKS.get(i) == k for i, k in BASELINE_LINKS.items())
    check("C6 every linked index security (the three baseline links + any read at runtime) is selectable "
          "with its series_key", linked_ok,
          "these are the only securities the overlay can plot today",
          json.dumps({i: (by_id.get(i, {}).get("selectable"), by_id.get(i, {}).get("series_key")) for i in LINKS}))
    others = [s for s in secs if s["id"] not in LINKS]
    others_ok = all(s["selectable"] is False and s["unselectable_reason"] == "no_price_history"
                    and s["price_source"] == "none" and s["series_key"] is None for s in others)
    check("C7 every other security is unselectable with reason 'no_price_history'", others_ok and bool(others),
          "a selectable security with no price history would plot an empty line", f"others={len(others)}")
    perms = body.get("permissions") or {}
    vocab = body.get("vocabularies") or {}
    env_ok = (perms.get("can_read") is True and perms.get("can_write") is False
              and "is_super_admin" in perms and perms.get("read_permission") is None
              and "write_permission" in perms
              and vocab.get("editable") == [] and vocab.get("inline_editable") == []
              and all(vocab.get(k) for k in ("transforms", "frequencies", "modes", "license_classes", "source_providers")))
    check("C8 permissions envelope and vocabularies are present (editable lists empty; read_permission null)",
          env_ok, "the platform envelope pattern; a lost envelope must fail CLOSED in the UI",
          f"permissions={perms} vocab_keys={sorted(vocab)}")


def a_anchor_index(R) -> None:
    section("as-of, anchor, index")
    m = dict(FIX["m_idx"]["points"])
    g = R["g_asof"]
    meta = col(g, K("m_idx"))
    v0 = ref_as_of(FIX["m_idx"]["points"], date(2020, 3, 1))
    exp = {d.isoformat(): q6(100 * ref_as_of(FIX["m_idx"]["points"], d) / v0)
           for d in (date(2020, 3, 31), date(2020, 4, 30), date(2020, 5, 31), date(2020, 6, 30))}
    check("X1 an anchor with no observation that day uses the last observation on or before it",
          g.status == 200 and meta.get("anchor_observation_date") == "2020-02-15" and meta.get("floating") is False
          and meta.get("anchor_value") == format(m[date(2020, 2, 15)], "f"),
          "anchoring on a non-trading day must not silently pick the NEXT observation",
          f"status={g.status} meta={meta}")
    f = R["g_float"]
    fm = col(f, K("m_idx"))
    fc = cells(f, K("m_idx"))
    check("X2 an anchor before the first observation sets floating = true and measures from the first observation",
          fm.get("floating") is True and fm.get("anchor_observation_date") == "2020-01-15"
          and fc.get("2020-01-31") == "100.000000" and fc.get("2020-04-20") == "115.000000",
          "a late-starting series must still be comparable, and the UI must be told it floats", f"meta={fm} cells={fc}")
    check("I1 index values equal 100 * v / v0 exactly (Decimal, 6 dp, half-even)",
          cells(g, K("m_idx")) == exp, "the grid is the one server-side definition of index",
          f"got={cells(g, K('m_idx'))} expected={exp}")
    neg = col(f, K("m_neg"))
    check("I2 a non-positive anchor value gives unavailable_reason 'non_positive_anchor' and no values",
          neg.get("unavailable_reason") == "non_positive_anchor" and all(v is None for v in cells(f, K("m_neg")).values()),
          "dividing by a zero or negative base produces a meaningless or sign-flipped index", f"meta={neg}")
    lvl = col(f, K("m_lvl"))
    check("I3 a default_transform='level' series indexed in 'index' mode carries 'rate_like_series_indexed'",
          "rate_like_series_indexed" in (lvl.get("warnings") or []) and lvl.get("unavailable_reason") is None
          and any(v is not None for v in cells(f, K("m_lvl")).values()),
          "indexing a rate is allowed but misleading; the UI needs the flag to say so", f"meta={lvl}")


def a_sigma(R, sd_sql: Decimal) -> None:
    section("sigma")
    g = R["g_sigma"]
    pts = FIX["m_sig"]["points"]
    v0 = ref_as_of(pts, date(2020, 2, 1))
    got = cells(g, K("m_sig"))
    exp = {d: (q6((ref_as_of(pts, date.fromisoformat(d)) - v0) / sd_sql)) for d in got}
    check("S1 sigma equals (v - v0) / stddev_samp, with stddev_samp computed independently in SQL",
          g.status == 200 and got == exp and len(got) >= 8,
          "sigma is only comparable across series if the scale is the full-history sample deviation",
          f"sd_sql={sd_sql} got={got} expected={exp}")
    z = col(g, K("m_zero"))
    check("S2 a zero-variance series gives unavailable_reason 'zero_variance'",
          z.get("unavailable_reason") == "zero_variance" and all(v is None for v in cells(g, K("m_zero")).values()),
          "dividing by a zero deviation must be refused, not returned as infinity or an error", f"meta={z}")


def a_default(R) -> None:
    section("default mode: yoy and mom")
    g = R["g_default"]
    for suffix, months, label in (("m_yoy", 12, "Y1 yoy"), ("m_mom", 1, "Y2 mom")):
        pts = FIX[suffix]["points"]
        got = cells(g, K(suffix))
        exp = {}
        for d in got:
            dd = date.fromisoformat(d)
            v, e = ref_as_of(pts, dd), ref_as_of(pts, ref_months_back(dd, months))
            exp[d] = None if v is None or e is None or e == 0 else q6(100 * (v / e - 1))
        has_null = any(v is None for v in got.values())
        has_val = any(v is not None for v in got.values())
        meta = col(g, K(suffix))
        check(f"{label} in 'default' mode matches hand-computed values and is null when the earlier point is missing",
              g.status == 200 and got == exp and has_null and has_val
              and meta.get("applied_transform") == ("yoy" if months == 12 else "mom"),
              "default mode must apply each series' OWN transform, never invent a base", f"got={got} exp={exp}")
    check("Y3 the 2019-12-31 yoy cell is null (no observation 12 months earlier) and 2019-02-28 mom is '2.500000'",
          cells(g, K("m_yoy")).get("2019-12-31") is None and cells(g, K("m_mom")).get("2019-02-28") == "2.500000"
          and cells(g, K("m_mom")).get("2019-01-31") is None,
          "pins the two hand-computed edges the general comparison above relies on")


def a_resample(R) -> None:
    section("Resampling")
    s = (R["s_month"].body or {}).get("series", [{}])[0]
    pts = dict(FIX["d_res"]["points"])
    exp = [["2021-01-29", format(pts[date(2021, 1, 29)], "f")], ["2021-02-26", format(pts[date(2021, 2, 26)], "f")],
           ["2021-03-31", format(pts[date(2021, 3, 31)], "f")]]
    check("R1 monthly takes the LAST observation of each calendar month and reports its true obs_date",
          R["s_month"].status == 200 and s.get("points") == exp and s.get("frequency") == "monthly",
          "a month-end label on a mid-month value would misdate every point", f"got={s.get('points')}")
    q = (R["s_quarter"].body or {}).get("series", [{}])[0]
    check("R2 a quarterly series requested monthly is returned natively, not upsampled",
          q.get("frequency") == "quarterly" and q.get("point_count") == 4
          and [p[0] for p in q.get("points", [])] == ["2020-01-01", "2020-04-01", "2020-07-01", "2020-10-01"],
          "upsampling invents data points that were never observed", f"got={q}")
    w = (R["s_week"].body or {}).get("series", [{}])[0]
    check("R3 weekly uses ISO weeks (Monday–Sunday): 2020-12-28..2021-01-03 is one week",
          [p[0] for p in w.get("points", [])] == ["2021-01-03", "2021-01-10", "2021-01-17"],
          "a calendar-year or Sunday-start week splits the year-end week in two", f"got={w.get('points')}")


def a_grid(R) -> None:
    section("Grid")
    f = R["g_float"]
    rows = (f.body or {}).get("rows", [])
    dates = [r["date"] for r in rows]
    check("G1 rows are period ends, newest first; only the newest row may be the window end",
          dates == ["2020-04-20", "2020-03-31", "2020-02-29", "2020-01-31", "2019-12-31", "2019-11-30"]
          and rows[0]["is_period_end"] is False and all(r["is_period_end"] for r in rows[1:]),
          "rows must line up across series at the same calendar instants", f"dates={dates}")
    fc = cells(f, K("m_idx"))
    check("G2 cells before a series' first observation are null",
          fc.get("2019-11-30") is None and fc.get("2019-12-31") is None and fc.get("2020-01-31") is not None,
          "a value before the series exists would be fabricated")
    qc = cells(f, K("q_nat"))
    qm = col(f, K("q_nat"))
    check("G3 carry-forward uses the as-of rule (quarterly series in a monthly grid)",
          qc.get("2020-02-29") == "100.000000" and qc.get("2020-03-31") == "100.000000"
          and qc.get("2020-04-20") == "200.000000"
          and "native_frequency_coarser_than_grid" in (qm.get("warnings") or []),
          "between releases the last known value is the right one, and the UI is told it is carried", f"cells={qc} meta={qm}")
    check("G4 the 2,000-row cap is enforced (weekly over 60 years → 422)",
          R["g_rowcap"].status == 422, "an unbounded grid is an unbounded response", f"status={R['g_rowcap'].status}")
    check("G5 daily rows are refused past 400 days and admitted at 400",
          R["g_daily_long"].status == 422 and R["g_daily_ok"].status == 200,
          "the daily grid limit is the documented one, at the boundary",
          f"401+ days={R['g_daily_long'].status} 400 days={R['g_daily_ok'].status}")
    check("G6 the key cap is enforced on grid, series and correlations (41 keys → 422)",
          R["v_41"].status == 422 and R["v_41s"].status == 422 and R["v_41c"].status == 422,
          "the 40-key limit bounds the query fan-out on every endpoint",
          f"grid={R['v_41'].status} series={R['v_41s'].status} corr={R['v_41c'].status}")


def a_correlations(R) -> None:
    section("Correlations")
    o = R["c_order"]
    check("K1 changes exactly 2× the focus's give r = '1.0000'", result(o, K("c_b")).get("r") == "1.0000",
          "a perfect linear relation in changes must read as exactly 1", f"got={result(o, K('c_b'))}")
    check("K2 changes exactly −1× the focus's give r = '-1.0000'", result(o, K("c_c")).get("r") == "-1.0000",
          "the sign must survive", f"got={result(o, K('c_c'))}")
    pq = result(R["c_pq"], K("c_q"))
    dp = [b - a for a, b in zip(_cum(-500, _WP), _cum(-500, _WP)[1:])]
    dq = [b - a for a, b in zip(_cum(-500, _WQ), _cum(-500, _WQ)[1:])]
    indep = statistics.correlation([float(x) for x in dp], [float(y) for y in dq])
    check("K3 an uncorrelated-by-construction pair matches an independent computation within 0.0001",
          pq.get("r") is not None and abs(float(pq["r"]) - indep) <= 0.0001 and pq.get("n") == 60,
          "the coefficient must agree with a second, unrelated implementation", f"api={pq} independent={indep:.6f}")
    sh = result(o, K("c_short"))
    check("K4 fewer than min_periods overlapping periods gives r = null, 'insufficient_overlap'",
          sh.get("r") is None and sh.get("unavailable_reason") == "insufficient_overlap" and sh.get("n") == 14,
          "a coefficient from 14 points is noise presented as signal", f"got={sh}")
    l3, l0 = result(R["c_lag3"], K("c_lead")), result(R["c_lag0"], K("c_lead"))
    check("K5 a series leading by 3 months gives r = '1.0000' at lag 3 and a lower value at lag 0",
          l3.get("r") == "1.0000" and l0.get("r") is not None and float(l0["r"]) < 0.99
          and (R["c_lag3"].body or {}).get("effective_lag_months") == 3,
          "positive lag means the candidate LEADS; getting the direction backwards inverts every reading",
          f"lag3={l3} lag0={l0}")
    dq_ = result(R["c_dq"], K("q_corr"))
    check("K6 a daily-versus-quarterly pair runs at, and reports, frequency 'quarterly'",
          dq_.get("frequency") == "quarterly" and dq_.get("n") == 19 and dq_.get("r") is not None,
          "correlating a daily series against a carried-forward quarterly one fakes overlap", f"got={dq_}")
    lg = result(R["c_log"], K("c_logb"))
    check("K7 non-positive series use differences; all-positive series use log changes",
          result(o, K("c_b")).get("change_method") == {"focus": "diff", "candidate": "diff"}
          and lg.get("change_method") == {"focus": "log", "candidate": "log"} and lg.get("r") == "1.0000",
          "ln of a non-positive value is undefined; a squared series has exactly 2× the log changes",
          f"diff={result(o, K('c_b')).get('change_method')} log={lg}")
    keys = [r["series_key"] for r in (o.body or {}).get("results", [])]
    check("K8 the focus is excluded from its own results, even when listed in keys",
          K("c_a") not in keys and len(keys) == 5, "self-correlation is always 1 and would top every list",
          f"keys={keys}")
    rs = (o.body or {}).get("results", [])
    vals = [abs(float(r["r"])) for r in rs if r["r"] is not None]
    first_null = next((i for i, r in enumerate(rs) if r["r"] is None), len(rs))
    check("K9 results are ordered by |r| descending with nulls last",
          vals == sorted(vals, reverse=True) and all(r["r"] is None for r in rs[first_null:]) and first_null < len(rs),
          "the UI shows the strongest relationships first", f"order={[(r['series_key'][len(PREFIX):], r['r']) for r in rs]}")


def _strings_only(resp: Resp) -> list[str]:
    bad: list[str] = []
    body = resp.body or {}
    for s in body.get("series", []):
        for p in s.get("points", []):
            if not isinstance(p[1], str):
                bad.append(f"series point {p}")
        if s.get("anchor_value") is not None and not isinstance(s["anchor_value"], str):
            bad.append("anchor_value")
    for r in body.get("rows", []):
        for k, v in r["cells"].items():
            if v is not None and not isinstance(v, str):
                bad.append(f"cell {k}")
    for r in body.get("results", []):
        if r["r"] is not None and not isinstance(r["r"], str):
            bad.append(f"r {r['series_key']}")
    return bad


def a_exactness(R) -> None:
    section("Exactness")
    x = (R["s_exact"].body or {}).get("series", [{}])[0]
    vals = [p[1] for p in x.get("points", [])]
    check("E1 '4.123456789' (and '0.000000123') round-trip through the series endpoint as identical strings",
          vals[:2] == ["4.123456789", "0.000000123"] and '"4.123456789"' in R["s_exact"].text,
          "a float hop or exponent notation silently changes a member's numbers", f"got={vals}")
    bad = []
    for name in ("s_exact", "s_month", "g_float", "g_sigma", "g_default", "c_order", "c_pq"):
        bad += [f"{name}: {b}" for b in _strings_only(R[name])]
    check("E2 no JSON number appears where a string is specified (points, cells, anchor values, r)",
          not bad, "JSON numbers are IEEE doubles in every browser", f"bad={bad[:5]}")


def a_gate(R) -> None:
    section("Permission gate (session-only, Task 1c)")
    rows = []
    ok = True
    for name in ("cat", "ser", "grid", "corr"):
        anon, auth = R[f"gate_anon_{name}"], R[f"gate_auth_{name}"]
        rows.append(f"{name}: anon={anon.status} auth={auth.status}")
        ok = ok and anon.status == 401 and auth.status == 200
    check("P1 the IDENTICAL request is refused without a session (401) and admitted with one (200), all four endpoints",
          ok, "proves the gate both ways on the same request, not two different requests", "; ".join(rows))
    from fastapi import HTTPException
    from services.market_data import access

    class _Req:
        class state:  # noqa: N801
            user = None
    refused = False
    try:  # runs in an executor thread, so asyncio.run has no outer loop to clash with
        asyncio.run(access.require_market_data_read(_Req()))
    except HTTPException as exc:
        refused = exc.status_code == 401
    check("P2 the gate function itself refuses a request with no verified session",
          refused, "keeps the endpoint closed even if its path were ever added to PUBLIC_PATHS by mistake")
    perms = (R["gate_auth_grid"].body or {}).get("permissions") or {}
    check("P3 the authenticated test caller is NOT super admin, and the envelope says so",
          perms.get("is_super_admin") is False,
          "the endpoints must work for an ordinary member, not only through the super-admin carve-out", f"perms={perms}")
    find(f"Task 1c: no existing permission clearly means 'may read market data' (nearest: view_dashboard, "
         f"view_portfolio). Gate = valid session only; read_permission = {access.MARKET_DATA_READ_PERMISSION!r}. "
         "Set access.MARKET_DATA_READ_PERMISSION to add one.")


def a_validation(R, deferred_key: str) -> None:
    section("Validation")
    for name, label in (("v_org", "org_id in a grid body"), ("v_user", "user_id in a correlations body"),
                        ("v_org_q", "org_id as a series query parameter")):
        r = R[name]
        check(f"V1 {label} is rejected with 422 and neither the value nor the field name is echoed",
              r.status == 422 and no_echo(r, SENTINEL, "org_id", "user_id"),
              "Rule 6: org and user come only from the verified session", f"status={r.status} body={r.text[:200]}")
    u = R["v_unknown"]
    check("V2 an unknown key → 422 listing ONLY the offending key",
          u.status == 422 and ((u.body or {}).get("detail") or {}).get("unknown_keys") == [K("nosuchseries")]
          and K("m_idx") not in u.text, "the caller learns which key to fix and nothing else", f"body={u.text[:200]}")
    i = R["v_inactive"]
    check(f"V3 a real non-active ('deferred') key → 422 listing it as inactive ({deferred_key})",
          i.status == 422 and ((i.body or {}).get("detail") or {}).get("inactive_keys") == [deferred_key],
          "a deferred series has no data; serving it would plot nothing", f"body={i.text[:200]}")
    for name, label in (("v_empty", "empty keys"), ("v_41", "41 keys"), ("v_order", "anchor after end"),
                        ("v_baddate", "a malformed date (body)"), ("v_baddate_s", "a malformed date (query)"),
                        ("v_lag", "lag_months outside -24..24"), ("v_json", "malformed JSON")):
        r = R[name]
        check(f"V4 {label} → 422 with no body echo",
              r.status == 422 and no_echo(r, SENTINEL, SENTINEL.lower(), "2020-13-45", "20x0-01-01", "zz0"),
              "every refusal is specific about the rule and silent about the input", f"status={r.status} body={r.text[:200]}")


def a_static() -> None:
    section("Least privilege (static)")
    offenders = []
    for path in READ_MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if any(n.split(".")[-1] == "platform_scope" for n in names):
                offenders.append(f"{path.name}:{getattr(node, 'lineno', '?')}")
    check("L1 no read module imports or references platform_scope (AST, docstrings excluded)",
          not offenders, "the read path must never widen its own RLS scope", f"offenders={offenders}")


async def a_direct(dsn) -> None:
    section("Least privilege (live connection)")
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check("L2 the test connection's role has rolbypassrls = false",
              bypass is False, "every isolation proof below is void on a bypass role", f"rolbypassrls={bypass}")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                               "set_config('app.current_org_id', '', true)")
            sup = await conn.fetchval("SELECT current_setting('app.is_super_admin', true)")
            from services.market_data import read_repository as repo
            await repo.make_read_only(conn)
            cat = await svc.build_catalog(conn)
            ser = await svc.read_series(conn, svc.SeriesQuery([K("m_idx")], None, None, "native"))
            grid = await svc.build_grid(conn, svc.GridRequest([K("m_idx")], date(2020, 3, 1), date(2020, 6, 30),
                                                             "index", "monthly"))
            corr = await svc.build_correlations(conn, svc.CorrelationRequest(
                K("c_a"), [K("c_b")], date(2015, 1, 1), date(2020, 12, 31), 0, 24))
            ro = await conn.fetchval("SELECT current_setting('transaction_read_only')")
        check("L3 all four service functions succeed with no super-admin context and no org, on a read-only transaction",
              sup == "false" and ro == "on" and len(cat["indicators"]) > 0 and ser["series"][0]["point_count"] == 24
              and grid["row_count"] == 4 and corr["results"][0]["r"] == "1.0000",
              "the global SELECT policies are enough; nothing on the read path needs platform scope",
              f"super={sup} read_only={ro}")
    finally:
        await conn.close()


async def phase_a(conn, reader, dsn) -> None:
    order = await fk_delete_order(reader)
    leftover = await delete_fixtures(conn, order)
    if any(leftover.values()):
        find(f"removed fixture rows left by an earlier interrupted run: {leftover}")
    base_untouched = await untouched_counts(reader)
    base_real = await real_counts(reader)
    try:
        section("Setup")
        await insert_fixtures(conn)
        n = await fixture_counts(reader)
        check("A0 fixtures inserted", n["series"] == len(FIX) and n["obs"] == sum(len(f["points"]) for f in FIX.values()),
              "every assertion below reads these rows", f"counts={n}")
        live_active = await reader.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE ingest_status = 'active'")
        await load_links(reader)
        sec_expected = await reader.fetchval(
            "SELECT count(*) FROM portfolio.securities_global WHERE valid_to IS NULL AND system_to IS NULL "
            "AND merged_into_id IS NULL")
        sd_sql = await reader.fetchval(
            f"SELECT stddev_samp(o.value) FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id "
            "WHERE s.series_key = $1 AND o.valid_to IS NULL AND o.system_to IS NULL", K("m_sig"))
        deferred_key = await reader.fetchval(
            f"SELECT series_key FROM {SERIES_T} WHERE ingest_status = 'deferred' ORDER BY series_key LIMIT 1")

        a_static()
        await a_direct(dsn)

        R = await asyncio.get_running_loop().run_in_executor(None, run_http, phase_a_plan(deferred_key))
        a_catalog(R, live_active, sec_expected)
        a_anchor_index(R)
        a_sigma(R, sd_sql)
        a_default(R)
        a_resample(R)
        a_grid(R)
        a_correlations(R)
        a_exactness(R)
        await asyncio.get_running_loop().run_in_executor(None, a_gate, R)
        a_validation(R, deferred_key)
        n_users = await users_row_for_sub(reader)
        check("L4 the read endpoints created no users row for the caller",
              n_users == 0, "a session-only gate must not write; reading market data is not an onboarding step",
              f"rows={n_users}")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — reported, then teardown still runs
        check("A.x Phase A ran to completion", False, "an unexpected exception aborted the remaining assertions",
              f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        section("Teardown")
        expected_order = order.index(OBS_T) < order.index(SERIES_T)
        deleted = await delete_fixtures(conn, order)
        remaining = await fixture_counts(reader)
        end_real = await real_counts(reader)
        end_untouched = await untouched_counts(reader)
        check("T1 portfolio.securities_global, securities_global_prices and public.fx_rates are untouched",
              end_untouched == base_untouched, "this sprint must never write the security master or prices",
              f"before={base_untouched} after={end_untouched}")
        check("T2 teardown order observations → series, derived from information_schema",
              expected_order, "a parent deleted first fails on the FK", f"order={order} deleted={deleted}")
        check("T3 NON-fixture counts in indicator_series, indicator_observations, indicator_ingest_runs are exactly as before",
              end_real == base_real, "proves the read API wrote nothing and teardown removed only fixtures — never a TRUNCATE",
              f"before={base_real} after={end_real}")
        if any(remaining.values()):
            print(f"[FAIL] T4 fixture rows remain after teardown: {remaining}")
            raise SystemExit(2)
        check("T4 zero fixture rows remain — re-read independently", True,
              "a leftover fixture would appear in every member's catalog")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — live data
# ═══════════════════════════════════════════════════════════════════════════
async def _first_last(reader, key):
    rows = await reader.fetch(
        f"""(SELECT o.obs_date, o.value FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id
              WHERE s.series_key = $1 AND o.valid_to IS NULL AND o.system_to IS NULL ORDER BY o.obs_date LIMIT 1)
            UNION ALL
            (SELECT o.obs_date, o.value FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id
              WHERE s.series_key = $1 AND o.valid_to IS NULL AND o.system_to IS NULL ORDER BY o.obs_date DESC LIMIT 1)""",
        key)
    return (rows[0]["obs_date"], rows[0]["value"]), (rows[1]["obs_date"], rows[1]["value"])


async def _as_of_sql(reader, key, d):
    return await reader.fetchrow(
        f"""SELECT o.obs_date, o.value FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id
             WHERE s.series_key = $1 AND o.valid_to IS NULL AND o.system_to IS NULL AND o.obs_date <= $2
             ORDER BY o.obs_date DESC LIMIT 1""", key, d)


async def phase_b(reader) -> None:
    section("PHASE B — live")
    live_active = await reader.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE ingest_status = 'active'")
    await load_links(reader)
    (sp_first, sp_latest) = await _first_last(reader, "fred.sp500")
    # mkt02c spliced pre-2016 history into fred.sp500, so the 2000-03-10 anchor
    # may now land ON a real observation instead of floating. The reference
    # point is the as-of observation when one exists, else the first one.
    sp_asof = await _as_of_sql(reader, "fred.sp500", date(2000, 3, 10))
    sp_ref = (sp_asof["obs_date"], sp_asof["value"]) if sp_asof else sp_first
    sp_end = date(2000, 3, 10) if sp_asof else sp_first[0]
    (_nd_first, nd_latest) = await _first_last(reader, "fred.nasdaq100")
    nd_anchor = await _as_of_sql(reader, "fred.nasdaq100", date(2000, 3, 10))
    (_t_first, t_latest) = await _first_last(reader, "fred.dgs10")
    t_anchor = await _as_of_sql(reader, "fred.dgs10", date(2000, 1, 3))
    t_sd = await reader.fetchval(
        f"SELECT stddev_samp(o.value) FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id "
        "WHERE s.series_key = 'fred.dgs10' AND o.valid_to IS NULL AND o.system_to IS NULL")
    top12 = [r["series_key"] for r in await reader.fetch(
        f"""SELECT s.series_key FROM {SERIES_T} s JOIN {OBS_T} o ON o.series_id = s.id
             WHERE s.ingest_status = 'active' AND o.valid_to IS NULL AND o.system_to IS NULL
             GROUP BY s.series_key ORDER BY count(*) DESC, s.series_key LIMIT 12""")]
    monthly = await reader.fetchval(
        f"SELECT series_key FROM {SERIES_T} WHERE ingest_status = 'active' AND frequency = 'monthly' "
        "ORDER BY sort_order LIMIT 1")
    cands = ["fred.nasdaq100", "yahoo.rut", "fred.dgs10", "fred.dff", "fred.dexjpus", monthly]

    G, S, C = f"{BASE}/grid", f"{BASE}/series", f"{BASE}/correlations"
    plan = [
        ("cat", "GET", f"{BASE}/catalog", None, True),
        ("sp_at_anchor", "POST", G, grid_body(["fred.sp500"], "2000-03-10", sp_end.isoformat(), "index", "monthly"), True),
        ("sp_latest", "POST", G, grid_body(["fred.sp500", "fred.nasdaq100"], "2000-03-10", sp_latest[0].isoformat(),
                                           "index", "monthly"), True),
        ("nd_latest", "POST", G, grid_body(["fred.nasdaq100"], "2000-03-10", nd_latest[0].isoformat(), "index", "monthly"), True),
        ("sigma", "POST", G, grid_body(["fred.dgs10"], "2000-01-03", t_latest[0].isoformat(), "sigma", "monthly"), True),
        ("corr", "POST", C, corr_body("fred.sp500", cands, anchor="2000-01-01", end=None), True),
        ("t_series12", "GET", S, {"keys": ",".join(top12)}, True),
        ("t_grid", "POST", G, grid_body(top12, "1990-01-01", None, "default", "monthly"), True),
    ]
    R = await asyncio.get_running_loop().run_in_executor(None, run_http, plan)

    cat = R["cat"].body or {}
    secs = {s["id"]: s for s in cat.get("securities", [])}
    check("B1 catalog active count equals the live active count, and every link (the three baseline + runtime) is present",
          R["cat"].status == 200 and len(cat.get("indicators", [])) == live_active
          and all(LINKS.get(i) == k for i, k in BASELINE_LINKS.items())
          and all(secs.get(i, {}).get("series_key") == k and secs[i]["selectable"] for i, k in LINKS.items()),
          "the live menu matches the registry", f"indicators={len(cat.get('indicators', []))} active={live_active}")

    a = R["sp_at_anchor"]
    meta = col(a, "fred.sp500")
    newest = (a.body or {}).get("rows", [{}])[0]
    if meta.get("floating"):
        find(f"fred.sp500 starts {sp_first[0]} (FRED carries only ~10 years of S&P 500), so anchor 2000-03-10 "
             f"FLOATS: v0 is the first observation, {sp_first[1]}. The 'value at the anchor' is checked at "
             "that first observation.")
    check("B2a fred.sp500 indexed from 2000-03-10 is exactly 100 at its anchor observation",
          a.status == 200 and newest.get("date") == sp_end.isoformat()
          and meta.get("anchor_observation_date") == sp_ref[0].isoformat()
          and newest.get("cells", {}).get("fred.sp500") == "100.000000",
          "the chart's anchor point must be exactly 100", f"status={a.status} newest={newest} meta={meta}")
    b = R["sp_latest"]
    latest_row = (b.body or {}).get("rows", [{}])[0]
    exp_sp = q6(100 * sp_latest[1] / sp_ref[1])
    check("B2b fred.sp500 at the latest date equals 100 * latest / anchor computed in SQL",
          latest_row.get("cells", {}).get("fred.sp500") == exp_sp,
          "the server's index is the same arithmetic as the database's own numbers",
          f"api={latest_row.get('cells', {}).get('fred.sp500')} sql={exp_sp}")
    nd = R["nd_latest"]
    ndm = col(nd, "fred.nasdaq100")
    exp_nd = q6(100 * nd_latest[1] / nd_anchor["value"])
    check("B2c fred.nasdaq100 (history reaches 2000) anchors ON 2000-03-10, not floating, and matches SQL at the latest date",
          ndm.get("floating") is False and ndm.get("anchor_observation_date") == nd_anchor["obs_date"].isoformat()
          and (nd.body or {}).get("rows", [{}])[0].get("cells", {}).get("fred.nasdaq100") == exp_nd,
          "proves the non-floating path on real data, which sp500 cannot", f"meta={ndm} exp={exp_nd}")
    sg = R["sigma"]
    exp_sg = q6((t_latest[1] - t_anchor["value"]) / t_sd)
    got_sg = (sg.body or {}).get("rows", [{}])[0].get("cells", {}).get("fred.dgs10")
    check("B3 fred.dgs10 in sigma mode matches SQL stddev_samp", got_sg == exp_sg,
          "the live sigma scale is the full-history sample deviation", f"api={got_sg} sql={exp_sg} sd={t_sd}")
    cr = R["corr"]
    res = (cr.body or {}).get("results", [])
    ok = cr.status == 200 and len(res) >= 5 and all(
        (r["r"] is not None and -1 <= float(r["r"]) <= 1 and r["n"] >= 24) or (r["r"] is None and r["unavailable_reason"])
        for r in res)
    check("B4 correlations, focus fred.sp500, against >= 5 real indicators: r in [-1, 1] with n >= 24, or a reason",
          ok, "every live answer is either a valid coefficient or an explicit refusal",
          json.dumps([(r["series_key"], r["r"], r["n"], r["frequency"], r["unavailable_reason"]) for r in res]))
    nq = result(cr, "fred.nasdaq100")
    find(f"sp500 vs nasdaq100 correlation of daily log changes = {nq.get('r')} (n={nq.get('n')}, "
         f"{nq.get('overlap_from')}..{nq.get('overlap_to')}); expected high.")
    find(f"timings: 12-series full-history series request {R['t_series12'].elapsed:.2f}s "
         f"(status {R['t_series12'].status}, {R['t_series12'].size:,} bytes); grid (12 series, monthly, default, "
         f"1990→today) {R['t_grid'].elapsed:.2f}s (status {R['t_grid'].status}); correlations (6 candidates) "
         f"{cr.elapsed:.2f}s.")
    big = max(R.items(), key=lambda kv: kv[1].size)
    find(f"largest response: {big[0]} = {big[1].size:,} bytes.")


# ═══════════════════════════════════════════════════════════════════════════
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
        await phase_a(conn, reader, dsn)
        if args.live:
            await phase_b(reader)
        else:
            skip("Phase B (live data) was not run — pass --live")
    finally:
        await conn.close()
        await reader.close()
    print(f"TOTAL: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
