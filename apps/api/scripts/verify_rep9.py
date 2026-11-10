"""verify_rep9 — Replication Studio, sprint 9 (REP9): block trading, allocation
and DVP settlement, manual-first. Approved account instructions aggregated
into a block under the firm's approved allocation policy, the allocation FIXED
at approval (maker-checker); a printed order execution sheet (always offered,
also for one account instruction); block fills → computed average price →
pro-rata allocation with deterministic rounding and the minimum allocation;
each account booked on its own instruction at the average price with its own
lots; DVP settlement per allocation (rep7b), statuses, fails → a to-do, the
close rule; corrections only with a reason and a second person; the FIX router
interface registered and NOT connected.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep9.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the httpx network transport records and refuses — any attempt is
a failed check. Every custodian trade adapter's ``submit`` records a call: a
block or a DVP route that reached one is a failed check.

REAL: the app through an ASGI transport (middleware, Principal, every gate),
the workflow engine (human tasks), rep2 / rep7b services, RLS and the live
database. The script's own connection is app_service (rolbypassrls = false,
asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-000000009001 / 9002, is_test =
true, asserted); users created explicitly with the auth0_sub their token
carries and an @test.local email; masked account numbers only (raw ZZ9B…);
securities with the reserved prefix ZZR9B and the name prefix 'REP9 VERIFY';
a fixture custodian code ZZREP9FRAC whose two capability rows (fractional
shares yes, equity trading API yes) are platform rows this run writes and
removes (custodian_code 'zzrep9frac'). All checked absent from real rows first.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with
execution.teardown = 'fixture' — seeds keyed by this run's fixture ids, FK
closure, one DELETE per table; then every counted table is re-read for its
exact pre-test count, and real rows are compared by count + checksum.

Every expected date is computed at the moment of the call. With
VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.

--cleanup-only: run only the teardown and the leftover check, print
'CLEANUP_SECONDS', 'LEFTOVERS' and 'TOTAL'. Exits 0 when clean.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

# ── VHK1: self-checks (git / route / changed-line), stdlib + git only. Each ends at rep9's OWN merge (or HEAD on its
# branch), never the working tree, so a later sprint's routes or edits to verify_rep7b.py cannot break them.
# --self-checks-only runs just these, BEFORE hydrate() — no Doppler, no database, no network, no npm.
import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep9"
BEFORE_REF = "87ad248"   # main @ 'refresh schema snapshot' after the marketdata1 merge — the rep9-work base
# this sprint's own routes (from its own diff) and the gate each names — never "every route added since a base"
ROUTES = {
    ("GET", "/api/v1/execution/blocks"): "view_blocks",
    ("POST", "/api/v1/execution/blocks"): "manage_blocks",
    ("GET", "/api/v1/execution/blocks/candidates"): "view_blocks",
    ("POST", "/api/v1/execution/blocks/preview"): "view_blocks",
    ("GET", "/api/v1/execution/blocks/routers"): "view_blocks",
    ("GET", "/api/v1/execution/blocks/settings/policies"): "view_blocks",
    ("POST", "/api/v1/execution/blocks/settings/policies"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/settings/policies/{policy_id}/approve"): "approve_blocks",
    ("POST", "/api/v1/execution/blocks/settings/policies/{policy_id}/reject"): "approve_blocks",
    ("GET", "/api/v1/execution/blocks/settings/execution-accounts"): "view_blocks",
    ("POST", "/api/v1/execution/blocks/settings/execution-accounts"): "manage_blocks",
    ("PATCH", "/api/v1/execution/blocks/settings/execution-accounts/{account_id}"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/instructions/{instruction_id}/sheet"): "view_execution",
    ("POST", "/api/v1/execution/blocks/corrections/{correction_id}/approve"): "approve_blocks",
    ("POST", "/api/v1/execution/blocks/corrections/{correction_id}/reject"): "approve_blocks",
    ("GET", "/api/v1/execution/blocks/{block_id}"): "view_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/submit"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/approve"): "approve_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/reject"): "approve_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/sent"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/fills"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/allocate"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/allocations/{allocation_id}/settlement"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/allocations/{allocation_id}/settlement-match"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/corrections"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/charges"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/charges/{charge_id}/close"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/close"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/cancel"): "manage_blocks",
    ("POST", "/api/v1/execution/blocks/{block_id}/sheet"): "view_blocks",
}
# the exact diff of verify_rep7b.py since the base: its route self-check scoped to its own routes, and (rep9 fix 1)
# its three stale checks — the decided matrix gains rep8's 'options' path; zero existing accounts / held-away
# drafts report a [FIND] (still asserting the rule for every row that exists) — nothing else
REP7B_ROUTE_SCOPE_DIFF = (
    '-    ("managed", "integrated"): (MODEL | EXEC, ("custodian_desk", "broker", "member", "other_adviser", "trader")),',
    '-    ("managed", "other_custodian"): (MODEL | EXEC, ("broker", "trader", "member", "other_adviser")),',
    "+    # rep9 fix 1 — rep8 added the 'options' (options-sleeve proposal) path: managed × integrated / other_custodian only",
    '+    ("managed", "integrated"): (MODEL | {"options"} | EXEC, ("custodian_desk", "broker", "member", "other_adviser",',
    '+                                                             "trader")),',
    '+    ("managed", "other_custodian"): (MODEL | {"options"} | EXEC, ("broker", "trader", "member", "other_adviser")),',
    '-    check(len(real) > 0 and not diffs and rows_for_real == 0,',
    "+    if not real:   # rep9 fix 1 — lotmethod's cleanup removed the leftover REP2 orgs that held the only accounts",
    '+        find("migration: no existing account outside this run\'s orgs", "none exist today — the rule below is "',
    '+             "asserted for every such account when any exist")',
    '+    check(not diffs and rows_for_real == 0,',
    '-    check(len(drafts) >= 1 and all(st == "draft" and "held-away" in why and "read-only" in why and "submit" not in acts',
    "+    if not drafts:   # rep9 fix 1 — the only held-away draft sat in a leftover REP2 org lotmethod's cleanup removed",
    '+        find("migration: no existing held-away draft instruction", "none exist today — the rule is asserted for "',
    '+             "every such draft when any exist, and proven below on an identical draft in this run\'s org")',
    '+    check(all(st == "draft" and "held-away" in why and "read-only" in why and "submit" not in acts',
    "-    # lotmethod: its routes are all under /api/v1/lot-method/ — not this script's",
    '-    new = [k for k in gn if k not in gb and not k[1].startswith("/api/v1/lot-method/")]',
    '-    bad = [(k, gn[k].get("after")) for k in new if want.get(k[1]) not in str(gn[k].get("after"))]',
    '-    check(len(new) == len(want) and not bad, f"{len(want)} new routes, each naming its permission", f"{len(new)} "',
    '-          f"{bad[:4]}")',
    '+    # rep9 fix — scoped to rep7b\'s OWN routes (``want``), never "every route added since a base commit": later',
    '+    # sprints (lotmethod, rep8, marketdata1, rep9 …) add routes of their own, and each proves its own',
    '+    mine = {k: gn[k] for k in gn if k[1] in want}',
    '+    bad = [(k, gn[k].get("after")) for k in mine if want[k[1]] not in str(gn[k].get("after"))]',
    '+    missing = sorted(set(want) - {k[1] for k in mine})',
    '+    check(len(mine) == len(want) and not bad and not missing,',
    '+          f"rep7b\'s {len(want)} routes still exist, each naming its permission", f"{len(mine)} {bad[:4]} {missing[:4]}")',
)


def _gates_by_method(d):
    return {(m, r["path"]): r for r in (d or {}).get("routes", []) for m in (r.get("methods") or [])}


def self_routes(check, find):
    # rep9's own routes: BEFORE_REF → its own merge (gates, its own diff of routers/blocks.py); each must still exist
    # today with the same gate.
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    gb = _gates_by_method(json.loads(VS.git_show(BEFORE_REF, VS.ROUTES_JSON) or "{}"))
    ge = _gates_by_method(json.loads(VS.git_show(end, VS.ROUTES_JSON) or "{}"))
    gt = _gates_by_method(json.loads((REPO / VS.ROUTES_JSON).read_text(encoding="utf-8")))
    changed = [k for k in gb if k in ge and VS.gate_of(gb[k]) != VS.gate_of(ge[k])]
    removed = [k for k in gb if k not in ge]
    check(len(gb) > 0 and not changed and not removed, "rep9 changed or removed no existing route's gate",
          f"{BEFORE_REF} → {how}; {changed[:5]} {removed[:5]}")
    code, diff, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "apps/api/routers/blocks.py")
    decl = re.findall(r'^\+@router\.(get|post|patch|put|delete)\("([^"]+)"', diff, re.M)
    mine = {(m.upper(), "/api/v1" + p) for m, p in decl}
    check(code == 0 and mine == set(ROUTES), f"this sprint's own diff declares exactly its {len(ROUTES)} routes",
          f"{how}; {len(mine)} extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    bad = [(k, (ge.get(k) or {}).get("after")) for k, perm in ROUTES.items()
           if k not in ge or perm not in str(ge[k].get("after")) or k in gb]
    check(not bad, f"each of the {len(ROUTES)} exists at rep9's own merge, is new, and names its permission",
          f"{how}; {bad[:4]}")
    drifted = [k for k in ROUTES if k in ge and (k not in gt or VS.gate_of(gt[k]) != VS.gate_of(ge[k]))]
    check(not drifted, f"rep9's {len(ROUTES)} routes all still exist today with the same gate", f"{drifted[:4]}")


def self_rep7b_diff(check, find):
    # rep9's own edits to verify_rep7b.py: BEFORE_REF → rep9's own merge, exactly REP7B_ROUTE_SCOPE_DIFF. A later
    # sprint may edit verify_rep7b.py freely; its own guard covers its edits (VHK1).
    code, diff, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "apps/api/scripts/verify_rep7b.py", args=("-U0",))
    changed = [ln.rstrip() for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    check(code == 0 and changed == list(REP7B_ROUTE_SCOPE_DIFF),
          "verify_rep7b.py differs from the base (at rep9's own merge) only by its route self-check scoped to its own "
          "11 routes and rep9 fix 1's three stale checks (options in the matrix; [FIND] on zero existing rows)",
          f"{VS.sprint_end_ref(SPRINT_KEY)[1]}; unexpected={[x for x in changed if x not in REP7B_ROUTE_SCOPE_DIFF][:3]} "
          f"{len(changed)}/{len(REP7B_ROUTE_SCOPE_DIFF)}")


def self_checks(check, find):
    self_routes(check, find)
    self_rep7b_diff(check, find)


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

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import blocks as BK  # noqa: E402
from services import execution as X  # noqa: E402
from services import execution_routers as RT  # noqa: E402
from services import order_sheet as OS  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody import trading as TR  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "rep9verify"
PREFIX = "ZZR9B"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FRAC_CODE = "ZZREP9FRAC"          # an account's custodian_code; its capability key is the lower-case form
FRAC_KEY = "zzrep9frac"
SEC_KEYS = ("K", "L", "M")
SEC_NAMES = {k: f"REP9 VERIFY {k}" for k in SEC_KEYS}
MEMBER_NAME = "REP9 Verify Member Alice Whitcombe"     # must never appear on an order execution sheet
BROKER = "ZZ Broker"
EA_LAST4 = "9876"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-000000009{n:03d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN1, U_ADMIN2, U_VIEWER, U_ADMIN_B = (_id(110 + i) for i in range(5))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP9 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP9 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP9 Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep9_viewer", "REP9 Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP9 Org B Admin"),
}
BLOCK_PERMS = ("view_blocks", "manage_blocks", "approve_blocks")
VIEWER_PERMS = ("view_blocks", "view_execution")

ENT_A, ENT_B = _id(201), _id(202)
A1, A2, A3, A4, A5, A6, A7, AB = (_id(301 + i) for i in range(8))
ACCOUNTS = {   # id: (org, custodian_code, held_away, discretionary, raw number, role)
    A1: (ORG_A, "ALTRUIST", False, True, "ZZ9B000001", "integrated, whole shares"),
    A2: (ORG_A, "ALTRUIST", False, True, "ZZ9B000002", "integrated, whole shares, small (minimum allocation)"),
    A3: (ORG_A, FRAC_CODE, False, True, "ZZ9B000003", "integrated, fractional, automated route available"),
    A4: (ORG_A, "SCHWAB", True, True, "ZZ9B000004", "managed at another custodian — DVP"),
    A5: (ORG_A, "FIDELITY", True, True, "ZZ9B000005", "advise"),
    A6: (ORG_A, "FIDELITY", True, True, "ZZ9B000006", "read-only (held-away default)"),
    A7: (ORG_A, "ALTRUIST", False, False, "ZZ9B000007", "managed, NON-discretionary"),
    AB: (ORG_B, "ALTRUIST", False, True, "ZZ9B000008", "org B"),
}
RECEIVING = {"custodian_name": "ZZ Receiving Trust", "participant_number": "0901", "agent_bank": "ZZ Agent Bank",
             "account_last4": "4321", "contra_broker": BROKER}
MINIMUM = Decimal(5)

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.accounts", "public.org_settings",
    "portfolio.assets", "portfolio.positions", "portfolio.tax_lots", "portfolio.transactions",
    "portfolio.securities_global", "portfolio.securities_global_identifiers", "public.custodian_capabilities",
    "public.execution_instructions", "public.execution_instruction_lots", "public.execution_fills",
    "public.execution_instruction_events", "public.account_authority_settings", "public.instruction_settlements",
    "public.execution_accounts", "public.allocation_policies", "public.trade_blocks", "public.allocation_corrections",
    "public.block_allocations", "public.block_fills", "public.block_charges", "public.block_events",
)
NEW_TABLES = ("public.execution_accounts", "public.allocation_policies", "public.trade_blocks",
              "public.allocation_corrections", "public.block_allocations", "public.block_fills", "public.block_charges",
              "public.block_events")
# Every literal the seed and the tests write into a CHECK-constrained column — checked against the live catalog.
SEED_CHECK_LITERALS = (
    ("public.trade_blocks", "trade_blocks_executor_chk", ("broker", "trader", "custodian_desk")),
    ("public.trade_blocks", "trade_blocks_route_chk", ("manual",)),
    ("public.trade_blocks", "trade_blocks_if_not_filled_chk", ("cancel", "carry", "ask")),
    ("public.trade_blocks", "trade_blocks_status_chk", ("draft", "pending_approval", "approved", "sent",
                                                        "partially_filled", "filled", "allocated", "settled", "closed",
                                                        "cancelled")),
    ("public.trade_blocks", "trade_blocks_reference_chk", ("security_data", "adviser")),
    ("public.block_allocations", "block_allocations_status_chk", ("planned", "fixed", "allocated", "cancelled")),
    ("public.block_allocations", "block_allocations_settlement_status_chk", ("pending", "affirmed", "settled",
                                                                              "failed", "cancelled")),
    ("public.block_allocations", "block_allocations_settlement_kind_chk", ("integrated", "dvp")),
    ("public.block_allocations", "block_allocations_settlement_method_chk", ("manual", "custodian_data")),
    ("public.allocation_policies", "allocation_policies_method_chk", ("pro_rata_requested",)),
    ("public.allocation_policies", "allocation_policies_status_chk", ("draft", "approved", "rejected")),
    ("public.allocation_corrections", "allocation_corrections_status_chk", ("draft", "approved", "rejected")),
    ("public.block_charges", "block_charges_kind_chk", ("commission",)),
    ("public.execution_accounts", "execution_accounts_status_chk", ("active", "retired")),
    ("public.execution_instructions", "execution_instructions_executor_chk", ("broker", "member")),
    ("public.execution_instructions", "execution_instructions_status_chk", ("draft", "pending_approval", "approved",
                                                                             "sent", "filled", "cancelled", "expired")),
    ("public.execution_instructions", "execution_instructions_if_not_filled_chk", ("cancel", "ask")),
    ("public.account_authority_settings", "account_authority_settings_custody_chk", ("other_custodian", "outside")),
    ("public.account_authority_settings", "account_authority_settings_authority_chk", ("managed", "advise")),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("fractional_shares", "equity_trading_api")),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes",)),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("assumed",)),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long",)),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity",)),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
)


S: dict = {}
NETWORK_CALLS: list[str] = []
ADAPTER_CALLS: list[str] = []          # adapters reached OUTSIDE fixture setup — any block path; must stay empty
SETUP_ADAPTER_CALLS: list[str] = []    # rep9 fix 1: rep2's POST /execution/instructions (route_trade_list) in setup
_IN_SETUP = [False]
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def git_show(ref: str, path: str) -> str:
    code, out, _ = run_cmd(["git", "show", f"{ref}:{path}"], REPO, timeout=60)
    return out if code == 0 else ""


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def D(v) -> Decimal:
    return Decimal(str(v))


def tk(key):
    return f"{PREFIX}{key}"


def masked(acct) -> str:
    return AccountNumber(ACCOUNTS[acct][4]).masked


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def next_business_day(d: date) -> date:
    """The verify's own T+1 (weekends only), independent of services/authority.py."""
    n = d + timedelta(days=1)
    while n.isoweekday() in (6, 7):
        n = n + timedelta(days=1)
    return n


