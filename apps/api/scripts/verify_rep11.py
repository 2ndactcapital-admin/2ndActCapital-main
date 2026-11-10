"""verify_rep11 — Replication Studio, sprint 11 (REP11, phase 3 starts): note
replication from the 424B2 terms — the platform replicability verdict per note,
replicas built from rep8's options machinery (plus a Treasury leg where the note
promises principal), the at-expiry payoff overlay, pricing when quotes exist
(the embedded-fee ESTIMATE), the X-ray marker and member visibility under
rep10's hypothetical-performance policy.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep11.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the httpx transport records and refuses — any attempt is a failed
check. NO model call: rep11 only reads terms already extracted. REAL: the app
through an ASGI transport (middleware, Principal, every gate), the workflow
engine (human tasks), rep8's contracts and quotes, rep10's policy, RLS and the
live database. The script's own connection is app_service (rolbypassrls =
false, asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-0000001b0001 / 0002, is_test =
true, asserted); users created explicitly; PLATFORM fixture notes, indices and
a Treasury with the reserved identifier prefix ZZR11 and the name prefix
'REP11 VERIFY' (note terms with NULL filing, underlying edges, option contracts
the builds define) — all checked absent from real rows first and removed BY ID
at teardown. Real note terms, readings, filings, relationships, positions and
users are compared by count + checksum before and after.

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
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep11"
BEFORE_REF = "22b4c0d"   # main @ 'merge previewperf' — the rep11-work base
_R = "/api/v1/replication"
_N = f"{_R}/notes"
ROUTES = {
    ("GET", _N): "view_replication",
    ("GET", f"{_N}/{{note_id}}"): "view_replication",
    ("GET", f"{_N}/replicas/{{replica_id}}"): "view_replication",
    ("POST", f"{_N}/verdicts/run"): "manage_replication",
    ("POST", f"{_N}/{{note_id}}/replicas"): "manage_replication",
    ("POST", f"{_N}/replicas/{{replica_id}}/price"): "manage_replication",
    ("POST", f"{_N}/replicas/{{replica_id}}/submit"): "manage_replication",
    ("POST", f"{_N}/replicas/{{replica_id}}/archive"): "manage_replication",
    ("POST", f"{_N}/replicas/{{replica_id}}/approve"): "approve_replication",
    ("POST", f"{_N}/replicas/{{replica_id}}/reject"): "approve_replication",
}


def self_routes(check, find):
    """rep11's own routes: BEFORE_REF → its own merge (or HEAD while unmerged); each exists today with that gate.
    Never "every route added since a base commit"."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "rep11 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate at rep11's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "rep11's routes all still exist today with the same gate", f"{drift[:4]}")


