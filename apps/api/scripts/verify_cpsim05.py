"""verify_cpsim05 — CPSIM05: a SIMULATED FIX counterparty. When a Hollisworks proposal is approved (its own
maker-checker — no second approval), the trades of accounts at the simulated custodian are sent AUTOMATICALLY as FIX 4.4
messages carried over HTTP, the counterparty answers with ExecutionReports (filled at the platform's last close on its
clock), Hollisworks books the fills through the EXISTING enter_fill, syncs the account, reconciles each fill and shows
realized / unrealized P/L (SIMULATED). Simulated counterparty ONLY: real custodians stay manual.

Proves (docs/cpsim/CPSIM05_NOTES.md D1–D8): the schema (catalog); the FIX endpoint (parse / checksum / BodyLength /
CompIDs / Reject / every message pair / outcomes / idempotency / book moves / the message log); the client + the
SimulatedFixRouter (the real `fix` router still NotConnected); the automatic path end to end per proposal kind (model —
human maker + checker, a system harvest proposal through rep2's second-person approval, a cash proposal's fund line;
the approval-of-record event, the send, fills via enter_fill with the checker as actor, the post-fill sync, the
automatic reconcile, the tax engine's realized ST / LT and the unrealized P/L); the outcomes (reject → to-do, partial /
delay / limit filled after a clock advance, no price, insufficient position, unsupported instrument); idempotency; the
D5 guard (every D2 condition, the DB backstop, the call-site scan, the person actions intact); blocks on fix_sim;
unsupported kinds (options / par lines / shorts) manual with the reason; org isolation both ways; after-response timing;
no N+1 on the message log; conformance (fix group); UI render tests; no network; real + demo rows unchanged; teardown.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim05.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets hydrate from
Doppler over HTTPS first — the only network the script itself uses; after that every httpx network transport refuses
and RECORDS the URL (a call that would leave the process is a [FAIL] naming it). The simulator, the app API, the FIX
client, the automatic step's sync and the webhook receiver all run IN-PROCESS against the real ``main.app`` (which
mounts the simulator): altruist_oauth.set_simulator_transport + fix_sim_auto.set_transport + cpsim.webhooks
.set_transport — the transport is threaded on EVERY call. The automatic step is detached: every assertion on it comes
after ``fix_sim_auto.drain()``. No model call (the helpers refuse and record).

TIME IS INJECTED: the counterparty clock (cpsim.registry.set_today) starts at D0 = four US business days before
yesterday and every admin "advance" moves it one business day (D1, D2, D3) — all in the past, so the sync window and
the platform-price reads (``<= today``) see them; the simulator's wall clock (cpsim.oauth.set_now — executed_at) is the
real time minus ten minutes, so enter_fill's "not in the future" rule holds. Prices: the fixture equity closes 52.00 on
D0, 50.00 on D1 and D2, 39.00 on D3 (a limit buy at 40 works from D2 and fills at D3).

FIXTURES (ids 99000000-0000-0000-0000-0000c955…; sign-in tag cpsim05verify): TEST orgs A and B inside the reserved
prefix (is_test asserted); users with real user_roles grants, each decided by the app (asserted): U_SA Super Admin, U_MK
maker (manage execution / proposals / blocks, custody connections), U_CK checker (approve execution / proposals /
blocks), U_MA a second approver (rep2's second person for a SYSTEM proposal), U_SD a person sender, U_RD reader, U_MKB /
U_CKB org B. Counterparties carry the reserved slug prefix ``cpsim5verify-``. Fixture securities 'CPSIM05 VERIFY …'
(identifiers CPSIM05… / CP5V…, prices provider_key cpsim05verify) — asserted to match no real row; named in the
cleanup plan by fixture id. The capability rows use the FIXTURE custodian code ``zz_cpsim5v_sim`` (services
.custodian_execution.SIMULATOR_CAPABILITY_CUSTODIAN substituted for the run, restored at the end). The adapter's
simulator protocol is left at its default 'fix' (asserted).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by this run's fixture ids with every
teardown GUC a guard reads (``execution.teardown`` — instructions / lots / fills / events / proposals / blocks /
capabilities; ``sod.teardown`` always; ``cpsim.allow_teardown`` — the append-only cpsim tables incl. fix_messages /
fix_allocations). The D5 event trigger is BEFORE INSERT only, so it has no teardown path to honour. Every touched table
re-read for its exact pre-test count BY FIXTURE IDS ONLY (audit_log, sod_decisions — item_id TEXT — by fixture ids).
Real rows fingerprinted before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<endpoint|auto|outcomes|idempotency|system|guard|blocks|unsupported|isolation|timing|unchanged|conformance|
safety>: the seed, the named sections (and what they need), the full teardown. VERIFY_REGRESSIONS (fast by default):
nested regressions run --self-checks-only.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import importlib
import inspect
import types
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
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

SPRINT_KEY = "cpsim05"
BEFORE_REF = "e62f871"   # cpsim05-work's base: 'merge altread1'
_P = "/api/v1/admin/counterparties"
_X = "/api/v1/execution"
_PR = "/api/v1/proposals"
ROUTES = {("POST", _X + "/blocks/{block_id}/fix-sim"): "manage_blocks",
          ("GET", _P + "/{counterparty_id}/fix/messages"): "super_admin"}
SIM_ROUTES = {("POST", "/cpsim/c/{slug}/fix")}
NODE_TEST = "tests/cpsim05/cpsim05.test.mjs"
NOTES = REPO / "docs" / "cpsim" / "CPSIM05_NOTES.md"
MIGRATION = REPO / "docs" / "cpsim05_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
BOUNDARY = HERE / "cpsim_boundary_check.py"
CONFORMANCE = HERE / "cpsim_altruist_conformance.py"
REGRESSION_SET = REPO / "scripts" / "regression_set.txt"
REGRESSIONS = ("verify_cpsim03.py", "verify_cpsim04.py", "verify_altread1.py", "verify_rep5.py", "verify_rep9.py",
               "verify_rep6.py", "verify_authz1.py")
HTTP_OK, HTTP_CREATED, HTTP_FORBIDDEN, HTTP_NOT_FOUND, HTTP_CONFLICT, HTTP_UNPROCESSABLE = 200, 201, 403, 404, 409, 422
CLIENT_TIMEOUT_S = 15.0        # the FIX / orders client's read timeout — the approval must answer well under it
N_PLUS_ONE_ROWS = 60           # FIX messages added before the message-log timing
N_PLUS_ONE_MAX_S = 5.0         # the log list (one query) must answer under this, whatever the row count
SECTION_TIMEOUT = 600          # every section must finish in < 10 minutes


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def node_test_count() -> int:
    """Derived from the test file itself (never a title prefix)."""
    return len(re.findall(r"^test\(", _src(f"apps/web/{NODE_TEST}"), re.M))


def self_routes(check, find):
    """cpsim05's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cpsim05 changed no existing route's gate and removed none", f"{o.where()}; changed={sorted(changed)[:3]} "
          f"removed={o.removed[:3]}")
    mine = flat(o.new)
    want = set(ROUTES)
    check(mine == want, f"this sprint's own routes are exactly its {len(ROUTES)} routes (block fix-sim re-run, admin "
          "FIX message log)", f"{o.where()}; extra={sorted(mine - want)[:3]} missing={sorted(want - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items()
           if k not in by or g not in str(by[k].get("after"))]
    check(not bad, "each own route names exactly its gate (manage_blocks / super_admin)", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cpsim05's routes all still exist today with the same gate", f"{drift[:4]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    sims = {(m, r.get("path")) for r in data.get("simulator_routes", []) for m in (r.get("methods") or [])}
    check(SIM_ROUTES <= sims, "the simulator FIX route is listed in the simulator section of docs/AUTHZ1_ROUTES (own "
          "auth, not an app route)", f"missing={sorted(SIM_ROUTES - sims)}")


def self_static(check, find):
    n = _src("docs/cpsim/CPSIM05_NOTES.md")
    for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"):
        check(f"| {d} |" in n, f"CPSIM05_NOTES records {d} as built")
    for w in ("message catalogue", "| 35 | MsgType", "| 17 | ExecID", "| 591 | PreallocMethod", "ASSUMED",
              "Reuse inventory", "approve_upstream", "AfterResponseGate"):
        check(w.lower() in n.lower(), f"CPSIM05_NOTES names {w!r}")
    rs = _src("scripts/regression_set.txt")
    check(bool(re.search(r"^verify_cpsim05\.py\s*$", rs, re.M)), "verify_cpsim05.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    check("## CPSIM05" in st and "STATUS: BUILT" in st[st.find("## CPSIM05"):st.find("## CPSIM05") + 600],
          "PROJECT_STATUS records CPSIM05 as BUILT")
    adm = _src("docs/cpsim/CPSIM_ADMIN.md")
    check(all(w in adm for w in ("FIX tab", "automatic", "demo only")),
          "CPSIM_ADMIN.md: the FIX tab, how the automatic path works and why it is demo-only")
    rp = _src("docs/REPLICATION_STUDIO.md")
    check("simulated FIX" in rp, "REPLICATION_STUDIO.md: the simulated FIX route beside manual")
    tr = _code_only(_src("apps/api/services/custody/trading.py"))
    check('simulator_protocol: str = "fix"' in tr and "sent_externally=True" not in tr,
          "the adapter's default simulator protocol is 'fix'; nothing is sent at submit (sent_externally never True)")
    rt = _code_only(_src("apps/api/services/execution_routers.py"))
    check('"fix": NotConnectedRouter(' in rt, "the real 'fix' router is still a NotConnectedRouter (registry source)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))


from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _cpsim_fixtures as FX  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_roles as VR  # noqa: E402

import main  # noqa: E402
from cpsim import db as SDB  # noqa: E402
from cpsim import mirror as SM  # noqa: E402
from cpsim import oauth as SO  # noqa: E402
from cpsim import registry as R  # noqa: E402
from cpsim import webhooks as SW  # noqa: E402
from services import account_models as AM  # noqa: E402
from services import altruist_oauth as AO  # noqa: E402
from services import altruist_webhook as AW  # noqa: E402
from services import blocks as BK  # noqa: E402
from services import custodian_capabilities as CC  # noqa: E402
from services import custodian_execution as CX  # noqa: E402
from services import execution as X  # noqa: E402
from services import execution_routers as XR  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import fix_sim_auto as FA  # noqa: E402
from services import fix_sim_client as FC  # noqa: E402
from services import fix_sim_pl as FPL  # noqa: E402
from services import harvest as HV  # noqa: E402
from services import portfolio_assets as PA  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
from services.custody.trading import AltruistTradeAdapter, ShortSaleNotDeclared, TradeList, route_trade_list  # noqa
from services.us_business_days import next_business_day, prior_business_day  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cpsim05verify"
NAME = "CPSIM05 VERIFY "
SLUG_PREFIX = "cpsim5verify-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c955"
FIX_CAP = "zz_cpsim5v_sim"
# the clock: four consecutive US business days, all before today (prior_business_day(d) returns d itself when d is a
# business day — ALTREAD1's finding — so each step passes the day before)
D3 = prior_business_day(date.today() - timedelta(days=1))
D2 = prior_business_day(D3 - timedelta(days=1))
D1 = prior_business_day(D2 - timedelta(days=1))
D0 = prior_business_day(D1 - timedelta(days=1))
THU = prior_business_day(D0 - timedelta(days=1))     # the book's first snapshot
assert next_business_day(D0 + timedelta(days=1)) == D1 and next_business_day(D2 + timedelta(days=1)) == D3
SIM_BASE = "https://cpsim.verify5.invalid"
REDIRECT = "https://verify.cpsim05.invalid/altruist/callback"
PROVIDER_KEY = "cpsim05verify"
SIM_XWALK, REAL_XWALK = "ALTRUIST_SIMULATOR", "ALTRUIST"
PRICE_EQ = {D0: "52.00", D1: "50.00", D2: "50.00", D3: "39.00"}
PRICE_FUND, PRICE_BOND = "20.00", "99.00"
HELD_EQ, HELD_FUND = Decimal("100"), Decimal("10")
LOT_LT = (Decimal("6"), Decimal("240.00"), date(2024, 1, 2))                       # unit cost 40, long-term
LOT_ST = (Decimal("4"), Decimal("220.00"), date.today() - timedelta(days=30))      # unit cost 55, short-term
EXECUTE_BY_DAYS = 14


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
ORG_R = _id(3)        # never created (kept in the cleanup scope so the shared plan stays cpsim03-shaped)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SA, U_MK, U_CK, U_SD, U_RD, U_MKB, U_CKB, U_MA = (_id(10), _id(11), _id(12), _id(13), _id(14), _id(15), _id(16),
                                                    _id(17))
ALL_USERS = [U_SA, U_MK, U_CK, U_SD, U_RD, U_MKB, U_CKB, U_MA]
E_A, E_B = _id(40), _id(41)
H_A, H_B = _id(30), _id(31)
ACC_SIM, ACC_REAL, ACC_NOX, ACC_HELD, ACC_OTHER, ACC_B = _id(50), _id(51), _id(52), _id(53), _id(54), _id(55)
ACC_SIM2, ACC_SIM3 = _id(56), _id(57)
SEC_EQ, SEC_FUND, SEC_NOPX, SEC_BOND = _id(60), _id(61), _id(62), _id(63)
MODEL_A, VERSION_A, LINK_A = _id(80), _id(81), _id(82)
POS_EQ, LOT_LT_ID, LOT_ST_ID, POS_HELD = _id(90), _id(91), _id(92), _id(93)
HOUSEHOLDS = [H_A, H_B]
ACCOUNTS = [ACC_SIM, ACC_REAL, ACC_NOX, ACC_HELD, ACC_OTHER, ACC_B, ACC_SIM2, ACC_SIM3]
SECURITIES = [SEC_EQ, SEC_FUND, SEC_NOPX, SEC_BOND]
EXEC_PERMS = ("view_execution", "manage_execution", "approve_execution")
PROP_PERMS = ("view_proposals", "manage_proposals", "approve_proposals")
BLOCK_PERMS = ("view_blocks", "manage_blocks", "approve_blocks")
CUSTODY_PERMS = ("manage_custody_connections", "view_custody_connections")
ROLE_PERMS = {"cpsim05_maker": ("view_execution", "manage_execution", "view_proposals", "manage_proposals",
                                "view_blocks", "manage_blocks") + CUSTODY_PERMS,
              "cpsim05_checker": ("view_execution", "approve_execution", "view_proposals", "approve_proposals",
                                  "view_blocks", "approve_blocks"),
              "cpsim05_reader": ("view_execution", "view_proposals"),
              "cpsim05_second": ("view_execution", "manage_execution", "approve_execution")}
USERS = {U_SA: (ORG_A, "super_admin", "sa", "super_admin"), U_MK: (ORG_A, "member", "mk", "cpsim05_maker"),
         U_CK: (ORG_A, "member", "ck", "cpsim05_checker"), U_SD: (ORG_A, "member", "sd", "cpsim05_maker"),
         U_RD: (ORG_A, "member", "rd", "cpsim05_reader"), U_MKB: (ORG_B, "member", "mkb", "cpsim05_maker"),
         U_CKB: (ORG_B, "member", "ckb", "cpsim05_checker"), U_MA: (ORG_A, "member", "ma", "cpsim05_second")}
ROLE_EXPECT = {U_SA: {"org": ORG_A, "roles": ["super_admin"]},
               U_MK: {"org": ORG_A, "roles": ["cpsim05_maker"], "has": ["manage_proposals", "manage_execution",
                                                                        "manage_blocks"],
                      "lacks": ["approve_proposals", "approve_execution", "approve_blocks"]},
               U_CK: {"org": ORG_A, "roles": ["cpsim05_checker"], "has": ["approve_proposals", "approve_execution",
                                                                          "approve_blocks"],
                      "lacks": ["manage_execution", "manage_proposals"]},
               U_SD: {"org": ORG_A, "roles": ["cpsim05_maker"], "has": ["manage_execution"],
                      "lacks": ["approve_execution"]},
               U_RD: {"org": ORG_A, "roles": ["cpsim05_reader"], "has": ["view_execution"],
                      "lacks": ["manage_execution", "approve_execution", "approve_proposals"]},
               U_MKB: {"org": ORG_B, "roles": ["cpsim05_maker"], "has": ["manage_execution"]},
               U_CKB: {"org": ORG_B, "roles": ["cpsim05_checker"], "has": ["approve_execution"]},
               U_MA: {"org": ORG_A, "roles": ["cpsim05_second"], "has": ["approve_execution", "manage_execution"],
                      "lacks": ["approve_proposals"]}}
A1, A2, A3, A4, A_BOTH = "cp5v-acct-1", "cp5v-acct-2", "cp5v-acct-3", "cp5v-acct-4", "cp5v-acct-both"
HH1, HH4 = "cp5v-hh-1", "cp5v-hh-4"
CUSIP_EQ, TICKER_EQ = "CPSIM05E1", "CP5VEQ"
CUSIP_FUND, TICKER_FUND = "CPSIM05F1", "CP5VFD"
TICKER_NOPX, TICKER_BOND = "CP5VNP", "CP5VBD"
REAL_EXT = "cp5v-real-1"
REJECT_REASON = "verify: the simulated desk is closed"
PARTIAL_PCT = 40
SEED_CHECK_LITERALS = (
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund", "fixed_income")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.securities_global_prices", "sec_global_price_type_chk", ("close",)),
    ("portfolio.external_references", "ext_ref_record_type_chk", ("account",)),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("equity_trading_api",)),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("assumed",)),
    ("public.execution_instructions", "execution_instructions_side_chk", ("buy", "sell")),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("custodian_desk",)),
    ("public.execution_instruction_events", "execution_instruction_events_action_chk",
     ("approve_upstream", "automatic_failed", "custodian_reject", "mark_sent", "fill", "reconcile")),
    ("public.rebalance_proposals", "rebalance_proposals_trigger_chk", ("manual", "harvest")),
    ("public.rebalance_proposals", "rebalance_proposals_status_chk", ("draft",)),
    ("public.rebalance_proposals", "rebalance_proposals_kind_chk", ("model", "cash", "cash_policy_id")),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_price_chk", ("security_price",)),
    ("public.rebalance_proposal_trades", "rebalance_proposal_trades_cash_leg_chk", ("reserve", "market", "limit")),
    ("public.trade_blocks", "trade_blocks_route_chk", ("manual", "fix_sim")),
    ("cpsim.orders", "orders_protocol_chk", ("rest", "fix")),
    ("cpsim.fix_messages", "fix_messages_direction_chk", ("in", "out")),
    ("cpsim.book_transactions", "book_transactions_type_chk", ("buy", "sell")),
)
UNIQUES = ("orders_client_order_live_uq", "order_executions_exec_uq", "execution_fills_custodian_exec_uq",
           "fix_messages_exchange_uq", "fix_allocations_leg_uq", "rebalance_proposals_open_uq",
           "external_references_org_source_ext_type_key", "altruist_connections_active_unique",
           "uq_sec_global_ident_issuer_assigned", "uq_sec_global_price_point", "org_settings_org_id_setting_key_key")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id", "is_held_away", "is_discretionary"),
    "portfolio.external_references": ("org_id", "source_system", "external_id", "record_type", "record_id"),
    "portfolio.securities_global": ("id", "name", "security_type", "currency_code"),
    "portfolio.securities_global_identifiers": ("global_security_id", "id_type", "id_value", "is_primary"),
    "portfolio.securities_global_prices": ("global_security_id", "price_date", "price", "currency_code", "price_type",
                                           "source", "provider_key"),
    "portfolio.positions": ("id", "org_id", "owner_entity_id", "asset_id", "as_of_date", "quantity", "authority",
                            "source_system", "account_id"),
    "portfolio.tax_lots": ("id", "org_id", "position_id", "acquired_date", "quantity", "adjusted_cost",
                           "original_cost", "term", "status"),
    "portfolio.models": ("id", "org_id", "name", "source", "status", "created_by"),
    "portfolio.model_versions": ("id", "org_id", "model_id", "version_no", "status", "origin", "maker_ids",
                                 "created_by"),
    "account_model_links": ("id", "org_id", "account_id", "model_id", "link_group_id", "change_kind", "status",
                            "maker_id", "checker_id", "decided_at", "decision_note"),
    "rebalance_proposals": ("id", "org_id", "account_id", "link_id", "model_id", "model_version_id", "trigger",
                            "trigger_ref", "status", "expires_at", "maker_kind", "maker_id", "proposal_kind",
                            "valuation_as_of"),
    "rebalance_proposal_trades": ("org_id", "proposal_id", "line_no", "global_security_id", "side", "quantity",
                                  "notional", "price", "price_source", "price_as_of", "lots", "reason", "cash_leg"),
    "execution_instruction_events": ("org_id", "instruction_id", "action", "from_status", "to_status", "actor_id",
                                     "detail"),
}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.entities", "public.households",
                  "public.household_memberships", "public.accounts", "public.org_settings", "public.audit_log",
                  "public.altruist_connections", "public.altruist_oauth_states", "public.altruist_webhook_events",
                  "public.member_todos", "public.sod_decisions", "public.execution_instructions",
                  "public.execution_instruction_lots", "public.execution_fills", "public.execution_instruction_events",
                  "public.custodian_capabilities", "public.rebalance_proposals", "public.rebalance_proposal_trades",
                  "public.account_model_links", "public.loss_bank_entries", "public.trade_blocks",
                  "public.block_allocations", "public.block_fills", "public.block_events",
                  "portfolio.external_references", "portfolio.positions", "portfolio.transactions",
                  "portfolio.tax_lots", "portfolio.assets", "portfolio.models", "portfolio.model_versions",
                  "portfolio.securities_global", "portfolio.securities_global_identifiers",
                  "portfolio.securities_global_prices")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.counterparty_behaviour",
                "cpsim.counterparty_clock", "cpsim.counterparty_fix_sessions", "cpsim.request_log",
                "cpsim.book_uploads", "cpsim.book_households", "cpsim.book_accounts", "cpsim.book_positions",
                "cpsim.book_transactions", "cpsim.oauth_codes", "cpsim.oauth_tokens", "cpsim.orders",
                "cpsim.order_executions", "cpsim.cash_movements", "cpsim.fix_messages", "cpsim.fix_allocations")
