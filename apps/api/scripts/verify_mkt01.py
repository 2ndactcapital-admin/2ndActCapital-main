#!/usr/bin/env python3
"""verify_mkt01 — market data foundation: registry, FRED adapter, backfill.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py
    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live

PHASE A (always) is self-contained: every row it writes is a fixture whose
series_key starts with 'verify.mkt01.', FRED is replaced by a FAKE transport
(no network), and teardown removes every fixture row, child tables first, in
an order derived from information_schema. Real rows are never touched — the
real seed file is only read.

PHASE B (--live) checks the REAL data after the operator has run
``market_data_ingest.py all``, and spot-checks three series against FRED.

Hydrates its own secrets from Doppler over HTTPS (scripts/_doppler_env.py) —
run_sprint.sh's verify step does not wrap scripts in ``doppler run --``.

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
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]
sys.path.insert(0, str(API_DIR))   # apps/api, for services.*
sys.path.insert(0, str(HERE.parent))  # scripts/, for _doppler_env + the operator script

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402
from services.market_data import fred, ingest, registry  # noqa: E402
from services.market_data.fred import (  # noqa: E402
    FredClient, FredResponse, FredTransientError, HttpxTransport, TransportError,
)
import market_data_ingest as mdi  # noqa: E402

PREFIX = "verify.mkt01."
SERIES_T = "market_data.indicator_series"
OBS_T = "market_data.indicator_observations"
RUNS_T = "market_data.indicator_ingest_runs"
UNTOUCHED = ("portfolio.securities_global", "portfolio.securities_global_prices", "public.fx_rates")
SENTINEL = f"SENTINELKEY{uuid.uuid4().hex}"

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
    return ok


def find(msg: str) -> None:
    print(f"[FIND] {msg}")


def skip(msg: str) -> None:
    print(f"[SKIP] {msg}")


@asynccontextmanager
async def outside(conn):
    """A transaction WITHOUT platform scope — exactly what the app pool sets
    for a non-super-admin caller (app.is_super_admin = 'false', local)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
        yield conn


async def nosleep(_seconds: float) -> None:
    return None


# ── Fake FRED transport ─────────────────────────────────────────────────────
class FakeTransport:
    """Per-code canned behaviour. Records every call so a test can prove the
    sentinel key really was sent (otherwise the no-secret proof is vacuous)."""

    def __init__(self):
        self.series: dict[str, object] = {}
        self.observations: dict[str, object] = {}
        self.calls: list[tuple[str, dict]] = []

    async def get(self, path, params):
        self.calls.append((path, dict(params)))
        table = self.observations if path == fred.OBSERVATIONS_PATH else self.series
        behaviour = table.get(params["series_id"])
        if behaviour is None:
            return FredResponse(500, None)
        if callable(behaviour):
            return behaviour(params)
        return behaviour

    def attempts(self, path, code) -> int:
        return sum(1 for p, q in self.calls if p == path and q["series_id"] == code)


def meta_payload(code, frequency_short, units="Percent", notes="mkt01 verify fixture"):
    return FredResponse(200, {"seriess": [{
        "id": code, "title": code, "frequency_short": frequency_short, "units": units,
        "seasonal_adjustment": "Not Seasonally Adjusted", "notes": notes}]})


def obs_payload(points):
    return FredResponse(200, {"count": len(points), "offset": 0, "limit": 100000,
                              "observations": [{"date": d, "value": v} for d, v in points]})


def fixture_row(suffix, code, status, *, frequency="daily", sort_order=0,
                license_class="public_domain", name=None):
    return {
        "series_key": PREFIX + suffix, "name": name or f"verify mkt01 {suffix}",
        "category": "Verify", "region": "US", "frequency": frequency,
        "best_view": None, "default_transform": "level", "cost_tier": "free",
        "cost_note": None, "license_class": license_class, "source_provider": "fred",
        "source_code": code, "source_url": None, "notes": "mkt01 verify fixture",
        "sort_order": sort_order, "ingest_status": status,
    }


FIXTURES = [
    fixture_row("fail", "VMKT01FAIL", "active", sort_order=-1009),
    fixture_row("backfill", "VMKT01BACKFILL", "active", sort_order=-1008),
    fixture_row("obs", "VMKT01OBS", "active", frequency="monthly", sort_order=-1007),
    fixture_row("vfound", "VMKT01FOUND", "pending", frequency="monthly", sort_order=-1006),
    fixture_row("vmissing", "VMKT01MISSING", "pending", sort_order=-1005),
    fixture_row("v5xx", "VMKT01FIVEXX", "active", sort_order=-1004),
    fixture_row("vtimeout", "VMKT01TIMEOUT", "pending", sort_order=-1003),
    fixture_row("vbadkey", "VMKT01BADKEY", "pending", sort_order=-1002),
    fixture_row("loader", "VMKT01LOADER", "pending", sort_order=-1001),
]
K = {r["series_key"][len(PREFIX):]: r["series_key"] for r in FIXTURES}


# ── Counting helpers ────────────────────────────────────────────────────────
FIXTURE_IDS = f"(SELECT id FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%')"


async def fixture_counts(conn) -> dict[str, int]:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(f"SELECT count(*) FROM {RUNS_T} WHERE series_id IN {FIXTURE_IDS}"),
        }


async def real_counts(conn) -> dict[str, int]:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key NOT LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id NOT IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(
                f"SELECT count(*) FROM {RUNS_T} WHERE series_id IS NULL OR series_id NOT IN {FIXTURE_IDS}"),
        }


async def untouched_counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in UNTOUCHED}


async def series_row(conn, key):
    return await conn.fetchrow(f"SELECT * FROM {SERIES_T} WHERE series_key = $1", key)


# ── Teardown ────────────────────────────────────────────────────────────────
async def fk_delete_order(conn) -> list[str]:
    """Child-before-parent order over the three market_data tables, DERIVED
    from information_schema rather than assumed."""
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
    return order


