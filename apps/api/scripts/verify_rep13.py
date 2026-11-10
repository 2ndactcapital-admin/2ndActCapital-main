"""verify_rep13 — Replication Studio, sprint 13 (REP13, phase 4 begins): the tax-aware LONG/SHORT extension
(130/30, 150/50) on any custodian that declares margin, short selling and stock borrow.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep13.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is
set before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``), the Auth0 profile call, R2 (``services.storage.upload_bytes`` /
``download_bytes`` → an in-memory dict, for the model upload), the security-data layer's end-of-day reads for
securities an account does not hold (``rebalance_proposals._unheld_prices`` at its CURRENT signature — fixture
prices; held positions price from their own market value as in production), and the price history the tracking
error reads (``long_short.history_reader`` — the module's own seam, at ``tilts.history_for``'s signature). Three
fixture trade adapters are registered for the fixture custodian routes ('zzr13ls' declares short selling; 'zzr13nd'
does not; 'zzr13ma' reads 'zzr13nd's capability, like Altruist's fixed capability row). NO network (the httpx
transport refuses and records), NO model call (the three AI helpers refuse and record). REAL: the app through an
ASGI transport, the proposal engine, the human-task engine, maker-checker, rep2's instructions and fills, RLS and
the live database. The script's own connection is app_service (rolbypassrls = false, asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-0000001d0001 / 0002, is_test = true, asserted); users seeded with real
user_roles grants (Super Admin through rbac.grant_named_role, org admins through grant_org_admin — the app's
decision on each asserted at the seed); platform securities named 'REP13 VERIFY …' with tickers ZZR13…, their
reference attributes (provider 'zz_rep13_test'), platform capability rows for custodian codes zzr13…, platform
borrow rates on fixture securities — every reserved prefix checked absent from real rows first, removed BY ID.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with execution.teardown = 'fixture' (and
models / replica), keyed by this run's fixture ids; then every counted table is re-read for its exact pre-test
count — audit_log and every table real activity writes to counted over FIXTURE rows only.

--self-checks-only: git / route self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and the
leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import csv
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep13"
BEFORE_REF = "219f057"   # rep13-work's base: 'prices1 fix 1'
_L = "/api/v1/long-short"
ROUTES = {
    ("GET", f"{_L}/accounts/{{account_id}}"): "view_proposals",
    ("GET", f"{_L}/accounts/{{account_id}}/tax-estimate"): "view_tax",
    ("GET", f"{_L}/borrow-rates"): "view_proposals",
    ("GET", f"{_L}/proposals/{{proposal_id}}"): "view_proposals",
    ("PUT", f"{_L}/accounts/{{account_id}}/settings/draft"): "manage_proposals",
    ("POST", f"{_L}/accounts/{{account_id}}/settings"): "manage_proposals",
    ("POST", f"{_L}/accounts/{{account_id}}/margin-agreement"): "manage_proposals",
    ("POST", f"{_L}/accounts/{{account_id}}/margin-agreement/remove"): "manage_proposals",
    ("POST", f"{_L}/margin-agreements/{{record_id}}/approve"): "approve_proposals",
    ("POST", f"{_L}/margin-agreements/{{record_id}}/reject"): "approve_proposals",
    ("POST", f"{_L}/borrow-rates"): "manage_proposals",
    ("POST", f"{_L}/borrow-rates/platform"): "super_admin",
    ("POST", f"{_L}/proposals/{{proposal_id}}/acknowledge"): "manage_proposals",
    ("POST", f"{_L}/proposals/{{proposal_id}}/execute"): "manage_proposals",
}
OWN_FILES = ("apps/api/services/long_short.py", "apps/api/services/long_short_rules.py",
             "apps/api/routers/long_short.py")


def self_routes(check, find):
    """rep13's own routes: BEFORE_REF → its own merge (or HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "rep13 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate at rep13's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "rep13's routes all still exist today with the same gate", f"{drift[:4]}")


def self_code(check, find):
    """rep13's own code at its own end point: the §1259 check sits in the ONE instruction chokepoint; the approval
    precheck is registered on both proposal task kinds; the adapter refuses an undeclared short at submit."""
    ex = VS.own_text(SPRINT_KEY, "apps/api/services/execution.py") or ""
    body = ex.split("async def create_instructions(", 1)[-1].split("\nasync def ", 1)[0]
    check("LS.instruction_guard(conn, org_id, acct, t)" in body,
          "every SELL SHORT instruction (a proposal's or a manual one) passes long_short.instruction_guard inside "
          "execution.create_instructions", f"{len(body)} chars")
    rp = VS.own_text(SPRINT_KEY, "apps/api/services/rebalance_proposals.py") or ""
    check("HT.register_precheck(TASK_APPROVAL, _long_short._approval_precheck)" in rp
          and "HT.register_precheck(TASK_REVIEW, _long_short._approval_precheck)" in rp,
          "the long/short approval precheck is registered on the approval AND the review task kinds")
    tr = VS.own_text(SPRINT_KEY, "apps/api/services/custody/trading.py") or ""
    check("await adapter.refuse_undeclared_shorts(conn, trade_list)" in tr and "if manual:" in tr,
          "route_trade_list: the custodian adapter refuses an undeclared short before submit; the manual route is a "
          "separate, always-available branch")
    ls = VS.own_text(SPRINT_KEY, OWN_FILES[0]) or ""
    check("override" not in ls.lower(), "the long/short service has no override of any kind (the §1259 block cannot "
          "be bypassed in this sprint)")
    for f in OWN_FILES:
        src = VS.own_text(SPRINT_KEY, f) or ""
        check(bool(src) and not re.search(r"call_claude|anthropic|litellm|httpx\.", src),
              f"{f}: no model call and no network client", f"{len(src)} chars")


def self_checks(check, find):
    self_routes(check, find)
    self_code(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
import _verify_scope as VSC  # noqa: E402  (stdlib only) — live-written tables counted by fixture ids
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import account_models as AM  # noqa: E402
from services import blocks as BK  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import harvest as HV  # noqa: E402
from services import long_short as LS  # noqa: E402
from services import long_short_rules as R  # noqa: E402
from services import order_sheet as OSH  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt, register_trade_adapter  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "rep13verify"
PREFIX = "ZZR13"
NAME_PREFIX = "REP13 VERIFY"
CAP_PREFIX = "zzr13"
REF_PROVIDER = "zz_rep13_test"
CIK_PAIR = "9913000001"           # the two share classes' shared (fixture) issuer CIK
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
ZERO = Decimal(0)
TRADING_COST_BPS = Decimal("10")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001d{n:04d}")


FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000001d"
ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN1, U_ADMIN2, U_VIEW, U_ADMIN_B, U_ADMIN_B2 = (_id(110 + i) for i in range(6))
# uid → (org, users.role (the mirror), short name, role granted in user_roles, full name)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP13 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP13 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP13 Admin Checker"),
    U_VIEW: (ORG_A, "member", "view", "rep13_viewer", "REP13 Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP13 Org B Admin"),
    U_ADMIN_B2: (ORG_B, "org_admin", "adminb2", "org_admin", "REP13 Org B Checker"),
}
VIEWER_PERMS = ("view_proposals", "view_tax")
ENT_A, ENT_A2, ENT_B = _id(200), _id(201), _id(202)
HH_A, HH_B = _id(210), _id(211)
# account → (org, custodian_code, household, primary entity, held away, cash)
ACC_LS, ACC_LS2, ACC_H, ACC_M, ACC_ND, ACC_ADV, ACC_HOLD, ACC_HA, ACC_B = (_id(301 + i) for i in range(9))
ACCOUNTS = {
    ACC_LS: (ORG_A, "ZZR13LS", HH_A, ENT_A, False, 100000),
    ACC_LS2: (ORG_A, "ZZR13LS", HH_A, ENT_A, False, 100000),
    ACC_H: (ORG_A, "ZZR13LS", HH_A, ENT_A, False, 100000),
    ACC_M: (ORG_A, "ZZR13MA", HH_A, ENT_A, False, 100000),
    ACC_ND: (ORG_A, "ZZR13ND", HH_A, ENT_A, False, 100000),
    ACC_ADV: (ORG_A, "ZZR13LS", HH_A, ENT_A, False, 100000),
    ACC_HOLD: (ORG_A, "ZZR13LS", HH_A, ENT_A2, False, 1000),
    ACC_HA: (ORG_A, "ZZR13HA", HH_A, ENT_A2, True, 0),
    ACC_B: (ORG_B, "ZZR13LS", HH_B, ENT_B, False, 100000),
}
CAPS = {   # fixture custodian route → capability rows (platform)
    "zzr13ls": {"margin": "yes", "short_selling": "yes", "stock_borrow": "yes", "margin_initial_pct": "0.5000",
                "margin_maintenance_pct": "0.3000", "fractional_shares": "no"},
    "zzr13ma": {"margin": "yes", "short_selling": "yes", "stock_borrow": "yes", "fractional_shares": "no"},
    "zzr13nd": {"margin": "yes", "short_selling": "no", "stock_borrow": "no", "fractional_shares": "no"},
    "zzr13ha": {"margin": "no", "short_selling": "no", "stock_borrow": "no"},
}
SEC_KEYS = ("LA", "LB", "SC", "SD", "SE", "SF", "G1", "H2", "CA", "CB", "LS1", "XS", "FND", "TA", "TB")
SEC_NAMES = {k: f"{NAME_PREFIX} {k}" for k in SEC_KEYS}
SEC_TYPES = {"FND": "fund"}
PRICES = {"LA": "100", "LB": "50", "SC": "40", "SD": "25", "SE": "20", "SF": "30", "G1": "50", "H2": "40",
          "CA": "60", "CB": "58", "LS1": "10", "XS": "20", "FND": "10", "TA": "10", "TB": "10"}
# held long positions (account, key, qty, market value, lot cost (total), acquired days ago)
HOLDINGS = (
    (ACC_HOLD, "G1", 100, 5000, 3000, 500),     # at a GAIN (cost 30 a share, price 50)
    (ACC_HOLD, "CA", 50, 3000, 2000, 500),      # at a GAIN — share class A of the CIK_PAIR issuer
    (ACC_HOLD, "LS1", 100, 1000, 1500, 200),    # at a LOSS (cost 15, price 10)
    (ACC_HA, "H2", 20, 800, 400, 700),          # held AWAY, at a GAIN
)
M1_ROWS = (("LA", 65), ("LB", 65), ("SC", -15), ("SD", -15))
# rep13 fix 1: rep3's duplicate guard (same org + same file sha256 as an upload a live model still references)
# answers 409 at confirm BEFORE validation runs — by design. Each refusal fixture that reuses M1's rows therefore
# lists them in its OWN order: the same holdings and sums (so exactly its rule fires), but distinct bytes.
M1_NEG_ROWS = M1_ROWS[1:] + M1_ROWS[:1]       # LB, SC, SD, LA — the long-only negative-weight case
M1_EXCL_ROWS = M1_ROWS[2:] + M1_ROWS[:2]      # SC, SD, LA, LB — the excluded-short case
M1_NAME_ROWS = M1_ROWS[3:] + M1_ROWS[:3]      # SD, LA, LB, SC — the "Holdings of …" name case
M2_ROWS = (("LA", 65), ("LB", 65), ("SE", -5), ("SF", -5), ("G1", -5), ("H2", -4), ("CB", -4), ("LS1", -7))
SETTINGS = {"extension": "130_30", "tracking_error_limit": "0.10", "max_short_per_name": "0.20",
            "borrow_cost_ceiling_bps": "500", "exclusions": []}
COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households",
    "public.household_memberships", "public.accounts", "public.account_balances_daily",
    "public.account_authority_settings", "public.documents", "public.org_settings", "portfolio.assets",
    "portfolio.positions", "portfolio.tax_lots", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "portfolio.security_reference_attributes",
    "public.custodian_capabilities", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.model_uploads", "public.account_model_links",
    "public.rebalance_proposals", "public.rebalance_proposal_trades", "public.execution_instructions",
    "public.execution_fills", "public.substitution_sets", "public.substitution_set_candidates",
    "public.suitability_records", "public.long_short_settings", "public.borrow_rates", "public.long_short_proposals",
)
#: the four new tables and the capability matrix: no real activity writes them, so they are counted WHOLE
WHOLE_COUNTED = ("public.suitability_records", "public.long_short_settings", "public.long_short_proposals",
                 "public.borrow_rates", "public.custodian_capabilities")
NEW_TABLES = ("public.suitability_records", "public.long_short_settings", "public.borrow_rates",
              "public.long_short_proposals")
SEED_CHECK_LITERALS = (
    ("public.custodian_capabilities", "custodian_capabilities_key_chk",
     ("margin", "short_selling", "stock_borrow", "margin_initial_pct", "margin_maintenance_pct", "fractional_shares")),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("assumed",)),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes", "no", "unknown")),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("ticker",)),
    ("portfolio.security_reference_attributes", "security_reference_attributes_field_chk", ("cik",)),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long", "short")),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
    ("portfolio.models", "models_kind_chk", ("long_only", "long_short")),
    ("public.account_authority_settings", "account_authority_settings_authority_chk", ("advise",)),
    ("public.account_authority_settings", "account_authority_settings_custody_chk", ("integrated",)),
    ("public.account_authority_settings", "account_authority_settings_status_chk", ("approved",)),
    ("public.substitution_sets", "substitution_sets_status_chk", ("approved",)),
    ("public.substitution_set_candidates", "substitution_set_candidates_source_chk", ("adviser",)),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_side_chk", ("sell_short", "buy_to_cover")),
    ("public.execution_instructions", "execution_instructions_side_chk", ("sell_short", "buy_to_cover")),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("broker",)),
    ("public.long_short_settings", "long_short_settings_extension_chk", ("130_30",)),
    ("public.suitability_records", "suitability_records_kind_chk", ("margin_agreement",)),
)
UNIQUES = ("custodian_capabilities_live_uq", "suitability_records_live_uq", "long_short_settings_live_uq",
           "long_short_settings_version_uq", "borrow_rates_live_uq", "long_short_proposals_live_uq",
           "substitution_sets_approved_uq", "substitution_set_candidates_rank_uq",
           "security_reference_attributes_live_uq", "account_authority_settings_live_uq")
FK_TABLES = ("public.suitability_records", "public.long_short_settings", "public.borrow_rates",
             "public.long_short_proposals")

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
BLOBS: dict[str, bytes] = {}
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
        return None, exc.stdout or "", f"timeout after {timeout}s"


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def D(v) -> Decimal:
    return Decimal(str(v))


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(o) -> str:
    """Full body text for a failing check's detail (never truncated mid-assertion)."""
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def raw_of(acc) -> str:
    return f"ZZ13{int(str(acc)[-4:]):06d}"


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """The same org context the route sets (org GUC, not super admin)."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


def _arr(vals, typ="uuid") -> str:
    return "ARRAY[" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals) + f"]::{typ}[]"


def fixture_scope(table: str) -> str | None:
    """The fixture-row predicate of each counted table that real activity writes to (docs/VERIFY_RULES.md rule 6)."""
    orgs, users = _arr(FIXTURE_ORGS), _arr(USERS)
    names = _arr(SEC_NAMES.values(), "text")
    by_org = f"org_id = ANY({orgs})"
    secs = f"(SELECT id FROM portfolio.securities_global WHERE name = ANY({names}))"
    return {
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR resource_id::text LIKE "
                            f"'{FIXTURE_ID_PREFIX}%'",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.organizations": f"id = ANY({orgs})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({_arr([HH_A, HH_B])})",
        "portfolio.securities_global": f"name = ANY({names})",
        "portfolio.securities_global_identifiers": f"global_security_id IN {secs}",
        "portfolio.security_reference_attributes": f"global_security_id IN {secs} OR provider_key = '{REF_PROVIDER}'",
        "public.custodian_capabilities": None,
    }.get(table, by_org if table not in WHOLE_COUNTED else None)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED:
        where = fixture_scope(t)
        out[t] = int(await platform(conn, (f"SELECT count(*) FROM {t} WHERE {where}" if where else VSC.count_sql(t, FIXTURE_ORGS, list(USERS))),
                                    fetch="fetchval"))
    return out


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def pk_cols(conn, table) -> str:
    return await platform(conn, """SELECT string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_index i
                                   JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                                   WHERE i.indrelid = $1::regclass AND i.indisprimary""", table, fetch="fetchval")


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint must never touch, keyed by each table's real primary key. Fingerprinted WHOLE (no
    background job writes them): the capability matrix outside the fixture codes, and the four new tables outside
    the fixture orgs. The demo org's accounts (its structural columns — no real job edits them). Excluded and
    named: positions / tax lots / proposals / instructions (the nightly refresh and the live proposals tick write
    them at any time — covered by the fixture-scoped counts instead)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    pk = await pk_cols(conn, "public.custodian_capabilities")
    out["custodian_capabilities"] = await fingerprint(conn, f"SELECT {pk} AS k, custodian_code, capability_key, value, "
                                                            "version_no, valid_to FROM public.custodian_capabilities "
                                                            "WHERE custodian_code NOT LIKE $1", f"{CAP_PREFIX}%")
    for t in NEW_TABLES:
        pk = await pk_cols(conn, t)
        cond = "org_id IS NULL OR NOT (org_id = ANY($1::uuid[]))"
        if t == "public.borrow_rates":
            cond = "(org_id IS NULL OR NOT (org_id = ANY($1::uuid[]))) AND global_security_id NOT IN (SELECT id FROM " \
                   "portfolio.securities_global WHERE name LIKE '" + NAME_PREFIX + "%')"
        out[t.split(".")[1]] = await fingerprint(conn, f"SELECT {pk} AS k, * FROM {t} WHERE {cond}", orgs)
    demo = await platform(conn, "SELECT id FROM organizations WHERE slug = 'demo-olivo'", fetch="fetchval")
    S["demo_org"] = demo
    if demo is not None:
        out["demo_olivo_accounts"] = await fingerprint(
            conn, "SELECT id, custodian_code, household_id, is_held_away, tax_status FROM public.accounts WHERE "
                  "org_id = $1", demo)
    out["real_long_short_models"] = await fingerprint(
        conn, "SELECT id FROM portfolio.models WHERE kind = 'long_short' AND NOT (org_id = ANY($1::uuid[]))", orgs)
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
        secs = [r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                                f"{NAME_PREFIX}%")]
        secs += [r["global_security_id"] for r in await op.fetch(
            "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
            f"{PREFIX}%") if r["global_security_id"] not in secs]
        orgs, users = FIXTURE_ORGS, list(USERS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),     # SECMASTER2 FIX 1: by fixture user
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH_A, HH_B]]),
            "public.borrow_rates": ("global_security_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])", [secs, orgs]),
            "public.custodian_capabilities": ("custodian_code LIKE $1", [f"{CAP_PREFIX}%"]),
            "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[]) OR provider_key = $2",
                                                        [secs, REF_PROVIDER]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_rep13")
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
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.security_reference_attributes WHERE provider_key = $1", [REF_PROVIDER]),
        ("SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE $1", [f"{CAP_PREFIX}%"]),
        ("SELECT count(*) FROM public.borrow_rates r JOIN portfolio.securities_global g ON g.id = r.global_security_id "
         "WHERE g.name LIKE $1", [f"{NAME_PREFIX}%"]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
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


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api_raw(uid, method, path, body=None, files=None, form=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep13"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
        kw["data"] = form or {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b""
    return res.status_code, res.content


async def api(uid, method, path, body=None, timeout=300.0):
    code, content = await api_raw(uid, method, path, body, timeout=timeout)
    try:
        return code, json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        return code, {"raw": content[:2000].decode("utf-8", "replace")}


class _ctx:
    """The task's RLS contextvars. ENTER IT BEFORE ``pool.acquire()``: the wrapped pool applies the GUCs from these
    contextvars once, at acquire — entered after, the transaction carries app.current_org_id = '' (rep13 fix 1)."""

    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


def _fixture_adapter(name: str, capability_custodian: str | None):
    """A fixture trade adapter: like Altruist's, it routes to manual instructions (no trading client exists)."""
    class _A(TR.TradeAdapter):
        async def submit(self, ctx, trade_list, *, reason=None, notes=None):
            res = await TR.ManualTradeAdapter().submit(ctx, trade_list, reason=f"REP13 fixture adapter {name}: no "
                                                       "trading client — manual instructions", notes=notes)
            res.adapter = name
            return res

        async def status(self, ctx, submission):
            return await TR.ManualTradeAdapter().status(ctx, submission)

        async def cancel(self, ctx, submission, reason):
            return await TR.ManualTradeAdapter().cancel(ctx, submission, reason)

    _A.__name__ = f"Rep13FixtureAdapter_{name}"
    _A.capability_custodian = capability_custodian
    return _A


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def put(key, data, content_type=None, bucket=None):
        BLOBS[key] = bytes(data)
        return key

    def get(key, bucket=None):
        return BLOBS[key]

    storage.upload_bytes = put
    storage.download_bytes = get

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep13: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_rep13: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))

    # security-data reads for unheld securities → the fixture prices (CURRENT signature of _unheld_prices)
    async def unheld_prices(conn, keys, since, *, org_id, today=None, provenance=None):
        out = {}
        for k in keys:
            key = S.get("key_of", {}).get(str(k))
            if key and key in S.get("prices", {}):
                out[str(k)] = (D(S["prices"][key]).quantize(Decimal("0.00000001")), today or date.today(),
                               "security_price")
        return out

    RP._unheld_prices = unheld_prices

    # the tracking error's price history: empty by default ("needs prices"); a fixture series when set
    async def history(conn, org_id, keys, today_, window_days):
        series = S.get("series") or {}
        return {k: series[k] for k in keys if k in series}, {}

    LS.history_reader = history
    for code, cap in (("zzr13ls", None), ("zzr13nd", None), ("zzr13ma", "zzr13nd")):
        register_trade_adapter(code, _fixture_adapter(code, cap))


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1f")
    rows = await platform(conn, "SELECT custodian_code, capability_key, value FROM public.custodian_capabilities WHERE "
                                "valid_to IS NULL AND system_to IS NULL AND capability_key IN ('margin', "
                                "'short_selling', 'stock_borrow') AND custodian_code NOT LIKE $1", f"{CAP_PREFIX}%")
    declared = [dict(r) for r in rows if r["value"] == "yes"]
    check(bool(rows) and not declared, "1a: margin / short_selling (keys since rep2) and stock_borrow (rep13) are "
          "declarable per custodian; NO stored custodian declares any of them", f"{len(rows)} rows; {declared}")
    stored = sorted({r["custodian_code"] for r in rows})
    find("1a stored custodians", f"{stored} — Schwab, Fidelity, Pershing and IBKR have NO capability rows at all "
         "(capability() reads a missing row as 'unknown', i.e. not declared)")
    src = (API_DIR / "services" / "model_upload.py").read_text(encoding="utf-8")
    check("not supported yet" not in src and "only a long/short model" in src,
          "1b: rep3's 'long/short models are not supported yet' was a placeholder; negative weights are now refused "
          "only on a long-only model")
    d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                             "'rebalance_proposal_trades_side_chk'", fetch="fetchval")
    check("sell_short" in (d or "") and "buy_to_cover" in (d or ""), "1c / 1f: the side vocabulary gains sell_short / "
          "buy_to_cover (proposal trades, instructions, blocks); a long book never sells shares it does not hold",
          d or "")
    check("short_close_term" in (API_DIR / "services" / "long_short_rules.py").read_text(encoding="utf-8"),
          "1d: the term of a short close is decided in one place (IRC §1233 — always short-term)")
    n = await platform(conn, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'suitability_records'",
                       fetch="fetchval")
    check(int(n) == 1, "1e: before rep13 nothing recorded a margin agreement; public.suitability_records now does")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation, the connection does not bypass RLS")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep13.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep13.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-300:] if code else ''}")
    for t in NEW_TABLES:
        sch, name = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", sch, name)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role is not None and role["rolbypassrls"] is False,
          "the verify's own connection does NOT bypass RLS (isolation checks are real)", f"{dict(role or {})}")


