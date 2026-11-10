"""verify_cash05 — CASH05: the Treasury OVERVIEW and what a family sees day to day (balance over time with transfer
markers, blended net / after-tax yield, the coverage card, HOLDINGS per sleeve with every rung, the ACTIVITY ledger with
a detail drawer, EARNINGS, DOCUMENTS in Eastern time). READ-ONLY: no route writes.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash05.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx transport
refuses and records (asserted empty at the end), the AI helpers refuse and record, and ``storage.get_signed_url`` is a
local stub (a presign is never computed against R2).

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``), the Auth0 profile call,
cash03's four security-data READS (``cash_ladder.read_curve`` / ``available_issues`` / ``read_auctions``,
``cash_instruments.read_mmfs`` — both cash providers ship switched off) returning the fixture issues / yields, and
``storage.get_signed_url``. REAL: the app through an ASGI transport, the net-worth core, services.cash_overview /
cash_holdings / cash_activity / cash_earnings / cash_documents, cash01–03's services, RLS and the live database. The
script's own connection does not bypass RLS (rolbypassrls = false, asserted). TIME IS INJECTED: "today" = 2026-11-09.

FIXTURES (prefix 99000000-0000-0000-0000-0000cac5…): orgs A (VISIBILITY1 enforcement ON, cut-off setting 14:00) and B,
is_test = true (asserted). Users with real user_roles grants (the app's decision on each asserted): org A U_ADV / U_ADV2
advisors (the policy's maker / checker; view_proposals + view_execution), U_VIEW compliance (view_portfolio only),
U_MEM member (H1), U_RS support staff restricted to H2; org B U_B advisor.
H1 (E_T): ACC_1 ****5001 holds the liquid book — cash (100,000 → 60,000 on 10-01), a Treasury MMF (50,000 on 09-15 →
80,000 on 11-06), a Reserve bill (40,000 par at 99.00 from 10-01, cost 39,400), a bill that matured 10-15 (19,800 from
07-01, cost 19,700, restated 0 on 10-15), a Long-term note (100,000 par at 98.50 from 10-20); ACC_EQ ****5002 holds only
an equity fund (NOT liquid); 11 transactions (every ledger type, one pending, one valuation, one non-liquid); documents:
a statement filed at UTC midnight 11-01, a confirmation on the pending buy, a 1099 with no stored file, a statement of
the non-cash account, a statement of H2. H2 (E_O, ACC_O), H3 (E_E, ACC_E: nothing — the empty state), HB (org B).
Obligations through the cash01 API; the H1 policy (N 3, R 12, horizon 24, 3 rungs) through the cash02 API, drafted by
U_ADV, approved by U_ADV2. Fee: H1's household schedule EXCLUDES cash ("not charged"); the charged path is proven in a
ROLLED-BACK transaction (a POSTED fee run can never be deleted — fee_runs_immutable_once_posted — so none is left).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with the execution / models / replica / sod
teardown GUCs, keyed by this run's fixture ids; tables without org_id passed in the plan by their fixture ids. Every
touched table re-read for its exact pre-test count BY FIXTURE IDS ONLY. Real rows (positions, transactions, documents,
links, fee tables, balances, accounts — the Olivo demo's included) fingerprinted before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<a,b,…>: the one-off path (cash05 fix 1) — teardown, prefix guard, real fingerprints, schema read, seed, then
ONLY the named fixture sections (overview | holdings | activity | drawer | earnings | fee | documents | visibility;
'holdings' pulls in 'overview'), then the full teardown + leftover + real-row checks; no regression, no routes /
front-end section.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
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
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash05"
BEFORE_REF = "ee91269"   # cash05-work's base: 'merge cash04'
_R = "any(view_portfolio|manage_portfolio); member scope (own family / published deals; staff unscoped)"
ROUTES = {("GET", "/api/v1/cash/households/{household_id}/overview"): _R,
          ("GET", "/api/v1/cash/households/{household_id}/holdings"): _R,
          ("GET", "/api/v1/cash/households/{household_id}/activity"): _R,
          ("GET", "/api/v1/cash/households/{household_id}/activity/{entry_id}"): _R,
          ("GET", "/api/v1/cash/households/{household_id}/earnings"): _R,
          ("GET", "/api/v1/cash/households/{household_id}/documents"): _R}
SERVICES = ("apps/api/services/cash_overview.py", "apps/api/services/cash_holdings.py",
            "apps/api/services/cash_activity.py", "apps/api/services/cash_earnings.py",
            "apps/api/services/cash_documents.py")
OWN_FILES = SERVICES + ("apps/api/routers/cash.py",)
NODE_TEST = "tests/cash05/cash05.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH05_NOTES.md"
DOC = REPO / "docs" / "cash" / "CASH_TREASURY_SCREENS.md"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash05's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    check(len(o.before) > 0 and not o.changed and not o.removed,
          "cash05 changed and removed no existing route's gate", f"{o.where()}; changed={sorted(flat(o.changed))[:3]} "
                                                               f"removed={sorted(flat(o.removed))[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)} reads (all GET)",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly the treasury read gate at cash05's own end point",
          f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash05's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    for f in SERVICES:
        code = _code_only(_src(f))
        check(bool(code) and not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|write_audit_log)\b", code,
                                           re.I),
              f"{f}: reads only (no INSERT / UPDATE / DELETE / audit write)")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    nw = _code_only(_src("apps/api/services/networth.py"))
    check("if keep is not None:" in nw and "keep=keep" in nw and "edges if edges is not None" in nw,
          "networth: the additive `keep` / `edges` parameters (which rows are valued — never how)")
    ch = _code_only(_src("apps/api/services/cash_holdings.py"))
    check("CC.is_cash_key(" in ch and "CPR.classify(" in ch and "CP.windows(" in ch and "NW.load(" in ch,
          "cash_holdings reads through cash01's key rule, cash04's classify, cash02's windows and the core")
    ce = _code_only(_src("apps/api/services/cash_earnings.py"))
    check("resolve_assignment_for_account(" in ce and "fee_run_lines" in _src("apps/api/services/cash_earnings.py")
          and "fee_calc" not in ce, "cash_earnings reads the fee engine's own records (assignment, posted lines) and "
                                    "never re-prices")
    # CASH07 (2026-11-09): these three are SINCE-BASE facts about what cash05 itself did, so they read cash05's OWN end
    # point (VS.own_text / VS.own_diff), never the working tree — cash07 adds payment routes in their own router, shows
    # cash.sweep_rate on a MEMBER's overview (routers/cash.py) and adds docs/cash07_part1.sql, each on purpose.
    rc = _code_only(VS.own_text(SPRINT_KEY, "apps/api/routers/cash.py") or "")
    tail = rc[rc.find("CASH05_LABELS"):]
    check(bool(rc) and "@router.post" not in tail and tail.count("@router.get(") == 6,
          "routers/cash.py at cash05's own end point: the CASH05 block declares six GET routes and no write")
    d = VS.own_text(SPRINT_KEY, "apps/api/services/disclosures.py") or ""
    own_services = "".join(_code_only(VS.own_text(SPRINT_KEY, f) or "") for f in SERVICES if not f.endswith("holdings.py"))
    check('"cash.sweep_rate"' in d and '"cash.sweep_rate"' not in rc and "SWEEP_RATE_KEY" not in own_services,
          "cash.sweep_rate was RESERVED by cash05 in the disclosure library and never resolved by a cash05 read "
          "(cash05's own end point; cash07 shows it to members on purpose)")
    code, out, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "docs/*.sql", "apps/api/migrations", args=("--name-only",))
    check(code == 0 and not out.strip(), "no migration: cash05's own diff changed no .sql file (no new table)",
          out.strip()[:200])
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec_ in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
        check(sec_ in notes, f"docs/cash/CASH05_NOTES.md has section {sec_}")
    check(DOC.exists() and "## Earnings" in DOC.read_text(encoding="utf-8")
          and "| maturity |" in DOC.read_text(encoding="utf-8"),
          "docs/cash/CASH_TREASURY_SCREENS.md: sources and formulas per screen, the type mapping, the earnings method")
    check("verify_cash05.py" in _src("scripts/regression_set.txt"), "verify_cash05.py is in scripts/regression_set.txt")


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
import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
import routers.cash as RC  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import cash_activity as CA  # noqa: E402
from services import cash_documents as CD  # noqa: E402
from services import cash_earnings as CE  # noqa: E402
from services import cash_holdings as CH  # noqa: E402
from services import cash_instruments as CI  # noqa: E402
from services import cash_ladder as CL  # noqa: E402
from services import cash_overview as CO  # noqa: E402
from services import disclosures as DS  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import storage as STO  # noqa: E402
from services import users as users_mod  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cash05verify"
NAME = "CASH05 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac5"
TODAY = date(2026, 11, 9)
TEARDOWN_GUCS = ("execution.teardown", "models.teardown", "replica.teardown", "sod.teardown")
CUSIP_PREFIX = "99C05"
CLASS_PREFIX = "C99005"
CUSTODIAN = "cash05verify"
FULL_NUMBERS = {"ACC_1": "990055501234", "ACC_EQ": "990055502345", "ACC_O": "990055503456", "ACC_E": "990055504567",
                "ACC_B": "990055505678"}
D = Decimal
ZERO = D(0)


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_ADV, U_ADV2, U_VIEW, U_MEM, U_RS, U_B = (_id(10 + i) for i in range(6))
H1, H2, H3, HB = _id(30), _id(31), _id(32), _id(33)
E_T, E_O, E_E, E_B = _id(40), _id(41), _id(42), _id(43)
ACC = {"ACC_1": _id(70), "ACC_EQ": _id(71), "ACC_O": _id(72), "ACC_E": _id(73), "ACC_B": _id(74)}
DOC_S, DOC_C, DOC_T, DOC_X, DOC_O = _id(80), _id(81), _id(82), _id(83), _id(84)
PROP, INSTR = _id(90), _id(91)
SCHED_X, ASSIGN_X = _id(95), _id(96)

PERMS = {
    "advisor": {"view_portfolio", "manage_portfolio", "view_members", "manage_cash_policy", "view_proposals",
                "view_execution"},
    "compliance": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "support_staff": {"view_portfolio", "view_members"},
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
ENTS = {E_T: (ORG_A, "trust", "Dynasty", "Treasury Trust"), E_O: (ORG_A, "individual", None, "Other Person"),
        E_E: (ORG_A, "individual", None, "Empty Person"), E_B: (ORG_B, "individual", None, "Org B Person")}
HOUSEHOLDS = {H1: (ORG_A, "Treasury", (E_T,)), H2: (ORG_A, "Other", (E_O,)), H3: (ORG_A, "Empty", (E_E,)),
              HB: (ORG_B, "B", (E_B,))}
# (key, org, household, entity, masked)
ACCOUNTS = [("ACC_1", ORG_A, H1, E_T, "****5001"), ("ACC_EQ", ORG_A, H1, E_T, "****5002"),
            ("ACC_O", ORG_A, H2, E_O, "****5003"), ("ACC_E", ORG_A, H3, E_E, "****5004"),
            ("ACC_B", ORG_B, HB, E_B, "****5005")]
#: account_balances_daily: (account, as_of, cash, total) — ACC_1's is the fee engine's input at the run's period end
BALANCES = [("ACC_1", date(2026, 9, 30), "60000.00", "300000.00"), ("ACC_O", date(2026, 11, 8), "1000.00", "1000.00"),
            ("ACC_B", date(2026, 11, 8), "500.00", "500.00")]
#: fixture securities: key → (name, security_type, id_type, id_value, attrs{field: (value, provider)})
SECS = {
    "MMF": ("Treasury MMF", "fund", "sec_class_id", "C990050001",
            {"mmf_category": ("treasury", "sec_nmfp"), "fund_structure": ("money_market", "sec_nmfp")}),
    "BILL": ("Bill 2027-03-01", "fixed_income", "cusip", "99C050AA1",
             {"treasury_type": ("Bill", "treasury_fiscal"), "maturity_date": ("2027-03-01", "treasury_fiscal")}),
    "BMAT": ("Bill 2026-10-15", "fixed_income", "cusip", "99C050AB9",
             {"treasury_type": ("Bill", "treasury_fiscal"), "maturity_date": ("2026-10-15", "treasury_fiscal")}),
    "NOTE": ("Note 2028-01-13", "fixed_income", "cusip", "99C050AC7",
             {"treasury_type": ("Note", "treasury_fiscal"), "maturity_date": ("2028-01-13", "treasury_fiscal")}),
    "EQF": ("Equity Fund", "fund", "sec_class_id", "C990050009", {}),
}
LIQUID_KEYS = ("CASH", "MMF", "BILL", "BMAT", "NOTE")
#: assets: key → (security key | None, asset_type, default taxonomy key) — the bill / note carry NO key: they are
#: liquid only through the sleeve rule ("+ the sleeves"); cash and the fund are Cash & Equivalents
ASSETS = {"CASH": (None, "cash", "taxonomy_mc_8_1"), "MMF": ("MMF", "fund", "taxonomy_sub_8_1_1"),
          "BILL": ("BILL", "fixed_income", None), "BMAT": ("BMAT", "fixed_income", None),
          "NOTE": ("NOTE", "fixed_income", None), "EQF": ("EQF", "fund", None),
          "MMF_O": ("MMF", "fund", "taxonomy_sub_8_1_1"), "MMF_B": ("MMF", "fund", "taxonomy_sub_8_1_1")}
ASSET_ORG = {"MMF_B": ORG_B}
#: positions: (key, asset, account, owner, as_of, quantity, market value, cost basis)
POSITIONS = [
    ("P_CASH1", "CASH", "ACC_1", E_T, date(2026, 8, 1), "100000", "100000.00", None),
    ("P_CASH2", "CASH", "ACC_1", E_T, date(2026, 10, 1), "60000", "60000.00", None),
    ("P_MMF1", "MMF", "ACC_1", E_T, date(2026, 9, 15), "50000", "50000.00", "50000.00"),
    ("P_MMF2", "MMF", "ACC_1", E_T, date(2026, 11, 6), "80000", "80000.00", "80000.00"),
    ("P_BILL", "BILL", "ACC_1", E_T, date(2026, 10, 1), "40000", "39600.00", "39400.00"),
    ("P_BMAT1", "BMAT", "ACC_1", E_T, date(2026, 7, 1), "20000", "19800.00", "19700.00"),
    ("P_BMAT2", "BMAT", "ACC_1", E_T, date(2026, 10, 15), "0", "0.00", "0.00"),
    ("P_NOTE", "NOTE", "ACC_1", E_T, date(2026, 10, 20), "100000", "98500.00", "98000.00"),
    ("P_EQF", "EQF", "ACC_EQ", E_T, date(2026, 8, 1), "100", "25000.00", "20000.00"),
    ("P_MMF_O", "MMF_O", "ACC_O", E_O, date(2026, 11, 1), "7000", "7000.00", "7000.00"),
    ("P_MMF_B", "MMF_B", "ACC_B", E_B, date(2026, 11, 1), "9000", "9000.00", "9000.00"),
]
#: transactions: key → (position, code, trade date, settle date, quantity, net amount) — expected ledger below
TXS = {
    "T1": ("P_MMF1", "buy", date(2026, 9, 15), date(2026, 9, 15), "50000", "50000.00"),
    "T2": ("P_MMF2", "buy", date(2026, 11, 6), date(2026, 11, 10), "30000", "30000.00"),
    "T3": ("P_MMF2", "dividend", date(2026, 10, 31), None, None, "180.25"),
    "T4": ("P_MMF2", "interest", date(2026, 11, 2), None, None, "95.10"),
    "T5": ("P_BMAT1", "sell", date(2026, 10, 15), date(2026, 10, 15), "20000", "20000.00"),
    "T6": ("P_NOTE", "interest", date(2026, 11, 2), None, None, "1250.00"),
    "T7": ("P_CASH2", "interest", date(2026, 11, 3), None, None, "12.34"),
    "T8": ("P_CASH2", "fee_expense", date(2026, 10, 31), None, None, "75.00"),
    "T9": ("P_CASH2", "adjustment", date(2026, 11, 4), None, None, "5.00"),
    "T10": ("P_BILL", "valuation", date(2026, 11, 1), None, None, None),
    "T11": ("P_EQF", "buy", date(2026, 9, 1), date(2026, 9, 2), "100", "25000.00"),
    "T_O": ("P_MMF_O", "dividend", date(2026, 11, 2), None, None, "20.00"),
    "T_B": ("P_MMF_B", "dividend", date(2026, 11, 2), None, None, "30.00"),
}
#: the verify's own mapping expectation: key → (type, signed change, pending)
EXPECT_LEDGER = {"T1": ("transfer", D("50000.00"), False), "T2": ("transfer", D("30000.00"), True),
                 "T3": ("dividend", D("180.25"), False), "T4": ("interest", D("95.10"), False),
                 "T5": ("maturity", D("-20000.00"), False), "T6": ("interest", D("1250.00"), False),
                 "T7": ("interest", D("12.34"), False), "T8": ("fee", D("-75.00"), False),
                 "T9": ("credit", D("5.00"), False)}
#: documents: id → (org, entity, file name, created_at (UTC), storage key | None, (record_type, record ref))
DOCS = {
    DOC_S: (ORG_A, E_T, "October statement.pdf", datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc),
            f"{TAG}/statement-5001.pdf", ("account_statement", "ACC_1")),
    DOC_C: (ORG_A, E_T, "Confirmation T2.pdf", datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc),
            f"{TAG}/confirm-t2.pdf", ("portfolio_transaction", "T2")),
    DOC_T: (ORG_A, E_T, "1099 2025.pdf", datetime(2026, 2, 14, 14, 0, tzinfo=timezone.utc), None,
            ("account_tax_form", "ACC_1")),
    DOC_X: (ORG_A, E_T, "Equity account statement.pdf", datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
            f"{TAG}/statement-5002.pdf", ("account_statement", "ACC_EQ")),
    DOC_O: (ORG_A, E_O, "Other family statement.pdf", datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
            f"{TAG}/statement-5003.pdf", ("account_statement", "ACC_O")),
}
OBLIGATIONS = [(H1, E_T, "tax_estimate", "January estimated tax", "400000.00", "2028-01-14", "firm"),
               (H1, E_T, "one_off", "Club dues", "24000.00", "2027-02-01", "estimated")]
POLICY = {"scope_type": "household", "preset": "custom", "near_months": 3, "reserve_months": 12, "horizon_months": 24,
          "rung_count": 3, "buffer_months": "0", "objective_score": 3, "min_coverage_months": "6"}
CURVE = {"DGS1MO": "4.20", "DGS3MO": "4.15", "DGS6MO": "4.10", "DGS1": "4.00", "DGS2": "3.90", "DGS5": "3.95",
         "DGS7": "4.05", "DGS10": "4.20"}
CURVE_DATE = date(2026, 11, 6)
AUCTIONS = {"BILL": D("4.20"), "NOTE": D("3.95")}
AUCTION_DATE = date(2026, 11, 3)
MMF_YIELD = D("4.0000")
MMF_AS_OF = date(2026, 10, 31)
CUTOFF = "14:00"
#: the charged fee path (inserted and ROLLED BACK): a POSTED quarterly run, net fee 750.00 on 300,000 billable
FEE_RUN = {"period_start": date(2026, 7, 1), "period_end": date(2026, 9, 30), "net_fee": D("750.00"),
           "billable": D("300000.00")}

COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "portfolio.assets", "portfolio.positions",
    "portfolio.transactions", "public.accounts", "public.account_balances_daily", "public.cash_obligations",
    "public.cash_policies", "public.documents", "public.document_record_links", "public.fee_schedules",
    "public.fee_assignments", "public.fee_runs", "public.fee_run_lines", "public.rebalance_proposals",
    "public.execution_instructions", "public.execution_instruction_events", "public.workflow_runs",
    "public.workflow_run_steps", "public.sod_decisions", "public.audit_log", "public.member_todos",
    "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.security_reference_attributes",
)
SEED_CHECK_LITERALS = (
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.positions", "positions_basis_chk", ("units",)),
    ("portfolio.transactions", "transactions_authority_chk", ("manual",)),
    ("public.fee_schedules", "fee_schedules_cash_treatment_check", ("INCLUDE", "EXCLUDE", "EXCLUDE_ABOVE_PCT")),
    ("public.fee_schedules", "fee_schedules_billing_frequency_check", ("QUARTERLY",)),
    ("public.fee_schedules", "fee_schedules_billing_timing_check", ("ARREARS",)),
    ("public.fee_schedules", "fee_schedules_product_type_check", ("ASSET_MANAGEMENT",)),
    ("public.fee_schedules", "fee_schedules_rate_type_check", ("BPS",)),
    ("public.fee_schedules", "fee_schedules_status_check", ("APPROVED",)),
    ("public.fee_schedules", "fee_schedules_valuation_method_check", ("PERIOD_END",)),
    ("public.fee_assignments", "fee_assignments_scope_type_check", ("HOUSEHOLD", "ACCOUNT")),
    ("public.fee_runs", "fee_runs_status_check", ("POSTED",)),
    ("public.fee_runs", "fee_runs_run_type_check", ("SCHEDULED",)),
    ("public.fee_runs", "fee_runs_billing_frequency_check", ("QUARTERLY",)),
    ("public.fee_run_lines", "fee_run_lines_payment_method_check", ("CUSTODIAL_DEBIT",)),
    ("public.execution_instructions", "execution_instructions_side_chk", ("buy",)),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("custodian_desk",)),
    ("public.execution_instructions", "execution_instructions_if_not_filled_chk", ("cancel",)),
    ("public.execution_instructions", "execution_instructions_source_chk", ("proposal",)),
    ("public.execution_instructions", "execution_instructions_status_chk", ("draft", "reconciled", "filled")),
    ("public.execution_instructions", "execution_instructions_price_chk", ("market",)),
    ("public.rebalance_proposals", "rebalance_proposals_kind_chk", ("cash", "cash_policy_id")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("manual",)),
    ("public.rebalance_proposals", "rebalance_proposals_status_chk", ("draft",)),
    ("public.rebalance_proposals", "rebalance_proposals_maker_kind_chk", ("user",)),
    ("public.cash_obligations", "cash_obligations_source_type_chk", ("one_off", "tax_estimate")),
    ("public.cash_obligations", "cash_obligations_certainty_chk", ("firm", "estimated")),
    ("public.cash_policies", "cash_policies_preset_chk", ("custom",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("fixed_income", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "sec_class_id")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_field_chk",
     ("maturity_date", "treasury_type", "mmf_category", "fund_structure")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_treasury_chk", ("Bill", "Note")),
    ("portfolio.security_reference_attributes", "security_reference_attributes_mmf_chk", ("treasury",)),
    ("portfolio.security_reference_attributes", "security_reference_attributes_structure_chk", ("money_market",)),
    ("public.delegate_grants", "delegate_grants_scope_check", ("view_only",)),
    ("public.execution_instruction_events", "execution_instruction_events_action_chk", ("reconcile",)),
)
_O, _A, _U, _P = (str(ORG_A), str(ACC["ACC_1"]), str(U_ADV), str(PROP))
#: every row SHAPE seeded by raw SQL (or inserted in the rolled-back fee transaction) — evaluated against EVERY CHECK
#: on its table IN THE DATABASE before seeding (jsonb_populate_record → the constraint's own expression)
SEED_ROWS = {
    "portfolio.positions": [{"org_id": _O, "owner_entity_id": _O, "asset_id": _O, "as_of_date": "2026-10-01",
                             "ownership_basis": "units", "quantity": "40000", "market_value": "39600.00",
                             "cost_basis": "39400.00", "authority": "manual", "source_system": "manual",
                             "account_id": _A}],
    "portfolio.transactions": [{"org_id": _O, "position_id": _O, "transaction_type_code": "buy",
                                "trade_date": "2026-11-06", "settle_date": "2026-11-10", "quantity": "30000",
                                "gross_amount": "30000.00", "net_amount": "30000.00", "authority": "manual",
                                "source_system": "manual"}],
    "public.fee_schedules": [
        {"org_id": _O, "code": f"{TAG}-x", "version": 1, "name": "x", "product_type": "ASSET_MANAGEMENT",
         "rate_type": "BPS", "billing_frequency": "QUARTERLY", "billing_timing": "ARREARS",
         "valuation_method": "PERIOD_END", "cash_treatment": "EXCLUDE", "status": "APPROVED"},
        {"org_id": _O, "code": f"{TAG}-i", "version": 1, "name": "i", "product_type": "ASSET_MANAGEMENT",
         "rate_type": "BPS", "billing_frequency": "QUARTERLY", "billing_timing": "ARREARS",
         "valuation_method": "PERIOD_END", "cash_treatment": "INCLUDE", "status": "APPROVED"}],
    "public.fee_assignments": [{"org_id": _O, "fee_schedule_id": _O, "scope_type": "HOUSEHOLD", "scope_id": _O,
                                "precedence": 50, "effective_from": "2026-01-01"},
                               {"org_id": _O, "fee_schedule_id": _O, "scope_type": "ACCOUNT", "scope_id": _A,
                                "precedence": 1, "effective_from": "2026-01-01"}],
    "public.fee_runs": [{"org_id": _O, "period_start": "2026-07-01", "period_end": "2026-09-30",
                         "billing_frequency": "QUARTERLY", "run_type": "SCHEDULED", "status": "POSTED"}],
    "public.fee_run_lines": [{"org_id": _O, "fee_run_id": _O, "account_id": _A, "product_type": "ASSET_MANAGEMENT",
                              "fee_schedule_id": _O, "billable_value": "300000.00", "valuation_method": "PERIOD_END",
                              "gross_fee": "750.00", "net_fee": "750.00", "payment_method": "CUSTODIAL_DEBIT",
                              "calc_detail": {}}],
    "public.rebalance_proposals": [
        {"id": _P, "org_id": _O, "account_id": _A, "trigger": "manual", "trigger_ref": {"cash_policy_id": _P},
         "status": "draft", "advice_only": False, "account_value": "0", "cash_value": "0", "target_weights": [],
         "before_weights": [], "after_weights": [], "warnings": [], "sell_later": [], "execution": {},
         "expires_at": "2026-11-14T00:00:00+00:00", "maker_kind": "user", "maker_id": _U, "proposal_kind": "cash",
         "est_st_gain": "0", "est_lt_gain": "0", "gain_realized_ytd": "0"}],
    "public.execution_instructions": [
        {"org_id": _O, "account_id": _A, "global_security_id": _O, "side": "buy", "quantity": "30000",
         "price_kind": "market", "executor_type": "custodian_desk", "execute_by": "2026-11-14",
         "if_not_filled": "cancel", "source": "proposal", "source_ref": _P, "routed_via": "manual",
         "routing_reason": "verify", "status": "draft", "maker_id": _U, "maker_ids": [_U]}],
    "public.execution_instruction_events": [
        {"org_id": _O, "instruction_id": _O, "action": "reconcile", "from_status": "filled", "to_status": "reconciled",
         "actor_id": _U, "detail": {"method": "manual", "transaction_ids": [_O]}}],
    "portfolio.security_reference_attributes": [
        {"global_security_id": _O, "field": "maturity_date", "value": "2027-03-01", "provider_key": "treasury_fiscal",
         "as_of": "2026-11-09"},
        {"global_security_id": _O, "field": "mmf_category", "value": "treasury", "provider_key": "sec_nmfp",
         "as_of": "2026-11-09"}],
}
#: literals in seed()'s source that are not CHECK-governed values: GUC names, booleans, intervals, labels
#: ('trust' / 'taxable' are Rule 1 config vocabularies — account_registration_types / account_tax_statuses — and
#: public.accounts carries no CHECK on them; '[]' is a jsonb empty array; the document status and an asset's
#: currency_code are unconstrained text — assets_cash_active_uniq keys on the currency, one cash asset per org)
SEED_NON_COLUMN = {"app.is_super_admin", "true", "1 day", "active", "staff_visibility.enforced", "general",
                   "Lead adviser", "application/pdf", "trust", "taxable", "[]", "USD"}
UNIQUES = ("assets_cash_active_uniq", "fee_schedules_code_version_uq", "rebalance_proposals_open_uq",
           "document_record_links_document_id_record_type_record_id_key", "accounts_active_identity_uq",
           "cash_policies_active_household_uq", "uq_sec_global_ident_issuer_assigned", "uq_sec_global_ident_sec_class",
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
    "portfolio.assets": ("id", "org_id", "global_security_id", "name", "asset_type", "default_taxonomy_key",
                         "currency_code"),
    "portfolio.positions": ("id", "org_id", "owner_entity_id", "asset_id", "as_of_date", "ownership_basis",
                            "quantity", "market_value", "cost_basis", "authority", "source_system", "account_id"),
    "portfolio.transactions": ("id", "org_id", "position_id", "transaction_type_code", "trade_date", "settle_date",
                               "quantity", "gross_amount", "net_amount", "authority", "source_system"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code",
                 "custodian_account_id", "registration_type", "tax_status", "primary_entity_id", "household_id",
                 "is_discretionary", "is_held_away"),
    "account_balances_daily": ("org_id", "account_id", "as_of_date", "total_market_value", "cash_value",
                               "source_system"),
    "documents": ("id", "org_id", "entity_id", "original_filename", "mime_type", "storage_key", "status",
                  "created_at"),
    "document_record_links": ("document_id", "org_id", "record_type", "record_id"),
    "fee_schedules": ("id", "org_id", "code", "version", "name", "product_type", "rate_type", "billing_frequency",
                      "billing_timing", "valuation_method", "cash_treatment", "status"),
    "fee_assignments": ("id", "org_id", "fee_schedule_id", "scope_type", "scope_id", "precedence", "effective_from"),
    "fee_runs": ("id", "org_id", "period_start", "period_end", "billing_frequency", "run_type", "status"),
    "fee_run_lines": ("org_id", "fee_run_id", "account_id", "product_type", "fee_schedule_id", "billable_value",
                      "valuation_method", "gross_fee", "net_fee", "calc_detail"),
    "rebalance_proposals": ("id", "org_id", "account_id", "trigger", "trigger_ref", "status", "advice_only",
                            "account_value", "cash_value", "target_weights", "before_weights", "after_weights",
                            "warnings", "sell_later", "execution", "expires_at", "maker_kind", "maker_id",
                            "proposal_kind", "est_st_gain", "est_lt_gain", "gain_realized_ytd"),
    "execution_instructions": ("id", "org_id", "account_id", "global_security_id", "side", "quantity", "price_kind",
                               "executor_type", "execute_by", "if_not_filled", "source", "source_ref", "routed_via",
                               "routing_reason", "status", "maker_id", "maker_ids"),
    "execution_instruction_events": ("org_id", "instruction_id", "action", "from_status", "to_status", "actor_id",
                                     "detail"),
    "portfolio.security_reference_attributes": ("global_security_id", "field", "value", "provider_key", "as_of"),
}

S: dict = {"sec": {}, "pos": {}, "tx": {}, "asset": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
SIGNED: list[str] = []
BODIES: list[tuple[str, str]] = []          # (who / path, full body) of every cash05 response — scanned for leaks
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


def cents(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def bp4(x: Decimal) -> Decimal:
    return x.quantize(D("0.0001"), rounding=ROUND_HALF_UP)


def sec(key) -> str:
    return str(S["sec"][key])


def tx(key) -> str:
    return str(S["tx"][key])


# ── the verify's OWN hand formulas (independent of the services) ─────────────────────────────────────────────────
def hand_balance(d: date) -> Decimal:
    """H1's liquid balance on ``d`` from the fixture statements: per (asset, account) the latest statement ≤ d."""
    latest: dict = {}
    for key, asset, acct, owner, as_of, _q, mv, _c in POSITIONS:
        if owner != E_T or asset not in LIQUID_KEYS or as_of > d:
            continue
        k = (asset, acct)
        if k not in latest or as_of > latest[k][0]:
            latest[k] = (as_of, D(mv))
    return sum((v for _a, v in latest.values()), ZERO)


