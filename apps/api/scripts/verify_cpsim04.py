"""verify_cpsim04 — CPSIM04: the simulated custodian MOVES MONEY and the simulated BANKS answer. An APPROVED payment
instruction between a family's own accounts is sent by a PERSON ("Send to custodian") to the simulated custodian's
transfers API (contract §9, every field ASSUMED); the simulator's clock acknowledges, settles and (by behaviour) returns
it with an ACH code; a PERSON refreshes the status ("Refresh status from custodian") — each new custodian status
applied through the EXISTING payments person function with that person as the actor; the cpsim02 sync brings the cash
transactions (D2 types) and the EXISTING matches() / reconcile(custodian_data) close the loop. Banks: domestic wire /
ACH and international wire checked against the bank's FAKE references, credited in its book, rejected with its reason,
ACH returns from the sender's and the receiving bank's behaviour. Consent, idempotency, the cash ledger (Decimal) for
transfers and fills, isolation both ways, the demo-household rule in a NON-test org, D4 all-or-manual with each
condition removed, the S30 floor, no unattended path, third-party payments still off, the conformance transfers group.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim04.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback) and
exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx network
transport refuses and RECORDS the URL (a call that would leave the process is a [FAIL] naming it). The simulator (the
custodian AND both banks — one sub-app), the app API and the webhook receiver all run IN-PROCESS through ASGI transports
against the real ``main.app``: the client's simulator transport (``altruist_oauth.set_simulator_transport`` — the
transfers client resolves EVERY call through ``_transport_for``, so the transport is threaded on every call) and the
simulator's webhook transport (``cpsim.webhooks.set_transport``). No model call (the three helpers refuse and record).

FIXTURE SECRETS are generated in-process and never printed: the counterparties' keys come from the admin create call
(shown once); ALTRUIST_SIMULATOR_WEBHOOK_SECRET = cpsim_whsec_ + random; ALTRUIST_WEBHOOK_SECRET is replaced IN THIS
PROCESS; CPSIM_BASE_URL is https://cpsim.verify4.invalid (never resolved). Fake bank numbers are never printed: only
their last four, and the scan for full numbers prints the culprit's last four.

TIME IS INJECTED: the counterparty clock (cpsim.registry.set_today) starts Monday 2026-11-16 and every section reads the
live clock back before it moves money; services.payments.set_clock follows it (``today()``), so value dates, settle
dates and return windows agree on both sides. The simulator's wall clock (cpsim.oauth.set_now) is the real time minus
ten minutes.

FIXTURES (ids 99000000-0000-0000-0000-0000c954…; sign-in tag cpsim04verify): TEST orgs A and B inside the reserved
prefix (is_test asserted); the NON-test org R (outside the prefix) exists ONLY inside one ROLLED-BACK transaction (the
demo-household decision). Users with real user_roles grants (INTERNAL role names), each decided by the app (asserted):
U_SA Super Admin; U_IN initiator (support_staff — initiate, no approve); U_AP approver + refresher (compliance —
approve, no initiate); U_MA maker who ALSO holds approve (advisor — the S30 refusal is services.sod's, not a 403);
U_RD reader (ir_member_relations — view_portfolio only); U_LT initiator whose trading tier is 'inquiry' (the authority
check); U_INB / U_APB org B's initiator / approver. Counterparties carry the reserved slug prefix ``cpsim4verify-``: the
custodian ``main`` (org A), ``other`` (org B), the domestic bank ``dom`` and the international bank ``intl``. One
fixture security 'CPSIM04 VERIFY EQ' (identifier CPSIM04E1 / CP4VEQ, provider_key cpsim04verify) for the fills' cash.
The capability rows use the FIXTURE custodian code ``zz_cpsim4v_sim`` (services.payment_custodian
.set_capability_custodian for the run, restored at the end); the real ``altruist_simulator`` code must have NO row.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by this run's fixture ids with every
teardown GUC a guard reads (``payments.teardown`` — payment instructions / events; ``execution.teardown`` — the fixture
capability rows; ``sod.teardown`` always; ``cpsim.allow_teardown`` — the upload log, orders, executions, transfers,
transfer events, cash movements). Every touched table re-read for its exact pre-test count BY FIXTURE IDS ONLY
(audit_log, sod_decisions, payment_events and webhook events by fixture ids). Real rows — every non-fixture
counterparty / transfer / book / cash movement, real payment instructions and events, real capability rows, the cpsim
settings and payment-adapter settings of real orgs, demo-tagged entities, the cash08 payee tables, real webhook events —
fingerprinted (count + md5) before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown, per-table counts and the real
fingerprints only. --only=<routing|governance|journal|banks|returns|consent|idempotency|book|webhooks|isolation|
unchanged|conformance|safety>: the seed, the named sections (each sets up what it needs itself), the full teardown.
--only=front_end (alone): the render tests + node --test tests/ only, no seed or fixtures (SKIP_BUILD=1 skips npm build).
With VERIFY_NESTED=1 the regressions print one '[SKIP] nested run'.
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

SPRINT_KEY = "cpsim04"
BEFORE_REF = "b767e2a"   # cpsim04-work's base: 'merge cpsim03'
_P = "/api/v1/admin/counterparties"
_PAY = "/api/v1/cash/households/{household_id}/payments/{payment_id}"
ROUTES = {("POST", _PAY + "/send-to-custodian"): "initiate_payment",
          ("POST", _PAY + "/refresh-from-custodian"): "any(initiate_payment|approve_payment)",
          ("GET", _PAY + "/custodian-transfer"): "any(initiate_payment|approve_payment)",
          ("GET", _P + "/{counterparty_id}/transfers"): "super_admin",
          ("POST", _P + "/{counterparty_id}/transfers/{transfer_id}/consent"): "super_admin",
          ("GET", _P + "/{counterparty_id}/received"): "super_admin",
          ("POST", _P + "/{counterparty_id}/links"): "super_admin"}
SIM_ROUTES = {("POST", "/cpsim/c/{slug}/v2/transfers"), ("GET", "/cpsim/c/{slug}/v2/transfers"),
              ("GET", "/cpsim/c/{slug}/v2/transfers/{transfer_id}"),
              ("DELETE", "/cpsim/c/{slug}/v2/transfers/{transfer_id}")}
NODE_TEST = "tests/cpsim04/cpsim04.test.mjs"
#: cpsim04 fix 2: the expected count is derived from the test file's own top-level ``test(`` declarations (a
#: constant went stale when fix 1 added the manual-route reason test); the file must keep at least this many
NODE_TEST_MIN = 9


def node_tests_declared() -> int:
    """Top-level ``test(`` declarations in NODE_TEST, counted statically by their file."""
    return len(re.findall(r"^test\(", _src("apps/web/" + NODE_TEST), re.M))
BOUNDARY = HERE / "cpsim_boundary_check.py"
CONFORMANCE = HERE / "cpsim_altruist_conformance.py"
LOADER = HERE / "cpsim04_capabilities.py"
REGRESSIONS = ("verify_cpsim03.py", "verify_cash07.py", "verify_cash08.py", "verify_authz1.py")
HTTP_OK, HTTP_CREATED, HTTP_FORBIDDEN, HTTP_NOT_FOUND, HTTP_CONFLICT, HTTP_UNPROCESSABLE = 200, 201, 403, 404, 409, 422
REFUSED_EXIT = 2
SECTION_TIMEOUT = 600   # every section must finish in < 10 minutes
D4_CONDITIONS = ("real_crosswalk", "crosswalk", "allowed", "connection", "demo_household", "adapter_enabled",
                 "status_feed", "rail", "destination")


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cpsim04's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cpsim04 changed no existing route's gate and removed none (mark sent / settle / reconcile / the admin "
          "routes keep theirs)", f"{o.where()}; changed={sorted(changed)[:3]} removed={o.removed[:3]}")
    mine = flat(o.new)
    want = set(ROUTES)
    check(mine == want, f"this sprint's own routes are exactly its {len(ROUTES)} routes (send, refresh, custodian "
          "transfer; admin transfers, consent, received, links)",
          f"{o.where()}; extra={sorted(mine - want)[:3]} missing={sorted(want - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items()
           if k not in by or g not in str(by[k].get("after"))]
    check(not bad, "each own route names exactly its gate at cpsim04's own end point (initiate_payment; initiate OR "
          "approve; super_admin)", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cpsim04's routes all still exist today with the same gate", f"{drift[:4]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    sim_ = {(m, r.get("path")) for r in data.get("simulator_routes", []) for m in (r.get("methods") or [])}
    check(SIM_ROUTES <= sim_, "the four simulator transfers routes are listed in the simulator section of "
          "docs/AUTHZ1_ROUTES (own auth, not app routes)", f"missing={sorted(SIM_ROUTES - sim_)}")


def _section9(text: str) -> str:
    i = text.find("## 9. Transfers")
    return text[i:] if i >= 0 else ""


def _flat(text: str) -> str:
    """CPSIM04 FIX 1 (D1 / D2): docs are hard-wrapped markdown — a fact split across a line break is still stated.
    Whitespace runs collapse to one space; the words themselves are matched exactly."""
    return re.sub(r"\s+", " ", text or "")


def self_static(check, find):
    n = _flat(_src("docs/cpsim/CPSIM04_NOTES.md"))
    for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11"):
        check(f"| {d} |" in n, f"CPSIM04_NOTES records {d} as built")
    for w in ("CPSIM_BANK", "buy", "sell", "adjustment", "payment_events_human_chk", "S30 floor", "DRY RUN",
              "Sandbox / production / manual unchanged", "51 PASS, 0 FAIL", "Differences from the prompt"):
        check(w in n, f"CPSIM04_NOTES names {w!r}")
    s9 = _section9(_src("docs/cpsim/ALTRUIST_CONTRACT.md"))
    head = s9.split("### 9.4")[0]
    rows = [ln for ln in head.splitlines() if ln.startswith("| ") and not ln.startswith("| #") and
            not ln.startswith("| Field") and not ln.startswith("|---")]
    unassumed = [ln[:60] for ln in rows if "ASSUMED" not in ln and "simulator" not in ln]
    check(bool(s9) and "ASSUMED — no published Altruist money-movement API seen" in s9 and len(rows) >= 25
          and not unassumed and "### 9.4 Feed transaction types for cash (`GET /v2/transactions`) — ASSUMED" in s9
          and "### 9.5 Webhooks (outbound, simulator → us) — ASSUMED" in s9,
          "ALTRUIST_CONTRACT §9 'Transfers (ASSUMED — no published Altruist money-movement API seen)': every call / "
          "field row is marked ASSUMED; §9.4 / §9.5 headed ASSUMED", f"{len(rows)} rows; not ASSUMED: {unassumed[:3]}")
    for w in ("POST {api_base}/v2/transfers", "GET {api_base}/v2/transfers/{transfer_id}",
              "DELETE {api_base}/v2/transfers/{transfer_id}", "client_transfer_id", "transfer.updated",
              "transfer_out", "ach_return", "pending_consent", "routing_last4", "iban_last4"):
        check(w in s9, f"contract §9 defines {w!r}")
    rs = _src("scripts/regression_set.txt")
    check(bool(re.search(r"^verify_cpsim04\.py\s*$", rs, re.M)), "verify_cpsim04.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    check("cpsim04" in st.lower(), "PROJECT_STATUS records cpsim04")
    adm = _src("docs/cpsim/CPSIM_ADMIN.md")
    check(all(w in adm for w in ("Transfers", "Received", "Record member consent", "cpsim04_capabilities.py --apply",
                                 "altruist_simulator")), "CPSIM_ADMIN.md: transfers, banks, consent, the operator steps")
    cp = _flat(_src("docs/cash/CASH_PAYMENTS.md"))
    cpl = cp.lower()
    check("a person approves, sends and refreshes" in cpl and "**manual** is the default route" in cpl
          and "`altruist_simulator`" in cp and "stay available beside it" in cp,
          "CASH_PAYMENTS.md: the simulator adapter beside manual (a person approves, sends and refreshes; manual "
          "still the default; the sheet / Confirm sent beside it)")
    dd = _src("docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md")
    check("v1 COMPLETE" in dd and "v2" in dd, "the design doc: v1 COMPLETE and the v2 list")
    pa = _code_only(_src("apps/api/services/payment_adapters.py"))
    check('_CONNECTED: dict[str, type[PaymentAdapter]] = {SIMULATOR_KEY: AltruistSimulatorPaymentAdapter}' in pa
          and 'SIMULATOR_KEY = "altruist_simulator"' in pa,
          "the ONLY connected payment adapter is the simulator's (key altruist_simulator — never altruist)")


def self_regression_mode(check):
    """CPSIM04 FIX 1 task 0 — the shared nested-run helper honours VERIFY_REGRESSIONS, proven on STUB verifies
    (temp files), never a real verify."""
    import tempfile
    import _verify_total as VT
    print("── [Y] Regression mode: VT.run_nested honours VERIFY_REGRESSIONS (fast = --self-checks-only, the default; "
          "full = the full nested run); proven on temp stub verifies ──")
    stub = (
        "import sys, os\n"
        "mode = 'self' if '--self-checks-only' in sys.argv else 'full'\n"
        "print('[INFO] stub mode=' + mode + ' nested=' + os.environ.get('VERIFY_NESTED', ''))\n"
        "fail = int(os.environ.get('STUB_FAIL_' + mode.upper(), '0'))\n"
        "print('[PASS] stub ' + mode)\n"
        "print('[FAIL] stub failure') if fail else None\n"
        "print('TOTAL: ' + ('3' if mode == 'self' else '40') + ' PASS, ' + str(fail) + ' FAIL')\n"
        "sys.exit(1 if fail else 0)\n"
    )
    saved = os.environ.get("VERIFY_REGRESSIONS")
    with tempfile.TemporaryDirectory() as td:
        good = pathlib.Path(td) / "verify_stub_good.py"
        good.write_text("# supports --self-checks-only\n" + stub, encoding="utf-8")
        bare = pathlib.Path(td) / "verify_stub_bare.py"
        bare.write_text(stub.replace("'--self-checks-only'", "'--self' + '-checks-only'"), encoding="utf-8")
        try:
            os.environ.pop("VERIFY_REGRESSIONS", None)
            check(VT.regression_mode() == "fast", "VERIFY_REGRESSIONS unset → fast (the default)")
            t, out, _e, code = VT.run_nested(good, timeout=60)
            check(t.ok and "stub mode=self nested=1" in out and t.passed == 3,
                  "fast: the stub ran with --self-checks-only + VERIFY_NESTED=1, judged by its own TOTAL", f"{t} {out!r}")
            t, out, _e, code = VT.run_nested(good, timeout=60, env_extra={"STUB_FAIL_SELF": "1"})
            check(not t.ok and t.failed == 1, "fast: a FAIL in the self-checks is still a FAIL", f"{t}")
            t, out, _e, code = VT.run_nested(bare, timeout=60)
            check(not t.ok and "no --self-checks-only" in t.reason and code == 2,
                  "fast: a nested script with no --self-checks-only is a FAIL (never silently skipped)", f"{t}")
            os.environ["VERIFY_REGRESSIONS"] = "full"
            check(VT.regression_mode() == "full", "VERIFY_REGRESSIONS=full → full")
            t, out, _e, code = VT.run_nested(good, timeout=60)
            check(t.ok and "stub mode=full nested=1" in out and t.passed == 40,
                  "full: the stub ran WITHOUT --self-checks-only (today's behaviour), VERIFY_NESTED=1", f"{t} {out!r}")
            t, out, _e, code = VT.run_nested(bare, timeout=60)
            check(t.ok, "full: a script with no --self-checks-only still runs in full", f"{t}")
            os.environ["VERIFY_REGRESSIONS"] = "fsat"
            try:
                VT.regression_mode()
                check(False, "an unknown VERIFY_REGRESSIONS value is refused")
            except ValueError:
                check(True, "an unknown VERIFY_REGRESSIONS value is refused")
        finally:
            if saved is None:
                os.environ.pop("VERIFY_REGRESSIONS", None)
            else:
                os.environ["VERIFY_REGRESSIONS"] = saved
    rr = _src("scripts/run_regressions.sh")
    check(bool(re.search(r"^export VERIFY_REGRESSIONS=full$", rr, re.M))
          and 'VERIFY_REGRESSIONS=full VERIFY_NESTED=1 timeout "$TIMEOUT_S"' in rr,
          "scripts/run_regressions.sh runs every verify with VERIFY_REGRESSIONS=full")
    vr = _src("docs/VERIFY_RULES.md")
    check("VERIFY_REGRESSIONS" in vr and "--self-checks-only" in vr, "docs/VERIFY_RULES.md states the regression-mode rule")
    ps = _src("docs/PROJECT_STATUS.md")
    check("full regression: scripts/run_regressions.sh at the end of each sprint cycle" in ps,
          "PROJECT_STATUS names the operator step (full regression at the end of each sprint cycle)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)
    self_regression_mode(check)


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
from cpsim import ledger as SLG  # noqa: E402
from cpsim import mirror as SM  # noqa: E402
from cpsim import oauth as SO  # noqa: E402
from cpsim import orders as SOR  # noqa: E402
from cpsim import registry as R  # noqa: E402
from cpsim import transfers as STR  # noqa: E402
from cpsim import webhooks as SW  # noqa: E402
from services import altruist_conformance as ACF  # noqa: E402
from services import altruist_oauth as AO  # noqa: E402
from services import altruist_positions_sync as APS  # noqa: E402
from services import altruist_transfers as AT  # noqa: E402
from services import altruist_webhook as AW  # noqa: E402
from services import cash_payees as CPY  # noqa: E402
from services import custodian_capabilities as CC  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import payment_adapters as PAD  # noqa: E402
from services import payment_custodian as PC  # noqa: E402
from services import payments as PS  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cpsim04verify"
NAME = "CPSIM04 VERIFY "
SLUG_PREFIX = "cpsim4verify-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c954"
FIX_CAP = "zz_cpsim4v_sim"
D0 = date(2026, 11, 16)          # the counterparty clock starts here (Monday)
THU = date(2026, 11, 12)         # the book's first snapshot
SIM_BASE = "https://cpsim.verify4.invalid"
REDIRECT = "https://verify.cpsim04.invalid/altruist/callback"
PROVIDER_KEY = "cpsim04verify"
SIM_XWALK, REAL_XWALK, BANK_XWALK = "ALTRUIST_SIMULATOR", "ALTRUIST", "CPSIM_BANK"
SETTING, SETTING_CP = "counterparty_sim.allowed", "counterparty_sim.altruist_counterparty"
TEARDOWN_GUCS = ("payments.teardown", "execution.teardown", "cpsim.allow_teardown")
CASH_A1, CASH_A3 = Decimal("100000.00"), Decimal("5000.00")
PRICE_EQ = Decimal("50.00")
RETURN_DAYS = 2


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
ORG_R = UUID("c5140000-0000-4000-8000-0000c9540003")    # NON-test, rolled back only (outside the reserved prefix)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SA, U_IN, U_AP, U_MA, U_RD, U_LT, U_INB, U_APB = (_id(10 + i) for i in range(8))
ALL_USERS = [U_SA, U_IN, U_AP, U_MA, U_RD, U_LT, U_INB, U_APB]
E_A, E_B, E_R_DEMO, E_R_PLAIN = _id(40), _id(41), _id(42), _id(43)
H_A, H_B, H_R_DEMO, H_R_PLAIN = _id(30), _id(31), _id(32), _id(33)
ACC_SRC, ACC_DST, ACC_DOM, ACC_INTL, ACC_REAL, ACC_NOX, ACC_B1, ACC_B2 = (_id(50 + i) for i in range(8))
ACC_R_DEMO, ACC_R_DEMO2, ACC_R_PLAIN, ACC_R_PLAIN2 = _id(60), _id(61), _id(62), _id(63)
SEC_EQ = _id(70)
HOUSEHOLDS = [H_A, H_B]
ACCOUNTS = [ACC_SRC, ACC_DST, ACC_DOM, ACC_INTL, ACC_REAL, ACC_NOX, ACC_B1, ACC_B2]
SECURITIES = [SEC_EQ]
PERMS = {"support_staff": ("view_portfolio", "initiate_payment"),
         "compliance": ("view_portfolio", "approve_payment"),
         "advisor": ("view_portfolio", "initiate_payment", "approve_payment"),
         "ir_member_relations": ("view_portfolio",)}
# uid → (org, users.role mirror, short, role granted, trading tier on the org's family or None)
USERS = {U_SA: (ORG_A, "super_admin", "sa", "super_admin", "limited"),
         U_IN: (ORG_A, "member", "in", "support_staff", "limited"),
         U_AP: (ORG_A, "member", "ap", "compliance", "limited"),
         U_MA: (ORG_A, "member", "ma", "advisor", "limited"),
         U_RD: (ORG_A, "member", "rd", "ir_member_relations", None),
         U_LT: (ORG_A, "member", "lt", "support_staff", "inquiry"),
         U_INB: (ORG_B, "member", "inb", "support_staff", "limited"),
         U_APB: (ORG_B, "member", "apb", "compliance", "limited")}
ROLE_EXPECT = {U_SA: {"org": ORG_A, "roles": ["super_admin"]},
               U_IN: {"org": ORG_A, "roles": ["support_staff"], "has": ["initiate_payment"],
                      "lacks": ["approve_payment"]},
               U_AP: {"org": ORG_A, "roles": ["compliance"], "has": ["approve_payment"],
                      "lacks": ["initiate_payment"]},
               U_MA: {"org": ORG_A, "roles": ["advisor"], "has": ["initiate_payment", "approve_payment"]},
               U_RD: {"org": ORG_A, "roles": ["ir_member_relations"], "has": ["view_portfolio"],
                      "lacks": ["initiate_payment", "approve_payment"]},
               U_LT: {"org": ORG_A, "roles": ["support_staff"], "has": ["initiate_payment"]},
               U_INB: {"org": ORG_B, "roles": ["support_staff"], "has": ["initiate_payment"]},
               U_APB: {"org": ORG_B, "roles": ["compliance"], "has": ["approve_payment"]}}
# the custodian book's external ids (text, compared as text)
A1, A2, A3, A_REAL_SIM = "cp4v-acct-1", "cp4v-acct-2", "cp4v-acct-3", "cp4v-acct-real"
B1, B2 = "cp4v-b-acct-1", "cp4v-b-acct-2"
HH1, HHB = "cp4v-hh-1", "cp4v-hh-b"
BANK_DOM_ACCT, BANK_INTL_ACCT = "cp4v-dom-1", "cp4v-intl-1"
REAL_EXT = "cp4v-real-1"
CUSIP_EQ, TICKER_EQ = "CPSIM04E1", "CP4VEQ"
SEED_CHECK_LITERALS = (
    ("portfolio.external_references", "ext_ref_record_type_chk", ("account",)),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("ach", "wire", "journal",
                                                                         "payment_status_feed")),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("assumed",)),
    ("public.payment_instructions", "payment_instructions_rail_chk", ("ach", "wire", "journal", "check")),
    ("public.payment_instructions", "payment_instructions_status_chk", ("approved", "sent", "awaiting_client_consent",
                                                                        "acknowledged", "settled", "reconciled",
                                                                        "closed", "rejected", "returned")),
    ("public.payment_events", "payment_events_action_chk", ("mark_sent", "consent_requested", "acknowledge",
                                                            "settle", "custodian_reject", "return", "reconcile")),
    ("public.trading_authority_grants", "trading_authority_grants_authority_tier_check", ("inquiry", "limited")),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity",)),
    ("portfolio.securities_global_prices", "sec_global_price_type_chk", ("close",)),
    ("cpsim.transfers", "transfers_status_chk", STR.STATUSES),
    ("cpsim.transfers", "transfers_rail_chk", ("ach", "wire", "journal")),
    ("cpsim.cash_movements", "cash_movements_kind_chk", SLG.KINDS),
    ("cpsim.book_transactions", "book_transactions_type_chk", ("transfer_in", "transfer_out", "ach_in", "ach_out",
                                                               "wire_in", "wire_out", "ach_return")),
    ("cpsim.book_positions", "book_positions_row_type_chk", ("cash", "security")),
    ("cpsim.counterparty_behaviour", "counterparty_behaviour_ach_chk", ()),
)
UNIQUES = ("transfers_client_live_uq", "transfers_transfer_id_live_uq", "transfer_events_seq_uq",
           "cash_movements_leg_uq", "external_references_org_source_ext_type_key", "altruist_connections_active_unique",
           "trading_authority_grants_entity_id_user_id_key", "custodian_capabilities_live_uq",
           "org_settings_org_id_setting_key_key", "uq_sec_global_ident_issuer_assigned", "uq_sec_global_price_point")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "tags"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id", "is_discretionary", "is_held_away"),
    "trading_authority_grants": ("org_id", "entity_id", "user_id", "authority_tier"),
    "portfolio.external_references": ("org_id", "source_system", "external_id", "record_type", "record_id"),
    "portfolio.securities_global": ("id", "name", "security_type", "currency_code"),
    "portfolio.securities_global_identifiers": ("global_security_id", "id_type", "id_value", "is_primary"),
    "portfolio.securities_global_prices": ("global_security_id", "price_date", "price", "currency_code", "price_type",
                                           "source", "provider_key"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category", "is_public"),
}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.entities", "public.households",
                  "public.household_memberships", "public.accounts", "public.org_settings",
                  "public.audit_log", "public.trading_authority_grants", "public.altruist_connections",
                  "public.altruist_oauth_states", "public.altruist_webhook_events", "public.member_todos",
                  "public.sod_decisions", "public.payment_instructions", "public.payment_events",
                  "public.custodian_capabilities", "portfolio.external_references", "portfolio.positions",
                  "portfolio.transactions", "portfolio.assets", "portfolio.securities_global",
                  "portfolio.securities_global_identifiers", "portfolio.securities_global_prices")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.counterparty_behaviour",
                "cpsim.counterparty_clock", "cpsim.counterparty_bank_details", "cpsim.request_log",
                "cpsim.book_uploads", "cpsim.book_households", "cpsim.book_accounts", "cpsim.book_positions",
                "cpsim.book_transactions", "cpsim.oauth_codes", "cpsim.oauth_tokens", "cpsim.orders",
                "cpsim.order_executions", "cpsim.transfers", "cpsim.transfer_events", "cpsim.cash_movements")
#: from the ONE shared list verify_cpsim02 / 03 read too — asserted exactly below
NEW_CPSIM_PRIVS = {t: set(FX.cpsim_app_privileges(t)) for t in ("transfers", "transfer_events", "cash_movements")}
assert NEW_CPSIM_PRIVS == {"transfers": {"SELECT", "INSERT", "UPDATE"}, "transfer_events": {"SELECT", "INSERT"},
                           "cash_movements": {"SELECT", "INSERT"}}

S: dict = {"cp": {}, "keys": {}, "client_ids": {}, "issued": [], "pay": {}, "bodies": [], "fake_numbers": [],
           "events": []}
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
    """Never print a secret or a full (fake) bank number: issued values masked, fake numbers cut to their last four."""
    t = str(text)
    for v in S["issued"]:
        if v:
            t = t.replace(v, "[secret]")
            body = v.split("_")[-1]
            if len(body) >= 16:
                t = t.replace(body, "[secret]")
    for v in S["fake_numbers"]:
        if v and len(v) > 4:
            t = t.replace(v, "…" + v[-4:])
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


def number_leaks(text: str) -> list[str]:
    """Any FULL fake bank number (ABA, account, IBAN) in ``text`` — reported by its last four only."""
    return ["…" + v[-4:] for v in S["fake_numbers"] if v and len(v) > 4 and v in text]


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


async def rolled_back(conn, fn, *, super_admin: bool = True, org=None, as_sim: bool = False):
    """Run ``fn(conn)`` in a transaction that is ALWAYS rolled back; returns (result, error)."""
    out = {"res": None, "err": None}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', $1, true), "
                               "set_config('app.current_org_id', $2, true)", "true" if super_admin else "false",
                               str(org) if org else "")
            if as_sim:
                await conn.execute("SET LOCAL ROLE cpsim_app")
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
        "counterparty_sim.* and cash.payment_adapters settings of every real org": await fingerprint(
            conn, "SELECT org_id, setting_key, setting_value, updated_at FROM org_settings WHERE (setting_key LIKE "
                  "'counterparty\\_sim.%' OR setting_key = 'cash.payment_adapters') AND NOT (org_id = ANY($1::uuid[]))",
            orgs),
        "the third-party payments switch of every real org": await fingerprint(
            conn, "SELECT org_id, setting_key, setting_value FROM org_settings WHERE setting_key LIKE "
                  "'cash.third\\_party%' AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "demo-tagged entities": await fingerprint(
            conn, "SELECT id, display_name, tags, status FROM entities WHERE EXISTS (SELECT 1 FROM unnest(tags) x "
                  "WHERE x LIKE 'demo-%') AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real payment instructions": await fingerprint(
            conn, "SELECT id, status, system_to, adapter, sent_channel, external_ref FROM payment_instructions WHERE "
                  "NOT (org_id = ANY($1::uuid[]))", orgs),
        "real payment events": await fingerprint(
            conn, "SELECT id, action, to_status FROM payment_events WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "cash08 payees (real)": await fingerprint(
            conn, "SELECT id, system_to FROM cash_payees WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "real custodian capability rows": await fingerprint(
            conn, "SELECT id, custodian_code, capability_key, value, valid_to FROM custodian_capabilities WHERE "
                  "custodian_code <> $1", FIX_CAP),
        "real Altruist connections": await fingerprint(
            conn, "SELECT id, org_id, environment, status, system_to FROM altruist_connections WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real crosswalks (every source)": await fingerprint(
            conn, "SELECT id, source_system, external_id, record_id FROM portfolio.external_references WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real webhook events": await fingerprint(
            conn, "SELECT id, event_id, org_id, processed_at FROM altruist_webhook_events WHERE NOT "
                  "(COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid) = ANY($1::uuid[])) AND event_id "
                  "NOT LIKE 'cpsim-evt-%'", orgs),
        "real to-dos": await fingerprint(
            conn, "SELECT id, status FROM member_todos WHERE source = 'payment_custodian_status' AND NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
    }
    for label, sql in (("counterparties (non-fixture)", f"SELECT id, slug, status, system_to FROM cpsim.counterparties "
                                                        f"WHERE slug NOT LIKE '{SLUG_PREFIX}%'"),
                       ("transfers (non-fixture)", f"SELECT id, status, system_to FROM cpsim.transfers WHERE "
                                                   f"counterparty_id NOT IN {FIX_CP}"),
                       ("cash movements (non-fixture)", f"SELECT id, amount FROM cpsim.cash_movements WHERE "
                                                        f"counterparty_id NOT IN {FIX_CP}"),
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
            # the security master has no org_id: the fixture security, its identifiers and platform prices by id
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [list(SECURITIES)]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            # the simulator's books have no org_id: by the reserved fixture slug prefix (transfers, events, movements,
            # orders, executions reached by the FK closure; their guards honour cpsim.allow_teardown for the owner)
            "cpsim.counterparties": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
            "cpsim.request_log": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cpsim04")
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
    """A direct service call in ONE org's RLS context — the same contextvars the API's middleware sets."""

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
    kw = {"headers": {"User-Agent": "verify_cpsim04", **(headers or {})}}
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


def pp(hh, tail: str = "") -> str:
    return f"/api/v1/cash/households/{hh}/payments{tail}"


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
        raise httpx.ConnectError(f"verify_cpsim04: network disabled ({request.url.host})", request=request)

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cpsim04: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for fn, nm in ((R.set_today, "registry.set_today"), (SM.set_now, "mirror.set_now"), (SO.set_now, "oauth.set_now"),
                   (AO.set_simulator_transport, "altruist_oauth.set_simulator_transport"),
                   (SW.set_transport, "webhooks.set_transport"), (PS.set_clock, "payments.set_clock"),
                   (PC.set_capability_custodian, "payment_custodian.set_capability_custodian")):
        if len(list(inspect.signature(fn).parameters)) != 1:
            raise RuntimeError(f"{nm} signature drifted")
    S["today"] = D0
    S["now"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    R.set_today(lambda: S["today"])
    PS.set_clock(lambda: S["today"])             # the payments side follows the counterparty clock
    SM.set_now(lambda: S["now"])
    SO.set_now(lambda: S["now"])
    global APP_T
    APP_T = FX.TimeoutASGITransport(app=main.app)
    AO.set_simulator_transport(APP_T)            # simulator environment ONLY (sandbox / production never read it)
    SW.set_transport(httpx.ASGITransport(app=main.app))
    SW.set_sleep(lambda s: asyncio.sleep(0))
    PC.set_capability_custodian(FIX_CAP)         # the fixture code (the real altruist_simulator has no row)
    os.environ[AO.CPSIM_BASE_URL_ENV] = SIM_BASE
    os.environ["ALTRUIST_REDIRECT_URI"] = REDIRECT
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", "cpsim04verify-" + secrets.token_hex(16))
    S["whsec"] = "cpsim_whsec_" + secrets.token_hex(20)
    S["real_whsec"] = "verify-real-" + secrets.token_hex(20)
    S["issued"] += [S["whsec"], S["real_whsec"]]
    os.environ[AW.SIMULATOR_SECRET_ENV_VAR] = S["whsec"]
    os.environ[AW.SECRET_ENV_VAR] = S["real_whsec"]


def restore_stubs():
    R.set_today(None)
    PS.set_clock(None)
    SM.set_now(None)
    SO.set_now(None)
    AO.set_simulator_transport(None)
    SW.set_transport(None)
    SW.set_sleep(None)
    PC.set_capability_custodian(None)


def set_sim_creds(key="main") -> None:
    os.environ["ALTRUIST_SIMULATOR_CLIENT_ID"] = S["client_ids"][key]
    os.environ["ALTRUIST_SIMULATOR_CLIENT_SECRET"] = S["keys"][key]


# ═══ The clock, the book, payments through the REAL routes ═════════════════════════════════════════════════════
async def clock(conn, key="main") -> date:
    """The counterparty's live business date — and the payments side follows it (services.payments.set_clock)."""
    d = await sim(conn, "SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = $1::uuid AND "
                        "valid_to IS NULL", S["cp"][key], fetch="fetchval")
    S["today"] = d
    return d


async def advance(conn, key="main") -> tuple[int, dict]:
    code, out = await api(U_SA, "POST", cpu(key, "/clock/advance"))
    await clock(conn, key)
    if code != HTTP_OK:
        check(False, "the Super Admin's clock advance → 200", f"{code} {dumps(out)[:300]}")
    return code, out


async def behaviour(key="main", **fields) -> int:
    code, out = await api(U_SA, "PUT", cpu(key, "/behaviour"), fields)
    if code != HTTP_OK:   # cpsim03 fix 1: a refused PUT must never pass silently
        check(False, f"the Super Admin's behaviour PUT {sorted(fields)} on {key} → 200", f"{code} {dumps(out)[:300]}")
    return code


async def cash(conn, key: str, account: str) -> Decimal | None:
    """The account's cash balance in the simulator's book (Decimal), on the SENDING custodian's live clock: a bank's
    legs are booked on the clock of the custodian that moved the money (CPSIM04_NOTES §4), so a bank is read on the
    ``main`` custodian's date; org B's custodian on its own."""
    clock_of = "other" if key == "other" else "main"
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', '', true)")
        await conn.execute("SET LOCAL ROLE cpsim_app")
        d = await conn.fetchval("SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = $1::uuid "
                                "AND valid_to IS NULL", S["cp"][clock_of])
        return await SLG.balance(conn, S["cp"][key], account, d)


async def transfer_of(conn, pid) -> dict:
    return dict(await sim(conn, "SELECT * FROM cpsim.transfers WHERE client_transfer_id = $1 AND system_to IS NULL AND "
                                "counterparty_id IN " + FIX_CP, str(pid), fetch="fetchrow") or {})


async def movements(conn, transfer_id: str) -> list[dict]:
    return [dict(r) for r in await sim(conn, "SELECT * FROM cpsim.cash_movements WHERE source_ref = $1 AND "
                                             "counterparty_id IN " + FIX_CP + " ORDER BY created_at, movement_kind",
                                       transfer_id)]


def body_of(src, dst, amount: str, rail: str, vd: date, purpose: str) -> dict:
    return {"source_account_id": str(src), "destination_account_id": str(dst), "amount": amount, "rail": rail,
            "value_date": vd.isoformat(), "purpose": purpose}


async def drafted(uid, src, dst, amount, rail, vd, purpose, hh=H_A) -> tuple[int, dict]:
    return await api(uid, "POST", pp(hh), body_of(src, dst, amount, rail, vd, purpose))


async def act(uid, pid, action, hh=H_A, **b) -> tuple[int, dict]:
    return await api(uid, "POST", pp(hh, f"/{pid}/{action}"), b)


async def approved(conn, src, dst, amount: str, rail: str, label: str, *, maker=U_IN, checker=U_AP, hh=H_A,
                   vd: date | None = None) -> str | None:
    """Draft (maker) → submit (maker) → approve (checker) through the routes. Returns the payment id (approved)."""
    vd = vd or await clock(conn, "main" if hh == H_A else "other")
    c0, o0 = await drafted(maker, src, dst, amount, rail, vd, f"{NAME}{label}", hh)
    pid = ((o0 or {}).get("payment") or {}).get("id")
    if c0 != HTTP_CREATED or not pid:
        check(False, f"{label}: drafted by the maker", f"{c0} {dumps(o0)[:400]}")
        return None
    c1, o1 = await act(maker, pid, "submit", hh)
    c2, o2 = await act(checker, pid, "approve", hh)
    row = await payment(conn, pid)
    if not (c1 == HTTP_OK and c2 == HTTP_OK and row.get("status") == "approved"):
        check(False, f"{label}: submitted by the maker, approved by a different person", f"{c1} {c2} "
              f"{row.get('status')} {dumps(o1)[:200]} {dumps(o2)[:200]}")
        return None
    S["pay"][label] = pid
    return pid


async def payment(conn, pid) -> dict:
    return dict(await platform(conn, "SELECT * FROM payment_instructions WHERE id = $1::uuid AND system_to IS NULL",
                               str(pid), fetch="fetchrow") or {})


async def pevents(conn, pid) -> list[dict]:
    rows = await platform(conn, "SELECT action, from_status, to_status, actor_id, detail FROM payment_events WHERE "
                                "payment_id = $1::uuid ORDER BY created_at, (from_status IS NOT NULL), id", str(pid))
    return [dict(r) | {"detail": json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]}
            for r in rows]


