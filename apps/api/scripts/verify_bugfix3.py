"""verify_bugfix3 — HollisMuse's Olivo pass (2026-11-05): the CRITICAL member-privacy leak on the Positions page (and
every portfolio read that shared its shape), private investments without a commitment, the household Recalculate,
approved import rows as live provisional positions, editable / endable addresses, permission-true CRM controls, the
manual-asset report, the listed_option contract picker, fixed-point money and the list loading state.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch and R2 are replaced by local stubs that match the CURRENT signatures.

Everything after the bearer token is the real app against the live database, in TWO fixture orgs
(99000000-…-000000560001 / 560002), ``is_test = true`` (asserted), as are the fixture users. Org A holds two families,
F1 (the member's: a delegate grant on F1's principal) and F2 (a distinctive asset name, "PE3V Zephyr Vineyard F2",
that must never reach the member). The Olivo demo rows (tag demo-olivo) and every real row are only READ: count +
checksum before and after. Teardown at start and end via _verify_cleanup by fixture id (fixture orgs → every org_id
table: entities, households, accounts, assets, positions, valuations, commitments, transactions, addresses,
provisional imports / rows, entity_holdings, rollup requests, human tasks, audit rows); every counted table is re-read
for its exact pre-test count.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"

import asyncio
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import date
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)

SPRINT_KEY = "bugfix3"
BEFORE_REF = "9504fff"   # bugfix3-work base: "olivo demo: Muse entry plan v4 (resume pass after portentry2)"

NEW_ROUTES = {
    ("POST", "/api/v1/admin/households/{household_id}/recalculate"): "internal + any(manage_members, manage_portfolio)",
    ("POST", "/api/v1/entities/{entity_id}/addresses/{addr_id}/end"): "internal + any(manage_members, edit_crm_contact)",
    ("GET", "/api/v1/portfolio/option-contracts"): "authz view_held_away",
}
#: existing routes whose gate this sprint DELIBERATELY changed (staff-only now: org-wide lists, no member filter)
CHANGED_TO_INTERNAL = (
    ("GET", "/api/v1/portfolio/tax-chase"),
    ("GET", "/api/v1/portfolio/position-account-exceptions"),
    ("GET", "/api/v1/portfolio/precedence/households/{household_id}"),
    ("GET", "/api/v1/udf/records/{target_type}"),
    ("GET", "/api/v1/udf/records/{target_type}/export"),
    ("GET", "/api/v1/udf/values/{target_type}/{target_id}"),
)
MUSE_IDS = ("MUSE-OLIVO-CRIT", "MUSE-OLIVO-H1", "MUSE-OLIVO-H2", "MUSE-OLIVO-H3", "MUSE-OLIVO-M1", "MUSE-OLIVO-M2",
            "MUSE-OLIVO-M3", "MUSE-OLIVO-M4", "MUSE-OLIVO-L1", "MUSE-OLIVO-L2", "MUSE-OLIVO-L3")

# ── Task 1 — needles written by hand from the discovery; each must be present at the sprint's end. ──────────────
TASK1 = [
    ("apps/api/services/portfolio_scope.py", "async def entity_scope(request: Request, pool) -> list[str] | None:",
     "1a: every member-reachable portfolio read takes the ONE shared visible-entity rule (authz.member_entity_scope)"),
    ("apps/api/services/portfolio_positions.py", 'add("p.owner_entity_id = ANY(${n}::uuid[])"',
     "1a: /portfolio/positions filtered in SQL (it was org-scoped only after a view_portfolio gate)"),
    ("apps/api/services/portfolio_securities.py", "HELD_BY_SCOPE = (",
     "1a: the Securities grid / detail / versions — instruments the family holds"),
    ("apps/api/services/portfolio_transactions.py", "-- BUGFIX3: another family's entry = the SAME None",
     "1a: the Transactions grid / detail / picker"),
    ("apps/api/routers/modeling_ta.py", "# BUGFIX3 — a member projects only their own family's commitment",
     "1a: the TA projection and obligation ledger by commitment id"),
    ("apps/api/services/household_admin.py", "async def provisional_holdings(conn, org_id, entity_ids)",
     "1c: approved import rows are live positions; the household view listed only direct / private kinds"),
    ("apps/api/services/money_text.py", 'return format(value, "f")',
     "1d: str(Decimal) → fixed-point (asyncpg / arithmetic exponent forms rendered as \"$9.0E\")"),
    ("apps/web/lib/listState.mjs", "export function listView(data",
     "1e: lists rendered their EMPTY sentence while their data was still null (loading)"),
]
TASK1_ROUTE_TABLE = [
    # (route, gate, member filter before, member today, class)
    ("GET /portfolio/positions", "view_portfolio", "none — org-wide", "own family", "member-scoped (FIXED)"),
    ("GET /portfolio/positions/{id}", "view_portfolio", "none", "own family, else 404 = missing", "member-scoped (FIXED)"),
    ("GET /portfolio/assets", "view_portfolio", "none", "assets the family holds", "member-scoped (FIXED)"),
    ("GET /portfolio/securities (+ /{id}, /versions)", "view_portfolio", "none", "held by the family; 404",
     "member-scoped (FIXED)"),
    ("GET /portfolio/transactions (+ /{id}, transaction-positions)", "view_portfolio", "none", "own family; 404",
     "member-scoped (FIXED)"),
    ("GET /modeling/ta/projection|obligations/{commitment_id}", "view_portfolio", "none", "own family; 404",
     "member-scoped (FIXED)"),
    ("GET /portfolio/commitments", "view_portfolio", "visible_entity_ids (BUGFIX1)", "own family", "already-correct"),
    ("GET /portfolio/tax-chase", "view_portfolio", "none — org-wide list", "403", "staff-only (FIXED)"),
    ("GET /portfolio/position-account-exceptions", "view_portfolio", "none", "403", "staff-only (FIXED)"),
    ("GET /portfolio/precedence/households/{id}", "view_portfolio", "none", "403", "staff-only (FIXED)"),
    ("GET /udf/records/{t} (+ /export), /udf/values/{t}/{id}", "view_portfolio", "none — every org row", "403",
     "staff-only (FIXED)"),
    ("GET /portfolio/global-securities (+ /{id})", "view_portfolio", "n/a — platform master, no org rows",
     "allowed", "public-to-org (reference data)"),
    ("GET /entities/{id}/direct-assets | private-investments", "member scope", "own family", "own family; 404",
     "already-correct"),
    ("GET /entities/{id}/net-worth | liabilities", "entity access", "own family", "own family; 404", "already-correct"),
    ("GET /portfolio/targets | allocations", "entity access", "own family", "own family", "already-correct"),
    ("GET /portfolio/my-investments | summary", "account", "own rows", "own rows", "already-correct"),
    ("GET /households/{id}/assets, /admin/households*", "internal (+manage_members)", "n/a", "403", "staff-only"),
    ("GET /held-away/*, /portfolio/manual-assets, option-contracts", "view_held_away", "n/a", "403", "staff-only"),
    ("GET /xray/*, /authority/*, /lot-method/*, /replication/*", "view_replication | view_authority | view_proposals",
     "n/a", "403", "staff-only"),
    ("GET /portfolio/precedence, /securities/search, profitability, fee screens", "internal", "n/a", "403",
     "already-correct"),
]
TASK1_FINDS = [
    "1b — bugfix2's 73-route classification covered routes WITHOUT any gate. /portfolio/positions (and the other "
    "view_portfolio reads above) already carried a permission gate, so they were never in its list; the member role "
    "HOLDS view_portfolio, and verify_bugfix2 seeded NO position for a second family — there was nothing a member "
    "could have seen that it would have flagged.",
    "1c — the 'approved' integrated import was never approved: portfolio.provisional_imports holds ONE row, status "
    "'draft' (maker-checker waits for a second Super Admin), and portfolio.provisional_positions is EMPTY. All 23 live "
    "positions are manual direct / private entries. Code-side: approval writes current positions (authority 'stated', "
    "source 'manual') that the grid did list, unlabelled; the household view listed only direct-asset and commitment "
    "kinds, so imported rows never appeared there. Now: is_provisional + the label in the grid and the household.",
    "1d — str(Decimal) at the JSON boundary in portfolio_positions / securities / transactions._s, authority / "
    "lot_method / xray._s, direct_holdings (valuations, cost basis, owner value, private totals), households._money "
    "(float!). asyncpg decodes a numeric whose trailing base-10000 groups are zero as an exponent (BUGFIX1 hit "
    "1.00E+6), so a cost stored as 900000 serialised as '9.0E+5' and lib/portentry money() split it on '.' → '$9.0E'.",
    "1e — HoldingsTab rendered 'No direct assets.' / 'No private investments.' while its envelope was still null; "
    "AddressesTab kept its first-render copy of the list (useState(initial)) and never followed a re-read.",
    "Northline — the three NLF lines (A1 / A6 / A8) are in DRAFT held-away imports (6 of the 58 drafts mention it); "
    "no asset exists yet for any of them. On approval, manual_asset_for reuses one org asset by identifier, else by "
    "kind + name + terms — so identical lines share one asset; differing terms make separate assets. The manual-asset "
    "report now shows position / owner counts and flags a shared name. A maker-checker MERGE is NOT built (it must "
    "re-point positions, valuations, identifiers, transactions, held-away records and relinks) — named as a gap.",
]


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _key(rec: dict) -> tuple:
    return ((rec.get("methods") or [""])[0], rec["path"])


def self_task1(check, find):
    for rel, needle, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or ""
        check(needle in txt, f"Task 1 — {meaning}", f"{rel}: needle missing")
    print("[INFO] Task 1a — portfolio read routes (route | gate | member filter before | member today | class)")
    for row in TASK1_ROUTE_TABLE:
        print("[INFO]   " + " | ".join(row))
    check(len(TASK1_ROUTE_TABLE) >= 20 and all(len(r) == 5 for r in TASK1_ROUTE_TABLE),
          "Task 1a — the route table is recorded (each row: gate, filter before, member today, class)")
    for f in TASK1_FINDS:
        find(f"Task 1 — {f}")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if want.split(" ")[0] not in str((new.get(k) or {}).get("after") or "")]
    check(not wrong, "every new route carries its stated gate", f"{wrong}")
    changed = {_key(own.end[k]): own.end[k].get("after") for k in own.changed}
    not_internal = [k for k in CHANGED_TO_INTERNAL if "internal" not in str(changed.get(k) or "")]
    check(not not_internal, "the six org-wide reads are now internal (staff-only)", f"{not_internal}; {changed}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_static(check, find):
    check("verify_bugfix3.py" in _read("scripts/regression_set.txt"), "Task 8: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. BUGFIX3 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    missing = [i for i in MUSE_IDS if i not in top]
    check(bool(top and not missing and "| Route |" in top), "Task 8: PROJECT_STATUS entry — the 1a table, the leak "
          "and every Muse finding with its ID", f"missing {missing}")
    pos_page = _read("apps/web/app/portfolio/positions/page.js")
    check(bool(pos_page and "Every holding across the firm" not in pos_page),
          "the Positions page no longer hard-codes 'Every holding across the firm' (the API's scope decides)")


#: calls that would mean the route applied a member / visibility filter (matched as CALLS in the AST, never as words —
#: the pre-fix docstring's "Org-scoped from JWT claims" made a substring check fail on a comment)
MEMBER_FILTER_CALLS = frozenset({"visible_entity_ids", "entity_scope", "member_entity_scope",
                                 "get_staff_visible_entity_ids", "resolve_entity_set", "filter_restricted",
                                 "require_entity_access", "_entity_scope"})


def route_calls(source: str, method: str, path: str) -> tuple[set[str], dict[str, set[str]]] | None:
    """(every called name, {called name: keyword names}) inside the function decorated @router.<method>(path) in
    ``source``; None when the route is absent. AST, so docstrings and comments cannot match."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for d in node.decorator_list:
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == method
                    and d.args and isinstance(d.args[0], ast.Constant) and d.args[0].value == path):
                names: set[str] = set()
                kws: dict[str, set[str]] = {}
                for c in ast.walk(node):
                    if isinstance(c, ast.Call):
                        f = c.func
                        nm = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                        if nm:
                            names.add(nm)
                            kws.setdefault(nm, set()).update(k.arg for k in c.keywords if k.arg)
                return names, kws
    return None

