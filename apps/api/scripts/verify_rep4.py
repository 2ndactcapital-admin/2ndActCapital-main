"""verify_rep4 — Replication Studio, sprint 4 (REP4): build a model from a fund —
"Holdings of <TICKER>" models from rep1's approved terms + live holdings into
rep3's registry, the five-question viability check, the fund-versus-replica
cost comparison, the registry "join the existing model" offer, and
holdings / terms monitoring that drafts new model versions for review.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep4.py 2>&1 | grep -E "^\\[FAIL\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``), the Auth0 profile call and
R2 (``services.storage`` → an in-memory dict). NO network: the real httpx
network transport is replaced by one that records and refuses — any attempt is
a failed check. One in-process stub, named where it is used: the custodian
capability lookup returns fractional_shares 'yes' for ONE direct service call
(the only way to prove the fractional minimum without touching the real,
platform-wide custodian_capabilities rows).

REAL: the app (middleware, Principal, every gate, every handler) through an
ASGI transport, rep3's model services and human tasks, the workflow engine,
RLS, the live database. The script's own connection is DATABASE_URL =
app_service (rolbypassrls = false, asserted).

FIXTURES: two orgs (99000000-…-0000000e4001 / e4002), five users created
explicitly with the auth0_sub their token carries and an @test.local email
(is_test by trigger). Fixture securities carry the reserved prefix ZZRF
(asserted absent from the real master first); fixture holdings snapshots,
rows, relationships and prices exist ONLY on those securities.

TEARDOWN (start and end): the operator DSN (role postgres) with
models.teardown / replica.teardown = 'fixture' in its transaction — the only
path past the immutability triggers — every table's DELETE keyed by fixture
ids, run child-before-parent in the order read from pg_constraint; then users /
roles / orgs of this run only; then every counted table is re-read for its
exact pre-test count and real models / entries / terms / prices / fee
schedules / snapshots / capabilities / users are compared by count + checksum.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

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
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

BEFORE_REF = "3c09f92"   # rep4-work base — main after "merge rep3" + schema refresh
TAG = "rep4verify"
PREFIX = "ZZRF"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_NAMES = tuple(f"REP4 VERIFY {k}" for k in ("A", "B", "C", "D", "E", "FUND F", "FUND H", "FUND G", "FUND K",
                                               "FUND L"))


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e4{n:03d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_ADMIN1, U_ADMIN2, U_VIEWER, U_NONE, U_ADMIN_B = (_id(110 + i) for i in range(5))
USERS = {   # org, users.role, short, rbac role, full name
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP4 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP4 Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep4_viewer", "REP4 Viewer"),
    U_NONE: (ORG_A, "member", "none", "rep4_none", "REP4 No Access"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP4 Org B Admin"),
}
STOCKS = "ABCDE"
CUSIP = {**{k: f"ZZRF0001{i + 1}" for i, k in enumerate(STOCKS)},
         **{k: f"ZZRF0002{i + 1}" for i, k in enumerate("FHGKL")}}
TICKER = {**{k: f"ZZRF{k}" for k in STOCKS}, **{k: f"ZZRF{k}" for k in "FHGKL"}}
ENTRY = {"F": _id(201), "H": _id(202), "G": _id(203), "K": _id(204), "L": _id(205), "FB": _id(206)}
TODAY = datetime.now(timezone.utc).date()
UNMATCHED_TEXT = "ZZRF UNMATCHED HOLDING (cusip ZZRF00099)"

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.domain_event_deliveries",
    "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps", "public.workflow_runs",
    "public.workflow_run_steps", "public.org_settings", "public.fee_schedules", "public.fee_schedule_tiers",
    "public.custodian_capabilities",
    "portfolio.securities_global", "portfolio.securities_global_identifiers", "portfolio.securities_global_prices",
    "portfolio.securities_global_relationships", "portfolio.replica_holdings_snapshots",
    "portfolio.replica_holdings_rows", "portfolio.replica_securities", "portfolio.replica_terms_versions",
    "portfolio.replica_terms_actions", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.model_events", "portfolio.replica_model_specs",
    "portfolio.replica_registry_keys", "portfolio.replica_viability_reports",
)
TOUCHED_FOR_CONSTRAINTS = (
    "portfolio.replica_model_specs", "portfolio.replica_registry_keys", "portfolio.replica_viability_reports",
    "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions", "portfolio.model_events",
    "portfolio.replica_securities", "portfolio.replica_terms_versions", "portfolio.replica_holdings_snapshots",
    "portfolio.replica_holdings_rows", "portfolio.securities_global_prices", "public.fee_schedules",
    "public.fee_schedule_tiers",
)

S: dict = {}
NETWORK_CALLS: list[str] = []
STORE: dict[str, bytes] = {}
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
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def git_show(ref: str, path: str) -> str:
    code, out, _ = run_cmd(["git", "show", f"{ref}:{path}"], REPO, timeout=60)
    return out if code == 0 else ""


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def D(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def sha(label: str) -> str:
    return hashlib.sha256(f"{TAG}:{label}".encode()).hexdigest()


def detail_of(out: dict) -> dict:
    d = out.get("detail")
    return d if isinstance(d, dict) else {"message": d}


# ═══ Independent expected-weight arithmetic (Fraction, not the service's Decimal code) ═══
def expected_alloc(weights: list[tuple[str, Decimal]], total: Decimal) -> dict[str, Decimal]:
    """Each share = w / Σw × total, floored to 1e-10; the left-over 1e-10 units go to the
    largest remainders (ties: earlier in ``weights``). Fractions throughout."""
    s = sum((Fraction(str(w)) for _, w in weights), Fraction(0))
    units = Fraction(str(total)) * 10 ** 10
    raw = [(k, Fraction(str(w)) / s * units, i) for i, (k, w) in enumerate(weights)]
    floors = {k: (u.numerator // u.denominator) for k, u, _ in raw}
    left = int(units) - sum(floors.values())
    for k, _, _ in sorted(raw, key=lambda x: (-(x[1] - floors[x[0]]), x[2]))[:left]:
        floors[k] += 1
    return {k: Decimal(v) / Decimal(10 ** 10) for k, v in floors.items()}


# ═══ DB helpers (verify's own app_service connection; RLS context explicit) ═══
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


async def fixture_security_ids(conn) -> list:
    rows = await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")
    return [r["global_security_id"] for r in rows]


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint must never touch (count + checksum)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    users = [str(u) for u in USERS]
    secs = [str(s) for s in await platform(conn, "SELECT DISTINCT global_security_id FROM "
                                                 "portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                                           f"{PREFIX}%")]
    return {
        "models": await fingerprint(conn, "SELECT * FROM portfolio.models WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "model_versions": await fingerprint(conn, "SELECT * FROM portfolio.model_versions WHERE NOT "
                                                  "(org_id = ANY($1::uuid[]))", orgs),
        "replica_securities": await fingerprint(conn, "SELECT * FROM portfolio.replica_securities WHERE NOT "
                                                      "(org_id = ANY($1::uuid[]))", orgs),
        "replica_terms_versions": await fingerprint(conn, "SELECT * FROM portfolio.replica_terms_versions WHERE NOT "
                                                          "(org_id = ANY($1::uuid[]))", orgs),
        "replica_holdings_snapshots": await fingerprint(conn, "SELECT * FROM portfolio.replica_holdings_snapshots "
                                                              "WHERE NOT (fund_global_security_id::text = "
                                                              "ANY($1::text[]))", secs),
        "securities_global_prices": await fingerprint(conn, "SELECT * FROM portfolio.securities_global_prices WHERE "
                                                            "NOT (global_security_id::text = ANY($1::text[]))", secs),
        "fee_schedules": await fingerprint(conn, "SELECT * FROM public.fee_schedules WHERE NOT "
                                                 "(org_id = ANY($1::uuid[]))", orgs),
        "custodian_capabilities": await fingerprint(conn, "SELECT * FROM public.custodian_capabilities"),
        "users": await fingerprint(conn, "SELECT id, org_id, role, status, email FROM public.users WHERE NOT "
                                         "(id = ANY($1::uuid[]))", users),
        "workflow_runs": await fingerprint(conn, "SELECT * FROM public.workflow_runs WHERE NOT "
                                                 "(org_id = ANY($1::uuid[]))", orgs),
    }


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


_FK_CACHE: dict = {}


async def fk_children(conn, table: str):
    if table not in _FK_CACHE:
        _FK_CACHE[table] = await conn.fetch("""
            SELECT cn.nspname || '.' || cc.relname AS child,
                   array_agg(ca.attname ORDER BY k.ord) AS child_cols,
                   array_agg(pa.attname ORDER BY k.ord) AS parent_cols
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord) ON true
            JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
            JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.ck
            JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
            WHERE c.contype::text = 'f' AND c.confrelid = $1::regclass
            GROUP BY c.oid, cn.nspname, cc.relname""", table)
    return _FK_CACHE[table]


async def delete_tree(conn, table: str, where: str, params: list, depth=0, log=None, path=()):
    """Child-before-parent delete for a FIXTURE-ID parent (users / roles / orgs
    of this run only), FKs read from pg_constraint (recursively)."""
    if depth > 14:
        raise RuntimeError(f"FK recursion too deep at {table} via {' > '.join(path)}")
    path = path + (table,)
    for fk in await fk_children(conn, table):
        if fk["child"] in path:
            continue
        cc = ", ".join(f'"{c}"' for c in fk["child_cols"])
        pc = ", ".join(f'"{c}"' for c in fk["parent_cols"])
        await delete_tree(conn, fk["child"], f"({cc}) IN (SELECT {pc} FROM {table} WHERE {where})",
                          params, depth + 1, log, path)
    status = await conn.execute(f"DELETE FROM {table} WHERE {where}", *params)
    n = int(status.rsplit(" ", 1)[-1])
    if n and log is not None:
        log.append(f"{table}: {n}")


async def fk_order(conn, tables: list[str]) -> list[str]:
    """``tables`` sorted child-before-parent from pg_constraint (Kahn)."""
    edges = {t: set() for t in tables}
    rows = await conn.fetch("""
        SELECT cn.nspname || '.' || cc.relname AS child, pn.nspname || '.' || pc.relname AS parent
        FROM pg_constraint c JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
        JOIN pg_class pc ON pc.oid = c.confrelid JOIN pg_namespace pn ON pn.oid = pc.relnamespace
        WHERE c.contype = 'f'""")
    for r in rows:
        if r["child"] in edges and r["parent"] in edges and r["child"] != r["parent"]:
            edges[r["child"]].add(r["parent"])
    children_of = {t: {c for c, ps in edges.items() if t in ps} for t in tables}
    out, done = [], set()
    while len(out) < len(tables):
        ready = [t for t in tables if t not in done and children_of[t] <= done]
        if not ready:
            raise RuntimeError(f"FK cycle among {sorted(set(tables) - done)}")
        for t in ready:
            out.append(t)
            done.add(t)
    return out


async def teardown(_conn=None) -> list[str]:
    """Operator connection, models/replica teardown = 'fixture'; every DELETE keyed by fixture ids, ordered
    child-before-parent from pg_constraint."""
    log: list[str] = []
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('models.teardown', 'fixture', true), "
                             "set_config('replica.teardown', 'fixture', true), "
                             "set_config('execution.teardown', 'fixture', true)")
            secs = await fixture_security_ids(op)
            users = list(USERS)
            orgs = FIXTURE_ORGS
            snaps = [r["id"] for r in await op.fetch(
                "SELECT id FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id = ANY($1::uuid[])",
                secs)]
            by_org = "org_id = ANY($1::uuid[])"
            deletes = {
                "portfolio.replica_viability_reports": (by_org, [orgs]),
                "portfolio.replica_model_specs": (by_org, [orgs]),
                "portfolio.replica_registry_keys": (by_org, [orgs]),
                "portfolio.model_events": (by_org, [orgs]),
                "portfolio.model_version_positions": (by_org, [orgs]),
                "portfolio.model_sleeves": (by_org, [orgs]),
                "portfolio.model_versions": (by_org, [orgs]),
                "portfolio.models": (by_org, [orgs]),
                "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
                "public.domain_event_deliveries": (by_org, [orgs]),
                "public.domain_events": (by_org, [orgs]),
                "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
                "public.workflow_run_steps": (by_org, [orgs]),
                "public.workflow_runs": (by_org, [orgs]),
                "public.workflow_steps": (by_org, [orgs]),
                "public.workflow_versions": (by_org, [orgs]),
                "public.workflow_definitions": (by_org, [orgs]),
                "portfolio.replica_terms_actions": (by_org, [orgs]),
                "public.document_field_corrections": (by_org, [orgs]),
                "portfolio.replica_terms_versions": (by_org, [orgs]),
                "portfolio.replica_securities": (by_org, [orgs]),
                "public.fee_schedule_tiers": (by_org, [orgs]),
                "public.fee_schedules": (by_org, [orgs]),
                "public.org_settings": (by_org, [orgs]),
                "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.replica_holdings_rows": ("snapshot_id = ANY($1::uuid[])", [snaps]),
                "portfolio.replica_holdings_snapshots": ("id = ANY($1::uuid[])", [snaps]),
                "portfolio.securities_global_relationships": (
                    "from_global_security_id = ANY($1::uuid[]) OR to_global_security_id = ANY($1::uuid[]) "
                    "OR proposed_global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
            }
            for table in await fk_order(op, list(deletes)):
                where, args = deletes[table]
                status = await op.execute(f"DELETE FROM {table} WHERE {where}", *args)
                n = int(status.rsplit(" ", 1)[-1])
                if n:
                    log.append(f"{table}: {n}")
            await delete_tree(op, "public.users", "id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                              [users, f"%{TAG}%"], log=log)
            await delete_tree(op, "public.roles", "org_id = ANY($1::uuid[])", [orgs], log=log)
            await delete_tree(op, "public.organizations", "id = ANY($1::uuid[])", [orgs], log=log)
    finally:
        await op.close()
    return log


async def leftovers(conn) -> int:
    n = 0
    orgs = FIXTURE_ORGS
    users = list(USERS)
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [users, f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [orgs]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])", [list(SEC_NAMES)]),
        ("SELECT count(*) FROM portfolio.securities_global_prices WHERE source = $1", [TAG]),
        ("SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE file_sha256 = ANY($1::text[])",
         [S.get("shas", [])]),
        ("SELECT count(*) FROM audit_log WHERE user_id = ANY($1::uuid[])", [users]),
        *[(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", [orgs]) for t in (
            "portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
            "portfolio.model_events", "portfolio.replica_model_specs", "portfolio.replica_registry_keys",
            "portfolio.replica_viability_reports", "portfolio.replica_securities", "portfolio.replica_terms_versions",
            "public.fee_schedules", "public.fee_schedule_tiers", "public.workflow_definitions", "public.workflow_runs",
            "public.workflow_run_steps", "public.member_todos", "public.domain_events", "public.audit_log")],
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=240.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep4"}}
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
    import main
    from services import storage
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def put(key, data, content_type=None, bucket=None):
        STORE[key] = bytes(data)
        return key

    storage.upload_bytes = put
    storage.download_bytes = lambda key, bucket=None: STORE[key]

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep4: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    """RLS contextvars for a direct service call (what the middleware sets)."""

    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        from services.database import set_rls_context

        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        from services.database import reset_rls_context

        reset_rls_context(self.tokens)
        return False


# ═══ Catalog constraints (read BEFORE seeding; the seed must satisfy them) ═══
async def read_constraints(conn) -> dict:
    out: dict = {}
    for t in TOUCHED_FOR_CONSTRAINTS:
        rows = await platform(conn, "SELECT conname, contype::text AS t, pg_get_constraintdef(oid) AS d FROM "
                                    "pg_constraint WHERE conrelid = $1::regclass AND contype IN ('c', 'u', 'f')", t)
        idx = await platform(conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname || '.' || tablename "
                                   "= $1 AND indexdef LIKE 'CREATE UNIQUE%'", t)
        out[t] = {"checks": {r["conname"]: r["d"] for r in rows if r["t"] == "c"},
                  "fks": {r["conname"]: r["d"] for r in rows if r["t"] == "f"},
                  "uniques": {r["indexname"]: r["indexdef"] for r in idx}}
    return out


def allowed(cons: dict, table: str, column: str) -> list[str]:
    for d in cons[table]["checks"].values():
        if re.search(rf"\(\(?{column} = ANY", d) or re.search(rf"\b{column}\b = ANY \(ARRAY", d):
            return re.findall(r"'([^']*)'::text", d)
    return []


# ═══ Seed ═══════════════════════════════════════════════════════════════════
def term_values(**fields) -> dict:
    return {k: {"value": v, "fund_value": v, "origin": "extracted", "confidence": "data", "needs_review": False}
            for k, v in fields.items()}


F_TERMS = dict(expense_ratio_net_pct=0.2, expense_ratio_gross_pct=0.2, rebalance_frequency="quarterly",
               weighting_method="float_adjusted_market_cap", index_name="ZZRF Verify Index")
# fund F (clean): five stocks + 1% cash. Weights of S1, S2, S3 (non-cash sums to exactly 1).
F_S1 = {"A": "0.40", "B": "0.30", "C": "0.20", "D": "0.004", "E": "0.096"}
F_S2 = {"A": "0.35", "B": "0.35", "C": "0.20", "D": "0.004", "E": "0.096"}
F_S3 = {"A": "0.351", "B": "0.349", "C": "0.20", "D": "0.004", "E": "0.096"}


async def insert_snapshot(conn, *, fund: str, as_of: date, source_key: str, status: str, rows: list[dict],
                          label: str, previous=None, diff: dict | None = None) -> UUID:
    """A platform holdings snapshot + rows on a FIXTURE fund (super-admin RLS, as rep1's ingest writes them)."""
    h = sha(label)
    S.setdefault("shas", []).append(h)
    total = sum((D(r["weight"]) for r in rows if r["weight"] is not None), Decimal(0))
    ex_cash = sum((D(r["weight"]) for r in rows if r["weight"] is not None and r["asset_type"] != "cash"), Decimal(0))
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        sid = await conn.fetchval(
            """INSERT INTO portfolio.replica_holdings_snapshots (fund_global_security_id, as_of, source_key, file_sha256,
                   row_count, total_weight, total_weight_ex_cash, status, validation, previous_snapshot_id, diff,
                   diff_exceeds_threshold)
               VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, '{}'::jsonb, $9, $10::jsonb, false) RETURNING id""",
            S[f"sec_{fund}"], as_of, source_key, h, len(rows), total, ex_cash, status, previous,
            json.dumps(diff or {"first_snapshot": True, "added": [], "removed": [], "moves": [], "turnover": "0"}))
        for i, r in enumerate(rows, start=1):
            await conn.execute(
                """INSERT INTO portfolio.replica_holdings_rows (snapshot_id, row_num, raw_name, raw_ticker, raw_cusip, raw,
                       asset_type, constituent_global_security_id, resolution_state, resolved_via, relationship_id,
                       weight, shares)
                   VALUES ($1, $2, $3, $4, $5, '{}'::jsonb, $6, $7::uuid, $8, $9, $10::uuid, $11, $12)""",
                sid, i, r["name"], r.get("ticker"), r.get("cusip"), r["asset_type"], r.get("gsid"), r["state"],
                "cusip" if r["state"] == "resolved" else None, r.get("rel"), D(r["weight"]), D(r.get("shares")))
    return sid


def stock_rows(weights: dict[str, str], cash: str | None = "0.01") -> list[dict]:
    rows = [{"name": f"REP4 VERIFY {k}", "ticker": TICKER[k], "cusip": CUSIP[k], "asset_type": "equity",
             "gsid": S[f"sec_{k}"], "state": "resolved", "weight": w, "shares": "1000"} for k, w in weights.items()]
    if cash is not None:
        rows.append({"name": "US DOLLAR", "asset_type": "cash", "state": "not_applicable", "weight": cash})
    return rows


async def insert_entry(conn, *, org, key: str, fund: str, cls: str, source: str, created_by) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3::uuid, $4, $5, $6, 'active', $7, "
            "$8)", ENTRY[key], org, S[f"sec_{fund}"], cls, TICKER[fund], source, f"Holdings of {TICKER[fund]}",
            created_by)


