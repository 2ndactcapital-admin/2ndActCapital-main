"""verify_cash02 — CASH02: cash POLICIES and the THREE SLEEVES (Near-term / Reserve / Long-term), maker-checker.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash02.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated
from Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx
transport refuses and records (asserted empty at the end), and the AI helpers refuse and record.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile
call. REAL: the app through an ASGI transport, services.cash_policy / cash_coverage / cash_obligations / networth /
sod / human_tasks / bulk_approvals / market_data.read_service / tax_profiles, RLS and the live database. The script's
own connection does not bypass RLS (rolbypassrls = false, asserted). TIME IS INJECTED: routers.cash's "today" is
TODAY. FRED IS NEVER READ LIVE: the yield proxies are re-pointed (``CP.YIELD_PROXIES``, same shape — asserted) at
fixture series ``cash02vfy.*`` (series source_provider 'none', which no adapter serves, so no nightly job ever
fetches them; observations source_provider NULL = "the series' own provider", exactly what the real fred.* rows carry —
cash02 fix 1), read through the real read_service.

FIXTURES: two orgs 99000000-0000-0000-0000-0000cac20001 / 0002 (is_test = true, asserted; org A has VISIBILITY1
enforcement ON); users with real user_roles grants — the app's decision on each asserted:
  org A  U_ADV, U_ADV2 advisor (view / manage portfolio + manage_cash_policy; assigned to both families),
         U_OA org_admin (+ manage_cash_policy; unscoped), U_VIEW compliance (view_portfolio only; assigned to H1),
         U_MEM member (Policy family), U_MEM2 member (Other family), U_RS support_staff (+ manage_cash_policy;
         assigned to the Policy family ONLY)
  org B  U_B advisor (+ manage_cash_policy)
Households: H1 Policy family (E_T trust 1,000,000 cash; E_ILIT 10,000 cash), H2 Other family (E_O), org B HB (E_B).
H1's obligations (hand-computed below) are recorded through the cash01 API. Reserved prefixes (the id range, the
sign-in tag 'cash02verify', names 'CASH02 VERIFY%', series keys 'cash02vfy.%') are checked absent from real rows
first.

TEARDOWN (start and end, one function): ``_verify_cleanup.cleanup`` on the operator DSN with the documented teardown GUC
``execution.teardown`` (rep6's tax_inputs_guard deletes the fixture tax profile only under it — cash02 fix 1), keyed by
this run's fixture ids (the global
fixture series and observations by their materialised ids); every touched table re-read for its exact pre-test count,
counted BY FIXTURE IDS ONLY (live activity writes audit_log, sod_decisions, member_todos and workflow runs). Real rows
(every non-fixture cash_policies / cash_obligations / tax_profiles row, the Olivo demo's, the SoD catalog's cash
policy rows) are fingerprinted before and after.

--self-checks-only: git / route / static self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and
the leftover check only (per-table fixture counts printed). --only=<a,b,…>: the one-off path — teardown, schema read,
seed, then ONLY the named fixture sections (seed | calculation | yields | maker-checker | minimum | inbox | visibility;
'visibility' pulls in 'maker-checker', whose v2 policy it reads), then the full teardown + leftover + real-row checks;
no Olivo / routes / front end / regression. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import calendar
import contextlib
import copy
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
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash02"
BEFORE_REF = "77d539a"   # cash02-work's base: 'merge cash01'
_W = "internal + any(manage_cash_policy)"
ROUTES = {
    ("POST", "/api/v1/cash/households/{household_id}/policy/preview"): _W,
    ("POST", "/api/v1/cash/households/{household_id}/policies"): _W,
    ("POST", "/api/v1/cash/households/{household_id}/policies/{policy_id}/submit"): _W,
    ("POST", "/api/v1/cash/households/{household_id}/policies/{policy_id}/approve"): _W,
    ("POST", "/api/v1/cash/households/{household_id}/policies/{policy_id}/reject"): _W,
}
#: existing routes cash02 changed ON PURPOSE (the approvals inbox gained the cash-policy type) — pinned exactly
_R0 = "any(view_authority|approve_authority|view_held_away|approve_held_away)"
_A0 = "any(approve_authority|approve_held_away)"
WIDENED = {
    ("GET", "/api/v1/approvals/bulk"): (_R0, _R0[:-1] + "|manage_cash_policy)"),
    ("GET", "/api/v1/approvals/summary"): (_R0, _R0[:-1] + "|manage_cash_policy)"),
    ("POST", "/api/v1/approvals/bulk"): (_A0, _A0[:-1] + "|manage_cash_policy)"),
}
#: existing routes cash02 REMOVED on purpose (the minimum moved into the policy)
RETIRED = {("PUT", "/api/v1/cash/households/{household_id}/minimums")}
OWN_FILES = ("apps/api/services/cash_policy.py", "apps/api/routers/cash.py", "apps/api/services/cash_coverage.py",
             "apps/api/scripts/load_olivo_cash_policy.py")
NODE_TEST = "tests/cash02/cash02.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH02_NOTES.md"
POLICY_DOC = REPO / "docs" / "cash" / "CASH_POLICY.md"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash02's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed, removed = flat(o.changed), flat(o.removed)
    bad_w = [(k, (o.before.get(kk) or {}).get("after"), (o.end.get(kk) or {}).get("after"))
             for kk in o.changed for k in flat([kk])
             if k not in WIDENED or (o.before[kk].get("after"), o.end[kk].get("after")) != WIDENED[k]]
    check(len(o.before) > 0 and changed == set(WIDENED) and not bad_w,
          "cash02 changed exactly the three approvals routes' gates, each by adding manage_cash_policy (pinned)",
          f"{o.where()}; changed={sorted(changed)} bad={bad_w[:3]}")
    check(removed == RETIRED, "cash02 removed exactly cash01's PUT …/minimums (the minimum moved into the policy)",
          f"{sorted(removed)}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash02's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash02's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    cp = _src("apps/api/services/cash_policy.py")
    cpc = _code_only(cp)
    check("CC.monthly_outflow(" in cpc and "CC.months_of_coverage(" in cpc and "portfolio.positions" not in cpc.split(
        "async def trailing_outflows")[0], "services/cash_policy.py sizes sleeves with the cash01 coverage formulas "
          "(monthly_outflow / months_of_coverage) and reads no positions for them (no second formula)")
    check("RS.read_series(" in cpc and "import httpx" not in cp and "fred.FredClient" not in cpc,
          "services/cash_policy.py reads the FRED proxies through services.market_data.read_service, never FRED itself")
    check("TP.approved_profile(" in cpc and "state_rate" not in cpc,
          "after-tax reads the approved tax profile and never applies its state rate (Treasury state-tax exemption)")
    cov = _code_only(_src("apps/api/services/cash_coverage.py"))
    check("FROM cash_policies" in cov and "cash_coverage_minimums" not in cov and "def set_minimum" not in cov,
          "services/cash_coverage.py reads the minimum from cash_policies only (no second copy, no direct setter)")
    rc = _code_only(_src("apps/api/routers/cash.py"))
    check("/minimums" not in rc and "set_minimum" not in rc, "routers/cash.py no longer carries the PUT …/minimums route")
    sod_src = _src("apps/api/services/sod.py")
    check('CASH_POLICY = "cash_policy"' in sod_src and '"cash_policy_approval": CASH_POLICY' in sod_src,
          "services/sod.py names the cash_policy item type and maps its task kind")
    ba = _src("apps/api/services/bulk_approvals.py")
    check('"cash_policy": BulkType("cash_policy"' in ba and "CP.decide(" in ba,
          "services/bulk_approvals.py decides a cash policy through services.cash_policy.decide (one path)")
    check('Literal["authority", "held_away", "cash_policy"]' in _src("apps/api/routers/approvals.py"),
          "routers/approvals.py accepts the cash_policy bulk type")
    check('"cash.yield_estimate"' in _src("apps/api/services/disclosures.py"),
          "the disclosure library defines cash.yield_estimate")
    check('("manage_cash_policy", "cash_policy", "manage")' in _src("apps/api/services/rbac.py"),
          "services/rbac.ORG_ADMIN_DEFAULT_PERMISSIONS grants manage_cash_policy to a new org's org_admin")
    sql = _src("docs/cash02_part1.sql")
    check("CREATE TABLE IF NOT EXISTS public.cash_policies" in sql and all(
        op in sql for op in ("FOR SELECT", "FOR INSERT", "FOR UPDATE", "FOR DELETE")) and "ENABLE ROW LEVEL SECURITY"
          in sql, "docs/cash02_part1.sql: cash_policies with RLS + a policy for every operation, in the same migration")
    check("cash_policies_maker_checker_chk CHECK (approved_by IS NULL OR NOT (approved_by = ANY (maker_ids)))" in sql,
          "docs/cash02_part1.sql: approved_by ∉ maker_ids is a database CHECK (the execution-instruction style)")
    check("IF EXISTS (SELECT 1 FROM public.cash_coverage_minimums)" in sql and "DROP TABLE public.cash_coverage_minimums"
          in sql, "docs/cash02_part1.sql retires cash_coverage_minimums only when empty (never copied in as active)")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8."):
        check(sec in notes, f"docs/cash/CASH02_NOTES.md has section {sec}")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    check("verify_cash02.py" in _src("scripts/regression_set.txt"), "verify_cash02.py is in scripts/regression_set.txt")


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
import _db_connect as DBC  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
import routers.cash as RC  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import cash_coverage as CC  # noqa: E402
from services import cash_obligations as OB  # noqa: E402
from services import cash_policy as CP  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import reset_rls_context, set_rls_context  # noqa: E402

TAG = "cash02verify"
NAME = "CASH02 VERIFY "
SERIES_PREFIX = "cash02vfy."
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac2"
TODAY = date(2026, 11, 9)       # the injected "today"
#: the fixture series' registry provider: no adapter serves 'none', so the nightly job never fetches a fixture series
SERIES_PROVIDER = "none"
#: the fixture observations' per-row provider. NULL = "the series' own provider" (services/market_data/splice.py) — what
#: every real fred.* observation carries; 'none' is NOT allowed by indicator_obs_source_provider_chk (cash02 fix 1)
OBS_PROVIDER = None
#: the documented teardown GUCs this script's fixtures need beyond _verify_cleanup's own 'sod.teardown':
#: public.execution_teardown_allowed() (read by rep6's tax_inputs_guard on tax_profiles) checks
#: current_setting('execution.teardown') = 'fixture' AND current_user <> 'app_service' (cash02 fix 1)
TEARDOWN_GUCS = ("execution.teardown",)
ZERO = Decimal(0)


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_ADV, U_ADV2, U_OA, U_VIEW, U_MEM, U_MEM2, U_RS, U_B = (_id(10 + i) for i in range(8))
H1, H2, HB = _id(30), _id(31), _id(32)
E_T, E_ILIT, E_O, E_B = _id(40), _id(41), _id(42), _id(43)
A_CASH, A_CASH_B = _id(60), _id(61)
P_T = _id(70)

PERMS = {
    "advisor": {"view_portfolio", "manage_portfolio", "view_members", "manage_cash_policy"},
    "compliance": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "org_admin": {"manage_org_settings", "manage_members", "view_portfolio", "view_members", "manage_cash_policy"},
    "support_staff": {"view_portfolio", "manage_portfolio", "view_members", "manage_cash_policy"},
}
# uid → (org, users.role mirror, short, role granted)
USERS = {
    U_ADV: (ORG_A, "member", "adv", "advisor"),
    U_ADV2: (ORG_A, "member", "adv2", "advisor"),
    U_OA: (ORG_A, "org_admin", "oa", "org_admin"),
    U_VIEW: (ORG_A, "member", "view", "compliance"),
    U_MEM: (ORG_A, "member", "mem", "member"),
    U_MEM2: (ORG_A, "member", "mem2", "member"),
    U_RS: (ORG_A, "member", "rs", "support_staff"),
    U_B: (ORG_B, "member", "b", "advisor"),
}
ALL_USERS = list(USERS)
ENTS = {
    E_T: (ORG_A, "trust", "Dynasty", "Policy Family Trust"),
    E_ILIT: (ORG_A, "trust", "ILIT", "Policy ILIT"),
    E_O: (ORG_A, "individual", None, "Other Family Person"),
    E_B: (ORG_B, "individual", None, "Org B Person"),
}
HOUSEHOLDS = {H1: (ORG_A, "Policy", (E_T, E_ILIT)), H2: (ORG_A, "Other", (E_O,)), HB: (ORG_B, "B", (E_B,))}
CASH_KEY = "taxonomy_mc_8_1"
ASSETS = {A_CASH: ORG_A, A_CASH_B: ORG_B}
# (position id or None, owner, asset, market value)
POSITIONS = [(P_T, E_T, A_CASH, "1000000.00"), (None, E_ILIT, A_CASH, "10000.00"), (None, E_O, A_CASH, "200000.00"),
             (None, E_B, A_CASH_B, "300000.00")]
# the hand-computed schedule (recorded through the cash01 API): (payer, source type, name, amount, due date)
OBLIGATIONS = [
    (E_T, "one_off", "o1 near", "100000.00", "2026-12-01"),
    (E_T, "one_off", "o2 on the N=3 boundary", "50000.00", "2027-02-09"),      # = add_months(TODAY, 3) → Reserve
    (E_T, "one_off", "o3 on the R=12 boundary", "70000.00", "2027-11-09"),     # = add_months(TODAY, 12) → Long-term
    (E_T, "one_off", "o4 long", "30000.00", "2028-06-15"),
    (E_ILIT, "insurance_premium", "ILIT premium", "60000.00", "2027-01-15"),
    (E_O, "one_off", "other family item", "20000.00", "2027-01-10"),
]
# the fixture FRED proxies: every real proxy key → a fixture key (absent ones are deliberately never seeded)
PROXY_KEY_MAP = {"fred.dgs3mo": SERIES_PREFIX + "t3m", "fred.dgs10": SERIES_PREFIX + "t10",
                 "fred.t10y3m": SERIES_PREFIX + "s10y3m", "fred.dgs6mo": SERIES_PREFIX + "t6m",
                 "fred.dgs1": SERIES_PREFIX + "t1y", "fred.dgs2": SERIES_PREFIX + "t2y"}
SERIES = {  # key → [(date, value)]  (t3m / t6m / t1y are NOT seeded: Near-term derives, Reserve reads "needs")
    SERIES_PREFIX + "t10": [(date(2026, 11, 5), "4.80")],
    SERIES_PREFIX + "s10y3m": [(date(2026, 11, 4), "0.60"), (date(2026, 11, 5), "0.70")],
    SERIES_PREFIX + "t2y": [(date(2026, 11, 4), "4.40"), (date(2026, 11, 6), "4.50"), (date(2026, 11, 12), "9.99")],
}
TAX = {"federal_ordinary_rate": Decimal("0.37"), "federal_lt_rate": Decimal("0.20"), "niit_applies": True,
       "niit_rate": Decimal("0.038"), "state_rate": Decimal("0.0685"), "city_rate": Decimal("0.03876")}

COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.transactions", "public.cash_obligations", "public.cash_policies", "public.tax_profiles",
    "public.sod_decisions", "public.audit_log", "public.member_todos", "public.workflow_runs",
    "public.workflow_definitions", "public.domain_events", "market_data.indicator_series",
    "market_data.indicator_observations",
)
SEED_CHECK_LITERALS = (
    ("public.cash_policies", "cash_policies_scope_chk", ("entity", "household")),
    ("public.cash_policies", "cash_policies_status_chk", ("draft", "pending_approval", "active", "retired",
                                                          "rejected")),
    ("public.cash_policies", "cash_policies_preset_chk", ("stay_liquid", "balanced", "recommended", "custom")),
    ("public.cash_policies", "cash_policies_objective_chk", ("stability", "balanced", "yield")),
    ("public.cash_obligations", "cash_obligations_source_type_chk", ("one_off", "insurance_premium")),
    ("public.cash_obligations", "cash_obligations_certainty_chk", ("firm",)),
    ("portfolio.positions", "positions_authority_chk", ("stated",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.positions", "positions_basis_chk", ("value",)),
    ("portfolio.assets", "assets_valuation_chk", ("amortized_cost",)),
    ("portfolio.assets", "assets_class_chk", ("financial",)),
    ("portfolio.transactions", "transactions_authority_chk", ("stated",)),
    ("public.tax_profiles", "tax_profiles_status_chk", ("approved",)),
    ("public.tax_profiles", "tax_profiles_subject_chk", ("household",)),
    ("public.tax_profiles", "tax_profiles_filing_chk", ("married_filing_jointly",)),
    ("public.tax_profiles", "tax_profiles_source_chk", ("adviser",)),
    ("market_data.indicator_series", "indicator_series_frequency_chk", ("daily",)),
    ("market_data.indicator_series", "indicator_series_cost_tier_chk", ("free",)),
    ("market_data.indicator_series", "indicator_series_status_chk", ("active",)),
    ("public.delegate_grants", "delegate_grants_scope_check", ("view_only",)),
)
#: every row SHAPE seed() inserts by raw SQL — its literals verbatim, its parameters at a representative fixture value.
#: y_schema evaluates EVERY CHECK constraint on each table against these rows in the database itself
#: (jsonb_populate_record → the constraint's own expression), so a literal the catalog refuses (cash02 fix 1: 'none'
#: into indicator_obs_source_provider_chk) fails the pre-check instead of crashing the seed. A static check asserts
#: every quoted literal in seed()'s source is one of these values (or a non-column literal in SEED_NON_COLUMN).
_U, _O, _E, _H = "99000000-0000-0000-0000-0000cac20010", "99000000-0000-0000-0000-0000cac20001", \
    "99000000-0000-0000-0000-0000cac20040", "99000000-0000-0000-0000-0000cac20030"
SEED_ROWS = {
    "public.organizations": [{"id": _O, "name": "CASH02 Verify Org A", "slug": "cash02verify-a", "is_test": True}],
    "public.users": [{"id": _U, "org_id": _O, "email": "cash02verify-adv@test.local", "full_name": "CASH02 ADV",
                      "auth0_sub": "auth0|cash02verify-adv", "role": "member", "status": "active", "is_active": True,
                      "is_test": True}],
    "public.entities": [{"id": _E, "org_id": _O, "display_name": "CASH02 VERIFY Dynasty", "status": "active",
                         "entity_type": "trust", "sub_type": "Dynasty"}],
    "public.households": [{"id": _H, "org_id": _O, "name": "CASH02 VERIFY Policy family"}],
    "public.delegate_grants": [{"org_id": _O, "principal_entity_id": _E, "delegate_user_id": _U, "scope": "view_only",
                                "effective_from": "2026-11-08T00:00:00+00:00"}],
    "public.staff_assignments": [{"org_id": _O, "entity_id": _E, "assigned_to_user_id": _U,
                                  "role_label": "Lead adviser"}],
    "public.org_settings": [{"org_id": _O, "setting_key": "staff_visibility.enforced", "setting_value": True,
                             "category": "general"}],
    "portfolio.assets": [{"id": _E, "org_id": _O, "name": "CASH02 VERIFY cash", "asset_class": "financial",
                          "asset_type": "cash", "ownership_basis": "value", "valuation_method": "amortized_cost",
                          "currency_code": "USD", "default_taxonomy_key": "taxonomy_mc_8_1"}],
    "portfolio.positions": [{"id": _E, "org_id": _O, "owner_entity_id": _E, "asset_id": _E, "as_of_date": "2026-11-08",
                             "ownership_basis": "value", "market_value": "1000000.00", "authority": "stated",
                             "source_system": "manual"}],
    "portfolio.transactions": [{"org_id": _O, "position_id": _E, "transaction_type_code": "call_investment",
                                "trade_date": "2026-10-01", "gross_amount": "60000.00", "net_amount": "60000.00",
                                "currency_code": "USD", "authority": "stated", "source_system": "manual"}],
    "public.tax_profiles": [{"org_id": _O, "subject_type": "household", "subject_id": _H, "tax_year": 2026,
                             "version_no": 1, "filing_status": "married_filing_jointly", "federal_ordinary_rate": "0.37",
                             "federal_lt_rate": "0.20", "niit_applies": True, "niit_rate": "0.038",
                             "state_rate": "0.0685", "city_rate": "0.03876", "as_of": "2026-11-09", "source": "adviser",
                             "source_note": "verify fixture", "status": "approved", "entered_by": _U,
                             "checker_id": "99000000-0000-0000-0000-0000cac20012",
                             "decided_at": "2026-11-09T00:00:00+00:00"}],
    "market_data.indicator_series": [{"series_key": "cash02vfy.t10", "name": "CASH02 VERIFY cash02vfy.t10",
                                      "category": "Rates & credit", "frequency": "daily", "cost_tier": "free",
                                      "source_provider": SERIES_PROVIDER, "ingest_status": "active"}],
    "market_data.indicator_observations": [{"series_id": _E, "obs_date": "2026-11-05", "value": "4.80",
                                            "source_provider": OBS_PROVIDER}],
}
#: quoted literals in seed()'s source that are not a column value (set_config names / values, casts, intervals)
SEED_NON_COLUMN = {"app.is_super_admin", "true", "1 day"}
UNIQUES = ("cash_policies_active_household_uq", "cash_policies_active_entity_uq", "cash_policies_open_household_uq",
           "cash_policies_open_entity_uq", "cash_policies_version_uq", "assets_cash_active_uniq",
           "indicator_series_key_uq", "org_settings_org_id_setting_key_key", "tax_profiles_approved_uq")
#: every column this script INSERTs, per table — checked against docs/schema_snapshot.sql before seeding (s29b fix 1)
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
    "portfolio.positions": ("id", "org_id", "owner_entity_id", "asset_id", "as_of_date", "ownership_basis",
                            "market_value", "authority", "source_system"),
    "portfolio.transactions": ("org_id", "position_id", "transaction_type_code", "trade_date", "gross_amount",
                               "net_amount", "currency_code", "authority", "source_system"),
    "tax_profiles": ("org_id", "subject_type", "subject_id", "tax_year", "version_no", "filing_status",
                     "federal_ordinary_rate", "federal_lt_rate", "niit_applies", "niit_rate", "state_rate", "city_rate",
                     "as_of", "source", "source_note", "status", "entered_by", "checker_id", "decided_at"),
    "market_data.indicator_series": ("series_key", "name", "category", "frequency", "cost_tier", "source_provider",
                                     "ingest_status"),
    "market_data.indicator_observations": ("series_id", "obs_date", "value", "source_provider"),
    "cash_policies": ("org_id", "scope_type", "household_id", "entity_id", "version_no", "near_months",
                      "reserve_months", "horizon_months", "rung_count", "buffer_months", "objective", "objective_score",
                      "preset", "min_coverage_months", "status", "maker_ids", "created_by", "submitted_by",
                      "submitted_at", "approval_task_id", "approved_by", "approved_at"),
}

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


async def series_ids(op) -> list[UUID]:
    return [r["id"] for r in await op.fetch("SELECT id FROM market_data.indicator_series WHERE series_key LIKE $1",
                                            SERIES_PREFIX + "%")]


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


def fixture_scope(table: str) -> str:
    orgs, users, hhs = _arr(FIXTURE_ORGS), _arr(ALL_USERS), _arr(HOUSEHOLDS)
    by_org = f"org_id = ANY({orgs})"
    like = SERIES_PREFIX.replace(".", "\\.") + "%"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({hhs})",
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "market_data.indicator_series": f"series_key LIKE '{like}'",
        "market_data.indicator_observations": f"series_id IN (SELECT id FROM market_data.indicator_series WHERE "
                                              f"series_key LIKE '{like}')",
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
        "cash_policies (every real org, the Olivo demo's included)": await fingerprint(
            conn, "SELECT * FROM public.cash_policies WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "cash_obligations (real, demo included)": await fingerprint(
            conn, "SELECT * FROM public.cash_obligations WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "tax_profiles (real)": await fingerprint(
            conn, "SELECT * FROM public.tax_profiles WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "the SoD catalog's cash_policy rows": await fingerprint(
            conn, "SELECT t.*, (SELECT string_agg(r.rule_key || r.status || r.version_no, ',' ORDER BY r.rule_key) "
                  "FROM sod_rules r WHERE r.item_type = t.item_type AND r.org_id IS NULL) AS rules FROM sod_item_types t "
                  "WHERE t.item_type = 'cash_policy'"),
        "real indicator series (key / status / provider)": await fingerprint(
            conn, "SELECT series_key, ingest_status, source_provider FROM market_data.indicator_series WHERE "
                  "series_key NOT LIKE $1", SERIES_PREFIX + "%"),
    }


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        sids = await series_ids(op)
        users, orgs, hhs = list(ALL_USERS), list(FIXTURE_ORGS), list(HOUSEHOLDS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [hhs]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            # tables without org_id, by their materialised fixture ids
            "market_data.indicator_observations": ("series_id = ANY($1::uuid[])", [sids]),
            "market_data.indicator_series": ("id = ANY($1::uuid[])", [sids]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cash02")
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
              "market_data.indicator_series", "market_data.indicator_observations"):
        n += int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
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
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash02"}}
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


def fixture_proxies() -> dict:
    """CP.YIELD_PROXIES with every key mapped to its fixture series — the SAME shape (asserted)."""
    out = copy.deepcopy(REAL_PROXIES)
    for cands in out.values():
        for c in cands:
            c["keys"] = tuple(PROXY_KEY_MAP[k] for k in c["keys"])
    return out


REAL_PROXIES = copy.deepcopy(CP.YIELD_PROXIES)


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
        raise AssertionError(f"verify_cash02: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash02: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    have = list(inspect.signature(RC.set_clock).parameters)
    if have != ["fn"]:
        raise RuntimeError(f"routers.cash.set_clock signature drifted: {have}")
    RC.set_clock(lambda: TODAY)
    have = list(inspect.signature(CP.read_yields).parameters)
    if have != ["conn", "today", "proxies"]:
        raise RuntimeError(f"cash_policy.read_yields signature drifted: {have}")
    CP.YIELD_PROXIES = fixture_proxies()


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes, the reuse inventory, cash01's decisions honoured")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8."):
        check(sec in notes, f"docs/cash/CASH02_NOTES.md has section {sec}")
    for marker in ("cash_obligations.schedule", "household_report", "sod_item_types", "TASK_KIND_ITEM_TYPES",
                   "register_kind", "bulk_approvals", "fred.dgs2", "fred.t10y3m", "approved_profile",
                   "cash_coverage_minimums", "manage_cash_policy", "Reuse inventory", "cash.yield_estimate"):
        check(marker in notes, f"the notes name {marker!r}")
    check(POLICY_DOC.exists() and "Questionnaire" in POLICY_DOC.read_text(encoding="utf-8"),
          "docs/cash/CASH_POLICY.md documents the windows, presets, questionnaire, yields and approval flow")
    if "CASH_TRACK_PLAN_v1.2" in notes and not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md is still not in the repo — its §2.4 was taken as the prompt stated it")


async def y_schema(conn):
    section("[Y] Schema: CHECK / UNIQUE / FK constraints, indexes and policies read from the catalog BEFORE seeding")
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
          f"{got} CHECK constraints read; every literal the seeds and bodies use is one the database allows", f"{bad}")
    # cash02 fix 1: EVERY CHECK on every raw-seeded table, evaluated by the database on the seed's own row shapes
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(n_chk > 0 and not refused, f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} raw-seeded tables evaluated "
          "by the database against the seed's exact row shapes — none refuses a seeded value (e.g. the observations' "
          "source_provider)", f"{refused}")
    # reproduce the run-1 crash in the pre-check itself: the old literal 'none' must be REFUSED by the same evaluation
    _keep = SEED_ROWS["market_data.indicator_observations"]
    SEED_ROWS["market_data.indicator_observations"] = [dict(_keep[0], source_provider="none")]
    try:
        _n, old_refused = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["market_data.indicator_observations"] = _keep
    check(("market_data.indicator_observations", "indicator_obs_source_provider_chk", 0) in old_refused,
          "the pre-check is not vacuous: run 1's observation literal 'none' is refused by the same evaluation "
          "(indicator_obs_source_provider_chk — the CheckViolationError the seed crashed on)", f"{old_refused}")
    lits = seed_literals()
    vals = {str(v) for rows in SEED_ROWS.values() for r in rows for v in r.values() if v is not None}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(lits) and not undeclared, f"every quoted literal in seed()'s source ({len(lits)}) is a value of SEED_ROWS "
          "(so the CHECK evaluation above covers it) or a declared non-column literal", f"{undeclared}")
    obs_def = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                   "'indicator_obs_source_provider_chk' AND conrelid = "
                                   "'market_data.indicator_observations'::regclass", fetch="fetchval")
    real_obs = await platform(conn, "SELECT DISTINCT o.source_provider FROM market_data.indicator_observations o "
                                    "JOIN market_data.indicator_series s ON s.id = o.series_id WHERE s.series_key = "
                                    "ANY($1::text[]) AND o.valid_to IS NULL AND o.system_to IS NULL",
                              ["fred.dgs10", "fred.t10y3m", "fred.dgs2"], fetch="fetch")
    real_ser = {r["series_key"]: r["source_provider"] for r in await platform(
        conn, "SELECT series_key, source_provider FROM market_data.indicator_series WHERE series_key = ANY($1::text[])",
        ["fred.dgs10", "fred.t10y3m", "fred.dgs2"])}
    from services.market_data import adapters as MDA
    check(bool(obs_def) and "source_provider IS NULL" in obs_def and "'none'" not in obs_def
          and [r["source_provider"] for r in real_obs] == [OBS_PROVIDER]
          and set(real_ser.values()) == {"fred"} and SERIES_PROVIDER not in MDA.REAL_PROVIDERS,
          "the fixture series mirror the real FRED proxies: real fred.dgs10 / t10y3m / dgs2 rows are registered "
          "'fred' and their observations carry source_provider NULL (= the series' own provider), as the fixture's do; "
          f"the fixture series' provider {SERIES_PROVIDER!r} has no adapter, so no nightly job fetches it",
          f"chk={obs_def} real obs={[r['source_provider'] for r in real_obs]} series={real_ser} "
          f"adapters={MDA.REAL_PROVIDERS}")
    mc = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                              "'public.cash_policies'::regclass AND conname = 'cash_policies_maker_checker_chk'",
                        fetch="fetchval")
    check(bool(mc) and "approved_by = ANY (maker_ids)" in mc and "NOT" in mc,
          "cash_policies_maker_checker_chk: approved_by ∉ maker_ids, in the database", f"{mc}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(len(idx) == len(UNIQUES) and len(idx) > 0 and all("UNIQUE" in v for v in idx.values()),
          f"{len(idx)} unique indexes read (one ACTIVE policy per scope, one OPEN version per scope, versions, one cash "
          "asset per org, one series per key, one setting per org, one approved tax profile)",
          f"missing={sorted(set(UNIQUES) - set(idx))}")
    act = idx.get("cash_policies_active_household_uq", "")
    check("status = 'active'" in act and "system_to IS NULL" in act,
          "the household active index is partial on status = 'active' AND system_to IS NULL", act)
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = 'public.cash_policies'::regclass")]
    check(len(fks) >= 8, "cash_policies' foreign keys are read (org, household, entity, users, task, self)",
          f"{len(fks)} {fks}")
    ops = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                 "tablename = 'cash_policies'")}
    check(ops == {"SELECT", "INSERT", "UPDATE", "DELETE"}, "cash_policies: a policy for every operation", f"{sorted(ops)}")
    gone = await platform(conn, "SELECT to_regclass('public.cash_coverage_minimums')::text", fetch="fetchval")
    check(gone is None, "cash01's cash_coverage_minimums is gone (the minimum has ONE home)", f"{gone}")
    it = await platform(conn, "SELECT maker_duty, checker_duty, floor_class, self_approval_capable FROM sod_item_types "
                              "WHERE item_type = 'cash_policy'", fetch="fetchrow")
    rules = {r["rule_kind"]: dict(r) for r in await platform(
        conn, "SELECT rule_kind, self_approval, enforce_floor, status FROM sod_rules WHERE org_id IS NULL AND "
              "item_type = 'cash_policy' AND status = 'active'")}
    duties = {r["duty_key"]: list(r["permissions"]) for r in await platform(
        conn, "SELECT duty_key, permissions FROM sod_duties WHERE duty_key = ANY($1::text[])",
        ["set_cash_policy", "approve_cash_policy"])}
    check(it is not None and it["maker_duty"] == "set_cash_policy" and it["checker_duty"] == "approve_cash_policy"
          and it["floor_class"] is None and it["self_approval_capable"] is False
          and set(rules) == {"default", "policy"} and rules["policy"]["self_approval"] == "never"
          and rules["policy"]["enforce_floor"] is True
          and duties == {"set_cash_policy": ["manage_cash_policy"], "approve_cash_policy": ["manage_cash_policy"]},
          "sod: item type cash_policy with its duties, the default rule and the platform policy (never self-approved, "
          "floor enforced; not a money-movement floor class)", f"{dict(it) if it else None} {rules} {duties}")
    check(sod.CASH_POLICY in sod.ITEM_TYPES and sod.item_type_for_task_kind(CP.TASK) == sod.CASH_POLICY
          and HT.get_kind(CP.TASK).maker_checker is True,
          "the task kind cash_policy_approval is maker-checker and decides the cash_policy item type")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{by}")
    # every INSERT's columns against docs/schema_snapshot.sql
    snap = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    badc = []
    for t, cols in SEED_COLUMNS.items():
        m = re.search(rf"^-- ===== {re.escape(t)} =====\n((?:--.*\n)+)", snap, re.M)
        have = set(re.findall(r"^--\s{3}(\w+)\s", m.group(1), re.M)) if m else set()
        miss = [c for c in cols if c not in have]
        if not m or miss:
            badc.append((t, "absent" if not m else miss))
    check(len(SEED_COLUMNS) > 0 and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables) exist in "
          "docs/schema_snapshot.sql", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and len(idx) == len(UNIQUES) and by is False
                          and not badc and gone is None)


def seed_literals() -> set[str]:
    """Every single-quoted literal in seed()'s own source (code only — comments stripped)."""
    src = _code_only(inspect.getsource(seed))
    return set(re.findall(r"'([^'$%{}]+)'", src))


