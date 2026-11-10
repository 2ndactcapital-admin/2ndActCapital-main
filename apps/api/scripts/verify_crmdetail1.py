"""verify_crmdetail1 — the CRM holds a multigenerational family: governed relationship types + the Relationships panel,
typed detail fields, people fields, portfolio.liabilities netting into net worth, permanent demo data, the 1e table.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``) before
``main`` is imported. No network: the Auth0 profile fetch and R2 are replaced by local stubs (each matches the CURRENT
signature it replaces).

Everything after the bearer token is the real app (middleware, Principal, every gate, every handler) against the live
database, in TWO fixture orgs (99000000-…-000000530001 / 530002), ``is_test = true`` (asserted), as are the fixture
users. Teardown at start and end: the shared scoped cleanup (_verify_cleanup) on the operator DSN, by fixture org /
user ids — entities, relationships, attributes, addresses, notes, liabilities, households, holdings: every org_id table
— with ``demo_fixture_orgs`` = the fixture orgs (the demo-tagged fixture is removed by the documented path only);
every counted table is re-read for its exact pre-test count and the REAL rows of entities / relationships / attributes
/ households / liabilities are fingerprinted (count + md5 by the catalog primary key) before and after.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
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
import _verify_scope as VSC  # noqa: E402  (stdlib only) — live-written tables counted by fixture ids

SPRINT_KEY = "crmdetail1"
BEFORE_REF = "17ab37e"   # crmdetail1-work base: main after "merge secmaster1-work"

IM = ("internal_perm", ("manage_members",))
NEW_ROUTES = {
    ("GET", "/api/v1/crm/vocabularies"): ("internal", ()),
    ("GET", "/api/v1/entities/{entity_id}/crm-relationships"): ("internal", ()),
    ("POST", "/api/v1/entities/{entity_id}/crm-relationships"): IM,
    ("POST", "/api/v1/crm-relationships/{rel_id}/end"): IM,
    ("GET", "/api/v1/entities/{entity_id}/details"): ("internal", ()),
    ("PUT", "/api/v1/entities/{entity_id}/details"): IM,
    ("GET", "/api/v1/entities/{entity_id}/liabilities"): ("entity", ()),
    ("POST", "/api/v1/entities/{entity_id}/liabilities"): IM,
    ("PATCH", "/api/v1/liabilities/{liability_id}"): IM,
    ("POST", "/api/v1/liabilities/{liability_id}/end"): IM,
    ("GET", "/api/v1/entities/{entity_id}/net-worth"): ("entity", ()),
}
REQUIRED_TYPES = ("spouse", "former_spouse", "partner", "parent_of", "sibling", "grantor", "trustee",
                  "successor_trustee", "co_trustee", "trust_protector", "distribution_committee", "beneficiary",
                  "income_beneficiary", "remainder_beneficiary", "annuitant", "managing_member", "sole_member",
                  "manager", "general_partner", "limited_partner", "investment_committee", "board_chair",
                  "board_member", "officer", "donor_advisor", "sponsor", "owner", "advisor", "ownership")
TRUST_TYPES = ("revocable", "irrevocable", "dynasty", "slat", "grat", "bypass", "2503c", "ilit", "crut", "crat", "clat",
               "idgt", "qprt", "other")
GAPS = ("GAP-CRM-01", "GAP-CRM-02", "GAP-CRM-03", "GAP-CRM-04", "GAP-CRM-05", "GAP-CRM-06", "GAP-PORT-01",
        "GAP-PORT-02", "GAP-PORT-03", "GAP-PORT-04", "GAP-PORT-05")
MUSE_DOC = "docs/demo/olivo/HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md"

# ── Task 1 — written by hand from the discovery; each needle must be present at the sprint's end. ───────────────────
TASK1 = [
    ("apps/web/components/crm/EntityDetailTabs.jsx", "{ key: \"relationships\", label: \"Relationships\" }",
     "1a: the record gains a Relationships tab (relationships were only on the hierarchy side rail before)"),
    ("apps/web/components/crm/tabs/AddressesTab.jsx", "season_from_month",
     "1a: the address form already offered seasonal months"),
    ("apps/web/components/crm/EntityDetailsForm.jsx", "name=\"tags\"", "1a: tags are on the Details form"),
    ("apps/api/services/ownership_read.py", "r.relationship_type = 'ownership'", "1b: ownership_read filters ownership"),
    ("apps/api/services/portfolio_rollup.py", "er.relationship_type = 'ownership'", "1b: rollup weights filter ownership"),
    ("apps/api/services/entity_graph.py", "er.relationship_type = 'beneficiary'",
     "1b: beneficiary already confers member visibility (kept)"),
    ("apps/api/routers/entity_graph.py", "AND relationship_type = 'ownership'",
     "1b: the ownership-sum warning now sums ownership only"),
    ("apps/api/services/households.py", "async def _net_worth(", "1c: net worth subtracts liabilities at the one sum"),
    ("apps/api/services/liabilities.py", "_ownership_weights", "1c: liabilities on the asset rollup's own weights"),
    ("apps/api/scripts/_verify_cleanup.py", "async def _demo_check(", "1f/6: the shared cleanup's demo guard"),
    ("apps/api/scripts/perms1_remove_muse_rows.py", "bad += await demo_refusals(conn, plan)", "1f/6: removal guard"),
    ("apps/api/scripts/bugfix2_remove_muse_rows.py", "bad += await demo_refusals(conn, plan)", "1f/6: removal guard"),
]
FINDINGS = [
    "1a: a person's 'Date of Birth' is inception_date and 'Date of Death' wrote end_date; Country / Region is a legal "
    "entity's state of formation (region_code) — the detail fields show it, the Details form edits it.",
    "1a: relationships were entered only on the hierarchy side rail (ownership / beneficiary, free text accepted, "
    "require_staff: the org admin — Muse's maker — was refused); the new panel is internal + manage_members.",
    "1b: beneficiary is read by member visibility and the ownership tree's default edges — the governed type keeps it.",
    "1d: direct assets / valuations / commitments have no entry route for the org admin (manage_portfolio not granted, "
    "record_valuation and create_commitment service-only); held-away manual entry needs a security in the master; an "
    "integrated account has no manual path — named gaps GAP-PORT-01..05, nothing built.",
    "1f: ~50 older verify scripts delete by name pattern; only the regression set uses _verify_cleanup — the database "
    "guard is the backstop for the rest.",
    "F (ownership %) keeps require_staff (verify_staffroles1 asserts it): GAP-CRM-05.",
]


def _key(rec: dict) -> tuple:
    return (rec["methods"][0] if rec.get("methods") else "", rec["path"])


def _rule(rec: dict) -> tuple:
    e = rec.get("expect") or {}
    return (e.get("rule"), tuple(sorted(e.get("any_of") or [])))


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def self_task1(check, find):
    bad = []
    for rel, needle, what in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or ""
        idx = txt.find(needle)
        print(f"      {rel}:{txt.count(chr(10), 0, idx) + 1 if idx >= 0 else '—'}  {what}")
        if idx < 0:
            bad.append((rel, needle[:40]))
    check(not bad, "Task 1: every discovery anchor is present at the sprint's end", f"{bad}")
    for f in FINDINGS:
        find(f"Task 1 — {f}")


def _covers(row_cell: str, letter: str, step: str) -> bool:
    cell = row_cell.strip()
    if not cell.startswith(letter):
        return False
    if re.match(rf"^{letter}(\s|$)", cell):
        return True                                   # a bare letter row covers the whole phase
    nums = re.findall(rf"{letter}\.(\d+)(?:\.\d+)?(?:[–-]{letter}\.(\d+))?", cell)
    words = re.findall(rf"{letter}\.([A-Za-z]+)", cell)
    m = re.match(rf"^{letter}\.(\d+)", step)
    if m:
        n = int(m.group(1))
        return any((int(a) <= n <= int(b)) if b else n == int(a) for a, b in nums)
    return step.split(".", 1)[1] in words


def self_table(check, find):
    status = VS.own_text(SPRINT_KEY, "docs/PROJECT_STATUS.md") or ""
    start = status.find("| Steps | Today | After CRMDETAIL1 | Note |")
    end = status.find("**Named gaps", start)
    rows = [ln.split("|")[1] for ln in status[start:end].splitlines()[2:] if ln.startswith("|")] if start >= 0 else []
    doc = _read(MUSE_DOC)
    steps = sorted(set(re.findall(r"\*\*([A-O]\.(?:\d+|check|SPV))\*\*|^\| ([A-O]\.\d+(?:\.\d+)?) \|", doc, re.M)))
    ids = sorted({a or b for a, b in steps} | {f"{x}.check" for x in "F"} | {"N", "O.1"})
    uncovered = [s for s in ids if s not in ("N",) and not any(_covers(r, s[0], s) for r in rows)]
    if "N" in ids and not any(r.strip().startswith("N") for r in rows):
        uncovered.append("N")
    check(bool(rows) and not uncovered, f"1e: every Muse step ID ({len(ids)}) is covered by a row of the 1e table",
          f"uncovered {uncovered[:12]}")
    unlabelled = [r for r in (status[start:end].splitlines()[2:] if start >= 0 else [])
                  if r.startswith("|") and not re.search(r"supported|GAP-|refused|missing", r)]
    check(not unlabelled, "1e: every row says supported / refused / missing / a named GAP", f"{unlabelled[:3]}")
    check(all(g in status[end:end + 6000] for g in GAPS), "Task 7: every named gap is recorded in PROJECT_STATUS",
          f"{[g for g in GAPS if g not in status[end:end + 6000]]}")
    check("integrated" in status[end:end + 6000] and "Options:" in status[end:end + 6000],
          "Task 7: positions on an integrated account with no feed — options recorded, nothing built")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), "the sprint added exactly its eleven routes", f"{own.where()}; extra "
          f"{sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = []
    for k, (kind, perms) in NEW_ROUTES.items():
        rec = new.get(k)
        after = (rec or {}).get("after", "")
        ok = rec is not None and (
            (kind == "internal" and after == "internal") or
            (kind == "internal_perm" and after == "internal + any(manage_members)") or
            (kind == "entity" and after == "entity access"))
        if not ok:
            wrong.append((k, after))
    check(not wrong, "every new route carries its gate (internal / internal + manage_members / entity access)",
          f"{wrong}")
    check(not own.changed, "no existing route's gate changed (POST/PATCH /entity-relationships keep require_staff)",
          f"{[(_key(own.end[k]), own.end[k].get('after')) for k in own.changed]}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_static(check, find):
    vocab = VS.own_text(SPRINT_KEY, "apps/api/services/crm_vocab.py") or ""
    missing = [t for t in REQUIRED_TYPES if f'_t("{t}"' not in vocab]
    check(not missing, "Task 2: the vocabulary carries every required type (ownership unchanged, panel=False)",
          f"{missing}")
    check(all(f'("{t}"' in vocab for t in TRUST_TYPES), "Task 3: every trust_type choice")
    sql = _read("docs/crmdetail1_part1.sql")
    check(bool("CREATE TABLE IF NOT EXISTS portfolio.liabilities" in sql and "crm_demo_delete_guard" in sql
               and "ADD COLUMN IF NOT EXISTS date_of_death" in sql and "BEGIN;" not in sql),
          "Task 5: docs/crmdetail1_part1.sql saved (liabilities, people columns, demo guard)")
    check(bool((HERE / "_apply_crmdetail1.py").exists()), "Task 5: scripts/_apply_crmdetail1.py exists")
    check("verify_crmdetail1.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    demo = _read("docs/demo/README.md")
    olivo = _read("docs/demo/olivo/README.md")
    check(bool("demo-olivo" in demo and "HollisMuse" in demo and "protection rule" in demo.lower()
               and "Protection rule (crmdetail1)" in olivo
               # PORTENTRY1: a0755ee moved the Olivo README to the v2 entry plan (v1 is still in the folder).
               # PORTENTRY2: ad97ea3 moved it to v3 — any version of the entry plan is the rule (VHK2 rule 6).
               and re.search(r"HOLLISMUSE_OLIVO_DATA_ENTRY_v\d+\.md", olivo) is not None),
          "Task 6: docs/demo/README.md index + the protection rule added to the Olivo README (kept)")
    tabs = _read("apps/web/components/crm/EntityDetailTabs.jsx")
    check(bool("RelationshipsPanel" in tabs and "LiabilitiesTab" in tabs and "DetailFieldsPanel" in tabs),
          "the record shows the Relationships panel, the Detail fields and the Liabilities tab")
    hh = _read("apps/web/components/households/HouseholdManager.jsx")
    check(bool("NetWorthLines" in hh and "detail.liabilities" in hh), "the household shows net worth + Liabilities")
    for f in ("RelationshipsPanel.jsx", "DetailFieldsPanel.jsx", "LiabilitiesTab.jsx"):
        src = _read(f"apps/web/components/crm/{f}")
        hard = [t for t in ("Trustee of", "Mortgage", "Dynasty", "Grantor") if f'"{t}"' in src or f">{t}<" in src]
        check(bool(src and not hard and "canWrite(env)" in src), f"{f}: no hard-coded vocabulary label; writes "
              "behind canWrite(env)", f"{hard}")
    check(bool((WEB_DIR / "tests" / "crmdetail1" / "crmdetail1.test.mjs").exists()), "node tests for the helpers")


def self_checks(check, find):
    self_task1(check, find)
    self_table(check, find)
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
TAG = "crmdetail1verify"
NAME = "CD1V "            # reserved fixture name prefix (asserted to match no real row)
DEMO_TAG = "demo-crmdetail1verify"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000053{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)

# Role grants written by hand (live 2026-11-04).
ORGADMIN_PERMS = frozenset({
    "approve_authority", "approve_blocks", "approve_execution", "approve_held_away", "approve_models",
    "approve_proposals", "approve_replication", "approve_tax", "author_workflows", "configure_workflow_triggers",
    "manage_authority", "manage_blocks", "manage_deals", "manage_execution", "manage_held_away",
    "manage_hypothetical_policy", "manage_members", "manage_models", "manage_org_settings", "manage_proposals",
    "manage_replication", "manage_tax", "review_agent_proposals", "view_authority", "view_blocks", "view_dashboard",
    "view_deals", "view_execution", "view_held_away", "view_members", "view_models", "view_portfolio",
    "view_proposals", "view_replication", "view_tax", "view_workflow_runs"})
SUPPORT_PERMS = frozenset({"review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
                           "view_deals", "view_members", "view_portfolio", "view_workflow_runs"})

U_SUPER, U_OA, U_SUPPORT, U_MEMBER, U_OTHER = _id(10), _id(11), _id(12), _id(13), _id(14)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPPORT: (ORG_A, "member", "support", "support_staff", SUPPORT_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OTHER: (ORG_B, "member", "other", "member", frozenset(C.MEMBER_PERMS)),
}
# entities: (id, org, type, name)
E = {
    "dom": (_id(30), ORG_A, "individual", "Dominic"), "ant": (_id(31), ORG_A, "individual", "Anthony"),
    "eli": (_id(32), ORG_A, "individual", "Elena"), "kid": (_id(33), ORG_A, "individual", "Minor Kid"),
    "trust": (_id(34), ORG_A, "trust", "Dynasty Trust"), "found": (_id(35), ORG_A, "foundation", "Foundation"),
    "daf": (_id(36), ORG_A, "foundation", "Giving Fund"), "llc": (_id(37), ORG_A, "llc", "Round Hill LLC"),
    "lp": (_id(38), ORG_A, "lp", "Holdings LP"), "gp": (_id(39), ORG_A, "gp", "OH GP LLC"),
    "fo": (_id(40), ORG_A, "family_office", "Family Office"), "bank": (_id(41), ORG_A, "corporation", "Coastal Bank"),
    "mem": (_id(42), ORG_A, "individual", "Member Self"), "memllc": (_id(43), ORG_A, "llc", "Member LLC"),
    "other_fam": (_id(44), ORG_A, "individual", "Other Family"), "orgb": (_id(45), ORG_B, "individual", "Org B Person"),
    "demo": (_id(46), ORG_A, "individual", "Demo Fixture"), "muse": (_id(47), ORG_A, "individual", "MUSE fixture"),
    "muse2": (_id(48), ORG_A, "individual", "MUSE fixture two"),
}
HH_A = _id(60)
KID_DOB = None            # set at seed time (11 years before today)

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.entity_attributes", "public.entity_addresses", "public.entity_notes", "public.households",
           "public.household_memberships", "portfolio.liabilities", "public.entity_holdings", "public.audit_log",
           "public.delegate_grants")
REAL_FINGERPRINTED = ("public.entities", "public.entity_relationships", "public.entity_attributes",
                      "public.households", "portfolio.liabilities", "public.organizations")

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


def eid(k: str) -> UUID:
    return E[k][0]


def dumps(x) -> str:
    """For DISPLAY in a check's detail only — truncated at 400 characters."""
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