def self_checks(check, find):
    self_routes(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import note_replication as N  # noqa: E402
from services import options_sleeve as OS  # noqa: E402
from services import rbac  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "rep11verify"
PREFIX = "ZZR11"
NAME_PREFIX = "REP11 VERIFY"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
HH_NAME = "REP11 Verify Household"
ZERO, ONE, HUNDRED = Decimal(0), Decimal(1), Decimal(100)
BP = Decimal("0.0001")

# Fixture notes — one per archetype / basket type / feature. The terms columns each sets (the rest NULL / default).
NOTE_TERMS = {
    "BUF": {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
            "protection_pct": "0.2", "cap_pct": "0.25", "participation_rate": "1"},
    "PPN": {"product_archetype": "principal_protected", "basket_type": "single", "protection_type": "floor",
            "protection_pct": "1", "cap_pct": "0.2", "participation_rate": "0.5"},
    "DIG": {"product_archetype": "digital", "basket_type": "single", "protection_type": "buffer",
            "protection_pct": "0.25", "cap_pct": "0.08"},
    "RC": {"product_archetype": "reverse_convertible", "basket_type": "single", "protection_type": "none",
           "coupon_rate": "0.1"},
    "WO": {"product_archetype": "buffered_note", "basket_type": "worst_of", "protection_type": "buffer",
           "protection_pct": "0.2", "cap_pct": "0.3", "participation_rate": "1"},
    "AUTO": {"product_archetype": "autocallable", "basket_type": "single", "protection_type": "buffer",
             "protection_pct": "0.2", "coupon_rate": "0.08", "coupon_barrier_pct": "0.7",
             "autocall_barrier_pct": "1", "autocall_frequency": "quarterly"},
    "BSK": {"product_archetype": "digital", "basket_type": "basket", "protection_type": "none", "cap_pct": "0.1"},
    "DEC": {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
            "protection_pct": "0.1", "cap_pct": "0.2", "participation_rate": "1", "is_decrement_index": True},
    "OTH": {"product_archetype": "other", "basket_type": "single", "protection_type": "none"},
    "PRE": {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
            "protection_pct": "0.15", "cap_pct": "0.3", "participation_rate": "1", "terms_status": "preliminary"},
    "REV": {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
            "protection_pct": "0.2", "cap_pct": "0.25", "participation_rate": "1",
            "extraction_confidence": "needs_review"},
    "UNR": {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
            "protection_pct": "0.2", "cap_pct": "0.25", "participation_rate": "1"},
    "TR": {"product_archetype": "buffered_note", "basket_type": "single", "protection_type": "buffer",
           "protection_pct": "0.2", "cap_pct": "0.25", "participation_rate": "1", "return_basis": "total_return"},
}
# key: (verdict, mapping, the reason codes, preliminary, needs review)
EXPECT = {
    "BUF": ("static", "buffer_package", {"buffer_package"}, False, False),
    "PPN": ("static", "principal_protected", {"principal_protected"}, False, False),
    "DIG": ("approximate", "digital_call_spread", {"digital"}, False, False),
    "RC": ("static", "reverse_convertible", {"reverse_convertible"}, False, False),
    "WO": ("not_replicable", None, {"worst_of"}, False, False),
    "AUTO": ("not_replicable", None, {"autocall", "contingent_coupon"}, False, False),
    "BSK": ("not_replicable", None, {"basket"}, False, False),
    "DEC": ("not_replicable", None, {"decrement_index"}, False, False),
    "OTH": ("not_replicable", None, {"no_mapping"}, False, False),
    "PRE": ("static", "buffer_package", {"buffer_package"}, True, False),
    "REV": ("static", "buffer_package", {"buffer_package"}, False, True),
    "UNR": ("static", "buffer_package", {"buffer_package"}, False, False),
    "TR": ("approximate", "buffer_package", {"buffer_package", "total_return"}, False, False),
}
INDEX_KEYS = ("IDX", "IDX2", "IDX3")
SEC_NAMES = {**{k: f"{NAME_PREFIX} NOTE {k}" for k in NOTE_TERMS}, **{k: f"{NAME_PREFIX} {k}" for k in INDEX_KEYS},
             "TSY": f"{NAME_PREFIX} TSY"}
SEC_TYPES = {**{k: "structured_note" for k in NOTE_TERMS}, **{k: "index" for k in INDEX_KEYS}, "TSY": "fixed_income"}
TENOR_DAYS = 730


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001b{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_ADMIN1, U_ADMIN2, U_VIEWER, U_MEMBER, U_ADMIN_B = (_id(110 + i) for i in range(5))
USERS = {
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP11 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP11 Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep11_viewer", "REP11 Viewer"),
    U_MEMBER: (ORG_A, "member", "member", "rep11_member", "REP11 Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP11 Org B Admin"),
}
ROLE_PERMS = {"rep11_viewer": ("view_replication",), "rep11_member": ("view_replication",)}
ENT_A, ENT_B = _id(201), _id(202)
HH_A = _id(250)
A_M = _id(301)
ACCT_RAW = "ZZ11M0000001"
POS_QTY, POS_MV = Decimal("10"), Decimal("9725")

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.delegate_grants", "portfolio.assets", "portfolio.positions", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "portfolio.securities_global_note_terms",
    "portfolio.securities_global_relationships", "portfolio.option_contracts", "portfolio.adviser_quotes",
    "public.hypothetical_performance_policies", "portfolio.note_replicability", "public.note_replicas",
    "public.note_replica_legs", "public.note_replica_comparisons",
)
NEW_TABLES = ("portfolio.note_replicability", "public.note_replicas", "public.note_replica_legs",
              "public.note_replica_comparisons")
SEED_CHECK_LITERALS = (
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_archetype_chk",
     ("buffered_note", "autocallable", "reverse_convertible", "digital", "principal_protected", "other")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_basket_chk", ("single", "basket", "worst_of")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_protection_chk", ("buffer", "floor", "none")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_status_chk", ("preliminary", "final")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_confidence_chk", ("high", "needs_review")),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_autocall_freq_chk", ("quarterly",)),
    ("portfolio.securities_global_note_terms", "sec_global_note_terms_return_basis_chk", ("price", "total_return")),
    ("portfolio.securities_global_relationships", "sec_global_rel_state_chk", ("resolved", "unresolved")),
    ("portfolio.securities_global_relationships", "sec_global_rel_type_chk", ("underlying_of",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("structured_note", "index", "fixed_income",
                                                                   "option")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.note_replicability", "note_replicability_verdict_chk", ("static", "approximate", "not_replicable")),
    ("public.note_replicas", "note_replicas_status_chk", ("draft", "pending_approval", "approved", "archived")),
    ("public.note_replicas", "note_replicas_mapping_chk", ("buffer_package", "principal_protected",
                                                           "digital_call_spread", "reverse_convertible")),
    ("public.note_replica_legs", "note_replica_legs_kind_chk", ("underlying", "option", "treasury")),
    ("public.note_replica_comparisons", "note_replica_comparisons_status_chk", ("priced", "not_priced")),
    ("portfolio.adviser_quotes", "adviser_quotes_kind_chk", ("premium", "index_level", "note_price",
                                                             "treasury_yield", "implied_vol")),
    ("public.hypothetical_performance_policies", "hypothetical_policies_audience_chk", ("staff_and_members",)),
)

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


def num(v) -> Decimal | None:
    """A seed literal for a numeric column (asyncpg wants a Decimal, never a str)."""
    return None if v is None else Decimal(str(v))


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def sec(key) -> str:
    return str(S[f"sec_{key}"])


# ═══ The verify's OWN statement of the rules (independent of the service) ═══
def stated(key: str, r: Decimal) -> Decimal:
    """The note's at-expiry return for an underlying return r, written from the fixture terms directly."""
    t = NOTE_TERMS[key]
    arch, prot = t["product_archetype"], t["protection_type"]
    pp = D(t["protection_pct"]) if t.get("protection_pct") else None

    def down(x):
        if prot == "buffer":
            return ZERO if x >= -pp else x + pp
        if prot == "floor":
            return max(x, pp - ONE)
        return x
    if arch == "digital":
        return D(t["cap_pct"]) if r >= 0 else down(r)
    if arch == "reverse_convertible":
        return S["rc_coupon"] + (ZERO if r >= 0 else down(r))
    p = D(t.get("participation_rate") or "1")
    if r >= 0:
        up = p * r
        return min(up, D(t["cap_pct"])) if t.get("cap_pct") else up
    return down(r)


def value_of(legs: list[dict], s: Decimal) -> Decimal:
    """The replica's value per unit of principal at expiry from the API's legs (strike_pct, ratio, face)."""
    v = ZERO
    for x in legs:
        ratio = D(x["ratio"])
        if x["kind"] == "underlying":
            v += ratio * s
        elif x["kind"] == "treasury":
            v += ratio * D(x["face_pct"])
        else:
            k = D(x["strike_pct"])
            iv = max(s - k, ZERO) if x["option_type"] == "call" else max(k - s, ZERO)
            v += (ONE if x["direction"] == "long" else -ONE) * ratio * iv
    return v


def discount(face: Decimal, y_pct: Decimal, days: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 28
        return face * (ONE + y_pct / HUNDRED) ** (-Decimal(days) / Decimal(365))


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
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def fixture_note_ids(conn) -> list:
    return [r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                              f"{NAME_PREFIX}%")]


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch: every real note, its terms, readings, filings, links, positions, users.
    note_term_readings and reference_filings are keyed by their real primary keys (read from the catalog)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    fx = [str(x) for x in await fixture_note_ids(conn)]
    out = {}
    for name, table in (("note_term_readings", "portfolio.note_term_readings"),
                        ("reference_filings", "portfolio.reference_filings")):
        pk = await platform(conn, """SELECT string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_index i
                                     JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                                     WHERE i.indrelid = $1::regclass AND i.indisprimary""", table, fetch="fetchval")
        cols = "id, cik, accession_number, content_hash, extraction_status, updated_at" if name == "reference_filings" \
            else "id, reference_filing_id, note_terms_id, field_key, value, status, created_at"
        out[name] = await fingerprint(conn, f"SELECT {pk} AS k, {cols} FROM {table}")
    out["note_terms"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global_note_terms WHERE NOT "
                                                "(global_security_id = ANY($1::uuid[]))", fx)
    out["relationships"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global_relationships WHERE NOT "
                                                   "(from_global_security_id = ANY($1::uuid[]))", fx)
    out["structured_notes"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global WHERE security_type = "
                                                      "'structured_note' AND NOT (id = ANY($1::uuid[]))", fx)
    out["positions"] = await fingerprint(conn, "SELECT * FROM portfolio.positions WHERE NOT (org_id = ANY($1::uuid[]))",
                                         orgs)
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
    for t in ("public.note_replicas", "public.note_replica_legs", "public.note_replica_comparisons",
              "portfolio.adviser_quotes", "portfolio.option_packages"):
        out[t.split(".")[1]] = await fingerprint(conn, f"SELECT * FROM {t} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
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


async def fixture_security_ids(conn) -> tuple[list, list]:
    """(the fixture securities, the option-contract securities the builds defined on the fixture indices)."""
    ids = {r["global_security_id"] for r in await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")}
    ids |= set(await fixture_note_ids(conn))
    opts = {r["global_security_id"] for r in await conn.fetch(
        "SELECT global_security_id FROM portfolio.option_contracts WHERE underlying_global_security_id = "
        "ANY($1::uuid[])", list(ids))}
    return list(ids), list(opts)


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs, opts = await fixture_security_ids(op)
        allsecs = secs + opts
        orgs, users = FIXTURE_ORGS, list(USERS)
        by_org = "org_id = ANY($1::uuid[])"
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan = {t: (by_org, [orgs]) for t in (
            "public.note_replica_comparisons", "public.note_replica_legs", "public.note_replicas",
            "portfolio.adviser_quotes", "public.hypothetical_performance_policies",
            "public.domain_event_deliveries", "public.domain_events", "public.workflow_run_steps",
            "public.agent_proposals", "public.workflow_runs", "public.workflow_steps", "public.workflow_versions",
            "public.workflow_definitions", "portfolio.positions", "portfolio.assets", "public.account_owners",
            "public.accounts", "public.delegate_grants", "public.household_memberships", "public.households",
            "public.entities", "public.org_settings")}
        plan["public.member_todos"] = org_or_user
        plan["public.audit_log"] = org_or_user
        plan["portfolio.note_replicability"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global_note_terms"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global_relationships"] = (
            "from_global_security_id = ANY($1::uuid[]) OR to_global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.option_contracts"] = ("underlying_global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [allsecs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [allsecs])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        cols = {(r["t"]) for r in await op.fetch(
            "SELECT table_schema || '.' || table_name AS t FROM information_schema.columns WHERE column_name = 'org_id'")}
        plan = {t: v for t, v in plan.items() if t in exists and (v[0] != by_org or t in cols)}
        if "public.household_memberships" in exists and "public.household_memberships" not in cols:
            plan["public.household_memberships"] = ("household_id = ANY($1::uuid[])", [[HH_A]])
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               label="verify_rep11")
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
    fx = await fixture_note_ids(conn)
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global_note_terms WHERE global_security_id = ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM portfolio.note_replicability WHERE global_security_id = ANY($1::uuid[])", [fx]),
        ("SELECT count(*) FROM portfolio.securities_global_relationships WHERE from_global_security_id = "
         "ANY($1::uuid[])", [fx]),
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
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep11"}}
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
        raise AssertionError(f"verify_rep11: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def quote(uid, key_or_id, kind, value, as_of: datetime, source="REP11 verify desk") -> dict:
    gsid = sec(key_or_id) if key_or_id in SEC_NAMES else str(key_or_id)
    c, o = await api(uid, "POST", f"{_R}/options/quotes", {"global_security_id": gsid, "kind": kind,
                                                           "value": str(value), "source": source,
                                                           "as_of": as_of.isoformat()})
    if c != 201:
        raise AssertionError(f"quote {kind} on {key_or_id}: {c} {str(o)[:200]}")
    return o.get("quote") or o


def at(d: date, hour: int = 15) -> datetime:
    return datetime.combine(d, time(hour, 0), tzinfo=timezone.utc)


async def replica_counts(conn) -> tuple:
    return (int(await platform(conn, "SELECT count(*) FROM public.note_replicas WHERE org_id = ANY($1::uuid[])",
                               FIXTURE_ORGS, fetch="fetchval")),
            int(await platform(conn, "SELECT count(*) FROM public.note_replica_legs WHERE org_id = ANY($1::uuid[])",
                               FIXTURE_ORGS, fetch="fetchval")),
            int(await platform(conn, "SELECT count(*) FROM portfolio.option_contracts WHERE "
                                     "underlying_global_security_id = ANY($1::uuid[])",
                               [S[f"sec_{k}"] for k in INDEX_KEYS], fetch="fetchval")))


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e (verdict counts over today's notes)")
    live = await platform(conn, "SELECT terms_status, extraction_confidence, routing_decision FROM "
                                "portfolio.securities_global_note_terms WHERE valid_to IS NULL AND system_to IS NULL "
                                "AND NOT (global_security_id = ANY($1::uuid[]))",
                          [str(x) for x in await fixture_note_ids(conn)])
    find("1a current terms", f"{len(live)} live real note-terms rows; 'the' terms per note = restated > final > "
         "preliminary, then the newest valid_from (N.current_of). routing_decision is NULL on "
         f"{sum(1 for r in live if r['routing_decision'] is None)} — STP routing never ran on them, so 'approved' is "
         "read from extraction_confidence = 'high' and field_status (no 'extraction_failed' on a used field); "
         f"high {sum(1 for r in live if r['extraction_confidence'] == 'high')}, final "
         f"{sum(1 for r in live if r['terms_status'] == 'final')}, preliminary "
         f"{sum(1 for r in live if r['terms_status'] == 'preliminary')}")
    rel = await platform(conn, "SELECT link_state, count(*) AS n FROM portfolio.securities_global_relationships WHERE "
                               "relationship_type = 'underlying_of' AND valid_to IS NULL AND system_to IS NULL AND NOT "
                               "(from_global_security_id = ANY($1::uuid[])) GROUP BY 1",
                         [str(x) for x in await fixture_note_ids(conn)])
    find("1a underlyings", f"underlying edges by state: {dict((r['link_state'], r['n']) for r in rel)} — building "
         "needs exactly one RESOLVED edge (a Super Admin confirms it); distribution agent and fees live only in "
         "note_term_readings (distribution, agent_commission_pct, total_fees_pct, estimated_value_pct) of the same "
         "filing, shown when read")
    find("1b mapping", "buffered_note single → buffer_package (static); principal_protected → Treasury zero + call "
         "spread (static); digital single → Treasury + call spread of width ε (approximate, exact outside [0, ε)); "
         "reverse_convertible single → Treasury (principal + coupon) + short put (static); autocall, worst-of, basket, "
         "decrement index, contingent / memory coupons, daily barrier, 'other' → not replicable, a reason per feature")
    pool = await get_pool()
    with _ctx(None, True):
        async with pool.acquire() as c2:
            rows = [t for t in await N.live_terms(c2) if not t["security_name"].startswith(NAME_PREFIX)]
            unds = await N.underlyings_of(c2, {r["global_security_id"] for r in rows})
            reads = await N.readings_of(c2, {r["reference_filing_id"] for r in rows})
    tally: dict = {}
    for t in rows:
        a = N.assess(t, n_underlyings=len(unds.get(str(t["global_security_id"]), [])),
                     readings=reads.get(str(t["reference_filing_id"])))
        tally[a["verdict"]] = tally.get(a["verdict"], 0) + 1
    find("1b verdict counts over today's real notes", f"{tally} of {len(rows)}")
    find("1c builder inputs", "strike % of the start level, ratio, expiry = final valuation date, outcome period = "
         "initial → final valuation date, the underlying from the resolved edge; the start level = the underlying's "
         "level quote ON the initial valuation date (never guessed)")
    n_pos = await platform(conn, "SELECT count(*) FROM portfolio.positions p JOIN portfolio.assets a ON a.id = "
                                 "p.asset_id JOIN portfolio.securities_global g ON g.id = a.global_security_id WHERE "
                                 "g.security_type = 'structured_note' AND p.valid_to IS NULL AND p.system_to IS NULL "
                                 "AND NOT (p.org_id = ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval")
    find("1d holdings", f"{n_pos} live real positions in a structured note (positions → assets.global_security_id → "
         "securities_global → note terms)")
    find("1e parent", "option_packages needs replica_security_id and model_id (NOT NULL) — a note replica is its own "
         "parent (public.note_replicas + note_replica_legs), reusing rep8's contracts, quotes and payoff math")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep11.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep11.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    for t in NEW_TABLES:
        sch, name = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", sch, name)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")


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
                                                       "ANY($1::text[])", ["sec_global_note_terms_current_unique",
                                                                           "note_replicability_live_uq",
                                                                           "note_replicas_open_uq",
                                                                           "note_replicas_approved_uq"])}
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                                   "ANY($1::regclass[])", list(NEW_TABLES), fetch="fetchval"))
    check(not bad and len(uq) == 4 and fks >= 15,
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints); the UNIQUE indexes "
          "and FKs the seeds rely on are in the catalog", "; ".join(bad) + f" uq={sorted(uq)} fks={fks}")
    S["literals_ok"] = not bad and len(uq) == 4


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM households WHERE name = $1", HH_NAME, fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(o) == 0 and int(h) == 0,
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', the fixture orgs, the household) match no real row",
                 f"{n}/{k}/{o}/{h}")


