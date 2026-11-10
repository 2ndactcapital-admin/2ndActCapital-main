"""verify_rep12 — Replication Studio, sprint 12 (REP12): the note-versus-replica
check across NEW issues — the hook on the extraction's one write path, the
daily sweep, a DRAFT replica only for an eligible note (never approved), the
comparison when quotes exist ("not priced" naming the gap otherwise), the "New
issues" feed with filters and alerts, review / dismiss recorded with the user,
and the Super Admin's underlying queue (one-click and multi-confirm through
confirm_resolution — still the one writer of 'resolved').

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep12.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
FAKED: the model step of the extraction — the verify writes each fixture note's
terms through ``services.note_terms_extraction.store_extraction``, the SAME
function ``extract_terms`` persists through and the hook lives in (with the
values a model would have returned). NO network (the httpx transport refuses
and records), NO model call. REAL: the app through an ASGI transport, the
routing step, rep11's verdict job / builder / pricing math, the human-task
engine, confirm_resolution, RLS and the live database. The script's own
connection is app_service (rolbypassrls = false, asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-0000001c0001 / 0002, is_test =
true, asserted); users created explicitly with explicit roles; PLATFORM fixture
filings (accession prefix ZZR12, cik prefix ZZR12), an STP policy for one
fixture filer, indices with the identifier prefix ZZR12 and names starting
'REP12 VERIFY', and the notes the faked extraction creates — all checked absent
from real rows first and removed BY ID at teardown. The hook and every re-check
are scoped to the fixture orgs (note_checks.HOOK_ONLY_ORGS) and every sweep to
the fixture notes, so no check row for a fixture note lands in a real org. Real
notes, terms, readings, filings, underlyings, replicas and users are compared by
count + checksum before and after.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with
execution.teardown = 'fixture' — keyed by this run's fixture ids; then every
counted table is re-read for its exact pre-test count.

--self-checks-only: git / route self-checks only (no Doppler, database, npm).
--cleanup-only: the teardown and the leftover check only. With VERIFY_NESTED=1
the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep12"
BEFORE_REF = "d529b18"   # main @ 'merge staffroles1' — the rep12-work base
_R = "/api/v1/replication"
_N = f"{_R}/notes"
_C = f"{_N}/checks"
_U = f"{_N}/underlyings"
ROUTES = {
    ("GET", _C): "internal + any(view_replication)",
    ("GET", f"{_C}/settings"): "internal + any(view_replication)",
    ("GET", f"{_C}/{{check_id}}"): "internal + any(view_replication)",
    ("POST", f"{_C}/run"): "manage_replication",
    ("POST", f"{_C}/settings"): "manage_replication",
    ("POST", f"{_C}/{{check_id}}/review"): "manage_replication",
    ("GET", _U): "super_admin",
    ("POST", f"{_U}/confirm"): "super_admin",
    ("POST", f"{_U}/{{relationship_id}}/reject"): "super_admin",
}
OWN_FILES = ("apps/api/services/note_checks.py", "apps/api/routers/replication_note_checks.py")


def self_routes(check, find):
    """rep12's own routes: BEFORE_REF → its own merge (or HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "rep12 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate at rep12's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "rep12's routes all still exist today with the same gate", f"{drift[:4]}")


def self_code(check, find):
    """rep12's own files at its own end point: the queue confirms ONLY through confirm_resolution and writes no
    link_state; the hook sits in the extraction's one write path, after routing."""
    src = VS.own_text(SPRINT_KEY, OWN_FILES[0]) or ""
    check("confirm_resolution(pool, rid" in src and not re.search(r"link_state\s*=\s*'resolved'", src)
          and not re.search(r"\bUPDATE\b[^;\"']*securities_global_relationships", src),
          "note_checks confirms only by calling confirm_resolution, once per edge; it writes no link_state",
          f"{len(src)} chars at the end point")
    ext = VS.own_text(SPRINT_KEY, "apps/api/services/note_terms_extraction.py") or ""
    body = ext.split("async def store_extraction(", 1)[-1].split("\nasync def ", 1)[0]
    i_route, i_hook = body.find("route_note_terms_row("), body.find("NC.on_terms_written(")
    check(0 <= i_route < i_hook and "stored = await store_extraction(" in ext,
          "the hook is the LAST step of store_extraction (after routing) and extract_terms persists through it")
    pa = VS.own_text(SPRINT_KEY, "apps/api/routers/pricing_admin.py") or ""
    check('on_terms_written(pool, str(note_terms_id), source="correction")' in pa,
          "a human correction of a terms row fires the same hook")


def self_checks(check, find):
    self_routes(check, find)
    self_code(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
import _verify_scope as VSC  # noqa: E402  (stdlib only) — live-written tables counted by fixture ids
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import note_checks as NC  # noqa: E402
from services import note_replication as N  # noqa: E402
from services import note_terms_extraction as NTE  # noqa: E402
from services import options_sleeve as OS  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "rep12verify"
PREFIX = "ZZR12"
NAME_PREFIX = "REP12 VERIFY"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
ZERO, ONE, HUNDRED = Decimal(0), Decimal(1), Decimal(100)
BP = Decimal("0.0001")
CENT = Decimal("0.01")
TENOR_DAYS = 730

CIK_STP, CIK_QUEUE, CIK_WATCH = f"{PREFIX}0001", f"{PREFIX}0002", f"{PREFIX}0003"
BANK, WATCHED = f"{NAME_PREFIX} Bank", f"{NAME_PREFIX} WATCHED Bank"
IDX_NAMES = {"IDX": f"{NAME_PREFIX} IDX", "IDX2": f"{NAME_PREFIX} IDX2"}
BUFFER = {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
          "protection_pct": "0.2", "cap_pct": "0.25", "participation_rate": "1"}
# key → (filer, cik, filing days before the seed day, raw underlyings, terms, confidence)
NOTES = {
    "WO": (WATCHED, CIK_WATCH, 2, [IDX_NAMES["IDX"], IDX_NAMES["IDX2"]], {**BUFFER, "basket_type": "worst_of"}, "high"),
    "BUF": (BANK, CIK_STP, 5, [IDX_NAMES["IDX"]], BUFFER, "high"),
    "BUF2": (BANK, CIK_STP, 6, [IDX_NAMES["IDX2"]], BUFFER, "high"),
    "BUF3": (BANK, CIK_STP, 7, [IDX_NAMES["IDX"]], BUFFER, "high"),
    "UNR": (BANK, CIK_STP, 8, [f"{NAME_PREFIX} Unmatched Co"], BUFFER, "high"),
    "REV": (BANK, CIK_QUEUE, 9, [IDX_NAMES["IDX"]], BUFFER, "needs_review"),
    "RST": (BANK, CIK_STP, 10, [IDX_NAMES["IDX"]], {"product_archetype": "digital", "basket_type": "single",
                                                    "protection_type": "buffer", "protection_pct": "0.25",
                                                    "cap_pct": "0.08"}, "high"),
}
EXPECT_VERDICT = {"WO": "not_replicable", "BUF": "static", "BUF2": "static", "BUF3": "static", "UNR": "static",
                  "REV": "static", "RST": "approximate"}
L0, LEVEL_NOW = Decimal("5000"), Decimal("5100")
PREMIUM = {("put", Decimal("1")): Decimal("250"), ("put", Decimal("0.8")): Decimal("90"),
           ("call", Decimal("1.25")): Decimal("60")}
DEFAULT_PREMIUM = Decimal("10")
BUF_NOTE_PRICE = Decimal("107.5")
THRESHOLD = Decimal("1.0")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001c{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN, U_STAFF_V, U_ADVISOR, U_MEMBER, U_ADMIN_B = (_id(110 + i) for i in range(6))
# uid → (org, users.role, short name, role granted (None = none), full name)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", None, "REP12 Super Admin"),
    U_ADMIN: (ORG_A, "org_admin", "admin", "org_admin", "REP12 Org Admin"),
    U_STAFF_V: (ORG_A, "member", "staffv", "support_staff", "REP12 Support Staff (view)"),
    U_ADVISOR: (ORG_A, "member", "advisor", "advisor", "REP12 Advisor (no replication)"),
    U_MEMBER: (ORG_A, "member", "member", "member", "REP12 Client Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP12 Org B Admin"),
}
ROLE_PERMS = {"support_staff": ("view_replication",), "advisor": (), "member": ("view_replication",)}

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "portfolio.securities_global_note_terms",
    "portfolio.securities_global_relationships", "portfolio.reference_filings", "portfolio.note_terms_stp_policy",
    "portfolio.option_contracts", "portfolio.adviser_quotes", "public.note_replicas", "public.note_replica_legs",
    "public.note_replica_comparisons", "public.note_checks", "public.note_check_settings",
)
NEW_TABLES = ("public.note_checks", "public.note_check_settings")
SEED_CHECK_LITERALS = (
    ("public.note_checks", "note_checks_trigger_chk", ("extraction", "sweep", "underlying_confirmed", "manual")),
    ("public.note_checks", "note_checks_status_chk", ("new", "reviewed", "dismissed")),
    ("public.note_checks", "note_checks_pricing_chk", ("priced", "not_priced", "no_replica")),
    ("portfolio.reference_filings", "reference_filings_form_type_check", ("424B2",)),
    ("portfolio.reference_filings", "reference_filings_extraction_status_check", ("extracted",)),
    ("portfolio.note_terms_stp_policy", "note_terms_stp_policy_form_type_chk", ("424B2",)),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_archetype_chk", ("buffered_note", "digital")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_basket_chk", ("single", "worst_of")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_confidence_chk", ("high", "needs_review")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_status_chk", ("final",)),
    ("portfolio.securities_global_relationships", "sec_global_rel_state_chk", ("ambiguous", "unresolved", "resolved")),
    ("portfolio.securities_global_relationships", "sec_global_rel_proposal_kind_chk", ("known_index", "single_name")),
    ("portfolio.securities_global_relationships", "sec_global_rel_proposal_confidence_chk",
     ("high", "needs_manual_match")),
    ("portfolio.securities_global", "securities_global_type_chk", ("index", "structured_note", "option")),
    ("portfolio.adviser_quotes", "adviser_quotes_kind_chk", ("index_level", "premium", "note_price")),
)
UNIQUES = ("note_checks_live_uq", "note_check_settings_live_uq", "uq_sec_global_rel_edge",
           "reference_filings_accession_document_unique", "note_terms_stp_policy_active_unique",
           "sec_global_note_terms_current_unique")