async def insert_terms(conn, *, org, key: str, version_no: int, values: dict, approved: bool, maker,
                       checker=None) -> UUID:
    vid = uuid.uuid4()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "INSERT INTO portfolio.replica_terms_versions (id, org_id, replica_security_id, version_no, status, "
            "term_values, maker_ids, created_by, checker_id, approved_at) VALUES ($1, $2, $3, $4, $5, $6::jsonb, "
            "$7::uuid[], $8, $9, $10)", vid, org, ENTRY[key], version_no, "approved" if approved else "draft",
            json.dumps(values), [maker], maker, checker if approved else None,
            datetime.now(timezone.utc) if approved else None)
    return vid


async def seed(conn) -> None:
    from services import rbac
    from services import securities_global as SG

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"REP4 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        rid = await rbac.ensure_role(conn, ORG_A, "rep4_viewer", "REP4 verify — view_replication + view_models only")
        for p in ("view_replication", "view_models"):
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid,
                               perm_ids[p])
        roles["rep4_viewer"] = rid
        roles["rep4_none"] = await rbac.ensure_role(conn, ORG_A, "rep4_none", "REP4 verify — no permissions")
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for k, name in zip(STOCKS + "FHGKL", SEC_NAMES):
            sid = await SG.create_security(conn, name=name, security_type="fund" if k in "FHGKL" else "equity",
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=CUSIP[k],
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=TICKER[k],
                                    is_super_admin=True)
            S[f"sec_{k}"] = str(sid)
        rels = await SG.add_relationships_bulk(conn, from_global_security_id=S["sec_H"], raw_texts=[UNMATCHED_TEXT],
                                               relationship_type="constituent_of", is_super_admin=True)
        S["rel_U"] = rels[UNMATCHED_TEXT]
    users = await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS))
    check(len(users) == len(USERS), "fixture users created with the token's auth0_sub, is_test = true",
          f"{len(users)}/{len(USERS)}")
    # registry entries
    await insert_entry(conn, org=ORG_A, key="F", fund="F", cls="index_etf", source="ssga_spdr", created_by=U_ADMIN1)
    await insert_entry(conn, org=ORG_A, key="H", fund="H", cls="index_etf", source="sec_nport", created_by=U_ADMIN1)
    await insert_entry(conn, org=ORG_A, key="G", fund="G", cls="active_etf", source="ssga_spdr", created_by=U_ADMIN1)
    await insert_entry(conn, org=ORG_A, key="K", fund="K", cls="index_etf", source="ssga_spdr", created_by=U_ADMIN1)
    await insert_entry(conn, org=ORG_A, key="L", fund="L", cls="index_etf", source="ssga_spdr", created_by=U_ADMIN1)
    # terms: F, H, G, L approved; K only a draft
    S["terms_F1"] = await insert_terms(conn, org=ORG_A, key="F", version_no=1, values=term_values(**F_TERMS),
                                       approved=True, maker=U_ADMIN1, checker=U_ADMIN2)
    S["terms_H1"] = await insert_terms(conn, org=ORG_A, key="H", version_no=1,
                                       values=term_values(rebalance_frequency="quarterly"), approved=True,
                                       maker=U_ADMIN1, checker=U_ADMIN2)
    for k in ("G", "L"):
        await insert_terms(conn, org=ORG_A, key=k, version_no=1, values=term_values(**F_TERMS), approved=True,
                           maker=U_ADMIN1, checker=U_ADMIN2)
    await insert_terms(conn, org=ORG_A, key="K", version_no=1, values=term_values(**F_TERMS), approved=False,
                       maker=U_ADMIN1)
    # snapshots
    S["snap_F1"] = await insert_snapshot(conn, fund="F", as_of=TODAY - timedelta(days=60), source_key="ssga_spdr",
                                         status="live", rows=stock_rows(F_S1), label="F1")
    S["snap_H1"] = await insert_snapshot(conn, fund="H", as_of=TODAY - timedelta(days=60), source_key="sec_nport",
                                         status="live", label="H1", rows=[
            {"name": "REP4 VERIFY A", "ticker": TICKER["A"], "cusip": CUSIP["A"], "asset_type": "equity",
             "gsid": S["sec_A"], "state": "resolved", "weight": "0.5"},
            {"name": "REP4 VERIFY B", "ticker": TICKER["B"], "cusip": CUSIP["B"], "asset_type": "equity",
             "gsid": S["sec_B"], "state": "resolved", "weight": "0.3"},
            {"name": "ZZRF UNMATCHED HOLDING", "ticker": "ZZRFU", "cusip": "ZZRF00099", "asset_type": "equity",
             "state": "unresolved", "rel": S["rel_U"], "weight": "0.1"},
            {"name": "E-MINI FUTURE", "asset_type": "future", "state": "not_applicable", "weight": "0.1"}])
    S["snap_G1"] = await insert_snapshot(conn, fund="G", as_of=TODAY - timedelta(days=2), source_key="ssga_spdr",
                                         status="live", rows=stock_rows(F_S1), label="G1")
    S["snap_K1"] = await insert_snapshot(conn, fund="K", as_of=TODAY - timedelta(days=2), source_key="ssga_spdr",
                                         status="live", rows=stock_rows(F_S1), label="K1")
    S["snap_L1"] = await insert_snapshot(conn, fund="L", as_of=TODAY - timedelta(days=2), source_key="ssga_spdr",
                                         status="needs_review", rows=stock_rows(F_S1), label="L1")