async def send(uid, pid, hh=H_A) -> tuple[int, dict]:
    return await api(uid, "POST", pp(hh, f"/{pid}/send-to-custodian"))


async def refresh(uid, pid, hh=H_A) -> tuple[int, dict]:
    return await api(uid, "POST", pp(hh, f"/{pid}/refresh-from-custodian"))


async def detail(uid, pid, hh=H_A) -> tuple[int, dict]:
    return await api(uid, "GET", pp(hh, f"/{pid}"))


def actions(out: dict) -> list[str]:
    return [a.get("key") for a in ((out or {}).get("payment") or {}).get("actions") or []]


async def set_cap(conn, key: str, value: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true), "
                           "set_config('app.current_org_id', $1, true)", str(ORG_A))
        await CC.set_capability(conn, custodian_code=FIX_CAP, key=key, value=value, evidence_kind="assumed",
                                evidence="cpsim04 verify fixture", as_of=date.today(), actor_id=U_SA,
                                actor_org_id=ORG_A, is_super_admin=True)


async def set_adapters(conn, org, keys: list[str]) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES "
                           "($1, $2, $3::jsonb, 'cash', false) ON CONFLICT (org_id, setting_key) DO UPDATE SET "
                           "setting_value = EXCLUDED.setting_value", org, PAD.ORG_SETTING, json.dumps(keys))


