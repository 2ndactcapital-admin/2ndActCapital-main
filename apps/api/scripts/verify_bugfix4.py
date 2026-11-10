"""verify_bugfix4 — HollisMuse's Olivo final report (2026-11-06): held-away snapshots (one draft per account, supersede
labelled, the repair script), the household total (each position once; unclassified-but-valued lines counted), the
platform jobs' schedule switch, the entry form (no stale security, digital assets never matched to the master, cash
without a quantity), the household page (total + Recalculate), private-investment edits + the projection link,
fixed-point quantities and the Tax label, the explicit picker create, ONE role source, placeholder logins and
maker-checker refusals that name people, and the AGG / VTI over-count.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch and R2 are stubbed with their CURRENT signatures, and httpx's network transport refuses.

Everything after the bearer token is the real app against the live database, in THREE fixture orgs
(99000000-…-000000570001 / 570002 / 570003 — A, B and a fixture PLATFORM org for the jobs switch), ``is_test = true``
(asserted), as are the fixture users. Fixture securities in the platform master by reserved ids / tickers (B4V…).
The Olivo demo rows (tag demo-olivo) and every real row are only READ: count + checksum before and after; no real job
is switched (the real scheduled triggers are fingerprinted). Teardown at start and end via _verify_cleanup by fixture
id, through the documented privileged path for the append-only held-away tables (``execution.teardown``).

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

SPRINT_KEY = "bugfix4"
BEFORE_REF = "be14df0"   # bugfix4-work base: "merge bulkapprove1"

NEW_ROUTES = {
    ("GET", "/api/v1/held-away/accounts/{account_id}/snapshot"): "authz view_held_away",
    ("GET", "/api/v1/households/{household_id}/summary"): "internal + any(manage_members|manage_portfolio|view_replication)",
    ("POST", "/api/v1/admin/security-data/master/jobs/{job_key}/schedule"): "super_admin",
    ("PATCH", "/api/v1/private-investments/assets/{asset_id}"): "internal + any(manage_members)",
    ("GET", "/api/v1/private-investments/assets/{asset_id}/history"): "internal + any(manage_members)",
}
MUSE_IDS = ("MUSE-OLIVO-O-04-02", "MUSE-OLIVO-O-04-03", "MUSE-OLIVO-FORM", "MUSE-OLIVO-K19", "MUSE-OLIVO-HH",
            "MUSE-OLIVO-PI", "MUSE-OLIVO-DISPLAY", "MUSE-OLIVO-STUBS", "MUSE-OLIVO-IDENTITY", "MUSE-OLIVO-ROLES",
            "MUSE-OLIVO-AGG-VTI")

# ── Task 1 — needles from the discovery (docs/BUGFIX4_DISCOVERY.md) and the code they pin ───────────────────────
TASK1 = [
    ("docs/BUGFIX4_DISCOVERY.md", "## 1a. Held-away apply = an account SNAPSHOT (hypothesis CONFIRMED)",
     "1a: an approved import replaces EVERY live line of the account; 32 Olivo lines superseded across 13 accounts"),
    ("docs/BUGFIX4_DISCOVERY.md", "SCHR 5,220,000 + SCMB 882,000", "1b: the $7,545,600 is unclassified (no taxonomy "
     "key), not unpriced; and the household gross double-counted owned members"),
    ("docs/BUGFIX4_DISCOVERY.md", "`--activate-jobs`", "1c: triggers seeded inactive by design; the attributes run is HELD"),
    ("docs/BUGFIX4_DISCOVERY.md", "master by CUSIP", "1d: stale CUSIP → QQQ; ETH via master tickers; quantity required"),
    ("docs/BUGFIX4_DISCOVERY.md", "fired on **mousedown**", "1e: the employer picker's one-gesture create"),
    ("docs/BUGFIX4_DISCOVERY.md", "**Muse's exact figures are not reproducible from today's data**",
     "1f: live AGG / VTI rows are exact; the code's only inflating aggregation is the member double count"),
    ("docs/BUGFIX4_DISCOVERY.md", "**The approval check compares user ids**",
     "1g: culmina.io pre-seeded with a fake sub; the Hollisworks login provisioned a placeholder; role sources split"),
]
CODE_NEEDLES = [
    ("apps/api/services/held_away.py", 'NO_MASTER_KINDS = frozenset({"digital_asset", "cash"})',
     "Task 5: digital assets and cash never resolve through the master's identifiers"),
    ("apps/api/services/held_away.py", 'VALUE_ONLY_KINDS = frozenset({"cash", "private"})',
     "Task 5: cash / value-only lines take an amount, no quantity"),
    ("apps/api/services/held_away.py", "async def import_states(conn, org_id, account_id)",
     "Task 2: each import and line shows live / superseded"),
    ("apps/api/services/held_away.py", "def replaces_text(snap: dict, new_as_of: date | None = None)",
     "Task 2: a new draft says what it replaces, before and after it is saved"),
    ("apps/api/services/household_total.py", "def shares(members: set[str], edges",
     "Task 3 / 7: the household total counts each position once at the household's share"),
    ("apps/api/services/org_settings.py", '"equity": "taxonomy_sc_1",',
     "Task 3: master-resolved equities are classified (the master's unambiguous types)"),
    ("apps/api/services/direct_holdings.py", '"cash_holding": "Cash / working capital",',
     "Task 3 (K.19): a cash-kind direct asset, mapped to Cash & Equivalents"),
    ("apps/api/scripts/backfill_taxonomy_keys.py", "async def reclassify(conn, org: str, items",
     "Task 3 (K.19): the backfill reclassifies a named 'other' asset (operator)"),
    ("apps/api/services/security_master_jobs.py", "async def set_schedule(conn, *, job_key: str, active: bool",
     "Task 4: the schedule switch (audited)"),
    ("apps/api/services/authz.py", "is_super = rbac.SUPER_ADMIN_ROLE in (row[\"role_names\"] or [])",
     "Task 7: Super Admin decided from user_roles (ONE role source)"),
    ("apps/api/routers/admin.py", "async def _forbid_super_grant(conn, request: Request, *, target_id, role_name",
     "Task 7: only a Super Admin grants super_admin"),
    ("apps/api/services/maker_names.py", "def refusal_text(maker: dict | None, checker: dict | None",
     "Task 7: maker-checker refusals name the maker (and shared sign-ins)"),
    ("apps/web/components/EntityPicker.jsx", "setConfirmCreate(null); handleCreate();",
     "Task 7: the picker creates only from its confirmation"),
    ("apps/web/components/heldaway/HeldAwayPanel.jsx", "const rows = lines.map((l) => lineRow(l, v, manualTermFields, manualRow));",
     "Task 2: ONE draft per account with all its lines"),
    ("apps/web/components/households/HouseholdSummary.jsx", "export function SummaryView(",
     "Task 6: the household's own page shows its total and Recalculate"),
    # bugfix4 fix 1 (FAIL 1, code): one owner holding one security in two accounts is TWO holdings — keyed on
    # (owner, asset) alone the second account's line was dropped (household gross 35,000 for 45,000 held)
    ("apps/api/services/portfolio_rollup.py", "SELECT DISTINCT ON (p.owner_entity_id, p.asset_id, p.account_id)",
     "fix 1: the current-positions read keeps one line per ACCOUNT (the same security in two accounts counts twice)"),
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
    find("Task 1b — the brief's premise ('excluded for want of a price series') was wrong: the rollup values an "
         "entered market value first; the four lines had NO taxonomy key (master type 'fund' / 'equity' unmapped)")
    find("Task 1f — Muse's AGG 24,831,150 / VTI 15,848,700 are not reproducible; the live rows equal the file")


def self_static(check, find):
    for rel, needle, meaning in CODE_NEEDLES:
        check(needle in _read(rel), meaning, f"{rel}: needle missing")
    check("verify_bugfix4.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. BUGFIX4 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    missing = [i for i in MUSE_IDS if i not in top]
    check(bool(top and not missing and "repair_heldaway_snapshots.py" in top and "--activate" in top),
          "Task 9: PROJECT_STATUS entry — every Muse finding with its ID and the operator steps", f"missing {missing}")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if want.split(" ")[0] not in str((new.get(k) or {}).get("after") or "")]
    check(not wrong, "every new route carries its stated gate", f"{wrong}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_pure(check, find):
    """Pure functions, no DB: each shown to refuse the bad case as well as pass the good one. (Its imports are local
    on purpose: --self-checks-only reaches here BEFORE hydrate(); these modules are pure at import.)"""
    from datetime import date as _d
    from decimal import Decimal as D

    from services import held_away as HA
    from services import household_total as HTL
    from services import maker_names as MN
    from services import rbac
    from services.wash_sale import TAX_CLASS_LABELS

    s = HTL.shares({"p", "llc"}, [("p", "llc", D(60)), ("x", "llc", D(40)), ("llc", "sub", D(50))])
    check(bool(s.get("p") == 1 and s.get("llc") == 1 and s.get("sub") == D("0.5") and "x" not in s),
          "household share: members 1 (never 1.6 for an owned member), a non-member descendant its share", f"{s}")
    s2 = HTL.shares({"a"}, [("a", "b", D(80)), ("b", "a", D(80)), ("b", "c", D(100))])
    check(bool(s2.get("b") == D("0.8") and s2.get("c") == D("0.8")), "a cycle cannot inflate a share", f"{s2}")
    check(bool(HA.state_of("approved", 0, 2) == "superseded" and HA.state_of("approved", 1, 1) == "partly_live"
               and HA.state_of("approved", 3, 0) == "live" and HA.state_of("draft", 0, 0) == "draft"),
          "import state: superseded / partly live / live / draft")
    t = HA.replaces_text({"as_of": "2026-11-04", "live_lines": 3, "imports": [{}], "open_drafts": []},
                         _d(2026, 11, 4))
    check(bool(t and "replaces the earlier snapshot" in t and "same as-of" in t)
          and HA.replaces_text({"live_lines": 0, "imports": [], "open_drafts": []}) is None,
          "replaces text: named when there is a live snapshot, None when there is none", f"{t}")
    r = HA.validate_row({"asset_kind": "cash", "market_value": "1050000", "cusip": "46090E103"}, _d(2026, 11, 4))
    check(bool(r.get("reject") is None and r["quantity"] == D("1050000")), "cash with no quantity: accepted, "
          "quantity = amount", f"{r.get('reject')}")
    r2 = HA.validate_row({"asset_kind": "mutual_fund", "name": "x", "market_value": "5"}, _d(2026, 11, 4))
    check(bool(r2.get("reject") and r2["reject"][0] == "quantity_missing"), "a non-value-only line still needs a "
          "quantity", f"{r2.get('reject')}")
    check(bool(rbac.mirrored_role_string(["admin", "super_admin"]) == "super_admin"
               and rbac.mirrored_role_string(["org_admin", "member"]) == "org_admin"
               and rbac.mirrored_role_string([]) == "member"), "users.role mirrors the RBAC roles")
    me = {"id": "1", "name": "J (a@b.c)", "sign_ins": 2, "emails": ["a@b.c", "a@d.e"]}
    other = {"id": "2", "name": "K", "sign_ins": 1, "emails": ["k@x.y"]}
    t1 = MN.refusal_text(me, me, approver="Super Admin", item="import")
    t2 = MN.refusal_text(me, other, approver="Super Admin", item="import")
    check(bool("Entered by J (a@b.c)" in t1 and "a@b.c, a@d.e" in t1 and "same person" in t1
               and "Entered by J" in t2 and "same person" not in t2),
          "the refusal names the maker; it adds the shared sign-ins only when checker IS maker", f"{t1} | {t2}")
    check("IRA" not in TAX_CLASS_LABELS["tax_advantaged"], "the Tax class label no longer says IRA / Roth for every "
          "non-taxable account", TAX_CLASS_LABELS["tax_advantaged"])


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

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
import backfill_taxonomy_keys as BF  # noqa: E402
import repair_heldaway_snapshots as R  # noqa: E402
from services import households as HH  # noqa: E402
from services import rbac  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.authz import load_principal  # noqa: E402
from services.database import close_pool, platform_scope  # noqa: E402
from services.tax_overview import _tax_status_label  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "bugfix4verify"
NAME = "B4V "            # reserved fixture name prefix (asserted to match no real row)


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000057{n:04d}")


ORG_A, ORG_B, ORG_P = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_A, ORG_B, ORG_P]
SEC_EQ, SEC_FUND, SEC_ETH = _id(900), _id(901), _id(902)
FIX_SECS = [SEC_EQ, SEC_FUND, SEC_ETH]
TICKER = {SEC_EQ: "B4VEQ", SEC_FUND: "B4VFND", SEC_ETH: "B4VETH"}
FUND_CUSIP = "B4V00FND1"

ORGADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                            "manage_portfolio", "view_dashboard", "view_held_away", "manage_held_away",
                            "approve_held_away", "view_replication"})
SUPPORT_PERMS = frozenset({"view_dashboard", "view_members", "view_portfolio"})
U_OA, U_SUPPORT, U_MEMBER, U_SUPER, U_OB_ADMIN, U_SUPER_P = _id(12), _id(13), _id(14), _id(15), _id(16), _id(17)
U_PROMO, U_STUB, U_PRE, U_STRING = _id(18), _id(19), _id(20), _id(21)
USERS = {
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPPORT: (ORG_A, "support_staff", "support", "support_staff", SUPPORT_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_OB_ADMIN: (ORG_B, "org_admin", "obadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPER_P: (ORG_P, "super_admin", "superp", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_PROMO: (ORG_A, "member", "promo", "member", frozenset(C.MEMBER_PERMS)),
    U_STUB: (ORG_A, "member", "stub", "member", frozenset(C.MEMBER_PERMS)),
    U_PRE: (ORG_A, "member", "pre", "member", frozenset(C.MEMBER_PERMS)),
    U_STRING: (ORG_A, "super_admin", "string", "member", frozenset(C.MEMBER_PERMS)),  # string says super, grant does not
}
E = {
    "f1p": (_id(30), ORG_A, "individual", "F1 Principal"),
    "f1llc": (_id(31), ORG_A, "llc", "F1 Holdings LLC"),
    "ap": (_id(32), ORG_A, "individual", "AGG Principal"),
    "allc": (_id(33), ORG_A, "llc", "AGG Partnership"),
    "orgb": (_id(34), ORG_B, "individual", "Org B Person"),
}
HH1, HH_AGG, HH_B = _id(60), _id(61), _id(62)
HHS = [HH1, HH_AGG, HH_B]
ACC_PL, ACC_ONE, ACC_FORM, ACC_AGG1, ACC_AGG2, ACC_B = _id(70), _id(71), _id(72), _id(73), _id(74), _id(75)
TAXONOMY_SOURCE_ORG = UUID("00000000-0000-0000-0000-000000000001")   # read-only: its asset_taxonomy rows are copied
AS_OF = date(2026, 11, 4)

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.households", "public.household_memberships", "public.accounts", "public.entity_holdings",
           "public.delegate_grants", "public.role_permissions", "public.user_roles", "public.user_identities",
           "public.held_away_imports", "public.held_away_records", "public.held_away_sources",
           "public.workflow_definitions", "public.workflow_triggers",
           "portfolio.assets", "portfolio.positions", "portfolio.valuations", "portfolio.commitments",
           "portfolio.transactions", "portfolio.tax_lots", "portfolio.rollup_requests",
           "portfolio.securities_global", "portfolio.securities_global_identifiers")
#: NOT counted, by name: public.audit_log and public.member_todos — real activity writes them during a run.
REAL_FINGERPRINTED = ("public.entities", "public.households", "public.household_memberships", "public.accounts",
                      "public.held_away_imports", "public.held_away_records", "public.workflow_triggers",
                      "portfolio.assets", "portfolio.positions", "portfolio.valuations", "portfolio.commitments",
                      "portfolio.tax_lots", "public.user_roles", "public.user_identities")

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
    if uid == U_STUB:
        return f"{sub_of(uid)}@placeholder.local"
    return f"{TAG}-{USERS[uid][2]}@test.local"


def eid(k: str) -> UUID:
    return E[k][0]


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:600]


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


#: columns real activity updates, excluded by name from a fingerprint: workflow_triggers.last_fired_at (the scheduler
#: stamps it on any active real trigger), user_identities none.
EXCLUDED_COLS = {"public.workflow_triggers": ("last_fired_at", "next_fire_at", "updated_at")}


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
    if table == "public.user_roles":
        return "user_id NOT IN (SELECT id FROM public.users WHERE org_id = ANY($1::uuid[]))"
    return "NOT (org_id = ANY($1::uuid[]))"


async def real_prints(conn) -> dict:
    out = {t: await fingerprint(conn, t, where=_real_where(t), args=(FIXTURE_ORGS,)) for t in REAL_FINGERPRINTED}
    out["portfolio.securities_global"] = await fingerprint(conn, "portfolio.securities_global",
                                                           where="NOT (id = ANY($1::uuid[]))", args=(FIX_SECS,))
    return out


DEMO = "SELECT id FROM public.entities WHERE 'demo-olivo' = ANY(tags)"
DEMO_SQL = {
    "entities": (f"SELECT * FROM public.entities WHERE 'demo-olivo' = ANY(tags)", "id, system_from"),
    "positions": (f"SELECT * FROM portfolio.positions WHERE owner_entity_id IN ({DEMO})", "id"),
    "assets": (f"SELECT * FROM portfolio.assets WHERE id IN (SELECT asset_id FROM portfolio.positions WHERE "
               f"owner_entity_id IN ({DEMO}))", "id, system_from"),
    "commitments": (f"SELECT * FROM portfolio.commitments WHERE position_id IN (SELECT id FROM portfolio.positions "
                    f"WHERE owner_entity_id IN ({DEMO}))", "id"),
    "held_away_records": (f"SELECT * FROM public.held_away_records WHERE account_id IN (SELECT id FROM "
                          f"public.accounts WHERE primary_entity_id IN ({DEMO}))", "id"),
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
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [FIX_SECS]),
        "portfolio.securities_global": ("id = ANY($1::uuid[])", [FIX_SECS]),
    }


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only, by fixture id — the documented privileged path (operator DSN + execution.teardown,
    transaction-local) for the append-only held-away import / record tables (rep7_part1.sql)."""
    op = await operator_conn()
    try:
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               gucs=("execution.teardown",), label="verify_bugfix4")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [HHS]),
            ("SELECT count(*) FROM portfolio.securities_global WHERE id = ANY($1::uuid[])", [FIX_SECS])])
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
CATALOG_TABLES = ("public.accounts", "public.households", "public.held_away_imports", "public.held_away_records",
                  "public.user_identities", "public.user_roles", "public.workflow_triggers", "portfolio.positions",
                  "portfolio.assets", "portfolio.commitments", "portfolio.securities_global",
                  "portfolio.securities_global_identifiers", "public.entity_relationships")