# ═══ Small readers ══════════════════════════════════════════════════════════
async def positions(conn, org, version_id) -> list[dict]:
    rows = await scoped(conn, org, "SELECT row_num, global_security_id, raw_ticker, target_weight, resolution_state "
                                   "FROM portfolio.model_version_positions WHERE version_id = $1::uuid AND valid_to IS "
                                   "NULL AND system_to IS NULL ORDER BY row_num", str(version_id))
    return [dict(r) for r in rows]


def by_security(pos: list[dict]) -> dict:
    out = {}
    for p in pos:
        k = str(p["global_security_id"]) if p["global_security_id"] else (
            "cash" if p["resolution_state"] == "cash" else f"unresolved:{p['raw_ticker']}")
        out[k] = D(p["target_weight"])
    return out


async def model_count(conn, org) -> int:
    return int(await scoped(conn, org, "SELECT count(*) FROM portfolio.models WHERE org_id = $1 AND valid_to IS NULL "
                                       "AND system_to IS NULL", org, fetch="fetchval"))


async def build(uid, entry_key: str, **body):
    return await api(uid, "POST", f"/api/v1/replication/registry/{ENTRY[entry_key]}/models", body)


async def viability(uid, entry_key: str, **body):
    return await api(uid, "POST", f"/api/v1/replication/registry/{ENTRY[entry_key]}/viability", body)


def answer(report: dict, key: str) -> dict:
    return next((a for a in report.get("answers", []) if a["key"] == key), {})


def line(report: dict, key: str) -> dict:
    return next((x for x in report.get("cost_lines", []) if x["key"] == key), {})


async def tasks_for(conn, org, subject_id, kind=None) -> list:
    rows = await scoped(conn, org, "SELECT rs.id, rs.status, r.context->'human_task' AS ht, ws.assigned_permission "
                                   "FROM workflow_run_steps rs JOIN workflow_runs r ON r.id = rs.workflow_run_id "
                                   "JOIN workflow_steps ws ON ws.id = rs.workflow_step_id WHERE "
                                   "r.context->'human_task'->>'subject_id' = $1 ORDER BY r.started_at", str(subject_id))
    out = []
    for r in rows:
        ht = r["ht"] if isinstance(r["ht"], dict) else json.loads(r["ht"])
        if kind is None or ht.get("kind") == kind:
            out.append({"id": r["id"], "status": r["status"], "ht": ht, "perm": r["assigned_permission"]})
    return out


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e — signatures, price availability and the monitoring hook")
    from services import custodian_capabilities as CC
    from services import fee_calc
    from services import human_tasks as HT
    from services import models as M
    from services import replica_holdings as H
    from services import replica_models as RM
    from services import replica_schedule as RS
    from services import replica_terms as T

    find("1a signatures", "models: create_model(conn,*,org_id,actor_id,name,source,…) REFUSES fund_replica → rep4 adds "
         "create_fund_model(conn,*,org_id,actor_id,name,replica_security_id,disclosure,description); "
         "_create_version(conn,*,org_id,model,origin,maker_ids,created_by,positions,sleeves,origin_ref,upload_id,notes,"
         "tradability); live_approved/open_version/positions_of/look_through; _submit_system(pool,*,org_id,model,"
         "version,why,extra_payload) opens model_version_review with NO maker (system_open) — the composite/subscription "
         "path monitoring reuses. replica_terms: load_entry(conn,org_id,entry_id), live_approved(conn,entry_id), "
         "display_name_for/disclosure_for(ticker). replica_holdings: latest_snapshot(conn,fund_id,source_key,*,"
         "live_only), snapshot_rows(conn,snapshot_id). human_tasks: open_task(pool,*,org_id,kind,subject_type,"
         "subject_id,maker_id,eligible_permission,title,payload,action_path,actor_kind). custodian_capabilities: "
         "capability(conn,code,key) → Capability; available() — 'unknown' is NOT available")
    sig = inspect.signature
    check("extra_payload" in sig(M._submit_system).parameters and hasattr(M, "create_fund_model")
          and "live_only" in sig(H.latest_snapshot).parameters
          and list(sig(CC.capability).parameters)[:3] == ["conn", "custodian_code", "key"]
          and list(sig(fee_calc.apply_tiers).parameters) == ["billable", "tiers", "method"]
          and {"actor_kind", "maker_id", "payload"} <= set(sig(HT.open_task).parameters)
          and HT.kinds().get(RM.TERMS_REVIEW_KIND) is not None and HT.kinds()[RM.TERMS_REVIEW_KIND].system_open
          and T.display_name_for("qqq") == "Holdings of QQQ",
          "the signatures this sprint calls exist as reported (and the terms-review kind is system-opened)")
    real = await platform(conn, """
        SELECT count(DISTINCT r.constituent_global_security_id) AS constituents,
               count(DISTINCT p.global_security_id) FILTER (WHERE p.price_date >= current_date - 5) AS fresh,
               count(DISTINCT p.global_security_id) AS any_price
        FROM portfolio.replica_holdings_rows r JOIN portfolio.replica_holdings_snapshots s ON s.id = r.snapshot_id
        LEFT JOIN portfolio.securities_global_prices p ON p.global_security_id = r.constituent_global_security_id
          AND p.valid_to IS NULL AND p.system_to IS NULL AND p.price_type = 'close'
        WHERE s.status = 'live' AND s.system_to IS NULL AND r.constituent_global_security_id IS NOT NULL
          AND NOT (s.file_sha256 = ANY($1::text[]))""", S.get("shas", []), fetch="fetchrow")
    allp = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE source IS DISTINCT FROM $1",
                          TAG, fetch="fetchval")
    find("1b prices", f"at sprint time (2026-10-23) securities_global_prices held 0 rows at all; now: {allp} real price "
         f"rows; real live-snapshot constituents {real['constituents']}, with any close {real['any_price']}, fresh "
         f"(<= 5 days) {real['fresh']}. Holdings files: SSGA = weight + shares; N-PORT = valUSD (market value) + balance "
         "— market value / shares is a price-as-of-filing, NOT used (never estimate a price). COMPUTABLE TODAY: Q1 "
         "(class), Q2 (asset types, staleness), Q4 (capabilities), Q5 expense ratio + fee (when terms/schedule exist), "
         "trading cost (with >= 2 live snapshots). 'unknown — needs prices': Q3 for every real fund until a price feed "
         "(FMP not contracted)")
    check(True, "1b price availability measured on the live database", f"{allp} real price rows")
    sched = (API_DIR / "services" / "replica_schedule.py").read_text(encoding="utf-8")
    rtr = (API_DIR / "routers" / "replication.py").read_text(encoding="utf-8")
    terms_src = (API_DIR / "services" / "replica_terms.py").read_text(encoding="utf-8")
    find("1c monitoring hook", "rep1's refresh signals a new snapshot only by its RETURN VALUE (IngestResult.created / "
         "status / snapshot_id) — no event of its own, a member_todos review only over the diff threshold. Hook: "
         "replica_schedule.monitor_models(pool, res), called after every ingest in maybe_refresh_holdings AND in the "
         "manual refresh route → replica_models.monitor_snapshot; terms: replica_terms._apply_terms_decision (approve, "
         "with a prior version) → replica_models.monitor_terms")
    check("await monitor_models(pool, res" in sched and "RS.monitor_models(pool, res" in rtr
          and "RM.monitor_terms(" in terms_src and inspect.iscoroutinefunction(RS.monitor_models),
          "the snapshot hook is wired in both refresh paths and the terms hook in the approval")
    find("1d illustrative fee", "fee_calc.apply_tiers(billable, tiers[FeeTierInput], method) → (annual, slices, "
         "assumptions); tiers from public.fee_schedule_tiers of the model's default_fee_schedule_id (APPROVED, never "
         "mutated); method = tier_method or GRADUATED; nothing writes fee runs / invoices")
    src = (API_DIR / "services" / "replica_viability.py").read_text(encoding="utf-8")
    check("apply_tiers(account_size, tiers, method)" in src and "fee_runs" not in src and "invoice" not in src.lower(),
          "viability prices the strategy fee through apply_tiers and never writes a fee run or invoice")
    n_e = await platform(conn, "SELECT count(*) FROM portfolio.replica_securities WHERE NOT (org_id = ANY($1::uuid[]))",
                         FIXTURE_ORGS, fetch="fetchval")
    n_m = await platform(conn, "SELECT count(*) FROM portfolio.models WHERE NOT (org_id = ANY($1::uuid[]))",
                         FIXTURE_ORGS, fetch="fetchval")
    find("1e real data", f"real replica entries {n_e}, real models {n_m} (0 / 0 at sprint time) — never touched: "
         "count + checksum compared after teardown")


