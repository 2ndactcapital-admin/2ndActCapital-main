"""verify_s30 — SEGREGATION OF DUTIES: one duty model, one maker-checker rule (services.sod), every approval site on it.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_s30.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a seeding exception included, with the
traceback) and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are
hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network (the httpx transport refuses and records), NO model call (the three AI helpers refuse and record). REAL: the
app through an ASGI transport, services.sod / services.sod_admin, services.agent_proposals, services.human_tasks, the
workflow engine and scheduler fire path, the guard triggers, RLS and the live database. The script's own connection does
not bypass RLS (rolbypassrls = false, asserted).

FIXTURES: two orgs 99000000-0000-0000-0000-0000c5300001 / 0002 (is_test = true, asserted); users seeded with real
user_roles grants — the app's decision on each asserted:
  org A  U_A1 admin (TWO logins: a user_identities row; plus two RETIRED rows of the same person — one linked by the
              operator path (user_person_links), one by the duplicate-merge rule), U_A2 admin, U_A3 reviewer,
              U_AV viewer (view_audit_log only), U_SA1 / U_SA2 Super Admins
  org B  U_B1 the ONE holder of review_agent_proposals (self-approval), U_B2 a settings admin + compliance (no review)
Reserved prefixes (the id range, the sign-in tag 's30verify', role names 's30_*', the household id) are checked absent
from real rows first. The live platform catalog is only READ, except one fixture platform DRAFT that a second fixture
Super Admin REJECTS (never approves) — so no real rule ever changes (fingerprinted).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN (sod.teardown = 'fixture' — the guard
triggers' documented path), keyed by this run's fixture ids (the fixture platform draft by its fixture drafter); every
touched table re-read for its exact pre-test count, counted BY FIXTURE IDS ONLY (live activity writes audit_log,
sod_decisions, member_todos and the workflow tables).

--self-checks-only: git / route / static self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and
the leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
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
from datetime import datetime, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "s30"
BEFORE_REF = "46adaee"   # s30-work's base: 'merge wftemplates1'
_R = "any(manage_org_settings|manage_compliance|view_audit_log)"
_W = "manage_org_settings"
ROUTES = {
    ("GET", "/api/v1/admin/sod"): _R,
    ("GET", "/api/v1/admin/sod/reports/{name}"): _R,
    ("POST", "/api/v1/admin/sod/rules"): _W,
    ("POST", "/api/v1/admin/sod/rules/{rule_id}/decide"): _W,
}
OWN_FILES = ("apps/api/services/sod.py", "apps/api/services/sod_admin.py", "apps/api/routers/sod.py")
NODE_TEST = "tests/s30/s30.test.mjs"
DISCOVERY = REPO / "docs" / "S30_DISCOVERY.md"

#: Task 1a — every enforcement site: (its site label, the file that carries it). Each must call services.sod with
#: that label (static) and appear in the discovery table with its "migrated" cell ticked.
SITES = {
    "human_tasks.complete_task": "services/human_tasks.py",
    "workflow_engine.complete_user_task": "services/workflow_engine.py",
    "agent_proposals.review_proposal:": "services/agent_proposals.py",
    "agent_proposals.eligibility": "services/agent_proposals.py",
    "account_models.decide": "services/account_models.py",
    "account_models._apply_outcome": "services/account_models.py",
    "authority.decide": "services/authority.py",
    "authority._apply": "services/authority.py",
    "lot_method.decide": "services/lot_method.py",
    "lot_method._apply": "services/lot_method.py",
    "blocks._decide_change": "services/blocks.py",
    "blocks._apply_policy": "services/blocks.py",
    "blocks._apply_correction": "services/blocks.py",
    "blocks.decide": "services/blocks.py",
    "blocks._apply_block_outcome": "services/blocks.py",
    "execution.decide": "services/execution.py",
    "execution._apply_outcome": "services/execution.py",
    "execution._verify_upstream": "services/execution.py",
    "gifts.decide": "services/gifts.py",
    "gifts._apply": "services/gifts.py",
    "tilts.decide": "services/tilts.py",
    "tilts._apply": "services/tilts.py",
    "section351.decide": "services/section351.py",
    "section351._apply": "services/section351.py",
    "model_provider._publish_precheck": "services/model_provider.py",
    "model_provider.decide_publish": "services/model_provider.py",
    "model_provider._sub_precheck": "services/model_provider.py",
    "model_provider.decide_subscription": "services/model_provider.py",
    "models.decide": "services/models.py",
    "models._apply_outcome": "services/models.py",
    "note_replication.decide": "services/note_replication.py",
    "note_replication._apply": "services/note_replication.py",
    "options_sleeve.decide_package": "services/options_sleeve.py",
    "options_sleeve._apply_decision": "services/options_sleeve.py",
    "rebalance_proposals.decide": "services/rebalance_proposals.py",
    "rebalance_proposals._apply_outcome": "services/rebalance_proposals.py",
    "long_short.decide_margin_agreement": "services/long_short.py",
    "long_short._apply_margin_outcome": "services/long_short.py",
    "held_away.decide_import": "services/held_away.py",
    "held_away._apply_import": "services/held_away.py",
    "integrated_import.decide": "services/integrated_import.py",
    "integrated_import._apply": "services/integrated_import.py",
    "hypothetical_policy.decide": "services/hypothetical_policy.py",
    "hypothetical_policy._apply": "services/hypothetical_policy.py",
    "substitutions.decide": "services/substitutions.py",
    "substitutions._set_outcome": "services/substitutions.py",
    "substitutions._deny_outcome": "services/substitutions.py",
    "substitutions.retire_set": "services/substitutions.py",
    "substitutions.retire_denial": "services/substitutions.py",
    "tax_profiles.decide": "services/tax_profiles.py",
    "tax_profiles._apply_outcome": "services/tax_profiles.py",
    "replica_terms.decide": "services/replica_terms.py",
    "replica_terms._apply_terms_decision": "services/replica_terms.py",
    "workflow_templates.decide_platform_version": "services/workflow_templates.py",
    "disclosures.publish": "services/disclosures.py",
    "security_data.uploads.decide": "services/security_data/uploads.py",
    "fee_runs.approve": "services/fee_runs.py",
    "spv_carry_runs.approve": "services/spv_carry_runs.py",
    "trading_authority.approve_money_movement": "services/trading_authority.py",
    "sod_admin.decide_rule": "services/sod_admin.py",
    "role_grant:": "services/sod_admin.py",
}
#: Task 1a — the screens' Approve offers, each now through sod.may_check_display (count of calls per file at least).
DISPLAY_FILES = {
    "services/human_tasks.py": 2, "services/authority.py": 1, "services/lot_method.py": 1, "services/blocks.py": 2,
    "services/execution.py": 1, "services/gifts.py": 1, "services/tilts.py": 1, "services/section351.py": 1,
    "services/note_replication.py": 1, "services/options_sleeve.py": 1, "services/rebalance_proposals.py": 1,
    "services/held_away.py": 1, "services/substitutions.py": 4, "services/tax_profiles.py": 1,
    "services/workflow_templates.py": 1, "services/bulk_approvals.py": 1, "routers/account_models.py": 2,
    "routers/blocks.py": 2, "routers/execution.py": 1, "routers/models.py": 2, "routers/long_short.py": 1,
    "routers/portentry.py": 1, "routers/replication_rep10.py": 1, "routers/replication.py": 1,
    "routers/disclosures.py": 1, "routers/security_data.py": 1,
}
#: a raw maker-vs-actor comparison left in a service is a site keeping its own rule — except these, each a
#: different rule (book-keeping a maker list, the REVERSE rule "only the maker submits / acknowledges", serialising)
RAW_COMPARE = re.compile(
    r'(maker_id|maker_ids|entered_by|drafted_by|requested_by|uploaded_by|proposed_by|submitted_by|version_maker_ids)'
    r'"\]\)?( or \[\]\))?\)?\s*(==|!=)|(==|!=)\s*(str\()?\w+\["(maker_id|entered_by|drafted_by|requested_by|'
    r'uploaded_by|proposed_by)"\]|\bin\s+\(?(set\()?\(?\w+\["(maker_ids|version_maker_ids)"\]|not in (makers|\(\w+\["maker_id"\])')
RAW_ALLOWED = (
    (re.compile(r'not in makers:\s*$'), "adds the actor to the item's own maker list (book-keeping, not a check)"),
    (re.compile(r'maker_kind'), "the REVERSE rule: only the maker submits"),
    (re.compile(r'acknowledged_by'), "the REVERSE rule: only the maker acknowledges"),
    (re.compile(r'upstream\.get'), "an upstream consistency check (the proposal's maker/checker equal the upstream's)"),
    (re.compile(r'"maker_ids": \['), "serialising the maker list"),
    (re.compile(r'makers = (sorted|\[)'), "building the maker list"),
    (re.compile(r'makers_add'), "building the maker list"),
    (re.compile(r'recipients = '), "a notification recipient list"),
    (re.compile(r'is_maker = p\["maker_id"\] is not None and str\(p\["maker_id"\]\) == principal\.user_id'),
     "routers/long_short.py: the REVERSE rule's display (only the maker acknowledges)"),
)


def self_routes(check, find):
    """s30's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed,
          "s30 changed or removed no existing route's gate", f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at s30's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "s30's routes all still exist today with the same gate", f"{drift[:4]}")


