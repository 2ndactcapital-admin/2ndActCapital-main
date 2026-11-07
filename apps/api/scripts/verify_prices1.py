"""verify_prices1 — end-of-day prices through the vendor-neutral security-data layer (PRICES1): the price receptor
(a plug-in contract), the first free adapter (Yahoo chart), the store + coverage with reasons, the backfill, the
prices platform job, and the readers that now compute.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_prices1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect``).

NO NETWORK TO ANY DATA SOURCE. Every adapter runs on ``eod.RecordedHttpGet`` (its CURRENT signature: responses,
responder, fail_after): RECORDED Yahoo responses of 2026-11-07 (apps/api/scripts/fixtures/prices1/) prove the parser;
everything that writes runs on SYNTHETIC responses in each source's real format, for fixture securities whose every
ticker / name carries a reserved prefix (ZZP…, "ZZP1 VERIFY") matching no real row (asserted before seeding). httpx's
transport refuses and records any real request; the LLM helpers are stubbed and must never be called.

FIXTURES: three orgs (is_test, asserted) — ORG_P, a fixture PLATFORM org the prices job is seeded into (added to
security_master_jobs.PLATFORM_JOB_ORG_IDS for this run), ORG_A and ORG_B, client orgs; a fixture Super Admin in ORG_P,
an org admin in ORG_A (an internal role) and a MEMBER in ORG_A (client role + view_replication). A fixture provider
'zzp1_vendor' (the paid vendor TEMPLATE under a fixture key: one registry row + an org rank override + one entry in
the layer's code registry — the plug-in proof). The scheduler is driven through the REAL run_scheduler_tick with an
injected instant and only_trigger_ids = the fixture trigger. Real platform jobs are only READ (the prices job must be
OFF).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN, by fixture org / user ids and by fixture
ids (securities — including their system-axis archive copies —, prices, coverage rows, quality events, call log, job
history, the fixture provider and its ranks). Every touched table is re-read for its exact pre-test count BY FIXTURE
IDS ONLY; real rows (prices, live securities, coverage, providers, platform rank, triggers, quality events) are compared
by count + checksum keyed by their real id.

--self-checks-only: git / doc / route self-checks only. --cleanup-only: teardown + leftover check. With
VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
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
import tempfile
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
FIX = HERE / "fixtures" / "prices1"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402
from _verify_total import nested_total  # noqa: E402

SPRINT_KEY = "prices1"
BEFORE_REF = "9049690"   # prices1-work @ merge secmaster2 — the sprint's base
ROUTES: dict = {}        # this sprint adds NO route; it changes what GET /security-data/lookup answers, not its gate
ADAPTERS = ("apps/api/services/security_data/providers/yahoo_chart.py",
            "apps/api/services/security_data/providers/tiingo.py",
            "apps/api/services/security_data/providers/vendor_stub.py")
#: files that must name NO vendor (the platform reads prices only through the layer / the contract)
VENDOR_FREE = ("apps/api/services/security_data/prices.py", "apps/api/services/security_data/providers/eod.py",
               "apps/api/services/security_data/writes.py", "apps/api/services/security_data/layer.py",
               "apps/api/services/networth.py", "apps/api/services/xray.py", "apps/api/services/account_models.py",
               "apps/api/scripts/prices_backfill.py")
VENDOR_WORDS = re.compile(r"yahoo|tiingo|stooq|polygon|alphavantage|alpha vantage|\bfmp\b|zzp1_vendor", re.I)


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ═══ Self-checks (no database) ══════════════════════════════════════════════
TASK1 = (
    ("docs/PRICES1_DISCOVERY.md", ("1a.", "HttpTransportBase", "RETRY_STATUSES", "budget_left", "vendor_slot",
                                   "Gaps for an EOD price adapter"), "1a: the provider contract and its gaps"),
    ("docs/PRICES1_DISCOVERY.md", ("Stooq", "bot wall", "Yahoo", "split-adjusted", "Tiingo", "Alpha Vantage",
                                   "Polygon", "Nasdaq Data Link", "TIINGO_API_KEY", "POLYGON_API_KEY",
                                   "ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "primary", "fallback", "not_allowed",
                                   "01:15 UTC"), "1b: the free sources, the samples, the choice and its licence facts"),
    ("docs/PRICES1_DISCOVERY.md", ("1c.", "29 securities", "28 held", "ACWI", "no benchmark table"),
     "1c: the coverage set, counted"),
    ("docs/PRICES1_DISCOVERY.md", ("1d.", "networth", "xray", "tilts", "backtest", "harvest", "load_holdings",
                                   "hard-wired"), "1d: every price reader and how it asks"),
)
STEPS = ("--dry-run", "prices_backfill.py`", "Security data — prices", "TIINGO_API_KEY")


def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "prices1 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), "this sprint adds no route (the lookup route keeps its gate; only its answer changed)",
          f"{o.where()}; extra={sorted(mine)[:3]}")
    look = [k for k in o.end if k[1] == "/api/v1/security-data/lookup"]
    check(bool(look) and all(k in o.before for k in look), "GET /security-data/lookup exists before and after, same gate",
          f"{look}")


def self_static(check, find):
    for rel, needles, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or _read(rel)
        miss = [n for n in needles if n not in txt]
        check(bool(txt) and not miss, f"Task 1 — {meaning}", f"{rel}: missing {miss}")
    old_base = VS.git_show(BEFORE_REF, "apps/api/services/security_data/providers/base.py") or ""
    old_nightly = VS.git_show(BEFORE_REF, "apps/api/services/security_data/nightly.py") or ""
    check(bool(old_base) and "fetch_eod" not in old_base and "return list(FRED.TREASURY_CURVE)" in old_nightly
          and VS.git_show(BEFORE_REF, "apps/api/services/security_data/providers/eod.py") is None,
          "REPRODUCED before the sprint: no EOD fetch in the contract, the nightly covered nothing for eod_price, no "
          "receptor module", "")
    bad = []
    for rel in VENDOR_FREE:
        for i, ln in enumerate(_read(rel).splitlines(), 1):
            code = ln.split("#", 1)[0]
            if VENDOR_WORDS.search(code) and not code.strip().startswith(('"', "'")):
                bad.append(f"{rel}:{i}")
    check(not bad, "no vendor name in the platform's price code outside the adapter files (prices, receptor, writes, "
          "layer, readers, backfill)", f"{bad[:6]}")
    fx = [rel for rel in ADAPTERS + VENDOR_FREE + ("apps/api/services/security_data/providers/__init__.py",)
          if "zzp1_vendor" in _read(rel)]
    check(not fx, "the fixture vendor key appears in NO application file (plugged in by configuration only)", f"{fx}")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. PRICES1 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    need = ("29 securities", "yahoo_chart", "tiingo", "not_allowed", "FMP_API_KEY", "bot wall", "SCHEMA OK",
            "inactive", "verify_prices1.py")
    check(bool(top) and all(n in top or n in _read("docs/SECURITY_DATA_DESIGN.md") for n in need)
          and all(n in top for n in need[:6]),
          "Task 9: PROJECT_STATUS entry — the source, its licence, the coverage set, the findings",
          f"missing {[n for n in need if n not in top]}")
    design = _read("docs/SECURITY_DATA_DESIGN.md")
    sec = design[design.find("PRICES1 operator steps"):] if "PRICES1 operator steps" in design else ""
    order = [sec.find(s) for s in STEPS]
    check(bool(sec) and -1 not in order and order == sorted(order),
          "Task 9: the design doc lists the operator steps IN ORDER (dry run → real run → switch the job on)",
          f"{list(zip(STEPS, order))}")
    check(all(s in design for s in ("EodSpec", "fetch_eod", "symbol_for", "Plug FMP (or any vendor) in later",
                                    "RecordedHttpGet", "keyed_by")),
          "Task 2/9: the design doc states the receptor contract and the plug-in checklist")
    check("verify_prices1.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    jobs = _read("apps/api/services/security_master_jobs.py")
    check('"system:Security data — prices"' in jobs and '"security_data.prices"' in jobs,
          "the platform job 'Security data — prices' is defined")
    sql = _read("docs/prices1_part1.sql")
    check("security_price_coverage" in sql and "'invalid_value'" in sql and "yahoo_chart" in sql
          and not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b", sql, re.I),
          "the migration is additive (no DELETE / TRUNCATE)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import account_models as AM  # noqa: E402
from services import backtest as BT  # noqa: E402
from services import extraction  # noqa: E402
from services import networth as NW  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_engine as E  # noqa: E402
from services import rep10_common as RC  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import wash_sale as WSM  # noqa: E402
from services import workflow_scheduler as WS  # noqa: E402
from services import xray as XR  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.database import get_pool  # noqa: E402
from services.security_data import layer as L  # noqa: E402
from services.security_data import prices as P  # noqa: E402
from services.security_data import registry as SDR  # noqa: E402
from services.security_data.providers import build_providers  # noqa: E402
from services.security_data.providers import tiingo as TG  # noqa: E402
from services.security_data.providers import yahoo_chart as YC  # noqa: E402
from services.security_data.providers.eod import (EodPriceProvider, EodSourceError, RecordedHttpGet,  # noqa: E402
                                                  contract_problems, registry_row)
from services.security_data.providers.reference_common import FixtureTransport  # noqa: E402
from services.security_data.providers.vendor_stub import VendorTemplateProvider  # noqa: E402
from services.workflow_schedule import next_occurrences  # noqa: E402

TAG = "prices1verify"
TICKER_PREFIX = "ZZP"
NAME_PREFIX = "ZZP1 VERIFY"
FIX_KEY = "zzp1_vendor"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
D = Decimal


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e1{n:03d}")


ORG_P, ORG_A, ORG_B = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_P, ORG_A, ORG_B]
U_SUPER, U_ADMIN_A, U_MEMBER_A, U_ADMIN_B = _id(101), _id(102), _id(103), _id(104)
USERS = {U_SUPER: (ORG_P, "super_admin", "super", "super_admin", "PRICES1 Super Admin"),
         U_ADMIN_A: (ORG_A, "org_admin", "admin-a", "org_admin", "PRICES1 Org Admin A"),
         U_MEMBER_A: (ORG_A, "member", "member-a", "zzp1_viewer", "PRICES1 Member A"),
         U_ADMIN_B: (ORG_B, "org_admin", "admin-b", "org_admin", "PRICES1 Org Admin B")}
ENT_A, HH_A = _id(201), _id(202)
VIEWER_PERMS = ("view_replication",)

#: fixture securities: key → (ticker | None, type, currency)
SECS = {"F": ("ZZPF", "fund", "USD"), "C": ("ZZPC", "equity", "USD"), "G": ("ZZPG", "fund", "USD"),
        "STALE": ("ZZPS", "equity", "USD"), "GAP": ("ZZPP", "equity", "USD"), "SPIKE": ("ZZPK", "equity", "USD"),
        "ZERO": ("ZZPZ", "equity", "USD"), "CUR": ("ZZPE", "equity", "USD"), "NF": ("ZZPN", "equity", "USD"),
        "NT": (None, "equity", "USD"), "R1": ("ZZPR", "equity", "USD"), "M1": ("ZZPM", "equity", "USD"),
        "M2": ("ZZPY", "equity", "USD"), "M3": ("ZZPO", "equity", "USD"), "V1": ("ZZPV", "equity", "USD"),
        **{f"BF{i}": (f"ZZPB{i}", "equity", "USD") for i in range(1, 5)}}

COUNTED: dict = {}
S: dict = {}
NETWORK_CALLS: list[str] = []
LLM_CALLS: list[str] = []
RUN_IDS: list[UUID] = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
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


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), \
            f"timeout after {timeout}s"


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


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


TODAY = last_weekday(datetime.now(timezone.utc).date())
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 22, 0, tzinfo=timezone.utc)
REC_NOW = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)   # when the Yahoo fixtures were recorded


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """The SAME org context the routes set (rls_context_middleware → set_config LOCAL, not super admin)."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