async def y_schema(conn):
    section("[Y] Schema landed: tables, constraints, partial uniques, RLS with a policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep4.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep4.py --verify: SCHEMA OK (catalog read back)",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    cons = S["cons"]
    check("replica_registry_keys_live_uq" in cons["portfolio.replica_registry_keys"]["uniques"]
          and "WHERE" in cons["portfolio.replica_registry_keys"]["uniques"]["replica_registry_keys_live_uq"]
          and "replica_model_specs_version_uq" in cons["portfolio.replica_model_specs"]["uniques"]
          and "model_events_holdings_monitor_uq" in cons["portfolio.model_events"]["uniques"],
          "partial unique indexes read from the catalog before seeding")
    try:
        await scoped(conn, ORG_A, "INSERT INTO portfolio.replica_viability_reports (org_id, replica_security_id, "
                                  "custodian_code, account_size, method, answers, overall, computed_by) VALUES ($1, $2, "
                                  "'altruist', 1, 'full', '[]'::jsonb, 'yes', $3)", ORG_A, ENTRY["F"], U_ADMIN1,
                     fetch="execute")
        check(False, "the database refuses a viability report without exactly five answers")
    except Exception as exc:  # noqa: BLE001
        check("replica_viability_reports_answers_chk" in str(exc),
              "the database refuses a viability report without exactly five answers", str(exc)[:100])


async def y_build(conn):
    section("[Y] Build: exact weights for full / top_n / min_weight / cash; departures; 'Holdings of'; draft via rep3; "
            "maker-checker on the identical request")
    from services import replica_terms as T

    n0 = await model_count(conn, ORG_A)
    code, out = await build(U_ADMIN1, "F", custodian_code="altruist", method="full", cash_target_pct="2")
    check(code == 201 and out.get("joined") is False, "build (fund F, altruist, full, 2% cash) → 201 new model",
          f"{code} {str(out)[:200]}")
    if code != 201:
        return
    m1, v1 = out["model"]["id"], out["version"]["id"]
    S.update(m1=m1, v1=v1)
    snap_rows = await platform(conn, "SELECT constituent_global_security_id AS g, weight, row_num FROM "
                                     "portfolio.replica_holdings_rows WHERE snapshot_id = $1 AND asset_type = 'equity' "
                                     "AND resolution_state = 'resolved' ORDER BY row_num", S["snap_F1"])
    want = expected_alloc([(str(r["g"]), D(r["weight"])) for r in snap_rows], Decimal("0.98"))
    want["cash"] = Decimal("0.02")
    got = by_security(await positions(conn, ORG_A, v1))
    check(bool(want) and got == want and sum(got.values()) == Decimal(1),
          "full: positions = the snapshot's resolved equity weights normalised to 1 − 2% (exact Decimal) + 2% cash",
          f"{got}")
    check(got[S["sec_A"]] == Decimal("0.392") and got[S["sec_D"]] == Decimal("0.00392"),
          "full: spot values exact (A 0.4 × 0.98 = 0.392; D 0.004 × 0.98 = 0.00392)")
    m = await scoped(conn, ORG_A, "SELECT * FROM portfolio.models WHERE id = $1::uuid AND system_to IS NULL", m1,
                     fetch="fetchrow")
    v = await scoped(conn, ORG_A, "SELECT * FROM portfolio.model_versions WHERE id = $1::uuid AND system_to IS NULL", v1,
                     fetch="fetchrow")
    check(m["name"] == f"Holdings of {TICKER['F']}" and m["source"] == "fund_replica"
          and m["replica_security_id"] == ENTRY["F"] and m["disclosure"] == T.disclosure_for(TICKER["F"]),
          "named 'Holdings of ZZRFF', source fund_replica, replica_security_id set, rep1's disclosure stored",
          f"{m['name']} {m['source']}")
    check(v["status"] == "draft" and v["origin"] == "fund_build" and list(v["maker_ids"]) == [U_ADMIN1]
          and v["created_by"] == U_ADMIN1, "a DRAFT through rep3 (origin fund_build, maker = the builder)",
          f"{v['status']} {v['origin']}")
    spec = await scoped(conn, ORG_A, "SELECT * FROM portfolio.replica_model_specs WHERE version_id = $1::uuid", v1,
                        fetch="fetchrow")
    deps = json.loads(spec["departures"]) if spec else []
    check(spec is not None and spec["method"] == "full" and spec["top_n"] is None and D(spec["cash_target"]) ==
          Decimal("0.02") and spec["unresolved_policy"] == "block" and spec["custodian_code"] == "altruist"
          and spec["snapshot_id"] == S["snap_F1"] and spec["terms_version_id"] == S["terms_F1"]
          and spec["rebalance_frequency"] == "quarterly" and spec["rebalance_origin"] == "terms"
          and spec["origin"] == "fund_build" and [d["kind"] for d in deps] == ["cash_target"],
          "the spec is stored with the version (snapshot, terms, custodian, method, cash, rebalance from terms)",
          f"{dict(spec) if spec else None}")
    ev = await scoped(conn, ORG_A, "SELECT detail FROM portfolio.model_events WHERE version_id = $1::uuid AND action = "
                                   "'create_draft'", v1, fetch="fetchval")
    check(ev is not None and json.loads(ev)["origin"] == "fund_build", "logged as a create_draft model event")
    # top_n
    code, out = await build(U_ADMIN1, "F", custodian_code="altruist", method="top_n", top_n=3)
    if check(code == 201, "build top_n 3 → 201", f"{code} {str(out)[:200]}"):
        S["m2"] = out["model"]["id"]
        got = by_security(await positions(conn, ORG_A, out["version"]["id"]))
        check(got == {S["sec_A"]: Decimal("0.4444444445"), S["sec_B"]: Decimal("0.3333333333"),
                      S["sec_C"]: Decimal("0.2222222222")} and sum(got.values()) == Decimal(1),
              "top_n: the 3 largest, renormalised exactly (largest remainder at 1e-10; sum exactly 1)", f"{got}")
        dropped = {d["label"]: d["reason"] for d in out["spec"]["dropped"]}
        check(out["model"]["name"] == f"Holdings of {TICKER['F']} — Top 3"
              and dropped.get(TICKER["D"]) == "below_top_n" and dropped.get(TICKER["E"]) == "below_top_n"
              and "top_n" in [d["kind"] for d in out["spec"]["departures"]],
              "variant named 'Holdings of ZZRFF — Top 3'; D and E dropped (below_top_n); top_n departure", f"{dropped}")
    # min_weight (and a different custodian → a different key)
    code, out = await build(U_ADMIN1, "F", custodian_code="manual", method="full", min_weight_pct="0.5")
    if check(code == 201, "build manual full, min weight 0.5% → 201", f"{code} {str(out)[:200]}"):
        S["m3"] = out["model"]["id"]
        rest = [(str(r["g"]), D(r["weight"])) for r in snap_rows if D(r["weight"]) >= Decimal("0.005")]
        want = expected_alloc(rest, Decimal(1))
        got = by_security(await positions(conn, ORG_A, out["version"]["id"]))
        dep = next((d for d in out["spec"]["departures"] if d["kind"] == "min_weight"), None)
        check(len(rest) == 4 and got == want and sum(got.values()) == Decimal(1) and S["sec_D"] not in got,
              "min_weight: D (0.4%) dropped and its weight spread pro rata — exact, summing to 1", f"{got}")
        check(dep is not None and dep["detail"]["rows"] == [4] and Decimal(dep["detail"]["weight"]) == Decimal("0.004"),
              "the min_weight drop is a RECORDED departure (row, weight, plain words)", f"{dep}")
    # maker-checker on the identical approve request
    c1, o1 = await api(U_ADMIN1, "POST", f"/api/v1/models/{m1}/versions/{v1}/submit", {})
    c2, o2 = await api(U_ADMIN1, "POST", f"/api/v1/models/{m1}/versions/{v1}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status FROM portfolio.model_versions WHERE id = $1::uuid AND system_to IS NULL",
                      v1, fetch="fetchval")
    c3, o3 = await api(U_ADMIN2, "POST", f"/api/v1/models/{m1}/versions/{v1}/approve", {})
    v = await scoped(conn, ORG_A, "SELECT status, checker_id FROM portfolio.model_versions WHERE id = $1::uuid AND "
                                  "system_to IS NULL", v1, fetch="fetchrow")
    check(c1 == 200 and c2 == 403 and "maker" in str(o2.get("detail")).lower() and st == "submitted",
          "the maker's approve is refused (403, maker-checker) and the version stays submitted", f"{c1} {c2}")
    check(c3 == 200 and v["status"] == "approved" and v["checker_id"] == U_ADMIN2,
          "the identical request from a checker approves it (rep3's human task)", f"{c3} {dict(v)}")
    check(await model_count(conn, ORG_A) == n0 + 3, "three models built, no more", f"{n0} → {await model_count(conn, ORG_A)}")


async def y_registry(conn):
    section("[Y] Registry find-or-build: no duplicates; a different key → a new model")
    if not S.get("m1"):
        check(False, "registry needs the first build")
        return
    n0 = await model_count(conn, ORG_A)
    k0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.replica_registry_keys", fetch="fetchval"))
    code, out = await build(U_ADMIN1, "F", custodian_code="altruist", method="full", cash_target_pct="2")
    k1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.replica_registry_keys", fetch="fetchval"))
    check(code == 200 and out.get("joined") is True and out["model"]["id"] == S["m1"]
          and out["live_version"]["version_no"] == 1 and out["accounts_using"] == 0
          and await model_count(conn, ORG_A) == n0 and k1 == k0,
          "a second build with the same key returns the existing model (200, joined) — no duplicate model or key",
          f"{code} {out.get('joined')}")
    code, out = await build(U_ADMIN1, "F", custodian_code="altruist", method="full", cash_target_pct="5")
    check(code == 200 and out["model"]["id"] == S["m1"] and out["requested_differs_in"] == ["cash_target"],
          "a different cash target on the same key still joins — and says what differs", f"{out.get('requested_differs_in')}")
    ev = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_events WHERE model_id = $1::uuid AND action = "
                                   "'fund_build_joined'", S["m1"], fetch="fetchval")
    check(int(ev) == 2, "each join is logged (fund_build_joined)", f"{ev}")
    c, f = await api(U_VIEWER, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/models/find?custodian_code=altruist"
                                      "&method=full")
    c2, f2 = await api(U_VIEWER, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/models/find?custodian_code=manual"
                                        "&method=top_n&top_n=3")
    check(c == 200 and f["model"]["model"]["id"] == S["m1"] and c2 == 200 and f2["model"] is None,
          "find: the key's live model (view_replication); an unbuilt key → none", f"{c} {c2}")
    # a bare fund model with NO key, so only the (org, entry, custodian, method, top N) key can collide
    bare = await scoped(conn, ORG_A, "INSERT INTO portfolio.models (org_id, name, source, created_by) VALUES ($1, "
                                     "'Holdings of ZZRFF keytest', 'fund_replica', $2) RETURNING id", ORG_A, U_ADMIN1,
                        fetch="fetchval")
    try:
        await scoped(conn, ORG_A, "INSERT INTO portfolio.replica_registry_keys (org_id, replica_security_id, "
                                  "custodian_code, method, model_id, created_by) VALUES ($1, $2, 'altruist', 'full', $3, "
                                  "$4)", ORG_A, ENTRY["F"], bare, U_ADMIN1, fetch="execute")
        check(False, "the database refuses a second live model for one key")
    except Exception as exc:  # noqa: BLE001
        check("replica_registry_keys_live_uq" in str(exc), "the database refuses a second live model for one key",
              str(exc)[:100])