# ═══ The verify's OWN statement of the allocation rules (independent of services/block_allocation.py) ═══
def exp_avg(fills: list[tuple[str, str]]) -> Decimal:
    q = sum((D(a) for a, _ in fills), Decimal(0))
    with localcontext() as ctx:
        ctx.prec = 80
        n = sum((D(a) * D(b) for a, b in fills), Decimal(0))
        return (n / q).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def exp_partial(slots: list[tuple[str, str, Decimal, Decimal]], filled: Decimal, minimum: Decimal) -> dict:
    """slots = [(key, account_id, fixed quantity, unit)] → {key: quantity}. Pro rata on the fixed allocations;
    an exact share below the minimum → 0 (re-apportioned); round down to the unit; whole-share residual by the
    largest remainder, ties by account id; a sub-share residual to the first fractional account in that order."""
    active = sorted(slots, key=lambda s: (s[1], s[0]))
    out = {}
    while True:
        tot = sum((s[2] for s in active), Decimal(0))
        with localcontext() as ctx:
            ctx.prec = 80
            exact = {s[0]: filled * s[2] / tot for s in active}
        low = [s for s in active if exact[s[0]] < minimum]
        if not low:
            break
        for s in low:
            out[s[0]] = Decimal(0)
        active = [s for s in active if s[0] not in out]
    base = {}
    for k, _a, cap, unit in active:
        base[k] = min((exact[k] / unit).to_integral_value(rounding=ROUND_DOWN) * unit, cap)
    rest = filled - sum(base.values(), Decimal(0))
    order = sorted(active, key=lambda s: (-(exact[s[0]] - base[s[0]]), s[1], s[0]))
    for k, _a, cap, _u in order:
        if rest >= 1 and base[k] + 1 <= cap:
            base[k] += 1
            rest -= 1
    for k, _a, cap, unit in order:
        if unit < 1 and rest > 0:
            give = min(rest, cap - base[k])
            base[k] += give
            rest -= give
    out.update(base)
    return out


def pdf_text(pdf: bytes) -> str:
    """The PDF's text layer, page by page, through pypdf (a dependency already).

    rep9 fix 1: the old raw inflate found NOTHING — reportlab writes its content streams as
    [/ASCII85Decode /FlateDecode] and the 'stream … endstream' regex matched zero streams, so every text
    assertion saw an empty string. pypdf applies the filters and the font encoding."""
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


# ═══ DB helpers (verify's own app_service connection; RLS context explicit) ═══
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


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch (count + checksum): everything outside this run's own fixture orgs."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("tax_lots", "portfolio.tax_lots"), ("transactions", "portfolio.transactions"),
                        ("execution_instructions", "public.execution_instructions"),
                        ("execution_instruction_lots", "public.execution_instruction_lots"),
                        ("execution_fills", "public.execution_fills"),
                        ("instruction_settlements", "public.instruction_settlements"),
                        ("account_authority_settings", "public.account_authority_settings"),
                        ("rebalance_proposals", "public.rebalance_proposals"),
                        *[(t.split(".")[1], t) for t in NEW_TABLES]):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["custodian_capabilities"] = await fingerprint(
        conn, "SELECT * FROM public.custodian_capabilities WHERE custodian_code <> $1", FRAC_KEY)
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
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


async def fixture_security_ids(conn) -> list:
    rows = await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")
    ids = {r["global_security_id"] for r in rows}
    ids |= {r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name = ANY($1::text[])",
                                              list(SEC_NAMES.values()))}
    return list(ids)


async def org_tables(conn) -> list[str]:
    return [r["t"] for r in await conn.fetch(
        "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
        "'information_schema') ORDER BY 1")]


