"""verify_rep7b — Replication Studio, sprint 7b (REP7B): account AUTHORITY
(read_only | advise | managed — a household setting, overridable per account,
approved by a second person) and CUSTODY (integrated | other_custodian |
outside); rep7's held-away guard re-keyed on authority; advise mode (one-off
proposals → member / other-adviser instructions + letter of instruction);
managed accounts at another custodian → manual DVP settlement instructions.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep7b.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the httpx network transport records and refuses — any attempt is
a failed check. Every custodian trade adapter's ``submit`` is wrapped to
record a call: a DVP or advise route that reached one is a failed check.

REAL: the app through an ASGI transport (middleware, Principal, every gate),
the workflow engine (human tasks), rep2 / rep5 / rep7 services, RLS and the
live database. The script's own connection is app_service (rolbypassrls =
false, asserted).

FIXTURES: three orgs (99000000-0000-0000-0000-00000007b001 / 7b002 / 7b003,
is_test = true, asserted); users created explicitly with the auth0_sub their
token carries and an @test.local email; masked account numbers only (raw
ZZ7B…); securities with the reserved prefix ZZR7B and the name prefix
'REP7B VERIFY'; households 'REP7B Verify Household …' (all checked absent from
real rows first). The leftover REP2 (…e2001/2/3) and REP6 (…e6001/2/3) fixture
orgs are never touched, swept or FK-walked; they are read only — as the
"existing accounts" whose behaviour must not change.

TEARDOWN (start and end): the operator DSN (role postgres) with
execution.teardown / models.teardown / replica.teardown = 'fixture' in its
transaction — tables in FK child-before-parent order read from pg_constraint,
every DELETE keyed by this run's fixture ids; then every counted table is
re-read for its exact pre-test count, and real rows are compared by count +
checksum (the checksum excludes only rep7b's new column, proposal_kind).

Every expected date is computed at the moment of the call (no TODAY fixed at
script start). With VERIFY_NESTED=1 the regression section prints one
'[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
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
import main  # noqa: E402
from services import account_models as AM  # noqa: E402
from services import authority as AU  # noqa: E402
from services import execution as X  # noqa: E402
from services import harvest as HV  # noqa: E402
from services import held_away as HA  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import models as M  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import settlement_letter as SL  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

BEFORE_REF = "9ed75ae"   # main @ merge rep7 — the rep7b-work base
TAG = "rep7bverify"
PREFIX = "ZZR7B"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("K", "FUND", "FUND2")
SEC_TYPES = {"FUND": "fund", "FUND2": "fund"}
SEC_NAMES = {k: f"REP7B VERIFY {k}" for k in SEC_KEYS}
HH_NAMES = ("REP7B Verify Household Advise", "REP7B Verify Household DVP")
# other scripts' leftover fixture orgs: never touched; excluded from every leftover check by construction
FOREIGN_FIXTURE_ORGS = [UUID(f"99000000-0000-0000-0000-0000000e{n}") for n in ("2001", "2002", "2003", "6001",
                                                                              "6002", "6003")]


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000007b{n:03d}")


ORG_A, ORG_B, ORG_C = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_C]
U_SUPER, U_ADMIN1, U_ADMIN2, U_ADMIN3, U_VIEWER, U_ADMIN_B = (_id(110 + i) for i in range(6))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP7B Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP7B Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP7B Admin Checker"),
    U_ADMIN3: (ORG_A, "org_admin", "admin3", "org_admin", "REP7B Admin Reviewer"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep7b_viewer", "REP7B Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP7B Org B Admin"),
}
AUTH_PERMS = ("view_authority", "manage_authority", "approve_authority")
VIEWER_PERMS = ("view_authority", "view_proposals", "view_execution", "view_held_away", "view_replication")

ENT_A, ENT_B = _id(201), _id(202)
HH_ADV, HH_DVP = _id(211), _id(212)
ACCT_INT, ACCT_INT2, ACCT_ADV, ACCT_DVP, ACCT_RO, ACCT_B = (_id(301 + i) for i in range(6))
ACCOUNTS = {   # id: (org, custodian_code, household, held_away, raw number)
    ACCT_INT: (ORG_A, "ALTRUIST", HH_ADV, False, "ZZ7B000001"),
    ACCT_INT2: (ORG_A, "ALTRUIST", HH_ADV, False, "ZZ7B000002"),
    ACCT_ADV: (ORG_A, "SCHWAB", HH_ADV, True, "ZZ7B000003"),
    ACCT_DVP: (ORG_A, "ALTRUIST", HH_DVP, True, "ZZ7B000004"),
    ACCT_RO: (ORG_A, "FIDELITY", HH_DVP, True, "ZZ7B000005"),
    ACCT_B: (ORG_B, "SCHWAB", None, True, "ZZ7B000006"),
}
STALE_DAYS = 30

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.account_balances_daily", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.tax_lots", "portfolio.transactions", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "public.execution_instructions", "public.execution_instruction_lots",
    "public.execution_fills", "public.execution_instruction_events", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.replica_securities", "public.account_model_links",
    "public.rebalance_proposals", "public.rebalance_proposal_trades", "public.held_away_sources",
    "public.held_away_imports", "public.held_away_records", "public.stale_confirmations", "public.xray_runs",
    "public.xray_lines", "public.household_authority", "public.account_authority_settings",
    "public.instruction_settlements",
)
NEW_TABLES = ("public.household_authority", "public.account_authority_settings", "public.instruction_settlements")
# what each live cell must do on every path (Task 1b) — stated HERE, independently of services/authority.py
TRIGGERS = ("join", "model_change", "drift", "cash_flow", "manual", "harvest")
MODEL = {"join", "change_model", "settings", "transition", "harvest", *(f"proposal:{t}" for t in TRIGGERS)}
EXEC = {"execute", "instructions", "route"}
EXPECTED = {
    ("read_only", "integrated"): (set(), ()), ("read_only", "other_custodian"): (set(), ()),
    ("read_only", "outside"): (set(), ()),
    ("advise", "integrated"): ({"one_off"} | EXEC, ("member", "other_adviser")),
    ("advise", "other_custodian"): ({"one_off"} | EXEC, ("member", "other_adviser")),
    ("advise", "outside"): ({"one_off"} | EXEC, ("member", "other_adviser")),
    ("managed", "integrated"): (MODEL | EXEC, ("custodian_desk", "broker", "member", "other_adviser", "trader")),
    ("managed", "other_custodian"): (MODEL | EXEC, ("broker", "trader", "member", "other_adviser")),
    ("managed", "outside"): (set(), ()),
}

S: dict = {}
NETWORK_CALLS: list[str] = []
ADAPTER_CALLS: list[str] = []
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


def tk(key):
    return f"{PREFIX}{key}"


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def ago(n: int) -> date:
    return today() - timedelta(days=n)


def next_business_day(d: date) -> date:
    """The verify's own T+1 (weekends only), independent of services/authority.py."""
    n = d + timedelta(days=1)
    while n.isoweekday() in (6, 7):
        n = n + timedelta(days=1)
    return n


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
    """Rows this sprint must never touch (count + checksum) — everything outside this run's own fixture orgs,
    including the leftover REP2 / REP6 fixture orgs (the 'existing accounts'). The checksum excludes only the new
    column rebalance_proposals.proposal_kind."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("tax_lots", "portfolio.tax_lots"), ("transactions", "portfolio.transactions"),
                        ("execution_instructions", "public.execution_instructions"),
                        ("account_model_links", "public.account_model_links"),
                        *[(t.split(".")[1], t) for t in NEW_TABLES]):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["rebalance_proposals"] = await fingerprint(
        conn, "SELECT to_jsonb(p) - 'proposal_kind' AS j FROM public.rebalance_proposals p "
              "WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
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
    """Child-before-parent delete for a FIXTURE-ID parent (this run's users / roles / orgs only)."""
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
    """Rows under THIS RUN's fixture orgs in any table the plan does not name. Keyed by this run's org ids only
    (never another script's leftover orgs); child-before-parent from pg_constraint."""
    assert not set(orgs) & set(FOREIGN_FIXTURE_ORGS)
    found = []
    for t in await org_tables(conn):
        if t in skip:
            continue
        if await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {t} WHERE org_id = ANY($1::uuid[]))", orgs):
            found.append(t)
    if not found:
        return
    for t in await fk_order(conn, found):
        status = await conn.execute(f"DELETE FROM {t} WHERE org_id = ANY($1::uuid[])", orgs)
        n = int(status.rsplit(" ", 1)[-1])
        if n:
            log.append(f"{t}: {n} (sweep)")