async def seed(conn) -> None:
    S["seed_day"] = today()
    S["initial"] = S["seed_day"] - timedelta(days=30)
    S["final"] = S["initial"] + timedelta(days=TENOR_DAYS)
    tenor = (Decimal(TENOR_DAYS) / Decimal(365)).quantize(Decimal("0.0001"))
    S["rc_coupon"] = D(NOTE_TERMS["RC"]["coupon_rate"]) * tenor
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true), "
                           "set_config('app.underlying_confirm', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP11 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for rname, perms in ROLE_PERMS.items():
            rid = await rbac.ensure_role(conn, ORG_A, rname, f"REP11 verify — {rname}")
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
            else:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for i, key in enumerate(SEC_NAMES):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES[key], currency_code="USD",
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}C{i:03d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        S["terms"] = {}
        for key, t in NOTE_TERMS.items():
            tid = uuid.uuid4()
            cols = {"product_archetype": None, "basket_type": None, "protection_type": None, "protection_pct": None,
                    "cap_pct": None, "participation_rate": None, "coupon_rate": None, "coupon_barrier_pct": None,
                    "autocall_barrier_pct": None, "autocall_frequency": "none", "is_decrement_index": False,
                    "return_basis": "price", "terms_status": "final", "extraction_confidence": "high"}
            cols.update(t)
            await conn.execute(
                """INSERT INTO portfolio.securities_global_note_terms (id, global_security_id, reference_filing_id,
                       terms_status, product_archetype, protection_type, basket_type, return_basis, is_decrement_index,
                       notional_currency, protection_pct, cap_pct, participation_rate, coupon_rate, coupon_barrier_pct,
                       autocall_barrier_pct, autocall_frequency, initial_valuation_date, final_valuation_date,
                       tenor_years, field_status, extraction_confidence)
                   VALUES ($1, $2, NULL, $3, $4, $5, $6, $7, $8, 'USD', $9::numeric, $10::numeric, $11::numeric,
                           $12::numeric, $13::numeric, $14::numeric, $15, $16, $17, $18, '{}'::jsonb, $19)""",
                tid, S[f"sec_{key}"], cols["terms_status"], cols["product_archetype"], cols["protection_type"],
                cols["basket_type"], cols["return_basis"], cols["is_decrement_index"], num(cols["protection_pct"]),
                num(cols["cap_pct"]), num(cols["participation_rate"]), num(cols["coupon_rate"]),
                num(cols["coupon_barrier_pct"]), num(cols["autocall_barrier_pct"]), cols["autocall_frequency"],
                S["initial"], S["final"], tenor,
                cols["extraction_confidence"])
            S["terms"][key] = tid
            targets = ["IDX", "IDX2", "IDX3"] if key == "WO" else ([] if key == "UNR" else ["IDX"])
            for tk in targets:
                await conn.execute(
                    """INSERT INTO portfolio.securities_global_relationships (id, from_global_security_id,
                           to_global_security_id, raw_underlying_text, link_state, relationship_type, resolved_by,
                           resolved_at) VALUES ($1, $2, $3, $4, 'resolved', 'underlying_of', $5, now())""",
                    uuid.uuid4(), S[f"sec_{key}"], S[f"sec_{tk}"], SEC_NAMES[tk], U_ADMIN1)
            if key == "UNR":
                await conn.execute(
                    """INSERT INTO portfolio.securities_global_relationships (id, from_global_security_id,
                           raw_underlying_text, link_state, relationship_type) VALUES ($1, $2, $3, 'unresolved',
                           'underlying_of')""", uuid.uuid4(), S["sec_UNR"], f"{NAME_PREFIX} unresolved index")
        # ORG_A: an entity, a household the member may see, a managed account holding the BUF note
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, 'individual', "
                           "$3)", ENT_A, ORG_A, "REP11 Verify Member Alice")
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, 'individual', "
                           "$3)", ENT_B, ORG_B, "REP11 Verify Org B")
        await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)", HH_A, ORG_A,
                           HH_NAME, U_ADMIN1)
        await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, $3)",
                           HH_A, ENT_A, U_ADMIN1)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from, granted_by) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day', $4)",
                           ORG_A, ENT_A, U_MEMBER, U_ADMIN1)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        salt = await get_or_create_salt(conn, ORG_A)
        n = AccountNumber(ACCT_RAW)
        await conn.execute(
            "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
            "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
            "VALUES ($1, $2, $3, $4, 'ALTRUIST', 'individual', 'taxable', $5, $6, true, false)",
            A_M, ORG_A, n.masked, n.hashed(salt), ENT_A, HH_A)
        asset = uuid.uuid4()
        await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) VALUES "
                           "($1, $2, $3, $4, 'structured_note')", asset, ORG_A, S["sec_BUF"], SEC_NAMES["BUF"])
        await conn.execute(
            "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
            "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
            "'manual', $8)", uuid.uuid4(), ORG_A, ENT_A, asset, S["seed_day"] - timedelta(days=1), POS_QTY, POS_MV, A_M)
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(len(orgs) == 2 and all(r["is_test"] is True for r in orgs), "every fixture org is is_test = true (the live "
          "scheduler skips them)", f"{[dict(r) for r in orgs]}")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                        fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly, is_test = true", f"{us}")
    S["seeded"] = True


