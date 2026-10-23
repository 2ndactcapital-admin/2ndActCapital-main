"""verify_rep2 — Replication Studio, sprint 2 (REP2): custodian capabilities, trade
adapters, governed human tasks, execution instructions.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep2.py 2>&1 | grep -E "^\\[FAIL\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the real httpx network transport is replaced by one that records
and refuses — any attempt is a failed check. Nothing reaches Altruist.

REAL: the app (middleware, Principal, every gate, every handler) through an
ASGI transport, the workflow engine, RLS, and the live database. The script's
own connection is DATABASE_URL = app_service (rolbypassrls = false, asserted).

FIXTURES: three orgs (99000000-…-0000000e2001 / e2002 / e2003); users created
explicitly with the auth0_sub their token carries and an @test.local email
(is_test by trigger); test custodian codes (ZZ_REP2_*), masked numbers only;
fixture securities carry the reserved prefix ZZRQ (checked absent from the real
security master first); capability edits use the test custodian zz_rep2_*.

TEARDOWN (start and end): the operator DSN (role postgres) with
execution.teardown = 'fixture' in its transaction — the only path past the
immutability triggers — tables in FK child-before-parent order read from
pg_constraint, every DELETE keyed by fixture ids; then every counted table is
re-read for its exact pre-test count, and real accounts / tax lots / workflow
rows / capability rows are compared by count + checksum.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import inspect
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
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

BEFORE_REF = "ff61884"   # rep2-work base — main after the rep1 merge + schema refresh
TAG = "rep2verify"
PREFIX = "ZZRQ"
CAP_TEST = "zz_rep2_custodian"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FUND_NAMES = ("REP2 VERIFY ALPHA", "REP2 VERIFY BETA")
FULL_NUMBER = "ZZ12345678"          # the fixture account's full number — must never appear in a stored row
FULL_NUMBER_B = "ZZ87654321"
WRONG_NUMBER = "ZZ00000000"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e2{n:03d}")


ORG_A, ORG_B, ORG_C = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_C]
(U_SUPER, U_ADMIN1, U_ADMIN2, U_ADMIN3, U_STAFF, U_MEMBER, U_VIEWER, U_ADMIN_B, U_NEWADMIN) = (
    _id(110 + i) for i in range(9))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP2 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP2 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP2 Admin Checker"),
    U_ADMIN3: (ORG_A, "org_admin", "admin3", "org_admin", "REP2 Admin Third"),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", "REP2 Staff"),
    U_MEMBER: (ORG_A, "member", "member", "member", "REP2 Member"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep2_viewer", "REP2 Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP2 Org B Admin"),
    U_NEWADMIN: (ORG_C, "member", "newadmin", None, "REP2 New Org Admin"),
}
EXECUTION_PERMS = ("view_execution", "manage_execution", "approve_execution")
SIX_PERMS = EXECUTION_PERMS + ("view_replication", "manage_replication", "approve_replication")

ENT_A, ENT_B = _id(201), _id(202)
ACCT_ALT, ACCT_HELD, ACCT_OTHER, ACCT_BROKER, ACCT_B = _id(301), _id(302), _id(303), _id(304), _id(305)
ASSET_A, ASSET_B, ASSET_HELD = _id(401), _id(402), _id(403)
POS_A, POS_B, POS_HELD = _id(411), _id(412), _id(413)
LOT_A1, LOT_A2, LOT_B1, LOT_HELD1 = _id(421), _id(422), _id(423), _id(424)
CODE_ALT, CODE_OTHER, CODE_B = "ZZ_REP2_ALTRUIST", "ZZ_REP2_OTHER", "ZZ_REP2_BORG"
TODAY = date.today()

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.domain_event_deliveries",
    "public.agent_proposals", "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.accounts", "public.org_settings",
    "portfolio.assets", "portfolio.positions", "portfolio.tax_lots", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "public.custodian_capabilities", "public.execution_instructions",
    "public.execution_instruction_lots", "public.execution_fills", "public.execution_instruction_events",
)

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
    return {
        "accounts": await fingerprint(conn, "SELECT * FROM public.accounts WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "tax_lots": await fingerprint(conn, "SELECT * FROM portfolio.tax_lots WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "workflow_definitions": await fingerprint(
            conn, "SELECT * FROM public.workflow_definitions WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "workflow_versions": await fingerprint(
            conn, "SELECT * FROM public.workflow_versions WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "workflow_steps": await fingerprint(
            conn, "SELECT * FROM public.workflow_steps WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "workflow_runs": await fingerprint(
            conn, "SELECT * FROM public.workflow_runs WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "workflow_run_steps": await fingerprint(
            conn, "SELECT * FROM public.workflow_run_steps WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "custodian_capabilities": await fingerprint(
            conn, "SELECT * FROM public.custodian_capabilities WHERE custodian_code NOT LIKE 'zz_rep2%'"),
        "securities_global": await fingerprint(
            conn, "SELECT * FROM portfolio.securities_global WHERE NOT (name = ANY($1::text[]))", list(FUND_NAMES)),
    }


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
    edges = {t: set() for t in tables}            # t -> parents of t inside the set
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
    return [r["global_security_id"] for r in rows]


async def teardown(_conn=None) -> list[str]:
    """Operator connection, execution.teardown = 'fixture', FK child-before-parent,
    every DELETE keyed by this run's fixture ids."""
    log: list[str] = []
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('execution.teardown', 'fixture', true)")
            secs = await fixture_security_ids(op)
            users = list(USERS)
            orgs = FIXTURE_ORGS
            by_org = "org_id = ANY($1::uuid[])"
            plan = {
                "public.execution_instruction_events": (by_org, [orgs]),
                "public.execution_fills": (by_org, [orgs]),
                "public.execution_instruction_lots": (by_org, [orgs]),
                "public.execution_instructions": (by_org, [orgs]),
                "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
                "public.domain_event_deliveries": (by_org, [orgs]),
                "public.domain_events": (by_org, [orgs]),
                "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
                "public.workflow_run_steps": (by_org, [orgs]),
                "public.agent_proposals": (by_org, [orgs]),
                "public.workflow_runs": (by_org, [orgs]),
                "public.workflow_steps": (by_org, [orgs]),
                "public.workflow_versions": (by_org, [orgs]),
                "public.workflow_definitions": (by_org, [orgs]),
                "public.custodian_capabilities": ("custodian_code LIKE 'zz_rep2%'", []),
                "portfolio.tax_lots": (by_org, [orgs]),
                "portfolio.positions": (by_org, [orgs]),
                "portfolio.assets": (by_org, [orgs]),
                "public.accounts": (by_org, [orgs]),
                "public.entities": (by_org, [orgs]),
                "public.org_settings": (by_org, [orgs]),
                "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
            }
            exists = {r["t"] for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind = 'r'")}
            if "public.assistant_activities" in exists:
                plan["public.assistant_activities"] = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
                                                       [orgs, users])
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
        ("SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE 'zz_rep2%'", []),
        *[(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", [orgs]) for t in (
            "public.execution_instructions", "public.execution_instruction_lots", "public.execution_fills",
            "public.execution_instruction_events", "public.workflow_definitions", "public.workflow_runs",
            "public.workflow_run_steps", "public.member_todos", "public.domain_events", "public.audit_log",
            "public.accounts", "public.entities", "portfolio.tax_lots", "portfolio.positions", "portfolio.assets",
            "public.org_settings", "public.agent_proposals")],
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
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep2"}}
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
    import main
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep2: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    """RLS contextvars for a direct service call (what the middleware sets)."""

    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        from services.database import set_rls_context

        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        from services.database import reset_rls_context

        reset_rls_context(self.tokens)
        return False


def body_for(account, security, *, side="sell", quantity="10", lots=None, executor="member", execute_by=None,
             if_not_filled="cancel", price=None, **extra):
    t = {"global_security_id": str(security), "side": side, "quantity": quantity,
         "price_guidance": price or {"kind": "limit", "limit_price": "101.25"},
         "lot_instructions": lots or []}
    b = {"account_id": str(account), "trades": [t], "executor_type": executor,
         "executor_name": "Pat Example", "executor_contact": "pat@example.invalid",
         "execute_by": (execute_by or (TODAY + timedelta(days=7))).isoformat(), "if_not_filled": if_not_filled}
    b.update(extra)
    return b


async def create(uid, account, security, **kw):
    code, out = await api(uid, "POST", "/api/v1/execution/instructions", body_for(account, security, **kw))
    ids = out.get("routing", {}).get("instruction_ids") or [] if code == 201 else []
    return code, out, (ids[0] if ids else None)


async def instr_row(conn, org, iid):
    return await scoped(conn, org, "SELECT * FROM public.execution_instructions WHERE id = $1::uuid "
                                   "AND system_to IS NULL", iid, fetch="fetchrow")