async def teardown() -> list[str]:
    """Operator connection, the teardown GUCs, FK child-before-parent, every DELETE keyed by this run's fixture
    ids. Audit, events, todos and workflow rows of this run go before the fixture users."""
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
            plan = {t: (by_org, [orgs]) for t in (
                "public.instruction_settlements", "public.account_authority_settings", "public.household_authority",
                "public.xray_lines", "public.xray_runs", "public.stale_confirmations", "public.held_away_records",
                "public.held_away_imports", "public.held_away_sources", "public.loss_bank_entries",
                "public.harvest_candidates", "public.harvest_runs", "public.rebalance_proposal_trades",
                "public.account_transition_plans", "public.rebalance_proposals", "public.account_model_links",
                "public.execution_instruction_events", "public.execution_fills", "public.execution_instruction_lots",
                "public.execution_instructions", "public.fee_exclusions", "public.fee_assignments",
                "public.domain_event_deliveries", "public.domain_events", "public.workflow_run_steps",
                "public.agent_proposals", "public.workflow_runs", "public.workflow_steps", "public.workflow_versions",
                "public.workflow_definitions", "portfolio.replica_registry_keys", "portfolio.model_events",
                "portfolio.model_version_positions", "portfolio.model_sleeves", "portfolio.model_versions",
                "portfolio.models", "portfolio.replica_securities", "portfolio.tax_lots", "portfolio.transactions",
                "portfolio.positions", "portfolio.assets", "public.account_balances_daily", "public.account_owners",
                "public.accounts", "public.households", "public.entities", "public.org_settings")}
            plan["public.member_todos"] = org_or_user
            plan["public.audit_log"] = org_or_user
            plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [secs])
            plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [secs])
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


async def teardown_retrying() -> list[str]:
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


