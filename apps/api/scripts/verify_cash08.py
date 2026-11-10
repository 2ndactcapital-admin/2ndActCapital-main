"""verify_cash08 — CASH08: PAYEES (a household's registry, bank details as the custodian's reference + the last four ONLY,
the database refusing any longer digit run), the S30 FLOOR duties (create / approve payee; the governed bank-detail
change with its CALL-BACK attestation; the first payment to a new payee — dormant), SLOAs linked from Chancery with
their flags, the THIRD-PARTY switch (off; two Super Admins + a counsel document required; still off at the end), vendor
invoices recorded as firm obligations that appear in coverage (duplicates refused), the Treasury → Payees / Invoices
screens.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash08.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx transport
refuses and records (asserted empty at the end) and the AI helpers refuse and record.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile call.
REAL: the app through an ASGI transport, services.cash_payees / cash_invoices / payments / sod / human_tasks /
cash_obligations / disclosures, the DB triggers, RLS and the live database. The script's own connection does not
bypass RLS (rolbypassrls = false, asserted). TIME IS INJECTED: "today" = 2026-11-09 (a Monday) through
``services.payments.set_clock`` and ``services.cash_payees.set_clock``.

FIXTURES (prefix 99000000-0000-0000-0000-0000cac8…): orgs A (VISIBILITY1 enforced) and B, is_test = true (asserted).
Users with real user_roles grants (the app's decision on each asserted): U_MK / U_CK advisors (manage + approve payees,
initiate + approve payments, manage_portfolio), U_CK2 compliance (approve payees only), U_SA / U_SA2 two Super Admins,
U_MEM a member of H1, U_RS fund finance restricted to H2, U_VIEW view_portfolio only; org B U_B. H1: E1 owns A1 ****8001
and A2 ****8002 (same owner — the "valid body" twin of the payee refusal). Custodian cash08verify declares ACH. Chancery
documents (each with distinct content / name): an SLOA, two invoices, a counsel sign-off, and one in org B. Fixture
"bank numbers" are obviously fake and never longer than four digits, except the refusal inputs in ``REFUSED_NUMBERS``.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with every teardown GUC a guard on a touched
table reads (cash_payees → payees.teardown; payment_instructions / payment_events → payments.teardown; sod_rules /
sod_decisions → sod.teardown), keyed by this run's fixture ids; tables without org_id (user_roles, role_permissions,
household_memberships, custodian_capabilities) passed in the plan by their fixture keys. Every touched table re-read
for its exact pre-test count BY FIXTURE IDS ONLY. Real rows (payees, payments, obligations, documents, links,
settings — the Olivo demo's included) fingerprinted before and after; the third-party switch asserted off for EVERY
org at the end.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<seed|payees|bank|numbers|sloa|thirdparty|firstpayment|invoices|visibility>: the one-off path — the seed, the
named sections (and what they need), the full teardown; no routes / front end / regression.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import inspect
import json
import logging
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
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash08"
BEFORE_REF = "e122089"   # cash08-work's base: 'merge cash06'
_R = ("any(view_portfolio|manage_payees|approve_payees); member scope (own family / published deals; staff "
      "unscoped)")
_M = f"authz manage_payees; {_R}"
_A = f"authz approve_payees; {_R}"
_MP = f"authz manage_payees+manage_portfolio; {_R}"
_SA = "super_admin"
_HH = "/api/v1/cash/households/{household_id}/payees"
_HI = "/api/v1/cash/households/{household_id}/invoices"
ROUTES = {("GET", _HH): _R, ("POST", _HH): _M, ("GET", _HH + "/{payee_id}"): _R,
          ("POST", _HH + "/{payee_id}/approve"): _A, ("POST", _HH + "/{payee_id}/reject"): _A,
          ("POST", _HH + "/{payee_id}/bank-change/approve"): _A, ("POST", _HH + "/{payee_id}/bank-change/reject"): _A,
          ("POST", _HH + "/{payee_id}/{action}"): _M,
          ("GET", _HI): _R, ("POST", _HI + "/tag"): _M, ("POST", _HI + "/record"): _MP,
          ("GET", "/api/v1/cash/third-party"): _SA, ("POST", "/api/v1/cash/third-party/request"): _SA,
          ("POST", "/api/v1/cash/third-party/approve"): _SA, ("POST", "/api/v1/cash/third-party/reject"): _SA}
SERVICES = ("apps/api/services/cash_payees.py", "apps/api/services/cash_invoices.py")
OWN_FILES = SERVICES + ("apps/api/routers/cash_payees.py",)
NODE_TEST = "tests/cash08/cash08.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH08_NOTES.md"
RUNBOOK = REPO / "docs" / "cash" / "CASH_PAYEES.md"
MIGRATION = REPO / "docs" / "cash08_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash08's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cash08 changed no existing route's gate and removed none (payments' draft body gained an optional payee "
          "field only)", f"{o.where()}; changed={sorted(changed)[:3]} removed={o.removed[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash08's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash08's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    cp = _code_only(_src("apps/api/services/cash_payees.py"))
    check("archive_live_row(" in cp and "jsonb_populate_record" not in cp,
          "cash_payees archives through the SHARED services.bitemporal helper (no copy of the archive SQL)")
    check(cp.count("sod.gate(") >= 6 and "sod.PAYEE," in cp and "sod.PAYEE_BANK_CHANGE," in cp
          and "sod.FIRST_PAYEE_PAYMENT," in cp and "sod.THIRD_PARTY_SETTING," in cp,
          "every floor decision goes through services.sod (payee, bank change, first payment, the switch)")
    check("HT.open_task(" in cp and "HT.complete_task(" in cp and "HT.register_precheck(BANK_TASK_KIND" in cp,
          "approvals are services.human_tasks tasks (the inbox), the bank change's precheck registered")
    check("THIRD_PARTY_RELEASE_LOCKED = True" in cp, "the release lock is ON in code (counsel first)")
    ps = _code_only(_src("apps/api/services/payments.py"))
    check("_third_party_gate(" in ps and "CPY.third_party_refusal(" in ps and "CPY.first_payment_gate(" in ps,
          "services.payments consults the third-party switch for a payee destination and carries the (dormant) "
          "first-payment gate")
    ci = _code_only(_src("apps/api/services/cash_invoices.py"))
    check("OB.create(" in ci and "external_key=" in ci and "call_claude" not in ci,
          "invoices go through cash_obligations.create with the duplicate key — amounts typed, no model call")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm|socket)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    sd = _src("apps/api/services/sod.py")
    check(all(m in sd for m in ('PAYEE = "payee"', 'PAYEE_BANK_CHANGE = "payee_bank_change"',
                                'FIRST_PAYEE_PAYMENT = "first_payment_to_new_payee"',
                                'THIRD_PARTY_SETTING = "third_party_payments_setting"', '"payee_approval": PAYEE',
                                '"payee_bank_change_approval": PAYEE_BANK_CHANGE',
                                '"third_party_switch_approval": THIRD_PARTY_SETTING')),
          "services.sod: the four item types and their three task kinds")
    mig = MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists() else ""
    for marker in ("payees_teardown_allowed", "cash_payees_last_four_chk", "cash_payees_maker_checker_chk",
                   "cash_payees_bank_change_checker_chk", "cash_payees_no_numbers", "cash_payees_guard", "'transfer'",
                   "[0-9]{5,}", "[0-9]{9,}", "_org_delete", "third_party_payments_setting"):
        check(marker in mig, f"docs/cash08_part1.sql carries {marker!r}")
    check("cash.third_party_payments" not in re.sub(r"--.*", "", mig) or "INSERT INTO public.org_settings" not in mig,
          "the migration never writes the cash.third_party_payments setting")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec_ in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
        check(sec_ in notes, f"docs/cash/CASH08_NOTES.md has section {sec_}")
    rb = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.exists() else ""
    check(all(m in rb for m in ("call-back", "on file", "SLOA", "counsel", "two Super Admins", "last four")),
          "docs/cash/CASH_PAYEES.md: onboarding, the call-back procedure, SLOAs, what enabling third-party payments "
          "requires")
    check("verify_cash08.py" in _src("scripts/regression_set.txt"), "verify_cash08.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    sodd = _src("docs/SEGREGATION_OF_DUTIES.md")
    check("cash08" in st.lower() and "payee_bank_change" in sodd and "third_party_payments_setting" in sodd,
          "PROJECT_STATUS (cash08) and SEGREGATION_OF_DUTIES (the new floor items) are updated")
    td = _src("docs/OUTSTANDING_TODO_LIST.md")
    check("cash.third_party" in td and "counsel" in td.lower(),
          "OUTSTANDING_TODO_LIST: publish cash.third_party; counsel review before the switch")
    dflt = _src("docs/disclosures/defaults/cash.third_party.md")
    check("key: cash.third_party" in dflt and "<!-- summary -->" in dflt,
          "the cash.third_party default text ships (a DRAFT a second Super Admin publishes)")
    code, out, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "docs/*.sql", args=("--name-only",))
    migs = [f for f in out.split() if f != "docs/schema_snapshot.sql"]
    check(code == 0 and migs == ["docs/cash08_part1.sql"] and "docs/schema_snapshot.sql" in out.split(),
          "cash08's own diff adds exactly one migration (docs/cash08_part1.sql) and refreshes the schema snapshot",
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

import main  # noqa: E402
from services import authz as AZ  # noqa: E402
from services import cash_invoices as CI  # noqa: E402
from services import cash_payees as CP  # noqa: E402
from services import disclosures as DS  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import payments as PS  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cash08verify"
NAME = "CASH08 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac8"
TODAY = date(2026, 11, 9)           # a Monday
TEARDOWN_GUCS = ("payees.teardown", "payments.teardown", "sod.teardown")
CUSTODIAN = "cash08verify"
D = Decimal
ON_FILE = "212-555-0100"            # the payee's call-back number on file (separated: no run over four digits)
REQUEST_PHONE = "646-555-0199"      # a number given IN the change request (never accepted as the call-back)
OTHER_PHONE = "917-555-0123"        # a number that is neither
#: the ONLY digit runs over four this script ever sends — each to a refusal check; scanned for afterwards
REFUSED_NUMBERS = {"last5": "48213", "aba": "026009593", "acct": "40817731205", "acct_sep": "026-009-593"}
INVOICE_NO = "INV-A7"
INVOICE_AMOUNT = "1250.00"


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_MK, U_CK, U_CK2, U_SA, U_SA2, U_MEM, U_RS, U_VIEW, U_B = (_id(10 + i) for i in range(9))
H1, H2, HB = _id(30), _id(31), _id(32)
E1, E1B, E2, EB = _id(40), _id(41), _id(42), _id(43)
A = {"A1": _id(70), "A2": _id(71), "A4": _id(73), "AB1": _id(77)}
DOC = {"SLOA": _id(80), "INV1": _id(81), "INV2": _id(82), "COUNSEL": _id(83), "OTHER": _id(84), "B": _id(85)}
PERMS = {
    "advisor": {"view_portfolio", "view_members", "manage_payees", "approve_payees", "initiate_payment",
                "approve_payment", "manage_portfolio"},
    "compliance": {"view_portfolio", "approve_payees"},
    "fund_finance": {"view_portfolio", "view_members", "manage_payees"},
    "ir_member_relations": {"view_portfolio"},
    "member": set(C.MEMBER_PERMS),
    "super_admin": set(),
}
# uid → (org, users.role mirror, short, role granted)
USERS = {
    U_MK: (ORG_A, "member", "mk", "advisor"), U_CK: (ORG_A, "member", "ck", "advisor"),
    U_CK2: (ORG_A, "member", "ck2", "compliance"), U_SA: (ORG_A, "super_admin", "sa", "super_admin"),
    U_SA2: (ORG_A, "super_admin", "sa2", "super_admin"), U_MEM: (ORG_A, "member", "mem", "member"),
    U_RS: (ORG_A, "member", "rs", "fund_finance"), U_VIEW: (ORG_A, "member", "view", "ir_member_relations"),
    U_B: (ORG_B, "member", "b", "advisor"),
}
ALL_USERS = list(USERS)
ENTS = {E1: (ORG_A, "Treasury Trust"), E1B: (ORG_A, "Spouse"), E2: (ORG_A, "Other Family"), EB: (ORG_B, "Org B")}
HOUSEHOLDS = {H1: (ORG_A, "Treasury", (E1, E1B)), H2: (ORG_A, "Other", (E2,)), HB: (ORG_B, "B", (EB,))}
# key → (org, household, entity, masked)
ACCOUNTS = {"A1": (ORG_A, H1, E1, "****8001"), "A2": (ORG_A, H1, E1, "****8002"), "A4": (ORG_A, H2, E2, "****8004"),
            "AB1": (ORG_B, HB, EB, "****8011")}
DOCS = {"SLOA": (ORG_A, E1, "cash08verify-sloa-harbor.pdf"), "INV1": (ORG_A, E1, "cash08verify-invoice-a7.pdf"),
        "INV2": (ORG_A, E1, "cash08verify-invoice-a7-resent.pdf"), "COUNSEL": (ORG_A, None, "cash08verify-counsel.pdf"),
        "OTHER": (ORG_A, E1, "cash08verify-sloa-second.pdf"), "B": (ORG_B, EB, "cash08verify-org-b.pdf")}
GRANTS = [(U_MK, E1, "limited"), (U_CK, E1, "limited")]
CAP_ROWS = [(CUSTODIAN, "ach", "yes")]
SEED_CHECK_LITERALS = (
    ("public.cash_payees", "cash_payees_kind_chk", ("vendor", "charity", "person", "outside_entity")),
    ("public.cash_payees", "cash_payees_status_chk", ("draft", "pending_approval", "active", "suspended", "retired")),
    ("public.cash_payees", "cash_payees_account_type_chk", ("checking", "savings", "other")),
    ("public.cash_obligations", "cash_obligations_source_type_chk", ("vendor_invoice",)),
    ("public.cash_obligations", "cash_obligations_certainty_chk", ("firm",)),
    ("public.cash_obligations", "cash_obligations_ref_chk", ("document",)),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("ach",)),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes",)),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("document",)),
    ("public.trading_authority_grants", "trading_authority_grants_authority_tier_check", ("limited",)),
    ("public.sod_item_types", "sod_item_types_floor_chk", ("transfer",)),
    ("public.payment_instructions", "payment_instructions_rail_chk", ("ach",)),
)
GOOD_PAYEE = {"kind": "vendor", "display_name": NAME + "Harbor Roofing", "legal_name": "Harbor Roofing LLC",
              "contact_name": "Accounts", "contact_email": "ap@harbor.example", "contact_phone": ON_FILE,
              "bank_name": "First Fixture Bank", "account_type": "checking", "last_four": "0042",
              "payee_reference": "STD-HARBOR-AB", "notes": "verify fixture"}
SEED_ROWS = {
    "public.cash_payees": [{"org_id": str(ORG_A), "household_id": str(H1), "kind": "vendor",
                            "display_name": NAME + "raw", "created_by": str(U_MK), "maker_ids": [str(U_MK)],
                            "status": "draft", "last_four": "0042", "payee_reference": "STD-RAW-AB",
                            "account_type": "checking", "sloa_account_ids": []}],
    "public.custodian_capabilities": [
        {"custodian_code": c, "capability_key": k, "value": v, "evidence_kind": "document",
         "evidence": "verify_cash08 fixture", "as_of": TODAY.isoformat(), "version_no": 1, "owner_scope": "platform",
         "org_id": None} for c, k, v in CAP_ROWS],
    "public.trading_authority_grants": [{"org_id": str(ORG_A), "entity_id": str(e), "user_id": str(u),
                                         "authority_tier": t} for u, e, t in GRANTS],
}
SEED_NON_COLUMN = {"app.is_super_admin", "app.current_org_id", "true", "1 day", "active", "staff_visibility.enforced",
                   "general", "individual", "view_only", "Lead adviser", "trust", "taxable", "upload", "stored",
                   "application/pdf"}
UNIQUES = ("cash_payees_name_live_uq", "cash_payees_task_live_uq", "cash_payees_bank_task_live_uq",
           "cash_obligations_external_key_uq", "cash_obligations_live_uq",
           "document_record_links_document_id_record_type_record_id_key", "org_settings_org_id_setting_key_key",
           "trading_authority_grants_entity_id_user_id_key", "custodian_capabilities_live_uq",
           "accounts_active_identity_uq")
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
    "documents": ("id", "org_id", "entity_id", "original_filename", "source", "mime_type", "status", "created_by"),
    "trading_authority_grants": ("org_id", "entity_id", "user_id", "authority_tier"),
    "custodian_capabilities": ("custodian_code", "capability_key", "value", "evidence_kind", "evidence", "as_of",
                               "version_no"),
    "cash_payees": ("org_id", "household_id", "kind", "display_name", "created_by", "maker_ids", "last_four",
                    "payee_reference", "notes", "status", "approved_by", "legal_name", "contact_name", "contact_email",
                    "contact_phone", "bank_name", "pending_bank_name", "pending_payee_reference",
                    "pending_contact_email", "bank_change_request_contact", "bank_change_reason", "callback_person",
                    "sloa_revoke_reason", "suspend_reason", "retire_reason", "pending_contact_phone",
                    "bank_change_request_phone", "callback_number", "pending_last_four"),
}
COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "public.accounts", "public.documents",
    "public.document_record_links", "public.trading_authority_grants", "public.custodian_capabilities",
    "public.cash_payees", "public.cash_obligations", "public.payment_instructions", "public.payment_events",
    "public.sod_decisions", "public.audit_log", "public.member_todos", "public.workflow_runs",
    "public.workflow_run_steps", "public.domain_events",
)

S: dict = {"payee": {}, "pay": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
BODIES: list[tuple[str, str]] = []          # (who / path, full body) of every payee / invoice / payment response
LOG_LINES: list[str] = []                   # every log record emitted during the run — scanned for number leaks
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


def number_leaks(text: str) -> list[str]:
    """Any refusal-input number inside ``text``: as sent, and as its bare digits — each bounded by a non-hex character
    so a uuid or a timestamp that happens to contain the digits never counts (separators are NOT stripped from the
    text: that would splice ids and times into false runs)."""
    out = []
    for k, v in REFUSED_NUMBERS.items():
        for form in {v, re.sub(r"\D", "", v)}:
            if re.search(rf"(?<![0-9A-Fa-f]){re.escape(form)}(?![0-9A-Fa-f])", text):
                out.append(k)
                break
    return out


class _LogCapture(logging.Handler):
    def emit(self, record):
        try:
            LOG_LINES.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


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
        "public.custodian_capabilities": f"custodian_code = '{CUSTODIAN}'",
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
            ("cash_payees", f"SELECT id, status, last_four, system_to FROM public.cash_payees WHERE {nf}"),
            ("payment_instructions", f"SELECT id, status, system_to FROM public.payment_instructions WHERE {nf}"),
            ("cash_obligations", f"SELECT id, amount, valid_to, system_to FROM public.cash_obligations WHERE {nf}"),
            ("documents", f"SELECT id, status FROM public.documents WHERE {nf}"),
            ("document_record_links", f"SELECT id, record_type, record_id FROM public.document_record_links WHERE {nf}"),
            ("org_settings", f"SELECT org_id, setting_key, setting_value FROM public.org_settings WHERE {nf}"),
            ("trading_authority_grants", f"SELECT id, authority_tier FROM public.trading_authority_grants WHERE {nf}"),
            ("sod_rules (org)", f"SELECT id, status, valid_to FROM public.sod_rules WHERE org_id IS NOT NULL AND {nf}")):
        out[f"{label} (real, the Olivo demo's included)"] = await fingerprint(conn, sql, orgs)
    out["custodian_capabilities (real)"] = await fingerprint(
        conn, "SELECT id, value, valid_to FROM public.custodian_capabilities WHERE custodian_code <> $1", CUSTODIAN)
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
            # platform rows without org_id, by the fixture custodian code (asserted to match no real row)
            "public.custodian_capabilities": ("custodian_code = $1::text", [CUSTODIAN]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cash08")
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
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash08"}}
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
    if any(x in path for x in ("/payees", "/invoices", "/payments", "/third-party", "/treasury")):
        BODIES.append((f"{USERS[uid][2]} {method} {path}", raw))
    try:
        return code, json.loads(raw or "{}")
    except ValueError:
        return code, {"raw": raw[:4000]}


def pe(hh, tail: str = "") -> str:
    return f"/api/v1/cash/households/{hh}/payees" + tail


def inv(hh, tail: str = "") -> str:
    return f"/api/v1/cash/households/{hh}/invoices" + tail


def payee_of(b: dict) -> dict:
    return (b or {}).get("payee") or {}


def detail_text(b) -> str:
    d = (b or {}).get("detail")
    return dumps(d) if not isinstance(d, str) else d


async def live(conn, pid) -> dict | None:
    r = await scoped(conn, ORG_A, "SELECT * FROM public.cash_payees WHERE id = $1::uuid AND system_to IS NULL", pid,
                     fetch="fetchrow")
    return dict(r) if r else None


async def states(conn, pid) -> list[dict]:
    return [dict(r) for r in await scoped(
        conn, ORG_A, "SELECT id, status, last_four, payee_reference, pending_last_four, system_to, callback_person "
                     "FROM public.cash_payees WHERE id = $1::uuid OR archived_from_id = $1::uuid ORDER BY system_from, "
                     "system_to NULLS LAST", pid)]


async def refusals(conn, item_type: str, item_id) -> int:
    return int(await platform(conn, "SELECT count(*) FROM public.sod_decisions WHERE item_id = $1 AND outcome = "
                                    "'refused' AND item_type = $2 AND org_id = $3::uuid", str(item_id), item_type,
                              ORG_A, fetch="fetchval"))


async def audits(conn, action: str, resource_id=None) -> int:
    return int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1::uuid AND action = $2 AND "
                                    "($3::uuid IS NULL OR resource_id = $3::uuid)", ORG_A, action,
                              str(resource_id) if resource_id else None, fetch="fetchval"))


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
        raise AssertionError(f"verify_cash08: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash08: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for mod in (PS, CP):
        have = list(inspect.signature(mod.set_clock).parameters)
        if have != ["fn"]:
            raise RuntimeError(f"{mod.__name__}.set_clock signature drifted: {have}")
        mod.set_clock(lambda: TODAY)
    h = _LogCapture()
    h.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(h)
    logging.getLogger().setLevel(logging.INFO)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes + reuse inventory; the findings recorded")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for marker in ("document_record_links", "cash_payee_sloa", "household_vendor_invoice", "same_owner_refusal",
                   "third_party_refusal", "load_principal", "org_settings", "external_key_uq", "archive_live_row",
                   "human_tasks", "Reuse inventory", "THIRD_PARTY_RELEASE_LOCKED", "first_payment_gate",
                   "cash.third_party", "call-back"):
        check(marker.lower() in notes.lower(), f"the notes name {marker!r}")
    for f in ("Chancery has no 'sloa' and no 'invoice' document kind", "cash07's same-owner decision",
              "The S30 path for a two-Super-Admin floor item", "Org settings"):
        check(f.replace("**", "") in notes.replace("**", ""), f"the notes record the finding {f[:60]!r}")
    cats = {r["code"] for r in await platform(conn, "SELECT code FROM reference_data WHERE list_key = 'doc_category' "
                                                    "AND org_id IS NULL")}
    if not ({"sloa", "invoice"} & cats):
        find(f"reference_data doc_category ({len(cats)} codes) still has no 'sloa' / 'invoice' — the document's role "
             "lives in document_record_links (cash05's precedent)")
    if not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md / ADDENDUM v1.1 are still not in the repo — nothing was invented from them")


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
          f"{got} CHECK constraints read; every literal the seeds and the sections use is one the database allows",
          f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(bool(n_chk > 0 and not refused), f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} raw-shaped tables "
          "evaluated by the database against the exact row shapes — none refuses", f"{refused}")
    keep = SEED_ROWS["public.cash_payees"]
    SEED_ROWS["public.cash_payees"] = [dict(keep[0], kind="supplier"), dict(keep[0], approved_by=str(U_MK)),
                                       dict(keep[0], last_four=REFUSED_NUMBERS["last5"])]
    try:
        _n, bad2 = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["public.cash_payees"] = keep
    want = {("public.cash_payees", "cash_payees_kind_chk", 0), ("public.cash_payees", "cash_payees_maker_checker_chk", 1),
            ("public.cash_payees", "cash_payees_last_four_chk", 2)}
    check(want <= set(bad2), "the pre-check is not vacuous: an unknown kind, an approver who is a maker and a five-digit "
          "last four are each refused by the same evaluation", f"{bad2}")
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
    check(bool(have == set(UNIQUES) and len(have) > 0 and all("UNIQUE" in v for v in idx.values())),
          f"{len(have)} unique indexes / constraints read (one live payee per household + name, one live payee per "
          "task, the invoice duplicate key, one link per document + role + record, …) — the seed respects each",
          f"missing={sorted(set(UNIQUES) - have)}")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = 'public.cash_payees'::regclass")]
    check(len(fks) >= 12, "cash_payees' foreign keys are read (org, household, creator, approver, tasks, SLOA document, "
          "the archive, every actor)", f"{len(fks)}")
    ops = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                  "tablename = 'cash_payees'")}
    check(ops == {"SELECT", "INSERT", "UPDATE", "DELETE"}, "RLS: a policy for EVERY operation on cash_payees",
          f"{sorted(ops)}")
    trg = {r["tgname"] for r in await platform(
        conn, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.cash_payees'::regclass AND NOT tgisinternal")}
    check({"cash_payees_no_numbers_trg", "cash_payees_guard_trg"} <= trg, "the number guard and the status guard exist",
          f"{sorted(trg)}")
    cols = {r["column_name"] for r in await platform(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = "
              "'payment_instructions'")}
    check(bool(cols) and "payee_id" not in cols and "destination_payee_id" not in cols,
          "payment_instructions has no payee column — no instruction can carry a payee (the first-payment gate is "
          "dormant by construction)", f"{len(cols)} columns")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{by}")
    snap = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    badc = []
    for t, cs in SEED_COLUMNS.items():
        mm = re.search(rf"^-- ===== {re.escape(t)} =====\n((?:--.*\n)+)", snap, re.M)
        have_c = set(re.findall(r"^--\s{3}(\w+)\s", mm.group(1), re.M)) if mm else set()
        miss = [c for c in cs if c not in have_c]
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
    p = int(await platform(conn, "SELECT count(*) FROM cash_payees WHERE display_name LIKE $1", NAME + "%",
                           fetch="fetchval"))
    d = int(await platform(conn, "SELECT count(*) FROM documents WHERE original_filename LIKE $1 OR id::text LIKE $2",
                           f"{TAG}%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    c = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1", CUSTODIAN,
                           fetch="fetchval"))
    return check(o == 0 and u == 0 and e == 0 and p == 0 and d == 0 and c == 0,
                 "the reserved prefixes (fixture ids, sign-in tag, 'CASH08 VERIFY' names, document names, the custodian "
                 "code) match no real row", f"orgs={o} users={u} entities={e} payees={p} documents={d} caps={c}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH08 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH08 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH08 {short.upper()}", sub_of(uid), account_role)
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
        for uid in (U_MK, U_CK, U_CK2, U_VIEW):
            for e in (E1, E1B, E2):
                await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                                   "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, e, uid)
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                           "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, E2, U_RS)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'staff_visibility.enforced', 'true'::jsonb, 'general')", ORG_A)
        for key, (org, hh, ent, masked) in ACCOUNTS.items():
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "custodian_account_id, registration_type, tax_status, primary_entity_id, household_id, "
                "is_discretionary, is_held_away) VALUES ($1, $2, $3, $4, $5, $6, 'trust', 'taxable', $7, $8, true, "
                "false)", A[key], org, masked, f"{TAG}-hash-{key}", CUSTODIAN, f"{TAG}-ref-{key}", ent, hh)
        for key, (org, ent, fname) in DOCS.items():
            await conn.execute(
                "INSERT INTO documents (id, org_id, entity_id, original_filename, source, mime_type, status, "
                "created_by) VALUES ($1, $2, $3, $4, 'upload', 'application/pdf', 'stored', $5)", DOC[key], org, ent,
                fname, U_MK if org == ORG_A else U_B)
        for row in SEED_ROWS["public.custodian_capabilities"]:
            await conn.execute(
                "INSERT INTO custodian_capabilities (custodian_code, capability_key, value, evidence_kind, evidence, "
                "as_of, version_no) VALUES ($1, $2, $3, $4, $5, $6, $7)", row["custodian_code"], row["capability_key"],
                row["value"], row["evidence_kind"], row["evidence"], TODAY, row["version_no"])
        for u, e, t in GRANTS:
            await conn.execute("INSERT INTO trading_authority_grants (org_id, entity_id, user_id, authority_tier) "
                               "VALUES ($1, $2, $3, $4)", USERS[u][0], e, u, t)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    ok_test = check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true "
                    "(privileged insert path)", f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/{len(FIXTURE_ORGS)} orgs")
    nd = int(await platform(conn, "SELECT count(DISTINCT original_filename) FROM documents WHERE id = ANY($1::uuid[])",
                            list(DOC.values()), fetch="fetchval"))
    ok_docs = check(nd == len(DOC), "each fixture document has distinct content (its own name)", f"{nd}/{len(DOC)}")
    ok_roles = await check_fixture_roles(conn)
    S["seeded"] = bool(ok_test and ok_docs and ok_roles)


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
                # cash06 fix 1: internal exactly for the roles authz classifies as internal
                ok = ok and bool(pr.is_internal) is (rname in AZ.INTERNAL_ROLE_NAMES)
                ok = ok and ("manage_payees" in perms) is (rname in ("advisor", "fund_finance"))
                ok = ok and ("approve_payees" in perms) is (rname in ("advisor", "compliance"))
            if not ok:
                bad.append((short, names, sorted(perms & {"manage_payees", "approve_payees", "view_portfolio"}),
                            bool(pr and pr.is_internal), bool(pr and pr.is_super_admin)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisors manage + approve "
                 "payees; compliance approves only; fund finance manages only; the member, the viewer as named; TWO "
                 "Super Admins; no staff bypass)", f"{bad}")


# ── payees ──────────────────────────────────────────────────────────────────────────────────────────────────────
async def create(uid=U_MK, hh=H1, **over) -> tuple[int, dict]:
    return await api(uid, "POST", pe(hh), {**GOOD_PAYEE, **over})


async def act(uid, pid, action, hh=H1, **b) -> tuple[int, dict]:
    return await api(uid, "POST", pe(hh, f"/{pid}/{action}"), b)


async def y_payees(conn):
    section("[Y] Payees: create → the creator cannot approve (refused, recorded); a second eligible person approves; "
            "Super Admin cannot self-approve; bi-temporal; RLS")
    c, b = await create()
    p = payee_of(b)
    pid = p.get("id")
    S["payee"]["P1"] = pid
    row = await live(conn, pid) if pid else None
    check(c == 201 and row is not None and row["status"] == "draft" and str(row["created_by"]) == str(U_MK)
          and row["last_four"] == "0042" and row["payee_reference"] == GOOD_PAYEE["payee_reference"],
          "a staff member with manage_payees creates a DRAFT payee — re-read from an independent connection",
          f"{c} {detail_text(b)[:200]} {row and row['status']}")
    check(p.get("last_four") == "0042" and "payee_reference" in p and "tax_id" not in p and "ssn" not in p,
          "the response carries the last four and the custodian's reference; no tax id field exists", dumps(p)[:200])
    cs, bs = await act(U_MK, pid, "submit")
    row = await live(conn, pid)
    task = row.get("approval_task_id") if row else None
    tk = await platform(conn, "SELECT id FROM workflow_run_steps WHERE id = $1::uuid AND org_id = $2::uuid", task,
                        ORG_A, fetch="fetchval") if task else None
    check(cs == 200 and row["status"] == "pending_approval" and tk is not None,
          "submit → pending_approval with its approval task (the inbox)", f"{cs} {detail_text(bs)[:200]}")
    r0 = await refusals(conn, sod.PAYEE, pid)
    ca, ba = await act(U_MK, pid, "approve")
    row = await live(conn, pid)
    r1 = await refusals(conn, sod.PAYEE, pid)
    check(ca == 403 and "maker-checker" in detail_text(ba) and row["status"] == "pending_approval"
          and row["approved_by"] is None and r1 > r0,
          "the CREATOR cannot approve: 403 (S30 floor), the payee unchanged, the refusal recorded in sod_decisions",
          f"{ca} {detail_text(ba)[:200]} {r0}->{r1}")
    # the inbox path refuses the maker too
    with _ctx(ORG_A):
        pool = await get_pool()
        try:
            await HT.complete_task(pool, org_id=str(ORG_A), task_id=task, actor_id=str(U_MK), outcome="approve")
            inbox_refused = False
        except (HT.MakerCheckerError, CP.MakerCheckerViolation, sod.SoDRefused):
            inbox_refused = True
    row = await live(conn, pid)
    check(inbox_refused and row["status"] == "pending_approval", "the generic inbox path refuses the creator as well",
          f"{inbox_refused} {row['status']}")
    cv, bv = await act(U_VIEW, pid, "approve")
    check(cv == 403 and "approve_payees" in detail_text(bv), "a view-only caller cannot approve (403 naming "
          "approve_payees)", f"{cv} {detail_text(bv)[:160]}")
    cc, bc = await act(U_CK, pid, "approve")
    row = await live(conn, pid)
    check(cc == 200 and row["status"] == "active" and str(row["approved_by"]) == str(U_CK)
          and row["approved_at"] is not None, "a SECOND eligible person approves → active (approved_by recorded)",
          f"{cc} {detail_text(bc)[:200]}")
    st = await states(conn, pid)
    live_rows = [s_ for s_ in st if s_["system_to"] is None]
    check(bool(len(st) >= 3 and len(live_rows) == 1 and str(st[-1]["id"]) == str(pid)
               and all(str(s_["id"]) != str(pid) for s_ in st[:-1])
               and {"draft", "pending_approval", "active"} <= {s_["status"] for s_ in st}),
          "bi-temporal (system axis): the live row keeps its id; every earlier state (draft, pending) is an archived copy",
          f"{[(s_['status'], s_['system_to'] is None) for s_ in st]}")
    # Super Admin cannot self-approve
    c2, b2 = await create(uid=U_SA, display_name=NAME + "Bay Charity", kind="charity", last_four="0917",
                          payee_reference="STD-BAY-AB")
    p2 = payee_of(b2).get("id")
    S["payee"]["P2"] = p2
    await act(U_SA, p2, "submit")
    r0 = await refusals(conn, sod.PAYEE, p2)
    c3, b3 = await act(U_SA, p2, "approve")
    row2 = await live(conn, p2) if p2 else {}
    r1 = await refusals(conn, sod.PAYEE, p2)
    check(c2 == 201 and c3 == 403 and row2.get("status") == "pending_approval" and r1 > r0,
          "a SUPER ADMIN cannot approve their own payee (403, floor — Super Admin included), recorded",
          f"{c2} {c3} {detail_text(b3)[:200]} {r0}->{r1}")
    c4, _ = await act(U_CK2, p2, "approve")
    check(c4 == 200 and (await live(conn, p2))["status"] == "active", "another eligible checker (compliance) approves it")
    # the database backstop: an approver who is a maker
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "UPDATE cash_payees SET approved_by = created_by WHERE id = $1::uuid AND system_to IS NULL", pid))
    check(isinstance(err, asyncpg.CheckViolationError) and "maker_checker" in (getattr(err, "constraint_name", "") or ""),
          "the database refuses an approver who is a maker (cash_payees_maker_checker_chk)", f"{type(err).__name__}")
    _r, err2 = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "DELETE FROM cash_payees WHERE id = $1::uuid", pid))
    check(err2 is not None and getattr(err2, "sqlstate", None) == "42501", "a payee is never deleted (retire it)",
          f"{type(err2).__name__}")
    acts = await audits(conn, "cash.payee.approved", pid)
    check(acts == 1 and await audits(conn, "cash.payee.created", pid) == 1 and await audits(conn, "cash.payee.submitted",
                                                                                                pid) == 1,
          "every action is audited (created, submitted, approved — once each)", f"{acts}")


async def y_bank(conn):
    section("[Y] Bank change: suspends; approval needs the call-back attestation to the number ON FILE; never the "
            "request's number; never the requester; history kept bi-temporally")
    pid = S["payee"].get("P1")
    before = await live(conn, pid)
    change = {"changes": {"last_four": "0777", "payee_reference": "STD-HARBOR-CD", "bank_name": "Second Fixture Bank"},
              "request_contact": f"email from ap@harbor.example, call {REQUEST_PHONE}", "request_phone": REQUEST_PHONE,
              "reason": "the vendor says it changed banks"}
    c, b = await act(U_MK, pid, "request_bank_change", **change)
    row = await live(conn, pid)
    flags = [f.get("key") for f in payee_of(b).get("flags") or []]
    check(c == 200 and row["status"] == "suspended" and row["last_four"] == "0042"
          and row["payee_reference"] == before["payee_reference"] and row["pending_last_four"] == "0777"
          and row["bank_change_task_id"] is not None and "bank_change_pending" in flags,
          "a bank-detail change SUSPENDS the payee; the approved details stand; the new ones wait as pending; flagged",
          f"{c} {detail_text(b)[:200]} {row['status']}")
    good = {"person": "A. Clerk, Harbor accounts", "number": "(212) 555-0100",
            "called_at": datetime.now(timezone.utc).isoformat()}

    async def approve(uid, att, note=None):
        body = dict(att or {})
        if note:
            body["note"] = note
        return await api(uid, "POST", pe(H1, f"/{pid}/bank-change/approve"), body)

    n0 = await audits(conn, "cash.payee.refused", pid)
    c1, b1 = await approve(U_CK, {})
    r1 = await live(conn, pid)
    check(c1 == 422 and "call-back attestation" in detail_text(b1) and r1["status"] == "suspended"
          and r1["last_four"] == "0042", "approval WITHOUT a call-back attestation → refused; still suspended, unchanged",
          f"{c1} {detail_text(b1)[:200]}")
    c2, b2 = await approve(U_CK, {**good, "number": REQUEST_PHONE.replace("-", " ")})
    r2 = await live(conn, pid)
    check(c2 == 422 and "given in the change request" in detail_text(b2) and r2["last_four"] == "0042",
          "a call-back number that matches the REQUEST's contact (not the one on file) → refused", f"{c2} "
          f"{detail_text(b2)[:200]}")
    c3, b3 = await approve(U_CK, {**good, "number": OTHER_PHONE})
    check(c3 == 422 and "not the number on file" in detail_text(b3), "a number that is not on file → refused",
          f"{c3} {detail_text(b3)[:200]}")
    n1 = await audits(conn, "cash.payee.refused", pid)
    check(n1 >= n0 + 3, "each attestation refusal is audited (cash.payee.refused)", f"{n0}->{n1}")
    r0 = await refusals(conn, sod.PAYEE_BANK_CHANGE, pid)
    c4, b4 = await approve(U_MK, good)
    r4 = await live(conn, pid)
    rr = await refusals(conn, sod.PAYEE_BANK_CHANGE, pid)
    check(c4 == 403 and "requested this bank-detail change" in detail_text(b4) and r4["status"] == "suspended" and rr > r0,
          "the REQUESTER cannot approve, even with a proper attestation (403, floor), recorded", f"{c4} "
          f"{detail_text(b4)[:200]} {r0}->{rr}")
    with _ctx(ORG_A):
        pool = await get_pool()
        try:
            await HT.complete_task(pool, org_id=str(ORG_A), task_id=r4["bank_change_task_id"], actor_id=str(U_CK),
                                   outcome="approve")
            bypass = "completed"
        except CP.PayeeError as exc:
            bypass = f"refused: {exc}"
        except Exception as exc:  # noqa: BLE001
            bypass = f"{type(exc).__name__}: {exc}"
    r5 = await live(conn, pid)
    check(bypass.startswith("refused") and "call-back attestation" in bypass and r5["status"] == "suspended"
          and r5["bank_change_task_id"] == r4["bank_change_task_id"],
          "the generic inbox cannot approve a bank change (no attestation): refused, the task stays open", bypass[:200])
    c6, b6 = await approve(U_CK, good, note="called back on the number on file")
    r6 = await live(conn, pid)
    check(c6 == 200 and r6["status"] == "active" and r6["last_four"] == "0777"
          and r6["payee_reference"] == "STD-HARBOR-CD" and r6["bank_name"] == "Second Fixture Bank"
          and r6["pending_last_four"] is None and r6["bank_change_task_id"] is None
          and str(r6["bank_change_approved_by"]) == str(U_CK) and r6["callback_person"] == good["person"]
          and r6["callback_number"] == good["number"] and r6["callback_at"] is not None and r6["id"] == before["id"],
          "a proper attestation by ANOTHER person → active again with the new details; who / which number / when "
          "recorded; the id kept", f"{c6} {detail_text(b6)[:200]}")
    st = await states(conn, pid)
    fours = [s["last_four"] for s in st]
    check("0042" in fours and fours[-1] == "0777" and any(s["status"] == "suspended" and s["pending_last_four"] == "0777"
                                                          for s in st)
          and sum(1 for s in st if s["system_to"] is None) == 1,
          "history kept bi-temporally: the old details, the suspended state with the pending change, the new details",
          f"{[(s['status'], s['last_four'], s['pending_last_four']) for s in st]}")
    hist = (payee_of((await api(U_CK, "GET", pe(H1, f"/{pid}")))[1]).get("history") or [])
    check(len(hist) == len(st) and hist[-1].get("current") is True, "the detail screen's history is every state",
          f"{len(hist)} vs {len(st)}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "UPDATE cash_payees SET last_four = '0999' WHERE id = $1::uuid AND system_to IS NULL", pid))
    check(err is not None and getattr(err, "sqlstate", None) == "42501" and "bank-detail change" in str(err),
          "the database refuses a bank-detail change in place (only an approved change moves them)",
          f"{type(err).__name__} {str(err)[:120]}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "UPDATE cash_payees SET last_four = '0999', bank_change_approved_at = now() WHERE id = $1::uuid AND system_to "
        "IS NULL", pid))
    check(err is not None and getattr(err, "sqlstate", None) == "42501",
          "…even when the same UPDATE also stamps an approval time (no open change, no pending details)",
          f"{type(err).__name__}")
    # reject keeps the approved details
    c7, _ = await act(U_MK, pid, "request_bank_change", **{**change, "changes": {"last_four": "0888",
                                                                                   "payee_reference": "STD-HARBOR-EF"}})
    c8, b8 = await api(U_CK, "POST", pe(H1, f"/{pid}/bank-change/reject"), {"note": "the vendor did not confirm"})
    r8 = await live(conn, pid)
    check(c7 == 200 and c8 == 200 and r8["status"] == "active" and r8["last_four"] == "0777"
          and r8["pending_last_four"] is None,
          "a rejected change: the pending details are discarded, the approved ones stand, the payee active again",
          f"{c7} {c8} {detail_text(b8)[:160]}")


async def y_numbers(conn):
    section("[Y] Numbers: the DB refuses a 5-digit last four, a 9-digit run in reference / notes, a full account number "
            "in ANY field; no response, audit row or log line carries a fixture number")
    cases = [("last_four", REFUSED_NUMBERS["last5"]), ("payee_reference", f"STD {REFUSED_NUMBERS['aba']}"),
             ("payee_reference", f"STD {REFUSED_NUMBERS['acct_sep']}"), ("notes", f"acct {REFUSED_NUMBERS['acct']}")]
    for field, val in cases:
        c, b = await create(display_name=NAME + f"refused {field} {len(val)}", **{field: val})
        check(c == 422 and field in detail_text(b), f"the API refuses {field} = <{len(re.sub(r'[^0-9]', '', val))} "
              "digits> (a VALID body otherwise) with the column named", f"{c} {detail_text(b)[:160]}")
    cols = sorted(set(CP.FREE_FIELDS) | set(CP.PHONE_FIELDS) | {"last_four", "pending_last_four"})
    bad = []
    for col in cols:
        def ins(c_, col=col):
            return c_.execute(
                f"INSERT INTO cash_payees (org_id, household_id, kind, display_name, created_by, maker_ids, {col}) "
                "VALUES ($1::uuid, $2::uuid, 'vendor', $3, $4::uuid, ARRAY[$4::uuid], $5)" if col != "display_name"
                else "INSERT INTO cash_payees (org_id, household_id, kind, display_name, created_by, maker_ids) VALUES "
                     "($1::uuid, $2::uuid, 'vendor', $3, $4::uuid, ARRAY[$4::uuid])",
                *((str(ORG_A), str(H1), f"{NAME}x {REFUSED_NUMBERS['acct']}", str(U_MK)) if col == "display_name"
                  else (str(ORG_A), str(H1), NAME + f"db {col}", str(U_MK), f"x {REFUSED_NUMBERS['acct']}")))
        _r, err = await in_rolled_back(conn, ORG_A, ins)
        if not (err is not None and getattr(err, "sqlstate", None) == "23514" and col in str(err).split("\n", 1)[0]):
            bad.append((col, type(err).__name__ if err else None))
    check(bool(cols) and not bad, f"the DATABASE refuses a full account number in every one of the {len(cols)} "
          "free-text / phone / last-four columns, naming the column (23514)", f"{bad}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "INSERT INTO cash_payees (org_id, household_id, kind, display_name, created_by, maker_ids, last_four) VALUES "
        "($1::uuid, $2::uuid, 'vendor', $3, $4::uuid, ARRAY[$4::uuid], $5)", str(ORG_A), str(H1), NAME + "db last5",
        str(U_MK), REFUSED_NUMBERS["last5"]))
    msg = str(err) if err else ""
    check(err is not None and REFUSED_NUMBERS["last5"] not in msg and "DETAIL" not in msg.upper().split("\n", 1)[0],
          "the database's own refusal of a 5-digit last four never echoes the value (the guard fires ahead of the CHECK)",
          f"{type(err).__name__}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "INSERT INTO cash_payees (org_id, household_id, kind, display_name, created_by, maker_ids, payee_reference) "
        "VALUES ($1::uuid, $2::uuid, 'vendor', $3, $4::uuid, ARRAY[$4::uuid], $5)", str(ORG_A), str(H1),
        NAME + "db aba", str(U_MK), f"r {REFUSED_NUMBERS['acct_sep']}"))
    check(err is not None and "nine or more digits" in str(err), "a 9-digit ABA written with separators is refused by "
          "the database too", f"{type(err).__name__}")
    _r, ok = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "INSERT INTO cash_payees (org_id, household_id, kind, display_name, created_by, maker_ids, last_four, "
        "contact_phone, payee_reference) VALUES ($1::uuid, $2::uuid, 'vendor', $3, $4::uuid, ARRAY[$4::uuid], '0042', "
        "$5, 'STD-OK-12')", str(ORG_A), str(H1), NAME + "db ok", str(U_MK), ON_FILE))
    check(ok is None, "…and accepts the same row with a four-digit last four, a separated phone and a short reference "
          "(the guard is not vacuous)", f"{type(ok).__name__ if ok else ''}")
    S["numbers_done"] = True


async def scan_leaks(conn):
    allb = "\n".join(b_ for _w, b_ in BODIES)
    audit = "\n".join(r["p"] for r in await platform(
        conn, "SELECT action || ' ' || payload::text AS p FROM audit_log WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    runs = "\n".join(r["c"] for r in await platform(
        conn, "SELECT context::text AS c FROM workflow_runs WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    evs = "\n".join(r["p"] for r in await platform(
        conn, "SELECT payload::text AS p FROM domain_events WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    sodr = "\n".join(r["r"] for r in await platform(
        conn, "SELECT reason AS r FROM sod_decisions WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    stored = "\n".join(r["t"] for r in await platform(
        conn, "SELECT t::text AS t FROM cash_payees t WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    logs = "\n".join(LOG_LINES)
    leaks = {k: number_leaks(v) for k, v in (("responses", allb), ("audit", audit), ("tasks", runs), ("events", evs),
                                              ("sod_decisions", sodr), ("stored payees", stored), ("log lines", logs))}
    check(bool(BODIES) and bool(audit) and bool(stored) and not any(leaks.values()),
          f"no refusal-input number (raw or separated) in ANY of the {len(BODIES)} responses, the audit rows, the "
          f"approval tasks, the events, the SoD log, the stored payees or the {len(LOG_LINES)} log lines",
          f"{ {k: v for k, v in leaks.items() if v} }")
    runs5 = sorted({m for txt in (allb,) for m in re.findall(r"(?<![0-9a-f-])[0-9]{5,}(?![0-9a-f-])",
                                                              re.sub(r'"[^"]*(_at|_on|date|from|to|today|until)"\s*:\s*"[^"]*"', "", txt))})
    if runs5:
        find("digit runs over four in the responses (none from a payee — dates / amounts / ids?)", f"{runs5[:8]}")


async def y_sloa(conn):
    section("[Y] SLOA: attach / expire / revoke flag the payee; an expired SLOA is flagged; a link in Chancery")
    pid = S["payee"].get("P2")

    async def flags(uid=U_CK) -> tuple[list, dict]:
        _c, b = await api(uid, "GET", pe(H1, f"/{pid}"))
        p = payee_of(b)
        return [f.get("key") for f in p.get("flags") or []], p

    f0, _p0 = await flags()
    check("sloa_missing" in f0, "a payee with no SLOA is flagged sloa_missing", f"{f0}")
    body = {"document_id": str(DOC["SLOA"]), "account_ids": [str(A["A1"])],
            "signed_on": (TODAY - timedelta(days=30)).isoformat(), "expires_on": (TODAY + timedelta(days=200)).isoformat()}
    c, b = await act(U_MK, pid, "attach_sloa", **body)
    f1, p1 = await flags()
    link = await platform(conn, "SELECT count(*) FROM document_record_links WHERE document_id = $1::uuid AND "
                                "record_type = $2 AND record_id = $3::uuid", DOC["SLOA"], CP.LINK_SLOA, pid,
                          fetch="fetchval")
    check(c == 200 and not any(k.startswith("sloa_") for k in f1) and (p1.get("sloa") or {}).get("state") == "sloa_current"
          and int(link) == 1 and (p1.get("sloa") or {}).get("blocks_payment") is None,
          "attach: the Chancery document is linked (document_record_links 'cash_payee_sloa'), no SLOA flag, would not "
          "block payment", f"{c} {detail_text(b)[:160]} {f1}")
    cx, bx = await act(U_MK, pid, "attach_sloa", **{**body, "account_ids": [str(A["A4"])]})
    check(cx == 422 and "household" in detail_text(bx), "an SLOA source account must be the household's own",
          f"{cx} {detail_text(bx)[:120]}")
    c2, _ = await act(U_MK, pid, "expire_sloa")
    f2, p2 = await flags()
    check(c2 == 200 and "sloa_expired" in f2 and bool((p2.get("sloa") or {}).get("blocks_payment")),
          "expire → flagged sloa_expired (and it would block payment)", f"{c2} {f2}")
    c3, _ = await act(U_MK, pid, "attach_sloa", **{**body, "document_id": str(DOC["OTHER"]),
                                                   "signed_on": (TODAY - timedelta(days=400)).isoformat(),
                                                   "expires_on": (TODAY - timedelta(days=1)).isoformat()})
    f3, _ = await flags()
    check(c3 == 200 and "sloa_expired" in f3, "an SLOA whose expiry date has passed (today injected) is flagged expired",
          f"{c3} {f3}")
    c4, _ = await act(U_MK, pid, "attach_sloa", **{**body, "expires_on": (TODAY + timedelta(days=10)).isoformat()})
    f4, p4 = await flags()
    check(c4 == 200 and "sloa_expiring_soon" in f4 and (p4.get("sloa") or {}).get("blocks_payment") is None,
          "an SLOA expiring within 30 days is flagged (not blocking)", f"{f4}")
    c5, _ = await act(U_MK, pid, "revoke_sloa", reason="the member revoked it with the custodian")
    f5, p5 = await flags()
    check(c5 == 200 and "sloa_revoked" in f5 and bool((p5.get("sloa") or {}).get("blocks_payment")),
          "revoke (with a reason) → flagged sloa_revoked (it would block payment)", f"{c5} {f5}")
    row = await live(conn, pid)
    check(CP.sloa_blocks_payment(row, TODAY) is not None and CP.sloa_blocks_payment(
        {**row, "sloa_revoked_at": None, "sloa_expired_at": None, "sloa_expires_on": TODAY + timedelta(days=90)},
        TODAY, A["A4"]) is not None,
          "sloa_blocks_payment: a revoked SLOA blocks; an SLOA not covering the source account blocks")
    cl, bl = await api(U_CK, "GET", pe(H1))
    lp = next((i for i in bl.get("items") or [] if i.get("id") == pid), {})
    check(cl == 200 and "sloa_revoked" in [f.get("key") for f in lp.get("flags") or []]
          and (lp.get("sloa") or {}).get("state") == "sloa_revoked", "the list carries the SLOA state and flags",
          f"{cl} {lp.get('flags')}")
    check(await audits(conn, "cash.payee.sloa_attached", pid) == 3 and await audits(conn, "cash.payee.sloa_revoked", pid)
          == 1 and await audits(conn, "cash.payee.sloa_expired", pid) == 1, "every SLOA action is audited")


async def y_thirdparty(conn):
    section("[Y] Third-party gate: off by default; a payment to a payee → 'third-party payments are not enabled'; one "
            "Super Admin, or no counsel document → refused; still off at the end")
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            on0 = await CP.third_party_enabled(c_, ORG_A)
            raw0 = await CP.stored_switch_value(c_, ORG_A)
    check(on0 is False and raw0 is None, "off by default (no stored value; the reader says off)", f"{on0} {raw0}")
    pid = S["payee"].get("P1")
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM payment_instructions WHERE org_id = $1::uuid", ORG_A,
                          fetch="fetchval"))
    base = {"source_account_id": str(A["A1"]), "amount": "500.00", "rail": "ach", "value_date": TODAY.isoformat(),
            "purpose": NAME + "pay the roofer"}
    c, b = await api(U_MK, "POST", f"/api/v1/cash/households/{H1}/payments", {**base, "destination_payee_id": pid})
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM payment_instructions WHERE org_id = $1::uuid", ORG_A,
                          fetch="fetchval"))
    ref = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1::uuid AND action = "
                                   "'cash.payment.refused' AND payload::text LIKE $2", ORG_A, f"%{PS.NOT_ENABLED}%",
                             fetch="fetchval"))
    check(c == 403 and detail_text(b) == PS.NOT_ENABLED and n1 == n0 and ref >= 1,
          "a payment instruction to a PAYEE → refused 'third-party payments are not enabled' (403); no instruction "
          "written; the refusal audited", f"{c} {detail_text(b)[:160]} {n0}->{n1} {ref}")
    c2, b2 = await api(U_MK, "POST", f"/api/v1/cash/households/{H1}/payments",
                       {**base, "destination_account_id": str(A["A2"])})
    p2 = (b2.get("payment") or {}).get("id")
    S["pay"]["twin"] = p2
    check(c2 == 201 and bool(p2), "the SAME body to a same-owner account is accepted (201) — only the third-party gate "
          "refused the payee one", f"{c2} {detail_text(b2)[:200]}")
    if p2:
        await api(U_MK, "POST", f"/api/v1/cash/households/{H1}/payments/{p2}/cancel", {"reason": "verify twin"})
    with _ctx(ORG_A):
        pool = await get_pool()
        try:
            await PS.edit(pool, org_id=str(ORG_A), actor_id=str(U_MK), payment_id=p2,
                          changes={"destination_payee_id": pid})
            e_ref = "accepted"
        except PS.ThirdPartyRefused as exc:
            e_ref = str(exc)
        except Exception as exc:  # noqa: BLE001
            e_ref = f"{type(exc).__name__}: {exc}"
    check(e_ref == PS.NOT_ENABLED, "editing an instruction to point at a payee is refused by the same gate", e_ref[:160])
    # the switch: who may ask
    sw = "/api/v1/cash/third-party"
    c3, b3 = await api(U_MK, "POST", sw + "/request", {"counsel_document_id": str(DOC["COUNSEL"]), "reason": "verify"})
    with _ctx(ORG_A):
        pool = await get_pool()
        try:
            await CP.request_third_party_switch(pool, org_id=str(ORG_A), actor_id=str(U_MK),
                                                counsel_document_id=str(DOC["COUNSEL"]), reason="verify")
            svc = "accepted"
        except CP.PayeeRefused as exc:
            svc = f"refused: {exc}"
    check(c3 == 403 and svc.startswith("refused") and "Super Admin" in svc,
          "a non-Super-Admin cannot ask (route 403; the service refuses on its own too)", f"{c3} {svc[:120]}")
    c4, b4 = await api(U_SA, "POST", sw + "/request", {"reason": "counsel not yet"})
    c5, b5 = await api(U_SA, "POST", sw + "/request", {"counsel_document_id": str(DOC["B"]), "reason": "wrong org"})
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            t_none = await CP.switch_task(c_, ORG_A)
    check(c4 == 422 and "counsel" in detail_text(b4) and c5 == 422 and t_none is None,
          "without the counsel document (or with another org's) → refused; no request opened", f"{c4} {c5} "
          f"{detail_text(b4)[:120]}")
    c6, b6 = await api(U_SA, "POST", sw + "/request", {"counsel_document_id": str(DOC["COUNSEL"]),
                                                       "reason": "verify: two Super Admins and counsel required"})
    task = b6.get("task_id")
    lk = int(await platform(conn, "SELECT count(*) FROM document_record_links WHERE document_id = $1::uuid AND "
                                  "record_type = $2 AND record_id = $3::uuid", DOC["COUNSEL"], CP.LINK_COUNSEL, ORG_A,
                            fetch="fetchval"))
    check(c6 == 201 and bool(task) and lk == 1, "a Super Admin with counsel's document opens the request (the "
          "document linked 'third_party_counsel_signoff'); nothing changes yet", f"{c6} {detail_text(b6)[:160]}")
    r0 = await refusals(conn, sod.THIRD_PARTY_SETTING, task)
    c7, b7 = await api(U_SA, "POST", sw + "/approve", {})
    r1 = await refusals(conn, sod.THIRD_PARTY_SETTING, task)
    check(c7 == 403 and "DIFFERENT Super Admin" in detail_text(b7) and r1 > r0,
          "ONE Super Admin cannot turn it on (approving their own request: 403, floor), recorded", f"{c7} "
          f"{detail_text(b7)[:160]} {r0}->{r1}")
    with _ctx(ORG_A):
        pool = await get_pool()
        try:
            await CP.decide_third_party_switch(pool, org_id=str(ORG_A), actor_id=str(U_CK), outcome="approve")
            nsa = "accepted"
        except (CP.PayeeRefused, PermissionError) as exc:
            nsa = f"refused: {exc}"
    check(nsa.startswith("refused") and "Super Admin" in nsa, "a non-Super-Admin cannot decide it", nsa[:160])
    c8, b8 = await api(U_SA2, "POST", sw + "/approve", {})
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            still = await CP.switch_task(c_, ORG_A)
            on1 = await CP.third_party_enabled(c_, ORG_A)
            raw1 = await CP.stored_switch_value(c_, ORG_A)
    check(c8 == 403 and "cannot be turned on in this release" in detail_text(b8) and still is not None
          and still["id"] == task and on1 is False and raw1 is None,
          "a SECOND Super Admin with counsel's document on file: refused in this release (the lock) — the request stays "
          "open, the setting unwritten, the switch off", f"{c8} {detail_text(b8)[:160]}")
    cs, bs = await api(U_SA2, "GET", sw)
    check(cs == 200 and bs.get("enabled") is False and bs.get("release_locked") is True
          and (bs.get("open_request") or {}).get("task_id") == task, "the switch's state read: off, locked, the request",
          f"{cs} {dumps(bs)[:200]}")
    c9, b9 = await api(U_SA2, "POST", sw + "/reject", {"note": "counsel has not signed off"})
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            gone = await CP.switch_task(c_, ORG_A)
    check(c9 == 200 and gone is None, "the second Super Admin may reject it (the request closes; nothing changes)",
          f"{c9} {detail_text(b9)[:160]}")
    S["switch_task"] = task


async def y_firstpayment(conn):
    section("[Y] First payment to a new payee — unit-proved on the dormant path (checker ≠ creator ≠ approver)")
    check(CP.first_payment_conflict(U_MK, U_MK, U_CK) is not None and CP.first_payment_conflict(U_CK, U_MK, U_CK)
          is not None and CP.first_payment_conflict(U_CK2, U_MK, U_CK) is None and CP.first_payment_conflict(None, U_MK,
                                                                                                             U_CK)
          is not None, "pure rule: the creator refused, the approver refused, a third person allowed, nobody refused")
    check(CP.first_payment_applies(0) is True and CP.first_payment_applies(1) is False,
          "the rule applies to the FIRST payment only")
    pid = S["payee"].get("P1")
    row = await live(conn, pid)
    out = {}
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            for who, uid in (("creator", U_MK), ("approver", U_CK), ("third", U_CK2)):
                try:
                    d = await CP.first_payment_gate(c_, org_id=str(ORG_A), payee=row, checker_id=str(uid),
                                                    item_id=pid, household_id=H1)
                    out[who] = d.outcome
                except CP.MakerCheckerViolation:
                    out[who] = "refused"
    n = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE org_id = $1::uuid AND item_type = $2 AND "
                                 "outcome = 'refused' AND item_id = $3", ORG_A, sod.FIRST_PAYEE_PAYMENT, str(pid),
                           fetch="fetchval"))
    check(out == {"creator": "refused", "approver": "refused", "third": sod.ALLOWED} and n == 2,
          "through services.sod (item first_payment_to_new_payee, floor): the payee's creator and approver are refused "
          "(recorded), a third person is allowed", f"{out} {n}")
    it = await platform(conn, "SELECT floor_class, self_approval_capable FROM sod_item_types WHERE item_type = $1",
                        sod.FIRST_PAYEE_PAYMENT, fetch="fetchrow")
    src = _code_only(_src("apps/api/services/payments.py"))
    check(it is not None and it["floor_class"] == "transfer" and not it["self_approval_capable"]
          and 'if row.get("payee_id"):' in src and "first_payment_gate(" in src,
          "the floor item is in the catalog and wired into payments' approval (dormant: no instruction has a payee)",
          f"{dict(it) if it else None}")


async def y_invoices(conn):
    section("[Y] Vendor invoices: record from a Chancery invoice → a firm obligation in coverage (cash01 read) with "
            "payee + document links; duplicate refused")
    pid = S["payee"].get("P1")
    c0, b0 = await api(U_MK, "POST", inv(H1, "/tag"), {"document_id": str(DOC["INV1"])})
    c1, b1 = await api(U_MK, "GET", inv(H1))
    aw = [d.get("document_id") for d in b1.get("awaiting") or []]
    check(c0 == 201 and c1 == 200 and str(DOC["INV1"]) in aw and any(a.get("key") == "record" for a in
                                                                   b1.get("actions") or []),
          "a tagged Chancery invoice awaits recording; the record action is offered", f"{c0} {c1} {aw}")
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE org_id = $1::uuid AND source_type = "
                                       "'vendor_invoice'", ORG_A, fetch="fetchval"))
    rec = {"document_id": str(DOC["INV1"]), "payee_id": pid, "entity_id": str(E1), "invoice_number": INVOICE_NO,
           "amount": INVOICE_AMOUNT, "due_date": (TODAY + timedelta(days=20)).isoformat()}
    c2, b2 = await api(U_MK, "POST", inv(H1, "/record"), rec)
    ob = (b2.get("obligation") or {})
    row = await scoped(conn, ORG_A, "SELECT * FROM cash_obligations WHERE id = $1::uuid", ob.get("id"),
                       fetch="fetchrow") if ob.get("id") else None
    links = {r["record_type"]: str(r["record_id"]) for r in await scoped(
        conn, ORG_A, "SELECT record_type, record_id FROM document_record_links WHERE document_id = $1::uuid",
        DOC["INV1"])}
    check(c2 == 201 and row is not None and row["source_type"] == "vendor_invoice" and row["certainty"] == "firm"
          and row["source_ref_type"] == "document" and str(row["source_ref_id"]) == str(DOC["INV1"])
          and row["amount"] == D(INVOICE_AMOUNT) and str(row["entity_id"]) == str(E1)
          and links.get(CP.LINK_PAYEE_INVOICE) == str(pid) and links.get(CP.LINK_OBLIGATION) == str(row["lineage_id"]),
          "recorded: a FIRM vendor_invoice obligation (amount typed, compared as a Decimal), the document as its source, "
          "linked to the payee and to the obligation — re-read independently", f"{c2} {detail_text(b2)[:200]} {links}")
    S["obligation"] = ob.get("id")
    ct, bt = await api(U_MK, "GET", f"/api/v1/cash/households/{H1}/treasury")
    lines = [ln for ln in bt.get("obligations") or [] if ln.get("obligation_id") == ob.get("id")]
    check(ct == 200 and bool(lines) and all(ln.get("certainty") == "firm" and ln.get("source_type") == "vendor_invoice"
                                            and D(str(ln.get("amount"))) == D(INVOICE_AMOUNT) for ln in lines),
          "it appears in COVERAGE at once: cash01's Treasury read carries a firm vendor_invoice line with this "
          "obligation's id and amount (Decimal)", f"{ct} {dumps(bt.get('obligations'))[:300]}")
    c3, b3 = await api(U_MK, "GET", inv(H1))
    check(str(DOC["INV1"]) not in [d.get("document_id") for d in b3.get("awaiting") or []]
          and any(r.get("obligation_id") == ob.get("id") and r.get("payee_id") == pid for r in b3.get("recorded") or []),
          "it leaves the awaiting list and is listed as recorded with its payee")
    await api(U_MK, "POST", inv(H1, "/tag"), {"document_id": str(DOC["INV2"])})
    dup = {**rec, "document_id": str(DOC["INV2"]), "invoice_number": " inv-a7 "}
    c4, b4 = await api(U_MK, "POST", inv(H1, "/record"), dup)
    c5, b5 = await api(U_MK, "POST", inv(H1, "/record"), {**rec, "invoice_number": "INV-A8"})
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE org_id = $1::uuid AND source_type = "
                                       "'vendor_invoice'", ORG_A, fetch="fetchval"))
    check(c4 == 409 and "already recorded" in detail_text(b4) and c5 == 409 and n1 == n0 + 1,
          "a second record of the same payee + invoice number (normalised) + amount → refused (409); the same document "
          "twice → refused; exactly one obligation written", f"{c4} {c5} {n0}->{n1} {detail_text(b4)[:120]}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c_: c_.execute(
        "INSERT INTO cash_obligations (lineage_id, org_id, entity_id, source_type, name, amount, due_date, certainty, "
        "source_ref_type, source_ref_id, external_key) SELECT gen_random_uuid(), org_id, entity_id, source_type, name, "
        "amount, due_date, certainty, source_ref_type, source_ref_id, external_key FROM cash_obligations WHERE id = "
        "$1::uuid", ob.get("id")))
    check(isinstance(err, asyncpg.UniqueViolationError), "the database backstop refuses the duplicate key too "
          "(cash_obligations_external_key_uq)", f"{type(err).__name__ if err else None}")
    c6, _ = await api(U_VIEW, "POST", inv(H1, "/record"), dup)
    c7, _ = await api(U_MEM, "POST", inv(H1, "/record"), dup)
    c8, _ = await api(U_MEM, "GET", inv(H1))
    check(c6 == 403 and c7 == 403 and c8 == 403, "a view-only caller and a member cannot record (403); a member does not "
          "see the invoices screen", f"{c6} {c7} {c8}")
    cd, bd = await create(display_name=NAME + "Draft Gardener", last_four="0313", payee_reference="STD-GARDEN-AB")
    p3 = payee_of(bd).get("id")
    c9, b9 = await api(U_MK, "POST", inv(H1, "/record"), {**dup, "payee_id": p3, "invoice_number": "INV-B1"})
    n2 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_obligations WHERE org_id = $1::uuid AND source_type = "
                                       "'vendor_invoice'", ORG_A, fetch="fetchval"))
    check(cd == 201 and c9 == 422 and "active payee" in detail_text(b9) and n2 == n1,
          "an invoice is recorded only against an APPROVED, active payee (a draft payee → 422; nothing written)",
          f"{cd} {c9} {detail_text(b9)[:120]}")


async def y_visibility(conn):
    section("[Y] Members only their family (read-only: name, kind, last four + cash.third_party); staff only visible "
            "families; org isolation both directions")
    c, b = await api(U_MEM, "GET", pe(H1))
    items = b.get("items") or []
    allowed = {"id", "household_id", "display_name", "kind", "kind_label", "last_four", "status", "status_label",
               "actions"}
    check(c == 200 and b.get("member_view") is True and (b.get("permissions") or {}).get("can_write") is False
          and (b.get("vocabularies") or {}).get("editable") == [] and b.get("actions") == [] and len(items) > 0
          and all(set(i) <= allowed and i.get("actions") == [] for i in items) and all(i.get("status") == "active"
                                                                                         for i in items)
          and "payee_reference" not in dumps(b) and "STD-" not in dumps(b),
          "a MEMBER reads their family's ACTIVE payees READ-ONLY — name, kind, last four, status; no reference, no "
          "contact, no SLOA, no control", f"{c} {dumps(b)[:300]}")
    lab = ((b.get("labels") or {}).get(CP.DISCLOSURE_KEY) or {}).get("text")
    check(bool(lab) and (b.get("third_party") or {}).get("enabled") is False,
          "the member's read carries the cash.third_party text (the library's, or the built-in default)", f"{lab}")
    c2, _ = await api(U_MEM, "GET", pe(H2))
    c3, _ = await create(uid=U_MEM, display_name=NAME + "member try")
    check(c2 == 404 and c3 == 403, "never another family (404); a member can never create (403)", f"{c2} {c3}")
    c5, _ = await api(U_RS, "GET", pe(H1))
    c6, _ = await api(U_RS, "GET", pe(H2))
    c7, b7 = await create(uid=U_VIEW, display_name=NAME + "viewer try")
    check(c5 == 404 and c6 == 200 and c7 == 403 and "manage_payees" in detail_text(b7),
          "restricted staff see only their visible family (H1 404, H2 200); a view-only caller cannot create (403 "
          "naming manage_payees)", f"{c5} {c6} {c7}")
    cb, bb = await api(U_B, "POST", pe(HB), {**GOOD_PAYEE, "display_name": NAME + "Org B vendor"})
    c9, _ = await api(U_B, "GET", pe(H1))
    c10, _ = await api(U_MK, "GET", pe(HB))
    c11, _ = await api(U_B, "GET", pe(H1, f"/{S['payee'].get('P1')}"))
    check(cb == 201 and c9 == 404 and c10 == 404 and c11 == 404,
          "org isolation in the API: org B cannot open org A's household or payee, nor A B's", f"{cb} {c9} {c10} {c11}")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    a_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_payees WHERE org_id = $1", ORG_B, fetch="fetchval"))
    b_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_payees WHERE org_id = $1", ORG_A, fetch="fetchval"))
    a_own = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_payees WHERE org_id = $1", ORG_A, fetch="fetchval"))
    b_own = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_payees WHERE org_id = $1", ORG_B, fetch="fetchval"))
    _r, err = await in_rolled_back(conn, ORG_B, lambda c_: c_.execute(
        "UPDATE cash_payees SET notes = 'x' WHERE org_id = $1", ORG_A))
    upd = _r
    check(by is False and a_b == 0 and b_a == 0 and a_own > 0 and b_own > 0 and err is None and upd == "UPDATE 0",
          "RLS isolation BOTH directions under a non-bypassing role (rolbypassrls = false): A reads none of B's payees, "
          "B none of A's (and updates none); each reads its own", f"{by} {a_b} {b_a} {a_own} {b_own} {upd}")
    await scan_leaks(conn)


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py / visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        if not (HERE / script).exists():
            continue
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=600)
        check(code == 0 and "CURRENT" in out, f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")
    self_static(check, find)
    perms = {n for n, _r, _a in rbac.ORG_ADMIN_DEFAULT_PERMISSIONS}
    check({"manage_payees", "approve_payees"} <= perms, "manage_payees / approve_payees are org_admin defaults")
    check(DS.fallback_text(CP.DISCLOSURE_KEY) == CP.THIRD_PARTY_DISCLOSURE and CP.DISCLOSURE_KEY in DS.KEYS,
          "the cash.third_party key is registered with its built-in fallback")


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
          f"the cash08 render tests ({NODE_TEST}, counted by their file: payee list, detail, new form, bank-change form, "
          f"call-back form, invoices, member read-only, fail closed) all pass ({n.group(1) if n else '?'})",
          f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


#: the nested regressions, ONCE each, flat, after teardown: cash06 (payments' rules — the payments service changed) and
#: cash01 (obligations + coverage, which invoices now write)
REGRESSIONS = ("verify_cash06.py", "verify_cash01.py")


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


FIXTURE_SECTIONS = (("payees", y_payees), ("bank change", y_bank), ("numbers", y_numbers), ("sloa", y_sloa),
                    ("third-party gate", y_thirdparty), ("first payment", y_firstpayment), ("invoices", y_invoices),
                    ("visibility + isolation", y_visibility))
ONLY_NAMES = {"payees": "payees", "bank": "bank change", "numbers": "numbers", "sloa": "sloa",
              "thirdparty": "third-party gate", "firstpayment": "first payment", "invoices": "invoices",
              "visibility": "visibility + isolation"}
#: a section that reads another's payee (S["payee"]) pulls it in (run in FIXTURE_SECTIONS order)
ONLY_NEEDS = {"bank": ("payees",), "sloa": ("payees",), "thirdparty": ("payees",), "firstpayment": ("payees",),
              "invoices": ("payees", "bank"), "visibility": ("payees", "numbers")}


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    bad = [x for x in want if x not in ONLY_NAMES and x != "seed"]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, {', '.join(ONLY_NAMES)}")
    names = {x for x in want if x != "seed"}
    for x in list(names):
        names |= set(ONLY_NEEDS.get(x, ()))
    return [ONLY_NAMES[x] for x in ONLY_NAMES if x in names]


async def switch_off_everywhere(conn) -> None:
    n = int(await platform(conn, "SELECT count(*) FROM org_settings WHERE setting_key = $1 AND setting_value = "
                                 "'true'::jsonb", CP.SETTING_THIRD_PARTY, fetch="fetchval"))
    check(n == 0 and CP.THIRD_PARTY_RELEASE_LOCKED is True,
          "the third-party switch is still OFF at the end — for EVERY organization (no stored true), and locked in code",
          f"{n}")


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
    if "--self-checks-only" in sys.argv:
        try:
            await run("task 1", y_task1, conn)
            await run("routes", y_routes, conn)
        finally:
            await close_conn(conn)
        print(total_line())
        return 0 if _n_fail == 0 else 1
    pre = None
    only = only_sections()
    if only is not None:
        print(f"[INFO] --only: seed + {only or '(seed only)'}")
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
        chosen = [(n, f) for n, f in FIXTURE_SECTIONS if only is None or n in only]
        if S.get("seeded"):
            for name, fn in chosen:
                await run(name, fn, conn)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged; the "
                "switch off")
        PS.set_clock(None)
        CP.set_clock(None)
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (payees, links, documents, obligations, instructions, tasks, "
                  "decisions, grants, audit rows, …)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count (fixture rows)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k]} → {after.get(k)}")
            await switch_off_everywhere(conn)
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