def _src(rel: str) -> str:
    p = API_DIR / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def self_static(check, find):
    """Task 4 statically: every 1a site calls services.sod with its own label; every display site uses
    sod.may_check_display; no site keeps a raw maker comparison of its own; the service has no model / network."""
    missing = []
    for label, rel in SITES.items():
        src = _src(rel)
        hit = [m.start() for m in re.finditer(re.escape(f'site="{label}') if not label.endswith(":")
                                              else re.escape(f'site=f"{label}'), src)]
        hit += [m.start() for m in re.finditer(re.escape(f'"{label}'), src)] if label.endswith(":") else []
        near = any(re.search(r"sod(_admin)?\.(gate|enforce|can_check|load|check_role_grant)\(|ctx\.evaluate\(",
                             src[max(0, h - 900):h + 900]) for h in hit)
        if not (hit and near):
            missing.append(label)
    check(bool(SITES) and not missing, f"every one of the {len(SITES)} enforcement sites calls services.sod with its own "
          "site label (Task 4: no site keeps its own rule)", f"missing={missing[:6]}")
    thin = {rel: (len(re.findall(r"(?<!def )\bmay_check_display\(", _src(rel))), n) for rel, n in DISPLAY_FILES.items()}
    thin = {k: v for k, v in thin.items() if v[0] < v[1]}
    check(not thin, f"every display site ({sum(DISPLAY_FILES.values())} in {len(DISPLAY_FILES)} files) offers Approve "
          "through sod.may_check_display", f"{thin}")
    culprits = []
    for base in ("services", "routers"):
        for p in sorted((API_DIR / base).rglob("*.py")):
            rel = str(p.relative_to(API_DIR))
            if rel in ("services/sod.py", "services/sod_admin.py", "services/maker_names.py"):
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
                if line.lstrip().startswith("#") or not RAW_COMPARE.search(line):
                    continue
                if any(rx.search(line) for rx, _why in RAW_ALLOWED):
                    continue
                culprits.append(f"{rel}:{i}: {line.strip()[:120]}")
    check(not culprits, "no service or router compares a maker with the actor on its own (the allowed exceptions are "
          "a different rule each: maker-list book-keeping, the reverse 'only the maker submits' rule, serialising)",
          "; ".join(culprits[:5]))
    for f in OWN_FILES:
        src = (REPO / f).read_text(encoding="utf-8") if (REPO / f).exists() else ""
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    sod_src = _src("services/sod.py")
    check("LOAD_SQL" in sod_src and sod_src.count("await conn.fetchrow(LOAD_SQL") == 1
          and "def evaluate(" in sod_src and "now()" not in sod_src.split("def evaluate(")[1].split("def _org_rule_hit")[0],
          "services.sod: ONE read (LOAD_SQL) then pure evaluation (no clock inside evaluate)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
from services import agent_proposals as AP  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import sod_admin as SA  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_engine as ENG  # noqa: E402
from services import workflow_nl_generator as G  # noqa: E402
from services import workflow_scheduler as SCHED  # noqa: E402
from services.action_registry import REGISTRY, AssistantAction  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.workflow_steps_deriver import derive_and_store_steps  # noqa: E402

TAG = "s30verify"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c530"


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_A1, U_A2, U_A3, U_AV, U_SA1, U_SA2, U_B1, U_B2 = (_id(10 + i) for i in range(8))
U_A1R, U_A1D = _id(30), _id(31)      # two RETIRED rows of U_A1's person (operator link; duplicate merge)
HOUSEHOLD = _id(50)                  # a household id (sod_decisions.household_id carries no FK)
PSET = _id(60)
DEF_T1, VER_T1, TRIG_T1 = _id(70), _id(71), _id(72)
FIXTURE_VERB = "s30verify.tier1_write"
UNDECLARED_VERB = "s30verify.undeclared_write"
HT_KIND = "s30verify_fixture_approval"
ADMIN_PERMS = {"manage_org_settings", "manage_members", "manage_authority", "approve_authority",
               "review_agent_proposals", "view_audit_log", "manage_compliance", "author_workflows",
               "configure_workflow_triggers", "view_workflow_runs"}
ROLE_PERMS = {
    "s30_admin": ADMIN_PERMS,
    "s30_reviewer": {"review_agent_proposals", "view_workflow_runs"},
    "s30_viewer": {"view_audit_log"},
    "s30_settings": {"manage_org_settings", "manage_members", "manage_compliance"},
}
# uid → (org, users.role mirror, short, role granted, full name)
USERS = {
    U_A1: (ORG_A, "member", "a1", "s30_admin", "S30 Pat Two-Logins"),
    U_A2: (ORG_A, "member", "a2", "s30_admin", "S30 Admin Two"),
    U_A3: (ORG_A, "member", "a3", "s30_reviewer", "S30 Reviewer"),
    U_AV: (ORG_A, "member", "av", "s30_viewer", "S30 Viewer"),
    U_SA1: (ORG_A, "super_admin", "sa1", "super_admin", "S30 Super Admin One"),
    U_SA2: (ORG_A, "super_admin", "sa2", "super_admin", "S30 Super Admin Two"),
    U_B1: (ORG_B, "member", "b1", "s30_admin", "S30 Solo Adviser"),
    U_B2: (ORG_B, "member", "b2", "s30_settings", "S30 Settings Admin"),
}
RETIRED = {U_A1R: "a1r", U_A1D: "a1d"}
ALL_USERS = [*USERS, *RETIRED]
SECOND_SUB = f"auth0|{TAG}-a1-second-login"
COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.user_identities", "public.user_person_links", "public.permission_sets", "public.user_permission_sets",
    "public.sod_rules", "public.sod_decisions", "public.audit_log", "public.member_todos", "public.agent_proposals",
    "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps", "public.workflow_runs",
    "public.workflow_run_steps", "public.workflow_triggers",
)
#: catalog literals the seeds and the API bodies rely on — read back from the CHECK constraints before seeding
SEED_CHECK_LITERALS = (
    ("public.sod_rules", "sod_rules_kind_chk", ("floor", "default", "policy", "org")),
    ("public.sod_rules", "sod_rules_scope_chk", ("same_item", "same_household", "same_period")),
    ("public.sod_rules", "sod_rules_status_chk", ("draft", "active", "rejected", "retired")),
    ("public.sod_rules", "sod_rules_severity_chk", ("block", "warn")),
    ("public.sod_rules", "sod_rules_selfapproval_chk", ("never", "when_no_other_checker")),
    ("public.sod_decisions", "sod_decisions_outcome_chk", ("allowed", "refused", "self_approved")),
    ("public.user_person_links", "user_person_links_via_chk", ("operator", "duplicate_merge")),
)
UNIQUES = ("sod_rules_active_uq", "sod_rules_draft_uq", "sod_rules_version_uq", "users_auth0_sub_key",
           "user_identities_auth0_sub_key", "user_person_links_pkey")
#: Task 4 — the database CHECK constraints that stay as the last line (docs/S30_DISCOVERY.md 1a)
DB_CHECKS = (
    "agent_proposals_maker_checker_chk", "workflow_run_steps_maker_checker", "assistant_activities_maker_checker_chk",
    "account_authority_settings_maker_checker_chk", "household_authority_maker_checker_chk",
    "account_lot_method_settings_maker_checker_chk", "household_lot_method_maker_checker_chk",
    "account_model_links_maker_checker_chk", "allocation_corrections_maker_checker_chk",
    "allocation_policies_maker_checker_chk", "disclosure_versions_maker_checker_chk", "gift_proposals_maker_checker_chk",
    "held_away_imports_maker_checker_chk", "hypothetical_policies_maker_checker_chk",
    "loss_carryforwards_maker_checker_chk", "rebalance_proposals_maker_checker_chk",
    "section351_plans_maker_checker_chk", "substitution_denials_maker_checker_chk",
    "substitution_sets_maker_checker_chk", "suitability_records_maker_checker_chk", "tax_profiles_maker_checker_chk",
    "tilt_sets_maker_checker_chk", "wtv_maker_checker_chk", "catalog_versions_maker_checker_chk",
    "data_uploads_maker_checker_chk", "provider_subscriptions_maker_checker_chk", "provisional_imports_checker_chk",
    "sod_rules_maker_checker_chk",
)

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
CALLS: dict[str, int] = {}
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


