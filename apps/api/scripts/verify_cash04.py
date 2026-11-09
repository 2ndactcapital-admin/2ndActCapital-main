"""verify_cash04 — CASH04: from PLAN to ORDERS. Cash proposals (a KIND of rebalance proposal), approval → execution
instructions through Replication Studio's existing path, and the monthly maturity waterfall (a platform job that only
DRAFTS).

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash04.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx transport
refuses and records (asserted empty at the end) and the AI helpers refuse and record.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``), the Auth0 profile call, and
cash03's FOUR security-data READS (``cash_ladder.read_curve`` / ``available_issues`` / ``read_auctions``,
``cash_instruments.read_mmfs`` — both providers ship switched off, so the real reads return "not connected"): they
return the fixture Treasury issues / auction yields / curve / fund, so cash03's REAL ladder and after-tax plan run
unchanged on the REAL policy, obligations and accounts. ``AltruistTradeAdapter.submit`` is wrapped to COUNT calls
(asserted zero) and ``execution.create_instructions`` to count the instruction path. REAL: the app through an ASGI
transport, services.cash_proposals / cash_waterfall / rebalance_proposals / execution / custody.trading / sod /
human_tasks / workflow_autonomy, RLS and the live database. The script's own connection does not bypass RLS
(rolbypassrls = false, asserted). TIME IS INJECTED: "today" is TODAY (2026-11-09) for routers.cash, the builders and the
job.

FIXTURES: orgs 99000000-0000-0000-0000-0000cac40001 (A, VISIBILITY1 enforcement ON) / 0002 (B) / 0003 (a fixture
PLATFORM org for the job — added to security_master_jobs.PLATFORM_JOB_ORG_IDS for the run), is_test = true (asserted).
Users with real user_roles grants (the app's decision on each asserted):
  org A  U_ADV, U_ADV2 advisor (proposals: view / manage / approve; execution; manage_cash_policy) — the maker and the
         checker; U_VIEW compliance (view_portfolio only); U_MEM member (H1); U_RS support_staff (proposals, assigned H2
         ONLY)
  org B  U_B, U_B2 advisor
Households: H1 (ACC_1, a managed taxable trust: a Reserve bill 80,000 par, a fund 20,000, a NON-CASH fund 5,000, cash
300,000), H2 (ACC_HA held away; ACC_ADV with an approved ADVISE authority), H3 (ACC_W: a bill that matured 2026-11-06,
a fund 60,000, cash 10,000, an interest payment 10,000), HB (org B: ACC_B cash 200,000). Obligations through the cash01
API; policies (N 3, R 12, horizon 24, 3 rungs) through the cash02 API, drafted by one person, approved by ANOTHER.
Global fixture securities ('CASH04 VERIFY …', CUSIPs 99C04…, SEC class C99004…) through services.securities_global.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with the execution / models / replica teardown
GUCs, keyed by this run's fixture ids; tables without org_id passed in the plan by their fixture ids. Every touched table
re-read for its exact pre-test count BY FIXTURE IDS ONLY. Real rows (every non-fixture proposal, trade, instruction,
policy, obligation, account, workflow definition / trigger and the real security master — the Olivo demo's included)
are fingerprinted before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<a,b,…>: the one-off path (cash04 fix 1) — teardown, prefix guard, real fingerprints, schema read, seed, then
ONLY the named fixture sections (seed | builder | approval | refusals | waterfall | app | empty; 'app' pulls in 'approval' and
'waterfall', 'waterfall' pulls in 'approval'), then the full teardown + leftover + real-row checks; no regression.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
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
import uuid
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash04"
BEFORE_REF = "af56a5e"   # cash04-work's base: 'merge cash03'
_W = "internal + any(manage_proposals)"
ROUTES = {("POST", "/api/v1/cash/households/{household_id}/proposals"): _W,
          ("POST", "/api/v1/cash/proposals/{proposal_id}/adopt"): _W}
OWN_FILES = ("apps/api/services/cash_proposals.py", "apps/api/services/cash_waterfall.py", "apps/api/routers/cash.py",
             "apps/api/scripts/olivo_cash_proposal.py", "apps/api/scripts/_apply_cash04.py")
NODE_TEST = "tests/cash04/cash04.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH04_NOTES.md"
DOC = REPO / "docs" / "cash" / "CASH_PROPOSALS.md"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
MIGRATION = REPO / "docs" / "cash04_part1.sql"
#: execution_instructions' CHECKs exactly as before cash04 (read live 2026-11-09) — cash04 must not loosen them
EXEC_CHECKS = {
    "execution_instructions_side_chk":
        "CHECK ((side = ANY (ARRAY['buy'::text, 'sell'::text, 'sell_short'::text, 'buy_to_cover'::text])))",
    "execution_instructions_amount_chk":
        "CHECK ((((quantity IS NOT NULL) AND (notional IS NULL) AND (quantity > (0)::numeric)) OR ((quantity IS NULL) "
        "AND (notional IS NOT NULL) AND (notional > (0)::numeric))))",
    "execution_instructions_price_chk":
        "CHECK ((((price_kind = 'market'::text) AND (limit_price IS NULL) AND (range_low IS NULL) AND (range_high IS "
        "NULL)) OR ((price_kind = 'limit'::text) AND (limit_price > (0)::numeric) AND (range_low IS NULL) AND "
        "(range_high IS NULL)) OR ((price_kind = 'range'::text) AND (limit_price IS NULL) AND (range_low > "
        "(0)::numeric) AND (range_high >= range_low) AND (reference_price > (0)::numeric) AND (reference_at IS NOT "
        "NULL))))",
}


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash04's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    check(len(o.before) > 0 and not o.changed and not o.removed,
          "cash04 changed and removed no existing route's gate (the proposal / instruction routes are reused as they are)",
          f"{o.where()}; changed={sorted(flat(o.changed))[:3]} removed={sorted(flat(o.removed))[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash04's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash04's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    cp = _code_only(_src("apps/api/services/cash_proposals.py"))
    cw = _code_only(_src("apps/api/services/cash_waterfall.py"))
    rp = _code_only(_src("apps/api/services/rebalance_proposals.py"))
    raw = _src("apps/api/services/cash_proposals.py")       # the SQL is in triple-quoted f-strings (code, not prose)
    check("RP.account_lock(" in cp and "HA.refuse(" in cp and "RP.open_proposal(" in cp
          and "INSERT INTO {RP.T_P}" in raw and "$12::uuid, 'cash')" in raw and "RP.OpenProposalExists(" in cp,
          "services/cash_proposals.py writes through the rebalance-proposal construct (account lock, the authority "
          "check, one open proposal per account, kind 'cash'; another kind's open proposal is never superseded)")
    check(all(x not in cp for x in ("route_trade_list(", "create_instructions(", "X.submit(")) and "INSERT INTO "
          "public.execution_instructions" not in raw,
          "services/cash_proposals.py never routes or creates an instruction itself (rep5's execute does, unchanged)")
    check("CPR.write_draft(" in cw and all(x not in cw for x in ("RP.submit(", "RP.execute(", "RP.decide(",
                                                                  "route_trade_list(", "create_instructions(",
                                                                  "CPR.adopt(", "CPR.write_proposal(", "X.submit(")),
          "services/cash_waterfall.py's ONLY write is cash_proposals.write_draft — it never submits, approves, adopts "
          "or routes (S29b draft-only)")
    check('leg.get("price_kind") == "limit"' in rp and 'PriceGuidance(kind="limit"' in rp
          and "CASH_ONLY_TRIGGERS" in rp, "rebalance_proposals: a cash leg's limit reaches the trade line (a bill / "
          "note by par with a limit); generate() refuses the cash_waterfall trigger")
    wa = _src("apps/api/services/workflow_autonomy.py")
    check('"cash.maturity_waterfall": {"class": ELIGIBLE, "platform_job": True, "draft_only": True' in wa
          and '"cash.proposal_submit": {"class": NEVER, "trades": True' in wa
          and '"cash.proposal_route": {"class": NEVER, "trades": True' in wa,
          "workflow_autonomy classes the waterfall's write as draft-only and the submit / route verbs as NEVER (trades)")
    check('"cash.maturity_waterfall": "propose"' in _src("apps/api/services/sod.py"),
          "sod.VERB_DUTIES: the waterfall's verb duty is 'propose'")
    check('"cash_maturity_waterfall": JobSpec(' in _src("apps/api/services/security_master_jobs.py")
          and "active=False" in _code_only(_src("apps/api/scripts/_apply_cash04.py")),
          "the waterfall job is a platform JobSpec seeded INACTIVE (scripts/_apply_cash04.py --seed-jobs, active=False)")
    sql = "\n".join(ln.split("--", 1)[0] for ln in (MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists()
                                                       else "").splitlines())
    check("'cash'::text" in sql and "'cash_waterfall'::text" in sql and "cash_leg" in sql
          and "rebalance_proposals_cash_waterfall_uq" in sql and "execution_instructions" not in sql
          and "CREATE TABLE" not in sql,
          "docs/cash04_part1.sql: a 'cash' kind branch, the cash_waterfall trigger, cash_leg, the waterfall unique "
          "index — no new table, execution_instructions untouched")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
        check(sec in notes, f"docs/cash/CASH04_NOTES.md has section {sec}")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    check("verify_cash04.py" in _src("scripts/regression_set.txt"), "verify_cash04.py is in scripts/regression_set.txt")


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
import _db_connect as DBC  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
import routers.cash as RC  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import cash_instruments as CI  # noqa: E402
from services import cash_ladder as CL  # noqa: E402
from services import cash_policy as CP  # noqa: E402
from services import cash_proposals as CPR  # noqa: E402
from services import cash_waterfall as CW  # noqa: E402
from services import execution as X  # noqa: E402
from services import execution_letter as EL  # noqa: E402
from services import execution_routers as XR  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.registry import trade_adapter_for  # noqa: E402
import olivo_cash_proposal as OL  # noqa: E402
import services.database as DBM  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "cash04verify"
NAME = "CASH04 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac4"
TODAY = date(2026, 11, 9)
TEARDOWN_GUCS = ("execution.teardown", "models.teardown", "replica.teardown")
CUSIP_PREFIX = "99C04"
CLASS_PREFIX = "C99004"
CUSTODIAN = "cash04verify"         # no trade adapter is registered for it → the MANUAL adapter (asserted)
D = Decimal
ZERO = D(0)


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B, ORG_P = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_P]
U_ADV, U_ADV2, U_VIEW, U_MEM, U_RS, U_B, U_B2 = (_id(10 + i) for i in range(7))
H1, H2, H3, HB = _id(30), _id(31), _id(32), _id(33)
E_T, E_O, E_W, E_B = _id(40), _id(41), _id(42), _id(43)
ACC_1, ACC_HA, ACC_ADV, ACC_W, ACC_B = _id(70), _id(71), _id(72), _id(73), _id(74)

_PROP = {"view_proposals", "manage_proposals", "approve_proposals"}
PERMS = {
    "advisor": {"view_portfolio", "manage_portfolio", "view_members", "manage_cash_policy", "view_execution",
                "manage_execution", "approve_execution"} | _PROP,
    "compliance": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "support_staff": {"view_portfolio", "manage_portfolio", "view_members", "view_proposals", "manage_proposals"},
}
USERS = {
    U_ADV: (ORG_A, "member", "adv", "advisor"),
    U_ADV2: (ORG_A, "member", "adv2", "advisor"),
    U_VIEW: (ORG_A, "member", "view", "compliance"),
    U_MEM: (ORG_A, "member", "mem", "member"),
    U_RS: (ORG_A, "member", "rs", "support_staff"),
    U_B: (ORG_B, "member", "b", "advisor"),
    U_B2: (ORG_B, "member", "b2", "advisor"),
}
ALL_USERS = list(USERS)
ENTS = {E_T: (ORG_A, "trust", "Dynasty", "Proposal Trust"), E_O: (ORG_A, "individual", None, "Other Person"),
        E_W: (ORG_A, "individual", None, "Waterfall Person"), E_B: (ORG_B, "individual", None, "Org B Person")}
HOUSEHOLDS = {H1: (ORG_A, "Proposal", (E_T,)), H2: (ORG_A, "Refusal", (E_O,)), H3: (ORG_A, "Waterfall", (E_W,)),
              HB: (ORG_B, "B", (E_B,))}
# (id, org, household, entity, registration, tax, masked, held away, cash)
ACCOUNTS = [(ACC_1, ORG_A, H1, E_T, "trust", "taxable", "****4001", False, "300000.00"),
            (ACC_HA, ORG_A, H2, E_O, "individual", "taxable", "****4002", True, "50000.00"),
            (ACC_ADV, ORG_A, H2, E_O, "individual", "taxable", "****4003", False, "50000.00"),
            (ACC_W, ORG_A, H3, E_W, "individual", "taxable", "****4004", False, "10000.00"),
            (ACC_B, ORG_B, HB, E_B, "individual", "taxable", "****4005", False, "200000.00")]
#: fixture securities: key → (name, security_type, id_type, id_value, attrs{field: (value, provider)})
SECS = {
    "MMF": ("Treasury MMF", "fund", "sec_class_id", "C990040001",
            {"mmf_category": ("treasury", "sec_nmfp"), "fund_structure": ("money_market", "sec_nmfp")}),
    "EQF": ("Equity Fund", "fund", "sec_class_id", "C990040009", {}),
    "B_RS": ("Bill 2027-03-01", "fixed_income", "cusip", "99C040AA1",
             {"treasury_type": ("Bill", "treasury_fiscal"), "maturity_date": ("2027-03-01", "treasury_fiscal")}),
    "B_RV": ("Bill 2027-05-06", "fixed_income", "cusip", "99C040AB9",
             {"treasury_type": ("Bill", "treasury_fiscal"), "maturity_date": ("2027-05-06", "treasury_fiscal")}),
    "B_LT1": ("Bill 2027-11-04", "fixed_income", "cusip", "99C040AC7",
              {"treasury_type": ("Bill", "treasury_fiscal"), "maturity_date": ("2027-11-04", "treasury_fiscal")}),
    "N_LT2": ("Note 2028-01-13", "fixed_income", "cusip", "99C040AD5",
              {"treasury_type": ("Note", "treasury_fiscal"), "maturity_date": ("2028-01-13", "treasury_fiscal")}),
    "B_MAT": ("Bill 2026-11-06", "fixed_income", "cusip", "99C040AE3",
              {"treasury_type": ("Bill", "treasury_fiscal"), "maturity_date": ("2026-11-06", "treasury_fiscal")}),
}
ISSUE_KEYS = ("B_RS", "B_RV", "B_LT1", "N_LT2")
AUCTION_DATE = date(2026, 11, 3)
AUCTIONS = {"B_RS": D("4.20"), "B_RV": D("4.10"), "B_LT1": D("4.05"), "N_LT2": D("3.95")}
MMF_YIELD = D("4.00")
CURVE = {"DGS1MO": "4.20", "DGS3MO": "4.15", "DGS6MO": "4.10", "DGS1": "4.00", "DGS2": "3.90", "DGS5": "3.95",
         "DGS7": "4.05", "DGS10": "4.20"}
CURVE_DATE = date(2026, 11, 6)
# (account, security key, quantity, market value) — positions as of TODAY − 1
HOLDINGS = [(ACC_1, "B_RS", "80000", "79200.00"), (ACC_1, "MMF", "20000", "20000.00"), (ACC_1, "EQF", "100", "5000.00"),
            (ACC_W, "B_MAT", "120000", "120000.00"), (ACC_W, "MMF", "60000", "60000.00"),
            (ACC_ADV, "MMF", "10000", "10000.00")]
EARNING = (ACC_W, "MMF", "interest", date(2026, 11, 2), "10000.00")
# (household, payer, source type, name, amount, due, certainty) — recorded through the cash01 API
OBLIGATIONS = [(hh, ent, st, nm, amt, due, cert) for hh, ent in ((H1, E_T), (H3, E_W)) for st, nm, amt, due, cert in (
    ("one_off", "near item", "100000.00", "2026-12-01", "firm"),
    ("tax_estimate", "January estimated tax", "400000.00", "2028-01-14", "firm"),
    ("one_off", "Club dues", "40000.00", "2027-12-15", "estimated"),
    ("one_off", "Pledge", "30000.00", "2028-06-01", "estimated"))] + [
    (H1, E_T, "one_off", "reserve item", "50000.00", "2027-03-15", "firm"),
    (HB, E_B, "one_off", "B near item", "50000.00", "2026-12-01", "firm")]
POLICY = {"scope_type": "household", "preset": "custom", "near_months": 3, "reserve_months": 12, "horizon_months": 24,
          "rung_count": 3, "buffer_months": "0", "objective_score": 3, "min_coverage_months": "6"}
POLICY_PEOPLE = {H1: (U_ADV, U_ADV2), H3: (U_ADV, U_ADV2), HB: (U_B, U_B2)}

COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.transactions", "public.accounts", "public.account_balances_daily", "public.account_authority_settings",
    "public.cash_obligations", "public.cash_policies", "public.rebalance_proposals", "public.rebalance_proposal_trades",
    "public.execution_instructions", "public.execution_instruction_events", "public.workflow_runs",
    "public.workflow_run_steps", "public.sod_decisions", "public.audit_log", "public.member_todos",
    "public.domain_events", "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.security_reference_attributes",
)
SEED_CHECK_LITERALS = (
    ("public.rebalance_proposals", "rebalance_proposals_kind_chk", ("cash", "cash_policy_id", "cash_waterfall",
                                                                    "period")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("manual", "cash_waterfall")),
    ("public.rebalance_proposals", "rebalance_proposals_status_chk", ("draft", "pending_approval", "approved",
                                                                      "superseded")),
    ("public.rebalance_proposals", "rebalance_proposals_maker_kind_chk", ("user", "system")),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_cash_leg_chk", ("near_term", "reserve",
                                                                                    "long_term", "market", "limit")),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_price_chk", ("security_price", "account_position",
                                                                                 "none")),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_side_chk", ("buy", "sell")),
    ("public.execution_instructions", "execution_instructions_side_chk", ("buy", "sell")),
    ("public.execution_instructions", "execution_instructions_price_chk", ("market", "limit")),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("custodian_desk",)),
    ("public.cash_obligations", "cash_obligations_source_type_chk", ("one_off", "tax_estimate")),
    ("public.cash_obligations", "cash_obligations_certainty_chk", ("firm", "estimated")),
    ("public.cash_policies", "cash_policies_preset_chk", ("custom",)),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.transactions", "transactions_authority_chk", ("manual",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("fixed_income", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "sec_class_id")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_field_chk",
     ("maturity_date", "treasury_type", "mmf_category", "fund_structure")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_treasury_chk", ("Bill", "Note")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_mmf_chk", ("treasury",)),
    ("public.delegate_grants", "delegate_grants_scope_check", ("view_only",)),
    ("public.account_authority_settings", "account_authority_settings_status_chk", ("approved",)),
)
_O, _A, _U, _P = (str(ORG_A), str(ACC_1), str(U_ADV), "99000000-0000-0000-0000-0000cac40999")
#: every row SHAPE seeded by raw SQL or written by a service on the verify's behalf — evaluated against EVERY CHECK on
#: its table IN THE DATABASE before seeding (jsonb_populate_record → the constraint's own expression)
SEED_ROWS = {
    "public.rebalance_proposals": [
        {"id": _P, "org_id": _O, "account_id": _A, "trigger": "manual", "trigger_ref": {"cash_policy_id": _P},
         "status": "draft", "advice_only": False, "account_value": "0", "cash_value": "0", "target_weights": [],
         "before_weights": [], "after_weights": [], "warnings": [], "sell_later": [], "execution": {},
         "expires_at": "2026-11-14T00:00:00+00:00", "maker_kind": "user", "maker_id": _U, "proposal_kind": "cash",
         "est_st_gain": "0", "est_lt_gain": "0", "gain_realized_ytd": "0"},
        {"id": _P, "org_id": _O, "account_id": _A, "trigger": "cash_waterfall",
         "trigger_ref": {"cash_policy_id": _P, "period": "2026-11"}, "status": "draft", "advice_only": False,
         "account_value": "0", "cash_value": "0", "target_weights": [], "before_weights": [], "after_weights": [],
         "warnings": [], "sell_later": [], "execution": {}, "expires_at": "2026-11-14T00:00:00+00:00",
         "maker_kind": "system", "proposal_kind": "cash", "est_st_gain": "0", "est_lt_gain": "0",
         "gain_realized_ytd": "0"}],
    "public.rebalance_proposal_trades": [
        {"org_id": _O, "proposal_id": _P, "line_no": 1, "global_security_id": _P, "side": "buy", "quantity": "40000",
         "price": "0.96158900", "price_source": "security_price", "price_as_of": "2026-11-03", "lots": [],
         "reason": "rung", "cash_leg": {"sleeve": "long_term", "price_kind": "limit", "limit_price": "96.1589"},
         "est_st_gain": "0", "est_lt_gain": "0"},
        {"org_id": _O, "proposal_id": _P, "line_no": 2, "global_security_id": _P, "side": "buy", "notional": "80000.00",
         "price_source": "none", "lots": [], "reason": "near", "cash_leg": {"sleeve": "near_term", "price_kind": "market",
                                                                             "limit_price": None},
         "est_st_gain": "0", "est_lt_gain": "0"},
        {"org_id": _O, "proposal_id": _P, "line_no": 3, "global_security_id": _P, "side": "sell", "quantity": "30000",
         "price": "0.99", "price_source": "account_position", "price_as_of": "2026-11-08", "lots": [],
         "reason": "withdrawal", "cash_leg": {"sleeve": "reserve", "price_kind": "limit", "limit_price": "99.0000"},
         "est_st_gain": "0", "est_lt_gain": "0"}],
    "portfolio.positions": [{"org_id": _O, "owner_entity_id": _O, "asset_id": _O, "as_of_date": "2026-11-08",
                             "quantity": "80000", "market_value": "79200.00", "authority": "manual",
                             "source_system": "manual", "account_id": _A}],
    "portfolio.transactions": [{"org_id": _O, "position_id": _O, "transaction_type_code": "interest",
                                "trade_date": "2026-11-02", "net_amount": "10000.00", "authority": "manual",
                                "source_system": "manual"}],
    "public.account_authority_settings": [{"org_id": _O, "account_id": _A, "authority_override": "advise",
                                           "custody": "integrated", "effective_from": "2026-11-08",
                                           "reason": "verify", "status": "approved", "maker_id": _U,
                                           "approved_by": "99000000-0000-0000-0000-0000cac40011",
                                           "decided_at": "2026-11-08T00:00:00+00:00"}],
    "portfolio.security_reference_attributes": [
        {"global_security_id": _O, "field": "maturity_date", "value": "2027-11-04", "provider_key": "treasury_fiscal",
         "as_of": "2026-11-09"},
        {"global_security_id": _O, "field": "treasury_type", "value": "Note", "provider_key": "treasury_fiscal",
         "as_of": "2026-11-09"},
        {"global_security_id": _O, "field": "mmf_category", "value": "treasury", "provider_key": "sec_nmfp",
         "as_of": "2026-11-09"}],
}
#: literals in seed() that are not CHECK-governed values: GUC names, booleans, intervals, labels and descriptions
SEED_NON_COLUMN = {"app.is_super_admin", "true", "1 day", "app.current_org_id", "active", "staff_visibility.enforced",
                   "general", "Lead adviser", "CASH04 verify — advise fixture"}
UNIQUES = ("rebalance_proposals_open_uq", "rebalance_proposals_task_uq", "rebalance_proposals_cash_waterfall_uq",
           "rebalance_proposal_trades_line_uq", "execution_instructions_task_live_uq", "accounts_active_identity_uq",
           "assets_cash_active_uniq", "cash_policies_active_household_uq", "cash_policies_open_household_uq",
           "account_authority_settings_live_uq", "uq_sec_global_ident_issuer_assigned", "uq_sec_global_ident_sec_class",
           "security_reference_attributes_live_uq", "org_settings_org_id_setting_key_key")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "sub_type"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "delegate_grants": ("org_id", "principal_entity_id", "delegate_user_id", "scope", "effective_from"),
    "staff_assignments": ("org_id", "entity_id", "assigned_to_user_id", "role_label"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category"),
    "portfolio.assets": ("id", "org_id", "global_security_id", "name", "asset_type"),
    "portfolio.positions": ("id", "org_id", "owner_entity_id", "asset_id", "as_of_date", "quantity", "market_value",
                            "authority", "source_system", "account_id"),
    "portfolio.transactions": ("org_id", "position_id", "transaction_type_code", "trade_date", "gross_amount",
                               "net_amount", "authority", "source_system"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id", "is_discretionary", "is_held_away"),
    "account_balances_daily": ("org_id", "account_id", "as_of_date", "total_market_value", "cash_value",
                               "source_system"),
    "account_authority_settings": ("org_id", "account_id", "authority_override", "custody", "effective_from", "reason",
                                   "status", "maker_id", "approved_by", "decided_at"),
    "portfolio.security_reference_attributes": ("global_security_id", "field", "value", "provider_key", "as_of"),
    # written on the verify's behalf by services.cash_proposals.write_proposal (the rebalance-proposal construct)
    "rebalance_proposals": ("id", "org_id", "account_id", "link_id", "model_id", "model_version_id", "trigger",
                            "trigger_ref", "status", "advice_only", "valuation_as_of", "account_value", "cash_value",
                            "warnings", "expires_at", "maker_kind", "maker_id", "proposal_kind"),
    "rebalance_proposal_trades": ("org_id", "proposal_id", "line_no", "global_security_id", "side", "quantity",
                                  "notional", "price", "price_source", "price_as_of", "reason", "cash_leg"),
    "audit_log": ("org_id", "user_id", "action", "resource_type", "resource_id", "payload"),
}

S: dict = {"sec": {}, "pos": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
ALTRUIST_CALLS: list[str] = []
CREATE_CALLS: list[dict] = []
ORIG: dict = {}
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


def sec(key) -> str:
    return str(S["sec"][key])


# ── the verify's OWN hand formulas (independent of services.cash_proposals) ──────────────────────────────────────
def bill_limit(y: Decimal, maturity: date) -> Decimal:
    days = D((maturity - TODAY).days)
    return (D(100) / (D(1) + y / D(100) * days / D(365))).quantize(D("0.0001"), rounding=ROUND_DOWN)


def floor100(x: Decimal) -> Decimal:
    return (x / D(100)).to_integral_value(rounding=ROUND_DOWN) * D(100)


def cents(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


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


async def in_org(conn, org, fn):
    """Run ``fn(conn)`` (reads only) on THIS connection with the org's RLS context set inside its transaction."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await fn(conn)


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
    p, DBM._pool = DBM._pool, None
    await close_conn(p)


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