async def verdicts_of(conn) -> dict:
    rows = await platform(conn, "SELECT * FROM portfolio.note_replicability WHERE note_terms_id = ANY($1::uuid[]) "
                                "AND valid_to IS NULL AND system_to IS NULL", list(S["terms"].values()))
    by_terms = {r["note_terms_id"]: dict(r) for r in rows}
    return {k: by_terms.get(t) for k, t in S["terms"].items()}


async def y_verdicts(conn):
    section("[Y] Verdicts per archetype / feature with reasons; preliminary and needs-review; idempotent recompute")
    pool = await get_pool()
    with _ctx(None, True):
        first = await N.run_verdicts(pool)
    v = await verdicts_of(conn)
    bad = []
    for k, (verdict, mapping, codes, prelim, review) in EXPECT.items():
        row = v.get(k)
        got = None if row is None else (row["verdict"], row["mapping"], {x["code"] for x in json.loads(row["reasons"])},
                                        row["is_preliminary"], row["needs_review"])
        if got != (verdict, mapping, codes, prelim, review):
            bad.append(f"{k}: {got}")
    check(not bad, f"each of the {len(EXPECT)} fixture notes gets the expected verdict, mapping, reason codes and "
          "flags", "; ".join(bad))
    wo = json.loads(v["WO"]["reasons"])[0]["text"] if v.get("WO") else ""
    check("Worst-of three underlyings" in wo and "correlation" in wo and "listed options cannot provide" in wo,
          "the worst-of reason reads plainly (three underlyings, correlation, listed options)", wo)
    au = " ".join(x["text"] for x in json.loads(v["AUTO"]["reasons"])) if v.get("AUTO") else ""
    check("autocall" in au.lower() and "quarterly" in au and "contingent coupons" in au.lower()
          and "70%" in au, "the autocallable lists BOTH path features — the autocall (quarterly) and the contingent "
          "coupon at 70%", au[:240])
    check(bool(v.get("PRE")) and v["PRE"]["is_preliminary"] is True and v["BUF"]["is_preliminary"] is False,
          "preliminary terms are marked (and final ones are not)")
    rr = json.loads(v["REV"]["review_reasons"]) if v.get("REV") else []
    check(bool(rr) and rr[0]["code"] == "confidence" and v["REV"]["needs_review"] is True,
          "needs-review terms are marked with the reason (extraction confidence)", f"{rr}")
    check(first["computed"] >= len(EXPECT), "the first run computed every fixture verdict",
          f"{ {k: first[k] for k in ('computed', 'unchanged', 'closed')} }")
    with _ctx(None, True):
        again = await N.run_verdicts(pool, terms_ids=list(S["terms"].values()))
    v2 = await verdicts_of(conn)
    check(again["computed"] == 0 and again["unchanged"] == len(EXPECT)
          and all(v2[k]["id"] == v[k]["id"] for k in EXPECT),
          "idempotent: a second run over the fixture terms writes nothing and keeps every verdict row", f"{again}")
    # a terms change (valid-axis restatement: PRE becomes final) → the old verdict is closed, a new one computed
    old_terms, new_terms = S["terms"]["PRE"], uuid.uuid4()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("UPDATE portfolio.securities_global_note_terms SET valid_to = now() WHERE id = $1", old_terms)
        await conn.execute(
            """INSERT INTO portfolio.securities_global_note_terms (id, global_security_id, reference_filing_id,
                   terms_status, product_archetype, protection_type, basket_type, return_basis, is_decrement_index,
                   notional_currency, protection_pct, cap_pct, participation_rate, initial_valuation_date,
                   final_valuation_date, tenor_years, field_status, extraction_confidence, autocall_frequency)
               SELECT $2, global_security_id, NULL, 'final', product_archetype, protection_type, basket_type,
                      return_basis, is_decrement_index, notional_currency, protection_pct, cap_pct, participation_rate,
                      initial_valuation_date, final_valuation_date, tenor_years, field_status, extraction_confidence,
                      autocall_frequency
               FROM portfolio.securities_global_note_terms WHERE id = $1""", old_terms, new_terms)
    S["terms"]["PRE"] = new_terms
    with _ctx(None, True):
        re_run = await N.run_verdicts(pool)
    old = await platform(conn, "SELECT valid_to FROM portfolio.note_replicability WHERE note_terms_id = $1", old_terms,
                         fetch="fetchval")
    newv = await platform(conn, "SELECT * FROM portfolio.note_replicability WHERE note_terms_id = $1 AND valid_to IS "
                                "NULL", new_terms, fetch="fetchrow")
    check(old is not None and newv is not None and newv["is_preliminary"] is False and newv["verdict"] == "static",
          "recomputes on a terms change: the restated (now final) terms get a new verdict, the old one is closed",
          f"old closed={old is not None} new={dict(newv) if newv else None} run={ {k: re_run[k] for k in ('computed', 'closed')} }")
    try:
        await platform(conn, "UPDATE portfolio.note_replicability SET verdict = 'static' WHERE id = $1", v["WO"]["id"])
        immut = False
    except asyncpg.PostgresError as exc:
        immut = "only ever closed" in str(exc)
    check(immut, "a verdict row is immutable (written once, only ever closed)")
    # the rule per REAL row (never a frozen count): one live verdict per live real terms row, its verdict / mapping
    # = the service's assessment, and each not-replicable reason backed by the feature on the row itself
    with _ctx(None, True):
        async with pool.acquire() as c2:
            reals = [t for t in await N.live_terms(c2) if not t["security_name"].startswith(NAME_PREFIX)]
            unds = await N.underlyings_of(c2, {r["global_security_id"] for r in reals})
            reads = await N.readings_of(c2, {r["reference_filing_id"] for r in reals})
    live = {r["note_terms_id"]: dict(r) for r in await platform(
        conn, "SELECT * FROM portfolio.note_replicability WHERE valid_to IS NULL AND system_to IS NULL")}
    culprits = []
    for t in reals:
        row = live.get(t["id"])
        a = N.assess(t, n_underlyings=len(unds.get(str(t["global_security_id"]), [])),
                     readings=reads.get(str(t["reference_filing_id"])))
        codes = {x["code"] for x in json.loads(row["reasons"])} if row else set()
        backed = all({
            "worst_of": t["basket_type"] == "worst_of", "basket": t["basket_type"] == "basket",
            "decrement_index": t["is_decrement_index"] is True,
            "autocall": t["product_archetype"] == "autocallable" or t["autocall_frequency"] not in (None, "none")
            or t["autocall_barrier_pct"] is not None,
            "contingent_coupon": t["coupon_barrier_pct"] is not None,
            "no_mapping": t["product_archetype"] in (None, "other")}.get(c, True) for c in codes)
        if row is None or row["verdict"] != a["verdict"] or row["mapping"] != a["mapping"] or not backed \
                or row["is_preliminary"] != (t["terms_status"] == "preliminary"):
            culprits.append((t["id"], f"{t['security_name']}: {row['verdict'] if row else None} vs {a['verdict']} "
                                      f"{codes}"))
    bad_ids = {c[0] for c in culprits}
    VS.when_empty(reals, "real notes: every live terms row has exactly one live verdict = the assessment, every "
                  "reason backed by the row's own feature, preliminary marked",
                  lambda t: t["id"] not in bad_ids, check=check, find=find,
                  detail=lambda bad: "; ".join(c[1] for c in culprits[:4]))