@asynccontextmanager
async def org_ctx(conn, org):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        yield conn


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def fixture_secs(conn) -> list:
    """Fixture securities, INCLUDING their system-axis archive copies (same name) — by name and by ticker."""
    ids = {r["id"] for r in await platform(conn, "SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                           f"{NAME_PREFIX}%")}
    ids |= {r["global_security_id"] for r in await platform(
        conn, "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE "
              "id_type = 'ticker' AND id_value LIKE $1", f"{TICKER_PREFIX}%")}
    return sorted(ids, key=str)


def counted_plan(fx: list, runs: list) -> dict:
    orgs, users = FIXTURE_ORGS, list(USERS)
    by_org = ("org_id = ANY($1::uuid[])", [orgs])
    run_txt = [str(r) for r in runs]
    return {
        "public.organizations": ("id = ANY($1::uuid[])", [orgs]),
        "public.users": ("id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [users, f"%{TAG}%"]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
        "public.roles": by_org,
        "public.entities": by_org,
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
        "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
        "portfolio.assets": by_org, "portfolio.positions": by_org,
        "public.workflow_definitions": by_org, "public.workflow_versions": by_org, "public.workflow_steps": by_org,
        "public.workflow_triggers": by_org, "public.workflow_runs": by_org, "public.workflow_run_steps": by_org,
        "portfolio.securities_global": ("id = ANY($1::uuid[]) OR name LIKE $2", [fx, f"{NAME_PREFIX}%"]),
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[]) OR id_value LIKE $2",
                                                    [fx, f"{TICKER_PREFIX}%"]),
        "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[]) OR provider_key = $2 OR "
                                               "org_id = ANY($3::uuid[])", [fx, FIX_KEY, orgs]),
        "portfolio.security_price_coverage": ("global_security_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])",
                                              [fx, runs]),
        "portfolio.data_quality_events": ("global_security_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[]) OR "
                                          "org_id = ANY($3::uuid[]) OR provider_key = $4", [fx, runs, orgs, FIX_KEY]),
        "portfolio.provider_call_log": ("run_id = ANY($1::uuid[]) OR provider_key = $2", [runs, FIX_KEY]),
        "portfolio.security_data_runs": ("id = ANY($1::uuid[]) OR summary->>'workflow_run_id' = ANY($2::text[]) OR "
                                         "summary->>'run_id' = ANY($2::text[])", [runs, run_txt]),
        "portfolio.data_providers": ("key = $1", [FIX_KEY]),
        "portfolio.data_source_rank": ("provider_key = $1 OR org_id = ANY($2::uuid[])", [FIX_KEY, orgs]),
    }


async def all_runs(conn) -> list:
    return list(RUN_IDS) + [r["id"] for r in await platform(
        conn, "SELECT id FROM workflow_runs WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS)]


async def counts(conn) -> dict:
    fx = await fixture_secs(conn)
    runs = await all_runs(conn)
    out = {}
    for t, (pred, args) in counted_plan(fx, runs).items():
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
    return out


async def real_fingerprints(conn) -> dict:
    """Real rows keyed by their real id; the fixture rows excluded by fixture ids. Excluded columns: none on these
    tables is updated by real activity except workflow_triggers.last_fired_at / occurrence_count (not read)."""
    fx = [str(x) for x in await fixture_secs(conn)]
    orgs = [str(o) for o in FIXTURE_ORGS]
    live = "valid_to IS NULL AND system_to IS NULL"
    return {
        "real prices": await fingerprint(
            conn, "SELECT id, global_security_id, price_date, price, provider_key, org_id, system_to, valid_to FROM "
                  "portfolio.securities_global_prices WHERE NOT (global_security_id = ANY($1::uuid[])) AND "
                  "COALESCE(provider_key, '') <> $2 AND NOT (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'"
                  "::uuid) = ANY($3::uuid[]))", fx, FIX_KEY, orgs),
        "real live securities (id, name, price_coverage)": await fingerprint(
            conn, f"SELECT id, name, price_coverage FROM portfolio.securities_global WHERE {live} AND NOT "
                  "(id = ANY($1::uuid[]))", fx),
        "real coverage rows": await fingerprint(
            conn, "SELECT id, global_security_id, status, valid_to FROM portfolio.security_price_coverage WHERE NOT "
                  "(global_security_id = ANY($1::uuid[]))", fx),
        "real providers": await fingerprint(
            conn, "SELECT id, key, status, lic_internal_use, lic_member_display, lic_redistribution, daily_call_budget "
                  "FROM portfolio.data_providers WHERE key <> $1", FIX_KEY),
        "platform rank": await fingerprint(
            conn, "SELECT id, data_type, provider_key, rank, valid_to FROM portfolio.data_source_rank WHERE "
                  "owner_scope = 'platform'"),
        "real triggers": await fingerprint(
            conn, "SELECT id, org_id, is_active, schedule_cron FROM workflow_triggers WHERE NOT (org_id = "
                  "ANY($1::uuid[]))", orgs),
        "real quality events": await fingerprint(
            conn, "SELECT id, data_type, subject_key, kind, valid_to FROM portfolio.data_quality_events WHERE NOT "
                  "(COALESCE(global_security_id, '00000000-0000-0000-0000-000000000000'::uuid) = ANY($1::uuid[])) "
                  "AND NOT (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid) = ANY($2::uuid[])) AND "
                  "provider_key <> $3 AND NOT (subject_key = ANY($4::text[]))", fx, orgs, FIX_KEY, fx),
    }


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


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs = {r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                                f"{NAME_PREFIX}%")}
        secs |= {r["global_security_id"] for r in await op.fetch(
            "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_type = 'ticker' "
            "AND id_value LIKE $1", f"{TICKER_PREFIX}%")}
        secs = list(secs)
        runs = [r["id"] for r in await op.fetch("SELECT id FROM workflow_runs WHERE org_id = ANY($1::uuid[])",
                                                FIXTURE_ORGS)] + list(RUN_IDS)
        run_txt = [str(r) for r in runs]
        orgs, users = FIXTURE_ORGS, list(USERS)
        by_org = ("org_id = ANY($1::uuid[])", [orgs])
        plan = {t: by_org for t in ("public.workflow_run_steps", "public.workflow_runs", "public.workflow_triggers",
                                    "public.workflow_steps", "public.workflow_versions", "public.workflow_definitions",
                                    "public.domain_events", "portfolio.positions", "portfolio.assets",
                                    "public.entities")}
        plan["public.member_todos"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan["public.audit_log"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan["public.user_roles"] = ("user_id = ANY($1::uuid[])", [users])
        plan["portfolio.securities_global_prices"] = ("global_security_id = ANY($1::uuid[]) OR provider_key = $2 OR "
                                                      "org_id = ANY($3::uuid[])", [secs, FIX_KEY, orgs])
        plan["portfolio.security_price_coverage"] = ("global_security_id = ANY($1::uuid[]) OR run_id = "
                                                     "ANY($2::uuid[])", [secs, runs])
        plan["portfolio.data_quality_events"] = ("global_security_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[]) OR "
                                                 "provider_key = $3 OR subject_key = ANY($4::text[])",
                                                 [secs, runs, FIX_KEY, [str(s) for s in secs]])
        plan["portfolio.provider_call_log"] = ("run_id = ANY($1::uuid[]) OR provider_key = $2", [runs, FIX_KEY])
        plan["portfolio.security_data_runs"] = ("id = ANY($1::uuid[]) OR summary->>'workflow_run_id' = "
                                                "ANY($2::text[]) OR summary->>'run_id' = ANY($2::text[])",
                                                [runs, run_txt])
        plan["portfolio.data_source_rank"] = ("provider_key = $1 OR org_id = ANY($2::uuid[])", [FIX_KEY, orgs])
        plan["portfolio.data_providers"] = ("key = $1", [FIX_KEY])
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [secs])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        cols = {r["t"] for r in await op.fetch(
            "SELECT table_schema || '.' || table_name AS t FROM information_schema.columns WHERE column_name = 'org_id'")}
        plan = {t: v for t, v in plan.items() if t in exists and (v is not by_org or t in cols)}
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan, gucs=(),
                               label="verify_prices1")
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
    fx = await fixture_secs(conn)
    out = {}
    for t, (pred, args) in counted_plan(fx, list(RUN_IDS)).items():
        n = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
        if n:
            out[t] = n
    return out


# ═══ App access + stubs ═════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=600.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_prices1"}}
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


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def no_profile(request, c):   # users.fetch_auth0_profile(request, c) — its current signature
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):   # httpx.AsyncHTTPTransport.handle_async_request(self, request)
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_prices1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    for name in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        if hasattr(extraction, name):
            def make(n):
                async def no_llm(*a, **k):
                    LLM_CALLS.append(n)
                    raise AssertionError(f"verify_prices1: {n} was called")
                return no_llm
            setattr(extraction, name, make(name))
    register_all()
    J.PLATFORM_JOB_ORG_IDS.add(ORG_P)
    J.TRANSPORT_FACTORY = lambda: FixtureTransport({})   # the reference-data boundary: nothing is fetched by this job
    J.PRICES_KWARGS.clear()
    P.TRANSPORT_FACTORY.clear()
    # THE PLUG-IN: the paid vendor template under the fixture key — one entry in the layer's code registry (what one
    # line in build_providers does), a registry row and a rank entry (seeded below). No application code changes.
    L._PROVIDERS = {**build_providers(), FIX_KEY: template(None)}


