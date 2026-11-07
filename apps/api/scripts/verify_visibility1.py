"""verify_visibility1 — team-based staff visibility, enforced per org (VISIBILITY1, 2026-11-07).

Joe's rules (2026-11-04): org admins see every family of their org; a family NO ONE is assigned to is visible only to
super admin, org admins and its owner / creator; every other staff member sees the families assigned to them, to a team
they are on, or to anyone who reports to them. The org switch org_settings 'staff_visibility.enforced' (default false)
turns it on; off = exactly the behaviour before the sprint.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch and R2 are stubbed with their CURRENT signatures, and httpx's network transport refuses.

The real app against the live database, in TWO fixture orgs (99000000-…-000000590001 / 590002), ``is_test = true``
(asserted), as are the fixture users. Fixture org A: a super admin, an org admin, support staff S1 (on team T) and S2
(not), a manager M (S1 reports to M), an internal user NP without manage_members, a member; families F1 (assigned to
S1), F2 (assigned to team T), F3 (unassigned, created by S2). Real orgs are only READ; their enforcement stays OFF and
their assignments / teams / managers are fingerprinted (count + checksum) before and after. Teardown at start and end
via _verify_cleanup by fixture id.

--self-checks-only: git / route-record / static / pure-function checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"

import asyncio
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(_site))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)

SPRINT_KEY = "visibility1"
BEFORE_REF = "ade331f"   # visibility1-work base: "merge rollup2"

#: (method, path) → a substring the route record's "after" gate must carry
NEW_ROUTES = {
    ("GET", "/api/v1/admin/staff-visibility"): "manage_org_settings",
    ("PUT", "/api/v1/admin/staff-visibility/enforcement"): "manage_org_settings",
    ("GET", "/api/v1/admin/staff-visibility/unassigned"): "manage_members",
    ("POST", "/api/v1/admin/staff-visibility/bulk-assign"): "manage_members",
    ("GET", "/api/v1/staff-visibility/entities/{entity_id}/team"): "manage_members",
    ("POST", "/api/v1/staff-visibility/entities/{entity_id}/team"): "manage_members",
    ("GET", "/api/v1/staff-visibility/households/{household_id}/team"): "manage_members",
    ("POST", "/api/v1/staff-visibility/households/{household_id}/team"): "manage_members",
    ("DELETE", "/api/v1/staff-visibility/assignments/{assignment_id}"): "manage_members",
    ("GET", "/api/v1/admin/staff/people"): "manage_members",
    ("PUT", "/api/v1/admin/staff/users/{user_id}/manager"): "manage_members",
}
#: existing routes whose gate the sprint deliberately changed (rbac manage_members → authz org admin OR manage_members)
CHANGED_PREFIX = "/api/v1/admin/staff/"

TASK1 = [
    ("docs/VISIBILITY1_DISCOVERY.md", "## 1a. The unit of visibility — a family", "1a: a family = a household + closure"),
    ("docs/VISIBILITY1_DISCOVERY.md", "**The closure rule — an assignment to any one record grants the whole family:**",
     "1a: the closure rule"),
    ("docs/VISIBILITY1_DISCOVERY.md", "**An entity in two households**", "1a: an entity in two households"),
    ("docs/VISIBILITY1_DISCOVERY.md", "## 1b. Owner / creator", "1b: owner / creator columns and the family rule"),
    ("docs/VISIBILITY1_DISCOVERY.md", "## 1c. Every internal staff-data read", "1c: the read routes"),
    ("docs/VISIBILITY1_DISCOVERY.md", "**[FIND] \"Today every internal user sees every family\" is not quite true.**",
     "1c: five surfaces already restricted staff to the legacy assignment set"),
    ("docs/VISIBILITY1_DISCOVERY.md", "## 1d. Writes that must also require visibility", "1d: writes"),
    ("docs/VISIBILITY1_DISCOVERY.md", "## 1e. Existing UI for teams, membership, managers, assignments", "1e: UI"),
    ("docs/VISIBILITY1_DISCOVERY.md", "## 1f. Where the visible set is computed", "1f: once per request"),
    ("docs/VISIBILITY1_ROUTES.md", "| tag | meaning |", "1c: the generated route table"),
]
CODE_NEEDLES = [
    ("apps/api/services/family_visibility.py", "class FamilyGraph:", "Task 2: the one rule (family graph)"),
    ("apps/api/services/family_visibility.py", 'SETTING_KEY = "staff_visibility.enforced"', "Task 2: the org switch key"),
    ("apps/api/services/family_visibility.py", "async def family_path_guard(request) -> None:",
     "Task 3: the one guard"),
    ("apps/api/main.py", "dependencies=[Depends(_family_path_guard)]", "Task 3: the guard is app-wide"),
    ("apps/api/main.py", "response = await _scrub_family_rows(request, response)", "Task 3: the response scrubber"),
    ("apps/api/services/authz.py", "visibility_enforced=_visibility_flag(row),",
     "Task 6: the switch rides on the Principal's one statement"),
    ("apps/api/services/staff_visibility.py", "ruled = await staff_visible_entity_ids(pool, user_id, org_id)",
     "Task 3: legacy callers switch to the rule"),
    ("apps/api/services/org_settings.py", 'if key == "staff_visibility.enforced":',
     "Task 5: the generic settings path refuses the switch"),
    ("apps/web/components/visibility/TeamPanel.jsx", "export function TeamView(", "Task 4: Team panel"),
    ("apps/web/components/visibility/StaffVisibilityAdmin.jsx", "export function PreviewView(",
     "Task 5: preview + switch"),
    ("apps/web/components/visibility/StaffVisibilityAdmin.jsx", "export function UnassignedView(",
     "Task 4: Unassigned families"),
    ("apps/web/app/portfolio/households/[id]/page.js", "<TeamPanel kind=\"household\"",
     "Task 4: the Team panel on a household"),
    ("apps/web/app/crm/[id]/page.js", "<TeamPanel kind=\"entity\"", "Task 4: the Team panel on a CRM record"),
    ("docs/STAFF_VISIBILITY.md", "## How to turn it on", "Task 7: docs"),
    ("docs/STAFF_VISIBILITY.md", "## The Olivo demo", "Task 7: how the Olivo demo is assigned"),
    ("apps/api/services/held_away.py", 'NO_MASTER_ASSET_TYPES = NO_MASTER_KINDS | frozenset({"cash_holding"})',
     "FIX 1 (H): the never-through-the-master kinds as stored asset types, derived from BUGFIX4's one list"),
    ("apps/api/services/portfolio_manual.py", "AND NOT (a.asset_type = ANY($3::text[])) ",
     "FIX 1 (H): the relink skips every one of those kinds (was: only 'cash')"),
    ("apps/api/scripts/revert_relinks.py", "from services.held_away import NO_MASTER_ASSET_TYPES",
     "FIX 1 (H): revert_relinks.py reads the same list"),
    ("apps/api/services/users.py", "await FV.preload_scope(conn, request, principal)",
     "FIX 1 (B–D): the visible set rides on the Principal's transaction"),
    ("apps/api/routers/approvals.py", "visible = await BA.visible_item_ids(",
     "FIX 1 (E): the bulk decision filters through an internal, ungated helper"),
]


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _key(rec: dict) -> tuple:
    return ((rec.get("methods") or [""])[0], rec["path"])


def self_task1(check, find):
    for rel, needle, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or _read(rel)
        check(needle in txt, f"Task 1 — {meaning}", f"{rel}: needle missing")


def self_static(check, find):
    for rel, needle, meaning in CODE_NEEDLES:
        check(needle in _read(rel), meaning, f"{rel}: needle missing")
    check("verify_visibility1.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. VISIBILITY1 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    steps = ["create teams", "Assign the\n   Olivo demo", "Review the preview", "Switch on"]
    missing = [s for s in steps if s not in top]
    order = [top.find(s) for s in steps]
    check(bool(top and not missing and order == sorted(order) and "DECISION FOR JOE — owner semantics" in top
               and "The switch is OFF for every org." in top),
          "Task 9: PROJECT_STATUS — the rule, the owner decision, the switch OFF, operator steps IN ORDER",
          f"missing {missing} {order}")
    old_sv = VS.git_show(BEFORE_REF, "apps/api/services/staff_visibility.py") or ""
    check(bool("``org_admin`` is DELIBERATELY excluded" in old_sv),
          "REPRODUCED before the sprint: the resolver deliberately excluded org admins (Joe reversed it)")
    old_authz = VS.git_show(BEFORE_REF, "apps/api/services/authz.py") or ""
    m = re.search(r"async def member_entity_scope\(.*?\n(?=\n\n|\ndef |\nasync def )", old_authz, re.S)
    check(bool(m and "if _sees_unscoped(request, p):\n        return None" in m.group(0)),
          "REPRODUCED before the sprint: every internal reader was unscoped (member_entity_scope → None)")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if want not in str((new.get(k) or {}).get("after") or "")]
    check(not wrong, "every new route carries its stated gate", f"{wrong}")
    changed = [own.end[k]["path"] for k in own.changed]
    check(bool(changed and all(p.startswith(CHANGED_PREFIX) for p in changed)),
          "the only existing gates changed are the teams / assignments routes (org admin OR manage_members)",
          f"{changed}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def _graph_row(ents, households=(), memberships=(), edges=(), assignments=(), team_members=(), teams=(), users=(),
               grants=(), accounts=()):
    E = lambda i, cb=None, rm=None, r=False: {"id": i, "name": i, "type": "individual", "cb": cb, "rm": rm,  # noqa: E731
                                               "ph": None, "r": r, "live": True}
    return {"entities": json.dumps([E(*e) if isinstance(e, tuple) else E(e) for e in ents]),
            "households": json.dumps(list(households)), "memberships": json.dumps(list(memberships)),
            "edges": json.dumps(list(edges)), "assignments": json.dumps(list(assignments)),
            "team_members": json.dumps(list(team_members)), "teams": json.dumps(list(teams)),
            "users": json.dumps(list(users)), "restricted_grants": json.dumps(list(grants)),
            "accounts": json.dumps(list(accounts))}


def self_pure(check, find):
    """The rule as pure functions, no DB — each refusal shown next to the admission."""
    from services import family_visibility as FV

    row = _graph_row(
        ["p1", "t1", "p2", ("p3", "S2"), ("t3", "S2"), "spv", "lone", "c1", "c2", ("sec", None, None, True)],
        households=[{"id": "H1", "name": "F1", "cb": None}, {"id": "H2", "name": "F2", "cb": None},
                    {"id": "H3", "name": "F3", "cb": "S2"}],
        memberships=[{"h": "H1", "e": "p1"}, {"h": "H2", "e": "p2"}, {"h": "H3", "e": "p3"}, {"h": "H1", "e": "sec"}],
        edges=[{"p": "p1", "c": "t1"}, {"p": "p3", "c": "t3"}, {"p": "t1", "c": "spv"}, {"p": "p2", "c": "spv"},
               {"p": "c1", "c": "c2"}, {"p": "c2", "c": "c1"}],
        assignments=[{"id": "a1", "e": "t1", "u": "S1", "t": None, "l": "Lead"},
                     {"id": "a2", "e": "p2", "u": None, "t": "T", "l": None}],
        team_members=[{"t": "T", "u": "S1"}], teams=[{"id": "T", "name": "Team"}],
        users=[{"id": "S1", "m": "M"}, {"id": "S2", "m": None}, {"id": "M", "m": None}, {"id": "X", "m": "M2"},
               {"id": "M2", "m": "X"}],
        accounts=[{"id": "acc1", "e": "p1", "h": "H1"}, {"id": "acc3", "e": "p3", "h": "H3"}])
    g = FV.FamilyGraph(row)
    s1, s2, m = (FV.scope_from_graph(g, u) for u in ("S1", "S2", "M"))
    check(bool(s1.family_keys == {"household:H1", "household:H2"}), "S1 sees F1 (assigned) and F2 (team)",
          f"{sorted(s1.family_keys)}")
    check(bool(m.family_keys == s1.family_keys), "M sees what S1 (who reports to M) sees", f"{sorted(m.family_keys)}")
    check(bool(s2.family_keys == {"household:H3"}), "S2 sees only F3 (created by S2, unassigned)",
          f"{sorted(s2.family_keys)}")
    check(bool("t1" in s1.entity_ids and "p1" in s1.entity_ids and "t1" not in s2.entity_ids),
          "closure: an assignment on F1's trust grants F1's member; S2 gets none of F1", f"{sorted(s1.entity_ids)}")
    check(bool("spv" in s1.entity_ids and "spv" not in s2.entity_ids
               and g.grant("spv") == (set(), set(g.closure("spv")))),
          "a vehicle shared by F1 and F2 is seen by their staff, and assigning it grants only itself")
    check(bool("sec" not in s1.entity_ids), "restricted-access entity filtered out without a grant")
    row_r = json.loads(row["restricted_grants"] or "[]") + [{"e": "sec", "u": "S1"}]
    g_r = FV.FamilyGraph({**row, "restricted_grants": json.dumps(row_r)})
    check(bool("sec" in FV.scope_from_graph(g_r, "S1").entity_ids), "…and seen with a restricted_access_grants row")
    check(bool({"entity:c1"} <= set(g.families) or {"entity:c2"} <= set(g.families)),
          "an ownerless cycle still forms a family (no entity is left out)", f"{sorted(g.families)}")
    check(bool(FV.scope_from_graph(g, "X").family_keys == set()), "a manager cycle (X ↔ M2) terminates; sees nothing")
    check(bool(s1.account_ids == {"acc1"} and s2.account_ids == {"acc3"}), "accounts follow their family")
    a3 = json.loads(row["assignments"]) + [{"id": "a3", "e": "p3", "u": "S1", "t": None, "l": None}]
    g3 = FV.FamilyGraph({**row, "assignments": json.dumps(a3)})
    check(bool(FV.scope_from_graph(g3, "S2").family_keys == set()
               and "household:H3" in FV.scope_from_graph(g3, "S1").family_keys),
          "owner rule: after F3 is assigned to S1, its creator S2 no longer sees it")
    ents_rm = json.loads(row["entities"])
    for e in ents_rm:
        if e["id"] == "p3":
            e["rm"] = "S2"
    g4 = FV.FamilyGraph({**row, "assignments": json.dumps(a3), "entities": json.dumps(ents_rm)})
    check(bool("household:H3" in FV.scope_from_graph(g4, "S2").family_keys),
          "owner rule: a RELATIONSHIP MANAGER keeps the family after it is assigned to someone else")
    # FIX 1 (A, VERIFY) — g4 also carries a3 (F3 assigned to S1, who reports to M), so M sees F3 through his
    # report's ASSIGNMENT on g3 and g4 alike; the RM change is the only difference between g3 and g4.
    m3, m4 = FV.scope_from_graph(g3, "M").family_keys, FV.scope_from_graph(g4, "M").family_keys
    find("M's families: original graph / g3 (+ a3) / g4 (+ a3 + RM)",
         f"{sorted(m.family_keys)} / {sorted(m3)} / {sorted(m4)}")
    check(bool(m4 == m3 and "household:H3" in m3), "a report's relationship-manager change adds nothing to the "
          "manager (M on g4 == M on g3; F3 is seen through S1's assignment)", f"{sorted(m3)} / {sorted(m4)}")
    ents5 = json.loads(row["entities"])
    for e in ents5:
        if e["id"] == "lone":
            e["rm"] = "S1"            # S1 is ONLY the relationship manager of the standalone 'lone' (no assignment)
        if e["id"] == "c1":
            e["cb"] = "S1"            # S1 ONLY created the unassigned ownerless-cycle family
    g5 = FV.FamilyGraph({**row, "entities": json.dumps(ents5)})
    s1_5, m5 = FV.scope_from_graph(g5, "S1").family_keys, FV.scope_from_graph(g5, "M").family_keys
    rm_fam = next(iter(g5.grant("lone")[0]), None)
    cb_fam = next(iter(g5.grant("c1")[0]), None)
    check(bool(rm_fam and cb_fam and {rm_fam, cb_fam} <= s1_5 and rm_fam not in m5 and cb_fam not in m5
               and m5 == m.family_keys),
          "a manager does not inherit a report's relationship-managed / created families (S1 sees them, M does not)",
          f"S1 {sorted(s1_5)} / M {sorted(m5)}")
    sc = FV.scope_from_graph(g, "S2")
    payload = {"rows": [{"id": "p1", "x": 1}, {"entity_id": "t1"}, {"entity_id": "p3"}, {"entity_id": "not-org"},
                        {"account_id": "acc1"}, {"household_id": "H3", "nested": [{"owner_entity_id": "t1"}]}]}
    out = FV.scrub(payload, sc)
    check(bool(out == {"rows": [{"entity_id": "p3"}, {"entity_id": "not-org"}, {"household_id": "H3", "nested": []}]}),
          "scrub: removes rows naming an invisible family (own id, entity, account, nested), keeps visible and "
          "non-family ids", f"{out}")
    check(bool(FV.setting_truthy(True) and FV.setting_truthy("true") and not FV.setting_truthy("false")
               and not FV.setting_truthy(None) and not FV.setting_truthy('"true"') and not FV.setting_truthy(1)),
          "only a stored jsonb true enforces")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)
    self_pure(check, find)


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


if __name__ == "__main__" and VS.SELF_CHECKS_FLAG in sys.argv:
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
from services import family_visibility as FV  # noqa: E402
from services import rbac  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.assistant_actions import queries as AQ  # noqa: E402
from services.authz import load_principal  # noqa: E402
from services.database import close_pool, get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.document_embedding import document_visible, visible_entity_ids  # noqa: E402
from services.staff_visibility import get_staff_visible_entity_ids  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "visibility1verify"
NAME = "V1V "             # reserved fixture name prefix (asserted to match no real row)
SEED_WORD = "Zephyrine"   # the distinctive F1 name — proved never to reach a caller who cannot see F1


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000059{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
ORGADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                            "manage_portfolio", "view_proposals", "manage_proposals"})
SUPPORT_WANTED = frozenset({"manage_members", "edit_crm_contact", "view_members", "view_portfolio", "manage_portfolio",
                            "view_proposals", "manage_proposals", "manage_documents", "view_held_away"})
NP_WANTED = frozenset({"view_members", "view_portfolio"})
U_SUPER, U_OA, U_S1, U_S2, U_M, U_NP, U_MEMBER, U_OB = (_id(n) for n in range(10, 18))
# uid: (org, users.role, short name, role name, wanted permissions)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_S1: (ORG_A, "member", "s1", "support_staff", SUPPORT_WANTED),
    U_S2: (ORG_A, "member", "s2", "support_staff", SUPPORT_WANTED),
    U_M: (ORG_A, "member", "manager", "support_staff", SUPPORT_WANTED),
    U_NP: (ORG_A, "member", "noperm", "advisor", NP_WANTED),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OB: (ORG_B, "org_admin", "obadmin", "org_admin", ORGADMIN_PERMS),
}
INTERNAL = [U_SUPER, U_OA, U_S1, U_S2, U_M, U_NP]
TEAM_T = _id(40)
TEAMS = [TEAM_T]
H1, H2, H3, HB = _id(60), _id(61), _id(62), _id(63)
HHS = [H1, H2, H3, HB]
# entities: key → (id, org, type, name, created_by)
E = {
    "p1": (_id(30), ORG_A, "individual", f"{SEED_WORD} Aurelio", None),
    "t1": (_id(31), ORG_A, "trust", f"{SEED_WORD} Family Trust", None),
    "p2": (_id(32), ORG_A, "individual", "Bramble Person", None),
    "p3": (_id(33), ORG_A, "individual", "Quillon Person", U_S2),
    "pb": (_id(34), ORG_B, "individual", "Org B Person", None),
}
ACC1 = _id(70)
#: FIX 1 (H) — fixture manual assets, all three carrying the SAME master-resolving ticker; only their kind differs
RL_DIGITAL, RL_CASH, RL_EQUITY = _id(90), _id(91), _id(92)
RL_TICKER = "BTC"
FIX1_REF = "fde0ad5"      # the visibility1 commit: relink_manual_assets as the nightly ran it at 07:00:56 UTC
ASSET1, POS1, DOC1, NOTE1, LIA1, NOTIF1 = _id(80), _id(81), _id(82), _id(83), _id(84), _id(85)
AS_OF = date(2026, 11, 4)
MISSING = UUID("99000000-0000-0000-0000-000000599999")   # never seeded — "a missing id"

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.households", "public.household_memberships", "public.accounts", "public.staff_assignments",
           "public.teams", "public.team_members", "public.org_settings", "public.documents", "public.entity_notes",
           "public.notifications", "public.notification_recipients", "public.role_permissions", "public.user_roles",
           "public.delegate_grants",
           "portfolio.assets", "portfolio.positions", "portfolio.liabilities")
#: NOT counted, by name: public.audit_log and public.member_todos — real activity writes them during a run.
#: Real rows fingerprinted by their real primary key. users is fingerprinted on a NAMED projection (id, role,
#: manager_id, status, is_active) — the columns real activity updates (last_login_at, updated_at, contact_email_at)
#: are excluded by that projection, by name.
REAL_FINGERPRINTED = ("public.staff_assignments", "public.teams", "public.team_members")

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def eid(k: str) -> UUID:
    return E[k][0]


@asynccontextmanager
async def rls(org):
    """The app's own RLS context (org GUC, not super admin) around a DIRECT service call from the verify — the pool
    wrapper applies it; without it every read is an RLS zero-row read that would fail for the wrong reason."""
    tokens = set_rls_context(str(org), False)
    try:
        yield
    finally:
        reset_rls_context(tokens)


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:700]


def body_text(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


# ═══ DB helpers (RLS context set explicitly on every read) ══════════════════════════════════════════════════════
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
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype::text = "
                                "'p' ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str, *, where: str = "true", args=()) -> tuple[int, str]:
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = ", ".join(f't."{c}"' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY {key}),"
                               f" '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


def _real_where(table: str) -> str:
    if table == "public.team_members":
        return "team_id NOT IN (SELECT id FROM public.teams WHERE org_id = ANY($1::uuid[]))"
    return "NOT (org_id = ANY($1::uuid[]))"


async def real_prints(conn) -> dict:
    out = {t: await fingerprint(conn, t, where=_real_where(t), args=(FIXTURE_ORGS,)) for t in REAL_FINGERPRINTED}
    r = await platform(conn, "SELECT count(*) AS n, md5(COALESCE(string_agg(concat_ws(',', id, role, manager_id, "
                             "status, is_active), '|' ORDER BY id), '')) AS h FROM public.users WHERE NOT (org_id = "
                             "ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchrow")
    out["public.users(id, role, manager_id, status, is_active)"] = (int(r["n"]), r["h"])
    r = await platform(conn, "SELECT count(*) AS n, md5(COALESCE(string_agg(concat_ws(',', id, org_id, setting_value),"
                             " '|' ORDER BY id), '')) AS h FROM public.org_settings WHERE setting_key = $1 AND NOT "
                             "(org_id = ANY($2::uuid[]))", FV.SETTING_KEY, FIXTURE_ORGS, fetch="fetchrow")
    out["public.org_settings[staff_visibility.enforced]"] = (int(r["n"]), r["h"])
    return out


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


def _plan() -> dict:
    users = list(USERS)
    return {
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        "public.household_memberships": ("household_id = ANY($1::uuid[])", [HHS]),
        "public.team_members": ("team_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [TEAMS, users]),
        "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                    [FIXTURE_ORGS]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
    }


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only, by fixture id. users.manager_id is a self-FK among the fixture users: cleared first."""
    op = await operator_conn()
    try:
        await op.execute("UPDATE public.users SET manager_id = NULL WHERE id = ANY($1::uuid[]) AND manager_id IS NOT "
                         "NULL", list(USERS))
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               label="verify_visibility1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [HHS]),
            ("SELECT count(*) FROM public.team_members WHERE team_id = ANY($1::uuid[])", [TEAMS])])
    finally:
        await op.close()


