"""verify_perms1 — one source of permissions (Principal = roles ∪ profile ∪ permission sets), the org admin baseline,
deal / household creation (Joe's decisions 1-2), the HollisMuse re-run defects (WF-06, WF-03, REP-10, WF-04, WF-05),
the test-row removal.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is
set before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``) before
``main`` is imported. No network: the Auth0 profile fetch, LiteLLM live info, the embedding call, R2 and the
workflow generator's model call are replaced by local stubs (each matches the CURRENT signature it replaces).

Everything after the bearer token is the real app (middleware, Principal, every gate, every handler) against the
live database, in TWO fixture orgs (99000000-…-000000520001 / 520002), ``is_test = true`` (asserted), as are the
fixture users and their fixture profiles / permission sets. Teardown at start and end: the shared scoped cleanup
(_verify_cleanup) on the operator DSN, by fixture org / user ids (profiles, permission sets, households, accounts,
deals, workflows, model uploads — every org_id table — included); every counted table is re-read for its exact
pre-test count, and the REAL rows of users / roles / grants / profiles / permission sets / entities / accounts /
households are fingerprinted (count + md5 by the catalog primary key; users excluding last_login_at only, which a
real sign-in writes) before and after. The only change this sprint makes to real rows is docs/perms1_part1.sql (the
org_admin grants) — already applied before this run, so the real role_permissions fingerprint must not move.

"BEFORE" (Task 6, first bullet). The pre-fix code cannot run in the same process, so "before" is reproduced two ways,
both real: (a) the Principal statement AT BEFORE_REF (read from git, executed against the fixture user) — what
/users/me and every role-axis gate (Positions) saw; (b) the identical request on the fixture org admin while its
org_admin role holds only the pre-PERMS1 defaults (Positions → 403), then again after rbac.ensure_org_admin_role
grants the PERMS1 baseline (→ 200). [FIND] recorded honestly: at BEFORE_REF the WORKFLOW API already admitted a
holder of the "Org Admin" profile (it read the profile axis only); what refused that org admin was the menu (Runs /
Triggers super-admin only, /users/me without the keys) and every role-axis gate. The profile-only workflow gate
refused the REVERSE case — a role-only holder (support staff's role grants view_workflow_runs): reproduced with
BEFORE_REF's own services/profiles.get_effective_permissions source executed against the fixture.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
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
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)

SPRINT_KEY = "perms1"
BEFORE_REF = "01c0f57"   # perms1-work base: main after "merge rep12" + "chore: refresh schema snapshot"

# ── Task 1a — every place permissions are computed or checked, and its source AFTER this sprint. Written by hand from
# the discovery; each needle must be present in the code at the sprint's end. (file, needle, source)
TASK1A = [
    ("apps/api/services/authz.py", "AS granted_names", "Principal — roles ∪ profile ∪ permission sets (ONE statement)"),
    ("apps/api/services/authz.py", "granted &= set(row[\"member_safe_names\"] or [])",
     "Principal — the BUGFIX2 client rule applied in principal_from_row"),
    ("apps/api/services/profiles.py", "return set(principal.permissions)",
     "profiles.get_effective_permissions → the Principal (was profile ∪ sets only)"),
    ("apps/api/services/rbac.py", "return set(principal.permissions)",
     "rbac.fetch_permission_names / get_user_permissions / has_permission → the Principal (was roles of ANY org)"),
    ("apps/api/services/rbac.py", "p = principal_from_row(row)",
     "rbac.get_users_with_permission (todo fan-out) → the same Principal statement"),
    ("apps/api/routers/workflows.py", "def _principal_has(request: Request, permission_key: str) -> bool:",
     "workflow gates → the request's Principal (was services.profiles, profile axis only)"),
    ("apps/api/services/workflow_engine.py", "if await user_has_permission(pool, actor_id, permission_key):",
     "workflow engine Service Tasks → profiles.user_has_permission → the Principal"),
    ("apps/api/routers/users.py", "permissions=sorted(principal.permissions)", "/users/me → the Principal (menu)"),
    ("apps/api/services/agent_proposals.py", "get_user_permissions", "maker-checker review → rbac → the Principal"),
    ("apps/web/lib/menuVisibility.mjs", "GATE_VIEW_WORKFLOW_RUNS", "menu → /users/me (the workflow keys now present)"),
    ("apps/api/routers/profiles.py", "staff_only_keys", "profile assignment refusal (BUGFIX2) — reads profile rows "
                                                         "to VALIDATE an assignment, computes no permissions"),
]
#: Task 1c / 1d / 1e / 1f findings, printed as [FIND] — written by hand.
FINDINGS = [
    ("1b: the profile key \"staff\" is NOT in the permissions catalog and nothing checks it (the action registry's "
     "last 'staff' gate became manage_deals long ago). It meant STAFF_ROLES membership; it now maps to NOTHING — the "
     "Principal joins profile keys to the catalog — and never to is_staff / is_internal (roles only)."),
    ("1c: workflow routes author_workflows / view_workflow_runs / configure_workflow_triggers (profile axis before); "
     "Positions / Commitments view_portfolio via rbac (role axis); CRM reads require_internal (staffroles1); fee "
     "reads view_portfolio, fee writes manage_billing (role axis)."),
    ("1d: fee pages /fee-schedules/chat, /profitability, /admin/billing-groups; only Billing Groups had a menu entry; "
     "/admin/fees never existed; no app not-found page. The fee READS gated view_portfolio only — a MEMBER grant: a "
     "member could read the firm's fee schedules and P&L. Now require_internal first. manage_billing (fee writes) is "
     "NOT granted to org_admin (not in Joe's list) — the org admin reads, a billing holder writes."),
    ("1e: WF-03 was the DATA path: GET /entities/{id}/tree read entity_relationships only, downward only; the Hargrove "
     "owners live in entity_ownership and point INTO the root. BUGFIX2 had fixed a different component "
     "(/ownership-graph). The look-through toggle is client-only. portfolio_rollup also reads get_lookthrough, so the "
     "both-tables read is opt-in (the graph route) — rollup input unchanged."),
    ("1f: REP-10 — 'In progress' = model_uploads.outcome 'received' with no outcome event; bugfix2's clean-up deleted "
     "the model and its draft_created EVENT but not the upload row. Duplicate key = (org, sha256) of a 'received' "
     "row (unique index model_uploads_stored_uq), events ignored."),
    ("NAV-10 / WF-02 at HEAD before PERMS1: the workflow API already admitted the 'Org Admin' profile holders (probe "
     "2026-11-04); the refusal the Muse saw is not reproducible at BEFORE_REF's API — the menu sent the org admin to "
     "super-admin-only tabs and /users/me lacked the keys. Unified now either way."),
    ("real users gaining keys from the union (live 2026-11-04): jpl99172 / jpl991723 +author_workflows, "
     "configure_workflow_triggers, view_workflow_runs (now also by role); jpl991722 (support_staff) +manage_documents, "
     "view_marketplace from 'CSA / Ops'; jpl991721 (member) nothing (client rule); 'staff' grants nothing."),
]

PERMS1_ADDED = ("view_portfolio", "view_members", "view_dashboard", "view_deals", "view_workflow_runs",
                "author_workflows", "configure_workflow_triggers", "manage_deals", "manage_members")
# The sprint's own route changes (method, path) → its gate rule at the end. Written by hand.
FEE_READ = ("perm", ("view_portfolio",))
FEE_WRITE = ("perm", ("manage_billing",))
HH = ("internal_perm", ("manage_members",))
ROUTE_EXPECT = {
    ("GET", "/api/v1/fee-chat/conversation"): FEE_READ,
    ("POST", "/api/v1/fee-chat/worked-example"): FEE_READ,
    ("POST", "/api/v1/fee-chat/propose"): FEE_WRITE,
    ("POST", "/api/v1/fee-chat/corrections"): FEE_WRITE,
    ("POST", "/api/v1/fee-chat/save"): FEE_WRITE,
    ("GET", "/api/v1/fee-schedules"): FEE_READ,
    ("GET", "/api/v1/fee-schedules/{schedule_id}"): FEE_READ,
    ("GET", "/api/v1/fee-schedules/{schedule_id}/assignments"): FEE_READ,
    ("GET", "/api/v1/fee-schedules/resolve/account/{account_id}"): FEE_READ,
    ("POST", "/api/v1/fee-schedules"): FEE_WRITE,
    ("PATCH", "/api/v1/fee-schedules/{schedule_id}"): FEE_WRITE,
    ("POST", "/api/v1/fee-schedules/{schedule_id}/submit"): FEE_WRITE,
    ("POST", "/api/v1/fee-schedules/{schedule_id}/retire"): FEE_WRITE,
    ("POST", "/api/v1/fee-schedules/assignments"): FEE_WRITE,
    ("POST", "/api/v1/fee-schedules/assignments/{assignment_id}/end"): FEE_WRITE,
    ("GET", "/api/v1/profitability/pnl"): FEE_READ,
    ("GET", "/api/v1/profitability/households-by-margin"): FEE_READ,
    ("POST", "/api/v1/deals"): ("perm", ("manage_deals",)),
    ("POST", "/api/v1/admin/households"): HH,
}
NEW_ROUTES = {("GET", "/api/v1/admin/households"): HH, ("GET", "/api/v1/admin/households/{household_id}"): HH,
              ("POST", "/api/v1/admin/households/{household_id}/accounts"): HH}
INTERNAL_MARKED = {k for k, v in ROUTE_EXPECT.items() if v[0] == "perm" and k != ("POST", "/api/v1/admin/households")}

# Task 5 — the removed HollisMuse rows (ids from the prompt / the removal script's print-out).
MUSE_WORKFLOW = UUID("c76c0067-dc07-415b-93fb-ad55e81357b5")
MUSE_UPLOADS = [UUID(x) for x in ("9e593a8f-7025-490d-8d5f-385a46ff3a01", "da16bd09-e478-436e-b9d3-800326ff7c42",
                                  "c3228f25-7dbe-4bc4-adc3-a97ee56dbfad", "dc28778f-5461-4435-b74c-1e29d7feb83e",
                                  "0d948645-7cbd-4dd2-b22a-119fe534bc3b", "c9e9bb16-ff8e-40f8-8538-3d7be4450fe8")]


def _key(rec: dict) -> tuple:
    return (rec["methods"][0] if rec.get("methods") else "", rec["path"])


def _rule(rec: dict) -> tuple:
    e = rec.get("expect") or {}
    return (e.get("rule"), tuple(sorted(e.get("any_of") or [])))


def self_task1(check, find):
    bad = []
    print("    Task 1a — where permissions are computed / checked, and the source each uses (at the sprint's end):")
    for rel, needle, source in TASK1A:
        txt = VS.own_text(SPRINT_KEY, rel) or ""
        idx = txt.find(needle)
        line = txt.count("\n", 0, idx) + 1 if idx >= 0 else None
        print(f"      {rel}:{line}  {source}")
        if idx < 0:
            bad.append((rel, needle[:50]))
    check(not bad, "Task 1a: every listed computation / check is present at the sprint's end with its source",
          f"{bad}")
    wf = VS.own_text(SPRINT_KEY, "apps/api/routers/workflows.py") or ""
    check("from services.profiles import user_has_permission" not in wf and "await user_has_permission(" not in wf,
          "Task 2: routers/workflows.py no longer computes permissions from the profile axis separately")
    for f in FINDINGS:
        find(f"Task 1 — {f}")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    where = own.where()
    changed = {k: own.end[k] for k in own.changed}
    got = {(_key(v)): _rule(v) for v in changed.values()}
    want = {k: (r, tuple(sorted(p))) for k, (r, p) in ROUTE_EXPECT.items()}
    check(set(got) == set(want), "the sprint's own route changes are exactly the hand table (fee gates + POST /deals "
          "+ POST /admin/households)", f"{where}; extra {sorted(set(got) - set(want))}; "
          f"missing {sorted(set(want) - set(got))}")
    mism = sorted(k for k in set(got) & set(want) if got[k] != want[k])
    check(not mism, "every changed route's record rule equals the hand table", f"{[(k, got[k]) for k in mism]}")
    internal_missing = sorted(_key(v) for v in changed.values()
                              if _key(v) in INTERNAL_MARKED and "internal" not in v["after"])
    check(not internal_missing, "every changed fee route and POST /deals now carries require_internal",
          f"{internal_missing}")
    new = {(_key(own.end[k])): _rule(own.end[k]) for k in own.new}
    want_new = {k: (r, tuple(sorted(p))) for k, (r, p) in NEW_ROUTES.items()}
    check(new == want_new, "the sprint added exactly the three household routes (internal + manage_members)",
          f"{new}")
    opened = [_key(v) for v in changed.values() if v["after"] == "none beyond auth"]
    check(not opened, "no route became open", f"{opened}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own, list(own.changed) + list(own.new))
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_static(check, find):
    def read(rel):
        p = REPO / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    before = VS.git_show(BEFORE_REF, "apps/api/services/authz.py") or ""
    now = VS.own_text(SPRINT_KEY, "apps/api/services/authz.py") or ""
    line = 'STAFF_ROLE_NAMES = frozenset({"investment_staff", "admin", "super_admin", "owner"})'
    check(bool(line in before and line in now and now.count("STAFF_ROLE_NAMES = ") == 1),
          "the bypass set STAFF_ROLE_NAMES is byte-identical to BEFORE_REF (never widened)")
    m_b = re.search(r"INTERNAL_ROLE_NAMES = STAFF_ROLE_NAMES \| frozenset\(\{(.*?)\}\)", before, re.S)
    m_n = re.search(r"INTERNAL_ROLE_NAMES = STAFF_ROLE_NAMES \| frozenset\(\{(.*?)\}\)", now, re.S)
    check(bool(m_b and m_n and m_b.group(1) == m_n.group(1)), "INTERNAL_ROLE_NAMES unchanged")
    isstaff = "is_staff=bool(admitted and (is_super or STAFF_ROLE_NAMES.intersection(roles)))"
    check(bool(isstaff in before and isstaff in now), "Principal.is_staff is still computed from ROLES only "
          "(no profile key — 'staff' included — can reach it)")
    cols = now[now.find("PRINCIPAL_COLUMNS = \"\"\""):now.find("PRINCIPAL_BY_ID_SQL")]
    check(bool(cols.count("$2") == 1 and "$3" not in cols and "JOIN permissions pm ON pm.name = g.k" in cols),
          "PRINCIPAL_COLUMNS: one statement, no new parameter, profile / set keys joined to the catalog")
    rbac_now = VS.own_text(SPRINT_KEY, "apps/api/services/rbac.py") or ""
    m = re.search(r"ORG_ADMIN_DEFAULT_PERMISSIONS = \((.*?)\n\)", rbac_now, re.S)
    m0 = re.search(r"ORG_ADMIN_DEFAULT_PERMISSIONS = \((.*?)\n\)", VS.git_show(BEFORE_REF, "apps/api/services/rbac.py")
                   or "", re.S)
    names = set(re.findall(r'\("(\w+)",', m.group(1))) if m else set()
    names0 = set(re.findall(r'\("(\w+)",', m0.group(1))) if m0 else set()
    check(bool(names - names0 == set(PERMS1_ADDED) and names0 <= names), "Task 3: ensure_org_admin_role's defaults "
          "gained exactly the nine PERMS1 keys", f"added {sorted(names - names0)}")
    check(not any(n.startswith("approve_") for n in names - names0), "Task 3: no new approve_* grant")
    sql = read("docs/perms1_part1.sql")
    check(bool(sql and all(f"'{k}'" in sql for k in PERMS1_ADDED) and "approve_" not in sql.split("BEGIN;")[-1]),
          "Task 3: docs/perms1_part1.sql grants the same nine keys to every org_admin role, no approve_*")
    regs = read("scripts/regression_set.txt")
    check("verify_perms1.py" in regs, "Task 8: verify_perms1.py is in scripts/regression_set.txt")
    status = read("docs/PROJECT_STATUS.md")
    check(bool("PERMS1" in status and "visibility1" in status and "DEAL-01" in status and "REP-01" in status
               and "NAV-03" in status and "PRIV-06" in status),
          "Task 7/8: PROJECT_STATUS.md records PERMS1 and Joe's four decisions")
    tests = read("apps/web/tests/perms1/perms1.test.mjs")
    check(bool("canCreateDeal" in tests and "layoutOwners" in tests and "/profitability" in tests),
          "node tests exist for the menu, deal / household controls, workflow delete and the owners layout")
    check(bool((WEB_DIR / "app" / "not-found.js").exists() and "redirect(\"/admin/billing\")" in
               read("apps/web/app/admin/fees/page.js")),
          "WF-04: the app has its own not-found page; /admin/fees redirects to the Billing & Custody landing")
    gen = VS.own_text(SPRINT_KEY, "apps/api/services/workflow_nl_generator.py") or ""
    check(bool("raise WorkflowGenerationError(generation_refusal(errors))" in gen and "_correction_prompt(errors)" in gen),
          "WF-05: the generator retries once with the validator's errors, then refuses with the reason (nothing saved)")
    mdl = VS.own_text(SPRINT_KEY, "apps/api/services/models.py") or ""
    check(bool("'closed'" in mdl and "LIVE_MODEL_REF_SQL" in mdl and '"reopened": True' in mdl),
          "REP-10: the duplicate guard keys on a LIVE model reference; a removed model's upload displays closed")


def self_checks(check, find):
    self_task1(check, find)
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

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "perms1verify"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000052{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)

# Role grants written by hand (live 2026-11-04, BEFORE docs/perms1_part1.sql for the org admin).
ORGADMIN_BEFORE = frozenset({
    "approve_authority", "approve_blocks", "approve_execution", "approve_held_away", "approve_models",
    "approve_proposals", "approve_replication", "approve_tax", "manage_authority", "manage_blocks", "manage_execution",
    "manage_held_away", "manage_hypothetical_policy", "manage_models", "manage_org_settings", "manage_proposals",
    "manage_replication", "manage_tax", "review_agent_proposals", "view_authority", "view_blocks", "view_execution",
    "view_held_away", "view_models", "view_proposals", "view_replication", "view_tax"})
SUPPORT_PERMS = frozenset({"review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
                           "view_deals", "view_members", "view_portfolio", "view_workflow_runs"})
WORKFLOW_KEYS = ("author_workflows", "configure_workflow_triggers", "view_workflow_runs")
STAFFY_PROFILE_KEYS = ("staff", "manage_documents", "view_members", "author_workflows", "manage_deals",
                       "view_portfolio")      # view_portfolio is member-safe; the rest are not ("staff" not a key)
STAFFY_SET_KEYS = ("manage_members", "view_workflow_runs")

U_SUPER, U_STAFF, U_OA, U_SUPPORT, U_MEMBER, U_OTHER = _id(10), _id(11), _id(12), _id(13), _id(14), _id(15)
# uid → (org, users.role, short name, role name, role permissions)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", frozenset(C.INVESTMENT_STAFF_PERMS)),
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_BEFORE),
    U_SUPPORT: (ORG_A, "member", "support", "support_staff", SUPPORT_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OTHER: (ORG_B, "member", "other", "member", frozenset(C.MEMBER_PERMS)),
}
P_OA, P_STAFFY, PS_STAFFY = _id(20), _id(21), _id(22)          # fixture profiles / permission set
E_M1, E_F2, E_B, E_LLC, E_TRUST, E_JAMES = _id(30), _id(31), _id(32), _id(33), _id(34), _id(35)
W_DRAFT, WV_DRAFT, W_SYS, WV_SYS, W_RUN, WV_RUN, WR_RUN = (_id(40), _id(41), _id(42), _id(43), _id(44), _id(45),
                                                           _id(46))
W_SYS_NAME = "system:human_task:perms1_fixture"
HH_B = _id(50)
ACCOUNT_NUMBER = "PM1-0042-6789"          # typed in full; must never be stored or returned
APPROVALS = (
    (f"/api/v1/proposals/{MISSING}/approve", "approve_proposals"),
    (f"/api/v1/execution/instructions/{MISSING}/approve", "approve_execution"),
    (f"/api/v1/models/{MISSING}/versions/{MISSING}/approve", "approve_models"),
)
BPMN = ('<?xml version="1.0" encoding="UTF-8"?><definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'id="d"><process id="p" isExecutable="true"><startEvent id="s"/><userTask id="u" name="Approve"/>'
        '<endEvent id="e"/><sequenceFlow id="f1" sourceRef="s" targetRef="u"/>'
        '<sequenceFlow id="f2" sourceRef="u" targetRef="e"/></process></definitions>')
CSV_BYTES = b"ticker,weight\nPM1A,60\nPM1B,40\n"

COUNTED = ("public.users", "public.organizations", "public.entities", "public.deals", "public.households",
           "public.household_memberships", "public.accounts", "public.account_authority_settings",
           "public.entity_ownership", "public.roles", "public.user_roles", "public.role_permissions",
           "public.profiles", "public.profile_permissions", "public.permission_sets",
           "public.permission_set_permissions", "public.user_permission_sets", "public.workflow_definitions",
           "public.workflow_versions", "public.workflow_runs", "portfolio.model_uploads", "public.audit_log",
           "public.member_todos", "public.org_settings")
REAL_FINGERPRINTED = ("public.users", "public.roles", "public.user_roles", "public.role_permissions",
                      "public.profiles", "public.profile_permissions", "public.permission_sets",
                      "public.permission_set_permissions", "public.user_permission_sets", "public.entities",
                      "public.households", "public.accounts", "public.deals", "public.workflow_definitions")
# Columns real activity writes during a run: a real sign-in stamps users.last_login_at.
ACTIVITY_COLUMNS = {"public.users": ("last_login_at",)}

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
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(x) -> str:
    """For DISPLAY in a check's detail only — truncated at 400 characters."""
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