SECS_SQL = ("SELECT id FROM portfolio.securities_global WHERE name LIKE 'CASH04 VERIFY %' UNION SELECT "
            "global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE '99C04%' OR "
            "id_value LIKE 'C99004%'")


def fixture_scope(table: str) -> str:
    orgs, users, hhs = _arr(FIXTURE_ORGS), _arr(ALL_USERS), _arr(HOUSEHOLDS)
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({hhs})",
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "portfolio.securities_global": f"id IN ({SECS_SQL})",
        "portfolio.securities_global_identifiers": f"global_security_id IN ({SECS_SQL})",
        "portfolio.security_reference_attributes": f"global_security_id IN ({SECS_SQL})",
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
    nf = "NOT (org_id = ANY($1::uuid[]))"
    return {
        "rebalance_proposals (every real proposal, the demo's included)": await fingerprint(
            conn, f"SELECT id, status, proposal_kind, system_to FROM public.rebalance_proposals WHERE {nf}", orgs),
        "rebalance_proposal_trades (real)": await fingerprint(
            conn, f"SELECT id, proposal_id, valid_to, system_to FROM public.rebalance_proposal_trades WHERE {nf}", orgs),
        "execution_instructions (real)": await fingerprint(
            conn, f"SELECT id, status, routed_via, system_to FROM public.execution_instructions WHERE {nf}", orgs),
        "cash_policies (real, the Olivo demo's included)": await fingerprint(
            conn, f"SELECT * FROM public.cash_policies WHERE {nf}", orgs),
        "cash_obligations (real)": await fingerprint(conn, f"SELECT * FROM public.cash_obligations WHERE {nf}", orgs),
        "accounts (real)": await fingerprint(
            conn, f"SELECT id, tax_status, valid_to, system_to FROM public.accounts WHERE {nf}", orgs),
        "workflow definitions + triggers (real — the waterfall job is NOT switched on)": await fingerprint(
            conn, "SELECT d.id, d.name, t.id AS tid, t.is_active FROM public.workflow_definitions d LEFT JOIN "
                  "public.workflow_triggers t ON t.workflow_definition_id = d.id WHERE NOT (d.org_id = ANY($1::uuid[]))",
            orgs),
        "the real security master (id, type, archive state)": await fingerprint(
            conn, f"SELECT id, security_type, valid_to, system_to FROM portfolio.securities_global WHERE NOT "
                  f"({fixture_scope('portfolio.securities_global')})"),
    }