async def seed_rows_vs_checks(conn) -> tuple[int, list]:
    """Each CHECK on each SEED_ROWS table, evaluated in the database on each row shape (absent columns NULL, as a
    CHECK sees them). Returns (constraints evaluated, [(table, constraint, row index)] refused)."""
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


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    s = int(await platform(conn, "SELECT count(*) FROM market_data.indicator_series WHERE series_key LIKE $1",
                           SERIES_PREFIX + "%", fetch="fetchval"))
    p = int(await platform(conn, "SELECT count(*) FROM cash_policies WHERE household_id::text LIKE $1 OR "
                                 "entity_id::text LIKE $1", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    return check(o == 0 and u == 0 and e == 0 and s == 0 and p == 0,
                 "the reserved prefixes (fixture id range, sign-in tag, 'CASH02 VERIFY' names, 'cash02vfy.' series) "
                 "match no real row", f"orgs={o} users={u} entities={e} series={s} policies={p}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH02 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH02 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH02 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, (org, etype, sub, nm) in ENTS.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, sub_type) VALUES "
                               "($1, $2, $3, 'active', $4::entity_type, $5)", eid, org, NAME + nm, etype, sub)
        for hh, (org, nm, members) in HOUSEHOLDS.items():
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + nm + " family")
            for e in members:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        for uid, ents in ((U_MEM, HOUSEHOLDS[H1][2]), (U_MEM2, HOUSEHOLDS[H2][2])):
            for e in ents:
                await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                                   "effective_from) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day')",
                                   ORG_A, e, uid)
        # VISIBILITY1 ON in org A: advisors assigned to both families, U_VIEW and U_RS to the Policy family only
        for uid, ents in ((U_ADV, (E_T, E_ILIT, E_O)), (U_ADV2, (E_T, E_ILIT, E_O)), (U_VIEW, (E_T, E_ILIT)),
                          (U_RS, (E_T, E_ILIT))):
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
        for pid, owner, asset, mv in POSITIONS:
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "market_value, authority, source_system) VALUES (COALESCE($1, gen_random_uuid()), $2, $3, $4, $5, "
                "'value', $6, 'stated', 'manual')", pid, ENTS[owner][0], owner, asset, TODAY - timedelta(days=1),
                Decimal(mv))
        # one paid capital call in the trailing 3 months (the questionnaire's cross-check): 60,000 on 2026-10-01
        await conn.execute(
            "INSERT INTO portfolio.transactions (org_id, position_id, transaction_type_code, trade_date, gross_amount, "
            "net_amount, currency_code, authority, source_system) VALUES ($1, $2, 'call_investment', '2026-10-01', "
            "60000.00, 60000.00, 'USD', 'stated', 'manual')", ORG_A, P_T)
        await conn.execute(
            "INSERT INTO tax_profiles (org_id, subject_type, subject_id, tax_year, version_no, filing_status, "
            "federal_ordinary_rate, federal_lt_rate, niit_applies, niit_rate, state_rate, city_rate, as_of, source, "
            "source_note, status, entered_by, checker_id, decided_at) VALUES ($1, 'household', $2, $3, 1, "
            "'married_filing_jointly', $4, $5, $6, $7, $8, $9, $10, 'adviser', 'verify fixture', 'approved', $11, "
            "$12, now())", ORG_A, H1, TODAY.year, TAX["federal_ordinary_rate"], TAX["federal_lt_rate"],
            TAX["niit_applies"], TAX["niit_rate"], TAX["state_rate"], TAX["city_rate"], TODAY, U_ADV, U_OA)
    op = await operator_conn()
    try:
        async with op.transaction():
            for key, pts in SERIES.items():
                sid = await op.fetchval(
                    "INSERT INTO market_data.indicator_series (series_key, name, category, frequency, cost_tier, "
                    "source_provider, ingest_status) VALUES ($1, $2, 'Rates & credit', 'daily', 'free', $3, "
                    "'active') RETURNING id", key, NAME + key, SERIES_PROVIDER)
                for d, v in pts:
                    await op.execute("INSERT INTO market_data.indicator_observations (series_id, obs_date, value, "
                                     "source_provider) VALUES ($1, $2, $3, $4)", sid, d, Decimal(v), OBS_PROVIDER)
    finally:
        await close_conn(op)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true",
          f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/2 orgs")
    ok_roles = await check_fixture_roles(conn)
    # the hand-computed schedule, recorded through cash01's own API
    codes = []
    for payer, st, nm, amt, due in OBLIGATIONS:
        hh = H1 if payer in (E_T, E_ILIT) else H2
        code, b = await api(U_ADV, "POST", f"/api/v1/cash/households/{hh}/obligations",
                            {"entity_id": str(payer), "source_type": st, "name": NAME + nm, "amount": amt,
                             "due_date": due, "certainty": "firm"})
        codes.append(code)
    code_b, _ = await api(U_B, "POST", f"/api/v1/cash/households/{HB}/obligations",
                          {"entity_id": str(E_B), "source_type": "one_off", "name": NAME + "org B item",
                           "amount": "1000.00", "due_date": "2027-01-01", "certainty": "firm"})
    ok_obl = check(codes == [201] * len(OBLIGATIONS) and code_b == 201,
                   "the fixture schedule is recorded through the cash01 API (201 each)", f"{codes} {code_b}")
    S["seeded"] = bool(ok_roles and ok_obl)


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
            ok = ok and bool(pr.is_internal) is (rname != "member") and not pr.is_staff
            ok = ok and AZ.is_member_principal(pr) is (rname == "member")
            ok = ok and (("manage_cash_policy" in perms) is (rname in ("advisor", "org_admin", "support_staff")))
            if not ok:
                bad.append((short, names, sorted(perms & keys), bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisor / org admin / "
                 "support staff hold manage_cash_policy, compliance and members do not; none is a staff bypass)",
                 f"{bad}")


async def treasury(uid, hh) -> tuple[int, dict]:
    return await api(uid, "GET", f"/api/v1/cash/households/{hh}/treasury")


def scope_of(env, st="household", sid=None) -> dict:
    for s in ((env.get("policy") or {}).get("scopes") or []):
        if s.get("scope_type") == st and (sid is None or s.get("scope_id") == str(sid)):
            return s
    return {}


def sleeve(derived, key) -> dict:
    return next((s for s in (derived or {}).get("sleeves") or [] if s.get("key") == key), {})


def body(**over) -> dict:
    b = {"scope_type": "household", "preset": "custom", "near_months": 3, "reserve_months": 12, "horizon_months": 24,
         "rung_count": 8, "buffer_months": "0", "objective_score": 3, "min_coverage_months": "6"}
    b.update(over)
    return {k: v for k, v in b.items() if v is not None}


def policy_fields(**over) -> dict:
    """What the ROUTE hands CP.normalise: the body through routers.cash.PolicyIn.fields() (which drops scope_type /
    entity_id / reason / questionnaire) — cash02 fix 1: the pure checks passed the raw route body, and normalise
    rightly refuses a non-policy field."""
    return RC.PolicyIn(**body(**over)).fields()


async def preview(uid, hh, **over):
    return await api(uid, "POST", f"/api/v1/cash/households/{hh}/policy/preview", body(**over))


async def y_calc(conn):
    section("[Y] Sleeves derived from the obligation schedule (hand-computed); presets + questionnaire deterministic")
    # household H1, N=3 R=12 H=24, buffer 0. Windows from TODAY 2026-11-09:
    #   Near-term [2026-11-09, 2027-02-09): o1 100,000 + ILIT premium 60,000            = 160,000.00
    #   Reserve   [2027-02-09, 2027-11-09): o2 50,000 (ON the N boundary → Reserve)      =  50,000.00
    #   Long-term [2027-11-09, 2028-11-09): o3 70,000 (ON the R boundary) + o4 30,000   = 100,000.00
    # liquid 1,010,000.00 → 15.84 % → 15.8; 4.95 % → 5.0 (half-up); 9.90 % → 9.9; unallocated 700,000.00
    code, b = await preview(U_ADV, H1)
    d = b.get("derived") or {}
    nt, rs, lt = sleeve(d, "near_term"), sleeve(d, "reserve"), sleeve(d, "long_term")
    check(code == 200 and dec(nt.get("amount")) == Decimal("160000.00") and dec(rs.get("amount")) == Decimal("50000.00")
          and dec(lt.get("amount")) == Decimal("100000.00") and dec(d.get("liquid")) == Decimal("1010000.00")
          and dec(d.get("unallocated")) == Decimal("700000.00"),
          "N=3 / R=12 / 24 months: Near-term 160,000, Reserve 50,000, Long-term 100,000 of 1,010,000 liquid "
          "(unallocated 700,000) — the server's derivation equals the hand computation", f"{code} {dumps(b)}")
    check(dec(nt.get("pct")) == Decimal("15.8") and dec(rs.get("pct")) == Decimal("5.0")
          and dec(lt.get("pct")) == Decimal("9.9"), "% of the liquid total: 15.8 / 5.0 / 9.9 (one decimal, half-up)",
          f"{nt.get('pct')} {rs.get('pct')} {lt.get('pct')}")
    check(nt.get("from") == TODAY.isoformat() and nt.get("until") == add_months(TODAY, 3).isoformat()
          and rs.get("until") == add_months(TODAY, 12).isoformat() and lt.get("until") == add_months(TODAY, 24).isoformat()
          and [nt.get("months"), rs.get("months"), lt.get("months")] == [3, 9, 12],
          "the windows are half-open month windows from today (3 / 9 / 12 months)",
          f"{nt.get('from')}..{nt.get('until')} {rs.get('until')} {lt.get('until')}")
    keys_r = rs.get("keys") or []
    keys_l = lt.get("keys") or []
    check(len(keys_r) == 1 and "2027-02-09" in keys_r[0] and len(keys_l) == 2 and any("2027-11-09" in k for k in keys_l),
          "an obligation exactly ON a boundary belongs to the LATER sleeve (Feb 9 → Reserve, Nov 9 → Long-term)",
          f"{keys_r} {keys_l}")
    # the buffer: 1 month × the average monthly outflow (12 months: 100,000 + 60,000 + 50,000 = 210,000 → 17,500)
    code, b = await preview(U_ADV, H1, buffer_months="1")
    d2 = b.get("derived") or {}
    check(code == 200 and dec(d2.get("monthly_outflow")) == Decimal("17500.00")
          and dec(sleeve(d2, "near_term").get("amount")) == Decimal("177500.00")
          and dec((d2.get("coverage") or {}).get("months")) == Decimal("57.7"),
          "a 1-month buffer adds the average monthly outflow (17,500) to Near-term (177,500); coverage 57.7 months",
          f"{code} {dumps(d2)}")
    # an EMPTY Reserve window (N=4, R=8: [2027-03-09, 2027-07-09) holds nothing) — pure, Decimal
    lines = CC.scope_lines(S["report"], "household")
    e = CP.evaluate(CP.normalise(policy_fields(near_months=4, reserve_months=8)), lines, Decimal("1010000.00"), {}, None, TODAY)
    er = next(s for s in e["sleeves"] if s["key"] == "reserve")
    en = next(s for s in e["sleeves"] if s["key"] == "near_term")
    check(er["amount"] == Decimal("0") and er["count"] == 0 and er["months"] == 4 and en["amount"] == Decimal("210000.00"),
          "an empty Reserve window (N=4, R=8) reads 0 with no obligations; Near-term holds 210,000 (Decimal compared)",
          f"{er['amount']} {er['count']} {en['amount']}")
    # the horizon SHORTER than R (H=9, R=12): Reserve cut at the horizon, Long-term empty — both say so
    e = CP.evaluate(CP.normalise(policy_fields(horizon_months=9)), lines, Decimal("1010000.00"), {}, None, TODAY)
    hr = next(s for s in e["sleeves"] if s["key"] == "reserve")
    hl = next(s for s in e["sleeves"] if s["key"] == "long_term")
    check(hr["months"] == 6 and hr["until"] == add_months(TODAY, 9) and hr["amount"] == Decimal("50000.00")
          and bool(hr["note"]) and hl["months"] == 0 and hl["amount"] == Decimal("0") and bool(hl["note"]),
          "a horizon shorter than R (9 < 12): Reserve clipped to month 9 (50,000), Long-term empty — each with a note",
          f"{hr['months']} {hr['amount']} {hr['note']} / {hl['months']} {hl['note']}")
    # presets
    pres = {p["key"]: p for p in b.get("presets") or []}
    sl, bl = pres.get("stay_liquid") or {}, pres.get("balanced") or {}
    check([sl.get("near_months"), sl.get("reserve_months"), bl.get("near_months"), bl.get("reserve_months")]
          == [24, 24, 0, 24] and dec(sleeve(sl, "near_term").get("amount")) == Decimal("327500.00")
          and dec(sleeve(bl, "reserve").get("amount")) == Decimal("310000.00")
          and dec(sleeve(bl, "near_term").get("amount")) == Decimal("17500.00"),
          "presets: Stay liquid = everything Near-term (N = R = 24: 310,000 + the 17,500 buffer); Balanced = "
          "everything Reserve (N = 0, R = 24: 310,000; the buffer stays Near-term)", f"{dumps(b.get('presets'))}")
    rec = [CP.normalise({"preset": "recommended", "objective_score": s}) for s in (1, 3, 5)]
    check([(r["near_months"], r["reserve_months"], r["objective"]) for r in rec]
          == [(6, 18, "stability"), (3, 12, "balanced"), (1, 6, "yield")]
          and [CP.recommend(s) for s in range(1, 6)] == [(6, 18), (4, 15), (3, 12), (2, 9), (1, 6)],
          "questionnaire endpoints: most stable → N 6 / R 18, most yield → N 1 / R 6, balanced → v1's 3 / 12 "
          "(monotone, deterministic, documented)", f"{rec}")
    code, b = await preview(U_ADV, H1, preset="recommended", objective_score=5, near_months=None, reserve_months=None)
    check(code == 200 and (b.get("fields") or {}).get("near_months") == 1
          and (b.get("fields") or {}).get("reserve_months") == 6 and (b.get("fields") or {}).get("objective") == "yield",
          "the preview route applies the Recommended mapping server-side (score 5 → 1 / 6)", f"{code} {dumps(b)}")
    code, b = await preview(U_ADV, H1, near_months=13, reserve_months=12)
    check(code == 422 and "boundary" in str(b.get("detail")), "N after R is refused (422, naming the boundary)",
          f"{code} {dumps(b)}")
    # questionnaire step 1: scheduled 210,000; trailing 3 months 60,000 (the 2026-10-01 call) → annualised 240,000
    code, b = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/policy/preview",
                        {**body(), "questionnaire": {"outflow_12m": "150000.00"}})
    q = b.get("questionnaire") or {}
    check(code == 200 and dec(q.get("scheduled_12m")) == Decimal("210000.00")
          and dec(q.get("trailing_3m")) == Decimal("60000.00")
          and dec(q.get("trailing_annualised")) == Decimal("240000.00")
          and [w["key"] for w in q.get("warnings") or []] == ["well_below"],
          "step 1: prefilled 210,000 from the schedule; 150,000 entered is well below the trailing 3 months annualised "
          "(240,000) → warned", f"{code} {dumps(q)}")
    code, b = await api(U_ADV, "POST", f"/api/v1/cash/households/{H2}/policy/preview",
                        {**body(), "questionnaire": {"outflow_12m": "50000.00"}})
    q2 = b.get("questionnaire") or {}
    check(code == 200 and q2.get("trailing_3m") is None and [w["key"] for w in q2.get("warnings") or []]
          == ["no_history", "unscheduled"] and "no outflow history" in dumps(q2),
          "a family with no transactions reads 'no outflow history'; an entered figure above the schedule names the "
          "unscheduled difference", f"{code} {dumps(q2)}")


