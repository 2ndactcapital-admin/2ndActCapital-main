"""verify_cpsim03 — CPSIM03: the simulated custodian TRADES. An APPROVED execution instruction is sent by a PERSON
("Send to custodian") to the simulated Altruist-compatible orders API (contract §8, every field ASSUMED); the simulator
answers with its scripted outcome (accept at the last platform price / limit / reject / partial / delay / no price /
unsupported instrument / insufficient position), moves its book for fills, reports executions and order webhooks; a
PERSON imports the executions through the EXISTING enter_fill and reconciles them (custodian_data) against the
transactions the next cpsim02 sync brings in. Routing is D2 all-or-manual (each condition removed in turn); the real
Altruist route never reaches the simulator; governance is the unchanged maker-checker; nothing unattended can send;
cancel both ways; org isolation both directions; the demo-household rule in a NON-test org; blocks / the FIX router /
the real capability / the un-applied loader unchanged; the conformance orders group; the UI render tests.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim03.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback) and
exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx network
transport refuses and RECORDS the URL (a call that would leave the process is a [FAIL] naming it). The simulator, the
app API and the webhook receiver all run IN-PROCESS through ASGI transports against the real ``main.app`` (which mounts
the simulator): the client's simulator transport (``altruist_oauth.set_simulator_transport`` — every orders call goes
through ``_transport_for``, so the transport is threaded on EVERY call) and the simulator's webhook transport
(``cpsim.webhooks.set_transport``). No model call (the three helpers refuse and record).

FIXTURE SECRETS are generated in-process and never printed: the counterparties' keys come from the admin create call
(shown once); ALTRUIST_SIMULATOR_WEBHOOK_SECRET = cpsim_whsec_ + random; ALTRUIST_WEBHOOK_SECRET is replaced IN THIS
PROCESS by a random value; CPSIM_BASE_URL is https://cpsim.verify3.invalid (never resolved).

TIME IS INJECTED: the counterparty clock (cpsim.registry.set_today) starts Friday 2026-11-13 (D0) and the admin
"advance" moves it to Monday 2026-11-16 (D1); the simulator's wall clock (cpsim.oauth.set_now — executions'
executed_at) is the real time minus ten minutes, so enter_fill's "not in the future" rule holds wherever the run happens.
Prices: the fixture equity closes 52.00 on D0 and 50.00 on D1 (a limit buy at 51 is working on D0, filled on D1).

FIXTURES (ids 99000000-0000-0000-0000-0000c953…; sign-in tag cpsim03verify): TEST orgs A and B inside the reserved
prefix (is_test asserted); the NON-test org R (outside the prefix) exists ONLY inside one ROLLED-BACK transaction (the
demo-household decision). Users with real user_roles grants, each decided by the app (asserted): U_SA Super Admin,
U_MK maker (manage + view execution, custody connections), U_CK checker (approve + view execution), U_SD sender
(manage + view execution), U_RD reader (view_execution only), U_MKB / U_CKB maker / checker of org B. Counterparties carry the reserved
slug prefix ``cpsim3verify-``. Fixture securities 'CPSIM03 VERIFY …' with identifiers CPSIM03… / CP3V… and their
platform prices (provider_key cpsim03verify) — asserted to match no real row; named in the cleanup plan by fixture id.
The capability rows use the FIXTURE custodian code ``zz_cpsim3v_sim`` (services.custodian_execution
.SIMULATOR_CAPABILITY_CUSTODIAN substituted for the run, restored at the end); the real ``altruist_simulator`` code
must have NO row (the loader is not applied).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by this run's fixture ids with every
teardown GUC a guard reads (``execution.teardown`` — instructions / lots / fills / events / capabilities; ``sod.teardown``
always; ``cpsim.allow_teardown`` — the upload log and the order tables). Every touched table re-read for its exact
pre-test count BY FIXTURE IDS ONLY (audit_log, sod_decisions and webhook events by fixture ids). Real rows — every
non-fixture counterparty / order / book, real instructions, real capability rows, the cpsim settings of real orgs,
demo-tagged entities, real webhook events — fingerprinted before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<routing|governance|outcomes|cancel|clock|book|fills|webhooks|isolation|unchanged|conformance|safety>: the seed,
the named sections (and what they need), the full teardown. With VERIFY_NESTED=1 the regressions print one
'[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import hmac
import inspect
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
import traceback
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

SPRINT_KEY = "cpsim03"
BEFORE_REF = "594b82d"   # cpsim03-work's base: 'merge cpsim02'
_P = "/api/v1/admin/counterparties"
_X = "/api/v1/execution"
_I = _X + "/instructions/{instruction_id}"
ROUTES = {("POST", _I + "/send-to-custodian"): "manage_execution",
          ("POST", _I + "/import-fills"): "manage_execution",
          ("GET", _I + "/custodian-order"): "view_execution",
          ("GET", _P + "/{counterparty_id}/orders"): "super_admin"}
SIM_ROUTES = {("POST", "/cpsim/c/{slug}/v2/orders"), ("GET", "/cpsim/c/{slug}/v2/orders"),
              ("GET", "/cpsim/c/{slug}/v2/orders/{order_id}"), ("DELETE", "/cpsim/c/{slug}/v2/orders/{order_id}")}
NODE_TEST = "tests/cpsim03/cpsim03.test.mjs"
NODE_TEST_COUNT = 10
NOTES = REPO / "docs" / "cpsim" / "CPSIM03_NOTES.md"
CONTRACT = REPO / "docs" / "cpsim" / "ALTRUIST_CONTRACT.md"
MIGRATION = REPO / "docs" / "cpsim03_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
BOUNDARY = HERE / "cpsim_boundary_check.py"
CONFORMANCE = HERE / "cpsim_altruist_conformance.py"
LOADER = HERE / "cpsim03_capabilities.py"
REGRESSION_SET = REPO / "scripts" / "regression_set.txt"
REGRESSIONS = ("verify_cpsim02.py", "verify_rep2.py", "verify_authz1.py")
HTTP_OK, HTTP_CREATED, HTTP_FORBIDDEN, HTTP_NOT_FOUND, HTTP_CONFLICT, HTTP_UNPROCESSABLE = 200, 201, 403, 404, 409, 422
REFUSED_EXIT = 2
SECTION_TIMEOUT = 600   # every section must finish in < 10 minutes


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cpsim03's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cpsim03 changed no existing route's gate and removed none (mark sent / fills / reconcile / cancel keep "
          "theirs)", f"{o.where()}; changed={sorted(changed)[:3]} removed={o.removed[:3]}")
    mine = flat(o.new)
    want = set(ROUTES)
    check(mine == want, f"this sprint's own routes are exactly its {len(ROUTES)} routes (send, import, custodian order, "
          "admin orders)", f"{o.where()}; extra={sorted(mine - want)[:3]} missing={sorted(want - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items()
           if k not in by or g not in str(by[k].get("after"))]
    check(not bad, "each own route names exactly its gate at cpsim03's own end point (manage_execution / "
          "view_execution / super_admin)", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cpsim03's routes all still exist today with the same gate", f"{drift[:4]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    sim = {(m, r.get("path")) for r in data.get("simulator_routes", []) for m in (r.get("methods") or [])}
    check(SIM_ROUTES <= sim, "the four simulator orders routes are listed in the simulator section of "
          "docs/AUTHZ1_ROUTES (own auth, not app routes)", f"missing={sorted(SIM_ROUTES - sim)}")


def _section8(text: str) -> str:
    i = text.find("## 8. Orders")
    return text[i:] if i >= 0 else ""


def self_static(check, find):
    n = _src("docs/cpsim/CPSIM03_NOTES.md")
    for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11"):
        check(f"| {d} |" in n, f"CPSIM03_NOTES records {d} as built")
    for w in ("custodian_order_id", "custodian_exec_id", "execution_instruction_events_action_chk", "S30 floor",
              "fund", "DRY RUN", "Sandbox / production unchanged"):
        check(w in n, f"CPSIM03_NOTES names {w!r}")
    s8 = _section8(_src("docs/cpsim/ALTRUIST_CONTRACT.md"))
    rows = [ln for ln in s8.splitlines() if ln.startswith("| ") and not ln.startswith("| #") and
            not ln.startswith("| Field") and not ln.startswith("|---")]
    unassumed = [ln[:60] for ln in rows if "ASSUMED" not in ln and "simulator" not in ln]
    check(bool(s8) and "ASSUMED — no published Altruist trading API seen" in s8 and len(rows) >= 20 and not unassumed,
          "ALTRUIST_CONTRACT §8 'Orders (ASSUMED — no published Altruist trading API seen)': every call / field row "
          "is marked ASSUMED", f"{len(rows)} rows; not ASSUMED: {unassumed[:3]}")
    for w in ("POST {api_base}/v2/orders", "GET {api_base}/v2/orders/{order_id}", "DELETE {api_base}/v2/orders/{order_id}",
              "client_order_id", "order.updated", "order.filled", "executions"):
        check(w in s8, f"contract §8 defines {w!r}")
    rs = _src("scripts/regression_set.txt")
    check(bool(re.search(r"^verify_cpsim03\.py\s*$", rs, re.M)), "verify_cpsim03.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    check("cpsim03" in st.lower(), "PROJECT_STATUS records cpsim03")
    adm = _src("docs/cpsim/CPSIM_ADMIN.md")
    check(all(w in adm for w in ("Orders", "cpsim03_capabilities.py --apply", "advance")),
          "CPSIM_ADMIN.md: behaviour → outcomes, the clock and working orders, the Orders tab, the operator step")
    rp = _src("docs/REPLICATION_STUDIO.md")
    check("a person approves, sends and imports" in rp, "REPLICATION_STUDIO.md: the simulator route beside manual")
    tr = _code_only(_src("apps/api/services/custody/trading.py"))
    check("sent_externally: bool = False" in tr and "sent_externally=True" not in tr and "sent_externally = True"
          not in tr, "SubmissionResult.sent_externally is never set True — nothing is sent at submit")


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
from services import altruist_conformance as ACF  # noqa: E402
from services import altruist_oauth as AO  # noqa: E402
from services import altruist_trading as AT  # noqa: E402
from services import altruist_webhook as AW  # noqa: E402
from services import custodian_capabilities as CC  # noqa: E402
from services import custodian_execution as CX  # noqa: E402
from services import execution as X  # noqa: E402
from services import execution_routers as XR  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import portfolio_assets as PA  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cpsim03verify"
NAME = "CPSIM03 VERIFY "
SLUG_PREFIX = "cpsim3verify-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c953"
FIX_CAP = "zz_cpsim3v_sim"
D0 = date(2026, 11, 13)          # the counterparty clock starts here (Friday)
D1 = date(2026, 11, 16)          # after "advance one business day" (Monday)
THU = date(2026, 11, 12)         # the book's first snapshot
SIM_BASE = "https://cpsim.verify3.invalid"
REDIRECT = "https://verify.cpsim03.invalid/altruist/callback"
PROVIDER_KEY = "cpsim03verify"
SIM_XWALK, REAL_XWALK = "ALTRUIST_SIMULATOR", "ALTRUIST"
SETTING, SETTING_CP = "counterparty_sim.allowed", "counterparty_sim.altruist_counterparty"
PRICE_EQ_D0, PRICE_EQ_D1, PRICE_FUND, PRICE_BOND = "52.00", "50.00", "20.00", "99.00"
HELD_EQ, HELD_FUND = Decimal("100"), Decimal("10")
EXECUTE_BY_DAYS = 14


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
ORG_R = UUID("c5130000-0000-4000-8000-0000c9530003")    # NON-test, rolled back only (outside the reserved prefix)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SA, U_MK, U_CK, U_SD, U_RD, U_MKB, U_CKB = _id(10), _id(11), _id(12), _id(13), _id(14), _id(15), _id(16)
ALL_USERS = [U_SA, U_MK, U_CK, U_SD, U_RD, U_MKB, U_CKB]
E_A, E_B = _id(40), _id(41)
E_R_DEMO, E_R_PLAIN = _id(42), _id(43)
H_A, H_B = _id(30), _id(31)
H_R_DEMO, H_R_PLAIN = _id(32), _id(33)
ACC_SIM, ACC_REAL, ACC_NOX, ACC_HELD, ACC_OTHER, ACC_B = _id(50), _id(51), _id(52), _id(53), _id(54), _id(55)
ACC_R_DEMO, ACC_R_PLAIN = _id(56), _id(57)
SEC_EQ, SEC_FUND, SEC_NOPX, SEC_BOND = _id(60), _id(61), _id(62), _id(63)
ASSET_EQ = _id(70)
HOUSEHOLDS = [H_A, H_B]
ACCOUNTS = [ACC_SIM, ACC_REAL, ACC_NOX, ACC_HELD, ACC_OTHER, ACC_B]
SECURITIES = [SEC_EQ, SEC_FUND, SEC_NOPX, SEC_BOND]
EXEC_PERMS = ("view_execution", "manage_execution", "approve_execution")
CUSTODY_PERMS = ("manage_custody_connections", "view_custody_connections")
ROLE_PERMS = {"cpsim03_maker": ("view_execution", "manage_execution") + CUSTODY_PERMS,
              "cpsim03_checker": ("view_execution", "approve_execution"),
              "cpsim03_reader": ("view_execution",)}
USERS = {U_SA: (ORG_A, "super_admin", "sa", "super_admin"), U_MK: (ORG_A, "member", "mk", "cpsim03_maker"),
         U_CK: (ORG_A, "member", "ck", "cpsim03_checker"), U_SD: (ORG_A, "member", "sd", "cpsim03_maker"),
         U_RD: (ORG_A, "member", "rd", "cpsim03_reader"), U_MKB: (ORG_B, "member", "mkb", "cpsim03_maker"),
         U_CKB: (ORG_B, "member", "ckb", "cpsim03_checker")}
ROLE_EXPECT = {U_SA: {"org": ORG_A, "roles": ["super_admin"]},
               U_MK: {"org": ORG_A, "roles": ["cpsim03_maker"], "has": ["manage_execution", "view_execution"],
                      "lacks": ["approve_execution"]},
               U_CK: {"org": ORG_A, "roles": ["cpsim03_checker"], "has": ["approve_execution"],
                      "lacks": ["manage_execution"]},
               U_SD: {"org": ORG_A, "roles": ["cpsim03_maker"], "has": ["manage_execution"],
                      "lacks": ["approve_execution"]},
               U_RD: {"org": ORG_A, "roles": ["cpsim03_reader"], "has": ["view_execution"],
                      "lacks": ["manage_execution", "approve_execution"]},
               U_MKB: {"org": ORG_B, "roles": ["cpsim03_maker"], "has": ["manage_execution"]},
               U_CKB: {"org": ORG_B, "roles": ["cpsim03_checker"], "has": ["approve_execution"]}}
# the book's external ids (text, compared as text)
A1, A4, A_BOTH = "cp3v-acct-1", "cp3v-acct-4", "cp3v-acct-both"
HH1, HH4 = "cp3v-hh-1", "cp3v-hh-4"
CUSIP_EQ, TICKER_EQ = "CPSIM03E1", "CP3VEQ"
CUSIP_FUND, TICKER_FUND = "CPSIM03F1", "CP3VFD"
TICKER_NOPX, TICKER_BOND = "CP3VNP", "CP3VBD"
REAL_EXT = "cp3v-real-1"
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
    ("public.execution_instructions", "execution_instructions_if_not_filled_chk", ("ask",)),
    ("public.execution_instruction_events", "execution_instruction_events_action_chk",
     ("custodian_reject", "custodian_cancel", "mark_sent", "fill")),
    ("cpsim.orders", "orders_status_chk", ("accepted", "working", "partially_filled", "filled", "rejected",
                                           "cancelled")),
    ("cpsim.orders", "orders_outcome_chk", ("accept", "reject", "partial", "delay")),
    ("cpsim.book_positions", "book_positions_price_source_chk", ("platform_price",)),
    ("cpsim.book_transactions", "book_transactions_type_chk", ("buy", "sell")),
)
UNIQUES = ("orders_client_order_live_uq", "orders_order_id_live_uq", "order_executions_exec_uq",
           "execution_fills_custodian_exec_uq", "external_references_org_source_ext_type_key",
           "altruist_connections_active_unique", "uq_sec_global_ident_issuer_assigned", "uq_sec_global_price_point",
           "org_settings_org_id_setting_key_key", "book_positions_live_uq", "book_transactions_live_uq")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "tags"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id", "is_held_away"),
    "portfolio.external_references": ("org_id", "source_system", "external_id", "record_type", "record_id"),
    "portfolio.securities_global": ("id", "name", "security_type", "currency_code"),
    "portfolio.securities_global_identifiers": ("global_security_id", "id_type", "id_value", "is_primary"),
    "portfolio.securities_global_prices": ("global_security_id", "price_date", "price", "currency_code", "price_type",
                                           "source", "provider_key"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category", "is_public"),
}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.entities", "public.households",
                  "public.household_memberships", "public.accounts", "public.org_settings", "public.audit_log",
                  "public.altruist_connections", "public.altruist_oauth_states", "public.altruist_webhook_events",
                  "public.account_import_exceptions", "public.member_todos", "public.sod_decisions",
                  "public.execution_instructions", "public.execution_instruction_lots", "public.execution_fills",
                  "public.execution_instruction_events", "public.custodian_capabilities",
                  "portfolio.external_references", "portfolio.positions", "portfolio.transactions",
                  "portfolio.assets", "portfolio.securities_global", "portfolio.securities_global_identifiers",
                  "portfolio.securities_global_prices")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.counterparty_behaviour",
                "cpsim.counterparty_clock", "cpsim.request_log", "cpsim.book_uploads", "cpsim.book_households",
                "cpsim.book_accounts", "cpsim.book_positions", "cpsim.book_transactions", "cpsim.oauth_codes",
                "cpsim.oauth_tokens", "cpsim.orders", "cpsim.order_executions")
NEW_CPSIM_PRIVS = {"orders": {"SELECT", "INSERT", "UPDATE"}, "order_executions": {"SELECT", "INSERT"}}

S: dict = {"cp": {}, "keys": {}, "client_ids": {}, "issued": [], "ins": {}, "orders": {}, "bodies": []}
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


async def real_fingerprints(conn) -> dict:
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
            conn, "SELECT id, status FROM member_todos WHERE source LIKE 'execution_custodian%' AND NOT "
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
                               gucs=("cpsim.allow_teardown", "execution.teardown"), label="verify_cpsim03")
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
    kw = {"headers": {"User-Agent": "verify_cpsim03", **(headers or {})}}
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
        raise httpx.ConnectError(f"verify_cpsim03: network disabled ({request.url.host})", request=request)

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cpsim03: a model call was attempted ({name})")
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
    os.environ[AO.CPSIM_BASE_URL_ENV] = SIM_BASE
    os.environ["ALTRUIST_REDIRECT_URI"] = REDIRECT
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", "cpsim03verify-" + secrets.token_hex(16))
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
                                evidence_kind="assumed", evidence="cpsim03 verify fixture", as_of=date.today(),
                                actor_id=U_SA, actor_org_id=ORG_A, is_super_admin=True)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Discovery + CPSIM03_NOTES (D1–D11 as built); cpsim02 merged; contract §8 every field ASSUMED")
    code, out, _e = run_cmd(["git", "log", "main", "--oneline"], REPO, timeout=120)
    check(code == 0 and bool(re.search(r"^\w+ merge cpsim02$", out, re.M)), "cpsim02 is merged to main ('merge "
          "cpsim02' in git log main)")
    self_static(check, find)


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


async def y_schema(conn):
    section("[Y] Schema: cpsim orders + executions (cpsim_app only); the minimal public CHECK / column changes (catalog)")
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
        check(privs == want and not others, f"cpsim.{t}: cpsim_app holds exactly {sorted(want)}; no other role",
              f"{sorted(privs)} {[r['rolname'] for r in others]}")
    cons = {}
    for nsp, rel in (("cpsim", "orders"), ("cpsim", "order_executions"), ("cpsim", "book_positions"),
                     ("cpsim", "book_transactions"), ("public", "execution_instructions"),
                     ("public", "execution_fills"), ("public", "execution_instruction_events"),
                     ("public", "custodian_capabilities"), ("public", "trade_blocks"), ("portfolio",
                     "securities_global"), ("portfolio", "securities_global_identifiers"),
                     ("portfolio", "securities_global_prices"), ("portfolio", "external_references")):
        cons[f"{nsp}.{rel}"] = await _constraints(conn, nsp, rel)
    n_read = sum(len(v) for v in cons.values())
    check(n_read > 0 and all(cons[k] for k in ("cpsim.orders", "cpsim.order_executions")),
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
          "the unique indexes the seed and the run rely on exist and are UNIQUE (read before seeding)",
          f"missing {sorted(set(UNIQUES) - set(idx))}")
    ei = cons["public.execution_instructions"]
    g = await platform(conn, "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE "
                             "n.nspname = 'public' AND p.proname = 'execution_instructions_guard'", fetch="fetchval")
    cols = {r["attname"] for r in await platform(
        conn, "SELECT a.attname FROM pg_attribute a WHERE a.attrelid IN ((" + REL + "), (" + REL.replace("$1", "$3")
        .replace("$2", "$4") + ")) AND a.attnum > 0 AND NOT a.attisdropped", "public", "execution_instructions",
        "public", "execution_fills")}
    check({"custodian_order_id", "custodian_exec_id"} <= cols and "custodian_api" in (
        ei.get("execution_instructions_custodian_order_chk") or "") and bool(g and "'custodian_order_id'" in g),
          "public: custodian_order_id (+ its CHECK tying it to custodian_api, + the guard's lifecycle list) and "
          "execution_fills.custodian_exec_id exist", f"{sorted(cols & {'custodian_order_id', 'custodian_exec_id'})}")
    ev = cons["public.execution_instruction_events"].get("execution_instruction_events_action_chk") or ""
    old14 = ("create", "edit", "submit", "approve", "reject", "mark_sent", "fill", "reconcile", "close", "cancel",
             "expire", "carry", "ask", "letter")
    check(all(f"'{a}'" in ev for a in old14) and ev.count("'::text") == 16,
          "the events action CHECK keeps rep2's 14 actions and adds exactly custodian_reject / custodian_cancel")
    check("sent_channel" not in " ".join(d for k, d in ei.items() if k != "execution_instructions_sent_chk" and
                                         k != "execution_instructions_custodian_order_chk"),
          "no other CHECK names sent_channel (the new channel needed no CHECK change)")
    check(cons["public.trade_blocks"].get("trade_blocks_route_chk") == "CHECK ((route = 'manual'::text))",
          "trade_blocks_route_chk unchanged: blocks route manual only")
    pols = await platform(conn, "SELECT tablename, cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                                "ANY($1::text[])", ["execution_instructions", "execution_fills",
                                                    "execution_instruction_events"])
    check(len(pols) == 12 and {(r["tablename"], r["cmd"]) for r in pols} == {
        (t, c) for t in ("execution_instructions", "execution_fills", "execution_instruction_events")
        for c in ("SELECT", "INSERT", "UPDATE", "DELETE")}, "RLS: a policy for every operation on the three execution "
          "tables, unchanged (4 each)", f"{len(pols)}")
    snap = _snapshot_columns()
    missing = []
    for t, cols_ in SEED_COLUMNS.items():
        key = t if "." in t else t
        have = snap.get(key) or snap.get(t.split(".")[-1]) or set()
        missing += [f"{t}.{c}" for c in cols_ if c not in have]
    check(not missing, "every INSERT's columns appear in docs/schema_snapshot.sql", f"{missing[:5]}")
    check(bool(snap.get("cpsim.orders")) and "custodian_order_id" in (snap.get("execution_instructions") or set()),
          "the snapshot was refreshed (cpsim.orders and execution_instructions.custodian_order_id are in it)")
    S["schema_ok"] = read == 2 and n_read > 0


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
                 "the reserved prefixes (fixture ids, the out-of-prefix rolled-back org, the sign-in tag, 'CPSIM03 "
                 "VERIFY' securities, their identifiers and price provider, the 'cpsim3verify-' slug, the fixture "
                 "custodian code) match no real row",
                 f"orgs={o} users={u} securities={s_} identifiers={i_} prices={p_} counterparties={c} caps={k}")


def book(key: str) -> dict:
    if key == "other":
        return {"households": [{"household_id": HH4, "name": "Verify household four"}],
                "accounts": [{"account_id": A4, "household_id": HH4, "name": "Verify org B account"}],
                "positions": [{"position_id": "cp3v-p-a4", "account_id": A4, "as_of": THU.isoformat(),
                               "cusip": CUSIP_EQ, "quantity": "5", "market_value": "260.00", "currency_code": "USD"}],
                "transactions": []}
    return {
        "households": [{"household_id": HH1, "name": "Verify household one"}],
        "accounts": [{"account_id": A1, "household_id": HH1, "name": "Verify brokerage one"},
                     {"account_id": A_BOTH, "household_id": HH1, "name": "Verify crosswalked both ways"}],
        "positions": [
            {"position_id": "cp3v-p-eq", "account_id": A1, "as_of": THU.isoformat(), "cusip": CUSIP_EQ,
             "quantity": str(HELD_EQ), "market_value": "5200.00", "currency_code": "USD"},
            {"position_id": "cp3v-p-fd", "account_id": A1, "as_of": THU.isoformat(), "cusip": CUSIP_FUND,
             "quantity": str(HELD_FUND), "market_value": "200.00", "currency_code": "USD"},
            {"position_id": "cp3v-p-cash", "account_id": A1, "as_of": THU.isoformat(), "row_type": "cash",
             "name": "Cash", "market_value": "1000.00", "currency_code": "USD"}],
        "transactions": [
            {"transaction_id": "cp3v-t-fd-buy", "account_id": A1, "type": "buy", "trade_date": THU.isoformat(),
             "settle_date": D0.isoformat(), "cusip": CUSIP_FUND, "quantity": "10", "price": "20",
             "gross_amount": "200", "net_amount": "200"}],
    }


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


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CPSIM03 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set(EXEC_PERMS + CUSTODY_PERMS) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in (ORG_A, ORG_B):
            for rname, perms in ROLE_PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CPSIM03 verify — {rname}")
                for pn in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CPSIM03 {short.upper()}", sub_of(uid), account_role)
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
                (ACC_OTHER, ORG_A, E_A, H_A, 5, "ZZ_CPSIM3V_OTHER", False), (ACC_B, ORG_B, E_B, H_B, 6, "ALTRUIST",
                                                                            False)):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_held_away) VALUES ($1, $2, $3, $4, "
                "$5, 'individual', 'taxable', $6, $7, $8)", acc, org, f"****{n:04d}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), cust, eid, hh, held)
        for org, src, ext, acc in ((ORG_A, SIM_XWALK, A1, ACC_SIM), (ORG_A, REAL_XWALK, REAL_EXT, ACC_REAL),
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
        for sid, d, px in ((SEC_EQ, D0, PRICE_EQ_D0), (SEC_EQ, D1, PRICE_EQ_D1), (SEC_FUND, THU, PRICE_FUND),
                           (SEC_BOND, D0, PRICE_BOND)):
            await conn.execute("INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                               "currency_code, price_type, source, provider_key) VALUES ($1, $2, $3::numeric, 'USD', "
                               "'close', 'cpsim03 verify', $4)", sid, d, px, PROVIDER_KEY)
    # the tenant asset the sync matches (by ticker) — LINKED to the security master, so reconcile's same-security rule
    # can hold (the sync itself creates unlinked assets; CPSIM03_NOTES §4)
    aid = await PA.create_asset(conn, org_id=str(ORG_A), name=NAME + "EQ asset", asset_type="equity",
                                global_security_id=str(SEC_EQ), currency_code="USD")
    await PA.add_identifier(conn, org_id=str(ORG_A), asset_id=aid, id_type="ticker", id_value=TICKER_EQ,
                            is_primary=True)
    S["asset_eq"] = aid
    await set_cap(conn, "yes")
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS), "fixture orgs A, B and their users are "
                    "is_test = true (privileged insert path; the reserved prefix)", f"{len(os_)} {len(us)}")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT, "(Super Admin, maker, checker, sender, "
                                                                       "reader without manage_execution, B's maker)")
    ok_cp = True
    for key in ("main", "other"):
        code, b = await api(U_SA, "POST", _P, {"name": NAME + key, "institution_type": "altruist",
                                               "slug": SLUG_PREFIX + key})
        issued = (b or {}).get("issued") or {}
        if code != HTTP_OK or not issued.get("api_key", "").startswith("cpsim_live_"):
            ok_cp = False
            continue
        S["cp"][key], S["keys"][key] = b["id"], issued["api_key"]
        S["issued"].append(issued["api_key"])
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
    check(ok_cp and ok_pt and ok_ptb and ok_conn and cap.value == "yes",
          "two fixture counterparties (main for org A, other for org B) created, activated and given their books "
          "through the admin API; A's simulator connected through the EXISTING /altruist/connect; the fixture "
          "capability equity_trading_api = 'yes'", f"cp={ok_cp} pointA={ok_pt} pointB={ok_ptb} conn={cc} "
                                                     f"{dumps(oc)[:200]} cap={cap.value}")
    S["seeded"] = bool(ok_test and ok_roles and ok_cp and ok_pt and ok_ptb and ok_conn and cap.value == "yes")


# ── routing (D2 / D4) ───────────────────────────────────────────────────────────────────────────────────────────
async def y_routing(conn):
    section("[Y] Routing: D2 all-or-manual with reasons; real Altruist never to the simulator; rep7 / rep7b unchanged")
    before = int(await sim(conn, "SELECT count(*) FROM cpsim.orders WHERE counterparty_id = $1::uuid",
                           S["cp"]["main"], fetch="fetchval"))
    code, out = await route(U_MK, trade_list(ACC_SIM))
    r = out.get("routing") or {}
    iid = (r.get("instruction_ids") or [None])[0]
    row = await instruction(conn, iid) if iid else {}
    after = int(await sim(conn, "SELECT count(*) FROM cpsim.orders WHERE counterparty_id = $1::uuid",
                          S["cp"]["main"], fetch="fetchval"))
    check(code == HTTP_CREATED and r.get("routed_to") == CX.ROUTE and (r.get("simulator") or {}).get("route") is True
          and r.get("sent_externally") is False and row.get("routed_via") == CX.ROUTE and row.get("status") == "draft"
          and SLUG_PREFIX + "main" in (row.get("routing_reason") or "") and after == before,
          "every D2 condition present → the SAME draft instruction, routed_via custodian_api with the counterparty "
          "named; nothing sent at submit (no order at the simulator, sent_externally false)",
          f"{code} {dumps(r)[:300]} {row.get('routed_via')} orders {before}→{after}")
    S["ins"]["routed"] = iid

    async def manual(uid, acc, label, condition, body=None):
        c, o = await route(uid, body or trade_list(acc))
        rr = o.get("routing") or {}
        rid = (rr.get("instruction_ids") or [None])[0]
        rw = await instruction(conn, rid) if rid else {}
        sim_ = rr.get("simulator") or {}
        ok = c == HTTP_CREATED and rr.get("routed_to") == "manual" and rw.get("routed_via") != CX.ROUTE and \
            (condition is None or sim_.get("condition") == condition)
        check(bool(ok), f"{label} → manual" + (f" with the reason (condition {condition}: "
                                              f"{(sim_.get('reason') or '')[:60]}…)" if condition else ""),
              f"{c} {dumps(rr)[:300]}")
        return rr

    rr = await manual(U_MK, ACC_REAL, "a REAL ALTRUIST-crosswalked account (also crosswalked to the simulator)",
                      "real_crosswalk")
    check("'unknown'" in (rr.get("reason") or "") and "equity_trading_api" in (rr.get("reason") or ""),
          "… its reason is the real Altruist capability ('unknown') — the real Altruist route stays manual, never the "
          "simulator", f"{rr.get('reason')}")
    await manual(U_MK, ACC_NOX, "condition 2 removed: an Altruist account with no simulator crosswalk", "crosswalk")
    c_off, _ = await api(U_SA, "PUT", f"{_P}/org-access/{ORG_A}", {"allowed": False})
    await manual(U_MK, ACC_SIM, "condition 3 removed: counterparty_sim.allowed off (identical trade list)", "allowed")
    c_on, _ = await api(U_SA, "PUT", f"{_P}/org-access/{ORG_A}", {"allowed": True})
    check(c_off == HTTP_OK and c_on == HTTP_OK, "the allowed flag switched off and back on by the Super Admin")
    await manual(U_MKB, ACC_B, "condition 4 removed: org B (allowed, pointed, crosswalked) has no simulator connection "
                               "yet", "connection")
    await set_cap(conn, "unknown")
    await manual(U_MK, ACC_SIM, "condition 7 removed: the simulator's equity_trading_api 'unknown' (identical trade "
                                "list)", "capability")
    await set_cap(conn, "yes")
    await manual(U_MK, ACC_SIM, "a per-trade rule: an acceptable-range price stays manual (identical account)", "trade",
                 body=trade_list(ACC_SIM, kind="range"))
    # condition 6 (authority × custody, rep7b): route_trade_list decides it BEFORE any adapter
    from services.custody.trading import TradeList, route_trade_list

    try:
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c:
                acct = await X.load_account(c, ORG_A, ACC_SIM)
            res = await route_trade_list(pool, org_id=str(ORG_A), actor_id=str(U_MK),
                                         account={**acct, "authority": "advise", "custody": "integrated"},
                                         trade_list=TradeList(**{**trade_list(ACC_SIM), "executor_type": "member"}))
        check(res.routed_to == "manual" and res.simulator is None and "advise" in res.reason and
              "never a custodian API" in res.reason, "condition 6: an account whose effective authority is advise "
              "(the account route_trade_list decides on) → the rep7b manual reason (never a custodian API), decided "
              "before the simulator is considered", f"{res.as_dict()}")
    except Exception as exc:  # noqa: BLE001
        check(False, "condition 6 (advise) routed", f"{type(exc).__name__}: {exc}")
    c_h, o_h = await route(U_MK, trade_list(ACC_HELD))
    check(c_h == HTTP_CONFLICT, "a held-away account is refused (rep7, unchanged)", f"{c_h} {dumps(o_h)[:200]}")
    rr = await manual(U_MK, ACC_OTHER, "another custodian (no registered trade adapter)", None)
    check("no trade adapter is registered" in (rr.get("reason") or "") and "simulator" not in rr,
          "… with the existing reason, and no simulator decision on a non-Altruist route", f"{rr.get('reason')}")
    try:
        n0 = len(NETWORK_CALLS)
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c:
                await AT.submit_order(c, org_id=str(ORG_A), order={"client_order_id": "x"}, environment="sandbox")
        check(False, "the orders client refuses a real environment before any request", f"{NETWORK_CALLS[n0:]}")
    except AO.AltruistOAuthError as exc:
        check(AT.NO_CONTRACT in str(exc), "the orders client refuses sandbox / production before any request ('no "
              "published Altruist orders contract')", str(exc))


# ── governance ──────────────────────────────────────────────────────────────────────────────────────────────────
async def y_governance(conn):
    section("[Y] Governance: the same maker-checker; Send is a person's action, gated both ways; nothing unattended")
    iid = S["ins"].get("routed")
    if not iid:
        check(False, "governance: the routed draft from the routing section exists")
        return
    orders0 = len(await sim_order(conn, iid))
    c0, o0 = await send(U_SD, iid)
    c1, _ = await api(U_MK, "POST", f"{_X}/instructions/{iid}/submit", {})
    c1s, o1s = await send(U_SD, iid)
    cm, om = await api(U_MK, "POST", f"{_X}/instructions/{iid}/approve", {})
    st_mk = (await instruction(conn, iid)).get("status")
    check(c0 == HTTP_CONFLICT and c1 == HTTP_OK and c1s == HTTP_CONFLICT and cm == HTTP_FORBIDDEN and
          st_mk == "pending_approval", "Send is refused on a draft and while awaiting approval (409); the maker "
          "cannot approve (403, services.sod) — the instruction stays awaiting approval",
          f"{c0} {c1} {c1s} {cm} {st_mk} {dumps(o0)[:120]} {dumps(om)[:120]}")
    sod_refused = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE org_id = $1::uuid AND item_id = "
                                           "$2::text AND outcome = 'refused'", ORG_A, str(iid), fetch="fetchval"))
    check(sod_refused >= 1, "the maker's refused approval is recorded by services.sod (sod_decisions)",
          f"{sod_refused}")
    ca, _ = await api(U_CK, "POST", f"{_X}/instructions/{iid}/approve", {})
    row = await instruction(conn, iid)
    orders1 = len(await sim_order(conn, iid))
    check(ca == HTTP_OK and row.get("status") == "approved" and str(row.get("approved_by")) == str(U_CK) and
          orders0 == 0 and orders1 == 0, "the checker approves → approved; NOTHING was sent at submit or approval "
          "(no order with this client_order_id at the simulator)", f"{ca} {row.get('status')} {orders0} {orders1}")
    cr, orr = await send(U_RD, iid)
    cc, occ = await send(U_CK, iid)
    cb, ob = await api(U_MKB, "POST", f"{_X}/instructions/{iid}/send-to-custodian", {})
    still = (await instruction(conn, iid)).get("status")
    check(cr == HTTP_FORBIDDEN and "manage_execution" in dumps(orr) and cc == HTTP_FORBIDDEN and
          cb == HTTP_NOT_FOUND and still == "approved" and not await sim_order(conn, iid),
          "Send refused without manage_execution (reader, checker: 403 naming it) and for a NON-member of the org "
          "(404) — the identical request; still approved, still no order", f"{cr} {cc} {cb} {still}")
    cman, oman = await api(U_SD, "POST", f"{_X}/instructions/{iid}/sent", {"channel": "custodian_api"})
    check(cman == HTTP_UNPROCESSABLE and (await instruction(conn, iid)).get("status") == "approved",
          "'Mark sent' with channel custodian_api is refused (422) — only Send to custodian records it",
          f"{cman} {dumps(oman)[:200]}")
    code, d = await api(U_SD, "GET", f"{_X}/instructions/{iid}")
    acts = d.get("actions") or []
    code_r, d_r = await api(U_RD, "GET", f"{_X}/instructions/{iid}")
    check(code == HTTP_OK and "send_to_custodian" in acts and "mark_sent" in acts and code_r == HTTP_OK and
          "send_to_custodian" not in (d_r.get("actions") or []) and (d.get("instruction") or {}).get("custodian", {})
          .get("send_available") is True, "the server lists send_to_custodian for a manage_execution holder (beside "
          "the always-offered manual Mark sent) and never for a reader", f"{acts} {d_r.get('actions')}")
    metas = {k: WA.verb_autonomy(type("A", (), {"key": k})()) for k in ("execution.custodian_send",
                                                                        "execution.custodian_import")}
    check(all(m["class"] == WA.NEVER and m.get("trades") is True for m in metas.values()) and
          all(REGISTRY.get(k) is None for k in metas) and
          all(sod.VERB_DUTIES.get(k) == "write" for k in metas),
          "Send / Import are declared NEVER (trades) for unattended runs and are NOT registered as actions — no "
          "workflow, agent or S29b step can reach them", f"{metas}")
    callers = []
    for p in (API_DIR / "services").rglob("*.py"):
        t = _code_only(p.read_text(encoding="utf-8"))
        if re.search(r"\bsend_to_custodian\(|\bimport_fills\(", t) and p.name != "custodian_execution.py":
            callers.append(str(p.relative_to(API_DIR)))
    for p in [API_DIR / "workflow_scheduler_tick.py", *(API_DIR / "routers").glob("*.py")]:
        t = _code_only(p.read_text(encoding="utf-8")) if p.exists() else ""
        if re.search(r"\bCX\.send_to_custodian\(|\bCX\.import_fills\(|custodian_execution\.send_to_custodian",
                     t) and p.name != "execution.py":
            callers.append(str(p.relative_to(API_DIR)))
    keys = [a.key for a in REGISTRY.all() if "custodian" in a.key or "execution." in a.key]
    check(not callers and not keys, "no unattended path can send: the only callers of send / import are the two "
          "person routes in routers/execution.py; no registered action, workflow tick or trigger names them",
          f"callers={callers} actions={keys}")


# ── outcomes (D6) ───────────────────────────────────────────────────────────────────────────────────────────────
async def _send_ok(conn, label, body, *, outcome_expected="sent"):
    iid = await approved(conn, body, label)
    if not iid:
        return None, {}
    c, o = await send(U_SD, iid)
    if c != HTTP_OK or o.get("outcome") != outcome_expected:
        check(False, f"{label}: Send to custodian → {outcome_expected}", f"{c} {dumps(o)[:400]}")
    S["orders"][label] = (o.get("order") or {})
    return iid, o


async def y_outcomes(conn):
    section("[Y] Outcomes: accept / limit / reject / partial / delay / no price / unsupported / insufficient; idempotent")
    await behaviour(default_outcome="accept", webhooks_enabled=False)
    # fix 1: the section's OWN approved instruction (route → submit → approve through the routes) — it never depends on
    # the governance section's leftover (a governance crash once left that one awaiting approval and cascaded here)
    iid = await approved(conn, trade_list(ACC_SIM), "accept")
    if not iid:
        check(False, "outcomes: the section's own approved instruction exists")
        return
    c, o = await send(U_SD, iid)
    order = o.get("order") or {}
    row = await instruction(conn, iid)
    ex = await sim_execs(conn, iid)
    check(c == HTTP_OK and o.get("outcome") == "sent" and order.get("status") == "filled" and len(ex) == 1 and
          dec(ex[0]["price"]) == Decimal(PRICE_EQ_D0) and dec(ex[0]["quantity"]) == Decimal("10") and
          ex[0]["trade_date"] == D0 and row.get("status") == "sent" and row.get("sent_channel") == CX.CHANNEL and
          row.get("custodian_order_id") == order.get("order_id") and str(row.get("sent_by")) == str(U_SD),
          "accept: the APPROVED instruction sent by a person → filled in full at the last PLATFORM price as of the "
          "clock date (exact Decimal 52.00, trade date D0); the instruction is sent through mark_sent with channel "
          "custodian_api, the order id and the sender", f"{c} {dumps(o)[:300]} {[dict(e) for e in ex]}")
    S["ins"]["accept"], S["orders"]["accept"] = iid, order
    mk = await events_of(conn, iid, "mark_sent")
    check(len(mk) == 1 and mk[0]["detail"].get("channel") == CX.CHANNEL and mk[0]["detail"].get("custodian_order_id")
          == order.get("order_id"), "… one mark_sent event (the existing path) naming the channel and the order id")
    # idempotent: the same client_order_id re-POSTed directly → the same order, never a second
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as cn:
            sec = await X._security(cn, SEC_EQ)
            body = CX.order_body(row, sec, A1)
            st2, again = await AT.submit_order(cn, org_id=str(ORG_A), order={**body, "quantity": "999"})
    check(st2 == HTTP_OK and again.get("order_id") == order.get("order_id") and len(await sim_order(conn, iid)) == 1,
          "idempotent: a repeat POST with the same client_order_id (another quantity) → 200, the SAME order — one "
          "order", f"{st2} {again.get('order_id')}")
    # limit not marketable: buy at 51 when the D0 price is 52 → working (filled after the clock moves: clock section)
    iid_l, o_l = await _send_ok(conn, "limit", trade_list(ACC_SIM, kind="limit", limit="51"))
    check(bool(iid_l) and (o_l.get("order") or {}).get("status") == "working" and not await sim_execs(conn, iid_l) and
          (await instruction(conn, iid_l)).get("status") == "sent", "limit buy at 51 with the price at 52 → working "
          "(no execution); the instruction is sent", f"{dumps(o_l)[:300]}")
    # reject
    await behaviour(default_outcome="reject", reject_reason=REJECT_REASON)
    iid_r, o_r = await _send_ok(conn, "reject", trade_list(ACC_SIM, quantity="3"), outcome_expected="rejected")
    rrow = await instruction(conn, iid_r) if iid_r else {}
    rev = await events_of(conn, iid_r, "custodian_reject") if iid_r else []
    todo = await platform(conn, "SELECT user_id, status FROM member_todos WHERE org_id = $1::uuid AND source = $2 AND "
                                "related_id = $3::uuid", ORG_A, CX.TODO_SOURCE_REJECT, iid_r) if iid_r else []
    check(bool(iid_r) and rrow.get("status") == "approved" and rrow.get("custodian_order_id") is None and len(rev) == 1
          and rev[0]["detail"].get("reason") == REJECT_REASON and str(rev[0]["actor_id"]) == str(U_SD) and
          len(todo) == 1 and str(todo[0]["user_id"]) == str(U_SD) and todo[0]["status"] == "open",
          "reject: the instruction STAYS approved; the reason is a custodian_reject event (by the sender); one open "
          "to-do for the sender", f"{rrow.get('status')} {rev} {[dict(t) for t in todo]}")
    c_again, o_again = await send(U_SD, iid_r) if iid_r else (0, {})
    check(c_again == HTTP_OK and (o_again.get("order") or {}).get("order_id") == (o_r.get("order") or {}).get(
        "order_id") and len(await sim_order(conn, iid_r)) == 1, "a second Send of the refused instruction re-POSTs "
          "the same client_order_id → the SAME (rejected) order, never a second", f"{c_again}")
    # partial
    await behaviour(default_outcome="partial", partial_pct=PARTIAL_PCT)
    iid_p, o_p = await _send_ok(conn, "partial", trade_list(ACC_SIM, quantity="10"))
    exp = (Decimal("10") * PARTIAL_PCT / 100).to_integral_value(rounding="ROUND_DOWN")
    pex = await sim_execs(conn, iid_p) if iid_p else []
    check(bool(iid_p) and (o_p.get("order") or {}).get("status") == "partially_filled" and len(pex) == 1 and
          dec(pex[0]["quantity"]) == exp, f"partial {PARTIAL_PCT}%: one execution of {exp} (rounded down to a whole "
          "share), the remainder working", f"{dumps(o_p)[:300]}")
    ci, oi = await import_fills(U_SD, iid_p) if iid_p else (0, {})
    prow = await instruction(conn, iid_p) if iid_p else {}
    check(ci == HTTP_OK and prow.get("status") == "partially_filled" and len(oi.get("booked") or []) == 1,
          "… after Import fills the instruction is partially filled; the remainder still works at the custodian",
          f"{ci} {prow.get('status')} {dumps(oi)[:200]}")
    # delay
    await behaviour(default_outcome="delay", delay_seconds=0)
    iid_d, o_d = await _send_ok(conn, "delay", trade_list(ACC_SIM, quantity="5"))
    check(bool(iid_d) and (o_d.get("order") or {}).get("status") == "working" and not await sim_execs(conn, iid_d),
          "delay: working until the clock advances (no execution yet)", f"{dumps(o_d)[:300]}")
    # rejections the custodian makes regardless of the scripted outcome
    await behaviour(default_outcome="accept")
    for label, body, reason in (
            ("no_price", trade_list(ACC_SIM, SEC_NOPX, quantity="1"), "no price"),
            ("unsupported", trade_list(ACC_SIM, SEC_BOND, quantity="1"), "unsupported instrument"),
            ("insufficient", trade_list(ACC_SIM, SEC_FUND, side="sell", quantity=str(HELD_FUND + 1000)),
             "insufficient position")):
        iid_x, o_x = await _send_ok(conn, label, body, outcome_expected="rejected")
        xr = await instruction(conn, iid_x) if iid_x else {}
        check(bool(iid_x) and o_x.get("reason") == reason and xr.get("status") == "approved" and
              not await sim_execs(conn, iid_x), f"{label.replace('_', ' ')} → rejected '{reason}', the instruction "
              "stays approved, no execution", f"{dumps(o_x)[:300]}")
    # filled at the custodian but NOT imported (the cancel section's already-filled case)
    iid_f, o_f = await _send_ok(conn, "filled_not_imported", trade_list(ACC_SIM, quantity="2"))
    check(bool(iid_f) and (o_f.get("order") or {}).get("status") == "filled", "an accepted order filled at the "
          "custodian, not yet imported (kept for the cancel section)")


# ── cancel (D11) ────────────────────────────────────────────────────────────────────────────────────────────────
async def y_cancel(conn):
    section("[Y] Cancel both ways: a working order is cancelled at the custodian first; a filled one refuses")
    await behaviour(default_outcome="accept")
    iid, o = await _send_ok(conn, "cancel_working", trade_list(ACC_SIM, kind="limit", limit="1.00", quantity="4"))
    if iid:
        c, oc = await api(U_SD, "POST", f"{_X}/instructions/{iid}/cancel", {"reason": "verify: no longer wanted"})
        so = await sim_order(conn, iid)
        row = await instruction(conn, iid)
        check(c == HTTP_OK and so and so[0]["status"] == "cancelled" and row.get("status") == "cancelled",
              "a WORKING custodian order: cancel → DELETE at the simulator first (order cancelled) and the "
              "instruction cancelled", f"{c} {[r['status'] for r in so]} {row.get('status')} {dumps(oc)[:200]}")
    iid_f = S["ins"].get("filled_not_imported")
    if iid_f:
        c, oc = await api(U_SD, "POST", f"{_X}/instructions/{iid_f}/cancel", {"reason": "verify: too late"})
        row = await instruction(conn, iid_f)
        check(c == HTTP_CONFLICT and "filled" in dumps(oc) and row.get("status") == "sent",
              "an order already FILLED at the custodian: cancel refused (409) with the reason; the instruction stays "
              "sent", f"{c} {dumps(oc)[:300]} {row.get('status')}")
    iid_p = S["ins"].get("partial")
    if iid_p:
        c1, _ = await api(U_SD, "POST", f"{_X}/instructions/{iid_p}/cancel", {"reason": "verify: stop the rest"})
        so = await sim_order(conn, iid_p)
        r1 = await instruction(conn, iid_p)
        cev = await events_of(conn, iid_p, "custodian_cancel")
        c2, _ = await api(U_SD, "POST", f"{_X}/instructions/{iid_p}/cancel", {"reason": "verify: close it"})
        r2 = await instruction(conn, iid_p)
        check(c1 == HTTP_OK and so and so[0]["status"] == "cancelled" and r1.get("status") == "partially_filled" and
              len(cev) == 1 and c2 == HTTP_OK and r2.get("status") == "cancelled",
              "a cancelled order WITH executions leaves the instruction partially filled (a custodian_cancel event); "
              "the existing cancel then closes it", f"{c1} {r1.get('status')} {len(cev)} {c2} {r2.get('status')}")


# ── the clock (working orders re-evaluated) ────────────────────────────────────────────────────────────────────
async def y_clock(conn):
    section("[Y] The clock: advance one business day re-evaluates working orders (delay and the limit fill)")
    c, out = await api(U_SA, "POST", cpu("main", "/clock/advance"))
    filled = {f["client_order_id"]: f for f in (out.get("filled") or [])}
    S["today"] = D1
    il, idl = S["ins"].get("limit"), S["ins"].get("delay")
    el, ed = (await sim_execs(conn, il) if il else []), (await sim_execs(conn, idl) if idl else [])
    check(c == HTTP_OK and out.get("to") == D1.isoformat() and il in filled and idl in filled and len(el) == 1 and
          dec(el[0]["price"]) == Decimal(PRICE_EQ_D1) and el[0]["trade_date"] == D1 and len(ed) == 1 and
          dec(ed[0]["quantity"]) == Decimal("5"), "advance → D1: the limit buy at 51 fills at the new price 50.00 "
          "(≤ limit); the delayed order fills as accept; the response lists what the advance filled",
          f"{c} {dumps(out)[:400]}")
    p = S["ins"].get("partial")
    check(not p or p not in filled, "a partial's remainder is not filled by an advance (it was cancelled / stays "
          "working)")
    for label in ("limit", "delay"):
        iid = S["ins"].get(label)
        if iid:
            ci, oi = await import_fills(U_SD, iid)
            row = await instruction(conn, iid)
            check(ci == HTTP_OK and row.get("status") == "filled", f"{label}: Import fills → filled",
                  f"{ci} {row.get('status')} {dumps(oi)[:200]}")


# ── the book (D7) + the next sync ──────────────────────────────────────────────────────────────────────────────
async def y_book(conn):
    section("[Y] Book moves for fills; the next cpsim02 sync brings the transactions and positions in")
    order = S["orders"].get("accept") or {}
    ex = (order.get("executions") or [{}])[0]
    tx = await sim(conn, "SELECT type, quantity, price, trade_date, settle_date FROM cpsim.book_transactions WHERE "
                         "counterparty_id = $1::uuid AND external_id = $2 AND valid_to IS NULL", S["cp"]["main"],
                   f"sim-x-{ex.get('exec_id')}", fetch="fetchrow")
    check(tx is not None and tx["type"] == "buy" and dec(tx["quantity"]) == Decimal("10") and
          dec(tx["price"]) == Decimal(PRICE_EQ_D0) and tx["trade_date"] == D0 and tx["settle_date"] > D0,
          "the accept fill wrote the book transaction sim-x-<exec_id> (buy 10 @ 52.00, trade D0, settle a later "
          "business day)", f"{dict(tx) if tx else None}")
    q = await sim(conn, "SELECT quantity FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND "
                        "account_external_id = $2 AND security_id = $3::uuid AND valid_to IS NULL AND as_of = (SELECT "
                        "max(as_of) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND account_external_id "
                        "= $2 AND valid_to IS NULL)", S["cp"]["main"], A1, SEC_EQ, fetch="fetchval")
    executed = Decimal(0)
    for label in ("accept", "partial", "limit", "delay", "filled_not_imported"):
        iid = S["ins"].get(label)
        if iid:
            executed += sum((dec(e["quantity"]) for e in await sim_execs(conn, iid)), Decimal(0))
    check(dec(q) == HELD_EQ + executed, "the position moved by exactly the executed quantity (held 100 + every fill)",
          f"{q} vs {HELD_EQ} + {executed}")
    set_sim_creds("main")
    cs, so = await api(U_MK, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    synced = await platform(conn, "SELECT x.id, x.quantity, x.price, x.external_ref, a.global_security_id FROM "
                                  "portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id JOIN "
                                  "portfolio.assets a ON a.id = p.asset_id WHERE x.org_id = $1::uuid AND p.account_id "
                                  "= $2::uuid AND x.external_ref = $3 AND x.system_to IS NULL", ORG_A, ACC_SIM,
                            f"sim-x-{ex.get('exec_id')}")
    S["synced_accept"] = [str(r["id"]) for r in synced]
    check(cs == HTTP_OK and len(synced) == 1 and dec(synced[0]["quantity"]) == Decimal("10") and
          dec(synced[0]["price"]) == Decimal(PRICE_EQ_D0) and str(synced[0]["global_security_id"]) == str(SEC_EQ),
          "the next sync (EXISTING /altruist/sync) brings the fill transaction into org A's account, on the asset "
          "linked to the security (re-read independently)", f"{cs} {dumps(so)[:200]} {[dict(r) for r in synced]}")
    pos = await platform(conn, "SELECT p.quantity FROM portfolio.positions p WHERE p.org_id = $1::uuid AND "
                               "p.account_id = $2::uuid AND p.asset_id = $3::uuid AND p.source_system = "
                               "'altruist_simulator' AND p.valid_to IS NULL AND p.system_to IS NULL", ORG_A, ACC_SIM,
                         S.get("asset_eq"))
    check(any(dec(r["quantity"]) == HELD_EQ + executed for r in pos), "… and the moved position (tagged "
          "altruist_simulator) with the quantity the simulator serves", f"{[dict(r) for r in pos]}")


# ── fills (D8) + reconcile ─────────────────────────────────────────────────────────────────────────────────────
async def y_fills(conn):
    section("[Y] Fills through enter_fill, idempotent, never over; reconcile against the synced transactions")
    iid = S["ins"].get("accept")
    if not iid:
        check(False, "fills: the accepted instruction exists")
        return
    order = S["orders"].get("accept") or {}
    ex = (order.get("executions") or [{}])[0]
    c, o = await import_fills(U_SD, iid)
    f = await fills_of(conn, iid)
    row = await instruction(conn, iid)
    check(c == HTTP_OK and o.get("booked") == [ex.get("exec_id")] and len(f) == 1 and
          f[0]["custodian_exec_id"] == ex.get("exec_id") and str(f[0]["entered_by"]) == str(U_SD) and
          dec(f[0]["price"]) == Decimal(PRICE_EQ_D0) and dec(f[0]["quantity"]) == Decimal("10") and
          row.get("status") == "filled", "Import fills books exactly the new execution through enter_fill: exec id "
          "kept, entered_by the importer, Decimal price / quantity exact → filled", f"{c} {dumps(o)[:300]}")
    fe = await events_of(conn, iid, "fill")
    check(len(fe) == 1 and str(fe[0]["actor_id"]) == str(U_SD), "… one fill event, by the importer")
    c2, o2 = await import_fills(U_SD, iid)
    check(c2 == HTTP_CONFLICT and len(await fills_of(conn, iid)) == 1, "a second import books nothing (the instruction "
          "is filled → refused; the fill count unchanged)", f"{c2}")
    ip = S["ins"].get("filled_not_imported")
    if ip:
        n0 = len(await fills_of(conn, ip))
        try:
            async with org_ctx(ORG_A) as pool:
                await X.enter_fill(pool, org_id=str(ORG_A), instruction_id=ip, actor_id=str(U_SD),
                                   quantity=Decimal("3"), price=Decimal(PRICE_EQ_D0),
                                   filled_at=datetime.now(timezone.utc) - timedelta(minutes=1))
            over = None
        except X.ExecutionError as exc:
            over = str(exc)
        ci, oi = await import_fills(U_SD, ip)
        f = await fills_of(conn, ip)
        check(over is not None and "more than" in over and len(f) == n0 + 1 and dec(f[-1]["quantity"]) == Decimal("2")
              and (await instruction(conn, ip)).get("status") == "filled", "never more than instructed: 3 on a "
              "2-share instruction is refused by enter_fill (existing rule); the import then books exactly the 2 "
              "executed", f"{over} {ci} {dumps(oi)[:200]}")
    # duplicate exec id refused by the unique index even past the app
    try:
        await platform(conn, "INSERT INTO execution_fills (org_id, instruction_id, quantity, price, filled_at, lots, "
                             "completes, entered_by, custodian_exec_id) VALUES ($1::uuid, $2::uuid, 1, 1, now(), "
                             "'[]'::jsonb, false, $3::uuid, $4)", ORG_A, iid, U_SD, ex.get("exec_id"), fetch="execute")
        dup = None
    except asyncpg.PostgresError as exc:
        dup = type(exc).__name__
    check(dup is not None, "the database itself refuses a second fill for the same execution (guard / unique index)",
          f"{dup}")
    cv, view = await api(U_RD, "GET", f"{_X}/instructions/{iid}/custodian-order")
    pre = view.get("preselect_transaction_ids") or []
    check(cv == HTTP_OK and pre == S.get("synced_accept") and view.get("reconcile_reference") == order.get("order_id")
          and all(e.get("booked") is True for e in (view.get("order") or {}).get("executions") or []),
          "the custodian-order view (view_execution) marks the execution booked and preselects the synced "
          "transaction; the reconcile reference is the order id", f"{cv} {dumps(view)[:300]}")
    other = await platform(conn, "SELECT x.id FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = "
                                 "x.position_id WHERE x.org_id = $1::uuid AND p.account_id = $2::uuid AND "
                                 "x.external_ref = 'cp3v-t-fd-buy' AND x.system_to IS NULL", ORG_A, ACC_SIM)
    if other:
        cb, ob = await api(U_SD, "POST", f"{_X}/instructions/{iid}/reconcile",
                           {"method": "custodian_data", "reference": order.get("order_id"),
                            "transaction_ids": [str(other[0]["id"])]})
        check(cb == HTTP_UNPROCESSABLE and (await instruction(conn, iid)).get("status") == "filled",
              "a synced transaction of another security is refused (existing rule: this account's, in this security)",
              f"{cb} {dumps(ob)[:200]}")
    else:
        check(False, "the book's fund transaction was synced (the other-security refusal needs it)")
    cr, orr = await api(U_SD, "POST", f"{_X}/instructions/{iid}/reconcile",
                        {"method": "custodian_data", "reference": order.get("order_id"), "transaction_ids": pre})
    row = await instruction(conn, iid)
    check(cr == HTTP_OK and row.get("status") == "reconciled" and row.get("reconcile_method") == "custodian_data" and
          row.get("reconcile_reference") == order.get("order_id"), "Reconcile (custodian_data, reference = the "
          "custodian order id, the synced transaction) → reconciled — a person, through the existing reconcile",
          f"{cr} {dumps(orr)[:200]}")


# ── webhooks (D9) ───────────────────────────────────────────────────────────────────────────────────────────────
async def y_webhooks(conn):
    section("[Y] Webhooks: order events signed and received; a to-do for the sender; nothing booked by the webhook")
    # fix 1: the section sets every field its outcome needs itself (delay needs delay_seconds) — never inherited
    await behaviour(default_outcome="delay", delay_seconds=0, webhooks_enabled=True)
    iid = await approved(conn, trade_list(ACC_SIM, quantity="1"), "webhook")
    t0 = time.monotonic()
    c_s, o = await send(U_SD, iid) if iid else (0, {})
    took = time.monotonic() - t0
    check(bool(iid) and c_s == HTTP_OK and o.get("outcome") == "sent" and took < AT.TIMEOUT_S / 2,
          f"webhook: with webhooks ON, Send to custodian → sent in {took:.1f} s, well under the client's "
          f"{AT.TIMEOUT_S:.0f} s read timeout — the order response never waits on a webhook (fix 1)",
          f"{c_s} {dumps(o)[:300]}")
    # order.updated is delivered AFTER the response (a detached task): await it, then prove it arrived after the send
    after_send = await SW.drain()
    upd = [d for batch in after_send for d in batch if d.get("event_type") == "order.updated"]
    check(len(upd) == 1 and upd[0].get("status") == HTTP_OK and upd[0].get("outcome") == "delivered",
          "… order.updated was delivered AFTER the send returned (scheduled, not inline), once, 200 from the receiver",
          f"{after_send}")
    # the next clock advance fills the delayed order and the simulator sends order.filled
    ca, oa = await api(U_SA, "POST", cpu("main", "/clock/advance"))
    await SW.drain()
    await behaviour(default_outcome="accept", webhooks_enabled=False)
    evs = await platform(conn, "SELECT event_id, event_type, raw_payload FROM altruist_webhook_events WHERE "
                               "event_type LIKE 'order.%' AND org_id = $1::uuid", ORG_A)
    pay = [(r["event_type"], json.loads(r["raw_payload"]) if isinstance(r["raw_payload"], str) else r["raw_payload"])
           for r in evs]
    mine = [(t, p) for t, p in pay if p.get("client_order_id") == iid]
    S.setdefault("events", []).extend(r["event_id"] for r in evs)
    out_log = await sim(conn, "SELECT status, auth_outcome FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND "
                              "direction = 'out' AND message_type LIKE 'order.%'", S["cp"]["main"])
    check(bool(iid) and ca == HTTP_OK and sorted(t for t, _p in mine) == ["order.filled", "order.updated"] and all(
        p.get("environment") == "simulator" and p.get("order_id") == (o.get("order") or {}).get("order_id")
        for _t, p in mine) and bool(out_log) and all(r["auth_outcome"] == "delivered" for r in out_log),
          "the simulator sent order.updated (at the send) and order.filled (at the advance), signed with the "
          "simulator secret; the receiver verified and recorded both, resolved to org A",
          f"{ca} {[t for t, _p in mine]} {[dict(r) for r in out_log]}")
    todo = await platform(conn, "SELECT user_id, status FROM member_todos WHERE org_id = $1::uuid AND source = $2 AND "
                                "related_id = $3::uuid", ORG_A, CX.TODO_SOURCE_FILLS, iid) if iid else []
    check(len(todo) == 1 and str(todo[0]["user_id"]) == str(U_SD) and todo[0]["status"] == "open",
          "ONE open to-do for the instruction's sender ('fills available — import them')", f"{[dict(t) for t in todo]}")
    check(bool(iid) and not await fills_of(conn, iid) and (await instruction(conn, iid)).get("status") == "sent" and
          len(await sim_execs(conn, iid)) == 1, "nothing booked by the webhook: the execution exists at the simulator, "
          "no fill in Hollisworks, the instruction still sent (a person imports)")
    key = S["real_whsec"]
    body = json.dumps({"event_id": f"{TAG}-real-claims-sim", "event_type": "order.filled", "order_id": "x",
                       "client_order_id": iid, "account_id": A1, "environment": "simulator"},
                      separators=(",", ":")).encode()
    S["events"].append(f"{TAG}-real-claims-sim")
    sig = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://verify") as cl:
        r = await cl.post("/api/v1/altruist/webhook", content=body, headers={AW.SIGNATURE_HEADER: sig,
                                                                           "Content-Type": "application/json"})
    n_todo = len(await platform(conn, "SELECT 1 FROM member_todos WHERE org_id = $1::uuid AND source = $2 AND "
                                      "related_id = $3::uuid", ORG_A, CX.TODO_SOURCE_FILLS, iid))
    check(r.status_code == HTTP_OK and r.json().get("status") == "refused_environment" and n_todo == 1,
          "a REAL-secret order event claiming the simulator is refused (cpsim02 rule) and raises no second to-do",
          f"{r.status_code} {r.text[:200]} to-dos={n_todo}")


# ── isolation ───────────────────────────────────────────────────────────────────────────────────────────────────
async def y_isolation(conn):
    section("[Y] Isolation: org A's orders never visible to or bookable by org B (both directions); demo-household "
            "rule in a NON-test org")
    set_sim_creds("other")
    cc, oc = await api(U_MKB, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
    set_sim_creds("main")
    iid_b = await _approve_b(conn)
    iid_a = S["ins"].get("accept")
    ok = cc == HTTP_OK and iid_b is not None
    if iid_b:
        cs, os_ = await api(U_MKB, "POST", f"{_X}/instructions/{iid_b}/send-to-custodian", {})
        ob = (os_.get("order") or {})
        # fix 1: cpsim.orders is SYSTEM-axis — a status change (accepted → filled) archives a copy, so one order is
        # several rows. Count LIVE rows and DISTINCT order ids over every version (one order, never a second);
        # org A's counterparty must hold NO row of any version.
        so_other = await sim(conn, "SELECT count(*) FILTER (WHERE system_to IS NULL) AS live, count(DISTINCT order_id) "
                                   "AS orders FROM cpsim.orders WHERE counterparty_id = $1::uuid AND client_order_id = $2",
                             S["cp"]["other"], iid_b, fetch="fetchrow")
        so_main = await sim(conn, "SELECT count(*) FROM cpsim.orders WHERE counterparty_id = $1::uuid AND "
                                  "client_order_id = $2", S["cp"]["main"], iid_b, fetch="fetchval")
        check(cs == HTTP_OK and ob.get("status") == "filled" and int(so_other["live"]) == 1 and
              int(so_other["orders"]) == 1 and int(so_main) == 0,
              "org B's instruction goes to ITS counterparty only (other) — one live order, one order id over every "
              "version — never to org A's (no row of any version)", f"{cs} {dict(so_other)} {so_main}")
        S["orders"]["b"] = ob
    for (uid, other_iid, lbl) in ((U_MKB, iid_a, "B on A's"), (U_SD, iid_b, "A on B's")):
        if not other_iid:
            continue
        c1, _ = await api(uid, "GET", f"{_X}/instructions/{other_iid}")
        c2, _ = await api(uid, "GET", f"{_X}/instructions/{other_iid}/custodian-order")
        c3, _ = await api(uid, "POST", f"{_X}/instructions/{other_iid}/import-fills", {})
        c4, _ = await api(uid, "POST", f"{_X}/instructions/{other_iid}/send-to-custodian", {})
        check(c1 == c2 == c3 == c4 == HTTP_NOT_FOUND, f"{lbl} instruction: detail, custodian order, import and send "
              "are 404 (never visible, never bookable)", f"{c1} {c2} {c3} {c4}")
    for (org, oid, lbl) in ((ORG_B, (S["orders"].get("accept") or {}).get("order_id"), "B asks for A's order"),
                            (ORG_A, (S["orders"].get("b") or {}).get("order_id"), "A asks for B's order")):
        if not oid:
            check(False, f"isolation: {lbl} — the order exists")
            continue
        try:
            async with org_ctx(org) as pool:
                async with pool.acquire() as c:
                    await AT.fetch_order(c, org_id=str(org), order_id=oid)
            got = "visible"
        except AT.AltruistOrderRefused as exc:
            got = exc.status
        check(got == HTTP_NOT_FOUND, f"at the simulator, {lbl} through its own connection → 404 (each org's "
              "counterparty answers only its own book)", f"{got}")
    n_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM execution_instructions WHERE id = $1::uuid", iid_a,
                           fetch="fetchval")) if iid_a else -1
    n_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM execution_instructions WHERE id = $1::uuid", iid_b,
                           fetch="fetchval")) if iid_b else -1
    bypass = await platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user", fetch="fetchval")
    check(bypass is False and n_b == 0 and n_a == 0, "RLS (rolbypassrls = false asserted): each org's instruction is "
          "invisible in the other org's context, both directions", f"bypass={bypass} {n_b} {n_a}")
    check(ok, "org B connected to its own counterparty and approved its own instruction", f"{cc} {dumps(oc)[:200]}")
    await _nontest(conn)


async def _approve_b(conn) -> str | None:
    code, out = await route(U_MKB, trade_list(ACC_B, quantity="1"))
    ids = (out.get("routing") or {}).get("instruction_ids") or []
    if code != HTTP_CREATED or not ids or (out.get("routing") or {}).get("routed_to") != CX.ROUTE:
        check(False, "org B's account routes to the simulator once connected", f"{code} {dumps(out)[:300]}")
        return None
    iid = ids[0]
    c1, _ = await api(U_MKB, "POST", f"{_X}/instructions/{iid}/submit", {})
    c2, o2 = await api(U_CKB, "POST", f"{_X}/instructions/{iid}/approve", {})
    st = (await instruction(conn, iid)).get("status")
    if st != "approved":
        check(False, "org B's maker submits and B's checker approves its own instruction", f"{c1} {c2} "
                                                                                         f"{dumps(o2)[:200]}")
        return None
    return iid


async def _nontest(conn):
    """The NON-test org R, inside ONE rolled-back transaction: the D2 decision for a DEMO household's account is the
    simulator route; for a plain household's account it is manual with the not-a-demo-household reason."""
    from services.altruist_oauth import exchange_code_for_tokens, store_new_connection, build_authorize_url
    from urllib.parse import parse_qs, urlsplit

    hosts = AO.simulator_hosts(SLUG_PREFIX + "main")
    state = secrets.token_urlsafe(32)
    url = build_authorize_url(environment=AO.SIMULATOR, state=state, hosts=hosts)
    async with httpx.AsyncClient(transport=APP_T, timeout=30.0, follow_redirects=False) as c:
        res = await c.get(url)
    code = (parse_qs(urlsplit(res.headers.get("location") or "").query).get("code") or [""])[0]
    tok = await exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts)
    S["issued"] += [code, tok.access_token, tok.refresh_token]

    async def fn(c):
        await c.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, false)", ORG_R,
                        "CPSIM03 Verify Org R (rolled back)", f"{TAG}-r")
        is_test = await c.fetchval("SELECT is_test FROM organizations WHERE id = $1", ORG_R)
        for eid, nm, tags in ((E_R_DEMO, "demo", ["demo-cpsim3verify"]), (E_R_PLAIN, "plain", [])):
            await c.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, tags) VALUES ($1, $2, "
                            "$3, 'active', 'individual'::entity_type, $4::text[])", eid, ORG_R, NAME + nm, tags)
        for hh, e in ((H_R_DEMO, E_R_DEMO), (H_R_PLAIN, E_R_PLAIN)):
            await c.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, ORG_R, NAME + "hh")
            await c.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        for acc, eid, hh, n, ext in ((ACC_R_DEMO, E_R_DEMO, H_R_DEMO, 8, "cp3v-r-demo"),
                                     (ACC_R_PLAIN, E_R_PLAIN, H_R_PLAIN, 9, "cp3v-r-plain")):
            await c.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id) VALUES ($1, $2, $3, $4, 'ALTRUIST', "
                "'individual', 'taxable', $5, $6)", acc, ORG_R, f"****{n:04d}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), eid, hh)
            await c.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                            "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", ORG_R, SIM_XWALK, ext, acc)
        for k, v in ((SETTING, "true"), (SETTING_CP, json.dumps(SLUG_PREFIX + "main"))):
            await c.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES "
                            "($1, $2, $3::jsonb, 'counterparty_sim', false)", ORG_R, k, v)
        await c.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_R))
        await store_new_connection(c, org_id=str(ORG_R), environment="simulator", client_id=S["client_ids"]["main"],
                                   client_secret=S["keys"]["main"], tokens=tok, connected_by=None)
        demo = await CX.simulator_route(c, ORG_R, {"id": ACC_R_DEMO, "household_id": H_R_DEMO, "is_held_away": False})
        plain = await CX.simulator_route(c, ORG_R, {"id": ACC_R_PLAIN, "household_id": H_R_PLAIN,
                                                     "is_held_away": False})
        return {"is_test": is_test, "demo": demo, "plain": plain}
    out, err = await rolled_back(conn, fn)
    check(err is None and out is not None and out["is_test"] is False and out["demo"]["ok"] is True and
          out["plain"]["ok"] is False and out["plain"]["condition"] == "demo_household",
          "a NON-test org (rolled back): a DEMO household's account takes the simulator route; a plain household's "
          "account is manual with 'not a demo household' (condition 5)", f"{out} {err!r}")
    left = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1::uuid", ORG_R, fetch="fetchval"))
    check(left == 0, "the non-test org and its demo rows were never committed", f"{left}")