async def y_seed_literals(conn):
    section("[Y] CHECK / UNIQUE / FK constraints and unique indexes read from the catalog before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    uq = {r["indexname"] for r in await platform(conn, "SELECT indexname FROM pg_indexes WHERE indexname = "
                                                       "ANY($1::text[]) AND indexdef LIKE 'CREATE UNIQUE%'",
                                                 list(UNIQUES))}
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                                   "ANY($1::regclass[])", list(FK_TABLES), fetch="fetchval"))
    check(not bad and uq == set(UNIQUES) and fks >= 12,
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints read); the "
          f"{len(UNIQUES)} UNIQUE indexes and the FKs the seeds rely on are in the catalog",
          "; ".join(bad) + f" uq_missing={sorted(set(UNIQUES) - uq)} fks={fks}")
    S["literals_ok"] = not bad and uq == set(UNIQUES)


async def guard_prefix(conn) -> bool:
    vals = []
    for sql, arg in (("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", f"{PREFIX}%"),
                     ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%"),
                     ("SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code LIKE $1",
                      f"{CAP_PREFIX}%"),
                     ("SELECT count(*) FROM portfolio.security_reference_attributes WHERE provider_key = $1 OR "
                      "(field = 'cik' AND ltrim(value, '0') = $1)", REF_PROVIDER),
                     ("SELECT count(*) FROM portfolio.security_reference_attributes WHERE field = 'cik' AND "
                      "ltrim(value, '0') = $1", CIK_PAIR),
                     ("SELECT count(*) FROM public.accounts WHERE upper(custodian_code) LIKE $1", f"{PREFIX}%")):
        vals.append(int(await platform(conn, sql, arg, fetch="fetchval")))
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) OR id::text LIKE $2",
                           FIXTURE_ORGS, f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    return check(all(v == 0 for v in vals) and o == 0 and u == 0,
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', {CAP_PREFIX}…, {REF_PROVIDER}, CIK {CIK_PAIR}, the "
                 "fixture id range) match no real row", f"{vals} orgs={o} users={u}")


async def check_fixture_roles(conn) -> bool:
    """Each fixture user's role AS THE APP DECIDES IT (user_roles → rbac.load_principal and the services.authz
    Principal), users.role its mirror (secmaster2 fix 1's seed-time check)."""
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
            perms = set(getattr(pr, "permissions", ()) or ())
            want_perms = set(VIEWER_PERMS) if rname == "rep13_viewer" else (
                {"view_proposals", "manage_proposals", "approve_proposals"} if rname == "org_admin" else set())
            ok = (rname in names and rbac.mirrored_role_string(names) == account_role
                  and rbac.is_super_admin(lp) is want_super and bool(pr and pr.is_super_admin) is want_super
                  and (want_super or want_perms <= perms)
                  and (rname != "rep13_viewer" or "manage_proposals" not in perms))
            if not ok:
                bad.append((str(uid), names, account_role, bool(pr and pr.is_super_admin), sorted(perms)[:8]))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (Super Admin, org admins "
                 "with view / manage / approve_proposals, the viewer view-only), users.role its mirror", f"{bad}")


async def seed(conn) -> None:
    S["seed_day"] = today()
    S["sec"], S["key_of"], S["prices"], S["lots"] = {}, {}, dict(PRICES), {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP13 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid = await rbac.ensure_role(conn, ORG_A, "rep13_viewer", "REP13 verify — view_proposals / view_tax only")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                               "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        for uid, (org, account_role, _nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await rbac.grant_named_role(conn, uid, org, rname)
        for ent, org, nm in ((ENT_A, ORG_A, "Spouse One"), (ENT_A2, ORG_A, "Spouse Two"), (ENT_B, ORG_B, "Org B")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"REP13 Verify {nm}")
        for hh, org, ents, maker in ((HH_A, ORG_A, (ENT_A, ENT_A2), U_ADMIN1), (HH_B, ORG_B, (ENT_B,), U_ADMIN_B)):
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                               hh, org, f"REP13 Verify Household {str(hh)[-2:]}", maker)
            for ent in ents:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, "
                                   "$2, $3)", hh, ent, maker)
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES.get(key, "equity"),
                                           currency_code="USD", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S["sec"][key] = UUID(str(sid))
            S["key_of"][str(sid)] = key
        for key in ("CA", "CB"):
            await conn.execute(
                "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, provider_key, "
                "as_of) VALUES ($1, 'cik', $2, $3, now())", S["sec"][key], CIK_PAIR, REF_PROVIDER)
        for code, caps in CAPS.items():
            for k, v in caps.items():
                await conn.execute(
                    "INSERT INTO public.custodian_capabilities (custodian_code, capability_key, value, evidence_kind, "
                    "evidence, as_of, version_no) VALUES ($1, $2, $3, 'assumed', 'REP13 verify fixture', $4, 1)",
                    code, k, v, S["seed_day"])
        salts = {}
        for org in FIXTURE_ORGS:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for acc, (org, code, hh, ent, held_away, cash) in ACCOUNTS.items():
            n = AccountNumber(raw_of(acc))
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, true, $8)",
                acc, org, n.masked, n.hashed(salts[org]), code, ent, hh, held_away)
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", org, acc,
                S["seed_day"], D(cash))
        S["masked"] = {a: AccountNumber(raw_of(a)).masked for a in ACCOUNTS}
        # the advise account: an approved account-level authority (advise, integrated) — maker ≠ checker
        await conn.execute(
            "INSERT INTO public.account_authority_settings (org_id, account_id, authority_override, custody, "
            "effective_from, reason, status, maker_id, approved_by, decided_at) VALUES ($1, $2, 'advise', "
            "'integrated', $3, 'REP13 verify — advise fixture', 'approved', $4, $5, now())",
            ORG_A, ACC_ADV, S["seed_day"] - timedelta(days=1), U_ADMIN1, U_ADMIN2)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, $3::jsonb)",
                           ORG_A, HV.COST_SETTING, json.dumps(int(TRADING_COST_BPS)))
        for acc, key, qty, mv, cost, days in HOLDINGS:
            org, ent = ACCOUNTS[acc][0], ACCOUNTS[acc][3]
            aid, pid, lid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", aid, org, S["sec"][key], SEC_NAMES[key])
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, org, ent, aid, S["seed_day"], D(qty), D(mv), acc)
            await conn.execute(
                "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                "original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, 'open')", lid, org, pid,
                S["seed_day"] - timedelta(days=days), D(qty), D(cost), "long" if days > 365 else "short")
            S["lots"][(acc, key)] = str(lid)
        # an APPROVED substitution set for SC → XS (a rep6 compliance artifact; maker ≠ checker)
        set_id = await conn.fetchval(
            "INSERT INTO public.substitution_sets (org_id, global_security_id, version_no, status, maker_id, checker_id, "
            "decided_at, notes) VALUES ($1, $2, 1, 'approved', $3, $4, now(), 'REP13 verify fixture') RETURNING id",
            ORG_A, S["sec"]["SC"], U_ADMIN1, U_ADMIN2)
        await conn.execute(
            "INSERT INTO public.substitution_set_candidates (org_id, set_id, rank, replacement_security_id, reason, "
            "evidence) VALUES ($1, $2, 1, $3, 'REP13 verify — a different issuer in the same sector', "
            "'REP13 verify fixture')", ORG_A, set_id, S["sec"]["XS"])
        S["doc"] = await conn.fetchval(
            "INSERT INTO public.documents (org_id, entity_id, original_filename, source, status, created_by) VALUES "
            "($1, $2, 'REP13 VERIFY margin agreement.pdf', 'upload', 'dropped', $3) RETURNING id", ORG_A, ENT_A,
            U_ADMIN1)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          list(USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true",
          f"{len(us)}/{len(USERS)} users, {len(os_)}/2 orgs")
    S["seeded"] = await check_fixture_roles(conn) and us == set(USERS) and os_ == set(FIXTURE_ORGS)


def acct(a) -> str:
    return str(a)


async def eligibility(conn, a) -> dict:
    code, out = await api(U_ADMIN1, "GET", f"{_L}/accounts/{acct(a)}")
    S.setdefault("panels", {})[a] = out
    return (out or {}).get("eligibility") or {}


def settings_body(**over) -> dict:
    return {**SETTINGS, **over}


