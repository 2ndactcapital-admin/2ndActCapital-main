"""verify_rep7 — Replication Studio, sprint 7 (REP7): held-away accounts and the
portfolio X-ray — read-only positions and lots for accounts held elsewhere
(manual entry, file upload through rep3's mapping, Chancery statements through
the 'statement_positions' ensemble, an aggregator interface with nothing
connected), held-away data in household wash-sale control, the gain budget and
the loss bank, the stale-data confirmation task, the trade-path guard, and the
X-ray with its one-click join request.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep7.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``), the Auth0 profile call, and
the statement extraction ensemble (``held_away.statement_ensemble`` and
``held_away.STATEMENT_READER``). NO network: the real httpx network transport
is replaced by one that records and refuses — any attempt is a failed check.

REAL: the app through an ASGI transport (middleware, Principal, every gate),
the workflow engine (human tasks), rep2 / rep3 / rep4 / rep5 / rep6 services,
RLS and the live database. The script's own connection is app_service
(rolbypassrls = false, asserted).

FIXTURES: three orgs (99000000-…-0000000e7001 / e7002 / e7003, is_test = true);
users created explicitly with the auth0_sub their token carries and an
@test.local email; masked account numbers only (raw ZZ7…); fixture securities
carry the reserved prefix ZZR7 and the name prefix 'REP7 VERIFY'; households
'REP7 Verify Household …'; the document 'REP7 VERIFY statement.pdf' (all
checked absent from real rows first).

TEARDOWN (start and end): the operator DSN (role postgres) with
execution.teardown / models.teardown / replica.teardown = 'fixture' in its
transaction — tables in FK child-before-parent order read from pg_constraint,
every DELETE keyed by this run's fixture ids; then every counted table is
re-read for its exact pre-test count, and real rows are compared by count +
checksum.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import uuid
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

sys.path.insert(0, str(API_DIR))
# Every import at the top (after hydrate: the pool reads DATABASE_URL lazily, on first get_pool()).
import main  # noqa: E402
from services import account_models as AM  # noqa: E402
from services import ai_ensembles as AE  # noqa: E402
from services import execution as X  # noqa: E402
from services import harvest as HV  # noqa: E402
from services import held_away as HA  # noqa: E402
from services import held_away_adapters as AD  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import loss_bank as LB  # noqa: E402
from services import model_upload as U  # noqa: E402
from services import models as M  # noqa: E402
from services import orgs as ORGS  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import wash_sale as WS  # noqa: E402
from services import xray as XR  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

BEFORE_REF = "759b7e4"   # rep7-work base — main after the rep6 merge + schema refresh
TAG = "rep7verify"
PREFIX = "ZZR7"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("S", "W30", "W31", "LS", "LS2", "Q", "G", "G2", "DQ", "DS", "K", "IR", "FUND", "FUND3", "STK", "C1", "C2",
            "C3", "C4", "FUND2")
FUND_TYPES = {"FUND": "fund", "FUND3": "fund", "FUND2": "fund"}
SEC_NAMES = {k: f"REP7 VERIFY {k}" for k in SEC_KEYS}
HH_NAMES = ("REP7 Verify Household A", "REP7 Verify Household X")
DOC_NAME = "REP7 VERIFY statement.pdf"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e7{n:03d}")


ORG_A, ORG_B, ORG_C = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_C]
(U_SUPER, U_ADMIN1, U_ADMIN2, U_ADMIN3, U_VIEWER, U_MEMBER, U_ADMIN_B, U_NEWADMIN) = (_id(110 + i) for i in range(8))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP7 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP7 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP7 Admin Checker"),
    U_ADMIN3: (ORG_A, "org_admin", "admin3", "org_admin", "REP7 Admin Reviewer"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep7_viewer", "REP7 Viewer"),
    U_MEMBER: (ORG_A, "member", "member", "rep7_member", "REP7 Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP7 Org B Admin"),
    U_NEWADMIN: (ORG_C, "member", "newadmin", None, "REP7 New Org Admin"),
}
HA_PERMS = ("view_held_away", "manage_held_away", "approve_held_away")
VIEWER_PERMS = ("view_held_away", "view_replication", "view_proposals", "view_tax")
MEMBER_PERMS = ("view_replication",)

ENT_A, ENT_A2, ENT_X, ENT_G, ENT_B = _id(201), _id(202), _id(203), _id(204), _id(205)
HH_A, HH_X = _id(211), _id(212)
(ACCT_M, ACCT_HA, ACCT_HIRA, ACCT_XM, ACCT_XH, ACCT_G, ACCT_B) = (_id(301 + i) for i in range(7))
ACCOUNTS = {   # id: (org, custodian_code, registration, tax_status, household, entity, held_away, raw number)
    ACCT_M: (ORG_A, "ALTRUIST", "individual", "taxable", HH_A, ENT_A, False, "ZZ70000001"),
    ACCT_HA: (ORG_A, "SCHWAB", "individual", "taxable", HH_A, ENT_A2, True, "ZZ70000002"),
    ACCT_HIRA: (ORG_A, "FIDELITY", "ira", "tax_deferred", HH_A, ENT_A, True, "ZZ70000003"),
    ACCT_XM: (ORG_A, "ALTRUIST", "individual", "taxable", HH_X, ENT_X, False, "ZZ70000004"),
    ACCT_XH: (ORG_A, "SCHWAB", "individual", "taxable", HH_X, ENT_X, True, "ZZ70000005"),
    ACCT_G: (ORG_A, "ALTRUIST", "individual", "taxable", None, ENT_G, False, "ZZ70000006"),
    ACCT_B: (ORG_B, "SCHWAB", "individual", "taxable", None, ENT_B, True, "ZZ70000007"),
}
DOC_A = _id(401)
FS1 = _id(501)
TODAY = date.today()
YEAR = TODAY.year
STALE_DAYS = 30
CENT = Decimal("0.01")

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.account_balances_daily", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.tax_lots", "portfolio.transactions", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "public.execution_instructions", "public.execution_instruction_lots",
    "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
    "portfolio.replica_securities", "portfolio.replica_terms_versions", "portfolio.replica_holdings_snapshots",
    "portfolio.replica_holdings_rows", "portfolio.replica_viability_reports", "public.fee_schedules",
    "public.fee_schedule_tiers", "public.account_model_links", "public.rebalance_proposals",
    "public.rebalance_proposal_trades", "public.tax_profiles", "public.declared_outside_trades",
    "public.loss_bank_entries", "public.documents", "public.document_extractions", "public.document_record_links",
    "public.held_away_sources", "public.held_away_imports", "public.held_away_records", "public.stale_confirmations",
    "public.xray_runs", "public.xray_lines",
)
NEW_TABLES = ("public.held_away_sources", "public.held_away_imports", "public.held_away_records",
              "public.stale_confirmations", "public.xray_runs", "public.xray_lines")

S: dict = {}
NETWORK_CALLS: list[str] = []
READER_CALLS: list[str] = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def git_show(ref: str, path: str) -> str:
    code, out, _ = run_cmd(["git", "show", f"{ref}:{path}"], REPO, timeout=60)
    return out if code == 0 else ""


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def D(v) -> Decimal:
    return Decimal(str(v))


def money(v) -> Decimal:
    return D(v).quantize(CENT)


def jl(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


# ═══ DB helpers (verify's own app_service connection; RLS context explicit) ═══
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


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint must never touch (count + checksum)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("tax_lots", "portfolio.tax_lots"), ("transactions", "portfolio.transactions"),
                        ("execution_instructions", "public.execution_instructions"),
                        ("rebalance_proposals", "public.rebalance_proposals"),
                        ("account_model_links", "public.account_model_links"),
                        ("loss_bank_entries", "public.loss_bank_entries"),
                        ("declared_outside_trades", "public.declared_outside_trades"),
                        ("documents", "public.documents"), ("document_record_links", "public.document_record_links"),
                        *[(t.split(".")[1], t) for t in NEW_TABLES]):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
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


_FK_CACHE: dict = {}


async def fk_children(conn, table: str):
    if table not in _FK_CACHE:
        _FK_CACHE[table] = await conn.fetch("""
            SELECT cn.nspname || '.' || cc.relname AS child,
                   array_agg(ca.attname ORDER BY k.ord) AS child_cols,
                   array_agg(pa.attname ORDER BY k.ord) AS parent_cols
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord) ON true
            JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
            JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.ck
            JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
            WHERE c.contype::text = 'f' AND c.confrelid = $1::regclass
            GROUP BY c.oid, cn.nspname, cc.relname""", table)
    return _FK_CACHE[table]


async def delete_tree(conn, table: str, where: str, params: list, depth=0, log=None, path=()):
    """Child-before-parent delete for a FIXTURE-ID parent (users / roles / orgs of this run only)."""
    if depth > 14:
        raise RuntimeError(f"FK recursion too deep at {table} via {' > '.join(path)}")
    path = path + (table,)
    for fk in await fk_children(conn, table):
        if fk["child"] in path:
            continue
        cc = ", ".join(f'"{c}"' for c in fk["child_cols"])
        pc = ", ".join(f'"{c}"' for c in fk["parent_cols"])
        await delete_tree(conn, fk["child"], f"({cc}) IN (SELECT {pc} FROM {table} WHERE {where})",
                          params, depth + 1, log, path)
    status = await conn.execute(f"DELETE FROM {table} WHERE {where}", *params)
    n = int(status.rsplit(" ", 1)[-1])
    if n and log is not None:
        log.append(f"{table}: {n}")


async def fk_order(conn, tables: list[str]) -> list[str]:
    edges = {t: set() for t in tables}
    rows = await conn.fetch("""
        SELECT cn.nspname || '.' || cc.relname AS child, pn.nspname || '.' || pc.relname AS parent
        FROM pg_constraint c JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
        JOIN pg_class pc ON pc.oid = c.confrelid JOIN pg_namespace pn ON pn.oid = pc.relnamespace
        WHERE c.contype = 'f'""")
    for r in rows:
        if r["child"] in edges and r["parent"] in edges and r["child"] != r["parent"]:
            edges[r["child"]].add(r["parent"])
    children_of = {t: {c for c, ps in edges.items() if t in ps} for t in tables}
    out, done = [], set()
    while len(out) < len(tables):
        ready = [t for t in tables if t not in done and children_of[t] <= done]
        if not ready:
            raise RuntimeError(f"FK cycle among {sorted(set(tables) - done)}")
        for t in ready:
            out.append(t)
            done.add(t)
    return out


async def fixture_security_ids(conn) -> list:
    rows = await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")
    ids = {r["global_security_id"] for r in rows}
    ids |= {r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name = ANY($1::text[])",
                                              list(SEC_NAMES.values()))}
    return list(ids)


async def org_tables(conn) -> list[str]:
    return [r["t"] for r in await conn.fetch(
        "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
        "'information_schema') ORDER BY 1")]


async def sweep_fixture_orgs(conn, orgs: list, skip: set, log: list[str]) -> None:
    """Rows under a FIXTURE org in any table the plan does not name (a crashed run's leftovers). Keyed by fixture
    org id only; child-before-parent from pg_constraint."""
    found = []
    for t in await org_tables(conn):
        if t in skip:
            continue
        if await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {t} WHERE org_id = ANY($1::uuid[]))", orgs):
            found.append(t)
    if not found:
        return
    try:
        order = await fk_order(conn, found)
    except RuntimeError:
        order = None
    for t in order or found:
        if order is None:
            await delete_tree(conn, t, "org_id = ANY($1::uuid[])", [orgs], log=log)
            continue
        status = await conn.execute(f"DELETE FROM {t} WHERE org_id = ANY($1::uuid[])", orgs)
        n = int(status.rsplit(" ", 1)[-1])
        if n:
            log.append(f"{t}: {n} (sweep)")


async def teardown(_conn=None) -> list[str]:
    """Operator connection, the teardown GUCs, FK child-before-parent, every DELETE keyed by this run's fixture
    ids (orgs, users, securities, households, the document). Audit, events, todos and workflow rows of this run
    go before the fixture users."""
    log: list[str] = []
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('execution.teardown', 'fixture', true), "
                             "set_config('models.teardown', 'fixture', true), "
                             "set_config('replica.teardown', 'fixture', true)")
            secs = await fixture_security_ids(op)
            users = list(USERS)
            orgs = FIXTURE_ORGS
            by_org = "org_id = ANY($1::uuid[])"
            org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
            snaps = "snapshot_id IN (SELECT id FROM portfolio.replica_holdings_snapshots WHERE " \
                    "fund_global_security_id = ANY($1::uuid[]))"
            plan = {
                "public.xray_lines": (by_org, [orgs]),
                "public.xray_runs": (by_org, [orgs]),
                "public.stale_confirmations": (by_org, [orgs]),
                "public.held_away_records": (by_org, [orgs]),
                "public.held_away_imports": (by_org, [orgs]),
                "public.held_away_sources": (by_org, [orgs]),
                "public.loss_bank_entries": (by_org, [orgs]),
                "public.declared_outside_trades": (by_org, [orgs]),
                "public.harvest_candidates": (by_org, [orgs]),
                "public.harvest_runs": (by_org, [orgs]),
                "public.tax_profiles": (by_org, [orgs]),
                "public.rebalance_proposal_trades": (by_org, [orgs]),
                "public.account_transition_plans": (by_org, [orgs]),
                "public.rebalance_proposals": (by_org, [orgs]),
                "public.account_model_links": (by_org, [orgs]),
                "public.execution_instruction_events": (by_org, [orgs]),
                "public.execution_fills": (by_org, [orgs]),
                "public.execution_instruction_lots": (by_org, [orgs]),
                "public.execution_instructions": (by_org, [orgs]),
                "public.fee_exclusions": (by_org, [orgs]),
                "public.fee_assignments": (by_org, [orgs]),
                "public.document_record_links": (by_org, [orgs]),
                "public.document_extractions": (by_org, [orgs]),
                "public.documents": (by_org, [orgs]),
                "public.member_todos": org_or_user,
                "public.domain_event_deliveries": (by_org, [orgs]),
                "public.domain_events": (by_org, [orgs]),
                "public.audit_log": org_or_user,
                "public.workflow_run_steps": (by_org, [orgs]),
                "public.agent_proposals": (by_org, [orgs]),
                "public.workflow_runs": (by_org, [orgs]),
                "public.workflow_steps": (by_org, [orgs]),
                "public.workflow_versions": (by_org, [orgs]),
                "public.workflow_definitions": (by_org, [orgs]),
                "portfolio.replica_viability_reports": (by_org, [orgs]),
                "portfolio.replica_terms_versions": (by_org, [orgs]),
                "portfolio.replica_model_specs": (by_org, [orgs]),
                "portfolio.replica_registry_keys": (by_org, [orgs]),
                "portfolio.model_events": (by_org, [orgs]),
                "portfolio.model_version_positions": (by_org, [orgs]),
                "portfolio.model_sleeves": (by_org, [orgs]),
                "portfolio.model_versions": (by_org, [orgs]),
                "portfolio.models": (by_org, [orgs]),
                "public.fee_schedule_tiers": (by_org, [orgs]),
                "public.fee_schedules": (by_org, [orgs]),
                "portfolio.replica_securities": (by_org, [orgs]),
                "portfolio.replica_holdings_rows": (snaps, [secs]),
                "portfolio.replica_holdings_snapshots": ("fund_global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.tax_lots": (by_org, [orgs]),
                "portfolio.transactions": (by_org, [orgs]),
                "portfolio.positions": (by_org, [orgs]),
                "portfolio.assets": (by_org, [orgs]),
                "public.account_balances_daily": (by_org, [orgs]),
                "public.account_owners": (by_org, [orgs]),
                "public.accounts": (by_org, [orgs]),
                "public.households": (by_org, [orgs]),
                "public.entities": (by_org, [orgs]),
                "public.org_settings": (by_org, [orgs]),
                "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
            }
            exists = {r["t"] for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind = 'r'")}
            if "public.assistant_activities" in exists:
                plan["public.assistant_activities"] = org_or_user
            for table in await fk_order(op, [t for t in plan if t in exists]):
                where, args = plan[table]
                status = await op.execute(f"DELETE FROM {table} WHERE {where}", *args)
                n = int(status.rsplit(" ", 1)[-1])
                if n:
                    log.append(f"{table}: {n}")
            await sweep_fixture_orgs(op, orgs, set(plan), log)
            await delete_tree(op, "public.users", "id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                              [users, f"%{TAG}%"], log=log)
            await delete_tree(op, "public.roles", "org_id = ANY($1::uuid[])", [orgs], log=log)
            await delete_tree(op, "public.organizations", "id = ANY($1::uuid[])", [orgs], log=log)
    finally:
        await close_conn(op)
    return log


TEARDOWN_RETRYABLE = (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
                      ConnectionError)


async def teardown_retrying(_conn=None) -> list[str]:
    try:
        return await teardown()
    except TEARDOWN_RETRYABLE as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; rolled back, retrying once")
        try:
            return await teardown()
        except Exception as exc2:  # noqa: BLE001
            check(False, "teardown retry failed", f"{type(exc2).__name__}: {exc2}")
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL", flush=True)
            os._exit(1)


async def pre_teardown(conn):
    log = await teardown_retrying(conn)
    if log:
        find("pre-run cleanup removed an earlier run's leftovers", "; ".join(log))
    else:
        print("[INFO] pre-run cleanup: nothing left from an earlier run")
    check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")


async def leftovers(conn) -> int:
    n = 0
    orgs = FIXTURE_ORGS
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [orgs]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])", [list(SEC_NAMES.values())]),
        ("SELECT count(*) FROM portfolio.replica_holdings_snapshots s JOIN portfolio.securities_global g ON "
         "g.id = s.fund_global_security_id WHERE g.name LIKE 'REP7 VERIFY%'", []),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    op = await operator_conn()
    try:
        for t in await org_tables(op):
            n += int(await op.fetchval(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", orgs))
    finally:
        await close_conn(op)
    return n


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api_raw(uid, method, path, body=None, timeout=240.0, files=None, data=None):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep7"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
        kw["data"] = data or {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


async def api(uid, method, path, body=None, timeout=240.0, files=None, data=None):
    code, content, _ = await api_raw(uid, method, path, body, timeout, files, data)
    try:
        return code, json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        return code, {"raw": content[:300].decode("utf-8", "replace")}


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep7: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def pool_():
    return await get_pool()


async def in_org(fn, org=None):
    """Run ``fn(conn)`` on the app pool in ``org``'s RLS context."""
    pool = await pool_()
    with _ctx(org or ORG_A):
        async with pool.acquire() as c2:
            return await fn(c2)


async def account_row(account_id) -> dict:
    return await in_org(lambda c: AM.load_account(c, ORG_A, account_id))


def pos(row, key, w):
    sid = S.get(f"sec_{key}") if key != "cash" else None
    return {"row_num": row, "global_security_id": str(sid) if sid else None, "raw_ticker": f"{PREFIX}{key}" if sid
            else None, "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": SEC_NAMES.get(key, "Cash"),
            "target_weight": D(w), "asset_class": None, "sleeve_label": None, "drift_min": None, "drift_max": None,
            "alternates": [], "restrictions": None, "notes": None,
            "resolution_state": "resolved" if sid else "cash", "resolution_note": None}


async def make_model(name: str, positions: list[dict], *, fund_entry=None) -> tuple[UUID, UUID]:
    pool = await pool_()
    with _ctx(ORG_A):
        async with pool.acquire() as conn:
            if fund_entry:
                model, ref = await M.create_fund_model(conn, org_id=ORG_A, actor_id=U_ADMIN1, name=name,
                                                       replica_security_id=fund_entry,
                                                       disclosure="REP7 verify fixture disclosure")
            else:
                model, ref = await M.create_model(conn, org_id=ORG_A, actor_id=U_ADMIN1, name=name, source="upload")
            v = await M._create_version(conn, org_id=ORG_A, model=model, origin="manual", maker_ids=[U_ADMIN1],
                                        created_by=U_ADMIN1, positions=positions)
        await M.publish(pool, [ref])
        await M.submit(pool, org_id=ORG_A, actor_id=U_ADMIN1, model_id=model["id"], version_id=v["id"])
        await M.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, model_id=model["id"], version_id=v["id"],
                       outcome="approve")
    return UUID(str(model["id"])), UUID(str(v["id"]))