async def fixture_security_ids(op) -> list[UUID]:
    return [r["id"] for r in await op.fetch(SECS_SQL)]


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs = sorted(set(await fixture_security_ids(op)), key=str)
        users, orgs, hhs = list(ALL_USERS), list(FIXTURE_ORGS), list(HOUSEHOLDS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [hhs]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            # tables without org_id, by their materialised fixture ids (asserted to match no real row)
            "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cash04")
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


async def leftovers(conn) -> int:
    n = 0
    for t in ("public.user_roles", "public.household_memberships", "public.audit_log", "public.member_todos",
              "portfolio.securities_global", "portfolio.securities_global_identifiers",
              "portfolio.security_reference_attributes"):
        n += int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                            ALL_USERS, f"%{TAG}%", fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                            fetch="fetchval"))
    n += sum((await org_table_counts()).values())
    return n


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash04"}}
    if body is not None:
        kw["json"] = body
    # cash04 fix 1: raise_app_exceptions=True so a 500 PRINTS the server's traceback (it used to be swallowed into
    # {"raw": "Internal Server Error"}); the call still answers 500 with the exception named — never a pass
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
        except Exception as exc:  # noqa: BLE001 — the app raised: a 500, with its traceback
            print(f"[INFO] {method} {path} raised in the app (a 500):")
            traceback.print_exc()
            return 500, {"raw": "Internal Server Error", "exc": f"{type(exc).__name__}: {exc}"[:2000]}
    raw = res.content.decode("utf-8", "replace")
    try:
        return res.status_code, json.loads(raw or "{}")
    except ValueError:
        return res.status_code, {"raw": raw[:4000]}


# ── the four cash03 security-data reads, replaced by the fixture (each at its CURRENT signature) ─────────────────
async def fx_read_curve(conn, org_id, today, *, purpose="internal", points=None, providers=None):
    return [{"key": k, "months": m, "yield_pct": D(CURVE[k]), "as_of": CURVE_DATE, "provider": "fred"}
            for k, m in CL.CURVE_POINTS], []


async def fx_available_issues(conn, org_id, today, until, *, purpose="internal", cusip_like=None):
    out = []
    for k in ISSUE_KEYS:
        name, _t, _it, cusip, attrs = SECS[k]
        m = date.fromisoformat(attrs["maturity_date"][0])
        if today < m <= until:
            out.append({"security_id": sec(k), "name": NAME + name, "cusip": cusip,
                        "treasury_type": attrs["treasury_type"][0], "maturity": m})
    return out, None


async def fx_read_auctions(conn, org_id, cusips, today, *, purpose="internal", providers=None):
    by_cusip = {SECS[k][3]: AUCTIONS[k] for k in AUCTIONS}
    return {c: ({"found": True, "yield_pct": by_cusip[c], "as_of": AUCTION_DATE, "provider": "treasury_fiscal"}
                if c in by_cusip else {"found": False, "missing": "not in the fixture"}) for c in cusips}


async def fx_read_mmfs(conn, org_id, today, *, purpose="internal", class_like=None, providers=None):
    return [{"security_id": sec("MMF"), "name": NAME + SECS["MMF"][0], "class_id": SECS["MMF"][3],
             "category": "treasury", "yield": {"estimated": True, "yield_pct": MMF_YIELD, "as_of": date(2026, 10, 31),
                                               "source": "nmfp", "basis": "7-day net yield (SEC Form N-MFP)"}}]


READ_STUBS = ((CL, "read_curve", fx_read_curve), (CL, "available_issues", fx_available_issues),
              (CL, "read_auctions", fx_read_auctions), (CI, "read_mmfs", fx_read_mmfs))