async def y_preconditions(conn):
    section("[Y] Preconditions and unresolved policies enforced")
    n0 = await model_count(conn, ORG_A)
    c1, o1 = await build(U_ADMIN1, "K", custodian_code="altruist")
    c2, o2 = await build(U_ADMIN1, "L", custodian_code="altruist")
    check(c1 == 409 and detail_of(o1).get("missing") == ["no approved terms version"],
          "no approved terms version → 409 naming it", f"{c1} {o1}")
    check(c2 == 409 and detail_of(o2).get("missing") == ["no live holdings snapshot"],
          "only a needs_review snapshot → 409 'no live holdings snapshot' (a needs_review snapshot is never used)",
          f"{c2} {o2}")
    c3, tab = await api(U_ADMIN1, "GET", f"/api/v1/replication/registry/{ENTRY['L']}/model")
    check(c3 == 200 and tab["sources"]["snapshot"] is None and tab["sources"]["missing"] == ["no live holdings snapshot"],
          "the Model tab shows no snapshot for that fund", f"{c3}")
    check(await model_count(conn, ORG_A) == n0, "nothing was created by the refused builds")
    # unresolved: block
    code, out = await build(U_ADMIN1, "H", custodian_code="altruist")
    if check(code == 201, "fund H (an unmatched holding + 10% futures), block → draft", f"{code} {str(out)[:200]}"):
        S["mh_block"] = out["model"]["id"]
        vid = out["version"]["id"]
        pos = await positions(conn, ORG_A, vid)
        got = by_security(pos)
        check(got == {S["sec_A"]: Decimal("0.5555555556"), S["sec_B"]: Decimal("0.3333333333"),
                      "unresolved:ZZRFU": Decimal("0.1111111111")} and sum(got.values()) == Decimal(1),
              "block: the unmatched holding stays as an UNRESOLVED position; weights exact", f"{got}")
        kinds = [d["kind"] for d in out["spec"]["departures"]]
        fut = [d for d in out["spec"]["dropped"] if d["reason"] == "not_equity"]
        check("non_equity_excluded" in kinds and len(fut) == 1, "futures dropped as not_equity, recorded as a departure",
              f"{kinds}")
        await api(U_ADMIN1, "POST", f"/api/v1/models/{out['model']['id']}/versions/{vid}/submit", {})
        c4, o4 = await api(U_ADMIN2, "POST", f"/api/v1/models/{out['model']['id']}/versions/{vid}/approve", {})
        st = await scoped(conn, ORG_A, "SELECT status FROM portfolio.model_versions WHERE id = $1::uuid AND system_to IS "
                                       "NULL", vid, fetch="fetchval")
        check(c4 == 409 and "unresolved" in str(o4.get("detail")) and st == "submitted",
              "block: the draft cannot be approved while a position is unresolved (rep3's rule, 409)", f"{c4} {st}")
    code, out = await build(U_ADMIN1, "H", custodian_code="manual", unresolved_policy="exclude_and_reweight")
    if check(code == 201, "fund H, exclude_and_reweight → draft", f"{code} {str(out)[:200]}"):
        got = by_security(await positions(conn, ORG_A, out["version"]["id"]))
        dep = next((d for d in out["spec"]["departures"] if d["kind"] == "exclude_unresolved"), None)
        check(got == {S["sec_A"]: Decimal("0.625"), S["sec_B"]: Decimal("0.375")}
              and dep is not None and Decimal(dep["detail"]["weight"]) == Decimal("0.1"),
              "exclude: the unmatched holding is left out, weights renormalised exactly, departure recorded", f"{got}")
    c5, o5 = await build(U_ADMIN1, "F", custodian_code="altruist", rebalance_frequency="monthly")
    check(c5 == 422 or (c5 == 200 and o5.get("joined") is True),
          "a rebalance change with no reason is refused before anything (422) — or the key already exists",
          f"{c5}")


async def set_prices(conn, prices: dict[str, str], day: date) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for k, p in prices.items():
            await conn.execute("INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                               "price_type, source) VALUES ($1::uuid, $2, $3, 'close', $4)", S[f"sec_{k}"], day, D(p),
                               TAG)


PRICES = {"A": "100", "B": "50", "C": "200", "D": "10", "E": "25"}