async def cleanup_only(conn) -> int:
    log: list[str] = []
    try:
        log = await teardown(conn)
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


# ═══ Catalog + seed ═════════════════════════════════════════════════════════════════════════════════════════════
VALUES: dict[str, str] = {}
CATALOG_TABLES = ("public.accounts", "public.households", "public.entity_relationships", "public.entities",
                  "public.staff_assignments", "public.teams", "public.team_members", "public.org_settings",
                  "public.documents", "public.entity_notes", "public.notifications", "public.notification_recipients",
                  "portfolio.positions", "portfolio.assets", "portfolio.liabilities", "public.delegate_grants")


def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


def _pick(cons: dict, table: str, col: str, prefer: str) -> str:
    defs = [d for (t, _n), (k, d) in cons.items() if t == table and k == "c" and re.search(rf"\b{col}\b", d)]
    vals = _allowed(defs[0]) if defs else []
    return prefer if (not vals or prefer in vals) else vals[0]


async def y_catalog(conn):
    section("[Y] Constraints read from the catalog before seeding; reserved fixture prefixes match no real row")
    cons = {}
    for t in CATALOG_TABLES:
        for r in await platform(conn, "SELECT conname, contype::text AS k, pg_get_constraintdef(oid) AS d FROM "
                                      "pg_constraint WHERE conrelid = $1::regclass AND contype::text IN ('p','c','u',"
                                      "'f')", t):
            cons[(t, r["conname"])] = (r["k"], r["d"])
    kinds = {k for k, _ in cons.values()}
    check(bool(cons and "p" in kinds and "u" in kinds and "f" in kinds),
          f"{len(cons)} PK / CHECK / UNIQUE / FK constraints read (decoded as text)", f"{sorted(kinds)}")
    uq = [d for (t, _n), (k, d) in cons.items() if t == "public.org_settings" and k == "u"]
    check(bool(any("org_id, setting_key" in d for d in uq)), "org_settings is UNIQUE (org_id, setting_key) — the "
          "switch upsert keys on it", f"{uq}")
    VALUES["registration_type"] = _pick(cons, "public.accounts", "registration_type", "individual")
    VALUES["tax_status"] = _pick(cons, "public.accounts", "tax_status", "taxable")
    VALUES["custodian_code"] = _pick(cons, "public.accounts", "custodian_code", "ALTRUIST")
    VALUES["asset_class"] = _pick(cons, "portfolio.assets", "asset_class", "financial")
    VALUES["authority"] = _pick(cons, "portfolio.positions", "authority", "manual")
    VALUES["source_system"] = _pick(cons, "portfolio.positions", "source_system", "manual")
    VALUES["liability_kind"] = _pick(cons, "portfolio.liabilities", "kind", "mortgage")
    VALUES["delegate_scope"] = _pick(cons, "public.delegate_grants", "scope", "view_only")
    find("seeded with catalog-legal values", f"{VALUES}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE (display_name LIKE $1 OR display_name "
                                "LIKE $6) AND NOT (org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM households WHERE "
                                "name LIKE $1 AND NOT (org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE "
                                "auth0_sub LIKE $2 AND NOT (id = ANY($5::uuid[]))) + (SELECT count(*) FROM organizations "
                                "WHERE slug LIKE $3 AND NOT (id = ANY($4::uuid[]))) + (SELECT count(*) FROM teams WHERE "
                                "name LIKE $1 AND NOT (org_id = ANY($4::uuid[])))",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), f"%{SEED_WORD}%",
                          fetch="fetchval")
    check(real == 0, f"the reserved fixture prefixes (names, '{SEED_WORD}', subs, slugs, team names) match no real "
          "row", f"{real}")
    ids = await platform(conn, "SELECT count(*) FROM entities WHERE id::text LIKE '99000000-0000-0000-0000-00000059%' "
                               "AND NOT (org_id = ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval")
    check(ids == 0, "the reserved fixture id range matches no real row", f"{ids}")