def install_reads(on: bool) -> None:
    for mod, name, fn in READ_STUBS:
        setattr(mod, name, fn if on else ORIG[(mod.__name__, name)])


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
        raise AssertionError(f"verify_cash04: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash04: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    # a stub must match the CURRENT signature of what it replaces (names, kinds, defaults' keys)
    for mod, name, fn in READ_STUBS:
        real = getattr(mod, name)
        ORIG[(mod.__name__, name)] = real
        a, b = inspect.signature(real).parameters, inspect.signature(fn).parameters
        if [(p.name, p.kind) for p in a.values()] != [(p.name, p.kind) for p in b.values()]:
            raise RuntimeError(f"{mod.__name__}.{name} signature drifted: {list(a)} vs the stub {list(b)}")
    install_reads(True)
    have = list(inspect.signature(RC.set_clock).parameters)
    if have != ["fn"]:
        raise RuntimeError(f"routers.cash.set_clock signature drifted: {have}")
    RC.set_clock(lambda: TODAY)
    # count (never change) the instruction path and any Altruist routing
    orig_alt = TR.AltruistTradeAdapter.submit

    async def counted_alt(self, ctx, trade_list, *, reason=None, notes=None):
        ALTRUIST_CALLS.append(str(trade_list.account_id))
        return await orig_alt(self, ctx, trade_list, reason=reason, notes=notes)

    TR.AltruistTradeAdapter.submit = counted_alt
    orig_ci = X.create_instructions
    want = ["pool", "org_id", "actor_id", "account", "trade_list", "routed_via", "routing_reason", "upstream"]
    if list(inspect.signature(orig_ci).parameters) != want:
        raise RuntimeError(f"execution.create_instructions signature drifted: "
                           f"{list(inspect.signature(orig_ci).parameters)}")

    async def counted_ci(pool, *, org_id, actor_id, account, trade_list, routed_via, routing_reason, upstream=None):
        CREATE_CALLS.append({"account": str(trade_list.account_id), "routed_via": routed_via,
                             "trades": len(trade_list.trades), "upstream": bool(upstream)})
        return await orig_ci(pool, org_id=org_id, actor_id=actor_id, account=account, trade_list=trade_list,
                             routed_via=routed_via, routing_reason=routing_reason, upstream=upstream)

    X.create_instructions = counted_ci
    if list(inspect.signature(CW.run_job).parameters) != ["pool", "org_id", "user_id", "run_context",
                                                          "workflow_run_id"]:
        raise RuntimeError(f"cash_waterfall.run_job signature drifted: {list(inspect.signature(CW.run_job).parameters)}")
    register_all()


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes, the reuse inventory, earlier cash decisions honoured")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec_ in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
        check(sec_ in notes, f"docs/cash/CASH04_NOTES.md has section {sec_}")
    for marker in ("rebalance_proposal_approval", "sod.REBALANCE_PROPOSAL", "create_instructions", "route_trade_list",
                   "execution_routers", "route_refusal", "proposal_kind = 'cash'", "cash_leg", "par", "limit",
                   "security_master_jobs", "ensure_platform_jobs", "VERB_AUTONOMY", "draft_only", "Reuse inventory",
                   "trading_authority", "withdrawal order", "cash_policy.windows", "cash03 D3"):
        check(marker.lower() in notes.lower(), f"the notes name {marker!r}")
    check("[FIND] The prompt names the wrong router registry" in notes and "[FIND] SOC5 trading authority" in notes,
          "the notes record the two prompt corrections as [FIND]s (FIX block router; SOC5 not on the trade path)")
    find("execution_routers.registry() is rep9's FIX block router (manual / fix), not a per-custodian Manual / Altruist "
         "registry — the per-custodian choice is custody.trading.route_trade_list (asserted below)")
    find("SOC5 trading authority (services/trading_authority.py) gates assistant money movement only; the trade path's "
         "authority check is rep7b's authority.refusal — that is what refuses below")
    if not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md is still not in the repo — nothing was invented from it")
    w = {x["key"]: (x["from"], x["until"]) for x in CP.windows(3, 12, 24, TODAY)}
    check(CPR.sleeve_windows({"near_months": 3, "reserve_months": 12, "horizon_months": 24}, TODAY) == w,
          "a held bill's sleeve comes from cash02's windows() (one formula)", f"{w}")
    check(DOC.exists() and "## Builder rules" in DOC.read_text(encoding="utf-8"),
          "docs/cash/CASH_PROPOSALS.md documents the builder rules, withdrawal order, approval path and waterfall")


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
    section("[Y] Schema: CHECK / UNIQUE / FK constraints and indexes read BEFORE seeding; CHECKs unchanged")
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
          f"{got} CHECK constraints read; every literal the seeds, services and bodies use is one the database allows "
          "(docs/cash04_part1.sql applied)", f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(n_chk > 0 and not refused, f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} seeded / service-written tables "
          "evaluated by the database against the exact row shapes (a person's cash proposal, the waterfall's draft, "
          "par + limit / notional + market / sale lines) — none refuses", f"{refused}")
    keep = SEED_ROWS["public.rebalance_proposals"]
    SEED_ROWS["public.rebalance_proposals"] = [dict(keep[0], trigger_ref={}),
                                               dict(keep[1], maker_kind="user", maker_id=_U)]
    keep_t = SEED_ROWS["public.rebalance_proposal_trades"]
    SEED_ROWS["public.rebalance_proposal_trades"] = [dict(keep_t[0], cash_leg={"sleeve": "long_term",
                                                                               "price_kind": "limit"})]
    try:
        _n, bad2 = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["public.rebalance_proposals"] = keep
        SEED_ROWS["public.rebalance_proposal_trades"] = keep_t
    want = {("public.rebalance_proposals", "rebalance_proposals_kind_chk", 0),
            ("public.rebalance_proposals", "rebalance_proposals_kind_chk", 1),
            ("public.rebalance_proposal_trades", "rebalance_proposal_trades_cash_leg_chk", 0)}
    check(want <= set(bad2), "the pre-check is not vacuous: a cash proposal without its policy id, a PERSON-made "
          "waterfall draft and a limit line with no limit price are each refused by the same evaluation", f"{bad2}")
    exec_now = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = ANY($1::text[])",
        list(EXEC_CHECKS))}
    check(exec_now == EXEC_CHECKS, "execution_instructions' side / amount / price CHECKs are EXACTLY as before cash04 "
          "(never loosened)", f"{[k for k in EXEC_CHECKS if exec_now.get(k) != EXEC_CHECKS[k]]}")
    lits = seed_literals()
    vals = {str(v) for rows in SEED_ROWS.values() for r in rows for v in r.values() if v is not None}
    vals |= {x for _t, _n2, ls in SEED_CHECK_LITERALS for x in ls} | {CUSTODIAN, TAG}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(lits) and not undeclared, f"every quoted literal in seed()'s source ({len(lits)}) is a declared row value "
          "or a checked CHECK literal", f"{undeclared}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(len(idx) == len(UNIQUES) and len(idx) > 0 and all("UNIQUE" in v for v in idx.values()),
          f"{len(idx)} unique indexes read (one OPEN proposal per account, one live waterfall proposal per (account, "
          "period), one line number per proposal, one active / open policy per household, one live authority, …)",
          f"missing={sorted(set(UNIQUES) - set(idx))}")
    S["open_uq"] = idx.get("rebalance_proposals_open_uq", "")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY(ARRAY["
              "'public.rebalance_proposals', 'public.rebalance_proposal_trades', 'public.execution_instructions', "
              "'portfolio.positions']::regclass[])")]
    check(len(fks) >= 10, "the written tables' foreign keys are read (proposal → account / task; trade → security / "
          "proposal / instruction; instruction → account / security; position → asset)", f"{len(fks)} {fks}")
    ops = {t: {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                      "tablename = $1", t)}
           for t in ("rebalance_proposals", "rebalance_proposal_trades", "execution_instructions")}
    check(all(v & {"ALL", "SELECT"} and (v & {"ALL", "UPDATE"}) and (v & {"ALL", "INSERT"}) for v in ops.values()),
          "the proposal / trade / instruction tables carry RLS policies for read, insert and update", f"{ops}")
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
    check(len(SEED_COLUMNS) > 0 and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables, the services' own "
          "writes included — rebalance_proposal_trades.cash_leg too) exist in docs/schema_snapshot.sql", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and not refused and len(idx) == len(UNIQUES)
                          and by is False and not badc and exec_now == EXEC_CHECKS)


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    g = int(await platform(conn, f"SELECT count(*) FROM ({SECS_SQL}) x", fetch="fetchval"))
    a = int(await platform(conn, "SELECT count(*) FROM accounts WHERE custodian_code = $1", CUSTODIAN,
                           fetch="fetchval"))
    adapter = trade_adapter_for(CUSTODIAN)
    return check(o == 0 and u == 0 and e == 0 and g == 0 and a == 0 and adapter is None,
                 "the reserved prefixes (fixture ids, sign-in tag, 'CASH04 VERIFY' names, CUSIPs 99C04…, SEC classes "
                 "C99004…, custodian code cash04verify) match no real row; no trade adapter is registered for the "
                 "fixture custodian (→ the MANUAL adapter)", f"orgs={o} users={u} entities={e} secs={g} accounts={a} "
                                                               f"adapter={adapter}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_P, "p")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH04 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in (ORG_A, ORG_B):
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH04 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH04 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, (org, etype, sub, nm) in ENTS.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, sub_type) VALUES "
                               "($1, $2, $3, 'active', $4::entity_type, $5)", eid, org, NAME + nm, etype, sub)
        for hh, (org, nm, members) in HOUSEHOLDS.items():
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + nm + " family")
            for e in members:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day')",
                           ORG_A, E_T, U_MEM)
        for uid, ents in ((U_ADV, (E_T, E_O, E_W)), (U_ADV2, (E_T, E_O, E_W)), (U_VIEW, (E_T,)), (U_RS, (E_O,))):
            for e in ents:
                await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                                   "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, e, uid)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'staff_visibility.enforced', 'true'::jsonb, 'general')", ORG_A)
        for key, (nm, stype, id_type, id_value, attrs) in SECS.items():
            sid = await SG.create_security(conn, name=NAME + nm, security_type=stype, currency_code="USD",
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type=id_type, id_value=id_value, is_primary=True,
                                    is_super_admin=True)
            for field, (value, prov) in attrs.items():
                await conn.execute(
                    "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, "
                    "provider_key, as_of) VALUES ($1, $2, $3, $4, $5)", UUID(str(sid)), field, value, prov, TODAY)
            S["sec"][key] = UUID(str(sid))
        for aid, org, hh, ent, reg, tax, masked, held, cash in ACCOUNTS:
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10)", aid, org, masked, f"{TAG}-{masked[-4:]}",
                CUSTODIAN, reg, tax, ent, hh, held)
            await conn.execute(
                "INSERT INTO account_balances_daily (org_id, account_id, as_of_date, total_market_value, cash_value, "
                "source_system) VALUES ($1, $2, $3, $4, $4, 'manual')", org, aid, TODAY - timedelta(days=1), D(cash))
        await conn.execute(
            "INSERT INTO account_authority_settings (org_id, account_id, authority_override, custody, effective_from, "
            "reason, status, maker_id, approved_by, decided_at) VALUES ($1, $2, 'advise', 'integrated', $3, "
            "'CASH04 verify — advise fixture', 'approved', $4, $5, now())", ORG_A, ACC_ADV, TODAY - timedelta(days=1),
            U_ADV, U_ADV2)
        org_of = {a[0]: (a[1], a[3]) for a in ACCOUNTS}
        for acc, key, qty, mv in HOLDINGS:
            org, ent = org_of[acc]
            aid, pid = uuid.uuid4(), uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, $5)", aid, org, S["sec"][key], NAME + SECS[key][0],
                               SECS[key][1])
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, org, ent, aid, TODAY - timedelta(days=1), D(qty), D(mv), acc)
            S["pos"][(acc, key)] = pid
        acc, key, code, day, amt = EARNING
        await conn.execute(
            "INSERT INTO portfolio.transactions (org_id, position_id, transaction_type_code, trade_date, gross_amount, "
            "net_amount, authority, source_system) VALUES ($1, $2, $3, $4, $5, $5, 'manual', 'manual')",
            ORG_A, S["pos"][(acc, key)], code, day, D(amt))
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true (privileged "
          "insert path)", f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/{len(FIXTURE_ORGS)} orgs")
    ok_roles = await check_fixture_roles(conn)
    codes = []
    for hh, payer, st, nm, amt, due, cert in OBLIGATIONS:
        maker = U_B if hh == HB else U_ADV
        code, _b = await api(maker, "POST", f"/api/v1/cash/households/{hh}/obligations",
                             {"entity_id": str(payer), "source_type": st, "name": NAME + nm, "amount": amt,
                              "due_date": due, "certainty": cert})
        codes.append(code)
    ok_obl = check(codes == [201] * len(OBLIGATIONS), "the fixture schedules are recorded through the cash01 API",
                   f"{codes}")
    ok_pol = True
    S["policy"] = {}
    for hh, (maker, checker) in POLICY_PEOPLE.items():
        c1, b1 = await api(maker, "POST", f"/api/v1/cash/households/{hh}/policies", dict(POLICY))
        pid = (b1.get("policy") or {}).get("id")
        c2, _b2 = await api(maker, "POST", f"/api/v1/cash/households/{hh}/policies/{pid}/submit")
        c3, b3 = await api(checker, "POST", f"/api/v1/cash/households/{hh}/policies/{pid}/approve", {"note": "verify"})
        S["policy"][hh] = pid
        ok_pol = check(c1 == 201 and c2 == 200 and c3 == 200 and (b3.get("policy") or {}).get("status") == "active",
                       f"the {HOUSEHOLDS[hh][1]} cash policy (N 3, R 12, horizon 24, 3 rungs) is drafted by one person "
                       "and approved by ANOTHER through the cash02 API", f"{c1} {c2} {c3} {dumps(b3)[:400]}") and ok_pol
    S["seeded"] = bool(ok_roles and ok_obl and ok_pol and len(S["sec"]) == len(SECS))


async def check_fixture_roles(conn) -> bool:
    bad = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, _acct, short, rname) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            pr = await AZ.load_principal(conn, uid)
            perms = set(getattr(pr, "permissions", ()) or ())
            ok = rname in names and pr is not None and pr.admitted and not pr.is_super_admin and not pr.is_staff
            ok = ok and PERMS[rname] <= perms
            ok = ok and bool(pr.is_internal) is (rname != "member")
            ok = ok and (("approve_proposals" in perms) is (rname == "advisor"))
            ok = ok and (("manage_proposals" in perms) is (rname in ("advisor", "support_staff")))
            if not ok:
                bad.append((short, names, sorted(perms & set().union(*PERMS.values())), bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisors view / manage / "
                 "approve proposals; support staff view / manage only; compliance and members none; no staff bypass)",
                 f"{bad}")


# ── Builder (pure, hand-computed) ─────────────────────────────────────────────────────────────────────────────
def _w():
    return {x["key"]: (x["from"], x["until"]) for x in CP.windows(3, 12, 24, TODAY)}


def _h(sid, label, kind, qty, price, mv, maturity=None):
    return {"security_id": sid, "label": label, "cash_kind": kind, "quantity": D(qty), "price": D(price),
            "price_as_of": TODAY - timedelta(days=1), "price_source": "account_position", "market_value": D(mv),
            "maturity": maturity}


def _shape(lines):
    return [(ln["side"], ln["security_id"], ln["sleeve"], ln["quantity"], ln["notional"], ln["price_kind"],
             ln["limit_price"]) for ln in lines]


async def y_builder_pure(conn):
    section("[Y] Builder (pure, hand-computed): withdrawal order, early-sale warnings, shortfall, non-cash never sold")
    # case 2 — every sleeve over target: Near-term bill first, then Reserve (the fund first), then Long-term (closest)
    hold = [_h("NB", "near bill", "bill", "150000", "0.995", "149250", date(2026, 12, 15)),
            _h("MM", "fund", "mmf", "200000", "1", "200000"),
            _h("RB", "reserve bill", "bill", "40000", "0.99", "39600", date(2027, 3, 1)),
            _h("LB1", "long bill A", "bill", "200000", "0.97", "194000", date(2027, 12, 1)),
            _h("LB2", "long note B", "note", "300000", "0.96", "288000", date(2028, 1, 13))]
    r = CPR.build_account(account={"id": "case2"}, holdings=hold, cash=ZERO,
                          targets={"near_term": D("100000"), "reserve": D("50000"), "long_term": D("400000")},
                          choices={"near_term": None, "reserve": None, "long_term": None}, rungs=[], windows=_w(),
                          today=TODAY)
    want = [("sell", "NB", "near_term", D("50000"), None, "limit", D("99.5000")),
            ("sell", "MM", "reserve", D("190000.000"), None, "market", None),
            ("sell", "LB1", "long_term", D("100000"), None, "limit", D("97.0000"))]
    check(_shape(r["lines"]) == want, "over target everywhere → sells in WITHDRAWAL ORDER: the Near-term bill (50,000 par "
          "at 99.5000), the Reserve fund FIRST (190,000 shares — the bill is not touched), the Long-term rung CLOSEST to "
          "maturity (100,000 par of 2027-12-01 at 97.0000); no buy", f"{_shape(r['lines'])}")
    warn = [w["message"] for w in r["warnings"] if w["code"] == "early_sale"]
    check(len(warn) == 2 and all("may lose principal" in w for w in warn)
          and [ln["warning"] is not None for ln in r["lines"]] == [True, False, True],
          "each early sale of a bill / note carries 'may lose principal'; the fund's sale does not", f"{warn}")
    check(r["sells_total"] == D("49750.00") + D("190000.00") + D("97000.00") and r["buys_total"] == ZERO,
          "sale proceeds are par × the account's own price (49,750 + 190,000 + 97,000)", f"{r['sells_total']}")
    # case 3 — not enough cash: buy what fits, the rest is the shortfall; a non-cash holding is never sold
    mmf = {"kind": "treasury_mmf", "security_id": "MMF", "name": "fund", "net": {"estimated": True,
                                                                                 "yield_pct": D("4.00")},
           "after_tax": {"estimated": False, "text": "after-tax: needs a tax profile"}}
    r3 = CPR.build_account(account={"id": "case3"}, holdings=[_h("EQ", "equity", None, "1000", "500", "500000")],
                           cash=D("30000"), targets={"near_term": D("100000"), "reserve": ZERO, "long_term": ZERO},
                           choices={"near_term": mmf, "reserve": None, "long_term": None}, rungs=[], windows=_w(),
                           today=TODAY)
    check(_shape(r3["lines"]) == [("buy", "MMF", "near_term", None, D("30000.00"), "market", None)]
          and r3["shortfall"] == D("70000.00") and r3["cash_after"] == ZERO,
          "cash 30,000 against a Near-term need of 100,000 → buy 30,000 by notional at market; SHORTFALL 70,000 — never "
          "borrowed (cash after = 0)", f"{_shape(r3['lines'])} short {r3['shortfall']}")
    check(not any(ln["side"] == "sell" for ln in r3["lines"]) and [u["security_id"] for u in r3["untouched"]] == ["EQ"],
          "the 500,000 non-cash holding is listed untouched and never sold, even with a shortfall",
          f"{r3['untouched']}")
    check(any(w["code"] == "cash_shortfall" and "never borrowed" in w["message"] for w in r3["warnings"]),
          "the shortfall is a warning on the proposal", f"{r3['warnings']}")
    allv = [v for ln in r["lines"] + r3["lines"] for v in (ln["quantity"], ln["notional"], ln["limit_price"],
                                                            ln["amount"]) if v is not None]
    check(bool(allv) and all(isinstance(v, Decimal) for v in allv),
          f"every quantity / notional / limit / amount is a Decimal ({len(allv)} values)")


# ── Builder (DB: the REAL plan for H1) ────────────────────────────────────────────────────────────────────────
def expected_h1() -> dict:
    """The verify's own hand computation of ACC_1's proposal (see the module docstring)."""
    l1 = bill_limit(AUCTIONS["B_LT1"], date(2027, 11, 4))
    proceeds = cents(D("30000") * D("0.99"))                    # sell 30,000 par of the Reserve bill
    avail = D("300000.00") + proceeds - D("80000.00")            # − the Near-term fund buy
    cost1 = cents(D("40000") * l1 / D(100))                      # rung B_LT1, 40,000 par
    avail2 = avail - cost1
    face2 = floor100(avail2)                                      # rung N_LT2 at par: what the cash buys
    return {"l1": l1, "lines": [("sell", sec("B_RS"), "reserve", D("30000"), None, "limit", D("99.0000")),
                                ("buy", sec("MMF"), "near_term", None, D("80000.00"), "market", None),
                                ("buy", sec("B_LT1"), "long_term", D("40000"), None, "limit", l1),
                                ("buy", sec("N_LT2"), "long_term", face2, None, "limit", D("100.0000"))],
            "shortfall": D("400000") - face2, "cost1": cost1, "face2": face2}


async def y_builder_db(conn):
    section("[Y] Builder on the REAL plan (H1): Near-term fund by notional, rungs by par with a limit, a Reserve "
            "sale with the warning, the shortfall, the non-cash fund untouched")
    recs = await in_org(conn, ORG_A, lambda c: CPR.build_household(c, ORG_A, H1, TODAY))
    S["h1_recs"] = recs
    rec = next((x for x in recs if str(x["account"]["id"]) == str(ACC_1)), None)
    if not check(rec is not None and rec["built"] is not None, "H1's active policy yields a proposal for ACC_1",
                 f"{[(str(x['account']['id']), x['why']) for x in recs]}"):
        return
    inp, b = rec["inputs"], rec["built"]
    check(inp["targets"] == {"near_term": D("100000.00"), "reserve": D("50000.00"), "long_term": D("470000.00")},
          "the targets are cash02's sleeves for this account (one account → all of it): 100,000 / 50,000 / 470,000",
          f"{inp['targets']}")
    rungs = sorted(inp["rungs"], key=lambda r: r["maturity"])
    shape = [(r["security_id"], r["par"]) for r in rungs]
    check(len(rungs) == 3 and shape[0] == (sec("B_LT1"), D("40000.00")) and shape[1] == (sec("N_LT2"),
                                                                                           D("400000.00"))
          and shape[2][0] is None and shape[2][1] == D("30000.00") and rungs[2]["indicative"] is True,
          "cash03's REAL ladder on the real obligations: the filler bill 2027-11-04 funds the club dues (40,000), the "
          "targeted note 2028-01-13 the January tax (400,000), an indicative rung the pledge (30,000)", f"{shape}")
    exp = expected_h1()
    check(_shape(b["lines"]) == exp["lines"], "the trade list is exactly the hand computation: SELL 30,000 par of the "
          "Reserve bill at 99.0000 (Reserve over target), BUY 80,000 of the chosen fund by notional at market "
          "(Near-term under target), BUY 40,000 par of the 2027-11-04 bill at its yield's limit, BUY the 2028-01-13 "
          "note at par with what the cash leaves", f"{_shape(b['lines'])} vs {exp['lines']}")
    check(b["shortfall"] == exp["shortfall"] and b["shortfall_by_sleeve"]["long_term"] == exp["shortfall"],
          f"the shortfall is {exp['shortfall']} (Long-term), shown — never borrowed", f"{b['shortfall']}")
    check(b["lines"][0]["warning"] == CPR.EARLY_SALE and any(w["code"] == "early_sale" for w in b["warnings"]),
          "the Reserve bill's early sale carries 'may lose principal'")
    check([u["security_id"] for u in b["untouched"]] == [sec("EQF")] and sec("EQF") not in
          [ln["security_id"] for ln in b["lines"]], "the non-cash fund (no N-MFP category) is untouched, never sold",
          f"{b['untouched']}")
    check(any("needs an issue" in n for n in b["notes"]) and not any(ln["security_id"] is None for ln in b["lines"]),
          "the indicative rung is not bought (no CUSIP in the security master) — 'needs an issue'", f"{b['notes']}")
    check(b["lines"][2]["limit_price"] == exp["l1"] and b["lines"][2]["price"] == (exp["l1"] / D(100)).quantize(
        D("0.00000001")), f"the bill's limit is 100 ÷ (1 + 4.05 % × 360 ÷ 365) = {exp['l1']} per 100, its line price "
                          "per $1 of par", f"{b['lines'][2]['limit_price']} {b['lines'][2]['price']}")


# ── Approval → instructions ───────────────────────────────────────────────────────────────────────────────────
async def y_approval(conn):
    section("[Y] Approval through services.sod; instructions only via create_instructions; Manual route; the sheet")
    code, out = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/proposals", {})
    made = out.get("made") or []
    pid = next((m["proposal_id"] for m in made if m["account_id"] == str(ACC_1)), None)
    S["pid"] = pid
    if not check(code == 201 and pid is not None, "POST /cash/households/H1/proposals (U_ADV) writes a DRAFT cash "
                 "proposal for ACC_1", f"{code} {dumps(out)[:600]}"):
        return
    row = await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS NULL",
                       pid, fetch="fetchrow")
    ref = json.loads(row["trigger_ref"]) if isinstance(row["trigger_ref"], str) else row["trigger_ref"]
    check(row["proposal_kind"] == "cash" and row["status"] == "draft" and row["trigger"] == "manual"
          and str(row["maker_id"]) == str(U_ADV) and row["model_id"] is None
          and ref.get("cash_policy_id") == S["policy"][H1] and len(ref.get("ladder") or []) == 3,
          "the proposal is a rebalance_proposals row of kind 'cash' (draft, trigger manual, maker U_ADV, no model) "
          "snapshotting the policy version and the 3 rungs (cash03 D3)", f"{dict(row)}")
    trades = await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposal_trades WHERE proposal_id = $1::uuid "
                                       "AND valid_to IS NULL AND system_to IS NULL ORDER BY line_no", pid)
    exp = expected_h1()
    got = [(t["side"], str(t["global_security_id"]), (json.loads(t["cash_leg"]) if isinstance(t["cash_leg"], str)
                                                      else t["cash_leg"])["sleeve"],
            dec(t["quantity"]), dec(t["notional"])) for t in trades]
    check(got == [(s, i, sl, q, n) for s, i, sl, q, n, _pk, _l in exp["lines"]],
          "the stored lines are the hand-computed trade list (par / notional, sleeve)", f"{got}")
    c2, b2 = await api(U_ADV, "POST", f"/api/v1/proposals/{pid}/submit")
    check(c2 == 200 and (b2.get("proposal") or {}).get("status") == "pending_approval",
          "the maker submits it through the EXISTING route (POST /proposals/{id}/submit) → awaiting approval",
          f"{c2} {dumps(b2)[:300]}")
    before = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE item_id = $1::text AND outcome = "
                                      "'refused'", str(pid), fetch="fetchval"))
    c3, b3 = await api(U_ADV, "POST", f"/api/v1/proposals/{pid}/approve", {"note": "self"})
    after = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE item_id = $1::text AND outcome = "
                                     "'refused'", str(pid), fetch="fetchval"))
    st = await scoped(conn, ORG_A, "SELECT status FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to "
                                   "IS NULL", pid, fetch="fetchval")
    check(c3 == 403 and after == before + 1 and st == "pending_approval",
          "the MAKER cannot approve (403, a VALID body) — refused by services.sod and RECORDED in sod_decisions (by this "
          "proposal's id); the proposal is unchanged", f"{c3} {dumps(b3)[:300]} refusals {before}→{after} {st}")
    n_ci = len(CREATE_CALLS)
    c4, b4 = await api(U_ADV2, "POST", f"/api/v1/proposals/{pid}/approve", {"note": "verify checker"})
    prop = b4.get("proposal") or {}
    ids = (prop.get("execution") or {}).get("instruction_ids") or []
    S["instruction_ids"] = ids
    check(c4 == 200 and prop.get("status") == "approved" and len(ids) == len(exp["lines"]),
          f"a DIFFERENT eligible person (U_ADV2) approves → routed: {len(exp['lines'])} instructions",
          f"{c4} {dumps(b4)[:600]}")
    calls = CREATE_CALLS[n_ci:]
    check(len(calls) == 1 and calls[0]["routed_via"] == "manual" and calls[0]["upstream"] is True
          and calls[0]["trades"] == len(exp["lines"]),
          "the instructions were created ONLY through execution.create_instructions — once, routed_via manual, carrying "
          "the proposal's upstream approval", f"{calls}")
    check(ALTRUIST_CALLS == [], "the Altruist adapter was never invoked (nothing routed to a real custodian)",
          f"{ALTRUIST_CALLS}")
    rows = await scoped(conn, ORG_A, "SELECT * FROM public.execution_instructions WHERE id = ANY($1::uuid[]) AND "
                                     "system_to IS NULL ORDER BY created_at, id", ids)
    by_sec = {str(r["global_security_id"]): r for r in rows}
    ok = len(rows) == len(ids) and len(ids) > 0
    for s_, sid, _sl, q, n, pk, lim in exp["lines"]:
        r = by_sec.get(sid)
        ok = ok and r is not None and r["side"] == s_ and dec(r["quantity"]) == q and dec(r["notional"]) == n \
            and r["price_kind"] == pk and dec(r["limit_price"]) == lim
    check(ok, "every instruction carries its line: the bills / note by PAR with their LIMIT per 100, the fund by "
          "NOTIONAL at market, the sale by par with the account's own valuation as its limit",
          f"{[(r['side'], r['quantity'], r['notional'], r['price_kind'], r['limit_price']) for r in rows]}")
    check(bool(rows) and all(r["routed_via"] == "manual" and r["source"] == "proposal" and str(r["source_ref"]) == pid
                             and r["status"] == "approved" and str(r["maker_id"]) == str(U_ADV)
                             and str(r["approved_by"]) == str(U_ADV2) and r["executor_type"] == "custodian_desk"
                             for r in rows),
          "each instruction: routed_via manual, source proposal (this one), approved by the checker, maker the "
          "proposal's maker, executed by the custodian desk (managed, integrated)",
          f"{[(r['routed_via'], r['status'], r['executor_type']) for r in rows]}")
    chk_ok = True
    for name, d in EXEC_CHECKS.items():
        expr = re.fullmatch(r"CHECK \((.*)\)", d, re.S).group(1)
        n_bad = int(await platform(conn, f"SELECT count(*) FROM public.execution_instructions WHERE id = "
                                         f"ANY($1::uuid[]) AND NOT ({expr})", ids, fetch="fetchval"))
        chk_ok = chk_ok and n_bad == 0
    check(chk_ok and bool(ids), "every instruction satisfies the table's side / amount / price CHECKs (re-evaluated)")
    sides = {r["side"] for r in rows}
    check(sides <= {"buy", "sell"} and bool(sides), "a cash proposal uses buy / sell only", f"{sides}")
    fi = next((r for r in rows if str(r["global_security_id"]) == sec("B_LT1")), None)
    if fi is not None:
        d_ = await in_org(conn, ORG_A, lambda c: X.detail(c, ORG_A, fi["id"]))
        sheet = dict(EL.letter_rows(d_, account_display=d_["account"]["account_number_masked"]))
        check("par (USD face value)" in sheet.get("Instruction", "") and "per 100 of par" in sheet.get("Price", "")
              and "quantity is PAR" in (sheet.get("Notes") or ""),
              "the manual sheet (execution_letter.letter_rows) prints the bill as par with a limit per 100 of par",
              f"{sheet}")
    check(CPR.routes_offered() == [{"key": k, "label": XR.ROUTE_LABELS[k], "refusal": XR.route_refusal(k)}
                                   for k in XR.ROUTES]
          and XR.route_refusal("manual") is None and bool(XR.route_refusal("fix")),
          "route_refusal is honoured: the manual route is offered; 'fix' is shown REFUSED with its reason",
          f"{CPR.routes_offered()}")