async def delete_fixtures(conn, order) -> dict[str, int]:
    where = {
        OBS_T: f"series_id IN {FIXTURE_IDS}",
        RUNS_T: f"series_id IN {FIXTURE_IDS}",
        SERIES_T: f"series_key LIKE '{PREFIX}%'",
    }
    deleted = {}
    async with platform_scope(conn):
        for table in order:
            status = await conn.execute(f"DELETE FROM {table} WHERE {where[table]}")
            deleted[table] = int(status.split()[-1])
    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A
# ═══════════════════════════════════════════════════════════════════════════
async def a_schema(conn) -> None:
    regs = {t: await conn.fetchval("SELECT to_regclass($1)::text", t) for t in (SERIES_T, OBS_T, RUNS_T)}
    check("A1.1 all three market_data tables exist (to_regclass)",
          all(regs.values()),
          "every later assertion reads or writes these tables; a missing one would make them fail for the wrong reason",
          str(regs))
    rls = await conn.fetch(
        "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'market_data'::regnamespace AND relkind = 'r' "
        "AND relname = ANY($1::text[])", [x.split(".", 1)[1] for x in (SERIES_T, OBS_T, RUNS_T)])
    check("A1.2 relrowsecurity = true on all three tables",
          len(rls) == 3 and all(r["relrowsecurity"] for r in rls),
          "with RLS disabled every policy below is decorative and any app_service caller could write global data",
          str([dict(r) for r in rls]))
    pol = {r["t"]: r["n"] for r in await conn.fetch(
        "SELECT polrelid::regclass::text AS t, count(*) AS n FROM pg_policy "
        "WHERE polrelid::regclass::text LIKE 'market_data.%' GROUP BY 1")}
    check("A1.3 policy counts: series 4, observations 4, ingest_runs 1",
          {k: v for k, v in pol.items() if k in (SERIES_T, OBS_T, RUNS_T)} == {SERIES_T: 4, OBS_T: 4, RUNS_T: 1},
          "a missing UPDATE policy silently matches zero rows (CLAUDE.md 'row not found' bug) — counts pin all four commands",
          str(pol))
    grants = {}
    for t in (SERIES_T, OBS_T, RUNS_T):
        grants[t] = all([await conn.fetchval("SELECT has_table_privilege('app_service', $1, $2)", t, p)
                         for p in ("SELECT", "INSERT", "UPDATE", "DELETE")])
    others = {}
    for role in ("anon", "authenticated"):
        if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
            for t in (SERIES_T, OBS_T, RUNS_T):
                others[(role, t)] = any([await conn.fetchval("SELECT has_table_privilege($1, $2, $3)", role, t, p)
                                         for p in ("SELECT", "INSERT", "UPDATE", "DELETE")])
    check("A1.4 app_service has SELECT/INSERT/UPDATE/DELETE on all three; anon/authenticated have nothing",
          all(grants.values()) and not any(others.values()),
          "the ingest runs as app_service, and Supabase's anon/authenticated roles must not reach platform data via PostgREST",
          f"app_service={grants} others={others}")
    who = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check("A1.5 the connection role does NOT bypass RLS (rolbypassrls = false)",
          who is not None and who["rolbypassrls"] is False,
          "every refusal and isolation check below is meaningless on a bypassing role — they would all 'pass' by never being enforced",
          str(dict(who) if who else None))
    idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname='market_data' AND indexname='uq_indicator_obs_point'")
    check("A1.6 uq_indicator_obs_point is a UNIQUE partial index on the active-row predicate",
          bool(idx) and "UNIQUE" in idx and "valid_to IS NULL" in idx and "system_to IS NULL" in idx,
          "the write rule relies on it: at most one active value per (series, date)", str(idx))


async def a_seed_file(conn) -> None:
    seed = registry.load_seed_file()
    keys = [s["series_key"] for s in seed]
    status = {}
    for s in seed:
        status[(s["source_provider"] == "fred", s["ingest_status"])] = status.get(
            (s["source_provider"] == "fred", s["ingest_status"]), 0) + 1
    check("A2.1 seed file: 75 series, 75 unique series_keys",
          len(seed) == 75 and len(set(keys)) == 75,
          "series_key is the upsert key; a duplicate would silently merge two indicators into one row",
          f"rows={len(seed)} unique={len(set(keys))}")
    check("A2.2 seed file: 57 FRED 'pending', 12 'deferred', 6 'deferred_paid'",
          status == {(True, "pending"): 57, (False, "deferred"): 12, (False, "deferred_paid"): 6},
          "validate only picks up FRED rows; a non-FRED row marked pending would sit pending forever", str(status))
    allowed = {}
    for r in await conn.fetch("SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
                              "WHERE conrelid = 'market_data.indicator_series'::regclass AND contype = 'c'"):
        allowed[r["conname"]] = set(re.findall(r"'([^']+)'::text", r["d"]))
    mapping = {"frequency": "indicator_series_frequency_chk",
               "default_transform": "indicator_series_transform_chk",
               "cost_tier": "indicator_series_cost_tier_chk",
               "license_class": "indicator_series_license_chk",
               "ingest_status": "indicator_series_status_chk"}
    bad = [(s["series_key"], f, s[f]) for s in seed for f, con in mapping.items()
           if s[f] not in allowed.get(con, set())]
    check("A2.3 every seed frequency/default_transform/cost_tier/license_class/ingest_status is allowed by the LIVE CHECKs",
          all(mapping[f] in allowed for f in mapping) and not bad,
          "a value the CHECK rejects aborts the whole load at the first bad row", f"bad={bad[:5]}")


