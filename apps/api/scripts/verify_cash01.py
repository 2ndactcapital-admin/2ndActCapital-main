"""verify_cash01 — CASH01: the obligations ledger and cash coverage (per entity and per household), read-only on money.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash01.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated
from Doppler over HTTPS (``_db_connect``) first.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile
call. NO network (the httpx transport refuses and records), NO model call (the AI helpers refuse and record). REAL: the
app through an ASGI transport, services.cash_obligations / cash_coverage / networth / ta_state / ta_model, RLS and the
live database. The script's own connection does not bypass RLS (rolbypassrls = false, asserted). TIME IS INJECTED:
routers.cash's "today" is fixed at TODAY.

FIXTURES: two orgs 99000000-0000-0000-0000-0000cac10001 / 0002 (is_test = true, asserted); users with real user_roles
grants — the app's decision on each asserted:
  org A  U_STAFF advisor (view_portfolio + manage_portfolio), U_VIEW compliance (view_portfolio only), U_MEM member
         (delegate of the Ledger family), U_MEM2 member (delegate of the Other family), U_OA org_admin
         (manage_org_settings), U_RS support_staff (view + manage_portfolio; assigned to the Coverage family only)
  org B  U_B advisor
Households (org A): H1 Ledger family (trust, ILIT, GRAT, LLC), H2 Other family, H3 Coverage family (two trusts, the
hand-computed coverage); org B: HB. Reserved prefixes (the id range, sign-in tag 'cash01verify', names 'CASH01 VERIFY%')
are checked absent from real rows first.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN, keyed by this run's fixture ids; every
touched table re-read for its exact pre-test count, counted BY FIXTURE IDS ONLY (live activity writes audit_log and
member_todos). Real rows (every non-fixture cash_obligations / cash_coverage_minimums row, the Olivo demo's) are
fingerprinted before and after.

--self-checks-only: git / route / static self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and
the leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import calendar
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash01"
BEFORE_REF = "8551137"   # cash01-work's base: 'merge s29b'
_R = "any(view_portfolio|manage_portfolio)"
_W = "internal + any(manage_portfolio)"
ROUTES = {
    ("GET", "/api/v1/cash/households"): _R,
    ("GET", "/api/v1/cash/households/{household_id}/treasury"): _R,
    ("POST", "/api/v1/cash/households/{household_id}/obligations"): _W,
    ("POST", "/api/v1/cash/obligations/{obligation_id}/revise"): _W,
    ("POST", "/api/v1/cash/obligations/{obligation_id}/end"): _W,
    ("POST", "/api/v1/cash/households/{household_id}/notices"): _W,
    ("PUT", "/api/v1/cash/households/{household_id}/minimums"): _W,
    ("POST", "/api/v1/cash/households/{household_id}/alerts/sync"): _W,
}
OWN_FILES = ("apps/api/services/cash_obligations.py", "apps/api/services/cash_coverage.py", "apps/api/routers/cash.py",
             "apps/api/services/ta_state.py", "apps/api/scripts/load_olivo_obligations.py")
NODE_TEST = "tests/cash01/cash01.test.mjs"
DISCOVERY = REPO / "docs" / "cash" / "CASH00_DISCOVERY.md"
OBLIGATIONS_DOC = REPO / "docs" / "cash" / "CASH_OBLIGATIONS.md"
DESIGN_DOCS = ("CASH_TREASURY_DESIGN_v1.md", "CASH_TREASURY_ADDENDUM_v1.1_EXECUTION_ADAPTERS.md",
               "CASH_TRACK_PLAN_v1.2.md")


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def self_routes(check, find):
    """cash01's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed,
          "cash01 changed or removed no existing route's gate", f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash01's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash01's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME."""
    cov = _src("apps/api/services/cash_coverage.py")
    check("NW.figures(" in cov and "liquid_from_buckets(" in cov and "portfolio.positions" not in cov
          and "market_value" not in cov,
          "services/cash_coverage.py reads the liquid balance from the net-worth core (networth.figures buckets) and "
          "carries no positions SQL of its own (no second formula)")
    ob = _src("apps/api/services/cash_obligations.py")
    check("resolve_commitment_state(" in ob and "project_cash_flows(" in ob
          and 'STORED_SOURCE_TYPES = frozenset(k for k, _ in SOURCE_TYPES if k != "capital_call_projected")' in ob,
          "services/cash_obligations.py projects calls with the TA model at read time and never stores a projection")
    mt = _src("apps/api/routers/modeling_ta.py")
    check("from services.ta_state import TAStateError, resolve_commitment_state" in mt,
          "routers/modeling_ta.py resolves a commitment's state through services.ta_state (ONE path)")
    sql = _src("docs/cash01_part1.sql")
    for t in ("cash_obligations", "cash_coverage_minimums"):
        check(f"CREATE TABLE IF NOT EXISTS public.{t}" in sql, f"docs/cash01_part1.sql creates {t}")
    check(all(op in sql for op in ("FOR SELECT", "FOR INSERT", "FOR UPDATE", "FOR DELETE"))
          and "ENABLE ROW LEVEL SECURITY" in sql, "docs/cash01_part1.sql: RLS + a policy for every operation, in the "
          "same migration")
    doc = DISCOVERY.read_text(encoding="utf-8") if DISCOVERY.exists() else ""
    for sec in ("1a", "1b", "1c", "1d", "1e", "1f"):
        check(re.search(rf"^##+ {sec}\b", doc, re.M) is not None, f"docs/cash/CASH00_DISCOVERY.md has section {sec}")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    rs = _src("scripts/regression_set.txt")
    check("verify_cash01.py" in rs, "verify_cash01.py is in scripts/regression_set.txt")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
import routers.cash as RC  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import cash_coverage as CC  # noqa: E402
from services import cash_obligations as OB  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import networth as NW  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.ta_config import params_for_strategy  # noqa: E402
from services.ta_model import project_cash_flows  # noqa: E402
from services.ta_params import set_override_params  # noqa: E402

TAG = "cash01verify"
NAME = "CASH01 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac1"
TODAY = date(2026, 11, 9)       # the injected "today"
ZERO = Decimal(0)


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_STAFF, U_VIEW, U_MEM, U_MEM2, U_OA, U_RS, U_B = (_id(10 + i) for i in range(7))
H1, H2, H3, HB = _id(30), _id(31), _id(32), _id(33)
E_FT, E_ILIT, E_GRAT, E_LLC, E_OTH, E_C1, E_C2, E_B = (_id(40 + i) for i in range(8))
A_CASH, A_MMF, A_EQ, A_FUND, A_CASH_B = (_id(60 + i) for i in range(5))
P_FUND = _id(70)
COMMIT = _id(71)
L_MORT, L_SBL, L_ODD, L_SCHED, L_C = (_id(80 + i) for i in range(5))
DOC = _id(90)
TEAM = _id(91)

PERMS = {
    "advisor": {"view_portfolio", "manage_portfolio", "view_members"},
    "compliance": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "org_admin": {"manage_org_settings", "manage_members", "view_portfolio", "view_members"},
    "support_staff": {"view_portfolio", "manage_portfolio", "view_members"},
}
# uid → (org, users.role mirror, short, role granted)
USERS = {
    U_STAFF: (ORG_A, "member", "staff", "advisor"),
    U_VIEW: (ORG_A, "member", "view", "compliance"),
    U_MEM: (ORG_A, "member", "mem", "member"),
    U_MEM2: (ORG_A, "member", "mem2", "member"),
    U_OA: (ORG_A, "org_admin", "oa", "org_admin"),
    U_RS: (ORG_A, "member", "rs", "support_staff"),
    U_B: (ORG_B, "member", "b", "advisor"),
}
ALL_USERS = list(USERS)
WRITERS_A = {str(U_STAFF), str(U_RS)}     # the explicit holders of manage_portfolio in org A (alert recipients)
# entity → (org, type, sub_type, name)
ENTS = {
    E_FT: (ORG_A, "trust", "Dynasty", "Ledger Family Trust"),
    E_ILIT: (ORG_A, "trust", "ILIT", "Ledger ILIT"),
    E_GRAT: (ORG_A, "trust", "GRAT", "Ledger GRAT"),
    E_LLC: (ORG_A, "llc", None, "Ledger Property LLC"),
    E_OTH: (ORG_A, "individual", None, "Other Family Person"),
    E_C1: (ORG_A, "trust", None, "Coverage Family Trust"),
    E_C2: (ORG_A, "trust", None, "Coverage ILIT-like Trust"),
    E_B: (ORG_B, "individual", None, "Org B Person"),
}
HOUSEHOLDS = {H1: (ORG_A, "Ledger", (E_FT, E_ILIT, E_GRAT, E_LLC)), H2: (ORG_A, "Other", (E_OTH,)),
              H3: (ORG_A, "Coverage", (E_C1, E_C2)), HB: (ORG_B, "B", (E_B,))}
CASH_KEY, MMF_KEY, EQ_KEY, FUND_KEY = "taxonomy_mc_8_1", "taxonomy_sub_8_1_1", "taxonomy_sc_1", "taxonomy_mc_3_1"
# asset → (org, kind, key)
ASSETS = {A_CASH: (ORG_A, "cash", CASH_KEY), A_MMF: (ORG_A, "money_market", MMF_KEY),
          A_EQ: (ORG_A, "equity", EQ_KEY), A_FUND: (ORG_A, "private_fund", FUND_KEY),
          A_CASH_B: (ORG_B, "cash", CASH_KEY)}
# (owner, asset, market value)
POSITIONS = [(E_FT, A_CASH, "1000000.00"), (E_FT, A_MMF, "200000.00"), (E_FT, A_EQ, "5000000.00"),
             (E_ILIT, A_CASH, "10000.00"), (E_C1, A_CASH, "600000.00"), (E_C1, A_EQ, "900000.00"),
             (E_C2, A_CASH, "50000.00"), (E_B, A_CASH_B, "777000.00")]

COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.entity_attributes",
    "public.delegate_grants", "public.staff_assignments", "public.teams", "public.team_members", "public.org_settings",
    "portfolio.assets", "portfolio.positions", "portfolio.commitments", "portfolio.liabilities",
    "portfolio.ta_model_params", "public.documents", "public.cash_obligations", "public.cash_coverage_minimums",
    "public.audit_log", "public.member_todos",
)
SEED_CHECK_LITERALS = (
    ("public.cash_obligations", "cash_obligations_source_type_chk",
     ("capital_call_noticed", "debt_service", "tax_estimate", "insurance_premium", "trust_payment", "charitable_payout",
      "property_carrying", "tuition", "lifestyle_recurring", "one_off", "vendor_invoice")),
    ("public.cash_obligations", "cash_obligations_certainty_chk", ("firm", "estimated")),
    ("public.cash_obligations", "cash_obligations_ref_chk", ("commitment", "liability", "document")),
    ("public.cash_coverage_minimums", "cash_coverage_minimums_scope_chk", ("entity", "household")),
    ("portfolio.positions", "positions_authority_chk", ("stated",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.positions", "positions_basis_chk", ("value",)),
    ("portfolio.assets", "assets_valuation_chk", ("amortized_cost",)),
    ("portfolio.assets", "assets_class_chk", ("financial",)),
    ("portfolio.liabilities", "liabilities_kind_chk", ("mortgage", "securities_backed_line", "term_loan")),
    ("public.delegate_grants", "delegate_grants_scope_check", ("view_only",)),
)
UNIQUES = ("cash_obligations_live_uq", "cash_obligations_external_key_uq", "cash_coverage_minimums_entity_uq",
           "cash_coverage_minimums_household_uq", "assets_cash_active_uniq")

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
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


def total_line() -> str:
    return f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL"


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or "") if isinstance(exc.stdout, str) else "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


def sub_of(uid) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def dumps(o) -> str:
    """The FULL body (never truncated — a cut body makes `in` false-fail and `not in` pass vacuously)."""
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def add_months(d: date, n: int) -> date:
    """The verify's OWN month arithmetic (independent of services.cash_obligations.add_months)."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