#: FIX 1 (verify) — the member's family in INDEPENDENT SQL: ONE anchor and ONE recursive term (see visible_expected).
VISIBLE_SQL = """
        WITH RECURSIVE fam(id) AS (
            SELECT principal_entity_id FROM delegate_grants WHERE delegate_user_id = $1 AND org_id = $2
          UNION
            SELECT e.child FROM fam JOIN (
                SELECT r.from_entity_id AS parent, r.to_entity_id AS child FROM entity_relationships r
                 WHERE r.org_id = $2 AND r.relationship_type = 'ownership' AND r.valid_to IS NULL
                   AND r.system_to IS NULL
                UNION
                SELECT o.parent_id AS parent, o.child_id AS child FROM entity_ownership o
                 WHERE o.org_id = $2 AND o.valid_to IS NULL AND o.system_to IS NULL) e ON e.parent = fam.id)
        SELECT id::text FROM fam"""


# ── BUGFIX3 FIX 1 — static proof (no DB): the verify's own reproduction is sound, and the taxonomy fix is wired. ──
FIX1_NEEDLES = [
    ("apps/api/services/portfolio_kind_taxonomy.py", "async def key_for_kind(c, org_id: str, asset_type: str | None)",
     "4a: ONE resolver: asset kind → the org's taxonomy key (org_settings portfolio.kind_taxonomy, validated against "
     "config asset_taxonomy)"),
    ("apps/api/services/portfolio_assets.py", "default_taxonomy_key = await key_for_kind(c, org_id, asset_type)",
     "4a: create_asset (direct asset, private investment, cash, file import) keys an unkeyed asset by kind"),
    ("apps/api/services/portfolio_manual.py", 'tax_key = ref.get("taxonomy_key") or await key_for_kind(c, org, kind)',
     "4a: manual_asset_for (manual assets, integrated import) keys by kind"),
    ("apps/api/services/held_away.py", "await key_for_kind(conn, str(org_id), kind)",
     "4a: the held-away global-security asset keys by kind"),
    ("apps/api/services/portfolio_rollup.py", "reason=NO_TAXONOMY_REASON,",
     "4b: the rollup's skip branch (no taxonomy key) names one reason the report counts"),
    ("apps/api/services/portfolio_refresh.py", "def skipped_of(result) -> dict:",
     "4b: skipped positions reported (count + ids), never a silent $0"),
    ("apps/api/services/household_admin.py", '"skipped": PR.skipped_of(ran)}',
     "4b: Recalculate returns what it skipped"),
    ("apps/api/services/household_admin.py", '"skipped": (last_rollup or {}).get("skipped") or PR.skipped_of(None),',
     "4b: the household detail carries the last rollup's skipped report"),
    ("apps/web/components/households/HouseholdManager.jsx", "skippedSentence(detail.skipped)",
     "4b: the household card says how many holdings are not counted"),
    ("apps/api/scripts/backfill_taxonomy_keys.py", "changes={\"default_taxonomy_key\": p[\"key\"]}",
     "4c: the backfill sets ONLY default_taxonomy_key, through the system-axis update_asset"),
]


def self_fix1(check, find):
    print("[INFO] FIX 1 — static: verify reproduction soundness + taxonomy wiring")
    for rel, needle, meaning in FIX1_NEEDLES:
        check(needle in _read(rel), f"FIX 1 — {meaning}", f"{rel}: needle missing")
    before = VS.git_show(BEFORE_REF, "apps/api/routers/portfolio_positions.py") or ""
    got = route_calls(before, "get", "/portfolio/positions") if before else None
    names = got[0] if got else set()
    check(bool(got and "list_positions" in names and not (names & MEMBER_FILTER_CALLS)
               and "entity_scope" not in got[1].get("list_positions", set())),
          f"FIX 1 (verify 1) — BEFORE ({BEFORE_REF}) the route's CALLS: list_positions, no member filter (AST; the "
          "docstring's 'Org-scoped' cannot match)", f"{sorted(names & MEMBER_FILTER_CALLS)}")
    neg = route_calls('@router.get("/x")\nasync def f():\n    """Org-scoped, visible_entity_ids()."""\n'
                      '    s = await _entity_scope(r, p)\n    return await list_positions(c, entity_scope=s)\n',
                      "get", "/x")
    check(bool(neg and "_entity_scope" in neg[0] and "visible_entity_ids" not in neg[0]
               and "entity_scope" in neg[1]["list_positions"]),
          "FIX 1 (verify 1) — the matcher sees a real filter CALL and ignores the same word in a docstring")
    rec = VISIBLE_SQL.split("UNION", 1)[1] if "UNION" in VISIBLE_SQL else ""
    check(bool(VISIBLE_SQL.count("JOIN (") == 1 and rec.count("FROM fam JOIN (") == 1 and "JOIN fam" not in rec
               and "entity_relationships" in rec and "entity_ownership" in rec
               and "relationship_type = 'ownership'" in rec),
          "FIX 1 (verify 3) — visible_expected: one anchor, ONE recursive term over a sub-select UNIONing both "
          "ownership edge sets", VISIBLE_SQL[:200])
    from services.direct_holdings import DIRECT_KINDS, PRIVATE_KINDS
    from services.org_settings import DEFAULT_SETTINGS
    from services.portfolio_manual import KIND_SHAPE

    m = DEFAULT_SETTINGS.get("portfolio.kind_taxonomy") or {}
    want = ({k for k in DIRECT_KINDS if k != "other"} | {f"private_{k}" for k in PRIVATE_KINDS} | set(KIND_SHAPE)
            | {"cash"})
    check(not (want - set(m)) and "other" not in m,
          "FIX 1 (4a) — every creation kind maps to a key (real estate, collectible, operating business, insurance "
          "cash value, private fund … digital asset, cash); 'other' deliberately unmapped (reported, not guessed)",
          f"missing {sorted(want - set(m))}")
    check(all(re.fullmatch(r"taxonomy_(sc_\d+|mc_\d+_\d+|sub_\d+_\d+_\d+)", v) for v in m.values()),
          "FIX 1 (4a) — the map holds Rule-4 taxonomy KEYS only (labels stay in config)", f"{m}")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)
    self_fix1(check, find)


def run_cmd(args, cwd, timeout=600, env=None, stdin_text=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), input=stdin_text,
                           stdin=None if stdin_text is not None else subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


if __name__ == "__main__" and VS.SELF_CHECKS_FLAG in sys.argv:
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "bugfix3verify"
NAME = "PE3V "            # reserved fixture name prefix (asserted to match no real row)
F2_MARK = "Zephyr Vineyard F2"   # the distinctive F2 asset name: never in a member's response


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000056{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)

ORGADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                            "manage_portfolio", "view_dashboard", "view_held_away", "manage_held_away",
                            "approve_held_away"})
SUPPORT_PERMS = frozenset({"review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
                           "view_deals", "view_members", "view_portfolio", "view_workflow_runs", "edit_crm_contact"})