# ═══ Synthetic responses in each source's real format ═══════════════════════
SYN: dict[str, dict] = {}     # symbol → {"bars": [(date, Decimal)], "currency": str} | {"status": int, "body": str}


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 14, 30, tzinfo=timezone.utc).timestamp())


def yahoo_body(sym: str) -> tuple[int, str]:
    e = SYN.get(sym)
    if e is None:
        return 404, json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                                     "description": "No data found, symbol may be "
                                                                                    "delisted"}}})
    if "status" in e:
        return e["status"], e["body"]
    bars = e["bars"]
    res = {"meta": {"currency": e.get("currency", "USD"), "symbol": sym, "gmtoffset": 0, "dataGranularity": "1d"},
           "timestamp": [_ts(d_) for d_, _ in bars],
           "indicators": {"quote": [{"close": [str(v) for _, v in bars], "open": [str(v) for _, v in bars],
                                     "high": [str(v) for _, v in bars], "low": [str(v) for _, v in bars],
                                     "volume": [1000 for _ in bars]}],
                          "adjclose": [{"adjclose": [str(v) for _, v in bars]}]}}
    # numbers, not strings, exactly as Yahoo sends them (parse_float=Decimal keeps them exact)
    text = json.dumps({"chart": {"result": [res], "error": None}})
    return 200, re.sub(r'"(-?\d+(?:\.\d+)?)"', r"\1", text)


def yahoo_responder(url: str, params: dict):
    return yahoo_body(url.rsplit("/", 1)[-1])


def yahoo(transport=None, **kw) -> YC.YahooChartProvider:
    return YC.YahooChartProvider(transport=transport or RecordedHttpGet(responder=yahoo_responder), min_interval=0.0,
                                 now=lambda: NOW, sleep=_nosleep, **kw)


async def _nosleep(_s):
    return None


def tiingo_responder(url: str, params: dict):
    sym = url.rsplit("/", 2)[-2].upper()
    e = SYN.get(sym)
    if e is None:
        return 404, json.dumps({"detail": f"Error: Ticker '{sym}' not found"})
    if "status" in e:
        return e["status"], e["body"]
    rows = [{"date": f"{d_.isoformat()}T00:00:00.000Z", "close": float(v), "high": float(v), "low": float(v),
             "open": float(v), "volume": 1000, "adjClose": float(v), "adjHigh": float(v), "adjLow": float(v),
             "adjOpen": float(v), "adjVolume": 1000, "divCash": 0.0, "splitFactor": 1.0} for d_, v in e["bars"]]
    return 200, json.dumps(rows)


def template_responder(url: str, params: dict):
    sym = url.rsplit("/", 1)[-1]
    e = SYN.get(sym)
    if e is None:
        return 404, json.dumps({"error": "unknown symbol"})
    if "status" in e:
        return e["status"], e["body"]
    return 200, json.dumps({"symbol": sym, "currency": e.get("currency", "USD"),
                            "bars": [{"date": d_.isoformat(), "close": format(v, "f"), "adj_close": format(v, "f"),
                                      "open": format(v, "f"), "high": format(v, "f"), "low": format(v, "f"),
                                      "volume": "1000"} for d_, v in e["bars"]]})


def template(transport) -> VendorTemplateProvider:
    return VendorTemplateProvider(FIX_KEY, base_url="https://vendor.invalid/v1", transport=transport,
                                  environ={"VENDOR_TEMPLATE_API_KEY": "fixture-not-a-secret"}, min_interval=0.0,
                                  sleep=_nosleep)


def tiingo(transport) -> TG.TiingoProvider:
    return TG.TiingoProvider(transport=transport, environ={"TIINGO_API_KEY": "fixture-not-a-secret"},
                             min_interval=0.0, sleep=_nosleep)


def series(n: int, end: date, fn) -> list[tuple[date, Decimal]]:
    return [(d_, fn(i)) for i, d_ in enumerate(weekdays_back(end, n))]


def build_syn() -> None:
    SYN.clear()
    tk = {k: v[0] for k, v in SECS.items()}
    # F alternates 100 / 101 (71 closes → 70 returns); C flat 100 — tracking error / backtest hand-computable
    SYN[tk["F"]] = {"bars": series(71, TODAY, lambda i: D("100") if i % 2 == 0 else D("101"))}
    SYN[tk["C"]] = {"bars": series(71, TODAY, lambda i: D("100"))}
    stale_end = weekdays_back(TODAY, 21)[0]
    SYN[tk["STALE"]] = {"bars": series(10, stale_end, lambda i: D("20"))}
    gap = series(30, TODAY, lambda i: D("30") + D(i % 2) / 10)
    SYN[tk["GAP"]] = {"bars": gap[:12] + gap[18:]}                          # 6 weekdays missing
    SYN[tk["SPIKE"]] = {"bars": [(d_, D("300") if i == 25 else v) for i, (d_, v) in
                                 enumerate(series(30, TODAY, lambda i: D("100") if i % 2 == 0 else D("100.5")))]}
    SYN[tk["ZERO"]] = {"bars": [(d_, D("0") if i == 4 else v) for i, (d_, v) in
                                enumerate(series(10, TODAY, lambda i: D("40")))]}
    SYN[tk["CUR"]] = {"bars": series(10, TODAY, lambda i: D("50")), "currency": "EUR"}
    SYN[tk["R1"]] = {"bars": series(5, TODAY, lambda i: D("10"))}
    SYN[tk["M1"]] = {"bars": series(3, TODAY, lambda i: D("123.45"))}       # via the template (member allowed)
    SYN[tk["M2"]] = {"bars": series(3, TODAY, lambda i: D("50"))}           # via yahoo (member not allowed)
    old = weekdays_back(TODAY, 10)[0]
    SYN[tk["M3"]] = {"bars": series(2, old, lambda i: D("77.7"))}           # older than the position
    SYN[tk["V1"]] = {"bars": series(5, TODAY, lambda i: D("9.5") + D(i) / 100)}
    for i in range(1, 5):
        SYN[tk[f"BF{i}"]] = {"bars": series(15, TODAY, lambda j, i=i: D(10 * i) + D(j) / 100)}


def item(key: str) -> P.Item:
    t, ty, cur = SECS[key]
    return P.Item(str(S["sec"][key]), {"ticker": t} if t else {}, cur, ["requested"])


def new_run() -> uuid.UUID:
    rid = uuid.uuid4()
    RUN_IDS.append(rid)
    return rid


async def live_prices(conn, key: str, provider: str | None = None) -> list:
    return await platform(conn, "SELECT price_date, price, quality_flags, adjustment_factor, bar, licence, system_to "
                                "FROM portfolio.securities_global_prices WHERE global_security_id = $1 AND "
                                "($2::text IS NULL OR provider_key = $2) AND valid_to IS NULL AND system_to IS NULL "
                                "ORDER BY price_date", S["sec"][key], provider)


async def events_of(conn, key: str) -> dict:
    return {r["kind"]: (json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]) for r in
            await platform(conn, "SELECT kind, detail FROM portfolio.data_quality_events WHERE global_security_id = $1 "
                                 "AND valid_to IS NULL AND system_to IS NULL", S["sec"][key])}


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1d (docs/PRICES1_DISCOVERY.md) and the live state they rest on")
    self_static(check, find)
    items = await P.coverage_set(conn)
    n = len(items)
    srcs = sorted({s for it in items for s in it.sources})
    check(n > 0 and all(it.gsid for it in items) and set(srcs) <= {"position", "model", "replica",
                                                                    "replica_constituent", "option_underlying",
                                                                    "benchmark", "note", "substitution", "backtest"},
          "1c: the coverage set reads (non-empty; every member says why it is needed)", f"{n} securities {srcs}")
    if n != 29:
        find("1c: the coverage set has changed since discovery", f"{n} securities now (29 on 2026-11-07)")