async def leftovers(conn) -> int:
    """This run's fixture rows only (other scripts' leftover orgs are excluded by construction)."""
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])", [list(SEC_NAMES.values())]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    op = await operator_conn()
    try:
        for t in await org_tables(op):
            n += int(await op.fetchval(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    finally:
        await close_conn(op)
    return n


async def pre_teardown(conn):
    log = await teardown_retrying()
    if log:
        find("pre-run cleanup removed an earlier run's leftovers", "; ".join(log))
    else:
        print("[INFO] pre-run cleanup: nothing left from an earlier run")
    check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api_raw(uid, method, path, body=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep7b"}}
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
        raise AssertionError(f"verify_rep7b: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    # every custodian trade adapter records a submit: a DVP / advise route must never reach one
    for cls in [c for c in vars(TR).values() if isinstance(c, type) and issubclass(c, TR.TradeAdapter)
                and c not in (TR.TradeAdapter, TR.ManualTradeAdapter)]:
        orig = cls.submit

        async def recording(self, ctx, trade_list, *a, _orig=orig, _name=cls.__name__, **kw):
            ADAPTER_CALLS.append(f"{_name}:{trade_list.account_id}")
            return await _orig(self, ctx, trade_list, *a, **kw)

        cls.submit = recording


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


async def in_org(fn, org=None, super_admin=False):
    pool = await pool_()
    with _ctx(org or ORG_A, super_admin):
        async with pool.acquire() as c2:
            return await fn(c2)


def pos(row, key, w):
    sid = S.get(f"sec_{key}") if key != "cash" else None
    return {"row_num": row, "global_security_id": str(sid) if sid else None, "raw_ticker": tk(key) if sid else None,
            "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": SEC_NAMES.get(key, "Cash"),
            "target_weight": D(w), "asset_class": None, "sleeve_label": None, "drift_min": None, "drift_max": None,
            "alternates": [], "restrictions": None, "notes": None,
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


async def join(acct, model):
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join", {"model_id": str(model), "settings": {}})
    lid = (o.get("link") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct}/model/changes/{lid}/approve", {})
    return c, c2, o2.get("transition_proposal_id"), o, o2


async def manual_import(acct, as_of: date, rows: list[dict], uid=None):
    return await api(uid or U_ADMIN1, "POST", f"/api/v1/held-away/accounts/{acct}/imports/manual",
                     {"as_of": as_of.isoformat(), "rows": rows})


async def approve_import(iid, uid=None):
    return await api(uid or U_ADMIN2, "POST", f"/api/v1/held-away/imports/{iid}/approve", {})


async def account_change(acct, body, uid=U_ADMIN1):
    return await api(uid, "POST", f"/api/v1/authority/accounts/{acct}/changes",
                     {"effective_from": today().isoformat(), "reason": "REP7B verify", **body})


async def household_change(hh, authority, uid=U_ADMIN1):
    return await api(uid, "POST", f"/api/v1/authority/households/{hh}/changes",
                     {"authority": authority, "effective_from": today().isoformat(), "reason": "REP7B verify"})


async def decide_change(cid, outcome="approve", uid=U_ADMIN2, note=None):
    return await api(uid, "POST", f"/api/v1/authority/changes/{cid}/{outcome}", {"note": note} if note else {})


async def effective_of(acct) -> dict:
    return await in_org(lambda c: AM.load_account(c, ORG_A, acct))


def authority_refused(code, body) -> bool:
    s = str(body)
    return bool(code == 409 and "refused:" in s and "(authority " in s)


def exc_refused(exc: Exception) -> bool:
    s = str(exc)
    return "refused:" in s and "(authority " in s


# Every literal the seed and the tests write into a CHECK-constrained column — checked against the live catalog.
SEED_CHECK_LITERALS = (
    ("public.household_authority", "household_authority_authority_chk", ("read_only", "advise", "managed")),
    ("public.household_authority", "household_authority_status_chk", ("draft", "approved", "rejected")),
    ("public.account_authority_settings", "account_authority_settings_custody_chk",
     ("integrated", "other_custodian", "outside")),
    ("public.account_authority_settings", "account_authority_settings_authority_chk", ("read_only", "advise",
                                                                                         "managed")),
    ("public.instruction_settlements", "instruction_settlements_executor_chk", ("broker", "trader")),
    ("public.instruction_settlements", "instruction_settlements_calendar_chk", ("weekends_only",)),
    ("public.execution_instructions", "execution_instructions_executor_chk",
     ("custodian_desk", "broker", "member", "other_adviser", "trader")),
    ("public.execution_instructions", "execution_instructions_status_chk",
     ("draft", "pending_approval", "approved", "sent", "filled", "reconciled")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", TRIGGERS),
    ("public.rebalance_proposals", "rebalance_proposals_status_chk", ("draft", "pending_approval", "approved",
                                                                       "expired", "executed")),
    ("public.rebalance_proposals", "rebalance_proposals_kind_chk", ("model", "one_off", "manual")),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long",)),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.replica_securities", "replica_securities_status_chk", ("active",)),
    ("portfolio.models", "models_source_chk", ("upload",)),
)
SEED_INSTRUMENT_CLASS = "index_etf"
SEED_REGISTRATION, SEED_TAX_STATUS = "individual", "taxable"


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e, with the final matrix and migration mapping")
    find("1a readers", "is_held_away: custody/importer.py sets it; read by held_away (sources, staleness, the guard), "
         "rebalance_proposals (warning, executor), xray (join eligibility), loss_bank / wash_sale / tax_overview "
         "(data tagging, unchanged), routers (envelopes), web (labels). is_discretionary → rep5 advice_only on the "
         "link at join (account_models.py) and at generate. service_model: free text, read by nothing that decides")
    find("1b matrix (decided)", "read_only: nothing. advise (any custody): one_off + execute/instructions/route with "
         "executor member | other_adviser only, never an adapter. managed+integrated: everything as before. "
         "managed+other_custodian: every model path; instructions broker | trader (manual DVP settlement) or "
         "member | other_adviser (rep5 advice-only). managed+outside: not a setting (CHECK + request refusal)")
    find("1b correction", "'advise ⇔ not discretionary' NOT adopted: a non-discretionary managed account following a "
         "model gets drift / model-change proposals as advice today (rep5); mapping it to advise would refuse them. "
         "One source per decision: paths ← authority × custody; managed executor ← rep5 advice_only; advise executor "
         "← the authority")
    find("1c migration", "writes NO rows: every live account sits in a leftover REP2 fixture org (is_test); the default "
         "is computed at read time (held-away → read_only / outside, else managed / integrated) — rep7 exactly")
    find("1d DVP / 1e PDF", "settlement detail is a child table (instruction_settlements); a separate DVP settlement "
         "PDF (different audience than rep2's letter); no holiday calendar exists → weekends only, stated")
    bad = []
    for (a, c), (paths, execs) in EXPECTED.items():
        if set(AU.allowed_paths(a, c)) != paths or tuple(AU.allowed_executors(a, c)) != execs:
            bad.append((a, c, sorted(AU.allowed_paths(a, c)), AU.allowed_executors(a, c)))
        if AU.valid_setting(a, c) != (not (a == "managed" and c == "outside")):
            bad.append((a, c, "valid"))
    check(not bad, "the implemented matrix equals the decided one in every authority × custody cell (paths and "
          "executors)", f"{bad[:3]}")
    check(AU.default_for({"is_held_away": True}) == ("read_only", "outside")
          and AU.default_for({"is_held_away": False}) == ("managed", "integrated"),
          "the migrated default is rep7's rule exactly")


async def y_schema(conn):
    section("[Y] Schema landed: tables / columns, CHECKs (managed + outside refused), partial uniques, RLS with a "
            "policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep7b.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep7b.py --verify: SCHEMA OK", f"{gaps[:3]} "
          f"{err[-200:] if code else ''}")
    for t in NEW_TABLES:
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                       "tablename = $1", t.split(".")[1])}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY($1::text[])",
        [t.split(".")[1] for t in NEW_TABLES] + ["rebalance_proposals"])}
    S["uniques"] = idx
    check(all("WHERE" in idx.get(n, "") for n in ("household_authority_live_uq", "account_authority_settings_live_uq",
                                                  "instruction_settlements_live_uq", "rebalance_proposals_open_uq")),
          "partial unique indexes read from the catalog before seeding (one live approved row per household / "
          "account; one live settlement per instruction; one open proposal per account)")
    fks = await platform(conn, "SELECT conrelid::regclass::text AS t, conname FROM pg_constraint WHERE contype = 'f' "
                               "AND conrelid = ANY($1::regclass[])", list(NEW_TABLES))
    check(len(fks) >= 10, "FK constraints on the new tables read from the catalog", f"{len(fks)}")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog (CHECKs, lookup tables) before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'::text", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    check(not bad, f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints)",
          "; ".join(bad))
    row = await platform(conn, "SELECT key, enabled FROM portfolio.replica_instrument_classes WHERE key = $1 "
                               "AND valid_to IS NULL AND system_to IS NULL", SEED_INSTRUMENT_CLASS, fetch="fetchrow")
    if check(row is not None and row["enabled"] is True, f"instrument class {SEED_INSTRUMENT_CLASS!r} is a current key"):
        S["instrument_class"] = row["key"]
    codes = {r["code"] for r in await platform(conn, "SELECT code FROM public.transaction_types WHERE code = "
                                                     "ANY($1::text[])", ["buy", "sell"])}
    check(codes == {"buy", "sell"}, "transaction_type_code 'buy' / 'sell' exist", f"{sorted(codes)}")
    find("unconstrained seed text", f"accounts.registration_type ({SEED_REGISTRATION!r}) / tax_status "
         f"({SEED_TAX_STATUS!r}) / custodian_code carry no CHECK — the values rep5–rep7 fixtures use")


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'REP7B VERIFY%'",
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM public.households WHERE name = ANY($1::text[])", list(HH_NAMES),
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    masks = [AccountNumber(v[4]).masked for v in ACCOUNTS.values()]
    a = await platform(conn, "SELECT count(*) FROM public.accounts WHERE account_number_masked = ANY($1::text[]) "
                             "AND custodian_code = ANY($2::text[])", masks, ["ALTRUIST", "SCHWAB", "FIDELITY"],
                       fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(h) == 0 and int(o) == 0,
                 f"the reserved prefixes ({PREFIX}, 'REP7B VERIFY', the household names, the fixture orgs) match no "
                 "real row", f"{n}/{k}/{h}/{o}; accounts with a fixture mask {a} (never touched)")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_C, "c")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP7B Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        desc, perms = C.BASELINE_ROLES["super_admin"]
        rid_super = await rbac.ensure_role(conn, ORG_A, "super_admin", desc)
        for p in perms:
            if p in perm_ids:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid_super, perm_ids[p])
        rid_view = await rbac.ensure_role(conn, ORG_A, "rep7b_viewer", f"REP7B verify — {', '.join(VIEWER_PERMS)}")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p])
        roles = {"super_admin": rid_super, "rep7b_viewer": rid_view}
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for ent, org in ((ENT_A, ORG_A), (ENT_B, ORG_B)):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"REP7B Verify {org}"[:60])
        for hh, nm in ((HH_ADV, HH_NAMES[0]), (HH_DVP, HH_NAMES[1])):
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                               hh, ORG_A, nm, U_ADMIN1)
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES.get(key, "equity"),
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:04d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, hh, held, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10)",
                aid, org, n.masked, n.hashed(salts[org]), code, SEED_REGISTRATION, SEED_TAX_STATUS,
                ENT_A if org == ORG_A else ENT_B, hh, held)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, $3::jsonb)",
                           ORG_A, HA.STALE_SETTING, json.dumps(STALE_DAYS))
        # the managed integrated accounts: cash (and K) on the integrated feed
        for acct in (ACCT_INT, ACCT_INT2):
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", ORG_A, acct,
                today() - timedelta(days=1), D(10000))
        # the X-ray registry entry for FUND (the line the one-off offer sits on)
        rs = uuid.uuid4()
        S["entry_FUND"] = rs
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3, $4, $5, 'zz_rep7b_test', "
            "'active', $6, $7)", rs, ORG_A, S["sec_FUND"], S["instrument_class"], tk("FUND"),
            f"Holdings of {tk('FUND')}", U_ADMIN1)
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(len(orgs) == 3 and all(r["is_test"] is True for r in orgs), "every fixture org is is_test = true (the live "
          "scheduler skips them)")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test AND auth0_sub LIKE $2",
                        list(USERS), f"%{TAG}%", fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly with the token's auth0_sub, is_test = true")
    S["mK"], S["mKv"] = await make_model("REP7B VERIFY K", [pos(1, "K", "0.50"), pos(2, "cash", "0.50")])
    # held-away data through rep7's real import path (a second person approves each)
    for acct, key, as_of in ((ACCT_ADV, "FUND", ago(1)), (ACCT_DVP, "FUND2", ago(STALE_DAYS + 5)),
                             (ACCT_RO, "FUND2", ago(1))):
        c, o = await manual_import(acct, as_of, [{"ticker": tk(key), "quantity": "100", "market_value": "10000",
                                                  "acquired_date": "2019-01-02", "cost": "8000"}])
        c2, o2 = await approve_import(o.get("id"))
        check(c == 201 and c2 == 200 and o2.get("status") == "approved",
              f"held-away data for {AccountNumber(ACCOUNTS[acct][4]).masked} approved through rep7's import",
              f"{c} {c2} {str(o2)[:120]}")