U_OA, U_SUPPORT, U_MEMBER, U_SUPER, U_SUPER2, U_OB_ADMIN = _id(12), _id(13), _id(14), _id(15), _id(16), _id(17)
USERS = {
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPPORT: (ORG_A, "member", "support", "support_staff", SUPPORT_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_SUPER2: (ORG_A, "super_admin", "super2", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_OB_ADMIN: (ORG_B, "org_admin", "obadmin", "org_admin", ORGADMIN_PERMS),
}
E = {
    "f1p": (_id(30), ORG_A, "individual", "F1 Principal"),
    "f1llc": (_id(31), ORG_A, "llc", "F1 Holdings LLC"),
    "f2p": (_id(32), ORG_A, "individual", "F2 Principal"),
    "f2llc": (_id(33), ORG_A, "llc", "F2 Holdings LLC"),
    "orgb": (_id(34), ORG_B, "individual", "Org B Person"),
}
HH1, HH_B = _id(60), _id(61)
TAXONOMY_SOURCE_ORG = UUID("00000000-0000-0000-0000-000000000001")   # read-only: its asset_taxonomy rows are copied
ACC1, ACC2 = _id(70), _id(71)

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.entity_addresses", "public.households", "public.household_memberships", "public.accounts",
           "public.entity_holdings", "public.delegate_grants", "public.role_permissions",
           "portfolio.assets", "portfolio.positions", "portfolio.valuations", "portfolio.commitments",
           "portfolio.transactions", "portfolio.provisional_imports", "portfolio.provisional_positions",
           "portfolio.rollup_requests")
#: NOT counted, by name: public.audit_log — real activity (sign-ins, HollisMuse mid-pass) writes it during a run.
REAL_FINGERPRINTED = ("public.entities", "public.entity_addresses", "public.households",
                      "public.household_memberships", "public.accounts", "portfolio.assets", "portfolio.positions",
                      "portfolio.valuations", "portfolio.commitments", "portfolio.transactions")

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


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def eid(k: str) -> UUID:
    return E[k][0]


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:500]


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
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' "
                                "ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str, *, where: str = "true", args=()) -> tuple[int, str]:
    """Count + md5 of every row keyed by the table's real primary key (read from the catalog). None of these tables is
    written by real sign-in activity, so no column is excluded."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


def _real_where(table: str) -> str:
    if table == "public.household_memberships":
        return "household_id NOT IN (SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))"
    return "NOT (org_id = ANY($1::uuid[]))"


async def real_prints(conn) -> dict:
    return {t: await fingerprint(conn, t, where=_real_where(t), args=(FIXTURE_ORGS,)) for t in REAL_FINGERPRINTED}


DEMO = "SELECT id FROM public.entities WHERE 'demo-olivo' = ANY(tags)"
DEMO_SQL = {
    "entities": (f"SELECT * FROM public.entities WHERE 'demo-olivo' = ANY(tags)", "id, system_from"),
    "positions": (f"SELECT * FROM portfolio.positions WHERE owner_entity_id IN ({DEMO})", "id"),
    "assets": (f"SELECT * FROM portfolio.assets WHERE id IN (SELECT asset_id FROM portfolio.positions WHERE "
               f"owner_entity_id IN ({DEMO}))", "id, system_from"),
    "valuations": (f"SELECT * FROM portfolio.valuations WHERE asset_id IN (SELECT asset_id FROM portfolio.positions "
                   f"WHERE owner_entity_id IN ({DEMO}))", "id"),
    "commitments": (f"SELECT * FROM portfolio.commitments WHERE position_id IN (SELECT id FROM portfolio.positions "
                    f"WHERE owner_entity_id IN ({DEMO}))", "id"),
    "addresses": (f"SELECT * FROM public.entity_addresses WHERE entity_id IN ({DEMO})", "id"),
    "provisional_imports": ("SELECT * FROM portfolio.provisional_imports WHERE NOT (org_id = ANY("
                            "'{99000000-0000-0000-0000-000000560001,99000000-0000-0000-0000-000000560002}'::uuid[]))",
                            "id"),
}


async def demo_prints(conn) -> dict:
    out = {}
    for k, (sql, order) in DEMO_SQL.items():
        r = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY "
                                 f"{', '.join('t.' + c.strip() for c in order.split(','))}), '')) AS h FROM ({sql}) t",
                           fetch="fetchrow")
        out[k] = (int(r["n"]), r["h"])
    return out


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only — the fixture orgs (every org_id table), the fixture users by id / sub, the fixture
    households' memberships, the fixture roles' grants, the fixture users' audit rows. The documented privileged path
    (operator DSN + the teardown GUC) for the append-only ledger / import tables."""
    users = list(USERS)
    op = await operator_conn()
    try:
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH1, HH_B]]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [FIXTURE_ORGS]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown",), label="verify_bugfix3")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [[HH1, HH_B]])])
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
ACCOUNT_VALUES: dict[str, str] = {}


def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


async def y_catalog(conn):
    section("[Y] Constraints read from the catalog before seeding; reserved fixture prefixes match no real row")
    cons = {}
    for t in ("public.accounts", "public.entity_addresses", "public.households", "portfolio.positions",
              "portfolio.assets", "portfolio.commitments", "portfolio.provisional_imports",
              "portfolio.provisional_positions", "public.entity_relationships"):
        for r in await platform(conn, "SELECT conname, contype, pg_get_constraintdef(oid) AS d FROM pg_constraint "
                                      "WHERE conrelid = $1::regclass AND contype IN ('p', 'c', 'u', 'f')", t):
            cons[(t, r["conname"])] = r["d"]
    print(f"[INFO] {len(cons)} constraints read (UNIQUE / CHECK / FK / PK)")
    pos_auth = [d for (t, _n), d in cons.items() if t == "portfolio.positions" and "authority" in d]
    check(bool(any("stated" in d for d in pos_auth) or not pos_auth),
          "positions: authority 'stated' (the import's mark) is catalog-legal", f"{pos_auth}")
    for col in ("registration_type", "tax_status", "custodian_code"):
        defs = [d for (t, _n), d in cons.items() if t == "public.accounts" and col in d and d.startswith("CHECK")]
        vals = _allowed(defs[0]) if defs else []
        ACCOUNT_VALUES[col] = vals[0] if vals else {"registration_type": "individual", "tax_status": "taxable",
                                                    "custodian_code": "ALTRUIST"}[col]
    find("accounts seeded with catalog-legal values", f"{ACCOUNT_VALUES}")
    pol = await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                               "'entity_addresses'", fetch="fetch")
    check(bool({r["cmd"] for r in pol} & {"ALL", "UPDATE"}), "entity_addresses has an UPDATE-capable policy (edit / "
          "end would otherwise match zero rows)", f"{[r['cmd'] for r in pol]}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM portfolio.assets WHERE name LIKE $1 AND NOT "
                                "(org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND "
                                "NOT (id = ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 "
                                "AND NOT (id = ANY($4::uuid[])))",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes match no real row", f"{real}")
    zz = await platform(conn, "SELECT count(*) FROM portfolio.assets WHERE name ILIKE $1", f"%{F2_MARK}%",
                        fetch="fetchval")
    check(zz == 0, "the distinctive F2 asset name exists nowhere before the seed", f"{zz}")


async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"BUGFIX3 Verify Org {nm.upper()}", f"{TAG}-{nm}")
            # FIX 1 — a real org has its asset taxonomy in config (the kind → key resolver validates against it);
            # the fixture orgs get a COPY of the default org's active rows (read only from it), removed by org.
            await conn.execute(
                "INSERT INTO config (org_id, config_key, config_value, value_type, category, display_order, "
                "is_active) SELECT $1, config_key, config_value, value_type, category, display_order, true FROM config "
                "WHERE org_id = $2 AND category = 'asset_taxonomy' AND is_active", org, TAXONOMY_SOURCE_ORG)
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        roles = {}
        for uid, (org, _a, _n, rname, perms) in USERS.items():
            if (org, rname) not in roles:
                rid = await rbac.ensure_role(conn, org, rname, f"BUGFIX3 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"BUGFIX3 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for k, (i, org, etype, name) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) "
                               "VALUES ($1, $2, $3, 'active', $4::entity_type)", i, org, NAME + name, etype)
        rel = ("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
               "ownership_pct) VALUES ($1, $2, $3, 'ownership', 100)")
        await conn.execute(rel, ORG_A, eid("f1p"), eid("f1llc"))
        await conn.execute(rel, ORG_A, eid("f2p"), eid("f2llc"))
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", HH1, ORG_A, NAME + "F1")
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", HH_B, ORG_B, NAME + "B")
        for k in ("f1p", "f1llc"):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", HH1,
                               eid(k))
        await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", HH_B,
                           eid("orgb"))
        # the member's family = F1 (a delegate grant on F1's principal; the subtree reaches F1's LLC)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, eid("f1p"), U_MEMBER)
        for aid, mask, owner in ((ACC1, "****5601", eid("f1p")), (ACC2, "****5602", eid("f1llc"))):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, household_id, account_number_masked, account_number_hash, "
                "custodian_code, registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, $3, "
                "$4, $5, $6, $7, $8, $9, false)", aid, ORG_A, HH1, mask,
                hashlib.sha256(f"{TAG}{mask}".encode()).hexdigest(), ACCOUNT_VALUES["custodian_code"],
                ACCOUNT_VALUES["registration_type"], ACCOUNT_VALUES["tax_status"], owner)


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == 2 and u == len(USERS)), "both fixture orgs and every fixture user are is_test = true", f"{o}/{u}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []
S: dict = {}   # what the seed through the API produced (ids)


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(uid, method, path, body=None, timeout=120.0, files=None, data=None):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
        kw["data"] = data or {}
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
    import inspect

    import main
    from services import storage
    from services import users as users_mod

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


