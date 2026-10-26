"""verify_rep6 — Replication Studio, sprint 6 (REP6): the tax engine — member
tax profiles and carryforwards, the substitution table and its denials,
household wash-sale CONTROL (replacing rep5's flag), daily loss harvesting as
proposals, the loss bank and tax-alpha ledger, declared outside trades, the
custodian-harvest switch, and the Tax screens.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep6.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the real httpx network transport is replaced by one that records
and refuses — any attempt is a failed check. Nothing reaches a custodian.

REAL: the app (middleware, Principal, every gate, every handler) through an
ASGI transport, the workflow engine (human tasks), rep2's execution flow, rep3's
model services, RLS and the live database. The script's own connection is
DATABASE_URL = app_service (rolbypassrls = false, asserted).

FIXTURES: three orgs (99000000-…-0000000e6001 / e6002 / e6003); users created
explicitly with the auth0_sub their token carries and an @test.local email
(is_test by trigger); masked account numbers only (raw ZZ6…); fixture
securities carry the reserved prefix ZZR6 and the name prefix 'REP6 VERIFY';
the test custodian code is zz_rep6_*; the household 'REP6 Verify Household'
(all checked absent from real rows first).

TEARDOWN (start and end): the operator DSN (role postgres) with
execution.teardown / models.teardown / replica.teardown = 'fixture' in its
transaction — the only path past the immutability triggers — tables in FK
child-before-parent order read from pg_constraint, every DELETE keyed by this
run's fixture ids; then every counted table is re-read for its exact pre-test
count, and real accounts / positions / lots / transactions / proposals /
instructions / tax rows / users are compared by count + checksum.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import difflib
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
from services import execution as X  # noqa: E402
from services import harvest as HV  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import loss_bank as LB  # noqa: E402
from services import models as M  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_engine as E  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import tax_profiles as TP  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import wash_sale as WS  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt, trade_custodian_key  # noqa: E402
from services.custody.trading import TradeList  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

BEFORE_REF = "f1b0eb0"   # rep6-work base — main after the rep5 merge + schema refresh
TAG = "rep6verify"
PREFIX = "ZZR6"
CAP_TEST = "zz_rep6_cust"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("S", "R1", "R2", "P", "W", "X", "V", "Y", "Q", "Z30", "Z31", "B30", "B31", "H1", "RH1", "RHX", "H2",
            "RH2", "H3A", "H3B", "H4", "H5", "H6", "K", "NS", "NC", "LBX", "LBY", "IX1", "IX2", "SC1", "SC2", "BX")
SEC_NAMES = {k: f"REP6 VERIFY {k}" for k in SEC_KEYS}
HOUSEHOLD_NAME = "REP6 Verify Household"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e6{n:03d}")


ORG_A, ORG_B, ORG_C = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_C]
(U_SUPER, U_ADMIN1, U_ADMIN2, U_ADMIN3, U_TAXVIEW, U_MEMBER, U_ADMIN_B, U_ADMIN_B2, U_NEWADMIN) = (
    _id(110 + i) for i in range(9))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP6 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP6 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP6 Admin Checker"),
    U_ADMIN3: (ORG_A, "org_admin", "admin3", "org_admin", "REP6 Admin Reviewer"),
    U_TAXVIEW: (ORG_A, "member", "taxview", "rep6_viewer", "REP6 Tax Viewer"),
    U_MEMBER: (ORG_A, "member", "member", "member", "REP6 Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP6 Org B Admin"),
    U_ADMIN_B2: (ORG_B, "org_admin", "adminb2", "org_admin", "REP6 Org B Checker"),
    U_NEWADMIN: (ORG_C, "member", "newadmin", None, "REP6 New Org Admin"),
}
TAX_PERMS = ("view_tax", "manage_tax", "approve_tax")

ENT_A, ENT_A2, ENT_SOLO, ENT_LB, ENT_B, ENT_RACE = _id(201), _id(202), _id(203), _id(204), _id(205), _id(206)
HH_A = _id(211)
(ACCT_T1, ACCT_T2, ACCT_IRA, ACCT_H, ACCT_SOLO, ACCT_CUST, ACCT_LB, ACCT_B, ACCT_RACE) = (_id(301 + i) for i in range(9))
ACCOUNTS = {   # id: (org, custodian_code, registration, tax_status, household, primary entity, raw number)
    ACCT_T1: (ORG_A, "ALTRUIST", "individual", "taxable", HH_A, ENT_A, "ZZ60000001"),
    ACCT_T2: (ORG_A, "ALTRUIST", "individual", "taxable", HH_A, ENT_A2, "ZZ60000002"),
    ACCT_IRA: (ORG_A, "ALTRUIST", "ira", "tax_deferred", HH_A, ENT_A, "ZZ60000003"),
    ACCT_H: (ORG_A, "ALTRUIST", "individual", "taxable", HH_A, ENT_A2, "ZZ60000004"),
    ACCT_SOLO: (ORG_A, "ALTRUIST", "individual", "taxable", None, ENT_SOLO, "ZZ60000005"),
    ACCT_CUST: (ORG_A, CAP_TEST.upper(), "individual", "taxable", None, ENT_SOLO, "ZZ60000006"),
    ACCT_LB: (ORG_A, "ALTRUIST", "individual", "taxable", None, ENT_LB, "ZZ60000007"),
    ACCT_B: (ORG_B, "ALTRUIST", "individual", "taxable", None, ENT_B, "ZZ60000008"),
    # fix 5: the lock-bypassed race's own account (follows m3, no model_change proposal), its own household
    ACCT_RACE: (ORG_A, "ALTRUIST", "individual", "taxable", None, ENT_RACE, "ZZ60000009"),
}
TODAY = date.today()
VAL = TODAY - timedelta(days=1)
YEAR = TODAY.year
YEAR_START = date(YEAR, 1, 1)
# the HH_A profile (adviser-entered) — combined rates computed exactly below
HH_RATES = {"federal_ordinary_rate": "0.37", "federal_lt_rate": "0.2", "niit_applies": True, "state_code": "NY",
            "state_rate": "0.0685", "city": "New York", "city_rate": "0.03876", "city_lt_rate": "0.03"}
HH_ST = Decimal("0.37") + Decimal("0.038") + Decimal("0.0685") + Decimal("0.03876")     # 0.51526
HH_LT = Decimal("0.2") + Decimal("0.038") + Decimal("0.0685") + Decimal("0.03")          # 0.3365
TRADING_COST_BPS = Decimal("10")

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.domain_event_deliveries",
    "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps", "public.workflow_runs",
    "public.workflow_run_steps", "public.entities", "public.households", "public.household_memberships",
    "public.accounts", "public.account_balances_daily", "public.org_settings", "portfolio.assets",
    "portfolio.positions", "portfolio.tax_lots", "portfolio.transactions", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "public.custodian_capabilities", "public.execution_instructions",
    "public.execution_instruction_lots", "public.execution_fills", "public.execution_instruction_events",
    "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions", "portfolio.model_events",
    "portfolio.replica_securities", "portfolio.replica_terms_versions", "public.account_model_links",
    "public.rebalance_proposals", "public.rebalance_proposal_trades", "public.account_transition_plans",
    "public.tax_profiles", "public.loss_carryforwards", "public.substitution_sets",
    "public.substitution_set_candidates", "public.substitution_denials", "public.declared_outside_trades",
    "public.loss_bank_entries", "public.harvest_runs", "public.harvest_candidates",
)
TOUCHED = ("public.tax_profiles", "public.loss_carryforwards", "public.substitution_sets",
           "public.substitution_set_candidates", "public.substitution_denials", "public.declared_outside_trades",
           "public.loss_bank_entries", "public.harvest_runs", "public.harvest_candidates",
           "public.account_model_links", "public.rebalance_proposals", "public.rebalance_proposal_trades",
           "public.accounts", "public.households", "public.household_memberships", "portfolio.positions",
           "portfolio.tax_lots", "portfolio.transactions", "public.account_balances_daily",
           "public.execution_instructions", "public.execution_instruction_lots", "public.execution_fills",
           "portfolio.replica_securities", "portfolio.replica_terms_versions", "public.custodian_capabilities",
           "public.org_settings", "public.users")
NEW_TABLES = ("public.tax_profiles", "public.loss_carryforwards", "public.substitution_sets",
              "public.substitution_set_candidates", "public.substitution_denials", "public.declared_outside_trades",
              "public.loss_bank_entries", "public.harvest_runs", "public.harvest_candidates")

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


def money(v) -> Decimal:
    return D(v).quantize(Decimal("0.01"))


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
                        ("execution_fills", "public.execution_fills"),
                        ("account_model_links", "public.account_model_links"),
                        ("rebalance_proposals", "public.rebalance_proposals"),
                        ("rebalance_proposal_trades", "public.rebalance_proposal_trades"),
                        *[(t.split(".")[1], t) for t in NEW_TABLES]):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
    out["custodian_capabilities"] = await fingerprint(
        conn, "SELECT * FROM public.custodian_capabilities WHERE custodian_code NOT LIKE 'zz_rep6%'")
    return out


async def close_conn(c, timeout: float = 30) -> None:
    """Close a connection or pool, bounded: a close that does not finish in ``timeout`` s is terminated."""
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
    """services.database's pool, closed with the same bound (its own close_pool waits forever)."""
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


async def org_tables(conn) -> list[str]:
    """Every table with an ``org_id`` column (partition children excluded)."""
    return [r["t"] for r in await conn.fetch(
        "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
        "'information_schema') ORDER BY 1")]


async def sweep_fixture_orgs(conn, orgs: list, skip: set, log: list[str]) -> None:
    """Rows under a FIXTURE org in any table the plan does not name — what a crashed run left behind and anything
    a scheduler made on its fixture accounts (rep6 fix 2). Keyed by fixture org id only; child-before-parent from
    pg_constraint (a cycle falls back to delete_tree, which follows the FKs recursively)."""
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
    """Operator connection, the teardown GUCs, FK child-before-parent, every
    DELETE keyed by this run's fixture ids (orgs, users, securities). Then a
    sweep of EVERY org_id table for rows under a fixture org the plan does not
    name (a crashed run's leftovers)."""
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
                "public.harvest_candidates": (by_org, [orgs]),
                "public.harvest_runs": (by_org, [orgs]),
                "public.loss_bank_entries": (by_org, [orgs]),
                "public.declared_outside_trades": (by_org, [orgs]),
                "public.substitution_set_candidates": (by_org, [orgs]),
                "public.substitution_sets": (by_org, [orgs]),
                "public.substitution_denials": (by_org, [orgs]),
                "public.loss_carryforwards": (by_org, [orgs]),
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
                "public.custodian_capabilities": ("custodian_code LIKE 'zz_rep6%'", []),
                "portfolio.replica_terms_versions": (by_org, [orgs]),
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
                "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH_A]]),
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
    """:func:`teardown` (one transaction, fixture ids only), retried ONCE on a deadlock or a dropped connection —
    the rolled-back attempt left nothing half-done; the retry opens its own operator connection."""
    try:
        return await teardown()
    except TEARDOWN_RETRYABLE as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; rolled back, retrying once")
        try:
            return await teardown()
        except Exception as exc2:  # noqa: BLE001
            # never wait on a second failure: report it and exit now
            check(False, "teardown retry failed", f"{type(exc2).__name__}: {exc2}")
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL", flush=True)
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(1)


async def pre_teardown(conn):
    """Start of run: everything a crashed earlier run left under the fixture orgs / ids, removed and reported."""
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
        ("SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE 'zz_rep6%'", []),
        ("SELECT count(*) FROM public.household_memberships WHERE household_id = $1", [HH_A]),
        *[(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", [orgs]) for t in (
            *NEW_TABLES, "public.account_model_links", "public.rebalance_proposals", "public.rebalance_proposal_trades",
            "public.account_transition_plans", "public.execution_instructions", "public.execution_instruction_lots",
            "public.execution_fills", "public.execution_instruction_events", "public.workflow_definitions",
            "public.workflow_runs", "public.workflow_run_steps", "public.member_todos", "public.domain_events",
            "public.audit_log", "public.accounts", "public.households", "public.entities",
            "public.account_balances_daily", "portfolio.tax_lots", "portfolio.transactions", "portfolio.positions",
            "portfolio.assets", "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
            "portfolio.replica_securities", "portfolio.replica_terms_versions", "public.org_settings")],
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    # and EVERY org_id table (the teardown's sweep), read on the operator connection — app_service cannot see
    # every schema
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