async def sync_a(org=ORG_A, key="main") -> tuple[int, dict]:
    return await api(U_SA, "POST", cpu(key, f"/connections/{org}/sync"))


async def feed_rows(conn, refs: list[str]) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT x.id, x.transaction_type_code, x.net_amount, x.external_ref, x.source_system, p.account_id "
              "FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id WHERE x.org_id = "
              "ANY($1::uuid[]) AND x.external_ref = ANY($2::text[]) AND x.system_to IS NULL",
        [str(o) for o in FIXTURE_ORGS], refs)]


async def as_sim(conn, fn):
    """``fn(conn)`` in one transaction AS cpsim_app (the simulator's own path — orders, the ledger)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', '', true)")
        await conn.execute("SET LOCAL ROLE cpsim_app")
        return await fn(conn)


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


def book(key: str) -> dict:
    cash_row = lambda pid, acct, mv: {"position_id": pid, "account_id": acct, "as_of": THU.isoformat(),  # noqa: E731
                                      "row_type": "cash", "name": "Cash", "market_value": mv, "currency_code": "USD"}
    if key == "other":
        return {"households": [{"household_id": HHB, "name": "Verify household B"}],
                "accounts": [{"account_id": B1, "household_id": HHB, "name": "Verify B one"},
                             {"account_id": B2, "household_id": HHB, "name": "Verify B two"}],
                "positions": [cash_row("cp4v-p-b1", B1, "50000.00"), cash_row("cp4v-p-b2", B2, "0.00")]}
    if key in ("dom", "intl"):
        acct = BANK_DOM_ACCT if key == "dom" else BANK_INTL_ACCT
        return {"households": [{"household_id": f"cp4v-{key}-hh", "name": "Bank customers"}],
                "accounts": [{"account_id": acct, "household_id": f"cp4v-{key}-hh", "name": f"Verify {key} customer"}],
                "positions": [cash_row(f"cp4v-p-{key}", acct, "0.00")]}
    return {"households": [{"household_id": HH1, "name": "Verify household one"}],
            "accounts": [{"account_id": a, "household_id": HH1, "name": f"Verify {a}"} for a in (A1, A2, A3, A_REAL_SIM)],
            "positions": [cash_row("cp4v-p-a1", A1, format(CASH_A1, "f")), cash_row("cp4v-p-a2", A2, "0.00"),
                          cash_row("cp4v-p-a3", A3, format(CASH_A3, "f")),
                          cash_row("cp4v-p-real", A_REAL_SIM, "1000.00")]}


async def point(org, key) -> bool:
    c1, _ = await api(U_SA, "PUT", f"{_P}/org-access/{org}", {"allowed": True})
    c2, _ = await api(U_SA, "PUT", cpu(key, f"/connections/{org}"), {"point": True})
    return c1 == HTTP_OK and c2 == HTTP_OK


async def link(key: str, account, bank_account: str) -> tuple[int, dict]:
    """D6 — the Super Admin's link (Admin → Counterparties → bank → Received → Link)."""
    return await api(U_SA, "POST", cpu(key, "/links"), {"target_org_id": str(ORG_A), "account_id": str(account),
                                                        "bank_account_id": bank_account})


async def fake_numbers(conn) -> None:
    """Every fake bank number of the fixture banks (refreshed after a regenerate) — the full-number scan's targets."""
    rows = await sim(conn, "SELECT aba_wire, aba_ach, account_number, iban, intermediary_aba FROM "
                           "cpsim.counterparty_bank_details WHERE counterparty_id IN " + FIX_CP)
    for r in rows:
        for v in r.values():
            if v and v not in S["fake_numbers"]:
                S["fake_numbers"].append(v)