# ── unchanged ───────────────────────────────────────────────────────────────────────────────────────────────────
async def y_unchanged(conn):
    section("[Y] Unchanged: blocks manual only, the FIX router not connected, real capability unknown, loader not "
            "applied")
    d = await platform(conn, "SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k WHERE k.conrelid = (" + REL +
                       ") AND k.conname = 'trade_blocks_route_chk'", "public", "trade_blocks", fetch="fetchval")
    check(d == "CHECK ((route = 'manual'::text))", "trade_blocks_route_chk (re-read): blocks route manual only", f"{d}")
    fix = XR.registry().get("fix")
    check(fix is not None and isinstance(fix, XR.NotConnectedRouter) and XR.any_connected() is False and
          XR.route_refusal("fix") is not None and XR.route_refusal("manual") is None,
          "the FIX router is still a NotConnectedRouter (route 'fix' refused with its reason; manual always offered)",
          f"{type(fix).__name__} {XR.route_refusal('fix')}")
    real = await CC.capability(conn, "altruist", "equity_trading_api")
    check(real.value == "unknown", "real Altruist equity_trading_api is still 'unknown'", f"{real.value}")
    n = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1",
                           "altruist_simulator", fetch="fetchval"))
    check(n == 0, "the capability loader was not applied: no altruist_simulator row exists (only the fixture code)",
          f"{n}")
    code, out, err = run_cmd([sys.executable, str(LOADER)], REPO, timeout=300)
    n2 = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1",
                            "altruist_simulator", fetch="fetchval"))
    check(code == 0 and "DRY RUN — nothing written" in out and "22 to set" in out and n2 == 0,
          "scripts/cpsim03_capabilities.py defaults to --dry-run: the plan printed, nothing written",
          f"{code} {(out + err)[-300:]}")