# ── Task 2: capabilities, suitability, authority ───────────────────────────
async def y_capabilities(conn):
    section("[Y] Capabilities + suitability + authority gate long/short; each refusal names the missing piece")
    el = await eligibility(conn, ACC_ND)
    miss = " ".join(el.get("missing") or [])
    check(el.get("eligible") is False and "short selling" in miss and "stock borrow" in miss and "margin agreement" in
          miss and "does not declare margin" not in miss,
          "a custodian that does not declare short selling / stock borrow refuses, naming each missing piece (margin "
          "IS declared, so it is not named)", dumps(el))
    el = await eligibility(conn, ACC_LS)
    miss = el.get("missing") or []
    check(el.get("eligible") is False and len(miss) == 1 and "margin agreement" in miss[0],
          "a declaring custodian (margin + short_selling + stock_borrow) with managed authority: only the missing "
          "margin agreement refuses", dumps(el))
    code, out = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(ACC_LS)}/settings", settings_body())
    check(code == 409 and "margin agreement" in dumps(out), "saving settings (a VALID body) is refused while the "
          "account is not eligible — the refusal names the margin agreement", f"{code} {dumps(out)}")
    code, out = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(ACC_LS)}/margin-agreement",
                          {"agreement_date": (today() - timedelta(days=10)).isoformat(), "document_id": str(S["doc"])})
    rec = (out or {}).get("record") or {}
    check(code == 201 and rec.get("status") == "pending_approval", "the margin agreement is recorded (date + document) "
          "and awaits a second person", f"{code} {dumps(out)}")
    S["margin_id"] = rec.get("id")
    el = await eligibility(conn, ACC_LS)
    check(el.get("eligible") is False, "a PENDING agreement does not make the account eligible", dumps(el))
    code, out = await api(U_ADMIN1, "POST", f"{_L}/margin-agreements/{S['margin_id']}/approve", {})
    check(code == 403, "maker-checker: the person who recorded the agreement cannot approve it", f"{code} {dumps(out)}")
    code, out = await api(U_VIEW, "POST", f"{_L}/margin-agreements/{S['margin_id']}/approve", {})
    check(code == 403 and "approve_proposals" in dumps(out), "a view-only user cannot approve (403 naming "
          "approve_proposals)", f"{code} {dumps(out)}")
    code, out = await api(U_ADMIN2, "POST", f"{_L}/margin-agreements/{S['margin_id']}/approve", {})
    check(code == 200 and ((out or {}).get("record") or {}).get("status") == "approved",
          "a different holder of approve_proposals approves it", f"{code} {dumps(out)}")
    hist = await scoped(conn, ORG_A, "SELECT status, system_to IS NOT NULL AS archived FROM public.suitability_records "
                                     "WHERE subject_id = $1 ORDER BY system_from", HH_A)
    aud = await scoped(conn, ORG_A, "SELECT action FROM audit_log WHERE org_id = $1 AND action LIKE "
                                    "'long_short.margin_agreement_%'", ORG_A)
    check([dict(h) for h in hist] == [{"status": "pending_approval", "archived": True},
                                      {"status": "approved", "archived": False}]
          and {"long_short.margin_agreement_recorded", "long_short.margin_agreement_approved"} <= {a["action"] for a in aud},
          "the record is audited, and its approval archived the pending version (system axis, id kept)",
          f"{[dict(h) for h in hist]} {[a['action'] for a in aud]}")
    el = await eligibility(conn, ACC_LS)
    check(el.get("eligible") is True and all(p.get("ok") is True for p in el.get("pieces") or []),
          "with the agreement approved the account is eligible (every piece true)", dumps(el))
    el = await eligibility(conn, ACC_ADV)
    miss = el.get("missing") or []
    check(el.get("eligible") is False and len(miss) == 1 and "advise" in miss[0],
          "authority ADVISE refuses (the same household's agreement applies), naming the authority", dumps(el))
    el = await eligibility(conn, ACC_HA)
    miss = " ".join(el.get("missing") or [])
    check(el.get("eligible") is False and "read-only" in miss and "held away" in miss,
          "authority READ-ONLY (a held-away account) refuses, naming read-only and held away", dumps(el))
    # capability edits: Super Admin only, versioned with history
    body = {"value": "no", "evidence_kind": "assumed", "evidence": "REP13 verify — re-recorded",
            "as_of": today().isoformat()}
    code, out = await api(U_ADMIN1, "PUT", f"/api/v1/custodians/capabilities/{CAP_PREFIX}nd/stock_borrow", body)
    check(code == 403, "an org admin cannot edit a custodian capability (a VALID body)", f"{code} {dumps(out)}")
    code, out = await api(U_SUPER, "PUT", f"/api/v1/custodians/capabilities/{CAP_PREFIX}nd/stock_borrow", body)
    code2, hist = await api(U_SUPER, "GET", f"/api/v1/custodians/capabilities/{CAP_PREFIX}nd/stock_borrow/history")
    rows = hist.get("history") or hist.get("rows") or (hist if isinstance(hist, list) else [])
    check(code == 200 and code2 == 200 and [r.get("version_no") for r in rows] == [2, 1],
          "Super Admin records a new version of stock_borrow; the history keeps both", f"{code} {code2} {dumps(hist)}")


# ── Task 3: models ─────────────────────────────────────────────────────────
HEADER = ["Ticker", "Weight %"]


def csv_bytes(rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEADER)
    for key, wt in rows:
        w.writerow([key if key.startswith(PREFIX) else f"{PREFIX}{key}", wt])
    return buf.getvalue().encode("utf-8")


def ls_spec(**over) -> dict:
    return {"extension": "130_30", "signal_source": "REP13 Verify Sub-Adviser", "signal_as_of":
            (today() - timedelta(days=2)).isoformat(), **over}


async def upload_confirm(name: str, rows, model_name: str, long_short: dict | None) -> tuple[int, dict, str | None]:
    code, content = await api_raw(U_ADMIN1, "POST", "/api/v1/models/uploads",
                                  files={"file": (name, csv_bytes(rows), "text/csv")}, form={})
    try:
        up = json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        up = {}
    if code != 201 or not (up.get("upload") or {}).get("id"):
        return code, up, None
    uid = up["upload"]["id"]
    body = {"model_name": model_name}
    if long_short is not None:
        body["long_short"] = long_short
    c2, o2 = await api(U_ADMIN1, "POST", f"/api/v1/models/uploads/{uid}/confirm", body)
    return c2, o2, uid


def codes_of(out) -> set:
    det = (out or {}).get("detail")
    ms = det.get("messages", []) if isinstance(det, dict) else []
    return {m.get("code") for m in ms}


async def approve_model(model_id, version_id) -> tuple[int, dict]:
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/models/{model_id}/versions/{version_id}/submit", {})
    if c1 != 200:
        return c1, o1
    return await api(U_ADMIN2, "POST", f"/api/v1/models/{model_id}/versions/{version_id}/approve", {})


