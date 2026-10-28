"""LOTMETHOD verify — the lot-relief method (household default, account
override, per-proposal override), the truer tax-optimized ranking, the
custodian's own method and average cost (Part A); BUGFIX2's fast scoped
verify cleanup and backlog items B2–B6 (Part B).

    python3 apps/api/scripts/verify_lotmethod.py

Pass / fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on a failure. AUTHZ_TEST_MODE=1 is set before the app is imported; secrets come
from Doppler over HTTPS (_db_connect.hydrate); every outbound HTTP call is
refused (a network call is a FAIL). Fixture orgs and users are is_test = true;
every expected date is computed AT THE CALL. Teardown (start and end) is the
shared scoped helper ``_verify_cleanup.cleanup`` keyed by THIS run's fixture
ids; every touched table is re-read for its exact pre-test count; rows outside
the fixture orgs are counted and checksummed before and after.

Subprocesses run synchronously with timeouts: the five --cleanup-only runs
(900 s each), node --test, npm run build, and the regression (verify_rep6.py
ONCE, flat, VERIFY_NESTED=1, 2700 s). With VERIFY_NESTED=1 this script prints
one '[SKIP] nested run' line instead of its regression section.

--cleanup-only: the teardown and the leftover check only (CLEANUP_SECONDS /
LEFTOVERS / TOTAL lines), as the other scripts' flag.
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
import time
import traceback
import types
import uuid
from datetime import date, timedelta
from decimal import Decimal, localcontext
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
from services import account_models as AM  # noqa: E402
from services import deal_lifecycle as DLC  # noqa: E402
from services import harvest as HV  # noqa: E402
from services import lot_method as LM  # noqa: E402
from services import models as M  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_engine as E  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import tax_profiles as TP  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import xray as XR  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

BEFORE_REF = VC.LOTMETHOD_BASE   # main @ merge rep7b — the lotmethod-work base
TAG = "lotmethodverify"
PREFIX = "ZZLM"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("S1", "S2", "K")
SEC_TYPES = {"S2": "fund"}
SEC_NAMES = {k: f"LOTMETHOD VERIFY {k}" for k in SEC_KEYS}
HH_NAME = "LOTMETHOD Verify Household"
DEAL_NAMES = ("LOTMETHOD Verify Deal One", "LOTMETHOD Verify Deal Two")
REP2_LEFTOVER_ORGS = [UUID(f"99000000-0000-0000-0000-0000000e200{n}") for n in (1, 2, 3)]


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000c1{n:03d}")


ORG_A, ORG_B, ORG_C = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_C]
U_ADMIN1, U_ADMIN2, U_VIEWER, U_ADMIN_B, U_NEWADMIN = (_id(110 + i) for i in range(5))
USERS = {   # org, users.role, short, rbac role, full name
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "LOTMETHOD Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "LOTMETHOD Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "lotmethod_viewer", "LOTMETHOD Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "LOTMETHOD Org B Admin"),
    U_NEWADMIN: (ORG_C, "member", "newadmin", None, "LOTMETHOD New Org Admin"),
}
VIEWER_PERMS = ("view_proposals",)
ENT_A, ENT_B = _id(201), _id(202)
HH_A = _id(211)
ACCT_A, ACCT_B = _id(301), _id(302)
ACCOUNTS = {ACCT_A: (ORG_A, "ALTRUIST", HH_A, "ZZLM000001"), ACCT_B: (ORG_B, "ALTRUIST", None, "ZZLM000002")}
PRICE = Decimal("100")
# S1 — five lots of 20 at differing cost, age, term (acquired N days ago, computed at the call)
S1_LOTS = {"LTG": ("50", 800), "STL": ("110", 100), "LTL": ("112", 900), "STG": ("90", 200), "LTG2": ("85", 500)}
SELL_QTY = Decimal("60")       # the model holds S1 at 40% of a 10,000 account → sell 60 of 100
HH_RATES = {"federal_ordinary_rate": "0.37", "federal_lt_rate": "0.2", "niit_applies": True, "state_code": "NY",
            "state_rate": "0.0685", "city": "New York", "city_rate": "0.03876", "city_lt_rate": "0.03"}
# exactly what each method must pick for the 60-share S1 sale (stated HERE, independently of the engine)
EXPECTED_DB = {
    "default": {"LTL", "STL", "STG"},            # today's rule: losses by highest cost, then lowest gain/$ raised
    "tax_optimized": {"STL", "LTL", "LTG2"},     # with the profile: ST loss, LT loss, then the LT gain (0.0505)
    "fifo": {"LTL", "LTG", "LTG2"},
    "lifo": {"STL", "STG", "LTG2"},
    "lowest_cost": {"LTG", "LTG2", "STG"},
    "hifo": {"LTL", "STL", "STG"},
}

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.account_balances_daily", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.tax_lots", "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
    "public.account_model_links", "public.rebalance_proposals", "public.rebalance_proposal_trades",
    "public.account_transition_plans", "public.tax_profiles", "public.execution_instructions", "public.deals",
    "public.household_lot_method", "public.account_lot_method_settings",
)
NEW_TABLES = ("public.household_lot_method", "public.account_lot_method_settings")

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
    print(f"\n── {title} ──", flush=True)


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, out, f"timeout after {timeout}s"


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


def jl(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


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


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch: everything outside this run's own fixture orgs."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("tax_lots", "portfolio.tax_lots"), ("execution_instructions", "public.execution_instructions"),
                        ("execution_instruction_lots", "public.execution_instruction_lots"),
                        ("rebalance_proposal_trades", "public.rebalance_proposal_trades"),
                        ("household_lot_method", "public.household_lot_method"),
                        ("account_lot_method_settings", "public.account_lot_method_settings")):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["rebalance_proposals"] = await fingerprint(conn, "SELECT * FROM public.rebalance_proposals WHERE NOT "
                                                         "(org_id = ANY($1::uuid[]))", orgs)
    # deals: the B6 backfill already ran (_apply_lotmethod.py); this run must change nothing more outside its orgs
    out["deals"] = await fingerprint(conn, "SELECT * FROM public.deals WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
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
    rows = await conn.fetch("SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers "
                            "WHERE id_value LIKE $1", f"{PREFIX}%")
    ids = {r["global_security_id"] for r in rows}
    ids |= {r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name = ANY($1::text[])",
                                              list(SEC_NAMES.values()))}
    return list(ids)


async def teardown() -> list[str]:
    """The shared scoped helper; every key is THIS run's fixture ids (orgs, users, securities)."""
    op = await operator_conn()
    try:
        secs = await fixture_security_ids(op)
        users = list(USERS)
        orgs = FIXTURE_ORGS
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan = {"public.member_todos": org_or_user, "public.audit_log": org_or_user,
                "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs])}
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_lotmethod")
        S.setdefault("cleanup_seconds", []).append(res["seconds"])
        return res["log"]
    finally:
        await close_conn(op)


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=(
            ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
            ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])",
             [list(SEC_NAMES.values())])))
    finally:
        await close_conn(op)


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'LOTMETHOD VERIFY%'",
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM public.households WHERE name = $1 AND NOT (org_id = ANY($2::uuid[]))",
                       HH_NAME, FIXTURE_ORGS, fetch="fetchval")
    d = await platform(conn, "SELECT count(*) FROM public.deals WHERE name = ANY($1::text[]) AND NOT "
                             "(org_id = ANY($2::uuid[]))", list(DEAL_NAMES), FIXTURE_ORGS, fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM public.users WHERE auth0_sub LIKE $1 AND NOT (org_id = "
                             "ANY($2::uuid[]))", f"%{TAG}%", FIXTURE_ORGS, fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(h) == 0 and int(d) == 0 and int(u) == 0,
                 f"the reserved fixture prefixes ({PREFIX}, 'LOTMETHOD VERIFY', the household / deal names, {TAG}) "
                 "match no real row", f"{n}/{k}/{h}/{d}/{u}")


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_lotmethod"}}
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
        raise AssertionError(f"verify_lotmethod: a network call was attempted: {request.url}")

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


async def in_org(fn, org=None, super_admin=False):
    pool = await get_pool()
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
    pool = await get_pool()
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


async def lots_of(pid) -> dict[str, Decimal]:
    """{fixture lot name: quantity} on a proposal's S1 sale line (RLS-scoped read)."""
    rows = await in_org(lambda c: RP.trades_of(c, pid))
    names = {str(v): k for k, v in S["lots"].items()}
    sell = next((r for r in rows if r["side"] == "sell" and str(r["global_security_id"]) == str(S["sec_S1"])), None)
    return {names.get(str(x["lot_id"]), str(x["lot_id"])): D(x["quantity"]) for x in jl(sell["lots"] if sell else [])}


async def sale_line(pid) -> dict | None:
    rows = await in_org(lambda c: RP.trades_of(c, pid))
    return next((r for r in rows if r["side"] == "sell" and str(r["global_security_id"]) == str(S["sec_S1"])), None)


async def proposal_row(pid) -> dict:
    return dict(await scoped(S["conn"], ORG_A, "SELECT * FROM public.rebalance_proposals WHERE id = $1 AND "
                                               "system_to IS NULL", UUID(str(pid)), fetch="fetchrow"))


async def rebalance_now() -> tuple[int, dict]:
    return await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_A}/proposals", {"trigger": "manual"})