def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


async def y_catalog(conn):
    section("[Y] Constraints read from the catalog before seeding; reserved fixture prefixes match no real row")
    cons = {}
    for t in CATALOG_TABLES:
        for r in await platform(conn, "SELECT conname, contype::text AS k, pg_get_constraintdef(oid) AS d FROM "
                                      "pg_constraint WHERE conrelid = $1::regclass AND contype::text IN ('p','c','u',"
                                      "'f')", t):
            cons[(t, r["conname"])] = (r["k"], r["d"])
    kinds = {k for k, _ in cons.values()}
    check(bool(cons and kinds <= {"p", "c", "u", "f"} and "p" in kinds), f"{len(cons)} PK / CHECK / UNIQUE / FK "
          "constraints read (decoded as text)", f"{sorted(kinds)}")
    for col in ("registration_type", "tax_status", "custodian_code"):
        defs = [d for (t, _n), (k, d) in cons.items() if t == "public.accounts" and col in d and k == "c"]
        vals = _allowed(defs[0]) if defs else []
        ACCOUNT_VALUES[col] = vals[0] if vals else {"registration_type": "individual", "tax_status": "taxable",
                                                    "custodian_code": "ALTRUIST"}[col]
    find("accounts seeded with catalog-legal values", f"{ACCOUNT_VALUES}")
    li = [d for (t, _n), (k, d) in cons.items() if t == "public.user_identities" and k == "c" and "linked_via" in d]
    check(bool(not li or "operator" in _allowed(li[0])), "user_identities.linked_via accepts 'operator'", f"{li}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM portfolio.assets WHERE name LIKE $1 AND NOT "
                                "(org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND "
                                "NOT (id = ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 "
                                "AND NOT (id = ANY($4::uuid[]))) + (SELECT count(*) FROM "
                                "portfolio.securities_global_identifiers WHERE id_value LIKE 'B4V%' AND NOT "
                                "(global_security_id = ANY($6::uuid[]))) + (SELECT count(*) FROM "
                                "portfolio.securities_global WHERE name LIKE $1 AND NOT (id = ANY($6::uuid[])))",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), FIX_SECS, fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (names, subs, slugs, B4V tickers / CUSIP) match no real row",
          f"{real}")


