"""verify_cash03 — CASH03: the OBLIGATION-MATCHED LADDER and AFTER-TAX INSTRUMENT SELECTION; Treasury Fiscal Data and
SEC N-MFP registered through the provider registry, switched off.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash03.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated
from Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx
transport refuses and records (asserted empty at the end), the AI helpers refuse and record, and both providers read
ONLY the recorded payloads in apps/api/tests/fixtures/cash03 through a FixtureTransport (which raises on any URL it
does not hold).

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``), the Auth0 profile call,
and the providers' call budget (``_cash03_fixtures.NoBudget`` — the fixture write never touches provider_call_log).
REAL: the app through an ASGI transport, services.cash_ladder / cash_instruments / cash_policy / cash_coverage /
security_master_load (Matcher) / security_data (layer, writes, nightly) / tax_profiles / edgar_nmfp /
market_data.treasury_fiscal, RLS and the live database. The script's own connection does not bypass RLS
(rolbypassrls = false, asserted). TIME IS INJECTED: routers.cash's "today" is TODAY (2026-11-09). The plan's reads are
narrowed to this script's fixture prefixes through routers.cash.set_plan_fixture (same shape — asserted).

SWITCHED OFF — and how the positive path is still proven. Both providers ship 'not_connected'; the app's plan (the
ASGI envelope) therefore shows every Treasury issue / fund yield as "not connected" (asserted). The ACTIVE path is
proven on the script's own connection inside ONE transaction that flips both rows to 'active', reads the plan
through services.cash_instruments.build, and is ROLLED BACK — the real provider rows are fingerprinted before and
after (unchanged).

FIXTURES: two orgs 99000000-0000-0000-0000-0000cac30001 / 0002 (is_test = true, asserted; org A has VISIBILITY1
enforcement ON); users with real user_roles grants — the app's decision on each asserted:
  org A  U_ADV, U_ADV2 advisor (+ manage_cash_policy; assigned to both families), U_VIEW compliance (view_portfolio
         only; assigned to H1), U_MEM member (H1), U_RS support_staff (+ manage_cash_policy; assigned to H2 ONLY)
  org B  U_B advisor (+ manage_cash_policy)
H1 (E_T trust 1,000,000 cash; E_P person) holds two accounts: A1 taxable trust (cash 600,000), A2 IRA (cash 400,000);
an approved NY tax profile (federal 37 % + NIIT 3.8 %, NY 10.9 %, NYC 3.876 %); the hand-computed schedule (below)
recorded through the cash01 API; a cash policy (N 3, R 12, horizon 24, 6 rungs) drafted by U_ADV and approved by
U_ADV2 through the cash02 API. Global fixture rows (no org_id): Treasury issues 99C030…, N-MFP series S99003… /
classes C99003… (through Matcher — the security master's ONE load path), their yields and a fixture Treasury curve
CASH03VFY_DGS* in portfolio.index_levels. Every reserved prefix is checked absent from real rows first.

TEARDOWN (start and end, one function): ``_verify_cleanup.cleanup`` on the operator DSN with ``execution.teardown``
(rep6's tax_inputs_guard), keyed by this run's fixture ids; the global rows passed in the plan by their fixture
prefixes / materialised ids. Every touched table re-read for its exact pre-test count, BY FIXTURE IDS ONLY. Real rows
(the provider rows and ranks, every non-fixture cash policy / obligation / account / tax profile — the Olivo demo's
included — the real security master and index levels) are fingerprinted before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only
(per-table fixture counts printed). --only=<a,b,…>: the one-off path (cash03 fix 1) — teardown, prefix guard, real
fingerprints, schema read, seed, then ONLY the named fixture sections (seed | providers | master | ladder | after-tax |
plan | app; 'plan' pulls in 'master', whose sec_by_cusip / sec_by_class it reads), then the full teardown + leftover +
real-row checks; no task 1 / loaders / routes / front end / regression. With VERIFY_NESTED=1 the regression section
prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash03"
BEFORE_REF = "7030b41"   # cash03-work's base: 'merge cash02'
#: cash03 fix 1 — the route no longer reads authz.member_entity_scope (on a POST it put an advisor author on the
#: MEMBER path → an empty scope → 404 on their own family); it narrows by the VISIBILITY1 staff scope
#: (scope_for_request, the 'sql-scope' tag) behind require_visible, exactly as cash02's write routes are gated
_W = "internal + any(manage_cash_policy)"
ROUTES = {("POST", "/api/v1/cash/households/{household_id}/plan/regenerate"): _W}
OWN_FILES = ("apps/api/services/cash_ladder.py", "apps/api/services/cash_instruments.py",
             "apps/api/services/us_business_days.py", "apps/api/routers/cash.py",
             "apps/api/scripts/load_olivo_ladder.py")
NETWORK_FILES = ("apps/api/services/market_data/treasury_fiscal.py", "apps/api/services/edgar_nmfp.py")
NODE_TEST = "tests/cash03/cash03.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH03_NOTES.md"
LADDER_DOC = REPO / "docs" / "cash" / "CASH_LADDER.md"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
MIGRATION = REPO / "docs" / "cash03_part1.sql"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash03's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    check(len(o.before) > 0 and not o.changed and not o.removed,
          "cash03 changed and removed no existing route's gate (the treasury envelope keeps cash01's read gate)",
          f"{o.where()}; changed={sorted(flat(o.changed))[:3]} removed={sorted(flat(o.removed))[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash03's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash03's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    cl = _code_only(_src("apps/api/services/cash_ladder.py"))
    ci = _code_only(_src("apps/api/services/cash_instruments.py"))
    check("CP.windows(" in cl and "OB.add_months(" in cl and "BD.prior_business_day(" in cl,
          "services/cash_ladder.py takes the Long-term window from cash02's windows(), months from the ledger's "
          "add_months and maturities from the business-day calendar (no second formula)")
    check("L.get_bulk(" in cl and "L.get_bulk(" in ci and "R.source_allowed(" in cl,
          "the ladder and the plan read every yield through THE read path (security_data.layer — rank, licence, "
          "staleness) and gate the issue read on the provider's own status / licence")
    check("TP.approved_profile(" in ci and "TP.subject_of(" in ci,
          "after-tax reads the owner's APPROVED tax profile (services.tax_profiles) per account subject")
    check(all("INSERT INTO" not in c for c in (cl, ci)),
          "the ladder and the plan write nothing (derived; no cash_ladder_rungs, no side table)")
    tf = _code_only(_src("apps/api/services/market_data/treasury_fiscal.py"))
    nm = _code_only(_src("apps/api/services/edgar_nmfp.py"))
    check("M.Matcher(KEY" in tf and "M.Matcher(KEY" in nm and "W.upsert_index_levels(" in tf
          and "W.upsert_index_levels(" in nm and "INSERT INTO portfolio.securities_global" not in tf + nm,
          "both providers write the security master ONLY through security_master_load.Matcher and the yields only "
          "through security_data.writes.upsert_index_levels")
    check("edgar_index.parse_index_lines(" in nm and "edgar_fetch." in nm and "import httpx" not in _src(
        "apps/api/services/edgar_nmfp.py"), "N-MFP rides the EDGAR pipeline (edgar_index discovery, edgar_fetch's "
          "archive layout; the real transport sends sec.gov through edgar_fetch._get) — no new fetcher")
    sml = _src("apps/api/services/security_master_load.py")
    check('"treasury_fiscal", "sec_nmfp"}' in sml and '"treasury_fiscal": {"cusip", "isin"}' in sml
          and 'CREATE_BY_CLASS_ID = {"sec_nmfp"}' in sml and '"maturity_date", "issue_date", "security_term", '
          '"treasury_type", "mmf_category")' in sml,
          "security_master_load: both providers are creators with their identifiers, N-MFP creates by SEC class id, "
          "the five new reference fields")
    reg = _src("apps/api/services/security_data/providers/__init__.py")
    check('"treasury_fiscal": treasury_fiscal.TreasuryFiscalProvider(' in reg and '"sec_nmfp": edgar_nmfp.NmfpProvider('
          in reg, "both provider modules are in the security-data code registry")
    sn = _src("apps/api/services/security_data/nightly.py")
    check('"cash_sources"' in sn and "async def cash_sources(" in sn and 'p["status"] != "active"' in sn
          and "def stale_limit(" in sn and "limit = stale_limit(prov, dtype)" in sn,
          "security_data/nightly.py: the cash_sources step acts only on ACTIVE providers; per-provider staleness")
    check('"cash.tax_estimate"' in _src("apps/api/services/disclosures.py"),
          "the disclosure library defines cash.tax_estimate")
    sql = "\n".join(ln.split("--", 1)[0] for ln in (MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists()
                                                       else "").splitlines())   # SQL only, comments stripped
    check("'treasury_fiscal'" in sql and "'sec_nmfp'" in sql and sql.count("'not_connected'") == 2
          and "CREATE TABLE" not in sql, "docs/cash03_part1.sql registers both providers NOT CONNECTED and creates no "
          "table")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9."):
        check(sec in notes, f"docs/cash/CASH03_NOTES.md has section {sec}")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    for f in NETWORK_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b",
                                          src, re.M),
              f"{f}: no model call and no network client of its own (the transport is injected)", f"{len(src)} chars")
    check("verify_cash03.py" in _src("scripts/regression_set.txt"), "verify_cash03.py is in scripts/regression_set.txt")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))


from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
import _cash03_fixtures as FX  # noqa: E402
import _db_connect as DBC  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
import routers.cash as RC  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import cash_coverage as CC  # noqa: E402
from services import cash_instruments as CI  # noqa: E402
from services import cash_ladder as CL  # noqa: E402
from services import cash_policy as CP  # noqa: E402
from services import edgar_nmfp as NM  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import rbac  # noqa: E402
from services import security_master_symbols as SYM  # noqa: E402
from services import us_business_days as BD  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.market_data import treasury_fiscal as TF  # noqa: E402
from services.security_data import nightly as SN  # noqa: E402
from services.security_data import settings as SDS  # noqa: E402
from services.security_data import writes as SDW  # noqa: E402
from services.security_data.providers import build_providers  # noqa: E402
from services.security_data.providers.reference_common import CallBudget  # noqa: E402

TAG = "cash03verify"
NAME = "CASH03 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac3"
TODAY = date(2026, 11, 9)       # the injected "today" (the recorded fixtures are dated relative to it)
TEARDOWN_GUCS = ("execution.teardown",)
CUSIP_PREFIX = "99C03"           # fixture Treasury CUSIPs (real Treasury CUSIPs start 912)
CLASS_PREFIX = "C99003"
SERIES_ID_PREFIX = "S99003"
CURVE_PREFIX = "CASH03VFY_"
KEY_PATTERNS = (f"tfd:{CUSIP_PREFIX}%", f"nmfp:{CLASS_PREFIX}%", f"nmfp:{SERIES_ID_PREFIX}%", f"{CURVE_PREFIX}%")
IDENT_PATTERNS = (f"{CUSIP_PREFIX}%", f"US{CUSIP_PREFIX}%", f"{CLASS_PREFIX}%", f"{SERIES_ID_PREFIX}%")
PROVIDERS = ("treasury_fiscal", "sec_nmfp")
ZERO = Decimal(0)


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_ADV, U_ADV2, U_VIEW, U_MEM, U_RS, U_B = (_id(10 + i) for i in range(6))
H1, H2, HB = _id(30), _id(31), _id(32)
E_T, E_P, E_O, E_B = _id(40), _id(41), _id(42), _id(43)
A_CASH, A_CASH_B = _id(60), _id(61)
ACC_1, ACC_2, ACC_O, ACC_B = _id(70), _id(71), _id(72), _id(73)
RUN_TF, RUN_NM = _id(900), _id(901)        # the fixture Matcher runs' ids (security_reference_attributes.run_id)
FIXTURE_RUNS = [RUN_TF, RUN_NM]

PERMS = {
    "advisor": {"view_portfolio", "manage_portfolio", "view_members", "manage_cash_policy"},
    "compliance": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "support_staff": {"view_portfolio", "manage_portfolio", "view_members", "manage_cash_policy"},
}
USERS = {
    U_ADV: (ORG_A, "member", "adv", "advisor"),
    U_ADV2: (ORG_A, "member", "adv2", "advisor"),
    U_VIEW: (ORG_A, "member", "view", "compliance"),
    U_MEM: (ORG_A, "member", "mem", "member"),
    U_RS: (ORG_A, "member", "rs", "support_staff"),
    U_B: (ORG_B, "member", "b", "advisor"),
}
ALL_USERS = list(USERS)
ENTS = {
    E_T: (ORG_A, "trust", "Dynasty", "Ladder Family Trust"),
    E_P: (ORG_A, "individual", None, "Ladder Family Person"),
    E_O: (ORG_A, "individual", None, "Other Family Person"),
    E_B: (ORG_B, "individual", None, "Org B Person"),
}
HOUSEHOLDS = {H1: (ORG_A, "Ladder", (E_T, E_P)), H2: (ORG_A, "Other", (E_O,)), HB: (ORG_B, "B", (E_B,))}
CASH_KEY = "taxonomy_mc_8_1"
ASSETS = {A_CASH: ORG_A, A_CASH_B: ORG_B}
POSITIONS = [(E_T, A_CASH, "1000000.00"), (E_O, A_CASH, "200000.00"), (E_B, A_CASH_B, "300000.00")]
# (id, org, household, primary entity, registration, tax status, masked, cash balance)
ACCOUNTS = [(ACC_1, ORG_A, H1, E_T, "trust", "taxable", "****3001", "600000.00"),
            (ACC_2, ORG_A, H1, E_P, "ira", "tax_deferred", "****3002", "400000.00"),
            (ACC_O, ORG_A, H2, E_O, "individual", "taxable", "****3003", "200000.00"),
            (ACC_B, ORG_B, HB, E_B, "individual", "taxable", "****3004", "300000.00")]
# the hand-computed schedule (recorded through the cash01 API): (key, payer, source type, name, amount, due, certainty)
OBLIGATIONS = [
    ("near", E_T, "one_off", "near item", "100000.00", "2026-12-01", "firm"),
    ("reserve", E_T, "one_off", "reserve item", "50000.00", "2027-03-15", "firm"),
    ("l1", E_T, "tax_estimate", "January estimated tax", "400000.00", "2028-01-14", "estimated"),
    ("l2", E_T, "one_off", "Pledge (4 July)", "300000.00", "2028-07-04", "firm"),
    ("s1", E_T, "one_off", "Lifestyle top-up", "20000.00", "2028-03-15", "estimated"),
    ("s2", E_T, "one_off", "Club dues", "15000.00", "2027-11-20", "estimated"),
    ("other", E_O, "one_off", "other family item", "20000.00", "2028-02-01", "firm"),
]
POLICY = {"scope_type": "household", "preset": "custom", "near_months": 3, "reserve_months": 12, "horizon_months": 24,
          "rung_count": 6, "buffer_months": "0", "objective_score": 3, "min_coverage_months": "6"}
TAX = {"federal_ordinary_rate": Decimal("0.37"), "federal_lt_rate": Decimal("0.20"), "niit_applies": True,
       "niit_rate": Decimal("0.038"), "state_code": "NY", "state_rate": Decimal("0.109"), "city": "New York City",
       "city_rate": Decimal("0.03876")}
FED, SL = Decimal("0.408"), Decimal("0.109") + Decimal("0.03876")
#: the fixture Treasury curve (provider fred, org NULL), as of 2026-11-06 — the same shape as CL.CURVE_POINTS
CURVE_VALUES = {"DGS1MO": "4.20", "DGS3MO": "4.15", "DGS6MO": "4.10", "DGS1": "4.00", "DGS2": "3.90", "DGS5": "3.95",
                "DGS7": "4.05", "DGS10": "4.20"}
CURVE_DATE = date(2026, 11, 6)
FIXTURE_CURVE = tuple((CURVE_PREFIX + k, m) for k, m in CL.CURVE_POINTS)
PLAN_FIXTURE = {"cusip_like": CUSIP_PREFIX + "%", "class_like": CLASS_PREFIX + "%", "curve_points": FIXTURE_CURVE}
#: staleness fixtures: (index key, provider, date) — 20 / 70 days old (stale for Treasury's 10 / N-MFP's 45); the fresh
#: N-MFP yields from the refresh are 9 days old: NOT stale at 45, but they WOULD be at index_level's default 5
STALE_ROWS = [(f"tfd:{CUSIP_PREFIX}0BA2:yield", "treasury_fiscal", date(2026, 10, 20), "4.300"),
              (f"nmfp:{CLASS_PREFIX}0009:7d", "sec_nmfp", date(2026, 8, 31), "4.900")]

COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "portfolio.assets", "portfolio.positions", "public.accounts",
    "public.account_balances_daily", "public.cash_obligations", "public.cash_policies", "public.tax_profiles",
    "public.sod_decisions", "public.audit_log", "public.member_todos", "public.workflow_runs", "public.domain_events",
    "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.security_reference_attributes", "portfolio.security_master_review", "portfolio.index_levels",
    "portfolio.data_quality_events", "portfolio.provider_call_log",
)
SEED_CHECK_LITERALS = (
    ("public.cash_obligations", "cash_obligations_source_type_chk", ("one_off", "tax_estimate")),
    ("public.cash_obligations", "cash_obligations_certainty_chk", ("firm", "estimated")),
    ("public.cash_policies", "cash_policies_preset_chk", ("custom",)),
    ("portfolio.positions", "positions_authority_chk", ("stated",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.positions", "positions_basis_chk", ("value",)),
    ("portfolio.assets", "assets_valuation_chk", ("amortized_cost",)),
    ("portfolio.assets", "assets_class_chk", ("financial",)),
    ("public.tax_profiles", "tax_profiles_status_chk", ("approved",)),
    ("public.tax_profiles", "tax_profiles_subject_chk", ("household",)),
    ("public.tax_profiles", "tax_profiles_filing_chk", ("married_filing_jointly",)),
    ("public.delegate_grants", "delegate_grants_scope_check", ("view_only",)),
    ("portfolio.data_providers", "data_providers_status_chk", ("active", "not_connected")),
    ("portfolio.securities_global", "securities_global_type_chk", ("fixed_income", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "isin", "sec_series_id",
                                                                              "sec_class_id")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_field_chk",
     ("maturity_date", "issue_date", "security_term", "treasury_type", "mmf_category", "fund_structure", "country")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_treasury_chk", ("Bill", "Note", "TIPS")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_mmf_chk",
     ("treasury", "government", "other_tax_exempt")),
    ("portfolio.data_quality_events", "data_quality_events_kind_chk", ("stale",)),
)
_U, _O, _E, _H, _A = ("99000000-0000-0000-0000-0000cac30010", "99000000-0000-0000-0000-0000cac30001",
                      "99000000-0000-0000-0000-0000cac30040", "99000000-0000-0000-0000-0000cac30030",
                      "99000000-0000-0000-0000-0000cac30070")
#: every row SHAPE seeded by raw SQL or written by a provider on the verify's behalf — evaluated against EVERY CHECK on
#: its table IN THE DATABASE before seeding (jsonb_populate_record → the constraint's own expression)
SEED_ROWS = {
    "public.organizations": [{"id": _O, "name": "CASH03 Verify Org A", "slug": "cash03verify-a", "is_test": True}],
    "public.users": [{"id": _U, "org_id": _O, "email": "cash03verify-adv@test.local", "full_name": "CASH03 ADV",
                      "auth0_sub": "auth0|cash03verify-adv", "role": "member", "status": "active", "is_active": True,
                      "is_test": True}],
    "public.entities": [{"id": _E, "org_id": _O, "display_name": "CASH03 VERIFY Ladder Family Trust",
                         "status": "active", "entity_type": "trust", "sub_type": "Dynasty"}],
    "public.households": [{"id": _H, "org_id": _O, "name": "CASH03 VERIFY Ladder family"}],
    "public.delegate_grants": [{"org_id": _O, "principal_entity_id": _E, "delegate_user_id": _U, "scope": "view_only",
                                "effective_from": "2026-11-08T00:00:00+00:00"}],
    "public.staff_assignments": [{"org_id": _O, "entity_id": _E, "assigned_to_user_id": _U,
                                  "role_label": "Lead adviser"}],
    "public.org_settings": [{"org_id": _O, "setting_key": "staff_visibility.enforced", "setting_value": True,
                             "category": "general"}],
    "portfolio.assets": [{"id": _E, "org_id": _O, "name": "CASH03 VERIFY cash", "asset_class": "financial",
                          "asset_type": "cash", "ownership_basis": "value", "valuation_method": "amortized_cost",
                          "currency_code": "USD", "default_taxonomy_key": "taxonomy_mc_8_1"}],
    "portfolio.positions": [{"id": _E, "org_id": _O, "owner_entity_id": _E, "asset_id": _E, "as_of_date": "2026-11-08",
                             "ownership_basis": "value", "market_value": "1000000.00", "authority": "stated",
                             "source_system": "manual"}],
    "public.accounts": [{"id": _A, "org_id": _O, "account_number_masked": "****3001", "account_number_hash":
                         "cash03verify-3001", "custodian_code": "cash03verify", "registration_type": "trust",
                         "tax_status": "taxable", "primary_entity_id": _E, "household_id": _H}],
    "public.account_balances_daily": [{"org_id": _O, "account_id": _A, "as_of_date": "2026-11-06",
                                       "total_market_value": "600000.00", "cash_value": "600000.00",
                                       "source_system": "manual"}],
    "public.tax_profiles": [{"org_id": _O, "subject_type": "household", "subject_id": _H, "tax_year": 2026,
                             "version_no": 1, "filing_status": "married_filing_jointly", "federal_ordinary_rate": "0.37",
                             "federal_lt_rate": "0.20", "niit_applies": True, "niit_rate": "0.038", "state_code": "NY",
                             "state_rate": "0.109", "city": "New York City", "city_rate": "0.03876",
                             "as_of": "2026-11-09", "source": "adviser", "source_note": "verify fixture",
                             "status": "approved", "entered_by": _U, "checker_id": "99000000-0000-0000-0000-0000cac30011",
                             "decided_at": "2026-11-09T00:00:00+00:00"}],
    "portfolio.index_levels": [{"index_key": CURVE_PREFIX + "DGS1", "level_date": "2026-11-06", "value": "4.00",
                                "provider_key": "fred", "licence": {}},
                               {"index_key": STALE_ROWS[0][0], "level_date": "2026-10-20", "value": "4.300",
                                "provider_key": "treasury_fiscal", "licence": {}},
                               {"index_key": "nmfp:C990030001:7d", "level_date": "2026-10-31", "value": "4.5000",
                                "provider_key": "sec_nmfp", "licence": {}}],
    "portfolio.security_reference_attributes": [
        {"global_security_id": _E, "field": "maturity_date", "value": "2027-11-04", "provider_key": "treasury_fiscal",
         "as_of": "2026-11-09"},
        {"global_security_id": _E, "field": "treasury_type", "value": "Bill", "provider_key": "treasury_fiscal",
         "as_of": "2026-11-09"},
        {"global_security_id": _E, "field": "treasury_type", "value": "TIPS", "provider_key": "treasury_fiscal",
         "as_of": "2026-11-09"},
        {"global_security_id": _E, "field": "mmf_category", "value": "other_tax_exempt", "provider_key": "sec_nmfp",
         "as_of": "2026-11-09"},
        {"global_security_id": _E, "field": "fund_structure", "value": "money_market", "provider_key": "sec_nmfp",
         "as_of": "2026-11-09"}],
    "portfolio.securities_global_identifiers": [
        {"global_security_id": _E, "id_type": "sec_class_id", "id_value": "C990030001"},
        {"global_security_id": _E, "id_type": "sec_series_id", "id_value": "S990030001"},
        {"global_security_id": _E, "id_type": "cusip", "id_value": "99C030AA3"}],
}
SEED_NON_COLUMN = {"app.is_super_admin", "true", "1 day"}
UNIQUES = ("accounts_active_identity_uq", "account_balances_daily_pkey", "tax_profiles_approved_uq",
           "assets_cash_active_uniq", "index_levels_point_live_uq", "uq_sec_global_ident_sec_class",
           "uq_sec_global_ident_issuer_assigned", "data_providers_key_live_uq", "data_source_rank_provider_live_uq",
           "data_quality_events_live_uq", "cash_policies_active_household_uq", "org_settings_org_id_setting_key_key")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "sub_type"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "delegate_grants": ("org_id", "principal_entity_id", "delegate_user_id", "scope", "effective_from"),
    "staff_assignments": ("org_id", "entity_id", "assigned_to_user_id", "role_label"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category"),
    "portfolio.assets": ("id", "org_id", "name", "asset_class", "asset_type", "ownership_basis", "valuation_method",
                         "currency_code", "default_taxonomy_key"),
    "portfolio.positions": ("org_id", "owner_entity_id", "asset_id", "as_of_date", "ownership_basis", "market_value",
                            "authority", "source_system"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id"),
    "account_balances_daily": ("org_id", "account_id", "as_of_date", "total_market_value", "cash_value",
                               "source_system"),
    "tax_profiles": ("org_id", "subject_type", "subject_id", "tax_year", "version_no", "filing_status",
                     "federal_ordinary_rate", "federal_lt_rate", "niit_applies", "niit_rate", "state_code",
                     "state_rate", "city", "city_rate", "as_of", "source", "source_note", "status", "entered_by",
                     "checker_id", "decided_at"),
    # written on the verify's behalf by the providers' real write paths (Matcher, upsert_index_levels)
    "portfolio.securities_global": ("id", "canonical_id", "name", "short_name", "security_type", "currency_code"),
    "portfolio.securities_global_identifiers": ("global_security_id", "id_type", "id_value", "is_primary"),
    "portfolio.security_reference_attributes": ("global_security_id", "field", "value", "provider_key", "as_of",
                                                "run_id"),
    "portfolio.index_levels": ("index_key", "provider_key", "org_id", "level_date", "value", "upload_id", "licence"),
}

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
_n_pass = 0
_n_fail = 0


class _Rollback(Exception):
    """Raised inside the activation transaction so it never commits."""


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


def q3(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def curve_at(months: Decimal) -> Decimal:
    """The verify's OWN linear interpolation of the fixture curve (independent of services.cash_ladder)."""
    pts = sorted((m, Decimal(CURVE_VALUES[k])) for k, m in CL.CURVE_POINTS)
    if months <= pts[0][0]:
        return pts[0][1]
    if months >= pts[-1][0]:
        return pts[-1][1]
    for (m0, y0), (m1, y1) in zip(pts, pts[1:]):
        if m0 <= months <= m1:
            return y0 + (y1 - y0) * (months - m0) / Decimal(m1 - m0)
    raise AssertionError("unreachable")