def email_of(uid) -> str:
    short = USERS[uid][2] if uid in USERS else RETIRED[uid]
    return f"{TAG}-{short}@test.local"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)[:700]
    except Exception:  # noqa: BLE001
        return str(o)[:700]


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


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS), _arr(ALL_USERS)
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.organizations": f"id = ANY({orgs})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.user_permission_sets": f"user_id = ANY({users})",
        "public.sod_rules": f"org_id = ANY({orgs}) OR drafted_by = ANY({users}) OR checker_id = ANY({users})",
        "public.sod_decisions": f"org_id = ANY({orgs}) OR checker_id = ANY({users})",
        "public.user_person_links": f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR person_user_id = ANY({users})",
    }.get(table, by_org)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
            for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint's verify must never change: the platform catalog (duties, item types, every platform rule
    not drafted by a fixture user), every real org's own rules, every real person link, and real role grants."""
    users = [str(u) for u in ALL_USERS]
    orgs = [str(o) for o in FIXTURE_ORGS]
    return {
        "sod_duties": await fingerprint(conn, "SELECT * FROM public.sod_duties"),
        "sod_item_types": await fingerprint(conn, "SELECT * FROM public.sod_item_types"),
        "platform_rules": await fingerprint(
            conn, "SELECT * FROM public.sod_rules WHERE org_id IS NULL AND NOT (COALESCE(drafted_by::text, '') = "
                  "ANY($1::text[]))", users),
        "real_org_rules": await fingerprint(
            conn, "SELECT * FROM public.sod_rules WHERE org_id IS NOT NULL AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real_person_links": await fingerprint(
            conn, "SELECT * FROM public.user_person_links WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "real_role_grants": await fingerprint(
            conn, "SELECT ur.* FROM public.user_roles ur JOIN public.roles r ON r.id = ur.role_id "
                  "WHERE NOT (r.org_id = ANY($1::uuid[]))", orgs),
    }


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
    """Child-first by the shared helper (sod.teardown = 'fixture'), keyed by this run's fixture ids; the platform DRAFT
    a fixture Super Admin wrote (org_id NULL) is planned by its fixture drafter."""
    op = await operator_conn()
    try:
        users = list(ALL_USERS)
        plan = {
            "public.sod_rules": ("org_id = ANY($1::uuid[]) OR drafted_by = ANY($2::uuid[]) OR checker_id = "
                                 "ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.sod_decisions": ("org_id = ANY($1::uuid[]) OR checker_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.user_person_links": ("user_id = ANY($1::uuid[]) OR person_user_id = ANY($1::uuid[])", [users]),
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.user_permission_sets": ("user_id = ANY($1::uuid[])", [users]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("sod.teardown",), label="verify_s30")
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
    users = list(ALL_USERS)
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [users, f"%{TAG}%"]),
        ("SELECT count(*) FROM user_identities WHERE auth0_sub LIKE $1", [f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM public.sod_rules WHERE org_id = ANY($1::uuid[]) OR drafted_by = ANY($2::uuid[]) "
         "OR checker_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        ("SELECT count(*) FROM public.sod_decisions WHERE org_id = ANY($1::uuid[]) OR checker_id = ANY($2::uuid[])",
         [FIXTURE_ORGS, users]),
        ("SELECT count(*) FROM public.user_person_links WHERE user_id = ANY($1::uuid[]) OR person_user_id = "
         "ANY($1::uuid[])", [users]),
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


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid, sub: str | None = None) -> str:
    c = {"sub": sub or sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, params=None, sub=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid, sub)}", "User-Agent": "verify_s30"}}
    if body is not None:
        kw["json"] = body
    if params:
        kw["params"] = params
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}, ""
    raw = res.content.decode("utf-8", "replace")
    try:
        return res.status_code, json.loads(raw or "{}"), res.headers.get("content-type", "")
    except ValueError:
        return res.status_code, {"raw": raw[:4000]}, res.headers.get("content-type", "")


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


def _make_handler(key: str):
    async def _handler(*, pool, user_id, org_id, run_context, workflow_run_id, **_):
        CALLS[key] = CALLS.get(key, 0) + 1
        return {"data": {"called_for": key, "count": CALLS[key]}, "text": None}
    return _handler


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()
    register_all()   # ASGITransport skips the app lifespan, where the registry is normally filled
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_s30: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_s30: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    G.call_claude_text = refuse_model("workflow_nl_generator.call_claude_text")

    # the fixture verbs: one Tier-1 write verb WITH a declared duty (invocable — it runs once on approval, counting),
    # one write verb with NONE (refused at validation). Never synced to any real catalog.
    async def _hermetic_sync_catalog(pool, org_id):
        return None

    REGISTRY.sync_catalog = _hermetic_sync_catalog
    have = {f for f in AssistantAction.__dataclass_fields__}
    if "duty" not in have:
        raise RuntimeError("AssistantAction has no 'duty' field (S30 Task 8)")
    REGISTRY.register(AssistantAction(
        key=FIXTURE_VERB, module="s30verify", description="verify_s30 fixture — counts, no side effect",
        access_type="write", required_permission=None, tier=1, reversible=False, render_target="inline",
        handler=_make_handler(FIXTURE_VERB), workflow_invocable=True, duty="write"))
    REGISTRY.register(AssistantAction(
        key=UNDECLARED_VERB, module="s30verify", description="verify_s30 fixture — a write verb with no duty",
        access_type="write", required_permission=None, tier=2, reversible=False, render_target="inline",
        handler=_make_handler(UNDECLARED_VERB)))
    HT.register_kind(HT_KIND, label="S30 verify approval", outcomes=("approve", "reject"), maker_checker=True)


def remove_fixture_verbs():
    for k in (FIXTURE_VERB, UNDECLARED_VERB):
        REGISTRY._actions.pop(k, None)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 inventory 1a–1d (docs/S30_DISCOVERY.md): every site listed, every row migrated or explained")
    txt = DISCOVERY.read_text(encoding="utf-8") if DISCOVERY.exists() else ""
    heads = set(re.findall(r"^## (1[a-d])\.", txt, re.M))
    check(heads >= {"1a", "1b", "1c", "1d"}, "the discovery doc has a section for each of 1a–1d", f"{sorted(heads)}")
    rows = [r for r in re.findall(r"^\| `([^`]+)` \|(.*)\|\s*$", txt, re.M)]
    labels = {lbl for lbl, _rest in rows}
    unlisted = [lbl for lbl in SITES if not any(x == lbl or x.startswith(lbl) for x in labels)]
    check(bool(rows) and not unlisted, f"every one of the {len(SITES)} enforcement sites is a row of the 1a table",
          f"unlisted={unlisted[:6]}")
    unticked = [lbl for lbl, rest in rows if not rest.rstrip().split("|")[-1].strip().startswith("✓")]
    check(bool(rows) and not unticked, "every 1a row's 'migrated' cell is ticked (✓ …)", f"{unticked[:6]}")
    check("Not migrated, each with its reason" in txt and "REVERSE rule" in txt,
          "the display sites not migrated are each explained (notification list, reverse rule, permission-only)")
    # 1c — the live identity facts the person test rests on (read, not frozen)
    retired = await platform(conn, "SELECT count(*) FROM users WHERE status = 'retired' AND NOT (id = ANY($1::uuid[]))",
                             list(ALL_USERS), fetch="fetchval")
    find("1c retired users rows today (each resolved through user_person_links or the duplicate-merge rule, or "
         "unknowable)", f"{retired}")
    # 1d — trigger owners
    rows = await platform(conn, "SELECT trigger_type, count(*) FILTER (WHERE created_by IS NULL) AS no_owner, "
                                "count(*) AS n FROM workflow_triggers WHERE NOT (org_id = ANY($1::uuid[])) GROUP BY 1",
                          FIXTURE_ORGS)
    find("1d live triggers without a named owner (their runs have no trigger owner to exclude)",
         f"{[dict(r) for r in rows]}")


async def y_schema(conn):
    section("[Y] Schema landed: duty model, versioned rules, decisions, person links; RLS for every operation; the "
            "connection does not bypass RLS")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_s30.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_s30.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-300:] if code else ''}")
    for t in ("sod_duties", "sod_item_types", "sod_rules", "sod_decisions", "user_person_links"):
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                       "tablename = $1", t)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"public.{t}: a policy for every operation",
              f"{sorted(cmds)}")
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role is not None and role["rolbypassrls"] is False,
          "the verify's own connection does NOT bypass RLS (isolation checks are real)", f"{dict(role or {})}")


async def y_catalog(conn):
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
                                                       "ANY($1::text[]) AND indexdef LIKE 'CREATE UNIQUE%'", list(UNIQUES))}
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype::text = 'f' AND conrelid = ANY("
                                   "ARRAY['public.sod_rules', 'public.sod_decisions', 'public.sod_item_types', "
                                   "'public.user_person_links']::regclass[])", fetch="fetchval"))
    check(len(SEED_CHECK_LITERALS) > 0 and not bad and uq == set(UNIQUES) and fks >= 8,
          f"every CHECK literal the seeds / bodies use is admitted ({len(SEED_CHECK_LITERALS)} constraints read); the "
          f"{len(UNIQUES)} UNIQUE indexes and the FKs the seeds rely on are in the catalog",
          "; ".join(bad) + f" uq_missing={sorted(set(UNIQUES) - uq)} fks={fks}")
    have = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype::text = 'c' "
                                                       "AND conname = ANY($1::text[])", list(DB_CHECKS))}
    check(have == set(DB_CHECKS), f"Task 4: the {len(DB_CHECKS)} maker-checker DB CHECK constraints are all still "
          "present (defence in depth — the service decides, the database is the last line)",
          f"missing={sorted(set(DB_CHECKS) - have)}")
    fk_users = await platform(conn, "SELECT conname FROM pg_constraint WHERE conrelid = 'public.sod_decisions'::regclass "
                                    "AND contype::text = 'f' AND confrelid = 'public.users'::regclass", fetch="fetch")
    check(not fk_users, "sod_decisions carries NO foreign key to users (a teardown never follows a fixture user into "
          "another org's decision log)", f"{[r['conname'] for r in fk_users]}")
    S["literals_ok"] = not bad and uq == set(UNIQUES)