def body_text(x) -> str:
    """The FULL serialised body, for every containment assertion. PERMS1 FIX 1: asserting on dumps() (cut at 400)
    made "the household detail shows the mask" fail on a correct 4.4 KB body (its accounts sit past char 400) and
    made every "X not in" check vacuous past char 400."""
    return json.dumps(x, ensure_ascii=False, default=str)


# ═══ DB helpers (the verify's own connection; RLS context set explicitly on every read) ═══════════════════
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


async def pk_of(conn, table: str) -> list[str]:
    rows = await platform(conn, "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' "
                                "ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


def _row_expr(table: str) -> str:
    expr = "to_jsonb(t)"
    for c in ACTIVITY_COLUMNS.get(table, ()):
        expr += f" - '{c}'"
    return expr


async def fingerprint(conn, table: str) -> tuple[int, str]:
    """Count + md5 of every row minus the named ACTIVITY_COLUMNS, keyed by the table's real primary key. The
    fixture rows are torn down before the first and before the last reading, so both read real rows only."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(({_row_expr(table)})::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t", fetch="fetchrow")
    return int(row["n"]), row["h"]


async def column_hashes(conn, table: str) -> dict:
    pk = await pk_of(conn, table)
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    rows = await platform(conn, f"SELECT {key} AS k, (SELECT jsonb_object_agg(e.key, md5(e.value::text)) "
                                f"FROM jsonb_each(to_jsonb(t)) e) AS h FROM {table} t")
    return {r["k"]: json.loads(r["h"]) if isinstance(r["h"], str) else r["h"] for r in rows}


def changed_columns(before: dict, after: dict) -> list[str]:
    out = [f"{k}: row removed" for k in before if k not in after]
    out += [f"{k}: row added" for k in after if k not in before]
    for k in before:
        if k in after:
            cols = sorted(c for c in set(before[k]) | set(after[k]) if before[k].get(c) != after[k].get(c))
            if cols:
                out.append(f"{k}: {','.join(cols)}")
    return out


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only — fixture orgs and fixture users, by id (every org_id table: profiles, permission sets,
    households, accounts, deals, workflows, model uploads, audit rows, todos). Never a TRUNCATE.
    PERMS1 FIX 1: "Add account" writes REP7B account_authority_settings (and may write household_authority), whose
    authority_change_guard refuses DELETE unless public.execution_teardown_allowed() — the LOCAL GUC
    execution.teardown = 'fixture' on a non-app_service connection, exactly as verify_rep7b's teardown sets it.
    The trigger is untouched; only this run's fixture-org rows are deleted."""
    users = list(USERS)
    op = await operator_conn()
    try:
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.member_todos": ("user_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])", [users, FIXTURE_ORGS]),
            "public.user_permission_sets": ("user_id = ANY($1::uuid[])", [users]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown"), label="verify_perms1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    n = int(await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                           list(USERS), f"%{TAG}%", fetch="fetchval"))
    op = await operator_conn()
    try:
        n += await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%")
    finally:
        await op.close()
    return n


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown(conn)
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"CLEANUP_SECONDS: {_time.monotonic() - t0:.1f}")
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