def body_text(x) -> str:
    """The FULL serialised body, for every containment assertion (never dumps())."""
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
    return {t: int(await platform(conn, VSC.count_sql(t, FIXTURE_ORGS, list(USERS)), fetch="fetchval")) for t in COUNTED}


async def pk_of(conn, table: str) -> list[str]:
    rows = await platform(conn, "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' "
                                "ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str) -> tuple[int, str]:
    """Count + md5 of every row, keyed by the table's real primary key (no activity column is excluded: none of these
    tables is written by real sign-in activity)."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t", fetch="fetchrow")
    return int(row["n"]), row["h"]


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only — fixture orgs and users, by id (every org_id table: entities, relationships, attributes,
    addresses, notes, liabilities, households, holdings, audit rows). The demo-tagged fixture lives in an is_test
    fixture org and is removed by the documented path (demo_fixture_orgs → crm.demo_teardown = 'fixture')."""
    users = list(USERS)
    op = await operator_conn()
    try:
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH_A, _id(61)]]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               label="verify_crmdetail1", demo_fixture_orgs=FIXTURE_ORGS)
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])",
             [[HH_A, _id(61)]])])
    finally:
        await op.close()


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


# ═══ Catalog + seed ═════════════════════════════════════════════════════════════════════════════════════════════
async def y_catalog(conn):
    section("[Y] Constraints read from the catalog before seeding; reserved fixture prefixes match no real row")
    chk = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = "
              "'portfolio.liabilities'::regclass AND contype IN ('c', 'f', 'u', 'p')")}
    check(bool("liabilities_kind_chk" in chk and "mortgage" in chk["liabilities_kind_chk"]
               and "liabilities_balance_chk" in chk), "liabilities CHECKs (kind, balance) present", f"{sorted(chk)}")
    fks = [d for d in chk.values() if d.startswith("FOREIGN KEY (owner_entity_id)")]
    check(len(fks) == 1, "liabilities.owner_entity_id is an FK to entities", f"{fks}")
    idx = await platform(conn, "SELECT indexdef FROM pg_indexes WHERE indexname = 'entity_rel_active_unique'",
                         fetch="fetchval")
    check(bool(idx and "UNIQUE" in idx and "valid_to IS NULL" in idx), "entity_rel_active_unique (from, to, type) "
          "live-only — ending frees the pair", f"{idx}")
    trg = await platform(conn, "SELECT count(*) FROM pg_trigger WHERE tgname = 'crm_demo_delete_guard_trg'",
                         fetch="fetchval")
    check(bool(trg >= 9), "the demo delete guard is installed on the CRM tables", f"{trg}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND NOT (id = "
                                "ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 AND NOT "
                                "(id = ANY($4::uuid[]))) + (SELECT count(*) FROM entities WHERE $6 = ANY(tags) AND NOT "
                                "(org_id = ANY($4::uuid[])))", NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS,
                          list(USERS), DEMO_TAG, fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes / tag match no real row", f"{real}")


async def seed(conn) -> None:
    global KID_DOB
    from services import rbac

    t = date.today()
    KID_DOB = date(t.year - 11, 1, 15)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CRMDETAIL1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        roles = {}
        for uid, (org, _a, _n, rname, perms) in USERS.items():
            if (org, rname) not in roles:
                rid = await rbac.ensure_role(conn, org, rname, f"CRMDETAIL1 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"CRMDETAIL1 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for k, (i, org, etype, name) in E.items():
            tags = [DEMO_TAG] if k == "demo" else []
            dname = ("MUSE – " + name) if k.startswith("muse") else NAME + name
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, tags, inception_date) "
                               "VALUES ($1, $2, $3::entity_type, $4, $5, $6)", i, org, etype, dname, tags,
                               KID_DOB if k == "kid" else None)
        # ownership fixture: trust 60% of the LLC; the member owns 100% of their LLC
        await conn.execute("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
                           "ownership_pct) VALUES ($1, $2, $3, 'ownership', 60), ($1, $4, $5, 'ownership', 100)",
                           ORG_A, eid("trust"), eid("llc"), eid("mem"), eid("memllc"))
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, eid("mem"), U_MEMBER)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_B, eid("orgb"), U_OTHER)
        # gross assets: look-through holdings buckets as the rollup writes them (fixture amounts, exact)
        for k, amt in (("llc", "14500000.00"), ("trust", "8700000.00"), ("dom", "2500000.55"), ("ant", "1000000.00")):
            await conn.execute("INSERT INTO entity_holdings (org_id, entity_id, taxonomy_key, market_value, "
                               "currency_code, as_of_date, source) VALUES ($1, $2, 'taxonomy_sc_1', $3, 'USD', $4, "
                               "'portfolio')", ORG_A, eid(k), Decimal(amt), date.today())
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", HH_A, ORG_A,
                           NAME + "household")
        for k in ("dom", "ant"):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", HH_A,
                               eid(k))


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


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(uid, method, path, body=None, timeout=90.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}"}}
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
    """Each stub matches the CURRENT signature of what it replaces (read at install time)."""
    import inspect

    import main
    from services import storage
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()
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


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS", f"{r['u']}")