async def y_yields(conn):
    section("[Y] Estimated yields (FRED proxy via read_service, fixture series) and after-tax only with a profile")
    check(set(CP.YIELD_PROXIES) == set(REAL_PROXIES) and all(
        [c["kind"] for c in CP.YIELD_PROXIES[k]] == [c["kind"] for c in REAL_PROXIES[k]] for k in REAL_PROXIES)
          and all(k in PROXY_KEY_MAP for cands in REAL_PROXIES.values() for c in cands for k in c["keys"]),
          "the fixture proxies have the real proxies' exact shape (sleeves, candidate kinds, every key mapped)")
    code, b = await preview(U_ADV, H1)
    d = b.get("derived") or {}
    nt, rs, lt = sleeve(d, "near_term"), sleeve(d, "reserve"), sleeve(d, "long_term")
    y = nt.get("yield") or {}
    check(code == 200 and y.get("estimated") is True and dec(y.get("yield_pct")) == Decimal("4.10")
          and y.get("as_of") == "2026-11-05" and y.get("derived") is True
          and y.get("label") == "estimate — Treasury curve proxy, as of 2026-11-05",
          "Near-term: no 3-month series → derived 10-year − (10y–3m) on the latest COMMON date: 4.80 − 0.70 = 4.10, "
          "labelled with its as-of", f"{dumps(y)}")
    yl = lt.get("yield") or {}
    check(yl.get("estimated") is True and dec(yl.get("yield_pct")) == Decimal("4.50") and yl.get("as_of") == "2026-11-06",
          "Long-term: the 2-year proxy's latest observation ON or before today (4.50 on Nov 6; the Nov 12 9.99 is "
          "ignored)", f"{dumps(yl)}")
    yr = rs.get("yield") or {}
    want = f"not estimated — needs {SERIES_PREFIX}t6m or {SERIES_PREFIX}t1y"
    check(yr.get("estimated") is False and yr.get("text") == want,
          "Reserve: a missing series reads 'not estimated — needs <series>'", f"{dumps(yr)}")
    # after-tax: the household HAS a fixture profile (fed 0.37 + NIIT 0.038; state 0.0685 / city NOT applied)
    at = nt.get("after_tax") or {}
    check(at.get("estimated") is True and dec(at.get("yield_pct")) == Decimal("2.43")
          and dec((lt.get("after_tax") or {}).get("yield_pct")) == Decimal("2.66") and "state" in (at.get("label") or ""),
          "after-tax with the household's profile: 4.10 × (1 − 0.408) = 2.43, 4.50 → 2.66 — state / local NOT applied "
          "(Treasury exemption), labelled", f"{dumps(at)} {dumps(lt.get('after_tax'))}")
    check(Decimal("4.10") * (1 - TAX["federal_ordinary_rate"] - TAX["niit_rate"] - TAX["state_rate"])
          < Decimal("2.43") - Decimal("0.2"), "(the state rate, had it been applied, would have moved the figure — "
          "the check above is not vacuous)")
    code, b = await preview(U_ADV, H1, scope_type="entity", entity_id=str(E_ILIT))
    ate = (sleeve(b.get("derived"), "near_term").get("after_tax") or {})
    check(code == 200 and ate.get("estimated") is False and ate.get("text") == "after-tax: needs a tax profile",
          "an entity with no tax profile reads 'after-tax: needs a tax profile'", f"{code} {dumps(ate)}")
    blend = (b.get("presets") or [])
    sl = next((p for p in blend if p.get("key") == "stay_liquid"), {})
    check(dec((sl.get("blended") or {}).get("yield_pct")) == Decimal("4.10")
          and (next((p for p in blend if p.get("key") == "balanced"), {}).get("blended") or {}).get("estimated") is False,
          "the blended estimate per preset: Stay liquid (all Near-term) 4.10; Balanced (all Reserve) not estimated",
          f"{dumps(blend)}")
    reads = await CP.read_yields(conn, TODAY)
    check(reads.get(SERIES_PREFIX + "t2y") == [(date(2026, 11, 4), Decimal("4.40")), (date(2026, 11, 6), Decimal("4.50"))]
          and reads.get(SERIES_PREFIX + "t6m") == [], "read_yields reads the window ending today through the read "
          "service (a future observation never appears; an unregistered key is empty, not an error)", f"{reads}")