# ═══ Seed ═══════════════════════════════════════════════════════════════════════════════════════════════════
ROLE_IDS: dict = {}


async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"PERMS1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        wanted = set().union(*(v[4] for v in USERS.values())) | set(PERMS1_ADDED)
        missing = sorted(wanted - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        for uid, (org, _acct, _nm, rname, perms) in USERS.items():
            if (org, rname) in ROLE_IDS:
                continue
            # rbac.ensure_role — NOT ensure_org_admin_role: the org admin role starts at the PRE-PERMS1 grants so the
            # "before" request is real; Task 3's ensure_org_admin_role runs later in y_baseline.
            rid = await rbac.ensure_role(conn, org, rname, f"PERMS1 fixture {rname}")
            for p in perms:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            ROLE_IDS[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _perms) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"PERMS1 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid,
                               ROLE_IDS[(org, rname)])
        # the "Org Admin" profile shape (the three workflow keys) on the org admin fixture
        await conn.execute("INSERT INTO profiles (id, org_id, name) VALUES ($1, $2, 'PM1 Org Admin')", P_OA, ORG_A)
        for k in WORKFLOW_KEYS:
            await conn.execute("INSERT INTO profile_permissions (org_id, profile_id, permission_key) VALUES ($1, $2, $3)",
                               ORG_A, P_OA, k)
        await conn.execute("UPDATE users SET profile_id = $1 WHERE id = $2", P_OA, U_OA)
        # a STAFF-ONLY profile (with the key "staff") and a staff-only permission set, held by the MEMBER directly
        await conn.execute("INSERT INTO profiles (id, org_id, name) VALUES ($1, $2, 'PM1 Staffy')", P_STAFFY, ORG_A)
        for k in STAFFY_PROFILE_KEYS:
            await conn.execute("INSERT INTO profile_permissions (org_id, profile_id, permission_key) VALUES ($1, $2, $3)",
                               ORG_A, P_STAFFY, k)
        await conn.execute("INSERT INTO permission_sets (id, org_id, name) VALUES ($1, $2, 'PM1 Staffy set')",
                           PS_STAFFY, ORG_A)
        for k in STAFFY_SET_KEYS:
            await conn.execute("INSERT INTO permission_set_permissions (org_id, permission_set_id, permission_key) "
                               "VALUES ($1, $2, $3)", ORG_A, PS_STAFFY, k)
        await conn.execute("UPDATE users SET profile_id = $1 WHERE id = $2", P_STAFFY, U_MEMBER)
        await conn.execute("INSERT INTO user_permission_sets (user_id, permission_set_id) VALUES ($1, $2)",
                           U_MEMBER, PS_STAFFY)
        # entities: the member's own, another family, org B; Hargrove-shaped LLC owned 60 / 40 in entity_ownership
        for eid, org, etype, name in ((E_M1, ORG_A, "individual", "PM1 Member One"),
                                      (E_F2, ORG_A, "individual", "PM1 Family Two"),
                                      (E_B, ORG_B, "individual", "PM1 Org B Person"),
                                      (E_LLC, ORG_A, "llc", "PM1 Capital LLC"),
                                      (E_TRUST, ORG_A, "trust", "PM1 Family Trust"),
                                      (E_JAMES, ORG_A, "individual", "PM1 James")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) "
                               "VALUES ($1, $2, $3::entity_type, $4)", eid, org, etype, name)
        await conn.execute("INSERT INTO entity_ownership (org_id, parent_id, child_id, ownership_pct) VALUES "
                           "($1, $2, $4, 60), ($1, $3, $4, 40)", ORG_A, E_TRUST, E_JAMES, E_LLC)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, E_M1, U_MEMBER)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_B, E_B, U_OTHER)
        # workflows: a never-run draft, a SYSTEM workflow (never run), one with a run
        for wid, vid, name in ((W_DRAFT, WV_DRAFT, "PM1 draft"), (W_SYS, WV_SYS, W_SYS_NAME),
                               (W_RUN, WV_RUN, "PM1 has a run")):
            await conn.execute("INSERT INTO workflow_definitions (id, org_id, name) VALUES ($1, $2, $3)", wid, ORG_A,
                               name)
            await conn.execute("INSERT INTO workflow_versions (id, workflow_definition_id, org_id, version_number, "
                               "bpmn_xml, is_current) VALUES ($1, $2, $3, 1, $4, true)", vid, wid, ORG_A, BPMN)
        await conn.execute("INSERT INTO workflow_runs (id, workflow_version_id, org_id) VALUES ($1, $2, $3)",
                           WR_RUN, WV_RUN, ORG_A)
        # org B household (isolation)
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, 'PM1 Org B household')",
                           HH_B, ORG_B)