ROLE_IDS: dict[tuple, UUID] = {}


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"VISIBILITY1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        role_perms: dict[str, set] = {}
        for _uid, (_org, _acct, _nm, rname, wanted) in USERS.items():
            role_perms.setdefault(rname, set()).update(wanted)
        dropped = {r: sorted(p - set(perm_ids)) for r, p in role_perms.items() if p - set(perm_ids)}
        if dropped:
            find("permissions not in the catalog (not granted)", f"{dropped}")
        for org in FIXTURE_ORGS:
            for rname, perms in role_perms.items():
                rid = await rbac.ensure_role(conn, org, rname, f"VISIBILITY1 fixture {rname}")
                for p in perms & set(perm_ids):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                ROLE_IDS[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, f"{TAG}-{nm}@test.local", f"{NAME}{nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, ROLE_IDS[(org, rname)])
        await conn.execute("UPDATE users SET manager_id = $1 WHERE id = $2", U_M, U_S1)        # S1 reports to M
        for k, (i, org, etype, name, cb) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, created_by) "
                               "VALUES ($1, $2, $3, 'active', $4::entity_type, $5)", i, org, NAME + name, etype, cb)
        await conn.execute("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
                           "ownership_pct) VALUES ($1, $2, $3, 'ownership', 100)", ORG_A, eid("p1"), eid("t1"))
        for hh, org, nm, cb in ((H1, ORG_A, f"{SEED_WORD} Household", None), (H2, ORG_A, "Bramble Household", None),
                                (H3, ORG_A, "Quillon Household", U_S2), (HB, ORG_B, "Org B Household", None)):
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                               hh, org, NAME + nm, cb)
        for hh, k in ((H1, "p1"), (H2, "p2"), (H3, "p3"), (HB, "pb")):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh,
                               eid(k))
        await conn.execute(
            "INSERT INTO accounts (id, org_id, household_id, account_number_masked, account_number_hash, custodian_code,"
            " registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, $3, '****5901', $4, $5, "
            "$6, $7, $8, true)", ACC1, ORG_A, H1, hashlib.sha256(f"{TAG}5901".encode()).hexdigest(),
            VALUES["custodian_code"], VALUES["registration_type"], VALUES["tax_status"], eid("p1"))
        await conn.execute(
            "INSERT INTO portfolio.assets (id, org_id, name, asset_class, asset_type, ownership_basis, valuation_method)"
            " VALUES ($1, $2, $3, $4, 'equity', 'value', 'market_price')", ASSET1, ORG_A,
            NAME + f"{SEED_WORD} Holding", VALUES["asset_class"])
        await conn.execute(
            "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
            "market_value, authority, source_system) VALUES ($1, $2, $3, $4, $5, 'value', 125000, $6, $7)",
            POS1, ORG_A, eid("t1"), ASSET1, AS_OF, VALUES["authority"], VALUES["source_system"])
        await conn.execute("INSERT INTO documents (id, org_id, entity_id, original_filename) VALUES ($1, $2, $3, $4)",
                           DOC1, ORG_A, eid("t1"), f"{NAME}{SEED_WORD} trust deed.pdf")
        await conn.execute("INSERT INTO entity_notes (id, org_id, entity_id, note_text) VALUES ($1, $2, $3, $4)",
                           NOTE1, ORG_A, eid("p1"), f"{NAME}{SEED_WORD} review note")
        await conn.execute(
            "INSERT INTO portfolio.liabilities (id, lineage_id, org_id, owner_entity_id, name, kind, current_balance, "
            "as_of) VALUES ($1, $1, $2, $3, $4, $5, 50000, $6)", LIA1, ORG_A, eid("p1"),
            NAME + f"{SEED_WORD} mortgage", VALUES["liability_kind"], AS_OF)
        await conn.execute("INSERT INTO notifications (id, org_id, event_type, title, body, resource_type, resource_id) "
                           "VALUES ($1, $2, 'visibility1.fixture', $3, 'fixture', 'entity', $4)", NOTIF1, ORG_A,
                           f"{NAME}{SEED_WORD} review due", eid("p1"))
        for u in (U_S1, U_S2):
            await conn.execute("INSERT INTO notification_recipients (org_id, notification_id, user_id) VALUES ($1, $2, "
                               "$3)", ORG_A, NOTIF1, u)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from) VALUES ($1, $2, $3, $4, now() - interval '1 day')", ORG_A, eid("p2"),
                           U_MEMBER, VALUES["delegate_scope"])                             # the member's own family
        await conn.execute("INSERT INTO teams (id, org_id, name) VALUES ($1, $2, $3)", TEAM_T, ORG_A, NAME + "Team T")
        await conn.execute("INSERT INTO team_members (team_id, user_id) VALUES ($1, $2)", TEAM_T, U_S1)
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label) VALUES "
                           "($1, $2, $3, 'Lead adviser')", ORG_A, eid("p1"), U_S1)          # F1 → S1
        await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_team_id) VALUES ($1, $2, $3)",
                           ORG_A, eid("p2"), TEAM_T)                                        # F2 → team T


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == len(FIXTURE_ORGS) and u == len(USERS)), "every fixture org and user is is_test = true", f"{o}/{u}")
    pool = await get_pool()
    ps = {}
    for uid in USERS:
        async with rls(USERS[uid][0]):
            async with pool.acquire() as c:
                ps[uid] = await load_principal(c, uid)
    shapes = {USERS[u][2]: (p.is_super_admin, p.is_internal, p.is_staff,
                            rbac.ORG_ADMIN_PERMISSION in p.permissions, "manage_members" in p.permissions)
              for u, p in ps.items()}
    check(bool(ps[U_SUPER].is_super_admin and FV.is_org_admin(ps[U_OA])
               and all(ps[u].is_internal and not ps[u].is_staff and not FV.is_org_admin(ps[u]) and
                       "manage_members" in ps[u].permissions for u in (U_S1, U_S2, U_M))
               and ps[U_NP].is_internal and "manage_members" not in ps[U_NP].permissions
               and not ps[U_MEMBER].is_internal),
          "fixture roles: super admin, org admin, three support staff with manage_members (internal, not a bypass), an "
          "internal user without manage_members, a member", f"{shapes}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []
NOTE_EXTRACTIONS: list = []
STMTS = {"n": 0}
GRAPH_LOADS = {"n": 0}


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(who, method, path, body=None, timeout=180.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(who)}"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text) if res.text else {}
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def install_stubs():
    """Each stub matches the CURRENT signature of what it replaces (read at install time)."""
    import main

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def no_r2(*a, **k):
        NETWORK_CALLS.append("r2")
        raise AssertionError("verify: R2 must never be called")

    storage.get_s3_client = no_r2

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_visibility1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    # FIX 1 (F) — POST /entities/{id}/notes schedules services.extraction.extract_from_note (an LLM call through the
    # LiteLLM proxy) as a background task; the admitted note in [Y] Writes reached it (three refused POSTs to
    # /v1/messages = one call + the SDK's two retries). Stubbed at its CURRENT signature; the call is recorded.
    from services import extraction as _extraction
    have = list(inspect.signature(_extraction.extract_from_note).parameters)
    if have != ["pool", "org_id", "note_id", "entity_id", "note_text"]:
        raise RuntimeError(f"extract_from_note signature drifted: {have}")

    async def no_extraction(pool, org_id, note_id, entity_id, note_text):
        NOTE_EXTRACTIONS.append(str(note_id))
        return {}

    _extraction.extract_from_note = no_extraction

    # statement counter (every asyncpg round trip) + graph-load counter (the visible set's one statement)
    for name in ("execute", "fetch", "fetchrow", "fetchval"):
        orig = getattr(asyncpg.Connection, name)

        def wrap(orig):
            async def counted(self, *a, **k):
                STMTS["n"] += 1
                return await orig(self, *a, **k)
            return counted
        setattr(asyncpg.Connection, name, wrap(orig))
    sig = list(inspect.signature(FV.load_graph).parameters)
    if sig != ["conn", "org_id"]:
        raise RuntimeError(f"load_graph signature drifted: {sig}")
    real_load = FV.load_graph

    async def counted_load(conn, org_id):
        GRAPH_LOADS["n"] += 1
        return await real_load(conn, org_id)

    FV.load_graph = counted_load


async def measured(who, method, path, body=None) -> tuple[int, dict, int]:
    n0 = STMTS["n"]
    code, b = await api(who, method, path, body)
    return code, b, STMTS["n"] - n0


async def set_switch(on: bool) -> None:
    c, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement", {"enforced": on, "confirm": True})
    if c != 200 or b.get("enforced") is not on:
        raise RuntimeError(f"switch {on}: {c} {dumps(b)}")


async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


def ids_in(body, key="id") -> set:
    rows = body if isinstance(body, list) else (body.get("rows") or body.get("items") or body.get("results") or [])
    return {str(r.get(key)) for r in rows if isinstance(r, dict) and r.get(key) is not None}


FAMILY_HH = {"F1": H1, "F2": H2, "F3": H3}


async def households_seen(who) -> set:
    c, b = await api(who, "GET", "/api/v1/admin/households")
    if c != 200:
        raise RuntimeError(f"households list for {USERS[who][2]}: {c} {dumps(b)}")
    return {k for k, h in FAMILY_HH.items() if str(h) in ids_in(b)}


async def crm_seen(who) -> tuple[set, dict]:
    c, b = await api(who, "GET", "/api/v1/entities?limit=500")
    if c != 200:
        raise RuntimeError(f"CRM list for {USERS[who][2]}: {c} {dumps(b)}")
    return {k for k in ("p1", "t1", "p2", "p3") if str(eid(k)) in ids_in(b)}, b


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_switch_off(conn):
    section("[Y] Switch OFF — every internal user sees F1–F3, with no visibility statement at all (today's behaviour)")
    pool = await get_pool()
    async with rls(ORG_A):
        async with pool.acquire() as c:
            p = await load_principal(c, U_S2)
    check(bool(p.visibility_enforced is False), "the Principal reads the switch: off (no row = default)")
    g0 = GRAPH_LOADS["n"]
    seen = {}
    for u in INTERNAL:
        ents, _ = await crm_seen(u)
        seen[USERS[u][2]] = sorted(ents)
    check(bool(all(v == ["p1", "p2", "p3", "t1"] for v in seen.values())),
          "CRM list: every internal user (super, org admin, S1, S2, M, NP) sees F1, F2, F3 and F1's trust", f"{seen}")
    hh = {USERS[u][2]: sorted(await households_seen(u)) for u in (U_SUPER, U_OA, U_S1, U_S2, U_M)}
    check(bool(all(v == ["F1", "F2", "F3"] for v in hh.values())), "households list: every assigner sees F1–F3",
          f"{hh}")
    codes = {USERS[u][2]: (await api(u, "GET", f"/api/v1/entities/{eid('t1')}"))[0] for u in INTERNAL}
    check(bool(set(codes.values()) == {200}), "F1's trust opens for every internal user", f"{codes}")
    _, b1 = await crm_seen(U_S1)
    _, b2 = await crm_seen(U_S2)
    check(bool(body_text(b1) == body_text(b2)), "S1 and S2 (different assignments) get IDENTICAL CRM list bodies")
    check(bool(GRAPH_LOADS["n"] == g0), "no visibility graph was read for any of those requests",
          f"{GRAPH_LOADS['n'] - g0} loads")
    async with rls(ORG_A):
        legacy = await get_staff_visible_entity_ids(pool, str(U_S1), str(ORG_A))
        check(bool(legacy == {str(eid("p1")), str(eid("p2"))}),
          "the legacy resolver is unchanged with the switch off (S1: direct p1 + team p2, NO closure)",
          f"{sorted(legacy)}")
    legacy_oa = await get_staff_visible_entity_ids(pool, str(U_OA), str(ORG_A))
    check(bool(legacy_oa == set()), "…and still excludes org admins with no assignment (today's legacy behaviour)",
          f"{sorted(legacy_oa)}")
    c, b = await api(U_OA, "GET", "/api/v1/admin/staff-visibility")
    check(bool(c == 200 and b.get("enforced") is False and b.get("people")),
          "the preview works with the switch off", f"{c} {dumps(b)[:200]}")


async def y_switch_gate(conn):
    section("[Y] The switch — org admin only, confirmed, audited; the generic settings route refuses the key")
    for who in (U_S1, U_NP, U_MEMBER):
        c, b = await api(who, "PUT", "/api/v1/admin/staff-visibility/enforcement", {"enforced": True, "confirm": True})
        check(c == 403, f"{USERS[who][2]} may not switch (403)", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement", {"enforced": True})
    check(c == 422, "without confirm → 422 (the preview must be confirmed)", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement",
                     {"enforced": True, "confirm": True, "org_id": str(ORG_B)})
    check(c == 422, "a body naming an org_id is refused (extra='forbid')", f"{c}")
    c, b = await api(U_OA, "PUT", f"/api/v1/orgs/{ORG_A}/settings/{FV.SETTING_KEY}", {"value": True})
    check(c == 400, "the generic settings route refuses staff_visibility.enforced", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "GET", "/api/v1/admin/staff-visibility")
    none_n = len(b.get("sees_none") or []) if c == 200 else -1
    c, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement",
                     {"enforced": True, "confirm": True, "acknowledged_sees_none": none_n + 7})
    check(c == 409, "a stale confirm (sees-none count changed) → 409", f"{c} {dumps(b)}")
    t0 = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                              "'staff_visibility.enforcement'", ORG_A, fetch="fetchval")
    c, b = await api(U_OA, "PUT", "/api/v1/admin/staff-visibility/enforcement",
                     {"enforced": True, "confirm": True, "acknowledged_sees_none": none_n})
    check(bool(c == 200 and b.get("enforced") is True and b.get("previous") is False),
          "org admin switches ON after the preview", f"{c} {dumps(b)[:200]}")
    stored = await scoped(conn, ORG_A, "SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = $2",
                          ORG_A, FV.SETTING_KEY, fetch="fetchval")
    check(bool(FV.setting_truthy(stored)), "re-read independently: the org's row is jsonb true", f"{stored!r}")
    t1 = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                              "'staff_visibility.enforcement'", ORG_A, fetch="fetchval")
    check(bool(t1 == t0 + 1), "the switch is audited (one staff_visibility.enforcement row)", f"{t0} → {t1}")
    other = await platform(conn, "SELECT count(*) FROM org_settings WHERE org_id = $1 AND setting_key = $2", ORG_B,
                           FV.SETTING_KEY, fetch="fetchval")
    check(other == 0, "switching org A wrote nothing for org B", f"{other}")


