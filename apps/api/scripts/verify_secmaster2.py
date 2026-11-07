"""verify_secmaster2 — finish the security master (SECMASTER2): SEC mutual-fund share classes in the master, the
FinanceDatabase attributes load working again, sharper fund categories, and the load summary.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_secmaster2.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is
set before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect``).

NO NETWORK TO ANY DATA SOURCE. Every source goes through the providers' injected boundary (``FixtureTransport`` —
its CURRENT signature: files, responder, fail_urls, status_urls); httpx's transport refuses and records any real
request; the LLM helpers are stubbed and must never be called. Parsing is proven on RECORDED samples of today's real
files (apps/api/scripts/fixtures/secmaster2/, fetched 2026-11-07); everything that writes runs on SYNTHETIC files in
the real formats whose every symbol / class id / series id / CIK / name carries a reserved prefix (ZZM…, C99…, S99…,
0999100…, "ZZM2 VERIFY") matching no real row (asserted before seeding).

FIXTURES: three orgs (is_test, asserted) — ORG_P, a fixture PLATFORM org the jobs are seeded into (added to
security_master_jobs.PLATFORM_JOB_ORG_IDS for this run), ORG_A and ORG_B, client orgs (taxonomy copied read-only
from 2nd Act) each with an org admin; a fixture Super Admin in ORG_P. The scheduler is driven through the REAL
run_scheduler_tick with an injected instant and only_trigger_ids = the fixture triggers. Real platform jobs are only
READ (the mutual-fund job must be OFF). The nightly relink is NEVER run on a real org: relink_manual_assets is called
for ORG_A / ORG_B only.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN, by fixture org / user ids and by fixture
ids (securities, identifiers, attributes, review items, source files, call log, job history). Every touched table is
re-read for its exact pre-test count BY FIXTURE IDS ONLY (audit_log, member_todos and asset_relinks are written by
real activity — the nightly runs ~07:00 UTC — so they are counted by fixture org / user ids, never whole-table);
real rows and the live master are compared by count + checksum keyed by their real id.

--self-checks-only: git / doc / route self-checks only. --cleanup-only: teardown + leftover check. With
VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import types
import uuid
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
FIX = HERE / "fixtures" / "secmaster2"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402
from _verify_total import nested_total  # noqa: E402

SPRINT_KEY = "secmaster2"
BEFORE_REF = "6379e28"   # secmaster2-work @ merge visibility1 — the sprint's base
_M = "/api/v1/admin/security-data/master"
ROUTES = {("GET", f"{_M}/summary"): "super_admin", ("GET", f"{_M}/securities"): "super_admin"}
FD_PATH = "apps/api/services/security_data/providers/finance_database.py"


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ═══ Self-checks (no database) ══════════════════════════════════════════════
TASK1 = (  # (doc, needles, meaning)
    ("docs/SECMASTER2_DISCOVERY.md", ("etfs/ASE.csv", "12611760", "equities/ASE.csv", "NCM", "funds/NAS.csv",
                                      "close_held_run"), "1a: the 404 (etfs/ASE.csv), today's files, the fix, the held run"),
    ("docs/SECMASTER2_DISCOVERY.md", ("company_tickers_mf.json", "28,647", "seriesId", "public domain", "User-Agent",
                                      "OpenFIGI", "no names"), "1b: the SEC mutual-fund file, its licence and identifiers"),
    ("docs/SECMASTER2_DISCOVERY.md", ("category_group", "Fixed Income", "taxonomy_mc_1_3", "taxonomy_sub_1_3_4",
                                      "fund_category_taxonomy", "N-PORT"), "1c: the categories each source has; the tree"),
    ("docs/SECMASTER2_DISCOVERY.md", ("1d", "attribute coverage", "price coverage", "review queue", "last run"),
     "1d: the load summary — what the admin page showed and what was missing"),
)
STEPS = ("close the held attributes run", "--source finance_database --dry-run", "--source sec_mutual_funds --dry-run",
         "--funds --dry-run", "--refine", "nightly relink", "Recalculate")


def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "secmaster2 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate (super_admin) at the sprint's end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "secmaster2's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    for rel, needles, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or _read(rel)
        miss = [n for n in needles if n not in txt]
        check(bool(txt) and not miss, f"Task 1 — {meaning}", f"{rel}: missing {miss}")
    old = VS.git_show(BEFORE_REF, FD_PATH) or ""
    now = _read(FD_PATH)
    m_old = re.search(r'US_ETF_FILES = \(([^)]*)\)', old)
    m_new = re.search(r'US_ETF_FILES = \(([^)]*)\)', now)
    check(bool(m_old and '"ASE"' in m_old.group(1)), "REPRODUCED before the sprint: the attributes job asked for "
          "etfs/ASE.csv (in US_ETF_FILES at the base)", m_old.group(1) if m_old else "no US_ETF_FILES at the base")
    check(bool(m_new and '"ASE"' not in m_new.group(1) and "US_FUND_FILES" in now and "SourceFormatChanged" in now),
          "today's US_ETF_FILES is what FinanceDatabase publishes (no ASE); funds/NAS.csv read; a 404 raises "
          "SourceFormatChanged", m_new.group(1) if m_new else "")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. SECMASTER2 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    need = ("22,789", "4,609", "1,247", "FXAIX", "FDRXX", "SNOXX", "REFINE", "taxonomy_mc_1_3", "taxonomy_sub_1_3_4")
    miss = [n for n in need if n not in top]
    check(bool(top) and not miss, "Task 8: PROJECT_STATUS entry — dry-run counts, the would-relink list, the refine "
          "proposals", f"missing {miss}")
    design = _read("docs/SECURITY_DATA_DESIGN.md")
    for where, txt in (("PROJECT_STATUS", top), ("SECURITY_DATA_DESIGN", design)):
        sec = txt[txt.find("SECMASTER2 operator steps"):] if "SECMASTER2 operator steps" in txt else ""
        order = [sec.find(s) for s in STEPS]
        check(bool(sec) and -1 not in order and order == sorted(order),
              f"Task 6: {where} lists the operator steps IN ORDER", f"{list(zip(STEPS, order))}")
    check("verify_secmaster2.py" in _read("scripts/regression_set.txt"), "Task 8: in scripts/regression_set.txt")
    jobs = _read("apps/api/services/security_master_jobs.py")
    check('"system:Security data — mutual funds"' in jobs and '"security_master.mutual_funds"' in jobs,
          "the monthly platform job 'Security data — mutual funds' is defined")
    load = _read("apps/api/services/security_master_load.py")
    check(bool("sec_mutual_funds" in load and not re.search(r"\bDELETE\s+FROM\b", load, re.I)),
          "the matcher knows the new source and still issues no DELETE FROM")
    summ = _read("apps/api/services/security_master_summary.py")
    body = summ[summ.find("async def summary("):summ.find("async def securities(")]
    check(body.count("await conn.fetch(") == 7 and "for r in rows" in body,
          "summary() is seven set queries (one per section), never a query per row (the DB section counts the "
          "statements it really issues)", f"{body.count('await conn.fetch(')} in summary()")


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
import backfill_taxonomy_keys as BF  # noqa: E402
from services import extraction  # noqa: E402
from services import fund_classification as FC  # noqa: E402
from services import portfolio_manual as PM  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import security_master_load as M  # noqa: E402
from services import security_master_summary as SUM  # noqa: E402
from services import security_master_sync as SS  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_scheduler as WS  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.database import get_pool  # noqa: E402
from services.held_away import NO_MASTER_ASSET_TYPES  # noqa: E402
from services.security_data.providers import finance_database as FD  # noqa: E402
from services.security_data.providers import sec_mutual_funds as MF  # noqa: E402
from services.security_data.providers.reference_common import (FixtureTransport, ParseStats,  # noqa: E402
                                                              SourceFormatChanged, SourceHTTPError)
from services.security_data.providers.reference_common import SourceRecord as Rec  # noqa: E402
from services.workflow_schedule import next_occurrences  # noqa: E402

TAG = "secmaster2verify"
TICKER_PREFIX = "ZZM"
NAME_PREFIX = "ZZM2 VERIFY"
CLASS_PREFIX, SERIES_PREFIX, CIK_PREFIX = "C99", "S99", "0999100"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
ORG_LIVE = UUID("00000000-0000-0000-0000-000000000001")   # read-only: its asset_taxonomy rows are copied


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000054d{n:03d}")


ORG_P, ORG_A, ORG_B = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_P, ORG_A, ORG_B]
U_SUPER, U_ADMIN_A, U_ADMIN_B = _id(101), _id(102), _id(103)
USERS = {U_SUPER: (ORG_P, "super_admin", "super", "super_admin", "SECMASTER2 Super Admin"),
         U_ADMIN_A: (ORG_A, "org_admin", "admin-a", "org_admin", "SECMASTER2 Org Admin A"),
         U_ADMIN_B: (ORG_B, "org_admin", "admin-b", "org_admin", "SECMASTER2 Org Admin B")}

#: the synthetic SEC mutual-fund file (the real format; every value reserved)
MF_ROWS = [
    [999100001, "S990000001", "C990000001", "ZZMAX"],   # new mutual fund
    [999100001, "S990000001", "C990000002", "ZZMBX"],   # new mutual fund, same series (series id shared)
    [999100002, "S990000002", "C990000003", "ZZMXX"],   # new money market (…XX)
    [999100003, "S990000003", "C990000004", "ZZME"],    # an ETF class the master already holds → matched
    [999100004, "S990000004", "C990000005", "ZZMOX"],   # held by an OTC security → ticker_conflict review
    [999100005, "S990000005", "C990000006", "ZZMQ"],    # not a mutual-fund ticker, not in the master → unmapped
    [999100006, "S990000006", "C990000007", ""],        # no ticker → excluded
]
MF_DOC = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": MF_ROWS}
#: category fixtures: ticker → (name, category, group, asset's current key)
CAT = {
    "ZZMSP": (f"{NAME_PREFIX} SPDR S&P 500 ETF Trust", "Large Cap", "Equities", None),
    "ZZMVI": (f"{NAME_PREFIX} Total International Stock ETF", "Developed Markets", "Equities", None),
    "ZZMEM": (f"{NAME_PREFIX} MSCI Emerging Markets ETF", "Emerging Markets", "Equities", None),
    "ZZMIW": (f"{NAME_PREFIX} Russell 2000 ETF", "Small Cap", "Equities", None),
    "ZZMBN": (f"{NAME_PREFIX} Total Bond Market ETF", "Investment Grade Bonds", "Fixed Income", None),
    "ZZMTS": (f"{NAME_PREFIX} U.S. Total Stock Market ETF", "Developed Markets", "Equities", "taxonomy_sc_1"),
    "ZZMDV": (f"{NAME_PREFIX} FTSE Developed Markets ETF", "Developed Markets", "Equities", "taxonomy_sc_1"),
    "ZZMBT": (f"{NAME_PREFIX} Bitcoin Mini Trust", "Corporate Bonds", "Fixed Income", None),
}
WANT = {"ZZMSP": ("apply", "taxonomy_mc_1_1"), "ZZMVI": ("apply", "taxonomy_sub_1_3_4"),
        "ZZMEM": ("apply", "taxonomy_mc_1_4"), "ZZMIW": ("apply", "taxonomy_mc_1_2"),
        "ZZMBN": ("apply", "taxonomy_mc_2_1"), "ZZMTS": ("refine", "taxonomy_mc_1_1"),
        "ZZMDV": ("refine", "taxonomy_mc_1_3"), "ZZMBT": ("review", "taxonomy_mc_7_3")}

COUNTED: dict = {}     # table -> (predicate, args builder) — filled by counted_plan(); BY FIXTURE IDS ONLY
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


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def fixture_secs(conn) -> list:
    ids = {r["id"] for r in await platform(conn, "SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                           f"{NAME_PREFIX}%")}
    ids |= {r["global_security_id"] for r in await platform(
        conn, "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE "
              "(id_type = 'ticker' AND id_value LIKE $1) OR (id_type = 'sec_class_id' AND id_value LIKE $2) OR "
              "(id_type = 'sec_series_id' AND id_value LIKE $3)", f"{TICKER_PREFIX}%", f"{CLASS_PREFIX}%",
        f"{SERIES_PREFIX}%")}
    return sorted(ids, key=str)


def counted_plan(fx: list, runs: list) -> dict:
    """table -> (predicate, args): every touched table counted BY FIXTURE IDS ONLY."""
    orgs, users = FIXTURE_ORGS, list(USERS)
    by_org = ("org_id = ANY($1::uuid[])", [orgs])
    return {
        "public.organizations": ("id = ANY($1::uuid[])", [orgs]),
        "public.users": ("id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [users, f"%{TAG}%"]),
        # SECMASTER2 FIX 1: the fixture role grants (user_roles is the ONE role source) and the fixture orgs' roles
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
        "public.roles": by_org,
        "public.config": by_org,
        # real activity writes these (the nightly ~07:00 UTC, every user action): fixture org / user ids only
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
        "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
        "portfolio.asset_relinks": by_org,
        "portfolio.assets": by_org,
        "portfolio.asset_identifiers": by_org,
        "public.workflow_definitions": by_org, "public.workflow_versions": by_org, "public.workflow_steps": by_org,
        "public.workflow_triggers": by_org, "public.workflow_runs": by_org, "public.workflow_run_steps": by_org,
        "portfolio.securities_global": ("id = ANY($1::uuid[]) OR name LIKE $2", [fx, f"{NAME_PREFIX}%"]),
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[]) OR id_value LIKE $2 OR "
                                                    "id_value LIKE $3", [fx, f"{TICKER_PREFIX}%", f"{CLASS_PREFIX}%"]),
        "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])",
                                                    [fx, runs]),
        "portfolio.security_master_review": ("global_security_id = ANY($1::uuid[]) OR subject_key LIKE $2 OR "
                                             "run_id = ANY($3::uuid[])", [fx, f"%{TICKER_PREFIX}%", runs]),
        "portfolio.security_master_source_files": ("run_id = ANY($1::uuid[])", [runs]),
        "portfolio.provider_call_log": ("run_id = ANY($1::uuid[])", [runs]),
        "portfolio.security_data_runs": ("id = ANY($1::uuid[]) OR summary->>'workflow_run_id' = ANY($2::text[])",
                                         [runs, [str(r) for r in runs]]),
    }


async def counts(conn) -> dict:
    fx = await fixture_secs(conn)
    runs = list(RUN_IDS) + [r["id"] for r in await platform(
        conn, "SELECT id FROM workflow_runs WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS)]
    out = {}
    for t, (pred, args) in counted_plan(fx, runs).items():
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
    return out


async def real_fingerprints(conn) -> dict:
    """The live master and the real orgs' rows, keyed by their real id. Excluded: the fixture rows themselves.
    Columns: identity + the bi-temporal close columns; real activity that may legitimately write meanwhile (the
    operator's own load, the nightly relink ~07:00 UTC) would show here — said, not hidden."""
    fx = [str(x) for x in await fixture_secs(conn)]
    orgs = [str(o) for o in FIXTURE_ORGS]
    live = "valid_to IS NULL AND system_to IS NULL"
    return {
        "live master securities": await fingerprint(
            conn, "SELECT id, name, security_type, merged_into_id, canonical_id FROM portfolio.securities_global WHERE "
                  f"{live} AND NOT (id = ANY($1::uuid[]))", fx),
        "live master identifiers": await fingerprint(
            conn, "SELECT id, global_security_id, id_type, id_value, is_primary FROM "
                  f"portfolio.securities_global_identifiers WHERE {live} AND NOT (global_security_id = ANY($1::uuid[]))",
            fx),
        "live master attributes": await fingerprint(
            conn, "SELECT id, global_security_id, field, value, provider_key FROM portfolio.security_reference_attributes "
                  f"WHERE {live} AND NOT (global_security_id = ANY($1::uuid[]))", fx),
        "live review items": await fingerprint(
            conn, "SELECT id, kind, subject_key, status FROM portfolio.security_master_review WHERE valid_to IS NULL "
                  "AND NOT (COALESCE(global_security_id, '00000000-0000-0000-0000-000000000000'::uuid) = "
                  "ANY($1::uuid[])) AND subject_key NOT LIKE $2", fx, f"%{TICKER_PREFIX}%"),
        # the kinds this sprint's code paths read or key (funds, and the no-master kinds the relink must skip)
        "real orgs' fund / no-master assets": await fingerprint(
            conn, "SELECT id, org_id, global_security_id, default_taxonomy_key, system_to FROM portfolio.assets WHERE "
                  "NOT (org_id = ANY($1::uuid[])) AND asset_type = ANY($2::text[])", orgs,
            sorted(FC.FUND_KINDS | {"money_market"} | set(NO_MASTER_ASSET_TYPES))),
        "real orgs' relinks": await fingerprint(
            conn, "SELECT id, asset_id, global_security_id FROM portfolio.asset_relinks WHERE NOT (org_id = "
                  "ANY($1::uuid[]))", orgs),
        # excluded: last_fired_at / occurrence_count — the real scheduler updates them while this runs
        "real triggers": await fingerprint(
            conn, "SELECT id, org_id, is_active, schedule_cron FROM workflow_triggers WHERE NOT (org_id = "
                  "ANY($1::uuid[]))", orgs),
        "providers": await fingerprint(
            conn, "SELECT id, key, status, lic_internal_use, lic_member_display, lic_redistribution FROM "
                  "portfolio.data_providers"),
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
            "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE (id_type = 'ticker' "
            "AND id_value LIKE $1) OR (id_type IN ('sec_class_id', 'sec_series_id') AND (id_value LIKE $2 OR "
            "id_value LIKE $3))", f"{TICKER_PREFIX}%", f"{CLASS_PREFIX}%", f"{SERIES_PREFIX}%")}
        secs = list(secs)
        runs = [r["id"] for r in await op.fetch("SELECT id FROM workflow_runs WHERE org_id = ANY($1::uuid[])",
                                                FIXTURE_ORGS)] + list(RUN_IDS)
        run_txt = [str(r) for r in runs]
        orgs, users = FIXTURE_ORGS, list(USERS)
        by_org = ("org_id = ANY($1::uuid[])", [orgs])
        plan = {t: by_org for t in ("public.workflow_run_steps", "public.workflow_runs", "public.workflow_triggers",
                                    "public.workflow_steps", "public.workflow_versions", "public.workflow_definitions",
                                    "public.domain_events", "portfolio.asset_relinks", "portfolio.asset_identifiers",
                                    "portfolio.assets", "public.config")}
        plan["public.member_todos"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan["public.audit_log"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan["public.user_roles"] = ("user_id = ANY($1::uuid[])", [users])   # SECMASTER2 FIX 1: by fixture user
        plan["portfolio.security_master_review"] = (
            "global_security_id = ANY($1::uuid[]) OR subject_key LIKE $4 OR run_id = ANY($2::uuid[]) OR "
            "decided_by = ANY($3::uuid[])", [secs, runs, users, f"%{TICKER_PREFIX}%"])
        plan["portfolio.security_reference_attributes"] = ("global_security_id = ANY($1::uuid[]) OR run_id = "
                                                           "ANY($2::uuid[])", [secs, runs])
        plan["portfolio.security_master_source_files"] = ("run_id = ANY($1::uuid[])", [runs])
        plan["portfolio.provider_call_log"] = ("run_id = ANY($1::uuid[])", [runs])
        plan["portfolio.security_data_runs"] = ("id = ANY($1::uuid[]) OR summary->>'workflow_run_id' = "
                                                "ANY($2::text[])", [runs, run_txt])
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [secs])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        cols = {r["t"] for r in await op.fetch(
            "SELECT table_schema || '.' || table_name AS t FROM information_schema.columns WHERE column_name = 'org_id'")}
        plan = {t: v for t, v in plan.items() if t in exists and (v is not by_org or t in cols)}
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan, gucs=(),
                               label="verify_secmaster2")
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
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_secmaster2"}}
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
        raise AssertionError(f"verify_secmaster2: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    for name in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):   # the LLM chokepoint
        if hasattr(extraction, name):
            def make(n):
                async def no_llm(*a, **k):
                    LLM_CALLS.append(n)
                    raise AssertionError(f"verify_secmaster2: {n} was called")
                return no_llm
            setattr(extraction, name, make(name))
    register_all()
    J.PLATFORM_JOB_ORG_IDS.add(ORG_P)
    J.TRANSPORT_FACTORY = lambda: fixture_transport()
    J.SYNC_OVERRIDES.clear()
    J.NIGHTLY_KWARGS.clear()


# ═══ Synthetic files in the real formats (every value reserved) ═════════════
FD_HEAD_EQ = ("symbol,name,summary,currency,sector,industry_group,industry,exchange,mic,market,country,state,city,"
              "zipcode,website,market_cap,isin,cusip,figi,composite_figi,shareclass_figi,delisted")
FD_HEAD_ETF = "symbol,name,currency,summary,category_group,category,family,exchange,mic,isin,delisted"
FD_HEAD_FUNDS = "symbol,name,currency,summary,category_group,category,family,exchange,mic"


def fd_etf_file() -> str:
    rows = [FD_HEAD_ETF]
    for t, (nm, cat, grp, _k) in CAT.items():
        rows.append(f'{t},"{nm}",USD,"A fixture.\nOn two lines.",{grp},{cat},ZZM Funds,PCX,ARCX,,False')
    rows.append('ZZME,ZZM2 VERIFY Open-End ETF Class,USD,"x",Equities,Large Cap,ZZM Funds,PCX,ARCX,,False')
    return "\n".join(rows)


def fd_funds_file() -> str:
    return "\n".join([FD_HEAD_FUNDS, 'ZZMAX,ZZM2 VERIFY S&P 500 Index Fund,USD,"x",Equities,Blend,ZZM Funds,NAS,',
                      'ZZMBX,ZZM2 VERIFY Bond Index Fund,USD,"x",Fixed Income,Bonds,ZZM Funds,NAS,'])


def fixture_transport(*, status_urls=None, mf_doc=None) -> FixtureTransport:
    files = {}
    for code in FD.US_EQUITY_FILES:
        files[FD.url("equities", code)] = FD_HEAD_EQ
    for code in FD.US_ETF_FILES:
        files[FD.url("etfs", code)] = fd_etf_file() if code == "PCX" else FD_HEAD_ETF
    for code in FD.US_FUND_FILES:
        files[FD.url("funds", code)] = fd_funds_file()
    files[MF.URL] = json.dumps(mf_doc or MF_DOC)
    return FixtureTransport(files, status_urls=status_urls or {})


def ctx(pool, transport, today=date(2026, 11, 7)) -> SS.SyncContext:
    rid = uuid.uuid4()
    RUN_IDS.append(rid)
    return SS.SyncContext(pool=pool, transport=transport, run_id=rid, today=today, throttle=False)


async def gsid_of(conn, id_type, value):
    rows = await platform(conn, "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE "
                                "id_type = $1 AND id_value = $2 AND valid_to IS NULL AND system_to IS NULL",
                          id_type, value)
    return rows[0]["global_security_id"] if len(rows) == 1 else (None if not rows else "AMBIGUOUS")


async def ids_of(conn, gsid) -> dict:
    out: dict = {}
    for r in await platform(conn, "SELECT id_type, id_value FROM portfolio.securities_global_identifiers WHERE "
                                  "global_security_id = $1 AND valid_to IS NULL AND system_to IS NULL", gsid):
        out.setdefault(r["id_type"], []).append(r["id_value"])
    return out


async def attrs_of(conn, gsid, provider) -> dict:
    return {r["field"]: r["value"] for r in await platform(
        conn, "SELECT field, value FROM portfolio.security_reference_attributes WHERE global_security_id = $1 AND "
              "provider_key = $2 AND valid_to IS NULL AND system_to IS NULL", gsid, provider)}


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1d (docs/SECMASTER2_DISCOVERY.md) + the live catalog they rest on")
    self_static(check, find)
    held = await platform(conn, "SELECT r.status, r.error_detail FROM workflow_runs r WHERE r.id = $1",
                          UUID("12611760-722c-40b0-872c-a84fdc8f82a6"), fetch="fetchrow")
    if held is None:
        find("1a: the held attributes run 12611760-… is no longer in workflow_runs")
    else:
        find("1a: the attributes run held on the 404 is now", f"{held['status']}: {(held['error_detail'] or '')[:160]}")


async def y_schema(conn):
    section("[Y] Schema: constraints read from the catalog BEFORE seeding; the provider, its rank, the job OFF")
    defs = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = ANY($1::text[])",
        ["sec_global_ident_type_chk", "sec_global_ident_sec_shape_chk", "security_reference_attributes_field_chk",
         "security_reference_attributes_structure_chk", "securities_global_type_chk", "asset_relinks_trigger_chk",
         "security_master_review_kind_chk", "fund_category_taxonomy_key_shape"])}
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])",
        ["uq_sec_global_ident_sec_class", "uq_sec_global_ident_issuer_assigned", "asset_relinks_asset_live_uq",
         "security_reference_attributes_live_uq", "security_master_review_open_uq", "assets_cash_active_uniq",
         "assets_internal_spv_active_uniq"])}
    asset_chk = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = "
              "'portfolio.assets'::regclass AND contype = 'c'")}
    check(bool(asset_chk) and all(f"'{v}'" in " ".join(asset_chk.values()) for v in ("financial", "units",
                                                                                    "market_price", "nav")),
          "portfolio.assets CHECKs read: the fixture assets' class / basis / valuation literals are admitted (no "
          "seeded asset is 'cash', so assets_cash_active_uniq cannot bite)", f"{sorted(asset_chk)}")
    fks = await platform(conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                               "'portfolio.asset_relinks'::regclass")
    S["catalog"] = (defs, idx)
    check(len(defs) == 8 and len(idx) == 7 and len(fks) >= 2, "constraints read from the catalog (CHECK / UNIQUE / FK)",
          f"checks {sorted(defs)} uniques {sorted(idx)} fks {len(fks)}")
    lits = (("sec_global_ident_type_chk", ("ticker", "sec_series_id", "sec_class_id")),
            ("security_reference_attributes_field_chk", M.REFERENCE_FIELDS + ("absent_runs",)),
            ("security_reference_attributes_structure_chk", ("mutual_fund", "money_market")),
            ("securities_global_type_chk", ("fund", "equity")),
            ("asset_relinks_trigger_chk", ("manual",)),
            ("security_master_review_kind_chk", ("ticker_conflict", "identifier_conflict", "type_mismatch")))
    for con, vals in lits:
        d = defs.get(con, "")
        miss = [v for v in vals if f"'{v}'" not in d]
        check(bool(d) and not miss, f"CHECK {con} admits every literal this script and the code write", f"missing {miss}")
    shape = defs.get("sec_global_ident_sec_shape_chk", "")
    check(bool(re.search(r"S\[0-9\]\{9\}", shape) and re.search(r"C\[0-9\]\{9\}", shape)
               and all(re.fullmatch(r"[SC]\d{9}", r[1] if i == 0 else r[2]) for r in MF_ROWS for i in (0, 1))),
          "every fixture series / class id has the SEC's S000000000 / C000000000 shape the CHECK demands", shape)
    u = idx.get("uq_sec_global_ident_sec_class", "")
    check("UNIQUE" in u and "sec_class_id" in u and "valid_to IS NULL" in u,
          "one LIVE row per share class id (partial unique index) — the fixture never seeds two", u)
    p = await platform(conn, "SELECT * FROM portfolio.data_providers WHERE key = 'sec_mutual_funds' AND valid_to IS NULL "
                             "AND system_to IS NULL", fetch="fetchrow")
    check(bool(p and p["capabilities"] == ["reference"] and p["status"] == "active" and p["lic_internal_use"]
               and p["lic_member_display"] == "allowed" and p["lic_redistribution"] and p["attribution"]
               and p["confirmed_on"] and "User-Agent" in (p["terms_note"] or "")
               and "personal" in (p["terms_note"] or "") and p["daily_call_budget"]),
          "licence facts recorded for sec_mutual_funds (public record: display + redistribution; the fair-access "
          "User-Agent rule, never a personal address; a daily budget)", f"{dict(p) if p else None}")
    rank = [r["provider_key"] for r in await platform(
        conn, "SELECT provider_key FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = "
              "'reference' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
    check("sec_mutual_funds" in rank and rank.index("sec_mutual_funds") >= 5 and rank[:5] == [
        "nasdaq_trader", "sec_tickers", "openfigi", "sec_13f_list", "finance_database"],
          "the rank is set: sec_mutual_funds after the five SECMASTER1 sources", f"{rank}")
    spec = J.JOBS["security_master_mutual_funds"]
    real = await platform(conn, "SELECT t.is_active, t.schedule_cron FROM workflow_definitions d JOIN workflow_triggers t ON "
                                "t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' WHERE d.org_id = $1 AND "
                                "d.name = $2", J.PLATFORM_ORG_ID, spec.name)
    check(bool(len(real) == 1 and real[0]["is_active"] is False and real[0]["schedule_cron"] == spec.cron),
          "the REAL platform job 'Security data — mutual funds' is seeded and OFF", f"{[dict(r) for r in real]}")
    role = await platform(conn, "SELECT r.rolname, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user",
                          fetch="fetchrow")
    S["role_ok"] = bool(role and role["rolbypassrls"] is False)
    check(S["role_ok"], "this script's connection role does NOT bypass RLS (rolbypassrls = false)",
          f"{dict(role) if role else None}")


async def guard_prefix(conn) -> bool:
    n_t = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE (id_type = 'ticker' "
                               "AND id_value LIKE $1) OR (id_type IN ('sec_class_id', 'sec_series_id') AND (id_value LIKE "
                               "$2 OR id_value LIKE $3))", f"{TICKER_PREFIX}%", f"{CLASS_PREFIX}%", f"{SERIES_PREFIX}%",
                         fetch="fetchval")
    n_n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                         fetch="fetchval")
    n_c = await platform(conn, "SELECT count(*) FROM portfolio.security_reference_attributes WHERE field = 'cik' AND "
                               "value LIKE $1", f"{CIK_PREFIX}%", fetch="fetchval")
    n_a = await platform(conn, "SELECT count(*) FROM portfolio.asset_identifiers WHERE id_value LIKE $1 AND NOT (org_id = "
                               "ANY($2::uuid[]))", f"{TICKER_PREFIX}%", FIXTURE_ORGS, fetch="fetchval")
    n_o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                         fetch="fetchval")
    return check(int(n_t) == 0 and int(n_n) == 0 and int(n_c) == 0 and int(n_a) == 0 and int(n_o) == 0,
                 "reserved fixture prefixes (tickers ZZM…, class C99…, series S99…, CIK 0999100…, names 'ZZM2 VERIFY', "
                 "fixture org ids) match no real row", f"identifiers {n_t}, names {n_n}, ciks {n_c}, real-org asset "
                                                        f"identifiers {n_a}, orgs {n_o}")