async def y_fixtures(conn):
    section("[Y] Fixture orgs, users, profiles and permission sets are the script's own; orgs / users is_test")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == 2 and u == len(USERS)), "both fixture orgs and every fixture user are is_test = true",
          f"{o} / {u}")
    pr = await platform(conn, "SELECT count(*) FROM profiles WHERE id = ANY($1::uuid[]) AND org_id = $2",
                        [P_OA, P_STAFFY], ORG_A, fetch="fetchval")
    ps = await platform(conn, "SELECT count(*) FROM permission_sets WHERE id = $1 AND org_id = $2", PS_STAFFY, ORG_A,
                        fetch="fetchval")
    check(bool(pr == 2 and ps == 1), "the fixture profiles and permission set live in the fixture org", f"{pr}/{ps}")


# ═══ The real app, token stubbed by bearer string ═════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []
R2_OBJECTS: dict = {}
GEN_CALLS: list = []


def claims(uid: UUID) -> dict:
    return {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}


def token_for(c: dict) -> str:
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = dict(c)
    return key


async def api(uid, method, path, body=None, timeout=90.0):
    import main

    headers = {"Authorization": f"Bearer {token_for(claims(uid))}"}
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def install_stubs():
    """Each stub matches the CURRENT signature of what it replaces (read from the module at install time)."""
    import inspect

    import main
    from services import document_embedding, storage
    from services import users as users_mod
    from services import workflow_nl_generator as gen
    import routers.org_settings as org_settings_router

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()   # ASGITransport skips the lifespan that registers the brief blocks

    have = {"fetch_auth0_profile": list(inspect.signature(users_mod.fetch_auth0_profile).parameters),
            "enrich_with_live_info": list(inspect.signature(org_settings_router.enrich_with_live_info).parameters),
            "semantic_search": list(inspect.signature(document_embedding.semantic_search).parameters),
            "upload_bytes": list(inspect.signature(storage.upload_bytes).parameters),
            "download_bytes": list(inspect.signature(storage.download_bytes).parameters),
            "_generate_once": list(inspect.signature(gen._generate_once).parameters)}
    if (len(have["fetch_auth0_profile"]) != 2 or len(have["enrich_with_live_info"]) != 1
            or have["semantic_search"] != ["pool", "org_id", "user_id", "query", "is_staff", "limit"]
            or have["_generate_once"] != ["system", "messages", "org_id", "valid_action_keys", "valid_profile_ids"]
            or len(have["download_bytes"]) < 1 or len(have["upload_bytes"]) < 2):
        raise RuntimeError(f"stub signatures drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def no_live(rows):
        NETWORK_CALLS.append("litellm")
        return rows

    org_settings_router.enrich_with_live_info = no_live

    async def no_semantic(pool, org_id, user_id, query, *, is_staff=True, limit=20):
        return []

    document_embedding.semantic_search = no_semantic

    def fake_upload(key, data, *a, **k):
        R2_OBJECTS[key] = bytes(data)
        return key

    def fake_download(key, *a, **k):
        if key not in R2_OBJECTS:
            raise AssertionError(f"verify: R2 object {key} was never stored by this run")
        return R2_OBJECTS[key]

    def no_r2(*a, **k):
        raise AssertionError("verify: real R2 must never be called")

    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_s3_client = no_r2
    storage.delete_object = no_r2

    async def invalid_twice(system, messages, org_id, valid_action_keys, valid_profile_ids):
        GEN_CALLS.append([m.get("content", "")[:2000] for m in messages])
        return "<definitions/>", ["Service Task 'st1' references unknown action 'pm1.no_such_action'"]

    gen._generate_once = invalid_twice


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS", f"{r['u']}")


async def _before_principal_perms(conn, uid) -> set:
    """The Principal statement AT BEFORE_REF (git), executed against the fixture user — the pre-fix answer."""
    src = VS.git_show(BEFORE_REF, "apps/api/services/authz.py") or ""
    m = re.search(r'PRINCIPAL_COLUMNS = """(.*?)"""', src, re.S)
    if not m:
        raise RuntimeError("BEFORE_REF's PRINCIPAL_COLUMNS not found")
    row = await platform(conn, f"SELECT {m.group(1)} FROM users u WHERE u.id = $1", uid, "super_admin",
                         fetch="fetchrow")
    return set(row["permission_names"] or [])


async def _before_profile_perms(conn, uid) -> set:
    """BEFORE_REF's services/profiles.get_effective_permissions (profile ∪ sets, bugfix2 member rule) — its own
    SQL, from git, run against the fixture user — the pre-fix WORKFLOW gate's answer."""
    src = VS.git_show(BEFORE_REF, "apps/api/services/profiles.py") or ""
    if "FROM profile_permissions" not in src or "FROM user_permission_sets ups" not in src:
        raise RuntimeError("BEFORE_REF's profiles.py does not read the profile axis as expected")
    pid = await platform(conn, "SELECT profile_id FROM users WHERE id = $1", uid, fetch="fetchval")
    a = {r["permission_key"] for r in await platform(
        conn, "SELECT permission_key FROM profile_permissions WHERE profile_id = $1", pid)} if pid else set()
    b = {r["permission_key"] for r in await platform(
        conn, "SELECT DISTINCT psp.permission_key FROM user_permission_sets ups JOIN permission_set_permissions psp "
              "ON psp.permission_set_id = ups.permission_set_id WHERE ups.user_id = $1", uid)}
    return a | b


async def y_reproduce_and_baseline(conn):
    section("[Y] Org admin baseline — before (pre-fix answer, pre-grant role) vs after (identical requests)")
    from services import rbac

    before = await _before_principal_perms(conn, U_OA)
    check(bool(before == set(ORGADMIN_BEFORE)), "before: the BEFORE_REF Principal of the org admin fixture = its role "
          "grants only (27) — no view_portfolio, no workflow key, though its profile carries them",
          f"{sorted(before ^ set(ORGADMIN_BEFORE))}")
    check(bool(not (before & set(WORKFLOW_KEYS)) and "view_portfolio" not in before),
          "before: /users/me (the Principal) lacked author_workflows / view_workflow_runs / view_portfolio")
    menu_before = VS.git_show(BEFORE_REF, "apps/web/lib/menuVisibility.mjs") or ""
    check(bool('"/admin/workflows/runs", "Runs", GATE_SUPER_ADMIN' in menu_before),
          "before: the Runs tab was super-admin only in the menu (so the org admin's approval link was refused)")
    c1, b1 = await api(U_OA, "GET", "/api/v1/portfolio/positions")
    check(bool(c1 == 403 and "view_portfolio" in body_text(b1)),
          "REPRODUCED: org admin (role at the pre-PERMS1 grants) refused on Positions — 403 naming view_portfolio",
          f"{c1} {dumps(b1)}")
    c2, b2 = await api(U_OA, "GET", "/api/v1/admin/workflow-runs")
    check(bool(c2 == 200), "after Task 2 (profile keys in the Principal): the same org admin is admitted to the Run "
          "Console via its profile", f"{c2} {dumps(b2)}")
    find("at BEFORE_REF the workflow API also admitted this profile holder (it read the profile axis) — the Muse "
         "refusal on workflows came from the menu / role axis, recorded in Task 1")
    # Task 3 — the code path that grants the baseline to an org_admin role
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        rid = await rbac.ensure_org_admin_role(conn, ORG_A)
    check(bool(rid == ROLE_IDS[(ORG_A, "org_admin")]), "ensure_org_admin_role reused the fixture org's org_admin role")
    granted = {r["name"] for r in await platform(
        conn, "SELECT p.name FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
              "WHERE rp.role_id = $1", rid)}
    added = granted - set(ORGADMIN_BEFORE)
    print(f"    [INFO] org_admin before ({len(ORGADMIN_BEFORE)}): {sorted(ORGADMIN_BEFORE)}")
    print(f"    [INFO] org_admin after  ({len(granted)}): added {sorted(added)}")
    check(bool(added == set(PERMS1_ADDED)), "Task 3: the org_admin role gained exactly the nine baseline keys",
          f"{sorted(added)}")
    check(bool(not any(a.startswith("approve_") for a in added)), "Task 3: no new approve_* grant")
    c3, b3 = await api(U_OA, "GET", "/api/v1/portfolio/positions")
    check(bool(c3 == 200), "AFTER: the identical Positions request is admitted (200)", f"{c3} {dumps(b3)}")
    c4, b4 = await api(U_OA, "GET", "/api/v1/portfolio/commitments")
    check(bool(c4 == 200), "AFTER: Commitments admitted (200)", f"{c4} {dumps(b4)}")
    for path in ("/api/v1/admin/workflows", "/api/v1/admin/workflow-runs", "/api/v1/admin/workflow-triggers"):
        c, b = await api(U_OA, "GET", path)
        check(bool(c == 200), f"AFTER: org admin admitted to {path}", f"{c} {dumps(b)}")
    for path, perm in APPROVALS:
        c, b = await api(U_OA, "POST", path, {})
        check(bool(c != 403 or perm not in body_text(b)), f"org admin keeps its existing {perm} grant (no change)",
              f"{c} {dumps(b)}")
    # the reverse disagreement: a ROLE-only workflow key (support staff, no profile) — refused by the old gate
    old = await _before_profile_perms(conn, U_SUPPORT)
    check(bool("view_workflow_runs" not in old), "REPRODUCED (reverse case): BEFORE_REF's profile-only workflow gate "
          "would refuse support staff, whose ROLE grants view_workflow_runs", f"{sorted(old)}")
    c5, b5 = await api(U_SUPPORT, "GET", "/api/v1/admin/workflow-runs")
    check(bool(c5 == 200), "AFTER: support staff admitted to the Run Console by its role grant", f"{c5} {dumps(b5)}")


async def y_one_source(conn):
    section("[Y] One source — Principal = profiles service = rbac = /users/me, for every fixture user")
    from services import authz, profiles, rbac
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    for name, uid in (("super", U_SUPER), ("staff", U_STAFF), ("orgadmin", U_OA), ("support", U_SUPPORT),
                      ("member", U_MEMBER), ("other", U_OTHER)):
        org = USERS[uid][0]
        tok = set_rls_context(str(org), False)
        try:
            async with pool.acquire() as c:
                p = await authz.load_principal(c, uid)
            eff = await profiles.get_effective_permissions(pool, uid)
            rb = await rbac.get_user_permissions(pool, uid, org)
        finally:
            reset_rls_context(tok)
        code, me = await api(uid, "GET", "/api/v1/users/me")
        mep = set(me.get("permissions") or []) if isinstance(me, dict) else set()
        pp = set(p.permissions) if p else set()
        check(bool(p is not None and pp and pp == set(eff) == set(rb) == mep and code == 200),
              f"{name}: Principal == profiles.get_effective_permissions == rbac.get_user_permissions == /users/me",
              f"{len(pp)} / {len(eff)} / {len(rb)} / {len(mep)}; {code}")
    tok = set_rls_context(str(ORG_A), False)
    try:
        async with pool.acquire() as c:
            p = await authz.load_principal(c, U_OA)
    finally:
        reset_rls_context(tok)
    check(bool(p and set(WORKFLOW_KEYS) <= set(p.permissions)), "the org admin's profile keys are in its Principal")


async def y_client_rule(conn):
    section("[Y] Client roles gain no staff-only key; the key \"staff\" grants nothing, never the bypass")
    from services import authz
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tok = set_rls_context(str(ORG_A), False)
    try:
        async with pool.acquire() as c:
            p = await authz.load_principal(c, U_MEMBER)
            safe = await authz.member_safe_keys(c, ORG_A)
    finally:
        reset_rls_context(tok)
    perms = set(p.permissions) if p else set()
    staff_only = perms - set(safe)
    check(bool(p is not None and perms and not staff_only), "member holding a staff-only profile + set: every key in "
          "its Principal is member-safe", f"staff-only held: {sorted(staff_only)}")
    check(bool(p is not None and "staff" not in perms and p.is_staff is False and p.is_internal is False),
          "member: no 'staff' key, is_staff = false, is_internal = false")
    leaked = sorted(set(STAFFY_PROFILE_KEYS + STAFFY_SET_KEYS) & perms - set(safe))
    check(not leaked, "none of manage_documents / author_workflows / manage_deals / manage_members / "
          "view_workflow_runs reached the member", f"{leaked}")
    check(bool(p is not None and "view_portfolio" in perms), "a member-safe profile key (view_portfolio) is kept")
    c, b = await api(U_MEMBER, "POST", "/api/v1/deals", {"name": "PM1 member deal"})
    check(bool(c == 403), "member refused deal creation even with manage_deals on its profile / set", f"{c} {dumps(b)}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/admin/workflows")
    check(bool(c == 403), "member refused the workflow library (author_workflows on its profile is dropped)",
          f"{c} {dumps(b)}")


async def y_support_and_bypass(conn):
    section("[Y] Support staff: not staff, reads admitted, approvals refused; bypass set unchanged")
    from services import authz
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tok = set_rls_context(str(ORG_A), False)
    try:
        async with pool.acquire() as c:
            sp = await authz.load_principal(c, U_SUPPORT)
            st = await authz.load_principal(c, U_STAFF)
    finally:
        reset_rls_context(tok)
    check(bool(sp is not None and sp.is_staff is False and sp.is_internal is True), "support: is_staff false, internal")
    for path in ("/api/v1/portfolio/positions", f"/api/v1/entities/{E_F2}/full", "/api/v1/admin/workflow-runs"):
        c, b = await api(U_SUPPORT, "GET", path)
        check(bool(c == 200), f"support admitted on its read {path}", f"{c} {dumps(b)}")
    for path, perm in APPROVALS:
        c, b = await api(U_SUPPORT, "POST", path, {})
        check(bool(c == 403 and perm in body_text(b)), f"support refused {perm} (403 naming it)", f"{c} {dumps(b)}")
    check(bool(st is not None and st.is_staff is True and set(st.permissions) == set(C.INVESTMENT_STAFF_PERMS)),
          "investment_staff fixture: is_staff true; its permissions are exactly its role grants (as before)",
          f"{sorted(set(st.permissions) ^ set(C.INVESTMENT_STAFF_PERMS)) if st else None}")
    c, b = await api(U_STAFF, "GET", f"/api/v1/entities/{E_F2}/full")
    check(bool(c == 200), "investment_staff still reads unscoped (bypass behaves as before)", f"{c}")


class _Counting:
    """Wraps an asyncpg connection; counts every statement issued through it."""

    def __init__(self, conn):
        self._c, self.n = conn, 0

    def __getattr__(self, name):
        attr = getattr(self._c, name)
        if name in ("fetch", "fetchrow", "fetchval", "execute"):
            async def wrapped(*a, **k):
                self.n += 1
                return await attr(*a, **k)
            return wrapped
        return attr


async def y_one_statement(conn):
    section("[Y] The Principal is still ONE statement")
    from services import authz

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        for uid in (U_OA, U_MEMBER, U_SUPER):
            cc = _Counting(conn)
            p = await authz.load_principal(cc, uid)
            check(bool(p is not None and cc.n == 1), f"load_principal({USERS[uid][2]}) issued exactly 1 statement",
                  f"{cc.n}")


async def y_deals(conn):
    section("[Y] Deal creation (decision 1) — org admin with manage_deals; members / internal without refused")
    c, b = await api(U_OA, "POST", "/api/v1/deals", {"name": "PM1 Org admin deal", "description": "d"})
    deal_id = b.get("id") if isinstance(b, dict) else None
    check(bool(c == 201 and deal_id and str(b.get("deal_status")) == "draft"),
          "org admin creates a deal — it starts as a draft", f"{c} {dumps(b)}")
    c2, b2 = await api(U_SUPPORT, "POST", "/api/v1/deals", {"name": "PM1 support deal"})
    check(bool(c2 == 403 and "manage_deals" in body_text(b2)), "support staff (internal, no manage_deals) refused naming "
          "manage_deals", f"{c2} {dumps(b2)}")
    if deal_id:
        c3, _ = await api(U_MEMBER, "GET", f"/api/v1/deals/{deal_id}")
        c4, b4 = await api(U_MEMBER, "GET", "/api/v1/deals")
        ids = [d.get("id") for d in b4] if isinstance(b4, list) else None
        check(bool(c3 == 404 and c4 == 200 and ids is not None and deal_id not in ids),
              "the draft is invisible to a member (404 single; absent from the list)", f"{c3} {c4}")
        c5, b5 = await api(U_OA, "GET", f"/api/v1/deals/{deal_id}")
        check(bool(c5 == 200), "the org admin (internal) sees its draft", f"{c5}")
        n = await scoped(conn, ORG_A, "SELECT count(*) FROM deals WHERE id = $1", UUID(deal_id), fetch="fetchval")
        check(bool(n >= 1), "the deal persisted (re-read on an independent RLS-scoped connection)", f"{n}")
    nb = await scoped(conn, ORG_A, "SELECT count(*) FROM deals WHERE name = 'PM1 support deal' OR "
                                   "name = 'PM1 member deal'", fetch="fetchval")
    check(bool(nb == 0), "the refused creations wrote nothing", f"{nb}")


async def y_households(conn):
    section("[Y] Households (decision 2) — create with CRM members, add an account (mask + hash), refusals")
    from services.custody.base import AccountNumber
    from services.custody.registry import get_or_create_salt

    c, b = await api(U_MEMBER, "POST", "/api/v1/admin/households", {"name": "PM1 member hh", "member_entity_ids": []})
    check(bool(c == 403), "member refused household creation", f"{c} {dumps(b)}")
    c, b = await api(U_SUPPORT, "POST", "/api/v1/admin/households", {"name": "PM1 support hh"})
    check(bool(c == 403 and "manage_members" in body_text(b)), "support staff (internal, no manage_members) refused naming "
          "manage_members", f"{c} {dumps(b)}")
    c, env = await api(U_SUPPORT, "GET", "/api/v1/admin/households")
    check(bool(c == 403), "support staff gets no management envelope (403)", f"{c}")
    c, env = await api(U_OA, "GET", "/api/v1/admin/households")
    perms = env.get("permissions") if isinstance(env, dict) else None
    check(bool(c == 200 and isinstance(perms, dict) and perms.get("can_write") is True
               and isinstance((env.get("vocabularies") or {}).get("editable"), list)
               and bool((env.get("vocabularies") or {}).get("editable"))),
          "org admin: the envelope says can_write true with a non-empty editable list", f"{c} {dumps(env)}")
    c, b = await api(U_OA, "POST", "/api/v1/admin/households",
                     {"name": "PM1 Hargrove household", "member_entity_ids": [str(E_M1), str(E_F2)]})
    hid = b.get("id") if isinstance(b, dict) else None
    check(bool(c == 201 and hid and len(b.get("members") or []) == 2), "org admin creates a household linking two CRM "
          "entities", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "POST", "/api/v1/admin/households", {"name": "PM1 bad", "member_entity_ids": [str(E_B)]})
    check(bool(c == 422), "an entity of another org cannot be linked (422)", f"{c} {dumps(b)}")
    if not hid:
        return
    mem = await scoped(conn, ORG_A, "SELECT count(*) FROM household_memberships WHERE household_id = $1", UUID(hid),
                       fetch="fetchval")
    check(bool(mem == 2), "both member links persisted", f"{mem}")
    body = {"custodian_code": "PM1CUST", "account_number": ACCOUNT_NUMBER, "primary_entity_id": str(E_M1),
            "authority": "managed", "custody": "integrated"}
    c, b = await api(U_MEMBER, "POST", f"/api/v1/admin/households/{hid}/accounts", body)
    check(bool(c in (403, 404)), "member refused Add account", f"{c}")
    c, b = await api(U_SUPPORT, "POST", f"/api/v1/admin/households/{hid}/accounts", body)
    check(bool(c == 403), "support staff refused Add account", f"{c}")
    c, b = await api(U_OA, "POST", f"/api/v1/admin/households/{hid}/accounts", body)
    check(bool(c == 201 and isinstance(b, dict) and b.get("account_number_masked") == "*******6789"
               and ACCOUNT_NUMBER not in body_text(b) and "00426789" not in body_text(b) and b.get("pending_change") is None),
          "org admin adds an account at the default authority / custody; the response carries the mask only",
          f"{c} {dumps(b)}")
    acct_id = b.get("id") if isinstance(b, dict) else None
    if acct_id:
        row = await scoped(conn, ORG_A, "SELECT account_number_masked, account_number_hash, household_id, "
                                        "is_held_away FROM accounts WHERE id = $1", UUID(acct_id), fetch="fetchrow")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
            salt = await get_or_create_salt(conn, ORG_A)
        want = AccountNumber(ACCOUNT_NUMBER).hashed(salt)
        check(bool(row and row["account_number_hash"] == want and str(row["household_id"]) == hid
                   and row["is_held_away"] is False),
              "stored: the keyed hash of the typed number (as the custody import computes it), on this household")
        allcols = await scoped(conn, ORG_A, "SELECT to_jsonb(a)::text FROM accounts a WHERE id = $1", UUID(acct_id),
                               fetch="fetchval")
        check(bool(allcols and "0042" not in allcols and "PM1-0042" not in allcols),
              "the full account number is stored nowhere on the row")
        c, d = await api(U_OA, "GET", f"/api/v1/admin/households/{hid}")
        full = body_text(d)
        shown = [a for a in ((d.get("accounts") if isinstance(d, dict) else None) or []) if a.get("id") == acct_id]
        stored_mask = row["account_number_masked"] if row else None
        print(f"    [INFO] detail: {len(full)} chars; this account's fields: "
              f"{json.dumps(shown, default=str) if shown else 'ABSENT'}")
        check(bool(c == 200 and stored_mask and len(shown) == 1 and shown[0].get("account_number_masked") == stored_mask
                   and stored_mask in full and "0042" not in full and ACCOUNT_NUMBER not in full
                   and "00426789" not in full),
              "the household detail shows the stored mask, never the full number (whole body searched)",
              f"{c} stored={stored_mask!r} shown={json.dumps(shown, default=str)}")
        c, b2 = await api(U_OA, "POST", f"/api/v1/admin/households/{hid}/accounts", body)
        check(bool(c == 409), "the same account again is refused (409) — the import's unique key", f"{c} {dumps(b2)}")
    body2 = {"custodian_code": "PM1OUT", "account_number": "PM1-7777-1234", "primary_entity_id": str(E_F2),
             "authority": "advise", "custody": "outside", "reason": "advice only"}
    c, b = await api(U_OA, "POST", f"/api/v1/admin/households/{hid}/accounts", body2)
    pend = b.get("pending_change") if isinstance(b, dict) else None
    check(bool(c == 201 and isinstance(pend, dict) and b.get("authority") == "read_only"),
          "a non-default authority (advise, held outside) is NOT applied directly: the account starts at REP7B's "
          "default and the choice is a draft change awaiting a second person", f"{c} {dumps(b)}")
    audit = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action LIKE 'household.%'",
                           ORG_A, fetch="fetchval")
    check(bool(audit >= 3), "household creation and each added account wrote an audit row", f"{audit}")
    c, b = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_B}")
    check(bool(c == 404), "org isolation: org A's admin cannot open org B's household (404)", f"{c}")