async def y_viability(conn):
    section("[Y] Viability: each question's yes / no / unknown with reasons; unknown when data is missing; "
            "exact cost numbers; no tax number")
    from services import replica_viability as RV

    c, r = await viability(U_VIEWER, "F", custodian_code="altruist")
    if not check(c == 201 and len(r.get("answers", [])) == 5, "compute (view_replication) → 201 with five answers",
                 f"{c} {str(r)[:200]}"):
        return
    S["report_first"] = r
    check(D(r["account_size"]) == Decimal("250000.00") and r.get("account_size_origin") == "default",
          "the account size defaults to the org setting's stated default ($250,000)", f"{r['account_size']}")
    check(answer(r, "rules_based")["status"] == "yes", "Q1 index_etf → yes")
    c, rg = await viability(U_VIEWER, "G", custodian_code="altruist")
    check(c == 201 and answer(rg, "rules_based")["status"] == "no" and "rules-based" in answer(rg, "rules_based")["reason"],
          "Q1 a non-index class (active_etf) → no, with the spec's reason", f"{answer(rg, 'rules_based')}")
    check(answer(r, "rebuildable")["status"] == "yes", "Q2 fund F (all stocks) → yes")
    c, rh = await viability(U_VIEWER, "H", custodian_code="altruist")
    a2 = answer(rh, "rebuildable")
    check(c == 201 and a2["status"] == "no" and Decimal(a2["inputs"]["non_equity_share"]) == Decimal("0.1")
          and "core-only" in a2["reason"],
          "Q2 a futures-heavy snapshot (10% > 5%) → no, with the core-only note", f"{a2.get('reason')}")
    check(a2["inputs"]["nport"] is True and a2["inputs"]["age_days"] == 60 and a2["inputs"]["nport_stale"] is True
          and "N-PORT" in a2["reason"], "Q2 N-PORT staleness shown (as-of, 60 days) and flagged (> 45)",
          f"{a2['inputs'].get('age_days')}")
    a3 = answer(r, "account_size")
    check(a3["status"] == "unknown" and "needs prices for 5 of 5 constituents" in a3["reason"]
          and len(a3["missing"]) == 5 and all("no close price" in m for m in a3["missing"]),
          "Q3 no prices at all → 'unknown — needs prices for 5 of 5 constituents', each named", f"{a3.get('reason')}")
    await set_prices(conn, PRICES, TODAY - timedelta(days=10))
    c, r = await viability(U_VIEWER, "F", custodian_code="altruist")
    a3 = answer(r, "account_size")
    check(a3["status"] == "unknown" and "5 of 5" in a3["reason"] and all("older than 5 days" in m for m in a3["missing"]),
          "Q3 stale prices (10 days) → still unknown — a stale price is never used", f"{a3.get('missing', [])[:1]}")
    await set_prices(conn, {k: v for k, v in PRICES.items() if k != "E"}, TODAY)
    c, r = await viability(U_VIEWER, "F", custodian_code="altruist")
    a3 = answer(r, "account_size")
    check(a3["status"] == "unknown" and "needs prices for 1 of 5" in a3["reason"]
          and any(TICKER["E"] in m for m in a3["missing"]),
          "Q3 one fresh price missing → 'needs prices for 1 of 5' (never estimated)", f"{a3.get('reason')}")
    await set_prices(conn, {"E": PRICES["E"]}, TODAY)
    c, r = await viability(U_VIEWER, "F", custodian_code="altruist")
    a3 = answer(r, "account_size")
    # independent: whole shares, unit 1: hold = max(p/w) = 10/0.004 = 2500; track = Σp / 0.005 = 385/0.005 = 77000
    hold = max(Fraction(PRICES[k]) / Fraction(F_S1[k]) for k in STOCKS)
    track = sum(Fraction(PRICES[k]) for k in STOCKS) / Fraction("0.005")
    want = Decimal(max(hold, track).numerator) / Decimal(max(hold, track).denominator)
    m = a3.get("inputs", {}).get("minimum_account", {})
    check(a3["status"] == "yes" and Decimal(m.get("exact", "0")) == want == Decimal(77000)
          and m.get("minimum") == "77000.00" and m.get("binding") == "tracking_target"
          and a3["inputs"]["unit"] == "1",
          "Q3 every price fresh, whole shares (Altruist fractional 'unknown') → exact minimum $77,000.00 → yes at "
          "$250,000", f"{m}")
    c, r50 = await viability(U_VIEWER, "F", custodian_code="altruist", account_size="50000")
    check(c == 201 and answer(r50, "account_size")["status"] == "no" and "77000.00" in answer(r50, "account_size")["reason"],
          "Q3 the same fund at $50,000 → no, naming the minimum", f"{answer(r50, 'account_size').get('reason')}")
    # fractional: ONE direct call with the capability lookup stubbed to fractional_shares 'yes'
    from services import custodian_capabilities as CC
    from services.database import get_pool

    real_cap = CC.capability

    async def frac_yes(conn_, code, key):
        if key == "fractional_shares":
            return CC.Capability("yes", "verify_rep4 in-process stub", TODAY, "assumed")
        return await real_cap(conn_, code, key)

    pool = await get_pool()
    CC.capability = frac_yes
    try:
        with _ctx(ORG_A):
            rf = await RV.compute(pool, org_id=ORG_A, actor_id=U_ADMIN1, entry_id=ENTRY["F"], custodian_code="altruist")
    finally:
        CC.capability = real_cap
    mf = answer(rf, "account_size")["inputs"].get("minimum_account", {})
    check(Decimal(mf.get("exact", "0")) == Decimal(77) and mf.get("minimum") == "77.00"
          and answer(rf, "account_size")["inputs"]["unit"] == "0.001",
          "Q3 fractional (increment 0.001 share) → exact minimum $77.00", f"{mf}")
    a4 = answer(r, "custodian")
    check(a4["status"] == "yes" and "via execution instructions" in a4["reason"]
          and a4["inputs"]["equity_trading_api"]["value"] == "unknown"
          and any("equity_trading_api" in x for x in a4["missing"]),
          "Q4 uses capabilities: Altruist's trading API 'unknown' → 'yes, via execution instructions' (manual path), "
          "the missing evidence named", f"{a4.get('reason')}")
    a5 = answer(r, "cost")
    fee = line(r, "strategy_fee")
    check(a5["status"] == "unknown" and fee["status"] == "unknown" and fee["amount"] is None
          and any("default fee schedule" in x for x in a5["missing"])
          and line(r, "fund_expense")["amount"] == "500.00",
          "Q5 unknown while the model has no default fee schedule (fund expense still exact: 0.20% × $250,000 = $500.00)",
          f"{a5.get('reason')}")
    c, rm = await viability(U_VIEWER, "F", custodian_code="manual", method="top_n", top_n=2)
    check(c == 201 and line(rm, "strategy_fee")["status"] == "unknown"
          and "no model built yet" in line(rm, "strategy_fee")["reason"],
          "Q5 strategy fee unknown when no model exists for the key", f"{line(rm, 'strategy_fee')}")
    check(answer(rh, "cost")["status"] == "unknown"
          and any("net expense ratio" in x for x in answer(rh, "cost")["missing"])
          and line(rh, "fund_expense")["amount"] is None,
          "Q5 unknown when the approved terms carry no expense ratio", f"{answer(rh, 'cost').get('missing')}")
    for rep in (r, rg, rh, rm, r50, rf):
        t = line(rep, "tax_benefit")
        if not (t.get("amount") is None and t.get("status") == "not_estimated" and t.get("reason") == RV.TAX_LINE_TEXT
                and "tax engine (rep6)" in answer(rep, "cost").get("inputs", {}).get("tax", "")):
            check(False, "the tax line never shows a number", f"{t}")
            break
    else:
        check(True, "the tax line never shows a number (every report: amount null, 'estimated by the tax engine')")
    # the fee schedule — values read from the catalog's CHECKs first
    cons = S["cons"]
    vals = {c_: allowed(cons, "public.fee_schedules", c_) for c_ in ("product_type", "rate_type", "billing_frequency",
                                                                     "billing_timing", "valuation_method", "status",
                                                                     "tier_method")}
    pick = {"product_type": "ASSET_MANAGEMENT", "rate_type": "BPS", "billing_frequency": "QUARTERLY",
            "billing_timing": "ARREARS", "valuation_method": "PERIOD_END", "status": "APPROVED",
            "tier_method": "GRADUATED"}
    check(all(pick[k] in vals[k] for k in pick), "fixture fee schedule values satisfy the catalog CHECKs", f"{vals}")
    fsid = uuid.uuid4()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        await conn.execute(
            "INSERT INTO public.fee_schedules (id, org_id, code, version, name, product_type, rate_type, tier_method, "
            "billing_frequency, billing_timing, valuation_method, status, approved_by, approved_at, created_by) "
            "VALUES ($1, $2, 'REP4-VERIFY', 1, 'REP4 verify strategy fee', $3, $4, $5, $6, $7, $8, $9, $10, now(), $11)",
            fsid, ORG_A, pick["product_type"], pick["rate_type"], pick["tier_method"], pick["billing_frequency"],
            pick["billing_timing"], pick["valuation_method"], pick["status"], U_ADMIN2, U_ADMIN1)
        await conn.execute("INSERT INTO public.fee_schedule_tiers (org_id, fee_schedule_id, tier_seq, lower_bound, "
                           "upper_bound, rate_bps) VALUES ($1, $2, 1, 0, 100000, 100), ($1, $2, 2, 100000, NULL, 50)",
                           ORG_A, fsid)
    S["fsid"] = fsid
    c, o = await api(U_ADMIN1, "PATCH", f"/api/v1/models/{S['m1']}", {"default_fee_schedule_id": str(fsid)})
    check(c == 200, "the model's default fee schedule set through rep3 (APPROVED, same org)", f"{c} {str(o)[:150]}")
    c, r = await viability(U_VIEWER, "F", custodian_code="altruist")
    from services.fee_calc import apply_tiers
    from services.fee_calc_inputs import FeeTierInput

    want_fee, _, _ = apply_tiers(Decimal("250000.00"), [
        FeeTierInput(tier_seq=1, lower_bound=Decimal(0), upper_bound=Decimal(100000), rate_bps=Decimal(100)),
        FeeTierInput(tier_seq=2, lower_bound=Decimal(100000), rate_bps=Decimal(50))], "GRADUATED")
    fee = line(r, "strategy_fee")
    check(fee["status"] == "known" and D(fee["exact"]) == want_fee == Decimal(1750) and fee["amount"] == "1750.00",
          "Q5 strategy fee exact through apply_tiers: 1% on the first $100,000 + 0.5% on $150,000 = $1,750.00",
          f"{fee}")
    t = line(r, "trading_cost")
    check(t["status"] == "unknown" and answer(r, "cost")["status"] == "unknown"
          and any("two live holdings snapshots" in x for x in answer(r, "cost")["missing"]),
          "Q5 trading cost unknown with one live snapshot — so the cost answer is unknown, never guessed", f"{t}")
    S["report_pre"] = r
    # immutability
    rid = r["id"]
    for sql, what in (("UPDATE portfolio.replica_viability_reports SET overall = 'yes' WHERE id = $1", "UPDATE"),
                      ("DELETE FROM portfolio.replica_viability_reports WHERE id = $1", "DELETE")):
        try:
            await scoped(conn, ORG_A, sql, UUID(rid), fetch="execute")
            check(False, f"a stored report refuses {what}")
        except Exception as exc:  # noqa: BLE001
            check("permanent record" in str(exc), f"a stored report refuses {what} (immutable)", str(exc)[:80])
    c, again = await api(U_VIEWER, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/viability/{rid}")
    check(c == 200 and again["answers"] == r["answers"] and again["overall"] == r["overall"],
          "the report reads back unchanged", f"{c}")
    c, lst = await api(U_VIEWER, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/viability")
    check(c == 200 and len(lst["rows"]) >= 7 and lst["rows"][0]["id"] == rid
          and [q["key"] for q in lst["vocabularies"]["questions"]] == ["rules_based", "rebuildable", "account_size",
                                                                     "custodian", "cost"],
          "reports listed newest first with the five question labels in the envelope", f"{len(lst.get('rows', []))}")


async def y_monitoring(conn):
    section("[Y] Monitoring: threshold drafts + review tasks; idempotent; critical terms changes raise review tasks")
    from services import human_tasks as HT
    from services import replica_holdings as H
    from services import replica_models as RM
    from services import replica_schedule as RS
    from services.database import get_pool

    if not S.get("m1"):
        check(False, "monitoring needs model M1")
        return
    pool = await get_pool()
    m1 = S["m1"]
    S["snap_F2"] = await insert_snapshot(conn, fund="F", as_of=TODAY - timedelta(days=30), source_key="ssga_spdr",
                                         status="live", rows=stock_rows(F_S2), label="F2", previous=S["snap_F1"],
                                         diff={"first_snapshot": False, "added": [], "removed": [], "moves": [],
                                               "turnover": "0.05"})
    v0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions WHERE model_id = $1::uuid AND "
                                       "system_to IS NULL", m1, fetch="fetchval"))
    with _ctx(None):
        res = await RM.monitor_snapshot(pool, fund_id=S["sec_F"], source_key="ssga_spdr", snapshot_id=str(S["snap_F2"]),
                                         include_test_orgs=True)
    outcomes = {r["model_id"]: r for r in res["models"]}
    r1 = outcomes.get(m1, {})
    check(r1.get("outcome") == "drafted" and Decimal(r1.get("turnover", "0")) == Decimal("0.049")
          and outcomes.get(S["m2"], {}).get("outcome") == "no_live_version" and not res["errors"],
          "a new live snapshot over the threshold (turnover 4.9% ≥ 2%) → drafted; models with no approved version "
          "are logged, not drafted", f"{ {k: v.get('outcome') for k, v in outcomes.items()} } {res['errors']}")
    vid = r1.get("version_id")
    if not vid:
        return
    S["v_mon"] = vid
    v = await scoped(conn, ORG_A, "SELECT * FROM portfolio.model_versions WHERE id = $1::uuid AND system_to IS NULL",
                     vid, fetch="fetchrow")
    diff = json.loads(v["diff"])
    ch = {c_["key"]: Decimal(c_["delta"]) for c_ in diff["changed"]}
    check(v["origin"] == "fund_monitor" and list(v["maker_ids"]) == [] and v["created_by"] is None
          and v["status"] == "submitted" and Decimal(diff["turnover"]) == Decimal("0.049")
          and ch == {S["sec_A"]: Decimal("-0.049"), S["sec_B"]: Decimal("0.049")} and not diff["added"]
          and not diff["removed"],
          "ONE system draft (origin fund_monitor, no maker) with the exact diff: A −0.049, B +0.049, turnover 0.049",
          f"{ch} {diff.get('turnover')}")
    tasks = await tasks_for(conn, ORG_A, vid, "model_version_review")
    pdiff = (tasks[0]["ht"].get("payload") or {}).get("diff") if tasks else None
    check(len(tasks) == 1 and tasks[0]["status"] == "active" and tasks[0]["ht"]["maker_id"] is None
          and tasks[0]["perm"] == "approve_models" and pdiff is not None
          and Decimal(pdiff["turnover"]) == Decimal("0.049") and len(pdiff["changed"]) == 2,
          "one review task (rep3 review kind, approve_models, no maker) carrying the diff", f"{len(tasks)}")
    spec = await scoped(conn, ORG_A, "SELECT * FROM portfolio.replica_model_specs WHERE version_id = $1::uuid", vid,
                        fetch="fetchrow")
    check(spec is not None and spec["origin"] == "fund_monitor" and spec["snapshot_id"] == S["snap_F2"]
          and spec["created_by"] is None and D(spec["cash_target"]) == Decimal("0.02"),
          "the drafted version carries its own spec (the live spec's construction, the new snapshot)")
    ev = await scoped(conn, ORG_A, "SELECT id, detail FROM portfolio.model_events WHERE model_id = $1::uuid AND action = "
                                   "'holdings_monitor' AND detail->>'snapshot_id' = $2", m1, str(S["snap_F2"]))
    de = await scoped(conn, ORG_A, "SELECT count(*) FROM domain_events WHERE source_type = 'model_event' AND source_id = "
                                   "$1 AND event_type = 'model.holdings_monitor'", ev[0]["id"] if ev else uuid.uuid4(),
                      fetch="fetchval")
    check(len(ev) == 1 and json.loads(ev[0]["detail"])["outcome"] == "drafted" and int(de) == 1,
          "logged as a model event + a domain event (model.holdings_monitor)", f"{len(ev)} {de}")
    # idempotent
    before = (int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions", fetch="fetchval")),
              int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_events", fetch="fetchval")),
              int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_runs", fetch="fetchval")))
    with _ctx(None):
        res2 = await RM.monitor_snapshot(pool, fund_id=S["sec_F"], source_key="ssga_spdr", snapshot_id=str(S["snap_F2"]),
                                         include_test_orgs=True)
    after = (int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions", fetch="fetchval")),
             int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_events", fetch="fetchval")),
             int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_runs", fetch="fetchval")))
    check(before == after and bool(res2["models"]) and all(x["outcome"] == "already" for x in res2["models"]),
          "the same snapshot again → nothing new (no version, event or task)", f"{before} → {after}")
    try:
        await scoped(conn, ORG_A, "INSERT INTO portfolio.model_events (org_id, model_id, action, detail) VALUES ($1, "
                                  "$2::uuid, 'holdings_monitor', $3::jsonb)", ORG_A, m1,
                     json.dumps({"snapshot_id": str(S["snap_F2"]), "outcome": "x"}), fetch="execute")
        check(False, "idempotency is enforced in the database")
    except Exception as exc:  # noqa: BLE001
        check("model_events_holdings_monitor_uq" in str(exc), "idempotency is enforced in the database (partial unique)",
              str(exc)[:90])
    # the reviewer approves the draft (review kind: no maker, so any approver)
    c, o = await api(U_ADMIN2, "POST", f"/api/v1/models/{m1}/versions/{vid}/approve", {})
    check(c == 200 and (o.get("version") or {}).get("status") == "approved",
          "the review task's approval makes the drafted version live",
          f"{c} {str(o)[:120]}")
    # under threshold, through the real hook (rep1's refresh calls RS.monitor_models with its IngestResult)
    S["snap_F3"] = await insert_snapshot(conn, fund="F", as_of=TODAY, source_key="ssga_spdr", status="live",
                                         rows=stock_rows(F_S3), label="F3", previous=S["snap_F2"],
                                         diff={"first_snapshot": False, "added": [], "removed": [], "moves": [],
                                               "turnover": "0.001"})
    v_before = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions WHERE model_id = $1::uuid "
                                             "AND system_to IS NULL", m1, fetch="fetchval"))
    ingest = H.IngestResult(S["sec_F"], TICKER["F"], "ssga_spdr", False, created=True, snapshot_id=str(S["snap_F3"]),
                            status="live")
    with _ctx(None):
        hook = await RS.monitor_models(pool, ingest, include_test_orgs=True)
    ev3 = await scoped(conn, ORG_A, "SELECT detail FROM portfolio.model_events WHERE model_id = $1::uuid AND action = "
                                    "'holdings_monitor' AND detail->>'snapshot_id' = $2", m1, str(S["snap_F3"]),
                       fetch="fetchval")
    d3 = json.loads(ev3) if ev3 else {}
    v_after = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions WHERE model_id = $1::uuid "
                                            "AND system_to IS NULL", m1, fetch="fetchval"))
    check(hook is not None and d3.get("outcome") == "within_tolerance"
          and Decimal(d3.get("turnover", "1")) == Decimal("0.00098") and v_after == v_before,
          "under the threshold (turnover 0.098%) → logged 'within tolerance', no draft (through rep1's refresh hook)",
          f"{hook} {d3.get('turnover')}")
    # a needs_review snapshot is never used
    S["snap_Fnr"] = await insert_snapshot(conn, fund="F", as_of=TODAY, source_key="ssga_spdr", status="needs_review",
                                          rows=stock_rows({"A": "0.9", "B": "0.1"}), label="Fnr")
    nr = H.IngestResult(S["sec_F"], TICKER["F"], "ssga_spdr", False, created=True, snapshot_id=str(S["snap_Fnr"]),
                        status="needs_review")
    with _ctx(None):
        hook_nr = await RS.monitor_models(pool, nr, include_test_orgs=True)
        direct = await RM.monitor_snapshot(pool, fund_id=S["sec_F"], source_key="ssga_spdr",
                                           snapshot_id=str(S["snap_Fnr"]), include_test_orgs=True)
    n_nr = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_events WHERE detail->>'snapshot_id' = $1",
                            str(S["snap_Fnr"]), fetch="fetchval"))
    check(hook_nr is None and "never used" in direct.get("skipped", "") and n_nr == 0,
          "a needs_review snapshot is never used by monitoring", f"{hook_nr} {direct.get('skipped')}")