def node_eval(js: str) -> tuple[int, str]:
    """Run an ES-module snippet in apps/web (the REAL lib files, never a copy); returns (code, stdout)."""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", dir=str(WEB_DIR / "tests"), delete=False,
                                     encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        code, out, err = run_cmd(["node", path], WEB_DIR, timeout=120)
        return code, (out + ("\n" + err if code else "")).strip()
    finally:
        os.unlink(path)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


AS_OF = date.today()


async def seed_holdings(conn):
    """Holdings through the REAL API (org admin): a direct asset per family (F1's cost $900,000; F2's the
    distinctive name), a fund with a commitment + called capital (a ledger entry) per family."""
    section("[Y] Seed holdings through the real write paths (org admin)")
    for fam, owner, name, cost in (("f1", "f1p", "Lake House F1", "900000"), ("f2", "f2p", F2_MARK, "750000")):
        c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid(owner)}/direct-assets", {
            "kind": "real_estate", "name": NAME + name, "cost_basis": cost,
            "valuation": {"value": "1200000", "date": AS_OF.isoformat(), "method": "appraisal"}})
        check(bool(c == 201 and b.get("asset_id")), f"{fam}: direct asset created", f"{c} {dumps(b)}")
        S[f"{fam}_asset"] = b.get("asset_id")
        S[f"{fam}_position"] = (b.get("position_ids") or [None])[0]
    for fam, owner in (("f1", "f1llc"), ("f2", "f2llc")):
        c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid(owner)}/private-investments", {
            "kind": "fund", "name": NAME + f"Fund {fam.upper()}", "commitment": {"amount": "1000000",
                                                                                "date": "2024-01-02"},
            "called_to_date": "250000", "nav": {"value": "300000", "date": AS_OF.isoformat()}})
        unf = b.get("unfunded") if c == 201 else None
        check(bool(c == 201 and b.get("commitment_id") and unf is not None and "E" not in str(unf)
                   and Decimal(str(unf)) == Decimal("750000")),
              f"{fam}: a fund with its commitment — unfunded computed (750,000)", f"{c} {dumps(b)}")
        S[f"{fam}_commitment"] = b.get("commitment_id")
        S[f"{fam}_fund_position"] = b.get("position_id")
    rows = await scoped(conn, ORG_A, "SELECT t.id, p.owner_entity_id FROM portfolio.transactions t JOIN "
                                     "portfolio.positions p ON p.id = t.position_id WHERE t.org_id = $1", ORG_A)
    S["f2_txn"] = next((str(r["id"]) for r in rows if r["owner_entity_id"] == eid("f2llc")), None)
    S["f1_txn"] = next((str(r["id"]) for r in rows if r["owner_entity_id"] == eid("f1llc")), None)
    check(bool(S["f1_txn"] and S["f2_txn"]), "each family's fund has an opening ledger entry", f"{len(rows)} rows")
    c, b = await api(U_OB_ADMIN, "POST", f"/api/v1/entities/{eid('orgb')}/direct-assets", {
        "kind": "collectible", "name": NAME + "Org B Painting", "valuation": {"value": "5000", "date":
                                                                             AS_OF.isoformat(), "method": "appraisal"}})
    check(c == 201, "org B: a direct asset of its own", f"{c} {dumps(b)}")


async def visible_expected(conn) -> set[str]:
    """INDEPENDENT SQL: the member's family = the delegate grant's principal + every entity it reaches by ownership
    edges in either ownership table (the fixture has no restricted-access rows)."""
    # FIX 1 (verify) — ONE anchor and ONE recursive term. Two UNIONed recursive terms made Postgres read
    # (anchor UNION recursive-1) as the non-recursive term → InvalidRecursionError, and the whole leak section never
    # ran. Both ownership edge sets are UNIONed in a sub-select the single recursive term joins.
    rows = await scoped(conn, ORG_A, VISIBLE_SQL, U_MEMBER, ORG_A)
    return {r["id"] for r in rows}



async def y_leak(conn):
    section("[Y] THE LEAK (CRITICAL) — reproduced on the pre-fix path, then closed on every member-reachable route")
    from services.database import get_pool
    from services.portfolio_positions import list_positions

    before = VS.git_show(BEFORE_REF, "apps/api/routers/portfolio_positions.py") or ""
    got = route_calls(before, "get", "/portfolio/positions") if before else None
    names, kws = got if got else (set(), {})
    check(bool(got and "list_positions" in names and not (names & MEMBER_FILTER_CALLS)
               and "entity_scope" not in kws.get("list_positions", set())),
          f"BEFORE ({BEFORE_REF}): GET /portfolio/positions called list_positions with NO member / visibility filter "
          "(calls matched in the AST, not words)",
          f"calls {sorted(names)}; filter calls {sorted(names & MEMBER_FILTER_CALLS)}")
    now = route_calls((API_DIR / "routers" / "portfolio_positions.py").read_text(encoding="utf-8"), "get",
                      "/portfolio/positions")
    check(bool(now and "entity_scope" in now[1].get("list_positions", set()) and (now[0] & MEMBER_FILTER_CALLS)),
          "AFTER (this tree): the same route passes the member's entity_scope into list_positions",
          f"{sorted(now[0] & MEMBER_FILTER_CALLS) if now else None}")
    # FIX 1 (verify) — the pre-fix call path WITH the org context the route had: rls_context_middleware set the
    # caller's org (a member: is_super_admin false) on the task, and the RLS pool's acquire() SET LOCALs it. A bare
    # pool acquire with no context returned [] (default-deny), which reproduced nothing.
    from services.database import reset_rls_context, set_rls_context

    pool = await get_pool()
    tokens = set_rls_context(str(ORG_A), False)
    try:
        async with pool.acquire() as c:
            ctx = await c.fetchval("SELECT current_setting('app.current_org_id', true)")
            old = await list_positions(c, org_id=str(ORG_A), resolve_values=False, limit=1000)
    finally:
        reset_rls_context(tokens)
    check(ctx == str(ORG_A), "the reproduction runs under the member request's org context (app.current_org_id)",
          f"{ctx}")
    old_names = {p["asset_name"] for p in old["positions"]}
    old_owners = {p["owner_entity_id"] for p in old["positions"]}
    check(bool(any(F2_MARK in (n or "") for n in old_names) and str(eid("f2p")) in old_owners
               and str(eid("f1p")) in old_owners),
          "REPRODUCED: the pre-fix call path (list_positions(org_id) only — what the member's request ran) returns "
          "F1's AND F2's positions, the distinctive F2 asset among them", f"{sorted(old_names)}")
    S["org_owners"] = old_owners

    want = await visible_expected(conn)
    check(bool(want == {str(eid("f1p")), str(eid("f1llc"))}), "independent SQL: the member's family is F1 only",
          f"{want}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/portfolio/positions?resolve_values=false")
    rows = b.get("positions", []) if c == 200 else []
    owners = {r["owner_entity_id"] for r in rows}
    sql_owners = {r["o"] for r in await scoped(conn, ORG_A, "SELECT DISTINCT owner_entity_id::text AS o FROM "
                                                            "portfolio.positions WHERE org_id = $1 AND owner_entity_id "
                                                            "= ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS "
                                                            "NULL", ORG_A, [UUID(x) for x in want])}
    check(bool(c == 200 and rows and owners == sql_owners and owners < old_owners),
          "AFTER: the member's positions are non-empty, exactly the F1 owners the SQL predicate gives, a strict subset "
          "of the org's", f"{c} owners {owners} sql {sql_owners} org {old_owners}")
    check(bool(c == 200 and F2_MARK not in body_text(b) and int(b.get("total") or 0) == len(rows)),
          "no F2 value anywhere in the member's response (rows, total, taxonomy, vocabularies, hint)",
          f"total {b.get('total')}; F2 present: {F2_MARK in body_text(b)}")
    sc = (b or {}).get("scope") or {}
    check(bool(sc.get("kind") == "own_family" and "across the firm" not in str(sc.get("description"))),
          "the page's sentence for a member reads as their own holdings", f"{sc}")
    c2, b2 = await api(U_OA, "GET", "/api/v1/portfolio/positions?resolve_values=false")
    oa_owners = {r["owner_entity_id"] for r in b2.get("positions", [])} if c2 == 200 else set()
    check(bool(c2 == 200 and str(eid("f2p")) in oa_owners and (b2.get("scope") or {}).get("kind") == "firm"),
          "the identical request is unscoped for the org admin (both families; 'across the firm')", f"{c2}")
    # filters cannot reach around it
    c3, b3 = await api(U_MEMBER, "GET", f"/api/v1/portfolio/positions?owner_entity_id={eid('f2p')}")
    check(bool(c3 == 200 and not b3.get("positions") and b3.get("total") == 0),
          "a member filtering on F2's owner gets nothing (the scope is applied first, in SQL)", f"{c3} {dumps(b3)}")
    c4, b4 = await api(U_MEMBER, "GET", f"/api/v1/portfolio/positions?search={F2_MARK.split()[0]}")
    check(bool(c4 == 200 and not b4.get("positions")), "search cannot reach F2's asset", f"{c4} {dumps(b4)}")

    # detail: another family's = identical to a missing id
    cm, bm = await api(U_MEMBER, "GET", f"/api/v1/portfolio/positions/{MISSING}")
    cf, bf = await api(U_MEMBER, "GET", f"/api/v1/portfolio/positions/{S['f2_position']}")
    co, bo = await api(U_MEMBER, "GET", f"/api/v1/portfolio/positions/{S['f1_position']}")
    check(bool(cf == 404 and (cf, bf) == (cm, bm)), "F2's position detail → 404 IDENTICAL to a missing id",
          f"{cf} {bf} vs {cm} {bm}")
    check(co == 200, "F1's own position detail → 200", f"{co}")
    ca, _ = await api(U_OA, "GET", f"/api/v1/portfolio/positions/{S['f2_position']}")
    check(ca == 200, "the org admin opens F2's position", f"{ca}")

    # asset picker / securities grid / detail / versions
    c, b = await api(U_MEMBER, "GET", "/api/v1/portfolio/assets?limit=200")
    names = [a["name"] for a in b.get("assets", [])] if c == 200 else []
    check(bool(c == 200 and any("Lake House F1" in n for n in names) and not any(F2_MARK in n for n in names)),
          "asset picker: F1's asset in, F2's out", f"{c} {names}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/portfolio/securities?resolve_values=false")
    names = [a.get("name") for a in b.get("assets", [])] if c == 200 else []
    check(bool(c == 200 and names and F2_MARK not in body_text(b)), "Securities grid: no F2 instrument",
          f"{c} {names}")
    for suffix in ("", "/versions"):
        cm, bm = await api(U_MEMBER, "GET", f"/api/v1/portfolio/securities/{MISSING}{suffix}")
        cf, bf = await api(U_MEMBER, "GET", f"/api/v1/portfolio/securities/{S['f2_asset']}{suffix}")
        check(bool(cf == 404 and (cf, bf) == (cm, bm)), f"securities/{{id}}{suffix}: F2's → identical to missing",
              f"{cf} {bf} vs {cm} {bm}")
    c, b = await api(U_MEMBER, "GET", f"/api/v1/portfolio/securities/{S['f1_asset']}")
    check(bool(c == 200 and F2_MARK not in body_text(b)), "F1's instrument detail opens", f"{c}")

    # transactions
    c, b = await api(U_MEMBER, "GET", "/api/v1/portfolio/transactions")
    t_owners = {t.get("owner_entity_id") for t in b.get("transactions", [])} if c == 200 else set()
    check(bool(c == 200 and t_owners and t_owners <= want), "Transactions grid: only F1's entries", f"{c} {t_owners}")
    cm, bm = await api(U_MEMBER, "GET", f"/api/v1/portfolio/transactions/{MISSING}")
    cf, bf = await api(U_MEMBER, "GET", f"/api/v1/portfolio/transactions/{S['f2_txn']}")
    check(bool(cf == 404 and (cf, bf) == (cm, bm)), "F2's transaction → identical to missing", f"{cf} vs {cm}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/portfolio/transaction-positions")
    check(bool(c == 200 and F2_MARK not in body_text(b) and "Fund F2" not in body_text(b)),
          "the 'record against…' picker: no F2 position", f"{c}")

    # commitments (BUGFIX1, already correct) and the TA projection / obligations by commitment id
    c, b = await api(U_MEMBER, "GET", "/api/v1/portfolio/commitments")
    check(bool(c == 200 and "Fund F2" not in body_text(b) and "Fund F1" in body_text(b)),
          "commitments list: F1's fund, not F2's", f"{c} {dumps(b)}")
    for kind in ("projection", "obligations"):
        cm, bm = await api(U_MEMBER, "GET", f"/api/v1/modeling/ta/{kind}/{MISSING}")
        cf, bf = await api(U_MEMBER, "GET", f"/api/v1/modeling/ta/{kind}/{S['f2_commitment']}")
        co, bo = await api(U_MEMBER, "GET", f"/api/v1/modeling/ta/{kind}/{S['f1_commitment']}")
        check(bool(cf == 404 and (cf, bf) == (cm, bm)), f"TA {kind}: F2's commitment → identical to missing",
              f"{cf} {bf} vs {cm} {bm}")
        check(co != 404, f"TA {kind}: F1's commitment is reachable (not 404)", f"{co} {dumps(bo)}")

    # family-scoped reads that were already correct (regression of the shared rule)
    for path in (f"/api/v1/entities/{eid('f2p')}/direct-assets", f"/api/v1/entities/{eid('f2llc')}/private-investments",
                 f"/api/v1/entities/{eid('f2p')}/net-worth"):
        cm, bm = await api(U_MEMBER, "GET", path.replace(str(eid("f2p")), str(MISSING)).replace(str(eid("f2llc")),
                                                                                                 str(MISSING)))
        cf, bf = await api(U_MEMBER, "GET", path)
        check(bool(cf == 404 and cm == 404), f"{path.split('/')[-1]} of F2 → 404 for the member", f"{cf} {dumps(bf)}")