async def y_switch_on(conn):
    section("[Y] Switch ON — the rule")
    expected = {U_SUPER: ["F1", "F2", "F3"], U_OA: ["F1", "F2", "F3"], U_S1: ["F1", "F2"], U_M: ["F1", "F2"],
                U_S2: ["F3"]}
    for who, want in expected.items():
        got = sorted(await households_seen(who))
        check(got == want, f"{USERS[who][2]} sees {want}", f"got {got}")
    crm = {USERS[u][2]: sorted((await crm_seen(u))[0]) for u in (U_SUPER, U_OA, U_S1, U_S2, U_M, U_NP)}
    check(bool(crm["super"] == crm["orgadmin"] == ["p1", "p2", "p3", "t1"] and crm["s1"] == ["p1", "p2", "t1"]
               and crm["manager"] == ["p1", "p2", "t1"] and crm["s2"] == ["p3"] and crm["noperm"] == []),
          "CRM list: admins all; S1 / M F1 + F2 (incl. F1's trust); S2 F3 only; an unassigned internal user nothing",
          f"{crm}")
    c, b = await api(U_OA, "GET", "/api/v1/admin/staff-visibility")
    if c != 200:
        raise RuntimeError(f"preview {c} {dumps(b)}")
    by = {p["user_id"]: p for p in b.get("people") or []}
    mism = {}
    for who, want in expected.items():
        p = by.get(str(who)) or {}
        if p.get("sees_all"):
            ok = want == ["F1", "F2", "F3"]
            got = "all"
        else:
            keys = {f["key"] for f in p.get("families") or []}
            got = sorted(k for k, h in FAMILY_HH.items() if f"household:{h}" in keys)
            ok = got == want
        if not ok:
            mism[USERS[who][2]] = got
    check(not mism, "the preview matches what each fixture user actually sees", f"{mism}")
    s1 = by.get(str(U_S1)) or {}
    kinds = {f["name"]: sorted(r["kind"] for r in f.get("reasons") or []) for f in s1.get("families") or []}
    check(bool(kinds.get(NAME + f"{SEED_WORD} Household") == ["assignment"]
               and kinds.get(NAME + "Bramble Household") == ["team"]),
          "the preview says why: S1 F1 by assignment, F2 by team", f"{kinds}")
    m = by.get(str(U_M)) or {}
    check(bool(all(r["kind"] == "report" for f in m.get("families") or [] for r in f.get("reasons") or [])
               and m.get("family_count") == 2), "…and M's are via a report", f"{dumps(m)[:300]}")
    none_ids = {x["user_id"] for x in b.get("sees_none") or []}
    check(bool(str(U_NP) in none_ids and str(U_S1) not in none_ids), "the preview warns: NP would see no family",
          f"{none_ids}")


