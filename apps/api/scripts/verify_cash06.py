"""verify_cash06 — CASH06: CASH RULES, stage A. Standing rules (distribute across accounts, maintain a target balance,
scheduled recurring transfer, redirect incoming funds) built as WORKFLOW TRIGGERS on the existing engine. Each firing
DRAFTS + SUBMITS payment instructions (cash07) for a person's approval and never approves, sends or moves money.
Covered: the pure evaluator (hand-computed amounts, rounding, holds), the engine wiring (scheduled + event, idempotent
per period / transaction, caps, pause, the kill switch, S29b refusing approve / send), governance (the cash_rule FLOOR,
the owner ≠ every instruction's checker, versioned edits), validation refusals, the screens, visibility and isolation.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash06.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``. Secrets are hydrated
from Doppler over HTTPS (``_db_connect``) first; that is the only network the script itself uses. After that every
httpx transport refuses and records (asserted empty at the end), and so do the AI helpers.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``), the Auth0 profile call,
and the payment adapters' ``send`` (a spy that records and refuses: nothing may be sent).
REAL: the app through an ASGI transport, services.cash_rules / payments / sod / human_tasks / workflow_engine /
workflow_scheduler._fire (the real fire path) / workflow_autonomy / domain_events, the DB triggers, RLS and the live
database. The script's own connection does not bypass RLS (rolbypassrls = false, asserted).
TIME IS INJECTED: "today" through ``services.payments.set_clock`` + ``services.cash_rules.set_clock``, and the engine's
clock through ``services.workflow_autonomy.set_clock``. Each firing moves both clocks explicitly. Base day: Tue
2026-11-10 (2026-11-11 is Veterans Day).

FIXTURES (prefix 99000000-0000-0000-0000-0000cac6…): orgs A (VISIBILITY1 enforced) and B, is_test = true (asserted).
Users with real user_roles grants (the app's decision on each asserted):
  U_OWN — advisor (initiate + approve): THE RULE OWNER
  U_CK — advisor (initiate + approve): the checker
  U_CK2 — compliance (approve only)
  U_ED — support staff (initiate only): an editor
  U_ADM1 / U_ADM2 — workflow admins (manage_org_settings + author_workflows): the S29b switches
  U_SA — Super Admin
  U_MEM — a member of H1
  U_RS — fund finance, restricted to H2
  U_B — org B advisor
H1: E1 owns A1 ****6001 (the source), A2 ****6002, A3 ****6003; E1B owns A4 ****6004 (same household, ANOTHER owner →
third party). H2: E2 owns A7 / A8. Org B: AB1 / AB2. Custodian cash06verify declares ACH and journal (wire 'no', check
'unknown'). A cash asset with balances on A1 / A2 / A3 as of 11-09, 11-10 and 11-12, and three feed transactions on
11-10: a dividend INTO A1 (an incoming credit), a sell OUT of A1 (not a credit), a dividend into A2 (another account).
Org ceiling cash.rules.max_amount_per_firing = 20,000.00. Full account numbers sit in accounts.custodian_account_id
ONLY, to prove they never leave.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with EVERY guard GUC a touched table reads:
cash_rules + cash_rule_firings → cash_rules.teardown, payment_instructions + payment_events → payments.teardown,
custodian_capabilities → execution.teardown, sod_rules / sod_decisions → sod.teardown. It is keyed by this run's
fixture ids; tables without org_id (custodian_capabilities, user_roles, role_permissions, household_memberships) are
passed in the plan by their fixture keys. Every touched table is re-read for its exact pre-test count BY FIXTURE IDS
ONLY. Real rows (rules, firings, payments, events, triggers, autonomy settings, capabilities, sod rules, accounts,
transactions — the Olivo demo's included) are fingerprinted before and after.

--self-checks-only: git / route / static self-checks only (no database). --cleanup-only: the teardown and the leftover
check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
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
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cash06"
BEFORE_REF = "1f459a9"   # cash06-work's base: 'merge cash07'
_R = "any(view_portfolio|initiate_payment|approve_payment)"
_I = f"authz initiate_payment; {_R}"
_A = f"authz approve_payment; {_R}"
_HH = "/api/v1/cash/households/{household_id}/rules"
ROUTES = {("GET", _HH): _R, ("POST", _HH): _I, ("GET", _HH + "/options"): _I, ("POST", _HH + "/preview"): _I,
          ("GET", _HH + "/{version_id}"): _R, ("POST", _HH + "/{version_id}/approve"): _A,
          ("POST", _HH + "/{version_id}/reject"): _A, ("POST", _HH + "/{version_id}/{action}"): _I}
OWN_FILES = ("apps/api/services/cash_rules.py", "apps/api/routers/cash_rules.py")
NODE_TEST = "tests/cash06/cash06.test.mjs"
NOTES = REPO / "docs" / "cash" / "CASH06_NOTES.md"
RUNBOOK = REPO / "docs" / "cash" / "CASH_RULES.md"
MIGRATION = REPO / "docs" / "cash06_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cash06's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cash06 changed no existing route's gate and removed none",
          f"{o.where()}; changed={sorted(changed)[:4]} removed={o.removed[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at cash06's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cash06's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    cr = _code_only(_src("apps/api/services/cash_rules.py"))
    check(bool("PS.draft_and_submit(" in cr and "PS.approve" not in cr and ".decide(pool" not in cr.split("def decide")[0]
          and "mark_sent" not in cr and ".send(" not in cr),
          "cash_rules calls ONE payments verb (draft_and_submit) — never approve, decide, mark_sent or an adapter send")
    check(bool("archive_live_row(" in cr and "jsonb_populate_record" not in cr),
          "cash_rules archives through the SHARED services.bitemporal helper (no copy of the archive SQL)")
    check(bool("PS.same_owner_refusal(" in cr and "PS.rail_refusal(" in cr and "PS.authority_refusals(" in cr
          and "trading_authority_grants" not in cr),
          "validation reuses cash07's own checks (same owner, the declared rail, both authorities) — never a copy")
    check(bool(cr.count('site="cash_rules.') >= 3 and "sod.gate(" in cr), "the cash_rule floor is decided by services.sod at "
          "three sites")
    check(bool("WA.hold_alerts(" in cr and "workflow_only=True" in cr and "FireRefused" in cr),
          "holds alert through the engine's hold_alerts; the verb is workflow-only and refuses outside its own run")
    check(bool("custodian_account_id" not in cr and "account_number_hash" not in cr),
          "services/cash_rules.py never reads custodian_account_id or account_number_hash (last four only)")
    ps = _code_only(_src("apps/api/services/payments.py"))
    m = re.search(r"async def draft_and_submit\([\s\S]*?\n(?=async def |def )", ps)
    body = m.group(0) if m else ""
    # the INSERT's status literal sits in a triple-quoted SQL string (stripped by _code_only): read the raw source
    mr = re.search(r"async def draft_and_submit\([\s\S]*?\n(?=async def |def )", _src("apps/api/services/payments.py"))
    raw = mr.group(0) if mr else ""
    check(bool(bool(body) and "HT.open_task(" in body and "'draft'" in raw and '"pending_approval"' in body
          and "approve" not in body.replace("APPROVE", "").replace("approved", "") and "_withdraw_task(" in body),
          "payments.draft_and_submit drafts + submits only (task opened, draft → pending in one transaction, the task "
          "withdrawn on failure) — no approve path", f"{len(body)} chars")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm|socket)\b|"
                                          r"httpx\.|requests\.", src, re.M)),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    wa = _src("apps/api/services/workflow_autonomy.py")
    check(bool('"cash.rule_fire": {"class": ELIGIBLE_WITH_CAP, "draft_only": True' in wa and '"cash.incoming_credit"' in wa
          and all(f'"cash.payment_{v}": {{"class": NEVER, "money": True' in wa for v in ("submit", "approve", "send"))),
          "workflow_autonomy: cash.rule_fire eligible_with_cap (draft only); the payment verbs stay NEVER; the event")
    sd = _src("apps/api/services/sod.py")
    check(bool('CASH_RULE = "cash_rule"' in sd and '"cash_rule_approval": CASH_RULE' in sd and '"cash.rule_fire": "propose"' in sd),
          "services.sod: the item type, its task kind and the firing verb's 'propose' duty")
    ar = _code_only(_src("apps/api/services/action_registry.py"))
    asr = _code_only(_src("apps/api/routers/assistant.py"))
    check(bool("_workflow_only" in ar and "is_workflow_only(action_key)" in asr),
          "the registry keeps workflow-only verbs out of the assistant; /assistant/confirm refuses them")
    mig = MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists() else ""
    for marker in ("cash_rules_teardown_allowed", "cash_rules_maker_checker_chk", "cash_rules_owner_maker_chk",
                   "cash_rules_same_owner", "cash_rules_guard", "cash_rule_firings_period_uq", "'transfer'",
                   "third-party payments come later", "_org_delete", "'cash_rule'", "stage = 'A'"):
        check(marker in mig, f"docs/cash06_part1.sql carries {marker!r}")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for sec_ in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9."):
        check(sec_ in notes, f"docs/cash/CASH06_NOTES.md has section {sec_}")
    rb = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.exists() else ""
    check(bool("kill switch" in rb.lower() and "Who approves" in rb and "Rule types" in rb and "What a firing does" in rb),
          "docs/cash/CASH_RULES.md: rule types, caps, what a firing does, who approves, how to pause everything")
    check("verify_cash06.py" in _src("scripts/regression_set.txt"), "verify_cash06.py is in scripts/regression_set.txt")
    check(bool("cash06" in _src("docs/PROJECT_STATUS.md").lower() and "cash_rule" in _src("docs/SEGREGATION_OF_DUTIES.md")
          and "cash.rule_fire" in _src("docs/WORKFLOW_AUTONOMY.md") and "cash.rules" in _src("docs/OUTSTANDING_TODO_LIST.md")),
          "PROJECT_STATUS (cash06), SEGREGATION_OF_DUTIES (cash_rule), WORKFLOW_AUTONOMY (the verb class), "
          "OUTSTANDING_TODO_LIST (publish cash.rules) are updated")
    code, out, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "docs/*.sql", args=("--name-only",))
    migs = [f for f in out.split() if f != "docs/schema_snapshot.sql"]
    check(bool(code == 0 and migs == ["docs/cash06_part1.sql"] and "docs/schema_snapshot.sql" in out.split()),
          "cash06's own diff adds exactly one migration (docs/cash06_part1.sql) and refreshes the schema snapshot",
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
from services import cash_rules as CR  # noqa: E402
from services import domain_events as DE  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import payment_adapters as PA  # noqa: E402
from services import payments as PS  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services import workflow_engine as ENG  # noqa: E402
from services import workflow_scheduler as SCHED  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.workflow_schedule import evaluate_trigger  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cash06verify"
NAME = "CASH06 VERIFY "
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000cac6"
TODAY = date(2026, 11, 10)          # a Tuesday
HOLIDAY = date(2026, 11, 11)        # Veterans Day (not a US business day)
BAL_DAYS = (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 12))
TEARDOWN_GUCS = ("cash_rules.teardown", "payments.teardown", "execution.teardown", "sod.teardown")
CUSTODIAN = "cash06verify"
CEILING = "20000.00"
D = Decimal
CLOCK = {"day": TODAY, "now": datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)}


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


def occ(d: date) -> datetime:
    """The scheduled occurrence of a 10:00-Eastern rule on day ``d`` (EST in November: 15:00 UTC)."""
    return datetime(d.year, d.month, d.day, 15, 0, tzinfo=timezone.utc)


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_OWN, U_CK, U_CK2, U_ED, U_ADM1, U_ADM2, U_SA, U_MEM, U_RS, U_B = (_id(10 + i) for i in range(10))
H1, H2, HB = _id(30), _id(31), _id(32)
E1, E1B, E2, EB = _id(40), _id(41), _id(42), _id(43)
A = {"A1": _id(70), "A2": _id(71), "A3": _id(72), "A4": _id(73), "A7": _id(74), "A8": _id(75), "AB1": _id(76),
     "AB2": _id(77)}
FULL_NUMBERS = {k: f"99006600{n:04d}" for n, k in enumerate(A, start=1)}
PERMS = {
    "advisor": {"view_portfolio", "view_members", "initiate_payment", "approve_payment"},
    "compliance": {"view_portfolio", "approve_payment"},
    "support_staff": {"view_portfolio", "view_members", "initiate_payment"},
    "fund_finance": {"view_portfolio", "view_members", "initiate_payment", "approve_payment"},
    "workflow_admin": {"view_portfolio", "manage_org_settings", "author_workflows", "view_workflow_runs",
                       "configure_workflow_triggers"},
    "member": set(C.MEMBER_PERMS),
    "super_admin": set(),
}
# uid → (org, users.role mirror, short, role granted)
USERS = {
    U_OWN: (ORG_A, "member", "own", "advisor"), U_CK: (ORG_A, "member", "ck", "advisor"),
    U_CK2: (ORG_A, "member", "ck2", "compliance"), U_ED: (ORG_A, "member", "ed", "support_staff"),
    U_ADM1: (ORG_A, "member", "adm1", "workflow_admin"), U_ADM2: (ORG_A, "member", "adm2", "workflow_admin"),
    U_SA: (ORG_A, "super_admin", "sa", "super_admin"), U_MEM: (ORG_A, "member", "mem", "member"),
    U_RS: (ORG_A, "member", "rs", "fund_finance"), U_B: (ORG_B, "member", "b", "advisor"),
}
ALL_USERS = list(USERS)
ENTS = {E1: (ORG_A, "Rules Trust"), E1B: (ORG_A, "Spouse"), E2: (ORG_A, "Other Family"), EB: (ORG_B, "Org B")}
HOUSEHOLDS = {H1: (ORG_A, "Rules", (E1, E1B)), H2: (ORG_A, "Other", (E2,)), HB: (ORG_B, "B", (EB,))}
# key → (org, household, entity, masked)
ACCOUNTS = {"A1": (ORG_A, H1, E1, "****6001"), "A2": (ORG_A, H1, E1, "****6002"), "A3": (ORG_A, H1, E1, "****6003"),
            "A4": (ORG_A, H1, E1B, "****6004"), "A7": (ORG_A, H2, E2, "****6007"), "A8": (ORG_A, H2, E2, "****6008"),
            "AB1": (ORG_B, HB, EB, "****6011"), "AB2": (ORG_B, HB, EB, "****6012")}
GRANTS = [(U_OWN, E1, "limited"), (U_CK, E1, "limited"), (U_CK2, E1, "limited"), (U_ED, E1, "limited"),
          (U_SA, E1, "limited"), (U_RS, E2, "limited"), (U_B, EB, "limited")]
CAP_ROWS = [(CUSTODIAN, "ach", "yes"), (CUSTODIAN, "journal", "yes"), (CUSTODIAN, "wire", "no"),
            (CUSTODIAN, "check", "unknown")]
# cash balances: account → amount (the same on each BAL_DAY)
BALANCES = {"A1": "50000.00", "A2": "7500.25", "A3": "40000.00"}
# the feed (dated TODAY): key → (account, code, amount)
TXS = {"T_CREDIT": ("A1", "dividend", "4321.99"), "T_OUT": ("A1", "sell", "1500.00"),
       "T_OTHER": ("A2", "dividend", "250.00")}

SEED_CHECK_LITERALS = (
    ("public.cash_rules", "cash_rules_type_chk", ("distribute", "maintain_target", "recurring", "redirect_incoming")),
    ("public.cash_rules", "cash_rules_basis_chk", ("pct_balance", "fixed", "pct_incoming")),
    ("public.cash_rules", "cash_rules_frequency_chk", ("daily", "business_days", "weekly", "monthly", "on_incoming")),
    ("public.cash_rules", "cash_rules_status_chk", ("draft", "pending_approval", "active", "paused", "superseded",
                                                    "retired")),
    ("public.cash_rules", "cash_rules_rail_chk", ("ach", "wire", "journal", "check")),
    ("public.cash_rules", "cash_rules_stage_chk", ("A",)),
    ("public.cash_rules", "cash_rules_pending_action_chk", ("activate", "resume")),
    ("public.cash_rule_firings", "cash_rule_firings_outcome_chk", ("firing", "drafted", "held", "partly_held",
                                                                   "skipped")),
    ("public.cash_rule_firings", "cash_rule_firings_trigger_chk", ("scheduled", "event", "manual")),
    ("public.payment_instructions", "payment_instructions_purpose_ref_chk", ("cash_rule",)),
    ("public.payment_instructions", "payment_instructions_status_chk", ("draft", "pending_approval", "approved")),
    ("public.payment_events", "payment_events_action_chk", ("create", "submit", "approve")),
    ("public.custodian_capabilities", "custodian_capabilities_key_chk", ("ach", "wire", "journal", "check")),
    ("public.custodian_capabilities", "custodian_capabilities_value_chk", ("yes", "no", "unknown")),
    ("public.custodian_capabilities", "custodian_capabilities_evidence_kind_chk", ("document",)),
    ("public.trading_authority_grants", "trading_authority_grants_authority_tier_check", ("limited",)),
    ("portfolio.assets", "assets_basis_chk", ("units",)),
)
SEED_ROWS = {
    "public.custodian_capabilities": [
        {"custodian_code": c, "capability_key": k, "value": v, "evidence_kind": "document",
         "evidence": "verify_cash06 fixture", "as_of": TODAY.isoformat(), "version_no": 1, "owner_scope": "platform",
         "org_id": None} for c, k, v in CAP_ROWS],
    "public.trading_authority_grants": [{"org_id": str(ORG_A), "entity_id": str(e), "user_id": str(u),
                                         "authority_tier": t} for u, e, t in GRANTS],
    # the shapes the services write (evaluated against every CHECK of the table before anything is seeded)
    "public.cash_rules": [
        {"org_id": str(ORG_A), "rule_id": str(_id(900)), "version_no": 1, "household_id": str(H1), "name": "verify",
         "rule_type": "recurring", "source_account_id": str(A["A1"]),
         "destinations": [{"account_id": str(A["A2"]), "amount": "2500.00"}], "basis": "fixed",
         "frequency": "business_days", "rail": "ach", "cap_per_firing": "10000.00", "max_per_day": 1, "stage": "A",
         "status": "draft", "owner_id": str(U_OWN), "maker_ids": [str(U_OWN)]},
        {"org_id": str(ORG_A), "rule_id": str(_id(901)), "version_no": 1, "household_id": str(H1), "name": "verify",
         "rule_type": "maintain_target", "source_account_id": str(A["A2"]), "destinations": [], "condition": "below",
         "target_amount": "10000.00", "supporting_account_id": str(A["A3"]), "frequency": "weekly", "day_of_week": 2,
         "rail": "journal", "cap_per_firing": "5000.00", "max_per_day": 1, "stage": "A", "status": "draft",
         "owner_id": str(U_OWN), "maker_ids": [str(U_OWN)]},
        {"org_id": str(ORG_A), "rule_id": str(_id(902)), "version_no": 1, "household_id": str(H1), "name": "verify",
         "rule_type": "redirect_incoming", "source_account_id": str(A["A1"]),
         "destinations": [{"account_id": str(A["A3"]), "pct": "25"}], "basis": "pct_incoming",
         "frequency": "on_incoming", "rail": "ach", "cap_per_firing": "5000.00", "max_per_day": 5, "stage": "A",
         "status": "draft", "owner_id": str(U_OWN), "maker_ids": [str(U_OWN)]}],
    "public.cash_rule_firings": [
        {"org_id": str(ORG_A), "rule_id": str(_id(900)), "rule_version_id": str(_id(903)), "period_key": "d:2026-11-10",
         "trigger_type": "scheduled", "owner_id": str(U_OWN), "evaluated_on": TODAY.isoformat(), "outcome": "firing"},
        {"org_id": str(ORG_A), "rule_id": str(_id(902)), "rule_version_id": str(_id(903)),
         "period_key": f"txn:{_id(904)}", "trigger_type": "event", "owner_id": str(U_OWN),
         "evaluated_on": TODAY.isoformat(), "outcome": "held", "reason": "verify", "completed_at": "2026-11-10T15:00:00Z"}],
}
SEED_NON_COLUMN = {"app.is_super_admin", "app.current_org_id", "true", "1 day", "active", "staff_visibility.enforced",
                   "general", "manual", "units", "cash", "taxonomy_mc_8_1", "USD", "trust", "taxable", "view_only",
                   "Lead adviser", "individual", "cash.rules.max_amount_per_firing"}
UNIQUES = ("assets_cash_active_uniq", "trading_authority_grants_entity_id_user_id_key", "custodian_capabilities_live_uq",
           "cash_rules_version_live_uq", "cash_rules_running_uq", "cash_rules_open_uq", "cash_rules_task_live_uq",
           "cash_rule_firings_period_uq", "payment_instructions_task_live_uq", "accounts_active_identity_uq",
           "org_settings_org_id_setting_key_key", "domain_events_source_dedupe_uq")
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
    "cash_rules": ("org_id", "rule_id", "version_no", "household_id", "name", "rule_type", "source_account_id",
                   "destinations", "basis", "condition", "target_amount", "supporting_account_id", "frequency",
                   "day_of_week", "day_of_month", "rail", "cap_per_firing", "max_per_day", "stage", "status",
                   "owner_id", "maker_ids", "workflow_definition_id", "workflow_version_id", "trigger_id", "reason",
                   "created_by"),
    "cash_rule_firings": ("org_id", "rule_id", "rule_version_id", "period_key", "trigger_type", "workflow_run_id",
                          "owner_id", "evaluated_on"),
    "payment_instructions": ("id", "org_id", "household_id", "source_account_id", "destination_account_id", "amount",
                             "rail", "value_date", "purpose", "purpose_ref_type", "purpose_ref_id", "status",
                             "maker_id", "maker_ids"),
    "workflow_definitions": ("org_id", "name", "description", "is_active", "created_by"),
    "workflow_versions": ("workflow_definition_id", "org_id", "version_number", "bpmn_xml", "change_summary",
                          "is_current", "created_by"),
    "workflow_steps": ("workflow_version_id", "org_id", "step_key", "step_type", "autonomy_tier",
                       "action_registry_key", "display_name"),
    "workflow_triggers": ("workflow_definition_id", "org_id", "trigger_type", "schedule_cron", "event_type",
                          "timezone", "is_active", "created_by"),
}
COUNTED = (
    "public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
    "public.entities", "public.households", "public.household_memberships", "public.delegate_grants",
    "public.staff_assignments", "public.org_settings", "public.accounts", "portfolio.assets", "portfolio.positions",
    "portfolio.transactions", "public.trading_authority_grants", "public.custodian_capabilities",
    "public.cash_rules", "public.cash_rule_firings", "public.sod_decisions", "public.payment_instructions",
    "public.payment_events", "public.audit_log", "public.member_todos", "public.workflow_definitions",
    "public.workflow_versions", "public.workflow_steps", "public.workflow_triggers", "public.workflow_runs",
    "public.workflow_run_steps", "public.workflow_autonomy_settings", "public.domain_events",
    "public.domain_event_deliveries",
)

S: dict = {"rule": {}, "pay": {}, "tx": {}, "runs": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
SEND_CALLS: list[str] = []
BODIES: list[tuple[str, str]] = []          # (who / path, full body) of every rules response — scanned for leaks
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
    return [n for n in FULL_NUMBERS.values() if n in text] + ([f"{TAG}-hash"] if f"{TAG}-hash" in text else [])


def set_day(d: date, now: datetime | None = None) -> None:
    """Move BOTH clocks: "today" (payments + cash rules) and the engine's (S29b limits, day starts)."""
    CLOCK["day"] = d
    CLOCK["now"] = now or occ(d)


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
                async with conn.transaction():
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
            ("cash_rules", f"SELECT id, status, system_to FROM public.cash_rules WHERE {nf}"),
            ("cash_rule_firings", f"SELECT id, outcome FROM public.cash_rule_firings WHERE {nf}"),
            ("payment_instructions", f"SELECT id, status, system_to FROM public.payment_instructions WHERE {nf}"),
            ("payment_events", f"SELECT id FROM public.payment_events WHERE {nf}"),
            ("workflow_triggers", f"SELECT id, is_active, schedule_cron, last_fired_at FROM public.workflow_triggers "
                                  f"WHERE {nf}"),
            ("workflow_autonomy_settings", "SELECT id, status, valid_to FROM public.workflow_autonomy_settings WHERE "
                                           "org_id IS NULL OR NOT (org_id = ANY($1::uuid[]))"),
            ("trading_authority_grants", f"SELECT id, authority_tier FROM public.trading_authority_grants WHERE {nf}"),
            ("sod_rules (org)", f"SELECT id, status, valid_to FROM public.sod_rules WHERE org_id IS NOT NULL AND {nf}"),
            ("accounts", f"SELECT id, valid_to, system_to FROM public.accounts WHERE {nf}"),
            ("transactions", f"SELECT id, net_amount, valid_to, system_to FROM portfolio.transactions WHERE {nf}"),
            ("demo entities", f"SELECT id, display_name, valid_to, system_to FROM public.entities WHERE {nf} AND "
                              "EXISTS (SELECT 1 FROM unnest(COALESCE(tags, '{}'::text[])) g WHERE g LIKE 'demo-%')"),
            ("org_settings", f"SELECT org_id, setting_key, setting_value FROM public.org_settings WHERE {nf}")):
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
            # platform rows without org_id, by their fixture custodian code (asserted to match no real row)
            "public.custodian_capabilities": ("custodian_code = $1", [CUSTODIAN]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_cash06")
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


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_cash06"}}
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
            return 500, {"raw": f"{type(exc).__name__}: {exc}"[:2000]}
    raw = res.content.decode("utf-8", "replace")
    if "/rules" in path or "/payments" in path:
        BODIES.append((f"{USERS[uid][2]} {method} {path}", raw))
    try:
        return res.status_code, json.loads(raw or "{}")
    except ValueError:
        return res.status_code, {"raw": raw[:4000]}


def rp(hh, tail: str = "") -> str:
    return f"/api/v1/cash/households/{hh}/rules" + tail


def detail_text(b) -> str:
    d = (b or {}).get("detail")
    return dumps(d) if not isinstance(d, str) else d


def reasons_of(b) -> list[str]:
    d = (b or {}).get("detail")
    return list(d.get("reasons") or []) if isinstance(d, dict) else []


def ver_of(b) -> dict:
    return ((b or {}).get("rule") or {}).get("version") or {}


def rule_body(kind: str, **over) -> dict:
    """A VALID rule body per type (only the field a refusal check names is changed, so only the gate can refuse)."""
    base = {"rail": "ach", "max_per_day": 1, "name": f"{NAME}{kind}"}
    if kind == "recurring":
        b = {**base, "rule_type": "recurring", "source_account_id": str(A["A1"]), "frequency": "business_days",
             "destinations": [{"account_id": str(A["A2"]), "amount": "2500.00"}], "cap_per_firing": "10000.00"}
    elif kind == "redirect":
        b = {**base, "rule_type": "redirect_incoming", "source_account_id": str(A["A1"]), "max_per_day": 5,
             "destinations": [{"account_id": str(A["A3"]), "pct": "25"}], "cap_per_firing": "5000.00"}
    elif kind == "cap":
        b = {**base, "rule_type": "distribute", "basis": "pct_balance", "source_account_id": str(A["A1"]),
             "frequency": "business_days", "max_per_day": 5, "cap_per_firing": "1000.00",
             "destinations": [{"account_id": str(A["A2"]), "pct": "50"}, {"account_id": str(A["A3"]), "pct": "50"}]}
    elif kind == "target":
        b = {**base, "rule_type": "maintain_target", "source_account_id": str(A["A2"]), "frequency": "weekly",
             "day_of_week": 2, "condition": "below", "target_amount": "10000.00", "supporting_account_id": str(A["A3"]),
             "rail": "journal", "cap_per_firing": "5000.00"}
    else:  # gov — distribute fixed
        b = {**base, "rule_type": "distribute", "basis": "fixed", "source_account_id": str(A["A1"]),
             "frequency": "monthly", "day_of_month": 1, "cap_per_firing": "5000.00",
             "destinations": [{"account_id": str(A["A2"]), "amount": "1200.00"}]}
    b.update(over)
    return b


async def live_rule(conn, vid) -> dict:
    r = await platform(conn, "SELECT * FROM public.cash_rules WHERE id = $1::uuid AND system_to IS NULL", vid,
                       fetch="fetchrow")
    return dict(r) if r else {}


async def trigger_row(conn, tid) -> dict:
    r = await platform(conn, "SELECT * FROM public.workflow_triggers WHERE id = $1::uuid", tid, fetch="fetchrow")
    return dict(r) if r else {}


async def firings(conn, rule_id) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT * FROM public.cash_rule_firings WHERE rule_id = $1::uuid ORDER BY created_at, id", rule_id)]


async def rule_payments(conn, rule_id) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT * FROM public.payment_instructions WHERE purpose_ref_type = 'cash_rule' AND purpose_ref_id = "
              "$1::uuid AND system_to IS NULL ORDER BY created_at, id", rule_id)]