async def y_finance_database(conn):
    section("[Y] FinanceDatabase: today's format parses; a 404 or a format change HOLDS with the reason (pure)")
    t = FixtureTransport({FD.url("etfs", "PCX"): (FIX / "fd_etfs_PCX_2026-11-07.csv").read_text(),
                          FD.url("funds", "NAS"): (FIX / "fd_funds_NAS_2026-11-07.csv").read_text()})
    recs, st = [], None
    async for kind, item in FD.fetch(t, kinds=("etfs", "funds"), etf_files=("PCX",), fund_files=("NAS",)):
        if kind == "record":
            recs.append(item)
        else:
            st = item
    etf = [r for r in recs if r.attrs.get("is_etf") == "true"]
    fund = [r for r in recs if r.attrs.get("is_etf") is None]
    aaa = next((r for r in etf if r.ticker == "AAA"), None)
    check(bool(st and st.rows == 24 and len(etf) == 12 and len(fund) == 12 and aaa
               and aaa.attrs.get("fund_category") == "Corporate Bonds"
               and aaa.attrs.get("fund_category_group") == "Fixed Income" and aaa.security_type == "fund"),
          "today's recorded etfs/PCX.csv and funds/NAS.csv parse (multi-line summaries; category AND its group kept; "
          "mutual funds typed fund, never flagged ETF)", f"rows={st and st.rows} etf={len(etf)} funds={len(fund)} "
                                                         f"{aaa and aaa.attrs}")
    listing = json.loads((FIX / "fd_listing_2026-11-07.json").read_text())

    def published() -> FixtureTransport:
        files, status = {}, {}
        for kind, codes in (("equities", FD.US_EQUITY_FILES + ("ASE",)), ("etfs", FD.US_ETF_FILES + ("ASE",)),
                            ("funds", FD.US_FUND_FILES)):
            have = set(listing["etfs"] if kind == "etfs" else listing["equities_us"] if kind == "equities"
                       else listing["funds_us"])
            for c in codes:
                if c in have:
                    files[FD.url(kind, c)] = {"equities": FD_HEAD_EQ, "etfs": FD_HEAD_ETF, "funds": FD_HEAD_FUNDS}[kind]
                else:
                    status[FD.url(kind, c)] = 404
        files[FD.url("etfs", "PCX")] = fd_etf_file()
        return FixtureTransport(files, status_urls=status)

    # reproduce the reported failure with the provider as it was at the base, against what the source publishes
    old_src = VS.git_show(BEFORE_REF, FD_PATH) or ""
    old = types.ModuleType("finance_database_before_secmaster2")
    exec(compile(old_src, "finance_database@" + BEFORE_REF, "exec"), old.__dict__)
    err = None
    try:
        async for _k, _i in old.fetch(published()):
            pass
    except Exception as exc:  # noqa: BLE001
        err = exc
    check(bool(isinstance(err, SourceHTTPError) and err.status == 404 and "etfs/ASE.csv" in err.url
               and "ASE" not in listing["etfs"]),
          "REPRODUCED: the provider at the base asks for etfs/ASE.csv, which FinanceDatabase does not publish "
          "(recorded listing) → HTTP 404 — the held run", f"{type(err).__name__ if err else None}: {err}")
    n = 0
    async for kind, _item in FD.fetch(published()):
        n += kind == "record"
    check(n == len(CAT) + 1, "…today's provider reads exactly the published files and completes", f"{n} records")
    for label, tr, kw, needle in (
            ("404", FixtureTransport({}, status_urls={FD.url("etfs", "ASE"): 404}), {"kinds": ("etfs",),
                                                                                    "etf_files": ("ASE",)},
             "no longer publishes etfs/ASE.csv"),
            ("missing column", FixtureTransport({FD.url("etfs", "PCX"): "symbol,name,currency,summary,family,exchange"}),
             {"kinds": ("etfs",), "etf_files": ("PCX",)}, "no column(s) category_group, category"),
            ("zero rows", FixtureTransport({FD.url("etfs", "PCX"): FD_HEAD_ETF}), {"kinds": ("etfs",),
                                                                                 "etf_files": ("PCX",)},
             "zero rows")):
        e = None
        try:
            async for _k, _i in FD.fetch(tr, **kw):
                pass
        except Exception as exc:  # noqa: BLE001
            e = exc
        check(bool(isinstance(e, SourceFormatChanged) and needle in str(e)),
              f"a {label} raises SourceFormatChanged with its reason (never a silent empty load)",
              f"{type(e).__name__ if e else None}: {str(e)[:200]}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "platform"), (ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"SECMASTER2 Verify {nm}", f"{TAG}-{nm}")
            if org != ORG_P:
                await conn.execute(
                    "INSERT INTO config (org_id, config_key, config_value, value_type, category, display_order, "
                    "is_active) SELECT $1, config_key, config_value, value_type, category, display_order, true FROM "
                    "config WHERE org_id = $2 AND category = 'asset_taxonomy' AND is_active", org, ORG_LIVE)
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            # SECMASTER2 FIX 1 — since BUGFIX4 user_roles is the ONE role source (users.role only mirrors it): every
            # fixture role is a real user_roles grant, through the app's own helpers, never the string alone.
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname:
                await rbac.grant_named_role(conn, uid, org, rname)
        # the ETF class the master already holds (listed on NYSE Arca by the listing source) and an OTC holder
        for t, nm, ty, ex in (("ZZME", f"{NAME_PREFIX} Open-End ETF Class", "fund", "ARCX"),
                              ("ZZMOX", f"{NAME_PREFIX} OTC Holder Co", "equity", "OOTC"),
                              *((t, v[0], "fund", "ARCX") for t, v in CAT.items())):
            sid = await SG.create_security(conn, name=nm, security_type=ty, currency_code="USD", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=t, is_super_admin=True)
            await conn.execute("INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, "
                               "provider_key, as_of) VALUES ($1::uuid, 'exchange', $2, 'nasdaq_trader', $3)",
                               str(sid), ex, date(2026, 11, 6))
            S.setdefault("sec", {})[t] = UUID(str(sid))
    flags = await platform(conn, "SELECT (SELECT bool_and(is_test) FROM organizations WHERE id = ANY($1::uuid[])) AS o, "
                                 "(SELECT bool_and(is_test) FROM users WHERE id = ANY($2::uuid[])) AS u, "
                                 "(SELECT count(*) FROM config WHERE org_id = $3 AND category = 'asset_taxonomy') AS k",
                           FIXTURE_ORGS, list(USERS), ORG_A, fetch="fetchrow")
    S["seeded"] = check(bool(flags["o"] is True and flags["u"] is True and int(flags["k"]) > 0),
                        "fixture orgs and users are is_test = true (asserted); the fixture orgs carry the taxonomy",
                        f"{dict(flags)}")
    S["seeded"] = bool(await check_fixture_roles(conn)) and S["seeded"]


async def check_fixture_roles(conn) -> bool:
    """SECMASTER2 FIX 1 — each fixture user's role AS THE APP DECIDES IT (user_roles, read by rbac.load_principal and
    the services.authz Principal) is the one the fixture means, and users.role mirrors it. A string-only seed (the
    pre-fix shape) fails here, at the seed, instead of as 403s / run refusals far downstream."""
    from services import authz as _AZ
    bad = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, account_role, _nm, rname, _full) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            lp = await rbac.load_principal(conn, uid)
            pr = await _AZ.load_principal(conn, uid)
            want_super = rname == "super_admin"
            ok = (rname in names and rbac.mirrored_role_string(names) == account_role
                  and rbac.is_super_admin(lp) is want_super and bool(pr and pr.is_super_admin) is want_super)
            if not ok:
                bad.append((str(uid), names, account_role, lp, bool(pr and pr.is_super_admin)))
    return check(not bad, "every fixture role is a user_roles grant the app decides from (Super Admin / org admin as "
                 "meant), users.role its mirror", f"{bad}")