def months_to(d: date) -> Decimal:
    return (Decimal((d - TODAY).days) / Decimal("30.4375")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


def _likes(col: str, pats) -> str:
    return "(" + " OR ".join(f"{col} LIKE '{p}'" for p in pats) + ")"


SECS_SQL = ("SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE "
            + _likes("id_value", IDENT_PATTERNS))


def fixture_scope(table: str) -> str:
    orgs, users, hhs, runs = _arr(FIXTURE_ORGS), _arr(ALL_USERS), _arr(HOUSEHOLDS), _arr(FIXTURE_RUNS)
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({hhs})",
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "portfolio.securities_global": f"id IN ({SECS_SQL}) OR name LIKE '{NAME}%'",
        "portfolio.securities_global_identifiers": f"global_security_id IN ({SECS_SQL})",
        "portfolio.security_reference_attributes": f"global_security_id IN ({SECS_SQL}) OR run_id = ANY({runs})",
        "portfolio.security_master_review": f"global_security_id IN ({SECS_SQL}) OR run_id = ANY({runs}) OR "
                                            + _likes("subject_key", tuple("%" + p for p in IDENT_PATTERNS)),
        "portfolio.index_levels": _likes("index_key", KEY_PATTERNS),
        "portfolio.data_quality_events": _likes("subject_key", KEY_PATTERNS),
        "portfolio.provider_call_log": f"run_id = ANY({runs})",
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
        "provider rows (data_providers, every live + history row)": await fingerprint(
            conn, "SELECT key, status, valid_from, valid_to, system_to FROM portfolio.data_providers"),
        "platform rank (data_source_rank)": await fingerprint(
            conn, "SELECT data_type, provider_key, rank, valid_to, system_to FROM portfolio.data_source_rank "
                  "WHERE owner_scope = 'platform'"),
        "cash_policies (real, the Olivo demo's included)": await fingerprint(
            conn, "SELECT * FROM public.cash_policies WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "cash_obligations (real, demo included)": await fingerprint(
            conn, "SELECT * FROM public.cash_obligations WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "accounts (real, demo included)": await fingerprint(
            conn, "SELECT id, tax_status, registration_type, valid_to, system_to FROM public.accounts WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "account_balances_daily (real)": await fingerprint(
            conn, "SELECT account_id, as_of_date, source_system, cash_value FROM public.account_balances_daily "
                  "WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "tax_profiles (real)": await fingerprint(
            conn, "SELECT * FROM public.tax_profiles WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "the real security master (id, type, archive state)": await fingerprint(
            conn, f"SELECT id, security_type, valid_to, system_to FROM portfolio.securities_global WHERE NOT "
                  f"({fixture_scope('portfolio.securities_global')})"),
        "real index levels (per key: count, latest date)": await fingerprint(
            conn, f"SELECT index_key, provider_key, count(*), max(level_date) FROM portfolio.index_levels WHERE NOT "
                  f"({fixture_scope('portfolio.index_levels')}) GROUP BY 1, 2"),
    }