NEW_CPSIM_PRIVS = {t: set(FX.cpsim_app_privileges(t)) for t in ("fix_messages", "fix_allocations")}
assert NEW_CPSIM_PRIVS == {"fix_messages": {"SELECT", "INSERT"}, "fix_allocations": {"SELECT", "INSERT"}}

S: dict = {"cp": {}, "keys": {}, "client_ids": {}, "issued": [], "ins": {}, "orders": {}, "bodies": [],
           "proposals": {}, "blocks": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
VERIFY_CALLS: list[str] = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {_redact(detail)}" if detail and not passed else ""),
          flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {_redact(detail)}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def total_line() -> str:
    return f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL"


def _redact(text) -> str:
    """Never print a secret: any issued value (or its random body) and any cpsim_ token is masked."""
    t = str(text)
    for v in S["issued"]:
        if v:
            t = t.replace(v, "[secret]")
            body = v.split("_")[-1]
            if len(body) >= 16:
                t = t.replace(body, "[secret]")
    return re.sub(r"cpsim_(?:live|at|rt|code|whsec)_[0-9a-f]{8,}", "[secret]", t)


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or "") if isinstance(exc.stdout, str) else "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


def sub_of(uid) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def secret_leaks(text: str) -> list[str]:
    return [v[:10] + "…" for v in S["issued"] if v and (v in text or v.split("_")[-1] in text)]


def dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """One transaction in ONE org's RLS context (no Super Admin carve-out) — the isolation reads."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', $1, true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def sim(conn, sql, *args, fetch="fetch"):
    """One transaction AS cpsim_app (SET LOCAL ROLE — the simulator's own path)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', '', true)")
        await conn.execute("SET LOCAL ROLE cpsim_app")
        return await getattr(conn, fetch)(sql, *args)


class Rollback(Exception):
    pass


async def rolled_back(conn, fn, *, super_admin: bool = True, org=None):
    """Run ``fn(conn)`` in a transaction that is ALWAYS rolled back; returns (result, error)."""
    out = {"res": None, "err": None}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', $1, true), "
                               "set_config('app.current_org_id', $2, true)", "true" if super_admin else "false",
                               str(org) if org else "")
            try:
                async with conn.transaction():
                    out["res"] = await fn(conn)
            except Exception as exc:  # noqa: BLE001
                out["err"] = exc
            raise Rollback()
    except Rollback:
        pass
    return out["res"], out["err"]


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


async def close_app_pools() -> None:
    p, DBM._pool = DBM._pool, None
    await close_conn(p)
    try:
        await SDB.close_pool()
    except Exception:  # noqa: BLE001
        pass


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