async def y_mutual_funds(conn):
    section("[Y] Mutual funds: N share classes with identifiers; matched not duplicated; conflict → review; "
            "idempotent; licence, rank; the job OFF")
    st = ParseStats()
    real = list(MF.parse(json.loads((FIX / "sec_company_tickers_mf_2026-11-07.json").read_text()), st))
    by = {r.ticker: r for r in real}
    fx = by.get("FXAIX")
    check(bool(st.rows == 35 and st.excluded.get("no ticker") == 1 and fx and fx.extra_ids == {
        "sec_class_id": "C000100045", "sec_series_id": "S000006027"} and fx.attrs == {"cik": "0000819118",
                                                                                         "fund_structure": "mutual_fund"}
               and by["FDRXX"].attrs.get("fund_structure") == "money_market" and "fund_structure" not in by["VTI"].attrs
               and fx.name == "FXAIX (SEC share class C000100045)"),
          "the recorded company_tickers_mf.json parses: class + series ids, CIK, structure (…XX money market; an ETF "
          "class like VTI states none); a name from SEC facts only", f"{st.rows} {st.excluded} {fx and fx.attrs}")
    pool = await get_pool()
    before_fp = await fingerprint(conn, "SELECT g.id, g.name FROM portfolio.securities_global g WHERE g.id = "
                                        "ANY($1::uuid[])", list(S["sec"].values()))
    r1 = await SS.sync_mutual_funds(ctx(pool, FixtureTransport({MF.URL: json.dumps(MF_DOC)})), return_outcomes=True)
    oc = {t: (v[0] if v else None) for t, v in (r1.pop("_by_ticker", {}) or {}).items()}
    S["mf1"] = r1
    check(bool(oc == {"ZZMAX": "new", "ZZMBX": "new", "ZZMXX": "new", "ZZME": "matched", "ZZMOX": "conflict",
                      "ZZMQ": "unmapped"} and r1["new"] == 3 and r1["kept"] == 6 and r1["excluded"] == {"no ticker": 1}),
          "the fixture file loads 3 NEW share classes; ZZME matched; ZZMOX conflict; ZZMQ unmapped; blank excluded",
          f"{oc} new={r1['new']} {r1['excluded']} {r1['unmapped_reasons']}")
    want = {"ZZMAX": ("C990000001", "S990000001", "mutual_fund", "0999100001"),
            "ZZMBX": ("C990000002", "S990000001", "mutual_fund", "0999100001"),
            "ZZMXX": ("C990000003", "S990000002", "money_market", "0999100002")}
    bad = []
    for t, (c, s, fs, cik) in want.items():
        g = await gsid_of(conn, "ticker", t)
        S.setdefault("mf", {})[t] = g
        ids = await ids_of(conn, g) if g and g != "AMBIGUOUS" else {}
        at = await attrs_of(conn, g, MF.KEY) if g and g != "AMBIGUOUS" else {}
        row = await platform(conn, "SELECT name, security_type FROM portfolio.securities_global WHERE id = $1", g,
                             fetch="fetchrow") if g and g != "AMBIGUOUS" else None
        if not (ids.get("ticker") == [t] and ids.get("sec_class_id") == [c] and ids.get("sec_series_id") == [s]
                and at.get("fund_structure") == fs and at.get("cik") == cik and at.get("exchange") == "US"
                and row and row["security_type"] == "fund" and row["name"] == f"{t} (SEC share class {c})"):
            bad.append((t, ids, at, dict(row) if row else None))
    check(not bad, "each new class: ONE security (type fund) with ticker + sec_class_id + sec_series_id, CIK, "
                   "fund_structure, exchange 'US' — re-read from the database", f"{bad[:2]}")
    e = S["sec"]["ZZME"]
    holders = await platform(conn, "SELECT count(DISTINCT global_security_id) FROM portfolio.securities_global_identifiers "
                                   "WHERE id_type = 'ticker' AND id_value = 'ZZME' AND valid_to IS NULL AND system_to "
                                   "IS NULL", fetch="fetchval")
    e_ids, e_at = await ids_of(conn, e), await attrs_of(conn, e, MF.KEY)
    check(bool(int(holders) == 1 and e_ids.get("sec_class_id") == ["C990000004"] and "exchange" not in e_at
               and "fund_structure" not in e_at and e_at.get("cik") == "0999100003"),
          "a ticker already in the master is MATCHED, not duplicated: one holder of ZZME, its class id added, its "
          "listing exchange left to the listing source", f"holders={holders} {e_ids} {e_at}")
    rv = await platform(conn, "SELECT kind, provider_key, global_security_id, status FROM portfolio.security_master_review "
                              "WHERE subject_key = 'ticker:ZZMOX' AND valid_to IS NULL AND system_to IS NULL")
    o_ids = await ids_of(conn, S["sec"]["ZZMOX"])
    check(bool(len(rv) == 1 and rv[0]["kind"] == "ticker_conflict" and rv[0]["provider_key"] == MF.KEY
               and rv[0]["status"] == "open" and "sec_class_id" not in o_ids),
          "the OTC holder's ticker is a CONFLICT: one open review item, nothing added to it", f"{[dict(r) for r in rv]}")
    fp_ids = await fingerprint(conn, "SELECT i.id, i.id_type, i.id_value FROM portfolio.securities_global_identifiers i "
                                     "WHERE i.global_security_id = ANY($1::uuid[]) AND i.valid_to IS NULL",
                               await fixture_secs(conn))
    fp_at = await fingerprint(conn, "SELECT a.id, a.field, a.value FROM portfolio.security_reference_attributes a WHERE "
                                    "a.global_security_id = ANY($1::uuid[]) AND a.valid_to IS NULL",
                              await fixture_secs(conn))
    r2 = await SS.sync_mutual_funds(ctx(pool, FixtureTransport({MF.URL: json.dumps(MF_DOC)})))
    fp_ids2 = await fingerprint(conn, "SELECT i.id, i.id_type, i.id_value FROM portfolio.securities_global_identifiers i "
                                      "WHERE i.global_security_id = ANY($1::uuid[]) AND i.valid_to IS NULL",
                                await fixture_secs(conn))
    fp_at2 = await fingerprint(conn, "SELECT a.id, a.field, a.value FROM portfolio.security_reference_attributes a "
                                     "WHERE a.global_security_id = ANY($1::uuid[]) AND a.valid_to IS NULL",
                               await fixture_secs(conn))
    check(bool(r2["new"] == 0 and r2["identifiers_added"] == 0 and r2["attributes_written"] == 0
               and r2["review_items_created"] == 0 and fp_ids2 == fp_ids and fp_at2 == fp_at),
          "an idempotent re-run writes nothing (counts AND the fixture rows' count + checksum unchanged)",
          f"{ {k: r2[k] for k in ('new', 'identifiers_added', 'attributes_written', 'review_items_created')} }")
    after_fp = await fingerprint(conn, "SELECT g.id, g.name FROM portfolio.securities_global g WHERE g.id = "
                                       "ANY($1::uuid[])", list(S["sec"].values()))
    check(after_fp == before_fp, "no existing security's name was rewritten by the load")