async def y_staff_only(conn):
    section("[Y] Staff-only portfolio reads refuse the member; the identical request is admitted for the org admin")
    for path in ("/api/v1/portfolio/tax-chase?tax_year=2026", "/api/v1/portfolio/position-account-exceptions",
                 f"/api/v1/portfolio/precedence/households/{HH1}", "/api/v1/udf/records/position",
                 "/api/v1/udf/records/position/export", f"/api/v1/udf/values/position/{S['f2_position']}"):
        cm, bm = await api(U_MEMBER, "GET", path)
        ca, ba = await api(U_OA, "GET", path)
        check(bool(cm == 403 and F2_MARK not in body_text(bm)), f"member → 403 on {path.split('?')[0]}",
              f"{cm} {dumps(bm)}")
        check(ca == 200, f"org admin → 200 on the identical {path.split('?')[0]}", f"{ca} {dumps(ba)}")


async def y_private(conn):
    section("[Y] Task 3 — private investments without a commitment; a fund still requires one; the note")
    c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/private-investments", {
        "kind": "safe", "name": NAME + "Seed SAFE", "amount_invested": "250000", "notes": "MFN; cap 12M",
        "nav": {"value": "300000", "date": AS_OF.isoformat()}})
    check(bool(c == 201 and b.get("has_commitment") is False and b.get("unfunded") is None
               and b.get("commitment_id") is None and b.get("amount_invested") == "250000"),
          "a SAFE with no commitment saves; unfunded not computed (None → '—')", f"{c} {dumps(b)}")
    pid = b.get("position_id")
    n_c = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.commitments WHERE position_id = $1::uuid",
                       pid or str(MISSING), fetch="fetchval")
    check(bool(pid and n_c == 0), "no commitment row was written (never a zero commitment)", f"{n_c}")
    c, b = await api(U_OA, "GET", f"/api/v1/entities/{eid('f1p')}/private-investments")
    row = next((r for r in b.get("rows", []) if r.get("position_id") == pid), None) if c == 200 else None
    check(bool(row and row["has_commitment"] is False and row["unfunded"] is None and row["notes"] == "MFN; cap 12M"
               and row["amount_invested"] == "250000" and row["nav"] == "300000.00"),
          "it lists with amount invested, latest value, unfunded '—' and the note round-trips", f"{dumps(row)}")
    rules = {r["value"]: r["rule"] for r in ((b.get("vocabularies") or {}).get("commitment_rules") or [])}
    check(bool(rules.get("fund") == "required" and rules.get("spv") == "optional" and rules.get("safe") == "none"
               and rules.get("hedge_fund") == "none"), "the envelope publishes each kind's commitment rule", f"{rules}")
    before = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1", ORG_A,
                          fetch="fetchval")
    c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/private-investments", {
        "kind": "fund", "name": NAME + "Fund No Commitment", "nav": {"value": "1", "date": AS_OF.isoformat()},
        "amount_invested": "10"})
    check(bool(c == 422 and "commitment" in body_text(b)), "a fund without its commitment is refused (422)",
          f"{c} {dumps(b)}")
    c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/private-investments", {
        "kind": "hedge_fund", "name": NAME + "HF", "commitment": {"amount": "5", "date": "2025-01-01"},
        "nav": {"value": "1", "date": AS_OF.isoformat()}})
    check(bool(c == 422 and "no commitment" in body_text(b)), "a hedge fund with a commitment is refused (422)",
          f"{c} {dumps(b)}")
    after = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1", ORG_A,
                         fetch="fetchval")
    check(after == before, "the refusals wrote nothing", f"{before} → {after}")
    c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/private-investments", {
        "kind": "spv", "name": NAME + "SPV no commitment", "amount_invested": "50000",
        "nav": {"value": "50000", "date": AS_OF.isoformat()}})
    check(bool(c == 201 and b.get("has_commitment") is False), "an SPV may be entered without a commitment",
          f"{c} {dumps(b)}")


