"""verify_bulkapprove1 — Approvals → Bulk approve (BULKAPPROVE1): maker-checker items decided many at once — authority &
custody changes (rep7b) and held-away entries (rep7) — through the SAME single-approval service per item.

WRITTEN, NOT RUN by the sprint (WSL memory). The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_bulkapprove1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the httpx network transport refuses and records, the Auth0 profile fetch is a local stub matching its CURRENT
signature, R2 refuses.

REAL: the app through an ASGI transport (middleware, Principal, every gate), the workflow engine (human tasks), the
rep7 / rep7b services, RLS and the live database. The script's own connection is app_service (rolbypassrls = false,
asserted).

FIXTURES: two orgs 99000000-0000-0000-0000-000000ba0001 / ba0002 (is_test = true, asserted) and their users (is_test,
asserted, auth0_sub 'auth0|bulkapprove1verify-…', @test.local). Names carry the reserved prefix 'BA1V ' (asserted to
match no real row first). Maker M makes 6 authority drafts (one household + five account) and 3 held-away entries
(plus twins approved through the SINGLE routes, and the mixed-batch / bad-id / isolation items); checker C decides.
The demo-olivo rows and every real row are only READ: count + checksum (keyed by the table's real primary key) before
and after.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with execution.teardown / models.teardown /
replica.teardown = 'fixture' set LOCAL (the documented path past the authority / held-away immutability triggers) —
seeds keyed by this run's fixture ids (every org_id table under the fixture orgs: households, accounts, authority rows,
held-away imports / records / sources, positions, assets, lots, human tasks = workflow runs / steps, todos, events,
audit rows; the fixture organizations and users; the fixture roles' grants; the fixture users' audit rows). Every
counted table is re-read for its exact pre-test count.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"

import ast
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
import uuid
from datetime import date
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

SPRINT_KEY = "bulkapprove1"
BEFORE_REF = "584402a"   # bulkapprove1-work base: "merge bugfix3"

READ_GATE = "any(view_authority|approve_authority|view_held_away|approve_held_away)"
NEW_ROUTES = {
    (("GET",), "/api/v1/approvals/bulk"): READ_GATE,
    (("GET",), "/api/v1/approvals/summary"): READ_GATE,
    (("POST",), "/api/v1/approvals/bulk"): "any(approve_authority|approve_held_away)",
}
DISCOVERY = "docs/BULKAPPROVE1_DISCOVERY.md"
SERVICE = "apps/api/services/bulk_approvals.py"
ROUTER = "apps/api/routers/approvals.py"


# ═══ Self-checks (stdlib + git only) ════════════════════════════════════════════════════════════════════════════
def _text(rel: str) -> str:
    """The file as the sprint left it (its own end ref), never a later sprint's edit."""
    t = VS.own_text(SPRINT_KEY, rel)
    if t is None:
        raise RuntimeError(f"{rel} absent at {SPRINT_KEY}'s end ref")
    return t


def self_task1(check, find):
    d = _text(DISCOVERY)
    for marker in ("## 1a.", "## 1b.", "## 1c.", "services.authority.decide", "services.held_away.decide_import",
                   "HT.complete_task", "entered_by", "maker_checker_chk", "approve_authority", "approve_held_away",
                   "LISTED ONLY", "My tasks"):
        check(marker in d, f"Task 1 findings name {marker!r}", DISCOVERY)
    for cand in ("Model approval", "Rebalance proposal", "Allocation policy", "Hypothetical-performance policy",
                 "Integrated-account import"):
        check(cand in d, f"1c lists the later candidate {cand!r} (listed only)", DISCOVERY)
    find("Task 1 findings", "1a: authority = POST /authority/changes/{id}/approve|reject → authority.decide → "
         "HT.complete_task (+ _precheck) → _apply; held-away = POST /held-away/imports/{id}/approve|reject → "
         "held_away.decide_import → HT.complete_task (+ _import_precheck) → _apply_import; maker ≠ checker in the "
         "service, the task, the engine and the table CHECK (held-away keys it on entered_by). 1b: My tasks → the "
         "subject's page → the panel → the row → Approve, one at a time; no list action. 1c: models, proposals, "
         "policies, integrated import and 10 more approve/reject maker-checker kinds — listed, none built.")


#: CASH02 (2026-11-08) — the approvals routes gained the cash-policy bulk type; CASH07 (2026-11-09) the payment
#: type (read by initiate_payment | approve_payment, decided by approve_payment): path → (gate at bulkapprove1's end,
#: gate today). Nothing else about them changed.
_R0 = "any(view_authority|approve_authority|view_held_away|approve_held_away)"
_W0 = "any(approve_authority|approve_held_away)"
CASH02_WIDENED = {
    ("GET", "/api/v1/approvals/bulk"): (_R0, _R0[:-1] + "|manage_cash_policy|initiate_payment|approve_payment)"),
    ("GET", "/api/v1/approvals/summary"): (_R0, _R0[:-1] + "|manage_cash_policy|initiate_payment|approve_payment)"),
    ("POST", "/api/v1/approvals/bulk"): (_W0, _W0[:-1] + "|manage_cash_policy|approve_payment)"),
}


def self_routes(check, find):
    R = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(bool(len(R.before) > 0 and not R.changed and not R.removed), "no existing route's gate changed or vanished",
          f"{R.where()}; {R.changed[:5]} {R.removed[:5]}")
    bad = [(k, R.end[k].get("after")) for k in R.new if NEW_ROUTES.get(k) != R.end[k].get("after")]
    check(bool(set(R.new) == set(NEW_ROUTES) and not bad), f"bulkapprove1's {len(NEW_ROUTES)} new routes, each with "
          "its stated gate", f"{R.where()}; new={R.new} bad={bad}")
    drifted = VS.still_gated(R)
    # CASH02 and CASH07 widened these three gates on purpose: bulk types for cash policies (manage_cash_policy) and
    # payment instructions (initiate_payment / approve_payment). The widening is pinned EXACTLY (each route's gate at bulkapprove1's end + the one key), so any other drift still fails.
    def _pin(k):
        return next((CASH02_WIDENED[(m, k[1])] for m in k[0] if (m, k[1]) in CASH02_WIDENED), None)

    wid = {k: g for k, g in drifted if _pin(k) is not None}
    exact = all(f"{_pin(k)[0]} → {_pin(k)[1]}" == g for k, g in wid.items())
    rest = [(k, g) for k, g in drifted if k not in wid]
    check(bool(R.new and not rest and exact), "bulkapprove1's routes all still exist today with the same gate (CASH02's / "
          "CASH07's pinned widening for cash policies and payments aside)", f"{drifted}")


def _sql_strings(source: str) -> list[str]:
    """Every string constant in ``source`` that is not a docstring (a docstring can never satisfy or break the rule)."""
    tree = ast.parse(source)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value,
                                                                                                     str):
                docs.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(v.value for v in node.values if isinstance(v, ast.Constant)))
    return out