async def y_viability_cost(conn):
    section("[Y] Viability (continued): Q5 numbers exact once turnover history exists; reports re-computable")
    if not S.get("snap_F3"):
        check(False, "needs the monitoring snapshots")
        return
    c, r = await viability(U_VIEWER, "F", custodian_code="altruist")
    t = line(r, "trading_cost")
    # independent: diffs F2 (0.05) + F3 (0.001) over 60 days; × 365 / 60 × 2 sides × $250,000 × 5 bp
    want = (Fraction("0.051") * 365 / 60) * 2 * 250000 * 5 / 10000
    want_d = Decimal(want.numerator) / Decimal(want.denominator)
    check(c == 201 and t["status"] == "known" and D(t["exact"]) == want_d == Decimal("77.5625") and t["amount"] == "77.56"
          and t["inputs"]["days_spanned"] == 60 and t["inputs"]["diffs"] == 2,
          "trading cost exact: 0.051 turnover over 60 days, annualised (0.31025) × 2 sides × $250,000 × 5 bp = $77.5625",
          f"{t}")
    tot = line(r, "replica_total")
    a5 = answer(r, "cost")
    check(D(tot["exact"]) == Decimal("1827.5625") and tot["amount"] == "1827.56" and a5["status"] == "no"
          and D(a5["inputs"]["fund_cost"]) == Decimal(500) and D(a5["inputs"]["replica_cost"]) == Decimal("1827.5625")
          and "tax engine (rep6)" in a5["reason"],
          "Q5 exact: replica $1,827.56 ($1,750 fee + $77.5625 trading) vs the fund's $500.00 → no, before tax (rep6)",
          f"{a5.get('reason')}")
    pre = S.get("report_pre") or {}
    c, again = await api(U_VIEWER, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/viability/{pre.get('id')}")
    check(c == 200 and r["id"] != pre.get("id") and again["answers"] == pre.get("answers"),
          "re-computing writes a NEW report; the earlier one is unchanged", f"{c}")


async def y_terms_monitor(conn):
    section("[Y] Monitoring (terms): a critical terms change raises a review task on each model of the fund")
    from services import human_tasks as HT
    from services import replica_models as RM
    from services.database import get_pool

    if not S.get("m1"):
        check(False, "terms monitoring needs the F models")
        return
    models = [S["m1"], S["m2"], S["m3"]]
    v_before = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions", fetch="fetchval"))
    new_terms = {**F_TERMS, "expense_ratio_net_pct": 0.25, "rebalance_frequency": "monthly"}
    v2 = await insert_terms(conn, org=ORG_A, key="F", version_no=2, values=term_values(**new_terms), approved=False,
                            maker=U_ADMIN1)
    c, o = await api(U_ADMIN2, "POST", f"/api/v1/replication/registry/{ENTRY['F']}/terms/{v2}/approve", {"note": "ok"})
    check(c == 200 and o["version"]["status"] == "approved", "terms v2 approved through rep1's route (the checker)",
          f"{c} {str(o)[:120]}")
    ok_all, fields_seen = True, []
    for mid in models:
        ev = await scoped(conn, ORG_A, "SELECT detail FROM portfolio.model_events WHERE model_id = $1::uuid AND action = "
                                       "'terms_monitor' AND detail->>'terms_version_id' = $2", mid, str(v2))
        tasks = await tasks_for(conn, ORG_A, mid, RM.TERMS_REVIEW_KIND)
        changed = json.loads(ev[0]["detail"])["changed"] if ev else []
        fields = sorted(x["field_key"] for x in changed)
        fields_seen.append(fields)
        ptitle = tasks[0]["ht"]["title"] if tasks else ""
        ok_all = ok_all and len(ev) == 1 and fields == ["expense_ratio_net_pct", "rebalance_frequency"] \
            and len(tasks) == 1 and tasks[0]["status"] == "active" and tasks[0]["perm"] == "manage_models" \
            and tasks[0]["ht"]["maker_id"] is None and "expense_ratio_net_pct" in ptitle
    check(bool(ok_all), "each of the fund's 3 models: one terms_monitor event + one review task naming exactly the "
                        "changed fields (expense_ratio_net_pct, rebalance_frequency)", f"{fields_seen}")
    v_after = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_versions", fetch="fetchval"))
    check(v_after == v_before, "no automatic version from a terms change", f"{v_before} → {v_after}")
    pool = await get_pool()
    with _ctx(ORG_A):
        again = await RM.monitor_terms(pool, org_id=ORG_A, entry_id=ENTRY["F"], version_id=v2,
                                       prior_version_id=S["terms_F1"])
    check(bool(again["models"]) and all(m_["outcome"] == "already" for m_ in again["models"]),
          "the same terms version again → nothing new", f"{again['models'][:1]}")
    tasks = await tasks_for(conn, ORG_A, S["m1"], RM.TERMS_REVIEW_KIND)
    if tasks:
        with _ctx(ORG_A):
            await HT.complete_task(pool, org_id=ORG_A, task_id=tasks[0]["id"], actor_id=U_ADMIN2, outcome="done",
                                   note="no new version needed")
        n = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.model_events WHERE model_id = $1::uuid AND action = "
                                      "'terms_review_done'", S["m1"], fetch="fetchval")
        check(int(n) == 1, "completing the review (outcome done) is logged on the model", f"{n}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways on identical requests")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service, rolbypassrls = false",
          f"{dict(who)}")
    # org B's own entry on the same fund, approved terms, its own build
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3::uuid, 'index_etf', $4, "
            "'ssga_spdr', 'active', $5, $6)", ENTRY["FB"], ORG_B, S["sec_F"], TICKER["F"], f"Holdings of {TICKER['F']}",
            U_ADMIN_B)
    await insert_terms(conn, org=ORG_B, key="FB", version_no=1, values=term_values(**F_TERMS), approved=True,
                       maker=U_ADMIN_B, checker=U_ADMIN1)
    c, o = await build(U_ADMIN_B, "FB", custodian_code="altruist")
    check(c == 201, "org B builds its own model on the same fund", f"{c} {str(o)[:150]}")
    mb = o.get("model", {}).get("id")
    pairs = (
        (U_ADMIN_B, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/model", None),
        (U_ADMIN_B, "GET", f"/api/v1/replication/models/{S.get('m1')}/spec", None),
        (U_ADMIN_B, "POST", f"/api/v1/replication/registry/{ENTRY['F']}/models", {"custodian_code": "altruist"}),
        (U_ADMIN_B, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/viability/{(S.get('report_pre') or {}).get('id')}",
         None),
        (U_ADMIN1, "GET", f"/api/v1/replication/registry/{ENTRY['FB']}/model", None),
        (U_ADMIN1, "GET", f"/api/v1/replication/models/{mb}/spec", None),
        (U_ADMIN1, "POST", f"/api/v1/replication/registry/{ENTRY['FB']}/viability", {"custodian_code": "altruist"}),
    )
    codes = [(await api(u, m, p, b))[0] for u, m, p, b in pairs]
    check(codes == [404] * len(pairs), "another org's entry, model, report and build are 404 — both directions",
          f"{codes}")
    for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        own = [int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", org, fetch="fetchval"))
               for t in ("portfolio.replica_model_specs", "portfolio.replica_registry_keys", "portfolio.models")]
        theirs = [int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", other, fetch="fetchval"))
                  for t in ("portfolio.replica_model_specs", "portfolio.replica_registry_keys",
                            "portfolio.replica_viability_reports", "portfolio.models")]
        check(all(n > 0 for n in own) and theirs == [0, 0, 0, 0],
              f"under RLS org {'A' if org == ORG_A else 'B'} sees its own specs/keys/models and none of the other's",
              f"own {own} theirs {theirs}")
    # monitoring crosses orgs by fund (platform data), each model in its own org
    from services import replica_models as RM
    from services.database import get_pool

    pool = await get_pool()
    with _ctx(None):
        res = await RM.monitor_snapshot(pool, fund_id=S["sec_F"], source_key="ssga_spdr", snapshot_id=str(S["snap_F3"]),
                                        include_test_orgs=True)
    rb = next((x for x in res["models"] if x["model_id"] == mb), {})
    evb = await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.model_events WHERE model_id = $1::uuid AND action = "
                                    "'holdings_monitor'", mb, fetch="fetchval") if mb else 0
    check(rb.get("outcome") == "no_live_version" and rb.get("org_id") == str(ORG_B) and int(evb) == 1,
          "a snapshot of fund F reaches org B's model too, logged in org B", f"{rb}")
    # gates both ways on identical requests
    body = {"custodian_code": "altruist", "method": "full", "cash_target_pct": "2"}
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/replication/registry/{ENTRY['F']}/models", body)
    ca, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{ENTRY['F']}/models", body)
    check(cv == 403 and "manage_models" in str(ov.get("detail")) and ca == 200,
          "the identical build: viewer 403 (manage_models), admin 200 (joins the existing model)", f"{cv} {ca}")
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/replication/registry/{ENTRY['F']}/models/preview", body)
    ca, oa = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{ENTRY['F']}/models/preview", body)
    check(cv == 403 and ca == 200 and oa["existing"]["model"]["id"] == S.get("m1"),
          "the identical preview: viewer 403, admin 200 (offers the existing model first)", f"{cv} {ca}")
    cn, on = await api(U_NONE, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/model")
    cv, ov = await api(U_VIEWER, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/model")
    ca, oa = await api(U_ADMIN1, "GET", f"/api/v1/replication/registry/{ENTRY['F']}/model")
    check(cn == 403 and "view_replication" in str(on.get("detail")) and cv == 200 and ov["permissions"]["can_build"] is False
          and ov["vocabularies"]["editable"] == [] and ca == 200 and oa["permissions"]["can_build"] is True
          and oa["vocabularies"]["editable"] != [],
          "the Model tab: no view_replication → 403; viewer gets a view-only envelope (editable []); admin can build",
          f"{cn} {cv} {ca}")
    voc = ov.get("vocabularies", {})
    check(all(voc.get(k) for k in ("questions", "answer_statuses", "methods", "unresolved_policies", "custodians")),
          "the envelope publishes the vocabularies (questions, statuses, methods, policies, custodians)")
    cn, _ = await api(U_NONE, "GET", f"/api/v1/replication/models/{S.get('m1')}/spec")
    cv, ov = await api(U_VIEWER, "GET", f"/api/v1/replication/models/{S.get('m1')}/spec")
    check(cn == 403 and cv == 200 and len(ov.get("specs", [])) == 2 and ov["key"]["custodian_code"] == "altruist",
          "spec read: no view_models → 403; view_models → the model's specs (build + monitor)", f"{cn} {cv}")
    cn, _ = await viability(U_NONE, "F", custodian_code="altruist")
    check(cn == 403, "viability without view_replication → 403", f"{cn}")


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k].get("after") != gn[k].get("after")]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    new = [k for k in gn if k not in gb]
    perms = ("view_replication", "manage_models", "view_models")
    check(len(new) == 8 and all(k[1].startswith("/api/v1/replication/") for k in new)
          and all(gn[k]["module"] == "routers.replication_models" for k in new)
          and all(any(p in str(gn[k].get("after")) for p in perms) for k in new)
          and all("manage_models" in gn[k]["after"] and "view_replication" in gn[k]["after"]
                  for k in new if k[0] == ("POST",) and k[1].endswith(("/models", "/models/preview"))),
          "8 new routes under /replication, each naming its permission (build + preview need both)", f"{len(new)}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    check((WEB_DIR / "tests" / "replication" / "rep4.test.mjs").exists()
          and "model tab: no Build / Preview / Use controls without manage_models" in out
          and "model tab: a missing or malformed envelope renders no write control" in out,
          "the rep4 node tests (no Build / Use controls without the flag) ran in that suite")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep3.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep3.py")], REPO, timeout=1200, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    result = re.findall(r"^RESULT: (.*)$", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    zero = (bool(total) and total[-1][1] == "0") or (bool(result) and "ALL ASSERTIONS PASSED" in result[-1])
    check(zero and not fails and code == 0, "verify_rep3.py: 0 FAIL ('TOTAL:' or 'RESULT:' summary)",
          f"{total[-1] if total else (result[-1] if result else 'no summary')}; {fails[:3]} "
          f"{err[-200:] if not (total or result) else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers i JOIN "
                             "portfolio.securities_global s ON s.id = i.global_security_id WHERE i.id_value LIKE $1 "
                             "AND NOT (s.name = ANY($2::text[]))", f"{PREFIX}%", list(SEC_NAMES), fetch="fetchval")
    m = await platform(conn, "SELECT count(*) FROM portfolio.models WHERE name LIKE $1 AND NOT (org_id = "
                             "ANY($2::uuid[]))", f"Holdings of {PREFIX}%", FIXTURE_ORGS, fetch="fetchval")
    p = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE source = $1", TAG,
                       fetch="fetchval")
    return check(int(n) == 0 and int(m) == 0 and int(p) == 0,
                 f"the reserved prefix {PREFIX} (securities, 'Holdings of {PREFIX}' models, '{TAG}' prices) matches no "
                 "real row", f"{n}/{m}/{p}")