async def y_task1(conn):
    section("[Y] Discovery + CPSIM04_NOTES (D1–D11 as built; differences recorded); cpsim03 merged; contract §9")
    code, out, _e = run_cmd(["git", "log", "main", "--oneline"], REPO, timeout=120)
    check(code == 0 and bool(re.search(r"^\w+ merge cpsim03$", out, re.M)), "cpsim03 is merged to main ('merge "
          "cpsim03' in git log main)")
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
    section("[Y] Schema: cpsim transfers / events / cash ledger / consent flag (cpsim_app only); NO public change")
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
        trg = int(await platform(conn, "SELECT count(*) FROM pg_trigger WHERE tgrelid = $1 AND NOT tgisinternal", oid,
                                 fetch="fetchval"))
        check(privs == want and not others and trg == 1, f"cpsim.{t}: cpsim_app holds exactly {sorted(want)} (the "
              "shared withheld list); no other role; its guard trigger", f"{sorted(privs)} "
              f"{[r['rolname'] for r in others]} triggers={trg}")
    cons = {}
    for nsp, rel in (("cpsim", "transfers"), ("cpsim", "transfer_events"), ("cpsim", "cash_movements"),
                     ("cpsim", "book_positions"), ("cpsim", "book_transactions"), ("cpsim", "counterparty_behaviour"),
                     ("public", "payment_instructions"), ("public", "payment_events"),
                     ("public", "custodian_capabilities"), ("public", "trading_authority_grants"),
                     ("portfolio", "securities_global"), ("portfolio", "securities_global_prices"),
                     ("portfolio", "external_references")):
        cons[f"{nsp}.{rel}"] = await _constraints(conn, nsp, rel)
    n_read = sum(len(v) for v in cons.values())
    check(n_read > 0 and all(cons[k] for k in ("cpsim.transfers", "cpsim.transfer_events", "cpsim.cash_movements")),
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
    beh = await platform(conn, "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS t, a.attnotnull, "
                               "pg_get_expr(d.adbin, d.adrelid) AS dflt FROM pg_attribute a LEFT JOIN pg_attrdef d ON "
                               "d.adrelid = a.attrelid AND d.adnum = a.attnum WHERE a.attrelid = (" + REL + ") AND "
                               "a.attname = 'consent_required' AND NOT a.attisdropped", "cpsim",
                         "counterparty_behaviour", fetch="fetchrow")
    check(bool(beh and beh["t"] == "boolean" and beh["attnotnull"] and beh["dflt"] == "false"),
          "cpsim.counterparty_behaviour.consent_required boolean NOT NULL DEFAULT false (D8)", f"{dict(beh or {})}")
    refs = cons["cpsim.transfers"].get("transfers_bank_refs_chk") or ""
    check("[0-9]{4}" in refs and "[A-Z0-9]{4}" in refs, "transfers_bank_refs_chk keeps a bank reference to four "
          "characters (never a full routing number or IBAN)", refs[:200])
    pi = cons["public.payment_instructions"]
    check(not any("sent_channel" in d or "adapter" in d for k, d in pi.items() if k != "payment_instructions_sent_chk"),
          "public: no CHECK names sent_channel or adapter beyond the non-blank sent rule — the channel custodian_api "
          "and the adapter altruist_simulator needed no DDL")
    ev = cons["public.payment_events"].get("payment_events_action_chk") or ""
    hum = cons["public.payment_events"].get("payment_events_human_chk") or ""
    check(ev.count("'::text") == 18 and "actor_id IS NOT NULL" in hum and "'expire'::text" in hum,
          "payment_events_action_chk keeps its 18 actions and payment_events_human_chk is unchanged (D7 applies "
          "existing actions only)", f"{ev.count(chr(39) + '::text')} {hum[:120]}")
    xr = cons["portfolio.external_references"]
    check(not any("source_system" in d for d in xr.values() if "CHECK" in d),
          "portfolio.external_references.source_system has no CHECK — the CPSIM_BANK source needed no DDL")
    pols = await platform(conn, "SELECT tablename, cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                                "ANY($1::text[])", ["payment_instructions", "payment_events"])
    check(len(pols) == 8 and {(r["tablename"], r["cmd"]) for r in pols} == {
        (t, c) for t in ("payment_instructions", "payment_events") for c in ("SELECT", "INSERT", "UPDATE", "DELETE")},
        "RLS: a policy for every operation on the two payment tables, unchanged (4 each)", f"{len(pols)}")
    snap = _snapshot_columns()
    missing = []
    for t, cols_ in SEED_COLUMNS.items():
        have = snap.get(t) or snap.get(t.split(".")[-1]) or set()
        missing += [f"{t}.{c}" for c in cols_ if c not in have]
    check(not missing, "every INSERT's columns appear in docs/schema_snapshot.sql", f"{missing[:5]}")
    check(bool(snap.get("cpsim.transfers")) and "consent_required" in (snap.get("cpsim.counterparty_behaviour") or set()),
          "the snapshot was refreshed (cpsim.transfers and counterparty_behaviour.consent_required are in it)")
    S["schema_ok"] = read == 3 and n_read > 0


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1 OR id = $2::uuid",
                           f"{FIXTURE_ID_PREFIX}%", ORG_R, fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    s_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE id::text LIKE $1 OR name "
                                  "LIKE $2", f"{FIXTURE_ID_PREFIX}%", NAME + "%", fetch="fetchval"))
    i_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value = "
                                  "ANY($1::text[])", [CUSIP_EQ, TICKER_EQ], fetch="fetchval"))
    p_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE provider_key = $1",
                            PROVIDER_KEY, fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1", SLUG_PREFIX + "%",
                      fetch="fetchval"))
    k = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1", FIX_CAP,
                           fetch="fetchval"))
    return check(o == 0 and u == 0 and s_ == 0 and i_ == 0 and p_ == 0 and c == 0 and k == 0,
                 "the reserved prefixes (fixture ids, the out-of-prefix rolled-back org, the sign-in tag, 'CPSIM04 "
                 "VERIFY' security, its identifiers and price provider, the 'cpsim4verify-' slug, the fixture "
                 "custodian code) match no real row",
                 f"orgs={o} users={u} securities={s_} identifiers={i_} prices={p_} counterparties={c} caps={k}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CPSIM04 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, perms in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CPSIM04 verify — {rname}")
                for pn in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname, _tier) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CPSIM04 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, org, nm in ((E_A, ORG_A, "A family"), (E_B, ORG_B, "B family")):
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) VALUES "
                               "($1, $2, $3, 'active', 'individual'::entity_type)", eid, org, NAME + nm)
        for hh, org, eid in ((H_A, ORG_A, E_A), (H_B, ORG_B, E_B)):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + "household")
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, eid)
        for n, (acc, org, eid, hh, cust) in enumerate((
                (ACC_SRC, ORG_A, E_A, H_A, "ALTRUIST"), (ACC_DST, ORG_A, E_A, H_A, "ALTRUIST"),
                (ACC_DOM, ORG_A, E_A, H_A, "ZZ_CPSIM4V_BANK"), (ACC_INTL, ORG_A, E_A, H_A, "ZZ_CPSIM4V_BANK"),
                (ACC_REAL, ORG_A, E_A, H_A, "ALTRUIST"), (ACC_NOX, ORG_A, E_A, H_A, "ALTRUIST"),
                (ACC_B1, ORG_B, E_B, H_B, "ALTRUIST"), (ACC_B2, ORG_B, E_B, H_B, "ALTRUIST")), start=1):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, true, false)", acc, org, f"****40{n:02d}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), cust, eid, hh)
        for uid, (org, _r, _s, _n, tier) in USERS.items():
            if tier:
                await conn.execute("INSERT INTO trading_authority_grants (org_id, entity_id, user_id, authority_tier) "
                                   "VALUES ($1, $2, $3, $4)", org, E_A if org == ORG_A else E_B, uid, tier)
        for org, src, ext, acc in ((ORG_A, SIM_XWALK, A1, ACC_SRC), (ORG_A, SIM_XWALK, A2, ACC_DST),
                                   (ORG_A, REAL_XWALK, REAL_EXT, ACC_REAL), (ORG_A, SIM_XWALK, A_REAL_SIM, ACC_REAL),
                                   (ORG_B, SIM_XWALK, B1, ACC_B1), (ORG_B, SIM_XWALK, B2, ACC_B2)):
            await conn.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                               "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", org, src, ext, acc)
        await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, currency_code) "
                           "VALUES ($1, $2, 'equity', 'USD')", SEC_EQ, NAME + "EQ")
        for typ, val, prim in (("cusip", CUSIP_EQ, True), ("ticker", TICKER_EQ, False)):
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1, $2, $3, $4)", SEC_EQ, typ, val, prim)
        await conn.execute("INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                           "currency_code, price_type, source, provider_key) VALUES ($1, $2, $3::numeric, 'USD', "
                           "'close', 'cpsim04 verify', $4)", SEC_EQ, THU, format(PRICE_EQ, "f"), PROVIDER_KEY)
    for k in ("ach", "wire", "journal", "payment_status_feed"):
        await set_cap(conn, k, "yes")
    await set_cap(conn, "check", "no")
    for org in FIXTURE_ORGS:
        await set_adapters(conn, org, [PAD.SIMULATOR_KEY])
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS), "fixture orgs A, B and their users are "
                    "is_test = true (privileged insert path; the reserved prefix)", f"{len(os_)} {len(us)}")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT, "(Super Admin, initiator, approver / refresher, "
                                                                       "maker-who-approves, reader, low tier, org B)")
    ok_cp = True
    for key, typ in (("main", "altruist"), ("other", "altruist"), ("dom", "domestic_bank"),
                     ("intl", "international_bank")):
        code, b = await api(U_SA, "POST", _P, {"name": NAME + key, "institution_type": typ, "slug": SLUG_PREFIX + key})
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
        for kind in ("households", "accounts", "positions"):
            cu, ou = await upload(key, kind, book(key)[kind])
            ok_cp &= cu == HTTP_OK and (ou.get("counts") or {}).get("rejected") == 0
    await fake_numbers(conn)
    ok_pt = await point(ORG_A, "main") and await point(ORG_B, "other")
    set_sim_creds("main")
    ca, oa = await api(U_SA, "POST", cpu("main", f"/connections/{ORG_A}/connect"))
    set_sim_creds("other")
    cb, ob = await api(U_SA, "POST", cpu("other", f"/connections/{ORG_B}/connect"))
    set_sim_creds("main")
    l1, o1 = await link("dom", ACC_DOM, BANK_DOM_ACCT)
    l2, o2 = await link("intl", ACC_INTL, BANK_INTL_ACCT)
    await clock(conn)
    check(ok_cp and ok_pt and ca == HTTP_OK and cb == HTTP_OK and l1 == HTTP_OK and l2 == HTTP_OK,
          "four fixture counterparties (custodians main / other, the domestic and international banks) created, "
          "activated and given their books through the admin API; A → main and B → other pointed and connected "
          "through the admin's connect (the EXISTING /altruist path); the two bank accounts linked (D6)",
          f"cp={ok_cp} point={ok_pt} connA={ca} {dumps(oa)[:160]} connB={cb} {dumps(ob)[:160]} links={l1} {l2} "
          f"{dumps(o1)[:160]} {dumps(o2)[:160]}")
    S["seeded"] = bool(ok_test and ok_roles and ok_cp and ok_pt and ca == HTTP_OK and cb == HTTP_OK
                       and l1 == HTTP_OK and l2 == HTTP_OK)


# ── routing (D4) ────────────────────────────────────────────────────────────────────────────────────────────────
async def y_routing(conn):
    section("[Y] Routing: D4 all-or-manual with reasons (each condition removed in turn, identical instruction); "
            "real Altruist never; 'altruist' still NotConnected")
    d = await clock(conn)
    pid = await approved(conn, ACC_SRC, ACC_DST, "11.00", "journal", "routing")
    if not pid:
        return
    row = await payment(conn, pid)
    c, out = await detail(U_IN, pid)
    cust = ((out or {}).get("payment") or {}).get("custodian") or {}
    check(c == HTTP_OK and cust.get("send_available") is True and "send_to_custodian" in actions(out)
          and "mark_sent" in actions(out) and "sheet" in actions(out),
          "every D4 condition present → the simulator route is offered (Send to custodian) beside the manual route "
          "(the sheet + Confirm sent)", f"{c} {cust} {actions(out)}")

    async def decide(c_, src=ACC_SRC):
        src_acct = await PS.load_account(c_, ORG_A, src, d)
        route = await PC.simulator_route(c_, ORG_A, src_acct, {**row, "source_account_id": src})
        adapter = await PS.adapter_of(c_, ORG_A, {**row, "source_account_id": src})
        panel = await PC.panel(c_, ORG_A, {**row, "source_account_id": src})   # what the payment page shows
        return {"route": route, "key": adapter.key, "connected": adapter.connected,
                "route_reason": getattr(adapter, "route_reason", None), "cls": type(adapter).__name__,
                "panel": panel, "sim_xwalk": bool(await PC._xwalk(c_, ORG_A, src, PC.XWALK_SIMULATOR))}

    out_all, err = await rolled_back(conn, decide, org=ORG_A)
    check(err is None and out_all["route"]["ok"] is True and out_all["cls"] == "AltruistSimulatorPaymentAdapter"
          and out_all["key"] == PAD.SIMULATOR_KEY, "all present → services.payments.adapter_of chooses the "
          "simulator's adapter (key altruist_simulator)", f"{out_all} {err!r}")

    async def removed(label, condition, mutate, *, src=ACC_SRC):
        async def fn(c_):
            await mutate(c_)
            return await decide(c_, src)
        res, e = await rolled_back(conn, fn, org=ORG_A)
        ok = (e is None and res["route"]["ok"] is False and res["route"]["condition"] == condition
              and res["cls"] == "ManualPaymentAdapter" and res["route_reason"] == res["route"]["reason"])
        check(bool(ok), f"condition {condition} removed ({label}) → MANUAL with the reason "
              f"({((res or {}).get('route') or {}).get('reason', '')[:70]}…)", f"{res} {e!r}")
        # CPSIM04 FIX 1: the reason reaches the payment page (payment.custodian, the panel) — whenever the source is
        # crosswalked to a simulated custodian (incl. one ALSO crosswalked to real Altruist) the panel shows it with
        # Send never available; a source with no simulator crosswalk shows today's page exactly (no panel).
        if res is not None and e is None:
            p_ = res.get("panel")
            if res.get("sim_xwalk"):
                check(isinstance(p_, dict) and p_.get("send_available") is False
                      and p_.get("send_refused") == f"manual: {res['route']['reason']}" and p_.get("manual_offered"),
                      f"condition {condition} removed ({label}) → the payment page's custodian panel says why it is "
                      "manual (send_refused = the reason; Send not available; the manual route offered)", f"{p_}")
            else:
                check(p_ is None, f"condition {condition} removed ({label}) → no simulator crosswalk: the payment page "
                      "is today's exactly (no custodian panel)", f"{p_}")
        return res

    async def nothing(c_):
        return None

    async def real_xwalk(c_):
        await c_.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, record_type, "
                         "record_id) VALUES ($1, $2, $3, 'account', $4)", ORG_A, REAL_XWALK, "cp4v-real-src", ACC_SRC)

    async def no_xwalk(c_):
        await c_.execute("DELETE FROM portfolio.external_references WHERE org_id = $1 AND source_system = $2 AND "
                         "record_id = $3", ORG_A, SIM_XWALK, ACC_SRC)

    async def not_allowed(c_):
        await c_.execute("UPDATE org_settings SET setting_value = 'false'::jsonb WHERE org_id = $1 AND setting_key = $2",
                         ORG_A, SETTING)

    async def no_connection(c_):
        await c_.execute("UPDATE altruist_connections SET status = 'needs_reauth' WHERE org_id = $1 AND environment = "
                         "'simulator' AND system_to IS NULL", ORG_A)

    async def not_enabled(c_):
        await c_.execute("UPDATE org_settings SET setting_value = '[]'::jsonb WHERE org_id = $1 AND setting_key = $2",
                         ORG_A, PAD.ORG_SETTING)

    async def no_feed(c_):
        await CC.set_capability(c_, custodian_code=FIX_CAP, key="payment_status_feed", value="unknown",
                                evidence_kind="assumed", evidence="cpsim04 verify (rolled back)", as_of=date.today(),
                                actor_id=U_SA, actor_org_id=ORG_A, is_super_admin=True)

    async def no_rail(c_):
        await CC.set_capability(c_, custodian_code=FIX_CAP, key="journal", value="unknown",
                                evidence_kind="assumed", evidence="cpsim04 verify (rolled back)", as_of=date.today(),
                                actor_id=U_SA, actor_org_id=ORG_A, is_super_admin=True)

    async def no_dest(c_):
        await c_.execute("DELETE FROM portfolio.external_references WHERE org_id = $1 AND source_system = $2 AND "
                         "record_id = $3", ORG_A, SIM_XWALK, ACC_DST)

    rr = await removed("the source is ALSO crosswalked to real Altruist", "real_crosswalk", real_xwalk)
    await removed("no simulator crosswalk", "crosswalk", no_xwalk)
    await removed("counterparty_sim.allowed off", "allowed", not_allowed)
    await removed("the simulator connection needs re-authorisation", "connection", no_connection)
    await removed("cash.payment_adapters does not list altruist_simulator", "adapter_enabled", not_enabled)
    await removed("payment_status_feed 'unknown'", "status_feed", no_feed)
    await removed("the journal rail 'unknown'", "rail", no_rail)
    await removed("the destination is not crosswalked to the simulator or a bank", "destination", no_dest)
    real = await removed("a REAL-Altruist-crosswalked source (also crosswalked to the simulator)", "real_crosswalk",
                         nothing, src=ACC_REAL)
    check(bool(rr and real and "real Altruist" in rr["route"]["reason"]), "a real-ALTRUIST-crosswalked account NEVER "
          "takes the simulator adapter (whatever else holds)", f"{(real or {}).get('route')}")
    await _nontest(conn)

    async def altruist_enabled(c_):
        await c_.execute("UPDATE org_settings SET setting_value = '[\"altruist\"]'::jsonb WHERE org_id = $1 AND "
                         "setting_key = $2", ORG_A, PAD.ORG_SETTING)
        await c_.execute("DELETE FROM portfolio.external_references WHERE org_id = $1 AND source_system = $2 AND "
                         "record_id = $3", ORG_A, SIM_XWALK, ACC_REAL)
        a = await PS.adapter_of(c_, ORG_A, {**row, "source_account_id": ACC_REAL})
        return {"cls": type(a).__name__, "key": a.key, "connected": a.connected, "reason": getattr(a, "reason", "")}
    res, e = await rolled_back(conn, altruist_enabled, org=ORG_A)
    check(e is None and res["cls"] == "NotConnectedPaymentAdapter" and res["key"] == "altruist"
          and res["connected"] is False and "no payment adapter is connected" in res["reason"],
          "'altruist' in cash.payment_adapters with a real Altruist source still resolves to NotConnected (unchanged)",
          f"{res} {e!r}")
    check(PAD.adapter_for({"custodian_code": "ALTRUIST"}, set()).key == PAD.MANUAL and
          PAD.adapter_for({"custodian_code": "ALTRUIST"}, {PAD.SIMULATOR_KEY}).key == PAD.MANUAL,
          "a real Altruist account resolves MANUAL whether or not altruist_simulator is enabled (the key is never "
          "altruist_simulator for it)")
    try:
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c_:
                await AT.transfer_session(c_, org_id=str(ORG_A), environment="sandbox")
        check(False, "the transfers client refuses a real environment before any request")
    except AO.AltruistOAuthError as exc:
        check(AT.NO_CONTRACT in str(exc), "the transfers client refuses sandbox / production before anything is read "
              "('no published Altruist money-movement contract')", str(exc))