UNTIL = add_months(TODAY, 24)


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
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


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


def fixture_scope(table: str) -> str:
    orgs, users, hhs = _arr(FIXTURE_ORGS), _arr(ALL_USERS), _arr(HOUSEHOLDS)
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({hhs})",
        "public.team_members": f"user_id = ANY({users})",
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
    }.get(table, by_org)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
            for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    orgs = [str(o) for o in FIXTURE_ORGS]
    return {
        "cash_obligations (every real org, the Olivo demo's included)": await fingerprint(
            conn, "SELECT * FROM public.cash_obligations WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "cash_coverage_minimums (real)": await fingerprint(
            conn, "SELECT * FROM public.cash_coverage_minimums WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "ta_model_params (real)": await fingerprint(
            conn, "SELECT * FROM portfolio.ta_model_params WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "liabilities (real, demo included)": await fingerprint(
            conn, "SELECT * FROM portfolio.liabilities WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "cash to-dos (real orgs)": await fingerprint(
            conn, "SELECT id, status, title FROM public.member_todos WHERE source LIKE 'cash\\_%' AND NOT (org_id = "
                  "ANY($1::uuid[]))", orgs),
        "CASH01 detail attributes (real)": await fingerprint(
            conn, "SELECT * FROM public.entity_attributes WHERE attribute_key = ANY($2::text[]) AND NOT (org_id = "
                  "ANY($1::uuid[]))", orgs, ["detail.annuity_amount", "detail.annuity_payment_date",
                                             "detail.unitrust_payment_date", "detail.policy_premium_amount",
                                             "detail.policy_premium_due", "detail.distribution_due"]),
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


TEARDOWN_PLAN_SQL = {
    "public.user_roles": ("user_id = ANY($1::uuid[])", 1),
    "public.team_members": ("user_id = ANY($1::uuid[])", 1),
    "public.household_memberships": ("household_id = ANY($3::uuid[])", 3),
    "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($2::uuid[]))", 2),
    "public.audit_log": ("org_id = ANY($2::uuid[]) OR user_id = ANY($1::uuid[])", 2),
    "public.member_todos": ("org_id = ANY($2::uuid[]) OR user_id = ANY($1::uuid[])", 2),
}


def _plan_args(k):
    users, orgs, hhs = list(ALL_USERS), list(FIXTURE_ORGS), list(HOUSEHOLDS)
    return {1: [users], 2: [users, orgs], 3: [users, orgs, hhs]}[k]