async def pay_events(conn, pid) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT action, from_status, to_status, actor_id FROM public.payment_events WHERE payment_id = $1::uuid "
              "ORDER BY created_at, id", pid)]


async def refusals(conn, item_type: str, item_id) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT checker_id, outcome, site FROM public.sod_decisions WHERE item_type = $1 AND item_id = $2 AND "
              "outcome = 'refused'", item_type, str(item_id))]


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_all()   # ASGITransport skips the app lifespan, where the registry is normally filled
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_cash06: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cash06: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))

    async def _hermetic_sync_catalog(pool, org_id):
        return None

    REGISTRY.sync_catalog = _hermetic_sync_catalog
    # the adapters' send — a spy at its CURRENT signature that records and refuses (a rule never sends)
    for cls in (PA.ManualPaymentAdapter, PA.NotConnectedPaymentAdapter):
        params = list(inspect.signature(cls.send).parameters)
        if params[:2] != ["self", "instruction"]:
            raise RuntimeError(f"{cls.__name__}.send signature drifted: {params}")

        async def spy(self, instruction, _c=cls.__name__, **kw):
            SEND_CALLS.append(f"{_c}:{(instruction or {}).get('id')}")
            raise AssertionError("verify_cash06: an adapter send was attempted")

        cls.send = spy
    # the handler signature the engine calls (services.workflow_engine._execute_service_task)
    eng_src = inspect.getsource(ENG._execute_service_task)
    for kw in ("pool=pool", "user_id=actor_id", "run_context=", "workflow_run_id="):
        if kw not in eng_src:
            raise RuntimeError(f"_execute_service_task no longer passes {kw!r}")
    for fn, want in ((PS.set_clock, ["fn"]), (CR.set_clock, ["fn"]), (WA.set_clock, ["fn"])):
        if list(inspect.signature(fn).parameters) != want:
            raise RuntimeError(f"{fn.__module__}.set_clock signature drifted")
    fire_params = list(inspect.signature(SCHED._fire).parameters)
    if fire_params != ["pool", "trigger", "occurrence_utc"]:
        raise RuntimeError(f"workflow_scheduler._fire signature drifted: {fire_params}")
    PS.set_clock(lambda: CLOCK["day"])
    CR.set_clock(lambda: CLOCK["day"])
    WA.set_clock(lambda: CLOCK["now"])


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — notes, the storage decision justified, the reuse inventory")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for marker in ("workflow_triggers", "no configuration column", "public.cash_rules", "cash_rule_firings",
                   "draft_and_submit", "eligible_with_cap", "cash.incoming_credit", "TYPE_MAP", "Reuse inventory",
                   "hold_alerts", "workflow_only", "_SERVICE_CONTEXT", "account_balances", "shadow", "floor class",
                   "system:cash_rule:", "wftemplates1"):
        check(marker.lower() in notes.lower(), f"the notes name {marker!r}")
    for f in ("[FIND]** `workflow_triggers` has NO configuration column", "[FIND]** No \"incoming credit\" domain event",
              "[FIND]** Every registered action is offered to the assistant", "[FIND]** The Altruist sync"):
        check(f.replace("**", "") in notes.replace("**", ""), f"the notes record the finding {f[:60]!r}")
    if not (REPO / "docs" / "cash" / "CASH_TRACK_PLAN_v1.2.md").exists():
        find("CASH_TRACK_PLAN_v1.2.md is still not in the repo — nothing was invented from it")
    cols = {r["column_name"] for r in await platform(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = "
              "'workflow_triggers'")}
    check(bool(bool(cols) and not ({"config", "configuration", "payload", "params", "definition", "data"} & cols)),
          f"REPRODUCED: workflow_triggers' {len(cols)} columns carry no rule definition (why cash_rules exists)",
          f"{sorted(cols)}")
    before = REPO / "docs" / "schema_snapshot.sql"
    snap = before.read_text(encoding="utf-8") if before.exists() else ""
    check(bool("===== cash_rules =====" in snap and "===== cash_rule_firings =====" in snap),
          "the schema snapshot was refreshed with the two new tables")


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
          f"{got} CHECK constraints read; every literal the seeds and the services use is one the database allows",
          f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn)
    check(bool(n_chk > 0 and not refused), f"{n_chk} CHECK constraints on the {len(SEED_ROWS)} checked tables evaluated "
          "by the database against the exact row shapes — none refuses", f"{refused}")
    keep = SEED_ROWS["public.cash_rules"]
    SEED_ROWS["public.cash_rules"] = [dict(keep[0], stage="B"), dict(keep[0], approved_by=str(U_OWN)),
                                      dict(keep[0], maker_ids=[str(U_CK)])]
    try:
        _n, bad2 = await seed_rows_vs_checks(conn)
    finally:
        SEED_ROWS["public.cash_rules"] = keep
    want = {("public.cash_rules", "cash_rules_stage_chk", 0), ("public.cash_rules", "cash_rules_maker_checker_chk", 1),
            ("public.cash_rules", "cash_rules_owner_maker_chk", 2)}
    check(want <= set(bad2), "the pre-check is not vacuous: stage B, an approver who is a maker, and an owner who is "
          "not a maker are each refused by the same evaluation", f"{bad2}")
    lits = seed_literals()
    vals = {str(v) for rows in SEED_ROWS.values() for r in rows for v in r.values() if v is not None
            and not isinstance(v, (list, dict))}
    vals |= {x for _t, _n2, ls in SEED_CHECK_LITERALS for x in ls} | {CUSTODIAN, TAG}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(bool(lits) and not undeclared), f"every quoted literal in seed()'s source ({len(lits)}) is a declared row "
          "value or a checked CHECK literal", f"{undeclared}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    cons = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype = 'u' AND "
                                                       "conname = ANY($1::text[])", list(UNIQUES))}
    have = set(idx) | cons
    check(bool(have == set(UNIQUES) and len(have) > 0 and all("UNIQUE" in v for v in idx.values())),
          f"{len(have)} unique indexes / constraints read (one running + one open version per rule, one firing per "
          "rule and period, one live payment per task, one event per source, …) — the seed respects each",
          f"missing={sorted(set(UNIQUES) - have)}")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY(ARRAY['public.cash_rules', "
              "'public.cash_rule_firings']::regclass[])")]
    check(len(fks) >= 10, "the new tables' foreign keys are read (household, accounts, owner, makers, approver, "
          "workflow definition / version / trigger, the firing's version)", f"{len(fks)}")
    ops = {t: {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                      "tablename = $1", t)}
           for t in ("cash_rules", "cash_rule_firings")}
    check(all(v == {"SELECT", "INSERT", "UPDATE", "DELETE"} for v in ops.values()),
          "RLS: a policy for EVERY operation on cash_rules and cash_rule_firings", f"{ops}")
    trg = {r["tgname"] for r in await platform(
        conn, "SELECT tgname FROM pg_trigger WHERE tgrelid = ANY(ARRAY['public.cash_rules', "
              "'public.cash_rule_firings']::regclass[]) AND NOT tgisinternal")}
    check({"cash_rules_guard_trg", "cash_rules_same_owner_trg", "cash_rule_firings_guard_trg"} <= trg,
          "the status-graph / frozen-terms, same-owner and firing-claim triggers exist", f"{sorted(trg)}")
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
    check(bool(bool(SEED_COLUMNS) and not badc), f"every INSERT's columns ({len(SEED_COLUMNS)} tables — the seed's AND the "
          "services') exist in docs/schema_snapshot.sql", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and not refused and have == set(UNIQUES)
                          and by is False and not badc)


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    a = int(await platform(conn, "SELECT count(*) FROM accounts WHERE custodian_code = $1 OR custodian_account_id = "
                                 "ANY($2::text[])", CUSTODIAN, list(FULL_NUMBERS.values()), fetch="fetchval"))
    c = int(await platform(conn, "SELECT count(*) FROM custodian_capabilities WHERE custodian_code = $1", CUSTODIAN,
                           fetch="fetchval"))
    r = int(await platform(conn, "SELECT count(*) FROM cash_rules WHERE name LIKE $1 OR rule_id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    return check(bool(o == 0 and u == 0 and e == 0 and a == 0 and c == 0 and r == 0),
                 "the reserved prefixes (fixture ids, sign-in tag, 'CASH06 VERIFY' names, the custodian code, full "
                 "numbers) match no real row", f"orgs={o} users={u} entities={e} accounts={a} capabilities={c} rules={r}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CASH06 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CASH06 verify — {rname}")
                for pn in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CASH06 {short.upper()}", sub_of(uid), account_role)
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
        for uid in (U_OWN, U_CK, U_CK2, U_ED):
            for e in (E1, E1B, E2):
                await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                                   "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, e, uid)
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) "
                           "VALUES ($1, $2, $3, 'Lead adviser')", ORG_A, E2, U_RS)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'staff_visibility.enforced', 'true'::jsonb, 'general')", ORG_A)
        await conn.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category) VALUES "
                           "($1, 'cash.rules.max_amount_per_firing', $2::jsonb, 'general')", ORG_A, json.dumps(CEILING))
        for key, (org, hh, ent, masked) in ACCOUNTS.items():
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "custodian_account_id, registration_type, tax_status, primary_entity_id, household_id, "
                "is_discretionary, is_held_away) VALUES ($1, $2, $3, $4, $5, $6, 'trust', 'taxable', $7, $8, true, "
                "false)", A[key], org, masked, f"{TAG}-hash-{key}", CUSTODIAN, FULL_NUMBERS[key], ent, hh)
        for row in SEED_ROWS["public.custodian_capabilities"]:
            await conn.execute(
                "INSERT INTO custodian_capabilities (custodian_code, capability_key, value, evidence_kind, evidence, "
                "as_of, version_no) VALUES ($1, $2, $3, $4, $5, $6, $7)", row["custodian_code"], row["capability_key"],
                row["value"], row["evidence_kind"], row["evidence"], TODAY, row["version_no"])
        for u, e, t in GRANTS:
            await conn.execute("INSERT INTO trading_authority_grants (org_id, entity_id, user_id, authority_tier) "
                               "VALUES ($1, $2, $3, $4)", USERS[u][0], e, u, t)
        aid = uuid.uuid4()
        await conn.execute("INSERT INTO portfolio.assets (id, org_id, name, asset_type, default_taxonomy_key, "
                           "currency_code) VALUES ($1, $2, $3, 'cash', 'taxonomy_mc_8_1', 'USD')", aid, ORG_A,
                           NAME + "Cash")
        pos = {}
        for day in BAL_DAYS:
            for key, amt in BALANCES.items():
                pid = uuid.uuid4()
                if day == BAL_DAYS[1]:
                    pos[key] = pid      # the feed transactions hang off the 11-10 cash positions
                await conn.execute(
                    "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, "
                    "ownership_basis, quantity, market_value, authority, source_system, account_id) VALUES ($1, $2, $3, "
                    "$4, $5, 'units', $6, $6, 'manual', 'manual', $7)", pid, ORG_A, E1, aid, day, D(amt), A[key])
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
    ok_test = check(bool(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS)), "fixture orgs and users are is_test = true "
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
                bad.append((short, names, sorted(perms & {"initiate_payment", "approve_payment", "view_portfolio",
                                                          "manage_org_settings"}), bool(pr and pr.is_internal)))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (advisors initiate + "
                 "approve; compliance approves only; support staff initiates only; workflow admins hold the S29b "
                 "settings; the member and the restricted staff as named; one Super Admin; no staff bypass)", f"{bad}")