async def change_account(body, uid=U_ADMIN1):
    return await api(uid, "POST", f"/api/v1/lot-method/accounts/{ACCT_A}/changes", {"reason": "LOTMETHOD verify", **body})


async def change_household(body, uid=U_ADMIN1):
    return await api(uid, "POST", f"/api/v1/lot-method/households/{HH_A}/changes",
                     {"reason": "LOTMETHOD verify", **body})


async def decide(cid, outcome="approve", uid=U_ADMIN2, note=None):
    return await api(uid, "POST", f"/api/v1/lot-method/changes/{cid}/{outcome}", {"note": note} if note else {})


async def set_account(body) -> bool:
    c, o = await change_account(body)
    cid = (o.get("change") or {}).get("id")
    c2, o2 = await decide(cid) if cid else (0, {})
    return check(c == 201 and c2 == 200 and (o2.get("change") or {}).get("status") == "approved",
                 f"account lot method set and approved by a second person: {body}", f"{c} {c2} {str(o2)[:160]}")


# ═══ Frozen pre-sprint engine ═══════════════════════════════════════════════
def frozen_engine():
    """services/rebalance_engine.py exactly as it was at the lotmethod base (git), as its own module."""
    if "frozen" in S:
        return S["frozen"]
    src = git_show(BEFORE_REF, "apps/api/services/rebalance_engine.py")
    mod = types.ModuleType("frozen_rebalance_engine")
    sys.modules["frozen_rebalance_engine"] = mod
    exec(compile(src, "frozen_rebalance_engine.py", "exec"), mod.__dict__)
    S["frozen"] = mod
    return mod


def lots_today(mod, spec=None):
    spec = spec or S1_LOTS
    return [mod.Lot(n, Decimal(20), D(c), ago(days)) for n, (c, days) in spec.items()]


def holding(mod, lots, key="S1", price=PRICE):
    q = sum((x.quantity for x in lots), Decimal(0))
    return mod.Holding(key, key, q, q * price, price, today(), "account_position", lots)


def plan(mod, q=SELL_QTY, *, budget=None, ytd=Decimal(0), wash=None, method=None, lots=None, avoid=True, wait=30,
         key="S1"):
    h = holding(mod, lots if lots is not None else lots_today(mod), key)
    kw = dict(today=today(), gain_budget=budget, realized_ytd=ytd, avoid_short_term=avoid, short_term_wait_days=wait,
              wash_hold=wash or {})
    if method is not None:
        kw["method"] = method
    return mod.plan_sales([(h, q)], mod.SaleSettings(**kw))


def picked(p, key="S1") -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for x in p.now.get(key, []):
        out[x.lot_id] = out.get(x.lot_id, Decimal(0)) + x.quantity
    return out


def later_of(p) -> list[tuple]:
    return sorted((x.lot_id, x.quantity, x.year, x.reason) for x in p.later)


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    callers = [ln for ln in run_cmd(["git", "grep", "-n", "plan_sales(\\|lot_order_key(", "--", "apps/api/services"],
                                    REPO, 60)[1].splitlines()]
    files = sorted({ln.split(":")[0] for ln in callers})
    check(files == ["apps/api/services/harvest.py", "apps/api/services/rebalance_engine.py"],
          "1a: plan_sales / lot_order_key are called only by the engine itself (build → every model proposal, one-off, "
          "transition) and harvest's swap-back", f"{files}")
    find("1a lots chosen without plan_sales", "harvest LOSS candidates — every qualifying loss lot sold whole (a per-lot "
         "threshold, not an order: line method 'harvest_loss'); xray._fund_analysis — a switch sells EVERY lot (the "
         "method changes no lot choice; average cost changes the character); instructions copy the proposal line's "
         "lots (execution_instruction_lots) — specific identification on every sale")
    find("1b setting", "versioned household default + account override, MAKER-CHECKER: a lot method is the same "
         "investment-policy class as rep5's gain budget / short-term guard, already approved link versions; the "
         "per-proposal override makes a NEW proposal whose own approval is its check; gates = rep5's "
         "view/manage/approve_proposals (no new permission)")
    d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                             "'portfolio.securities_global'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) "
                             "LIKE '%security_type%'", fetch="fetchval") or ""
    check("'fund'" in d and "mutual" not in d,
          "1c: the security master has no mutual-fund type and no cost-basis marker ('fund' covers ETFs and mutual "
          "funds alike) — average cost is the ACCOUNT's election, listed on its lot-method setting", d[:160])
    src5 = git_show(BEFORE_REF, "apps/api/scripts/verify_rep7.py")
    check("async def delete_tree" in src5 and "await delete_tree(op, \"public.users\"" in src5
          and "await delete_tree(conn, t, \"org_id = ANY($1::uuid[])\"" in src5,
          "1d: the slow steps were delete_tree on public.users / roles / organizations (a recursive walk over EVERY FK "
          "child — deal, SPV, agreement tables — with nested IN-subqueries) and sweep_fixture_orgs' fallback to it "
          "on an fk_order cycle")
    real = await platform(conn, """SELECT r.id, array_agg(p.name) AS perms FROM roles r JOIN organizations o
                                   ON o.id = r.org_id AND NOT o.is_test LEFT JOIN role_permissions rp ON rp.role_id = r.id
                                   LEFT JOIN permissions p ON p.id = rp.permission_id WHERE r.name = 'org_admin'
                                   GROUP BY r.id""")
    sets = [set(x for x in r["perms"] if x) for r in real]
    every = set.intersection(*sets) if sets else set()
    any_ = set.union(*sets) if sets else set()
    granted = {n for n, _, _ in rbac.ORG_ADMIN_DEFAULT_PERMISSIONS} | {rbac.ORG_ADMIN_PERMISSION}
    S["b3_every"] = every
    check(bool(sets) and every <= granted, f"1e: every permission ALL {len(sets)} real org_admin roles hold is in "
          "ensure_org_admin_role's grant (rep2 → rep7b already extended it; B3's premise was stale)",
          f"missing {sorted(every - granted)}")
    find("1e outlier", f"held by SOME real org_admin only, not granted to a new org: {sorted(any_ - granted)} "
         "(review_agent_proposals: one org's grant, by no migration — adding it would widen a new org's admin "
         "beyond what another real org's admin holds)")