async def fixture_security_ids(op) -> list[UUID]:
    return [r["global_security_id"] for r in await op.fetch(SECS_SQL)] + [
        r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1", NAME + "%")]


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs = sorted(set(await fixture_security_ids(op)), key=str)
        users, orgs, hhs, runs = list(ALL_USERS), list(FIXTURE_ORGS), list(HOUSEHOLDS), list(FIXTURE_RUNS)
        keys = [r["index_key"] for r in await op.fetch(
            f"SELECT DISTINCT index_key FROM portfolio.index_levels WHERE {fixture_scope('portfolio.index_levels')}")]
        dq = [r["id"] for r in await op.fetch(
            f"SELECT id FROM portfolio.data_quality_events WHERE {fixture_scope('portfolio.data_quality_events')}")]
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [hhs]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            # tables without org_id, by their materialised fixture ids / prefixes (asserted to match no real row)
            "portfolio.index_levels": ("index_key = ANY($1::text[])", [keys]),
            "portfolio.data_quality_events": ("id = ANY($1::uuid[])", [dq]),
            "portfolio.provider_call_log": ("run_id = ANY($1::uuid[])", [runs]),
            "portfolio.security_master_review": ("global_security_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])",
                                                 [secs, runs]),
            "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[]) OR run_id = "
                                                        "ANY($2::uuid[])", [secs, runs]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cash03")
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
    for t in ("public.user_roles", "public.household_memberships", "public.audit_log", "public.member_todos",
              "portfolio.securities_global", "portfolio.securities_global_identifiers",
              "portfolio.security_reference_attributes", "portfolio.security_master_review", "portfolio.index_levels",
              "portfolio.data_quality_events", "portfolio.provider_call_log"):
        n += int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                            ALL_USERS, f"%{TAG}%", fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                            fetch="fetchval"))
    n += sum((await org_table_counts()).values())
    return n