S: dict = {}
NETWORK_CALLS: list[str] = []
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


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def D(v) -> Decimal:
    return Decimal(str(v))


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def at(d: date, hour: int = 15) -> datetime:
    return datetime.combine(d, time(hour, 0), tzinfo=timezone.utc)


# ═══ DB helpers ═════════════════════════════════════════════════════════════
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


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def fixture_secs(conn) -> list:
    """The fixture securities: names 'REP12 VERIFY…' (the notes the faked extraction created, the indices) and the
    ZZR12 identifiers."""
    ids = {r["id"] for r in await platform(conn, "SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                           f"{NAME_PREFIX}%")}
    ids |= {r["global_security_id"] for r in await platform(
        conn, "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")}
    return sorted(ids, key=str)


async def fixture_filings(conn) -> list:
    return [r["id"] for r in await platform(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number "
                                                  "LIKE $1", f"{PREFIX}%")]


async def pk_cols(conn, table) -> str:
    return await platform(conn, """SELECT string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_index i
                                   JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                                   WHERE i.indrelid = $1::regclass AND i.indisprimary""", table, fetch="fetchval")


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch, keyed by each table's real primary key (from the catalog). Excluded and
    named: note_replicability (rep11's live verdict job writes it on its own schedule) and note_checks in real orgs
    (the live sweep writes them once rep12 is deployed) — the latter is asserted separately: no real-org check of a
    FIXTURE note."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    fx = [str(x) for x in await fixture_secs(conn)]
    fl = [str(x) for x in await fixture_filings(conn)]
    out = {}
    pk = await pk_cols(conn, "portfolio.note_term_readings")
    out["note_term_readings"] = await fingerprint(conn, f"SELECT {pk} AS k, id, reference_filing_id, note_terms_id, "
                                                        "field_key, value, status, created_at FROM "
                                                        "portfolio.note_term_readings")
    pk = await pk_cols(conn, "portfolio.reference_filings")
    out["reference_filings"] = await fingerprint(conn, f"SELECT {pk} AS k, cik, accession_number, content_hash, "
                                                       "extraction_status, filing_date FROM portfolio.reference_filings "
                                                       "WHERE NOT (id = ANY($1::uuid[]))", fl)
    out["note_terms"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global_note_terms WHERE NOT "
                                                "(global_security_id = ANY($1::uuid[]))", fx)
    out["relationships"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global_relationships WHERE NOT "
                                                   "(from_global_security_id = ANY($1::uuid[]))", fx)
    out["structured_notes"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global WHERE security_type = "
                                                      "'structured_note' AND NOT (id = ANY($1::uuid[]))", fx)
    out["stp_policies"] = await fingerprint(conn, "SELECT * FROM portfolio.note_terms_stp_policy WHERE cik NOT LIKE $1",
                                            f"{PREFIX}%")
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
    for t in ("public.note_replicas", "public.note_replica_comparisons", "portfolio.adviser_quotes"):
        col = "global_security_id"
        out[t.split(".")[1]] = await fingerprint(conn, f"SELECT * FROM {t} WHERE NOT (org_id = ANY($1::uuid[])) AND "
                                                       f"NOT ({col} = ANY($2::uuid[]))", orgs, fx)
    out["note_replica_legs"] = await fingerprint(conn, "SELECT * FROM public.note_replica_legs WHERE NOT (org_id = "
                                                       "ANY($1::uuid[]))", orgs)
    return out


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
    op = await operator_conn()
    try:
        secs = {r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                                f"{NAME_PREFIX}%")}
        secs |= {r["global_security_id"] for r in await op.fetch(
            "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
            f"{PREFIX}%")}
        secs = list(secs)
        opts = [r["global_security_id"] for r in await op.fetch(
            "SELECT global_security_id FROM portfolio.option_contracts WHERE underlying_global_security_id = "
            "ANY($1::uuid[])", secs)]
        allsecs = secs + opts
        filings = [r["id"] for r in await op.fetch("SELECT id FROM portfolio.reference_filings WHERE accession_number "
                                                   "LIKE $1", f"{PREFIX}%")]
        reps = [r["id"] for r in await op.fetch("SELECT id FROM public.note_replicas WHERE global_security_id = "
                                                "ANY($1::uuid[])", secs)]
        orgs, users = FIXTURE_ORGS, list(USERS)
        by_org = ("org_id = ANY($1::uuid[])", [orgs])
        org_or_sec = ("org_id = ANY($1::uuid[]) OR global_security_id = ANY($2::uuid[])", [orgs, secs])
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan = {t: by_org for t in (
            "public.note_check_settings", "portfolio.adviser_quotes", "public.domain_event_deliveries",
            "public.domain_events", "public.workflow_run_steps", "public.agent_proposals", "public.workflow_runs",
            "public.workflow_steps", "public.workflow_versions", "public.workflow_definitions", "public.org_settings")}
        plan["public.note_checks"] = org_or_sec
        plan["public.note_replicas"] = org_or_sec
        plan["public.note_replica_comparisons"] = org_or_sec
        plan["public.note_replica_legs"] = ("org_id = ANY($1::uuid[]) OR replica_id = ANY($2::uuid[])", [orgs, reps])
        plan["public.member_todos"] = org_or_user
        plan["public.audit_log"] = org_or_user
        plan["portfolio.note_replicability"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global_note_terms"] = (
            "global_security_id = ANY($1::uuid[]) OR reference_filing_id = ANY($2::uuid[])", [secs, filings])
        plan["portfolio.securities_global_relationships"] = (
            "from_global_security_id = ANY($1::uuid[]) OR to_global_security_id = ANY($1::uuid[]) OR "
            "proposed_global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.option_contracts"] = ("underlying_global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [allsecs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [allsecs])
        plan["portfolio.reference_filings"] = ("id = ANY($1::uuid[])", [filings])
        plan["portfolio.note_terms_stp_policy"] = ("cik LIKE $1", [f"{PREFIX}%"])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        cols = {r["t"] for r in await op.fetch(
            "SELECT table_schema || '.' || table_name AS t FROM information_schema.columns WHERE column_name = 'org_id'")}
        plan = {t: v for t, v in plan.items() if t in exists and (v is not by_org or t in cols)}
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               label="verify_rep12")
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
    fx = await fixture_secs(conn)
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.reference_filings WHERE accession_number LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.note_terms_stp_policy WHERE cik LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global_note_terms WHERE global_security_id = ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM portfolio.note_replicability WHERE global_security_id = ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM portfolio.securities_global_relationships WHERE from_global_security_id = "
         "ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM public.note_checks WHERE global_security_id = ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM public.note_replicas WHERE global_security_id = ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM portfolio.option_contracts c JOIN portfolio.securities_global g ON g.id = "
         "c.underlying_global_security_id WHERE g.name LIKE $1", [f"{NAME_PREFIX}%"]),
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


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep12"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
    try:
        return res.status_code, json.loads(res.content.decode("utf-8") or "{}")
    except ValueError:
        return res.status_code, {"raw": res.content[:300].decode("utf-8", "replace")}


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep12: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    # the hook and every re-check reach ONLY the fixture orgs (never a real org, for a fixture note)
    NC.HOOK_INCLUDES_TEST_ORGS = True
    NC.HOOK_ONLY_ORGS = [str(o) for o in FIXTURE_ORGS]


class _ctx:
    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def quote(uid, gsid, kind, value, as_of: datetime, source="REP12 verify desk") -> dict:
    c, o = await api(uid, "POST", f"{_R}/options/quotes", {"global_security_id": str(gsid), "kind": kind,
                                                           "value": str(value), "source": source,
                                                           "as_of": as_of.isoformat()})
    if c != 201:
        raise AssertionError(f"quote {kind} on {gsid}: {c} {str(o)[:200]}")
    return o.get("quote") or o


def values_of(key: str, *, cap=None) -> tuple[dict, dict]:
    """What the (faked) model returned for a fixture note: the typed values and field_status."""
    t = dict(NOTES[key][4])
    if cap is not None:
        t["cap_pct"] = cap
    vals = {k: (D(v) if k.endswith(("_pct", "_rate")) else v) for k, v in t.items()}
    vals.update({"return_basis": "price", "is_decrement_index": False, "notional_currency": "USD",
                 "autocall_frequency": "none", "initial_valuation_date": S["initial"],
                 "final_valuation_date": S["final"], "tenor_years": S["tenor"]})
    fs = {k: "extracted" for k, v in vals.items() if v is not None}
    fs["terms_status"] = "extracted"
    return vals, fs


async def fake_extract(key: str, *, existing_id=None, cap=None) -> dict:
    """The extraction with its model step faked: store_extraction — the same write path extract_terms uses, the
    one the rep12 hook lives in."""
    vals, fs = values_of(key, cap=cap)
    pool = await get_pool()
    with _ctx(None, True):
        return await NTE.store_extraction(
            pool, filing_id=str(S["filing"][key]), existing_id=str(existing_id) if existing_id else None,
            security_name=f"{NAME_PREFIX} NOTE {key}", cusip=None, values=vals, field_status=fs,
            confidence=NOTES[key][5], char_start=None, char_end=None, underlyings=list(NOTES[key][3]),
            derived_status="final", disagreements={})


async def live_checks(conn, org, note_key) -> list:
    return [dict(r) for r in await platform(
        conn, "SELECT * FROM public.note_checks WHERE org_id = $1 AND global_security_id = $2 AND valid_to IS NULL "
              "AND system_to IS NULL", org, S["note"][note_key])]


async def all_checks(conn, org, note_key) -> list:
    return [dict(r) for r in await platform(
        conn, "SELECT * FROM public.note_checks WHERE org_id = $1 AND global_security_id = $2 AND system_to IS NULL "
              "ORDER BY valid_from", org, S["note"][note_key])]


async def fixture_check_count(conn) -> int:
    """Checks in the fixture orgs on the SYSTEM-live axis (an alert or a review archives a copy of a check — that
    copy is history, never a second check)."""
    return int(await platform(conn, "SELECT count(*) FROM public.note_checks WHERE org_id = ANY($1::uuid[]) AND "
                                    "system_to IS NULL", FIXTURE_ORGS, fetch="fetchval"))


async def edge_of(conn, key, raw) -> dict:
    return dict(await platform(conn, "SELECT * FROM portfolio.securities_global_relationships WHERE "
                                     "from_global_security_id = $1 AND raw_underlying_text = $2 AND valid_to IS NULL "
                                     "AND system_to IS NULL", S["note"][key], raw, fetch="fetchrow"))


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    ext = (API_DIR / "services" / "note_terms_extraction.py").read_text(encoding="utf-8")
    check("stored = await store_extraction(" in ext and "NC.on_terms_written(" in ext,
          "1a: extract_terms writes the live terms row through store_extraction, whose last step is the rep12 hook")
    daily = (API_DIR / "services" / "ai_daily_runs.py").read_text(encoding="utf-8")
    check("securities_global_note_terms" not in daily,
          "1a: the daily document-reading run never writes a live terms row (readings / staging only) — the sweep is "
          "its safety net")
    idx = await platform(conn, "SELECT indexdef FROM pg_indexes WHERE indexname = 'note_replicability_live_uq'",
                         fetch="fetchval")
    check("note_terms_id" in (idx or ""), "1b: rep11's verdict is keyed by the terms version (note_terms_id)",
          (idx or "")[-90:])
    fx = [str(x) for x in await fixture_secs(conn)]
    rows = await platform(conn, "SELECT link_state, (proposed_global_security_id IS NOT NULL) AS one, count(*) AS n "
                                "FROM portfolio.securities_global_relationships WHERE relationship_type = "
                                "'underlying_of' AND valid_to IS NULL AND system_to IS NULL AND NOT "
                                "(from_global_security_id = ANY($1::uuid[])) GROUP BY 1, 2 ORDER BY 1, 2", fx)
    find("1c real underlying edges (state, one proposal?, n)",
         f"{[(r['link_state'], r['one'], r['n']) for r in rows]} — an 'ambiguous' edge carries exactly one proposed "
         "target (a CHECK), so 'several proposals' cannot occur; 'unresolved' edges open the manual match")
    amb = await platform(conn, "SELECT id, proposed_global_security_id FROM portfolio.securities_global_relationships "
                               "WHERE link_state = 'ambiguous' AND valid_to IS NULL AND system_to IS NULL AND NOT "
                               "(from_global_security_id = ANY($1::uuid[]))", fx)
    VS.when_empty(amb, "1c real ambiguous edges", lambda r: r["proposed_global_security_id"] is not None,
                  check=check, find=find)
    find("1d a new issue", "dated by the 424B2 filing date (reference_filings.filing_date); with no filing, the day "
         "its terms were first written — the pricing date is missing on some real rows")
    holders = await platform(conn, "SELECT DISTINCT r.name FROM roles r JOIN role_permissions rp ON rp.role_id = r.id "
                                   "JOIN permissions p ON p.id = rp.permission_id JOIN organizations o ON o.id = "
                                   "r.org_id WHERE p.name = 'view_replication' AND NOT o.is_test")
    find("1e alerts", f"roles holding view_replication in real orgs: {sorted(r['name'] for r in holders)} — alerts "
         "are system-opened human tasks (member_todos fan-out to holders of view_replication); a non-internal "
         "recipient's to-do is dismissed at once")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep12.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep12.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    for t in NEW_TABLES:
        sch, name = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", sch, name)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role is not None and role["rolbypassrls"] is False,
          "the verify's own connection does NOT bypass RLS (isolation checks are real)", f"{dict(role or {})}")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    uq = {r["indexname"] for r in await platform(conn, "SELECT indexname FROM pg_indexes WHERE indexname = "
                                                       "ANY($1::text[])", list(UNIQUES))}
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                                   "ANY($1::regclass[])", list(NEW_TABLES), fetch="fetchval"))
    check(not bad and uq == set(UNIQUES) and fks >= 11,
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints); the UNIQUE indexes "
          "and FKs the seeds rely on are in the catalog", "; ".join(bad) + f" uq={sorted(uq)} fks={fks}")
    S["literals_ok"] = not bad and uq == set(UNIQUES)


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                       fetch="fetchval")
    f = await platform(conn, "SELECT count(*) FROM portfolio.reference_filings WHERE accession_number LIKE $1 OR cik "
                             "LIKE $1", f"{PREFIX}%", fetch="fetchval")
    p = await platform(conn, "SELECT count(*) FROM portfolio.note_terms_stp_policy WHERE cik LIKE $1", f"{PREFIX}%",
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(f) == 0 and int(p) == 0 and int(o) == 0,
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', the fixture orgs) match no real row",
                 f"{n}/{k}/{f}/{p}/{o}")


