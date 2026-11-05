"""verify_staffroles1 — "may SEE staff data" (is_internal) split from "skips permission checks" (is_staff).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure.
AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from Doppler over HTTPS
(``_db_connect.hydrate``) before ``main`` is imported. No network: the LiteLLM live-info enrichment, the embedding
call and R2 are replaced by local stubs (a real call fails the run).

Everything after the bearer token is the real app (middleware, Principal, every gate, every handler) against the
live database, in TWO fixture orgs (99000000-…-000000510001 / 510002), ``is_test = true`` (asserted), as are the
fixture users — one per role tested: super admin, investment_staff (staff = bypass), org_admin WITHOUT any approve_*
permission, support_staff (its live grants), advisor (its live grants), member (delegate of family 1), an
UNCLASSIFIED role holding view_members + view_spv, and org B's member. Two families in org A: family 1 (the
member's) and family 2 (another family — note, compliance record, document, entity group).

Teardown at start and end: the shared scoped cleanup (_verify_cleanup) on the operator DSN, by fixture org / user
ids; then every counted table is re-read for its exact pre-test count, and the REAL rows of users / roles / grants /
profiles / entities are fingerprinted (count + md5 by the catalog primary key; users excluding last_login_at only,
which a real sign-in writes) before and after — they must be identical. No real user's role is touched.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
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

SPRINT_KEY = "staffroles1"
BEFORE_REF = "e1feebf"   # main after "merge bugfix2" — the pre-sprint code
ROUTES_JSON = REPO / "docs" / "AUTHZ1_ROUTES.json"

# ── Task 1b — the hand table: every staff-data READ route this sprint re-gated, and what a NON-staff internal role
# also needs. Written by hand from the discovery, never derived from the record (the record is checked against it).
#   ("internal",)              any internal firm role
#   ("internal_perm", perms)   an internal role holding one of perms; staff (bypass) and Super Admin as before
CRM = ("internal",)
SPV = ("internal_perm", ("view_spv",))
LEDGER = ("internal_perm", ("manage_ledger", "view_spv"))
FIRM_PORTFOLIO = ("internal_perm", ("view_portfolio",))
ROUTE_EXPECT = {
    # BUGFIX2's 24 staff-only reads
    "/api/v1/entities/{entity_id}/full": CRM,
    "/api/v1/entities/{entity_id}/notes": CRM,
    "/api/v1/entities/{entity_id}/ownership-graph": CRM,
    "/api/v1/entities/{entity_id}/tree": CRM,
    "/api/v1/entities/{entity_id}/lookthrough": CRM,
    "/api/v1/entities/{entity_id}/relationships": CRM,
    "/api/v1/entities/{entity_id}/ownership": CRM,
    "/api/v1/entities/{entity_id}/ownership/history": CRM,
    "/api/v1/entity-groups": CRM,
    "/api/v1/entity-groups/{group_id}": CRM,
    "/api/v1/ledger/accounts": LEDGER,
    "/api/v1/ledger/entries": LEDGER,
    "/api/v1/ledger/trial-balance": LEDGER,
    "/api/v1/ledger/capital-accounts": LEDGER,
    "/api/v1/ledger/templates": LEDGER,
    "/api/v1/spvs": SPV,
    "/api/v1/spvs/{spv_id}": SPV,
    "/api/v1/spvs/{spv_id}/documents": SPV,
    "/api/v1/spvs/{spv_id}/documents/{doc_id}/download": SPV,
    "/api/v1/spvs/{spv_id}/transactions": SPV,
    "/api/v1/documents/{document_id}/links": CRM,
    "/api/v1/records/{record_type}/{record_id}/documents": CRM,
    "/api/v1/document-link-proposals": CRM,
    "/api/v1/portfolio/precedence": CRM,
    # the staff ownership tree (BUGFIX2's own path; was already staff-only)
    "/api/v1/entities/{entity_id}/ownership-tree": CRM,
    # BUGFIX2's billing / custody reads (require_staff on top of view_portfolio)
    "/api/v1/billing-groups": FIRM_PORTFOLIO,
    "/api/v1/billing-groups/accounts/{account_id}/memberships": FIRM_PORTFOLIO,
    "/api/v1/billing-groups/{group_id}/members": FIRM_PORTFOLIO,
    "/api/v1/custody/batches": FIRM_PORTFOLIO,
    "/api/v1/custody/batches/{batch_id}": FIRM_PORTFOLIO,
    "/api/v1/custody/profiles": FIRM_PORTFOLIO,
}
BUGFIX2_STAFF_ONLY_24 = [p for p in list(ROUTE_EXPECT)[:24]]
# The eight non-staff internal roles this sprint names (its own constant, not live state).
OWN_INTERNAL_ADDED = ("org_admin", "support_staff", "advisor", "compliance", "fund_finance", "investment_committee",
                      "ir_member_relations", "member_manager")
# BUGFIX2's 3 staff-or-org-admin settings reads — UNCHANGED (staff or manage_org_settings; support staff is refused).
STAFF_OR_ORGADMIN = ("/api/v1/admin/model-catalog", "/api/v1/admin/system-one-catalog",
                     "/api/v1/modeling/ta/calibration-floor")

# ── Task 1a — every use of is_staff / require_staff / isStaffStrict, and what it MEANS. "see" moved to is_internal /
# require_internal; "bypass" (skip a permission check / act) stayed. (file, needle that must be present at the
# sprint's end, meaning). Line numbers are printed from the file, never frozen.
TASK1A = [
    ("apps/api/services/authz.py", "is_staff=bool(admitted and (is_super or STAFF_ROLE_NAMES.intersection(roles)))",
     "bypass — Principal.is_staff, unchanged"),
    ("apps/api/services/authz.py", "def require_staff(request: Request) -> Principal:", "bypass — require_staff, kept"),
    ("apps/api/services/authz.py", "if p.is_super_admin or p.is_staff or permission in p.permissions:",
     "bypass — require_staff_or_permission, kept"),
    ("apps/api/services/authz.py", "if _sees_unscoped(request, p):",
     "see — require_entity_access / member_entity_scope: internal unscoped on READ, staff on write"),
    ("apps/api/services/authz.py", "p.is_super_admin or p.is_staff or p.is_internal or rbac.ORG_ADMIN_PERMISSION",
     "see — is_member_principal (an internal role's own profile is not stripped)"),
    ("apps/api/routers/entities.py", "principal = require_internal(request)", "see — /entities/{id}/full"),
    ("apps/api/routers/entity_graph.py", "require_internal(request)  # STAFFROLES1", "see — tree/graph/group reads"),
    ("apps/api/routers/entity_graph.py", "    require_staff(request)\n", "bypass/act — relationship, ownership, "
     "group WRITES keep require_staff"),
    ("apps/api/routers/ownership_tree.py", "require_internal(request)", "see — staff ownership tree"),
    ("apps/api/routers/document_links.py", "require_internal(request)", "see — document links reads"),
    ("apps/api/routers/document_links.py", "staff = is_internal(request)", "see — document search scope"),
    ("apps/api/routers/portfolio_ingest.py", "require_internal(request)", "see — precedence read"),
    ("apps/api/routers/spv.py", 'require_internal_permission(request, "view_spv")', "see — SPV reads (+view_spv)"),
    ("apps/api/routers/spv.py", "staff = is_internal(request)", "see — SPV status / transaction visibility"),
    ("apps/api/routers/spv.py", "if not (principal.is_super_admin or principal.is_staff):",
     "bypass — subscribe on behalf of an entity without indicate_interest, kept"),
    ("apps/api/routers/ledger.py", 'require_internal_permission(request, "view_spv", "manage_ledger")',
     "see — ledger reads (+view_spv|manage_ledger)"),
    ("apps/api/routers/billing_groups.py", 'require_internal_permission(request, "view_portfolio")',
     "see — billing reads (+view_portfolio)"),
    ("apps/api/routers/custody_import.py", 'require_internal_permission(request, "view_portfolio")',
     "see — custody import reads (+view_portfolio)"),
    ("apps/api/routers/investment_profile.py", "    require_staff(request)\n", "bypass/act — extraction / brief "
     "WRITES keep require_staff"),
    ("apps/api/routers/portfolio.py", "    require_staff(request)\n", "bypass/act — target WRITES keep require_staff"),
    ("apps/api/routers/marketplace.py", "staff = is_internal(request)", "see — draft deals visible to the firm"),
    ("apps/api/routers/allocation_lens.py", "staff = is_internal(request)", "see — lens scope"),
    ("apps/api/routers/semantic_search.py", "staff = is_internal(request)", "see — search scope"),
    ("apps/api/routers/portfolio_positions.py", "is_internal(request)))", "see — commitments scope"),
    ("apps/api/routers/assistant.py", "caller_is_staff = is_internal(request)", "see — count/query actions' scope"),
    ("apps/api/routers/document_review.py", "principal.is_super_admin or principal.is_internal", "see — doc visibility"),
    ("apps/api/routers/xray.py", "principal.is_super_admin or principal.is_internal or ORG_ADMIN", "see — X-ray"),
    ("apps/api/routers/replication_notes.py", "principal.is_super_admin or principal.is_internal or ORG_ADMIN",
     "see — hypothetical figures"),
    ("apps/api/routers/users.py", "is_internal=principal.is_internal", "see — /users/me publishes is_internal"),
    ("apps/api/routers/ai_ensembles.py", 'require_staff_or_permission(request, "manage_org_settings")',
     "settings read — unchanged (staff or org admin)"),
    ("apps/web/lib/menuVisibility.mjs", "export function isInternalStrict(me)", "see — staff screens"),
    ("apps/web/lib/menuVisibility.mjs", 'main(20, "/crm", "CRM", "crm", GATE_CRM)', "see — CRM menu item"),
    ("apps/web/lib/menuVisibility.mjs", "export function isStaffStrict(me)", "bypass mirror — kept for staff gates"),
]


def _rec_rule(rec: dict) -> tuple:
    e = rec.get("expect") or {}
    if e.get("rule") == "internal":
        return ("internal",)
    if e.get("rule") == "internal_perm":
        return ("internal_perm", tuple(sorted(e.get("any_of") or [])))
    return (e.get("rule"), rec.get("after"))


def self_task1(check, find):
    bad = []
    for rel, needle, meaning in TASK1A:
        txt = VS.own_text(SPRINT_KEY, rel) or ""
        idx = txt.find(needle)
        line = txt.count("\n", 0, idx) + 1 if idx >= 0 else None
        print(f"    {('SEE' if meaning.startswith('see') else 'BYPASS/OTHER'):13s} {rel}:{line}  {meaning}")
        if idx < 0:
            bad.append((rel, needle[:50]))
    check(not bad, "Task 1a: every listed use of is_staff / require_staff / isStaffStrict is classified see vs "
          "bypass and the code at the sprint's end matches its class", f"{bad}")
    end_ref, _how = VS.sprint_end_ref(SPRINT_KEY)
    routers = []
    code, out, _ = VS.git("ls-tree", "-r", "--name-only", end_ref, "apps/api/routers")
    for rel in out.split():
        txt = VS.git_show(end_ref, rel) or ""
        if re.search(r"(?<![\w.])require_staff\(\s*request", txt):
            routers.append(rel)
    print(f"    [INFO] routers still calling require_staff at the sprint's end (writes only): {routers}")
    rec = json.loads(VS.git_show(end_ref, "docs/AUTHZ1_ROUTES.json") or "{}")
    staff_reads = [r["path"] for r in rec.get("routes", []) if not r["write"] and re.search(r"(^|; )staff($|;)", r["after"])]
    check(not staff_reads, "Task 1a: no READ route is still gated by require_staff (each one is internal now)",
          f"{staff_reads}")
    find("Task 1a: the prompt counted 20 router files using require_staff; at BEFORE_REF it was 15 (11 require_staff "
         "+ 4 require_staff_or_permission only)", "the four settings reads keep require_staff_or_permission")
    find("Task 1b: CRM / document links / precedence / staff tree → any internal role; SPV → + view_spv; ledger → + "
         "view_spv or manage_ledger; billing / custody → + view_portfolio; settings catalogs unchanged (staff or "
         "manage_org_settings)", "view_spv and view_portfolio are MEMBER grants — the internal role is what refuses "
         "a member, the permission what narrows a non-staff internal role")
    find("Task 1c: org_admin (27 live grants) → CRM, documents, replication / instructions / models / proposals / "
         "tax / blocks screens, settings; NO view_spv / view_portfolio / view_members, so no SPV Manager, ledger or "
         "billing. support_staff (8) → CRM, documents, billing & custody reads (view_portfolio); no SPV, no "
         "instructions (no view_execution), no approvals",
         "the real org_admin role DOES hold every approve_* (approve_proposals, approve_execution, approve_models, "
         "approve_blocks …) by design — a permission grant, not the bypass; support_staff holds none")
    find("Task 1c: the workflow Runs console is gated on the PROFILE axis (view_workflow_runs); the menu keeps it "
         "Super Admin only until /users/me publishes profile keys", "unchanged; support staff's 'CSA / Ops' "
         "profile is no longer stripped to member-safe keys (is_member_principal reads is_internal)")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    where = own.where()
    changed = {k[1]: (own.before[k], own.end[k]) for k in own.changed}
    check(set(changed) == set(ROUTE_EXPECT),
          "the sprint's own route changes are exactly the 31 staff-data reads of the hand table",
          f"{where}; extra {sorted(set(changed) - set(ROUTE_EXPECT))}; missing {sorted(set(ROUTE_EXPECT) - set(changed))}")
    mism = [(p, _rec_rule(e)) for p, (_b, e) in changed.items() if p in ROUTE_EXPECT and
            _rec_rule(e) != (ROUTE_EXPECT[p][0],) + ((tuple(sorted(ROUTE_EXPECT[p][1])),) if len(ROUTE_EXPECT[p]) > 1
                                                     else ())]
    check(not mism, "every re-gated route's record rule equals the hand table (internal / internal + permission)",
          f"{mism}")
    was_staff = [p for p, (b, _e) in changed.items() if "staff" not in b["after"]]
    check(not was_staff, "every re-gated route was staff-gated before (nothing open was touched)", f"{was_staff}")
    writes = [p for p, (b, _e) in changed.items() if b["write"]]
    check(not writes, "no WRITE route changed gate (writes keep require_staff / their permission)", f"{writes}")
    opened = [p for p, (_b, e) in changed.items() if e["after"] == "none beyond auth"]
    check(not opened, "no route became open", f"{opened}")
    check(not own.new and not own.removed, "no route added or removed", f"{own.new} {own.removed}")
    drift = VS.still_gated(own, own.changed)
    check(not drift, "the re-gated routes still carry the same gate today", f"{drift}")
    b24_missing = [p for p in BUGFIX2_STAFF_ONLY_24 if p not in changed]
    check(bool(BUGFIX2_STAFF_ONLY_24) and not b24_missing,
          f"all {len(BUGFIX2_STAFF_ONLY_24)} of BUGFIX2's staff-only reads (own hand list) are among them",
          f"{b24_missing}")
    s3 = [p for p in STAFF_OR_ORGADMIN if any(k[1] == p for k in own.changed)]
    check(not s3, "the 3 staff-or-org-admin settings reads are unchanged", f"{s3}")


def self_static(check, find):
    def read(rel):
        p = REPO / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    before = VS.git_show(BEFORE_REF, "apps/api/services/authz.py") or ""
    now = VS.own_text(SPRINT_KEY, "apps/api/services/authz.py") or ""
    line = 'STAFF_ROLE_NAMES = frozenset({"investment_staff", "admin", "super_admin", "owner"})'
    check(line in before and line in now and now.count("STAFF_ROLE_NAMES = ") == 1,
          "the bypass set STAFF_ROLE_NAMES is byte-identical to BEFORE_REF (never widened)")
    check("INTERNAL_ROLE_NAMES = STAFF_ROLE_NAMES | frozenset({" in now and "def require_internal(" in now
          and "def require_internal_permission(" in now and "is_internal: bool = False" in now,
          "services/authz.py: INTERNAL_ROLE_NAMES ⊇ STAFF_ROLE_NAMES, Principal.is_internal, require_internal, "
          "require_internal_permission")
    m = re.search(r"INTERNAL_ROLE_NAMES = STAFF_ROLE_NAMES \| frozenset\(\{(.*?)\}\)", now, re.S)
    added = set(re.findall(r'"(\w+)"', m.group(1))) if m else set()
    check(added == set(OWN_INTERNAL_ADDED),
          "INTERNAL_ROLE_NAMES adds exactly the eight non-staff internal firm roles (no client role, no fixture role)",
          f"{sorted(added)}")
    check("member" not in added and "next_gen" not in added, "client roles member / next_gen are not internal")
    regs = read("scripts/regression_set.txt")
    check("verify_staffroles1.py" in regs, "Task 6: verify_staffroles1.py is in scripts/regression_set.txt")
    status = read("docs/PROJECT_STATUS.md")
    check("STAFFROLES1" in status and "is_internal" in status and "staff_assignments" in status,
          "Task 4/6: PROJECT_STATUS.md carries the staffroles1 entry (two concepts, staff visibility stays OFF)")
    tests = read("apps/web/tests/staffroles1/staffroles1.test.mjs")
    check("isInternalStrict" in tests and "unknown_role" in tests, "menu node tests exist for the personas")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "staffroles1verify"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000051{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)

# Live grants of the 2nd Act roles, read 2026-11-04 (roles ⋈ role_permissions), written by hand. The org admin
# fixture deliberately holds NO approve_* (the bypass test); the real org_admin role holds every approve_* by grant.
ORGADMIN_FIXTURE_PERMS = frozenset({"manage_org_settings", "review_agent_proposals", "view_execution",
                                    "view_proposals", "view_models", "view_blocks", "view_replication", "view_tax"})
SUPPORT_PERMS = frozenset({"review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
                           "view_deals", "view_members", "view_portfolio", "view_workflow_runs"})
ADVISOR_PERMS = frozenset({"manage_compliance", "manage_portfolio", "review_agent_proposals", "view_audit_log",
                           "view_community", "view_compliance", "view_dashboard", "view_marketplace", "view_members",
                           "view_portfolio", "view_spv", "view_workflow_runs"})
UNKNOWN_ROLE = "sr1_unclassified"
UNKNOWN_PERMS = frozenset({"view_members", "view_spv"})

U_SUPER, U_STAFF, U_ORGADMIN, U_SUPPORT, U_ADVISOR, U_MEMBER, U_UNKNOWN, U_OTHER = (
    _id(10), _id(11), _id(12), _id(13), _id(14), _id(15), _id(16), _id(17))
# uid → (org, users.role, short name, role name, permissions)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", frozenset(C.INVESTMENT_STAFF_PERMS)),
    U_ORGADMIN: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_FIXTURE_PERMS),
    U_SUPPORT: (ORG_A, "member", "support", "support_staff", SUPPORT_PERMS),
    U_ADVISOR: (ORG_A, "member", "advisor", "advisor", ADVISOR_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_UNKNOWN: (ORG_A, "member", "unknown", UNKNOWN_ROLE, UNKNOWN_PERMS),
    U_OTHER: (ORG_B, "member", "other", "member", frozenset(C.MEMBER_PERMS)),
}
PERSONAS = {"super": U_SUPER, "staff": U_STAFF, "orgadmin": U_ORGADMIN, "support": U_SUPPORT,
            "advisor": U_ADVISOR, "member": U_MEMBER, "unknown": U_UNKNOWN}
INTERNAL_PERSONAS = {"super", "staff", "orgadmin", "support", "advisor"}
BYPASS_PERSONAS = {"super", "staff"}

E_M1, E_F2, E_B = _id(20), _id(21), _id(22)
F2_NAME = "SR1 Family Two"
D_ACTIVE, V_SPV, G_F2, DOC_F2, P_STAFFONLY = _id(30), _id(31), _id(32), _id(33), _id(34)
DOC_F2_KEY = f"{TAG}/doc-f2.pdf"
LEDGER_VEHICLE_ROUTES = ("/api/v1/ledger/entries", "/api/v1/ledger/trial-balance",
                         "/api/v1/ledger/capital-accounts", "/api/v1/ledger/templates")
# Routes whose fixture rows exist: an ADMITTED caller must get exactly 200 there (a 404 / 422 is not admission).
MUST_BE_200 = {"/api/v1/entities/{entity_id}/full", "/api/v1/entities/{entity_id}/notes", "/api/v1/entity-groups",
               "/api/v1/entity-groups/{group_id}", "/api/v1/spvs", "/api/v1/spvs/{spv_id}", *LEDGER_VEHICLE_ROUTES}
APPROVALS = (  # (path, the permission it names) — the approve routes the prompt names
    (f"/api/v1/proposals/{MISSING}/approve", "approve_proposals"),
    (f"/api/v1/execution/instructions/{MISSING}/approve", "approve_execution"),
    (f"/api/v1/models/{MISSING}/versions/{MISSING}/approve", "approve_models"),
    (f"/api/v1/execution/blocks/settings/policies/{MISSING}/approve", "approve_blocks"),
)

COUNTED = ("public.users", "public.organizations", "public.entities", "public.deals", "public.spvs",
           "public.delegate_grants", "public.compliance_records", "public.entity_notes", "public.entity_documents",
           "public.entity_groups", "public.entity_group_members", "public.roles", "public.user_roles",
           "public.role_permissions", "public.profiles", "public.profile_permissions", "public.audit_log",
           "public.member_todos", "public.investment_profile_answers")
REAL_FINGERPRINTED = ("public.users", "public.roles", "public.user_roles", "public.role_permissions",
                      "public.profiles", "public.profile_permissions", "public.permission_sets",
                      "public.user_permission_sets", "public.entities", "public.delegate_grants")
# Columns real activity writes during a run: a real sign-in stamps users.last_login_at (BUGFIX2 fix 1, FAIL E).
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
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


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
    """Count + md5 of every REAL row (fixture orgs excluded) minus the named ACTIVITY_COLUMNS, keyed by the
    table's real primary key from the catalog."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(({_row_expr(table)})::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t", fetch="fetchrow")
    return int(row["n"]), row["h"]