async def y_migration(conn):
    section("[Y] Migration: every existing account's behaviour unchanged; the existing held-away draft instruction "
            "refused approval with the reason, kept")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep7b.py"), "--migrate"], REPO, timeout=600)
    changed = [ln for ln in out.splitlines() if "CHANGED" in ln and "MIGRATION" not in ln]
    check(code == 0 and "MIGRATION OK" in out and not changed, "_apply_rep7b.py --migrate: every existing account "
          "UNCHANGED on every path", f"{changed[:3]} {err[-200:] if code else ''}")
    real = [dict(r) for r in await platform(
        conn, "SELECT id, org_id, account_number_masked, is_held_away FROM public.accounts WHERE NOT (org_id = "
              "ANY($1::uuid[])) AND valid_to IS NULL AND system_to IS NULL", FIXTURE_ORGS)]
    diffs, rows_for_real = [], 0
    for a in real:
        acct = await in_org(lambda c, a=a: AM.load_account(c, a["org_id"], a["id"]), a["org_id"], True)
        for p in AU.ALL_PATHS:
            before = (not a["is_held_away"]) if p != "one_off" else False
            if AU.path_allowed(acct, p) != before:
                diffs.append(f"{a['account_number_masked']}:{p}")
        rows_for_real += 0 if acct["authority_source"] == "default" and acct["settings_id"] is None else 1
    check(len(real) > 0 and not diffs and rows_for_real == 0,
          f"the {len(real)} existing account(s), loaded through the real loader: every path allowed / refused exactly "
          "as before, every one on the migrated default (no settings row)", f"{diffs[:5]}")
    drafts = await platform(
        conn, """SELECT i.id, i.org_id, i.status, i.executor_type, i.maker_id, i.created_at, i.cancelled_at
                 FROM public.execution_instructions i JOIN public.accounts a ON a.id = i.account_id
                   AND a.valid_to IS NULL AND a.system_to IS NULL
                 WHERE a.is_held_away AND i.system_to IS NULL AND i.status = 'draft'
                   AND NOT (i.org_id = ANY($1::uuid[]))""", FIXTURE_ORGS)
    S["foreign_drafts"] = [(str(r["id"]), r["status"]) for r in drafts]
    outs = []
    for r in drafts:
        d = await in_org(lambda c, r=r: X.detail(c, r["org_id"], r["id"]), r["org_id"], True)
        acts = X.actions_for({**d, "maker_ids": d.get("maker_ids") or []}, can_write=True, can_approve=True,
                             user_id=uuid.uuid4(), task_open=False)
        outs.append((d["status"], d.get("approval_refused") or "", acts, r["cancelled_at"]))
    check(len(drafts) >= 1 and all(st == "draft" and "held-away" in why and "read-only" in why and "submit" not in acts
                                   and cancelled is None for st, why, acts, cancelled in outs),
          "the existing held-away DRAFT instruction(s): read through the real detail — approval refused with the "
          "plain reason (read-only), no submit action, still draft (neither deleted nor cancelled)",
          f"{[(str(r['id'])[:8], r['executor_type'], str(r['created_at'])[:10]) for r in drafts]} "
          f"{[o[1][:90] for o in outs]}")
    # the same rule, proven on an identical draft in this run's org: submit refused, nothing written
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_A))
        iid = await conn.fetchval(
            "INSERT INTO public.execution_instructions (org_id, account_id, global_security_id, side, quantity, "
            "price_kind, executor_type, executor_name, execute_by, if_not_filled, source, routed_via, routing_reason, "
            "status, maker_id, maker_ids) VALUES ($1, $2, $3, 'sell', 1, 'market', 'member', 'Pat Example', $4, "
            "'cancel', 'manual', 'manual', 'pre-rep7 style draft (verify)', 'draft', $5, ARRAY[$5::uuid]) "
            "RETURNING id", ORG_A, ACCT_RO, S["sec_FUND2"], today() + timedelta(days=7), U_ADMIN1)
    S["ro_draft"] = iid
    tasks0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_run_steps", fetch="fetchval"))
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/submit", {})
    row = await scoped(conn, ORG_A, "SELECT status, approval_task_id FROM public.execution_instructions WHERE id = $1 "
                                    "AND system_to IS NULL", iid, fetch="fetchrow")
    tasks1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_run_steps", fetch="fetchval"))
    check(c == 409 and "read-only" in str(o) and row["status"] == "draft" and row["approval_task_id"] is None
          and tasks0 == tasks1, "an identical draft on a read-only held-away account: submit for approval refused "
          "(409, the reason), the instruction stays a draft, no task opened", f"{c} {str(o)[:140]} {dict(row)}")