ROLE_IDS: dict[tuple, UUID] = {}


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b"), (ORG_P, "p")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"BUGFIX4 Verify Org {nm.upper()}", f"{TAG}-{nm}")
            await conn.execute(
                "INSERT INTO config (org_id, config_key, config_value, value_type, category, display_order, "
                "is_active) SELECT $1, config_key, config_value, value_type, category, display_order, true FROM config "
                "WHERE org_id = $2 AND category = 'asset_taxonomy' AND is_active", org, TAXONOMY_SOURCE_ORG)
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        role_perms = {"org_admin": ORGADMIN_PERMS, "support_staff": SUPPORT_PERMS,
                      "member": frozenset(C.MEMBER_PERMS), "super_admin": frozenset(C.SUPER_ADMIN_PERMS)}
        for org in FIXTURE_ORGS:
            for rname, perms in role_perms.items():
                rid = await rbac.ensure_role(conn, org, rname, f"BUGFIX4 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                ROLE_IDS[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            sub = None if uid == U_PRE else sub_of(uid)
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test, "
                "contact_email) VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true, $7)",
                uid, org, email_of(uid), f"BUGFIX4 {nm.title()}", sub, account_role,
                f"{TAG}-stub-contact@test.local" if uid == U_STUB else None)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, ROLE_IDS[(org, rname)])
        for k, (i, org, etype, name) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) "
                               "VALUES ($1, $2, $3, 'active', $4::entity_type)", i, org, NAME + name, etype)
        rel = ("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
               "ownership_pct) VALUES ($1, $2, $3, 'ownership', $4)")
        await conn.execute(rel, ORG_A, eid("f1p"), eid("f1llc"), 100)
        await conn.execute(rel, ORG_A, eid("ap"), eid("allc"), 60)       # AGG: the principal owns 60 % of the LP
        for hh, org, nm in ((HH1, ORG_A, "F1"), (HH_AGG, ORG_A, "AGG"), (HH_B, ORG_B, "B")):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org, NAME + nm)
        for hh, k in ((HH1, "f1p"), (HH1, "f1llc"), (HH_AGG, "ap"), (HH_AGG, "allc"), (HH_B, "orgb")):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh,
                               eid(k))
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, eid("f1p"), U_MEMBER)
        for aid, mask, owner, hh, org in ((ACC_PL, "****5701", "f1p", HH1, ORG_A), (ACC_ONE, "****5702", "f1p", HH1, ORG_A),
                                          (ACC_FORM, "****5703", "f1llc", HH1, ORG_A),
                                          (ACC_AGG1, "****5704", "ap", HH_AGG, ORG_A),
                                          (ACC_AGG2, "****5705", "allc", HH_AGG, ORG_A),
                                          (ACC_B, "****5706", "orgb", HH_B, ORG_B)):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, household_id, account_number_masked, account_number_hash, "
                "custodian_code, registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, $3, "
                "$4, $5, $6, $7, $8, $9, true)", aid, org, hh, mask,
                hashlib.sha256(f"{TAG}{mask}".encode()).hexdigest(), ACCOUNT_VALUES["custodian_code"],
                ACCOUNT_VALUES["registration_type"], ACCOUNT_VALUES["tax_status"], eid(owner))
        for sid, stype, nm in ((SEC_EQ, "equity", "Fixture Equity"), (SEC_FUND, "fund", "Fixture Bond Fund ETF"),
                               (SEC_ETH, "fund", "Fixture Ethereum Mini Trust ETF")):
            await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, canonical_id) "
                               "VALUES ($1, $2, $3, $1)", sid, NAME + nm, stype)
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1, 'ticker', $2, true)", sid, TICKER[sid])
        await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value) "
                           "VALUES ($1, 'cusip', $2)", SEC_FUND, FUND_CUSIP)


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
S: dict = {}


