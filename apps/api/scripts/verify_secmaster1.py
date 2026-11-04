"""verify_secmaster1 — the security master from free public sources (SECMASTER1): five reference-data adapters,
the matcher / upsert, the 13(f) list, the one-time load's machinery and four platform jobs on the workflow engine.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_secmaster1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is
set before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect``).

NO NETWORK TO ANY DATA SOURCE: every source is a recorded fixture through ``FixtureTransport`` (the adapters'
injected boundary), httpx's transport refuses and records any real request. Parsing is proven on RECORDED samples
of the real files (apps/api/scripts/fixtures/secmaster1/); the matcher and jobs run on SYNTHETIC files in the
real formats whose every symbol / CUSIP / FIGI carries a reserved prefix (ZZSM…, 9ZZSM…, BBGZZSM…) matching no
real row (asserted before seeding).

FIXTURES: two orgs (is_test, asserted) — ORG_P, a fixture PLATFORM org the jobs are seeded into (added to
security_master_jobs.PLATFORM_JOB_ORG_IDS for this run), and ORG_A, a client org with an org admin; a fixture Super
Admin in ORG_P; a 13(f) quarter '1999q1'. The scheduler is driven through the REAL run_scheduler_tick with an
injected instant and ``only_trigger_ids`` = the fixture triggers (a real trigger is never fired or claimed:
asserted by fingerprint). The real platform jobs (Hollisworks org) are only READ.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN, keyed by the fixture orgs / users and
by fixture ids (securities, identifiers, attributes, 13(f) rows, review items, OpenFIGI answers, source files, call
log and job-history rows of the fixture runs); every counted table re-read for its exact pre-test count; real rows
compared by count + checksum.

--self-checks-only: git / route self-checks only. --cleanup-only: teardown + leftover check. With VERIFY_NESTED=1
the regression section prints one '[SKIP] nested run'.
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
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
FIX = HERE / "fixtures" / "secmaster1"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "secmaster1"
BEFORE_REF = "ff54ef0"   # secmaster1-work @ perms1 — the sprint's base
_M = "/api/v1/admin/security-data/master"
ROUTES = {
    ("GET", f"{_M}/status"): "super_admin",
    ("GET", f"{_M}/jobs"): "super_admin",
    ("POST", f"{_M}/jobs/{{job_key}}/run-now"): "super_admin",
    ("POST", f"{_M}/runs/{{run_id}}/close"): "super_admin",
    ("GET", f"{_M}/review"): "super_admin",
    ("POST", f"{_M}/review/{{review_id}}/decision"): "super_admin",
}
OWN_FILES = ("apps/api/services/security_master_load.py", "apps/api/services/security_master_jobs.py",
             "apps/api/routers/security_master.py")


def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "secmaster1 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate (super_admin) at the sprint's end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "secmaster1's routes all still exist today with the same gate", f"{drift[:4]}")


def self_code(check, find):
    load = VS.own_text(SPRINT_KEY, OWN_FILES[0]) or ""
    check(bool("executemany" in load and not re.search(r"\bDELETE\s+FROM\b", load, re.I)),
          "the matcher writes in bulk (executemany) and issues no DELETE FROM", f"{len(load)} chars")
    jobs = VS.own_text(SPRINT_KEY, OWN_FILES[1]) or ""
    check('REQUIRED_PERMISSION = "run_platform_jobs"' in jobs and "workflow_invocable=True" in jobs and "tier=2" in jobs,
          "the job actions are workflow-invocable tier 2 verbs behind a non-catalog (Super Admin only) permission")
    sched = VS.own_text(SPRINT_KEY, "apps/api/services/workflow_scheduler.py") or ""
    check("only_trigger_ids=None" in sched, "run_scheduler_tick's only_trigger_ids is additive (default None)")


def self_checks(check, find):
    self_routes(check, find)
    self_code(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import security_master_load as M  # noqa: E402
from services import security_master_symbols as SYM  # noqa: E402
from services import security_master_sync as SS  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_scheduler as WS  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.security_data.providers import finance_database as FD  # noqa: E402
from services.security_data.providers import nasdaq_trader as NT  # noqa: E402
from services.security_data.providers import openfigi as OF  # noqa: E402
from services.security_data.providers import sec_13f_list as F13  # noqa: E402
from services.security_data.providers import sec_tickers as SEC  # noqa: E402
from services.security_data.providers.reference_common import FixtureTransport, ParseStats  # noqa: E402
from services.security_data.providers.reference_common import SourceRecord as Rec  # noqa: E402
from services.workflow_schedule import next_occurrences  # noqa: E402

TAG = "secmaster1verify"
PREFIXES = ("ZZSM", "9ZZSM", "US9ZZSM", "BBGZZSM")
NAME_PREFIX = "ZZSM VERIFY"
QUARTER = "1999q1"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
PROVIDERS = ("nasdaq_trader", "sec_tickers", "openfigi", "sec_13f_list", "finance_database")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000053c{n:03d}")


ORG_P, ORG_A = _id(1), _id(2)
FIXTURE_ORGS = [ORG_P, ORG_A]
U_SUPER, U_ADMIN = _id(101), _id(102)
USERS = {U_SUPER: (ORG_P, "super_admin", "super", None, "SECMASTER1 Super Admin"),
         U_ADMIN: (ORG_A, "org_admin", "admin", "org_admin", "SECMASTER1 Org Admin")}


def cus(p8: str) -> str:
    return p8 + str(SYM.cusip_check_digit(p8))


C_MAPA, C_NEW, C_MISS, C_OPT, C_DEL = cus("9ZZSM00A"), cus("9ZZSM00N"), cus("9ZZSM00X"), cus("9ZZSM00O"), cus("9ZZSM00D")
C_OLD, C_MOVE = cus("9ZZSM0M1"), cus("9ZZSM0M2")

COUNTED = ("public.users", "public.organizations", "public.member_todos", "public.workflow_definitions",
           "public.workflow_versions", "public.workflow_steps", "public.workflow_triggers", "public.workflow_runs",
           "public.workflow_run_steps", "portfolio.securities_global", "portfolio.securities_global_identifiers",
           "portfolio.security_reference_attributes", "portfolio.sec_13f_list", "portfolio.security_master_review",
           "portfolio.openfigi_mappings", "portfolio.security_master_source_files", "portfolio.provider_call_log",
           "portfolio.security_data_runs", "portfolio.security_master_checkpoints")

S: dict = {}
NETWORK_CALLS: list[str] = []
RUN_IDS: list[UUID] = []      # SyncContext run ids this script mints (call log / source files / openfigi rows)
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
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


_FX_IDENT = " OR ".join(f"id_value LIKE '{p}%'" for p in PREFIXES)


async def fixture_secs(conn) -> list:
    ids = {r["id"] for r in await platform(conn, "SELECT id FROM portfolio.securities_global WHERE name LIKE $1 "
                                                 "OR name LIKE 'ZZSM%'", f"{NAME_PREFIX}%")}
    ids |= {r["global_security_id"] for r in await platform(
        conn, f"SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE {_FX_IDENT}")}
    return sorted(ids, key=str)


async def real_fingerprints(conn) -> dict:
    """Rows this sprint's verify must never touch, keyed by each table's real primary key (id). Nothing excluded
    but the fixture rows themselves; the operator's own load (if it runs concurrently) is the only legitimate
    writer and would show here — say so rather than hide it."""
    fx = [str(x) for x in await fixture_secs(conn)]
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {"securities": await fingerprint(conn, "SELECT id, name, security_type, merged_into_id, canonical_id, valid_to, "
                                                  "system_to FROM portfolio.securities_global WHERE NOT (id = ANY($1::uuid[]))", fx),
           "identifiers": await fingerprint(conn, "SELECT id, global_security_id, id_type, id_value, is_primary, valid_to "
                                                   "FROM portfolio.securities_global_identifiers WHERE NOT "
                                                   "(global_security_id = ANY($1::uuid[]))", fx),
           "attributes": await fingerprint(conn, "SELECT id, global_security_id, field, value, provider_key, valid_to FROM "
                                                  "portfolio.security_reference_attributes WHERE NOT "
                                                  "(global_security_id = ANY($1::uuid[]))", fx),
           "real_triggers": await fingerprint(conn, "SELECT id, is_active, last_fired_at, occurrence_count FROM "
                                                     "workflow_triggers WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
           "real_platform_jobs": await fingerprint(conn, "SELECT d.id, d.name, v.id, v.bpmn_xml FROM workflow_definitions d "
                                                          "JOIN workflow_versions v ON v.workflow_definition_id = d.id "
                                                          "WHERE d.org_id = $1", J.PLATFORM_ORG_ID),
           "providers": await fingerprint(conn, "SELECT id, key, status, lic_internal_use, lic_member_display, "
                                                 "lic_redistribution FROM portfolio.data_providers")}
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
        secs = {r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE 'ZZSM%'")}
        secs |= {r["global_security_id"] for r in await op.fetch(
            f"SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE {_FX_IDENT}")}
        secs |= {r["global_security_id"] for r in await op.fetch(
            "SELECT DISTINCT global_security_id FROM portfolio.sec_13f_list WHERE quarter = $1 AND "
            "global_security_id IS NOT NULL", QUARTER)}
        secs = list(secs)
        runs = [r["id"] for r in await op.fetch("SELECT id FROM workflow_runs WHERE org_id = ANY($1::uuid[])",
                                                FIXTURE_ORGS)] + list(RUN_IDS)
        run_txt = [str(r) for r in runs]
        orgs, users = FIXTURE_ORGS, list(USERS)
        by_org = ("org_id = ANY($1::uuid[])", [orgs])
        plan = {t: by_org for t in ("public.workflow_run_steps", "public.workflow_runs", "public.workflow_triggers",
                                    "public.workflow_steps", "public.workflow_versions", "public.workflow_definitions",
                                    "public.domain_events", "public.agent_proposals")}
        plan["public.member_todos"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan["public.audit_log"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan["portfolio.security_master_review"] = (
            "global_security_id = ANY($1::uuid[]) OR subject_key LIKE '%ZZSM%' OR run_id = ANY($2::uuid[]) OR "
            "decided_by = ANY($3::uuid[])", [secs, runs, users])
        plan["portfolio.security_reference_attributes"] = ("global_security_id = ANY($1::uuid[]) OR run_id = "
                                                           "ANY($2::uuid[])", [secs, runs])
        plan["portfolio.sec_13f_list"] = ("quarter = $1 OR global_security_id = ANY($2::uuid[])", [QUARTER, secs])
        plan["portfolio.security_master_source_files"] = (
            "run_id = ANY($1::uuid[]) OR (provider_key = 'sec_13f_list' AND file_key = $2)", [runs, QUARTER])
        plan["portfolio.openfigi_mappings"] = ("run_id = ANY($1::uuid[]) OR id_value LIKE 'ZZSM%' OR id_value LIKE "
                                               "'9ZZSM%'", [runs])
        plan["portfolio.provider_call_log"] = ("run_id = ANY($1::uuid[])", [runs])
        sd_ids = [UUID(str(x)) for x in S.get("sd_run_ids", [])]
        plan["portfolio.security_data_runs"] = ("id = ANY($1::uuid[]) OR summary->>'workflow_run_id' = ANY($2::text[])",
                                                [runs + sd_ids, run_txt])
        plan["portfolio.security_master_checkpoints"] = ("load_id = ANY($1::uuid[])", [runs])
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [secs])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        cols = {r["t"] for r in await op.fetch(
            "SELECT table_schema || '.' || table_name AS t FROM information_schema.columns WHERE column_name = 'org_id'")}
        plan = {t: v for t, v in plan.items() if t in exists and (v is not by_org or t in cols)}
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan, gucs=(),
                               label="verify_secmaster1")
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
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        (f"SELECT count(*) FROM portfolio.securities_global_identifiers WHERE {_FX_IDENT}", []),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'ZZSM%'", []),
        ("SELECT count(*) FROM portfolio.sec_13f_list WHERE quarter = $1", [QUARTER]),
        ("SELECT count(*) FROM portfolio.security_master_review WHERE subject_key LIKE '%ZZSM%'", []),
        ("SELECT count(*) FROM portfolio.openfigi_mappings WHERE id_value LIKE 'ZZSM%' OR id_value LIKE '9ZZSM%'", []),
        ("SELECT count(*) FROM portfolio.security_master_source_files WHERE provider_key = 'sec_13f_list' AND "
         "file_key = $1", [QUARTER]),
        ("SELECT count(*) FROM workflow_runs WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM workflow_triggers WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM member_todos WHERE org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
         [FIXTURE_ORGS, list(USERS)]),
        ("SELECT count(*) FROM portfolio.provider_call_log WHERE run_id = ANY($1::uuid[])", [list(RUN_IDS)]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=600.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_secmaster1"}}
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

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_secmaster1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    register_all()
    J.PLATFORM_JOB_ORG_IDS.add(ORG_P)
    J.TRANSPORT_FACTORY = lambda: fixture_transport()
    J.SYNC_OVERRIDES.clear()
    J.SYNC_OVERRIDES.update({"universe_scope_prefix": "ZZSM"})
    J.NIGHTLY_KWARGS.clear()
    J.NIGHTLY_KWARGS.update({"steps": ()})    # marketdata1's nightly runs with no network step


# ═══ Synthetic files in the real formats (every symbol reserved) ════════════
def footer(d: date) -> str:
    return f"File Creation Time: {d:%m%d%Y}18:05"


def nasdaq_files(d: date, *, drop=()) -> dict:
    nl = ["Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"]
    for sym, name, etf in (("ZZSMA", "ZZSM Alpha Inc. - Common Stock", "N"), ("ZZSMF", "ZZSM Index Fund ETF", "Y"),
                           ("ZZSMAPL", "ZZSM Apple-shaped Inc. - Common Stock", "N"),
                           ("ZZSMDL", "ZZSM Delisting Co - Common Stock", "N")):
        if sym not in drop:
            nl.append(f"{sym}|{name}|Q|N|N|100|{etf}|N")
    nl += ["ZZSMT|ZZSM TEST STOCK|G|Y|N|100|N|N", footer(d) + "|||||||"]
    ol = ["ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
          "ZZSMK.B|ZZSM Klass Inc Class B Common Stock|N|ZZSMK.B|N|100|N|ZZSMK.B",
          "ZZSMP$A|ZZSM Pref Corp Depositary Shares Series A Preferred|N|ZZSMPpA|N|100|N|ZZSMP-A",
          "ZZSMIDX|ZZSM Clash Corp Common Stock|N|ZZSMIDX|N|100|N|ZZSMIDX",
          "ZZSMTT|ZZSM TEST ETF|P|ZZSMTT|Y|100|Y|ZZSMTT", footer(d) + "||||||"]
    return {NT.NASDAQ_LISTED_URL: "\n".join(nl), NT.OTHER_LISTED_URL: "\n".join(ol)}


SEC_DOC = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
    [999000001, "ZZSM ALPHA INC", "ZZSMA", "Nasdaq"], [999000002, "ZZSM KLASS INC", "ZZSMK-B", "NYSE"],
    [999000003, "ZZSM OTC CO", "ZZSMO", "OTC"], [999000004, "ZZSM NO EXCHANGE CO", "ZZSMNX", None]]}


def line13f(cusip, issuer, desc, status="", star=" "):
    st = {"added": "*A*", "deleted": "*D*"}.get(status, "   ")
    return (cusip + star + issuer.ljust(30)[:30] + desc.ljust(27)[:27] + st).ljust(79) + "E"


F13_URL = f"https://www.sec.gov/files/investment/13flist{QUARTER}.txt"
F13_FILE = "\n".join([line13f(C_MAPA, "ZZSM ALPHA INC", "COM", star="*"), line13f(C_NEW, "ZZSM NEWCO INC", "COM", "added"),
                      line13f(C_MISS, "ZZSM MISSING CORP", "COM"), line13f(C_OPT, "ZZSM ALPHA INC", "CALL"),
                      line13f(C_DEL, "ZZSM DELETED CO", "COM", "deleted")])
F13_PAGE = f'<li><a href="/files/investment/13flist{QUARTER}.txt">{QUARTER}</a></li>'
FD_HEAD_EQ = ("symbol,name,summary,currency,sector,industry_group,industry,exchange,mic,market,country,state,city,zipcode,"
              "website,market_cap,isin,cusip,figi,composite_figi,shareclass_figi,delisted")
FD_HEAD_ETF = "symbol,name,currency,summary,category_group,category,family,exchange,mic,isin,delisted"
FD_EQ = "\n".join([FD_HEAD_EQ,
                   'ZZSMA,ZZSM Alpha Inc.,"ZZSM Alpha makes fixtures.\nIt spans two lines, as real summaries do.",USD,'
                   'Information Technology,Software & Services,Software,NMS,XNAS,NASDAQ Global Select,United States,,,,,'
                   'Large Cap,,,,,,False',
                   "ZZSMQ,ZZSM Unknown Co,,USD,Financials,Banks,Banks,NMS,XNAS,NASDAQ,United States,,,,,Small Cap,,,,,,False",
                   "ZZSMZ,ZZSM Gone Co,,USD,Energy,Energy,Oil,NMS,XNAS,NASDAQ,United States,,,,,Small Cap,,,,,,True"])
FD_ETF = "\n".join([FD_HEAD_ETF, 'ZZSMF,ZZSM Index Fund ETF,USD,"Tracks a fixture.",Equities,Large Blend,ZZSM Funds,NMS,XNAS,,False'])


FIGI_CALLS: list = []   # (request jobs, stub answer) per OpenFIGI request — printed in the [Y] 13(f) detail


def figi_responder(url, payload):
    out = []
    FIGI_CALLS.append((payload, out))
    for job in payload:
        v = job["idValue"]
        hit = {"ZZSMA": ("BBGZZSM0000A", "ZZSMA", "Common Stock", "ZZSM ALPHA INC"),
               "ZZSMF": ("BBGZZSM0000F", "ZZSMF", "ETP", "ZZSM INDEX FUND ETF"),
               C_MAPA: ("BBGZZSM0000A", "ZZSMA", "Common Stock", "ZZSM ALPHA INC"),
               C_NEW: ("BBGZZSM0000N", "ZZSMN", "Common Stock", "ZZSM NEWCO INC")}.get(v)
        if hit is None:
            out.append({"warning": "No identifier found."})
        else:
            f, t, ty, nm = hit
            out.append({"data": [{"figi": f, "compositeFIGI": f, "ticker": t, "exchCode": "US", "securityType2": ty,
                                  "securityType": ty, "marketSector": "Equity", "name": nm}]})
    return 200, {"ratelimit-remaining": "24"}, out


def fixture_transport(d: date | None = None, *, drop=(), fail_urls=()) -> FixtureTransport:
    files = {**nasdaq_files(d or date(2026, 11, 2), drop=drop), SEC.URL: json.dumps(SEC_DOC), F13.INDEX_URL: F13_PAGE,
             F13_URL: F13_FILE}
    for code in FD.US_EQUITY_FILES:
        files[FD.url("equities", code)] = FD_EQ if code == "NMS" else FD_HEAD_EQ
    for code in FD.US_ETF_FILES:
        files[FD.url("etfs", code)] = FD_ETF if code == "NMS" else FD_HEAD_ETF
    return FixtureTransport(files, responder=figi_responder, fail_urls=fail_urls)


def ctx(pool, transport, today=date(2026, 11, 2)) -> SS.SyncContext:
    rid = uuid.uuid4()
    RUN_IDS.append(rid)
    return SS.SyncContext(pool=pool, transport=transport, run_id=rid, today=today, throttle=False,
                          universe_scope_prefix="ZZSM")


async def gsid_of(conn, id_type, value):
    return await platform(conn, "SELECT global_security_id FROM portfolio.securities_global_identifiers WHERE "
                                "id_type = $1 AND id_value = $2 AND valid_to IS NULL AND system_to IS NULL",
                          id_type, value, fetch="fetchval")


async def attr(conn, gsid, field, provider):
    return await platform(conn, "SELECT value FROM portfolio.security_reference_attributes WHERE global_security_id = $1 "
                                "AND field = $2 AND provider_key = $3 AND valid_to IS NULL AND system_to IS NULL",
                          gsid, field, provider, fetch="fetchval")


async def master_counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in (
        "portfolio.securities_global", "portfolio.securities_global_identifiers",
        "portfolio.security_reference_attributes", "portfolio.security_master_review", "portfolio.sec_13f_list")}


# ═══ [Y] sections ═══════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    doc = (REPO / "docs" / "SECMASTER1_DISCOVERY.md").read_text(encoding="utf-8")
    for h in ("## 1a", "## 1b", "## 1c", "## 1d", "## 1e"):
        check(h in doc, f"discovery doc has {h}")
    check(bool("JerBouma" in doc and "ratelimit-policy" in doc and "*D*" in doc and "File Creation Time" in doc),
          "1a records each source's real format (FinanceDatabase owner, OpenFIGI limits, 13(f) status flags, footer)")
    live = await platform(conn, "SELECT security_type, count(*) AS n FROM portfolio.securities_global WHERE valid_to IS "
                                "NULL AND system_to IS NULL GROUP BY 1")
    find("1b — the live master at verify time", f"{ {r['security_type']: r['n'] for r in live} }")
    dup = await platform(conn, "SELECT id_value FROM portfolio.securities_global_identifiers WHERE id_type = 'ticker' AND "
                               "valid_to IS NULL AND system_to IS NULL GROUP BY 1 HAVING count(DISTINCT "
                               "global_security_id) > 1 LIMIT 5")
    if dup:
        find("1b — tickers held by more than one security (each is ambiguous for a ticker match)",
             f"{[r['id_value'] for r in dup]}")
    check(bool(J.PLATFORM_ORG_ID == UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")),
          "1c — the platform jobs live in the Hollisworks platform org")
    check(SYM.normalize_symbol("BRK-B") == SYM.normalize_symbol("BRK/B") == SYM.normalize_symbol("brk.b") == "BRK.B"
          and SYM.normalize_symbol("ASB$E") == SYM.normalize_symbol("ASB-PE") == "ASB.PRE"
          and SYM.normalize_symbol("AAC.W") == "AAC.WS" and SYM.normalize_symbol("AACIU") == "AACIU",
          "1d — one canonical symbol form, idempotent")
    idx = {r["indexname"] for r in await platform(conn, "SELECT indexname FROM pg_indexes WHERE indexname LIKE '%trgm'")}
    check({"idx_sec_global_name_trgm", "idx_sec_global_ident_value_trgm"} <= idx, "1e — trigram indexes for search")


async def y_schema(conn):
    section("[Y] Schema landed, RLS every operation, five providers with licence facts")
    tables = ("security_reference_attributes", "security_master_source_files", "sec_13f_list", "security_master_review",
              "openfigi_mappings", "security_master_checkpoints")
    for t in tables:
        pol = await platform(conn, "SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' AND "
                                   "tablename = $1", t)
        check({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol}, f"portfolio.{t}: a policy per operation",
              f"{sorted(r['cmd'] for r in pol)}")
        rls = await platform(conn, "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"portfolio.{t}",
                             fetch="fetchval")
        check(rls is True, f"portfolio.{t}: RLS enabled")
    provs = {r["key"]: dict(r) for r in await platform(
        conn, "SELECT * FROM portfolio.data_providers WHERE key = ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL",
        list(PROVIDERS))}
    check(set(provs) == set(PROVIDERS), "the five providers are registered", f"{sorted(provs)}")
    for k, p in provs.items():
        check(bool(p["capabilities"] == ["reference"] and p["terms_note"] and p["attribution"] and p["confirmed_on"]
                   and p["lic_internal_use"]), f"{k}: licence facts recorded", f"{p['lic_member_display']}")
    check(bool(not provs["finance_database"]["lic_redistribution"] and not provs["sec_13f_list"]["lic_redistribution"]
               and "redistribut" in (provs["finance_database"]["terms_note"] or "")
               and "redistribut" in (provs["sec_13f_list"]["terms_note"] or "")),
          "FinanceDatabase and 13(f) CUSIPs carry no redistribution right, and the terms note says so")
    check(provs["sec_13f_list"]["lic_member_display"] == "own_holdings_only", "13(f) CUSIPs: member display own holdings only")
    rank = [r["provider_key"] for r in await platform(
        conn, "SELECT provider_key FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = "
              "'reference' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
    check(rank == list(PROVIDERS), "the platform rank for 'reference'", f"{rank}")
    # seed literals against the live CHECKs (read from the catalog before seeding)
    for con, lits in (("security_master_review_kind_chk", M.REVIEW_KINDS),
                      ("sec_13f_list_map_status_chk", ("pending", "mapped", "not_mapped", "option_skipped")),
                      ("security_reference_attributes_field_chk", M.REFERENCE_FIELDS + ("absent_runs",)),
                      ("securities_global_type_chk", ("equity", "fund", "other", "index"))):
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1", con,
                           fetch="fetchval") or ""
        miss = [x for x in lits if f"'{x}'" not in d]
        check(bool(d) and not miss, f"CHECK {con} admits every literal the code writes", f"missing {miss}")
    uq = await platform(conn, "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_sec_global_ident_issuer_assigned'",
                        fetch="fetchval") or ""
    check("cusip" in uq and "figi" in uq and "isin" in uq, "UNIQUE live cusip / isin / figi read from the catalog", uq[:120])


async def y_adapters(conn):
    section("[Y] Adapters parse RECORDED samples; exclusions correct")
    st = ParseStats()
    nl = list(NT.parse_nasdaqlisted((FIX / "nasdaqlisted_sample.txt").read_text().splitlines(), st))
    check(st.excluded.get("test issue") == 2 and st.as_of is not None and st.as_of.year == 2026 and len(nl) == st.kept
          and all(r.exchange == "XNAS" for r in nl), "nasdaqlisted: footer read, 2 test issues excluded, rest kept",
          f"{st.excluded} {st.as_of} kept={st.kept}")
    check(bool([r for r in nl if r.ticker == "AAAP" and r.attrs["is_etf"] == "true" and r.security_type == "fund"]),
          "nasdaqlisted: the ETF flag becomes is_etf + type fund")
    st2 = ParseStats()
    ol = {r.ticker: r for r in NT.parse_otherlisted((FIX / "otherlisted_sample.txt").read_text().splitlines(), st2)}
    check(st2.excluded.get("test issue") == 4 and not any(t.startswith(("ATEST", "ZZZT")) for t in ol),
          "otherlisted: ATEST.* / ZZZT* test issues excluded", f"{st2.excluded}")
    check(bool({"BF.A", "BF.B", "ASB.PRE", "AAC.WS", "AAC.U", "AIIA.R"} <= set(ol) and ol["AA"].exchange == "XNYS"
               and ol["AAA"].exchange == "ARCX"), "otherlisted: classes / preferreds / warrants / units / rights canonical; "
          "exchange letter → MIC", f"{sorted(ol)[:12]}")
    st3 = ParseStats()
    sec = {r.ticker: r for r in SEC.parse(json.loads((FIX / "sec_tickers_sample.json").read_text()), st3)}
    check(bool(sec["AAPL"].attrs == {"cik": "0000320193", "exchange": "XNAS"} and "BRK.B" in sec and "PCG.PRA" in sec),
          "SEC tickers: 10-digit CIK, exchange MIC, BRK-B / PCG-PA canonical", f"{sec.get('AAPL').attrs if 'AAPL' in sec else None}")
    q = F13.find_quarters((FIX / "13f_page_sample.html").read_text())
    check(bool(q and q[0][0] == "2026q3" and ("2026q2", "https://www.sec.gov/files/investment/13flist2026q2-txt.txt") in q),
          "13(f) page: newest quarter found; the '-txt' file name read, not built", f"{q[:3]}")
    st4 = ParseStats()
    rows = list(F13.parse((FIX / "13f_sample.txt").read_text().splitlines(), st4))
    opts = [r for r in rows if r.is_option]
    check(bool(opts and all(r.description in ("CALL", "PUT") for r in opts) and st4.kept == len(rows) - len(opts)
               and any(r.status == "added" for r in rows) and any(r.status == "deleted" for r in rows)
               and any(r.has_listed_options for r in rows)),
          "13(f) file: option lines flagged and excluded from loading; *A* / *D* / '*' read",
          f"rows={len(rows)} options={len(opts)} {st4.excluded}")
    out = []
    from services.security_data.providers.reference_common import stream_csv_rows

    async def lines(text):
        for ln in text.splitlines():
            yield ln
    st5 = ParseStats()
    async for row in stream_csv_rows(lines((FIX / "fd_etfs_sample.csv").read_text())):
        r = FD.to_record("etfs", row, st5)
        if r:
            out.append(r)
    st6 = ParseStats()
    async for row in stream_csv_rows(lines((FIX / "fd_equities_sample.csv").read_text())):
        FD.to_record("equities", row, st6)
    check(bool(out and out[0].ticker == "AAA" and out[0].attrs.get("fund_category") == "Corporate Bonds"
               and st6.excluded.get("delisted in FinanceDatabase") == 2),
          "FinanceDatabase: multi-line quoted summary parsed; ETF category kept; delisted rows excluded",
          f"{[(r.ticker, r.attrs) for r in out]} {st6.excluded}")
    body = json.loads((FIX / "openfigi_sample_response.json").read_text())
    ans = OF.parse_response([OF.cusip_job("037833100"), OF.ticker_job("BRK.B"), OF.cusip_job("000000000")], body)
    rec = OF.to_record(ans[1])
    check(bool(ans[0].hit["ticker"] == "AAPL" and rec.ticker == "BRK.B" and rec.security_type == "equity"
               and ans[2].hit is None and "No identifier" in (ans[2].warning or "")),
          "OpenFIGI: a recorded answer → FIGI / type / canonical ticker; a miss keeps its warning")


async def y_isin(conn):
    section("[Y] ISIN derivation")
    check(SYM.isin_from_cusip("037833100") == "US0378331005", "AAPL 037833100 → US0378331005")
    check(SYM.isin_from_cusip("594918104") == "US5949181045", "MSFT 594918104 → US5949181045")
    check(SYM.isin_from_cusip("G0R21F105") is None, "a CINS (non-US issuer) is never given a US ISIN")
    check(SYM.is_valid_cusip("037833100") and not SYM.is_valid_cusip("037833101"), "CUSIP check digit validated")


async def guard_prefix(conn) -> bool:
    n = await platform(conn, f"SELECT count(*) FROM portfolio.securities_global_identifiers WHERE {_FX_IDENT}",
                       fetch="fetchval")
    m = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'ZZSM%'", fetch="fetchval")
    q = await platform(conn, "SELECT count(*) FROM portfolio.sec_13f_list WHERE quarter = $1", QUARTER, fetch="fetchval")
    return check(n == 0 and m == 0 and q == 0, "reserved fixture prefixes (ZZSM / 9ZZSM / US9ZZSM / BBGZZSM, quarter "
                 f"{QUARTER}) match no real row", f"identifiers {n}, names {m}, 13f rows {q}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "platform"), (ORG_A, "client")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"SECMASTER1 Verify {nm}", f"{TAG}-{nm}")
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
        # an INDEX holding ZZSMIDX (no exchange — the shape of the 13 real index rows) and a 13(f)-deleted security
        sid = await SG.create_security(conn, name=f"{NAME_PREFIX} IDX", security_type="index", currency_code="USD",
                                       is_super_admin=True)
        await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value="ZZSMIDX", is_super_admin=True)
        S["idx"] = UUID(str(sid))
        did = await SG.create_security(conn, name=f"{NAME_PREFIX} DELETED CO", security_type="equity",
                                       currency_code="USD", is_super_admin=True)
        await SG.add_identifier(conn, global_security_id=did, id_type="cusip", id_value=C_DEL, is_super_admin=True)
        S["del"] = UUID(str(did))
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true",
          f"{len(us)}/{len(USERS)} users, {len(os_)}/2 orgs")
    S["seeded"] = True


async def y_matcher(conn):
    section("[Y] Matcher: four match paths, conflicts to review, idempotent, retire never delete")
    pool = await get_pool()
    idx_fp = await fingerprint(conn, "SELECT s.id, s.name, s.security_type, i.id, i.id_value, i.valid_to FROM "
                                     "portfolio.securities_global s JOIN portfolio.securities_global_identifiers i ON "
                                     "i.global_security_id = s.id WHERE s.id = $1", S["idx"])
    t = fixture_transport(date(2026, 11, 2))
    u1 = await SS.sync_universe(ctx(pool, t, date(2026, 11, 2)))
    check(u1["new"] == 6 and u1["conflicts"] == 1 and u1["excluded"].get("test issue") == 2,
          "universe: 6 new securities (test issues excluded), ZZSMIDX → a conflict", f"{ {k: u1[k] for k in ('new', 'matched', 'conflicts', 'excluded')} }")
    a = await gsid_of(conn, "ticker", "ZZSMA")
    check(bool(a and await attr(conn, a, "exchange", "nasdaq_trader") == "XNAS"
               and await attr(conn, a, "listing_status", "nasdaq_trader") == "listed"),
          "a new security carries ticker + exchange + listing status from its provider")
    rv = await platform(conn, "SELECT kind, global_security_id, existing_value FROM portfolio.security_master_review WHERE "
                              "subject_key = 'ticker:ZZSMIDX' AND status = 'open' AND valid_to IS NULL")
    check(bool(len(rv) == 1 and rv[0]["kind"] == "ticker_conflict" and rv[0]["global_security_id"] == S["idx"]),
          "a listed equity on an index's ticker → ONE ticker_conflict review item naming the index", f"{[dict(r) for r in rv]}")
    check(await fingerprint(conn, "SELECT s.id, s.name, s.security_type, i.id, i.id_value, i.valid_to FROM "
                                  "portfolio.securities_global s JOIN portfolio.securities_global_identifiers i ON "
                                  "i.global_security_id = s.id WHERE s.id = $1", S["idx"]) == idx_fp,
          "the conflicting existing row is unchanged (checksum)")
    before = await master_counts(conn)
    u2 = await SS.sync_universe(ctx(pool, fixture_transport(date(2026, 11, 2)), date(2026, 11, 2)))
    after = await master_counts(conn)
    check(bool(after == before and u2["new"] == 0 and u2["matched"].get("ticker_exchange") == 6),
          "a second run on the same files writes nothing to the master", f"{before} → {after}; {u2['matched']}")
    sec = await SS.sync_sec_tickers(ctx(pool, fixture_transport()))
    k = await gsid_of(conn, "ticker", "ZZSMK.B")
    check(bool(sec["matched"].get("ticker_exchange") == 2 and await attr(conn, a, "cik", "sec_tickers") == "0999000001"
               and await attr(conn, k, "cik", "sec_tickers") == "0999000002"
               and sec["unmapped_reasons"].get("no exchange to match the ticker on", 0)
               + sec["unmapped_reasons"].get("nothing to create it from (no exchange)", 0) == 1),
          "SEC tickers: ZZSMA / ZZSMK-B matched on ticker + exchange family (CIK added); no-exchange row unmapped",
          f"{sec['matched']} {sec['unmapped_reasons']}")
    # CUSIP + FIGI paths
    r13 = await M.Matcher("sec_13f_list", as_of=date(2026, 11, 2), run_id=RUN_IDS[-1]).process(
        pool, [Rec("sec_13f_list", ticker="ZZSMA", cusip=C_MAPA, figi="BBGZZSM0000A", exchange="US",
                   security_type="equity", attrs={"exchange": "US"})])
    ids = {r["id_type"]: r["id_value"] for r in await platform(
        conn, "SELECT id_type, id_value FROM portfolio.securities_global_identifiers WHERE global_security_id = $1 AND "
              "valid_to IS NULL", a)}
    check(bool(r13.matched == {"ticker_exchange": 1} and ids.get("cusip") == C_MAPA and ids.get("figi") == "BBGZZSM0000A"
               and ids.get("isin") == SYM.isin_from_cusip(C_MAPA)),
          "ticker + exchange path; CUSIP, FIGI and the derived ISIN added", f"{ids}")
    rc = await M.Matcher("sec_13f_list", as_of=date(2026, 11, 2)).process(pool, [Rec("sec_13f_list", cusip=C_MAPA)])
    rf = await M.Matcher("openfigi", as_of=date(2026, 11, 2), create_new=False).process(
        pool, [Rec("openfigi", figi="BBGZZSM0000A", ticker="ZZSMA", exchange="US", security_type="equity")])
    check(bool(rc.matched == {"cusip": 1} and rf.matched == {"figi": 1} and rc.writes == 0 and rf.new == 0
               and rf.identifiers_added == 0 and rf.review_items == []),
          "CUSIP path and FIGI path match the same security; no identifier or security added (OpenFIGI records only "
          "its own first_seen)", f"{rc.matched} {rf.matched} attrs={rf.attributes_written}")
    # ticker moves to a new CUSIP: old identifier retired (valid_to), new security holds it
    await M.Matcher("sec_13f_list", as_of=date(2026, 11, 2)).process(
        pool, [Rec("sec_13f_list", ticker="ZZSMMV", cusip=C_OLD, name=f"{NAME_PREFIX} MOVER OLD", exchange="US",
                   security_type="equity", attrs={"exchange": "US"})])
    old = await gsid_of(conn, "cusip", C_OLD)
    mv = await M.Matcher("sec_13f_list", as_of=date(2026, 11, 3)).process(
        pool, [Rec("sec_13f_list", ticker="ZZSMMV", cusip=C_MOVE, name=f"{NAME_PREFIX} MOVER NEW", exchange="US",
                   security_type="equity", attrs={"exchange": "US"})])
    hold = await platform(conn, "SELECT global_security_id, valid_to IS NULL AS live FROM "
                                "portfolio.securities_global_identifiers WHERE id_type = 'ticker' AND id_value = 'ZZSMMV'")
    new = await gsid_of(conn, "cusip", C_MOVE)
    check(bool(mv.identifiers_retired == 1 and {(r["global_security_id"], r["live"]) for r in hold} == {(old, False), (new, True)}
               and await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE id = $1", old,
                                  fetch="fetchval") == 1),
          "ticker change: the old identifier is retired (valid_to), the new CUSIP's security holds it, nothing deleted",
          f"{[dict(r) for r in hold]}")
    check(bool(await platform(conn, "SELECT count(*) FROM portfolio.security_master_review WHERE kind = 'ticker_moved' AND "
                                    "subject_key = 'ticker:ZZSMMV'", fetch="fetchval") == 1), "the move is recorded as a review item")
    # 3-run absence → delisted, nothing deleted; back → listed
    dl = await gsid_of(conn, "ticker", "ZZSMDL")
    states = []
    for d in (date(2026, 11, 3), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)):
        await SS.sync_universe(ctx(pool, fixture_transport(d, drop=("ZZSMDL",)), d))
        states.append((await attr(conn, dl, "absent_runs", "nasdaq_trader"),
                       await attr(conn, dl, "listing_status", "nasdaq_trader")))
    check(states == [("1", "listed"), ("1", "listed"), ("2", "listed"), ("3", "delisted")],
          "absent from 3 distinct files → delisted (the same file twice counts once)", f"{states}")
    check(bool(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE id = $1 AND valid_to IS NULL",
                              dl, fetch="fetchval") == 1 and await gsid_of(conn, "ticker", "ZZSMDL") == dl),
          "the delisted security and its identifiers still exist")
    await SS.sync_universe(ctx(pool, fixture_transport(date(2026, 11, 6)), date(2026, 11, 6)))
    check(bool(await attr(conn, dl, "listing_status", "nasdaq_trader") == "listed"
               and await attr(conn, dl, "absent_runs", "nasdaq_trader") is None),
          "back in the file → listed again, the absence counter retired")
    S["matcher_ok"] = True


async def y_13f(conn):
    section("[Y] 13(f): a new quarter loads once; DELETED flags review only; unmapped reasons recorded")
    pool = await get_pool()
    del_fp = await fingerprint(conn, "SELECT s.id, s.name, s.valid_to, i.id_value, i.valid_to FROM portfolio.securities_global s "
                                     "JOIN portfolio.securities_global_identifiers i ON i.global_security_id = s.id WHERE s.id = $1",
                               S["del"])
    FIGI_CALLS.clear()
    r1 = await SS.sync_13f(ctx(pool, fixture_transport()), map_requests=5)
    figi_seen = [(j.get("idType"), j.get("idValue"), (a.get("data") or [{}])[0].get("figi") if "data" in a
                  else a.get("warning")) for jobs, ans in FIGI_CALLS for j, a in zip(jobs, ans)]
    rows = {r["cusip"]: dict(r) for r in await platform(conn, "SELECT cusip, map_status, map_note, global_security_id, "
                                                              "status, is_option FROM portfolio.sec_13f_list WHERE quarter = $1 "
                                                              "AND valid_to IS NULL AND system_to IS NULL", QUARTER)}
    check(bool(r1["new_quarter"] and len(rows) == 5), f"{QUARTER} detected and loaded (5 rows)", f"{r1.get('load')}")
    check(rows.get(C_OPT, {}).get("map_status") == "option_skipped", "the option row is kept, never mapped or loaded")
    a = await gsid_of(conn, "ticker", "ZZSMA")
    check(bool(rows[C_MAPA]["map_status"] == "mapped" and rows[C_MAPA]["global_security_id"] == a),
          "a CUSIP already in the master maps to it at load", f"{rows[C_MAPA]}")
    n = await gsid_of(conn, "cusip", C_NEW)
    check(bool(n and rows[C_NEW]["map_status"] == "mapped" and rows[C_NEW]["global_security_id"] == n
               and await gsid_of(conn, "ticker", "ZZSMN") == n and await gsid_of(conn, "figi", "BBGZZSM0000N") == n),
          "a new CUSIP mapped through OpenFIGI → a new security with ticker + FIGI + CUSIP",
          f"{rows[C_NEW]} jobs→answers (positional)={figi_seen} sync={ {k: v for k, v in r1.items() if k not in ('load', 'review_item_ids')} }")
    check(bool(rows[C_MISS]["map_status"] == "not_mapped" and (rows[C_MISS]["map_note"] or "").startswith(
        "not mapped — OpenFIGI found no identifier")), "an unmapped CUSIP stays with 'not mapped — <why>'",
          f"{rows[C_MISS]['map_note']}")
    rv = await platform(conn, "SELECT kind, global_security_id FROM portfolio.security_master_review WHERE kind = "
                              "'13f_deleted' AND subject_key = $1 AND status = 'open' AND valid_to IS NULL", f"cusip:{C_DEL}")
    check(bool(len(rv) == 1 and rv[0]["global_security_id"] == S["del"]), "a DELETED row in the master → one review item")
    check(await fingerprint(conn, "SELECT s.id, s.name, s.valid_to, i.id_value, i.valid_to FROM portfolio.securities_global s "
                                  "JOIN portfolio.securities_global_identifiers i ON i.global_security_id = s.id WHERE s.id = $1",
                            S["del"]) == del_fp, "…and retired nothing (security + identifiers unchanged)")
    before = await master_counts(conn)
    r2 = await SS.sync_13f(ctx(pool, fixture_transport()), map_requests=5)
    check(bool(not r2["new_quarter"] and "already loaded" in str(r2["load"]) and await master_counts(conn) == before),
          "the same quarter again: not loaded twice, nothing written", f"{r2.get('load')}")


async def fire(conn, pool, trigger_id, cron, tzname, after):
    occ = next_occurrences(schedule_cron=cron, timezone_name=tzname, after_utc=after, count=1)[0]
    res = await WS.run_scheduler_tick(conn, pool, now_utc=occ + timedelta(minutes=5), only_trigger_ids=[trigger_id],
                                      log=lambda m: None)
    return occ, res


async def run_row(conn, run_id):
    return await platform(conn, "SELECT id, status, error_detail, org_id, started_by FROM workflow_runs WHERE id = $1",
                          run_id, fetch="fetchrow")


async def y_jobs(conn):
    section("[Y] Jobs: system workflows, scheduled fire, held + alert on failure, platform runs hidden from org admins")
    pool = await get_pool()
    real = await platform(conn, "SELECT d.name, t.schedule_cron, t.timezone FROM workflow_definitions d LEFT JOIN "
                                "workflow_triggers t ON t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' "
                                "WHERE d.org_id = $1 AND d.name = ANY($2::text[])", J.PLATFORM_ORG_ID,
                          [j.name for j in J.JOBS.values()])
    check(bool(len(real) == len(J.JOBS) and all(r["name"].startswith("system:") and r["schedule_cron"] for r in real)),
          "each of the four REAL platform jobs exists in the platform org, is a system workflow, has a schedule",
          f"{[(r['name'], r['schedule_cron'], r['timezone']) for r in real]}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        seeded = await J.ensure_platform_jobs(conn, org_id=ORG_P, created_by=U_SUPER, active=True)
    S["jobs"] = seeded
    c, o = await api(U_SUPER, "GET", "/api/v1/admin/workflows")
    lib = {w["name"]: w for w in (o if isinstance(o, list) else [])}
    check(bool(c == 200 and all(lib.get(j.name, {}).get("can_delete") is False for j in J.JOBS.values())),
          "the library shows no Delete for a platform job", f"{c} {[(k, v.get('can_delete')) for k, v in lib.items()][:4]}")
    c, o = await api(U_SUPER, "DELETE", f"/api/v1/admin/workflows/{seeded['security_master_nightly']['definition_id']}")
    check(c == 409 and "system workflow" in str(o).lower(), "the API refuses to delete a platform job (409)", f"{c} {o}")
    now = datetime.now(timezone.utc)
    S["runs"] = {}
    for key, spec in J.JOBS.items():
        occ, res = await fire(conn, pool, seeded[key]["trigger_id"], spec.cron, spec.timezone, now)
        fired = res.fired[0] if res.fired else None
        row = await run_row(conn, fired["run_id"]) if fired else None
        steps = await platform(conn, "SELECT ws.step_key, rs.status, rs.result FROM workflow_run_steps rs JOIN workflow_steps "
                                     "ws ON ws.id = rs.workflow_step_id WHERE rs.workflow_run_id = $1",
                               fired["run_id"]) if fired else []
        outs = [json.loads(s["result"]) if isinstance(s["result"], str) else s["result"] for s in steps]
        ok = bool(row and row["status"] == "completed" and len(steps) == len(spec.steps)
                  and all(s["status"] == "completed" for s in steps)
                  and all((x or {}).get("invoked") and isinstance((x or {}).get("handler_data"), dict) for x in outs))
        check(ok, f"{spec.name}: its trigger fires through run_scheduler_tick (injected instant) and the run completes "
                  "with each step's output", f"{res.summary()} {row and dict(row)} {[str(x)[:200] for x in outs]}")
        if fired:
            S["runs"][key] = fired["run_id"]
            S.setdefault("sd_run_ids", []).extend(
                [(x or {}).get("handler_data", {}).get("security_data_run_id") for x in outs
                 if (x or {}).get("handler_data", {}).get("security_data_run_id")])
        if key == "security_master_nightly" and fired:
            # by step_key, not row order: the steps query has no ORDER BY, and the openfigi step's output (no
            # nasdaq_trader / sec_tickers counts) came back first in the fix-1 proof run (SECMASTER1 FIX 1)
            by_key = {st["step_key"]: o for st, o in zip(steps, outs)}
            hd = (by_key.get("universe") or {}).get("handler_data") or {}
            check(bool(hd and "review_items_created" in hd and "nasdaq_trader" in hd and "sec_tickers" in hd),
                  "the nightly step's output records counts and the review items it created",
                  f"{ {k: hd.get(k) for k in ('status', 'review_items_created')} if hd else None}")
    hist = await platform(conn, "SELECT count(*) FROM portfolio.security_data_runs WHERE summary->>'workflow_run_id' = "
                                "ANY($1::text[])", [str(v) for v in S["runs"].values()], fetch="fetchval")
    check(int(hist or 0) >= len(S["runs"]) > 0, "each job run wrote a row to the job history (security_data_runs)", f"{hist}")
    # forced failure → HELD + Super Admin alert; a held run blocks re-fire; close it → fires again
    spec = J.JOBS["security_master_attributes"]
    J.TRANSPORT_FACTORY = lambda: fixture_transport(fail_urls=(FD.url("equities", "NMS"),))
    try:
        occ, res = await fire(conn, pool, seeded["security_master_attributes"]["trigger_id"], spec.cron, spec.timezone,
                              now + timedelta(days=8))
    finally:
        J.TRANSPORT_FACTORY = lambda: fixture_transport()
    # a run that fails is HELD and its error re-raised, so the tick reports it under `errors` (with the held run's
    # id), never under `fired` — the first run of this check read `fired` and got None (SECMASTER1 FIX 1)
    held = next((e.get("run_id") for e in res.errors
                 if str(e.get("trigger_id")) == str(seeded["security_master_attributes"]["trigger_id"])), None)
    row = await run_row(conn, held) if held else None
    check(bool(not res.fired and held and row and row["status"] == "held" and row["org_id"] == ORG_P
               and "forced failure" in (row["error_detail"] or "")),
          "a failing step HOLDS the run with the error (the tick reports the held run's id)",
          f"{res.summary()} {res.errors and str(res.errors[0].get('error'))[:160]} {row and dict(row)}")
    todos = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'workflow_run_held' AND related_id = $1 "
                                 "AND status = 'open'", held) if held else []
    check(bool({t["user_id"] for t in todos} == {U_SUPER}), "the held-run alert goes to the Super Admin (and no client org admin)",
          f"{[str(t['user_id']) for t in todos]}")
    occ2, res2 = await fire(conn, pool, seeded["security_master_attributes"]["trigger_id"], spec.cron, spec.timezone,
                            occ + timedelta(minutes=1))
    blockers = [s.get("blocking_run_id") for s in res2.skipped if "overlap" in str(s.get("reason"))]
    check(bool(held and not res2.fired and blockers and all(str(b) == str(held) for b in blockers)),
          "a held run blocks the next fire — and the blocking run is THIS section's held run, not a leftover",
          f"{res2.summary()} blocking={[str(b) for b in blockers]} held={held}")
    c, o = await api(U_ADMIN, "POST", f"{_M}/runs/{held}/close", {"note": "verify"})
    check(c == 403, "an org admin cannot close a platform run (403)", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{_M}/runs/{held}/close", {"note": "verify: closed after the forced failure"})
    row = await run_row(conn, held)
    open_todos = await platform(conn, "SELECT count(*) FROM member_todos WHERE related_id = $1 AND status = 'open'", held,
                                fetch="fetchval")
    check(bool(c == 200 and row["status"] == "cancelled" and int(open_todos) == 0),
          "Super Admin closes the held run (cancelled, alert closed)", f"{c} {row and row['status']} {o}")
    occ3, res3 = await fire(conn, pool, seeded["security_master_attributes"]["trigger_id"], spec.cron, spec.timezone,
                            occ2 + timedelta(minutes=1))
    check(bool(res3.fired and res3.fired[0]["status"] == "completed"), "…and the schedule fires again", f"{res3.summary()}")
    # a client org cannot borrow a platform action
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        borrowed = await J.ensure_platform_jobs(conn, org_id=ORG_A, created_by=U_SUPER, active=False,
                                                jobs={"security_master_attributes": J.JOBS["security_master_attributes"]})
    async def client_run(actor):
        before = {r["id"] for r in await platform(conn, "SELECT id FROM workflow_runs WHERE org_id = $1", ORG_A)}
        tokens = set_rls_context(str(ORG_A), False)
        try:
            await J.run_now(pool, job_key="security_master_attributes", actor_id=actor, org_id=ORG_A)
            st = "completed"
        except Exception as exc:  # noqa: BLE001
            st = f"{type(exc).__name__}: {exc}"
        finally:
            reset_rls_context(tokens)
        new = [dict(r) for r in await platform(conn, "SELECT id, status, error_detail, started_by FROM workflow_runs "
                                                     "WHERE org_id = $1", ORG_A) if r["id"] not in before]
        return st, (new[0] if len(new) == 1 else None), new

    # (1) a starter who HOLDS the permission (the Super Admin, acting in the client org) → only the org guard refuses
    st, a_run, new = await client_run(U_SUPER)
    check(bool(a_run and a_run["status"] == "held" and a_run["started_by"] == U_SUPER
               and "runs only in the platform org" in (a_run["error_detail"] or "")
               and "requires permission" not in (a_run["error_detail"] or "")),
          "the same action in a client org's run, started by a Super Admin, is refused by the org guard (held: "
          "'runs only in the platform org')", f"{st[:160]} {new}")
    S["client_run"] = a_run["id"] if a_run else None
    # (2) a starter WITHOUT the permission (the client org admin) → refused by the permission check
    st2, b_run, new2 = await client_run(U_ADMIN)
    check(bool(b_run and b_run["status"] == "held" and b_run["started_by"] == U_ADMIN
               and f"requires permission {J.REQUIRED_PERMISSION!r}" in (b_run["error_detail"] or "")
               and "does not hold it" in (b_run["error_detail"] or "")),
          "…and started by a member without run_platform_jobs, it is refused by the permission check",
          f"{st2[:160]} {new2}")
    # nothing the guarded action writes exists for either client run (the guard fires before any write)
    wrote = await platform(conn, "SELECT count(*) FROM portfolio.security_data_runs WHERE summary->>'workflow_run_id' = "
                                 "ANY($1::text[])", [str(x["id"]) for x in new + new2], fetch="fetchval")
    check(bool(new and new2 and int(wrote or 0) == 0), "neither refused client run wrote a job-history row", f"{wrote}")
    _ = borrowed
    tokens = set_rls_context(str(ORG_P), False)
    try:
        started = await J.run_now(pool, job_key="security_master_13f", actor_id=U_SUPER, org_id=ORG_P)
    finally:
        reset_rls_context(tokens)
    check(bool(started and started.get("status") == "completed"),
          "Run now (the screen's action) starts the job through the same engine path, started_by the Super Admin",
          f"{started}")
    # visibility — identical requests
    c1, su = await api(U_SUPER, "GET", "/api/v1/admin/workflow-runs?period=all&limit=1000")
    c2, oa = await api(U_ADMIN, "GET", "/api/v1/admin/workflow-runs?period=all&limit=1000")
    su_ids = {r["id"] for r in (su.get("rows") or [])}
    oa_ids = {r["id"] for r in (oa.get("rows") or [])}
    plat = {str(v) for v in S["runs"].values()}
    check(bool(c1 == 200 and plat and plat <= su_ids), "Super Admin sees the platform runs", f"{c1} {len(plat & su_ids)}/{len(plat)}")
    check(bool(c2 == 200 and not (plat & oa_ids) and str(S["client_run"]) in oa_ids),
          "the client org admin does NOT see them (identical request) — and does see their own org's run",
          f"{c2} leaked={sorted(plat & oa_ids)[:2]}")
    c, o = await api(U_ADMIN, "GET", f"/api/v1/admin/workflow-runs/{S['runs']['security_master_nightly']}")
    check(c in (403, 404), "the client org admin cannot open a platform run by id", f"{c}")
    # status panel + run now
    c, o = await api(U_SUPER, "GET", f"{_M}/status")
    st = o.get("status") or {}
    check(bool(c == 200 and o.get("permissions", {}).get("can_run") is True and "securities_by_type" in st
               and "review_open" in st and "unmapped" in (st.get("sec_13f") or {})), "status panel for Super Admin",
          f"{c} {list(st)[:8]}")
    for path, m in ((f"{_M}/status", "GET"), (f"{_M}/jobs", "GET"), (f"{_M}/review", "GET"),
                    (f"{_M}/jobs/security_master_13f/run-now", "POST")):
        c, o = await api(U_ADMIN, m, path, {} if m == "POST" else None)
        check(c == 403, f"org admin: {m} {path} → 403", f"{c}")


async def y_review(conn):
    section("[Y] Review queue: list + decision (records only; changes no security)")
    rows_fp = await fingerprint(conn, "SELECT id, name, security_type FROM portfolio.securities_global WHERE id = $1", S["idx"])
    c, o = await api(U_SUPER, "GET", f"{_M}/review?status=open&kind=ticker_conflict")
    item = next((r for r in o.get("rows") or [] if r["subject_key"] == "ticker:ZZSMIDX"), None)
    check(bool(c == 200 and item and item["actions"] == ["resolved", "dismissed"]), "the item is listed with its actions",
          f"{c} {item and item['actions']}")
    if item:
        c, o = await api(U_ADMIN, "POST", f"{_M}/review/{item['id']}/decision", {"outcome": "dismissed", "note": "x"})
        check(c == 403, "an org admin cannot decide (403)", f"{c}")
        c, o = await api(U_SUPER, "POST", f"{_M}/review/{item['id']}/decision",
                         {"outcome": "dismissed", "note": "verify: an index ticker, not the listing"})
        dec = await platform(conn, "SELECT status, decided_by FROM portfolio.security_master_review WHERE id = $1",
                             UUID(str(o.get("id"))) if o.get("id") else None, fetch="fetchrow")
        check(bool(c == 200 and dec and dec["status"] == "dismissed" and dec["decided_by"] == U_SUPER),
              "decided by the Super Admin (restated: the decided row carries who)", f"{c} {o}")
        check(await fingerprint(conn, "SELECT id, name, security_type FROM portfolio.securities_global WHERE id = $1",
                                S["idx"]) == rows_fp, "the decision changed no security")


async def y_search(conn):
    section("[Y] Search finds by ticker and by name")
    c, o = await api(U_ADMIN, "GET", "/api/v1/portfolio/global-securities?search=ZZSMAPL")
    names = [s["name"] for s in o.get("securities") or []]
    check(bool(c == 200 and any("Apple-shaped" in n for n in names)), "the securities route finds it by ticker", f"{c} {names}")
    c, o = await api(U_ADMIN, "GET", "/api/v1/portfolio/global-securities?search=apple-shaped")
    names = [s["name"] for s in o.get("securities") or []]
    check(bool(c == 200 and any("Apple-shaped" in n for n in names)), "…and by name (case-insensitive)", f"{c} {names}")
    pool = await get_pool()
    async with pool.acquire() as c2:
        hits = await SG.get_by_identifiers_bulk(c2, "ticker", ["zzsmapl", "ZZSMK-B", "ZZSMK/B"])
    check(bool({"ZZSMAPL", "ZZSMK.B"} <= set(hits)), "the model-upload resolver (Muse's lookup) finds the ticker, and "
          "BRK-B / BRK/B spellings resolve to the canonical class share", f"{sorted(hits)}")


async def y_licence(conn):
    section("[Y] Licence: CUSIPs shown to members only for their own holdings")
    a = await gsid_of(conn, "ticker", "ZZSMA")
    pool = await get_pool()
    async with pool.acquire() as c2:
        member = {r["id_type"] for r in await M.identifiers_for_display(c2, a, purpose="member_display")}
        own = {r["id_type"] for r in await M.identifiers_for_display(c2, a, purpose="member_display", own_holdings=True)}
        internal = {r["id_type"] for r in await M.identifiers_for_display(c2, a, purpose="internal")}
        ref = await M.read_reference(c2, a, org_id=None)
    check(bool("cusip" not in member and "isin" not in member and "ticker" in member), "member display: no CUSIP / ISIN",
          f"{member}")
    check(bool({"cusip", "isin"} <= own and {"cusip", "isin"} <= internal), "own holdings and internal use: shown",
          f"{own} {internal}")
    check(bool(ref["exchange"]["value"] == "XNAS" and ref["exchange"]["provider"] == "nasdaq_trader"
               and ref["cik"]["provider"] == "sec_tickers"),
          "reference reads go through the rank (exchange from Nasdaq Trader, CIK from the SEC)",
          f"{ {k: (v['value'], v['provider']) for k, v in ref.items() if v['found']} }")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS b")
    check(bool(who["u"] == "app_service" and who["b"] is False), "the script's connection is app_service, rolbypassrls = false",
          f"{dict(who)}")
    prun = S["runs"].get("security_master_nightly")
    crun = S.get("client_run")
    a_sees_p = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_runs WHERE id = $1", prun, fetch="fetchval")
    p_sees_p = await scoped(conn, ORG_P, "SELECT count(*) FROM workflow_runs WHERE id = $1", prun, fetch="fetchval")
    p_sees_a = await scoped(conn, ORG_P, "SELECT count(*) FROM workflow_runs WHERE id = $1", crun, fetch="fetchval")
    a_sees_a = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_runs WHERE id = $1", crun, fetch="fetchval")
    check(bool(a_sees_p == 0 and p_sees_p == 1 and p_sees_a == 0 and a_sees_a == 1),
          "client org cannot read the platform run; the platform org cannot read the client's run",
          f"A→P {a_sees_p}, P→P {p_sees_p}, P→A {p_sees_a}, A→A {a_sees_a}")
    rv = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.security_master_review", fetch="fetchval")
    at = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.security_reference_attributes WHERE "
                                   "global_security_id = $1", S["idx"], fetch="fetchval")
    check(int(rv) == 0, "the review queue is invisible without Super Admin", f"{rv}")
    try:
        await scoped(conn, ORG_A, "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, "
                                  "provider_key, as_of) VALUES ($1, 'sector', 'x', 'nasdaq_trader', now()::date)", S["idx"],
                     fetch="execute")
        wrote = True
    except asyncpg.InsufficientPrivilegeError:
        wrote = False
    except asyncpg.PostgresError as exc:
        wrote = "row-level security" not in str(exc)
    check(wrote is False, "an org cannot write platform reference data (RLS)")
    _ = at


async def y_real_rows(conn):
    section("[Y] Real securities / identifiers / attributes / triggers / platform jobs untouched (count + checksum)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after[k] == v, f"real {k} unchanged (count + checksum)", f"{v[0]} → {after[k][0]}")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^\s*ok \d+ - (?:super admin: Run now|view without the flags|close held run only|status panel)",
                      out, re.M)
    check(len(mine) >= 4, "the secmaster1 render tests ran and passed (no control without the server's flag)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_marketdata1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_marketdata1.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_marketdata1.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-200:] if not total else ''}")


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
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
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
    pre = None
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("adapters", y_adapters, conn)
        await run("isin", y_isin, conn)
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("seed", seed, conn)
        if S.get("seeded"):
            await run("matcher", y_matcher, conn)
            if S.get("matcher_ok"):
                await run("13f", y_13f, conn)
                await run("jobs", y_jobs, conn)
                await run("review", y_review, conn)
                await run("search", y_search, conn)
                await run("licence", y_licence, conn)
                if S.get("runs"):
                    await run("isolation", y_isolation, conn)
            else:
                check(False, "13(f), jobs, review, search, licence and isolation need the matcher fixtures",
                      "the matcher section failed")
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (securities, identifiers, attributes, 13(f) rows, review items, "
                  "OpenFIGI answers, call log, job history, workflow runs / steps / triggers / todos)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after[k] == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
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