def hand_bill_ytm() -> Decimal:
    p = D("39600.00") / D("40000")
    return bp4((D(1) - p) / p * D(365) / D((date(2027, 3, 1) - TODAY).days) * D(100))


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
    p, DBM._pool = DBM._pool, None
    await close_conn(p)


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


SECS_SQL = ("SELECT id FROM portfolio.securities_global WHERE name LIKE 'CASH05 VERIFY %' UNION SELECT "
            "global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE '99C05%' OR "
            "id_value LIKE 'C99005%'")


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
    out = {}
    for label, sql in (
            ("positions", f"SELECT id, quantity, market_value, valid_to, system_to FROM portfolio.positions WHERE {nf}"),
            ("transactions", f"SELECT id, net_amount, valid_to, system_to FROM portfolio.transactions WHERE {nf}"),
            ("documents", f"SELECT id, storage_key, status FROM public.documents WHERE {nf}"),
            ("document_record_links", f"SELECT id, record_type, record_id FROM public.document_record_links WHERE {nf}"),
            ("fee_schedules", f"SELECT id, cash_treatment, status, system_to FROM public.fee_schedules WHERE {nf}"),
            ("fee_assignments", f"SELECT id, scope_id, system_to FROM public.fee_assignments WHERE {nf}"),
            ("fee_runs", f"SELECT id, status, system_to FROM public.fee_runs WHERE {nf}"),
            ("account_balances_daily", f"SELECT account_id, as_of_date, cash_value FROM public.account_balances_daily "
                                       f"WHERE {nf}"),
            ("accounts", f"SELECT id, tax_status, valid_to, system_to FROM public.accounts WHERE {nf}"),
            ("cash_policies", f"SELECT id, status, system_to FROM public.cash_policies WHERE {nf}")):
        out[f"{label} (real, the Olivo demo's included)"] = await fingerprint(conn, sql, orgs)
    return out


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
                               gucs=TEARDOWN_GUCS, label="verify_cash05")
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
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash05"}}
    if body is not None:
        kw["json"] = body
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
    if "/cash/households/" in path:
        BODIES.append((f"{USERS[uid][2]} {method} {path}", raw))
    try:
        return res.status_code, json.loads(raw or "{}")
    except ValueError:
        return res.status_code, {"raw": raw[:4000]}