async def y_model(conn):
    section("[Y] Duty model: every item type a catalog row, its maker / checker duty, a floor or default rule and a "
            "policy; seeded EQUAL to the pre-S30 rules")
    types = {r["item_type"]: dict(r) for r in await platform(conn, "SELECT * FROM public.sod_item_types")}
    check(set(sod.ITEM_TYPES) == set(types), "services.sod.ITEM_TYPES and public.sod_item_types are the same set",
          f"code-only={sorted(set(sod.ITEM_TYPES) - set(types))} db-only={sorted(set(types) - set(sod.ITEM_TYPES))}")
    kinds = {k for k, v in HT.kinds().items() if v.maker_checker and k != HT_KIND}
    unmapped = sorted(k for k in kinds if k not in sod.TASK_KIND_ITEM_TYPES)
    check(bool(kinds) and not unmapped, f"every maker-checker human-task kind ({len(kinds)}) maps to an item type",
          f"{unmapped}")
    rules = [dict(r) for r in await platform(
        conn, "SELECT rule_key, rule_kind, item_type, self_approval, enforce_floor FROM public.sod_rules WHERE org_id "
              "IS NULL AND status = 'active' AND valid_to IS NULL")]
    by = {}
    for r in rules:
        by.setdefault(r["item_type"], []).append(r)
    bad = []
    for t, row in types.items():
        kinds_here = {r["rule_kind"] for r in by.get(t, [])}
        want = {"floor" if row["floor_class"] else "default", "policy"}
        pol = next((r for r in by.get(t, []) if r["rule_kind"] == "policy"), None)
        want_sa = "when_no_other_checker" if t in (sod.AGENT_PROPOSAL, sod.WORKFLOW_STEP) else "never"
        want_ef = row["pre_s30_rule"] == "maker_ne_checker"
        if kinds_here != want or pol is None or pol["self_approval"] != want_sa or pol["enforce_floor"] is not want_ef:
            bad.append((t, sorted(kinds_here), pol and (pol["self_approval"], pol["enforce_floor"])))
    check(not bad, "each item type: exactly one floor (floor class) or default rule + one platform policy; self-approval "
          "'when_no_other_checker' ONLY for agent_proposal / workflow_step; enforce_floor true wherever the pre-S30 rule "
          "refused the maker, false only for role grants (no rule existed)", f"{bad[:5]}")
    floors = sorted(t for t, r in types.items() if r["floor_class"])
    check({"trade_block", "money_movement", "disclosure_version", "role_grant"} <= set(floors)
          and all(not types[t]["self_approval_capable"] for t in floors),
          "the floor classes cover own trade, own transfer, own disclosure, own role grant; no floor item type can carry "
          "a self-approval", f"{floors}")
    duties = {r["duty_key"] for r in await platform(conn, "SELECT duty_key FROM public.sod_duties")}
    check(all(r["maker_duty"] in duties and r["checker_duty"] in duties for r in types.values()),
          "every item type's maker and checker duty is a sod_duties row")


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    i = int(await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub LIKE $1", f"%{TAG}%",
                           fetch="fetchval"))
    r = int(await platform(conn, "SELECT count(*) FROM roles WHERE name LIKE 's30\\_%'", fetch="fetchval"))
    d = int(await platform(conn, "SELECT count(*) FROM sod_decisions WHERE household_id::text LIKE $1 OR item_id LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    return check(o == 0 and u == 0 and i == 0 and r == 0 and d == 0,
                 "the reserved prefixes (fixture id range, sign-in tag, role names 's30_*', household / item ids) match "
                 "no real row", f"orgs={o} users={u} identities={i} roles={r} decisions={d}")


async def check_fixture_roles(conn) -> bool:
    """Each fixture user's role AS THE APP DECIDES IT (user_roles → services.authz Principal)."""
    from services import authz as _AZ

    bad = []
    keys = set().union(*ROLE_PERMS.values())
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, account_role, _nm, rname, _full) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            pr = await _AZ.load_principal(conn, uid)
            want_super = rname == "super_admin"
            perms = set(getattr(pr, "permissions", ()) or ())
            ok = rname in names and pr is not None and pr.admitted and bool(pr.is_super_admin) is want_super
            if not want_super:
                ok = ok and (perms & keys) == ROLE_PERMS[rname]
            if not ok:
                bad.append((str(uid), names, bool(pr and pr.is_super_admin), sorted(perms & keys)[:12]))
        for uid in RETIRED:
            pr = await _AZ.load_principal(conn, uid)
            if pr is not None and pr.admitted:
                bad.append((str(uid), "a retired row is admitted"))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (two Super Admins; the "
                 "admins / reviewer / viewer / settings admin hold exactly their keys; the retired rows hold nothing)",
                 f"{bad}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"S30 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*ROLE_PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in ROLE_PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"S30 verify — {rname}")
                for p in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        for uid, (org, account_role, _nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        # U_A1's SECOND login (one person, two logins)
        await conn.execute("INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via) "
                           "VALUES ($1, $2, $3, 'https://verify.invalid/', $4, 'operator')",
                           U_A1, ORG_A, SECOND_SUB, f"{TAG}-a1-second@test.local")
        # two RETIRED rows of the same person: one operator-linked (sub moved away), one duplicate-merged (keeps the sub
        # that is now U_A1's linked identity) — rows that once made items, as the live 3320ed63… / 80f1744f… did
        await conn.execute(
            "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
            "VALUES ($1, $2, $3, 'S30 retired placeholder', NULL, 'member', 'retired', false, true)",
            U_A1R, ORG_A, email_of(U_A1R))
        await conn.execute(
            "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
            "VALUES ($1, $2, $3, 'S30 retired duplicate', $4, 'member', 'retired', false, true)",
            U_A1D, ORG_A, email_of(U_A1D), SECOND_SUB)
        await conn.execute("INSERT INTO user_person_links (user_id, person_user_id, org_id, linked_via) "
                           "VALUES ($1, $2, $3, 'operator')", U_A1R, U_A1, ORG_A)
        await conn.execute("INSERT INTO permission_sets (id, org_id, name, description) VALUES ($1, $2, $3, $4)",
                           PSET, ORG_A, "S30 verify set", "verify_s30 fixture")
        await conn.execute("INSERT INTO permission_set_permissions (org_id, permission_set_id, permission_key) "
                           "VALUES ($1, $2, 'view_audit_log')", ORG_A, PSET)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          list(ALL_USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users (retired rows included) are "
          "is_test = true", f"{len(us)}/{len(ALL_USERS)} users, {len(os_)}/2 orgs")
    S["seeded"] = await check_fixture_roles(conn) and us == set(ALL_USERS) and os_ == set(FIXTURE_ORGS)


# ── pure replay of the rules in force BEFORE S30 (Task 7) ───────────────────────────────────────────────────────
def pre_s30(item: dict, checker, makers, other_exists) -> str:
    """What the site decided before S30: login ids; maker refused everywhere (role grants: no rule); self-approval only
    on agent_proposal / workflow_step when no other eligible checker."""
    same = str(checker) in [str(m) for m in makers if m is not None]
    if item["pre_s30_rule"] == "none":
        return sod.ALLOWED
    if not same:
        return sod.ALLOWED
    if item["item_type"] in (sod.AGENT_PROPOSAL, sod.WORKFLOW_STEP) and other_exists is False:
        return sod.SELF_APPROVAL_ALLOWED
    return sod.REFUSED


async def y_replay(conn):
    section("[Y] Task 7 — today's decisions replayed exactly per item type through services.sod (no silent tightening)")
    pool = await get_pool()
    cases = [("the maker checks", U_B2, [U_B2]), ("another person checks", U_B2, [U_B1]),
             ("a second maker checks", U_B2, [U_B1, U_B2]), ("no human maker", U_B2, [None])]
    mismatches = []
    n = 0
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            for t in sod.ITEM_TYPES:
                ctx = await sod.load(c, org_id=ORG_B, item_type=t, ids=[U_B1, U_B2])
                if ctx.item is None:
                    mismatches.append((t, "no catalog row"))
                    continue
                for label, chk, makers in cases:
                    for other in ((True, False) if ctx.item and ctx.item["self_approval_capable"] else (True,)):
                        want = pre_s30(ctx.item, chk, makers, other)
                        got = ctx.evaluate(checker_id=chk, maker_ids=makers, other_checker_exists=other).outcome
                        n += 1
                        if got != want:
                            mismatches.append((t, label, other, want, got))
    check(n > len(sod.ITEM_TYPES) * 3 and not mismatches,
          f"{n} replayed decisions across all {len(sod.ITEM_TYPES)} item types equal the pre-S30 rule under the seeded "
          "(platform default) policy", f"{mismatches[:6]}")
    # live: would the person test change any live decision? (maker columns referencing a retired / linked row)
    op = await operator_conn()
    try:
        cols = await op.fetch(
            "SELECT n.nspname s, cl.relname t, a.attname col FROM pg_constraint con JOIN pg_class cl ON "
            "cl.oid = con.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace JOIN pg_attribute a ON a.attrelid = "
            "con.conrelid AND a.attnum = ANY(con.conkey) WHERE con.contype = 'f' AND con.confrelid = "
            "'public.users'::regclass AND a.attname ~ '(maker|entered_by|proposed_by|drafted_by|requested_by|"
            "submitted_by|uploaded_by|started_by)'")
        hits = []
        for r in cols:
            k = await op.fetchval(
                f'SELECT count(*) FROM "{r["s"]}"."{r["t"]}" x WHERE x."{r["col"]}" IN (SELECT id FROM users WHERE '
                f'status = \'retired\' AND NOT (id = ANY($1::uuid[]))) AND NOT (x."{r["col"]}" = ANY($1::uuid[]))',
                ALL_USERS)
            if k:
                hits.append((f'{r["s"]}.{r["t"]}.{r["col"]}', int(k)))
    finally:
        await close_conn(op)
    if hits:
        find("live rows MADE by a retired login row — the person test changes their decision (reported, not "
             "silently tightened: behind sod.enforce_floor)", f"{hits[:8]}")
    check(bool(cols), f"Task 7 live replay: {len(cols)} maker columns scanned for rows made by a retired login row "
          "(the only decisions the person test can change)", f"{len(hits)} column(s) with such rows")
    check(not hits, "no live decision changes by turning the person test on (else the default enforce_floor for that "
          "item type must be false — see the FIND)", f"{hits[:4]}")


async def y_floor(conn):
    section("[Y] Floor rules incl. Super Admin; persons, not logins")
    pool = await get_pool()
    with _ctx(ORG_A):
        async with pool.acquire() as c:
            for t in (sod.TRADE_BLOCK, sod.MONEY_MOVEMENT, sod.DISCLOSURE_VERSION, sod.REBALANCE_PROPOSAL):
                for who, uid in (("an admin", U_A2), ("a Super Admin", U_SA1)):
                    d = await sod.can_check(c, org_id=ORG_A, item_type=t, checker_id=uid, maker_ids=[uid])
                    check(d.refused and (d.rule or {}).get("rule_kind") == "floor",
                          f"floor: {who} checking their own {t} is refused by the floor rule", f"{d.as_dict()}")
                d = await sod.can_check(c, org_id=ORG_A, item_type=t, checker_id=U_SA1, maker_ids=[U_SA2])
                check(d.allowed, f"floor: {t} made by another Super Admin is allowed (two people)", d.reason)
            d1 = await sod.can_check(c, org_id=ORG_A, item_type=sod.TRADE_BLOCK, checker_id=U_A1, maker_ids=[U_A1R])
            d2 = await sod.can_check(c, org_id=ORG_A, item_type=sod.TRADE_BLOCK, checker_id=U_A1, maker_ids=[U_A1D])
            d3 = await sod.can_check(c, org_id=ORG_A, item_type=sod.TRADE_BLOCK, checker_id=U_A2, maker_ids=[U_A1R])
            check(d1.refused and d1.same_person and d1.maker_persons == [str(U_A1)],
                  "persons: a trade the person's OPERATOR-LINKED retired row made is refused to that person",
                  f"{d1.as_dict()}")
            check(d2.refused and d2.same_person and d2.maker_persons == [str(U_A1)],
                  "persons: a trade the person's DUPLICATE-MERGED retired row made is refused to that person",
                  f"{d2.as_dict()}")
            check(d3.allowed and not d3.same_person, "persons: two people are two (another admin may check it)",
                  d3.reason)
            # the pre-S30 rule (login ids) would have allowed d1 / d2 — the person test is the tightening, and it
            # changes no live decision (the replay section's live scan)
            check(pre_s30({"item_type": "trade_block", "pre_s30_rule": "maker_ne_checker"}, U_A1, [U_A1R], True)
                  == sod.ALLOWED, "reproduced: by login ids alone, the linked row's trade WOULD have been approvable "
                  "by the same person (the gap the person test closes)")
            same = await sod.same_person(c, ORG_A, U_A1D, U_A1)
            check(same is True, "sod.same_person: the duplicate-merged row and the person are one person")
    # two logins of ONE person at the API: U_A1's second login resolves to U_A1 — its self-grant is refused below
    code, body, _ = await api(U_A1, "GET", "/api/v1/admin/sod", sub=SECOND_SUB)
    check(code == 200 and body.get("viewer_id") == str(U_A1),
          "two logins of one person: the second sign-in resolves to the same person", f"{code} {body.get('viewer_id')}")


async def y_org_rules(conn):
    section("[Y] Org rules: maker-checker + history; stricter rule applied; an org cannot relax a floor (VALID body)")
    # role grants: today no rule — org A turns the floor ON (a tightening an org may make), maker-checker
    code, r1, _ = await api(U_A1, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "policy", "item_type": "role_grant", "self_approval": "never", "enforce_floor": True,
        "description": "S30 verify: nobody grants themselves a role"})
    rid = (r1.get("rule") or {}).get("id")
    check(code == 201 and (r1.get("rule") or {}).get("status") == "draft", "an org admin drafts a policy (draft)",
          f"{code} {dumps(r1)}")
    code, b, _ = await api(U_A1, "POST", f"/api/v1/admin/sod/rules/{rid}/decide", {"outcome": "approve"})
    still = await scoped(conn, ORG_A, "SELECT status FROM sod_rules WHERE id = $1::uuid", rid, fetch="fetchval")
    check(code == 403 and still == "draft", "maker-checker: the drafter cannot approve their own draft (403, still a "
          "draft)", f"{code} {dumps(b)} {still}")
    code, b, _ = await api(U_A2, "POST", f"/api/v1/admin/sod/rules/{rid}/decide", {"outcome": "approve"})
    row = await scoped(conn, ORG_A, "SELECT status, checker_id::text AS c, drafted_by::text AS d FROM sod_rules "
                                    "WHERE id = $1::uuid", rid, fetch="fetchrow")
    check(code == 200 and row is not None and row["status"] == "active" and row["c"] == str(U_A2)
          and row["d"] == str(U_A1), "a DIFFERENT admin approves it: active, checker ≠ drafter (re-read)",
          f"{code} {dumps(b)} {dict(row or {})}")
    S["role_policy_on"] = code == 200
    # relaxations, each a VALID body, refused, nothing written
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    for label, body in (
        ("turn the floor OFF for a trade block", {"rule_kind": "policy", "item_type": "trade_block",
                                                   "self_approval": "never", "enforce_floor": False}),
        ("allow self-approval of a disclosure", {"rule_kind": "policy", "item_type": "disclosure_version",
                                                 "self_approval": "when_no_other_checker", "enforce_floor": True}),
        ("allow self-approval of an authority change", {"rule_kind": "policy", "item_type": "authority_change",
                                                        "self_approval": "when_no_other_checker",
                                                        "enforce_floor": True}),
    ):
        code, b, _ = await api(U_A1, "POST", "/api/v1/admin/sod/rules", {**body, "description": f"S30 verify: {label}"})
        check(code == 403, f"an org cannot relax the floor: '{label}' (valid body) is refused 403", f"{code} {dumps(b)}")
    code, b, _ = await api(U_A1, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "floor", "item_type": "trade_block", "description": "S30 verify: an org floor"})
    check(code in (400, 403), "an org cannot write a floor rule at all", f"{code} {dumps(b)}")
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    check(n1 == n0, "no rule row was written by any refused relaxation", f"{n0} → {n1}")

    # a STRICTER org rule: no approvals inside a household by someone who made a change there in the last 30 days
    code, r2, _ = await api(U_A1, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "org", "item_type": "authority_change", "scope": "same_household", "period_days": 30,
        "severity": "block", "description": "S30 verify: a household's own change-makers do not approve its changes"})
    hid = (r2.get("rule") or {}).get("id")
    code2, _b, _ = await api(U_A2, "POST", f"/api/v1/admin/sod/rules/{hid}/decide", {"outcome": "approve"})
    check(code == 201 and code2 == 200, "the stricter household rule is drafted and approved by a second admin",
          f"{code} {code2} {dumps(r2)}")
    pool = await get_pool()
    with _ctx(ORG_A):
        async with pool.acquire() as c:
            base = await sod.can_check(c, org_id=ORG_A, item_type=sod.AUTHORITY_CHANGE, checker_id=U_A3,
                                       maker_ids=[U_A1], household_id=HOUSEHOLD)
            check(base.allowed, "before any history: U_A3 may check U_A1's change in the household", base.reason)
            # history: U_A3 MADE an authority change in this household (checked by U_A2) — recorded by the service
            hist = await sod.can_check(c, org_id=ORG_A, item_type=sod.AUTHORITY_CHANGE, checker_id=U_A2,
                                       maker_ids=[U_A3], item_id=f"{FIXTURE_ID_PREFIX}0901", household_id=HOUSEHOLD,
                                       site="verify_s30.history")
            did = await sod.record_decision(c, hist, site="verify_s30.history")
            S.setdefault("decisions", []).append(did)
            hit = await sod.can_check(c, org_id=ORG_A, item_type=sod.AUTHORITY_CHANGE, checker_id=U_A3,
                                      maker_ids=[U_A1], household_id=HOUSEHOLD)
            other = await sod.can_check(c, org_id=ORG_A, item_type=sod.AUTHORITY_CHANGE, checker_id=U_A2,
                                        maker_ids=[U_A1], household_id=HOUSEHOLD)
            elsewhere = await sod.can_check(c, org_id=ORG_A, item_type=sod.AUTHORITY_CHANGE, checker_id=U_A3,
                                            maker_ids=[U_A1], household_id=None)
    check(did is not None and hit.refused and (hit.rule or {}).get("rule_kind") == "org",
          "the stricter org rule REFUSES U_A3 (who made a change in this household) — the platform rule alone allowed it",
          f"{hit.as_dict()}")
    check(other.allowed and elsewhere.allowed, "… while U_A2 (made nothing there) may check it, and U_A3 may check "
          "outside the household", f"{other.reason} / {elsewhere.reason}")
    # history: version 2 (warn) replaces version 1; the first is kept, closed
    code, r3, _ = await api(U_A2, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "org", "item_type": "authority_change", "scope": "same_household", "period_days": 30,
        "severity": "warn", "description": "S30 verify: warn only"})
    v2 = (r3.get("rule") or {}).get("id")
    code2, _b, _ = await api(U_A1, "POST", f"/api/v1/admin/sod/rules/{v2}/decide", {"outcome": "approve"})
    vers = await scoped(conn, ORG_A, "SELECT version_no, status, valid_to IS NOT NULL AS closed, severity FROM sod_rules "
                                     "WHERE org_id = $1 AND rule_key LIKE 'org:authority_change:%' ORDER BY version_no",
                        ORG_A)
    check(code == 201 and code2 == 200 and [(r["version_no"], r["status"], r["closed"]) for r in vers]
          == [(1, "retired", True), (2, "active", False)],
          "edits are versioned: v1 retired (valid_to set, kept), v2 active", f"{[dict(r) for r in vers]}")
    with _ctx(ORG_A):
        async with pool.acquire() as c:
            warn = await sod.can_check(c, org_id=ORG_A, item_type=sod.AUTHORITY_CHANGE, checker_id=U_A3,
                                       maker_ids=[U_A1], household_id=HOUSEHOLD)
    check(warn.allowed and any(w.get("code") == "org_rule_warn" for w in warn.warnings),
          "severity 'warn': allowed, with the rule named as a warning", f"{warn.as_dict()}")
    # platform rules: Super Admin only, a DIFFERENT Super Admin decides — rejected here so no real rule changes
    code, b, _ = await api(U_A1, "POST", "/api/v1/admin/sod/rules", {
        "owner_scope": "platform", "rule_kind": "policy", "item_type": "role_grant", "self_approval": "never",
        "enforce_floor": True, "description": "S30 verify: platform draft by a non-Super Admin"})
    check(code == 403, "an org admin cannot draft a platform rule", f"{code} {dumps(b)}")
    code, p1, _ = await api(U_SA1, "POST", "/api/v1/admin/sod/rules", {
        "owner_scope": "platform", "rule_kind": "policy", "item_type": "role_grant", "self_approval": "never",
        "enforce_floor": True, "description": "S30 verify: platform draft (rejected by the verify)"})
    pid = (p1.get("rule") or {}).get("id")
    code_a, _b, _ = await api(U_SA1, "POST", f"/api/v1/admin/sod/rules/{pid}/decide", {"outcome": "approve"})
    code_r, b_r, _ = await api(U_SA2, "POST", f"/api/v1/admin/sod/rules/{pid}/decide",
                               {"outcome": "reject", "note": "verify: never applied"})
    st = await platform(conn, "SELECT status FROM sod_rules WHERE id = $1::uuid", pid, fetch="fetchval")
    check(code == 201 and code_a == 403 and code_r == 200 and st == "rejected",
          "a platform draft: its drafter (a Super Admin) cannot decide it; a second Super Admin rejects it",
          f"{code} {code_a} {code_r} {st} {dumps(b_r)}")