async def column_hashes(conn, table: str) -> dict:
    """{primary key text: {column: md5(value)}} — diagnostics only (ids and column NAMES printed, never values)."""
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
    """Fixture rows only — fixture orgs and fixture users, by id. Never a TRUNCATE."""
    users = list(USERS)
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            plan = {
                "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
                "public.member_todos": ("user_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
                                        [users, FIXTURE_ORGS]),
            }
            res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                                   label="verify_staffroles1")
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
async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"STAFFROLES1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        wanted = set().union(*(v[4] for v in USERS.values()))
        missing = sorted(wanted - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        roles = {}
        for uid, (org, _acct, _nm, rname, perms) in USERS.items():
            if (org, rname) in roles:
                continue
            # rbac.ensure_role — NOT ensure_org_admin_role, which would add every ORG_ADMIN_DEFAULT_PERMISSIONS
            # entry (approve_* included) to the org_admin fixture role.
            rid = await rbac.ensure_role(conn, org, rname, f"STAFFROLES1 fixture {rname}")
            for p in perms:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _perms) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"STAFFROLES1 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])

        for eid, org, etype, name in ((E_M1, ORG_A, "individual", "SR1 Member One"),
                                      (E_F2, ORG_A, "individual", F2_NAME),
                                      (E_B, ORG_B, "individual", "SR1 Org B Person")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) "
                               "VALUES ($1, $2, $3::entity_type, $4)", eid, org, etype, name)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, E_M1, U_MEMBER)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_B, E_B, U_OTHER)
        await conn.execute("INSERT INTO compliance_records (org_id, entity_id, kyc_status, ofac_screen_status, "
                           "pep_status) VALUES ($1, $2, 'approved'::kyc_status, 'passed'::ofac_status, true)",
                           ORG_A, E_F2)
        await conn.execute("INSERT INTO entity_notes (org_id, entity_id, note_text) VALUES ($1, $2, $3)",
                           ORG_A, E_F2, f"{F2_NAME} private note")
        await conn.execute("INSERT INTO entity_documents (id, org_id, entity_id, title, storage_key) VALUES "
                           "($1, $2, $3, $4, $5)", DOC_F2, ORG_A, E_F2, f"{F2_NAME} doc", DOC_F2_KEY)
        await conn.execute("INSERT INTO entity_groups (id, org_id, name) VALUES ($1, $2, 'SR1 group')", G_F2, ORG_A)
        await conn.execute("INSERT INTO entity_group_members (org_id, group_id, entity_id) VALUES ($1, $2, $3)",
                           ORG_A, G_F2, E_F2)
        await conn.execute(
            "INSERT INTO deals (id, org_id, name, slug, deal_status, description, asset_super_class, target_raise) "
            "VALUES ($1, $2, 'SR1 Deal', $3, 'active'::deal_status, 'd', 'taxonomy_sc_1', 1000000)",
            D_ACTIVE, ORG_A, f"{TAG}-deal")
        await conn.execute("INSERT INTO spvs (id, org_id, deal_id, name) VALUES ($1, $2, $3, 'SR1 vehicle')",
                           V_SPV, ORG_A, D_ACTIVE)
        # a staff-only PROFILE held by the support-staff fixture (the jpl991722 "CSA / Ops" shape)
        await conn.execute("INSERT INTO profiles (id, org_id, name) VALUES ($1, $2, 'SR1 CSA / Ops')",
                           P_STAFFONLY, ORG_A)
        for k in ("staff", "manage_documents", "view_members", "view_workflow_runs"):
            await conn.execute("INSERT INTO profile_permissions (org_id, profile_id, permission_key) "
                               "VALUES ($1, $2, $3)", ORG_A, P_STAFFONLY, k)
        await conn.execute("UPDATE users SET profile_id = $1 WHERE id = $2", P_STAFFONLY, U_SUPPORT)


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test = true; each persona's DB grant is what its seeding documents")
    from services.authz import load_principal

    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(o == 2 and u == len(USERS), "both fixture orgs and every fixture user are is_test = true", f"{o} / {u}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for name, uid in PERSONAS.items():
            p = await load_principal(conn, uid)
            want = USERS[uid][4]
            got = set(p.permissions) if p else set()
            if name == "super":
                check(p is not None and p.is_super_admin and want <= got, "super persona: Super Admin, catalog held",
                      f"{len(got)}")
                continue
            # PERMS1 FIX 1: since perms1 the Principal = role ∪ profile ∪ permission sets (catalog keys only), so
            # the expectation is the union the fixture ACTUALLY holds, read straight from the grant tables (not from
            # the Principal's own statement). Every seeded role key must be present; no key may appear that was not
            # granted on some path (the original "no extra" guarantee, unchanged in strength).
            elsewhere = await granted_off_role(conn, uid)
            union = set(want) | elsewhere
            check(set(want) <= got and got <= union,
                  f"{name} persona's DB permissions == its seeded grant (role) ∪ its profile / permission-set keys",
                  f"missing {sorted(set(want) - got)}; extra (granted nowhere) {sorted(got - union)}; "
                  f"via profile / set {sorted(elsewhere - set(want))}")
        sup = await load_principal(conn, U_SUPPORT)
        sup_union = set(USERS[U_SUPPORT][4]) | await granted_off_role(conn, U_SUPPORT)
        check(sup is not None and set(sup.permissions) == sup_union and "manage_documents" in sup.permissions
              and "manage_documents" not in USERS[U_SUPPORT][4] and "staff" not in sup.permissions,
              "support (internal): Principal == role ∪ 'CSA / Ops' profile exactly — manage_documents arrives ONLY "
              "via the profile; the non-catalog key 'staff' grants nothing",
              f"{sorted(set(sup.permissions if sup else ()) ^ sup_union)}")
        p = await load_principal(conn, U_ORGADMIN)
        appr = sorted(x for x in (p.permissions if p else ()) if x.startswith("approve_"))
        check(p is not None and not appr, "the org admin fixture holds NO approve_* permission", f"{appr}")
    await y_member_staff_profile(conn)


async def granted_off_role(conn, uid) -> set:
    """The user's profile + permission-set keys that are real catalog permissions — read from the grant tables
    directly (the caller's transaction sets app.is_super_admin)."""
    rows = await conn.fetch(
        "SELECT pp.permission_key AS k FROM users u JOIN profile_permissions pp ON pp.profile_id = u.profile_id "
        "WHERE u.id = $1 UNION SELECT psp.permission_key FROM user_permission_sets ups "
        "JOIN permission_set_permissions psp ON psp.permission_set_id = ups.permission_set_id WHERE ups.user_id = $1",
        uid)
    catalog = {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}
    return {r["k"] for r in rows} & catalog


class _Rollback(Exception):
    pass


async def y_member_staff_profile(conn):
    """PERMS1 FIX 1 — the opposite direction: a key granted ONLY on a staff-only profile still does not reach a
    member. The member fixture is given the 'SR1 CSA / Ops' profile inside a transaction that is rolled back, so
    no other check sees it and nothing persists."""
    from services.authz import load_principal

    member_role = set(USERS[U_MEMBER][4])
    seen: dict = {}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            safe = {r["name"] for r in await conn.fetch(
                "SELECT p.name FROM roles r JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p "
                "ON p.id = rp.permission_id WHERE r.org_id = $1 AND r.name = 'member'", ORG_A)}
            await conn.execute("UPDATE users SET profile_id = $1 WHERE id = $2", P_STAFFONLY, U_MEMBER)
            seen["linked"] = await conn.fetchval("SELECT profile_id FROM users WHERE id = $1", U_MEMBER)
            seen["profile_keys"] = await granted_off_role(conn, U_MEMBER)
            pm = await load_principal(conn, U_MEMBER)
            seen["perms"] = set(pm.permissions) if pm else None
            seen["safe"] = safe
            raise _Rollback
    except _Rollback:
        pass
    after = await platform(conn, "SELECT profile_id FROM users WHERE id = $1", U_MEMBER, fetch="fetchval")
    staff_only = (seen.get("profile_keys") or set()) - seen.get("safe", set()) - member_role
    perms = seen.get("perms")
    check(seen.get("linked") == P_STAFFONLY and "manage_documents" in staff_only and perms is not None
          and not (perms & staff_only) and perms <= member_role | seen.get("safe", set()),
          "a key granted ONLY on a staff-only profile (manage_documents on 'SR1 CSA / Ops') does not reach a member "
          "holding that profile",
          f"staff-only {sorted(staff_only)}; leaked {sorted((perms or set()) & staff_only)}")
    check(after is None, "the member's profile link was rolled back (nothing persisted)", f"{after}")


# ═══ The real app, token stubbed by bearer string ═════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []


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
    import routers.org_settings as org_settings_router
    import routers.entity_documents as entity_documents_router

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()   # ASGITransport skips the lifespan that registers the brief blocks

    want = {"fetch_auth0_profile": ["request", "c"], "enrich_with_live_info": ["rows"],
            "semantic_search": ["pool", "org_id", "user_id", "query", "is_staff", "limit"]}
    have = {"fetch_auth0_profile": list(inspect.signature(users_mod.fetch_auth0_profile).parameters),
            "enrich_with_live_info": list(inspect.signature(org_settings_router.enrich_with_live_info).parameters),
            "semantic_search": list(inspect.signature(document_embedding.semantic_search).parameters)}
    if len(have["fetch_auth0_profile"]) != 2 or len(have["enrich_with_live_info"]) != 1 or \
            have["semantic_search"] != want["semantic_search"]:
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

    def fake_url(*a, **k):
        return "https://r2.invalid/staffroles1-verify"

    def no_r2(*a, **k):
        raise AssertionError("verify: real R2 must never be called")

    storage.get_signed_url = fake_url
    entity_documents_router.get_signed_url = fake_url
    storage.get_s3_client = no_r2
    storage.upload_bytes = no_r2
    storage.delete_object = no_r2


def leaks_family2(body) -> bool:
    text = json.dumps(body, ensure_ascii=False, default=str)
    return any(s in text for s in (str(E_F2), F2_NAME, str(DOC_F2), str(G_F2)))


def _fill(path: str) -> str:
    p = path.replace("{entity_id}", str(E_F2)).replace("{group_id}", str(G_F2)).replace("{spv_id}", str(V_SPV))
    p = p.replace("{record_type}", "entity").replace("{record_id}", str(E_F2)).replace("{document_id}", str(DOC_F2))
    p = re.sub(r"\{[^}]+\}", str(MISSING), p)
    q = {"/api/v1/modeling/ta/calibration-floor": "?periods_per_year=4",
         **{r: f"?vehicle_id={V_SPV}" for r in LEDGER_VEHICLE_ROUTES}}.get(path, "")
    return p + q


def expected(persona: str, rule: tuple) -> bool:
    """True = admitted. From the hand table and each persona's SEEDED grant — never from the route record."""
    if persona == "super":
        return True
    if rule[0] == "internal":
        return persona in INTERNAL_PERSONAS
    if rule[0] == "internal_perm":
        if persona in BYPASS_PERSONAS:
            return True
        return persona in INTERNAL_PERSONAS and bool(USERS[PERSONAS[persona]][4] & set(rule[1]))
    if rule[0] == "staff_or_orgadmin":
        return persona in BYPASS_PERSONAS or "manage_org_settings" in USERS[PERSONAS[persona]][4]
    raise ValueError(rule)


# ═══ Sections ═════════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify connection is a genuinely non-bypassing role")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(r["rolbypassrls"] is False, "the app connection's role has rolbypassrls = false (RLS is real)",
          f"{r['u']} rolbypassrls={r['rolbypassrls']}")