ROUTES_F1 = [
    ("entity (legal entity)", lambda x: f"/api/v1/entities/{x}", "t1"),
    ("entity full", lambda x: f"/api/v1/entities/{x}/full", "t1"),
    ("household", lambda x: f"/api/v1/admin/households/{x}", "H1"),
    ("account", lambda x: f"/api/v1/held-away/accounts/{x}", "ACC1"),
    ("position", lambda x: f"/api/v1/portfolio/positions/{x}", "POS1"),
    ("document", lambda x: f"/api/v1/documents/{x}/links", "DOC1"),
    ("notes", lambda x: f"/api/v1/entities/{x}/notes", "p1"),
    ("liabilities", lambda x: f"/api/v1/entities/{x}/liabilities", "p1"),
    ("team panel", lambda x: f"/api/v1/staff-visibility/entities/{x}/team", "p1"),
]
REAL_ID = {"t1": eid("t1"), "p1": eid("p1"), "H1": H1, "ACC1": ACC1, "POS1": POS1, "DOC1": DOC1}


async def y_closure(conn):
    section("[Y] Family closure — S1 opens F1's records; S2 gets not-found, identical to a missing id")
    for label, path, k in ROUTES_F1:
        c1, b1 = await api(U_S1, "GET", path(REAL_ID[k]))
        c2, b2 = await api(U_S2, "GET", path(REAL_ID[k]))
        cm, bm = await api(U_S2, "GET", path(MISSING))
        check(bool(c1 != 404 and c1 < 500), f"{label}: S1 (F1's adviser) is not refused not-found", f"{c1} {dumps(b1)}")
        check(bool(c2 == 404 and cm == 404 and b2 == bm), f"{label}: S2 → 404 with the SAME body as a missing id",
              f"S2 {c2} {dumps(b2)} / missing {cm} {dumps(bm)}")
    _, nb = await api(U_S1, "GET", f"/api/v1/entities/{eid('p1')}/notes")
    check(bool(str(NOTE1) in body_text(nb)), "S1 reads F1's note", dumps(nb))
    _, lb = await api(U_S1, "GET", f"/api/v1/entities/{eid('p1')}/liabilities")
    check(bool(str(LIA1) in body_text(lb)), "S1 reads F1's liability", dumps(lb))
    c, b = await api(U_S2, "GET", f"/api/v1/entities?owner_entity_id={eid('t1')}")
    check(c == 404, "a query-string id of an invisible family is refused the same way", f"{c}")