OWNERSHIP_BEFORE: dict = {}


async def snapshot_ownership():
    out = {}
    for path in (f"/api/v1/entities/{eid('trust')}/tree", f"/api/v1/entities/{eid('trust')}/lookthrough",
                 f"/api/v1/entities/{eid('llc')}/ownership-graph", f"/api/v1/entities/{eid('llc')}/relationships",
                 f"/api/v1/entities/{eid('llc')}/ownership"):
        c, b = await api(U_SUPER, "GET", path)
        out[path] = (c, body_text(b))
    return out


async def y_ownership_before(conn):
    section("[Y] Ownership readers — snapshot BEFORE any governed relationship exists")
    OWNERSHIP_BEFORE.update(await snapshot_ownership())
    check(all(c == 200 for c, _ in OWNERSHIP_BEFORE.values()), "tree / lookthrough / graph / relationships / "
          "ownership all answer for the fixture", f"{[(p, c) for p, (c, _) in OWNERSHIP_BEFORE.items()]}")
    check(str(eid("llc")) in OWNERSHIP_BEFORE[f"/api/v1/entities/{eid('trust')}/lookthrough"][1],
          "look-through reaches the LLC (60%)")


def _pair_for(t: dict) -> tuple[str, str] | None:
    fr = [k for k, v in E.items() if v[1] == ORG_A and k not in ("demo", "muse", "muse2", "other_fam", "kid")
          and (t["from_types"] is None or v[2] in t["from_types"])]
    to = [k for k, v in E.items() if v[1] == ORG_A and k not in ("demo", "muse", "muse2", "other_fam", "kid")
          and (t["to_types"] is None or v[2] in t["to_types"])]
    for a in fr:
        for b in to:
            if a != b:
                return a, b
    return None