def hh_path(hh, screen: str, **q) -> str:
    qs = "&".join(f"{k}={v}" for k, v in q.items() if v is not None)
    return f"/api/v1/cash/households/{hh}/{screen}" + (f"?{qs}" if qs else "")


# ── cash03's four security-data reads, replaced by the fixture (each at its CURRENT signature) ───────────────────
async def fx_read_curve(conn, org_id, today, *, purpose="internal", points=None, providers=None):
    return [{"key": k, "months": m, "yield_pct": D(CURVE[k]), "as_of": CURVE_DATE, "provider": "fred"}
            for k, m in CL.CURVE_POINTS], []


async def fx_available_issues(conn, org_id, today, until, *, purpose="internal", cusip_like=None):
    out = []
    for k in ("BILL", "NOTE"):
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
             "category": "treasury", "yield": {"estimated": True, "yield_pct": MMF_YIELD, "as_of": MMF_AS_OF,
                                               "source": "nmfp", "basis": "7-day net yield (SEC Form N-MFP)"}}]


def fx_signed_url(key: str, expires: int = 3600, bucket: str | None = None) -> str:
    SIGNED.append(key)
    return f"https://signed.invalid/{key}?expires={expires}"


READ_STUBS = ((CL, "read_curve", fx_read_curve), (CL, "available_issues", fx_available_issues),
              (CL, "read_auctions", fx_read_auctions), (CI, "read_mmfs", fx_read_mmfs),
              (STO, "get_signed_url", fx_signed_url))


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
        raise AssertionError(f"verify_cash05: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash05: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    # a stub must match the CURRENT signature of what it replaces (names, kinds)
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


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes, the reuse inventory, no new table without a reason")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec_ in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
        check(sec_ in notes, f"docs/cash/CASH05_NOTES.md has section {sec_}")
    for marker in ("networth.load", "keep", "_current_positions", "account_balances_daily", "transaction_types",
                   "settle_date", "cost_basis", "coupon", "resolve_assignment_for_account", "cash_treatment",
                   "fee_run_lines", "document_record_links", "record_type", "America/New_York", "prospectus",
                   "Reuse inventory", "no new table", "cash.sweep_rate", "cash.mmf_cutoff_et"):
        check(marker.lower() in notes.lower(), f"the notes name {marker!r}")
    for f in ("[FIND] There is no stored daily balance series", "[FIND]** There is no deposit",
              "[FIND]** A note's coupon is not recorded", "[FIND]** The `doc_category` reference list",
              "[FIND]** None.", "[FIND] Fund cut-off times are not recorded"):
        check(f.replace("**", "") in notes.replace("**", ""), f"the notes record the finding {f[:60]!r}")
    if not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md is still not in the repo — nothing was invented from it")
    live = {r["code"] for r in await platform(conn, "SELECT code FROM public.transaction_types WHERE is_active")}
    unmapped = sorted(live - set(CA.TYPE_MAP) - set(CA.EXCLUDED_CODES))
    check(bool(live) and not unmapped, f"every one of the {len(live)} live transaction type codes maps to exactly one "
          "ledger type (or is excluded with a reason: valuation)", f"unmapped={unmapped}")
    check(set(CA.EXCLUDED_CODES) == {"valuation"} and {t for t, _s in CA.TYPE_MAP.values()} <= {k for k, _ in CA.TYPES},
          "the six ledger types are exactly transfer / dividend / interest / maturity / fee / credit (maturity is "
          "derived from a Treasury sell on / after its maturity date)")


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
    section("[Y] Schema: CHECK / UNIQUE / FK constraints and indexes read BEFORE seeding; every INSERT's columns exist")
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
          f"{got} CHECK constraints read; every literal the seeds use is one the database allows", f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(n_chk > 0 and not refused, f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} raw-seeded tables evaluated "
          "by the database against the exact row shapes — none refuses", f"{refused}")
    keep_s, keep_i = SEED_ROWS["public.fee_schedules"], SEED_ROWS["public.execution_instructions"]
    SEED_ROWS["public.fee_schedules"] = [dict(keep_s[0], cash_treatment="SOMETIMES")]
    SEED_ROWS["public.execution_instructions"] = [dict(keep_i[0], source_ref=None)]
    try:
        _n, bad2 = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["public.fee_schedules"], SEED_ROWS["public.execution_instructions"] = keep_s, keep_i
    want = {("public.fee_schedules", "fee_schedules_cash_treatment_check", 0),
            ("public.execution_instructions", "execution_instructions_source_chk", 0)}
    check(want <= set(bad2), "the pre-check is not vacuous: an unknown cash treatment and a proposal-sourced "
          "instruction with no source_ref are each refused by the same evaluation", f"{bad2}")
    lits = seed_literals()
    vals = {str(v) for rows in SEED_ROWS.values() for r in rows for v in r.values() if v is not None}
    vals |= {x for _t, _n2, ls in SEED_CHECK_LITERALS for x in ls} | {CUSTODIAN, TAG}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(lits) and not undeclared, f"every quoted literal in seed()'s source ({len(lits)}) is a declared row "
          "value or a checked CHECK literal", f"{undeclared}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    cons = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype = 'u' AND "
                                                       "conname = ANY($1::text[])", list(UNIQUES))}
    have = set(idx) | cons
    check(have == set(UNIQUES) and len(have) > 0 and all("UNIQUE" in v for v in idx.values()),
          f"{len(have)} unique indexes / constraints read (one cash asset per org and currency, one fee schedule code "
          "version, one OPEN proposal per account, one link per (document, record), …) — the seed respects each",
          f"missing={sorted(set(UNIQUES) - have)}")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY(ARRAY["
              "'portfolio.positions', 'portfolio.transactions', 'public.documents', 'public.document_record_links', "
              "'public.fee_run_lines', 'public.execution_instructions']::regclass[])")]
    check(len(fks) >= 15, "the seeded tables' foreign keys are read (transaction → position / type; position → asset "
          "/ account; link → document; fee line → run / schedule / account; instruction → account / security)",
          f"{len(fks)}")
    ops = {t: {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                      "tablename = $2", s, t)}
           for s, t in (("portfolio", "transactions"), ("portfolio", "positions"), ("public", "documents"),
                        ("public", "document_record_links"), ("public", "fee_run_lines"))}
    check(all(v & {"ALL", "SELECT"} for v in ops.values()), "every table the reads touch carries an RLS read policy",
          f"{ops}")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{by}")
    snap = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    badc = []
    for t, cols in SEED_COLUMNS.items():
        mm = re.search(rf"^-- ===== {re.escape(t)} =====\n((?:--.*\n)+)", snap, re.M)
        have_c = set(re.findall(r"^--\s{3}(\w+)\s", mm.group(1), re.M)) if mm else set()
        miss = [c for c in cols if c not in have_c]
        if not mm or miss:
            badc.append((t, "absent" if not mm else miss))
    check(len(SEED_COLUMNS) > 0 and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables) exist in "
          "docs/schema_snapshot.sql", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and not refused and have == set(UNIQUES)
                          and by is False and not badc)


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    g = int(await platform(conn, f"SELECT count(*) FROM ({SECS_SQL}) x", fetch="fetchval"))
    a = int(await platform(conn, "SELECT count(*) FROM accounts WHERE custodian_code = $1 OR custodian_account_id = "
                                 "ANY($2::text[])", CUSTODIAN, list(FULL_NUMBERS.values()), fetch="fetchval"))
    d = int(await platform(conn, "SELECT count(*) FROM documents WHERE storage_key LIKE $1 OR id::text LIKE $2",
                           f"{TAG}/%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    f = int(await platform(conn, "SELECT count(*) FROM fee_schedules WHERE code LIKE $1", f"{TAG}%", fetch="fetchval"))
    return check(o == 0 and u == 0 and e == 0 and g == 0 and a == 0 and d == 0 and f == 0,
                 "the reserved prefixes (fixture ids, sign-in tag, 'CASH05 VERIFY' names, CUSIPs 99C05…, SEC classes "
                 "C99005…, custodian code / full numbers, storage keys, fee schedule codes) match no real row",
                 f"orgs={o} users={u} entities={e} secs={g} accounts={a} documents={d} schedules={f}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH05 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH05 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH05 {short.upper()}", sub_of(uid), account_role)
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
        for uid, ents in ((U_ADV, (E_T, E_O, E_E)), (U_ADV2, (E_T, E_O, E_E)), (U_VIEW, (E_T,)), (U_RS, (E_O,))):
            for e in ents:
                await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                                   "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, e, uid)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'staff_visibility.enforced', 'true'::jsonb, 'general')", ORG_A)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, $2, $3::jsonb, 'general')", ORG_A, CH.CUTOFF_SETTING, json.dumps(CUTOFF))
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
        acct = {}
        for key, org, hh, ent, masked in ACCOUNTS:
            acct[key] = (org, ent)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "custodian_account_id, registration_type, tax_status, primary_entity_id, household_id, "
                "is_discretionary, is_held_away) VALUES ($1, $2, $3, $4, $5, $6, 'trust', 'taxable', $7, $8, true, "
                "false)", ACC[key], org, masked, f"{TAG}-hash-{masked[-4:]}", CUSTODIAN, FULL_NUMBERS[key], ent, hh)
        for key, day, cash, total in BALANCES:
            await conn.execute(
                "INSERT INTO account_balances_daily (org_id, account_id, as_of_date, total_market_value, cash_value, "
                "source_system) VALUES ($1, $2, $3, $4, $5, 'manual')", acct[key][0], ACC[key], day, D(total), D(cash))
        for key, (skey, atype, tkey) in ASSETS.items():
            aid = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type, "
                               "default_taxonomy_key, currency_code) VALUES ($1, $2, $3, $4, $5, $6, 'USD')",
                               aid, ASSET_ORG.get(key, ORG_A), S["sec"][skey] if skey else None,
                               NAME + (SECS[skey][0] if skey else "Cash"), atype, tkey)
            S["asset"][key] = aid
        for key, asset, acc, owner, as_of, qty, mv, cost in POSITIONS:
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "quantity, market_value, cost_basis, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, "
                "'units', $6, $7, $8, 'manual', 'manual', $9)", pid, acct[acc][0], owner, S["asset"][asset], as_of,
                D(qty), D(mv), dec(cost), ACC[acc])
            S["pos"][key] = pid
        pos_org = {k: acct[a][0] for k, _as, a, *_r in POSITIONS}
        for key, (pkey, code, day, settle, qty, amt) in TXS.items():
            tid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.transactions (id, org_id, position_id, transaction_type_code, trade_date, "
                "settle_date, quantity, gross_amount, net_amount, authority, source_system) VALUES ($1, $2, $3, $4, $5, "
                "$6, $7, $8, $8, 'manual', 'manual')", tid, pos_org[pkey], S["pos"][pkey], code, day, settle, dec(qty),
                dec(amt))
            S["tx"][key] = tid
        for did, (org, ent, fname, created, skey, (rtype, ref)) in DOCS.items():
            await conn.execute("INSERT INTO documents (id, org_id, entity_id, original_filename, mime_type, storage_key, "
                               "status, created_at) VALUES ($1, $2, $3, $4, 'application/pdf', $5, 'active', $6)",
                               did, org, ent, fname, skey, created)
            rec = S["tx"][ref] if ref in S["tx"] else ACC[ref]
            await conn.execute("INSERT INTO document_record_links (document_id, org_id, record_type, record_id) VALUES "
                               "($1, $2, $3, $4)", did, org, rtype, rec)
        sx = SEED_ROWS["public.fee_schedules"][0]
        await conn.execute(
            "INSERT INTO fee_schedules (id, org_id, code, version, name, product_type, rate_type, billing_frequency, "
            "billing_timing, valuation_method, cash_treatment, status) VALUES ($1, $2, $3, 1, $4, $5, $6, $7, $8, $9, $10, "
            "$11)", SCHED_X, ORG_A, sx["code"], NAME + "fee schedule (cash excluded)", sx["product_type"],
            sx["rate_type"], sx["billing_frequency"], sx["billing_timing"], sx["valuation_method"],
            sx["cash_treatment"], sx["status"])
        await conn.execute("INSERT INTO fee_assignments (id, org_id, fee_schedule_id, scope_type, scope_id, precedence, "
                           "effective_from) VALUES ($1, $2, $3, 'HOUSEHOLD', $4, 50, $5)", ASSIGN_X, ORG_A, SCHED_X, H1,
                           date(2026, 1, 1))
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true (privileged "
          "insert path)", f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/{len(FIXTURE_ORGS)} orgs")
    ok_roles = await check_fixture_roles(conn)
    codes = []
    for hh, payer, st, nm, amt, due, cert in OBLIGATIONS:
        code, _b = await api(U_ADV, "POST", f"/api/v1/cash/households/{hh}/obligations",
                             {"entity_id": str(payer), "source_type": st, "name": NAME + nm, "amount": amt,
                              "due_date": due, "certainty": cert})
        codes.append(code)
    ok_obl = check(codes == [201] * len(OBLIGATIONS), "the H1 obligations are recorded through the cash01 API",
                   f"{codes}")
    c1, b1 = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/policies", dict(POLICY))
    pid = (b1.get("policy") or {}).get("id")
    c2, _b2 = await api(U_ADV, "POST", f"/api/v1/cash/households/{H1}/policies/{pid}/submit")
    c3, b3 = await api(U_ADV2, "POST", f"/api/v1/cash/households/{H1}/policies/{pid}/approve", {"note": "verify"})
    S["policy"] = pid
    ok_pol = check(c1 == 201 and c2 == 200 and c3 == 200 and (b3.get("policy") or {}).get("status") == "active",
                   "the H1 cash policy (N 3, R 12, horizon 24, 3 rungs) is drafted by U_ADV and approved by U_ADV2 "
                   "through the cash02 API", f"{c1} {c2} {c3} {dumps(b3)[:400]}")
    # the drawer's link fixtures: a DRAFT cash proposal (cash04's shape) and a DRAFT instruction sourced from it, with a
    # reconcile EVENT naming the pending buy T2 — the event is what the drawer reads; the lifecycle is rep2's to verify
    ok_links = False
    if pid:
        rp = dict(SEED_ROWS["public.rebalance_proposals"][0])
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await conn.execute(
                "INSERT INTO public.rebalance_proposals (id, org_id, account_id, trigger, trigger_ref, status, "
                "advice_only, account_value, cash_value, target_weights, before_weights, after_weights, warnings, "
                "sell_later, execution, expires_at, maker_kind, maker_id, proposal_kind, est_st_gain, est_lt_gain, "
                "gain_realized_ytd) VALUES ($1, $2, $3, $4, $5::jsonb, $6, false, 0, 0, '[]'::jsonb, '[]'::jsonb, "
                "'[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, $7, $8, $9, $10, 0, 0, 0)",
                PROP, ORG_A, ACC["ACC_1"], rp["trigger"], json.dumps({"cash_policy_id": str(pid)}), rp["status"],
                datetime(2026, 11, 14, tzinfo=timezone.utc), rp["maker_kind"], U_ADV, rp["proposal_kind"])
            ei = SEED_ROWS["public.execution_instructions"][0]
            await conn.execute(
                "INSERT INTO public.execution_instructions (id, org_id, account_id, global_security_id, side, quantity, "
                "price_kind, executor_type, execute_by, if_not_filled, source, source_ref, routed_via, routing_reason, "
                "status, maker_id, maker_ids) VALUES ($1, $2, $3, $4, $5, 30000, $6, $7, $8, $9, $10, $11, $12, $13, "
                "$14, $15, ARRAY[$15]::uuid[])", INSTR, ORG_A, ACC["ACC_1"], S["sec"]["MMF"], ei["side"],
                ei["price_kind"], ei["executor_type"], TODAY + timedelta(days=5), ei["if_not_filled"], ei["source"],
                PROP, ei["routed_via"], "verify link fixture", ei["status"], U_ADV)
            await conn.execute(
                "INSERT INTO public.execution_instruction_events (org_id, instruction_id, action, from_status, "
                "to_status, actor_id, detail) VALUES ($1, $2, 'reconcile', 'filled', 'reconciled', $3, $4::jsonb)",
                ORG_A, INSTR, U_ADV, json.dumps({"method": "manual", "transaction_ids": [tx("T2")]}))
        ok_links = True
    S["seeded"] = bool(ok_roles and ok_obl and ok_pol and ok_links and len(S["sec"]) == len(SECS))


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
            ok = ok and PERMS[rname] <= perms and "view_portfolio" in perms
            ok = ok and bool(pr.is_internal) is (rname != "member")
            ok = ok and (("view_execution" in perms) is (rname == "advisor"))
            ok = ok and (("view_proposals" in perms) is (rname == "advisor"))
            if not ok:
                bad.append((short, names, sorted(perms & set().union(*PERMS.values())), bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisors: view "
                 "proposals + execution; compliance / support staff / members: view_portfolio only; no staff bypass)",
                 f"{bad}")


# ── Overview ──────────────────────────────────────────────────────────────────────────────────────────────────
INDEPENDENT_SQL = """
SELECT COALESCE(sum(x.market_value), 0) FROM (
  SELECT DISTINCT ON (p.owner_entity_id, p.asset_id, p.account_id) p.market_value
    FROM portfolio.positions p
    JOIN portfolio.assets a ON a.id = p.asset_id AND a.valid_to IS NULL AND a.system_to IS NULL
   WHERE p.org_id = $1 AND p.owner_entity_id = $2 AND p.as_of_date <= $3 AND p.valid_to IS NULL
     AND p.system_to IS NULL AND p.superseded_by_source IS NULL
     AND (COALESCE(p.taxonomy_key, a.default_taxonomy_key) IN ('taxonomy_mc_8_1')
          OR COALESCE(p.taxonomy_key, a.default_taxonomy_key) ~ '^taxonomy_sub_8_1_[0-9]+$'
          OR a.asset_type = 'cash'
          OR EXISTS (SELECT 1 FROM portfolio.security_reference_attributes r WHERE r.global_security_id =
                     a.global_security_id AND r.valid_to IS NULL AND r.system_to IS NULL
                     AND (r.field = 'mmf_category' OR (r.field = 'treasury_type' AND r.value IN ('Bill', 'Note', 'Bond')))))
   ORDER BY p.owner_entity_id, p.asset_id, p.account_id, p.as_of_date DESC, p.system_from DESC) x
"""

#: cash01's Cash & Equivalents ONLY (the exact keys) — the coverage card's figure
CE_ONLY_SQL = """
SELECT COALESCE(sum(x.market_value), 0) FROM (
  SELECT DISTINCT ON (p.owner_entity_id, p.asset_id, p.account_id) p.market_value
    FROM portfolio.positions p
    JOIN portfolio.assets a ON a.id = p.asset_id AND a.valid_to IS NULL AND a.system_to IS NULL
   WHERE p.org_id = $1 AND p.owner_entity_id = $2 AND p.as_of_date <= $3 AND p.valid_to IS NULL
     AND p.system_to IS NULL AND p.superseded_by_source IS NULL
     AND (COALESCE(p.taxonomy_key, a.default_taxonomy_key) = 'taxonomy_mc_8_1'
          OR COALESCE(p.taxonomy_key, a.default_taxonomy_key) ~ '^taxonomy_sub_8_1_[0-9]+$')
   ORDER BY p.owner_entity_id, p.asset_id, p.account_id, p.as_of_date DESC, p.system_from DESC) x
"""


async def y_overview(conn):
    section("[Y] Overview: balance series == core C&E + sleeves (independent SQL), markers, pending, ranges, yields")
    c, ov = await api(U_ADV, "GET", hh_path(H1, "overview", range="90d"))
    S["overview"] = ov
    pts = ov.get("points") or []
    if not check(c == 200 and len(pts) > 0, "GET overview (90d) answers 200 with points", f"{c} {dumps(ov)[:500]}"):
        return
    days = [date.fromisoformat(p["date"]) for p in pts]
    want_days = [date(2026, 8, 11) + timedelta(days=i) for i in range(91)]
    check(days == want_days, "90d spans 2026-08-11 … 2026-11-09 inclusive: 91 daily core evaluations (both range ends)",
          f"{len(days)} {days[:1]}…{days[-1:]}")
    hand_bad = [(p["date"], p["value"], str(hand_balance(d))) for p, d in zip(pts, days) if dec(p["value"]) !=
                hand_balance(d)]
    check(not hand_bad, "every point equals the verify's HAND computation from the fixture statements (latest "
          "statement on or before the day, per holding)", f"{hand_bad[:4]}")
    for d_, want in ((date(2026, 8, 11), D("119800.00")), (date(2026, 10, 10), D("169400.00")),
                     (date(2026, 11, 9), D("278100.00"))):
        got = next((dec(p["value"]) for p in pts if p["date"] == d_.isoformat()), None)
        check(got == want, f"{d_}: liquid balance {want} by hand (cash + fund + bills + note on that day)", f"{got}")
    sql_bad = []
    for p, d_ in zip(pts, days):
        v = D(str(await scoped(conn, ORG_A, INDEPENDENT_SQL, ORG_A, E_T, d_, fetch="fetchval")))
        if v != dec(p["value"]):
            sql_bad.append((p["date"], p["value"], str(v)))
    check(not sql_bad and len(pts) == 91, "on EVERY sampled day the series equals independent SQL: Cash & Equivalents "
          "(exact keys) + the sleeve instruments, latest statement per holding (RLS-scoped read)", f"{sql_bad[:4]}")
    ce_only = D(str(await scoped(conn, ORG_A, CE_ONLY_SQL, ORG_A, E_T, TODAY, fetch="fetchval")))
    full = D(str(await scoped(conn, ORG_A, INDEPENDENT_SQL, ORG_A, E_T, TODAY, fetch="fetchval")))
    cov = ov.get("coverage") or {}
    check(dec(cov.get("liquid")) == ce_only == D("140000.00") and dec(ov.get("balance")) == full == D("278100.00"),
          "the coverage card is cash01's Cash & Equivalents (independent SQL: 140,000 — cash + the keyed fund) and the "
          "balance adds the sleeves (+ 39,600 bill + 98,500 note, unkeyed) — 278,100",
          f"card {cov.get('liquid')} / SQL {ce_only}; balance {ov.get('balance')} / SQL {full}")
    mk = ov.get("markers") or []
    check([(m["id"], m["date"], dec(m["change"]), m["direction"], m["pending"]) for m in mk] ==
          [(tx("T1"), "2026-09-15", D("50000.00"), "in", False), (tx("T2"), "2026-11-06", D("30000.00"), "in", True)],
          "transfer markers: the 09-15 buy and the PENDING 11-06 buy (the maturity and income are not transfers)",
          f"{dumps(mk)}")
    pend = {p["date"]: (dec(p["pending_in"]), dec(p["pending_out"])) for p in pts}
    check(all(pend[d_] == (D("30000.00"), ZERO) for d_ in ("2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09"))
          and pend["2026-11-05"] == (ZERO, ZERO) and pend["2026-09-15"] == (ZERO, ZERO),
          "tooltip pending in = 30,000 from the trade date (11-06) until settlement (11-10); 0 before; the same-day "
          "settled buy is never pending", f"{[(k, v) for k, v in pend.items() if k >= '2026-11-04']}")
    c30, o30 = await api(U_ADV, "GET", hh_path(H1, "overview", range="30d"))
    p30 = o30.get("points") or []
    check(c30 == 200 and o30.get("range", {}).get("start") == "2026-10-10" and bool(p30) and p30[0]["date"] == "2026-10-10"
          and dec(p30[0]["value"]) == D("169400.00") and [m["id"] for m in o30.get("markers") or []] == [tx("T2")],
          "range boundary: 30d starts 2026-10-10 (inclusive) at 169,400; the 09-15 marker falls outside it",
          f"{c30} {dumps(o30.get('range'))} {p30[:1]}")
    want_start = {"6m": "2026-05-09", "ytd": "2026-01-01", "12m": "2025-11-09", "all": "2026-07-01"}
    got_start = {}
    for k in want_start:
        ck, ok_ = await api(U_ADV, "GET", hh_path(H1, "overview", range=k))
        got_start[k] = (ck, (ok_.get("range") or {}).get("start"), dec((ok_.get("points") or [{}])[-1].get("value")))
    check(all(got_start[k][0] == 200 and got_start[k][1] == v and got_start[k][2] == D("278100.00")
              for k, v in want_start.items()),
          "6m / YTD / 12m / all start where the doc says (all = the first liquid statement, 07-01) and end today",
          f"{got_start}")
    cbad, _o = await api(U_ADV, "GET", hh_path(H1, "overview", range="5y"))
    check(cbad == 422, "an unknown range is refused 422", f"{cbad}")
    y = ov.get("yields") or {}
    yb = hand_bill_ytm()
    gross = bp4((D("80000.00") * MMF_YIELD + D("39600.00") * yb + D("98500.00") * D("3.95")) / D("218100.00"))
    check(y.get("estimated") is True and dec(y.get("net_yield_pct")) == gross and dec(y.get("gross_yield_pct")) == gross
          and dec(y.get("fee_pct")) == ZERO and y.get("fee_text") == CE.NOT_CHARGED and "Σ" in (y.get("method") or ""),
          f"blended net yield = Σ value × yield ÷ value with a yield − fee = {gross}% (fund 4.00 7-day, bill "
          f"{yb} YTM, note 3.95 auction; the fee schedule excludes cash: 0)", f"{dumps(y)}")
    check([w["name"] for w in y.get("without_yield") or []] == [NAME + "Cash"] and dec(y.get("covered")) ==
          D("218100.00"), "cash (no custodian rate) is listed outside the blend, never guessed", f"{dumps(y)}")
    check((y.get("after_tax") or {}).get("estimated") is False and (y.get("after_tax") or {}).get("text") ==
          CI.NEEDS_PROFILE, "after-tax reads 'after-tax: needs a tax profile' with no approved profile")
    acts = [a["key"] for a in ov.get("actions") or []]
    check(acts == ["documents", "edit_policy", "proposed_trades"] and all("#treasury-" in a["href"] for a in
                                                                          ov.get("actions") or []),
          "staff actions are links into existing screens: documents, edit policy, proposed trades", f"{acts}")
    perms = ov.get("permissions") or {}
    check(perms.get("can_read") is True and perms.get("can_write") is False and
          (ov.get("vocabularies") or {}).get("editable") == [], "the read envelope is read-only (can_write false, "
          "editable empty)", f"{perms}")
    ce, oe = await api(U_ADV, "GET", hh_path(H1, "overview", range="90d", entity_id=E_T))
    check(ce == 200 and dec(oe.get("balance")) == D("278100.00") and (oe.get("coverage") or {}).get("entity_id") ==
          str(E_T), "the entity filter (E_T) answers that entity's own figure and its coverage row", f"{ce}")
    cx, _x = await api(U_ADV, "GET", hh_path(H1, "overview", entity_id=E_O))
    check(cx == 404, "an entity the household does not reach is a 404 (the same as a missing one)", f"{cx}")


# ── Holdings ──────────────────────────────────────────────────────────────────────────────────────────────────
async def y_holdings(conn):
    section("[Y] Holdings: sleeve totals == the overview; rungs par vs market + YTM with basis; liquidity per type")
    c, hd = await api(U_ADV, "GET", hh_path(H1, "holdings"))
    S["holdings"] = hd
    groups = {g["key"]: g for g in hd.get("groups") or []}
    if not check(c == 200 and set(groups) >= {"cash", "near_term", "reserve", "long_term"}, "GET holdings answers 200 "
                 "with Cash / Near-term / Reserve / Long-term", f"{c} {dumps(hd)[:500]}"):
        return
    vals = {k: dec(g["value"]) for k, g in groups.items()}
    check(vals == {"cash": D("60000.00"), "near_term": D("80000.00"), "reserve": D("39600.00"),
                   "long_term": D("98500.00")},
          "sleeves: cash 60,000; Near-term the fund 80,000; Reserve the 2027-03-01 bill 39,600; Long-term the "
          "2028-01-13 note 98,500 (the matured bill, restated 0, is not shown)", f"{vals}")
    total = sum(vals.values(), ZERO)
    check(total == dec(hd.get("total")) == dec((S.get("overview") or {}).get("balance")) == D("278100.00"),
          "Σ sleeve values == the holdings total == the overview's balance today (one core read)", f"{total}")
    months = {k: g.get("months_covered") for k, g in groups.items()}
    check(months == {"cash": "30.0", "near_term": "40.0", "reserve": "19.8", "long_term": "49.3"},
          "months of obligations covered = value ÷ 2,000 a month (the 24,000 club dues in the next 12 months ÷ 12): "
          "30.0 / 40.0 / 19.8 / 49.3", f"{months}")
    hs = {h["name"]: h for g in groups.values() for h in g.get("holdings") or []}
    mmf, bill, note, cash = (hs.get(NAME + SECS["MMF"][0]), hs.get(NAME + SECS["BILL"][0]),
                             hs.get(NAME + SECS["NOTE"][0]), hs.get(NAME + "Cash"))
    check(bool(mmf and mmf["liquidity"] == f"Same day if instructed by {CUTOFF} ET" and
               (mmf["yield"] or {}).get("yield_pct") == "4.0000" and (mmf["yield"] or {}).get("as_of") == "2026-10-31"
               and "N-MFP" in ((mmf["yield"] or {}).get("basis") or "") and mmf["shares"] == "80000"
               and "money-market fund" in mmf["description"]),
          "the fund: 7-day yield 4.0000 with basis and as-of, 'Same day if instructed by 14:00 ET' (the org's cut-off), "
          "80,000 shares, a plain-language description", f"{dumps(mmf)}")
    check(bool(bill and (bill["yield"] or {}).get("yield_pct") == str(hand_bill_ytm())
               and "(100 − P) ÷ P × 365 ÷ days" in ((bill["yield"] or {}).get("basis") or "")
               and bill["liquidity"].startswith("At maturity (2027-03-01)") and "may lose principal" in bill["liquidity"]
               and bill["units"] == "par (USD face)"),
          f"the bill: YTM {hand_bill_ytm()} from its own valuation (basis stated), 'At maturity (…); an early sale … "
          "may lose principal', par units", f"{dumps(bill)}")
    check(bool(cash and cash["liquidity"] == "Available now" and (cash["yield"] or {}).get("text") == CH.NO_CASH_RATE),
          "cash: 'Available now'; yield 'not estimated — needs the custodian's cash rate'", f"{dumps(cash)}")
    check(all((h.get("prospectus") or {}).get("href") is None and (h.get("prospectus") or {}).get("text") ==
              CH.NO_PROSPECTUS for h in hs.values()), "no prospectus link is invented: 'not recorded in the security "
                                                      "master' on every holding")
    rungs = groups["long_term"].get("rungs") or []
    held = [r for r in rungs if r["held"]]
    planned = [r for r in rungs if not r["held"]]
    check(len(held) == 1 and held[0]["maturity"] == "2028-01-13" and dec(held[0]["par"]) == D("100000.00")
          and dec(held[0]["market_value"]) == D("98500.00") and dec(held[0]["par_vs_market"]) == D("-1500.00")
          and (held[0]["ytm"] or {}).get("yield_pct") == "3.9500" and "auction" in ((held[0]["ytm"] or {}).get("basis")
                                                                                     or "")
          and "January estimated tax" in held[0]["serves"],
          "the held rung: maturity, par 100,000 vs market 98,500 (−1,500), YTM 3.9500 with its basis, serving the "
          "January estimated tax (cash03's planned rung matched by security)", f"{dumps(held)}")
    check(len(planned) >= 1 and all(r["market_value"] is None and (r["ytm"] or {}).get("text") == "planned — not held"
                                    for r in planned) and len(rungs) == 3,
          "the ladder expands to EVERY rung of the 3-rung plan: the held one + the planned rungs not held, marked",
          f"{dumps(rungs)}")
    cb, hb = await api(U_B, "GET", hh_path(HB, "holdings"))
    hbm = [h for g in hb.get("groups") or [] for h in g.get("holdings") or [] if h["kind"] == "mmf"]
    check(cb == 200 and len(hbm) == 1 and hbm[0]["liquidity"].endswith(f"needs the org setting {CH.CUTOFF_SETTING})"),
          "org B (no cut-off recorded): the fund's liquidity says the cut-off is not recorded — never a guessed time",
          f"{cb} {dumps(hbm)}")


# ── Activity + drawer ─────────────────────────────────────────────────────────────────────────────────────────
async def y_activity(conn):
    section("[Y] Activity: each fixture transaction maps to exactly one type; pending vs settled; filters; empty state")
    c, act = await api(U_ADV, "GET", hh_path(H1, "activity"))
    S["activity"] = act
    ents = {e["id"]: e for e in act.get("entries") or []}
    if not check(c == 200 and len(ents) > 0, "GET activity answers 200 with entries", f"{c} {dumps(act)[:500]}"):
        return
    want = {tx(k): v for k, v in EXPECT_LEDGER.items()}
    got = {i: (e["type"], dec(e["change"]), e["pending"]) for i, e in ents.items()}
    check(got == want, "the ledger is EXACTLY the nine liquid entries, each with one type, its signed change and its "
          "pending flag (buy +, maturity −, dividend / interest +, fee −, credit at its sign)",
          f"{sorted((k, v) for k, v in got.items())} vs {sorted(want.items())}")
    check(tx("T10") not in ents and any(x["id"] == tx("T10") and x["code"] == "valuation" for x in
                                        act.get("excluded") or []) and tx("T11") not in ents,
          "the valuation mark is listed as excluded (moves no money); the equity account's buy is not a cash entry",
          f"{dumps(act.get('excluded'))}")
    t2 = ents.get(tx("T2")) or {}
    check(t2.get("pending") is True and "settles 2026-11-10" in (t2.get("status_text") or "") and "T+0" in
          (t2.get("status_text") or "") and act.get("pending_count") == 1,
          "the 11-06 buy is PENDING with settlement guidance (settles 2026-11-10; a fund T+0 before the cut-off)",
          f"{dumps(t2)}")
    t3 = ents.get(tx("T3")) or {}
    t1 = ents.get(tx("T1")) or {}
    check(t3.get("pending") is False and t3.get("status_text") == CA.NO_SETTLE and t1.get("status_text") ==
          "Settled 2026-09-15", "no settle date → settled on the trade date (said so); a settled buy says when",
          f"{t3.get('status_text')} | {t1.get('status_text')}")
    check(all(e.get("account") == "****5001" for e in ents.values()), "every entry names the account by its last four "
                                                                       "only")
    cf, af = await api(U_ADV, "GET", hh_path(H1, "activity", type="interest"))
    check(cf == 200 and {e["id"] for e in af.get("entries") or []} == {tx("T4"), tx("T6"), tx("T7")},
          "filter type=interest narrows to exactly the three interest entries", f"{cf}")
    cp, ap = await api(U_ADV, "GET", hh_path(H1, "activity", status="pending"))
    check(cp == 200 and [e["id"] for e in ap.get("entries") or []] == [tx("T2")], "filter status=pending → the one "
          "pending entry", f"{cp}")
    cn, an = await api(U_ADV, "GET", hh_path(H1, "activity", type="fee", status="pending"))
    check(cn == 200 and an.get("entries") == [] and an.get("empty_text") == "No activity matches these filters.",
          "a filter that matches nothing says so", f"{cn} {an.get('empty_text')}")
    ce, ae = await api(U_ADV, "GET", hh_path(H3, "activity"))
    check(ce == 200 and ae.get("entries") == [] and ae.get("empty_text") == "No activity for these cash holdings yet.",
          "EMPTY STATE: H3's account holds nothing — 200 with the clear empty text", f"{ce} {dumps(ae)[:300]}")
    cw, aw = await api(U_ADV, "GET", hh_path(H1, "activity", start="2026-11-01", end="2026-11-09"))
    check(cw == 200 and {e["id"] for e in aw.get("entries") or []} == {tx(k) for k in ("T2", "T4", "T6", "T7", "T9")},
          "a trade-date window narrows to its entries (inclusive)", f"{cw}")
    cbad, _b = await api(U_ADV, "GET", hh_path(H1, "activity", type="rebate"))
    check(cbad == 422, "an unknown type filter is refused 422", f"{cbad}")


def record_of(kind: str, body: dict) -> str | None:
    """The record id (or, for a document, the signed URL) a link's GET answered with. Every detail route nests its
    record under its own kind: {"transaction": {...}}, {"position": {...}}, {"instruction": {...}},
    {"proposal": {...}} (cash05 fix 1: instruction / proposal were read off the top level, always None)."""
    if not isinstance(body, dict):
        return None
    if kind in ("transaction", "position", "instruction", "proposal"):
        rid = (body.get(kind) or {}).get("id")
        return None if rid is None else str(rid)
    if kind in DOC_LINK_KINDS:
        return body.get("url")
    return None


#: the drawer names its document link "document"; the Documents screen names its open / download link "download" —
#: both are GET /documents/{id}/download and must answer with the signed URL of THAT document's own storage key
DOC_LINK_KINDS = ("document", "download")


def expected_of(ln: dict) -> str | None:
    if ln["kind"] in DOC_LINK_KINDS:
        key = (DOCS.get(UUID(str(ln["id"]))) or (None,) * 6)[4]
        return f"https://signed.invalid/{key}?expires=3600" if key else None
    return None if ln.get("id") is None else str(ln["id"])


async def link_results(uid, links) -> list[dict]:
    """Every link: kind, path, status, expected record, found record, ok — printed in full by the caller."""
    out = []
    for ln in links:
        c, b = await api(uid, "GET", ln["api"])
        want, got = expected_of(ln), record_of(ln["kind"], b)
        out.append({"kind": ln["kind"], "api": ln["api"], "status": c, "expected": want, "found": got,
                    "ok": bool(c == 200 and want is not None and got == want)})
    return out


def print_links(label: str, res: list[dict]) -> None:
    for r in res:
        print(f"[INFO] {label}: {'ok ' if r['ok'] else 'BAD'} {r['kind']:<11} {r['status']} {r['api']} "
              f"expected={r['expected']} found={r['found']}", flush=True)


async def resolve_links(uid, links, label: str | None = None) -> list:
    res = await link_results(uid, links)
    if label:
        print_links(label, res)
    return [(r["kind"], r["api"], r["status"], r["expected"], r["found"]) for r in res if not r["ok"]]


async def y_drawer(conn):
    section("[Y] Detail drawer: source, account, security, settlement, linked instruction / proposal / documents — "
            "every link resolves (200, the right record)")
    c, d = await api(U_ADV, "GET", hh_path(H1, f"activity/{tx('T2')}"))
    links = d.get("links") or []
    kinds = sorted(ln["kind"] for ln in links)
    check(c == 200 and kinds == ["document", "instruction", "position", "proposal", "transaction"],
          "the pending buy's drawer (adviser) links the transaction, position, the reconciled instruction, its proposal "
          "and the confirmation", f"{c} {kinds} {dumps(d)[:600]}")
    check((d.get("account") or {}).get("account") == "****5001" and (d.get("security") or {}).get("name") == NAME +
          SECS["MMF"][0] and (d.get("settlement") or {}).get("settle_date") == "2026-11-10" and
          (d.get("source") or {}).get("table") == "portfolio.transactions" and (d.get("source") or {}).get("id") ==
          tx("T2"), "source record, account (last four), security and settlement date are in the drawer",
          f"{dumps({k: d.get(k) for k in ('account', 'security', 'settlement', 'source')})}")
    check({ln["id"] for ln in links} == {tx("T2"), str(S["pos"]["P_MMF2"]), str(INSTR), str(PROP), str(DOC_C)},
          "each link names the right record id", f"{[(ln['kind'], ln['id']) for ln in links]}")
    bad = await resolve_links(U_ADV, links, "drawer T2 (adviser)")
    shapes = {}
    for ln in links:
        if ln["kind"] in ("instruction", "proposal"):
            _c, b_ = await api(U_ADV, "GET", ln["api"])
            shapes[ln["kind"]] = ("id" in b_, str((b_.get(ln["kind"]) or {}).get("id")))
    check(shapes == {"instruction": (False, str(INSTR)), "proposal": (False, str(PROP))},
          "REPRODUCED (cash05 fix 1, verify defect): the instruction / proposal routes nest their record "
          "({\"instruction\": {id}}, {\"proposal\": {id}}) — the old body[\"id\"] read was None, so a correct "
          "link failed; record_of now reads body[kind][\"id\"]", f"{shapes}")
    check(not bad and len(links) == 5, "EVERY drawer link resolves: GET its api path → 200 with the same record "
          "(the document → a signed URL for its own storage key)", f"{bad}")
    c3, d3 = await api(U_ADV, "GET", hh_path(H1, f"activity/{tx('T3')}"))
    check(c3 == 200 and sorted(ln["kind"] for ln in d3.get("links") or []) == ["position", "transaction"] and not
          await resolve_links(U_ADV, d3.get("links") or [], "drawer T3 (adviser)"), "an entry with nothing linked offers only its own "
                                                               "records — each resolves", f"{c3}")
    cv, dv = await api(U_VIEW, "GET", hh_path(H1, f"activity/{tx('T2')}"))
    kv = sorted(ln["kind"] for ln in dv.get("links") or [])
    check(cv == 200 and kv == ["document", "position", "transaction"] and not await resolve_links(U_VIEW, dv["links"], "drawer T2 (viewer)"),
          "a reader without view_execution / view_proposals is offered NO instruction or proposal link (never a "
          "dead one); what is offered resolves", f"{cv} {kv}")
    c11, _b = await api(U_ADV, "GET", hh_path(H1, f"activity/{tx('T11')}"))
    cb, _b2 = await api(U_ADV, "GET", hh_path(H1, f"activity/{tx('T_B')}"))
    co, _b3 = await api(U_ADV, "GET", hh_path(H1, f"activity/{tx('T_O')}"))
    check(c11 == 404 and cb == 404 and co == 404, "a non-liquid entry, another org's entry and another family's entry "
          "are each a 404 on H1's drawer", f"{c11} {cb} {co}")


# ── Earnings ──────────────────────────────────────────────────────────────────────────────────────────────────
async def y_earnings(conn):
    section("[Y] Earnings: dividends + interest + cash interest − fee = total == the activity ledger to the cent")
    q = {"period": "custom", "start": "2026-10-01", "end": "2026-11-09"}
    c, er = await api(U_ADV, "GET", hh_path(H1, "earnings", **q))
    if not check(c == 200 and er.get("total") is not None, "GET earnings (2026-10-01 … 2026-11-09) answers 200",
                 f"{c} {dumps(er)[:500]}"):
        return
    div, lad, cash = (dec((er.get("dividends") or {}).get("total")), dec((er.get("ladder_interest") or {}).get("total")),
                      dec((er.get("cash_interest") or {}).get("total")))
    fee, tot = dec((er.get("fee") or {}).get("amount")), dec(er.get("total"))
    check((div, lad, cash, fee, tot) == (D("275.35"), D("1550.00"), D("12.34"), D("0.00"), D("1837.69")),
          "by hand: dividends 180.25 + 95.10 (the fund) = 275.35; ladder 1,250.00 coupon + 300.00 discount realised "
          "at maturity (20,000 − 19,700) = 1,550.00; cash 12.34; fee 0.00 → total 1,837.69",
          f"{(div, lad, cash, fee, tot)}")
    check(div + lad + cash - fee == tot, "dividends + ladder interest + cash interest − fee == total (Decimal)")
    ca, al = await api(U_ADV, "GET", hh_path(H1, "activity", start=q["start"], end=q["end"]))
    ents = al.get("entries") or []
    income = sum((dec(e["change"]) for e in ents if e["type"] in ("dividend", "interest")), ZERO)
    mats = [e for e in ents if e["type"] == "maturity"]
    realised = sum((abs(dec(e["change"])) - D("19700.00") for e in mats), ZERO)
    check(ca == 200 and len(mats) == 1 and income + realised == tot and dec((er.get("reconciliation") or {}).get(
        "ledger_income")) == tot and (er.get("reconciliation") or {}).get("reconciles") is True,
          "EQUALS THE ACTIVITY LEDGER of the same period to the cent: Σ its dividend + interest entries (1,537.69) + "
          "the maturity's proceeds − cost (300.00) == 1,837.69", f"{income} + {realised} vs {tot}")
    check((er.get("fee") or {}).get("charged") is False and (er.get("fee") or {}).get("text") == CE.NOT_CHARGED
          and dec((er.get("reconciliation") or {}).get("ledger_fee_debits")) == D("75.00"),
          "the household's schedule EXCLUDES cash → 'fee on cash: not charged'; the ledger's 75.00 fee debit is shown "
          "beside it, not deducted", f"{dumps(er.get('fee'))}")
    acc = er.get("accreting") or []
    want_acc = cents((D("40000") - D("39400")) * D(40) / D((date(2027, 3, 1) - date(2026, 10, 1)).days))
    check(len(acc) == 1 and dec(acc[0].get("amount")) == want_acc,
          f"the held bill accretes straight-line ({want_acc} over the 40 days) — shown beside the total, NOT in it",
          f"{dumps(acc)}")
    cm, em = await api(U_ADV, "GET", hh_path(H1, "earnings", period="mtd"))
    check(cm == 200 and (em.get("period") or {}).get("start") == "2026-11-01" and dec(em.get("total")) ==
          D("95.10") + D("1250.00") + D("12.34"), "month to date: 95.10 + 1,250.00 + 12.34 = 1,357.44",
          f"{cm} {em.get('total')}")
    cbad, _b = await api(U_ADV, "GET", hh_path(H1, "earnings", period="custom", start="2026-11-09", end="2026-10-01"))
    check(cbad == 422, "a custom period with start after end is refused 422", f"{cbad}")


class _Rollback(Exception):
    pass


async def y_fee_charged(conn):
    section("[Y] Fee on cash when the schedule CHARGES cash — the fee engine's posted line, proven in a ROLLED-BACK "
            "transaction (a POSTED run can never be deleted)")
    before = int(await platform(conn, "SELECT count(*) FROM fee_runs WHERE org_id = $1", ORG_A, fetch="fetchval"))
    out: dict = {}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true), set_config('app.current_org_id', "
                               "$1, true)", str(ORG_A))
            si = SEED_ROWS["public.fee_schedules"][1]
            sid = await conn.fetchval(
                "INSERT INTO fee_schedules (org_id, code, version, name, product_type, rate_type, billing_frequency, "
                "billing_timing, valuation_method, cash_treatment, status) VALUES ($1, $2, 1, $3, $4, $5, $6, $7, $8, "
                "$9, $10) RETURNING id", ORG_A, si["code"], NAME + "fee schedule (cash included)", si["product_type"],
                si["rate_type"], si["billing_frequency"], si["billing_timing"], si["valuation_method"],
                si["cash_treatment"], si["status"])
            await conn.execute("INSERT INTO fee_assignments (org_id, fee_schedule_id, scope_type, scope_id, precedence, "
                               "effective_from) VALUES ($1, $2, 'ACCOUNT', $3, 1, $4)", ORG_A, sid, ACC["ACC_1"],
                               date(2026, 1, 1))
            fr = SEED_ROWS["public.fee_runs"][0]
            rid = await conn.fetchval(
                "INSERT INTO fee_runs (org_id, period_start, period_end, billing_frequency, run_type, status) VALUES "
                "($1, $2, $3, $4, $5, $6) RETURNING id", ORG_A, FEE_RUN["period_start"], FEE_RUN["period_end"],
                fr["billing_frequency"], fr["run_type"], fr["status"])
            fl = SEED_ROWS["public.fee_run_lines"][0]
            await conn.execute(
                "INSERT INTO fee_run_lines (org_id, fee_run_id, account_id, product_type, fee_schedule_id, "
                "billable_value, valuation_method, gross_fee, net_fee, payment_method, calc_detail) VALUES ($1, $2, $3, "
                "$4, $5, $6, $7, $8, $8, $9, '{}'::jsonb)", ORG_A, rid, ACC["ACC_1"], fl["product_type"], sid,
                FEE_RUN["billable"], fl["valuation_method"], FEE_RUN["net_fee"], fl["payment_method"])
            out["fee"] = await CE.fee_on_cash(conn, str(ORG_A), [str(ACC["ACC_1"])], date(2026, 9, 1), TODAY)
            out["rate"] = await CE.fee_rate(conn, str(ORG_A), [str(ACC["ACC_1"])], TODAY)
            raise _Rollback
    except _Rollback:
        pass
    after = int(await platform(conn, "SELECT count(*) FROM fee_runs WHERE org_id = $1", ORG_A, fetch="fetchval"))
    fee, rate = out.get("fee") or {}, out.get("rate") or {}
    want = FEE_RUN["net_fee"] * (D("60000.00") / FEE_RUN["billable"]) * D(30) / D(92)
    check(fee.get("charged") is True and fee.get("amount") is not None and cents(fee["amount"]) == cents(want) ==
          D("48.91") and len(fee.get("lines") or []) == 1,
          "INCLUDE: 750.00 × billed cash 60,000 ÷ billable 300,000 × 30 overlap days (09-01 … 09-30) ÷ the run's 92 "
          "days = 48.91 (accrued daily, charged per run)", f"{dumps(fee)}")
    check(rate.get("estimated") is True and bp4(rate["rate_pct"]) == D("1.0000"),
          "the blended yield's fee rate: 750 × 4 runs a year ÷ 300,000 = 1.0000 %", f"{dumps(rate)}")
    check(CE.billed_cash("EXCLUDE_ABOVE_PCT", D("60000"), D("300000"), D("0.05")) == D("15000.00")
          and CE.billed_cash("EXCLUDE", D("60000"), D("300000"), None) == ZERO,
          "EXCLUDE_ABOVE_PCT bills min(cash, total × pct) (15,000); EXCLUDE bills none — the engine's own rule")
    check(before == after, "the charged-path rows were ROLLED BACK: no posted fee run is left behind", f"{before} → "
                                                                                                     f"{after}")


# ── Documents ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_documents(conn):
    section("[Y] Documents: only the cash accounts' documents; Eastern-time dates; open / download via the existing route")
    c, dc = await api(U_ADV, "GET", hh_path(H1, "documents"))
    docs = {d_["id"]: d_ for d_ in dc.get("documents") or []}
    check(c == 200 and set(docs) == {str(DOC_S), str(DOC_C), str(DOC_T)},
          "H1 lists exactly the cash account's statement, the confirmation on its pending buy and its 1099 — not the "
          "equity-only account's statement, not another family's", f"{c} {sorted(docs)}")
    s = docs.get(str(DOC_S)) or {}
    check(s.get("date") == "2026-10-31" and s.get("datetime") == "2026-10-31 20:00 ET" and s.get("type") ==
          "statement" and s.get("account") == "****5001" and dc.get("timezone") == "America/New_York",
          "a statement filed at 2026-11-01 00:00 UTC shows 2026-10-31 (20:00 ET) — Eastern time, not UTC",
          f"{dumps(s)}")
    cf = docs.get(str(DOC_C)) or {}
    t = docs.get(str(DOC_T)) or {}
    check(cf.get("type") == "confirmation" and cf.get("transaction_id") == tx("T2") and cf.get("date") == "2026-11-07"
          and t.get("type") == "tax_1099" and t.get("links") == [] and t.get("file_text") == "no stored file yet",
          "the confirmation is the pending buy's; the 1099 has no stored file → no link, said so", f"{dumps(cf)} "
                                                                                                   f"{dumps(t)}")
    bad = await resolve_links(U_ADV, [{**ln, "id": d_["id"]} for d_ in docs.values() for ln in d_.get("links") or []],
                              "documents H1 (adviser)")
    dkinds = sorted({ln["kind"] for d_ in docs.values() for ln in d_.get("links") or []})
    check(dkinds == ["download"], "REPRODUCED (cash05 fix 1, verify defect): the Documents screen names its link "
          "'download' (the drawer's is 'document') — the old resolver knew only 'document' and read None; both kinds "
          "now compare the signed URL against the document's own storage key", f"{dkinds}")
    check(not bad and len(SIGNED) >= 1, "open / download goes through the existing GET /documents/{id}/download (200, "
          "a signed URL for that document's own key; no new storage)", f"{bad} {SIGNED}")
    ct, dt = await api(U_ADV, "GET", hh_path(H1, "documents", type="tax_1099"))
    check(ct == 200 and [d_["id"] for d_ in dt.get("documents") or []] == [str(DOC_T)], "type=tax_1099 narrows to the "
          "1099", f"{ct}")
    ce, de = await api(U_ADV, "GET", hh_path(H3, "documents"))
    check(ce == 200 and de.get("documents") == [] and bool(de.get("empty_text")), "an account with no documents: the "
          "empty state", f"{ce}")
    check(CD.eastern(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))[0] == date(2026, 10, 31)
          and CD.eastern(datetime(2026, 7, 1, 3, 59, tzinfo=timezone.utc))[0] == date(2026, 6, 30)
          and CD.eastern(datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc))[0] == date(2026, 7, 1),
          "Eastern time honours daylight saving (EDT −4 in July, EST −5 in November)")