async def y_writes(conn):
    section("[Y] Writes refused on an invisible family (identical request admitted for F1's adviser)")
    before = await platform(conn, "SELECT (SELECT count(*) FROM entity_notes WHERE entity_id = $1) + (SELECT count(*) "
                                  "FROM entity_addresses WHERE entity_id = $1) + (SELECT count(*) FROM accounts WHERE "
                                  "household_id = $2) + (SELECT count(*) FROM entity_relationships WHERE to_entity_id = "
                                  "$3)", eid("p1"), H1, eid("t1"), fetch="fetchval")
    writes = [
        ("note", "POST", f"/api/v1/entities/{eid('p1')}/notes", {"note_text": NAME + "write probe"}),
        ("address", "POST", f"/api/v1/entities/{eid('p1')}/addresses", {"street1": "1 Probe St", "city": "Probe"}),
        ("account", "POST", f"/api/v1/admin/households/{H1}/accounts",
         {"custodian_code": VALUES.get("custodian_code"), "account_number": "5999", "primary_entity_id": str(eid("p1"))}),
        ("proposal", "POST", f"/api/v1/accounts/{ACC1}/proposals", {"trigger": "manual"}),
        ("liability edit", "PATCH", f"/api/v1/liabilities/{LIA1}", {"reason": "probe"}),
        ("ownership (body ids)", "POST", "/api/v1/entity-ownership",
         {"parent_id": str(eid("p3")), "child_id": str(eid("t1")), "ownership_pct": 1}),
    ]
    for label, method, path, body in writes:
        c, b = await api(U_S2, method, path, body)
        check(c == 404, f"{label}: S2 refused (404)", f"{c} {dumps(b)}")
    after = await platform(conn, "SELECT (SELECT count(*) FROM entity_notes WHERE entity_id = $1) + (SELECT count(*) "
                                 "FROM entity_addresses WHERE entity_id = $1) + (SELECT count(*) FROM accounts WHERE "
                                 "household_id = $2) + (SELECT count(*) FROM entity_relationships WHERE to_entity_id = "
                                 "$3)", eid("p1"), H1, eid("t1"), fetch="fetchval")
    check(bool(after == before), "the refused writes left F1 unchanged (re-read)", f"{before} → {after}")
    c, b = await api(U_S2, "POST", f"/api/v1/entities/{MISSING}/notes", {"note_text": "x"})
    c2, b2 = await api(U_S2, "POST", f"/api/v1/entities/{eid('p1')}/notes", {"note_text": "x"})
    check(bool(c == c2 == 404 and b == b2), "a write to F1 reads exactly like a write to a missing id", f"{b} / {b2}")
    c, b = await api(U_S1, "POST", f"/api/v1/entities/{eid('p1')}/notes", {"note_text": NAME + "admitted note"})
    n = await platform(conn, "SELECT count(*) FROM entity_notes WHERE entity_id = $1 AND note_text = $2", eid("p1"),
                       NAME + "admitted note", fetch="fetchval")
    check(bool(c == 201 and n == 1), "the identical note request from S1 is admitted and persisted", f"{c} {n}")
    check(bool(c == 201 and NOTE_EXTRACTIONS.count(str(b.get("id"))) == 1),
          "…its note extraction reached the stub (no LLM call), once", f"{NOTE_EXTRACTIONS}")


async def y_lists(conn):
    section(f"[Y] Lists, search, counts, assistant, documents, notifications — '{SEED_WORD}' never reaches S2")
    pool = await get_pool()
    probes = [("CRM list", "GET", "/api/v1/entities?limit=500"),
              ("CRM list search", "GET", f"/api/v1/entities?search={SEED_WORD}"),
              ("CRM search", "GET", f"/api/v1/entities/search?q={SEED_WORD}"),
              ("households", "GET", "/api/v1/admin/households"),
              ("households net worth", "GET", "/api/v1/admin/households/networth"),
              ("positions", "GET", "/api/v1/portfolio/positions"),
              ("notifications", "GET", "/api/v1/notifications"),
              ("staff assignments", "GET", "/api/v1/admin/staff/assignments"),
              ("unassigned families", "GET", "/api/v1/admin/staff-visibility/unassigned")]
    positive = 0
    for label, method, path in probes:
        c2, b2 = await api(U_S2, method, path)
        c1, b1 = await api(U_S1, method, path)
        check(bool(c2 < 500 and SEED_WORD not in body_text(b2)), f"{label}: '{SEED_WORD}' absent for S2",
              f"{c2} {dumps(b2)}")
        if SEED_WORD in body_text(b1) or str(eid("p1")) in body_text(b1):
            positive += 1
    check(positive >= 5, "positive control: the same probes DO show F1 to S1", f"{positive} of {len(probes)}")
    c1, n1 = await api(U_S1, "GET", "/api/v1/notifications/count")
    c2, n2 = await api(U_S2, "GET", "/api/v1/notifications/count")
    check(bool(c1 == c2 == 200 and n1.get("unread_count", 0) >= 1 and n2.get("unread_count") == 0),
          "a notification about F1 counts for S1, not for S2 (both are recipients)", f"{n1} {n2}")
    async with rls(ORG_A):
        r1 = await AQ._count_entities(pool, str(U_S1), str(ORG_A), is_staff=True)
        r2 = await AQ._count_entities(pool, str(U_S2), str(ORG_A), is_staff=True)
    s1t, s2t = body_text(r1), body_text(r2)
    check(bool(SEED_WORD in s1t and SEED_WORD not in s2t), "assistant entities.count: S1's answer includes F1, S2's not",
          f"{s2t[:300]}")
    async with rls(ORG_A):
        v1 = await visible_entity_ids(pool, str(ORG_A), str(U_S1), True)
        v2 = await visible_entity_ids(pool, str(ORG_A), str(U_S2), True)
    check(bool(document_visible(eid("t1"), v1, True) and not document_visible(eid("t1"), v2, True)),
          "document search's rule: F1's trust deed visible to S1, not to S2")
    sql_f1 = {str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT e.id FROM entities e WHERE e.org_id = $1 AND "
                                                             "(e.id = $2 OR e.id IN (SELECT to_entity_id FROM "
                                                             "entity_relationships WHERE from_entity_id = $2))",
                                                 ORG_A, eid("p1"))}
    check(bool(sql_f1 and sql_f1 <= v1 and not (sql_f1 & v2)),
          "the same family predicate run directly in SQL: F1 ⊆ S1's set, disjoint from S2's", f"{sorted(sql_f1)}")
    c, b = await api(U_S2, "GET", "/api/v1/approvals/summary")
    check(bool(c in (200, 403) and SEED_WORD not in body_text(b)), "approvals summary carries nothing of F1 for S2",
          f"{c}")


async def y_owner_rule(conn):
    section("[Y] Owner rule — after F3 is assigned to S1, its creator S2 loses it; a relationship manager keeps it")
    c, b = await api(U_S1, "POST", f"/api/v1/staff-visibility/entities/{eid('p3')}/team",
                     {"assigned_to_user_id": str(U_S1)})
    check(c == 404, "S1 cannot assign F3 (a family S1 cannot see) — 404", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "POST", f"/api/v1/staff-visibility/households/{H3}/team",
                     {"assigned_to_user_id": str(U_S1), "role_label": "Associate"})
    aid = (b or {}).get("assignment_id")
    check(bool(c == 201 and aid), "the org admin assigns F3 to S1 from the household's Team panel", f"{c} {dumps(b)}")
    c2, _ = await api(U_S2, "GET", f"/api/v1/entities/{eid('p3')}")
    c1, _ = await api(U_S1, "GET", f"/api/v1/entities/{eid('p3')}")
    check(bool(c2 == 404 and c1 == 200), "creator S2 no longer sees F3; S1 now does", f"S2 {c2} S1 {c1}")
    await platform(conn, "UPDATE entities SET relationship_manager_id = $1 WHERE id = $2", U_S2, eid("p3"),
                   fetch="execute")
    c2, _ = await api(U_S2, "GET", f"/api/v1/entities/{eid('p3')}")
    check(c2 == 200, "with S2 as relationship manager, S2 keeps F3 although it is assigned", f"{c2}")
    cm, _ = await api(U_M, "GET", f"/api/v1/entities/{eid('p3')}")
    check(cm == 200, "M sees F3 through S1's assignment", f"{cm}")
    await platform(conn, "UPDATE entities SET relationship_manager_id = NULL WHERE id = $1", eid("p3"), fetch="execute")
    if aid:
        c, _ = await api(U_OA, "DELETE", f"/api/v1/staff-visibility/assignments/{aid}")
        c2, _ = await api(U_S2, "GET", f"/api/v1/entities/{eid('p3')}")
        check(bool(c == 200 and c2 == 200), "assignment removed → F3 is unassigned again → its creator sees it",
              f"{c} {c2}")