async def y_workflow_delete(conn):
    section("[Y] WF-06 — no Delete on system workflows or workflows with runs; API refuses both")
    c, rows = await api(U_OA, "GET", "/api/v1/admin/workflows")
    by = {r["id"]: r for r in rows} if isinstance(rows, list) else {}
    sysrow, draft, run = by.get(str(W_SYS)), by.get(str(W_DRAFT)), by.get(str(W_RUN))
    check(bool(c == 200 and sysrow and sysrow.get("can_delete") is False and "system workflow" in
               str(sysrow.get("delete_refusal") or "")), "the system workflow's row: can_delete false + the reason",
          f"{c} {dumps(sysrow)}")
    check(bool(run and run.get("can_delete") is False), "the workflow with a run: can_delete false", f"{dumps(run)}")
    check(bool(draft and draft.get("can_delete") is True), "the never-run draft: can_delete true", f"{dumps(draft)}")
    c, b = await api(U_OA, "DELETE", f"/api/v1/admin/workflows/{W_SYS}")
    check(bool(c == 409 and W_SYS_NAME in body_text(b)), "API refuses deleting the system workflow (409 naming it)",
          f"{c} {dumps(b)}")
    c, b = await api(U_OA, "DELETE", f"/api/v1/admin/workflows/{W_RUN}")
    check(bool(c == 409), "API refuses deleting a workflow with runs (409)", f"{c} {dumps(b)}")
    c, b = await api(U_MEMBER, "DELETE", f"/api/v1/admin/workflows/{W_DRAFT}")
    check(bool(c == 403), "member refused deleting", f"{c}")
    c, b = await api(U_OA, "DELETE", f"/api/v1/admin/workflows/{W_DRAFT}")
    gone = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_definitions WHERE id = $1", W_DRAFT,
                        fetch="fetchval")
    left = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_definitions WHERE id = ANY($1::uuid[])",
                        [W_SYS, W_RUN], fetch="fetchval")
    check(bool(c == 200 and gone == 0 and left == 2), "the never-run draft is deleted; the system and run workflows "
          "remain", f"{c} {gone} {left}")
    lib = (WEB_DIR / "lib" / "workflowLibrary.mjs").read_text(encoding="utf-8")
    comp = (WEB_DIR / "components" / "admin" / "WorkflowLibraryManager.jsx").read_text(encoding="utf-8")
    check(bool("export function deleteConfirmText" in lib and "{deleteConfirmText(w)}" in comp
               and "canDeleteWorkflow(w) ?" in comp), "the library renders Delete only on can_delete and confirms with "
          "the workflow's name (node test covers the render logic)")


