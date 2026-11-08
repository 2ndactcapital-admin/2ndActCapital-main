"""verify_rep14 — Replication Studio, sprint 14 (REP14, phase 4): the SECTION 351 EXIT (planning side, manual-first).

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep14.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a seeding exception included) and
exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``), the Auth0 profile call,
R2 (``services.storage.upload_bytes`` / ``download_bytes`` → an in-memory dict that refuses an overwrite;
``get_s3_client`` refuses), and the security-data reads for securities an account does not hold
(``rebalance_proposals._unheld_prices`` — fixture prices). NO network (the httpx transport refuses and records), NO
model call (the three AI helpers refuse and record). REAL: the app through an ASGI transport, the 25 / 50 test, the
rep5 proposal engine + rep6 lot method, the human-task engine, maker-checker, the disclosure library, RLS and the
live database. The script's own connection is app_service (rolbypassrls = false, asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-0000001e0001 / 0002, is_test = true, asserted); users seeded with real
user_roles grants (Super Admin through rbac.grant_named_role, org admins through grant_org_admin, a view-only role)
— the app's decision on each asserted; platform securities 'REP14 VERIFY …' with tickers ZZR14…, a fixture CIK
attribute (provider 'zz_rep14_test'), a platform holdings snapshot for the fixture ETF; fixture custodian code
ZZR14C (no capability rows → in_kind_transfers 'unknown'). Every reserved prefix is checked absent from real rows
first and removed BY ID.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with execution.teardown / models.teardown /
replica.teardown = 'fixture', keyed by this run's fixture ids (org_id tables by the fixture orgs; platform rows by
the fixture securities); then every counted table is re-read for its exact pre-test count — audit_log and every
table real activity writes to counted over FIXTURE rows only.

--self-checks-only: git / route self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and the
leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import base64
import csv
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
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep14"
BEFORE_REF = "be844bf"   # rep14-work's base: 'merge disclosures1'
_P = "/api/v1/section351"
_MS, _VP, _AP, _OS, _SA = "manage_proposals", "view_proposals", "approve_proposals", "manage_org_settings", "super_admin"
ROUTES = {
    ("GET", f"{_P}/candidates"): _VP,
    ("GET", f"{_P}/sponsor-profiles"): _VP,
    ("GET", f"{_P}/thresholds"): _VP,
    ("GET", f"{_P}/accounts/{{account_id}}/test"): _VP,
    ("GET", f"{_P}/plans"): _VP,
    ("GET", f"{_P}/plans/{{plan_id}}"): _VP,
    ("GET", f"{_P}/plans/{{plan_id}}/instruction-sheet"): _VP,
    ("GET", f"{_P}/basis-records/{{record_id}}/file/{{kind}}"): _VP,
    ("POST", f"{_P}/accounts/{{account_id}}/fixup"): _MS,
    ("POST", f"{_P}/plans"): _MS,
    ("PATCH", f"{_P}/plans/{{plan_id}}"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/accounts"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/retest"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/ready"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/reopen"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/submit"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/cancel"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/basis-record"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/route"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/remove"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/us-person"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/sponsor-review"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/consent"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/tax-adviser"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/boot"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/acknowledge"): _MS,
    ("POST", f"{_P}/plan-accounts/{{plan_account_id}}/confirm"): _MS,
    ("POST", f"{_P}/basis-records/{{record_id}}/delivered"): _MS,
    ("PATCH", f"{_P}/steps/{{step_id}}"): _MS,
    ("POST", f"{_P}/steps/{{step_id}}/done"): _MS,
    ("POST", f"{_P}/asset-classes"): _MS,
    ("POST", f"{_P}/asset-classes/{{class_id}}/retire"): _MS,
    ("POST", f"{_P}/plans/{{plan_id}}/approve"): _AP,
    ("POST", f"{_P}/plans/{{plan_id}}/reject"): _AP,
    ("POST", f"{_P}/sponsor-profiles"): _OS,
    ("POST", f"{_P}/sponsor-profiles/{{profile_key}}/versions"): _OS,
    ("PUT", f"{_P}/thresholds"): _OS,
    ("POST", f"{_P}/asset-classes/platform"): _SA,
}
OWN_FILES = ("apps/api/services/section351.py", "apps/api/services/section351_rules.py",
             "apps/api/routers/section351.py")


def self_routes(check, find):
    """rep14's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "rep14 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate at rep14's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "rep14's routes all still exist today with the same gate", f"{drift[:4]}")


def self_code(check, find):
    """rep14's own code at its own end point: a transfer realizes nothing; the adapter route reads the declared
    in_kind_transfers capability; ready reads the library (no disclosure text in code); no model / network client."""
    sv = VS.own_text(SPRINT_KEY, OWN_FILES[0]) or ""
    confirm = sv.split("async def confirm_account(", 1)[-1].split("\nasync def ", 1)[0]
    check("NULL, 'closed'" in confirm and '"realized_gain": ZERO' in confirm,
          "confirm_account closes each lot BY TRANSFER: realized_gain_loss NULL on the closed lot, realized_gain 0 on "
          "the transfer", f"{len(confirm)} chars")
    check('capability(conn, code, "in_kind_transfers")' in sv,
          "the adapter route reads the custodian's declared in_kind_transfers capability")
    check('DISCLOSURE_KEY = "section351.exit"' in sv and "resolve_safe(conn, DISCLOSURE_KEY" in sv,
          "the section351.exit disclosure comes from the library resolver (never text in code)")
    for f in OWN_FILES:
        src = VS.own_text(SPRINT_KEY, f) or ""
        check(bool(src) and not re.search(r"call_claude|anthropic|litellm|httpx\.", src),
              f"{f}: no model call and no network client", f"{len(src)} chars")


def self_checks(check, find):
    self_routes(check, find)
    self_code(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import loss_bank as LB  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import section351 as S351  # noqa: E402
from services import section351_rules as R  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import storage  # noqa: E402
from services import tax_profiles as TP  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "rep14verify"
PREFIX = "ZZR14"
NAME_PREFIX = "REP14 VERIFY"
REF_PROVIDER = "zz_rep14_test"
CIK_PAIR = "9914000001"          # the two share classes' shared (fixture) issuer CIK
CUSTODIAN = "ZZR14C"             # no capability rows → in_kind_transfers 'unknown' (not declared)
SNAP_SOURCE = "zz_rep14_test"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
ZERO = Decimal(0)
LT_RATE, ST_RATE = Decimal("0.20"), Decimal("0.37")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001e{n:04d}")


FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000001e"
ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN1, U_ADMIN2, U_VIEW, U_ADMIN_B = (_id(110 + i) for i in range(5))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP14 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP14 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP14 Admin Checker"),
    U_VIEW: (ORG_A, "member", "view", "rep14_viewer", "REP14 Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP14 Org B Admin"),
}
VIEWER_PERMS = ("view_proposals",)
ADMIN_PERMS = {"view_proposals", "manage_proposals", "approve_proposals", "manage_org_settings"}
# entities: (org, entity_type, country_code)
ENT_IND, ENT_LLC, ENT_CCORP, ENT_GB, ENT_B = _id(200), _id(201), _id(202), _id(203), _id(204)
ENTITIES = {ENT_IND: (ORG_A, "individual", None), ENT_LLC: (ORG_A, "llc", None), ENT_CCORP: (ORG_A, "c_corp", None),
            ENT_GB: (ORG_A, "individual", "GB"), ENT_B: (ORG_B, "individual", None)}
HH_A, HH_FIX, HH_HA, HH_B = _id(210), _id(211), _id(212), _id(213)
HOUSEHOLDS = {HH_A: (ORG_A, (ENT_IND, ENT_LLC, ENT_CCORP, ENT_GB)), HH_FIX: (ORG_A, ()), HH_HA: (ORG_A, ()),
              HH_B: (ORG_B, (ENT_B,))}
(ACC_PASS, ACC_PART, ACC_FIX, ACC_CCORP, ACC_LT, ACC_NOH, ACC_INEL, ACC_F2501, ACC_F5001, ACC_AVG, ACC_NODATE,
 ACC_ADV, ACC_HA, ACC_NONUS, ACC_IRA, ACC_B) = (_id(301 + i) for i in range(16))
# account → (org, registration, tax status, household, entity, held away, cash)
ACCOUNTS = {
    ACC_PASS: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_PART: (ORG_A, "llc", "taxable", HH_A, ENT_LLC, False, 0),
    ACC_FIX: (ORG_A, "individual", "taxable", HH_FIX, ENT_IND, False, 1000),
    ACC_CCORP: (ORG_A, "individual", "taxable", HH_A, ENT_CCORP, False, 0),
    ACC_LT: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_NOH: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_INEL: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_F2501: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_F5001: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_AVG: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_NODATE: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_ADV: (ORG_A, "individual", "taxable", HH_A, ENT_IND, False, 0),
    ACC_HA: (ORG_A, "individual", "taxable", HH_HA, ENT_IND, True, 0),
    ACC_NONUS: (ORG_A, "individual", "taxable", HH_A, ENT_GB, False, 0),
    ACC_IRA: (ORG_A, "ira", "tax_deferred", HH_A, ENT_IND, False, 0),
    ACC_B: (ORG_B, "individual", "taxable", HH_B, ENT_B, False, 0),
}
SEC_KEYS = ("A1", "A2", "B", "C", "D", "E", "GOV", "X", "Y", "FND", "FND2", "OPT", "CRY", "M1", "M2", "NEWETF", "PLT")
SEC_NAMES = {k: f"{NAME_PREFIX} {k}" for k in SEC_KEYS}
SEC_TYPES = {"FND": "fund", "FND2": "fund", "NEWETF": "fund", "OPT": "option", "GOV": "fixed_income"}
PRICES = {"M1": "100", "M2": "100"}      # the model's (unheld) names, via the _unheld_prices stub
# holdings: (account, key, shares, price, [(lot shares, unit cost, acquired days ago | None)] | None (no lot rows))
# ACC_PASS — 100,000 at $10: issuer A = A1 15,000 + A2 10,000 (one CIK) = 25,000 (25.00 %); B 10,000; C, D, E 5,000
#   each → top five 25 + 10 + 15 = 50.00 %; GOV 50,000 is in the TOTAL but no issuer. Passes exactly at both edges.
#   (GOV as an issuer → 50 % one issuer; GOV out of the total → A = 50 %; either reading would FAIL.)
# ACC_PART — 10,000: A1 2,000 + B 2,000 + GOV 6,000 → 20 % / 40 % (passes); cost $7 → basis 7,000.
# ACC_FIX — test total 100,000 at $100: A 30,000 (lot a1 150 @ 95, lot a2 150 @ 50), B–E 2,500 each, GOV 60,000;
#   cash 1,000 (stays in the account). A = 30 % FAILS (top five 40 %).
# ACC_LT — X 20,000 direct + FND 20,000 (holdings X 25 %, Y 75 % → X 5,000, Y 15,000) + GOV 60,000: X = 25,000 =
#   25.00 % ONLY when the direct and looked-through amounts are summed; Y 15 %; top five 40 % → passes.
# ACC_NOH — FND2 (no holdings data) 10,000 + B 10,000 → fails closed ('holdings_needed').
# ACC_INEL — OPT (an option leg) 1,000 + CRY (crypto, asset_type digital_asset) 1,000 + B 1,000 + GOV 9,000: both
#   excluded and listed; contributed 10,000 (B 10 %).
# ACC_F2501 — A1 25,010 + B, C, D, E 6,000 each + GOV 50,990 = 100,000: A = 25.01 % (fails ONE issuer; top 49.01 %).
# ACC_F5001 — A1 25,000 + B 7,000 + C 6,000 + D 6,000 + E 6,010 + GOV 49,990: top five 50.01 % (fails; A 25.00 %).
HOLDINGS = (
    (ACC_PASS, "A1", 1500, 10, [(1500, 6, 800)]), (ACC_PASS, "A2", 1000, 10, [(1000, 6, 800)]),
    (ACC_PASS, "B", 1000, 10, [(1000, 6, 800)]), (ACC_PASS, "C", 500, 10, [(500, 6, 800)]),
    (ACC_PASS, "D", 500, 10, [(500, 6, 800)]), (ACC_PASS, "E", 500, 10, [(500, 6, 800)]),
    (ACC_PASS, "GOV", 5000, 10, [(5000, 6, 800)]),
    (ACC_PART, "A1", 200, 10, [(200, 7, 700)]), (ACC_PART, "B", 200, 10, [(200, 7, 700)]),
    (ACC_PART, "GOV", 600, 10, [(600, 7, 700)]),
    (ACC_FIX, "A1", 300, 100, [(150, 95, 800), (150, 50, 800)]), (ACC_FIX, "B", 25, 100, [(25, 80, 800)]),
    (ACC_FIX, "C", 25, 100, [(25, 80, 800)]), (ACC_FIX, "D", 25, 100, [(25, 80, 800)]),
    (ACC_FIX, "E", 25, 100, [(25, 80, 800)]), (ACC_FIX, "GOV", 600, 100, [(600, 99, 800)]),
    (ACC_CCORP, "B", 100, 10, [(100, 6, 800)]), (ACC_CCORP, "GOV", 300, 10, [(300, 6, 800)]),
    (ACC_LT, "X", 2000, 10, [(2000, 6, 800)]), (ACC_LT, "FND", 2000, 10, [(2000, 6, 800)]),
    (ACC_LT, "GOV", 6000, 10, [(6000, 6, 800)]),
    (ACC_NOH, "FND2", 1000, 10, [(1000, 6, 800)]), (ACC_NOH, "B", 1000, 10, [(1000, 6, 800)]),
    (ACC_INEL, "OPT", 100, 10, [(100, 6, 800)]), (ACC_INEL, "CRY", 100, 10, [(100, 6, 800)]),
    (ACC_INEL, "B", 100, 10, [(100, 6, 800)]), (ACC_INEL, "GOV", 900, 10, [(900, 6, 800)]),
    (ACC_F2501, "A1", 2501, 10, [(2501, 6, 800)]), (ACC_F2501, "B", 600, 10, [(600, 6, 800)]),
    (ACC_F2501, "C", 600, 10, [(600, 6, 800)]), (ACC_F2501, "D", 600, 10, [(600, 6, 800)]),
    (ACC_F2501, "E", 600, 10, [(600, 6, 800)]), (ACC_F2501, "GOV", 5099, 10, [(5099, 6, 800)]),
    (ACC_F5001, "A1", 2500, 10, [(2500, 6, 800)]), (ACC_F5001, "B", 700, 10, [(700, 6, 800)]),
    (ACC_F5001, "C", 600, 10, [(600, 6, 800)]), (ACC_F5001, "D", 600, 10, [(600, 6, 800)]),
    (ACC_F5001, "E", 601, 10, [(601, 6, 800)]), (ACC_F5001, "GOV", 4999, 10, [(4999, 6, 800)]),
    (ACC_AVG, "B", 100, 10, None),                                   # a position with NO lot rows (average cost)
    (ACC_NODATE, "B", 100, 10, [(100, 6, None)]),                    # a lot with no acquisition date
    (ACC_ADV, "B", 100, 10, [(100, 6, 800)]), (ACC_HA, "B", 100, 10, [(100, 6, 800)]),
    (ACC_NONUS, "B", 100, 10, [(100, 6, 800)]), (ACC_IRA, "B", 100, 10, [(100, 6, 800)]),
    (ACC_B, "B", 100, 10, [(100, 6, 800)]),
)
JOINED = (ACC_PASS, ACC_PART, ACC_FIX, ACC_CCORP)
SECTION351_TABLES = ("public.section351_sponsor_profiles", "public.section351_settings",
                     "public.section351_asset_classes", "public.section351_plans", "public.section351_plan_accounts",
                     "public.section351_basis_records", "public.section351_tracker_steps",
                     "public.section351_transfers")
COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households",
    "public.household_memberships", "public.accounts", "public.account_balances_daily",
    "public.account_authority_settings", "public.documents", "portfolio.assets", "portfolio.positions",
    "portfolio.tax_lots", "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.security_reference_attributes", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.model_uploads", "public.account_model_links",
    "public.rebalance_proposals", "public.rebalance_proposal_trades", "public.tax_profiles",
    "public.disclosure_versions", "public.disclosure_acknowledgements", "portfolio.replica_holdings_snapshots",
    "portfolio.replica_holdings_rows", "public.loss_bank_entries", *SECTION351_TABLES,
)
SEED_CHECK_LITERALS = (
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund", "option", "fixed_income")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("ticker",)),
    ("portfolio.security_reference_attributes", "security_reference_attributes_field_chk", ("cik",)),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open", "closed")),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long",)),
    ("portfolio.replica_holdings_snapshots", "replica_holdings_snapshots_status_chk", ("live",)),
    ("portfolio.replica_holdings_rows", "replica_holdings_rows_asset_type_chk", ("equity",)),
    ("portfolio.replica_holdings_rows", "replica_holdings_rows_state_chk", ("resolved",)),
    ("portfolio.replica_holdings_rows", "replica_holdings_rows_via_chk", ("ticker",)),
    ("public.account_authority_settings", "account_authority_settings_authority_chk", ("advise",)),
    ("public.account_authority_settings", "account_authority_settings_custody_chk", ("integrated",)),
    ("public.account_authority_settings", "account_authority_settings_status_chk", ("approved",)),
    ("public.section351_asset_classes", "section351_asset_classes_class_chk", ("government", "restricted")),
    ("public.section351_plans", "section351_plans_status_chk", ("draft", "ready", "pending_approval", "approved",
                                                              "transferring", "completed")),
    ("public.section351_transfers", "section351_transfers_status_chk", ("approved", "sent", "confirmed")),
    ("public.section351_tracker_steps", "section351_tracker_steps_key_chk", ("counsel_engaged", "confirmation")),
    ("public.section351_plan_accounts", "section351_plan_accounts_owner_chk", ("individual", "partnership")),
)
UNIQUES = ("section351_sponsor_profiles_live_uq", "section351_sponsor_profiles_version_uq",
           "section351_settings_live_uq", "section351_settings_version_uq", "section351_asset_classes_live_uq",
           "section351_plan_accounts_live_uq", "section351_basis_records_live_uq",
           "section351_basis_records_version_uq", "section351_tracker_steps_live_uq", "section351_transfers_lot_uq",
           "replica_holdings_snapshots_file_uq", "replica_holdings_rows_row_uq")
FK_TABLES = SECTION351_TABLES

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
S3_BUILT: list[str] = []
BLOBS: dict[str, bytes] = {}
PUT_LOG: list[str] = []
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
        return None, exc.stdout or "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


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
    return f"ZZ14{int(str(acc)[-4:]):06d}"


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """The same org context the route sets (org GUC, not super admin), ON the connection inside its transaction."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


def _arr(vals, typ="uuid") -> str:
    return "ARRAY[" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals) + f"]::{typ}[]"


def fixture_scope(table: str) -> str:
    """The fixture-row predicate of EVERY counted table (live activity may write any of them)."""
    orgs, users = _arr(FIXTURE_ORGS), _arr(USERS)
    names = _arr(SEC_NAMES.values(), "text")
    secs = f"(SELECT id FROM portfolio.securities_global WHERE name = ANY({names}))"
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR resource_id::text LIKE "
                            f"'{FIXTURE_ID_PREFIX}%'",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.organizations": f"id = ANY({orgs})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({_arr(HOUSEHOLDS)})",
        "portfolio.securities_global": f"name = ANY({names})",
        "portfolio.securities_global_identifiers": f"global_security_id IN {secs}",
        "portfolio.security_reference_attributes": f"global_security_id IN {secs} OR provider_key = '{REF_PROVIDER}'",
        "portfolio.replica_holdings_snapshots": f"fund_global_security_id IN {secs}",
        "portfolio.replica_holdings_rows": f"snapshot_id IN (SELECT id FROM portfolio.replica_holdings_snapshots "
                                           f"WHERE fund_global_security_id IN {secs})",
        "public.section351_asset_classes": f"org_id = ANY({orgs}) OR global_security_id IN {secs}",
    }.get(table, by_org)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    return out


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint must never touch. The section351 tables, disclosure versions / acknowledgements and the
    holdings snapshots — every row OUTSIDE the fixture orgs / fixture securities (no background job writes them);
    the demo org's accounts (structural columns). Positions / lots / proposals are written by live jobs at any time
    — covered by the fixture-scoped counts instead."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for t in SECTION351_TABLES:
        cond = "NOT (org_id = ANY($1::uuid[]))"
        if t == "public.section351_asset_classes":
            cond = ("(org_id IS NULL OR NOT (org_id = ANY($1::uuid[]))) AND global_security_id NOT IN (SELECT id FROM "
                    f"portfolio.securities_global WHERE name LIKE '{NAME_PREFIX}%')")
        out[t.split(".")[1]] = await fingerprint(conn, f"SELECT * FROM {t} WHERE {cond}", orgs)
    for t in ("public.disclosure_versions", "public.disclosure_acknowledgements"):
        out[t.split(".")[1]] = await fingerprint(conn, f"SELECT * FROM {t} WHERE org_id IS NULL OR NOT "
                                                       "(org_id = ANY($1::uuid[]))", orgs)
    out["replica_holdings_snapshots"] = await fingerprint(
        conn, "SELECT * FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id NOT IN (SELECT id FROM "
              "portfolio.securities_global WHERE name LIKE $1)", f"{NAME_PREFIX}%")
    demo = await platform(conn, "SELECT id FROM organizations WHERE slug = 'demo-olivo'", fetch="fetchval")
    S["demo_org"] = demo
    if demo is not None:
        out["demo_olivo_accounts"] = await fingerprint(
            conn, "SELECT id, custodian_code, household_id, is_held_away, tax_status FROM public.accounts WHERE "
                  "org_id = $1", demo)
        out["demo_olivo_lots"] = await fingerprint(
            conn, "SELECT tl.id, tl.status, tl.quantity, tl.valid_to FROM portfolio.tax_lots tl WHERE tl.org_id = $1",
            demo)
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
    """Child-first by the shared helper; every platform key MATERIALISED first (the security ids, then the snapshot
    ids) — never a subquery over a table the same teardown deletes from (cleanup-plan-subquery-order)."""
    op = await operator_conn()
    try:
        secs = [r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                                f"{NAME_PREFIX}%")]
        secs += [r["global_security_id"] for r in await op.fetch(
            "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
            f"{PREFIX}%") if r["global_security_id"] not in secs]
        snaps = [r["id"] for r in await op.fetch(
            "SELECT id FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id = ANY($1::uuid[]) "
            "OR source_key = $2", secs, SNAP_SOURCE)]
        orgs, users = FIXTURE_ORGS, list(USERS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [list(HOUSEHOLDS)]),
            "public.section351_asset_classes": ("global_security_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
                                                [secs, orgs]),
            "portfolio.replica_holdings_rows": ("snapshot_id = ANY($1::uuid[])", [snaps]),
            "portfolio.replica_holdings_snapshots": ("id = ANY($1::uuid[])", [snaps]),
            "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[]) OR provider_key = $2",
                                                        [secs, REF_PROVIDER]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_rep14")
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
        ("SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE source_key = $1", [SNAP_SOURCE]),
        ("SELECT count(*) FROM public.section351_asset_classes c JOIN portfolio.securities_global g ON "
         "g.id = c.global_security_id WHERE g.name LIKE $1", [f"{NAME_PREFIX}%"]),
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
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep14"}}
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
    """The task's RLS contextvars. ENTER IT BEFORE ``pool.acquire()`` (rep13 fix 1): the wrapped pool applies the
    GUCs once, at acquire — entered after, the transaction carries app.current_org_id = ''."""

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
    register_brief_blocks()
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile
    up = list(inspect.signature(storage.upload_bytes).parameters)
    down = list(inspect.signature(storage.download_bytes).parameters)
    if up != ["key", "data", "content_type", "bucket"] or down != ["key", "bucket"]:
        raise RuntimeError(f"storage signatures drifted: upload_bytes{up} download_bytes{down}")

    def fake_upload(key, data, content_type=None, bucket=None):
        if key in BLOBS:
            raise AssertionError(f"fake R2: an existing object was about to be overwritten: {key}")
        BLOBS[key] = bytes(data)
        PUT_LOG.append(key)
        return key

    def fake_download(key, bucket=None):
        return BLOBS[key]

    def no_client(*a, **k):
        S3_BUILT.append("get_s3_client")
        raise AssertionError("verify: a real R2 client must never be built")

    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_s3_client = no_client

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep14: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_rep14: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))

    have = list(inspect.signature(RP._unheld_prices).parameters)
    if have[:3] != ["conn", "keys", "since"] or "org_id" not in have or "today" not in have:
        raise RuntimeError(f"_unheld_prices signature drifted: {have}")

    async def unheld_prices(conn, keys, since, *, org_id, today=None, provenance=None):
        out = {}
        for k in keys:
            key = S.get("key_of", {}).get(str(k))
            if key and key in PRICES:
                out[str(k)] = (D(PRICES[key]).quantize(Decimal("0.00000001")), today or date.today(),
                               "security_price")
        return out

    RP._unheld_prices = unheld_prices


def acct(a) -> str:
    return str(a)


def sec(key) -> str:
    return str(S["sec"][key])


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1f (docs/REP14_DISCOVERY.md)")
    p = REPO / "docs" / "REP14_DISCOVERY.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    heads = re.findall(r"^## (1[a-f])", txt, re.M)
    check(set(heads) >= {"1a", "1b", "1c", "1d", "1e", "1f"}, "the discovery doc has a section for each of 1a–1f",
          f"{p.exists()} {sorted(set(heads))}")
    n_avg = await platform(conn, """SELECT count(*) FROM public.accounts a WHERE a.system_to IS NULL AND
        a.valid_to IS NULL AND NOT (a.org_id = ANY($1::uuid[])) AND EXISTS (SELECT 1 FROM portfolio.positions p
        WHERE p.account_id = a.id AND p.valid_to IS NULL AND p.system_to IS NULL AND p.quantity <> 0) AND NOT EXISTS
        (SELECT 1 FROM portfolio.tax_lots tl JOIN portfolio.positions p ON p.id = tl.position_id WHERE
        p.account_id = a.id AND tl.status = 'open' AND tl.valid_to IS NULL AND tl.system_to IS NULL)""",
                           FIXTURE_ORGS, fetch="fetchval")
    find("1a live accounts with positions but no open lot rows (average cost only — never eligible)", f"{n_avg}")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation, the connection does not bypass RLS")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep14.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep14.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-300:] if code else ''}")
    for t in SECTION351_TABLES:
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
                                 "AND conname = $2 AND contype::text = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    uq = {r["indexname"] for r in await platform(conn, "SELECT indexname FROM pg_indexes WHERE indexname = "
                                                       "ANY($1::text[]) AND indexdef LIKE 'CREATE UNIQUE%'",
                                                 list(UNIQUES))}
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype::text = 'f' AND conrelid = "
                                   "ANY($1::regclass[])", list(FK_TABLES), fetch="fetchval"))
    check(len(SEED_CHECK_LITERALS) > 0 and not bad and uq == set(UNIQUES) and fks >= 20,
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints read); the "
          f"{len(UNIQUES)} UNIQUE indexes and the FKs the seeds rely on are in the catalog",
          "; ".join(bad) + f" uq_missing={sorted(set(UNIQUES) - uq)} fks={fks}")
    S["literals_ok"] = not bad and uq == set(UNIQUES)


async def guard_prefix(conn) -> bool:
    vals = []
    for sql, arg in (("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", f"{PREFIX}%"),
                     ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%"),
                     ("SELECT count(*) FROM portfolio.security_reference_attributes WHERE provider_key = $1",
                      REF_PROVIDER),
                     ("SELECT count(*) FROM portfolio.security_reference_attributes WHERE field = 'cik' AND "
                      "ltrim(value, '0') = $1", CIK_PAIR),
                     ("SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE source_key = $1", SNAP_SOURCE),
                     ("SELECT count(*) FROM public.accounts WHERE upper(custodian_code) LIKE $1", f"{PREFIX}%"),
                     ("SELECT count(*) FROM public.custodian_capabilities WHERE upper(custodian_code) LIKE $1",
                      f"{PREFIX}%")):
        vals.append(int(await platform(conn, sql, arg, fetch="fetchval")))
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    return check(all(v == 0 for v in vals) and o == 0 and u == 0,
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', {REF_PROVIDER}, CIK {CIK_PAIR}, snapshot source "
                 f"{SNAP_SOURCE}, custodian {CUSTODIAN}, the fixture id range) match no real row",
                 f"{vals} orgs={o} users={u}")


async def check_fixture_roles(conn) -> bool:
    """Each fixture user's role AS THE APP DECIDES IT (user_roles → services.authz Principal), users.role its mirror."""
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
            ok = (rname in names and rbac.mirrored_role_string(names) == account_role
                  and rbac.is_super_admin(lp) is want_super and bool(pr and pr.is_super_admin) is want_super)
            if rname == "org_admin":
                ok = ok and ADMIN_PERMS <= perms
            if rname == "rep14_viewer":
                ok = ok and set(VIEWER_PERMS) <= perms and not ({"manage_proposals", "approve_proposals",
                                                                 "manage_org_settings"} & perms)
            if not ok:
                bad.append((str(uid), names, account_role, bool(pr and pr.is_super_admin), sorted(perms)[:12]))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (Super Admin; org admins "
                 "with view / manage / approve_proposals + manage_org_settings; the viewer view_proposals only), "
                 "users.role its mirror", f"{bad}")