async def org_table_counts() -> dict:
    op = await operator_conn()
    try:
        out = {}
        for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
                "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
                "'information_schema')"):
            n = int(await op.fetchval(f"SELECT count(*) FROM {r['t']} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
            if n:
                out[r["t"]] = n
        return out
    finally:
        await close_conn(op)


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash03"}}
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
        raise AssertionError(f"verify_cash03: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash03: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for fn, want in ((RC.set_clock, ["fn"]), (RC.set_plan_fixture, ["fx"])):
        have = list(inspect.signature(fn).parameters)
        if have != want:
            raise RuntimeError(f"routers.cash.{fn.__name__} signature drifted: {have}")
    # the CallBudget stand-in must match the CURRENT CallBudget interface it replaces
    for m in ("take", "flush", "remaining"):
        if not hasattr(CallBudget, m) or not hasattr(FX.NoBudget, m):
            raise RuntimeError(f"CallBudget / NoBudget interface drifted: {m}")
    have = list(inspect.signature(TF.refresh).parameters)
    if have[:2] != ["pool", "transport"] or "budget" not in have:
        raise RuntimeError(f"treasury_fiscal.refresh signature drifted: {have}")
    have = list(inspect.signature(NM.refresh).parameters)
    if have[:2] != ["pool", "transport"] or "budget" not in have or "ciks" not in have:
        raise RuntimeError(f"edgar_nmfp.refresh signature drifted: {have}")
    have = list(inspect.signature(CI.build).parameters)
    if have[:2] != ["conn", "org_id"] or "fixture" not in have:
        raise RuntimeError(f"cash_instruments.build signature drifted: {have}")
    RC.set_clock(lambda: TODAY)
    RC.set_plan_fixture(dict(PLAN_FIXTURE))


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes, the reuse inventory, earlier cash decisions honoured")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9."):
        check(sec in notes, f"docs/cash/CASH03_NOTES.md has section {sec}")
    for marker in ("portfolio.data_providers", "fred.py", "N-MFP2", "N-MFP3", "sevenDayNetYield",
                   "averagePortfolioMaturity", "auctions_query", "public domain", "tax_status", "profile_for_account",
                   "Matcher", "Reuse inventory", "cash_ladder_rungs", "CP.windows", "account_balances_daily",
                   "cash.tax_estimate", "133 bp"):
        check(marker in notes, f"the notes name {marker!r}")
    check(LADDER_DOC.exists() and "## Ladder rules" in LADDER_DOC.read_text(encoding="utf-8"),
          "docs/cash/CASH_LADDER.md documents the ladder rules, the after-tax method, yield bases and providers")
    if "CASH_TRACK_PLAN_v1.2" in notes and not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md is still not in the repo — nothing was invented from it")
    lt = CL.long_term_window({"near_months": 3, "reserve_months": 12, "horizon_months": 24}, TODAY)
    w = next(x for x in CP.windows(3, 12, 24, TODAY) if x["key"] == "long_term")
    check(lt == w, "the ladder's Long-term window IS cash02's windows()['long_term'] (one formula)", f"{lt} vs {w}")


async def seed_rows_vs_checks(conn) -> tuple[int, list]:
    n, refused = 0, []
    for table, rows in SEED_ROWS.items():
        defs = await platform(conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE contype = "
                                    "'c' AND conrelid = $1::regclass ORDER BY conname", table)
        for d in defs:
            m = re.fullmatch(r"CHECK \((.*)\)( NOT VALID)?", d["d"], re.S)
            if not m:
                refused.append((table, d["conname"], "unparsed"))
                continue
            n += 1
            for i, row in enumerate(rows):
                ok = await platform(conn, f"SELECT ({m.group(1)}) IS NOT FALSE FROM jsonb_populate_record(NULL::{table}, "
                                          "$1::jsonb) AS t", json.dumps(row), fetch="fetchval")
                if ok is not True:
                    refused.append((table, d["conname"], i))
    return n, refused


def seed_literals() -> set[str]:
    src = _code_only(inspect.getsource(seed))
    return set(re.findall(r"'([^'$%{}]+)'", src))


async def y_schema(conn):
    section("[Y] Schema: CHECK / UNIQUE / FK constraints, indexes, policies and the provider rows read BEFORE seeding")
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
    check(got == len(SEED_CHECK_LITERALS) and got > 0 and not bad,
          f"{got} CHECK constraints read; every literal the seeds, providers and bodies use is one the database allows",
          f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(n_chk > 0 and not refused, f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} seeded / provider-written "
          "tables evaluated by the database against the exact row shapes — none refuses", f"{refused}")
    keep = SEED_ROWS["portfolio.security_reference_attributes"]
    SEED_ROWS["portfolio.security_reference_attributes"] = [dict(keep[0], value="4 Nov 2027")]
    try:
        _n, bad_date = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["portfolio.security_reference_attributes"] = keep
    check(("portfolio.security_reference_attributes", "security_reference_attributes_date_chk", 0) in bad_date,
          "the pre-check is not vacuous: a maturity_date that is not an ISO date is refused by the same evaluation "
          "(security_reference_attributes_date_chk)", f"{bad_date}")
    key_rx = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                  "'index_levels_key_chk'", fetch="fetchval")
    keys = [TF.index_key("99C030AA3"), NM.yield_key("C990030001"), NM.wam_key("S990030001"), FIXTURE_CURVE[0][0]]
    m = re.search(r"'(\^.*\$)'", key_rx or "")
    check(bool(m) and all(re.fullmatch(m.group(1).replace("^", "", 1).rstrip("$"), k) for k in keys),
          "every index key the providers and the curve fixture write fits index_levels_key_chk", f"{key_rx} {keys}")
    lits = seed_literals()
    vals = {str(v) for rows in SEED_ROWS.values() for r in rows for v in r.values() if v is not None}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(lits) and not undeclared, f"every quoted literal in seed()'s source ({len(lits)}) is a SEED_ROWS value "
          "or a declared non-column literal", f"{undeclared}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(len(idx) == len(UNIQUES) and len(idx) > 0 and all("UNIQUE" in v for v in idx.values()),
          f"{len(idx)} unique indexes read (one account identity per custodian, one balance per day and source, one "
          "approved tax profile, one live index point per key / date / provider, one class id, one CUSIP …)",
          f"missing={sorted(set(UNIQUES) - set(idx))}")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY(ARRAY['public.accounts', "
              "'public.account_balances_daily', 'portfolio.index_levels', "
              "'portfolio.securities_global_identifiers']::regclass[])")]
    check(len(fks) >= 7, "the seeded tables' foreign keys are read (accounts → org / household / entity; balances → "
          "account; index levels / identifiers → the security master)", f"{len(fks)} {fks}")
    ops = {}
    for t in ("accounts", "account_balances_daily"):
        ops[t] = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                         "tablename = $1", t)}
    check(all(v & {"ALL", "SELECT"} for v in ops.values()), "accounts and account_balances_daily carry RLS policies",
          f"{ops}")
    provs = {r["key"]: dict(r) for r in await platform(
        conn, "SELECT key, kind, status, capabilities, lic_internal_use, lic_member_display, lic_redistribution, "
              "retention_after_cancel, attribution, terms_note, daily_call_budget FROM portfolio.data_providers WHERE "
              "key = ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL", list(PROVIDERS))}
    check(set(provs) == set(PROVIDERS) and all(p["status"] == "not_connected" for p in provs.values()),
          "both providers are registered and SWITCHED OFF (status not_connected) — docs/cash03_part1.sql applied",
          f"{provs}")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{by}")
    snap = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    badc = []
    for t, cols in SEED_COLUMNS.items():
        mm = re.search(rf"^-- ===== {re.escape(t)} =====\n((?:--.*\n)+)", snap, re.M)
        have = set(re.findall(r"^--\s{3}(\w+)\s", mm.group(1), re.M)) if mm else set()
        miss = [c for c in cols if c not in have]
        if not mm or miss:
            badc.append((t, "absent" if not mm else miss))
    check(len(SEED_COLUMNS) > 0 and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables, the providers' "
          "own writes included) exist in docs/schema_snapshot.sql", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and not refused and len(idx) == len(UNIQUES)
                          and by is False and not badc and set(provs) == set(PROVIDERS))


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    g = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
         for t in ("portfolio.securities_global", "portfolio.securities_global_identifiers", "portfolio.index_levels",
                   "portfolio.data_quality_events", "portfolio.security_master_review")}
    real912 = all(not p.startswith("912") for p in (CUSIP_PREFIX,))
    return check(o == 0 and u == 0 and e == 0 and not any(g.values()) and real912,
                 "the reserved prefixes (fixture ids, sign-in tag, 'CASH03 VERIFY' names, CUSIPs 99C03…, SEC ids "
                 "S/C99003…, index keys, the curve prefix) match no real row (real Treasury CUSIPs start 912)",
                 f"orgs={o} users={u} entities={e} global={g}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH03 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH03 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH03 {short.upper()}", sub_of(uid), account_role)
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
        for uid, ents in ((U_ADV, (E_T, E_P, E_O)), (U_ADV2, (E_T, E_P, E_O)), (U_VIEW, (E_T, E_P)), (U_RS, (E_O,))):
            for e in ents:
                await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                                   "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, e, uid)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'staff_visibility.enforced', 'true'::jsonb, 'general')", ORG_A)
        for aid, org in ASSETS.items():
            await conn.execute(
                "INSERT INTO portfolio.assets (id, org_id, name, asset_class, asset_type, ownership_basis, "
                "valuation_method, currency_code, default_taxonomy_key) VALUES ($1, $2, $3, 'financial', 'cash', "
                "'value', 'amortized_cost', 'USD', $4)", aid, org, NAME + "cash", CASH_KEY)
        for owner, asset, mv in POSITIONS:
            await conn.execute(
                "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "market_value, authority, source_system) VALUES ($1, $2, $3, $4, 'value', $5, 'stated', 'manual')",
                ENTS[owner][0], owner, asset, TODAY - timedelta(days=1), Decimal(mv))
        for aid, org, hh, ent, reg, tax, masked, cash in ACCOUNTS:
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id) VALUES ($1, $2, $3, $4, "
                "'cash03verify', $5, $6, $7, $8)", aid, org, masked, f"{TAG}-{masked[-4:]}", reg, tax, ent, hh)
            await conn.execute(
                "INSERT INTO account_balances_daily (org_id, account_id, as_of_date, total_market_value, cash_value, "
                "source_system) VALUES ($1, $2, $3, $4, $4, 'manual')", org, aid, TODAY - timedelta(days=3),
                Decimal(cash))
        await conn.execute(
            "INSERT INTO tax_profiles (org_id, subject_type, subject_id, tax_year, version_no, filing_status, "
            "federal_ordinary_rate, federal_lt_rate, niit_applies, niit_rate, state_code, state_rate, city, city_rate, "
            "as_of, source, source_note, status, entered_by, checker_id, decided_at) VALUES ($1, 'household', $2, $3, "
            "1, 'married_filing_jointly', $4, $5, $6, $7, $8, $9, $10, $11, $12, 'adviser', 'verify fixture', "
            "'approved', $13, $14, now())", ORG_A, H1, TODAY.year, TAX["federal_ordinary_rate"],
            TAX["federal_lt_rate"], TAX["niit_applies"], TAX["niit_rate"], TAX["state_code"], TAX["state_rate"],
            TAX["city"], TAX["city_rate"], TODAY, U_ADV, U_ADV2)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true (privileged "
          "insert path)", f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/2 orgs")
    ok_roles = await check_fixture_roles(conn)
    codes = []
    for key, payer, st, nm, amt, due, cert in OBLIGATIONS:
        hh = H1 if payer in (E_T, E_P) else H2
        code, b = await api(U_ADV, "POST", f"/api/v1/cash/households/{hh}/obligations",
                            {"entity_id": str(payer), "source_type": st, "name": NAME + nm, "amount": amt,
                             "due_date": due, "certainty": cert})
        codes.append(code)
    ok_obl = check(codes == [201] * len(OBLIGATIONS), "the fixture schedule is recorded through the cash01 API (201 "
                   "each)", f"{codes}")
    # the policy, through cash02's maker-checker: U_ADV drafts + submits, U_ADV2 approves
    c1, b1 = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/policies", dict(POLICY))
    pid = (b1.get("policy") or {}).get("id")
    c2, b2 = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/policies/{pid}/submit")
    c3, b3 = await api(U_ADV2, "POST", f"/api/v1/cash/households/{H1}/policies/{pid}/approve", {"note": "verify"})
    S["policy_id"] = pid
    ok_pol = check(c1 == 201 and c2 == 200 and c3 == 200 and (b3.get("policy") or {}).get("status") == "active",
                   "the H1 cash policy (N 3, R 12, horizon 24, 6 rungs) is drafted, submitted and approved by a "
                   "DIFFERENT person through the cash02 API", f"{c1} {c2} {c3} {dumps(b3)[:600]}")
    ok_glob = await seed_global()
    S["seeded"] = bool(ok_roles and ok_obl and ok_pol and ok_glob)


async def seed_global() -> bool:
    """The global fixture rows, through the providers' REAL write paths (Matcher + upsert_index_levels) on the app pool,
    and the fixture curve + staleness rows through security_data.writes."""
    pool = await get_pool()
    tf = await TF.refresh(pool, FX.transport(TODAY), today=TODAY, run_id=RUN_TF, budget=FX.NoBudget())
    nm = await NM.refresh(pool, FX.transport(TODAY), today=TODAY, ciks=FX.FIXTURE_CIKS, run_id=RUN_NM,
                          budget=FX.NoBudget())
    S["tf"], S["nm"] = tf, nm
    for k, m in FIXTURE_CURVE:
        await SDW.upsert_index_levels(pool, k, [(CURVE_DATE, Decimal(CURVE_VALUES[k[len(CURVE_PREFIX):]]))],
                                      provider_key="fred")
    for key, prov, d, v in STALE_ROWS:
        await SDW.upsert_index_levels(pool, key, [(d, Decimal(v))], provider_key=prov)
    return check(tf.master.get("new") == 5 and nm.master.get("new") == 4 and tf.yields_written == 6
                 and nm.yields_written >= 4,
                 "the fixture Treasury issues (5) and N-MFP classes (4) enter the security master through Matcher; "
                 "their yields (6 auction points, 4 class yields + WAM / WAL) through upsert_index_levels",
                 f"tf={tf.master} {tf.yields_written} nm={nm.master} {nm.yields_written}")


async def check_fixture_roles(conn) -> bool:
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
            ok = ok and bool(pr.is_internal) is (rname != "member") and not pr.is_staff
            ok = ok and AZ.is_member_principal(pr) is (rname == "member")
            ok = ok and (("manage_cash_policy" in perms) is (rname in ("advisor", "support_staff")))
            if not ok:
                bad.append((short, names, sorted(perms & keys), bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisor / support staff "
                 "hold manage_cash_policy, compliance and members do not; none is a staff bypass)", f"{bad}")


# ── Providers ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_providers(conn):
    section("[Y] Providers: fixtures parse to the expected rows; bad payloads refused with a reason; licence facts; "
            "switched off; cadence; staleness fires")
    p1 = TF.parse_auctions(FX.text("tfd_auctions_p1.json"))
    p2 = TF.parse_auctions(FX.text("tfd_auctions_p2.json"))
    got = {(a.cusip, a.auction_date): (a.security_type, a.maturity_date, a.yield_pct, a.basis)
           for a in p1.auctions + p2.auctions}
    want = {("99C030AA3", date(2026, 11, 3)): ("Bill", date(2027, 11, 4), Decimal("4.010"), TF.BASIS_INVESTMENT),
            ("99C030AD7", date(2026, 11, 3)): ("Bill", date(2027, 5, 6), Decimal("4.120"), TF.BASIS_INVESTMENT),
            ("99C030AB1", date(2026, 10, 27)): ("Note", date(2028, 10, 31), Decimal("4.375"), TF.BASIS_YIELD),
            ("99C030AC9", date(2025, 12, 22)): ("Note", date(2027, 12, 31), Decimal("3.900"), TF.BASIS_YIELD),
            ("99C030AE5", date(2026, 10, 23)): ("TIPS", date(2031, 10, 31), Decimal("1.850"), TF.BASIS_YIELD),
            ("99C030AB1", date(2026, 11, 5)): ("Note", date(2028, 10, 31), Decimal("4.400"), TF.BASIS_YIELD)}
    check(got == want and all(isinstance(v[2], Decimal) for v in got.values()),
          "Treasury Fiscal Data fixtures → the expected rows (CUSIP, type, maturity, the auction yield as Decimal: a "
          "bill's high investment rate, a note's high yield; a reopening is a second auction)", f"{got}")
    check(p1.rejected == [(4, "CUSIP '99C030AF9' is not a valid 9-character CUSIP")] and not p2.rejected
          and p1.total_pages == 2, "a row with a bad CUSIP check digit is skipped WITH its reason; the rest stand",
          f"{p1.rejected} {p2.rejected}")
    reasons = {}
    for f, want_why in (("tfd_bad_not_json.txt", "not JSON"), ("tfd_bad_partial.json", "partial page"),
                        ("tfd_bad_no_data.json", "no 'data' list")):
        try:
            TF.parse_auctions(FX.text(f))
            reasons[f] = None
        except TF.PayloadRejected as exc:
            reasons[f] = str(exc)
    check(all(r and w in r for (f, w), r in zip((("tfd_bad_not_json.txt", "not JSON"),
                                                ("tfd_bad_partial.json", "partial page"),
                                                ("tfd_bad_no_data.json", "no 'data' list")), reasons.values())),
          "bad / partial Treasury payloads are REFUSED whole, each with its reason", f"{reasons}")
    f1, f2, f3 = (NM.parse_nmfp(FX.text(n)) for n in ("nmfp_treasury.xml", "nmfp_government.xml", "nmfp_muni.xml"))
    got_m = {c.class_id: (f.series_id, f.category, c.yield_pct, c.yield_date) for f in (f1, f2, f3) for c in f.classes}
    want_m = {"C990030001": ("S990030001", "treasury", Decimal("4.5"), date(2026, 10, 31)),
              "C990030002": ("S990030002", "government", Decimal("4.5"), date(2026, 10, 31)),
              "C990030003": ("S990030002", "government", Decimal("4.6"), date(2026, 10, 31)),
              "C990030004": ("S990030003", "other_tax_exempt", Decimal("3.1"), date(2026, 10, 31))}
    check(got_m == want_m and f1.wam_days == Decimal(32) and f1.wal_days == Decimal(88),
          "N-MFP fixtures → each class's 7-day net yield in percent (fraction × 100, Decimal) with its as-of (the "
          "latest dated value), the category, WAM / WAL", f"{got_m} wam={f1.wam_days} wal={f1.wal_days}")
    nm_why = {}
    for f, w in (("nmfp_bad_no_yield.xml", "no 7-day net yield"), ("nmfp_bad_percent.xml", "not a fraction"),
                 ("nmfp_bad_doctype.xml", "DOCTYPE")):
        try:
            NM.parse_nmfp(FX.text(f))
            nm_why[f] = None
        except NM.FilingRejected as exc:
            nm_why[f] = (w in str(exc), str(exc))
    check(all(v and v[0] for v in nm_why.values()), "bad N-MFP filings are REFUSED with a reason (no yield, a "
          "percent where a fraction belongs, a DOCTYPE never expanded)", f"{nm_why}")
    metas = NM.filings_from_index(FX.text("edgar_master_idx.txt"))
    check(sorted(m.form_type for m in metas) == ["N-MFP2", "N-MFP3", "N-MFP3", "N-MFP3"]
          and all(m.archive_base.startswith("https://www.sec.gov/Archives/edgar/data/") for m in metas),
          "N-MFP discovery reuses edgar_index.parse_index_lines (forms N-MFP2 / N-MFP3; the 424B2 line ignored) and "
          "edgar_fetch's archive layout", f"{[(m.cik, m.form_type) for m in metas]}")
    # licence facts on the registry rows; the code modules
    provs = {r["key"]: dict(r) for r in await platform(
        conn, "SELECT key, kind, status, capabilities, lic_internal_use, lic_member_display, lic_redistribution, "
              "retention_after_cancel, attribution FROM portfolio.data_providers WHERE key = ANY($1::text[]) AND "
              "valid_to IS NULL AND system_to IS NULL", list(PROVIDERS))}
    lic_ok = all(p["lic_internal_use"] is True and p["lic_member_display"] == "allowed" and p["lic_redistribution"]
                 is True and p["retention_after_cancel"] == "keep" and bool(p["attribution"])
                 and set(p["capabilities"]) == {"index_level", "reference"} for p in provs.values())
    check(len(provs) == 2 and lic_ok and provs["treasury_fiscal"]["kind"] == "public_api"
          and provs["sec_nmfp"]["kind"] == "regulator_filing"
          and provs["treasury_fiscal"]["attribution"] == TF.LICENCE["attribution"]
          and provs["sec_nmfp"]["attribution"] == NM.LICENCE["attribution"],
          "registry rows carry the licence facts (public domain / SEC public: internal use, member display allowed, "
          "redistribution, keep) and the code's LICENCE mirrors them", f"{provs}")
    ranks = {(r["data_type"], r["provider_key"]): r["rank"] for r in await platform(
        conn, "SELECT data_type, provider_key, rank FROM portfolio.data_source_rank WHERE owner_scope = 'platform' "
              "AND provider_key = ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL", list(PROVIDERS))}
    check(set(ranks) == {("index_level", "treasury_fiscal"), ("index_level", "sec_nmfp"),
                         ("reference", "treasury_fiscal"), ("reference", "sec_nmfp")},
          "both are in the platform rank for index_level and reference (after every existing provider)", f"{ranks}")
    code = build_providers()
    mods_ok = all(k in code and getattr(code[k], "connected", False) and not code[k].fetches
                  and code[k].capabilities == frozenset({"index_level", "reference"}) for k in PROVIDERS)
    check(mods_ok, "both code modules are in the security-data code registry, connected (keyless — a Super Admin MAY "
          "switch them on), and never fetched by marketdata1's refresh (no 'fetches')", f"{list(code)}")
    # switched off: the budget refuses, so the real write path refuses; the nightly step leaves them alone
    pool = await get_pool()
    refused = None
    try:
        await CallBudget(pool, "treasury_fiscal", today=TODAY).load()
    except RuntimeError as exc:
        refused = str(exc)
    check(refused is not None and "not_connected" in refused, "switched OFF: a fetch is refused before any request "
          "(CallBudget: 'not_connected — not fetched')", f"{refused}")
    run = SN.RunResult(_id(950), "verify")
    fake_mods = {k: _CountingProvider() for k in PROVIDERS}
    await SN.cash_sources(pool, fake_mods, TODAY, run, ciks_fn=_no_ciks)
    check(all(m.calls == 0 for m in fake_mods.values()) and not any(k.startswith("cash:") for k in run.fetched),
          "the nightly's cash_sources step does NOT run a provider that is not active (both ship not_connected)",
          f"{[m.calls for m in fake_mods.values()]} {run.fetched}")
    import services.security_data.nightly as SNmod
    check("cash_sources" in inspect.signature(SNmod.run_nightly).parameters["steps"].default,
          "cash_sources is wired into the existing nightly job's default steps")
    cad = [SN.cash_source_due("treasury_fiscal", TODAY, None), SN.cash_source_due("treasury_fiscal", TODAY, TODAY),
           SN.cash_source_due("treasury_fiscal", TODAY, TODAY - timedelta(days=1)),
           SN.cash_source_due("sec_nmfp", date(2026, 11, 7), None), SN.cash_source_due("sec_nmfp", TODAY, None),
           SN.cash_source_due("sec_nmfp", TODAY, date(2026, 11, 8)), SN.cash_source_due("sec_nmfp", TODAY,
                                                                                         date(2026, 10, 9))]
    check(cad == [True, False, True, False, True, False, True], "cadence: Treasury daily (once per day); N-MFP monthly "
          "from the 8th (once per calendar month)", f"{cad}")
    # staleness: THE rule with the per-provider limits, fired by the platform-quality step on the fixture keys only
    lim = (SN.stale_limit("treasury_fiscal", "index_level"), SN.stale_limit("sec_nmfp", "index_level"),
           SN.stale_limit("fred", "index_level"))
    check(lim == (TF.STALE_DAYS, NM.STALE_DAYS, SDS.STALE_DAYS_DEFAULT["index_level"]) == (10, 45, 5),
          "staleness limits: Treasury 10 days, N-MFP 45 days, every other provider its data type's default (5)",
          f"{lim}")
    keys = [k for k, _p, _d, _v in STALE_ROWS] + [NM.yield_key("C990030001"), TF.index_key("99C030AA3")]
    run = SN.RunResult(_id(951), "verify")
    await SN.platform_quality(pool, TODAY, run, only_keys=keys)
    ev = {(r["subject_key"], r["kind"]) for r in await platform(
        conn, "SELECT subject_key, kind FROM portfolio.data_quality_events WHERE subject_key = ANY($1::text[]) AND "
              "valid_to IS NULL AND system_to IS NULL AND kind = 'stale'", keys)}
    check(ev == {(STALE_ROWS[0][0], "stale"), (STALE_ROWS[1][0], "stale")},
          "staleness fires: a 20-day-old auction yield (limit 10) and a 70-day-old N-MFP yield (limit 45) are 'stale'; "
          "a 9-day-old N-MFP yield is NOT (it would be at the default 5) nor a 6-day-old auction", f"{sorted(ev)}")


class _CountingProvider:
    connected = True
    capabilities = frozenset({"index_level", "reference"})
    fetches = frozenset()

    def __init__(self) -> None:
        self.calls = 0

    async def refresh(self, pool, **kw):
        self.calls += 1
        raise AssertionError("a switched-off provider was refreshed")


async def _no_ciks(conn):
    return set()


# ── Security master ───────────────────────────────────────────────────────────────────────────────────────────
async def y_master(conn):
    section("[Y] Instruments resolve to the security master; none created outside its load path")
    rows = await platform(conn, "SELECT g.id, g.security_type, g.name, i.id_type, i.id_value FROM "
                                "portfolio.securities_global g JOIN portfolio.securities_global_identifiers i ON "
                                "i.global_security_id = g.id AND i.valid_to IS NULL AND i.system_to IS NULL WHERE "
                                f"g.id IN ({SECS_SQL})")
    by = {}
    for r in rows:
        by.setdefault(r["id"], {"type": r["security_type"], "name": r["name"], "ids": {}})["ids"].setdefault(
            r["id_type"], set()).add(r["id_value"])
    cusips = {next(iter(v["ids"]["cusip"])): v for v in by.values() if "cusip" in v["ids"]}
    classes = {next(iter(v["ids"]["sec_class_id"])): v for v in by.values() if "sec_class_id" in v["ids"]}
    check(set(cusips) == {"99C030AA3", "99C030AD7", "99C030AB1", "99C030AC9", "99C030AE5"}
          and all(v["type"] == "fixed_income" and SYM.isin_from_cusip(c) in v["ids"].get("isin", set())
                  for c, v in cusips.items()) and "99C030AF9" not in str(by),
          "the 5 valid Treasury issues are fixed-income securities with their CUSIP and derived ISIN (the bad row is "
          "absent)", f"{sorted(cusips)}")
    check(set(classes) == {"C990030001", "C990030002", "C990030003", "C990030004"}
          and all(v["type"] == "fund" and v["ids"].get("sec_series_id") for v in classes.values())
          and "C990030005" not in classes,
          "the 4 N-MFP share classes are funds created BY SEC CLASS ID (no ticker) with their series id; the rejected "
          "filing's class is absent", f"{sorted(classes)}")
    # cash03 fix 1 — the reported bug reproduced, then the fix: Matcher deduped a batch on (cusip, figi, ticker,
    # exchange family), which is (None, None, None, None) for EVERY N-MFP class record → 1 security of 4
    from services import security_master_load as SML
    recs = [r for n in ("nmfp_treasury.xml", "nmfp_government.xml", "nmfp_muni.xml")
            for r in NM.source_records(NM.parse_nmfp(FX.text(n)), TODAY)]
    old_keys = {(r.cusip, r.figi, r.ticker, SYM.family(r.exchange)) for r in recs}
    new_keys = {SML.batch_key(r) for r in recs}
    shared = [s for s in {r.extra_ids["sec_series_id"] for r in recs}
              if sum(v["ids"].get("sec_series_id") == {s} for v in classes.values()) > 1]
    check(len(recs) == 4 and len(old_keys) == 1 and len(new_keys) == 4 and shared == ["S990030002"],
          "fix 1: the pre-fix batch key collapses the 4 id-less class records to 1 (the reported 'new 1'); "
          "Matcher.batch_key (with sec_class_id) keeps all 4, and two classes of S990030002 share the series id "
          "(a SHARED id — no conflict)", f"records {len(recs)} old {len(old_keys)} new {len(new_keys)} {shared}")
    # cash03 fix 2 — batch_key is SHARED Matcher code: a record carrying a CUSIP / FIGI / ticker keys EXACTLY as before,
    # even with a unique extra id (sec_mutual_funds: ticker + sec_class_id — a ticker under two class ids stays ONE)
    from services.security_data.providers.reference_common import SourceRecord as _SR
    smf = [_SR("sec_mutual_funds", ticker=t, exchange="US", extra_ids={"sec_class_id": c, "sec_series_id": "S990030009"})
           for t, c in (("ZZCAA", "C990030091"), ("ZZCAB", "C990030092"), ("ZZCAA", "C990030093"))]
    other = [_SR("openfigi", ticker="ZZCAA", figi="BBG0ZZCAA001", exchange="XNAS"), _SR("x", cusip="99C030AA3")]
    check(all(SML.batch_key(r) == (r.cusip, r.figi, r.ticker, SYM.family(r.exchange)) for r in smf + other)
          and len({SML.batch_key(r) for r in smf}) == 2,
          "fix 2: a record with a CUSIP / FIGI / ticker keeps the pre-fix batch key even with a sec_class_id "
          "(sec_mutual_funds' first-ticker-wins dedup unchanged)", f"{[SML.batch_key(r) for r in smf]}")
    attrs = {(r["field"], r["value"], r["provider_key"]) for r in await platform(
        conn, "SELECT field, value, provider_key FROM portfolio.security_reference_attributes WHERE global_security_id "
              f"IN ({SECS_SQL}) AND valid_to IS NULL AND system_to IS NULL")}
    check({("maturity_date", "2027-11-04", "treasury_fiscal"), ("treasury_type", "Bill", "treasury_fiscal"),
           ("treasury_type", "TIPS", "treasury_fiscal"), ("mmf_category", "treasury", "sec_nmfp"),
           ("mmf_category", "other_tax_exempt", "sec_nmfp"), ("fund_structure", "money_market", "sec_nmfp")} <= attrs,
          "their reference attributes are written per provider (maturity, type, category, structure)",
          f"{sorted(attrs)[:12]}")
    n_new = (S["tf"].master.get("new") or 0) + (S["nm"].master.get("new") or 0)
    check(len(by) == n_new == 9, "every fixture security was created by Matcher (the count equals the load path's own "
          "'new' counts) — none outside it", f"{len(by)} vs {n_new}")
    side = [r["t"] for r in await platform(conn, "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN "
                                                 "pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND "
                                                 "c.relname ~ '(cash_ladder|treasury_securit|mmf_yield|nmfp)'")]
    check(side == [], "no side table exists for issues, funds or rungs (cash_ladder_rungs deliberately not built — "
          "CASH03_NOTES D3)", f"{side}")
    S["sec_by_cusip"] = {c: str(k) for k, v in by.items() for c in v["ids"].get("cusip", ())}
    S["sec_by_class"] = {c: str(k) for k, v in by.items() for c in v["ids"].get("sec_class_id", ())}


# ── Ladder (pure, hand-computed) ──────────────────────────────────────────────────────────────────────────────
def _fields(**over) -> dict:
    f = {"near_months": 3, "reserve_months": 12, "horizon_months": 24, "rung_count": 6, "buffer_months": ZERO,
         "objective": "balanced", "objective_score": 3, "preset": "custom", "min_coverage_months": Decimal(6)}
    f.update(over)
    return f


def _lines() -> list[dict]:
    out = []
    for key, _payer, st, nm, amt, due, cert in OBLIGATIONS:
        if key == "other":
            continue
        out.append({"key": key, "date": date.fromisoformat(due), "amount": Decimal(amt), "counted": True,
                    "certainty": cert, "name": nm, "source_type": st})
    return out


def _issues() -> list[dict]:
    tf = TF.parse_auctions(FX.text("tfd_auctions_p1.json")).auctions + TF.parse_auctions(
        FX.text("tfd_auctions_p2.json")).auctions
    return [{"cusip": a.cusip, "security_id": f"sec-{a.cusip}", "name": a.name(), "treasury_type": a.security_type,
             "maturity": a.maturity_date} for a in TF.issues(tf)]


def _auctions() -> dict:
    tf = TF.parse_auctions(FX.text("tfd_auctions_p1.json")).auctions + TF.parse_auctions(
        FX.text("tfd_auctions_p2.json")).auctions
    out = {}
    for a in sorted(tf, key=lambda a: a.auction_date):
        out[a.cusip] = {"found": True, "yield_pct": a.yield_pct, "as_of": a.auction_date}
    return out


def _curve() -> list[dict]:
    return [{"key": k, "months": m, "yield_pct": Decimal(CURVE_VALUES[k]), "as_of": CURVE_DATE}
            for k, m in CL.CURVE_POINTS]


#: the hand-computed ladder for the fixture schedule (N 3, R 12, horizon 24, 6 rungs, today 2026-11-09):
#: Long-term = [2027-11-09, 2028-11-09); target 735,000 → large ≥ 73,500: l1 400,000 (10 %), l2 300,000 (firm ≥ 250k);
#: free months 12,13,15,16,17,18,20,21,22,23 → 4 fillers at indices 0,2,5,7 → months 12, 15, 18, 21
EXPECTED = [  # (kind, target date, maturity, cusip | None, type, par, targets, funds) — obligations by NAME
    ("filler", date(2027, 11, 9), date(2027, 11, 4), "99C030AA3", "bill", Decimal("15000"), [], ["Club dues"]),
    ("targeted", date(2028, 1, 14), date(2027, 12, 31), "99C030AC9", "note", Decimal("400000"),
     ["January estimated tax"], []),
    ("filler", date(2028, 2, 9), date(2028, 2, 9), None, "note", Decimal("20000"), [], ["Lifestyle top-up"]),
    ("filler", date(2028, 5, 9), date(2028, 5, 9), None, "note", ZERO, [], []),
    ("targeted", date(2028, 7, 4), date(2028, 7, 3), None, "note", Decimal("300000"), ["Pledge (4 July)"], []),
    ("filler", date(2028, 8, 9), date(2028, 8, 9), None, "note", ZERO, [], []),
]


def _shape(lad: dict, names: dict) -> list[tuple]:
    """The rungs with each funded obligation by its NAME (the fixture prefix stripped): the pure lines carry short
    keys, the ledger's carry lineage keys — the names are the same in both."""
    def nm(keys):
        return [(names.get(k) or k).removeprefix(NAME) for k in keys]

    return [(r["kind"], r["target_date"], r["maturity"], r["cusip"], r["instrument_type"], r["par"], nm(r["targets"]),
             nm(r["funds"])) for r in lad["rungs"]]


def _names(lines: list[dict]) -> dict:
    return {ln["key"]: ln.get("name") for ln in lines}


async def y_ladder(conn):
    section("[Y] Ladder rules, hand-computed (targeted + filler, rung count, business days, indicative fallback)")
    lad = CL.build_ladder(_fields(), _lines(), TODAY, issues=_issues(), auctions=_auctions(), curve=_curve())
    check(_shape(lad, _names(_lines())) == EXPECTED, "the fixture schedule → exactly the hand-computed 6 rungs (2 targeted on the large "
          "obligations, 4 monthly fillers; real issues where one matures within 35 days before; small obligations on "
          "the latest earlier rung)", f"{_shape(lad, _names(_lines()))}")
    check(lad["target"] == Decimal("735000") and lad["par_total"] == Decimal("735000") and lad["leftover"] == ZERO
          and isinstance(lad["par_total"], Decimal) and all(isinstance(r["par"], Decimal) for r in lad["rungs"]),
          "Decimal as Decimal: target 735,000 = Σ par, no leftover", f"{lad['target']} {lad['par_total']}")
    holiday = lad["rungs"][4]
    check(BD.holiday_name(date(2028, 7, 4)) == "independence_day" and holiday["maturity"] == date(2028, 7, 3)
          and holiday["indicative"] is True and holiday["label"] == CL.INDICATIVE
          and "moved to the prior business day 2028-07-03" in (holiday["adjustment"] or ""),
          "a holiday maturity (4 July 2028) moves to the prior business day, and the rung says so", f"{holiday}")
    r1, r2, r3 = lad["rungs"][0], lad["rungs"][1], lad["rungs"][2]
    want2 = curve_at(months_to(date(2027, 12, 31)))
    want3 = curve_at(months_to(date(2028, 2, 9)))
    check(r1["yield"]["yield_pct"] == Decimal("4.010") and r1["yield"]["source"] == "auction"
          and r1["yield"]["as_of"] == date(2026, 11, 3)
          and r2["yield"]["source"] == "curve" and r2["yield"]["yield_pct"] == want2
          and "more than 35 days old" in r2["yield"]["basis"]
          and r3["yield"]["yield_pct"] == want3 and "interpolated" in r3["yield"]["basis"],
          "yields carry basis + as-of: a fresh auction's own yield (4.010, 2026-11-03); a stale auction falls back to "
          "the curve (interpolated in months, said so) — equal to this script's own interpolation",
          f"{r1['yield']} {r2['yield']} want {want2} {r3['yield']} want {want3}")
    # rung count respected: 3 large dates, 2 rungs → the smallest is demoted and funded as small
    lines3 = _lines() + [{"key": "l3", "date": date(2028, 9, 15), "amount": Decimal("280000"), "counted": True,
                          "certainty": "firm", "name": "third", "source_type": "one_off"}]
    lad2 = CL.build_ladder(_fields(rung_count=2), lines3, TODAY, issues=[], curve=_curve())
    kinds = [(r["kind"], r["targets"]) for r in lad2["rungs"]]
    check(len(lad2["rungs"]) == 2 and kinds == [("targeted", ["l1"]), ("targeted", ["l2"])]
          and "l3" in lad2["rungs"][1]["funds"] and any("is large but the 2 rungs are taken" in n for n in lad2["notes"]),
          "the rung count is respected: 3 large dates and 2 rungs → the two largest are targeted, the third is funded "
          "as a small obligation and the note says so", f"{kinds} {lad2['notes']}")
    # leftover spread across the fillers to the cent (odd cents to the earliest)
    lad3 = CL.build_ladder(_fields(), _lines(), TODAY, issues=_issues(), auctions=_auctions(), curve=_curve(),
                           sleeve_amount=Decimal("735100.03"))
    fl = [r["leftover"] for r in lad3["rungs"] if r["kind"] == "filler"]
    check(fl == [Decimal("25.01"), Decimal("25.01"), Decimal("25.01"), Decimal("25.00")]
          and lad3["par_total"] == Decimal("735100.03"),
          "leftover sleeve $ (100.03) spread across the 4 fillers to the cent, odd cents to the earliest", f"{fl}")
    # no matching issue → indicative and labelled; no curve → 'needs the Treasury curve'
    lad4 = CL.build_ladder(_fields(), _lines(), TODAY, issues=[], curve=[])
    check(all(r["indicative"] and r["label"] == CL.INDICATIVE for r in lad4["rungs"])
          and all(r["yield"] == {"estimated": False, "text": CL.NO_CURVE} for r in lad4["rungs"])
          and lad4["yield"]["estimated"] is False,
          "no matching issue → every rung indicative and labelled; no curve → 'not estimated — needs the Treasury "
          "curve' (never a number)", f"{[r['yield'] for r in lad4['rungs']][:2]}")
    # the threshold, both arms, and below it
    t = Decimal("1000000")
    arms = (CL.is_large({"amount": Decimal("100000"), "certainty": "estimated"}, t)[0],
            CL.is_large({"amount": Decimal("99999.99"), "certainty": "estimated"}, t)[0],
            CL.is_large({"amount": Decimal("250000"), "certainty": "firm"}, Decimal("10000000"))[0],
            CL.is_large({"amount": Decimal("249999.99"), "certainty": "firm"}, Decimal("10000000"))[0],
            CL.is_large({"amount": Decimal("250000"), "certainty": "estimated"}, Decimal("10000000"))[0])
    check(arms == (True, False, True, False, False), "the threshold: ≥ 10 % of the Long-term target, or FIRM and "
          "≥ $250,000 (an estimated $250,000 at under 10 % is not large)", f"{arms}")
    hol = sorted(BD.holidays(2027))
    want_h = [date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 5, 31), date(2027, 6, 18),
              date(2027, 7, 5), date(2027, 9, 6), date(2027, 10, 11), date(2027, 11, 11), date(2027, 11, 25),
              date(2027, 12, 24)]
    check(hol == want_h and BD.prior_business_day(date(2028, 9, 9)) == date(2028, 9, 8)
          and BD.date(2028, 1, 1) not in BD.holidays(2027) and BD.is_business_day(date(2027, 12, 31)),
          "the business-day calendar: the 2027 federal holidays with their observed dates (Juneteenth Fri 18 Jun, 4 "
          "July Mon 5 Jul, Christmas Fri 24 Dec); a Saturday moves to Friday; New Year 2028 (a Saturday) is not "
          "observed on 31 Dec", f"{hol}")
    S["ladder"] = lad


# ── After-tax (pure) ──────────────────────────────────────────────────────────────────────────────────────────
def _cand(kind, pct, sid=None):
    return {"kind": kind, "security_id": sid or kind, "name": kind,
            "net": {"estimated": True, "yield_pct": Decimal(pct), "as_of": date(2026, 10, 31)}}


def _choose(cands, account, profile):
    treat = CI.treatment(account)
    r = CI.rates(profile) if treat["tax_applies"] else None
    cs = [dict(c, after_tax=CI.after_tax(c["kind"], c["net"], treat, r)) for c in cands]
    return CI.choose(cs, treat, r), cs


async def y_after_tax(conn):
    section("[Y] After-tax selection by account treatment + profile; labels where data is missing; the 133 bp case")
    ny = {"federal_ordinary_rate": "0.37", "niit_applies": True, "niit_rate": "0.038", "state_rate": "0.109",
          "city_rate": "0.03876", "tax_year": 2026, "as_of": TODAY, "id": "ny", "state_code": "NY"}
    ct = {"federal_ordinary_rate": "0.37", "niit_applies": True, "niit_rate": "0.038", "state_rate": "0.0699",
          "city_rate": "0", "tax_year": 2026, "as_of": TODAY, "id": "ct", "state_code": "CT"}
    taxable = {"tax_status": "taxable", "registration_type": "trust"}
    eq = [_cand("treasury_mmf", "4.50"), _cand("government_mmf", "4.50")]
    for nm, prof in (("NY", ny), ("CT", ct)):
        ch, cs = _choose(eq, taxable, prof)
        sl = Decimal(prof["state_rate"]) + Decimal(prof["city_rate"])
        want_gap = ((Decimal("4.50") * (1 - FED) - Decimal("4.50") * (1 - FED - sl)) * 100).quantize(Decimal("0.1"))
        check(ch["chosen"]["kind"] == "treasury_mmf" and ch["runner_up"]["kind"] == "government_mmf"
              and ch["gap_bp"] == want_gap and ch["basis"] == "after_tax" and "state- and local-tax exempt" in ch["why"],
              f"{nm} taxable owner: Treasury MMF preferred over a taxable (Government) MMF at EQUAL yield — {want_gap} bp "
              "after tax, and the why says the Treasury is state- and local-tax exempt", f"{ch['why']} {ch['gap_bp']}")
    ira = {"tax_status": "tax_deferred", "registration_type": "ira"}
    mix = [_cand("treasury_mmf", "4.50"), _cand("government_mmf", "4.60"), _cand("muni_mmf", "3.10")]
    ch, cs = _choose(mix, ira, ny)
    check(ch["chosen"]["kind"] == "government_mmf" and ch["basis"] == "net" and ch["gap_bp"] == Decimal("10.0")
          and all(c["after_tax"]["yield_pct"] == c["net"]["yield_pct"] for c in cs),
          "IRA (tax-deferred): the highest NET yield wins regardless of exemption (Government 4.60 over Treasury 4.50); "
          "after-tax = net", f"{ch['why']}")
    foundation = {"tax_status": "tax_exempt", "registration_type": "foundation"}
    ch_f, _cs = _choose(mix, foundation, None)
    check(ch_f["chosen"]["kind"] == "government_mmf", "a tax-exempt foundation account: the same — highest net",
          f"{ch_f['why']}")
    ch, cs = _choose(eq, taxable, None)
    check(all(c["after_tax"] == {"estimated": False, "text": CI.NEEDS_PROFILE} for c in cs)
          and ch["basis"] == "net" and "no approved tax profile" in ch["why"],
          "no profile → net only, labelled 'after-tax: needs a tax profile', and the why says the choice is on net",
          f"{cs[0]['after_tax']} {ch['why']}")
    h = CI.cd_hurdle(Decimal("4.00"), CI.rates(ny))
    want_eq = Decimal("4.00") * (1 - FED) / (1 - FED - SL)
    check(h["hurdle_bp"] == Decimal("133") and h["equivalent_pct"] == want_eq and q3(want_eq) == Decimal("5.330"),
          "the roadmap fact: a NY-domiciled taxable member's T-bill at 4.00 % beats a CD unless the CD yields 5.330 % — "
          "a 133 bp hurdle at the profile's rates", f"{h}")
    muni_only = [{"kind": "muni_ladder", "security_id": None, "name": None,
                  "net": {"estimated": False, "text": CI.KINDS["muni_ladder"]["unavailable"]}}]
    ch, _cs = _choose(muni_only, taxable, ny)
    cands = CI.candidates("long_term", mmfs={}, bill=None, ladder={"yield": {"estimated": True,
                                                                             "yield_pct": Decimal("3.9"),
                                                                             "as_of": TODAY}})
    ch2, _ = _choose(cands, taxable, ny)
    check(ch["chosen"] is None and "muni yield: needs a source" in ch["why"]
          and ch2["chosen"]["kind"] == "treasury_ladder" and ch2["runner_up"] is None,
          "a muni without a yield source reads 'muni yield: needs a source' and is NEVER chosen (alone: nothing is "
          "chosen; beside the Treasury ladder: the ladder, no runner-up)", f"{ch['why']} / {ch2['why']}")
    m = CI.after_tax("muni_mmf", {"estimated": True, "yield_pct": Decimal("3.10")}, CI.treatment(taxable), CI.rates(ny))
    check(m["yield_pct"] == Decimal("3.10") * (1 - SL) and m["rate"] == SL,
          "a muni MMF is federally exempt and state / city taxed (domicile not recorded — conservative)", f"{m}")
    w, why = CI.weights([{"id": "a", "cash_value": None}, {"id": "b", "cash_value": None}])
    w1, _w = CI.weights([{"id": "a", "cash_value": None}])
    w2, _w = CI.weights([{"id": "a", "cash_value": Decimal("600000")}, {"id": "b", "cash_value": Decimal("400000")}])
    check(w is None and why == CI.NOT_ALLOCATED and w1 == {"a": Decimal(1)}
          and w2 == {"a": Decimal("0.6"), "b": Decimal("0.4")},
          "allocation: by cash balance; one account → 1; several without a balance → 'not allocated — needs each "
          "account's cash balance'", f"{w} {w1} {w2}")


# ── The plan through the DB (activation rolled back) ──────────────────────────────────────────────────────────
async def report_and_view():
    tokens = set_rls_context(str(ORG_A), False)
    try:
        pool = await get_pool()
        async with pool.acquire() as c:
            rep = await CC.household_report(c, ORG_A, H1, today=TODAY)
            fields = CP.fields_of(await CP.load(c, ORG_A, S["policy_id"]))
    finally:
        reset_rls_context(tokens)
    lines = CC.scope_lines(rep, "household")
    view = CP.evaluate(fields, lines, CC.scope_liquid(rep, "household"), {}, None, TODAY)
    return rep, fields, lines, view


async def build_activated(conn, purpose: str = "internal"):
    """CI.build on the verify's own connection inside ONE transaction that switches both providers ON and is rolled
    back (the real provider rows never change — fingerprinted)."""
    rep, fields, lines, view = await report_and_view()
    out = {}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            n = await conn.execute("UPDATE portfolio.data_providers SET status = 'active' WHERE key = ANY($1::text[]) "
                                   "AND valid_to IS NULL AND system_to IS NULL", list(PROVIDERS))
            out["flipped"] = int(n.split()[-1])
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true), "
                "set_config('app.current_auth0_sub', '', true)", str(ORG_A))
            accounts = await CI.scope_accounts(conn, ORG_A, household_id=H1, entity_ids=list(rep.shares), today=TODAY)
            out["accounts"] = accounts
            out["built"] = await CI.build(conn, ORG_A, fields=fields, lines=lines, sleeves=view["sleeves"],
                                          accounts=accounts, today=TODAY, purpose=purpose, fixture=dict(PLAN_FIXTURE))
            raise _Rollback()
    except _Rollback:
        pass
    out["view"], out["fields"], out["lines"] = view, fields, lines
    return out