async def y_roles(conn):
    section("[Y] Conflicting-role warnings; self-grant blocked (Super Admin and a second login included); report")
    roles = {r["name"]: str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT id, name FROM roles WHERE org_id = $1",
                                                           ORG_A)}
    rolesb = {r["name"]: str(r["id"]) for r in await scoped(conn, ORG_B, "SELECT id, name FROM roles WHERE org_id = $1",
                                                            ORG_B)}
    code, b, _ = await api(U_A2, "PUT", f"/api/v1/admin/users/{U_AV}/role", {"role_id": roles["s30_admin"]})
    w = [x for x in (b.get("sod_warnings") or []) if x.get("code") == "conflicting_duties"]
    au = next((x for x in w if x.get("item_type") == "authority_change"), None)
    check(code == 200 and au is not None and any(r.get("rule_key") == "default:authority_change"
                                                  for r in au.get("still_stopped_by") or []),
          "a conflicting grant WARNS (200): the person can both set and approve authority, still stopped per item by "
          "default:authority_change", f"{code} {dumps(b.get('sod_warnings'))}")
    code, _b, _ = await api(U_A2, "PUT", f"/api/v1/admin/users/{U_AV}/role", {"role_id": roles["s30_viewer"]})
    check(code == 200, "… and the viewer is restored to their own role", f"{code}")
    before = [r["role_id"] for r in await scoped(conn, ORG_A, "SELECT role_id::text AS role_id FROM user_roles WHERE "
                                                              "user_id = $1 ORDER BY 1", U_A2)]
    code, b, _ = await api(U_A2, "PUT", f"/api/v1/admin/users/{U_A2}/role", {"role_id": roles["s30_viewer"]})
    after = [r["role_id"] for r in await scoped(conn, ORG_A, "SELECT role_id::text AS role_id FROM user_roles WHERE "
                                                             "user_id = $1 ORDER BY 1", U_A2)]
    check(code == 403 and after == before and S.get("role_policy_on") is True,
          "granting YOURSELF a role is blocked (403) once the org's floor is on — and nothing changed", f"{code} "
          f"{dumps(b)} {before} → {after}")
    code, b, _ = await api(U_A1, "PUT", f"/api/v1/admin/users/{U_A1}/role", {"role_id": roles["s30_viewer"]},
                           sub=SECOND_SUB)
    check(code == 403, "… from the person's SECOND login too (two logins, one person)", f"{code} {dumps(b)}")
    code, b, _ = await api(U_SA1, "PUT", f"/api/v1/admin/users/{U_SA1}/role", {"role_id": roles["s30_admin"]})
    sa_still = await platform(conn, "SELECT EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                                    "WHERE ur.user_id = $1 AND r.name = 'super_admin')", U_SA1, fetch="fetchval")
    check(code == 403 and sa_still is True, "… and for a Super Admin (403, still a Super Admin)", f"{code} {dumps(b)}")
    code, b, _ = await api(U_A2, "POST", f"/api/v1/admin/permission-sets/{PSET}/users", {"user_id": str(U_A2)})
    n = await scoped(conn, ORG_A, "SELECT count(*) FROM user_permission_sets WHERE user_id = $1", U_A2,
                     fetch="fetchval")
    check(code == 403 and n == 0, "assigning YOURSELF a permission set is blocked the same way", f"{code} {n}")
    code, b, _ = await api(U_A2, "POST", f"/api/v1/admin/permission-sets/{PSET}/users", {"user_id": str(U_A1)})
    check(code in (200, 201) and isinstance(b.get("sod_warnings"), list),
          "assigning a permission set to someone else is allowed and returns its warnings list", f"{code} {dumps(b)}")
    # org B: the floor is not on (platform default) — today's behaviour: allowed, and SAID so
    code, b, _ = await api(U_B2, "PUT", f"/api/v1/admin/users/{U_B2}/role", {"role_id": rolesb["s30_settings"]})
    check(code == 200 and any(x.get("code") == "floor_not_enforced" for x in b.get("sod_warnings") or []),
          "where the org has not turned the floor on, a self-grant is still allowed as before S30 — with a "
          "'floor not enforced' warning (reported, never silently tightened)", f"{code} {dumps(b.get('sod_warnings'))}")
    refused = await scoped(conn, ORG_A, "SELECT count(*) FROM sod_decisions WHERE org_id = $1 AND item_type = "
                                        "'role_grant' AND outcome = 'refused'", ORG_A, fetch="fetchval")
    check(refused >= 4, "every refused self-grant is recorded in sod_decisions (fixture org)", f"{refused}")
    code, ov, _ = await api(U_A2, "GET", "/api/v1/admin/sod")
    people = {p["user_id"]: p for p in ov.get("conflicts") or []}
    check(code == 200 and str(U_A1) in people and any(c["item_type"] == "authority_change"
                                                      for c in people[str(U_A1)]["conflicts"])
          and str(U_A3) not in people,
          "the conflict report lists the fixture person (U_A1: set + approve authority) and not the reviewer",
          f"{code} {sorted(people)[:6]}")