async def y_schema(conn):
    section("[Y] Schema: constraints read from the catalog BEFORE seeding; providers, rank, the job OFF; the role")
    names = ["sec_global_price_positive_chk", "sec_global_price_type_chk", "sec_global_price_upload_chk",
             "sec_global_price_adj_factor_chk", "data_quality_events_kind_chk", "data_quality_events_scope_chk",
             "security_price_coverage_status_chk", "security_price_coverage_platform_chk",
             "security_price_coverage_reason_chk", "data_providers_display_needs_internal_chk",
             "data_providers_redistribution_chk", "data_source_rank_scope_chk", "securities_global_price_coverage_chk",
             "securities_global_prices_global_security_id_fkey"]
    defs = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = ANY($1::text[])", names)}
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])",
        ["uq_sec_global_price_point", "security_price_coverage_live_uq", "data_quality_events_live_uq",
         "data_source_rank_provider_live_uq", "data_source_rank_position_live_uq", "data_providers_key_live_uq"])}
    S["catalog"] = (defs, idx)
    check(len(defs) == len(names) and len(idx) == 6, "constraints read from the catalog (CHECK / UNIQUE / FK)",
          f"missing checks {sorted(set(names) - set(defs))} uniques {sorted(idx)}")
    kinds = defs.get("data_quality_events_kind_chk", "")
    check(all(f"'{k}'" in kinds for k in ("stale", "gap", "outlier", "invalid_value", "currency_mismatch")),
          "the quality-kind CHECK admits every kind the ingest writes", kinds)
    check("price > (0)" in defs.get("sec_global_price_positive_chk", "").replace("::numeric", ""),
          "the store refuses a non-positive price (so a zero close can only be an event)",
          defs.get("sec_global_price_positive_chk", ""))
    u = idx.get("uq_sec_global_price_point", "")
    check("UNIQUE" in u and "provider_key" in u and "system_to IS NULL" in u,
          "one LIVE close per (security, date, type, provider, org) — what makes a re-run idempotent", u)
    pol = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND "
                                                  "tablename = 'security_price_coverage'")}
    check(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, "security_price_coverage has a policy for EVERY operation",
          f"{sorted(pol)}")
    code = build_providers()
    for key, status in (("yahoo_chart", "active"), ("tiingo", "not_connected")):
        p = await platform(conn, "SELECT * FROM portfolio.data_providers WHERE key = $1 AND valid_to IS NULL AND "
                                 "system_to IS NULL", key, fetch="fetchrow")
        lic = code[key].spec.licence
        check(bool(p and p["status"] == status and p["capabilities"] == ["eod_price"] and p["lic_internal_use"] is True
                   and p["lic_member_display"] == "not_allowed" == lic.member_display
                   and p["lic_redistribution"] is False and p["attribution"] == lic.attribution
                   and p["confirmed_on"] and p["daily_call_budget"] and "personal" in (p["terms_note"] or "")),
              f"licence facts recorded for {key} ({status}): internal yes, members not_allowed, no redistribution, "
              "attribution, terms, budget", f"{dict(p) if p else None}")
    rank = [r["provider_key"] for r in await platform(
        conn, "SELECT provider_key FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = "
              "'eod_price' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
    check(rank == ["custodian", "csv_upload", "adviser_entry", "platform_entry", "yahoo_chart", "tiingo"],
          "the platform rank for eod_price: marketdata1's four, then yahoo_chart, then tiingo", f"{rank}")
    spec = J.JOBS["security_data_prices"]
    real = await platform(conn, "SELECT t.is_active, t.schedule_cron, t.timezone FROM workflow_definitions d JOIN "
                                "workflow_triggers t ON t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' "
                                "WHERE d.org_id = $1 AND d.name = $2", J.PLATFORM_ORG_ID, spec.name)
    check(bool(len(real) == 1 and real[0]["is_active"] is False and real[0]["schedule_cron"] == spec.cron),
          "the REAL platform job 'Security data — prices' is seeded and OFF", f"{[dict(r) for r in real]}")
    other = await platform(conn, "SELECT count(*) FROM workflow_definitions d JOIN workflow_triggers t ON "
                                 "t.workflow_definition_id = d.id WHERE d.name = $1 AND t.is_active AND d.org_id <> ALL("
                                 "$2::uuid[])", spec.name, FIXTURE_ORGS, fetch="fetchval")
    check(int(other) == 0, "the prices job is OFF for every real org (no active trigger anywhere)", f"{other}")
    role = await platform(conn, "SELECT r.rolname, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user",
                          fetch="fetchrow")
    S["role_ok"] = bool(role and role["rolbypassrls"] is False)
    check(S["role_ok"], "this script's connection role does NOT bypass RLS (rolbypassrls = false)",
          f"{dict(role) if role else None}")


async def guard_prefix(conn) -> bool:
    n_t = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_type = 'ticker' "
                               "AND id_value LIKE $1", f"{TICKER_PREFIX}%", fetch="fetchval")
    n_n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                         fetch="fetchval")
    n_p = await platform(conn, "SELECT count(*) FROM portfolio.data_providers WHERE key = $1 OR key LIKE 'zzp%'",
                         FIX_KEY, fetch="fetchval")
    n_o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                         fetch="fetchval")
    n_u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                         list(USERS), f"%{TAG}%", fetch="fetchval")
    return check(int(n_t) == 0 and int(n_n) == 0 and int(n_p) == 0 and int(n_o) == 0 and int(n_u) == 0,
                 "reserved fixture prefixes (tickers ZZP…, names 'ZZP1 VERIFY', provider zzp1_vendor, fixture org / user "
                 "ids) match no real row", f"tickers {n_t}, names {n_n}, providers {n_p}, orgs {n_o}, users {n_u}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "platform"), (ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"PRICES1 Verify {nm}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid_view = await rbac.ensure_role(conn, ORG_A, "zzp1_viewer", "PRICES1 verify — view_replication")
        for p_ in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p_])
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname == "zzp1_viewer":
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid_view)
            else:
                await rbac.grant_named_role(conn, uid, org, rname)
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, 'individual', "
                           "$3)", ENT_A, ORG_A, f"{NAME_PREFIX} owner")
        for key, (t, ty, cur) in SECS.items():
            sid = await SG.create_security(conn, name=f"{NAME_PREFIX} {key}", security_type=ty, currency_code=cur,
                                           is_super_admin=True)
            if t:
                await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=t, is_super_admin=True)
            S.setdefault("sec", {})[key] = UUID(str(sid))
        # the fixture vendor: ONE registry row (what eod.registry_row(spec) says) + its rank entry (ORG_A's override)
        row = registry_row(template(None).spec, status="active")
        await conn.execute(
            "INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, "
            "lic_member_display, lic_redistribution, retention_after_cancel, attribution, terms_note, confirmed_on, "
            "daily_call_budget) VALUES ($1, $2, $3, $4, $5::text[], $6, $7, $8, $9, $10, $11, $12, $13)",
            row["key"], f"{NAME_PREFIX} vendor", row["kind"], row["status"], row["capabilities"],
            row["lic_internal_use"], row["lic_member_display"], row["lic_redistribution"],
            row["retention_after_cancel"], row["attribution"], row["terms_note"], TODAY, row["daily_call_budget"])
    for org in (ORG_A, ORG_B):
        async with org_ctx(conn, org):
            for i, k in enumerate([FIX_KEY, "yahoo_chart", "custodian"], start=1):
                await conn.execute("INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope, "
                                   "org_id) VALUES ('eod_price', $1, $2, 'org', $3)", k, i, org)
    flags = await platform(conn, "SELECT (SELECT bool_and(is_test) FROM organizations WHERE id = ANY($1::uuid[])) AS o, "
                                 "(SELECT bool_and(is_test) FROM users WHERE id = ANY($2::uuid[])) AS u",
                           FIXTURE_ORGS, list(USERS), fetch="fetchrow")
    S["seeded"] = check(bool(flags["o"] is True and flags["u"] is True and len(S.get("sec", {})) == len(SECS)),
                        "fixture orgs and users are is_test = true (asserted); fixture securities seeded",
                        f"{dict(flags)} {len(S.get('sec', {}))}")
    S["seeded"] = bool(await check_fixture_roles(conn)) and S["seeded"]