def token_for_sub(sub: str, org: UUID) -> str:
    key = f"t-{TAG}-{sub}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub, "org_id": str(org)}
    return key


async def api(who, method, path, body=None, timeout=180.0):
    """``who`` = a fixture user id, or (sub, org) for a raw sign-in."""
    import main

    tok = token_for_sub(*who) if isinstance(who, tuple) else token_for_sub(sub_of(who), USERS[who][0])
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
        raise AssertionError(f"verify_bugfix4: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    # the jobs switch acts on THIS script's fixture platform org — never the real one
    J.PLATFORM_ORG_ID = ORG_P
    J.PLATFORM_JOB_ORG_IDS.add(ORG_P)


async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


# ── helpers on the held-away API ────────────────────────────────────────────────────────────────────────────────
async def draft(acc, rows, who=U_OA):
    return await api(who, "POST", f"/api/v1/held-away/accounts/{acc}/imports/manual",
                     {"as_of": AS_OF.isoformat(), "rows": rows})


async def approve(imp_id, who=U_SUPER):
    return await api(who, "POST", f"/api/v1/held-away/imports/{imp_id}/approve", {"note": "verify"})


async def live_positions(conn, acc) -> list:
    return await scoped(conn, ORG_A, "SELECT p.id, p.asset_id, p.quantity, p.market_value, p.taxonomy_key, "
                                     "a.global_security_id, a.default_taxonomy_key FROM portfolio.positions p JOIN "
                                     "portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL WHERE "
                                     "p.account_id = $1 AND p.valid_to IS NULL AND p.system_to IS NULL", acc)


async def panel_imports(acc) -> dict:
    c, b = await api(U_OA, "GET", f"/api/v1/held-away/accounts/{acc}")
    return {i["id"]: i for i in (b.get("imports") or [])} if c == 200 else {}


# ═══ Task 2 — one draft per account; supersede labelled; repair ═════════════════════════════════════════════════
LINE_EQ = {"ticker": TICKER[SEC_EQ], "quantity": "100", "market_value": "10000"}
LINE_FUND = {"ticker": TICKER[SEC_FUND], "quantity": "200", "market_value": "20000"}
LINE_CASH = {"asset_kind": "cash", "market_value": "5000"}           # NO quantity: Task 5


async def y_snapshot(conn):
    section("[Y] Task 2 — before: three single-line approvals leave ONE live line; after: one draft, three lines")
    ids = []
    for row in (LINE_EQ, LINE_FUND, LINE_CASH):
        c, b = await draft(ACC_PL, [row])
        ok = c == 201 and b.get("rows_rejected") == 0
        c2, b2 = await approve(b.get("id")) if ok else (None, None)
        check(bool(ok and c2 == 200), f"single-line draft {row.get('ticker') or 'cash'} saved and approved by a "
              "second person", f"{c} {dumps(b)} / {c2} {dumps(b2)}")
        ids.append(b.get("id"))
    S["pl_imports"] = ids
    live = await live_positions(conn, ACC_PL)
    check(len(live) == 1, "REPRODUCED (O-04-02): three approved single-line drafts → ONE live line (snapshot)",
          f"{len(live)} live")
    imps = await panel_imports(ACC_PL)
    st = [(imps.get(i) or {}).get("state") for i in ids]
    by = [(imps.get(i) or {}).get("superseded_by") for i in ids]
    check(bool(st == ["superseded", "superseded", "live"] and by[0] == [ids[1]] and by[1] == [ids[2]]),
          "each import now SAYS it is superseded, and by which import (it used to read only 'Approved')",
          f"{st} {by}")
    c, b = await draft(ACC_ONE, [LINE_EQ, LINE_FUND, {**LINE_CASH},
                                 {"ticker": TICKER[SEC_EQ], "quantity": "3250000", "market_value": "1"}])
    check(bool(c == 201 and b.get("rows_rejected") == 0 and b.get("replaces") is None
               and len((b.get("payload") or {}).get("positions") or []) == 3),
          "ONE draft carries every line (two lines of the same security sum into one position); nothing to replace "
          "yet", f"{c} {dumps(b)}")
    c2, _ = await approve(b.get("id"))
    live = await live_positions(conn, ACC_ONE)
    check(bool(c2 == 200 and len(live) == 3), "after: one approved draft with three holdings → three live lines",
          f"{c2} {len(live)}")
    S["one_import"] = b.get("id")
    cs, snap = await api(U_OA, "GET", f"/api/v1/held-away/accounts/{ACC_ONE}/snapshot?as_of={AS_OF.isoformat()}")
    check(bool(cs == 200 and (snap.get("snapshot") or {}).get("live_lines") == 3
               and "replaces the earlier snapshot" in (snap.get("replaces") or "")
               and "same as-of" in (snap.get("replaces") or "")),
          "BEFORE saving a second draft, the snapshot read says it replaces the earlier one (3 live lines)",
          f"{cs} {dumps(snap)}")
    c3, b3 = await draft(ACC_ONE, [LINE_EQ])
    check(bool(c3 == 201 and "replaces the earlier snapshot" in (b3.get("replaces") or "")),
          "the replacing draft is labelled 'replaces the earlier snapshot' (never silent)", f"{c3} {dumps(b3)}")
    imps = await panel_imports(ACC_ONE)
    check("replaces the earlier snapshot" in ((imps.get(b3.get("id")) or {}).get("replaces") or ""),
          "the panel shows the draft's replace warning too")
    c4, _ = await approve(b3.get("id"))
    imps = await panel_imports(ACC_ONE)
    old = imps.get(S["one_import"]) or {}
    check(bool(c4 == 200 and old.get("state") == "superseded" and old.get("superseded_by") == [b3.get("id")]
               and old.get("live_lines") == 0 and old.get("superseded_lines") == 3),
          "on approval the earlier snapshot is marked superseded (its 3 lines), by the replacing import",
          f"{c4} {dumps(old)}")
    S["one_replacer"] = b3.get("id")


async def y_repair(conn):
    section("[Y] Task 2 — repair_heldaway_snapshots (fixture): dry run lists, real run restores once, re-run nothing")
    before = await live_positions(conn, ACC_PL)
    plan = await R.plan(conn, str(ORG_A))
    p = plan.get(str(ACC_PL)) or {}
    check(bool(len(p.get("restore") or []) == 2 and {x["market_value"] for x in p["restore"]} == {"10000", "20000"}),
          "dry run: the account's two superseded single-line drafts are listed with their values",
          f"{dumps({k: (len(v['restore']), v['collisions']) for k, v in plan.items()})}")
    check(str(ACC_ONE) not in plan, "a deliberate full re-entry (a 3-line snapshot replaced) is NOT a repair "
          "candidate", f"{list(plan)}")
    after_dry = await live_positions(conn, ACC_PL)
    check(len(after_dry) == len(before) == 1, "the dry run wrote nothing", f"{len(before)} → {len(after_dry)}")
    n = await R.apply(conn, str(ORG_A), {str(ACC_PL): p})
    live = await live_positions(conn, ACC_PL)
    tot = sum((r["market_value"] for r in live), Decimal(0))
    check(bool(n == 2 and len(live) == 3 and tot == Decimal("35000")),
          "real run: the union of the account's approved lines is live (3 lines, 35,000)", f"{n} {len(live)} {tot}")
    plan2 = await R.plan(conn, str(ORG_A))
    p2 = plan2.get(str(ACC_PL)) or {}
    n2 = await R.apply(conn, str(ORG_A), plan2)
    live2 = await live_positions(conn, ACC_PL)
    check(bool(n2 == 0 and not p2.get("restore") and p2.get("already") == 2 and len(live2) == 3),
          "second run: nothing to restore, CHANGED 0 (idempotent)", f"{n2} {dumps({k: v for k, v in p2.items() if k != 'restore'})}")
    aud = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE action = 'held_away.snapshot_repaired' AND "
                                    "resource_id = $1", ACC_PL, fetch="fetchval")
    check(aud == 1, "the repair is audited once per account repaired", f"{aud}")
    imps = await panel_imports(ACC_PL)
    st = [(imps.get(i) or {}).get("state") for i in S["pl_imports"]]
    check(st == ["partly_live", "partly_live", "live"], "the restored lines read live again on their imports",
          f"{st}")