async def y_relink(conn):
    section("[Y] Relink: a fixture org's manual mutual fund links to the new class (asset_relinks); a digital asset "
            "'BTC' and a cash_holding do NOT")
    btc = (await SG.get_by_identifiers_bulk(conn, "ticker", ["BTC"])).get("BTC")
    check(bool(btc), "precondition: the ticker BTC resolves to exactly one master security (the Grayscale trust)",
          f"{btc and btc.get('name')}")
    check({"cash", "cash_holding", "digital_asset"} <= set(NO_MASTER_ASSET_TYPES),
          "held_away.NO_MASTER_ASSET_TYPES keeps cash, cash_holding, digital_asset", f"{sorted(NO_MASTER_ASSET_TYPES)}")
    A = {}
    for org, key, ref in (
            (ORG_A, "mf", {"asset_kind": "mutual_fund", "name": f"{NAME_PREFIX} S&P 500 Index Fund",
                           "identifiers": {"ticker": "ZZMAX"}}),
            (ORG_A, "dig", {"asset_kind": "digital_asset", "name": f"{NAME_PREFIX} Bitcoin",
                            "identifiers": {"ticker": "BTC"}}),
            (ORG_A, "cash", {"asset_kind": "cash_holding", "name": f"{NAME_PREFIX} Cash sweep",
                             "identifiers": {"ticker": "ZZMXX"}}),
            (ORG_B, "mfb", {"asset_kind": "mutual_fund", "name": f"{NAME_PREFIX} Bond Index Fund",
                            "identifiers": {"ticker": "ZZMBX"}})):
        A[key] = (org, await PM.manual_asset_for(conn, org, ref))
    S["assets"] = A
    cands = {r["asset_id"] for r in await PM.relink_candidates(conn, ORG_A)}
    check(bool(A["mf"][1] in cands and A["dig"][1] not in cands and A["cash"][1] not in cands),
          "relink_candidates (THE definition the nightly and the would-relink list share) skips the no-master kinds",
          f"{sorted(cands)}")
    done_a = await PM.relink_manual_assets(conn, ORG_A, trigger="manual")
    done_b = await PM.relink_manual_assets(conn, ORG_B, trigger="manual")
    pool = await get_pool()
    async with pool.acquire() as c2:     # an independent connection, the org's own context
        rows = {r["id"]: r["g"] for r in await scoped(c2, ORG_A, "SELECT id::text, global_security_id::text AS g FROM "
                                                      "portfolio.assets WHERE org_id = $1 AND valid_to IS NULL AND "
                                                      "system_to IS NULL", ORG_A)}
        rel = await scoped(c2, ORG_A, "SELECT asset_id::text, global_security_id::text AS g, id_type, id_value, trigger "
                                      "FROM portfolio.asset_relinks WHERE org_id = $1 AND valid_to IS NULL", ORG_A)
    check(bool(rows.get(A["mf"][1]) == str(S["mf"]["ZZMAX"]) and rows.get(A["dig"][1]) is None
               and rows.get(A["cash"][1]) is None and [d["asset_id"] for d in done_a] == [A["mf"][1]]),
          "the manual mutual fund now carries the new share class; the BTC digital asset and the cash_holding stay "
          "unlinked (re-read on an independent connection)", f"{rows} {[d['asset_id'] for d in done_a]}")
    check(bool(len(rel) == 1 and rel[0]["asset_id"] == A["mf"][1] and rel[0]["g"] == str(S["mf"]["ZZMAX"])
               and rel[0]["id_type"] == "ticker" and rel[0]["id_value"] == "ZZMAX" and rel[0]["trigger"] == "manual"),
          "…and the relink is REPORTED in portfolio.asset_relinks", f"{[dict(r) for r in rel]}")
    aud = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                    "'manual_asset_relinked' AND resource_id = $2::uuid", ORG_A, A["mf"][1],
                       fetch="fetchval")
    check(int(aud) == 1, "…and in the audit log (counted by the fixture org and asset id only)", f"{aud}")
    check(bool([d["asset_id"] for d in done_b] == [A["mfb"][1]]), "org B's manual mutual fund relinks in org B",
          f"{done_b}")
    again = await PM.relink_manual_assets(conn, ORG_A, trigger="manual")
    check(again == [], "a second relink links nothing more", f"{again}")