async def y_changes(conn):
    section("[Y] Authority changes: maker-checker, override beats household, history, forbidden open proposals "
            "withdrawn with the reason")
    # ACCT_INT: its own override managed (approved) BEFORE the household goes advise
    c, o = await account_change(ACCT_INT, {"authority_override": "managed", "custody": "integrated"})
    cid = (o.get("change") or {}).get("id")
    cm, om = await decide_change(cid, uid=U_ADMIN1)
    cv, ov = await decide_change(cid, uid=U_VIEWER)
    st = await scoped(conn, ORG_A, "SELECT status FROM public.account_authority_settings WHERE id = $1 AND "
                                   "system_to IS NULL", cid, fetch="fetchval")
    ca, oa = await decide_change(cid, uid=U_ADMIN2)
    check(c == 201 and cm == 403 and "maker-checker" in str(om) and cv == 403 and "approve_authority" in str(ov)
          and st == "draft" and ca == 200 and (oa.get("change") or {}).get("status") == "approved",
          "account override: the maker is refused (403 maker-checker), a viewer is refused naming approve_authority, "
          "the change stays a draft until a second person approves it", f"{c} {cm} {cv} {st} {ca}")
    # ACCT_INT2 (same household, no override) joins a model: its transition proposal is open, awaiting approval
    cj, cj2, pid_join, _, oj2 = await join(ACCT_INT2, S["mK"])
    S["pid_int2"] = pid_join
    p0 = await scoped(conn, ORG_A, "SELECT status, approval_task_id FROM public.rebalance_proposals WHERE id = $1 AND "
                                   "system_to IS NULL", pid_join, fetch="fetchrow")
    check(cj == 201 and cj2 == 200 and p0 is not None and p0["status"] in ("draft", "pending_approval"),
          "a managed account in the household joins: its transition proposal is open", f"{cj} {cj2} {dict(p0 or {})}")
    # the household → advise
    c, o = await household_change(HH_ADV, "advise")
    hid = (o.get("change") or {}).get("id")
    cm, om = await decide_change(hid, uid=U_ADMIN1)
    ca, oa = await decide_change(hid, uid=U_ADMIN2)
    wd = (oa.get("change") or {}).get("withdrawn") or []
    p1 = await scoped(conn, ORG_A, "SELECT status, status_reason FROM public.rebalance_proposals WHERE id = $1 AND "
                                   "system_to IS NULL", pid_join, fetch="fetchrow")
    task = await in_org(lambda c_: HT.get_task(c_, ORG_A, p0["approval_task_id"])) if p0["approval_task_id"] \
        else {"status": "none"}
    check(c == 201 and cm == 403 and ca == 200 and p1["status"] == "expired" and "withdrawn" in (p1["status_reason"] or "")
          and "advise" in (p1["status_reason"] or "") and any(w.get("proposal_id") == str(pid_join) for w in wd)
          and task["status"] in ("cancelled", "none"),
          "household → advise: maker refused, checker approves; the open proposal the new authority forbids is "
          "withdrawn (expired) with the plain reason and its task withdrawn — never executed",
          f"{c} {cm} {ca} {dict(p1)} {task['status']} {wd}")
    e_int, e_int2, e_adv = (await effective_of(a) for a in (ACCT_INT, ACCT_INT2, ACCT_ADV))
    check((e_int["authority"], e_int["authority_source"]) == ("managed", "account")
          and (e_int2["authority"], e_int2["authority_source"]) == ("advise", "household")
          and (e_adv["authority"], e_adv["custody"], e_adv["authority_source"]) == ("advise", "outside", "household"),
          "the account override beats the household (managed stays managed); the others inherit advise",
          f"{e_int['authority']}/{e_int['authority_source']} {e_int2['authority']} {e_adv['authority']}")
    # ACCT_DVP: custody another custodian (no override) — then its household goes advise, then managed
    c, o = await account_change(ACCT_DVP, {"custody": "other_custodian", "receiving": {
        "custodian_name": "ZZ Receiving Trust", "participant_number": "0901", "agent_bank": "ZZ Agent Bank",
        "account_last4": "4321", "contra_broker": "ZZ Broker"}})
    ca, _ = await decide_change((o.get("change") or {}).get("id"))
    rec = (o.get("change") or {}).get("receiving") or {}
    check(c == 201 and ca == 200 and rec.get("account_masked") == "****4321" and "ZZ7B" not in json.dumps(o),
          "custody change to another custodian with the receiving details (account stored masked, last four only)",
          f"{c} {ca} {rec}")
    cf, of_ = await account_change(ACCT_DVP, {"custody": "other_custodian", "receiving": {
        "custodian_name": "ZZ", "participant_number": "0901", "account_last4": "123456789"}})
    check(cf == 422, "a full receiving account number is refused (last four only)", f"{cf} {str(of_)[:100]}")
    c1, o1 = await household_change(HH_DVP, "advise")
    a1, _ = await decide_change((o1.get("change") or {}).get("id"))
    # managed for the household: ACCT_RO inherits and is outside → refused as a setting, naming it
    cr, orr = await household_change(HH_DVP, "managed")
    check(cr == 409 and "managed needs a custodian" in str(orr) and AccountNumber(ACCOUNTS[ACCT_RO][4]).masked in str(orr),
          "a household change to managed is refused while an inheriting account is outside the platform (named)",
          f"{cr} {str(orr)[:140]}")
    cro, oro = await account_change(ACCT_RO, {"authority_override": "read_only", "custody": "outside"})
    aro, _ = await decide_change((oro.get("change") or {}).get("id"))
    c2, o2 = await household_change(HH_DVP, "managed")
    rj, orj = await decide_change((o2.get("change") or {}).get("id"), "reject")
    rj2, _ = await decide_change((o2.get("change") or {}).get("id"), "reject", note="REP7B verify — not yet")
    c3, o3 = await household_change(HH_DVP, "managed")
    a3, _ = await decide_change((o3.get("change") or {}).get("id"))
    hist = await scoped(conn, ORG_A, "SELECT authority, status, valid_to, decision_note FROM public.household_authority "
                                     "WHERE household_id = $1 AND system_to IS NULL ORDER BY created_at", HH_DVP)
    arch = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.household_authority WHERE household_id = $1 AND "
                                         "system_to IS NOT NULL", HH_DVP, fetch="fetchval"))
    check(c1 == 201 and a1 == 200 and cro == 201 and aro == 200 and c2 == 201 and rj == 422 and rj2 == 200
          and c3 == 201 and a3 == 200
          and [(h["authority"], h["status"], h["valid_to"] is not None) for h in hist]
          == [("advise", "approved", True), ("managed", "rejected", False), ("managed", "approved", False)]
          and arch == 3,
          "history kept: advise approved then superseded (closed, never deleted), a rejection (needs a note), managed "
          "approved and live; each decision archived its draft state", f"{[dict(h) for h in hist]} archived={arch}")
    e_dvp, e_ro = await effective_of(ACCT_DVP), await effective_of(ACCT_RO)
    check((e_dvp["authority"], e_dvp["custody"], e_dvp["authority_source"]) == ("managed", "other_custodian",
                                                                                  "household")
          and (e_ro["authority"], e_ro["authority_source"]) == ("read_only", "account"),
          "the household's managed applies to the account at another custodian; the read-only override holds",
          f"{e_dvp['authority']}/{e_dvp['custody']} {e_ro['authority']}")
    # managed + outside: refused as a request and by the CHECK
    co, oo = await account_change(ACCT_ADV, {"authority_override": "managed", "custody": "outside"})
    try:
        # every other CHECK satisfied (an approved, decided row) so only managed + outside can refuse it
        await scoped(conn, ORG_A, "INSERT INTO public.account_authority_settings (org_id, account_id, "
                                  "authority_override, custody, effective_from, reason, status, maker_id, "
                                  "approved_by, decided_at) VALUES ($1, $2, 'managed', 'outside', $3, 'x', 'approved', "
                                  "$4, $5, now())", ORG_A, ACCT_ADV, today(), U_ADMIN1, U_ADMIN2, fetch="execute")
        chk = None
    except asyncpg.CheckViolationError as exc:
        chk = exc.constraint_name
    check(co == 409 and "managed needs a custodian" in str(oo) and chk == "account_authority_settings_managed_outside_chk",
          "managed + outside refused as a setting: the request (409, plain reason) and the database CHECK",
          f"{co} {str(oo)[:120]} {chk}")