def _calls(source: str, fn_name: str) -> set[str]:
    """Dotted call names inside function ``fn_name``."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            got = set()
            for c in ast.walk(node):
                if isinstance(c, ast.Call):
                    f = c.func
                    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                        got.add(f"{f.value.id}.{f.attr}")
                    elif isinstance(f, ast.Name):
                        got.add(f.id)
            return got
    return set()


def self_static(check, find):
    svc = _text(SERVICE)
    writes = [s for s in _sql_strings(svc) if re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b", s, re.I)]
    targets = sorted({m.group(2).lower() for s in writes for m in
                      re.finditer(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w.]+)", s, re.I)})
    check(bool(writes and targets == ["audit_log"]), "ONE PATH: the bulk service writes ONLY audit rows — no setting, "
          "position or task is written by new code", f"targets={targets}")
    check(bool(not _sql_strings('"""INSERT INTO accounts"""\nx = 1\n')),
          "self-check: a docstring cannot satisfy the write scan", "")
    calls = _calls(svc, "_decide_one")
    check(bool({"AU.decide", "HA.decide_import"} <= calls), "each item goes through the SAME service function the "
          "single route calls (authority.decide / held_away.decide_import)", f"{sorted(calls)}")
    single_au = _calls(_text("apps/api/routers/authority.py"), "approve")
    single_ha = _calls(_text("apps/api/routers/held_away.py"), "approve_import")
    check(bool("AU.decide" in single_au and "HA.decide_import" in single_ha),
          "… and those ARE what the single approve routes call", f"{sorted(single_au)} {sorted(single_ha)}")
    loop = _calls(svc, "decide_bulk")
    check(bool("_decide_one" in loop and "_audit" in loop), "decide_bulk loops per item (one _decide_one each) and "
          "audits", f"{sorted(loop)}")
    check(bool(re.search(r"MAX_ITEMS\s*=\s*200\b", svc)), "the hard cap per request is 200", "")
    rtr = _text(ROUTER)
    check(bool("extra=\"forbid\"" in rtr and "org_id" not in rtr.split("class BulkDecision", 1)[1].split("def ", 1)[0]),
          "the request model forbids extras and declares no org_id", "")
    reg = _text("scripts/regression_set.txt")
    check("verify_bulkapprove1.py" in reg.splitlines(), "scripts/regression_set.txt lists verify_bulkapprove1.py", "")
    st = _text("docs/PROJECT_STATUS.md")
    check(bool("BULKAPPROVE1" in st and "one-path rule" in st and "Later candidates" in st),
          "PROJECT_STATUS records the screen, the one-path rule and the 1c candidates", "")
    menu = _text("apps/web/lib/menuVisibility.mjs")
    check(bool('"/portfolio/approvals", "Approvals", GATE_APPROVALS' in menu), "the menu lists Portfolio → Approvals "
          "behind GATE_APPROVALS", "")
    for rel, pat in (("apps/web/app/portfolio/households/[id]/page.js", "<BulkPendingLink householdId={id} />"),
                     ("apps/web/components/proposals/AccountsList.jsx", "<BulkPendingLink />"),
                     ("apps/web/components/dashboard/MyTasks.jsx", "<BulkPendingLink />")):
        check(pat in _text(rel), f"entry point mounted: {rel}", "")
    screen = _text("apps/web/components/approvals/BulkApprove.jsx")
    check(bool("org_id" not in screen and "|| DEFAULT" not in screen), "the screen sends no org_id and has no "
          "client-side default list", "")


def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


#: the maker column each maker-checker table compares approved_by against (held-away records who ENTERED it)
MAKER_CHECKER_COLS = {"public.household_authority": "maker_id", "public.account_authority_settings": "maker_id",
                      "public.held_away_imports": "entered_by"}


def lit_in(cons: dict, table: str, col: str, want: set) -> tuple[bool, list]:
    """The literals ``want`` are catalog-legal for ``table.col``. An EMPTY definition list FAILS (fix 1: it used
    to pass — `not defs or …` — which is how five vocabulary checks passed on zero constraints read)."""
    defs = [d for (t, _n), (c, d) in cons.items() if t == table and c == "c" and col in d]
    allowed = set().union(*(_allowed(d) for d in defs)) if defs else set()
    return bool(defs and want and want <= allowed), defs


def maker_checker_checks(cons: dict, table: str, maker: str) -> list[str]:
    """CHECKs on ``table`` that refuse approved_by = the maker column (normalised definition, not a name match)."""
    out = []
    for (t, n), (c, d) in cons.items():
        flat = re.sub(r"[()\s]", "", d or "")
        if t == table and c == "c" and (f"approved_by<>{maker}" in flat or f"{maker}<>approved_by" in flat):
            out.append(n)
    return out


def self_catalog_rules(check, find):
    """fix 1: the catalog helpers on synthetic definitions — an empty set never passes; bytes kinds never match."""
    ok, _ = lit_in({}, "public.household_authority", "status", {"draft"})
    check(ok is False, "lit(): NO definition found → FAIL (empty never passes)", f"{ok}")
    good = {("public.household_authority", "s"): ("c", "CHECK ((status = ANY (ARRAY['draft'::text, 'approved'::text])))")}
    check(lit_in(good, "public.household_authority", "status", {"draft"})[0] is True, "lit(): a legal literal passes", "")
    check(lit_in(good, "public.household_authority", "status", {"bogus"})[0] is False, "lit(): an illegal literal "
          "fails", "")
    as_bytes = {k: (b"c", d) for k, (_c, d) in good.items()}
    check(lit_in(as_bytes, "public.household_authority", "status", {"draft"})[0] is False, "lit(): an undecoded "
          "bytes kind (the original defect) is not read as a CHECK — and now fails rather than passes", "")
    mc = {("public.household_authority", "x_maker_checker_chk"):
          ("c", "CHECK (((approved_by IS NULL) OR (approved_by <> maker_id)))"),
          ("public.held_away_imports", "y_maker_checker_chk"):
          ("c", "CHECK (((approved_by IS NULL) OR (approved_by <> entered_by)))")}
    check(maker_checker_checks(mc, "public.household_authority", "maker_id") == ["x_maker_checker_chk"],
          "maker ≠ checker found by definition (maker_id)", "")
    check(maker_checker_checks(mc, "public.held_away_imports", "entered_by") == ["y_maker_checker_chk"],
          "maker ≠ checker found by definition (entered_by)", "")
    check(maker_checker_checks(mc, "public.account_authority_settings", "maker_id") == [],
          "maker ≠ checker: a table without the CHECK yields nothing (so the per-table check FAILS)", "")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)
    self_catalog_rules(check, find)


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

import httpx  # noqa: E402

import main  # noqa: E402
from services import bulk_approvals as BA  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import rbac  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import close_pool, get_pool, reset_rls_context, set_rls_context  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "bulkapprove1verify"
NAME = "BA1V "            # reserved fixture name prefix (asserted to match no real row)
REASON = "BA1V verify change"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-000000ba{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)
FULL = frozenset({"view_authority", "manage_authority", "approve_authority", "view_held_away", "manage_held_away",
                  "approve_held_away"})
NO_APPROVE = frozenset({"view_authority", "manage_authority", "view_held_away", "manage_held_away"})
HA_ONLY = frozenset({"view_authority", "view_held_away", "approve_held_away"})
U_M, U_C, U_N, U_HAO, U_MB, U_CB = _id(11), _id(12), _id(13), _id(14), _id(15), _id(16)
USERS = {   # org, short, rbac role, permissions
    U_M: (ORG_A, "maker", "ba1v_approver", FULL),
    U_C: (ORG_A, "checker", "ba1v_approver", FULL),
    U_N: (ORG_A, "noapprove", "ba1v_noapprove", NO_APPROVE),
    U_HAO: (ORG_A, "heldawayonly", "ba1v_heldawayonly", HA_ONLY),
    U_MB: (ORG_B, "makerb", "ba1v_approver", FULL),
    U_CB: (ORG_B, "checkerb", "ba1v_approver", FULL),
}
ENT_A, ENT_B = _id(30), _id(31)
HH1, HH2, HH3, HH_B = _id(40), _id(41), _id(42), _id(43)
# integrated accounts (org A): drafts D2..D6, twin, D7 (mixed), D8 (withdrawn), D9 (bad-id batch), D10 (org B target)
AC = {k: _id(100 + i) for i, k in enumerate(("d2", "d3", "d4", "d5", "d6", "twin", "d7", "d8", "d9", "d10"))}
# held-away accounts (org A): E1..E3 bulk, the twin (single route), E5 (org B target)
HAC = {k: _id(130 + i) for i, k in enumerate(("e1", "e2", "e3", "twin", "e5"))}
ACB, HACB = _id(150), _id(151)
HELD_ROWS = [{"asset_kind": "private", "name": NAME + "Vineyard", "quantity": "1", "market_value": "900000"},
             {"asset_kind": "private", "name": NAME + "Sculpture", "quantity": "2", "market_value": "2500.50"}]
HELD_TOTAL = "902500.50"

#: whole-table exact pre-test counts (tables only this script's fixtures should change during the run)
#: S30: the identity / role tables moved to FIXTURE_IDS — a real sign-in or role change during the run writes them
#: (a whole-table count of a live table is frozen against live state, docs/VERIFY_RULES.md rule 6).
COUNTED = ("public.entities", "public.households", "public.accounts", "public.household_authority",
           "public.account_authority_settings", "public.held_away_sources", "public.held_away_imports",
           "public.held_away_records", "portfolio.assets", "portfolio.positions", "portfolio.tax_lots")
#: written by REAL activity during any run (sign-ins, HollisMuse mid-pass): counted under the fixture orgs only, by
#: name — public.audit_log, public.domain_events, public.member_todos, public.workflow_runs, public.workflow_run_steps
FIXTURE_SCOPED = ("public.audit_log", "public.domain_events", "public.member_todos", "public.workflow_runs")
#: S30: counted by THIS script's fixture ids (orgs / users), never the whole table — audit_log by fixture org OR
#: fixture user (exactly what the teardown deletes), the identity / role tables by their fixture keys.
FIXTURE_IDS = ("public.audit_log", "public.users", "public.organizations", "public.roles", "public.user_roles",
               "public.role_permissions")
REAL_FINGERPRINTED = ("public.household_authority", "public.account_authority_settings", "public.held_away_imports",
                      "public.held_away_records", "public.held_away_sources", "public.accounts", "public.households",
                      "portfolio.positions")

_n_pass = 0
_n_fail = 0
S: dict = {}
NETWORK_CALLS: list = []
CLAIMS_BY_TOKEN: dict[str, dict] = {}


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
    return f"auth0|{TAG}-{USERS[uid][1]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][1]}@test.local"


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:600]


def today() -> date:
    return date.today()


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


def fixture_pred(table: str) -> str:
    """S30 — the fixture-row predicate of a FIXTURE_IDS table ($1 = fixture orgs, $2 = fixture users)."""
    return {
        "public.audit_log": "org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
        "public.users": "org_id = ANY($1::uuid[]) OR id = ANY($2::uuid[])",
        "public.organizations": "id = ANY($1::uuid[]) AND cardinality($2::uuid[]) >= 0",
        "public.roles": "org_id = ANY($1::uuid[]) AND cardinality($2::uuid[]) >= 0",
        "public.user_roles": "user_id = ANY($2::uuid[]) AND cardinality($1::uuid[]) >= 0",
        "public.role_permissions": "role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[])) "
                                   "AND cardinality($2::uuid[]) >= 0",
    }[table]


async def counts(conn) -> dict:
    out = {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}
    for t in FIXTURE_SCOPED:
        if t in FIXTURE_IDS:
            continue
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS,
                                    fetch="fetchval"))
    for t in FIXTURE_IDS:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_pred(t)}", FIXTURE_ORGS,
                                    list(USERS), fetch="fetchval"))
    return out


async def pk_of(conn, table: str) -> list[str]:
    rows = await platform(conn, "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' "
                                "ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str, *, where: str = "true", args=()) -> tuple[int, str]:
    """Count + md5 of every row, ordered by the table's real primary key (read from the catalog). None of these
    tables is written by sign-in activity, so no column is excluded."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = ", ".join(f't."{c}"' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