async def a_loader(conn, reader) -> None:
    async with platform_scope(conn):
        first = await registry.load_registry(conn, FIXTURES)
    n1 = (await fixture_counts(reader))["series"]
    check("A3.1 first load of the fixture seed inserts every row",
          (first.inserted, first.updated, first.unchanged) == (len(FIXTURES), 0, 0) and n1 == len(FIXTURES),
          "the loader's insert path is what the operator's first `load` exercises",
          f"result={first} rows={n1}")
    stamps = {r["series_key"]: r["updated_at"] for r in await reader.fetch(
        f"SELECT series_key, updated_at FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%'")}
    async with platform_scope(conn):
        second = await registry.load_registry(conn, FIXTURES)
    n2 = (await fixture_counts(reader))["series"]
    stamps2 = {r["series_key"]: r["updated_at"] for r in await reader.fetch(
        f"SELECT series_key, updated_at FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%'")}
    check("A3.2 loading the same seed twice: identical row count, 0 inserted, 0 updated, updated_at untouched",
          (second.inserted, second.updated, second.unchanged) == (0, 0, len(FIXTURES)) and n2 == n1 and stamps2 == stamps,
          "the nightly/operator re-run must be a no-op; fixtures stand in for the real seed so Phase A never writes real rows",
          f"result={second} rows {n1}->{n2}")

    async with platform_scope(conn):
        await conn.execute(
            f"UPDATE {SERIES_T} SET ingest_status='active', units='Percent', seasonal_adjustment='SA', "
            f"last_error='kept by verify', last_observation_date='2020-01-01', last_validated_at=now() "
            f"WHERE series_key = $1", K["loader"])
    before = await series_row(reader, K["loader"])
    edited = [dict(r) for r in FIXTURES if r["series_key"] != K["loader"]]
    changed = dict(next(r for r in FIXTURES if r["series_key"] == K["loader"]))
    changed.update(name="verify mkt01 loader RENAMED", ingest_status="pending")
    edited.append(changed)
    async with platform_scope(conn):
        third = await registry.load_registry(conn, edited)
    after = await series_row(reader, K["loader"])
    ops_kept = all(before[f] == after[f] for f in registry.OPERATIONAL_FIELDS)
    check("A3.3 a reload updates the definition field but NEVER overwrites ingest_status/units/seasonal_adjustment/"
          "last_validated_at/last_observation_date/last_error",
          third.updated == 1 and after["name"] == "verify mkt01 loader RENAMED" and ops_kept
          and after["ingest_status"] == "active" and after["units"] == "Percent",
          "the seed says 'pending'; letting it win would reset every validated series and wipe what validate learned",
          f"result={third} status={after['ingest_status']} units={after['units']}")

    async with platform_scope(conn):
        await registry.load_registry(conn, FIXTURES[:-1])
    n4 = (await fixture_counts(reader))["series"]
    check("A3.4 a seed missing a row does not delete that registry row",
          n4 == len(FIXTURES),
          "registry rows carry ingest history; a seed edit must never cascade into data loss", f"rows={n4}")


async def a_permission_gate(conn, reader) -> None:
    key = PREFIX + "gate"
    ins_series = (f"INSERT INTO {SERIES_T} (series_key, name, category, frequency, cost_tier, source_provider) "
                  "VALUES ($1, 'verify gate', 'Verify', 'daily', 'free', 'fred')")

    async def n_series():
        return await reader.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key = $1", key)

    refused = None
    try:
        async with outside(conn):
            await conn.execute(ins_series, key)
    except asyncpg.InsufficientPrivilegeError as exc:
        refused = str(exc)
    check("A4.1 INSERT into indicator_series is REFUSED outside platform_scope (RLS error, row count unchanged)",
          refused is not None and "row-level security" in refused and await n_series() == 0,
          "only platform code may define global indicators; a tenant request reaching this INSERT must fail",
          f"error={refused!r}")
    async with platform_scope(conn):
        await conn.execute(ins_series, key)
    check("A4.2 the IDENTICAL INSERT is ADMITTED inside platform_scope",
          await n_series() == 1,
          "a gate that refuses everyone would pass A4.1 trivially; the same statement must succeed with scope")
    sid = await reader.fetchval(f"SELECT id FROM {SERIES_T} WHERE series_key = $1", key)

    ins_obs = f"INSERT INTO {OBS_T} (series_id, obs_date, value) VALUES ($1, DATE '2024-01-01', 1)"

    async def n_obs():
        return await reader.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id = $1", sid)

    refused = None
    try:
        async with outside(conn):
            await conn.execute(ins_obs, sid)
    except asyncpg.InsufficientPrivilegeError as exc:
        refused = str(exc)
    check("A4.3 INSERT into indicator_observations is REFUSED outside platform_scope (RLS error, count unchanged)",
          refused is not None and "row-level security" in refused and await n_obs() == 0,
          "observation history feeds every tenant's charts; only the platform ingest may write it", f"error={refused!r}")
    async with platform_scope(conn):
        await conn.execute(ins_obs, sid)
    check("A4.4 the IDENTICAL observation INSERT is ADMITTED inside platform_scope",
          await n_obs() == 1, "proves the refusal in A4.3 is the scope gate, not a broken statement")

    upd_s = f"UPDATE {SERIES_T} SET name = 'verify gate HIJACKED' WHERE series_key = $1"
    upd_o = f"UPDATE {OBS_T} SET value = 999 WHERE series_id = $1"
    async with outside(conn):
        s1 = await conn.execute(upd_s, key)
        s2 = await conn.execute(upd_o, sid)
    name = await reader.fetchval(f"SELECT name FROM {SERIES_T} WHERE series_key = $1", key)
    val = await reader.fetchval(f"SELECT value FROM {OBS_T} WHERE series_id = $1", sid)
    check("A4.5 UPDATE outside platform_scope leaves series and observation unchanged (re-read independently)",
          s1 == "UPDATE 0" and s2 == "UPDATE 0" and name == "verify gate" and val == Decimal("1"),
          "RLS refuses UPDATE silently (zero rows, no error) — only a re-read proves nothing changed",
          f"status={s1}/{s2} name={name!r} value={val}")
    async with outside(conn):
        d1 = await conn.execute(f"DELETE FROM {OBS_T} WHERE series_id = $1", sid)
        d2 = await conn.execute(f"DELETE FROM {SERIES_T} WHERE series_key = $1", key)
    check("A4.6 DELETE outside platform_scope leaves both rows in place (re-read independently)",
          d1 == "DELETE 0" and d2 == "DELETE 0" and await n_obs() == 1 and await n_series() == 1,
          "a tenant must not be able to erase platform history", f"status={d1}/{d2}")
    async with platform_scope(conn):
        s1 = await conn.execute(upd_s, key)
        s2 = await conn.execute(upd_o, sid)
    name = await reader.fetchval(f"SELECT name FROM {SERIES_T} WHERE series_key = $1", key)
    val = await reader.fetchval(f"SELECT value FROM {OBS_T} WHERE series_id = $1", sid)
    check("A4.7 the IDENTICAL UPDATEs succeed inside platform_scope",
          s1 == "UPDATE 1" and s2 == "UPDATE 1" and name == "verify gate HIJACKED" and val == Decimal("999"),
          "proves A4.5's zero rows was the gate, not a WHERE clause that matched nothing")
    async with platform_scope(conn):
        d1 = await conn.execute(f"DELETE FROM {OBS_T} WHERE series_id = $1", sid)
        d2 = await conn.execute(f"DELETE FROM {SERIES_T} WHERE series_key = $1", key)
    check("A4.8 the IDENTICAL DELETEs succeed inside platform_scope",
          d1 == "DELETE 1" and d2 == "DELETE 1" and await n_obs() == 0 and await n_series() == 0,
          "proves A4.6 was the gate; also proves the DELETE policies exist (teardown depends on them)")