async def teardown() -> list[str]:
    """Operator connection, the teardown GUC, every DELETE keyed by this run's fixture ids (and the fixture
    custodian's two platform capability rows, by their reserved code)."""
    op = await operator_conn()
    try:
        secs = await fixture_security_ids(op)
        users = list(USERS)
        orgs = FIXTURE_ORGS
        by_org = "org_id = ANY($1::uuid[])"
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan = {t: (by_org, [orgs]) for t in (
            "public.block_events", "public.block_charges", "public.block_fills", "public.block_allocations",
            "public.allocation_corrections", "public.trade_blocks", "public.allocation_policies",
            "public.execution_accounts", "public.instruction_settlements", "public.account_authority_settings",
            "public.execution_instruction_events", "public.execution_fills", "public.execution_instruction_lots",
            "public.execution_instructions", "public.domain_event_deliveries", "public.domain_events",
            "public.workflow_run_steps", "public.agent_proposals", "public.workflow_runs", "public.workflow_steps",
            "public.workflow_versions", "public.workflow_definitions", "portfolio.tax_lots", "portfolio.transactions",
            "portfolio.positions", "portfolio.assets", "public.account_owners", "public.accounts", "public.entities",
            "public.org_settings")}
        plan["public.member_todos"] = org_or_user
        plan["public.audit_log"] = org_or_user
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [secs])
        plan["public.custodian_capabilities"] = ("custodian_code = $1", [FRAC_KEY])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        if "public.assistant_activities" in exists:
            plan["public.assistant_activities"] = org_or_user
        plan = {t: v for t, v in plan.items() if t in exists}
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_rep9")
        return res["log"]
    finally:
        await close_conn(op)


TEARDOWN_RETRYABLE = (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
                      ConnectionError)


async def teardown_retrying() -> list[str]:
    try:
        return await teardown()
    except TEARDOWN_RETRYABLE as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; rolled back, retrying once")
        try:
            return await teardown()
        except Exception as exc2:  # noqa: BLE001
            check(False, "teardown retry failed", f"{type(exc2).__name__}: {exc2}")
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL", flush=True)
            os._exit(1)


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])", [list(SEC_NAMES.values())]),
        ("SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code = $1", [FRAC_KEY]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    op = await operator_conn()
    try:
        for t in await org_tables(op):
            n += int(await op.fetchval(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    finally:
        await close_conn(op)
    return n


async def pre_teardown(conn):
    log = await teardown_retrying()
    if log:
        find("pre-run cleanup removed an earlier run's leftovers", "; ".join(log))
    else:
        print("[INFO] pre-run cleanup: nothing left from an earlier run")
    check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api_raw(uid, method, path, body=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep9"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


async def api(uid, method, path, body=None, timeout=240.0):
    code, content, _ = await api_raw(uid, method, path, body, timeout)
    try:
        return code, json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        return code, {"raw": content[:300].decode("utf-8", "replace")}


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep9: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    for cls in [c for c in vars(TR).values() if isinstance(c, type) and issubclass(c, TR.TradeAdapter)
                and c not in (TR.TradeAdapter, TR.ManualTradeAdapter)]:
        orig = cls.submit

        async def recording(self, ctx, trade_list, *a, _orig=orig, _name=cls.__name__, **kw):
            (SETUP_ADAPTER_CALLS if _IN_SETUP[0] else ADAPTER_CALLS).append(f"{_name}:{trade_list.account_id}")
            return await _orig(self, ctx, trade_list, *a, **kw)

        cls.submit = recording


class _ctx:
    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def in_org(fn, org=None, super_admin=False):
    pool = await get_pool()
    with _ctx(org or ORG_A, super_admin):
        async with pool.acquire() as c2:
            return await fn(c2)


async def instruction(acct, key, side, qty=None, *, executor="broker", lots=None, by=None, rule="cancel",
                      notional=None, approve=True) -> tuple[str | None, list]:
    """A real rep2 instruction through the API: route → draft → submit (maker) → approve (checker)."""
    trade = {"global_security_id": str(S[f"sec_{key}"]), "side": side, "lot_instructions": lots or []}
    if notional is not None:
        trade["notional"] = str(notional)
    else:
        trade["quantity"] = str(qty)
    body = {"account_id": str(acct), "trades": [trade], "executor_type": executor, "executor_name": BROKER,
            "execute_by": (by or S["by"]).isoformat(), "if_not_filled": rule}
    # rep9 fix 1 — fixture setup: rep2's own creation path routes through custody/trading.route_trade_list, which
    # picks the custodian's adapter (Altruist → manual). That is setup, not a block path: recorded apart.
    _IN_SETUP[0] = True
    try:
        c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", body)
    finally:
        _IN_SETUP[0] = False
    iid = ((o.get("routing") or {}).get("instruction_ids") or [None])[0]
    codes = [c]
    if iid and approve:
        codes.append((await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{iid}/submit", {}))[0])
        codes.append((await api(U_ADMIN2, "POST", f"/api/v1/execution/instructions/{iid}/approve", {}))[0])
    return iid, codes if iid else [c, str(o)[:200]]


async def ins_row(conn, iid):
    return await scoped(conn, ORG_A, "SELECT * FROM public.execution_instructions WHERE id = $1 AND system_to IS NULL",
                        iid, fetch="fetchrow")


async def block_detail(bid, uid=U_ADMIN1):
    return await api(uid, "GET", f"/api/v1/execution/blocks/{bid}")


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    find("1a life of an approved trade", "rebalance_proposals.execute → custody/trading.route_trade_list → "
         "ManualTradeAdapter → execution.create_instructions (execution_instructions + execution_instruction_lots; "
         "instruction_settlements for managed + other custodian) → mark sent → execution_fills (lots on the fill) → "
         "reconcile → close. A block sits ABOVE approved account instructions (block_allocations.instruction_id); "
         "rep2's single-account flow is untouched, except that an instruction held in an OPEN block is sent / filled "
         "/ cancelled through the block and skipped by rep2's execute-by sweep")
    find("1a fills never write portfolio.tax_lots", "rep2's equity path records each fill (and its lots, as jsonb) "
         "on the instruction; the book's lots arrive from the custodian feed / held-away import at reconcile. rep9 "
         "books each account's allocation the same way (a rep2 fill at the average price with its own lots) — no "
         "second write path into portfolio.tax_lots")
    find("1b eligibility", "approved (or a carried remainder), quantity-based (not notional), not an option leg, "
         "executor broker / trader / custodian desk, authority managed with custody integrated or other_custodian, "
         "accounts.is_discretionary and not a rep5 advice_only proposal, execute-by not passed; one security, one "
         "side, one executor, one execute-by per block. advise / read_only never")
    find("1c omnibus", "execution_accounts: an org-level record of the firm's account at a broker (masked "
         "reference, DVP-capable flag) — never a member account (no FK to public.accounts)")
    find("1d rounding", "fractional only where the account's custodian has fractional_shares as an evidenced 'yes' "
         "(every real custodian is 'unknown' today → whole shares); largest remainder then account id; capped at "
         "each fixed allocation; minimum allocation → zero with the reason")
    find("1e sheet vs rep2 letter", "rep2's letter renders account / trade / price / lots for a member or another "
         "adviser; the order execution sheet adds the executing account, the allocation table with each "
         "allocation's settlement instruction, the price guidance's source + as-of, the firm contact, and never a "
         "member name")
    find("permissions", "new view_blocks / manage_blocks / approve_blocks rather than reusing approve_proposals: "
         "approving a block FIXES an allocation across accounts under the firm's written policy — a separate act from "
         "approving one account's proposal; the instruction sheet keeps rep2's view_execution (like its letter)")


async def y_schema(conn):
    section("[Y] Schema landed: tables, CHECKs, partial uniques, guards, RLS with a policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep9.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep9.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    for t in NEW_TABLES:
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                       "tablename = $1", t.split(".")[1])}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY($1::text[])",
        [t.split(".")[1] for t in NEW_TABLES])}
    check(all("UNIQUE" in idx.get(n, "") and "WHERE" in idx.get(n, "") for n in (
        "allocation_policies_live_uq", "allocation_policies_pending_uq", "block_allocations_open_instruction_uq",
        "allocation_corrections_pending_uq")),
        "partial unique indexes read from the catalog before seeding (one approved policy per org; one pending; an "
        "instruction in at most one open allocation; one pending correction per block)")
    fks = await platform(conn, "SELECT conrelid::regclass::text AS t, conname FROM pg_constraint WHERE contype = 'f' "
                               "AND conrelid = ANY($1::regclass[])", list(NEW_TABLES))
    check(len(fks) >= 20, "FK constraints on the new tables read from the catalog", f"{len(fks)}")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog (CHECKs) before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'::text", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    check(not bad, f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints)",
          "; ".join(bad))
    codes = {r["code"] for r in await platform(conn, "SELECT code FROM public.transaction_types WHERE code = "
                                                     "ANY($1::text[])", ["buy", "sell"])}
    check(codes == {"buy", "sell"}, "transaction_type_code 'buy' / 'sell' exist", f"{sorted(codes)}")
    S["literals_ok"] = not bad


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'REP9 VERIFY%'",
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    cap = await platform(conn, "SELECT count(*) FROM public.custodian_capabilities WHERE custodian_code = $1",
                         FRAC_KEY, fetch="fetchval")
    acc = await platform(conn, "SELECT count(*) FROM public.accounts WHERE upper(custodian_code) = $1", FRAC_CODE,
                         fetch="fetchval")
    ent = await platform(conn, "SELECT count(*) FROM public.entities WHERE display_name = $1", MEMBER_NAME,
                         fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(o) == 0 and int(cap) == 0 and int(acc) == 0 and int(ent) == 0,
                 f"the reserved prefixes ({PREFIX}, 'REP9 VERIFY', the fixture orgs, custodian {FRAC_CODE}, the member "
                 "name) match no real row", f"{n}/{k}/{o}/{cap}/{acc}/{ent}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP9 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        desc, perms = C.BASELINE_ROLES["super_admin"]
        rid_super = await rbac.ensure_role(conn, ORG_A, "super_admin", desc)
        for p in perms:
            if p in perm_ids:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid_super, perm_ids[p])
        rid_view = await rbac.ensure_role(conn, ORG_A, "rep9_viewer", f"REP9 verify — {', '.join(VIEWER_PERMS)}")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p])
        roles = {"super_admin": rid_super, "rep9_viewer": rid_view}
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for ent, org, name in ((ENT_A, ORG_A, MEMBER_NAME), (ENT_B, ORG_B, "REP9 Verify Org B Member")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, name)
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type="equity", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:04d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        # the fixture custodian: fractional shares and an equity trading API, both an evidenced 'yes' (platform rows)
        for key in ("fractional_shares", "equity_trading_api"):
            await conn.execute(
                "INSERT INTO public.custodian_capabilities (custodian_code, capability_key, value, evidence_kind, "
                "evidence, as_of, version_no, note) VALUES ($1, $2, 'yes', 'assumed', 'REP9 verify fixture', $3, 1, "
                "'REP9 verify fixture custodian')", FRAC_KEY, key, today())
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, held, disc, raw, _role) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, $8)",
                aid, org, n.masked, n.hashed(salts[org]), code, ENT_A if org == ORG_A else ENT_B, disc, held)
        # lots to sell (security L): A1 two lots, A3 one fractional lot
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        S["asset_L"], S["asset_K"] = uuid.uuid4(), uuid.uuid4()
        for aid_, key in ((S["asset_L"], "L"), (S["asset_K"], "K")):
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", aid_, ORG_A, S[f"sec_{key}"], SEC_NAMES[key])
        S["lots"] = {}
        for acct, qty, lots in ((A1, "100", (("L1", "60", date(2019, 1, 2)), ("L2", "40", date(2020, 3, 4)))),
                                (A3, "30.5", (("L3", "30.5", date(2019, 6, 7)),))):
            pid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, 'manual', 'manual', $7)",
                pid, ORG_A, ENT_A, S["asset_L"], today() - timedelta(days=1), D(qty), acct)
            for name, q, acq in lots:
                lid = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                    "original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $6, 'long', 'open')",
                    lid, ORG_A, pid, acq, D(q), D(q) * D(15))
                S["lots"][name] = lid
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(len(orgs) == 2 and all(r["is_test"] is True for r in orgs), "every fixture org is is_test = true (the live "
          "scheduler skips them)", f"{[dict(r) for r in orgs]}")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test AND auth0_sub LIKE $2",
                        list(USERS), f"%{TAG}%", fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly with the token's auth0_sub, is_test = true")
    # authority / custody through rep7b's real path (a second person approves each)
    for acct, body in ((A4, {"authority_override": "managed", "custody": "other_custodian", "receiving": RECEIVING}),
                       (A5, {"authority_override": "advise", "custody": "outside"})):
        c, o = await api(U_ADMIN1, "POST", f"/api/v1/authority/accounts/{acct}/changes",
                         {"effective_from": today().isoformat(), "reason": "REP9 verify", **body})
        cid = (o.get("change") or {}).get("id")
        ca, _ = await api(U_ADMIN2, "POST", f"/api/v1/authority/changes/{cid}/approve", {})
        check(c == 201 and ca == 200, f"{masked(acct)}: {ACCOUNTS[acct][5]} set through rep7b's approved change",
              f"{c} {ca} {str(o)[:160]}")
    S["seeded"] = True