async def join(acct, model):
    """rep5's real join path: a maker requests, a checker approves → the transition proposal."""
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join", {"model_id": str(model), "settings": {}})
    lid = (o.get("link") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct}/model/changes/{lid}/approve", {})
    return c, c2, o2.get("transition_proposal_id"), o, o2


async def approve_import(iid, uid=None):
    return await api(uid or U_ADMIN2, "POST", f"/api/v1/held-away/imports/{iid}/approve", {})


async def manual_import(acct, as_of: date, rows: list[dict], uid=None):
    return await api(uid or U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{acct}/imports/manual",
                     {"as_of": as_of.isoformat(), "rows": rows})


def tk(key):
    return f"{PREFIX}{key}"


def ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# Every literal the seed and the tests write into a CHECK-constrained column — checked against the live catalog.
SEED_INSTRUMENT_CLASS = "index_etf"
SEED_CHECK_LITERALS = (
    ("portfolio.positions", "positions_authority_chk", ("manual", "stated")),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.transactions", "transactions_authority_chk", ("manual", "stated")),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long", "short")),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open", "closed")),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.replica_securities", "replica_securities_status_chk", ("active",)),
    ("portfolio.replica_terms_versions", "replica_terms_versions_status_chk", ("approved",)),
    ("portfolio.replica_holdings_snapshots", "replica_holdings_snapshots_status_chk", ("live",)),
    ("portfolio.replica_holdings_rows", "replica_holdings_rows_asset_type_chk", ("equity",)),
    ("portfolio.replica_holdings_rows", "replica_holdings_rows_state_chk", ("resolved",)),
    ("portfolio.replica_holdings_rows", "replica_holdings_rows_via_chk", ("cusip",)),
    ("portfolio.replica_viability_reports", "replica_viability_reports_overall_chk", ("yes",)),
    ("portfolio.models", "models_source_chk", ("upload", "fund_replica")),
    ("public.fee_schedules", "fee_schedules_product_type_check", ("ASSET_MANAGEMENT",)),
    ("public.fee_schedules", "fee_schedules_rate_type_check", ("BPS",)),
    ("public.fee_schedules", "fee_schedules_tier_method_check", ("GRADUATED",)),
    ("public.fee_schedules", "fee_schedules_billing_frequency_check", ("QUARTERLY",)),
    ("public.fee_schedules", "fee_schedules_billing_timing_check", ("ARREARS",)),
    ("public.fee_schedules", "fee_schedules_valuation_method_check", ("PERIOD_END",)),
    ("public.fee_schedules", "fee_schedules_status_check", ("APPROVED",)),
    ("public.declared_outside_trades", "declared_outside_trades_side_chk", ("buy", "sell")),
    ("public.declared_outside_trades", "declared_outside_trades_loss_chk", ("yes",)),
    ("public.declared_outside_trades", "declared_outside_trades_class_chk", ("taxable",)),
    ("public.tax_profiles", "tax_profiles_filing_chk", ("married_filing_jointly",)),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("join", "manual")),
    ("public.loss_bank_entries", "loss_bank_entries_source_chk", ("held_away", "declared_outside")),
    ("public.held_away_sources", "held_away_sources_kind_chk", ("manual", "file", "statement", "aggregator")),
    ("public.held_away_sources", "held_away_sources_status_chk", ("active", "not_connected")),
    ("public.held_away_imports", "held_away_imports_status_chk", ("draft", "approved", "rejected")),
    ("public.stale_confirmations", "stale_confirmations_purpose_chk", ("proposal", "general")),
    ("public.xray_runs", "xray_runs_trigger_chk", ("on_demand", "scheduler")),
    ("public.xray_lines", "xray_lines_kind_chk", ("holding", "constituent", "fund")),
)


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e reported with signatures and chosen designs")
    sig = {n: str(inspect.signature(f)) for n, f in (
        ("held_away.refusal", HA.refusal), ("held_away.create_manual_import", HA.create_manual_import),
        ("held_away.create_file_import", HA.create_file_import),
        ("held_away.create_statement_import", HA.create_statement_import),
        ("held_away.decide_import", HA.decide_import), ("held_away.staleness", HA.staleness),
        ("held_away.realized_elsewhere", HA.realized_elsewhere), ("rebalance_proposals.realized_ytd", RP.realized_ytd),
        ("rebalance_proposals.org_tick", RP.org_tick), ("wash_sale.load_context", WS.load_context),
        ("loss_bank.ingest", LB.ingest), ("model_upload.mapping_from_request", U.mapping_from_request),
        ("model_upload.detect_header", U.detect_header), ("human_tasks.register_precheck", HT.register_precheck),
        ("xray.compute", XR.compute), ("xray.open_join", XR.open_join))}
    for k, v in sig.items():
        find(f"signature {k}", v)
    find("1a held-away today", "accounts.is_held_away is set by the custody CSV importer (custody/importer.py); readers: "
         "account_models (harvest options), execution (display), custody/trading.route_trade_list (routed to MANUAL), "
         "rebalance_proposals.compute (a warning only) and .execute (executor = member), fee_calc, model_upload, "
         "tax_overview. Trade paths a held-away account could reach: propose_change (join), _generate (every trigger), "
         "execute, harvest.scan_account (no check at all), create_instructions / route_trade_list — each now refuses")
    find("1b chosen design", "held-away rows live in portfolio.positions / tax_lots / transactions (one place for every "
         "reader); the missing source + import + as-of provenance is public.held_away_records (a link table)")
    find("1c double count", "wash_sale.load_context reads every household account's transactions (held-away ones now "
         "too, source 'held_away') plus declared_outside_trades; a declaration matched by a held-away transaction "
         "(held_away_records.matched_declared_id) is skipped there and its loss-bank entry closed")
    find("1d reuse", "rep3: model_upload.precheck / read_workbook / detect_header / mapping_from_request take a field "
         "vocabulary (fields=) — the held-away file uses rep3's machinery with its own fields; Chancery documents link "
         "via document_record_links (record_type 'held_away_account'); ensemble task_key 'statement_positions', "
         "Claude refused via replica_terms.is_claude")
    find("1e registry", "a held fund matches portfolio.replica_securities.global_security_id; holdings + as-of from "
         "replica_holdings.latest_snapshot / snapshot_rows; expense_ratio_net_pct (a percent) via "
         "replica_models.term_value; verdict = replica_viability.list_reports()[0].overall; sector only from the "
         "holdings file's raw 'Sector' column, else 'unclassified'")
    src_rf = inspect.getsource(HA.read_file)
    check("U.mapping_from_request" in src_rf and "U.detect_header" in src_rf and "U.read_workbook" in src_rf
          and "U.precheck" in src_rf, "1d: the held-away file path calls rep3's precheck / reader / header / mapping "
          "(no second parser)")
    check(U.detect_header([["Ticker", "Weight"]]) == 1 and U.suggest_mapping(["Ticker", "Weight"])[1]["field"] == "weight",
          "1d: rep3's own model vocabulary is unchanged (fields= defaults to it)")
    check("statement_positions" in AE.KNOWN_TASK_KEYS,
          "1d: 'statement_positions' is a known ensemble task key")