async def y_categories(conn):
    section("[Y] Categories: US / international / EM / cap split with reasons; contradiction → review; "
            "--refine only with the flag; a re-run changes nothing")
    m = FC.DEFAULT_CATEGORY_MAP
    keys = {r["config_key"] for r in await scoped(conn, ORG_A, "SELECT config_key FROM config WHERE org_id = $1 AND "
                                                  "category = 'asset_taxonomy'", ORG_A)}
    pure = {t: FC.propose_one(name=v[0], category=v[1], provider="finance_database", current_key=v[3],
                              asset_type="fund", mapping=m, org_keys=keys, group=v[2]) for t, v in CAT.items()}
    bad = {t: (p["status"], p["key"], p["reason"]) for t, p in pure.items() if (p["status"], p["key"]) != WANT[t]}
    check(not bad, "SPY / VXUS / EEM / IWM / BND-style funds → US large cap / global ex-US (international developed) / "
                   "emerging / small cap / investment grade; a 'Developed Markets' US fund and an international one "
                   "split by their own data; the bitcoin-named 'Corporate Bonds' fund is REVIEW", f"{bad}")
    reasons_ok = all(p["reason"] and ("name says" in p["reason"] or "category" in p["reason"]) for p in pure.values())
    check(bool(reasons_ok and "'s&p 500'" in pure["ZZMSP"]["reason"] and "'total international'" in pure["ZZMVI"]["reason"]
               and "Developed Markets" in pure["ZZMTS"]["reason"] and "'u.s.'" in pure["ZZMTS"]["reason"]),
          "every proposal carries its reason (the category, and the name token that decided it)",
          f"{pure['ZZMTS']['reason']}")
    em_bond = FC.propose_one(name="EM Bond ETF", category="Emerging Markets", provider="finance_database",
                             current_key=None, asset_type="fund", mapping=m, org_keys=keys, group="Fixed Income")
    check(em_bond["key"] == "taxonomy_mc_2_4", "the category GROUP decides an ambiguous category (Fixed Income / "
          "Emerging Markets → Emerging Market Debt, not EM equity)", f"{em_bond['key']} {em_bond['reason']}")
    # the master's categories through the attributes job's matcher (FinanceDatabase never creates)
    pool = await get_pool()
    recs = [Rec("finance_database", ticker=t, name=v[0], exchange="ARCX", security_type="fund",
                attrs={"is_etf": "true", "fund_category": v[1], "fund_category_group": v[2]}) for t, v in CAT.items()]
    recs.append(Rec("finance_database", ticker="ZZMAX", name="x", exchange="XNAS", security_type="fund",
                    attrs={"fund_category": "Blend", "fund_category_group": "Equities"}))
    rid = uuid.uuid4()
    RUN_IDS.append(rid)
    res = await M.Matcher("finance_database", as_of=date(2026, 11, 7), run_id=rid, create_new=False).process(pool, recs)
    check(bool(res.new == 0 and sum(res.matched.values()) == len(recs)),
          "the fixture categories land on the fixture securities through the matcher (none created; the new mutual "
          "fund class matched on the US composite family)", f"{res.summary()}")
    for t, v in CAT.items():
        await scoped(conn, ORG_A, "INSERT INTO portfolio.assets (org_id, global_security_id, name, asset_class, "
                     "asset_type, ownership_basis, valuation_method, default_taxonomy_key) VALUES ($1, $2, $3, "
                     "'financial', 'fund', 'units', 'market_price', $4)", ORG_A, S["sec"][t], v[0], v[3],
                     fetch="execute")
    ids = {r["name"]: r["id"] for r in await scoped(conn, ORG_A, "SELECT id::text, name FROM portfolio.assets WHERE "
                                                    "org_id = $1 AND valid_to IS NULL AND system_to IS NULL", ORG_A)}

    async def keys_now() -> dict:
        return {r["name"]: (r["id"], r["k"]) for r in await scoped(
            conn, ORG_A, "SELECT id::text, name, default_taxonomy_key AS k FROM portfolio.assets WHERE org_id = $1 AND "
                         "valid_to IS NULL AND system_to IS NULL", ORG_A)}

    k0 = await keys_now()
    buf = io.StringIO()
    with redirect_stdout(buf):
        n_dry = await BF.funds(conn, str(ORG_A), dry_run=True)
    out = buf.getvalue()
    k1 = await keys_now()
    refine_lines = [ln for ln in out.splitlines() if ln.strip().startswith("REFINE ")]
    check(bool(n_dry == 0 and k1 == k0 and len(refine_lines) == 2
               and any("taxonomy_sc_1 -> taxonomy_mc_1_1" in ln and CAT["ZZMTS"][0][:40] in ln for ln in refine_lines)
               and any("taxonomy_sc_1 -> taxonomy_mc_1_3" in ln for ln in refine_lines)),
          "--funds --dry-run writes nothing and prints a REFINE line (old -> new, reason) for each already-keyed fund",
          f"{refine_lines}")
    buf = io.StringIO()
    with redirect_stdout(buf):
        n1 = await BF.funds(conn, str(ORG_A), dry_run=False, refine=False)
    k2 = await keys_now()
    applied = {nm for nm, (i, k) in k2.items() if k != k1[nm][1]}
    want_apply = {CAT[t][0] for t in ("ZZMSP", "ZZMVI", "ZZMEM", "ZZMIW", "ZZMBN")} | {f"{NAME_PREFIX} S&P 500 Index Fund"}
    check(bool(applied == want_apply and k2[CAT["ZZMTS"][0]][1] == "taxonomy_sc_1"
               and k2[CAT["ZZMDV"][0]][1] == "taxonomy_sc_1" and k2[CAT["ZZMBT"][0]][1] is None and n1 == 6),
          "a real run WITHOUT --refine applies only 'apply' (the linked mutual fund included: its category arrived "
          "with the link); refine lines and the REVIEW stay as they were", f"{n1} {sorted(applied)}")
    check(k2[f"{NAME_PREFIX} S&P 500 Index Fund"][1] == "taxonomy_mc_1_1",
          "the relinked mutual fund gets its category from the same sources (Equities / Blend + 'S&P 500' → US large)",
          f"{k2.get(f'{NAME_PREFIX} S&P 500 Index Fund')}")
    buf = io.StringIO()
    with redirect_stdout(buf):
        n2 = await BF.funds(conn, str(ORG_A), dry_run=False, refine=True)
    k3 = await keys_now()
    check(bool(n2 == 2 and k3[CAT["ZZMTS"][0]] == (k2[CAT["ZZMTS"][0]][0], "taxonomy_mc_1_1")
               and k3[CAT["ZZMDV"][0]][1] == "taxonomy_mc_1_3" and k3[CAT["ZZMBT"][0]][1] is None),
          "--refine applies the two refine proposals (the asset id KEPT: system-axis correction); REVIEW never applied",
          f"{n2} {k3[CAT['ZZMTS'][0]]} {k3[CAT['ZZMDV'][0]]}")
    # update_asset: the LIVE row keeps its id; the outgoing version is preserved as a NEW row stamped system_to
    hist = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1 AND name = $2 AND "
                        "system_to IS NOT NULL AND default_taxonomy_key = 'taxonomy_sc_1' AND id <> $3::uuid", ORG_A,
                        CAT["ZZMTS"][0], ids[CAT["ZZMTS"][0]], fetch="fetchval")
    check(int(hist) == 1, "…the outgoing version (taxonomy_sc_1) is preserved as its own system_to-stamped row; the "
          "live row kept its id (never a bare update)", f"{hist}")
    with redirect_stdout(io.StringIO()):
        n3 = await BF.funds(conn, str(ORG_A), dry_run=False, refine=True)
    check(bool(n3 == 0 and await keys_now() == k3), "a re-run changes nothing", f"{n3}")