async def a_adapter(conn, reader, fake: FakeTransport, client: FredClient, captured: list[str]) -> None:
    mapping = {"D": "daily", "W": "weekly", "M": "monthly", "Q": "quarterly", "SA": "semiannual",
               "A": "irregular", "BW": "irregular", "": "irregular", None: "irregular"}
    got = {k: fred.map_frequency(k) for k in mapping}
    check("A5.1 frequency mapping D/W/M/Q/SA → daily/weekly/monthly/quarterly/semiannual, anything else → irregular",
          got == mapping, "FRED is authoritative for frequency; a wrong map would rewrite correct registry rows", str(got))

    fake.series["VMKT01FOUND"] = meta_payload(
        "VMKT01FOUND", "D", notes="Copyright, 2026, Example Data Vendor. All rights reserved.")
    fake.series["VMKT01MISSING"] = FredResponse(
        400, {"error_code": 400, "error_message": "Bad Request.  The series does not exist."})
    fake.series["VMKT01FIVEXX"] = FredResponse(503, None)

    def timeout(_params):
        raise TransportError("ReadTimeout")
    fake.series["VMKT01TIMEOUT"] = timeout
    # A hostile response: FRED echoing the key back inside a 400 about the key.
    fake.series["VMKT01BADKEY"] = lambda p: FredResponse(400, {
        "error_code": 400,
        "error_message": f"Bad Request.  The value for variable api_key is not registered. "
                         f"api_key={p['api_key']} (raw: {p['api_key']})"})

    before = {k: await series_row(reader, K[k]) for k in ("v5xx", "vtimeout", "vbadkey")}
    keys = [K["vfound"], K["vmissing"], K["v5xx"], K["vtimeout"], K["vbadkey"]]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        outcomes = await mdi.run_validate(conn, client, keys)
    captured += [out.getvalue(), err.getvalue()]
    by_key = {o.series_key: o for o in outcomes}

    found = await series_row(reader, K["vfound"])
    check("A5.2 found series → status active, units + seasonal_adjustment set, last_validated_at set, last_error NULL",
          found["ingest_status"] == "active" and found["units"] == "Percent"
          and found["seasonal_adjustment"] == "Not Seasonally Adjusted"
          and found["last_validated_at"] is not None and found["last_error"] is None,
          "backfill only runs 'active' series; this is the gate between registry and history",
          f"{dict(found)}")
    check("A5.3 FRED frequency 'D' overrides the seed's 'monthly', and the operator output prints a [FIND] for it",
          found["frequency"] == "daily"
          and "[FIND] verify.mkt01.vfound: frequency seed=monthly FRED=daily" in out.getvalue(),
          "the seed's frequencies were written from memory; a silent correction would hide which ones were wrong",
          f"frequency={found['frequency']}")
    check("A5.4 FRED notes mentioning copyright on a public_domain seed row print a [FIND] and leave license_class alone",
          found["license_class"] == "public_domain" and "copyright" in out.getvalue().lower()
          and any("copyright" in f for f in by_key[K["vfound"]].finds),
          "licensing is a human decision for a multi-tenant product; code must flag it, never decide it")
    async with platform_scope(conn):
        await registry.load_registry(conn, FIXTURES)
    reloaded = await series_row(reader, K["vfound"])
    check("A5.5 a reload does NOT revert a validated FRED row's frequency to the seed value",
          reloaded["frequency"] == "daily",
          "otherwise load and validate fight forever, flipping the value and re-printing the same [FIND] each run "
          "(documented exception in registry.py)", f"frequency={reloaded['frequency']}")

    missing = await series_row(reader, K["vmissing"])
    check("A5.6 'series does not exist' (HTTP 400 + FRED's message) → invalid_code with a last_error",
          missing["ingest_status"] == "invalid_code" and "does not exist" in (missing["last_error"] or ""),
          "an unvalidated code from memory must be visibly quarantined, not left pending forever",
          f"status={missing['ingest_status']} error={missing['last_error']!r}")

    five = await series_row(reader, K["v5xx"])
    check("A5.7 a persistent 5xx leaves status unchanged ('active') and sets last_error — after exactly 4 attempts",
          five["ingest_status"] == before["v5xx"]["ingest_status"] == "active" and five["last_error"]
          and fake.attempts(fred.SERIES_PATH, "VMKT01FIVEXX") == 4,
          "a FRED outage must never demote a working series; 1 try + 3 retries is the spec'd budget",
          f"status={five['ingest_status']} attempts={fake.attempts(fred.SERIES_PATH, 'VMKT01FIVEXX')}")
    tmo = await series_row(reader, K["vtimeout"])
    check("A5.8 a timeout leaves status unchanged ('pending') — transient is never invalid_code",
          tmo["ingest_status"] == "pending" and tmo["last_error"] and "ReadTimeout" in tmo["last_error"],
          "a slow network night must not mark a valid code invalid", f"status={tmo['ingest_status']}")
    badkey = await series_row(reader, K["vbadkey"])
    check("A5.9 a 400 that is NOT 'series does not exist' (bad API key) leaves status unchanged",
          badkey["ingest_status"] == "pending" and badkey["last_error"],
          "FRED answers a bad key with HTTP 400 too; treating every 400 as not-found would mark all 57 series invalid",
          f"status={badkey['ingest_status']}")
    async with platform_scope(reader):
        runs = await reader.fetch(
            f"SELECT s.series_key, r.status FROM {RUNS_T} r JOIN {SERIES_T} s ON s.id = r.series_id "
            f"WHERE r.run_trigger = 'validate' AND s.series_key = ANY($1::text[])", keys)
    per = {k: [r["status"] for r in runs if r["series_key"] == k] for k in keys}
    check("A5.10 one indicator_ingest_runs row per validated series (run_trigger='validate')",
          all(len(v) == 1 for v in per.values()) and per[K["vfound"]] == ["success"],
          "the run log is how an operator audits which codes were checked and when", str(per))