async def y_refusals(conn):
    section("[Y] Refusals by the EXISTING path: a held-away account and an advise account; route_trade_list")
    b = next((x["built"] for x in S.get("h1_recs") or [] if x["built"]), None)
    if not check(b is not None, "a valid trade list to send (H1's) — so only the gate can refuse"):
        return
    p = next(x for x in S["h1_recs"] if x["built"])
    ref = CPR.trigger_ref_for(p["scope_plan"], p["inputs"], b)
    pool = await get_pool()
    for acc, want in ((ACC_HA, "held-away"), (ACC_ADV, "advise covers one-off trades only")):
        tokens = set_rls_context(str(ORG_A), False)
        try:
            err = None
            try:
                await CPR.write_proposal(pool, org_id=ORG_A, account_id=acc, built=b, trigger_ref=ref,
                                         maker_kind="user", maker_id=U_ADV, trigger="manual", today=TODAY)
            except CPR.CashProposalConflict as exc:
                err = str(exc)
        finally:
            reset_rls_context(tokens)
        n = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1",
                               acc, fetch="fetchval"))
        check(err is not None and want in err and n == 0,
              f"{'a held-away (read-only)' if acc == ACC_HA else 'an ADVISE'} account is refused a cash proposal by "
              "rep7b's authority check, WITH THE REASON; nothing written", f"{err} rows={n}")
    tokens = set_rls_context(str(ORG_A), False)
    try:
        async with pool.acquire() as c:
            acct = await X.load_account(c, ORG_A, ACC_HA)
        tl = TR.TradeList(account_id=ACC_HA, executor_type="custodian_desk", execute_by=date.today() + timedelta(days=5),
                          trades=[TR.TradeLine(global_security_id=S["sec"]["MMF"], side="buy", notional=D("1000"))])
        err = None
        try:
            await TR.route_trade_list(pool, org_id=ORG_A, actor_id=U_ADV, account=acct, trade_list=tl)
        except X.ExecutionConflict as exc:
            err = str(exc)
    finally:
        reset_rls_context(tokens)
    n = int(await platform(conn, "SELECT count(*) FROM public.execution_instructions WHERE account_id = $1", ACC_HA,
                           fetch="fetchval"))
    check(err is not None and "read-only" in err and n == 0, "route_trade_list (the instruction path) refuses the "
          "held-away account with the reason; no instruction", f"{err} n={n}")
    find("SOC5 trading authority is not checked on the trade path (it gates assistant money movement only); the "
         "refusals above are the trade path's own authority rule (rep7b) — nothing was re-implemented")