# ═══ Task 3 + 7 — roll-up values what it has; household total once; AGG / VTI ══════════════════════════════════
async def y_rollup(conn):
    section("[Y] Task 3 — unpriced listed line valued at its entered value; roll-up == X-ray; cash direct asset counts")
    live = await live_positions(conn, ACC_PL)
    eq = next((r for r in live if r["global_security_id"] == SEC_EQ), None)
    fund = next((r for r in live if r["global_security_id"] == SEC_FUND), None)
    check(bool(eq and (eq["taxonomy_key"] or eq["default_taxonomy_key"]) == "taxonomy_sc_1"),
          "a master-resolved EQUITY line is classified (it used to be unkeyed and skipped)", f"{dict(eq) if eq else None}")
    check(bool(fund and not (fund["taxonomy_key"] or fund["default_taxonomy_key"])),
          "a master FUND stays unclassified (equity and bond funds share the type) — counted, not guessed",
          f"{dict(fund) if fund else None}")
    c, b = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1llc')}/direct-assets", {
        "kind": "cash_holding", "name": NAME + "Working capital", "valuation": {"value": "1400000",
                                                                                "date": AS_OF.isoformat(),
                                                                                "method": "amortized_cost"}})
    check(c == 201, "a cash / working-capital direct asset is a kind (K.19)", f"{c} {dumps(b)}")
    key = await scoped(conn, ORG_A, "SELECT default_taxonomy_key FROM portfolio.assets WHERE id = $1",
                       UUID(b["asset_id"]) if c == 201 else UUID(int=0), fetch="fetchval")
    check(key == "taxonomy_mc_8_1", "the cash direct asset maps to Cash & Equivalents", f"{key}")
    cr, rr = await api(U_OA, "POST", f"/api/v1/admin/households/{HH1}/recalculate")
    cs, sm = await api(U_OA, "GET", f"/api/v1/households/{HH1}/summary")
    t = sm.get("total") or {}
    cx, xr = await api(U_OA, "GET", f"/api/v1/xray/households/{HH1}")
    xt = ((xr.get("xray") or {}).get("total_market_value")) if cx == 200 else None
    accounts_value = await scoped(conn, ORG_A, "SELECT COALESCE(sum(p.market_value), 0) FROM portfolio.positions p "
                                               "JOIN accounts a ON a.id = p.account_id WHERE a.household_id = $1 AND "
                                               "p.valid_to IS NULL AND p.system_to IS NULL AND p.quantity <> 0", HH1,
                                  fetch="fetchval")
    check(bool(cs == 200 and cx == 200 and xt is not None
               and Decimal(t.get("gross") or "0") - Decimal("1400000") == Decimal(xt) == accounts_value),
          "household total (less the off-account cash) == the X-ray total == independent SQL over the accounts",
          f"{cs}/{cx} gross {t.get('gross')} xray {xt} sql {accounts_value}")
    check(bool(Decimal(t.get("unclassified_value") or "0") >= Decimal("20000")
               and (t.get("by_basis") or {}).get("entered_value")),
          "the unpriced, unclassified fund line is IN the total, labelled 'entered value' and reported unclassified",
          f"{dumps(t)}")
    cash_bucket = await scoped(conn, ORG_A, "SELECT COALESCE(sum(market_value), 0) FROM entity_holdings WHERE "
                                            "org_id = $1 AND entity_id = $2 AND taxonomy_key = 'taxonomy_mc_8_1'",
                               ORG_A, eid("f1llc"), fetch="fetchval")
    check(bool(cr == 200 and cash_bucket >= Decimal("1400000")), "after Recalculate the roll-up counts the cash "
          "direct asset (Cash & Equivalents bucket)", f"{cr} {cash_bucket}")
    find("Task 3 — no free end-of-day price source exists, so 'a priced position uses the price' has nothing to read "
         "yet: every line is valued at its entered value (labelled), the X-ray's basis")
    # the backfill's operator reclassify (K.19): an 'other' asset → cash_holding, dry run / real / second run
    c, o = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/direct-assets", {
        "kind": "other", "name": NAME + "Other cash", "valuation": {"value": "1000", "date": AS_OF.isoformat(),
                                                                   "method": "appraisal"}})
    aid = o.get("asset_id") if c == 201 else None
    n0 = await BF.reclassify(conn, str(ORG_A), [(aid, "cash_holding")], dry_run=True) if aid else -1
    k0 = await scoped(conn, ORG_A, "SELECT asset_type FROM portfolio.assets WHERE id = $1 AND system_to IS NULL",
                      UUID(aid), fetch="fetchval") if aid else None
    n1 = await BF.reclassify(conn, str(ORG_A), [(aid, "cash_holding")], dry_run=False) if aid else -1
    k1 = await scoped(conn, ORG_A, "SELECT asset_type || '/' || default_taxonomy_key FROM portfolio.assets WHERE id = "
                                   "$1 AND system_to IS NULL", UUID(aid), fetch="fetchval") if aid else None
    n2 = await BF.reclassify(conn, str(ORG_A), [(aid, "cash_holding")], dry_run=False) if aid else -1
    check(bool(n0 == 0 and k0 == "other" and n1 == 1 and k1 == "cash_holding/taxonomy_mc_8_1" and n2 == 0),
          "backfill --reclassify (K.19 shape): dry run changes nothing, real run sets kind + key, re-run UNCHANGED",
          f"{n0} {k0} {n1} {k1} {n2}")


async def y_agg(conn):
    section("[Y] Task 7 — AGG / VTI: a member owning a member is counted ONCE (reproduce, then exact)")
    for acc, qty, mv in ((ACC_AGG1, "5000", "500000"), (ACC_AGG2, "10000", "1000000")):
        c, b = await draft(acc, [{"ticker": TICKER[SEC_EQ], "quantity": qty, "market_value": mv}])
        c2, _ = await approve(b.get("id")) if c == 201 else (None, None)
        check(bool(c == 201 and c2 == 200), f"AGG fixture line {mv} approved", f"{c} {c2}")
    cr, _ = await api(U_OA, "POST", f"/api/v1/admin/households/{HH_AGG}/recalculate")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        old = (await HH._sum_holdings(conn, str(ORG_A), [str(eid("ap")), str(eid("allc"))], None))["total_value"]
    check(old == Decimal("2100000"), "REPRODUCED: the old member-sum counts the LP once itself and again as 60 % of "
          "its owner (2,100,000 for 1,500,000 held)", f"{cr} {old}")
    sql = await scoped(conn, ORG_A, "SELECT sum(market_value) FROM portfolio.positions WHERE account_id = ANY($1::uuid[]) "
                                    "AND valid_to IS NULL AND system_to IS NULL", [ACC_AGG1, ACC_AGG2],
                       fetch="fetchval")
    cs, sm = await api(U_OA, "GET", f"/api/v1/households/{HH_AGG}/summary")
    t = sm.get("total") or {}
    top = (t.get("top_holdings") or [{}])[0]
    check(bool(cs == 200 and sql == Decimal("1500000") and t.get("gross") == "1500000.00"
               and top.get("key") == str(SEC_EQ) and top.get("value") == "1500000.00" and top.get("positions") == 2),
          "the household total and its top holding aggregate EXACTLY (1,500,000 = independent SQL)",
          f"{cs} sql {sql} {dumps(t)}")
    cx, xr = await api(U_OA, "GET", f"/api/v1/xray/households/{HH_AGG}")
    check(bool(cx == 200 and Decimal((xr.get("xray") or {}).get("total_market_value") or "0") == Decimal("1500000")),
          "the X-ray agrees (1,500,000)", f"{cx}")
    cd, d = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_AGG}")
    check(bool(cd == 200 and (d.get("net_worth") or {}).get("gross") == "1500000.00"),
          "the People & Access household card reads the same corrected total", f"{cd} {dumps(d.get('net_worth'))}")