async def run_backfill(conn, client, keys, captured):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        outcomes = await mdi.run_backfill(conn, client, keys)
    captured += [out.getvalue(), err.getvalue()]
    return {o.series_key: o for o in outcomes}


async def a_bitemporal(conn, reader, fake, client, captured) -> None:
    key = K["obs"]
    sid = await reader.fetchval(f"SELECT id FROM {SERIES_T} WHERE series_key = $1", key)
    data = [("2024-01-01", "1.5"), ("2024-02-01", "2.25"), ("2024-04-01", "3.0"), ("2024-07-01", "4")]

    async def totals():
        return (await reader.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id = $1", sid),
                await reader.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id = $1 "
                                      "AND valid_to IS NULL AND system_to IS NULL", sid))

    fake.observations["VMKT01OBS"] = obs_payload(data)
    o1 = (await run_backfill(conn, client, [key], captured))[key]
    total1, active1 = await totals()
    dates = sorted(r["obs_date"] for r in await reader.fetch(f"SELECT obs_date FROM {OBS_T} WHERE series_id = $1", sid))
    check("A6.1 first load of N=4 fixture observations = 4 inserts, 4 rows, dates stored exactly as given",
          o1.counts.inserted == 4 and o1.counts.revised == 0 and total1 == active1 == 4
          and dates == [date.fromisoformat(d) for d, _ in data],
          "monthly/quarterly FRED dates must not be shifted — the chart aligns series on them",
          f"counts={o1.counts} rows={total1} dates={dates}")

    # Same values, one spelled differently ('3.0' -> '3.00'): numerically equal.
    fake.observations["VMKT01OBS"] = obs_payload([(d, "3.00" if v == "3.0" else v) for d, v in data])
    o2 = (await run_backfill(conn, client, [key], captured))[key]
    total2, active2 = await totals()
    check("A6.2 reloading identical data = zero writes (row count unchanged), even with '3.0' respelled '3.00'",
          (o2.counts.inserted, o2.counts.revised, o2.counts.unchanged) == (0, 0, 4) and total2 == total1,
          "a nightly re-fetch must not grow history; comparison is numeric Decimal, not string",
          f"counts={o2.counts} rows {total1}->{total2}")

    fake.observations["VMKT01OBS"] = obs_payload([(d, "2.5" if d == "2024-02-01" else v) for d, v in data])
    o3 = (await run_backfill(conn, client, [key], captured))[key]
    hist = await reader.fetch(
        f"SELECT value, valid_from, valid_to, system_to FROM {OBS_T} WHERE series_id = $1 AND obs_date = '2024-02-01' "
        "ORDER BY valid_from", sid)
    active_rows = [h for h in hist if h["valid_to"] is None and h["system_to"] is None]
    closed = [h for h in hist if h["valid_to"] is not None]
    total3, _ = await totals()
    check("A6.3 ONE changed value = exactly one revision: old row closed (valid_to set), one active row, two history rows",
          (o3.counts.inserted, o3.counts.revised, o3.counts.unchanged) == (0, 1, 3)
          and len(hist) == 2 and len(active_rows) == 1 and active_rows[0]["value"] == Decimal("2.5")
          and len(closed) == 1 and closed[0]["value"] == Decimal("2.25") and total3 == total1 + 1,
          "Rule 3: a FRED revision must keep what we previously showed, not overwrite it",
          f"counts={o3.counts} history={[dict(h) for h in hist]}")
    check("A6.4 the closed row's valid_to equals the new row's valid_from (no gap, no overlap)",
          len(closed) == 1 and len(active_rows) == 1 and closed[0]["valid_to"] == active_rows[0]["valid_from"],
          "an as-of query at any instant must resolve to exactly one value")
    srow = await series_row(reader, key)
    maxd = await reader.fetchval(f"SELECT max(obs_date) FROM {OBS_T} WHERE series_id = $1 "
                                 "AND valid_to IS NULL AND system_to IS NULL", sid)
    async with platform_scope(reader):
        runs = await reader.fetch(f"SELECT status, rows_inserted, rows_revised, rows_unchanged FROM {RUNS_T} "
                                  "WHERE series_id = $1 AND run_trigger = 'backfill' ORDER BY started_at", sid)
    check("A6.5 persisted on an independent connection: last_observation_date = max(obs_date), 3 backfill run rows with the counts",
          srow["last_observation_date"] == maxd == date(2024, 7, 1)
          and [(r["status"], r["rows_inserted"], r["rows_revised"], r["rows_unchanged"]) for r in runs]
          == [("success", 4, 0, 0), ("success", 0, 0, 4), ("success", 0, 1, 3)],
          "the operator's audit trail must match what was actually written, read back rather than returned",
          f"last={srow['last_observation_date']} max={maxd} runs={[dict(r) for r in runs]}")