# ── Waterfall ─────────────────────────────────────────────────────────────────────────────────────────────────
def expected_w() -> dict:
    l1 = bill_limit(AUCTIONS["B_LT1"], date(2027, 11, 4))
    unit = l1 / D(100)
    face_a = floor100(D("80000") / unit)                          # (2) the remainder of 120,000 after the 40,000 top-up
    near_e = (D("10000") * D("100000") / D("570000")).quantize(D("0.01"), rounding=ROUND_DOWN)   # (3) earnings
    long_e = D("10000") - near_e
    face_b = floor100(long_e / unit)
    return {"lines": [("buy", sec("MMF"), "near_term", None, D("40000.00") + near_e, "market", None),
                      ("buy", sec("B_LT1"), "long_term", face_a + face_b, None, "limit", l1)]}


class _Verb:
    access_type = "write"
    autonomy_class = None

    def __init__(self, key):
        self.key = key


async def y_waterfall(conn):
    section("[Y] Waterfall job: off by default, draft-only under S29b, correct, idempotent")
    spec = J.JOBS.get(CW.JOB_KEY)
    check(spec is not None and [s.action for s in spec.steps] == [CW.ACTION_KEY],
          "the waterfall is a platform JobSpec (security_master_jobs.JOBS) with one service task, cash.maturity_waterfall")
    live = int(await platform(conn, "SELECT count(*) FROM public.workflow_definitions d JOIN public.workflow_triggers t "
                                    "ON t.workflow_definition_id = d.id WHERE d.name = $1 AND t.is_active AND NOT "
                                    "(d.org_id = ANY($2::uuid[]))", spec.name if spec else "", FIXTURE_ORGS,
                              fetch="fetchval"))
    check(live == 0, "SWITCHED OFF: no real org holds an ACTIVE trigger for it (seeded inactive by the operator's "
          "--seed-jobs; switched on only by a Super Admin)", f"{live}")
    act = REGISTRY.get(CW.ACTION_KEY)
    meta = WA.verb_autonomy(act)
    check(act is not None and act.workflow_invocable and meta["class"] == WA.ELIGIBLE and meta.get("draft_only") is True
          and meta.get("platform_job") is True and CW.ACTION_KEY in WA.DRAFT_ONLY_VERBS,
          "S29b: the job's verb is eligible + platform_job + DRAFT-ONLY", f"{meta}")
    check(REGISTRY.get("cash.proposal_submit") is None and REGISTRY.get("cash.proposal_route") is None,
          "the submit / route verbs are NOT registered actions (no workflow and no agent can reach them)")
    tokens = set_rls_context(str(ORG_A), False)
    try:
        pool = await get_pool()
        modes = {}
        for k in ("cash.proposal_submit", "cash.proposal_route"):
            d_ = await WA.decide_step(pool, org_id=ORG_A, run_id=uuid.uuid4(),
                                      run_context={"trigger_id": str(uuid.uuid4()), "trigger_type": "scheduled"},
                                      started_by=None, action=_Verb(k), invocable=True)
            modes[k] = (d_.mode, d_.reason)
    finally:
        reset_rls_context(tokens)
    check(all(m == WA.MODE_SUSPEND and ("never run unattended" in r or "kill switch" in r) for m, r in modes.values()),
          "S29b REFUSES a submit / route from a scheduled run: decide_step suspends it for a person (class never — "
          "submits a trade)", f"{modes}")
    J.PLATFORM_JOB_ORG_IDS.add(ORG_P)
    CW.JOB_KWARGS.update({"org_ids": [str(ORG_A)], "today": TODAY})
    try:
        pool = await get_pool()
        run_id = uuid.uuid4()
        res = await CW.run_job(pool, org_id=ORG_P, user_id=None, run_context={"trigger_type": "manual"},
                               workflow_run_id=run_id)
        data = res["data"]
        check(data["submitted"] == 0 and data["routed"] == 0 and data["drafts_made"] >= 1,
              "the run drafts and NEVER submits or routes", f"{data}")
        rows = await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposals WHERE account_id = $1 AND trigger = "
                                         "'cash_waterfall' AND system_to IS NULL", ACC_W)
        w = rows[0] if len(rows) == 1 else None
        ref = (json.loads(w["trigger_ref"]) if isinstance(w["trigger_ref"], str) else w["trigger_ref"]) if w else {}
        S["wid"] = str(w["id"]) if w else None
        check(w is not None and w["status"] == "draft" and w["maker_kind"] == "system" and w["approval_task_id"] is None
              and ref.get("period") == "2026-11" and ref.get("workflow_run_id") == str(run_id),
              "ONE draft for ACC_W: a SYSTEM draft with no task, period 2026-11, naming its run", f"{[dict(r) for r in rows]}")
        if w is not None:
            trades = await scoped(conn, ORG_A, "SELECT * FROM public.rebalance_proposal_trades WHERE proposal_id = $1 "
                                               "AND valid_to IS NULL AND system_to IS NULL ORDER BY line_no", w["id"])
            got = [(t["side"], str(t["global_security_id"]), (json.loads(t["cash_leg"]) if isinstance(t["cash_leg"], str)
                                                              else t["cash_leg"])["sleeve"], dec(t["quantity"]),
                    dec(t["notional"])) for t in trades]
            exp = expected_w()
            check(got == [(s, i, sl, q, n) for s, i, sl, q, n, _p, _l in exp["lines"]],
                  "hand-computed: the matured 120,000 TOPS UP Near-term first (40,000 of the fund), the rest becomes a "
                  "NEW RUNG on the next UNCOVERED obligation's rung (the 2027-11-04 bill, at its limit); the 10,000 of "
                  "interest is reinvested BY THE TARGET ALLOCATION (100 : 0 : 470 → fund + rung)",
                  f"{got} vs {exp['lines']}")
            check(any(m.get("maturity") == "2026-11-06" for m in ref.get("maturities") or [])
                  and ref.get("earnings") == "10000.00", "the run detected the maturity and the earnings since the last "
                  "run", f"{ref.get('maturities')} {ref.get('earnings')}")
        tasks = await in_org(conn, ORG_A, lambda c: HT.list_tasks(c, ORG_A, status="open", kind=CPR.TASK_KIND_REVIEW,
                                                                  subject_type="household", subject_id=H3, limit=5))
        check(len(tasks) == 1, "ONE review task for the household (cash_waterfall_review)", f"{len(tasks)}")
        res2 = await CW.run_job(pool, org_id=ORG_P, user_id=None, run_context={"trigger_type": "manual"},
                                workflow_run_id=uuid.uuid4())
        n2 = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                      "trigger = 'cash_waterfall'", ACC_W, fetch="fetchval"))
        tasks2 = await in_org(conn, ORG_A, lambda c: HT.list_tasks(c, ORG_A, status="open", kind=CPR.TASK_KIND_REVIEW,
                                                                   subject_type="household", subject_id=H3, limit=5))
        check(n2 == 1 and len(tasks2) == 1 and res2["data"]["drafts_made"] == 0,
              "a re-run in the same period writes NO duplicate proposal and NO second task", f"{n2} {len(tasks2)} "
                                                                                             f"{res2['data']}")
        h1 = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                      "trigger = 'cash_waterfall'", ACC_1, fetch="fetchval"))
        check(h1 == 0, "ACC_1 (its approved proposal still executing) gets no waterfall draft", f"{h1}")
    finally:
        CW.JOB_KWARGS.clear()
        J.PLATFORM_JOB_ORG_IDS.discard(ORG_P)
    if S.get("wid"):
        c, out = await api(U_ADV, "POST", f"/api/v1/cash/proposals/{S['wid']}/adopt", {})
        p = out.get("proposal") or {}
        old = await scoped(conn, ORG_A, "SELECT status FROM public.rebalance_proposals WHERE id = $1::uuid AND "
                                        "system_to IS NULL", S["wid"], fetch="fetchval")
        check(c == 200 and p.get("status") == "pending_approval" and p.get("maker_id") == str(U_ADV)
              and p.get("trigger") == "manual" and old == "superseded",
              "a PERSON adopts the draft: a new proposal (their own) is submitted for a DIFFERENT person's approval; the "
              "system draft is superseded", f"{c} {dumps(out)[:400]} old={old}")