def _plan_sql(sql: str, k: int) -> tuple[str, list]:
    """A plan entry with exactly the placeholders its SQL uses (a surplus argument is an asyncpg error)."""
    used = sorted({int(n) for n in re.findall(r"\$(\d+)", sql)})
    args = _plan_args(k)
    remap = {n: i + 1 for i, n in enumerate(used)}
    out = re.sub(r"\$(\d+)", lambda m: f"${remap[int(m.group(1))]}", sql)
    return out, [args[n - 1] for n in used]


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        plan = {t: _plan_sql(sql, k) for t, (sql, k) in TEARDOWN_PLAN_SQL.items()}
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               label="verify_cash01")
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
    for t, (sql, k) in TEARDOWN_PLAN_SQL.items():
        q, args = _plan_sql(sql, k)
        n += int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {q}", *args, fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                            ALL_USERS, f"%{TAG}%", fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                            fetch="fetchval"))
    op = await operator_conn()
    try:
        for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
                "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
                "'information_schema')"):
            n += int(await op.fetchval(f"SELECT count(*) FROM {r['t']} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    finally:
        await close_conn(op)
    return n


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash01"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
    raw = res.content.decode("utf-8", "replace")
    try:
        return res.status_code, json.loads(raw or "{}")
    except ValueError:
        return res.status_code, {"raw": raw[:4000]}


class _ctx:
    """The task's RLS contextvars. ENTER IT BEFORE ``pool.acquire()`` — the wrapped pool applies the GUCs at acquire."""

    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_cash01: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash01: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    have = list(inspect.signature(RC.set_clock).parameters)
    if have != ["fn"]:
        raise RuntimeError(f"routers.cash.set_clock signature drifted: {have}")
    RC.set_clock(lambda: TODAY)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 (cash00) — decisions 1a–1f recorded, with the reuse inventory")
    doc = DISCOVERY.read_text(encoding="utf-8") if DISCOVERY.exists() else ""
    for sec in ("1a", "1b", "1c", "1d", "1e", "1f"):
        check(re.search(rf"^##+ {sec}\b", doc, re.M) is not None, f"docs/cash/CASH00_DISCOVERY.md has section {sec}")
    for marker in ("portfolio.commitments", "spv_subscriptions", "services/networth.py", "member_todos",
                   "payment_instructions", "execution_instructions", "custodian_capabilities", "PaymentAdapter",
                   "last four", "cash_obligations", "cash_coverage_minimums"):
        check(marker in doc, f"the discovery doc names {marker!r} (the reuse inventory / the recorded decisions)")
    present = [n for n in DESIGN_DOCS if (REPO / "docs" / "cash" / n).exists()]
    check("CASH_TREASURY_DESIGN_v1.md" in present and OBLIGATIONS_DOC.exists(),
          "docs/cash/ carries the design spec v1 and CASH_OBLIGATIONS.md", f"{present}")
    missing = [n for n in DESIGN_DOCS if n not in present]
    if missing:
        find("design documents not mirrored (not available to the sprint) — the decisions the prompt recorded from "
             "them are in CASH00_DISCOVERY.md 1e", f"{missing}")


async def y_schema(conn):
    section("[Y] Schema: CHECK / UNIQUE / FK constraints and policies read from the catalog BEFORE seeding")
    got, bad = 0, []
    for table, name, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2", table, name, fetch="fetchval")
        if d is None:
            bad.append((table, name, "absent"))
            continue
        got += 1
        miss = [x for x in lits if f"'{x}'" not in d]
        if miss:
            bad.append((table, name, miss))
    check(got == len(SEED_CHECK_LITERALS) and not bad, f"{got} CHECK constraints read; every literal the seeds and "
          "bodies use is one the database allows", f"{bad}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(len(idx) == len(UNIQUES) and all("UNIQUE" in v for v in idx.values()),
          f"{len(idx)} unique indexes read (one live version per lineage, one live loader key, one minimum per scope, "
          "one cash asset per org + currency)", f"missing={sorted(set(UNIQUES) - set(idx))}")
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY($1::"
                                   "regclass[])", ["public.cash_obligations", "public.cash_coverage_minimums"],
                             fetch="fetchval"))
    check(fks >= 6, "the new tables' foreign keys are read (org, entity, household, users)", f"{fks}")
    for t in ("cash_obligations", "cash_coverage_minimums"):
        ops = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                     "tablename = $1", t)}
        check(ops == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(ops)}")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{by}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and len(idx) == len(UNIQUES) and by is False)


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    ob = int(await platform(conn, "SELECT count(*) FROM cash_obligations WHERE name LIKE $1 OR entity_id::text LIKE $2",
                            NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    return check(o == 0 and u == 0 and e == 0 and ob == 0,
                 "the reserved prefixes (fixture id range, sign-in tag, 'CASH01 VERIFY' names) match no real row",
                 f"orgs={o} users={u} entities={e} obligations={ob}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH01 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH01 verify — {rname}")
                for p in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH01 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, (org, etype, sub, nm) in ENTS.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, sub_type) VALUES "
                               "($1, $2, $3, 'active', $4::entity_type, $5)", eid, org, NAME + nm, etype, sub)
        for hh, (org, nm, members) in HOUSEHOLDS.items():
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + nm + " family")
            for e in members:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        for e in HOUSEHOLDS[H1][2]:
            await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                               "effective_from) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day')",
                               ORG_A, e, U_MEM)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day')",
                           ORG_A, E_OTH, U_MEM2)
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) VALUES "
                           "($1, $2, $3, 'Lead adviser'), ($1, $4, $3, 'Lead adviser')", ORG_A, E_C1, U_RS, E_C2)
        for aid, (org, kind, key) in ASSETS.items():
            await conn.execute(
                "INSERT INTO portfolio.assets (id, org_id, name, asset_class, asset_type, ownership_basis, "
                "valuation_method, currency_code, default_taxonomy_key) VALUES ($1, $2, $3, 'financial', $4, 'value', "
                "'amortized_cost', 'USD', $5)", aid, org, NAME + f"asset {kind}", kind, key)
        for owner, asset, mv in POSITIONS:
            await conn.execute(
                "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "market_value, authority, source_system) VALUES ($1, $2, $3, $4, 'value', $5, 'stated', 'manual')",
                ENTS[owner][0], owner, asset, TODAY - timedelta(days=1), Decimal(mv))
        await conn.execute(
            "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
            "market_value, authority, source_system) VALUES ($1, $2, $3, $4, $5, 'value', 2000000.00, 'stated', "
            "'manual')", P_FUND, ORG_A, E_FT, A_FUND, TODAY - timedelta(days=1))
        await conn.execute(
            "INSERT INTO portfolio.commitments (id, org_id, position_id, commitment_amount, commitment_date, "
            "called_to_date, distributed_to_date, recallable_amount, unfunded) VALUES ($1, $2, $3, 5000000.00, "
            "'2024-01-15', 2000000.00, 0, 0, 3000000.00)", COMMIT, ORG_A, P_FUND)
        for lid, owner, nm, kind, rate, orig, bal, rt, mat in (
                (L_MORT, E_LLC, "mortgage", "mortgage", "6.00% fixed, 2040", None, "1200000.00", "fixed", "2040-01-01"),
                (L_SBL, E_FT, "securities-backed line", "securities_backed_line", "SOFR + 1.10%", "5000000.00",
                 "1000000.00", "floating", None),
                (L_ODD, E_LLC, "term loan", "term_loan", "per the loan agreement", None, "300000.00", None, None),
                (L_SCHED, E_LLC, "scheduled mortgage", "mortgage", "5.00% fixed", None, "2000000.00", "fixed", None),
                (L_C, E_C1, "coverage line", "securities_backed_line", "SOFR + 1%", "3000000.00", "500000.00",
                 "floating", None)):
            await conn.execute(
                "INSERT INTO portfolio.liabilities (id, lineage_id, org_id, owner_entity_id, name, kind, original_amount, "
                "current_balance, as_of, rate_type, rate_text, maturity) VALUES ($1, $1, $2, $3, $4, $5, $6, $7, $8, $9, "
                "$10, $11)", lid, ORG_A, owner, NAME + nm, kind, dec(orig), Decimal(bal), TODAY - timedelta(days=1), rt,
                rate, date.fromisoformat(mat) if mat else None)
        for eid, attrs in ((E_GRAT, {"trust_type": ("grat", "string"), "annuity_amount": ("250000", "decimal"),
                                     "annuity_payment_date": ("2026-12-15", "date"),
                                     "annuity_end": ("2027-12-31", "date")}),
                           (E_ILIT, {"trust_type": ("ilit", "string"), "policy_premium_amount": ("60000", "decimal"),
                                     "policy_premium_due": ("2026-12-15", "date")})):
            for k, (v, vt) in attrs.items():
                await conn.execute("INSERT INTO entity_attributes (org_id, entity_id, attribute_key, attribute_value, "
                                   "value_type) VALUES ($1, $2, $3, $4, $5)", ORG_A, eid, "detail." + k, v, vt)
        await conn.execute("INSERT INTO documents (id, org_id, entity_id, original_filename) VALUES ($1, $2, $3, $4)",
                           DOC, ORG_A, E_LLC, f"{TAG}-invoice.pdf")
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c:
            await set_override_params(c, org_id=str(ORG_A), commitment_id=str(COMMIT), ta_strategy_key="buyout",
                                      params=params_for_strategy("buyout", {}), created_by=str(U_STAFF))
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true",
          f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/2 orgs")
    S["seeded"] = await check_fixture_roles(conn)