async def check_fixture_roles(conn) -> bool:
    from services import authz as _AZ
    bad = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, account_role, _nm, rname, _full) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            pr = await _AZ.load_principal(conn, uid)
            want_super = rname == "super_admin"
            ok = rname in names and bool(pr and pr.is_super_admin) is want_super
            if uid == U_MEMBER_A:
                ok = ok and bool(pr) and not pr.is_internal and not pr.is_staff
            if uid == U_ADMIN_A:
                ok = ok and bool(pr) and pr.is_internal
            if not ok:
                bad.append((str(uid), names, account_role, bool(pr and pr.is_super_admin),
                            bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides from (Super Admin; org admin = an "
                 "internal role; the member is NOT internal)", f"{bad}")


# ── [Y] the plug-in contract ────────────────────────────────────────────────
async def contract_suite(name: str, make, known: str, unknown: str, fail_transport, *, start: date, end: date,
                         expect_first=None):
    """The SAME behavioural tests for every adapter: declared contract, a known symbol, an unknown one, a failing
    request, the injected transport only, the symbol mapping."""
    rec = RecordedHttpGet(responder=make["responder"]) if "responder" in make else RecordedHttpGet(make["responses"])
    a = make["build"](rec)
    probs = contract_problems(a)
    check(probs == [] and isinstance(a, EodPriceProvider), f"contract [{name}]: declares everything the platform reads",
          f"{probs}")
    got = await a.fetch_eod([known, unknown], start, end)
    k, u = got.get(known), got.get(unknown)
    bars = k.bars if k else []
    ok = bool(k and k.found and bars and all(isinstance(b.close, Decimal) and b.close > 0 for b in bars)
              and [b.day for b in bars] == sorted(b.day for b in bars) and all(start <= b.day <= end for b in bars)
              and k.currency)
    if expect_first is not None and ok:
        ok = (bars[0].day, bars[0].close) == expect_first
    check(ok, f"contract [{name}]: a known symbol → bars ascending inside [start, end], Decimal closes > 0, a currency",
          f"{k and (k.found, len(bars), k.currency, bars[:1])}")
    check(bool(u and u.found is False and u.reason and set(got) == {known, unknown}),
          f"contract [{name}]: an unknown symbol → found=False with the source's reason (one series per symbol, "
          "no exception)", f"{u and u.reason}")
    check(rec.requests == a.calls == 2 and NETWORK_CALLS == [],
          f"contract [{name}]: every call went through the INJECTED transport (2 requests, none to the network)",
          f"{rec.requests} {a.calls} {NETWORK_CALLS[:2]}")
    bad = make["build"](fail_transport)
    raised = None
    try:
        await bad.fetch_eod([known], start, end)
    except EodSourceError as exc:
        raised = str(exc)
    check(bool(raised), f"contract [{name}]: a refused request raises EodSourceError (the run holds; nothing guessed)",
          f"{raised}")
    check(a.symbol_for({"ticker": "BRK.B"}) is not None and a.symbol_for({}) is None,
          f"contract [{name}]: symbol_for maps a master ticker and refuses a security without one",
          f"{a.symbol_for({'ticker': 'BRK.B'})}")
    return a


async def y_contract(conn):
    section("[Y] Plug-in contract: the free adapter, the fallback and the PAID VENDOR TEMPLATE pass the same tests")
    files = {s: (FIX / f"yahoo_chart_{s}.json").read_text(encoding="utf-8")
             for s in ("AAPL", "VFIAX", "SPAXX", "NVDA_split", "KO_div", "ZZNOSUCH")}
    resp = {YC.url_for("AAPL"): (200, files["AAPL"]), YC.url_for("ZZNOSUCHX"): (404, files["ZZNOSUCH"])}
    await contract_suite("yahoo_chart, recorded 2026-11-07", {
        "responses": resp, "build": lambda t: YC.YahooChartProvider(transport=t, min_interval=0.0, now=lambda: REC_NOW,
                                                                     sleep=_nosleep)},
        "AAPL", "ZZNOSUCHX", RecordedHttpGet({YC.url_for("AAPL"): (403, "Forbidden")}),
        start=date(2026, 10, 1), end=date(2026, 11, 7), expect_first=(date(2026, 10, 27), D("347.1000")))
    build_syn()
    tk = SECS["V1"][0]
    await contract_suite("tiingo (synthetic, documented format — no key to record with)", {
        "responder": tiingo_responder, "build": tiingo}, tk, "ZZPXXX",
        RecordedHttpGet(responder=lambda u, p: (403, json.dumps({"detail": "Please supply a token"}))),
        start=TODAY - timedelta(days=30), end=TODAY)
    await contract_suite("paid vendor template under the fixture key", {
        "responder": template_responder, "build": template}, tk, "ZZPXXX",
        RecordedHttpGet(responder=lambda u, p: (401, "{}")), start=TODAY - timedelta(days=30), end=TODAY)
    # recorded parser facts
    nv = YC.parse_chart(files["NVDA_split"], "NVDA", date(2024, 6, 1), date(2024, 6, 30), now=REC_NOW)
    by = {b.day: b for b in nv.bars}
    check(bool(nv.found and by[date(2024, 6, 3)].close == D("1150.0000") and by[date(2024, 6, 14)].close == D("131.8800")
               and nv.events["splits"] == [{"date": "2024-06-10", "ratio": "10"}]
               and by[date(2024, 6, 3)].adjustment_factor is not None and by[date(2024, 6, 3)].adjustment_factor < 1),
          "Yahoo (recorded NVDA, split 10:1 on 2024-06-10): the UNADJUSTED close is restored before the split "
          "(1150.0000, not 115.00), unchanged after it; the adjustment factor is kept",
          f"{by.get(date(2024, 6, 3))} {nv.events}")
    mm = YC.parse_chart(files["SPAXX"], "SPAXX", date(2026, 10, 1), date(2026, 11, 7), now=REC_NOW)
    mf = YC.parse_chart(files["VFIAX"], "VFIAX", date(2026, 10, 1), date(2026, 11, 7), now=REC_NOW)
    ko = YC.parse_chart(files["KO_div"], "KO", date(2026, 8, 1), date(2026, 11, 7), now=REC_NOW)
    check(bool(mm.found and len(mm.bars) == 1 and mm.bars[0].close == D("1.0000") and mm.bars[0].open is None
               and mf.found and len(mf.bars) == 9 and mf.bars[0].volume is None and mf.currency == "USD"
               and ko.events["dividends"] and ko.events["dividends"][0]["amount"] == "0.53"),
          "Yahoo (recorded): money market NAV 1.0000 with its zero open dropped; a mutual fund's 9 NAVs (no volume); "
          "a dividend event", f"{mm.bars[:1]} {len(mf.bars)} {ko.events.get('dividends')}")
    # end to end: the template under the fixture key, plugged in by configuration only, through the rank walk
    S["tpl"] = template(RecordedHttpGet(responder=template_responder))
    out = await P.ingest(await get_pool(), S["tpl"], [item("V1")], TODAY - timedelta(days=30), TODAY, today=TODAY,
                         run_id=new_run())
    async with org_ctx(conn, ORG_A):
        g = await L.get(conn, ORG_A, "eod_price", str(S["sec"]["V1"]), as_of=TODAY, purpose="member_display")
    want = SYN[tk]["bars"][-1][1]
    check(bool(out.found == 1 and out.inserted == 5 and g.found and g.provider_key == FIX_KEY and g.value == want
               and g.attribution == template(None).spec.licence.attribution),
          "plug-in proven: the template's prices flow through the SAME ingest into the store and the rank walk answers "
          "with them (member display allowed by ITS registry row) — no application code names it",
          f"{out.summary()} {g.as_dict()}")


# ── [Y] free adapter: quality events, licence facts ────────────────────────
async def y_quality(conn):
    section("[Y] Free adapter on synthetic Yahoo responses: quality events (stale, gap, spike, zero, currency); "
            "licence facts on every stored row")
    pool = await get_pool()
    keys = ["F", "C", "STALE", "GAP", "SPIKE", "ZERO", "CUR", "NF", "NT", "R1"]
    S["y"] = yahoo()
    out = await P.ingest(pool, S["y"], [item(k) for k in keys], TODAY - timedelta(days=400), TODAY, today=TODAY,
                         run_id=new_run())
    S["first_ingest"] = out
    check(bool(out.securities == len(keys) and out.found == 8 and out.not_found == 1 and out.no_symbol == 1
               and not out.errors and out.calls == 9),
          "one ingest pass: 10 securities, 8 found, 1 unknown to the source, 1 without a ticker (no call made for "
          "it), 9 calls, no error", f"{out.summary()}")
    ev = {k: await events_of(conn, k) for k in ("STALE", "GAP", "SPIKE", "ZERO", "CUR", "F")}
    check("stale" in ev["STALE"] and ev["STALE"]["stale"].get("limit") == 5, "a stale series → a 'stale' event",
          f"{ev['STALE']}")
    check("gap" in ev["GAP"] and ev["GAP"]["gap"].get("weekdays_missing") == 6, "a gapped series → a 'gap' event (6 "
          "weekdays missing)", f"{ev['GAP']}")
    sp = await live_prices(conn, "SPIKE")
    spike_day = SYN[SECS["SPIKE"][0]]["bars"][25][0]
    flagged = [r["price_date"] for r in sp if "outlier" in (r["quality_flags"] or [])]
    check(bool("outlier" in ev["SPIKE"] and spike_day in flagged and len(sp) == 30),
          "a spiked series → an 'outlier' event AND the bar stored flagged 'outlier' (never silently)",
          f"{ev['SPIKE']} flagged {flagged}")
    zr = await live_prices(conn, "ZERO")
    zero_day = SYN[SECS["ZERO"][0]]["bars"][4][0]
    check(bool("invalid_value" in ev["ZERO"] and len(zr) == 9 and zero_day not in {r["price_date"] for r in zr}),
          "a zero close → an 'invalid_value' event and NOT stored (the other 9 are)", f"{ev['ZERO']} {len(zr)}")
    cr = await live_prices(conn, "CUR")
    check(bool("currency_mismatch" in ev["CUR"] and not cr), "a source currency (EUR) other than the security's (USD) "
          "→ a 'currency_mismatch' event and nothing stored", f"{ev['CUR']} {len(cr)}")
    check(not ev["F"], "a clean series raises no event (the negative case)", f"{ev['F']}")
    fr = await live_prices(conn, "F")
    lic = fr[0]["licence"] if fr else {}
    lic = json.loads(lic) if isinstance(lic, str) else lic
    check(bool(len(fr) == 71 and lic.get("member_display") == "not_allowed" and lic.get("internal_use") is True
               and lic.get("attribution") == "Prices: Yahoo Finance"
               and all(r["adjustment_factor"] == D("1.000000") for r in fr)),
          "every stored row carries the provider's licence facts (internal, not for members, attribution) and the "
          "adjustment factor", f"{len(fr)} {lic}")


# ── [Y] store: idempotent, restatement, coverage with reasons ──────────────
async def y_store(conn):
    section("[Y] Store: an idempotent re-run writes nothing; a restatement is a new SYSTEM version; coverage flags "
            "with reasons")
    pool = await get_pool()
    keys = ["F", "C", "SPIKE", "R1"]
    before = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE global_security_id = "
                                  "ANY($1::uuid[])", [S["sec"][k] for k in keys], fetch="fetchval")
    again = await P.ingest(pool, yahoo(), [item(k) for k in keys], TODAY - timedelta(days=400), TODAY, today=TODAY,
                           run_id=new_run())
    after = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE global_security_id = "
                                 "ANY($1::uuid[])", [S["sec"][k] for k in keys], fetch="fetchval")
    check(bool(again.inserted == 0 and again.restated == 0 and again.unchanged == 71 + 71 + 30 + 5
               and int(before) == int(after) > 0),
          "the identical re-run writes nothing (0 inserted, 0 restated, every close unchanged; row count equal)",
          f"{again.summary()} {before} → {after}")
    last_day = SYN[SECS["R1"][0]]["bars"][-1][0]
    SYN[SECS["R1"][0]]["bars"][-1] = (last_day, D("10.5"))
    rs = await P.ingest(pool, yahoo(), [item("R1")], TODAY - timedelta(days=30), TODAY, today=TODAY, run_id=new_run())
    rows = await platform(conn, "SELECT price, system_to, valid_to FROM portfolio.securities_global_prices WHERE "
                                "global_security_id = $1 AND price_date = $2 ORDER BY system_from", S["sec"]["R1"],
                          last_day)
    check(bool(rs.restated == 1 and rs.inserted == 0 and len(rows) == 2 and rows[0]["price"] == D("10")
               and rows[0]["system_to"] is not None and rows[0]["valid_to"] is None
               and rows[1]["price"] == D("10.5") and rows[1]["system_to"] is None),
          "a changed close is a restatement on the SYSTEM axis: the old row kept with system_to, the new one live",
          f"{rs.summary()} {[dict(r) for r in rows]}")
    cov = {k: await platform(conn, "SELECT c.status, c.reason, c.provider_key, c.points, s.price_coverage FROM "
                                   "portfolio.security_price_coverage c JOIN portfolio.securities_global s ON s.id = "
                                   "c.global_security_id AND s.valid_to IS NULL AND s.system_to IS NULL WHERE "
                                   "c.global_security_id = $1 AND c.valid_to IS NULL AND c.system_to IS NULL",
                             S["sec"][k], fetch="fetchrow") for k in ("F", "NF", "NT", "CUR")}
    f = cov["F"]
    check(bool(f and f["status"] == f["price_coverage"] == "has_series" and f["points"] == 71
               and f["reason"].startswith("71 daily closes") and "yahoo_chart" in f["reason"]),
          "coverage: has_series with its reason (71 daily closes A..B from yahoo_chart)", f"{f and dict(f)}")
    nf = cov["NF"]
    check(bool(nf and nf["status"] == nf["price_coverage"] == "no_public_source"
               and "Yahoo has no data for ZZPN" in nf["reason"]),
          "coverage: no_public_source with the SOURCE's own answer as the reason", f"{nf and dict(nf)}")
    nt = cov["NT"]
    check(bool(nt and nt["status"] == "no_public_source" and "asks by ticker" in nt["reason"]),
          "coverage: a security with no ticker → no_public_source, reason 'asks by ticker and the master holds none'",
          f"{nt and dict(nt)}")
    check(cov["CUR"] is None, "coverage: a currency mismatch stores nothing and leaves the flag alone (unknown)",
          f"{cov['CUR'] and dict(cov['CUR'])}")
    arch = await platform(conn, "SELECT count(*) FILTER (WHERE system_to IS NOT NULL AND price_coverage = 'unknown') AS a, "
                                "count(*) FILTER (WHERE system_to IS NULL) AS l FROM portfolio.securities_global WHERE "
                                "name = $1", f"{NAME_PREFIX} F", fetch="fetchrow")
    sid_kept = await platform(conn, "SELECT price_coverage FROM portfolio.securities_global WHERE id = $1",
                              S["sec"]["F"], fetch="fetchval")
    check(bool(int(arch["a"]) == 1 and int(arch["l"]) == 1 and sid_kept == "has_series"),
          "the flag changed on the SYSTEM axis: the prior version archived (system_to), the referenced id KEPT",
          f"{dict(arch)} {sid_kept}")


