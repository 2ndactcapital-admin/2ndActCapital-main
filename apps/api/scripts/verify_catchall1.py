"""verify_catchall1 — the small-fixes sweep: bulk-approve wording, ONE shared busy indicator, End / Edit / History for
direct assets (a disposal row the net-worth core honours by valid time), defining a listed option contract from the
held-away picker through rep8's own service, the HollisMuse Lows, and three pre-existing verify FAILs.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch and R2 are stubbed with their CURRENT signatures, and httpx's network transport refuses (the
count of attempts is asserted 0).

Everything after the bearer token is the real app against the live database, in TWO fixture orgs
(99000000-…-000000610001 / 610002), ``is_test = true`` (asserted), as are the fixture users; their roles are granted
through user_roles and load_principal is asserted to decide each one as intended. One fixture security in the platform
master (reserved ticker CA1VSPY) is the option underlying; the contract rep8 creates on it AND that contract's own
master 'option' security (recorded from rep8's response; ids materialised before the delete) are removed by id. Demo rows
(tag demo-olivo) and every real row are only READ: count + checksum before and after. audit_log is counted BY FIXTURE
IDS ONLY (real activity — the ~07:00 UTC nightly refresh — writes to it). Teardown at start and end via
_verify_cleanup by fixture id (``execution.teardown`` for the append-only option contracts).

--self-checks-only: git / route-record / static / pure checks + the three repaired verifies' own --self-checks-only
(stdlib + git; no Doppler, DB, network or npm). --cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"

import asyncio
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402  (stdlib + asyncpg only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)
import _verify_scope as VSC  # noqa: E402  (stdlib only) — live-written tables counted by fixture ids

SPRINT_KEY = "catchall1"
BEFORE_REF = "8b7e8f2"   # catchall1-work base: "merge s28"

NEW_ROUTES = {
    ("PATCH", "/api/v1/direct-assets/{asset_id}"): "internal + any(manage_members)",
    ("POST", "/api/v1/direct-assets/{asset_id}/end"): "internal + any(manage_members)",
    ("GET", "/api/v1/direct-assets/{asset_id}/history"): "internal + any(manage_members)",
}
#: the brief's own vocabulary (Task 4) — a spec, not live state
BRIEF_END_REASONS = ("sold", "disposed", "transferred", "written_off", "entered_in_error")
BRIEF_EDIT_FIELDS = ("name", "kind", "value", "as_of", "method", "notes")
REPAIRED_VERIFIES = ("verify_previewperf.py", "verify_previewperffix1.py", "verify_vhk2.py")
NODE_TEST_FILE = "tests/catchall1/catchall1.test.mjs"


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded, never interactive."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


# ═══ --self-checks-only (no Doppler / DB / network / npm) ═══════════════════════════════════════════════════════
TASK1 = [
    ("docs/CATCHALL1_DISCOVERY.md", "## 1a.", "1a — the bulk-approve two-step flow"),
    ("docs/CATCHALL1_DISCOVERY.md", "## 1b.", "1b — the slow actions and the (absent) shared busy component"),
    ("docs/CATCHALL1_DISCOVERY.md", "asset_disposals", "1c — the direct-asset model and what 'ended' means"),
    ("docs/CATCHALL1_DISCOVERY.md", "find_or_create_contract", "1d — the picker → rep8 option-contract service"),
    ("docs/CATCHALL1_DISCOVERY.md", "Secured credit line", "1e — the three Lows"),
    ("docs/CATCHALL1_DISCOVERY.md", "SPRINT_PINNED", "1f — the cause of each pre-existing verify FAIL"),
]
CODE_NEEDLES = [
    ("apps/api/services/portfolio_rollup.py", "AND d.end_date <= $2::date)",
     "the net-worth core counts a position only BEFORE its asset's end date (one place: _current_positions)"),
    ("apps/api/services/portfolio_kind_taxonomy.py", "async def reclassify_target(",
     "ONE reclassify rule (portfolio_kind_taxonomy.reclassify_target)"),
    ("apps/api/scripts/backfill_taxonomy_keys.py", "await reclassify_target(c, org,",
     "backfill --reclassify uses the shared rule"),
    ("apps/api/services/direct_holdings.py", "await reclassify_target(",
     "the direct-asset Edit uses the SAME rule (not a copy)"),
    ("docs/catchall1_part1.sql", "CREATE POLICY asset_disposals_update",
     "the disposal table has a policy for every operation (SELECT / INSERT / UPDATE / DELETE)"),
    ("apps/api/routers/portentry.py", '"editable": ["define_contract"] if can_define else []',
     "the picker publishes 'define_contract' only for rep8's gate"),
    ("apps/web/components/heldaway/OptionContractPicker.jsx", '"/api/replication/options/contracts"',
     "the picker defines a contract through rep8's own route (no second writer)"),
    ("apps/api/services/crm_vocab.py", '("securities_backed_line", "Secured credit line — securities")',
     "liability labels use the plan's wording (keys unchanged)"),
    ("apps/api/scripts/verify_goldset.py", "reviewable = {k for k in live if k not in _derive.CALCULATED_FIELDS}",
     "verify_goldset: a candidate is done over the REVIEWABLE fields (calculated ones refused, not reviewed)"),
    ("apps/api/scripts/_verify_self.py", '"previewperf": ("ca39a17", "d9afcea")',
     "_verify_self pins previewperf's own end (it shares its merge with fix 1)"),
]


def _key(rec: dict) -> tuple:
    return ((rec.get("methods") or [""])[0], rec["path"])


def self_task1(check, find):
    for rel, needle, meaning in TASK1:
        check(needle in _read(rel), f"Task 1 — {meaning}", f"{rel}: needle {needle!r} missing")
    find("1c — 'ended' is a disposal row, not valid_to on the positions: their valid_to means 'superseded by a "
         "restatement', so stamping it would also drop the asset from every as-of before the end")
    find("1d — rep8 has NO maker-checker on defining a contract (find-or-create, idempotent on its terms) — none added")
    find("1e — EntityDetailTabs' Overview 'Owns' tree still uses the server-rendered graph prop (named, not changed)")


def self_static(check, find):
    for rel, needle, meaning in CODE_NEEDLES:
        check(needle in _read(rel), meaning, f"{rel}: needle missing")
    check("verify_catchall1.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m = re.search(r"\n## [^\n]*CATCHALL1[^\n]*\n.*?(?=\n## |\Z)", status, re.S)
    sec = m.group(0) if m else ""
    items = ["Bulk approve", "busy indicator", "Direct assets", "Option contract", "Lows", "verify FAILs"]
    missing = [i for i in items if i.lower() not in sec.lower()]
    check(bool(sec and not missing and sec.count("DONE") >= 6), "Task 9: PROJECT_STATUS has a CATCHALL1 section with "
          "each item marked DONE", f"missing {missing}")
    # the shared reclassify is ONE function: neither caller resolves kind → key itself any more
    bf = _read("apps/api/scripts/backfill_taxonomy_keys.py")
    rec = bf[bf.find("async def reclassify("):bf.find("async def funds(")]
    check(bool(rec and "mapping.get(kind)" not in rec and "reclassify_target" in rec),
          "backfill --reclassify no longer carries its own kind → key copy", "")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if want not in str((new.get(k) or {}).get("after") or "")]
    check(not wrong, "each new route carries the Add-asset gate (internal + manage_members)", f"{wrong}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_goldset(check, find):
    """goldset's failing check, reproduced and resolved statically (the check itself needs the full fixture flow):
    the field the 422 named IS a calculated registry field; the route refuses an edit of every calculated field; the
    progress the candidate's status reads EXCLUDES them — so the old expectation (every live field reviewed) could
    never pass, and the new one (every reviewable field) is the route's own rule."""
    from services.note_extraction import derive

    route = _read("apps/api/routers/note_extraction_admin.py")
    check("proceeds_to_issuer_usd" in derive.CALCULATED_FIELDS and "total_fees_usd" in derive.CALCULATED_FIELDS,
          "reproduced: proceeds_to_issuer_usd (the 422's field) is a CALCULATED field", f"{derive.CALCULATED_FIELDS}")
    check(bool("if field_key in derive.CALCULATED_FIELDS:" in route and "status_code=422" in route
               and "return {k for k in keys if k not in derive.CALCULATED_FIELDS}" in route),
          "the route refuses a calculated field (422) and _reviewable excludes it from progress", "")
    old = VS.git_show(BEFORE_REF, "apps/api/scripts/verify_goldset.py") or ""
    new = _read("apps/api/scripts/verify_goldset.py")
    check(bool("remaining = [k for k in sorted(live) if k not in have]" in old
               and "remaining = [k for k in sorted(reviewable) if k not in have]" in new
               and "is refused (422) and nothing is written" in new),
          "the verify defect at BEFORE_REF (every live field) is fixed (reviewable fields + the refusal asserted)", "")