async def check_fixture_roles(conn) -> bool:
    """Each fixture user's role AS THE APP DECIDES IT (user_roles → services.authz Principal)."""
    bad = []
    keys = set().union(*PERMS.values())
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, _acct, short, rname) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            pr = await AZ.load_principal(conn, uid)
            perms = set(getattr(pr, "permissions", ()) or ())
            ok = rname in names and pr is not None and pr.admitted and not pr.is_super_admin
            ok = ok and PERMS[rname] <= perms
            internal_expected = rname != "member"
            ok = ok and bool(pr.is_internal) is internal_expected and not pr.is_staff
            ok = ok and AZ.is_member_principal(pr) is (rname == "member")
            if not ok:
                bad.append((short, names, sorted(perms & keys), bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisor / compliance / "
                 "support staff / org admin internal, never a staff bypass; members are members)", f"{bad}")


async def treasury(uid, hh) -> tuple[int, dict]:
    return await api(uid, "GET", f"/api/v1/cash/households/{hh}/treasury")


def lines_of(env, **match) -> list[dict]:
    return [o for o in env.get("obligations") or [] if all(o.get(k) == v for k, v in match.items())]


def ent_row(env, eid) -> dict:
    return next((r for r in env.get("entities") or [] if r.get("entity_id") == str(eid)), {})


async def obligation(uid, hh, **body) -> tuple[int, dict]:
    base = {"entity_id": str(E_FT), "source_type": "one_off", "name": NAME + "item", "amount": "1000.00",
            "due_date": "2027-01-31", "certainty": "firm"}
    return await api(uid, "POST", f"/api/v1/cash/households/{hh}/obligations", {**base, **body})


async def y_ledger(conn):
    section("[Y] Ledger: stored manual / noticed / invoice rows, bi-temporal; recurrences expand (month-end, leap day); "
            "an ended recurrence stops")
    code, b = await obligation(U_STAFF, H1, name=NAME + "monthly", rrule="FREQ=MONTHLY", end_date="2027-05-31")
    ob = b.get("obligation") or {}
    check(code == 201 and ob.get("source_type") == "one_off" and ob.get("rrule") == "FREQ=MONTHLY",
          "a staff writer adds a recurring manual obligation (201)", f"{code} {dumps(b)}")
    S["monthly"] = ob
    op = await operator_conn()
    try:
        row = await op.fetchrow("SELECT lineage_id::text AS l, amount, valid_to, created_by FROM cash_obligations "
                                "WHERE id = $1::uuid", ob.get("id") or str(_id(9999)))
    finally:
        await close_conn(op)
    check(row is not None and row["l"] == ob.get("lineage_id") and row["valid_to"] is None
          and row["amount"] == Decimal("1000.00") and str(row["created_by"]) == str(U_STAFF),
          "the write persisted (re-read on an independent connection: live, its lineage, the amount, the author)",
          f"{dict(row) if row else None}")
    code, env = await treasury(U_STAFF, H1)
    got = [o["date"] for o in env.get("obligations") or [] if o.get("lineage_id") == ob.get("lineage_id")]
    check(code == 200 and got == ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"],
          "FREQ=MONTHLY from Jan 31 → each month's last day (Feb 28, Apr 30), and it stops at its end date",
          f"{code} {got}")
    code, b = await obligation(U_STAFF, H1, name=NAME + "leap", due_date="2024-02-29", rrule="FREQ=YEARLY")
    leap = (b.get("obligation") or {}).get("lineage_id")
    code2, env = await treasury(U_STAFF, H1)
    got = [o["date"] for o in env.get("obligations") or [] if o.get("lineage_id") == leap]
    check(code == 201 and got == ["2027-02-28", "2028-02-29"],
          "FREQ=YEARLY from a leap day → Feb 28 in a common year, Feb 29 in a leap year (24-month horizon)",
          f"{code} {code2} {got}")
    pure = OB.occurrences(date(2026, 1, 15), "FREQ=YEARLY;BYMONTH=1,4,6,9", end_date=None, start=TODAY,
                          until=date(2027, 12, 31))
    check([d.isoformat() for d in pure] == ["2027-01-15", "2027-04-15", "2027-06-15", "2027-09-15"],
          "the estimated-tax recurrence (BYMONTH=1,4,6,9) expands to the four dates", f"{pure}")
    # revise → a new version, same lineage; the old one closed
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/obligations/{ob.get('id')}/revise",
                        {"changes": {"amount": "1500.00"}, "reason": "verify: amount restated"})
    nv = b.get("obligation") or {}
    hist = await scoped(conn, ORG_A, "SELECT id::text AS id, amount, valid_to FROM cash_obligations WHERE lineage_id = "
                                     "$1::uuid ORDER BY valid_from, id", ob.get("lineage_id"))
    check(code == 200 and nv.get("lineage_id") == ob.get("lineage_id") and nv.get("id") != ob.get("id")
          and len(hist) == 2 and hist[0]["valid_to"] is not None and hist[1]["valid_to"] is None
          and hist[1]["amount"] == Decimal("1500.00") and hist[0]["amount"] == Decimal("1000.00"),
          "a revision closes the live version and inserts a new one on the same lineage (Rule 3; never in place)",
          f"{code} {dumps(b)} {[dict(h) for h in hist]}")
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/obligations/{nv.get('id')}/end", {"reason": "verify: paid"})
    code2, env = await treasury(U_STAFF, H1)
    after = [o for o in env.get("obligations") or [] if o.get("lineage_id") == ob.get("lineage_id")]
    live = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE lineage_id = $1::uuid AND "
                                         "valid_to IS NULL", ob.get("lineage_id"), fetch="fetchval"))
    kept = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE lineage_id = $1::uuid",
                            ob.get("lineage_id"), fetch="fetchval"))
    check(code == 200 and code2 == 200 and after == [] and live == 0 and kept == 2
          and (b.get("obligation") or {}).get("end_reason") == "verify: paid",
          "ending stops the recurrence (no line in the horizon) and keeps both versions as history",
          f"{code} {len(after)} live={live} kept={kept}")
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/obligations/{nv.get('id')}/end", {"reason": "again"})
    check(code == 404, "ending an ended obligation is a 404 (nothing live to end)", f"{code}")
    code, b = await obligation(U_STAFF, H1, entity_id=str(E_LLC), source_type="vendor_invoice", name=NAME + "invoice",
                               amount="12500.00", due_date="2026-12-20", source_ref_type="document",
                               source_ref_id=str(DOC))
    inv = b.get("obligation") or {}
    check(code == 201 and inv.get("source_ref_id") == str(DOC) and inv.get("source_label") == "Vendor invoice",
          "a vendor invoice is stored linked to its Chancery document", f"{code} {dumps(b)}")
    code, b = await obligation(U_STAFF, H1, entity_id=str(E_LLC), source_type="vendor_invoice", name=NAME + "bad inv")
    check(code == 422 and "document" in str(b.get("detail")), "an invoice without its document is refused (422)",
          f"{code} {dumps(b)}")
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/households/{H1}/obligations",
                        {"entity_id": str(E_FT), "source_type": "one_off", "name": NAME + "float", "amount": 1000.5,
                         "due_date": "2027-01-31", "certainty": "firm"})
    check(code == 422, "a float amount is refused at the boundary (money is text → Decimal)", f"{code}")
    code, b = await obligation(U_STAFF, H1, entity_id=str(E_OTH))
    check(code == 422, "a payer outside the household is refused (422)", f"{code} {dumps(b)}")
    refusal = None
    try:
        await scoped(conn, ORG_A, "INSERT INTO cash_obligations (lineage_id, org_id, entity_id, source_type, name, amount, "
                                  "due_date, certainty) VALUES (gen_random_uuid(), $1, $2, 'capital_call_projected', "
                                  "'x', 1, '2027-01-01', 'estimated')", ORG_A, E_FT, fetch="execute")
    except asyncpg.CheckViolationError as exc:
        refusal = exc.constraint_name
    check(refusal == "cash_obligations_source_type_chk", "the database refuses to STORE a projected call (it is "
          "computed at read time only)", f"{refusal}")