# ── helpers: a rule through the API, its S29b opt-in, a firing through the real fire path ──────────────────────
async def create_active(conn, kind: str, key: str, *, owner=U_OWN, checker=U_CK, **over) -> dict | None:
    """Create (owner) → submit (owner) → approve (a different person) through the API; returns the live row."""
    c1, b1 = await api(owner, "POST", rp(H1), rule_body(kind, **over))
    vid = ver_of(b1).get("id")
    if c1 != 201 or not vid:
        check(False, f"{key}: the rule is created through the API", f"{c1} {dumps(b1)[:400]}")
        return None
    c2, b2 = await api(owner, "POST", rp(H1, f"/{vid}/submit"), {})
    c3, b3 = await api(checker, "POST", rp(H1, f"/{vid}/approve"), {"note": "verify"})
    row = await live_rule(conn, vid)
    ok = check(bool(c2 == 200 and c3 == 200 and row.get("status") == "active" and row.get("approved_by") == checker),
               f"{key}: created by its owner, submitted, approved by a different person → active",
               f"{c2} {detail_text(b2)[:200]} / {c3} {detail_text(b3)[:200]} / {row.get('status')}")
    if not ok:
        return None
    S["rule"][key] = row
    return row


async def admin_setting(key, value, *, version=None, reason="verify_cash06") -> bool:
    """U_ADM1 asks (the service the S29b route calls); a loosening draft is decided by U_ADM2."""
    pool = await get_pool()
    with _ctx(ORG_A):
        async with pool.acquire() as c:
            st = await WA.change_setting(c, org_id=ORG_A, actor_id=str(U_ADM1), is_super_admin=False, key=key,
                                         value=value, reason=reason, workflow_version_id=version)
    if st.get("needs_checker"):
        with _ctx(ORG_A):
            async with pool.acquire() as c:
                done = await WA.decide_setting(c, org_id=ORG_A, actor_id=str(U_ADM2), setting_id=st["id"],
                                               outcome="approve")
        return done.get("status") == "active"
    return st.get("status") == "active"