async def y_schema(conn):
    section("[Y] Schema landed: tables, constraints, partial uniques, RLS with a policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep7.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep7.py --verify: SCHEMA OK (columns, CHECKs, partial "
          "uniques, RLS on, a NULLIF org policy per operation, guards, grants, permissions)",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY($1::text[])",
        [t.split(".")[1] for t in NEW_TABLES])}
    S["indexes"] = idx
    check(all(("WHERE" in idx.get(n, "")) for n in ("held_away_sources_live_uq", "held_away_records_declared_uq",
                                                    "stale_confirmations_open_uq", "xray_runs_daily_uq")),
          "partial unique indexes read from the catalog before seeding")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog (CHECKs, lookup tables, enums) before seeding")
    row = await platform(conn, "SELECT key, enabled FROM portfolio.replica_instrument_classes WHERE key = $1 "
                               "AND valid_to IS NULL AND system_to IS NULL", SEED_INSTRUMENT_CLASS, fetch="fetchrow")
    if check(row is not None and row["enabled"] is True, f"instrument class {SEED_INSTRUMENT_CLASS!r} is a current key"):
        S["instrument_class"] = row["key"]
    codes = {r["code"] for r in await platform(conn, "SELECT code FROM public.transaction_types WHERE code = "
                                                     "ANY($1::text[])", ["buy", "sell"])}
    check(codes == {"buy", "sell"}, "transaction_type_code 'buy' / 'sell' exist", f"{sorted(codes)}")
    labels = {r["l"] for r in await platform(conn, "SELECT enumlabel AS l FROM pg_enum WHERE enumtypid = "
                                                   "'public.entity_type'::regtype")}
    check("individual" in labels, "entity_type 'individual' is an enum label")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'::text", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    check(not bad, f"every CHECK-constrained seed literal is admitted ({len(SEED_CHECK_LITERALS)} constraints)",
          "; ".join(bad))
    find("unconstrained seed text", "accounts.custodian_code / registration_type / tax_status carry no CHECK; "
         "wash_sale.tax_class classifies 'ira' + 'tax_deferred' as tax-advantaged")
    check(WS.tax_class({"tax_status": "tax_deferred", "registration_type": "ira"}) == "tax_advantaged"
          and WS.tax_class({"tax_status": "taxable", "registration_type": "individual"}) == "taxable",
          "fixture registration / tax_status literals classify as intended")


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'REP7 VERIFY%'",
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM public.households WHERE name = ANY($1::text[])", list(HH_NAMES),
                       fetch="fetchval")
    d = await platform(conn, "SELECT count(*) FROM public.documents WHERE original_filename = $1", DOC_NAME,
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    a = await platform(conn, "SELECT count(*) FROM public.accounts WHERE account_number_masked LIKE '%' || $1",
                       "7000000", fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(h) == 0 and int(d) == 0 and int(o) == 0,
                 f"the reserved prefixes ({PREFIX}, 'REP7 VERIFY', the household names, the document, the fixture orgs) "
                 "match no real row", f"{n}/{k}/{h}/{d}/{o}; accounts with the masked suffix {a} (never touched)")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_C, "c")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP7 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        desc, perms = C.BASELINE_ROLES["super_admin"]
        rid = await rbac.ensure_role(conn, ORG_A, "super_admin", desc)
        for p in perms:
            if p in perm_ids:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        roles[(ORG_A, "super_admin")] = rid
        for rname, plist in (("rep7_viewer", VIEWER_PERMS), ("rep7_member", MEMBER_PERMS)):
            rid = await rbac.ensure_role(conn, ORG_A, rname, f"REP7 verify — {', '.join(plist)} only")
            for p in plist:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid,
                                   perm_ids[p])
            roles[(ORG_A, rname)] = rid
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid,
                                   roles[(org, rname)])
        for ent, org, nm in ((ENT_A, ORG_A, "Spouse One"), (ENT_A2, ORG_A, "Spouse Two"), (ENT_X, ORG_A, "X-ray"),
                             (ENT_G, ORG_A, "Guard"), (ENT_B, ORG_B, "Org B")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"REP7 Verify {nm}")
        for hh, nm in ((HH_A, HH_NAMES[0]), (HH_X, HH_NAMES[1])):
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                               hh, ORG_A, nm, U_ADMIN1)
        for i, key in enumerate(SEC_NAMES):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=FUND_TYPES.get(key, "equity"),
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:05d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, reg, tax, hh, ent, held, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10)",
                aid, org, n.masked, n.hashed(salts[org]), code, reg, tax, ent, hh, held)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, $3::jsonb)",
                           ORG_A, HA.STALE_SETTING, json.dumps(STALE_DAYS))
        assets = {}
        for key in SEC_KEYS:
            aid = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, $5)", aid, ORG_A, S[f"sec_{key}"], SEC_NAMES[key],
                               FUND_TYPES.get(key, "equity"))
            assets[key] = aid

        async def position(acct, key, qty, mv, cost, acquired, term="long"):
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, ORG_A, ACCOUNTS[acct][5], assets[key], TODAY - timedelta(days=1), D(qty),
                D(mv), acct)
            await conn.execute(
                "INSERT INTO portfolio.tax_lots (org_id, position_id, acquired_date, quantity, adjusted_cost, "
                "original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $5, $6, 'open')", ORG_A, pid, acquired,
                D(qty), D(cost), term)
            return pid

        # the managed X-ray account: FUND 100,000 (cost 80,000, long), FUND3 20,000, a stock 20,000, FUND2 10,000
        await position(ACCT_XM, "FUND", 1000, 100000, 80000, date(2019, 1, 2))
        await position(ACCT_XM, "FUND3", 100, 20000, 20000, date(2019, 1, 2))
        await position(ACCT_XM, "STK", 100, 20000, 15000, date(2019, 1, 2))
        await position(ACCT_XM, "FUND2", 10, 10000, 9000, date(2019, 1, 2))
        # the managed household-A account: one holding + cash; the guard account: cash only
        await position(ACCT_M, "K", 10, 1000, 900, date(2019, 1, 2))
        for acct, cash in ((ACCT_M, 10000), (ACCT_G, 10000), (ACCT_XM, 1000)):
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", ORG_A, acct,
                TODAY - timedelta(days=1), D(cash))
        # the registry: FUND (terms, holdings, a model, a viability verdict, a fee schedule) and FUND3 (holdings only)
        for key in ("FUND", "FUND3"):
            rs = uuid.uuid4()
            S[f"entry_{key}"] = rs
            await conn.execute(
                "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
                "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3, $4, $5, 'zz_rep7_test', "
                "'active', $6, $7)", rs, ORG_A, S[f"sec_{key}"], S["instrument_class"], tk(key), f"Holdings of {tk(key)}",
                U_ADMIN1)
        tv = await conn.fetchval(
            "INSERT INTO portfolio.replica_terms_versions (org_id, replica_security_id, version_no, status, term_values, "
            "maker_ids, created_by, checker_id, approved_at) VALUES ($1, $2, 1, 'approved', $3::jsonb, ARRAY[$4::uuid], "
            "$4, $5, now()) RETURNING id", ORG_A, S["entry_FUND"],
            json.dumps({"expense_ratio_net_pct": {"value": "0.09"}}), U_ADMIN1, U_ADMIN2)
        S["terms_FUND"] = tv
        S["snap_as_of"] = TODAY - timedelta(days=20)
        S["snap_FUND"] = await snapshot(conn, "FUND", [("C1", "0.5", "Information Technology"),
                                                       ("C2", "0.3", "Health Care"), ("C3", "0.2", None)])
        S["snap_FUND3"] = await snapshot(conn, "FUND3", [("C1", "0.6", "Information Technology"), ("C4", "0.4", "Energy")])
        await conn.execute(
            "INSERT INTO public.fee_schedules (id, org_id, code, version, name, product_type, rate_type, tier_method, "
            "billing_frequency, billing_timing, valuation_method, status, approved_by, approved_at, created_by) "
            "VALUES ($1, $2, 'REP7-VERIFY', 1, 'REP7 verify strategy fee', 'ASSET_MANAGEMENT', 'BPS', 'GRADUATED', "
            "'QUARTERLY', 'ARREARS', 'PERIOD_END', 'APPROVED', $3, now(), $4)", FS1, ORG_A, U_ADMIN2, U_ADMIN1)
        await conn.execute("INSERT INTO public.fee_schedule_tiers (org_id, fee_schedule_id, tier_seq, lower_bound, "
                           "upper_bound, rate_bps) VALUES ($1, $2, 1, 0, NULL, 5)", ORG_A, FS1)
        # the Chancery statement document, with the text Chancery extracted
        await conn.execute("INSERT INTO public.documents (id, org_id, original_filename, source, mime_type, status, "
                           "created_by) VALUES ($1, $2, $3, 'upload', 'application/pdf', 'confirmed', $4)", DOC_A,
                           ORG_A, DOC_NAME, U_ADMIN1)
        await conn.execute("INSERT INTO public.document_extractions (document_id, org_id, extraction_method, "
                           "extracted_text, page_count) VALUES ($1, $2, 'native', $3, 1)", DOC_A, ORG_A,
                           "REP7 VERIFY statement — positions as of the statement date")
    S["mK"], S["mKv"] = await make_model("REP7 VERIFY K", [pos(1, "K", "0.50"), pos(2, "cash", "0.50")])
    S["mF"], S["mFv"] = await make_model(f"Holdings of {tk('FUND')}", [pos(1, "C1", "0.5"), pos(2, "C2", "0.3"),
                                                                       pos(3, "C3", "0.2")], fund_entry=S["entry_FUND"])
    c, o = await api(U_ADMIN1, "PATCH", f"/api/v1/models/{S['mF']}", {"default_fee_schedule_id": str(FS1)})
    check(c == 200, "the fund model's default fee schedule is set through rep3's route", f"{c} {str(o)[:120]}")
    answers = [{"key": k, "status": "yes", "reason": "REP7 verify fixture", "inputs": {}, "missing": []}
               for k in ("rules_based", "rebuildable", "account_size", "custodian", "cost")]
    await scoped(conn, ORG_A, "INSERT INTO portfolio.replica_viability_reports (org_id, replica_security_id, "
                              "custodian_code, account_size, method, snapshot_id, terms_version_id, model_id, answers, "
                              "cost_lines, overall, computed_by) VALUES ($1, $2, 'altruist', 100000, 'full', $3, $4, $5, "
                              "$6::jsonb, '[]'::jsonb, 'yes', $7)", ORG_A, S["entry_FUND"], S["snap_FUND"],
                 S["terms_FUND"], S["mF"], json.dumps(answers), U_ADMIN1, fetch="execute")