async def y_policy_accounts(conn):
    section("[Y] Allocation policy (versioned, a second person approves) and executing accounts (masked, org-level)")
    c0, o0 = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", {"instruction_ids": [str(uuid.uuid4())]})
    check(c0 == 409 and "no approved allocation policy" in str(o0), "no block is built before the firm's policy is "
          "approved", f"{c0} {str(o0)[:160]}")
    body = {"minimum_allocation": str(MINIMUM), "policy_text": "REP9 verify: the firm aggregates client orders "
            "only when it is in each client's best interest; pro rata on the requested quantity.",
            "adv_reference": "Form ADV Part 2A, Item 12", "reason": "first version"}
    cf, of_ = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks/settings/policies",
                        {**body, "minimum_allocation": 5.0})
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks/settings/policies", body)
    pid = (o.get("policy") or {}).get("id")
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/settings/policies/{pid}/approve", {})
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/execution/blocks/settings/policies/{pid}/approve", {})
    row = await scoped(conn, ORG_A, "SELECT status, version_no, minimum_allocation, maker_id, approved_by, "
                                    "approval_task_id FROM public.allocation_policies WHERE id = $1 AND system_to IS NULL",
                       pid, fetch="fetchrow")
    check(cf == 422 and c == 201 and cm == 403 and ca == 200 and row is not None and row["status"] == "approved"
          and row["version_no"] == 1 and row["minimum_allocation"] == MINIMUM and row["approved_by"] == U_ADMIN2
          and row["maker_id"] == U_ADMIN1 and row["approval_task_id"] is not None,
          "policy version 1: a float minimum refused (422); written by one admin, refused to its maker (403), "
          "approved by a second (a human task)", f"{cf} {c} {cm} {str(om)[:100]} {ca} {str(oa)[:100]} {row}")
    S["policy"] = pid
    cfull, ofull = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks/settings/execution-accounts",
                             {"broker_name": BROKER, "reference_last4": "12345", "dvp_capable": True})
    c2, o2 = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks/settings/execution-accounts",
                       {"broker_name": BROKER, "reference_last4": EA_LAST4, "dvp_capable": True,
                        "contact": "desk@zzbroker.test"})
    ea = o2.get("execution_account") or {}
    check(cfull == 422 and c2 == 201 and ea.get("account_reference_masked") == f"****{EA_LAST4}",
          "executing account: stored masked (last four only; a longer reference refused); an org record, not a "
          "member account", f"{cfull} {c2} {ea}")
    S["ea"] = ea.get("id")
    fk = await platform(conn, "SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.execution_accounts'"
                              "::regclass AND contype = 'f' AND confrelid = 'public.accounts'::regclass",
                        fetch="fetchval")
    check(int(fk) == 0, "execution_accounts has no FK to public.accounts (never a member account)", f"{fk}")


async def y_eligibility(conn):
    section("[Y] Eligibility: advise / read-only / non-discretionary / notional refused with the reason; opposite "
            "sides never netted; suggestions")
    S["by"] = today() + timedelta(days=5)
    made = {}
    for name, acct, key, side, qty, ex in (
            ("buy_A1", A1, "K", "buy", "200", "broker"), ("buy_A2", A2, "K", "buy", "6", "broker"),
            ("buy_A3", A3, "K", "buy", "100.5", "broker"), ("buy_A4", A4, "K", "buy", "100", "broker"),
            ("sell_A2", A2, "K", "sell", "8", "broker"), ("sell_A2_small", A2, "K", "sell", "3", "broker"),
            ("buy_A7", A7, "K", "buy", "10", "broker"),
            ("adv_A5", A5, "K", "buy", "10", "member")):
        iid, codes = await instruction(acct, key, side, qty, executor=ex)
        made[name] = iid
        check(iid is not None and codes == [201, 200, 200], f"instruction {name} ({ACCOUNTS[acct][5]}) created, "
              "submitted and approved by a second person", f"{codes}")
    nid, ncodes = await instruction(A1, "M", "buy", notional="1000")
    made["notional_A1"] = nid
    check(nid is not None and ncodes == [201, 200, 200], "a notional instruction (approved) exists to be refused",
          f"{ncodes}")
    S["ins"] = made
    cro, oro = await api(U_ADMIN1, "POST", "/api/v1/execution/instructions", {
        "account_id": str(A6), "trades": [{"global_security_id": str(S["sec_K"]), "side": "buy", "quantity": "1"}],
        "executor_type": "broker", "execute_by": S["by"].isoformat(), "if_not_filled": "cancel"})
    check(cro == 409 and "read-only" in str(oro), "a read-only account cannot even have an instruction (rep7b)",
          f"{cro} {str(oro)[:120]}")
    c, o = await api(U_ADMIN1, "GET", "/api/v1/execution/blocks/candidates")
    elig = {e["instruction_id"] for e in o.get("eligible") or []}
    inel = {e["instruction_id"]: e["reason"] for e in o.get("ineligible") or []}
    want_elig = {made[k] for k in ("buy_A1", "buy_A2", "buy_A3", "buy_A4", "sell_A2")}
    check(c == 200 and want_elig <= elig and bool(want_elig),
          "managed discretionary broker instructions (integrated, fractional and DVP) are eligible",
          f"{c} missing={want_elig - elig}")
    check("authority advise" in inel.get(made["adv_A5"], "") and "non-discretionary" in inel.get(made["buy_A7"], "")
          and "notional" in inel.get(made["notional_A1"], "") and not ({made["adv_A5"], made["buy_A7"]} & elig),
          "ineligible ones listed with the plain reason: advise (never in a block), non-discretionary (advice only), "
          "notional (no share quantity)", f"{ {k: inel.get(made[k]) for k in ('adv_A5', 'buy_A7', 'notional_A1')} }")
    acct6 = await in_org(lambda c_: BK.load_account(c_, ORG_A, A6))
    pure = BK.eligibility({"id": uuid.uuid4(), "status": "approved", "quantity": D(1), "executor_type": "broker",
                           "execute_by": S["by"], "option_terms": None}, acct6, advice_only=False, open_block=None,
                          carried=False, remaining=D(1), today=today())
    check(pure is not None and "read-only" in pure, "a read-only account is refused by the eligibility rule itself "
          "(with the reason)", f"{pure}")
    sug = [s for s in o.get("suggestions") or [] if s["side"] == "buy" and s["global_security_id"] == str(S["sec_K"])]
    check(len(sug) == 1 and set(sug[0]["instruction_ids"]) == {made[k] for k in ("buy_A1", "buy_A2", "buy_A3",
                                                                                   "buy_A4")},
          "the system suggests the buy-K grouping (security / side / executor / window) of the four eligible accounts",
          f"{sug}")
    cn, on = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks/preview",
                       {"instruction_ids": [made["buy_A1"], made["sell_A2"]]})
    check(cn == 409 and "never netted" in str(on), "opposite sides of the same security are never netted across "
          "accounts (refused)", f"{cn} {str(on)[:160]}")
    cp, op_ = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks/preview",
                        {"instruction_ids": [made["buy_A1"], made["adv_A5"]]})
    ref = (op_.get("preview") or {}).get("refused") or []
    check(cp == 200 and any(r["instruction_id"] == made["adv_A5"] and "advise" in r["reason"] for r in ref),
          "the preview lists an ineligible pick with its reason (never silently dropped)", f"{cp} {ref}")