async def seed(conn) -> None:
    S["seed_day"] = today()
    S["sec"], S["key_of"], S["lots"], S["asset"] = {}, {}, {}, {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP14 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid = await rbac.ensure_role(conn, ORG_A, "rep14_viewer", "REP14 verify — view_proposals only")
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
        for ent, (org, et, country) in ENTITIES.items():
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, country_code) VALUES "
                               "($1, $2, $3::entity_type, $4, $5)", ent, org, et, f"REP14 Verify {et} {str(ent)[-3:]}",
                               country)
        for hh, (org, ents) in HOUSEHOLDS.items():
            maker = U_ADMIN_B if org == ORG_B else U_ADMIN1
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                               hh, org, f"REP14 Verify Household {str(hh)[-2:]}", maker)
            for ent in ents:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, "
                                   "$2, $3)", hh, ent, maker)
        for key in SEC_KEYS:
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES.get(key, "equity"),
                                           currency_code="USD", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S["sec"][key] = UUID(str(sid))
            S["key_of"][str(sid)] = key
        for key in ("A1", "A2"):                              # two share classes, ONE issuer (the same SEC CIK)
            await conn.execute(
                "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, provider_key, "
                "as_of) VALUES ($1, 'cik', $2, $3, now())", S["sec"][key], CIK_PAIR, REF_PROVIDER)
        # FND's holdings (PLATFORM snapshot, live): X 25 %, Y 75 %; FND2 has none
        snap = await conn.fetchval(
            "INSERT INTO portfolio.replica_holdings_snapshots (fund_global_security_id, as_of, source_key, file_sha256, "
            "row_count, total_weight, status) VALUES ($1, $2, $3, $4, 2, 1, 'live') RETURNING id", S["sec"]["FND"],
            S["seed_day"] - timedelta(days=5), SNAP_SOURCE, hashlib.sha256(b"rep14 verify FND holdings").hexdigest())
        for n, (k, w) in enumerate((("X", "0.25"), ("Y", "0.75")), start=1):
            await conn.execute(
                "INSERT INTO portfolio.replica_holdings_rows (snapshot_id, row_num, raw_name, raw_ticker, asset_type, "
                "constituent_global_security_id, resolution_state, resolved_via, weight) VALUES ($1, $2, $3, $4, "
                "'equity', $5, 'resolved', 'ticker', $6)", snap, n, SEC_NAMES[k], f"{PREFIX}{k}", S["sec"][k], D(w))
        S["snap_as_of"] = S["seed_day"] - timedelta(days=5)
        salts = {}
        for org in FIXTURE_ORGS:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for acc, (org, reg, tax, hh, ent, held_away, cash) in ACCOUNTS.items():
            n = AccountNumber(raw_of(acc))
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10)",
                acc, org, n.masked, n.hashed(salts[org]), CUSTODIAN, reg, tax, ent, hh, held_away)
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", org, acc,
                S["seed_day"], D(cash))
        S["masked"] = {a: AccountNumber(raw_of(a)).masked for a in ACCOUNTS}
        await conn.execute(
            "INSERT INTO public.account_authority_settings (org_id, account_id, authority_override, custody, "
            "effective_from, reason, status, maker_id, approved_by, decided_at) VALUES ($1, $2, 'advise', "
            "'integrated', $3, 'REP14 verify — advise fixture', 'approved', $4, $5, now())",
            ORG_A, ACC_ADV, S["seed_day"] - timedelta(days=1), U_ADMIN1, U_ADMIN2)
        for acc, key, qty, price, lots in HOLDINGS:
            org, ent = ACCOUNTS[acc][0], ACCOUNTS[acc][4]
            aid = S["asset"].get((org, key))
            if aid is None:
                aid = S["asset"][(org, key)] = uuid.uuid4()
                await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                                   "VALUES ($1, $2, $3, $4, $5)", aid, org, S["sec"][key], SEC_NAMES[key],
                                   "digital_asset" if key == "CRY" else ("fund" if key.startswith("FND") else "equity"))
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, org, ent, aid, S["seed_day"], D(qty), D(qty) * D(price), acc)
            for i, (lq, uc, days) in enumerate(lots or []):
                lid = uuid.uuid4()
                acq = None if days is None else S["seed_day"] - timedelta(days=days)
                await conn.execute(
                    "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                    "original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, 'open')", lid, org, pid,
                    acq, D(lq), D(lq) * D(uc), None if days is None else ("long" if days > 365 else "short"))
                S["lots"][(acc, key, i)] = str(lid)
        S["doc"] = {}
        for acc in (ACC_PASS, ACC_PART, ACC_FIX):
            S["doc"][acc] = await conn.fetchval(
                "INSERT INTO public.documents (org_id, entity_id, original_filename, source, status, created_by) "
                "VALUES ($1, $2, $3, 'upload', 'dropped', $4) RETURNING id", ORG_A, ACCOUNTS[acc][4],
                f"REP14 VERIFY consent {S['masked'][acc]}.pdf", U_ADMIN1)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          list(USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true",
          f"{len(us)}/{len(USERS)} users, {len(os_)}/2 orgs")
    S["seeded"] = await check_fixture_roles(conn) and us == set(USERS) and os_ == set(FIXTURE_ORGS)


# ── models, joins, tax profile ─────────────────────────────────────────────
def csv_bytes(rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Ticker", "Weight %"])
    for key, wt in rows:
        w.writerow([f"{PREFIX}{key}", wt])
    return buf.getvalue().encode("utf-8")


async def join(a, model_id) -> dict:
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{acct(a)}/model/join",
                          {"model_id": str(model_id), "settings": {}})
    link = (out or {}).get("link") or {}
    if code != 201 or not link.get("id"):
        return {"code": code, "out": out}
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{acct(a)}/model/changes/{link['id']}/approve", {})
    return {"code": c2, "out": o2, "link": link["id"]}


async def y_model_and_joins(conn):
    section("[Y] Fixture model (M1 50 % / M2 50 %), the joined replica accounts and the approved tax profile")
    code, content = await api_raw(U_ADMIN1, "POST", "/api/v1/models/uploads",
                                  files={"file": ("rep14_model.csv", csv_bytes((("M1", 50), ("M2", 50))), "text/csv")},
                                  form={})
    try:
        up = json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        up = {}
    uid = (up.get("upload") or {}).get("id")
    c2, o2 = (await api(U_ADMIN1, "POST", f"/api/v1/models/uploads/{uid}/confirm",
                        {"model_name": "REP14 Verify Exit Model"})) if uid else (code, up)
    m = (o2 or {}).get("model") or {}
    v = (o2 or {}).get("version") or {}
    c3, o3 = await api(U_ADMIN1, "POST", f"/api/v1/models/{m.get('id')}/versions/{v.get('id')}/submit", {})
    c4, o4 = await api(U_ADMIN2, "POST", f"/api/v1/models/{m.get('id')}/versions/{v.get('id')}/approve", {})
    ok = check(c2 == 201 and c3 == 200 and c4 == 200 and bool(m.get("id")),
               "the fixture model uploads and is approved by a second person", f"{code} {c2} {c3} {c4} {dumps(o2)} "
               f"{dumps(o4)}")
    S["model"] = m.get("id")
    if not ok:
        return
    joined = []
    for a in JOINED:
        r = await join(a, S["model"])
        joined.append((S["masked"][a], r["code"]))
    check(all(c == 200 for _, c in joined), "the four replica fixture accounts join the model (approved by a second "
          "person)", f"{joined}")
    with _ctx(ORG_A):
        pool = await get_pool()
        p = await TP.propose_profile(pool, org_id=ORG_A, actor_id=U_ADMIN1, subject_type="household",
                                     subject_id=HH_FIX, tax_year=today().year,
                                     data={"filing_status": "single", "federal_ordinary_rate": ST_RATE,
                                           "federal_lt_rate": LT_RATE, "niit_applies": False,
                                           "as_of": today() - timedelta(days=3),
                                           "source_note": "REP14 verify — the adviser's entered rates"})
        await TP.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, kind="profile", row_id=p["id"], outcome="approve")
        async with pool.acquire() as c:
            from services import account_models as AM
            prof = await TP.profile_for_account(c, ORG_A, await AM.load_account(c, ORG_A, ACC_FIX), today().year)
    rates = TP.combined_rates(prof)
    check(rates is not None and rates["st"] == ST_RATE and rates["lt"] == LT_RATE,
          "the fix-up household's tax profile is APPROVED (entered rates with their date; ST 37 %, LT 20 %)",
          dumps(rates))
    S["rates"] = rates
    S["joined"] = all(c == 200 for _, c in joined)