# ── Member view, visibility, isolation ────────────────────────────────────────────────────────────────────────
SCREENS = ("overview", "holdings", "activity", "earnings", "documents")


async def y_visibility(conn):
    section("[Y] Member read-only with library labels; staff only visible families; org isolation both directions")
    mem = {}
    for s_ in SCREENS:
        mem[s_] = await api(U_MEM, "GET", hh_path(H1, s_))
    md = await api(U_MEM, "GET", hh_path(H1, f"activity/{tx('T2')}"))
    check(all(c == 200 and b.get("member_view") is True and (b.get("permissions") or {}).get("can_write") is False
              and (b.get("vocabularies") or {}).get("editable") == [] for c, b in [*mem.values(), md]),
          "the MEMBER reads their family's overview, holdings, activity, earnings, documents and a drawer — read-only "
          "(member_view, can_write false, editable empty)", f"{[(k, v[0]) for k, v in mem.items()]} {md[0]}")
    lab = (mem["overview"][1].get("labels") or {})
    hlab = (mem["holdings"][1].get("labels") or {})
    keys = [("overview", k) for k in (CI.TAX_KEY, "cash.coverage_estimate", "cash.yield_estimate")]
    keys.append(("holdings", "cash.yield_estimate"))
    got = {(scr, k): ((lab if scr == "overview" else hlab).get(k) or {}) for scr, k in keys}
    for (scr, k), lb in got.items():
        text, src, lib = lb.get("text"), lb.get("source"), DS.fallback_text(k)
        # the library's text: its built-in fallback when no version is in force, else the version in force (named)
        from_library = (src == "fallback" and lib is not None and text == lib) or \
                       (src not in (None, "fallback") and bool(lb.get("version_id")) and bool(lb.get("content_sha256")))
        check(isinstance(text, str) and bool(text.strip()) and lb.get("key") == k and from_library,
              f"the member's {scr} carries {k} with its disclosure-library text", f"{src} {text!r}")
    check(all(isinstance(lb.get("text"), str) and bool(lb["text"].strip()) for lb in got.values()),
          "the member's overview carries cash.coverage_estimate / cash.yield_estimate / cash.tax_estimate and holdings "
          "cash.yield_estimate from the disclosure library", f"{sorted(lab)} {sorted(hlab)}")
    check([a["key"] for a in mem["overview"][1].get("actions") or []] == ["documents"],
          "a member's only action is Documents (no policy editor, no proposed trades)")
    mkinds = sorted(ln["kind"] for ln in md[1].get("links") or [])
    check(mkinds == ["document", "position", "transaction"] and not await resolve_links(U_MEM, md[1]["links"], "drawer T2 (member)"),
          "the member's drawer offers no instruction / proposal link; every link it offers resolves for the member",
          f"{mkinds}")
    mbodies = " ".join(b for who, b in BODIES if who.startswith("mem "))
    check(CUSIP_PREFIX not in mbodies, "no CUSIP reaches a member (99C05… absent from every member body)")
    for uid, hh, label in ((U_MEM, H2, "a member, another family"), (U_RS, H1, "restricted staff, not their family"),
                           (U_B, H1, "org B, org A's household")):
        codes = [(await api(uid, "GET", hh_path(hh, s_)))[0] for s_ in SCREENS]
        codes.append((await api(uid, "GET", hh_path(hh, f"activity/{tx('T2')}")))[0])
        check(codes == [404] * 6, f"{label}: all six reads are 404 (never another family's data)", f"{codes}")
    rs = [(await api(U_RS, "GET", hh_path(H2, s_)))[0] for s_ in SCREENS]
    ca, a2 = await api(U_RS, "GET", hh_path(H2, "activity"))
    check(rs == [200] * 5 and [e["id"] for e in a2.get("entries") or []] == [tx("T_O")],
          "restricted staff read their own family (H2) — and only its entries", f"{rs} {ca}")
    cb, ab = await api(U_B, "GET", hh_path(HB, "activity"))
    check(cb == 200 and [e["id"] for e in ab.get("entries") or []] == [tx("T_B")], "org B reads its own household's "
          "activity only", f"{cb}")
    a_sees_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1", ORG_B,
                                fetch="fetchval"))
    b_sees_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1", ORG_A,
                                fetch="fetchval"))
    a_docs_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.documents WHERE org_id = $1", ORG_A,
                                fetch="fetchval"))
    a_own = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1", ORG_A,
                             fetch="fetchval"))
    b_own = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.transactions WHERE org_id = $1", ORG_B,
                             fetch="fetchval"))
    check(a_sees_b == 0 and b_sees_a == 0 and a_docs_b == 0 and a_own > 0 and b_own > 0,
          "RLS isolation BOTH directions under a non-bypassing role: A reads none of B's transactions, B none of A's "
          "(nor A's documents); each reads its own", f"A→B {a_sees_b} B→A {b_sees_a} docs {a_docs_b} own {a_own}/{b_own}")
    allb = "\n".join(b for _w, b in BODIES)
    leaks = [n for n in FULL_NUMBERS.values() if n in allb] + [h for h in (f"{TAG}-hash-",) if h in allb]
    check(bool(BODIES) and not leaks, f"no full account number (seeded in custodian_account_id) and no account hash in "
          f"ANY of the {len(BODIES)} cash05 responses — last four only", f"{leaks}")
    # CASH07 (2026-11-09): the sweep-rate disclosure is now shown on a MEMBER's overview (and nowhere else) on purpose
    # cash07 fix 1: the member ALSO requests another family's overview (H2) — a 404 that rightly carries no label. The
    # check counted it as a "member overview" and required the key in it. Own family (H1) = must carry; every other
    # response (staff, the member's other screens, the member's 404 on another family) = must not.
    mem_pfx = f"{USERS[U_MEM][2]} GET {hh_path(H1, 'overview').split('?')[0]}"
    mem_ov = [(w, b) for w, b in BODIES if w.startswith(mem_pfx)]
    others = [(w, b) for w, b in BODIES if not w.startswith(mem_pfx)]
    leak = [w for w, b in others if "cash.sweep_rate" in b]
    missing = [w for w, b in mem_ov if "cash.sweep_rate" not in b]
    mem_other_ov = [w for w, _b in others if w.startswith(f"{USERS[U_MEM][2]} ") and "/overview" in w]
    reg = DS.fallback_text("cash.sweep_rate") is not None and "cash.sweep_rate" in DS.KEYS
    print(f"[INFO] sweep_rate: own-family member overviews {[w for w, _b in mem_ov]}; the member's other-family "
          f"overviews (not counted as theirs) {mem_other_ov}; {len(others)} other responses")
    check(bool(reg and mem_ov and not missing and not leak),
          "cash.sweep_rate is registered with its fallback and shown ONLY on a member's overview (CASH07), in no other "
          "cash05 response", f"registered={reg} own-family member overviews={len(mem_ov)} missing={missing[:3]} "
          f"leaks={leak[:3]}")
    check(bool(mem_other_ov) and all(w not in leak for w in mem_other_ov),
          "REPRODUCED (cash07 fix 1): the member's 404 on ANOTHER family's overview is a second '/overview' body for the "
          "member — it carries no sweep-rate label (and is no longer required to)", f"{mem_other_ov}")