async def events_of(conn, org, iid) -> list:
    return await scoped(conn, org, "SELECT id, action, from_status, to_status, actor_id, detail FROM "
                                   "public.execution_instruction_events WHERE instruction_id = $1::uuid "
                                   "ORDER BY created_at, id", iid)


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings (1a–1d) — files, the engine change and its effect on existing runs")
    import services.custody as CU
    from services import altruist_oauth as AO
    from services.custody.trading import AltruistTradeAdapter, ManualTradeAdapter

    find("1a registry", "services/custody/registry.py: resolve_profile(conn, org_id, custodian_code) reads "
         "org_settings['custody.profiles'] over DEFAULT_PROFILES (GENERIC_CSV) and maps the profile's 'adapter' key "
         "to a class registered by import side effect (custody/__init__.py). rep2 adds a SEPARATE map in the same "
         "module (register_trade_adapter / trade_adapter_for / trade_custodian_key) keyed by lower-case custodian "
         "route; the CSV map, build_adapter, csv_adapter.py and importer.py are unchanged")
    check(CU.registered_adapters() == ["csv"], "ingestion adapters unchanged: ['csv']", f"{CU.registered_adapters()}")
    check(CU.registered_trade_adapters() == ["altruist", "manual"]
          and CU.trade_adapter_for("ALTRUIST") is AltruistTradeAdapter
          and CU.trade_adapter_for("manual") is ManualTradeAdapter and CU.trade_adapter_for("GENERIC_CSV") is None,
          "trade adapters registered in the same registry module: altruist, manual; a CSV profile code has none")
    codes = await platform(conn, "SELECT custodian_code, count(*) AS n FROM public.accounts WHERE NOT (org_id = "
                                 "ANY($1::uuid[])) AND system_to IS NULL GROUP BY 1 ORDER BY 2 DESC",
                           [str(o) for o in FIXTURE_ORGS])
    find("1a custodian codes in public.accounts today (counts only)",
         f"{[(r['custodian_code'], int(r['n'])) for r in codes] or 'none — the table holds no real accounts'}")
    find("1b engine", "services/workflow_engine.py: a SYSTEM definition 'system:human_task:<permission>' is seeded "
         "per org on first use (services/human_tasks.ensure_definition, advisory-locked) and started with "
         "start_workflow_run(pool, version, org, context, started_by); the subject rides in workflow_runs.context."
         "human_task; maker != checker is complete_user_task's MakerCheckerError (proposed_by == completed_by) backed "
         "by the workflow_run_steps_maker_checker CHECK; todos attach through workflow_todos.sync_user_task_todos "
         "(source 'workflow_user_task', related_id = the run step)")
    find("1b engine change (additive)", "workflow_steps.assigned_permission (nullable, no default): the engine "
         "selects it, notifies admitted holders of it except the maker, and refuses a completer who lacks it; plus "
         "cancel_user_task. EFFECT ON EXISTING RUNS: none — every pre-existing step has NULL, which takes exactly the "
         "old path (profile todos, any completer)")
    col = await platform(conn, "SELECT is_nullable, column_default FROM information_schema.columns WHERE "
                               "table_name = 'workflow_steps' AND column_name = 'assigned_permission'", fetch="fetchrow")
    pre = await platform(conn, "SELECT count(*) FROM workflow_steps ws JOIN workflow_versions v ON v.id = "
                               "ws.workflow_version_id JOIN workflow_definitions d ON d.id = v.workflow_definition_id "
                               "WHERE ws.assigned_permission IS NOT NULL AND d.name NOT LIKE 'system:human_task:%'",
                         fetch="fetchval")
    check(col is not None and col["is_nullable"] == "YES" and col["column_default"] is None and int(pre) == 0,
          "engine column is additive: nullable, no default, NULL on every non-system step", f"{pre}")
    lot_cols = {r["column_name"] for r in await platform(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' AND table_name = "
              "'tax_lots'")}
    find("1c tax lots", f"portfolio.tax_lots columns {sorted(lot_cols)}; a lot ties to an account through "
         "position_id → portfolio.positions.account_id and to a security through positions.asset_id → "
         "portfolio.assets.global_security_id (no account or security column on the lot itself)")
    check({"position_id", "quantity", "acquired_date", "status", "term"} <= lot_cols and "account_id" not in lot_cols,
          "tax_lots reaches account/security only through positions/assets")
    sig = inspect.signature(AO.build_authorize_url)
    acct = AO._normalize_account({"id": "x", "name": "y", "options_level": 4, "margin": True})
    pos = AO._normalize_position({"id": "p", "cost_basis": "1"})
    src = "".join((API_DIR / "services" / f).read_text(encoding="utf-8") for f in (
        "altruist_oauth.py", "altruist_identity.py", "altruist_positions_sync.py", "altruist_webhook.py",
        "altruist_one.py", "portfolio_altruist.py"))
    no_trade = not re.search(r"/(orders?|trades?)\b", src)
    check(sig.parameters["scope"].default == "accounts:read" and set(acct) == {"id", "name", "raw"}
          and "cost_basis" in pos and not any("lot" in k for k in pos) and no_trade,
          "1d evidence: OAuth scope accounts:read; mocked account payload carries no options/margin field; "
          "position cost basis is position-level; no order/trade endpoint in any Altruist module")
    find("1d Altruist capabilities", "EVIDENCE: scope accounts:read (altruist_oauth.build_authorize_url); mocked "
         "account = id + name; positions carry a position-level cost_basis only; the fee37 rate card lists margin "
         "spreads and fee38 an Altruist One TLH benefit — both UNVERIFIED (fee37 F6) and about the product, not an "
         "account. UNKNOWN: every capability for an account — all 13 seeded 'unknown' with that evidence cited")


async def seed(conn) -> None:
    from services import rbac
    from services import securities_global as SG
    from services.custody.base import AccountNumber
    from services.custody.registry import get_or_create_salt

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_C, "c")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"REP2 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for org, rname in ((ORG_A, "member"), (ORG_A, "investment_staff"), (ORG_A, "super_admin")):
            desc, perms = C.BASELINE_ROLES[rname]
            rid = await rbac.ensure_role(conn, org, rname, desc)
            for p in perms:
                if p in perm_ids:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid
        rid = await rbac.ensure_role(conn, ORG_A, "rep2_viewer", "REP2 verify — view_execution only")
        await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid,
                           perm_ids["view_execution"])
        roles[(ORG_A, "rep2_viewer")] = rid
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
                               "'individual', $3)", ent, org, f"REP2 Verify Client {str(org)[-1]}")
        for name, cusip, ticker in (("REP2 VERIFY ALPHA", "ZZRQ00011", "ZZRQA"), ("REP2 VERIFY BETA", "ZZRQ00012",
                                                                                 "ZZRQB")):
            sid = await SG.create_security(conn, name=name, security_type="equity", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=cusip, is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=ticker,
                                    is_super_admin=True)
            S[f"sec_{ticker}"] = UUID(str(sid))
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        salt_a = await get_or_create_salt(conn, ORG_A)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_B))
        salt_b = await get_or_create_salt(conn, ORG_B)
        accts = (
            (ACCT_ALT, ORG_A, ENT_A, CODE_ALT, FULL_NUMBER, False, salt_a),
            (ACCT_HELD, ORG_A, ENT_A, CODE_ALT, "ZZ11112222", True, salt_a),
            (ACCT_OTHER, ORG_A, ENT_A, CODE_OTHER, "ZZ33334444", False, salt_a),
            (ACCT_BROKER, ORG_A, ENT_A, CODE_OTHER, "ZZ55556666", False, salt_a),
            (ACCT_B, ORG_B, ENT_B, CODE_B, FULL_NUMBER_B, False, salt_b),
        )
        for aid, org, ent, code, raw, held, salt in accts:
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, $3, $4, $5, "
                "'individual', 'taxable', $6, $7)", aid, org, n.masked, n.hashed(salt), code, ent, held)
        sa, sb = S["sec_ZZRQA"], S["sec_ZZRQB"]
        for asset, org, sid, nm in ((ASSET_A, ORG_A, sa, "REP2 VERIFY ALPHA"), (ASSET_B, ORG_A, sb, "REP2 VERIFY BETA"),
                                    (ASSET_HELD, ORG_A, sa, "REP2 VERIFY ALPHA (held)")):
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", asset, org, sid, nm)
        for pos, asset, acct in ((POS_A, ASSET_A, ACCT_ALT), (POS_B, ASSET_B, ACCT_ALT), (POS_HELD, ASSET_HELD,
                                                                                         ACCT_HELD)):
            await conn.execute("INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, "
                               "quantity, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, 100, "
                               "'manual', 'manual', $6)", pos, ORG_A, ENT_A, asset, TODAY, acct)
        for lot, pos, qty, acq, term in ((LOT_A1, POS_A, 60, date(2020, 1, 2), "long"),
                                         (LOT_A2, POS_A, 40, TODAY - timedelta(days=30), "short"),
                                         (LOT_B1, POS_B, 100, date(2021, 3, 4), "long"),
                                         (LOT_HELD1, POS_HELD, 100, date(2022, 5, 6), "long")):
            await conn.execute("INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, "
                               "adjusted_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $7, 'open')",
                               lot, ORG_A, pos, acq, Decimal(qty), Decimal(qty) * Decimal("90.5"), term)


async def y_constraints(conn):
    section("[Y] Constraints of the touched tables read from the catalog (seed satisfies them)")
    tables = ("public.accounts", "public.entities", "portfolio.assets", "portfolio.positions", "portfolio.tax_lots",
              "public.execution_instructions", "public.execution_instruction_lots", "public.execution_fills",
              "public.execution_instruction_events", "public.custodian_capabilities", "public.workflow_run_steps")
    seen = 0
    for t in tables:
        cons = await platform(conn, "SELECT conname, contype::text AS k FROM pg_constraint WHERE conrelid = "
                                    "$1::regclass AND contype IN ('c', 'u', 'f')", t)
        idx = await platform(conn, "SELECT indexname FROM pg_indexes WHERE schemaname || '.' || tablename = $1 "
                                   "AND indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%WHERE%'", t)
        seen += len(cons)
        print(f"[INFO] {t}: {len(cons)} CHECK/UNIQUE/FK, partial uniques {[r['indexname'] for r in idx]}")
    check(seen > 20, "CHECK / UNIQUE / FK constraints read for every touched table", f"{seen}")