async def y_models(conn):
    section("[Y] Long/short models validated (upload + submit); a long-only negative refused; each refusal named")
    c, o, up1 = await upload_confirm("rep13_m1.csv", M1_ROWS, "Holdings of REP13 Verify 130/30", ls_spec())
    m = (o or {}).get("model") or {}
    check(c == 201 and m.get("kind") == "long_short" and m.get("gross_long") == "1.30" and m.get("gross_short") ==
          "0.30" and m.get("signal_source") == "REP13 Verify Sub-Adviser",
          "a 130/30 fixture uploads as a long/short model (gross 1.30 / 0.30, net 100%; the signal source and its "
          "as-of recorded)", f"{c} {dumps(o)}")
    S["m1"], S["m1_version"] = m.get("id"), ((o or {}).get("version") or {}).get("id")
    pos = await scoped(conn, ORG_A, "SELECT global_security_id, target_weight FROM portfolio.model_version_positions "
                                    "WHERE version_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                       S["m1_version"]) if S["m1_version"] else []
    want = {S["sec"][k]: D(w) / 100 for k, w in M1_ROWS}
    got = {r["global_security_id"]: D(r["target_weight"]) for r in pos}
    check(got == want, "the stored weights are the signed fractions (two shorts at −15%)", f"{got}")
    shas = [hashlib.sha256(csv_bytes(r)).hexdigest() for r in (M1_ROWS, M1_NEG_ROWS, M1_EXCL_ROWS, M1_NAME_ROWS)]
    check(len(set(shas)) == 4 and all(sorted(r) == sorted(M1_ROWS) for r in (M1_NEG_ROWS, M1_EXCL_ROWS, M1_NAME_ROWS)),
          "the three refusal fixtures hold M1's exact rows but are not M1's bytes (rep3's duplicate guard precedes "
          "validation, so a byte-identical file would be answered 409 'duplicate', never validated)", f"{shas}")
    c, o, _ = await upload_confirm("rep13_neg.csv", M1_NEG_ROWS, "REP13 Verify Long Only", None)
    check(c == 422 and "weight_negative" in codes_of(o), "a LONG-ONLY model with a negative weight is refused "
          "(weight_negative, with its row)", f"{c} {dumps(o)}")
    cases = (
        ("gross mismatch", (("LA", 65), ("LB", 70), ("SC", -20), ("SD", -15)), ls_spec(), {"ls_gross_long",
                                                                                         "ls_gross_short"}),
        ("a name both long and short", (("LA", 65), ("LB", 65), ("SC", -15), ("LA", -15)), ls_spec(),
         {"ls_both_sides"}),
        ("an excluded short", M1_EXCL_ROWS, ls_spec(short_exclusions=[str(S["sec"]["SC"])]), {"ls_short_excluded"}),
        ("a non-equity short", (("LA", 65), ("LB", 65), ("SC", -15), ("FND", -15)), ls_spec(), {"ls_short_not_equity"}),
    )
    for i, (what, rows, spec, want_codes) in enumerate(cases):
        c, o, upid = await upload_confirm(f"rep13_bad{i}.csv", rows, f"Holdings of REP13 Verify Bad {i}", spec)
        check(c == 422 and want_codes <= codes_of(o), f"{what}: refused with its reason ({sorted(want_codes)})",
              f"{c} {dumps(o)}")
    c, o, _ = await upload_confirm("rep13_name.csv", M1_NAME_ROWS, "REP13 Verify No Prefix", ls_spec())
    check(c == 422 and "Holdings of" in dumps(o), "a long/short model is named \"Holdings of …\" (refused otherwise)",
          f"{c} {dumps(o)}")
    code, log = await api(U_ADMIN1, "GET", "/api/v1/models/uploads")
    rows = (log or {}).get("rows") or (log or {}).get("uploads") or []
    mine = [r for r in rows if r.get("id") == up1]
    check(bool(mine) and mine[0].get("model_kind") == "long_short" and mine[0].get("model_kind_label") == "Long/short",
          "the upload log shows the model's kind", f"{code} {dumps(mine)}")
    rej = [r for r in rows if r.get("file_name", "").startswith("rep13_bad")]
    check(len(rej) == len(cases) and all(r.get("outcome") == "rejected" for r in rej),
          "every refused upload is logged 'rejected'", dumps([(r.get("file_name"), r.get("outcome")) for r in rej]))
    code, out = await approve_model(S["m1"], S["m1_version"])
    check(code == 200, "the 130/30 model is approved (maker submits, a different person approves)", f"{code} {dumps(out)}")
    c, o, _ = await upload_confirm("rep13_m2.csv", M2_ROWS, "Holdings of REP13 Verify Blocks", ls_spec())
    S["m2"], S["m2_version"] = ((o or {}).get("model") or {}).get("id"), ((o or {}).get("version") or {}).get("id")
    code, out = await approve_model(S["m2"], S["m2_version"]) if c == 201 else (c, o)
    check(c == 201 and code == 200, "the second 130/30 model (the gate fixture) uploads and is approved",
          f"{c} {code} {dumps(out)}")
    c, o, _ = await upload_confirm("rep13_lo.csv", (("LA", 50), ("LB", 50)), "REP13 Verify Long Only OK", None)
    lo_version = ((o or {}).get("version") or {}).get("id")
    check(c == 201 and ((o or {}).get("model") or {}).get("kind") == "long_only" and bool(lo_version),
          "a long-only upload still creates a long-only draft (rep3 unchanged)", f"{c} {dumps(o)}")
    if lo_version:
        try:
            await scoped(conn, ORG_A, "INSERT INTO portfolio.model_version_positions (org_id, version_id, row_num, "
                                      "global_security_id, raw_ticker, target_weight, resolution_state) VALUES ($1, "
                                      "$2::uuid, 99, $3, 'X', -0.01, 'resolved')", ORG_A, lo_version, S["sec"]["SC"],
                         fetch="execute")
            check(False, "the database refuses a negative weight on a long-only model's version", "the insert passed")
        except Exception as exc:  # noqa: BLE001
            check("long/short" in str(exc), "the database refuses a negative weight on a long-only model's version "
                  "(the trigger, below the service)", str(exc)[:200])


# ── Task 4: settings ───────────────────────────────────────────────────────
async def y_settings(conn):
    section("[Y] Account settings: draft → saved, versioned with history, audited; refusals with valid bodies")
    a = acct(ACC_LS)
    code, out = await api(U_ADMIN1, "PUT", f"{_L}/accounts/{a}/settings/draft", settings_body(tracking_error_limit="0.08"))
    check(code == 200 and ((out or {}).get("settings") or {}).get("status") == "draft", "a draft is saved",
          f"{code} {dumps(out)}")
    code, out = await api(U_ADMIN1, "POST", f"{_L}/accounts/{a}/settings", settings_body(tracking_error_limit="0.12"))
    v1 = (out or {}).get("settings") or {}
    code2, out2 = await api(U_ADMIN1, "POST", f"{_L}/accounts/{a}/settings", settings_body())
    v2 = (out2 or {}).get("settings") or {}
    check(code == 201 and code2 == 201 and v1.get("version_no") == 1 and v2.get("version_no") == 2,
          "saving twice records version 1 then version 2", f"{code} {code2} {dumps(out)} {dumps(out2)}")
    rows = await scoped(conn, ORG_A, "SELECT status, version_no, tracking_error_limit, valid_to IS NULL AS live FROM "
                                     "public.long_short_settings WHERE account_id = $1 ORDER BY valid_from", ACC_LS)
    check([(r["status"], r["version_no"], str(r["tracking_error_limit"]), r["live"]) for r in rows]
          == [("draft", 1, "0.08", False), ("saved", 1, "0.12", False), ("saved", 2, "0.10", True)],
          "history: the draft closed by the save, version 1 closed by version 2 — only ever closed, never edited",
          f"{[dict(r) for r in rows]}")
    aud = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action IN "
                                    "('long_short.settings_draft', 'long_short.settings_saved')", ORG_A,
                       fetch="fetchval")
    check(int(aud) == 3, "every settings write is audited (counted by the fixture org)", f"{aud}")
    code, out = await api(U_ADMIN1, "POST", f"{_L}/accounts/{a}/settings", settings_body(max_short_per_name="0.60"))
    check(code == 422, "a maximum short per name above 50% is refused", f"{code} {dumps(out)}")
    code, out = await api(U_ADMIN1, "POST", f"{_L}/accounts/{a}/settings", {**settings_body(),
                                                                            "tracking_error_limit": 0.1})
    check(code == 422, "a JSON float is refused (decimal strings only)", f"{code} {dumps(out)}")
    code, out = await api(U_VIEW, "POST", f"{_L}/accounts/{a}/settings", settings_body())
    check(code == 403 and "manage_proposals" in dumps(out), "a view-only user's VALID body is refused 403 naming "
          "manage_proposals", f"{code} {dumps(out)}")
    code, out = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(ACC_ND)}/settings", settings_body())
    check(code == 409 and "short selling" in dumps(out), "settings on an INELIGIBLE account are refused, naming the "
          "missing piece", f"{code} {dumps(out)}")
    for a2 in (ACC_LS2, ACC_H, ACC_M):
        c, o = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(a2)}/settings", settings_body())
        check(c == 201, f"settings saved for the fixture account {S['masked'][a2]}", f"{c} {dumps(o)}")


# ── joins ──────────────────────────────────────────────────────────────────
async def join(a, model_id, settings=None) -> dict:
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct(a)}/model/join",
                          {"model_id": str(model_id), "settings": settings or {}})
    link = (out or {}).get("link") or {}
    if code != 201 or not link.get("id"):
        return {"code": code, "out": out}
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct(a)}/model/changes/{link['id']}/approve", {})
    return {"code": c2, "out": o2, "link": link["id"]}


async def y_joins(conn):
    section("[Y] Accounts join the long/short models (eligible, settings saved with the same extension)")
    r = await join(ACC_ND, S["m1"])
    check(r["code"] == 409 and "long/short" in dumps(r.get("out")), "an INELIGIBLE account cannot join a long/short "
          "model (409, eligibility named)", f"{r['code']} {dumps(r.get('out'))}")
    for a, m, st in ((ACC_LS, S["m1"], None), (ACC_LS2, S["m2"], None),
                     (ACC_H, S["m1"], {"harvest_enabled": True, "harvest_loss_pct": "0.01", "harvest_loss_min": "1"}),
                     (ACC_M, S["m1"], None)):
        r = await join(a, m, st)
        check(r["code"] == 200, f"{S['masked'][a]} joins its long/short model (the join approved by a second person)",
              f"{r['code']} {dumps(r.get('out'))}")
        out = r.get("out") or {}
        S.setdefault("join_proposal", {})[a] = out.get("transition_proposal_id")
        if out.get("transition_proposal_error"):
            find(f"{S['masked'][a]} join proposal", out["transition_proposal_error"])
    pid = S["join_proposal"].get(ACC_LS)
    if pid:
        p = await scoped(conn, ORG_A, "SELECT status, trigger FROM public.rebalance_proposals WHERE id = $1::uuid AND "
                                      "system_to IS NULL", pid, fetch="fetchrow")
        check(p is not None and p["status"] == "draft" and p["trigger"] == "join",
              "the join's long/short proposal is a DRAFT (never auto-submitted: its maker acknowledges first)",
              f"{dict(p or {})}")


# ── borrow rates ───────────────────────────────────────────────────────────
async def rate(uid, key, bps, rebate=None, platform_scope=False, as_of=None):
    body = {"global_security_id": str(S["sec"][key]), "rate_bps": str(bps), "as_of": (as_of or today()).isoformat(),
            "source": "REP13 verify locate sheet"}
    if rebate is not None:
        body["rebate_bps"] = str(rebate)
    return await api(uid, "POST", f"{_L}/borrow-rates" + ("/platform" if platform_scope else ""), body)


async def y_borrow_rates(conn):
    section("[Y] Borrow rates: entered per security with an as-of; org first, else platform; Super Admin for platform")
    c, o = await rate(U_ADMIN1, "SD", 300, platform_scope=True)
    check(c == 403, "an org admin cannot enter a PLATFORM rate (a valid body)", f"{c} {dumps(o)}")
    c, o = await rate(U_SUPER, "SD", 300, platform_scope=True)
    check(c == 201 and ((o or {}).get("rate") or {}).get("scope") == "platform", "Super Admin enters a platform rate",
          f"{c} {dumps(o)}")
    ok = True
    for key, bps, reb in (("SC", 50, 10), ("SF", 900, None), ("G1", 100, None), ("H2", 100, None), ("CB", 100, None),
                          ("LS1", 100, None), ("XS", 80, None)):
        c, o = await rate(U_ADMIN1, key, bps, reb)
        ok = ok and c == 201
    check(ok, "org rates entered (SC with a 10 bps rebate; SF at 900 bps; SE deliberately has none)")
    c, o = await rate(U_ADMIN_B, "SC", 999)
    check(c == 201, "org B enters its own rate on SC", f"{c} {dumps(o)}")
    c, o = await rate(U_ADMIN1, "SC", "1.5e400")
    check(c == 422, "a non-finite rate is refused", f"{c}")
    with _ctx(ORG_A):  # the route's order: RLS context FIRST, then acquire (the pool applies the GUCs at acquire)
        async with (await get_pool()).acquire() as pc:
            got = await LS.borrow_rates_for(pc, ORG_A, [str(S["sec"][k]) for k in ("SC", "SD", "SE")], today())
    sc, sd = got.get(str(S["sec"]["SC"])), got.get(str(S["sec"]["SD"]))
    check(sc is not None and sc.rate_bps == 50 and sc.scope == "org" and sd is not None and sd.scope == "platform"
          and str(S["sec"]["SE"]) not in got,
          "org A reads its OWN SC rate (never org B's 999), the platform SD rate, and NO rate for SE",
          f"{[(k, v.rate_bps, v.scope) for k, v in got.items()]}")


# ── Task 5: the proposal ───────────────────────────────────────────────────
async def generate(a) -> tuple[int, dict]:
    return await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct(a)}/proposals", {"trigger": "manual"})


async def trades(conn, pid) -> list:
    return [dict(r) for r in await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposal_trades WHERE "
                                                       "proposal_id = $1::uuid AND valid_to IS NULL AND system_to IS "
                                                       "NULL ORDER BY line_no", pid)]


async def details(uid, pid) -> tuple[int, dict]:
    return await api(uid, "GET", f"{_L}/proposals/{pid}")


def hand_p1() -> dict:
    """The hand computation of the 130/30 proposal on $100,000 of cash, whole shares (fractional 'no'):
    LA 65,000 / 100 = 650; LB 65,000 / 50 = 1,300 (bought with the cash and the short proceeds);
    SC 15,000 / 40 = 375 short; SD 15,000 / 25 = 600 short. Borrow: SC 375 × 40 × 50 bps = $75.00, SD 600 × 25 ×
    300 bps = $450.00 → $525.00 a year; rebate SC 15,000 × 10 bps = $15.00. Traded $160,000 × 10 bps = $160.00;
    net $160 + $525 − $15 = $670.00. Margin 50% / 30% of $30,000 short = $15,000.00 / $9,000.00."""
    return {"trades": {"LA": ("buy", Decimal(650)), "LB": ("buy", Decimal(1300)), "SC": ("sell_short", Decimal(375)),
                       "SD": ("sell_short", Decimal(600))},
            "fees": {"SC": Decimal("75.00"), "SD": Decimal("450.00")}, "borrow_total": Decimal("525.00"),
            "rebate": Decimal("15.00"), "trading": Decimal("160.00"), "net": Decimal("670.00"),
            "margin_initial": "$15000.00", "margin_maintenance": "$9000.00"}