async def y_build_approve(conn):
    section("[Y] Build → allocation fixed at approval (maker refused, checker approves); route fix refused; cross flag")
    ids = [S["ins"][k] for k in ("buy_A1", "buy_A2", "buy_A3", "buy_A4")]
    S["ref_at"] = datetime.now(timezone.utc)
    body = {"instruction_ids": ids, "execution_account_id": S["ea"], "executor_name": BROKER, "price_kind": "limit",
            "limit_price": "10.50", "reference_price": "10.00", "reference_at": S["ref_at"].isoformat(),
            "if_not_filled": "cancel", "firm_contact": "trading@rep9verify.test"}
    cfix, ofix = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", {**body, "route": "fix"})
    check(cfix == 409 and "not connected" in str(ofix), "route fix refused with the reason (no FIX router connected)",
          f"{cfix} {str(ofix)[:160]}")
    cfl, _ = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", {**body, "limit_price": 10.5})
    check(cfl == 422, "a JSON float price is refused (decimal strings only)", f"{cfl}")
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", body)
    bid = (o.get("block") or {}).get("id")
    S["buy_block"] = bid
    if not check(c == 201 and bid is not None, "a draft buy block is built over the four accounts", f"{c} {str(o)[:200]}"):
        return
    rows = await scoped(conn, ORG_A, "SELECT * FROM public.block_allocations WHERE block_id = $1 AND system_to IS NULL "
                                     "ORDER BY account_id", bid)
    by = {r["account_id"]: r for r in rows}
    check(len(rows) == 4 and all(r["status"] == "planned" and r["allocated_quantity"] == r["requested_quantity"]
                                 for r in rows)
          and by[A3]["quantity_unit"] == D("0.00000001") and by[A1]["quantity_unit"] == D(1)
          and by[A4]["settlement_kind"] == "dvp" and by[A1]["settlement_kind"] == "integrated"
          and by[A4]["receiving_custodian"] == RECEIVING["custodian_name"],
          "pre-trade allocation = requested (pro_rata_requested); whole vs fractional units from the custodian's "
          "capability; DVP vs integrated per account", f"{[(r['account_id'], r['allocated_quantity'], r['quantity_unit'], r['settlement_kind']) for r in rows]}")
    b = await scoped(conn, ORG_A, "SELECT * FROM public.trade_blocks WHERE id = $1 AND system_to IS NULL", bid,
                     fetch="fetchrow")
    check(b["total_quantity"] == D("406.5") and b["route"] == "manual" and b["reference_source"] == "adviser"
          and b["reference_price"] == D("10.000000") and b["reference_as_of"] is not None
          and b["policy_id"] == UUID(S["policy"]) and b["cross_flag"] is False,
          "block: total 406.5, manual route, the typed reference price with its source and as-of, the policy "
          "version", f"{dict(b)}")
    cs, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/submit", {})
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/approve", {})
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/execution/blocks/{bid}/approve", {})
    rows2 = await scoped(conn, ORG_A, "SELECT status, allocated_quantity FROM public.block_allocations WHERE "
                                      "block_id = $1 AND system_to IS NULL", bid)
    b2 = await scoped(conn, ORG_A, "SELECT status, approved_by FROM public.trade_blocks WHERE id = $1 AND system_to "
                                   "IS NULL", bid, fetch="fetchrow")
    check(cs == 200 and cm == 403 and "maker-checker" in str(om) and ca == 200 and b2["status"] == "approved"
          and b2["approved_by"] == U_ADMIN2 and all(r["status"] == "fixed" for r in rows2) and len(rows2) == 4,
          "submit; the maker's approval refused (403); a second person approves and the allocation is FIXED",
          f"{cs} {cm} {ca} {dict(b2)} {[dict(r) for r in rows2]}")
    try:
        await scoped(conn, ORG_A, "UPDATE public.block_allocations SET allocated_quantity = allocated_quantity - 1 "
                                  "WHERE block_id = $1 AND account_id = $2 AND system_to IS NULL", bid, A1,
                     fetch="execute")
        err = None
    except asyncpg.PostgresError as exc:
        err = exc
    check(err is not None and getattr(err, "sqlstate", "") == "42501" and "fixed at approval" in str(err),
          "the database refuses any change to a fixed allocation (app_service, RLS context set)", f"{err!r}")
    # cross-trade risk: a sell block of the same security, same window
    # rep9 fix 1 — reproduce the original failure first: a sell BELOW the policy minimum (3 < 5) used to be refused
    # on the minimum alone, the cross-trade risk never reported. Now the refusal names both; nothing is written.
    n_before = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.trade_blocks WHERE system_to IS NULL",
                                fetch="fetchval"))
    css, oss = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks",
                         {"instruction_ids": [S["ins"]["sell_A2_small"]], "execution_account_id": S["ea"],
                          "executor_name": BROKER})
    n_after = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.trade_blocks WHERE system_to IS NULL",
                               fetch="fetchval"))
    check(css == 409 and "minimum allocation" in str(oss) and "cross-trade" in str(oss) and bid in str(oss)
          and n_after == n_before,
          "a below-minimum sell in the buy block's window: the refusal names the minimum AND the cross-trade risk "
          "(the buy block named) — the flag is never hidden by another refusal; no block written",
          f"{css} {str(oss)[:240]} {n_before}->{n_after}")
    sb = {"instruction_ids": [S["ins"]["sell_A2"]], "execution_account_id": S["ea"], "executor_name": BROKER}
    cx, ox = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", sb)
    cx2, ox2 = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", {**sb, "cross_reason": "REP9 verify: the "
                                                                          "sell is a different client decision"})
    xid = (ox2.get("block") or {}).get("id")
    xrow = await scoped(conn, ORG_A, "SELECT cross_flag, cross_reason FROM public.trade_blocks WHERE id = $1 AND "
                                     "system_to IS NULL", xid, fetch="fetchrow") if xid else None
    cc, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{xid}/cancel", {"reason": "REP9 verify"})
    check(cx == 409 and "cross-trade" in str(ox) and bid in str(ox) and cx2 == 201 and xrow is not None
          and xrow["cross_flag"] is True and cc == 200,
          "a sell block of the same security in the same window is flagged (cross-trade risk, the buy block named) "
          "and needs a reason; then cancelled", f"{cx} {str(ox)[:160]} {cx2} {xrow}")


async def y_sheet(conn):
    section("[Y] Order execution sheet (block): %PDF, masked references, no member names, allocation table + DVP, "
            "price guidance source + as-of")
    bid = S.get("buy_block")
    code, pdf, hdr = await api_raw(U_VIEWER, "POST", f"/api/v1/execution/blocks/{bid}/sheet", {})
    text = pdf_text(pdf) if code == 200 else ""
    d = await in_org(lambda c_: BK._sheet_dict(c_, ORG_A, bid))
    rows = OS.sheet_rows(d)
    alloc = OS.allocation_rows(d)
    flat = " ".join(str(v) for _, v in rows) + " " + " ".join(" ".join(a) for a in alloc)
    raws = [ACCOUNTS[a][4] for a in (A1, A2, A3, A4)]
    check(code == 200 and pdf[:4] == b"%PDF" and "no-store" in hdr.get("cache-control", ""),
          "a viewer (view_blocks) downloads the sheet: %PDF, never cached", f"{code} {pdf[:8]!r}")
    check(all(masked(a) in flat for a in (A1, A2, A3, A4)) and f"****{EA_LAST4}" in flat
          and not any(r in flat for r in raws) and MEMBER_NAME not in flat,
          "sheet rows: every account masked, the executing account masked, no full number, no member name",
          f"{flat[:300]}")
    check(bool(text) and f"****{EA_LAST4}" in text and masked(A4) in text and not any(r in text for r in raws)
          and MEMBER_NAME not in text and "Whitcombe" not in text,
          "the PDF itself (inflated): masked references present, no full number, no member name", f"{len(text)}")
    dvp = next((a for a in alloc if a[0] == masked(A4)), None)
    check(dvp is not None and "0901" in dvp[3] and RECEIVING["custodian_name"] in dvp[3] and "****4321" in dvp[3]
          and any("integrated custodian" in a[3] for a in alloc if a[0] == masked(A1)),
          "allocation table: quantity, receiving custodian and the DVP instruction (participant, masked account) per "
          "allocation; integrated accounts receive versus payment", f"{alloc}")
    guide = dict(rows).get("Price guidance", "")
    check("Limit 10.50" in guide and "10.000000" in guide and "typed by the adviser" in guide
          and S["ref_at"].date().isoformat() in guide and "406.5" in dict(rows).get("Total quantity", "")
          and bid in dict(rows).get("Reference", "") and dict(rows).get("Execute by") == S["by"].isoformat()
          and "rep9verify" in dict(rows).get("Firm contact", ""),
          "price guidance with its source and as-of; total quantity; execute-by; firm contact; block id", f"{rows}")
    ev = await scoped(conn, ORG_A, "SELECT actor_id, detail FROM public.block_events WHERE block_id = $1 AND action = "
                                   "'sheet'", bid)
    check(len(ev) == 1 and ev[0]["actor_id"] == U_VIEWER, "generating the sheet is logged (who), never stored",
          f"{[dict(r) for r in ev]}")