async def api_raw(uid, method, path, body=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep6"}}
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
        raise AssertionError(f"verify_rep6: a network call was attempted: {request.url}")

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


def jl(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


async def prop_row(conn, org, pid):
    return await scoped(conn, org, "SELECT * FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS NULL",
                        str(pid), fetch="fetchrow")


async def trades_rows(conn, org, pid):
    return await scoped(conn, org, "SELECT * FROM public.rebalance_proposal_trades WHERE proposal_id = $1::uuid "
                                   "AND valid_to IS NULL AND system_to IS NULL ORDER BY line_no", str(pid))


async def open_prop(conn, org, account):
    return await scoped(conn, org, "SELECT * FROM public.rebalance_proposals WHERE account_id = $1::uuid AND system_to IS "
                                   "NULL AND status IN ('draft', 'pending_approval', 'approved')", str(account),
                        fetch="fetchrow")


async def open_props(conn, org, account) -> str:
    """Every open proposal of the account (detail text for a failing check: names the culprit)."""
    rows = await scoped(conn, org, "SELECT id, trigger, status, maker_kind, created_at FROM "
                                   "public.rebalance_proposals WHERE account_id = $1::uuid AND system_to IS NULL AND "
                                   "status IN ('draft', 'pending_approval', 'approved') ORDER BY created_at", str(account))
    return "open: " + (", ".join(f"({r['id']}, {r['trigger']}, {r['status']}, {r['maker_kind']}, "
                                 f"{r['created_at'].isoformat()})" for r in rows) or "none")


async def account_row(account_id) -> dict:
    pool = await pool_()
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            return await AM.load_account(c2, ORG_A, account_id)


async def ctx_for(account_id, today=None) -> WS.Context:
    pool = await pool_()
    acct = await account_row(account_id)
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            return await WS.load_context(c2, ORG_A, acct, today or TODAY)


def by_line(trades) -> dict:
    """{(security, side, outcome): row}"""
    out = {}
    for r in trades:
        w = jl(r["wash_sale"]) or {}
        out[(r["global_security_id"], r["side"], w.get("outcome"))] = r
    return out


# ═══ Fixtures ═══════════════════════════════════════════════════════════════
def pos(row, key, w, alts=()):
    sid = S.get(f"sec_{key}") if key != "cash" else None
    return {"row_num": row, "global_security_id": str(sid) if sid else None, "raw_ticker": f"{PREFIX}{key}" if sid
            else None, "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": SEC_NAMES.get(key, "Cash"),
            "target_weight": D(w), "asset_class": None, "sleeve_label": None, "drift_min": None, "drift_max": None,
            "alternates": list(alts), "restrictions": None, "notes": None,
            "resolution_state": "resolved" if sid else "cash", "resolution_note": None}


async def make_model(name: str, positions: list[dict]) -> tuple[UUID, UUID]:
    pool = await pool_()
    with _ctx(ORG_A):
        async with pool.acquire() as conn:
            model, ref = await M.create_model(conn, org_id=ORG_A, actor_id=U_ADMIN1, name=name, source="upload")
            v = await M._create_version(conn, org_id=ORG_A, model=model, origin="manual", maker_ids=[U_ADMIN1],
                                        created_by=U_ADMIN1, positions=positions)
        await M.publish(pool, [ref])
        await M.submit(pool, org_id=ORG_A, actor_id=U_ADMIN1, model_id=model["id"], version_id=v["id"])
        await M.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, model_id=model["id"], version_id=v["id"],
                       outcome="approve")
    return UUID(str(model["id"])), UUID(str(v["id"]))


# Every literal seed() (and the later direct-SQL tests) writes into a column a CHECK, lookup table or enum
# constrains — checked against the live catalog first (rep5's first run died on an instrument class literal).
SEED_INSTRUMENT_CLASS = "index_etf"
SEED_CHECK_LITERALS = (   # (table, constraint, literals the script writes)
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.transactions", "transactions_authority_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long", "short")),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open", "closed")),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity",)),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.replica_securities", "replica_securities_status_chk", ("active",)),
    ("portfolio.replica_terms_versions", "replica_terms_versions_status_chk", ("approved",)),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("custodian_tax_loss_harvesting",)),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("assumed",)),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes",)),
    ("portfolio.models", "models_source_chk", ("upload",)),
    ("public.account_model_links", "account_model_links_engine_chk", ("hollisworks", "custodian")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("join", "harvest")),
    ("public.rebalance_proposals", "rebalance_proposals_maker_kind_chk", ("user", "system")),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_side_chk", ("buy",)),
    ("public.tax_profiles", "tax_profiles_filing_chk", ("married_filing_jointly", "married_filing_separately")),
    ("public.tax_profiles", "tax_profiles_subject_chk", ("household", "entity")),
    ("public.declared_outside_trades", "declared_outside_trades_class_chk", ("taxable", "unknown")),
    ("public.declared_outside_trades", "declared_outside_trades_side_chk", ("buy", "sell")),
    ("public.declared_outside_trades", "declared_outside_trades_loss_chk", ("yes",)),
    ("public.substitution_denials", "substitution_denials_rule_chk", ("adviser", "same_index", "share_class")),
    ("public.harvest_candidates", "harvest_candidates_reason_chk", (
        "below_threshold", "needs_price", "no_clear_replacement", "recent_household_buy", "open_proposal",
        "nothing_to_swap")),
    ("public.execution_instructions", "execution_instructions_side_chk", ("sell",)),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("member",)),
)


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog (CHECKs, lookup tables, enums) before seeding")
    S.pop("instrument_class", None)
    row = await platform(conn, "SELECT key, enabled FROM portfolio.replica_instrument_classes WHERE key = $1 "
                               "AND valid_to IS NULL AND system_to IS NULL", SEED_INSTRUMENT_CLASS, fetch="fetchrow")
    if check(row is not None and row["enabled"] is True, f"instrument class {SEED_INSTRUMENT_CLASS!r} is a current, "
             "enabled portfolio.replica_instrument_classes key"):
        S["instrument_class"] = row["key"]
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
    check(d is not None and re.fullmatch(r"[a-z][a-z0-9_]*", CAP_TEST) is not None
          and trade_custodian_key(CAP_TEST.upper()) == CAP_TEST and trade_custodian_key("ALTRUIST") == "altruist",
          "the test custodian code satisfies custodian_capabilities_code_chk and routes to its own key", f"{d}")
    find("unconstrained seed text", "accounts.registration_type / tax_status (no CHECK, FK or enum — rep6 classifies "
         "them in services.wash_sale.tax_class: 'taxable', IRA-like registration or a deferred / exempt status), "
         "portfolio.assets.asset_type, account_balances_daily.source_system and org_settings keys carry no constraint")
    check(WS.tax_class({"tax_status": "taxable", "registration_type": "individual"}) == "taxable"
          and WS.tax_class({"tax_status": "tax_deferred", "registration_type": "ira"}) == "tax_advantaged"
          and WS.tax_class({"tax_status": "UNKNOWN", "registration_type": "UNKNOWN"}) == "unknown",
          "the fixture registration / tax_status literals classify as intended (taxable, IRA, unknown sentinel)")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_C, "c")):
            # is_test: schedulers never act on a fixture org (services.orgs; the reserved-prefix trigger sets it too)
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP6 Verify Org {nm.upper()}", f"{TAG}-{nm}")
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
        rid = await rbac.ensure_role(conn, ORG_A, "rep6_viewer", "REP6 verify — view_tax (and view_proposals) only")
        for p in ("view_tax", "view_proposals"):
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid,
                               perm_ids[p])
        roles[(ORG_A, "rep6_viewer")] = rid
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
        for ent, org, nm in ((ENT_A, ORG_A, "Spouse One"), (ENT_A2, ORG_A, "Spouse Two"), (ENT_SOLO, ORG_A, "Solo"),
                             (ENT_LB, ORG_A, "Ledger"), (ENT_B, ORG_B, "Org B"), (ENT_RACE, ORG_A, "Race")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"REP6 Verify {nm}")
        await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                           HH_A, ORG_A, HOUSEHOLD_NAME, U_ADMIN1)
        for ent in (ENT_A, ENT_A2):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, "
                               "$3)", HH_A, ent, U_ADMIN1)
        for i, key in enumerate(SEC_NAMES):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type="equity", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:05d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        await conn.execute(
            "INSERT INTO public.custodian_capabilities (custodian_code, capability_key, value, evidence_kind, "
            "evidence, as_of, version_no) VALUES ($1, 'custodian_tax_loss_harvesting', 'yes', 'assumed', "
            "'REP6 verify fixture', $2, 1)", CAP_TEST, TODAY)
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, reg, tax, hh, ent, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, false)",
                aid, org, n.masked, n.hashed(salts[org]), code, reg, tax, ent, hh)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, $3::jsonb)",
                           ORG_A, HV.COST_SETTING, json.dumps(int(TRADING_COST_BPS)))
        assets = {}
        for key in SEC_KEYS:
            org = ORG_B if key == "BX" else ORG_A
            aid = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", aid, org, S[f"sec_{key}"], SEC_NAMES[key])
            assets[key] = aid
        S["assets"], S["pos"], S["lots"] = assets, {}, {}

        async def position(acct, key, qty, mv, as_of=VAL):
            org, ent = ACCOUNTS[acct][0], ACCOUNTS[acct][5]
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, org, ent, assets[key], as_of, D(qty), None if mv is None else D(mv), acct)
            S["pos"][(acct, key, as_of)] = pid
            return pid

        async def lot(name, pos_id, qty, cost, acquired, term, status="open", realized=None, closing=None):
            lid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                "original_cost, term, status, realized_gain_loss, closing_transaction_id) VALUES ($1, $2, $3, $4, $5, "
                "$6, $6, $7, $8, $9, $10)", lid, ORG_A, pos_id, acquired, D(qty), D(cost), term, status,
                None if realized is None else D(realized), closing)
            S["lots"][name] = lid
            return lid

        async def txn(pos_id, code, trade_date, qty, price):
            tid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.transactions (id, org_id, position_id, transaction_type_code, trade_date, "
                "quantity, price, authority, source_system, is_corporate_action_adjustment) VALUES ($1, $2, $3, $4, $5, "
                "$6, $7, 'manual', 'manual', false)", tid, ORG_A, pos_id, code, trade_date, D(qty), D(price))
            return tid

        S["helpers"] = (position, lot, txn)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        # ACCT_T1 — the transition account: X (a loss lot + a gain lot), V (a loss), Y (a loss)
        p = await position(ACCT_T1, "X", 30, 3000)
        await lot("LX_LOSS", p, 10, 1300, date(2020, 1, 2), "long")
        await lot("LX_GAIN", p, 20, 1000, date(2019, 1, 2), "long")
        await lot("LV", await position(ACCT_T1, "V", 10, 1000), 10, 1500, date(2021, 1, 4), "long")
        await lot("LY", await position(ACCT_T1, "Y", 10, 1000), 10, 1200, date(2021, 1, 4), "long")
        # ACCT_T2 — the spouse: loss sales of S (5 days ago), W (10) and RHX (3); a buy of H6 (10)
        for key, days, price, cost, realized, term in (("S", 5, 40, 600, -200, "short"), ("W", 10, 30, 600, -300, "long"),
                                                       ("RHX", 3, 10, 200, -100, "short")):
            p = await position(ACCT_T2, key, 0, 0)
            t = await txn(p, "sell", TODAY - timedelta(days=days), 10, price)
            await lot(f"T2_{key}", p, 10, cost, TODAY - timedelta(days=100) if term == "short" else date(2020, 1, 2),
                      term, status="closed", realized=realized, closing=t)
        await txn(await position(ACCT_T2, "H6", 0, 0), "buy", TODAY - timedelta(days=10), 5, 10)
        # ACCT_IRA — bought X 10 days ago; sold Q at a loss 5 days ago (an IRA loss: no window)
        p = await position(ACCT_IRA, "X", 10, 1000)
        await lot("IRA_X", p, 10, 500, date(2020, 1, 2), "long")
        await txn(p, "buy", TODAY - timedelta(days=10), 10, 100)
        p = await position(ACCT_IRA, "Q", 0, 0)
        t = await txn(p, "sell", TODAY - timedelta(days=5), 5, 40)
        await lot("IRA_Q", p, 5, 250, date(2022, 1, 3), "long", status="closed", realized=-50, closing=t)
        # ACCT_H — the harvest account
        await lot("LH1", await position(ACCT_H, "H1", 100, 5000), 100, 6500, TODAY - timedelta(days=100), "short")
        await lot("LH2", await position(ACCT_H, "H2", 50, 4000), 50, 5000, date(2020, 1, 2), "long")
        await lot("LH3A", await position(ACCT_H, "H3A", 10, 5000), 10, 5505, date(2021, 1, 4), "long")
        await lot("LH3B", await position(ACCT_H, "H3B", 200, 20000), 200, 20600, date(2021, 1, 4), "long")
        await lot("LH4", await position(ACCT_H, "H4", 10, None), 10, 2000, date(2021, 1, 4), "long")
        await lot("LH5", await position(ACCT_H, "H5", 10, 1000), 10, 2000, date(2021, 1, 4), "long")
        await lot("LH6", await position(ACCT_H, "H6", 10, 1000), 10, 2000, date(2021, 1, 4), "long")
        # ACCT_SOLO / ACCT_CUST — no tax profile; the custodian's own harvesting
        await lot("LNS", await position(ACCT_SOLO, "NS", 10, 1000), 10, 2000, date(2021, 1, 4), "long")
        await lot("LNC", await position(ACCT_CUST, "NC", 10, 1000), 10, 2000, date(2021, 1, 4), "long")
        # ACCT_LB — the loss bank: four closed lots this year, and an open lot a fill will sell
        px = await position(ACCT_LB, "LBX", 10, 400)
        await lot("LBF", px, 10, 600, TODAY - timedelta(days=100), "short")
        py = await position(ACCT_LB, "LBY", 0, 0)
        for name, realized, term in (("LB_STG", 1000, "short"), ("LB_STL", -5000, "short"), ("LB_LTG", 500, "long"),
                                     ("LB_LTL", -200, "long")):
            t = await txn(py, "sell", YEAR_START, 10, 100)
            await lot(name, py, 10, 1000 - realized, date(2020, 1, 2) if term == "long" else YEAR_START, term,
                      status="closed", realized=realized, closing=t)
        for acct, cash in ((ACCT_T1, 10000), (ACCT_T2, 5000), (ACCT_IRA, 10000), (ACCT_H, 1000), (ACCT_SOLO, 1000),
                           (ACCT_CUST, 1000), (ACCT_LB, 0), (ACCT_RACE, 1000)):
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", ORG_A, acct, VAL,
                D(cash))
        # rule denials' evidence: two registry funds on the same index (approved rep1 terms), two share classes
        for key, series in (("IX1", None), ("IX2", None), ("SC1", f"{PREFIX}S000001"), ("SC2", f"{PREFIX}S000001")):
            rs = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
                "holdings_source_key, status, display_name, sec_series_id, created_by) VALUES ($1, $2, $3, $4, $5, "
                "'zz_rep6_test', 'active', $6, $7, $8)", rs, ORG_A, S[f"sec_{key}"], S["instrument_class"],
                f"{PREFIX}{key}", f"Holdings of {PREFIX}{key}", series, U_ADMIN1)
            if key.startswith("IX"):
                await conn.execute(
                    "INSERT INTO portfolio.replica_terms_versions (org_id, replica_security_id, version_no, status, "
                    "term_values, maker_ids, created_by, checker_id, approved_at) VALUES ($1, $2, 1, 'approved', "
                    "$3::jsonb, ARRAY[$4::uuid], $4, $5, now())", ORG_A, rs,
                    json.dumps({"index_name": "REP6 VERIFY INDEX"}), U_ADMIN1, U_ADMIN2)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_B))
        await position(ACCT_B, "BX", 10, 1000)
    S["m1"], S["m1v1"] = await make_model("REP6 VERIFY CORE", [pos(1, "S", "0.30"), pos(2, "W", "0.30"),
                                                               pos(3, "cash", "0.40")])
    S["m2"], S["m2v1"] = await make_model("REP6 VERIFY V", [pos(1, "V", "0.50"), pos(2, "cash", "0.50")])
    S["m3"], S["m3v1"] = await make_model("REP6 VERIFY CASH", [pos(1, "K", "0.10"), pos(2, "cash", "0.90")])