async def y_concepts(conn):
    section("[Y] The two concepts — is_internal (see) vs is_staff (bypass), from the DB Principal and /users/me")
    from services import authz
    from services.authz import load_principal

    check(authz.STAFF_ROLE_NAMES == frozenset({"investment_staff", "admin", "super_admin", "owner"}),
          "STAFF_ROLE_NAMES (the bypass) is exactly as before", f"{sorted(authz.STAFF_ROLE_NAMES)}")
    check(authz.STAFF_ROLE_NAMES <= authz.INTERNAL_ROLE_NAMES and not (authz.INTERNAL_ROLE_NAMES
                                                                      & {"member", "next_gen"}),
          "INTERNAL_ROLE_NAMES ⊇ STAFF_ROLE_NAMES and excludes the client roles", f"{sorted(authz.INTERNAL_ROLE_NAMES)}")
    want = {"super": (True, True), "staff": (True, True), "orgadmin": (True, False), "support": (True, False),
            "advisor": (True, False), "member": (False, False), "unknown": (False, False)}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        got = {}
        for name, uid in PERSONAS.items():
            p = await load_principal(conn, uid)
            got[name] = (p.is_internal, p.is_staff) if p else None
    check(got == want, "Principal (is_internal, is_staff) per persona: internal roles see, only staff bypass; the "
          "unknown role is neither", f"{got}")
    bad = []
    for name, uid in PERSONAS.items():
        code, me = await api(uid, "GET", "/api/v1/users/me")
        if code != 200 or (me.get("is_internal"), me.get("is_staff")) != want[name]:
            bad.append((name, code, me.get("is_internal"), me.get("is_staff")))
    check(not bad, "/users/me publishes is_internal alongside an unchanged is_staff, per persona", f"{bad}")