async def real_prints(conn) -> dict:
    return {t: await fingerprint(conn, t, where="NOT (org_id = ANY($1::uuid[]))", args=(FIXTURE_ORGS,))
            for t in REAL_FINGERPRINTED}


DEMO = "SELECT id FROM public.entities WHERE 'demo-olivo' = ANY(tags)"
DEMO_ACCTS = f"SELECT id FROM public.accounts WHERE primary_entity_id IN ({DEMO})"
DEMO_SQL = {
    "entities": (f"SELECT * FROM public.entities WHERE 'demo-olivo' = ANY(tags)", "id, system_from"),
    "accounts": (f"SELECT * FROM public.accounts WHERE id IN ({DEMO_ACCTS})", "id"),
    "positions": (f"SELECT * FROM portfolio.positions WHERE owner_entity_id IN ({DEMO})", "id"),
    "account_authority": (f"SELECT * FROM public.account_authority_settings WHERE account_id IN ({DEMO_ACCTS})", "id"),
    "household_authority": ("SELECT * FROM public.household_authority WHERE household_id IN (SELECT household_id "
                            f"FROM public.accounts WHERE id IN ({DEMO_ACCTS}))", "id"),
    "held_away_imports": (f"SELECT * FROM public.held_away_imports WHERE account_id IN ({DEMO_ACCTS})", "id"),
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
    """Fixture rows only, by fixture id; the documented privileged path (operator DSN + the teardown GUCs, LOCAL)
    for the immutable authority / held-away / ledger tables."""
    users = list(USERS)
    op = await operator_conn()
    try:
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [FIXTURE_ORGS]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               label="verify_bulkapprove1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.audit_log WHERE user_id = ANY($1::uuid[])", [list(USERS)])])
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


async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


async def y_catalog(conn):
    section("[Y] UNIQUE / CHECK / FK constraints read from the catalog before seeding; reserved prefixes match nothing")
    cons = {}
    for t in ("public.accounts", "public.households", "public.entities", "public.household_authority",
              "public.account_authority_settings", "public.held_away_imports", "public.held_away_sources",
              "public.audit_log", "public.users", "public.organizations"):
        # contype is Postgres "char", which asyncpg decodes as BYTES (b'c'); cast to text so it compares to "c"
        # (fix 1: the bytes value never equalled "c", so every kind counted 0 although rows WERE returned).
        for r in await platform(conn, "SELECT conname, contype::text AS contype, pg_get_constraintdef(oid) AS d "
                                      "FROM pg_constraint WHERE conrelid = $1::regclass "
                                      "AND contype IN ('p', 'c', 'u', 'f')", t):
            cons[(t, r["conname"])] = (r["contype"], r["d"])
    kinds = {k: sum(1 for (_t, _n), (c, _d) in cons.items() if c == k) for k in "pcuf"}
    print(f"[INFO] {len(cons)} constraints read: {kinds}")
    odd = sorted({type(c).__name__ for (c, _d) in cons.values()} - {"str"})
    check(bool(cons and not odd), "constraint kinds decode as text", f"non-str kinds: {odd}")
    check(bool(kinds["c"] > 0 and kinds["f"] > 0 and kinds["p"] > 0), "CHECK, FK and PK constraints were read", f"{kinds}")
    no_pk = [t for t in {t for (t, _n) in cons} if not any(tt == t and c == "p" for (tt, _n), (c, _d) in cons.items())]
    check(bool(len({t for (t, _n) in cons}) == 10 and not no_pk), "every one of the 10 tables has constraints read, "
          "each with its PK", f"tables {len({t for (t, _n) in cons})}, without PK {no_pk}")

    def lit(table, col, want):
        return lit_in(cons, table, col, want)

    for table, col, want in (("public.account_authority_settings", "custody", {"integrated"}),
                             ("public.account_authority_settings", "authority_override", {"advise"}),
                             ("public.household_authority", "authority", {"advise"}),
                             ("public.household_authority", "status", {"draft", "approved", "rejected"}),
                             ("public.held_away_imports", "status", {"draft", "approved", "rejected"})):
        ok, defs = lit(table, col, want)
        check(ok, f"{table}.{col}: the literals this run writes {sorted(want)} are catalog-legal", f"{defs[:2]}")
    for table, maker in MAKER_CHECKER_COLS.items():
        mc = maker_checker_checks(cons, table, maker)
        check(bool(len(mc) == 1), f"maker ≠ checker is a CHECK on {table} (approved_by <> {maker})", f"{mc}")
    for col in ("registration_type", "tax_status", "custodian_code"):
        defs = [d for (t, _n), (c, d) in cons.items() if t == "public.accounts" and c == "c" and col in d]
        vals = _allowed(defs[0]) if defs else []
        ACCOUNT_VALUES[col] = vals[0] if vals else {"registration_type": "individual", "tax_status": "taxable",
                                                    "custodian_code": "ALTRUIST"}[col]
    find("accounts seeded with catalog-legal values", f"{ACCOUNT_VALUES}")
    uq = [n for (t, n), (c, _d) in cons.items() if t == "public.accounts" and c == "u"]
    find("accounts UNIQUE constraints honoured by distinct hashes / masks", f"{uq}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM households WHERE name LIKE $1 AND NOT "
                                "(org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM portfolio.assets WHERE name LIKE $1 "
                                "AND NOT (org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE "
                                "$2 AND NOT (id = ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug "
                                "LIKE $3 AND NOT (id = ANY($4::uuid[]))) + (SELECT count(*) FROM accounts WHERE "
                                "account_number_masked LIKE '****ba%' AND NOT (org_id = ANY($4::uuid[])))",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (names, subs, slugs, masks) match no real row", f"{real}")
    ids = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                         fetch="fetchval")
    check(ids == 0, "the fixture org ids are free after the start teardown", f"{ids}")