async def y_fills_allocate(conn):
    section("[Y] Sent; fills; average price exact over three fills; partial fill pro rata with deterministic rounding "
            "and the minimum allocation; lots / fills per account at the average price; DVP rewritten")
    bid = S.get("buy_block")
    cs, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/sent", {"channel": "email"})
    ins = await scoped(conn, ORG_A, "SELECT id, status, sent_channel, sent_by FROM public.execution_instructions WHERE "
                                    "id = ANY($1::uuid[]) AND system_to IS NULL",
                       [S["ins"][k] for k in ("buy_A1", "buy_A2", "buy_A3", "buy_A4")])
    check(cs == 200 and len(ins) == 4 and all(r["status"] == "sent" and r["sent_channel"] == "email"
                                              and r["sent_by"] == U_ADMIN1 for r in ins),
          "marking the block sent records the channel and who, on the block and on each account instruction",
          f"{cs} {[dict(r) for r in ins]}")
    cd, od = await api(U_ADMIN1, "POST", f"/api/v1/execution/instructions/{S['ins']['buy_A1']}/fills",
                       {"quantity": "1", "price": "10", "filled_at": datetime.now(timezone.utc).isoformat()})
    check(cd == 409 and "block" in str(od), "an account instruction in an open block is never filled on its own",
          f"{cd} {str(od)[:120]}")
    now = datetime.now(timezone.utc)
    fills = [("100", "10.01", now - timedelta(minutes=30)), ("100", "10.02", now - timedelta(minutes=20)),
             ("33", "10.035", now - timedelta(minutes=10))]
    codes = []
    for q, p, t in fills:
        codes.append((await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/fills",
                                {"quantity": q, "price": p, "filled_at": t.isoformat(), "venue": "ZZ venue"}))[0])
    avg = exp_avg([(q, p) for q, p, _ in fills])
    b = await scoped(conn, ORG_A, "SELECT status, filled_quantity, average_price FROM public.trade_blocks WHERE id = $1 "
                                  "AND system_to IS NULL", bid, fetch="fetchrow")
    check(codes == [200, 200, 200] and b["status"] == "partially_filled" and b["filled_quantity"] == D(233)
          and b["average_price"] == avg and avg == D("10.017833"),
          "three fills: the average price is computed exactly (Σq·p / Σq, 6 dp half up = 10.017833), never entered",
          f"{codes} {dict(b)} expected {avg}")
    over, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/fills",
                        {"quantity": "500", "price": "10", "filled_at": now.isoformat()})
    check(over == 422, "a fill beyond the block's total is refused", f"{over}")
    allocs = await scoped(conn, ORG_A, "SELECT id, account_id, allocated_quantity, quantity_unit, instruction_id FROM "
                                       "public.block_allocations WHERE block_id = $1 AND system_to IS NULL", bid)
    expected = exp_partial([(str(r["id"]), str(r["account_id"]), r["allocated_quantity"], r["quantity_unit"])
                            for r in allocs], D(233), MINIMUM)
    old_st = await scoped(conn, ORG_A, "SELECT id FROM public.instruction_settlements WHERE instruction_id = $1 AND "
                                       "valid_to IS NULL AND system_to IS NULL", S["ins"]["buy_A4"], fetch="fetchval")
    ca, oa = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/allocate", {})
    trade_day = max(t for _, _, t in fills).astimezone(timezone.utc).date()     # computed at the call
    got = await scoped(conn, ORG_A, "SELECT * FROM public.block_allocations WHERE block_id = $1 AND system_to IS NULL",
                       bid)
    g = {r["account_id"]: r for r in got}
    e = {r["account_id"]: expected[str(r["id"])] for r in allocs}
    check(ca == 200 and all(g[a]["final_quantity"] == e[a] for a in g) and sum(e.values()) == D(233)
          and sum((g[a]["final_quantity"] for a in g), Decimal(0)) == D(233),
          "partial fill: each account's quantity equals the rule computed independently here, and the total equals "
          "the filled 233 exactly", f"{ca} {str(oa)[:160]} got={ {str(a)[-3:]: str(g[a]['final_quantity']) for a in g} } "
          f"expected={ {str(a)[-3:]: str(v) for a, v in e.items()} }")
    check(e[A2] == 0 and g[A2]["final_quantity"] == 0 and "minimum allocation" in (g[A2]["zero_reason"] or "")
          and g[A2]["settlement_status"] == "cancelled",
          "minimum allocation: the small account's pro-rata share is below 5 → zero, with the reason",
          f"{g[A2]['zero_reason']}")
    check(all(g[a]["final_quantity"] == g[a]["final_quantity"].to_integral_value() for a in (A1, A2, A4))
          and g[A3]["final_quantity"] == g[A3]["final_quantity"].quantize(D("0.00000001")),
          "whole-share accounts are integers; the fractional account is to 8 dp", f"{[str(g[a]['final_quantity']) for a in (A1, A2, A3, A4)]}")
    xf = await scoped(conn, ORG_A, "SELECT f.instruction_id, f.quantity, f.price, f.completes, i.status FROM "
                                   "public.execution_fills f JOIN public.execution_instructions i ON i.id = "
                                   "f.instruction_id AND i.system_to IS NULL WHERE f.instruction_id = ANY($1::uuid[])",
                      [S["ins"][k] for k in ("buy_A1", "buy_A2", "buy_A3", "buy_A4")])
    byi = {r["instruction_id"]: r for r in xf}
    i2 = await ins_row(conn, S["ins"]["buy_A2"])
    check(len(xf) == 3 and all(r["price"] == avg and r["completes"] is True and r["status"] == "filled" for r in xf)
          and all(byi[UUID(S["ins"][k])]["quantity"] == e[a] for k, a in (("buy_A1", A1), ("buy_A3", A3),
                                                                          ("buy_A4", A4)))
          and i2["status"] == "cancelled" and "minimum allocation" in (i2["cancel_reason"] or ""),
          "each account's own instruction gets a rep2 fill at the average price; the remainder cancelled per the "
          "block's rule (completed short); the zero account's instruction cancelled with the reason",
          f"{[dict(r) for r in xf]} {i2['status']}")
    st = await scoped(conn, ORG_A, "SELECT * FROM public.instruction_settlements WHERE instruction_id = $1 ORDER BY "
                                   "created_at", S["ins"]["buy_A4"])
    live = [r for r in st if r["valid_to"] is None]
    check(len(live) == 1 and live[0]["trade_date"] == trade_day
          and live[0]["settlement_date"] == next_business_day(trade_day) and live[0]["calendar_basis"] == "weekends_only"
          and live[0]["receiving_participant_number"] == "0901" and live[0]["receiving_account_masked"] == "****4321"
          and g[A4]["settlement_id"] == live[0]["id"] and any(r["id"] == old_st and r["valid_to"] is not None
                                                              for r in st),
          f"DVP: the other-custodian allocation gets rep7b's settlement instruction from the block's trade date "
          f"({trade_day} → T+1 {next_business_day(trade_day)}, weekends only), the creation-day one closed",
          f"{[(str(r['trade_date']), str(r['settlement_date']), r['valid_to'] is None) for r in st]}")
    check(g[A1]["settlement_kind"] == "integrated" and g[A1]["settlement_id"] is None
          and g[A1]["settlement_status"] == "pending",
          "integrated allocations settle at the custodian (receive versus payment; the desk instruction), pending", "")
    S["final"] = {a: g[a]["final_quantity"] for a in g}
    S["alloc_ids"] = {a: g[a]["id"] for a in g}
    S["avg"] = avg


async def y_corrections(conn):
    section("[Y] Corrections: only with a reason and a second person; the original kept; the database refuses "
            "anything else")
    bid = S.get("buy_block")
    a1, a4 = S["alloc_ids"][A1], S["alloc_ids"][A4]
    f1, f4 = S["final"][A1], S["final"][A4]
    try:
        await scoped(conn, ORG_A, "UPDATE public.block_allocations SET final_quantity = final_quantity - 1 WHERE id = $1 "
                                  "AND system_to IS NULL", a1, fetch="execute")
        err = None
    except asyncpg.PostgresError as exc:
        err = exc
    check(err is not None and "approved correction" in str(err), "a booked allocation cannot be changed directly "
          "(the database names the correction path)", f"{err!r}")
    cbad, obad = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/corrections",
                           {"changes": [{"allocation_id": str(a1), "quantity": str(f1 - 1)}], "reason": "x"})
    cnr, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/corrections",
                       {"changes": [{"allocation_id": str(a1), "quantity": str(f1 - 1)},
                                    {"allocation_id": str(a4), "quantity": str(f4 + 1)}], "reason": ""})
    body = {"changes": [{"allocation_id": str(a1), "quantity": str(f1 - 1)},
                        {"allocation_id": str(a4), "quantity": str(f4 + 1)}],
            "reason": "REP9 verify: trade error — one share booked to the wrong account"}
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/corrections", body)
    cid = (o.get("correction") or {}).get("id")
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/corrections/{cid}/approve", {})
    mid = await scoped(conn, ORG_A, "SELECT final_quantity FROM public.block_allocations WHERE id = $1 AND system_to "
                                    "IS NULL", a1, fetch="fetchval")
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/execution/blocks/corrections/{cid}/approve", {})
    now1 = await scoped(conn, ORG_A, "SELECT final_quantity, correction_id FROM public.block_allocations WHERE id = $1 "
                                     "AND system_to IS NULL", a1, fetch="fetchrow")
    now4 = await scoped(conn, ORG_A, "SELECT final_quantity FROM public.block_allocations WHERE id = $1 AND system_to "
                                     "IS NULL", a4, fetch="fetchval")
    hist = await scoped(conn, ORG_A, "SELECT final_quantity FROM public.block_allocations WHERE archived_from_id = $1 "
                                     "AND system_to IS NOT NULL", a1)
    corr = await scoped(conn, ORG_A, "SELECT status, maker_id, approved_by, reason FROM public.allocation_corrections "
                                     "WHERE id = $1 AND system_to IS NULL", cid, fetch="fetchrow")
    check(cbad == 422 and "total must stay" in str(obad) and cnr == 422 and c == 201 and cm == 403
          and mid == f1 and ca == 200 and now1["final_quantity"] == f1 - 1 and now4 == f4 + 1
          and str(now1["correction_id"]) == cid and any(h["final_quantity"] == f1 for h in hist)
          and corr["status"] == "approved" and corr["maker_id"] == U_ADMIN1 and corr["approved_by"] == U_ADMIN2,
          "a correction that changes the filled total refused; no reason refused; the maker's approval refused (no "
          "change); a second person approves; the original allocation stays readable (system axis)",
          f"{cbad} {cnr} {c} {cm} {str(om)[:80]} {mid} {ca} {str(oa)[:100]} {dict(now1)} {now4} "
          f"{[h['final_quantity'] for h in hist]}")
    ev = await scoped(conn, ORG_A, "SELECT detail FROM public.execution_instruction_events WHERE instruction_id = $1 AND "
                                   "action = 'edit'", S["ins"]["buy_A1"])
    check(any((json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]).get("block_correction") == cid
              for r in ev), "the correction is on the account instruction's timeline too", f"{len(ev)}")
    S["final"][A1], S["final"][A4] = f1 - 1, f4 + 1


async def y_settlement(conn):
    section("[Y] DVP settlement per allocation: statuses, a fail opens a to-do with the allocation and the "
            "counterparty, match from custodian data, the block closes only when every allocation settles")
    bid = S.get("buy_block")
    base = f"/api/v1/execution/blocks/{bid}/allocations"
    a1, a3, a4 = S["alloc_ids"][A1], S["alloc_ids"][A3], S["alloc_ids"][A4]
    c1, _ = await api(U_ADMIN1, "POST", f"{base}/{a4}/settlement", {"status": "affirmed"})
    cf0, _ = await api(U_ADMIN1, "POST", f"{base}/{a4}/settlement", {"status": "failed"})
    c2, _ = await api(U_ADMIN1, "POST", f"{base}/{a4}/settlement", {"status": "failed",
                                                                     "fail_reason": "REP9 verify: DK at the receiver"})
    todos = await scoped(conn, ORG_A, "SELECT user_id, title, detail FROM public.member_todos WHERE related_id = $1 AND "
                                      "source = 'block_settlement_failed'", a4)
    check(c1 == 200 and cf0 == 422 and c2 == 200 and len(todos) >= 2
          and {U_ADMIN1, U_ADMIN2} <= {t["user_id"] for t in todos}
          and all(masked(A4) in t["title"] and RECEIVING["custodian_name"] in t["detail"] and str(a4) in t["detail"]
                  for t in todos),
          "pending → affirmed → failed (a reason required): a to-do for the makers and managers names the allocation "
          "and the counterparty", f"{c1} {cf0} {c2} {[dict(t) for t in todos]}")
    cc, oc = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/close", {})
    check(cc == 409 and masked(A4) in str(oc), "the block does not close while an allocation is unsettled (named)",
          f"{cc} {str(oc)[:160]}")
    cs4, _ = await api(U_ADMIN1, "POST", f"{base}/{a4}/settlement", {"status": "settled",
                                                                      "note": "REP9 verify: confirmed by phone"})
    # A1 matched from custodian data: a transaction of the account, same security, side and quantity
    positions: dict = {}

    async def seed_txn(acct, qty):
        tid = uuid.uuid4()
        if acct not in positions:
            positions[acct] = uuid.uuid4()
            await scoped(conn, ORG_A, "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, "
                                      "as_of_date, quantity, authority, source_system, account_id) VALUES ($1, $2, $3, "
                                      "$4, $5, $6, 'manual', 'manual', $7)", positions[acct], ORG_A, ENT_A,
                         S["asset_K"], today(), D(qty), acct, fetch="execute")
        pid = positions[acct]
        await scoped(conn, ORG_A, "INSERT INTO portfolio.transactions (id, org_id, position_id, transaction_type_code, "
                                  "trade_date, quantity, price, authority, source_system, "
                                  "is_corporate_action_adjustment) VALUES ($1, $2, $3, 'buy', $4, $5, $6, 'manual', "
                                  "'manual', false)", tid, ORG_A, pid, today(), D(qty), S["avg"], fetch="execute")
        return tid
    t_wrong = await seed_txn(A3, S["final"][A1])
    t_qty = await seed_txn(A1, S["final"][A1] + 7)
    t_ok = await seed_txn(A1, S["final"][A1])
    cw, ow = await api(U_ADMIN1, "POST", f"{base}/{a1}/settlement-match", {"record_kind": "transaction",
                                                                           "record_id": str(t_wrong)})
    cq, oq = await api(U_ADMIN1, "POST", f"{base}/{a1}/settlement-match", {"record_kind": "transaction",
                                                                           "record_id": str(t_qty)})
    cm, om = await api(U_ADMIN1, "POST", f"{base}/{a1}/settlement-match", {"record_kind": "transaction",
                                                                           "record_id": str(t_ok)})
    r1 = await scoped(conn, ORG_A, "SELECT settlement_status, settlement_method, settlement_reference FROM "
                                   "public.block_allocations WHERE id = $1 AND system_to IS NULL", a1, fetch="fetchrow")
    check(cw == 409 and "another account" in str(ow) and cq == 409 and cm == 200 and r1["settlement_status"] == "settled"
          and r1["settlement_method"] == "custodian_data" and r1["settlement_reference"] == f"transaction:{t_ok}",
          "settled when matched to the custodian feed (same account, security, side and quantity); another "
          "account's or a different quantity refused", f"{cw} {cq} {str(oq)[:100]} {cm} {str(om)[:100]} {dict(r1)}")
    b_mid = await scoped(conn, ORG_A, "SELECT status FROM public.trade_blocks WHERE id = $1 AND system_to IS NULL", bid,
                         fetch="fetchval")
    cs3, _ = await api(U_ADMIN1, "POST", f"{base}/{a3}/settlement", {"status": "settled", "note": "REP9 verify: seen "
                                                                                                  "on the statement"})
    b_after = await scoped(conn, ORG_A, "SELECT status, settled_at FROM public.trade_blocks WHERE id = $1 AND system_to "
                                        "IS NULL", bid, fetch="fetchrow")
    cc2, _ = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/close", {})
    b_end = await scoped(conn, ORG_A, "SELECT status, closed_by FROM public.trade_blocks WHERE id = $1 AND system_to "
                                      "IS NULL", bid, fetch="fetchrow")
    check(cs4 == 200 and b_mid == "allocated" and cs3 == 200 and b_after["status"] == "settled"
          and b_after["settled_at"] is not None and cc2 == 200 and b_end["status"] == "closed"
          and b_end["closed_by"] == U_ADMIN1,
          "the block settles only when every allocation is settled or cancelled (the zero allocation is cancelled), "
          "and only then closes", f"{cs4} {b_mid} {cs3} {dict(b_after)} {cc2} {dict(b_end)}")