FIX_CP = f"(SELECT id FROM cpsim.counterparties WHERE slug LIKE '{SLUG_PREFIX}%')"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS + [ORG_R]), _arr(ALL_USERS)
    by_org = f"org_id = ANY({orgs})"
    secs = _arr(SECURITIES)
    cps = _arr(S["cp"].values()) if S["cp"] else "ARRAY[]::uuid[]"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.household_memberships": f"household_id = ANY({_arr(HOUSEHOLDS)})",
        "public.audit_log": f"{by_org} OR user_id = ANY({users}) OR resource_id = ANY({orgs}) OR "
                            f"resource_id = ANY({cps})",
        "public.altruist_webhook_events": f"{by_org} OR event_id LIKE '{TAG}%'",
        "public.custodian_capabilities": f"custodian_code = '{FIX_CAP}'",
        "portfolio.securities_global": f"id = ANY({secs})",
        "portfolio.securities_global_identifiers": f"global_security_id = ANY({secs})",
        "portfolio.securities_global_prices": f"global_security_id = ANY({secs})",
        "cpsim.counterparties": f"slug LIKE '{SLUG_PREFIX}%'",
        "cpsim.request_log": f"slug LIKE '{SLUG_PREFIX}%' OR counterparty_id IN {FIX_CP}",
    }.get(table, f"counterparty_id IN {FIX_CP}" if table.startswith("cpsim.") else by_org)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED_PUBLIC:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    for t in CPSIM_TABLES:
        out[t] = int(await sim(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    return out


async def fingerprint(conn, sql, *args, as_sim=False) -> tuple[int, str]:
    q = f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS s FROM ({sql}) t"
    row = await (sim(conn, q, *args, fetch="fetchrow") if as_sim else platform(conn, q, *args, fetch="fetchrow"))
    return int(row["n"]), row["s"]


async def _real_fingerprints_base(conn) -> dict:
    orgs = [str(o) for o in FIXTURE_ORGS + [ORG_R]]
    out = {
        "counterparty_sim.* settings of every real org": await fingerprint(
            conn, "SELECT org_id, setting_key, setting_value, updated_at FROM org_settings WHERE setting_key LIKE "
                  "'counterparty\\_sim.%' AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "demo-tagged entities": await fingerprint(
            conn, "SELECT id, display_name, tags, status FROM entities WHERE EXISTS (SELECT 1 FROM unnest(tags) x "
                  "WHERE x LIKE 'demo-%') AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real execution instructions": await fingerprint(
            conn, "SELECT id, status, system_to, custodian_order_id FROM execution_instructions WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real execution fills": await fingerprint(
            conn, "SELECT id, custodian_exec_id FROM execution_fills WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "real custodian capability rows": await fingerprint(
            conn, "SELECT id, custodian_code, capability_key, value, valid_to FROM custodian_capabilities WHERE "
                  "custodian_code <> $1", FIX_CAP),
        "real Altruist connections": await fingerprint(
            conn, "SELECT id, org_id, environment, status, system_to FROM altruist_connections WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real webhook events": await fingerprint(
            conn, "SELECT id, event_id, org_id, processed_at FROM altruist_webhook_events WHERE NOT "
                  "(COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid) = ANY($1::uuid[])) AND event_id "
                  "NOT LIKE 'cpsim-evt-%'", orgs),
        "real to-dos": await fingerprint(
            conn, "SELECT id, status FROM member_todos WHERE (source LIKE 'execution_custodian%' OR source LIKE "
                  "'%fix_sim%') AND NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
    }
    for label, sql in (("counterparties (non-fixture)", f"SELECT id, slug, status, system_to FROM cpsim.counterparties "
                                                        f"WHERE slug NOT LIKE '{SLUG_PREFIX}%'"),
                       ("orders (non-fixture)", f"SELECT id, status, system_to FROM cpsim.orders WHERE counterparty_id "
                                                f"NOT IN {FIX_CP}"),
                       ("books (non-fixture)", "SELECT id, counterparty_id, valid_to FROM cpsim.book_positions WHERE "
                                               f"counterparty_id NOT IN {FIX_CP}")):
        out[label] = await fingerprint(conn, sql, as_sim=True)
    return out


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        users, orgs = list(ALL_USERS), list(FIXTURE_ORGS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [list(HOUSEHOLDS)]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.altruist_webhook_events": ("event_id = ANY($1::text[]) OR event_id LIKE $2::text",
                                               [list(S.get("events", [])), f"{TAG}%"]),
            # the platform capability rows of the FIXTURE custodian code (guard: execution.teardown)
            "public.custodian_capabilities": ("custodian_code = $1::text", [FIX_CAP]),
            # the security master has no org_id: the fixture securities, their identifiers and platform prices by id
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [list(SECURITIES)]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            # the simulator's book has no org_id: by the reserved fixture slug prefix (orders + executions reached by
            # the FK closure, their guard honouring cpsim.allow_teardown for the owner)
            "cpsim.counterparties": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
            "cpsim.request_log": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("cpsim.allow_teardown", "execution.teardown"), label="verify_cpsim05")
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
    c = await counts(conn)
    n = sum(c.values())
    op = await operator_conn()
    try:
        n += await VC.leftover_count(op, orgs=FIXTURE_ORGS + [ORG_R], users=ALL_USERS, user_sub_like=f"%{TAG}%")
    finally:
        await close_conn(op)
    return n


class org_ctx:
    """A direct service call (route_trade_list, the orders client, enter_fill) in ONE org's RLS context — the same
    contextvars the API's middleware sets, so every pooled transaction SET LOCALs it."""

    def __init__(self, org, super_admin: bool = False):
        self.org, self.su = str(org), super_admin

    async def __aenter__(self):
        self.tokens = DBM.set_rls_context(self.org, self.su)
        return await DBM.get_pool()

    async def __aexit__(self, *a):
        DBM.reset_rls_context(self.tokens)
        return False


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
APP_T = None


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def raw(method, path, *, headers=None, body=None, timeout=300.0):
    """(status, parsed body) through the REAL app (which mounts the simulator)."""
    kw = {"headers": {"User-Agent": "verify_cpsim05", **(headers or {})}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
        except Exception as exc:  # noqa: BLE001
            print(f"[INFO] {method} {path} raised in the app (a 500):")
            traceback.print_exc()
            return 500, {"raw": f"{type(exc).__name__}"}
    try:
        data_out = res.json() if res.content else {}
    except ValueError:
        data_out = {"raw": res.text[:4000]}
    if not (method == "POST" and path == _P):     # the create call shows the new key ONCE, by design
        S["bodies"].append(res.text[:20000])
    return res.status_code, data_out


async def api(uid, method, path, body=None):
    return await raw(method, path, headers={"Authorization": f"Bearer {token_for(uid)}"}, body=body)


def cpu(key: str, tail: str = "") -> str:
    return f"{_P}/{S['cp'][key]}{tail}"


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")

    def stub(tok):
        VERIFY_CALLS.append(tok)
        return dict(CLAIMS_BY_TOKEN[tok])

    main.verify_token = stub
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(f"{request.method} {request.url}")
        raise httpx.ConnectError(f"verify_cpsim05: network disabled ({request.url.host})", request=request)

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cpsim05: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for fn, nm in ((R.set_today, "registry.set_today"), (SM.set_now, "mirror.set_now"), (SO.set_now, "oauth.set_now"),
                   (AO.set_simulator_transport, "altruist_oauth.set_simulator_transport"),
                   (SW.set_transport, "webhooks.set_transport")):
        if len(list(inspect.signature(fn).parameters)) != 1:
            raise RuntimeError(f"{nm} signature drifted")
    S["today"] = D0
    S["now"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    R.set_today(lambda: S["today"])
    SM.set_now(lambda: S["now"])
    SO.set_now(lambda: S["now"])
    global APP_T
    APP_T = FX.TimeoutASGITransport(app=main.app)
    AO.set_simulator_transport(APP_T)            # simulator environment ONLY (sandbox / production never read it)
    SW.set_transport(httpx.ASGITransport(app=main.app))
    SW.set_sleep(lambda s: asyncio.sleep(0))
    S["cap_saved"] = CX.SIMULATOR_CAPABILITY_CUSTODIAN
    CX.SIMULATOR_CAPABILITY_CUSTODIAN = FIX_CAP  # the fixture code (the real altruist_simulator has no row)
    # cpsim05: the adapter's simulator protocol stays at its default 'fix' (asserted); the automatic step's FIX and
    # sync calls use the SAME in-process transport (threaded on every call)
    if AltruistTradeAdapter.simulator_protocol != "fix":
        raise RuntimeError(f"the adapter's simulator protocol is {AltruistTradeAdapter.simulator_protocol!r}, not 'fix'")
    FA.set_transport(APP_T)
    FA.timings.clear()
    os.environ[AO.CPSIM_BASE_URL_ENV] = SIM_BASE
    os.environ["ALTRUIST_REDIRECT_URI"] = REDIRECT
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", "cpsim05verify-" + secrets.token_hex(16))
    S["whsec"] = "cpsim_whsec_" + secrets.token_hex(20)
    S["real_whsec"] = "verify-real-" + secrets.token_hex(20)
    S["issued"] += [S["whsec"], S["real_whsec"]]
    os.environ[AW.SIMULATOR_SECRET_ENV_VAR] = S["whsec"]
    os.environ[AW.SECRET_ENV_VAR] = S["real_whsec"]


def restore_stubs():
    R.set_today(None)
    SM.set_now(None)
    SO.set_now(None)
    AO.set_simulator_transport(None)
    SW.set_transport(None)
    SW.set_sleep(None)
    if "cap_saved" in S:
        CX.SIMULATOR_CAPABILITY_CUSTODIAN = S["cap_saved"]
    FA.set_transport(None)


def set_sim_creds(key="main") -> None:
    os.environ["ALTRUIST_SIMULATOR_CLIENT_ID"] = S["client_ids"][key]
    os.environ["ALTRUIST_SIMULATOR_CLIENT_SECRET"] = S["keys"][key]


# ═══ Instructions through the REAL routes ═══════════════════════════════════════════════════════════════════════
def trade_list(account, security=SEC_EQ, *, side="buy", quantity="10", notional=None, kind="market", limit=None,
               executor="custodian_desk") -> dict:
    t = {"global_security_id": str(security), "side": side, "price_guidance": {"kind": kind}}
    if kind == "limit":
        t["price_guidance"]["limit_price"] = limit
    if kind == "range":
        t["price_guidance"].update({"range_low": "40", "range_high": "60", "reference_price": "52",
                                    "reference_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    if notional is not None:
        t["notional"] = notional
    else:
        t["quantity"] = quantity
    return {"account_id": str(account), "trades": [t], "executor_type": executor,
            "execute_by": (date.today() + timedelta(days=EXECUTE_BY_DAYS)).isoformat(), "if_not_filled": "ask"}


async def route(uid, body) -> tuple[int, dict]:
    return await api(uid, "POST", f"{_X}/instructions", body)


async def instruction(conn, iid) -> dict:
    return dict(await platform(conn, "SELECT * FROM execution_instructions WHERE id = $1::uuid AND system_to IS NULL",
                               iid, fetch="fetchrow") or {})


async def approved(conn, body, label: str) -> str | None:
    """Route (maker) → submit (maker) → approve (checker) through the routes. Returns the instruction id (approved)."""
    code, out = await route(U_MK, body)
    ids = (out.get("routing") or {}).get("instruction_ids") or []
    if code != HTTP_CREATED or len(ids) != 1:
        check(False, f"{label}: routed to one draft instruction", f"{code} {dumps(out)[:400]}")
        return None
    iid = ids[0]
    c1, o1 = await api(U_MK, "POST", f"{_X}/instructions/{iid}/submit", {})
    c2, o2 = await api(U_CK, "POST", f"{_X}/instructions/{iid}/approve", {})
    row = await instruction(conn, iid)
    if not (c1 == HTTP_OK and c2 == HTTP_OK and row.get("status") == "approved"):
        check(False, f"{label}: submitted by the maker, approved by the checker", f"{c1} {c2} {row.get('status')} "
              f"{dumps(o1)[:200]} {dumps(o2)[:200]}")
        return None
    S["ins"][label] = iid
    return iid


async def send(uid, iid) -> tuple[int, dict]:
    return await api(uid, "POST", f"{_X}/instructions/{iid}/send-to-custodian", {})


async def import_fills(uid, iid) -> tuple[int, dict]:
    return await api(uid, "POST", f"{_X}/instructions/{iid}/import-fills", {})


async def behaviour(**fields) -> int:
    code, out = await api(U_SA, "PUT", cpu("main", "/behaviour"), fields)
    if code != HTTP_OK:   # fix 1: a refused PUT once left webhooks OFF silently (delay needs delay_seconds)
        check(False, f"the Super Admin's behaviour PUT {sorted(fields)} → 200", f"{code} {dumps(out)[:300]}")
    return code


async def sim_order(conn, client_order_id: str) -> list:
    return list(await sim(conn, "SELECT * FROM cpsim.orders WHERE counterparty_id = $1::uuid AND client_order_id = $2 "
                                "AND system_to IS NULL", S["cp"]["main"], str(client_order_id)))


async def sim_execs(conn, client_order_id: str) -> list:
    return list(await sim(conn, "SELECT e.* FROM cpsim.order_executions e JOIN cpsim.orders o ON o.id = e.order_row_id "
                                "WHERE o.counterparty_id = $1::uuid AND o.client_order_id = $2 AND o.system_to IS NULL "
                                "ORDER BY e.executed_at", S["cp"]["main"], str(client_order_id)))


async def fills_of(conn, iid) -> list:
    return list(await platform(conn, "SELECT * FROM execution_fills WHERE instruction_id = $1::uuid AND system_to IS "
                                     "NULL ORDER BY filled_at", iid))


async def events_of(conn, iid, action=None) -> list:
    rows = await platform(conn, "SELECT action, from_status, to_status, actor_id, detail FROM "
                                "execution_instruction_events WHERE instruction_id = $1::uuid ORDER BY created_at", iid)
    out = [dict(r) | {"detail": json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]} for r in rows]
    return [e for e in out if action is None or e["action"] == action]


async def set_cap(conn, value: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true), "
                           "set_config('app.current_org_id', $1, true)", str(ORG_A))
        await CC.set_capability(conn, custodian_code=FIX_CAP, key=CX.CAPABILITY_KEY, value=value,
                                evidence_kind="assumed", evidence="cpsim05 verify fixture", as_of=date.today(),
                                actor_id=U_SA, actor_org_id=ORG_A, is_super_admin=True)


async def real_fingerprints(conn) -> dict:
    """cpsim03's real-row set + what cpsim05 could touch: real proposals, blocks, loss-bank entries, FIX messages."""
    out = await _real_fingerprints_base(conn)
    orgs = [str(o) for o in FIXTURE_ORGS + [ORG_R]]
    for label, sql in (("real rebalance proposals", "SELECT id, status, system_to FROM rebalance_proposals WHERE NOT "
                                                    "(org_id = ANY($1::uuid[]))"),
                       ("real trade blocks", "SELECT id, status, route, system_to FROM trade_blocks WHERE NOT "
                                             "(org_id = ANY($1::uuid[]))"),
                       ("real loss-bank entries", "SELECT id, valid_to FROM loss_bank_entries WHERE NOT "
                                                  "(org_id = ANY($1::uuid[]))")):
        out[label] = await fingerprint(conn, sql, orgs)
    out["FIX messages (non-fixture)"] = await fingerprint(
        conn, f"SELECT id FROM cpsim.fix_messages WHERE counterparty_id IS NULL OR counterparty_id NOT IN {FIX_CP}",
        as_sim=True)
    return out


async def _constraints(conn, nsp: str, rel: str) -> dict:
    rows = await platform(conn, "SELECT k.conname, k.contype, pg_get_constraintdef(k.oid) AS d FROM pg_constraint k "
                                f"WHERE k.conrelid = ({REL})", nsp, rel)
    return {r["conname"]: r["d"] for r in rows}


def _snapshot_columns() -> dict[str, set]:
    out: dict[str, set] = {}
    cur = None
    for ln in _src("docs/schema_snapshot.sql").splitlines():
        m = re.match(r"^-- ===== ([\w.]+) =====", ln)
        if m:
            cur = m.group(1)
            out[cur] = set()
            continue
        m = re.match(r"^--   (\w+)\s+\S", ln)
        if cur and m and m.group(1) not in ("PRIMARY", "UNIQUE", "FOREIGN"):
            out[cur].add(m.group(1))
    return out


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1 OR id = $2::uuid",
                           f"{FIXTURE_ID_PREFIX}%", ORG_R, fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    s_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE id::text LIKE $1 OR name "
                                  "LIKE $2", f"{FIXTURE_ID_PREFIX}%", NAME + "%", fetch="fetchval"))
    i_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value = "
                                  "ANY($1::text[])", [CUSIP_EQ, TICKER_EQ, CUSIP_FUND, TICKER_FUND, TICKER_NOPX,
                                                      TICKER_BOND], fetch="fetchval"))
    p_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE provider_key = $1",
                            PROVIDER_KEY, fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1", SLUG_PREFIX + "%",
                      fetch="fetchval"))
    k = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1", FIX_CAP,
                           fetch="fetchval"))
    return check(o == 0 and u == 0 and s_ == 0 and i_ == 0 and p_ == 0 and c == 0 and k == 0,
                 "the reserved prefixes (fixture ids, the out-of-prefix rolled-back org, the sign-in tag, 'CPSIM05 "
                 "VERIFY' securities, their identifiers and price provider, the 'cpsim3verify-' slug, the fixture "
                 "custodian code) match no real row",
                 f"orgs={o} users={u} securities={s_} identifiers={i_} prices={p_} counterparties={c} caps={k}")


async def upload(key, kind, rows) -> tuple[int, dict]:
    files = {"file": (f"{kind}.json", json.dumps({"rows": rows}).encode(), "application/json")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://verify",
                                 timeout=300) as client:
        res = await client.post(cpu(key, "/book/accept"), files=files, data={"kind": kind},
                                headers={"Authorization": f"Bearer {token_for(U_SA)}"})
    try:
        return res.status_code, res.json()
    except ValueError:
        return res.status_code, {}


async def point(org, key) -> bool:
    c1, _ = await api(U_SA, "PUT", f"{_P}/org-access/{org}", {"allowed": True})
    c2, _ = await api(U_SA, "PUT", cpu(key, f"/connections/{org}"), {"point": True})
    return c1 == HTTP_OK and c2 == HTTP_OK