async def y_proposal_exact(conn):
    section("[Y] Proposal: both books exact; borrow per line and in the comparison; tracking error 'needs prices'")
    S["series"] = {}
    code, out = await generate(ACC_LS)
    p = (out or {}).get("proposal") or {}
    check(code == 201 and p.get("status") == "draft", "a person generates the long/short proposal (a draft)",
          f"{code} {dumps(out)}")
    S["p1"] = p.get("id")
    if not S["p1"]:
        return
    tr = await trades(conn, S["p1"])
    h = hand_p1()
    got = {S["key_of"].get(str(t["global_security_id"])): (t["side"], D(t["quantity"])) for t in tr}
    check(got == h["trades"], "the exact hand-computed long AND short trades (SELL SHORT 375 SC, 600 SD; BUY 650 LA, "
          "1,300 LB)", f"{got}")
    fees = {S["key_of"][str(t["global_security_id"])]: D(json.loads(t["short_leg"])["est_borrow_fee_annual"])
            for t in tr if t["side"] == "sell_short"}
    check(fees == h["fees"], "the estimated borrow fee per short line equals the hand computation", f"{fees}")
    check(all(t["side"] != "sell" for t in tr), "no SELL of shares not held (a short is a SELL SHORT)",
          f"{[t['side'] for t in tr]}")
    code, d = await details(U_ADMIN1, S["p1"])
    det = (d or {}).get("details") or {}
    cc = {x["key"]: x for x in det.get("cost_comparison") or []}
    check(code == 200 and D(cc.get("borrow_cost", {}).get("amount") or -1) == h["borrow_total"]
          and cc["borrow_cost"]["label"] == "Borrow cost (long/short only) — estimated"
          and D(cc.get("short_rebate", {}).get("amount") or -1) == h["rebate"]
          and D(cc.get("trading_cost", {}).get("amount") or -1) == h["trading"]
          and D(cc.get("net_cost", {}).get("amount") or -1) == h["net"],
          "the cost comparison: 'Borrow cost (long/short only) — estimated' $525.00, 'Short rebate' $15.00, trading "
          "$160.00, net $670.00 — the hand computation", dumps(det.get("cost_comparison")))
    te = det.get("tracking_error") or {}
    check(te.get("estimated") is False and str(te.get("text", "")).startswith("needs prices"),
          "with no price series the tracking error says 'needs prices' — never invented", dumps(te))
    check(det.get("blocking") == [], "nothing blocks this proposal", dumps(det.get("blocking")))
    S["p1_details"] = det


def _te_hand(final: dict, model: dict, series: dict) -> Decimal:
    keys = sorted(k for k in set(final) | set(model) if k != "cash")
    days = sorted(set.intersection(*[{d for d, _ in series[k]} for k in keys]))
    with localcontext() as ctx:
        ctx.prec = 50
        px = {k: dict(series[k]) for k in keys}
        port = []
        for i in range(1, len(days)):
            port.append(sum(((D(final.get(k, 0)) - D(model.get(k, 0))) * (px[k][days[i]] / px[k][days[i - 1]] - 1)
                             for k in keys), Decimal(0)))
        mean = sum(port, Decimal(0)) / len(port)
        var = sum(((x - mean) ** 2 for x in port), Decimal(0)) / (len(port) - 1)
        return (var * 252).sqrt()


async def y_tracking_error(conn):
    section("[Y] Tracking error computes the hand value from a fixture price series (signed weights)")
    start = today() - timedelta(days=200)
    series = {}
    for j, key in enumerate(("TA", "TB")):
        pts, p = [], Decimal("10")
        for i in range(80):
            p = p * (1 + Decimal(((i * (j + 3)) % 7) - 3) / Decimal(200))
            pts.append((start + timedelta(days=i), p))
        series[str(S["sec"][key])] = pts
    S["series"] = series
    final = {str(S["sec"]["TA"]): Decimal("1.30"), str(S["sec"]["TB"]): Decimal("-0.25"), "cash": Decimal("-0.05")}
    model = {str(S["sec"]["TA"]): Decimal("1.30"), str(S["sec"]["TB"]): Decimal("-0.30")}
    with _ctx(ORG_A):  # the route's order: RLS context FIRST, then acquire (the pool applies the GUCs at acquire)
        async with (await get_pool()).acquire() as pc:
            te = await LS.tracking_error(pc, ORG_A, final, model, today())
    hand = _te_hand(final, model, series)
    check(te.get("estimated") is True and abs(D(te["tracking_error"]) - hand) < Decimal("0.0000001"),
          "estimated tracking error = sqrt(252 × sample variance of the active return) — the hand value",
          f"{te.get('tracking_error')} vs {hand}")
    final_missing = {**final, str(S["sec"]["LA"]): Decimal("0.01")}
    with _ctx(ORG_A):  # the route's order: RLS context FIRST, then acquire (the pool applies the GUCs at acquire)
        async with (await get_pool()).acquire() as pc:
            te2 = await LS.tracking_error(pc, ORG_A, final_missing, model, today())
    check(te2.get("estimated") is False and "1 of 3" in te2.get("text", ""),
          "one name without a series → 'needs prices — 1 of 3 names …', not a partial estimate", dumps(te2))
    S["series"] = {}


async def y_gates_proposal(conn):
    section("[Y] Proposal gates: over the ceiling skipped; no borrow rate → 'borrow rate needed' blocks approval; "
            "§1259 hard block (another account, held away, another share class) — a loss does not block")
    code, out = await generate(ACC_LS2)
    p = (out or {}).get("proposal") or {}
    S["p2"] = p.get("id")
    check(code == 201 and bool(S["p2"]), "the gate fixture's proposal is generated (blocked lines are recorded, never "
          "routed)", f"{code} {dumps(out)}")
    if not S["p2"]:
        return
    tr = {S["key_of"].get(str(t["global_security_id"])): t for t in await trades(conn, S["p2"])}
    code, d = await details(U_ADMIN1, S["p2"])
    det = (d or {}).get("details") or {}
    sk = {S["key_of"].get(x["key"]): x for x in det.get("skipped") or []}
    check("SF" in sk and sk["SF"]["code"] == "over_ceiling" and "SF" not in tr and "900" in sk["SF"]["reason"],
          "SF (900 bps > the 500 bps ceiling) is skipped with the reason", dumps(det.get("skipped")))
    se = tr.get("SE")
    se_leg = json.loads(se["short_leg"]) if se else {}
    check(se is not None and "borrow_rate_needed" in se_leg.get("flags", []) and se_leg.get("borrow_rate_bps") is None
          and "borrow_rate_needed" in det.get("blocking", []),
          "SE (no rate on record) is a line flagged 'borrow rate needed' and blocks approval", dumps(se_leg))

    def wash(k):
        t = tr.get(k)
        return json.loads(t["wash_sale"]) if t and t.get("wash_sale") else {}

    g1, h2, cb, ls1 = wash("G1"), wash("H2"), wash("CB"), wash("LS1")
    g1m = (g1.get("matches") or [{}])[0]
    check(g1.get("outcome") == "blocked" and g1.get("rule") == "1259" and g1m.get("account") == S["masked"][ACC_HOLD]
          and g1m.get("lot_id") == S["lots"][(ACC_HOLD, "G1")] and "1259" in g1.get("reason", ""),
          "G1 held long at a gain in ANOTHER household account: BLOCKED (§1259), naming that account and its lot",
          dumps(g1))
    h2m = (h2.get("matches") or [{}])[0]
    check(h2.get("outcome") == "blocked" and h2m.get("held_away") is True and h2m.get("account") ==
          S["masked"][ACC_HA], "H2 held at a gain in a readable HELD-AWAY account: blocked, naming it", dumps(h2))
    cbm = (cb.get("matches") or [{}])[0]
    check(cb.get("outcome") == "blocked" and cbm.get("security_id") == str(S["sec"]["CA"])
          and "share class" in cbm.get("identical_because", "") and CIK_PAIR.lstrip("0") in cbm.get("identical_because", ""),
          "CB, ANOTHER SHARE CLASS (same CIK) of CA held at a gain: blocked, naming CA's lot and the rule", dumps(cb))
    check(ls1.get("outcome") == "clear", "LS1, held long at a LOSS, does not block its short", dumps(ls1))
    check("constructive_sale" in det.get("blocking", []), "the §1259 block is a blocking code on the proposal",
          dumps(det.get("blocking")))
    # no bypass: acknowledge (an override field is refused), submit, approve → refused; a manual SELL SHORT refused
    items = [x["key"] for x in det.get("acknowledgement_items") or []]
    c, o = await api(U_ADMIN1, "POST", f"{_L}/proposals/{S['p2']}/acknowledge", {"items": items,
                                                                                  "override_constructive_sale": True})
    check(c == 422, "there is no override: an 'override' field is refused by the request model", f"{c} {dumps(o)}")
    c1, _ = await api(U_ADMIN1, "POST", f"{_L}/proposals/{S['p2']}/acknowledge", {"items": items})
    c2, _ = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{S['p2']}/submit")
    c3, o3 = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{S['p2']}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS "
                                   "NULL", S["p2"], fetch="fetchval")
    check(c1 == 200 and c2 == 200 and c3 == 409 and "borrow rate" in dumps(o3) and "1259" in dumps(o3)
          and st == "pending_approval",
          "approval is REFUSED (borrow rate needed; §1259) and the proposal stays awaiting approval",
          f"{c1} {c2} {c3} {dumps(o3)} {st}")
    tl = {"account_id": acct(ACC_LS2), "executor_type": "broker", "executor_name": "REP13 Verify Broker",
          "execute_by": (today() + timedelta(days=3)).isoformat(),
          "trades": [{"global_security_id": str(S["sec"]["G1"]), "side": "sell_short", "quantity": "10",
                      "short_terms": {"position_effect": "open"}}]}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", tl)
    check(c == 409 and "1259" in dumps(o) and S["masked"][ACC_HOLD] in dumps(o),
          "a MANUAL SELL SHORT instruction of G1 is refused the same way (the one chokepoint), naming the account",
          f"{c} {dumps(o)}")
    c, o = await rate(U_ADMIN1, "SE", 120)
    code, out = await generate(ACC_LS2)
    p3 = ((out or {}).get("proposal") or {}).get("id")
    c4, d4 = await details(U_ADMIN1, p3) if p3 else (0, {})
    blk = ((d4 or {}).get("details") or {}).get("blocking")
    check(code == 201 and blk == ["constructive_sale"], "with SE's rate entered and the proposal rebuilt, the §1259 "
          "block alone remains — it never lapses on its own", f"{code} {dumps(blk)}")
    S["p2"] = p3
    with _ctx(ORG_A):  # the route's order: RLS context FIRST, then acquire (the pool applies the GUCs at acquire)
        async with (await get_pool()).acquire() as pc:
            a = await AM.load_account(pc, ORG_A, ACC_LS2)
            blocks, read = await LS.constructive_sale_check(pc, ORG_A, a, [str(S["sec"]["LS1"])], today())
    check(blocks == {} and any(r.get("held_away") for r in read if r.get("read")),
          "the household read covers every account, the held-away one included; LS1 (a loss) has no block",
          f"{blocks} {read}")
    # exclusions in the account's settings: a name never shorted
    c, o = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(ACC_LS2)}/settings",
                     settings_body(exclusions=[{"global_security_id": str(S["sec"]["LS1"]),
                                                "reason": "REP13 verify — the member's employer"}]))
    code, out = await generate(ACC_LS2)
    p4 = ((out or {}).get("proposal") or {}).get("id")
    c5, d5 = await details(U_ADMIN1, p4) if p4 else (0, {})
    sk = {S["key_of"].get(x["key"]): x["code"] for x in ((d5 or {}).get("details") or {}).get("skipped") or []}
    check(c == 201 and sk.get("LS1") == "excluded", "an account exclusion: LS1 is never shorted (skipped, reason "
          "stated)", f"{c} {sk}")