# ── [Y] backfill: dry run + resume ─────────────────────────────────────────
async def y_backfill(conn):
    section("[Y] Backfill: --dry-run counts (no call); a forced stop then --resume continues without duplicates")
    pool = await get_pool()
    items = [item(f"BF{i}") for i in range(1, 5)]
    rec0 = RecordedHttpGet(responder=yahoo_responder)
    dry = await P.backfill(pool, yahoo(rec0), items, years=1, today=TODAY, dry_run=True)
    check(bool(dry["dry_run"] and dry["to_fetch"] == 4 and dry["estimated_calls"] == 4 and rec0.requests == 0
               and dry["daily_budget"] is not None),
          "--dry-run: 4 securities, 4 estimated calls, the budget — and NO request made, nothing written", f"{dry}")
    ck_path = pathlib.Path(tempfile.mkdtemp(prefix="prices1verify-")) / "ck.json"
    ck = P.Checkpoint(ck_path, "yahoo_chart:1")
    first = await P.backfill(pool, yahoo(RecordedHttpGet(responder=yahoo_responder, fail_after=2)), items, years=1,
                             today=TODAY, checkpoint=ck, batch=1, progress=lambda m: None)
    RUN_IDS.append(UUID(first["run_id"]))
    done1 = json.loads(ck_path.read_text())["done"]
    check(bool(first["status"] == "partial" and first["errors"] and len(done1) == 2 and first["deferred"] == 2),
          "a forced stop after 2 calls: the run is partial, the checkpoint holds the 2 committed securities",
          f"{ {k: first.get(k) for k in ('status', 'errors', 'deferred', 'found')} } {len(done1)}")
    ck2 = P.Checkpoint(ck_path, "yahoo_chart:1")
    rec2 = RecordedHttpGet(responder=yahoo_responder)
    second = await P.backfill(pool, yahoo(rec2), items, years=1, today=TODAY, checkpoint=ck2, batch=1,
                              progress=lambda m: None)
    RUN_IDS.append(UUID(second["run_id"]))
    agg = await platform(conn, "SELECT count(*) AS n, count(DISTINCT (global_security_id, price_date)) AS d FROM "
                               "portfolio.securities_global_prices WHERE global_security_id = ANY($1::uuid[]) AND "
                               "valid_to IS NULL AND system_to IS NULL", [S["sec"][f"BF{i}"] for i in range(1, 5)],
                         fetch="fetchrow")
    runs = await platform(conn, "SELECT status, summary FROM portfolio.security_data_runs WHERE id = ANY($1::uuid[]) "
                                "ORDER BY started_at", [UUID(first["run_id"]), UUID(second["run_id"])])
    sm = [json.loads(r["summary"]) if isinstance(r["summary"], str) else r["summary"] for r in runs]
    check(bool(second["status"] == "success" and second["already_done"] == 2 and second["to_fetch"] == 2
               and rec2.requests == 2 and int(agg["n"]) == int(agg["d"]) == 4 * 15),
          "--resume: skips the 2 done, fetches the other 2 (2 requests); 60 live closes, no duplicate",
          f"{ {k: second.get(k) for k in ('status', 'already_done', 'to_fetch')} } {dict(agg)} {rec2.requests}")
    check(bool(len(runs) == 2 and all(s.get("job") == "prices_backfill" for s in sm)
               and [r["status"] for r in runs] == ["partial", "success"]),
          "each backfill run is a row in the job history (security_data_runs, summary.job = prices_backfill)",
          f"{[(r['status'], s.get('job')) for r, s in zip(runs, sm)]}")


# ── [Y] readers ─────────────────────────────────────────────────────────────
def te_by_hand(prices: list[Decimal]) -> Decimal:
    """√(sample variance of daily simple returns × 252) — the active series is the fund's own (the constituent is
    flat). Written out here, independent of the code path, at 50 digits."""
    with localcontext() as ctx:
        ctx.prec = 50
        r = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
        m = sum(r, D(0)) / len(r)
        var = sum(((x - m) ** 2 for x in r), D(0)) / (len(r) - 1)
        return (var * 252).sqrt().quantize(RC.Q, rounding=ROUND_HALF_EVEN)