async def save(uid, hh, **over):
    return await api(uid, "POST", f"/api/v1/cash/households/{hh}/policies", body(**over))


async def act(uid, hh, pid, action, note=None):
    return await api(uid, "POST", f"/api/v1/cash/households/{hh}/policies/{pid}/{action}",
                     None if action == "submit" else {"note": note})


async def decisions(conn, pid) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT outcome, checker_id::text AS c, site FROM sod_decisions WHERE org_id = $1 AND item_id = $2 "
              "ORDER BY created_at, id", ORG_A, str(pid))]


async def policy_row(op, pid) -> dict | None:
    r = await op.fetchrow("SELECT * FROM cash_policies WHERE id = $1::uuid AND system_to IS NULL", str(pid))
    return dict(r) if r else None


async def y_maker_checker(conn):
    section("[Y] Maker-checker through services.sod: refusals recorded, nothing changed; a different person approves")
    code, b = await save(U_ADV, H1, min_coverage_months="8", reason="verify v1")
    p1 = (b.get("policy") or {}).get("id")
    S["p1"] = p1
    op = await operator_conn()
    try:
        r = await policy_row(op, p1 or str(_id(9999)))
        check(code == 201 and r is not None and r["status"] == "draft" and r["version_no"] == 1
              and r["min_coverage_months"] == Decimal("8") and [str(m) for m in r["maker_ids"]] == [str(U_ADV)],
              "a DRAFT is saved (201) and persisted (re-read on an independent connection: draft, v1, minimum 8, "
              "maker U_ADV)", f"{code} {dumps(b)} {r}")
        code, b = await save(U_ADV2, H1, min_coverage_months="7")
        r = await policy_row(op, p1)
        arch = int(await op.fetchval("SELECT count(*) FROM cash_policies WHERE archived_from_id = $1::uuid", p1))
        check(code == 201 and (b.get("policy") or {}).get("id") == p1 and r["min_coverage_months"] == Decimal("7")
              and {str(m) for m in r["maker_ids"]} == {str(U_ADV), str(U_ADV2)} and arch == 1,
              "editing the open draft keeps its id, archives the prior state (system axis) and adds the editor to the "
              "makers", f"{code} {r['maker_ids'] if r else None} archived={arch}")
        code, env = await treasury(U_ADV, H1)
        h = (env.get("coverage") or {})
        check(code == 200 and h.get("min_months") == "6", "a draft moves no minimum (the household still reads 6)",
              f"{h.get('min_months')}")
        code, b = await act(U_ADV, H1, p1, "submit")
        r = await policy_row(op, p1)
        S["task1"] = (b.get("policy") or {}).get("approval_task_id")
        check(code == 200 and r["status"] == "pending_approval" and r["approval_task_id"] is not None
              and str(r["submitted_by"]) == str(U_ADV),
              "submit → awaiting approval, with its human task", f"{code} {dumps(b)}")
        before = await op.fetchval("SELECT md5(t::text) FROM cash_policies t WHERE id = $1::uuid AND system_to IS NULL",
                                   p1)
        n0 = len(await decisions(conn, p1))
        code_a, ba = await act(U_ADV, H1, p1, "approve", "self")
        code_b, bb = await act(U_ADV2, H1, p1, "approve", "editor")
        after = await op.fetchval("SELECT md5(t::text) FROM cash_policies t WHERE id = $1::uuid AND system_to IS NULL",
                                  p1)
        ds = await decisions(conn, p1)
        refused = [x for x in ds[n0:] if x["outcome"] == "refused"]
        check(code_a == 403 and code_b == 403 and before == after and {x["c"] for x in refused} == {str(U_ADV),
                                                                                                  str(U_ADV2)}
              and len(refused) == 2,
              "the submitter AND the editor (both makers) are refused (403); each refusal is in sod_decisions; the "
              "policy is unchanged", f"{code_a} {dumps(ba)} {code_b} {dumps(bb)} {ds}")
        aud = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = 'sod.refused' AND "
                                       "payload->>'item_id' = $2", ORG_A, str(p1), fetch="fetchval"))
        check(aud == 2, "each refusal is audited (audit_log 'sod.refused', by the fixture item id)", f"{aud}")
        code_v, bv = await act(U_VIEW, H1, p1, "approve", "valid body")
        check(code_v == 403 and "manage_cash_policy" in str(bv.get("detail")) and len(await decisions(conn, p1)) == len(ds),
              "a staff member without manage_cash_policy is refused by the route gate (403 naming it); no decision "
              "recorded", f"{code_v} {dumps(bv)}")
        code, b = await act(U_OA, H1, p1, "approve", "verify: approved")
        r = await policy_row(op, p1)
        ds2 = await decisions(conn, p1)
        check(code == 200 and r["status"] == "active" and str(r["approved_by"]) == str(U_OA)
              and r["approved_at"] is not None and r["valid_to"] is None
              and any(x["outcome"] == "allowed" and x["c"] == str(U_OA) for x in ds2),
              "a DIFFERENT eligible person (U_OA) approves → active; the allowed decision is in sod_decisions",
              f"{code} {dumps(b)} {ds2}")
        code, env = await treasury(U_ADV, H1)
        h = env.get("coverage") or {}
        rows = {x["entity_id"]: x for x in env.get("entities") or []}
        check(h.get("min_months") == "7" and h.get("min_basis") == "household policy"
              and (rows.get(str(E_T)) or {}).get("min_months") == "7"
              and (rows.get(str(E_T)) or {}).get("min_basis") == "household policy",
              "cash01's coverage now reads the minimum (7) from the ACTIVE household policy; entities inherit it",
              f"{h.get('min_months')} {h.get('min_basis')} {rows.get(str(E_T))}")
        # v2: an edit of the ACTIVE policy is a NEW draft version; the active one stays in force until approved
        code, b = await save(U_ADV2, H1, near_months=4, min_coverage_months="7", reason="verify v2")
        p2 = (b.get("policy") or {}).get("id")
        S["p2"] = p2
        r1, r2 = await policy_row(op, p1), await policy_row(op, p2 or str(_id(9998)))
        code_e, env = await treasury(U_ADV, H1)
        sc = scope_of(env)
        check(code == 201 and p2 and p2 != p1 and r2["version_no"] == 2 and r2["status"] == "draft"
              and r1["status"] == "active"
              and [d["field"] for d in ((sc.get("open") or {}).get("diff") or [])] == ["near_months"],
              "editing the active policy creates draft v2; v1 stays active; the envelope shows the diff (near_months)",
              f"{code} {dumps(b)} {dumps(sc.get('open'))}")
        await act(U_ADV2, H1, p2, "submit")
        code, b = await act(U_ADV, H1, p2, "approve", "v2 ok")
        r1, r2 = await policy_row(op, p1), await policy_row(op, p2)
        check(code == 200 and r2["status"] == "active" and r1["status"] == "retired" and r1["valid_to"] is not None
              and r1["retired_at"] is not None and str(r1["superseded_by"]) == p2
              and (b.get("policy") or {}).get("retired_policy_id") == p1,
              "U_ADV (not a maker of v2) approves → v2 active; v1 RETIRED bi-temporally (valid_to set, superseded_by v2)",
              f"{code} {dumps(b)} v1={r1['status']} {r1['valid_to']}")
        n_active = int(await op.fetchval("SELECT count(*) FROM cash_policies WHERE household_id = $1 AND status = "
                                         "'active' AND system_to IS NULL", H1))
        hist = int(await op.fetchval("SELECT count(*) FROM cash_policies WHERE household_id = $1", H1))
        check(n_active == 1 and hist >= 6, "one active policy for the household; every prior state kept (system-axis "
              "archive rows)", f"active={n_active} rows={hist}")
        # the database itself: a second active per scope, and approved_by ∈ makers
        err1 = err2 = None
        try:
            async with op.transaction():
                await op.execute(
                    "INSERT INTO cash_policies (org_id, scope_type, household_id, version_no, near_months, "
                    "reserve_months, horizon_months, rung_count, buffer_months, objective, objective_score, preset, "
                    "min_coverage_months, status, maker_ids, created_by, submitted_by, submitted_at, approval_task_id, "
                    "approved_by, approved_at) VALUES ($1, 'household', $2, 99, 3, 12, 24, 8, 0, 'balanced', 3, "
                    "'custom', 6, 'active', ARRAY[$3::uuid], $3::uuid, $3::uuid, now(), $4::uuid, $5::uuid, now())",
                    ORG_A, H1, U_ADV, r2["approval_task_id"], U_OA)
        except asyncpg.UniqueViolationError as exc:
            err1 = exc.constraint_name
        try:
            async with op.transaction():
                await op.execute(
                    "INSERT INTO cash_policies (org_id, scope_type, entity_id, version_no, near_months, reserve_months, "
                    "horizon_months, rung_count, buffer_months, objective, objective_score, preset, min_coverage_months, "
                    "status, maker_ids, created_by, approved_by) VALUES ($1, 'entity', $2, 98, 3, 12, 24, 8, 0, "
                    "'balanced', 3, 'custom', 6, 'draft', ARRAY[$3::uuid], $3::uuid, $3::uuid)", ORG_A, E_O, U_ADV)
        except asyncpg.CheckViolationError as exc:
            err2 = exc.constraint_name
        check(err1 == "cash_policies_active_household_uq", "a second ACTIVE policy for the scope is refused by the "
              "unique index itself (a direct insert)", f"{err1}")
        check(err2 == "cash_policies_maker_checker_chk", "approved_by ∈ maker_ids is refused by the database itself",
              f"{err2}")
        # reject, then an entity override approved
        code, b = await save(U_ADV, H1, scope_type="entity", entity_id=str(E_ILIT), min_coverage_months="3")
        pe = (b.get("policy") or {}).get("id")
        await act(U_ADV, H1, pe, "submit")
        code_n, bn = await act(U_OA, H1, pe, "reject", None)
        code_r, br = await act(U_OA, H1, pe, "reject", "verify: not yet")
        re_ = await policy_row(op, pe)
        check(code_n == 422 and code_r == 200 and re_["status"] == "rejected" and re_["reject_reason"] == "verify: not yet"
              and str(re_["rejected_by"]) == str(U_OA),
              "a rejection needs a reason (422 without); with one → rejected, the reason kept", f"{code_n} {dumps(bn)} "
              f"{code_r} {dumps(br)}")
        code, env = await treasury(U_ADV, H1)
        il = next((x for x in env.get("entities") or [] if x["entity_id"] == str(E_ILIT)), {})
        check(il.get("min_months") == "7" and il.get("min_basis") == "household policy",
              "a rejected override moves nothing (the ILIT still inherits the household's 7)", f"{dumps(il)}")
        code, b = await save(U_ADV, H1, scope_type="entity", entity_id=str(E_ILIT), min_coverage_months="3")
        pe2 = (b.get("policy") or {}).get("id")
        await act(U_ADV, H1, pe2, "submit")
        code, b = await act(U_OA, H1, pe2, "approve", "ok")
        code_t, env = await treasury(U_ADV, H1)
        il = next((x for x in env.get("entities") or [] if x["entity_id"] == str(E_ILIT)), {})
        r = await policy_row(op, pe2)
        check(code == 200 and r["version_no"] == 2 and il.get("min_months") == "3" and il.get("min_basis") ==
              "entity policy" and "below_minimum" in (il.get("flags") or []),
              "an approved entity override (v2 after the rejected v1) sets the ILIT's own minimum (3): 10,000 ÷ 5,000 a "
              "month = 2.0 months, below it", f"{code} {dumps(il)}")
        # the approved policy's task carried what the checker approved (the derived view as of submit)
        t = await platform(conn, "SELECT r.context FROM workflow_run_steps rs JOIN workflow_runs r ON r.id = "
                                 "rs.workflow_run_id WHERE rs.id = $1::uuid", r["approval_task_id"], fetch="fetchval")
        ctx = json.loads(t) if isinstance(t, str) else (t or {})
        pay = ((ctx.get("human_task") or {}).get("payload") or {})
        check(((pay.get("derived_at_submit") or {}).get("sleeves") or [{}])[0].get("key") == "near_term"
              and (pay.get("policy") or {}).get("min_coverage_months") == "3",
              "the approval task's payload records what was approved (the policy + its derived sleeves as of submit) — "
              "no snapshot column", f"{list(pay)}")
    finally:
        await close_conn(op)