async def seed(conn) -> None:
    S["seed_day"] = today()
    S["initial"] = S["seed_day"] - timedelta(days=30)
    S["final"] = S["initial"] + timedelta(days=TENOR_DAYS)
    S["tenor"] = (Decimal(TENOR_DAYS) / Decimal(365)).quantize(Decimal("0.0001"))
    S["filing"], S["filed_on"], S["note"], S["terms"], S["idx"] = {}, {}, {}, {}, {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP12 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for rname, perms in ROLE_PERMS.items():
            rid = await rbac.ensure_role(conn, ORG_A, rname, f"REP12 verify — {rname}")
            for p in perms:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[rname] = rid
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for i, (key, name) in enumerate(IDX_NAMES.items()):
            sid = await SG.create_security(conn, name=name, security_type="index", currency_code="USD",
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S["idx"][key] = UUID(str(sid))
        for i, (key, (filer, cik, days, _u, _t, _c)) in enumerate(NOTES.items()):
            S["filed_on"][key] = S["seed_day"] - timedelta(days=days)
            S["filing"][key] = await conn.fetchval(
                """INSERT INTO portfolio.reference_filings (cik, filer_name, form_type, accession_number, filing_date,
                       primary_document, source_url, extraction_status, extracted_text)
                   VALUES ($1, $2, '424B2', $3, $4, $5, $6, 'extracted', $7) RETURNING id""",
                cik, filer, f"{PREFIX}-{i:04d}", S["filed_on"][key], f"{PREFIX.lower()}{i}.htm",
                f"https://example.invalid/{PREFIX.lower()}/{i}", f"{NAME_PREFIX} fixture filing {key}")
        await conn.execute("INSERT INTO portfolio.note_terms_stp_policy (cik, form_type, enabled, notes) VALUES "
                           "($1, '424B2', true, $2)", CIK_STP, f"{NAME_PREFIX} fixture STP grant")
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          list(USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs and users are is_test = true",
          f"{len(us)}/{len(USERS)} users, {len(os_)}/2 orgs")
    S["seeded"] = True


async def y_settings(conn):
    section("[Y] Settings: the org's threshold + watches (a person's change, valid axis)")
    body = {"enabled": True, "fee_alert_threshold_pct": str(THRESHOLD), "watch_archetypes": [],
            "watch_issuers": ["WATCHED"], "watch_underlyings": []}
    cv, ov = await api(U_STAFF_V, "POST", f"{_C}/settings", body)
    ca, oa = await api(U_ADMIN, "POST", f"{_C}/settings", body)
    rows = await scoped(conn, ORG_A, "SELECT * FROM public.note_check_settings WHERE org_id = $1 ORDER BY valid_from",
                        ORG_A)
    check(cv == 403 and "manage_replication" in json.dumps(ov) and ca == 200 and len(rows) == 1
          and rows[0]["created_by"] == U_ADMIN and D(rows[0]["fee_alert_threshold_pct"]) == THRESHOLD
          and list(rows[0]["watch_issuers"]) == ["WATCHED"],
          "the view-only staff member is refused (403 naming manage_replication); the admin's identical request "
          "writes one settings row with the admin as creator", f"{cv} {ca} {[dict(r) for r in rows][:1]}")
    ca2, _ = await api(U_ADMIN, "POST", f"{_C}/settings", body)
    rows = await scoped(conn, ORG_A, "SELECT valid_to FROM public.note_check_settings WHERE org_id = $1 ORDER BY "
                                     "valid_from", ORG_A)
    check(ca2 == 200 and len(rows) == 2 and rows[0]["valid_to"] is not None and rows[1]["valid_to"] is None,
          "a change closes the live settings row and writes a new one (one live row)", f"{[dict(r) for r in rows]}")


async def y_hook(conn):
    section("[Y] The hook: a new terms version (faked extraction) → one check per org; re-run adds nothing")
    before = await fixture_check_count(conn)
    S["stored"] = {}
    for key in NOTES:
        out = await fake_extract(key)
        S["stored"][key] = out
        S["note"][key] = UUID(out["global_security_id"])
        S["terms"][key] = UUID(out["note_terms_id"])
    bad = []
    for key in NOTES:
        for org in FIXTURE_ORGS:
            live = await live_checks(conn, org, key)
            if len(live) != 1 or live[0]["verdict"] != EXPECT_VERDICT[key] or live[0]["note_terms_id"] != S["terms"][key] \
                    or live[0]["trigger"] != "extraction":
                bad.append((key, str(org)[-4:], [(r["verdict"], r["trigger"]) for r in live]))
    check(not bad and await fixture_check_count(conn) - before == 2 * len(NOTES),
          f"each of the {len(NOTES)} faked extractions wrote exactly one check per fixture org, with rep11's verdict",
          f"{bad[:4]}")
    routes = {k: S["stored"][k]["routing_decision"] for k in NOTES}
    check(routes["BUF"] == "stp" and routes["REV"] == "queued",
          "routing ran on the real path: the STP filer's note is 'stp', the needs-review note 'queued'", f"{routes}")
    buf = (await live_checks(conn, ORG_A, "BUF"))[0]
    rev = (await live_checks(conn, ORG_A, "REV"))[0]
    wo = (await live_checks(conn, ORG_A, "WO"))[0]
    nb = json.loads(buf["not_built_reasons"])
    check(buf["replica_outcome"] == "not_built" and buf["replica_id"] is None and any("Underlying" in x for x in nb)
          and buf["pricing_status"] == "no_replica" and buf["underlying_state"] == "unresolved",
          "an unresolved underlying → no replica, the reason recorded", f"{nb}")
    check(rev["replica_outcome"] == "not_built" and any("review" in x.lower() for x in json.loads(rev["not_built_reasons"])),
          "terms that are not approved (queued / needs review) → no replica, the reason recorded",
          f"{rev['not_built_reasons']}")
    check(wo["replica_outcome"] == "not_built" and wo["verdict"] == "not_replicable" and wo["replica_id"] is None,
          "not replicable → no replica", f"{wo['not_built_reasons']}")
    n_rep = int(await platform(conn, "SELECT count(*) FROM public.note_replicas WHERE org_id = ANY($1::uuid[])",
                               FIXTURE_ORGS, fetch="fetchval"))
    check(n_rep == 0, "no replica exists anywhere yet (no underlying is confirmed)", f"{n_rep}")
    # idempotent: the hook again, then the sweep
    n0 = await fixture_check_count(conn)
    again = [await NC.on_terms_written(None, S["terms"][k], source="extraction") for k in NOTES]
    acts = {o["action"] for r in again for o in r["orgs"]}
    pool = await get_pool()
    sw = await NC.sweep(pool, include_test_orgs=True, orgs=[str(o) for o in FIXTURE_ORGS],
                        note_ids=[str(S["note"][k]) for k in NOTES])
    check(acts == {"unchanged"} and sw["checked"] == 0 and await fixture_check_count(conn) == n0,
          "re-running the hook and the sweep over unchanged terms adds nothing", f"{acts} {sw}")
    S["alert_wo"] = wo["alert_task_id"]


async def y_restatement(conn):
    section("[Y] A restatement (a new terms version) → a new check; one live check per (org, note)")
    old = S["terms"]["RST"]
    out = await fake_extract("RST", existing_id=old, cap="0.09")
    S["terms"]["RST"] = UUID(out["note_terms_id"])
    for org in FIXTURE_ORGS:
        allr = await all_checks(conn, org, "RST")
        live = [r for r in allr if r["valid_to"] is None]
        check(len(allr) == 2 and len(live) == 1 and live[0]["note_terms_id"] == S["terms"]["RST"]
              and allr[0]["note_terms_id"] == old and allr[0]["valid_to"] is not None and S["terms"]["RST"] != old,
              f"org …{str(org)[-4:]}: the restated version has its own check; the earlier one is closed (valid_to)",
              f"{[(str(r['note_terms_id'])[-6:], r['valid_to'] is None) for r in allr]}")
    n = int(await platform(conn, "SELECT count(*) FROM (SELECT org_id, global_security_id FROM public.note_checks "
                                 "WHERE org_id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL GROUP BY 1, 2 "
                                 "HAVING count(*) > 1) x", FIXTURE_ORGS, fetch="fetchval"))
    check(n == 0, "one live check per (org, note), over every live row", f"{n} duplicates")


async def y_sweep_catches(conn):
    section("[Y] The sweep catches a change the hook never saw (proposals appear) — once")
    for key, idx in (("BUF", "IDX"), ("BUF2", "IDX2"), ("BUF3", "IDX")):
        await platform(conn, "UPDATE portfolio.securities_global_relationships SET link_state = 'ambiguous', "
                             "proposed_global_security_id = $3, proposal_confidence = 'high', proposal_kind = "
                             "'known_index', proposed_at = now() WHERE from_global_security_id = $1 AND "
                             "raw_underlying_text = $2 AND valid_to IS NULL AND system_to IS NULL",
                       S["note"][key], NOTES[key][3][0], S["idx"][idx], fetch="execute")
    await platform(conn, "UPDATE portfolio.securities_global_relationships SET proposal_confidence = "
                         "'needs_manual_match', proposal_kind = 'single_name', proposed_at = now() WHERE "
                         "from_global_security_id = $1 AND valid_to IS NULL AND system_to IS NULL", S["note"]["UNR"],
                   fetch="execute")
    pool = await get_pool()
    scope = dict(include_test_orgs=True, orgs=[str(o) for o in FIXTURE_ORGS], note_ids=[str(S["note"][k]) for k in NOTES])
    s1 = await NC.sweep(pool, **scope)
    s2 = await NC.sweep(pool, **scope)
    states = {k: (await live_checks(conn, ORG_A, k))[0]["underlying_state"] for k in ("BUF", "BUF2", "BUF3", "UNR")}
    check(s1["checked"] == 6 and s2["checked"] == 0 and states["BUF"] == "ambiguous" and states["UNR"] == "unresolved",
          "the sweep re-checked exactly the three notes whose underlying changed (× 2 orgs); a second sweep wrote "
          "nothing", f"{s1} {s2} {states}")


async def y_quotes(conn):
    section("Quotes for the buffered note on IDX (start level, current level, premiums, the note's price)")
    v = N.spec_in(json.loads((await platform(conn, "SELECT spec FROM portfolio.note_replicability WHERE note_terms_id = "
                                                   "$1 AND valid_to IS NULL", S["terms"]["BUF"], fetch="fetchval"))))
    legs = N.legs_for(v, spread=N.DEFAULT_SPREAD)
    S["premium_by_gsid"] = {}
    pool = await get_pool()
    with _ctx(None, True):
        for x in legs:
            if x["kind"] != "option":
                continue
            strike = (D(x["strike_pct"]) * L0).quantize(CENT, rounding=ROUND_HALF_EVEN)
            c = await OS.find_or_create_contract(
                pool, actor_id=U_SUPER, underlying_global_security_id=S["idx"]["IDX"], option_type=x["option_type"],
                strike=strike, expiry=S["final"], exercise_style="european", settlement="cash", is_flex=True,
                multiplier=Decimal("100"), broad_based_index=True)
            S["premium_by_gsid"][str(c["global_security_id"])] = PREMIUM.get((x["option_type"], D(x["strike_pct"])),
                                                                             DEFAULT_PREMIUM)
    now = datetime.now(timezone.utc)
    await quote(U_ADMIN, S["idx"]["IDX"], "index_level", L0, at(S["initial"]))
    await quote(U_ADMIN, S["idx"]["IDX"], "index_level", LEVEL_NOW, now - timedelta(minutes=5))
    for gsid, prem in S["premium_by_gsid"].items():
        await quote(U_ADMIN, gsid, "premium", prem, now - timedelta(minutes=5))
    await quote(U_ADMIN, S["note"]["BUF"], "note_price", BUF_NOTE_PRICE, now - timedelta(minutes=5))
    S["quotes_ok"] = True


async def expected_cost(conn, replica_id) -> Decimal:
    """The replica's cost per 100, computed HERE from its legs and the quotes this script entered (rep11's rule:
    underlying at current ÷ start level; options at ± ratio × premium ÷ start level)."""
    legs = await platform(conn, "SELECT l.kind, l.direction, l.ratio, c.global_security_id FROM public.note_replica_legs "
                                "l LEFT JOIN portfolio.option_contracts c ON c.id = l.contract_id WHERE l.replica_id = $1 "
                                "AND l.valid_to IS NULL AND l.system_to IS NULL", replica_id)
    total = ZERO
    for x in legs:
        if x["kind"] == "underlying":
            total += D(x["ratio"]) * LEVEL_NOW / L0
        elif x["kind"] == "option":
            sign = ONE if x["direction"] == "long" else -ONE
            total += sign * D(x["ratio"]) * S["premium_by_gsid"][str(x["global_security_id"])] / L0
    return (total * HUNDRED).quantize(BP, rounding=ROUND_HALF_EVEN)


async def y_queue(conn):
    section("[Y] Underlying queue: one-click and multi-confirm via confirm_resolution only; Super Admin only; "
            "the note re-checked")
    ca, oa = await api(U_ADMIN, "GET", _U)
    cs, os_ = await api(U_SUPER, "GET", _U)
    one = {i["id"] for i in (os_.get("groups") or {}).get("one_proposal", [])}
    manual = {i["id"] for i in (os_.get("groups") or {}).get("manual", [])}
    e = {k: await edge_of(conn, k, NOTES[k][3][0]) for k in ("BUF", "BUF2", "BUF3", "UNR")}
    check(ca == 403 and cs == 200 and {str(e[k]["id"]) for k in ("BUF", "BUF2", "BUF3")} <= one
          and str(e["UNR"]["id"]) in manual and set((os_.get("counts") or {})) >= {"resolved", "ambiguous", "unresolved"},
          "GET queue: org admin 403, Super Admin 200; one-proposal edges in the one-click group, the unmatched edge "
          "in the manual group; counts per state at the top", f"{ca} {cs} {os_.get('counts')}")
    # a non-super is refused and nothing changes
    cr, orr = await api(U_ADMIN, "POST", f"{_U}/confirm", {"relationship_ids": [str(e["BUF2"]["id"])]})
    still = await edge_of(conn, "BUF2", NOTES["BUF2"][3][0])
    check(cr == 403 and still["link_state"] == "ambiguous" and still["resolved_by"] is None,
          "the org admin's identical confirm is refused (403) and the edge is unchanged", f"{cr} {still['link_state']}")
    # one click
    c1, o1 = await api(U_SUPER, "POST", f"{_U}/confirm", {"relationship_ids": [str(e["BUF"]["id"])]})
    got = await edge_of(conn, "BUF", NOTES["BUF"][3][0])
    check(c1 == 200 and o1.get("confirmed") == 1 and got["link_state"] == "resolved" and got["resolved_by"] == U_SUPER
          and got["to_global_security_id"] == S["idx"]["IDX"] and got["resolved_at"] is not None,
          "one click: the edge is resolved to its proposal, recorded with the Super Admin who clicked",
          f"{c1} {str(o1)[:200]}")
    # the manual-group edge is refused in the one-click path
    cu, ou = await api(U_SUPER, "POST", f"{_U}/confirm", {"relationship_ids": [str(e["UNR"]["id"])]})
    unr = await edge_of(conn, "UNR", NOTES["UNR"][3][0])
    check(cu == 200 and ou.get("confirmed") == 0 and not (ou.get("results") or [{}])[0].get("ok")
          and unr["link_state"] == "unresolved",
          "an edge with no proposal is refused by one-click (open the manual match); it stays unresolved",
          f"{str(ou.get('results'))[:200]}")
    # multi-confirm
    ids = [str(e["BUF2"]["id"]), str(e["BUF3"]["id"])]
    c2, o2 = await api(U_SUPER, "POST", f"{_U}/confirm", {"relationship_ids": ids})
    g2 = [await edge_of(conn, k, NOTES[k][3][0]) for k in ("BUF2", "BUF3")]
    check(c2 == 200 and o2.get("confirmed") == 2 and all(g["link_state"] == "resolved" and g["resolved_by"] == U_SUPER
                                                         and g["resolved_at"] is not None for g in g2)
          and g2[0]["id"] != g2[1]["id"],
          "multi-confirm: one confirmation per edge, each recorded with the user", f"{c2} {str(o2.get('results'))[:200]}")
    # the single writer: by code scan and by row
    hits = []
    for base in ("services", "routers", "models", "migrations"):
        for p in (API_DIR / base).rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"\bUPDATE\b", text):
                tail = text[m.end():]
                end = min([i for i in (tail.find(";"), tail.find('"""'), tail.find("'''")) if i != -1] or [len(tail)])
                if re.search(r"link_state\s*=\s*'resolved'", tail[:end]):
                    hits.append(f"{p.relative_to(REPO)}:{text[:m.start()].count(chr(10)) + 1}")
    check(len(hits) == 1 and hits[0].startswith("apps/api/services/underlying_resolution.py"),
          "code scan: EXACTLY ONE UPDATE writes link_state='resolved' — confirm_resolution", f"{hits}")
    notes = [r["resolution_notes"] or "" for r in [got] + g2]
    check(all(n.startswith(f"resolved by {U_SUPER}") for n in notes),
          "by row: every confirmed edge carries confirm_resolution's own resolution note naming the user", f"{notes}")
    S["queue_ok"] = True


async def y_drafts(conn):
    section("[Y] After the confirmation: the notes re-checked; a DRAFT replica (never approved); the comparison")
    buf = (await live_checks(conn, ORG_A, "BUF"))[0]
    rep = dict(await platform(conn, "SELECT * FROM public.note_replicas WHERE id = $1", buf["replica_id"],
                              fetch="fetchrow")) if buf["replica_id"] else {}
    check(buf["trigger"] == "underlying_confirmed" and buf["underlying_state"] == "resolved"
          and buf["replica_outcome"] == "prepared" and rep.get("status") == "draft" and rep.get("created_by") is None
          and list(rep.get("maker_ids") or []) == [] and rep.get("checker_id") is None
          and rep.get("approval_task_id") is None and rep.get("start_level") == L0,
          "static + one RESOLVED underlying + approved terms → a DRAFT replica prepared by the check, with no maker "
          "and no approval", f"{buf['trigger']} {buf['replica_outcome']} {rep.get('status')} {rep.get('created_by')}")
    S["rep_buf"] = buf["replica_id"]
    cmp_ = dict(await platform(conn, "SELECT * FROM public.note_replica_comparisons WHERE id = $1",
                               buf["comparison_id"], fetch="fetchrow")) if buf["comparison_id"] else {}
    cost = await expected_cost(conn, buf["replica_id"])
    fee = BUF_NOTE_PRICE - cost
    S["cost"] = cost
    check(buf["pricing_status"] == "priced" and cmp_.get("status") == "priced"
          and D(cmp_.get("replica_cost_pct")) == cost and D(cmp_.get("embedded_fee_pct")) == fee
          and D(buf["embedded_fee_pct"]) == fee and cmp_.get("computed_source") == "automatic_check"
          and cmp_.get("computed_by") is None,
          f"quotes present → the comparison is exact (rep11's math, recomputed here): cost {cost}, fee {fee}; "
          "recorded as the automatic check's, by no person",
          f"{cmp_.get('replica_cost_pct')} / {cmp_.get('embedded_fee_pct')} / {buf['embedded_fee_pct']}")
    b3 = (await live_checks(conn, ORG_A, "BUF3"))[0]
    miss = json.loads(b3["missing"])
    check(b3["replica_outcome"] == "prepared" and b3["pricing_status"] == "not_priced" and len(miss) == 1
          and "note's price" in miss[0] and b3["embedded_fee_pct"] is None,
          "one quote missing (the note's price) → 'not priced', naming exactly that gap", f"{miss}")
    S["rep_buf3"] = b3["replica_id"]
    b2 = (await live_checks(conn, ORG_A, "BUF2"))[0]
    m2 = json.loads(b2["missing"])
    check(b2["pricing_status"] == "not_priced" and any("start level" in m for m in m2) and len(m2) >= 2,
          "no level for the underlying → 'not priced', naming the start level among the gaps", f"{m2}")
    nb = (await live_checks(conn, ORG_B, "BUF"))[0]
    check(nb["replica_outcome"] == "prepared" and nb["pricing_status"] == "not_priced",
          "org B (no quotes of its own — quotes are per org) gets its own DRAFT, not priced", f"{nb['missing']}")
    appr = int(await platform(conn, "SELECT count(*) FROM public.note_replicas WHERE org_id = ANY($1::uuid[]) AND "
                                    "(status <> 'draft' OR checker_id IS NOT NULL OR approval_task_id IS NOT NULL)",
                              FIXTURE_ORGS, fetch="fetchval"))
    check(appr == 0, "no replica the check prepared was submitted or approved", f"{appr}")
    aud = await platform(conn, "SELECT payload FROM audit_log WHERE org_id = $1 AND action = 'notes.replica_built' AND "
                               "resource_id = $2", ORG_A, buf["replica_id"])
    check(len(aud) == 1 and json.loads(aud[0]["payload"]).get("source") == "automatic_check",
          "the build is audited as the automatic check's", f"{[a['payload'] for a in aud]}")
    # the hook again over the confirmed state adds nothing
    n0 = await fixture_check_count(conn)
    again = await NC.on_terms_written(None, S["terms"]["BUF"], source="extraction")
    check({o["action"] for o in again["orgs"]} == {"unchanged"} and await fixture_check_count(conn) == n0,
          "re-running the hook after the confirmation adds nothing", f"{again['orgs']}")


async def tasks_for(conn, task_id) -> list:
    return [dict(r) for r in await platform(conn, "SELECT user_id, status FROM member_todos WHERE related_type = "
                                                  "'workflow_run_step' AND related_id = $1", task_id)]


async def y_alerts(conn):
    section("[Y] Alerts: above the threshold and for a watched issuer; not below; staff only")
    buf = (await live_checks(conn, ORG_A, "BUF"))[0]
    reasons = {r["code"] for r in json.loads(buf["alert_reasons"])}
    check("fee_threshold" in reasons and buf["alert_task_id"] is not None,
          f"the priced note's estimate ({buf['embedded_fee_pct']}) is above the org's threshold ({THRESHOLD}) → an "
          "alert (a human task)", f"{reasons}")
    wo = (await live_checks(conn, ORG_A, "WO"))[0]
    check({r["code"] for r in json.loads(wo["alert_reasons"])} == {"watched_issuer"} and wo["alert_task_id"] is not None,
          "a note from a watched issuer → an alert", f"{wo['alert_reasons']}")
    others = [(k, json.loads((await live_checks(conn, ORG_A, k))[0]["alert_reasons"]))
              for k in ("BUF2", "UNR", "REV", "RST")]
    check(all(r == [] for _, r in others), "no alert for an unpriced note from an unwatched issuer", f"{others}")
    nb = [json.loads((await live_checks(conn, ORG_B, k))[0]["alert_reasons"]) for k in ("BUF", "WO")]
    check(nb == [[], []], "org B (no settings) alerts on nothing", f"{nb}")
    td = await tasks_for(conn, buf["alert_task_id"])
    by = {r["user_id"]: r["status"] for r in td}
    check(by.get(U_ADMIN) == "open" and by.get(U_STAFF_V) == "open" and by.get(U_MEMBER) in (None, "dismissed")
          and U_ADVISOR not in by,
          "the alert reaches the internal holders of view_replication; the client member's to-do is withheld; the "
          "adviser without the permission gets none", f"{by}")
    # below / above the threshold, priced LATER by a person (rep11's own price route)
    now = datetime.now(timezone.utc)
    await quote(U_ADMIN, S["note"]["BUF3"], "note_price", S["cost"] + Decimal("0.5"), now - timedelta(minutes=2))
    cp, op_ = await api(U_ADMIN, "POST", f"{_N}/replicas/{S['rep_buf3']}/price", {})
    pool = await get_pool()
    n_below = await NC.alert_sweep(pool, str(ORG_A))
    b3 = (await live_checks(conn, ORG_A, "BUF3"))[0]
    check(cp == 201 and n_below == 0 and b3["alert_task_id"] is None,
          "priced below the threshold (0.5) → no alert", f"{cp} {n_below} {str(op_)[:160] if cp != 201 else ''}")
    await quote(U_ADMIN, S["note"]["BUF3"], "note_price", S["cost"] + Decimal("2"), now - timedelta(minutes=1))
    cp2, _ = await api(U_ADMIN, "POST", f"{_N}/replicas/{S['rep_buf3']}/price", {})
    n_above = await NC.alert_sweep(pool, str(ORG_A))
    n_again = await NC.alert_sweep(pool, str(ORG_A))
    b3 = (await live_checks(conn, ORG_A, "BUF3"))[0]
    check(cp2 == 201 and n_above == 1 and n_again == 0 and b3["alert_task_id"] is not None
          and any(r["code"] == "fee_threshold" for r in json.loads(b3["alert_reasons"])),
          "priced later above the threshold (2.0) → one alert; the alert sweep again adds none", f"{n_above} {n_again}")
    S["alert_buf"] = buf["alert_task_id"]


async def y_feed(conn):
    section("[Y] Feed: newest first, filters, sort by the estimate (unpriced last)")
    c, o = await api(U_ADMIN, "GET", _C)
    rows = o.get("rows") or []
    keys = {str(S["note"][k]): k for k in NOTES}
    got = [keys.get(r["global_security_id"]) for r in rows]
    want = [k for k, _ in sorted(S["filed_on"].items(), key=lambda kv: kv[1], reverse=True)]
    check(c == 200 and got == want, "newest first by the issue (filing) date", f"{c} {got} vs {want}")
    check(all(r["issued_on"] == S["filed_on"][keys[r["global_security_id"]]].isoformat()
              and r["issued_basis"] == "filing_date" for r in rows) and bool(rows),
          "every row's issue date is its 424B2 filing date", f"{[(r['issued_on'], r['issued_basis']) for r in rows][:3]}")
    check(all(r["note"]["link"] == f"/portfolio/replication/notes/{r['global_security_id']}" for r in rows) and bool(rows),
          "each row links to the note page")

    async def subset(query, pred, label):
        cq, oq = await api(U_ADMIN, "GET", f"{_C}?{query}")
        got_ = {keys.get(r["global_security_id"]) for r in oq.get("rows") or []}
        exp = {k for k in NOTES if pred(k)}
        check(cq == 200 and got_ == exp and 0 < len(exp) < len(NOTES), f"filter {label}: exactly the matching notes "
              "(non-empty, a strict subset)", f"{cq} {sorted(got_)} vs {sorted(exp)}")

    await subset("verdict=not_replicable", lambda k: EXPECT_VERDICT[k] == "not_replicable", "verdict")
    await subset("archetype=digital", lambda k: NOTES[k][4]["product_archetype"] == "digital", "archetype")
    await subset("issuer=WATCHED", lambda k: "WATCHED" in NOTES[k][0], "issuer")
    await subset("underlying=IDX2", lambda k: any("IDX2" in u for u in NOTES[k][3]), "underlying")
    d0 = S["seed_day"] - timedelta(days=6)
    await subset(f"date_from={d0.isoformat()}", lambda k: S["filed_on"][k] >= d0, "date from")
    cf, of = await api(U_ADMIN, "GET", f"{_C}?sort=fee")
    fr = of.get("rows") or []
    priced = [keys.get(r["global_security_id"]) for r in fr if r["estimate"]["priced"]]
    tail = fr[len(priced):]
    fees = [D(r["estimate"]["embedded_fee_pct"]) for r in fr[:len(priced)]]
    check(cf == 200 and priced == ["BUF", "BUF3"] and fees == sorted(fees, reverse=True)
          and all(not r["estimate"]["priced"] and r["estimate"]["text"].startswith("not priced") for r in tail)
          and len(fr) == len(NOTES),
          "sort by the estimate: priced rows highest first, every unpriced row last saying what it needs",
          f"{priced} {fees} {[r['estimate']['text'][:40] for r in tail][:3]}")
    v = o.get("vocabularies") or {}
    check(bool(v.get("verdicts")) and bool(v.get("sorts")) and v.get("estimate_label") == "estimate"
          and (o.get("permissions") or {}).get("can_write") is True and bool(v.get("editable")),
          "the envelope carries labels, vocabularies and permissions (editable for the admin)")
    cv, ov = await api(U_STAFF_V, "GET", _C)
    check(cv == 200 and (ov.get("vocabularies") or {}).get("editable") == [] and ov.get("actions") == []
          and all(r.get("actions") == [] for r in ov.get("rows") or []) and bool(ov.get("rows")),
          "a view-only staff member: rows, no actions, an empty editable list", f"{cv} {ov.get('actions')}")


async def y_review(conn):
    section("[Y] Reviewed / dismissed — a person's decision, recorded with the user")
    buf = (await live_checks(conn, ORG_A, "BUF"))[0]
    cv, ov = await api(U_STAFF_V, "POST", f"{_C}/{buf['id']}/review", {"outcome": "reviewed"})
    check(cv == 403 and "manage_replication" in json.dumps(ov), "view-only staff cannot review (403 naming "
          "manage_replication)", f"{cv}")
    c, o = await api(U_ADMIN, "POST", f"{_C}/{buf['id']}/review", {"outcome": "reviewed", "note": "looked at it"})
    live = await platform(conn, "SELECT * FROM public.note_checks WHERE id = $1", buf["id"], fetch="fetchrow")
    arch = await platform(conn, "SELECT status FROM public.note_checks WHERE archived_from_id = $1 ORDER BY "
                                "system_to", buf["id"])
    task = await platform(conn, "SELECT status, approved_by FROM workflow_run_steps WHERE id = $1",
                          buf["alert_task_id"], fetch="fetchrow")
    check(c == 200 and live["status"] == "reviewed" and live["reviewed_by"] == U_ADMIN and live["reviewed_at"] is not None
          and bool(arch) and all(a["status"] == "new" for a in arch) and task["status"] == "completed"
          and task["approved_by"] == U_ADMIN,
          "reviewed through the open alert task: the check keeps its id, every prior state is archived (status "
          "'new'), the task is completed by the reviewer", f"{c} {live['status']} {[a['status'] for a in arch]} "
                                                           f"{dict(task)}"),
    c2, _ = await api(U_ADMIN, "POST", f"{_C}/{buf['id']}/review", {"outcome": "dismissed", "note": "x"})
    check(c2 == 409, "a decided check is not decided again (409)", f"{c2}")
    wo = (await live_checks(conn, ORG_A, "WO"))[0]
    cn, _ = await api(U_ADMIN, "POST", f"{_C}/{wo['id']}/review", {"outcome": "dismissed"})
    cd, od = await api(U_ADMIN, "POST", f"{_C}/{wo['id']}/review", {"outcome": "dismissed", "note": "not our issuer"})
    w2 = await platform(conn, "SELECT * FROM public.note_checks WHERE id = $1", wo["id"], fetch="fetchrow")
    check(cn == 422 and cd == 200 and w2["status"] == "dismissed" and w2["reviewed_by"] == U_ADMIN
          and w2["review_note"] == "not our issuer",
          "dismissing needs a note (422 without); with one it is recorded with the user and the note", f"{cn} {cd}")
    rst = (await live_checks(conn, ORG_A, "RST"))[0]
    cr, _ = await api(U_ADMIN, "POST", f"{_C}/{rst['id']}/review", {"outcome": "reviewed"})
    r2 = await platform(conn, "SELECT status, reviewed_by FROM public.note_checks WHERE id = $1", rst["id"],
                        fetch="fetchrow")
    check(cr == 200 and r2["status"] == "reviewed" and r2["reviewed_by"] == U_ADMIN and rst["alert_task_id"] is None,
          "a check with no alert task is reviewed directly, recorded with the user", f"{cr} {dict(r2)}")
    aud = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action LIKE "
                                   "'note_checks.check_%' AND user_id = $2", ORG_A, U_ADMIN, fetch="fetchval"))
    check(aud >= 3, "every decision is audited with the user", f"{aud}")
    c3, o3 = await api(U_ADMIN, "GET", f"{_C}?status=reviewed")
    got = {r["global_security_id"] for r in o3.get("rows") or []}
    check(c3 == 200 and got == {str(S["note"]["BUF"]), str(S["note"]["RST"])}, "status filter: exactly the reviewed",
          f"{got}")