async def y_schema(conn):
    section("[Y] Schema landed (method settings, custodian method, proposal method, B6 deal id), RLS on every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_lotmethod.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_lotmethod.py --verify: SCHEMA OK", f"{gaps[:4]} "
          f"{(out[-300:] + err[-200:]) if code else ''}")
    pols = await platform(conn, "SELECT tablename, array_agg(DISTINCT cmd) AS c FROM pg_policies WHERE schemaname = "
                                "'public' AND tablename = ANY($1::text[]) GROUP BY tablename",
                          [t.split(".")[1] for t in NEW_TABLES])
    have = {r["tablename"]: set(r["c"]) for r in pols}
    check(len(have) == 2 and all({"SELECT", "INSERT", "UPDATE", "DELETE"} <= v for v in have.values()),
          "both new tables: a policy for SELECT, INSERT, UPDATE and DELETE", f"{have}")
    cols = {(r["table_name"], r["column_name"]) for r in await platform(
        conn, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' AND "
              "table_name = ANY($1::text[])", ["rebalance_proposals", "rebalance_proposal_trades", "deals",
                                               "account_lot_method_settings"])}
    want = {("rebalance_proposals", c) for c in ("lot_method", "lot_method_source", "gain_preference",
                                                 "realize_gains_target", "lot_picks", "custodian_cost_basis_method")}
    want |= {("rebalance_proposal_trades", "lot_method"), ("deals", "version_of_deal_id"),
             ("account_lot_method_settings", "custodian_cost_basis_method"),
             ("account_lot_method_settings", "average_cost_security_ids")}
    check(want <= cols, "proposal method columns, the sale line's method, the custodian method, average-cost "
          "securities and deals.version_of_deal_id exist", f"{sorted(want - cols)}")
    pre = await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE lot_method IS NOT NULL AND "
                               "NOT (org_id = ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval")
    check(int(pre) == 0, "the migration wrote no method onto any existing proposal (they used today's rule)", f"{pre}")
    # every literal this script writes into a CHECK, against the live catalog
    lits = (("public.household_lot_method", "household_lot_method_method_chk", ("fifo", "tax_optimized")),
            ("public.household_lot_method", "household_lot_method_pref_chk", ("prefer_long_term",)),
            ("public.account_lot_method_settings", "account_lot_method_settings_method_chk",
             ("tax_optimized", "fifo", "lifo", "lowest_cost", "hifo", "specific")),
            ("public.account_lot_method_settings", "account_lot_method_settings_custodian_chk",
             ("unknown", "fifo", "specific")),
            ("public.account_lot_method_settings", "account_lot_method_settings_source_chk", ("custodian",)),
            ("public.rebalance_proposals", "rebalance_proposals_lot_method_chk", ("default", "account", "proposal")),
            ("public.rebalance_proposal_trades", "rebalance_proposal_trades_lot_method_chk", ("harvest_loss",)),
            ("portfolio.tax_lots", "tax_lots_term_check", ("long", "short")),
            ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
            ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund")),
            ("portfolio.models", "models_source_chk", ("upload",)))
    bad = []
    for table, con, vals in lits:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'::text", d or ""))
        if d is None or [v for v in vals if v not in allowed]:
            bad.append(f"{table}.{con}: {'absent' if d is None else [v for v in vals if v not in allowed]}")
    check(not bad, f"every seed / test literal admitted by its CHECK ({len(lits)} constraints read from the catalog)",
          "; ".join(bad))
    uq = await platform(conn, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY($1) "
                              "AND indexdef LIKE 'CREATE UNIQUE%'", ["household_lot_method",
                                                                    "account_lot_method_settings"])
    check(len(uq) >= 8, "UNIQUE indexes read from the catalog before seeding (pkeys + live / pending / task partials)",
          f"{sorted(r['indexname'] for r in uq)}")


def y_engine_default():
    section("[Y] Default method = today's rule, exactly (against the FROZEN pre-sprint engine from git)")
    old = frozen_engine()
    check(hasattr(old, "lot_order_key") and not hasattr(old, "LotMethod"), "the frozen engine is the pre-sprint one "
          f"(git {BEFORE_REF})")
    diffs = []
    specs = [S1_LOTS, {"A": ("120", 40), "B": ("95", 380), "C": (None, 700), "D": ("60", 10), "E": ("100", 1200)}]
    for spec in specs:
        for budget in (None, Decimal(0), Decimal("150"), Decimal("900")):
            for q in (Decimal(5), Decimal(25), Decimal(60), Decimal(100)):
                for wash in ({}, {"S1": {"until": today() + timedelta(days=12), "reason": "buy", "matches": []}}):
                    def lots_for(mod):
                        return [mod.Lot(n, Decimal(20), None if c is None else D(c), ago(days))
                                for n, (c, days) in spec.items()]
                    a = plan(old, q, budget=budget, wash=wash, lots=lots_for(old))
                    b = plan(E, q, budget=budget, wash=wash, lots=lots_for(E))
                    sa = ([(x.lot_id, x.quantity, x.gain) for x in a.now.get("S1", [])], later_of(a),
                          [(x["lot_id"], x["reason"]) for x in a.blocked], a.net_gain_now)
                    sb = ([(x.lot_id, x.quantity, x.gain) for x in b.now.get("S1", [])], later_of(b),
                          [(x["lot_id"], x["reason"]) for x in b.blocked], b.net_gain_now)
                    if sa != sb:
                        diffs.append((list(spec)[0], str(budget), str(q), bool(wash)))
    check(not diffs, "default plan == the frozen engine's plan on 64 combinations (lots, budgets, quantities, "
          "wash holds): same lots now, same 'sell later', same blocks, same net gain", f"{diffs[:4]}")
    oldkey = [x.id for x in sorted(lots_today(old), key=lambda x: old.lot_order_key(x, PRICE, today()))]
    newkey = [x.id for x in sorted(lots_today(E), key=lambda x: E.lot_order_key(x, PRICE, today()))]
    check(oldkey == newkey and len(oldkey) == 5, "lot_order_key itself is unchanged", f"{oldkey} {newkey}")
    p = plan(E)
    check(p.method_of.get("S1", (None, ""))[1].startswith("no lot method set") and set(picked(p)) ==
          EXPECTED_DB["default"], "the default plan states 'no lot method set — today's rule' and picks LTL, STL, STG",
          f"{p.method_of} {picked(p)}")