async def start_run(row: dict, ctx_extra: dict, started_by=U_OWN) -> dict:
    pool = await get_pool()
    ctx = {"trigger_id": str(row["trigger_id"]), **ctx_extra}
    with _ctx(ORG_A):
        try:
            res = await ENG.start_workflow_run(pool, row["workflow_version_id"], ORG_A, ctx, started_by)
            return {"ok": True, **res}
        except Exception as exc:  # noqa: BLE001 — a held run re-raises with its id
            return {"ok": False, "run_id": getattr(exc, "held_run_id", None), "error": f"{type(exc).__name__}: {exc}"}


async def opt_in(conn, row: dict, key: str) -> bool:
    """S29b, unchanged: the version to shadow (at once) → ONE shadow run (the org needs 1) reviewed clean by the
    trigger OWNER → 'on' asked by U_ADM1, decided by U_ADM2 (never the owner)."""
    vid = row["workflow_version_id"]
    ok = await admin_setting(WA.KEY_VERSION, {"state": "shadow"}, version=vid)
    trig_type = "event" if row["frequency"] == "on_incoming" else "scheduled"
    extra = ({"trigger_type": "scheduled", "scheduled_occurrence": occ(date(2026, 11, 6)).isoformat()}
             if trig_type == "scheduled" else
             {"trigger_type": "event", "event_type": CR.EVENT_TYPE, "source_type": CR.FEED_SOURCE,
              "source_id": str(_id(950))})
    r = await start_run(row, extra)
    pool = await get_pool()
    reviewed = None
    if r.get("run_id"):
        with _ctx(ORG_A):
            async with pool.acquire() as c:
                reviewed = await WA.review_shadow_run(c, org_id=ORG_A, actor_id=str(U_OWN), run_id=r["run_id"],
                                                      outcome="clean")
    n_fire = len(await firings(conn, row["rule_id"]))
    on = await admin_setting(WA.KEY_VERSION, {"state": "on"}, version=vid)
    return check(bool(ok and r.get("status") == "completed" and reviewed is not None and on and n_fire == 0),
                 f"{key}: S29b opt-in — shadow (one run, the handler NOT called: no firing record), reviewed clean by "
                 "the owner, 'on' decided by a second person", f"{ok} {r} {reviewed} {on} firings={n_fire}")


async def fire_sched(row: dict, d: date) -> dict:
    """A scheduled fire of the rule's trigger through the REAL scheduler fire path, at day ``d``'s occurrence."""
    set_day(d)
    pool = await get_pool()
    trig = {"id": row["trigger_id"], "org_id": ORG_A, "workflow_version_id": row["workflow_version_id"],
            "created_by": row["owner_id"]}
    try:
        res = await SCHED._fire(pool, trigger=trig, occurrence_utc=occ(d))
        return {"ok": True, **res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "run_id": getattr(exc, "held_run_id", None), "error": f"{type(exc).__name__}: {exc}"}


def _j(v):
    """A jsonb column as read by asyncpg (text) → its value."""
    return json.loads(v) if isinstance(v, (str, bytes)) else (v if v is not None else [])