async def y_self_approval(conn):
    section("[Y] Governed self-approval: empty eligible set AND policy; disclosed, logged, compliance to-do; switch off "
            "→ the item waits; floor items never")
    pool = await get_pool()
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            empty = await sod.eligible_checkers(c, org_id=ORG_B, item_type=sod.AGENT_PROPOSAL, maker_ids=[U_B1])
            p1 = await AP.create_proposal(c, str(ORG_B), agent_key="authoring", object_type="s30_verify",
                                          payload={"n": 1}, proposed_by=str(U_B1))
            p2 = await AP.create_proposal(c, str(ORG_B), agent_key="authoring", object_type="s30_verify",
                                          payload={"n": 2}, proposed_by=str(U_B1))
    check(empty == [], "org B: the eligible-checker set for U_B1's proposal is genuinely empty", f"{empty}")
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            try:
                await AP.review_proposal(pool, c, str(ORG_B), p1, reviewed_by=str(U_B1), decision="approved")
                no_reason = "accepted"
            except AP.SelfApprovalReasonRequiredError:
                no_reason = "reason required"
    check(no_reason == "reason required", "self-approval without a reason is refused", no_reason)
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            row = await AP.review_proposal(pool, c, str(ORG_B), p1, reviewed_by=str(U_B1), decision="approved",
                                           self_approval_reason="sole adviser this week")
    item = await scoped(conn, ORG_B, "SELECT status, self_approved, self_approval_reason FROM agent_proposals WHERE "
                                     "id = $1::uuid", p1, fetch="fetchrow")
    dec = await scoped(conn, ORG_B, "SELECT id::text AS id, outcome, disclosure, rule_key FROM sod_decisions WHERE "
                                    "org_id = $1 AND item_id = $2 ORDER BY created_at DESC LIMIT 1", ORG_B, p1,
                       fetch="fetchrow")
    check(row.get("self_approved") is True and item is not None and item["self_approved"] is True
          and item["status"] == "approved", "the self-approval is shown on the item (self_approved, reason; re-read)",
          f"{dict(item or {})}")
    check(dec is not None and dec["outcome"] == "self_approved" and sod.SELF_APPROVAL_REASON in (dec["disclosure"] or "")
          and dec["rule_key"] == "policy:agent_proposal",
          "recorded in sod_decisions with the reason 'no other eligible checker' and the policy that allowed it",
          f"{dict(dec or {})}")
    todos = await scoped(conn, ORG_B, "SELECT user_id::text AS u FROM member_todos WHERE org_id = $1 AND source = $2 "
                                      "AND related_id = $3::uuid", ORG_B, sod.TODO_SOURCE_SELF_APPROVAL,
                         dec["id"] if dec else None)
    check(bool(todos) and {t["u"] for t in todos} == {str(U_B2)},
          "a compliance to-do is raised — to the compliance holder (U_B2), not the self-approver", f"{[dict(t) for t in todos]}")
    code, ov, _ = await api(U_B2, "GET", "/api/v1/admin/sod")
    check(code == 200 and any(d.get("item_id") == p1 for d in ov.get("self_approvals") or []),
          "the self-approval log on Admin → Segregation of duties shows it", f"{code}")
    # the org turns self-approval OFF for agent proposals — U_B1 drafts, U_B2 approves (one review holder suffices)
    code, r, _ = await api(U_B1, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "policy", "item_type": "agent_proposal", "self_approval": "never", "enforce_floor": True,
        "description": "S30 verify: no self-approval of agent proposals"})
    code2, _b, _ = await api(U_B2, "POST", f"/api/v1/admin/sod/rules/{(r.get('rule') or {}).get('id')}/decide",
                             {"outcome": "approve"})
    check(code == 201 and code2 == 200, "org B turns self-approval off (drafted by U_B1, approved by U_B2)",
          f"{code} {code2} {dumps(r)}")
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            try:
                await AP.review_proposal(pool, c, str(ORG_B), p2, reviewed_by=str(U_B1), decision="approved",
                                         self_approval_reason="sole adviser this week")
                off = "accepted"
            except AP.MakerCheckerError as exc:
                off = f"refused: {exc}"
    waits = await scoped(conn, ORG_B, "SELECT status FROM agent_proposals WHERE id = $1::uuid", p2, fetch="fetchval")
    check(off.startswith("refused") and waits == "pending", "with the policy off, the item WAITS (refused; still "
          "pending)", f"{off} {waits}")
    # org A: another eligible checker exists — the maker is refused, a different holder approves
    with _ctx(ORG_A):
        async with pool.acquire() as c:
            p3 = await AP.create_proposal(c, str(ORG_A), agent_key="authoring", object_type="s30_verify",
                                          payload={"n": 3}, proposed_by=str(U_A1))
            try:
                await AP.review_proposal(pool, c, str(ORG_A), p3, reviewed_by=str(U_A1), decision="approved",
                                         self_approval_reason="x")
                own = "accepted"
            except AP.MakerCheckerError:
                own = "refused"
            ok = await AP.review_proposal(pool, c, str(ORG_A), p3, reviewed_by=str(U_A3), decision="approved")
    check(own == "refused" and ok.get("status") == "approved" and ok.get("self_approved") is False,
          "where another eligible checker exists, self-approval is refused even with a reason; another holder approves",
          f"{own} {dumps(ok)}")
    # floor items never: even a context whose policy says 'when_no_other_checker' refuses a floor item
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            ctx = await sod.load(c, org_id=ORG_B, item_type=sod.TRADE_BLOCK, ids=[U_B1])
    forged = dict(ctx.rules[0]) if ctx.rules else {}
    ctx.rules = [*ctx.rules, {**forged, "id": "forged", "rule_key": "policy:trade_block", "rule_kind": "policy",
                              "org_id": str(ORG_B), "self_approval": "when_no_other_checker", "enforce_floor": True,
                              "version_no": 99}]
    d = ctx.evaluate(checker_id=U_B1, maker_ids=[U_B1], other_checker_exists=False)
    check(d.refused and (d.rule or {}).get("rule_kind") == "floor",
          "floor items are NEVER self-approved — even under a (forged) policy allowing it, the floor rule refuses",
          f"{d.as_dict()}")