# ── Task 7: disclosures, acknowledgement, maker-checker, lapsed eligibility ─
async def y_disclosures(conn):
    section("[Y] Disclosures shown; submit refused without acknowledgement; maker cannot approve; approval refused "
            "after the margin agreement is removed")
    det = S.get("p1_details") or {}
    h = hand_p1()
    disc = det.get("disclosures") or {}
    risks = [r.get("key") for r in disc.get("risks") or []]
    m = det.get("margin") or {}
    # DISCLOSURES1: the risks come from the disclosure library (long_short.risks in force for the account) — the
    # built-in six when none is in force; a published version may add risks (margin / short-sale) but keeps AQR's six
    lib = disc.get("library") or {}
    check(bool(set(R.RISK_KEYS) <= set(risks) and len(risks) == len(set(risks))
               and (risks == list(R.RISK_KEYS) if lib.get("source") != "library" else True)),
          "the six risks (AQR's list) are shown — exactly the built-in six unless a library version is in force",
          f"{dumps(risks)} source {lib.get('source')}")
    check("525.00" in (disc.get("borrow_estimate") or {}).get("text", "") and h["margin_initial"] in
          m.get("initial", {}).get("text", "") and h["margin_maintenance"] in m.get("maintenance", {}).get("text", ""),
          "the borrow-cost estimate and the margin requirement AS DECLARED by the custodian (50% / 30% of the short "
          "value)", dumps(disc) + dumps(m))
    check(bool(det.get("member_summary")) and "short" in det["member_summary"], "a member-facing summary is shown",
          det.get("member_summary", ""))
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{S['p1']}/submit")
    check(c == 409 and "acknowledge" in dumps(o), "submit is refused without the acknowledgement", f"{c} {dumps(o)}")
    items = [x["key"] for x in det.get("acknowledgement_items") or []]
    c, o = await api(U_ADMIN2, "POST", f"{_L}/proposals/{S['p1']}/acknowledge", {"items": items})
    check(c == 403, "only the proposal's maker acknowledges", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_L}/proposals/{S['p1']}/acknowledge", {"items": items[:-1]})
    check(c == 422, "a partial acknowledgement is refused (every disclosure)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_L}/proposals/{S['p1']}/acknowledge", {"items": items})
    check(c == 200 and ((o or {}).get("details") or {}).get("acknowledged_by") == str(U_ADMIN1),
          "the maker acknowledges every disclosure (recorded once)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{S['p1']}/submit")
    check(c == 200, "submitted after the acknowledgement", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{S['p1']}/approve", {})
    check(c == 403, "the maker cannot approve", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(ACC_LS)}/margin-agreement/remove",
                     {"reason": "REP13 verify — the agreement was revoked"})
    check(c == 200 and ((o or {}).get("record") or {}).get("status") == "removed", "the margin agreement is removed "
          "(at once, audited)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{S['p1']}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS "
                                   "NULL", S["p1"], fetch="fetchval")
    check(c == 409 and "lapsed" in dumps(o) and "margin agreement" in dumps(o) and st == "pending_approval",
          "approval is refused because eligibility lapsed since the build (naming the margin agreement)",
          f"{c} {dumps(o)} {st}")
    c, o = await api(U_ADMIN1, "POST", f"{_L}/accounts/{acct(ACC_LS)}/margin-agreement",
                     {"agreement_date": today().isoformat(), "document_id": str(S["doc"])})
    rid = ((o or {}).get("record") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"{_L}/margin-agreements/{rid}/approve", {})
    check(c == 201 and c2 == 200, "a new agreement is recorded and approved (maker-checker)", f"{c} {c2} {dumps(o2)}")
    c, o = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{S['p1']}/approve", {})
    p = (o or {}).get("proposal") or {}
    check(c == 200 and p.get("status") == "approved" and p.get("checker_id") == str(U_ADMIN2),
          "a different person approves once every eligibility piece holds again", f"{c} {dumps(o)}")
    S["p1_approved"] = c == 200


# ── Task 8: execution ──────────────────────────────────────────────────────
async def instructions_of(conn, pid) -> list:
    return [dict(r) for r in await scoped(conn, ORG_A, "SELECT * FROM public.execution_instructions WHERE source_ref = "
                                                       "$1::uuid AND system_to IS NULL ORDER BY created_at", pid)]


async def send_and_fill(iid, qty, price, filled_at=None) -> tuple[int, dict]:
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/sent", {"channel": "phone"})
    if c != 200:
        return c, o
    at = (filled_at or datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    return await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/fills",
                     {"quantity": str(qty), "price": str(price), "filled_at": at})


async def manual_instruction(a, key, side, qty, effect, lots=None) -> tuple[int, dict]:
    st = {"position_effect": effect}
    if lots:
        st["lots"] = lots
    tl = {"account_id": acct(a), "executor_type": "broker", "executor_name": "REP13 Verify Broker",
          "execute_by": (today() + timedelta(days=3)).isoformat(),
          "trades": [{"global_security_id": str(S["sec"][key]), "side": side, "quantity": str(qty),
                      "short_terms": st}]}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", tl)
    ids = ((o or {}).get("routing") or {}).get("instruction_ids") or []
    if c not in (200, 201) or not ids:
        return c, o
    iid = ids[0]
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/submit")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/execution/instructions/{iid}/approve", {})
    return (c2 if c1 == 200 else c1), {"id": iid, "submit": o1, "approve": o2}


async def short_book(conn, a):
    with _ctx(ORG_A):  # the route's order: RLS context FIRST, then acquire (the pool applies the GUCs at acquire)
        async with (await get_pool()).acquire() as pc:
            book, realized = await LS.short_book(pc, ORG_A, [acct(a)])
    return book.get(acct(a), {}), realized


async def y_execution(conn):
    section("[Y] Execution: SELL SHORT / BUY TO COVER end to end; the order sheet prints them; a non-declaring adapter "
            "refuses at submit; the manual route is always offered; fills give a negative position with lots")
    if S.get("p1_approved"):
        ins = await instructions_of(conn, S["p1"])
        by = {S["key_of"].get(str(i["global_security_id"])): i for i in ins}
        sc = by.get("SC")
        sct = json.loads(sc["short_terms"]) if sc and sc.get("short_terms") else {}
        check(len(ins) == 4 and sc is not None and sc["side"] == "sell_short" and sct.get("position_effect") == "open"
              and sc["status"] == "approved" and by["LA"]["side"] == "buy" and by["LA"].get("short_terms") is None,
              "approval routed the proposal through the declaring adapter: 4 instructions APPROVED, the shorts "
              "'sell_short' with their short terms", dumps([(k, v["side"], v["status"]) for k, v in by.items()]))
        if sc:
            with _ctx(ORG_A):  # the route's order: RLS context FIRST, then acquire (the pool applies the GUCs at acquire)
                async with (await get_pool()).acquire() as pc:
                    sd = await BK.instruction_sheet_dict(pc, ORG_A, str(sc["id"]))
            rows = dict(OSH.sheet_rows(sd))
            check(rows.get("Side") == "SELL SHORT" and "OPENING a short sale" in rows.get("Position effect", ""),
                  "the manual order sheet prints 'SELL SHORT' plainly, with the position effect", dumps(rows))
            c, o = await send_and_fill(sc["id"], 375, 40)
            pos, _ = await short_book(conn, ACC_LS)
            x = pos.get(str(S["sec"]["SC"]))
            check(c == 200 and x is not None and x.quantity == 375 and len(x.lots) == 1 and x.lots[0].open_price == 40,
                  "the SELL SHORT fill opens a short position (−375 SC) with its lot (the fill)",
                  f"{c} {dumps(o)} {x and (x.quantity, [(l.id, l.quantity) for l in x.lots])}")
            c, o = await api(U_ADMIN1, "GET", f"{_L}/accounts/{acct(ACC_LS)}")
            book = {b["security_id"]: b for b in (o or {}).get("short_book") or []}
            b = book.get(str(S["sec"]["SC"])) or {}
            # rep13 fix 1: the exact Decimal, not its text — the API returns the column's scale ("-375.00000000")
            sq = b.get("signed_quantity")
            check(sq is not None and str(sq).startswith("-") and D(sq) == Decimal(-375) and b.get("term") == "short" and len(b.get("lots") or []) == 1,
                  "the account panel shows the short as a NEGATIVE quantity with its lot", dumps(b))
    # a custodian adapter that does not declare short_selling refuses at submit
    tl = {"account_id": acct(ACC_ND), "executor_type": "broker", "executor_name": "REP13 Verify Broker",
          "execute_by": (today() + timedelta(days=3)).isoformat(),
          "trades": [{"global_security_id": str(S["sec"]["SC"]), "side": "sell_short", "quantity": "10",
                      "short_terms": {"position_effect": "open"}}]}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", tl)
    n = await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions WHERE account_id = $1",
                     ACC_ND, fetch="fetchval")
    check(c == 409 and "does not declare short_selling" in dumps(o) and "manual route" in dumps(o) and int(n) == 0,
          "the zzr13nd adapter REFUSES a SELL SHORT at submit (409, nothing written), naming the manual route",
          f"{c} {dumps(o)} {n}")
    tl_bad = {**tl, "trades": [{**tl["trades"][0], "short_terms": None}]}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", tl_bad)
    check(c == 422, "a SELL SHORT without short terms is refused by the typed trade list", f"{c}")
    # the manual route on a long/short proposal whose custodian adapter refuses
    code, out = await generate(ACC_M)
    pm = ((out or {}).get("proposal") or {}).get("id")
    c0, d0 = await details(U_ADMIN1, pm) if pm else (0, {})
    items = [x["key"] for x in ((d0 or {}).get("details") or {}).get("acknowledgement_items") or []]
    await api(U_ADMIN1, "POST", f"{_L}/proposals/{pm}/acknowledge", {"items": items})
    await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pm}/submit")
    c, o = await api(U_ADMIN2, "POST", f"/api/v1/proposals/{pm}/approve", {})
    check(c == 200 and ((o or {}).get("proposal") or {}).get("status") == "approved"
          and "does not declare short_selling" in str((o or {}).get("execution_error")),
          "approved: the adapter route (zzr13ma reads zzr13nd's capability) refused the shorts — the approval stands",
          f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_L}/proposals/{pm}/execute", {"route": "adapter"})
    check(c == 409 and "manual route" in dumps(o), "routing through the adapter again: refused, naming the manual route",
          f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_L}/proposals/{pm}/execute", {"route": "manual"})
    ins = await instructions_of(conn, pm) if pm else []
    shorts = [i for i in ins if i["side"] == "sell_short"]
    check(c == 200 and len(ins) == 4 and len(shorts) == 2 and all(i["routed_via"] == "manual" for i in ins)
          and all("manual route chosen" in (i["routing_reason"] or "") for i in ins),
          "the MANUAL route is always offered: 4 instructions (2 SELL SHORT) a person carries out",
          f"{c} {dumps(o)} {[(i['side'], i['routed_via']) for i in ins]}")