def _mask(n: int) -> str:
    return f"****ba{n % 100:02d}"


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"BULKAPPROVE1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[3] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        roles = {}
        for uid, (org, _n, rname, perms) in USERS.items():
            if (org, rname) not in roles:
                rid = await rbac.ensure_role(conn, org, rname, f"BULKAPPROVE1 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        for uid, (org, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, 'member', 'active', true, true)",
                uid, org, email_of(uid), f"BA1V {nm.title()}", sub_of(uid))
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for eid, org, name in ((ENT_A, ORG_A, "Principal A"), (ENT_B, ORG_B, "Principal B")):
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) "
                               "VALUES ($1, $2, $3, 'active', 'individual'::entity_type)", eid, org, NAME + name)
        for hid, org, name in ((HH1, ORG_A, "Household One"), (HH2, ORG_A, "Household Two"),
                               (HH3, ORG_A, "Household Accounts"), (HH_B, ORG_B, "Household B")):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hid, org, NAME + name)
        n = 0
        for group, held, org, hh, ent in ((AC, False, ORG_A, HH3, ENT_A), (HAC, True, ORG_A, HH3, ENT_A),
                                          ({"b": ACB}, False, ORG_B, HH_B, ENT_B),
                                          ({"hb": HACB}, True, ORG_B, HH_B, ENT_B)):
            for _k, aid in group.items():
                n += 1
                mask = _mask(n)
                await conn.execute(
                    "INSERT INTO accounts (id, org_id, household_id, account_number_masked, account_number_hash, "
                    "custodian_code, registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, "
                    "$3, $4, $5, $6, $7, $8, $9, $10)", aid, org, hh, mask,
                    hashlib.sha256(f"{TAG}{aid}".encode()).hexdigest(), ACCOUNT_VALUES["custodian_code"],
                    ACCOUNT_VALUES["registration_type"], ACCOUNT_VALUES["tax_status"], ent, held)
                S[f"mask:{aid}"] = mask


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == 2 and u == len(USERS)), "both fixture orgs and every fixture user are is_test = true", f"{o}/{u}")
    # S30 — every fixture role is a user_roles grant the APP decides as meant (services.authz Principal: Super Admin
    # first, then the role's own permissions); a grant the app does not honour would fake every gate below.
    from services import authz as _AZ

    bad = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, _nm, rname, perms) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 "
                "AND r.org_id = $2", uid, org)]
            pr = await _AZ.load_principal(conn, uid)
            have = set(getattr(pr, "permissions", ()) or ())
            keys = FULL | NO_APPROVE | HA_ONLY
            if not (names == [rname] and pr is not None and pr.admitted and not pr.is_super_admin
                    and (have & keys) == set(perms)):
                bad.append((str(uid), names, sorted(have & keys)))
    check(not bad, "every fixture role is a user_roles grant the app decides as meant (its permissions, exactly; "
          "nobody Super Admin)", f"{bad}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_bulkapprove1"}}
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
    import inspect

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
        raise AssertionError(f"verify_bulkapprove1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org), self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


def node_eval(js: str) -> tuple[int, str]:
    """Run an ES-module snippet in apps/web/tests (the REAL lib / component files, never a copy)."""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", dir=str(WEB_DIR / "tests"), delete=False,
                                     encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        code, out, err = run_cmd(["node", path], WEB_DIR, timeout=120)
        return code, (out + ("\n" + err if code else "")).strip()
    finally:
        os.unlink(path)


# ═══ Fixture items through the REAL request routes ══════════════════════════════════════════════════════════════
async def req_account(uid, acct):
    return await api(uid, "POST", f"/api/v1/authority/accounts/{acct}/changes",
                     {"authority_override": "advise", "custody": "integrated",
                      "effective_from": today().isoformat(), "reason": REASON})


async def req_household(uid, hh):
    return await api(uid, "POST", f"/api/v1/authority/households/{hh}/changes",
                     {"authority": "advise", "effective_from": today().isoformat(), "reason": REASON})


async def held_entry(uid, acct):
    return await api(uid, "POST", f"/api/v1/held-away/accounts/{acct}/imports/manual",
                     {"as_of": today().isoformat(), "rows": HELD_ROWS, "notes": NAME + "entry"})


async def bulk(uid, type_, ids, decision="approve", note=None):
    body = {"type": type_, "item_ids": [str(i) for i in ids], "decision": decision}
    if note is not None:
        body["note"] = note
    return await api(uid, "POST", "/api/v1/approvals/bulk", body)


async def y_make(conn):
    section("[Y] Fixture items: 6 authority drafts + 3 held-away entries by maker M (plus twins and batch items)")
    made = []
    c, b = await req_household(U_M, HH1)
    S["D1"], S["task:D1"] = (b.get("change") or {}).get("id"), b.get("task_id")
    made.append(("D1 household HH1", c))
    c, b = await req_household(U_M, HH2)
    S["TW_HH"], S["task:TW_HH"] = (b.get("change") or {}).get("id"), b.get("task_id")
    made.append(("twin household HH2", c))
    for k, aid in AC.items():
        key = {"twin": "TW_ACC"}.get(k, k.upper())
        c, b = await req_account(U_M, aid)
        S[key], S[f"task:{key}"] = (b.get("change") or {}).get("id"), b.get("task_id")
        made.append((f"{key} account", c))
    for k, aid in HAC.items():
        key = {"twin": "TW_E"}.get(k, k.upper())
        c, b = await held_entry(U_M, aid)
        S[key], S[f"task:{key}"] = b.get("id"), b.get("approval_task_id")
        made.append((f"{key} held-away", c))
    c, b = await req_account(U_MB, ACB)
    S["DB1"], S["task:DB1"] = (b.get("change") or {}).get("id"), b.get("task_id")
    made.append(("DB1 org B account", c))
    c, b = await held_entry(U_MB, HACB)
    S["EB1"], S["task:EB1"] = b.get("id"), b.get("approval_task_id")
    made.append(("EB1 org B held-away", c))
    bad = [m for m in made if m[1] != 201]
    check(bool(not bad and len(made) == 2 + len(AC) + len(HAC) + 2),
          f"{len(made)} drafts created through the real request routes (each 201, each with its own task)",
          f"{bad}")
    keys = [k for k in S if not k.startswith(("task:", "mask:"))]
    check(bool(all(S.get(k) and S.get(f"task:{k}") for k in keys)), "every draft has an id and an approval task",
          f"{[k for k in keys if not (S.get(k) and S.get(f'task:{k}'))]}")
    # D8 is WITHDRAWN: its approval task cancelled by the maker (HT.cancel_task — the documented withdraw path)
    pool = await get_pool()
    with _ctx(ORG_A):
        await HT.cancel_task(pool, org_id=ORG_A, task_id=S["task:D8"], actor_id=U_M,
                             reason="BA1V verify: withdrawn by the person who asked")
    st = await task_status(conn, ORG_A, [S["task:D8"]])
    row = await authority_row(conn, ORG_A, S["D8"])
    check(bool(st.get(S["task:D8"]) == "cancelled" and row and row["status"] == "draft"),
          "D8 withdrawn: its task cancelled, the change itself still a draft", f"{st} {dict(row) if row else None}")


# ═══ State reads ════════════════════════════════════════════════════════════════════════════════════════════════
AUTH_TABLES = ("public.household_authority", "public.account_authority_settings")


async def authority_row(conn, org, cid):
    for t in AUTH_TABLES:
        r = await scoped(conn, org, f"SELECT * FROM {t} WHERE id = $1::uuid AND system_to IS NULL", cid,
                         fetch="fetchrow")
        if r is not None:
            return r
    return None


async def task_status(conn, org, task_ids) -> dict:
    rows = await scoped(conn, org, "SELECT rs.id::text AS id, rs.status AS s, r.status AS rs, rs.approved_by FROM "
                                   "workflow_run_steps rs JOIN workflow_runs r ON r.id = rs.workflow_run_id "
                                   "WHERE rs.id = ANY($1::uuid[])", [str(t) for t in task_ids])
    return {r["id"]: HT._status_of(r["s"], r["rs"]) for r in rows}


async def task_approver(conn, org, task_id):
    return await scoped(conn, org, "SELECT approved_by FROM workflow_run_steps WHERE id = $1::uuid", str(task_id),
                        fetch="fetchval")


async def snap(conn, org, keys) -> dict:
    """Per item: the live row's decision columns, the archive count and its task's status — a before/after unit."""
    out = {}
    for k in keys:
        cid = S[k]
        r = await authority_row(conn, org, cid)
        arch = 0
        for t in AUTH_TABLES:
            arch += int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE archived_from_id = $1::uuid", cid,
                                     fetch="fetchval"))
        st = await task_status(conn, org, [S[f"task:{k}"]])
        out[k] = (r["status"] if r else None, str(r["approved_by"]) if r and r["approved_by"] else None,
                  r["decision_note"] if r else None, arch, st.get(str(S[f"task:{k}"])))
    return out