DROPPED_CONNECTION = (asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError, ConnectionError)


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
    pre = real = None
    try:
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        S["cons"] = await read_constraints(conn)
        n_cons = sum(len(v["checks"]) + len(v["fks"]) + len(v["uniques"]) for v in S["cons"].values())
        check(n_cons > 0 and all(S["cons"][t]["checks"] or S["cons"][t]["fks"] for t in TOUCHED_FOR_CONSTRAINTS),
              "every UNIQUE (incl. partial), CHECK and FK on the touched tables read from the catalog before seeding",
              f"{n_cons} constraints on {len(TOUCHED_FOR_CONSTRAINTS)} tables")
        snap_ok = {"live", "needs_review"} <= set(allowed(S["cons"], "portfolio.replica_holdings_snapshots", "status"))
        types_ok = {"equity", "cash", "future"} <= set(allowed(S["cons"], "portfolio.replica_holdings_rows", "asset_type"))
        check(snap_ok and types_ok, "the fixture snapshot statuses and asset types are allowed by the catalog CHECKs")
        await run("task 1", y_task1, conn)
        await run("seed", seed, conn)
        if S.get("sec_A") and S.get("snap_F1"):
            await run("schema", y_schema, conn)
            await run("build", y_build, conn)
            await run("registry", y_registry, conn)
            await run("preconditions", y_preconditions, conn)
            await run("viability", y_viability, conn)
            await run("monitoring", y_monitoring, conn)
            await run("viability cost", y_viability_cost, conn)
            await run("terms monitor", y_terms_monitor, conn)
            await run("isolation + gates", y_isolation, conn)
        else:
            check(False, "seed created the fixture securities and snapshots", "every fixture-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass

        async def after_teardown(c):
            """Every post-teardown read, as ONE unit, so a retry re-runs all of it on the same connection."""
            return (await leftovers(c), await counts(c) if pre is not None else None,
                    await real_fingerprints(c) if real is not None else None)

        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            try:
                left, post, after = await after_teardown(conn)
            except DROPPED_CONNECTION as exc:
                # Reconnect ONCE and re-run every count on the new connection. A second drop propagates
                # to "teardown raised" below — a dropped connection is never reported as clean.
                print(f"[INFO] connection dropped during the leftover check ({type(exc).__name__}); reconnecting once")
                try:
                    conn.terminate()
                except Exception:  # noqa: BLE001
                    pass
                conn = await connect(dsn)
                left, post, after = await after_teardown(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if post is not None:
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if after is not None:
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass
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