async def a_decimal_and_isolation(conn, reader, fake, client, captured) -> None:
    probes = {
        ".": None, "4.123456789": Decimal("4.123456789"), "-0.25": Decimal("-0.25"),
    }
    ok = all(ingest.parse_value(k) == v and (v is None or isinstance(ingest.parse_value(k), Decimal))
             for k, v in probes.items())
    rejected = []
    for bad in ("abc", "NaN", "Infinity", "-inf", "1_000", "", "1,000"):
        try:
            ingest.parse_value(bad)
        except ingest.RejectedValue:
            rejected.append(bad)
    check("A7.1 parse_value: '.' → None, numbers → Decimal; 'abc'/NaN/Infinity/'1_000'/''/'1,000' rejected",
          ok and len(rejected) == 7,
          "Decimal() alone accepts NaN, Infinity and '1_000'; any of them in history would poison every chart computation",
          f"rejected={rejected}")

    # 'fail' sorts first and raises with the key in its text; 'backfill' must still run.
    def explode(p):
        raise RuntimeError(f"GET https://api.stlouisfed.org/fred/series/observations?api_key={p['api_key']}")
    fake.observations["VMKT01FAIL"] = explode
    fake.observations["VMKT01BACKFILL"] = obs_payload([
        ("2024-01-01", "4.123456789"), ("2024-02-01", "."), ("2024-03-01", "abc"),
        ("2024-04-01", "NaN"), ("2024-05-01", "-0.25")])
    res = await run_backfill(conn, client, [K["fail"], K["backfill"]], captured)
    sid = await reader.fetchval(f"SELECT id FROM {SERIES_T} WHERE series_key = $1", K["backfill"])
    stored = {r["obs_date"]: r["value"] for r in await reader.fetch(
        f"SELECT obs_date, value FROM {OBS_T} WHERE series_id = $1", sid)}
    exact = stored.get(date(2024, 1, 1))
    check("A7.2 '4.123456789' round-trips through Postgres as an EQUAL Decimal with the same digits (no float drift)",
          isinstance(exact, Decimal) and exact == Decimal("4.123456789") and str(exact) == "4.123456789",
          "a float hop turns 4.123456789 into 4.12345678899999…; equality would then fail on every re-fetch",
          f"stored={exact!r}")
    b = res[K["backfill"]]
    check("A7.3 '.' is skipped and never stored; 'abc' and 'NaN' are rejected and never stored; status 'partial'",
          set(stored) == {date(2024, 1, 1), date(2024, 5, 1)} and b.skipped == 1 and b.rejected == 2
          and b.status == "partial",
          "FRED's '.' means 'no observation'; storing it as zero would draw a false crash on the chart",
          f"stored={sorted(stored)} skipped={b.skipped} rejected={b.rejected} status={b.status}")
    f = res[K["fail"]]
    async with platform_scope(reader):
        frun = await reader.fetchval(
            f"SELECT status FROM {RUNS_T} r JOIN {SERIES_T} s ON s.id = r.series_id "
            "WHERE s.series_key = $1 AND r.run_trigger = 'backfill'", K["fail"])
    check("A7.4 one series failing does not abort the others: 'fail' recorded failed, 'backfill' (run after it) still wrote",
          f.status == "failed" and frun == "failed" and len(stored) == 2,
          "one bad FRED code at 2am must not leave the other 56 series without history",
          f"fail={f.status}/{frun} backfill rows={len(stored)}")


async def a_unique_index(conn, reader) -> None:
    sid = await reader.fetchval(f"SELECT id FROM {SERIES_T} WHERE series_key = $1", K["obs"])
    n0 = await reader.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id = $1", sid)
    violation = None
    try:
        async with platform_scope(conn):
            await conn.execute(f"INSERT INTO {OBS_T} (series_id, obs_date, value) VALUES ($1, DATE '2024-01-01', 42)", sid)
    except asyncpg.UniqueViolationError as exc:
        violation = exc
    n1 = await reader.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id = $1", sid)
    check("A8.1 a second ACTIVE row for the same (series, date) raises a unique violation on uq_indicator_obs_point; count unchanged",
          violation is not None and getattr(violation, "constraint_name", None) == "uq_indicator_obs_point" and n1 == n0,
          "if a buggy writer could create two active values, every reader would double-count that date",
          f"violation={type(violation).__name__ if violation else None} rows {n0}->{n1}")


async def a_read_gate(conn) -> None:
    key = K["obs"]
    q_series = f"SELECT count(*) FROM {SERIES_T} WHERE series_key = $1"
    q_obs = f"SELECT count(*) FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id WHERE s.series_key = $1"
    q_runs = f"SELECT count(*) FROM {RUNS_T} r JOIN {SERIES_T} s ON s.id = r.series_id WHERE s.series_key = $1"
    q_all_runs = f"SELECT count(*) FROM {RUNS_T}"
    async with outside(conn):
        o = [await conn.fetchval(q, key) for q in (q_series, q_obs, q_runs)] + [await conn.fetchval(q_all_runs)]
    async with platform_scope(conn):
        i = [await conn.fetchval(q, key) for q in (q_series, q_obs, q_runs)] + [await conn.fetchval(q_all_runs)]
    check("A9.1 series and observations are readable WITHOUT platform scope (same counts as with it)",
          o[0] == i[0] == 1 and o[1] == i[1] and o[1] > 0,
          "indicators are global reference data every tenant's chart reads", f"outside={o[:2]} inside={i[:2]}")
    check("A9.2 indicator_ingest_runs returns ZERO rows without platform scope, and the fixture rows with it",
          o[2] == 0 and o[3] == 0 and i[2] == 3 and i[3] >= i[2],
          "run logs carry error text about platform operations; tenants must not see them",
          f"outside={o[2:]} inside={i[2:]}")