async def import_row(conn, org, iid):
    return await scoped(conn, org, "SELECT * FROM public.held_away_imports WHERE id = $1::uuid AND system_to IS NULL",
                        iid, fetch="fetchrow")


async def audit_actions(conn, org, resource_ids) -> list:
    return await scoped(conn, org, "SELECT action, user_id, resource_id::text AS rid, payload FROM audit_log WHERE "
                                   "org_id = $1::uuid AND resource_id = ANY($2::uuid[]) ORDER BY created_at",
                        org, [str(x) for x in resource_ids])


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_screen(conn):
    section("[Y] Screen: pending items, the server's per-item actions, greyed with reasons, filters, the cap")
    c, env = await api(U_C, "GET", "/api/v1/approvals/bulk?type=authority")
    rows = {r["id"]: r for r in env.get("rows") or []}
    S["env_C"] = env
    pending = ["D1", "D2", "D3", "D4", "D5", "D6", "TW_HH", "TW_ACC", "D7", "D9", "D10"]
    missing = [k for k in pending + ["D8"] if S[k] not in rows]
    check(bool(c == 200 and rows and not missing), "checker C: every pending fixture draft is listed", f"{c} "
          f"missing={missing} {dumps(env)[:200] if c != 200 else ''}")
    check(bool(S["DB1"] not in rows), "org B's draft is not listed for org A's checker", "")
    ok = [k for k in pending if (rows.get(S[k]) or {}).get("actions") == ["approve", "reject"]]
    check(bool(len(ok) == len(pending)), "C may approve or reject every open draft (actions from the server)",
          f"{sorted(set(pending) - set(ok))}")
    d8 = rows.get(S["D8"]) or {}
    check(bool(d8.get("actions") == [] and d8.get("blocked") == "not_pending" and "cancelled" in str(
        d8.get("blocked_reason"))), "the withdrawn draft is greyed: no longer pending (its task is cancelled)",
          dumps(d8))
    d2 = rows.get(S["D2"]) or {}
    check(bool(d2.get("account_masked") == S[f"mask:{AC['d2']}"] and "Managed" in str(d2.get("from_label"))
               and "Advise" in str(d2.get("to_label")) and d2.get("reason") == REASON
               and d2.get("maker_id") == str(U_M) and d2.get("maker_name") == "BA1V Maker"
               and d2.get("household_name") == NAME + "Household Accounts" and d2.get("requested_at")),
          "an account row: household, masked account, from → to, the reason, maker, requested at", dumps(d2))
    d1 = rows.get(S["D1"]) or {}
    check(bool(d1.get("subtype") == "household" and d1.get("account_masked") is None and "Advise" in
               str(d1.get("to_label"))), "the household row: the household's change, no account", dumps(d1))
    p, v = env.get("permissions") or {}, env.get("vocabularies") or {}
    check(bool(p.get("can_approve") is True and v.get("editable") == ["decision"] and v.get("inline_editable") == []
               and (env.get("limits") or {}).get("max_items") == 200),
          "C's envelope: can_approve, editable ['decision'], the cap 200", dumps({"p": p, "l": env.get("limits")}))
    hh = {x["key"] for x in v.get("households") or []}
    mk = {x["key"] for x in v.get("makers") or []}
    check(bool({str(HH1), str(HH2), str(HH3)} <= hh and str(U_M) in mk and v.get("ages") and v.get("subtypes")),
          "filter vocabularies (households, makers, types, ages) come from the server", f"{hh} {mk}")
    # the maker
    c, env_m = await api(U_M, "GET", "/api/v1/approvals/bulk?type=authority")
    S["env_M"] = env_m
    mrows = [r for r in env_m.get("rows") or [] if r["id"] in {S[k] for k in pending}]
    check(bool(c == 200 and len(mrows) == len(pending) and all(r["actions"] == [] and r["blocked"] == "maker"
                                                               and "You made this" in r["blocked_reason"]
                                                               for r in mrows)),
          "maker M sees their own items greyed: 'You made this'", f"{c} {dumps(mrows[:1])}")
    # no approval permission
    c, env_n = await api(U_N, "GET", "/api/v1/approvals/bulk?type=authority")
    S["env_N"] = env_n
    nrows = env_n.get("rows") or []
    check(bool(c == 200 and nrows and all(r["actions"] == [] and r["blocked"] == "permission"
                                          and "approve_authority" in r["blocked_reason"] for r in nrows)
               and (env_n.get("permissions") or {}).get("can_approve") is False
               and (env_n.get("vocabularies") or {}).get("editable") == []),
          "a viewer without approve_authority: every row greyed with the permission, editable [] (never omitted)",
          f"{c} {dumps(nrows[:1])}")
    # held-away: lines and totals
    c, env_h = await api(U_C, "GET", "/api/v1/approvals/bulk?type=held_away")
    S["env_H"] = env_h
    hrows = {r["id"]: r for r in env_h.get("rows") or []}
    e1 = hrows.get(S["E1"]) or {}
    check(bool(c == 200 and all(S[k] in hrows for k in ("E1", "E2", "E3", "TW_E", "E5"))
               and e1.get("actions") == ["approve", "reject"]), "held-away: every fixture entry listed, decidable by C",
          f"{c} {sorted(hrows)[:6]}")
    t = e1.get("totals") or {}
    pos = (e1.get("lines") or {}).get("positions") or []
    check(bool(t.get("market_value") == HELD_TOTAL and t.get("positions") == 2 and len(pos) == 2
               and {p_.get("label") for p_ in pos} == {r["name"] for r in HELD_ROWS}),
          "a held-away row carries its lines and totals (fixed-point)", dumps({"t": t, "pos": pos}))
    # summary for the entry points
    c, sm = await api(U_C, "GET", f"/api/v1/approvals/summary?household_id={HH3}")
    a = ((sm.get("types") or {}).get("authority") or {})
    h = ((sm.get("types") or {}).get("held_away") or {})
    check(bool(c == 200 and a.get("decidable") == 9 and h.get("decidable") == 5 and sm.get("link") ==
               "/portfolio/approvals"), "summary for the household: 9 decidable authority drafts (D2–D10 less the "
          "withdrawn D8, with the twin), 5 held-away", dumps(sm))
    c, smm = await api(U_M, "GET", f"/api/v1/approvals/summary?household_id={HH3}")
    check(bool(c == 200 and ((smm.get("types") or {}).get("authority") or {}).get("decidable") == 0),
          "the maker's summary: 0 decidable (no link for their own items)", dumps(smm))
    c, sacc = await api(U_C, "GET", f"/api/v1/approvals/summary?account_id={AC['d2']}")
    check(bool(c == 200 and ((sacc.get("types") or {}).get("authority") or {}).get("pending") == 1),
          "summary for one account: exactly its own draft", dumps(sacc))