async def y_no_writes(conn, before: dict):
    section("[Y] The reads write nothing (every touched table at its seeded count, by fixture ids)")
    after = await counts(conn)
    diff = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
    check(not diff, "after every cash05 GET of the run, each touched table holds exactly its seeded fixture count "
          "(audit_log / live-written tables counted BY FIXTURE IDS ONLY)", f"{diff}")


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
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) >= 8 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the cash05 render tests ({NODE_TEST}, counted by their file: overview, yields + coverage, holdings + rungs, "
          f"activity + drawer, earnings, documents, member read-only + fail closed, the Treasury tab) all pass "
          f"({n.group(1) if n else '?'})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


#: the nested regression, ONCE, flat, after teardown: cash04 (the Treasury envelope + proposals this sprint sits beside)
REGRESSIONS = ("verify_cash04.py",)


def regression():
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    for script in REGRESSIONS:
        section(f"[Y] Regression — {script} ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=2700, env=env)
        t = VT.nested_total(out, code)
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
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


FIXTURE_SECTIONS = (("overview", y_overview), ("holdings", y_holdings), ("activity", y_activity),
                    ("drawer", y_drawer), ("earnings", y_earnings), ("fee charged (rolled back)", y_fee_charged),
                    ("documents", y_documents), ("member + visibility + isolation", y_visibility))


ONLY_NAMES = {"overview": "overview", "holdings": "holdings", "activity": "activity", "drawer": "drawer",
              "earnings": "earnings", "fee": "fee charged (rolled back)", "documents": "documents",
              "visibility": "member + visibility + isolation"}
ONLY_NEEDS = {"holdings": ("overview",)}


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
    names |= {ONLY_NAMES[n] for w in want for n in ONLY_NEEDS.get(w, ())}
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
            seeded = await counts(conn)
            for name, fn in chosen:
                await run(name, fn, conn)
            await run("no writes", y_no_writes, conn, seeded)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
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
            check(left == 0, "zero fixture rows remain (positions, transactions, documents, links, fee rows, "
                  "proposal, instruction, events, policies, obligations, audit rows, securities, …)", f"{left}")
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
        print("[SKIP] regression — --only one-off run")
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