# ── Task 3 pure: the 25 / 50 test, hand-computed ──────────────────────────
def L(key, v, issuer=None, kind=R.KIND_ISSUER, via="direct"):
    return R.Line(security_id=key, label=key, value=D(v), kind=kind, issuer_key=issuer, issuer_label=issuer, via=via)


def y_rules():
    section("[Y] 25 / 50 test exact at the edges; issuer grouping; the government rule; look-through (pure)")
    gov = lambda v: L("GOV", v, kind=R.KIND_GOVERNMENT)  # noqa: E731
    # A 25 + B 10 + C, D, E 5 = 50 (top five), GOV 50 → 25.00 % / 50.00 % → PASS
    t = R.diversification_test([L("A", 25, "iA"), L("B", 10, "iB"), L("C", 5, "iC"), L("D", 5, "iD"), L("E", 5, "iE"),
                                gov(50)])
    check(t["verdict"] == "pass" and D(t["largest_issuer_weight"]) == D("0.25") and D(t["top_five_weight"]) == D("0.5")
          and D(t["margin_one"]) == ZERO and D(t["margin_five"]) == ZERO,
          "exactly 25.00 % in one issuer and 50.00 % in the top five PASSES (margins 0)", dumps(t))
    # A 25.01, B–E 6 each (top five 49.01), GOV 50.99 → fails ONE issuer only
    t = R.diversification_test([L("A", "25.01", "iA")] + [L(k, 6, f"i{k}") for k in "BCDE"] + [gov("50.99")])
    check(t["verdict"] == "fail" and t["largest_issuer_pct"] == "25.01%" and t["top_five_pct"] == "49.01%"
          and len(t["reasons"]) == 1 and "one issuer" in t["reasons"][0],
          "25.01 % in one issuer FAILS (the top five at 49.01 % would pass)", dumps(t))
    # A 25, B 7, C 6, D 6, E 6.01 = 50.01, GOV 49.99 → fails the top five only
    t = R.diversification_test([L("A", 25, "iA"), L("B", 7, "iB"), L("C", 6, "iC"), L("D", 6, "iD"),
                                L("E", "6.01", "iE"), gov("49.99")])
    check(t["verdict"] == "fail" and t["largest_issuer_pct"] == "25.00%" and t["top_five_pct"] == "50.01%"
          and len(t["reasons"]) == 1 and "five" in t["reasons"][0],
          "50.01 % in the five largest FAILS (the largest at 25.00 % would pass)", dumps(t))
    # two share classes of ONE issuer: 15 + 15 = 30 % → fails; as two issuers each would be 15 %
    t = R.diversification_test([L("GOOG", 15, "cik:1"), L("GOOGL", 15, "cik:1"), L("B", 10, "iB")] +
                               [L(f"K{i}", 6, f"iK{i}") for i in range(10)])
    top = t["issuers"][0] if t["issuers"] else {}
    check(t["verdict"] == "fail" and top.get("issuer_key") == "cik:1" and D(top.get("weight", 0)) == D("0.3")
          and top.get("securities") == ["GOOG", "GOOGL"],
          "two share classes of one issuer are counted as ONE issuer (15 + 15 = 30 % of 100)", dumps(top))
    # government: 40 in the total, not an issuer → A 25 / 100 = 25 %; as an issuer GOV would be 40 % (fail)
    t = R.diversification_test([gov(40), L("A", 25, "iA")] + [L(f"X{i}", 5, f"iX{i}") for i in range(7)])
    check(t["verdict"] == "pass" and t["total_assets"] == "100.00" and t["government"] == "40.00"
          and all(x["issuer_key"] != "GOV" for x in t["issuers"]) and t["largest_issuer_pct"] == "25.00%",
          "a government security is IN the total and NOT an issuer (Treas. Reg. §1.351-1(c)(6)(i))", dumps(t))
    # cash is excluded from the total (§368(a)(2)(F)(iv)): A 25 of 100 + cash 50 → still 25 %
    t = R.diversification_test([L("A", 25, "iA"), gov(75), L("CASH", 50, kind=R.KIND_CASH)])
    check(t["verdict"] == "pass" and t["total_assets"] == "100.00" and t["cash_excluded"] == "50.00",
          "cash is excluded from the total", dumps(t))
    # look-through: FND 20 (X 25 %, Y 75 % → X 5, Y 15) + X 20 direct + GOV 60 → X = 25.00 %
    snap = {"as_of": date(2026, 11, 1)}
    rows = [{"asset_type": "equity", "resolution_state": "resolved", "constituent_global_security_id": "X",
             "weight": "0.25", "row_num": 1}, {"asset_type": "equity", "resolution_state": "resolved",
                                               "constituent_global_security_id": "Y", "weight": "0.75", "row_num": 2}]
    lt, why = R.look_through(fund_id="FND", fund_label="FND", fund_value=D(20), snapshot=snap, rows=rows,
                             issuers={"X": {"issuer_key": "iX", "label": "X"}, "Y": {"issuer_key": "iY", "label": "Y"}})
    t = R.diversification_test([L("X", 20, "iX")] + lt + [gov(60)])
    x = next((i for i in t["issuers"] if i["issuer_key"] == "iX"), {})
    check(why is None and t["verdict"] == "pass" and D(x.get("value_exact", 0)) == D(25) and x.get("direct") == "20.00"
          and x.get("look_through") == "5.00" and x.get("via_funds") == ["FND"]
          and all(ln["holdings_as_of"] == "2026-11-01" for ln in t["lines"] if ln["via"] == "look_through"),
          "an ETF is looked through: X held directly (20) AND inside the ETF (5) is summed to 25.00 %; the holdings' "
          "as-of is shown", dumps(x))
    lt, why = R.look_through(fund_id="F2", fund_label="F2", fund_value=D(10), snapshot=None, rows=[], issuers={})
    t = R.diversification_test([L("B", 10, "iB")] + lt, blocked=[why] if why else [])
    check(lt == [] and why is not None and "holdings needed" in why and t["verdict"] == "holdings_needed",
          "a fund with NO holdings data fails closed ('holdings needed')", f"{why} {t['verdict']}")
    rows_u = [dict(rows[0]), {"asset_type": "equity", "resolution_state": "unresolved",
                              "constituent_global_security_id": None, "weight": "0.75", "row_num": 2,
                              "raw_ticker": "ZZQ"}]
    lt, why = R.look_through(fund_id="F3", fund_label="F3", fund_value=D(10), snapshot=snap, rows=rows_u,
                             issuers={"X": {"issuer_key": "iX"}})
    check(lt == [] and why is not None and "not resolved" in why,
          "a fund holding whose constituent is not resolved to an issuer fails closed", f"{why}")
    # control: 80.00 % passes, 79.99 % fails (exact)
    c = R.control_test([{"contributed_value": "8000", "cash_to_member": "0"}], seed_capital=D(2000))
    c2 = R.control_test([{"contributed_value": "7999", "cash_to_member": "0"}], seed_capital=D(2001))
    check(c["verdict"] == "pass" and D(c["contributors_share"]) == D("0.8") and c2["verdict"] == "fail"
          and c2["contributors_share_pct"] == "79.99%",
          "control: contributors at 80.00 % pass; at 79.99 % fail (§351(a) / §368(c))", f"{dumps(c)} {dumps(c2)}")
    c3 = R.control_test([{"contributed_value": "10000", "cash_to_member": "500"}], seed_capital=D(0))
    check(c3["boot_taxable"] is True and "TAXABLE" in c3["boot_note"] and c3["contributors_etf_value"] == "9500.00",
          "cash to a contributor is boot, shown TAXABLE; it comes off the contributor's ETF value", dumps(c3))
    # carryover with boot: lot g fmv 1000 basis 600 (gain 400), lot l fmv 500 basis 800 (loss 300); boot 150 split by
    # FMV → 100 / 50; recognized: g min(100, 400) = 100; l max(0, min(50, −300)) = 0 (no loss is recognized)
    # ETF basis: g 600 − 100 + 100 = 600; l 800 − 50 + 0 = 750; shares 30 by FMV → 20 / 10
    co = R.carryover([{"tax_lot_id": "g", "security_id": "s", "quantity": "10", "basis": "600",
                       "acquired_date": "2015-01-02", "fmv": "1000"},
                      {"tax_lot_id": "l", "security_id": "s", "quantity": "5", "basis": "800",
                       "acquired_date": "2026-06-01", "fmv": "500"}], etf_shares=D(30), cash_to_member=D(150),
                     on=date(2026, 11, 8))
    g, lo = co["lines"]
    check(g["boot_allocated"] == "100.00" and g["gain_recognized"] == "100.00" and g["etf_basis"] == "600.00"
          and lo["boot_allocated"] == "50.00" and lo["gain_recognized"] == "0.00" and lo["etf_basis"] == "750.00"
          and D(g["etf_shares"]) == 20 and D(lo["etf_shares"]) == 10 and g["etf_acquired_date"] == "2015-01-02"
          and lo["etf_acquired_date"] == "2026-06-01" and g["holding_period_on_transfer"] == "long"
          and lo["holding_period_on_transfer"] == "short" and co["totals"]["boot_taxable"] is True,
          "carryover: boot recognizes gain up to each lot's gain, never a loss; ETF basis = basis − boot + gain "
          "recognized; shares split by FMV; acquisition dates tacked per lot", dumps(co["lines"]))
    parts = R.cents_split(D("10.00"), [D(1), D(1), D(1)])
    check(parts == [D("3.34"), D("3.33"), D("3.33")] and sum(parts) == D("10.00"),
          "a split to the cent sums exactly (largest remainder)", f"{parts}")
    # fix-up (the coordinator's worked example): A 30 sh @ 100 (a1 10 @ 50, a2 20 @ 95, both 2015) + 14 issuers of
    # 10 sh @ 50 = 10,000; level L = 2,500 → sell 5 sh of a2 (tax/$ 0.05 × 0.20 < a1's 0.5 × 0.20); buy N1 @ 25,
    # capped at the 5th-largest issuer after the sales (500) → 20 sh; after: 25.00 % / 45.00 %, cash 0
    hold = [R.FixHolding(security_id="A", label="A", issuer_key="iA", issuer_label="A", price=D(100), quantity=D(30),
                         lots=[R.FixLot("a1", D(10), D(50), date(2015, 1, 2)),
                               R.FixLot("a2", D(20), D(95), date(2015, 1, 2))])]
    hold += [R.FixHolding(security_id=f"O{i}", label=f"O{i}", issuer_key=f"iO{i}", issuer_label=f"O{i}", price=D(50),
                          quantity=D(10), lots=[R.FixLot(f"o{i}", D(10), D(40), date(2015, 1, 2))]) for i in range(14)]
    fp = R.fixup_plan(hold, [{"security_id": "N1", "label": "N1", "issuer_key": "iN1", "price": D(25),
                              "weight": D(1)}], on=date(2026, 11, 8), rates={"st": ST_RATE, "lt": LT_RATE})
    check(fp["possible"] is True and [(s["lot_id"], D(s["quantity"])) for s in fp["sales"]] == [("a2", D(5))]
          and fp["sales"][0]["gain"] == "25.00" and [(b["security_id"], D(b["quantity"])) for b in fp["buys"]]
          == [("N1", D(20))] and fp["after"]["largest_issuer_pct"] == "25.00%"
          and fp["after"]["top_five_pct"] == "45.00%" and fp["cash_left"] == "0.00",
          "fix-up (pure): the least tax-per-dollar lot is sold only as far as the level needs; the proceeds buy a model "
          "name capped at the 5th-largest issuer; the result passes", dumps(fp))


# ── Task 2: candidates, eligibility, run-dry thresholds ────────────────────
async def candidates(uid):
    return await api(uid, "GET", f"{_P}/candidates")


def entry(body, acc):
    for k in ("eligible", "ineligible"):
        for e in (body or {}).get(k) or []:
            if e.get("account_id") == acct(acc):
                return k, e
    return None, {}