async def y_client(conn):
    section("[Y] Client side: the REAL envelopes fed into the screen's own render logic")
    envs = {k: S.get(k) for k in ("env_C", "env_M", "env_N", "env_H")}
    if not all(envs.values()):
        check(False, "the envelopes from the screen section exist", f"{[k for k, v in envs.items() if not v]}")
        return
    js = f"""
import {{ register }} from "node:module";
register("./market/jsxLoader.mjs", import.meta.url);
const {{ createElement: h }} = await import("react");
const {{ renderToStaticMarkup }} = await import("react-dom/server");
const B = await import("../lib/bulkApprove.mjs");
const {{ BulkApproveView }} = await import("../components/approvals/BulkApprove.jsx");
const envs = {json.dumps(envs, default=str)};
const noop = () => {{}};
const out = {{}};
for (const [k, e] of Object.entries(envs)) {{
  const html = renderToStaticMarkup(h(BulkApproveView, {{ envelope: e, selected: [], filters: {{ age: "any" }},
    expanded: null, onToggle: noop, onSelectAll: noop, onClear: noop, onFilter: noop, onExpand: noop, onAsk: noop }}));
  out[k] = {{ canDecide: B.canDecide(e), selectAll: B.selectAll(e, B.filterRows(e, {{}})).length,
    rows: B.rowsOf(e).length, approveButton: /Review \\d+ selected →/.test(html),  // CATCHALL1 — the first button's wording
    checkboxes: (html.match(/type="checkbox"/g) || []).length,
    enabled: (html.match(/data-selectable="1"/g) || []).length,
    blocked: (html.match(/data-blocked="1"/g) || []).length,
    byHousehold: B.filterRows(e, {{ household: "{HH1}" }}).map((r) => r.id) }};
}}
console.log(JSON.stringify(out));
"""
    code, out = node_eval(js)
    try:
        res = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check(False, "the node render ran", out[-400:])
        return
    c_, m_, n_, h_ = res["env_C"], res["env_M"], res["env_N"], res["env_H"]
    check(bool(code == 0 and c_["canDecide"] and c_["approveButton"] and c_["selectAll"] == 11
               and c_["enabled"] == 11 and c_["blocked"] == 1),
          "C's real envelope: the approve control ('Review N selected →') renders; select all takes the 11 decidable drafts, never the withdrawn",
          f"{c_}")
    check(bool(c_["byHousehold"] == [S["D1"]]), "the household filter narrows to exactly that household's item",
          f"{c_['byHousehold']}")
    check(bool(m_["canDecide"] and m_["selectAll"] == 0 and m_["enabled"] == 0 and m_["blocked"] >= 11),
          "M's real envelope: nothing of their own is selectable; every own row says why", f"{m_}")
    check(bool(not n_["canDecide"] and not n_["approveButton"] and n_["checkboxes"] == 0 and n_["selectAll"] == 0
               and n_["rows"] > 0), "N's real envelope (no approve_authority): NO decision control renders", f"{n_}")
    check(bool(h_["canDecide"] and h_["selectAll"] == 5), "held-away envelope: the 5 entries selectable", f"{h_}")


async def y_maker(conn):
    section("[Y] Maker ≠ checker per item: M selecting their own items is refused per item; nothing changes")
    keys = ["D2", "D3", "D4"]
    before = await snap(conn, ORG_A, keys)
    a0 = await audit_actions(conn, ORG_A, [S[k] for k in keys])
    c, b = await bulk(U_M, "authority", [S[k] for k in keys])
    res = b.get("results") or []
    check(bool(c == 200 and len(res) == 3 and all(r["result"] == "refused" and r["code"] == 403 and
                                                  r["reason"].startswith("You made this") for r in res)),
          "authority: every one of M's own items refused, 'you made this'", f"{c} {dumps(b)}")
    after = await snap(conn, ORG_A, keys)
    a1 = await audit_actions(conn, ORG_A, [S[k] for k in keys])
    check(bool(before == after and all(v[0] == "draft" and v[4] == "open" for v in after.values())),
          "… nothing changed: each still a draft, its task still open, no archive", f"{before} → {after}")
    new = [r["action"] for r in a1[len(a0):]]
    check(bool(new and set(new) == {"approvals.bulk_item"}), "… the only new audit rows are the per-item refusals",
          f"{new}")
    hb = await import_row(conn, ORG_A, S["E1"])
    c, b = await bulk(U_M, "held_away", [S["E1"]])
    ha = await import_row(conn, ORG_A, S["E1"])
    r0 = (b.get("results") or [{}])[0]
    check(bool(c == 200 and r0.get("result") == "refused" and "You made this" in str(r0.get("reason"))
               and dict(hb) == dict(ha) and ha["status"] == "draft"),
          "held-away: M's own entry refused per item (entered_by); the import unchanged", f"{c} {dumps(b)}")


async def y_permission(conn):
    section("[Y] Permission: without the approval permission the WHOLE request is refused; the identical body is "
            "admitted for C")
    keys = ["D1", "D2", "D3", "D4", "D5"]
    S["batch5"] = [S[k] for k in keys]
    before = await snap(conn, ORG_A, keys)
    c, b = await bulk(U_N, "authority", S["batch5"])
    check(bool(c == 403 and "approve_authority" in str(b.get("detail"))), "a user with neither approve key: 403 "
          "naming the permission (a VALID body — only the gate refuses)", f"{c} {dumps(b)}")
    c2, b2 = await bulk(U_HAO, "authority", S["batch5"])
    check(bool(c2 == 403 and "approve_authority" in str(b2.get("detail"))), "approve_held_away alone does not decide "
          "authority: 403 naming approve_authority (checked per type)", f"{c2} {dumps(b2)}")
    after = await snap(conn, ORG_A, keys)
    check(bool(before == after), "… nothing changed for any of the 5", f"{before} → {after}")
    n_batch = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE action = 'approvals.bulk' AND user_id = "
                                        "ANY($1::uuid[])", [U_N, U_HAO], fetch="fetchval")
    check(n_batch == 0, "… and no batch audit row for a refused request", f"{n_batch}")