# ── conformance ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_conformance(conn):
    section("[Y] Conformance: the orders group passes against the simulator; --target sandbox skips it")
    out_json = pathlib.Path("/tmp") / f"cpsim03_verify_conformance_{secrets.token_hex(4)}.json"
    code, out, err = run_cmd([sys.executable, str(CONFORMANCE), "--target", "simulator", "--json-out", str(out_json)],
                             REPO, timeout=900)
    try:
        doc = json.loads(out_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    res = {r["id"]: r["passed"] for r in doc.get("results", [])}
    want = [f"O{n:02d}" for n in range(1, 15)]
    check(code == 0 and all(res.get(k) is True for k in want) and doc.get("failed") == 0,
          "the in-process conformance run: every orders check O01–O14 PASS, 0 FAIL overall",
          f"{code} {[k for k in want if res.get(k) is not True]} {(out + err)[-300:]}")
    code2, out2, _e2 = run_cmd([sys.executable, str(CONFORMANCE), "--target", "sandbox"], REPO, timeout=120)
    check(code2 == REFUSED_EXIT and "orders group: skipped — no published Altruist orders contract" in out2,
          "--target sandbox: the orders group is skipped with 'no published Altruist orders contract' (and the run "
          "refuses to start without sandbox access)", f"{code2} {out2[-200:]}")
    check(ACF.orders_group_skip("sandbox", None) == ACF.NO_ORDERS_CONTRACT and
          ACF.orders_group_skip("production", {"x": 1}) == ACF.NO_ORDERS_CONTRACT and
          ACF.orders_group_skip("simulator", None) == ACF.NOT_A_FIXTURE and
          ACF.orders_group_skip("simulator", {"account_id": "a"}) is None,
          "the suite's own skip rule: any real target → no published contract; the simulator without a fixture book "
          "→ not run")
    try:
        out_json.unlink()
    except OSError:
        pass


# ── safety ──────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_safety(conn):
    section("[Y] Boundary check; app role cannot read cpsim.*; cpsim_app privileges as documented; no secret printed")
    code, out, err = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=300)
    check(code == 0 and "0 violation" in out, "cpsim_boundary_check passes", f"{code} {(out + err)[-300:]}")
    for t in ("orders", "order_executions"):
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
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    check(left == 0, "zero fixture rows remain (public + cpsim)", f"{left}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


FIXTURE_SECTIONS = (("routing", y_routing), ("governance", y_governance), ("outcomes", y_outcomes),
                    ("cancel", y_cancel), ("clock", y_clock), ("book", y_book), ("fills", y_fills),
                    ("webhooks", y_webhooks), ("isolation", y_isolation), ("unchanged", y_unchanged),
                    ("conformance", y_conformance), ("safety", y_safety))
ONLY_NEEDS = {"routing": (), "governance": ("routing",), "outcomes": (),
              "cancel": ("outcomes",), "clock": ("outcomes",),
              "book": ("outcomes", "clock"),
              "fills": ("outcomes", "clock", "book"), "webhooks": (),
              "isolation": ("outcomes",), "unchanged": (), "conformance": (), "safety": ()}


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
                  "fills, events, approval tasks, sod decisions, to-dos, positions, transactions, assets, connections, "
                  "webhook events, settings, capability rows, audit rows, fixture securities, every cpsim table "
                  "incl. orders + executions)", f"{left}")
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