async def snapshot(conn, fund: str, rows: list[tuple]) -> UUID:
    h = hashlib.sha256(f"{TAG}-{fund}".encode()).hexdigest()
    total = sum((D(w) for _, w, _ in rows), Decimal(0))
    sid = await conn.fetchval(
        """INSERT INTO portfolio.replica_holdings_snapshots (fund_global_security_id, as_of, source_key, file_sha256,
               row_count, total_weight, total_weight_ex_cash, status, validation, diff, diff_exceeds_threshold)
           VALUES ($1::uuid, $2, 'zz_rep7_test', $3, $4, $5, $5, 'live', '{}'::jsonb,
                   '{"first_snapshot": true}'::jsonb, false) RETURNING id""",
        S[f"sec_{fund}"], S["snap_as_of"], h, len(rows), total)
    for i, (key, w, sector) in enumerate(rows, start=1):
        await conn.execute(
            """INSERT INTO portfolio.replica_holdings_rows (snapshot_id, row_num, raw_name, raw_ticker, raw_cusip, raw,
                   asset_type, constituent_global_security_id, resolution_state, resolved_via, weight, shares)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'equity', $7::uuid, 'resolved', 'cusip', $8, 100)""",
            sid, i, SEC_NAMES[key], tk(key), None, json.dumps({"Sector": sector} if sector else {}), S[f"sec_{key}"],
            D(w))
    return sid


async def y_aggregator(conn):
    section("[Y] Aggregator: the interface present, every provider 'not connected', no network")
    c, o = await api(U_VIEWER, "GET", "/api/v1/held-away/providers")
    rows = o.get("rows") or []
    check(c == 200 and len(rows) >= 2 and all(r["status"] == "not_connected" for r in rows),
          "every registered provider is listed 'not connected'", f"{c} {[(r['key'], r['status']) for r in rows]}")
    abstract = sorted(getattr(AD.HeldAwayAdapter, "__abstractmethods__", ()))
    check(abstract == ["accounts", "as_of", "lots", "positions", "transactions"],
          "the provider-neutral interface: accounts, positions, lots, transactions, as-of", f"{abstract}")
    try:
        AD.adapter_for("plaid")
        check(False, "adapter_for refuses a provider with no client")
    except AD.ProviderNotConnected as exc:
        check("not connected" in str(exc), "adapter_for refuses: no provider client exists", str(exc))
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_HA}/sources",
                     {"kind": "aggregator", "provider": "plaid", "notes": "REP7 verify"})
    check(c == 201 and o.get("status") == "not_connected" and o.get("provider") == "plaid",
          "an aggregator source is recorded 'not connected'", f"{c} {o}")
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.held_away_sources (org_id, account_id, kind, provider, status) "
                                  "VALUES ($1, $2, 'aggregator', 'yodlee', 'active')", ORG_A, ACCT_HA, fetch="execute")
        check(False, "the database refuses an 'active' aggregator source")
    except asyncpg.CheckViolationError as exc:
        check(exc.constraint_name == "held_away_sources_connected_chk", "the database refuses an 'active' aggregator "
              "source (held_away_sources_connected_chk)", exc.constraint_name)
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_M}/sources", {"kind": "manual"})
    check(c == 409 and "not a held-away account" in str(o), "a managed account takes no held-away source (409)",
          f"{c}")
    check(NETWORK_CALLS == [], "no network call so far", f"{NETWORK_CALLS[:2]}")


async def pos_count(conn, acct, live=True) -> int:
    return int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE account_id = $1"
                                         + (" AND valid_to IS NULL" if live else "") + " AND system_to IS NULL", acct,
                            fetch="fetchval"))


async def y_imports(conn):
    section("[Y] Imports: rep3 mapping, rejects with reasons, draft changes nothing, maker-checker, as-of versions "
            "kept; statements never Claude, 'no extraction models configured' when none")
    # an earlier version (as of 60 days ago) → approved
    c, o = await manual_import(ACCT_XH, TODAY - timedelta(days=60),
                               [{"ticker": tk("FUND"), "quantity": "400", "market_value": "40000",
                                 "acquired_date": "2019-01-02", "cost": "36000"}])
    c2, o2 = await approve_import(o.get("id"))
    S["xh_old_import"] = o.get("id")
    check(c == 201 and c2 == 200 and o2.get("status") == "approved", "an earlier manual import (60 days old) is "
          "approved by a second person", f"{c} {c2} {str(o2)[:120]}")
    before = await pos_count(conn, ACCT_XH)
    csv = (f"Symbol,Shares,Market Value,Acquired,Cost Basis\n{tk('FUND')},500,50000,2019-01-02,45000\n"
           f"{tk('STK')},abc,10,,\n{PREFIX}NOPE,5,50,,\n,7,70,,\n").encode()
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_XH}/imports/file",
                     files={"file": ("zz_rep7_positions.csv", csv, "text/csv")},
                     data={"as_of": (TODAY - timedelta(days=1)).isoformat()})
    S["xh_file"] = o.get("id")
    codes = sorted(r["code"] for r in o.get("rejects") or [])
    check(c == 201 and o.get("status") == "draft" and o.get("rows_read") == 4 and o.get("rows_accepted") == 1
          and o.get("rows_rejected") == 3 and codes == ["no_identifier", "not_number", "unresolved"]
          and all(r.get("detail") for r in o.get("rejects") or []),
          "file: 4 rows read, 1 accepted, 3 rejected each with its reason (not a number, not in the security master, "
          "no identifier)", f"{c} {o.get('rows_read')} {o.get('rows_accepted')} {codes}")
    check((o.get("mapping") or {}) == {"Symbol": "ticker", "Shares": "quantity", "Market Value": "market_value",
                                       "Acquired": "acquired_date", "Cost Basis": "cost"},
          "the mapping used is recorded by header (rep3's suggestion machinery)", f"{o.get('mapping')}")
    check(await pos_count(conn, ACCT_XH) == before, "a draft changes nothing (live positions unchanged)")
    c_self, o_self = await approve_import(S["xh_file"], U_ADMIN1)
    c_view, o_view = await approve_import(S["xh_file"], U_VIEWER)
    st = await scoped(conn, ORG_A, "SELECT status FROM public.held_away_imports WHERE id = $1 AND system_to IS NULL",
                      S["xh_file"], fetch="fetchval")
    check(c_self == 403 and "maker-checker" in str(o_self) and c_view == 403 and "approve_held_away" in str(o_view)
          and st == "draft", "the maker is refused (maker-checker) and a viewer is refused naming approve_held_away; "
          "the import stays a draft", f"{c_self} {c_view} {st}")
    c, o = await approve_import(S["xh_file"])
    live = await scoped(conn, ORG_A, "SELECT p.quantity, p.market_value, p.as_of_date, h.import_id FROM "
                                     "portfolio.positions p JOIN public.held_away_records h ON h.record_id = p.id AND "
                                     "h.record_type = 'position' WHERE p.account_id = $1 AND p.valid_to IS NULL "
                                     "AND p.system_to IS NULL", ACCT_XH)
    old = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE account_id = $1 AND as_of_date = $2 "
                                    "AND valid_to IS NOT NULL AND system_to IS NULL", ACCT_XH,
                       TODAY - timedelta(days=60), fetch="fetchval")
    check(c == 200 and o.get("status") == "approved" and len(live) == 1 and live[0]["quantity"] == D(500)
          and live[0]["as_of_date"] == TODAY - timedelta(days=1) and str(live[0]["import_id"]) == S["xh_file"]
          and int(old) == 1, "a second person approves: the positions are replaced as of the date with their source "
          "and import; the earlier version is kept (closed on the valid axis)", f"{c} {[dict(x) for x in live]} {old}")
    c, o = await api(U_VIEWER, "GET", f"/api/v1/held-away/accounts/{ACCT_XH}/positions?as_of="
                                      f"{(TODAY - timedelta(days=30)).isoformat()}")
    pp = o.get("positions") or []
    check(c == 200 and len(pp) == 1 and pp[0]["quantity"] == "400" and pp[0]["as_of"] == ago(60),
          "the earlier as-of version is still readable", f"{c} {pp[:1]}")
    # an import older than the account's data: refused BEFORE the task commits — draft, task open
    c, o = await manual_import(ACCT_XH, TODAY - timedelta(days=90), [{"ticker": tk("FUND"), "quantity": "1"}])
    c2, o2 = await approve_import(o.get("id"))
    row = await scoped(conn, ORG_A, "SELECT status, approval_task_id FROM public.held_away_imports WHERE id = $1 AND "
                                    "system_to IS NULL", o.get("id"), fetch="fetchrow")
    task = await scoped(conn, ORG_A, "SELECT status FROM public.workflow_run_steps WHERE id = $1",
                        row["approval_task_id"], fetch="fetchval") if row else None
    check(c2 == 409 and "newer" in str(o2) and row is not None and row["status"] == "draft"
          and str(task or "") not in ("completed", "done"), "an import older than the account's data is refused "
          "(409) and stays a draft with its task still open", f"{c2} {row and row['status']} {task}")
    # a refused file is still logged; it can only be rejected
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_XH}/imports/file",
                     files={"file": ("zz_rep7.xlsm", b"PK\x03\x04macro", "application/octet-stream")},
                     data={"as_of": TODAY.isoformat()})
    c2, o2 = await approve_import(o.get("id"))
    check(c == 201 and [r["code"] for r in o.get("rejects") or []] == ["file_refused"] and c2 == 409,
          "a refused file (macro workbook) is logged as a draft with the reason and cannot be approved",
          f"{c} {o.get('rejects')} {c2}")
    await y_statements(conn)