async def y_assign_screens(conn):
    section("[Y] Assignment screens — audited; only permitted roles assign (identical request refused / admitted)")
    body = {"assigned_to_team_id": str(TEAM_T), "role_label": "Service"}
    a0 = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action IN "
                              "('create_staff_assignment', 'delete_staff_assignment')", ORG_A, fetch="fetchval")
    c, b = await api(U_NP, "POST", f"/api/v1/staff-visibility/entities/{eid('p1')}/team", body)
    check(c in (403, 404), "an internal user without manage_members is refused", f"{c} {dumps(b)}")
    c, b = await api(U_MEMBER, "POST", f"/api/v1/staff-visibility/entities/{eid('p1')}/team", body)
    check(c == 403, "a member is refused", f"{c}")
    c, b = await api(U_S2, "POST", f"/api/v1/staff-visibility/entities/{eid('p1')}/team", body)
    check(c == 404, "S2 (manage_members, cannot see F1) → 404", f"{c}")
    c, b = await api(U_S1, "POST", f"/api/v1/staff-visibility/entities/{eid('p1')}/team", body)
    aid = (b or {}).get("assignment_id")
    check(bool(c == 201 and aid), "S1 (manage_members, sees F1) — the identical request is admitted", f"{c} {dumps(b)}")
    row = await scoped(conn, ORG_A, "SELECT assigned_to_team_id, role_label FROM staff_assignments WHERE id = $1",
                       UUID(aid) if aid else MISSING, fetch="fetchrow")
    check(bool(row and row["assigned_to_team_id"] == TEAM_T and row["role_label"] == "Service"),
          "re-read: the assignment persisted with its role label")
    c, panel = await api(U_S1, "GET", f"/api/v1/staff-visibility/households/{H1}/team")
    check(bool(c == 200 and panel["permissions"]["can_write"] is True
               and panel["vocabularies"]["editable"] == ["assignments"]
               and aid in {a["id"] for a in panel["assignments"]}),
          "the household Team panel lists it and grants S1 the controls", f"{c} {dumps(panel)[:300]}")
    c, panel_np = await api(U_NP, "GET", f"/api/v1/staff-visibility/households/{H1}/team")
    check(bool(c in (403, 404) or (panel_np.get("permissions", {}).get("can_write") is False
                                   and panel_np.get("vocabularies", {}).get("editable") == [])),
          "a caller who may not assign gets no write grant (refused, or editable empty)", f"{c}")
    if aid:
        c, _ = await api(U_S2, "DELETE", f"/api/v1/staff-visibility/assignments/{aid}")
        check(c == 404, "S2 cannot remove an assignment of F1 (404)", f"{c}")
        c, _ = await api(U_S1, "DELETE", f"/api/v1/staff-visibility/assignments/{aid}")
        check(c == 200, "S1 removes it", f"{c}")
    a1 = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action IN "
                              "('create_staff_assignment', 'delete_staff_assignment')", ORG_A, fetch="fetchval")
    check(bool(a1 == a0 + 2), "add and remove are audited (two rows)", f"{a0} → {a1}")
    c, b = await api(U_OA, "GET", "/api/v1/admin/staff-visibility/unassigned")
    keys = {r["key"] for r in (b or {}).get("rows") or []}
    check(bool(c == 200 and f"household:{H3}" in keys and f"household:{H1}" not in keys),
          "Unassigned families lists F3 (with its owner), not F1", f"{c} {sorted(keys)[:6]}")
    c, b = await api(U_OA, "POST", "/api/v1/admin/staff-visibility/bulk-assign",
                     {"family_keys": [f"household:{H3}"], "assigned_to_team_id": str(TEAM_T)})
    created = [x["assignment_id"] for x in (b or {}).get("created") or []]
    c1, _ = await api(U_S1, "GET", f"/api/v1/entities/{eid('p3')}")
    check(bool(c == 201 and len(created) == 1 and c1 == 200), "bulk assign F3 to team T → S1 sees F3",
          f"{c} {dumps(b)} {c1}")
    c, b = await api(U_S2, "POST", "/api/v1/admin/staff-visibility/bulk-assign",
                     {"family_keys": [f"household:{H1}"], "assigned_to_team_id": str(TEAM_T)})
    check(c == 404, "S2 cannot bulk-assign a family S2 cannot see", f"{c}")
    for x in created:
        await api(U_OA, "DELETE", f"/api/v1/staff-visibility/assignments/{x}")
    t0 = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action IN ('add_team_member', "
                              "'remove_team_member', 'set_manager')", ORG_A, fetch="fetchval")
    c, _ = await api(U_OA, "POST", f"/api/v1/admin/staff/teams/{TEAM_T}/members", {"user_id": str(U_S2)})
    c2, _ = await api(U_OA, "DELETE", f"/api/v1/admin/staff/teams/{TEAM_T}/members/{U_S2}")
    c3, b3 = await api(U_OA, "PUT", f"/api/v1/admin/staff/users/{U_M}/manager", {"manager_id": str(U_S1)})
    check(c3 == 409, "a manager chain that would loop (M → S1 while S1 → M) is refused", f"{c3} {dumps(b3)}")
    c4, _ = await api(U_OA, "PUT", f"/api/v1/admin/staff/users/{U_S2}/manager", {"manager_id": str(U_M)})
    c5, _ = await api(U_OA, "PUT", f"/api/v1/admin/staff/users/{U_S2}/manager", {"manager_id": None})
    t1 = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action IN ('add_team_member', "
                              "'remove_team_member', 'set_manager')", ORG_A, fetch="fetchval")
    check(bool(c == 201 and c2 == 200 and c4 == 200 and c5 == 200 and t1 == t0 + 4),
          "team member add / remove and set / clear manager — each audited", f"{c} {c2} {c4} {c5}; {t0} → {t1}")
    c, _ = await api(U_NP, "PUT", f"/api/v1/admin/staff/users/{U_S2}/manager", {"manager_id": str(U_M)})
    check(c == 403, "setting a manager needs org admin or manage_members", f"{c}")


async def y_members_isolation(conn):
    section("[Y] A member still sees only their own family; org isolation both directions (rolbypassrls = false)")
    pool = await get_pool()
    async with rls(ORG_A):
        own = {str(x) for x in await visible_entity_ids(pool, str(ORG_A), str(U_MEMBER), False)}
    check(bool(own == {str(eid("p2"))}), "the member's own family (a delegate grant on F2's member) is non-empty",
          f"{sorted(own)}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/entities?limit=500")
    got = ids_in(b)
    check(bool(c == 200 and got == own), "member CRM list == exactly their own family (switch on: unchanged rule)",
          f"{c} {sorted(got)[:5]}")
    c, b = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('p1')}")
    check(c == 404, "a member opening F1 → 404", f"{c}")
    for path in (f"/api/v1/entities/{eid('p1')}", f"/api/v1/admin/households/{H1}",
                 f"/api/v1/staff-visibility/households/{H1}/team"):
        c, _ = await api(U_OB, "GET", path)
        check(c in (403, 404), f"org B's admin refused org A's {path.split('/api/v1')[1][:48]}", f"{c}")
    c, b = await api(U_OA, "GET", f"/api/v1/staff-visibility/households/{HB}/team")
    check(c == 404, "org A's admin refused org B's household Team panel", f"{c}")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a_a = await scoped(conn, ORG_A, "SELECT count(*) FROM staff_assignments WHERE org_id = $1", ORG_A, fetch="fetchval")
    a_b = await scoped(conn, ORG_B, "SELECT count(*) FROM staff_assignments WHERE org_id = $1", ORG_A, fetch="fetchval")
    t_b = await scoped(conn, ORG_B, "SELECT count(*) FROM team_members WHERE team_id = $1", TEAM_T, fetch="fetchval")
    s_b = await scoped(conn, ORG_B, "SELECT count(*) FROM org_settings WHERE org_id = $1", ORG_A, fetch="fetchval")
    check(bool(a_a > 0 and a_b == 0 and t_b == 0 and s_b == 0),
          "RLS: org B's context reads none of org A's assignments, team members or settings",
          f"A {a_a} B→A {a_b}/{t_b}/{s_b}")
    async with rls(ORG_B):
        async with pool.acquire() as c:
            g_b = await FV.load_graph(c, ORG_B)
    check(bool(str(eid("p1")) not in g_b.entity and str(eid("pb")) in g_b.entity),
          "org B's graph carries none of org A's records")