async def build_as(uid, key, body=None) -> tuple[int, dict]:
    return await api(uid, "POST", f"{_N}/{sec(key)}/replicas", body if body is not None else {"broad_based_index": True})


async def y_replicas(conn):
    section("[Y] Replicas exact for buffered / principal-protected; digital within tolerance; refusals write nothing")
    level0 = Decimal("5000")
    S["q_start"] = await quote(U_ADMIN1, "IDX", "index_level", level0, at(S["initial"]))
    c, o = await build_as(U_ADMIN1, "BUF")
    S["rep_buf"] = (o.get("replica") or {}).get("id")
    legs = o.get("legs") or []
    shape = [(x["kind"], x.get("option_type"), x["direction"], x["ratio"], x.get("strike_pct")) for x in legs]
    want = [("underlying", None, "long", "1", None), ("option", "put", "long", "1", "1"),
            ("option", "put", "short", "1", "0.8"), ("option", "call", "short", "1", "1.25")]
    check(c == 201 and shape == want, "buffered single-index note: legs exact — underlying, long put 100%, short put "
          "100% − buffer (80%), short call 100% + cap (125%)", f"{c} {shape} {str(o)[:200]}")
    levels = [(x.get("strike_level"), (x.get("contract") or {}).get("strike")) for x in legs if x["kind"] == "option"]
    exp_levels = [str((level0 * D(p)).quantize(Decimal("0.01"))) for p in ("1", "0.8", "1.25")]
    check([D(a) for a, _ in levels] == [D(x) for x in exp_levels] and all(b is not None and D(a) == D(b)
                                                                          for a, b in levels)
          and all((x.get("contract") or {}).get("expiry") == S["final"].isoformat() for x in legs if x["kind"] == "option"),
          "strike levels = start level × strike % (cents); each leg's contract (rep8 find_or_create_contract) carries "
          "the strike and the note's final valuation date as expiry", f"{levels} {exp_levels}")
    rows = (o.get("computed") or {}).get("rows") or []
    comp = {r["term"]: (r["stated"], r["computed"], r["equal"]) for r in rows}
    check(comp.get("cap") == ("0.25", "0.25", True) and comp.get("buffer") == ("0.2", "0.2", True)
          and comp.get("floor") == ("-0.8", "-0.8", True) and all(r["equal"] for r in rows),
          "computed cap / buffer / floor equal the note's stated values exactly (no difference)", f"{comp}")
    pts = (o.get("payoff") or {}).get("points") or []
    culprits = [p for p in pts if D(p["note_return"]) != stated("BUF", D(p["underlying_return"]))
                or D(p["replica_return"]) != value_of(legs, ONE + D(p["underlying_return"])) - ONE
                or D(p["replica_return"]) != D(p["note_return"])]
    check(len(pts) >= 40 and not culprits, f"payoff grid: the replica equals the note's stated payoff at every one of "
          f"{len(pts)} grid points (both recomputed here from the terms and the legs)", f"{culprits[:3]}")
    instr = [x.get("instruction") or "" for x in legs]
    check(all("manual" in s for s in instr) and o.get("manual_only", "").startswith("Nothing trades"),
          "every leg is a manual instruction (nothing trades)", f"{instr[:2]}")
    # principal-protected: Treasury leg + call spread
    c, o = await build_as(U_ADMIN1, "PPN", {"broad_based_index": True, "treasury_global_security_id": sec("TSY")})
    S["rep_ppn"] = (o.get("replica") or {}).get("id")
    legs = o.get("legs") or []
    shape = [(x["kind"], x.get("option_type"), x["direction"], x["ratio"], x.get("strike_pct"), x.get("face_pct"))
             for x in legs]
    want = [("treasury", None, "long", "1", None, "1"), ("option", "call", "long", "0.5", "1", None),
            ("option", "call", "short", "0.5", "1.4", None)]
    pts = (o.get("payoff") or {}).get("points") or []
    culprits = [p for p in pts if D(p["replica_return"]) != stated("PPN", D(p["underlying_return"]))
                or D(p["replica_return"]) != value_of(legs, ONE + D(p["underlying_return"])) - ONE]
    comp = {r["term"]: (r["stated"], r["computed"]) for r in (o.get("computed") or {}).get("rows") or []}
    check(c == 201 and shape == want and not culprits and comp.get("participation") == ("0.5", "0.5")
          and comp.get("cap") == ("0.2", "0.2") and comp.get("floor") == ("0", "0")
          and (o.get("replica") or {}).get("treasury_maturity") == S["final"].isoformat(),
          "principal-protected: a Treasury zero (face 100%, maturing at the final valuation) + a call spread "
          "(0.5× at 100%, short 0.5× at 140%) reproduce protection, participation and cap exactly at expiry",
          f"{c} {shape} {culprits[:2]} {comp}")
    # digital: within the stated tolerance
    c, o = await build_as(U_ADMIN1, "DIG")
    S["rep_dig"] = (o.get("replica") or {}).get("id")
    legs = o.get("legs") or []
    spread = D((o.get("replica") or {}).get("digital_spread_pct") or "0")
    d = D(NOTE_TERMS["DIG"]["cap_pct"])
    fine = [Decimal(i) / Decimal(1000) for i in range(-1000, 1001)]
    outside = [r for r in fine if not (ZERO <= r < spread) and value_of(legs, ONE + r) - ONE != stated("DIG", r)]
    inside = [r for r in fine if ZERO <= r < spread]
    worst = max((abs(value_of(legs, ONE + r) - ONE - stated("DIG", r)) for r in inside), default=None)
    approx = o.get("approximation") or {}
    check(c == 201 and spread == N.DEFAULT_SPREAD and not outside and inside and worst == d
          and approx.get("max_error") == str(d) and approx.get("region") == ["0", str(spread)],
          f"digital: exact at expiry outside [0, {spread}) on a 0.1% grid of 2001 points; inside, the replica pays "
          f"less by at most the digital amount {d} — exactly the stated tolerance", f"{c} {outside[:3]} {worst} {approx}")
    check(o.get("replica", {}).get("verdict") == "approximate" and "less than the note" in (approx.get("text") or ""),
          "the approximation statement is on the replica", approx.get("text", "")[:160])
    # refusals write nothing
    before = await replica_counts(conn)
    outs = {}
    for key in ("WO", "AUTO", "REV", "UNR", "OTH"):
        outs[key] = await build_as(U_ADMIN1, key)
    after = await replica_counts(conn)
    det = {k: (cv, json.dumps((ov.get("detail") or ov.get("error") or ""))) for k, (cv, ov) in outs.items()}
    check(all(cv == 409 for cv, _ in det.values()) and "Worst-of three underlyings" in det["WO"][1]
          and "autocall" in det["AUTO"][1].lower() and "Terms need review" in det["REV"][1]
          and "Underlying" in det["UNR"][1] and "not confirmed" in det["UNR"][1],
          "worst-of / autocallable / needs-review / unconfirmed-underlying / 'other' builds are refused (409) with the "
          "reason", "; ".join(f"{k}: {v[0]} {v[1][:90]}" for k, v in det.items()))
    check(before == after, "a refusal writes nothing — no replica, no leg, no contract", f"{before} → {after}")