async def y_advise(conn):
    section("[Y] Advise: manual one-off → member / other-adviser instructions + letter (%PDF, masked); other "
            "triggers refused; reconcile from a held-away record; X-ray one-off trade")
    c, x = await api(U_ADMIN1, "GET", f"/api/v1/xray/households/{HH_ADV}")
    line = next((f for f in (x.get("xray") or {}).get("funds") or [] if f.get("account_id") == str(ACCT_ADV)), {})
    check(c == 200 and line.get("actions") == ["propose_one_off"] and (line.get("one_off") or {}).get("available") is True
          and (line.get("join") or {}).get("available") is False,
          "X-ray: the advise account's fund line offers 'Propose a one-off trade', not 'Open join request'",
          f"{c} {line.get('actions')} {line.get('join')}")
    cj, oj = await api(U_ADMIN1, "POST", f"/api/v1/xray/households/{HH_ADV}/join",
                       {"account_id": str(ACCT_ADV), "fund_security_id": str(S["sec_FUND"])})
    check(cj == 409 and "advise covers one-off trades only" in str(oj), "the X-ray join is refused for the advise "
          "account (plain reason)", f"{cj} {str(oj)[:120]}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/xray/households/{HH_ADV}/one-off",
                     {"account_id": str(ACCT_ADV), "fund_security_id": str(S["sec_FUND"]),
                      "executor_type": "other_adviser", "executor_name": "ZZ Other Adviser",
                      "executor_contact": "other@example.invalid"})
    p = o.get("proposal") or {}
    pid = p.get("id")
    trades = await scoped(conn, ORG_A, "SELECT side, quantity, global_security_id, lots FROM "
                                       "public.rebalance_proposal_trades WHERE proposal_id = $1 AND valid_to IS NULL "
                                       "AND system_to IS NULL", pid)
    check(c == 201 and p.get("proposal_kind") == "one_off" and p.get("trigger") == "manual" and p.get("status") == "draft"
          and p.get("model_id") is None and len(trades) == 1 and trades[0]["side"] == "sell"
          and D(trades[0]["quantity"]) == D(100) and len(json.loads(trades[0]["lots"])) == 1,
          "a draft one-off proposal (manual, no model) selling the whole fund, lots chosen by the engine",
          f"{c} {str(o)[:160]} {[dict(t) for t in trades]}")
    ws = {w["code"] for w in p.get("warnings") or []}
    check("one_off" in ws and "held_away" in ws, "the one-off carries the engine's warnings (one-off, held-away)",
          f"{sorted(ws)}")
    cs, _ = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/submit", {})
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/approve", {})
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {})
    pr = oa.get("proposal") or {}
    ids = (pr.get("execution") or {}).get("instruction_ids") or []
    ins = await scoped(conn, ORG_A, "SELECT id, executor_type, executor_name, source, source_ref, status, routed_via "
                                    "FROM public.execution_instructions WHERE id = ANY($1::uuid[]) AND system_to IS NULL",
                       ids)
    check(cs == 200 and cm == 403 and ca == 200 and len(ins) == 1 and ins[0]["executor_type"] == "other_adviser"
          and ins[0]["executor_name"] == "ZZ Other Adviser" and ins[0]["source"] == "proposal"
          and str(ins[0]["source_ref"]) == pid and ins[0]["status"] == "approved" and ins[0]["routed_via"] == "manual",
          "submitted, the maker refused, a checker approves → one instruction for the other adviser (source "
          "proposal, approved from the upstream maker-checker, routed manually)",
          f"{cs} {cm} {ca} {[dict(r) for r in ins]}")
    iid = str(ins[0]["id"]) if ins else None
    S["adv_iid"] = iid
    cl, body, hdr = await api_raw(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/letter", {})
    d = await in_org(lambda c_: X.detail(c_, ORG_A, iid))
    from services.execution_letter import letter_rows
    rows = letter_rows(d, account_display=d["account"]["account_number_masked"])
    raw = ACCOUNTS[ACCT_ADV][4]
    check(cl == 200 and body[:4] == b"%PDF" and raw.encode() not in body and "no-store" in hdr.get("cache-control", "")
          and any(v == AccountNumber(raw).masked for _, v in rows) and not any(raw in str(v) for _, v in rows),
          "the letter of instruction: a PDF (%PDF), masked account number only", f"{cl} {body[:8]!r} {rows[:2]}")
    cs2, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/settlement", {})
    check(cs2 == 422, "an advise instruction carries no DVP settlement detail (no settlement PDF)", f"{cs2}")
    # every other trigger refused for the advise account; the identical request passes the guard for the managed one
    rows_ = []
    for acct in (ACCT_ADV, ACCT_INT):
        cjn, ojn = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join", {"model_id": str(S["mK"]),
                                                                                         "settings": {}})
        rows_.append((acct, "join", authority_refused(cjn, ojn)))
        pool = await pool_()
        for trig, kind in (("drift", "system"), ("model_change", "system"), ("harvest", "system")):
            with _ctx(ORG_A):
                try:
                    await RP.generate(pool, org_id=ORG_A, account_id=acct, trigger=trig, maker_kind=kind)
                    rows_.append((acct, trig, False))
                except Exception as exc:  # noqa: BLE001
                    rows_.append((acct, trig, exc_refused(exc)))
        with _ctx(ORG_A):
            try:
                await HV.scan_account(pool, org_id=ORG_A, account_id=acct)
                rows_.append((acct, "harvest_scan", False))
            except Exception as exc:  # noqa: BLE001
                rows_.append((acct, "harvest_scan", exc_refused(exc)))
    adv = [r for r in rows_ if r[0] == ACCT_ADV]
    mng = [r for r in rows_ if r[0] == ACCT_INT]
    check(len(adv) == 5 and all(r[2] for r in adv) and len(mng) == 5 and not any(r[2] for r in mng),
          "advise: join, drift, model_change, harvest proposals and the harvest scan refused; the identical requests "
          "pass the authority guard for a managed account", f"{rows_}")
    # the member executes; the fill is entered; the held-away feed shows the trade → reconciled from that record
    cs3, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/sent", {"channel": "letter"})
    cf, of_ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/fills",
                        {"quantity": "100", "price": "101.50",
                         "filled_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()})
    trade_day = today()
    c, o = await manual_import(ACCT_ADV, trade_day, [
        {"ticker": tk("FUND"), "side": "sell", "trade_date": trade_day.isoformat(), "quantity": "100",
         "price": "101.50"},
        {"ticker": tk("FUND"), "side": "buy", "trade_date": trade_day.isoformat(), "quantity": "1", "price": "101"}])
    c2, _ = await approve_import(o.get("id"))
    recs = await scoped(conn, ORG_A, """SELECT r.id, t.transaction_type_code AS side FROM public.held_away_records r
                                         JOIN portfolio.transactions t ON t.id = r.record_id
                                         WHERE r.import_id = $1 AND r.record_type = 'transaction'
                                           AND r.system_to IS NULL""", o.get("id"))
    sell = next((str(r["id"]) for r in recs if r["side"] == "sell"), None)
    buy = next((str(r["id"]) for r in recs if r["side"] == "buy"), None)
    cw, ow = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/reconcile-held-away",
                       {"record_id": buy})
    cr, orr = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/reconcile-held-away",
                        {"record_id": sell})
    fin = await scoped(conn, ORG_A, "SELECT status, reconcile_method, reconcile_reference FROM "
                                    "public.execution_instructions WHERE id = $1 AND system_to IS NULL", iid,
                       fetch="fetchrow")
    check(cs3 == 200 and cf == 200 and c == 201 and c2 == 200 and bool(sell) and cw == 409 and cr == 200
          and fin["status"] == "reconciled" and fin["reconcile_method"] == "custodian_data"
          and fin["reconcile_reference"] == f"held_away_record:{sell}",
          "the fill is entered; a held-away record of the wrong side is refused; the matching held-away transaction "
          "reconciles it (custodian_data, the record as reference)", f"{cs3} {cf} {c} {c2} {cw} {cr} {dict(fin)}")