async def y_human_tasks(conn):
    section("[Y] Human tasks: open/notify/complete; maker-checker on the identical request; reject; engine "
            "records + events + audit; agent refused; existing workflows unchanged")
    from services import human_tasks as HT
    from services import workflow_engine as E
    from services.database import get_pool

    HT.register_kind("rep2verify_check", label="Verify check", outcomes=("approve", "reject"), maker_checker=True)
    pool = await get_pool()
    subject = uuid.uuid4()
    with _ctx(ORG_A):
        t = await HT.open_task(pool, org_id=ORG_A, kind="rep2verify_check", subject_type="rep2verify_subject",
                               subject_id=subject, maker_id=U_ADMIN1, eligible_permission="approve_execution",
                               title="REP2 verify — approve the subject", payload={"x": 1},
                               action_path="/portfolio/instructions")
    tid = t["id"]
    holders = {str(r["id"]) for r in await scoped(conn, ORG_A, """
        SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id
        JOIN role_permissions rp ON rp.role_id = ur.role_id JOIN permissions p ON p.id = rp.permission_id
        WHERE u.org_id = $1 AND p.name = 'approve_execution'""", ORG_A)}
    todos = await scoped(conn, ORG_A, "SELECT user_id, status, title, action_key FROM member_todos WHERE "
                                      "related_type = 'workflow_run_step' AND related_id = $1::uuid", tid)
    got = {str(r["user_id"]) for r in todos}
    check(bool(got) and got == holders - {str(U_ADMIN1)} == {str(U_ADMIN2), str(U_ADMIN3)}
          and all(r["status"] == "open" and r["title"].startswith("REP2 verify") for r in todos),
          "open → todos for exactly the eligible holders other than the maker (no staff / member / viewer)",
          f"{sorted(got)}")
    run = await scoped(conn, ORG_A, "SELECT r.*, d.name, ws.assigned_permission, rs.status AS step_status, "
                                    "rs.proposed_by FROM workflow_run_steps rs JOIN workflow_runs r ON r.id = "
                                    "rs.workflow_run_id JOIN workflow_steps ws ON ws.id = rs.workflow_step_id JOIN "
                                    "workflow_versions v ON v.id = r.workflow_version_id JOIN workflow_definitions d "
                                    "ON d.id = v.workflow_definition_id WHERE rs.id = $1::uuid", tid, fetch="fetchrow")
    ctx = json.loads(run["context"]) if isinstance(run["context"], str) else run["context"]
    check(run["name"] == "system:human_task:approve_execution" and run["assigned_permission"] == "approve_execution"
          and run["step_status"] == "active" and run["proposed_by"] == U_ADMIN1 and run["started_by"] == U_ADMIN1
          and ctx["human_task"]["subject_id"] == str(subject),
          "engine records: system definition run, step active, proposed_by = maker, subject in run context")
    try:
        with _ctx(ORG_A):
            await HT.complete_task(pool, org_id=ORG_A, task_id=tid, actor_id=U_ADMIN1, outcome="approve")
        check(False, "the maker's approve is refused")
    except HT.MakerCheckerError as exc:
        st = await scoped(conn, ORG_A, "SELECT status, approved_by FROM workflow_run_steps WHERE id = $1::uuid", tid,
                          fetch="fetchrow")
        check("maker-checker" in str(exc) and st["status"] == "active" and st["approved_by"] is None,
              "the maker's approve is refused naming maker-checker; the task stays open", str(exc)[:120])
    try:
        with _ctx(ORG_A):
            await HT.complete_task(pool, org_id=ORG_A, task_id=tid, actor_id=U_STAFF, outcome="approve")
        check(False, "a non-holder's approve is refused")
    except HT.HumanTaskPermissionError as exc:
        check("approve_execution" in str(exc), "a non-holder (staff) is refused naming approve_execution")
    try:
        with _ctx(ORG_A):
            await E.complete_user_task(pool, UUID(tid), U_STAFF, {"outcome": "approve"})
        check(False, "the ENGINE refuses a non-holder too")
    except E.WorkflowPermissionError:
        check(True, "the engine itself refuses a completer without the step's assigned_permission")
    for kw, label in (({"actor_kind": "agent", "actor_id": U_ADMIN2}, "actor_kind agent"),
                      ({"actor_id": "portfolio_monitor"}, "an agent key as the actor")):
        try:
            with _ctx(ORG_A):
                await HT.complete_task(pool, org_id=ORG_A, task_id=tid, outcome="approve", **kw)
            check(False, f"agent refused on complete ({label})")
        except HT.AgentActorRefused:
            check(True, f"an agent cannot complete a task ({label})")
    try:
        with _ctx(ORG_A):
            await HT.open_task(pool, org_id=ORG_A, kind="rep2verify_check", subject_type="rep2verify_subject",
                               subject_id=uuid.uuid4(), maker_id=U_ADMIN1, eligible_permission="approve_execution",
                               title="REP2 verify — agent", actor_kind="agent")
        check(False, "agent refused on open")
    except HT.AgentActorRefused:
        check(True, "an agent cannot open a task")
    with _ctx(ORG_A):
        done = await HT.complete_task(pool, org_id=ORG_A, task_id=tid, actor_id=U_ADMIN2, outcome="approve",
                                      note="looks right")
    st = await scoped(conn, ORG_A, "SELECT rs.status, rs.approved_by, rs.result, r.status AS run_status FROM "
                                   "workflow_run_steps rs JOIN workflow_runs r ON r.id = rs.workflow_run_id WHERE "
                                   "rs.id = $1::uuid", tid, fetch="fetchrow")
    res = json.loads(st["result"]) if isinstance(st["result"], str) else st["result"]
    open_left = await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE related_id = $1::uuid AND "
                                          "status = 'open'", tid, fetch="fetchval")
    check(done["status"] == "completed" and st["status"] == "completed" and st["approved_by"] == U_ADMIN2
          and res.get("outcome") == "approve" and st["run_status"] == "completed" and int(open_left) == 0,
          "the IDENTICAL request by a different approver succeeds: step completed, approved_by, run completed, "
          "todos closed (re-read)")
    with _ctx(ORG_A):
        t2 = await HT.open_task(pool, org_id=ORG_A, kind="rep2verify_check", subject_type="rep2verify_subject",
                                subject_id=uuid.uuid4(), maker_id=U_ADMIN1, eligible_permission="approve_execution",
                                title="REP2 verify — reject me")
        try:
            await HT.complete_task(pool, org_id=ORG_A, task_id=t2["id"], actor_id=U_ADMIN3, outcome="reject")
            check(False, "a rejection without a note is refused")
        except HT.HumanTaskError:
            check(True, "a rejection without a note is refused")
        r2 = await HT.complete_task(pool, org_id=ORG_A, task_id=t2["id"], actor_id=U_ADMIN3, outcome="reject",
                                    note="wrong account")
    check(r2["status"] == "completed" and r2["outcome"] == "reject" and r2["completed_by"] == str(U_ADMIN3),
          "reject path: completed with outcome reject by the checker")
    with _ctx(ORG_A):
        async with pool.acquire() as pc:
            hist = await HT.task_history(pc, ORG_A, tid)
    evs = {e["event_type"] for e in hist["events"]}
    auds = {a["action"] for a in hist["audit"]}
    db_evs = {r["event_type"] for r in await scoped(conn, ORG_A, "SELECT event_type FROM domain_events WHERE "
                                                                 "source_type = 'workflow_run_step' AND source_id = "
                                                                 "$1::uuid", tid)}
    check({"human_task.opened", "human_task.completed"} <= evs == db_evs
          and {"human_task.opened", "human_task.completed"} <= auds and len(hist["steps"]) == 1
          and hist["run"]["status"] == "completed",
          "events + audit + engine run/step records re-read for the task", f"{sorted(evs)} {sorted(auds)}")
    with _ctx(ORG_A):
        t3 = await HT.open_task(pool, org_id=ORG_A, kind="rep2verify_check", subject_type="rep2verify_subject",
                                subject_id=uuid.uuid4(), maker_id=U_ADMIN1, eligible_permission="approve_execution",
                                title="REP2 verify — cancel me")
        c3 = await HT.cancel_task(pool, org_id=ORG_A, task_id=t3["id"], actor_id=U_ADMIN1, reason="subject withdrawn")
    rs3 = await scoped(conn, ORG_A, "SELECT rs.status, r.status AS rstat FROM workflow_run_steps rs JOIN workflow_runs "
                                    "r ON r.id = rs.workflow_run_id WHERE rs.id = $1::uuid", t3["id"], fetch="fetchrow")
    open3 = await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE related_id = $1::uuid AND status = "
                                      "'open'", t3["id"], fetch="fetchval")
    check(c3["status"] == "cancelled" and rs3["status"] == "cancelled" and rs3["rstat"] == "cancelled"
          and int(open3) == 0, "cancel: step + run cancelled on the engine, todos closed")
    with _ctx(ORG_A):
        async with pool.acquire() as pc:
            d1 = await HT.ensure_definition(pc, ORG_A, "approve_execution")
            d2 = await HT.ensure_definition(pc, ORG_A, "approve_execution")
    ndef = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_definitions WHERE org_id = $1 AND name = "
                                     "'system:human_task:approve_execution'", ORG_A, fetch="fetchval")
    check(d1 == d2 and int(ndef) == 1, "the system definition is created once per org (idempotent)")
    # An EXISTING-style workflow (no assigned_permission) behaves exactly as before.
    xml = ('<?xml version="1.0" encoding="UTF-8"?><bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/'
           'MODEL" id="D_rep2v" targetNamespace="http://x"><bpmn:process id="rep2v" isExecutable="true">'
           '<bpmn:startEvent id="s"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent><bpmn:userTask id="review" '
           'name="Review"><bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:userTask>'
           '<bpmn:endEvent id="e"><bpmn:incoming>f2</bpmn:incoming></bpmn:endEvent><bpmn:sequenceFlow id="f1" '
           'sourceRef="s" targetRef="review"/><bpmn:sequenceFlow id="f2" sourceRef="review" targetRef="e"/>'
           '</bpmn:process></bpmn:definitions>')
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        did = await conn.fetchval("INSERT INTO workflow_definitions (org_id, name) VALUES ($1, 'REP2 verify legacy') "
                                  "RETURNING id", ORG_A)
        vid = await conn.fetchval("INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, "
                                  "bpmn_xml, is_current) VALUES ($1, $2, 1, $3, true) RETURNING id", did, ORG_A, xml)
        await conn.execute("INSERT INTO workflow_steps (workflow_version_id, org_id, step_key, step_type, "
                           "display_name) VALUES ($1, $2, 'review', 'user', 'Review')", vid, ORG_A)
    with _ctx(ORG_A):
        r = await E.start_workflow_run(pool, vid, ORG_A, {}, U_ADMIN1)
    rsid = await scoped(conn, ORG_A, "SELECT rs.id FROM workflow_run_steps rs WHERE rs.workflow_run_id = $1 AND "
                                     "rs.status = 'active'", r["run_id"], fetch="fetchval")
    n_todo = await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE related_id = $1", rsid, fetch="fetchval")
    try:
        with _ctx(ORG_A):
            await E.complete_user_task(pool, rsid, U_ADMIN1, {})
        legacy_mc = False
    except E.MakerCheckerError:
        legacy_mc = True
    with _ctx(ORG_A):
        out = await E.complete_user_task(pool, rsid, U_STAFF, {})
    check(r["status"] == "running" and r["paused_at"] == "review" and int(n_todo) == 0 and legacy_mc
          and out["run_status"] == "completed",
          "an existing-style workflow (no assigned_permission) behaves exactly as before: pauses, no profile → no "
          "todos, maker refused, ANY other completer accepted (no permission check)")