async def y_plan_db(conn):
    section("[Y] The plan through the database with both providers ACTIVE (one rolled-back transaction): issues, "
            "auction and N-MFP yields, per-account after-tax choices, allocation, blends")
    before = await real_fingerprints(conn)
    b = await build_activated(conn)
    after = await real_fingerprints(conn)
    check(b.get("flipped") == 2 and before == after, "the activation flipped exactly the 2 provider rows and was "
          "rolled back — the real provider rows and every other real row are unchanged", f"{b.get('flipped')}")
    built = b["built"]
    lad = built["ladder"]
    shape = _shape(lad, _names(b["lines"]))
    check(shape == EXPECTED, "the ladder from the DATABASE (the master's issues, the stored auction yields, the fixture "
          "curve) equals the hand-computed one", f"{shape}")
    sec = S.get("sec_by_cusip") or {}
    check(lad["rungs"][0]["security_id"] == sec.get("99C030AA3") and lad["rungs"][1]["security_id"] == sec.get(
        "99C030AC9") and lad["rungs"][0]["yield"]["yield_pct"] == Decimal("4.010"),
          "matched rungs resolve to the security master's own ids, with the stored auction yield", f"{sec}")
    acc = {a["id"]: a for a in b["accounts"]}
    check(set(acc) == {str(ACC_1), str(ACC_2)} and acc[str(ACC_1)]["cash_value"] == Decimal("600000.00"),
          "the scope's accounts: H1's two open accounts (never H2's or org B's), each with its latest cash balance",
          f"{sorted(acc)}")
    ins = built["instruments"]
    near = next(s for s in ins["sleeves"] if s["key"] == "near_term")
    rows = {r["account_id"]: r for r in near["accounts"]}
    a1, a2 = rows[str(ACC_1)], rows[str(ACC_2)]
    at_t, at_m = Decimal("4.5") * (1 - FED), Decimal("3.1") * (1 - SL)
    check(a1["chosen"]["kind"] == "treasury_mmf" and a1["chosen"]["class_id"] == "C990030001"
          and a1["chosen"]["security_id"] == (S.get("sec_by_class") or {}).get("C990030001")
          and a1["chosen"]["after_tax"]["yield_pct"] == at_t and a1["runner_up"]["kind"] == "muni_mmf"
          and a1["gap_bp"] == ((at_t - at_m) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
          and a1["chosen"]["net"]["as_of"] == date(2026, 10, 31) and a1["chosen"]["net"]["basis"] == NM.BASIS,
          "A1 (taxable trust, NY profile), Near-term: the Treasury MMF (its N-MFP class in the master) — after-tax "
          f"{at_t} beats the muni MMF's {at_m}; basis and as-of carried", f"{a1['why']} gap={a1['gap_bp']}")
    check(a2["chosen"]["kind"] == "government_mmf" and a2["chosen"]["class_id"] == "C990030003"
          and a2["basis"] == "net" and a2["gap_bp"] == Decimal("10.0"),
          "A2 (IRA): the Government MMF's best class (4.60) — the highest net yield, exemption irrelevant",
          f"{a2['why']}")
    check(a1["value"] == Decimal("100000") * Decimal("0.6") and a2["value"] == Decimal("100000") * Decimal("0.4")
          and near["blended"]["yield_pct"] == Decimal("0.6") * Decimal("4.5") + Decimal("0.4") * Decimal("4.6")
          and near["blended_after_tax"]["yield_pct"] == Decimal("0.6") * at_t + Decimal("0.4") * Decimal("4.6")
          and near["effective"]["from"] == "instruments",
          "allocation by cash balance (60 / 40) and the blends Σ value × yield ÷ total, net and after-tax; the "
          "instrument blend replaces cash02's proxy", f"{near['blended']} {near['blended_after_tax']}")
    res = next(s for s in ins["sleeves"] if s["key"] == "reserve")
    bill = next(c for c in res["accounts"][0]["candidates"] if c["kind"] == "bill")
    usf = next(c for c in res["accounts"][0]["candidates"] if c["kind"] == "ultra_short_fund")
    check(bill.get("cusip") == "99C030AD7" and bill["net"]["yield_pct"] == Decimal("4.120")
          and usf["net"] == {"estimated": False, "text": "30-day SEC yield: needs a source"},
          "Reserve: the real 26-week bill maturing on or before the window's midpoint with its auction yield; the "
          "ultra-short fund reads 'needs a source'", f"{bill.get('cusip')} {bill['net']}")
    hurdle = a1.get("cd_hurdle") or {}
    check(hurdle.get("hurdle_bp") == ((Decimal("4.5") * (1 - FED) / (1 - FED - SL) - Decimal("4.5")) * 100).quantize(
        Decimal(1), rounding=ROUND_HALF_UP), "A1's Treasury choice carries the CD hurdle at the profile's rates",
          f"{hurdle}")


# ── The app (providers OFF), member view, visibility, isolation, audit ────────────────────────────────────────
async def treasury(uid, hh):
    return await api(uid, "GET", f"/api/v1/cash/households/{hh}/treasury")


async def y_app(conn):
    section("[Y] The app: the plan in the treasury envelope (providers OFF), member read-only with library labels, "
            "visibility, isolation both directions, every regeneration audited")
    code, env = await treasury(U_ADV, H1)
    plan = env.get("plan") or {}
    sc = (plan.get("scopes") or [{}])[0]
    lad = sc.get("ladder") or {}
    rungs = lad.get("rungs") or []
    want = [(k, t.isoformat(), BD.prior_business_day(t).isoformat()) for k, t, *_r in EXPECTED]
    got = [(r.get("kind"), r.get("target_date"), r.get("maturity")) for r in rungs]
    check(code == 200 and len(plan.get("scopes") or []) == 1 and got == want and all(r.get("indicative") for r in rungs),
          "staff: the plan rides in the treasury envelope; with the providers OFF every rung is INDICATIVE on the "
          "hand-computed dates (no issue is read)", f"{code} {got}")
    check("not connected" in ((sc.get("sources") or {}).get("issues") or "")
          and [dec(r["par"]) for r in rungs] == [Decimal("15000"), Decimal("400000"), Decimal("20000"), ZERO,
                                                  Decimal("300000"), ZERO],
          "the envelope names why: 'Treasury issues (treasury_fiscal): not connected'; pars as hand-computed",
          f"{sc.get('sources')}")
    y2 = (rungs[1].get("yield") or {}) if len(rungs) > 1 else {}
    check(y2.get("estimated") is True and dec(y2.get("yield_pct")) == q3(curve_at(months_to(date(2028, 1, 14))))
          and "FRED Treasury curve" in (y2.get("basis") or ""),
          "staff read the fixture curve (fred, internal use) — the rung yield equals this script's interpolation at 3 "
          "decimals", f"{y2}")
    near = next((s for s in ((sc.get("instruments") or {}).get("sleeves") or []) if s.get("key") == "near_term"), {})
    a1 = next((a for a in near.get("accounts") or [] if a.get("account_id") == str(ACC_1)), {})
    check(a1.get("chosen") is None and "not connected" in (a1.get("why") or "") + dumps(a1.get("candidates")),
          "with N-MFP OFF no money-market yield is used: nothing is chosen for Near-term, and the reason says 'not "
          "connected'", f"{dumps(a1)[:800]}")
    check(plan.get("actions") == ["regenerate"] and plan.get("purpose") == "internal"
          and (plan.get("vocabularies") or {}).get("editable") == []
          and {p["key"]: p["status"] for p in plan.get("providers") or []}.get("treasury_fiscal") == "not_connected",
          "the plan block: Regenerate offered to an author, purpose internal, the providers' status published",
          f"{dumps({k: plan.get(k) for k in ('actions', 'purpose', 'providers')})}")
    # member
    code_m, env_m = await treasury(U_MEM, H1)
    pm = env_m.get("plan") or {}
    lab = (env_m.get("labels") or {}).get(CI.TAX_KEY) or {}
    body_m = dumps(pm)
    check(code_m == 200 and pm.get("member_view") is True and pm.get("actions") == []
          and pm.get("purpose") == "member_display" and len(pm.get("scopes") or []) == 1
          and "99C03" not in body_m and '"curve_missing"' not in body_m,
          "a member reads their family's ladder and plan READ-ONLY (no action), purpose member_display, no CUSIP",
          f"{code_m} {body_m[:600]}")
    my = ((pm.get("scopes") or [{}])[0].get("ladder") or {}).get("rungs") or [{}]
    check("licence does not allow showing values to members" in dumps(my[0].get("yield")),
          "THE licence rule for a member: the FRED curve (member display not allowed) is never shown — the rung says "
          "why", f"{my[0].get('yield')}")
    check(bool(lab.get("text")) and lab.get("key") == CI.TAX_KEY and (lab.get("source") == "library" or lab.get(
        "text") == CI.TAX_ESTIMATE) and bool(((env_m.get("labels") or {}).get(CP.YIELD_KEY) or {}).get("text")),
          "the member envelope carries cash.tax_estimate and cash.yield_estimate from the disclosure library",
          f"{dumps(lab)}")
    code_m2, _b = await treasury(U_MEM, H2)
    c_mr, b_mr = await api(U_MEM, "POST", f"/api/v1/cash/households/{H1}/plan/regenerate", {})
    check(code_m2 == 404 and c_mr == 403, "a member never opens another family (404) and cannot regenerate (403)",
          f"{code_m2} {c_mr} {dumps(b_mr)}")
    # permissions: compliance reads, cannot regenerate — the refusal names the permission
    c_v, env_v = await treasury(U_VIEW, H1)
    c_vr, b_vr = await api(U_VIEW, "POST", f"/api/v1/cash/households/{H1}/plan/regenerate", {})
    check(c_v == 200 and (env_v.get("plan") or {}).get("actions") == [] and c_vr == 403
          and "manage_cash_policy" in dumps(b_vr),
          "a reader without manage_cash_policy sees the plan, no Regenerate, and the API refuses it (403 naming "
          "manage_cash_policy)", f"{c_v} {c_vr} {dumps(b_vr)}")
    # regenerate → audited
    before = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                      "'cash.plan.regenerated'", ORG_A, fetch="fetchval"))
    c_r, b_r = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/plan/regenerate", {})
    await asyncio.sleep(0)
    rows = await platform(conn, "SELECT user_id, payload FROM audit_log WHERE org_id = $1 AND action = "
                                "'cash.plan.regenerated' ORDER BY created_at DESC LIMIT 1", ORG_A)
    after = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                     "'cash.plan.regenerated'", ORG_A, fetch="fetchval"))
    newv = rows[0]["payload"] if rows else None
    newv = json.loads(newv) if isinstance(newv, str) else (newv or {})
    fp = ((b_r.get("plan") or {}).get("scopes") or [{}])[0].get("fingerprint")
    check(c_r == 200 and after == before + 1 and bool(rows) and str(rows[0]["user_id"]) == str(U_ADV)
          and ((newv.get("scopes") or [{}])[0].get("fingerprint")) == fp and fp == sc.get("fingerprint"),
          "a regeneration is audited (cash.plan.regenerated, by the caller, naming the policy version and the plan's "
          "fingerprint — the same plan the envelope showed)",
          f"{c_r} {before}→{after} {dumps(newv)[:400]} response {dumps(b_r)[:300]}")
    # VISIBILITY1: a restricted author (assigned to H2 only)
    c_rs1, _x = await api(U_RS, "POST", f"/api/v1/cash/households/{H1}/plan/regenerate", {})
    c_rs2, b_rs2 = await api(U_RS, "POST", f"/api/v1/cash/households/{H2}/plan/regenerate", {})
    check(c_rs1 == 404 and c_rs2 == 200 and (b_rs2.get("plan") or {}).get("scopes") == [],
          "VISIBILITY1 ON: a restricted author cannot regenerate another family's plan (404); their own family has no "
          "active policy → an empty plan", f"{c_rs1} {c_rs2} {dumps(b_rs2)[:300]}")
    # org isolation both directions
    c_b, _y = await treasury(U_B, H1)
    c_br, _z = await api(U_B, "POST", f"/api/v1/cash/households/{H1}/plan/regenerate", {})
    a_sees_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM accounts WHERE id = $1", ACC_B, fetch="fetchval"))
    b_sees_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM accounts WHERE id = $1", ACC_1, fetch="fetchval"))
    a_sees_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM accounts WHERE id = $1", ACC_1, fetch="fetchval"))
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(c_b == 404 and c_br == 404 and a_sees_b == 0 and b_sees_a == 0 and a_sees_a == 1 and by is False,
          "org isolation both directions: B's caller gets 404 on A's household and plan; under RLS (rolbypassrls = "
          "false) A sees none of B's accounts and B none of A's, while A sees its own",
          f"{c_b} {c_br} a→b={a_sees_b} b→a={b_sees_a} a→a={a_sees_a} bypass={by}")
    err = None
    try:
        await scoped(conn, ORG_B, "INSERT INTO account_balances_daily (org_id, account_id, as_of_date, "
                                  "total_market_value, cash_value, source_system) VALUES ($1, $2, $3, 1, 1, "
                                  "'manual')", ORG_A, ACC_1, TODAY, fetch="execute")
    except asyncpg.InsufficientPrivilegeError as exc:
        err = type(exc).__name__
    check(err == "InsufficientPrivilegeError", "an INSERT naming another org's account is refused by RLS", f"{err}")