async def y_gates(conn):
    section("[Y] Member none; permission gates both ways (identical requests); org isolation")
    codes = {}
    for uid in (U_MEMBER, U_ADVISOR, U_STAFF_V, U_ADMIN, U_SUPER):
        c, o = await api(uid, "GET", _C)
        codes[USERS[uid][2]] = (c, json.dumps(o.get("detail")) if c != 200 else len(o.get("rows") or []))
    check(codes["member"][0] == 403 and "Staff access required" in str(codes["member"][1])
          and codes["advisor"][0] == 403 and "view_replication" in str(codes["advisor"][1])
          and codes["staffv"][0] == 200 and codes["admin"][0] == 200 and codes["super"][0] == 200
          and codes["admin"][1] > 0,
          "GET New issues: a client member (even holding view_replication) 403; an internal adviser without "
          "view_replication 403 naming it; staff with it, the org admin and Super Admin 200", f"{codes}")
    mt = int(await platform(conn, "SELECT count(*) FROM member_todos WHERE user_id = $1 AND status = 'open'", U_MEMBER,
                            fetch="fetchval"))
    wd = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                  "'note_checks.alert_withheld' AND payload::text LIKE $2", ORG_A, f"%{U_MEMBER}%",
                            fetch="fetchval"))
    check(mt == 0, "the client member holds no open to-do (no alert reached them)", f"{mt} open; {wd} withheld")
    for uid, path in ((U_MEMBER, f"{_C}/settings"), (U_ADMIN, _U)):
        c, _ = await api(uid, "GET", path)
        check(c == 403, f"{USERS[uid][2]} refused {path.split('/api/v1')[1]}", f"{c}")
    # isolation (rolbypassrls = false asserted in the schema section)
    a_own = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.note_checks WHERE org_id = $1", ORG_A,
                             fetch="fetchval"))
    a_b = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.note_checks WHERE org_id = $1", ORG_B,
                           fetch="fetchval"))
    b_own = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.note_checks WHERE org_id = $1", ORG_B,
                             fetch="fetchval"))
    b_a = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.note_checks WHERE org_id = $1", ORG_A,
                           fetch="fetchval"))
    s_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.note_check_settings WHERE org_id = $1", ORG_A,
                           fetch="fetchval"))
    check(a_own > 0 and b_own > 0 and a_b == 0 and b_a == 0 and s_b == 0,
          "RLS both directions: each org sees its own checks (non-empty) and none of the other's; org B sees none of "
          "org A's settings", f"{a_own} {a_b} {b_own} {b_a} {s_b}")
    upd = await scoped(conn, ORG_B, "UPDATE public.note_checks SET review_note = 'x' WHERE org_id = $1 RETURNING id",
                       ORG_A)
    check(upd == [], "org B cannot touch org A's checks (zero rows)")
    a_check = (await live_checks(conn, ORG_A, "BUF2"))[0]["id"]
    c1, _ = await api(U_ADMIN_B, "GET", f"{_C}/{a_check}")
    c2, _ = await api(U_ADMIN_B, "POST", f"{_C}/{a_check}/review", {"outcome": "reviewed"})
    cb, ob = await api(U_ADMIN_B, "GET", _C)
    b_ids = {r["id"] for r in ob.get("rows") or []}
    b_db = {str(r["id"]) for r in await scoped(conn, ORG_B, "SELECT id FROM public.note_checks WHERE org_id = $1 AND "
                                                            "valid_to IS NULL AND system_to IS NULL", ORG_B)}
    check(c1 == 404 and c2 == 404 and cb == 200 and bool(b_ids) and b_ids == b_db,
          "org B's admin: org A's check is a 404 (read and review); its own feed is exactly its own live checks",
          f"{c1} {c2} {cb} {len(b_ids)}/{len(b_db)}")
    real = int(await platform(conn, "SELECT count(*) FROM public.note_checks WHERE NOT (org_id = ANY($1::uuid[])) AND "
                                    "global_security_id = ANY($2::uuid[])", FIXTURE_ORGS,
                              [S["note"][k] for k in NOTES], fetch="fetchval"))
    check(real == 0, "no check of a fixture note was written into a real org", f"{real}")