async def y_relink_kinds(conn):
    section("[Y] FIX 1 (H) — the relink never sends a no-master kind through the master; revert_relinks.py")
    import importlib.util
    import types

    from services import portfolio_manual as PM
    from services import securities_global as SG
    from services.held_away import NO_MASTER_ASSET_TYPES, NO_MASTER_KINDS

    check(bool(NO_MASTER_KINDS <= NO_MASTER_ASSET_TYPES and {"cash", "cash_holding", "digital_asset"}
               <= NO_MASTER_ASSET_TYPES), "the relink's list is BUGFIX4's NO_MASTER_KINDS plus their stored form",
          f"{sorted(NO_MASTER_ASSET_TYPES)}")
    hit = (await SG.get_by_identifiers_bulk(conn, "ticker", [RL_TICKER])).get(RL_TICKER)
    if not hit:
        check(False, f"precondition: ticker {RL_TICKER} resolves in the master", "no master hit")
        return
    gsid = str(hit.get("matched_global_security_id") or hit["id"])
    find(f"ticker {RL_TICKER} resolves in the master to", f"{gsid} {hit.get('name')!r}")
    for aid, kind, nm in ((RL_DIGITAL, "digital_asset", "Bitcoin"), (RL_CASH, "cash_holding", "Cash sweep"),
                          (RL_EQUITY, "equity", "Listed holding")):
        await scoped(conn, ORG_A, "INSERT INTO portfolio.assets (id, org_id, name, asset_class, asset_type, "
                     "ownership_basis, valuation_method) VALUES ($1, $2, $3, $4, $5, 'units', 'market_price')",
                     aid, ORG_A, NAME + nm, VALUES["asset_class"], kind, fetch="execute")
        await scoped(conn, ORG_A, "INSERT INTO portfolio.asset_identifiers (asset_id, org_id, id_type, id_value, "
                     "is_primary) VALUES ($1, $2, 'ticker', $3, true)", aid, ORG_A, RL_TICKER, fetch="execute")

    async def linked() -> dict:
        rows = await scoped(conn, ORG_A, "SELECT id, global_security_id::text AS g FROM portfolio.assets WHERE id = "
                            "ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                            [RL_DIGITAL, RL_CASH, RL_EQUITY])
        return {r["id"]: r["g"] for r in rows}

    done = await PM.relink_manual_assets(conn, ORG_A, trigger="manual")
    now = await linked()
    check(bool(now.get(RL_EQUITY) == gsid and now.get(RL_DIGITAL) is None and now.get(RL_CASH) is None
               and {d["asset_id"] for d in done} == {str(RL_EQUITY)}),
          "relink_manual_assets: a digital asset (BTC) and a cash_holding are NOT relinked; an equity with the same "
          "ticker still is", f"{now} {[d['asset_id'] for d in done]}")

    # reproduce the 07:00:56 UTC defect with the relink as it was before this fix (fixture org only)
    old_src = VS.git_show(FIX1_REF, "apps/api/services/portfolio_manual.py")
    old = types.ModuleType("portfolio_manual_before_fix1")
    exec(compile(old_src or "", "portfolio_manual@" + FIX1_REF, "exec"), old.__dict__)
    done_old = await old.relink_manual_assets(conn, ORG_A, trigger="nightly")
    now = await linked()
    check(bool(now.get(RL_DIGITAL) == gsid and now.get(RL_CASH) == gsid
               and {d["asset_id"] for d in done_old} == {str(RL_DIGITAL), str(RL_CASH)}),
          f"REPRODUCED before the fix ({FIX1_REF}): the nightly relink sent the digital asset and the cash_holding "
          "to the master security by ticker", f"{now}")

    spec = importlib.util.spec_from_file_location("revert_relinks", HERE / "revert_relinks.py")
    RR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RR)
    archived0 = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1 AND name LIKE $2 "
                             "AND system_to IS NOT NULL", ORG_A, NAME + "%", fetch="fetchval")
    plan = await RR.plan(conn, str(ORG_A))
    listed = {r["asset_id"]: r for r in plan}
    check(bool(set(listed) == {str(RL_DIGITAL), str(RL_CASH)} and all(r["to_revert"] for r in plan)
               and all(r["to_gsid"] == gsid and r["trigger"] == "nightly" for r in plan)),
          "revert_relinks dry run lists exactly the digital asset and the cash_holding (from / to security, when, "
          "trigger); the equity's relink is not listed", f"{sorted(listed)}")
    check(bool(await linked() == now), "…and the dry run changed nothing (re-read)")
    changed = await RR.apply(conn, str(ORG_A), plan)
    async with rls(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c2:                                   # an independent connection
            rows = await c2.fetch("SELECT id, global_security_id::text AS g FROM portfolio.assets WHERE id = "
                                  "ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                                  [RL_DIGITAL, RL_CASH, RL_EQUITY])
            open_relinks = await c2.fetchval("SELECT count(*) FROM portfolio.asset_relinks WHERE asset_id = "
                                             "ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                                             [RL_DIGITAL, RL_CASH])
            archived = await c2.fetchval("SELECT count(*) FROM portfolio.assets WHERE org_id = $1 AND name LIKE $2 "
                                         "AND system_to IS NOT NULL", ORG_A, NAME + "%")
    after = {r["id"]: r["g"] for r in rows}
    audits = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = $2 AND "
                          "resource_id = ANY($3::uuid[])", ORG_A, RR.AUDIT_ACTION, [RL_DIGITAL, RL_CASH],
                          fetch="fetchval")
    check(bool(changed == 2 and after.get(RL_DIGITAL) is None and after.get(RL_CASH) is None
               and after.get(RL_EQUITY) == gsid and set(after) == {RL_DIGITAL, RL_CASH, RL_EQUITY}),
          "revert_relinks real run: both unlinked, ids kept, the equity untouched (re-read, independent connection)",
          f"CHANGED {changed} {after}")
    check(bool(archived == archived0 + 2 and open_relinks == 0 and audits == 2),
          "…each as a new system version (prior archived), its relink closed, audited",
          f"archived {archived0} → {archived}, open relinks {open_relinks}, audit {audits}")
    again = await RR.apply(conn, str(ORG_A), await RR.plan(conn, str(ORG_A)))
    check(again == 0, "revert_relinks second run: CHANGED: 0", f"{again}")


async def y_statements(conn):
    section("[Y] Statement budget — the visible set once per request; one filter; the Principal one statement")
    import services.users as su

    pool = await get_pool()
    async with rls(ORG_A):
        async with pool.acquire() as c:
            n0 = STMTS["n"]
            await load_principal(c, U_S1)
            principal_n = STMTS["n"] - n0
    check(principal_n == 1, "load_principal is ONE statement (the switch rides on it)", f"{principal_n}")
    phases = {}
    switch_state = {"on": None}
    real_resolve = su.resolve_request_principal

    async def timed(request):
        n_0, g_0 = STMTS["n"], GRAPH_LOADS["n"]
        try:
            return await real_resolve(request)
        finally:
            # FIX 1 (B–D) — the visible set's ONE statement now rides on the Principal's transaction; it is counted
            # by GRAPH_LOADS and in the route budget below, and excluded HERE, so this still proves the switch adds
            # no statement to the Principal itself.
            phases.setdefault("principal", []).append((STMTS["n"] - n_0) - (GRAPH_LOADS["n"] - g_0))
            phases.setdefault(("principal_graph", switch_state["on"]), []).append(GRAPH_LOADS["n"] - g_0)

    su.resolve_request_principal = timed
    routes = [("CRM list", "/api/v1/entities?limit=50"), ("households", "/api/v1/admin/households"),
              ("positions", "/api/v1/portfolio/positions")]
    table = {}
    try:
        for on in (False, True):
            await set_switch(on)
            switch_state["on"] = on
            for label, path in routes:
                await measured(U_S1, "GET", path)          # warm
                g0 = GRAPH_LOADS["n"]
                code, _b, n = await measured(U_S1, "GET", path)
                table[(label, on)] = (code, n, GRAPH_LOADS["n"] - g0)
    finally:
        su.resolve_request_principal = real_resolve
    find("statements per request (S1): off → on", "; ".join(
        f"{lb}: {table[(lb, False)][1]} → {table[(lb, True)][1]}" for lb, _ in routes))
    for label, _ in routes:
        off, on = table[(label, False)], table[(label, True)]
        check(bool(off[0] == on[0] == 200 and on[2] == 1 and off[2] == 0 and on[1] - off[1] <= 1),
              f"{label}: enforcement adds at most ONE statement (the graph, read once)",
              f"off {off} on {on}")
    pr = phases.get("principal") or []
    check(bool(pr and len(set(pr)) == 1), "the Principal's statement count is the same with the switch off and on",
          f"{pr}")
    pg_off, pg_on = phases.get(("principal_graph", False)) or [], phases.get(("principal_graph", True)) or []
    check(bool(pg_off and pg_on and set(pg_off) == {0} and set(pg_on) == {1}),
          "the visible set is read on the Principal's own transaction (once per request, only with the switch on)",
          f"off {pg_off} on {pg_on}")


async def y_real_orgs(conn, before):
    section("[Y] Real orgs — enforcement still OFF; real assignments, teams, managers unchanged")
    on = await platform(conn, "SELECT count(*) FROM org_settings WHERE setting_key = $1 AND setting_value = 'true'::jsonb"
                              " AND NOT (org_id = ANY($2::uuid[]))", FV.SETTING_KEY, FIXTURE_ORGS, fetch="fetchval")
    check(on == 0, "no real org has staff_visibility.enforced = true", f"{on}")
    now = await real_prints(conn)
    for t in before:
        check(now[t] == before[t], f"real {t} unchanged (count + checksum)", f"{before[t][0]} → {now[t][0]}")


async def y_routes(conn):
    section("[Y] Routes — own routes via _verify_self; authz1_routes.py --check CURRENT; visibility1_routes --check")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: the route record is CURRENT",
          (out + err)[-200:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "visibility1_routes.py --check: the route table is CURRENT",
          (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(bool(code == 0 and m is not None and m.group(1) == "0"), "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code2, out2, _ = run_cmd(["node", "--test", "tests/visibility1/"], WEB_DIR, timeout=300)
    check(bool(code2 == 0 and re.search(r"^# fail 0", out2, re.M)),
          "the sprint's node tests (Team panel, preview + switch, unassigned, people — fail closed) pass",
          out2[-200:] if code2 else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_staffroles1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    t = time.monotonic()
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_staffroles1.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_staffroles1.py: 0 FAIL (its OWN final TOTAL line)",
          f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}; {time.monotonic() - t:.0f}s")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            return await res
        return res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


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
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_prints(conn)
        await run("role", y_role_is_real, conn)
        await run("catalog", y_catalog, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        section("[Y] Task 1 — findings 1a–1f and the route table")
        self_task1(check, find)
        section("[Y] The rule — pure functions")
        await run("pure", self_pure, check, find)
        await run("switch off", y_switch_off, conn)
        await run("switch gate", y_switch_gate, conn)
        await run("switch on", y_switch_on, conn)
        await run("closure", y_closure, conn)
        await run("writes", y_writes, conn)
        await run("lists", y_lists, conn)
        await run("owner rule", y_owner_rule, conn)
        await run("assignment screens", y_assign_screens, conn)
        await run("members + isolation", y_members_isolation, conn)
        await run("relink kinds", y_relink_kinds, conn)
        await run("statements", y_statements, conn)
        if real is not None:
            await run("real orgs", y_real_orgs, conn, real)
        section("[Y] Static — docs, regression set, code markers")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
        check(len(NOTE_EXTRACTIONS) == 1, "exactly one note extraction was scheduled (the admitted note), stubbed",
              f"{NOTE_EXTRACTIONS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
        try:
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
                now = await real_prints(conn)
                for t in real:
                    check(now[t] == real[t], f"real {t} unchanged after teardown", f"{real[t][0]} → {now[t][0]}")
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