async def y_relationships(conn):
    section("[Y] Task 2 — every governed type round-trips; reverse label on the other record; refusals; end → history")
    c, v = await api(U_OA, "GET", "/api/v1/crm/vocabularies")
    types = (v.get("vocabularies") or {}).get("relationship_types") or [] if isinstance(v, dict) else []
    keys = {t["key"] for t in types}
    check(bool(c == 200 and set(REQUIRED_TYPES) <= keys), "the envelope serves every required type",
          f"{c} missing {sorted(set(REQUIRED_TYPES) - keys)}")
    check(all({"key", "label", "reverse_label", "from_types", "to_types", "symmetric"} <= set(t) for t in types),
          "each type carries key / label / reverse label / allowed from / to types / symmetric")
    own = next((t for t in types if t["key"] == "ownership"), {})
    check(bool(own.get("panel") is False and own.get("from_types") is None), "ownership: unchanged (any pair), "
          "not offered on the panel")
    done, bad = [], []
    for t in types:
        if t["key"] == "ownership":
            continue
        pair = _pair_for(t)
        if pair is None:
            bad.append((t["key"], "no fixture pair"))
            continue
        a, b = pair
        c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid(a)}/crm-relationships",
                         {"relationship_type": t["key"], "other_entity_id": str(eid(b)), "direction": "outgoing",
                          "notes": f"cd1 {t['key']}", "effective_date": "2020-01-01"})
        if c != 201 or r.get("label") != t["label"]:
            bad.append((t["key"], c, dumps(r)))
            continue
        c2, other = await api(U_OA, "GET", f"/api/v1/entities/{eid(b)}/crm-relationships")
        rows = [x for x in ((other.get("rows") or {}).get("current") or []) if x.get("id") == r["id"]] \
            if isinstance(other, dict) else []
        if not (c2 == 200 and len(rows) == 1 and rows[0]["label"] == t["reverse_label"]
                and rows[0]["other_entity_id"] == str(eid(a)) and rows[0]["direction"] == "incoming"):
            bad.append((t["key"], "reverse", c2, dumps(rows)))
            continue
        db = await scoped(conn, ORG_A, "SELECT relationship_type, notes, effective_date FROM entity_relationships "
                                       "WHERE id = $1", UUID(r["id"]), fetch="fetchrow")
        if not (db and db["relationship_type"] == t["key"] and db["effective_date"] == date(2020, 1, 1)):
            bad.append((t["key"], "db", dict(db) if db else None))
            continue
        done.append((t["key"], r["id"], a, b))
    check(bool(done) and not bad, f"{len(done)} governed types round-trip (API → DB re-read → reverse label on the "
          "other record)", f"{bad[:4]}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('dom')}/crm-relationships",
                     {"relationship_type": "best_friend", "other_entity_id": str(eid('ant'))})
    check(bool(c == 422 and "not a relationship type" in body_text(r)), "unknown type refused with the reason",
          f"{c} {dumps(r)}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('dom')}/crm-relationships",
                     {"relationship_type": "trustee", "other_entity_id": str(eid('ant')), "direction": "outgoing"})
    check(bool(c == 422 and "cannot point to an Individual record" in body_text(r)),
          "disallowed pair refused with the reason (trustee of a person)", f"{c} {dumps(r)}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('dom')}/crm-relationships",
                     {"relationship_type": "ownership", "other_entity_id": str(eid('llc'))})
    check(bool(c == 422 and "Ownership tab" in body_text(r)), "ownership is refused on the panel (Ownership tab)",
          f"{c}")
    c, r = await api(U_SUPER, "POST", "/api/v1/entity-relationships",
                     {"from_entity_id": str(eid('dom')), "to_entity_id": str(eid('ant')), "relationship_type": "pal"})
    check(bool(c == 422 and "not a relationship type" in body_text(r)), "the hierarchy rail's POST "
          "/entity-relationships refuses an unknown type too", f"{c} {dumps(r)}")
    sp = next((d for d in done if d[0] == "spouse"), None)
    if sp:
        c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid(sp[3])}/crm-relationships",
                         {"relationship_type": "spouse", "other_entity_id": str(eid(sp[2]))})
        check(bool(c == 409), "a symmetric type's reverse duplicate is refused (409)", f"{c} {dumps(r)}")
    if done:
        key, rid, a, b = done[0]
        n0 = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE id = $1", UUID(rid),
                          fetch="fetchval")
        c, r = await api(U_OA, "POST", f"/api/v1/crm-relationships/{rid}/end", {"reason": "cd1 ended"})
        c2, lst = await api(U_OA, "GET", f"/api/v1/entities/{eid(a)}/crm-relationships")
        hist = [x["id"] for x in ((lst.get("rows") or {}).get("history") or [])] if isinstance(lst, dict) else []
        cur = [x["id"] for x in ((lst.get("rows") or {}).get("current") or [])] if isinstance(lst, dict) else []
        row = await scoped(conn, ORG_A, "SELECT valid_to, system_to FROM entity_relationships WHERE id = $1",
                           UUID(rid), fetch="fetchrow")
        check(bool(c == 200 and rid in hist and rid not in cur and n0 == 1 and row and row["valid_to"] is not None
                   and row["system_to"] is None),
              "ending sets valid_to; the row is still there and listed as history, not current", f"{c} {dumps(r)}")
        c3, _ = await api(U_OA, "POST", f"/api/v1/crm-relationships/{rid}/end", {})
        check(bool(c3 == 404), "ending twice → 404", f"{c3}")
    return done