async def _nontest(conn):
    """The NON-test org R, inside ONE rolled-back transaction: a DEMO household's payment takes the simulator route;
    a plain household's is manual with the not-a-demo-household reason (condition demo_household)."""
    from urllib.parse import parse_qs, urlsplit

    from services.altruist_oauth import build_authorize_url, exchange_code_for_tokens, store_new_connection

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
                        "CPSIM04 Verify Org R (rolled back)", f"{TAG}-r")
        is_test = await c.fetchval("SELECT is_test FROM organizations WHERE id = $1", ORG_R)
        for eid, nm, tags in ((E_R_DEMO, "demo", ["demo-cpsim4verify"]), (E_R_PLAIN, "plain", [])):
            await c.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, tags) VALUES ($1, $2, "
                            "$3, 'active', 'individual'::entity_type, $4::text[])", eid, ORG_R, NAME + nm, tags)
        for hh, e in ((H_R_DEMO, E_R_DEMO), (H_R_PLAIN, E_R_PLAIN)):
            await c.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, ORG_R, NAME + "hh")
            await c.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        for acc, eid, hh, n, ext in ((ACC_R_DEMO, E_R_DEMO, H_R_DEMO, 81, A1), (ACC_R_DEMO2, E_R_DEMO, H_R_DEMO, 82, A2),
                                     (ACC_R_PLAIN, E_R_PLAIN, H_R_PLAIN, 83, A3),
                                     (ACC_R_PLAIN2, E_R_PLAIN, H_R_PLAIN, 84, A_REAL_SIM)):
            await c.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary) VALUES ($1, $2, $3, "
                "$4, 'ALTRUIST', 'individual', 'taxable', $5, $6, true)", acc, ORG_R, f"****40{n}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), eid, hh)
            await c.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                            "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", ORG_R, SIM_XWALK, ext, acc)
        for k, v in ((SETTING, "true"), (SETTING_CP, json.dumps(SLUG_PREFIX + "main")),
                     (PAD.ORG_SETTING, json.dumps([PAD.SIMULATOR_KEY]))):
            await c.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES "
                            "($1, $2, $3::jsonb, 'counterparty_sim', false)", ORG_R, k, v)
        await c.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_R))
        await store_new_connection(c, org_id=str(ORG_R), environment="simulator", client_id=S["client_ids"]["main"],
                                   client_secret=S["keys"]["main"], tokens=tok, connected_by=None)
        row = {"rail": "journal", "amount": Decimal("1.00")}
        demo = await PC.simulator_route(c, ORG_R, {"id": ACC_R_DEMO, "household_id": H_R_DEMO},
                                        {**row, "destination_account_id": ACC_R_DEMO2})
        plain = await PC.simulator_route(c, ORG_R, {"id": ACC_R_PLAIN, "household_id": H_R_PLAIN},
                                         {**row, "destination_account_id": ACC_R_PLAIN2})
        return {"is_test": is_test, "demo": demo, "plain": plain}
    out, err = await rolled_back(conn, fn)
    check(err is None and out is not None and out["is_test"] is False and out["demo"]["ok"] is True and
          out["plain"]["ok"] is False and out["plain"]["condition"] == "demo_household",
          "a NON-test org (rolled back): a DEMO household's account moves money through the simulator; a plain "
          "household's account routes manual with 'not a demo household' (condition demo_household)", f"{out} {err!r}")
    left = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1::uuid", ORG_R, fetch="fetchval"))
    check(left == 0, "the non-test org and its demo rows were never committed", f"{left}")