def _t1_bpmn() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:twoa="http://2ndactcapital.com/bpmn/ext" id="D_s30" targetNamespace="http://2ndactcapital.com/bpmn">'
        '<bpmn:process id="proc_s30" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Svc_1" name="S30 write verb">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{FIXTURE_VERB}" autonomyTier="1"/>'
        '</bpmn:extensionElements><bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Svc_1"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="Svc_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>')


async def y_workflows(conn):
    section("[Y] Workflows: every verb declares its duty; a write verb without one is refused; a scheduled run's "
            "Tier-1 step refuses its trigger owner, accepts another person")
    undeclared = sod.undeclared_write_verbs(REGISTRY)
    check(undeclared == [UNDECLARED_VERB], "every registered write verb declares a duty (the only undeclared one is "
          "the verify's own fixture)", f"{undeclared}")
    check(all(sod.verb_duty(a) in sod.VERB_DUTY_KINDS for a in REGISTRY.all() if a.key != UNDECLARED_VERB),
          "every other verb's duty is one of read / propose / approve / write")
    xml_bad = _t1_bpmn().replace(FIXTURE_VERB, UNDECLARED_VERB)
    errs = G._validate(xml_bad, {UNDECLARED_VERB}, set())
    check(any("no declared duty" in e for e in errs), "a write verb without a declared duty is refused at validation",
          f"{errs}")
    check(G._validate(_t1_bpmn(), {FIXTURE_VERB}, set()) == [], "the same diagram with a declared write verb validates")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO workflow_definitions (id, org_id, name, description, created_by) VALUES "
                           "($1, $2, 'S30 VERIFY tier1', 'verify_s30 fixture', $3)", DEF_T1, ORG_A, U_A2)
        await conn.execute("INSERT INTO workflow_versions (id, workflow_definition_id, org_id, version_number, bpmn_xml, "
                           "change_summary, is_current, created_by) VALUES ($1, $2, $3, 1, $4, 's30 fixture', true, $5)",
                           VER_T1, DEF_T1, ORG_A, _t1_bpmn(), U_A2)
        await derive_and_store_steps(conn, VER_T1, ORG_A, _t1_bpmn())
        # the trigger names its owner (U_A2); inactive, so no real cron tick ever fires it
        await conn.execute("INSERT INTO workflow_triggers (id, workflow_definition_id, org_id, trigger_type, "
                           "schedule_cron, timezone, is_active, created_by) VALUES ($1, $2, $3, 'scheduled', "
                           "'0 9 * * 1', 'UTC', false, $4)", TRIG_T1, DEF_T1, ORG_A, U_A2)
    pool = await get_pool()
    trig = {"id": TRIG_T1, "org_id": ORG_A, "workflow_version_id": VER_T1, "created_by": U_A2}
    run = await SCHED._fire(pool, trigger=trig, occurrence_utc=datetime.now(timezone.utc))
    step = await scoped(conn, ORG_A, "SELECT rs.id, rs.status, rs.proposed_by::text AS p FROM workflow_run_steps rs WHERE "
                                     "rs.workflow_run_id = $1", run.get("run_id"), fetch="fetchrow")
    check(run.get("status") == "awaiting_approval" and step is not None and step["p"] == str(U_A2),
          "a scheduled run has a NAMED maker — the trigger's owner — and its Tier-1 step suspends", f"{run} {dict(step or {})}")
    with _ctx(ORG_A):
        try:
            await ENG.resolve_tier_approval(pool, step["id"], reviewed_by=str(U_A2), decision="approved")
            own = "accepted"
        except AP.MakerCheckerError:
            own = "refused"
        ok = await ENG.resolve_tier_approval(pool, step["id"], reviewed_by=str(U_A3), decision="approved")
    check(own == "refused" and CALLS.get(FIXTURE_VERB) == 1,
          "the Tier-1 step REFUSES the trigger owner as checker and accepts another person (the verb runs once)",
          f"{own} calls={CALLS.get(FIXTURE_VERB)} {dumps(ok)}")
    # an event-style run: started by someone else (U_A1), the trigger still owned by U_A2 — the owner is a maker too
    with _ctx(ORG_A):
        run2 = await ENG.start_workflow_run(pool, VER_T1, ORG_A, {"trigger_id": str(TRIG_T1), "trigger_type": "event"},
                                            U_A1)
    step2 = await scoped(conn, ORG_A, "SELECT id FROM workflow_run_steps WHERE workflow_run_id = $1", run2.get("run_id"),
                         fetch="fetchval")
    with _ctx(ORG_A):
        try:
            await ENG.resolve_tier_approval(pool, step2, reviewed_by=str(U_A2), decision="approved")
            owner = "accepted"
        except AP.MakerCheckerError:
            owner = "refused"
        await ENG.resolve_tier_approval(pool, step2, reviewed_by=str(U_A3), decision="approved")
    check(owner == "refused" and CALLS.get(FIXTURE_VERB) == 2,
          "an event-started run: the trigger's OWNER (not the starter) is refused as checker; another person approves",
          f"{owner} calls={CALLS.get(FIXTURE_VERB)}")
    find("event-triggered runs: before S30 the trigger owner could approve a Tier-1 step its own trigger produced "
         "(only the starter was the maker) — now both are makers; the one live event trigger has no owner, so no live "
         "decision changes")