async def y_ownership_after(conn):
    section("[Y] Ownership readers unchanged by the governed relationships (same output as before)")
    after = await snapshot_ownership()
    diff = [p for p in OWNERSHIP_BEFORE if OWNERSHIP_BEFORE[p] != after.get(p)]
    check(bool(OWNERSHIP_BEFORE and not diff), "tree / lookthrough / graph / relationships / ownership: identical "
          "bodies before and after ~28 governed relationships on the same records", f"{diff}")


async def y_details(conn):
    section("[Y] Task 3 — detail fields save and re-read; trust_type sets sub_type; wrong types refused")
    trust = {"trust_type": "dynasty", "revocable": False, "situs_state": "DE", "gst_exempt": True,
             "vesting_age": "21", "termination": "perpetual (Delaware)", "annuity_term_years": "2",
             "annuity_end": "2027-12-31", "unitrust_rate_pct": "5.25", "crummey_notices": True,
             "funded_with": "Northline stock"}
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('trust')}/details", {"values": trust})
    c2, g = await api(U_OA, "GET", f"/api/v1/entities/{eid('trust')}/details")
    vals = {f["key"]: f["value"] for f in ((g.get("rows") or {}).get("fields") or [])} if isinstance(g, dict) else {}
    want = {"trust_type": "dynasty", "revocable": False, "situs_state": "DE", "gst_exempt": True, "vesting_age": 21,
            "termination": "perpetual (Delaware)", "annuity_term_years": 2, "annuity_end": "2027-12-31",
            "unitrust_rate_pct": "5.25", "crummey_notices": True, "funded_with": "Northline stock"}
    check(bool(c == 200 and c2 == 200 and all(vals.get(k) == v for k, v in want.items())),
          "a trust saves and re-reads every field (typed values; the decimal stays a string)",
          f"{c} {[(k, vals.get(k)) for k, v in want.items() if vals.get(k) != v]} {dumps(r)}")
    db = {r["attribute_key"]: (r["attribute_value"], r["value_type"]) for r in await scoped(
        conn, ORG_A, "SELECT attribute_key, attribute_value, value_type FROM entity_attributes WHERE entity_id = $1 "
                     "AND valid_to IS NULL AND system_to IS NULL", eid("trust"))}
    check(bool(db.get("detail.trust_type") == ("dynasty", "string") and db.get("detail.unitrust_rate_pct")
               == ("5.25", "decimal") and db.get("detail.gst_exempt") == ("true", "boolean") and len(db) == 11),
          "re-read from the table: 11 live detail rows with their value types", f"{db}")
    st = await scoped(conn, ORG_A, "SELECT sub_type FROM entities WHERE id = $1 AND valid_to IS NULL AND system_to IS "
                                   "NULL", eid("trust"), fetch="fetchval")
    arch = await scoped(conn, ORG_A, "SELECT count(*) FROM entities WHERE display_name = $1 AND system_to IS NOT NULL",
                        NAME + "Dynasty Trust", fetch="fetchval")
    check(bool(st == "Dynasty" and arch >= 1), "trust_type set sub_type 'Dynasty' through the system-axis write "
          "(prior version archived, id kept)", f"{st} / archived {arch}")
    c, _ = await api(U_OA, "PUT", f"/api/v1/entities/{eid('trust')}/details", {"values": {"vesting_age": "25"}})
    hist = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_attributes WHERE entity_id = $1 AND attribute_key = "
                                     "'detail.vesting_age'", eid("trust"), fetch="fetchval")
    check(bool(c == 200 and hist == 2), "an edit is a valid-time restatement (old row closed, new row live)",
          f"{c} rows {hist}")
    n0 = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_attributes WHERE entity_id = $1", eid("trust"),
                      fetch="fetchval")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('trust')}/details",
                     {"values": {"vesting_age": "twenty", "unitrust_rate_pct": 5.5, "trust_type": "bogus",
                                 "revocable": "maybe", "not_a_field": "x"}})
    n1 = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_attributes WHERE entity_id = $1", eid("trust"),
                      fetch="fetchval")
    t = body_text(r)
    check(bool(c == 422 and n0 == n1 and "whole number" in t and "float is refused" in t and "'bogus'" in t
               and "yes / no" in t and "not_a_field" in t), "wrong types refused (every reason at once), nothing "
          "written", f"{c} {n0}->{n1} {dumps(r)}")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('daf')}/details",
                     {"values": {"charity_type": "daf", "sponsor": str(eid("found")), "min_distribution_pct": "5"}})
    c2, g = await api(U_OA, "GET", f"/api/v1/entities/{eid('daf')}/details")
    f = {x["key"]: x for x in ((g.get("rows") or {}).get("fields") or [])} if isinstance(g, dict) else {}
    check(bool(c == 200 and f.get("charity_type", {}).get("value") == "daf" and f.get("sponsor", {}).get("display")
               == NAME + "Foundation" and f.get("min_distribution_pct", {}).get("value") == "5"
               and (g.get("rows") or {}).get("sub_type") == "Donor-advised fund"),
          "a foundation (DAF) saves charity_type (→ sub_type), sponsor (a CRM record) and the minimum", f"{dumps(f)}")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('daf')}/details", {"values": {"sponsor": str(eid('dom'))}})
    check(bool(c == 422), "a sponsor must be a legal entity, not a person", f"{c} {dumps(r)}")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('llc')}/details",
                     {"values": {"management": "manager_managed", "purpose": "Greenwich residence",
                                 "valuation_policy": "appraisal"}})
    c2, g = await api(U_OA, "GET", f"/api/v1/entities/{eid('llc')}/details")
    f = {x["key"]: x["value"] for x in ((g.get("rows") or {}).get("fields") or [])} if isinstance(g, dict) else {}
    check(bool(c == 200 and f.get("management") == "manager_managed" and f.get("purpose") == "Greenwich residence"
               and f.get("valuation_policy") == "appraisal" and "state_of_formation" in f),
          "an LLC saves management / purpose / valuation policy and shows its state of formation", f"{f}")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('llc')}/details", {"values": {"state_of_formation": "DE"}})
    check(bool(c == 422 and "Details form" in body_text(r)), "state of formation is edited on the Details form "
          "(region_code), not here", f"{c}")
    c, r = await api(U_OA, "GET", f"/api/v1/entities/{eid('trust')}/full")
    attrs = [a["attribute_key"] for a in (r.get("attributes") or [])] if isinstance(r, dict) else []
    check(bool(c == 200 and not any(a.startswith("detail.") for a in attrs)), "the generic Attributes list excludes "
          "the detail keys", f"{attrs}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('trust')}/attributes",
                     {"attribute_key": "detail.trust_type", "attribute_value": "grat"})
    check(bool(c == 422), "the generic attribute route refuses a 'detail.' key", f"{c}")