async def y_capabilities(conn):
    section("[Y] Capabilities: versioned, Super Admin only edits, 'unknown' = unavailable, evidence recorded")
    from services import custodian_capabilities as CC

    code, out = await api(U_STAFF, "GET", "/api/v1/custodians/capabilities")
    rows = {r["custodian_code"]: r for r in out.get("rows", [])} if code == 200 else {}
    alt = {c["capability_key"]: c for c in rows.get("altruist", {}).get("cells", [])}
    man = {c["capability_key"]: c for c in rows.get("manual", {}).get("cells", [])}
    check(code == 200 and len(alt) == 13 and all(c["value"] == "unknown" and c["available"] is False
                                                  for c in alt.values()),
          "staff read: Altruist has all 13 capabilities 'unknown' and NOT available", f"{code}")
    check(man.get("equity_trading_api", {}).get("value") == "no" and man.get("model_api", {}).get("value") == "no",
          "manual: trade APIs 'no'")
    check(all((c.get("evidence") or "").strip() and c.get("as_of") and c.get("evidence_kind") in
              ("document", "url", "assumed") for c in list(alt.values()) + list(man.values())),
          "every seeded capability carries evidence, its kind and an as-of date")
    check(out.get("permissions", {}).get("can_write") is False and out.get("vocabularies", {}).get("editable") == [],
          "staff envelope: read-only (can_write false, editable [])")
    code_m, out_m = await api(U_MEMBER, "GET", "/api/v1/custodians/capabilities")
    code_mh, _ = await api(U_MEMBER, "GET", "/api/v1/custodians/capabilities/altruist/margin/history")
    check(code_m == 403 and code_mh == 403 and "rows" not in out_m,
          "a member (neither staff nor manage_org_settings) is refused: matrix AND history 403, no envelope",
          f"{code_m} {code_mh}")
    # Joe, 2026-10-23: read = staff OR manage_org_settings — an org admin reads; writing stays Super Admin only.
    code_oa, out_oa = await api(U_ADMIN1, "GET", "/api/v1/custodians/capabilities")
    code_oah, _ = await api(U_ADMIN1, "GET", "/api/v1/custodians/capabilities/altruist/margin/history")
    check(code_oa == 200 and code_oah == 200 and len(out_oa.get("rows", [])) == len(rows) > 0
          and out_oa.get("permissions", {}).get("can_write") is False
          and out_oa.get("vocabularies", {}).get("editable") == [],
          "org admin (manage_org_settings) reads the matrix and history (200), read-only envelope (can_write "
          "false, editable [])", f"{code_oa} {code_oah}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
        cap = await CC.capability(conn, "altruist", "equity_trading_api")
        missing = await CC.capability(conn, "zz_rep2_nobody", "margin")
    check(cap.value == "unknown" and CC.available(cap) is False and CC.available("unknown") is False
          and CC.available("yes") is True and CC.available("0") is False and CC.available("2", at_least=2) is True
          and missing.value == "unknown" and CC.available(missing) is False,
          "capability() helper: 'unknown' (and no record) are NOT available; only an evidenced positive is")
    body = {"value": "yes", "evidence_kind": "url", "evidence": "https://example.invalid/rep2-doc",
            "as_of": TODAY.isoformat()}
    before = await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1", CAP_TEST,
                            fetch="fetchval")
    code_a, out_a = await api(U_ADMIN1, "PUT", f"/api/v1/custodians/capabilities/{CAP_TEST}/margin", body)
    after_a = await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1", CAP_TEST,
                             fetch="fetchval")
    check(code_a == 403 and int(after_a) == int(before), "org admin edit → 403, nothing written", f"{code_a}")
    code1, o1 = await api(U_SUPER, "PUT", f"/api/v1/custodians/capabilities/{CAP_TEST}/margin", body)
    code2, o2 = await api(U_SUPER, "PUT", f"/api/v1/custodians/capabilities/{CAP_TEST}/margin",
                          {**body, "value": "no", "evidence_kind": "document", "evidence": "rep2 verify letter"})
    hist = await platform(conn, "SELECT version_no, value, valid_to, recorded_by FROM custodian_capabilities WHERE "
                                "custodian_code = $1 AND capability_key = 'margin' ORDER BY version_no", CAP_TEST)
    check(code1 == 200 and code2 == 200 and [h["version_no"] for h in hist] == [1, 2]
          and hist[0]["valid_to"] is not None and hist[1]["valid_to"] is None and hist[0]["value"] == "yes"
          and hist[1]["value"] == "no" and all(h["recorded_by"] == U_SUPER for h in hist),
          "Super Admin edits: each a NEW version, the old one kept (closed), recorder recorded", f"{code1} {code2}")
    aud = await platform(conn, "SELECT count(*) FROM audit_log WHERE action = 'custodian_capability.set' AND user_id "
                               "= $1", U_SUPER, fetch="fetchval")
    check(int(aud) == 2, "every edit is logged (audit_log)", f"{aud}")
    code_bad, _ = await api(U_SUPER, "PUT", f"/api/v1/custodians/capabilities/{CAP_TEST}/options_level",
                            {**body, "value": "yes"})
    check(code_bad == 422, "options_level takes 0-4 or unknown, not yes (422)", f"{code_bad}")
    try:
        await scoped(conn, ORG_A, "INSERT INTO custodian_capabilities (custodian_code, capability_key, value, "
                                  "evidence_kind, evidence, as_of, version_no) VALUES ($1, 'margin', 'yes', 'assumed', "
                                  "'x', now()::date, 9)", CAP_TEST, fetch="execute")
        check(False, "an org connection cannot write platform capabilities")
    except Exception as exc:  # noqa: BLE001
        check("row-level security" in str(exc), "an org connection cannot write platform capabilities (RLS)",
              str(exc)[:90])
    try:
        await platform(conn, "UPDATE custodian_capabilities SET value = 'yes' WHERE custodian_code = $1 AND "
                             "capability_key = 'margin' AND valid_to IS NULL", CAP_TEST, fetch="execute")
        check(False, "a version is never edited in place")
    except Exception as exc:  # noqa: BLE001
        check("immutable" in str(exc), "a version is never edited in place (trigger), even by Super Admin",
              str(exc)[:90])
    na = await scoped(conn, ORG_A, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code IN "
                                   "('altruist', 'manual')", fetch="fetchval")
    nb = await scoped(conn, ORG_B, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code IN "
                                   "('altruist', 'manual')", fetch="fetchval")
    check(int(na) == int(nb) >= 26, "platform capabilities are readable by both orgs", f"{na}/{nb}")
    S["cap_envelopes"] = {"staff": out, "super": (await api(U_SUPER, "GET", "/api/v1/custodians/capabilities"))[1]}
    code_oa, _ = await api(U_ADMIN1, "GET", "/api/v1/custodians/capabilities")
    find("capabilities read gate is require_staff (as specified)", f"an org admin who holds no STAFF_ROLE_NAMES role "
         f"gets {code_oa} — org_admin is not a staff role, so the Billing & Custody group's own admins do not see the "
         "matrix unless they also hold a staff role; widening it is a product decision, not taken here")


