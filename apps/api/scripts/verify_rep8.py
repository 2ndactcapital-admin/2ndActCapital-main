"""verify_rep8 — Replication Studio, sprint 8 (REP8, phase 2 starts): the
OPTIONS SLEEVE — buffer (defined-outcome) and covered-call / income ETFs as the
underlying plus an options package; option contracts in the security master;
adviser-entered quotes; the payoff lab; the options TAX GATE; options
proposals → manual per-leg instructions; fills → option lots; outcome-period
rolls and expiry; minimum-account sizing; viability Q4 for options.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep8.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the httpx network transport records and refuses — any attempt is a
failed check. Every custodian trade adapter's ``submit`` is wrapped to record a
call: an options leg that reached one is a failed check.

REAL: the app through an ASGI transport (middleware, Principal, every gate),
the workflow engine (human tasks), rep1 / rep2 / rep3 / rep5 / rep6 / rep7b
services, RLS and the live database. The script's own connection is the live
DATABASE_URL (app_service, rolbypassrls = false — asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-000000080001 / 80002, is_test =
true, asserted); users created explicitly with the auth0_sub their token
carries and an @test.local email; masked account numbers only (raw ZZ8…);
securities with the reserved prefix ZZR8 and the name prefix 'REP8 VERIFY'
(option contracts the run defines hang off those underlyings); the household
'REP8 Verify Household' — all checked absent from real rows first.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with
execution.teardown / models.teardown / replica.teardown = 'fixture' — seeds
keyed by this run's fixture ids (every org_id table under the fixture orgs,
the fixture organizations and users, the platform option contracts on the
fixture underlyings and their 'option' securities), a FK closure that only
follows children that hold rows, one DELETE per table child-before-parent.
Every counted table is re-read for its exact pre-test count; real rows are
compared by count + checksum.

Every expected date is computed at the moment of the call (no TODAY fixed at
script start). With VERIFY_NESTED=1 the regression section prints one
'[SKIP] nested run'. --cleanup-only: the teardown and the leftover check only.
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
import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import execution as X  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import models as M  # noqa: E402
from services import options_sleeve as OS  # noqa: E402
from services import options_tax_gate as G  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import wash_sale as WS  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

BEFORE_REF = "5681d30"   # main @ merge dealroom3 — the rep8-work base
TAG = "rep8verify"
PREFIX = "ZZR8"
NAME_PREFIX = "REP8 VERIFY"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("IDX", "BUFR", "CORE", "STK")
SEC_TYPES = {"IDX": "index", "BUFR": "fund", "CORE": "fund", "STK": "equity"}
SEC_NAMES = {k: f"{NAME_PREFIX} {k}" for k in SEC_KEYS}
HH_NAME = "REP8 Verify Household"
FOREIGN_FIXTURE_ORGS = [UUID(f"99000000-0000-0000-0000-0000000e{n}") for n in ("2001", "2002", "2003", "6001",
                                                                              "6002", "6003")]


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000008{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN1, U_ADMIN2, U_VIEWER, U_ADMIN_B = (_id(110 + i) for i in range(5))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP8 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP8 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP8 Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep8_viewer", "REP8 Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP8 Org B Admin"),
}
VIEWER_PERMS = ("view_replication", "view_proposals", "view_execution", "view_models")
ENT_A, ENT_A2, ENT_A3, ENT_B = _id(201), _id(202), _id(203), _id(204)
HH = _id(211)
ACCT_M, ACCT_RO, ACCT_ADV, ACCT_B = (_id(301 + i) for i in range(4))
ACCOUNTS = {   # id: (org, custodian_code, household, entity, held_away, raw number)
    ACCT_M: (ORG_A, "ALTRUIST", HH, ENT_A, False, "ZZ8000000001"),
    ACCT_RO: (ORG_A, "SCHWAB", None, ENT_A2, True, "ZZ8000000002"),
    ACCT_ADV: (ORG_A, "ALTRUIST", None, ENT_A3, False, "ZZ8000000003"),
    ACCT_B: (ORG_B, "ALTRUIST", None, ENT_B, False, "ZZ8000000004"),
}
CORE_QTY, CORE_MV, CORE_COST = Decimal(1000), Decimal(1000000), Decimal(800000)
LEVEL = Decimal(5000)
PREMIUMS = {"long_put_atm": Decimal(120), "short_put_buffer_end": Decimal(70), "short_call_cap": Decimal(40)}
TERMS_PCT = {"cap_pct": 15, "buffer_pct": 10, "leg_long_put_strike_pct": 100, "leg_short_put_strike_pct": 90,
             "leg_short_call_strike_pct": 115}

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.account_balances_daily", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.tax_lots", "portfolio.transactions", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "public.execution_instructions", "public.execution_instruction_lots",
    "public.execution_fills", "public.execution_instruction_events", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.replica_securities", "portfolio.replica_terms_versions",
    "public.account_model_links", "public.rebalance_proposals", "public.rebalance_proposal_trades",
    "public.declared_outside_trades", "public.account_authority_settings", "portfolio.option_contracts",
    "portfolio.adviser_quotes", "portfolio.option_packages", "portfolio.option_package_legs",
    "public.options_tax_gate_results", "public.option_settlement_entries",
)
NEW_ORG_TABLES = ("portfolio.adviser_quotes", "portfolio.option_packages", "portfolio.option_package_legs",
                  "public.options_tax_gate_results", "public.option_settlement_entries")
NEW_TABLES = ("portfolio.option_contracts",) + NEW_ORG_TABLES
NEW_ROUTES = {
    ("GET", "/api/v1/replication/options/entries/{entry_id}"): "view_replication",
    ("POST", "/api/v1/replication/options/entries/{entry_id}/build"): "manage_models",
    ("GET", "/api/v1/replication/options/packages/{package_id}"): "view_replication",
    ("GET", "/api/v1/replication/options/packages/{package_id}/payoff"): "view_replication",
    ("GET", "/api/v1/replication/options/packages/{package_id}/settlements"): "view_replication",
    ("POST", "/api/v1/replication/options/packages/{package_id}/price"): "manage_replication",
    ("POST", "/api/v1/replication/options/packages/{package_id}/submit"): "manage_replication",
    ("POST", "/api/v1/replication/options/packages/{package_id}/approve"): "approve_replication",
    ("POST", "/api/v1/replication/options/packages/{package_id}/reject"): "approve_replication",
    ("GET", "/api/v1/replication/options/quotes"): "view_replication",
    ("POST", "/api/v1/replication/options/quotes"): "manage_replication",
    ("GET", "/api/v1/replication/options/contracts"): "view_replication",
    ("POST", "/api/v1/replication/options/contracts"): "manage_replication",
    ("POST", "/api/v1/replication/options/accounts/{account_id}/proposals"): "manage_proposals",
    ("GET", "/api/v1/replication/options/proposals/{proposal_id}/gate"): "view_proposals",
    ("POST", "/api/v1/replication/options/proposals/{proposal_id}/gate"): "manage_proposals",
    ("POST", "/api/v1/replication/options/instructions/{instruction_id}/book"): "manage_execution",
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


def D(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def tk(key):
    return f"{PREFIX}{key}"


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def ago(n: int) -> date:
    return today() - timedelta(days=n)


def now() -> datetime:
    return datetime.now(timezone.utc)


def stated(r: Decimal) -> Decimal:
    """The verify's own buffer outcome (cap 15%, buffer 10%) — independent of services/options_sleeve.py."""
    if r >= 0:
        return min(r, Decimal("0.15"))
    if r >= Decimal("-0.10"):
        return Decimal(0)
    return r + Decimal("0.10")