def expected_projection() -> list[tuple[date, Decimal]]:
    params = params_for_strategy("buyout", {})
    step = 12 // params.periods_per_year
    n = 24 // step
    periods = project_cash_flows(committed_capital=Decimal("5000000.00"), called_to_date=Decimal("2000000.00"),
                                 distributed_to_date=ZERO, current_nav=Decimal("2000000.00"), params=params,
                                 horizon_periods=n + 1)
    return [(add_months(TODAY, p.period * step), p.contribution) for p in periods
            if add_months(TODAY, p.period * step) <= UNTIL and p.contribution > 0]


async def y_derived(conn):
    section("[Y] Derived sources: projected calls (TA), noticed supersession, debt service, trust payments — labelled")
    code, env = await treasury(U_STAFF, H1)
    proj = lines_of(env, source_type="capital_call_projected", source_ref_id=str(COMMIT))
    exp = expected_projection()
    got = [(date.fromisoformat(o["date"]), Decimal(o["amount"])) for o in proj]
    check(code == 200 and len(exp) > 0 and got == exp and all(o["derived"] is True and "TA model" in o["derived_from"]
                                                             and o["certainty"] == "estimated" for o in proj),
          f"the fixture commitment's TA projection appears as {len(exp)} projected, derived, estimated calls — the SAME "
          "amounts ta_model.project_cash_flows gives for it", f"{code} got={got[:3]} exp={exp[:3]}")
    stored_proj = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE org_id = $1 AND "
                                                "(source_type = 'capital_call_projected' OR source_ref_id = $2)",
                                   ORG_A, COMMIT, fetch="fetchval"))
    check(stored_proj == 0, "no projected call was copied into the table (nothing stored against the commitment yet)",
          f"{stored_proj}")
    first = exp[0][0] if exp else TODAY
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/households/{H1}/notices",
                        {"entity_id": str(E_FT), "commitment_id": str(COMMIT), "name": NAME + "call notice 1",
                         "amount": "400000.00", "due_date": (first + timedelta(days=5)).isoformat()})
    notice = b.get("obligation") or {}
    check(code == 201 and b.get("matched_commitment_id") == str(COMMIT) and notice.get("source_ref_id") == str(COMMIT)
          and notice.get("certainty") == "firm", "a capital-call notice is matched to its commitment", f"{code} "
          f"{dumps(b)}")
    code, env = await treasury(U_STAFF, H1)
    proj = lines_of(env, source_type="capital_call_projected", source_ref_id=str(COMMIT))
    sup = [o for o in proj if o["counted"] is False]
    nline = lines_of(env, lineage_id=notice.get("lineage_id"))
    counted = sum((Decimal(o["amount"]) for o in proj + nline if o["counted"]), ZERO)
    want = sum((a for _d, a in exp), ZERO) - exp[0][1] + Decimal("400000.00") if exp else None
    check(len(proj) == len(exp) and len(sup) == 1 and sup[0]["date"] == first.isoformat()
          and sup[0]["superseded_by"] == notice.get("lineage_id") and len(nline) == 1 and nline[0]["counted"] is True
          and counted == want,
          "the notice supersedes the NEAREST projected call: the projection is kept (not counted), the call is counted "
          "ONCE", f"sup={sup[:1]} counted={counted} want={want}")
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/households/{H1}/notices",
                        {"entity_id": str(E_LLC), "name": NAME + "unmatched notice", "amount": "50000.00",
                         "due_date": "2026-12-10"})
    un = b.get("obligation") or {}
    S["unmatched"] = un
    code2, env = await treasury(U_STAFF, H1)
    check(code == 201 and b.get("matched_commitment_id") is None
          and any(n.get("lineage_id") == un.get("lineage_id") for n in env.get("unmatched_notices") or [])
          and len(lines_of(env, lineage_id=un.get("lineage_id"))) == 1,
          "a notice matching no commitment is recorded UNMATCHED (it still counts) and listed for matching",
          f"{code} {dumps(b)}")
    todos = await scoped(conn, ORG_A, "SELECT id::text AS id, user_id::text AS u, status FROM member_todos WHERE "
                                      "org_id = $1 AND source = 'cash_unmatched_notice' AND related_id = $2::uuid",
                         ORG_A, un.get("lineage_id"))
    check({t["u"] for t in todos} == WRITERS_A and len(todos) == len(WRITERS_A) and all(t["status"] == "open"
                                                                                        for t in todos),
          "the unmatched notice raises ONE open to-do per obligation author (manage_portfolio holders)",
          f"{[dict(t) for t in todos]}")
    S["unmatched_todos"] = sorted(t["id"] for t in todos)
    mort = [o for o in env.get("obligations") or [] if o.get("source_ref_id") == str(L_MORT)]
    check(len(mort) > 0 and all(Decimal(o["amount"]) == Decimal("6000.00") and o["certainty"] == "estimated"
                                and "interest only" in o["explanation"] and o["derived"] is True for o in mort)
          and mort[0]["date"] == "2026-12-01",
          "debt service from a fixed rate only: an interest-only estimate (1,200,000 × 6% ÷ 12 = 6,000.00 monthly), "
          "labelled 'estimated — interest only, rate from terms'", f"{mort[:1]}")
    ne = {x.get("source_ref_id"): x for x in env.get("not_estimated") or []}
    check(str(L_ODD) in ne and "needs payment terms" in ne[str(L_ODD)]["text"]
          and not [o for o in env.get("obligations") or [] if o.get("source_ref_id") == str(L_ODD)],
          "unparseable terms read 'not estimated — needs payment terms' (no line, no guessed number)",
          f"{ne.get(str(L_ODD))}")
    check(str(L_SBL) in ne and "needs payment terms" in ne[str(L_SBL)]["text"],
          "a floating rate on an index the app does not carry is not estimated either", f"{ne.get(str(L_SBL))}")
    code, b = await obligation(U_STAFF, H1, entity_id=str(E_LLC), source_type="debt_service", name=NAME + "schedule",
                               amount="9000.00", due_date="2026-12-01", rrule="FREQ=MONTHLY", certainty="firm",
                               source_ref_type="liability", source_ref_id=str(L_SCHED))
    code2, env = await treasury(U_STAFF, H1)
    sched = [o for o in env.get("obligations") or [] if o.get("source_ref_id") == str(L_SCHED)]
    check(code == 201 and len(sched) == 24 and all(o["derived"] is False and Decimal(o["amount"]) == Decimal("9000.00")
                                                   for o in sched),
          "a liability WITH a schedule: the stored schedule is used and no interest-only estimate is derived for it",
          f"{code} {dumps(b) if code != 201 else ''} n={len(sched)} derived={[o for o in sched if o['derived']][:1]}")
    grat = lines_of(env, payer_entity_id=str(E_GRAT), source_type="trust_payment")
    check([(o["date"], Decimal(o["amount"])) for o in grat] == [("2026-12-15", Decimal("250000.00")),
                                                                ("2027-12-15", Decimal("250000.00"))]
          and all(o["derived"] and o["derived_from"] == "the GRAT's CRM terms" and o["certainty"] == "firm"
                  for o in grat),
          "a fixture GRAT's annuity from its CRM terms: yearly, through Annuity end (2027-12-31), labelled",
          f"{grat}")
    prem = lines_of(env, payer_entity_id=str(E_ILIT), source_type="insurance_premium")
    check([o["date"] for o in prem] == ["2026-12-15", "2027-12-15"] and all(
        Decimal(o["amount"]) == Decimal("60000.00") and o["derived"] for o in prem),
          "an ILIT's premium from its CRM policy fields", f"{prem}")
    loc = {x.get("liability_id"): x for x in env.get("lines_of_credit") or []}
    check(str(L_SBL) in loc and dec(loc[str(L_SBL)]["undrawn"]) == Decimal("4000000.00"),
          "the securities-backed line's undrawn amount (limit 5,000,000 − drawn 1,000,000) is shown separately",
          f"{loc.get(str(L_SBL))}")