async def y_candidates(conn):
    section("[Y] Candidates and eligibility with reasons; the 'run dry' flag follows its versioned thresholds")
    code, body = await candidates(U_VIEW)
    k, e = entry(body, ACC_PASS)
    m = e.get("metrics") or {}
    # ACC_PASS: value 100,000 (cash 0); every lot cost $6 at $10 → gain 4 × 10,000 shares = 40,000; no loss lots
    check(code == 200 and k == "eligible" and m.get("account_value") == "100000.00"
          and m.get("unrealized_gain") == "40000.00" and m.get("harvestable_loss") == "0.00"
          and m.get("appreciated_value") == "100000.00" and e.get("run_dry", {}).get("run_dry") is False,
          "a replica account with lot-level data is a candidate: value, unrealised gain, harvestable loss shown; with "
          "the default thresholds (3 years) a new link is NOT run dry", dumps(e))
    check(isinstance((m.get("decay") or {}).get("label"), str) and (m.get("decay") or {}).get("estimated") is False,
          "the benefit-decay estimate is shown next to year 1 (here: not estimated — nothing harvested in year 1)",
          dumps(m.get("decay")))
    want = {ACC_AVG: "average_cost", ACC_HA: "held_away", ACC_ADV: "authority", ACC_NODATE: "lot_dates"}
    for acc, code_ in want.items():
        k, e = entry(body, acc)
        codes = {r["code"] for r in e.get("reasons") or []}
        check(k == "ineligible" and code_ in codes and all(r.get("reason") for r in e.get("reasons") or []),
              f"{S['masked'][acc]} is ineligible with the reason '{code_}' (each reason in words)", dumps(e))
    k, e = entry(body, ACC_HA)
    check("authority" in {r["code"] for r in e.get("reasons") or []},
          "the held-away account is also refused for its (read-only) authority", dumps(e))
    check((body or {}).get("actions") == [] and (body or {}).get("permissions", {}).get("can_write") is False,
          "a view-only caller gets no write action from the envelope", dumps((body or {}).get("actions")))
    bad_body = {"max_harvestable_loss_pct": "0.02", "min_unrealized_gain_pct": "0.4", "min_years_since_inception": "0"}
    c, o = await api(U_VIEW, "PUT", f"{_P}/thresholds", bad_body)
    check(c == 403 and "manage_org_settings" in dumps(o), "a view-only user cannot save thresholds (a VALID body; 403 "
          "naming manage_org_settings)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "PUT", f"{_P}/thresholds", bad_body)
    code, body = await candidates(U_VIEW)
    _, e = entry(body, ACC_PASS)
    # gain ratio 40,000 / 100,000 = 0.4 ≥ 0.4; loss 0 ≤ 0.02; years 0 ≥ 0 → run dry
    check(c == 200 and (o.get("thresholds") or {}).get("version_no") == 1 and e.get("run_dry", {}).get("run_dry") is True,
          "thresholds version 1 (gain ≥ 40 %, loss ≤ 2 %, 0 years): the account is RUN DRY exactly at the edge",
          f"{c} {dumps(o)} {dumps(e.get('run_dry'))}")
    c, o = await api(U_ADMIN1, "PUT", f"{_P}/thresholds", {**bad_body, "min_unrealized_gain_pct": "0.4000000001"})
    code, body = await candidates(U_VIEW)
    _, e = entry(body, ACC_PASS)
    hist = await scoped(conn, ORG_A, "SELECT version_no, valid_to IS NOT NULL AS closed FROM public.section351_settings "
                                     "WHERE org_id = $1 ORDER BY version_no", ORG_A)
    check(c == 200 and e.get("run_dry", {}).get("run_dry") is False
          and [(h["version_no"], h["closed"]) for h in hist] == [(1, True), (2, False)],
          "a thresholds edit is a NEW version (v1 closed, kept); one step past the edge the flag flips back",
          f"{dumps(e.get('run_dry'))} {[dict(h) for h in hist]}")


# ── sponsor profiles, owner types ──────────────────────────────────────────
async def y_profiles(conn):
    section("[Y] Sponsor profile: eligible / review / refused owner types; an edit is a new version")
    body = {"name": "REP14 Verify Sponsor", "adviser_name": "REP14 Verify Adviser", "contact": "desk@test.local",
            "launch_windows": [{"opens": (today() + timedelta(days=90)).isoformat(),
                                "closes": (today() + timedelta(days=120)).isoformat()}]}
    c, o = await api(U_VIEW, "POST", f"{_P}/sponsor-profiles", body)
    check(c == 403 and "manage_org_settings" in dumps(o), "a view-only user cannot create a sponsor profile (VALID "
          "body; 403 naming manage_org_settings)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/sponsor-profiles", body)
    p1 = (o or {}).get("profile") or {}
    check(c == 201 and p1.get("version_no") == 1 and p1.get("eligible_owner_types") == ["individual", "joint", "trust",
                                                                                          "s_corp"]
          and p1.get("review_owner_types") == ["partnership"] and p1.get("us_persons_only") is True
          and p1.get("cash_rule") == "not_contributed" and "derivative" in p1.get("ineligible_assets", [])
          and "crypto" in p1.get("ineligible_assets", []),
          "the org admin creates a profile; the default eligibility rules apply (individual / joint / trust / S-corp; "
          "partnership needs review; U.S. persons only; cash not contributed)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/sponsor-profiles/{p1.get('profile_key')}/versions",
                     {**body, "contact": "desk2@test.local"})
    p2 = (o or {}).get("profile") or {}
    rows = await scoped(conn, ORG_A, "SELECT id, version_no, valid_to IS NOT NULL AS closed FROM "
                                     "public.section351_sponsor_profiles WHERE org_id = $1 ORDER BY version_no", ORG_A)
    check(c == 201 and p2.get("version_no") == 2 and p2.get("profile_key") == p1.get("profile_key")
          and [(r["version_no"], r["closed"]) for r in rows] == [(1, True), (2, False)]
          and str(rows[0]["id"]) == p1.get("id"),
          "editing is a NEW version (v1 closed and kept, v2 live, same profile key)", f"{c} {[dict(r) for r in rows]}")
    S["p1"], S["p2"] = p1, p2
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            from services import account_models as AM
            prof = await S351.profile_version(c_, ORG_A, p2["id"])
            got = {}
            for acc in (ACC_PASS, ACC_PART, ACC_CCORP, ACC_NONUS, ACC_IRA):
                ot, _how = await S351.owner_type_of(c_, ORG_A, await AM.load_account(c_, ORG_A, acc))
                got[acc] = (ot, S351.owner_decision(ot, prof)["decision"])
    check(got == {ACC_PASS: ("individual", "eligible"), ACC_PART: ("partnership", "needs_sponsor_review"),
                  ACC_CCORP: ("c_corp", "refused"), ACC_NONUS: ("non_us", "refused"),
                  ACC_IRA: ("retirement", "refused")},
          "owner types under the profile: individual eligible; partnership / LLC needs sponsor review; C-corp, non-U.S. "
          "and retirement refused", dumps({S["masked"][k]: v for k, v in got.items()}))


# ── Task 3 through the database ────────────────────────────────────────────
async def acc_test(acc):
    return await api(U_VIEW, "GET", f"{_P}/accounts/{acct(acc)}/test?valuation_date={today().isoformat()}"
                                    f"&profile_id={S['p2']['id']}")


async def y_test_db(conn):
    section("[Y] The 25 / 50 test on fixture accounts (the DB contribution path, through the route)")
    c, o = await api(U_VIEW, "POST", f"{_P}/asset-classes", {"global_security_id": sec("GOV"),
                                                            "asset_class": "government", "source": "REP14 verify",
                                                            "as_of": today().isoformat()})
    check(c == 403, "a view-only user cannot classify a security (VALID body)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/asset-classes", {"global_security_id": sec("GOV"),
                                                              "asset_class": "government",
                                                              "source": "REP14 verify — Treasury", "as_of":
                                                              today().isoformat()})
    check(c == 201 and (o.get("asset_class") or {}).get("scope") == "org", "an org classification: GOV is a "
          "government security (entered, with source and as-of)", f"{c} {dumps(o)}")
    body = {"global_security_id": sec("PLT"), "asset_class": "restricted", "source": "REP14 verify",
            "as_of": today().isoformat()}
    c1, _ = await api(U_ADMIN1, "POST", f"{_P}/asset-classes/platform", body)
    c2, o2 = await api(U_SUPER, "POST", f"{_P}/asset-classes/platform", body)
    check(c1 == 403 and c2 == 201 and (o2.get("asset_class") or {}).get("scope") == "platform",
          "a PLATFORM classification is Super Admin only", f"{c1} {c2} {dumps(o2)}")
    c, o = await acc_test(ACC_PASS)
    t = (o or {}).get("test") or {}
    top = t.get("issuers", [{}])[0] if t.get("issuers") else {}
    check(c == 200 and t.get("verdict") == "pass" and t.get("largest_issuer_pct") == "25.00%"
          and t.get("top_five_pct") == "50.00%" and t.get("total_assets") == "100000.00"
          and t.get("government") == "50000.00" and top.get("issuer_key") == f"cik:{CIK_PAIR}"
          and D(top.get("value_exact", 0)) == D(25000) and len(top.get("securities", [])) == 2,
          "ACC_PASS: exactly 25.00 % / 50.00 % passes; the two share classes are ONE issuer (25,000); GOV in the total, "
          "not an issuer", dumps(t))
    lines = t.get("lines") or []
    check(bool(lines) and all(ln.get("valuation_source") and ln.get("valuation_as_of") for ln in lines)
          and D(t.get("margin_one", "1")) == ZERO and isinstance(t.get("top_five"), list) and len(t["top_five"]) == 5
          and t.get("label") == R.LABEL,
          "every line carries its valuation source and as-of; the top five and the margins are shown; labelled "
          "'estimate — confirm with counsel'", dumps(lines[:2]))
    for acc, want, frag in ((ACC_F2501, ("25.01%", "49.01%"), "one issuer"), (ACC_F5001, ("25.00%", "50.01%"), "five")):
        c, o = await acc_test(acc)
        t = (o or {}).get("test") or {}
        check(c == 200 and t.get("verdict") == "fail" and (t.get("largest_issuer_pct"), t.get("top_five_pct")) == want
              and len(t.get("reasons") or []) == 1 and frag in t["reasons"][0],
              f"{S['masked'][acc]}: {want[0]} / {want[1]} FAILS on the {frag} limit", dumps(t))
    c, o = await acc_test(ACC_LT)
    t = (o or {}).get("test") or {}
    x = next((i for i in t.get("issuers") or [] if i.get("issuer_key") == f"security:{sec('X')}"), {})
    lt_lines = [ln for ln in t.get("lines") or [] if ln.get("via") == "look_through"]
    check(c == 200 and t.get("verdict") == "pass" and D(x.get("value_exact", 0)) == D(25000)
          and x.get("direct") == "20000.00" and x.get("look_through") == "5000.00"
          and len(lt_lines) == 2 and all(ln.get("holdings_as_of") == S["snap_as_of"].isoformat() for ln in lt_lines),
          "ACC_LT: X held directly (20,000) AND inside the ETF (5,000) is summed — 25.00 %; the holdings' as-of is "
          "shown", dumps(t))
    c, o = await acc_test(ACC_NOH)
    t = (o or {}).get("test") or {}
    check(c == 200 and t.get("verdict") == "holdings_needed" and any("holdings needed" in b for b in t.get("blocked")
                                                                     or []),
          "ACC_NOH: a fund with no holdings data FAILS CLOSED ('holdings needed')", dumps(t))
    c, o = await acc_test(ACC_INEL)
    t = (o or {}).get("test") or {}
    ex = {x.get("security_id"): x.get("classes") for x in t.get("excluded") or []}
    in_lines = {ln.get("security_id") for ln in t.get("lines") or []}
    check(c == 200 and ex.get(sec("OPT")) == ["derivative"] and ex.get(sec("CRY")) == ["crypto"]
          and sec("OPT") not in in_lines and sec("CRY") not in in_lines and t.get("contributed_value") == "10000.00",
          "ACC_INEL: the option leg and the crypto asset are EXCLUDED and listed (contributed 10,000 = B + GOV)",
          dumps(t))


# ── Task 4: fix-up proposal ────────────────────────────────────────────────
async def y_fixup(conn):
    section("[Y] Fix-up proposal passes after the hand-computed sales; tax estimate from the rep6 engine; "
            "maker-checker")
    # ACC_FIX: A 30,000 of a 100,000 total (B–E 2,500, GOV 60,000) → level L = 25,000 (top five of min(v, L) =
    # 25,000 + 10,000 = 35 % ≤ 50 %). A sells 5,000: lot a1 (cost 95: 0.05 × 0.20 tax per $) before a2 (cost 50:
    # 0.5 × 0.20) → 50 sh of a1, gain 50 × 5 = 250 LT. Proceeds 5,000 → M1 and M2 (50 % each, $100), each capped at
    # the 5th-largest issuer after the sales (2,500) → 25 sh each. After: A 25.00 %, top five 35.00 %. Tax = 250 ×
    # 20 % = 50.00. The account's 1,000 cash is outside the test; the engine's cash target leaves 900.
    body = {"valuation_date": today().isoformat(), "sponsor_profile_id": S["p2"]["id"]}
    c, o = await api(U_VIEW, "POST", f"{_P}/accounts/{acct(ACC_FIX)}/fixup", body)
    check(c == 403 and "manage_proposals" in dumps(o), "a view-only user cannot make a fix-up proposal (VALID body)",
          f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/accounts/{acct(ACC_PASS)}/fixup", body)
    check(c == 409 and "already passes" in dumps(o), "a passing account gets no fix-up (409)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/accounts/{acct(ACC_FIX)}/fixup", body)
    f = (o or {}).get("fixup") or {}
    pid = f.get("proposal_id")
    S["fix_pid"] = pid
    trades = await scoped(conn, ORG_A, "SELECT global_security_id, side, quantity, lots, est_st_gain, est_lt_gain FROM "
                                       "public.rebalance_proposal_trades WHERE proposal_id = $1::uuid ORDER BY line_no",
                          pid) if pid else []
    got = sorted((S["key_of"].get(str(t["global_security_id"])), t["side"], D(t["quantity"])) for t in trades)
    sell = next((t for t in trades if t["side"] == "sell"), None)
    lots = json.loads(sell["lots"]) if sell else []
    check(c == 201 and got == [("A1", "sell", D(50)), ("M1", "buy", D(25)), ("M2", "buy", D(25))]
          and [(x.get("lot_id"), D(x.get("quantity"))) for x in lots] == [(S["lots"][(ACC_FIX, "A1", 0)], D(50))]
          and f.get("matches_plan") is True,
          "the proposal's trades are the hand-computed ones: SELL 50 A from lot a1 (least tax per dollar), BUY 25 M1 "
          "+ 25 M2; they match the plan", f"{c} {got} {dumps(lots)} {dumps(f)}")
    after = f.get("after") or {}
    check(after.get("verdict") == "pass" and after.get("largest_issuer_pct") == "25.00%"
          and after.get("top_five_pct") == "35.00%" and (f.get("before") or {}).get("verdict") == "fail",
          "the account FAILS before and PASSES after the proposal's own trades (25.00 % / 35.00 %)", dumps(after))
    p = await scoped(conn, ORG_A, "SELECT status, est_st_gain, est_lt_gain, maker_id, proposal_kind, trigger FROM "
                                  "public.rebalance_proposals WHERE id = $1::uuid AND system_to IS NULL", pid,
                     fetch="fetchrow") if pid else None
    rates = S.get("rates") or {}
    engine_tax = R.money(D(p["est_st_gain"]) * rates["st"] + D(p["est_lt_gain"]) * rates["lt"]) if p and rates else None
    tax = f.get("tax") or {}
    check(p is not None and p["status"] == "draft" and p["maker_id"] == U_ADMIN1 and D(p["est_lt_gain"]) == D(250)
          and D(p["est_st_gain"]) == ZERO and engine_tax == D("50.00") and D(tax.get("tax") or -1) == engine_tax
          and tax.get("label") == R.LABEL,
          "the tax cost is the rep6 engine's estimate on the stored proposal: LT gain 250 × 20 % = 50.00, labelled an "
          "estimate", f"{dict(p or {})} {dumps(tax)} {engine_tax}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/submit", {})
    c2, o2 = await api(U_ADMIN1, "POST", f"/api/v1/proposals/{pid}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.rebalance_proposals WHERE id = $1::uuid AND system_to IS "
                                   "NULL", pid, fetch="fetchval") if pid else None
    check(c == 200 and c2 == 403 and st == "pending_approval",
          "it goes through the normal rep5 maker-checker: submitted, the MAKER cannot approve it (403), it stays "
          "pending; nothing executed", f"{c} {c2} {st} {dumps(o2)}")
    n = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions WHERE account_id = $1",
                         ACC_FIX, fetch="fetchval"))
    check(n == 0, "no execution instruction exists for the fix-up account (nothing executed automatically)", f"{n}")