def book(key: str) -> dict:
    if key == "other":
        return {"households": [{"household_id": HH4, "name": "Verify household four"}],
                "accounts": [{"account_id": A4, "household_id": HH4, "name": "Verify org B account"}],
                "positions": [{"position_id": "cp5v-p-a4", "account_id": A4, "as_of": THU.isoformat(),
                               "cusip": CUSIP_EQ, "quantity": "5", "market_value": "260.00", "currency_code": "USD"}],
                "transactions": []}
    pos = []
    for acct in (A1, A2, A3):
        pos += [{"position_id": f"cp5v-p-eq-{acct}", "account_id": acct, "as_of": THU.isoformat(), "cusip": CUSIP_EQ,
                 "quantity": str(HELD_EQ), "market_value": "5200.00", "currency_code": "USD"},
                {"position_id": f"cp5v-p-fd-{acct}", "account_id": acct, "as_of": THU.isoformat(), "cusip": CUSIP_FUND,
                 "quantity": str(HELD_FUND), "market_value": "200.00", "currency_code": "USD"},
                {"position_id": f"cp5v-p-cash-{acct}", "account_id": acct, "as_of": THU.isoformat(),
                 "row_type": "cash", "name": "Cash", "market_value": "100000.00", "currency_code": "USD"}]
    return {
        "households": [{"household_id": HH1, "name": "Verify household one"}],
        "accounts": [{"account_id": A1, "household_id": HH1, "name": "Verify brokerage one"},
                     {"account_id": A2, "household_id": HH1, "name": "Verify brokerage two"},
                     {"account_id": A3, "household_id": HH1, "name": "Verify brokerage three"},
                     {"account_id": A_BOTH, "household_id": HH1, "name": "Verify crosswalked both ways"}],
        "positions": pos,
        "transactions": [],
    }


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CPSIM05 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set(EXEC_PERMS + PROP_PERMS + BLOCK_PERMS + CUSTODY_PERMS) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in (ORG_A, ORG_B):
            for rname, perms in ROLE_PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CPSIM05 verify — {rname}")
                for pn in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CPSIM05 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, org, nm in ((E_A, ORG_A, "A family"), (E_B, ORG_B, "B family")):
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) VALUES "
                               "($1, $2, $3, 'active', 'individual'::entity_type)", eid, org, NAME + nm)
        for hh, org, eid in ((H_A, ORG_A, E_A), (H_B, ORG_B, E_B)):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + "household")
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, eid)
        for acc, org, eid, hh, n, cust, held in (
                (ACC_SIM, ORG_A, E_A, H_A, 1, "ALTRUIST", False), (ACC_REAL, ORG_A, E_A, H_A, 2, "ALTRUIST", False),
                (ACC_NOX, ORG_A, E_A, H_A, 3, "ALTRUIST", False), (ACC_HELD, ORG_A, E_A, H_A, 4, "ALTRUIST", True),
                (ACC_OTHER, ORG_A, E_A, H_A, 5, "ZZ_CPSIM5V_OTHER", False),
                (ACC_B, ORG_B, E_B, H_B, 6, "ALTRUIST", False), (ACC_SIM2, ORG_A, E_A, H_A, 7, "ALTRUIST", False),
                (ACC_SIM3, ORG_A, E_A, H_A, 8, "ALTRUIST", False)):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_held_away, is_discretionary) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, $8, true)", acc, org, f"****{n:04d}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), cust, eid, hh, held)
        for org, src, ext, acc in ((ORG_A, SIM_XWALK, A1, ACC_SIM), (ORG_A, SIM_XWALK, A2, ACC_SIM2),
                                   (ORG_A, SIM_XWALK, A3, ACC_SIM3), (ORG_A, REAL_XWALK, REAL_EXT, ACC_REAL),
                                   (ORG_A, SIM_XWALK, A_BOTH, ACC_REAL), (ORG_B, SIM_XWALK, A4, ACC_B)):
            await conn.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                               "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", org, src, ext, acc)
        for sid, nm, typ in ((SEC_EQ, "EQ", "equity"), (SEC_FUND, "FUND", "fund"), (SEC_NOPX, "NOPX", "equity"),
                             (SEC_BOND, "BOND", "fixed_income")):
            await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, currency_code) "
                               "VALUES ($1, $2, $3, 'USD')", sid, NAME + nm, typ)
        for sid, typ, val, prim in ((SEC_EQ, "cusip", CUSIP_EQ, True), (SEC_EQ, "ticker", TICKER_EQ, False),
                                    (SEC_FUND, "cusip", CUSIP_FUND, True), (SEC_FUND, "ticker", TICKER_FUND, False),
                                    (SEC_NOPX, "ticker", TICKER_NOPX, True), (SEC_BOND, "ticker", TICKER_BOND, True)):
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1, $2, $3, $4)", sid, typ, val, prim)
        for sid, d, px in [(SEC_EQ, d, p) for d, p in PRICE_EQ.items()] + [(SEC_FUND, THU, PRICE_FUND),
                                                                           (SEC_BOND, D0, PRICE_BOND)]:
            await conn.execute("INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                               "currency_code, price_type, source, provider_key) VALUES ($1, $2, $3::numeric, 'USD', "
                               "'close', 'cpsim05 verify', $4)", sid, d, px, PROVIDER_KEY)
        # a model + version + link (FK targets of a model-kind proposal) — rep5's privileged-insert pattern
        await conn.execute("INSERT INTO portfolio.models (id, org_id, name, source, status, created_by) VALUES ($1, $2, "
                           "$3, 'upload', 'draft', $4)", MODEL_A, ORG_A, NAME + "model", U_MK)
        await conn.execute("INSERT INTO portfolio.model_versions (id, org_id, model_id, version_no, status, origin, "
                           "maker_ids, created_by) VALUES ($1, $2, $3, 1, 'draft', 'manual', $4::uuid[], $5)",
                           VERSION_A, ORG_A, MODEL_A, [U_MK], U_MK)
        await conn.execute("INSERT INTO public.account_model_links (id, org_id, account_id, model_id, link_group_id, "
                           "change_kind, status, maker_id, checker_id, decided_at, decision_note) VALUES ($1, $2, $3, "
                           "$4, gen_random_uuid(), 'join', 'rejected', $5, $6, now(), 'CPSIM05 verify — FK target')",
                           LINK_A, ORG_A, ACC_SIM, MODEL_A, U_MK, U_CK)
    # the tenant assets the sync matches — LINKED to the security master (cpsim03 finding 7), CUSIP primary
    for key, sid, cusip, ticker, typ in (("eq", SEC_EQ, CUSIP_EQ, TICKER_EQ, "equity"),
                                         ("fund", SEC_FUND, CUSIP_FUND, TICKER_FUND, "fund")):
        aid = await PA.create_asset(conn, org_id=str(ORG_A), name=NAME + key + " asset", asset_type=typ,
                                    global_security_id=str(sid), currency_code="USD")
        await PA.add_identifier(conn, org_id=str(ORG_A), asset_id=aid, id_type="cusip", id_value=cusip, is_primary=True)
        await PA.add_identifier(conn, org_id=str(ORG_A), asset_id=aid, id_type="ticker", id_value=ticker,
                                is_primary=False)
        S[f"asset_{key}"] = aid
    # the account's Hollisworks position + two open lots (LT: 6 @ 40, ST: 4 @ 55) — what the tax engine relieves
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                           "authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, 'manual', 'manual', "
                           "$7)", POS_EQ, ORG_A, E_A, S["asset_eq"], THU, LOT_LT[0] + LOT_ST[0], ACC_SIM)
        # CPSIM05 FIX 1: the household's held-away account (rep7) needs its OWN current data — with none ("as of
        # never") every proposal of the household is held at approval for an adviser's confirm_held_away_data (the
        # gate is correct; the first run never reached it because every submit had already expired). One fund
        # position as of THU (inside the 30-day stale limit); not the equity, so no wash-sale interaction.
        await conn.execute("INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                           "authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, 1, 'manual', 'manual', "
                           "$6)", POS_HELD, ORG_A, E_A, S["asset_fund"], THU, ACC_HELD)
        for lid, (q, cost, acq), term in ((LOT_LT_ID, LOT_LT, "long"), (LOT_ST_ID, LOT_ST, "short")):
            await conn.execute("INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, "
                               "adjusted_cost, original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, "
                               "'open')", lid, ORG_A, POS_EQ, acq, q, cost, term)
    await set_cap(conn, "yes")
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS), "fixture orgs A, B and their users are "
                    "is_test = true (privileged insert path; the reserved prefix)", f"{len(os_)} {len(us)}")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT, "(Super Admin, maker, checker, second approver, "
                                                                       "sender, reader, B's maker / checker)")
    ok_cp = True
    for key in ("main", "other"):
        code, b = await api(U_SA, "POST", _P, {"name": NAME + key, "institution_type": "altruist",
                                               "slug": SLUG_PREFIX + key})
        issued = (b or {}).get("issued") or {}
        if code != HTTP_OK or not issued.get("api_key", "").startswith("cpsim_live_"):
            ok_cp = False
            continue
        S["cp"][key], S["keys"][key] = b["id"], issued["api_key"]
        S["issued"] += [v for v in issued.values() if v]      # the api key and (cpsim05) the FIX password
        c2, _ = await api(U_SA, "POST", cpu(key, "/activate"))
        ok_cp &= c2 == HTTP_OK
        S["client_ids"][key] = await sim(conn, "SELECT id::text FROM cpsim.counterparty_credentials WHERE "
                                               "counterparty_id = $1::uuid AND kind = 'api_key' AND rotated_at IS NULL "
                                               "AND revoked_at IS NULL", b["id"], fetch="fetchval")
        for kind in ("households", "accounts", "positions", "transactions"):
            rows = book(key)[kind]
            if rows:
                cu, ou = await upload(key, kind, rows)
                ok_cp &= cu == HTTP_OK and (ou.get("counts") or {}).get("rejected") == 0
    ok_pt = await point(ORG_A, "main")
    set_sim_creds("main")
    cc, oc = await api(U_MK, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
    ok_conn = cc == HTTP_OK and oc.get("connected") is True
    ok_ptb = await point(ORG_B, "other")
    cap = await CC.capability(conn, FIX_CAP, CX.CAPABILITY_KEY)
    sess = await sim(conn, "SELECT sender_comp_id, target_comp_id FROM cpsim.counterparty_fix_sessions WHERE "
                           "counterparty_id = $1::uuid AND valid_to IS NULL", S["cp"].get("main"), fetch="fetchrow")
    ok_sess = sess is not None and sess["sender_comp_id"] == FC.DEFAULT_SENDER
    S["comp"] = dict(sess) if sess else {}
    # the block allocation policy (rep9): requested by the maker, approved by the checker — its own maker-checker
    ok_pol = False
    try:
        async with org_ctx(ORG_A) as pool:
            pol = await BK.request_policy(pool, org_id=str(ORG_A), actor_id=str(U_MK), minimum_allocation=Decimal("1"),
                                          policy_text="CPSIM05 verify allocation policy", adv_reference=None,
                                          reason="cpsim05 verify fixture")
            done = await BK.decide_policy(pool, org_id=str(ORG_A), actor_id=str(U_CK), policy_id=pol["id"],
                                          outcome="approve")
        ok_pol = (done or {}).get("status") == "approved"
    except Exception as exc:  # noqa: BLE001
        print(f"[INFO] allocation policy: {type(exc).__name__}: {exc}")
    check(ok_cp and ok_pt and ok_ptb and ok_conn and cap.value == "yes" and ok_sess and ok_pol,
          "two fixture counterparties created (each WITH a FIX session — the altruist type now carries the 'fix' "
          "group), activated and given their books through the admin API; A's simulator connected through the "
          "EXISTING /altruist/connect; the fixture capability equity_trading_api = 'yes'; an approved allocation "
          "policy", f"cp={ok_cp} pointA={ok_pt} pointB={ok_ptb} conn={cc} {dumps(oc)[:200]} cap={cap.value} "
                     f"session={dict(sess) if sess else None} policy={ok_pol}")
    S["seeded"] = bool(ok_test and ok_roles and ok_cp and ok_pt and ok_ptb and ok_conn and cap.value == "yes"
                       and ok_sess and ok_pol)


# ═══ cpsim05 helpers ═══════════════════════════════════════════════════════════════════════════════════════════
SEC_FIX = {SEC_EQ: {"identifiers": {"cusip": CUSIP_EQ, "ticker": TICKER_EQ}},
           SEC_FUND: {"identifiers": {"cusip": CUSIP_FUND, "ticker": TICKER_FUND}}}


async def make_proposal(conn, account, lines: list[dict], *, kind="model", trigger="manual", system=False) -> str:
    """A DRAFT proposal through the privileged insert path (the generator's own engine is rep5's, not under test):
    its lines exactly as given. Everything after — submit, approval, execute, the hook — is the REAL path."""
    pid = uuid.uuid4()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        model = kind == "model"
        # CPSIM05 FIX 1 (FAIL E): the proposal is stamped with THE account's valuation as-of (AM.valuation_as_of — the
        # ONE definition the generator stamps and rep5's expiry compares against), read here, right before submit.
        # The first run stamped the counterparty clock's D0, but the connect / post-fill syncs write positions at the
        # wall-clock sync date (altruist_positions_sync: as_of = date.today()), so _expire_if_due saw "the valuation
        # moved" and every submit was 409 "expired". The expiry rule and the TTL are unchanged.
        val_as_of = await AM.valuation_as_of(conn, ORG_A, account)
        await conn.execute(
            "INSERT INTO rebalance_proposals (id, org_id, account_id, link_id, model_id, model_version_id, trigger, "
            "trigger_ref, status, expires_at, maker_kind, maker_id, proposal_kind, valuation_as_of) VALUES ($1, $2, $3, "
            "$4, $5, $6, $7, $8::jsonb, 'draft', now() + interval '7 days', $9, $10, $11, $12)", pid, ORG_A, account,
            LINK_A if model else None, MODEL_A if model else None, VERSION_A if model else None, trigger,
            json.dumps({} if model else {"cash_policy_id": str(uuid.uuid4())}), "system" if system else "user",
            None if system else U_MK, kind, val_as_of)
        for n, ln in enumerate(lines, start=1):
            await conn.execute(
                "INSERT INTO rebalance_proposal_trades (org_id, proposal_id, line_no, global_security_id, side, "
                "quantity, notional, price, price_source, price_as_of, lots, reason, cash_leg) VALUES ($1, $2, $3, $4, "
                "$5, $6, NULL, $7, 'security_price', $8, $9::jsonb, $10, $11::jsonb)", ORG_A, pid, n, ln["security"],
                ln["side"], Decimal(ln["quantity"]), Decimal(ln.get("price", "50")), D0,
                json.dumps(ln.get("lots", [])), "cpsim05 verify line", json.dumps(ln["cash_leg"])
                if ln.get("cash_leg") else None)
    return str(pid)


async def submit_and_approve(conn, pid: str, *, system=False) -> tuple[int, dict, float]:
    """Submit (the maker; the system for a system proposal), approve through the REAL HTTP route as the checker.
    Returns the approval's (status, body, elapsed seconds)."""
    if system:
        async with org_ctx(ORG_A) as pool:
            await RP.submit(pool, org_id=str(ORG_A), proposal_id=pid, system=True)
    else:
        c, o = await api(U_MK, "POST", f"{_PR}/{pid}/submit")
        if c != HTTP_OK:
            check(False, "the maker submits the proposal (200)", f"{c} {dumps(o)[:300]}")
    t0 = time.monotonic()
    c, o = await api(U_CK, "POST", f"{_PR}/{pid}/approve", {"note": "cpsim05 verify"})
    return c, o, time.monotonic() - t0


async def proposal_row(conn, pid) -> dict:
    r = await platform(conn, "SELECT * FROM rebalance_proposals WHERE id = $1::uuid AND system_to IS NULL", pid,
                       fetch="fetchrow")
    if r is None:
        return {}
    d = dict(r)
    d["execution"] = json.loads(d["execution"]) if isinstance(d["execution"], str) else (d["execution"] or {})
    return d


async def ids_of(conn, pid) -> list[str]:
    return [str(i) for i in (await proposal_row(conn, pid)).get("execution", {}).get("instruction_ids") or []]


async def advance() -> int:
    c, o = await api(U_SA, "POST", cpu("main", "/clock/advance"))
    if c != HTTP_OK:
        check(False, "the Super Admin advances the counterparty clock (200)", f"{c} {dumps(o)[:300]}")
    await FA.drain()
    return c


async def sim_count(conn, table: str) -> int:
    return int(await sim(conn, f"SELECT count(*) FROM cpsim.{table} WHERE counterparty_id = $1::uuid",
                         S["cp"]["main"], fetch="fetchval"))


async def todos(conn, iid, source="execution_fix_sim") -> list:
    return list(await platform(conn, "SELECT user_id, title, detail FROM member_todos WHERE related_id = $1::uuid AND "
                                     "source = $2 AND status = 'open'", iid, source))


async def free_account(conn, pid) -> bool:
    """Close / cancel this proposal's instructions (the existing person actions) so the account takes the next
    proposal (rebalance_proposals_open_uq), then sync_executed → executed."""
    async with org_ctx(ORG_A) as pool:
        for iid in await ids_of(conn, pid):
            row = await instruction(conn, iid)
            try:
                if row["status"] == "filled":
                    await X.reconcile(pool, org_id=str(ORG_A), instruction_id=iid, actor_id=str(U_CK),
                                      method="manual", note="cpsim05 verify: freed")
                    row = await instruction(conn, iid)
                if row["status"] == "reconciled":
                    await X.close(pool, org_id=str(ORG_A), instruction_id=iid, actor_id=str(U_CK))
                elif row["status"] in X.OPEN_STATUSES:
                    await X.cancel(pool, org_id=str(ORG_A), instruction_id=iid, actor_id=str(U_CK),
                                   reason="cpsim05 verify: freed for the next proposal")
            except Exception as exc:  # noqa: BLE001
                print(f"[INFO] free {iid}: {type(exc).__name__}: {exc}")
        await RP.sync_executed(pool, org_id=str(ORG_A), proposal_id=pid)
    return (await proposal_row(conn, pid)).get("status") == "executed"


async def run_proposal(conn, label, account, lines, **kw) -> tuple[str, list[str], tuple]:
    pid = await make_proposal(conn, account, lines, **{k: v for k, v in kw.items() if k in ("kind", "trigger",
                                                                                            "system")})
    res = await submit_and_approve(conn, pid, system=kw.get("system", False))
    await FA.drain()
    S["proposals"][label] = pid
    return pid, await ids_of(conn, pid), res


def ev1(evts, action):
    return next((e for e in evts if e["action"] == action), None)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Discovery + CPSIM05_NOTES (D1–D8 as built, message catalogue); altread1 merged")
    code, out, _e = run_cmd(["git", "log", "main", "--oneline"], REPO, timeout=120)
    check(code == 0 and bool(re.search(r"^\w+ merge altread1$", out, re.M)), "altread1 is merged to main")
    check(VT.regression_mode() == "fast" or os.environ.get("VERIFY_REGRESSIONS") == "full",
          "fast regression mode is present (VERIFY_REGRESSIONS, nested --self-checks-only by default)")
    self_static(check, find)


async def y_schema(conn):
    section("[Y] Schema: fix_messages / fix_allocations append-only (cpsim_app S/I, shared withheld list); minimal "
            "public CHECK / trigger changes (catalog)")
    app_role = await platform(conn, "SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'", fetch="fetchval")
    read = 0
    for t, want in NEW_CPSIM_PRIVS.items():
        oid = await platform(conn, REL, "cpsim", t, fetch="fetchval")
        if oid is None:
            check(False, f"cpsim.{t} exists (catalog, namespace + relname)")
            continue
        read += 1
        privs = {r["privilege_type"] for r in await platform(
            conn, "SELECT x.privilege_type, x.grantee FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid = $1", oid)
            if r["grantee"] == app_role}
        others = await platform(conn, "SELECT r.rolname FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON "
                                      "r.oid = x.grantee WHERE c.oid = $1 AND r.rolname NOT IN ('cpsim_app', "
                                      "'postgres')", oid)
        trg = await platform(conn, "SELECT count(*) FROM pg_trigger WHERE tgrelid = $1 AND NOT tgisinternal", oid,
                             fetch="fetchval")
        check(privs == want and not others and trg == 1, f"cpsim.{t}: cpsim_app holds exactly {sorted(want)} (the "
              "shared _cpsim_fixtures.CPSIM_APP_WITHHELD list); no other role; its append-only guard trigger",
              f"{sorted(privs)} {[r['rolname'] for r in others]} trg={trg}")
    cons = {}
    for nsp, rel in (("cpsim", "orders"), ("cpsim", "fix_messages"), ("cpsim", "fix_allocations"),
                     ("cpsim", "book_transactions"), ("public", "execution_instructions"),
                     ("public", "execution_instruction_events"), ("public", "custodian_capabilities"),
                     ("public", "trade_blocks"), ("public", "rebalance_proposals"),
                     ("public", "rebalance_proposal_trades"), ("portfolio", "securities_global"),
                     ("portfolio", "securities_global_identifiers"), ("portfolio", "securities_global_prices"),
                     ("portfolio", "external_references")):
        cons[f"{nsp}.{rel}"] = await _constraints(conn, nsp, rel)
    n_read = sum(len(v) for v in cons.values())
    check(n_read > 0 and all(cons[k] for k in ("cpsim.orders", "cpsim.fix_messages", "cpsim.fix_allocations")),
          "UNIQUE / CHECK / FK constraints read from the catalog (cpsim objects by namespace + relname, never "
          "::regclass)", f"{n_read}")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = cons.get(table, {}).get(con)
        if d is None:
            bad.append(f"{table}.{con} missing")
            continue
        bad += [f"{table}.{con} lacks {x!r}" for x in lits if f"'{x}'" not in d]
    check(not bad, "every seeded / expected literal in a CHECKed column matches its CHECK (read from the catalog)",
          f"{bad[:5]}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(set(UNIQUES) <= set(idx) and all("UNIQUE" in d for d in idx.values()),
          "the unique indexes the seed and the run rely on exist and are UNIQUE (read before seeding — one open "
          "proposal per account, one fill per ExecID, one allocation leg per account)",
          f"missing {sorted(set(UNIQUES) - set(idx))}")
    ei = cons["public.execution_instructions"]
    check("'fix_sim'" in (ei.get("execution_instructions_custodian_order_chk") or "") and "routed_via" in (
        ei.get("execution_instructions_fix_sim_chk") or ""), "public: custodian_order_chk admits a FIX OrderID on a "
          "fix_sim instruction; execution_instructions_fix_sim_chk (the fix_sim channel only on a fix_sim route)")
    ev = cons["public.execution_instruction_events"].get("execution_instruction_events_action_chk") or ""
    check(ev.count("'::text") == 18 and "'approve_upstream'" in ev and "'automatic_failed'" in ev,
          "the events action CHECK = cpsim03's 16 + approve_upstream + automatic_failed", f"{ev.count(chr(39) + '::text')}")
    trg = await platform(conn, "SELECT tgname, tgtype FROM pg_trigger WHERE tgrelid = (" + REL + ") AND NOT "
                               "tgisinternal AND tgname = 'execution_instruction_events_fix_sim_guard_trg'",
                         "public", "execution_instruction_events", fetch="fetchrow")
    # tgtype bit 2 = BEFORE, bit 4 = INSERT, bit 8 = DELETE, bit 16 = UPDATE
    check(trg is not None and bool(trg["tgtype"] & 2) and bool(trg["tgtype"] & 4) and not trg["tgtype"] & 24,
          "the D5 event trigger exists, BEFORE INSERT only (so teardown deletes have no path to honour)", f"{trg}")
    check(cons["public.trade_blocks"].get("trade_blocks_route_chk") ==
          "CHECK ((route = ANY (ARRAY['manual'::text, 'fix_sim'::text])))",
          "trade_blocks_route_chk = manual, fix_sim — 'fix' still not admitted")
    groups = await sim(conn, "SELECT field_groups FROM cpsim.institution_types WHERE key = 'altruist'",
                       fetch="fetchval")
    check(bool(groups) and "fix" in groups, "the altruist institution type carries the 'fix' field group",
          f"{groups}")
    pols = await platform(conn, "SELECT tablename, cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                                "ANY($1::text[])", ["execution_instructions", "execution_instruction_events",
                                                    "trade_blocks"])
    check({(r["tablename"], r["cmd"]) for r in pols} == {(t, c) for t in ("execution_instructions",
                                                                         "execution_instruction_events",
                                                                         "trade_blocks")
                                                        for c in ("SELECT", "INSERT", "UPDATE", "DELETE")},
          "RLS unchanged: a policy for every operation on the three public tables touched", f"{len(pols)}")
    snap = _snapshot_columns()
    missing = []
    for t, cols_ in SEED_COLUMNS.items():
        have = snap.get(t) or snap.get(t.split(".")[-1]) or set()
        missing += [f"{t}.{c}" for c in cols_ if c not in have]
    check(not missing, "every INSERT's columns appear in docs/schema_snapshot.sql", f"{missing[:5]}")
    check(bool(snap.get("cpsim.fix_messages")) and "protocol" in (snap.get("cpsim.orders") or set()),
          "the snapshot was refreshed (cpsim.fix_messages and cpsim.orders.protocol are in it)")
    S["schema_ok"] = read == 2 and n_read > 0