class CountingConn:
    """Counts the statements a function issues (fetch / fetchval / fetchrow / execute), delegating to ``c``."""

    def __init__(self, c):
        self._c = c
        self.n = 0

    async def fetch(self, *a, **k):
        self.n += 1
        return await self._c.fetch(*a, **k)

    async def fetchval(self, *a, **k):
        self.n += 1
        return await self._c.fetchval(*a, **k)

    async def fetchrow(self, *a, **k):
        self.n += 1
        return await self._c.fetchrow(*a, **k)

    async def execute(self, *a, **k):
        self.n += 1
        return await self._c.execute(*a, **k)


async def y_summary(conn):
    section("[Y] Load summary: every count equals independent SQL over the fixture master; each count's list agrees")
    fx = [str(x) for x in await fixture_secs(conn)]
    live = "s.valid_to IS NULL AND s.system_to IS NULL AND s.merged_into_id IS NULL"
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        cc = CountingConn(conn)
        sm = await SUM.summary(cc, scope=fx)
        n_queries = cc.n
        total = await conn.fetchval(f"SELECT count(*) FROM portfolio.securities_global s WHERE {live} AND s.id = "
                                    "ANY($1::uuid[])", fx)
        types_sql = {r["security_type"]: int(r["n"]) for r in await conn.fetch(
            f"SELECT security_type, count(*) AS n FROM portfolio.securities_global s WHERE {live} AND s.id = "
            "ANY($1::uuid[]) GROUP BY 1", fx)}
        struct_sql = {r["v"]: int(r["n"]) for r in await conn.fetch(
            f"SELECT COALESCE((SELECT min(a.value) FROM portfolio.security_reference_attributes a WHERE "
            "a.global_security_id = s.id AND a.field = 'fund_structure' AND a.valid_to IS NULL AND a.system_to IS NULL),"
            f" 'not_stated') AS v, count(*) AS n FROM portfolio.securities_global s WHERE {live} AND s.security_type = "
            "'fund' AND s.id = ANY($1::uuid[]) GROUP BY 1", fx)}
        ids_sql = {r["t"]: (int(r["s"]), int(r["n"])) for r in await conn.fetch(
            "SELECT i.id_type AS t, count(DISTINCT i.global_security_id) AS s, count(*) AS n FROM "
            "portfolio.securities_global_identifiers i WHERE i.valid_to IS NULL AND i.system_to IS NULL AND "
            "i.global_security_id IN (SELECT s.id FROM portfolio.securities_global s WHERE "
            f"{live} AND s.id = ANY($1::uuid[])) GROUP BY 1", fx)}
        prov_sql = {r["p"]: int(r["n"]) for r in await conn.fetch(
            "SELECT a.provider_key AS p, count(DISTINCT a.global_security_id) AS n FROM "
            "portfolio.security_reference_attributes a WHERE a.valid_to IS NULL AND a.system_to IS NULL AND "
            f"a.global_security_id IN (SELECT s.id FROM portfolio.securities_global s WHERE {live} AND s.id = "
            "ANY($1::uuid[])) GROUP BY 1", fx)}
        field_sql = {r["f"]: int(r["n"]) for r in await conn.fetch(
            "SELECT a.field AS f, count(DISTINCT a.global_security_id) AS n FROM portfolio.security_reference_attributes a "
            "WHERE a.valid_to IS NULL AND a.system_to IS NULL AND a.global_security_id IN (SELECT s.id FROM "
            f"portfolio.securities_global s WHERE {live} AND s.id = ANY($1::uuid[])) GROUP BY 1", fx)}
        price_sql = {r["c"]: int(r["n"]) for r in await conn.fetch(
            f"SELECT price_coverage AS c, count(*) AS n FROM portfolio.securities_global s WHERE {live} AND s.id = "
            "ANY($1::uuid[]) GROUP BY 1", fx)}
        review_sql = {r["k"]: int(r["n"]) for r in await conn.fetch(
            "SELECT kind AS k, count(*) AS n FROM portfolio.security_master_review WHERE status = 'open' AND valid_to IS "
            "NULL AND system_to IS NULL AND global_security_id = ANY($1::uuid[]) GROUP BY 1", fx)}
        jobs_sql = {r["j"]: r["status"] for r in await conn.fetch(
            "SELECT DISTINCT ON (summary->>'job') summary->>'job' AS j, status FROM portfolio.security_data_runs WHERE "
            "summary ? 'job' ORDER BY summary->>'job', finished_at DESC NULLS LAST")}
        lists = []
        for sec_name, items, fkeys in (("by_type", sm["by_type"], ("filter",)),
                                       ("structures", [x for t in sm["by_type"] for x in t["structures"]], ("filter",)),
                                       ("identifiers", sm["identifiers"], ("filter", "missing_filter")),
                                       ("providers", sm["providers"], ("filter",)),
                                       ("attributes", sm["attributes"], ("filter", "missing_filter")),
                                       ("prices", sm["prices"], ("filter", "with_price_filter"))):
            for it in items:
                for fk in fkeys:
                    want = {"filter": it.get("count", it.get("securities")), "missing_filter": it.get("missing"),
                            "with_price_filter": it.get("with_price")}[fk]
                    got = await SUM.securities(conn, filters=it[fk], scope=fx, limit=500)
                    if got["total"] != want:
                        lists.append((sec_name, it[fk], want, got["total"]))
    s_types = {t["security_type"]: t["count"] for t in sm["by_type"]}
    s_struct = {x["structure"]: x["count"] for t in sm["by_type"] if t["security_type"] == "fund" for x in t["structures"]}
    check(bool(sm["total"] == int(total) and int(total) >= 13 and s_types == types_sql),
          "summary: total and per-type counts == SQL (non-empty fixture master)", f"{sm['total']} {s_types} {types_sql}")
    check(bool(s_struct == struct_sql and struct_sql.get("mutual_fund") == 2 and struct_sql.get("money_market") == 1),
          "summary: funds split by structure == SQL (2 mutual funds, 1 money market)", f"{s_struct} {struct_sql}")
    s_ids = {x["id_type"]: (x["securities"], x["identifiers"]) for x in sm["identifiers"]}
    check(bool(s_ids == ids_sql and ids_sql.get("sec_class_id", (0,))[0] == 4
               and all(x["missing"] == sm["total"] - x["securities"] for x in sm["identifiers"])),
          "summary: per identifier type (securities, identifiers, missing) == SQL", f"{s_ids} {ids_sql}")
    s_prov = {x["provider_key"]: x["securities"] for x in sm["providers"]}
    check(bool(s_prov == prov_sql and prov_sql.get("sec_mutual_funds") == 4),
          "summary: per provider == SQL", f"{s_prov} {prov_sql}")
    s_field = {x["field"]: x["securities"] for x in sm["attributes"]}
    check(bool(all(s_field[f] == field_sql.get(f, 0) for f in s_field) and set(field_sql) <= set(s_field)
               and field_sql.get("fund_category_group", 0) >= 9),
          "summary: attribute coverage per field == SQL", f"{s_field} {field_sql}")
    s_price = {x["price_coverage"]: x["securities"] for x in sm["prices"]}
    check(s_price == price_sql, "summary: price coverage == SQL", f"{s_price} {price_sql}")
    s_rev = {x["kind"]: x["open"] for x in sm["review"]["by_kind"]}
    check(bool(s_rev == review_sql and sm["review"]["open"] == sum(review_sql.values())),
          "summary: the open review queue per kind == SQL", f"{s_rev} {review_sql}")
    check({j["job"]: j["status"] for j in sm["jobs"]} == jobs_sql, "summary: last run per job == SQL",
          f"{sm['jobs'][:3]}")
    check(not lists, "each count's filtered list has exactly that many securities (count == list total, every count)",
          f"{lists[:4]}")
    check(n_queries == 7, "the summary issued 7 statements — one per section, not one per row", f"{n_queries}")
    # the routes
    c, o = await api(U_SUPER, "GET", f"{_M}/summary")
    check(bool(c == 200 and o.get("permissions", {}).get("can_read") is True and o.get("list_filters")
               and (o.get("summary") or {}).get("total", 0) >= sm["total"]),
          "GET /summary as Super Admin: 200 with the envelope (permissions, list_filters)",
          f"{c} {str(o)[:200]}")
    rows, c, found, offset = [], 200, False, 0
    while c == 200 and not found and offset < 5000:   # paged: after the real load there are ~940 money market funds
        c, o = await api(U_SUPER, "GET", f"{_M}/securities?fund_structure=money_market&limit=500&offset={offset}")
        page = o.get("rows") or []
        rows += page
        found = str(S["mf"]["ZZMXX"]) in {str(r["id"]) for r in page}
        if not page:
            break
        offset += 500
    ok_rows = await platform(conn, "SELECT count(*) FROM portfolio.security_reference_attributes WHERE global_security_id "
                                   "= ANY($1::uuid[]) AND field = 'fund_structure' AND value = 'money_market' AND "
                                   "valid_to IS NULL AND system_to IS NULL", [r["id"] for r in rows],
                             fetch="fetchval") if rows else 0
    check(bool(c == 200 and rows and str(S["mf"]["ZZMXX"]) in {str(r["id"]) for r in rows}
               and int(ok_rows) == len({r["id"] for r in rows})),
          "GET /securities?fund_structure=money_market lists the fixture money market fund and ONLY money market "
          "funds (every row checked in SQL)", f"{c} {len(rows)} {ok_rows}")
    c, o = await api(U_SUPER, "GET", f"{_M}/securities?id_type=bogus")
    check(bool(c == 422 and "bogus" not in json.dumps(o)), "an unknown filter value is refused (422) without echoing it",
          f"{c} {o}")