async def join(acct, model, settings=None):
    """rep5's real join path: a maker requests, a checker approves → the transition proposal."""
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join",
                     {"model_id": str(model), "settings": settings or {}})
    lid = (o.get("link") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct}/model/changes/{lid}/approve", {})
    return c, c2, o2.get("transition_proposal_id"), o, o2


async def direct_rows(conn, sql, *args):
    """A fixture write the custodian sync would make (as app_service, in ORG_A's RLS context)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        return await conn.fetch(sql, *args)


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e — signatures and the chosen definitions")
    sig = {n: str(inspect.signature(f)) for n, f in (
        ("rebalance_proposals.generate", RP.generate), ("rebalance_proposals.compute", RP.compute),
        ("rebalance_proposals.household_accounts", RP.household_accounts),
        ("rebalance_proposals.realized_ytd", RP.realized_ytd), ("rebalance_proposals.org_tick", RP.org_tick),
        ("rebalance_engine.plan_sales", E.plan_sales), ("rebalance_engine.build", E.build),
        ("account_models.load_account", AM.load_account), ("account_models.active_link", AM.active_link),
        ("account_models.settings_of", AM.settings_of), ("account_models.harvesting_options", AM.harvesting_options),
        ("account_models.load_holdings", AM.load_holdings), ("execution.create_instructions", X.create_instructions),
        ("human_tasks.open_task", HT.open_task), ("human_tasks.complete_task", HT.complete_task),
        ("human_tasks.register_kind", HT.register_kind), ("wash_sale.load_context", WS.load_context),
        ("wash_sale.evaluate_buy", WS.evaluate_buy), ("wash_sale.sale_restriction", WS.sale_restriction),
        ("harvest.scan_account", HV.scan_account), ("loss_bank.ingest", LB.ingest))}
    for k, v in sig.items():
        find(f"1a {k}", v)
    check("harvest_plan" in sig["rebalance_proposals.generate"] and "upstream" in sig["execution.create_instructions"]
          and "system_open" in sig["human_tasks.register_kind"],
          "1a: generate takes the additive harvest_plan=; create_instructions upstream=; system-opened review tasks")
    src_c = inspect.getsource(RP.compute)
    check(not hasattr(E, "wash_sale_flags") and "WS.load_context" in src_c and "sale_restriction" in src_c
          and "apply_wash_control" in src_c,
          "1a: rep5's wash_sale_flags is gone; compute runs household CONTROL (context → rule b before the plan → "
          "rules a / c / d on the built trades)")
    rp_src = (API_DIR / "services" / "rebalance_proposals.py").read_text(encoding="utf-8")
    web_src = (WEB_DIR / "components" / "proposals" / "ProposalDetail.jsx").read_text(encoding="utf-8")
    check("wash_sale_flag" in rp_src and "_dumps(wash)" in rp_src and "t.wash_sale ?" in web_src,
          "1a: every reader of the flag updated — the trades insert writes `wash_sale`, _trade_line notes it, "
          "trade_out returns both, ProposalDetail renders both (a pre-rep6 flag still shows)")
    find("1b subject", "a tax profile hangs on the account's HOUSEHOLD (accounts.household_id → households — a "
         "married couple filing jointly is one profile covering both spouses' accounts) or, with no household, its "
         "primary entity (tax_profiles.subject_of); wash-sale scope is rep5's household_accounts (household, primary "
         "entity, shared account_owners) — wider than the profile's subject")
    find("1c loss bank sources", "lot_close (closed tax_lots.realized_gain_loss, keyed by lot id) | fill (rep2 fills "
         "on sells, per named lot, PROVISIONAL — closed when the custodian's lot arrives) | carryforward (approved "
         "rows) | declared_outside (declared sales with an amount); transactions carry no gain → not a source; one row "
         "per source key, closed rows included")
    find("1d pending trades", "an open instruction counts from today to its execute_by; a proposal awaiting approval "
         "or approved-not-routed counts from today to expiry + execute-within days (only its clear / replaced lines); "
         "the account's own draft / pending proposal is excluded (the new one supersedes it); drafts of others are not "
         "counted")
    tick = inspect.getsource(RP.org_tick)
    sched = (API_DIR / "workflow_scheduler_tick.py").read_text(encoding="utf-8")
    i_lb, i_hv = tick.find("LB.ingest"), tick.find("HV.scan_account")
    i_drift = tick.find("await scan_account(pool, org_id=org_id, account_id=a")
    check(0 <= i_lb < i_hv < i_drift and sched.find("maybe_refresh_holdings(pool)") < sched.find("proposals_tick(pool)"),
          "1e: the harvest scan hooks into rep5's org_tick AFTER the holdings refresh, the loss-bank ingest before it, "
          "and BEFORE the drift scan (opt-in per link; it never supersedes an open proposal)",
          f"{i_lb} {i_hv} {i_drift}")


async def y_schema(conn):
    section("[Y] Schema landed: tables, constraints, partial uniques, RLS with a policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep6.py"), "--verify"], REPO, timeout=300)
    check(code == 0 and "SCHEMA OK" in out, "_apply_rep6.py --verify: SCHEMA OK", (out + err)[-300:])
    seen = 0
    for t in TOUCHED:
        cons = await platform(conn, "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass "
                                    "AND contype IN ('c', 'u', 'f')", t)
        idx = await platform(conn, "SELECT indexname FROM pg_indexes WHERE schemaname || '.' || tablename = $1 "
                                   "AND indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%WHERE%'", t)
        seen += len(cons)
        print(f"[INFO] {t}: {len(cons)} CHECK/UNIQUE/FK, partial uniques {[r['indexname'] for r in idx]}")
    check(seen > 150, "CHECK / UNIQUE / FK constraints read for every touched table before seeding", f"{seen}")
    pols = await platform(conn, "SELECT tablename, array_agg(DISTINCT cmd) AS c FROM pg_policies WHERE schemaname = "
                                "'public' AND tablename = ANY($1::text[]) GROUP BY tablename",
                          [t.split(".")[1] for t in NEW_TABLES])
    have = {r["tablename"]: set(r["c"]) for r in pols}
    check(len(have) == len(NEW_TABLES) and all({"SELECT", "INSERT", "UPDATE", "DELETE"} <= v for v in have.values()),
          "every new table has a policy for SELECT, INSERT, UPDATE and DELETE", f"{have}")


async def y_profiles(conn):
    section("[Y] Profiles and carryforwards: maker-checker; exact combined rates; no profile → not estimated")
    body = {"subject_type": "household", "subject_id": str(HH_A), "tax_year": YEAR,
            "filing_status": "married_filing_jointly", **HH_RATES, "as_of": (TODAY - timedelta(days=2)).isoformat(),
            "source_note": "REP6 verify — the adviser's planning memo"}
    cv, ov = await api(U_TAXVIEW, "POST", "/api/v1/tax/profiles", body)
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", body)
    pid = (o.get("profile") or {}).get("id")
    check(cv == 403 and "manage_tax" in str(ov) and c == 201 and (o.get("profile") or {}).get("status") ==
          "pending_approval", "enter: a view_tax holder is refused naming manage_tax; an org admin's identical "
          "request is accepted (awaiting approval)", f"{cv} {c} {str(o)[:160]}")
    cf_, of_ = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", {**body, "federal_ordinary_rate": 0.37})
    cm, om = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", {**body, "tax_year": YEAR + 1,
                                                                  "federal_lt_rate": None})
    check(cf_ == 422 and cm == 422, "a JSON float is refused (422); a missing rate is refused — never assumed",
          f"{cf_} {cm} {str(om)[:120]}")
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/tax/profiles/{pid}/approve", {})
    st1 = await scoped(conn, ORG_A, "SELECT status FROM public.tax_profiles WHERE id = $1 AND system_to IS NULL", UUID(pid),
                       fetch="fetchval")
    cv2, ov2 = await api(U_TAXVIEW, "POST", f"/api/v1/tax/profiles/{pid}/approve", {})
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/tax/profiles/{pid}/approve", {})
    row = await scoped(conn, ORG_A, "SELECT * FROM public.tax_profiles WHERE id = $1 AND system_to IS NULL", UUID(pid),
                       fetch="fetchrow")
    check(c1 == 403 and st1 == "pending_approval" and cv2 == 403 and "approve_tax" in str(ov2) and c2 == 200
          and row["status"] == "approved" and row["checker_id"] == U_ADMIN2 and row["entered_by"] == U_ADMIN1,
          "approve: its maker is refused (403, unchanged); a viewer refused naming approve_tax; a second person's "
          "identical request approves it", f"{c1} {st1} {cv2} {c2} {row['status']}")
    try:
        await scoped(conn, ORG_A, "UPDATE public.tax_profiles SET checker_id = entered_by WHERE id = $1", UUID(pid),
                     fetch="execute")
        check(False, "the database refuses a checker equal to the maker")
    except (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError) as exc:
        check(True, "the database refuses a profile whose checker is its maker (CHECK / guard)", type(exc).__name__)
    r = TP.combined_rates(dict(row))
    S["hh_profile"] = row
    check(r is not None and r["st"] == HH_ST and r["lt"] == HH_LT and HH_ST == Decimal("0.51526")
          and HH_LT == Decimal("0.3365") and r["parts"]["state_lt_source"].startswith("ordinary")
          and r["parts"]["city_lt"] == "0.030000",
          "combined rates EXACT: ST = 37% + NIIT 3.8% + state 6.85% + city 3.876% = 51.526%; LT = 20% + 3.8% + "
          "state 6.85% (LT as ordinary — none entered) + city LT 3% = 33.65%", f"{r and (r['st'], r['lt'])}")
    nc = await platform(conn, "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND "
                              "table_name = 'tax_profiles' AND column_name ILIKE '%combined%'", fetch="fetchval")
    check(int(nc) == 0, "combined rates are computed, never stored (no combined column)")
    # no profile → not estimated, never a default rate
    with _ctx(ORG_A):
        pool = await pool_()
        async with pool.acquire() as c3:
            none = await TP.profile_for_account(c3, ORG_A, await AM.load_account(c3, ORG_A, ACCT_SOLO), YEAR)
    cp, op = await api(U_ADMIN1, "GET", f"/api/v1/tax/accounts/{ACCT_SOLO}")
    rates = ((op.get("panel") or {}).get("rates") or {})
    check(none is None and TP.combined_rates(None) is None and cp == 200 and rates.get("estimated") is False
          and rates.get("label") == TP.NO_PROFILE and "st" not in rates and "lt" not in rates,
          "no approved profile → the panel's rates read 'not estimated — no tax profile' with no number at all",
          f"{cp} {rates}")
    # a carryforward into this year for the ledger member, approved the same way
    cfb = {"subject_type": "entity", "subject_id": str(ENT_LB), "tax_year": YEAR, "st_amount": "0",
           "lt_amount": "1000", "source_document": "REP6 verify — prior-year Schedule D, line 14"}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/carryforwards", cfb)
    cid = (o.get("carryforward") or {}).get("id")
    c1, _ = await api(U_ADMIN1, "POST", f"/api/v1/tax/carryforwards/{cid}/approve", {})
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/carryforwards/{cid}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.loss_carryforwards WHERE id = $1 AND system_to IS NULL",
                      UUID(cid) if cid else uuid.uuid4(), fetch="fetchval")
    S["cf_id"] = cid
    check(c == 201 and c1 == 403 and c2 == 200 and st == "approved",
          "carryforward: entered from the prior return; its maker refused, a second person approves", f"{c} {c1} {c2}")


async def y_substitutions(conn):
    section("[Y] Substitutions: approved-only, maker-checker, denials override, rule denials seeded")
    cands = [{"replacement_security_id": str(S["sec_R1"]), "reason": "same industry, different issuer",
              "evidence": "different index provider"},
             {"replacement_security_id": str(S["sec_R2"]), "reason": "same sector", "evidence": "another provider"}]
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/sets",
                     {"global_security_id": str(S["sec_S"]), "candidates": cands})
    sid = (o.get("set") or {}).get("id")
    with _ctx(ORG_A):
        pool = await pool_()
        async with pool.acquire() as c3:
            before = await WS.approved_substitutes(c3, ORG_A)
    c1, _ = await api(U_ADMIN1, "POST", f"/api/v1/tax/substitutions/sets/{sid}/approve", {})
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/substitutions/sets/{sid}/approve", {})
    with _ctx(ORG_A):
        async with pool.acquire() as c3:
            after = await WS.approved_substitutes(c3, ORG_A)
    check(c == 201 and str(S["sec_S"]) not in before and c1 == 403 and c2 == 200
          and [x["key"] for x in after.get(str(S["sec_S"]), [])] == [str(S["sec_R1"]), str(S["sec_R2"])],
          "a set is proposed (pending — NOT used by the engine), its maker refused, a second person approves → used "
          "in rank order", f"{c} {c1} {c2}")
    S["set_s_v1"] = sid
    # the harvest account's sets: H1 → [RHX (has a household loss window), RH1]; H2 → [RH2]
    for key, reps in (("H1", ("RHX", "RH1")), ("H2", ("RH2",))):
        c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/sets", {
            "global_security_id": str(S[f"sec_{key}"]),
            "candidates": [{"replacement_security_id": str(S[f"sec_{r}"]), "reason": "similar exposure",
                            "evidence": "different index provider"} for r in reps]})
        c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/substitutions/sets/{(o.get('set') or {}).get('id')}/approve",
                          {})
        check(c == 201 and c2 == 200, f"the substitution set for {key} approved by a second person", f"{c} {c2}")
    # an adviser's denial: S and P are substantially identical
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/denials", {
        "security_a_id": str(S["sec_S"]), "security_b_id": str(S["sec_P"]), "reason": "the same index, two wrappers",
        "evidence": "both prospectuses"})
    did = (o.get("denial") or {}).get("id")
    c1, _ = await api(U_ADMIN1, "POST", f"/api/v1/tax/substitutions/denials/{did}/approve", {})
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/substitutions/denials/{did}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.substitution_denials WHERE id = $1 AND system_to IS NULL",
                      UUID(did) if did else uuid.uuid4(), fetch="fetchval")
    check(c == 201 and c1 == 403 and c2 == 200 and st == "active", "an adviser's denial: maker refused, a second person "
          "approves → active", f"{c} {c1} {c2} {st}")
    # a denial overrides a set at proposal time
    cx, ox = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/sets", {
        "global_security_id": str(S["sec_P"]), "candidates": [{"replacement_security_id": str(S["sec_S"]),
                                                               "reason": "x", "evidence": "y"}]})
    cs, _ = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/sets", {
        "global_security_id": str(S["sec_P"]), "candidates": [{"replacement_security_id": str(S["sec_P"]),
                                                               "reason": "x", "evidence": "y"}]})
    check(cx == 422 and "denial" in str(ox) and cs == 422, "a set naming a denied pair is refused (a denial overrides "
          "a set); a security is never its own substitute", f"{cx} {cs}")
    # rule denials: same index (approved rep1 terms, both in the registry) and share classes (one SEC series)
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/denials/seed", {})
    seeded = o.get("seeded") or []
    pairs = {(x["rule"], frozenset((x["a"], x["b"]))) for x in seeded}
    c2, o2 = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/denials/seed", {})
    check(c == 200 and ("same_index", frozenset((str(S["sec_IX1"]), str(S["sec_IX2"])))) in pairs
          and ("share_class", frozenset((str(S["sec_SC1"]), str(S["sec_SC2"])))) in pairs and c2 == 200
          and (o2.get("seeded") or []) == [], "rule denials seeded: the two funds on one index, the two share classes "
          "of one series — active at once; seeding again adds nothing", f"{len(seeded)} {len(o2.get('seeded') or [])}")
    rows = await scoped(conn, ORG_A, "SELECT rule, status, maker_kind FROM public.substitution_denials WHERE "
                                     "rule <> 'adviser' AND valid_to IS NULL AND system_to IS NULL")
    check(bool(rows) and all(r["status"] == "active" and r["maker_kind"] == "system" for r in rows),
          "seeded denials are the system's (maker_kind system) and active")
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.substitution_denials (org_id, security_a_id, security_b_id, rule, "
                                  "reason, evidence, status, maker_kind) VALUES ($1, $2, $3, 'adviser', 'x', 'y', "
                                  "'active', 'system')", ORG_A, *sorted([S["sec_Q"], S["sec_K"]], key=str),
                     fetch="execute")
        check(False, "the database refuses an adviser denial with no maker")
    except asyncpg.CheckViolationError as exc:
        check(exc.constraint_name == "substitution_denials_maker_chk", "the database refuses an adviser denial with no "
              "person behind it (substitution_denials_maker_chk)", exc.constraint_name)


async def declare(sub_type, sub_id, key, side, days_ago, loss=None, cls="taxable", amount=None, term=None):
    body = {"subject_type": sub_type, "subject_id": str(sub_id), "account_description": "REP6 verify outside account",
            "account_tax_class": cls, "global_security_id": str(S[f"sec_{key}"]), "side": side,
            "trade_date": (TODAY - timedelta(days=days_ago)).isoformat(), "source_note": "REP6 verify — declared"}
    if side == "sell":
        body["loss"] = loss or "yes"
        if amount is not None:
            body["realized_amount"], body["term"] = amount, term
    return await api(U_ADMIN1, "POST", "/api/v1/tax/declared-trades", body)


async def y_wash_rules(conn):
    section("[Y] Wash-sale rules on the real household context: both window edges, denied pairs, declared outside "
            "trades, IRA hard block, IRA losses, household boundary")
    # declared outside trades of the household: loss sales 30 / 31 days ago, buys 30 / 31 days ago, a buy of Y 5 days ago
    codes = []
    for key, side, days in (("Z30", "sell", 30), ("Z31", "sell", 31), ("B30", "buy", 30), ("B31", "buy", 31),
                            ("Y", "buy", 5)):
        c, _ = await declare("household", HH_A, key, side, days)
        codes.append(c)
    cf, of = await api(U_ADMIN1, "POST", "/api/v1/tax/declared-trades", {
        "subject_type": "household", "subject_id": str(HH_A), "account_description": "x", "global_security_id":
        str(S["sec_Z30"]), "side": "buy", "trade_date": (TODAY + timedelta(days=1)).isoformat(), "source_note": "x"})
    check(codes == [201] * 5 and cf == 422, "declared outside trades recorded (a future date refused — a declared "
          "trade has happened)", f"{codes} {cf}")
    ctx = await ctx_for(ACCT_T1)
    r30 = WS.evaluate_buy(str(S["sec_Z30"]), account_class="taxable", ctx=ctx)
    r31 = WS.evaluate_buy(str(S["sec_Z31"]), account_class="taxable", ctx=ctx)
    check(r30["outcome"] == "deferred" and r30["until"] == (TODAY + timedelta(days=1)).isoformat()
          and any(m["source"] == "declared_outside" for m in r30["matches"]) and r31["outcome"] == "clear",
          "a BUY 30 days after a household loss sale is NOT clear (deferred to the exact first clear day, day 31); "
          "31 days after is clear — the loss sale a DECLARED outside trade", f"{r30['outcome']} {r30.get('until')} "
          f"{r31['outcome']}")
    s30 = WS.sale_restriction(str(S["sec_B30"]), account_class="taxable", ctx=ctx)
    s31 = WS.sale_restriction(str(S["sec_B31"]), account_class="taxable", ctx=ctx)
    check(s30 is not None and s30["until"] == TODAY + timedelta(days=1) and s31 is None,
          "a LOSS SALE 30 days after a household buy is NOT clear (its loss lots wait for day 31); 31 days after is "
          "clear", f"{s30 and s30['until']} {s31}")
    # forward edges (a planned loss sale reserves its next 30 days; rule d) — pure, the same functions
    fwd = WS.Context(TODAY, [WS.Event("F30", "sell", TODAY + timedelta(days=30), TODAY + timedelta(days=30), True, None,
                                      "x", "taxable", "proposal"),
                             WS.Event("F31", "sell", TODAY + timedelta(days=31), TODAY + timedelta(days=31), True, None,
                                      "x", "taxable", "proposal")], {}, {}, {})
    check(WS.evaluate_buy("F30", account_class="taxable", ctx=fwd)["outcome"] == "deferred"
          and WS.evaluate_buy("F31", account_class="taxable", ctx=fwd)["outcome"] == "clear",
          "forward edges: a buy 30 days BEFORE a planned loss sale is not clear; 31 days before is clear")
    # denied pairs count, approved substitutes do not
    rp = WS.evaluate_buy(str(S["sec_P"]), account_class="taxable", ctx=ctx)
    rr = WS.evaluate_buy(str(S["sec_R1"]), account_class="taxable", ctx=ctx)
    check(rp["outcome"] == "deferred" and any("denied pair" in m["identical_because"] for m in rp["matches"])
          and rr["outcome"] == "clear",
          "a buy of P after the spouse's loss sale of S is deferred — S and P are a DENIED pair; a buy of R1 (an "
          "approved substitute of S) is clear — a substitute is not identical", f"{rp['outcome']} {rr['outcome']}")
    # rule a: replaced by the next approved substitute that is itself clear
    rs = WS.evaluate_buy(str(S["sec_S"]), account_class="taxable", ctx=ctx)
    check(rs["outcome"] == "replaced" and rs["replacement"]["security_id"] == str(S["sec_R1"])
          and any(m["account_id"] == str(ACCT_T2) and m["side"] == "sell" for m in rs["matches"]),
          "a buy of S within 30 days after the SPOUSE's loss sale (another account of the household) → replaced by the "
          "approved substitute R1, the match named", f"{rs['outcome']}")
    # rule c: an IRA buy inside a taxable loss window → BLOCKED, in plain words
    rw = WS.evaluate_buy(str(S["sec_W"]), account_class="tax_advantaged", ctx=ctx)
    check(rw["outcome"] == "blocked" and "permanently forfeit" in rw["reason"] and "Rev. Rul. 2008-5" in rw["reason"]
          and "IRA" in rw["reason"], "an IRA buy inside a taxable loss window (no clear substitute) is BLOCKED: 'would "
          "permanently forfeit the household's loss … (Rev. Rul. 2008-5)'", rw["reason"][:140])
    # a loss realized INSIDE the IRA opens no window
    rq = WS.evaluate_buy(str(S["sec_Q"]), account_class="taxable", ctx=ctx)
    check(rq["outcome"] == "clear" and any(e.key == str(S["sec_Q"]) and e.tax_class == "tax_advantaged"
                                           for e in ctx.events),
          "the IRA's loss sale of Q (5 days ago) is SEEN but opens no window — an IRA loss is not deductible")
    # the household boundary: another member's household never sees HH_A's trades
    other = await ctx_for(ACCT_SOLO)
    check(WS.evaluate_buy(str(S["sec_S"]), account_class="taxable", ctx=other)["outcome"] == "clear"
          and not any(e.account_id == str(ACCT_T2) for e in other.events),
          "another household (a different member, no shared account) sees none of HH_A's trades")


async def y_wash_proposals(conn):
    section("[Y] Wash-sale control on real proposals: replaced / deferred / other lots / blocked; pending proposals "
            "and instructions count; a blocked or deferred line never reaches an instruction")
    pool = await pool_()
    c, c2, pid, o, o2 = await join(ACCT_T1, S["m1"])
    S["p_t1"] = pid
    trades = await trades_rows(conn, ORG_A, pid) if pid else []
    b = by_line(trades)
    S["t1_trades"] = trades
    rs = b.get((S["sec_R1"], "buy", "replaced"))
    check(c == 201 and c2 == 200 and rs is not None and (jl(rs["wash_sale"]) or {}).get("original", {}).get(
        "security_id") == str(S["sec_S"]) and (S["sec_S"], "buy", "clear") not in b,
          "rule a: the model's buy of S → a buy of R1 (the approved substitute) for the same dollars; S itself is not "
          "bought", f"{c} {c2} {[(str(k[0])[-4:], k[1], k[2]) for k in b]}")
    rw = b.get((S["sec_W"], "buy", "deferred"))
    check(rw is not None and (jl(rw["wash_sale"]) or {}).get("until") == (TODAY + timedelta(days=21)).isoformat(),
          "rule a: the buy of W (the spouse sold it at a loss 10 days ago; no substitute) → DEFERRED to the exact "
          "first clear day (+21)", f"{(jl(rw['wash_sale']) or {}).get('until') if rw else None}")
    xs = b.get((S["sec_X"], "sell", "clear"))
    xd = b.get((S["sec_X"], "sell", "deferred"))
    wxd = jl(xd["wash_sale"]) if xd else {}
    check(xs is not None and [x["lot_id"] for x in jl(xs["lots"])] == [str(S["lots"]["LX_GAIN"])]
          and xd is not None and [x["lot_id"] for x in jl(xd["lots"])] == [str(S["lots"]["LX_LOSS"])]
          and wxd.get("until") == (TODAY + timedelta(days=21)).isoformat() and wxd.get("forfeit") is True
          and "PERMANENTLY" in wxd.get("reason", "") and "IRA" in wxd.get("reason", ""),
          "rule b: the loss sale of X after the household IRA bought X 10 days ago → its LOSS lot deferred to day +21 "
          "(the reason says the IRA purchase would forfeit it permanently); the GAIN lot is sold now instead (stated)",
          f"{wxd.get('until')} {wxd.get('forfeit')}")
    yd = b.get((S["sec_Y"], "sell", "deferred"))
    check(yd is not None and (jl(yd["wash_sale"]) or {}).get("until") == (TODAY + timedelta(days=26)).isoformat()
          and any(m["source"] == "declared_outside" for m in (jl(yd["wash_sale"]) or {}).get("matches", [])),
          "a DECLARED outside buy of Y (5 days ago) holds Y's loss sale to day +26")
    vs = b.get((S["sec_V"], "sell", "clear"))
    check(vs is not None and D(vs["est_lt_gain"]) == D(-500), "the loss sale of V (no household buy) goes ahead, clear")
    check(bool(trades) and all(r["wash_sale_flag"] is None and jl(r["wash_sale"]) is not None for r in trades),
          "rep5 proposals now carry a wash-sale OUTCOME on every line — never the old flag")
    # rule c on a real IRA proposal
    c, c2, pira, _, _ = await join(ACCT_IRA, S["m1"])
    S["p_ira"] = pira
    bi = by_line(await trades_rows(conn, ORG_A, pira) if pira else [])
    wb = bi.get((S["sec_W"], "buy", "blocked"))
    check(c2 == 200 and (S["sec_R1"], "buy", "replaced") in bi and wb is not None
          and "permanently forfeit" in (jl(wb["wash_sale"]) or {}).get("reason", ""),
          "rule c: the IRA's buy of W inside the spouse's taxable loss window → BLOCKED (plain words); its buy of S → "
          "replaced by R1", f"{[(str(k[0])[-4:], k[1], k[2]) for k in bi]}")
    # rule d: T1's pending proposal sells V at a loss → the spouse's later proposal to BUY V is deferred
    t1 = await prop_row(conn, ORG_A, pid)
    end = max(t1["expires_at"].date(), TODAY) + timedelta(days=RP.DEFAULT_EXECUTE_WITHIN)
    c, c2, pt2, _, _ = await join(ACCT_T2, S["m2"])
    S["p_t2"] = pt2
    bt2 = by_line(await trades_rows(conn, ORG_A, pt2) if pt2 else [])
    vd = bt2.get((S["sec_V"], "buy", "deferred"))
    wv = jl(vd["wash_sale"]) if vd else {}
    check(c2 == 200 and t1["status"] == "pending_approval" and vd is not None
          and wv.get("until") == (end + timedelta(days=31)).isoformat()
          and any(m["source"] == "proposal" and m["ref"] == str(pid) for m in wv.get("matches", [])),
          "rule d: a PENDING proposal's planned loss sale of V reserves its forward 30 days — the spouse's later buy of "
          "V is deferred to 31 days after the last day the pending sale could happen", f"{wv.get('until')} {end}")
    # approve T1 → only clear / replaced lines become instructions
    ca, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {})
    p = await prop_row(conn, ORG_A, pid)
    lines = await trades_rows(conn, ORG_A, pid)
    routable = [r for r in lines if (jl(r["wash_sale"]) or {}).get("outcome") in ("clear", "replaced")]
    held = [r for r in lines if (jl(r["wash_sale"]) or {}).get("outcome") in ("deferred", "blocked")]
    ids = (jl(p["execution"]) or {}).get("instruction_ids") or []
    check(ca == 200 and p["status"] == "approved" and len(ids) == len(routable) > 0 and len(held) == 3
          and all(r["instruction_id"] is not None for r in routable) and all(r["instruction_id"] is None for r in held)
          and (jl(p["execution"]) or {}).get("held_by_wash_sale") == 3,
          "approved: every clear / replaced line has its instruction; the 3 deferred lines have NONE (recorded on the "
          "proposal, never routed)", f"{ca} {len(ids)} {len(routable)} {len(held)}")
    ins = await scoped(conn, ORG_A, "SELECT * FROM public.execution_instructions WHERE id = ANY($1::uuid[]) AND "
                                    "system_to IS NULL", ids)
    r1i = next((i for i in ins if i["global_security_id"] == S["sec_R1"]), None)
    check(r1i is not None and (r1i["wash_sale_notes"] or "").startswith("[wash-sale replaced]"),
          "the replaced buy's instruction carries the wash-sale result in its notes")
    try:
        await scoped(conn, ORG_A, "INSERT INTO public.rebalance_proposal_trades (org_id, proposal_id, line_no, "
                                  "global_security_id, side, notional, price_source, reason, wash_sale, instruction_id) "
                                  "VALUES ($1, $2, 99, $3, 'buy', 100, 'none', 'x', '{\"outcome\": \"deferred\"}'::jsonb, "
                                  "$4)", ORG_A, UUID(pid), S["sec_W"], ins[0]["id"], fetch="execute")
        check(False, "the database refuses a deferred line linked to an instruction")
    except asyncpg.CheckViolationError as exc:
        check(exc.constraint_name == "rebalance_proposal_trades_wash_route_chk", "the database refuses a deferred / "
              "blocked line linked to an instruction (rebalance_proposal_trades_wash_route_chk)", exc.constraint_name)
    # routed → its open instruction counts now (from today to its execute-by)
    vi = next((i for i in ins if i["global_security_id"] == S["sec_V"]), None)
    ctx2 = await ctx_for(ACCT_T2)
    r = WS.evaluate_buy(str(S["sec_V"]), account_class="taxable", ctx=ctx2)
    check(vi is not None and r["outcome"] == "deferred" and r["until"] == (max(vi["execute_by"], TODAY)
                                                                           + timedelta(days=31)).isoformat()
          and any(m["source"] == "instruction" for m in r["matches"])
          and not any(m["source"] == "proposal" and m["ref"] == str(pid) for m in r["matches"]),
          "once routed, the OPEN INSTRUCTION's loss sale of V counts (to its execute-by + 31), not the proposal",
          f"{r.get('until')}")
    ci, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pira}/approve", {})
    pi = await prop_row(conn, ORG_A, pira)
    li = await trades_rows(conn, ORG_A, pira)
    blocked = [x for x in li if (jl(x["wash_sale"]) or {}).get("outcome") == "blocked"]
    check(ci == 200 and bool(blocked) and all(x["instruction_id"] is None for x in blocked)
          and len((jl(pi["execution"]) or {}).get("instruction_ids") or []) == len(li) - len(blocked),
          "the IRA's BLOCKED line never reaches an instruction", f"{ci}")
    # the API publishes the outcome and the matches on the trade (the screen renders them)
    cd, od = await api(U_TAXVIEW, "GET", f"/api/v1/proposals/{pid}")
    tr = (od.get("proposal") or {}).get("trades") or []
    voc = od.get("vocabularies") or {}
    check(cd == 200 and bool(tr) and all(t.get("wash_sale") for t in tr) and any(t.get("routable") is False for t in tr)
          and {x["key"] for x in voc.get("wash_sale_outcomes") or []} == set(WS.OUTCOMES),
          "GET /proposals/{id}: every trade has its wash_sale result (routable false on the held ones); outcome "
          "labels in the envelope's vocabulary", f"{cd}")


async def cands_of(conn, run_id) -> dict:
    rows = await scoped(conn, ORG_A, "SELECT * FROM public.harvest_candidates WHERE run_id = $1::uuid AND valid_to IS "
                                     "NULL AND system_to IS NULL", UUID(str(run_id)))
    return {(r["global_security_id"], r["kind"]): r for r in rows}


async def y_harvest(conn):
    section("[Y] Harvesting: thresholds net of trading cost, exact estimated values, unpriced skipped, replacement via "
            "wash-sale control, one human-approved proposal per account per day, every candidate recorded, custodian "
            "engine, swap back")
    pool = await pool_()
    # OPT-IN: a link joined without harvest_enabled is never harvested — T1 holds loss lots (V, Y, X)
    t1_link = await scoped(conn, ORG_A, "SELECT harvest_enabled, harvesting_engine FROM public.account_model_links "
                                        "WHERE account_id = $1 AND status = 'active' AND valid_to IS NULL AND system_to "
                                        "IS NULL", ACCT_T1, fetch="fetchrow")
    n_hp0 = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                      "trigger = 'harvest'", ACCT_T1, fetch="fetchval")
    co, oo = await api(U_ADMIN1, "POST", f"/api/v1/harvest/accounts/{ACCT_T1}/scan", {})
    n_run = await scoped(conn, ORG_A, "SELECT count(*) FROM public.harvest_runs WHERE account_id = $1", ACCT_T1,
                         fetch="fetchval")
    n_hp = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                     "trigger = 'harvest'", ACCT_T1, fetch="fetchval")
    check(t1_link is not None and t1_link["harvest_enabled"] is False and t1_link["harvesting_engine"] == "hollisworks"
          and co == 200 and (oo.get("scan") or {}).get("action") == "none"
          and "off" in ((oo.get("scan") or {}).get("reason") or "") and int(n_run) == 0 and int(n_hp) == int(n_hp0) == 0,
          "harvesting is OFF by default: a link joined without harvest_enabled (engine 'hollisworks', loss lots held) "
          "is not scanned — no run, no harvest proposal", f"{dict(t1_link) if t1_link else None} {co} {oo} {n_run} {n_hp}")
    c, c2, ptr, o, _ = await join(ACCT_H, S["m3"], {"harvest_enabled": True, "harvest_loss_pct": "0.05",
                                                    "harvest_loss_min": "500", "harvest_swap_back": True})
    cr, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{ptr}/reject", {"note": "REP6 verify — not now"})
    link = (o.get("link") or {}).get("settings") or {}
    check(c == 201 and c2 == 200 and cr == 200 and link.get("harvest_enabled") is True
          and link.get("harvest_loss_pct") == "0.0500000000"
          and link.get("harvest_loss_min") == "500.00" and link.get("harvest_swap_back") is True,
          "the account's harvest settings (opted in, 5%, $500, swap back) are versioned link settings, approved with "
          "the join",
          f"{c} {c2} {cr} {link}")
    # a harvest never supersedes an open proposal (proven on SOLO below), so the fixture RESOLVES every open
    # proposal on H first: the join's transition (rejected above) and anything else open on the account, each
    # rejected with a note. A proposal that is not the join's is recorded — the live scheduler once made drift
    # proposals on these fixture accounts (fixed: schedulers skip test orgs, services.orgs)
    stray = await scoped(conn, ORG_A, "SELECT id, trigger, maker_kind, status FROM public.rebalance_proposals WHERE "
                                      "account_id = $1 AND system_to IS NULL AND status = ANY($2::text[]) AND id <> "
                                      "$3::uuid", ACCT_H, list(RP.OPEN_STATUSES), str(ptr or uuid.uuid4()))
    for sp in stray:
        if sp["status"] == "pending_approval":
            await api(U_ADMIN2, "POST", f"/api/v1/proposals/{sp['id']}/reject", {"note": "REP6 verify — not now"})
    if stray:
        find("open proposals on H besides the join's",
             f"{[(str(x['id']), x['trigger'], x['maker_kind'], x['status']) for x in stray]} — each pending one "
             "rejected with a note before the scan")
    n_open = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                       "system_to IS NULL AND status = ANY($2::text[])", ACCT_H,
                          list(RP.OPEN_STATUSES), fetch="fetchval")
    check(int(n_open) == 0, "before the scan, H has NO open proposal (the join's transition rejected with a note)",
          f"{n_open} open")
    cs, os_ = await api(U_ADMIN1, "POST", f"/api/v1/harvest/accounts/{ACCT_H}/scan", {})
    scan = os_.get("scan") or {}
    run = await scoped(conn, ORG_A, "SELECT * FROM public.harvest_runs WHERE account_id = $1 AND run_date = $2 AND "
                                    "valid_to IS NULL AND system_to IS NULL", ACCT_H, TODAY, fetch="fetchrow")
    cd = await cands_of(conn, run["id"]) if run else {}
    S["h_run"] = run
    rates = jl(run["rates"]) if run else {}
    check(cs == 200 and scan.get("action") == "harvest" and run is not None and run["outcome"] == "proposed"
          and rates.get("st") == str(HH_ST.quantize(Decimal("0.000001"))) and rates.get("label") == "estimate",
          "the scan made ONE harvest proposal and recorded the run with the approved profile's combined rates",
          f"{cs} {scan} {rates.get('st')}")
    h1, h2 = cd.get((S["sec_H1"], "harvest")), cd.get((S["sec_H2"], "harvest"))
    ok1 = h1 is not None and h1["outcome"] == "proposed" and D(h1["unrealized"]) == D(-1500) and D(
        h1["trading_cost"]) == D(10) and D(h1["net_loss"]) == D(1490) and D(h1["loss_pct"]) == D("0.2292307692") \
        and h1["term"] == "short" and D(h1["rate_used"]) == HH_ST and D(h1["est_tax_value"]) == money(1500 * HH_ST) \
        and D(h1["est_tax_value"]) == D("772.89") and "estimate" in h1["value_note"]
    ok2 = h2 is not None and h2["outcome"] == "proposed" and D(h2["net_loss"]) == D(992) and h2["term"] == "long" \
        and D(h2["est_tax_value"]) == D("336.50") and D(h2["rate_used"]) == HH_LT
    check(bool(ok1 and ok2), "values EXACT: H1 short-term loss 1,500 − 10 trading cost (5,000 × 10 bps × 2) = 1,490 net "
          "(22.92% of cost) → est. 1,500 × 51.526% = 772.89; H2 long-term 1,000 → est. 1,000 × 33.65% = 336.50 — "
          "each labelled an estimate",
          f"{h1 and (h1['net_loss'], h1['loss_pct'], h1['est_tax_value'])} {h2 and (h2['net_loss'], h2['est_tax_value'])}")
    a, b_ = cd.get((S["sec_H3A"], "harvest")), cd.get((S["sec_H3B"], "harvest"))
    check(a is not None and a["skip_reason"] == "below_threshold" and a["passes_pct"] is True
          and a["passes_amount"] is False and D(a["unrealized"]) == D(-505) and D(a["net_loss"]) == D(495)
          and b_ is not None and b_["skip_reason"] == "below_threshold" and b_["passes_amount"] is True
          and b_["passes_pct"] is False and D(b_["loss_pct"]) == D("0.0271844660"),
          "thresholds exact and BOTH required: H3A's gross 505 ≥ $500 but 495 net of trading cost → skipped; H3B $560 "
          "net but 2.72% < 5% → skipped", f"{a and (a['net_loss'], a['passes_pct'], a['passes_amount'])} "
          f"{b_ and (b_['loss_pct'], b_['passes_pct'])}")
    h4, h5, h6 = (cd.get((S[f"sec_{k}"], "harvest")) for k in ("H4", "H5", "H6"))
    check(h4 is not None and h4["skip_reason"] == "needs_price" and h4["price"] is None
          and h4["est_tax_value"] is None, "an unpriced holding is skipped: 'needs a price' (never a guessed price)")
    check(h5 is not None and h5["skip_reason"] == "no_clear_replacement" and h6 is not None
          and h6["skip_reason"] == "recent_household_buy" and (jl(h6["wash_sale"]) or {}).get("until") ==
          (TODAY + timedelta(days=21)).isoformat(),
          "no approved substitute → skipped with the reason; the spouse's buy of H6 10 days ago holds its harvest "
          "(wash sale, clear from +21)")
    tried = (jl(h1["wash_sale"]) or {}).get("tried") if h1 else []
    check(h1 is not None and h1["replacement_security_id"] == S["sec_RH1"]
          and any(t["security_id"] == str(S["sec_RHX"]) and "loss window" in t["why_not"] for t in tried or []),
          "replacement chosen through wash-sale control: RHX (rank 1) has the spouse's loss sale 3 days ago → skipped; "
          "RH1 (rank 2) is clear → chosen", f"{tried}")
    n_c = await scoped(conn, ORG_A, "SELECT count(*) FROM public.harvest_candidates WHERE run_id = $1", run["id"],
                       fetch="fetchval") if run else None
    check(run is not None and int(n_c) == run["candidates"] == 7 and run["taken"] == 2,
          "every candidate recorded — taken (2) or skipped with its reason (5)",
          f"{n_c} {run and (run['candidates'], run['taken'])}")
    S["p_harvest"] = run["proposal_id"] if run else None
    if S["p_harvest"] is None:
        # never cascade: every check below that needs the proposal is reported as not run, the independent ones run
        check(False, "the harvest proposal exists — its review task, lines, approval and swap back are NOT checked",
              f"{scan}")
        await y_harvest_guards(conn)
        return
    hp = await prop_row(conn, ORG_A, run["proposal_id"])
    lines = await trades_rows(conn, ORG_A, run["proposal_id"])
    bl = by_line(lines)
    task = await scoped(conn, ORG_A, "SELECT r.context->'human_task'->>'kind' FROM workflow_run_steps rs JOIN "
                                     "workflow_runs r ON r.id = rs.workflow_run_id WHERE rs.id = $1",
                        hp["approval_task_id"], fetch="fetchval")
    check(hp["trigger"] == "harvest" and hp["maker_kind"] == "system" and hp["status"] == "pending_approval"
          and task == "rebalance_proposal_review" and (jl(hp["trigger_ref"]) or {}).get("run_id") == str(run["id"])
          and (S["sec_H1"], "sell", "clear") in bl and (S["sec_RH1"], "buy", "clear") in bl
          and (S["sec_H2"], "sell", "clear") in bl and (S["sec_RH2"], "buy", "clear") in bl and len(lines) == 4,
          "the proposal: trigger 'harvest', the SYSTEM's, on a review task — sell the loss lots, buy the approved "
          "replacements", f"{hp['trigger']} {hp['status']} {task} {len(lines)}")
    rb = bl.get((S["sec_RH1"], "buy", "clear"))
    check(rb is not None and rb["notional"] is not None and D(rb["notional"]) == D(5000)
          and rb["price_source"] == "none", "the replacement has no price → bought by amount (the sale's 5,000), never "
          "a guessed quantity")
    cs2, os2 = await api(U_ADMIN1, "POST", f"/api/v1/harvest/accounts/{ACCT_H}/scan", {})
    nr = await scoped(conn, ORG_A, "SELECT count(*) FROM public.harvest_runs WHERE account_id = $1 AND run_date = $2",
                      ACCT_H, TODAY, fetch="fetchval")
    nh = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND trigger = "
                                   "'harvest' AND archived_from_id IS NULL AND trigger_ref->>'run_id' = $2", ACCT_H,
                      str(run["id"]), fetch="fetchval")
    check(cs2 == 200 and (os2.get("scan") or {}).get("reason") == "already scanned today" and int(nr) == 1
          and int(nh) == 1, "ONE proposal per account per day: a second scan today does nothing", f"{os2} {nr} {nh}")
    ca, _ = await api(U_ADMIN3, "POST", f"/api/v1/proposals/{run['proposal_id']}/approve", {})
    hp2 = await prop_row(conn, ORG_A, run["proposal_id"])
    check(ca == 200 and hp2["status"] == "approved" and hp2["checker_id"] == U_ADMIN3,
          "a PERSON approves the harvest (approve_proposals) — no trade placed; its instructions go to rep2",
          f"{ca} {hp2['status']}")
    await y_harvest_guards(conn)
    await y_harvest_swap_back(conn, run, hp2, h1)


async def y_harvest_guards(conn):
    """Independent of the H harvest proposal: an open person-made proposal skips the scan; no profile → not
    estimated; the custodian engine → no harvest proposal."""
    # no profile → candidates listed, value not estimated
    # ...and a harvest NEVER supersedes an open proposal: SOLO's person-made transition proposal is still pending
    c, c2, ps, _, o2 = await join(ACCT_SOLO, S["m3"], {"harvest_enabled": True})
    ps_before = await prop_row(conn, ORG_A, ps) if ps else None
    # the join's own transition proposal is made under the account lock like every other — it is not a model_change
    # duplicate, so nothing refuses or supersedes it
    check(c == 201 and c2 == 200 and ps_before is not None and ps_before["trigger"] == "join"
          and ps_before["maker_kind"] == "user" and ps_before["maker_id"] == U_ADMIN1
          and ps_before["status"] == "pending_approval" and not o2.get("transition_proposal_error"),
          "SOLO's join → its PERSON-made transition proposal (trigger join, the joiner as maker), awaiting approval",
          f"{c} {c2} {o2.get('transition_proposal_error')} {await open_props(conn, ORG_A, ACCT_SOLO)}")
    # the submit (draft → pending_approval) already archived a version; only archives written AFTER this read count
    read_at = await conn.fetchval("SELECT clock_timestamp()")
    await api(U_ADMIN1, "POST", f"/api/v1/harvest/accounts/{ACCT_SOLO}/scan", {})
    rs = await scoped(conn, ORG_A, "SELECT * FROM public.harvest_runs WHERE account_id = $1 AND run_date = $2",
                      ACCT_SOLO, TODAY, fetch="fetchrow")
    ps_after = await prop_row(conn, ORG_A, ps) if ps else None
    n_arch = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE archived_from_id = $1 "
                                       "AND GREATEST(system_from, created_at, system_to) > $2",
                          UUID(str(ps)) if ps else uuid.uuid4(), read_at, fetch="fetchval")
    n_hs = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                     "trigger = 'harvest'", ACCT_SOLO, fetch="fetchval")
    check(ps_before is not None and ps_before["maker_kind"] == "user" and ps_before["status"] == "pending_approval"
          and rs is not None and rs["outcome"] == "skipped" and rs["reason"] == "an open proposal exists"
          and rs["proposal_id"] is None and int(n_hs) == 0 and ps_after is not None
          and ps_after["status"] == "pending_approval" and ps_after["superseded_by_id"] is None
          and ps_after["system_from"] == ps_before["system_from"] and int(n_arch) == 0,
          "an open PERSON-made proposal → the harvest is skipped ('an open proposal exists'), no harvest proposal, and "
          "the person's proposal is untouched (same live row, still awaiting approval, never superseded)",
          f"{rs and (rs['outcome'], rs['reason'])} {ps_after and ps_after['status']} {n_arch} {n_hs} "
          f"{await open_props(conn, ORG_A, ACCT_SOLO)}")
    await api(U_ADMIN2, "POST", f"/api/v1/proposals/{ps}/reject", {"note": "REP6 verify — not now"})
    ns = (await cands_of(conn, rs["id"])).get((S["sec_NS"], "harvest")) if rs else None
    check(ns is not None and ns["est_tax_value"] is None and ns["value_note"] == TP.NO_PROFILE and ns["rate_used"] is None
          and (jl(rs["rates"]) or {}).get("estimated") is False,
          "no tax profile → the candidate is listed with 'not estimated — no tax profile' (no rate, no value)")
    # the custodian's own harvesting → no harvest proposals; wash-sale control still on its own proposals
    c, c2, pc, _, _ = await join(ACCT_CUST, S["m3"], {"harvesting_engine": "custodian", "harvest_enabled": True})
    await api(U_ADMIN1, "POST", f"/api/v1/harvest/accounts/{ACCT_CUST}/scan", {})
    rc = await scoped(conn, ORG_A, "SELECT * FROM public.harvest_runs WHERE account_id = $1 AND run_date = $2",
                      ACCT_CUST, TODAY, fetch="fetchrow")
    nhc = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND trigger = "
                                    "'harvest'", ACCT_CUST, fetch="fetchval")
    lc = await trades_rows(conn, ORG_A, pc) if pc else []
    check(c2 == 200 and rc is not None and rc["engine"] == "custodian" and rc["outcome"] == "skipped"
          and rc["proposal_id"] is None and int(nhc) == 0 and "custodian" in rc["reason"] and bool(lc)
          and all(jl(x["wash_sale"]) is not None for x in lc),
          "harvesting_engine 'custodian' → no harvest proposal (its loss lot NC notwithstanding); its own transition "
          "proposal still carries wash-sale results", f"{rc and (rc['engine'], rc['outcome'])} {nhc}")


async def y_concurrency(conn):
    """fix 4: two generators for one account + model version at once (the direct after_approval call vs a
    scheduler's event sweep, as in verify_rep5's 17 FAILs) → exactly one proposal and one review task."""
    section("[Y] fix 4 — concurrent generate for one account + model version: one proposal, one review task")
    pool = await pool_()
    version = S["m3v1"]

    async def state(acct, since):
        rows = await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposals WHERE account_id = $1 AND trigger = "
                                         "'model_change' AND model_version_id = $2 AND system_to IS NULL", acct, version)
        tasks = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_runs r WHERE r.org_id = $1 AND r.started_at >= "
                                          "$2 AND r.context->'human_task'->>'kind' = 'rebalance_proposal_review' AND "
                                          "r.context->'human_task'->>'subject_id' IN (SELECT id::text FROM "
                                          "public.rebalance_proposals WHERE account_id = $3)", ORG_A, since, acct,
                             fetch="fetchval")
        opens = await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                          "system_to IS NULL AND status IN ('draft', 'pending_approval', 'approved')",
                             acct, fetch="fetchval")
        return rows, int(tasks), int(opens)

    def gen(fn, acct):
        return fn(pool, org_id=ORG_A, account_id=acct, trigger="model_change", maker_kind="system", maker_id=None,
                  auto_submit=False, trigger_ref={"model_version_id": str(version), "verify": "fix4"}, withdrawal=None,
                  today=None, harvest_plan=None)

    # 1. through generate (the account lock): CUST follows m3 live and still has its person-made join proposal open
    before = await open_prop(conn, ORG_A, ACCT_CUST)
    pre_rows, _, _ = await state(ACCT_CUST, datetime.now(timezone.utc))
    t0 = await conn.fetchval("SELECT now()")
    with _ctx(ORG_A):
        res = await asyncio.gather(gen(RP.generate, ACCT_CUST), gen(RP.generate, ACCT_CUST), return_exceptions=True)
    rows, tasks, opens = await state(ACCT_CUST, t0)
    made = [r for r in res if isinstance(r, dict)]
    refused = [r for r in res if isinstance(r, BaseException)]
    check(not pre_rows and len(made) == 1 and len(refused) == 1 and isinstance(refused[0], RP.AlreadyProposed),
          "two generate calls at once (asyncio.gather): one proposal, the other 'already proposed' (AlreadyProposed)",
          f"{[type(r).__name__ for r in res]} {refused[0] if refused else ''}")
    check(len(rows) == 1 and rows[0]["status"] == "pending_approval" and rows[0]["maker_kind"] == "system"
          and tasks == 1 and opens == 1,
          "exactly ONE model_change proposal for the account + version, awaiting approval on ONE review task; one open "
          "proposal for the account", f"{len(rows)} rows, {tasks} review task(s), {opens} open")
    old = await prop_row(conn, ORG_A, before["id"]) if before else None
    check(before is not None and old is not None and old["status"] == "superseded"
          and rows and old["superseded_by_id"] == rows[0]["id"],
          "rep5 kept: the newer system proposal superseded the account's older open one (successor named)",
          f"{old and (old['status'], old['superseded_by_id'])}")
    with _ctx(ORG_A):
        again = await RP.on_model_version_approved(pool, org_id=ORG_A, model_id=S["m3"], version_id=version)
    mine = [x for x in again if x.get("account_id") == str(ACCT_CUST)]
    rows2, tasks2, _ = await state(ACCT_CUST, t0)
    check(len(mine) == 1 and mine[0].get("skipped") == "already proposed" and len(rows2) == 1 and tasks2 == 1,
          "the event sweep after it → 'already proposed', nothing new", f"{mine}")
    # fix 5: the sweep never proposes a version to an account that joined ON it. Before the fix this very sweep made
    # SOLO (joined m3 on this version, its join's transition rejected) a model_change proposal — a proposal from a
    # rejection's aftermath, and the reason the lock-bypassed race below once found SOLO already proposed
    solo = [x for x in again if x.get("account_id") == str(ACCT_SOLO)]
    solo_rows, _, _ = await state(ACCT_SOLO, t0)
    check(len(solo) == 1 and solo[0].get("skipped") in ("joined on this version", "a proposal on this version exists")
          and "proposal_id" not in solo[0] and not solo_rows,
          "the event sweep proposes NOTHING to an account that joined on the version (SOLO: its rejected join stays "
          "the last word — no model_change proposal)", f"{solo} {len(solo_rows)} {await open_props(conn, ORG_A, ACCT_SOLO)}")
    # 2. the database backstop alone: the same race with the account lock BYPASSED (RP._generate) still leaves one.
    # Its own fresh account (fix 5): RACE follows m3 live (joined, the join's transition rejected), with no
    # model_change proposal for the version and no open proposal — SOLO is not reused, so nothing an earlier section
    # did to it decides this check
    c, c2, pr, _, _ = await join(ACCT_RACE, S["m3"])
    cr, _ = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pr}/reject", {"note": "REP6 verify — race fixture"})
    pre_rows, _, pre_open = await state(ACCT_RACE, datetime.now(timezone.utc))
    check(c == 201 and c2 == 200 and cr == 200 and not pre_rows and pre_open == 0,
          "the race's own account follows m3 live with no model_change proposal for the version and nothing open",
          f"{c} {c2} {cr} {len(pre_rows)} {await open_props(conn, ORG_A, ACCT_RACE)}")
    t1 = await conn.fetchval("SELECT now()")
    with _ctx(ORG_A):
        res = await asyncio.gather(gen(RP._generate, ACCT_RACE), gen(RP._generate, ACCT_RACE), return_exceptions=True)
    rows, tasks, opens = await state(ACCT_RACE, t1)
    refused = [r for r in res if isinstance(r, BaseException)]
    names = {getattr(r, "constraint_name", None) or type(r).__name__ for r in refused}
    check(sum(isinstance(r, dict) for r in res) == 1 and len(refused) == 1
          and names <= {"AlreadyProposed", "rebalance_proposals_open_uq"} and len(rows) == 1 and tasks == 1
          and opens == 1,
          "WITHOUT the lock the database still allows one: the second insert is refused (AlreadyProposed from "
          "rebalance_proposals_model_change_uq, or the open-proposal index) — one proposal, one review task",
          f"{[type(r).__name__ for r in res]} {names} {len(rows)} rows, {tasks} task(s) "
          f"{await open_props(conn, ORG_A, ACCT_RACE)}")
    idx = await platform(conn, "SELECT pg_get_indexdef(indexrelid) FROM pg_index i JOIN pg_class c ON c.oid = "
                               "i.indexrelid WHERE c.relname = 'rebalance_proposals_model_change_uq' AND i.indisunique",
                         fetch="fetchval")
    check(idx is not None and "(account_id, model_version_id)" in idx and "model_change" in idx,
          "rebalance_proposals_model_change_uq: unique (account_id, model_version_id) over live model_change rows",
          f"{idx}")


async def y_harvest_swap_back(conn, run, hp2, h1):
    pool = await pool_()
    # swap back: the approved harvest is done (its instructions cancelled, the proposal executed), the replacement held
    ids = (jl(hp2["execution"]) or {}).get("instruction_ids") or []
    for iid in ids:
        await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/cancel", {"reason": "REP6 verify — swap test"})
    await api(U_ADMIN1, "POST", f"/api/v1/proposals/{run['proposal_id']}/refresh", {})
    ex = await prop_row(conn, ORG_A, run["proposal_id"])
    position, lot, txn = S["helpers"]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        await position(ACCT_H, "H1", 0, 0, as_of=TODAY)
        await position(ACCT_H, "H2", 0, 0, as_of=TODAY)
        await lot("LRH1", await position(ACCT_H, "RH1", 100, 5100, as_of=TODAY), 100, 5000, TODAY, "short")
        ph = S["pos"][(ACCT_H, "H1", TODAY)]
        t = await txn(ph, "sell", TODAY, 100, 50)
        await lot("LH1_CLOSED", ph, 100, 6500, TODAY - timedelta(days=100), "short", status="closed", realized=-1500,
                  closing=t)
    with _ctx(ORG_A):
        d30 = await HV.scan_account(pool, org_id=ORG_A, account_id=ACCT_H, today=TODAY + timedelta(days=30))
        d31 = await HV.scan_account(pool, org_id=ORG_A, account_id=ACCT_H, today=TODAY + timedelta(days=31))
    c30 = await cands_of(conn, d30["run_id"]) if d30.get("run_id") else {}
    c31 = await cands_of(conn, d31["run_id"]) if d31.get("run_id") else {}
    sb = c31.get((S["sec_RH1"], "swap_back"))
    check(ex["status"] == "executed" and not any(k[1] == "swap_back" for k in c30) and d30.get("action") == "none"
          and sb is not None and sb["outcome"] == "proposed" and sb["replacement_security_id"] == S["sec_H1"]
          and sb["original_candidate_id"] == h1["id"] and d31.get("action") == "harvest",
          "swap back: on day 30 after the harvest nothing is proposed; on day 31 a harvest proposal sells the "
          "replacement RH1 and buys the original H1 back (clear by then)",
          f"{ex['status']} {d30.get('action')} {d31.get('action')} {sb and sb['outcome']}")
    sl = by_line(await trades_rows(conn, ORG_A, d31["proposal_id"])) if d31.get("proposal_id") else {}
    nts = c31.get((S["sec_RH2"], "swap_back"))
    check((S["sec_RH1"], "sell", "clear") in sl and (S["sec_H1"], "buy", "clear") in sl and nts is not None
          and nts["skip_reason"] == "nothing_to_swap",
          "the swap-back lines: sell RH1 (through plan_sales — gain budget and short-term guard) and buy H1; RH2 is no "
          "longer held → 'nothing to swap'")


async def lb_entries(conn, live_only=True):
    return await scoped(conn, ORG_A, "SELECT * FROM public.loss_bank_entries WHERE subject_type = 'entity' AND "
                                     "subject_id = $1 AND system_to IS NULL" + (" AND valid_to IS NULL" if live_only
                                                                                 else ""), ENT_LB)


async def ledger(uid=U_ADMIN1):
    c, o = await api(uid, "GET", f"/api/v1/tax/loss-bank?subject_type=entity&subject_id={ENT_LB}&tax_year={YEAR}")
    return c, (o.get("ledger") or {}), o


async def y_loss_bank(conn):
    section("[Y] Loss bank: idempotent sources (a fill superseded by the custodian's lot), IRS netting order, the "
            "$3,000 / $1,500 limit, carryforward character, estimates labelled")
    pool = await pool_()
    lbf = str(S["lots"]["LBF"])
    tl = TradeList(account_id=ACCT_LB, trades=[{"global_security_id": str(S["sec_LBX"]), "side": "sell", "quantity": "10",
                                                "lot_instructions": [{"tax_lot_id": lbf, "quantity": "10"}]}],
                   executor_type="member", execute_by=TODAY + timedelta(days=3))
    with _ctx(ORG_A):
        async with pool.acquire() as c3:
            acct = await X.load_account(c3, ORG_A, ACCT_LB)
        res = await TR.route_trade_list(pool, org_id=ORG_A, actor_id=U_ADMIN1, account=acct, trade_list=tl)
        iid = res.instruction_ids[0]
        await X.submit(pool, org_id=ORG_A, instruction_id=iid, actor_id=U_ADMIN1)
        await X.decide(pool, org_id=ORG_A, instruction_id=iid, actor_id=U_ADMIN2, outcome="approve")
        await X.mark_sent(pool, org_id=ORG_A, instruction_id=iid, actor_id=U_ADMIN1, channel="email")
        await X.enter_fill(pool, org_id=ORG_A, instruction_id=iid, actor_id=U_ADMIN1, quantity=D(10), price=D(40),
                           filled_at=datetime.now(timezone.utc) - timedelta(minutes=1), lots=[(lbf, D(10))])
    cd, _ = await declare("entity", ENT_LB, "LBY", "sell", (TODAY - YEAR_START).days, loss="yes", amount="-300",
                          term="short")
    c1, o1 = await api(U_ADMIN1, "POST", "/api/v1/tax/loss-bank/refresh", {})
    rows = await lb_entries(conn)
    src = sorted((r["source"], r["character"], str(money(r["amount"]))) for r in rows)
    want = sorted([("lot_close", "short", "1000.00"), ("lot_close", "short", "-5000.00"), ("lot_close", "long", "500.00"),
                   ("lot_close", "long", "-200.00"), ("fill", "short", "-200.00"), ("carryforward", "long", "-1000.00"),
                   ("declared_outside", "short", "-300.00")])
    check(cd == 201 and c1 == 200 and src == want,
          "every source in, once: 4 closed lots (the custodian's numbers), the fill (10 × (40 − 60) = −200, provisional), "
          "the approved LT carryforward (−1,000, its character kept), the declared outside loss (−300)", f"{src}")
    c, lg, _ = await ledger()
    t, n = lg.get("totals") or {}, lg.get("netting") or {}
    check(c == 200 and (t.get("st_gains"), t.get("st_losses"), t.get("lt_gains"), t.get("lt_losses"), t.get("cf_lt")) ==
          ("1000.00", "5500.00", "500.00", "200.00", "1000.00")
          and (n.get("net_st"), n.get("net_lt"), n.get("cross_netted")) == ("-4500.00", "-700.00", "0.00")
          and (n.get("ordinary_limit"), n.get("ordinary_offset"), n.get("ordinary_offset_from_st")) ==
          ("3000.00", "3000.00", "3000.00")
          and (lg.get("carryforward_out") or {}).get("st") == "1500.00"
          and (lg.get("carryforward_out") or {}).get("lt") == "700.00",
          "IRS order exact: ST 1,000 − 5,500 = −4,500; LT 500 − 200 − 1,000 carried in = −700; no cross-net (both "
          "losses); $3,000 against ordinary income, taken from the ST loss first → carry ST 1,500 and LT 700 (character "
          "kept)", f"{t} {n}")
    tv = lg.get("tax_value") or {}
    check(tv.get("estimated") is False and tv.get("label") == TP.NO_PROFILE and "realized_losses_value" not in tv
          and lg.get("after_tax_performance") == TP.NEEDS_PRICES,
          "no profile → the tax value reads 'not estimated — no tax profile'; after-tax performance 'not estimated — "
          "needs prices'")
    body = {"subject_type": "entity", "subject_id": str(ENT_LB), "tax_year": YEAR,
            "filing_status": "married_filing_separately", "federal_ordinary_rate": "0.35", "federal_lt_rate": "0.15",
            "niit_applies": False, "as_of": TODAY.isoformat(), "source_note": "REP6 verify — MFS member"}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", body)
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/profiles/{(o.get('profile') or {}).get('id')}/approve", {})
    c, lg, _ = await ledger()
    n, tv = lg.get("netting") or {}, lg.get("tax_value") or {}
    check(c2 == 200 and (n.get("ordinary_limit"), n.get("ordinary_offset"), n.get("carry_st"), n.get("carry_lt")) ==
          ("1500.00", "1500.00", "3000.00", "700.00"),
          "married filing separately: the limit is $1,500 → carry ST 3,000 and LT 700", f"{n}")
    check(tv.get("estimated") is True and tv.get("label") == "estimate" and tv.get("realized_losses_value") == "1955.00"
          and (tv.get("rates") or {}).get("as_of") == TODAY.isoformat() and "DEFERRAL" in (tv.get("note") or ""),
          "the tax value is labelled 'estimate' with the rates and their as-of: 5,500 × 35% + 200 × 15% = 1,955.00; the "
          "benefit's decay stated (a deferral)", f"{tv}")
    c3, o3 = await api(U_ADMIN1, "POST", "/api/v1/tax/loss-bank/refresh", {})
    added = (o3.get("ingest") or {}).get("added") or {}
    rows2 = await lb_entries(conn)
    check(c3 == 200 and all(v == 0 for v in added.values()) and len(rows2) == len(rows),
          "idempotent: a re-run adds nothing", f"{added} {len(rows)} → {len(rows2)}")
    # the custodian's closed lot arrives for the filled lot → it replaces the provisional fill
    position, lot, txn = S["helpers"]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        t = await txn(S["pos"][(ACCT_LB, "LBX", VAL)], "sell", TODAY, 10, 39)
        upd = await conn.fetch("UPDATE portfolio.tax_lots SET status = 'closed', realized_gain_loss = -210, "
                               "closing_transaction_id = $2 WHERE id = $1 RETURNING id", S["lots"]["LBF"], t)
    c4, o4 = await api(U_ADMIN1, "POST", "/api/v1/tax/loss-bank/refresh", {})
    all_rows = await lb_entries(conn, live_only=False)
    fill = [r for r in all_rows if r["source"] == "fill"]
    lc = [r for r in all_rows if r["source"] == "lot_close" and r["tax_lot_id"] == S["lots"]["LBF"]]
    check(len(upd) == 1 and c4 == 200 and len(fill) == 1 and fill[0]["valid_to"] is not None
          and "replaces this provisional fill" in (fill[0]["superseded_reason"] or "") and len(lc) == 1
          and D(lc[0]["amount"]) == D(-210),
          "the custodian's closed lot (−210) REPLACES the provisional fill (−200): the fill entry is closed with the "
          "reason — never both counted", f"{len(upd)} {[(r['valid_to'] is not None) for r in fill]}")
    c, lg, _ = await ledger()
    check((lg.get("totals") or {}).get("st_losses") == "5510.00" and (lg.get("netting") or {}).get("carry_st") ==
          "3010.00", "the ledger now counts −210, once (ST losses 5,510; carry ST 3,010)",
          f"{(lg.get('totals') or {}).get('st_losses')}")
    c5, o5 = await api(U_ADMIN1, "POST", "/api/v1/tax/loss-bank/refresh", {})
    check(all(v == 0 for v in ((o5.get("ingest") or {}).get("added") or {}).values())
          and len(await lb_entries(conn, live_only=False)) == len(all_rows),
          "a re-run after the supersession adds nothing (the closed fill row keeps its key)")
    # cross-netting both ways (pure — the same function the ledger uses)
    a = LB.net(st_gains=D(4000), st_losses=D(1000), lt_gains=D(0), lt_losses=D(7000))
    b = LB.net(st_gains=D(0), st_losses=D(6000), lt_gains=D(2000), lt_losses=D(0))
    check((a["cross_netted"], a["st_after_cross"], a["lt_after_cross"], a["ordinary_offset"], a["carry_st"],
           a["carry_lt"]) == (D(3000), D(0), D(-4000), D(3000), D(0), D(1000))
          and (b["cross_netted"], b["st_after_cross"], b["ordinary_offset"], b["carry_st"], b["carry_lt"]) ==
          (D(2000), D(-4000), D(3000), D(1000), D(0)),
          "cross-netting: a net ST gain absorbs a net LT loss (and the reverse); the remainder carries with its own "
          "character", f"{a['carry_lt']} {b['carry_st']}")


async def y_substitutions_late(conn):
    section("[Y] Substitutions: every version kept, the newest approved one used; a later denial overrides a set in the "
            "engine; retiring a denial needs a second person")
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/sets", {
        "global_security_id": str(S["sec_S"]), "candidates": [{"replacement_security_id": str(S["sec_R2"]),
                                                               "reason": "a different provider", "evidence": "factsheet"}]})
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/substitutions/sets/{(o.get('set') or {}).get('id')}/approve", {})
    v1 = await scoped(conn, ORG_A, "SELECT status, valid_to FROM public.substitution_sets WHERE id = $1 AND system_to IS "
                                   "NULL", UUID(S["set_s_v1"]), fetch="fetchrow")
    cl, ol = await api(U_TAXVIEW, "GET", "/api/v1/tax/substitutions")
    vers = sorted(s["version_no"] for s in ol.get("sets") or [] if s["global_security_id"] == str(S["sec_S"]))
    ctx = await ctx_for(ACCT_T1)
    r = WS.evaluate_buy(str(S["sec_S"]), account_class="taxable", ctx=ctx)
    check(c == 201 and c2 == 200 and v1["status"] == "superseded" and v1["valid_to"] is not None and vers == [1, 2]
          and r["outcome"] == "replaced" and r["replacement"]["security_id"] == str(S["sec_R2"])
          and r["replacement"]["set_version"] == 2,
          "v2 approved: v1 kept (superseded, valid_to set), both versions listed; the engine uses v2 only", f"{vers}")
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/denials", {
        "security_a_id": str(S["sec_R2"]), "security_b_id": str(S["sec_S"]), "reason": "found to track one index",
        "evidence": "index methodology"})
    did = (o.get("denial") or {}).get("id")
    await api(U_ADMIN2, "POST", f"/api/v1/tax/substitutions/denials/{did}/approve", {})
    ctx = await ctx_for(ACCT_T1)
    r = WS.evaluate_buy(str(S["sec_S"]), account_class="taxable", ctx=ctx)
    check(r["outcome"] == "deferred" and any(t["security_id"] == str(S["sec_R2"]) and "denial" in t["why_not"]
                                            for t in r.get("tried") or []),
          "a denial approved AFTER the set overrides it: R2 is skipped ('a denial overrides the substitution set') and "
          "the buy defers", f"{r['outcome']} {r.get('tried')}")
    cm, _ = await api(U_ADMIN1, "POST", f"/api/v1/tax/substitutions/denials/{did}/retire", {"reason": "verify"})
    cc, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/substitutions/denials/{did}/retire", {"reason": "verify"})
    r2 = WS.evaluate_buy(str(S["sec_S"]), account_class="taxable", ctx=await ctx_for(ACCT_T1))
    check(cm == 403 and cc == 200 and r2["outcome"] == "replaced", "retiring the denial: its maker refused; a second "
          "person retires it → the set applies again", f"{cm} {cc} {r2['outcome']}")


async def y_rep5_diff(conn):
    section("[Y] rep5: proposals carry wash-sale outcomes; verify_rep5.py changed ONLY where it asserted the flag")
    old = git_show(BEFORE_REF, "apps/api/scripts/verify_rep5.py").splitlines()
    new = (HERE / "verify_rep5.py").read_text(encoding="utf-8").splitlines()

    def fn_at(lines, i):
        for k in range(min(i, len(lines) - 1), -1, -1):
            m = re.match(r"(?:async )?def (\w+)", lines[k])
            if m:
                return m.group(1)
        return None

    def ok(fn, text):
        s = text.strip()
        if fn == "y_wash":
            return True
        if fn == "seed" and ("pix" in s or s.startswith("#")):
            return True
        # fix-5: diagnostics only — the drift / calm / maker-refused checks print the account's open proposals
        # (open_props) in their detail text; no assertion changed
        if fn in ("open_prop", "open_props") or "open_props" in s:
            return True
        if fn == "y_proposals" and (s.startswith('f"{drift}') or s.startswith('f"{calm}')
                                    or s.startswith('check(calm.get("action") == "none" and cp is None,')):
            return True
        if fn == "y_execution" and ("prop_row(conn, ORG_A, pid)" in s or s == 'st = pr["status"]'
                                    or s.startswith('f"{c1} {st}')):
            return True
        if fn == "y_execution":
            return s.startswith("#") or any(w in s for w in ("wash", "routed", "fl_rows", "flagged", 'startswith("[")'))
        # fix-2: the teardown retry (TEARDOWN_RETRYABLE sits after teardown, so fn_at names it teardown) — and the
        # two call sites in main_async that switched to it
        if fn in ("teardown", "teardown_retrying"):
            return True
        if fn == "main_async":
            return bool(re.search(r"\bteardown(_retrying)?\b", s))
        # fix-2: the fixture orgs are is_test, so the schedulers never act on them
        if fn == "seed":
            return "is_test" in s or "INSERT INTO organizations (id, name, slug) VALUES" in s
        return False

    bad, changed = [], 0
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            changed += 1
            if not ok(fn_at(old, i), old[i]):
                bad.append(f"-{i + 1} {fn_at(old, i)}: {old[i].strip()[:60]}")
        for j in range(j1, j2):
            changed += 1
            if not ok(fn_at(new, j), new[j]):
                bad.append(f"+{j + 1} {fn_at(new, j)}: {new[j].strip()[:60]}")
    check(bool(old) and changed > 0 and not bad, "every changed line of verify_rep5.py is in y_wash (the flag checks, "
          "restated as outcomes), the IRA X buy fixture (10 → 31 days), y_execution's 'flags never block' check, or "
          "fix-2's teardown retry / is_test fixture orgs",
          f"{changed} changed; {bad[:4]}")
    find("verify_rep5 lines changed", "seed: the IRA's X buy moved from 10 to 31 days ago (a 10-day buy is now control "
         "and would hold the transition's X loss lot — the transition arithmetic checks depend on it; rule b is proven "
         "here instead); y_wash: the four flag checks restated as control outcomes (the IRA loss → clear, day 31 → "
         "clear, 45 days → clear, a gain → clear) plus 'no flag is written'; y_execution: 'flags never block' → 'every "
         "clear / replaced line has its instruction'")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways on identical requests; "
            "envelopes; tax data never visible without view_tax")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    cb, ob = await api(U_ADMIN_B, "POST", "/api/v1/tax/profiles", {
        "subject_type": "entity", "subject_id": str(ENT_B), "tax_year": YEAR, "filing_status": "married_filing_jointly",
        "federal_ordinary_rate": "0.32", "federal_lt_rate": "0.15", "niit_applies": False, "as_of": TODAY.isoformat(),
        "source_note": "REP6 verify — org B"})
    a_in_a = await scoped(conn, ORG_A, "SELECT count(*) FROM public.tax_profiles WHERE org_id = $1", ORG_A,
                          fetch="fetchval")
    b_in_a = await scoped(conn, ORG_A, "SELECT count(*) FROM public.tax_profiles WHERE org_id = $1", ORG_B,
                          fetch="fetchval")
    b_in_b = await scoped(conn, ORG_B, "SELECT count(*) FROM public.tax_profiles WHERE org_id = $1", ORG_B,
                          fetch="fetchval")
    a_in_b = await scoped(conn, ORG_B, "SELECT count(*) FROM public.tax_profiles WHERE org_id = $1", ORG_A,
                          fetch="fetchval")
    others = {}
    for t in ("harvest_runs", "harvest_candidates", "loss_bank_entries", "substitution_sets", "substitution_denials",
              "declared_outside_trades", "loss_carryforwards"):
        others[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM public.{t}", fetch="fetchval")),
                     int(await scoped(conn, ORG_B, f"SELECT count(*) FROM public.{t} WHERE org_id = $1", ORG_A,
                                      fetch="fetchval")))
    check(cb == 201 and int(a_in_a) > 0 and int(b_in_b) > 0 and int(b_in_a) == 0 and int(a_in_b) == 0
          and all(x[0] > 0 and x[1] == 0 for x in others.values()),
          "RLS: each org sees its own tax rows (non-empty) and none of the other's — profiles, runs, candidates, the "
          "ledger, sets, denials, declared trades, carryforwards", f"{a_in_a} {b_in_b} {b_in_a} {a_in_b} {others}")
    run_id = S["h_run"]["id"] if S.get("h_run") else uuid.uuid4()
    codes = [
        (await api(U_ADMIN_B, "GET", f"/api/v1/tax/accounts/{ACCT_T1}"))[0],
        (await api(U_ADMIN1, "GET", f"/api/v1/tax/accounts/{ACCT_B}"))[0],
        (await api(U_ADMIN_B, "GET", f"/api/v1/harvest/runs/{run_id}"))[0],
        (await api(U_ADMIN_B, "POST", f"/api/v1/tax/profiles/{S['hh_profile']['id']}/approve", {}))[0],
        (await api(U_ADMIN_B, "POST", "/api/v1/tax/profiles", {
            "subject_type": "household", "subject_id": str(HH_A), "tax_year": YEAR + 1,
            "filing_status": "married_filing_jointly", "federal_ordinary_rate": "0.3", "federal_lt_rate": "0.1",
            "niit_applies": False, "as_of": TODAY.isoformat(), "source_note": "x"}))[0],
        (await api(U_ADMIN_B, "POST", f"/api/v1/harvest/accounts/{ACCT_H}/scan", {}))[0],
    ]
    check(codes == [404] * 6, "another org's account, run, profile and household are a 404 both directions (reads, an "
          "approval, a profile for another org's household, a scan)", f"{codes}")
    # gates both ways on identical requests
    dbody = {"subject_type": "household", "subject_id": str(HH_A), "account_description": "REP6 verify outside",
             "global_security_id": str(S["sec_K"]), "side": "buy", "trade_date": TODAY.isoformat(),
             "source_note": "REP6 verify"}
    cv, ov = await api(U_TAXVIEW, "POST", "/api/v1/tax/declared-trades", dbody)
    ca, oa = await api(U_ADMIN1, "POST", "/api/v1/tax/declared-trades", dbody)
    tid = (oa.get("declared_trade") or {}).get("id")
    cw, ow = await api(U_TAXVIEW, "POST", f"/api/v1/tax/declared-trades/{tid}/withdraw", {"reason": "verify"})
    cw2, _ = await api(U_ADMIN1, "POST", f"/api/v1/tax/declared-trades/{tid}/withdraw", {"reason": "verify"})
    check(cv == 403 and "manage_tax" in str(ov) and ca == 201 and cw == 403 and cw2 == 200,
          "declare / withdraw: a view_tax holder is refused naming manage_tax; an org admin's identical request is "
          "accepted", f"{cv} {ca} {cw} {cw2}")
    cs, os_ = await api(U_TAXVIEW, "POST", "/api/v1/tax/substitutions/denials/seed", {})
    cs2, _ = await api(U_ADMIN1, "POST", "/api/v1/tax/substitutions/denials/seed", {})
    check(cs == 403 and "manage_tax" in str(os_) and cs2 == 200, "seed: refused for the viewer, accepted for an admin")
    cm, om = await api(U_MEMBER, "GET", "/api/v1/tax/overview")
    cv, ov = await api(U_TAXVIEW, "GET", "/api/v1/tax/overview")
    check(cm == 403 and "view_tax" in str(om) and cv == 200 and bool((ov.get("dashboard") or {}).get("gain_budgets")),
          "read: a member without view_tax is refused naming it; the viewer reads the dashboard", f"{cm} {cv}")
    hidden = [(await api(U_MEMBER, "GET", p))[0] for p in (
        f"/api/v1/tax/accounts/{ACCT_T1}", f"/api/v1/tax/loss-bank?subject_type=entity&subject_id={ENT_LB}"
        f"&tax_year={YEAR}", "/api/v1/harvest/runs", f"/api/v1/harvest/runs/{run_id}", "/api/v1/tax/profiles",
        "/api/v1/tax/substitutions", "/api/v1/tax/declared-trades", "/api/v1/tax/subjects")]
    check(hidden == [403] * 8, "tax data is never visible without view_tax (every read 403)", f"{hidden}")
    # envelopes: the viewer gets no actions; the maker none on her own pending row; a checker approve / reject
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", {
        "subject_type": "entity", "subject_id": str(ENT_SOLO), "tax_year": YEAR, "filing_status": "married_filing_jointly",
        "federal_ordinary_rate": "0.24", "federal_lt_rate": "0.15", "niit_applies": False, "as_of": TODAY.isoformat(),
        "source_note": "REP6 verify — pending"})
    pend = (o.get("profile") or {}).get("id")
    _, ev = await api(U_TAXVIEW, "GET", "/api/v1/tax/profiles")
    _, em = await api(U_ADMIN1, "GET", "/api/v1/tax/profiles")
    _, ec = await api(U_ADMIN2, "GET", "/api/v1/tax/profiles")
    row_v = next((r for r in ev.get("rows") or [] if r["id"] == pend), {})
    row_m = next((r for r in em.get("rows") or [] if r["id"] == pend), {})
    row_c = next((r for r in ec.get("rows") or [] if r["id"] == pend), {})
    pv = ev.get("permissions") or {}
    check(c == 201 and pv.get("can_write") is False and pv.get("can_approve") is False and ev.get("actions") == []
          and (ev.get("vocabularies") or {}).get("editable") == [] and row_v.get("actions") == []
          and row_m.get("actions") == [] and row_c.get("actions") == ["approve", "reject"]
          and "create_profile" in (em.get("actions") or []),
          "envelopes: view-only → no actions, empty editable; the maker gets no decision on her own row; a checker "
          "gets approve / reject; labels in vocabularies", f"{row_v.get('actions')} {row_m.get('actions')} "
          f"{row_c.get('actions')}")
    voc = em.get("vocabularies") or {}
    check(all(bool(voc.get(k)) for k in ("filing_statuses", "wash_sale_outcomes", "skip_reasons", "ledger_sources",
                                          "denial_rules", "set_statuses", "tax_classes")),
          "vocabularies carry filing statuses, wash-sale outcomes, skip reasons, ledger sources, denial rules")


async def y_new_org(conn):
    section("[Y] New org's first org_admin gets the three tax permissions")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        before = await conn.fetchval("SELECT count(*) FROM roles WHERE org_id = $1 AND name = 'org_admin'", ORG_C)
        await rbac.grant_org_admin(conn, U_NEWADMIN, ORG_C)
        perms = {r["name"] for r in await conn.fetch(
            "SELECT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id JOIN role_permissions rp ON "
            "rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id WHERE ur.user_id = $1", U_NEWADMIN)}
    check(int(before) == 0 and set(TAX_PERMS) <= perms, "a new org's first org_admin holds view / manage / approve_tax "
          "(rbac.ensure_org_admin_role)", f"{sorted(perms & set(TAX_PERMS))}")
    missing = await platform(conn, """SELECT count(*) FROM roles r CROSS JOIN permissions p WHERE r.name = 'org_admin'
                                      AND p.name = ANY($1::text[]) AND NOT (r.org_id = ANY($2::uuid[]))
                                      AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                                                      AND rp.permission_id = p.id)""",
                             list(TAX_PERMS), [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
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
    new = [k for k in gn if k not in gb]
    tax = [k for k in new if k[1].startswith(("/api/v1/tax/", "/api/v1/harvest/"))]
    check(len(new) == len(tax) == 31, "31 new routes, all under /tax/… and /harvest/…", f"{len(new)} {len(tax)}")

    def want(k):
        if "GET" in k[0]:
            return "view_tax"
        return "approve_tax" if k[1].endswith(("/approve", "/reject", "/retire")) else "manage_tax"

    bad = [(k, gn[k].get("after")) for k in tax if want(k) not in str(gn[k].get("after"))]
    check(not bad, "each names its tax permission: GET view_tax; approve / reject / retire approve_tax; every other "
          "write manage_tax", f"{bad[:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    rep6 = re.findall(r"^ok \d+ - (?:harvest dashboard|substitutions|profiles|declared trades|account Tax panel|"
                      r"proposal detail: each trade|lib: estimates|menu: Tax)", out, re.M)
    check(len(rep6) >= 8 and "not ok" not in out, "the rep6 render tests ran and passed (no Approve / Edit / Declare / "
          "Scan controls without the server's flags; every tax number an estimate)", f"{len(rep6)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep5.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    # verify_rep5 takes ~22 minutes standalone — 1200 s cut it off
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep5.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    result = re.findall(r"^RESULT: (.*)$", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    zero = (bool(total) and total[-1][1] == "0") or (bool(result) and "ALL ASSERTIONS PASSED" in result[-1])
    check(zero and not fails and code == 0, "verify_rep5.py: 0 FAIL ('TOTAL:' or 'RESULT:' summary)",
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
    m = await platform(conn, "SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE 'zz_rep6%'",
                       fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'REP6 VERIFY%'",
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM public.households WHERE name = $1", HOUSEHOLD_NAME, fetch="fetchval")
    a = await platform(conn, "SELECT count(*) FROM public.accounts WHERE account_number_masked LIKE '%' || $1 OR "
                             "org_id = ANY($2::uuid[])", "6000000", FIXTURE_ORGS, fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    return check(int(n) == 0 and int(m) == 0 and int(k) == 0 and int(h) == 0 and int(o) == 0,
                 f"the reserved prefixes ({PREFIX}, zz_rep6_, 'REP6 VERIFY', the household name, the fixture orgs) match "
                 "no real row", f"{n}/{m}/{k}/{h}/{o}; accounts with the masked suffix {a} (counted, never touched)")


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
        else:
            check(False, "seed skipped: no catalog instrument class for the registry fixtures")
        if S.get("m3"):
            await run("profiles", y_profiles, conn)
            await run("substitutions", y_substitutions, conn)
            await run("wash-sale rules", y_wash_rules, conn)
            await run("wash-sale proposals", y_wash_proposals, conn)
            await run("harvest", y_harvest, conn)
            await run("concurrency (fix 4)", y_concurrency, conn)
            await run("loss bank", y_loss_bank, conn)
            await run("substitutions (versions, late denial)", y_substitutions_late, conn)
            await run("rep5 diff", y_rep5_diff, conn)
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
            """Every post-teardown read, as ONE unit, so a retry re-runs all of it on the same connection."""
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