# ── governance ──────────────────────────────────────────────────────────────────────────────────────────────────
async def y_governance(conn):
    section("[Y] Governance: the S30 floor unchanged; Send / Refresh person actions gated both ways with both "
            "authority checks; nothing sends at submit / approval; cash06 rules still draft only; no unattended path")
    vd = await clock(conn)
    n0 = int(await sim(conn, "SELECT count(*) FROM cpsim.transfers WHERE counterparty_id IN " + FIX_CP,
                       fetch="fetchval"))
    pid = await approved(conn, ACC_SRC, ACC_DST, "12.00", "journal", "governance")
    n1 = int(await sim(conn, "SELECT count(*) FROM cpsim.transfers WHERE counterparty_id IN " + FIX_CP,
                       fetch="fetchval"))
    check(bool(pid) and n1 == n0, "nothing is sent at submit or at approval (no transfer at any fixture counterparty)",
          f"{n0} → {n1}")
    # the S30 floor: a maker who HOLDS approve_payment (the refusal is services.sod's) — and a Super Admin maker
    for maker, who in ((U_MA, "a maker who holds approve_payment"), (U_SA, "a Super Admin maker")):
        c0, o0 = await drafted(maker, ACC_SRC, ACC_DST, "13.00", "journal", vd, f"{NAME}floor {who}")
        p = ((o0 or {}).get("payment") or {}).get("id")
        c1, _o1 = await act(maker, p, "submit") if p else (0, {})
        c2, o2 = await act(maker, p, "approve") if p else (0, {})
        st = (await payment(conn, p)).get("status") if p else None
        sd = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE item_id = $1::text AND outcome <> "
                                      "'allowed'", str(p), fetch="fetchval")) if p else 0
        check(c0 == HTTP_CREATED and c1 == HTTP_OK and c2 == HTTP_FORBIDDEN and st == "pending_approval",
              f"the S30 floor: {who} cannot approve their own payment (403; still awaiting a second person)",
              f"{c0} {c1} {c2} {st} {dumps(o2)[:200]}")
        if maker == U_MA:
            check(sd >= 1, "… and the refusal is services.sod's (a sod_decisions row for the item, compared as text)",
                  f"{sd}")
    # Send refused: before approval; without initiate_payment; a failing authority check (both reasons); another org
    c0, o0 = await drafted(U_IN, ACC_SRC, ACC_DST, "14.00", "journal", vd, f"{NAME}draft only")
    pd = ((o0 or {}).get("payment") or {}).get("id")
    cs, os_ = await send(U_IN, pd) if pd else (0, {})
    check(cs == HTTP_CONFLICT and "approved" in dumps(os_), "Send before approval → 409 (allowed from approved only)",
          f"{cs} {dumps(os_)[:200]}")
    if not pid:
        return
    for uid, who in ((U_RD, "a reader without initiate_payment"), (U_AP, "an approver without initiate_payment")):
        c, o = await send(uid, pid)
        check(c == HTTP_FORBIDDEN, f"Send by {who} → 403 (the same VALID request)", f"{c} {dumps(o)[:200]}")
    await platform(conn, "UPDATE accounts SET is_held_away = true WHERE id = $1::uuid", ACC_SRC, fetch="execute")
    try:
        c, o = await send(U_LT, pid)
    finally:
        await platform(conn, "UPDATE accounts SET is_held_away = false WHERE id = $1::uuid", ACC_SRC, fetch="execute")
    reasons = ((o or {}).get("detail") or {}).get("reasons") or [] if isinstance((o or {}).get("detail"), dict) else []
    check(c == HTTP_FORBIDDEN and len(reasons) == 2 and any(r.startswith("trading authority") for r in reasons)
          and any(r.startswith("account authority") for r in reasons),
          "Send with a failing authority check → 403 naming BOTH reasons (trading tier 'inquiry' and an account "
          "Hollisworks does not manage) — refused BEFORE anything is sent", f"{c} {dumps(o)[:300]}")
    cB, oB = await api(U_INB, "POST", pp(H_A, f"/{pid}/send-to-custodian"))
    cM, oM = await api(U_INB, "POST", pp(H_A, f"/{uuid_missing()}/send-to-custodian"))
    check(cB == HTTP_NOT_FOUND and cM == HTTP_NOT_FOUND, "Send for ANOTHER org's payment → 404, identical to a missing "
          "one", f"{cB} {cM}")
    n2 = int(await sim(conn, "SELECT count(*) FROM cpsim.transfers WHERE client_transfer_id = $1 AND counterparty_id "
                             "IN " + FIX_CP, str(pid), fetch="fetchval"))
    check(n2 == 0 and (await payment(conn, pid)).get("status") == "approved",
          "no refused Send reached the custodian (no transfer for the payment; still approved)", f"{n2}")
    cR, oR = await refresh(U_RD, pid)
    check(cR == HTTP_FORBIDDEN, "Refresh by a reader (neither initiate nor approve) → 403", f"{cR} {dumps(oR)[:200]}")
    # CPSIM04 FIX 1 (N2): the custodian-transfer read is STAFF money detail — any(initiate_payment | approve_payment),
    # never a bare view_portfolio holder — and docs/AUTHZ1_ROUTES.json now states exactly that gate (the generator had
    # taken the union with _open's looser any(view_portfolio | …) and expected member / staff / other_org admitted).
    cT, oT = await api(U_RD, "GET", pp(H_A, f"/{pid}/custodian-transfer"))
    cT2, _oT2 = await api(U_AP, "GET", pp(H_A, f"/{pid}/custodian-transfer"))
    cT3, _oT3 = await api(U_INB, "GET", pp(H_A, f"/{pid}/custodian-transfer"))
    check(cT == HTTP_FORBIDDEN and "initiate_payment" in dumps(oT) and cT2 == HTTP_CONFLICT and cT3 == HTTP_NOT_FOUND,
          "GET …/custodian-transfer: a view_portfolio-only reader → 403 naming initiate_payment; the approver passes "
          "the gate (409 — this payment was never sent; the state refuses, as Refresh); "
          "ANOTHER org's initiator → 404", f"{cT} {dumps(oT)[:160]} {cT2} {cT3}")
    _rj = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    _ex = {(r.get("path"), m): r.get("expect") for r in _rj.get("routes", []) for m in (r.get("methods") or [])}
    _want = {"rule": "perm", "any_of": ["approve_payment", "initiate_payment"]}
    _base = "/api/v1/cash/households/{household_id}/payments/{payment_id}"
    check(_ex.get((_base + "/custodian-transfer", "GET")) == _want
          and _ex.get((_base + "/refresh-from-custodian", "POST")) == _want,
          "docs/AUTHZ1_ROUTES.json expects exactly any(initiate_payment | approve_payment) for the custodian-transfer "
          "read and Refresh (view_portfolio alone is refused — the matrix's member / staff / other_org rows)",
          f"{_ex.get((_base + '/custodian-transfer', 'GET'))} {_ex.get((_base + '/refresh-from-custodian', 'POST'))}")
    cR2, oR2 = await refresh(U_AP, pid)
    check(cR2 == HTTP_CONFLICT, "Refresh of a payment that was never sent → 409 (the gate passes, the state refuses)",
          f"{cR2} {dumps(oR2)[:200]}")
    # nothing unattended can send or refresh
    va = WA.VERB_AUTONOMY
    check(va.get("cash.payment_send", {}).get("class") == WA.NEVER and va.get("cash.payment_refresh", {}).get("class")
          == WA.NEVER and va.get("cash.payment_send", {}).get("money") is True and va.get("cash.payment_refresh", {})
          .get("money") is True, "cash.payment_send and cash.payment_refresh are declared NEVER (money) in "
          "services.workflow_autonomy")
    check(sod.VERB_DUTIES.get("cash.payment_send") == "write" and sod.VERB_DUTIES.get("cash.payment_refresh") == "write",
          "services.sod.VERB_DUTIES names both as 'write' duties")
    # CPSIM04 FIX 1 (G): the registry is an ActionRegistry (not iterable) — enumerate it through its public API,
    # REGISTRY.all(), after register_all() (what the app's lifespan does), exactly as cpsim03's scan reads it.
    from services.assistant_actions import register_all as _register_all
    if not REGISTRY.all():
        _register_all()
    acts = REGISTRY.all()
    keys = [a.key for a in acts]
    pay_keys = [k for k in keys if any(w in k for w in ("payment", "transfer", "custodian"))]
    check(bool(acts) and all(REGISTRY.get(k) is None for k in ("cash.payment_send", "cash.payment_refresh"))
          and not any(("send" in k or "refresh" in k or "settle" in k) and ("payment" in k or "transfer" in k)
                      for k in pay_keys),
          f"neither is registered as an action ({len(acts)} registered actions enumerated; no workflow action, S29b "
          "step, agent or trigger can name them)", f"{pay_keys[:5]}")
    money_rx = re.compile(r"payment_custodian|altruist_transfers|\bsend_to_custodian\(|\brefresh_from_custodian\(|"
                          r"\bmark_sent\(|\bsettle\(|\backnowledge\(|\brecord_return\(|\bcustodian_reject\(|"
                          r"\bconsent_requested\(|/v2/transfers")

    def _closure(fn) -> str:
        """The source of ``fn`` plus every function of ITS OWN module it calls, transitively (one module deep)."""
        mod = sys.modules.get(getattr(fn, "__module__", "") or "")
        seen, todo, out = set(), [fn], []
        while todo:
            f = todo.pop()
            name = getattr(f, "__name__", None)
            if name in seen:
                continue
            seen.add(name)
            try:
                src = _code_only(inspect.getsource(f))
            except (OSError, TypeError):
                out.append(f"<no source for {name}>")
                continue
            out.append(src)
            for callee in set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\(", src)):
                g = getattr(mod, callee, None) if mod else None
                if inspect.isfunction(g) and g.__module__ == getattr(mod, "__name__", None) and callee not in seen:
                    todo.append(g)
        return "\n".join(out)

    # positive control: the same scan DOES see the money path where it really is (else every "no hit" proves nothing)
    ctl = [nm for nm, f in (("payment_custodian.send_to_custodian", PC.send_to_custodian),
                            ("payment_custodian.refresh_from_custodian", PC.refresh_from_custodian))
           if money_rx.search(_closure(f))]
    check(len(ctl) == 2, "the scan's positive control: it detects the money path in payment_custodian's own Send and "
          "Refresh (so a clean result below is real)", f"{ctl}")
    hits, nosrc = [], []
    for a in acts:
        for h in (a.handler, a.draft_handler):
            if h is None:
                continue
            src = _closure(h)
            if "<no source for" in src:
                nosrc.append(a.key)
            m = money_rx.search(src)
            if m:
                hits.append(f"{a.key}:{getattr(h, '__name__', h)}:{m.group(0)}")
    inv = [a.key for a in acts if a.workflow_invocable]
    check(not hits and not nosrc, f"every registered action ({len(acts)}; {len(inv)} workflow-invocable, incl. "
          f"{', '.join(sorted(inv)[:4])}…) — handler and draft handler, with every same-module function they call — "
          "never sends, refreshes, settles or records a custodian status for a payment", f"{hits[:5]} {nosrc[:5]}")
    import importlib
    sweeps, sweep_hits = [], []
    for p in sorted((API_DIR / "services").glob("*.py")):
        if not re.search(r"^(?:async )?def [A-Za-z_]*sweep[A-Za-z_]*\(", p.read_text(encoding="utf-8"), re.M):
            continue
        mod = importlib.import_module(f"services.{p.stem}")
        for nm, f in inspect.getmembers(mod, inspect.isfunction):
            if "sweep" in nm and f.__module__ == mod.__name__:
                sweeps.append(f"{p.stem}.{nm}")
                m = money_rx.search(_closure(f))
                if m:
                    sweep_hits.append(f"{p.stem}.{nm}:{m.group(0)}")
    check(len(sweeps) >= 5 and "payments.sweep_tick" in sweeps and not sweep_hits,
          f"every sweep ({len(sweeps)}: {', '.join(sweeps[:6])}…), with every same-module function it calls, never "
          "sends, refreshes or settles money (the payment sweep only expires / flags)", f"{sweep_hits[:5]}")
    tick = API_DIR / "workflow_scheduler_tick.py"
    tsrc = _code_only(tick.read_text(encoding="utf-8")) if tick.exists() else ""
    check(bool(tsrc) and not money_rx.search(tsrc),
          "the scheduler tick (apps/api/workflow_scheduler_tick.py — every trigger, sweep and scheduled run starts "
          "here) names no send / refresh / settle path", f"exists={tick.exists()} {money_rx.search(tsrc)}")
    rf = va.get("cash.rule_fire", {})
    check(rf.get("class") == WA.ELIGIBLE_WITH_CAP and rf.get("draft_only") is True and rf.get("submits_for_approval")
          is True, "a cash06 rule still only drafts + submits (S29b eligible_with_cap, draft only)", f"{rf}")
    # every reference to a send / refresh, classified by MODULE: PC / payment_custodian = the money route (cpsim04),
    # CX / custodian_execution = the trade route (cpsim03, its own verify). Unclassified = FAIL.
    money, trade, unknown = [], [], []
    files = (sorted((API_DIR / "services").rglob("*.py")) + sorted((API_DIR / "routers").glob("*.py"))
             + sorted((API_DIR / "cpsim").glob("*.py")) + [tick, API_DIR / "main.py"])
    for p in files:
        src = _code_only(p.read_text(encoding="utf-8")) if p.exists() else ""
        rel = str(p.relative_to(API_DIR))
        imports_pc = bool(re.search(r"from services\.payment_custodian import[^\n]*\b(send_to_custodian|"
                                    r"refresh_from_custodian)\b", src))
        for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_.]*\.)?\b(send_to_custodian|refresh_from_custodian)\(", src):
            line = src[:m.start()].rsplit("\n", 1)[-1]
            if re.match(r"\s*(async\s+)?def\s*$", line):
                continue
            q = (m.group(1) or "").rstrip(".")
            fn_ = src[:m.start()].rsplit("\nasync def ", 1)[-1].split("(", 1)[0]
            ref = f"{rel}:{fn_}:{m.group(2)}"
            if q in ("PC", "payment_custodian", "services.payment_custodian") or (not q and imports_pc):
                money.append(ref)
            elif q in ("CX", "custodian_execution", "services.custodian_execution"):
                trade.append(ref)
            else:
                unknown.append(f"{ref} (qualifier {q!r})")
    rp = _code_only(_src("apps/api/routers/payments.py"))
    routed = all(re.search(r"@router\.post\([^)]*\)\s*\nasync def " + re.escape(r.split(":")[1]) + r"\(", rp)
                 for r in money)
    check(sorted(money) == ["routers/payments.py:payment_refresh_from_custodian_route:refresh_from_custodian",
                            "routers/payments.py:payment_send_to_custodian_route:send_to_custodian"] and routed
          and not unknown,
          "code scan (every services / routers / cpsim module, main.py and the scheduler tick), by MODULE: the payment "
          "Send / Refresh (services.payment_custodian) are called ONLY by the two person routes in routers/payments.py "
          "(@router.post); every other send is cpsim03's trade send (custodian_execution); nothing unclassified",
          f"money={money} trade={trade[:4]} unknown={unknown[:4]} routed={routed}")
    for f in ("workflow_scheduler_tick.py", "services/cash_rules.py", "services/action_registry.py",
              "services/workflow_engine.py", "services/domain_events.py"):
        pth = API_DIR / f
        src = _code_only(pth.read_text(encoding="utf-8")) if pth.exists() else None
        if src is None:
            print(f"[INFO] {f} does not exist (not scanned)")
            continue
        check("payment_custodian" not in src and "altruist_transfers" not in src,
              f"{f} never reaches the custodian-payment code")


def uuid_missing() -> str:
    return "99000000-0000-0000-0000-0000c954ffff"


# ── the journal, end to end ─────────────────────────────────────────────────────────────────────────────────────
async def sent_ok(conn, pid, label: str, *, expect_status: str | None = None) -> dict:
    """Send (U_IN) → 200; the instruction is SENT through mark_sent (custodian_api, altruist_simulator, the transfer
    id as external_ref, a mark_sent event by the sender). Returns the custodian's transfer row (cpsim)."""
    c, o = await send(U_IN, pid)
    row = await payment(conn, pid)
    t = await transfer_of(conn, pid)
    ev = [e for e in await pevents(conn, pid) if e["action"] == "mark_sent"]
    ok = (c == HTTP_OK and row.get("status") == "sent" and row.get("sent_channel") == PAD.CUSTODIAN_API
          and row.get("adapter") == PAD.SIMULATOR_KEY and row.get("external_ref") == t.get("transfer_id")
          and str(row.get("sent_by")) == str(U_IN) and len(ev) == 1 and str(ev[0]["actor_id"]) == str(U_IN)
          and (expect_status is None or t.get("status") == expect_status))
    check(bool(ok), f"{label}: Send (a person) → POST /v2/transfers → SENT through mark_sent (channel custodian_api, "
          f"adapter altruist_simulator, the transfer id stored, one mark_sent event by the sender)"
          + (f"; the custodian's transfer is {expect_status}" if expect_status else ""),
          f"{c} {dumps(o)[:300]} {row.get('status')} {row.get('sent_channel')} {row.get('adapter')} "
          f"{t.get('status')} {t.get('reject_reason')}")
    return t


async def refreshed(conn, pid, label: str, want: list[str], *, uid=U_AP) -> dict:
    """Refresh (default: the approver) → exactly ``want`` applied, in order, each a payment_events row whose actor is
    the refresher."""
    before = len(await pevents(conn, pid))
    c, o = await refresh(uid, pid)
    got = [a.get("action") for a in (o or {}).get("applied") or []]
    evs = (await pevents(conn, pid))[before:]
    names = {"acknowledge": "acknowledge", "settle": "settle", "consent_requested": "consent_requested",
             "custodian_reject": "custodian_reject", "record_return": "return"}
    ok = (c == HTTP_OK and got == want and [e["action"] for e in evs] == [names[w] for w in want]
          and all(str(e["actor_id"]) == str(uid) for e in evs))
    check(bool(ok), f"{label}: Refresh (a person) applies exactly {want or 'nothing'} in order, each a payment_events "
          "row with that person as the actor", f"{c} applied={got} events={[(e['action'], str(e['actor_id'])[-4:]) for e in evs]} "
          f"pending={(o or {}).get('pending')} {dumps(o)[:200] if c != HTTP_OK else ''}")
    return o or {}


async def y_journal(conn):
    section("[Y] Journal end to end: send → acknowledge → settle (refresh by a person) → sync → matches() both legs → "
            "reconcile(custodian_data) → close")
    d = await clock(conn)
    amt = Decimal("2500.00")
    a1, a2 = await cash(conn, "main", A1), await cash(conn, "main", A2)
    pid = await approved(conn, ACC_SRC, ACC_DST, format(amt, "f"), "journal", "journal")
    if not pid:
        return
    t = await sent_ok(conn, pid, "journal", expect_status="settled")
    tid = t.get("transfer_id") or ""
    check(t.get("rail") == "journal" and Decimal(t.get("amount") or 0) == amt and t.get("value_date") == d
          and t.get("from_account_external_id") == A1 and t.get("to_account_external_id") == A2,
          "the custodian holds exactly what was approved: journal, the amount (Decimal), the value date, the "
          "crosswalked accounts", f"{t}")
    o = await refreshed(conn, pid, "journal", ["acknowledge", "settle"])
    row = await payment(conn, pid)
    check(row.get("status") == "settled" and row.get("settled_on") == d and row.get("provisional_until") is None
          and str(row.get("acknowledged_by")) == str(U_AP) and str(row.get("settled_by")) == str(U_AP),
          "the payment is settled on the custodian's settle date (a journal is final: not provisional); acknowledged "
          "and settled BY the refreshing person", f"{row.get('status')} {row.get('settled_on')} "
          f"{row.get('provisional_until')}")
    b1, b2 = await cash(conn, "main", A1), await cash(conn, "main", A2)
    check(a1 is not None and a2 is not None and b1 == a1 - amt and b2 == a2 + amt,
          "the simulator's cash ledger moved exactly (Decimal): the source −2500.00, the destination +2500.00",
          f"{a1}→{b1} {a2}→{b2}")
    mv = await movements(conn, tid)
    check(sorted((m["movement_kind"], m["amount"]) for m in mv) == [("transfer_credit", amt), ("transfer_debit", -amt)],
          "cpsim.cash_movements: one debit and one credit for the transfer (signed, Decimal)",
          f"{[(m['movement_kind'], m['amount']) for m in mv]}")
    cs, osync = await sync_a()
    rows = await feed_rows(conn, [f"{tid}-out", f"{tid}-in"])
    by = {r["external_ref"]: r for r in rows}
    out_, in_ = by.get(f"{tid}-out") or {}, by.get(f"{tid}-in") or {}
    check(cs == HTTP_OK and out_.get("transaction_type_code") == "sell" and in_.get("transaction_type_code") == "buy"
          and dec(out_.get("net_amount")) == amt and dec(in_.get("net_amount")) == amt
          and str(out_.get("account_id")) == str(ACC_SRC) and str(in_.get("account_id")) == str(ACC_DST)
          and out_.get("source_system") == "altruist_simulator",
          "the cpsim02 sync brought the D2 types: transfer_out → sell on the source, transfer_in → buy on the "
          "destination (ledger tag altruist_simulator)", f"{cs} {dumps(osync)[:200]} {rows}")
    cm, om = await api(U_IN, "GET", pp(H_A, f"/{pid}/matches"))
    legs = {m["id"]: m["leg"] for m in (om or {}).get("matches") or []}
    check(cm == HTTP_OK and legs.get(str(out_.get("id"))) == "out" and legs.get(str(in_.get("id"))) == "in",
          "matches() finds BOTH legs (the source's out, the destination's in — direction by TYPE_MAP sign)",
          f"{cm} {legs}")
    cv, ov = await api(U_AP, "GET", pp(H_A, f"/{pid}/custodian-transfer"))
    check(cv == HTTP_OK and set(ov.get("preselect_transaction_ids") or []) == {str(out_.get("id")), str(in_.get("id"))}
          and (ov.get("transfer") or {}).get("transfer_id") == tid and ov.get("simulated") is True,
          "the custodian-transfer read preselects exactly the transfer's two matched feed rows for the reconcile "
          "picker", f"{cv} {dumps(ov)[:300]}")
    cr, orr = await act(U_IN, pid, "reconcile", method="custodian_data",
                        transaction_ids=[str(out_.get("id")), str(in_.get("id"))])
    cc, oc = await act(U_IN, pid, "close")
    row = await payment(conn, pid)
    check(cr == HTTP_OK and cc == HTTP_OK and row.get("status") == "closed" and row.get("reconcile_method") ==
          "custodian_data" and {str(x) for x in row.get("reconciled_transaction_ids") or []} == {
              str(out_.get("id")), str(in_.get("id"))},
          "reconcile(custodian_data) with the matched rows → reconciled; close → closed (the loop is closed by people)",
          f"{cr} {dumps(orr)[:200]} {cc} {dumps(oc)[:200]} {row.get('status')}")
    S["journal"] = {"pid": pid, "tid": tid}


# ── banks ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def ensure_links(conn) -> bool:
    l1, _ = await link("dom", ACC_DOM, BANK_DOM_ACCT)
    l2, _ = await link("intl", ACC_INTL, BANK_INTL_ACCT)
    await fake_numbers(conn)
    return l1 == HTTP_OK and l2 == HTTP_OK


async def y_banks(conn):
    section("[Y] Banks: domestic wire and international wire credited at the bank; ACH settles the next business day; "
            "wrong ABA / BIC / IBAN rejected with the bank's reason; insufficient funds rejected")
    if not await ensure_links(conn):
        check(False, "the bank accounts are linked (D6) before the bank section")
        return
    d = await clock(conn)
    for key, acc, bank_acct, amt, rail in (("dom", ACC_DOM, BANK_DOM_ACCT, Decimal("1000.00"), "wire"),
                                           ("intl", ACC_INTL, BANK_INTL_ACCT, Decimal("1100.00"), "wire")):
        before = await cash(conn, key, bank_acct) or Decimal(0)
        pid = await approved(conn, ACC_SRC, acc, format(amt, "f"), rail, f"{key} wire")
        if not pid:
            continue
        t = await sent_ok(conn, pid, f"{key} wire", expect_status="settled")
        await refreshed(conn, pid, f"{key} wire", ["acknowledge", "settle"])
        after = await cash(conn, key, bank_acct)
        mv = await movements(conn, t.get("transfer_id") or "")
        check(after == before + amt and any(m["movement_kind"] == "transfer_credit" and m["counterparty_id"] ==
                                            UUID(S["cp"][key]) and m["account_external_id"] == bank_acct for m in mv),
              f"the {key} bank credited its book account exactly (+{amt}) after checking its FAKE references "
              f"({'routing last four' if key == 'dom' else 'BIC + IBAN last four'})", f"{before}→{after}")
        S.setdefault("bank_pids", []).append(pid)
    pid = await approved(conn, ACC_SRC, ACC_DOM, "1200.00", "ach", "dom ach")
    if pid:
        t = await sent_ok(conn, pid, "domestic ACH", expect_status="acknowledged")
        check(t.get("settle_on") is not None and t["settle_on"] > d, "an ACH is acknowledged at receipt (the source "
              "debited) and settles the next business day", f"{t.get('settle_on')} vs {d}")
        o = await refreshed(conn, pid, "domestic ACH (before settlement)", ["acknowledge"])
        check(bool((o or {}).get("pending") is None or "after today" not in str(o.get("pending"))),
              "… nothing more applies until the custodian settles (never jumped ahead)", f"{o.get('pending')}")
        await advance(conn)
        await refreshed(conn, pid, "domestic ACH (after the clock advance)", ["settle"])
        row = await payment(conn, pid)
        check(row.get("status") == "settled" and row.get("provisional_until") is not None,
              "the ACH is settled PROVISIONALLY (through its return window)", f"{row.get('provisional_until')}")
    pid = await approved(conn, ACC_SRC, ACC_DST, "999999.00", "journal", "insufficient funds")
    if pid:
        await sent_ok(conn, pid, "insufficient funds", expect_status="rejected")
        await refreshed(conn, pid, "insufficient funds", ["custodian_reject"])
        row = await payment(conn, pid)
        check(row.get("status") == "rejected" and row.get("custodian_rejected_reason") == "insufficient funds",
              "more than the source holds → the custodian rejects 'insufficient funds' → custodian_reject via Refresh",
              f"{row.get('status')} {row.get('custodian_rejected_reason')}")
    # wrong details: the bank regenerates its fake details AFTER the link — the stale reference is rejected
    for key, acc, word in (("dom", ACC_DOM, "routing number does not match"),
                           ("intl", ACC_INTL, "does not match the receiving bank")):
        cg, _o = await api(U_SA, "POST", cpu(key, "/bank/regenerate"))
        await fake_numbers(conn)
        pid = await approved(conn, ACC_SRC, acc, "1300.00", "wire", f"{key} stale reference")
        if not pid:
            continue
        await sent_ok(conn, pid, f"{key} wire with a stale reference", expect_status="rejected")
        await refreshed(conn, pid, f"{key} wire with a stale reference", ["custodian_reject"])
        row = await payment(conn, pid)
        check(cg == HTTP_OK and row.get("status") == "rejected" and word in (row.get("custodian_rejected_reason") or ""),
              f"a wrong {'ABA' if key == 'dom' else 'BIC / IBAN'} → rejected with the receiving bank's reason ('{word}')"
              " → custodian_reject via Refresh", f"{cg} {row.get('status')} {row.get('custodian_rejected_reason')}")
    await ensure_links(conn)   # re-link with the new references for the sections after this one


# ── ACH returns ─────────────────────────────────────────────────────────────────────────────────────────────────
async def ach_return(conn, label: str, amt: Decimal, code: str, *, receiver: bool) -> None:
    pid = await approved(conn, ACC_SRC, ACC_DOM, format(amt, "f"), "ach", label)
    if not pid:
        return
    a1 = await cash(conn, "main", A1)
    bk0 = await cash(conn, "dom", BANK_DOM_ACCT) or Decimal(0)
    t = await sent_ok(conn, pid, label, expect_status="acknowledged")
    tid = t.get("transfer_id") or ""
    if receiver:
        await advance(conn)                      # settles AND returns at once (the bank's return after 0 days)
        await refreshed(conn, pid, label, ["acknowledge", "settle", "record_return"])
    else:
        await refreshed(conn, pid, f"{label} (acknowledged)", ["acknowledge"])
        await advance(conn)
        await refreshed(conn, pid, f"{label} (settled)", ["settle"])
        for _ in range(RETURN_DAYS):
            await advance(conn)
        await refreshed(conn, pid, f"{label} (returned after {RETURN_DAYS} business days)", ["record_return"])
    row = await payment(conn, pid)
    t2 = await transfer_of(conn, pid)
    a1b, bk1 = await cash(conn, "main", A1), await cash(conn, "dom", BANK_DOM_ACCT)
    mv = {(m["movement_kind"], m["amount"]) for m in await movements(conn, tid)}
    check(row.get("status") == "returned" and row.get("return_code") == code and t2.get("status") == "returned"
          and t2.get("return_source") == ("receiver" if receiver else "sender"),
          f"{label}: settled → returned with {code} (from the {'receiving bank' if receiver else 'sender'}'s "
          "behaviour), recorded through record_return by a person", f"{row.get('status')} {row.get('return_code')} "
          f"{t2.get('status')} {t2.get('return_source')}")
    check(a1b == a1 and bk1 == bk0 and {("transfer_debit", -amt), ("transfer_credit", amt), ("return_credit", amt),
                                       ("return_debit", -amt)} == mv,
          f"{label}: BOTH legs reversed in the book (the source and the bank account back where they were, Decimal)",
          f"A1 {a1}→{a1b} bank {bk0}→{bk1} {sorted(mv)}")
    if not receiver:
        due = t2.get("settled_on")
        check(due is not None and t2.get("returned_on") is not None and
              STR.add_business_days(due, RETURN_DAYS) == t2["returned_on"],
              f"{label}: returned exactly {RETURN_DAYS} business days after settlement", f"{due} → {t2.get('returned_on')}")
    S.setdefault("returns", []).append(tid)


async def y_returns(conn):
    section("[Y] ACH returns: the sender's behaviour, then the receiving bank's — settled → returned with the code after "
            "N business days, both legs reversed, record_return through Refresh")
    if not await ensure_links(conn):
        check(False, "the domestic bank account is linked before the returns section")
        return
    await clock(conn)
    if await behaviour("main", ach_return_code="R01", ach_return_after_days=RETURN_DAYS) == HTTP_OK:
        await ach_return(conn, "ACH returned R01 (sender)", Decimal("1400.00"), "R01", receiver=False)
    await behaviour("main", ach_return_code=None, ach_return_after_days=None)
    if await behaviour("dom", ach_return_code="R03", ach_return_after_days=0) == HTTP_OK:
        await ach_return(conn, "ACH returned R03 (receiving bank)", Decimal("1500.00"), "R03", receiver=True)
    await behaviour("dom", ach_return_code=None, ach_return_after_days=None)


# ── consent ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_consent(conn):
    section("[Y] Consent: consent_required → awaiting_client_consent → 'Record member consent' → acknowledged")
    await clock(conn)
    if await behaviour("main", consent_required=True) != HTTP_OK:
        return
    try:
        pid = await approved(conn, ACC_SRC, ACC_DST, "1600.00", "journal", "consent")
        if not pid:
            return
        t = await sent_ok(conn, pid, "consent", expect_status="pending_consent")
        await refreshed(conn, pid, "consent (pending)", ["consent_requested"])
        row = await payment(conn, pid)
        check(row.get("status") == "awaiting_client_consent", "the payment awaits the member's consent",
              f"{row.get('status')}")
        ct, ot = await api(U_SA, "GET", cpu("main", "/transfers"))
        tr = [x for x in (ot or {}).get("rows") or [] if x.get("transfer_id") == t.get("transfer_id")]
        check(ct == HTTP_OK and len(tr) == 1 and tr[0]["status"] == "pending_consent" and
              (ot.get("behaviour") or {}).get("consent_required") is True,
              "the custodian's Transfers tab lists it pending consent", f"{ct} {tr}")
        cc, oc = await api(U_SA, "POST", cpu("main", f"/transfers/{t.get('transfer_id')}/consent"))
        check(cc == HTTP_OK and ((oc or {}).get("transfer") or {}).get("status") == "settled",
              "'Record member consent' (Super Admin, the custodian's e-consent screen) → acknowledged and, a same-day "
              "journal, settled", f"{cc} {dumps(oc)[:300]}")
        cc2, _ = await api(U_SA, "POST", cpu("main", f"/transfers/{t.get('transfer_id')}/consent"))
        check(cc2 == HTTP_CONFLICT, "a second 'Record member consent' → 409 (no consent awaited)", f"{cc2}")
        await refreshed(conn, pid, "consent (after consent)", ["acknowledge", "settle"])
        ev = [e["action"] for e in await pevents(conn, pid)]
        check(ev[-4:] == ["mark_sent", "consent_requested", "acknowledge", "settle"],
              "the timeline: sent → consent requested → acknowledged → settled — no state skipped", f"{ev}")
    finally:
        await behaviour("main", consent_required=False)


# ── idempotency ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_idempotency(conn):
    section("[Y] Idempotent send and refresh; a delayed transfer applies nothing; no state is ever skipped")
    await clock(conn)
    pid = await approved(conn, ACC_SRC, ACC_DST, "1700.00", "journal", "idempotency")
    if not pid:
        return
    t = await sent_ok(conn, pid, "idempotency", expect_status="settled")
    c2, o2 = await send(U_IN, pid)
    live = await sim(conn, "SELECT count(*) FROM cpsim.transfers WHERE client_transfer_id = $1 AND system_to IS NULL "
                           "AND counterparty_id IN " + FIX_CP, str(pid), fetch="fetchval")
    check(c2 == HTTP_CONFLICT and int(live) == 1, "a repeat Send → 409 (the payment is already sent); ONE transfer",
          f"{c2} {dumps(o2)[:200]} live={live}")
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c_:
            session = await AT.transfer_session(c_, org_id=str(ORG_A))
    st, again = await AT.submit_with_session(session, transfer={
        "client_transfer_id": str(pid), "from_account_id": A1, "to_account_id": A2, "amount": "1.00",
        "currency": "USD", "rail": "journal", "value_date": S["today"].isoformat()})
    live2 = await sim(conn, "SELECT count(*) FROM cpsim.transfers WHERE client_transfer_id = $1 AND system_to IS NULL "
                            "AND counterparty_id IN " + FIX_CP, str(pid), fetch="fetchval")
    check(st == HTTP_OK and again.get("transfer_id") == t.get("transfer_id") and again.get("amount") == "1700.00"
          and int(live2) == 1, "the client re-POSTing the same client_transfer_id with a DIFFERENT body → 200, the "
          "original transfer unchanged, still one (contract §9.1)", f"{st} {again.get('amount')} {live2}")
    await refreshed(conn, pid, "idempotency (first refresh)", ["acknowledge", "settle"])
    n = len(await pevents(conn, pid))
    await refreshed(conn, pid, "idempotency (repeat refresh)", [])
    check(len(await pevents(conn, pid)) == n, "a repeat Refresh applies nothing twice (no new payment_events row)")
    # a DELAYED transfer: received only → nothing applies; after the clock → acknowledge THEN settle, in order
    if await behaviour("main", default_outcome="delay", delay_seconds=0) != HTTP_OK:
        return
    try:
        pid2 = await approved(conn, ACC_SRC, ACC_DST, "1750.00", "journal", "delayed")
        if not pid2:
            return
        await sent_ok(conn, pid2, "delayed", expect_status="received")
        await refreshed(conn, pid2, "delayed (still received)", [])
        check((await payment(conn, pid2)).get("status") == "sent", "a received-only transfer leaves the payment sent")
    finally:
        await behaviour("main", default_outcome="accept")
    await advance(conn)
    await refreshed(conn, pid2, "delayed (after the clock advance)", ["acknowledge", "settle"])


# ── the book: Decimal for transfers and fills ───────────────────────────────────────────────────────────────────
async def y_book(conn):
    section("[Y] The cash ledger is exact (Decimal) for transfers and for cpsim03 fills; the feed carries the D2 "
            "types; the real path's other behaviour unchanged")
    d = await clock(conn)
    c0 = await cash(conn, "main", A3)
    tag = secrets.token_hex(4)

    async def buy(c_):
        cp = await R.live(c_, S["cp"]["main"])
        beh = await R.behaviour(c_, S["cp"]["main"]) or {}
        order, created, _ev = await SOR.submit(c_, cp, {"client_order_id": f"cp4v-{tag}", "account_id": A3,
                                                         "cusip": CUSIP_EQ, "side": "buy", "quantity": "2",
                                                         "order_type": "market", "time_in_force": "gtc"},
                                               clock=d, behaviour=beh)
        return order
    order = await as_sim(conn, buy)
    ex = (order.get("executions") or [{}])[0]
    c1 = await cash(conn, "main", A3)
    check(order.get("status") == "filled" and c1 == c0, "a cpsim03 fill (buy 2 @ 50.00) moves NO cash on its trade "
          "date", f"{order.get('status')} {c0}→{c1}")
    await advance(conn)
    c2 = await cash(conn, "main", A3)
    mv = await sim(conn, "SELECT movement_kind, amount, balance_after FROM cpsim.cash_movements WHERE source_ref = $1 "
                         "AND counterparty_id IN " + FIX_CP, ex.get("exec_id") or "")
    want = Decimal(2) * PRICE_EQ
    check(c0 is not None and c2 == c0 - want and len(mv) == 1 and mv[0]["movement_kind"] == "fill_buy"
          and mv[0]["amount"] == -want and mv[0]["balance_after"] == c2,
          "… on its settle date (the clock advance) the buy debits exactly 2 × 50.00 = 100.00 (Decimal), once",
          f"{c0}→{c2} {[dict(m) for m in mv]}")
    await advance(conn)
    mv2 = await sim(conn, "SELECT count(*) FROM cpsim.cash_movements WHERE source_ref = $1 AND counterparty_id IN "
                          + FIX_CP, ex.get("exec_id") or "", fetch="fetchval")
    check(int(mv2) == 1 and await cash(conn, "main", A3) == c2, "a later advance never moves the fill's cash again")
    types = {r["type"] for r in await sim(conn, "SELECT DISTINCT type FROM cpsim.book_transactions WHERE "
                                                "counterparty_id IN " + FIX_CP)}
    check(bool(types) and types <= set(APS.TRANSACTION_TYPE_MAP) and bool(types & {"transfer_out", "wire_out", "ach_out"}),
          "every type the fixture books serve is one the client maps (the D2 cash types included)", f"{sorted(types)}")
    orig = {"buy": "buy", "sell": "sell", "dividend": "dividend", "interest": "interest", "fee": "fee_expense",
            "fee_expense": "fee_expense", "adjustment": "adjustment"}
    added = {k: v for k, v in APS.TRANSACTION_TYPE_MAP.items() if k not in orig}
    check(all(APS.TRANSACTION_TYPE_MAP.get(k) == v for k, v in orig.items()) and added == {
        "transfer_out": "sell", "ach_out": "sell", "wire_out": "sell", "transfer_in": "buy", "ach_in": "buy",
        "wire_in": "buy", "ach_return": "adjustment"} and "transfer" not in APS.TRANSACTION_TYPE_MAP,
          "D2 is additive: the client's original 7 mappings unchanged, exactly the 7 cash types added, any other type "
          "('transfer') still unmapped (a per-row error, as before)", f"{added}")
    from services import cash_activity as CA
    check(CA.TYPE_MAP["sell"][1] < 0 and CA.TYPE_MAP["buy"][1] > 0 and CA.TYPE_MAP["adjustment"][1] == 0,
          "the D2 codes carry the right sign per leg (sell −, buy +, adjustment = the stored sign)")


# ── webhooks ────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_webhooks(conn):
    section("[Y] transfer.updated: signed, delivered after the response, → a to-do for the sender; nothing applied")
    await clock(conn)
    pid = await approved(conn, ACC_SRC, ACC_DOM, "1800.00", "ach", "webhook")
    if not pid or not await ensure_links(conn):
        return
    await sent_ok(conn, pid, "webhook", expect_status="acknowledged")
    if await behaviour("main", webhooks_enabled=True) != HTTP_OK:
        return
    try:
        t0 = time.monotonic()
        code, out = await advance(conn)
        took = time.monotonic() - t0
        await SW.drain()
        todos = await platform(conn, "SELECT user_id, status, related_id FROM member_todos WHERE org_id = $1 AND "
                                     "source = $2 AND related_id = $3::uuid", ORG_A, PC.TODO_SOURCE, str(pid))
        row = await payment(conn, pid)
        settled_in_sim = (await transfer_of(conn, pid)).get("status")
        check(code == HTTP_OK and settled_in_sim == "settled" and len(todos) == 1 and str(todos[0]["user_id"]) ==
              str(U_IN) and todos[0]["status"] == "open" and row.get("status") == "sent",
              "the clock settles the ACH, the simulator's transfer.updated reaches the receiver (signed, simulator "
              "forced) and raises ONE to-do for the sender; the payment is NOT changed by the webhook (still sent "
              "until a person refreshes)", f"{code} {took:.1f}s sim={settled_in_sim} todos={[dict(t) for t in todos]} "
              f"payment={row.get('status')}")
        await behaviour("main", webhooks_enabled=True)
        _c, _o = await advance(conn)
        await SW.drain()
        n = int(await platform(conn, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND source = $2 AND "
                                     "related_id = $3::uuid AND status = 'open'", ORG_A, PC.TODO_SOURCE, str(pid),
                               fetch="fetchval"))
        check(n == 1, "a second notification never piles up a second open to-do", f"{n}")
    finally:
        await behaviour("main", webhooks_enabled=False)
        S["events"] = [r["event_id"] for r in await platform(
            conn, "SELECT event_id FROM altruist_webhook_events WHERE org_id = ANY($1::uuid[]) OR event_id LIKE $2",
            FIXTURE_ORGS, f"{TAG}%")]
    await refreshed(conn, pid, "webhook (the person refreshes)", ["acknowledge", "settle"])


# ── isolation ───────────────────────────────────────────────────────────────────────────────────────────────────
async def y_isolation(conn):
    section("[Y] Isolation: org A's payments never visible / sendable / refreshable by org B (both directions); the "
            "demo-household rule; bank crosswalks isolated from every real source")
    try:
        await _isolation(conn)
    finally:
        await clock(conn)   # CPSIM04 FIX 1 (C): the section restores the payments clock to org A's custodian's date


async def _isolation(conn):
    # CPSIM04 FIX 1 (C): the section sets its OWN clock. The payments side has ONE injected "today" (S["today"],
    # services.payments.set_clock) while org A and org B each have their own simulated custodian's clock; in the full
    # run earlier sections had advanced A's to 2026-12-01 and this section sent B's payment on A's date ("the value
    # date 2026-11-16 is in the past"). The precondition is now made true HERE (the two clocks differ — one advance of
    # A's if a one-off run left them equal) and every org-B action runs on org B's own clock.
    d_main, d_other = await clock(conn), await clock(conn, "other")
    if d_main == d_other:
        await advance(conn)
        d_main = await clock(conn)
    check(d_main != d_other, f"precondition (the run-order state that hid this check): org A's custodian clock "
          f"({d_main}) and org B's ({d_other}) differ", f"{d_main} {d_other}")
    pa = await approved(conn, ACC_SRC, ACC_DST, "1900.00", "journal", "isolation A", vd=d_main)
    d_b = await clock(conn, "other")             # from here: org B acts, the payments side on org B's own clock
    pb = await approved(conn, ACC_B1, ACC_B2, "1950.00", "journal", "isolation B", maker=U_INB, checker=U_APB, hh=H_B,
                        vd=d_b)
    if not (pa and pb):
        return
    cb, ob = await api(U_INB, "POST", pp(H_B, f"/{pb}/send-to-custodian"))
    await clock(conn)                            # back to org A's clock for the cross-org checks below
    tb = await transfer_of(conn, pb)
    in_main = int(await sim(conn, "SELECT count(*) FROM cpsim.transfers WHERE counterparty_id = $1::uuid AND "
                                  "client_transfer_id = $2", S["cp"]["main"], str(pb), fetch="fetchval"))
    check(cb == HTTP_OK and str(tb.get("counterparty_id")) == str(S["cp"]["other"]) and in_main == 0,
          "org B's payment goes to org B's own simulated custodian (never A's)", f"{cb} {dumps(ob)[:200]} {in_main}")
    for uid, hh, pid, who in ((U_INB, H_A, pa, "B on A's household"), (U_INB, H_B, pa, "B through its own household"),
                              (U_IN, H_B, pb, "A on B's household"), (U_IN, H_A, pb, "A through its own household")):
        g, _ = await api(uid, "GET", pp(hh, f"/{pid}"))
        s_, _ = await api(uid, "POST", pp(hh, f"/{pid}/send-to-custodian"))
        r_, _ = await api(uid, "POST", pp(hh, f"/{pid}/refresh-from-custodian"))
        t_, _ = await api(uid, "GET", pp(hh, f"/{pid}/custodian-transfer"))
        check(g == HTTP_NOT_FOUND and s_ == HTTP_NOT_FOUND and r_ == HTTP_NOT_FOUND and t_ == HTTP_NOT_FOUND,
              f"{who}: the other org's payment cannot be read, sent, refreshed or its transfer read (404 each)",
              f"{g} {s_} {r_} {t_}")
    seen_a = {r["id"] for r in await scoped(conn, ORG_A, "SELECT id FROM payment_instructions WHERE system_to IS NULL "
                                                         "AND id = ANY($1::uuid[])", [pa, pb])}
    seen_b = {r["id"] for r in await scoped(conn, ORG_B, "SELECT id FROM payment_instructions WHERE system_to IS NULL "
                                                         "AND id = ANY($1::uuid[])", [pa, pb])}
    bypass = await platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user", fetch="fetchval")
    check(bypass is False and seen_a == {UUID(pa)} and seen_b == {UUID(pb)},
          "RLS (rolbypassrls false): org A's context reads only A's payment, org B's only B's", f"{seen_a} {seen_b}")
    keys = await platform(conn, "SELECT 1", fetch="fetchval")
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c_:
            sim_keys = await PC.payment_custodian_keys(c_, ORG_A, [ACC_SRC, ACC_DST, ACC_DOM, ACC_INTL, ACC_REAL])
            resolved = await APS.list_resolved_altruist_accounts(c_, org_id=str(ORG_A), source=SIM_XWALK)
            real_res = await APS.list_resolved_altruist_accounts(c_, org_id=str(ORG_A), source=REAL_XWALK)
    res_ids = {str(r.get("hollisworks_account_id") or r.get("account_id") or r.get("record_id")) for r in resolved}
    real_ids = {str(r.get("hollisworks_account_id") or r.get("account_id") or r.get("record_id")) for r in real_res}
    check(keys == 1 and set(sim_keys) == {str(ACC_SRC), str(ACC_DST)} and str(ACC_DOM) not in res_ids
          and str(ACC_INTL) not in res_ids and str(ACC_DOM) not in real_ids and str(ACC_INTL) not in real_ids,
          "the CPSIM_BANK crosswalk never collides with a real source: bank-linked accounts are neither simulated "
          "custodian accounts (payment_custodian_keys) nor synced as Altruist / simulator accounts; the real-Altruist "
          "account is not a simulated custodian's", f"{sim_keys} sim={sorted(res_ids)[:4]} real={sorted(real_ids)[:4]}")
    found = await platform(conn, "SELECT count(*) FROM portfolio.external_references WHERE source_system = $1 AND "
                                 "external_id = ANY($2::text[])", SIM_XWALK, [BANK_DOM_ACCT, BANK_INTL_ACCT],
                           fetch="fetchval")
    check(int(found) == 0, "a bank's book account id is never a simulator / Altruist crosswalk (the webhook receiver's "
          "reverse lookup cannot resolve it)", f"{found}")


# ── unchanged ───────────────────────────────────────────────────────────────────────────────────────────────────
async def y_unchanged(conn):
    section("[Y] Unchanged: third-party payments OFF and gated; cash08 tables untouched; the manual adapter and the "
            "payment sheet still offered and the default; real capabilities; the loaders not applied")
    await clock(conn)
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c_:
            why = await CPY.third_party_refusal(c_, ORG_A)
    check(why == CPY.NOT_ENABLED, "the third-party switch is OFF for the fixture org", f"{why}")
    c, o = await api(U_IN, "POST", pp(H_A), {**body_of(ACC_SRC, ACC_DST, "10.00", "journal", S["today"], NAME + "tp"),
                                             "destination_account_id": None,
                                             "destination_payee_id": str(uuid_missing())})
    check(c == HTTP_FORBIDDEN and "third-party payments are not enabled" in dumps(o),
          "a draft naming a payee is refused 'third-party payments are not enabled' (the gate is unchanged)",
          f"{c} {dumps(o)[:200]}")
    pid = await approved(conn, ACC_SRC, ACC_DST, "20.00", "journal", "unchanged manual")
    if pid:
        c, o = await detail(U_IN, pid)
        check(c == HTTP_OK and {"sheet", "mark_sent", "send_to_custodian"} <= set(actions(o)),
              "the manual route stays offered beside the simulator's: Download payment sheet, Confirm sent, Send",
              f"{actions(o)}")
        chans = [x.get("key") for x in ((o or {}).get("vocabularies") or {}).get("channels") or []]
        check(PAD.CUSTODIAN_API not in chans and set(chans) == set(PAD.MANUAL_CHANNELS),
              "Confirm sent offers only the manual channels (the custodian API channel is Send's alone)", f"{chans}")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://verify",
                                     timeout=300) as client:
            res = await client.post(pp(H_A, f"/{pid}/sheet"), headers={"Authorization": f"Bearer {token_for(U_IN)}"})
        check(res.status_code == HTTP_OK and res.headers.get("content-type", "").startswith("application/pdf")
              and res.content[:4] == b"%PDF", "the payment sheet (the MANUAL adapter's) is still downloadable for a "
              "simulator-routed payment", f"{res.status_code} {res.headers.get('content-type')}")
        cm, om = await act(U_IN, pid, "mark_sent", channel=PAD.CUSTODIAN_API, external_ref="sim-t-0000000000000000")
        check(cm == HTTP_UNPROCESSABLE and (await payment(conn, pid)).get("status") == "approved",
              "'Confirm sent' refuses the custodian_api channel (only Send records it)", f"{cm} {dumps(om)[:200]}")
        cm2, _ = await act(U_IN, pid, "mark_sent", channel="email", note="sent by hand")
        row = await payment(conn, pid)
        check(cm2 == HTTP_OK and row.get("adapter") == PAD.MANUAL and row.get("sent_channel") == "email",
              "the manual route still works: Confirm sent (email) → sent with adapter 'manual', exactly as before",
              f"{cm2} {row.get('adapter')} {row.get('sent_channel')}")
    check(PAD.adapter_for({"custodian_code": "SCHWAB"}, set()).key == PAD.MANUAL and
          PAD.registry()[PAD.MANUAL]["connected"] is True, "manual is the default for every other custodian")
    real = {k: (await CC.capability(conn, "altruist", k)).value for k in ("ach", "wire", "journal",
                                                                          "payment_status_feed")}
    check(real == {"ach": "unknown", "wire": "unknown", "journal": "unknown", "payment_status_feed": "no"},
          "real Altruist's payment capabilities unchanged (ach / wire / journal unknown, status feed no)", f"{real}")
    n = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1",
                           "altruist_simulator", fetch="fetchval"))
    code, out, err = run_cmd([sys.executable, str(LOADER)], REPO, timeout=300)
    n2 = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1",
                            "altruist_simulator", fetch="fetchval"))
    check(n == 0 and n2 == 0 and code == 0 and "DRY RUN — nothing written" in out and "6 to set" in out,
          "the loaders were not applied: no altruist_simulator row; scripts/cpsim04_capabilities.py defaults to "
          "--dry-run (the plan printed, nothing written)", f"{n} {n2} {code} {(out + err)[-300:]}")