async def y_graph(conn):
    section("[Y] WF-03 — the hierarchy graph's tree carries the owners and their edges (both look-through states)")
    # before: BEFORE_REF's get_subtree read entity_relationships only, downward → nothing for the LLC
    old = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE org_id = $1 AND "
                                    "from_entity_id = $2 AND relationship_type = 'ownership'", ORG_A, E_LLC,
                       fetch="fetchval")
    src = VS.git_show(BEFORE_REF, "apps/api/services/entity_graph.py") or ""
    check(bool(old == 0 and "\"owners\"" not in src and "FROM entity_relationships er" in src),
          "REPRODUCED: BEFORE_REF's tree read entity_relationships downward only — the LLC had no node to show")
    c, t = await api(U_OA, "GET", f"/api/v1/entities/{E_LLC}/tree")
    owners = {o["id"]: o for o in (t.get("owners") or [])} if isinstance(t, dict) else {}
    check(bool(c == 200 and set(owners) == {str(E_TRUST), str(E_JAMES)}),
          "the tree's owners are the Trust and James (entity_ownership, upward)", f"{c} {dumps(t)}")
    check(bool(owners and float(owners[str(E_TRUST)]["ownership_pct"]) == 60.0
               and float(owners[str(E_JAMES)]["ownership_pct"]) == 40.0
               and float(owners[str(E_TRUST)]["effective_pct"]) == 60.0),
          "each owner edge carries its stake (60 / 40) and its look-through share", f"{dumps(owners)}")
    c2, g = await api(U_OA, "GET", f"/api/v1/entities/{E_LLC}/ownership-graph")
    parents = {str(e.get("parent_id")) for e in (g.get("edges") or [])
               if str(e.get("child_id")) == str(E_LLC)} if isinstance(g, dict) else set()
    check(bool(c2 == 200 and parents == set(owners)), "the graph's owners match the detail page's edges exactly",
          f"{parents}")
    c3, lt = await api(U_OA, "GET", f"/api/v1/entities/{E_TRUST}/lookthrough")
    ids = {x["entity_id"] for x in (lt.get("lookthrough") or [])} if isinstance(lt, dict) else set()
    check(bool(c3 == 200 and str(E_LLC) in ids), "look-through ON: the Trust's look-through reaches the LLC (both "
          "tables)", f"{c3} {dumps(lt)}")
    comp = (WEB_DIR / "components" / "crm" / "HierarchyBuilder.jsx").read_text(encoding="utf-8")
    check(bool("layoutOwners(treeData" in comp and "ownerLayout.nodes.map" in comp),
          "HierarchyBuilder draws the owners and owner edges regardless of the look-through toggle")