async def y_isolation(conn):
    section("[Y] Isolation: both directions under a non-bypassing role; the routes refuse an org admin (403)")
    if not S.get("role_ok"):
        check(False, "isolation needs a role with rolbypassrls = false", "see the schema section")
        return
    a_mf, b_mf = S["assets"]["mf"][1], S["assets"]["mfb"][1]
    a_sees_b = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.asset_relinks WHERE asset_id = $1::uuid", b_mf,
                            fetch="fetchval")
    b_sees_a = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.asset_relinks WHERE asset_id = $1::uuid", a_mf,
                            fetch="fetchval")
    a_own = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.asset_relinks WHERE asset_id = $1::uuid", a_mf,
                         fetch="fetchval")
    b_own = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.asset_relinks WHERE asset_id = $1::uuid", b_mf,
                         fetch="fetchval")
    check(bool(int(a_own) == 1 and int(b_own) == 1 and int(a_sees_b) == 0 and int(b_sees_a) == 0),
          "org A reads its own relink and NOT org B's; org B its own and NOT org A's", f"{a_own} {b_own} {a_sees_b} {b_sees_a}")
    a_asset_b = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE id = $1::uuid", b_mf,
                             fetch="fetchval")
    b_asset_a = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.assets WHERE id = $1::uuid", a_mf,
                             fetch="fetchval")
    check(bool(int(a_asset_b) == 0 and int(b_asset_a) == 0), "neither org reads the other's asset (both directions)")
    for path in (f"{_M}/summary", f"{_M}/securities?security_type=fund"):
        c, o = await api(U_ADMIN_A, "GET", path)
        check(c == 403, f"org admin: GET {path} → 403", f"{c} {str(o)[:160]}")