def y_engine_methods():
    section("[Y] Each method's lots exact; tax_optimized by estimated tax per dollar; no profile → today's rule, stated")
    rates = {"st": Decimal("0.51526"), "lt": Decimal("0.3365"), "profile_id": "p", "tax_year": today().year,
             "as_of": today().isoformat()}
    for meth in ("fifo", "lifo", "lowest_cost", "hifo", "highest_cost"):
        p = plan(E, method=E.LotMethod(meth, source="account"))
        want = EXPECTED_DB["hifo" if meth == "highest_cost" else meth]
        got = picked(p)
        check(set(got) == want and all(v == 20 for v in got.values()) and p.method_of["S1"][0] == meth,
              f"{meth}: exactly {sorted(want)} (20 each)", f"{got}")
    p = plan(E, method=E.LotMethod("tax_optimized", rates=rates, source="account"))
    check(set(picked(p)) == EXPECTED_DB["tax_optimized"], "tax_optimized with a profile: STL, LTL, LTG2 (differs from "
          "today's rule, which takes STG)", f"{picked(p)}")
    with localcontext() as ctx:
        ctx.prec = 60
        exp = {"STL": (PRICE - 110) / PRICE * rates["st"], "LTL": (PRICE - 112) / PRICE * rates["lt"],
               "STG": (PRICE - 90) / PRICE * rates["st"], "LTG2": (PRICE - 85) / PRICE * rates["lt"],
               "LTG": (PRICE - 50) / PRICE * rates["lt"]}
    got = {x.id: E.tax_per_dollar(x, PRICE, today(), rates) for x in lots_today(E)}
    check(got == exp, "estimated tax per dollar raised = (price − cost) ÷ price × the combined rate, exactly",
          f"{ {k: str(v) for k, v in got.items()} }")
    order = [x.id for x in sorted(lots_today(E), key=lambda x: E.method_key(E.LotMethod("tax_optimized", rates=rates),
                                                                            x, PRICE, today()))]
    check(order.index("STL") < order.index("LTL") and order.index("LTG2") < order.index("STG")
          and exp["STL"] < exp["LTL"] and exp["LTG2"] < exp["STG"] and order == ["STL", "LTL", "LTG2", "STG", "LTG"],
          "a short-term loss ranks ahead of a long-term loss, a long-term gain ahead of a short-term gain "
          "(today's rule ranks LTL first and STG ahead of LTG2)", f"{order}")
    why = {x.lot_id: x.why for x in p.now["S1"]}
    check(all("est. tax per $ raised" in w and "profile rates" in w for w in why.values()) and len(why) == 3,
          "each tax-optimized lot states its estimated tax per dollar and the rate used", f"{why}")
    p0 = plan(E, method=E.LotMethod("tax_optimized", rates=None, source="account"))
    old = plan(frozen_engine())
    check(picked(p0) == picked(old) and "no tax profile" in p0.method_of["S1"][1] and "today's rule" in
          p0.method_of["S1"][1], "tax_optimized with NO profile → today's rule exactly, and it says so",
          f"{picked(p0)} {p0.method_of}")
    fifo = plan(E, method=E.LotMethod("fifo", source="account"))
    check(all("oldest first" in x.why for x in fifo.now["S1"]), "every sale lot states why it was chosen "
          "(fifo: 'oldest first (acquired …)')", f"{[x.why for x in fifo.now['S1']]}")


def y_engine_precedence():
    section("[Y] Precedence: wash-sale holds / blocks and guards beat every method; specific validated; "
            "realize_gains target; average cost")
    until = today() + timedelta(days=9)
    wash = {"S1": {"until": until, "reason": "household buy", "matches": []}}
    bad = []
    for meth in E.METHODS:
        if meth == "specific":
            continue
        p = plan(E, wash=wash, method=E.LotMethod(meth, source="account"))
        if {"STL", "LTL"} & set(picked(p)) or not {h["lot"].id for h in p.held} <= {"STL", "LTL"}:
            bad.append(meth)
    check(not bad, "a wash-sale hold beats every method: no method sells a held loss lot now", f"{bad}")
    unknown = {**S1_LOTS, "UNK": (None, 400)}

    def with_unknown(mod):
        return [mod.Lot(n, Decimal(20), None if c is None else D(c), ago(days)) for n, (c, days) in unknown.items()]
    bad = [m for m in ("lowest_cost", "fifo", "lifo", "hifo") if "UNK" in picked(
        plan(E, Decimal(120), budget=Decimal(100000), lots=with_unknown(E), method=E.LotMethod(m, source="account")))]
    check(not bad, "the hard block beats every method: an unknown-cost lot is never sold against a gain budget",
          f"{bad}")
    near = {"NEAR": ("60", 350), "OLD": ("70", 900)}     # NEAR turns long-term in ~16 days
    lifo = plan(E, Decimal(20), lots=[E.Lot(n, Decimal(20), D(c), ago(d)) for n, (c, d) in near.items()],
                method=E.LotMethod("lifo", source="account"))
    check("NEAR" not in picked(lifo) and picked(lifo).get("OLD") == 20,
          "the short-term guard beats lifo: the newest lot (a short-term gain turning long-term within 30 days) is "
          "deferred; the method sells the next lot", f"{picked(lifo)} {later_of(lifo)}")
    budget = plan(E, budget=Decimal("200"), method=E.LotMethod("lowest_cost", source="account"))
    check(budget.net_gain_now <= Decimal("200") and any(x.reason == "over this year's gain budget" for x in budget.later),
          "the gain budget beats lowest_cost: net gain now within the budget, the rest 'sell later'",
          f"net {budget.net_gain_now} later {later_of(budget)[:3]}")
    for picks, why in (([("ZZ", Decimal(5))], "not an open lot"), ([("LTG", Decimal(25))], "fewer than"),
                       ([("STL", Decimal(5))], "wash-sale"), ([("LTG", Decimal(5)), ("LTG", Decimal(1))], "twice")):
        try:
            plan(E, wash=wash if why == "wash-sale" else None,
                 method=E.LotMethod("specific", picks={"S1": picks}, source="proposal"))
            check(False, f"specific: a pick refused ({why})", "no refusal")
        except E.LotPickError as exc:
            check(why in str(exc), f"specific: a pick refused — {why}", str(exc)[:120])
    try:
        plan(E, Decimal(60), budget=Decimal(10000), lots=with_unknown(E),
             method=E.LotMethod("specific", picks={"S1": [("UNK", Decimal(5))]}, source="proposal"))
        check(False, "specific: an unknown-cost pick under a budget refused", "no refusal")
    except E.LotPickError as exc:
        check("no cost basis" in str(exc), "specific: an unknown-cost pick under a gain budget refused", str(exc)[:100])
    sp = plan(E, method=E.LotMethod("specific", picks={"S1": [("LTG", Decimal(10))]}, source="proposal"))
    got = picked(sp)
    whys = {x.lot_id: x.why for x in sp.now["S1"]}
    check(got.get("LTG") == 10 and sum(got.values()) == SELL_QTY and whys["LTG"] == "picked by the adviser"
          and all("not picked" in w for k, w in whys.items() if k != "LTG"),
          "specific: the picked lot used as given; the rest filled by today's rule, each stated", f"{got} {whys}")
    rg = plan(E, method=E.LotMethod("lifo", gain_preference="realize_gains", realize_target=Decimal(600),
                                    source="account"))
    tgt = {x.lot_id: x for x in rg.now["S1"] if "target" in x.why}
    realized = sum((x.gain for x in tgt.values()), Decimal(0))
    check({k: x.quantity for k, x in tgt.items()} == {"LTG2": 20, "LTG": 6} and realized == Decimal(600)
          and sum(picked(rg).values()) == SELL_QTY and picked(rg).get("STL") == 20,
          "realize_gains (target 600, lifo): long-term gains first — LTG2 20 × 15 = 300, then LTG only 6 × 50 = 300 "
          "— stopping exactly at the target, stated; the rest of the sale by lifo",
          f"{[(x.lot_id, x.quantity, x.why) for x in rg.now['S1']]}")
    av = plan(E, Decimal(30), method=E.LotMethod(average_cost=frozenset({"S1"}), source="account"))
    avg = sum((D(c) * 20 for c, _ in S1_LOTS.values()), Decimal(0)) / 100
    rows = av.now["S1"]
    check(all(x.lot_id is None and x.average_cost and x.unit_cost == avg for x in rows) and sum(x.quantity for x in rows)
          == 30 and av.method_of["S1"][0] == "average_cost" and "no lot choice" in av.method_of["S1"][1],
          f"average cost: no lot choice — every share at the average cost {avg}, no lot named, stated",
          f"{[(x.lot_id, x.quantity, x.unit_cost) for x in rows]}")
    first = [x.term for x in rows]
    check(first and first[0] == "long", "average cost: holding periods first in, first out (the oldest shares — "
          "long-term — go first)", f"{first}")


