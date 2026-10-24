"""verify_rep5 — Replication Studio, sprint 5 (REP5): accounts join models, the
transition analysis, rebalance proposals from every trigger, approval on human
tasks, and approved proposals turned into execution instructions through rep2.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep5.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the real httpx network transport is replaced by one that records
and refuses — any attempt is a failed check. Nothing reaches Altruist.

REAL: the app (middleware, Principal, every gate, every handler) through an
ASGI transport, the workflow engine, the fee module, RLS and the live database.
The script's own connection is DATABASE_URL = app_service (rolbypassrls =
false, asserted).

FIXTURES: three orgs (99000000-…-0000000e5001 / e5002 / e5003); users created
explicitly with the auth0_sub their token carries and an @test.local email
(is_test by trigger); masked account numbers only; fixture securities carry
the reserved prefix ZZR5 and the test custodian code zz_rep5_* (both checked
absent from real rows first).

TEARDOWN (start and end): the operator DSN (role postgres) with
execution.teardown / models.teardown / replica.teardown = 'fixture' in its
transaction — the only path past the immutability triggers — tables in FK
child-before-parent order read from pg_constraint, every DELETE keyed by this
run's fixture ids; then every counted table is re-read for its exact pre-test
count, and real accounts / positions / lots / transactions / models / fee rows /
users are compared by count + checksum.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
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
from services import custodian_capabilities as CC  # noqa: E402
from services import execution as X  # noqa: E402
from services import fee_schedules as FS  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import models as M  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_engine as E  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import replica_models as RM  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt, trade_custodian_key  # noqa: E402
from services.custody.trading import TradeList  # noqa: E402
from services.database import close_pool, get_pool, reset_rls_context, set_rls_context  # noqa: E402

BEFORE_REF = "1717482"   # rep5-work base — main after the rep4 merge + schema refresh
TAG = "rep5verify"
PREFIX = "ZZR5"
CAP_TEST = "zz_rep5_frac"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_NAMES = {k: f"REP5 VERIFY {k}" for k in ("A", "B", "C", "D", "X", "F", "U", "BX")}


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e5{n:03d}")


ORG_A, ORG_B, ORG_C = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_C]
(U_SUPER, U_ADMIN1, U_ADMIN2, U_ADMIN3, U_VIEWER, U_MEMBER, U_ADMIN_B, U_ADMIN_B2, U_NEWADMIN) = (
    _id(110 + i) for i in range(9))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP5 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP5 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP5 Admin Checker"),
    U_ADMIN3: (ORG_A, "org_admin", "admin3", "org_admin", "REP5 Admin Reviewer"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep5_viewer", "REP5 Viewer"),
    U_MEMBER: (ORG_A, "member", "member", "member", "REP5 Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP5 Org B Admin"),
    U_ADMIN_B2: (ORG_B, "org_admin", "adminb2", "org_admin", "REP5 Org B Checker"),
    U_NEWADMIN: (ORG_C, "member", "newadmin", None, "REP5 New Org Admin"),
}
PROPOSAL_PERMS = ("view_proposals", "manage_proposals", "approve_proposals")

ENT_A, ENT_B = _id(201), _id(202)
HH_A = _id(211)
(ACCT_MAIN, ACCT_IRA, ACCT_ADV, ACCT_HELD, ACCT_CLOSED, ACCT_SYS, ACCT_DRIFT, ACCT_CALM, ACCT_CASH, ACCT_FRAC,
 ACCT_B) = (_id(301 + i) for i in range(11))
ACCOUNTS = {   # id: (org, custodian_code, registration, tax_status, household, discretionary, held_away, closed, raw)
    ACCT_MAIN: (ORG_A, "ALTRUIST", "individual", "taxable", HH_A, True, False, None, "ZZ50000001"),
    ACCT_IRA: (ORG_A, "ALTRUIST", "ira", "tax_deferred", HH_A, True, False, None, "ZZ50000002"),
    ACCT_ADV: (ORG_A, "ALTRUIST", "individual", "taxable", None, False, False, None, "ZZ50000003"),
    ACCT_HELD: (ORG_A, "ALTRUIST", "individual", "taxable", None, True, True, None, "ZZ50000004"),
    ACCT_CLOSED: (ORG_A, "ALTRUIST", "individual", "taxable", None, True, False, "closed", "ZZ50000005"),
    ACCT_SYS: (ORG_A, "ALTRUIST", "individual", "taxable", None, True, False, None, "ZZ50000006"),
    ACCT_DRIFT: (ORG_A, "ALTRUIST", "individual", "taxable", None, True, False, None, "ZZ50000007"),
    ACCT_CALM: (ORG_A, "ALTRUIST", "individual", "taxable", None, True, False, None, "ZZ50000008"),
    ACCT_CASH: (ORG_A, "ALTRUIST", "individual", "taxable", None, True, False, None, "ZZ50000009"),
    ACCT_FRAC: (ORG_A, CAP_TEST.upper(), "individual", "taxable", None, True, False, None, "ZZ50000010"),
    ACCT_B: (ORG_B, "ALTRUIST", "individual", "taxable", None, True, False, None, "ZZ50000011"),
}
ENTRY_F = _id(501)
FS1 = _id(511)
TODAY = date.today()
VAL = TODAY - timedelta(days=1)
YEAR_START = date(TODAY.year, 1, 1)

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.domain_event_deliveries",
    "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps", "public.workflow_runs",
    "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.account_balances_daily", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.tax_lots", "portfolio.transactions", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "public.custodian_capabilities", "public.execution_instructions",
    "public.execution_instruction_lots", "public.execution_fills", "public.execution_instruction_events",
    "public.fee_schedules", "public.fee_schedule_tiers", "public.fee_assignments", "public.fee_exclusions",
    "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions", "portfolio.model_events",
    "portfolio.replica_securities", "public.account_model_links", "public.rebalance_proposals",
    "public.rebalance_proposal_trades", "public.account_transition_plans",
)
TOUCHED = ("public.account_model_links", "public.rebalance_proposals", "public.rebalance_proposal_trades",
           "public.account_transition_plans", "public.accounts", "portfolio.positions", "portfolio.tax_lots",
           "portfolio.transactions", "public.account_balances_daily", "public.execution_instructions",
           "public.execution_instruction_lots", "public.fee_assignments", "public.fee_exclusions",
           "public.fee_schedules", "portfolio.models", "portfolio.model_versions",
           "portfolio.model_version_positions", "portfolio.replica_securities", "public.custodian_capabilities",
           "public.households", "public.users")

S: dict = {}
NETWORK_CALLS: list[str] = []
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
                        ("models", "portfolio.models"), ("model_versions", "portfolio.model_versions"),
                        ("fee_schedules", "public.fee_schedules"), ("fee_assignments", "public.fee_assignments"),
                        ("fee_exclusions", "public.fee_exclusions"),
                        ("execution_instructions", "public.execution_instructions"),
                        ("account_model_links", "public.account_model_links"),
                        ("rebalance_proposals", "public.rebalance_proposals")):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
    out["custodian_capabilities"] = await fingerprint(
        conn, "SELECT * FROM public.custodian_capabilities WHERE custodian_code NOT LIKE 'zz_rep5%'")
    return out


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
    """Child-before-parent delete for a FIXTURE-ID parent (users / roles / orgs
    of this run only), FKs read from pg_constraint (recursively)."""
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
    """``tables`` sorted child-before-parent from pg_constraint (Kahn)."""
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


async def teardown(_conn=None) -> list[str]:
    """Operator connection, the teardown GUCs, FK child-before-parent, every
    DELETE keyed by this run's fixture ids."""
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
            plan = {
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
                "public.fee_schedule_tiers": (by_org, [orgs]),
                "public.fee_schedules": (by_org, [orgs]),
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
                "public.custodian_capabilities": ("custodian_code LIKE 'zz_rep5%'", []),
                "portfolio.replica_model_specs": (by_org, [orgs]),
                "portfolio.replica_registry_keys": (by_org, [orgs]),
                "portfolio.model_events": (by_org, [orgs]),
                "portfolio.model_version_positions": (by_org, [orgs]),
                "portfolio.model_sleeves": (by_org, [orgs]),
                "portfolio.model_versions": (by_org, [orgs]),
                "portfolio.models": (by_org, [orgs]),
                "portfolio.replica_securities": (by_org, [orgs]),
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
            await delete_tree(op, "public.users", "id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                              [users, f"%{TAG}%"], log=log)
            await delete_tree(op, "public.roles", "org_id = ANY($1::uuid[])", [orgs], log=log)
            await delete_tree(op, "public.organizations", "id = ANY($1::uuid[])", [orgs], log=log)
    finally:
        await op.close()
    return log


async def leftovers(conn) -> int:
    n = 0
    orgs = FIXTURE_ORGS
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [orgs]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])", [list(SEC_NAMES.values())]),
        ("SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE 'zz_rep5%'", []),
        *[(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", [orgs]) for t in (
            "public.account_model_links", "public.rebalance_proposals", "public.rebalance_proposal_trades",
            "public.account_transition_plans", "public.execution_instructions", "public.execution_instruction_lots",
            "public.execution_fills", "public.execution_instruction_events", "public.fee_schedules",
            "public.fee_assignments", "public.fee_exclusions", "public.workflow_definitions", "public.workflow_runs",
            "public.workflow_run_steps", "public.member_todos", "public.domain_events", "public.audit_log",
            "public.accounts", "public.households", "public.entities", "public.account_balances_daily",
            "portfolio.tax_lots", "portfolio.transactions", "portfolio.positions", "portfolio.assets",
            "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
            "portfolio.replica_securities", "public.org_settings")],
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api_raw(uid, method, path, body=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep5"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


async def api(uid, method, path, body=None, timeout=240.0):
    code, content, _ = await api_raw(uid, method, path, body, timeout)
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
        raise AssertionError(f"verify_rep5: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    """RLS contextvars for a direct service call (what the middleware sets)."""

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


async def prop_row(conn, org, pid):
    return await scoped(conn, org, "SELECT * FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS NULL",
                        str(pid), fetch="fetchrow")


async def trades_rows(conn, org, pid):
    return await scoped(conn, org, "SELECT * FROM public.rebalance_proposal_trades WHERE proposal_id = $1::uuid "
                                   "AND valid_to IS NULL AND system_to IS NULL ORDER BY line_no", str(pid))


async def link_live(conn, org, account, status="active"):
    return await scoped(conn, org, "SELECT * FROM public.account_model_links WHERE account_id = $1::uuid AND status = $2 "
                                   "AND valid_to IS NULL AND system_to IS NULL", str(account), status, fetch="fetchrow")


async def open_prop(conn, org, account):
    return await scoped(conn, org, "SELECT * FROM public.rebalance_proposals WHERE account_id = $1::uuid AND system_to IS "
                                   "NULL AND status IN ('draft', 'pending_approval', 'approved')", str(account),
                        fetch="fetchrow")


def jl(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


# ═══ Fixtures ═══════════════════════════════════════════════════════════════
def pos(row, key, w, alts=()):
    sid = S.get(f"sec_{key}") if key != "cash" else None
    return {"row_num": row, "global_security_id": str(sid) if sid else None, "raw_ticker": f"{PREFIX}{key}" if sid
            else None, "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": SEC_NAMES.get(key, "Cash"),
            "target_weight": D(w), "asset_class": None, "sleeve_label": None, "drift_min": None, "drift_max": None,
            "alternates": list(alts), "restrictions": None, "notes": None,
            "resolution_state": "resolved" if sid else "cash", "resolution_note": None}


async def make_model(name: str, positions: list[dict], *, fund_entry=None) -> tuple[UUID, UUID]:
    pool = await pool_()
    with _ctx(ORG_A):
        async with pool.acquire() as conn:
            if fund_entry:
                model, ref = await M.create_fund_model(conn, org_id=ORG_A, actor_id=U_ADMIN1, name=name,
                                                       replica_security_id=fund_entry,
                                                       disclosure="REP5 verify fixture disclosure")
            else:
                model, ref = await M.create_model(conn, org_id=ORG_A, actor_id=U_ADMIN1, name=name, source="upload")
            v = await M._create_version(conn, org_id=ORG_A, model=model, origin="manual", maker_ids=[U_ADMIN1],
                                        created_by=U_ADMIN1, positions=positions)
        await M.publish(pool, [ref])
        await M.submit(pool, org_id=ORG_A, actor_id=U_ADMIN1, model_id=model["id"], version_id=v["id"])
        await M.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, model_id=model["id"], version_id=v["id"],
                       outcome="approve")
    return UUID(str(model["id"])), UUID(str(v["id"]))


# Every literal seed() (and the later direct-SQL negative tests) writes into a column that a CHECK, a lookup
# table or an enum constrains — checked against the live catalog first. A seed INSERT that dies on a constraint
# skips every fixture-scoped section after it (rep5's first run: instrument_class 'etf', not a catalog key).
SEED_INSTRUMENT_CLASS = "index_etf"
SEED_CHECK_LITERALS = (   # (table, constraint, literals the script writes)
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.transactions", "transactions_authority_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long", "short")),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open", "closed")),
    ("portfolio.replica_securities", "replica_securities_status_chk", ("active",)),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk",
     ("fractional_shares", "custodian_tax_loss_harvesting")),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("assumed",)),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes",)),
    ("public.fee_schedules", "fee_schedules_product_type_check", ("ASSET_MANAGEMENT",)),
    ("public.fee_schedules", "fee_schedules_rate_type_check", ("BPS",)),
    ("public.fee_schedules", "fee_schedules_tier_method_check", ("GRADUATED",)),
    ("public.fee_schedules", "fee_schedules_billing_frequency_check", ("QUARTERLY",)),
    ("public.fee_schedules", "fee_schedules_billing_timing_check", ("ARREARS",)),
    ("public.fee_schedules", "fee_schedules_valuation_method_check", ("PERIOD_END",)),
    ("public.fee_schedules", "fee_schedules_status_check", ("APPROVED",)),
    ("portfolio.models", "models_source_chk", ("upload",)),
    ("portfolio.models", "models_status_chk", ("draft",)),
    ("public.account_model_links", "account_model_links_change_chk", ("join",)),
    ("public.account_model_links", "account_model_links_status_chk", ("active", "rejected")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("manual",)),
    ("public.rebalance_proposals", "rebalance_proposals_maker_kind_chk", ("user",)),
    ("public.execution_instructions", "execution_instructions_side_chk", ("buy",)),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("member",)),
    ("public.execution_instructions", "execution_instructions_if_not_filled_chk", ("ask",)),
    ("public.execution_instructions", "execution_instructions_source_chk", ("manual",)),
    ("public.execution_instructions", "execution_instructions_status_chk", ("approved",)),
    ("public.execution_instructions", "execution_instructions_price_chk", ("market",)),
)


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog (CHECKs, lookup tables, enums) before seeding")
    S.pop("instrument_class", None)
    row = await platform(conn, "SELECT key, enabled FROM portfolio.replica_instrument_classes WHERE key = $1 "
                               "AND valid_to IS NULL AND system_to IS NULL", SEED_INSTRUMENT_CLASS, fetch="fetchrow")
    keys = [r["key"] for r in await platform(conn, "SELECT key FROM portfolio.replica_instrument_classes WHERE "
                                                   "valid_to IS NULL AND system_to IS NULL ORDER BY sort_order")]
    if check(row is not None and row["enabled"] is True, f"instrument class {SEED_INSTRUMENT_CLASS!r} is a current, "
             "enabled portfolio.replica_instrument_classes key", f"live keys {keys}"):
        S["instrument_class"] = row["key"]    # seed() reads the key from the table, never a literal
    codes = {r["code"] for r in await platform(conn, "SELECT code FROM public.transaction_types WHERE code = "
                                                     "ANY($1::text[])", ["buy", "sell"])}
    check(codes == {"buy", "sell"}, "transaction_type_code 'buy' / 'sell' exist in public.transaction_types",
          f"{sorted(codes)}")
    labels = {r["l"] for r in await platform(conn, "SELECT enumlabel AS l FROM pg_enum WHERE enumtypid = "
                                                   "'public.entity_type'::regtype")}
    check("individual" in labels, "entity_type 'individual' is a label of the entity_type enum")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'::text", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    check(not bad, f"every CHECK-constrained seed literal is admitted by its constraint ({len(SEED_CHECK_LITERALS)} "
          "constraints)", "; ".join(bad))
    d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                             "'public.custodian_capabilities'::regclass AND conname = 'custodian_capabilities_code_chk'",
                       fetch="fetchval")
    check(d is not None and "^[a-z][a-z0-9_]*$" in d and re.fullmatch(r"[a-z][a-z0-9_]*", CAP_TEST) is not None,
          "the test custodian code satisfies custodian_capabilities_code_chk", f"{CAP_TEST!r} {d}")
    check(all(trade_custodian_key(a[1]) in (CAP_TEST, "altruist") for a in ACCOUNTS.values()),
          "every fixture account's custodian_code routes to a key the capability rows / adapters use",
          f"{sorted({trade_custodian_key(a[1]) for a in ACCOUNTS.values()})}")
    find("unconstrained seed text", "accounts.custodian_code / registration_type / tax_status, portfolio.assets."
         "asset_type and account_balances_daily.source_system / source_confidence carry no CHECK, FK or enum — "
         "nothing to validate; source_confidence now 'CONFIRMED' (the column default and the importer's vocabulary), "
         "was 'high'")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_C, "c")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"REP5 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for org, rname in ((ORG_A, "member"), (ORG_A, "super_admin")):
            desc, perms = C.BASELINE_ROLES[rname]
            rid = await rbac.ensure_role(conn, org, rname, desc)
            for p in perms:
                if p in perm_ids:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid
        rid = await rbac.ensure_role(conn, ORG_A, "rep5_viewer", "REP5 verify — view_proposals only")
        await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid,
                           perm_ids["view_proposals"])
        roles[(ORG_A, "rep5_viewer")] = rid
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
        for ent, org in ((ENT_A, ORG_A), (ENT_B, ORG_B)):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"REP5 Verify Client {str(org)[-1]}")
        await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, 'REP5 Verify "
                           "Household', $3)", HH_A, ORG_A, U_ADMIN1)
        for i, key in enumerate(SEC_NAMES):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type="equity", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}000{i:02d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        for key, value in (("fractional_shares", "yes"), ("custodian_tax_loss_harvesting", "yes")):
            await conn.execute(
                "INSERT INTO public.custodian_capabilities (custodian_code, capability_key, value, evidence_kind, "
                "evidence, as_of, version_no) VALUES ($1, $2, $3, 'assumed', 'REP5 verify fixture', $4, 1)",
                CAP_TEST, key, value, TODAY)
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, reg, tax, hh, disc, held, closed, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away, "
                "closed_on) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                aid, org, n.masked, n.hashed(salts[org]), code, reg, tax, ENT_A if org == ORG_A else ENT_B, hh, disc,
                held, (TODAY - timedelta(days=10)) if closed else None)
        assets = {}
        for key in ("A", "B", "X", "F", "U"):
            aid = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", aid, ORG_A, S[f"sec_{key}"], SEC_NAMES[key])
            assets[key] = aid
        bx = uuid.uuid4()
        await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                           "VALUES ($1, $2, $3, $4, 'equity')", bx, ORG_B, S["sec_BX"], SEC_NAMES["BX"])
        S["assets"] = assets
        S["pos"] = {}

        async def position(acct, key, qty, mv, org=ORG_A, asset=None, as_of=VAL):
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, org, ENT_A if org == ORG_A else ENT_B, asset or assets[key], as_of, D(qty),
                None if mv is None else D(mv), acct)
            S["pos"][(acct, key)] = pid
            return pid

        async def lot(pos_id, qty, cost, acquired, term, status="open", realized=None, closing=None):
            lid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                "original_cost, term, status, realized_gain_loss, closing_transaction_id) VALUES ($1, $2, $3, $4, $5, "
                "$6, $6, $7, $8, $9, $10)", lid, ORG_A, pos_id, acquired, D(qty), D(cost), term, status,
                None if realized is None else D(realized), closing)
            return lid

        async def txn(pos_id, code, trade_date, qty, price):
            tid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.transactions (id, org_id, position_id, transaction_type_code, trade_date, "
                "quantity, price, authority, source_system, is_corporate_action_adjustment) VALUES ($1, $2, $3, $4, $5, "
                "$6, $7, 'manual', 'manual', false)", tid, ORG_A, pos_id, code, trade_date, D(qty), D(price))
            return tid

        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        # ACCT_MAIN — the transition account
        pma = await position(ACCT_MAIN, "A", 100, 5000)
        pmx = await position(ACCT_MAIN, "X", 100, 10000)
        pmf = await position(ACCT_MAIN, "F", 10, 1000)
        await position(ACCT_MAIN, "U", 5, None)                  # cannot value
        S["LA1"] = await lot(pma, 60, 3600, date(2020, 1, 2), "long")
        S["LA2"] = await lot(pma, 40, 1200, date(2021, 3, 4), "long")
        S["LX1"] = await lot(pmx, 50, 2000, date(2019, 5, 6), "long")
        S["LX2_ACQ"] = TODAY - timedelta(days=360)
        S["LX2"] = await lot(pmx, 30, 1200, S["LX2_ACQ"], "short")
        S["LX3"] = await lot(pmx, 20, 2400, date(2018, 7, 8), "long")
        S["LF1"] = await lot(pmf, 10, 900, date(2020, 2, 3), "long")
        ytd = await txn(pma, "sell", YEAR_START, 5, 70)
        await lot(pma, 5, 290, date(2019, 1, 2), "long", status="closed", realized=300, closing=ytd)
        # ACCT_IRA — the household's IRA (wash-sale evidence)
        pia = await position(ACCT_IRA, "A", 10, 500)
        pix = await position(ACCT_IRA, "X", 10, 1000)
        pib = await position(ACCT_IRA, "B", 10, 1000)
        ia_sell = await txn(pia, "sell", TODAY - timedelta(days=5), 5, 49)
        await lot(pia, 5, 295, date(2022, 1, 3), "long", status="closed", realized=-50, closing=ia_sell)
        await txn(pix, "buy", TODAY - timedelta(days=10), 10, 100)
        ib_sell = await txn(pib, "sell", TODAY - timedelta(days=45), 5, 92)
        await lot(pib, 5, 540, date(2022, 1, 3), "long", status="closed", realized=-80, closing=ib_sell)
        # the others
        await lot(await position(ACCT_ADV, "A", 100, 5000), 100, 4000, date(2020, 1, 2), "long")
        await lot(await position(ACCT_HELD, "A", 100, 5000), 100, 4000, date(2020, 1, 2), "long")
        await lot(await position(ACCT_SYS, "A", 100, 5000), 100, 4000, date(2020, 1, 2), "long")
        await lot(await position(ACCT_SYS, "X", 50, 5000), 50, 2500, date(2020, 1, 2), "long")
        await lot(await position(ACCT_DRIFT, "X", 100, 10000), 100, 9000, date(2020, 1, 2), "long")
        await position(ACCT_CALM, "A", 90, 4500)
        await position(ACCT_CASH, "A", 90, 4500)
        await position(ACCT_FRAC, "A", 10, 510)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_B))
        await position(ACCT_B, "BX", 10, 1000, org=ORG_B, asset=bx)
        for acct, cash, org in ((ACCT_MAIN, 1000, ORG_A), (ACCT_SYS, 1000, ORG_A), (ACCT_CALM, 500, ORG_A),
                                (ACCT_CASH, 1500, ORG_A), (ACCT_FRAC, 10000, ORG_A), (ACCT_B, 0, ORG_B)):
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", org, acct, VAL, D(cash))
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3, $4, $5, 'zz_rep5_test', "
            "'active', $6, $7)", ENTRY_F, ORG_A, S["sec_F"], S["instrument_class"], f"{PREFIX}F",
            f"Holdings of {PREFIX}F", U_ADMIN1)
        await conn.execute(
            "INSERT INTO public.fee_schedules (id, org_id, code, version, name, product_type, rate_type, tier_method, "
            "billing_frequency, billing_timing, valuation_method, status, approved_by, approved_at, created_by) "
            "VALUES ($1, $2, 'REP5-VERIFY', 1, 'REP5 verify strategy fee', 'ASSET_MANAGEMENT', 'BPS', 'GRADUATED', "
            "'QUARTERLY', 'ARREARS', 'PERIOD_END', 'APPROVED', $3, now(), $4)", FS1, ORG_A, U_ADMIN2, U_ADMIN1)
        await conn.execute("INSERT INTO public.fee_schedule_tiers (org_id, fee_schedule_id, tier_seq, lower_bound, "
                           "upper_bound, rate_bps) VALUES ($1, $2, 1, 0, NULL, 75)", ORG_A, FS1)
    # models — through rep3's own services (approved by a second person)
    S["m1"], S["m1v1"] = await make_model(f"Holdings of {PREFIX}F", [
        pos(1, "A", "0.40", alts=(f"{PREFIX}D",)), pos(2, "B", "0.30"), pos(3, "C", "0.20"), pos(4, "cash", "0.10")],
        fund_entry=ENTRY_F)
    S["m2"], S["m2v1"] = await make_model("REP5 VERIFY CORE", [pos(1, "A", "0.90"), pos(2, "cash", "0.10")])
    S["m3"], S["m3v1"] = await make_model("REP5 VERIFY SYS", [pos(1, "A", "0.50"), pos(2, "X", "0.40"),
                                                             pos(3, "cash", "0.10")])
    c, o = await api(U_ADMIN1, "PATCH", f"/api/v1/models/{S['m1']}", {"default_fee_schedule_id": str(FS1)})
    check(c == 200, "fixture: the fund model's default fee schedule set through rep3", f"{c} {str(o)[:150]}")


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e — signatures and the chosen mechanisms")

    sig = {n: str(inspect.signature(f)) for n, f in (
        ("execution.create_instructions", X.create_instructions), ("execution.submit", X.submit),
        ("execution.decide", X.decide), ("execution.account_lots", X.account_lots),
        ("execution._validate_lots", X._validate_lots), ("trading.route_trade_list", TR.route_trade_list),
        ("human_tasks.open_task", HT.open_task), ("human_tasks.complete_task", HT.complete_task),
        ("human_tasks.cancel_task", HT.cancel_task), ("human_tasks.list_tasks", HT.list_tasks),
        ("models.live_approved", M.live_approved), ("models.positions_of", M.positions_of),
        ("models.look_through", M.look_through), ("models.after_approval", M.after_approval),
        ("replica_models.spec_for_version", RM.spec_for_version),
        ("fee_schedules.create_assignment", FS.create_assignment), ("fee_schedules.end_assignment",
                                                                     FS.end_assignment),
        ("fee_schedules.create_exclusion", FS.create_exclusion), ("fee_schedules.end_exclusion", FS.end_exclusion),
        ("custodian_capabilities.capability", CC.capability))}
    for k, v in sig.items():
        find(f"1a {k}", v)
    check("upstream" in sig["execution.create_instructions"] and "upstream" in sig["trading.route_trade_list"]
          and "maker_id" in sig["human_tasks.open_task"] and "actor_kind" in sig["human_tasks.open_task"],
          "1a/1d: create_instructions and route_trade_list carry the additive upstream= parameter")
    find("1b holdings", "positions for one account: portfolio.positions WHERE account_id (latest live row per asset, "
         "as_of_date desc — the valuation as-of is the latest of those); lots: execution.account_lots (lot → "
         "position.account_id, security via asset.global_security_id); price = market_value / quantity on the "
         "position's as_of_date (source 'account_position'); household = same household_id, the primary entity, or a "
         "shared account_owners entity; realized YTD = closed tax_lots.realized_gain_loss whose closing transaction "
         "trade_date is this year (transactions carry no gain)")
    real = await platform(conn, "SELECT (SELECT count(*) FROM public.accounts WHERE NOT (org_id = ANY($1::uuid[]))) AS a,"
                                " (SELECT count(*) FROM portfolio.positions WHERE account_id IS NOT NULL AND NOT "
                                "(org_id = ANY($1::uuid[]))) AS p", [str(o) for o in FIXTURE_ORGS], fetch="fetchrow")
    find("1b real rows (counts only)", f"accounts {real['a']}, account positions {real['p']}")
    src = inspect.getsource(M.after_approval)
    check("on_model_version_approved" in src and "rebalance_proposals" in src,
          "1c: rep3's after_approval calls rebalance_proposals.on_model_version_approved directly (domain_events "
          "fans out only to BPMN workflow triggers — no in-process subscriber registry exists)")
    find("1c mechanism", "a DIRECT call from models.after_approval (deterministic, same request, idempotent per "
         "account + model version) plus a scheduler-tick sweep of the last 7 days of 'model.version_approved' "
         "domain_events (recovers anything the direct call missed)")
    fn = await platform(conn, "SELECT pg_get_functiondef('public.execution_instructions_insert_guard'::regproc)",
                        fetch="fetchval")
    check("p.maker_id = NEW.maker_id" in (fn or "") and "p.checker_id = NEW.approved_by" in (fn or ""),
          "1d: the DB insert guard admits 'approved' only from an approved proposal whose maker/checker it carries")
    find("1d exact change", "create_instructions(upstream={proposal_id, maker_id, checker_id}) re-reads the proposal "
         "(approved, a PERSON's, maker != checker, task completed 'approve' by the checker) and inserts each "
         "instruction 'approved' with maker_id = the proposal's maker, approved_by = its checker; new BEFORE INSERT "
         "trigger execution_instructions_insert_guard; execution_instruction_lots_guard admits lots for such an "
         "instruction only in its creating transaction. Every rep2 CHECK unchanged")
    find("1d system proposals", "a SYSTEM proposal has no human maker; rep2's makers_chk + maker_checker_chk need "
         "one distinct from approved_by, so its instructions are drafts by the reviewer, submitted to rep2's own "
         "approval (a second person) — NOT started approved")
    find("1e fee module", "create_assignment(conn, org_id, *, fee_schedule_id, scope_type='ACCOUNT', scope_id, "
         "created_by, replace_existing=True): precedence derived (ACCOUNT 10), a RETIRED schedule refused, NO approval "
         "of its own. fee_exclusions had NO write path at all (fee35 reads it, validate_exclusion existed unused) — "
         "rep5 adds fee_schedules.create_exclusion / end_exclusion through validate_exclusion; a SECURITY basis is "
         "the org's portfolio.assets.id (fee_calc matches position.asset_id). The join task is their approval")


async def y_schema(conn):
    section("[Y] Schema landed: tables, constraints, partial uniques, RLS with a policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep5.py"), "--verify"], REPO, timeout=300)
    check(code == 0 and "SCHEMA OK" in out, "_apply_rep5.py --verify: SCHEMA OK", (out + err)[-300:])
    seen = 0
    for t in TOUCHED:
        cons = await platform(conn, "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass "
                                    "AND contype IN ('c', 'u', 'f')", t)
        idx = await platform(conn, "SELECT indexname FROM pg_indexes WHERE schemaname || '.' || tablename = $1 "
                                   "AND indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%WHERE%'", t)
        seen += len(cons)
        print(f"[INFO] {t}: {len(cons)} CHECK/UNIQUE/FK, partial uniques {[r['indexname'] for r in idx]}")
    check(seen > 60, "CHECK / UNIQUE / FK constraints read for every touched table before seeding", f"{seen}")


async def y_join(conn):
    section("[Y] Join: maker-checker on the identical request; fees via the fee module; closed refused; advice "
            "only; one active link")
    body = {"model_id": str(S["m1"]), "settings": {"gain_budget": "1000", "avoid_short_term": True,
                                                   "short_term_wait_days": 30}}
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_MAIN}/model/join", body)
    link = (o or {}).get("link") or {}
    check(c == 201 and link.get("status") == "pending" and bool((o.get("task") or {}).get("id")),
          "join → a pending link version and an open task", f"{c} {str(o)[:200]}")
    fee = link.get("proposed_fee") or {}
    check(fee.get("schedule_id") == str(FS1) and fee.get("source") == "model_default"
          and [e.get("asset_id") for e in fee.get("exclusions") or []] == [str(S["assets"]["F"])],
          "the join proposes the model's default schedule and a SECURITY exclusion for the held fund's asset",
          f"{fee}")
    lid = link.get("id")
    S["link_main_pending"] = lid
    before_fee = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.fee_assignments WHERE scope_id = $1",
                                  ACCT_MAIN, fetch="fetchval"))
    path = f"/api/v1/accounts/{ACCT_MAIN}/model/changes/{lid}/approve"
    c1, o1 = await api(U_ADMIN1, "POST", path, {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.account_model_links WHERE id = $1::uuid AND system_to IS "
                                   "NULL", lid, fetch="fetchval")
    check(c1 == 403 and "Maker-checker" in str(o1) and st == "pending" and before_fee == 0,
          "the maker's approve is refused (403, naming maker-checker); the link stays pending, no fee row written",
          f"{c1} {st}")
    c2, o2 = await api(U_ADMIN2, "POST", path, {})
    active = await link_live(conn, ORG_A, ACCT_MAIN)
    check(c2 == 200 and active is not None and str(active["id"]) == lid and active["checker_id"] == U_ADMIN2
          and active["maker_id"] == U_ADMIN1, "a checker's identical request approves it: the link is active "
          "(checker recorded, maker kept)", f"{c2} {str(o2)[:200]}")
    S["link_main"] = lid
    fa = await scoped(conn, ORG_A, "SELECT * FROM public.fee_assignments WHERE scope_type = 'ACCOUNT' AND scope_id = $1 "
                                   "AND valid_to IS NULL AND system_to IS NULL", ACCT_MAIN)
    check(len(fa) == 1 and fa[0]["fee_schedule_id"] == FS1 and fa[0]["precedence"] == 10
          and fa[0]["created_by"] == U_ADMIN2 and active is not None and active["fee_assignment_id"] == fa[0]["id"],
          "the ACCOUNT fee assignment re-read: model default schedule, precedence 10 (derived by the fee module), "
          "made by the checker, recorded on the link", f"{[dict(r) for r in fa][:1]}")
    fx = await scoped(conn, ORG_A, "SELECT * FROM public.fee_exclusions WHERE scope_type = 'ACCOUNT' AND scope_id = $1 "
                                   "AND valid_to IS NULL AND system_to IS NULL", ACCT_MAIN)
    check(len(fx) == 1 and fx[0]["basis_type"] == "SECURITY" and fx[0]["basis_value"] == str(S["assets"]["F"])
          and fx[0]["treatment"] == "EXCLUDE" and bool((fx[0]["reason"] or "").strip()) and active is not None
          and list(active["fee_exclusion_ids"]) == [fx[0]["id"]],
          "the SECURITY fee exclusion re-read: the fund's asset, EXCLUDE, with a reason, recorded on the link")
    S["join_proposal_main"] = (o2 or {}).get("transition_proposal_id")
    p = await prop_row(conn, ORG_A, S["join_proposal_main"]) if S["join_proposal_main"] else None
    check(p is not None and p["trigger"] == "join" and p["status"] == "pending_approval" and p["maker_kind"] == "user"
          and p["maker_id"] == U_ADMIN1, "approval generated the transition proposal (trigger join, the joiner as "
          "maker, submitted)", f"{dict(p) if p else None}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_CLOSED}/model/join", {"model_id": str(S["m2"])})
    n = await scoped(conn, ORG_A, "SELECT count(*) FROM public.account_model_links WHERE account_id = $1", ACCT_CLOSED,
                     fetch="fetchval")
    check(c == 409 and "closed" in str(o) and int(n) == 0, "a closed account is refused (409) and no link row exists",
          f"{c} {str(o)[:120]}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_MAIN}/model/join", {"model_id": str(S["m2"])})
    check(c == 409, "a second join for an account already following a model is refused (409)", f"{c}")
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.account_model_links (org_id, account_id, model_id, link_group_id, "
                                  "change_kind, status, maker_id, checker_id, decided_at, joined_at) VALUES ($1, $2, $3, "
                                  "gen_random_uuid(), 'join', 'active', $4, $5, now(), now())",
                     ORG_A, ACCT_MAIN, S["m2"], U_ADMIN1, U_ADMIN2, fetch="execute")
        check(False, "the database refuses a second active link for one account")
    except asyncpg.UniqueViolationError as exc:
        check(exc.constraint_name == "account_model_links_active_uq",
              "the database refuses a second active link (account_model_links_active_uq)", exc.constraint_name)
    n_active = await scoped(conn, ORG_A, "SELECT count(*) FROM public.account_model_links WHERE account_id = $1 AND "
                                         "status = 'active' AND valid_to IS NULL AND system_to IS NULL", ACCT_MAIN,
                            fetch="fetchval")
    check(int(n_active) == 1, "exactly one active link for the account", f"{n_active}")
    # the other joins this run uses
    joins = {ACCT_ADV: ("m2", {}), ACCT_HELD: ("m2", {}), ACCT_SYS: ("m3", {}),
             ACCT_DRIFT: ("m2", {"drift_threshold": "0.05"}), ACCT_CALM: ("m2", {"drift_threshold": "0.05"}),
             ACCT_CASH: ("m2", {"drift_threshold": "0.5"})}
    for acct, (m, st_) in joins.items():
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join", {"model_id": str(S[m]),
                                                                                  "settings": st_})
        lk = (o or {}).get("link") or {}
        c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct}/model/changes/{lk.get('id')}/approve", {})
        S[f"join_{acct}"] = (o2 or {}).get("transition_proposal_id")
        check(c == 201 and c2 == 200 and bool(S[f"join_{acct}"]), f"join + approval for {ACCOUNTS[acct][8][-2:]}",
              f"{c} {c2} {str(o2)[:150]}")
    adv = await link_live(conn, ORG_A, ACCT_ADV)
    pa = await prop_row(conn, ORG_A, S[f"join_{ACCT_ADV}"])
    check(adv is not None and adv["advice_only"] is True and pa is not None and pa["advice_only"] is True
          and any(w.get("code") == "advice_only" for w in jl(pa["warnings"]) or []),
          "a non-discretionary account joins as ADVICE ONLY: link and proposal flagged, with the warning")


async def y_exclusions(conn):
    section("[Y] Exclusion policies exact (on the real model's look-through)")

    pool = await pool_()
    sa, sb, sc, sd = (str(S[f"sec_{k}"]) for k in "ABCD")

    def shim(policy, excl):
        return {"exclusions": json.dumps([{"global_security_id": g, "reason": "client request"} for g in excl]),
                "exclusion_policy": policy}

    with _ctx(ORG_A):
        async with pool.acquire() as conn2:
            base = await RP.target_for(conn2, ORG_A, shim("pro_rata", []), S["m1v1"])
            pr = await RP.target_for(conn2, ORG_A, shim("pro_rata", [sa]), S["m1v1"])
            tc = await RP.target_for(conn2, ORG_A, shim("to_cash", [sa]), S["m1v1"])
            ua = await RP.target_for(conn2, ORG_A, shim("use_alternates", [sa]), S["m1v1"])
            fb = await RP.target_for(conn2, ORG_A, shim("use_alternates", [sa, sb]), S["m1v1"])
    b = base["target"]
    check(b == {sa: D("0.4"), sb: D("0.3"), sc: D("0.2"), "cash": D("0.1")} or
          {k: D(v) for k, v in b.items()} == {sa: D("0.4"), sb: D("0.3"), sc: D("0.2"), "cash": D("0.1")},
          "the model's own target read back exactly", f"{b}")
    t = pr["target"]
    check(sa not in t and t[sb] == D("0.5400000000") and t[sc] == D("0.3600000000") and t["cash"] == D("0.1")
          and sum(t.values()) == D(1), "pro_rata: A's 40% spread 30:20 → B 54%, C 36%, cash unchanged, total exactly 1",
          f"{t}")
    t = tc["target"]
    check(sa not in t and t[sb] == D("0.3") and t[sc] == D("0.2") and t["cash"] == D("0.5") and sum(t.values()) == D(1),
          "to_cash: A's 40% to cash → cash 50%, B and C unchanged", f"{t}")
    t = ua["target"]
    check(sa not in t and t.get(sd) == D("0.4") and t[sb] == D("0.3") and t[sc] == D("0.2")
          and any("alternate" in n for n in ua["notes"]), "use_alternates: A's 40% goes to its alternate D (the "
          "model's own alternates)", f"{t} {ua['notes']}")
    t = fb["target"]
    check(sb not in t and t.get(sd) == D("0.6000000000") and t[sc] == D("0.3000000000") and t["cash"] == D("0.1")
          and sum(t.values()) == D(1) and any("no usable alternate" in n for n in fb["notes"]),
          "use_alternates without an alternate (B) falls back to pro rata and SAYS so: D 60%, C 30%",
          f"{t} {fb['notes']}")


async def y_transition(conn):
    section("[Y] Transition: lot order, short-term guard, exact gain budget, plan by year, unpriced never sold")

    pid = S.get("join_proposal_main")
    p = await prop_row(conn, ORG_A, pid)
    trades = await trades_rows(conn, ORG_A, pid)
    by = {(r["global_security_id"], r["side"]): r for r in trades}
    sx, sf, su = S["sec_X"], S["sec_F"], S["sec_U"]
    tx = by.get((sx, "sell"))
    lots = jl(tx["lots"]) if tx else []
    check(tx is not None and [x["lot_id"] for x in lots] == [str(S["LX3"]), str(S["LX1"])],
          "SEC X (not in the model): the LOSS lot first (LX3), then the highest-cost gain lot (LX1)",
          f"{[(x['lot_id'][-6:], x['quantity']) for x in lots]}")
    check(tx is not None and D(tx["quantity"]) == D(36) and [D(x["quantity"]) for x in lots] == [D(20), D(16)],
          "the gain budget fits exactly: 20 loss shares + 16 gain shares sold now (room 1000 − 300 realized + 400 "
          "loss − 100 fund gain = 1000 → floor(1000 / 60) = 16)",
          f"{tx['quantity'] if tx else None}")
    gains_now = sum((D(r["est_st_gain"]) + D(r["est_lt_gain"]) for r in trades if r["side"] == "sell"), D(0))
    ytd = D(p["gain_realized_ytd"]) if p else None
    check(p is not None and ytd == D(300) and gains_now == D(660) and ytd + gains_now <= D(1000)
          and D(1000) - (ytd + gains_now) < D(60),
          "net realized now (660 = −400 loss + 100 fund + 960) + already realized this year (300) ≤ the 1000 budget, "
          "and not one more gain share (60) fits", f"ytd {ytd} now {gains_now}")
    later = jl(p["sell_later"]) if p else []
    lx2 = [x for x in later if x["lot_id"] == str(S["LX2"])]
    turns = E.long_term_from(S["LX2_ACQ"])
    check(bool(lx2) and "turns long-term on " + turns.isoformat() in lx2[0]["reason"]
          and sum(D(x["quantity"]) for x in lx2) == D(30),
          "the short-term lot turning long-term within the 30-day wait is DEFERRED with its date",
          f"{turns} {lx2[:1]}")
    years: dict = {}
    for x in later:
        if x["year"] is not None and x["est_gain"] is not None:
            years[x["year"]] = years.get(x["year"], D(0)) + D(x["est_gain"])
    check(bool(years) and all(v <= D(1000) for v in years.values()),
          "every later year's planned gains fit that year's budget", f"{years}")
    total_x = D(36) + sum((D(x["quantity"]) for x in later if x["key"] == str(sx)), D(0))
    check(total_x == D(100), "every SEC X share is accounted for: 36 now + the rest planned later", f"{total_x}")
    check((su, "sell") not in by and (su, "buy") not in by
          and any(w.get("code") == "cannot_value" for w in jl(p["warnings"]) or []),
          "the unpriced holding is never sold (cannot value — needs a price)")
    plan = await scoped(conn, ORG_A, "SELECT * FROM public.account_transition_plans WHERE account_id = $1 AND valid_to "
                                     "IS NULL AND system_to IS NULL", ACCT_MAIN, fetch="fetchrow")
    hold = jl(plan["holdings"]) if plan else []
    acts = {h["key"]: h["action"] for h in hold}
    yrs = sorted(int(x["year"]) for x in later if x["year"] is not None)
    check(plan is not None and acts.get(str(su)) == "cannot_value" and acts.get(str(sx)) == "sell_now_and_later"
          and acts.get(str(sf)) == "sell_now" and plan["years_to_complete"] == (yrs[-1] - TODAY.year + 1)
          and plan["years_to_complete"] >= 2,
          "the plan: keep / sell now / sell later by year, the years to complete at the budget", f"{acts} "
          f"{plan['years_to_complete'] if plan else None}")
    # lot ORDER on the account's own loaded lots (equal cost: long-term before short-term)
    pool = await pool_()
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            h = await AM.load_holdings(c2, ORG_A, ACCT_MAIN)
    hx = next((x for x in h["holdings"] if x.key == str(sx)), None)
    lot_eq_lt = E.Lot("lt", D(10), D(40), date(2015, 1, 1))
    lot_eq_st = E.Lot("st", D(10), D(40), TODAY - timedelta(days=100))
    order = [x.id for x in sorted((hx.lots if hx else []) + [lot_eq_lt, lot_eq_st],
                                  key=lambda x: E.lot_order_key(x, hx.price if hx else D(1), TODAY))]
    check(hx is not None and hx.price == D(100) and hx.price_source == "account_position" and hx.price_as_of == VAL
          and order[0] == str(S["LX3"]) and order.index("lt") < order.index("st"),
          "loaded from the account's own positions: price 100 = market value / quantity as of the valuation date; "
          "order losses first, and at EQUAL cost long-term before short-term", f"{order}")


async def y_proposals(conn):
    section("[Y] Proposals from all five triggers; notional for unpriced; whole vs fractional; dust; one open per "
            "account; expiry")

    pool = await pool_()
    # join — notional for unpriced, whole shares for Altruist (fractional 'unknown')
    trades = await trades_rows(conn, ORG_A, S["join_proposal_main"])
    by = {(r["global_security_id"], r["side"]): r for r in trades}
    ba, bb, bc = by.get((S["sec_A"], "buy")), by.get((S["sec_B"], "buy")), by.get((S["sec_C"], "buy"))
    check(ba is not None and ba["quantity"] is not None and D(ba["quantity"]) == D(ba["quantity"]).to_integral_value()
          and ba["price_source"] == "account_position" and D(ba["price"]) == D(50) and ba["price_as_of"] == VAL,
          "a priced buy is WHOLE shares at the account's own price (Altruist fractional_shares 'unknown')",
          f"{dict(ba) if ba else None}")
    check(bb is not None and bc is not None and bb["quantity"] is None and bb["notional"] is not None
          and bc["notional"] is not None and bb["price_source"] == "none" and bb["price"] is None,
          "an unpriced buy is by NOTIONAL (an amount), never a guessed quantity",
          f"{dict(bb) if bb else None}")
    # fractional when the custodian says 'yes'
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            acct = await AM.load_account(c2, ORG_A, ACCT_FRAC)
            shim = {"exclusions": "[]", "exclusion_policy": "pro_rata", "gain_budget": None, "avoid_short_term": True,
                    "short_term_wait_days": 30, "cash_floor": D(0), "advice_only": False, "drift_threshold": None}
            cf = await RP.compute(c2, ORG_A, acct, shim, S["m2v1"])
            hold = await AM.load_holdings(c2, ORG_A, ACCT_FRAC)
    fbuy = next((t for t in cf["result"].trades if t.side == "buy"), None)
    whole = E.build(hold["holdings"], hold["cash"], {str(S["sec_A"]): D("0.9"), "cash": D("0.1")}, {}, {},
                    E.BuildSettings(sale=E.SaleSettings(TODAY, None, fractional=False), min_trade=D(100)))
    wbuy = next((t for t in whole.trades if t.side == "buy"), None)
    check(cf["fractional"] is True and fbuy is not None and fbuy.quantity != fbuy.quantity.to_integral_value()
          and wbuy is not None and wbuy.quantity == wbuy.quantity.to_integral_value() and wbuy.quantity < fbuy.quantity,
          "fractional_shares 'yes' → a fractional quantity; the same holdings without it → whole shares",
          f"{fbuy.quantity if fbuy else None} vs {wbuy.quantity if wbuy else None}")
    # dust
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            hm = await AM.load_holdings(c2, ORG_A, ACCT_MAIN)
    tgt = {str(S["sec_A"]): D("0.4"), str(S["sec_B"]): D("0.3"), str(S["sec_C"]): D("0.2"), "cash": D("0.1")}
    loose = E.build(hm["holdings"], hm["cash"], tgt, {}, {}, E.BuildSettings(sale=E.SaleSettings(TODAY, None),
                                                                             min_trade=D(0)))
    tight = E.build(hm["holdings"], hm["cash"], tgt, {}, {}, E.BuildSettings(sale=E.SaleSettings(TODAY, None),
                                                                             min_trade=D(3000)))

    def dollars(t):
        return t.notional if t.notional is not None else t.quantity * t.price

    check(bool(loose.trades) and len(tight.trades) < len(loose.trades)
          and all(dollars(t) >= D(3000) for t in tight.trades)
          and any(w["code"] == "dust_suppressed" for w in tight.warnings),
          "trades under the minimum trade size are suppressed (and counted in a warning)",
          f"{len(loose.trades)} → {len(tight.trades)}")
    # model_change via the event
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            model = await M.load_model(c2, ORG_A, S["m3"])
            v2 = await M._create_version(c2, org_id=ORG_A, model=model, origin="revision", maker_ids=[U_ADMIN1],
                                         created_by=U_ADMIN1, positions=[pos(1, "A", "0.60"), pos(2, "X", "0.30"),
                                                                         pos(3, "cash", "0.10")])
        await M.submit(pool, org_id=ORG_A, actor_id=U_ADMIN1, model_id=S["m3"], version_id=v2["id"])
        await M.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, model_id=S["m3"], version_id=v2["id"], outcome="approve")
    S["m3v2"] = UUID(str(v2["id"]))
    ev = await scoped(conn, ORG_A, "SELECT id FROM domain_events WHERE event_type = 'model.version_approved' AND "
                                   "source_id = $1::uuid", S["m3v2"], fetch="fetchval")
    mc = await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposals WHERE account_id = $1 AND trigger = "
                                   "'model_change' AND system_to IS NULL", ACCT_SYS)
    task = await scoped(conn, ORG_A, "SELECT r.context->'human_task'->>'kind' FROM workflow_run_steps rs JOIN "
                                     "workflow_runs r ON r.id = rs.workflow_run_id WHERE rs.id = $1",
                        mc[0]["approval_task_id"], fetch="fetchval") if mc and mc[0]["approval_task_id"] else None
    check(ev is not None and len(mc) == 1 and mc[0]["model_version_id"] == S["m3v2"] and mc[0]["maker_kind"] == "system"
          and mc[0]["status"] == "pending_approval" and task == "rebalance_proposal_review"
          and (jl(mc[0]["trigger_ref"]) or {}).get("event_id") == str(ev),
          "model_change: the 'model.version_approved' event produced a SYSTEM proposal for the follower, on a review "
          "task, citing the event", f"{len(mc)} {task}")
    S["mc_sys"] = mc[0]["id"] if mc else None
    jp = await prop_row(conn, ORG_A, S[f"join_{ACCT_SYS}"])
    check(jp is not None and jp["status"] == "superseded" and jp["superseded_by_id"] == (mc[0]["id"] if mc else None),
          "the account's older open proposal was superseded by it (superseded_by recorded)")
    with _ctx(ORG_A):
        again = await RP.on_model_version_approved(pool, org_id=ORG_A, model_id=S["m3"], version_id=S["m3v2"])
    n = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND trigger = "
                                  "'model_change' AND archived_from_id IS NULL AND system_to IS NULL", ACCT_SYS,
                     fetch="fetchval")
    check(int(n) == 1 and any(x.get("skipped") for x in again), "consuming the same event again is a no-op "
          "(idempotent per account + model version)", f"{again}")
    # drift / none under / cash_flow — after the join proposals are rejected (reject needs a note)
    for acct in (ACCT_DRIFT, ACCT_CALM, ACCT_CASH):
        pid = S[f"join_{acct}"]
        c0, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/reject", {})
        c1, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/reject", {"note": "REP5 verify — not now"})
        r = await prop_row(conn, ORG_A, pid)
        check(c0 == 422 and c1 == 200 and r is not None and r["status"] == "rejected"
              and r["decision_note"] == "REP5 verify — not now", "a reject without a note is refused (422); with "
              "one it is rejected", f"{c0} {c1}")
    with _ctx(ORG_A):
        drift = await RP.scan_account(pool, org_id=ORG_A, account_id=ACCT_DRIFT)
        calm = await RP.scan_account(pool, org_id=ORG_A, account_id=ACCT_CALM)
        cash = await RP.scan_account(pool, org_id=ORG_A, account_id=ACCT_CASH)
    dp = await open_prop(conn, ORG_A, ACCT_DRIFT)
    check(drift.get("action") == "drift" and dp is not None and dp["trigger"] == "drift" and dp["maker_kind"] == "system"
          and D(dp["drift_before"]) > D("0.05"), "drift over the account's threshold → a drift proposal (system)",
          f"{drift}")
    cp = await open_prop(conn, ORG_A, ACCT_CALM)
    check(calm.get("action") == "none" and cp is None, "an account within its threshold gets NO proposal", f"{calm}")
    kp = await open_prop(conn, ORG_A, ACCT_CASH)
    check(cash.get("action") == "cash_flow" and kp is not None and kp["trigger"] == "cash_flow"
          and D(kp["drift_before"]) <= D("0.5"), "cash above its target by more than the threshold → cash_flow "
          "(drift within its own threshold)", f"{cash}")
    # a staff-entered withdrawal supersedes the system cash-flow proposal (logged)
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_CASH}/proposals",
                     {"trigger": "cash_flow", "withdrawal_amount": "500"})
    newp = (o or {}).get("proposal") or {}
    old = await prop_row(conn, ORG_A, kp["id"]) if kp else None
    audit = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE action = 'proposal.superseded' AND "
                                      "resource_id = $1::uuid", kp["id"] if kp else uuid.uuid4(), fetch="fetchval")
    old_task = await scoped(conn, ORG_A, "SELECT status FROM workflow_run_steps WHERE id = $1",
                            kp["approval_task_id"], fetch="fetchval") if kp else None
    tcash = next((D(w["weight"]) for w in newp.get("target_weights") or [] if w["key"] == "cash"), None)
    check(c == 201 and newp.get("trigger") == "cash_flow" and newp.get("withdrawal_amount") == "500.00000000"
          and tcash is not None and tcash > D("0.1"),
          "a staff-entered withdrawal → a cash_flow proposal raising the cash target", f"{c} {tcash}")
    check(old is not None and old["status"] == "superseded" and str(old["superseded_by_id"]) == newp.get("id")
          and int(audit) == 1 and old_task == "cancelled",
          "one open proposal per account: the older one superseded (successor named, audit logged, its task "
          "withdrawn)", f"{old['status'] if old else None} {audit} {old_task}")
    # manual — submit only by its maker
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_CALM}/proposals", {"trigger": "manual"})
    mp = (o or {}).get("proposal") or {}
    c1, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{mp.get('id')}/submit", {})
    c2, o2 = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{mp.get('id')}/submit", {})
    check(c == 201 and mp.get("trigger") == "manual" and mp.get("status") == "draft" and c1 == 403 and c2 == 200
          and (o2.get("proposal") or {}).get("status") == "pending_approval",
          "manual ('Rebalance now'): a draft; only its maker submits it (403 for another) → awaiting approval",
          f"{c} {c1} {c2}")
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.rebalance_proposals (org_id, account_id, link_id, model_id, "
                                  "model_version_id, trigger, expires_at, maker_kind, maker_id) VALUES ($1, $2, $3, $4, "
                                  "$5, 'manual', now() + interval '1 day', 'user', $6)", ORG_A, ACCT_CALM,
                     UUID(mp["link_id"]), S["m2"], S["m2v1"], U_ADMIN1, fetch="execute")
        check(False, "the database refuses a second open proposal for one account")
    except asyncpg.UniqueViolationError as exc:
        check(exc.constraint_name == "rebalance_proposals_open_uq", "the database refuses a second open proposal "
              "(rebalance_proposals_open_uq)", exc.constraint_name)
    # expiry — the valuation moves
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        await conn.execute("INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                           "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, 90, 4600, "
                           "'manual', 'manual', $5)", ORG_A, ENT_A, S["assets"]["A"], TODAY, ACCT_CALM)
    c, o = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{mp.get('id')}/approve", {})
    ep = await prop_row(conn, ORG_A, mp.get("id"))
    check(c == 409 and ep is not None and ep["status"] == "expired" and "valuation moved" in (ep["status_reason"] or ""),
          "a proposal EXPIRES when the account's valuation date moves past it (approve refused, 409)",
          f"{c} {ep['status_reason'] if ep else None}")
    with _ctx(ORG_A):
        tp = await RP.generate(pool, org_id=ORG_A, account_id=ACCT_CALM, trigger="manual", maker_kind="user",
                               maker_id=U_ADMIN1)
        did = await RP._expire_if_due(pool, ORG_A, {**tp, "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    tr = await prop_row(conn, ORG_A, tp["id"])
    check(did is True and tr is not None and tr["status"] == "expired" and "expired at" in (tr["status_reason"] or ""),
          "and after N days (expires_at)", f"{tr['status_reason'] if tr else None}")


async def y_wash(conn):
    section("[Y] Wash-sale flag both directions across the household (IRA included); never blocks")
    trades = await trades_rows(conn, ORG_A, S["join_proposal_main"])
    by = {(r["global_security_id"], r["side"]): r for r in trades}
    fa = jl((by.get((S["sec_A"], "buy")) or {}).get("wash_sale_flag"))
    fx = jl((by.get((S["sec_X"], "sell")) or {}).get("wash_sale_flag"))
    fb = jl((by.get((S["sec_B"], "buy")) or {}).get("wash_sale_flag"))
    ff = jl((by.get((S["sec_F"], "sell")) or {}).get("wash_sale_flag"))
    check(fa is not None and fa["kind"] == "buy_after_loss_sale"
          and any(m["registration_type"] == "ira" and m["side"] == "sell" for m in fa["matches"]),
          "a BUY of a security the household's IRA sold at a loss 5 days ago is flagged", f"{fa}")
    check(fx is not None and fx["kind"] == "loss_sale_after_buy"
          and any(m["registration_type"] == "ira" and m["side"] == "buy" for m in fx["matches"]),
          "a LOSS SALE of a security the household's IRA bought 10 days ago is flagged", f"{fx}")
    check(fb is None, "a loss sale 45 days ago (outside 30) flags nothing", f"{fb}")
    check(ff is None, "a sale at a gain (no recent buy) flags nothing", f"{ff}")
    S["flagged_lines"] = [r["line_no"] for r in trades if r["wash_sale_flag"] is not None]


async def y_execution(conn):
    section("[Y] Execution: approved proposal → approved manual instructions with lots, maker and checker; rep2 flow "
            "unchanged; executed when all closed")

    pool = await pool_()
    pid = S["join_proposal_main"]
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/approve", {})
    st = (await prop_row(conn, ORG_A, pid))["status"]
    check(c1 == 403 and st == "pending_approval", "the proposal's maker is refused (403); it stays pending",
          f"{c1} {st}")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {})
    p = await prop_row(conn, ORG_A, pid)
    ex = jl(p["execution"]) or {}
    ids = ex.get("instruction_ids") or []
    trades = await trades_rows(conn, ORG_A, pid)
    check(c2 == 200 and p["status"] == "approved" and p["checker_id"] == U_ADMIN2 and len(ids) == len(trades) > 0
          and all(t["instruction_id"] is not None for t in trades),
          "a checker approves → every trade routed: one instruction each, linked on the trade", f"{c2} {len(ids)}")
    rows = await scoped(conn, ORG_A, "SELECT * FROM public.execution_instructions WHERE id = ANY($1::uuid[]) AND "
                                     "system_to IS NULL", ids)
    check(len(rows) == len(ids) and all(r["status"] == "approved" and r["maker_id"] == U_ADMIN1
                                        and list(r["maker_ids"]) == [U_ADMIN1] and r["approved_by"] == U_ADMIN2
                                        and r["source"] == "proposal" and r["source_ref"] == UUID(str(pid))
                                        and r["routed_via"] == "manual" and r["executor_type"] == "custodian_desk"
                                        for r in rows),
          "instructions START APPROVED with the proposal's maker and checker, sourced from it, routed manual")
    check(all("equity_trading_api is 'unknown'" in (r["routing_reason"] or "") for r in rows)
          and (ex.get("routing") or {}).get("adapter") == "altruist",
          "Altruist's trading API 'unknown' → manual execution instructions, and the reason says so")
    sell = next((t for t in trades if t["side"] == "sell" and t["global_security_id"] == S["sec_X"]), None)
    lots = await scoped(conn, ORG_A, "SELECT tax_lot_id, quantity FROM public.execution_instruction_lots WHERE "
                                     "instruction_id = $1 AND valid_to IS NULL", sell["instruction_id"]) if sell else []
    want = sorted((x["lot_id"], D(x["quantity"])) for x in jl(sell["lots"])) if sell else []
    check(bool(want) and sorted((str(r["tax_lot_id"]), D(r["quantity"])) for r in lots) == want,
          "the sell instruction carries the proposal's specific lots", f"{want}")
    flagged = [t for t in trades if t["wash_sale_flag"] is not None]
    fl_rows = {r["id"]: r for r in rows}
    check(bool(flagged) and all(t["instruction_id"] in fl_rows and (fl_rows[t["instruction_id"]]["wash_sale_notes"]
                                                                    or "").startswith("[")
                                for t in flagged),
          "wash-sale flags never block: the flagged trades have instructions, the flag in their notes")
    ev = await scoped(conn, ORG_A, "SELECT to_status, actor_id, detail FROM public.execution_instruction_events WHERE "
                                   "instruction_id = $1 AND action = 'create'", ids[0], fetch="fetchrow") if ids else None
    check(ev is not None and ev["to_status"] == "approved" and ev["actor_id"] == U_ADMIN2
          and (jl(ev["detail"]) or {}).get("upstream", {}).get("proposal_id") == str(pid),
          "the create event records the upstream approval (proposal, task, maker, checker)")
    # the DB guard and the app re-read refuse a forged upstream
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.execution_instructions (org_id, account_id, global_security_id, "
                                  "side, quantity, price_kind, executor_type, execute_by, if_not_filled, source, "
                                  "routed_via, routing_reason, status, maker_id, maker_ids, approved_by, approved_at) "
                                  "VALUES ($1, $2, $3, 'buy', 1, 'market', 'member', $4, 'ask', 'manual', 'manual', 'x', "
                                  "'approved', $5, ARRAY[$5::uuid], $6, now())",
                     ORG_A, ACCT_MAIN, S["sec_A"], TODAY + timedelta(days=3), U_ADMIN1, U_ADMIN2, fetch="execute")
        check(False, "the database refuses an instruction inserted approved without a proposal")
    except asyncpg.InsufficientPrivilegeError:
        check(True, "the database refuses an instruction inserted 'approved' that no approved proposal backs")
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.execution_instruction_lots (org_id, instruction_id, tax_lot_id, "
                                  "quantity, created_by) VALUES ($1, $2, $3, 1, $4)", ORG_A, sell["instruction_id"],
                     S["LA1"], U_ADMIN1, fetch="execute")
        check(False, "a lot cannot be added to an approved instruction after its creating transaction")
    except asyncpg.InsufficientPrivilegeError:
        check(True, "a lot cannot be added to an approved instruction after the transaction that created it")
    tl = TradeList(account_id=ACCT_MAIN, trades=[{"global_security_id": S["sec_A"], "side": "buy", "quantity": "1"}],
                   executor_type="member", execute_by=TODAY + timedelta(days=3), source="proposal", source_ref=pid)
    try:
        with _ctx(ORG_A):
            async with pool.acquire() as c3:
                acct = await X.load_account(c3, ORG_A, ACCT_MAIN)
            await X.create_instructions(pool, org_id=ORG_A, actor_id=U_ADMIN2, account=acct, trade_list=tl,
                                        routed_via="manual", routing_reason="x",
                                        upstream={"proposal_id": str(pid), "maker_id": str(U_ADMIN3),
                                                  "checker_id": str(U_ADMIN2)})
        check(False, "create_instructions refuses an upstream whose maker does not match the proposal")
    except X.ExecutionError as exc:
        check("maker" in str(exc), "create_instructions re-reads the proposal and refuses a forged maker", str(exc)[:100])
    # rep2's later transitions, unchanged
    first = next((r for r in rows if r["quantity"] is not None), None)
    iid = str(first["id"]) if first else None
    ca, _ = await api(U_ADMIN2, "POST", f"/api/v1/execution/instructions/{iid}/approve", {})
    c_s, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/sent", {"channel": "email"})
    c_f, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/fills",
                       {"quantity": str(first["quantity"]), "price": "50.00",
                        "filled_at": datetime.now(timezone.utc).isoformat()})
    c_r, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/reconcile",
                       {"method": "manual", "note": "REP5 verify — confirmed by phone"})
    c_c, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/close", {})
    fin = await scoped(conn, ORG_A, "SELECT status FROM public.execution_instructions WHERE id = $1::uuid AND system_to "
                                    "IS NULL", iid, fetch="fetchval")
    check(ca == 409 and (c_s, c_f, c_r, c_c) == (200, 200, 200, 200) and fin == "closed",
          "rep2's flow unchanged: an approved instruction cannot be approved again (409); sent → filled → "
          "reconciled → closed", f"{ca} {(c_s, c_f, c_r, c_c)} {fin}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/refresh", {})
    mid = (await prop_row(conn, ORG_A, pid))["status"]
    check(c == 200 and mid == "approved", "the proposal stays approved while any instruction is still working")
    for r in rows:
        if str(r["id"]) != iid:
            await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{r['id']}/cancel",
                      {"reason": "REP5 verify — not needed"})
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/refresh", {})
    done = await prop_row(conn, ORG_A, pid)
    check(c == 200 and done["status"] == "executed" and "1 closed" in (done["status_reason"] or "")
          and "cancelled" in (done["status_reason"] or "") and done["executed_at"] is not None,
          "executed once every instruction is closed or cancelled — the reason says how many of each",
          f"{done['status_reason']}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_MAIN}/proposals", {"trigger": "manual"})
    check(c == 201, "after execution a new proposal can be generated for the account", f"{c} {str(o)[:120]}")
    # a SYSTEM proposal: the reviewer's single decision cannot stand in for rep2's maker AND checker
    sp = S.get("mc_sys")
    c, o = await api(U_ADMIN3, "POST", f"/api/v1/proposals/{sp}/approve", {})
    sprow = await prop_row(conn, ORG_A, sp)
    sids = (jl(sprow["execution"]) or {}).get("instruction_ids") or []
    srows = await scoped(conn, ORG_A, "SELECT * FROM public.execution_instructions WHERE id = ANY($1::uuid[]) AND "
                                      "system_to IS NULL", sids)
    check(c == 200 and sprow["checker_id"] == U_ADMIN3 and bool(srows)
          and all(r["status"] == "pending_approval" and r["maker_id"] == U_ADMIN3 for r in srows),
          "a system proposal approved by a reviewer → instructions made by the reviewer, awaiting rep2's own approval")
    find("system-proposal instructions", "not started approved: rep2's makers_chk / maker_checker_chk need a human "
         "maker distinct from approved_by and a system proposal has none — a second person approves on rep2")
    if srows:
        x1, _ = await api(U_ADMIN3, "POST", f"/api/v1/execution/instructions/{srows[0]['id']}/approve", {})
        x2, _ = await api(U_ADMIN2, "POST", f"/api/v1/execution/instructions/{srows[0]['id']}/approve", {})
        check(x1 == 403 and x2 == 200, "rep2 refuses the reviewer (its maker) and a second person approves", f"{x1} {x2}")
    # held-away and advice-only → the member executes
    for acct, why in ((ACCT_HELD, "held-away account"), (ACCT_ADV, None)):
        pid2 = S[f"join_{acct}"]
        c, o = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid2}/approve", {})
        r2 = await prop_row(conn, ORG_A, pid2)
        ids2 = (jl(r2["execution"]) or {}).get("instruction_ids") or []
        rr = await scoped(conn, ORG_A, "SELECT executor_type, routing_reason, notes, status FROM "
                                       "public.execution_instructions WHERE id = ANY($1::uuid[]) AND system_to IS NULL",
                          ids2)
        ok = bool(rr) and all(x["executor_type"] == "member" and x["status"] == "approved" for x in rr)
        if why:
            ok = ok and all(why in (x["routing_reason"] or "") for x in rr)
        else:
            ok = ok and all("ADVICE ONLY" in (x["notes"] or "") for x in rr)
        check(c == 200 and ok, f"{'held-away' if why else 'advice-only'}: the instructions go to the member "
              f"({why or 'ADVICE ONLY in the notes'})", f"{c} {[dict(x) for x in rr][:1]}")


async def y_name(conn):
    section("[Y] Manual variant name; .gitignore covers sprint_prompts/logs/ and no log is tracked")

    pool = await pool_()
    p = RM.params_from_input(custodian_code="manual", method="full")
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            name = await RM._model_name(c2, ORG_A, {"ticker": f"{PREFIX}F"}, p)
    check(name == f"Holdings of {PREFIX}F — Manual", "the manual-custodian variant reads 'Holdings of X — Manual' "
          "(the base name is taken by the fixture model)", name)
    check(CC.CUSTODIAN_LABELS["manual"] == "Manual — execution instruction",
          "the capability matrix keeps its own custodian label (a model name is not a route)")
    gi = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    code, out, _ = run_cmd(["git", "ls-files", "sprint_prompts/logs"], REPO, timeout=60)
    code2, out2, _ = run_cmd(["git", "check-ignore", "-q", "sprint_prompts/logs/anything.log"], REPO, timeout=60)
    check("sprint_prompts/logs/" in [x.strip() for x in gi] and code == 0 and out.strip() == "" and code2 == 0,
          ".gitignore ignores sprint_prompts/logs/ entirely; git ls-files sprint_prompts/logs is empty",
          f"{out.strip()[:120]}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        mb = await conn.fetchval("INSERT INTO portfolio.models (org_id, name, source, status, created_by) VALUES ($1, "
                                 "'REP5 VERIFY B', 'upload', 'draft', $2) RETURNING id", ORG_B, U_ADMIN_B)
        await conn.execute("INSERT INTO public.account_model_links (org_id, account_id, model_id, link_group_id, "
                           "change_kind, status, maker_id, checker_id, decided_at, decision_note) VALUES ($1, $2, $3, "
                           "gen_random_uuid(), 'join', 'rejected', $4, $5, now(), 'REP5 verify — org B row')",
                           ORG_B, ACCT_B, mb, U_ADMIN_B, U_ADMIN_B2)
    a_in_a = await scoped(conn, ORG_A, "SELECT count(*) FROM public.account_model_links WHERE org_id = $1", ORG_A,
                          fetch="fetchval")
    b_in_a = await scoped(conn, ORG_A, "SELECT count(*) FROM public.account_model_links WHERE org_id = $1", ORG_B,
                          fetch="fetchval")
    b_in_b = await scoped(conn, ORG_B, "SELECT count(*) FROM public.account_model_links WHERE org_id = $1", ORG_B,
                          fetch="fetchval")
    a_in_b = await scoped(conn, ORG_B, "SELECT count(*) FROM public.account_model_links WHERE org_id = $1", ORG_A,
                          fetch="fetchval")
    pa_in_b = await scoped(conn, ORG_B, "SELECT count(*) FROM public.rebalance_proposals", fetch="fetchval")
    pa_in_a = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals", fetch="fetchval")
    check(int(a_in_a) > 0 and int(b_in_b) > 0 and int(b_in_a) == 0 and int(a_in_b) == 0 and int(pa_in_a) > 0
          and int(pa_in_b) == 0, "RLS: each org sees its own links (non-empty) and none of the other's; org B sees "
          "none of org A's proposals", f"{a_in_a} {b_in_b} {b_in_a} {a_in_b} {pa_in_a} {pa_in_b}")
    c1, _ = await api(U_ADMIN_B, "GET", f"/api/v1/accounts/{ACCT_MAIN}/model")
    c2, _ = await api(U_ADMIN_B, "GET", f"/api/v1/proposals/{S['join_proposal_main']}")
    c3, _ = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_B}/model")
    c4, _ = await api(U_ADMIN_B, "POST", f"/api/v1/proposals/{S['join_proposal_main']}/refresh", {})
    check((c1, c2, c3, c4) == (404, 404, 404, 404), "another org's account / proposal is a 404 both directions",
          f"{(c1, c2, c3, c4)}")
    # gates both ways on IDENTICAL requests
    body = {"model_id": str(S["m2"])}
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/accounts/{ACCT_IRA}/model/join", body)
    ca, _ = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_IRA}/model/join", body)
    check(cv == 403 and "manage_proposals" in str(ov) and ca == 201,
          "join: the viewer is refused naming manage_proposals; an org admin's identical request is accepted",
          f"{cv} {ca}")
    pend = await link_live(conn, ORG_A, ACCT_IRA, "pending")
    path = f"/api/v1/accounts/{ACCT_IRA}/model/changes/{pend['id'] if pend else uuid.uuid4()}/reject"
    cv, ov = await api(U_VIEWER, "POST", path, {"note": "verify"})
    ca, _ = await api(U_ADMIN2, "POST", path, {"note": "verify"})
    check(cv == 403 and "approve_proposals" in str(ov) and ca == 200,
          "decide a change: the viewer refused naming approve_proposals; a checker's identical request decides it",
          f"{cv} {ca}")
    cm, om = await api(U_MEMBER, "GET", "/api/v1/proposals/accounts")
    cv, ov = await api(U_VIEWER, "GET", "/api/v1/proposals/accounts")
    check(cm == 403 and "view_proposals" in str(om) and cv == 200 and bool(ov.get("rows")),
          "read: a member without view_proposals is refused naming it; the viewer reads the list", f"{cm} {cv}")
    cv, ov = await api(U_VIEWER, "GET", f"/api/v1/accounts/{ACCT_MAIN}/model")
    ca, oa = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_MAIN}/model")
    pv, pa = ov.get("permissions") or {}, oa.get("permissions") or {}
    check(cv == 200 and pv.get("can_write") is False and ov.get("actions") == [] and ov.get("registry") == []
          and (ov.get("vocabularies") or {}).get("editable") == [] and ca == 200 and pa.get("can_write") is True
          and "generate" in (oa.get("actions") or []) and len((oa.get("vocabularies") or {}).get("editable") or []) > 0,
          "envelopes: view-only → can_write false, no actions, empty registry and editable; an admin → the opposite")
    voc = oa.get("vocabularies") or {}
    check(all(bool(voc.get(k)) for k in ("triggers", "statuses", "exclusion_policies", "warning_codes",
                                          "harvesting_engines")),
          "vocabularies carry triggers, statuses, exclusion policies, warning codes and harvesting engines")
    eng_alt = [o["key"] for o in oa.get("harvesting_engines") or []]
    cf, of = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_FRAC}/model")
    eng_frac = [o["key"] for o in of.get("harvesting_engines") or []]
    cx, ox = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_CALM}/model/settings",
                       {"settings": {"harvesting_engine": "custodian"}})
    check(eng_alt == ["hollisworks"] and eng_frac == ["hollisworks", "custodian"] and cx == 422,
          "the custodian harvesting engine is offered only where custodian_tax_loss_harvesting is 'yes', and "
          "refused elsewhere", f"{eng_alt} {eng_frac} {cx}")


async def y_new_org(conn):
    section("[Y] New org's first org_admin gets the three proposal permissions")

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        before = await conn.fetchval("SELECT count(*) FROM roles WHERE org_id = $1 AND name = 'org_admin'", ORG_C)
        await rbac.grant_org_admin(conn, U_NEWADMIN, ORG_C)
        perms = {r["name"] for r in await conn.fetch(
            "SELECT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id JOIN role_permissions rp ON "
            "rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id WHERE ur.user_id = $1", U_NEWADMIN)}
    check(int(before) == 0 and set(PROPOSAL_PERMS) <= perms, "a new org's first org_admin holds view / manage / "
          "approve_proposals (rbac.ensure_org_admin_role)", f"{sorted(perms & set(PROPOSAL_PERMS))}")
    missing = await platform(conn, """SELECT count(*) FROM roles r CROSS JOIN permissions p WHERE r.name = 'org_admin'
                                      AND p.name = ANY($1::text[]) AND NOT (r.org_id = ANY($2::uuid[]))
                                      AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                                                      AND rp.permission_id = p.id)""",
                             list(PROPOSAL_PERMS), [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
    check(int(missing) == 0, "every real org_admin role holds the three (fixture-org roles ignored)", f"{missing}")


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k].get("after") != gn[k].get("after")]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    new = [k for k in gn if k not in gb and (k[1].startswith("/api/v1/proposals") or (
        k[1].startswith("/api/v1/accounts/{account_id}/") and ("/model" in k[1] or k[1].endswith("/proposals"))))]
    check(len(new) == 17, "17 new routes under /accounts/{id}/model…, /accounts/{id}/proposals, /proposals…",
          f"{len(new)}")

    def want(k):
        if "GET" in k[0]:
            return "view_proposals"
        return "approve_proposals" if k[1].endswith(("/approve", "/reject")) else "manage_proposals"

    bad = [(k, gn[k].get("after")) for k in new if want(k) not in str(gn[k].get("after"))]
    check(not bad, "each names its proposal permission: GET view, decide approve, every other write manage",
          f"{bad[:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    rep5 = re.findall(r"^ok \d+ - (?:account page|proposal|accounts list|harvesting|lib|menu)", out, re.M)
    check(len(rep5) >= 10 and "not ok" not in out, "the rep5 render tests ran and passed (no Join / Generate / "
          "Approve controls without the server's flags)", f"{len(rep5)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep2.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep2.py")], REPO, timeout=1200, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    result = re.findall(r"^RESULT: (.*)$", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    zero = (bool(total) and total[-1][1] == "0") or (bool(result) and "ALL ASSERTIONS PASSED" in result[-1])
    check(zero and not fails and code == 0, "verify_rep2.py: 0 FAIL ('TOTAL:' or 'RESULT:' summary)",
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


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    m = await platform(conn, "SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE 'zz_rep5%'",
                       fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])",
                       list(SEC_NAMES.values()), fetch="fetchval")
    return check(int(n) == 0 and int(m) == 0 and int(k) == 0,
                 f"the reserved prefixes ({PREFIX}, zz_rep5_, 'REP5 VERIFY') match no real row", f"{n}/{m}/{k}")


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
        await run("pre-teardown", teardown, conn)
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
        else:
            check(False, "seed skipped: no catalog instrument class to seed the fund entry with")
        if S.get("m3"):
            await run("join", y_join, conn)
            await run("exclusions", y_exclusions, conn)
            if S.get("join_proposal_main"):
                await run("transition", y_transition, conn)
                await run("wash sale", y_wash, conn)
                await run("proposals", y_proposals, conn)
                await run("execution", y_execution, conn)
            else:
                check(False, "the join produced the transition proposal", "transition / proposals / execution skipped")
            await run("name + gitignore", y_name, conn)
            await run("isolation + gates", y_isolation, conn)
            await run("new org", y_new_org, conn)
        else:
            check(False, "seed created the fixture models", "every fixture-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        try:
            await close_pool()
        except Exception:  # noqa: BLE001
            pass

        async def after_teardown(c):
            """Every post-teardown read, as ONE unit, so a retry re-runs all of it on the same connection."""
            return (await leftovers(c), await counts(c) if pre is not None else None,
                    await real_fingerprints(c) if real is not None else None)

        try:
            log = await teardown(conn)
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
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass
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