def _mount_before_entities():
    """The PRE-SPRINT routers/entities.py (BEFORE_REF) as its own module under /__sr1before behind the SAME
    middleware / Principal — the exact original handlers on the identical request."""
    import main

    src = VS.git_show(BEFORE_REF, "apps/api/routers/entities.py")
    if not src:
        raise RuntimeError("cannot read routers/entities.py at BEFORE_REF")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sr1before-")) / "entities_before_staffroles1.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("entities_before_staffroles1", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main.app.include_router(mod.router, prefix="/__sr1before/api/v1")
    return "/__sr1before"


async def y_reproduce(conn):
    section("[Y] The reported bug — org admin / support staff refused the CRM: reproduced BEFORE, fixed AFTER")
    prefix = _mount_before_entities()
    for name in ("orgadmin", "support"):
        uid = PERSONAS[name]
        for p in (f"/api/v1/entities/{E_F2}/full", f"/api/v1/entities/{E_F2}/notes"):
            cb, bb = await api(uid, "GET", prefix + p)
            ca, ba = await api(uid, "GET", p)
            check(cb == 403 and "Staff access required" in dumps(bb) and ca == 200 and leaks_family2(ba),
                  f"{name} GET {p}: BEFORE_REF handler 403 'Staff access required'; now 200 with the record",
                  f"before {cb} {dumps(bb)[:80]}; after {ca} {dumps(ba)[:120]}")
    for name in ("member",):
        cb, _ = await api(PERSONAS[name], "GET", f"{prefix}/api/v1/entities/{E_F2}/full")
        ca, ba = await api(PERSONAS[name], "GET", f"/api/v1/entities/{E_F2}/full")
        check(cb == 403 and ca == 403 and not leaks_family2(ba), "member: 403 before and after (unchanged)",
              f"{cb}/{ca} {dumps(ba)[:80]}")


async def y_routes_matrix(conn):
    section("[Y] Every re-gated staff-data read + the 3 settings reads — every persona, identical request")
    now = {r["path"]: r for r in json.loads(ROUTES_JSON.read_text(encoding="utf-8"))["routes"]
           if "GET" in r["methods"]}
    missing = [p for p in ROUTE_EXPECT if p not in now]
    check(not missing, "every hand-table route exists in today's record", f"{missing}")
    table = [(p, ROUTE_EXPECT[p]) for p in ROUTE_EXPECT] + [(p, ("staff_or_orgadmin",)) for p in STAFF_OR_ORGADMIN]
    bad = []
    exercised = 0
    for path, rule in table:
        url = _fill(path)
        for name, uid in PERSONAS.items():
            code, body = await api(uid, "GET", url)
            exercised += 1
            if expected(name, rule):
                ok = code not in (401, 403) and code < 500 and (path not in MUST_BE_200 or code == 200)
            else:
                ok = code == 403 and not leaks_family2(body)
                if ok and rule[0] == "internal_perm" and name in INTERNAL_PERSONAS:
                    ok = all(p in dumps(body) for p in rule[1][:1])   # the 403 names the missing permission
            if not ok:
                bad.append(f"{name} {path} → {code} {dumps(body)[:100]}")
    check(exercised == len(table) * len(PERSONAS) and not bad,
          f"{len(table)} routes × {len(PERSONAS)} personas: super 200; staff as before; org admin / support / "
          "advisor admitted per the hand table (internal, or internal + its permission); member / unknown 403 "
          "with no family-2 data", f"{exercised} requests; {len(bad)} wrong: {bad[:8]}")
    # the compliance record inside /full still needs view_compliance (support holds it, org admin does not)
    _c1, b_o = await api(U_ORGADMIN, "GET", f"/api/v1/entities/{E_F2}/full")
    _c2, b_s = await api(U_SUPPORT, "GET", f"/api/v1/entities/{E_F2}/full")
    check((b_o or {}).get("compliance_record") is None and ((b_s or {}).get("compliance_record") or {})
          .get("kyc_status") == "approved",
          "/full: the KYC / OFAC record reaches support staff (view_compliance) and NOT the org admin (no "
          "view_compliance)", f"{dumps((b_o or {}).get('compliance_record'))} / "
                              f"{dumps((b_s or {}).get('compliance_record'))[:80]}")
    code, body = await api(U_SUPPORT, "GET", f"/api/v1/entity-groups/{G_F2}")
    mem = [m.get("entity_id") for m in (body or {}).get("members") or []]
    db = [str(x["entity_id"]) for x in await scoped(conn, ORG_A, "SELECT entity_id FROM entity_group_members "
                                                    "WHERE group_id = $1", G_F2)]
    check(code == 200 and bool(mem) and mem == db, "support staff reads the entity group, equal to the same rows in "
          "SQL", f"HTTP {code}; {mem} vs {db}")
    code, body = await api(U_ADVISOR, "GET", "/api/v1/spvs")
    ids = {r.get("id") for r in body} if isinstance(body, list) else set()
    check(code == 200 and str(V_SPV) in ids, "advisor (internal + view_spv) lists the org's SPVs", f"HTTP {code}")
    check(NETWORK_CALLS.count("litellm") >= 1, "the model catalog's live-info enrichment hit the local stub",
          f"{NETWORK_CALLS}")


async def y_bypass(conn):
    section("[Y] The bypass is unchanged — internal roles without approve_* are refused every approval; writes keep "
            "require_staff / the member path")
    from services.authz import load_principal

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        perms = {n: set((await load_principal(conn, PERSONAS[n])).permissions) for n in ("orgadmin", "support",
                                                                                        "advisor", "staff")}
    for path, perm in APPROVALS:
        for name in ("orgadmin", "support", "advisor", "staff"):
            code, body = await api(PERSONAS[name], "POST", path, {})
            check(perm not in perms[name] and code == 403 and f"Permission required: {perm}" in dumps(body),
                  f"{name} (no {perm}) POST {path.split(str(MISSING))[0]}… → 403 naming {perm}",
                  f"HTTP {code}; {dumps(body)}")
        code, body = await api(U_SUPER, "POST", path, {})
        check(code not in (401, 403), f"super admin passes the {perm} gate (then the missing id decides)",
              f"HTTP {code}; {dumps(body)[:120]}")
    # writes that keep require_staff: an internal non-staff role is refused, staff passes the gate
    for path in (f"/api/v1/entity-relationships/{MISSING}",):
        for name in ("orgadmin", "support", "advisor"):
            code, body = await api(PERSONAS[name], "DELETE", path)
            check(code == 403 and "Staff access required" in dumps(body), f"{name} DELETE {path} → 403 (a write "
                  "keeps require_staff)", f"HTTP {code}; {dumps(body)}")
        code, body = await api(U_STAFF, "DELETE", path)
        check(code not in (401, 403), f"investment staff passes the same write gate as before (DELETE {path})",
              f"HTTP {code}; {dumps(body)[:120]}")
    # PORTENTRY1 (GAP-CRM-05) — a DECIDED change of rule, not a weakening (Joe, 2026-11-04): ownership % is written
    # by an internal role holding manage_members (the Relationships panel's gate). The identical request: the org
    # admin (manage_members) is admitted, support staff / advisor (no manage_members) and the member are refused
    # naming it / as non-staff; staff and super admin pass as before.
    path = f"/api/v1/entity-relationships/{MISSING}/ownership"
    # the fixture org admin holds no manage_members (a hand-written set); the REAL org_admin role does (perms1). Grant
    # it to the FIXTURE role for this one request, then remove exactly that grant (fixture org, by id).
    grant = ("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
             "WHERE r.org_id = $1 AND r.name = 'org_admin' AND p.name = 'manage_members' ON CONFLICT DO NOTHING")
    ungrant = ("DELETE FROM role_permissions WHERE role_id = (SELECT id FROM roles WHERE org_id = $1 AND name = "
               "'org_admin') AND permission_id = (SELECT id FROM permissions WHERE name = 'manage_members')")
    await platform(conn, grant, ORG_A, fetch="execute")
    try:
        code, body = await api(U_ORGADMIN, "DELETE", path)
    finally:
        await platform(conn, ungrant, ORG_A, fetch="execute")
    check(code not in (401, 403), f"org admin (internal + manage_members) passes the ownership gate (DELETE {path} → "
          "the missing id decides)", f"HTTP {code}; {dumps(body)[:160]}")
    for name in ("support", "advisor"):
        code, body = await api(PERSONAS[name], "DELETE", path)
        check(code == 403 and "Permission required: manage_members" in dumps(body),
              f"{name} (internal, no manage_members) DELETE {path} → 403 naming manage_members",
              f"HTTP {code}; {dumps(body)}")
    code, body = await api(U_MEMBER, "DELETE", path)
    check(code == 403, f"member DELETE {path} → 403 (a client role never writes ownership)",
          f"HTTP {code}; {dumps(body)}")
    for uid, name in ((U_STAFF, "investment staff"), (U_SUPER, "super admin")):
        code, body = await api(uid, "DELETE", path)
        check(code not in (401, 403), f"{name} passes the ownership gate as before (DELETE {path})",
              f"HTTP {code}; {dumps(body)[:120]}")
    n0 = await platform(conn, "SELECT count(*) FROM entity_group_members WHERE group_id = $1", G_F2, fetch="fetchval")
    code, body = await api(U_SUPPORT, "DELETE", f"/api/v1/entity-groups/{G_F2}/members/{E_F2}")
    n1 = await platform(conn, "SELECT count(*) FROM entity_group_members WHERE group_id = $1", G_F2, fetch="fetchval")
    check(code == 403 and n0 == n1 == 1, "support staff DELETE of a group member → 403, the row is still there "
          "(re-read)", f"HTTP {code}; {n0} → {n1}")
    # entity-scoped write: seeing every family on a READ never widens a WRITE (member path → 404)
    for name in ("orgadmin", "support"):
        cr, _ = await api(PERSONAS[name], "GET", f"/api/v1/entities/{E_F2}")
        cw, bw = await api(PERSONAS[name], "POST", f"/api/v1/investment-profile/{E_F2}/answers",
                           {"question_id": str(MISSING), "answer_value": "x"})
        check(cr == 200 and cw == 404 and "Entity not found" in dumps(bw),
              f"{name}: GET /entities/{{family 2}} 200 (sees) but POST its investment-profile answer → 404 (cannot "
              "write another family)", f"read {cr}; write {cw} {dumps(bw)}")
    n = await platform(conn, "SELECT count(*) FROM investment_profile_answers WHERE entity_id = $1", E_F2,
                       fetch="fetchval")
    check(n == 0, "no investment-profile answer was written for family 2 (re-read)", f"{n}")


async def y_member_scope(conn):
    section("[Y] Member scope — internal roles read every family; a member only its own; unknown role = member")
    for name in ("orgadmin", "support", "advisor"):
        code, rows = await api(PERSONAS[name], "GET", "/api/v1/entities?limit=500")
        ids = {r["id"] for r in rows} if isinstance(rows, list) else set()
        check(code == 200 and {str(E_M1), str(E_F2)} <= ids and str(E_B) not in ids,
              f"{name} GET /entities: both families of org A, never org B", f"HTTP {code}; {len(ids)}")
    for name in ("member", "unknown"):
        code, rows = await api(PERSONAS[name], "GET", "/api/v1/entities?limit=500")
        ids = {r["id"] for r in rows} if isinstance(rows, list) else set()
        want = {str(E_M1)} if name == "member" else set()
        check(code == 200 and ids == want, f"{name} GET /entities: exactly its own family "
              f"({'family 1' if want else 'nothing — no delegate grant'})", f"HTTP {code}; {sorted(ids)}")
        code, body = await api(PERSONAS[name], "GET", f"/api/v1/entities/{E_F2}")
        check(code == 404 and not leaks_family2(body), f"{name} GET another family's entity → 404", f"HTTP {code}")
    sql = {str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT id FROM entities WHERE org_id = $1 AND "
                                                         "id = ANY($2::uuid[])", ORG_A, [E_M1, E_F2])}
    check(sql == {str(E_M1), str(E_F2)}, "…the org-A fixture entities read directly in SQL (non-empty)", f"{sql}")