async def a_no_secret(conn, reader, fake, client, captured) -> None:
    sent = [q for _, q in fake.calls if q.get("api_key") == SENTINEL]
    exc_texts: list[str] = []
    # Direct client calls on the hostile fakes — exception text must be clean.
    for code in ("VMKT01BADKEY", "VMKT01TIMEOUT", "VMKT01FIVEXX"):
        try:
            await client.get_series(code)
        except Exception as exc:  # noqa: BLE001
            exc_texts.append(f"{type(exc).__name__}: {exc}")
    try:
        await client.get_observations("VMKT01FAIL")
    except Exception as exc:  # noqa: BLE001
        exc_texts.append(f"{type(exc).__name__}: {exc}")

    # The REAL httpx transport, over httpx.MockTransport (no network): httpx's
    # own exception text embeds the URL, query string and all.
    seen_urls: list[str] = []

    def handler(request: httpx.Request):
        seen_urls.append(str(request.url))
        if request.url.params.get("series_id") == "TIMEOUT":
            raise httpx.ReadTimeout(f"timed out fetching {request.url}", request=request)
        return httpx.Response(400, json={"error_code": 400,
                                         "error_message": f"bad key in {request.url}"})
    real = FredClient(SENTINEL, HttpxTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler))),
                      min_interval=0, sleep=nosleep)
    for code in ("TIMEOUT", "ECHO"):
        try:
            await real.get_series(code)
        except Exception as exc:  # noqa: BLE001
            exc_texts.append(f"{type(exc).__name__}: {exc}")
    await real.aclose()

    async with platform_scope(reader):
        stored = [r["e"] for r in await reader.fetch(
            f"SELECT last_error AS e FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%' AND last_error IS NOT NULL "
            f"UNION ALL SELECT error FROM {RUNS_T} WHERE series_id IN {FIXTURE_IDS} AND error IS NOT NULL")]
    haystack = "\n".join(captured + exc_texts + stored)
    exercised = (len(sent) > 0 and any(SENTINEL in u for u in seen_urls)
                 and any("api_key=***" in s for s in stored) and len(exc_texts) == 6)
    check("A10.1 the sentinel API key appears nowhere in captured stdout/stderr, stored last_error/run errors, or exception text",
          exercised and SENTINEL not in haystack,
          "FRED takes the key in the query string, so any logged URL or echoed error leaks it; the fakes echo it on purpose "
          "and the key was really sent (not a vacuous pass)",
          f"sent={len(sent)} urls_with_key={sum(SENTINEL in u for u in seen_urls)} exc={len(exc_texts)} "
          f"leaked={SENTINEL in haystack}")

    forbidden_names = {"url", "params", "query", "api_key", "request", "response"}
    forbidden_attrs = {"url", "params", "api_key", "_api_key", "request"}
    offenders = []
    files = sorted((API_DIR / "services" / "market_data").glob("*.py")) + [HERE.parent / "market_data_ingest.py"]
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
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                for sub in ast.walk(arg):
                    if (isinstance(sub, ast.Name) and sub.id in forbidden_names) or \
                       (isinstance(sub, ast.Attribute) and sub.attr in forbidden_attrs):
                        offenders.append(f"{path.name}:{node.lineno}")
    check("A10.2 static: no print/log call in the new modules formats a URL, params, request or the key",
          not offenders and len(files) >= 4,
          "the runtime proof only covers the paths the fakes reach; this covers every output call", str(offenders))


async def phase_a(conn, reader) -> None:
    order = await fk_delete_order(reader)
    leftover = await delete_fixtures(conn, order)
    if any(leftover.values()):
        find(f"removed fixture rows left by an earlier interrupted run: {leftover}")
    base_untouched = await untouched_counts(reader)
    base_real = await real_counts(reader)

    fake = FakeTransport()
    client = FredClient(SENTINEL, fake, min_interval=0, sleep=nosleep)
    captured: list[str] = []
    try:
        await a_schema(reader)
        await a_seed_file(reader)
        await a_loader(conn, reader)
        await a_permission_gate(conn, reader)
        await a_adapter(conn, reader, fake, client, captured)
        await a_bitemporal(conn, reader, fake, client, captured)
        await a_decimal_and_isolation(conn, reader, fake, client, captured)
        await a_unique_index(conn, reader)
        await a_read_gate(conn)
        await a_no_secret(conn, reader, fake, client, captured)
    except Exception as exc:  # noqa: BLE001 — reported, then teardown still runs
        check("A.x Phase A ran to completion", False, "an unexpected exception aborted the remaining assertions",
              fred.scrub(f"{type(exc).__name__}: {exc}", SENTINEL))
    finally:
        expected_order = order.index(OBS_T) < order.index(SERIES_T) and order.index(RUNS_T) < order.index(SERIES_T)
        deleted = await delete_fixtures(conn, order)
        remaining = await fixture_counts(reader)
        end_real = await real_counts(reader)
        end_untouched = await untouched_counts(reader)
        check("A11.1 existing tables untouched: portfolio.securities_global, securities_global_prices, public.fx_rates counts equal",
              end_untouched == base_untouched,
              "this sprint must not modify any existing table; indicator data never goes into security pricing",
              f"before={base_untouched} after={end_untouched}")
        check("A11.2 teardown deleted fixtures child-before-parent in an order derived from information_schema",
              expected_order, "deleting the parent first would fail on the FK, or (with a cascade added later) "
              "silently remove more than intended", f"order={order} deleted={deleted}")
        check("A11.3 after teardown, real (non-fixture) row counts in all three tables are exactly as before",
              end_real == base_real,
              "proves Phase A wrote only fixtures and removed only fixtures — never a TRUNCATE",
              f"before={base_real} after={end_real}")
        if any(remaining.values()):
            print(f"[FAIL] A11.4 fixture rows remain after teardown: {remaining}")
            raise SystemExit(2)
        check("A11.4 zero fixture rows remain in any of the three tables (re-read independently)",
              True, "a leftover fixture would surface in real charts and grids")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — live data, after the operator's `market_data_ingest.py all`