# ═══ DB helpers ══════════════════════════════════════════════════════════════
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
    """Rows this sprint must never touch (count + checksum), outside this run's fixture orgs. The checksums exclude
    only rep8's new columns (rebalance_proposal_trades.option_leg, execution_instructions.option_terms)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("tax_lots", "portfolio.tax_lots"), ("transactions", "portfolio.transactions"),
                        ("rebalance_proposals", "public.rebalance_proposals"),
                        ("account_model_links", "public.account_model_links"),
                        *[(t.split(".")[1], t) for t in NEW_ORG_TABLES]):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["execution_instructions"] = await fingerprint(
        conn, "SELECT to_jsonb(i) - 'option_terms' AS j FROM public.execution_instructions i "
              "WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["rebalance_proposal_trades"] = await fingerprint(
        conn, "SELECT to_jsonb(t) - 'option_leg' AS j FROM public.rebalance_proposal_trades t "
              "WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    # rows that existed when the run started (a concurrent platform job may add others; it never changes these)
    t0 = S.setdefault("t0", now())
    out["securities_global"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global "
                                                       "WHERE system_from < $1", t0)
    out["option_contracts"] = await fingerprint(conn, "SELECT * FROM portfolio.option_contracts WHERE created_at < $1",
                                                t0)
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


async def fixture_security_ids(conn) -> list:
    ids = {r["global_security_id"] for r in await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")}
    ids |= {r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                              f"{NAME_PREFIX}%")}
    # the option contracts this run defined hang off the fixture underlyings
    ids |= {r["global_security_id"] for r in await conn.fetch(
        "SELECT global_security_id FROM portfolio.option_contracts WHERE underlying_global_security_id = "
        "ANY($1::uuid[])", list(ids))}
    return list(ids)


async def org_tables(conn) -> list[str]:
    return [r["t"] for r in await conn.fetch(
        "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
        "'information_schema') ORDER BY 1")]


async def teardown() -> list[str]:
    log: list[str] = []
    op = await operator_conn()
    try:
        secs = await fixture_security_ids(op)
        users = list(USERS)
        orgs = FIXTURE_ORGS
        assert not set(orgs) & set(FOREIGN_FIXTURE_ORGS)
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan = {
            "public.member_todos": org_or_user, "public.audit_log": org_or_user,
            "portfolio.option_contracts": ("underlying_global_security_id = ANY($1::uuid[]) OR "
                                           "global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        if "public.assistant_activities" in exists:
            plan["public.assistant_activities"] = org_or_user
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_rep8")
        log = res["log"]
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
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.option_contracts c JOIN portfolio.securities_global g ON "
         "g.id = c.underlying_global_security_id WHERE g.name LIKE $1", [f"{NAME_PREFIX}%"]),
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


async def api(uid, method, path, body=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep8"}}
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
    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep8: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    # every trade adapter (the manual one included) records a submit: an options leg must never reach one
    for cls in [c for c in vars(TR).values() if isinstance(c, type) and issubclass(c, TR.TradeAdapter)
                and c is not TR.TradeAdapter]:
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


async def in_org(fn, org=None, super_admin=False):
    pool = await get_pool()
    with _ctx(org or ORG_A, super_admin):
        async with pool.acquire() as c2:
            return await fn(c2)


async def pool_call(fn, org=None):
    pool = await get_pool()
    with _ctx(org or ORG_A):
        return await fn(pool)


# Every literal the seed and the tests write into a CHECK-constrained column — checked against the live catalog.
SEED_CHECK_LITERALS = (
    ("portfolio.option_contracts", "option_contracts_type_chk", ("call", "put")),
    ("portfolio.option_contracts", "option_contracts_style_chk", ("european",)),
    ("portfolio.option_contracts", "option_contracts_settlement_chk", ("cash",)),
    ("portfolio.option_contracts", "option_contracts_underlying_kind_chk", ("index", "equity")),
    ("portfolio.adviser_quotes", "adviser_quotes_kind_chk", ("premium", "index_level", "underlying_price")),
    ("portfolio.option_packages", "option_packages_status_chk", ("draft", "approved", "rolled", "expired")),
    ("portfolio.option_packages", "option_packages_kind_chk", ("buffer",)),
    ("portfolio.option_package_legs", "option_package_legs_role_chk",
     ("underlying", "long_put_atm", "short_put_buffer_end", "short_call_cap")),
    ("public.options_tax_gate_results", "options_tax_gate_results_rule_chk", G.RULES),
    ("public.options_tax_gate_results", "options_tax_gate_results_outcome_chk", ("pass", "flag", "block")),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("custodian_desk",)),
    ("public.execution_instructions", "execution_instructions_status_chk", ("approved", "sent", "filled")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("manual",)),
    ("public.rebalance_proposals", "rebalance_proposals_status_chk", ("pending_approval", "approved", "rejected")),
    ("public.rebalance_proposals", "rebalance_proposals_kind_chk", ("options",)),
    ("public.declared_outside_trades", "declared_outside_trades_subject_chk", ("household",)),
    ("public.declared_outside_trades", "declared_outside_trades_class_chk", ("taxable",)),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("index", "fund", "equity", "option")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.replica_securities", "replica_securities_status_chk", ("active",)),
    ("portfolio.models", "models_source_chk", ("fund_replica",)),
)


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    find("1a security master", "an option is a securities_global row of type 'option' (added to the CHECK) plus a "
         "platform portfolio.option_contracts row (terms = identity, terms_key); positions / lots / instructions / "
         "wash-sale keep keying on global_security_id")
    find("1b terms / holdings", "the terms registry was global → class-scoped (instrument_classes, NULL = every class); "
         "holdings parsers type option legs 'other' with no strike / expiry, so legs come from approved terms")
    find("1c sleeve / proposal / instruction", "an options overlay is not a weight: option_packages on the model "
         "version; proposals = rebalance_proposals.proposal_kind 'options' with option_leg on each line; instructions "
         "carry contracts in quantity and the leg in option_terms (CHECK: manual only)")
    find("1d substantially identical", "wash_sale.identical() is the one decision point — extended with the option → "
         "underlying exposure (never weakened)")
    find("1e prices", "securities_global_prices holds no licensed series; quotes live in portfolio.adviser_quotes "
         "(insert-only, keyed by instrument + kind + as-of)")
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices", fetch="fetchval")
    find("securities_global_prices rows today", f"{n}")
    a = await platform(conn, "SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code = 'altruist' "
                             "AND valid_to IS NULL AND value <> 'unknown'", fetch="fetchval")
    check(int(a) == 0, "every Altruist capability is still 'unknown' (options level / FLEX unconfirmed)", f"{a}")


async def y_schema(conn):
    section("[Y] Schema landed: classes, option contracts, packages / legs, quotes, gate results; RLS for every op")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep8.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep8.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    for t in NEW_TABLES:
        s, n = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", s, n)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    cls = {r["key"]: r["enabled"] for r in await platform(
        conn, "SELECT key, enabled FROM portfolio.replica_instrument_classes WHERE key = ANY($1::text[])",
        ["buffer_etf", "income_etf", "index_etf"])}
    check(cls == {"buffer_etf": True, "income_etf": True, "index_etf": True}, "instrument classes buffer_etf / "
          "income_etf enabled (lookup rows read before seeding)", f"{cls}")
    S["classes_ok"] = cls.get("buffer_etf") is True
    uq = await platform(conn, "SELECT indexname FROM pg_indexes WHERE indexname LIKE 'option_%_uq'")
    fks = await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY($1::regclass[])",
                         list(NEW_TABLES), fetch="fetchval")
    check(len(uq) >= 7 and int(fks) >= 20, "UNIQUE (partial) and FK constraints of the new tables read from the "
          "catalog", f"{len(uq)} uniques, {fks} FKs")


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
          f"{bad[:4]}")
    S["literals_ok"] = not bad


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM public.households WHERE name = $1", HH_NAME, fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(h) == 0 and int(o) == 0,
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', the household name, the fixture orgs) match no "
                 "real row", f"{n}/{k}/{h}/{o}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP8 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        desc, perms = C.BASELINE_ROLES["super_admin"]
        rid_super = await rbac.ensure_role(conn, ORG_A, "super_admin", desc)
        for p in perms:
            if p in perm_ids:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid_super, perm_ids[p])
        rid_view = await rbac.ensure_role(conn, ORG_A, "rep8_viewer", f"REP8 verify — {', '.join(VIEWER_PERMS)}")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p])
        roles = {"super_admin": rid_super, "rep8_viewer": rid_view}
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for ent, org in ((ENT_A, ORG_A), (ENT_A2, ORG_A), (ENT_A3, ORG_A), (ENT_B, ORG_B)):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"REP8 Verify {str(ent)[-4:]}")
        await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                           HH, ORG_A, HH_NAME, U_ADMIN1)
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES[key],
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:05d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        salts = {}
        for org in FIXTURE_ORGS:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, hh, ent, held, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, true, $8)",
                aid, org, n.masked, n.hashed(salts[org]), code, ent, hh, held)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        # the core sleeve the managed account already holds: CORE 1,000 sh, $1,000,000, cost $800,000 (appreciated)
        asset, pid = uuid.uuid4(), uuid.uuid4()
        await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) VALUES "
                           "($1, $2, $3, $4, 'fund')", asset, ORG_A, S["sec_CORE"], SEC_NAMES["CORE"])
        await conn.execute(
            "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, market_value, "
            "authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', 'manual', $8)",
            pid, ORG_A, ENT_A, asset, ago(1), CORE_QTY, CORE_MV, ACCT_M)
        await conn.execute(
            "INSERT INTO portfolio.tax_lots (org_id, position_id, acquired_date, quantity, adjusted_cost, original_cost, "
            "term, status) VALUES ($1, $2, $3, $4, $5, $5, 'long', 'open')", ORG_A, pid, ago(900), CORE_QTY, CORE_COST)
        await conn.execute(
            "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, cash_value, "
            "margin_balance, accrued_income, source_system, source_confidence, is_billing_source, is_final) "
            "VALUES ($1, $2, $3, 0, 0, 0, 0, 'manual', 'CONFIRMED', true, true)", ORG_A, ACCT_M, ago(1))
        # the buffer ETF in the registry, with a DRAFT terms version (no values) — filled in through the API below
        entry, ver = uuid.uuid4(), uuid.uuid4()
        S["entry"], S["terms_draft"] = entry, ver
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3, 'buffer_etf', $4, "
            "'zz_rep8_test', 'active', $5, $6)", entry, ORG_A, S["sec_BUFR"], tk("BUFR"), f"Holdings of {tk('BUFR')}",
            U_ADMIN1)
        await conn.execute(
            "INSERT INTO portfolio.replica_terms_versions (id, org_id, replica_security_id, version_no, status, "
            "term_values, created_by) VALUES ($1, $2, $3, 1, 'draft', '{}'::jsonb, $4)", ver, ORG_A, entry, U_SUPER)
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(len(orgs) == 2 and all(r["is_test"] is True for r in orgs), "every fixture org is is_test = true (the live "
          "scheduler skips them)")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test AND auth0_sub LIKE $2",
                        list(USERS), f"%{TAG}%", fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly with the token's auth0_sub, is_test = true")
    S["seeded"] = True


async def y_terms(conn):
    section("[Y] Terms capture + maker-checker approval for a buffer ETF (rep1 pattern; CRITICAL_TERMS extended)")
    from services import replica_models as RM
    from services import replica_terms as T

    reg = await in_org(lambda c: T.load_registry(c, "buffer_etf"))
    idx = await in_org(lambda c: T.load_registry(c, "index_etf"))
    keys = {r["field_key"] for r in reg}
    check({"outcome_period_start", "cap_pct", "buffer_pct", "leg_short_call_strike_pct"} <= keys
          and not ({"cap_pct", "call_tenor_days"} & {r["field_key"] for r in idx}),
          "the registry is class-scoped: buffer fields for a buffer ETF, none of them for an index ETF")
    check(all(k in RM.CRITICAL_TERMS for k in ("outcome_period_start", "outcome_period_end", "cap_pct", "buffer_pct",
                                               "leg_long_put_strike_pct", "leg_short_put_strike_pct",
                                               "leg_short_call_strike_pct")),
          "CRITICAL_TERMS covers the outcome period, cap, buffer and every leg")
    values = {**TERMS_PCT, "outcome_period_start": ago(360).isoformat(),
              "outcome_period_end": (today() + timedelta(days=5)).isoformat(), "underlying_reference": "ZZR8 INDEX",
              "option_exercise_style": "european"}
    codes = []
    for k, v in values.items():
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{S['entry']}/terms/corrections",
                         {"version_id": str(S["terms_draft"]), "field_key": k, "value": v, "notes": "REP8 verify"})
        codes.append((k, c, str(o)[:120] if c >= 300 else ""))
    check(all(c == 200 for _, c, _ in codes), "an adviser enters every buffer term (no extraction ensemble needed)",
          f"{[x for x in codes if x[1] != 200][:3]}")
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{S['entry']}/terms/{S['terms_draft']}/approve",
                       {})
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/replication/registry/{S['entry']}/terms/{S['terms_draft']}/approve",
                       {})
    st = await platform(conn, "SELECT status, checker_id FROM portfolio.replica_terms_versions WHERE id = $1 AND "
                              "system_to IS NULL", S["terms_draft"], fetch="fetchrow")
    check(c1 == 403 and c2 == 200 and st is not None and st["status"] == "approved"
          and st["checker_id"] == U_ADMIN2, "approval by a second person: the maker is refused (403), another "
          "holder of approve_replication approves", f"{c1} {c2} {dict(st) if st else None} {str(o2)[:160]}")


async def y_builder(conn):
    section("[Y] Builder: core + options sleeve; minimum account exact or 'not priced'; quotes never updated")
    e = S["entry"]
    body = {"underlying_global_security_id": str(S["sec_IDX"]), "core_global_security_id": str(S["sec_CORE"])}
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/replication/options/entries/{e}/build", body)
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/entries/{e}/build", body)
    check(cv == 403 and c == 201 and (o.get("package") or {}).get("status") == "draft",
          "build: the viewer is refused (403 naming the permission); an admin builds a DRAFT package",
          f"{cv} {c} {str(o)[:200]}")
    S["pkg"], S["model"], S["version"] = (o.get("package") or {}).get("id"), (o.get("model") or {}).get("id"), \
        (o.get("version") or {}).get("id")
    pos = await platform(conn, "SELECT global_security_id, target_weight, sleeve_label FROM "
                               "portfolio.model_version_positions WHERE version_id = $1::uuid AND valid_to IS NULL",
                         S["version"])
    legs = await platform(conn, "SELECT role, strike_pct, ratio, contract_id FROM portfolio.option_package_legs WHERE "
                                "package_id = $1::uuid AND valid_to IS NULL ORDER BY leg_no", S["pkg"])
    roles = {r["role"]: r for r in legs}
    check(len(pos) == 1 and pos[0]["global_security_id"] == S["sec_CORE"] and D(pos[0]["target_weight"]) == 1
          and pos[0]["sleeve_label"] == "core", "core sleeve = the ETF itself, 100%, sleeve 'core'", f"{[dict(r) for r in pos]}")
    check(set(roles) == {"underlying", "long_put_atm", "short_put_buffer_end", "short_call_cap"}
          and D(roles["long_put_atm"]["strike_pct"]) == 1 and D(roles["short_put_buffer_end"]["strike_pct"]) == Decimal("0.9")
          and D(roles["short_call_cap"]["strike_pct"]) == Decimal("1.15")
          and all(r["contract_id"] is None for r in legs), "options sleeve = the four-leg package from the approved "
          "terms; no contract until a start level is quoted", f"{[dict(r) for r in legs]}")
    p = await platform(conn, "SELECT cap_pct, buffer_pct, floor_pct, stated_cap_pct FROM portfolio.option_packages "
                             "WHERE id = $1::uuid AND system_to IS NULL", S["pkg"], fetch="fetchrow")
    check(D(p["cap_pct"]) == Decimal("0.15") and D(p["buffer_pct"]) == Decimal("0.1")
          and D(p["floor_pct"]) == Decimal("-0.9") and D(p["stated_cap_pct"]) == Decimal("0.15"),
          "cap / buffer / floor computed from the legs and stored next to the stated values", f"{dict(p)}")
    _, d0 = await api(U_ADMIN1, "GET", f"/api/v1/replication/options/packages/{S['pkg']}")
    m0 = d0.get("minimum_account") or {}
    check(m0.get("status") == "not_priced" and "not priced" in str(m0.get("message")),
          "minimum account without a level quote: 'not priced — enter an index level'", f"{m0}")
    # quotes
    q_at = now() - timedelta(hours=1)
    cq, oq = await api(U_ADMIN1, "POST", "/api/v1/replication/options/quotes",
                       {"global_security_id": str(S["sec_IDX"]), "kind": "index_level", "value": str(LEVEL),
                        "source": "REP8 verify desk screen", "as_of": q_at.isoformat()})
    cf, _ = await api(U_ADMIN1, "POST", "/api/v1/replication/options/quotes",
                      {"global_security_id": str(S["sec_IDX"]), "kind": "index_level", "value": 5000.5,
                       "source": "x", "as_of": q_at.isoformat()})
    cp, _ = await api(U_ADMIN1, "POST", "/api/v1/replication/options/quotes",
                      {"global_security_id": str(S["sec_IDX"]), "kind": "premium", "value": "1",
                       "source": "x", "as_of": q_at.isoformat()})
    S["level_quote"] = (oq.get("quote") or {}).get("id")
    check(cq == 201 and cf == 422 and cp == 422 and (oq.get("quote") or {}).get("age") == "1 hour old",
          "a quote is entered with its source and as-of and shown with its age; a JSON float is a 422; a premium on "
          "an index is refused", f"{cq} {cf} {cp} {oq}")
    before = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.adviser_quotes", fetch="fetchval")
    try:
        await scoped(conn, ORG_A, "UPDATE portfolio.adviser_quotes SET value = 1 WHERE id = $1::uuid", S["level_quote"],
                     fetch="execute")
        upd = "updated"
    except asyncpg.PostgresError as exc:
        upd = type(exc).__name__
    after = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.adviser_quotes", fetch="fetchval")
    val = await scoped(conn, ORG_A, "SELECT value FROM portfolio.adviser_quotes WHERE id = $1::uuid", S["level_quote"],
                       fetch="fetchval")
    check(upd != "updated" and before == after and D(val) == LEVEL, "a quote is never updated in place (the guard "
          "refuses; value and count unchanged)", f"{upd} {before}→{after} {val}")
    _, d1 = await api(U_ADMIN1, "GET", f"/api/v1/replication/options/packages/{S['pkg']}")
    m1 = d1.get("minimum_account") or {}
    t = Decimal("0.05")
    n_per = Decimal(100) * LEVEL
    want_min = n_per / (Decimal(1) * (1 + t))
    want_cont = Decimal(10) * n_per / (1 + t)          # n* = ceil(0.95 / 0.10) = 10
    check(m1.get("status") == "priced" and D((m1.get("standard") or {}).get("minimum_account")) == want_min
          and D((m1.get("standard") or {}).get("continuous_from")) == want_cont
          and (m1.get("quote") or {}).get("id") == S["level_quote"] and m1.get("label") == "estimate",
          "minimum account exact: multiplier 100 × level 5000 ÷ (1 + 5% tolerance); continuous from n* = 10; the "
          "figure lists its quote and is labelled estimate", f"{m1}")
    # price the draft, maker-checker approval
    cpr, opr = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/packages/{S['pkg']}/price",
                         {"start_level_quote_id": S["level_quote"]})
    legs2 = await platform(conn, "SELECT l.role, l.strike_level, c.strike, c.is_flex, c.exercise_style, c.settlement, "
                                 "c.broad_based_index, c.global_security_id FROM portfolio.option_package_legs l JOIN "
                                 "portfolio.option_contracts c ON c.id = l.contract_id WHERE l.package_id = $1::uuid "
                                 "AND l.valid_to IS NULL", S["pkg"])
    S["contracts"] = {r["role"]: r["global_security_id"] for r in legs2}
    want = {"long_put_atm": Decimal(5000), "short_put_buffer_end": Decimal(4500), "short_call_cap": Decimal(5750)}
    check(cpr == 200 and len(legs2) == 3 and all(D(r["strike"]) == want[r["role"]] for r in legs2)
          and all(r["is_flex"] and r["exercise_style"] == "european" and r["settlement"] == "cash"
                  and r["broad_based_index"] for r in legs2),
          "pricing: strike levels from the start level; FLEX index options (European, cash-settled, broad-based) "
          "defined in the security master", f"{cpr} {[dict(r) for r in legs2]} {str(opr)[:160]}")
    cs, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/packages/{S['pkg']}/submit")
    ca1, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/packages/{S['pkg']}/approve", {})
    ca2, oa2 = await api(U_ADMIN2, "POST", f"/api/v1/replication/options/packages/{S['pkg']}/approve", {})
    check(cs == 200 and ca1 == 403 and ca2 == 200 and (oa2.get("package") or {}).get("status") == "approved",
          "package approval: maker refused (403), a second holder approves", f"{cs} {ca1} {ca2}")
    # the options model's own rep3 approval, so an account can join it
    pool = await get_pool()
    with _ctx(ORG_A):
        await M.submit(pool, org_id=ORG_A, actor_id=U_ADMIN1, model_id=S["model"], version_id=S["version"])
        await M.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, model_id=S["model"], version_id=S["version"],
                       outcome="approve")


async def y_payoff(conn):
    section("[Y] Payoff lab exact at every edge; mark-to-market only from quotes")
    _, o = await api(U_VIEWER, "GET", f"/api/v1/replication/options/packages/{S['pkg']}/payoff")
    pts = {D(p["underlying_return"]): p for p in o.get("points") or []}
    bad = []
    for r in ("-0.50", "-0.15", "-0.10", "-0.05", "0", "0.05", "0.15", "0.30"):
        x = Decimal(r)
        p = pts.get(x)
        if p is None or D(p["replica_return"]) != stated(x) or D(p["stated_return"]) != stated(x):
            bad.append((r, p))
    check(not bad and len(pts) > 8, "at expiry the replica equals the stated outcome at −50, −15, −10, −5, 0, +5, "
          "+15, +30 (both buffer edges and the cap), exactly", f"{bad[:3]}")
    mtm = o.get("mark_to_market") or {}
    check(mtm.get("status") == "not_priced" and "not priced" in str(mtm.get("message"))
          and (o.get("reprice") or {}).get("status") == "not_priced"
          and (o.get("note_comparison") or {}).get("phase") == "phase 3"
          and (o.get("note_comparison") or {}).get("status") == "empty",
          "without premium quotes: mark-to-market and re-priced cap read 'not priced'; the note slot is phase 3, empty",
          f"{mtm} {o.get('reprice')}")
    at = now() - timedelta(minutes=30)
    for role, prem in PREMIUMS.items():
        await api(U_ADMIN1, "POST", "/api/v1/replication/options/quotes",
                  {"global_security_id": str(S["contracts"][role]), "kind": "premium", "value": str(prem),
                   "source": "REP8 verify quote", "as_of": at.isoformat()})
    _, o2 = await api(U_VIEWER, "GET", f"/api/v1/replication/options/packages/{S['pkg']}/payoff")
    m2 = o2.get("mark_to_market") or {}
    want = (PREMIUMS["long_put_atm"] - PREMIUMS["short_put_buffer_end"] - PREMIUMS["short_call_cap"]) * 100
    check(m2.get("status") == "priced" and D(m2.get("value_per_unit")) == want and m2.get("label") == "estimate"
          and all((ln.get("quote") or {}).get("as_of") for ln in m2.get("lines") or []),
          "mark-to-market from the premium quotes only, labelled estimate, each line with its quote's as-of",
          f"{m2}")
    rp = o2.get("reprice") or {}
    check(rp.get("status") == "not_priced", "re-priced cap: the only quoted call (40) does not cover the net put cost "
          "(50) → not priced (never interpolated)", f"{rp}")
    exp = (await platform(conn, "SELECT expiry FROM portfolio.option_contracts WHERE global_security_id = $1",
                          S["contracts"]["short_call_cap"], fetch="fetchval"))
    cc, oc = await api(U_ADMIN1, "POST", "/api/v1/replication/options/contracts",
                       {"underlying_global_security_id": str(S["sec_IDX"]), "option_type": "call", "strike": "5500",
                        "expiry": exp.isoformat(), "exercise_style": "european", "settlement": "cash", "is_flex": True,
                        "multiplier": "100", "broad_based_index": True})
    cc2, oc2 = await api(U_ADMIN1, "POST", "/api/v1/replication/options/contracts",
                         {"underlying_global_security_id": str(S["sec_IDX"]), "option_type": "call", "strike": "5500",
                          "expiry": exp.isoformat(), "exercise_style": "european", "settlement": "cash",
                          "is_flex": True, "multiplier": "100", "broad_based_index": True})
    same = (oc.get("contract") or {}).get("id") == (oc2.get("contract") or {}).get("id")
    await api(U_ADMIN1, "POST", "/api/v1/replication/options/quotes",
              {"global_security_id": (oc.get("contract") or {}).get("global_security_id"), "kind": "premium",
               "value": "60", "source": "REP8 verify quote", "as_of": at.isoformat()})
    _, o3 = await api(U_VIEWER, "GET", f"/api/v1/replication/options/packages/{S['pkg']}/payoff")
    rp3 = o3.get("reprice") or {}
    check(cc == 201 and cc2 == 201 and same and rp3.get("status") == "priced"
          and D(rp3.get("cap_pct")) == Decimal("0.1") and len(rp3.get("quotes") or []) == 3,
          "a contract is defined once (same terms → same contract); with a covering quoted strike the new cap is "
          "5500 ÷ 5000 − 1 = 10%, listing its three quotes", f"{cc} {cc2} {same} {rp3}")


def _leg(role, otype, direction, effect, contract, *, strike_pct=None, est=None):
    return {"trade_id": None, "role": role, "option_type": otype, "direction": direction, "position_effect": effect,
            "side": "buy", "contracts": Decimal(1), "price": None, "strike_pct": strike_pct, "est_result": est,
            "contract": contract}


def _contract(gsid, und, kind, otype, strike, expiry, *, broad=True, listed=True, flex=False):
    return {"global_security_id": gsid, "underlying_global_security_id": und, "underlying_kind": kind,
            "option_type": otype, "strike": Decimal(strike), "expiry": expiry, "broad_based_index": broad,
            "exchange_listed": listed, "is_flex": flex, "multiplier": Decimal(100)}


async def y_gate_rules(conn):
    section("[Y] Tax gate: every rule, both edges where dated, pending-counsel labels, blocks name the rule")
    stk, opt = str(S["sec_STK"]), "opt-" + str(uuid.uuid4())

    def ctx_with(loss_day: date | None):
        ev = [WS.Event(stk, "sell", loss_day, loss_day, True, None, "outside", "taxable", "declared_outside")] \
            if loss_day else []
        return WS.Context(today=today(), events=ev, denials={}, substitutes={}, labels={stk: "STK"},
                          exposure={opt: stk})

    core_none = {"holds_offset": False, "offset_label": "none", "appreciation": None, "labels": {}}
    call = _contract(opt, stk, "equity", "call", "100", today() + timedelta(days=60), broad=False)
    put = _contract(opt, stk, "equity", "put", "100", today() + timedelta(days=60), broad=False)
    idx = _contract(opt, str(S["sec_IDX"]), "index", "call", "5000", today() + timedelta(days=60), flex=True)
    r = G.rule_1256(_leg("short_call_cap", "call", "short", "open", idx))
    r2 = G.rule_1256(_leg("short_call_income", "call", "short", "open", call))
    check(r["outcome"] == "flag" and "60%" in r["reason"] and "marked to market" in r["reason"]
          and r2["outcome"] == "pass" and "not a Section 1256" in r2["reason"],
          "Section 1256: a broad-based index option → flag (60/40, year-end mark-to-market); a single-stock option → "
          "not 1256")
    edge = {}
    for d in (-30, -31, 30, 31):
        res = G.rule_wash(_leg("long_call", "call", "long", "open", call), ctx_with(today() + timedelta(days=d)))
        edge[d] = res["outcome"]
    check(edge == {-30: "block", -31: "pass", 30: "block", 31: "pass"},
          "wash sale: a call bought on a household loss-sale issuer is blocked on day L ± 30 and clear on L ± 31",
          f"{edge}")
    cp = G.rule_wash(_leg("short_put", "put", "short", "open", put), ctx_with(ago(10)))
    lp = G.rule_wash(_leg("long_put_atm", "put", "long", "open", put), ctx_with(ago(10)))
    check(cp["rule"] == "cash_secured_put" and cp["outcome"] == "block" and "Writing a put" in cp["reason"]
          and lp["outcome"] == "pass", "cash-secured put on a stock sold at a loss within 30 days → block naming "
          "cash_secured_put; a put bought acquires nothing → pass", f"{cp['reason'][:120]}")
    ctx0 = ctx_with(None)
    check(WS.identical(opt, stk, ctx0) and not WS.identical(stk, str(S["sec_CORE"]), ctx0)
          and WS.identical(stk, stk, ctx0), "wash_sale.identical: an option is its underlying's exposure; unrelated "
          "securities stay distinct; the same security is still identical (never weakened)")
    core = {"holds_offset": True, "offset_label": "core sleeve (CORE)", "appreciation": Decimal(200000), "labels": {}}
    st = G.rule_straddle([_leg("short_call_cap", "call", "short", "open", idx)], core)
    sl = G.rule_straddle([_leg("long_put_atm", "put", "long", "close", {**idx, "option_type": "put"},
                               est=Decimal(-100))], core)
    sn = G.rule_straddle([_leg("long_put_atm", "put", "long", "close", {**idx, "option_type": "put"})], core)
    check(st[0]["outcome"] == "flag" and st[0]["pending_counsel"] and "pending counsel review" in st[0]["reason"]
          and "holding period" in st[0]["reason"] and sl[0]["outcome"] == "block" and sl[0]["rule"] == "straddle"
          and sn[0]["outcome"] == "block" and G.rule_straddle([_leg("x", "call", "short", "open", idx)], core_none)[0][
              "outcome"] == "pass",
          "straddle: index options vs the basket identified, pending counsel (loss deferral, holding-period "
          "suspension); a loss — or an unknown result — on one leg while the offset is open → block; nothing held → "
          "pass")
    exp = today() + timedelta(days=60)
    lpc = _contract(opt, stk, "equity", "put", "100", exp, broad=False)
    scc = _contract(opt, stk, "equity", "call", "115", exp, broad=False)

    def collar(call_pct, appr, with_short_put=False):
        legs = [_leg("long_put_atm", "put", "long", "open", lpc, strike_pct=Decimal(1)),
                _leg("short_call_cap", "call", "short", "open", scc, strike_pct=Decimal(call_pct))]
        if with_short_put:
            legs.append(_leg("short_put_buffer_end", "put", "short", "open", lpc, strike_pct=Decimal("0.9")))
        return G.rule_constructive_sale(legs, {**core, "appreciation": appr}, Decimal("0.15"))[0]

    at, inside, wider = collar("1.15", Decimal(1)), collar("1.10", Decimal(1)), collar("1.16", Decimal(1))
    unknown, buffer_pkg = collar("1.15", None), collar("1.15", Decimal(1), with_short_put=True)
    check(at["outcome"] == "block" and inside["outcome"] == "block" and wider["outcome"] == "pass"
          and unknown["outcome"] == "block" and buffer_pkg["outcome"] == "pass" and at["rule"] == "constructive_sale",
          "constructive sale: a collar on an appreciated position at / inside the 15% band → block; wider → pass; "
          "appreciation unknown → block; a buffer package (written put below) is not a collar",
          f"{at['outcome']}/{inside['outcome']}/{wider['outcome']}/{unknown['outcome']}/{buffer_pkg['outcome']}")
    price = {"value": "100", "as_of": now().isoformat(), "id": "q"}

    def qcc(days, strike, q=price, listed=True):
        c = _contract(opt, stk, "equity", "call", strike, today() + timedelta(days=days), broad=False, listed=listed)
        return G.rule_qcc(_leg("short_call_income", "call", "short", "open", c), today=today(), price_quote=q)

    q31, q30, deep, noprice = qcc(31, "100"), qcc(30, "100"), qcc(60, "90"), qcc(60, "100", q=None)
    check(q31["outcome"] == "pass" and q30["outcome"] == "block" and deep["outcome"] == "block"
          and noprice["outcome"] == "block" and "cannot qualify without a price" in noprice["reason"]
          and qcc(60, "100", listed=False)["outcome"] == "block",
          "qualified covered call: 31 days pass / 30 days fail; deep in the money fails; no price → block 'cannot "
          "qualify without a price'; not exchange-traded fails",
          f"{q31['outcome']}/{q30['outcome']}/{deep['outcome']}/{noprice['outcome']}")


async def join_and_clear(acct):
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct}/model/join", {"model_id": str(S["model"]),
                                                                             "settings": {}})
    lid = (o.get("link") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct}/model/changes/{lid}/approve", {})
    op = await in_org(lambda cn: RP.open_proposal(cn, acct))
    if op is not None and op["status"] == "pending_approval":
        await api(U_ADMIN2, "POST", f"/api/v1/proposals/{op['id']}/reject", {"note": "REP8 verify: clear the slot"})
    left = await in_org(lambda cn: RP.open_proposal(cn, acct))
    return c, c2, left


async def y_proposals(conn):
    section("[Y] Proposals → manual per-leg instructions; the gate at approval; never an adapter; authority respected")
    cj, cj2, left = await join_and_clear(ACCT_M)
    check(cj == 201 and cj2 == 200 and left is None, "the managed account joins the options model (rep5's maker-"
          "checker) and has no open proposal", f"{cj} {cj2} {left and left['status']}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/accounts/{ACCT_M}/proposals",
                     {"package_id": S["pkg"], "action": "open"})
    p = o.get("proposal") or {}
    S["prop"] = p.get("id")
    trades = p.get("trades") or []
    check(c == 201 and p.get("proposal_kind") == "options" and len(trades) == 3
          and all(D(t["quantity"]) == 2 and t["price_source"] == "adviser_quote" for t in trades)
          and {(t["option_leg"]["role"], t["side"]) for t in trades} ==
          {("long_put_atm", "buy"), ("short_put_buffer_end", "sell"), ("short_call_cap", "sell")},
          "an options proposal: 2 contracts per leg (core $1,000,000 ÷ (100 × 5000)), priced from the premium "
          "quotes, buy / sell-to-open by leg", f"{c} {str(o)[:300]}")
    _, g = await api(U_VIEWER, "GET", f"/api/v1/replication/options/proposals/{S['prop']}/gate")
    res = g.get("results") or []
    rules = {(r["rule"], r["outcome"]) for r in res}
    check(("section_1256", "flag") in rules and ("straddle", "flag") in rules
          and any(r["pending_counsel"] and "pending counsel review" in r["reason"] for r in res)
          and ("constructive_sale", "pass") in rules and not g.get("blocking")
          and all(r.get("inputs") for r in res if r["trade_id"]),
          "the gate ran at generate and is stored per leg with its inputs: 1256 flagged, straddle identified "
          "(pending counsel), no block", f"{sorted(rules)}")
    cs, _ = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{S['prop']}/submit")
    # a household loss sale of the index within 30 days → the written put is a wash-sale purchase
    dec = uuid.uuid4()
    await scoped(conn, ORG_A, "INSERT INTO public.declared_outside_trades (id, org_id, subject_type, subject_id, "
                              "account_description, account_tax_class, global_security_id, side, trade_date, loss, "
                              "declared_by, source_note) VALUES ($1, $2, 'household', $3, 'ZZR8 outside account', "
                              "'taxable', $4, 'sell', $5, 'yes', $6, 'REP8 verify')",
                 dec, ORG_A, HH, S["sec_IDX"], ago(5), U_ADMIN1, fetch="execute")
    cb, ob = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{S['prop']}/approve", {})
    st = await platform(conn, "SELECT status FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS NULL",
                        S["prop"], fetch="fetchval")
    n_ins = await platform(conn, "SELECT count(*) FROM public.execution_instructions WHERE source_ref = $1::uuid",
                           S["prop"], fetch="fetchval")
    check(cs == 200 and cb == 409 and "cash_secured_put" in str(ob) and st == "pending_approval" and int(n_ins) == 0,
          "approval refused while a gate rule blocks: 409 naming the rule; the proposal stays awaiting approval; no "
          "instruction", f"{cs} {cb} {str(ob)[:200]} {st} {n_ins}")
    await scoped(conn, ORG_A, "UPDATE public.declared_outside_trades SET valid_to = now(), withdraw_reason = "
                              "'REP8 verify' WHERE id = $1", dec, fetch="execute")
    ca1, _ = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{S['prop']}/approve", {})
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{S['prop']}/approve", {})
    ins = await platform(conn, "SELECT * FROM public.execution_instructions WHERE source_ref = $1::uuid AND "
                               "system_to IS NULL", S["prop"])
    S["instructions"] = [dict(r) for r in ins]
    terms = [json.loads(r["option_terms"]) if isinstance(r["option_terms"], str) else r["option_terms"] for r in ins]
    check(ca1 == 403 and ca == 200 and len(ins) == 3 and all(r["routed_via"] == "manual" and r["status"] == "approved"
                                                             and r["executor_type"] == "custodian_desk"
                                                             and r["price_kind"] == "limit" and D(r["quantity"]) == 2
                                                             for r in ins)
          and all(t and t["position_effect"] == "open" and t.get("expiry") and t.get("is_flex") is True
                  and t.get("quote_as_of") for t in terms)
          and {D(r["limit_price"]) for r in ins} == set(PREMIUMS.values()),
          "maker refused; the checker approves → one MANUAL instruction per leg: contracts, a limit from the adviser "
          "quote (its as-of carried), expiry and FLEX terms", f"{ca1} {ca} {str(oa)[:200]}")
    check(ADAPTER_CALLS == [], "no trade adapter (not even the manual one's submit) was called for an options leg",
          f"{ADAPTER_CALLS[:3]}")
    cro, oro = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/accounts/{ACCT_RO}/proposals",
                         {"package_id": S["pkg"], "action": "open"})
    cch, och = await api(U_ADMIN1, "POST", f"/api/v1/authority/accounts/{ACCT_ADV}/changes",
                         {"authority_override": "advise", "custody": "integrated", "effective_from": today().isoformat(),
                          "reason": "REP8 verify"})
    cid = (och.get("change") or {}).get("id")
    cd, _ = await api(U_ADMIN2, "POST", f"/api/v1/authority/changes/{cid}/approve", {})
    cad, oad = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/accounts/{ACCT_ADV}/proposals",
                         {"package_id": S["pkg"], "action": "open"})
    check(cro == 409 and "read-only" in str(oro) and cch == 201 and cd == 200 and cad == 409
          and "options sleeve" in str(oad),
          "authority: a read-only (held-away) account and an advise account are refused an options proposal (409)",
          f"{cro} {str(oro)[:120]} {cch} {cd} {cad} {str(oad)[:160]}")


async def y_fills(conn):
    section("[Y] Fills entered manually → option lots booked (idempotent)")
    pool = await get_pool()
    booked = []
    for r in S.get("instructions") or []:
        with _ctx(ORG_A):
            await X.mark_sent(pool, org_id=ORG_A, instruction_id=r["id"], actor_id=U_ADMIN1, channel="phone")
            await X.enter_fill(pool, org_id=ORG_A, instruction_id=r["id"], actor_id=U_ADMIN1, quantity=Decimal(2),
                               price=D(r["limit_price"]), filled_at=now() - timedelta(minutes=1))
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/instructions/{r['id']}/book")
        c2, o2 = await api(U_ADMIN1, "POST", f"/api/v1/replication/options/instructions/{r['id']}/book")
        booked.append((c, len(o.get("booked") or []), c2, len(o2.get("skipped") or [])))
    lots = await scoped(conn, ORG_A, "SELECT quantity, original_cost FROM portfolio.tax_lots WHERE external_ref LIKE "
                                     "'option_fill:%' AND valid_to IS NULL AND status = 'open'")
    want = sorted([(Decimal(2), Decimal(2) * 120 * 100), (Decimal(-2), Decimal(-2) * 70 * 100),
                   (Decimal(-2), Decimal(-2) * 40 * 100)])
    got = sorted((D(r["quantity"]), D(r["original_cost"])) for r in lots)
    check(booked == [(200, 1, 200, 1)] * 3 and got == want, "each fill booked once as an option lot (long +, short −; "
          "cost = premium × 100 × contracts); a second booking skips it", f"{booked} {got}")


async def y_rolls(conn):
    section("[Y] Rolls and expiry: to-do + draft next package; expired → to-do + settlement entry; is_test skipped")
    pool = await get_pool()
    with _ctx(ORG_A):
        tick = await RP.org_tick(pool, org_id=ORG_A, today=today())
    check("test org" in str(tick.get("skipped")) and tick.get("options") is None,
          "the live sweep (rep5's org_tick) skips the is_test fixture org — no roll, no expiry", f"{tick.get('skipped')}")
    with _ctx(ORG_A):
        r1 = await OS.sweep(pool, org_id=ORG_A, today=today())
        r2 = await OS.sweep(pool, org_id=ORG_A, today=today())
    roll = await platform(conn, "SELECT * FROM portfolio.option_packages WHERE rolled_from_id = $1::uuid AND "
                                "system_to IS NULL", S["pkg"])
    roles = [r["role"] for r in await platform(conn, "SELECT role FROM portfolio.option_package_legs WHERE package_id = "
                                                     "$1 AND valid_to IS NULL", roll[0]["id"])] if roll else []
    tasks = await in_org(lambda c: HT.list_tasks(c, ORG_A, status="open", kind=OS.TASK_ROLL))
    end = await platform(conn, "SELECT outcome_end FROM portfolio.option_packages WHERE id = $1::uuid AND system_to "
                               "IS NULL", S["pkg"], fetch="fetchval")
    check(len(r1["rolls"]) == 1 and not r2["rolls"] and len(roll) == 1 and roll[0]["status"] == "draft"
          and roll[0]["cap_pct"] is None and D(roll[0]["buffer_pct"]) == Decimal("0.1")
          and roll[0]["outcome_start"] == end and "short_call_cap" not in roles and len(tasks) == 1,
          f"{(end - today()).days} days before the period ends (notice 10): a roll to-do with a DRAFT next package "
          "(same buffer, cap not priced); idempotent", f"{r1} {[dict(x) for x in roll][:1]} {roles}")
    later = end + timedelta(days=1)
    with _ctx(ORG_A):
        r3 = await OS.sweep(pool, org_id=ORG_A, today=later)
    st = await platform(conn, "SELECT status, status_reason FROM portfolio.option_packages WHERE id = $1::uuid AND "
                              "system_to IS NULL", S["pkg"], fetch="fetchrow")
    ents = await scoped(conn, ORG_A, "SELECT * FROM public.option_settlement_entries WHERE package_id = $1::uuid AND "
                                     "valid_to IS NULL", S["pkg"])
    exp_tasks = await in_org(lambda c: HT.list_tasks(c, ORG_A, status="open", kind=OS.TASK_EXPIRED))
    check(len(r3["expired"]) == 1 and st["status"] == "expired" and len(ents) == 3
          and all(e["account_id"] == ACCT_M and e["settlement_level"] is None and e["status"] == "pending"
                  for e in ents) and len(exp_tasks) == 1,
          "past expiry with no approved roll: expired, a to-do, a pending settlement entry per cash-settled leg for "
          "the holding account (not priced — no level quote on the expiry day)", f"{r3} {dict(st)} {len(ents)}")
    refused = None
    try:
        await pool_call(lambda p: HT.complete_task(p, org_id=ORG_A, task_id=exp_tasks[0]["id"], actor_id=U_ADMIN1,
                                                   outcome="done"))
    except HT.HumanTaskConflict as exc:
        refused = str(exc)
    check(refused is not None and "not priced" in refused, "confirming settlement is refused while an entry is not "
          "priced", f"{refused}")


async def y_viability(conn):
    section("[Y] Viability: Altruist options 'unknown' → manual-only note; core unaffected")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{S['entry']}/viability",
                     {"custodian_code": "altruist"})
    ans = {a["key"]: a for a in (o.get("answers") or [])}
    q4 = ans.get("custodian") or {}
    check(c in (200, 201) and q4.get("status") == "yes"
          and "manual execution only — Altruist options access unconfirmed" in q4.get("reason", "")
          and (q4.get("inputs") or {}).get("options", {}).get("core_affected") is False
          and (ans.get("rules_based") or {}).get("status") == "yes",
          "Q4 reads options_level / flex_options: 'unknown' → manual execution only, never a block on the core; Q1 "
          "rules-based", f"{c} {q4.get('status')} {q4.get('reason', '')[:200]}")
    _, env = await api(U_ADMIN1, "GET", f"/api/v1/replication/options/entries/{S['entry']}")
    check((env.get("viability") or {}).get("manual_only") is True and "unconfirmed" in str(env.get("viability")),
          "the Options tab carries the same manual-only note")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    cbq, _ = await api(U_ADMIN_B, "POST", "/api/v1/replication/options/quotes",
                       {"global_security_id": str(S["sec_IDX"]), "kind": "index_level", "value": "4999",
                        "source": "org B", "as_of": (now() - timedelta(minutes=5)).isoformat()})
    res = {}
    for t in NEW_ORG_TABLES:
        res[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")),
                  int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")))
    bb = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.adviser_quotes WHERE org_id = $1", ORG_B,
                          fetch="fetchval"))
    ba = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.adviser_quotes WHERE org_id = $1", ORG_B,
                          fetch="fetchval"))
    check(cbq == 201 and all(a > 0 and b == 0 for a, b in res.values()) and bb == 1 and ba == 0,
          "RLS: org A's rows in every new org table are non-empty and invisible to org B; org B's quote invisible to "
          "org A", f"{res} {bb} {ba}")
    codes = [(await api(U_ADMIN_B, "GET", f"/api/v1/replication/options/entries/{S['entry']}"))[0],
             (await api(U_ADMIN_B, "GET", f"/api/v1/replication/options/packages/{S['pkg']}"))[0],
             (await api(U_ADMIN_B, "GET", f"/api/v1/replication/options/proposals/{S['prop']}/gate"))[0],
             (await api(U_ADMIN_B, "POST", f"/api/v1/replication/options/packages/{S['pkg']}/approve", {}))[0]]
    _, qb = await api(U_ADMIN_B, "GET", "/api/v1/replication/options/quotes")
    check(codes == [404] * 4 and len(qb.get("rows") or []) == 1, "another org's entry, package, gate and approval are "
          "404; org B lists only its own quote", f"{codes} {len(qb.get('rows') or [])}")
    body = {"global_security_id": str(S["sec_IDX"]), "kind": "index_level", "value": "5001", "source": "gates",
            "as_of": (now() - timedelta(minutes=5)).isoformat()}
    cv, ov = await api(U_VIEWER, "POST", "/api/v1/replication/options/quotes", body)
    ca, _ = await api(U_ADMIN1, "POST", "/api/v1/replication/options/quotes", body)
    cvg, ovg = await api(U_VIEWER, "POST", f"/api/v1/replication/options/proposals/{S['prop']}/gate")
    check(cv == 403 and "manage_replication" in str(ov) and ca == 201 and cvg == 403 and "manage_proposals" in str(ovg),
          "gates both ways on identical requests: the viewer is refused naming the permission, the admin accepted")
    _, ev = await api(U_VIEWER, "GET", f"/api/v1/replication/options/entries/{S['entry']}")
    pv, vv = ev.get("permissions") or {}, ev.get("vocabularies") or {}
    check(pv.get("can_write") is False and pv.get("can_approve") is False and pv.get("can_build") is False
          and vv.get("editable") == [] and vv.get("price_editable") == [] and vv.get("quote_editable") == []
          and bool(vv.get("leg_roles")) and bool(vv.get("gate_rules")) and bool(vv.get("quote_kinds"))
          and ((ev.get("current") or {}).get("actions") == []),
          "view-only envelope: no write flag, empty editable lists, no actions; leg roles / gate rules / quote kinds "
          "from the envelope", f"{pv} {(ev.get('current') or {}).get('actions')}")


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now_ = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now_)
    changed = [k for k in gb if k in gn and (gb[k].get("after"), gb[k].get("expect")) !=
               (gn[k].get("after"), gn[k].get("expect"))]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    new = [k for k in gn if k not in gb]
    bad = [(k, gn[k].get("after")) for k in new if NEW_ROUTES.get((k[0][0] if k[0] else "", k[1])) is None
           or NEW_ROUTES[(k[0][0], k[1])] not in str(gn[k].get("after"))]
    check(len(new) == len(NEW_ROUTES) and not bad, f"{len(NEW_ROUTES)} new routes, all under /replication/options/, "
          "each naming its permission", f"{len(new)} {bad[:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^ok \d+ - (?:view-only: no build|controls appear only|build only before|propose / roll|"
                      r"minimum account:|payoff lab:|tax gate:)", out, re.M)
    check(len(mine) >= 7 and "not ok" not in out, "the rep8 render tests ran and passed (no approve / roll / write "
          "control without the server's flags)", f"{len(mine)}")
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
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_rep6.py: 0 FAIL",
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


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown_retrying()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    seconds = _time.monotonic() - t0
    left = await leftovers(conn)
    print(f"CLEANUP_SECONDS: {seconds:.1f}")
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
        if S.get("classes_ok") and S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("terms", y_terms, conn)
            await run("builder", y_builder, conn)
            await run("gate rules", y_gate_rules, conn)
            if S.get("pkg") and S.get("contracts"):
                await run("payoff", y_payoff, conn)
                await run("proposals", y_proposals, conn)
                await run("fills", y_fills, conn)
                await run("rolls", y_rolls, conn)
                await run("isolation + gates", y_isolation, conn)
            else:
                check(False, "the builder made and priced a package", "every package-scoped section skipped")
            await run("viability", y_viability, conn)
        else:
            check(False, "seed created the fixtures", "every fixture-scoped section skipped")
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