# ── Task 6: tax ────────────────────────────────────────────────────────────
async def y_tax(conn):
    section("[Y] Tax: a short closed after 400 days is short-term; a short at a loss is a harvest candidate and its "
            "re-establishment respects the wash rule")
    old = datetime.now(timezone.utc) - timedelta(days=400)
    c, r1 = await manual_instruction(ACC_H, "SC", "sell_short", 100, "open")
    ok1 = c == 200 and r1.get("id")
    if ok1:
        c, o = await send_and_fill(r1["id"], 100, 40, filled_at=old)
        ok1 = c == 200
    check(bool(ok1), "a SELL SHORT of 100 SC (manual instruction, maker-checker) fills 400 days ago at 40",
          f"{c} {dumps(r1)}")
    c, r2 = await manual_instruction(ACC_H, "SC", "buy_to_cover", 40, "close")
    if c == 200 and r2.get("id"):
        c, o = await send_and_fill(r2["id"], 40, 30)
    pos, realized = await short_book(conn, ACC_H)
    last = realized[-1] if realized else {}
    check(c == 200 and last.get("term") == "short" and D(last.get("gain", 0)) == Decimal(400)
          and last.get("lots", [{}])[0].get("opened") == old.date().isoformat(),
          "covered 40 at 30 after 400 days: a $400 gain, SHORT-TERM (IRC §1233 — whatever the holding period)",
          dumps(last))
    check(R.short_close_term(old.date() - timedelta(days=4000), today()) == "short",
          "the rule itself: a short open for eleven years still closes short-term")
    c, o = await manual_instruction(ACC_H, "SC", "buy_to_cover", 1000, "close")
    check(c == 409 and "shares short" in dumps(o), "a cover larger than the open short is refused", f"{c} {dumps(o)}")
    # the price rises to 44: the remaining 60 short are at a loss (40 − 44) × 60 = −240 → harvest
    S["prices"]["SC"] = "44"
    code, out = await generate(ACC_H)
    ph = ((out or {}).get("proposal") or {}).get("id")
    c2, d = await details(U_ADMIN1, ph) if ph else (0, {})
    hv = [x for x in ((d or {}).get("details") or {}).get("harvest") or [] if x.get("book") == "short"]
    tr = {(S["key_of"].get(str(t["global_security_id"])), t["side"]): t for t in await trades(conn, ph)} if ph else {}
    cover = tr.get(("SC", "buy_to_cover"))
    leg = json.loads(cover["short_leg"]) if cover else {}
    check(code == 201 and len(hv) == 1 and hv[0]["outcome"] == "proposed" and D(hv[0]["loss"]) == Decimal(-240)
          and hv[0]["substitute"] == str(S["sec"]["XS"]),
          "the short at a loss is a harvest candidate (−$240), re-established in the approved substitute XS",
          dumps(hv))
    check(cover is not None and D(cover["quantity"]) == 60 and [x["short_lot_id"] for x in leg.get("lots") or []]
          == [pos[str(S["sec"]["SC"])].lots[0].id] and ("XS", "sell_short") in tr and ("SC", "sell_short") not in tr,
          "BUY TO COVER 60 SC names the open short lot; SELL SHORT XS replaces it; SC is not re-shorted",
          f"{dumps(leg)} {sorted(tr)}")
    check(D(cover["est_st_gain"]) == Decimal(-240) and cover["est_lt_gain"] == 0,
          "the harvested loss is short-term", f"{cover and (cover['est_st_gain'], cover['est_lt_gain'])}")
    # cover the rest AT A LOSS; a plain SELL SHORT of SC within 30 days is then deferred (IRC §1091(e))
    c, r3 = await manual_instruction(ACC_H, "SC", "buy_to_cover", 60, "close")
    if c == 200 and r3.get("id"):
        c, o = await send_and_fill(r3["id"], 60, 44)
    code, out = await generate(ACC_H)
    pw = ((out or {}).get("proposal") or {}).get("id")
    tr = {(S["key_of"].get(str(t["global_security_id"])), t["side"]): t for t in await trades(conn, pw)} if pw else {}
    sc = tr.get(("SC", "sell_short"))
    w = json.loads(sc["wash_sale"]) if sc and sc.get("wash_sale") else {}
    check(c == 200 and code == 201 and w.get("outcome") == "deferred" and w.get("rule") == "short_wash"
          and w.get("until") == (today() + timedelta(days=31)).isoformat() and "1091" in w.get("reason", ""),
          "after a cover at a loss, a new SELL SHORT of SC is DEFERRED to the first clear day (never routed)",
          dumps(w))
    code, est = await api(U_VIEW, "GET", f"{_L}/accounts/{acct(ACC_H)}/tax-estimate")
    te = (est or {}).get("tax_estimate") or {}
    check(code == 200 and te.get("label") == "estimate" and te.get("text") == "not estimated — no tax profile"
          and te.get("realized_short_term_ytd") == "160.00",
          "the tax estimate is labelled 'estimate': realized short-term this year $160.00 (+$400 − $240, both closes "
          "today); 'not estimated — no tax profile' without one (never a default rate)", dumps(te))


# ── isolation / no network / real rows / routes / front end ────────────────
async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); no model call, no network call")
    for path in (f"{_L}/accounts/{acct(ACC_LS)}", f"{_L}/proposals/{S.get('p1')}"):
        c, o = await api(U_ADMIN_B, "GET", path)
        check(c == 404, f"org B cannot read org A's {path.split('/')[4]} (404)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN_B, "POST", f"{_L}/accounts/{acct(ACC_LS)}/settings", settings_body())
    check(c == 404, "org B's VALID settings body on org A's account is a 404", f"{c} {dumps(o)}")
    for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        own = int(await scoped(conn, org, "SELECT count(*) FROM public.borrow_rates WHERE org_id = $1", org,
                               fetch="fetchval"))
        theirs = int(await scoped(conn, org, "SELECT count(*) FROM public.borrow_rates WHERE org_id = $1", other,
                                  fetch="fetchval"))
        plat = int(await scoped(conn, org, "SELECT count(*) FROM public.borrow_rates r JOIN portfolio.securities_global"
                                           " g ON g.id = r.global_security_id WHERE r.owner_scope = 'platform' AND "
                                           "g.name LIKE $1", f"{NAME_PREFIX}%", fetch="fetchval"))
        check(own >= 1 and theirs == 0 and plat == 1, f"org {'A' if org == ORG_A else 'B'} sees its own borrow rates "
              "and the platform rate, never the other org's", f"own={own} theirs={theirs} platform={plat}")
    for t in ("public.long_short_settings", "public.suitability_records", "public.long_short_proposals"):
        a_n = int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval"))
        b_n = int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval"))
        check(a_n > 0 and b_n == 0, f"{t}: org A's rows visible to org A ({a_n}), none to org B", f"{a_n} {b_n}")
    check(NETWORK_CALLS == [], "no network call was attempted", dumps(NETWORK_CALLS[:3]))
    check(MODEL_CALLS == [], "no model call was attempted", dumps(MODEL_CALLS[:3]))


async def y_real_rows(conn):
    section("[Y] Real and demo-olivo rows untouched (count + checksum, keyed by the real primary key)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")
    if S.get("demo_org") is None:
        find("demo-olivo", "no organization with slug 'demo-olivo' — its accounts were not fingerprinted")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    data = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
    rows = data.get("routes", []) if isinstance(data, dict) else data
    have = {(m, r.get("path")): r.get("after") for r in rows for m in (r.get("methods") or [])}
    miss = [(k, g) for k, g in ROUTES.items() if g not in str(have.get(k))]
    check(not miss, f"each of rep13's {len(ROUTES)} routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    # rep13 fix 1: count rep13's OWN file, run by path — a title-prefix match over the whole suite also counted 4
    # other files' tests sharing a prefix ("a view-only …", "the panel …", "a lost envelope …") → 12
    c13, o13, e13 = run_cmd(["node", "--test", "tests/replication/rep13.test.mjs"], WEB_DIR, timeout=300)
    mine = re.findall(r"^ok \d+ - ", o13, re.M)
    check(c13 == 0 and len(mine) == 8 and re.search(r"^# tests 8$", o13, re.M) is not None
          and re.search(r"^# pass 8$", o13, re.M) is not None and re.search(r"^# fail 0$", o13, re.M) is not None
          and "not ok" not in o13 and "not ok" not in out,
          "the 8 rep13 render tests (tests/replication/rep13.test.mjs, run by path) ran and passed (no control without "
          "the server's flag and action)", f"{c13} {len(mine)} {(o13 + e13)[-300:] if c13 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep6.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep6.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_rep6.py: 0 FAIL (its own final TOTAL line and exit code)",
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
    import time as _time

    t0 = _time.monotonic()
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"CLEANUP_SECONDS: {_time.monotonic() - t0:.1f}")
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    check(left == 0, "zero fixture rows remain", f"{left}")
    await close_conn(conn)
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
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("capabilities", y_capabilities, conn)
            await run("models", y_models, conn)
            await run("settings", y_settings, conn)
            await run("borrow rates", y_borrow_rates, conn)
            if S.get("m1") and S.get("m2"):
                await run("joins", y_joins, conn)
                await run("proposal", y_proposal_exact, conn)
                await run("tracking error", y_tracking_error, conn)
                await run("proposal gates", y_gates_proposal, conn)
                if S.get("p1"):
                    await run("disclosures", y_disclosures, conn)
                await run("execution", y_execution, conn)
                await run("tax", y_tax, conn)
            else:
                check(False, "the long/short models were created", "the models section failed")
            await run("isolation", y_isolation, conn)
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (platform securities, attributes, capability rows and borrow "
                  "rates included)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count"
                          + ("" if t in WHOLE_COUNTED else " (fixture rows)"), f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k]} → {after.get(k)}")
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
