"""verify_rollup2 — ONE net-worth roll-up everywhere (HollisMuse fix-up v5, 2026-11-06): the entity card, its
Recalculate, the household's Owners list and the household total count the SAME holdings; a valued holding with no
allocation bucket is counted under "Unclassified" (never dropped) and listed, a valueless one is listed as skipped;
listed funds get a bucket from their master category (the mutual-fund default no longer keys a bond fund as equity);
duplicate ownership entries are reported, warned and repairable, and a new percentage is a new version; the household
total is split into Family and Charitable; cash lines keep their names.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch and R2 are stubbed with their CURRENT signatures, and httpx's network transport refuses.

The real app against the live database, in TWO fixture orgs (99000000-…-000000580001 / 580002), ``is_test = true``
(asserted), as are the fixture users; fixture securities in the platform master by reserved ids / R2V tickers, a
fixture-only provider key for the fund category map. The live org is only READ (inside READ ONLY transactions, under
its own RLS context): its readers must AGREE with each other — never a fixed total. Real and demo (demo-olivo) rows:
count + checksum before and after. Teardown at start and end via _verify_cleanup by fixture id.

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
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from contextlib import redirect_stdout
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

SPRINT_KEY = "rollup2"
BEFORE_REF = "9d77a42"   # rollup2-work base: "merge bugfix4"

NEW_ROUTES = {
    ("GET", "/api/v1/admin/ownership/duplicates"): "super_admin",
    ("GET", "/api/v1/admin/fund-categories"): "super_admin",
    ("PUT", "/api/v1/admin/fund-categories"): "super_admin",
    ("GET", "/api/v1/admin/fund-categories/proposals"): "super_admin",
}

# ── Task 1 — needles from the discovery (docs/ROLLUP2_DISCOVERY.md) ───────────────────────────────────────────────
TASK1 = [
    ("docs/ROLLUP2_DISCOVERY.md", "## 1a. Every net-worth reader — and the one that dropped valued holdings",
     "1a: the card / Recalculate read the stored snapshot, which the rollup built WITHOUT unkeyed lines"),
    ("docs/ROLLUP2_DISCOVERY.md", "wrote **no `entity_holdings`\nrow at all** for the CRUT",
     "1b: the CRUT's only holdings were unkeyed funds → no snapshot row → '$0, not yet rolled up'"),
    ("docs/ROLLUP2_DISCOVERY.md", "**Muse's exact figures are not reproducible from today's data.**",
     "1c: Owners-list vs card — two snapshots of different moments; neither side right"),
    ("docs/ROLLUP2_DISCOVERY.md", "ZERO owner / owned pairs with more than one live percentage",
     "1d: no live duplicate; Art Holdings 60 % is the LP's stake on the look-through tree"),
    ("docs/ROLLUP2_DISCOVERY.md", "**24 of the 25 kind-`fund` assets carry a category**",
     "1e: the master's fund categories (FinanceDatabase), BTC filed as 'Corporate Bonds'"),
    ("docs/ROLLUP2_DISCOVERY.md", "charitable-REMAINDER trust: **CRUT** or **CRAT**",
     "1f: charitable = foundations and charitable-remainder trusts"),
    ("docs/ROLLUP2_DISCOVERY.md", "**apply 23, review 2, unchanged 6, unclassified 1**",
     "Task 3: the live --funds dry-run proposals are recorded"),
]
CODE_NEEDLES = [
    ("apps/api/services/networth.py", 'UNCLASSIFIED_KEY = "unclassified"',
     "Task 2: one core; unclassified holdings counted under their own bucket"),
    ("apps/api/services/liabilities.py", "nw = await NW.entity(conn, org_id, entity_id, as_of=as_of)",
     "Task 2: the entity card computes live from the core (not the stored snapshot)"),
    ("apps/api/services/portfolio_rollup.py", "async def compute_buckets(c, org_id: str, as_of_date: date",
     "Task 2 / 7: the stored snapshot is built from the same core"),
    ("apps/api/services/portfolio_rollup.py", "SELECT DISTINCT ON (p.owner_entity_id, p.asset_id, p.account_id)",
     "bugfix4 fix 1 kept: one line per owner / asset / account"),
    ("apps/api/services/household_total.py", "def split(ctx, members: set[str]) -> dict:",
     "Task 5: family / charitable split"),
    ("apps/api/services/household_total.py", "def owners(ctx, members: set[str]) -> list[dict]:",
     "1c: the Owners list = each member's own card figure"),
    ("apps/api/services/fund_classification.py", "def propose_one(*, name: str | None, category: str | None",
     "Task 3: a fund's key from its category, cross-checked with its name"),
    ("apps/api/services/portfolio_kind_taxonomy.py", "if kind in FUND_KINDS:",
     "Task 3: a fund kind is never keyed by its kind"),
    ("apps/api/scripts/backfill_taxonomy_keys.py", "async def funds(conn, org: str, *, dry_run: bool) -> int:",
     "Task 3: backfill --funds"),
    ("apps/api/services/ownership_duplicates.py", "def group(rows: list[dict]) -> list[dict]:",
     "Task 4: the duplicate report"),
    ("apps/api/routers/entity_graph.py", "separate_holding: bool = False",
     "Task 4: a new percentage is a new version unless a separate holding is asked for"),
    ("apps/api/scripts/repair_ownership_duplicates.py", "async def run(conn, org: str, *, dry_run: bool",
     "Task 4: the repair script"),
    ("apps/api/scripts/recalculate_rollups.py", "async def plan(conn, org: str, as_of: date, household",
     "Task 7: the snapshot refresh script"),
    ("apps/api/services/held_away.py", 'NAME_OPTIONAL_KINDS = frozenset({"cash", "money_market"})',
     "Task 6: cash / money-market lines take an optional name"),
    ("apps/web/components/networth/NetWorthDetails.jsx", "export function SplitTable(",
     "Task 5: the household page shows Family / Charitable / Household total"),
    ("apps/web/components/crm/OwnershipTree.jsx", "via={ancestors.length >= 1 ? node.display_name : null}",
     "1d: a nested tree entry says whose stake it is"),
    ("docs/rollup2_part1.sql", "CREATE TABLE IF NOT EXISTS portfolio.fund_category_taxonomy",
     "Task 3: the fund category map table (Super Admin)"),
    ("docs/ROLLUP2_DISCOVERY.md", "**CORRECTED (rollup2 fix 1, 2026-11-07).**",
     "fix 1: 1d corrected — entity_rel_active_unique forbids a second live edge; duplicates live in entity_ownership"),
    ("apps/api/routers/entity_graph.py", "async def _require_org_entity(pool, org_id, entity_id) -> None:",
     "fix 1: another org's entity id is a 404 on the entity_graph reads"),
    ("apps/api/routers/entity_graph.py", "except asyncpg.UniqueViolationError as exc:",
     "fix 1: the unique index's refusal is a 409, never a 500"),
]
REPO_ROLLUP = "apps/api/services/portfolio_rollup.py"


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _key(rec: dict) -> tuple:
    return ((rec.get("methods") or [""])[0], rec["path"])


def self_task1(check, find):
    for rel, needle, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or _read(rel)
        check(needle in txt, f"Task 1 — {meaning}", f"{rel}: needle missing")
    old = VS.git_show(BEFORE_REF, REPO_ROLLUP) or ""
    check(bool("if not taxonomy_key:" in old and "reason=NO_TAXONOMY_REASON" in old),
          "Task 1a REPRODUCED in code: before the sprint the rollup skipped a valued position with no key",
          f"{BEFORE_REF}:{REPO_ROLLUP}")
    old_l = VS.git_show(BEFORE_REF, "apps/api/services/liabilities.py") or ""
    check("held = await _sum_holdings(conn, org_id, [str(entity_id)], as_of)" in old_l,
          "Task 1a REPRODUCED in code: before the sprint the entity card read the stored snapshot")
    find("1c — Muse's Owners-list figures are not reproducible; there is no Owners list in the Consolidate code; "
         "the household page now has one, computed per member by the card's own function")
    find("1d — no duplicate live ownership pair exists in the live org; the 60 % / 15 % was two different owners")
    find("1d CORRECTED (fix 1) — entity_rel_active_unique forbids two live entity_relationships edges per pair; a "
         "duplicate arises only inside entity_ownership or across the two tables")


def self_static(check, find):
    for rel, needle, meaning in CODE_NEEDLES:
        check(needle in _read(rel), meaning, f"{rel}: needle missing")
    check("verify_rollup2.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. ROLLUP2 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    steps = ["backfill_taxonomy_keys.py --org", "--funds --dry-run", "repair_ownership_duplicates.py",
             "recalculate_rollups.py", "Recalculate"]
    missing = [s for s in steps if s not in top]
    order = [top.find(s) for s in ("backfill_taxonomy_keys.py", "repair_ownership_duplicates.py",
                                   "recalculate_rollups.py")]
    check(bool(top and not missing and order == sorted(order) and -1 not in order and "| Owner" in top),
          "Task 9: PROJECT_STATUS entry — findings table and the operator steps IN ORDER", f"missing {missing} {order}")
    old_kmap = VS.git_show(BEFORE_REF, "apps/api/services/org_settings.py") or ""
    check(bool('"mutual_fund": "taxonomy_sc_1",' in old_kmap
               and '"mutual_fund": "taxonomy_sc_1",' not in _read("apps/api/services/org_settings.py")),
          "Task 3: REPRODUCED the mutual-fund → equity default before the sprint; removed now")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if want.split(" ")[0] not in str((new.get(k) or {}).get("after") or "")]
    check(not wrong, "every new route carries its stated gate (Super Admin)", f"{wrong}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_pure(check, find):
    """Pure functions, no DB: each shown to refuse the bad case as well as pass the good one. (Imports are local on
    purpose: --self-checks-only reaches here BEFORE hydrate(); these modules are pure at import.)"""
    from datetime import date as _d
    from decimal import Decimal as D

    from services import fund_classification as FC
    from services import household_total as HTL
    from services import networth as NW
    from services import ownership_duplicates as OD
    from services import held_away as HA
    from services import portfolio_rollup as PRU

    check(NW.UNCLASSIFIED_KEY == PRU.UNCLASSIFIED_BUCKET, "the snapshot's Unclassified bucket IS the core's key")
    ctx = NW.Ctx(org_id="o", as_of=_d(2026, 11, 4))
    ctx.edges = [("p", "llc", D(50))]
    ln = lambda pid, own, aid, v, k: {"position_id": pid, "owner_entity_id": own, "asset_id": aid, "value": D(v),  # noqa: E731
                                      "taxonomy_key": k, "as_of_date": _d(2026, 11, 4), "basis": "entered_value"}
    ctx.lines = [ln("1", "p", "fund", "100", None), ln("2", "p", "eq", "50", "taxonomy_sc_1"),
                 ln("3", "llc", "eq2", "40", "taxonomy_sc_1")]
    ctx.skipped = [{"position_id": "4", "owner_entity_id": "p", "asset_id": "x", "reason": "no value"}]
    ctx.liabilities = [("p", D(10))]
    f = NW.figures(ctx, {"p"})
    check(bool(f["gross"] == D(170) and f["unclassified"] == D(100) and f["by_bucket"]["unclassified"] == D(100)
               and f["liabilities"] == D(10) and [r["position_id"] for r in f["skipped_rows"]] == ["4"]),
          "core: an unkeyed valued line is IN gross under Unclassified; a valueless one listed, not counted",
          f"{f['gross']} {f['unclassified']} {dict(f['by_bucket'])}")
    f0 = NW.figures(ctx, {"nobody"})
    check(bool(f0["gross"] == 0 and f0["counted"] == 0 and not f0["skipped_rows"]),
          "core: an entity that reaches nothing counts nothing (no borrowed lines)")
    check(bool(NW.is_charitable({"entity_type": "foundation"}) and NW.is_charitable({"entity_type": "trust",
                                                                                      "sub_type": "CRUT"})
               and not NW.is_charitable({"entity_type": "trust", "sub_type": "CLAT"})
               and not NW.is_charitable({"entity_type": "trust", "sub_type": "Revocable"})
               and not NW.is_charitable({"entity_type": "individual"})),
          "charitable: foundation and CRUT yes; CLAT, revocable trust, person no")
    ctx.names = {"p": {"display_name": "P", "entity_type": "individual", "sub_type": None},
                 "c": {"display_name": "C", "entity_type": "trust", "sub_type": "CRUT"}}
    ctx.lines.append(ln("5", "c", "f2", "30", None))
    sp = HTL.split(ctx, {"p", "c"})
    sums = all(D(sp["family"][k]) + D(sp["charitable"][k]) == D(sp["household"][k])
               for k in ("gross", "liabilities", "net", "unclassified_value"))
    check(bool(sums and sp["charitable"]["gross"] == "30.00" and sp["household"]["gross"] == "200.00"),
          "split: family + charitable == household on every figure; charitable = the CRUT's 30", f"{sp}")
    m = FC.DEFAULT_CATEGORY_MAP
    keys = {"taxonomy_mc_2_1", "taxonomy_sub_2_3_4", "taxonomy_mc_1_1", "taxonomy_mc_7_3", "taxonomy_sc_2",
            "taxonomy_sc_1", "taxonomy_sc_7"}
    pb = FC.propose_one(name="Total Bond ETF", category="Investment Grade Bonds", provider="finance_database",
                        current_key=None, asset_type="fund", mapping=m, org_keys=keys)
    pm = FC.propose_one(name="Municipal Bond ETF", category="Municipal Bonds", provider="finance_database",
                        current_key=None, asset_type="fund", mapping=m, org_keys=keys)
    pe = FC.propose_one(name="Large Cap Equity ETF", category="Large Cap", provider="finance_database",
                        current_key=None, asset_type="fund", mapping=m, org_keys=keys)
    pn = FC.propose_one(name="Mystery ETF", category=None, provider=None, current_key=None, asset_type="fund",
                        mapping=m, org_keys=keys)
    pbtc = FC.propose_one(name="Grayscale Bitcoin Mini Trust (BTC)", category="Corporate Bonds",
                          provider="finance_database", current_key=None, asset_type="fund", mapping=m, org_keys=keys)
    pmf = FC.propose_one(name="Fidelity U.S. Bond Index Fund", category=None, provider=None,
                         current_key="taxonomy_sc_1", asset_type="mutual_fund", mapping=m, org_keys=keys)
    check(bool(pb["status"] == "apply" and pb["key"] == "taxonomy_mc_2_1" and pm["key"] == "taxonomy_sub_2_3_4"
               and pm["status"] == "apply" and pe["key"] == "taxonomy_mc_1_1" and pn["status"] == "unclassified"
               and pn["key"] is None),
          "fund map: bond / muni / equity by category; no category stays Unclassified",
          f"{pb['key']} {pm['key']} {pe['key']} {pn['status']}")
    check(bool(pbtc["status"] == "review" and pbtc["key"] == "taxonomy_mc_7_3"
               and pmf["status"] == "review" and pmf["key"] == "taxonomy_sc_2"),
          "fund map: a category the name contradicts (BTC 'Corporate Bonds') and a bond fund keyed equity by the old "
          "default are REVIEW, never applied", f"{pbtc} {pmf}")
    rows = [{"id": "a", "source": "entity_relationships", "parent_id": "P", "child_id": "L", "ownership_pct": D(50),
             "valid_from": _d(2026, 1, 1), "created_at": _d(2026, 1, 1), "note": None},
            {"id": "b", "source": "entity_relationships", "parent_id": "P", "child_id": "L", "ownership_pct": D(30),
             "valid_from": _d(2026, 2, 1), "created_at": _d(2026, 2, 1), "note": None},
            {"id": "c", "source": "entity_relationships", "parent_id": "P", "child_id": "X", "ownership_pct": D(10),
             "valid_from": _d(2026, 2, 1), "created_at": _d(2026, 2, 1), "note": None}]
    g = OD.group(rows)
    plan = OD.repair_plan(g)
    check(bool(len(g) == 1 and g[0]["survivor"] == "b" and [e["id"] for e in plan[0]["archive"]] == ["a"]
               and g[0]["entries"][0]["newer"] is True),
          "duplicates: only the pair with two live entries; the newer survives, the older is archived", f"{g}")
    a1 = HA.manual_key({"asset_kind": "cash", "name": "Coastal Bank checking", "terms": {}})
    a2 = HA.manual_key({"asset_kind": "cash", "name": "Cash / sweep", "terms": {}})
    check(bool(a1 != a2 and a1.startswith("manual:")), "two named cash lines resolve to two different holdings")
    r = HA.validate_row({"asset_kind": "money_market", "quantity": "5"}, _d(2026, 11, 4))
    check(bool(r.get("reject") is None), "a money-market line with no name is accepted (name optional)",
          f"{r.get('reject')}")
    r2 = HA.validate_row({"asset_kind": "mutual_fund", "quantity": "5"}, _d(2026, 11, 4))
    check(bool(r2.get("reject") and r2["reject"][0] == "name_missing"), "a mutual-fund line still needs a name",
          f"{r2.get('reject')}")


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

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
import backfill_taxonomy_keys as BF  # noqa: E402
import repair_ownership_duplicates as RD  # noqa: E402
from services import fund_classification as FC  # noqa: E402
from services import ownership_duplicates as OD  # noqa: E402
from services import household_total as HT  # noqa: E402
from services import liabilities as L  # noqa: E402
from services import portfolio_assets as PA  # noqa: E402
from services import rbac  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import close_pool  # noqa: E402
from services.portfolio_assets import _OrgWrite  # noqa: E402
from services.portfolio_kind_taxonomy import key_for_kind, kind_map  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "rollup2verify"
NAME = "R2V "            # reserved fixture name prefix (asserted to match no real row)
FIX_PROVIDER = "r2v_fixture"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000058{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
ORG_LIVE = UUID("00000000-0000-0000-0000-000000000001")
TAXONOMY_SOURCE_ORG = ORG_LIVE   # read-only: its asset_taxonomy rows are copied into the fixture orgs
SEC_BOND, SEC_MUNI, SEC_EQF, SEC_NOCAT = _id(900), _id(901), _id(902), _id(903)
FIX_SECS = [SEC_BOND, SEC_MUNI, SEC_EQF, SEC_NOCAT]
SEC_INFO = {SEC_BOND: ("Fixture Total Bond ETF", "R2VBND", "Investment Grade Bonds"),
            SEC_MUNI: ("Fixture Municipal Bond ETF", "R2VMUN", "Municipal Bonds"),
            SEC_EQF: ("Fixture Large Cap Equity ETF", "R2VEQF", "Large Cap"),
            SEC_NOCAT: ("Fixture Mystery ETF", "R2VNOC", None)}

ORGADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                            "manage_portfolio", "view_dashboard", "view_held_away", "manage_held_away",
                            "approve_held_away", "view_replication"})
U_OA, U_SUPER, U_MEMBER, U_OB_ADMIN = _id(12), _id(13), _id(14), _id(15)
USERS = {
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OB_ADMIN: (ORG_B, "org_admin", "obadmin", "org_admin", ORGADMIN_PERMS),
}
E = {
    "p": (_id(30), ORG_A, "individual", "Principal", None),
    "llc": (_id(31), ORG_A, "llc", "Holdings LLC", None),
    "crut": (_id(32), ORG_A, "trust", "Charitable Remainder Trust", "CRUT"),
    "cashp": (_id(33), ORG_A, "individual", "Cash Owner", None),
    "dup": (_id(34), ORG_A, "llc", "Dup Target LLC", None),
    "orgb": (_id(35), ORG_B, "individual", "Org B Person", None),
}
HH1, HH2, HH_B = _id(60), _id(61), _id(62)
HHS = [HH1, HH2, HH_B]
ACC_CASH, ACC_B = _id(70), _id(71)
# assets: (kind, key, gsid)
A = {"fund": (_id(100), "fund", None, None), "eq": (_id(101), "equity", "taxonomy_sc_1", None),
     "cash": (_id(102), "cash_holding", "taxonomy_mc_8_1", None), "noval": (_id(103), "other", None, None),
     "llc_eq": (_id(104), "equity", "taxonomy_sc_1", None), "crut1": (_id(105), "fund", None, None),
     "crut2": (_id(106), "fund", None, None), "dup_eq": (_id(107), "equity", "taxonomy_sc_1", None),
     "f_bond": (_id(110), "fund", None, SEC_BOND), "f_muni": (_id(111), "fund", None, SEC_MUNI),
     "f_eqf": (_id(112), "fund", None, SEC_EQF), "f_nocat": (_id(113), "fund", None, SEC_NOCAT),
     "b_eq": (_id(120), "equity", "taxonomy_sc_1", None)}
FUND_FIXTURES = ("f_bond", "f_muni", "f_eqf", "f_nocat")
# positions: (owner, asset, market_value or None (units, qty 10))
POS = [("p", "fund", "100000"), ("p", "eq", "50000"), ("p", "cash", "25000"), ("p", "noval", None),
       ("llc", "llc_eq", "40000"), ("crut", "crut1", "30000"), ("crut", "crut2", "20000"),
       ("dup", "dup_eq", "80000"), ("orgb", "b_eq", "7000")]
AS_OF = date(2026, 11, 4)

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.households", "public.household_memberships", "public.accounts", "public.entity_holdings",
           "public.role_permissions", "public.user_roles", "public.held_away_imports", "public.held_away_records",
           "public.ownership_change_log", "public.entity_ownership",
           "portfolio.assets", "portfolio.positions", "portfolio.valuations", "portfolio.liabilities",
           "portfolio.rollup_requests", "portfolio.securities_global", "portfolio.securities_global_identifiers",
           "portfolio.security_reference_attributes", "portfolio.fund_category_taxonomy")
#: NOT counted, by name: public.audit_log and public.member_todos — real activity writes them during a run.
REAL_FINGERPRINTED = ("public.entities", "public.households", "public.household_memberships", "public.accounts",
                      "public.entity_relationships", "public.held_away_imports", "public.held_away_records",
                      "portfolio.assets", "portfolio.positions", "portfolio.valuations", "portfolio.liabilities")
#: public.entity_holdings is NOT fingerprinted, by name: it is derived data every real rollup (the nightly, any real
#: Recalculate) rewrites during a run; the fixture never writes a real org's row (the rollup is org-scoped by RLS).

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


def eid(k: str) -> UUID:
    return E[k][0]


def aid(k: str) -> UUID:
    return A[k][0]


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:700]


def body_text(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def D(x) -> Decimal:
    return Decimal(str(x)) if x is not None else Decimal(-1)


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


#: columns real activity updates, excluded by name from a fingerprint: none on the fingerprinted tables.
EXCLUDED_COLS: dict[str, tuple] = {}


async def fingerprint(conn, table: str, *, where: str = "true", args=()) -> tuple[int, str]:
    """Count + md5 of every row keyed by the table's real primary key (read from the catalog)."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    drop = "".join(f" - '{c}'" for c in EXCLUDED_COLS.get(table, ()))
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg((to_jsonb(t){drop})::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


def _real_where(table: str) -> str:
    if table == "public.household_memberships":
        return "household_id NOT IN (SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))"
    return "NOT (org_id = ANY($1::uuid[]))"


async def real_prints(conn) -> dict:
    out = {t: await fingerprint(conn, t, where=_real_where(t), args=(FIXTURE_ORGS,)) for t in REAL_FINGERPRINTED}
    out["portfolio.securities_global"] = await fingerprint(conn, "portfolio.securities_global",
                                                           where="NOT (id = ANY($1::uuid[]))", args=(FIX_SECS,))
    out["portfolio.fund_category_taxonomy"] = await fingerprint(conn, "portfolio.fund_category_taxonomy",
                                                                where="provider_key <> $1", args=(FIX_PROVIDER,))
    return out


DEMO = "SELECT id FROM public.entities WHERE 'demo-olivo' = ANY(tags)"
DEMO_SQL = {
    "entities": (f"SELECT * FROM public.entities WHERE 'demo-olivo' = ANY(tags)", "id, system_from"),
    "positions": (f"SELECT * FROM portfolio.positions WHERE owner_entity_id IN ({DEMO})", "id"),
    "assets": (f"SELECT * FROM portfolio.assets WHERE id IN (SELECT asset_id FROM portfolio.positions WHERE "
               f"owner_entity_id IN ({DEMO}))", "id, system_from"),
    "relationships": (f"SELECT * FROM public.entity_relationships WHERE from_entity_id IN ({DEMO}) OR "
                      f"to_entity_id IN ({DEMO})", "id"),
    "liabilities": (f"SELECT * FROM portfolio.liabilities WHERE owner_entity_id IN ({DEMO})", "id"),
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


def _plan() -> dict:
    users = list(USERS)
    return {
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        "public.household_memberships": ("household_id = ANY($1::uuid[])", [HHS]),
        "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                    [FIXTURE_ORGS]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
        "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[])", [FIX_SECS]),
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [FIX_SECS]),
        "portfolio.securities_global": ("id = ANY($1::uuid[])", [FIX_SECS]),
        "portfolio.fund_category_taxonomy": ("provider_key = $1", [FIX_PROVIDER]),
    }


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only, by fixture id — the documented privileged path (operator DSN + execution.teardown,
    transaction-local) for the append-only held-away import / record tables."""
    op = await operator_conn()
    try:
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               gucs=("execution.teardown",), label="verify_rollup2")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [HHS]),
            ("SELECT count(*) FROM portfolio.securities_global WHERE id = ANY($1::uuid[])", [FIX_SECS]),
            ("SELECT count(*) FROM portfolio.security_reference_attributes WHERE global_security_id = "
             "ANY($1::uuid[])", [FIX_SECS]),
            ("SELECT count(*) FROM portfolio.fund_category_taxonomy WHERE provider_key = $1", [FIX_PROVIDER])])
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
                  "portfolio.positions", "portfolio.assets", "portfolio.liabilities", "portfolio.securities_global",
                  "portfolio.securities_global_identifiers", "portfolio.security_reference_attributes",
                  "portfolio.fund_category_taxonomy", "public.ownership_change_log")


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
    check(bool(cons and {"p", "c", "u", "f"} >= kinds and "p" in kinds and "c" in kinds),
          f"{len(cons)} PK / CHECK / UNIQUE / FK constraints read (decoded as text)", f"{sorted(kinds)}")
    fct = [d for (t, _n), (k, d) in cons.items() if t == "portfolio.fund_category_taxonomy" and k == "c"]
    check(bool(len(fct) >= 3 and any("org_id IS NULL" in d for d in fct)),
          "the fund category map table is deployed with its platform-scope CHECK", f"{fct}")
    VALUES["registration_type"] = _pick(cons, "public.accounts", "registration_type", "individual")
    VALUES["tax_status"] = _pick(cons, "public.accounts", "tax_status", "taxable")
    VALUES["custodian_code"] = _pick(cons, "public.accounts", "custodian_code", "ALTRUIST")
    VALUES["asset_class"] = _pick(cons, "portfolio.assets", "asset_class", "financial")
    VALUES["authority"] = _pick(cons, "portfolio.positions", "authority", "manual")
    VALUES["source_system"] = _pick(cons, "portfolio.positions", "source_system", "manual")
    find("seeded with catalog-legal values", f"{VALUES}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM portfolio.assets WHERE name LIKE $1 AND NOT "
                                "(org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND "
                                "NOT (id = ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 "
                                "AND NOT (id = ANY($4::uuid[]))) + (SELECT count(*) FROM "
                                "portfolio.securities_global_identifiers WHERE id_value LIKE 'R2V%' AND NOT "
                                "(global_security_id = ANY($6::uuid[]))) + (SELECT count(*) FROM "
                                "portfolio.securities_global WHERE name LIKE $1 AND NOT (id = ANY($6::uuid[]))) + "
                                "(SELECT count(*) FROM portfolio.fund_category_taxonomy WHERE provider_key = $7)",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), FIX_SECS, FIX_PROVIDER,
                          fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (names, subs, slugs, R2V tickers, the fixture provider key) "
          "match no real row", f"{real}")


ROLE_IDS: dict[tuple, UUID] = {}


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"ROLLUP2 Verify Org {nm.upper()}", f"{TAG}-{nm}")
            await conn.execute(
                "INSERT INTO config (org_id, config_key, config_value, value_type, category, display_order, "
                "is_active) SELECT $1, config_key, config_value, value_type, category, display_order, true FROM config "
                "WHERE org_id = $2 AND category = 'asset_taxonomy' AND is_active", org, TAXONOMY_SOURCE_ORG)
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        role_perms = {"org_admin": ORGADMIN_PERMS, "member": frozenset(C.MEMBER_PERMS),
                      "super_admin": frozenset(C.SUPER_ADMIN_PERMS)}
        for org in FIXTURE_ORGS:
            for rname, perms in role_perms.items():
                rid = await rbac.ensure_role(conn, org, rname, f"ROLLUP2 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                ROLE_IDS[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, f"{TAG}-{nm}@test.local", f"ROLLUP2 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, ROLE_IDS[(org, rname)])
        for k, (i, org, etype, name, sub) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, sub_type) "
                               "VALUES ($1, $2, $3, 'active', $4::entity_type, $5)", i, org, NAME + name, etype, sub)
        await conn.execute("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
                           "ownership_pct) VALUES ($1, $2, $3, 'ownership', 50)", ORG_A, eid("p"), eid("llc"))
        for hh, org, nm in ((HH1, ORG_A, "Family"), (HH2, ORG_A, "Cash"), (HH_B, ORG_B, "B")):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org, NAME + nm)
        for hh, k in ((HH1, "p"), (HH1, "llc"), (HH1, "crut"), (HH2, "cashp"), (HH2, "dup"), (HH_B, "orgb")):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh,
                               eid(k))
        for acc, mask, owner, hh, org in ((ACC_CASH, "****5801", "cashp", HH2, ORG_A),
                                          (ACC_B, "****5802", "orgb", HH_B, ORG_B)):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, household_id, account_number_masked, account_number_hash, "
                "custodian_code, registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, $3, "
                "$4, $5, $6, $7, $8, $9, true)", acc, org, hh, mask, hashlib.sha256(f"{TAG}{mask}".encode()).hexdigest(),
                VALUES["custodian_code"], VALUES["registration_type"], VALUES["tax_status"], eid(owner))
        for sid, (nm, tick, cat) in SEC_INFO.items():
            await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, canonical_id) "
                               "VALUES ($1, $2, 'fund', $1)", sid, NAME + nm)
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1, 'ticker', $2, true)", sid, tick)
            if cat:
                await conn.execute("INSERT INTO portfolio.security_reference_attributes (global_security_id, field, "
                                   "value, provider_key, as_of) VALUES ($1, 'fund_category', $2, 'finance_database', "
                                   "$3)", sid, cat, AS_OF)
        for k, (i, kind, key, gs) in A.items():
            org = ORG_B if k == "b_eq" else ORG_A
            await conn.execute(
                "INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_class, asset_type, "
                "ownership_basis, valuation_method, default_taxonomy_key) VALUES ($1, $2, $3, $4, $5, $6, 'units', "
                "'market_price', $7)", i, org, gs, NAME + f"asset {k}", VALUES["asset_class"], kind, key)
        for owner, asset, mv in POS:
            org = E[owner][1]
            await conn.execute(
                "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis, "
                "quantity, market_value, authority, source_system) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                org, eid(owner), aid(asset), AS_OF, "value" if mv is not None else "units",
                None if mv is not None else Decimal(10), Decimal(mv) if mv is not None else None,
                VALUES["authority"], VALUES["source_system"])


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == len(FIXTURE_ORGS) and u == len(USERS)), "every fixture org and user is is_test = true", f"{o}/{u}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []


def token_for_sub(sub: str, org: UUID) -> str:
    key = f"t-{TAG}-{sub}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub, "org_id": str(org)}
    return key


async def api(who, method, path, body=None, timeout=180.0):
    import main

    tok = token_for_sub(sub_of(who), USERS[who][0])
    kw = {"headers": {"Authorization": f"Bearer {tok}"}}
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
        raise AssertionError(f"verify_rollup2: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


# ── independent SQL (one anchor, one recursive term) ─────────────────────────────────────────────────────────────
CARD_SQL = """
    WITH RECURSIVE reach(entity_id, share, depth) AS (
        SELECT $2::uuid, 1::numeric, 0
      UNION ALL
        SELECT r.to_entity_id, reach.share * r.ownership_pct / 100, reach.depth + 1
          FROM reach JOIN entity_relationships r ON r.from_entity_id = reach.entity_id AND r.org_id = $1
           AND r.relationship_type = 'ownership' AND r.ownership_pct IS NOT NULL AND r.valid_to IS NULL
           AND r.system_to IS NULL
         WHERE reach.depth < 10
    )
    SELECT COALESCE(sum(p.market_value * reach.share) FILTER (WHERE p.market_value IS NOT NULL), 0) AS gross,
           COALESCE(sum(p.market_value * reach.share) FILTER (WHERE p.market_value IS NOT NULL
                    AND COALESCE(p.taxonomy_key, a.default_taxonomy_key) IS NULL), 0) AS unclassified,
           COALESCE(sum(p.market_value * reach.share) FILTER (WHERE p.market_value IS NOT NULL
                    AND COALESCE(p.taxonomy_key, a.default_taxonomy_key) IS NOT NULL), 0) AS keyed_only,
           count(*) FILTER (WHERE p.market_value IS NULL) AS valueless
      FROM reach JOIN portfolio.positions p ON p.owner_entity_id = reach.entity_id AND p.org_id = $1
       AND p.valid_to IS NULL AND p.system_to IS NULL AND p.superseded_by_source IS NULL
      JOIN portfolio.assets a ON a.id = p.asset_id AND a.valid_to IS NULL AND a.system_to IS NULL