async def y_people(conn, done):
    section("[Y] Task 4 — date of death → deceased + inactive, still listed with relationships; minor from DOB")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('eli')}/crm-relationships",
                     {"relationship_type": "parent_of", "other_entity_id": str(eid("kid")), "direction": "outgoing"})
    check(bool(c == 201), "fixture: Elena is recorded as parent of the minor (before her date of death)", f"{c}")
    rels_before = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE (from_entity_id = $1 OR "
                                            "to_entity_id = $1) AND valid_to IS NULL", eid("eli"), fetch="fetchval")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('eli')}", {"date_of_death": "2008-02-09",
                                                                    "maiden_name": "Zucchini"})
    check(bool(c == 200 and r.get("status") == "deceased" and r.get("is_active") is False
               and r.get("is_deceased") is True and r.get("maiden_name") == "Zucchini" and r.get("is_minor") is False),
          "date of death sets status deceased and is_active false (and stores the maiden name)", f"{c} {dumps(r)}")
    c, full = await api(U_OA, "GET", f"/api/v1/entities/{eid('eli')}/full")
    c2, rel = await api(U_OA, "GET", f"/api/v1/entities/{eid('eli')}/crm-relationships")
    n_rel = len((rel.get("rows") or {}).get("current") or []) if isinstance(rel, dict) else -1
    c3, lst = await api(U_OA, "GET", f"/api/v1/entities?include_inactive=true&search={NAME}Elena")
    check(bool(c == 200 and c2 == 200 and n_rel == rels_before and rels_before >= 1 and c3 == 200
               and any(x.get("id") == str(eid("eli")) for x in lst)),
          "the deceased record is still listed, with every relationship", f"{c}/{c2}/{c3} rels {rels_before}→{n_rel}")
    row = await scoped(conn, ORG_A, "SELECT count(*) FROM entities WHERE id = $1", eid("eli"), fetch="fetchval")
    check(bool(row == 1), "nothing deleted (the stable id row is still there)", f"{row}")
    c, r = await api(U_OA, "PUT", f"/api/v1/entities/{eid('trust')}", {"date_of_death": "2020-01-01"})
    check(bool(c == 422), "a date of death on a trust is refused", f"{c}")
    c, lst = await api(U_OA, "GET", f"/api/v1/entities?search={NAME}")
    by = {x["id"]: x for x in lst} if isinstance(lst, list) else {}
    kid, ant = by.get(str(eid("kid")), {}), by.get(str(eid("ant")), {})
    t = date.today()
    exp_age = t.year - KID_DOB.year - ((t.month, t.day) < (KID_DOB.month, KID_DOB.day))
    check(bool(kid.get("is_minor") is True and kid.get("age") == exp_age and ant.get("is_minor") is False),
          "minor flag from DOB (under 18 today) in the CRM list; an adult without DOB is not a minor",
          f"{kid.get('age')} vs {exp_age}")