async def y_human_task(conn):
    section("[Y] The central site: a governed task (services.human_tasks) refuses its maker, records both decisions")
    pool = await get_pool()
    with _ctx(ORG_A):
        task = await HT.open_task(pool, org_id=ORG_A, kind=HT_KIND, subject_type="s30_verify",
                                  subject_id=f"{FIXTURE_ID_PREFIX}0801", maker_id=U_A1,
                                  eligible_permission="approve_authority", title="S30 verify task")
        try:
            await HT.complete_task(pool, org_id=ORG_A, task_id=task["id"], actor_id=U_A1, outcome="approve")
            own = "accepted"
        except HT.MakerCheckerError as exc:
            own = f"refused: {exc}"
        done = await HT.complete_task(pool, org_id=ORG_A, task_id=task["id"], actor_id=U_A2, outcome="approve")
    check(own.startswith("refused") and "Entered by" in own, "the maker is refused, with the refusal text naming who "
          "made it (services.maker_names)", own[:200])
    check(done.get("status") == "completed", "a different holder decides it", f"{done.get('status')}")
    rows = await scoped(conn, ORG_A, "SELECT outcome, checker_id::text AS c FROM sod_decisions WHERE org_id = $1 AND "
                                     "item_type = 'human_task' AND item_id = $2 ORDER BY created_at",
                        ORG_A, f"{FIXTURE_ID_PREFIX}0801")
    check([(r["outcome"], r["c"]) for r in rows] == [("refused", str(U_A1)), ("allowed", str(U_A2))],
          "both decisions are recorded (refused for the maker, allowed for the checker)", f"{[dict(r) for r in rows]}")


async def y_admin_page(conn):
    section("[Y] Admin → Segregation of duties: envelope, reports, refused checks (90 days), CSV; view-only fails closed")
    code, ov, _ = await api(U_A1, "GET", "/api/v1/admin/sod")
    p, v = ov.get("permissions") or {}, ov.get("vocabularies") or {}
    check(code == 200 and p.get("can_write") is True and set(v.get("editable") or []) == {"org_rule", "policy"}
          and len(ov.get("item_types") or []) == len(sod.ITEM_TYPES) and len(ov.get("duties") or []) > 0,
          "an org admin: the page with duties, every item type (with its policy) and write controls", f"{code} {p} {v}")
    check(any(r.get("rule_kind") == "floor" for r in ov.get("rules") or [])
          and any(r.get("org_id") == str(ORG_A) for r in ov.get("rules") or [])
          and any(d.get("item_type") == "role_grant" for d in ov.get("refusals") or []),
          "the rules show floor and org rows; refused checks list the fixture refusals", f"{len(ov.get('rules') or [])}")
    code, vw, _ = await api(U_AV, "GET", "/api/v1/admin/sod")
    check(code == 200 and (vw.get("permissions") or {}).get("can_write") is False
          and (vw.get("vocabularies") or {}).get("editable") == [],
          "a view-only caller (view_audit_log): can_write false, editable is an EMPTY list", f"{code} {vw.get('permissions')}")
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    code, b, _ = await api(U_AV, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "org", "item_type": "trade_block", "scope": "same_item", "severity": "block",
        "description": "S30 verify: a viewer's valid body"})
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    check(code == 403 and n1 == n0, "the server refuses the viewer's VALID write (403, nothing written)",
          f"{code} {dumps(b)} {n0} → {n1}")
    code, _b, _ = await api(U_B1, "GET", "/api/v1/admin/sod/reports/conflicts")
    code_m, _b2, _ = await api(U_B1, "GET", "/api/v1/admin/sod/reports/not_a_report")
    check(code == 200 and code_m == 404, "a report by name (JSON); an unknown report is 404", f"{code} {code_m}")
    code, b, ctype = await api(U_A1, "GET", "/api/v1/admin/sod/reports/refusals", params={"format": "csv"})
    raw = b.get("raw") or ""
    check(code == 200 and ctype.startswith("text/csv") and raw.splitlines()[:1] and raw.splitlines()[0].startswith("id,")
          and "role_grant" in raw, "CSV export: text/csv with a header row and the fixture refusals", f"{code} {ctype} "
          f"{raw[:160]!r}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    a_rules = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    b_sees_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    b_rules = int(await scoped(conn, ORG_B, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_B, fetch="fetchval"))
    a_sees_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_B, fetch="fetchval"))
    check(a_rules > 0 and b_rules > 0 and b_sees_a == 0 and a_sees_b == 0,
          "sod_rules: each org reads its own rules, never the other's (both directions)", f"A={a_rules} B={b_rules} "
          f"B→A={b_sees_a} A→B={a_sees_b}")
    a_dec = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_decisions WHERE org_id = $1", ORG_A, fetch="fetchval"))
    b_dec = int(await scoped(conn, ORG_B, "SELECT count(*) FROM sod_decisions WHERE org_id = $1", ORG_B, fetch="fetchval"))
    x1 = int(await scoped(conn, ORG_B, "SELECT count(*) FROM sod_decisions WHERE org_id = $1", ORG_A, fetch="fetchval"))
    x2 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_decisions WHERE org_id = $1", ORG_B, fetch="fetchval"))
    check(a_dec > 0 and b_dec > 0 and x1 == 0 and x2 == 0, "sod_decisions: isolated both directions",
          f"A={a_dec} B={b_dec} {x1} {x2}")
    plat_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id IS NULL AND status = 'active'",
                              fetch="fetchval"))
    plat_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM sod_rules WHERE org_id IS NULL AND status = 'active'",
                              fetch="fetchval"))
    check(plat_a > 0 and plat_a == plat_b, "the platform rules are readable by both orgs", f"{plat_a} {plat_b}")
    code, ov, _ = await api(U_B1, "GET", "/api/v1/admin/sod")
    raw = json.dumps(ov, default=str) if code == 200 else ""
    leaked = [str(x) for x in (ORG_A, U_A1, U_A2, U_A3) if str(x) in raw]
    check(code == 200 and not leaked, "org B's page carries no org A rule, decision or person", f"{code} {leaked}")
    refusal = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                               "'false', true)", str(ORG_B))
            await conn.execute("INSERT INTO sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, maker_duty, "
                               "checker_duty, scope, severity, description, status, version_no, drafted_by) VALUES "
                               "('org:x', 'org', $1, 'org', 'trade_block', 'propose_trade', 'approve_trade', "
                               "'same_item', 'block', 'cross-org probe', 'draft', 1, $2)", ORG_A, U_B1)
            raise RuntimeError("accepted")
    except asyncpg.PostgresError as exc:
        refusal = type(exc).__name__
    except RuntimeError:
        refusal = None
    check(refusal is not None, "org B cannot write a rule into org A (RLS refuses the insert)", f"{refusal}")


async def y_real_rows(conn):
    section("[Y] Real rows untouched (count + checksum)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    self_static(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0, "visibility1_routes.py --check: current", (out + err)[-300:])
    data = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
    rows = data.get("routes", []) if isinstance(data, dict) else data
    have = {(m, r.get("path")): r.get("after") for r in rows for m in (r.get("methods") or [])}
    miss = [(k, g) for k, g in ROUTES.items() if g not in str(have.get(k))]
    check(not miss, f"each of s30's {len(ROUTES)} routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")
    rs = (REPO / "scripts" / "regression_set.txt").read_text(encoding="utf-8") if (REPO / "scripts" /
                                                                                  "regression_set.txt").exists() else ""
    check("verify_s30.py" in rs, "verify_s30.py is in scripts/regression_set.txt")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) > 0 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the s30 render tests ({NODE_TEST}, run by path) all pass ({n.group(1) if n else '?'})",
          f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_bulkapprove1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_bulkapprove1.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_bulkapprove1.py: 0 FAIL (its own final TOTAL line and exit code)",
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
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("catalog", y_catalog, conn)
        await run("model", y_model, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("replay", y_replay, conn)
            await run("floor", y_floor, conn)
            await run("human task", y_human_task, conn)
            await run("org rules", y_org_rules, conn)
            await run("roles", y_roles, conn)
            await run("self-approval", y_self_approval, conn)
            await run("workflows", y_workflows, conn)
            await run("admin page", y_admin_page, conn)
            await run("isolation", y_isolation, conn)
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        remove_fixture_verbs()
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (rules, decisions, person links, runs, proposals, to-dos)",
                  f"{left}")
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