async def _session():
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            return await FC.session(c, org_id=str(ORG_A))


async def y_endpoint(conn):
    section("[Y] FIX endpoint: parse, checksum, session fields, Reject, every message pair, outcomes, idempotency, "
            "book moves, message log")
    await behaviour(default_outcome="accept")
    sess = await _session()
    check(sess["sender"] == S["comp"].get("sender_comp_id") and sess["target"] == S["comp"].get("target_comp_id"),
          "the client's CompIDs are the counterparty's stored FIX session fields (Hollisworks orientation)",
          f"{sess['sender']}/{sess['target']} vs {S['comp']}")
    n_in0 = await sim_count(conn, "fix_messages")
    cl = f"cp5v-{secrets.token_hex(4)}"
    res = await FC.send(sess, "D", FC.new_order_single(cl_ord_id=cl, account=A2, side="buy", sec=SEC_FIX[SEC_EQ],
                                                       quantity=Decimal("2")), transport=APP_T)
    s = res["summary"]
    price_ok = bool(s["trades"]) and Decimal(s["trades"][0]["price"]) == Decimal(PRICE_EQ[D0])
    check(s["status"] == "filled" and price_ok and FC.first(res["replies"][0], 150) == "0",
          "NewOrderSingle → New (150=0) then a Trade (150=F, 39=2) at EXACTLY the platform close on the counterparty "
          "clock (Decimal)", f"{s} {res['received'][:2]}")
    tx = await sim(conn, "SELECT count(*) FROM cpsim.book_transactions WHERE counterparty_id = $1::uuid AND "
                         "external_id = $2", S["cp"]["main"], "sim-x-" + (s["trades"][0]["exec_id"] if s["trades"]
                                                                          else "none"), fetch="fetchval")
    check(int(tx) == 1, "the fill moved the counterparty book (transaction sim-x-<ExecID>, cpsim03 D7)", f"{tx}")
    rep = await FC.send(sess, "D", FC.new_order_single(cl_ord_id=cl, account=A2, side="buy", sec=SEC_FIX[SEC_EQ],
                                                       quantity=Decimal("9")), transport=APP_T)
    live = await sim(conn, "SELECT count(*) FROM cpsim.orders WHERE counterparty_id = $1::uuid AND client_order_id = "
                           "$2 AND system_to IS NULL", S["cp"]["main"], cl, fetch="fetchval")
    check(rep["summary"]["order_id"] == s["order_id"] and int(live) == 1 and FC.first(rep["replies"][-1], 150) == "I",
          "a repeated ClOrdID → the same order's reports (150=I), ONE order", f"{rep['summary']} live={live}")
    short = [(t, "5" if t == 54 else v) for t, v in FC.new_order_single(
        cl_ord_id=cl + "s", account=A2, side="sell", sec=SEC_FIX[SEC_EQ], quantity=Decimal("1"))]
    rs = await FC.send(sess, "D", short, transport=APP_T)
    check(rs["summary"]["status"] == "rejected" and "no shorting" in (rs["summary"]["reject_reason"] or ""),
          "54=5 (sell short) → rejected 'no shorting declared'", f"{rs['summary']}")
    good = FC.build("H", [(11, cl)], sender=sess["sender"], target=sess["target"], seq=7, delim=FC.PIPE)
    cs = good.split("|10=")[1][:3]
    n9 = good.split("|9=")[1].split("|")[0]
    cases = {"bad checksum": (good.replace(f"|10={cs}", f"|10={(int(cs) + 1) % 256:03d}"), "10"),
             "wrong BodyLength": (good.replace(f"|9={n9}|", f"|9={int(n9) + 2}|", 1), "9"),
             "unknown CompID": (FC.build("H", [(11, cl)], sender="NOT_US", target=sess["target"], seq=8,
                                         delim=FC.PIPE), "49"),
             "missing required tag": (FC.build("D", [(11, cl + "m"), (1, A2), (55, TICKER_EQ), (38, "1"), (40, "1")],
                                               sender=sess["sender"], target=sess["target"], seq=9, delim=FC.PIPE),
                                      "54")}
    for label, (msg, tag) in cases.items():
        lines = await FC.post_message(api_base=sess["api_base"], access_token=sess["access_token"], message=msg,
                                      transport=APP_T)
        f = FC.parse(lines[0]) if lines else []
        check(bool(f) and FC.first(f, 35) == "3" and FC.first(f, 371) == tag and bool(FC.first(f, 58)),
              f"{label} → Reject (35=3) naming tag {tag} with the reason (58) — HTTP 200, never a 500",
              f"{lines[:1]}")
    soh = await FC.send(sess, "H", [(11, cl)], delim=FC.SOH, transport=APP_T)
    check(soh["summary"]["order_id"] == s["order_id"], "SOH and '|' both accepted")
    rows = await sim(conn, "SELECT direction, valid, raw FROM cpsim.fix_messages WHERE counterparty_id = $1::uuid "
                           "ORDER BY created_at", S["cp"]["main"])
    added = len(rows) - n_in0
    check(added >= 2 * (4 + len(cases)) and sum(1 for r in rows if r["direction"] == "in" and not r["valid"]) >= len(cases)
          and not any("554=" in r["raw"] and "[redacted]" not in r["raw"] for r in rows),
          "every message in and out is in cpsim.fix_messages (the Rejects flagged not valid; no password stored)",
          f"added={added}")
    code, out = await api(U_SA, "GET", cpu("main", "/fix/messages"))
    check(code == HTTP_OK and len(out.get("rows") or []) >= added and (out.get("session") or {}).get(
        "sender_comp_id") == sess["sender"], "Admin → Counterparties → FIX: the session fields and the message log "
          "(Super Admin)", f"{code}")
    c2, _ = await api(U_MK, "GET", cpu("main", "/fix/messages"))
    check(c2 == HTTP_FORBIDDEN, "… a non-Super-Admin is refused (403)", f"{c2}")