async def y_liabilities(conn):
    section("[Y] Task 5 — liabilities: create / edit (new version) / end; net worth exact; member scope; isolation")
    body = {"name": NAME + "Coastal mortgage", "kind": "mortgage", "lender_entity_id": str(eid("bank")),
            "original_amount": "4000000.00", "current_balance": "1000000.01", "as_of": date.today().isoformat(),
            "rate_type": "fixed", "rate_text": "5.35%", "maturity": "2049-01-01", "collateral_text": "the residence"}
    c, li = await api(U_OA, "POST", f"/api/v1/entities/{eid('llc')}/liabilities", body)
    row = await scoped(conn, ORG_A, "SELECT current_balance, original_amount, kind, lender_entity_id, lineage_id FROM "
                                    "portfolio.liabilities WHERE id = $1", UUID(li["id"]) if c == 201 else MISSING,
                       fetch="fetchrow")
    check(bool(c == 201 and row and row["current_balance"] == Decimal("1000000.01")
               and row["original_amount"] == Decimal("4000000.00") and row["lender_entity_id"] == eid("bank")
               and li.get("lender_display") == NAME + "Coastal Bank"), "create: persisted exactly (Decimal)",
          f"{c} {dumps(li)}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('llc')}/liabilities", {**body, "current_balance": 1.5})
    check(bool(c == 422 and "float is refused" in body_text(r)), "a float amount is refused", f"{c}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('llc')}/liabilities", {**body, "kind": "loan_shark"})
    check(bool(c == 422), "an unknown kind is refused", f"{c}")
    c, r = await api(U_OA, "POST", f"/api/v1/entities/{eid('llc')}/liabilities", {**body, "org_id": str(ORG_B)})
    check(bool(c == 422), "a body declaring org_id is refused (extra='forbid')", f"{c}")
    c, li2 = await api(U_OA, "PATCH", f"/api/v1/liabilities/{li['id']}", {"current_balance": "900000.00",
                                                                         "reason": "amortised"})
    old = await scoped(conn, ORG_A, "SELECT valid_to FROM portfolio.liabilities WHERE id = $1", UUID(li["id"]),
                       fetch="fetchval")
    check(bool(c == 200 and li2.get("id") != li["id"] and li2.get("lineage_id") == li["lineage_id"]
               and li2.get("current_balance") == "900000.00" and old is not None),
          "edit: a new version (new id, same lineage), the old row closed", f"{c} {dumps(li2)}")
    c, r = await api(U_OA, "PATCH", f"/api/v1/liabilities/{li['id']}", {"current_balance": "1.00"})
    check(bool(c == 404), "editing a closed version → 404", f"{c}")
    # net worth: LLC gross 14,500,000.00 − 900,000.00; the trust owns 60% → 8,700,000.00 − 540,000.00
    c, nw = await api(U_OA, "GET", f"/api/v1/entities/{eid('llc')}/net-worth")
    n = (nw or {}).get("rows") or {}
    check(bool(c == 200 and n.get("gross") == "14500000.00" and n.get("liabilities") == "900000.00"
               and n.get("net") == "13600000.00"), "entity net worth = gross − liabilities, exact", f"{dumps(n)}")
    c, nw = await api(U_OA, "GET", f"/api/v1/entities/{eid('trust')}/net-worth")
    n = (nw or {}).get("rows") or {}
    check(bool(c == 200 and n.get("liabilities") == "540000.00" and n.get("net") == "8160000.00"),
          "look-through: the 60% owner carries 60% of the LLC's liability", f"{dumps(n)}")
    c, l2 = await api(U_OA, "POST", f"/api/v1/entities/{eid('dom')}/liabilities",
                      {"name": NAME + "SBL", "kind": "securities_backed_line", "current_balance": "250000.33",
                       "as_of": date.today().isoformat(), "rate_type": "floating", "rate_text": "SOFR + 1.10%"})
    c2, hh = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_A}")
    hn = (hh or {}).get("net_worth") or {}
    names = [x.get("name") for x in (hh or {}).get("liabilities") or []] if isinstance(hh, dict) else []
    check(bool(c == 201 and c2 == 200 and hn.get("gross") == "3500000.55" and hn.get("liabilities") == "250000.33"
               and hn.get("net") == "3250000.22" and names == [NAME + "SBL"]),
          "household: Liabilities section + net worth = 3,500,000.55 − 250,000.33 = 3,250,000.22 (exact)",
          f"{c}/{c2} {dumps(hn)} {names}")
    c, ro = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_A}/rollup")
    check(bool(c == 200 and (ro.get("net_worth") or {}).get("net") == "3250000.22"),
          "the flexible rollup carries the same three numbers", f"{c} {dumps(ro)}")
    c, e = await api(U_OA, "POST", f"/api/v1/liabilities/{li2['id']}/end", {"reason": "repaid"})
    c2, lst = await api(U_OA, "GET", f"/api/v1/entities/{eid('llc')}/liabilities")
    rows = (lst.get("rows") or {}) if isinstance(lst, dict) else {}
    kept = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.liabilities WHERE lineage_id = $1",
                        UUID(li["lineage_id"]), fetch="fetchval")
    check(bool(c == 200 and c2 == 200 and not rows.get("current") and len(rows.get("history") or []) == 2
               and kept == 2 and (lst.get("net_worth") or {}).get("liabilities") == "0.00"),
          "end: no current row, both versions kept as history, net worth back to gross", f"{dumps(rows)}")
    # member scope
    c, mine = await api(U_OA, "POST", f"/api/v1/entities/{eid('mem')}/liabilities",
                        {"name": NAME + "Member mortgage", "kind": "mortgage", "current_balance": "10.00",
                         "as_of": date.today().isoformat()})
    c1, r1 = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('mem')}/liabilities")
    c2, r2 = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('dom')}/liabilities")
    c3, r3 = await api(U_OTHER, "GET", f"/api/v1/entities/{eid('mem')}/liabilities")
    check(bool(c == 201 and c1 == 200 and [x["name"] for x in (r1.get("rows") or {}).get("current") or []]
               == [NAME + "Member mortgage"] and (r1.get("permissions") or {}).get("can_write") is False
               and (r1.get("vocabularies") or {}).get("editable") == []),
          "a member sees their own family's liabilities, view-only (editable empty)", f"{c1} {dumps(r1)}")
    check(bool(c2 == 404 and NAME + "SBL" not in body_text(r2)), "a member of the org refused another family's "
          "(404, not disclosed)", f"{c2}")
    check(bool(c3 == 404 and "Member mortgage" not in body_text(r3)), "a member of ANOTHER org refused (404)",
          f"{c3}")
    # isolation (app_service, rolbypassrls = false)
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.liabilities WHERE org_id = $1", ORG_A,
                     fetch="fetchval")
    b = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.liabilities WHERE org_id = $1", ORG_A,
                     fetch="fetchval")
    await platform(conn, "INSERT INTO portfolio.liabilities (lineage_id, org_id, owner_entity_id, name, kind, "
                         "current_balance, as_of) VALUES (gen_random_uuid(), $1, $2, $3, 'other', 1, $4)",
                   ORG_B, eid("orgb"), NAME + "Org B loan", date.today())
    ab = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.liabilities WHERE org_id = $1", ORG_B,
                      fetch="fetchval")
    bb = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.liabilities WHERE org_id = $1", ORG_B,
                      fetch="fetchval")
    check(bool(a >= 3 and b == 0 and ab == 0 and bb == 1), "RLS: each org sees only its own liabilities, both "
          "directions", f"A sees A {a}, B sees A {b}, A sees B {ab}, B sees B {bb}")
    try:
        await scoped(conn, ORG_B, "INSERT INTO portfolio.liabilities (lineage_id, org_id, owner_entity_id, name, kind, "
                                  "current_balance, as_of) VALUES (gen_random_uuid(), $1, $2, 'x', 'other', 1, $3)",
                     ORG_A, eid("dom"), date.today(), fetch="execute")
        wrote = True
    except Exception:  # noqa: BLE001
        wrote = False
    check(not wrote, "RLS: org B cannot write a liability into org A (WITH CHECK)")