def y_harvest_xray_pure():
    section("[Y] Harvest and X-ray follow the method (source + pure)")
    src = inspect.getsource(HV.scan_account)
    check("lot_method_for" in src and "method=method" in src and '"harvest_loss"' in src,
          "harvest: the swap-back sale goes through plan_sales WITH the account's method; a loss line says "
          "'harvest_loss' (each qualifying loss lot, whole)")
    swap = [E.Lot("R1", Decimal(10), D("90"), ago(400)), E.Lot("R2", Decimal(10), D("60"), ago(500))]
    a = plan(E, Decimal(10), lots=swap, key="S1", method=E.LotMethod("lowest_cost", source="account"))
    b = plan(E, Decimal(10), lots=swap, key="S1")
    check(set(picked(a)) == {"R2"} and set(picked(b)) == {"R1"}, "a swap-back of half the replacement: lowest_cost "
          "sells R2, the default (today's rule) R1", f"{picked(a)} {picked(b)}")
    lots = [{"quantity": "10", "adjusted_cost": "100", "original_cost": "100", "term": "long",
             "acquired_date": ago(900).isoformat()},
            {"quantity": "10", "adjusted_cost": "900", "original_cost": "900", "term": "short",
             "acquired_date": ago(30).isoformat()}]
    k1, st1, lt1, _ = XR.embedded_by_character(lots, Decimal(100), today(), average=False)
    k2, st2, lt2, avg = XR.embedded_by_character(lots, Decimal(100), today(), average=True)
    check(k1 and k2 and (st1, lt1) == (Decimal(100), Decimal(900)) and avg == Decimal(50)
          and (st2, lt2) == (Decimal(500), Decimal(500)) and st1 + lt1 == st2 + lt2,
          "X-ray: a full switch, lot by lot ST 100 / LT 900; at AVERAGE cost (50) ST 500 / LT 500 — same total, a "
          "different character (so a different transition tax)", f"{st1}/{lt1} vs {st2}/{lt2} avg {avg}")
    check("LM.effective" in inspect.getsource(XR._fund_analysis) and "embedded_by_character" in
          inspect.getsource(XR._fund_analysis), "X-ray: _fund_analysis reads the account's method / average-cost list")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_C, "c")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"LOTMETHOD Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid_view = await rbac.ensure_role(conn, ORG_A, "lotmethod_viewer", "LOTMETHOD verify — view_proposals")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p])
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid_view)
        for ent, org in ((ENT_A, ORG_A), (ENT_B, ORG_B)):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"LOTMETHOD Verify {str(org)[-4:]}")
        await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                           HH_A, ORG_A, HH_NAME, U_ADMIN1)
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES.get(key, "equity"),
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, hh, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, true, false)",
                aid, org, n.masked, n.hashed(salts[org]), code, ENT_A if org == ORG_A else ENT_B, hh)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        S["lots"] = {}
        for key in ("S1", "S2"):
            asset = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", asset, ORG_A, S[f"sec_{key}"], SEC_NAMES[key])
            qty = Decimal(100) if key == "S1" else Decimal(10)
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, ORG_A, ENT_A, asset, ago(1), qty, qty * PRICE, ACCT_A)
            spec = S1_LOTS if key == "S1" else {"S2A": ("40", 900), "S2B": ("160", 30)}
            n_lot = Decimal(20) if key == "S1" else Decimal(5)
            for name, (cost, days) in spec.items():
                lid = uuid.uuid4()
                acquired = ago(days)
                term = "long" if today() >= E.long_term_from(acquired) else "short"
                await conn.execute(
                    "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                    "original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, 'open')",
                    lid, ORG_A, pid, acquired, n_lot, D(cost) * n_lot, term)
                S["lots"][name] = lid
        await conn.execute(
            "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, cash_value, "
            "margin_balance, accrued_income, source_system, source_confidence, is_billing_source, is_final) "
            "VALUES ($1, $2, $3, 0, 0, 0, 0, 'manual', 'CONFIRMED', true, true)", ORG_A, ACCT_A, ago(1))
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(len(orgs) == 3 and all(r["is_test"] is True for r in orgs), "every fixture org is is_test = true (the live "
          "scheduler skips them)")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test AND auth0_sub LIKE $2",
                        list(USERS), f"%{TAG}%", fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly with the token's auth0_sub, is_test = true")
    # the model: S1 40 %, S2 at its current weight, the rest cash → the rebalance sells exactly 60 S1
    # account value = 100 × 100 + 10 × 100 = 11,000; S1 target 40/110 of it = 4,000 → sell 60
    w1 = (Decimal(4000) / Decimal(11000)).quantize(E.Q)
    w2 = (Decimal(1000) / Decimal(11000)).quantize(E.Q)
    S["model"], S["model_v"] = await make_model("LOTMETHOD VERIFY MODEL", [pos(1, "S1", w1), pos(2, "S2", w2),
                                                                         pos(3, "cash", Decimal(1) - w1 - w2)])
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{ACCT_A}/model/join", {"model_id": str(S["model"]),
                                                                                  "settings": {}})
    lid = (o.get("link") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{ACCT_A}/model/changes/{lid}/approve", {})
    S["join_proposal"] = o2.get("transition_proposal_id")
    check(c == 201 and c2 == 200 and bool(S["join_proposal"]), "the account joins the model (rep5's path; a second "
          "person approves) and the transition proposal is made", f"{c} {c2} {str(o2)[:160]}")


async def y_db_default(conn):
    section("[Y] Default (no method set): the live proposal's lots are today's rule; recorded as source 'default'")
    pid = S["join_proposal"]
    got = await lots_of(pid)
    p = await proposal_row(pid)
    line = await sale_line(pid)
    check(set(got) == EXPECTED_DB["default"] and all(v == 20 for v in got.values()),
          "the join proposal sells 60 S1 from exactly LTL, STL, STG (20 each) — the frozen engine's choice", f"{got}")
    check(p["lot_method"] == "tax_optimized" and p["lot_method_source"] == "default" and p["lot_picks"] is None
          and line is not None and line["lot_method"] == "tax_optimized",
          "the proposal records method tax_optimized, source 'default' (= today's rule); the sale line its method",
          f"{p['lot_method']} {p['lot_method_source']}")
    w = [x.get("code") for x in jl(p["warnings"])]
    check("lot_method" not in w and "lot_method_mismatch" not in w, "no method set → no lot-method warning at all "
          "(no existing proposal's warnings change)", f"{w}")
    whys = [x.get("why") for x in jl(line["lots"])] if line else []
    check(len(whys) == 3 and all(whys), "each lot on the line states why it was chosen", f"{whys}")
    old = frozen_engine()
    hold = await in_org(lambda c: AM.load_holdings(c, ORG_A, ACCT_A))
    h = next(x for x in hold["holdings"] if x.key == str(S["sec_S1"]))
    fl = [old.Lot(x.id, x.quantity, x.unit_cost, x.acquired, x.term_hint) for x in h.lots]
    fh = old.Holding(h.key, h.label, h.quantity, h.market_value, h.price, h.price_as_of, h.price_source, fl)
    fp = old.plan_sales([(fh, SELL_QTY)], old.SaleSettings(today=today(), gain_budget=None))
    names = {str(v): k for k, v in S["lots"].items()}
    check({names[str(x.lot_id)]: x.quantity for x in fp.now[h.key]} == got,
          "on the live holdings, the frozen pre-sprint plan_sales picks the same lots and quantities")


async def y_overrides(conn):
    section("[Y] Overrides: household default; account beats household; per-proposal override recorded; "
            "custodian-mismatch warning")
    # the tax profile (rep6's path: an adviser enters it, a second person approves) — tax_optimized needs it
    body = {"subject_type": "household", "subject_id": str(HH_A), "tax_year": today().year,
            "filing_status": "married_filing_jointly", **HH_RATES, "as_of": ago(2).isoformat(),
            "source_note": "LOTMETHOD verify — planning memo"}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", body)
    pid_ = (o.get("profile") or {}).get("id")
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/profiles/{pid_}/approve", {})
    check(c == 201 and c2 == 200, "the household's tax profile entered and approved (rep6)", f"{c} {c2} {str(o)[:120]}")
    c, o = await change_household({"lot_method": "fifo", "gain_preference": "prefer_long_term"})
    cid = (o.get("change") or {}).get("id")
    cm, om = await decide(cid, uid=U_ADMIN1)
    cv, ov = await decide(cid, uid=U_VIEWER)
    c2, o2 = await decide(cid)
    check(c == 201 and cm == 403 and cv == 403 and "approve_proposals" in str(ov) and c2 == 200,
          "household default fifo: the maker is refused (403), a viewer refused naming approve_proposals, a second "
          "person approves", f"{c} {cm} {cv} {c2}")
    c, o = await rebalance_now()
    pid = (o.get("proposal") or o).get("id")
    got = await lots_of(pid)
    p = await proposal_row(pid)
    check(set(got) == EXPECTED_DB["fifo"] and p["lot_method"] == "fifo" and p["lot_method_source"] == "household",
          "with the household default fifo: LTL, LTG, LTG2; source 'household'", f"{c} {got} {p['lot_method_source']}")
    await set_account({"lot_method_override": "tax_optimized", "custodian_cost_basis_method": "fifo",
                       "custodian_method_source": "custodian"})
    c, o = await rebalance_now()
    pid = (o.get("proposal") or o).get("id")
    got = await lots_of(pid)
    p = await proposal_row(pid)
    w = {x.get("code"): x for x in jl(p["warnings"])}
    check(set(got) == EXPECTED_DB["tax_optimized"] and p["lot_method_source"] == "account",
          "the account override (tax_optimized) beats the household's fifo: STL, LTL, LTG2 at the profile's rates",
          f"{got} {p['lot_method_source']}")
    check("lot_method_mismatch" in w and p["custodian_cost_basis_method"] == "fifo",
          "custodian fifo ≠ tax_optimized → the proposal carries the mismatch warning (name the lots)",
          f"{list(w)}")
    S["mismatch_pid"] = pid
    # per-proposal override: lifo on this draft → a NEW proposal, the old one superseded
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/lot-method/proposals/{pid}/override", {"lot_method": "lifo"})
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/lot-method/proposals/{pid}/override", {"lot_method": "lifo"})
    new = (o.get("proposal") or {}).get("id")
    old_row = await proposal_row(pid)
    got = await lots_of(new) if new else {}
    np_ = await proposal_row(new) if new else {}
    check(cv == 403 and "manage_proposals" in str(ov) and c == 201 and old_row["status"] == "superseded"
          and set(got) == EXPECTED_DB["lifo"] and np_.get("lot_method") == "lifo"
          and np_.get("lot_method_source") == "proposal" and jl(np_.get("trigger_ref")).get("lot_method_override_of")
          == str(pid), "per-proposal override (lifo): a viewer refused naming manage_proposals; the admin's makes a "
          "NEW proposal (STL, STG, LTG2; source 'proposal', naming what it overrides); the old one superseded",
          f"{cv} {c} {old_row['status']} {got} {np_.get('lot_method_source')}")
    # specific: the lot picker, then a validated pick
    c, o = await api(U_ADMIN1, "GET", f"/api/v1/lot-method/proposals/{new}/lots")
    lines = o.get("lines") or []
    s1 = next((x for x in lines if x["global_security_id"] == str(S["sec_S1"])), {})
    check(c == 200 and len(s1.get("lots") or []) == 5 and "override_lot_method" in (o.get("actions") or [])
          and "lots" in o["vocabularies"]["editable"], "the lot picker lists the 5 open S1 lots; override allowed",
          f"{c} {len(s1.get('lots') or [])}")
    cv, ov = await api(U_VIEWER, "GET", f"/api/v1/lot-method/proposals/{new}/lots")
    check(cv == 200 and ov.get("actions") == [] and ov["vocabularies"]["editable"] == [],
          "a view-only caller: the picker data, no action, an EMPTY editable list", f"{cv} {ov.get('actions')}")
    bad_pick = {"lot_method": "specific", "lots": [{"global_security_id": str(S["sec_S1"]),
                                                    "lot_id": str(S["lots"]["LTG"]), "quantity": "25"}]}
    cb, ob = await api(U_ADMIN1, "POST", f"/api/v1/lot-method/proposals/{new}/override", bad_pick)
    still = await proposal_row(new)
    check(cb == 422 and "fewer than" in str(ob) and still["status"] in ("draft", "pending_approval"),
          "specific: a pick larger than its lot refused (422); the proposal unchanged", f"{cb} {str(ob)[:140]}")
    cf, of_ = await api(U_ADMIN1, "POST", f"/api/v1/lot-method/proposals/{new}/override",
                        {"lot_method": "specific", "lots": [{"global_security_id": str(S["sec_S1"]),
                                                             "lot_id": str(S["lots"]["LTG"]), "quantity": 5.0}]})
    check(cf == 422, "a JSON float quantity is refused (422)", f"{cf}")
    good = {"lot_method": "specific", "lots": [{"global_security_id": str(S["sec_S1"]), "lot_id": str(S["lots"]["LTG"]),
                                                "quantity": "10"}]}
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/lot-method/proposals/{new}/override", good)
    sp = (o.get("proposal") or {}).get("id")
    got = await lots_of(sp) if sp else {}
    row = await proposal_row(sp) if sp else {}
    check(c == 201 and got.get("LTG") == 10 and sum(got.values()) == SELL_QTY and row.get("lot_method") == "specific"
          and jl(row.get("lot_picks")) and jl(row["lot_picks"])[0]["lot_id"] == str(S["lots"]["LTG"]),
          "specific: the picked lot (LTG 10) used as given, the rest filled; the picks recorded on the proposal",
          f"{c} {got} {str(row.get('lot_picks'))[:100]}")
    S["final_pid"] = sp


async def y_transition_instruction(conn):
    section("[Y] Transition follows the method; the instruction carries the custodian-mismatch note")
    c, o = await api(U_ADMIN1, "GET", f"/api/v1/accounts/{ACCT_A}/model/transition")
    sm = next((x for x in (o.get("sale_methods") or []) if x["key"] == str(S["sec_S1"])), {})
    names = {str(v): k for k, v in S["lots"].items()}
    got = {names.get(x["lot_id"]) for x in sm.get("lots") or []}
    check(c == 200 and sm.get("lot_method") == "tax_optimized" and got == EXPECTED_DB["tax_optimized"]
          and (o.get("lot_method") or {}).get("lot_method_source") == "account",
          "transition preview: the account's tax_optimized method picks STL, LTL, LTG2", f"{c} {sm.get('lot_method')} "
          f"{got}")
    # route the final (specific) proposal: submit → approve (second person) → instructions
    pid = S["final_pid"]
    c1, _ = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/submit", {})
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {})
    ins = await scoped(conn, ORG_A, "SELECT notes, side FROM public.execution_instructions WHERE source_ref = $1 "
                                    "AND system_to IS NULL", UUID(str(pid)))
    lots_n = await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instruction_lots l JOIN "
                                       "public.execution_instructions i ON i.id = l.instruction_id WHERE "
                                       "i.source_ref = $1", UUID(str(pid)), fetch="fetchval")
    check(c1 == 200 and c2 == 200 and len(ins) >= 1 and all("LOT METHOD" in (r["notes"] or "") for r in ins)
          and int(lots_n) >= 1, "approved by a second person → instructions; every one names its lots and carries "
          "the note that the custodian's default (FIFO) differs", f"{c1} {c2} {len(ins)} lots={lots_n} "
          f"{[(r['notes'] or '')[-120:] for r in ins][:1]}")
    # no mismatch when the custodian's method is specific identification
    await set_account({"lot_method_override": "tax_optimized", "custodian_cost_basis_method": "specific",
                       "custodian_method_source": "adviser"})
    eff = await in_org(lambda c: LM.effective(c, ORG_A, {"id": ACCT_A, "household_id": HH_A}))
    check(LM.mismatch(eff) is None and eff["custodian_cost_basis_method"] == "specific",
          "a newer approved account setting replaces the old one; custodian specific identification → no mismatch")
    hist = await scoped(conn, ORG_A, "SELECT status, valid_to FROM public.account_lot_method_settings WHERE "
                                     "account_id = $1 AND system_to IS NULL ORDER BY created_at", ACCT_A)
    check(len(hist) == 2 and hist[0]["valid_to"] is not None and hist[1]["valid_to"] is None,
          "the account setting's history: the earlier approved row closed (valid_to), never deleted",
          f"{[(r['status'], r['valid_to'] is not None) for r in hist]}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service, rolbypassrls false",
          f"{dict(who)}")
    a_rows = await scoped(conn, ORG_A, "SELECT org_id FROM public.account_lot_method_settings")
    b_rows = await scoped(conn, ORG_B, "SELECT org_id FROM public.account_lot_method_settings WHERE account_id = $1",
                          ACCT_A)
    hb = await scoped(conn, ORG_B, "SELECT count(*) FROM public.household_lot_method WHERE household_id = $1", HH_A,
                      fetch="fetchval")
    check(len(a_rows) >= 2 and all(r["org_id"] == ORG_A for r in a_rows) and b_rows == [] and int(hb) == 0,
          "org A sees its own settings rows (non-empty, all org A); org B sees none of them", f"{len(a_rows)} "
          f"{len(b_rows)} {hb}")
    try:
        await scoped(conn, ORG_B, "INSERT INTO public.household_lot_method (org_id, household_id, lot_method, reason, "
                                  "maker_id, status, approval_task_id) VALUES ($1, $2, 'fifo', 'x', $3, 'draft', NULL)",
                     ORG_A, HH_A, U_ADMIN_B, fetch="execute")
        check(False, "org B cannot write a row into org A", "insert accepted")
    except (asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError) as exc:
        check(True, "org B cannot write a row into org A (RLS / CHECK refuse it)", type(exc).__name__)
    cb, ob = await api(U_ADMIN_B, "GET", f"/api/v1/lot-method/accounts/{ACCT_A}")
    cbh, _ = await api(U_ADMIN_B, "GET", f"/api/v1/lot-method/households/{HH_A}")
    co, _ = await api(U_ADMIN_B, "POST", f"/api/v1/lot-method/accounts/{ACCT_A}/changes",
                      {"lot_method_override": "fifo", "reason": "x"})
    check(cb == 404 and cbh == 404 and co == 404, "org B's admin: org A's account / household are 404, and a change "
          "request for them is refused (404)", f"{cb} {cbh} {co}")
    cbb, obb = await api(U_ADMIN_B, "GET", f"/api/v1/lot-method/accounts/{ACCT_B}")
    ca, _ = await api(U_ADMIN1, "GET", f"/api/v1/lot-method/accounts/{ACCT_B}")
    check(cbb == 200 and (obb.get("effective") or {}).get("lot_method_source") == "default" and ca == 404,
          "the other direction: org B reads its own account (no method set); org A's admin gets 404 for it",
          f"{cbb} {ca}")
    cv, ov = await api(U_VIEWER, "GET", f"/api/v1/lot-method/accounts/{ACCT_A}")
    cw, ow = await api(U_VIEWER, "POST", f"/api/v1/lot-method/accounts/{ACCT_A}/changes",
                       {"lot_method_override": "fifo", "reason": "x"})
    check(cv == 200 and ov["permissions"]["can_write"] is False and ov["vocabularies"]["editable"] == []
          and ov.get("actions") == [] and cw == 403 and "manage_proposals" in str(ow),
          "a view-only caller reads (empty editable, no action) and is refused a write naming manage_proposals; "
          "the admin's identical request was accepted above", f"{cv} {cw}")
    cn, on = await api(U_ADMIN1, "POST", f"/api/v1/lot-method/households/{HH_A}/changes",
                       {"lot_method": "fifo", "org_id": str(ORG_B), "reason": "x"})
    cf, _ = await api(U_ADMIN1, "POST", f"/api/v1/lot-method/households/{HH_A}/changes",
                      {"lot_method": "fifo", "gain_preference": "realize_gains", "realize_gains_target": 600.0,
                       "reason": "x"})
    check(cn == 422 and cf == 422, "an org_id in a body is refused (extra='forbid'); a JSON float target is refused",
          f"{cn} {cf}")