async def run_status(conn, run_id) -> str | None:
    return await platform(conn, "SELECT status FROM workflow_runs WHERE id = $1::uuid", run_id, fetch="fetchval")


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
def _rule(**kw) -> dict:
    base = {"status": "active", "rail": "ach", "cap_per_firing": D("100000.00"), "max_per_day": 5,
            "source_account_id": "s", "supporting_account_id": None, "condition": None, "target_amount": None,
            "day_of_week": None, "day_of_month": None, "destinations": [], "basis": None}
    return {**base, **kw}


async def y_evaluator(conn):
    section("[Y] Evaluator — hand-computed drafts per rule type; cent rounding; holds (shortfall / cap / stale); "
            "business days")
    B = CR.Balance
    s, x, y, z = "s", "x", "y", "z"
    fresh = {s: B(D("1234.57"), date(2026, 11, 9))}
    r = _rule(rule_type="distribute", basis="pct_balance", frequency="business_days", source_account_id=s,
              destinations=[{"account_id": x, "pct": "12.5"}, {"account_id": y, "pct": "33.3333"}])
    ev = CR.evaluate(r, fresh, [], TODAY)
    # 12.5 % × 1234.57 = 154.32125 → 154.32; 33.3333 % × 1234.57 = 411.52259… → 411.52 (DOWN to the cent)
    check(bool([d.amount for d in ev.drafts] == [D("154.32"), D("411.52")] and ev.outcome == "drafted"
          and all(d.value_date == TODAY and d.source_account_id == s for d in ev.drafts)),
          "% of balance: 12.5 % and 33.3333 % of 1,234.57 = 154.32 + 411.52 (rounded DOWN; Σ never past the balance)",
          f"{[(d.amount, d.value_date) for d in ev.drafts]} {ev.outcome}")
    check(CR.evaluate(r, fresh, [], TODAY).out() == ev.out(), "deterministic: the same inputs give the same output")
    rf = _rule(rule_type="recurring", basis="fixed", frequency="monthly", day_of_month=1, source_account_id=s,
               destinations=[{"account_id": x, "amount": "2500.00"}])
    ev = CR.evaluate(rf, {s: B(D("9000.00"), date(2026, 11, 9))}, [], TODAY)
    check(bool([d.amount for d in ev.drafts] == [D("2500.00")] and ev.drafts[0].destination_account_id == x),
          "fixed: 2,500.00 exactly", f"{ev.out()}")
    ev = CR.evaluate(rf, {s: B(D("9000.00"), date(2026, 11, 13))}, [], date(2026, 11, 14))
    check(bool(len(ev.drafts) == 1 and ev.drafts[0].value_date == date(2026, 11, 16) and any("Saturday" in n for n in ev.notes)),
          "a monthly rule on a Saturday drafts for the next business day (Mon 11-16) and SAYS so", f"{ev.out()}")
    rr = _rule(rule_type="redirect_incoming", basis="pct_incoming", frequency="on_incoming", source_account_id=s,
               destinations=[{"account_id": z, "pct": "25"}])
    t1 = str(_id(960))
    ev = CR.evaluate(rr, {s: B(D("50000.00"), date(2026, 11, 9))},
                     [{"transaction_id": t1, "account_id": s, "amount": D("4321.99")}], TODAY)
    check(bool([d.amount for d in ev.drafts] == [D("1080.49")] and ev.period_key == f"txn:{t1}"),
          "% of incoming: 25 % of the 4,321.99 credit = 1,080.4975 → 1,080.49; the period IS the transaction",
          f"{ev.out()}")
    ev = CR.evaluate(rr, {s: B(D("50000.00"), date(2026, 11, 9))},
                     [{"transaction_id": t1, "account_id": "other", "amount": D("4321.99")}], TODAY)
    check(bool(not ev.drafts and ev.skipped is not None), "a credit to ANOTHER account drafts nothing", f"{ev.out()}")
    rt = _rule(rule_type="maintain_target", frequency="daily", condition="below", target_amount=D("10000.00"),
               source_account_id=s, supporting_account_id=z)
    ev = CR.evaluate(rt, {s: B(D("7500.255"), date(2026, 11, 9)), z: B(D("40000.00"), date(2026, 11, 9))}, [], TODAY)
    d0 = ev.drafts[0] if ev.drafts else None
    check(bool(d0 is not None and d0.amount == D("2499.75") and d0.source_account_id == z and d0.destination_account_id == s),
          "target BELOW: 10,000.00 − 7,500.255 = 2,499.745 → 2,499.75 (UP: it reaches the target), drawn FROM the "
          "supporting account", f"{ev.out()}")
    ra = dict(rt, condition="above")
    ev = CR.evaluate(ra, {s: B(D("12345.678"), date(2026, 11, 9)), z: B(D("1.00"), date(2026, 11, 9))}, [], TODAY)
    d0 = ev.drafts[0] if ev.drafts else None
    check(bool(d0 is not None and d0.amount == D("2345.67") and d0.source_account_id == s and d0.destination_account_id == z),
          "target ABOVE: 12,345.678 − 10,000.00 = 2,345.678 → 2,345.67 (DOWN: never below the target), swept TO the "
          "supporting account", f"{ev.out()}")
    ev = CR.evaluate(rt, {s: B(D("10000.00"), date(2026, 11, 9)), z: B(D("40000.00"), date(2026, 11, 9))}, [], TODAY)
    check(bool(not ev.drafts and not ev.holds and "at or above" in (ev.skipped or "")), "at the target: nothing to draw",
          f"{ev.out()}")
    rb = _rule(rule_type="recurring", basis="fixed", frequency="business_days", source_account_id=s,
               destinations=[{"account_id": x, "amount": "100.00"}])
    days = [date(2026, 11, 10), HOLIDAY, date(2026, 11, 12)]
    outs = [CR.evaluate(rb, {s: B(D("9000.00"), date(2026, 11, 10))}, [], d) for d in days]
    check(bool([len(o.drafts) for o in outs] == [1, 0, 1] and "holiday" in (outs[1].skipped or "")
          and outs[2].drafts[0].value_date == date(2026, 11, 12)),
          "recurring on business days: 11-10 drafts, 11-11 (Veterans Day) is SKIPPED with the reason, 11-12 drafts",
          f"{[o.out() for o in outs]}")
    ev = CR.evaluate(rf, {s: B(D("2000.00"), date(2026, 11, 9))}, [], TODAY)
    check(bool(not ev.drafts and [h.kind for h in ev.holds] == ["shortfall"] and ev.holds[0].amount == D("2500.00")
          and "no partial overdraft" in ev.holds[0].reason),
          "shortfall: 2,500.00 from an account holding 2,000.00 → HELD, nothing drafted (never a partial overdraft)",
          f"{ev.out()}")
    rt2 = dict(rt, target_amount=D("50000.00"))
    ev = CR.evaluate(rt2, {s: B(D("100.00"), date(2026, 11, 9)), z: B(D("1000.00"), date(2026, 11, 9))}, [], TODAY)
    check(bool(not ev.drafts and [h.kind for h in ev.holds] == ["shortfall"]),
          "shortfall on the SUPPORTING account of a target draw → held", f"{ev.out()}")
    ev = CR.evaluate(r, {s: B(D("1234.57"), date(2026, 11, 6))}, [], TODAY)
    check(bool(not ev.drafts and [h.kind for h in ev.holds] == ["stale"]),
          "stale: a balance as of Fri 11-06 on Tue 11-10 (2 business days) → held", f"{ev.out()}")
    ev = CR.evaluate(r, {s: B(D("1234.57"), date(2026, 11, 10))}, [], date(2026, 11, 12))
    check(len(ev.drafts) == 2, "not stale across the holiday: 11-10 → 11-12 is ONE business day", f"{ev.out()}")
    ev = CR.evaluate(r, {}, [], TODAY)
    check(bool(not ev.drafts and [h.kind for h in ev.holds] == ["no_balance"]), "no balance on record → held", f"{ev.out()}")
    rc = dict(rf, cap_per_firing=D("1000.00"))
    ev = CR.evaluate(rc, {s: B(D("9000.00"), date(2026, 11, 9))}, [], TODAY)
    check(bool([d.amount for d in ev.drafts] == [D("1000.00")] and [(h.kind, h.amount) for h in ev.holds] == [("cap", D("1500.00"))]
          and ev.outcome == "partly_held"), "cap: 2,500.00 with a 1,000.00 cap → 1,000.00 drafted, 1,500.00 HELD",
          f"{ev.out()}")
    ev = CR.evaluate(rc, {s: B(D("9000.00"), date(2026, 11, 9))}, [], TODAY, ceiling_amount=D("400.00"))
    check([d.amount for d in ev.drafts] == [D("400.00")], "the org ceiling below the rule's cap wins (400.00)",
          f"{ev.out()}")
    ev = CR.evaluate(rf, {s: B(D("9000.00"), date(2026, 11, 9))}, [], TODAY, drafted_today=5)
    check(bool(not ev.drafts and [h.kind for h in ev.holds] == ["per_day"]), "per day: the 6th instruction of the day → held",
          f"{ev.out()}")
    r0 = dict(r, destinations=[{"account_id": x, "pct": "0.0001"}])
    ev = CR.evaluate(r0, {s: B(D("50.00"), date(2026, 11, 9))}, [], TODAY)
    check(bool(not ev.drafts and not ev.holds and any("under $0.01" in n for n in ev.notes)),
          "0.0001 % of 50.00 rounds under $0.01 → nothing drafted (never below one cent)", f"{ev.out()}")
    ev = CR.evaluate(dict(r, status="paused"), fresh, [], TODAY)
    check(bool(not ev.drafts and not ev.holds and "paused" in (ev.skipped or "")), "a paused rule drafts nothing")
    check(bool(CR.period_key("weekly", TODAY) == "w:2026-W46" and CR.period_key("monthly", TODAY) == "m:2026-11"
          and CR.period_key("business_days", TODAY) == "d:2026-11-10"
          and CR.cron_for("business_days") == "0 10 * * 1-5" and CR.cron_for("weekly", 3) == "0 10 * * 3"
          and CR.cron_for("on_incoming") is None), "periods (day / ISO week / month / transaction) and schedules")
    floats = []
    for bad in (2500.0, True):
        try:
            CR.parse_cents(bad, "x")
        except CR.RuleError:
            floats.append(bad)
    check(floats == [2500.0, True], "a binary float amount is refused (Decimal only)")