"""


async def sql_card(conn, entity) -> dict:
    r = await scoped(conn, ORG_A, CARD_SQL, ORG_A, entity, fetch="fetchrow")
    return {k: r[k] for k in ("gross", "unclassified", "keyed_only", "valueless")}


async def card(who, entity) -> tuple[int, dict]:
    c, b = await api(who, "GET", f"/api/v1/entities/{entity}/net-worth")
    return c, (b.get("rows") or {}) if c == 200 else b


# ═══ Task 2 / 8 — one roll-up ════════════════════════════════════════════════════════════════════════════════════
async def y_reproduce(conn):
    """BEFORE any write that triggers a family rollup (the liability below runs one inline: debounce 0)."""
    section("[Y] Reproduce first — the old snapshot's figure vs what is owned; the CRUT's missing row")
    p, crut = eid("p"), eid("crut")
    sp = await sql_card(conn, p)
    check(bool(sp["keyed_only"] == Decimal("95000") and sp["gross"] == Decimal("195000")),
          "REPRODUCED: what the old snapshot held for the principal (keyed lines only, 95,000) is 100,000 short of "
          "what it owns (195,000 — the unkeyed fund)", f"{sp}")
    sc = await sql_card(conn, crut)
    snap0 = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_holdings WHERE org_id = $1 AND entity_id = $2",
                         ORG_A, crut, fetch="fetchval")
    check(bool(snap0 == 0 and sc["keyed_only"] == 0 and sc["gross"] == Decimal("50000")),
          "REPRODUCED: the CRUT owns 50,000, all unkeyed — the old rollup's keyed figure 0, and no snapshot row yet "
          "(the '$0, not yet rolled up' state)", f"{snap0} {sc}")


async def y_one_rollup(conn):
    section("[Y] Task 2 — card == Recalculate == Owners list == household total == independent SQL")
    p, crut = eid("p"), eid("crut")
    sp = await sql_card(conn, p)
    c, nw = await card(U_OA, p)
    ok = c == 200 and D(nw.get("gross")) == sp["gross"] == Decimal("195000")
    check(bool(ok and D(nw.get("liabilities")) == Decimal("10000") and D(nw.get("net")) == Decimal("185000")),
          "entity card == independent SQL (195,000 gross, 10,000 liabilities, 185,000 net)", f"{c} {dumps(nw)}")
    uncl_ids = [r.get("position_id") for r in nw.get("unclassified") or []]
    fund_pid = await scoped(conn, ORG_A, "SELECT id::text FROM portfolio.positions WHERE asset_id = $1", aid("fund"),
                            fetch="fetchval")
    noval_pid = await scoped(conn, ORG_A, "SELECT id::text FROM portfolio.positions WHERE asset_id = $1",
                             aid("noval"), fetch="fetchval")
    check(bool(c == 200 and fund_pid and uncl_ids == [fund_pid] and D(nw.get("unclassified_value")) == Decimal("100000")
               and (nw.get("by_bucket") or {}).get("unclassified") == "100000.00"),
          "the unkeyed fund is counted under 'Unclassified' and listed (exactly that one line)",
          f"{uncl_ids} {fund_pid} {dumps(nw.get('by_bucket'))}")
    sk = [r.get("position_id") for r in nw.get("skipped") or []]
    check(bool(c == 200 and noval_pid and sk == [noval_pid] and nw.get("unvalued_positions") == 1),
          "the valueless line is NOT counted and is listed as skipped (with its reason)", f"{dumps(nw.get('skipped'))}")
    cr, rr = await api(U_OA, "POST", f"/api/v1/entities/{p}/net-worth/recalculate", {})
    rn = rr.get("net_worth") or {}
    check(bool(cr == 200 and rn.get("gross") == nw.get("gross") and rn.get("net") == nw.get("net")),
          "Recalculate returns the same figure as the card", f"{cr} {dumps(rn)}")
    today = date.today()
    snap = {r["taxonomy_key"]: r["market_value"] for r in await scoped(
        conn, ORG_A, "SELECT taxonomy_key, market_value FROM entity_holdings WHERE org_id = $1 AND entity_id = $2 AND "
                     "as_of_date = $3 AND source = 'portfolio'", ORG_A, p, today)}
    check(bool(snap and sum(snap.values(), Decimal(0)) == Decimal("195000")
               and snap.get("unclassified") == Decimal("100000.00")),
          "the stored snapshot Recalculate wrote equals the card, the fund in the 'unclassified' bucket", f"{snap}")
    cs, sm = await api(U_OA, "GET", f"/api/v1/households/{HH1}/summary")
    t = sm.get("total") or {}
    owners = {o["entity_id"]: o for o in t.get("owners") or []}
    po = owners.get(str(p)) or {}
    check(bool(cs == 200 and po.get("gross") == nw.get("gross") and po.get("net") == nw.get("net")),
          "the household's Owners list row for the principal == its card", f"{cs} {dumps(po)}")
    hsql = await scoped(conn, ORG_A, "SELECT sum(p.market_value) FROM portfolio.positions p WHERE p.org_id = $1 AND "
                                     "p.owner_entity_id = ANY($2::uuid[]) AND p.valid_to IS NULL AND p.system_to IS "
                                     "NULL", ORG_A, [p, eid("llc"), crut], fetch="fetchval")
    check(bool(cs == 200 and D(t.get("gross")) == hsql == Decimal("265000")),
          "household total == independent SQL (every member's valued lines once: 265,000)",
          f"{t.get('gross')} sql {hsql}")
    mismatch = []
    for ent in (p, eid("llc"), crut):
        cc, cn = await card(U_OA, ent)
        o = owners.get(str(ent)) or {}
        s = await sql_card(conn, ent)
        if not (cc == 200 and cn.get("gross") == o.get("gross") and D(cn.get("gross")) == s["gross"]):
            mismatch.append((str(ent), cc, cn.get("gross"), o.get("gross"), s["gross"]))
    check(bool(cs == 200 and len(owners) == 3 and not mismatch),
          "for every member: card == Owners-list row == independent SQL", f"{mismatch}")


async def y_crut(conn):
    section("[Y] A CRUT whose only holdings are unkeyed funds shows its real total and is in the Owners list")
    crut = eid("crut")
    c, nw = await card(U_OA, crut)
    check(bool(c == 200 and nw.get("gross") == "50000.00" and nw.get("unclassified_value") == "50000.00"
               and nw.get("holdings_as_of") == AS_OF.isoformat()),
          "the CRUT's card: 50,000 (never $0), all Unclassified, holdings dated (not 'not rolled up')",
          f"{c} {dumps(nw)}")
    cs, sm = await api(U_OA, "GET", f"/api/v1/households/{HH1}/summary")
    o = next((x for x in (sm.get("total") or {}).get("owners") or [] if x["entity_id"] == str(crut)), None)
    check(bool(cs == 200 and o and o["gross"] == "50000.00" and o["charitable"] is True),
          "the CRUT appears in the Owners list with its total, marked charitable", f"{dumps(o)}")


async def y_split(conn):
    section("[Y] Task 5 — Family / Charitable / Household total")
    cs, sm = await api(U_OA, "GET", f"/api/v1/households/{HH1}/summary")
    t = sm.get("total") or {}
    s = t.get("split") or {}
    fields = ("gross", "liabilities", "net", "unclassified_value")
    sums = bool(s) and all(D(s["family"][k]) + D(s["charitable"][k]) == D(s["household"][k]) == D(t[k])
                           for k in fields)
    check(bool(cs == 200 and sums), "family + charitable == household total == the summary's own figures, on "
          "gross / liabilities / net / unclassified", f"{dumps(s)}")
    names = [e.get("display_name") for e in (s.get("charitable") or {}).get("entities") or []]
    check(bool(cs == 200 and (s.get("charitable") or {}).get("gross") == "50000.00"
               and (s.get("family") or {}).get("gross") == "215000.00" and names == [NAME + E["crut"][3]]),
          "charitable = the CRUT (50,000); family = the rest (215,000)", f"{names} {dumps(s)}")
    labels = [x.get("key") for x in (sm.get("vocabularies") or {}).get("split_lines") or []]
    check(labels == ["family", "charitable", "household"], "the split's labels come from the envelope", f"{labels}")


# ═══ Task 3 — fund classification ════════════════════════════════════════════════════════════════════════════════
async def y_funds(conn):
    section("[Y] Task 3 — fund buckets from the master category; the mutual-fund default fixed; --funds dry run")
    async with _OrgWrite(conn, str(ORG_A)) as c:
        km = await kind_map(c, str(ORG_A))
        mf = await key_for_kind(c, str(ORG_A), "mutual_fund")
        kb = await key_for_kind(c, str(ORG_A), "fund", global_security_id=SEC_BOND)
        kn = await key_for_kind(c, str(ORG_A), "fund", global_security_id=SEC_NOCAT)
    check(bool("mutual_fund" not in km and mf is None),
          "the mutual-fund kind no longer maps to Public Equity (a bond mutual fund is not keyed equity)", f"{mf}")
    check(bool(kb == "taxonomy_mc_2_1" and kn is None),
          "creation path: a master bond fund is keyed by its category; a fund with no category stays unkeyed",
          f"{kb} {kn}")
    mf_id = await PA.create_asset(conn, org_id=str(ORG_A), name=NAME + "U.S. Bond Index Fund",
                                  asset_type="mutual_fund")
    mf_key = await scoped(conn, ORG_A, "SELECT default_taxonomy_key FROM portfolio.assets WHERE id = $1 AND "
                                       "system_to IS NULL", UUID(str(mf_id)), fetch="fetchval")
    check(mf_key is None, "a new mutual fund (no master link) is created unkeyed → counted as Unclassified",
          f"{mf_key}")
    fp0 = await fingerprint(conn, "portfolio.assets", where="org_id = $1", args=(ORG_A,))
    buf = io.StringIO()
    with redirect_stdout(buf):
        n_dry = await BF.funds(conn, str(ORG_A), dry_run=True)
    out = buf.getvalue()
    fp1 = await fingerprint(conn, "portfolio.assets", where="org_id = $1", args=(ORG_A,))
    async with _OrgWrite(conn, str(ORG_A)) as c:
        props = {p["asset_id"]: p for p in await FC.propose(c, str(ORG_A))}
    applies = sorted(k for k, p in props.items() if p["status"] == "apply")
    want = sorted(str(aid(k)) for k in ("f_bond", "f_muni", "f_eqf"))
    got = {k: (props.get(str(aid(k))) or {}).get("key") for k in FUND_FIXTURES}
    check(bool(n_dry == 0 and fp1 == fp0), "--funds --dry-run writes nothing (assets count + checksum unchanged)",
          f"{n_dry}")
    check(bool(applies == want and got == {"f_bond": "taxonomy_mc_2_1", "f_muni": "taxonomy_sub_2_3_4",
                                           "f_eqf": "taxonomy_mc_1_1", "f_nocat": None}
               and (props.get(str(aid("f_nocat"))) or {}).get("status") == "unclassified"),
          "--funds proposes EXACTLY the bond → IG credit, muni → Municipal, equity → US Large Cap; the fund with no "
          "category stays Unclassified", f"{applies} {got}")
    printed = all(str(aid(k)) in out for k in FUND_FIXTURES) and "category 'Municipal Bonds'" in out
    check(bool(printed), "the dry run lists every proposal with its reason (category → key)", out[-400:])
    n1 = await BF.funds(conn, str(ORG_A), dry_run=False)
    keyed = {r["id"]: r["k"] for r in await scoped(conn, ORG_A, "SELECT id::text, default_taxonomy_key AS k FROM "
                                                                "portfolio.assets WHERE id = ANY($1::uuid[]) AND "
                                                                "system_to IS NULL", [aid(k) for k in FUND_FIXTURES])}
    n2 = await BF.funds(conn, str(ORG_A), dry_run=False)
    check(bool(n1 == 3 and keyed.get(str(aid("f_muni"))) == "taxonomy_sub_2_3_4"
               and keyed.get(str(aid("f_nocat"))) is None and n2 == 0),
          "the real --funds keys exactly the three (id kept), the uncategorised one stays unkeyed; a re-run keys 0",
          f"{n1} {keyed} {n2}")
    cs, sm = await api(U_SUPER, "GET", "/api/v1/admin/fund-categories")
    row = next((r for r in sm.get("rows") or [] if r["category"] == "municipal bonds"), None)
    check(bool(cs == 200 and row and row["taxonomy_key"] == "taxonomy_sub_2_3_4"),
          "Super Admin reads the map (muni → Municipal Bonds)", f"{cs} {dumps(row)}")
    body = {"provider_key": FIX_PROVIDER, "category": "R2V Test Category", "taxonomy_key": "taxonomy_mc_2_1"}
    c403, b403 = await api(U_OA, "PUT", "/api/v1/admin/fund-categories", body)
    g403, _ = await api(U_OA, "GET", "/api/v1/admin/fund-categories")
    left = await platform(conn, "SELECT count(*) FROM portfolio.fund_category_taxonomy WHERE provider_key = $1",
                          FIX_PROVIDER, fetch="fetchval")
    check(bool(c403 == 403 and g403 == 403 and left == 0),
          "an org admin is refused the map (read and a VALID write), and nothing was written", f"{c403} {g403} {left}")
    c1, b1 = await api(U_SUPER, "PUT", "/api/v1/admin/fund-categories", body)
    c2, b2 = await api(U_SUPER, "PUT", "/api/v1/admin/fund-categories", {**body, "taxonomy_key": "taxonomy_sc_2"})
    live = await platform(conn, "SELECT taxonomy_key FROM portfolio.fund_category_taxonomy WHERE provider_key = $1 "
                                "AND valid_to IS NULL AND system_to IS NULL", FIX_PROVIDER)
    allv = await platform(conn, "SELECT count(*) FROM portfolio.fund_category_taxonomy WHERE provider_key = $1",
                          FIX_PROVIDER, fetch="fetchval")
    check(bool(c1 == 200 and c2 == 200 and (b2.get("replaced") or {}).get("taxonomy_key") == "taxonomy_mc_2_1"
               and [r["taxonomy_key"] for r in live] == ["taxonomy_sc_2"] and allv == 2),
          "Super Admin edits the map: a new version, the prior kept as history (one live, two rows)",
          f"{c1} {c2} {dumps(b2)} {allv}")
    cb, bb = await api(U_SUPER, "PUT", "/api/v1/admin/fund-categories", {**body, "taxonomy_key": "equity"})
    check(cb == 422, "a value that is not a taxonomy key is refused (422)", f"{cb} {dumps(bb)}")


# ═══ Task 4 — duplicate ownership ═══════════════════════════════════════════════════════════════════════════════
async def y_duplicates(conn):
    section("[Y] Task 4 — duplicate ownership: reported, warned, repaired (dry run / real / CHANGED: 0); new % = new "
            "version")
    owner, owned = eid("cashp"), eid("dup")
    # ROLLUP2 fix 1 — WHERE a duplicate can really arise (catalog + write paths). entity_relationships CANNOT hold two
    # live edges for one pair: the partial unique index entity_rel_active_unique (valid_to IS NULL) refuses the
    # second. entity_ownership has only its primary key. So a duplicate is (i) two live entity_ownership rows
    # (POST /entity-ownership checks only that the pair's sum stays <= 100), or (ii) one live edge in EACH table —
    # POST /entities/{id}/ownership versions only an entity_relationships edge and never looks at entity_ownership.
    idx = await scoped(conn, ORG_A, "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = "
                                    "'entity_rel_active_unique'", fetch="fetchval")
    eo_uniq = await scoped(conn, ORG_A, "SELECT array_agg(indexname ORDER BY indexname) FROM pg_indexes WHERE "
                                        "schemaname = 'public' AND tablename = 'entity_ownership' AND indexdef LIKE "
                                        "'CREATE UNIQUE%'", fetch="fetchval")
    check(bool(idx and "UNIQUE" in idx and "(from_entity_id, to_entity_id, relationship_type)" in idx
               and "valid_to IS NULL" in idx and list(eo_uniq or []) == ["entity_ownership_pkey"]),
          "catalog: entity_relationships has ONE live edge per pair (entity_rel_active_unique); entity_ownership has "
          "no unique key but its primary key", f"{idx} {eo_uniq}")
    rel = ("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, ownership_pct, "
           "valid_from) VALUES ($1, $2, $3, 'ownership', $4, $5) RETURNING id::text")
    refused = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                               "set_config('app.is_super_admin', 'false', true)", str(ORG_A))
            await conn.fetchval(rel, ORG_A, owner, owned, Decimal(60), date(2026, 1, 1))
            await conn.fetchval(rel, ORG_A, owner, owned, Decimal(15), date(2026, 2, 1))
    except Exception as exc:  # noqa: BLE001
        refused = type(exc).__name__
    left = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE from_entity_id = $1 AND "
                                     "to_entity_id = $2", owner, owned, fetch="fetchval")
    check(bool(refused == "UniqueViolationError" and left == 0),
          "two live entity_relationships edges for one pair are REFUSED by the index (the old fixture's state is "
          "impossible); nothing written", f"{refused} {left}")
    eo = ("INSERT INTO entity_ownership (org_id, parent_id, child_id, ownership_pct, ownership_type) "
          "VALUES ($1, $2, $3, $4, 'direct') RETURNING id")
    same_table = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                               "set_config('app.is_super_admin', 'false', true)", str(ORG_A))
            await conn.fetchval(eo, ORG_A, owner, owned, Decimal(60))
            await conn.fetchval(eo, ORG_A, owner, owned, Decimal(10))
            same_table = [p for p in await OD.find(conn, ORG_A) if p["parent_id"] == str(owner)]
            raise _Rollback()
    except _Rollback:
        pass
    left_eo = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_ownership WHERE parent_id = $1 AND child_id = $2",
                           owner, owned, fetch="fetchval")
    check(bool(same_table and len(same_table) == 1 and len(same_table[0]["entries"]) == 2
               and {e["source"] for e in same_table[0]["entries"]} == {"entity_ownership"} and left_eo == 0),
          "two live entity_ownership rows for one pair ARE accepted (no unique key) and the report finds them "
          "(rolled back)", f"{dumps(same_table)} {left_eo}")
    # the fixture duplicate, seeded the way it really arises: through the two real write routes, one edge in each table
    ce, be = await api(U_OA, "POST", "/api/v1/entity-ownership",
                       {"parent_id": str(owner), "child_id": str(owned), "ownership_pct": 60, "ownership_type": "direct"})
    old_id = str(be.get("id"))
    cn, bn = await api(U_OA, "POST", f"/api/v1/entities/{owner}/ownership",
                       {"direction": "owns", "counterparty_id": str(owned), "ownership_pct": "15"})
    new_id = str(bn.get("id"))
    check(bool(ce == 201 and cn == 201 and not bn.get("replaced_relationship_id")),
          "REAL PATH: POST /entity-ownership 60 % then POST /entities/{id}/ownership 15 % for the same pair are both "
          "accepted — the second never looks at entity_ownership", f"{ce} {cn} {dumps(bn)[:200]}")
    c0, nw0 = await card(U_OA, owner)
    check(bool(c0 == 200 and nw0.get("gross") == "60000.00"),
          "REPRODUCED: two live entries (60 % + 15 %) inflate the owner's look-through to 75 % (60,000 of 80,000)",
          f"{dumps(nw0)}")
    cr, rep = await api(U_SUPER, "GET", "/api/v1/admin/ownership/duplicates")
    pair = next((p for p in rep.get("rows") or [] if p["parent_id"] == str(owner) and p["child_id"] == str(owned)),
                None)
    check(bool(cr == 200 and pair and pair["survivor"] == new_id and len(pair["entries"]) == 2
               and pair["entries"][0]["newer"] is True
               and [e["source"] for e in pair["entries"]] == ["entity_relationships", "entity_ownership"]),
          "the Super Admin report lists the pair, both entries (one per table), the newer one as survivor",
          f"{cr} {dumps(pair)}")
    c403, _ = await api(U_OA, "GET", "/api/v1/admin/ownership/duplicates")
    check(c403 == 403, "an org admin is refused the duplicate report", f"{c403}")
    cp, own = await api(U_OA, "GET", f"/api/v1/entities/{owner}/ownership")
    w = [d.get("warning") for d in own.get("duplicates") or []]
    check(bool(cp == 200 and len(w) == 1 and "2 live ownership entries" in (w[0] or "")),
          "the ownership panel carries the warning", f"{cp} {w}")
    cq, ownp = await api(U_OA, "GET", f"/api/v1/entities/{eid('p')}/ownership")
    check(bool(cq == 200 and ownp.get("duplicates") == []), "no warning where there is no duplicate",
          f"{dumps(ownp.get('duplicates'))}")
    live_sql = ("SELECT (SELECT count(*) FROM entity_relationships WHERE from_entity_id = $1 AND to_entity_id = $2 "
                "AND valid_to IS NULL AND system_to IS NULL) + (SELECT count(*) FROM entity_ownership WHERE "
                "parent_id = $1 AND child_id = $2 AND valid_to IS NULL AND system_to IS NULL)")
    buf = io.StringIO()
    with redirect_stdout(buf):
        n_dry = await RD.run(conn, str(ORG_A), dry_run=True)
    dry_out = buf.getvalue()
    live_after_dry = await scoped(conn, ORG_A, live_sql, owner, owned, fetch="fetchval")
    check(bool(n_dry == 0 and live_after_dry == 2 and old_id in dry_out and new_id in dry_out and "NEWER" in dry_out),
          "repair --dry-run lists the pair (both entries, which is newer) and changes nothing", dry_out[-300:])
    with redirect_stdout(io.StringIO()):
        n1 = await RD.run(conn, str(ORG_A), dry_run=False)
        n2 = await RD.run(conn, str(ORG_A), dry_run=False)
    live = await scoped(conn, ORG_A, "SELECT id::text, ownership_pct FROM entity_relationships WHERE from_entity_id = "
                                     "$1 AND to_entity_id = $2 AND valid_to IS NULL AND system_to IS NULL", owner, owned)
    live_all = await scoped(conn, ORG_A, live_sql, owner, owned, fetch="fetchval")
    hist = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_ownership WHERE id = $1::uuid AND valid_to IS "
                                     "NOT NULL AND system_to IS NOT NULL", old_id, fetch="fetchval")
    audit = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                      "'repair_ownership_duplicate' AND resource_id = $2::uuid", ORG_A, old_id,
                         fetch="fetchval")
    check(bool(n1 == 1 and n2 == 0 and [r["id"] for r in live] == [new_id] and live_all == 1 and hist == 1
               and audit == 1),
          "the real repair archives the older entry (kept as history, audited); a second run CHANGED: 0",
          f"{n1} {n2} {[dict(r) for r in live]} {live_all} {hist} {audit}")
    c1, nw1 = await card(U_OA, owner)
    check(bool(c1 == 200 and nw1.get("gross") == "12000.00"), "after repair the owner's card uses the one live 15 %",
          f"{dumps(nw1)}")
    cv, v = await api(U_OA, "POST", f"/api/v1/entities/{owner}/ownership",
                      {"direction": "owns", "counterparty_id": str(owned), "ownership_pct": "25"})
    live2 = await scoped(conn, ORG_A, "SELECT id::text, ownership_pct FROM entity_relationships WHERE from_entity_id = "
                                      "$1 AND to_entity_id = $2 AND valid_to IS NULL AND system_to IS NULL", owner, owned)
    prior_closed = await scoped(conn, ORG_A, "SELECT valid_to IS NOT NULL AND system_to IS NOT NULL FROM "
                                             "entity_relationships WHERE id = $1::uuid", new_id, fetch="fetchval")
    check(bool(cv == 201 and v.get("replaced_relationship_id") == new_id and len(live2) == 1
               and live2[0]["ownership_pct"] == Decimal(25) and prior_closed is True),
          "a new percentage for the same pair is a NEW VERSION: the prior archived, one live entry (25 %)",
          f"{cv} {dumps(v)} {[dict(r) for r in live2]}")
    before = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE from_entity_id = $1 AND "
                                       "to_entity_id = $2", owner, owned, fetch="fetchval")
    cs, s = await api(U_OA, "POST", f"/api/v1/entities/{owner}/ownership",
                      {"direction": "owns", "counterparty_id": str(owned), "ownership_pct": "5",
                       "separate_holding": True})
    after = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE from_entity_id = $1 AND "
                                      "to_entity_id = $2", owner, owned, fetch="fetchval")
    live3 = await scoped(conn, ORG_A, "SELECT id::text, ownership_pct FROM entity_relationships WHERE from_entity_id = "
                                      "$1 AND to_entity_id = $2 AND valid_to IS NULL AND system_to IS NULL", owner, owned)
    check(bool(cs == 409 and live2 and live2[0]["id"] in str(s.get("detail")) and before == after
               and [r["id"] for r in live3] == [live2[0]["id"]]),
          "a 'separate holding' on a pair with a live edge is a 409 naming that edge (the index allows one), never a "
          "500; nothing written", f"{cs} {dumps(s)} {before}->{after}")


class _Rollback(Exception):
    pass


# ═══ Task 6 — cash line names ═══════════════════════════════════════════════════════════════════════════════════
async def y_cash_names(conn):
    section("[Y] Task 6 — cash lines keep their names; two named cash lines stay two holdings")
    rows = [{"asset_kind": "cash", "name": NAME + "Coastal Bank checking", "market_value": "1150000"},
            {"asset_kind": "cash", "name": NAME + "Cash / sweep", "market_value": "1050000"},
            {"asset_kind": "cash", "market_value": "500"}]
    c, b = await api(U_OA, "POST", f"/api/v1/held-away/accounts/{ACC_CASH}/imports/manual",
                     {"as_of": AS_OF.isoformat(), "rows": rows})
    c2, b2 = await api(U_SUPER, "POST", f"/api/v1/held-away/imports/{b.get('id')}/approve", {"note": "verify"}) \
        if c == 201 else (None, None)
    check(bool(c == 201 and b.get("rows_rejected") == 0 and c2 == 200), "a draft with two named cash lines and one "
          "unnamed is accepted and approved", f"{c} {dumps(b)} {c2} {dumps(b2)}")
    live = await scoped(conn, ORG_A, "SELECT a.name, a.asset_type, p.market_value FROM portfolio.positions p JOIN "
                                     "portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL WHERE "
                                     "p.account_id = $1 AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY "
                                     "p.market_value DESC", ACC_CASH)
    got = [(r["name"], r["market_value"]) for r in live]
    check(bool(len(live) == 3 and got[0] == (NAME + "Coastal Bank checking", Decimal("1150000"))
               and got[1] == (NAME + "Cash / sweep", Decimal("1050000"))
               and [r["asset_type"] for r in live] == ["cash_holding", "cash_holding", "cash"]),
          "three live cash holdings: the two names kept (each its own Cash & Equivalents holding) and the unnamed one "
          "on the org's cash asset", f"{[(r['name'], r['asset_type'], r['market_value']) for r in live]}")
    cp, panel = await api(U_OA, "GET", f"/api/v1/held-away/accounts/{ACC_CASH}")
    txt = body_text(panel)
    check(bool(cp == 200 and NAME + "Coastal Bank checking" in txt and NAME + "Cash / sweep" in txt),
          "the account's holdings show the cash line names", f"{cp}")
    cc, nw = await card(U_OA, eid("cashp"))
    sql = await sql_card(conn, eid("cashp"))
    check(bool(cc == 200 and D(nw.get("gross")) == sql["gross"]), "the cash owner's card == independent SQL",
          f"{nw.get('gross')} {sql}")


# ═══ Live org, read-only ═══════════════════════════════════════════════════════════════════════════════════════════
MUSE_ROWS = [("18400d6a", ("SPY", "VXUS", "BND", "IWM")), ("b06b7417", ("VT", "BND")), ("60c20f3b", ("SCHD", "SCHR")),
             ("27783cda", ("SCHB", "SCHF", "SCMB")), ("3f098811", ("SCHD", "SCHP")), ("4ea9aa44", ("QQQ",)),
             ("801422c7", ("SCHB", "SCMB"))]


async def y_live(conn):
    section("[Y] Live org, READ ONLY: every Olivo reader agrees; Muse's unkeyed lines are counted on their cards")
    async with conn.transaction(readonly=True):
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true), set_config('app.current_auth0_sub', '', true)", str(ORG_LIVE))
        hhs = await conn.fetch("SELECT id::text, name FROM households WHERE org_id = $1 AND name ILIKE '%olivo%'",
                               ORG_LIVE)
        check(bool(hhs), "the live org has an Olivo household to read (RLS context set)", f"{len(hhs)}")
        disagree, n_owner, cards = [], 0, {}
        for h in hhs:
            members = await HT.household_members(conn, ORG_LIVE, h["id"])
            t = await HT.total(conn, ORG_LIVE, members)
            s = t["split"]
            sums = all(Decimal(s["family"][k]) + Decimal(s["charitable"][k]) == Decimal(s["household"][k])
                       == Decimal(t[k]) for k in ("gross", "liabilities", "net", "unclassified_value"))
            check(sums, f"{h['name']}: family + charitable == household total (live)",
                  f"{t['gross']} {s['family']['gross']} + {s['charitable']['gross']}")
            for o in t["owners"]:
                n_owner += 1
                cn = await L.entity_net_worth(conn, ORG_LIVE, o["entity_id"])
                cards[o["entity_id"]] = (o["display_name"], cn)
                if (cn["gross"], cn["liabilities"], cn["net"]) != (o["gross"], o["liabilities"], o["net"]):
                    disagree.append((o["display_name"], cn["gross"], o["gross"]))
        check(bool(n_owner > 0 and not disagree), f"every Olivo entity: card == Owners-list row ({n_owner} entities)",
              f"{disagree}")
        found, problems = 0, []
        for prefix, tickers in MUSE_ROWS:
            ent = next((e for e in cards if e.startswith(prefix)), None)
            if ent is None:
                find(f"entity {prefix}… is not in an Olivo household today — not checked")
                continue
            name, cn = cards[ent]
            uncl = {r["position_id"] for r in cn["unclassified"]}
            skipped = {r["position_id"] for r in cn["skipped"]}
            for tk in tickers:
                rows = await conn.fetch(
                    "SELECT p.id::text, p.market_value, COALESCE(p.taxonomy_key, a.default_taxonomy_key) AS k "
                    "FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL "
                    "AND a.valid_to IS NULL JOIN portfolio.securities_global_identifiers i ON i.global_security_id = "
                    "a.global_security_id AND i.id_type = 'ticker' AND i.id_value = $3 AND i.system_to IS NULL "
                    "WHERE p.org_id = $1 AND p.owner_entity_id = $2::uuid AND p.valid_to IS NULL AND p.system_to IS "
                    "NULL AND p.superseded_by_source IS NULL", ORG_LIVE, ent, tk)
                if not rows:
                    find(f"{name}: no live {tk} line today (data changed) — not checked")
                    continue
                for r in rows:
                    found += 1
                    ok = r["id"] not in skipped and (r["id"] in uncl if r["k"] is None else True)
                    if not ok:
                        problems.append((name, tk, r["id"], r["k"]))
            find(f"{name}: card gross {cn['gross']}, of which Unclassified {cn['unclassified_value']}")
        check(bool(found > 0 and not problems), f"Muse's {found} unkeyed fund lines are COUNTED on their cards (listed "
              "under Unclassified while unkeyed; in a bucket once keyed)", f"{problems}")


async def y_scope(conn):
    section("[Y] Org isolation both directions; a member and support refused the admin reports")
    for path in (f"/api/v1/entities/{eid('p')}/net-worth", f"/api/v1/households/{HH1}/summary",
                 f"/api/v1/entities/{eid('p')}/ownership", f"/api/v1/entities/{eid('p')}/ownership/history",
                 f"/api/v1/entities/{eid('p')}/relationships"):
        c, _ = await api(U_OB_ADMIN, "GET", path)
        check(c in (403, 404), f"org B's admin on org A's {path.split('/api/v1')[1][:50]} → refused", f"{c}")
    # ROLLUP2 fix 1 — these three answered 200 with empty lists for another org's id; now 404 like net-worth, and
    # org A's own admin still reads them
    for path in (f"/api/v1/entities/{eid('p')}/ownership", f"/api/v1/entities/{eid('p')}/ownership/history",
                 f"/api/v1/entities/{eid('p')}/relationships"):
        cb, _ = await api(U_OB_ADMIN, "GET", path)
        ca, _ = await api(U_OA, "GET", path)
        check(bool(cb == 404 and ca == 200), f"{path.split('/api/v1')[1][:50]}: org B 404 (as a missing id), org A 200",
              f"B {cb} A {ca}")
    for path in ("/api/v1/admin/ownership/duplicates", "/api/v1/admin/fund-categories/proposals"):
        c, _ = await api(U_MEMBER, "GET", path)
        check(c == 403, f"a member is refused {path}", f"{c}")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a_a = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_A,
                       fetch="fetchval")
    a_b = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_A,
                       fetch="fetchval")
    b_b = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_B,
                       fetch="fetchval")
    b_a = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1", ORG_B,
                       fetch="fetchval")
    check(bool(a_a > 0 and b_b > 0 and a_b == 0 and b_a == 0), "RLS: each org reads its own positions, none of the "
          "other's", f"A {a_a}/{b_a} B {b_b}/{a_b}")
    async with _OrgWrite(conn, str(ORG_B)) as c:
        t = await HT.total(c, str(ORG_B), [str(eid("p"))])
    check(bool(t["gross"] == "0.00" and t["positions_counted"] == 0),
          "the core under org B's context counts nothing of org A's entity", f"{t['gross']}")


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
    code2, out2, _ = run_cmd(["node", "--test", "tests/rollup2/"], WEB_DIR, timeout=300)
    check(bool(code2 == 0 and re.search(r"^# fail 0", out2, re.M)), "the sprint's node tests (unclassified list, "
          "split, Owners list, 'via' label, duplicate warning, cash name) pass", out2[-200:] if code2 else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_bugfix4.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_bugfix4.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_bugfix4.py: 0 FAIL (its OWN final TOTAL line)",
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


async def add_liability():
    c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid('p')}/liabilities",
                     {"name": NAME + "Mortgage", "kind": "mortgage", "current_balance": "10000.00",
                      "as_of": AS_OF.isoformat()})
    check(c == 201, "fixture liability (10,000) recorded through the real route", f"{c} {dumps(b)}")


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
        section("[Y] Task 1 — findings 1a–1f")
        self_task1(check, find)
        section("[Y] Pure functions")
        await run("pure", self_pure, check, find)
        await run("reproduce", y_reproduce, conn)
        await run("liability", add_liability)
        await run("one rollup", y_one_rollup, conn)
        await run("crut", y_crut, conn)
        await run("split", y_split, conn)
        await run("funds", y_funds, conn)
        await run("duplicates", y_duplicates, conn)
        await run("cash names", y_cash_names, conn)
        await run("live", y_live, conn)
        await run("scope", y_scope, conn)
        section("[Y] Static — docs, regression set, code markers")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; demo + real unchanged")
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