async def y_rep10(conn):
    section("[Y] REP-10 — re-upload allowed when no live model uses the earlier upload; true duplicate blocked")
    from services import models as M
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tok = set_rls_context(str(ORG_A), False)
    try:
        r1 = await M.receive_upload(pool, org_id=ORG_A, actor_id=U_OA, data=CSV_BYTES, filename="pm1-model.csv",
                                    content_type="text/csv")
        up1 = (r1.get("upload") or {}).get("id")
        check(bool(up1 and (r1.get("upload") or {}).get("outcome") == "received"), "first upload received",
              f"{dumps(r1)}")
        live = await M.upload_live_model(await _acq(pool), ORG_A, up1) if up1 else "x"
        check(bool(live is None), "no live model references it")
        r2 = await M.receive_upload(pool, org_id=ORG_A, actor_id=U_OA, data=CSV_BYTES, filename="pm1-model.csv",
                                    content_type="text/csv")
        check(bool(r2.get("reopened") is True and (r2.get("upload") or {}).get("id") == up1
                   and not r2.get("duplicate_of") and r2.get("sheets")),
              "re-uploading the same bytes is ALLOWED: the earlier upload is re-opened with its wizard",
              f"{dumps(r2)}")
        dups = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_uploads WHERE org_id = $1 AND "
                                         "outcome = 'duplicate'", ORG_A, fetch="fetchval")
        check(bool(dups == 0), "no 'Duplicate' log entry was written for the allowed re-upload", f"{dups}")
        real_upload = M.upload_live_model

        async def pretend_live(conn_, org_id, upload_id):
            return MISSING

        M.upload_live_model = pretend_live
        try:
            r3 = await M.receive_upload(pool, org_id=ORG_A, actor_id=U_OA, data=CSV_BYTES, filename="pm1-model.csv",
                                        content_type="text/csv")
        finally:
            M.upload_live_model = real_upload
        check(bool(r3.get("duplicate_of") and (r3.get("upload") or {}).get("outcome") == "duplicate"),
              "a TRUE duplicate (a live model references the earlier upload) is still blocked and logged",
              f"{dumps(r3)}")
    finally:
        reset_rls_context(tok)
    # the live-reference SQL itself, on real data (read-only): a live version with an upload → its model
    real = await platform(conn, f"SELECT v.upload_id, v.model_id FROM {M.T_VER} v JOIN {M.T_MODEL} m ON m.id = "
                                "v.model_id AND m.system_to IS NULL WHERE v.upload_id IS NOT NULL AND v.system_to IS "
                                "NULL AND v.status NOT IN ('abandoned', 'rejected') AND m.status <> 'retired' "
                                "AND v.org_id <> ALL($1::uuid[]) LIMIT 1", FIXTURE_ORGS, fetch="fetchrow")
    if real is None:
        find("no real live model version references an upload today — the live-reference SQL is proven on the "
             "fixture's negative case and the patched positive case only")
    else:
        org = await platform(conn, f"SELECT org_id FROM {M.T_VER} WHERE upload_id = $1 LIMIT 1", real["upload_id"],
                             fetch="fetchval")
        got = await platform(conn, M.LIVE_MODEL_REF_SQL, real["upload_id"], str(org), fetch="fetchval")
        check(bool(got == real["model_id"]), "LIVE_MODEL_REF_SQL finds a real live model's upload", f"{got}")
    lab = M.UPLOAD_OUTCOME_LABELS.get("closed")
    check(bool(lab and "In progress" not in lab), "a removed model's upload displays as closed, not 'In progress'",
          f"{lab}")