async def y_routing(conn):
    section("[Y] Trade routing: Altruist (unknown API) and held-away → manual; extra fields refused")
    from services.custody.registry import register_trade_adapter
    from services.custody.trading import AltruistTradeAdapter

    class FixtureAltruist(AltruistTradeAdapter):
        """The real Altruist adapter under the fixture custodian code — reads
        the REAL 'altruist' capability row (capability_custodian)."""

    register_trade_adapter(CODE_ALT, FixtureAltruist)
    check(FixtureAltruist.capability_custodian == "altruist" and AltruistTradeAdapter.custodian_key == "altruist",
          "the fixture route reads Altruist's real capability row; the real registration is untouched")
    sa = S["sec_ZZRQA"]
    code, out, iid = await create(U_ADMIN1, ACCT_ALT, sa, side="buy", quantity="5")
    r = out.get("routing", {})
    row = await instr_row(conn, ORG_A, iid) if iid else None
    check(code == 201 and r.get("adapter") == "altruist" and r.get("routed_to") == "manual"
          and "'unknown'" in (r.get("reason") or "") and r.get("sent_externally") is False and row is not None
          and row["status"] == "draft" and row["routed_via"] == "manual",
          "Altruist account, equity_trading_api unknown → a DRAFT manual instruction, and the result says why",
          f"{code} {str(r)[:220]}")
    S["routed_alt"] = iid
    code, out, iid = await create(U_ADMIN1, ACCT_HELD, sa, side="buy", quantity="5")
    r = out.get("routing", {})
    check(code == 201 and r.get("adapter") == "manual" and "held-away" in (r.get("reason") or ""),
          "held-away account (same custodian) → manual regardless", f"{code} {str(r)[:160]}")
    code, out, iid = await create(U_ADMIN1, ACCT_OTHER, sa, side="buy", quantity="5")
    r = out.get("routing", {})
    check(code == 201 and r.get("adapter") == "manual" and "no trade adapter" in (r.get("reason") or ""),
          "a custodian with no trade adapter → manual", f"{code}")
    n0 = await scoped(conn, ORG_A, "SELECT count(*) FROM execution_instructions WHERE org_id = $1", ORG_A,
                      fetch="fetchval")
    bad = []
    for label, b in (
        ("extra top-level field", {**body_for(ACCT_ALT, sa), "org_id": str(ORG_B)}),
        ("extra field inside a trade", {**body_for(ACCT_ALT, sa), "trades": [{**body_for(ACCT_ALT, sa)["trades"][0],
                                                                              "route": "fix"}]}),
        ("a binary float quantity", {**body_for(ACCT_ALT, sa), "trades": [{**body_for(ACCT_ALT, sa)["trades"][0],
                                                                            "quantity": 10.5}]}),
        ("quantity AND notional", {**body_for(ACCT_ALT, sa), "trades": [{**body_for(ACCT_ALT, sa)["trades"][0],
                                                                          "notional": "1000"}]}),
    ):
        c, _ = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", b)
        bad.append((label, c))
    n1 = await scoped(conn, ORG_A, "SELECT count(*) FROM execution_instructions WHERE org_id = $1", ORG_A,
                      fetch="fetchval")
    check(all(c == 422 for _, c in bad) and int(n1) == int(n0),
          "a trade list with an extra field (top level or per trade), a float, or two amounts is refused (422); "
          "nothing written", f"{bad}")
    check(NETWORK_CALLS == [], "no network call was attempted by any route", f"{NETWORK_CALLS[:3]}")