async def y_dvp(conn):
    section("[Y] Managed at another custodian: proposals → manual DVP settlement instructions with the details and "
            "T+1, never an API, masked PDF, staleness applies")
    ADAPTER_CALLS.clear()
    cj, cj2, pid, _, oj2 = await join(ACCT_DVP, S["mK"])
    c, d = await api(U_ADMIN1, "GET", f"/api/v1/proposals/{pid}")
    p = d.get("proposal") or {}
    ha = p.get("held_away") or {}
    own = next((x for x in ha.get("dependencies") or [] if x["account_id"] == str(ACCT_DVP)), {})
    check(cj == 201 and cj2 == 200 and bool(pid) and own.get("blocking") is True
          and own.get("as_of") == ago(STALE_DAYS + 5).isoformat(),
          "managed at another custodian: the join's transition proposal is generated, and depends on the account's "
          "OWN held-away data, stale (as-of stated) — staleness applies", f"{cj} {cj2} {own}")
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {})
    conf = await scoped(conn, ORG_A, "SELECT id FROM public.stale_confirmations WHERE account_id = $1 AND "
                                     "proposal_id = $2 AND valid_to IS NULL AND system_to IS NULL", ACCT_DVP, pid,
                        fetch="fetchval")
    cc, _ = await api(U_ADMIN1, "POST", f"/api/v1/held-away/confirmations/{conf}/confirm", {"note": "seen"})
    ca2, oa2 = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {})
    trade_day = today()   # the expected dates, computed at the call
    ids = ((oa2.get("proposal") or {}).get("execution") or {}).get("instruction_ids") or []
    ins = await scoped(conn, ORG_A, "SELECT id, executor_type, routed_via, status FROM public.execution_instructions "
                                    "WHERE id = ANY($1::uuid[]) AND system_to IS NULL", ids)
    sts = await scoped(conn, ORG_A, "SELECT * FROM public.instruction_settlements WHERE instruction_id = "
                                    "ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL", ids)
    check(ca == 409 and "confirm" in str(oa) and conf is not None and cc == 200 and ca2 == 200 and len(ins) >= 1
          and all(r["executor_type"] == "broker" and r["routed_via"] == "manual" and r["status"] == "approved"
                  for r in ins) and len(sts) == len(ins),
          "approval waits for the stale-data confirmation; once confirmed and approved, every trade is a manual "
          "instruction for a broker with its DVP settlement row", f"{ca} {cc} {ca2} {[dict(r) for r in ins]} "
          f"{len(sts)}")
    want = next_business_day(trade_day)
    check(all(r["trade_date"] == trade_day and r["settlement_date"] == want and r["receiving_custodian_name"]
              == "ZZ Receiving Trust" and r["receiving_participant_number"] == "0901"
              and r["receiving_agent_bank"] == "ZZ Agent Bank" and r["receiving_account_masked"] == "****4321"
              and r["contra_broker"] == "ZZ Broker" and r["calendar_basis"] == "weekends_only"
              and r["authority"] == "managed" and r["custody"] == "other_custodian" for r in sts) and bool(sts),
          f"each settlement carries the receiving custodian's details and settles T+1 ({want}, weekends only)",
          f"{[(str(r['trade_date']), str(r['settlement_date'])) for r in sts]}")
    check(ADAPTER_CALLS == [] and NETWORK_CALLS == [], "never routed to a custodian adapter / API (the account's "
          "custodian_code has a registered adapter; no submit was recorded)", f"{ADAPTER_CALLS}")
    iid = str(ins[0]["id"]) if ins else None
    cl, body, _ = await api_raw(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/settlement", {})
    d = await in_org(lambda c_: X.detail(c_, ORG_A, iid))
    rows = SL.settlement_rows(d, d["settlement"], account_display=d["account"]["account_number_masked"])
    raw = ACCOUNTS[ACCT_DVP][4]
    flat = " ".join(str(v) for _, v in rows)
    check(cl == 200 and body[:4] == b"%PDF" and raw not in flat and raw.encode() not in body
          and AccountNumber(raw).masked in flat and "****4321" in flat and "0901" in flat and str(want) in flat,
          "the DVP settlement instruction PDF (%PDF): masked numbers only, the receiving details and the settlement "
          "date", f"{cl} {body[:8]!r}")
    cl2, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/letter", {})
    check(cl2 == 422, "a broker's DVP instruction is not a letter of instruction (rep2's letter is for a member / "
          "other adviser)", f"{cl2}")
    # a manual instruction on the DVP account: broker → DVP settlement too; the integrated desk refused
    def body_for(executor):
        return {"account_id": str(ACCT_DVP), "trades": [{"global_security_id": str(S["sec_K"]), "side": "buy",
                                                          "quantity": "1", "lot_instructions": []}],
                "executor_type": executor, "executor_name": "ZZ Broker",
                "execute_by": (today() + timedelta(days=5)).isoformat(), "if_not_filled": "cancel"}
    cb, ob = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", body_for("broker"))
    cd, od = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", body_for("custodian_desk"))
    nid = ((ob.get("routing") or {}).get("instruction_ids") or [None])[0]
    st2 = await scoped(conn, ORG_A, "SELECT settlement_date, trade_date FROM public.instruction_settlements WHERE "
                                    "instruction_id = $1", nid, fetch="fetchrow") if nid else None
    check(cb == 201 and st2 is not None and st2["settlement_date"] == next_business_day(st2["trade_date"])
          and authority_refused(cd, od) and ADAPTER_CALLS == [],
          "a manual broker instruction on the account also carries its DVP settlement (T+1); the integrated "
          "custodian's desk is refused with the reason", f"{cb} {cd} {str(od)[:120]}")
    pool = await pool_()
    with _ctx(ORG_A):
        try:
            r = await HV.scan_account(pool, org_id=ORG_A, account_id=ACCT_DVP)
            hv = ("passed", r.get("action"))
        except Exception as exc:  # noqa: BLE001
            hv = ("refused" if exc_refused(exc) else "error", str(exc)[:120])
    check(hv[0] == "passed", "the harvest scan runs for a managed account at another custodian (not refused)",
          f"{hv}")
    acct = await effective_of(ACCT_DVP)
    check(RP.executor_for({"proposal_kind": "model", "advice_only": False, "trigger": "harvest", "trigger_ref": "{}"},
                          acct)[0] == "broker",
          "a harvest proposal on it routes to the same manual DVP path (executor broker)")
    find("harvest scenario", "the harvest proposal itself is proven at the guard, the scan and the executor; a full "
         "loss-harvest needs loss lots, prices and an opt-in link (rep6's own verify covers that machinery)")
    find("lots at the fill price", "rep2's fills record the price; lots of a held-away account arrive through the "
         "next approved held-away import (maker-checker), never written from a fill — no second write path")


async def y_matrix(conn):
    section("[Y] Matrix: every authority × custody cell, every path, allowed / refused on identical requests")
    cells = {ACCT_RO: ("read_only", "outside"), ACCT_ADV: ("advise", "outside"), ACCT_INT: ("managed", "integrated"),
             ACCT_DVP: ("managed", "other_custodian")}
    live = {a: (lambda e: (e["authority"], e["custody"]))(await effective_of(a)) for a in cells}
    check(live == cells, "four live cells set up through approved changes (read-only, advise, managed integrated, "
          "managed at another custodian)", f"{live}")
    pool = await pool_()
    results: dict = {}
    for acct in cells:
        out = {}
        cj, oj = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join", {"model_id": str(S["mK"]),
                                                                                       "settings": {}})
        out["join"] = not authority_refused(cj, oj)
        ct, ot = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{acct}/model/transition?model_id={S['mK']}")
        out["transition"] = not authority_refused(ct, ot)
        for trig in TRIGGERS:
            kw = {"maker_kind": "system"} if trig in ("drift", "model_change", "harvest") else {
                "maker_kind": "user", "maker_id": U_ADMIN1}
            if trig == "cash_flow":
                kw["withdrawal"] = D("100")
            with _ctx(ORG_A):
                try:
                    await RP.generate(pool, org_id=ORG_A, account_id=acct, trigger=trig, **kw)
                    out[f"proposal:{trig}"] = True
                except Exception as exc:  # noqa: BLE001
                    out[f"proposal:{trig}"] = not exc_refused(exc)
        with _ctx(ORG_A):
            try:
                await HV.scan_account(pool, org_id=ORG_A, account_id=acct)
                out["harvest"] = True
            except Exception as exc:  # noqa: BLE001
                out["harvest"] = not exc_refused(exc)
        co, oo = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/proposals/one-off",
                           {"lines": [{"global_security_id": str(S["sec_K"]), "side": "sell"}],
                            "executor_type": "member"})
        out["one_off"] = not authority_refused(co, oo)
        for ex in ("member", "broker", "custodian_desk"):
            ci, oi = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", {
                "account_id": str(acct), "trades": [{"global_security_id": str(S["sec_K"]), "side": "buy",
                                                     "quantity": "1", "lot_instructions": []}],
                "executor_type": ex, "executor_name": "ZZ Executor",
                "execute_by": (today() + timedelta(days=5)).isoformat(), "if_not_filled": "cancel"})
            out[f"instructions:{ex}"] = not authority_refused(ci, oi)
        a = await in_org(lambda c_, acct=acct: X.load_account(c_, ORG_A, acct))
        tl = TR.TradeList(account_id=acct, trades=[TR.TradeLine(global_security_id=S["sec_K"], side="buy",
                                                                quantity=D(1))],
                          executor_type="member", execute_by=today() + timedelta(days=5), if_not_filled="cancel")
        with _ctx(ORG_A):
            try:
                await TR.route_trade_list(pool, org_id=ORG_A, actor_id=U_ADMIN1, account=a, trade_list=tl)
                out["route"] = True
            except Exception as exc:  # noqa: BLE001
                out["route"] = not exc_refused(exc)
        results[acct] = out
    bad = []
    for acct, out in results.items():
        paths, execs = EXPECTED[cells[acct]]
        for k, ok in out.items():
            if k.startswith("instructions:"):
                want = "instructions" in paths and k.split(":")[1] in execs
            elif k == "route":
                want = "route" in paths and "member" in execs
            else:
                want = k in paths
            if ok != want:
                bad.append(f"{cells[acct]} {k}: {'allowed' if ok else 'refused'}, expected "
                           f"{'allowed' if want else 'refused'}")
    check(len(results) == 4 and all(len(o) == 14 for o in results.values()) and not bad,
          "every path (join, transition, each proposal trigger, harvest, one-off, instructions by executor, route) "
          "allowed / refused exactly as the matrix, on identical requests across the four live cells",
          "; ".join(bad[:6]))
    check(ADAPTER_CALLS == [] or all(not s.endswith((str(ACCT_ADV), str(ACCT_DVP), str(ACCT_RO)))
                                     for s in ADAPTER_CALLS),
          "no held-away account's trade list reached a custodian adapter", f"{ADAPTER_CALLS}")
    for acct in cells:
        _, env = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{acct}/model")
        acts = set(env.get("actions") or [])
        allowed = EXPECTED[cells[acct]][0]
        leaked = [a for a, p in (("join", "join"), ("change_model", "change_model"), ("change_settings", "settings"),
                                 ("generate", "proposal:manual"), ("propose_one_off", "one_off"))
                  if a in acts and p not in allowed]
        check(not leaked and ((env.get("registry") == []) or "join" in allowed),
              f"envelope for {cells[acct]}: lists only the actions the matrix allows", f"{sorted(acts)}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    cb, ob = await account_change(ACCT_B, {"authority_override": "advise", "custody": "outside"}, uid=U_ADMIN_B)
    res = {}
    for t in NEW_TABLES:
        res[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")),
                  int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")))
    b_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.account_authority_settings WHERE org_id = $1",
                           ORG_B, fetch="fetchval"))
    b_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.account_authority_settings WHERE org_id = $1",
                           ORG_B, fetch="fetchval"))
    check(cb == 201 and all(a > 0 and b == 0 for a, b in res.values()) and b_b > 0 and b_a == 0,
          "RLS: each org sees its own authority / settlement rows (non-empty) and none of the other's",
          f"{res} {b_b} {b_a}")
    bid = (ob.get("change") or {}).get("id")
    codes = [(await api(U_ADMIN_B, "GET", f"/api/v1/authority/accounts/{ACCT_ADV}"))[0],
             (await api(U_ADMIN_B, "GET", f"/api/v1/authority/households/{HH_ADV}"))[0],
             (await api(U_ADMIN1, "GET", f"/api/v1/authority/accounts/{ACCT_B}"))[0],
             (await api(U_ADMIN1, "POST", f"/api/v1/authority/changes/{bid}/approve", {}))[0]]
    check(codes == [404] * 4, "another org's account, household and change are a 404 both directions", f"{codes}")
    cv, ov = await household_change(HH_ADV, "read_only", uid=U_VIEWER)
    check(cv == 403 and "manage_authority" in str(ov), "gates both ways: the viewer's identical change request is "
          "refused naming manage_authority (an org admin's was accepted above)", f"{cv}")
    _, ev = await api(U_VIEWER, "GET", f"/api/v1/authority/accounts/{ACCT_ADV}")
    _, ea = await api(U_ADMIN1, "GET", f"/api/v1/authority/accounts/{ACCT_ADV}")
    pv = ev.get("permissions") or {}
    check(pv.get("can_write") is False and pv.get("can_approve") is False and ev.get("actions") == []
          and (ev.get("vocabularies") or {}).get("editable") == [] and ea.get("actions") == ["request_change"]
          and bool((ev.get("vocabularies") or {}).get("matrix")),
          "envelopes: view-only → no actions, empty editable; an admin may request; labels and the matrix in "
          "vocabularies", f"{ev.get('actions')} {ea.get('actions')}")
    _, hv = await api(U_VIEWER, "GET", f"/api/v1/held-away/accounts/{ACCT_ADV}")
    _, hm = await api(U_ADMIN1, "GET", f"/api/v1/held-away/accounts/{ACCT_RO}")
    check(hv.get("trade_actions") == [] and hm.get("trade_actions") == []
          and (hm.get("panel") or {}).get("trade_controls") is False,
          "held-away panel: no trade action for a viewer, none for a read-only account", "")
    missing = await platform(conn, """SELECT count(*) FROM roles r CROSS JOIN permissions p WHERE r.name = 'org_admin'
                                      AND p.name = ANY($1::text[]) AND NOT (r.org_id = ANY($2::uuid[]))
                                      AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                                                      AND rp.permission_id = p.id)""",
                             list(AUTH_PERMS), [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
    check(int(missing) == 0, "every real org_admin role holds the three authority permissions (fixture-org roles "
          "ignored)", f"{missing}")
    perms = await platform(conn, "SELECT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id JOIN "
                                 "role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id "
                                 "WHERE ur.user_id = $1", U_ADMIN_B)
    check(set(AUTH_PERMS) <= {r["name"] for r in perms}, "a new org's first org_admin holds view / manage / "
          "approve_authority (rbac.ensure_org_admin_role)")


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
    want = {
        "/api/v1/authority/vocabulary": "view_authority", "/api/v1/authority/households/{household_id}": "view_authority",
        "/api/v1/authority/accounts/{account_id}": "view_authority",
        "/api/v1/authority/households/{household_id}/changes": "manage_authority",
        "/api/v1/authority/accounts/{account_id}/changes": "manage_authority",
        "/api/v1/authority/changes/{change_id}/approve": "approve_authority",
        "/api/v1/authority/changes/{change_id}/reject": "approve_authority",
        "/api/v1/accounts/{account_id}/proposals/one-off": "manage_proposals",
        "/api/v1/xray/households/{household_id}/one-off": "manage_proposals",
        "/api/v1/execution/instructions/{instruction_id}/settlement": "view_execution",
        "/api/v1/execution/instructions/{instruction_id}/reconcile-held-away": "manage_execution",
    }
    new = [k for k in gn if k not in gb]
    bad = [(k, gn[k].get("after")) for k in new if want.get(k[1]) not in str(gn[k].get("after"))]
    check(len(new) == len(want) and not bad, f"{len(want)} new routes, each naming its permission", f"{len(new)} "
          f"{bad[:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^ok \d+ - (?:authority panel|custody options|one-off form|X-ray: an advise line)", out, re.M)
    check(len(mine) >= 6 and "not ok" not in out, "the rep7b render tests ran and passed (no authority-change / trade "
          "control the matrix does not allow)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep7.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s; only its TODAY fix "
            "changed")
    code, diff, _ = run_cmd(["git", "diff", "-U0", BEFORE_REF, "--", "apps/api/scripts/verify_rep7.py"], REPO, 60)
    changed = [ln for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    check(code == 0 and len(changed) == 2 and "TODAY + timedelta(days=STALE_DAYS)" in changed[0]
          and "date.today() + timedelta(days=STALE_DAYS)" in changed[1],
          "verify_rep7.py differs from main only by the expires_on expectation computed at the call", f"{changed}")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep7.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_rep7.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-200:] if not total else ''}")


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
        if S.get("mK"):
            await run("migration", y_migration, conn)
            await run("authority changes", y_changes, conn)
            await run("advise", y_advise, conn)
            await run("managed at another custodian", y_dvp, conn)
            await run("matrix", y_matrix, conn)
            await run("isolation + gates", y_isolation, conn)
        else:
            check(False, "seed created the fixture model", "every fixture-scoped section skipped")
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
            log = await teardown_retrying()
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
            if S.get("foreign_drafts"):
                now_ = await platform(conn, "SELECT id::text, status FROM public.execution_instructions WHERE id = "
                                            "ANY($1::uuid[]) AND system_to IS NULL",
                                      [i for i, _ in S["foreign_drafts"]])
                check(sorted((r["id"], r["status"]) for r in now_) == sorted(S["foreign_drafts"]),
                      "the existing held-away draft instruction(s) still exist, still draft", f"{[dict(r) for r in now_]}")
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