async def y_minimum(conn):
    section("[Y] min_coverage_months has ONE home; cash01's coverage reads it")
    src = inspect.getsource(CC.minimums)
    check("FROM cash_policies" in src and "status = 'active'" in src and not hasattr(CC, "set_minimum"),
          "cash_coverage.minimums reads ACTIVE cash_policies; no direct setter remains")
    code, b = await api(U_ADV, "PUT", f"/api/v1/cash/households/{H1}/minimums",
                        {"scope_type": "household", "months": "4", "reason": "valid body"})
    check(code in (404, 405), "cash01's PUT …/minimums is gone (404 / 405)", f"{code} {dumps(b)}")
    n = await platform(conn, "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND "
                             "table_name IN ('cash_policies', 'cash_coverage_minimums') AND column_name IN "
                             "('min_months', 'min_coverage_months')", fetch="fetchval")
    check(int(n) == 1, "the minimum coverage has one column left (cash_policies.min_coverage_months; cash01's "
          "cash_coverage_minimums.min_months is gone)", f"{n}")


async def y_inbox(conn):
    section("[Y] The task appears in the approval inbox; bulk-approve respects the gate")
    code, b = await save(U_ADV, H2, min_coverage_months="5")
    ph2 = (b.get("policy") or {}).get("id")
    code_s, bs = await act(U_ADV, H2, ph2, "submit")
    tid = (bs.get("policy") or {}).get("approval_task_id")
    c1, m_oa = await api(U_OA, "GET", "/api/v1/human-tasks/mine")
    c2, m_adv = await api(U_ADV, "GET", "/api/v1/human-tasks/mine")
    check(c1 == 200 and c2 == 200 and tid in [t["id"] for t in m_oa.get("actionable") or []]
          and any(t["id"] == tid and t["kind"] == CP.TASK for t in m_oa.get("actionable") or [])
          and tid in [t["id"] for t in m_adv.get("waiting_on_others") or []]
          and tid not in [t["id"] for t in m_adv.get("actionable") or []],
          "the cash_policy_approval task is actionable for a different eligible person and 'waiting on others' for "
          "its maker", f"{tid} {c1} {c2}")
    c, env = await api(U_ADV, "GET", "/api/v1/approvals/bulk?type=cash_policy")
    item = next((i for i in env.get("rows") or [] if i.get("id") == ph2), {})
    check(c == 200 and item.get("actions") == [] and item.get("blocked") == "maker",
          "bulk list: the maker's own item offers no decision (blocked: maker)", f"{c} {dumps(item)}")
    n0 = len(await decisions(conn, ph2))
    c, out = await api(U_ADV, "POST", "/api/v1/approvals/bulk", {"type": "cash_policy", "item_ids": [ph2],
                                                                 "decision": "approve"})
    res = (out.get("results") or [{}])[0]
    op = await operator_conn()
    try:
        st = (await policy_row(op, ph2) or {}).get("status")
        ds = await decisions(conn, ph2)
        check(c == 200 and res.get("result") == "refused" and res.get("code") == 403 and st == "pending_approval"
              and any(x["outcome"] == "refused" and x["c"] == str(U_ADV) for x in ds[n0:]),
              "bulk-approve by the maker: refused per item (403) through the same gate, recorded, nothing changed",
              f"{c} {dumps(out)} {st}")
        c, out = await api(U_OA, "POST", "/api/v1/approvals/bulk", {"type": "cash_policy", "item_ids": [ph2],
                                                                    "decision": "approve"})
        st = (await policy_row(op, ph2) or {}).get("status")
        check(c == 200 and (out.get("results") or [{}])[0].get("result") == "approved" and st == "active",
              "bulk-approve by a different eligible person: approved → active", f"{c} {dumps(out)} {st}")
    finally:
        await close_conn(op)
    c, _env = await api(U_VIEW, "GET", "/api/v1/approvals/bulk?type=cash_policy")
    check(c == 403, "a compliance reader (no manage_cash_policy) cannot open the cash-policy bulk list", f"{c}")
    c, _env = await api(U_MEM, "GET", "/api/v1/approvals/bulk?type=cash_policy")
    check(c == 403, "a member cannot open it (view_portfolio is not enough)", f"{c}")