async def y_household(conn):
    section("[Y] Task 4 — household Recalculate updates net worth; approved import rows are live, labelled provisional")
    from services import portfolio_assets as PA
    from services.database import get_pool

    c0, d0 = await api(U_OA, "GET", f"/api/v1/admin/households/{HH1}")
    check(bool(c0 == 200 and ((d0.get("permissions") or {}).get("can") or {}).get("recalculate") is True),
          "the household envelope offers Recalculate to the org admin", f"{c0} {dumps(d0.get('permissions'))}")
    cs, ds = await api(U_SUPPORT, "GET", f"/api/v1/admin/households/{HH1}")
    check(cs == 403, "support staff (no manage_members) does not open household management", f"{cs}")
    # settle: one Recalculate first (any refresh still in flight from the seed is overtaken), then the baseline
    cz, rz = await api(U_OA, "POST", f"/api/v1/admin/households/{HH1}/recalculate")
    g0 = Decimal(str(((rz.get("net_worth") or {}).get("gross")) or "0")) if cz == 200 else None
    check(bool(cz == 200 and rz.get("status") == "done"), "baseline Recalculate (org admin) runs", f"{cz} {dumps(rz)}")
    # a new valuation written WITHOUT the write path's refresh: net worth must not move until Recalculate
    pool = await get_pool()
    async with pool.acquire() as c:
        await PA.record_valuation(c, org_id=str(ORG_A), asset_id=S["f1_asset"], valuation_date=AS_OF,
                                  value=Decimal("2200000"), value_basis=PA.TOTAL, status="final", purpose="market",
                                  currency_code="USD", valuation_method="appraisal", valuation_source="verify")
    c1, d1 = await api(U_OA, "GET", f"/api/v1/admin/households/{HH1}")
    g1 = Decimal(str(((d1.get("net_worth") or {}).get("gross")) or "0"))
    check(bool(c1 == 200 and g0 is not None and g1 == g0), "before Recalculate the new valuation has not moved the "
          "household's net worth (it reads the rolled-up holdings)", f"{g0} → {g1}")
    cm, bm = await api(U_MEMBER, "POST", f"/api/v1/admin/households/{HH1}/recalculate")
    check(cm == 403, "a member cannot Recalculate", f"{cm} {dumps(bm)}")
    cb, bb = await api(U_OB_ADMIN, "POST", f"/api/v1/admin/households/{HH1}/recalculate")
    check(cb == 404, "org B's admin on org A's household → 404", f"{cb}")
    cr, rr = await api(U_OA, "POST", f"/api/v1/admin/households/{HH1}/recalculate")
    g2 = Decimal(str(((rr.get("net_worth") or {}).get("gross")) or "0")) if cr == 200 else None
    check(bool(cr == 200 and rr.get("status") == "done" and g2 is not None and g2 - g1 == Decimal("1000000.00")),
          "Recalculate runs the family rollup now: gross moves by exactly the new valuation (+1,000,000)",
          f"{cr} {dumps(rr)}; before {g1}")
    req = await scoped(conn, ORG_A, "SELECT status, entity_ids FROM portfolio.rollup_requests WHERE id = $1::uuid",
                       rr.get("request_id") or str(MISSING), fetch="fetchrow")
    check(bool(req and req["status"] == "done" and {str(x) for x in req["entity_ids"]}
               >= {str(eid("f1p")), str(eid("f1llc"))} and str(eid("f2p")) not in {str(x) for x in req["entity_ids"]}),
          "the rollup request is family-scoped (F1's members, never F2)", f"{dict(req) if req else None}")

    csv_text = ("account_mask,as_of_date,name,ticker,cusip,asset_class,sub_class,taxonomy_key,quantity,price,"
                "market_value,cost_basis\n"
                f"****5601,{AS_OF.isoformat()},{NAME}Northline Fund (NLF),,,Private,Hedge fund,taxonomy_sc_1,,,"
                "400000,350000\n"
                f"****5602,{AS_OF.isoformat()},{NAME}Northline Fund (NLF),,,Private,Hedge fund,taxonomy_sc_1,,,"
                "100000,90000\n")
    cu, up = await api(U_SUPER, "POST", "/api/v1/admin/integrated-imports",
                       files={"file": ("int.csv", csv_text.encode(), "text/csv")})
    iid = (up or {}).get("id")
    check(bool(cu == 201 and up.get("status") == "draft"), "an integrated import uploads as a draft", f"{cu} {dumps(up)}")
    ca, ap = await api(U_SUPER2, "POST", f"/api/v1/admin/integrated-imports/{iid}/approve", {"note": "second person"})
    check(ca == 200, "a second Super Admin approves", f"{ca} {dumps(ap)}")
    c, b = await api(U_OA, "GET", "/api/v1/portfolio/positions?resolve_values=false&limit=500")
    prov = [r for r in b.get("positions", []) if r.get("is_provisional") is True] if c == 200 else []
    check(bool(c == 200 and len(prov) == 2 and all(r["is_current"] for r in prov)
               and (b.get("vocabularies") or {}).get("provisional_label") == "Provisional — until the custodian feed"),
          "the approved rows are LIVE positions in the grid, flagged provisional with the API's label",
          f"{c} {len(prov)} {dumps((b.get('vocabularies') or {}).get('provisional_label'))}")
    non = [r for r in b.get("positions", []) if r.get("is_provisional") is not True] if c == 200 else []
    check(bool(non), "…and nothing else is flagged provisional", f"{len(non)} non-provisional rows")
    c, d = await api(U_OA, "GET", f"/api/v1/admin/households/{HH1}")
    ph = d.get("provisional_holdings") or [] if c == 200 else []
    check(bool(len(ph) == 2 and all(x["label"] == "Provisional — until the custodian feed" for x in ph)
               and {x["market_value"] for x in ph} == {"400000", "100000"}),
          "the household lists them as provisional holdings (fixed-point values)", f"{dumps(ph)}")
    # the Northline question: two lines, one manual asset
    c, m = await api(U_OA, "GET", "/api/v1/portfolio/manual-assets")
    nl = [a for a in ((m.get("rows") or {}).get("assets") or []) if "Northline" in (a.get("name") or "")] \
        if c == 200 else []
    check(bool(len(nl) == 1 and nl[0]["position_count"] == 2 and nl[0]["owner_count"] == 2
               and nl[0]["same_name_count"] == 1),
          "manual-asset report: two identical NLF lines share ONE org asset (2 positions, 2 owners)", f"{c} {dumps(nl)}")
    cm, _ = await api(U_MEMBER, "GET", "/api/v1/portfolio/manual-assets")
    check(cm == 403, "the manual-asset report is refused to a member", f"{cm}")


async def y_addresses(conn):
    section("[Y] Task 5 — addresses: edit and end with history; support staff admitted, member refused")
    body = {"address_type": "primary_residence", "street1": "1 Vine Road", "city": "Napa", "country": "US",
            "is_seasonal": False}
    cm, bm = await api(U_MEMBER, "POST", f"/api/v1/entities/{eid('f1p')}/addresses", body)
    check(cm == 403, "a member cannot add an address (valid body)", f"{cm}")
    c, a = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('f1p')}/addresses", body)
    check(c == 201, "support staff (edit_crm_contact) adds an address", f"{c} {dumps(a)}")
    aid = a.get("id")
    edit = {**body, "street1": "2 Vine Road", "is_seasonal": True, "season_from_month": 11, "season_to_month": 4,
            "note": "Winter residence"}
    cm, bm = await api(U_MEMBER, "PUT", f"/api/v1/entities/{eid('f1p')}/addresses/{aid}", edit)
    check(cm == 403, "a member cannot edit it (valid body)", f"{cm}")
    c, e = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('f1p')}/addresses/{aid}", edit)
    check(bool(c == 200 and e.get("street1") == "2 Vine Road" and e.get("note") == "Winter residence"
               and e.get("season_from_month") == 11 and e.get("id") == aid),
          "support staff edits it: street, seasonal months and note saved; the id is kept", f"{c} {dumps(e)}")
    arch = await scoped(conn, ORG_A, "SELECT street1 FROM entity_addresses WHERE entity_id = $1 AND system_to IS NOT "
                                     "NULL ORDER BY system_to", eid("f1p"))
    check([r["street1"] for r in arch] == ["1 Vine Road"], "the prior version is kept (system-axis archive)",
          f"{[r['street1'] for r in arch]}")
    cm, bm = await api(U_MEMBER, "POST", f"/api/v1/entities/{eid('f1p')}/addresses/{aid}/end")
    check(cm == 403, "a member cannot end it", f"{cm}")
    c, en = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('f1p')}/addresses/{aid}/end")
    check(bool(c == 200 and en.get("ended") is True), "support staff ends it", f"{c} {dumps(en)}")
    row = await scoped(conn, ORG_A, "SELECT valid_to, street1 FROM entity_addresses WHERE id = $1::uuid", aid,
                       fetch="fetchrow")
    n_hist = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_addresses WHERE entity_id = $1", eid("f1p"),
                          fetch="fetchval")
    check(bool(row and row["valid_to"] is not None and n_hist == 3),
          "ended = valid_to set, nothing deleted (live row + 2 archived versions)", f"{dict(row) if row else None} "
                                                                                    f"{n_hist}")
    c, full = await api(U_OA, "GET", f"/api/v1/entities/{eid('f1p')}/full")
    check(bool(c == 200 and all(x.get("id") != aid for x in full.get("addresses", []))),
          "an ended address leaves the current list", f"{c}")
    c2, b2 = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/addresses/{aid}/end")
    cmiss, bmiss = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/addresses/{MISSING}/end")
    check(bool(c2 == 404 and (c2, b2) == (cmiss, bmiss)), "ending it again = the same 404 as a missing id", f"{c2}")
    cb, _ = await api(U_OB_ADMIN, "PUT", f"/api/v1/entities/{eid('f1p')}/addresses/{aid}", edit)
    check(cb in (403, 404), "org B cannot touch org A's address", f"{cb}")