async def y_readers(conn):
    section("[Y] Readers: with a series they compute (tracking error, backtest, X-ray harvest value, drift, roll-up), "
            "without one they still say 'needs prices'")
    pool = await get_pool()
    F, C, G = (str(S["sec"][k]) for k in ("F", "C", "G"))
    # roll-up BEFORE the M prices exist: entered value everywhere
    async with org_ctx(conn, ORG_A):
        for key, qty, mv in (("M1", 10, 1000), ("M2", 4, 180), ("G", 2, 77), ("M3", 3, 210)):
            asset = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) VALUES "
                               "($1, $2, $3, $4, 'equity')", asset, ORG_A, S["sec"][key], f"{NAME_PREFIX} asset {key}")
            await conn.execute(
                "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity, market_value, "
                "ownership_basis, authority, source_system) VALUES ($1, $2, $3, $4, $5, $6, 'units', 'manual', 'manual')",
                ORG_A, ENT_A, asset, TODAY - timedelta(days=3), D(qty), D(mv))
            S.setdefault("asset", {})[key] = str(asset)
        v0, _ = await NW.value_lines(conn, str(ORG_A), TODAY, [str(ENT_A)])
    b0 = {ln["asset_id"]: (ln["basis"], ln["value"]) for ln in v0}
    check(bool(len(b0) == 4 and all(b == "entered_value" for b, _ in b0.values())),
          "roll-up BEFORE a series: every listed line at its entered value (bugfix4)", f"{b0}")
    await P.ingest(pool, template(RecordedHttpGet(responder=template_responder)), [item("M1"), item("M3")],
                   TODAY - timedelta(days=30), TODAY, today=TODAY, run_id=new_run())
    await P.ingest(pool, yahoo(), [item("M2")], TODAY - timedelta(days=30), TODAY, today=TODAY, run_id=new_run())
    async with org_ctx(conn, ORG_A):
        vm, _ = await NW.value_lines(conn, str(ORG_A), TODAY, [str(ENT_A)])
        vi, _ = await NW.value_lines(conn, str(ORG_A), TODAY, [str(ENT_A)], price_purpose="internal")
    bm = {ln["asset_id"]: (ln["basis"], ln["value"]) for ln in vm}
    bi = {ln["asset_id"]: (ln["basis"], ln["value"]) for ln in vi}
    a = S["asset"]
    check(bm.get(a["M1"]) == ("price", D("1234.50")), "roll-up switches to the price: 10 × 123.45 = 1234.50 (a source "
          "whose licence allows members)", f"{bm.get(a['M1'])}")
    check(bool(bm.get(a["M2"]) == ("entered_value", D("180")) and bi.get(a["M2"]) == ("price", D("200"))),
          "roll-up for a member keeps the entered value 180 where the only close is internal-only (Yahoo); the "
          "adviser view (internal) uses it: 4 × 50 = 200", f"{bm.get(a['M2'])} {bi.get(a['M2'])}")
    check(bool(bm.get(a["G"]) == ("entered_value", D("77")) and bm.get(a["M3"]) == ("entered_value", D("210"))),
          "no series → entered value (77); a close OLDER than the position's own date never replaces it (210)",
          f"{bm.get(a['G'])} {bm.get(a['M3'])}")
    line = next((ln for ln in vm if ln["asset_id"] == a["M1"]), {})
    check(bool((line.get("price") or {}).get("provider_key") == FIX_KEY and (line.get("price") or {}).get("as_of")
               == TODAY.isoformat() and "estimate" in (line.get("price") or {}).get("label", "")
               and (line.get("price") or {}).get("entered_value") == "1000"),
          "the priced line shows its source, as-of and 'estimate' (and the entered value it replaced)",
          f"{line.get('price')}")
    # drift (the holdings every proposal is built on)
    async with org_ctx(conn, ORG_A):
        hs = {str(S["sec"]["M2"]): E.Holding(key=str(S["sec"]["M2"]), label="M2", quantity=D(4), market_value=D(180),
                                             price=D(45), price_as_of=TODAY - timedelta(days=3),
                                             price_source="account_position"),
              G: E.Holding(key=G, label="G", quantity=D(2), market_value=D(77), price=D("38.5"),
                           price_as_of=TODAY - timedelta(days=3), price_source="account_position")}
        await AM._fresher_closes(conn, ORG_A, hs)
    h2, hg = hs[str(S["sec"]["M2"])], hs[G]
    check(bool(h2.price == D("50").quantize(E.QTY) and h2.market_value == D(200) and h2.price_source == "security_price"
               and h2.price_as_of == TODAY and hg.price == D("38.5") and hg.price_source == "account_position"),
          "drift: a fresher close values the holding (4 × 50 = 200, 'security_price'); without one it keeps its own "
          "valuation", f"{h2} {hg}")
    # tracking error + harvest value (the X-ray's fund analysis), backtest
    orig = XR.model_weights

    async def fixture_weights(conn_, version_id):   # XR.model_weights(conn, version_id) — its current signature
        return {C: D(1)} if version_id == "zzp1-model" else {}

    XR.model_weights = fixture_weights
    try:
        acct = {"household_id": HH_A, "primary_entity_id": ENT_A, "tax_status": "taxable",
                "registration_type": "individual"}
        cache = {str(HH_A): {"st": D("0.40"), "lt": D("0.20"), "profile_id": "fixture", "tax_year": TODAY.year,
                             "as_of": TODAY.isoformat()}}
        lots = [{"quantity": D(10), "adjusted_cost": D(1500), "original_cost": D(1500), "term": "long",
                 "acquired_date": "2020-01-02"}]

        def blank():
            return {"tracking_error": XR.NEEDS_PRICES, "harvest_benefit": XR.NEEDS_PRICES,
                    "needs_prices_missing": dict(XR.NEEDS_PRICES_MISSING)}

        with_s, without = blank(), blank()
        async with org_ctx(conn, ORG_A):
            await XR._priced_estimates(conn, ORG_A, with_s, F, {"live_version_id": "zzp1-model"}, lots, D(10), TODAY,
                                       False, acct, cache, WSM, None)
            await XR._priced_estimates(conn, ORG_A, without, G, {"live_version_id": "zzp1-model"}, lots, D(10), TODAY,
                                       False, acct, cache, WSM, None)
            hist, _prov = await BT.histories(conn, ORG_A, [C, F], TODAY - timedelta(days=200), TODAY)
    finally:
        XR.model_weights = orig
    fprices = [v for _, v in SYN[SECS["F"][0]]["bars"]]
    want_te = te_by_hand(fprices)
    det = with_s.get("tracking_error_detail") or {}
    check(bool(det.get("estimated") is True and D(det.get("tracking_error")) == want_te
               and with_s["tracking_error"].startswith(f"{(want_te * 100).quantize(D('0.01'))}% a year (estimate — "
                                                       "yahoo_chart, as of")),
          f"X-ray tracking error vs the replica = {want_te} (hand-computed: √(sample var of the fund's 70 returns × 252)); "
          "shown with source and as-of", f"{with_s['tracking_error']} {det.get('tracking_error')}")
    hd = with_s.get("harvest_benefit_detail") or {}
    check(bool(hd.get("amount") == "100.00" and hd.get("price") == "100.0000" and hd.get("provider_key") == "yahoo_chart"
               and with_s["harvest_benefit"].startswith("100.00 (estimate")),
          "X-ray harvest value of switching = 100.00 (hand-computed: 10 × 100 − 1500 = −500 long-term × 20 %), at the "
          "latest MARKET close", f"{with_s['harvest_benefit']} {hd}")
    check(bool(without["tracking_error"] == XR.NEEDS_PRICES and without["harvest_benefit"] == XR.NEEDS_PRICES
               and "tracking_error" in without["needs_prices_missing"]),
          "without a series the X-ray still says 'not estimated — needs prices' (both figures)", f"{without}")
    days = sorted(hist.get(F, {}))
    bt = BT.simulate(days, {C: hist.get(C, {})}, hist.get(F, {}), lambda d_: {C: D(1)}, cadence="none", harvest=None,
                     notional=D(1000)) if days else {}
    stt = bt.get("statistics") or {}
    with localcontext() as ctx:
        ctx.prec = 50
        want_fr = (fprices[-1] / fprices[0] - 1).quantize(RC.Q)
    check(bool(len(days) == 71 and stt.get("replica_return") == format(D(0).quantize(RC.Q), "f")
               and stt.get("fund_return") == format(want_fr, "f") and stt.get("end_value") == "1000.00"
               and stt.get("tracking_error") == format(want_te, "f")),
          "backtest on the stored series (through the layer): replica return 0, fund return and tracking error exactly "
          "as hand-computed, end value 1000.00", f"{len(days)} {stt}")
    S["xray_with"] = with_s


# ── [Y] licence ─────────────────────────────────────────────────────────────
async def y_licence(conn):
    section("[Y] Licence: a not_allowed provider's price is hidden from a member, labelled 'internal' for staff — the "
            "identical request both ways")
    path = (f"/api/v1/security-data/lookup?data_type=eod_price&subject={S['sec']['M2']}&as_of={TODAY.isoformat()}"
            "&purpose=internal")
    cm, om = await api(U_MEMBER_A, "GET", path)
    cs, os_ = await api(U_ADMIN_A, "GET", path)
    rm, rs = om.get("result") or {}, os_.get("result") or {}
    check(bool(cm == 200 and rm.get("found") is False and om.get("purpose") == "member_display"
               and "does not allow showing values to members" in json.dumps(rm.get("tried"))),
          "member: the Yahoo-only price is NOT shown (answered for member display whatever purpose was sent)",
          f"{cm} {json.dumps(om)[:300]}")
    check(bool(cs == 200 and rs.get("found") is True and D(rs.get("value") or "0") == D(50)
               and rs.get("display") == "internal"
               and rs.get("provider_key") == "yahoo_chart"),
          "staff (org admin): the SAME request shows 50, labelled 'internal'", f"{cs} {json.dumps(os_)[:300]}")
    path1 = path.replace(str(S["sec"]["M2"]), str(S["sec"]["M1"]))
    c1, o1 = await api(U_MEMBER_A, "GET", path1)
    r1 = o1.get("result") or {}
    check(bool(c1 == 200 and r1.get("found") is True and r1.get("display") == "member"
               and D(r1.get("value") or "0") == D("123.45")),
          "member: a source whose licence allows it IS shown (the negative of the rule)", f"{c1} {json.dumps(o1)[:200]}")
    x = {"funds": [json.loads(json.dumps(S["xray_with"], default=str))]}
    xm = XR.member_price_filter(json.loads(json.dumps(x)), staff=False)
    xs = XR.member_price_filter(json.loads(json.dumps(x)), staff=True)
    fm, fs = xm["funds"][0], xs["funds"][0]
    check(bool(fm["tracking_error"] == XR.NEEDS_PRICES and fm["harvest_benefit"] == XR.NEEDS_PRICES
               and "tracking_error_detail" not in fm and fs["tracking_error"].endswith("[internal]")
               and fs["tracking_error_detail"].get("display") == "internal" and fs["harvest_benefit"].endswith("[internal]")),
          "X-ray: the internal-only tracking error / harvest value revert to 'needs prices' for a member and read "
          "'[internal]' for staff", f"{fm['tracking_error']} | {fs['tracking_error']}")