def self_pure(check, find):
    """Pure functions, no DB (imports local: --self-checks-only reaches here before hydrate)."""
    from services import crm_vocab as V
    from services import direct_holdings as DH

    labels = dict(V.LIABILITY_KINDS)
    check(bool(labels.get("securities_backed_line", "").startswith("Secured credit line")
               and labels.get("asset_secured_line", "").startswith("Secured credit line")
               and labels["securities_backed_line"] != labels["asset_secured_line"]),
          "both plan 'Secured credit line' kinds carry the plan's wording and stay distinguishable", f"{labels}")
    old_src = VS.git_show(BEFORE_REF, "apps/api/services/crm_vocab.py") or ""
    old_block = old_src[old_src.find("LIABILITY_KINDS = "):old_src.find("RATE_TYPES = ")]
    old_keys = set(re.findall(r'\("([a-z_]+)", "', old_block))
    check(bool(old_keys and set(labels) == old_keys), "liability KEYS unchanged since BEFORE_REF (labels only)",
          f"{sorted(labels)} vs {sorted(old_keys)}")
    check(bool(set(DH.END_REASONS) == set(BRIEF_END_REASONS) and DH.ERROR_REASON in DH.END_REASONS),
          "the end reasons are the five the brief names", f"{DH.END_REASONS}")
    check(bool("cash_holding" in DH.DIRECT_KINDS and set(DH.DIRECT_EDIT_FIELDS) == set(BRIEF_EDIT_FIELDS)),
          "Edit takes the brief's fields (name / kind incl. Cash / working capital / value / as-of / method / notes)",
          f"{DH.DIRECT_EDIT_FIELDS}")