async def y_instructions(conn):
    section("[Y] Instructions: full lifecycle logged; illegal transitions refused; lot checks; Decimal throughout")
    from services import execution as X
    from services.database import get_pool

    sa, sb = S["sec_ZZRQA"], S["sec_ZZRQB"]
    pool = await get_pool()
    lots = [{"tax_lot_id": str(LOT_A1), "quantity": "6"}]
    # lot negative cases
    neg = []
    for label, kw in (
        ("a lot of another account", {"lots": [{"tax_lot_id": str(LOT_HELD1), "quantity": "1"}]}),
        ("a lot of another security", {"lots": [{"tax_lot_id": str(LOT_B1), "quantity": "1"}]}),
        ("more than the lot holds", {"quantity": "50", "lots": [{"tax_lot_id": str(LOT_A2), "quantity": "41"}]}),
        ("lots on a buy", {"side": "buy", "lots": [{"tax_lot_id": str(LOT_A1), "quantity": "1"}]}),
    ):
        c, out, _ = await create(U_ADMIN1, ACCT_ALT, sa, **kw)
        neg.append((label, c, str(out.get("detail"))[:60]))
    check(all(c == 422 for _, c, _ in neg), "lot instructions only for open lots of THAT account and security "
          "(other account, other security, over-quantity, buy side → 422)", f"{neg}")
    code, out, iid = await create(U_ADMIN1, ACCT_ALT, sa, lots=lots)
    check(code == 201 and iid is not None, "create: sell 10 with a specific lot (6 of LOT_A1)", f"{code}")
    if not iid:
        return
    S["main"] = iid
    row = await instr_row(conn, ORG_A, iid)
    lot_rows = await scoped(conn, ORG_A, "SELECT quantity FROM execution_instruction_lots WHERE instruction_id = $1 "
                                         "AND valid_to IS NULL", UUID(iid))
    check(isinstance(row["quantity"], Decimal) and row["quantity"] == Decimal("10")
          and isinstance(row["limit_price"], Decimal) and row["limit_price"] == Decimal("101.25")
          and [r["quantity"] for r in lot_rows] == [Decimal("6")], "quantities and prices persist as Decimal")
    path = f"/api/v1/execution/instructions/{iid}"
    fill = {"quantity": "4", "price": "101.10", "filled_at": datetime.now(timezone.utc).isoformat(),
            "lots": [{"tax_lot_id": str(LOT_A1), "quantity": "4"}]}
    c_fill0, _ = await api(U_ADMIN1, "POST", f"{path}/fills", fill)
    n_f0 = await scoped(conn, ORG_A, "SELECT count(*) FROM execution_fills WHERE instruction_id = $1", UUID(iid),
                        fetch="fetchval")
    check(c_fill0 == 409 and int(n_f0) == 0, "fill before approval → 409, no fill row", f"{c_fill0}")
    c_send0, _ = await api(U_ADMIN1, "POST", f"{path}/sent", {"channel": "email"})
    check(c_send0 == 409, "mark sent before approval → 409", f"{c_send0}")
    S["writer_draft"] = (await api(U_ADMIN1, "GET", path))[1]
    c_sub, o_sub = await api(U_ADMIN1, "POST", f"{path}/submit", {})
    row = await instr_row(conn, ORG_A, iid)
    check(c_sub == 200 and row["status"] == "pending_approval" and row["approval_task_id"] is not None,
          "submit: draft → awaiting approval with its human task", f"{c_sub} {str(o_sub)[:120]}")
    S["maker_pending"] = (await api(U_ADMIN1, "GET", path))[1]
    S["checker_pending"] = (await api(U_ADMIN2, "GET", path))[1]
    S["viewer_detail"] = (await api(U_VIEWER, "GET", path))[1]
    c_edit1, _ = await api(U_ADMIN1, "PATCH", path, {"quantity": "9"})
    check(c_edit1 == 409, "edit after submission → 409 (terms frozen)", f"{c_edit1}")
    try:
        await scoped(conn, ORG_A, "UPDATE execution_instructions SET quantity = 9 WHERE id = $1 AND system_to IS NULL",
                     UUID(iid), fetch="execute")
        check(False, "the database refuses an edit after submission")
    except Exception as exc:  # noqa: BLE001
        check("frozen" in str(exc), "the database refuses an edit after submission (trigger)", str(exc)[:80])
    c_mk, o_mk = await api(U_ADMIN1, "POST", f"{path}/approve", {})
    st_mk = (await instr_row(conn, ORG_A, iid))["status"]
    check(c_mk == 403 and "maker-checker" in str(o_mk.get("detail")) and st_mk == "pending_approval",
          "approve by the maker → 403 naming maker-checker; still awaiting approval", f"{c_mk}")
    c_ok, _ = await api(U_ADMIN2, "POST", f"{path}/approve", {})
    row = await instr_row(conn, ORG_A, iid)
    task = await scoped(conn, ORG_A, "SELECT status, approved_by FROM workflow_run_steps WHERE id = $1",
                        row["approval_task_id"], fetch="fetchrow")
    check(c_ok == 200 and row["status"] == "approved" and row["approved_by"] == U_ADMIN2
          and task["status"] == "completed" and task["approved_by"] == U_ADMIN2,
          "the IDENTICAL approve by a different approver → approved; the engine step completed by the checker")
    c_edit2, _ = await api(U_ADMIN1, "PATCH", path, {"quantity": "9"})
    check(c_edit2 == 409, "edit after approval → 409", f"{c_edit2}")
    c_s, _ = await api(U_ADMIN1, "POST", f"{path}/sent", {"channel": "letter", "note": "posted to the member"})
    check(c_s == 200 and (await instr_row(conn, ORG_A, iid))["status"] == "sent", "mark sent (channel) → sent")
    c_f1, _ = await api(U_ADMIN1, "POST", f"{path}/fills", fill)
    check(c_f1 == 200 and (await instr_row(conn, ORG_A, iid))["status"] == "partially_filled",
          "fill 4 of 10 → partially filled", f"{c_f1}")
    bad_fills = []
    for label, b in (
        ("over-fill", {**fill, "quantity": "7", "lots": []}),
        ("a lot the instruction does not name", {**fill, "quantity": "1", "lots": [{"tax_lot_id": str(LOT_A2),
                                                                                   "quantity": "1"}]}),
        ("lots not adding up", {**fill, "quantity": "2", "lots": [{"tax_lot_id": str(LOT_A1), "quantity": "1"}]}),
        ("a binary float price", {**fill, "price": 101.1, "lots": []}),
    ):
        c, _ = await api(U_ADMIN1, "POST", f"{path}/fills", b)
        bad_fills.append((label, c))
    check(all(c == 422 for _, c in bad_fills), "fills refused: over-fill, foreign lot, mismatched lots, float",
          f"{bad_fills}")
    c_f2, _ = await api(U_ADMIN1, "POST", f"{path}/fills", {**fill, "quantity": "6", "price": "101.20", "lots": []})
    check(c_f2 == 200 and (await instr_row(conn, ORG_A, iid))["status"] == "filled", "fill the rest → filled")
    fills = await scoped(conn, ORG_A, "SELECT quantity, price FROM execution_fills WHERE instruction_id = $1 ORDER BY "
                                      "created_at", UUID(iid))
    check([type(f["quantity"]) for f in fills] == [Decimal, Decimal] and sum(f["quantity"] for f in fills)
          == Decimal("10") and fills[1]["price"] == Decimal("101.20"), "fills persisted as Decimal; total 10")
    c_r0, _ = await api(U_ADMIN1, "POST", f"{path}/reconcile", {"method": "manual"})
    check(c_r0 == 422, "reconcile manually without a note → 422")
    c_r, _ = await api(U_ADMIN1, "POST", f"{path}/reconcile", {"method": "manual",
                                                              "note": "member's confirmation matched"})
    c_c, _ = await api(U_ADMIN1, "POST", f"{path}/close", {})
    row = await instr_row(conn, ORG_A, iid)
    check(c_r == 200 and c_c == 200 and row["status"] == "closed" and row["reconcile_method"] == "manual",
          "reconciled (confirmed manually with a note) → closed")
    c_x, _ = await api(U_ADMIN1, "POST", f"{path}/cancel", {"reason": "too late"})
    check(c_x == 409, "cancel a closed instruction → 409", f"{c_x}")
    evs = await events_of(conn, ORG_A, iid)
    acts = [e["action"] for e in evs]
    want = ["create", "submit", "approve", "mark_sent", "fill", "fill", "reconcile", "close"]
    check(acts == want and all(e["actor_id"] is not None for e in evs), "every transition logged, with its person",
          f"{acts}")
    ev_ids = [str(e["id"]) for e in evs]
    n_dom = await scoped(conn, ORG_A, "SELECT count(*) FROM domain_events WHERE source_type = "
                                      "'execution_instruction_event' AND source_id = ANY($1::uuid[]) AND event_type "
                                      "LIKE 'execution.%'", ev_ids, fetch="fetchval")
    n_aud = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE resource_type = 'execution_instruction' "
                                      "AND resource_id = $1 AND action LIKE 'execution.%'", UUID(iid), fetch="fetchval")
    check(int(n_dom) == len(evs) and int(n_aud) == len(evs), "one domain event + one audit row per action",
          f"{n_dom}/{n_aud}/{len(evs)}")
    n_arch = await scoped(conn, ORG_A, "SELECT count(*) FROM execution_instructions WHERE archived_from_id = $1",
                          UUID(iid), fetch="fetchval")
    check(int(n_arch) == 7, "every change archived the prior state on the system axis (7 states kept; id stable)",
          f"{n_arch}")
    # reject → draft → edit → resubmit; an editor becomes a maker
    code, _, i2 = await create(U_ADMIN1, ACCT_ALT, sa, quantity="5", lots=[])
    p2 = f"/api/v1/execution/instructions/{i2}"
    await api(U_ADMIN1, "POST", f"{p2}/submit", {})
    c_rj0, _ = await api(U_ADMIN2, "POST", f"{p2}/reject", {})
    c_rj, _ = await api(U_ADMIN2, "POST", f"{p2}/reject", {"note": "wrong size"})
    r2 = await instr_row(conn, ORG_A, i2)
    check(c_rj0 == 422 and c_rj == 200 and r2["status"] == "draft" and r2["approval_task_id"] is None,
          "reject needs a note; rejected → back to draft")
    c_e, _ = await api(U_ADMIN2, "PATCH", p2, {"quantity": "3"})
    r2 = await instr_row(conn, ORG_A, i2)
    check(c_e == 200 and r2["quantity"] == Decimal("3") and U_ADMIN2 in r2["maker_ids"],
          "a draft is editable again; the editor joins the makers")
    await api(U_ADMIN1, "POST", f"{p2}/submit", {})
    c_a2, o_a2 = await api(U_ADMIN2, "POST", f"{p2}/approve", {})
    c_a3, _ = await api(U_ADMIN3, "POST", f"{p2}/approve", {})
    check(c_a2 == 403 and "maker-checker" in str(o_a2.get("detail")) and c_a3 == 200,
          "someone who EDITED it cannot approve it; a third person can", f"{c_a2} {c_a3}")
    # cancel: draft (reason required) and pending (the engine task is withdrawn)
    _, _, i3 = await create(U_ADMIN1, ACCT_ALT, sa, quantity="2", lots=[])
    c_c0, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{i3}/cancel", {"reason": ""})
    c_c1, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{i3}/cancel", {"reason": "duplicate"})
    r3 = await instr_row(conn, ORG_A, i3)
    check(c_c0 == 422 and c_c1 == 200 and r3["status"] == "cancelled" and r3["cancel_reason"] == "duplicate",
          "cancel needs a reason; cancelled with it")
    _, _, i4 = await create(U_ADMIN1, ACCT_ALT, sa, quantity="2", lots=[])
    await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{i4}/submit", {})
    task4 = (await instr_row(conn, ORG_A, i4))["approval_task_id"]
    await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{i4}/cancel", {"reason": "client changed mind"})
    t4 = await scoped(conn, ORG_A, "SELECT rs.status, r.status AS rstat FROM workflow_run_steps rs JOIN workflow_runs "
                                   "r ON r.id = rs.workflow_run_id WHERE rs.id = $1", task4, fetch="fetchrow")
    check((await instr_row(conn, ORG_A, i4))["status"] == "cancelled" and t4["status"] == "cancelled"
          and t4["rstat"] == "cancelled", "cancelling while awaiting approval withdraws the engine task")
    # execute-by: expire / carry / ask (the only automatic step, logged with no actor)
    # The date is read HERE, not from the module-level TODAY: a run that crosses midnight would otherwise
    # create with yesterday's date, the service refuses it as past (422), and every read below sees nothing.
    today = date.today()
    c5, o5, i5 = await create(U_ADMIN1, ACCT_ALT, sa, quantity="1", lots=[], execute_by=today, if_not_filled="cancel")
    c6, o6, i6 = await create(U_ADMIN1, ACCT_ALT, sa, quantity="1", lots=[], execute_by=today, if_not_filled="carry")
    c7, o7, i7 = await create(U_ADMIN1, ACCT_ALT, sa, quantity="1", lots=[], execute_by=today, if_not_filled="ask")
    check(c5 == c6 == c7 == 201 and None not in (i5, i6, i7),
          "execute-by fixtures created (execute-by = today)",
          f"{c5} {c6} {c7} {[o.get('detail') for o in (o5, o6, o7)]}")
    for i in (i6, i7):
        await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{i}/submit", {})
        await api(U_ADMIN2, "POST", f"/api/v1/execution/instructions/{i}/approve", {})
    await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{i7}/sent", {"channel": "phone"})
    later = today + timedelta(days=2)
    with _ctx(None, super_admin=True):
        res = await X.expire_due(pool, today=later, org_id=ORG_A)
        res2 = await X.expire_due(pool, today=later, org_id=ORG_A)
    r5 = await instr_row(conn, ORG_A, i5)
    r6 = await instr_row(conn, ORG_A, i6)
    r7 = await instr_row(conn, ORG_A, i7)
    e5 = ((await events_of(conn, ORG_A, i5)) or [{"action": None, "actor_id": "none logged"}])[-1]
    e7 = [e for e in await events_of(conn, ORG_A, i7) if e["action"] == "ask"]
    todo7 = await scoped(conn, ORG_A, "SELECT user_id FROM member_todos WHERE source = 'execution_not_filled' AND "
                                      "related_id = $1", UUID(i7))
    check(r5["status"] == "expired" and "execute-by" in (r5["expiry_reason"] or "") and e5["action"] == "expire"
          and e5["actor_id"] is None, "if not filled: cancel → expired at execute-by, with a reason, logged (no actor)")
    check(r6["status"] == "approved" and r6["execute_by"] == later and i6 in res["carried"],
          "if not filled: carry → still working, execute-by moved to today, logged")
    check(r7["status"] == "sent" and len(e7) == 1 and len(todo7) >= 1 and i7 in res["asked"]
          and i7 not in res2["asked"], "if not filled: ask → a todo for a person, status unchanged, asked ONCE")
    # pending differences
    code_p, out_p = await api(U_VIEWER, "GET", f"/api/v1/execution/accounts/{ACCT_ALT}/pending")
    pend = {r["global_security_id"]: r for r in out_p.get("rows", [])}
    ids_open = {x["id"] for r in out_p.get("rows", []) for x in r["instructions"]}
    check(code_p == 200 and str(sa) in pend and iid not in ids_open and i3 not in ids_open and i6 in ids_open
          and isinstance(pend[str(sa)]["pending_quantity"], str),
          "pending differences: open instructions only (closed / cancelled excluded), decimal strings", f"{code_p}")
    try:
        with _ctx(ORG_A):
            await X.enter_fill(pool, org_id=ORG_A, instruction_id=i7, actor_id=U_ADMIN1, quantity=1.0,
                               price=Decimal("1"), filled_at=datetime.now(timezone.utc))
        check(False, "the service refuses a float quantity")
    except X.ExecutionError:
        check(True, "the service refuses a float quantity (Decimal only)")