async def y_auto(conn):
    section("[Y] Automatic path end to end per kind: approval of record via the proposal, FIX send, fills via "
            "enter_fill, post-fill sync, auto-reconcile; P/L = the tax engine's numbers; SIMULATED")
    await behaviour(default_outcome="accept")
    lots = [{"lot_id": str(LOT_LT_ID), "quantity": str(LOT_LT[0])}, {"lot_id": str(LOT_ST_ID), "quantity": str(LOT_ST[0])}]
    pid, ids, (code, out, secs) = await run_proposal(
        conn, "model", ACC_SIM, [{"security": SEC_EQ, "side": "sell", "quantity": "10", "lots": lots},
                                 {"security": SEC_FUND, "side": "buy", "quantity": "2", "price": PRICE_FUND}])
    S["timing"] = {"secs": secs, "code": code, "timings": list(FA.timings)}
    check(code == HTTP_OK and len(ids) == 2, "an approved model proposal (human maker, a different checker) is "
          "executed at the choke point into 2 instructions", f"{code} {dumps(out)[:300]} {ids}")
    rows = [await instruction(conn, i) for i in ids]
    check(all(r.get("routed_via") == "fix_sim" for r in rows), "both routed 'fix_sim' (the adapter's FIX default)",
          f"{[r.get('routed_via') for r in rows]}")
    for r in rows:
        evts = await events_of(conn, r["id"])
        up, ms = ev1(evts, "approve_upstream"), ev1(evts, "mark_sent")
        fs = await fills_of(conn, r["id"])
        check(bool(up and up["detail"].get("checker_id") == str(U_CK) and up["detail"].get("proposal_id") == pid
                   and up["detail"].get("note") == FA.APPROVAL_NOTE and str(up["actor_id"]) == str(U_CK)),
              f"{r['side']}: the approval of record — 'approved via proposal maker-checker (simulated counterparty)', "
              "naming the proposal and its checker (an explicit audited event, never a silent skip)", f"{up}")
        check(bool(ms and ms["detail"].get("channel") == "fix_sim" and ms["detail"].get("on_behalf") == "automatic"
                   and str(ms["actor_id"]) == str(U_CK) and r.get("custodian_order_id", "").startswith("sim-o-")
                   and "35=D" in ((ms["detail"].get("fix") or {}).get("sent") or "")),
              f"{r['side']}: sent automatically — mark_sent channel fix_sim, actor the checker, the FIX OrderID kept, "
              "the NewOrderSingle on the event", f"{ms} {r.get('custodian_order_id')}")
        want_px = Decimal(PRICE_EQ[D0]) if r["side"] == "sell" else None
        check(bool(fs) and all(str(f["entered_by"]) == str(U_CK) and (f["custodian_exec_id"] or "").startswith("sim-e-")
                               and (f["note"] or "").startswith("fix_sim exec ") for f in fs)
              and (want_px is None or all(Decimal(f["price"]) == want_px for f in fs)),
              f"{r['side']}: fills booked by the EXISTING enter_fill — actor the checker, ExecID kept, at the platform "
              "close (Decimal)", f"{[dict(f) for f in fs][:2]}")
        check(r.get("status") == "reconciled" and r.get("reconcile_method") == "custodian_data" and
              r.get("reconcile_reference") == r.get("custodian_order_id"),
              f"{r['side']}: the post-fill sync brought the transactions and the fill was reconciled automatically "
              "(custodian_data, reference = the OrderID)", f"{r.get('status')} {r.get('reconcile_reference')}")
    sell = next((r for r in rows if r.get("side") == "sell"), {})
    fs = await fills_of(conn, sell.get("id")) if sell else []
    lb = await platform(conn, "SELECT character, amount FROM loss_bank_entries WHERE source = 'fill' AND source_id = "
                              "ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL", [f["id"] for f in fs])
    st = sum((Decimal(r["amount"]) for r in lb if r["character"] == "short"), Decimal(0))
    lt = sum((Decimal(r["amount"]) for r in lb if r["character"] == "long"), Decimal(0))
    px = Decimal(PRICE_EQ[D0])
    want_lt = (LOT_LT[0] * (px - LOT_LT[1] / LOT_LT[0])).quantize(Decimal("0.01"))
    want_st = (LOT_ST[0] * (px - LOT_ST[1] / LOT_ST[0])).quantize(Decimal("0.01"))
    check(bool(lb) and lt == want_lt and st == want_st, "the tax engine's own realized figures (loss bank, fill "
          f"source, the lots the instruction named): LT {want_lt}, ST {want_st}", f"lt={lt} st={st} {[dict(x) for x in lb]}")
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            det = await RP.detail(c, str(ORG_A), pid)
            lots_now = await X.account_lots(c, str(ORG_A), str(ACC_SIM))
            closes = await FPL.last_platform_closes(c, [str(SEC_EQ)], date.today())
    pl = det.get("fix_sim_pl") or {}
    t = pl.get("totals") or {}
    want_u = sum((HV.evaluate_lot(quantity=Decimal(str(l["quantity"])), unit_cost=Decimal(str(l["adjusted_cost"])) /
                                  Decimal(str(l["quantity"])), price=closes[str(SEC_EQ)][0], term=l["term"] or "",
                                  settings=FPL._EVAL_SETTINGS, rates=None)["unrealized"] for l in lots_now),
                 Decimal(0)) if str(SEC_EQ) in closes else None
    indep = (LOT_LT[0] * (closes[str(SEC_EQ)][0] - Decimal(40)) + LOT_ST[0] * (closes[str(SEC_EQ)][0] - Decimal(55))
             ) if str(SEC_EQ) in closes else None
    check(pl.get("label") == "SIMULATED" and all(x.get("label") == "SIMULATED" for x in pl.get("trades") or [{}])
          # CPSIM05 FIX 1: compared as Decimals — fix_sim_pl._money renders 72.00 as "72" (trailing zeros stripped),
          # so the first comparison of strings ("72" vs "72.00") failed on equal figures
          and t.get("realized_lt") is not None and t.get("realized_st") is not None
          and Decimal(t["realized_lt"]) == want_lt and Decimal(t["realized_st"]) == want_st,
          "the proposal page's P/L: realized ST / LT equal the tax engine's, every figure labelled SIMULATED",
          f"{t}")
    got_u = (pl.get("unrealized") or {}).get("total")
    check(want_u is not None and indep is not None and got_u is not None and Decimal(got_u) ==
          want_u.quantize(Decimal("0.01")) == indep.quantize(Decimal("0.01")),
          "the account's unrealized P/L at the last platform close = harvest.evaluate_lot over the tax engine's lots "
          "= the arithmetic done here independently", f"{pl.get('unrealized')} {want_u} {indep}")
    c3, o3 = await api(U_RD, "GET", f"{_X}/instructions/{sell.get('id')}")
    fx = (o3.get("instruction") or {}).get("fix_exchange") or {}
    names = {m.get("msg_name") for m in fx.get("messages") or []}
    check(c3 == HTTP_OK and fx.get("automatic") is True and {"NewOrderSingle", "ExecutionReport"} <= names,
          "the instruction page shows the FIX exchange (readable) and 'automatic — simulated counterparty' (a reader "
          "sees it)", f"{c3} {names}")
    find("tilts / completion (rep10) are not a proposal kind — they adjust a MODEL proposal's target "
         "(rebalance_proposals._rep10_adjust in target_for), so a tilted proposal takes exactly the model path proven "
         "above (execute has no tilt branch)",
         "static: 'tilt' does not appear in rebalance_proposals.execute" if "tilt" not in inspect.getsource(RP.execute)
         else "execute names tilts — re-check")
    S["auto_ok"] = True


async def y_cash(conn):
    # CPSIM05 FIX 1: split out of y_auto (its own account ACC_SIM3, its own behaviour) so each one-off fits the
    # 600 s limit; it sets up everything it needs itself
    section("[Y] Automatic path, cash proposal: the FUND line automatic; the PAR line refused by the simulator")
    await behaviour(default_outcome="accept")
    # the cash proposal: the fund line automatic; the par line refused by the simulator (unsupported instrument)
    cpid, cids, (cc, co, _s) = await run_proposal(
        conn, "cash", ACC_SIM3, [{"security": SEC_FUND, "side": "buy", "quantity": "1", "price": PRICE_FUND,
                                  "cash_leg": {"sleeve": "reserve", "price_kind": "market"}},
                                 {"security": SEC_BOND, "side": "buy", "quantity": "1000", "price": PRICE_BOND,
                                  "cash_leg": {"sleeve": "long_term", "price_kind": "limit", "limit_price": "99.5"}}],
        kind="cash")
    crow = {(await instruction(conn, i)).get("global_security_id"): await instruction(conn, i) for i in cids}
    fund, bond = crow.get(SEC_FUND, {}), crow.get(SEC_BOND, {})
    rej = ev1(await events_of(conn, bond.get("id")), "custodian_reject") if bond else None
    check(cc == HTTP_OK and fund.get("status") == "reconciled", "a cash proposal's FUND line: sent, filled, synced and "
          "reconciled automatically", f"{cc} {fund.get('status')} {dumps(co)[:200]}")
    check(bool(rej and "unsupported instrument" in (rej["detail"].get("reason") or "")) and bond.get("status") ==
          "approved" and bool(await todos(conn, bond.get("id"))), "… its fixed-income PAR line is refused by the "
          "simulator ('unsupported instrument'): left approved for a person, with the reason and a to-do",
          f"{bond.get('status')} {rej}")


async def y_outcomes(conn):
    section("[Y] Outcomes and failure → true state + to-do: reject, partial (rest on advance), delay, limit (a person's "
            "fix_sim instruction), no price, insufficient position — each with its FIX tags")
    pid = S["proposals"].get("model")
    if pid:
        await free_account(conn, pid)
    await behaviour(default_outcome="reject", reject_reason=REJECT_REASON)
    pid, ids, _r = await run_proposal(conn, "reject", ACC_SIM, [{"security": SEC_EQ, "side": "sell", "quantity": "1"}])
    row = await instruction(conn, ids[0]) if ids else {}
    rej = ev1(await events_of(conn, row.get("id")), "custodian_reject") if row else None
    tds = await todos(conn, row.get("id")) if row else []
    check(row.get("status") == "approved" and bool(rej and rej["detail"].get("reason") == REJECT_REASON
                                                   and "39=8" in "".join((rej["detail"].get("fix") or {}).get(
                                                       "received") or [])) and not await fills_of(conn, row.get("id"))
          and any(str(t["user_id"]) == str(U_CK) for t in tds),
          "reject → the instruction stays APPROVED, the reason (39=8 / 58) on a custodian_reject event, a to-do for "
          "the checker, nothing booked", f"{row.get('status')} {rej} {len(tds)}")
    await free_account(conn, pid)
    await behaviour(default_outcome="partial", partial_pct=PARTIAL_PCT)
    pid, ids, _r = await run_proposal(conn, "partial", ACC_SIM, [{"security": SEC_EQ, "side": "buy", "quantity": "5"}])
    row = await instruction(conn, ids[0]) if ids else {}
    f1 = await fills_of(conn, row.get("id")) if row else []
    check(row.get("status") == "partially_filled" and sum((Decimal(f["quantity"]) for f in f1), Decimal(0)) ==
          Decimal("2"), "partial (40%) → 39=1: 2 of 5 booked, partially filled", f"{row.get('status')} {len(f1)}")
    await advance()      # D1: the rest fills; the advance's collect books it (OrderStatusRequest)
    row = await instruction(conn, ids[0]) if ids else {}
    f2 = await fills_of(conn, row.get("id")) if row else []
    check(row.get("status") == "reconciled" and len(f2) == 2 and Decimal(f2[-1]["price"]) == Decimal(PRICE_EQ[D1]),
          "… the rest fills on the clock advance (the FIX rule, D2) at the new close, booked and reconciled then",
          f"{row.get('status')} {[(str(f['quantity']), str(f['price'])) for f in f2]}")
    await free_account(conn, pid)
    await behaviour(default_outcome="delay", delay_seconds=0)
    pid, ids, _r = await run_proposal(conn, "delay", ACC_SIM, [{"security": SEC_EQ, "side": "buy", "quantity": "1"}])
    row = await instruction(conn, ids[0]) if ids else {}
    check(row.get("status") == "sent" and not await fills_of(conn, row.get("id")), "delay → New (39=0): sent, nothing "
          "filled", f"{row.get('status')}")
    await advance()      # D2
    row = await instruction(conn, ids[0]) if ids else {}
    f3 = await fills_of(conn, row.get("id")) if row else []
    check(row.get("status") == "reconciled" and len(f3) == 1 and Decimal(f3[0]["price"]) == Decimal(PRICE_EQ[D2]),
          "… filled after the advance, booked and reconciled then", f"{row.get('status')} {len(f3)}")
    await free_account(conn, pid)
    await behaviour(default_outcome="accept")
    pid, ids, _r = await run_proposal(conn, "nopx_oversell", ACC_SIM,
                                      [{"security": SEC_NOPX, "side": "buy", "quantity": "1", "price": "10"},
                                       {"security": SEC_EQ, "side": "sell", "quantity": "100000"}])
    reasons = {}
    for i in ids:
        r = await instruction(conn, i)
        e = ev1(await events_of(conn, i), "custodian_reject")
        reasons[str(r.get("global_security_id"))] = ((e or {}).get("detail") or {}).get("reason"), r.get("status")
    check(reasons.get(str(SEC_NOPX), (None,))[0] == "no price" and reasons.get(str(SEC_EQ), (None,))[0] ==
          "insufficient position" and all(v[1] == "approved" for v in reasons.values()),
          "no price → rejected 'no price'; an oversell → rejected 'insufficient position'; both left approved",
          f"{reasons}")
    await free_account(conn, pid)
    # limit: a PERSON's ad-hoc instruction (not automatic — D5) sent over FIX by a person; filled on the advance;
    # the advance tells its sender (to-do) and the person imports
    iid = await approved(conn, trade_list(ACC_SIM2, kind="limit", limit="40"), "limit")
    if iid:
        row = await instruction(conn, iid)
        c, o = await send(U_SD, iid)
        row = await instruction(conn, iid)
        check(c == HTTP_OK and row.get("routed_via") == "fix_sim" and row.get("sent_channel") == "fix_sim" and
              not await fills_of(conn, iid) and str(row.get("sent_by")) == str(U_SD),
              "a limit not marketable (40 vs 50) → New (39=0): a person's fix_sim instruction sent by that person, "
              "nothing filled", f"{c} {dumps(o)[:300]} {row.get('status')}")
        await advance()      # D3: the close 39 makes the limit marketable
        tds = await todos(conn, iid)
        check(not await fills_of(conn, iid) and any(str(t["user_id"]) == str(U_SD) for t in tds),
              "… the advance fills it at the counterparty, but books NOTHING for a person-sent instruction (the "
              "automatic path is proposals only) — its sender gets a 'Fills available' to-do", f"{len(tds)}")
        c, o = await import_fills(U_SD, iid)
        f4 = await fills_of(conn, iid)
        check(c == HTTP_OK and len(f4) == 1 and Decimal(f4[0]["price"]) == Decimal(PRICE_EQ[D3]) and
              str(f4[0]["entered_by"]) == str(U_SD), "… the person presses Import fills: booked at the D3 close (the "
              "person is the actor)", f"{c} {dumps(o)[:200]}")


async def y_idempotency(conn):
    section("[Y] Idempotency: a repeated NewOrderSingle → one order; a re-run of the automatic step books nothing twice")
    ids = await ids_of(conn, S["proposals"].get("model", ""))
    # CPSIM05 FIX 1 (FAIL I): a generator expression containing ``await`` is an ASYNC generator — next() cannot
    # consume it (TypeError). Found by an explicit loop instead.
    iid = None
    for i in ids:
        if (await instruction(conn, i)).get("side") == "sell":
            iid = i
            break
    if not iid:
        check(False, "the model proposal's sell instruction exists (from the auto section)")
        return
    before = len(await fills_of(conn, iid))
    orders0 = await sim_count(conn, "orders")
    async with org_ctx(ORG_A) as pool:
        r1 = await FA.run_one(pool, str(ORG_A), iid, str(U_CK))
        await FA.after_batch(pool, str(ORG_A), [iid], str(U_CK))
    check(r1.get("outcome") == "nothing_to_do" and len(await fills_of(conn, iid)) == before and
          await sim_count(conn, "orders") == orders0, "re-running the automatic step on a done instruction books "
          "nothing and sends nothing", f"{r1} {before}")
    row = await instruction(conn, iid)
    sess = await _session()
    rep = await FC.send(sess, "D", FC.new_order_single(cl_ord_id=iid, account=A1, side="sell", sec=SEC_FIX[SEC_EQ],
                                                       quantity=Decimal("10")), transport=APP_T)
    live = await sim(conn, "SELECT count(*) FROM cpsim.orders WHERE counterparty_id = $1::uuid AND client_order_id = "
                           "$2 AND system_to IS NULL", S["cp"]["main"], iid, fetch="fetchval")
    check(rep["summary"]["order_id"] == row.get("custodian_order_id") and int(live) == 1,
          "the same ClOrdID sent again → the same order, one live order", f"{rep['summary']} {live}")


