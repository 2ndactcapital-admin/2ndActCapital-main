#!/usr/bin/env python3
"""verify_mkt03b — key dates, regimes, personal dates and saved views.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03b.py
    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03b.py --live

PHASE A (always) is self-contained. It creates two fixture ORGANIZATIONS and
four fixture USERS (fixed ids below, auth0_sub 'verify.mkt03b.<hex>'), one
fixture SERIES 'verify.mkt03b.stale', and fixture reference rows whose slug /
name starts with 'verify.mkt03b.'. Loader tests call
``reference_seed.load_reference`` with fixture ROWS, never the seed files.
HTTP goes through the real ASGI app (httpx.ASGITransport, ``main.verify_token``
replaced), so the JWT middleware, the RLS context middleware, the active-account
gate, ensure_user and the routes all run as in production. Direct SQL runs on
DATABASE_URL — the app_service role, whose rolbypassrls = false is asserted as
its own check before any isolation proof.

PHASE B (--live) reads the real reference data. Run the loader first:
    doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_seed_reference.py load

Hydrates secrets from Doppler over HTTPS (scripts/_doppler_env.py), like every
market data verify. Does NOT chain any other verify.

Output: [PASS] / [FAIL] / [FIND] / [SKIP] lines, then
``TOTAL: <passed> passed, <failed> failed``. Exit 1 on any [FAIL]; exit 2 if
teardown leaves a fixture row behind.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import calendar
import copy
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(API_DIR))      # apps/api, for services.* and main
sys.path.insert(0, str(HERE.parent))  # scripts/, for _doppler_env

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402  (gate proofs + loader only)
from services.market_data import key_dates as kd  # noqa: E402
from services.market_data import reference_seed as rs  # noqa: E402
from services.market_data import saved_views as sv  # noqa: E402
from services.market_data.read_service import MarketDataRequestError  # noqa: E402

PREFIX = "verify.mkt03b."
ORG_A = UUID("99000000-0000-0000-0000-00000003b0a1")
ORG_B = UUID("99000000-0000-0000-0000-00000003b0b1")
UA1 = UUID("99000000-0000-0000-0000-00000003b1a1")  # org A: messages, own rows
UA2 = UUID("99000000-0000-0000-0000-00000003b1a2")  # org A: the "other user", never saves a date
UA3 = UUID("99000000-0000-0000-0000-00000003b1a3")  # org A: the 50-row limits
UB1 = UUID("99000000-0000-0000-0000-00000003b1b1")  # org B
ORGS = [ORG_A, ORG_B]
USERS = [UA1, UA2, UA3, UB1]
ORG_OF = {UA1: ORG_A, UA2: ORG_A, UA3: ORG_A, UB1: ORG_B}
STALE_KEY = PREFIX + "stale"
SENTINEL = f"SENTINEL{uuid.uuid4().hex[:16]}"
SENTINEL_UUID = str(uuid.uuid4())
HEADERS = {"Authorization": "Bearer verify-token"}
BASE = "/api/v1/market"
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

SERIES_T = "market_data.indicator_series"
OBS_T = "market_data.indicator_observations"
RUNS_T = "market_data.indicator_ingest_runs"
FIXTURE_SERIES = f"(SELECT id FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%')"

PERSONAL_MODULES = [
    API_DIR / "services/market_data/key_dates.py",
    API_DIR / "services/market_data/saved_views.py",
    API_DIR / "services/market_data/personal.py",
    API_DIR / "routers/market_data_personal.py",
]
LOADER_MODULE = API_DIR / "services/market_data/reference_seed.py"

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


def sub_of(uid: UUID) -> str:
    return f"{PREFIX}{uid.hex}"


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers — every statement runs in its own transaction with an explicit
# RLS context (the same three GUCs services.database sets per request)
# ═══════════════════════════════════════════════════════════════════════════
class _Rollback(Exception):
    pass


async def set_ctx(conn, org=None, sup=False) -> None:
    await conn.execute(
        "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', $2, true), "
        "set_config('app.current_auth0_sub', '', true)",
        str(org) if org else "", "true" if sup else "false")


async def q(conn, method: str, sql: str, *args, org=None, sup=False):
    async with conn.transaction():
        await set_ctx(conn, org, sup)
        return await getattr(conn, method)(sql, *args)


async def sval(conn, sql, *a):
    return await q(conn, "fetchval", sql, *a, sup=True)


async def sfetch(conn, sql, *a):
    return await q(conn, "fetch", sql, *a, sup=True)


async def srow(conn, sql, *a):
    return await q(conn, "fetchrow", sql, *a, sup=True)


async def sexec(conn, sql, *a):
    return await q(conn, "execute", sql, *a, sup=True)


async def attempt(conn, statements, *, org=None, sup=False, scope=False):
    """Run ``statements`` [(sql, args), ...] in ONE transaction that is ALWAYS
    rolled back. Returns ('ok', last result) or (exception class name, None).
    ``scope=True`` uses the real services.database.platform_scope()."""
    try:
        if scope:
            async with platform_scope(conn):
                res = None
                for sql, args in statements:
                    res = await conn.execute(sql, *args)
                raise _Rollback(res)
        async with conn.transaction():
            await set_ctx(conn, org, sup)
            res = None
            for sql, args in statements:
                res = await conn.execute(sql, *args)
            raise _Rollback(res)
    except _Rollback as r:
        return "ok", r.args[0]
    except asyncpg.PostgresError as exc:
        return type(exc).__name__, None


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
FX_KEY_DATES = [
    {"slug": PREFIX + "kd-b", "name": "verify kd b", "kind": "equity_crash", "start_date": "1995-06-15",
     "start_precision": "day", "end_date": None, "end_precision": None, "source": "owner_list"},
    {"slug": PREFIX + "kd-a", "name": "verify kd a", "kind": "rates_monetary", "start_date": "1993-03-01",
     "start_precision": "month", "end_date": "1993-09-30", "end_precision": "day", "source": "suggested"},
    {"slug": PREFIX + "kd-c", "name": "verify kd c", "kind": "policy_regime", "start_date": "1994-01-05",
     "start_precision": "day", "end_date": None, "end_precision": None, "source": "owner_list"},
]
FX_REGIMES = [
    {"regime_type": "recession", "name": PREFIX + "r1", "start_date": "1901-01-01", "end_date": "1901-06-30",
     "source": "nber"},
    {"regime_type": "fed_tightening", "name": PREFIX + "r2", "start_date": "1902-01-01", "end_date": "1902-12-31",
     "source": "fomc_curated"},
]
OVERLAYS = {"events": True, "band": True, "emphasis": True}


def preset_cfg(keys):
    return {"v": 1, "selection": [{"kind": "indicator", "key": k} for k in keys],
            "anchor": {"type": "date", "value": "2010-01-01"}, "end": None, "mode": "index", "scale": "log",
            "overlays": dict(OVERLAYS)}


FX_PRESET_OK = {"name": PREFIX + "preset-ok", "config": preset_cfg(["fred.sp500", "fred.dgs10"])}
FX_PRESET_BAD = {"name": PREFIX + "preset-bad", "config": preset_cfg(["fred.sp500", PREFIX + "missing-key"])}


async def insert_fixtures(conn) -> None:
    async with conn.transaction():
        await set_ctx(conn, sup=True)
        for org, tag in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO public.organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"verify mkt03b org {tag}", f"verify-mkt03b-{tag}")
        for uid in USERS:
            s = sub_of(uid)
            await conn.execute(
                """INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, 'member', true)""",
                uid, ORG_OF[uid], f"{s}@test.local", s, s)
        sid = await conn.fetchval(
            f"""INSERT INTO {SERIES_T} (series_key, name, category, region, frequency, default_transform,
                    cost_tier, license_class, source_provider, source_code, notes, sort_order, ingest_status)
                VALUES ($1, 'verify mkt03b stale', 'Verify mkt03b', 'US', 'monthly', 'level', 'free',
                        'public_domain', 'verifyfake', 'VMKT03BSTALE', 'mkt03b verify fixture', -3500, 'active')
                RETURNING id""", STALE_KEY)
        await conn.execute(
            f"""INSERT INTO {OBS_T} (series_id, obs_date, value)
                SELECT $1, d, v FROM unnest($2::date[], $3::numeric[]) AS t(d, v)""",
            sid, [date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1)], [1, 2, 3])


# ═══════════════════════════════════════════════════════════════════════════
# Counting + teardown
# ═══════════════════════════════════════════════════════════════════════════
ORG_ARR = "ARRAY['99000000-0000-0000-0000-00000003b0a1','99000000-0000-0000-0000-00000003b0b1']::uuid[]"
USER_ARR = ("ARRAY['99000000-0000-0000-0000-00000003b1a1','99000000-0000-0000-0000-00000003b1a2',"
            "'99000000-0000-0000-0000-00000003b1a3','99000000-0000-0000-0000-00000003b1b1']::uuid[]")
FIXTURE_WHERE = {
    "market_data.user_key_dates": f"org_id = ANY({ORG_ARR}) OR user_id = ANY({USER_ARR})",
    "market_data.saved_views": (f"org_id = ANY({ORG_ARR}) OR user_id = ANY({USER_ARR}) "
                                f"OR (owner_scope = 'platform' AND name LIKE '{PREFIX}%')"),
    "market_data.key_dates": f"slug LIKE '{PREFIX}%'",
    "market_data.regimes": f"name LIKE '{PREFIX}%'",
    "public.users": f"id = ANY({USER_ARR}) OR auth0_sub LIKE '{PREFIX}%'",
    "public.organizations": f"id = ANY({ORG_ARR})",
    OBS_T: f"series_id IN {FIXTURE_SERIES}",
    RUNS_T: f"series_id IN {FIXTURE_SERIES}",
    SERIES_T: f"series_key LIKE '{PREFIX}%'",
}
NON_FIXTURE_COUNTED = list(FIXTURE_WHERE) + [
    "portfolio.securities_global", "portfolio.securities_global_prices", "public.fx_rates"]
TEARDOWN_ORDER = [
    "market_data.user_key_dates", "market_data.saved_views", "market_data.key_dates", "market_data.regimes",
    "public.users", "public.organizations", OBS_T, RUNS_T, SERIES_T,
]


async def fixture_counts(conn) -> dict:
    return {t: await sval(conn, f"SELECT count(*) FROM {t} WHERE {w}") for t, w in FIXTURE_WHERE.items()}


async def real_counts(conn) -> dict:
    out = {}
    for t in NON_FIXTURE_COUNTED:
        w = FIXTURE_WHERE.get(t)
        # coalesce: a NULL in a tag column (auth0_sub, org_id, series_id) makes the
        # predicate NULL, and NOT NULL would silently drop that real row from the count.
        sql = f"SELECT count(*) FROM {t}" + (f" WHERE NOT coalesce(({w}), false)" if w else "")
        out[t] = await sval(conn, sql)
    return out


async def fk_edges(conn) -> list[tuple[str, str]]:
    """(child, parent) among the tables this script deletes from, read from
    information_schema — the teardown order is checked against these."""
    rows = await conn.fetch("""
        SELECT DISTINCT ch.table_schema || '.' || ch.table_name AS child,
               pa.table_schema || '.' || pa.table_name AS parent
          FROM information_schema.referential_constraints rc
          JOIN information_schema.table_constraints ch
            ON ch.constraint_schema = rc.constraint_schema AND ch.constraint_name = rc.constraint_name
          JOIN information_schema.table_constraints pa
            ON pa.constraint_schema = rc.unique_constraint_schema
           AND pa.constraint_name = rc.unique_constraint_name""")
    tables = set(TEARDOWN_ORDER)
    return [(r["child"], r["parent"]) for r in rows
            if r["child"] in tables and r["parent"] in tables and r["child"] != r["parent"]]


async def delete_fixtures(conn) -> dict:
    """By fixture tag only — never a TRUNCATE — in TEARDOWN_ORDER."""
    deleted = {}
    async with conn.transaction():
        await set_ctx(conn, sup=True)
        for t in TEARDOWN_ORDER:
            status = await conn.execute(f"DELETE FROM {t} WHERE {FIXTURE_WHERE[t]}")
            deleted[t] = int(status.split()[-1])
    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# HTTP through the real app (httpx.ASGITransport, on THIS event loop)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class Resp:
    status: int
    text: str
    body: object

    @property
    def message(self):
        d = self.body.get("detail") if isinstance(self.body, dict) else None
        return d.get("message") if isinstance(d, dict) else None

    @property
    def detail(self) -> dict:
        d = self.body.get("detail") if isinstance(self.body, dict) else None
        return d if isinstance(d, dict) else {}


async def api(uid, method, path, body=None, raw=None) -> Resp:
    import httpx
    import main

    headers = {}
    if uid is not None:
        s, org = sub_of(uid), str(ORG_OF[uid])
        main.verify_token = lambda _t: {"sub": s, "email": f"{s}@test.local", "org_id": org}
        headers = dict(HEADERS)
    kw = {"headers": headers}
    if raw is not None:
        kw["content"] = raw
        headers["Content-Type"] = "application/json"
    elif body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=120.0) as client:
        res = await client.request(method, BASE + path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = None
    return Resp(res.status_code, res.text, parsed)


def no_echo(resp: Resp, *needles: str) -> bool:
    return not any(n in resp.text for n in needles)


# ═══════════════════════════════════════════════════════════════════════════
# Independent expectations (do not import the service's own formatting)
# ═══════════════════════════════════════════════════════════════════════════
async def sql_range(conn) -> tuple[date, date]:
    row = await sfetch(conn, f"""
        SELECT min(o.obs_date) AS f, max(o.obs_date) AS l FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id
         WHERE s.ingest_status = 'active' AND o.valid_to IS NULL AND o.system_to IS NULL""")
    return row[0]["f"], row[0]["l"]


def outside_text(first: date, last: date) -> str:
    return (f"That date is outside the available data ({MON[first.month - 1]} {first.year} to "
            f"{MON[last.month - 1]} {last.year}).")


def view_cfg(selection, anchor=None, end=None, mode="index", scale="log"):
    return {"v": 1, "selection": selection, "anchor": anchor or {"type": "date", "value": "2010-01-01"},
            "end": end, "mode": mode, "scale": scale, "overlays": dict(OVERLAYS)}


def ind(k):
    return {"kind": "indicator", "key": k}


def sec(k):
    return {"kind": "security", "key": k}


# ═══════════════════════════════════════════════════════════════════════════
# Phase A sections
# ═══════════════════════════════════════════════════════════════════════════
async def a_role(conn) -> bool:
    section("Non-bypassing connection")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles "
                              "WHERE rolname = current_user) AS bypass")
    return check("G0 the test connection's role has rolbypassrls = false",
                 who["bypass"] is False,
                 "every RLS gate and isolation proof below is void on a bypass role", f"role={who['u']} bypass={who['bypass']}")


async def a_schema(conn) -> None:
    section("Schema behaviour — every CHECK refuses what it should and admits what it should")
    KD = ("INSERT INTO market_data.key_dates (slug, name, kind, start_date, start_precision, end_date, end_precision, "
          "source) VALUES ($1, 'x', $2, $3, $4, $5, $6, 'owner_list')")
    s = PREFIX + "schema"
    cases = [
        ("S1 key_dates: month precision on the 15th is refused; on the 1st admitted",
         [(KD, [s, "equity_crash", date(2000, 5, 15), "month", None, None])], "CheckViolationError",
         [(KD, [s, "equity_crash", date(2000, 5, 1), "month", None, None])],
         "a month-precision date that is not the 1st would plot at a false exact day"),
        ("S2 key_dates: end before start is refused; end after start admitted",
         [(KD, [s, "equity_crash", date(2000, 5, 2), "day", date(2000, 5, 1), "day"])], "CheckViolationError",
         [(KD, [s, "equity_crash", date(2000, 5, 2), "day", date(2000, 5, 3), "day"])],
         "a negative-length span cannot be drawn"),
        ("S3 key_dates: end_date without end_precision is refused; both set admitted",
         [(KD, [s, "equity_crash", date(2000, 5, 2), "day", date(2000, 6, 1), None])], "CheckViolationError",
         [(KD, [s, "equity_crash", date(2000, 5, 2), "day", date(2000, 6, 1), "month"])],
         "the UI cannot label an end it does not know the precision of"),
        ("S4 key_dates: an unknown kind is refused; a listed kind admitted",
         [(KD, [s, "alien_kind", date(2000, 5, 2), "day", None, None])], "CheckViolationError",
         [(KD, [s, "policy_regime", date(2000, 5, 2), "day", None, None])],
         "kinds are the closed vocabulary the server labels"),
    ]
    RG = "INSERT INTO market_data.regimes (regime_type, name, start_date, end_date, source) VALUES ($1, $2, $3, $4, $5)"
    cases += [
        ("S5 regimes: end before start is refused; a valid span admitted",
         [(RG, ["recession", s, date(1903, 5, 2), date(1903, 5, 1), "nber"])], "CheckViolationError",
         [(RG, ["recession", s, date(1903, 5, 2), date(1903, 6, 1), "nber"])],
         "a negative-length shaded band is meaningless"),
        ("S6 regimes: a duplicate (regime_type, start_date) is refused; the same start under the other type admitted",
         [(RG, ["recession", s, date(1903, 5, 2), date(1903, 6, 1), "nber"]),
          (RG, ["recession", s + "2", date(1903, 5, 2), date(1903, 7, 1), "nber"])], "UniqueViolationError",
         [(RG, ["recession", s, date(1903, 5, 2), date(1903, 6, 1), "nber"]),
          (RG, ["fed_tightening", s + "2", date(1903, 5, 2), date(1903, 7, 1), "fomc_curated"])],
         "the loader's upsert key must be a real constraint, scoped by type"),
    ]
    UK = "INSERT INTO market_data.user_key_dates (org_id, user_id, name, event_date) VALUES ($1, $2, $3, $4)"
    d0 = date(2001, 1, 1)
    cases += [
        ("S7 user_key_dates: an untrimmed name is refused; the trimmed name admitted",
         [(UK, [ORG_A, UA1, " padded ", d0])], "CheckViolationError", [(UK, [ORG_A, UA1, "padded", d0])],
         "the database itself holds the trimmed-name rule, not only the API"),
        ("S8 user_key_dates: an empty name is refused; one character admitted",
         [(UK, [ORG_A, UA1, "", d0])], "CheckViolationError", [(UK, [ORG_A, UA1, "x", d0])],
         "a blank label would render as an unlabeled line"),
        ("S9 user_key_dates: 61 characters are refused; 60 admitted",
         [(UK, [ORG_A, UA1, "y" * 61, d0])], "CheckViolationError", [(UK, [ORG_A, UA1, "y" * 60, d0])],
         "the 60-character limit is enforced at the boundary, both sides"),
        ("S10 user_key_dates: a duplicate differing only in letter case is refused; another user may use it",
         [(UK, [ORG_A, UA1, "Same Name", d0]), (UK, [ORG_A, UA1, "same name", d0])], "UniqueViolationError",
         [(UK, [ORG_A, UA1, "Same Name", d0]), (UK, [ORG_A, UA2, "same name", d0])],
         "the unique index is per user and case-insensitive"),
    ]
    VW = ("INSERT INTO market_data.saved_views (owner_scope, org_id, user_id, name, config) "
          "VALUES ($1, $2, $3, $4, $5::jsonb)")
    ok_cfg = json.dumps(preset_cfg(["fred.sp500"]))
    big_cfg = json.dumps({"pad": "x" * 20_001})
    cases += [
        ("S11 saved_views: a platform row with an org_id is refused; a platform row with none admitted",
         [(VW, ["platform", ORG_A, None, s, ok_cfg])], "CheckViolationError",
         [(VW, ["platform", None, None, s, ok_cfg])],
         "a preset tied to one org would leak or vanish depending on who reads it"),
        ("S12 saved_views: a user row without a user_id is refused; with one admitted",
         [(VW, ["user", ORG_A, None, s, ok_cfg])], "CheckViolationError", [(VW, ["user", ORG_A, UA1, s, ok_cfg])],
         "a user view with no owner could never be listed or deleted by anyone"),
        ("S13 saved_views: an 81-character name is refused; 80 admitted",
         [(VW, ["user", ORG_A, UA1, "n" * 81, ok_cfg])], "CheckViolationError",
         [(VW, ["user", ORG_A, UA1, "n" * 80, ok_cfg])], "the 80-character limit holds at the boundary"),
        ("S14 saved_views: a config that is not a JSON object is refused; an object admitted",
         [(VW, ["user", ORG_A, UA1, s, "[1, 2]"])], "CheckViolationError", [(VW, ["user", ORG_A, UA1, s, ok_cfg])],
         "the UI reads config as an object"),
        ("S15 saved_views: a config over 20,000 bytes is refused; a small one admitted",
         [(VW, ["user", ORG_A, UA1, s, big_cfg])], "CheckViolationError", [(VW, ["user", ORG_A, UA1, s, ok_cfg])],
         "bounds what one member can store per view"),
        ("S16 saved_views: a case-insensitive duplicate name for one user is refused; another user admitted",
         [(VW, ["user", ORG_A, UA1, "My View", ok_cfg]), (VW, ["user", ORG_A, UA1, "MY VIEW", ok_cfg])],
         "UniqueViolationError",
         [(VW, ["user", ORG_A, UA1, "My View", ok_cfg]), (VW, ["user", ORG_A, UA2, "MY VIEW", ok_cfg])],
         "names are unique per user, case-insensitively, and only per user"),
        ("S17 saved_views: a case-insensitive duplicate platform name is refused; distinct names admitted",
         [(VW, ["platform", None, None, s + "P", ok_cfg]), (VW, ["platform", None, None, s + "p", ok_cfg])],
         "UniqueViolationError",
         [(VW, ["platform", None, None, s + "P", ok_cfg]), (VW, ["platform", None, None, s + "Q", ok_cfg])],
         "the loader upserts presets on lower(name); the index must agree"),
    ]
    for name, bad, bad_exc, good, why in cases:
        r_bad, _ = await attempt(conn, bad, sup=True)
        r_good, _ = await attempt(conn, good, sup=True)
        check(name, r_bad == bad_exc and r_good == "ok", why, f"refused={r_bad} admitted={r_good}")


async def a_gates(conn) -> None:
    section("RLS gates — the IDENTICAL statement, both ways, on the non-bypassing connection")
    KD = [("INSERT INTO market_data.key_dates (slug, name, kind, start_date, source) "
           "VALUES ($1, 'gate', 'equity_crash', '2000-01-03', 'owner_list')", [PREFIX + "gate"])]
    RG = [("INSERT INTO market_data.regimes (regime_type, name, start_date, end_date, source) "
           "VALUES ('recession', $1, '1904-01-01', '1904-02-01', 'nber')", [PREFIX + "gate"])]
    VW = [("INSERT INTO market_data.saved_views (owner_scope, name, config) VALUES ('platform', $1, $2::jsonb)",
           [PREFIX + "gate", json.dumps(preset_cfg(["fred.sp500"]))])]
    for label, stmts, why in (
        ("G1 key_dates insert", KD, "reference data must be writable only by the platform loader"),
        ("G2 regimes insert", RG, "reference data must be writable only by the platform loader"),
        ("G3 platform preset insert", VW, "an org member must never be able to create a preset every org sees"),
    ):
        refused, _ = await attempt(conn, stmts, org=ORG_A, sup=False)
        admitted, _ = await attempt(conn, stmts, scope=True)
        check(f"{label}: refused under an org context, admitted under platform_scope()",
              refused == "InsufficientPrivilegeError" and admitted == "ok", why,
              f"org={refused} platform={admitted}")

    pid = await sval(conn, "SELECT id FROM market_data.saved_views WHERE owner_scope = 'platform' AND name = $1",
                     FX_PRESET_OK["name"])
    before = await srow(conn, "SELECT name, config::text AS c, updated_at FROM market_data.saved_views WHERE id = $1", pid)
    up = await q(conn, "execute", "UPDATE market_data.saved_views SET name = 'hijacked' WHERE id = $1", pid, org=ORG_A)
    de = await q(conn, "execute", "DELETE FROM market_data.saved_views WHERE id = $1", pid, org=ORG_A)
    after = await srow(conn, "SELECT name, config::text AS c, updated_at FROM market_data.saved_views WHERE id = $1", pid)
    check("G4 an org context cannot update or delete a platform preset: zero rows each, re-read unchanged",
          pid is not None and up == "UPDATE 0" and de == "DELETE 0" and before is not None and dict(before) == dict(after or {}),
          "presets are seed-only; RLS is the backstop if the API filter were ever dropped",
          f"update={up} delete={de} unchanged={before is not None and dict(before) == dict(after or {})}")


async def a_org_isolation(conn) -> None:
    section("Org isolation — both directions, both personal tables")
    ids = {}
    cfg = json.dumps(preset_cfg(["fred.sp500"]))
    for org, uid in ((ORG_A, UA1), (ORG_B, UB1)):
        ids[(org, "d")] = await q(conn, "fetchval",
                                  "INSERT INTO market_data.user_key_dates (org_id, user_id, name, event_date) "
                                  "VALUES ($1, $2, 'iso date', '2005-05-05') RETURNING id", org, uid, org=org)
        ids[(org, "v")] = await q(conn, "fetchval",
                                  "INSERT INTO market_data.saved_views (owner_scope, org_id, user_id, name, config) "
                                  "VALUES ('user', $1, $2, 'iso view', $3::jsonb) RETURNING id", org, uid, cfg, org=org)
    for me, other, other_user in ((ORG_A, ORG_B, UB1), (ORG_B, ORG_A, UA1)):
        tag = "A→B" if me == ORG_A else "B→A"
        for table, key in (("user_key_dates", "d"), ("saved_views", "v")):
            rid = ids[(other, key)]
            before = await srow(conn, f"SELECT to_jsonb(t)::text AS j FROM market_data.{table} t WHERE id = $1", rid)
            seen = await q(conn, "fetchval", f"SELECT count(*) FROM market_data.{table} WHERE id = $1", rid, org=me)
            if table == "user_key_dates":
                ins = [("INSERT INTO market_data.user_key_dates (org_id, user_id, name, event_date) "
                        "VALUES ($1, $2, 'smuggled', '2005-05-06')", [other, other_user])]
            else:
                ins = [("INSERT INTO market_data.saved_views (owner_scope, org_id, user_id, name, config) "
                        "VALUES ('user', $1, $2, 'smuggled', $3::jsonb)", [other, other_user, cfg])]
            ins_res, _ = await attempt(conn, ins, org=me)
            up = await q(conn, "execute", f"UPDATE market_data.{table} SET name = 'stolen' WHERE id = $1", rid, org=me)
            de = await q(conn, "execute", f"DELETE FROM market_data.{table} WHERE id = $1", rid, org=me)
            after = await srow(conn, f"SELECT to_jsonb(t)::text AS j FROM market_data.{table} t WHERE id = $1", rid)
            check(f"O {tag} {table}: cannot read, insert on behalf of, update or delete the other org's row",
                  seen == 0 and ins_res == "InsufficientPrivilegeError" and up == "UPDATE 0" and de == "DELETE 0"
                  and before is not None and after is not None and before["j"] == after["j"],
                  "org isolation is enforced by the database, not only by the service filter; a mismatched "
                  "org_id insert is refused by the RLS check",
                  f"read={seen} insert={ins_res} update={up} delete={de}")


async def a_loader(conn) -> None:
    section("Loader — fixture rows through reference_seed.load_reference")
    res = await rs.load_reference(conn, key_dates=FX_KEY_DATES, regimes=FX_REGIMES,
                                  views=[FX_PRESET_OK, FX_PRESET_BAD])
    c = res.counts
    loaded_ok = await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE owner_scope = 'platform' AND name = $1",
                           FX_PRESET_OK["name"])
    loaded_bad = await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE owner_scope = 'platform' AND name = $1",
                            FX_PRESET_BAD["name"])
    check("L1 first call inserts every fixture row: 3 key dates, 2 regimes, 1 preset",
          c["key_dates"]["inserted"] == 3 and c["regimes"]["inserted"] == 2 and c["saved_views"]["inserted"] == 1,
          "the loader's insert path writes what it is given", f"counts={c}")
    bad_find = [f for f in res.finds if PREFIX + "missing-key" in f]
    check("L2 a preset with an unresolvable key is SKIPPED with a [FIND] naming the key, while the other preset in "
          "the same call still loads",
          len(bad_find) == 1 and c["saved_views"]["skipped"] == 1 and loaded_ok == 1 and loaded_bad == 0,
          "one bad preset must neither block the seed nor land a view that can never render",
          f"finds={res.finds} ok={loaded_ok} bad={loaded_bad}")
    for f in res.finds:
        find(f"(loader fixture) {f}")

    snap_sql = (f"SELECT 'kd:' || slug AS k, updated_at FROM market_data.key_dates WHERE slug LIKE '{PREFIX}%' "
                f"UNION ALL SELECT 'rg:' || name, updated_at FROM market_data.regimes WHERE name LIKE '{PREFIX}%' "
                f"UNION ALL SELECT 'vw:' || name, updated_at FROM market_data.saved_views "
                f"WHERE owner_scope = 'platform' AND name LIKE '{PREFIX}%'")
    snap1 = {r["k"]: r["updated_at"] for r in await sfetch(conn, snap_sql)}
    res2 = await rs.load_reference(conn, key_dates=FX_KEY_DATES, regimes=FX_REGIMES, views=[FX_PRESET_OK])
    snap2 = {r["k"]: r["updated_at"] for r in await sfetch(conn, snap_sql)}
    c2 = res2.counts
    check("L3 a second identical call writes nothing and leaves every updated_at untouched",
          all(c2[t]["inserted"] == 0 and c2[t]["updated"] == 0 for t in rs.TABLES)
          and c2["key_dates"]["unchanged"] == 3 and c2["regimes"]["unchanged"] == 2
          and c2["saved_views"]["unchanged"] == 1 and snap1 == snap2 and len(snap1) == 6,
          "a re-run must be a true no-op, or updated_at stops meaning anything", f"counts={c2}")

    changed = copy.deepcopy(FX_KEY_DATES)
    changed[2]["name"] = "verify kd c renamed"
    res3 = await rs.load_reference(conn, key_dates=changed, regimes=FX_REGIMES, views=[FX_PRESET_OK])
    snap3 = {r["k"]: r["updated_at"] for r in await sfetch(conn, snap_sql)}
    moved = sorted(k for k in snap3 if snap3[k] != snap2.get(k))
    name_c = await sval(conn, "SELECT name FROM market_data.key_dates WHERE slug = $1", PREFIX + "kd-c")
    check("L4 a changed value updates only that row (and only its updated_at moves)",
          res3.counts["key_dates"]["updated"] == 1 and res3.counts["key_dates"]["unchanged"] == 2
          and moved == ["kd:" + PREFIX + "kd-c"] and name_c == "verify kd c renamed",
          "a correction to one key date must not touch its neighbours", f"moved={moved}")

    await sexec(conn, "UPDATE market_data.key_dates SET is_active = false, notes = 'keep me' WHERE slug = $1",
                PREFIX + "kd-a")
    changed[1]["name"] = "verify kd a renamed"
    await rs.load_reference(conn, key_dates=changed, regimes=FX_REGIMES, views=[FX_PRESET_OK])
    row_a = await srow(conn, "SELECT name, is_active, notes FROM market_data.key_dates WHERE slug = $1", PREFIX + "kd-a")
    check("L5 is_active and notes on an existing row are never overwritten by the loader",
          row_a is not None and row_a["name"] == "verify kd a renamed" and row_a["is_active"] is False
          and row_a["notes"] == "keep me",
          "an owner's hand-curation (hiding a date, annotating it) must survive every re-seed", f"row={dict(row_a or {})}")

    n_before = await sval(conn, f"SELECT count(*) FROM market_data.key_dates WHERE slug LIKE '{PREFIX}%'")
    await rs.load_reference(conn, key_dates=[changed[0]], regimes=[], views=[])
    n_after = await sval(conn, f"SELECT count(*) FROM market_data.key_dates WHERE slug LIKE '{PREFIX}%'")
    check("L6 a row absent from the input is never deleted",
          n_before == 3 and n_after == 3, "removing a line from a seed file must not silently erase reference data",
          f"before={n_before} after={n_after}")

    dry_same = await rs.load_reference(conn, key_dates=changed, regimes=FX_REGIMES, views=[FX_PRESET_OK], dry_run=True)
    more = copy.deepcopy(changed)
    more[0]["name"] = "dry-run only"
    before_dry = await sval(conn, "SELECT name FROM market_data.key_dates WHERE slug = $1", PREFIX + "kd-b")
    dry_diff = await rs.load_reference(conn, key_dates=more, regimes=FX_REGIMES, views=[FX_PRESET_OK], dry_run=True)
    after_dry = await sval(conn, "SELECT name FROM market_data.key_dates WHERE slug = $1", PREFIX + "kd-b")
    check("L7 dry run classifies without writing: 0/0 on identical rows, 1 updated on a change, database unchanged",
          all(dry_same.counts[t]["inserted"] == 0 and dry_same.counts[t]["updated"] == 0 for t in rs.TABLES)
          and dry_diff.counts["key_dates"]["updated"] == 1 and before_dry == after_dry == "verify kd b",
          "Phase B relies on the dry run to prove the live seed is fully loaded", f"same={dry_same.counts} diff={dry_diff.counts}")


async def a_seed_files(conn) -> None:
    section("Seed files")
    root = REPO_ROOT / "docs" / "market_data"
    kdf = json.loads((root / "market_key_dates_v1.json").read_text(encoding="utf-8"))
    rgf = json.loads((root / "market_regimes_v1.json").read_text(encoding="utf-8"))
    vwf = json.loads((root / "market_view_presets_v1.json").read_text(encoding="utf-8"))
    kds, rgs, vws = kdf.get("key_dates", []), rgf.get("regimes", []), vwf.get("views", [])
    check("F1 each file carries version 1",
          kdf.get("version") == 1 and rgf.get("version") == 1 and vwf.get("version") == 1,
          "the loader refuses an unversioned file; a future v2 must be explicit")
    by_src = {s: sum(1 for r in kds if r["source"] == s) for s in ("owner_list", "suggested")}
    by_type = {t: sum(1 for r in rgs if r["regime_type"] == t) for t in ("recession", "fed_tightening")}
    check("F2 counts: 32 key dates (22 owner_list, 10 suggested), 16 regimes (8 per type), 4 views",
          len(kds) == 32 and by_src == {"owner_list": 22, "suggested": 10} and len(rgs) == 16
          and by_type == {"recession": 8, "fed_tightening": 8} and len(vws) == 4,
          "matches the owner's list exactly", f"kd={len(kds)} {by_src} rg={len(rgs)} {by_type} views={len(vws)}")
    slugs = [r["slug"] for r in kds]
    check("F3 slugs, (regime_type, start_date) pairs and lower(view name) are unique within each file",
          len(set(slugs)) == len(slugs) and len({(r["regime_type"], r["start_date"]) for r in rgs}) == len(rgs)
          and len({v["name"].lower() for v in vws}) == len(vws),
          "a duplicate key would make the upsert silently overwrite a sibling")

    async def eval_checks(table: str, coldefs: str, rows: list[dict], label_col: str) -> list[str]:
        cons = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conrelid = $1::text::regclass AND contype = 'c'", table)
        bad = []
        for c in cons:
            expr = c["d"][len("CHECK "):] if c["d"].startswith("CHECK ") else c["d"]
            failing = await conn.fetch(
                f"SELECT t.{label_col} AS k FROM jsonb_to_recordset($1::jsonb) AS t({coldefs}) "
                f"WHERE NOT coalesce({expr}, true)", json.dumps(rows))
            bad += [f"{c['conname']}:{r['k']}" for r in failing]
        return bad
    bad = await eval_checks("market_data.key_dates",
                            "slug text, name text, kind text, start_date date, start_precision text, end_date date, "
                            "end_precision text, source text", kds, "slug")
    bad += await eval_checks("market_data.regimes",
                             "regime_type text, name text, start_date date, end_date date, source text", rgs, "name")
    bad += await eval_checks("market_data.saved_views",
                             "owner_scope text, org_id uuid, user_id uuid, name text, config jsonb",
                             [{"owner_scope": "platform", "org_id": None, "user_id": None, **v} for v in vws], "name")
    check("F4 every kind, precision, source and config value passes the LIVE CHECK constraints (read from pg_constraint)",
          not bad, "the loader would otherwise abort mid-seed on the first bad row", f"failing={bad}")
    not_first = [r["slug"] for r in kds if r["start_precision"] == "month" and not r["start_date"].endswith("-01")]
    check("F5 every month-precision start date is the first of its month",
          not not_first and sum(1 for r in kds if r["start_precision"] == "month") == 3,
          "the three month-precision entries are the ones the owner dated by month", f"offenders={not_first}")
    by_slug = {r["slug"]: r for r in kds}
    by_rg = {r["name"]: r for r in rgs}
    by_vw = {v["name"]: v for v in vws}
    spots = (
        by_slug.get("black-monday-1987") == {"slug": "black-monday-1987", "name": "Black Monday", "kind": "equity_crash",
                                             "start_date": "1987-10-19", "start_precision": "day", "end_date": None,
                                             "end_precision": None, "source": "owner_list"},
        by_slug.get("covid-crash-2020") == {"slug": "covid-crash-2020", "name": "COVID crash", "kind": "equity_crash",
                                            "start_date": "2020-02-19", "start_precision": "day",
                                            "end_date": "2020-03-23", "end_precision": "day", "source": "owner_list"},
        by_slug.get("latam-debt-crisis-1982") == {"slug": "latam-debt-crisis-1982", "name": "Latin American debt crisis",
                                                  "kind": "sovereign_currency", "start_date": "1982-08-01",
                                                  "start_precision": "month", "end_date": None, "end_precision": None,
                                                  "source": "owner_list"},
        by_rg.get("US recession 2007-09") == {"regime_type": "recession", "name": "US recession 2007-09",
                                              "start_date": "2007-12-01", "end_date": "2009-06-30", "source": "nber"},
        by_rg.get("Fed tightening 2022-23") == {"regime_type": "fed_tightening", "name": "Fed tightening 2022-23",
                                                "start_date": "2022-03-01", "end_date": "2023-07-31",
                                                "source": "fomc_curated"},
        by_vw.get("Housing cycle") == {"name": "Housing cycle", "config": {
            "v": 1, "selection": [ind("fred.csushpinsa"), ind("fred.houst"), ind("fred.dgs10"), ind("fred.unrate"),
                                  ind("fred.cpiaucsl"), ind("fred.sp500")],
            "anchor": {"type": "date", "value": "2003-01-01"}, "end": None, "mode": "sigma", "scale": "linear",
            "overlays": dict(OVERLAYS)}},
    )
    check("F6 spot checks equal the sprint prompt's data: black-monday-1987, covid-crash-2020, latam-debt-crisis-1982, "
          "one regime of each type, and the Housing cycle preset",
          all(spots), "the files must be the owner's data verbatim, not a re-typed approximation", f"results={spots}")


async def a_least_privilege_static() -> None:
    section("Least privilege (static)")

    def refs(path: Path) -> list[str]:
        out = []
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if any(n.split(".")[-1] == "platform_scope" for n in names):
                out.append(f"{path.name}:{getattr(node, 'lineno', '?')}")
        return out
    offenders = [o for p in PERSONAL_MODULES for o in refs(p)]
    control = refs(LOADER_MODULE)
    check("P1 the key-date, view and router modules never import or reference platform_scope (AST); the loader "
          "module does (positive control)",
          not offenders and bool(control),
          "the per-user endpoints must never widen their own RLS scope; the control proves the scan can see it",
          f"offenders={offenders} control={control}")


async def a_least_privilege_live(conn) -> None:
    section("Least privilege (live connection, no super-admin)")
    async with conn.transaction():
        await set_ctx(conn, org=ORG_A, sup=False)
        sup = await conn.fetchval("SELECT current_setting('app.is_super_admin', true)")
        g = await kd.build_key_dates(conn, ORG_A, UA2)
        made = await kd.create_custom_date(conn, ORG_A, UA2, json.dumps(
            {"name": "lp date", "event_date": "2010-06-01"}).encode())
        gone = await kd.delete_custom_date(conn, ORG_A, UA2, made["id"])
        v = await sv.build_views(conn, ORG_A, UA2)
        mv = await sv.create_view(conn, ORG_A, UA2, json.dumps(
            {"name": "lp view", "config": view_cfg([ind("fred.sp500")])}).encode())
        uv = await sv.update_view(conn, ORG_A, UA2, mv["id"], json.dumps({"name": "lp view 2"}).encode())
        dv = await sv.delete_view(conn, ORG_A, UA2, mv["id"])
    check("P2 every personal service function succeeds on the non-bypassing connection with is_super_admin = 'false'",
          sup == "false" and "custom_dates" in g and gone == {"deleted": True} and "presets" in v
          and uv["name"] == "lp view 2" and dv == {"deleted": True},
          "an ordinary member's session is enough; nothing needs the platform carve-out", f"super={sup}")


async def a_key_dates_http(conn, first: date, last: date) -> None:
    section("Key-date POST — messages, range, duplicates, limit, no echo")
    P = "/key-dates/custom"
    lo = first.replace(day=1)
    hi = last.replace(day=calendar.monthrange(last.year, last.month)[1])
    outside = outside_text(first, last)

    async def n_rows(uid):
        return await sval(conn, "SELECT count(*) FROM market_data.user_key_dates WHERE user_id = $1", uid)

    base = await n_rows(UA1)
    msgs = [
        ("K1a empty name", {"name": "", "event_date": "2010-01-04"}, 422, "Enter a name for this date."),
        ("K1b whitespace-only name", {"name": "   \t ", "event_date": "2010-01-04"}, 422, "Enter a name for this date."),
        ("K1c missing name", {"event_date": "2010-01-04"}, 422, "Enter a name for this date."),
        ("K1d 61-character name", {"name": "z" * 61, "event_date": "2010-01-04"}, 422, "Names can be up to 60 characters."),
        ("K1e control character", {"name": "bell\x07here", "event_date": "2010-01-04"}, 422,
         "Names cannot contain control characters."),
        ("K1f missing date", {"name": "no date"}, 422, "Pick a date."),
        ("K1g malformed date", {"name": "bad date", "event_date": "2010-13-01"}, 422, "Pick a date."),
        ("K1h non-string date", {"name": "bad date", "event_date": 20100104}, 422, "Pick a date."),
        ("K1i the day before the first available month", {"name": "too early", "event_date": (lo - timedelta(days=1)).isoformat()},
         422, outside),
        ("K1j the day after the last available month", {"name": "too late", "event_date": (hi + timedelta(days=1)).isoformat()},
         422, outside),
    ]
    results = []
    for label, body, status, msg in msgs:
        r = await api(UA1, "POST", P, body)
        results.append((label, r.status, r.message))
        check(f"{label} → {status} {msg!r}", r.status == status and r.message == msg,
              "the UI shows this exact server message; the server is the only validator that cannot be bypassed",
              f"got {r.status} {r.message!r}")
    check("K1z none of the refused POSTs stored a row", await n_rows(UA1) == base,
          "a refusal must leave nothing behind", f"before={base} after={await n_rows(UA1)}")

    r_lo = await api(UA1, "POST", P, {"name": "first month", "event_date": lo.isoformat()})
    r_hi = await api(UA1, "POST", P, {"name": "last month", "event_date": hi.isoformat()})
    check("K2 both edges of the available range are admitted (first day of the first month, last day of the last)",
          r_lo.status == 201 and r_hi.status == 201,
          f"the range is read live ({lo} to {hi}); the boundary must be inclusive on both sides",
          f"lo={r_lo.status} hi={r_hi.status}")

    r = await api(UA1, "POST", P, {"name": "   Padded name  ", "event_date": "2011-03-04"})
    stored = await sval(conn, "SELECT name FROM market_data.user_key_dates WHERE user_id = $1 AND event_date = '2011-03-04'",
                        UA1)
    check("K3 the saved row stores the trimmed name (response and an independent re-read)",
          r.status == 201 and (r.body or {}).get("name") == "Padded name" and stored == "Padded name",
          "the table's CHECK requires a trimmed name; the API must trim rather than fail", f"stored={stored!r}")
    n = await n_rows(UA1)
    r = await api(UA1, "POST", P, {"name": "PADDED NAME", "event_date": "2011-03-04"})
    r_other_day = await api(UA1, "POST", P, {"name": "PADDED NAME", "event_date": "2011-03-05"})
    check("K4 the same name in different letter case on the same date → 409 'You already saved that name on that date.'; "
          "on another date it is admitted",
          r.status == 409 and r.message == "You already saved that name on that date." and r_other_day.status == 201
          and await n_rows(UA1) == n + 1,
          "duplicates are per (name, date), case-insensitively", f"dup={r.status} other_day={r_other_day.status}")

    # 51st: 49 direct rows + 1 through the API = 50; then the API must refuse the 51st.
    await q(conn, "execute", """INSERT INTO market_data.user_key_dates (org_id, user_id, name, event_date)
                                SELECT $1, $2, 'verify filler ' || g, DATE '2000-01-01' + g
                                  FROM generate_series(1, 49) g""", ORG_A, UA3, org=ORG_A)
    r50 = await api(UA3, "POST", P, {"name": "fiftieth", "event_date": "2012-01-02"})
    r51 = await api(UA3, "POST", P, {"name": "fifty-first", "event_date": "2012-01-03"})
    check("K5 the 50th date is admitted and the 51st → 422 'You can save up to 50 dates. Delete one first.'",
          r50.status == 201 and r51.status == 422 and r51.message == "You can save up to 50 dates. Delete one first."
          and await n_rows(UA3) == 50,
          "the limit is enforced on the server under a per-user lock", f"50th={r50.status} 51st={r51.status} {r51.message!r}")

    for label, field in (("K6a", "org_id"), ("K6b", "user_id")):
        body = {"name": SENTINEL, "event_date": "2013-01-02", field: SENTINEL_UUID}
        r = await api(UA1, "POST", P, body)
        stored = await sval(conn, "SELECT count(*) FROM market_data.user_key_dates WHERE name = $1", SENTINEL)
        check(f"{label} a body containing {field} → 422, nothing stored, neither the field name nor any value echoed",
              r.status == 422 and stored == 0 and no_echo(r, field, SENTINEL, SENTINEL_UUID),
              "org and user come only from the verified session (Rule 6)", f"status={r.status} stored={stored}")


async def a_key_dates_get(conn, first: date, last: date) -> None:
    section("Key-date GET — order, empty arrays, envelope, gate")
    r = await api(UA2, "GET", "/key-dates")
    b = r.body or {}
    kds = b.get("key_dates", [])
    starts = [k["start_date"] for k in kds]
    fx = [k["slug"] for k in kds if k["slug"].startswith(PREFIX)]
    check("KG1 key dates are sorted chronologically; fixtures appear in date order and the deactivated one is absent",
          r.status == 200 and starts == sorted(starts) and fx == [PREFIX + "kd-c", PREFIX + "kd-b"],
          "the chart draws markers left to right; an inactive date must not be drawn", f"fixtures={fx}")
    rg = b.get("regimes", [])
    check("KG2 regimes are sorted by start date and include the fixture regimes",
          [x["start_date"] for x in rg] == sorted(x["start_date"] for x in rg)
          and {PREFIX + "r1", PREFIX + "r2"} <= {x["name"] for x in rg},
          "shaded bands render in order", f"n={len(rg)}")
    check("KG3 custom_dates is an empty ARRAY (present, not omitted) for a user who has none",
          "custom_dates" in b and b["custom_dates"] == [],
          "a missing key would make the UI fall back to a default — the envelope pattern forbids that")
    perms = b.get("permissions")
    check("KG4 the permissions envelope: can_read and can_write (own rows) true, not super admin, both permission names null",
          perms == {"can_read": True, "can_write": True, "is_super_admin": False, "read_permission": None,
                    "write_permission": None},
          "the UI renders its add/delete controls only inside this envelope", f"perms={perms}")
    voc = b.get("vocabularies", {})
    want_kinds = [
        {"key": "equity_crash", "label": "Equity crash"}, {"key": "market_peak_trough", "label": "Market peak or trough"},
        {"key": "banking_credit", "label": "Banking or credit crisis"},
        {"key": "sovereign_currency", "label": "Sovereign or currency crisis"},
        {"key": "rates_monetary", "label": "Rates and monetary policy"},
        {"key": "geopolitical_trade", "label": "Geopolitical or trade"}, {"key": "policy_regime", "label": "Policy regime"}]
    want_types = [{"key": "recession", "label": "Recession"}, {"key": "fed_tightening", "label": "Fed tightening"}]
    check("KG5 vocabularies carry every kind and regime type with the server's labels, and the limits are 50 / 60",
          voc.get("kinds") == want_kinds and voc.get("regime_types") == want_types
          and b.get("limits") == {"custom_dates_max": 50, "name_max": 60},
          "Rule 1: labels come from the server, never hardcoded in the UI", f"limits={b.get('limits')}")
    check("KG6 data_range equals the earliest first and latest last observation across ACTIVE series (independent SQL)",
          b.get("data_range") == {"first": first.isoformat(), "last": last.isoformat()},
          "the range is read per request, never hardcoded", f"got={b.get('data_range')} sql={first}..{last}")
    own = await api(UA1, "GET", "/key-dates")
    ev = [c["event_date"] for c in (own.body or {}).get("custom_dates", [])]
    check("KG7 the owner's custom dates come back sorted by event_date", own.status == 200 and ev == sorted(ev) and len(ev) >= 4,
          "the 'My dates' list reads in date order", f"n={len(ev)}")

    routes = [("GET", "/key-dates"), ("POST", "/key-dates/custom"), ("DELETE", f"/key-dates/custom/{uuid.uuid4()}"),
              ("GET", "/views"), ("POST", "/views"), ("PUT", f"/views/{uuid.uuid4()}"), ("DELETE", f"/views/{uuid.uuid4()}")]
    anon, authed = [], []
    for m, p in routes:
        payload = {"name": "x"} if m in ("POST", "PUT") else None
        anon.append((await api(None, m, p, payload)).status)
        rr = await api(UA2, m, p, payload)
        # The JWT middleware answers 401 BEFORE routing, so a 401 alone would also
        # "pass" for a path that does not exist. The identical authenticated
        # request must reach a real route: not 401, not 405, and not FastAPI's
        # own routing 404 body ({"detail": "Not Found"}).
        authed.append((rr.status, rr.status not in (401, 405) and rr.body != {"detail": "Not Found"}))
    check("KG8 every one of the seven routes refuses the request with no session (401) and the IDENTICAL request "
          "with a session reaches the real route",
          all(s == 401 for s in anon) and all(ok for _, ok in authed),
          "the gate holds on every route, and the 401s are not a routing miss in disguise",
          f"anon={anon} authed={[s for s, _ in authed]}")
    from fastapi import HTTPException
    from services.market_data import access

    class _Req:
        class state:  # noqa: N801
            user = None
    refused = False
    try:
        await access.require_market_data_read(_Req())
    except HTTPException as exc:
        refused = exc.status_code == 401
    check("KG9 the shared gate function itself refuses a request with no verified session",
          refused, "keeps every route closed even if its path were ever added to PUBLIC_PATHS by mistake")


async def a_views_config(conn, deferred_key: str, note_sec: str, good_sec: str) -> None:
    section("Views — config validation (each variant refused with 422 naming the field or the offending keys only)")
    good = view_cfg([ind("fred.sp500"), ind("fred.dgs10")])

    def with_(f):
        c = copy.deepcopy(good)
        f(c)
        return c

    def loc_is(r, loc):
        return any(e.get("loc") == loc for e in r.detail.get("errors", []))

    n0 = await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE user_id = $1", UA1)
    cases = [
        ("V1a unknown field at the top level", with_(lambda c: c.update({SENTINEL: 1})),
         lambda r: loc_is(r, ["body", "config"])),
        ("V1b unknown field in a selection", with_(lambda c: c["selection"][0].update({SENTINEL: 1})),
         lambda r: loc_is(r, ["body", "config", "selection", 0])),
        ("V1c unknown field in the anchor", with_(lambda c: c["anchor"].update({SENTINEL: 1})),
         lambda r: loc_is(r, ["body", "config", "anchor", "date"])),
        ("V1d unknown field in end", with_(lambda c: c.update(end={"type": "relative", "years": 1, SENTINEL: 1})),
         lambda r: loc_is(r, ["body", "config", "end", "relative"])),
        ("V1e unknown field in overlays", with_(lambda c: c["overlays"].update({SENTINEL: 1})),
         lambda r: loc_is(r, ["body", "config", "overlays"])),
        ("V2 an inactive ('deferred') series key", with_(lambda c: c["selection"].append(ind(deferred_key))),
         lambda r: r.detail.get("inactive_keys") == [deferred_key] and r.detail.get("unknown_keys") == []
         and r.detail.get("unselectable_keys") == [] and "fred.sp500" not in r.text),
        ("V3 an unselectable security (a real note with no price history)",
         with_(lambda c: c["selection"].append(sec(note_sec))),
         lambda r: r.detail.get("unselectable_keys") == [note_sec] and r.detail.get("inactive_keys") == []
         and "fred.sp500" not in r.text),
        ("V4 a duplicate selection", with_(lambda c: c["selection"].append(ind("fred.sp500"))),
         lambda r: r.detail.get("duplicate_keys") == ["fred.sp500"] and loc_is(r, ["body", "config", "selection"])),
        ("V5 an empty selection", with_(lambda c: c.update(selection=[])),
         lambda r: loc_is(r, ["body", "config", "selection"])),
        ("V6 41 selections", with_(lambda c: c.update(selection=[ind(f"fred.x{i}") for i in range(41)])),
         lambda r: loc_is(r, ["body", "config", "selection"])
         and any(e.get("type") == "too_long" for e in r.detail.get("errors", []))),
        ("V7 a bad mode", with_(lambda c: c.update(mode=SENTINEL)), lambda r: loc_is(r, ["body", "config", "mode"])),
        ("V8 a bad scale", with_(lambda c: c.update(scale=SENTINEL)), lambda r: loc_is(r, ["body", "config", "scale"])),
        ("V9 a bad anchor type", with_(lambda c: c.update(anchor={"type": SENTINEL, "value": "2010-01-01"})),
         lambda r: loc_is(r, ["body", "config", "anchor"])),
        ("V10a relative years 0", with_(lambda c: c.update(anchor={"type": "relative", "years": 0})),
         lambda r: loc_is(r, ["body", "config", "anchor", "relative", "years"])),
        ("V10b relative years 61", with_(lambda c: c.update(anchor={"type": "relative", "years": 61})),
         lambda r: loc_is(r, ["body", "config", "anchor", "relative", "years"])),
        ("V11 anchor after end", with_(lambda c: c.update(anchor={"type": "date", "value": "2015-01-01"},
                                                          end={"type": "date", "value": "2014-12-31"})),
         lambda r: loc_is(r, ["body", "config", "anchor"])),
        ("V12 an unparseable anchor date", with_(lambda c: c.update(anchor={"type": "date", "value": "2015-02-30"})),
         lambda r: loc_is(r, ["body", "config", "anchor", "value"])),
        ("V13 a config over 20,000 bytes", with_(lambda c: c.update(pad=SENTINEL + "x" * 20_001)),
         lambda r: any(e.get("type") == "too_large" for e in r.detail.get("errors", []))),
    ]
    for label, cfg, ok in cases:
        r = await api(UA1, "POST", "/views", {"name": "bad view", "config": cfg})
        check(f"{label} → 422", r.status == 422 and ok(r) and no_echo(r, SENTINEL),
              "the stored config is what the chart replays; an invalid one would fail only later, in the UI",
              f"status={r.status} detail={json.dumps(r.detail)[:300]}")
    check("V14 none of the refused configs stored a row",
          await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE user_id = $1", UA1) == n0,
          "a refusal leaves nothing behind")

    for field in ("org_id", "user_id"):
        r = await api(UA1, "POST", "/views", {"name": SENTINEL, "config": good, field: SENTINEL_UUID})
        stored = await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE name = $1", SENTINEL)
        check(f"V15 a view body containing {field} → 422, nothing stored, nothing echoed",
              r.status == 422 and stored == 0 and no_echo(r, field, SENTINEL, SENTINEL_UUID),
              "org and user come only from the verified session (Rule 6)", f"status={r.status}")

    valid = view_cfg([ind("fred.sp500"), ind("fred.dgs10"), sec(good_sec)],
                     anchor={"type": "relative", "years": 10}, end={"type": "date", "value": "2024-12-31"},
                     mode="sigma", scale="linear")
    r = await api(UA1, "POST", "/views", {"name": "Round trip", "config": valid})
    g = await api(UA1, "GET", "/views")
    mine = [v for v in (g.body or {}).get("views", []) if v["name"] == "Round trip"]
    check("V16 a valid config round-trips through POST then GET unchanged, with nothing unavailable",
          r.status == 201 and len(mine) == 1 and mine[0]["config"] == valid and mine[0]["unavailable"] == []
          and (r.body or {}).get("config") == valid,
          "what the member saved is exactly what the chart reloads", f"post={r.status}")


async def a_views_names(conn) -> None:
    section("Views — names, limits, PUT, presets")
    good = view_cfg([ind("fred.sp500")])
    r_e = await api(UA1, "POST", "/views", {"name": "  ", "config": good})
    r_l = await api(UA1, "POST", "/views", {"name": "w" * 81, "config": good})
    check("N1 empty name → 'Enter a name for this view.'; 81 characters → 'Names can be up to 80 characters.'",
          r_e.status == 422 and r_e.message == "Enter a name for this view." and r_l.status == 422
          and r_l.message == "Names can be up to 80 characters.",
          "the UI shows these exact messages", f"{r_e.message!r} / {r_l.message!r}")
    r1 = await api(UA1, "POST", "/views", {"name": "Alpha View", "config": good})
    r2 = await api(UA1, "POST", "/views", {"name": "ALPHA view", "config": good})
    check("N2 a case-insensitive duplicate name for this user → 409 'You already have a view with that name.'",
          r1.status == 201 and r2.status == 409 and r2.message == "You already have a view with that name.",
          "two views a member cannot tell apart in a menu", f"first={r1.status} dup={r2.status}")

    await q(conn, "execute", """INSERT INTO market_data.saved_views (owner_scope, org_id, user_id, name, config)
                                SELECT 'user', $1, $2, 'verify filler view ' || g, $3::jsonb
                                  FROM generate_series(1, 49) g""", ORG_A, UA3, json.dumps(good), org=ORG_A)
    r50 = await api(UA3, "POST", "/views", {"name": "fiftieth view", "config": good})
    r51 = await api(UA3, "POST", "/views", {"name": "fifty-first view", "config": good})
    n3 = await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE user_id = $1", UA3)
    check("N3 the 50th view is admitted and the 51st → 422 'You can save up to 50 views. Delete one first.'",
          r50.status == 201 and r51.status == 422 and r51.message == "You can save up to 50 views. Delete one first."
          and n3 == 50, "the limit is enforced on the server", f"50th={r50.status} 51st={r51.status} rows={n3}")

    vid = (r1.body or {}).get("id")
    before = await srow(conn, "SELECT updated_at FROM market_data.saved_views WHERE id = $1", UUID(vid))
    new_cfg = view_cfg([ind("fred.dgs10")], mode="level", scale="linear")
    rp = await api(UA1, "PUT", f"/views/{vid}", {"name": "Alpha View v2", "config": new_cfg})
    after = await srow(conn, "SELECT name, config, updated_at FROM market_data.saved_views WHERE id = $1", UUID(vid))
    check("N4 PUT by the owner overwrites name and config; an independent re-read agrees and updated_at moved",
          rp.status == 200 and after["name"] == "Alpha View v2" and json.loads(after["config"]) == new_cfg
          and after["updated_at"] > before["updated_at"],
          "editing a saved view must really persist", f"status={rp.status}")
    r_other = await api(UA1, "POST", "/views", {"name": "Beta View", "config": good})
    rr = await api(UA1, "PUT", f"/views/{vid}", {"name": "beta VIEW"})
    still = await sval(conn, "SELECT name FROM market_data.saved_views WHERE id = $1", UUID(vid))
    check("N5 PUT renaming onto another of the caller's names (any case) → 409, row unchanged",
          r_other.status == 201 and rr.status == 409 and rr.message == "You already have a view with that name."
          and still == "Alpha View v2", "a rename must not create an indistinguishable pair", f"status={rr.status}")
    re_ = await api(UA1, "PUT", f"/views/{vid}", {})
    check("N6 PUT with neither name nor config → 422", re_.status == 422, "an empty update is a client bug, not a no-op success")

    pid = await sval(conn, "SELECT id FROM market_data.saved_views WHERE owner_scope = 'platform' AND name = $1",
                     FX_PRESET_OK["name"])
    snap = await srow(conn, "SELECT name, config::text AS c, updated_at FROM market_data.saved_views WHERE id = $1", pid)
    p_put = await api(UA1, "PUT", f"/views/{pid}", {"name": "hijacked preset"})
    p_del = await api(UA1, "DELETE", f"/views/{pid}")
    snap2 = await srow(conn, "SELECT name, config::text AS c, updated_at FROM market_data.saved_views WHERE id = $1", pid)
    check("N7 PUT and DELETE on a platform preset id → 404, preset unchanged",
          p_put.status == 404 and p_del.status == 404 and snap is not None and dict(snap) == dict(snap2 or {}),
          "platform presets are seed-only and can never be changed through the API",
          f"put={p_put.status} delete={p_del.status}")
    g = await api(UA2, "GET", "/views")
    b = g.body or {}
    check("N8 GET /views: presets listed, an empty views array for a user with none, envelope, vocabularies and limits",
          g.status == 200 and any(p["name"] == FX_PRESET_OK["name"] for p in b.get("presets", []))
          and b.get("views") == [] and (b.get("permissions") or {}).get("can_write") is True
          and [m["key"] for m in b.get("vocabularies", {}).get("modes", [])] == ["index", "sigma", "level", "default"]
          and [s["key"] for s in b.get("vocabularies", {}).get("scales", [])] == ["log", "linear"]
          and (b.get("limits") or {}).get("views_max") == 50 and (b.get("limits") or {}).get("name_max") == 80,
          "the views menu is driven entirely by the server's response", f"limits={b.get('limits')}")


async def a_user_isolation(conn) -> None:
    section("User isolation inside one org (service layer, through the API)")
    d = await api(UA1, "POST", "/key-dates/custom", {"name": "A1 private", "event_date": "2016-06-23"})
    v = await api(UA1, "POST", "/views", {"name": "A1 private view", "config": view_cfg([ind("fred.sp500")])})
    did, vid = (d.body or {}).get("id"), (v.body or {}).get("id")
    g_d = await api(UA2, "GET", "/key-dates")
    g_v = await api(UA2, "GET", "/views")
    check("U1 user A's custom dates and views never appear in user B's GET (same org)",
          d.status == 201 and v.status == 201 and (g_d.body or {}).get("custom_dates") == []
          and (g_v.body or {}).get("views") == [],
          "RLS isolates orgs only; per-user privacy rests on the service filter, so prove it")

    async def snap(table, rid):
        return await sval(conn, f"SELECT to_jsonb(t)::text FROM market_data.{table} t WHERE id = $1", UUID(rid))
    s_d, s_v = await snap("user_key_dates", did), await snap("saved_views", vid)
    x1 = await api(UA2, "DELETE", f"/key-dates/custom/{did}")
    x2 = await api(UA2, "PUT", f"/views/{vid}", {"name": "taken over"})
    x3 = await api(UA2, "DELETE", f"/views/{vid}")
    x4 = await api(UB1, "DELETE", f"/key-dates/custom/{did}")
    x5 = await api(UB1, "PUT", f"/views/{vid}", {"name": "taken over"})
    missing = await api(UA2, "DELETE", f"/key-dates/custom/{uuid.uuid4()}")
    malformed = await api(UA2, "DELETE", "/views/not-a-uuid")
    same = {x.text for x in (x1, x3, x4, missing, malformed)}
    check("U2 another user's DELETE of A's date, PUT and DELETE of A's view (same org and other org) → 404, rows unchanged",
          all(x.status == 404 for x in (x1, x2, x3, x4, x5)) and s_d == await snap("user_key_dates", did)
          and s_v == await snap("saved_views", vid),
          "a member can never change another member's bookmarks", f"{[x.status for x in (x1, x2, x3, x4, x5)]}")
    check("U3 the 404 is identical for another user's row, another org's row, a missing id and a malformed id",
          len(same) == 1 and missing.status == 404 and malformed.status == 404,
          "the answer must give no hint which", f"bodies={same}")
    y1 = await api(UA1, "DELETE", f"/key-dates/custom/{did}")
    y2 = await api(UA1, "DELETE", f"/views/{vid}")
    gone_d = await sval(conn, "SELECT count(*) FROM market_data.user_key_dates WHERE id = $1", UUID(did))
    gone_v = await sval(conn, "SELECT count(*) FROM market_data.saved_views WHERE id = $1", UUID(vid))
    check("U4 the owner's own DELETEs succeed with {\"deleted\": true}, re-read independently (hard delete)",
          y1.status == 200 and y1.body == {"deleted": True} and y2.status == 200 and y2.body == {"deleted": True}
          and gone_d == 0 and gone_v == 0,
          "personal rows are bookmarks: a delete removes them", f"{y1.status}/{y2.status} rows={gone_d}/{gone_v}")


async def a_stale(conn, good_sec: str) -> None:
    section("Stale selections")
    cfg = view_cfg([ind(STALE_KEY), ind("fred.sp500"), sec(good_sec)])
    r = await api(UA1, "POST", "/views", {"name": "Stale view", "config": cfg})
    vid = UUID((r.body or {}).get("id"))
    before = await srow(conn, "SELECT config::text AS c, updated_at FROM market_data.saved_views WHERE id = $1", vid)
    await sexec(conn, f"UPDATE {SERIES_T} SET ingest_status = 'deferred' WHERE series_key = $1", STALE_KEY)
    g = await api(UA1, "GET", "/views")
    after = await srow(conn, "SELECT config::text AS c, updated_at FROM market_data.saved_views WHERE id = $1", vid)
    mine = [v for v in (g.body or {}).get("views", []) if v["id"] == str(vid)]
    check("ST1 after the series goes 'deferred', GET lists its key in unavailable, the other selections are intact, "
          "and the stored config is byte-for-byte unchanged",
          r.status == 201 and len(mine) == 1 and mine[0]["unavailable"] == [STALE_KEY] and mine[0]["config"] == cfg
          and before is not None and dict(before) == dict(after or {}),
          "nothing is silently removed from a member's view, and a read never writes",
          f"unavailable={mine[0]['unavailable'] if mine else None}")


async def phase_a(conn) -> None:
    edges = await fk_edges(conn)
    leftover = await delete_fixtures(conn)
    if any(leftover.values()):
        find(f"removed fixture rows left by an earlier interrupted run: { {k: v for k, v in leftover.items() if v} }")
    base_real = await real_counts(conn)
    try:
        section("Setup")
        await insert_fixtures(conn)
        n = await fixture_counts(conn)
        check("A0 fixtures inserted: 2 orgs, 4 users, 1 series with 3 observations",
              n["public.organizations"] == 2 and n["public.users"] == 4 and n[SERIES_T] == 1 and n[OBS_T] == 3,
              "every assertion below runs as these callers", f"counts={n}")
        first, last = await sql_range(conn)
        deferred_key = await sval(conn, f"SELECT series_key FROM {SERIES_T} WHERE ingest_status = 'deferred' "
                                        f"AND series_key NOT LIKE '{PREFIX}%' ORDER BY series_key LIMIT 1")
        note_sec = await sval(conn, """SELECT g.id::text FROM portfolio.securities_global g
                                        WHERE g.security_type = 'structured_note' AND g.valid_to IS NULL
                                          AND g.system_to IS NULL AND g.merged_into_id IS NULL
                                          AND NOT EXISTS (SELECT 1 FROM market_data.indicator_series s
                                                           WHERE s.security_global_id = g.id AND s.ingest_status = 'active')
                                        ORDER BY g.id LIMIT 1""")
        good_sec = await sval(conn, f"""SELECT g.id::text FROM portfolio.securities_global g
                                         JOIN {SERIES_T} s ON s.security_global_id = g.id AND s.ingest_status = 'active'
                                        WHERE g.valid_to IS NULL AND g.system_to IS NULL AND g.merged_into_id IS NULL
                                          AND s.series_key NOT LIKE '{PREFIX}%' ORDER BY g.id LIMIT 1""")
        if not (deferred_key and note_sec and good_sec):
            check("A0b live prerequisites present: a deferred series, an unpriced note, a priced security", False,
                  "the refusal and round-trip cases need real examples",
                  f"deferred={deferred_key} note={note_sec} priced={good_sec}")
            return

        if not await a_role(conn):
            return
        await a_loader(conn)          # creates the fixture preset the gate and 404 tests use
        await a_schema(conn)
        await a_gates(conn)
        await a_org_isolation(conn)
        await a_least_privilege_static()
        await a_least_privilege_live(conn)
        await a_key_dates_http(conn, first, last)
        await a_key_dates_get(conn, first, last)
        await a_views_config(conn, deferred_key, note_sec, good_sec)
        await a_views_names(conn)
        await a_user_isolation(conn)
        await a_stale(conn, good_sec)
        await a_seed_files(conn)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — reported, then teardown still runs
        check("A.x Phase A ran to completion", False, "an unexpected exception aborted the remaining assertions",
              f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        section("Teardown")
        pos = {t: i for i, t in enumerate(TEARDOWN_ORDER)}
        bad_edges = [(c, p) for c, p in edges if pos[c] > pos[p]]
        needed = {("market_data.user_key_dates", "public.users"), ("market_data.user_key_dates", "public.organizations"),
                  ("market_data.saved_views", "public.users"), ("market_data.saved_views", "public.organizations"),
                  (OBS_T, SERIES_T)}
        check("T1 teardown order (personal rows → users → organizations → observations → series) respects every FK "
              "among these tables, read from information_schema",
              not bad_edges and needed <= set(edges),
              "a parent deleted before its child fails on the FK and strands fixtures",
              f"violations={bad_edges} missing={needed - set(edges)}")
        try:
            deleted = await delete_fixtures(conn)
        except asyncpg.PostgresError as exc:
            print(f"[FAIL] T2 teardown failed: {type(exc).__name__}: {str(exc)[:300]}")
            raise SystemExit(2)
        remaining = await fixture_counts(conn)
        end_real = await real_counts(conn)
        diff = {t: (base_real[t], end_real[t]) for t in base_real if base_real[t] != end_real[t]}
        check("T2 NON-fixture row counts are exactly as before in every table touched or guarded "
              "(indicator_series/observations/ingest_runs, securities_global(+_prices), fx_rates, users, "
              "organizations, key_dates, regimes, saved_views, user_key_dates)",
              not diff, "proves the sprint wrote nothing outside its fixtures and teardown removed only fixtures — "
              "never a TRUNCATE", f"changed={diff} deleted={deleted}")
        if any(remaining.values()):
            print(f"[FAIL] T3 fixture rows remain after teardown: { {k: v for k, v in remaining.items() if v} }")
            raise SystemExit(2)
        check("T3 zero fixture rows remain — re-read independently", True,
              "a leftover fixture user or preset would be visible to real members")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — live reference data (after the operator has run the loader)
# ═══════════════════════════════════════════════════════════════════════════
async def phase_b(conn) -> None:
    section("Phase B — live reference data")
    import importlib.util
    spec = importlib.util.spec_from_file_location("seed_cli", HERE.parent / "market_data_seed_reference.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    seed_kd, seed_rg, seed_vw = cli.read_all_seeds()

    kd_rows = await sfetch(conn, f"SELECT slug, source, start_date FROM market_data.key_dates WHERE slug NOT LIKE '{PREFIX}%'")
    by_src = {s: sum(1 for r in kd_rows if r["source"] == s) for s in ("owner_list", "suggested")}
    check("B1 key_dates holds 32 rows: 22 owner_list, 10 suggested",
          len(kd_rows) == 32 and by_src == {"owner_list": 22, "suggested": 10},
          "the loader landed exactly the owner's list", f"n={len(kd_rows)} {by_src}")
    rg_rows = await sfetch(conn, f"SELECT regime_type, start_date FROM market_data.regimes WHERE name NOT LIKE '{PREFIX}%'")
    by_type = {t: sum(1 for r in rg_rows if r["regime_type"] == t) for t in ("recession", "fed_tightening")}
    check("B2 regimes holds 16 rows, 8 of each type", len(rg_rows) == 16 and by_type == {"recession": 8, "fed_tightening": 8},
          "both band types are present for the chart", f"n={len(rg_rows)} {by_type}")
    pv = await sfetch(conn, f"SELECT name, config FROM market_data.saved_views WHERE owner_scope = 'platform' "
                            f"AND name NOT LIKE '{PREFIX}%'")
    check("B3 there are exactly 4 platform preset views, named as in the seed file",
          sorted(r["name"] for r in pv) == sorted(v["name"] for v in seed_vw),
          "every starter view loaded, none skipped", f"names={[r['name'] for r in pv]}")
    bad = {}
    for r in pv:
        cfg = json.loads(r["config"]) if isinstance(r["config"], str) else r["config"]
        probs = await sv.selection_problems(conn, cfg.get("selection", []))
        if any(probs.values()):
            bad[r["name"]] = probs
    check("B4 every key in every preset resolves to an ACTIVE series (or selectable security)",
          not bad and len(pv) == 4, "a starter view with a dead key would open broken for every member", f"bad={bad}")

    first, last = await sql_range(conn)
    early_kd = min((r["start_date"] for r in kd_rows), default=None)
    early_rg = min((r["start_date"] for r in rg_rows), default=None)
    before = [x for x in (early_kd, early_rg) if x is not None and x < first.replace(day=1)]
    for x in before:
        find(f"a reference date {x} falls before the available data range ({first} to {last})")
    check("B5 the earliest key date and the earliest regime lie within the available data range",
          early_kd is not None and early_rg is not None and not before
          and max(early_kd, early_rg) <= last,
          "a marker before the first observation would be drawn off the chart",
          f"earliest key date={early_kd} earliest regime={early_rg} range={first}..{last}")

    async def kd_row(slug):
        return await srow(conn, "SELECT start_date, start_precision, end_date, end_precision FROM market_data.key_dates "
                                "WHERE slug = $1", slug)
    bm, cv, la = await kd_row("black-monday-1987"), await kd_row("covid-crash-2020"), await kd_row("latam-debt-crisis-1982")
    check("B6 spot checks read back exactly: black-monday-1987, covid-crash-2020, latam-debt-crisis-1982",
          bm is not None and dict(bm) == {"start_date": date(1987, 10, 19), "start_precision": "day", "end_date": None,
                                          "end_precision": None}
          and cv is not None and dict(cv) == {"start_date": date(2020, 2, 19), "start_precision": "day",
                                              "end_date": date(2020, 3, 23), "end_precision": "day"}
          and la is not None and dict(la) == {"start_date": date(1982, 8, 1), "start_precision": "month",
                                              "end_date": None, "end_precision": None},
          "the database holds the owner's dates and precisions verbatim")

    async with conn.transaction():
        await set_ctx(conn, org=ORG_A, sup=False)
        body = await kd.build_key_dates(conn, ORG_A, UA2)
    starts = [k["start_date"] for k in body["key_dates"]]
    check("B7 GET /market/key-dates through the production function as a fixture caller (no super-admin): 32 key dates "
          "chronologically, 16 regimes, empty custom_dates, and the data range",
          len(body["key_dates"]) == 32 and starts == sorted(starts) and len(body["regimes"]) == 16
          and body["custom_dates"] == [] and body["data_range"] == {"first": first.isoformat(), "last": last.isoformat()},
          "this is exactly what the mkt04 chart will receive", f"kd={len(body['key_dates'])} rg={len(body['regimes'])}")

    res = await rs.load_reference(conn, key_dates=seed_kd, regimes=seed_rg, views=seed_vw, dry_run=True)
    c = res.counts
    check("B8 the loader's dry run over the real seed files reports 0 inserted and 0 updated in every table",
          all(c[t]["inserted"] == 0 and c[t]["updated"] == 0 and c[t]["skipped"] == 0 for t in rs.TABLES),
          "the live tables match the seed files exactly", f"counts={c} finds={res.finds}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="also run Phase B against the real reference data")
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
    except Exception as exc:  # noqa: BLE001
        check("DB connect", False, "nothing can be proven without the database", type(exc).__name__)
        print(f"TOTAL: {PASSED} passed, {FAILED} failed")
        return 1
    try:
        await phase_a(conn)
        if args.live:
            await phase_b(conn)
        else:
            skip("Phase B (live reference data) was not run — pass --live after running the loader")
    finally:
        await conn.close()
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
    print(f"TOTAL: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