async def y_statements(conn):
    real = await platform(conn, "SELECT count(*) FROM public.ai_ensemble_configs WHERE task_key = $1 AND is_active",
                          HA.STATEMENT_TASK_KEY, fetch="fetchval")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_XH}/statements",
                     {"document_id": str(DOC_A)})
    check(c == 201, "a Chancery document is linked to the held-away account", f"{c} {o}")
    body = {"document_id": str(DOC_A), "as_of": TODAY.isoformat()}
    if int(real) == 0:
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_XH}/imports/statement", body)
        _, panel = await api(U_ADMIN1, "GET", f"/api/v1/held-away/accounts/{ACCT_XH}")
        se = (panel.get("panel") or {}).get("statement_extraction") or {}
        check(c == 409 and "no extraction models configured" in str(o) and se.get("available") is False
              and "manual_import" in (panel.get("actions") or []) and "statement_import" not in (panel.get("actions") or []),
              "no ensemble → 'no extraction models configured'; the screen offers manual entry instead", f"{c} {o}")
    else:
        find("statement ensemble", f"{real} live 'statement_positions' config(s) exist — the no-models path is "
             "proven only by the mock below")
    orig_ens, orig_reader = HA.statement_ensemble, HA.STATEMENT_READER

    async def fake_reader(model, text, as_of):
        READER_CALLS.append(model)
        rows = [{"ticker": tk("FUND"), "quantity": "500", "market_value": "50000"}]
        if model.endswith("-a"):
            rows.append({"ticker": tk("STK"), "quantity": "7", "market_value": "1400"})
        return rows

    try:
        async def ens_claude(c_):
            return {"id": uuid.uuid4(), "model_1": "claude-sonnet-verify", "model_2": "zz-reader-b",
                    "system_one_model": None}

        HA.statement_ensemble, HA.STATEMENT_READER = ens_claude, fake_reader
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_XH}/imports/statement", body)
        check(c == 409 and "Claude" in str(o) and READER_CALLS == [], "an ensemble naming a Claude model is refused "
              "before any reader runs", f"{c} {READER_CALLS}")

        async def ens_ok(c_):
            return {"id": uuid.uuid4(), "model_1": "zz-reader-a", "model_2": "zz-reader-b", "system_one_model": None}

        HA.statement_ensemble = ens_ok
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_XH}/imports/statement", body)
        ex = o.get("extraction") or {}
        check(c == 201 and o.get("status") == "draft" and o.get("kind") == "statement" and o.get("rows_accepted") == 1
              and [r["code"] for r in o.get("rejects") or []] == ["models_disagree"]
              and ex.get("models") == ["zz-reader-a", "zz-reader-b"] and sorted(READER_CALLS) == ["zz-reader-a",
                                                                                                    "zz-reader-b"],
              "a MOCKED ensemble result → a DRAFT import: the row both readers report accepted, the row one reader "
              "saw rejected ('models_disagree'); never Claude; no network", f"{c} {o.get('rows_accepted')} {ex}")
    finally:
        HA.statement_ensemble, HA.STATEMENT_READER = orig_ens, orig_reader


async def y_held_away_data(conn):
    """Household A's held-away data: declared trades first, then the held-away import that matches them."""
    pool = await pool_()
    with _ctx(ORG_A):
        await LB.ingest(pool, org_id=ORG_A)
    for key, side, days, qty, extra in (("DQ", "buy", 7, "5", {}),
                                        ("DS", "sell", 6, "3", {"loss": "yes", "realized_amount": "-300",
                                                                "term": "short"})):
        c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/declared-trades", {
            "subject_type": "household", "subject_id": str(HH_A), "account_description": "REP7 verify outside",
            "account_tax_class": "taxable", "global_security_id": str(S[f"sec_{key}"]), "side": side,
            "trade_date": ago(days), "quantity": qty, "source_note": "REP7 verify", **extra})
        S[f"decl_{key}"] = (o.get("declared_trade") or {}).get("id")
        check(c == 201 and bool(S[f"decl_{key}"]), f"declared outside {side} of {key} recorded", f"{c} {str(o)[:120]}")
    with _ctx(ORG_A):
        await LB.ingest(pool, org_id=ORG_A)
    ctx0 = await in_org(lambda c_: WS.load_context(c_, ORG_A, S["acct_M"], TODAY))
    pre = [e for e in ctx0.events if e.key == str(S["sec_DQ"])]
    check(len(pre) == 1 and pre[0].source == "declared_outside", "before the held-away feed: the declared buy counts "
          "once (as declared)", f"{[e.source for e in pre]}")
    tx = [("S", "buy", 10, "10", "50", None, None), ("W30", "buy", 30, "1", "10", None, None),
          ("W31", "buy", 31, "1", "10", None, None), ("LS", "sell", 30, "1", "5", "-100", "short"),
          ("LS2", "sell", 31, "1", "5", "-100", "short"), ("Q", "sell", 5, "2", "10", "-200", "short"),
          ("G2", "sell", 3, "5", "100", "3000", "long"), ("DQ", "buy", 7, "5", "20", None, None),
          ("DS", "sell", 6, "3", "10", "-300", "short")]
    rows = [{"ticker": tk("G"), "quantity": "10", "market_value": "1000", "acquired_date": "2020-01-02", "cost": "800"}]
    for key, side, days, qty, price, real, term in tx:
        r = {"ticker": tk(key), "side": side, "trade_date": ago(days), "quantity": qty, "price": price}
        if real is not None:
            r.update(realized_gain_loss=real, term=term)
        rows.append(r)
    S["ha_tx"] = tx
    c, o = await manual_import(ACCT_HA, TODAY - timedelta(days=1), rows)
    c2, o2 = await approve_import(o.get("id"))
    S["ha_import"] = o.get("id")
    check(c == 201 and o.get("rows_accepted") == 10 and c2 == 200 and o2.get("status") == "approved",
          "household A's held-away import (a position + lot, nine trades) is approved by a second person",
          f"{c} {o.get('rows_accepted')} {o.get('rejects')} {c2} {str(o2)[:120]}")
    m = await scoped(conn, ORG_A, "SELECT record_id, matched_declared_id FROM public.held_away_records WHERE "
                                  "import_id = $1 AND matched_declared_id IS NOT NULL", S["ha_import"])
    check(sorted(str(r["matched_declared_id"]) for r in m) == sorted([S["decl_DQ"], S["decl_DS"]]),
          "the two declared trades are matched by held-away transactions (recorded on the provenance row)",
          f"{[dict(r) for r in m]}")


async def y_wash(conn):
    section("[Y] Wash-sale with held-away lots / transactions incl. the held-away IRA block; a declared trade matched "
            "once; both window edges")
    ctx = await in_org(lambda c_: WS.load_context(c_, ORG_A, S["acct_M"], TODAY))
    sk = lambda k: str(S[f"sec_{k}"])  # noqa: E731
    ev_s = [e for e in ctx.events if e.key == sk("S")]
    check(len(ev_s) == 1 and ev_s[0].source == "held_away" and ev_s[0].side == "buy"
          and ev_s[0].account_id == str(ACCT_HA), "the held-away buy of S counts in the household window "
          "(source 'held_away')", f"{[e.out() for e in ev_s]}")
    r = WS.sale_restriction(sk("S"), account_class="taxable", ctx=ctx)
    check(r is not None and any(x["source"] == "held_away" for x in r["matches"]),
          "a loss sale of S in the MANAGED account is held back by the held-away buy (rule b)", f"{r and r['reason']}")
    check(bool(WS.matching_buys(sk("W30"), ctx)) and not WS.matching_buys(sk("W31"), ctx),
          "buy edge: a held-away buy 30 days ago counts, 31 days ago is clear")
    e30 = WS.evaluate_buy(sk("LS"), account_class="taxable", ctx=ctx)
    e31 = WS.evaluate_buy(sk("LS2"), account_class="taxable", ctx=ctx)
    check(e30["outcome"] == "deferred" and e31["outcome"] == "clear",
          "loss-sale edge: a held-away loss sale 30 days ago defers a buy; 31 days ago is clear",
          f"{e30['outcome']} {e31['outcome']}")
    blk = WS.evaluate_buy(sk("Q"), account_class="tax_advantaged", ctx=ctx)
    tax = WS.evaluate_buy(sk("Q"), account_class="taxable", ctx=ctx)
    check(blk["outcome"] == "blocked" and blk["rule"] == "c" and tax["outcome"] == "deferred",
          "rule (c): a buy of Q into ANY household IRA is blocked by the held-away loss sale (a taxable account defers)",
          f"{blk['outcome']} {tax['outcome']}")
    hh = await in_org(lambda c_: RP.household_accounts(c_, ORG_A, S["acct_M"]))
    ira = [a for a in hh if a["id"] == ACCT_HIRA]
    check(len(ira) == 1 and ira[0]["is_held_away"] is True and WS.tax_class(ira[0]) == "tax_advantaged",
          "the IRA held elsewhere is one of the household's accounts (tax-advantaged) — its buys count with "
          "permanent-forfeit weight")
    ira_ctx = await in_org(lambda c_: WS.load_context(c_, ORG_A, ira[0] | {"primary_entity_id": ENT_A,
                                                                           "household_id": HH_A}, TODAY))
    b2 = WS.evaluate_buy(sk("Q"), account_class=WS.tax_class(ira[0]), ctx=ira_ctx)
    check(b2["outcome"] == "blocked", "the held-away IRA's own household context blocks the same buy (rule c)",
          b2["outcome"])
    dq = [e for e in ctx.events if e.key == sk("DQ")]
    check(len(dq) == 1 and dq[0].source == "held_away",
          "a declared outside buy matched by a held-away transaction counts ONCE (the held-away row; the declaration "
          "is skipped)", f"{[e.source for e in dq]}")
    live_decl = await scoped(conn, ORG_A, "SELECT count(*) FROM public.declared_outside_trades WHERE id = $1 AND "
                                          "valid_to IS NULL", S["decl_DQ"], fetch="fetchval")
    check(int(live_decl) == 1, "…while the declaration itself stays on record (both rows exist — the match is what "
          "prevents the double count)")