async def y_system(conn):
    section("[Y] A SYSTEM proposal keeps rep2's second-person approval; then automatic")
    await behaviour(default_outcome="accept")
    o0 = await sim_count(conn, "orders")
    pid, ids, (code, out, _s) = await run_proposal(conn, "harvest", ACC_SIM2,
                                                   [{"security": SEC_FUND, "side": "sell", "quantity": "3",
                                                     "price": PRICE_FUND}], trigger="harvest", system=True)
    rows = [await instruction(conn, i) for i in ids]
    # CPSIM05 FIX 1: the same async-generator shape as FAIL I (any() over a genexpr containing await) — a list
    # comprehension is awaited inline
    upstream = [ev1(await events_of(conn, r["id"]), "approve_upstream") for r in rows]
    check(code == HTTP_OK and bool(rows) and all(r.get("status") == "pending_approval" for r in rows) and
          await sim_count(conn, "orders") == o0 and not any(upstream),
          "a system (harvest) proposal reviewed → its instructions wait for rep2's approval: nothing sent, no "
          "approval-of-record event", f"{code} {[r.get('status') for r in rows]}")
    for r in rows:
        c, o = await api(U_MA, "POST", f"{_X}/instructions/{r['id']}/approve", {})
        await FA.drain()
        cur = await instruction(conn, r["id"])
        ms = ev1(await events_of(conn, r["id"]), "mark_sent")
        check(c == HTTP_OK and cur.get("status") == "reconciled" and str((ms or {}).get("actor_id")) == str(U_MA)
              and str(cur.get("approved_by")) == str(U_MA),
              "… a second person (approve_execution) approves → sent, filled and reconciled automatically, as that "
              "approver", f"{c} {cur.get('status')} {dumps(o)[:200]}")


async def y_guard(conn):
    section("[Y] Guard: demo-only by every condition; the DB backstop; the call-site scan; real routes manual; the "
            "person actions intact")
    o0, m0 = await sim_count(conn, "orders"), await sim_count(conn, "fix_messages")

    async def manual(uid, acc, label, condition, body=None):
        c, o = await route(uid, body or trade_list(acc))
        rr = o.get("routing") or {}
        rid = (rr.get("instruction_ids") or [None])[0]
        rw = await instruction(conn, rid) if rid else {}
        sim_ = rr.get("simulator") or {}
        check(bool(c == HTTP_CREATED and rr.get("routed_to") == "manual" and rw.get("routed_via") not in
                   ("fix_sim", "custodian_api") and (condition is None or sim_.get("condition") == condition)),
              f"{label} → manual" + (f" with the reason (condition {condition})" if condition else ""),
              f"{c} {dumps(rr)[:300]}")
        return rr

    rr = await manual(U_MK, ACC_REAL, "a REAL ALTRUIST-crosswalked account", "real_crosswalk")
    check("'unknown'" in (rr.get("reason") or ""), "… its reason is the real Altruist capability ('unknown')")
    await manual(U_MK, ACC_NOX, "condition: no simulator crosswalk", "crosswalk")
    await api(U_SA, "PUT", f"{_P}/org-access/{ORG_A}", {"allowed": False})
    await manual(U_MK, ACC_SIM2, "condition: counterparty_sim.allowed off", "allowed")
    await api(U_SA, "PUT", f"{_P}/org-access/{ORG_A}", {"allowed": True})
    await manual(U_MKB, ACC_B, "condition: no simulator connection (org B)", "connection")
    await set_cap(conn, "unknown")
    await manual(U_MK, ACC_SIM2, "condition: the simulator's equity_trading_api 'unknown'", "capability")
    await set_cap(conn, "yes")
    await manual(U_MK, ACC_SIM2, "a per-trade rule (acceptable-range price)", "trade",
                 body=trade_list(ACC_SIM2, kind="range"))
    try:
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c:
                acct = await X.load_account(c, ORG_A, ACC_SIM2)
            res = await route_trade_list(pool, org_id=str(ORG_A), actor_id=str(U_MK),
                                         account={**acct, "authority": "advise", "custody": "integrated"},
                                         trade_list=TradeList(**{**trade_list(ACC_SIM2), "executor_type": "member"}))
        check(res.routed_to == "manual" and "never a custodian API" in res.reason, "condition: authority advise → the "
              "rep7b manual reason, before the simulator is considered", f"{res.as_dict()}")
    except Exception as exc:  # noqa: BLE001
        check(False, "condition authority (advise) routed", f"{type(exc).__name__}: {exc}")
    find("condition demo_household (a real org's non-demo household) is proven by verify_cpsim03's rolled-back "
         "NON-test org section — the same simulator_route function this route and the guard both call")
    check(await sim_count(conn, "orders") == o0 and await sim_count(conn, "fix_messages") == m0,
          "… and NOTHING was sent for any of them (no order, no FIX message)",
          f"orders {o0}→{await sim_count(conn, 'orders')}")
    # the guard itself refuses a manual instruction (the automatic sender reached by hand)
    mid = await approved(conn, trade_list(ACC_NOX), "manual_for_guard")
    if mid:
        async with org_ctx(ORG_A) as pool:
            out = await FA.run_one(pool, str(ORG_A), mid, str(U_CK))
        audit = await platform(conn, "SELECT count(*) FROM audit_log WHERE resource_id = $1::uuid AND action = "
                                     "'execution.fix_sim_refused'", mid, fetch="fetchval")
        row = await instruction(conn, mid)
        check(out.get("outcome") == "failed" and "not routed to the simulated FIX counterparty" in out.get("reason", "")
              and row.get("status") == "approved" and int(audit) == 1 and await sim_count(conn, "orders") == o0,
              "the automatic sender called on a MANUAL instruction refuses with the reason (audited), sends nothing, "
              "leaves it approved for a person", f"{out} audit={audit}")

        async def ins_event(c):
            await c.execute("INSERT INTO execution_instruction_events (org_id, instruction_id, action, from_status, "
                            "to_status, actor_id, detail) VALUES ($1, $2, 'mark_sent', 'approved', 'approved', $3, "
                            "$4::jsonb)", ORG_A, mid, U_CK, json.dumps({"channel": "fix_sim"}))
        _r, err = await rolled_back(conn, ins_event, org=ORG_A)
        check(err is not None and "cpsim05" in str(err), "the DB backstop refuses a fix_sim event on a non-fix_sim "
              "instruction (the trigger)", f"{type(err).__name__ if err else None}: {err}")

        async def bad_channel(c):
            await c.execute("UPDATE execution_instructions SET sent_channel = 'fix_sim' WHERE id = $1 AND system_to "
                            "IS NULL", mid)
        _r, err = await rolled_back(conn, bad_channel, org=ORG_A)
        check(err is not None and ("fix_sim_chk" in str(err) or "frozen" in str(err) or "custodian_order_chk" in
                                   str(err)), "… and the CHECK refuses the fix_sim channel on a non-fix_sim route",
              f"{err}")
    # the scan: the automatic entry points have only their documented call sites; not a registered verb
    want = {"after_proposal_routed": {"apps/api/services/rebalance_proposals.py"},
            "after_instruction_approved": {"apps/api/services/execution.py"},
            "collect_after_advance": {"apps/api/routers/admin_counterparties.py"},
            "run_person": {"apps/api/services/custodian_execution.py"},
            "after_block_approved": {"apps/api/services/blocks.py"}}
    got = {k: set() for k in want}
    for p in (API_DIR / "services").rglob("*.py"):
        _scan(p, got)
    for p in (API_DIR / "routers").rglob("*.py"):
        _scan(p, got)
    for extra in ("main.py", "workflow_scheduler_tick.py"):
        if (API_DIR / extra).exists():
            _scan(API_DIR / extra, got)
    check(got == want, "the scan: each automatic entry point is called only from its documented site (the choke point; "
          "rep2 approval of a proposal-sourced instruction; the clock advance; the person Send / Import; block "
          "approval) — nothing else, no scheduler tick", f"{got}")
    # CPSIM05 FIX 1 (FAIL N): the registry is filled by register_all() in the app's lifespan, which ASGITransport
    # never runs — the first run enumerated an EMPTY registry (0). Enumerate it exactly as cpsim04 fix 1 does.
    from services.assistant_actions import register_all as _register_all
    if not REGISTRY.all():
        _register_all()
    acts = REGISTRY.all()
    REGISTRY_KEYS = [a.key for a in acts]
    auto_mods = ("services.fix_sim_auto", "services.fix_sim_blocks")
    entry_rx = re.compile(r"\b(?:after_proposal_routed|after_instruction_approved|collect_after_advance|run_person|"
                          r"after_block_approved|run_one|after_batch)\(|fix_sim_auto|fix_sim_blocks")

    def _handler_hits(actions) -> list[str]:
        out = []
        for a in actions:
            for h in (a.handler, a.draft_handler):
                if h is None:
                    continue
                try:
                    hs = _code_only(inspect.getsource(h))
                except (OSError, TypeError):
                    hs = ""
                if getattr(h, "__module__", "") in auto_mods or entry_rx.search(hs):
                    out.append(a.key)
        return out
    hits = _handler_hits(acts)
    # positive control: a planted action whose handler IS the automatic sender must be caught by the same detector
    planted = types.SimpleNamespace(key="zz.planted", handler=FA.run_one, draft_handler=None)
    check(len(acts) >= 20 and not hits and _handler_hits([planted]) == ["zz.planted"],
          f"every registered action ({len(acts)}) — handler and draft handler — neither lives in nor calls the "
          "automatic sender (positive control: a planted action whose handler is fix_sim_auto.run_one IS caught)",
          f"{hits[:5]}")
    sweeps, sweep_hits = [], []
    for sp in sorted((API_DIR / "services").glob("*.py")):
        if sp.stem in ("fix_sim_auto", "fix_sim_blocks") or not re.search(
                r"^(?:async )?def [A-Za-z_]*sweep[A-Za-z_]*\(", sp.read_text(encoding="utf-8"), re.M):
            continue
        mod = importlib.import_module(f"services.{sp.stem}")
        for nm, f in inspect.getmembers(mod, inspect.isfunction):
            if "sweep" in nm and f.__module__ == mod.__name__:
                sweeps.append(f"{sp.stem}.{nm}")
                if entry_rx.search(_code_only(inspect.getsource(f))):
                    sweep_hits.append(f"{sp.stem}.{nm}")
    tick = API_DIR / "workflow_scheduler_tick.py"
    tsrc = _code_only(tick.read_text(encoding="utf-8")) if tick.exists() else ""
    ctl = entry_rx.search(_code_only((API_DIR / "services" / "rebalance_proposals.py").read_text(encoding="utf-8")))
    check(len(sweeps) >= 5 and not sweep_hits and tick.exists() and not entry_rx.search(tsrc) and ctl is not None,
          f"every sweep ({len(sweeps)}: {', '.join(sweeps[:5])}…) and the scheduler tick name no automatic entry "
          "point (positive control: the same pattern finds the choke point's call in rebalance_proposals)",
          f"{sweep_hits[:5]} tick={tick.exists()} ctl={bool(ctl)}")
    check(bool(REGISTRY_KEYS) and not any("fix_sim" in k for k in REGISTRY_KEYS) and
          WA.VERB_AUTONOMY.get("execution.fix_sim_auto", {}).get("class") == WA.NEVER and
          sod.VERB_DUTIES.get("execution.fix_sim_auto") == "write" and
          WA.VERB_AUTONOMY.get("execution.custodian_send", {}).get("class") == WA.NEVER and
          WA.VERB_AUTONOMY.get("execution.custodian_import", {}).get("class") == WA.NEVER,
          "execution.fix_sim_auto is declared NEVER (unattended) and is not a registered action; cpsim03's Send / "
          "Import are still NEVER", f"{len(REGISTRY_KEYS)}")
    # cpsim03's REST person route still works when a subclass / the flag asks for it
    saved = AltruistTradeAdapter.simulator_protocol
    AltruistTradeAdapter.simulator_protocol = "rest"
    try:
        rid = await approved(conn, trade_list(ACC_SIM3, quantity="1"), "rest_person")
        row = await instruction(conn, rid) if rid else {}
        c, o = await send(U_SD, rid) if rid else (0, {})
        row2 = await instruction(conn, rid) if rid else {}
        check(row.get("routed_via") == "custodian_api" and c == HTTP_OK and row2.get("sent_channel") ==
              "custodian_api", "cpsim03's REST orders route is still callable (protocol 'rest'): a person's Send to "
              "custodian answers and records custodian_api", f"{row.get('routed_via')} {c} {dumps(o)[:200]}")
    finally:
        AltruistTradeAdapter.simulator_protocol = saved
    check(AltruistTradeAdapter.simulator_protocol == "fix", "the adapter's default protocol is 'fix'")


def _scan(p: pathlib.Path, got: dict) -> None:
    rel = p.relative_to(REPO).as_posix()
    if rel.endswith(("fix_sim_auto.py", "fix_sim_blocks.py")):
        return
    src = _code_only(p.read_text(encoding="utf-8"))
    for name in got:
        if re.search(rf"\.{name}\(", src):
            got[name].add(rel)