# ── App: visibility, isolation, member view ───────────────────────────────────────────────────────────────────
async def treasury(uid, hh, horizon=None):
    q = f"?horizon_months={horizon}" if horizon else ""
    return await api(uid, "GET", f"/api/v1/cash/households/{hh}/treasury{q}")


async def y_app(conn):
    section("[Y] Screens' envelope, visibility (staff see only visible families), org isolation both directions")
    c, env = await treasury(U_ADV, H1)
    blk = env.get("proposals") or {}
    perms = env.get("permissions") or {}
    mine = next((p for p in blk.get("proposals") or [] if p.get("id") == S.get("pid")), None)
    check(c == 200 and perms.get("can_view_proposals") is True and perms.get("can_generate_proposals") is True
          and "generate" in (blk.get("actions") or []) and mine is not None and len(mine.get("lines") or []) == 4,
          "the adviser's Treasury envelope carries the Proposed-trades block (the H1 proposal and its 4 lines)",
          f"{c} {dumps(blk)[:500]}")
    if mine:
        l0 = mine["lines"][0]
        check(l0.get("warning") == CPR.EARLY_SALE and l0.get("sleeve") == "reserve" and mine.get("link") ==
              f"/portfolio/proposals/{S['pid']}" and len(mine.get("instruction_links") or []) == 4,
              "lines carry sleeve / reason / warning, a LINK to the existing proposal screen and to each instruction",
              f"{dumps(l0)[:300]}")
    c, env = await treasury(U_ADV, H3)
    hist = (env.get("proposals") or {}).get("waterfall_history") or []
    check(c == 200 and any(h.get("period") == "2026-11" for h in hist), "H3's envelope lists the waterfall's run "
          "history (period 2026-11)", f"{dumps(hist)[:300]}")
    c, env = await treasury(U_VIEW, H1)
    check(c == 200 and env.get("proposals") is None and (env.get("permissions") or {}).get("can_view_proposals")
          is False, "a reader without view_proposals gets NO proposals block (fails closed)", f"{c}")
    c, env = await treasury(U_MEM, H1)
    check(c == 200 and env.get("member_view") is True and env.get("proposals") is None
          and "cash_leg" not in dumps(env) and "waterfall_history" not in dumps(env),
          "a MEMBER's envelope is unchanged: no proposals block, no trade detail", f"{c}")
    c, out = await api(U_VIEW, "POST", f"/api/v1/cash/households/{H1}/proposals", {})
    check(c == 403, "generating needs manage_proposals (compliance refused, 403)", f"{c} {dumps(out)[:200]}")
    before = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1", ORG_A,
                                fetch="fetchval"))
    c1, _o = await api(U_RS, "POST", f"/api/v1/cash/households/{H1}/proposals", {})
    c2, _e = await treasury(U_RS, H1)
    c3, env3 = await treasury(U_RS, H2)
    after = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1", ORG_A,
                               fetch="fetchval"))
    check(c1 == 404 and c2 == 404 and c3 == 200 and env3.get("proposals") is not None and after == before,
          "a restricted staff member (H2 only) gets 404 on H1 (generate and read), sees H2's panel; nothing written",
          f"{c1} {c2} {c3} {before}→{after}")
    c4, _o = await api(U_B, "POST", f"/api/v1/cash/households/{H1}/proposals", {})
    c5, ob = await api(U_B, "POST", f"/api/v1/cash/households/{HB}/proposals", {})
    check(c4 == 404 and c5 == 201 and len(ob.get("made") or []) == 1,
          "org B cannot reach org A's household (404); org B's own household gets its proposal", f"{c4} {c5} "
                                                                                                  f"{dumps(ob)[:300]}")
    a_sees_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1",
                                ORG_B, fetch="fetchval"))
    b_sees_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1",
                                ORG_A, fetch="fetchval"))
    a_own = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1", ORG_A,
                             fetch="fetchval"))
    b_own = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1", ORG_B,
                             fetch="fetchval"))
    ia = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.execution_instructions WHERE org_id = $1", ORG_A,
                          fetch="fetchval"))
    check(a_sees_b == 0 and b_sees_a == 0 and a_own > 0 and b_own > 0 and ia == 0,
          "RLS isolation BOTH directions under a non-bypassing role: A reads none of B's cash proposals and B none of "
          "A's (or A's instructions); each reads its own", f"A→B {a_sees_b} B→A {b_sees_a} own {a_own}/{b_own} instr {ia}")
    routed = await platform(conn, "SELECT DISTINCT routed_via FROM public.execution_instructions WHERE org_id = "
                                  "ANY($1::uuid[])", FIXTURE_ORGS)
    check([r["routed_via"] for r in routed] == ["manual"] and ALTRUIST_CALLS == [],
          "NOTHING was routed to a real custodian: every fixture instruction is routed_via manual; the Altruist adapter "
          "was never called", f"{[r['routed_via'] for r in routed]} {ALTRUIST_CALLS}")