async def y_letter(conn):
    section("[Y] Letter of instruction: on demand, masked by default, full number only by explicit logged choice, gated")
    from pypdf import PdfReader

    iid = S.get("main")
    if not iid:
        check(False, "main instruction exists for the letter")
        return
    path = f"/api/v1/execution/instructions/{iid}/letter"

    def pdf_text(content: bytes) -> str:
        return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages)

    masked = "******5678"
    code, content, headers = await api_raw(U_VIEWER, "POST", path, {})
    txt = pdf_text(content) if code == 200 else ""
    digits = re.sub(r"[^A-Za-z0-9]", "", txt)
    check(code == 200 and headers.get("content-type", "").startswith("application/pdf") and masked in txt
          and FULL_NUMBER not in digits and "12345678" not in digits,
          "PDF on demand for a view-only caller; masked number shown; the full number appears NOWHERE in the text",
          f"{code}")
    stored = await scoped(conn, ORG_A, "SELECT count(*) FROM execution_instructions WHERE id = $1", UUID(iid),
                          fetch="fetchval")
    check(int(stored) == 1, "nothing stored for the letter (the instruction is the source of truth)")
    for label, b, want in (("include without the number", {"include_full_number": True}, 422),
                           ("a wrong number", {"include_full_number": True, "full_account_number": WRONG_NUMBER}, 422),
                           ("a number without choosing to include it", {"full_account_number": FULL_NUMBER}, 422)):
        c, _, _ = await api_raw(U_VIEWER, "POST", path, b)
        check(c == want, f"letter refused: {label} ({want})", f"{c}")
    code, content, _ = await api_raw(U_VIEWER, "POST", path, {"include_full_number": True,
                                                             "full_account_number": FULL_NUMBER})
    txt = pdf_text(content) if code == 200 else ""
    check(code == 200 and FULL_NUMBER in re.sub(r"\s", "", txt), "the explicit choice + the right number → full number "
          "in the PDF")
    aud = await scoped(conn, ORG_A, "SELECT payload::text AS p FROM audit_log WHERE action = "
                                    "'execution.letter_full_account_number' AND resource_id = $1", UUID(iid))
    ev = await scoped(conn, ORG_A, "SELECT detail FROM execution_instruction_events WHERE instruction_id = $1 AND "
                                   "action = 'letter' ORDER BY created_at", UUID(iid))
    det = [json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"] for e in ev]
    check(len(aud) == 1 and [d.get("include_full_number") for d in det] == [False, True],
          "every letter logged; the full-number choice logged separately (audit)", f"{len(aud)} {det}")
    leak = 0
    for sql in ("SELECT count(*) FROM audit_log WHERE org_id = $1 AND payload::text LIKE $2",
                "SELECT count(*) FROM domain_events WHERE org_id = $1 AND payload::text LIKE $2",
                "SELECT count(*) FROM execution_instruction_events WHERE org_id = $1 AND detail::text LIKE $2",
                "SELECT count(*) FROM accounts WHERE org_id = $1 AND (account_number_masked LIKE $2 OR "
                "account_number_hash LIKE $2)"):
        leak += int(await scoped(conn, ORG_A, sql, ORG_A, f"%{FULL_NUMBER[2:]}%", fetch="fetchval"))
    check(leak == 0, "the full number is stored or logged NOWHERE (audit, events, accounts)", f"{leak}")
    c_m, _, _ = await api_raw(U_MEMBER, "POST", path, {})
    c_b, _, _ = await api_raw(U_ADMIN_B, "POST", path, {})
    check(c_m == 403 and c_b == 404, "refused for a caller who cannot view it (no permission 403; other org 404)",
          f"{c_m} {c_b}")
    _, _, ib = await create(U_ADMIN1, ACCT_BROKER, S["sec_ZZRQA"], side="buy", quantity="1", executor="broker")
    c_br, _, _ = await api_raw(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{ib}/letter", {})
    check(c_br == 422, "a letter is only for a member / other-adviser executor (broker → 422)", f"{c_br}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions under app_service (rolbypassrls = false); capabilities shared")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "isolation is tested on a role WITHOUT rolbypassrls", f"{await conn.fetchval('SELECT current_user')}")
    code, out, ib = await create(U_ADMIN_B, ACCT_B, S["sec_ZZRQA"], side="buy", quantity="1", lots=[])
    check(code == 201 and ib is not None, "org B creates its own instruction", f"{code}")
    if not ib:
        return
    await api(U_ADMIN_B, "POST", f"/api/v1/execution/instructions/{ib}/submit", {})
    ia = S.get("main")
    for viewer, own, other, label in ((U_ADMIN_B, ib, ia, "B → A"), (U_ADMIN1, ia, ib, "A → B")):
        c_own, _ = await api(viewer, "GET", f"/api/v1/execution/instructions/{own}")
        c_oth, _ = await api(viewer, "GET", f"/api/v1/execution/instructions/{other}")
        c_wr, _ = await api(viewer, "POST", f"/api/v1/execution/instructions/{other}/cancel", {"reason": "x"})
        _, lst = await api(viewer, "GET", "/api/v1/execution/instructions")
        ids = {r["id"] for r in lst.get("rows", [])}
        _, mine = await api(viewer, "GET", "/api/v1/human-tasks/mine")
        task_orgs = {t["org_id"] for t in mine.get("actionable", []) + mine.get("waiting_on_others", [])}
        check(c_own == 200 and c_oth == 404 and c_wr == 404 and own in ids and other not in ids
              and task_orgs <= {str(USERS[viewer][0])},
              f"{label}: own 200, the other org's instruction 404 (read AND write), absent from the list; tasks own org",
              f"{c_own} {c_oth} {c_wr}")
    for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        q = {t: await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", other, fetch="fetchval")
             for t in ("execution_instructions", "execution_instruction_events", "workflow_runs",
                       "workflow_run_steps", "member_todos")}
        own = await scoped(conn, org, "SELECT count(*) FROM execution_instructions WHERE org_id = $1", org,
                           fetch="fetchval")
        caps = await scoped(conn, org, "SELECT count(*) FROM custodian_capabilities", fetch="fetchval")
        check(int(own) > 0 and all(int(v) == 0 for v in q.values()) and int(caps) >= 26,
              f"SQL as {str(org)[-4:]}: own instructions visible; the other org's instructions/events/task runs/"
              "todos 0; platform capabilities readable", f"own {own} other {q} caps {caps}")
    try:
        await scoped(conn, ORG_B, "INSERT INTO execution_instruction_events (org_id, instruction_id, action, to_status, "
                                  "actor_id) VALUES ($1, $2, 'cancel', 'cancelled', $3)", ORG_A, UUID(ia), U_ADMIN_B,
                     fetch="execute")
        check(False, "org B cannot write an org A row")
    except Exception as exc:  # noqa: BLE001
        check("row-level security" in str(exc), "org B cannot write an org A row (RLS WITH CHECK)", str(exc)[:90])


async def y_gates(conn):
    section("[Y] Gates both ways on identical requests; envelopes; node tests render no controls without the flags")
    lst = "/api/v1/execution/instructions"
    for uid, who in ((U_MEMBER, "member"), (U_STAFF, "staff")):
        code, out = await api(uid, "GET", lst)
        check(code == 403 and "view_execution" in str(out.get("detail")) and "rows" not in out,
              f"GET instructions: {who} → 403 naming view_execution, no envelope", f"{code}")
    code_v, out_v = await api(U_VIEWER, "GET", lst)
    code_a, out_a = await api(U_ADMIN1, "GET", lst)
    check(code_v == 200 and code_a == 200, "the identical GET: viewer and org admin → 200")
    check(out_v["permissions"]["can_write"] is False and out_v["vocabularies"]["editable"] == []
          and out_a["permissions"]["can_write"] is True and len(out_a["vocabularies"]["editable"]) > 0,
          "envelopes: viewer can_write false + editable []; org admin can_write true + editable from the server")
    body = body_for(ACCT_ALT, S["sec_ZZRQA"], side="buy", quantity="1")
    c_v, o_v = await api(U_VIEWER, "POST", lst, body)
    c_s, o_s = await api(U_STAFF, "POST", lst, body)
    c_a, _ = await api(U_ADMIN3, "POST", lst, body)
    check(c_v == 403 and "manage_execution" in str(o_v.get("detail")) and c_s == 403 and c_a == 201,
          "POST instructions: viewer / staff → 403 naming manage_execution; org admin → 201 on the identical body",
          f"{c_v} {c_s} {c_a}")
    code_su, out_su = await api(U_SUPER, "GET", lst)
    check(code_su == 200 and out_su["permissions"]["can_approve"] is True and out_su["permissions"]["is_super_admin"],
          "Super Admin first: full envelope")
    _, _, ip = await create(U_ADMIN1, ACCT_ALT, S["sec_ZZRQA"], side="buy", quantity="1")
    await api(U_ADMIN1, "POST", f"{lst}/{ip}/submit", {})
    _, mine = await api(U_ADMIN2, "GET", "/api/v1/human-tasks/mine")
    _, mine_m = await api(U_ADMIN1, "GET", "/api/v1/human-tasks/mine")
    _, mine_s = await api(U_STAFF, "GET", "/api/v1/human-tasks/mine")
    check(any(t["subject_id"] == ip for t in mine.get("actionable", []))
          and all(t["maker_id"] != str(U_ADMIN2) for t in mine.get("actionable", []))
          and any(t["subject_id"] == ip for t in mine_m.get("waiting_on_others", []))
          and not any(t["subject_id"] == ip for t in mine_m.get("actionable", []))
          and not mine_s.get("actionable"),
          "My tasks: the checker sees the open task to decide; the maker sees it as waiting; staff sees none")
    for uid, who in ((U_VIEWER, "viewer"), (U_STAFF, "staff")):
        code, out = await api(uid, "POST", f"{lst}/{ip}/approve", {})
        check(code == 403 and "approve_execution" in str(out.get("detail")),
              f"POST approve: {who} → 403 naming approve_execution", f"{code}")
    code, _ = await api(U_ADMIN2, "POST", f"{lst}/{ip}/approve", {})
    check(code == 200, "POST approve: an org admin who is not the maker → 200 on the identical request")
    mp, cp, vd = S.get("maker_pending", {}), S.get("checker_pending", {}), S.get("viewer_detail", {})
    check("approve" not in mp.get("actions", []) and mp.get("permissions", {}).get("is_maker") is True
          and "approve" in cp.get("actions", []) and cp.get("permissions", {}).get("can_decide") is True
          and not set(vd.get("actions", [])) & {"edit", "submit", "cancel", "mark_sent", "fill", "reconcile", "close",
                                                "approve", "reject"},
          "server actions: maker gets no approve; checker gets approve; viewer gets no write action")
    envs = {"viewer_list": out_v, "admin_list": out_a, "viewer_detail": vd, "maker_pending": mp,
            "checker_pending": cp, "writer_draft": S.get("writer_draft", {}),
            "staff_capabilities": S.get("cap_envelopes", {}).get("staff", {}),
            "super_capabilities": S.get("cap_envelopes", {}).get("super", {})}
    fd, path = tempfile.mkstemp(suffix=".json", prefix="rep2_envelopes_")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(envs, fh)
        code, out, err = run_cmd(["node", "--test", "tests/execution/"], WEB_DIR, timeout=300,
                                 env=os.environ | {"REP2_REAL_ENVELOPES": path})
    finally:
        os.unlink(path)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/execution passes",
          (out + err)[-300:] if code else "")
    for name in ("instructions list: no New control unless the envelope says can_write === true",
                 "instruction detail: a view-only envelope renders no write or approve control (only the letter)",
                 "approve / reject: only with approve_execution, can_decide, and never for a maker",
                 "capabilities: Edit only for Super Admin; 'unknown' shown plainly"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}$", out, re.M) is not None, f"node test: {name}")
    real = re.search(r"^ok \d+ - real API envelopes: no write / approve controls without the server's flags(.*)$",
                     out, re.M)
    check(real is not None and "SKIP" not in real.group(1),
          "node test on the REAL API envelopes ran (not skipped) and passed")