async def y_blocks(conn):
    section("[Y] Blocks on fix_sim (all members simulated): Hollisworks average price, allocation + Ack; manual "
            "default; 'fix' refused")
    await behaviour(default_outcome="accept")
    m1 = await approved(conn, trade_list(ACC_SIM2, quantity="3"), "block_m1")
    m2 = await approved(conn, trade_list(ACC_SIM3, quantity="2"), "block_m2")
    nx = await approved(conn, trade_list(ACC_NOX, quantity="1"), "block_nx")
    if not (m1 and m2 and nx):
        check(False, "the block members exist (approved fix_sim / manual instructions)")
        return
    c, o = await api(U_MK, "POST", f"{_X}/blocks", {"instruction_ids": [m1, nx], "route": "fix_sim"})
    check(c == HTTP_CONFLICT and "simulated" in dumps(o), "a block with one non-simulated member cannot take fix_sim "
          "(409, the member's reason)", f"{c} {dumps(o)[:300]}")
    c, o = await api(U_MK, "POST", f"{_X}/blocks", {"instruction_ids": [m1, m2], "route": "fix"})
    check(c == HTTP_CONFLICT and "not connected" in dumps(o), "route 'fix' is still refused (not connected)",
          f"{c} {dumps(o)[:200]}")
    check(XR.DEFAULT_ROUTE == "manual" and XR.route_refusal("manual") is None and XR.any_connected() is False,
          "manual is still the default and always offered; the simulated router does not count as a real automated "
          "route")
    c, o = await api(U_MK, "POST", f"{_X}/blocks", {"instruction_ids": [m1, m2], "route": "fix_sim"})
    bid = ((o.get("block") or {}).get("id"))
    if c != HTTP_CREATED or not bid:
        check(False, "a fix_sim block over two simulated members is built (201)", f"{c} {dumps(o)[:300]}")
        return
    S["blocks"]["sim"] = bid
    c1, o1 = await api(U_MK, "POST", f"{_X}/blocks/{bid}/submit", {})
    c2, o2 = await api(U_CK, "POST", f"{_X}/blocks/{bid}/approve", {"note": "cpsim05 verify"})
    await FA.drain()
    blk = dict(await platform(conn, "SELECT * FROM trade_blocks WHERE id = $1::uuid AND system_to IS NULL", bid,
                              fetch="fetchrow") or {})
    bf = list(await platform(conn, "SELECT quantity, price, note FROM block_fills WHERE block_id = $1::uuid AND "
                                   "system_to IS NULL", bid))
    q = sum((Decimal(f["quantity"]) for f in bf), Decimal(0))
    avg = (sum((Decimal(f["quantity"]) * Decimal(f["price"]) for f in bf), Decimal(0)) / q) if q else None
    al = list(await platform(conn, "SELECT status, settlement_status, final_quantity FROM block_allocations WHERE "
                                   "block_id = $1::uuid AND system_to IS NULL", bid))
    legs = await sim(conn, "SELECT count(*) FROM cpsim.fix_allocations WHERE counterparty_id = $1::uuid AND alloc_id "
                           "= $2", S["cp"]["main"], f"{bid}:alloc", fetch="fetchval")
    check(c1 == HTTP_OK and c2 == HTTP_OK and blk.get("route") == "fix_sim" and q == Decimal("5") and avg is not None
          and Decimal(str(blk.get("average_price"))) == avg and all(f["note"].startswith("fix_sim exec ") for f in bf),
          "the block's own maker-checker approved it → block NewOrderSingle, fills via blocks.enter_fill, the average "
          "price computed by Hollisworks from the fills", f"{c1} {c2} {blk.get('status')} {avg} {blk.get('average_price')}")
    check(blk.get("status") in ("allocated", "settled") and len(al) == 2 and all(
        a["status"] == "allocated" and a["settlement_status"] == "settled" for a in al) and int(legs) == 2,
          "allocated by the EXISTING allocation path; AllocationInstruction (35=J) accepted (35=P) — each "
          "allocation settled, each account moved at the counterparty", f"{blk.get('status')} {[dict(a) for a in al]} "
                                                                        f"legs={legs}")
    for m in (m1, m2):
        r = await instruction(conn, m)
        check(r.get("sent_channel") == "fix_sim" and r.get("status") in ("filled", "reconciled"),
              "a member instruction got its fill from the allocation path (sent fix_sim, filled / reconciled)",
              f"{r.get('status')} {r.get('sent_channel')}")


async def y_unsupported(conn):
    section("[Y] Unsupported kinds stay manual with the reason: option legs, par lines (above), shorts")
    src = inspect.getsource(__import__("services.options_proposals", fromlist=["x"]).execute)
    check('routed_via="manual"' in src and "route_trade_list" not in src, "an options proposal's legs are created "
          "routed_via 'manual' directly (rep8 — never an adapter, never the simulator)")
    d = await platform(conn, "SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k WHERE k.conrelid = (" + REL +
                       ") AND k.conname = 'execution_instructions_option_terms_chk'", "public",
                       "execution_instructions", fetch="fetchval")
    check(bool(d and "'manual'" in d), "… and the database ties option terms to the manual route", f"{d}")
    o0 = await sim_count(conn, "orders")
    try:
        # CPSIM05 FIX 1 (FAIL S): a VALID rep13 SELL SHORT line — it carries its short terms (position effect open),
        # as TradeLine requires — so the model accepts it and only the ROUTE can refuse it.
        body = trade_list(ACC_SIM2, side="sell_short", quantity="1")
        body["trades"][0]["short_terms"] = {"position_effect": "open"}
        tl = TradeList(**body)
        check(tl.trades[0].side == "sell_short" and tl.trades[0].short_terms == {"position_effect": "open"},
              "the SELL SHORT line is a valid rep13 trade list (short terms carried) — the refusal below is the "
              "route's, not the model's")
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c:
                acct = await X.load_account(c, ORG_A, ACC_SIM2)
            await route_trade_list(pool, org_id=str(ORG_A), actor_id=str(U_MK), account=acct, trade_list=tl)
        check(False, "a SELL SHORT is refused at the adapter (no shorting declared)")
    except ShortSaleNotDeclared as exc:
        check("short_selling" in str(exc) and await sim_count(conn, "orders") == o0, "a SELL SHORT on a simulated "
              "account is refused with the reason (no short_selling declared; the manual route offered); nothing "
              "sent", str(exc)[:200])
    except Exception as exc:  # noqa: BLE001
        check(False, "a SELL SHORT is refused with ShortSaleNotDeclared", f"{type(exc).__name__}: {exc}")


async def y_isolation(conn):
    section("[Y] Isolation: org A's instructions / proposals / FIX evidence never visible to org B (both directions)")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["b"] is False, "the verify connection does not bypass RLS", f"{who['u']} {who['b']}")
    a_in_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM execution_instructions WHERE routed_via = 'fix_sim' "
                                           "AND system_to IS NULL", fetch="fetchval"))
    a_in_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM execution_instructions WHERE org_id = $1::uuid",
                              ORG_A, fetch="fetchval"))
    ev_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM execution_instruction_events WHERE org_id = $1::uuid",
                            ORG_A, fetch="fetchval"))
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, record_type, "
                           "record_id) VALUES ($1, 'CPSIM05_ISO', 'cp5v-iso-b', 'account', $2)", ORG_B, ACC_B)
    b_in_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.external_references WHERE source_system = "
                                           "'CPSIM05_ISO'", fetch="fetchval"))
    b_in_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.external_references WHERE source_system = "
                                           "'CPSIM05_ISO'", fetch="fetchval"))
    check(a_in_a > 0 and a_in_b == 0 and ev_b == 0 and b_in_b == 1 and b_in_a == 0,
          "RLS both ways: A sees its fix_sim instructions (non-empty) and B sees none of A's instructions or events; "
          "B's row is B's alone", f"a_in_a={a_in_a} a_in_b={a_in_b} ev_b={ev_b} b_in_b={b_in_b} b_in_a={b_in_a}")
    pid = S["proposals"].get("model")
    c, _o = await api(U_MKB, "GET", f"{_PR}/{pid}")
    c2, _o2 = await api(U_MKB, "GET", f"{_X}/instructions/{(await ids_of(conn, pid) or [None])[0]}")
    check(c in (HTTP_NOT_FOUND, HTTP_FORBIDDEN) and c2 in (HTTP_NOT_FOUND, HTTP_FORBIDDEN),
          "org B's maker cannot open org A's proposal or its instruction (the FIX exchange with it)", f"{c} {c2}")
    c3, _o3 = await api(U_MKB, "GET", cpu("other", "/fix/messages"))
    check(c3 == HTTP_FORBIDDEN, "the FIX message log is Super Admin only (org B's maker: 403)", f"{c3}")


async def y_timing(conn):
    section("[Y] Timing: the approval request returns BEFORE the automatic step starts, well under the client timeout; "
            "the message-log list has no N+1")
    t = S.get("timing") or {}
    gated = [x for x in t.get("timings") or [] if x.get("gated")]
    check(bool(gated) and all(x["response_finished_at"] is not None and x["started_at"] >= x["response_finished_at"]
                              for x in gated),
          "every automatic step scheduled inside a request started only after that request's response was sent "
          "(the after-response gate)", f"{gated[:2]}")
    check(t.get("code") == HTTP_OK and t.get("secs", 99) < CLIENT_TIMEOUT_S / 3,
          f"the approval request answered in {t.get('secs', 0):.2f} s — well under the {CLIENT_TIMEOUT_S:.0f} s client "
          "timeout (it schedules, it does not wait)", f"{t.get('secs')}")
    sess = await _session()
    for i in range(N_PLUS_ONE_ROWS // 2):
        await FC.send(sess, "H", [(11, f"cp5v-np1-{i}")], transport=APP_T)
    n = await sim_count(conn, "fix_messages")
    t0 = time.monotonic()
    code, out = await api(U_SA, "GET", cpu("main", "/fix/messages") + "?limit=1000")
    secs = time.monotonic() - t0
    from cpsim import fix as CF

    one_query = inspect.getsource(CF.list_messages).count("conn.fetch") == 1
    check(code == HTTP_OK and len(out.get("rows") or []) == min(n, 1000) and secs < N_PLUS_ONE_MAX_S and one_query,
          f"the FIX message log ({n} rows) answers in {secs:.2f} s with ONE query (no per-row reads)",
          f"{code} {secs} one_query={one_query}")


async def y_unchanged(conn):
    section("[Y] Unchanged: the real `fix` router NotConnected; real capability unknown; no loader applied; no network")
    fix = XR.registry().get("fix")
    check(isinstance(fix, XR.NotConnectedRouter) and XR.route_refusal("fix") is not None and
          isinstance(XR.registry().get("fix_sim"), XR.SimulatedFixRouter) and XR.simulated_router() is not None,
          "the real 'fix' router is still a NotConnectedRouter; 'fix_sim' is the SimulatedFixRouter (connected, "
          "simulated)", f"{type(fix).__name__}")
    real = await CC.capability(conn, "altruist", "equity_trading_api")
    check(real.value == "unknown", "real Altruist equity_trading_api is still 'unknown'", f"{real.value}")
    n = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = "
                                 "'altruist_simulator'", fetch="fetchval"))
    check(n == 0, "no capability loader applied (no altruist_simulator row)", f"{n}")
    try:
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c:
                await FC.session(c, org_id=str(ORG_A), environment="production")
        check(False, "the FIX client refuses a real environment before any request")
    except AO.AltruistOAuthError as exc:
        check(FC.NO_CONTRACT in str(exc), "the FIX client refuses sandbox / production before any request", str(exc))


async def y_conformance(conn):
    section("[Y] Conformance: the fix group passes, labelled 'simulated FIX — no Altruist FIX contract seen'")
    code, out, err = run_cmd([sys.executable, str(CONFORMANCE), "--target", "simulator"], REPO, timeout=1500)
    frows = [ln for ln in out.splitlines() if re.match(r"^F\d\d\s", ln)]
    fails = [ln for ln in frows if " FAIL " in ln]
    check(code == 0 and len(frows) >= 20 and not fails and all("simulated FIX" in ln for ln in frows) and
          re.search(r"CONFORMANCE .*: \d+ PASS, 0 FAIL", out) is not None,
          f"the in-process conformance run: {len(frows)} fix rows, all PASS, all labelled simulated FIX",
          f"{code} {fails[:3]} {(out + err)[-300:]}")


async def y_safety(conn):
    section("[Y] Boundary check; app role cannot read cpsim.*; cpsim_app privileges as documented; no secret printed")
    code, out, err = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=300)
    check(code == 0 and "0 violation" in out, "cpsim_boundary_check passes", f"{code} {(out + err)[-300:]}")
    for t in ("fix_messages", "fix_allocations", "orders"):
        try:
            await platform(conn, f"SELECT count(*) FROM cpsim.{t}", fetch="fetchval")
            denied = False
        except asyncpg.InsufficientPrivilegeError:
            denied = True
        check(denied, f"the app role cannot read cpsim.{t} (permission denied)")
    usage = await platform(conn, "SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')", fetch="fetchval")
    check(usage is False, "app_service has no USAGE on schema cpsim")
    leaks = [x for b in S["bodies"] for x in secret_leaks(b)]
    check(not leaks, "no response body carried a simulator key, token or webhook secret", f"{leaks[:3]}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py and visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=900,
                                 env={**os.environ, "AUTHZ_TEST_MODE": "1"})
        check(code == 0 and "CURRENT" in out, f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")


async def y_front_end(conn):
    section("[Y] UI render tests; node --test tests/ passes; npm run build exits 0 (run by this script)")
    NODE_TEST_COUNT = node_test_count()
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) == NODE_TEST_COUNT and
          n.group(1) == p.group(1) and re.search(r"^# fail 0$", o1, re.M) is not None,
          f"the cpsim03 render tests ({NODE_TEST}, counted by their file: instruction page, Orders tab, SIMULATED "
          f"badge) all pass ({n.group(1) if n else '?'}/{NODE_TEST_COUNT})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1800)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regressions — each ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s, its OWN final TOTAL line")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        # VERIFY_REGRESSIONS fast (default): the nested run is --self-checks-only (VT.run_nested appends it)
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


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


async def cleanup_only(conn) -> int:
    # fix 2: per-table fixture counts before / after, and the real-row fingerprints (count + checksum) before / after
    before = await counts(conn)
    print(f"[INFO] fixture rows before cleanup: {sum(before.values())} "
          f"{ {t: n for t, n in sorted(before.items()) if n} }")
    real = await real_fingerprints(conn)
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    after = await counts(conn)
    nonzero = {t: n for t, n in sorted(after.items()) if n}
    check(not nonzero, f"every counted table at zero fixture rows after cleanup ({len(after)} tables)", f"{nonzero}")
    left = await leftovers(conn)
    check(left == 0, "zero fixture rows remain (public + cpsim)", f"{left}")
    now = await real_fingerprints(conn)
    for k in real:
        check(now.get(k) == real[k], f"real {k} unchanged by cleanup (count + checksum)", f"{real[k]} → {now.get(k)}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


FIXTURE_SECTIONS = (("endpoint", y_endpoint), ("auto", y_auto), ("cash", y_cash), ("outcomes", y_outcomes),
                    ("idempotency", y_idempotency), ("system", y_system), ("guard", y_guard), ("blocks", y_blocks),
                    ("unsupported", y_unsupported), ("isolation", y_isolation), ("timing", y_timing),
                    ("unchanged", y_unchanged), ("conformance", y_conformance), ("safety", y_safety))
ONLY_NEEDS = {"endpoint": (), "auto": (), "cash": (), "outcomes": ("auto",), "idempotency": ("auto",), "system": (),
              "guard": (), "blocks": (), "unsupported": (), "isolation": ("auto",), "timing": ("auto",),
              "unchanged": (), "conformance": (), "safety": ()}


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    bad = [x for x in want if x not in ONLY_NEEDS and x != "seed"]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, {', '.join(ONLY_NEEDS)}")
    names = {x for x in want if x != "seed"}
    for x in list(names):
        names |= set(ONLY_NEEDS.get(x, ()))
    return [n for n, _f in FIXTURE_SECTIONS if n in names]


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
        restore_stubs()
        return await cleanup_only(conn)
    if "--self-checks-only" in sys.argv:
        try:
            await run("task 1", y_task1, conn)
            await run("routes", y_routes, conn)
        finally:
            restore_stubs()
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
        bypass = await platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user",
                                fetch="fetchval")
        check(bypass is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
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
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run "
              "(every simulator, orders, admin and webhook call was in-process)",
              f"network: {NETWORK_CALLS[:3]} model: {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows "
                "unchanged; no counterparty, order or book row left")
        await FA.drain()   # cpsim05: no automatic simulated-FIX step is still in flight
        await SW.drain()   # fix 1: no scheduled order webhook is still in flight when the stubs / pools go away
        restore_stubs()
        await close_app_pools()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (orgs, users, grants, accounts, crosswalks, instructions, lots, "
                  "fills, events, approval tasks, sod decisions, to-dos, proposals, blocks, loss-bank entries, positions, "
                  "tax lots, transactions, assets, models, connections, webhook events, settings, capability rows, "
                  "audit rows, fixture securities, every cpsim table incl. fix_messages + fix_allocations)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in sorted(pre):
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
        check(False, "the verify crashed", f"{type(exc).__name__}: {_redact(exc)}")
        print(total_line())
        rc = 1
    sys.exit(rc)