# ═══ Task 4 — jobs switch ═══════════════════════════════════════════════════════════════════════════════════════
async def y_jobs(conn):
    section("[Y] Task 4 — the jobs switch: Super Admin only, audited, the fixture platform org only")
    async with platform_scope(conn):
        seeded = await J.ensure_platform_jobs(conn, org_id=ORG_P, created_by=U_SUPER_P, active=False)
    check(len(seeded) == len(J.JOBS), "the fixture platform org has the four jobs, triggers inactive", f"{len(seeded)}")
    key = "security_master_13f"
    body = {"active": True}
    co, bo = await api(U_OA, "POST", f"/api/v1/admin/security-data/master/jobs/{key}/schedule", body)
    act0 = await platform(conn, "SELECT is_active FROM workflow_triggers WHERE id = $1", seeded[key]["trigger_id"],
                          fetch="fetchval")
    check(bool(co == 403 and act0 is False), "an org admin is refused (identical request) and nothing changed",
          f"{co} {dumps(bo)} {act0}")
    cs, bs = await api(U_SUPER, "POST", f"/api/v1/admin/security-data/master/jobs/{key}/schedule", body)
    act1 = await platform(conn, "SELECT is_active FROM workflow_triggers WHERE id = $1", seeded[key]["trigger_id"],
                          fetch="fetchval")
    aud = await platform(conn, "SELECT payload FROM audit_log WHERE org_id = $1 AND action = "
                               "'security_master.schedule_switched' ORDER BY created_at", ORG_P)
    check(bool(cs == 200 and act1 is True and (bs.get("schedule") or {}).get("was") is False and len(aud) == 1),
          "a Super Admin switches it on — the trigger is active (re-read), one audit row", f"{cs} {dumps(bs)} {aud}")
    cf, _ = await api(U_SUPER, "POST", f"/api/v1/admin/security-data/master/jobs/{key}/schedule", {"active": False})
    act2 = await platform(conn, "SELECT is_active FROM workflow_triggers WHERE id = $1", seeded[key]["trigger_id"],
                          fetch="fetchval")
    check(bool(cf == 200 and act2 is False), "and off again", f"{cf} {act2}")
    cj, bj = await api(U_SUPER, "GET", "/api/v1/admin/security-data/master/jobs")
    jobs = {j["key"]: j for j in (bj.get("jobs") or [])}
    check(bool(cj == 200 and all(jobs.get(k, {}).get("fetches") for k in J.JOBS) and "price" in (bj.get("price_gap_note") or "")
               and "switch_schedule" in (bj.get("actions") or [])),
          "the jobs screen says what each job fetches and that no free EOD price source exists", f"{cj}")
    cm, _ = await api(U_OA, "GET", "/api/v1/admin/security-data/master/jobs")
    check(cm == 403, "an org admin cannot read the jobs screen", f"{cm}")


# ═══ Task 5 — the entry form ════════════════════════════════════════════════════════════════════════════════════
async def y_form(conn):
    section("[Y] Task 5 — stale security impossible; a digital asset never matches a master ETF; cash needs no qty")
    c, b = await draft(ACC_FORM, [
        {"asset_kind": "cash", "cusip": FUND_CUSIP, "ticker": TICKER[SEC_FUND], "market_value": "1050000"},
        {"asset_kind": "digital_asset", "name": NAME + "Ether", "ticker": TICKER[SEC_ETH], "quantity": "120",
         "market_value": "393600"},
        {"ticker": TICKER[SEC_ETH], "quantity": "10", "market_value": "1000"}])
    pos = {p.get("label"): p for p in ((b.get("payload") or {}).get("positions") or [])} if c == 201 else {}
    sids = [p["security_id"] for p in pos.values()]
    cash = next((p for p in pos.values() if str(p["security_id"]).startswith("cash:")), None)
    eth = next((p for p in pos.values() if p.get("label") == NAME + "Ether"), None)
    listed = next((p for p in pos.values() if p["security_id"] == str(SEC_ETH)), None)
    check(bool(c == 201 and b.get("rows_rejected") == 0 and cash and str(SEC_FUND) not in sids),
          "a cash line carrying the previous line's CUSIP / ticker resolves to the org's cash, never the security",
          f"{c} {dumps(b.get('payload'))}")
    check(bool(eth and str(eth["security_id"]).startswith("manual:") and listed),
          "'ETH' as a digital asset → an org manual asset; the SAME ticker as a listed line → the master ETF "
          "(so the ticker IS in the master — the refusal is the kind's)", f"{dumps(eth)} / {dumps(listed)}")
    check(bool(cash and cash.get("quantity") == "1050000" and cash.get("market_value") == "1050000"),
          "cash entered as an amount: no quantity typed, stored quantity = amount", f"{dumps(cash)}")
    S["form_import"] = b.get("id")