async def y_controls(conn):
    section("[Y] Task 6 — permission-true CRM controls: envelope false ⇔ the server refuses (identical request)")
    c, sp = await api(U_SUPPORT, "GET", f"/api/v1/entities/{eid('f1p')}/permissions")
    c2, op = await api(U_OA, "GET", f"/api/v1/entities/{eid('f1p')}/permissions")
    c3, su = await api(U_SUPER, "GET", f"/api/v1/entities/{eid('f1p')}/permissions")
    s_can = (sp.get("permissions") or {}).get("can") or {}
    o_can = (op.get("permissions") or {}).get("can") or {}
    u_can = (su.get("permissions") or {}).get("can") or {}
    check(bool(c == 200 and s_can.get("addresses") is True and s_can.get("relationships") is False
               and s_can.get("graph") is False and s_can.get("new_entity") is False),
          "support staff: addresses yes; relationships / hierarchy / new entity no", f"{s_can}")
    check(bool(c2 == 200 and o_can.get("relationships") is True and o_can.get("new_entity") is True
               and o_can.get("graph") is False), "org admin: relationships + new entity yes; hierarchy (staff) no",
          f"{o_can}")
    check(bool(c3 == 200 and u_can.get("graph") is True), "super admin: hierarchy yes", f"{u_can}")
    cv, v = await api(U_SUPPORT, "GET", "/api/v1/crm/vocabularies")
    cv2, v2 = await api(U_OA, "GET", "/api/v1/crm/vocabularies")
    check(bool(cv == 200 and ((v.get("permissions") or {}).get("can") or {}).get("new_entity") is False
               and ((v2.get("permissions") or {}).get("can") or {}).get("new_entity") is True),
          "the CRM list envelope: New Entity false for support, true for the org admin", f"{cv}/{cv2}")
    new = {"entity_type": "llc", "display_name": NAME + "New Co"}
    cs, bs = await api(U_SUPPORT, "POST", "/api/v1/entities", new)
    co, bo = await api(U_OA, "POST", "/api/v1/entities", new)
    check(bool(cs == 403 and co == 201), "POST /entities: support refused, the identical request admitted for the org "
          "admin", f"{cs} {dumps(bs)} / {co}")
    rel = {"from_entity_id": str(eid("f1p")), "to_entity_id": str(eid("f2llc")), "relationship_type": "ownership",
           "ownership_pct": 5}
    cs, bs = await api(U_SUPPORT, "POST", "/api/v1/entity-relationships", rel)
    co, bo = await api(U_OA, "POST", "/api/v1/entity-relationships", rel)
    cu, bu = await api(U_SUPER, "POST", "/api/v1/entity-relationships", rel)
    check(bool(cs == 403 and co == 403 and cu == 201), "hierarchy Add Relationship: support and org admin refused "
          "(envelope false), super admin admitted (envelope true)", f"{cs}/{co}/{cu} {dumps(bu)}")
    crel = {"other_entity_id": str(eid("f2p")), "relationship_type": "spouse"}
    cs, bs = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('f1p')}/crm-relationships", crel)
    co, bo = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/crm-relationships", crel)
    check(bool(cs == 403 and co == 201), "CRM relationship add: support refused (envelope false), the identical "
          "request admitted for the org admin (envelope true)", f"{cs} {dumps(bs)} / {co} {dumps(bo)}")


async def y_picker_money(conn):
    section("[Y] Task 6 — option-contract picker; fixed-point money; list render")
    c, b = await api(U_OA, "GET", "/api/v1/portfolio/option-contracts?underlying_ticker=PE3VZZ")
    check(bool(c == 200 and b.get("rows") == [] and "not in the security master" in str(b.get("empty_reason"))),
          "listed_option picker: an unknown underlying says why there is no contract", f"{c} {dumps(b)}")
    real = await platform(conn, "SELECT s.id, i.id_value FROM portfolio.option_contracts oc JOIN "
                                "portfolio.securities_global s ON s.id = oc.underlying_global_security_id JOIN "
                                "portfolio.securities_global_identifiers i ON i.global_security_id = s.id AND i.id_type = "
                                "'ticker' WHERE oc.valid_to IS NULL AND oc.system_to IS NULL LIMIT 1", fetch="fetchrow")
    if real is None:
        find("no rep8 option contract exists in this database — the picker's populated branch is shown by its node test")
    else:
        c, b = await api(U_OA, "GET", f"/api/v1/portfolio/option-contracts?underlying_ticker={real['id_value']}")
        check(bool(c == 200 and b.get("rows") and all("E" not in r["strike"] for r in b["rows"])),
              f"the picker lists the existing contracts on {real['id_value']} (fixed-point strikes)", f"{c}")
    cm, _ = await api(U_MEMBER, "GET", "/api/v1/portfolio/option-contracts?underlying_ticker=AAPL")
    check(cm == 403, "the picker is refused to a member", f"{cm}")

    from services.money_text import fixed

    check(bool(fixed(Decimal("9.0E+5")) == "900000" and fixed(Decimal("1E+6")) == "1000000"),
          "money_text.fixed: exponent forms serialise fixed-point")
    c, b = await api(U_OA, "GET", f"/api/v1/entities/{eid('f1p')}/direct-assets")
    row = next((r for r in b.get("rows", []) if r.get("asset_id") == S["f1_asset"]), {}) if c == 200 else {}
    cost = row.get("cost_basis")
    check(bool(cost and "E" not in cost and Decimal(cost) == Decimal("900000")), "the $900,000 cost comes back "
          "fixed-point from the API", f"{cost!r}")
    texts = body_text(b) + body_text((await api(U_OA, "GET", "/api/v1/portfolio/positions?limit=500"))[1])
    check(not re.search(r'"-?\d+(\.\d+)?E[+-]\d+"', texts), "no scientific notation anywhere in the direct-asset or "
          "positions responses")
    code, out = node_eval(
        "const CD = await import('../lib/crmDetail.mjs');\nconst PE = await import('../lib/portentry.mjs');\n"
        f"console.log(JSON.stringify([CD.formatMoney({json.dumps(cost)}), PE.money('9.0E+5'), "
        "CD.formatMoney('9.0E+5')]));\n")
    check(bool(code == 0 and out.endswith('["$900,000.00","$900,000","$900,000.00"]')),
          "the $900,000 cost renders '900,000.00' — never scientific notation", out[-200:])
    code, out = node_eval("const L = await import('../lib/listState.mjs');\n"
                          "console.log(JSON.stringify([L.listView(null).state, L.listView({rows: []}).state, "
                          "L.listView({rows: [1]}).state]));\n")
    check(out.endswith('["loading","empty","rows"]'), "list render: null is loading (never the empty sentence)",
          out[-200:])