async def y_real_rows(conn):
    section("[Y] Real notes, terms, readings, filings, underlyings, replicas and users untouched (count + checksum)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after[k] == v, f"real {k} unchanged (count + checksum)", f"{v[0]} → {after[k][0]}")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])
    data = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
    rows = data.get("routes", []) if isinstance(data, dict) else data
    have = {(m, r.get("path")): r.get("after") for r in rows for m in (r.get("methods") or [])}
    miss = [(k, g) for k, g in ROUTES.items() if g not in str(have.get(k))]
    check(not miss, "each of rep12's routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^\s*ok \d+ - (?:new issues|underlying queue|lib: multi-confirm)", out, re.M)
    check(len(mine) >= 5 and "not ok" not in out, "the rep12 render tests ran and passed (no control without the "
          "server's flag and action; the estimate exactly as the server states it)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep11.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep11.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, None if code == 124 else code)  # its OWN final ^TOTAL line + exit code
    total = [(str(nt.passed), str(nt.failed))] if nt.line else []
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(nt.ok and not fails and code == 0, "verify_rep11.py: 0 FAIL",
          f"{nt.detail()}; {fails[:3]} {err[-200:] if not total else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown_retrying()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    seconds = _time.monotonic() - t0
    left = await leftovers(conn)
    print(f"CLEANUP_SECONDS: {seconds:.1f}")
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await close_conn(conn)
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
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
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("settings", y_settings, conn)
            await run("hook", y_hook, conn)
            if len(S.get("terms", {})) == len(NOTES):
                await run("restatement", y_restatement, conn)
                await run("sweep", y_sweep_catches, conn)
                await run("quotes", y_quotes, conn)
                await run("queue", y_queue, conn)
                if S.get("queue_ok") and S.get("quotes_ok"):
                    await run("drafts", y_drafts, conn)
                    if S.get("rep_buf3") and S.get("cost") is not None:
                        await run("alerts", y_alerts, conn)
                    else:
                        check(False, "alerts need the priced draft and BUF3's draft", "an earlier section failed")
                    await run("feed", y_feed, conn)
                    await run("review", y_review, conn)
                else:
                    check(False, "drafts, alerts, feed and review need the confirmed queue and the quotes",
                          "an earlier section failed")
                await run("gates + isolation", y_gates, conn)
            else:
                check(False, "every fixture note was extracted", "the hook section failed")
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (platform fixture filings, notes, terms, verdicts, edges, "
                  "contracts and the STP grant included)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    if t == "public.note_checks":
                        # the live sweep (once rep12 is deployed) may add checks of REAL notes in REAL orgs: its own
                        # output, never a fixture — fixture checks are proven gone by the leftover count above
                        check(post[t] >= pre[t], f"{t}: no real check deleted", f"{pre[t]} → {post[t]}")
                        continue
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after[k] == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