async def y_new_org(conn):
    section("[Y] New org's first org_admin gets the six permissions")
    from services import rbac

    before = await platform(conn, "SELECT count(*) FROM roles WHERE org_id = $1 AND name = 'org_admin'", ORG_C,
                            fetch="fetchval")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await rbac.grant_org_admin(conn, U_NEWADMIN, ORG_C)
    dsn, _ = await admin_dsn()
    fresh = await connect(dsn)
    try:
        perms = {r["name"] for r in await platform(fresh, """
            SELECT p.name FROM roles r JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p
            ON p.id = rp.permission_id WHERE r.org_id = $1 AND r.name = 'org_admin'""", ORG_C)}
        held = await platform(fresh, "SELECT count(*) FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE "
                                     "ur.user_id = $1 AND r.org_id = $2 AND r.name = 'org_admin'", U_NEWADMIN, ORG_C,
                              fetch="fetchval")
    finally:
        await fresh.close()
    check(int(before) == 0 and set(SIX_PERMS) <= perms and "manage_org_settings" in perms and int(held) == 1,
          "a NEW org's first org_admin role carries the three execution AND three replication permissions "
          "(re-read on an independent connection)", f"{sorted(perms)}")


CSV_FIXTURE = (
    "account_number,entity,registration_type,tax_status,is_held_away\n"
    f"ZZ99990001,{ENT_A},individual,taxable,false\n"
    f"ZZ99990002,{ENT_A},joint,taxable,true\n"
    ",nobody,individual,taxable,false\n"
).encode("utf-8")


async def y_csv(conn):
    section("[Y] CSV custody import path unchanged")
    from services.custody import importer
    from services.custody import registered_adapters as CU_registered_adapters

    same = []
    for f in ("services/custody/csv_adapter.py", "services/custody/importer.py", "services/custody/base.py"):
        same.append((f, git_show(BEFORE_REF, f"apps/api/{f}") == (API_DIR / f).read_text(encoding="utf-8")))
    check(all(ok for _, ok in same), "csv_adapter.py, importer.py, base.py are byte-identical to main", f"{same}")
    before_reg = git_show(BEFORE_REF, "apps/api/services/custody/registry.py").rstrip()
    now_reg = (API_DIR / "services/custody/registry.py").read_text(encoding="utf-8")
    check(bool(before_reg) and now_reg.startswith(before_reg), "registry.py: main's text is kept verbatim; rep2 only "
          "APPENDS the trade-adapter map")
    # Main's code and this branch's each run in their OWN subprocess over the same fixture file. Exec'ing main's
    # csv_adapter into this process would re-register 'csv' in the live registry (refused, rightly: a second
    # class on one key). Main's custody package is written from git into a temp `services` package of its own.
    probe = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from services.custody.csv_adapter import CsvCustodyAdapter\n"
        "from services.custody.registry import DEFAULT_PROFILES, registered_adapters\n"
        "a = CsvCustodyAdapter(custodian_code='GENERIC_CSV', source_system='CSV', file_bytes=open(sys.argv[2], "
        "'rb').read(), filename='f.csv', column_map=DEFAULT_PROFILES['GENERIC_CSV']['column_map'])\n"
        "o = a.fetch_accounts()\n"
        "print(json.dumps({'records': [r.summary() for r in o.records], "
        "'errors': [[e.source_row, e.reason_code] for e in o.errors], 'adapters': registered_adapters()}, "
        "default=str, sort_keys=True))\n"
    )

    def outcome(root: pathlib.Path, fixture: pathlib.Path):
        code, out, err = run_cmd([sys.executable, "-c", probe, str(root), str(fixture)], root, timeout=120)
        if code != 0:
            return {"error": (err or out)[-300:]}
        return json.loads(out.strip().splitlines()[-1])

    with tempfile.TemporaryDirectory(prefix="rep2_csv_main_") as tmp:
        root = pathlib.Path(tmp)
        fixture = root / "fixture.csv"
        fixture.write_bytes(CSV_FIXTURE)
        (root / "services" / "custody").mkdir(parents=True)
        (root / "services" / "__init__.py").write_text("", encoding="utf-8")
        _, listing, _ = run_cmd(["git", "ls-tree", "--name-only", BEFORE_REF, "apps/api/services/custody/"], REPO,
                                timeout=60)
        for f in listing.split():
            (root / "services" / "custody" / Path(f).name).write_text(git_show(BEFORE_REF, f), encoding="utf-8")
        old_out = outcome(root, fixture)
        new_out = outcome(API_DIR, fixture)
    check("error" not in old_out and "error" not in new_out and old_out["records"] == new_out["records"]
          and old_out["errors"] == new_out["errors"] and len(new_out["records"]) == 2
          # 0 row errors, not 1: the blank-number line has no account record and is SKIPPED by design
          # (csv_adapter._absent) — main's own code, run in its own process, returns 0 on this file.
          and len(new_out["errors"]) == 0 and old_out["adapters"] == new_out["adapters"] == ["csv"],
          "the same fixture file parses identically under main's code and this branch's (two separate processes)",
          f"{old_out.get('error') or old_out.get('errors')} | {new_out.get('error') or new_out.get('errors')}")
    check(CU_registered_adapters() == ["csv"], "the live ingestion registry was not touched: ['csv']")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        plan = await importer.build_plan(conn, org_id=str(ORG_A), custodian_code="GENERIC_CSV",
                                         file_bytes=CSV_FIXTURE, filename="f.csv")
    c = plan.to_json()["counts"]
    check(c["accounts_new"] == 2 and c["unmatched"] == 1 and plan.custodian_code == "GENERIC_CSV",
          "the importer's dry run over it: 2 new accounts, 1 exception — the same result as main", f"{c}")


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
    new = [k for k in gn if k not in gb]
    prefixes = ("/api/v1/execution/", "/api/v1/custodians/", "/api/v1/human-tasks/")
    check(len(new) == 21 and all(k[1].startswith(prefixes) for k in new),
          "21 new routes, all under /execution, /custodians, /human-tasks", f"{len(new)}")
    ok_gate = all(
        any(p in str(gn[k].get("after")) for p in EXECUTION_PERMS) if k[1].startswith("/api/v1/execution/")
        else (str(gn[k].get("after")) == ("staff or authz manage_org_settings" if "GET" in k[0] else "super_admin"))
        if k[1].startswith("/api/v1/custodians/")
        else True for k in new)
    check(ok_gate, "execution routes name their execution permission; capabilities: staff or manage_org_settings "
          "read / super_admin write",
          f"{[(k[1][-40:], gn[k].get('after')) for k in new][:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_workflowmgr5.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1")
    # SKIP_BUILD: this script already ran npm run build once; a second build in
    # the nested run would only repeat it (workflowmgr5 honours SKIP_BUILD).
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_workflowmgr5.py")], REPO, timeout=1200, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    result = re.findall(r"^RESULT: (.*)$", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    zero = (bool(total) and total[-1][1] == "0") or (bool(result) and "ALL ASSERTIONS PASSED" in result[-1])
    check(zero and not fails and code == 0, "verify_workflowmgr5.py: 0 FAIL ('TOTAL:' or 'RESULT:' summary)",
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
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers i JOIN "
                             "portfolio.securities_global s ON s.id = i.global_security_id WHERE i.id_value LIKE $1 "
                             "AND NOT (s.name = ANY($2::text[]))", f"{PREFIX}%", list(FUND_NAMES), fetch="fetchval")
    m = await platform(conn, "SELECT count(*) FROM public.accounts WHERE custodian_code LIKE 'ZZ_REP2_%' AND NOT "
                             "(org_id = ANY($1::uuid[]))", [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
    return check(int(n) == 0 and int(m) == 0, f"the reserved prefixes ({PREFIX}, ZZ_REP2_) match no real row",
                 f"{n}/{m}")


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
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("seed", seed, conn)
        await run("constraints", y_constraints, conn)
        if S.get("sec_ZZRQA"):
            await run("human tasks", y_human_tasks, conn)
            await run("capabilities", y_capabilities, conn)
            await run("routing", y_routing, conn)
            await run("instructions", y_instructions, conn)
            await run("letter", y_letter, conn)
            await run("isolation", y_isolation, conn)
            await run("gates", y_gates, conn)
            await run("new org", y_new_org, conn)
            await run("csv", y_csv, conn)
        else:
            check(False, "seed created the fixture securities", "every fixture-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        try:
            from services.database import close_pool

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
                # Reconnect ONCE and re-run every count on the new connection. A second drop propagates
                # to "teardown raised" below — a dropped connection is never reported as clean.
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