async def y_fix1_taxonomy(conn):
    """FIX 1 (code, 4a-c) on org B's fixture family: every creation path keys its asset by kind and rolls up; an
    unkeyed position is REPORTED; the backfill dry-run counts, the real run keys, a second run changes nothing."""
    section("[Y] FIX 1 — every creation path assigns a taxonomy key; the rollup reports what it skips; the backfill")
    import io
    from contextlib import redirect_stdout

    import backfill_taxonomy_keys as BT
    from services import portfolio_assets as PA
    from services.database import get_pool, reset_rls_context, set_rls_context
    from services.org_settings import DEFAULT_SETTINGS
    from services.portfolio_cash import ensure_cash_asset
    from services.portfolio_manual import manual_asset_for

    kmap = DEFAULT_SETTINGS["portfolio.kind_taxonomy"]
    tax = {r["config_key"] for r in await scoped(conn, ORG_B, "SELECT config_key FROM config WHERE org_id = $1 AND "
                                                             "category = 'asset_taxonomy' AND is_active", ORG_B)}
    check(bool(tax and set(kmap.values()) <= tax), "the fixture org's own taxonomy holds every mapped key",
          f"{len(tax)} keys; missing {sorted(set(kmap.values()) - tax)}")
    owner = eid("orgb")
    c0, r0 = await api(U_OB_ADMIN, "POST", f"/api/v1/admin/households/{HH_B}/recalculate")
    g0 = Decimal(str(((r0.get("net_worth") or {}).get("gross")) or "0")) if c0 == 200 else None
    # the seed's collectible (org B Painting, 5,000) was created through the direct-asset path AFTER the fix
    check(bool(c0 == 200 and g0 == Decimal("5000.00") and (r0.get("skipped") or {}).get("count") == 0),
          "baseline: the seed's collectible is keyed and counted (gross 5,000, nothing skipped)", f"{c0} {dumps(r0)}")
    made: dict[str, tuple[str, Decimal]] = {}   # asset_type -> (asset_id, value)
    v = 1000
    for kind in ("real_estate", "collectible", "operating_business", "insurance_cash_value"):
        v += 1000
        c, b = await api(U_OB_ADMIN, "POST", f"/api/v1/entities/{owner}/direct-assets", {
            "kind": kind, "name": NAME + f"FIX1 {kind}", "valuation": {"value": str(v), "date": AS_OF.isoformat(),
                                                                      "method": "appraisal"}})
        check(bool(c == 201 and b.get("asset_id")), f"direct asset {kind} created", f"{c} {dumps(b)}")
        made[kind] = (b.get("asset_id"), Decimal(v))
    for kind, extra in (("fund", {"commitment": {"amount": "100000", "date": "2024-01-02"}, "called_to_date": "1"}),
                        ("hedge_fund", {"amount_invested": "1"}), ("direct_deal", {"amount_invested": "1"}),
                        ("spv", {"amount_invested": "1"}), ("safe", {"amount_invested": "1"}),
                        ("note", {"amount_invested": "1"})):
        v += 1000
        c, b = await api(U_OB_ADMIN, "POST", f"/api/v1/entities/{owner}/private-investments", {
            "kind": kind, "name": NAME + f"FIX1 {kind}", "nav": {"value": str(v), "date": AS_OF.isoformat()}, **extra})
        check(bool(c == 201 and b.get("asset_id")), f"private investment {kind} created", f"{c} {dumps(b)}")
        made[f"private_{kind}"] = (b.get("asset_id"), Decimal(v))
    pool = await get_pool()
    tokens = set_rls_context(str(ORG_B), False)
    try:
        async with pool.acquire() as c:
            for kind in ("mutual_fund", "money_market", "municipal_bond", "private", "listed_option",
                         "digital_asset", "cash"):
                v += 1000
                if kind == "cash":
                    aid = await ensure_cash_asset(c, org_id=str(ORG_B), currency_code="USD")
                else:
                    aid = await manual_asset_for(c, ORG_B, {"asset_kind": kind, "name": NAME + f"FIX1 {kind}",
                                                            "identifiers": {}, "terms": {}})
                await PA.create_position(c, org_id=str(ORG_B), owner_entity_id=str(owner), asset_id=str(aid),
                                         as_of_date=AS_OF, authority="manual", source_system="manual",
                                         ownership_basis="value", market_value=Decimal(v))
                made[kind] = (str(aid), Decimal(v))
    finally:
        reset_rls_context(tokens)
    got = {r["id"]: r for r in await scoped(conn, ORG_B, "SELECT id::text AS id, asset_type, default_taxonomy_key FROM "
                                                         "portfolio.assets WHERE id = ANY($1::uuid[]) AND system_to "
                                                         "IS NULL", [UUID(a) for a, _ in made.values()])}
    wrong = {k: (got.get(a) or {}).get("default_taxonomy_key") for k, (a, _) in made.items()
             if (got.get(a) or {}).get("default_taxonomy_key") != kmap[k]}
    check(not wrong and len(made) == 17, f"each of the {len(made)} creation kinds got the org's key for its kind "
          "(direct asset ×4, private investment ×6, manual asset ×6, cash)", f"wrong {wrong}")
    # a position deliberately left with NO key: kind 'other' (no honest bucket) through the real path
    c, b = await api(U_OB_ADMIN, "POST", f"/api/v1/entities/{owner}/direct-assets", {
        "kind": "other", "name": NAME + "FIX1 other", "valuation": {"value": "777", "date": AS_OF.isoformat(),
                                                                    "method": "appraisal"}})
    other_pos = (b.get("position_ids") or [None])[0] if c == 201 else None
    # and one created the way the pre-fix paths did (no key at all) — the backfill's target
    raw_aid, raw_pid = await scoped(conn, ORG_B, """
        WITH a AS (INSERT INTO portfolio.assets (org_id, name, asset_class, asset_type, ownership_basis,
                       valuation_method) VALUES ($1, $2, 'hard_asset', 'real_estate', 'value', 'appraisal')
                   RETURNING id),
             p AS (INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis,
                       market_value, authority, source_system) SELECT $1, $3, a.id, $4, 'value', 888, 'manual',
                       'manual' FROM a RETURNING id)
        SELECT (SELECT id::text FROM a), (SELECT id::text FROM p)""", ORG_B, NAME + "FIX1 legacy unkeyed", owner,
                                       AS_OF, fetch="fetchrow")
    added = sum(val for _, val in made.values())
    c1, r1 = await api(U_OB_ADMIN, "POST", f"/api/v1/admin/households/{HH_B}/recalculate")
    g1 = Decimal(str(((r1.get("net_worth") or {}).get("gross")) or "0")) if c1 == 200 else None
    check(bool(c1 == 200 and g1 is not None and g1 - g0 == added),
          f"household gross = the sum of the keyed values (+{added}); the unkeyed two are not counted",
          f"{g0} → {g1}")
    sk = r1.get("skipped") or {}
    check(bool(sk.get("count") == 2 and sk.get("unclassified") == 2
               and set(sk.get("position_ids") or []) == {other_pos, raw_pid}),
          "Recalculate REPORTS the two unkeyed positions (count 2, both ids, unclassified), never a silent $0",
          f"{dumps(sk)}")
    cd, dd = await api(U_OB_ADMIN, "GET", f"/api/v1/admin/households/{HH_B}")
    check(bool(cd == 200 and (dd.get("skipped") or {}).get("count") == 2),
          "the household detail carries the same skipped report", f"{cd} {dumps(dd.get('skipped'))}")
    # the backfill: dry-run counts, writes nothing; the real run keys exactly the legacy asset; a 2nd run is a no-op
    fp0 = await fingerprint(conn, "portfolio.assets", where="org_id = $1", args=(ORG_B,))
    buf = io.StringIO()
    with redirect_stdout(buf):
        n_dry = await BT.run(conn, [str(ORG_B)], dry_run=True)
    out = buf.getvalue()
    fp1 = await fingerprint(conn, "portfolio.assets", where="org_id = $1", args=(ORG_B,))
    plan = await BT.plan_org(conn, str(ORG_B))
    check(bool(n_dry == 0 and fp1 == fp0), "backfill --dry-run writes nothing (assets count + checksum unchanged)",
          f"{n_dry}")
    check(bool(set(plan) == {"real_estate", "other"} and plan["real_estate"]["assets"] == [raw_aid]
               and plan["real_estate"]["positions"] == 1 and plan["real_estate"]["key"] == kmap["real_estate"]
               and plan["other"]["key"] is None and len(plan["other"]["assets"]) == 1
               and "KIND real_estate: assets 1, live unkeyed positions 1 -> " + kmap["real_estate"] in out
               and "KIND other: assets 1" in out and "SKIP" in out),
          "the dry-run lists the right counts per kind (legacy real estate 1 → its key; 'other' 1 → left, said why)",
          out[-400:])
    buf = io.StringIO()
    with redirect_stdout(buf):
        n1 = await BT.run(conn, [str(ORG_B)], dry_run=False)
    row = await scoped(conn, ORG_B, "SELECT (SELECT default_taxonomy_key FROM portfolio.assets WHERE id = $1::uuid AND "
                                    "system_to IS NULL) AS k, (SELECT count(*) FROM portfolio.assets WHERE name = $2 "
                                    "AND org_id = $3 AND system_to IS NOT NULL) AS archived, (SELECT taxonomy_key FROM "
                                    "portfolio.positions WHERE id = $4::uuid) AS pk", raw_aid,
                       NAME + "FIX1 legacy unkeyed", ORG_B, raw_pid, fetch="fetchrow")
    check(bool(n1 == 1 and row["k"] == kmap["real_estate"] and row["archived"] == 1 and row["pk"] is None),
          "the real run keys exactly the legacy asset (id kept, prior version archived on the system axis; the "
          "position's own column untouched)", f"{n1} {dict(row)}")
    buf = io.StringIO()
    with redirect_stdout(buf):
        n2 = await BT.run(conn, [str(ORG_B)], dry_run=False)
    fp2 = await fingerprint(conn, "portfolio.assets", where="org_id = $1", args=(ORG_B,))
    with redirect_stdout(io.StringIO()):
        await BT.run(conn, [str(ORG_B)], dry_run=False)
    fp3 = await fingerprint(conn, "portfolio.assets", where="org_id = $1", args=(ORG_B,))
    check(bool(n2 == 0 and fp3 == fp2), "a second run changes nothing (idempotent)", f"{n2}")
    c2, r2 = await api(U_OB_ADMIN, "POST", f"/api/v1/admin/households/{HH_B}/recalculate")
    g2 = Decimal(str(((r2.get("net_worth") or {}).get("gross")) or "0")) if c2 == 200 else None
    sk2 = r2.get("skipped") or {}
    check(bool(c2 == 200 and g2 is not None and g2 - g1 == Decimal("888.00") and sk2.get("count") == 1
               and sk2.get("position_ids") == [other_pos]),
          "after the backfill, Recalculate counts the legacy holding (+888) and still reports only 'other'",
          f"{g1} → {g2}; {dumps(sk2)}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a_a = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_A,
                       fetch="fetchval")
    a_b = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_B,
                       fetch="fetchval")
    b_b = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_B,
                       fetch="fetchval")
    b_a = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_A,
                       fetch="fetchval")
    check(bool(a_a > 0 and b_b > 0 and a_b == 0 and b_a == 0), "RLS positions: each org reads its own, none of the "
          "other's", f"A:{a_a}/{a_b} B:{b_b}/{b_a}")
    c, b = await api(U_OB_ADMIN, "GET", "/api/v1/portfolio/positions?resolve_values=false")
    check(bool(c == 200 and b.get("positions") and NAME + "Lake House" not in body_text(b)
               and F2_MARK not in body_text(b)), "org B's admin sees only org B's positions", f"{c}")
    cb, _ = await api(U_OB_ADMIN, "GET", f"/api/v1/portfolio/positions/{S['f1_position']}")
    check(cb == 404, "org B's admin on org A's position → 404", f"{cb}")
    c, b = await api(U_OA, "GET", "/api/v1/portfolio/positions?resolve_values=false&limit=500")
    check(bool(c == 200 and "Org B Painting" not in body_text(b)), "org A never sees org B's asset", f"{c}")


async def y_routes(conn):
    section("[Y] Routes — own routes via _verify_self; authz1_routes.py --check CURRENT")
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
    code2, out2, _ = run_cmd(["node", "--test", "tests/bugfix3/"], WEB_DIR, timeout=300)
    check(bool(code2 == 0 and re.search(r"^# fail 0", out2, re.M)), "the sprint's node tests (money, list state, "
          "commitment rules, permission rendering, scope text, picker, manual assets) pass", out2[-200:] if code2 else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_bugfix2.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_bugfix2.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_bugfix2.py: 0 FAIL", f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}")


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
    pre = real = demo = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_prints(conn)
        demo = await demo_prints(conn)
        find("demo-olivo rows fingerprinted", f"{ {k: v[0] for k, v in demo.items()} }")
        await run("role", y_role_is_real, conn)
        await run("catalog", y_catalog, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        section("[Y] Task 1 — findings 1a–1e and the route table")
        self_task1(check, find)
        await run("seed holdings", seed_holdings, conn)
        if S.get("f1_position") and S.get("f2_position"):
            await run("leak", y_leak, conn)
            await run("staff-only", y_staff_only, conn)
            await run("private investments", y_private, conn)
            await run("household", y_household, conn)
            await run("picker / money / list", y_picker_money, conn)
            await run("isolation", y_isolation, conn)
            await run("fix1 taxonomy", y_fix1_taxonomy, conn)
        else:
            check(False, "the holdings seed produced both families' positions — dependent sections not run", f"{S}")
        await run("addresses", y_addresses, conn)
        await run("controls", y_controls, conn)
        section("[Y] Static — docs, regression set, front-end markers")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; demo + real rows unchanged")
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
                now = await real_prints(conn)
                for t in REAL_FINGERPRINTED:
                    check(now[t] == real[t], f"real {t} unchanged (count + checksum by primary key)",
                          f"{real[t][0]} → {now[t][0]} rows")
            if demo is not None:
                now = await demo_prints(conn)
                for k in DEMO_SQL:
                    check(now[k] == demo[k], f"demo-olivo {k} unchanged (count + checksum)",
                          f"{demo[k][0]} → {now[k][0]}")
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