def self_repaired(check, find):
    """Task 8 — each repaired verify's OWN --self-checks-only, judged by its own final TOTAL line + exit code."""
    env = os.environ | {"VERIFY_NESTED": "1"}
    for name in REPAIRED_VERIFIES:
        code, out, err = run_cmd([sys.executable, str(HERE / name), "--self-checks-only"], REPO, timeout=600, env=env)
        nt = VT.nested_total(out, code)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(bool(nt.ok), f"{name} --self-checks-only: 0 FAIL (its own final TOTAL line)",
              f"{nt.detail()}; {fails[:3]} {err[-200:] if not nt.ok else ''}")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)
    self_goldset(check, find)
    self_pure(check, find)
    self_repaired(check, find)


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
from services import direct_holdings as DH  # noqa: E402
from services import networth as NW  # noqa: E402
from services import rbac  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.authz import load_principal  # noqa: E402
from services.database import close_pool  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "catchall1verify"
NAME = "CA1V "            # reserved fixture name prefix (asserted to match no real row)
#: every security NAME starting 'CA1V' (no space) is reserved too: rep8 names a contract's option security
#: "<underlying ticker> <expiry> <strike> …", so the contract on CA1VSPY is "CA1VSPY 2026-12-18 660 CALL …"
NAME_PREFIX = "CA1V"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000061{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
SEC_U = _id(900)
FIX_SECS = [SEC_U]
TICKER_U = "CA1VSPY"
#: the option securities rep8's contract service created during THIS run (recorded from its response, by id)
CREATED_SECS: list[UUID] = []

OA_PERMS = frozenset({"manage_members", "view_members", "view_portfolio", "manage_portfolio", "view_dashboard",
                      "view_held_away", "manage_held_away", "view_replication", "manage_replication"})
#: an INTERNAL adviser who may add / edit assets and use the held-away picker, but does NOT hold rep8's gate
HA_PERMS = frozenset({"manage_members", "view_members", "view_portfolio", "view_dashboard", "view_held_away"})
U_OA, U_HA, U_MEMBER, U_OB = _id(12), _id(13), _id(14), _id(15)
USERS = {   # uid: (org, users.role, short name, RBAC role, permissions)
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", OA_PERMS),
    U_HA: (ORG_A, "member", "adviser", "advisor", HA_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OB: (ORG_B, "org_admin", "obadmin", "org_admin", OA_PERMS),
}
E = {"p": (_id(30), ORG_A, "individual", "Principal"), "b": (_id(31), ORG_B, "individual", "Org B Person")}
TAXONOMY_SOURCE_ORG = UUID("00000000-0000-0000-0000-000000000001")   # read-only: its asset_taxonomy rows are copied
CASH_KEY = "taxonomy_mc_8_1"            # Cash & Equivalents (org_settings.DEFAULT_SETTINGS portfolio.kind_taxonomy)
D0 = date(2026, 10, 1)                  # every fixture asset's first valuation / position as-of
D_END = date(2026, 10, 20)              # the 'sold' end date (in the past, so before / after / today are all real)

COUNTED = ("public.users", "public.organizations", "public.entities", "public.delegate_grants",
           "public.role_permissions", "public.user_roles", "public.entity_holdings",
           "portfolio.assets", "portfolio.positions", "portfolio.valuations", "portfolio.asset_disposals",
           "portfolio.rollup_requests", "portfolio.option_contracts",
           "portfolio.securities_global", "portfolio.securities_global_identifiers")
#: NOT counted whole-table, by name: public.audit_log and public.member_todos (real activity writes them) — the
#: fixture's own audit rows are counted by fixture ids (y_audit + leftovers).
REAL_FINGERPRINTED = ("public.entities", "portfolio.assets", "portfolio.positions", "portfolio.valuations",
                      "portfolio.asset_disposals", "public.user_roles")

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


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:600]


# ═══ DB helpers (RLS context set ON the connection inside its transaction, every read) ═════════════════════════
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


async def core(conn, org, entity, as_of: date) -> dict:
    """The net-worth core itself (services.networth.entity) at ``as_of``, under the same org context a route sets."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await NW.entity(conn, org, entity, as_of=as_of)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, VSC.count_sql(t, FIXTURE_ORGS, list(USERS)), fetch="fetchval")) for t in COUNTED}


async def pk_of(conn, table: str) -> list[str]:
    rows = await platform(conn, "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype::text = "
                                "'p' ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str, *, where: str = "true", args=()) -> tuple[int, str]:
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY {key}),"
                               f" '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


def _real_where(table: str) -> str:
    if table == "public.user_roles":
        return "user_id NOT IN (SELECT id FROM public.users WHERE org_id = ANY($1::uuid[]))"
    return "NOT (org_id = ANY($1::uuid[]))"


async def real_prints(conn) -> dict:
    out = {t: await fingerprint(conn, t, where=_real_where(t), args=(FIXTURE_ORGS,)) for t in REAL_FINGERPRINTED}
    out["portfolio.option_contracts"] = await fingerprint(
        conn, "portfolio.option_contracts", where="NOT (underlying_global_security_id = ANY($1::uuid[]))",
        args=(FIX_SECS,))
    out["portfolio.securities_global"] = await fingerprint(
        conn, "portfolio.securities_global", where="NOT (id = ANY($1::uuid[])) AND id NOT IN (SELECT "
        "global_security_id FROM portfolio.option_contracts WHERE underlying_global_security_id = ANY($1::uuid[]))",
        args=(FIX_SECS,))
    return out


DEMO = "SELECT id FROM public.entities WHERE 'demo-olivo' = ANY(tags)"
DEMO_SQL = {
    "entities": ("SELECT * FROM public.entities WHERE 'demo-olivo' = ANY(tags)", "id, system_from"),
    "positions": (f"SELECT * FROM portfolio.positions WHERE owner_entity_id IN ({DEMO})", "id"),
    "assets": (f"SELECT * FROM portfolio.assets WHERE id IN (SELECT asset_id FROM portfolio.positions WHERE "
               f"owner_entity_id IN ({DEMO}))", "id, system_from"),
    "valuations": (f"SELECT * FROM portfolio.valuations WHERE asset_id IN (SELECT asset_id FROM portfolio.positions "
                   f"WHERE owner_entity_id IN ({DEMO}))", "id"),
    "disposals": (f"SELECT * FROM portfolio.asset_disposals WHERE asset_id IN (SELECT asset_id FROM "
                  f"portfolio.positions WHERE owner_entity_id IN ({DEMO}))", "id"),
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


#: the option securities this verify owns, wherever they are: (1) recorded from rep8's response this run; (2) the
#: security of any contract on a fixture underlying; (3) a LEAKED one from an earlier run — an 'option' security
#: whose name starts with the fixture ticker (contract_name(TICKER_U, …)) that no contract references any more
#: (its contract and its fixture underlying were torn down, the security was not: the catchall1 fix 1 leak).
FIXTURE_OPTION_SQL = (
    "SELECT id FROM portfolio.securities_global WHERE id = ANY($2::uuid[]) "
    "UNION SELECT global_security_id FROM portfolio.option_contracts WHERE underlying_global_security_id = "
    "ANY($1::uuid[]) "
    "UNION SELECT g.id FROM portfolio.securities_global g WHERE g.security_type = 'option' AND g.name LIKE $3 "
    "AND NOT EXISTS (SELECT 1 FROM portfolio.option_contracts c WHERE c.global_security_id = g.id)")


async def fixture_option_secs(op) -> list[UUID]:
    """Materialised BEFORE the delete: VC.cleanup deletes children first (option_contracts before
    securities_global), so a predicate that reads option_contracts at DELETE time matches nothing — the leak."""
    rows = await op.fetch(FIXTURE_OPTION_SQL, FIX_SECS, CREATED_SECS, TICKER_U + " %")
    ids = sorted({r["id"] for r in rows}, key=str)
    # refuse (delete nothing) if a reserved-name security belongs to a contract on a NON-fixture underlying
    bad = await op.fetchval(
        "SELECT count(*) FROM portfolio.option_contracts c JOIN portfolio.securities_global g ON "
        "g.id = c.global_security_id WHERE g.name LIKE $1 AND NOT (c.underlying_global_security_id = "
        "ANY($2::uuid[]))", NAME_PREFIX + "%", FIX_SECS)
    if bad:
        raise RuntimeError(f"refusing teardown: {bad} reserved-name ({NAME_PREFIX}%) option security(ies) sit on a "
                           "contract whose underlying is NOT a fixture security")
    return ids


def _plan(opt_secs: list[UUID]) -> dict:
    users = list(USERS)
    secs = list(FIX_SECS) + [i for i in opt_secs if i not in FIX_SECS]
    return {
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
        "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                    [FIXTURE_ORGS]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
        # the contract rep8 defines on the fixture underlying (a platform row: org_id NULL) — by fixture id, explicitly
        "portfolio.option_contracts": ("underlying_global_security_id = ANY($1::uuid[]) OR global_security_id = "
                                       "ANY($2::uuid[])", [FIX_SECS, secs]),
        # the fixture underlying AND the option securities rep8 created on it — ids materialised, never a subquery
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
    }


async def teardown(_conn=None) -> list[str]:
    op = await operator_conn()
    try:
        opt = await fixture_option_secs(op)
        for r in await op.fetch("SELECT id, name, security_type, system_from FROM portfolio.securities_global WHERE "
                                "id = ANY($1::uuid[]) ORDER BY id", opt):
            print(f"[INFO] fixture option security to remove: {r['id']} {r['name']!r} ({r['security_type']}, "
                  f"created {r['system_from'].isoformat()})")
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(opt),
                               gucs=("execution.teardown",), label="verify_catchall1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM portfolio.option_contracts WHERE underlying_global_security_id = ANY($1::uuid[])",
             [FIX_SECS]),
            ("SELECT count(*) FROM portfolio.securities_global WHERE id = ANY($1::uuid[])", [FIX_SECS]),
            # every reserved-name security (incl. rep8's "CA1VSPY …" option) and the ones recorded this run
            ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1 OR id = ANY($2::uuid[])",
             [NAME_PREFIX + "%", CREATED_SECS]),
            ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1 OR "
             "global_security_id = ANY($2::uuid[])", [NAME_PREFIX + "%", CREATED_SECS]),
            ("SELECT count(*) FROM public.audit_log WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS])])
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
CATALOG_TABLES = ("portfolio.asset_disposals", "portfolio.assets", "portfolio.positions", "portfolio.valuations",
                  "portfolio.option_contracts", "portfolio.securities_global",
                  "portfolio.securities_global_identifiers", "public.user_roles", "public.delegate_grants")


def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


async def y_catalog(conn):
    section("[Y] Constraints + unique indexes read from the catalog before seeding; reserved prefixes match no real row")
    cons = {}
    for t in CATALOG_TABLES:
        for r in await platform(conn, "SELECT conname, contype::text AS k, pg_get_constraintdef(oid) AS d FROM "
                                      "pg_constraint WHERE conrelid = $1::regclass AND contype::text IN ('p','c','u',"
                                      "'f')", t):
            cons[(t, r["conname"])] = (r["k"], r["d"])
    kinds = {k for k, _ in cons.values()}
    check(bool(cons and {"p", "c", "f"} <= kinds), f"{len(cons)} PK / CHECK / UNIQUE / FK constraints read",
          f"{sorted(kinds)}")
    reason = [d for (t, n), (k, d) in cons.items() if t == "portfolio.asset_disposals" and k == "c"
              and n == "asset_disposals_reason_chk"]
    allowed = set(_allowed(reason[0])) if reason else set()
    check(allowed == set(DH.END_REASONS), "the disposal reason CHECK allows exactly the service's end reasons",
          f"{sorted(allowed)} vs {sorted(DH.END_REASONS)}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND tablename = ANY($1)",
        ["asset_disposals", "option_contracts"])}
    check(bool("asset_disposals_one_current" in idx and "UNIQUE" in idx["asset_disposals_one_current"]
               and "option_contracts_terms_uq" in idx),
          "unique indexes read: one current end per asset; one live contract per terms_key", f"{sorted(idx)}")
    pol = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND "
                                                  "tablename = 'asset_disposals'")}
    check(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, "asset_disposals has an RLS policy for every operation",
          f"{sorted(pol)}")
    rls = await platform(conn, "SELECT relrowsecurity FROM pg_class WHERE oid = 'portfolio.asset_disposals'::regclass",
                         fetch="fetchval")
    check(rls is True, "RLS is enabled on asset_disposals", f"{rls}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM portfolio.assets WHERE name LIKE $1 AND NOT "
                                "(org_id = ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND "
                                "NOT (id = ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 "
                                "AND NOT (id = ANY($4::uuid[]))) + (SELECT count(*) FROM "
                                "portfolio.securities_global_identifiers WHERE id_value LIKE 'CA1V%' AND NOT "
                                "(global_security_id = ANY($6::uuid[]))) + (SELECT count(*) FROM "
                                "portfolio.securities_global WHERE name LIKE $7 AND NOT (id = ANY($6::uuid[]))) + "
                                "(SELECT count(*) FROM portfolio.option_contracts WHERE "
                                "underlying_global_security_id = ANY($6::uuid[]))",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), FIX_SECS, NAME_PREFIX + "%",
                          fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (names incl. 'CA1V' security names with no space, subs, slugs, "
          "CA1V tickers, contracts on a fixture underlying) match no real row", f"{real}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CATCHALL1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
            await conn.execute(
                "INSERT INTO config (org_id, config_key, config_value, value_type, category, display_order, "
                "is_active) SELECT $1, config_key, config_value, value_type, category, display_order, true FROM config "
                "WHERE org_id = $2 AND category = 'asset_taxonomy' AND is_active", org, TAXONOMY_SOURCE_ORG)
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        role_ids = {}
        for uid, (org, _acct, _nm, rname, perms) in USERS.items():
            rid = role_ids.get((org, rname)) or await rbac.ensure_role(conn, org, rname, f"CATCHALL1 fixture {rname}")
            role_ids[(org, rname)] = rid
            for p in perms:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, f"{TAG}-{nm}@test.local", f"CATCHALL1 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, role_ids[(org, rname)])
        for k, (i, org, etype, name) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) "
                               "VALUES ($1, $2, $3, 'active', $4::entity_type)", i, org, NAME + name, etype)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, eid("p"), U_MEMBER)
        await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, canonical_id) "
                           "VALUES ($1, $2, 'fund', $1)", SEC_U, NAME + "S&P 500 Fixture ETF")
        await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                           "id_value, is_primary) VALUES ($1, 'ticker', $2, true)", SEC_U, TICKER_U)


async def y_fixtures(conn):
    section("[Y] Fixture orgs + users are is_test; roles come from user_roles and decide as intended")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == len(FIXTURE_ORGS) and u == len(USERS)), "every fixture org and user is is_test = true", f"{o}/{u}")
    pr = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid in USERS:
            pr[uid] = await load_principal(conn, uid)
    want = {U_OA: (True, True, True), U_HA: (True, True, False), U_MEMBER: (False, False, False),
            U_OB: (True, True, True)}
    got = {uid: (bool(p.is_internal), "manage_members" in p.permissions, "manage_replication" in p.permissions)
           for uid, p in pr.items()}
    check(got == want and not any(p.is_super_admin or p.is_staff for p in pr.values()),
          "load_principal decides each fixture as intended (internal / manage_members / manage_replication; none "
          "staff or super admin)", f"{got}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []
S: dict = {}


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{USERS[uid][2]}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(who: UUID, method, path, body=None, timeout=180.0):
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

    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
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
        raise AssertionError(f"verify_catchall1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


async def y_role_is_real(conn):
    section("[Y] The verify's DB role does not bypass RLS")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


# ── direct-asset helpers ────────────────────────────────────────────────────────────────────────────────────────
async def add_asset(who, entity, kind, name, value, *, method="appraisal", day=D0):
    return await api(who, "POST", f"/api/v1/entities/{entity}/direct-assets", {
        "kind": kind, "name": NAME + name, "valuation": {"value": value, "date": day.isoformat(), "method": method}})


async def asset_snapshot(conn, aid) -> dict:
    """Every row of the asset across its tables (archives included) — for 'nothing deleted' / 'unchanged'."""
    a = await fingerprint(conn, "portfolio.assets", where="org_id = $1 AND (id = $2 OR valid_from = (SELECT "
                          "valid_from FROM portfolio.assets WHERE id = $2))", args=(ORG_A, aid))
    p = await fingerprint(conn, "portfolio.positions", where="asset_id = $1", args=(aid,))
    v = await fingerprint(conn, "portfolio.valuations", where="asset_id = $1", args=(aid,))
    d = await fingerprint(conn, "portfolio.asset_disposals", where="asset_id = $1", args=(aid,))
    return {"assets": a, "positions": p, "valuations": v, "disposals": d}


def asset_line(nw: dict, aid: str) -> Decimal:
    """The value the core counted for one asset (its top-holdings line; 0 when not counted)."""
    for h in nw.get("top_holdings") or []:   # networth keys a holding with no master security "asset:<id>"
        if h.get("key") == f"asset:{aid}":
            return Decimal(h["value"])
    return Decimal(0)


async def sql_value(conn, aid, as_of: date) -> Decimal:
    """Independent SQL: pct × the latest valuation <= as_of, ZERO when a current end has end_date <= as_of."""
    v = await scoped(conn, ORG_A, """
        SELECT COALESCE(sum(p.ownership_pct / 100 * (SELECT v.value FROM portfolio.valuations v WHERE v.asset_id =
               p.asset_id AND v.valuation_date <= $2 AND v.valid_to IS NULL AND v.system_to IS NULL ORDER BY
               v.valuation_date DESC, v.system_from DESC LIMIT 1)), 0)
          FROM portfolio.positions p
         WHERE p.asset_id = $1 AND p.as_of_date <= $2 AND p.valid_to IS NULL AND p.system_to IS NULL
           AND NOT EXISTS (SELECT 1 FROM portfolio.asset_disposals d WHERE d.asset_id = p.asset_id AND d.valid_to
                           IS NULL AND d.system_to IS NULL AND d.end_date <= $2)""", aid, as_of, fetch="fetchval")
    return Decimal(v).quantize(Decimal("0.01"))


async def y_edit(conn):
    section("[Y] Direct assets — Edit: a new version (id kept, prior archived), history lists both; kind → Cash re-keys")
    c, b = await add_asset(U_OA, eid("p"), "real_estate", "Lake house", "900000")
    check(c == 201, "Add asset (real estate, 900,000 at 2026-10-01)", f"{c} {dumps(b)}")
    aid = UUID(b["asset_id"]) if c == 201 else UUID(int=0)
    S["edit"] = aid
    k0 = await scoped(conn, ORG_A, "SELECT default_taxonomy_key FROM portfolio.assets WHERE id = $1 AND system_to IS "
                                   "NULL", aid, fetch="fetchval")
    c1, e1 = await api(U_OA, "PATCH", f"/api/v1/direct-assets/{aid}", {"name": NAME + "Lake house (Muskoka)",
                                                                        "notes": "appraised 2026"})
    rows = await scoped(conn, ORG_A, "SELECT id, name, notes, system_to FROM portfolio.assets WHERE org_id = $1 AND "
                                     "valid_from = (SELECT valid_from FROM portfolio.assets WHERE id = $2) ORDER BY "
                                     "system_to NULLS FIRST", ORG_A, aid)
    live = [r for r in rows if r["system_to"] is None]
    prior = [r for r in rows if r["system_to"] is not None]
    check(bool(c1 == 200 and len(live) == 1 and live[0]["id"] == aid and live[0]["name"].endswith("(Muskoka)")
               and live[0]["notes"] == "appraised 2026"),
          "Edit kept the id: the live row IS the same asset id, with the new name / notes", f"{c1} {dumps(e1)} {rows}")
    check(bool(len(prior) == 1 and prior[0]["id"] != aid and prior[0]["name"] == NAME + "Lake house"
               and prior[0]["notes"] is None), "...and the prior version is archived (system axis), not overwritten",
          f"{[dict(r) for r in prior]}")
    ch, hist = await api(U_OA, "GET", f"/api/v1/direct-assets/{aid}/history")
    vers = hist.get("versions") or []
    check(bool(ch == 200 and len(vers) == 2 and vers[0].get("current") is True and vers[1].get("current") is False
               and vers[1].get("name") == NAME + "Lake house"),
          "History lists both versions (current first, then the prior)", f"{ch} {dumps(vers)}")
    n_audit = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND resource_id = $2 AND "
                                        "action = 'portfolio.direct_asset_edited'", ORG_A, aid, fetch="fetchval")
    check(n_audit == 1, "the edit is audited (counted by the fixture asset id)", f"{n_audit}")
    before_nw = await core(conn, ORG_A, eid("p"), date.today())
    b_before = (before_nw.get("by_bucket") or {})
    check(bool(k0 and k0 != CASH_KEY and Decimal(b_before.get(k0, "0")) == Decimal("900000.00")
               and Decimal(b_before.get(CASH_KEY, "0")) == 0),
          "before the kind change the core counts it under its real-estate bucket, nothing under Cash", f"{k0} {b_before}")
    c2, e2 = await api(U_OA, "PATCH", f"/api/v1/direct-assets/{aid}", {"kind": "cash_holding"})
    kk = await scoped(conn, ORG_A, "SELECT asset_type, default_taxonomy_key FROM portfolio.assets WHERE id = $1 AND "
                                   "system_to IS NULL", aid, fetch="fetchrow")
    check(bool(c2 == 200 and kk and kk["asset_type"] == "cash_holding" and kk["default_taxonomy_key"] == CASH_KEY),
          "a kind change to Cash / working capital re-keys it to Cash & Equivalents (the --reclassify rule)",
          f"{c2} {dumps(e2)} {dict(kk) if kk else None}")
    cc, card = await api(U_OA, "GET", f"/api/v1/entities/{eid('p')}/net-worth")
    cr = (card.get("rows") or {})
    nw = await core(conn, ORG_A, eid("p"), date.today())
    sql = await sql_value(conn, aid, date.today())
    cb, nb = (cr.get("by_bucket") or {}), (nw.get("by_bucket") or {})
    check(bool(cc == 200 and Decimal(cb.get(CASH_KEY, "0")) == Decimal(nb.get(CASH_KEY, "0")) == sql
               == Decimal("900000.00") and Decimal(cb.get(k0, "0")) == 0 and cr.get("gross") == nw.get("gross")),
          "the net-worth core moved it: card == core == SQL under Cash & Equivalents, nothing left in real estate",
          f"card {cb} core {nb} sql {sql}")
    c3, _ = await api(U_OA, "PATCH", f"/api/v1/direct-assets/{aid}", {"value": "950000.00",
                                                                       "as_of": "2026-10-15", "method": "appraisal"})
    vals = await scoped(conn, ORG_A, "SELECT value, valuation_date FROM portfolio.valuations WHERE asset_id = $1 AND "
                                     "valid_to IS NULL AND system_to IS NULL ORDER BY valuation_date", aid)
    nw2 = await core(conn, ORG_A, eid("p"), date.today())
    check(bool(c3 == 200 and [v["value"] for v in vals] == [Decimal("900000"), Decimal("950000.00")]
               and Decimal((nw2.get("by_bucket") or {}).get(CASH_KEY, "0")) == Decimal("950000.00")),
          "a value edit is a NEW valuation (the prior kept) and the core reads the new one", f"{c3} {vals}")
    c4, e4 = await api(U_OA, "PATCH", f"/api/v1/direct-assets/{aid}", {})
    check(c4 == 422, "an empty edit is refused (422, nothing to change)", f"{c4} {dumps(e4)}")


async def y_end(conn):
    section("[Y] Direct assets — End: counted before its end date, not after; 'entered in error' never; nothing deleted")
    c, b = await add_asset(U_OA, eid("p"), "collectible", "Painting", "50000")
    aid = UUID(b["asset_id"]) if c == 201 else UUID(int=0)
    S["end"] = aid
    snap0 = await asset_snapshot(conn, aid)
    ce, e = await api(U_OA, "POST", f"/api/v1/direct-assets/{aid}/end", {"reason": "sold",
                                                                          "end_date": D_END.isoformat(),
                                                                          "proceeds": "60000.00"})
    d = await scoped(conn, ORG_A, "SELECT end_date, reason, proceeds FROM portfolio.asset_disposals WHERE asset_id = "
                                  "$1 AND valid_to IS NULL AND system_to IS NULL", aid, fetch="fetchrow")
    check(bool(c == 201 and ce == 200 and d and d["end_date"] == D_END and d["reason"] == "sold"
               and d["proceeds"] == Decimal("60000.00")),
          "End (sold, 2026-10-20, proceeds 60,000) writes ONE current disposal row", f"{ce} {dumps(e)} {d}")
    before, after = D_END - timedelta(days=5), D_END + timedelta(days=5)
    for day, want in ((before, Decimal("50000.00")), (D_END, Decimal(0)), (after, Decimal(0)),
                      (date.today(), Decimal(0))):
        nw = await core(conn, ORG_A, eid("p"), day)
        got = asset_line(nw, aid)
        check(bool(got == want == await sql_value(conn, aid, day)),
              f"as of {day}: the core counts {want} for the ended asset (== independent SQL)", f"core {got}")
    cc, card = await api(U_OA, "GET", f"/api/v1/entities/{eid('p')}/net-worth")
    check(bool(cc == 200 and asset_line(card.get("rows") or {}, aid) == 0), "the card (today) no longer counts it",
          f"{cc}")
    snap1 = await asset_snapshot(conn, aid)
    check(bool(snap1["assets"] == snap0["assets"] and snap1["positions"] == snap0["positions"]
               and snap1["valuations"] == snap0["valuations"] and snap1["disposals"][0] == snap0["disposals"][0] + 1),
          "nothing deleted or rewritten: asset, positions and valuations identical; one disposal row added",
          f"{snap0} -> {snap1}")
    cl, lst = await api(U_OA, "GET", f"/api/v1/entities/{eid('p')}/direct-assets")
    row = next((r for r in lst.get("rows") or [] if r.get("asset_id") == str(aid)), {})
    check(bool(cl == 200 and (row.get("ended") or {}).get("end_date") == D_END.isoformat()
               and row.get("actions") == ["history"]),
          "the list keeps the ended asset with its end; its only action is History", f"{dumps(row)}")
    c2, e2 = await api(U_OA, "POST", f"/api/v1/direct-assets/{aid}/end", {"reason": "sold", "end_date": "2026-10-25"})
    c3, e3 = await api(U_OA, "PATCH", f"/api/v1/direct-assets/{aid}", {"name": NAME + "Painting II"})
    check(bool(c2 == 409 and c3 == 409), "an ended asset is not ended again or edited (409)",
          f"{c2} {dumps(e2)} / {c3} {dumps(e3)}")
    c4, e4 = await api(U_OA, "POST", f"/api/v1/direct-assets/{aid}/end", {"reason": "lost"})
    check(c4 == 422, "an unknown reason is refused (422)", f"{c4}")
    ch, hist = await api(U_OA, "GET", f"/api/v1/direct-assets/{aid}/history")
    check(bool(ch == 200 and len(hist.get("ends") or []) == 1 and len(hist.get("valuations") or []) == 1),
          "History shows its valuation and its end", f"{dumps(hist)}")
    # entered in error: ended from its START — never counted on any day
    c, b = await add_asset(U_OA, eid("p"), "operating_business", "Typo LLC stake", "7000",
                           method="mark_to_model")
    err = UUID(b["asset_id"]) if c == 201 else UUID(int=0)
    S["error"] = err
    n_d0 = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.asset_disposals WHERE asset_id = $1", err,
                        fetch="fetchval")
    c6, e6 = await api(U_OA, "POST", f"/api/v1/direct-assets/{err}/end", {"reason": "entered_in_error",
                                                                          "proceeds": "1"})
    n_d1 = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.asset_disposals WHERE asset_id = $1", err,
                        fetch="fetchval")
    check(bool(c6 == 422 and n_d0 == n_d1 == 0), "'entered in error' takes no proceeds (422, nothing written)",
          f"{c6} {dumps(e6)} {n_d0}->{n_d1}")
    c5, e5 = await api(U_OA, "POST", f"/api/v1/direct-assets/{err}/end", {"reason": "entered_in_error",
                                                                           "notes": "keyed on the wrong family"})
    de = await scoped(conn, ORG_A, "SELECT end_date FROM portfolio.asset_disposals WHERE asset_id = $1 AND valid_to IS "
                                   "NULL AND system_to IS NULL", err, fetch="fetchval")
    check(bool(c5 == 200 and de == D0), "'entered in error' ends it at its start (the first as-of, 2026-10-01)",
          f"{c5} {dumps(e5)} {de}")
    days = (D0, D0 + timedelta(days=10), date.today())
    vals = [asset_line(await core(conn, ORG_A, eid("p"), d_), err) for d_ in days]
    check(bool(all(v == 0 for v in vals) and len(vals) == 3), "...and it never counted: 0 on its start, 10 days on and "
          "today", f"{vals}")
    n_audit = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND resource_id = ANY($2) "
                                        "AND action = 'portfolio.direct_asset_ended'", ORG_A, [aid, err],
                           fetch="fetchval")
    check(n_audit == 2, "each end is audited (counted by fixture asset ids)", f"{n_audit}")


async def y_refusals(conn):
    section("[Y] Direct assets — a member sees no controls and is refused the routes; another org gets 404 (VALID bodies)")
    aid = S.get("edit") or UUID(int=0)
    snap0 = await asset_snapshot(conn, aid)
    cl, lst = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('p')}/direct-assets")
    perms, voc = lst.get("permissions") or {}, lst.get("vocabularies") or {}
    rows = lst.get("rows") or []
    check(bool(cl == 200 and rows and perms.get("can_write") is False and voc.get("editable") == []
               and all(r.get("actions") == [] for r in rows)),
          "the member reads the list with can_write false, editable [] and NO row actions (non-empty list)",
          f"{cl} {dumps(perms)} {len(rows)} rows")
    edit = {"name": NAME + "Member rename"}
    end = {"reason": "sold", "end_date": D_END.isoformat()}
    res = [await api(U_MEMBER, "PATCH", f"/api/v1/direct-assets/{aid}", edit),
           await api(U_MEMBER, "POST", f"/api/v1/direct-assets/{aid}/end", end),
           await api(U_MEMBER, "GET", f"/api/v1/direct-assets/{aid}/history")]
    check(all(c == 403 for c, _ in res), "the member is refused Edit / End / History (403) on valid bodies",
          f"{[(c, dumps(b)[:120]) for c, b in res]}")
    resb = [await api(U_OB, "PATCH", f"/api/v1/direct-assets/{aid}", edit),
            await api(U_OB, "POST", f"/api/v1/direct-assets/{aid}/end", end),
            await api(U_OB, "GET", f"/api/v1/direct-assets/{aid}/history")]
    check(all(c == 404 for c, _ in resb), "another org's admin (holding manage_members) gets 404 on the same requests",
          f"{[(c, dumps(b)[:120]) for c, b in resb]}")
    snap1 = await asset_snapshot(conn, aid)
    check(snap1 == snap0, "the refusals left every row of the asset unchanged", f"{snap0} -> {snap1}")
    ca, a_ok = await api(U_HA, "PATCH", f"/api/v1/direct-assets/{aid}", {"notes": "adviser note"})
    check(ca == 200, "the same Edit by an internal adviser holding manage_members (the Add-asset gate) is admitted",
          f"{ca} {dumps(a_ok)}")


async def y_isolation(conn):
    section("[Y] Org isolation, both directions (rolbypassrls = false)")
    c, b = await add_asset(U_OB, eid("b"), "collectible", "Org B vase", "1000")
    bid = UUID(b["asset_id"]) if c == 201 else UUID(int=0)
    ce, _ = await api(U_OB, "POST", f"/api/v1/direct-assets/{bid}/end", {"reason": "sold",
                                                                          "end_date": D_END.isoformat()})
    a_sees = await scoped(conn, ORG_A, "SELECT org_id FROM portfolio.asset_disposals WHERE org_id = ANY($1::uuid[])",
                          FIXTURE_ORGS)
    b_sees = await scoped(conn, ORG_B, "SELECT org_id FROM portfolio.asset_disposals WHERE org_id = ANY($1::uuid[])",
                          FIXTURE_ORGS)
    check(bool(c == 201 and ce == 200 and a_sees and all(r["org_id"] == ORG_A for r in a_sees)),
          "org A's context reads only org A's disposals (non-empty)", f"{[str(r['org_id']) for r in a_sees]}")
    check(bool(b_sees and all(r["org_id"] == ORG_B for r in b_sees)),
          "org B's context reads only org B's disposals (non-empty)", f"{[str(r['org_id']) for r in b_sees]}")
    cx, _ = await api(U_OA, "GET", f"/api/v1/direct-assets/{bid}/history")
    check(cx == 404, "org A's admin cannot read org B's asset history (404)", f"{cx}")
    none = await scoped(conn, "", "SELECT count(*) FROM portfolio.asset_disposals WHERE org_id = ANY($1::uuid[])",
                        FIXTURE_ORGS, fetch="fetchval")
    check(none == 0, "with no org context the disposals table reads nothing (the policy is really enforced)", f"{none}")


async def y_option(conn):
    section("[Y] Option contract — defined from the picker through rep8; selectable; same terms → the existing one")
    path = f"/api/v1/portfolio/option-contracts?underlying_ticker={TICKER_U}"
    c0, p0 = await api(U_OA, "GET", path)
    voc = p0.get("vocabularies") or {}
    check(bool(c0 == 200 and p0.get("rows") == [] and "define_contract" in (voc.get("editable") or [])
               and (p0.get("underlying") or {}).get("global_security_id") == str(SEC_U)
               and (p0.get("permissions") or {}).get("write_permission") == "manage_replication"),
          "no contract yet: the picker offers 'define_contract' to a manage_replication holder, with the master "
          "underlying", f"{c0} {dumps(p0)}")
    ch, ph = await api(U_HA, "GET", path)
    check(bool(ch == 200 and (ph.get("vocabularies") or {}).get("editable") == []
               and (ph.get("permissions") or {}).get("can_write") is False),
          "an adviser without manage_replication sees no define control (editable [], can_write false)", f"{dumps(ph)}")
    body = {"underlying_global_security_id": str(SEC_U), "option_type": "call", "strike": "660",
            "expiry": "2026-12-18", "exercise_style": "american", "settlement": "physical", "is_flex": False,
            "multiplier": "100", "exchange_listed": True, "broad_based_index": False, "is_mini": False}
    n0 = await platform(conn, "SELECT count(*) FROM portfolio.option_contracts WHERE underlying_global_security_id = "
                              "$1", SEC_U, fetch="fetchval")
    cr, rr = await api(U_HA, "POST", "/api/v1/replication/options/contracts", body)
    n1 = await platform(conn, "SELECT count(*) FROM portfolio.option_contracts WHERE underlying_global_security_id = "
                              "$1", SEC_U, fetch="fetchval")
    check(bool(cr == 403 and n1 == n0 == 0), "rep8's gate still applies: the adviser's identical, valid define is "
          "refused (403) and nothing is written", f"{cr} {dumps(rr)} {n0}->{n1}")
    c1, r1 = await api(U_OA, "POST", "/api/v1/replication/options/contracts", body)
    k1 = (r1.get("contract") or {})
    if k1.get("global_security_id"):
        CREATED_SECS.append(UUID(k1["global_security_id"]))      # torn down by id (and found again by name)
    sec1 = await platform(conn, "SELECT name, security_type FROM portfolio.securities_global WHERE id = $1",
                          k1.get("global_security_id"), fetch="fetchrow") if k1.get("global_security_id") else None
    check(bool(sec1 and sec1["security_type"] == "option" and sec1["name"].startswith(TICKER_U + " ")),
          "rep8 created the contract's master 'option' security (the verify records its id for teardown; its name "
          "starts with the fixture ticker, inside the reserved prefix)", f"{dict(sec1) if sec1 else None}")
    check(bool(c1 == 201 and k1.get("strike") == "660" and k1.get("option_type") == "call"
               and k1.get("underlying_global_security_id") == str(SEC_U)),
          "defined through rep8's own route (call 660 Dec 2026, ×100)", f"{c1} {dumps(r1)}")
    c2, p2 = await api(U_OA, "GET", path)
    ids = [r.get("global_security_id") for r in p2.get("rows") or []]
    check(bool(c2 == 200 and ids == [k1.get("global_security_id")] and p2.get("empty_reason") is None),
          "...and it is now selectable in the held-away line's picker", f"{dumps(p2)}")
    c3, r3 = await api(U_OA, "POST", "/api/v1/replication/options/contracts", body)
    n2 = await platform(conn, "SELECT count(*) FROM portfolio.option_contracts WHERE underlying_global_security_id = "
                              "$1", SEC_U, fetch="fetchval")
    check(bool(c3 == 201 and (r3.get("contract") or {}).get("id") == k1.get("id") and n2 == 1),
          "the same terms again FIND the existing contract (same id; one row)", f"{c3} {n2} {dumps(r3)}")


async def y_lows(conn):
    section("[Y] The Lows — liability labels from the API envelope (keys unchanged)")
    c, b = await api(U_OA, "GET", f"/api/v1/entities/{eid('p')}/liabilities")
    kinds = {k["value"]: k["label"] for k in ((b.get("vocabularies") or {}).get("liability_kinds") or [])}
    chk = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                               "'liabilities_kind_chk'", fetch="fetchval")
    check(bool(c == 200 and kinds and set(kinds) == set(_allowed(chk))),
          "the served kinds are exactly the stored keys liabilities_kind_chk allows", f"{sorted(kinds)} / {chk}")
    check(bool(kinds.get("securities_backed_line", "").startswith("Secured credit line")
               and kinds.get("asset_secured_line", "").startswith("Secured credit line")),
          "both of the plan's 'Secured credit line' kinds carry its wording", f"{kinds}")
    find("lists / pickers are client behaviour — proven by the node tests (tests/catchall1): value-driven typeahead "
         "with onInput, lists that follow a re-read")


async def y_audit(conn):
    section("[Y] audit_log — this run's rows counted BY FIXTURE IDS ONLY")
    n = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[]) AND action LIKE "
                             "'portfolio.direct_asset_%'", FIXTURE_ORGS, fetch="fetchval")
    check(n >= 6, "the fixture edits and ends wrote their audit rows (fixture orgs only)", f"{n}")


async def y_routes(conn):
    section("[Y] Routes — own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: the route record is CURRENT",
          (out + err)[-200:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "visibility1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/ (and the sprint's own file); npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(bool(code == 0 and m is not None and m.group(1) == "0"), "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code2, out2, err2 = run_cmd(["node", "--test", NODE_TEST_FILE], WEB_DIR, timeout=300)
    n_tests = len(re.findall(r"^test\(", _read(f"apps/web/{NODE_TEST_FILE}"), re.M))
    mp = re.search(r"^# pass (\d+)", out2, re.M)
    check(bool(code2 == 0 and mp and int(mp.group(1)) == n_tests and n_tests >= 10
               and re.search(r"^# fail 0", out2, re.M)),
          f"the sprint's own node tests ({NODE_TEST_FILE}): every test in the FILE passes",
          f"{n_tests} in file; {(out2 + err2)[-200:] if code2 else (mp.group(1) if mp else None)}")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rollup2.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rollup2.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_rollup2.py: 0 FAIL (its OWN final TOTAL line + exit code)",
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
        section("[Y] Task 1 — findings 1a–1f")
        await run("task1", self_task1, check, find)
        section("[Y] Pure functions + goldset's check (reproduced, then resolved)")
        await run("pure", self_pure, check, find)
        await run("goldset", self_goldset, check, find)
        await run("edit", y_edit, conn)
        await run("end", y_end, conn)
        await run("refusals", y_refusals, conn)
        await run("isolation", y_isolation, conn)
        await run("option", y_option, conn)
        await run("lows", y_lows, conn)
        await run("audit", y_audit, conn)
        section("[Y] Static — docs, regression set, code markers")
        await run("static", self_static, check, find)
        section("[Y] Task 8 — the repaired verifies' --self-checks-only")
        await run("repaired", self_repaired, check, find)
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
            check(left == 0, "zero fixture rows remain (audit_log by fixture org included)", f"{left}")
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