async def y_gain_and_bank(conn):
    section("[Y] Gain budget and loss bank count held-away realized results exactly, idempotent")
    expected = sum((D(real) for key, side, days, qty, price, real, term in S["ha_tx"]
                    if real is not None and (TODAY - timedelta(days=days)).year == YEAR), Decimal(0))
    n_lots = sum(1 for key, side, days, qty, price, real, term in S["ha_tx"]
                 if real is not None and (TODAY - timedelta(days=days)).year == YEAR)
    ytd = await in_org(lambda c_: RP.realized_ytd(c_, ORG_A, ACCT_M, TODAY))
    own = await in_org(lambda c_: RP.realized_ytd(c_, ORG_A, ACCT_M, TODAY, include_held_away=False))
    el = await in_org(lambda c_: HA.realized_elsewhere(c_, ORG_A, S["acct_M"], TODAY))
    check(own == 0 and ytd == expected and el["amount"] == expected and el["lots"] == n_lots
          and el["accounts"] == [str(ACCT_HA)],
          f"realized YTD for the managed account = its own 0 + {expected} realized in the held-away account",
          f"own {own} ytd {ytd} elsewhere {el}")
    S["expected_elsewhere"] = expected
    pool = await pool_()
    with _ctx(ORG_A):
        a = await LB.ingest(pool, org_id=ORG_A)
        b = await LB.ingest(pool, org_id=ORG_A)
    lots = await scoped(conn, ORG_A, "SELECT tl.id, tl.realized_gain_loss FROM portfolio.tax_lots tl JOIN "
                                     "public.held_away_records h ON h.record_id = tl.id AND h.record_type = 'tax_lot' "
                                     "WHERE h.import_id = $1 AND tl.status = 'closed'", S["ha_import"])
    ent = await scoped(conn, ORG_A, "SELECT source, source_key, amount FROM public.loss_bank_entries WHERE "
                                    "tax_lot_id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                       [r["id"] for r in lots])
    check(len(lots) == 5 and len(ent) == 5 and all(e["source"] == "held_away" for e in ent)
          and sorted(D(e["amount"]) for e in ent) == sorted(money(r["realized_gain_loss"]) for r in lots)
          and a["added"]["held_away"] >= 5 and b["added"]["held_away"] == 0,
          "the held-away closed lots enter the bank with source 'held_away' at their exact amounts; a re-run adds "
          "nothing (no lot_close entry for them)", f"{a['added']} {b['added']} {[dict(e) for e in ent]}")
    ds = await scoped(conn, ORG_A, "SELECT source, valid_to IS NULL AS live, superseded_reason FROM "
                                   "public.loss_bank_entries WHERE global_security_id = $1 AND system_to IS NULL",
                      S["sec_DS"])
    live = [r for r in ds if r["live"]]
    closed = [r for r in ds if r["source"] == "declared_outside"]
    check(len(live) == 1 and live[0]["source"] == "held_away" and len(closed) == 1 and closed[0]["live"] is False
          and "matched by held-away" in (closed[0]["superseded_reason"] or ""),
          "the declared sale's entry (added before the feed) is closed as matched — the loss counts once",
          f"{[dict(r) for r in ds]}")


async def y_guard(conn):
    section("[Y] Held-away accounts refused by every trade path (identical requests succeed for a managed account); "
            "no trade controls")
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_HA}/model/join",
                       {"model_id": str(S["mK"]), "settings": {}})
    cj, cj2, pid_g, _, _ = await join(ACCT_G, S["mK"])
    check(c1 == 409 and "held-away" in str(o1) and cj == 201 and cj2 == 200 and bool(pid_g),
          "transition (join): refused for the held-away account (409, plain words); the identical join of a managed "
          "account is accepted", f"{c1} {str(o1)[:120]} {cj} {cj2}")
    t1, o_t1 = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_HA}/model/transition?model_id={S['mK']}")
    t2, _ = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_G}/model/transition?model_id={S['mK']}")
    check(t1 == 409 and "held-away" in str(o_t1) and t2 == 200, "transition preview: refused / accepted",
          f"{t1} {t2}")
    pool = await pool_()
    outcomes = []
    for acct in (ACCT_HA, ACCT_G):
        with _ctx(ORG_A):
            try:
                r = await RP.generate(pool, org_id=ORG_A, account_id=acct, trigger="manual", maker_kind="user",
                                      maker_id=U_ADMIN1)
                outcomes.append(("ok", r.get("status")))
            except RP.ProposalConflict as exc:
                outcomes.append(("refused", str(exc)))
    check(outcomes[0][0] == "refused" and "held-away" in outcomes[0][1] and outcomes[1][0] == "ok",
          "rebalance (generate, trigger manual): refused for the held-away account; generated for the managed one",
          f"{outcomes}")
    outcomes = []
    for acct in (ACCT_HA, ACCT_G):
        with _ctx(ORG_A):
            try:
                r = await HV.scan_account(pool, org_id=ORG_A, account_id=acct)
                outcomes.append(("ok", r.get("action")))
            except RP.ProposalConflict as exc:
                outcomes.append(("refused", str(exc)))
    check(outcomes[0][0] == "refused" and "held-away" in outcomes[0][1] and outcomes[1][0] == "ok",
          "harvest: the scan refuses the held-away account; the managed account's scan runs", f"{outcomes}")

    def body(acct):
        return {"account_id": str(acct), "trades": [{"global_security_id": str(S["sec_K"]), "side": "buy",
                                                     "quantity": "1", "price_guidance": {"kind": "limit", "limit_price": "101.25"},
                                                     "lot_instructions": []}],
                "executor_type": "member", "executor_name": "Pat Example", "executor_contact": "pat@example.invalid",
                "execute_by": (TODAY + timedelta(days=7)).isoformat(), "if_not_filled": "cancel"}

    n0 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions WHERE account_id = $1",
                      ACCT_HA, fetch="fetchval")
    ci1, oi1 = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", body(ACCT_HA))
    ci2, oi2 = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", body(ACCT_G))
    n1 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions WHERE account_id = $1",
                      ACCT_HA, fetch="fetchval")
    check(ci1 == 409 and "held-away" in str(oi1) and ci2 == 201 and int(n0) == int(n1) == 0,
          "instruction creation: refused for the held-away account (nothing written); created for the managed one",
          f"{ci1} {ci2} {n0}->{n1} {str(oi2)[:100]}")
    for src, fn in (("rebalance_proposals._generate", RP._generate), ("rebalance_proposals.execute", RP.execute),
                    ("rebalance_proposals.transition_preview", RP.transition_preview),
                    ("account_models.propose_change", AM.propose_change), ("harvest.scan_account", HV.scan_account),
                    ("execution.create_instructions", X.create_instructions),
                    ("custody.trading.route_trade_list", TR.route_trade_list)):
        n = inspect.getsource(fn).count("refuse(")
        check(n == 1, f"the guard sits in ONE place in {src}", f"{n} call(s)")
    c, o = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_HA}/model")
    c2, o2 = await api(U_ADMIN1, "GET", f"/api/v1/held-away/accounts/{ACCT_HA}")
    check(c == 200 and not set(o.get("actions") or []) & {"join", "change_model", "change_settings", "generate"}
          and o.get("registry") == [] and c2 == 200 and o2.get("trade_actions") == []
          and (o2.get("panel") or {}).get("trade_controls") is False,
          "envelopes: the held-away account lists no join / model / settings / proposal action and no registry offer; "
          "the held-away panel lists no trade action", f"{o.get('actions')} {o2.get('trade_actions')}")
    find("execute path", "an APPROVED proposal for a held-away account can no longer exist (every generate refuses); "
         "rebalance_proposals.execute still refuses one (pre-rep7 rows) — proven by the one-place source check above")


async def y_staleness(conn):
    section("[Y] Staleness: threshold edge, approval refused until confirmed, confirmation expiry")
    check(HA.is_stale(TODAY - timedelta(days=STALE_DAYS), TODAY, STALE_DAYS) is False
          and HA.is_stale(TODAY - timedelta(days=STALE_DAYS + 1), TODAY, STALE_DAYS) is True
          and HA.is_stale(None, TODAY, STALE_DAYS) is True,
          "the rule: exactly stale_after_days old is not stale; one day over is; no data is stale")
    asof_ha = TODAY - timedelta(days=1)
    at = await in_org(lambda c_: HA.staleness(c_, ORG_A, S["acct_M"], asof_ha + timedelta(days=STALE_DAYS)))
    over = await in_org(lambda c_: HA.staleness(c_, ORG_A, S["acct_M"], asof_ha + timedelta(days=STALE_DAYS + 1)))
    ha_at = next((x for x in at if x["account_id"] == str(ACCT_HA)), {})
    ha_over = next((x for x in over if x["account_id"] == str(ACCT_HA)), {})
    check(ha_at.get("stale") is False and ha_over.get("stale") is True and ha_at.get("stale_after_days") == STALE_DAYS,
          "the held-away account's data, exactly at the threshold day: not stale; one day over: stale",
          f"{ha_at.get('age_days')} {ha_at.get('stale')} / {ha_over.get('age_days')} {ha_over.get('stale')}")
    # the IRA held away: data 31 days old (stale today)
    c, o = await manual_import(ACCT_HIRA, TODAY - timedelta(days=STALE_DAYS + 1),
                               [{"ticker": tk("IR"), "quantity": "10", "market_value": "1000"}])
    c2, _ = await approve_import(o.get("id"))
    check(c == 201 and c2 == 200, "the held-away IRA's data (31 days old) is approved")
    cj, cj2, pid, _, oj2 = await join(ACCT_M, S["mK"])
    S["pid_m"] = pid
    c, d = await api(U_ADMIN1, "GET", f"/api/v1/proposals/{pid}")
    p = d.get("proposal") or {}
    ws = {w["code"]: w for w in p.get("warnings") or []}
    ha = p.get("held_away") or {}
    stale_ira = next((x for x in ha.get("dependencies") or [] if x["account_id"] == str(ACCT_HIRA)), {})
    check(cj == 201 and cj2 == 200 and "stale_held_away" in ws and ws["stale_held_away"]["as_of"] == ago(STALE_DAYS + 1)
          and ha.get("blocking") is True and stale_ira.get("blocking") is True,
          "the proposal states the stale held-away date and is blocked for approval", f"{ws.keys()} {ha}")
    exp = S.get("expected_elsewhere")
    check("held_away_realized" in ws and exp is not None and f"${exp.quantize(CENT)}" in ws["held_away_realized"]["message"]
          and D(p.get("gain_realized_ytd")) == exp,
          f"the proposal's gain budget includes the gains realized elsewhere ({exp}) and says so",
          f"{ws.get('held_away_realized')} {p.get('gain_realized_ytd')}")
    conf = await scoped(conn, ORG_A, "SELECT * FROM public.stale_confirmations WHERE account_id = $1 AND proposal_id "
                                     "= $2 AND system_to IS NULL AND valid_to IS NULL", ACCT_HIRA, pid, fetch="fetchrow")
    check(conf is not None and conf["status"] == "pending" and conf["task_id"] is not None,
          "the 'confirm_held_away_data' task was opened for the stale dependency")
    maker = await scoped(conn, ORG_A, "SELECT maker_id FROM public.rebalance_proposals WHERE id = $1 AND "
                                      "system_to IS NULL", pid, fetch="fetchval")
    approver = next(u for u in (U_ADMIN3, U_ADMIN1, U_ADMIN2) if str(u) != str(maker))
    ca, oa = await api(approver, "POST", f"/api/v1/proposals/{pid}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.rebalance_proposals WHERE id = $1 AND system_to IS NULL",
                      pid, fetch="fetchval")
    check(ca == 409 and "confirm" in str(oa) and st == "pending_approval",
          "approval is refused (409) until the data is confirmed; the proposal stays awaiting approval",
          f"{ca} {str(oa)[:160]} {st}")
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/held-away/confirmations/{conf['id']}/confirm", {})
    cc, oc = await api(U_ADMIN1, "POST", f"/api/v1/held-away/confirmations/{conf['id']}/confirm", {"note": "seen"})
    check(cv == 403 and cc == 200 and oc.get("status") == "confirmed" and oc.get("confirmed_by") == str(U_ADMIN1)
          and oc.get("as_of_confirmed") == ago(STALE_DAYS + 1) and oc.get("expires_on") == (
              date.today() + timedelta(days=STALE_DAYS)).isoformat(),
          "a viewer cannot confirm; an adviser confirms — logged with who and which as-of, expiring after "
          "stale_after_days", f"{cv} {cc} {oc}")
    ca, oa = await api(approver, "POST", f"/api/v1/proposals/{pid}/approve", {})
    check(ca == 200, "with the confirmation, the proposal is approved", f"{ca} {str(oa)[:160]}")
    # newer data expires the confirmation
    c, o = await manual_import(ACCT_HIRA, TODAY - timedelta(days=2),
                               [{"ticker": tk("IR"), "quantity": "10", "market_value": "1100"}])
    c2, _ = await approve_import(o.get("id"))
    row = await scoped(conn, ORG_A, "SELECT status, closed_reason FROM public.stale_confirmations WHERE id = $1 AND "
                                    "system_to IS NULL", conf["id"], fetch="fetchrow")
    check(c2 == 200 and row["status"] == "expired" and "newer" in (row["closed_reason"] or ""),
          "newer held-away data expires the confirmation", f"{dict(row)}")
    # time expiry at the edge
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_HA}/confirmations", {})
    c2, o2 = await api(U_ADMIN1, "POST", f"/api/v1/held-away/confirmations/{o.get('id')}/confirm", {})
    exp_on = date.fromisoformat(o2["expires_on"])
    pool = await pool_()

    async def expire(day):
        with _ctx(ORG_A):
            async with pool.acquire() as c_:
                async with c_.transaction():
                    return await HA._expire_confirmations(c_, ORG_A, account_id=ACCT_HA, today=day)

    on_day = await expire(exp_on)
    after = await expire(exp_on + timedelta(days=1))
    check(c == 201 and c2 == 200 and on_day == [] and after == [o.get("id")],
          "a confirmation holds on its expiry day and expires the day after", f"{on_day} {after}")