async def y_b3(conn):
    section("[Y] B3: a new org's first org_admin holds every permission existing org_admins hold")
    pool = await get_pool()
    with _ctx(ORG_C, True):
        async with pool.acquire() as c2:
            async with c2.transaction():
                await rbac.grant_org_admin(c2, U_NEWADMIN, ORG_C)
    held = {r["name"] for r in await platform(conn, """SELECT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                                                     JOIN role_permissions rp ON rp.role_id = r.id
                                                     JOIN permissions p ON p.id = rp.permission_id
                                                     WHERE ur.user_id = $1 AND r.org_id = $2""", U_NEWADMIN, ORG_C)}
    every = S.get("b3_every") or set()
    check(bool(every) and every <= held and {"view_replication", "manage_replication", "approve_replication",
                                             "view_tax", "view_held_away", "view_authority"} <= held,
          f"the new org's admin holds all {len(every)} permissions every real org_admin holds (replication, tax, "
          "held-away, authority included)", f"missing {sorted(every - held)}")


async def y_b6(conn):
    section("[Y] B6: un-archive restores each deal to ITS prior stage (two deals, one transaction, one creator)")
    pool = await get_pool()
    ids = []
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            async with c2.transaction():
                for nm in DEAL_NAMES:
                    ids.append(await c2.fetchval("INSERT INTO deals (org_id, name, created_by) VALUES ($1, $2, $3) "
                                                 "RETURNING id", ORG_A, nm, U_ADMIN1))
            d1, d2 = ids
            async with c2.transaction():
                await DLC.apply_status(c2, ORG_A, d1, "submitted", actor=U_ADMIN1, confirm=True)
            async with c2.transaction():
                await DLC.apply_status(c2, ORG_A, d1, "archived", actor=U_ADMIN1, confirm=True)
            async with c2.transaction():
                await DLC.apply_status(c2, ORG_A, d2, "archived", actor=U_ADMIN1, confirm=True)
            same = await c2.fetchval("SELECT count(DISTINCT (created_at, created_by)) FROM deals WHERE id = ANY($1)", ids)
            # reproduce the bug first: the pre-B6 prior_status, frozen from git, on the same rows
            src = git_show(BEFORE_REF, "apps/api/services/deal_lifecycle.py")
            ns: dict = {}
            exec(compile(src, "frozen_deal_lifecycle.py", "exec"), ns)
            old1 = await ns["prior_status"](c2, ORG_A, d1)
            new1 = await DLC.prior_status(c2, ORG_A, d1)
            new2 = await DLC.prior_status(c2, ORG_A, d2)
            async with c2.transaction():
                r1 = await DLC.apply_status(c2, ORG_A, d1, "archived", actor=U_ADMIN1, unarchive=True)
            async with c2.transaction():
                r2 = await DLC.apply_status(c2, ORG_A, d2, "archived", actor=U_ADMIN1, unarchive=True)
            tagged = await c2.fetchval("SELECT count(*) FROM deals WHERE version_of_deal_id = ANY($1) AND "
                                       "system_to IS NOT NULL", ids)
    check(int(same) == 1, "the two deals share created_at and creator (one transaction, one person)", f"{same}")
    check(old1 == "draft", "the bug reproduced: the pre-B6 match restores deal one to deal TWO's stage ('draft', not "
          "'submitted')", f"{old1}")
    check(new1 == "submitted" and new2 == "draft" and str(r1["deal_status"]) == "submitted"
          and str(r2["deal_status"]) == "draft" and int(tagged) >= 3,
          "fixed: each history row carries its deal's id; un-archive restores deal one to 'submitted' and deal two "
          "to 'draft'", f"{new1} {new2} {r1['deal_status']} {r2['deal_status']} tagged={tagged}")