# ── conformance ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_conformance(conn):
    section("[Y] Conformance: the transfers group passes against the simulator; --target sandbox skips it")
    out_json = pathlib.Path("/tmp") / f"cpsim04_verify_conformance_{secrets.token_hex(4)}.json"
    code, out, err = run_cmd([sys.executable, str(CONFORMANCE), "--target", "simulator", "--json-out", str(out_json)],
                             REPO, timeout=900)
    try:
        doc = json.loads(out_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    res = {r["id"]: r["passed"] for r in doc.get("results", [])}
    want = [f"T{n:02d}" for n in range(1, 17)]
    check(code == 0 and all(res.get(k) is True for k in want) and doc.get("failed") == 0,
          "the in-process conformance run: every transfers check T01–T16 PASS, 0 FAIL overall",
          f"{code} {[k for k in want if res.get(k) is not True]} "
          f"{[(r['id'], str(r.get('detail'))[:300]) for r in doc.get('results', []) if r.get('passed') is not True]} "
          f"{(out + err)[-300:]}")
    code2, out2, _e2 = run_cmd([sys.executable, str(CONFORMANCE), "--target", "sandbox"], REPO, timeout=120)
    check(code2 == REFUSED_EXIT and "transfers group: skipped — no published Altruist money-movement contract" in out2,
          "--target sandbox: the transfers group is skipped with 'no published Altruist money-movement contract'",
          f"{code2} {out2[-200:]}")
    check(ACF.transfers_group_skip("sandbox", None) == ACF.NO_TRANSFERS_CONTRACT and
          ACF.transfers_group_skip("production", {"x": 1}) == ACF.NO_TRANSFERS_CONTRACT and
          ACF.transfers_group_skip("simulator", None) == ACF.NOT_A_TRANSFER_FIXTURE and
          ACF.transfers_group_skip("simulator", {"from": "a"}) is None,
          "the suite's own skip rule: any real target → no published contract; the simulator without a fixture → not "
          "run")
    try:
        out_json.unlink()
    except OSError:
        pass


# ── safety ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _real_aba() -> str:
    """A 99-prefixed routing number that PASSES the real ABA check digit (what cpsim01 refuses as real-looking)."""
    for tail in range(1000000):
        s = f"99{tail:06d}"
        for c in range(10):
            d = [int(x) for x in s + str(c)]
            if (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0:
                return s + str(c)
    raise RuntimeError("no checksum-valid number found")


async def y_safety(conn):
    section("[Y] No real-looking number accepted; no full fake number shown anywhere; boundary check; the app role "
            "cannot read cpsim.*; cpsim_app privileges exactly as documented; no secret printed")
    real = _real_aba()

    async def ins(c_):
        await c_.execute("INSERT INTO cpsim.counterparty_bank_details (counterparty_id, kind, bank_name, aba_wire, "
                         "aba_ach, account_number) VALUES ($1::uuid, 'domestic', 'CPSIM Real-looking', $2, $3, "
                         "'CPSIM000000001')", S["cp"]["dom"], real, real)
    _r, e = await rolled_back(conn, ins, super_admin=False, as_sim=True)
    check(isinstance(e, (asyncpg.CheckViolationError, asyncpg.RaiseError, asyncpg.UniqueViolationError,
                         asyncpg.InsufficientPrivilegeError)),
          "a checksum-valid (real-looking) routing number is refused by the cpsim01 rules (rolled back)", f"{e!r}"[:200])

    async def ref(c_):
        await c_.execute("INSERT INTO cpsim.transfers (counterparty_id, transfer_id, client_transfer_id, "
                         "from_account_external_id, to_bank_slug, to_bank_account_id, to_bank_routing_last4, amount, "
                         "rail, value_date, status, received_on) VALUES ($1::uuid, 'sim-t-0000000000000001', 'x', 'a', "
                         "'b', 'c', $2, 1, 'ach', $3, 'received', $3)", S["cp"]["main"], real, S["today"])
    _r, e2 = await rolled_back(conn, ref, super_admin=False, as_sim=True)
    check(isinstance(e2, asyncpg.CheckViolationError) and getattr(e2, "constraint_name", "") == "transfers_bank_refs_chk",
          "a FULL routing number in a transfer's bank reference is refused by transfers_bank_refs_chk", f"{e2!r}"[:200])
    await fake_numbers(conn)
    bodies = [x for b in S["bodies"] for x in number_leaks(b)]
    audit = await platform(conn, "SELECT payload::text AS p FROM audit_log WHERE org_id = ANY($1::uuid[]) OR "
                                 "resource_id = ANY($2::uuid[])", FIXTURE_ORGS, [UUID(v) for v in S["cp"].values()])
    in_audit = [x for r in audit for x in number_leaks(r["p"])]
    logs = await sim(conn, "SELECT path FROM cpsim.request_log WHERE counterparty_id IN " + FIX_CP)
    in_logs = [x for r in logs for x in number_leaks(r["path"])]
    xw = await platform(conn, "SELECT external_id FROM portfolio.external_references WHERE source_system = $1 AND "
                              "org_id = ANY($2::uuid[])", BANK_XWALK, FIXTURE_ORGS)
    in_xw = [x for r in xw for x in number_leaks(r["external_id"])] + [
        r["external_id"][-6:] for r in xw if re.search(r"\d{5,}", r["external_id"].split(":")[-1])]
    tr = await sim(conn, "SELECT to_bank_routing_last4, to_bank_iban_last4 FROM cpsim.transfers WHERE counterparty_id "
                         "IN " + FIX_CP)
    long_refs = [r for r in tr if any(v and len(v) > 4 for v in r.values())]
    check(bool(S["fake_numbers"]) and len(xw) >= 2 and not bodies and not in_audit and not in_logs and not in_xw
          and not long_refs,
          "no FULL fake bank number in any response body, audit row, request-log path, crosswalk or transfer — last "
          "four only", f"bodies={bodies[:3]} audit={in_audit[:3]} logs={in_logs[:3]} xwalk={in_xw[:3]} "
          f"refs={long_refs[:2]}")
    code, out, err = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=300)
    check(code == 0 and "0 violation" in out, "cpsim_boundary_check passes", f"{code} {(out + err)[-300:]}")
    for t in ("transfers", "transfer_events", "cash_movements"):
        try:
            await platform(conn, f"SELECT count(*) FROM cpsim.{t}", fetch="fetchval")
            denied = False
        except asyncpg.InsufficientPrivilegeError:
            denied = True
        check(denied, f"the app role cannot read cpsim.{t} (permission denied)")
    app_role = await platform(conn, "SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'", fetch="fetchval")
    tabs = [r["relname"] for r in await platform(conn, "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = "
                                                       "c.relnamespace WHERE n.nspname = 'cpsim' AND c.relkind = 'r'")]
    wrong = []
    for t in tabs:
        privs = {r["privilege_type"] for r in await platform(
            conn, "SELECT x.privilege_type, x.grantee FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid = (" + REL +
                  ")", "cpsim", t) if r["grantee"] == app_role}
        if t in ("institution_types", "real_bic_examples"):
            continue
        if privs != set(FX.cpsim_app_privileges(t)):
            wrong.append((t, sorted(privs)))
    check(bool(tabs) and not wrong, "cpsim_app's privileges on every cpsim table are EXACTLY the documented set (the "
          "shared _cpsim_fixtures.CPSIM_APP_WITHHELD list: transfers −DELETE; transfer_events / cash_movements "
          "append-only)", f"{wrong[:4]}")
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
    declared = node_tests_declared()
    check(declared >= NODE_TEST_MIN and
          re.search(r"^test\(\"cpsim04 fix 1: a source ALSO crosswalked", _src("apps/web/" + NODE_TEST), re.M) is not None,
          f"{NODE_TEST} declares >= {NODE_TEST_MIN} tests, the fix 1 reason-panel test among them ({declared})")
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) == declared and
          int(p.group(1)) == declared and re.search(r"^# fail 0$", o1, re.M) is not None,
          f"the cpsim04 render tests ({NODE_TEST}, counted by their file: payment page panel, Transfers / Received "
          f"tabs, badges) all pass ({p.group(1) if p else '?'}/{n.group(1) if n else '?'}; declared {declared})",
          f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1800)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    mode = VT.regression_mode()
    section(f"[Y] Regressions — each ONCE, flat, AFTER teardown, VERIFY_NESTED=1, its OWN final TOTAL line "
            f"(VERIFY_REGRESSIONS={mode}: {'full run, 2700 s' if mode == 'full' else '--self-checks-only, 600 s'})")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)"
              + (" [--self-checks-only]" if mode == "fast" else ""),
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")
        print(f"[INFO] {script} ({mode}): {t.line.strip() if t.line else 'no TOTAL line'}")


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