async def y_pricing(conn):
    section("[Y] Pricing exact with quotes; 'not priced' naming the gap")
    rid = S["rep_buf"]
    c, o = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rid}/price", {})
    cmp_ = o.get("comparison") or {}
    miss = " | ".join(cmp_.get("missing") or [])
    legs = o.get("legs") or []
    names = [(x.get("contract") or {}).get("name") or "" for x in legs if x["kind"] == "option"]
    check(c == 201 and cmp_.get("status") == "not_priced" and cmp_.get("embedded_fee_pct") is None
          and "note's price" in miss and all(n and n in miss for n in names)
          and "not priced — enter a quote" in (cmp_.get("text") or ""),
          "only the start level quoted → 'not priced — enter a quote', naming the note price and every premium "
          "(the start-level quote stands as the newest level, shown with its as-of)",
          f"{c} {cmp_.get('status')} {miss[:300]}")
    now = datetime.now(timezone.utc)
    qs = {"note": await quote(U_ADMIN1, "BUF", "note_price", "97.25", now - timedelta(hours=3)),
          "level": await quote(U_ADMIN1, "IDX", "index_level", "5250", now - timedelta(hours=2))}
    prem = {"1": "210.5", "0.8": "60.25", "1.25": "95.75"}
    for x in legs:
        if x["kind"] == "option":
            qs[x["strike_pct"]] = await quote(U_ADMIN1, x["contract"]["global_security_id"], "premium",
                                              prem[x["strike_pct"]], now - timedelta(hours=1))
    c, o = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rid}/price", {})
    cmp_ = o.get("comparison") or {}
    l0, total = Decimal("5000"), Decimal(0)
    for x in legs:
        if x["kind"] == "underlying":
            total += D(x["ratio"]) * Decimal("5250") / l0
        else:
            total += (ONE if x["direction"] == "long" else -ONE) * D(x["ratio"]) * D(prem[x["strike_pct"]]) / l0
    cost = (total * HUNDRED).quantize(BP, rounding=ROUND_HALF_EVEN)
    fee = Decimal("97.25") - cost
    ids = {i.get("quote_id") for i in cmp_.get("inputs") or []}
    as_of = min(datetime.fromisoformat(q["as_of"]) for q in qs.values())
    check(c == 201 and cmp_.get("status") == "priced" and D(cmp_.get("replica_cost_pct")) == cost
          and D(cmp_.get("embedded_fee_pct")) == fee and {q["id"] for q in qs.values()} <= ids
          and datetime.fromisoformat(cmp_["as_of"]) == as_of and cmp_.get("label") == "estimate",
          f"all quotes present → replica cost {cost} and embedded fee {fee} per 100 exactly; every quote listed; the "
          "as-of is the oldest input", f"{c} {cmp_.get('replica_cost_pct')} {cmp_.get('embedded_fee_pct')} {cmp_.get('as_of')}")
    db = await scoped(conn, ORG_A, "SELECT status, embedded_fee_pct, note_price_pct, replica_cost_pct FROM "
                                   "public.note_replica_comparisons WHERE replica_id = $1 ORDER BY created_at DESC "
                                   "LIMIT 1", UUID(rid), fetch="fetchrow")
    check(db is not None and db["status"] == "priced" and db["embedded_fee_pct"] == fee
          and db["embedded_fee_pct"] == db["note_price_pct"] - db["replica_cost_pct"],
          "the comparison persisted (re-read on its own connection): fee = note price − replica cost", f"{dict(db) if db else None}")
    try:
        await scoped(conn, ORG_A, "UPDATE public.note_replica_comparisons SET note_price_pct = 1 WHERE replica_id = $1",
                     UUID(rid))
        upd = False
    except asyncpg.PostgresError as exc:
        upd = "never updated" in str(exc)
    check(upd, "comparisons are insert-only (an update is refused)")
    S["fee_buf"] = fee
    # one input missing → not priced, naming it: the principal-protected replica without a Treasury yield
    rp = S["rep_ppn"]
    _, o = await api(U_ADMIN1, "GET", f"{_N}/replicas/{rp}")
    if (o.get("replica") or {}).get("start_level") is None:
        _, o = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rp}/price", {})
    plegs = o.get("legs") or []
    pprem = {"1": "180", "1.4": "40"}
    await quote(U_ADMIN1, "PPN", "note_price", "99.5", now - timedelta(hours=2))
    for x in plegs:
        if x["kind"] == "option":
            await quote(U_ADMIN1, x["contract"]["global_security_id"], "premium", pprem[x["strike_pct"]],
                        now - timedelta(hours=1))
    c, o = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rp}/price", {})
    cmp_ = o.get("comparison") or {}
    check(c == 201 and cmp_.get("status") == "not_priced" and len(cmp_.get("missing") or []) == 1
          and "Treasury yield" in (cmp_["missing"] or [""])[0] and SEC_NAMES["TSY"] in cmp_["missing"][0],
          "one quote missing → 'not priced', naming exactly that one (the Treasury yield on the fixture Treasury)",
          f"{c} {cmp_.get('missing')}")
    yq = await quote(U_ADMIN1, "TSY", "treasury_yield", "4.25", now - timedelta(minutes=30))
    c, o = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rp}/price", {})
    cmp_ = o.get("comparison") or {}
    total = Decimal(0)
    for x in plegs:
        if x["kind"] == "treasury":
            days = (date.fromisoformat(x["maturity"]) - datetime.fromisoformat(yq["as_of"]).astimezone(
                timezone.utc).date()).days
            total += discount(D(x["face_pct"]) * D(x["ratio"]), Decimal("4.25"), days)
        elif x["kind"] == "option":
            total += (ONE if x["direction"] == "long" else -ONE) * D(x["ratio"]) * D(pprem[x["strike_pct"]]) / Decimal("5000")
    cost = (total * HUNDRED).quantize(BP, rounding=ROUND_HALF_EVEN)
    check(c == 201 and cmp_.get("status") == "priced" and D(cmp_.get("replica_cost_pct")) == cost
          and D(cmp_.get("embedded_fee_pct")) == Decimal("99.5") - cost,
          f"with the yield: Treasury at face × (1 + y)^−T plus the calls → cost {cost}, fee exact",
          f"{cmp_.get('replica_cost_pct')} {cmp_.get('embedded_fee_pct')}")
    v = o.get("vocabularies") or {}
    check((v.get("ssvi") or {}).get("used") is False and (v.get("ssvi") or {}).get("min_quotes_per_slice") == 8,
          "the SSVI surface is not used, and the envelope states its minimum (8 quotes per expiry) — nothing "
          "extrapolated", f"{v.get('ssvi')}")