async def xray(uid=U_ADMIN1):
    return await api(uid, "GET", f"/api/v1/xray/households/{HH_X}")


async def set_setting(conn, key, value):
    await platform(conn, "DELETE FROM public.org_settings WHERE org_id = $1 AND setting_key = $2", ORG_A, key,
                   fetch="execute")
    await platform(conn, "INSERT INTO public.org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, $3::jsonb)",
                   ORG_A, key, json.dumps(value), fetch="execute")


async def y_xray(conn):
    section("[Y] X-ray: look-through exact with as-of, unmatched funds, sectors, concentration, overlap, fee difference "
            "/ transition tax / break-even exact, not-estimated lines, join button managed-only")
    await set_setting(conn, XR.CONCENTRATION_SETTING, "0.15")
    c, o = await xray()
    x = o.get("xray") or {}
    sk = lambda k: str(S[f"sec_{k}"])  # noqa: E731
    by = {e["key"]: e for e in x.get("by_security") or []}
    want = {sk("C1"): ("87000.00", "0.4350000000"), sk("C2"): ("45000.00", "0.2250000000"),
            sk("C3"): ("30000.00", "0.1500000000"), sk("STK"): ("20000.00", "0.1000000000"),
            sk("FUND2"): ("10000.00", "0.0500000000"), sk("C4"): ("8000.00", "0.0400000000")}
    got = {k: (by.get(k, {}).get("exposure"), by.get(k, {}).get("weight")) for k in want}
    check(c == 200 and x.get("total_market_value") == "200000.00" and got == want and len(by) == len(want),
          "look-through exposure exact: weight × market value across managed and held-away accounts (C1 = 0.5 × "
          "150,000 + 0.6 × 20,000)", f"{c} {x.get('total_market_value')} {got}")
    cons = [r for r in x.get("constituents") or [] if r.get("look_through")]
    check(bool(cons) and all(r["weight_as_of"] == S["snap_as_of"].isoformat() for r in cons),
          "every look-through line carries the holdings file's as-of", f"{S['snap_as_of']}")
    f2 = [r for r in x.get("constituents") or [] if r.get("fund_security_id") == sk("FUND2")]
    h2 = [h for h in x.get("holdings") or [] if h.get("security_id") == sk("FUND2")]
    check(len(f2) == 1 and f2[0]["look_through"] is False and f2[0]["sector"] == "no look-through"
          and any("no look-through" in s for s in h2[0]["status_lines"]),
          "an unmatched fund stays ONE line 'no look-through'", f"{f2}")
    stk = [r for r in x.get("constituents") or [] if r.get("security_id") == sk("STK")]
    check(len(stk) == 1 and stk[0]["weight"] == "1" and stk[0]["sector"] == XR.UNCLASSIFIED,
          "a single stock passes through at weight 1 (sector unclassified — nothing inferred)")
    sec = {s["sector"]: s["exposure"] for s in x.get("by_sector") or []}
    check(sec == {"Information Technology": "87000.00", "Health Care": "45000.00", "unclassified": "50000.00",
                  "Energy": "8000.00", "no look-through": "10000.00"},
          "sector totals exact (the file's own Sector column; no sector → unclassified)", f"{sec}")
    flags = sorted(f["key"] for f in (x.get("concentration") or {}).get("flags") or [])
    await set_setting(conn, XR.CONCENTRATION_SETTING, "0.1500000001")
    _, o2 = await xray()
    flags2 = sorted(f["key"] for f in ((o2.get("xray") or {}).get("concentration") or {}).get("flags") or [])
    check(flags == sorted([sk("C1"), sk("C2"), sk("C3")]) and flags2 == sorted([sk("C1"), sk("C2")]),
          "concentration: a name exactly AT the threshold (C3 = 15%) is flagged; just above the threshold it is not",
          f"{flags} / {flags2}")
    ov = x.get("overlap") or []
    check(len(ov) == 1 and ov[0]["shared"] == 1 and ov[0]["overlap_weight"] == "0.5000000000"
          and {ov[0]["fund_a"], ov[0]["fund_b"]} == {sk("FUND"), sk("FUND3")},
          "overlap between funds: one shared constituent, Σ min(weights) = 0.5", f"{ov}")
    funds = {(f["account_id"], f["fund_security_id"]): f for f in x.get("funds") or []}
    fm = funds.get((str(ACCT_XM), sk("FUND")), {})
    fh = funds.get((str(ACCT_XH), sk("FUND")), {})
    f3 = funds.get((str(ACCT_XM), sk("FUND3")), {})
    check((fm.get("fee") or {}).get("annual_difference") == "40.00" and (fh.get("fee") or {}).get(
          "annual_difference") == "20.00" and (fm.get("embedded_gain") or {}).get("amount") == "20000.00"
          and (fh.get("embedded_gain") or {}).get("amount") == "5000.00",
          "fee difference exact (100,000 × 0.09% − 5 bp = 40.00; 50,000 → 20.00) and embedded gain exact from the lots",
          f"{fm.get('fee')} {fh.get('fee')} {fm.get('embedded_gain')}")
    check((fm.get("transition_tax") or {}).get("amount") is None
          and (fm.get("transition_tax") or {}).get("note") == XR.NO_PROFILE and XR.NO_PROFILE in fm.get("status_lines", []),
          "no tax profile → 'not estimated — no tax profile'")
    check(fm.get("harvest_benefit") == XR.NEEDS_PRICES and fm.get("tracking_error") == XR.NEEDS_PRICES
          and all(s in x.get("status_lines", []) for s in (XR.STATUS_LINES["tracking_error"],
                                                           XR.STATUS_LINES["harvest_benefit"],
                                                           XR.STATUS_LINES["after_tax_performance"])),
          "the needs-prices lines are present (tracking error, harvest value of switching, after-tax performance)")
    check(f3.get("model") is None and f3.get("replicable") is False and XR.NO_MODEL in f3.get("status_lines", [])
          and (f3.get("fee") or {}).get("note") == XR.NO_ER and f3.get("actions") == [],
          "a registry fund with no model: 'no model yet', not replicable, no button")
    check(fm.get("replicable") is True and fm.get("actions") == ["open_join_request"] and fh.get("actions") == []
          and (fh.get("join") or {}).get("reason") == XR.HELD_AWAY_NOTE and fh.get("look_through_as_of") ==
          S["snap_as_of"].isoformat(), "the join button is offered on the managed line only; the held-away line reads "
          "'held away — moving the assets comes first'", f"{fm.get('actions')} {fh.get('join')}")
    # the household's tax profile → transition tax and break-even exact
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", {
        "subject_type": "household", "subject_id": str(HH_X), "tax_year": YEAR, "filing_status": "married_filing_jointly",
        "federal_ordinary_rate": "0.37", "federal_lt_rate": "0.2", "niit_applies": True, "as_of": TODAY.isoformat(),
        "source_note": "REP7 verify"})
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/profiles/{(o.get('profile') or {}).get('id')}/approve", {})
    _, o3 = await xray()
    funds = {(f["account_id"], f["fund_security_id"]): f for f in (o3.get("xray") or {}).get("funds") or []}
    fm, fh = funds.get((str(ACCT_XM), sk("FUND")), {}), funds.get((str(ACCT_XH), sk("FUND")), {})
    check(c == 201 and c2 == 200 and (fm.get("transition_tax") or {}).get("amount") == "4760.00"
          and (fh.get("transition_tax") or {}).get("amount") == "1190.00"
          and (fm.get("break_even") or {}).get("years") == "119.00"
          and (fh.get("break_even") or {}).get("years") == "59.50" and fm.get("label_estimate") == "estimate",
          "transition tax exact (20,000 × (0.2 + 0.038) = 4,760.00) and break-even = tax ÷ fee difference "
          "(119.00 years; 59.50) — every figure labelled estimate", f"{fm.get('transition_tax')} {fm.get('break_even')}")
    # the one click
    cv, ov_ = await api(U_VIEWER, "POST", f"/api/v1/xray/households/{HH_X}/join",
                        {"account_id": str(ACCT_XM), "fund_security_id": sk("FUND")})
    ch, oh = await api(U_ADMIN1, "POST", f"/api/v1/xray/households/{HH_X}/join",
                       {"account_id": str(ACCT_XH), "fund_security_id": sk("FUND")})
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/xray/households/{HH_X}/join",
                       {"account_id": str(ACCT_XM), "fund_security_id": sk("FUND")})
    link = await scoped(conn, ORG_A, "SELECT status, model_id, maker_id, change_kind FROM public.account_model_links "
                                     "WHERE id = $1 AND system_to IS NULL", om.get("link_id"), fetch="fetchrow")
    check(cv == 403 and "manage_proposals" in str(ov_) and ch == 409 and "held away" in str(oh) and cm == 201
          and link is not None and link["status"] == "pending" and link["change_kind"] == "join"
          and link["model_id"] == S["mF"] and link["maker_id"] == U_ADMIN1,
          "'Open join request' opens rep5's join (pending, the clicker is the maker) for the managed account; refused "
          "for the held-away account (409) and for a caller without manage_proposals (403)",
          f"{cv} {ch} {cm} {dict(link) if link else None}")
    cs, os_ = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_XM}/model/changes/{om.get('link_id')}/approve", {})
    check(cs == 403, "rep5's maker-checker follows: the clicker cannot approve the join", f"{cs}")