async def y_coverage(conn):
    section("[Y] Coverage: hand-computed months / covered-through / next gap per entity and household; undrawn line "
            "separate; flags raised once; closed when resolved")
    bodies = [dict(entity_id=str(E_C1), source_type="lifestyle_recurring", name=NAME + "C1 monthly", amount="10000.00",
                   due_date="2026-12-01", rrule="FREQ=MONTHLY", certainty="estimated"),
              dict(entity_id=str(E_C2), source_type="one_off", name=NAME + "C2 monthly", amount="1000.00",
                   due_date="2026-12-01", rrule="FREQ=MONTHLY", certainty="estimated"),
              dict(entity_id=str(E_C2), source_type="insurance_premium", name=NAME + "C2 premium", amount="120000.00",
                   due_date="2027-01-15", certainty="firm")]
    codes = [(await obligation(U_STAFF, H3, **b))[0] for b in bodies]
    check(codes == [201, 201, 201], "the coverage fixture's three obligations are recorded", f"{codes}")
    code, env = await treasury(U_STAFF, H3)
    c1, c2, hh = ent_row(env, E_C1), ent_row(env, E_C2), env.get("coverage") or {}
    # E_C1: 600,000 ÷ (12 × 10,000 ÷ 12) = 60.0 months; 24 × 10,000 = 240,000 ≤ 600,000 → covered beyond the horizon
    check(code == 200 and dec(c1.get("liquid")) == Decimal("600000.00") and dec(c1.get("months")) == Decimal("60.0")
          and c1.get("covered_beyond_horizon") is True and c1.get("covered_through") == UNTIL.isoformat()
          and c1.get("next_gap") is None and c1.get("flags") == [],
          "the covered entity: 600,000 liquid ÷ 10,000 a month = 60.0 months, covered beyond the 24-month horizon",
          f"{code} {dumps(c1)}")
    # E_C2: 12-month outflow 120,000 + 12 × 1,000 = 132,000 → 11,000 / month → 50,000 ÷ 11,000 = 4.5 months;
    # walk: Dec 1 (49,000), Jan 1 (48,000), Jan 15 premium 120,000 > 48,000 → gap; covered through Jan 14
    g = c2.get("next_gap") or {}
    check(dec(c2.get("liquid")) == Decimal("50000.00") and dec(c2.get("months")) == Decimal("4.5")
          and c2.get("covered_through") == "2027-01-14" and g.get("name") == NAME + "C2 premium"
          and dec(g.get("amount")) == Decimal("120000.00") and g.get("date") == "2027-01-15"
          and dec(g.get("shortfall")) == Decimal("72000.00")
          and set(c2.get("flags") or []) == {"below_minimum", "uncovered_90d"},
          "the ILIT-like entity: 4.5 months (below 6), covered through 2027-01-14, next gap the premium (~120,000, "
          "short 72,000) — uncovered within 90 days", f"{dumps(c2)}")
    # household: 650,000 ÷ ((120,000 + 132,000) ÷ 12 = 21,000) = 30.95 → 31.0; 240,000 + 144,000 = 384,000 ≤ 650,000
    check(dec(hh.get("liquid")) == Decimal("650000.00") and dec(hh.get("months")) == Decimal("31.0")
          and hh.get("covered_beyond_horizon") is True and hh.get("next_gap") is None and hh.get("flags") == [],
          "the HOUSEHOLD is covered (31.0 months, beyond the horizon) while its ILIT-like entity is not",
          f"{dumps(hh)}")
    loc = {x.get("liability_id"): x for x in env.get("lines_of_credit") or []}
    check(str(L_C) in loc and dec(loc[str(L_C)]["undrawn"]) == Decimal("2500000.00")
          and dec(hh.get("liquid")) == Decimal("650000.00") and dec(c1.get("liquid")) == Decimal("600000.00"),
          "the undrawn securities-backed line (2,500,000) is shown separately and NOT counted in any liquid balance",
          f"{loc.get(str(L_C))}")
    c1_debt = [o for o in env.get("obligations") or [] if o.get("source_ref_id") == str(L_C)]
    check(c1_debt == [], "the floating line adds no guessed debt-service line to the walk", f"{c1_debt[:1]}")

    async def todos(source):
        return await scoped(conn, ORG_A, "SELECT id::text AS id, user_id::text AS u, status, related_type FROM "
                                         "member_todos WHERE org_id = $1 AND source = $2 AND related_id = $3::uuid "
                                         "ORDER BY id", ORG_A, source, E_C2)

    c1s, b1 = await api(U_STAFF, "POST", f"/api/v1/cash/households/{H3}/alerts/sync")
    t_min, t_gap = await todos("cash_below_minimum"), await todos("cash_uncovered_90d")
    c2s, b2 = await api(U_STAFF, "POST", f"/api/v1/cash/households/{H3}/alerts/sync")
    t_min2, t_gap2 = await todos("cash_below_minimum"), await todos("cash_uncovered_90d")
    others = int(await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND source LIKE "
                                           "'cash\\_%' AND related_id = ANY($2::uuid[])", ORG_A, [E_C1, H3],
                              fetch="fetchval"))
    check(c1s == 200 and c2s == 200 and {t["u"] for t in t_min} == WRITERS_A and len(t_min) == len(WRITERS_A)
          and {t["u"] for t in t_gap} == WRITERS_A and len(t_gap) == len(WRITERS_A)
          and [t["id"] for t in t_min2] == [t["id"] for t in t_min] and [t["id"] for t in t_gap2] == [
              t["id"] for t in t_gap] and others == 0,
          "below-minimum and uncovered-within-90-days each raise ONE to-do per author; a second sync refreshes, never "
          "duplicates; the covered entity and household raise none", f"{len(t_min)} {len(t_gap)} → {len(t_min2)} "
          f"{len(t_gap2)} others={others} {dumps(b2)}")
    code, b = await api(U_STAFF, "PUT", f"/api/v1/cash/households/{H3}/minimums",
                        {"scope_type": "entity", "entity_id": str(E_C2), "months": "4", "reason": "verify: lower"})
    t_min3, t_gap3 = await todos("cash_below_minimum"), await todos("cash_uncovered_90d")
    mins = await scoped(conn, ORG_A, "SELECT min_months, valid_to FROM cash_coverage_minimums WHERE entity_id = $1",
                        E_C2)
    check(code == 200 and t_min3 and all(t["status"] == "done" for t in t_min3) and t_gap3
          and all(t["status"] == "open" for t in t_gap3) and len(mins) == 1 and mins[0]["min_months"] == Decimal("4"),
          "a resolved flag closes its to-do (kept, status done); the still-uncovered premium's to-do stays open",
          f"{code} {dumps(b)} {[dict(t) for t in t_min3]}")
    code, env = await treasury(U_STAFF, H3)
    check(set(ent_row(env, E_C2).get("flags") or []) == {"uncovered_90d"} and ent_row(env, E_C2).get("min_months") == "4",
          "the minimum is editable per entity (4.5 months is no longer below it)", f"{dumps(ent_row(env, E_C2))}")
    code, b = await api(U_STAFF, "POST", f"/api/v1/cash/obligations/{(S.get('unmatched') or {}).get('id')}/end",
                        {"reason": "verify: matched by hand"})
    rows = await scoped(conn, ORG_A, "SELECT status FROM member_todos WHERE id = ANY($1::uuid[])",
                        S.get("unmatched_todos") or [])
    check(code == 200 and len(rows) == len(WRITERS_A) and all(r["status"] == "done" for r in rows),
          "ending the unmatched notice closes its to-dos", f"{code} {[dict(r) for r in rows]}")