# ── Loaders ───────────────────────────────────────────────────────────────────────────────────────────────────
async def y_loaders(conn):
    section("[Y] Loaders: load_cash_market_data --dry-run --fixtures and load_olivo_ladder --dry-run write nothing")
    import load_cash_market_data as LM
    import load_olivo_ladder as LL

    for mod in (LM, LL):
        have = list(inspect.signature(mod.main).parameters)
        check(have == ["argv"], f"{mod.__name__}.main(argv) — the signature this section calls", f"{have}")
    before, c_before = await real_fingerprints(conn), await counts(conn)
    orig = DBC.hydrate
    DBC.hydrate = lambda: None          # already hydrated: no second Doppler call (no network during the run)
    outs = {}
    try:
        for name, mod, argv in (("market data", LM, ["--dry-run", "--fixtures"]), ("olivo", LL, ["--dry-run"])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = await mod.main(argv)
            outs[name] = (rc, buf.getvalue())
    finally:
        DBC.hydrate = orig
    after, c_after = await real_fingerprints(conn), await counts(conn)
    rc, out = outs["market data"]
    check(rc == 0 and "DRY RUN — nothing written (fixtures)." in out and "99C030AA3" in out and "C990030001" in out
          and "rejected: page 1 row 4" in out, "load_cash_market_data.py --dry-run --fixtures prints the parsed issues, "
          "yields and refusals", f"rc={rc} {out[-500:]}")
    rc2, out2 = outs["olivo"]
    check(rc2 == 0 and "DRY RUN — nothing written (read-only transaction)." in out2 and "Ladder (household)" in out2
          and "Instrument plan" in out2, "load_olivo_ladder.py --dry-run prints the demo family's ladder and plan",
          f"rc={rc2} {out2[-500:]}")
    check(before == after and c_before == c_after, "both dry runs wrote NOTHING (real + demo fingerprints and every "
          "fixture count unchanged)", f"{[k for k in before if before[k] != after.get(k)]}")
    check(NETWORK_CALLS == [], "no network call during the loaders (httpx refuses; the fixtures are files)",
          f"{NETWORK_CALLS[:3]}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py / visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=600)
        check(code == 0 and "CURRENT" in out, f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")
    self_static(check, find)


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) >= 7 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the cash03 render tests ({NODE_TEST}, counted by their file: rungs, indicative + holiday, timeline, plan, "
          f"policy-editor blends, fail-closed control, member read-only) all pass ({n.group(1) if n else '?'})",
          f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


#: the nested regressions, each ONCE, flat, after teardown. verify_secmaster2.py (cash03 fix 2): fix 1's
#: security_master_load.batch_key changed SHARED Matcher code (the within-batch dedup key), and secmaster2 owns
#: the Matcher — every existing securities feed's dedup must be unchanged.
REGRESSIONS = ("verify_cash02.py", "verify_secmaster2.py")


def regression():
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    for script in REGRESSIONS:
        section(f"[Y] Regression — {script} ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=2700, env=env)
        t = VT.nested_total(out, code)
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(t.ok and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def print_fixture_counts(conn, when: str) -> int:
    c = await counts(conn)
    extra = await org_table_counts()
    for t in sorted(set(c) | set(extra)):
        n = c.get(t, extra.get(t, 0))
        if n or t in c:
            print(f"[INFO] {when}: {t} = {n}")
    return sum(c.values()) + sum(v for t, v in extra.items() if t not in c)


async def cleanup_only(conn) -> int:
    await print_fixture_counts(conn, "before cleanup")
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    after = await print_fixture_counts(conn, "after cleanup")
    check(after == 0, "every touched table (and every org_id table) holds zero fixture rows", f"{after}")
    check(left == 0, "zero fixture rows remain", f"{left}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


FIXTURE_SECTIONS = (("providers", y_providers), ("security master", y_master), ("ladder", y_ladder),
                    ("after-tax", y_after_tax), ("plan (db)", y_plan_db), ("app + visibility", y_app))
#: --only=<names>: short name → FIXTURE_SECTIONS name; a section that reads another's state pulls it in (ordered)
ONLY_NAMES = {"providers": "providers", "master": "security master", "ladder": "ladder", "after-tax": "after-tax",
              "plan": "plan (db)", "app": "app + visibility"}
ONLY_NEEDS = {"plan (db)": ("security master",)}    # it reads S["sec_by_cusip"] / S["sec_by_class"]


def only_sections() -> list[str] | None:
    """None = the full run; [] = seed only; else the FIXTURE_SECTIONS names to run, in their own order."""
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = {x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()}
    bad = want - set(ONLY_NAMES) - {"seed"}
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, {', '.join(ONLY_NAMES)}")
    names = {ONLY_NAMES[w] for w in want if w != "seed"}
    names |= {n for x in list(names) for n in ONLY_NEEDS.get(x, ())}
    return [n for n, _fn in FIXTURE_SECTIONS if n in names]


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
    only = only_sections()
    if only is not None:
        print(f"[INFO] --only: seed + {only or '(seed only)'}")
    pre = None
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        if not await guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        if only is None:
            await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        chosen = [(n, fn) for n, fn in FIXTURE_SECTIONS if only is None or n in only]
        if S.get("seeded"):
            for name, fn in chosen:
                await run(name, fn, conn)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
            await run("loaders", y_loaders, conn)
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        RC.set_clock(None)
        RC.set_plan_fixture(None)
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (securities, identifiers, attributes, index levels, quality "
                  "events, accounts, policies, audit rows, …)", f"{left}")
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
    if only is not None:
        print("[INFO] --only: no regression (the one-off path)")
    elif os.environ.get("VERIFY_NESTED") == "1":
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