async def y_bulk5(conn):
    section("[Y] C bulk-approves 5 authority drafts in ONE request; == the single-approval result")
    keys = ["D1", "D2", "D3", "D4", "D5"]
    c, b = await bulk(U_C, "authority", S["batch5"])
    res = b.get("results") or []
    check(bool(c == 200 and [r["id"] for r in res] == [str(x) for x in S["batch5"]]
               and all(r["result"] == "approved" for r in res) and (b.get("counts") or {}).get("approved") == 5),
          "ONE request → 5 approved, in order", f"{c} {dumps(b)}")
    S["batch5_id"] = b.get("batch_id")
    after = await snap(conn, ORG_A, keys)
    check(bool(all(v[0] == "approved" and v[1] == str(U_C) and v[3] == 1 and v[4] == "completed"
                   for v in after.values())),
          "each: approved by C, archived once (system axis), its own task completed", f"{after}")
    appr = {k: str(await task_approver(conn, ORG_A, S[f"task:{k}"])) for k in keys}
    check(bool(set(appr.values()) == {str(U_C)} and len({S[f"task:{k}"] for k in keys}) == 5),
          "five distinct tasks, each closed by C", f"{appr}")
    aud = await audit_actions(conn, ORG_A, [S[k] for k in keys] + [S[f"task:{k}"] for k in keys])
    per = {k: sorted(r["action"] for r in aud if r["rid"] in (str(S[k]), str(S[f"task:{k}"]))
                     and str(r["user_id"]) == str(U_C)) for k in keys}
    want_hh = sorted(["authority.household_approved", "human_task.completed", "approvals.bulk_item"])
    want_acc = sorted(["authority.account_approved", "human_task.completed", "approvals.bulk_item"])
    check(bool(per["D1"] == want_hh and all(per[k] == want_acc for k in keys[1:])),
          "each item has its OWN audit rows (the single path's + one bulk_item)", f"{per}")
    batch = await scoped(conn, ORG_A, "SELECT user_id, payload FROM audit_log WHERE action = 'approvals.bulk' AND "
                                      "resource_id = $1::uuid", S["batch5_id"], fetch="fetch")
    pl = json.loads(batch[0]["payload"]) if batch and isinstance(batch[0]["payload"], str) else (
        batch[0]["payload"] if batch else {})
    check(bool(len(batch) == 1 and str(batch[0]["user_id"]) == str(U_C) and pl.get("requested") == 5
               and pl.get("item_ids") == [str(x) for x in S["batch5"]] and (pl.get("counts") or {}).get("approved")
               == 5), "ONE batch audit row: who, how many, which ids", dumps([dict(r) for r in batch]))
    items = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE action = 'approvals.bulk_item' AND "
                                      "payload->>'batch_id' = $1", S["batch5_id"], fetch="fetchval")
    check(items == 5, "… and one bulk_item row per item of that batch", f"{items}")
    # the single path, on identical twins
    c1, b1 = await api(U_C, "POST", f"/api/v1/authority/changes/{S['TW_ACC']}/approve", {})
    c2, b2 = await api(U_C, "POST", f"/api/v1/authority/changes/{S['TW_HH']}/approve", {})
    check(bool(c1 == 200 and c2 == 200), "the twins approved through the SINGLE routes", f"{c1} {c2} {dumps(b1)}")
    _, pa = await api(U_C, "GET", f"/api/v1/authority/accounts/{AC['d2']}")
    _, pt = await api(U_C, "GET", f"/api/v1/authority/accounts/{AC['twin']}")

    def strip(d):
        return {k: v for k, v in (d or {}).items() if not k.endswith("_id") and k not in ("id", "account_id")}

    ea, et = strip(pa.get("effective")), strip(pt.get("effective"))
    check(bool(ea and ea == et and ea.get("authority") == "advise" and ea.get("authority_source") == "account"),
          "the effective setting after a bulk approval == after a single approval (account)", f"{ea} vs {et}")
    keep = ("status", "authority_override", "custody", "effective_from", "reason", "decision_note", "approved_by",
            "superseded", "kind", "status_label")
    ha_, ht_ = (pa.get("history") or [{}])[0], (pt.get("history") or [{}])[0]
    check(bool({k: ha_.get(k) for k in keep} == {k: ht_.get(k) for k in keep}),
          "… and the decided change rows match field for field", f"{ha_} vs {ht_}")
    _, qa = await api(U_C, "GET", f"/api/v1/authority/households/{HH1}")
    _, qt = await api(U_C, "GET", f"/api/v1/authority/households/{HH2}")
    ca, ct = (qa.get("current") or {}), (qt.get("current") or {})
    check(bool(ca.get("authority") == ct.get("authority") == "advise" and ca.get("effective_from") ==
               ct.get("effective_from")), "the effective setting after a bulk approval == single (household)",
          f"{ca} vs {ct}")
    tw = await audit_actions(conn, ORG_A, [S["TW_ACC"], S["task:TW_ACC"]])
    tw_actions = sorted(r["action"] for r in tw if str(r["user_id"]) == str(U_C))
    check(bool(tw_actions == sorted(a for a in want_acc if a != "approvals.bulk_item")),
          "the single path wrote the same audit rows, less the bulk_item", f"{tw_actions}")


async def y_mixed(conn):
    section("[Y] Mixed batch: one already approved + one withdrawn refused with reasons; the rest approved")
    before = await snap(conn, ORG_A, ["D5", "D8"])
    c, b = await bulk(U_C, "authority", [S["D5"], S["D8"], S["D7"]])
    r = {x["id"]: x for x in b.get("results") or []}
    r5, r8, r7 = r.get(str(S["D5"]), {}), r.get(str(S["D8"]), {}), r.get(str(S["D7"]), {})
    check(bool(c == 200 and r5.get("result") == "refused" and r5.get("code") == 409 and "approved" in
               str(r5.get("reason"))), "the already-approved item: refused, 'no longer pending — … approved'", dumps(r5))
    check(bool(r8.get("result") == "refused" and r8.get("code") == 409 and "cancelled" in str(r8.get("reason"))),
          "the withdrawn item: refused, its task is cancelled", dumps(r8))
    check(bool(r7.get("result") == "approved"), "the pending item in the same batch: approved", dumps(r7))
    after = await snap(conn, ORG_A, ["D5", "D8", "D7"])
    check(bool(after["D5"] == before["D5"] and after["D8"] == before["D8"] and after["D7"][0] == "approved"
               and after["D7"][1] == str(U_C)), "… the refused two are unchanged; D7 approved by C",
          f"{before} → {after}")


async def y_reject(conn):
    section("[Y] Reject selected requires a note; a rejected draft changes nothing")
    _, pre = await api(U_C, "GET", f"/api/v1/authority/accounts/{AC['d6']}")
    before = await snap(conn, ORG_A, ["D6"])
    c0, b0 = await bulk(U_C, "authority", [S["D6"]], "reject")
    c1, b1 = await bulk(U_C, "authority", [S["D6"]], "reject", "   ")
    check(bool(c0 == 422 and c1 == 422 and "note" in str(b1.get("detail"))), "reject without a note (or blank): 422",
          f"{c0} {c1} {dumps(b1)}")
    check(bool(await snap(conn, ORG_A, ["D6"]) == before), "… nothing changed", "")
    c, b = await bulk(U_C, "authority", [S["D6"]], "reject", "BA1V wrong account")
    r = (b.get("results") or [{}])[0]
    after = await snap(conn, ORG_A, ["D6"])
    check(bool(c == 200 and r.get("result") == "rejected" and after["D6"][0] == "rejected" and after["D6"][1] ==
               str(U_C) and after["D6"][2] == "BA1V wrong account" and after["D6"][4] == "completed"),
          "with a note: rejected by C, the note recorded, its task completed", f"{c} {dumps(b)} {after}")
    _, post = await api(U_C, "GET", f"/api/v1/authority/accounts/{AC['d6']}")
    check(bool(pre.get("effective") and pre.get("effective") == post.get("effective")
               and (post.get("effective") or {}).get("authority_source") == "default"),
          "the rejected draft changed no setting: the account's effective authority is exactly as before",
          f"{pre.get('effective')} → {post.get('effective')}")


def _pos_key(r) -> tuple:
    return (r["name"], r["asset_type"], r["q"], r["mv"], r["cost"], r["authority"], r["source_system"],
            r["as_of_date"], r["taxonomy_key"], str(r["owner_entity_id"]))


async def positions_of(conn, acct) -> list[tuple]:
    rows = await scoped(conn, ORG_A, "SELECT a.name, a.asset_type, p.quantity::text AS q, p.market_value::text AS mv, "
                                     "p.cost_basis::text AS cost, p.authority, p.source_system, p.as_of_date, "
                                     "p.taxonomy_key, p.owner_entity_id FROM portfolio.positions p JOIN "
                                     "portfolio.assets a ON a.id = p.asset_id WHERE p.account_id = $1::uuid AND "
                                     "p.valid_to IS NULL AND p.system_to IS NULL ORDER BY a.name", acct)
    return [_pos_key(r) for r in rows]


async def records_of(conn, iid) -> list:
    rows = await scoped(conn, ORG_A, "SELECT record_type, count(*) AS n FROM public.held_away_records WHERE import_id "
                                     "= $1::uuid AND valid_to IS NULL AND system_to IS NULL GROUP BY 1 ORDER BY 1", iid)
    return [(r["record_type"], r["n"]) for r in rows]