async def y_maker_checker(conn):
    section("[Y] Maker-checker on a replica becoming the firm's alternative")
    rid = S["rep_buf"]
    c, o = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rid}/submit", {})
    check(c == 200 and (o.get("replica") or {}).get("status") == "pending_approval"
          and "approve" not in (o.get("actions") or []),
          "the maker submits; the maker is offered no approve action", f"{c} {o.get('actions')} {str(o)[:160]}")
    c1, o1 = await api(U_ADMIN1, "POST", f"{_N}/replicas/{rid}/approve", {})
    st1 = await scoped(conn, ORG_A, "SELECT status FROM public.note_replicas WHERE id = $1 AND system_to IS NULL",
                       UUID(rid), fetch="fetchval")
    check(c1 == 403 and "maker-checker" in json.dumps(o1).lower() and st1 == "pending_approval",
          "the maker cannot approve (403, maker-checker) and the replica is unchanged", f"{c1} {st1}")
    c2, o2 = await api(U_ADMIN2, "POST", f"{_N}/replicas/{rid}/approve", {"note": "REP11 verify approval"})
    row = await scoped(conn, ORG_A, "SELECT status, checker_id, maker_ids FROM public.note_replicas WHERE id = $1 AND "
                                    "system_to IS NULL", UUID(rid), fetch="fetchrow")
    check(c2 == 200 and row is not None and row["status"] == "approved" and row["checker_id"] == U_ADMIN2
          and U_ADMIN2 not in (row["maker_ids"] or []),
          "a second holder of approve_replication approves; re-read: approved, checker ≠ every maker",
          f"{c2} {dict(row) if row else None}")
    try:
        await scoped(conn, ORG_A, "UPDATE public.note_replicas SET checker_id = $2 WHERE id = $1 AND system_to IS NULL",
                     UUID(rid), U_ADMIN1)
        blocked = False
    except asyncpg.PostgresError as exc:
        blocked = True
        S["mc_db"] = str(exc).split("\n")[0]
    check(blocked, "the database refuses a checker who is a maker (and a decided replica's terms are frozen)",
          S.get("mc_db", ""))