async def y_jobs(conn):
    section("[Y] Jobs: the 404 HOLDS the attributes run with the reason + alerts; closing it lets the trigger fire "
            "again (injected instant); the mutual-fund job runs through the engine")
    pool = await get_pool()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        seeded = await J.ensure_platform_jobs(conn, org_id=ORG_P, created_by=U_SUPER, active=True,
                                              jobs={k: J.JOBS[k] for k in ("security_master_attributes",
                                                                           "security_master_mutual_funds")})
    spec = J.JOBS["security_master_attributes"]
    trig = seeded["security_master_attributes"]["trigger_id"]
    now = datetime.now(timezone.utc)

    async def fire(trigger_id, cron, tzname, after):
        occ = next_occurrences(schedule_cron=cron, timezone_name=tzname, after_utc=after, count=1)[0]
        res = await WS.run_scheduler_tick(conn, pool, now_utc=occ + timedelta(minutes=5),
                                          only_trigger_ids=[trigger_id], log=lambda m: None)
        return occ, res

    J.TRANSPORT_FACTORY = lambda: fixture_transport(status_urls={FD.url("etfs", "PCX"): 404})
    try:
        occ, res = await fire(trig, spec.cron, spec.timezone, now)
    finally:
        J.TRANSPORT_FACTORY = lambda: fixture_transport()
    held = next((e.get("run_id") for e in res.errors if str(e.get("trigger_id")) == str(trig)), None)
    row = await platform(conn, "SELECT id, status, error_detail, org_id FROM workflow_runs WHERE id = $1", held,
                         fetch="fetchrow") if held else None
    check(bool(held and row and row["status"] == "held" and row["org_id"] == ORG_P
               and "no longer publishes etfs/PCX.csv" in (row["error_detail"] or "")),
          "a 404 HOLDS the attributes run with the reason (which file, what to correct)",
          f"{res.summary()} {row and (row['error_detail'] or '')[:220]}")
    todos = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'workflow_run_held' AND related_id = $1 "
                                 "AND status = 'open'", held) if held else []
    check(bool(held and {t["user_id"] for t in todos} == {U_SUPER}), "…and alerts the Super Admin (and no one else)",
          f"{[str(t['user_id']) for t in todos]}")
    occ2, res2 = await fire(trig, spec.cron, spec.timezone, occ + timedelta(minutes=1))
    blockers = [s.get("blocking_run_id") for s in res2.skipped if "overlap" in str(s.get("reason"))]
    check(bool(held and not res2.fired and blockers and all(str(b) == str(held) for b in blockers)),
          "the held run blocks the next fire (the blocker is THIS run)", f"{res2.summary()}")
    c, o = await api(U_ADMIN_A, "POST", f"{_M}/runs/{held}/close", {"note": "verify: an org admin may not close it"})
    check(c == 403, "an org admin cannot close the held platform run (a VALID body: only the gate refuses)", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{_M}/runs/{held}/close", {"note": "verify: FinanceDatabase file list corrected"})
    row = await platform(conn, "SELECT status FROM workflow_runs WHERE id = $1", held, fetch="fetchrow")
    check(bool(c == 200 and row and row["status"] == "cancelled"),
          "the existing close path (POST …/runs/{id}/close, Super Admin) closes the held run", f"{c} {o}")
    occ3, res3 = await fire(trig, spec.cron, spec.timezone, occ2 + timedelta(minutes=1))
    check(bool(res3.fired and res3.fired[0]["status"] == "completed"),
          "…and the trigger fires again (injected instant) — this time the run completes", f"{res3.summary()}")
    spec_m = J.JOBS["security_master_mutual_funds"]
    occ4, res4 = await fire(seeded["security_master_mutual_funds"]["trigger_id"], spec_m.cron, spec_m.timezone, now)
    fired = res4.fired[0] if res4.fired else None
    steps = await platform(conn, "SELECT rs.status, rs.result FROM workflow_run_steps rs WHERE rs.workflow_run_id = $1",
                           fired["run_id"]) if fired else []
    outs = [json.loads(s["result"]) if isinstance(s["result"], str) else s["result"] for s in steps]
    hd = ((outs[0] or {}).get("handler_data") or {}) if outs else {}
    check(bool(fired and fired["status"] == "completed" and len(steps) == 1 and hd.get("source") == MF.KEY
               and hd.get("new") == 0 and "_by_ticker" not in hd),
          "the mutual-fund job runs through the engine; its output is the counts (an already-loaded file adds nothing)",
          f"{res4.summary()} {str(hd)[:200]}")


async def y_real_rows(conn):
    section("[Y] Real rows and the live master unchanged (count + checksum, keyed by real id)")
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
    mine = re.findall(r"^\s*ok \d+ - (?:summary: every count|summary fails closed|link helpers keep|list: the server)",
                      out, re.M)
    check(len(mine) >= 4, "the secmaster2 render tests ran and passed (links carry the server's filters; fail closed)",
          f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_secmaster1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_secmaster1.py")], REPO, timeout=2700, env=env)
    t = nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(t.ok and not fails), "verify_secmaster1.py: 0 FAIL (its own final TOTAL line + exit code)",
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
    print(f"[INFO] database via {how}")
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
        await run("finance database", y_finance_database, conn)
        if not S.get("catalog") or not await guard_prefix(conn):
            check(False, "seeding needs the catalog read and the reserved prefixes free", "every DB section skipped")
        else:
            pre = await counts(conn)
            S["real"] = await real_fingerprints(conn)
            await run("seed", seed, conn)
            if S.get("seeded"):
                await run("mutual funds", y_mutual_funds, conn)
                if S.get("mf"):
                    await run("relink", y_relink, conn)
                    if S.get("assets"):
                        await run("categories", y_categories, conn)
                        await run("isolation", y_isolation, conn)
                    await run("summary", y_summary, conn)
                else:
                    check(False, "relink, categories, summary and isolation need the mutual-fund load",
                          "the mutual-fund section failed")
                await run("jobs", y_jobs, conn)
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
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(not left, "zero fixture rows remain (securities, identifiers, attributes, review items, call log, job "
                            "history, assets, relinks, audit, todos, workflow rows, config, orgs, users)", f"{left}")
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