async def y_held(conn):
    section("[Y] Held-away: bulk approval creates the same positions as single approval (row-for-row)")
    for k in ("e1", "e2", "e3", "twin"):
        check(bool(await positions_of(conn, HAC[k]) == []), f"no live position on {k} before", "")
    c1, b1 = await api(U_C, "POST", f"/api/v1/held-away/imports/{S['TW_E']}/approve", {})
    check(bool(c1 == 200 and b1.get("status") == "approved"), "the twin entry approved through the SINGLE route",
          f"{c1} {dumps(b1)}")
    c, b = await bulk(U_C, "held_away", [S["E1"], S["E2"], S["E3"]])
    res = b.get("results") or []
    check(bool(c == 200 and len(res) == 3 and all(r["result"] == "approved" for r in res)),
          "ONE request → 3 held-away entries approved", f"{c} {dumps(b)}")
    twin = await positions_of(conn, HAC["twin"])
    check(bool(len(twin) == len(HELD_ROWS)), "the single approval made one position per line", f"{twin}")
    for k in ("e1", "e2", "e3"):
        got = await positions_of(conn, HAC[k])
        check(bool(got and got == twin), f"bulk-approved {k.upper()}: positions == the single approval's, row for row",
              f"{got} vs {twin}")
    rt = await records_of(conn, S["TW_E"])
    for k in ("E1", "E2", "E3"):
        imp = await import_row(conn, ORG_A, S[k])
        rk = await records_of(conn, S[k])
        aud = await audit_actions(conn, ORG_A, [S[k], S[f"task:{k}"]])
        acts = sorted(r["action"] for r in aud if str(r["user_id"]) == str(U_C))
        check(bool(imp and imp["status"] == "approved" and str(imp["approved_by"]) == str(U_C) and rk == rt and rt
                   and acts == sorted(["held_away.import_approved", "human_task.completed", "approvals.bulk_item"])),
              f"{k}: approved by C, the same provenance records as the twin, its own audit rows",
              f"{dict(imp) if imp else None} {rk} vs {rt} {acts}")


async def y_cap_badid(conn):
    section("[Y] Cap enforced; one bad id does not stop the others")
    n0 = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE action = 'approvals.bulk'", fetch="fetchval")
    over = [str(uuid.uuid4()) for _ in range(BA.MAX_ITEMS + 1)]
    c, b = await bulk(U_C, "authority", over)
    n1 = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE action = 'approvals.bulk'", fetch="fetchval")
    check(bool(c == 422 and n1 == n0), f"{BA.MAX_ITEMS + 1} ids: 422, nothing decided, no batch row", f"{c} {n0}→{n1}")
    at = [str(uuid.uuid4()) for _ in range(BA.MAX_ITEMS)]
    c, b = await bulk(U_C, "authority", at)
    res = b.get("results") or []
    check(bool(c == 200 and len(res) == BA.MAX_ITEMS and all(r["result"] == "refused" and r["code"] == 404
                                                             for r in res)),
          f"exactly {BA.MAX_ITEMS} ids: admitted, each refused 'not found' on its own line", f"{c} {dumps(res[:2])}")
    before = await snap(conn, ORG_A, ["D9"])
    c, b = await bulk(U_C, "authority", ["not-a-uuid", str(MISSING), S["D9"], S["D9"]])
    res = b.get("results") or []
    by = [(r["id"], r["result"], r["code"]) for r in res]
    check(bool(c == 200 and len(res) == 3 and by[0] == ("not-a-uuid", "refused", 422) and by[1] == (str(MISSING),
                                                                                                    "refused", 404)
               and by[2] == (str(S["D9"]), "approved", 200)),
          "a malformed id and a missing id are refused on their own lines; the good item (listed twice) approved once",
          f"{c} {by}")
    after = await snap(conn, ORG_A, ["D9"])
    check(bool(before["D9"][0] == "draft" and after["D9"][0] == "approved" and after["D9"][3] == 1),
          "… D9 decided exactly once", f"{before} → {after}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    q = "SELECT count(*) FROM public.account_authority_settings WHERE org_id = $1"
    a_a = await scoped(conn, ORG_A, q, ORG_A, fetch="fetchval")
    a_b = await scoped(conn, ORG_A, q, ORG_B, fetch="fetchval")
    b_b = await scoped(conn, ORG_B, q, ORG_B, fetch="fetchval")
    b_a = await scoped(conn, ORG_B, q, ORG_A, fetch="fetchval")
    check(bool(a_a > 0 and b_b > 0 and a_b == 0 and b_a == 0), "RLS: each org reads its own authority rows, none of "
          "the other's", f"A:{a_a}/{a_b} B:{b_b}/{b_a}")
    c, b = await bulk(U_C, "authority", [S["DB1"]])
    r0 = (b.get("results") or [{}])[0]
    row = await authority_row(conn, ORG_B, S["DB1"])
    check(bool(c == 200 and r0.get("result") == "refused" and r0.get("code") == 404 and row and row["status"] ==
               "draft"), "org B's draft in org A's request: refused as not found; it stays a draft", f"{c} {dumps(b)}")
    c, b = await bulk(U_CB, "authority", [S["D10"]])
    r0 = (b.get("results") or [{}])[0]
    row = await authority_row(conn, ORG_A, S["D10"])
    check(bool(c == 200 and r0.get("result") == "refused" and r0.get("code") == 404 and row and row["status"] ==
               "draft"), "org A's draft in org B's request: refused as not found; it stays a draft", f"{c} {dumps(b)}")
    c, b = await bulk(U_C, "held_away", [S["EB1"]])
    r0 = (b.get("results") or [{}])[0]
    c2, b2 = await bulk(U_CB, "held_away", [S["E5"]])
    r1 = (b2.get("results") or [{}])[0]
    e5 = await import_row(conn, ORG_A, S["E5"])
    eb = await import_row(conn, ORG_B, S["EB1"])
    check(bool(r0.get("code") == 404 and r1.get("code") == 404 and e5["status"] == "draft" and eb["status"] ==
               "draft"), "held-away both directions: refused as not found; both still drafts", f"{dumps(b)} {dumps(b2)}")
    c, b = await bulk(U_CB, "authority", [S["DB1"]])
    r0 = (b.get("results") or [{}])[0]
    check(bool(c == 200 and r0.get("result") == "approved"), "positive control: org B's own checker approves DB1",
          f"{c} {dumps(b)}")
    c, env = await api(U_CB, "GET", "/api/v1/approvals/bulk?type=authority")
    ids = {x["id"] for x in env.get("rows") or []}
    check(bool(c == 200 and not ids & {str(S[k]) for k in ("D10", "D7", "D2")}), "org B's list holds no org A item",
          f"{c} {len(ids)}")


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
    code2, out2, _ = run_cmd(["node", "--test", "tests/bulkapprove1/"], WEB_DIR, timeout=300)
    check(bool(code2 == 0 and re.search(r"^# fail 0", out2, re.M)), "the sprint's node tests (render, fail-closed, "
          "filters, select all, confirmation, entry points, menu) pass", out2[-200:] if code2 else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep7b.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep7b.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_rep7b.py: 0 FAIL (its OWN final TOTAL line)",
          f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}")


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
    if VS.CLEANUP_FLAG in sys.argv:
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
        section("[Y] Task 1 — findings 1a–1c")
        await run("task1", self_task1, check, find)
        await run("make", y_make, conn)
        if all(S.get(k) for k in ("D1", "D2", "D5", "D6", "D7", "D8", "D9", "D10", "TW_ACC", "TW_HH", "E1", "E5",
                                  "TW_E", "DB1", "EB1")):
            await run("screen", y_screen, conn)
            await run("client", y_client, conn)
            await run("maker", y_maker, conn)
            await run("permission", y_permission, conn)
            await run("bulk5", y_bulk5, conn)
            await run("mixed", y_mixed, conn)
            await run("reject", y_reject, conn)
            await run("held-away", y_held, conn)
            await run("cap / bad id", y_cap_badid, conn)
            await run("isolation", y_isolation, conn)
        else:
            check(False, "the fixture items were created — dependent sections not run",
                  f"{ {k: v for k, v in S.items() if not k.startswith('mask:')} }")
        section("[Y] Static — the one-path rule, docs, regression set, front-end markers")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; demo + real rows unchanged")
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
                for t in pre:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                now = await real_prints(conn)
                for t in REAL_FINGERPRINTED:
                    check(now[t] == real[t], f"real {t} unchanged (count + checksum by primary key)",
                          f"{real[t][0]} → {now[t][0]} rows (a concurrent HollisMuse write would also show here)")
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
    try:
        rc = asyncio.run(main_async())
    except BaseException as exc:  # noqa: BLE001 — S30: TOTAL on every exit path, the traceback printed
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise
        import traceback as _tb

        _tb.print_exc()
        check(False, "the verify crashed", f"{type(exc).__name__}: {exc}")
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        rc = 1
    sys.exit(rc)