async def y_liquid(conn):
    section("[Y] Liquid balance == the net-worth core's Cash & Equivalents (independent SQL; no second formula)")
    sql = ("SELECT COALESCE(sum(p.market_value), 0) FROM portfolio.positions p JOIN portfolio.assets a ON a.id = "
           "p.asset_id WHERE p.org_id = $1 AND p.owner_entity_id = $2 AND p.valid_to IS NULL AND p.system_to IS NULL "
           "AND (COALESCE(p.taxonomy_key, a.default_taxonomy_key) = 'taxonomy_mc_8_1' OR COALESCE(p.taxonomy_key, "
           "a.default_taxonomy_key) ~ '^taxonomy_sub_8_1_[0-9]+$')")
    code, env1 = await treasury(U_STAFF, H1)
    code3, env3 = await treasury(U_STAFF, H3)
    pool = await get_pool()
    with _ctx(ORG_A):
        async with pool.acquire() as c:
            ctx = await NW.load(c, str(ORG_A), [str(E_FT), str(E_C1), str(E_C2)], as_of=TODAY)
    for eid, env, want in ((E_FT, env1, Decimal("1200000.00")), (E_C1, env3, Decimal("600000.00"))):
        indep = await scoped(conn, ORG_A, sql, ORG_A, eid, fetch="fetchval")
        core = CC.liquid_from_buckets(NW.figures(ctx, weights={str(eid): Decimal(1)})["by_bucket"])
        shown = dec(ent_row(env, eid).get("liquid"))
        check(shown is not None and shown == indep == core == want,
              f"{ENTS[eid][3]}: shown liquid == independent SQL == networth.figures Cash & Equivalents ({want}; the "
              "equity and the fund are not cash)", f"shown={shown} sql={indep} core={core}")
    hh_sql = sum([await scoped(conn, ORG_A, sql, ORG_A, e, fetch="fetchval") for e in (E_C1, E_C2)], ZERO)
    core_h = CC.liquid_from_buckets(NW.figures(ctx, {str(E_C1), str(E_C2)})["by_bucket"])
    check(dec((env3.get("coverage") or {}).get("liquid")) == hh_sql == core_h,
          "the household's liquid balance == the core household figure's Cash & Equivalents == independent SQL",
          f"{(env3.get('coverage') or {}).get('liquid')} {hh_sql} {core_h}")