# ── [Y] isolation ───────────────────────────────────────────────────────────
async def y_isolation(conn):
    section("[Y] Isolation both directions under a non-bypassing role")
    if not S.get("role_ok"):
        check(False, "isolation needs a role with rolbypassrls = false", "see the schema section")
        return
    day = TODAY
    for org, px in ((ORG_A, D("11.11")), (ORG_B, D("22.22"))):
        async with org_ctx(conn, org):
            await conn.execute("INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                               "currency_code, price_type, source, provider_key, as_of, licence, org_id) VALUES ($1, $2, "
                               "$3, 'USD', 'close', $4, $4, now(), '{}'::jsonb, $5)", S["sec"]["G"], day, px, FIX_KEY,
                               org)
    q = ("SELECT org_id, price FROM portfolio.securities_global_prices WHERE global_security_id = $1 AND org_id IS NOT "
         "NULL AND valid_to IS NULL AND system_to IS NULL")
    a_rows = await scoped(conn, ORG_A, q, S["sec"]["G"])
    b_rows = await scoped(conn, ORG_B, q, S["sec"]["G"])
    check(bool([r["org_id"] for r in a_rows] == [ORG_A] and [r["org_id"] for r in b_rows] == [ORG_B]),
          "org A reads its own org price and NOT org B's; org B its own and NOT org A's", f"{a_rows} {b_rows}")
    async with org_ctx(conn, ORG_A):
        ga = await L.get(conn, ORG_A, "eod_price", str(S["sec"]["G"]), as_of=day)
    async with org_ctx(conn, ORG_B):
        gb = await L.get(conn, ORG_B, "eod_price", str(S["sec"]["G"]), as_of=day)
    check(bool(ga.found and ga.value == D("11.11") and gb.found and gb.value == D("22.22")),
          "the rank walk answers each org with ITS value (both directions)", f"{ga.value} {gb.value}")
    wb = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.data_source_rank WHERE org_id = $1", ORG_B,
                      fetch="fetchval")
    wa = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.data_source_rank WHERE org_id = $1", ORG_A,
                      fetch="fetchval")
    check(int(wb) == 0 and int(wa) == 0, "neither org reads the other's rank override (both directions)",
          f"{wb} {wa}")
    refused = None
    try:
        await scoped(conn, ORG_A, "INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                                  "price_type, provider_key, org_id) VALUES ($1, $2, 1, 'close', $3, $4)",
                     S["sec"]["G"], day - timedelta(days=1), FIX_KEY, ORG_B, fetch="execute")
    except asyncpg.InsufficientPrivilegeError as exc:
        refused = type(exc).__name__
    except asyncpg.PostgresError as exc:
        refused = f"{type(exc).__name__}: {exc}"
    check(bool(refused and "InsufficientPrivilege" in refused), "org A cannot write a price into org B (RLS refuses)",
          f"{refused}")
    plat = None
    try:
        await scoped(conn, ORG_A, "INSERT INTO portfolio.security_price_coverage (global_security_id, status, reason) "
                                  "VALUES ($1, 'unknown', 'verify')", S["sec"]["G"], fetch="execute")
    except asyncpg.PostgresError as exc:
        plat = type(exc).__name__
    check(plat == "InsufficientPrivilegeError", "an org context cannot write the platform coverage table", f"{plat}")


# ── [Y] the job ─────────────────────────────────────────────────────────────
async def y_job(conn):
    section("[Y] Job: 'Security data — prices' fires through the scheduler (injected instant), holds + alerts on a "
            "forced failure; OFF for real orgs")
    pool = await get_pool()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        seeded = await J.ensure_platform_jobs(conn, org_id=ORG_P, created_by=U_SUPER, active=True,
                                              jobs={"security_data_prices": J.JOBS["security_data_prices"]})
    spec = J.JOBS["security_data_prices"]
    trig = seeded["security_data_prices"]["trigger_id"]
    J.PRICES_KWARGS.update(only_gsids=[str(S["sec"]["F"]), str(S["sec"]["C"])], keys=["yahoo_chart"],
                           include_test_orgs=True, today=TODAY)
    now = datetime.now(timezone.utc)

    async def fire(after):
        occ = next_occurrences(schedule_cron=spec.cron, timezone_name=spec.timezone, after_utc=after, count=1)[0]
        res = await WS.run_scheduler_tick(conn, pool, now_utc=occ + timedelta(minutes=5), only_trigger_ids=[trig],
                                          log=lambda m: None)
        return occ, res

    P.TRANSPORT_FACTORY["yahoo_chart"] = lambda: RecordedHttpGet(responder=yahoo_responder)
    try:
        occ, res = await fire(now)
    finally:
        P.TRANSPORT_FACTORY.clear()
    fired = res.fired[0] if res.fired else None
    steps = await platform(conn, "SELECT rs.status, rs.result FROM workflow_run_steps rs WHERE rs.workflow_run_id = $1",
                           fired["run_id"]) if fired else []
    outs = [json.loads(s["result"]) if isinstance(s["result"], str) else s["result"] for s in steps]
    hd = ((outs[0] or {}).get("handler_data") or {}) if outs else {}
    gf = ((hd.get("providers") or {}).get("yahoo_chart") or {}).get("gap_fill") or {}
    check(bool(fired and fired["status"] == "completed" and len(steps) == 1 and hd.get("coverage_set") == 2
               and gf.get("found") == 2 and gf.get("inserted") == 0 and not hd.get("errors")),
          "the job fires through the REAL scheduler tick and completes: gap fill for the 2 covered securities "
          "(already stored → nothing new), counts as the step output", f"{res.summary()} {str(hd)[:300]}")
    hist = await platform(conn, "SELECT status, summary FROM portfolio.security_data_runs WHERE summary->>'workflow_run_id' "
                                "= $1", str(fired["run_id"]) if fired else "", fetch="fetch")
    check(bool(hist and hist[0]["status"] == "success"), "…and writes one row in the job history", f"{len(hist)}")
    P.TRANSPORT_FACTORY["yahoo_chart"] = lambda: RecordedHttpGet(responder=lambda u, p: (403, "Forbidden"))
    try:
        occ2, res2 = await fire(occ + timedelta(minutes=1))
    finally:
        P.TRANSPORT_FACTORY.clear()
    held = next((e.get("run_id") for e in res2.errors if str(e.get("trigger_id")) == str(trig)), None)
    row = await platform(conn, "SELECT status, error_detail, org_id FROM workflow_runs WHERE id = $1", held,
                         fetch="fetchrow") if held else None
    check(bool(held and row and row["status"] == "held" and row["org_id"] == ORG_P
               and "HTTP 403" in (row["error_detail"] or "")),
          "a forced source failure (HTTP 403) HOLDS the run with the reason", f"{res2.summary()} "
                                                                              f"{row and (row['error_detail'] or '')[:200]}")
    todos = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'workflow_run_held' AND related_id = $1 "
                                 "AND status = 'open'", held) if held else []
    check(bool(held and {t["user_id"] for t in todos} == {U_SUPER}), "…and alerts the Super Admin (and no one else)",
          f"{[str(t['user_id']) for t in todos]}")
    J.PRICES_KWARGS.clear()
    c, o = await api(U_SUPER, "GET", "/api/v1/admin/security-data/master/jobs")
    jobs = {j.get("key"): j for j in (o.get("jobs") or [])}
    pj = jobs.get("security_data_prices") or {}
    check(bool(c == 200 and pj and pj.get("trigger_active") is False and pj.get("fetches")),
          "the job switch screen lists 'Security data — prices' (what it fetches) and it is OFF in the platform org",
          f"{c} {str(pj)[:200]} {str(o)[:200] if c != 200 else ''}")


async def y_real_rows(conn):
    section("[Y] Real rows unchanged except the platform price store fixtures (count + checksum, keyed by real id)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after[k] == v, f"{k} unchanged (count + checksum)", f"{v[0]} → {after[k][0]}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_marketdata1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_marketdata1.py")], REPO, timeout=2700, env=env)
    t = nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(t.ok and not fails), "verify_marketdata1.py: 0 FAIL (its own final TOTAL line + exit code)",
          f"{t.detail()}; {fails[:3]} {err[-200:] if not t.ok else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    log: list[str] = []
    try:
        log = await teardown_retrying()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await close_conn(conn)
    check(not left, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}; TODAY = {TODAY.isoformat()}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = None
    try:
        await run("pre-teardown", teardown_retrying)
        left0 = await leftovers(conn)
        check(not left0, "pre-run cleanup: zero fixture rows before seeding", f"{left0}")
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if not S.get("catalog") or not await guard_prefix(conn):
            check(False, "seeding needs the catalog read and the reserved prefixes free", "every DB section skipped")
        else:
            pre = await counts(conn)
            S["real"] = await real_fingerprints(conn)
            await run("seed", seed, conn)
            if S.get("seeded"):
                await run("contract", y_contract, conn)
                await run("quality", y_quality, conn)
                if S.get("first_ingest"):
                    await run("store", y_store, conn)
                    await run("readers", y_readers, conn)
                    if S.get("xray_with"):
                        await run("licence", y_licence, conn)
                    else:
                        check(False, "the licence section needs the readers section's X-ray figures", "skipped")
                    await run("job", y_job, conn)
                else:
                    check(False, "store, readers, licence and job need the first ingest", "the quality section failed")
                await run("backfill", y_backfill, conn)
                await run("isolation", y_isolation, conn)
            else:
                check(False, "seed completed", "every fixture-scoped section skipped")
            await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
        check(LLM_CALLS == [], "no LLM helper was called", f"{LLM_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        L._PROVIDERS = None
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(not left, "zero fixture rows remain (securities + archive copies, prices, coverage, quality events, "
                            "call log, job history, provider, ranks, assets, positions, workflow rows, todos, orgs, "
                            "users)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in pre:
                    check(post[t] == pre[t], f"{t} at its pre-test count (by fixture ids)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after[k] == S["real"][k], f"{k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
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