async def _acq(pool):
    """A connection for one read inside the RLS pool's transaction (released by the pool on exit)."""
    class _One:
        async def fetchval(self, *a):
            async with pool.acquire() as c:
                return await c.fetchval(*a)
    return _One()


async def y_fees(conn):
    section("[Y] WF-04 — fee screens: internal + view_portfolio reads, members refused, menu entries")
    for path in ("/api/v1/profitability/pnl", "/api/v1/fee-schedules", "/api/v1/fee-chat/conversation"):
        c, b = await api(U_MEMBER, "GET", path)
        check(bool(c == 403), f"member (holds view_portfolio) refused {path}", f"{c} {dumps(b)}")
        c, b = await api(U_OA, "GET", path)
        check(bool(c not in (401, 403)), f"org admin admitted to {path}", f"{c} {dumps(b)}")
    menu = (WEB_DIR / "lib" / "menuVisibility.mjs").read_text(encoding="utf-8")
    check(bool('"/fee-schedules/chat", "Fee schedules", GATE_FIRM_PORTFOLIO' in menu
               and '"/profitability", "Profitability", GATE_FIRM_PORTFOLIO' in menu),
          "the Billing & Custody group lists the fee screens (node tests: shown to org admin / support, never member)")


async def y_generator(conn):
    section("[Y] WF-05 — the generator retries once with the validator's error, then shows the reason")
    before = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_definitions WHERE org_id = $1", ORG_A,
                          fetch="fetchval")
    GEN_CALLS.clear()
    c, b = await api(U_OA, "POST", "/api/v1/admin/workflows", {"description": "do the thing"})
    after = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_definitions WHERE org_id = $1", ORG_A,
                         fetch="fetchval")
    detail = b.get("detail") if isinstance(b, dict) else None
    check(bool(len(GEN_CALLS) == 2 and any("pm1.no_such_action" in m for m in GEN_CALLS[1])),
          "two attempts; the second carries the validator's error", f"{len(GEN_CALLS)}")
    check(bool(c == 422 and isinstance(detail, str) and detail.startswith("The workflow was not saved")
               and "pm1.no_such_action" in detail), "422 with the plain reason (validator's own words)",
          f"{c} {dumps(b)}")
    check(bool(after == before), "nothing was saved", f"{before} → {after}")


async def y_test_rows(conn):
    section("[Y] Task 5 — the HollisMuse test rows are gone")
    w = await platform(conn, "SELECT count(*) FROM workflow_definitions WHERE id = $1", MUSE_WORKFLOW, fetch="fetchval")
    v = await platform(conn, "SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $1", MUSE_WORKFLOW,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM portfolio.model_uploads WHERE id = ANY($1::uuid[])", MUSE_UPLOADS,
                       fetch="fetchval")
    check(bool(w == 0 and v == 0 and u == 0), "zero MUSE workflow / version / upload rows remain", f"{w} {v} {u}")
    src = (HERE / "perms1_remove_muse_rows.py").read_text(encoding="utf-8")
    check(bool("models.teardown" in src and "count + md5" in src and "DRY RUN" in src),
          "the removal used the documented privileged path, by id, with count + checksum (trigger not weakened)")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (app_service, rolbypassrls = false)")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a = {x["id"] for x in await scoped(conn, ORG_A, "SELECT id FROM profiles WHERE id = ANY($1::uuid[])",
                                       [P_OA, P_STAFFY])}
    b = {x["id"] for x in await scoped(conn, ORG_B, "SELECT id FROM profiles WHERE id = ANY($1::uuid[])",
                                       [P_OA, P_STAFFY])}
    check(bool(a == {P_OA, P_STAFFY} and b == set()), "RLS: org A's profiles are invisible to org B", f"{a} / {b}")
    hb = {x["id"] for x in await scoped(conn, ORG_A, "SELECT id FROM households WHERE id = $1", HH_B)}
    check(bool(not hb), "RLS: org B's household is invisible to org A")
    c, me = await api(U_OTHER, "GET", "/api/v1/users/me")
    perms = set(me.get("permissions") or []) if isinstance(me, dict) else set()
    check(bool(c == 200 and perms == set(C.MEMBER_PERMS)), "org B's member: its Principal holds only its own role's "
          "grants (org A's profiles never apply)", f"{sorted(perms ^ set(C.MEMBER_PERMS))}")
    c, b = await api(U_OTHER, "GET", f"/api/v1/entities/{E_LLC}/tree")
    check(bool(c == 403), "org B's member on org A's tree: 403 (not internal)", f"{c}")


async def y_routes(conn):
    section("[Y] Routes — own changes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: the route record is CURRENT",
          (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(bool(code == 0 and m is not None and m.group(1) == "0"), "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_staffroles1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_staffroles1.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_staffroles1.py: 0 FAIL",
          f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}; today {date.today().isoformat()}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    real_cols = {}
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = {t: await fingerprint(conn, t) for t in REAL_FINGERPRINTED}
        real_cols = {t: await column_hashes(conn, t) for t in ACTIVITY_COLUMNS}
        await run("role", y_role_is_real, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        section("[Y] Task 1 — where permissions are computed, and from what (1a table, 1b–1f findings)")
        self_task1(check, find)
        await run("reproduce + baseline", y_reproduce_and_baseline, conn)
        await run("one source", y_one_source, conn)
        await run("client rule", y_client_rule, conn)
        await run("support + bypass", y_support_and_bypass, conn)
        await run("one statement", y_one_statement, conn)
        await run("deals", y_deals, conn)
        await run("households", y_households, conn)
        await run("workflow delete", y_workflow_delete, conn)
        await run("graph", y_graph, conn)
        await run("rep10", y_rep10, conn)
        await run("fees", y_fees, conn)
        await run("generator", y_generator, conn)
        await run("test rows", y_test_rows, conn)
        await run("isolation", y_isolation, conn)
        section("[Y] Static — bypass set, Principal statement, grants, docs, regression set")
        self_static(check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                for t in REAL_FINGERPRINTED:
                    now = await fingerprint(conn, t)
                    excl = ACTIVITY_COLUMNS.get(t, ())
                    check(now == real[t], f"real {t} unchanged (count + checksum by primary key"
                          + (f"; excluding activity column(s) {', '.join(excl)}" if excl else "") + ")",
                          f"{real[t][0]} → {now[0]} rows")
                for t, before_cols in real_cols.items():
                    diff = changed_columns(before_cols, await column_hashes(conn, t))
                    print(f"[INFO] real {t} rows changed during the run (id: column names; no values): "
                          f"{diff or 'none'}")
                    off = [d for d in diff if not set(d.split(': ', 1)[1].split(',')) <= set(ACTIVITY_COLUMNS[t])]
                    check(not off, f"real {t}: nothing but {', '.join(ACTIVITY_COLUMNS[t])} changed on any row",
                          f"{off}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
        finally:
            await conn.close()

    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        await run("regression", regression)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