async def y_access(conn):
    section("[Y] Screens' envelope + member read-only with the library's labels; refusals; org isolation")
    code, env = await treasury(U_MEM, H1)
    p, v = env.get("permissions") or {}, env.get("vocabularies") or {}
    lab = (env.get("labels") or {}).get(CC.ESTIMATE_KEY) or {}
    check(code == 200 and env.get("member_view") is True and p.get("can_write") is False and v.get("editable") == []
          and env.get("actions") == [] and env.get("stored") == [] and v.get("manual_source_types") == [],
          "a member reads their family's Treasury READ-ONLY (no write flag, editable EMPTY, no actions)",
          f"{code} {dumps(p)} {v.get('editable')}")
    check(bool(lab.get("text")) and lab.get("key") == CC.ESTIMATE_KEY and lab.get("source") in ("library", "fallback")
          and (lab.get("source") == "library" or lab.get("text") == CC.COVERAGE_ESTIMATE)
          and "counsel" in (lab.get("text") or "") and bool(((env.get("labels") or {}).get("tax.estimate_label")
                                                             or {}).get("text")),
          "the member envelope carries the estimate labels from the disclosure library (cash.coverage_estimate — "
          "the built-in 'have counsel review' text until a firm publishes one — and tax.estimate_label)",
          f"{dumps(lab)}")
    ids = {e.get("entity_id") for e in env.get("entities") or []}
    check(ids <= {str(e) for e in HOUSEHOLDS[H1][2]} and str(E_OTH) not in json.dumps(env),
          "the member's envelope names only their own family's records", f"{ids}")
    for hh, what in ((H2, "another family's household"), (HB, "another org's household")):
        code, b = await treasury(U_MEM, hh)
        check(code == 404, f"a member opening {what} gets 404 (never another family)", f"{code} {dumps(b)[:300]}")
    code, b = await api(U_MEM, "GET", "/api/v1/cash/households")
    check(code == 200 and [h.get("id") for h in b.get("households") or []] == [str(H1)],
          "the member's household list is exactly their own family's", f"{code} {dumps(b)}")
    code, b = await treasury(U_MEM2, H1)
    check(code == 404, "the OTHER family's member cannot open the Ledger family (404)", f"{code}")
    n0 = int(await platform(conn, "SELECT count(*) FROM cash_obligations WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS,
                            fetch="fetchval"))
    code_m, bm = await obligation(U_MEM, H1, name=NAME + "member try")
    code_v, bv = await obligation(U_VIEW, H1, name=NAME + "viewer try")
    n1 = int(await platform(conn, "SELECT count(*) FROM cash_obligations WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS,
                            fetch="fetchval"))
    check(code_m == 403 and code_v == 403 and "manage_portfolio" in str(bv.get("detail")) and n1 == n0,
          "with a VALID body, a member and a view-only staff member are refused (403, naming manage_portfolio); "
          "nothing is written", f"{code_m} {dumps(bm)} {code_v} {dumps(bv)} {n0}→{n1}")
    code, env = await treasury(U_VIEW, H1)
    check(code == 200 and (env.get("permissions") or {}).get("can_write") is False
          and (env.get("vocabularies") or {}).get("editable") == [] and env.get("actions") == [],
          "a view-only staff member reads it with no write control (editable EMPTY)", f"{code}")
    code, env = await treasury(U_STAFF, H1)
    check(code == 200 and (env.get("permissions") or {}).get("can_write") is True
          and set((env.get("vocabularies") or {}).get("editable") or []) == {"obligation", "notice", "minimum"},
          "the author's envelope lists the write controls", f"{code} {dumps(env.get('permissions'))}")
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    code, b = await treasury(U_B, H1)
    code2, envb = await treasury(U_B, HB)
    check(code == 404 and code2 == 200 and dec((envb.get("coverage") or {}).get("liquid")) == Decimal("777000.00")
          and str(ORG_A) not in json.dumps(envb) and str(E_FT) not in json.dumps(envb),
          "org B cannot open org A's household (404) and its own carries nothing of org A", f"{code} {code2}")
    a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE org_id = $1", ORG_A, fetch="fetchval"))
    ab = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_obligations WHERE org_id = $1", ORG_A,
                          fetch="fetchval"))
    code, b = await obligation(U_B, HB, entity_id=str(E_B), name=NAME + "B item")
    b_n = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_obligations WHERE org_id = $1", ORG_B,
                           fetch="fetchval"))
    ba = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE org_id = $1", ORG_B,
                          fetch="fetchval"))
    check(code == 201 and a > 0 and b_n > 0 and ab == 0 and ba == 0,
          "cash_obligations: each org reads its own rows and none of the other's (both directions)",
          f"A={a} B={b_n} A→B={ab} B→A={ba}")
    refusal = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                               "'false', true)", str(ORG_B))
            await conn.execute("INSERT INTO cash_obligations (lineage_id, org_id, entity_id, source_type, name, amount, "
                               "due_date, certainty) VALUES (gen_random_uuid(), $1, $2, 'one_off', 'probe', 1, "
                               "'2027-01-01', 'firm')", ORG_A, E_FT)
            raise RuntimeError("accepted")
    except asyncpg.PostgresError as exc:
        refusal = type(exc).__name__
    except RuntimeError:
        refusal = None
    check(refusal is not None, "org B cannot write an obligation into org A (RLS refuses the insert)", f"{refusal}")
    upd = await scoped(conn, ORG_B, "UPDATE cash_obligations SET amount = 0 WHERE org_id = $1", ORG_A, fetch="execute")
    check(upd == "UPDATE 0", "org B's UPDATE of org A's obligations matches nothing", f"{upd}")


async def y_visibility(conn):
    section("[Y] VISIBILITY1 ON (fixture org): restricted staff see only their families")
    code, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement", {"enforced": True, "confirm": True})
    check(code == 200, "the fixture org admin turns staff visibility ON for org A", f"{code} {dumps(b)[:300]}")
    try:
        c1, _e1 = await treasury(U_RS, H3)
        c2, b2 = await treasury(U_RS, H1)
        c3, b3 = await obligation(U_RS, H1, name=NAME + "restricted try")
        c4, b4 = await api(U_RS, "GET", "/api/v1/cash/households")
        listed = [h.get("id") for h in b4.get("households") or []]
        check(c1 == 200 and c2 == 404 and c3 == 404 and str(H1) not in listed and str(H3) in listed,
              "a restricted staff member opens their assigned family (200), not another (404 — read and write), and "
              "lists only theirs", f"{c1} {c2} {c3} {listed}")
    finally:
        code, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement",
                            {"enforced": False, "confirm": True})
        check(code == 200, "staff visibility turned back OFF for the fixture org", f"{code}")


async def y_olivo(conn):
    section("[Y] Olivo loader --dry-run writes nothing (real + demo rows unchanged)")
    before = await real_fingerprints(conn)
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "load_olivo_obligations.py"), "--dry-run"], REPO,
                             timeout=900, env=env)
    after = await real_fingerprints(conn)
    rows = len(re.findall(r"^\s+\[(create|revise|unchanged)\] demo-olivo:", out, re.M))
    check(code == 0 and "DRY RUN — nothing written" in out and rows == 18 and "HOUSEHOLD Olivo Family" in out,
          "load_olivo_obligations.py --dry-run lists every one of the 18 rows and the coverage it would show",
          f"{code} rows={rows} {(out + err)[-600:]}")
    check(after == before, "the dry run changed no real or demo row (cash tables, liabilities, to-dos fingerprinted)",
          f"{[k for k in before if before[k] != after.get(k)]}")
    S["olivo_out"] = out


async def y_routes(conn):
    section("[Y] Own routes gated; authz1_routes.py --check CURRENT; visibility1_routes.py --check")
    self_routes(check, find)
    self_static(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0, "visibility1_routes.py --check: current", (out + err)[-300:])


async def y_real(conn):
    section("[Y] Real and demo rows unchanged by the whole run")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) >= 6 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the cash01 render tests ({NODE_TEST}, run by path: coverage card, entity table, calendar, member "
          f"read-only) all pass ({n.group(1) if n else '?'})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rollup2.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rollup2.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_rollup2.py: 0 FAIL (its own final TOTAL line and exit code)",
          f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    check(left == 0, "zero fixture rows remain", f"{left}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working DATABASE_URL", how)
        print(total_line())
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
        if not await guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("ledger", y_ledger, conn)
            await run("derived", y_derived, conn)
            await run("coverage", y_coverage, conn)
            await run("liquid", y_liquid, conn)
            await run("access", y_access, conn)
            await run("visibility", y_visibility, conn)
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("olivo", y_olivo, conn)
        await run("real", y_real, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        RC.set_clock(None)
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (obligations, minimums, positions, liabilities, to-dos, …)",
                  f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count (fixture rows)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k]} → {after.get(k)}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
        if S.get("no_teardown"):
            print(total_line())
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(total_line())
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main_async())
    except BaseException as exc:  # noqa: BLE001 — TOTAL on every exit path, the traceback printed
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise
        traceback.print_exc()
        check(False, "the verify crashed", f"{type(exc).__name__}: {exc}")
        print(total_line())
        rc = 1
    sys.exit(rc)