async def y_visibility(conn):
    section("[Y] Member sees only their family, read-only with library labels; staff only visible families; isolation")
    code, env = await treasury(U_MEM, H1)
    pol = env.get("policy") or {}
    sc = scope_of(env)
    lab = (env.get("labels") or {}).get(CP.YIELD_KEY) or {}
    check(code == 200 and pol.get("member_view") is True and pol.get("actions") == []
          and (pol.get("vocabularies") or {}).get("editable") == [] and sc.get("active") is not None
          and "open" not in sc and "history" not in sc and "questionnaire" not in sc and "presets" not in sc
          and (env.get("permissions") or {}).get("can_author_policy") is False,
          "a member reads their family's ACTIVE policy and sleeves READ-ONLY (no drafts, history, questionnaire or "
          "write control)", f"{code} {dumps(pol)[:2000]}")
    check(bool(lab.get("text")) and lab.get("key") == CP.YIELD_KEY and "counsel" in (lab.get("text") or "")
          and (lab.get("source") == "library" or lab.get("text") == CP.YIELD_ESTIMATE)
          and bool(((env.get("labels") or {}).get(CC.ESTIMATE_KEY) or {}).get("text")),
          "the member envelope carries cash.yield_estimate and cash.coverage_estimate from the disclosure library",
          f"{dumps(lab)}")
    code2, _b = await treasury(U_MEM, H2)
    code3, b3 = await preview(U_MEM, H1)
    check(code2 == 404 and code3 == 403, "a member never opens another family (404) and cannot preview / author (403)",
          f"{code2} {code3} {dumps(b3)}")
    c_rs1, _e1 = await treasury(U_RS, H1)
    c_rs2, _e2 = await treasury(U_RS, H2)
    c_rs3, b_rs3 = await save(U_RS, H2, min_coverage_months="9")
    c_rsb, env_b = await api(U_RS, "GET", "/api/v1/approvals/summary")
    check(c_rs1 == 200 and c_rs2 == 404 and c_rs3 == 404,
          "VISIBILITY1 ON: a restricted author opens their family (200), not another (404), and cannot draft for it",
          f"{c_rs1} {c_rs2} {c_rs3} {dumps(b_rs3)}")
    check(c_rsb == 200, "the restricted author reads the approvals summary over visible families only",
          f"{c_rsb} {dumps(env_b)}")
    # org isolation, both directions (the app, and RLS on a non-bypass connection)
    code, _b = await treasury(U_B, H1)
    code_d, _b2 = await act(U_B, H1, S.get("p2"), "approve", "x")
    code_s, bb = await save(U_B, HB, min_coverage_months="2")
    pb = (bb.get("policy") or {}).get("id")
    a_sees_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_policies WHERE id = $1::uuid", pb or str(_id(9997)),
                                fetch="fetchval"))
    b_sees_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_policies WHERE id = $1::uuid", S.get("p2"),
                                fetch="fetchval"))
    a_sees_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_policies WHERE id = $1::uuid", S.get("p2"),
                                fetch="fetchval"))
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(code == 404 and code_d == 404 and code_s == 201 and a_sees_b == 0 and b_sees_a == 0 and a_sees_a == 1
          and by is False, "org isolation both directions: B's caller gets 404 on A's household and policy; under RLS "
          "(rolbypassrls = false) A sees none of B's policy rows and B none of A's, while A sees its own",
          f"{code} {code_d} {code_s} a→b={a_sees_b} b→a={b_sees_a} a→a={a_sees_a} bypass={by}")
    err = None
    try:
        await scoped(conn, ORG_B, "INSERT INTO cash_policies (org_id, scope_type, household_id, version_no, near_months, "
                                  "reserve_months, objective, preset, maker_ids, created_by) VALUES ($1, 'household', "
                                  "$2, 97, 3, 12, 'balanced', 'custom', ARRAY[$3::uuid], $3::uuid)", ORG_A, H1, U_B,
                     fetch="execute")
    except asyncpg.InsufficientPrivilegeError as exc:
        err = type(exc).__name__
    check(err == "InsufficientPrivilegeError", "an INSERT naming another org is refused by the RLS WITH CHECK", f"{err}")