# ── Tasks 5–8: the pool ────────────────────────────────────────────────────
async def plan_detail(uid=U_ADMIN1, pid=None):
    return await api(uid, "GET", f"{_P}/plans/{pid or S['plan']}")


def pa_of(d, acc) -> dict:
    return next((a for a in (d or {}).get("accounts") or [] if a.get("account_id") == acct(acc)
                 and a.get("status") != "removed"), {})


def failing(out) -> list[tuple]:
    det = (out or {}).get("detail") or {}
    gates = det.get("gates") if isinstance(det, dict) else None
    return sorted((g.get("gate"), g.get("account_id")) for g in gates or [])


async def ready(uid=U_ADMIN1):
    return await api(uid, "POST", f"{_P}/plans/{S['plan']}/ready", {})


async def upload_disclosure(n: int, eff: date):
    data = f"# Section 351 exit — REP14 verify version {n}\n\nDistinct content {n} {uuid.uuid4()}\n".encode()
    body = {"scope": "org", "file_name": f"rep14_v{n}.md", "content_base64": base64.b64encode(data).decode(),
            "summary": f"REP14 verify section351.exit org version {n}", "effective_from": eff.isoformat()}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/disclosures/keys/section351.exit/versions", body)
    vid = ((o or {}).get("version") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/disclosures/versions/{vid}/publish", {}) if vid else (c, o)
    return c, c2, vid, o2


async def ack_all(accs):
    out = []
    code, d = await plan_detail()
    for acc in accs:
        pa = pa_of(d, acc)
        vid = (((pa.get("disclosure") or {}).get("resolved")) or {}).get("version_id")
        c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{pa.get('id')}/acknowledge", {"version_id": vid})
        out.append((c, ((o or {}).get("disclosure") or {}).get("current")))
    return out


async def y_pool(conn):
    section("[Y] Pool: plan + tracker; control at the edge; boot taxable; 'ready' gates (each separately); disclosure")
    body = {"name": "REP14 Verify Exit", "target_etf": "REP14 Verify Exit ETF (to launch)",
            "sponsor_profile_id": S["p1"]["id"], "valuation_date": today().isoformat(),
            "etf_custodian_account": "REP14 verify ETF custodian account (text)"}
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans", body)
    check(c == 409 and "replaced" in dumps(o), "a plan cannot use a replaced sponsor-profile version (v1)",
          f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans", {**body, "sponsor_profile_id": S["p2"]["id"]})
    pl = (o or {}).get("plan") or {}
    S["plan"] = pl.get("id")
    tl = (o or {}).get("timeline") or {}
    steps = tl.get("steps") or []
    check(c == 201 and pl.get("status") == "draft" and pl.get("sponsor_profile_id") == S["p2"]["id"]
          and pl.get("sponsor_profile_version") == 2,
          "a plan records the sponsor profile VERSION it used (v2)", f"{c} {dumps(pl)}")
    check(len(steps) == 8 and all(s.get("task_id") for s in steps) and [s["step_key"] for s in steps] ==
          [k for k, _, _ in S351.STEPS] and tl.get("within_6_to_9_months") is True,
          "the project tracker: the 8 standard steps, each a human task on the workflow engine, due over a 6–9 month "
          "timeline", dumps(tl))
    if not S["plan"]:
        return
    tasks = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_run_steps rs JOIN public.workflow_runs r"
                                          " ON r.id = rs.workflow_run_id WHERE r.org_id = $1 AND rs.status = 'active' "
                                          "AND rs.id = ANY($2::uuid[])", ORG_A,
                             [UUID(s["task_id"]) for s in steps if s.get("task_id")], fetch="fetchval"))
    check(tasks == 8, "the 8 step tasks are open on the engine", f"{tasks}")
    st0 = steps[0]
    c, o = await api(U_VIEW, "POST", f"{_P}/steps/{st0['id']}/done", {})
    check(c == 403, "a view-only user cannot complete a step (403)", f"{c} {dumps(o)}")
    new_due = (today() + timedelta(days=20)).isoformat()
    c, o = await api(U_ADMIN1, "PATCH", f"{_P}/steps/{st0['id']}", {"due_on": new_due, "owner_id": str(U_ADMIN2)})
    c2, o2 = await api(U_ADMIN1, "POST", f"{_P}/steps/{st0['id']}/done", {"note": "counsel engaged (verify)"})
    s2 = (o2 or {}).get("step") or {}
    check(c == 200 and (o.get("step") or {}).get("due_on") == new_due and c2 == 200 and s2.get("status") == "done"
          and s2.get("done_by") == str(U_ADMIN1),
          "a step's owner and due date are edited; it is completed through its human task (who and when recorded)",
          f"{c} {c2} {dumps(o2)}")
    # accounts
    adds = {}
    for acc in (ACC_PASS, ACC_PART, ACC_FIX, ACC_CCORP):
        adds[acc] = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/accounts", {"account_id": acct(acc)})
    a_pass = (adds[ACC_PASS][1] or {}).get("account") or {}
    a_part = (adds[ACC_PART][1] or {}).get("account") or {}
    a_fix = (adds[ACC_FIX][1] or {}).get("account") or {}
    check(adds[ACC_PASS][0] == 201 and a_pass.get("test_verdict") == "pass" and a_pass.get("owner_type") == "individual"
          and D(a_pass.get("contributed_value") or 0) == D(100000),
          "ACC_PASS joins the pool (individual, test PASS at the valuation date, contributes 100,000)",
          dumps(adds[ACC_PASS]))
    check(adds[ACC_PART][0] == 201 and (a_part.get("eligibility") or {}).get("decision") == "needs_sponsor_review",
          "the partnership account joins as 'needs sponsor review'", dumps(adds[ACC_PART]))
    check(adds[ACC_FIX][0] == 201 and a_fix.get("test_verdict") == "fail", "the failing account is in the pool "
          "(test FAIL)", dumps(adds[ACC_FIX]))
    check(adds[ACC_CCORP][0] == 409 and "C corporation" in dumps(adds[ACC_CCORP][1]),
          "a C-corp account is refused with the reason (409)", dumps(adds[ACC_CCORP]))
    S["pa"] = {ACC_PASS: a_pass.get("id"), ACC_PART: a_part.get("id"), ACC_FIX: a_fix.get("id")}
    # record everything EXCEPT: ACC_PASS's consent and ACC_PART's tax adviser (each shown missing on its own)
    for acc in (ACC_PASS, ACC_PART, ACC_FIX):
        pa = S["pa"][acc]
        await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{pa}/us-person",
                  {"us_person": "yes", "source": "REP14 verify KYC file", "as_of": today().isoformat()})
        if acc != ACC_PASS:
            await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{pa}/consent",
                      {"document_id": str(S["doc"][acc]), "signed_on": today().isoformat(),
                       "signer": f"Member of {S['masked'][acc]}"})
        if acc != ACC_PART:
            await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{pa}/tax-adviser",
                      {"adviser_name": "REP14 Verify CPA", "confirmed_on": today().isoformat()})
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PART]}/sponsor-review",
                     {"decision": "accepted", "note": "the sponsor reviewed the LLC (verify)",
                      "reviewed_on": today().isoformat()})
    check(c == 200 and ((o.get("account") or {}).get("eligibility") or {}).get("sponsor_review", {}).get("decision")
          == "accepted", "the sponsor's review of the partnership is recorded (accepted, dated)", f"{c} {dumps(o)}")
    # no disclosure version in force yet (the library is the only source; never text in code)
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c_:
            from services import disclosures as DL
            res = await DL.resolve(c_, "section351.exit", org_id=ORG_A, account_id=acct(ACC_PASS))
    c, o = await ready()
    fl = failing(o)
    if res["source"] == "library":
        find("section351.exit already in force for the fixture org", f"a {res['scope']} version {res['version_no']} "
             "is published — the 'no version in force' case cannot be shown on this database")
    else:
        check(c == 409 and ("disclosure", acct(ACC_PASS)) in fl and "never text in code" in dumps(o),
              "with NO section351.exit version published, 'ready' is refused on the disclosure gate (no text in code)",
              f"{c} {fl}")
    # publish org v1 (maker uploads, a second person publishes), acknowledge all three
    cu, cp, v1, _ = await upload_disclosure(1, today() - timedelta(days=2))
    acks = await ack_all((ACC_PASS, ACC_PART, ACC_FIX))
    check(cu == 201 and cp == 200 and acks == [(200, True)] * 3,
          "an org section351.exit version is published (draft → a second person) and acknowledged for each account "
          "at that version", f"{cu} {cp} {acks}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PASS]}/acknowledge",
                     {"version_id": str(uuid.uuid4())})
    check(c == 409 and "not the version in force" in dumps(o), "acknowledging a version that is not in force is "
          "refused (VALID body)", f"{c} {dumps(o)}")
    c, o = await ready()
    fl = failing(o)
    check(c == 409 and ("diversification", acct(ACC_FIX)) in fl and ("consent", acct(ACC_PASS)) in fl
          and ("tax_adviser", acct(ACC_PART)) in fl and len(fl) == 3,
          "'ready' is refused while one account FAILS the test, one consent and one tax-adviser confirmation are "
          "missing — exactly those three gates, each on its own account", f"{c} {fl}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_FIX]}/remove",
                     {"reason": "fails the 25 / 50 test — the fix-up proposal is pending"})
    c2, o2 = await ready()
    check(c == 200 and failing(o2) == sorted([("consent", acct(ACC_PASS)), ("tax_adviser", acct(ACC_PART))]),
          "with the failing account removed, only the missing consent and tax-adviser confirmation refuse",
          f"{c} {failing(o2)}")
    await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PASS]}/consent",
              {"document_id": str(S["doc"][ACC_PASS]), "signed_on": today().isoformat(), "signer": "Member PASS"})
    c, o = await ready()
    check(c == 409 and failing(o) == [("tax_adviser", acct(ACC_PART))],
          "with the consent recorded, the missing tax-adviser confirmation alone refuses 'ready'", f"{c} {failing(o)}")
    await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PART]}/tax-adviser",
              {"adviser_name": "REP14 Verify CPA 2", "confirmed_on": today().isoformat(), "note": "confirmed by phone"})
    # control: contributed 100,000 + 10,000 = 110,000; seed 27,520 → 110,000 / 137,520 = 79.99 % (fails);
    # seed 27,500 → 110,000 / 137,500 = 80.00 % (passes)
    c, o = await api(U_ADMIN1, "PATCH", f"{_P}/plans/{S['plan']}", {"seed_capital": "27520"})
    c2, o2 = await ready()
    ctl = ((o or {}).get("readiness") or {}).get("control") or {}
    check(c == 200 and ctl.get("contributors_share_pct") == "79.99%" and ctl.get("verdict") == "fail"
          and c2 == 409 and failing(o2) == [("control", None)],
          "outside seed 27,520: contributors 79.99 % — control FAILS and alone refuses 'ready'", f"{dumps(ctl)} "
          f"{failing(o2)}")
    c, o = await api(U_ADMIN1, "PATCH", f"{_P}/plans/{S['plan']}", {"seed_capital": "27500"})
    ctl = ((o or {}).get("readiness") or {}).get("control") or {}
    check(c == 200 and ctl.get("contributors_share_pct") == "80.00%" and ctl.get("verdict") == "pass"
          and D(ctl.get("contributors_share")) == D("0.8"),
          "outside seed 27,500: contributors exactly 80.00 % — control PASSES", dumps(ctl))
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PART]}/boot", {"amount": "1000"})
    c2, d = await plan_detail()
    ctl = ((d or {}).get("readiness") or {}).get("control") or {}
    check(c == 200 and ctl.get("boot_taxable") is True and "TAXABLE" in ctl.get("boot_note", "")
          and ctl.get("cash_to_contributors") == "1000.00",
          "cash to a contributor is shown as boot — TAXABLE", dumps(ctl))
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PART]}/boot", {"amount": "0"})
    c, o = await ready()
    pl = (o or {}).get("plan") or {}
    check(c == 200 and pl.get("status") == "ready" and (pl.get("readiness") or {}).get("ok") is True,
          "every gate met: the plan is READY (readiness stored with the control result)", f"{c} {dumps(o)}")
    S["ready_ok"] = c == 200