def y_b1():
    section("[Y] B1: each script's --cleanup-only, synchronously; zero fixture rows; times printed; REP2 leftovers gone")
    total = 0.0
    for script in ("verify_rep2.py", "verify_rep5.py", "verify_rep6.py", "verify_rep7.py", "verify_rep7b.py"):
        t0 = time.monotonic()
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--cleanup-only"], REPO, timeout=900,
                                 env=os.environ | {"VERIFY_NESTED": "1"})
        wall = time.monotonic() - t0
        secs = re.findall(r"^CLEANUP_SECONDS: ([\d.]+)", out, re.M)
        left = re.findall(r"^LEFTOVERS: (\d+)", out, re.M)
        total += float(secs[-1]) if secs else wall
        print(f"[INFO] {script} --cleanup-only: {secs[-1] if secs else '?'} s cleanup, {wall:.1f} s wall")
        check(code == 0 and bool(secs) and bool(left) and left[-1] == "0",
              f"{script} --cleanup-only: exits 0, prints its cleanup time, zero fixture rows remain",
              f"code={code} secs={secs[-1:] or '?'} left={left[-1:] or '?'} {(out[-300:] + err[-200:]) if code else ''}")
    print(f"[INFO] total cleanup time of all five: {total:.1f} s")
    find("B1 total cleanup time", f"{total:.1f} s for all five ({'minutes, not tens of minutes' if total < 600 else 'STILL SLOW'})")