async def y_profile(conn):
    section("[Y] Support staff's own staff-only profile is theirs (not stripped like a member's)")
    from services import profiles as P
    from services.authz import is_member_principal, load_principal
    from services.database import get_pool, reset_rls_context, set_rls_context

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        ps = await load_principal(conn, U_SUPPORT)
        pu = await load_principal(conn, U_UNKNOWN)
    check(is_member_principal(ps) is False and is_member_principal(pu) is True,
          "is_member_principal: support staff is not a member; the unknown role is (fail closed)")
    pool = await get_pool()
    tok = set_rls_context(str(ORG_A), False)
    try:
        eff = await P.get_effective_permissions(pool, U_SUPPORT)
    finally:
        reset_rls_context(tok)
    check({"manage_documents", "view_workflow_runs", "view_members"} <= set(eff),
          "support staff's 'CSA / Ops'-shaped profile grants its keys on the profile axis (workflow run console)",
          f"{sorted(eff)}")


async def y_menu(conn):
    section("[Y] Menu — node tests + the REAL /users/me envelopes fed into the menu's own logic")
    code, out, err = run_cmd(["node", "--test", "tests/staffroles1/", "tests/bugfix2/", "tests/nav/"], WEB_DIR,
                             timeout=600)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(code == 0 and m is not None and m.group(1) == "0", "node --test staffroles1 / bugfix2 / nav: 0 fail",
          (out + err)[-400:] if code else "")
    envs = {}
    for name, uid in PERSONAS.items():
        c, me = await api(uid, "GET", "/api/v1/users/me")
        envs[name] = me if c == 200 else None
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sr1menu-"))
    (tmp / "envs.json").write_text(json.dumps(envs), encoding="utf-8")
    script = tmp / "menu.mjs"
    mv = (WEB_DIR / "lib" / "menuVisibility.mjs").as_uri()
    script.write_text(
        "import { readFileSync } from 'node:fs';\n"
        f"const m = await import({json.dumps(mv)});\n"
        "const envs = JSON.parse(readFileSync(process.argv[2], 'utf8'));\n"
        "const out = {};\n"
        "for (const [k, me] of Object.entries(envs)) {\n"
        "  const hrefs = [...m.visibleMenuItems(me).map((i) => i.href),\n"
        "    ...m.visibleSections(me).flatMap((s) => s.entries.flatMap((e) => e.tabHrefs)),\n"
        "    ...m.visibleAdminGroups(me).flatMap((g) => g.tabs.map((t) => t.href))];\n"
        "  out[k] = { hrefs: [...new Set(hrefs)].sort(), crm: m.canOpenArea(me, '/crm'),\n"
        "    spvs: m.canOpenArea(me, '/spvs'), internal: m.isInternalStrict(me), staff: m.isStaffStrict(me) };\n"
        "}\n"
        "console.log(JSON.stringify(out));\n", encoding="utf-8")
    code, out, err = run_cmd(["node", str(script), str(tmp / "envs.json")], WEB_DIR, timeout=120)
    res = json.loads(out.strip().splitlines()[-1]) if code == 0 and out.strip() else {}
    check(bool(res), "the menu module evaluated the real envelopes", (out + err)[-300:])
    staff_screens = {"/crm", "/spvs", "/admin/billing-groups", "/admin/custody-import"}
    want_staff = {"orgadmin": {"/crm"}, "support": {"/crm", "/admin/billing-groups", "/admin/custody-import"},
                  "advisor": staff_screens, "staff": staff_screens, "super": staff_screens,
                  "member": set(), "unknown": set()}
    for name, want in want_staff.items():
        r = res.get(name) or {}
        got = set(r.get("hrefs") or []) & staff_screens
        check(got == want and r.get("crm") is ("/crm" in want) and r.get("spvs") is ("/spvs" in want),
              f"menu ({name}, real /users/me): staff screens = {sorted(want) or 'none'}; /crm and /spvs areas match",
              f"{sorted(got)}; crm={r.get('crm')} spvs={r.get('spvs')}")
    for name in ("orgadmin", "support"):
        r = res.get(name) or {}
        check(r.get("internal") is True and r.get("staff") is False, f"menu ({name}): internal, not staff (no bypass "
              "controls)", f"{r.get('internal')} / {r.get('staff')}")
    oa = set((res.get("orgadmin") or {}).get("hrefs") or [])
    check({"/portfolio/instructions", "/portfolio/replication", "/admin/settings", "/documents"} <= oa,
          "org admin keeps its own screens (instructions, replication, settings, documents)", f"{sorted(oa)[:20]}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (app_service, rolbypassrls = false)")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(r["rolbypassrls"] is False, "isolation is measured on a non-bypassing role")
    a = {x["id"] for x in await scoped(conn, ORG_A, "SELECT id FROM entities WHERE id = ANY($1::uuid[])", [E_F2, E_B])}
    b = {x["id"] for x in await scoped(conn, ORG_B, "SELECT id FROM entities WHERE id = ANY($1::uuid[])", [E_F2, E_B])}
    check(a == {E_F2} and b == {E_B}, "RLS: org A sees only its entity, org B only its own", f"{a} / {b}")
    for name in ("orgadmin", "support"):
        c1, b1 = await api(PERSONAS[name], "GET", f"/api/v1/entities/{E_B}/full")
        c2, _ = await api(PERSONAS[name], "GET", f"/api/v1/entities/{E_B}")
        check(c1 == 404 and c2 == 404 and "SR1 Org B" not in dumps(b1),
              f"{name} (org A, internal) on org B's entity: /full 404, /entities/{{id}} 404", f"{c1}/{c2}")
    c1, _ = await api(U_OTHER, "GET", f"/api/v1/entities/{E_F2}/full")
    c2, _ = await api(U_OTHER, "GET", f"/api/v1/entities/{E_F2}")
    check(c1 == 403 and c2 == 404, "org B's member on org A's entity: /full 403 (not internal), /entities/{id} 404",
          f"{c1}/{c2}")


async def y_routes(conn):
    section("[Y] Routes — own changes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: the route record is CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(code == 0 and m is not None and m.group(1) == "0", "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_bugfix2.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_bugfix2.py")], REPO, timeout=2700, env=env)
    m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(m is not None and m.group(2) == "0" and code == 0, "verify_bugfix2.py: 0 FAIL",
          f"{m.group(0) if m else 'no TOTAL'}; {fails[:4]} {err[-200:] if not m else ''}")


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
        section("[Y] Task 1 — see vs bypass for every use (the 1a table), 1b / 1c findings")
        self_task1(check, find)
        await run("concepts", y_concepts, conn)
        await run("reproduce", y_reproduce, conn)
        await run("routes matrix", y_routes_matrix, conn)
        await run("bypass", y_bypass, conn)
        await run("member scope", y_member_scope, conn)
        await run("profile", y_profile, conn)
        await run("menu", y_menu, conn)
        await run("isolation", y_isolation, conn)
        section("[Y] Static — authz.py, regression set, PROJECT_STATUS")
        self_static(check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
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
                    check(not off, f"real {t}: nothing but {', '.join(ACTIVITY_COLUMNS[t])} changed on any row "
                          "(no row added / removed; no role / status / org / profile change)", f"{off}")
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