async def y_basis(conn):
    section("[Y] Basis record exact (lots, carryover, tacked dates), totals to the cent, exported and versioned")
    c, o = await api(U_VIEW, "POST", f"{_P}/plans/{S['plan']}/basis-record", {})
    check(c == 403, "a view-only user cannot export the basis record", f"{c}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/basis-record", {})
    r1 = (o or {}).get("basis_record") or {}
    t = r1.get("totals") or {}
    # basis: ACC_PASS 10,000 sh × $6 = 60,000; ACC_PART 1,000 sh × $7 = 7,000 → 67,000; value 110,000
    check(c == 201 and r1.get("version_no") == 1 and t.get("lots") == 10 and t.get("basis") == "67000.00"
          and t.get("fmv") == "110000.00" and t.get("etf_basis") == "67000.00"
          and t.get("plan_contributed_value") == "110000.00" and t.get("reconciles_to_plan") is True,
          "version 1: 10 lots, basis 67,000.00, value 110,000.00 — reconciles to the plan to the cent", f"{c} {dumps(o)}")
    csv_keys = [k for k in PUT_LOG if f"/section351/{S['plan']}/basis-record-v1-" in k and k.endswith(".csv")]
    data = BLOBS.get(csv_keys[0]) if csv_keys else b""
    check(len(csv_keys) == 1 and hashlib.sha256(data).hexdigest() == r1.get("csv_sha256"),
          "the CSV is stored under the org's section351 folder; its bytes hash to the recorded sha256", f"{csv_keys}")
    rows = [r for r in csv.reader(io.StringIO(data.decode("utf-8"))) if r and not r[0].startswith("#")]
    head, body = (rows[0], rows[1:-1]) if rows else ([], [])
    idx = {h: i for i, h in enumerate(head)}
    acq = (S["seed_day"] - timedelta(days=800)).isoformat()
    want_basis = {S["lots"][(ACC_PASS, "A1", 0)]: "9000.00", S["lots"][(ACC_PASS, "GOV", 0)]: "30000.00",
                  S["lots"][(ACC_PART, "B", 0)]: "1400.00"}
    got = {r[idx["tax_lot_id"]]: r for r in body} if idx else {}
    check(len(body) == 10 and all(got.get(k, [None] * 20)[idx.get("basis", 0)] == v for k, v in want_basis.items())
          and all(r[idx["etf_basis_carryover"]] == r[idx["basis"]] for r in body)
          and all(r[idx["etf_acquired_date_tacked"]] == r[idx["acquired_date"]] for r in body)
          and got.get(S["lots"][(ACC_PASS, "A1", 0)], [None] * 20)[idx.get("acquired_date", 0)] == acq
          and all(r[idx["holding_period_at_valuation"]] == "long" for r in body)
          and rows[-1][0] == "TOTAL" and rows[-1][idx["basis"]] == "67000.00",
          "every lot: quantity, basis, acquisition date, holding period; the ETF basis carries over and the date is "
          "tacked; the TOTAL row reconciles", dumps(body[:2]))
    code, content = await api_raw(U_VIEW, "GET", f"{_P}/basis-records/{r1.get('id')}/file/csv")
    check(code == 200 and content == data, "the stored CSV is read back through the API (hash-checked)", f"{code}")
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/basis-record", {})
    r2 = (o or {}).get("basis_record") or {}
    vers = await scoped(conn, ORG_A, "SELECT version_no, valid_to IS NOT NULL AS closed FROM "
                                     "public.section351_basis_records WHERE plan_id = $1::uuid ORDER BY version_no",
                        S["plan"])
    docs = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.documents WHERE org_id = $1 AND doc_family = "
                                         "'section351_basis_record'", ORG_A, fetch="fetchval"))
    check(c == 201 and r2.get("version_no") == 2 and [(v["version_no"], v["closed"]) for v in vers]
          == [(1, True), (2, False)] and docs == 4,
          "a second export is version 2; version 1 is closed and kept; 4 vault documents (CSV + PDF each)",
          f"{c} {[dict(v) for v in vers]} docs={docs}")
    S["basis_ok"] = c == 201


async def y_disclosure_reack(conn):
    section("[Y] A newly published section351.exit version requires re-acknowledgement (ready / submit / approval)")
    cu, cp, v2, _ = await upload_disclosure(2, today() - timedelta(days=1))
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/submit", {})
    check(cu == 201 and cp == 200 and c == 409 and ("disclosure", acct(ACC_PASS)) in failing(o)
          and "newer version" in dumps(o),
          "after version 2 is published, SUBMIT is refused until it is re-acknowledged", f"{cu} {cp} {c} {failing(o)}")
    await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/reopen", {})
    c, o = await ready()
    check(c == 409 and sorted(g for g, _ in failing(o)) == ["disclosure", "disclosure"],
          "reopened, READY is refused on the disclosure gate of both accounts", f"{c} {failing(o)}")
    acks = await ack_all((ACC_PASS, ACC_PART))
    c, o = await ready()
    c2, o2 = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/submit", {})
    check(acks == [(200, True)] * 2 and c == 200 and c2 == 200
          and ((o2 or {}).get("plan") or {}).get("status") == "pending_approval",
          "re-acknowledged at version 2: ready, then submitted (a human task awaits a second person)",
          f"{acks} {c} {c2} {dumps(o2)}")
    cu, cp, v3, _ = await upload_disclosure(3, today())
    c, o = await api(U_ADMIN2, "POST", f"{_P}/plans/{S['plan']}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.section351_plans WHERE id = $1::uuid AND system_to IS "
                                   "NULL", S["plan"], fetch="fetchval")
    check(cu == 201 and cp == 200 and c == 409 and "no longer ready" in dumps(o) and st == "pending_approval",
          "version 3 published while pending: the APPROVAL is refused (the precheck) and the plan stays pending",
          f"{c} {dumps(o)} {st}")
    acks = await ack_all((ACC_PASS, ACC_PART))
    check(acks == [(200, True)] * 2, "re-acknowledged at version 3", f"{acks}")