async def y_xray(conn):
    section("[Y] X-ray marker; member visibility per policy both ways")
    _, a = await api(U_ADMIN1, "GET", f"/api/v1/xray/households/{HH_A}")
    notes = (a.get("xray") or {}).get("notes") or []
    n = next((x for x in notes if x["security_id"] == sec("BUF")), None)
    check(n is not None and n["verdict"]["verdict"] == "static"
          and (n.get("replicable_alternative") or {}).get("label") == "replicable alternative"
          and (n.get("replicable_alternative") or {}).get("replica_id") == S["rep_buf"]
          and (n.get("terms") or {}).get("cap_pct") == "0.25" and n.get("distribution", {}).get("known") is False,
          "the household's X-ray marks the held note: verdict, stated terms, distribution ('not in the extracted "
          "terms') and 'replicable alternative' linking the approved replica", f"{str(n)[:300]}")
    check(n is not None and (n.get("estimate") or {}).get("status") == "priced"
          and D((n.get("estimate") or {}).get("embedded_fee_pct")) == S["fee_buf"],
          "staff (an org admin) see the embedded-fee estimate", f"{(n or {}).get('estimate')}")
    _, m = await api(U_MEMBER, "GET", f"/api/v1/xray/households/{HH_A}")
    mn = next((x for x in (m.get("xray") or {}).get("notes") or [] if x["security_id"] == sec("BUF")), None)
    check(mn is not None and mn["verdict"]["verdict"] == "static" and (mn.get("estimate") or {}).get("withheld") is True
          and "embedded_fee_pct" not in json.dumps(mn.get("estimate")) and S["fee_buf"].to_eng_string() not in json.dumps(mn),
          "a member WITHOUT an approved policy sees the verdict and terms but no fee figure (withheld)",
          f"{str(mn)[:300]}")
    _, rm = await api(U_MEMBER, "GET", f"{_N}/replicas/{S['rep_buf']}")
    check((rm.get("comparison") or {}).get("withheld") is True, "the replica page withholds the figure from that "
          "member too", f"{rm.get('comparison')}")
    c, p = await api(U_ADMIN1, "POST", f"{_R}/hypothetical-policy",
                     {"policy_text": "REP11 verify — members may see estimates", "audience": "staff_and_members",
                      "disclosure_text": "REP11 verify disclosure — hypothetical, an estimate", "reason": "REP11 verify"})
    pid = p.get("id") or (p.get("policy") or {}).get("id")
    c2, _ = await api(U_ADMIN2, "POST", f"{_R}/hypothetical-policy/{pid}/approve", {})
    _, m2 = await api(U_MEMBER, "GET", f"/api/v1/xray/households/{HH_A}")
    mn2 = next((x for x in (m2.get("xray") or {}).get("notes") or [] if x["security_id"] == sec("BUF")), None)
    check(c == 201 and c2 == 200 and mn2 is not None and (mn2.get("estimate") or {}).get("status") == "priced"
          and D(mn2["estimate"]["embedded_fee_pct"]) == S["fee_buf"]
          and mn2.get("estimate_disclosure") == "REP11 verify disclosure — hypothetical, an estimate",
          "with an approved staff_and_members policy the member sees the estimate, with the disclosure",
          f"{c} {c2} {str(mn2)[:300]}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with rolbypassrls = "
          "false", f"{who['u']} {who['b']}")
    cb, ob = await build_as(U_ADMIN_B, "PPN", {"broad_based_index": True})
    S["rep_b"] = (ob.get("replica") or {}).get("id")
    res = {}
    for t in ("public.note_replicas", "public.note_replica_legs"):
        res[t] = [int(await scoped(conn, a, f"SELECT count(*) FROM {t} WHERE org_id = $1", b, fetch="fetchval"))
                  for a, b in ((ORG_A, ORG_A), (ORG_B, ORG_A), (ORG_B, ORG_B), (ORG_A, ORG_B))]
    cmp_ = [int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.note_replica_comparisons WHERE org_id = $1",
                             ORG_A, fetch="fetchval")),
            int(await scoped(conn, ORG_B, "SELECT count(*) FROM public.note_replica_comparisons WHERE org_id = $1",
                             ORG_A, fetch="fetchval"))]
    check(cb == 201 and all(a > 0 and b == 0 and c > 0 and d == 0 for a, b, c, d in res.values())
          and cmp_[0] > 0 and cmp_[1] == 0,
          "RLS both directions: each org sees its own replicas and legs (non-empty) and none of the other's; org B "
          "sees none of org A's comparisons", f"{cb} {res} {cmp_}")
    upd = await scoped(conn, ORG_B, "UPDATE public.note_replicas SET status_reason = 'x' WHERE id = $1 RETURNING id",
                       UUID(S["rep_buf"]), fetch="fetch")
    check(upd == [], "org B cannot touch org A's replica (zero rows)")
    codes = [(await api(U_ADMIN_B, "GET", f"{_N}/replicas/{S['rep_buf']}"))[0],
             (await api(U_ADMIN1, "GET", f"{_N}/replicas/{S['rep_b']}"))[0],
             (await api(U_ADMIN_B, "POST", f"{_N}/replicas/{S['rep_buf']}/price", {}))[0]]
    check(codes == [404, 404, 404], "another org's replica is a 404 both ways (read and price)", f"{codes}")
    # gates both ways on identical requests
    body = {"broad_based_index": True}
    cv, ov = await build_as(U_VIEWER, "RC", body)
    n_before = await replica_counts(conn)
    ca, _ = await build_as(U_ADMIN1, "RC", body)
    check(cv == 403 and "manage_replication" in json.dumps(ov) and ca == 201,
          "build: the view-only caller is refused (403 naming manage_replication); the identical request by the admin "
          "is 201", f"{cv} {ca} {str(ov)[:120]}")
    cv2, ov2 = await api(U_VIEWER, "POST", f"{_N}/replicas/{S['rep_dig']}/approve", {})
    cv3, _ = await api(U_VIEWER, "POST", f"{_N}/verdicts/run", {})
    check(cv2 == 403 and "approve_replication" in json.dumps(ov2) and cv3 == 403,
          "approve / recompute: the viewer is refused (403 naming the permission)", f"{cv2} {cv3}")
    cl, ol = await api(U_VIEWER, "GET", _N)
    cn, on = await api(U_VIEWER, "GET", f"{_N}/{sec('BUF')}")
    pv = on.get("permissions") or {}
    check(cl == 200 and cn == 200 and pv.get("can_write") is False and on.get("actions") == []
          and (on.get("vocabularies") or {}).get("editable") == [] and ol.get("actions") == []
          and bool((on.get("vocabularies") or {}).get("verdicts")),
          "envelopes for a viewer: reads allowed, no actions, empty editable; labels from the vocabularies",
          f"{cl} {cn} {on.get('actions')} {pv}")
    _, oa = await api(U_ADMIN1, "GET", f"{_N}/{sec('DIG')}")
    check("build" not in (oa.get("actions") or []) and (oa.get("permissions") or {}).get("can_write") is True,
          "the admin is not offered 'build' while a replica of the note is open", f"{oa.get('actions')}")
    find("gates", f"replica counts before the admin's RC build: {n_before}")


async def y_real_rows(conn):
    section("[Y] Real note-terms rows untouched (count + checksum)")
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
    check(not miss, "each of rep11's routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^\s*ok \d+ - (?:notes list|note page|replica page|pricing|X-ray: a held note|lib: pct)",
                      out, re.M)
    check(len(mine) >= 6 and "not ok" not in out, "the rep11 render tests ran and passed (no build / approve control "
          "without the flags; the estimate withheld for a member)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep8.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep8.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_rep8.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-200:] if not total else ''}")


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
            await run("verdicts", y_verdicts, conn)
            await run("replicas", y_replicas, conn)
            if S.get("rep_buf") and S.get("rep_ppn"):
                await run("pricing", y_pricing, conn)
            if S.get("fee_buf") is not None:
                await run("maker-checker", y_maker_checker, conn)
                await run("x-ray", y_xray, conn)
            else:
                check(False, "maker-checker and the X-ray need the priced buffered replica", "an earlier section failed")
            if S.get("rep_buf") and S.get("rep_dig"):
                await run("isolation + gates", y_isolation, conn)
            else:
                check(False, "isolation + gates need the fixture replicas", "an earlier section failed")
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
            check(left == 0, "zero fixture rows remain (platform fixture notes, terms, verdicts, links and contracts "
                  "included)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    if t == "portfolio.note_replicability":
                        # the verdict job ran over REAL notes too: their verdicts are its own output, never a fixture
                        real_new = post[t] - pre[t]
                        check(real_new >= 0, f"{t}: no real verdict deleted (the job may add real verdicts — its "
                              "output, not a fixture)", f"{pre[t]} → {post[t]}")
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