# ═══ Task 6 — household page, private investments, display ═════════════════════════════════════════════════════
async def y_household_page(conn):
    section("[Y] Task 6 — household total + Recalculate on its own page; private-investment edit; display")
    cs, sm = await api(U_OA, "GET", f"/api/v1/households/{HH1}/summary")
    t = sm.get("total") or {}
    check(bool(cs == 200 and "recalculate" in (sm.get("actions") or [])
               and Decimal(t["gross"]) - Decimal(t["liabilities"]) == Decimal(t["net"]) and t.get("holdings_as_of")),
          "the org admin reads gross / liabilities / net / as-of and is offered Recalculate", f"{cs} {dumps(sm)}")
    cr, rr = await api(U_OA, "POST", f"/api/v1/admin/households/{HH1}/recalculate")
    check(bool(cr == 200 and (rr.get("net_worth") or {}).get("gross") == t.get("gross")),
          "Recalculate returns the same total", f"{cr} {dumps(rr.get('net_worth'))}")
    c, hf = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/private-investments", {
        "kind": "hedge_fund", "name": NAME + "Macro HF", "amount_invested": "250000", "vintage_year": 2019,
        "investment_date": "2019-05-01", "nav": {"value": "300000", "date": AS_OF.isoformat()}})
    check(c == 201, "a hedge fund (simplified kind) saved with its vintage and investment date", f"{c} {dumps(hf)}")
    cf, fd = await api(U_OA, "POST", f"/api/v1/entities/{eid('f1p')}/private-investments", {
        "kind": "fund", "name": NAME + "Growth Fund", "commitment": {"amount": "1000000", "date": "2021-03-01",
                                                                    "vintage_year": 2021},
        "nav": {"value": "200000", "date": AS_OF.isoformat()}})
    check(cf == 201, "a fund with its commitment", f"{cf} {dumps(fd)}")
    cl, lst = await api(U_OA, "GET", f"/api/v1/entities/{eid('f1p')}/private-investments")
    rows = {r["name"]: r for r in (lst.get("rows") or [])}
    h = rows.get(NAME + "Macro HF") or {}
    f = rows.get(NAME + "Growth Fund") or {}
    check(bool(h.get("vintage_year") == 2019 and h.get("investment_date") == "2019-05-01" and h.get("actions") == ["edit"]),
          "the simplified kind shows its vintage + date; the row offers Edit to the org admin", f"{dumps(h)}")
    check(bool(f.get("projection_path") == f"/portfolio/commitments/{f.get('commitment_id')}"),
          "the commitment row links to its capital-call projection", f"{dumps(f)}")
    # bugfix4 fix 1 (FAIL 2, verify): the bare GET is the DESIGNED 422 "no TA parameter override set — pass
    # ?strategy_key=<…>" (a new commitment has no calibration yet; verify_tamodel3 3.1 pins that 422 as the contract
    # that drives the page's strategy picker). So the link never leads to an error: either a 200 projection, or that
    # 422 naming the missing input AND the page renders the picker for it — and the SAME commitment projects 200 once
    # a strategy is picked (both halves, never only the status code).
    from services.ta_config import TA_STRATEGY_KEYS
    cp, pb = await api(U_OA, "GET", f"/api/v1/modeling/ta/projection/{f.get('commitment_id')}")
    web = pathlib.Path(__file__).resolve().parents[2] / "web"
    page_src = (web / "app/portfolio/commitments/[commitmentId]/page.js").read_text(encoding="utf-8")
    scr_src = (web / "components/portfolio/CommitmentProjectionScreen.jsx").read_text(encoding="utf-8")
    picker = ("error?.status === 422" in page_src and "strategy_defaults" in page_src
              and "loadError?.status === 422 && !projection" in scr_src and "<StrategyPicker" in scr_src)
    check(bool(cp == 200 or (cp == 422 and "strategy_key" in dumps(pb) and picker)),
          "the projection link opens a projection, or the clear 'needs a strategy' state (the API names "
          "strategy_key; the page renders the strategy picker for exactly that 422) — never an error",
          f"{cp} {dumps(pb)} picker={picker}")
    cq, qb = await api(U_OA, "GET", f"/api/v1/modeling/ta/projection/{f.get('commitment_id')}"
                                    f"?strategy_key={TA_STRATEGY_KEYS[0]}")
    check(bool(cq == 200 and qb.get("commitment_id") == f.get("commitment_id") and len(qb.get("periods") or []) > 0
               and Decimal(str(qb.get("committed_capital") or "0")) == Decimal("1000000")),
          "with a strategy picked, the SAME new commitment projects (200, its periods, its 1,000,000 committed)",
          f"{cq} {str(dumps(qb))[:300]}")
    ce, e1 = await api(U_OA, "PATCH", f"/api/v1/private-investments/assets/{h.get('asset_id')}",
                       {"notes": "B4V note", "vintage_year": 2020})
    hist_c, hist = await api(U_OA, "GET", f"/api/v1/private-investments/assets/{h.get('asset_id')}/history")
    arch = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1 AND name = $2 AND "
                                     "system_to IS NOT NULL", ORG_A, NAME + "Macro HF", fetch="fetchval")
    vers = hist.get("asset_versions") or []
    check(bool(ce == 200 and (e1.get("row") or {}).get("notes") == "B4V note"
               and (e1.get("row") or {}).get("vintage_year") == 2020 and arch == 1 and hist_c == 200
               and len(vers) == 1 and (vers[0].get("before") or {}).get("notes") is None
               and (e1.get("row") or {}).get("asset_id") == h.get("asset_id")),
          "edit = a NEW version (id kept, the prior archived) with its before / after in the history",
          f"{ce} {dumps(e1)} arch {arch} {dumps(hist)}")
    # bugfix4 fix 1 (FAIL 3, verify): the query ordered `system_to NULLS LAST`, putting the LIVE row (system_to NULL)
    # SECOND while the assertions read cm[0] as live — so it compared the ARCHIVE copy's fresh id to the commitment
    # id. The service keeps the id (UPDATE in place after archiving a copy). Live row first; and a capital call made
    # BEFORE the edit must still resolve to the same commitment id after it, and a new flow still posts to it.
    cf1, fl1 = await api(U_OA, "POST", f"/api/v1/private-investments/{f.get('commitment_id')}/flows",
                         {"type": "call", "amount": "100000", "date": "2021-06-01"})
    cc, e2 = await api(U_OA, "PATCH", f"/api/v1/private-investments/assets/{f.get('asset_id')}",
                       {"commitment_date": "2021-04-01"})
    cm = await scoped(conn, ORG_A, "SELECT id, commitment_date, called_to_date, system_to FROM portfolio.commitments "
                                   "WHERE position_id = $1 ORDER BY system_to NULLS FIRST", UUID(f["position_id"]))
    check(bool(cc == 200 and len(cm) == 2 and str(cm[0]["id"]) == f.get("commitment_id") and cm[0]["system_to"] is None
               and cm[0]["commitment_date"] == date(2021, 4, 1) and cm[1]["system_to"] is not None
               and cm[1]["commitment_date"] == date(2021, 3, 1) and str(cm[1]["id"]) != f.get("commitment_id")
               and (e2.get("row") or e2).get("commitment_id") == f.get("commitment_id")),
          "a commitment-date edit keeps the commitment id (flows point at it) and archives the prior version",
          f"{cc} {[dict(r) for r in cm]}")
    cf2, fl2 = await api(U_OA, "POST", f"/api/v1/private-investments/{f.get('commitment_id')}/flows",
                         {"type": "call", "amount": "50000", "date": "2021-09-01"})
    calls = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.transactions WHERE position_id = $1 AND "
                                      "valid_to IS NULL AND system_to IS NULL", UUID(f["position_id"]),
                         fetch="fetchval")
    live_called = await scoped(conn, ORG_A, "SELECT called_to_date FROM portfolio.commitments WHERE id = $1 AND "
                                            "system_to IS NULL AND valid_to IS NULL", UUID(f["commitment_id"]),
                               fetch="fetchval")
    check(bool(cf1 == 201 and cf2 == 201 and fl2.get("commitment_id") == f.get("commitment_id")
               and Decimal(str(fl2.get("called_to_date") or "0")) == Decimal("150000")
               and live_called == Decimal("150000") and calls >= 2),
          "the capital call made BEFORE the edit still resolves to the same commitment after it, and a new call posts "
          "to it (called 100,000 + 50,000 = 150,000 on the kept id)",
          f"{cf1} {dumps(fl1)} / {cf2} {dumps(fl2)} live_called {live_called} txns {calls}")
    cbad, _ = await api(U_OA, "PATCH", f"/api/v1/private-investments/assets/{f.get('asset_id')}",
                        {"investment_date": "2021-01-01"})
    check(cbad == 422, "a commitment row refuses an investment date (the commitment carries its date)", f"{cbad}")
    c, b = await api(U_OA, "GET", f"/api/v1/held-away/accounts/{ACC_ONE}")
    qtys = [p.get("quantity") for p in ((b.get("positions") or {}).get("positions") or [])]
    lots = [l.get("quantity") for p in ((b.get("positions") or {}).get("positions") or []) for l in p.get("lots") or []]
    check(bool(c == 200 and qtys and all("E" not in str(q) for q in qtys + lots)),
          "account quantities serialise fixed-point (no '3.25E+6')", f"{qtys} {lots}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        lab = await _tax_status_label(conn, str(ORG_A), "tax_exempt")
    check(bool(lab), "the Tax section reads the account's STORED status (labelled from the org vocabulary)", f"{lab}")


# ═══ Task 7 — picker, identity, roles, refusals ═════════════════════════════════════════════════════════════════
async def y_identity(conn):
    section("[Y] Task 7 — picker never creates silently; refusals name the maker; placeholder logins; ONE role source")
    n0 = await scoped(conn, ORG_A, "SELECT count(*) FROM entities WHERE org_id = $1", ORG_A, fetch="fetchval")
    c, _ = await api(U_OA, "GET", "/api/v1/entities/search?q=B4V%20Nobody%20Typed%20This")
    n1 = await scoped(conn, ORG_A, "SELECT count(*) FROM entities WHERE org_id = $1", ORG_A, fetch="fetchval")
    check(bool(c == 200 and n0 == n1), "typing (searching) creates no entity — creation is only the explicit, "
          "confirmed POST (front end: tests/bugfix4)", f"{c} {n0} → {n1}")
    stubs = await platform(conn, "SELECT e.display_name, e.created_at::date AS d FROM entities e WHERE e.created_via = "
                                 "'picker_stub' AND NOT (e.org_id = ANY($1::uuid[])) AND e.valid_to IS NULL AND "
                                 "e.system_to IS NULL ORDER BY e.created_at", FIXTURE_ORGS)
    find("existing picker stubs (read-only, not deleted — listed for Joe in PROJECT_STATUS)",
         f"{[(r['display_name'], str(r['d'])) for r in stubs]}")
    # maker-checker refusal names the maker; two logins of ONE person are named as such
    imp = S.get("form_import")
    ca, ba = await api(U_OA, "POST", f"/api/v1/held-away/imports/{imp}/approve", {"note": "self"})
    check(bool(ca == 403 and "Entered by BUGFIX4 Orgadmin" in body_text(ba)),
          "the maker's own approval is refused, naming who entered it", f"{ca} {dumps(ba)}")
    await platform(conn, "INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via) VALUES "
                         "($1, $2, $3, 'https://verify.local/', $4, 'operator')", U_OA, ORG_A,
                   f"auth0|{TAG}-orgadmin-second", f"{TAG}-orgadmin-second@test.local", fetch="execute")
    cb, bb = await api((f"auth0|{TAG}-orgadmin-second", ORG_A), "POST", f"/api/v1/held-away/imports/{imp}/approve",
                       {"note": "second login"})
    check(bool(cb == 403 and "same person" in body_text(bb) and f"{TAG}-orgadmin-second@test.local" in body_text(bb)),
          "from the maker's SECOND sign-in: refused, naming both sign-ins as one person", f"{cb} {dumps(bb)}")
    # placeholder login flagged + linked to a pre-created user
    cu, lu = await api(U_OA, "GET", "/api/v1/admin/users?include_test=true&limit=200")
    row = next((r for r in (lu if isinstance(lu, list) else []) if r.get("id") == str(U_STUB)), {})
    check(bool(cu == 200 and row.get("stub_login") is True and row.get("sign_in_id") == sub_of(U_STUB)
               and row.get("contact_email") and "link" in (row.get("actions") or [])),
          "People & Access flags the placeholder login with its sign-in id + contact email, and offers Link",
          f"{cu} {dumps(row)}")
    cl, bl = await api(U_OA, "POST", f"/api/v1/admin/users/{U_STUB}/link", {"target_user_id": str(U_PRE)})
    st = await platform(conn, "SELECT (SELECT status FROM users WHERE id = $1) AS stub, (SELECT auth0_sub FROM users "
                              "WHERE id = $2) AS pre_sub", U_STUB, U_PRE, fetch="fetchrow")
    check(bool(cl == 200 and st["stub"] == "retired" and st["pre_sub"] == sub_of(U_STUB)),
          "linked: the stub row is retired (kept, not deleted); the pre-created user now owns the sign-in",
          f"{cl} {dumps(bl)} {dict(st)}")
    cm, me = await api((sub_of(U_STUB), ORG_A), "GET", "/api/v1/users/me")
    check(bool(cm == 200 and str(me.get("id")) == str(U_PRE)), "the placeholder sign-in now resolves to the ONE row "
          "for that person", f"{cm} {dumps(me)}")
    # ONE role source
    sup_role = ROLE_IDS[(ORG_A, "super_admin")]
    co, bo = await api(U_OA, "PUT", f"/api/v1/admin/users/{U_PROMO}/role", {"role_id": str(sup_role)})
    r0 = await platform(conn, "SELECT role FROM users WHERE id = $1", U_PROMO, fetch="fetchval")
    check(bool(co == 403 and r0 == "member"), "an org admin cannot grant Super Admin (valid body; only the gate "
          "refuses)", f"{co} {dumps(bo)} {r0}")
    for rname, want_su, probe in (("super_admin", True, 200), ("org_admin", False, 403), ("support_staff", False, 403)):
        cs, _ = await api(U_SUPER, "PUT", f"/api/v1/admin/users/{U_PROMO}/role", {"role_id": str(ROLE_IDS[(ORG_A, rname)])})
        cj, _ = await api(U_PROMO, "GET", "/api/v1/admin/security-data/master/jobs")
        st = await platform(conn, "SELECT role FROM users WHERE id = $1", U_PROMO, fetch="fetchval")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            pr = await load_principal(conn, U_PROMO)
        check(bool(cs == 200 and cj == probe and st == rname and pr.is_super_admin is want_su and rname in pr.roles),
              f"role set in People & Access → {rname}: the identical next request agrees (Super-Admin route {probe}), "
              "users.role mirrors it, the server's Principal agrees", f"{cs} {cj} {st} {pr.is_super_admin} {pr.roles}")
    cz, _ = await api(U_STRING, "GET", "/api/v1/admin/security-data/master/jobs")
    check(cz == 403, "a users.role string of 'super_admin' with NO grant no longer makes a Super Admin", f"{cz}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        dis = await rbac.role_disagreements(conn)
    real = [d for d in dis if UUID(d["org_id"]) not in FIXTURE_ORGS]
    find("REPORT (read-only, nothing changed): real users whose users.role differs from what their RBAC roles mirror to",
         f"{len(real)}: {[(d['id'][:8], d['account_role'], d['rbac_roles']) for d in real]}")
    supers = [d for d in real if "super_admin" in d["rbac_roles"] or d["account_role"] == "super_admin"]
    check(not supers, "no real user's Super Admin standing differs between the two sources (so moving the decision "
          "to user_roles changed nobody's access)", f"{supers}")


# ═══ Members, isolation ═════════════════════════════════════════════════════════════════════════════════════════
async def y_scope(conn):
    section("[Y] Members own family only on every changed route; org isolation both directions")
    for path, method, body in ((f"/api/v1/households/{HH1}/summary", "GET", None),
                               (f"/api/v1/held-away/accounts/{ACC_ONE}/snapshot", "GET", None),
                               (f"/api/v1/private-investments/assets/{UUID(int=1)}", "PATCH", {"notes": "x"}),
                               ("/api/v1/admin/security-data/master/jobs/security_master_13f/schedule", "POST",
                                {"active": True})):
        c, b = await api(U_MEMBER, method, path, body)
        check(c == 403, f"a member is refused: {method} {path.split('/api/v1')[1][:60]}", f"{c} {dumps(b)}")
    c, b = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('f1p')}/private-investments")
    check(bool(c == 200 and all(r.get("actions") == [] for r in (b.get("rows") or []))),
          "a member reads their own family's private investments with NO edit action", f"{c}")
    cs, _ = await api(U_SUPPORT, "GET", f"/api/v1/households/{HH1}/summary")
    check(cs == 403, "support staff without the permission are refused the household total", f"{cs}")
    for path in (f"/api/v1/households/{HH1}/summary", f"/api/v1/held-away/accounts/{ACC_ONE}/snapshot"):
        c, _ = await api(U_OB_ADMIN, "GET", path)
        check(c == 404, f"org B's admin on org A's {path.split('/')[3]} → 404 (as a missing id)", f"{c}")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a_a = await scoped(conn, ORG_A, "SELECT count(*) FROM held_away_imports WHERE org_id = $1", ORG_A, fetch="fetchval")
    a_b = await scoped(conn, ORG_B, "SELECT count(*) FROM held_away_imports WHERE org_id = $1", ORG_A, fetch="fetchval")
    b_a = await scoped(conn, ORG_A, "SELECT count(*) FROM households WHERE org_id = $1", ORG_B, fetch="fetchval")
    b_b = await scoped(conn, ORG_B, "SELECT count(*) FROM households WHERE org_id = $1", ORG_B, fetch="fetchval")
    check(bool(a_a > 0 and b_b > 0 and a_b == 0 and b_a == 0), "RLS: each org reads its own imports / households, "
          "none of the other's", f"A {a_a}/{b_a} B {b_b}/{a_b}")


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
    code2, out2, _ = run_cmd(["node", "--test", "tests/bugfix4/"], WEB_DIR, timeout=300)
    check(bool(code2 == 0 and re.search(r"^# fail 0", out2, re.M)), "the sprint's node tests (draft lines, stale "
          "fields, picker confirmation, envelope controls, stub notice, fixed-point) pass", out2[-200:] if code2 else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_portentry1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_portentry1.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_portentry1.py: 0 FAIL (its OWN final TOTAL line)",
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
        section("[Y] Task 1 — findings 1a–1g")
        self_task1(check, find)
        section("[Y] Pure functions")
        await run("pure", self_pure, check, find)
        await run("snapshot", y_snapshot, conn)
        await run("repair", y_repair, conn)
        await run("rollup", y_rollup, conn)
        await run("agg", y_agg, conn)
        await run("jobs", y_jobs, conn)
        await run("form", y_form, conn)
        await run("household page", y_household_page, conn)
        await run("identity", y_identity, conn)
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