async def y_transfer(conn):
    section("[Y] Approval → in-kind transfer instructions; adapter refuses; manual route; confirmation closes lots by "
            "transfer (no realised gain); the ETF carries the basis")
    c1, o1 = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/approve", {})
    c2, o2 = await api(U_VIEW, "POST", f"{_P}/plans/{S['plan']}/approve", {})
    check(c1 == 403 and c2 == 403 and "approve_proposals" in dumps(o2),
          "the MAKER cannot approve the plan (403); a view-only user neither (403 naming approve_proposals)",
          f"{c1} {dumps(o1)} {c2}")
    c, o = await api(U_ADMIN2, "POST", f"{_P}/plans/{S['plan']}/approve", {"note": "approved (verify)"})
    pl = (o or {}).get("plan") or {}
    xs = (o or {}).get("transfers") or []
    by_acc = {}
    for x in xs:
        by_acc.setdefault(x["account_id"], []).append(x)
    check(c == 200 and pl.get("status") == "approved" and pl.get("approved_by") == str(U_ADMIN2)
          and len(xs) == 10 and len(by_acc.get(acct(ACC_PASS), [])) == 7 and len(by_acc.get(acct(ACC_PART), [])) == 3
          and all(x["kind"] == "in_kind_transfer_out" and x["status"] == "approved" for x in xs),
          "a different person approves: one 'in-kind transfer out' instruction per contributed lot (7 + 3)",
          f"{c} {dumps(pl)} {len(xs)}")
    rows = await scoped(conn, ORG_A, "SELECT maker_ids, approved_by FROM public.section351_transfers WHERE plan_id = "
                                     "$1::uuid AND system_to IS NULL", S["plan"])
    check(bool(rows) and all(U_ADMIN1 in r["maker_ids"] and r["approved_by"] == U_ADMIN2 for r in rows),
          "every instruction names its maker(s) and a different approver (maker-checker CHECK)",
          f"{[dict(r) for r in rows[:2]]}")
    ins = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions WHERE account_id = "
                                        "ANY($1::uuid[])", [ACC_PASS, ACC_PART], fetch="fetchval"))
    check(ins == 0, "no trade instruction exists for the contributing accounts (no trade, no sale)", f"{ins}")
    body = {"account_id": acct(ACC_PASS), "route": "adapter", "channel": "API"}
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/route", body)
    det = (o or {}).get("detail") or {}
    check(c == 409 and isinstance(det, dict) and det.get("manual_route") is True
          and "does not declare in_kind_transfers" in det.get("message", ""),
          "the adapter route REFUSES (the custodian does not declare in_kind_transfers); the manual route is offered",
          f"{c} {dumps(o)}")
    code, pdf = await api_raw(U_VIEW, "GET", f"{_P}/plans/{S['plan']}/instruction-sheet")
    check(code == 200 and pdf[:5] == b"%PDF-", "the manual instruction sheet (PDF) is printed", f"{code} {pdf[:20]}")
    S["ytd_before"] = {}
    with _ctx(ORG_A):
        pool = await get_pool()
        await LB.ingest(pool, org_id=ORG_A)
        async with pool.acquire() as c_:
            for a in (ACC_PASS, ACC_PART):
                S["ytd_before"][a] = await RP.realized_ytd(c_, ORG_A, acct(a), today())
    lb_before = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.loss_bank_entries WHERE org_id = $1", ORG_A,
                                 fetch="fetchval"))
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/route",
                     {"account_id": acct(ACC_PASS), "route": "manual", "channel": "signed letter to the custodian"})
    check(c == 200 and (o or {}).get("sent") == 7 and all(x["status"] == "sent" and x["routed_via"] == "manual"
                                                          for x in (o or {}).get("transfers") or []),
          "the manual route marks the account's 7 instructions SENT (by this person, with the channel)",
          f"{c} {dumps(o)}")
    old_lots = [S["lots"][(ACC_PASS, k, 0)] for k in ("A1", "A2", "B", "C", "D", "E", "GOV")]
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PASS]}/confirm",
                     {"received_on": today().isoformat(), "etf_global_security_id": sec("NEWETF"),
                      "etf_shares": "10000"})
    xs = (o or {}).get("transfers") or []
    check(c == 200 and len(xs) == 7 and all(x["status"] == "confirmed" and D(x["realized_gain"]) == ZERO for x in xs),
          "the custodian's confirmation is recorded: every transfer CONFIRMED with realized gain 0", f"{c} {dumps(o)}")
    lot_rows = await scoped(conn, ORG_A, "SELECT id, status, valid_to IS NOT NULL AS closed_version, "
                                         "realized_gain_loss, external_ref, quantity, adjusted_cost, acquired_date FROM "
                                         "portfolio.tax_lots WHERE id = ANY($1::uuid[]) OR external_ref = ANY($2::text[])",
                            [UUID(x) for x in old_lots], [f"section351:transfer:{x['id']}" for x in xs])
    olds = [r for r in lot_rows if str(r["id"]) in old_lots]
    news = [r for r in lot_rows if (r["external_ref"] or "").startswith("section351:transfer:")]
    check(len(olds) == 7 and all(r["closed_version"] and r["status"] == "open" for r in olds)
          and len(news) == 7 and all(r["status"] == "closed" and r["realized_gain_loss"] is None for r in news),
          "each lot closes BY TRANSFER: the open version is closed (valid_to), a closed row records it with NO realised "
          "gain (realized_gain_loss NULL — not a sale)", f"{[dict(r) for r in lot_rows][:3]}")
    with _ctx(ORG_A):
        pool = await get_pool()
        await LB.ingest(pool, org_id=ORG_A)
        async with pool.acquire() as c_:
            ytd_after = await RP.realized_ytd(c_, ORG_A, acct(ACC_PASS), today())
    lb_after = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.loss_bank_entries WHERE org_id = $1", ORG_A,
                                fetch="fetchval"))
    check(ytd_after == S["ytd_before"][ACC_PASS] and lb_after == lb_before,
          "realized YTD and the loss bank are unchanged by the transfer (nothing was realised)",
          f"{S['ytd_before'][ACC_PASS]} → {ytd_after}; loss bank {lb_before} → {lb_after}")
    pos = await scoped(conn, ORG_A, "SELECT p.id, p.quantity, p.cost_basis, p.market_value FROM portfolio.positions p "
                                    "JOIN portfolio.assets a ON a.id = p.asset_id WHERE p.account_id = $1 AND "
                                    "a.global_security_id = $2 AND p.valid_to IS NULL AND p.system_to IS NULL", ACC_PASS,
                       S["sec"]["NEWETF"])
    etf_lots = await scoped(conn, ORG_A, "SELECT quantity, adjusted_cost, acquired_date, external_ref FROM "
                                         "portfolio.tax_lots WHERE position_id = $1 AND status = 'open' AND valid_to IS "
                                         "NULL AND system_to IS NULL ORDER BY quantity DESC, adjusted_cost DESC",
                            pos[0]["id"]) if len(pos) == 1 else []
    # shares by FMV: A1 15,000 / 100,000 × 10,000 = 1,500 (basis 1,500 × $6 = 9,000); GOV 5,000 (30,000); …
    want = sorted([(D(1500), D(9000)), (D(1000), D(6000)), (D(1000), D(6000)), (D(500), D(3000)), (D(500), D(3000)),
                   (D(500), D(3000)), (D(5000), D(30000))], reverse=True)
    got = [(D(r["quantity"]), D(r["adjusted_cost"])) for r in etf_lots]
    acq = S["seed_day"] - timedelta(days=800)
    check(len(pos) == 1 and D(pos[0]["quantity"]) == D(10000) and D(pos[0]["cost_basis"]) == D(60000)
          and got == want and sum(q for q, _ in got) == D(10000) and all(r["acquired_date"] == acq for r in etf_lots),
          "the ETF position: 10,000 shares at carryover basis 60,000; one lot per contributed lot (shares by FMV, "
          "summing EXACTLY) with its basis and the tacked acquisition date", f"{[dict(p) for p in pos]} {got}")
    left = await scoped(conn, ORG_A, "SELECT a.global_security_id, p.quantity FROM portfolio.positions p JOIN "
                                     "portfolio.assets a ON a.id = p.asset_id WHERE p.account_id = $1 AND "
                                     "p.valid_to IS NULL AND p.system_to IS NULL AND a.global_security_id <> $2",
                        ACC_PASS, S["sec"]["NEWETF"])
    check(bool(left) and all(D(r["quantity"]) == ZERO for r in left),
          "the contributed positions are restated to what is left (zero)", f"{[dict(r) for r in left]}")
    await api(U_ADMIN1, "POST", f"{_P}/plans/{S['plan']}/route",
              {"account_id": acct(ACC_PART), "route": "manual", "channel": "signed letter to the custodian"})
    c, o = await api(U_ADMIN1, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PART]}/confirm",
                     {"received_on": today().isoformat(), "etf_global_security_id": sec("NEWETF"), "etf_shares": "1000"})
    st = await scoped(conn, ORG_A, "SELECT status FROM public.section351_plans WHERE id = $1::uuid AND system_to IS "
                                   "NULL", S["plan"], fetch="fetchval")
    check(c == 200 and st == "completed", "the last account confirmed: the plan is COMPLETED", f"{c} {st} {dumps(o)}")


async def y_envelope(conn):
    section("[Y] Permission envelope: view-only gets no write control (server side and envelope)")
    c, d = await plan_detail(U_VIEW)
    check(c == 200 and d.get("actions") == [] and d.get("vocabularies", {}).get("editable") == []
          and d.get("vocabularies", {}).get("admin_editable") == [] and d.get("permissions", {}).get("can_write")
          is False, "a view-only caller reads the plan; the envelope carries no action and empty editable lists",
          dumps({k: d.get(k) for k in ("actions", "permissions")}))
    c, o = await api(U_VIEW, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PASS]}/tax-adviser",
                     {"adviser_name": "X", "confirmed_on": today().isoformat()})
    check(c == 403 and "manage_proposals" in dumps(o), "…and the server refuses its VALID write (403 naming "
          "manage_proposals)", f"{c} {dumps(o)}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); no model call, no network call")
    c, o = await api(U_ADMIN_B, "POST", f"{_P}/sponsor-profiles", {"name": "REP14 Verify Sponsor B"})
    pb = (o or {}).get("profile") or {}
    c2, o2 = await api(U_ADMIN_B, "POST", f"{_P}/plans", {"name": "REP14 Verify B", "target_etf": "B ETF",
                                                          "sponsor_profile_id": pb.get("id"),
                                                          "valuation_date": today().isoformat()})
    plan_b = ((o2 or {}).get("plan") or {}).get("id")
    check(c == 201 and c2 == 201 and bool(plan_b), "org B makes its own profile and plan", f"{c} {c2} {dumps(o2)}")
    c, o = await api(U_ADMIN_B, "GET", f"{_P}/plans/{S.get('plan')}")
    check(c == 404, "org B cannot read org A's plan (404)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN_B, "POST", f"{_P}/plan-accounts/{S['pa'][ACC_PASS]}/tax-adviser",
                     {"adviser_name": "B CPA", "confirmed_on": today().isoformat()})
    check(c == 404, "org B's VALID write on org A's plan account is a 404", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "GET", f"{_P}/plans/{plan_b}")
    check(c == 404, "org A cannot read org B's plan (404)", f"{c} {dumps(o)}")
    c, o = await api(U_ADMIN1, "GET", f"{_P}/accounts/{acct(ACC_B)}/test?valuation_date={today().isoformat()}"
                                      f"&profile_id={S['p2']['id']}")
    check(c == 404, "org A cannot test org B's account (404)", f"{c} {dumps(o)}")
    for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        for t in ("public.section351_plans", "public.section351_sponsor_profiles", "public.section351_tracker_steps"):
            own = int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", org, fetch="fetchval"))
            theirs = int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", other,
                                      fetch="fetchval"))
            check(own > 0 and theirs == 0, f"{t}: org {'A' if org == ORG_A else 'B'} sees its own rows ({own}), none of "
                  "the other org's", f"{own} {theirs}")
    check(NETWORK_CALLS == [], "no network call was attempted", dumps(NETWORK_CALLS[:3]))
    check(MODEL_CALLS == [], "no model call was attempted", dumps(MODEL_CALLS[:3]))
    check(S3_BUILT == [], "no real R2 client was built", dumps(S3_BUILT))


async def y_real_rows(conn):
    section("[Y] Real and demo-olivo rows untouched (count + checksum)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")
    if S.get("demo_org") is None:
        find("demo-olivo", "no organization with slug 'demo-olivo' — its rows were not fingerprinted")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    self_code(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    data = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
    rows = data.get("routes", []) if isinstance(data, dict) else data
    have = {(m, r.get("path")): r.get("after") for r in rows for m in (r.get("methods") or [])}
    miss = [(k, g) for k, g in ROUTES.items() if g not in str(have.get(k))]
    check(not miss, f"each of rep14's {len(ROUTES)} routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    # count rep14's OWN file, run by path (rep13 fix 1: a title-prefix match over the suite over-counts)
    c14, o14, e14 = run_cmd(["node", "--test", "tests/replication/rep14.test.mjs"], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o14, re.M)
    p = re.search(r"^# pass (\d+)$", o14, re.M)
    check(c14 == 0 and n is not None and p is not None and int(n.group(1)) > 0 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o14, re.M) is not None and "not ok" not in o14,
          f"the rep14 render tests (tests/replication/rep14.test.mjs, run by path) all pass "
          f"({n.group(1) if n else '?'})", f"{c14} {(o14 + e14)[-300:] if c14 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep13.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep13.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_rep13.py: 0 FAIL (its own final TOTAL line and exit code)",
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
            # a reserved prefix matches a real row: seed nothing and NEVER run the by-prefix teardown over it
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        await run("rules (pure)", y_rules)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("model + joins", y_model_and_joins, conn)
            await run("profiles", y_profiles, conn)
            if S.get("p2", {}).get("id"):
                await run("test (db)", y_test_db, conn)
                if S.get("joined"):
                    await run("candidates", y_candidates, conn)
                    await run("fix-up", y_fixup, conn)
                    await run("pool", y_pool, conn)
                    if S.get("ready_ok"):
                        await run("basis record", y_basis, conn)
                        await run("disclosure re-acknowledgement", y_disclosure_reack, conn)
                        await run("transfer", y_transfer, conn)
                    else:
                        check(False, "the plan reached 'ready'", "the basis / approval / transfer sections skipped")
                    await run("envelope", y_envelope, conn)
                else:
                    check(False, "the replica fixture accounts joined the model", "fixture-dependent sections skipped")
                await run("isolation", y_isolation, conn)
            else:
                check(False, "the sponsor profile was created", "the profile-dependent sections skipped")
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
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (platform securities, attributes, snapshot and classifications "
                  "included)", f"{left}")
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