async def y_olivo(conn):
    section("[Y] The Olivo loader --dry-run writes nothing")
    import load_olivo_cash_policy as LP

    have = list(inspect.signature(LP.main).parameters)
    check(have == ["argv"], "load_olivo_cash_policy.main(argv) — the signature this section calls", f"{have}")
    before = await real_fingerprints(conn)
    orig = DBC.hydrate
    DBC.hydrate = lambda: None          # already hydrated: no second Doppler call (no network during the run)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = await LP.main(["--dry-run"])
    finally:
        DBC.hydrate = orig
    out = buf.getvalue()
    after = await real_fingerprints(conn)
    check(rc == 0 and "DRY RUN — nothing written" in out and "Recommended policy (household)" in out
          and "Near-term" in out and "coverage" in out and before == after,
          "load_olivo_cash_policy.py --dry-run prints the derived sleeves, yields and coverage vs minimum and writes "
          "nothing (real + demo fingerprints unchanged)", f"rc={rc} {out[-600:]}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check reports CURRENT", f"{code} {(out + err)[-300:]}")
    self_static(check, find)


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) >= 5 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the cash02 render tests ({NODE_TEST}, run by path: sleeves, editor, questionnaire, approval status, member "
          f"read-only) all pass ({n.group(1) if n else '?'})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_cash01.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_cash01.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_cash01.py: 0 FAIL (its own final TOTAL line and exit code)",
          f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def report_for_calc(conn) -> None:
    """The household's report (for the pure checks' lines), read under the fixture org's RLS context on a pooled
    connection (the context is set before acquire)."""
    from services.database import get_pool

    tokens = set_rls_context(str(ORG_A), False)
    try:
        pool = await get_pool()
        async with pool.acquire() as c:
            S["report"] = await CC.household_report(c, ORG_A, H1, today=TODAY)
    finally:
        reset_rls_context(tokens)


async def print_fixture_counts(conn, when: str) -> int:
    """Every COUNTED table's fixture rows (by fixture ids / reserved prefixes) + every org_id table holding any."""
    c = await counts(conn)
    extra = await org_table_counts()
    for t in sorted(set(c) | set(extra)):
        n = c.get(t, extra.get(t, 0))
        if n or t in c:
            print(f"[INFO] {when}: {t} = {n}")
    return sum(c.values()) + sum(v for t, v in extra.items() if t not in c)


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


FIXTURE_SECTIONS = (("calculation", y_calc), ("yields", y_yields), ("maker-checker", y_maker_checker),
                    ("minimum", y_minimum), ("inbox + bulk", y_inbox), ("visibility + isolation", y_visibility))
#: --only=<names>: short name → FIXTURE_SECTIONS name; a section that reads another's state pulls it in (ordered)
ONLY_NAMES = {"calculation": "calculation", "yields": "yields", "maker-checker": "maker-checker",
              "minimum": "minimum", "inbox": "inbox + bulk", "visibility": "visibility + isolation"}
ONLY_NEEDS = {"visibility + isolation": ("maker-checker",)}    # it approves / reads S["p2"]


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
    pre = None
    only = only_sections()
    if only is not None:
        print(f"[INFO] one-off run: seed + {only or 'no section'} (no Olivo / routes / front end / regression)")
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
        if S.get("seeded"):
            await run("report", report_for_calc, conn)
            for name, fn in FIXTURE_SECTIONS:
                if only is None or name in only:
                    await run(name, fn, conn)
        else:
            for name, _fn in FIXTURE_SECTIONS:
                if only is None or name in only:
                    check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
            await run("olivo", y_olivo, conn)
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        RC.set_clock(None)
        CP.YIELD_PROXIES = REAL_PROXIES
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (policies, decisions, tasks, series, positions, to-dos, …)",
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
    if only is not None:
        print("[SKIP] regression (one-off run)")
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