# ═══════════════════════════════════════════════════════════════════════════
async def phase_b(reader) -> None:
    notfix = f"series_key NOT LIKE '{PREFIX}%'"
    pending = await reader.fetch(
        f"SELECT series_key, ingest_status FROM {SERIES_T} WHERE {notfix} AND source_provider = 'fred' "
        "AND ingest_status NOT IN ('active', 'invalid_code')")
    nfred = await reader.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE {notfix} AND source_provider = 'fred'")
    check("B1 every source_provider='fred' row is 'active' or 'invalid_code' — none 'pending'",
          nfred > 0 and not pending,
          "a pending FRED row means validate never reached it, so its history silently does not exist",
          f"fred_rows={nfred} offenders={[dict(r) for r in pending][:10]}")

    stats = await reader.fetch(f"""
        SELECT s.series_key, s.last_observation_date, s.units,
               (SELECT count(*) FROM {OBS_T} o WHERE o.series_id = s.id
                  AND o.valid_to IS NULL AND o.system_to IS NULL) AS n,
               (SELECT max(obs_date) FROM {OBS_T} o WHERE o.series_id = s.id
                  AND o.valid_to IS NULL AND o.system_to IS NULL) AS maxd
          FROM {SERIES_T} s WHERE {notfix} AND s.ingest_status = 'active'""")
    empty = [r["series_key"] for r in stats if r["n"] == 0]
    drift = [(r["series_key"], r["last_observation_date"], r["maxd"]) for r in stats
             if r["last_observation_date"] != r["maxd"]]
    check("B2 every 'active' series has ≥1 active observation and last_observation_date = max(obs_date)",
          len(stats) > 0 and not empty and not drift,
          "the chart and the nightly job both trust last_observation_date; drift means a lost write",
          f"active={len(stats)} empty={empty[:10]} drift={drift[:5]}")

    dups = await reader.fetchval(f"""SELECT count(*) FROM (SELECT series_id, obs_date FROM {OBS_T}
        WHERE valid_to IS NULL AND system_to IS NULL GROUP BY 1, 2 HAVING count(*) > 1) d""")
    check("B3 no (series_id, obs_date) has more than one active row",
          dups == 0, "two active values for one date would double-count in every reader", f"duplicates={dups}")

    async with platform_scope(reader):
        ok_runs = {r["series_key"] for r in await reader.fetch(
            f"SELECT DISTINCT s.series_key FROM {RUNS_T} r JOIN {SERIES_T} s ON s.id = r.series_id "
            "WHERE r.run_trigger = 'backfill' AND r.status = 'success'")}
    no_units = [r["series_key"] for r in stats if not r["units"]]
    no_run = [r["series_key"] for r in stats if r["series_key"] not in ok_runs]
    check("B4 every 'active' series has units populated and a backfill 'success' row in indicator_ingest_runs",
          not no_units and not no_run,
          "units come only from FRED validation and the run row is the audit trail — either missing means a step was skipped",
          f"no_units={no_units[:10]} no_success_run={no_run[:10]}")

    for r in await reader.fetch(f"SELECT series_key, source_code, last_error FROM {SERIES_T} "
                                f"WHERE {notfix} AND ingest_status = 'invalid_code' ORDER BY series_key"):
        find(f"invalid_code: {r['series_key']} ({r['source_code']}): {r['last_error']}")

    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        check("B5 spot check DGS10/UNRATE/CPIAUCSL against FRED", False,
              "the live comparison needs FRED_API_KEY", "FRED_API_KEY is not set")
    else:
        client = FredClient(api_key)
        try:
            for code in ("DGS10", "UNRATE", "CPIAUCSL"):
                row = await reader.fetchrow(
                    f"SELECT id, series_key, ingest_status, last_observation_date FROM {SERIES_T} "
                    f"WHERE {notfix} AND source_provider = 'fred' AND source_code = $1", code)
                if row is None or row["ingest_status"] != "active" or row["last_observation_date"] is None:
                    check(f"B5 spot check {code}", False, "a headline series must be active with history",
                          str(dict(row) if row else "not in registry"))
                    continue
                last = row["last_observation_date"]
                stored = await reader.fetchval(
                    f"SELECT value FROM {OBS_T} WHERE series_id = $1 AND obs_date = $2 "
                    "AND valid_to IS NULL AND system_to IS NULL", row["id"], last)
                try:
                    raw = await client.get_observations(code, sort_order="desc", limit=10,
                                                        observation_end=last.isoformat(), max_pages=1)
                    fred_val = next((ingest.parse_value(v) for d, v in raw
                                     if d == last.isoformat() and v.strip() != "."), None)
                    detail = f"date={last} stored={stored} fred={fred_val}"
                except Exception as exc:  # noqa: BLE001
                    fred_val, detail = None, client.scrub(f"{type(exc).__name__}: {exc}")
                check(f"B5 spot check {code}: latest stored observation equals FRED's value for the same date",
                      fred_val is not None and stored == fred_val,
                      "end-to-end proof that what we store is what FRED publishes, digit for digit", detail)
                newer = await client.get_observations(code, sort_order="desc", limit=1, max_pages=1)
                if newer and newer[0][0] > last.isoformat():
                    find(f"{code}: FRED now has {newer[0][0]} (stored through {last}) — expected until mkt02's nightly job")
        finally:
            await client.aclose()

    counts = {r["ingest_status"]: r["n"] for r in await reader.fetch(
        f"SELECT ingest_status, count(*) AS n FROM {SERIES_T} WHERE {notfix} GROUP BY 1 ORDER BY 1")}
    total = await reader.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE {notfix}")
    check("B6 counts by ingest_status (sum equals registry size)",
          sum(counts.values()) == total and total >= 75,
          "the operator's one-line health picture of the registry", f"{counts} total={total}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="also run Phase B against the real ingested data")
    args = parser.parse_args()

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS "
              f"(overwriting any stale ambient copies, e.g. apps/api/.env / ~/.bashrc)")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to ambient DATABASE_URL")

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
        check("DB connect", False, "nothing can be proven without the database", f"{type(exc).__name__}")
        print(f"TOTAL: {PASSED} passed, {FAILED} failed")
        return 1
    try:
        await phase_a(conn, reader)
        if args.live:
            await phase_b(reader)
        else:
            skip("Phase B (live data + FRED spot checks) was not run — pass --live after `market_data_ingest.py all`")
    finally:
        await conn.close()
        await reader.close()
    print(f"TOTAL: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