async def y_validation(conn):
    section("[Y] Validation — third-party destination, % > 100, a rail the custodian lacks, a cap over the ceiling: "
            "refused with reasons (valid bodies otherwise)")
    n0 = int(await platform(conn, "SELECT count(*) FROM cash_rules WHERE household_id = $1", H1, fetch="fetchval"))
    cases = (
        ("third-party destination (another owner)", rule_body("gov", destinations=[{"account_id": str(A["A4"]),
                                                                                     "amount": "1200.00"}]),
         "third-party payments come later"),
        ("percentages over 100", rule_body("cap", destinations=[{"account_id": str(A["A2"]), "pct": "60"},
                                                               {"account_id": str(A["A3"]), "pct": "50"}]),
         "more than 100%"),
        ("a rail the custodian lacks (wire 'no')", rule_body("recurring", rail="wire"), "does not declare"),
        ("a cap over the org ceiling (25,000 > 20,000)", rule_body("recurring", cap_per_firing="25000.00"),
         "ceiling"),
        ("an amount that is not more than zero", rule_body("recurring", destinations=[{"account_id": str(A["A2"]),
                                                                                       "amount": "0.00"}]),
         "at least $0.01"),
    )
    for label, body, want in cases:
        c, b = await api(U_OWN, "POST", rp(H1), body)
        rs = reasons_of(b)
        check(bool(c == 422 and any(want in x for x in rs)), f"{label} → 422 naming why", f"{c} {rs or detail_text(b)[:300]}")
    c, b = await api(U_OWN, "POST", rp(H1), rule_body("gov", cap_per_firing=5000.0))
    check(c == 422, "a binary float cap is refused at the API boundary (422)", f"{c}")
    n1 = int(await platform(conn, "SELECT count(*) FROM cash_rules WHERE household_id = $1", H1, fetch="fetchval"))
    check(n1 == n0, "no refused definition wrote a rule row", f"{n0} → {n1}")

    async def raw_third(c2):
        await c2.execute(
            "INSERT INTO public.cash_rules (org_id, rule_id, household_id, name, rule_type, source_account_id, "
            "destinations, basis, frequency, rail, cap_per_firing, max_per_day, owner_id, maker_ids) VALUES ($1, $2, "
            "$3, 'raw', 'distribute', $4, $5::jsonb, 'fixed', 'daily', 'ach', 100, 1, $6, ARRAY[$6]::uuid[])",
            ORG_A, uuid.uuid4(), H1, A["A1"], json.dumps([{"account_id": str(A["A4"]), "amount": "10.00"}]), U_OWN)
    _r, err = await in_rolled_back(conn, ORG_A, raw_third)
    check(bool(err is not None and "third-party payments come later" in str(err)),
          "the DATABASE refuses a raw third-party rule too (cash_rules_same_owner_trg) — rolled back",
          f"{type(err).__name__ if err else None}: {err}")
    # a PERSON's payment draft may not link to a cash rule (only a rule's firing does)
    c, b = await api(U_OWN, "POST", f"/api/v1/cash/households/{H1}/payments",
                     {"source_account_id": str(A["A1"]), "destination_account_id": str(A["A2"]), "amount": "10.00",
                      "rail": "ach", "value_date": TODAY.isoformat(), "purpose": f"{NAME}manual",
                      "purpose_ref_type": "cash_rule", "purpose_ref_id": str(_id(900))})
    check(bool(c == 422 and PS.RULE_ONLY_REF in detail_text(b)), "a person's payment draft linked to a cash rule → 422",
          f"{c} {detail_text(b)[:200]}")
    # the preview drafts nothing
    p0 = int(await platform(conn, "SELECT count(*) FROM payment_instructions WHERE household_id = $1", H1,
                            fetch="fetchval"))
    c, b = await api(U_OWN, "POST", rp(H1, "/preview"), rule_body("cap"))
    pv = (b or {}).get("preview") or {}
    p1 = int(await platform(conn, "SELECT count(*) FROM payment_instructions WHERE household_id = $1", H1,
                            fetch="fetchval"))
    check(bool(c == 200 and pv.get("outcome") == "partly_held" and [d.get("amount") for d in pv.get("drafts", [])] == ["1000.00"]
          and pv.get("would_send") is False and p1 == p0),
          "the editor's PREVIEW: 50 % + 50 % of 50,000.00 with a 1,000.00 cap → 1,000.00 drafted + the rest held — "
          "and NOTHING written", f"{c} {dumps(pv)[:400]} {p0}→{p1}")


async def y_governance(conn):
    section("[Y] Governance — the cash_rule FLOOR: the maker / owner cannot activate; a second person does; resume is "
            "maker-checker; pause / retire are immediate")
    c, b = await api(U_OWN, "POST", rp(H1), rule_body("gov"))
    v = ver_of(b)
    vid = v.get("id")
    check(bool(c == 201 and v.get("status") == "draft" and v.get("owner_id") == str(U_OWN)),
          "a new rule is a DRAFT owned by its drafter", f"{c} {dumps(b)[:300]}")
    if not vid:
        return
    row = await live_rule(conn, vid)
    trig = await trigger_row(conn, row.get("trigger_id"))
    check(bool(bool(trig) and trig["is_active"] is False and trig["created_by"] == U_OWN and trig["trigger_type"] == "scheduled"
          and trig["schedule_cron"] == "0 10 1 * *" and trig["timezone"] == "America/New_York"),
          "its ONE workflow trigger exists, INACTIVE, owned by the owner (monthly on the 1st, 10:00 Eastern)",
          f"{ {k: trig.get(k) for k in ('is_active', 'created_by', 'trigger_type', 'schedule_cron', 'timezone')} }")
    c, b = await api(U_ED, "POST", rp(H1, f"/{vid}/edit"), {"changes": {"name": f"{NAME}gov edited"}})
    row = await live_rule(conn, vid)
    check(bool(c == 200 and {U_OWN, U_ED} <= set(row.get("maker_ids") or []) and row.get("owner_id") == U_OWN),
          "an editor joins the draft's makers; the owner stays the owner", f"{c} {row.get('maker_ids')}")
    c, b = await api(U_ED, "POST", rp(H1, f"/{vid}/submit"), {})
    check(bool(c == 200 and ver_of(b).get("status") == "pending_approval"), "submitted → awaiting approval", f"{c}")
    n_ref = len(await refusals(conn, "cash_rule", vid))
    c, b = await api(U_OWN, "POST", rp(H1, f"/{vid}/approve"), {"note": "self"})
    row = await live_rule(conn, vid)
    ref = await refusals(conn, "cash_rule", vid)
    check(bool(c == 403 and row.get("status") == "pending_approval" and len(ref) == n_ref + 1
          and any(r["checker_id"] == U_OWN for r in ref)),
          "the OWNER (a maker) cannot activate it: 403, nothing changes, the refusal recorded in sod_decisions",
          f"{c} {detail_text(b)[:200]} {row.get('status')} {ref}")
    c, b = await api(U_CK2, "POST", rp(H1, f"/{vid}/approve"), {"note": "a second eligible person"})
    row = await live_rule(conn, vid)
    trig = await trigger_row(conn, row.get("trigger_id"))
    check(bool(c == 200 and row.get("status") == "active" and row.get("approved_by") == U_CK2 and trig.get("is_active") is True),
          "a second eligible person (compliance) activates it; the trigger turns on", f"{c} {row.get('status')}")
    c, b = await api(U_ED, "POST", rp(H1, f"/{vid}/pause"), {"reason": "family travelling"})
    row = await live_rule(conn, vid)
    trig = await trigger_row(conn, row.get("trigger_id"))
    check(bool(c == 200 and row.get("status") == "paused" and trig.get("is_active") is False),
          "pause applies at once (tightening): paused, the trigger off", f"{c} {row.get('status')}")
    c, b = await api(U_ED, "POST", rp(H1, f"/{vid}/resume"), {})
    row = await live_rule(conn, vid)
    check(bool(c == 200 and row.get("status") == "paused" and row.get("pending_action") == "resume"
          and row.get("resume_requested_by") == U_ED), "resume is ASKED for: still paused, awaiting a second person",
          f"{c} {detail_text(b)[:200]} {row.get('status')} {row.get('pending_action')}")
    c1, _b = await api(U_OWN, "POST", rp(H1, f"/{vid}/approve"), {"note": "self"})
    c2, _b = await api(U_CK, "POST", rp(H1, f"/{vid}/approve"), {"note": "resume ok"})
    row = await live_rule(conn, vid)
    trig = await trigger_row(conn, row.get("trigger_id"))
    check(bool(c1 == 403 and c2 == 200 and row.get("status") == "active" and row.get("approved_by") == U_CK
          and trig.get("is_active") is True),
          "the owner cannot approve the resume (403); another person does → active, trigger on", f"{c1} {c2}")
    c, b = await api(U_OWN, "POST", rp(H1, f"/{vid}/retire"), {"reason": "no longer needed"})
    row = await live_rule(conn, vid)
    trig = await trigger_row(conn, row.get("trigger_id"))
    check(bool(c == 200 and row.get("status") == "retired" and trig.get("is_active") is False),
          "retire applies at once: retired, the trigger off", f"{c} {row.get('status')}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c2: c2.execute(
        "UPDATE public.cash_rules SET status = 'active' WHERE id = $1::uuid AND system_to IS NULL", vid))
    check(bool(err is not None and "immutable" in str(err)), "a retired version is immutable in the database", f"{err}")
    _r, err = await in_rolled_back(conn, ORG_A, lambda c2: c2.execute(
        "DELETE FROM public.cash_rules WHERE id = $1::uuid", vid))
    check(bool(err is not None and "never deleted" in str(err)), "a rule is never deleted (app role, no teardown GUC)",
          f"{err}")
    S["rule"]["GOV"] = row