async def y_full_fill_lots(conn):
    section("[Y] Full fill: every account exactly its fixed quantity; each account's own lots relieved at the "
            "average price (sells)")
    lots_a1 = [{"tax_lot_id": str(S["lots"]["L1"]), "quantity": "60"}, {"tax_lot_id": str(S["lots"]["L2"]),
                                                                        "quantity": "40"}]
    lots_a3 = [{"tax_lot_id": str(S["lots"]["L3"]), "quantity": "30.5"}]
    i1, c1 = await instruction(A1, "L", "sell", "100", lots=lots_a1, rule="ask")
    i3, c3 = await instruction(A3, "L", "sell", "30.5", lots=lots_a3, rule="ask")
    check(c1 == [201, 200, 200] and c3 == [201, 200, 200], "two approved sell instructions with their own lots",
          f"{c1} {c3}")
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", {"instruction_ids": [i1, i3], "execution_account_id":
                                                                    S["ea"], "executor_name": BROKER,
                                                                    "if_not_filled": "ask"})
    bid = (o.get("block") or {}).get("id")
    S["sell_block"] = bid
    steps = [c, (await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/submit", {}))[0],
             (await api(U_ADMIN2, "POST", f"/api/v1/execution/blocks/{bid}/approve", {}))[0],
             (await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/sent", {"channel": "phone"}))[0]]
    now = datetime.now(timezone.utc)
    fills = [("80", "20.00"), ("50.5", "20.10")]
    for q, p in fills:
        steps.append((await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/fills",
                                {"quantity": q, "price": p, "filled_at": (now - timedelta(minutes=5)).isoformat()}))[0])
    steps.append((await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/allocate", {}))[0])
    avg = exp_avg(fills)
    b = await scoped(conn, ORG_A, "SELECT status, reference_price, reference_source, reference_provider, "
                                  "reference_as_of, remainder_handling FROM public.trade_blocks WHERE id = $1 AND "
                                  "system_to IS NULL", bid, fetch="fetchrow")
    got = await scoped(conn, ORG_A, "SELECT account_id, allocated_quantity, final_quantity, average_price FROM "
                                    "public.block_allocations WHERE block_id = $1 AND system_to IS NULL", bid)
    check(steps == [201, 200, 200, 200, 200, 200, 200] and b["status"] == "allocated" and b["remainder_handling"] is None
          and len(got) == 2 and all(r["final_quantity"] == r["allocated_quantity"] and r["average_price"] == avg
                                    for r in got),
          f"full fill: each account gets exactly its fixed quantity at the average price {avg}",
          f"{steps} {dict(b) if b else None} {[dict(r) for r in got]}")
    check((b["reference_price"] is None and b["reference_source"] is None and b["reference_provider"] is None
           and b["reference_as_of"] is None) or (b["reference_source"] == "security_data"
                                                 and b["reference_provider"] is not None
                                                 and b["reference_as_of"] is not None),
          "no typed reference: the security-data layer's price with provider + as-of, or none at all — never invented",
          f"{dict(b)}")
    xf = await scoped(conn, ORG_A, "SELECT instruction_id, quantity, price, lots FROM public.execution_fills WHERE "
                                   "instruction_id = ANY($1::uuid[])", [i1, i3])
    lots = {str(r["instruction_id"]): sorted((x["tax_lot_id"], D(x["quantity"])) for x in
                                             (json.loads(r["lots"]) if isinstance(r["lots"], str) else r["lots"]))
            for r in xf}
    want1 = sorted([(str(S["lots"]["L1"]), D(60)), (str(S["lots"]["L2"]), D(40))])
    want3 = [(str(S["lots"]["L3"]), D("30.5"))]
    check(len(xf) == 2 and all(r["price"] == avg for r in xf) and lots.get(i1) == want1 and lots.get(i3) == want3,
          "lots booked per account at the average price: each account's own chosen lots relieved, nothing else",
          f"{lots}")
    tl = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.tax_lots WHERE id = ANY($1::uuid[]) AND status = "
                                   "'open' AND system_to IS NULL", list(S["lots"].values()), fetch="fetchval")
    find("lots on the book", f"the fills carry the lots; portfolio.tax_lots stays as the custodian feed last reported "
         f"it ({tl} of 3 still open) until the next import / reconcile — rep2's path, unchanged")
    cch, och = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/charges",
                         {"kind": "commission", "amount": "12.50", "payee": BROKER, "trade_away": True})
    ch = await scoped(conn, ORG_A, "SELECT kind, amount, trade_away FROM public.block_charges WHERE block_id = $1", bid)
    check(cch == 201 and len(ch) == 1 and ch[0]["kind"] == "commission" and ch[0]["trade_away"] is True
          and ch[0]["amount"] == D("12.50"),
          "a trade-away commission is recorded on the block (adviser-entered)", f"{cch} {str(och)[:100]} {ch}")
    ids = await scoped(conn, ORG_A, "SELECT id FROM public.block_allocations WHERE block_id = $1 AND system_to IS NULL",
                       bid)
    sc = []
    for r in ids:
        sc.append((await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/{bid}/allocations/{r['id']}/settlement",
                             {"status": "settled", "note": "REP9 verify"}))[0])
    bs = await scoped(conn, ORG_A, "SELECT status FROM public.trade_blocks WHERE id = $1 AND system_to IS NULL", bid,
                      fetch="fetchval")
    check(sc == [200, 200] and bs == "settled", "both integrated allocations settled (manual note) → the block settles",
          f"{sc} {bs}")


async def y_single_sheet(conn):
    section("[Y] Order execution sheet for ONE broker instruction; manual always offered; the choice over an "
            "available automated route recorded with its reason")
    i_auto, ca = await instruction(A3, "M", "buy", "5", rule="cancel")
    i_plain, cp = await instruction(A1, "M", "buy", "6", rule="cancel")   # fix 1: ≥ the minimum (y_expiry blocks it)
    S["ins"]["m_A3"], S["ins"]["m_A1"] = i_auto, i_plain
    cg, og = await api(U_ADMIN1, "GET", f"/api/v1/execution/instructions/{i_plain}")
    check(ca == [201, 200, 200] and cp == [201, 200, 200] and cg == 200 and "order_sheet" in (og.get("actions") or []),
          "a broker instruction's envelope offers order_sheet (the manual route on every trade)",
          f"{ca} {cp} {og.get('actions')}")
    c0, o0 = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/instructions/{i_auto}/sheet", {})
    c1, pdf, _ = await api_raw(U_ADMIN1, "POST", f"/api/v1/execution/blocks/instructions/{i_auto}/sheet",
                               {"manual_reason": "REP9 verify: the broker gets a better price on the open"})
    c2, pdf2, _ = await api_raw(U_ADMIN1, "POST", f"/api/v1/execution/blocks/instructions/{i_plain}/sheet", {})
    ev = await scoped(conn, ORG_A, "SELECT instruction_id, detail FROM public.execution_instruction_events WHERE "
                                   "instruction_id = ANY($1::uuid[]) AND action = 'letter'", [i_auto, i_plain])
    det = {str(r["instruction_id"]): (json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"])
           for r in ev}
    check(c0 == 409 and "automated route is available" in str(o0) and c1 == 200 and pdf[:4] == b"%PDF" and c2 == 200
          and pdf2[:4] == b"%PDF" and det.get(i_auto, {}).get("document") == "order_execution_sheet"
          and det.get(i_auto, {}).get("automated_route_available") is True
          and "better price" in (det.get(i_auto, {}).get("manual_reason") or "")
          and det.get(i_plain, {}).get("automated_route_available") is False,
          "an automated route available (custodian API = yes): manual needs a reason, then the sheet is generated and "
          "the choice + reason recorded; no automated route: the sheet without one", f"{c0} {str(o0)[:120]} {c1} {c2} {det}")
    t = pdf_text(pdf)
    check(masked(A3) in t and ACCOUNTS[A3][4] not in t and MEMBER_NAME not in t,
          "the single-instruction sheet: masked account, no full number, no member name", f"{len(t)}")
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/execution/blocks/instructions/{S['ins']['adv_A5']}/sheet", {})
    check(cm == 422 and "letter of instruction" in str(om), "a member-executed instruction gets its letter, not a sheet",
          f"{cm} {str(om)[:120]}")


async def y_fix(conn):
    section("[Y] FIX router interface: registered, NOT connected; route fix refused; no network")
    c, o = await api(U_ADMIN1, "GET", "/api/v1/execution/blocks/routers")
    rows = o.get("rows") or []
    check(c == 200 and bool(rows) and all(r["connected"] is False and r["status"] == "not_connected" for r in rows),
          "every registered router is 'not connected'", f"{rows}")
    refused = []
    for r in RT.registry().values():
        for call in (r.submit_block({}, []), r.cancel_block({}, "x"), r.execution_reports({}), r.send_allocations({}, [])):
            try:
                await call
                refused.append(False)
            except RT.RouterNotConnected:
                refused.append(True)
    check(bool(refused) and all(refused), "submit / cancel / execution reports / allocations all refuse with "
          "RouterNotConnected (no client code)", f"{refused}")
    d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                             "'trade_blocks_route_chk'", fetch="fetchval")
    check(d == "CHECK ((route = 'manual'::text))", "the database admits route 'manual' only", f"{d}")
    check(RT.route_refusal("manual") is None and "not connected" in (RT.route_refusal("fix") or ""),
          "manual is always available; fix is refused with the reason", "")
    # rep9 fix 1 — the spy is live (rep2's setup path DID reach Altruist's adapter for A1) and nothing outside setup
    # — build / approve / sent / fills / allocate / settle / sheets / corrections — reached any adapter
    check(any(c_.startswith("AltruistTradeAdapter:") and c_.endswith(str(A1)) for c_ in SETUP_ADAPTER_CALLS),
          "the adapter spy is live: fixture setup (rep2's POST /execution/instructions → route_trade_list) reached "
          "AltruistTradeAdapter for the integrated Altruist account", f"{SETUP_ADAPTER_CALLS[:3]}")
    check(NETWORK_CALLS == [] and ADAPTER_CALLS == [], "no network call and no custodian adapter reached by any "
          "block path", f"{NETWORK_CALLS[:3]} {ADAPTER_CALLS}")


async def y_expiry(conn):
    section("[Y] Execute-by: rep2's sweep skips an instruction held in an open block; the block's own rule applies")
    iid = S["ins"].get("m_A1")
    c, o = await api(U_ADMIN1, "POST", "/api/v1/execution/blocks", {"instruction_ids": [iid], "execution_account_id":
                                                                    S["ea"], "if_not_filled": "cancel"})
    bid = (o.get("block") or {}).get("id")
    later = S["by"] + timedelta(days=1)      # computed at the call
    pool = await get_pool()
    with _ctx(ORG_A):
        sweep = await X.expire_due(pool, today=later, org_id=ORG_A)
        bsweep = await BK.expire_due(pool, today=later, org_id=ORG_A)
    b = await scoped(conn, ORG_A, "SELECT status, cancel_reason FROM public.trade_blocks WHERE id = $1 AND system_to IS "
                                  "NULL", bid, fetch="fetchrow")
    ev = await scoped(conn, ORG_A, "SELECT actor_id FROM public.block_events WHERE block_id = $1 AND action = 'expire'",
                      bid)
    al = await scoped(conn, ORG_A, "SELECT status FROM public.block_allocations WHERE block_id = $1 AND system_to IS "
                                   "NULL", bid)
    check(c == 201 and bid is not None and b is not None and iid not in sweep["expired"]
          and S["ins"]["m_A3"] in sweep["expired"]
          and bid in bsweep["cancelled"] and b["status"] == "cancelled" and "execute-by" in (b["cancel_reason"] or "")
          and len(ev) == 1 and ev[0]["actor_id"] is None and all(r["status"] == "cancelled" for r in al),
          "rep2's sweep skips the blocked instruction (and still expires another); the draft block is cancelled at "
          "its execute-by, logged with no actor, its allocations cancelled",
          f"{c} {str(o)[:200]} {sweep} {bsweep} {dict(b) if b else None}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    cb, ob = await api(U_ADMIN_B, "POST", "/api/v1/execution/blocks/settings/execution-accounts",
                       {"broker_name": "ZZ B Broker", "reference_last4": "1111", "dvp_capable": False})
    cbp, _ = await api(U_ADMIN_B, "POST", "/api/v1/execution/blocks/settings/policies",
                       {"minimum_allocation": "1", "policy_text": "Org B policy.", "reason": "REP9 verify"})
    res = {}
    for t in ("public.execution_accounts", "public.allocation_policies"):
        res[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")),
                  int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")),
                  int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_B, fetch="fetchval")),
                  int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_B, fetch="fetchval")))
    one_way = {}
    for t in ("public.trade_blocks", "public.block_allocations", "public.block_fills", "public.block_events",
              "public.allocation_corrections", "public.block_charges"):
        one_way[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A,
                                       fetch="fetchval")),
                      int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A,
                                       fetch="fetchval")))
    check(cb == 201 and cbp == 201 and all(a > 0 and b == 0 and c_ > 0 and d == 0 for a, b, c_, d in res.values())
          and all(a > 0 and b == 0 for a, b in one_way.values()),
          "RLS both directions: each org sees its own executing accounts / policies (non-empty) and none of the "
          "other's; org B sees none of org A's blocks, allocations, fills, events, corrections or charges",
          f"{res} {one_way}")
    bea = (ob.get("execution_account") or {}).get("id")
    codes = [(await api(U_ADMIN_B, "GET", f"/api/v1/execution/blocks/{S['buy_block']}"))[0],
             (await api(U_ADMIN_B, "POST", f"/api/v1/execution/blocks/{S['buy_block']}/sheet", {}))[0],
             (await api(U_ADMIN1, "PATCH", f"/api/v1/execution/blocks/settings/execution-accounts/{bea}",
                        {"status": "retired"}))[0]]
    _, lb = await api(U_ADMIN_B, "GET", "/api/v1/execution/blocks")
    check(codes == [404, 404, 404] and not any(r["id"] in (S["buy_block"], S["sell_block"]) for r in lb.get("rows") or []),
          "another org's block, its sheet and an executing account are a 404 both directions; B's list has none of "
          "A's", f"{codes}")
    body = {"instruction_ids": [S["ins"]["m_A3"]], "execution_account_id": S["ea"]}
    cv, ov = await api(U_VIEWER, "POST", "/api/v1/execution/blocks", body)
    cva, ova = await api(U_VIEWER, "POST", f"/api/v1/execution/blocks/{S['sell_block']}/approve", {})
    cvc, ovc = await api(U_VIEWER, "POST", f"/api/v1/execution/blocks/{S['buy_block']}/corrections",
                         {"changes": [{"allocation_id": str(S["alloc_ids"][A1]), "quantity": "1"}], "reason": "x"})
    check(cv == 403 and "manage_blocks" in str(ov) and cva == 403 and "approve_blocks" in str(ova) and cvc == 403,
          "gates both ways on identical requests: the viewer is refused build (manage_blocks), approve "
          "(approve_blocks) and corrections; an admin's identical build was accepted above", f"{cv} {cva} {cvc}")
    _, ev = await block_detail(S["sell_block"], U_VIEWER)
    _, ea = await block_detail(S["sell_block"], U_ADMIN1)
    pv = ev.get("permissions") or {}
    check(pv.get("can_write") is False and pv.get("can_approve") is False and ev.get("actions") == ["sheet"]
          and (ev.get("vocabularies") or {}).get("editable") == [] and "charge" in (ea.get("actions") or [])
          and bool((ev.get("vocabularies") or {}).get("compliance_notes")),
          "envelopes: view-only → only the sheet, empty editable; an admin may write; labels and the compliance "
          "notes come from the vocabularies", f"{ev.get('actions')} {ea.get('actions')}")
    missing = await platform(conn, """SELECT count(*) FROM roles r CROSS JOIN permissions p WHERE r.name = 'org_admin'
                                      AND p.name = ANY($1::text[]) AND NOT (r.org_id = ANY($2::uuid[]))
                                      AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                                                      AND rp.permission_id = p.id)""",
                             list(BLOCK_PERMS), [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
    check(int(missing) == 0, "every real org_admin role holds view / manage / approve_blocks", f"{missing}")
    perms = await platform(conn, "SELECT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id JOIN "
                                 "role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id "
                                 "WHERE ur.user_id = $1", U_ADMIN_B)
    check(set(BLOCK_PERMS) <= {r["name"] for r in perms}, "a new org's first org_admin holds the block permissions "
          "(rbac.ensure_org_admin_role)")


async def y_routes(conn):
    section("[Y] This sprint's routes (from its own diff) exist, each gated and in AUTHZ1_ROUTES.json; no existing "
            "gate changed; --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^\s*ok \d+ - (?:blocks list|block detail|approve / reject: never for a maker|allocation table|allocation policy|"
                      r"executing accounts|a single broker instruction|menu: Blocks|buildBody)", out, re.M)
    check(len(mine) >= 9 and "not ok" not in out, "the rep9 render tests ran and passed (no approve / allocate / "
          "correct control without the flags)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep7b.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    self_rep7b_diff(check, find)
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep7b.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_rep7b.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-200:] if not total else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


DROPPED_CONNECTION = (asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError, ConnectionError)


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown_retrying()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    seconds = _time.monotonic() - t0
    left = await leftovers(conn)
    print(f"CLEANUP_SECONDS: {seconds:.1f}")
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await close_conn(conn)
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    try:
        await run("pre-teardown", pre_teardown, conn)
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("policy + executing accounts", y_policy_accounts, conn)
            await run("eligibility", y_eligibility, conn)
            if S.get("policy") and S.get("ea") and S.get("ins"):
                await run("build + approve", y_build_approve, conn)
            if S.get("buy_block"):
                await run("order execution sheet", y_sheet, conn)
                await run("fills + allocation", y_fills_allocate, conn)
            if S.get("alloc_ids"):
                await run("corrections", y_corrections, conn)
                await run("settlement", y_settlement, conn)
            await run("full fill + lots", y_full_fill_lots, conn)
            await run("single-instruction sheet", y_single_sheet, conn)
            await run("FIX interface", y_fix, conn)
            if S.get("ins", {}).get("m_A1"):
                await run("execute-by", y_expiry, conn)
            if S.get("buy_block") and S.get("sell_block") and S.get("alloc_ids"):
                await run("isolation + gates", y_isolation, conn)
            else:
                check(False, "isolation + gates needs both blocks", "skipped — an earlier section failed")
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()

        async def after_teardown(c):
            return (await leftovers(c), await counts(c) if pre is not None else None,
                    await real_fingerprints(c) if real is not None else None)

        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            try:
                left, post, after = await after_teardown(conn)
            except DROPPED_CONNECTION as exc:
                print(f"[INFO] connection dropped during the leftover check ({type(exc).__name__}); reconnecting once")
                try:
                    conn.terminate()
                except Exception:  # noqa: BLE001
                    pass
                conn = await connect(dsn)
                left, post, after = await after_teardown(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if post is not None:
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if after is not None:
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