async def y_gates(conn):
    section("[Y] Gates both ways on the identical request (member / support staff / org admin)")
    req = [("POST", f"/api/v1/entities/{eid('ant')}/liabilities",
            {"name": NAME + "gate", "kind": "other", "current_balance": "1.00", "as_of": date.today().isoformat()}),
           ("POST", f"/api/v1/entities/{eid('ant')}/crm-relationships",
            {"relationship_type": "advisor", "other_entity_id": str(eid('bank')), "direction": "incoming"}),
           ("PUT", f"/api/v1/entities/{eid('lp')}/details", {"values": {"purpose": "gate"}})]
    for method, path, body in req:
        cm, bm = await api(U_MEMBER, method, path, body)
        cs, bs = await api(U_SUPPORT, method, path, body)
        co, bo = await api(U_OA, method, path, body)
        check(bool(cm == 403 and cs == 403 and "manage_members" in body_text(bs) and co in (200, 201)),
              f"{method} {path.split('/api/v1')[1].replace(str(eid('ant')), '{id}').replace(str(eid('lp')), '{id}')}"
              ": member 403, support staff 403 naming manage_members, org admin admitted",
              f"{cm}/{cs}/{co} {dumps(bs)} {dumps(bo)}")
    cm, _ = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('mem')}/crm-relationships")
    cs, bs = await api(U_SUPPORT, "GET", f"/api/v1/entities/{eid('ant')}/crm-relationships")
    check(bool(cm == 403 and cs == 200 and (bs.get("permissions") or {}).get("can_write") is False
               and (bs.get("vocabularies") or {}).get("editable") == []),
          "reads: a member is refused the staff-data panel; support staff reads it view-only (editable empty)",
          f"{cm}/{cs}")
    cs, bs = await api(U_SUPPORT, "GET", f"/api/v1/entities/{eid('trust')}/details")
    check(bool(cs == 200 and (bs.get("vocabularies") or {}).get("editable") == []), "details: support staff view-only")
    for path in (f"/api/v1/liabilities/{MISSING}/end", f"/api/v1/crm-relationships/{MISSING}/end"):
        cs, _ = await api(U_SUPPORT, "POST", path, {})
        co, _ = await api(U_OA, "POST", path, {})
        check(bool(cs == 403 and co == 404), f"POST {path.split('/api/v1')[1].split(str(MISSING))[0]}…/end: support "
              "403, org admin passes the gate (then 404)", f"{cs}/{co}")


async def y_demo(conn):
    section("[Y] Task 6 — demo protection: a demo-tagged fixture survives every clean-up; a MUSE fixture is removed")
    op = await operator_conn()
    try:
        raised = None
        try:
            await VC.cleanup(op, orgs=[], plan={"public.entities": ("id = ANY($1::uuid[])", [[eid("demo"),
                                                                                         eid("muse")]])},
                             label="crmdetail1 demo probe")
        except RuntimeError as exc:
            raised = str(exc)
        both = await platform(conn, "SELECT count(*) FROM entities WHERE id = ANY($1::uuid[])", [eid("demo"),
                                                                                                eid("muse")],
                              fetch="fetchval")
        check(bool(raised and "permanent demo data" in raised and both == 2),
              "_verify_cleanup refuses a delete set holding a demo row — rolled back, the MUSE row untouched too",
              f"{raised} / {both}")
        res = await VC.cleanup(op, orgs=[], plan={"public.entities": ("id = ANY($1::uuid[])", [[eid("muse")]])},
                               label="crmdetail1 muse probe")
        gone = await platform(conn, "SELECT count(*) FROM entities WHERE id = $1", eid("muse"), fetch="fetchval")
        demo = await platform(conn, "SELECT count(*) FROM entities WHERE id = $1", eid("demo"), fetch="fetchval")
        check(bool(gone == 0 and demo == 1), "a MUSE fixture is still removed; the demo fixture survives",
              f"{res['log']}")
        refused_db = None
        try:
            async with op.transaction():
                await op.execute("DELETE FROM entities WHERE id = $1", eid("demo"))
        except Exception as exc:  # noqa: BLE001
            refused_db = str(exc)
        check(bool(refused_db and "permanent demo data" in refused_db), "a raw DELETE on the operator connection is "
              "refused by the database guard", f"{refused_db}")
        # the documented fixture path works ONLY for an is_test org — and is rolled back here
        ok_path = None
        try:
            async with op.transaction():
                await op.execute("SELECT set_config('crm.demo_teardown', 'fixture', true)")
                n = await op.execute("DELETE FROM entities WHERE id = $1", eid("demo"))
                ok_path = n
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        check(bool(ok_path == "DELETE 1"), "the fixture GUC admits a demo row of an is_test org (rolled back)",
              f"{ok_path}")
        real_refused = None
        try:
            async with op.transaction():
                await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                rid = await op.fetchval("INSERT INTO entities (org_id, entity_type, display_name, tags) VALUES "
                                        "('00000000-0000-0000-0000-000000000001', 'individual', $1, $2) RETURNING id",
                                        NAME + "real-org demo probe (rolled back)", [DEMO_TAG])
                await op.execute("SELECT set_config('crm.demo_teardown', 'fixture', true)")
                try:
                    async with op.transaction():
                        await op.execute("DELETE FROM entities WHERE id = $1", rid)
                except Exception as exc:  # noqa: BLE001
                    real_refused = str(exc)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        left = await platform(conn, "SELECT count(*) FROM entities WHERE display_name = $1",
                              NAME + "real-org demo probe (rolled back)", fetch="fetchval")
        check(bool(real_refused and "permanent demo data" in real_refused and left == 0),
              "a REAL org's demo row is refused even with the fixture GUC (rolled back, nothing left)", f"{real_refused}")
        for mod_name in ("perms1_remove_muse_rows", "bugfix2_remove_muse_rows"):
            mod = importlib.import_module(mod_name)
            ref = await mod.demo_refusals(op, {"public.entities": [eid("demo")]})
            clean = await mod.demo_refusals(op, {"public.entities": [eid("muse2")]})
            src = (HERE / f"{mod_name}.py").read_text(encoding="utf-8")
            check(bool(ref and not clean and "bad += await demo_refusals(conn, plan)" in src),
                  f"{mod_name}: its guards refuse a plan holding a demo row; a MUSE row passes", f"{ref} / {clean}")
        # a demo household (every member demo-tagged) cannot be deleted in the app
        await platform(conn, "INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", _id(61), ORG_A,
                       NAME + "demo household")
        await platform(conn, "INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", _id(61),
                       eid("demo"))
        c, r = await api(U_OA, "DELETE", f"/api/v1/admin/households/{_id(61)}")
        still = await platform(conn, "SELECT count(*) FROM households WHERE id = $1", _id(61), fetch="fetchval")
        check(bool(c == 409 and still == 1), "the app refuses to delete a demo household (409), it stays",
              f"{c} {dumps(r)}")
    finally:
        await op.close()


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
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_perms1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_perms1.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_perms1.py: 0 FAIL",
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
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = {t: await fingerprint(conn, t) for t in REAL_FINGERPRINTED}
        await run("role", y_role_is_real, conn)
        await run("catalog", y_catalog, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        section("[Y] Task 1 — findings 1a–1f and the 1e table")
        self_task1(check, find)
        self_table(check, find)
        await run("ownership before", y_ownership_before, conn)
        done = await run("relationships", y_relationships, conn) or []
        await run("ownership after", y_ownership_after, conn)
        await run("details", y_details, conn)
        await run("people", y_people, conn, done)
        await run("liabilities", y_liabilities, conn)
        await run("gates", y_gates, conn)
        await run("demo", y_demo, conn)
        section("[Y] Static — vocabulary, schema file, docs, regression set, panels")
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
            check(left == 0, "zero fixture rows remain (the demo-tagged fixture included, by the fixture path)",
                  f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                for t in REAL_FINGERPRINTED:
                    now = await fingerprint(conn, t)
                    check(now == real[t], f"real {t} unchanged (count + checksum by primary key)",
                          f"{real[t][0]} → {now[0]} rows")
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
