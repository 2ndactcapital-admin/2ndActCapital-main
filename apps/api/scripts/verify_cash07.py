"""verify_cash07 — CASH07: PAYMENT INSTRUCTIONS (stage A, manual) between a family's OWN accounts — the table + event
log on the execution-instruction governance spine, the S30 FLOOR (no self-approval, ever), both authority checks, rails
by custodian capability, business-day value dates, the manual adapter's payment sheet (last four only) and the
not-connected adapters, reconciliation against the custodian feed (or manual by a different person), ACH provisional
settlement and returns, the unmatched-settlement task, the disclosure drafts, the Treasury → Payments screens.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash07.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx transport
refuses and records (asserted empty at the end) and the AI helpers refuse and record.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile call.
REAL: the app through an ASGI transport, services.payments / payment_adapters / sod / human_tasks / trading_authority /
authority / custodian_capabilities / bulk_approvals / disclosures, the DB triggers, RLS and the live database. The
script's own connection does not bypass RLS (rolbypassrls = false, asserted). TIME IS INJECTED: "today" = 2026-11-09
(a Monday) through ``services.payments.set_clock``; the ACH window and the unmatched sweep move the clock explicitly.

FIXTURES (prefix 99000000-0000-0000-0000-0000cac7…): orgs A (VISIBILITY1 enforced) and B, is_test = true (asserted).
Users with real user_roles grants (the app's decision on each asserted): U_MK / U_CK advisors (initiate + approve),
U_CK2 compliance (approve only), U_INQ support staff (initiate; trading tier INQUIRY), U_SA Super Admin, U_RS fund
finance restricted to H2, U_VIEW view_portfolio only, U_MEM a member of H1; org B U_B (the ONLY approver of org B).
H1: E1 owns A1 ****7001 (source) and A2 ****7002 (destination), A5 ****7005 (held away → read_only), A6 ****7006 at
custodian cash07verifyx (org A enables that custodian's direct adapter — none is connected); E1B owns A3 ****7003 (same
household, another owner). H2: E2 owns A4 ****7004 and A8 ****7008. Org B: AB1 / AB2. Custodian cash07verify declares
ACH and journal (wire 'no', check 'unknown'); cash07verifyx declares ACH. A cash asset with positions on A1 / A2 and
four feed transactions dated "today": the matching OUT (sell 25,000.00 on A1) and IN (buy on A2), a wrong amount and a
wrong direction. Full account numbers sit in accounts.custodian_account_id ONLY to prove they never leave.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with the payments / execution / sod teardown
GUCs (every guard a touched table reads: payment_instructions + payment_events → payments.teardown,
custodian_capabilities → execution.teardown, sod_rules / sod_decisions → sod.teardown), keyed by this run's fixture
ids; tables without org_id (custodian_capabilities, user_roles, role_permissions, household_memberships) passed in the
plan by their fixture keys. Every touched table re-read for its exact pre-test count BY FIXTURE IDS ONLY. Real rows
(payments, capabilities, sod rules, grants, accounts, transactions — the Olivo demo's included) fingerprinted before
and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import base64
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
import zlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash07"
BEFORE_REF = "0b2c427"   # cash07-work's base: 'merge cash05'
_R = ("any(view_portfolio|initiate_payment|approve_payment); member scope (own family / published deals; staff "
      "unscoped)")
_I = f"authz initiate_payment; {_R}"
_A = f"authz approve_payment; {_R}"
_HH = "/api/v1/cash/households/{household_id}/payments"
ROUTES = {("GET", _HH): _R, ("POST", _HH): _I, ("GET", _HH + "/options"): _I, ("GET", _HH + "/{payment_id}"): _R,
          ("GET", _HH + "/{payment_id}/matches"): _I, ("POST", _HH + "/{payment_id}/sheet"): _I,
          ("POST", _HH + "/{payment_id}/approve"): _A, ("POST", _HH + "/{payment_id}/reject"): _A,
          ("POST", _HH + "/{payment_id}/{action}"): _I}
#: existing routes cash07 changed ON PURPOSE (bulk approve gained the payment type) — pinned exactly
_R0 = "any(view_authority|approve_authority|view_held_away|approve_held_away|manage_cash_policy)"
_W0 = "any(approve_authority|approve_held_away|manage_cash_policy)"
WIDENED = {
    ("GET", "/api/v1/approvals/bulk"): (_R0, _R0[:-1] + "|initiate_payment|approve_payment)"),
    ("GET", "/api/v1/approvals/summary"): (_R0, _R0[:-1] + "|initiate_payment|approve_payment)"),
    ("POST", "/api/v1/approvals/bulk"): (_W0, _W0[:-1] + "|approve_payment)"),
}
SERVICES = ("apps/api/services/payments.py", "apps/api/services/payment_adapters.py")
OWN_FILES = SERVICES + ("apps/api/routers/payments.py", "apps/api/services/bitemporal.py")
NODE_TEST = "tests/cash07/cash07.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH07_NOTES.md"
RUNBOOK = REPO / "docs" / "cash" / "CASH_PAYMENTS.md"
MIGRATION = REPO / "docs" / "cash07_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash07's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    bad_w = [(k, (o.before.get(kk) or {}).get("after"), (o.end.get(kk) or {}).get("after"))
             for kk in o.changed for k in flat([kk])
             if k not in WIDENED or (o.before[kk].get("after"), o.end[kk].get("after")) != WIDENED[k]]
    check(bool(len(o.before) > 0 and changed == set(WIDENED) and not bad_w and not o.removed),
          "cash07 changed exactly the three approvals routes' gates (the payment bulk type, pinned) and removed none",
          f"{o.where()}; changed={sorted(changed)} bad={bad_w[:3]} removed={o.removed[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash07's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash07's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    ps = _code_only(_src("apps/api/services/payments.py"))
    check("archive_live_row(" in ps and "jsonb_populate_record" not in ps,
          "payments archives through the SHARED services.bitemporal helper (no copy of the archive SQL)")
    check("TA.assert_can_propose(" in ps and "AU.payment_refusal(" in ps and "trading_authority_grants" not in ps,
          "both authority checks go through their own services (trading_authority, authority) — never re-read here")
    check("sod.gate(" in ps and ps.count("site=\"payments.") >= 3, "the floor is decided by services.sod at three sites")
    check("CAP.available(" in ps and "BD.is_business_day(" in ps and "CA.TYPE_MAP" in ps,
          "rails through custodian_capabilities.available, dates through us_business_days, the feed direction through "
          "cash_activity.TYPE_MAP (one sign table)")
    check("custodian_account_id" not in ps and "account_number_hash" not in ps,
          "services/payments.py never reads custodian_account_id or account_number_hash (last four only)")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm|socket)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    wa = _src("apps/api/services/workflow_autonomy.py")
    check(all(f'"cash.payment_{v}": {{"class": NEVER, "money": True' in wa for v in ("draft", "submit", "approve",
                                                                                     "send")),
          "workflow_autonomy: the four payment verbs are declared NEVER (money) — the assistant drafts at most")
    sd = _src("apps/api/services/sod.py")
    check('"cash.payment_draft": "propose"' in sd and 'PAYMENT_INSTRUCTION = "payment_instruction"' in sd
          and '"payment_instruction_approval": PAYMENT_INSTRUCTION' in sd,
          "services.sod: the item type, its task kind and the draft verb's 'propose' duty")
    mig = MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists() else ""
    for marker in ("payments_teardown_allowed", "payment_instructions_maker_checker_chk", "payment_events_human_chk",
                   "payment_instructions_same_owner", "payment_instructions_guard", "'transfer'",
                   "third-party payments come later", "_org_delete"):
        check(marker in mig, f"docs/cash07_part1.sql carries {marker!r}")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec_ in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
        check(sec_ in notes, f"docs/cash/CASH07_NOTES.md has section {sec_}")
    rb = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.exists() else ""
    check("What waits for a second checker at 2nd Act" in rb and "PAYMENT_SWEEP_ENABLED" in rb
          and "never loaded for the demo family" in rb, "docs/cash/CASH_PAYMENTS.md: the runbook (sheet, confirmations, "
                                                        "reconcile, the second checker, the switched-off sweep)")
    for rel in ("scripts/regression_set.txt",):
        check("verify_cash07.py" in _src(rel), f"verify_cash07.py is in {rel}")
    st = _src("docs/PROJECT_STATUS.md")
    check("cash07" in st.lower() and "payment_instruction" in _src("docs/SEGREGATION_OF_DUTIES.md"),
          "PROJECT_STATUS (cash07) and SEGREGATION_OF_DUTIES (the floor item type) are updated")
    td = _src("docs/OUTSTANDING_TODO_LIST.md")
    check("cash.payments" in td and "second" in td.lower() and "approve_payment" in td,
          "OUTSTANDING_TODO_LIST: publish the two disclosure drafts; a second eligible money-movement checker")
    code, out, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "docs/*.sql", args=("--name-only",))
    migs = [f for f in out.split() if f != "docs/schema_snapshot.sql"]      # the snapshot is a read-back, not a migration
    check(code == 0 and migs == ["docs/cash07_part1.sql"] and "docs/schema_snapshot.sql" in out.split(),
          "cash07's own diff adds exactly one migration (docs/cash07_part1.sql) and refreshes the schema snapshot",
          out.strip()[:200])


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
import load_disclosure_defaults as LDD  # noqa: E402

import main  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import custodian_capabilities as CAPS  # noqa: E402
from services import disclosures as DS  # noqa: E402
from services import execution_letter as EL  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import payment_adapters as PA  # noqa: E402
from services import payments as PS  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import trading_authority as TA  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cash07verify"
NAME = "CASH07 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac7"
TODAY = date(2026, 11, 9)           # a Monday
HOLIDAY = date(2026, 11, 26)        # Thanksgiving
SATURDAY = date(2026, 11, 14)
TEARDOWN_GUCS = ("payments.teardown", "execution.teardown", "sod.teardown")
CUSTODIAN = "cash07verify"
CUSTODIAN_X = "cash07verifyx"
D = Decimal
AMOUNT = "25000.00"


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_MK, U_CK, U_CK2, U_INQ, U_SA, U_MEM, U_RS, U_VIEW, U_B = (_id(10 + i) for i in range(9))
H1, H2, HB = _id(30), _id(31), _id(32)
E1, E1B, E2, EB = _id(40), _id(41), _id(42), _id(43)
A = {"A1": _id(70), "A2": _id(71), "A3": _id(72), "A4": _id(73), "A5": _id(74), "A6": _id(75), "A8": _id(76),
     "AB1": _id(77), "AB2": _id(78)}
FULL_NUMBERS = {k: f"99007700{n:04d}" for n, k in enumerate(A, start=1)}
PERMS = {
    "advisor": {"view_portfolio", "view_members", "initiate_payment", "approve_payment"},
    "compliance": {"view_portfolio", "approve_payment"},
    "support_staff": {"view_portfolio", "view_members", "initiate_payment"},
    "fund_finance": {"view_portfolio", "view_members", "initiate_payment", "approve_payment"},
    "ir_member_relations": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "super_admin": set(),
}
# uid → (org, users.role mirror, short, role granted)
USERS = {
    U_MK: (ORG_A, "member", "mk", "advisor"), U_CK: (ORG_A, "member", "ck", "advisor"),
    U_CK2: (ORG_A, "member", "ck2", "compliance"), U_INQ: (ORG_A, "member", "inq", "support_staff"),
    U_SA: (ORG_A, "super_admin", "sa", "super_admin"), U_MEM: (ORG_A, "member", "mem", "member"),
    U_RS: (ORG_A, "member", "rs", "fund_finance"), U_VIEW: (ORG_A, "member", "view", "ir_member_relations"),
    U_B: (ORG_B, "member", "b", "advisor"),
}
ALL_USERS = list(USERS)
ENTS = {E1: (ORG_A, "Treasury Trust"), E1B: (ORG_A, "Spouse"), E2: (ORG_A, "Other Family"), EB: (ORG_B, "Org B")}
HOUSEHOLDS = {H1: (ORG_A, "Treasury", (E1, E1B)), H2: (ORG_A, "Other", (E2,)), HB: (ORG_B, "B", (EB,))}
# key → (org, household, entity, custodian, masked, held_away)
ACCOUNTS = {"A1": (ORG_A, H1, E1, CUSTODIAN, "****7001", False), "A2": (ORG_A, H1, E1, CUSTODIAN, "****7002", False),
            "A3": (ORG_A, H1, E1B, CUSTODIAN, "****7003", False), "A4": (ORG_A, H2, E2, CUSTODIAN, "****7004", False),
            "A5": (ORG_A, H1, E1, CUSTODIAN, "****7005", True), "A6": (ORG_A, H1, E1, CUSTODIAN_X, "****7006", False),
            "A8": (ORG_A, H2, E2, CUSTODIAN, "****7008", False), "AB1": (ORG_B, HB, EB, CUSTODIAN, "****7011", False),
            "AB2": (ORG_B, HB, EB, CUSTODIAN, "****7012", False)}
# (user, entity, tier)
GRANTS = [(U_MK, E1, "limited"), (U_CK, E1, "limited"), (U_CK2, E1, "limited"), (U_INQ, E1, "inquiry"),
          (U_SA, E1, "limited"), (U_RS, E2, "limited"), (U_MK, E2, "limited"), (U_CK, E2, "limited"),
          (U_B, EB, "limited")]
# (custodian, key, value)
CAP_ROWS = [(CUSTODIAN, "ach", "yes"), (CUSTODIAN, "journal", "yes"), (CUSTODIAN, "wire", "no"),
            (CUSTODIAN, "check", "unknown"), (CUSTODIAN_X, "ach", "yes")]
# the feed: key → (account, code, amount)
TXS = {"T_OUT": ("A1", "sell", AMOUNT), "T_IN": ("A2", "buy", AMOUNT), "T_WRONG": ("A1", "sell", "24999.99"),
       "T_DIR": ("A1", "buy", AMOUNT)}
ORG_POLICIES = [("agent_proposal", "when_no_other_checker", False), ("payment_instruction", "when_no_other_checker", False)]

SEED_CHECK_LITERALS = (
    ("public.payment_instructions", "payment_instructions_rail_chk", ("ach", "wire", "journal", "check")),
    ("public.payment_instructions", "payment_instructions_status_chk",
     ("draft", "pending_approval", "approved", "sent", "awaiting_client_consent", "acknowledged", "settled",
      "reconciled", "closed", "rejected", "returned", "cancelled", "expired")),
    ("public.payment_instructions", "payment_instructions_currency_chk", ("USD",)),
    ("public.payment_events", "payment_events_action_chk",
     ("create", "submit", "approve", "mark_sent", "settle", "reconcile", "sheet", "unmatched_alert", "expire", "return")),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("ach", "wire", "journal", "check")),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes", "no", "unknown")),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("document",)),
    ("public.trading_authority_grants", "trading_authority_grants_authority_tier_check", ("inquiry", "limited")),
    ("public.sod_rules", "sod_rules_kind_chk", ("policy",)),
    ("public.sod_rules", "sod_rules_selfapproval_chk", ("when_no_other_checker",)),
    ("portfolio.assets", "assets_basis_chk", ("units",)),
)
SEED_ROWS = {
    "public.custodian_capabilities": [
        {"custodian_code": c, "capability_key": k, "value": v, "evidence_kind": "document",
         "evidence": "verify_cash07 fixture", "as_of": TODAY.isoformat(), "version_no": 1, "owner_scope": "platform",
         "org_id": None} for c, k, v in CAP_ROWS],
    "public.trading_authority_grants": [{"org_id": str(ORG_A), "entity_id": str(e), "user_id": str(u),
                                         "authority_tier": t} for u, e, t in GRANTS],
    "public.sod_rules": [{"rule_key": f"policy:{it}", "owner_scope": "org", "org_id": str(ORG_A), "rule_kind": "policy",
                          "item_type": it, "scope": "same_item", "severity": "block", "self_approval": sa,
                          "enforce_floor": ef, "description": "verify_cash07: an org that turned self-approval on",
                          "status": "active", "version_no": 1, "retires": False} for it, sa, ef in ORG_POLICIES],
    "public.payment_instructions": [{
        "org_id": str(ORG_A), "household_id": str(H1), "source_account_id": str(A["A1"]),
        "destination_account_id": str(A["A2"]), "amount": AMOUNT, "currency_code": "USD", "rail": "ach",
        "value_date": TODAY.isoformat(), "purpose": "verify", "status": "draft", "maker_id": str(U_MK),
        "maker_ids": [str(U_MK)], "reconciled_transaction_ids": []}],
}
SEED_NON_COLUMN = {"app.is_super_admin", "true", "1 day", "active", "staff_visibility.enforced", "general", "manual",
                   "units", "cash", "taxonomy_mc_8_1", "USD", "trust", "taxable", "view_only", "Lead adviser",
                   "individual", "application/json"}
UNIQUES = ("assets_cash_active_uniq", "trading_authority_grants_entity_id_user_id_key", "custodian_capabilities_live_uq",
           "payment_instructions_task_live_uq", "sod_rules_active_uq", "accounts_active_identity_uq",
           "org_settings_org_id_setting_key_key")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "delegate_grants": ("org_id", "principal_entity_id", "delegate_user_id", "scope", "effective_from"),
    "staff_assignments": ("org_id", "entity_id", "assigned_to_user_id", "role_label"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "custodian_account_id",
                 "registration_type", "tax_status", "primary_entity_id", "household_id", "is_discretionary",
                 "is_held_away"),
    "portfolio.assets": ("id", "org_id", "name", "asset_type", "default_taxonomy_key", "currency_code"),
    "portfolio.positions": ("id", "org_id", "owner_entity_id", "asset_id", "as_of_date", "ownership_basis", "quantity",
                            "market_value", "authority", "source_system", "account_id"),
    "portfolio.transactions": ("id", "org_id", "position_id", "transaction_type_code", "trade_date", "settle_date",
                               "gross_amount", "net_amount", "authority", "source_system", "external_ref"),
    "trading_authority_grants": ("org_id", "entity_id", "user_id", "authority_tier"),
    "custodian_capabilities": ("custodian_code", "capability_key", "value", "evidence_kind", "evidence", "as_of",
                               "version_no"),
    "sod_rules": ("rule_key", "owner_scope", "org_id", "rule_kind", "item_type", "scope", "severity", "self_approval",
                  "enforce_floor", "description", "status", "version_no"),
    "payment_instructions": ("org_id", "household_id", "source_account_id", "destination_account_id", "amount", "rail",
                             "value_date", "purpose", "status", "maker_id", "maker_ids"),
    "payment_events": ("org_id", "payment_id", "action", "from_status", "to_status", "actor_id", "detail"),
}
COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "public.accounts", "portfolio.assets", "portfolio.positions",
    "portfolio.transactions", "public.trading_authority_grants", "public.custodian_capabilities", "public.sod_rules",
    "public.sod_decisions", "public.payment_instructions", "public.payment_events", "public.audit_log",
    "public.member_todos", "public.workflow_runs", "public.workflow_run_steps", "public.domain_events",
)

S: dict = {"pay": {}, "tx": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
BODIES: list[tuple[str, str]] = []          # (who / path, full body) of every payments response — scanned for leaks
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""),
          flush=True)
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


def leaks_in(text: str) -> list[str]:
    """Any full account number, or any account hash, inside ``text``."""
    return [n for n in FULL_NUMBERS.values() if n in text] + ([f"{TAG}-hash"] if f"{TAG}-hash" in text else [])


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


class Rollback(Exception):
    pass


async def in_rolled_back(conn, org, fn):
    """Run ``fn(conn)`` in a transaction scoped to ``org`` that is ALWAYS rolled back; returns (result, error)."""
    out = {"res": None, "err": None}
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true)",
                str(org))
            try:
                async with conn.transaction():     # a savepoint: the statement's own error is caught here
                    out["res"] = await fn(conn)
            except Exception as exc:  # noqa: BLE001
                out["err"] = exc
            raise Rollback()
    except Rollback:
        pass
    return out["res"], out["err"]


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
        "public.custodian_capabilities": f"custodian_code IN ('{CUSTODIAN}', '{CUSTODIAN_X}')",
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
            ("payment_instructions", f"SELECT id, status, system_to FROM public.payment_instructions WHERE {nf}"),
            ("payment_events", f"SELECT id FROM public.payment_events WHERE {nf}"),
            ("trading_authority_grants", f"SELECT id, authority_tier FROM public.trading_authority_grants WHERE {nf}"),
            ("sod_rules (org)", f"SELECT id, status, valid_to FROM public.sod_rules WHERE org_id IS NOT NULL AND {nf}"),
            ("accounts", f"SELECT id, valid_to, system_to FROM public.accounts WHERE {nf}"),
            ("transactions", f"SELECT id, net_amount, valid_to, system_to FROM portfolio.transactions WHERE {nf}"),
            ("org_settings", f"SELECT org_id, setting_key, setting_value FROM public.org_settings WHERE {nf}")):
        out[f"{label} (real, the Olivo demo's included)"] = await fingerprint(conn, sql, orgs)
    out["custodian_capabilities (real)"] = await fingerprint(
        conn, "SELECT id, value, valid_to FROM public.custodian_capabilities WHERE custodian_code <> ALL($1::text[])",
        [CUSTODIAN, CUSTODIAN_X])
    return out


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        users, orgs, hhs = list(ALL_USERS), list(FIXTURE_ORGS), list(HOUSEHOLDS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [hhs]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            # platform rows without org_id, by their fixture custodian codes (asserted to match no real row)
            "public.custodian_capabilities": ("custodian_code = ANY($1::text[])", [[CUSTODIAN, CUSTODIAN_X]]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cash07")
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
              "public.custodian_capabilities"):
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


async def api_raw(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash07"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b"", ""
        except Exception as exc:  # noqa: BLE001 — the app raised: a 500, with its traceback
            print(f"[INFO] {method} {path} raised in the app (a 500):")
            traceback.print_exc()
            return 500, f"{type(exc).__name__}: {exc}"[:2000].encode(), "text/plain"
    return res.status_code, res.content, res.headers.get("content-type", "")


async def api(uid, method, path, body=None, timeout=300.0):
    code, raw_b, _ctype = await api_raw(uid, method, path, body, timeout)
    raw = raw_b.decode("utf-8", "replace")
    if "/payments" in path or "/approvals/" in path or "/overview" in path:
        BODIES.append((f"{USERS[uid][2]} {method} {path}", raw))
    try:
        return code, json.loads(raw or "{}")
    except ValueError:
        return code, {"raw": raw[:4000]}


def pp(hh, tail: str = "") -> str:
    return f"/api/v1/cash/households/{hh}/payments" + tail


def pay_of(b: dict) -> dict:
    return (b or {}).get("payment") or {}


def body(src="A1", dst="A2", rail="ach", amount=AMOUNT, vdate=TODAY, purpose=None, **extra) -> dict:
    """A VALID draft body (only the field a refusal check names is changed, so only the gate can refuse)."""
    return {"source_account_id": str(A[src]), "destination_account_id": str(A[dst]), "amount": amount, "rail": rail,
            "value_date": vdate.isoformat(), "purpose": purpose or f"{NAME}fund the {src} obligation", **extra}


async def events(conn, pid) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT action, from_status, to_status, actor_id FROM public.payment_events WHERE payment_id = $1::uuid "
              "ORDER BY created_at, id", pid)]


async def live(conn, pid) -> dict:
    return dict(await platform(conn, "SELECT * FROM public.payment_instructions WHERE id = $1::uuid AND system_to IS NULL",
                               pid, fetch="fetchrow"))


async def refusals(conn, pid) -> int:
    return int(await platform(conn, "SELECT count(*) FROM public.sod_decisions WHERE item_id = $1 AND outcome = "
                                    "'refused' AND item_type = 'payment_instruction'", str(pid), fetch="fetchval"))


def detail_text(b) -> str:
    d = (b or {}).get("detail")
    return dumps(d) if not isinstance(d, str) else d


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
        raise AssertionError(f"verify_cash07: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash07: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    have = list(inspect.signature(PS.set_clock).parameters)
    if have != ["fn"]:
        raise RuntimeError(f"services.payments.set_clock signature drifted: {have}")
    PS.set_clock(lambda: TODAY)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes, the reuse inventory, shared helpers preferred over copies")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for marker in ("execution_instructions_guard", "payment_instructions_guard", "services.bitemporal",
                   "archive_live_row", "sod.gate", "human_tasks", "assert_can_propose", "payment_refusal",
                   "custodian_capabilities", "payment_status_feed", "TYPE_MAP", "external_ref",
                   "FALLBACK_SOURCES", "load_disclosure_defaults", "Reuse inventory", "floor_class",
                   "Context.policy", "third-party payments come later"):
        check(marker.lower() in notes.lower(), f"the notes name {marker!r}")
    for f in ("[FIND]** There are **0** `trading_authority_grants`", "[FIND]** No custodian declares a payment rail",
              "[FIND]** The transaction feed has **no transfer type**", "[FIND]** `verify_s30.py` was 1 FAIL"):
        check(f.replace("**", "") in notes.replace("**", ""), f"the notes record the finding {f[:60]!r}")
    if not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md / ADDENDUM v1.1 are still not in the repo — nothing was invented from them")
    live_grants = int(await platform(conn, "SELECT count(*) FROM public.trading_authority_grants WHERE NOT (org_id = "
                                           "ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval"))
    if live_grants == 0:
        find("there are still 0 real trading_authority_grants — no one can initiate a real payment until an operator "
             "grants 'limited' (docs/cash/CASH_PAYMENTS.md)")
    else:
        find(f"{live_grants} real trading_authority_grants rows exist (operator step taken)")


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
    check(bool(got == len(SEED_CHECK_LITERALS) and got > 0 and not bad),
          f"{got} CHECK constraints read; every literal the seeds and the lifecycle use is one the database allows",
          f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(bool(n_chk > 0 and not refused), f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} raw-seeded tables "
          "evaluated by the database against the exact row shapes — none refuses", f"{refused}")
    keep = SEED_ROWS["public.payment_instructions"]
    SEED_ROWS["public.payment_instructions"] = [dict(keep[0], rail="swift"), dict(keep[0], approved_by=str(U_MK))]
    try:
        _n, bad2 = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["public.payment_instructions"] = keep
    want = {("public.payment_instructions", "payment_instructions_rail_chk", 0),
            ("public.payment_instructions", "payment_instructions_maker_checker_chk", 1)}
    check(want <= set(bad2), "the pre-check is not vacuous: an unknown rail and an approver who is a maker are each "
          "refused by the same evaluation", f"{bad2}")
    lits = seed_literals()
    vals = {str(v) for rows in SEED_ROWS.values() for r in rows for v in r.values() if v is not None}
    vals |= {x for _t, _n2, ls in SEED_CHECK_LITERALS for x in ls} | {CUSTODIAN, CUSTODIAN_X, TAG}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(lits) and not undeclared, f"every quoted literal in seed()'s source ({len(lits)}) is a declared row "
          "value or a checked CHECK literal", f"{undeclared}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    cons = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype = 'u' AND "
                                                       "conname = ANY($1::text[])", list(UNIQUES))}
    have = set(idx) | cons
    check(bool(have == set(UNIQUES) and len(have) > 0 and all("UNIQUE" in v for v in idx.values())),
          f"{len(have)} unique indexes / constraints read (one cash asset per org, one grant per entity and user, one "
          "live capability, one live payment per approval task, …) — the seed respects each",
          f"missing={sorted(set(UNIQUES) - have)}")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY(ARRAY["
              "'public.payment_instructions', 'public.payment_events']::regclass[])")]
    check(len(fks) >= 10, "the new tables' foreign keys are read (accounts, household, makers, approver, task, archive, "
          "the event's payment)", f"{len(fks)}")
    ops = {t: {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                      "tablename = $1", t)}
           for t in ("payment_instructions", "payment_events")}
    check(all(v == {"SELECT", "INSERT", "UPDATE", "DELETE"} for v in ops.values()),
          "RLS: a policy for EVERY operation on payment_instructions and payment_events", f"{ops}")
    trg = {r["tgname"] for r in await platform(
        conn, "SELECT tgname FROM pg_trigger WHERE tgrelid = ANY(ARRAY['public.payment_instructions', "
              "'public.payment_events']::regclass[]) AND NOT tgisinternal")}
    check({"payment_instructions_guard_trg", "payment_instructions_same_owner_trg", "payment_instructions_event_trg",
           "payment_events_immutable_trg"} <= trg, "the transition, same-owner, event and append-only triggers exist",
          f"{sorted(trg)}")
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
    check(bool(SEED_COLUMNS) and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables) exist in "
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
    a = int(await platform(conn, "SELECT count(*) FROM accounts WHERE custodian_code = ANY($1::text[]) OR "
                                 "custodian_account_id = ANY($2::text[])", [CUSTODIAN, CUSTODIAN_X],
                           list(FULL_NUMBERS.values()), fetch="fetchval"))
    c = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = ANY($1::text[])",
                           [CUSTODIAN, CUSTODIAN_X], fetch="fetchval"))
    return check(o == 0 and u == 0 and e == 0 and a == 0 and c == 0,
                 "the reserved prefixes (fixture ids, sign-in tag, 'CASH07 VERIFY' names, custodian codes, full "
                 "numbers) match no real row", f"orgs={o} users={u} entities={e} accounts={a} capabilities={c}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH07 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH07 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH07 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, (org, nm) in ENTS.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) VALUES "
                               "($1, $2, $3, 'active', 'individual'::entity_type)", eid, org, NAME + nm)
        for hh, (org, nm, members) in HOUSEHOLDS.items():
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + nm + " family")
            for e in members:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day')",
                           ORG_A, E1, U_MEM)
        for uid in (U_MK, U_CK, U_CK2, U_INQ, U_VIEW):
            for e in (E1, E1B, E2):
                await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                                   "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, e, uid)
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                           "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, E2, U_RS)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'staff_visibility.enforced', 'true'::jsonb, 'general')", ORG_A)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, $2, $3::jsonb, 'general')", ORG_A, PA.ORG_SETTING, json.dumps([CUSTODIAN_X]))
        for key, (org, hh, ent, cust, masked, held) in ACCOUNTS.items():
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "custodian_account_id, registration_type, tax_status, primary_entity_id, household_id, "
                "is_discretionary, is_held_away) VALUES ($1, $2, $3, $4, $5, $6, 'trust', 'taxable', $7, $8, true, $9)",
                A[key], org, masked, f"{TAG}-hash-{key}", cust, FULL_NUMBERS[key], ent, hh, held)
        for row in SEED_ROWS["public.custodian_capabilities"]:
            await conn.execute(
                "INSERT INTO custodian_capabilities (custodian_code, capability_key, value, evidence_kind, evidence, "
                "as_of, version_no) VALUES ($1, $2, $3, $4, $5, $6, $7)", row["custodian_code"], row["capability_key"],
                row["value"], row["evidence_kind"], row["evidence"], TODAY, row["version_no"])
        for u, e, t in GRANTS:
            await conn.execute("INSERT INTO trading_authority_grants (org_id, entity_id, user_id, authority_tier) "
                               "VALUES ($1, $2, $3, $4)", USERS[u][0], e, u, t)
        for row in SEED_ROWS["public.sod_rules"]:
            await conn.execute(
                "INSERT INTO sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, severity, "
                "self_approval, enforce_floor, description, status, version_no) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, "
                "$9, $10, $11, $12)", row["rule_key"], row["owner_scope"], ORG_A, row["rule_kind"], row["item_type"],
                row["scope"], row["severity"], row["self_approval"], row["enforce_floor"], row["description"],
                row["status"], row["version_no"])
        aid = uuid.uuid4()
        await conn.execute("INSERT INTO portfolio.assets (id, org_id, name, asset_type, default_taxonomy_key, "
                           "currency_code) VALUES ($1, $2, $3, 'cash', 'taxonomy_mc_8_1', 'USD')", aid, ORG_A,
                           NAME + "Cash")
        pos = {}
        for key in ("A1", "A2"):
            pos[key] = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "quantity, market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, 'units', "
                "$6, $6, 'manual', 'manual', $7)", pos[key], ORG_A, E1, aid, TODAY - timedelta(days=30),
                D("100000.00"), A[key])
        for key, (acct, code, amt) in TXS.items():
            tid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.transactions (id, org_id, position_id, transaction_type_code, trade_date, "
                "settle_date, gross_amount, net_amount, authority, source_system, external_ref) VALUES ($1, $2, $3, $4, "
                "$5, $5, $6, $6, 'manual', 'manual', $7)", tid, ORG_A, pos[acct], code, TODAY, D(amt), f"{TAG}-{key}")
            S["tx"][key] = str(tid)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    ok_test = check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true "
                    "(privileged insert path)", f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/{len(FIXTURE_ORGS)} orgs")
    ok_roles = await check_fixture_roles(conn)
    S["seeded"] = bool(ok_test and ok_roles and len(S["tx"]) == len(TXS))


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
            ok = rname in names and pr is not None and pr.admitted
            if rname == "super_admin":
                ok = ok and pr.is_super_admin
            else:
                ok = ok and not pr.is_super_admin and not pr.is_staff and PERMS[rname] <= perms
                ok = ok and bool(pr.is_internal) is (rname != "member")
                ok = ok and ("initiate_payment" in perms) is (rname in ("advisor", "support_staff", "fund_finance"))
                ok = ok and ("approve_payment" in perms) is (rname in ("advisor", "compliance", "fund_finance"))
            if not ok:
                bad.append((short, names, sorted(perms & {"initiate_payment", "approve_payment", "view_portfolio"}),
                            bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisors initiate + "
                 "approve; compliance approves only; support staff initiates only; the member, the viewer and the "
                 "restricted staff as named; one Super Admin; no staff bypass)", f"{bad}")


# ── helpers that drive one payment through the API ──────────────────────────────────────────────────────────────
async def drafted(uid=U_MK, hh=H1, **kw) -> tuple[int, dict]:
    return await api(uid, "POST", pp(hh), body(**kw))


async def act(uid, pid, action, hh=H1, **b) -> tuple[int, dict]:
    if action in ("approve", "reject"):
        return await api(uid, "POST", pp(hh, f"/{pid}/{action}"), b or {})
    return await api(uid, "POST", pp(hh, f"/{pid}/{action}"), b)


async def to_status(pid, status: str, *, hh=H1, maker=U_MK, checker=U_CK, rail="ach") -> list[tuple[str, int]]:
    """Drive a draft to ``status`` through the API (each step a real route); returns [(step, code)]."""
    steps = [("submit", maker, {}), ("approve", checker, {"note": "verify"}),
             ("mark_sent", maker, {"channel": "custodian_portal", "external_ref": f"{TAG}-ref"}),
             ("acknowledge", maker, {"external_ref": f"{TAG}-ack"}),
             ("settle", maker, {"settled_on": TODAY.isoformat()})]
    order = ["draft", "pending_approval", "approved", "sent", "acknowledged", "settled"]
    out = []
    for name, uid, b in steps[:order.index(status)]:
        c, _b = await act(uid, pid, name, hh=hh, **b)
        out.append((name, c))
    return out


# ── Sections ───────────────────────────────────────────────────────────────────────────────────────────────────
ALLOWED_EDGES = {
    "draft": {"pending_approval", "cancelled", "expired"},
    "pending_approval": {"approved", "draft", "cancelled", "expired"},
    "approved": {"sent", "cancelled", "expired"},
    "sent": {"awaiting_client_consent", "acknowledged", "rejected", "cancelled"},
    "awaiting_client_consent": {"acknowledged", "rejected", "cancelled", "expired"},
    "acknowledged": {"settled", "rejected"},
    "settled": {"reconciled", "returned"},
    "reconciled": {"closed", "returned"},
}
STATUSES = tuple(sorted(PS.STATUS_LABELS))


async def disallowed_refused(conn, pid, frm: str) -> list:
    """Every status this row may NOT move to, by a direct UPDATE (rolled back): each must be refused by the trigger."""
    bad = []
    for to in STATUSES:
        if to == frm or to in ALLOWED_EDGES.get(frm, set()):
            continue
        _r, err = await in_rolled_back(conn, ORG_A, lambda c, to=to: c.execute(
            "UPDATE public.payment_instructions SET status = $2 WHERE id = $1::uuid AND system_to IS NULL", pid, to))
        msg = str(err or "")
        if not (isinstance(err, asyncpg.InsufficientPrivilegeError)
                and ("illegal payment instruction transition" in msg or "is immutable" in msg)):
            bad.append((frm, to, type(err).__name__ if err else "ALLOWED", msg[:80]))
    return bad


async def y_lifecycle(conn):
    section("[Y] Lifecycle — every allowed transition (one payment_events row each); every disallowed one refused by "
            "the DB trigger; the event log is append-only")
    c, b = await drafted()
    pid = pay_of(b).get("id")
    check(c == 201 and pay_of(b).get("status") == "draft", "a same-owner ACH draft (A1 → A2, value date today) is "
          "created through the API", f"{c} {dumps(b)[:300]}")
    if not pid:
        return
    S["pay"]["P1"] = pid
    ev = await events(conn, pid)
    check([e["action"] for e in ev] == ["create"] and ev[0]["actor_id"] == U_MK,
          "creating writes exactly ONE event ('create', by the maker) — written by the database trigger", f"{ev}")
    walk = [("draft", None), ("pending_approval", ("submit", U_MK, {})), ("approved", ("approve", U_CK, {"note": "ok"})),
            ("sent", ("mark_sent", U_MK, {"channel": "custodian_portal", "external_ref": f"{TAG}-ref"})),
            ("awaiting_client_consent", ("consent_requested", U_MK, {"note": "custodian asks for e-consent"})),
            ("acknowledged", ("acknowledge", U_MK, {"external_ref": f"{TAG}-ack"})),
            ("settled", ("settle", U_MK, {"settled_on": TODAY.isoformat()})),
            ("reconciled", ("reconcile", U_MK, {"method": "custodian_data", "transaction_ids": [S["tx"]["T_OUT"]]}))]
    actions = {"pending_approval": "submit", "approved": "approve", "sent": "mark_sent",
               "awaiting_client_consent": "consent_requested", "acknowledged": "acknowledge", "settled": "settle",
               "reconciled": "reconcile"}
    for status, step in walk:
        if step is not None:
            n0 = len(await events(conn, pid))
            name, uid, bb = step
            c, b = await act(uid, pid, name, **bb)
            evs = await events(conn, pid)
            check(c == 200 and pay_of(b).get("status") == status and len(evs) == n0 + 1
                  and evs[-1]["action"] == actions[status] and evs[-1]["to_status"] == status
                  and evs[-1]["actor_id"] == uid,
                  f"{name}: → {status}, exactly ONE payment_events row ({actions[status]}, its actor)",
                  f"{c} {detail_text(b)[:300]} events={evs[n0:]}")
        bad = await disallowed_refused(conn, pid, status)
        n_dis = len([t for t in STATUSES if t != status and t not in ALLOWED_EDGES.get(status, set())])
        check(not bad and n_dis > 0, f"from {status}: all {n_dis} disallowed targets refused by the DB trigger "
              "(a direct UPDATE)", f"{bad[:4]}")
    # an ALLOWED edge by a direct UPDATE that names no actor is refused too (no money state without a person)
    _r, err = await in_rolled_back(conn, ORG_A, lambda c2: c2.execute(
        "UPDATE public.payment_instructions SET status = 'closed', closed_by = $2, closed_at = now() WHERE id = "
        "$1::uuid AND system_to IS NULL", pid, U_MK))
    check(isinstance(err, asyncpg.CheckViolationError) and "payment_events_human_chk" in str(err),
          "an ALLOWED edge (reconciled → closed) by a direct UPDATE naming no actor is refused: the DB's own event row "
          "fails payment_events_human_chk", f"{type(err).__name__}: {err}")

    async def named(c2):
        await c2.execute("SELECT set_config('payments.action', 'close', true), set_config('payments.actor_id', $1, "
                         "true)", str(U_CK))
        await c2.execute("UPDATE public.payment_instructions SET status = 'closed', closed_by = $2, closed_at = now() "
                         "WHERE id = $1::uuid AND system_to IS NULL", pid, U_CK)
        return await c2.fetch("SELECT action, actor_id FROM public.payment_events WHERE payment_id = $1::uuid AND "
                              "action = 'close'", pid)
    rows, err = await in_rolled_back(conn, ORG_A, named)
    check(err is None and rows is not None and len(rows) == 1 and rows[0]["actor_id"] == U_CK,
          "the same edge naming its actor writes exactly ONE event row (the database's) — rolled back",
          f"{err} {rows}")
    # ACH: provisional through the return window
    c, b = await act(U_MK, pid, "close")
    p = await live(conn, pid)
    check(c == 409 and "provisional" in detail_text(b) and p["status"] == "reconciled"
          and p["provisional_until"] == date(2026, 11, 11),
          "ACH: settled on 11-09 is PROVISIONAL until 11-11 (2 business days); close is refused through the window",
          f"{c} {detail_text(b)[:200]} {p['status']} {p['provisional_until']}")
    PS.set_clock(lambda: date(2026, 11, 12))
    try:
        c, b = await act(U_MK, pid, "close", note="window passed")
    finally:
        PS.set_clock(lambda: TODAY)
    check(c == 200 and pay_of(b).get("status") == "closed", "after the window (clock 11-12) it closes", f"{c}")
    bad = await disallowed_refused(conn, pid, "closed")
    check(not bad, f"closed is immutable: every one of the {len(STATUSES) - 1} targets refused", f"{bad[:3]}")
    # append-only events
    _r, err = await in_rolled_back(conn, ORG_A, lambda c2: c2.execute(
        "UPDATE public.payment_events SET detail = '{}'::jsonb WHERE payment_id = $1::uuid", pid))
    _r2, err2 = await in_rolled_back(conn, ORG_A, lambda c2: c2.execute(
        "DELETE FROM public.payment_events WHERE payment_id = $1::uuid", pid))
    check(isinstance(err, asyncpg.InsufficientPrivilegeError) and isinstance(err2, asyncpg.InsufficientPrivilegeError),
          "payment_events refuses UPDATE and DELETE (append-only) for the app", f"{err} / {err2}")
    op = await operator_conn()
    try:
        _r3, err3 = await in_rolled_back(op, ORG_A, lambda c2: c2.execute(
            "DELETE FROM public.payment_events WHERE payment_id = $1::uuid", pid))

        async def with_guc(c2):
            await c2.execute("SELECT set_config('payments.teardown', 'fixture', true)")
            return await c2.execute("DELETE FROM public.payment_events WHERE payment_id = $1::uuid", pid)
        r4, err4 = await in_rolled_back(op, ORG_A, with_guc)
    finally:
        await close_conn(op)
    check(isinstance(err3, asyncpg.InsufficientPrivilegeError) and err4 is None and str(r4).startswith("DELETE "),
          "even the operator role is refused WITHOUT the teardown GUC; the documented path (payments.teardown = "
          "'fixture', not app_service) deletes — rolled back", f"{err3} / {err4} {r4}")


async def y_approval(conn):
    section("[Y] Approval — the S30 FLOOR: no self-approval for anyone; refusals recorded; the DB backs it")
    c, b = await drafted(purpose=NAME + "floor case")
    pid = pay_of(b).get("id")
    c2, _b2 = await act(U_MK, pid, "submit")
    S["pay"]["P_FLOOR"] = pid
    before = await live(conn, pid)
    r0 = await refusals(conn, pid)
    c3, b3 = await act(U_MK, pid, "approve", note="me")
    after = await live(conn, pid)
    check(c == 201 and c2 == 200 and c3 == 403 and after["status"] == "pending_approval" and after["approved_by"] is None
          and after["updated_at"] == before["updated_at"] and await refusals(conn, pid) == r0 + 1,
          "the maker cannot approve: 403, recorded in sod_decisions, nothing changed", f"{c} {c2} {c3} "
          f"{detail_text(b3)[:200]}")
    # the org turned self-approval ON (agent proposals AND this item type, floor off) — the floor still holds
    pol = await platform(conn, "SELECT item_type, self_approval, enforce_floor FROM sod_rules WHERE org_id = $1 AND "
                               "rule_kind = 'policy' AND status = 'active'", ORG_A)
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            d = await sod.can_check(c_, org_id=ORG_A, item_type=sod.PAYMENT_INSTRUCTION, checker_id=U_MK,
                                    maker_ids=[U_MK], other_checker_exists=False)
            d_ap = await sod.can_check(c_, org_id=ORG_A, item_type=sod.AGENT_PROPOSAL, checker_id=U_MK,
                                       maker_ids=[U_MK], other_checker_exists=False)
    check(len(pol) == 2 and d.refused and "floor rule" in d.reason and d_ap.self_approval,
          "an org that turned self-approval ON (agent proposals self-approve there) still cannot self-approve a "
          "payment: the floor rule refuses", f"{[dict(r) for r in pol]} {d.reason} / {d_ap.outcome}")
    # a co-maker (an editor) cannot approve either
    c, b = await drafted(purpose=NAME + "co-maker")
    pco = pay_of(b).get("id")
    ce, _be = await act(U_CK, pco, "edit", changes={"purpose": NAME + "co-maker, edited"})
    cs, _bs = await act(U_MK, pco, "submit")
    r0 = await refusals(conn, pco)
    ca, ba = await act(U_CK, pco, "approve")
    check(ce == 200 and cs == 200 and ca == 403 and (await live(conn, pco))["status"] == "pending_approval"
          and await refusals(conn, pco) == r0 + 1,
          "an EDITOR is a maker too: U_CK edited, so U_CK's approval is refused and recorded", f"{ce} {cs} {ca} "
          f"{detail_text(ba)[:200]}")
    # a Super Admin who made it
    c, b = await drafted(uid=U_SA, purpose=NAME + "super admin")
    psa = pay_of(b).get("id")
    cs, _ = await act(U_SA, psa, "submit")
    r0 = await refusals(conn, psa)
    ca, ba = await act(U_SA, psa, "approve")
    check(c == 201 and cs == 200 and ca == 403 and (await live(conn, psa))["status"] == "pending_approval"
          and await refusals(conn, psa) == r0 + 1,
          "a SUPER ADMIN who made the payment cannot approve it (403, recorded)", f"{c} {cs} {ca} {detail_text(ba)[:200]}")
    # a second eligible person approves
    n0 = len(await events(conn, pid))
    ca, ba = await act(U_CK, pid, "approve", note="second person")
    p = await live(conn, pid)
    allowed = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE item_id = $1 AND outcome = 'allowed'",
                                 str(pid), fetch="fetchval"))
    check(ca == 200 and p["status"] == "approved" and p["approved_by"] == U_CK and U_CK not in p["maker_ids"]
          and len(await events(conn, pid)) == n0 + 1 and allowed >= 1,
          "a second eligible person (U_CK) approves: approved_by is U_CK, ∉ maker_ids, the decision recorded",
          f"{ca} {detail_text(ba)[:200]} {p['status']} allowed={allowed}")
    # the DB itself refuses approved_by ∈ makers
    _r, err = await in_rolled_back(conn, ORG_A, lambda c2: c2.execute(
        "UPDATE public.payment_instructions SET approved_by = $2 WHERE id = $1::uuid AND system_to IS NULL", psa, U_SA))
    check(isinstance(err, asyncpg.CheckViolationError) and "payment_instructions_maker_checker_chk" in str(err),
          "the DATABASE refuses approved_by ∈ maker_ids (payment_instructions_maker_checker_chk)", f"{err}")
    # org B: the only approver is the maker — it WAITS, no bypass
    c, b = await drafted(uid=U_B, hh=HB, src="AB1", dst="AB2", purpose=NAME + "org B")
    pb = pay_of(b).get("id")
    cs, _ = await act(U_B, pb, "submit", hh=HB)
    ca, _ = await act(U_B, pb, "approve", hh=HB)
    S["pay"]["PB"] = pb
    with _ctx(ORG_B):
        pool = await get_pool()
        async with pool.acquire() as c_:
            elig = await sod.eligible_checkers(c_, org_id=ORG_B, item_type=sod.PAYMENT_INSTRUCTION, maker_ids=[U_B])
    check(c == 201 and cs == 200 and ca == 403 and elig == [] and (await live(conn, pb))["status"] == "pending_approval",
          "an org with ONE eligible checker (org B, like 2nd Act today): the maker is refused and the payment WAITS "
          "(no eligible checker, no bypass)", f"{c} {cs} {ca} {elig}")
    # the inbox: the maker waits, the checker may act
    c1, mk = await api(U_MK, "GET", "/api/v1/human-tasks/mine")
    c2, ck = await api(U_CK2, "GET", "/api/v1/human-tasks/mine")
    tid = str(after["approval_task_id"])
    in_wait = any(t.get("id") == tid for t in mk.get("waiting_on_others") or [])
    not_act = not any(t.get("id") == tid for t in mk.get("actionable") or [])
    co_ok = any(t.get("id") == str((await live(conn, pco))["approval_task_id"]) for t in ck.get("actionable") or [])
    ed_blocked = not any(t.get("id") == str((await live(conn, pco))["approval_task_id"])
                         for t in (await api(U_CK, "GET", "/api/v1/human-tasks/mine"))[1].get("actionable") or [])
    check(c1 == 200 and c2 == 200 and in_wait and not_act and co_ok and ed_blocked,
          "the inbox respects the floor: the maker sees the task WAITING (never actionable), the editor is not offered "
          "it, a different approver is", f"{c1} {c2} wait={in_wait} notact={not_act} co={co_ok} ed={ed_blocked}")
    # bulk approve: the same single path
    c, b = await drafted(purpose=NAME + "bulk")
    pbk = pay_of(b).get("id")
    await act(U_MK, pbk, "submit")
    cl, lst = await api(U_MK, "GET", "/api/v1/approvals/bulk?type=payment")
    mine = next((i for i in lst.get("items") or [] if i.get("id") == pbk), {})
    r0 = await refusals(conn, pbk)
    cm, bm = await api(U_MK, "POST", "/api/v1/approvals/bulk", {"type": "payment", "item_ids": [pbk],
                                                               "decision": "approve"})
    res_m = (bm.get("results") or [{}])[0]
    cl2, lst2 = await api(U_CK2, "GET", "/api/v1/approvals/bulk?type=payment")
    theirs = next((i for i in lst2.get("items") or [] if i.get("id") == pbk), {})
    cc, bc = await api(U_CK2, "POST", "/api/v1/approvals/bulk", {"type": "payment", "item_ids": [pbk],
                                                                "decision": "approve"})
    res_c = (bc.get("results") or [{}])[0]
    p = await live(conn, pbk)
    check(cl == 200 and mine.get("actions") == [] and mine.get("blocked") == "maker" and cm == 200
          and res_m.get("result") == "refused" and res_m.get("code") == 403 and await refusals(conn, pbk) == r0 + 1
          and cl2 == 200 and theirs.get("actions") == ["approve", "reject"] and cc == 200
          and res_c.get("result") == "approved" and p["status"] == "approved" and p["approved_by"] == U_CK2,
          "bulk approve respects the floor: the maker's list offers nothing (blocked: maker), the maker's bulk "
          "approval is refused per item (403, recorded); U_CK2's goes through the same payments.decide",
          f"{cl} {mine.get('actions')} {cm} {res_m} {cl2} {theirs.get('actions')} {cc} {res_c} {p['status']}")


async def y_authority(conn):
    section("[Y] Authority — BOTH checks in the one path (custody: trading tier; account: rep7b managed); third party")
    n0 = int(await platform(conn, "SELECT count(*) FROM payment_instructions WHERE org_id = $1", ORG_A, fetch="fetchval"))
    c, b = await drafted(uid=U_INQ)
    det = b.get("detail") if isinstance(b.get("detail"), dict) else {}
    rs = det.get("reasons") or []
    check(c == 403 and len(rs) == 1 and "trading authority" in rs[0] and "inquiry" in rs[0],
          "INQUIRY-only trading authority → refused (403) with the custody reason", f"{c} {detail_text(b)[:300]}")
    c, b = await drafted(src="A5")
    det = b.get("detail") if isinstance(b.get("detail"), dict) else {}
    rs = det.get("reasons") or []
    check(c == 403 and len(rs) == 1 and "account authority" in rs[0] and "read-only" in rs[0],
          "a source account that is not MANAGED (held away → read_only) → refused with the account reason",
          f"{c} {detail_text(b)[:300]}")
    c, b = await drafted(uid=U_INQ, src="A5")
    det = b.get("detail") if isinstance(b.get("detail"), dict) else {}
    rs = det.get("reasons") or []
    check(c == 403 and len(rs) == 2 and any("trading authority" in r for r in rs) and any("account authority" in r
                                                                                          for r in rs),
          "both failing → BOTH reasons shown together", f"{c} {rs}")
    c1, b1 = await drafted(dst="A3")
    c2, b2 = await drafted(dst="A4")
    check(c1 == 422 and "third-party payments come later" in detail_text(b1) and "same owner" in detail_text(b1)
          and c2 == 422 and "third-party payments come later" in detail_text(b2) and "household" in detail_text(b2),
          "a destination of ANOTHER OWNER (same household) or in ANOTHER HOUSEHOLD → \"third-party payments come later\"",
          f"{c1} {detail_text(b1)[:200]} / {c2} {detail_text(b2)[:200]}")
    # the database refuses it too (a direct INSERT naming an actor)
    async def ins(c_, dst):
        await c_.execute("SELECT set_config('payments.actor_id', $1, true)", str(U_MK))
        return await c_.execute(
            "INSERT INTO payment_instructions (org_id, household_id, source_account_id, destination_account_id, amount, "
            "rail, value_date, purpose, status, maker_id, maker_ids) VALUES ($1, $2, $3, $4, $5, 'ach', $6, 'verify', "
            "'draft', $7, ARRAY[$7]::uuid[])", ORG_A, H1, A["A1"], A[dst], D(AMOUNT), TODAY, U_MK)
    _r, e1 = await in_rolled_back(conn, ORG_A, lambda c_: ins(c_, "A3"))
    _r, e2 = await in_rolled_back(conn, ORG_A, lambda c_: ins(c_, "A4"))
    check(all(isinstance(e, asyncpg.CheckViolationError) and "third-party payments come later" in str(e)
              for e in (e1, e2)), "the DB trigger refuses both too (a direct INSERT)", f"{e1} / {e2}")
    audited = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                       "'cash.payment.refused'", ORG_A, fetch="fetchval"))
    n1 = int(await platform(conn, "SELECT count(*) FROM payment_instructions WHERE org_id = $1", ORG_A, fetch="fetchval"))
    check(n1 == n0 and audited >= 5, "no refused draft wrote a payment; every refusal is audited (cash.payment.refused)",
          f"rows {n0}→{n1} audited={audited}")
    # the form only offers what may be instructed
    c, o = await api(U_INQ, "GET", pp(H1, "/options"))
    srcs = {s["id"]: s for s in o.get("sources") or []}
    c2, o2 = await api(U_MK, "GET", pp(H1, "/options"))
    s2 = {s["id"]: s for s in o2.get("sources") or []}
    a1 = s2.get(str(A["A1"])) or {}
    check(c == 200 and srcs and all(not s["eligible"] for s in srcs.values()) and c2 == 200
          and a1.get("eligible") is True and [d["id"] for d in a1.get("destinations") or []] == [str(A["A2"]),
                                                                                                 str(A["A5"]),
                                                                                                 str(A["A6"])]
          and [r["key"] for r in a1.get("rails") or []] == ["ach", "journal"]
          and (s2.get(str(A["A5"])) or {}).get("eligible") is False,
          "the new-instruction form: an inquiry holder can instruct nothing; for U_MK, A1 offers only its SAME-OWNER "
          "accounts and only the DECLARED rails; the held-away A5 is offered with its reason",
          f"{c} {c2} {dumps(a1)[:400]}")


async def y_rails(conn):
    section("[Y] Rails by capability; value dates on business days (refused, never moved — the documented rule)")
    c1, b1 = await drafted(rail="wire")
    c2, b2 = await drafted(rail="check")
    c3, b3 = await drafted(rail="journal")
    check(c1 == 422 and "does not declare" in detail_text(b1) and c2 == 422 and "unknown" in detail_text(b2)
          and c3 == 201, "a rail the custodian declares 'no' (wire) or leaves 'unknown' (check) is refused; a declared "
          "one (journal) is accepted", f"{c1} {detail_text(b1)[:160]} / {c2} / {c3}")
    S["pay"]["P_JOURNAL"] = pay_of(b3).get("id")
    c4, b4 = await drafted(vdate=HOLIDAY)
    c5, b5 = await drafted(vdate=SATURDAY)
    c6, b6 = await drafted(vdate=TODAY - timedelta(days=3))
    check(c4 == 422 and "holiday" in detail_text(b4) and "2026-11-27" in detail_text(b4)
          and c5 == 422 and "Saturday" in detail_text(b5) and "2026-11-16" in detail_text(b5)
          and c6 == 422 and "in the past" in detail_text(b6),
          "value date: Thanksgiving refused naming 11-27, a Saturday refused naming 11-16, a past date refused — "
          "never moved silently", f"{c4} {detail_text(b4)[:160]} / {c5} / {c6}")
    check(PS.value_date_refusal(HOLIDAY, TODAY) is not None and PS.value_date_refusal(TODAY, TODAY) is None
          and PS.add_business_days(date(2026, 11, 25), 2) == date(2026, 11, 30),
          "the rule itself: a business day today is fine; business days skip Thanksgiving and the weekend")


def pdf_text(pdf: bytes) -> str:
    """The text of a reportlab PDF: every content stream, inflated."""
    out = [pdf.decode("latin-1", "replace")]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        data = m.group(1).strip()
        try:
            if data.endswith(b"~>"):           # reportlab: ASCII85 over Flate
                data = base64.a85decode((data[2:] if data.startswith(b"<~") else data)[:-2].strip(), adobe=False)
            out.append(zlib.decompress(data).decode("latin-1", "replace"))
        except (zlib.error, ValueError):
            out.append(m.group(1).decode("latin-1", "replace"))
    return "\n".join(out)


async def y_adapter(conn):
    section("[Y] Adapters — manual renders the sheet (last four only), confirm → sent; any other custodian is "
            "NotConnected with its reason; no network")
    c, b = await drafted(purpose=NAME + "sheet")
    pid = pay_of(b).get("id")
    steps = await to_status(pid, "approved")
    n0 = len(await events(conn, pid))
    code, raw, ctype = await api_raw(U_MK, "POST", pp(H1, f"/{pid}/sheet"))
    text = pdf_text(raw)
    evs = await events(conn, pid)
    check(code == 200 and ctype.startswith("application/pdf") and raw[:4] == b"%PDF" and "****7001" in text
          and "****7002" in text and not leaks_in(text) and (await live(conn, pid))["status"] == "approved"
          and len(evs) == n0 + 1 and evs[-1]["action"] == "sheet",
          "the manual adapter renders the PDF payment sheet: both accounts by their mask (****7001 / ****7002), NO "
          "full number and no hash in it; logged ('sheet'); the status does not change",
          f"{steps} {code} {ctype} leaks={leaks_in(text)} {len(evs) - n0}")
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            d = await PS.detail(c_, ORG_A, pid)
    rows = EL.payment_sheet_rows(d)
    check(dict(rows).get("From account", "").startswith("****7001") and not leaks_in(dumps(rows)),
          "payment_sheet_rows (what the sheet prints) carry the mask only", f"{rows[:2]}")
    c, b = await act(U_MK, pid, "mark_sent", channel="custodian_portal", external_ref=f"{TAG}-portal")
    p = await live(conn, pid)
    check(c == 200 and p["status"] == "sent" and p["adapter"] == "manual" and p["sent_channel"] == "custodian_portal"
          and p["sent_by"] == U_MK, "confirm the channel → SENT (adapter manual, the channel and sender recorded)",
          f"{c} {detail_text(b)[:200]}")
    S["pay"]["P_SENT"] = pid
    c, b = await drafted(src="A6", purpose=NAME + "not connected")
    px = pay_of(b).get("id")
    steps = await to_status(px, "approved")
    code, raw, _ct = await api_raw(U_MK, "POST", pp(H1, f"/{px}/sheet"))
    c2, b2 = await act(U_MK, px, "mark_sent", channel="email")
    msg = raw.decode("utf-8", "replace")
    check(c == 201 and [s for _n, s in steps] == [200, 200] and code == 409 and "not connected" in msg
          and c2 == 409 and "not connected" in detail_text(b2) and (await live(conn, px))["status"] == "approved",
          "a custodian the org enabled a direct adapter for (none connected) → NotConnected: the sheet and the send are "
          "refused WITH the reason; nothing changes", f"{c} {steps} {code} {msg[:200]} {c2}")
    alt = PA.adapter_for({"custodian_code": "Altruist"}, {"altruist"})
    man = PA.adapter_for({"custodian_code": "Altruist"}, set())
    refused = None
    try:
        await alt.send({"id": "x"})
    except PA.PaymentAdapterNotConnected as exc:
        refused = str(exc)
    check(isinstance(alt, PA.NotConnectedPaymentAdapter) and refused is not None and "altruist" in refused
          and isinstance(man, PA.ManualPaymentAdapter) and PA.registry()["manual"]["connected"] is True,
          "the registry: account → custodian → the org's enabled adapter (Altruist: NotConnected, with the reason) → "
          "else manual (always offered)", f"{type(alt).__name__} {refused} {type(man).__name__}")
    check(NETWORK_CALLS == [], "no network call during the adapter section (every httpx transport refuses)",
          f"{NETWORK_CALLS[:3]}")


async def y_reconcile(conn):
    section("[Y] Reconciliation — the custodian feed (amount, window, account, direction) or manual with a reason by "
            "a different person; unmatched after 3 business days → ONE task; ACH returns")
    c, b = await drafted(purpose=NAME + "reconcile")
    pid = pay_of(b).get("id")
    await to_status(pid, "settled")
    c, m = await api(U_MK, "GET", pp(H1, f"/{pid}/matches"))
    ids = {x["id"]: x["leg"] for x in m.get("matches") or []}
    p1_used = S["tx"]["T_OUT"]       # the lifecycle payment reconciled T_OUT already
    check(c == 200 and ids.get(S["tx"]["T_IN"]) == "in" and S["tx"]["T_WRONG"] not in ids
          and S["tx"]["T_DIR"] not in ids and p1_used not in ids,
          "matches: the destination's IN leg (same amount, window, direction) — never the wrong amount, never the wrong "
          "direction, never a feed row another payment already used", f"{c} {ids}")
    c, b = await act(U_MK, pid, "reconcile", method="custodian_data", transaction_ids=[S["tx"]["T_IN"]])
    check(c == 422 and "outgoing leg" in detail_text(b),
          "a match must include the SOURCE's outgoing leg — an IN leg alone is refused", f"{c} {detail_text(b)[:200]}")
    S["pay"]["P_UNMATCHED"] = pid
    # manual: the sender cannot; a note is required; a different person with a note can
    pj = S["pay"].get("P_JOURNAL")
    await to_status(pj, "settled", rail="journal")
    c1, b1 = await act(U_MK, pj, "reconcile", method="manual", note="seen on the statement")
    c2, b2 = await act(U_CK, pj, "reconcile", method="manual")
    c3, b3 = await act(U_CK, pj, "reconcile", method="manual", note="confirmed on the custodian statement")
    p = await live(conn, pj)
    check(c1 == 403 and c2 == 422 and c3 == 200 and p["status"] == "reconciled" and p["reconcile_method"] == "manual"
          and p["reconciled_by"] == U_CK and p["sent_by"] == U_MK,
          "manual reconciliation: the SENDER is refused (403), no reason is refused (422), a different person with a "
          "reason reconciles", f"{c1} {c2} {c3} {detail_text(b1)[:120]}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "UPDATE public.payment_instructions SET reconciled_by = sent_by WHERE id = $1::uuid AND system_to IS NULL", pj))
    check(isinstance(err, asyncpg.CheckViolationError) and "payment_instructions_reconciler_chk" in str(err),
          "the DATABASE refuses a manual reconciler who is the sender (payment_instructions_reconciler_chk)", f"{err}")
    # unmatched → one task
    with _ctx(ORG_A):
        pool = await get_pool()
        early = await PS.unmatched_due(pool, today_=date(2026, 11, 11), org_id=ORG_A)
        first = await PS.unmatched_due(pool, today_=date(2026, 11, 12), org_id=ORG_A)
        again = await PS.unmatched_due(pool, today_=date(2026, 11, 13), org_id=ORG_A)
        async with pool.acquire() as c_:
            tasks = await HT.list_tasks(c_, ORG_A, kind=PS.REVIEW_KIND, subject_type=PS.SUBJECT, subject_id=pid,
                                        limit=10)
    evs = [e for e in await events(conn, pid) if e["action"] == "unmatched_alert"]
    check(pid not in early and pid in first and pid not in again and len(tasks) == 1 and len(evs) == 1
          and evs[0]["actor_id"] is None,
          "settled and not reconciled: after 2 business days nothing; after 3 → ONE review task + ONE unmatched_alert "
          "event; the next sweep adds none", f"early={early} first={first} again={again} tasks={len(tasks)} ev={evs}")
    # ACH return after settlement
    c, b = await drafted(purpose=NAME + "return")
    pr = pay_of(b).get("id")
    await to_status(pr, "settled")
    cbad, bbad = await act(U_MK, pr, "return", return_code="X1", reason="bad")
    c, b = await act(U_MK, pr, "return", return_code="R01", reason="insufficient funds")
    p = await live(conn, pr)
    ev = await events(conn, pr)
    cj, bj = await act(U_MK, pj, "return", return_code="R01")
    check(cbad == 422 and c == 200 and p["status"] == "returned" and p["return_code"] == "R01"
          and ev[-1]["action"] == "return" and ev[-1]["to_status"] == "returned" and cj == 409,
          "ACH: a return code after settlement → RETURNED, its event recorded; a malformed code is refused; a journal "
          "payment is never 'returned'", f"{cbad} {c} {p['status']} {ev[-1]} {cj}")
    # cancel and expire
    c, b = await drafted(purpose=NAME + "cancel pending")
    pc = pay_of(b).get("id")
    await act(U_MK, pc, "submit")
    tid = str((await live(conn, pc))["approval_task_id"])
    cx0, _ = await act(U_MK, pc, "cancel")
    cx, _ = await act(U_MK, pc, "cancel", reason="no longer needed")
    with _ctx(ORG_A):
        async with (await get_pool()).acquire() as c_:
            t = await HT.get_task(c_, ORG_A, tid)
    check(cx0 == 422 and cx == 200 and (await live(conn, pc))["status"] == "cancelled" and t["status"] == "cancelled",
          "cancel needs a reason; cancelling a pending payment withdraws its approval task", f"{cx0} {cx} {t['status']}")
    c, b = await drafted(purpose=NAME + "expire")
    pe = pay_of(b).get("id")
    with _ctx(ORG_A):
        out = await PS.expire_due(await get_pool(), today_=TODAY + timedelta(days=1), org_id=ORG_A)
    p = await live(conn, pe)
    ev = await events(conn, pe)
    check(pe in out and p["status"] == "expired" and ev[-1]["action"] == "expire" and ev[-1]["actor_id"] is None
          and os.environ.get(PS.SWEEP_ENV) != "1",
          "expire: a draft past its value date expires, logged with no actor (the sweep itself stays switched off)",
          f"{out} {p['status']} {ev[-1]}")


async def y_disclosure(conn):
    section("[Y] Disclosures — cash.payments and cash.sweep_rate exist as drafts and show where specified")
    bad = []
    for k in (PS.DISCLOSURE_KEY, "cash.sweep_rate"):
        d = LDD.parse(REPO / "docs" / "disclosures" / "defaults" / f"{k}.md")
        bad += LDD.validate(d)
    check(not bad and "cash.payments" in DS.KEYS and DS.fallback_text("cash.payments") == PS.PAYMENTS_DISCLOSURE
          and DS.fallback_text("cash.sweep_rate") == "The cash sweep rate and any spread are disclosed in our Form ADV.",
          "both keys are registered with their fallback text; both default files validate as DRAFTS for the loader "
          "(a second Super Admin publishes)", f"{bad}")
    pub = int(await platform(conn, "SELECT count(*) FROM disclosure_versions v JOIN disclosures d ON d.id = "
                                   "v.disclosure_id WHERE d.key = ANY($1::text[]) AND v.status = 'published'",
                             ["cash.payments", "cash.sweep_rate"], fetch="fetchval"))
    if pub:
        find(f"{pub} published version(s) of the two keys exist — screens show the library text")
    else:
        find("neither key has a published version yet — screens show the built-in text until a second Super Admin "
             "publishes the drafts")
    c, b = await api(U_MK, "GET", pp(H1))
    lab = ((b.get("labels") or {}).get("cash.payments") or {})
    cm, bm = await api(U_MEM, "GET", f"/api/v1/cash/households/{H1}/overview")
    cs, bs = await api(U_MK, "GET", f"/api/v1/cash/households/{H1}/overview")
    sw = ((bm.get("labels") or {}).get("cash.sweep_rate") or {})
    check(c == 200 and isinstance(lab.get("text"), str) and bool(lab["text"]) and cm == 200
          and isinstance(sw.get("text"), str) and "Form ADV" in sw["text"] and cs == 200
          and "cash.sweep_rate" not in (bs.get("labels") or {}),
          "cash.payments on the payments screen; cash.sweep_rate on a MEMBER's Treasury overview (not staff's)",
          f"{c} {lab.get('source')} {cm} {sw.get('source')} {cs}")
    pid = S["pay"].get("P_SENT")
    if pid:
        code, raw, _ = await api_raw(U_MK, "POST", pp(H1, f"/{pid}/sheet"))
        check(code == 200 and "Disclosure" in pdf_text(raw), "the payment sheet carries the cash.payments text",
              f"{code}")


async def y_visibility(conn):
    section("[Y] Members only their family (read-only, last four); staff only visible families; org isolation; no full "
            "account number anywhere")
    c, b = await api(U_MEM, "GET", pp(H1))
    items = b.get("items") or []
    check(c == 200 and b.get("member_view") is True and (b.get("permissions") or {}).get("can_write") is False
          and (b.get("vocabularies") or {}).get("editable") == [] and b.get("actions") == [] and len(items) > 0
          and all(i.get("actions") == [] for i in items) and all("maker_ids" not in i for i in items),
          "a MEMBER reads their family's payments READ-ONLY (no write flag, editable empty, no actions, no makers)",
          f"{c} {len(items)} {dumps(b)[:300]}")
    pid = S["pay"].get("P1")
    cd, bd = await api(U_MEM, "GET", pp(H1, f"/{pid}"))
    who = {e.get("who") for e in (pay_of(bd).get("timeline") or [])}
    check(cd == 200 and pay_of(bd).get("actions") == [] and who <= {"Your adviser's team", "System"} and bool(who),
          "the member's detail: the timeline without staff names, no actions", f"{cd} {who}")
    c2, _ = await api(U_MEM, "GET", pp(H2))
    c3, _ = await api(U_MEM, "GET", pp(H2, f"/{S['pay'].get('P1')}"))
    c4, b4 = await drafted(uid=U_MEM)
    check(c2 == 404 and c3 == 404 and c4 == 403, "never another family (404, as for a missing one); a member can never "
          "draft (403)", f"{c2} {c3} {c4} {detail_text(b4)[:120]}")
    c5, _ = await api(U_RS, "GET", pp(H1))
    c6, _ = await api(U_RS, "GET", pp(H2))
    c7, b7 = await api(U_VIEW, "GET", pp(H1))
    c8, b8 = await drafted(uid=U_VIEW)
    check(c5 == 404 and c6 == 200 and c7 == 200 and (b7.get("permissions") or {}).get("can_write") is False
          and c8 == 403 and "initiate_payment" in detail_text(b8),
          "restricted staff see only their visible family (H1 404, H2 200); a view-only caller reads, cannot draft "
          "(403 naming initiate_payment)", f"{c5} {c6} {c7} {c8}")
    c9, _ = await api(U_B, "GET", pp(H1))
    c10, _ = await api(U_MK, "GET", pp(HB))
    check(c9 == 404 and c10 == 404, "org isolation in the API: org B cannot open org A's household, nor A B's",
          f"{c9} {c10}")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    a_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM payment_instructions WHERE org_id = $1", ORG_B,
                           fetch="fetchval"))
    b_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM payment_instructions WHERE org_id = $1", ORG_A,
                           fetch="fetchval"))
    a_ev = int(await scoped(conn, ORG_B, "SELECT count(*) FROM payment_events WHERE org_id = $1", ORG_A,
                            fetch="fetchval"))
    a_own = int(await scoped(conn, ORG_A, "SELECT count(*) FROM payment_instructions WHERE org_id = $1", ORG_A,
                             fetch="fetchval"))
    b_own = int(await scoped(conn, ORG_B, "SELECT count(*) FROM payment_instructions WHERE org_id = $1", ORG_B,
                             fetch="fetchval"))
    check(by is False and a_b == 0 and b_a == 0 and a_ev == 0 and a_own > 0 and b_own > 0,
          "RLS isolation BOTH directions under a non-bypassing role (rolbypassrls = false): A reads none of B's "
          "payments, B none of A's (nor A's events); each reads its own", f"{by} {a_b} {b_a} {a_ev} {a_own} {b_own}")
    allb = "\n".join(b_ for _w, b_ in BODIES)
    audit = "\n".join(r["p"] for r in await platform(
        conn, "SELECT payload::text AS p FROM audit_log WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    evd = "\n".join(r["d"] for r in await platform(
        conn, "SELECT detail::text AS d FROM payment_events WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    runs = "\n".join(r["c"] for r in await platform(
        conn, "SELECT context::text AS c FROM workflow_runs WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    leaks = {k: leaks_in(v) for k, v in (("responses", allb), ("audit", audit), ("events", evd), ("tasks", runs))}
    check(bool(BODIES) and bool(audit) and bool(evd) and bool(runs) and not any(leaks.values()),
          f"no full account number (seeded in custodian_account_id) and no account hash in ANY of the {len(BODIES)} "
          "responses, the audit rows, the event details or the approval tasks — last four only", f"{leaks}")


async def y_autonomy(conn):
    section("[Y] Permissions / autonomy — the assistant may draft at most; every verb that moves money is never")
    metas = {k: WA.verb_autonomy(type("A", (), {"key": k})()) for k in
             ("cash.payment_draft", "cash.payment_submit", "cash.payment_approve", "cash.payment_send")}
    check(all(m["class"] == WA.NEVER and m.get("money") is True for m in metas.values())
          and all(REGISTRY.get(k) is None for k in metas),
          "the four payment verbs are declared NEVER (money) and NOT registered as actions — no workflow and no agent "
          "can reach them", f"{metas}")
    check(sod.VERB_DUTIES.get("cash.payment_draft") == "propose" and TA.required_tier(PS.MONEY_ACTION) == TA.LIMITED
          and not TA.requires_full_authority(PS.MONEY_ACTION),
          "duty 'propose' for the draft verb; a same-owner payment needs 'limited' trading authority (never 'inquiry')")
    perms = {n for n, _r, _a in rbac.ORG_ADMIN_DEFAULT_PERMISSIONS}
    have = {r["name"] for r in await platform(conn, "SELECT name FROM permissions WHERE name = ANY($1::text[])",
                                              ["initiate_payment", "approve_payment"])}
    check({"initiate_payment", "approve_payment"} <= perms and have == {"initiate_payment", "approve_payment"},
          "initiate_payment / approve_payment exist and are org_admin defaults (new orgs too)", f"{have}")
    it = await platform(conn, "SELECT floor_class, self_approval_capable FROM sod_item_types WHERE item_type = "
                              "'payment_instruction'", fetch="fetchrow")
    check(it is not None and it["floor_class"] == "transfer" and it["self_approval_capable"] is False
          and sod.item_type_for_task_kind(PS.TASK_KIND) == sod.PAYMENT_INSTRUCTION,
          "the S30 catalog: payment_instruction is a FLOOR item (transfer), never self-approval capable", f"{it}")
    acts = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action LIKE 'cash.payment.%'",
                              ORG_A, fetch="fetchval"))
    check(acts > 20, "every action is audited (cash.payment.*)", f"{acts}")


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
          f"the cash07 render tests ({NODE_TEST}, counted by their file: list, new-form limits, the draft body, detail "
          f"timeline, sheet + reconcile, member read-only, fail closed, the Treasury mount) all pass "
          f"({n.group(1) if n else '?'})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


#: the nested regressions, ONCE each, flat, after teardown: cash05 (the Treasury screens this sprint extends) and s30
#: (the segregation-of-duties service the floor item type joins)
REGRESSIONS = ("verify_cash05.py", "verify_s30.py")


def regression():
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    for script in REGRESSIONS:
        section(f"[Y] Regression — {script} ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=2700, env=env)
        t = VT.nested_total(out, code)
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


SECTION_TIMEOUT = 600   # every one-off section must finish in < 10 minutes


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await asyncio.wait_for(res, SECTION_TIMEOUT)
    except asyncio.TimeoutError:
        check(False, f"{name} exceeded {SECTION_TIMEOUT} s")
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


FIXTURE_SECTIONS = (("lifecycle", y_lifecycle), ("approval (the floor)", y_approval), ("authority", y_authority),
                    ("rails + value dates", y_rails), ("adapters", y_adapter), ("reconciliation", y_reconcile),
                    ("disclosures", y_disclosure), ("visibility + isolation", y_visibility),
                    ("permissions + autonomy", y_autonomy))


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
            for name, fn in FIXTURE_SECTIONS:
                await run(name, fn, conn)
        else:
            for name, _fn in FIXTURE_SECTIONS:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        PS.set_clock(None)
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (payments, events, tasks, decisions, grants, capabilities, "
                  "accounts, transactions, audit rows, …)", f"{left}")
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