FIXTURE_SECTIONS = (("routing", y_routing), ("governance", y_governance), ("journal", y_journal),
                    ("banks", y_banks), ("returns", y_returns), ("consent", y_consent),
                    ("idempotency", y_idempotency), ("book", y_book), ("webhooks", y_webhooks),
                    ("isolation", y_isolation), ("unchanged", y_unchanged), ("conformance", y_conformance),
                    ("safety", y_safety))
#: every section sets up what it needs itself (cpsim02 fix 1) — no section depends on another having run
ONLY_NEEDS = {name: () for name, _f in FIXTURE_SECTIONS}


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    bad = [x for x in want if x not in ONLY_NEEDS and x not in ("seed", "front_end")]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, front_end, {', '.join(ONLY_NEEDS)}")
    if "front_end" in want and want != ["front_end"]:
        raise SystemExit("--only=front_end runs alone (no seed, no DB fixtures)")
    names = {x for x in want if x != "seed"}
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
    if any(a == "--only=front_end" for a in sys.argv[1:]):
        only_sections()   # validates the flag
        try:   # cpsim04 fix 2: the front-end section one-off — no seed, no fixtures (npm build skipped via SKIP_BUILD=1)
            await run("front end", y_front_end, conn)
        finally:
            restore_stubs()
            await close_conn(conn)
        print(total_line())
        return 0 if _n_fail == 0 else 1
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
              "(every simulator, bank, transfers, admin and webhook call was in-process)",
              f"network: {NETWORK_CALLS[:3]} model: {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows "
                "unchanged; no counterparty, transfer, cash movement or book row left")
        await SW.drain()   # cpsim03 fix 1: no scheduled webhook is still in flight when the stubs / pools go away
        restore_stubs()
        await close_app_pools()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (orgs, users, grants, accounts, crosswalks, payment instructions "
                  "and events, approval tasks, sod decisions, to-dos, positions, transactions, assets, connections, "
                  "webhook events, settings, capability rows, audit rows, the fixture security, every cpsim table incl. "
                  "transfers, transfer events and the cash ledger)", f"{left}")
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