async def y_engine(conn):
    section("[Y] Engine — a scheduled firing drafts + submits ONE instruction (maker = the owner); the same period "
            "again → none; an incoming credit fires once per transaction; caps hold the excess with an alert; a "
            "paused rule and the kill switch → nothing; S29b refuses approve / send from the rule path")
    ok = await admin_setting(WA.KEY_AUTONOMY, True) and await admin_setting(WA.KEY_SHADOW_RUNS, 1)
    check(ok, "S29b: the org switch workflows.autonomy ON and 1 shadow run required — each decided by a second person")
    rs = await create_active(conn, "recurring", "SCHED")
    ri = await create_active(conn, "redirect", "INC")
    rc = await create_active(conn, "cap", "CAP")
    if not (rs and ri and rc):
        return
    for key, row in (("SCHED", rs), ("INC", ri), ("CAP", rc)):
        await opt_in(conn, row, key)
    tr = await trigger_row(conn, rs["trigger_id"])
    dec = evaluate_trigger(schedule_cron=tr["schedule_cron"], timezone_name=tr["timezone"],
                           now_utc=occ(TODAY) + timedelta(minutes=5), last_fired_at=None)
    check(bool(dec.due and dec.occurrence_utc == occ(TODAY)), "the trigger's schedule is due at 10:00 Eastern = 15:00 UTC "
          "on 11-10 (the scheduler's own evaluator)", f"{dec}")
    # ── 1. one scheduled firing → ONE instruction, pending approval, maker = the owner
    r1 = await fire_sched(rs, TODAY)
    pays = await rule_payments(conn, rs["rule_id"])
    fs = await firings(conn, rs["rule_id"])
    p1 = pays[0] if pays else {}
    check(bool(r1.get("ok") and r1.get("status") == "completed" and len(pays) == 1 and p1.get("status") == "pending_approval"
          and p1.get("amount") == D("2500.00") and p1.get("maker_ids") == [U_OWN] and p1.get("maker_id") == U_OWN
          and p1.get("value_date") == TODAY and p1.get("approval_task_id") is not None and p1.get("approved_by") is None),
          "a scheduled firing drafts + submits exactly ONE instruction: 2,500.00 A1 → A2, pending approval, its "
          "approval task open, maker = the rule owner", f"{r1} {[(p['amount'], p['status']) for p in pays]}")
    ev1 = await pay_events(conn, p1.get("id")) if p1 else []
    check(bool([e["action"] for e in ev1] == ["create", "submit"] and all(e["actor_id"] == U_OWN for e in ev1)),
          "its payment_events: create + submit, both by the owner (written by the database)", f"{ev1}")
    check(bool(len(fs) == 1 and fs[0]["outcome"] == "drafted" and fs[0]["period_key"] == "d:2026-11-10"
          and fs[0]["instruction_ids"] == [p1.get("id")] and fs[0]["workflow_run_id"] == UUID(str(r1.get("run_id")))),
          "one firing record: period d:2026-11-10 → that instruction, linked to the engine run", f"{fs}")
    step = await platform(conn, "SELECT rs.autonomy_mode, rs.delegate_for, rs.idempotency_key FROM workflow_run_steps rs "
                                "WHERE rs.workflow_run_id = $1::uuid", r1.get("run_id"), fetch="fetchrow")
    check(bool(step is not None and step["autonomy_mode"] == "unattended" and step["delegate_for"] == U_OWN
          and (step["idempotency_key"] or "").startswith("wfstep:")),
          "the step ran UNATTENDED as the owner's delegate, claimed once (S29b)", f"{dict(step) if step else None}")
    S["pay"]["P1"] = p1.get("id")
    # ── 2. the same period again → none (the rule's claim), and the scheduler's own claim refuses the occurrence
    r2 = await fire_sched(rs, TODAY)
    pays2 = await rule_payments(conn, rs["rule_id"])
    data2 = (((await platform(conn, "SELECT result FROM workflow_run_steps WHERE workflow_run_id = $1::uuid",
                              r2.get("run_id"), fetch="fetchval")) or "{}"))
    check(bool(r2.get("ok") and len(pays2) == 1 and len(await firings(conn, rs["rule_id"])) == 1
          and "already fired" in str(data2)), "the same period again drafts NOTHING (idempotent per (rule, period))",
          f"{r2} {len(pays2)} {str(data2)[:200]}")
    c1 = await SCHED._claim(conn, trigger_id=rs["trigger_id"], occurrence_utc=occ(date(2026, 11, 20)))
    c2 = await SCHED._claim(conn, trigger_id=rs["trigger_id"], occurrence_utc=occ(date(2026, 11, 20)))
    check(bool(c1 is not None and c2 is None), "and the scheduler's claim admits one occurrence once (the second is lost)",
          f"{c1} {c2}")
    # ── 3. the holiday: skipped with its reason, nothing drafted
    await fire_sched(rs, HOLIDAY)
    fs = await firings(conn, rs["rule_id"])
    hol = [f for f in fs if f["period_key"] == "d:2026-11-11"]
    check(bool(len(hol) == 1 and hol[0]["outcome"] == "skipped" and "holiday" in (hol[0]["reason"] or "")
          and len(await rule_payments(conn, rs["rule_id"])) == 1),
          "Veterans Day: the business-day rule's firing is SKIPPED with the reason, nothing drafted", f"{hol}")
    # ── 4. an incoming credit fires the redirect rule ONCE per transaction (the real emitter + publish_event)
    set_day(TODAY, occ(TODAY) + timedelta(hours=1))
    pool = await get_pool()
    pub = await CR.publish_incoming_credits(ORG_A, [S["tx"]["T_CREDIT"], S["tx"]["T_OUT"], S["tx"]["T_OTHER"]], pool=pool)
    pays_i = await rule_payments(conn, ri["rule_id"])
    check(bool(len(pub) == 1 and len(pub[0].get("delivered") or []) >= 1 and len(pays_i) == 1
          and pays_i[0]["amount"] == D("1080.49") and pays_i[0]["destination_account_id"] == A["A3"]
          and pays_i[0]["status"] == "pending_approval" and pays_i[0]["maker_ids"] == [U_OWN]),
          "the emitter publishes ONE event (the credit INTO A1 — not the sell, not A2's dividend); the redirect rule "
          "drafts 25 % of 4,321.99 = 1,080.49 A1 → A3, pending approval", f"{dumps(pub)[:400]} {pays_i}")
    pub2 = await CR.publish_incoming_credits(ORG_A, [S["tx"]["T_CREDIT"]], pool=pool)
    fs_i = await firings(conn, ri["rule_id"])
    check(bool(len(await rule_payments(conn, ri["rule_id"])) == 1 and len(fs_i) == 1
          and fs_i[0]["period_key"] == f"txn:{S['tx']['T_CREDIT']}" and pub2 and pub2[0].get("deduped") is True
          and not pub2[0].get("delivered")),
          "the same credit again → deduped, no new run, nothing drafted (once per transaction)", f"{dumps(pub2)[:300]}")
    rdup = await start_run(ri, {"trigger_type": "event", "event_type": CR.EVENT_TYPE, "source_type": CR.FEED_SOURCE,
                                "source_id": S["tx"]["T_CREDIT"]})
    check(bool(rdup.get("status") == "completed" and len(await rule_payments(conn, ri["rule_id"])) == 1),
          "a second run for the same transaction (started directly) drafts nothing — the rule's own claim",
          f"{rdup}")
    # ── 5. caps: the excess is held, with an alert to the owner and the org admins
    r5 = await fire_sched(rc, TODAY)
    pays_c = await rule_payments(conn, rc["rule_id"])
    fs_c = await firings(conn, rc["rule_id"])
    todos = [dict(t) for t in await platform(
        conn, "SELECT user_id, detail FROM member_todos WHERE org_id = $1 AND source = $2 AND related_id = $3::uuid",
        ORG_A, WA.TODO_SOURCE_HOLD, r5.get("run_id"))]
    who = {t["user_id"] for t in todos}
    check(bool(r5.get("ok") and len(pays_c) == 1 and pays_c[0]["amount"] == D("1000.00")
          and len(fs_c) == 1 and fs_c[0]["outcome"] == "partly_held"
          and sorted((h["kind"], h["amount"]) for h in _j(fs_c[0]["holds"])) == [("cap", "24000.00"), ("cap", "25000.00")]),
          "caps: 50 % + 50 % of 50,000.00 under a 1,000.00 cap → ONE 1,000.00 instruction; 24,000.00 + 25,000.00 HELD",
          f"{r5} {[(p['amount']) for p in pays_c]} {fs_c}")
    check(bool(U_OWN in who and U_ADM1 in who and all("per-firing cap" in (t["detail"] or "") for t in todos)),
          "the hold alerts the owner AND the org admins (workflow_autonomy.hold_alerts — the engine's own)",
          f"{todos}")
    # ── 6. an edit runs the OLD version until the new one is approved
    c, b = await api(U_ED, "POST", rp(H1, f"/{rs['id']}/new_version"),
                     {"changes": {"destinations": [{"account_id": str(A["A2"]), "amount": "3000.00"}]}})
    v2 = ver_of(b)
    check(bool(c == 200 and v2.get("status") == "draft" and v2.get("version_no") == 2
          and (await live_rule(conn, rs["id"])).get("status") == "active"),
          "an edit of the active rule is a NEW draft version (v2); v1 stays active", f"{c} {dumps(b)[:300]}")
    await fire_sched(rs, date(2026, 11, 12))
    amounts = [p["amount"] for p in await rule_payments(conn, rs["rule_id"])]
    check(amounts == [D("2500.00"), D("2500.00")], "11-12, before the new version is approved: the OLD terms run "
          "(2,500.00)", f"{amounts}")
    c1, _b = await api(U_ED, "POST", rp(H1, f"/{v2.get('id')}/submit"), {})
    c2, _b = await api(U_OWN, "POST", rp(H1, f"/{v2.get('id')}/approve"), {"note": "self"})
    c3, _b = await api(U_CK, "POST", rp(H1, f"/{v2.get('id')}/approve"), {"note": "v2 ok"})
    old = await platform(conn, "SELECT status, superseded_by FROM cash_rules WHERE id = $1::uuid AND system_to IS NULL",
                         rs["id"], fetch="fetchrow")
    check(bool(c1 == 200 and c2 == 403 and c3 == 200 and old is not None and old["status"] == "superseded"
          and str(old["superseded_by"]) == v2.get("id")),
          "v2 approved by a second person (the owner refused, 403) → v1 SUPERSEDED", f"{c1} {c2} {c3} {old}")
    await fire_sched(rs, date(2026, 11, 13))
    amounts = [p["amount"] for p in await rule_payments(conn, rs["rule_id"])]
    check(amounts == [D("2500.00"), D("2500.00"), D("3000.00")], "11-13: the NEW terms run (3,000.00)", f"{amounts}")
    rs2 = await live_rule(conn, v2.get("id"))
    # ── 7. the kill switch → nothing; a stale balance → held; a paused rule → nothing
    killed = await admin_setting(WA.KEY_KILL, True, reason="verify_cash06: stop every rule")
    rk = await fire_sched(rs2, date(2026, 11, 16))
    n_after = len(await rule_payments(conn, rs["rule_id"]))
    st = await run_status(conn, rk.get("run_id"))
    check(bool(killed and st == ENG.RUN_STATUS_AWAITING_APPROVAL and n_after == 3
          and not [f for f in await firings(conn, rs["rule_id"]) if f["period_key"] == "d:2026-11-16"]),
          "the ORG KILL SWITCH: the rule's step SUSPENDS for a person — nothing evaluated, nothing drafted",
          f"{killed} {rk} {st} {n_after}")
    cleared = await admin_setting(WA.KEY_KILL, False, reason="verify_cash06: clear")
    r7 = await fire_sched(rs2, date(2026, 11, 17))
    f7 = [f for f in await firings(conn, rs["rule_id"]) if f["period_key"] == "d:2026-11-17"]
    check(bool(cleared and r7.get("ok") and len(f7) == 1 and f7[0]["outcome"] == "held" and "stale" in (f7[0]["reason"] or "")
          and len(await rule_payments(conn, rs["rule_id"])) == 3),
          "a STALE balance (as of 11-12 on 11-17) → the firing is HELD, nothing drafted", f"{r7} {f7}")
    c, b = await api(U_ED, "POST", rp(H1, f"/{rs2['id']}/pause"), {"reason": "verify"})
    tr = await trigger_row(conn, rs2["trigger_id"])
    due = [r for r in await SCHED.load_due_candidates(conn) if r["id"] == rs2["trigger_id"]]
    rp_ = await fire_sched(dict(rs2, status="paused"), date(2026, 11, 18))
    check(bool(c == 200 and tr.get("is_active") is False and not due and rp_.get("ok")
          and not [f for f in await firings(conn, rs["rule_id"]) if f["period_key"] == "d:2026-11-18"]
          and len(await rule_payments(conn, rs["rule_id"])) == 3),
          "a PAUSED rule: its trigger is off (the scheduler never sees it) and even a forced run drafts nothing",
          f"{c} {tr.get('is_active')} {due} {rp_}")
    # ── 8. S29b refuses approve / send from the rule path (attempted)
    run_ctx = json.loads(await platform(conn, "SELECT context::text FROM workflow_runs WHERE id = $1::uuid",
                                        r1.get("run_id"), fetch="fetchval") or "{}")
    refused = {}
    for key in ("cash.payment_approve", "cash.payment_send", "cash.payment_submit"):
        fake = type("V", (), {"key": key, "access_type": "write", "workflow_invocable": True, "tier": 2,
                              "autonomy_class": None})()
        with _ctx(ORG_A):
            d = await WA.decide_step(pool, org_id=ORG_A, run_id=r1.get("run_id"), run_context=run_ctx,
                                     started_by=U_OWN, action=fake, invocable=True)
        refused[key] = (d.mode, d.reason)
    check(bool(all(m == WA.MODE_SUSPEND and "never run unattended" in why and "moves money" in why
              for m, why in refused.values())
          and all(REGISTRY.get(k) is None for k in refused)),
          "S29b on the rule's own run (switches on, version opted in): approve / send / submit as a step are NEVER — "
          "each suspends for a person, and none is a registered action", f"{refused}")
    me = WA.verb_autonomy(REGISTRY.get(CR.ACTION_KEY))
    check(bool(me["class"] == WA.ELIGIBLE_WITH_CAP and me.get("draft_only") is True and callable(me.get("quantity"))
          and me["quantity"]({}) == CR.MAX_DESTINATIONS and sod.verb_duty(REGISTRY.get(CR.ACTION_KEY)) == "propose"),
          "the rule's verb: eligible_with_cap, draft only, quantity = its 5 destinations, duty 'propose'", f"{me}")
    c, b = await api(U_OWN, "POST", "/api/v1/assistant/confirm",
                     {"proposed_action": {"action_key": CR.ACTION_KEY, "params": {"run_context": {
                         "trigger_id": str(rs2["trigger_id"])}}, "rationale": "verify"}, "choice_value": "save"})
    listed = [a.key for a in REGISTRY.list_for_user(str(U_OWN), {"initiate_payment", "approve_payment", "view_portfolio"})]
    check(bool(c == 403 and CR.ACTION_KEY not in listed), "the assistant never lists the rule's verb and /assistant/confirm "
          "refuses it (403)", f"{c} {detail_text(b)[:200]}")
    forged = []
    for kw in ({"workflow_run_id": None}, {"workflow_run_id": r1.get("run_id")}):
        try:
            with _ctx(ORG_A):
                await CR._handler(pool=pool, user_id=U_OWN, org_id=ORG_A, run_context={"trigger_id": str(rs2["trigger_id"])},
                                  **kw)
            forged.append("ran")
        except CR.FireRefused:
            forged.append("refused")
    check(forged == ["refused", "refused"], "a forged call of the handler (no run; a finished run) is REFUSED — the "
          "verb runs only inside its rule's own running workflow run", f"{forged}")
    S["rule"]["SCHED2"] = rs2