async def y_runs_and_scheduler(conn):
    section("[Y] X-ray runs recorded; the daily sweep and the staleness check skip is_test orgs; nothing trades")
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/xray/households/{HH_X}/runs", {})
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/xray/households/{HH_X}/runs", {})
    rid = o.get("run_id")
    n = await scoped(conn, ORG_A, "SELECT line_kind, count(*) AS n FROM public.xray_lines WHERE run_id = $1 GROUP BY 1",
                     rid)
    kinds = {r["line_kind"]: int(r["n"]) for r in n}
    check(cv == 403 and "manage_replication" in str(ov) and c == 201
          and kinds == {"holding": 5, "constituent": 10, "fund": 3},
          "an on-demand run is recorded (5 holdings, 10 look-through lines, 3 fund analyses); a viewer cannot record",
          f"{cv} {c} {kinds}")
    try:
        await scoped(conn, ORG_A, "UPDATE public.xray_runs SET trigger = 'scheduler' WHERE id = $1", rid,
                     fetch="execute")
        check(False, "an X-ray run is immutable")
    except asyncpg.PostgresError as exc:
        check("written once" in str(exc), "an X-ray run is immutable (guard trigger)", str(exc)[:80])
    org_test = [await platform(conn, "SELECT is_test FROM organizations WHERE id = $1", o_, fetch="fetchval")
                for o_ in FIXTURE_ORGS]
    pool = await pool_()
    with _ctx(ORG_A):
        is_test = await in_org(lambda c_: ORGS.is_test_org(c_, ORG_A))
        tick = await RP.org_tick(pool, org_id=ORG_A)
        tick2 = await RP.org_tick(pool, org_id=ORG_A, include_test_orgs=True)
    check(org_test == [True, True, True] and is_test is True and "skipped" in tick and tick2.get("xray") is None,
          "every fixture org is is_test; the live scheduler's org_tick skips it; a verify tick of its own org gets no "
          "X-ray sweep unless it asks", f"{org_test} {tick.get('skipped')} {tick2.get('xray')}")
    src = inspect.getsource(RP.org_tick)
    check(src.find("HV.scan_account") < src.find("HA.staleness_tick") < src.find("XR.sweep"),
          "the staleness check and the X-ray sweep join rep5's org_tick after the harvest / drift steps")
    p0 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals", fetch="fetchval")
    i0 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions", fetch="fetchval")
    with _ctx(ORG_A):
        s1 = await XR.sweep(pool, org_id=ORG_A)
        s2 = await XR.sweep(pool, org_id=ORG_A)
        st = await HA.staleness_tick(pool, org_id=ORG_A)
    p1 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals", fetch="fetchval")
    i1 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions", fetch="fetchval")
    check(len(s1["runs"]) == 2 and not s1["errors"] and s2["runs"] == [] and s2["skipped"] == 2 and p0 == p1
          and i0 == i1, "the sweep records one scheduler run per household and day (a second sweep records none); "
          "neither the sweep nor the staleness check opens a proposal or an instruction",
          f"{s1} {s2} {st} {p0}->{p1} {i0}->{i1}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); household access; gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    cb, _ = await api(U_ADMIN_B, "POST", f"/api/v1/held-away/accounts/{ACCT_B}/sources", {"kind": "manual"})
    res = {}
    for t in ("held_away_sources", "held_away_imports", "held_away_records", "xray_runs", "xray_lines",
              "stale_confirmations"):
        res[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM public.{t} WHERE org_id = $1", ORG_A,
                                   fetch="fetchval")),
                  int(await scoped(conn, ORG_B, f"SELECT count(*) FROM public.{t} WHERE org_id = $1", ORG_A,
                                   fetch="fetchval")))
    b_in_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.held_away_sources WHERE org_id = $1", ORG_B,
                              fetch="fetchval"))
    b_in_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.held_away_sources WHERE org_id = $1", ORG_B,
                              fetch="fetchval"))
    check(cb == 201 and all(a > 0 and b == 0 for a, b in res.values()) and b_in_b > 0 and b_in_a == 0,
          "RLS: each org sees its own held-away and X-ray rows (non-empty) and none of the other's", f"{res} {b_in_b}")
    codes = [(await api(U_ADMIN_B, "GET", f"/api/v1/held-away/accounts/{ACCT_HA}"))[0],
             (await api(U_ADMIN_B, "GET", f"/api/v1/xray/households/{HH_X}"))[0],
             (await api(U_ADMIN_B, "POST", f"/api/v1/held-away/imports/{S['xh_file']}/approve", {}))[0],
             (await api(U_ADMIN1, "GET", f"/api/v1/held-away/accounts/{ACCT_B}"))[0]]
    check(codes == [404] * 4, "another org's account, household and import are a 404 both directions", f"{codes}")
    cm, om = await api(U_MEMBER, "GET", f"/api/v1/xray/households/{HH_X}")
    cl, ol = await api(U_MEMBER, "GET", "/api/v1/xray/households")
    ca, _ = await api(U_ADMIN1, "GET", f"/api/v1/xray/households/{HH_X}")
    check(cm == 404 and cl == 200 and ol.get("rows") == [] and ca == 200,
          "household access: a view_replication holder with no access to the household gets a 404 and an empty list; "
          "an org admin reads it", f"{cm} {cl} {ca}")
    cn, on = await api(U_MEMBER, "GET", f"/api/v1/held-away/accounts/{ACCT_HA}")
    check(cn == 403 and "view_held_away" in str(on), "held-away data is never visible without view_held_away")
    body = {"as_of": TODAY.isoformat(), "rows": [{"ticker": tk("G"), "quantity": "1"}]}
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/held-away/accounts/{ACCT_HA}/imports/manual", body)
    ca, oa = await api(U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{ACCT_HA}/imports/manual", body)
    check(cv == 403 and "manage_held_away" in str(ov) and ca == 201, "gates both ways on the identical import: the "
          "viewer refused naming manage_held_away; an org admin accepted", f"{cv} {ca}")
    _, ev = await api(U_VIEWER, "GET", f"/api/v1/held-away/accounts/{ACCT_HA}")
    _, em = await api(U_ADMIN1, "GET", f"/api/v1/held-away/accounts/{ACCT_HA}")
    _, ec = await api(U_ADMIN2, "GET", f"/api/v1/held-away/accounts/{ACCT_HA}")
    rv = next((i for i in ev.get("imports") or [] if i["id"] == oa.get("id")), {})
    rm = next((i for i in em.get("imports") or [] if i["id"] == oa.get("id")), {})
    rc = next((i for i in ec.get("imports") or [] if i["id"] == oa.get("id")), {})
    pv = ev.get("permissions") or {}
    check(pv.get("can_write") is False and pv.get("can_approve") is False and ev.get("actions") == []
          and (ev.get("vocabularies") or {}).get("editable") == [] and rv.get("actions") == []
          and rm.get("actions") == [] and rc.get("actions") == ["approve", "reject"]
          and bool((ev.get("vocabularies") or {}).get("reject_reasons"))
          and bool((ev.get("vocabularies") or {}).get("source_kinds")),
          "envelopes: view-only → no actions, empty editable; the maker gets no decision on her own import; a checker "
          "gets approve / reject; labels in vocabularies", f"{rv.get('actions')} {rm.get('actions')} {rc.get('actions')}")


async def y_new_org(conn):
    section("[Y] New org's first org_admin gets the three held-away permissions")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        before = await conn.fetchval("SELECT count(*) FROM roles WHERE org_id = $1 AND name = 'org_admin'", ORG_C)
        await rbac.grant_org_admin(conn, U_NEWADMIN, ORG_C)
        perms = {r["name"] for r in await conn.fetch(
            "SELECT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id JOIN role_permissions rp ON "
            "rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id WHERE ur.user_id = $1", U_NEWADMIN)}
    check(int(before) == 0 and set(HA_PERMS) <= perms, "a new org's first org_admin holds view / manage / "
          "approve_held_away (rbac.ensure_org_admin_role)", f"{sorted(perms & set(HA_PERMS))}")
    missing = await platform(conn, """SELECT count(*) FROM roles r CROSS JOIN permissions p WHERE r.name = 'org_admin'
                                      AND p.name = ANY($1::text[]) AND NOT (r.org_id = ANY($2::uuid[]))
                                      AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                                                      AND rp.permission_id = p.id)""",
                             list(HA_PERMS), [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
    check(int(missing) == 0, "every real org_admin role holds the three (fixture-org roles ignored)", f"{missing}")


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and (gb[k].get("after"), gb[k].get("expect")) !=
               (gn[k].get("after"), gn[k].get("expect"))]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    # rep7b fix 1 — scoped to rep7's own routes: rep7b (built on this branch after rep7) added these 11; any other
    # new route still fails the count
    rep7b_added = {(("POST",), "/api/v1/xray/households/{household_id}/one-off"),
                   (("POST",), "/api/v1/accounts/{account_id}/proposals/one-off"),
                   (("POST",), "/api/v1/execution/instructions/{instruction_id}/settlement"),
                   (("POST",), "/api/v1/execution/instructions/{instruction_id}/reconcile-held-away")}
    new = [k for k in gn if k not in gb and k not in rep7b_added and not k[1].startswith("/api/v1/authority/")]
    mine = [k for k in new if k[1].startswith(("/api/v1/held-away/", "/api/v1/xray/"))]
    check(len(new) == len(mine) == 19, "19 new routes, all under /held-away/… and /xray/… (rep7b's /authority/… and "
          "its 4 other routes excluded)", f"{len(new)} {len(mine)} {[k for k in new if k not in mine][:4]}")

    def want(k):
        if k[1].startswith("/api/v1/xray/"):
            if "GET" in k[0]:
                return "view_replication"
            return "manage_proposals" if k[1].endswith("/join") else "manage_replication"
        if "GET" in k[0]:
            return "view_held_away"
        return "approve_held_away" if k[1].endswith(("/approve", "/reject")) else "manage_held_away"

    bad = [(k, gn[k].get("after")) for k in mine if want(k) not in str(gn[k].get("after"))]
    check(not bad, "each names its permission (held-away view / manage / approve; X-ray view_replication, runs "
          "manage_replication, join manage_proposals)", f"{bad[:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    rep7 = re.findall(r"^ok \d+ - (?:held-away panel|X-ray:|proposal view: stale|lib: helpers fail closed)", out, re.M)
    check(len(rep7) >= 7 and "not ok" not in out, "the rep7 render tests ran and passed (no import / approve / join "
          "controls without the flags; no trade control on a held-away account)", f"{len(rep7)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep6.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep6.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    result = re.findall(r"^RESULT: (.*)$", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    zero = (bool(total) and total[-1][1] == "0") or (bool(result) and "ALL ASSERTIONS PASSED" in result[-1])
    check(zero and not fails and code == 0, "verify_rep6.py: 0 FAIL ('TOTAL:' or 'RESULT:' summary)",
          f"{total[-1] if total else (result[-1] if result else 'no summary')}; {fails[:3]} "
          f"{err[-200:] if not (total or result) else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


DROPPED_CONNECTION = (asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError, ConnectionError)


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
    pre = real = None
    try:
        await run("pre-teardown", pre_teardown, conn)
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("instrument_class"):
            await run("seed", seed, conn)
        if S.get("mF"):
            S["acct_M"] = await account_row(ACCT_M)
            await run("aggregator", y_aggregator, conn)
            await run("imports", y_imports, conn)
            await run("held-away data", y_held_away_data, conn)
            await run("wash sale", y_wash, conn)
            await run("gain budget + loss bank", y_gain_and_bank, conn)
            await run("guard", y_guard, conn)
            await run("staleness", y_staleness, conn)
            await run("x-ray", y_xray, conn)
            await run("runs + scheduler", y_runs_and_scheduler, conn)
            await run("isolation + gates", y_isolation, conn)
            await run("new org", y_new_org, conn)
        else:
            check(False, "seed created the fixture models", "every fixture-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()

        async def after_teardown(c):
            return (await leftovers(c), await counts(c) if pre is not None else None,
                    await real_fingerprints(c) if real is not None else None)

        try:
            log = await teardown_retrying(conn)
            print(f"[INFO] teardown removed: {log}")
            try:
                left, post, after = await after_teardown(conn)
            except DROPPED_CONNECTION as exc:
                print(f"[INFO] connection dropped during the leftover check ({type(exc).__name__}); reconnecting once")
                try:
                    conn.terminate()
                except Exception:  # noqa: BLE001
                    pass
                conn = await connect(dsn)
                left, post, after = await after_teardown(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if post is not None:
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if after is not None:
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k][0]} → {after[k][0]}")
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