#: cash04 fix 1 — the commit whose services/cash_ladder.py still had the zero-par IndexError (the reproduction)
PRE_FIX_REF = "ec6efd4"


def _prefix_ladder_module():
    """services/cash_ladder.py as of PRE_FIX_REF, loaded as a separate module (never replaces the live one)."""
    import types

    src = subprocess.run(["git", "-C", str(REPO), "show", f"{PRE_FIX_REF}:apps/api/services/cash_ladder.py"],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    mod = types.ModuleType("cash_ladder_prefix_cash04fix1")
    exec(compile(src, "cash_ladder@" + PRE_FIX_REF, "exec"), mod.__dict__)  # noqa: S102 — the repo's own file
    return mod


async def y_empty(conn):
    section("[Y] Empty states (cash04 fix 1): no policy → 200 everywhere; a zero-par ladder → 200 (was a 500)")
    # 1 — reproduce the ORIGINAL failure (the pre-fix _finish), then show the fix resolves it
    zero = {"rungs": [{"par": ZERO, "yield": {"estimated": True, "yield_pct": Decimal("4.05"), "as_of": date(2026, 11, 6)}}
                      for _ in range(3)]}
    try:
        old = _prefix_ladder_module()
        try:
            old._finish({k: (list(v) if isinstance(v, list) else v) for k, v in zero.items()})
            repro = "no exception"
        except IndexError as exc:
            repro = f"IndexError: {exc}"
    except Exception as exc:  # noqa: BLE001
        repro = f"could not load the pre-fix module: {type(exc).__name__}: {exc}"
    check(repro.startswith("IndexError"), f"REPRODUCED: the pre-fix cash_ladder._finish ({PRE_FIX_REF}) raises on 3 "
          "rungs that all carry a yield but hold ZERO par (org B's household: no Long-term obligation, an empty "
          "sleeve) — the 500", repro)
    try:
        fixed = CL._finish({k: (list(v) if isinstance(v, list) else v) for k, v in zero.items()})
        y = fixed["yield"]
        ok, det = y.get("estimated") is False and "holds no par" in (y.get("text") or ""), f"{y}"
    except Exception as exc:  # noqa: BLE001
        ok, det = False, f"{type(exc).__name__}: {exc}"
    check(ok, "FIXED: the same input → not estimated, 'the ladder holds no par' (no exception)", det)
    # 2 — org B's own household (the zero-par ladder, through the real plan): the Treasury GET is 200 with NO plan error
    c, env = await treasury(U_B, HB)
    plan = env.get("plan") or {}
    lad = [(sc.get("ladder") or {}).get("yield") for sc in plan.get("scopes") or []]
    check(c == 200 and "error" not in plan and "error" not in (env.get("proposals") or {}) and len(lad) == 1
          and "holds no par" in ((lad[0] or {}).get("text") or ""),
          "org B's own household: GET /treasury answers 200, its plan built (no 'error' — the envelope used to hide "
          "the IndexError as a plan error), the ladder 'holds no par'", f"{c} {dumps(lad)[:300]} "
                                                                       f"{dumps(plan.get('error'))[:200]}")
    # 3 — a household with NO active cash policy (H2): every cash GET is 200; generating is 200 with the empty state
    n_pol = int(await platform(conn, "SELECT count(*) FROM public.cash_policies WHERE household_id = $1::uuid AND "
                                     "status = 'active'", str(H2), fetch="fetchval"))
    check(n_pol == 0, "the fixture premise: H2 holds NO active cash policy", f"{n_pol}")
    c1, b1 = await api(U_ADV, "GET", "/api/v1/cash/households")
    c2, e2 = await treasury(U_ADV, H2)
    p2 = e2.get("plan") or {}
    check(c1 == 200 and c2 == 200 and "error" not in p2 and p2.get("scopes") == []
          and "error" not in (e2.get("proposals") or {}) and (e2.get("proposals") or {}).get("proposals") == [],
          "every cash GET answers 200 for the no-policy household: GET /cash/households, GET /treasury (no plan scopes, "
          "no plan error, an empty proposals panel)", f"{c1} {c2} {dumps(p2)[:200]}")
    c3, e3 = await treasury(U_ADV, H2, horizon=60)
    check(c3 == 200 and "error" not in (e3.get("plan") or {}), "GET /treasury?horizon_months=60 (no policy) → 200",
          f"{c3}")
    before = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1::uuid",
                                str(ORG_A), fetch="fetchval"))
    c4, b4 = await api(U_ADV, "POST", f"/api/v1/cash/households/{H2}/proposals", {})
    after = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE org_id = $1::uuid",
                               str(ORG_A), fetch="fetchval"))
    check(c4 == 200 and b4.get("empty") == CPR.NO_ACTIVE_POLICY and b4.get("made") == [] and before == after,
          "generating for the no-policy household → 200 'no active cash policy — nothing to propose'; nothing written",
          f"{c4} {dumps(b4)[:300]} {before}→{after}")


async def y_loaders(conn):
    section("[Y] Operator step: olivo_cash_proposal --dry-run writes nothing and never routes")
    have = list(inspect.signature(OL.main).parameters)
    check(have == ["argv"], "olivo_cash_proposal.main(argv) — the signature this section calls", f"{have}")
    before, c_before = await real_fingerprints(conn), await counts(conn)
    n_ci = len(CREATE_CALLS)
    orig = DBC.hydrate
    DBC.hydrate = lambda: None          # already hydrated: no second Doppler call
    install_reads(False)                # the demo reads its REAL sources (switched off), never the fixture
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = await OL.main(["--dry-run"])
    finally:
        DBC.hydrate = orig
        install_reads(True)
    out = buf.getvalue()
    after, c_after = await real_fingerprints(conn), await counts(conn)
    check(rc == 0 and "DRY RUN — nothing written (read-only transaction)." in out
          and (OL.NO_POLICY in out or "Nothing routed" in out),
          "olivo_cash_proposal.py --dry-run exits 0: the demo family's lines + the sheet each WOULD become, or 'no ACTIVE "
          "cash policy' (cash02's approval step not done)", f"rc={rc} {out[-500:]}")
    if OL.NO_POLICY in out:
        find("the Olivo household has no ACTIVE cash policy yet — the dry run printed that and exited 0")
    check(before == after and c_before == c_after and len(CREATE_CALLS) == n_ci,
          "the dry run wrote NOTHING (real + demo fingerprints and every fixture count unchanged; no instruction path "
          "call)", f"{[k for k in before if before[k] != after.get(k)]}")


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
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) >= 6 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the cash04 render tests ({NODE_TEST}, counted by their file: proposed trades grouped by sleeve / rung, "
          f"warnings + shortfall + after-tax, run history, fail-closed controls, member view, routes) all pass "
          f"({n.group(1) if n else '?'})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


#: the nested regressions, each ONCE, flat, after teardown: cash03 (the plan this sprint refactored into
#: cash_instruments.scope_plans) and rep13 (the execution path a cash proposal routes through)
REGRESSIONS = ("verify_cash03.py", "verify_rep13.py")


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


FIXTURE_SECTIONS = (("builder (db)", y_builder_db), ("approval", y_approval), ("refusals", y_refusals),
                    ("waterfall", y_waterfall), ("app + visibility", y_app), ("empty states", y_empty))
#: --only=<names> (cash04 fix 1): short name → FIXTURE_SECTIONS name; a section that reads another's state pulls it
#: in (ordered). 'app' reads S["pid"] / S["instruction_ids"] (approval) and the waterfall's history (H3).
ONLY_NAMES = {"builder": "builder (db)", "approval": "approval", "refusals": "refusals", "waterfall": "waterfall",
              "app": "app + visibility", "empty": "empty states"}
ONLY_NEEDS = {"app + visibility": ("approval", "waterfall"), "waterfall": ("approval",)}


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
    for _ in range(3):
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
        if only is None:
            await run("builder (pure)", y_builder_pure, conn)
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
        if ORIG:
            install_reads(False)
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (proposals, trades, instructions, events, tasks, sod decisions, "
                  "audit rows, securities, …)", f"{left}")
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