async def y_instructions(conn):
    section("[Y] The drafted instructions — the owner cannot approve (the cash07 floor, in sod_decisions); a different "
            "person can; nothing sent; no adapter send; no network")
    pid = S["pay"].get("P1")
    if not pid:
        check(False, "the engine section drafted the instruction this section approves")
        return
    set_day(TODAY)   # the instruction's value date (11-10) must still be today for a checker to approve it
    hh = f"/api/v1/cash/households/{H1}/payments/{pid}"
    n0 = len(await refusals(conn, "payment_instruction", pid))
    c, b = await api(U_OWN, "POST", hh + "/approve", {"note": "the owner"})
    ref = await refusals(conn, "payment_instruction", pid)
    st = (await platform(conn, "SELECT status FROM payment_instructions WHERE id = $1::uuid AND system_to IS NULL", pid,
                         fetch="fetchval"))
    check(bool(c == 403 and st == "pending_approval" and len(ref) == n0 + 1 and any(r["checker_id"] == U_OWN for r in ref)),
          "the RULE OWNER cannot approve an instruction the rule drafted: 403, unchanged, recorded in sod_decisions",
          f"{c} {detail_text(b)[:200]} {st} {ref}")
    c, b = await api(U_CK, "POST", hh + "/approve", {"note": "a different person"})
    row = await platform(conn, "SELECT status, approved_by, sent_at FROM payment_instructions WHERE id = $1::uuid AND "
                               "system_to IS NULL", pid, fetch="fetchrow")
    check(bool(c == 200 and row["status"] == "approved" and row["approved_by"] == U_CK and row["sent_at"] is None),
          "a different person approves it — approved, NOT sent (sending is a person's step, never the rule's)",
          f"{c} {dict(row)}")
    rule_ids = [r["rule_id"] for k, r in S["rule"].items() if k in ("SCHED", "INC", "CAP")]
    rows = [dict(r) for r in await platform(
        conn, "SELECT id, status, approved_by, sent_at, adapter FROM payment_instructions WHERE purpose_ref_type = "
              "'cash_rule' AND purpose_ref_id = ANY($1::uuid[]) AND system_to IS NULL", rule_ids)]
    others = [r for r in rows if str(r["id"]) != str(pid)]
    check(bool(len(rows) >= 5 and all(r["status"] == "pending_approval" and r["approved_by"] is None for r in others)
          and all(r["sent_at"] is None and r["adapter"] is None for r in rows)),
          f"every one of the {len(rows)} rule-drafted instructions is pending approval (but the one approved by a "
          "person); none sent, none routed to an adapter", f"{rows}")
    acts = {r["action"] for r in await platform(
        conn, "SELECT DISTINCT e.action FROM payment_events e JOIN payment_instructions p ON p.id = e.payment_id WHERE "
              "p.purpose_ref_type = 'cash_rule' AND p.purpose_ref_id = ANY($1::uuid[])", rule_ids)}
    check(acts == {"create", "submit", "approve"}, "their events: create / submit (the rule) and approve (a person) — "
          "never sent / settled / reconciled", f"{sorted(acts)}")
    check(bool(SEND_CALLS == [] and NETWORK_CALLS == [] and MODEL_CALLS == []),
          "no adapter send was called, no network call, no model call", f"{SEND_CALLS} {NETWORK_CALLS} {MODEL_CALLS}")


async def y_visibility(conn):
    section("[Y] Visibility + isolation — members their family's active rules only; staff their visible families; org "
            "isolation both ways (rolbypassrls = false); no full account number")
    c, b = await api(U_MEM, "GET", rp(H1))
    items = (b or {}).get("items") or []
    statuses = {((it.get("current") or {}).get("status")) for it in items}
    flat = dumps(b)
    check(bool(c == 200 and (b or {}).get("member_view") is True and bool(items) and statuses == {"active"}
          and "maker_ids" not in flat and "owner_name" not in flat and (b.get("permissions") or {}).get("can_write") is False
          and b.get("actions") == [] and all((it.get("current") or {}).get("actions") == [] for it in items)
          and "Rules prepare payment instructions for approval" in flat),
          "a member reads their family's ACTIVE rules only, read-only (no makers, no owner, no actions), with the "
          "cash.rules text", f"{c} {statuses} {flat[:300]}")
    c1, _ = await api(U_MEM, "GET", rp(H2))
    c2, _ = await api(U_MEM, "POST", rp(H1), rule_body("gov"))
    check(bool(c1 == 404 and c2 == 403), "a member: another family's rules are a 404; creating a rule is refused (403)",
          f"{c1} {c2}")
    c3, _ = await api(U_RS, "GET", rp(H1))
    c4, _ = await api(U_RS, "GET", rp(H2))
    c5, _ = await api(U_B, "GET", rp(H1))
    check(bool(c3 == 404 and c4 == 200 and c5 == 404), "staff see only their visible families (restricted → H1 is a 404, H2 "
          "200); org B → org A's household is a 404", f"{c3} {c4} {c5}")
    a_in_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    a_in_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    f_in_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM cash_rule_firings WHERE org_id = $1", ORG_A,
                              fetch="fetchval"))

    async def cross_insert(c6):
        await c6.execute("INSERT INTO public.cash_rule_firings (org_id, rule_id, rule_version_id, period_key, "
                         "trigger_type, owner_id, evaluated_on) VALUES ($1, $2, $3, 'd:2026-12-01', 'manual', $4, $5)",
                         ORG_A, uuid.uuid4(), S["rule"]["SCHED"]["id"], U_OWN, TODAY)
    _r, err = await in_rolled_back(conn, ORG_B, cross_insert)
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(by is False and a_in_a > 0 and a_in_b == 0 and f_in_b == 0
          and isinstance(err, asyncpg.InsufficientPrivilegeError)),
          "RLS isolation both ways under a non-bypassing role: org B reads 0 of org A's rules / firings and cannot "
          "insert one for org A", f"by={by} a={a_in_a} b={a_in_b} f={f_in_b} {type(err).__name__ if err else None}")
    b_in_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM cash_rules WHERE org_id = $1", ORG_B, fetch="fetchval"))
    check(b_in_a == 0, "and org A reads none of org B's", f"{b_in_a}")
    allb = "\n".join(x for _w, x in BODIES)
    audit = "\n".join(r["p"] for r in await platform(
        conn, "SELECT payload::text AS p FROM audit_log WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    fires = "\n".join(r["p"] for r in await platform(
        conn, "SELECT (drafts::text || holds::text || COALESCE(reason, '')) AS p FROM cash_rule_firings WHERE org_id = "
              "ANY($1::uuid[])", FIXTURE_ORGS))
    runs = "\n".join(r["c"] for r in await platform(
        conn, "SELECT COALESCE(context::text, '') || COALESCE((SELECT string_agg(COALESCE(result::text, ''), '') FROM "
              "workflow_run_steps s WHERE s.workflow_run_id = r.id), '') AS c FROM workflow_runs r WHERE org_id = "
              "ANY($1::uuid[])", FIXTURE_ORGS))
    todos = "\n".join(r["d"] for r in await platform(
        conn, "SELECT COALESCE(detail, '') AS d FROM member_todos WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    leaks = {k: leaks_in(v) for k, v in (("responses", allb), ("audit", audit), ("firings", fires), ("runs", runs),
                                         ("todos", todos))}
    check(bool(bool(BODIES) and bool(audit) and bool(fires) and bool(runs) and not any(leaks.values())),
          f"no full account number (seeded in custodian_account_id) and no account hash in ANY of the {len(BODIES)} "
          "responses, the audit rows, the firing records, the run contexts / results or the alerts — last four only",
          f"{leaks}")


async def y_screens(conn):
    section("[Y] Screens — node render tests (list, editor per type with preview, run history, member read-only)")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(bool(c1 == 0 and n is not None and p is not None and int(n.group(1)) >= 8 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1),
          f"the cash06 render tests ({NODE_TEST}, counted by their file: list, editor per type, the editor's preview, "
          f"the request bodies + refusals, detail + run history, member read-only, fail closed + the Treasury mount) "
          f"all pass ({n.group(1) if n else '?'})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    for label in ("list", "editor per type: distribute", "editor per type: maintain target", "editor preview",
                  "detail + run history", "member:", "fails closed"):
        check(re.search(rf"^ok \d+ - {re.escape(label)}", o1, re.M) is not None, f"render test present and passing: "
              f"{label!r}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py / visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=600)
        check(bool(code == 0 and "CURRENT" in out), f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")
    self_static(check, find)


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(bool(code == 0 and re.search(r"^# fail 0", out, re.M) is not None), "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


#: the nested regressions, ONCE each, flat, after teardown: cash07 (the payments a rule drafts) and s29b (the autonomy
#: decision a rule's firing goes through)
REGRESSIONS = ("verify_cash07.py", "verify_s29b.py")


def regression():
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    for script in REGRESSIONS:
        section(f"[Y] Regression — {script} ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=2700, env=env)
        t = VT.nested_total(out, code)
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(bool(t.ok) and not fails), f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
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


FIXTURE_SECTIONS = (("validation", y_validation), ("governance (the floor)", y_governance), ("engine", y_engine),
                    ("instructions (nothing sent)", y_instructions), ("visibility + isolation", y_visibility))


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
        await run("evaluator", y_evaluator, conn)
        await run("schema", y_schema, conn)
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            for name, fn in FIXTURE_SECTIONS:
                await run(name, fn, conn)
        else:
            for name, _fn in FIXTURE_SECTIONS:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        await run("screens", y_screens, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(bool(NETWORK_CALLS == [] and MODEL_CALLS == [] and SEND_CALLS == []),
              "no network call, no model call and no adapter send during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]} {SEND_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows unchanged")
        PS.set_clock(None)
        CR.set_clock(None)
        WA.set_clock(None)
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (rules, firings, triggers, runs, instructions, payment_events, "
                  "tasks, sod_decisions, autonomy settings, events, audit rows, …)", f"{left}")
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