async def y_rep2_leftovers(conn):
    n = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", REP2_LEFTOVER_ORGS,
                       fetch="fetchval")
    op = await operator_conn()
    try:
        rows = await VC.leftover_count(op, orgs=REP2_LEFTOVER_ORGS)
    finally:
        await close_conn(op)
    check(int(n) == 0 and rows == 0, "the REP2 e2001–e2003 leftover orgs are gone, with every row under them",
          f"orgs {n} rows {rows}")


def y_b2_b5():
    section("[Y] B2 / B4 / B5 proven")
    src5 = (HERE / "verify_rep5.py").read_text(encoding="utf-8")
    m = re.search(r'run_cmd\(\[sys\.executable, str\(HERE / "verify_rep2\.py"\)\][^)]*timeout=(\d+)', src5)
    check(bool(m) and m.group(1) == "2700", "B2: verify_rep5's nested verify_rep2 timeout is 2700 s",
          m.group(0)[-60:] if m else "not found")
    code, out, err = run_cmd(["node", "--test", "tests/bugfix2/"], WEB_DIR, timeout=300)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "B4: the node test proves an expired session "
          "redirects to the login (lib/authHeaders.mjs, the one place in lib/api.js)", (out + err)[-200:])
    ur = (HERE / "verify_underlyingresolution.py").read_text(encoding="utf-8")
    scan = re.search(r"for base in \(([^)]*)\)", ur)
    check("print(f\"TOTAL: {passed} PASS, {failed} FAIL\")" in ur and bool(scan) and "apps/api/scripts" not in
          scan.group(1), "B5: verify_underlyingresolution prints 'TOTAL: N PASS, M FAIL'; its code scan excludes "
          "apps/api/scripts", scan.group(1)[:120] if scan else "no scan")
    wm = (HERE / "verify_workflowmgr5.py").read_text(encoding="utf-8")
    pf = (HERE / "verify_workflowpermsfix.py").read_text(encoding="utf-8")
    check("granted to NO seeded Profile by default" not in wm and "workflowpermsfix" in wm
          and "EXPECTED_PROFILE_GRANTS" in pf, "B5: verify_workflowmgr5's assertion is the workflowpermsfix grants "
          "(Org Admin / Adviser / CSA / Ops), not 'NO seeded profile'")


def y_routes():
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; no existing gate changed; --check CURRENT")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and (gb[k].get("after"), gb[k].get("expect")) !=
               (gn[k].get("after"), gn[k].get("expect"))]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:4]} {removed[:4]}")
    new = [k for k in gn if k not in gb]

    def want(k):
        if "GET" in k[0]:
            return "view_proposals"
        return "approve_proposals" if k[1].endswith(("/approve", "/reject")) else "manage_proposals"

    bad = [(k, gn[k].get("after")) for k in new if want(k) not in str(gn[k].get("after"))]
    check(len(new) == 9 and all(k[1].startswith("/api/v1/lot-method/") for k in new) and not bad,
          "9 new routes, all under /lot-method/…, each naming its proposal permission", f"{len(new)} {bad[:3]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


def y_front_end():
    section("[Y] node --test tests/ passes; npm run build exits 0")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    fails = re.findall(r"^# fail (\d+)", out, re.M)
    passes = re.findall(r"^# pass (\d+)", out, re.M)
    check(code == 0 and fails == ["0"], "node --test tests/ (the lotmethod render test included): 0 fail",
          (out[-300:] + err[-200:]) if code else f"{passes} pass")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out[-300:] + err[-300:]) if code else "")


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
    t0 = time.monotonic()
    log: list[str] = []
    try:
        log = await teardown()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
    seconds = time.monotonic() - t0
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
    S["conn"] = conn
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    try:
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        log = await teardown()
        if log:
            find("pre-run cleanup removed an earlier run's leftovers", "; ".join(log))
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("engine default", y_engine_default)
        await run("engine methods", y_engine_methods)
        await run("engine precedence", y_engine_precedence)
        await run("harvest / x-ray", y_harvest_xray_pure)
        await run("seed", seed, conn)
        if S.get("join_proposal"):
            await run("db default", y_db_default, conn)
            await run("overrides", y_overrides, conn)
            if S.get("final_pid"):
                await run("transition + instruction", y_transition_instruction, conn)
            await run("isolation + gates", y_isolation, conn)
        else:
            check(False, "seed made the join proposal", "every fixture-scoped section skipped")
        await run("b3", y_b3, conn)
        await run("b6", y_b6, conn)
        await run("b2-b5", y_b2_b5)
        await run("routes", y_routes)
        await run("front end", y_front_end)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()

        async def after_teardown(c):
            return (await leftovers(c), await counts(c) if pre is not None else None,
                    await real_fingerprints(c) if real is not None else None)

        try:
            log = await teardown()
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
    # B1 after this run's own teardown: the five --cleanup-only runs, then the REP2 leftovers
    try:
        y_b1()
        c3 = await connect(dsn)
        try:
            await y_rep2_leftovers(c3)
        finally:
            await close_conn(c3)
    except Exception as exc:  # noqa: BLE001
        check(False, "B1 raised", f"{type(exc).__name__}: {exc}")
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
