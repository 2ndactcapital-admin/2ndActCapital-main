"""verify_marketdata1 — the vendor-neutral security-data layer: one canonical
store per data type, a provider registry with licence facts on every
provider, a rank per data type (platform default, org override), call
budgets, data-quality events, the free adapters (custodian valuations, SEC
N-PORT, issuer files, FRED, CSV uploads with maker-checker, adviser entry), a
not-connected vendor slot, and every "needs prices" reader switched to the
layer.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_marketdata1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

NO NETWORK: the httpx network transport records and refuses — any attempt is
a failed check. FRED runs through ``FredClient`` with a recorded-fixture
transport; the vendor slot's transport records every call (there must be
none); the N-PORT / issuer-file providers get an ``httpx.MockTransport``.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
REAL: the app through an ASGI transport (middleware, Principal, every gate),
the layer, rep3's upload machinery, rep5's proposal pricing path, rep4's
viability Q3, RLS and the live database. The script's own connection is the
live DATABASE_URL (app_service, rolbypassrls = false — asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-0000000d1001 / d1002, is_test =
true, asserted); users created explicitly with the auth0_sub their token
carries and an @test.local email; masked account numbers only (raw ZZD1…);
securities with the reserved identifier prefix ZZMD1 and the name prefix
'MARKETDATA1 VERIFY'; index keys ZZMD1…; fixture providers keyed zzmd1… (the
real providers and the real platform rank are never edited — the rank tests
use the fixture org's OWN override rows); nightly runs with trigger 'test'.
All checked absent from real rows first.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN —
seeds keyed by this run's fixture ids (every org_id table under the fixture
orgs, the fixture organizations and users, the fixture securities and their
prices / corporate actions / holdings snapshots, ZZMD1 index levels and
quality events, zzmd1 providers and their call log, the 'test' nightly
summary rows), a FK closure, one DELETE per table child-before-parent. Every
counted table is re-read for its exact pre-test count; real rows are compared
by count + checksum.

Every expected date is computed at the moment of the call. With
VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
--cleanup-only: the teardown and the leftover check only.
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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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

import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_engine as E  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import replica_holdings as H  # noqa: E402
from services import replica_models as RM  # noqa: E402
from services import replica_viability as RV  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_data as SD  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import xray as XR  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.market_data.fred import FredResponse  # noqa: E402
from services.security_data import admin as SDA  # noqa: E402
from services.security_data import nightly as SDN  # noqa: E402
from services.security_data import quality as Q  # noqa: E402
from services.security_data import settings as SDS  # noqa: E402
from services.security_data import uploads as UP  # noqa: E402
from services.security_data import writes as W  # noqa: E402
from services.security_data.providers import build_providers  # noqa: E402
from services.security_data.providers.base import BaseProvider, FetchOutcome  # noqa: E402
from services.security_data.providers.fred import FredProvider  # noqa: E402
from services.security_data.providers.sec_nport import NportProvider  # noqa: E402
from services.security_data.providers.vendor_stub import VendorStubProvider  # noqa: E402
from services.security_data.types import Point, SecurityDataError  # noqa: E402

BEFORE_REF = "afdbc6d"   # main @ "chore: refresh schema snapshot" — the marketdata1-work base
TAG = "md1verify"
PREFIX = "ZZMD1"
PKEY = "zzmd1"
NAME_PREFIX = "MARKETDATA1 VERIFY"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("X", "Y", "Z", "FUND", "FUND2", "IDX")
SEC_TYPES = {"X": "equity", "Y": "equity", "Z": "equity", "FUND": "fund", "FUND2": "fund", "IDX": "index"}
SEC_NAMES = {k: f"{NAME_PREFIX} {k}" for k in SEC_KEYS}
FRED_CODE = f"{PREFIX}DGS10"
IDX_KEYS = [f"{PREFIX}IDX{i}" for i in range(1, 4)]
GAP_KEY, OUT_KEY = f"{PREFIX}GAP", f"{PREFIX}OUT"
FIX_PROVIDERS = {   # key: (kind, internal, member_display, requires_entitlement, budget)
    f"{PKEY}int": ("public_api", True, "not_allowed", False, None),
    f"{PKEY}disp": ("public_api", True, "allowed", False, None),
    f"{PKEY}ent": ("vendor", True, "allowed", True, None),
    f"{PKEY}fa": ("public_api", True, "not_allowed", False, 2),
    f"{PKEY}fb": ("public_api", True, "not_allowed", False, None),
}
VENDOR_NAMES = re.compile(r"\b(fmp|financial\s*modeling\s*prep|tiingo|eodhd|polygon|nasdaq\s*data\s*link|quandl|"
                          r"bloomberg|refinitiv|yahoo|intrinio|alpha\s*vantage|iex\s*cloud|morningstar|factset|"
                          r"twelve\s*data|finnhub)\b", re.I)
READER_FILES = ("services/harvest.py", "services/rebalance_proposals.py", "services/replica_viability.py",
                "services/xray.py", "services/portfolio_securities.py", "services/loss_bank.py",
                "services/options_sleeve.py", "services/security_data/layer.py", "services/security_data/registry.py",
                "services/security_data/types.py")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000d{n:04d}")


ORG_A, ORG_B = _id(1001), _id(1002)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN1, U_ADMIN2, U_VIEWER, U_ADMIN_B = (_id(1110 + i) for i in range(5))
USERS = {   # org, users.role, short, rbac role, full name
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "MD1 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "MD1 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "MD1 Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "md1_viewer", "MD1 Viewer"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "MD1 Org B Admin"),
}
VIEWER_PERMS = ("view_replication",)
ENT_1, ENT_2, ENT_B = _id(1201), _id(1202), _id(1203)
ACCT_1, ACCT_2, ACCT_B = _id(1301), _id(1302), _id(1303)
ACCOUNTS = {   # id: (org, custodian_code, entity, raw number)
    ACCT_1: (ORG_A, "ALTRUIST", ENT_1, "ZZD1000000001"),
    ACCT_2: (ORG_A, "ALTRUIST", ENT_2, "ZZD1000000002"),
    ACCT_B: (ORG_B, "ALTRUIST", ENT_B, "ZZD1000000003"),
}
# member 1 holds X; member 2 holds Y; org B holds X at a different valuation
X_QTY, X_MV = Decimal(100), Decimal("12345.67")
Y_QTY, Y_MV = Decimal(10), Decimal(500)
XB_QTY, XB_MV = Decimal(50), Decimal(999)

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.member_todos", "public.entities", "public.accounts", "public.org_settings", "portfolio.assets",
    "portfolio.positions", "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.securities_global_prices", "portfolio.securities_global_corporate_actions",
    "portfolio.replica_holdings_snapshots", "portfolio.adviser_quotes",
    "portfolio.data_providers", "portfolio.data_source_rank", "portfolio.data_provider_entitlements",
    "portfolio.data_uploads", "portfolio.index_levels", "portfolio.data_quality_events", "portfolio.provider_call_log",
    "portfolio.security_data_runs",
)
NEW_TABLES = ("portfolio.data_providers", "portfolio.data_source_rank", "portfolio.data_provider_entitlements",
              "portfolio.data_uploads", "portfolio.index_levels", "portfolio.data_quality_events",
              "portfolio.provider_call_log", "portfolio.security_data_runs")
NEW_ORG_TABLES = ("portfolio.data_provider_entitlements", "portfolio.data_uploads")
NEW_ROUTES = {
    ("GET", "/api/v1/admin/security-data/providers"): "super_admin",
    ("PUT", "/api/v1/admin/security-data/providers/{key}"): "super_admin",
    ("GET", "/api/v1/admin/security-data/call-log"): "super_admin",
    ("GET", "/api/v1/admin/security-data/rank"): "super_admin",
    ("PUT", "/api/v1/admin/security-data/rank/{data_type}"): "super_admin",
    ("GET", "/api/v1/admin/security-data/runs/latest"): "super_admin",
    ("GET", "/api/v1/security-data/rank"): "manage_org_settings",
    ("PUT", "/api/v1/security-data/rank/{data_type}"): "manage_org_settings",
    ("GET", "/api/v1/security-data/quality"): "manage_org_settings",
    ("GET", "/api/v1/security-data/lookup"): "manage_org_settings",
    ("GET", "/api/v1/security-data/uploads"): "manage_org_settings",
    ("POST", "/api/v1/security-data/uploads"): "manage_org_settings",
    ("POST", "/api/v1/security-data/uploads/{upload_id}/decision"): "manage_org_settings",
}

S: dict = {}
NETWORK_CALLS: list[str] = []
VENDOR_CALLS: list[str] = []
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


def tk(key):
    return f"{PREFIX}{key}"


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def ago(n: int) -> date:
    return today() - timedelta(days=n)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ═══ DB helpers ══════════════════════════════════════════════════════════════
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


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch (count + checksum), outside this run's fixtures."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    t0 = S.setdefault("t0", now())
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("adviser_quotes", "portfolio.adviser_quotes"), ("org_settings", "public.org_settings")):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["prices"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global_prices WHERE system_from < $1",
                                      t0)
    out["corporate_actions"] = await fingerprint(
        conn, "SELECT * FROM portfolio.securities_global_corporate_actions WHERE system_from < $1", t0)
    out["holdings_snapshots"] = await fingerprint(
        conn, "SELECT id, status, as_of, source_key, provider_key FROM portfolio.replica_holdings_snapshots "
              "WHERE fetched_at < $1", t0)
    out["holdings_rows"] = await fingerprint(
        conn, "SELECT count(*) AS n, max(id::text) AS m FROM portfolio.replica_holdings_rows")
    out["securities_global"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global WHERE system_from < $1",
                                                 t0)
    out["providers"] = await fingerprint(conn, "SELECT * FROM portfolio.data_providers WHERE key NOT LIKE $1",
                                         f"{PKEY}%")
    out["platform_rank"] = await fingerprint(conn, "SELECT * FROM portfolio.data_source_rank WHERE owner_scope = "
                                                   "'platform'")
    out["index_levels"] = await fingerprint(conn, "SELECT * FROM portfolio.index_levels WHERE index_key NOT LIKE $1",
                                            f"{PREFIX}%")
    out["call_log"] = await fingerprint(conn, "SELECT * FROM portfolio.provider_call_log WHERE provider_key NOT LIKE $1",
                                        f"{PKEY}%")
    # the mkt01–03 indicator store (large): count, live count, newest system_from — per series
    out["indicator_observations"] = await fingerprint(
        conn, "SELECT series_id, count(*) AS n, count(*) FILTER (WHERE valid_to IS NULL) AS live, max(system_from) "
              "AS m FROM market_data.indicator_observations GROUP BY series_id")
    out["indicator_series"] = await fingerprint(conn, "SELECT id, series_key, ingest_status, last_observation_date "
                                                      "FROM market_data.indicator_series")
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
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


async def fixture_security_ids(conn) -> list:
    ids = {r["global_security_id"] for r in await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")}
    ids |= {r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                              f"{NAME_PREFIX}%")}
    return list(ids)


async def org_tables(conn) -> list[str]:
    return [r["t"] for r in await conn.fetch(
        "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
        "'information_schema') ORDER BY 1")]


async def teardown() -> list[str]:
    log: list[str] = []
    op = await operator_conn()
    try:
        secs = await fixture_security_ids(op)
        users = list(USERS)
        orgs = FIXTURE_ORGS
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        plan = {
            "public.member_todos": org_or_user, "public.audit_log": org_or_user,
            "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global_corporate_actions": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.replica_holdings_snapshots": ("fund_global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.index_levels": ("index_key LIKE $1", [f"{PREFIX}%"]),
            "portfolio.data_quality_events": ("subject_key LIKE $1 OR global_security_id = ANY($2::uuid[]) OR "
                                              "provider_key LIKE $3", [f"{PREFIX}%", secs, f"{PKEY}%"]),
            "portfolio.provider_call_log": ("provider_key LIKE $1", [f"{PKEY}%"]),
            "portfolio.security_data_runs": ("run_trigger = 'test'", []),
            "portfolio.data_source_rank": ("provider_key LIKE $1", [f"{PKEY}%"]),
            "portfolio.data_providers": ("key LIKE $1", [f"{PKEY}%"]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               label="verify_marketdata1")
        log = res["log"]
    finally:
        await close_conn(op)
    return log


TEARDOWN_RETRYABLE = (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
                      ConnectionError)


async def teardown_retrying() -> list[str]:
    try:
        return await teardown()
    except TEARDOWN_RETRYABLE as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; rolled back, retrying once")
        try:
            return await teardown()
        except Exception as exc2:  # noqa: BLE001
            check(False, "teardown retry failed", f"{type(exc2).__name__}: {exc2}")
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL", flush=True)
            os._exit(1)


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.index_levels WHERE index_key LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.data_quality_events WHERE subject_key LIKE $1 OR provider_key LIKE $2",
         [f"{PREFIX}%", f"{PKEY}%"]),
        ("SELECT count(*) FROM portfolio.provider_call_log WHERE provider_key LIKE $1", [f"{PKEY}%"]),
        ("SELECT count(*) FROM portfolio.data_providers WHERE key LIKE $1", [f"{PKEY}%"]),
        ("SELECT count(*) FROM portfolio.data_source_rank WHERE provider_key LIKE $1", [f"{PKEY}%"]),
        ("SELECT count(*) FROM portfolio.security_data_runs WHERE run_trigger = 'test'", []),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    op = await operator_conn()
    try:
        for t in await org_tables(op):
            n += int(await op.fetchval(f"SELECT count(*) FROM {t} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    finally:
        await close_conn(op)
    return n


async def pre_teardown(conn):
    log = await teardown_retrying()
    if log:
        find("pre-run cleanup removed an earlier run's leftovers", "; ".join(log))
    else:
        print("[INFO] pre-run cleanup: nothing left from an earlier run")
    check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, *, files=None, data=None, timeout=240.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_marketdata1"}}
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
        raise AssertionError(f"verify_marketdata1: a network call was attempted: {request.url}")

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


async def in_org(fn, org=None, super_admin=False):
    pool = await get_pool()
    with _ctx(org or ORG_A, super_admin):
        async with pool.acquire() as c2:
            return await fn(c2)


async def pool_call(fn, org=None):
    pool = await get_pool()
    with _ctx(org or ORG_A):
        return await fn(pool)


# ═══ Fakes (recorded fixtures — no network) ═════════════════════════════════
class FakeProvider(BaseProvider):
    """A provider whose values the test sets: {(data_type, subject): [Point newest first]}."""

    def __init__(self, key: str, caps=("eod_price", "index_level")):
        self.key = key
        self.capabilities = frozenset(caps)
        self.fetches = frozenset({"index_level"})
        self.points: dict = {}
        self.fetch_calls: list[str] = []

    def set(self, data_type, subject, value, as_of, **detail):
        self.points[(data_type, str(subject))] = [Point(Decimal(str(value)), as_of, self.key, {}, [], detail)]

    def clear(self):
        self.points.clear()

    async def read(self, conn, ctx, data_type, subjects, *, end, start=None):
        out = {}
        for s in subjects:
            pts = [p for p in self.points.get((data_type, str(s)), []) if p.as_of <= end
                   and (start is None or p.as_of >= start)]
            if pts:
                out[str(s)] = pts
        return out

    async def fetch(self, pool, data_type, subjects, *, today, budget_left):
        out = FetchOutcome()
        for code in subjects:
            if budget_left is not None and out.calls >= budget_left:
                out.budget_exhausted = True
                out.skipped.append(code)
                continue
            out.calls += 1
            self.fetch_calls.append(code)
            out.written += await W.upsert_index_levels(pool, code, [(today - timedelta(days=1), Decimal("100"))],
                                                       provider_key=self.key)
        return out


class FredFixtureTransport:
    """Recorded FRED observations (the '.' marker = no value)."""

    def __init__(self, points):
        self.points = points
        self.calls: list[str] = []

    async def get(self, path, params):
        self.calls.append(path)
        obs = [{"date": d.isoformat(), "value": v} for d, v in self.points]
        return FredResponse(200, {"observations": obs, "count": len(obs)}, None)

    async def aclose(self):
        return None


class RecordingTransport:
    async def get(self, url, params):
        VENDOR_CALLS.append(url)
        return 200, {}


FAKES: dict[str, FakeProvider] = {}


def registry() -> dict:
    """The REAL code registry + this run's fixture providers."""
    if not FAKES:
        for k in FIX_PROVIDERS:
            FAKES[k] = FakeProvider(k)
    return {**build_providers(transports={"vendor_slot": RecordingTransport()}), **FAKES}


# Every literal the seed and the tests write into a CHECK-constrained column — checked against the live catalog.
SEED_CHECK_LITERALS = (
    ("portfolio.data_providers", "data_providers_kind_chk", ("public_api", "vendor")),
    ("portfolio.data_providers", "data_providers_status_chk", ("active", "not_connected")),
    ("portfolio.data_providers", "data_providers_member_display_chk", ("allowed", "not_allowed", "own_holdings_only")),
    ("portfolio.data_providers", "data_providers_retention_chk", ("keep",)),
    ("portfolio.data_source_rank", "data_source_rank_type_chk", ("eod_price", "index_level", "fund_holdings")),
    ("portfolio.data_source_rank", "data_source_rank_scope_chk", ("platform", "org")),
    ("portfolio.data_uploads", "data_uploads_type_chk", ("eod_price", "corporate_action", "index_level")),
    ("portfolio.data_uploads", "data_uploads_status_chk", ("pending_approval", "approved", "rejected")),
    ("portfolio.data_quality_events", "data_quality_events_kind_chk",
     ("stale", "gap", "outlier", "disagreement", "budget_exhausted")),
    ("portfolio.security_data_runs", "security_data_runs_trigger_chk", ("test",)),
    ("portfolio.securities_global_prices", "sec_global_price_type_chk", ("close",)),
    ("portfolio.securities_global_corporate_actions", "corp_actions_type_chk", ("cash_dividend", "split")),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund", "index")),
    ("portfolio.adviser_quotes", "adviser_quotes_kind_chk", ("index_level",)),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
)


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1d")
    find("1a readers", "rebalance_proposals._unheld_prices (rep5 buys of unheld securities, rep6 harvest replacements, "
         "rep7b one-off buys) and replica_viability.latest_prices (Q3 minimum account) queried "
         "securities_global_prices directly (0 rows → 'bought by amount' / 'needs prices for N of M'); xray "
         "(embedded gain = the position's own valuation; tracking error / harvest value / after-tax performance → "
         "'not estimated — needs prices'); harvest plan note; loss_bank / options_sleeve basis-risk text; "
         "portfolio_securities (UX3 grid: latest platform price, 'No platform price.'); adviser_quotes read only by "
         "options_sleeve (rep8). No reader of index levels, dividends or option quotes other than rep8's quotes.")
    find("1b shared with mkt01–03", "reused as-is, no change there: adapters.scrub_error, fred.FredClient (injectable "
         "transport, Throttle, retries), ingest.parse_observations; the indicator store / read gate / nightly stay "
         "separate (services.market_data untouched)")
    find("1c source model", "rep1's replica_holdings_sources keys (ssga_spdr, sec_nport) ARE the provider keys of the "
         "issuer_file / regulator_filing providers (snapshots now carry provider_key); rep8's adviser_quotes are the "
         "adviser_entry provider (premium → option_quote, index_level, underlying_price → eod_price); A1's add_price "
         "is 'platform_entry'")
    find("1d licence facts", "per provider: internal_use, member_display (allowed | own_holdings_only | not_allowed), "
         "redistribution, retention_after_cancel, attribution, terms note + confirmed_on, daily budget. Member-facing "
         "prices today: none directly — the member pages show market VALUES; prices appear on staff screens "
         "(proposal lines, UX3 securities grid, X-ray inputs)")
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_prices WHERE system_from < $1",
                       S.setdefault("t0", now()), fetch="fetchval")
    find("securities_global_prices rows before this run", f"{n}")
    src_rp = (API_DIR / "services" / "rebalance_proposals.py").read_text(encoding="utf-8")
    src_rv = (API_DIR / "services" / "replica_viability.py").read_text(encoding="utf-8")
    body = src_rp[src_rp.index("async def _unheld_prices"):src_rp.index("async def compute(")]
    check(bool("securities_global_prices" not in body and "SD.get_bulk" in body
               and "securities_global_prices" not in src_rv and "SD.get_bulk" in src_rv),
          "1a: the two direct price queries now ask the layer (no reader queries the price table itself)")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_marketdata1.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(bool(code == 0 and "SCHEMA OK" in out and not gaps), "_apply_marketdata1.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    for t in NEW_TABLES + ("portfolio.securities_global_prices", "portfolio.securities_global_corporate_actions"):
        s, n = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", s, n)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    uq = await platform(conn, "SELECT indexname FROM pg_indexes WHERE schemaname = 'portfolio' AND indexdef LIKE "
                              "'%UNIQUE%' AND indexdef LIKE '%WHERE%' AND tablename = ANY($1::text[])",
                        [t.split(".")[1] for t in NEW_TABLES])
    fks = await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY($1::regclass[])",
                         list(NEW_TABLES), fetch="fetchval")
    check(bool(len(uq) >= 7 and int(fks) >= 6), "partial UNIQUE and FK constraints of the new tables read from the "
          "catalog", f"{len(uq)} uniques, {fks} FKs")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog (CHECKs) before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'(?:::text)?", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    st = await platform(conn, "SELECT string_agg(pg_get_constraintdef(oid), ' ') FROM pg_constraint WHERE conrelid = "
                              "'portfolio.replica_holdings_snapshots'::regclass AND contype = 'c' AND "
                              "pg_get_constraintdef(oid) LIKE '%status%'", fetch="fetchval")
    if "'live'" not in (st or ""):
        bad.append(f"replica_holdings_snapshots status CHECK does not admit 'live': {st}")
    check(not bad, f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS) + 1} constraints)",
          f"{bad[:4]}")
    S["literals_ok"] = not bad


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                       fetch="fetchval")
    i = await platform(conn, "SELECT count(*) FROM portfolio.index_levels WHERE index_key LIKE $1", f"{PREFIX}%",
                       fetch="fetchval")
    p = await platform(conn, "SELECT count(*) FROM portfolio.data_providers WHERE key LIKE $1", f"{PKEY}%",
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    r = await platform(conn, "SELECT count(*) FROM portfolio.security_data_runs WHERE run_trigger = 'test'",
                       fetch="fetchval")
    return check(bool(int(n) == 0 and int(k) == 0 and int(i) == 0 and int(p) == 0 and int(o) == 0 and int(r) == 0),
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', provider keys {PKEY}…, 'test' runs, the fixture "
                 "orgs) match no real row", f"{n}/{k}/{i}/{p}/{o}/{r}")


async def _position(conn, org, ent, acct, sec_key, qty, mv, as_of):
    asset = uuid.uuid4()
    await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) VALUES "
                       "($1, $2, $3, $4, 'equity')", asset, org, S[f"sec_{sec_key}"], SEC_NAMES[sec_key])
    await conn.execute(
        "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity, market_value, "
        "authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, 'manual', 'manual', $7)",
        org, ent, asset, as_of, qty, mv, acct)


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"MD1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid_view = await rbac.ensure_role(conn, ORG_A, "md1_viewer", f"MD1 verify — {', '.join(VIEWER_PERMS)}")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p])
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname == "md1_viewer":
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid_view)
        for ent, org in ((ENT_1, ORG_A), (ENT_2, ORG_A), (ENT_B, ORG_B)):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, f"MD1 Verify {str(ent)[-4:]}")
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES[key],
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:04d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        for k, (kind, internal, md, ent, budget) in FIX_PROVIDERS.items():
            await conn.execute(
                "INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, "
                "lic_member_display, retention_after_cancel, daily_call_budget, requires_entitlement, attribution) "
                "VALUES ($1, $2, $3, 'active', ARRAY['eod_price', 'index_level'], $4, $5, 'keep', $6, $7, $8)",
                k, f"{NAME_PREFIX} provider {k}", kind, internal, md, budget, ent, f"{NAME_PREFIX} attribution {k}")
        salts = {}
        for org in FIXTURE_ORGS:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, ent, raw) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, true, false)",
                aid, org, n.masked, n.hashed(salts[org]), code, ent)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        await _position(conn, ORG_A, ENT_1, ACCT_1, "X", X_QTY, X_MV, ago(1))
        await _position(conn, ORG_A, ENT_2, ACCT_2, "Y", Y_QTY, Y_MV, ago(1))
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_B))
        await _position(conn, ORG_B, ENT_B, ACCT_B, "X", XB_QTY, XB_MV, ago(1))
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(bool(len(orgs) == 2 and all(r["is_test"] is True for r in orgs)), "every fixture org is is_test = true (the "
          "live scheduler and the security-data nightly skip them)")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test AND auth0_sub LIKE $2",
                        list(USERS), f"%{TAG}%", fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly with the token's auth0_sub, is_test = true")
    S["seeded"] = True


async def set_org_rank_raw(conn, org, data_type, keys):
    """The fixture org's OWN override rows, written directly (the service would refuse providers outside the
    platform rank — that refusal is proven separately)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(org))
        await conn.execute("UPDATE portfolio.data_source_rank SET valid_to = now() WHERE owner_scope = 'org' AND "
                           "org_id = $1 AND data_type = $2 AND valid_to IS NULL AND system_to IS NULL", org, data_type)
        for i, k in enumerate(keys, start=1):
            await conn.execute("INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope, "
                               "org_id) VALUES ($1, $2, $3, 'org', $4)", data_type, k, i, org)


async def get(org, data_type, subject, **kw):
    reg = registry()
    return await in_org(lambda c: SD.get(c, str(org), data_type, str(subject), as_of=kw.pop("as_of", today()),
                                         providers=reg, **kw), org)


async def y_rank_walk(conn):
    section("[Y] Rank walk + licence rule + 'not priced'")
    registry()
    fi, fd, fe = FAKES[f"{PKEY}int"], FAKES[f"{PKEY}disp"], FAKES[f"{PKEY}ent"]
    z = S["sec_Z"]
    await set_org_rank_raw(conn, ORG_A, "eod_price", [f"{PKEY}int", f"{PKEY}disp"])
    fi.set("eod_price", z, "10", ago(1))
    fd.set("eod_price", z, "11", ago(1))
    r = await get(ORG_A, "eod_price", z)
    check(bool(r.found and r.provider_key == f"{PKEY}int" and r.value == Decimal("10") and r.as_of == ago(1)
               and r.detail.get("rank_origin") == "org_override"),
          "internal use: the first ranked, allowed source wins (value, provider, as-of; the org's override)",
          json.dumps(r.as_dict(), default=str)[:300])
    m = await get(ORG_A, "eod_price", z, purpose="member_display")
    why = [t["why"] for t in m.tried if t["provider"] == f"{PKEY}int"]
    check(bool(m.found and m.provider_key == f"{PKEY}disp" and m.value == Decimal("11")
               and why == ["its licence does not allow showing values to members"]),
          "member display: the internal-only source is SKIPPED by its licence and the next allowed source wins",
          json.dumps(m.as_dict(), default=str)[:300])
    fi.clear()
    fd.clear()
    n = await get(ORG_A, "eod_price", z)
    check(bool(not n.found and n.value is None and (n.missing or "").startswith("eod_price")
               and f"{PKEY}int: no value" in n.missing and f"{PKEY}disp: no value" in n.missing
               and n.label.startswith("not priced")),
          "nothing anywhere → 'not priced' naming the data type and every source's reason; no value invented",
          f"{n.missing}")
    await set_org_rank_raw(conn, ORG_A, "eod_price", [f"{PKEY}ent", f"{PKEY}int"])
    fe.set("eod_price", z, "12", ago(1))
    fi.set("eod_price", z, "10", ago(1))
    a = await get(ORG_A, "eod_price", z)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO portfolio.data_provider_entitlements (org_id, provider_key, note) VALUES "
                           "($1, $2, $3)", ORG_A, f"{PKEY}ent", f"{TAG} entitlement")
    b = await get(ORG_A, "eod_price", z)
    bo = await get(ORG_B, "eod_price", z)
    check(bool(a.found and a.provider_key == f"{PKEY}int"
               and any(t["why"] == "the org has no entitlement to this provider" for t in a.tried)
               and b.found and b.provider_key == f"{PKEY}ent" and b.value == Decimal("12")
               and bo.detail.get("rank_origin") == "platform"),
          "entitlement: a requires-entitlement source is skipped without the org's entitlement row and used with it; "
          "org B (no override) walks the platform rank", f"{a.provider_key} {b.provider_key} {bo.detail}")
    # disagreement: same-day values 5% apart (> 2% default) flagged; 1% apart not flagged
    await set_org_rank_raw(conn, ORG_A, "eod_price", [f"{PKEY}int", f"{PKEY}disp"])
    fe.clear()
    fi.set("eod_price", z, "10", ago(1))
    fd.set("eod_price", z, "10.5", ago(1))
    d1 = await get(ORG_A, "eod_price", z)
    fd.set("eod_price", z, "10.1", ago(1))
    d2 = await get(ORG_A, "eod_price", z)
    fd.set("eod_price", z, "10.5", ago(2))
    d3 = await get(ORG_A, "eod_price", z)
    check(bool("disagreement" in d1.quality and d1.detail.get("disagreement")[0]["provider"] == f"{PKEY}disp"
               and "disagreement" not in d2.quality and "disagreement" not in d3.quality and d1.value == Decimal("10")),
          "cross-source disagreement: 5% apart on the same day is flagged (winner kept), 1% is not, a different day "
          "is not compared", f"{d1.quality} {d2.quality} {d3.quality}")
    fi.clear()
    fd.clear()


async def y_custodian(conn):
    section("[Y] Custodian valuation: internal use; the member's own display only; never another member's")
    await set_org_rank_raw(conn, ORG_A, "eod_price", [])
    x = S["sec_X"]
    want = X_MV / X_QTY
    r = await get(ORG_A, "eod_price", x)
    check(bool(r.found and r.provider_key == "custodian" and r.value == want and r.as_of == ago(1)
               and r.detail.get("account_id") == str(ACCT_1) and r.licence.get("member_display") == "own_holdings_only"),
          "custodian prices a held security for internal use: market value ÷ quantity on the position date, exact",
          json.dumps(r.as_dict(), default=str)[:300])
    own = await get(ORG_A, "eod_price", x, purpose="member_display", account_ids=[str(ACCT_1)])
    other = await get(ORG_A, "eod_price", x, purpose="member_display", account_ids=[str(ACCT_2)])
    none_ = await get(ORG_A, "eod_price", x, purpose="member_display")
    check(bool(own.found and own.value == want and own.detail.get("own_holdings") is True
               and not other.found and not none_.found
               and any(t["provider"] == "custodian" and t["why"] == "no value" for t in other.tried)),
          "member display: member 1's own holding is shown to member 1; member 2 (who does not hold X) and an "
          "unscoped member read get 'not priced'", f"{own.found} {other.found} {none_.found}")
    y_ = await get(ORG_A, "eod_price", S["sec_Y"], purpose="member_display", account_ids=[str(ACCT_1)])
    check(bool(not y_.found), "member 1 never sees member 2's valuation of Y (scoped to member 1's accounts)",
          f"{y_.missing}")
    b = await get(ORG_B, "eod_price", x)
    check(bool(b.found and b.value == XB_MV / XB_QTY and r.value != b.value),
          "org isolation of valuations: org B's view of X is org B's own position, never org A's", f"{b.value} {r.value}")
    S["x_price"] = want


async def _csv_upload(uid, csv_text, data_type, licence, mapping=None, name="px.csv"):
    data = {"data_type": data_type, "licence": json.dumps(licence)}
    if mapping is not None:
        data["mapping"] = json.dumps(mapping)
    return await api(uid, "POST", "/api/v1/security-data/uploads",
                     files={"file": (name, csv_text.encode("utf-8"), "text/csv")}, data=data)


async def y_csv(conn):
    section("[Y] CSV upload: rep3 mapping reused, rejects with reasons, maker-checker, licence recorded, counts only "
            "after approval")
    z, x = S["sec_Z"], S["sec_X"]
    d0, fut = ago(2), today() + timedelta(days=3)
    lic = {"internal_use": True, "member_display": "allowed", "redistribution": False,
           "retention_after_cancel": "delete", "attribution": f"{NAME_PREFIX} file"}
    csv = ("Symbol,Px Date,Close,Currency\n"
           f"{tk('Z')},{d0.isoformat()},42.50,USD\n"
           f"{tk('X')},{d0.isoformat()},123.40,USD\n"
           f"{tk('NOPE')},{d0.isoformat()},10,USD\n"
           f"{tk('Z')},{fut.isoformat()},43,USD\n"
           f"{tk('X')},{ago(3).isoformat()},abc,USD\n")
    cn, on = await _csv_upload(U_ADMIN1, csv, "eod_price", {k: v for k, v in lic.items() if k != "internal_use"},
                               {"Px Date": "date"})
    check(bool(cn == 422 and "internal_use" in str(on)), "a file without its licence is refused (the uploader states "
          "it)", f"{cn} {str(on)[:200]}")
    c, o = await _csv_upload(U_ADMIN1, csv, "eod_price", lic, {"Px Date": "date"})
    up = o.get("upload") or {}
    rej = {r["code"]: r for r in up.get("rejects") or []}
    check(bool(c == 201 and up.get("status") == "pending_approval" and up.get("row_count") == 5
               and up.get("accepted_count") == 2 and set(rej) == {"unresolved", "future_date", "bad_number"}
               and rej["future_date"]["reason"] == UP.REJECT_REASONS["future_date"]
               and (up.get("mapping") or {}).get("Px Date") == "date" and (up.get("mapping") or {}).get("Symbol") ==
               "ticker" and up.get("licence", {}).get("member_display") == "allowed"
               and up.get("licence", {}).get("retention_after_cancel") == "delete"),
          "staged: rep3's header detection + synonyms (Symbol, Close) + an explicit mapping (Px Date); 3 rows "
          "rejected each with its reason; 2 accepted; the uploader's licence recorded", f"{c} {json.dumps(o)[:400]}")
    S["upload"] = up.get("id")
    n_before = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.securities_global_prices WHERE "
                                             "global_security_id = ANY($1::uuid[])", [z, x], fetch="fetchval"))
    pre = await get(ORG_A, "eod_price", z)
    check(bool(n_before == 0 and not pre.found), "nothing counts before approval: no store row, the layer says "
          "'not priced'", f"{n_before} {pre.found}")
    cm, om = await api(U_ADMIN1, "POST", f"/api/v1/security-data/uploads/{S['upload']}/decision", {"approve": True})
    cv, ov = await api(U_VIEWER, "POST", f"/api/v1/security-data/uploads/{S['upload']}/decision", {"approve": True})
    cb, _ = await api(U_ADMIN_B, "POST", f"/api/v1/security-data/uploads/{S['upload']}/decision", {"approve": True})
    check(bool(cm == 403 and "maker-checker" in str(om) and cv == 403 and "manage_org_settings" in str(ov)
               and cb == 404),
          "maker-checker: the uploader cannot approve their own file; a viewer is refused naming the permission; "
          "another org's admin gets 404", f"{cm} {cv} {cb}")
    todo = int(await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND source = $2 AND "
                                         "related_id = $3::uuid AND status = 'open' AND user_id = $4", ORG_A,
                            UP.TODO_SOURCE, S["upload"], U_ADMIN2, fetch="fetchval"))
    ca, oa = await api(U_ADMIN2, "POST", f"/api/v1/security-data/uploads/{S['upload']}/decision", {"approve": True})
    rows = await scoped(conn, ORG_A, "SELECT global_security_id, price, provider_key, org_id, upload_id, licence FROM "
                                     "portfolio.securities_global_prices WHERE global_security_id = ANY($1::uuid[]) "
                                     "AND valid_to IS NULL AND system_to IS NULL", [z, x])
    hist = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.data_uploads WHERE id = $1::uuid",
                            S["upload"], fetch="fetchval"))
    check(bool(todo == 1 and ca == 200 and (oa.get("upload") or {}).get("status") == "approved"
               and oa["upload"].get("written") == 2 and len(rows) == 2
               and all(r["provider_key"] == "csv_upload" and r["org_id"] == ORG_A and str(r["upload_id"]) == S["upload"]
                       for r in rows) and hist == 2),
          "the checker approves (their to-do was open): 2 rows enter the store as the org's own csv_upload rows with "
          "upload_id; the upload kept its id (system-axis: the pending row archived)", f"{todo} {ca} {len(rows)} {hist}")
    post = await get(ORG_A, "eod_price", z)
    lic_row = json.loads(rows[0]["licence"]) if rows and isinstance(rows[0]["licence"], str) else (rows[0]["licence"]
                                                                                                   if rows else {})
    check(bool(post.found and post.provider_key == "csv_upload" and post.value == Decimal("42.50") and post.as_of == d0
               and post.licence.get("retention_after_cancel") == "delete" and lic_row.get("member_display") == "allowed"),
          "after approval the layer prices Z from the upload, with the FILE's licence", json.dumps(post.as_dict(),
                                                                                                 default=str)[:300])
    xb = await get(ORG_B, "eod_price", z)
    seen_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.securities_global_prices WHERE "
                                           "global_security_id = $1", z, fetch="fetchval"))
    check(bool(not xb.found and seen_b == 0), "org B never sees org A's uploaded price (RLS and the layer)",
          f"{xb.found} {seen_b}")
    cd, _ = await _csv_upload(U_ADMIN1, csv, "eod_price", lic, {"Px Date": "date"})
    check(cd == 422, "the same file again is refused", f"{cd}")
    # a second file, rejected with a note: nothing written
    csv2 = f"Index,Date,Level\n{PREFIX}UPIDX,{d0.isoformat()},101.5\n"
    c2, o2 = await _csv_upload(U_ADMIN1, csv2, "index_level", lic, name="idx.csv")
    uid2 = (o2.get("upload") or {}).get("id")
    cr0, _ = await api(U_ADMIN2, "POST", f"/api/v1/security-data/uploads/{uid2}/decision", {"approve": False})
    cr, orj = await api(U_ADMIN2, "POST", f"/api/v1/security-data/uploads/{uid2}/decision",
                        {"approve": False, "note": "wrong vendor file"})
    nidx = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.index_levels WHERE index_key = $1",
                            f"{PREFIX}UPIDX", fetch="fetchval"))
    check(bool(c2 == 201 and cr0 == 422 and cr == 200 and (orj.get("upload") or {}).get("status") == "rejected"
               and nidx == 0),
          "a rejection needs a note; a rejected file writes nothing", f"{c2} {cr0} {cr} {nidx}")
    # corporate actions through the service (same machinery): a dividend and a split, one bad row
    csv3 = ("Ticker,Action,Ex Date,Pay Date,Amount,Ratio From,Ratio To\n"
            f"{tk('X')},Dividend,{ago(10).isoformat()},{ago(5).isoformat()},0.25,,\n"
            f"{tk('X')},Split,{ago(20).isoformat()},,,1,2\n"
            f"{tk('X')},Merger,{ago(30).isoformat()},,,,\n")
    st = await pool_call(lambda pool: UP.stage(pool, org_id=str(ORG_A), actor_id=str(U_ADMIN1),
                                               data_type="corporate_action", data=csv3.encode(), filename="ca.csv",
                                               licence=lic))
    dec = await pool_call(lambda pool: UP.decide(pool, org_id=str(ORG_A), actor_id=str(U_ADMIN2), upload_id=st["id"],
                                                 approve=True))
    ca_ = await get(ORG_A, "corporate_action", x)
    acts = {a_["action_type"]: a_ for a_ in (ca_.value or [])}
    check(bool(st["accepted_count"] == 2 and [r["code"] for r in st["rejects"]] == ["bad_action"]
               and dec["written"] == 2 and ca_.found and ca_.provider_key == "csv_upload"
               and acts.get("cash_dividend", {}).get("terms", {}).get("amount_per_share") == "0.25"
               and acts.get("split", {}).get("terms", {}).get("ratio_to") == "2"
               and acts["cash_dividend"]["ex_date"] == ago(10).isoformat()),
          "corporate actions from a file: a dividend (ex / pay date, amount) and a split (ratio) recorded after "
          "approval; an unknown action rejected with its reason", f"{st['rejects']} {acts}")


async def y_adapters(conn):
    section("[Y] Free adapters: FRED, N-PORT / issuer file, adviser entry; vendor stub not connected")
    # FRED: recorded observations through FredClient (injectable transport) into index_levels
    tr = FredFixtureTransport([(ago(3), "4.10"), (ago(2), "."), (ago(1), "4.20")])
    fp = FredProvider(transport=tr, environ={"FRED_API_KEY": "zzmd1-fixture-key-not-real"})
    out = await pool_call(lambda pool: fp.fetch(pool, "index_level", [FRED_CODE], today=today(), budget_left=5))
    reg = {**registry(), "fred": fp}
    r = await in_org(lambda c: SD.get(c, str(ORG_A), "index_level", FRED_CODE, as_of=today(), providers=reg))
    check(bool(out.calls == 1 and out.written == 2 and not out.errors and len(tr.calls) == 1 and r.found
               and r.provider_key == "fred" and r.value == Decimal("4.20") and r.as_of == ago(1)),
          "FRED (public_api): one call through the existing FredClient, '.' skipped, 2 levels written; the layer "
          "reads the latest as an index level", f"{out} {r.as_dict()}")
    out2 = await pool_call(lambda pool: fp.fetch(pool, "index_level", [FRED_CODE], today=today(), budget_left=5))
    check(bool(out2.written == 0), "FRED re-run: unchanged values write nothing (Rule 3 diff)", f"{out2}")
    # N-PORT / issuer file: read = the latest LIVE snapshot through that source, in rank order
    fund, fund2 = S["sec_FUND"], S["sec_FUND2"]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for f, key, as_of in ((fund, "sec_nport", ago(70)), (fund, "ssga_spdr", ago(1)), (fund2, "sec_nport", ago(70))):
            await conn.execute(
                "INSERT INTO portfolio.replica_holdings_snapshots (fund_global_security_id, as_of, source_key, "
                "file_sha256, row_count, status, provider_key) VALUES ($1, $2, $3, $4, 3, 'live', $3)",
                f, as_of, key, hashlib.sha256(f"{TAG}{f}{key}".encode()).hexdigest())
    h1 = await get(ORG_A, "fund_holdings", fund, max_age_days=400)
    h2 = await get(ORG_A, "fund_holdings", fund2, max_age_days=400)
    h3 = await get(ORG_A, "fund_holdings", fund2)
    check(bool(h1.found and h1.provider_key == "ssga_spdr" and h2.found and h2.provider_key == "sec_nport"
               and h2.as_of == ago(70) and not h3.found and any("stale" in t["why"] for t in h3.tried)),
          "holdings: the issuer file (rank 1) wins where it exists; N-PORT (rank 2) serves the other fund; a 70-day-old "
          "N-PORT snapshot is stale at the 35-day default", f"{h1.provider_key} {h2.provider_key} {h3.missing}")
    seen = {}

    async def fake_ingest(pool, **kw):
        seen.update(kw)
        return H.IngestResult(kw.get("fund_global_security_id") or "", kw["ticker"], kw["source"]["key"], True)

    orig = H.ingest
    H.ingest = fake_ingest
    try:
        mock = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(599)))
        np_ = NportProvider(http=mock)
        res = await pool_call(lambda pool: np_.fetch_fund(pool, fund_gsid=None, ticker=tk("FUND"),
                                                          series_id="S000ZZMD1", dry_run=True))
        await mock.aclose()
    finally:
        H.ingest = orig
    check(bool(seen.get("source", {}).get("key") == "sec_nport" and seen.get("http") is mock
               and seen.get("dry_run") is True and res.dry_run is True),
          "N-PORT provider fetch = rep1's ingest with rep1's own source row and the INJECTED http client (re-homed, "
          "not rewritten)", f"{ {k: str(v)[:40] for k, v in seen.items()} }")
    # adviser entry: a rep8 quote through the layer, internal only. rep8's guard admits a 'premium' only on an
    # option contract (option_contracts row), so the proof uses an index-level quote on a fixture index.
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", str(ORG_A))
        await conn.execute("INSERT INTO portfolio.adviser_quotes (org_id, global_security_id, kind, value, source, as_of, "
                           "entered_by) VALUES ($1, $2, 'index_level', 5012.5, $3, $4, $5)", ORG_A, S["sec_IDX"],
                           f"{TAG} desk quote", now() - timedelta(minutes=10), U_ADMIN1)
    q = await get(ORG_A, "index_level", S["sec_IDX"])
    qm = await get(ORG_A, "index_level", S["sec_IDX"], purpose="member_display")
    qo = await get(ORG_A, "option_quote", S["sec_IDX"])
    check(bool(q.found and q.provider_key == "adviser_entry" and q.value == Decimal("5012.5")
               and q.detail.get("source_text") == f"{TAG} desk quote" and q.detail.get("quote_kind") == "index_level"
               and not qm.found and not qo.found),
          "adviser entry: rep8's index-level quote is the layer's index_level with its source text (after FRED and "
          "uploads in the rank); never shown to members; not an option quote", f"{q.as_dict()} {qm.missing}")
    # vendor slot: registered not connected; in a rank it changes nothing; no network; cannot be switched on
    vrow = await platform(conn, "SELECT status, lic_internal_use FROM portfolio.data_providers WHERE key = "
                                "'vendor_slot' AND valid_to IS NULL AND system_to IS NULL", fetch="fetchrow")
    base = await get(ORG_A, "eod_price", S["sec_X"])
    await set_org_rank_raw(conn, ORG_A, "eod_price", ["vendor_slot", "custodian"])
    withv = await get(ORG_A, "eod_price", S["sec_X"])
    vs = VendorStubProvider("vendor_slot", transport=RecordingTransport())
    vf = await pool_call(lambda pool: vs.fetch(pool, "eod_price", [str(S["sec_X"])], today=today(), budget_left=None))
    refused = None
    try:
        await pool_call(lambda pool: SDA.update_provider(pool, key="vendor_slot", changes={"status": "active"},
                                                         actor_id=str(U_SUPER)))
    except SecurityDataError as exc:
        refused = str(exc)
    org_refused = None
    try:
        await pool_call(lambda pool: SDA.set_org_rank(pool, org_id=str(ORG_A), data_type="eod_price",
                                                      keys=["vendor_slot", "custodian"], actor_id=str(U_ADMIN1)))
    except SecurityDataError as exc:
        org_refused = str(exc)
    await set_org_rank_raw(conn, ORG_A, "eod_price", [])
    check(bool(vrow and vrow["status"] == "not_connected" and vrow["lic_internal_use"] is False
               and withv.found and withv.value == base.value and withv.provider_key == base.provider_key
               and withv.tried[0] == {"provider": "vendor_slot", "why": "not connected"}
               and vf.calls == 0 and VENDOR_CALLS == [] and refused and "not connected" in refused
               and org_refused and "not an allowed provider" in org_refused),
          "vendor stub: registered 'not connected' with no rights; put first in a rank it is skipped and the answer "
          "is unchanged; zero transport calls; it cannot be set active; an org cannot rank it",
          f"{withv.tried[:2]} {vf} {VENDOR_CALLS} {refused} {org_refused}")


async def y_quality(conn):
    section("[Y] Quality events, both stale edges, gap / outlier, budget exhausted → next source")
    fi = FAKES[f"{PKEY}int"]
    fd = FAKES[f"{PKEY}disp"]
    z = S["sec_Y"]
    await set_org_rank_raw(conn, ORG_A, "eod_price", [f"{PKEY}int", f"{PKEY}disp"])
    lim = await in_org(lambda c: SDS.stale_days(c, str(ORG_A), "eod_price"))
    fi.set("eod_price", z, "20", ago(lim))
    at = await get(ORG_A, "eod_price", z)
    fi.set("eod_price", z, "20", ago(lim + 1))
    fd.set("eod_price", z, "21", ago(lim + 1))
    over = await get(ORG_A, "eod_price", z)
    check(bool(lim == 5 and at.found and at.age_days == lim and not over.found
               and all(t["why"].startswith("stale") for t in over.tried if t["provider"].startswith(PKEY))
               and f"{lim + 1} days old (limit {lim})" in over.missing),
          "stale, both edges: exactly at the setting (5 days) is used; one day over is skipped as stale and, with no "
          "fresher source, 'not priced' says so", f"{at.age_days} {over.missing}")
    fi.clear()
    fd.clear()
    await set_org_rank_raw(conn, ORG_A, "eod_price", [])
    # gap / outlier rules, both edges, pure
    mon = today() - timedelta(days=today().weekday() + 35)       # a Monday five weeks back (every point ≤ today)
    g_ok = Q.gaps([(mon, Decimal(1)), (mon + timedelta(days=4), Decimal(1))], 3)     # Mon → Fri: 3 weekdays between
    g_bad = Q.gaps([(mon, Decimal(1)), (mon + timedelta(days=7), Decimal(1))], 3)    # Mon → Mon: 4 weekdays between
    series = [(mon + timedelta(days=i), Decimal(100) + Decimal(i % 2)) for i in range(25)]
    med_move = Decimal(1) / Decimal(100)
    under = series + [(mon + timedelta(days=25), series[-1][1] * (1 + med_move * 9))]
    over_ = series + [(mon + timedelta(days=25), series[-1][1] * (1 + med_move * 12))]
    o_under = Q.outliers(under, Decimal(10), 20)
    o_over = Q.outliers(over_, Decimal(10), 20)
    check(bool(g_ok == [] and len(g_bad) == 1 and g_bad[0]["weekdays_missing"] == 4 and o_under == []
               and len(o_over) == 1 and o_over[0]["date"] == (mon + timedelta(days=25)).isoformat()),
          "gap (3 weekdays missing passes, 4 is a gap) and outlier (9× the median move passes, 12× is flagged)",
          f"{g_ok} {g_bad} {o_under} {o_over}")
    # the nightly's platform quality over fixture series only
    pts_gap = [(mon, Decimal(5)), (mon + timedelta(days=8), Decimal(5))]
    await pool_call(lambda pool: W.upsert_index_levels(pool, GAP_KEY, pts_gap, provider_key=f"{PKEY}int"))
    await pool_call(lambda pool: W.upsert_index_levels(pool, OUT_KEY, over_, provider_key=f"{PKEY}int"))
    run = await pool_call(lambda pool: SDN.run_nightly(pool, providers=registry(), trigger="test", today=today(),
                                                       steps=("platform_quality",), only_keys=[GAP_KEY, OUT_KEY]))
    ev = {(r["subject_key"], r["kind"]) for r in await platform(
        conn, "SELECT subject_key, kind FROM portfolio.data_quality_events WHERE run_id = $1", run.run_id)}
    real_ev = int(await platform(conn, "SELECT count(*) FROM portfolio.data_quality_events WHERE run_id = $1 AND NOT "
                                       "(subject_key LIKE $2)", run.run_id, f"{PREFIX}%", fetch="fetchval"))
    check(bool({(GAP_KEY, "gap"), (GAP_KEY, "stale"), (OUT_KEY, "outlier")} <= ev and real_ev == 0
               and run.status == "success"),
          "nightly platform quality records gap, stale and outlier events per item and day (fixture series only)",
          f"{sorted(ev)} {real_ev} {run.status} {run.errors}")
    # budget: fa (2 calls/day) then fb; three series
    fa, fb = FAKES[f"{PKEY}fa"], FAKES[f"{PKEY}fb"]
    ranks = {"index_level": [f"{PKEY}fa", f"{PKEY}fb"]}
    cov = lambda dt, k: list(IDX_KEYS) if dt == "index_level" else []  # noqa: E731
    r1 = await pool_call(lambda pool: SDN.run_nightly(pool, providers=registry(), trigger="test", today=today(), steps=("refresh",),
                                                      ranks=ranks, coverage_fn=cov))
    calls = {r["provider_key"]: int(r["n"]) for r in await platform(
        conn, "SELECT provider_key, sum(calls) AS n FROM portfolio.provider_call_log WHERE provider_key LIKE $1 AND "
              "call_date = $2 GROUP BY 1", f"{PKEY}%", today())}
    lv = {(r["index_key"], r["provider_key"]) for r in await platform(
        conn, "SELECT index_key, provider_key FROM portfolio.index_levels WHERE index_key = ANY($1::text[]) AND "
              "valid_to IS NULL AND system_to IS NULL", IDX_KEYS)}
    be = await platform(conn, "SELECT subject_key, provider_key FROM portfolio.data_quality_events WHERE run_id = $1 "
                              "AND kind = 'budget_exhausted'", r1.run_id)
    check(bool(fa.fetch_calls == IDX_KEYS[:2] and fb.fetch_calls == IDX_KEYS[2:] and calls == {f"{PKEY}fa": 2,
                                                                                              f"{PKEY}fb": 1}
               and lv == {(IDX_KEYS[0], f"{PKEY}fa"), (IDX_KEYS[1], f"{PKEY}fa"), (IDX_KEYS[2], f"{PKEY}fb")}
               and [(r["subject_key"], r["provider_key"]) for r in be] == [(IDX_KEYS[2], f"{PKEY}fa")]),
          "budget: the first source spends its 2 calls, the third series is a 'budget_exhausted' event and is "
          "fetched by the NEXT source in the rank; the call log sums per provider and day",
          f"{fa.fetch_calls} {fb.fetch_calls} {calls} {sorted(lv)} {[dict(r) for r in be]}")
    r2 = await pool_call(lambda pool: SDN.run_nightly(pool, providers=registry(), trigger="test", today=today(), steps=("refresh",),
                                                      ranks=ranks, coverage_fn=cov))
    # one LIVE quality event per item, day, kind and provider (data_quality_events_live_uq): IDX3's event from the
    # first run stands, so the second run adds IDX1 and IDX2 — the day holds exactly three, one per series
    be2 = int(await platform(conn, "SELECT count(*) FROM portfolio.data_quality_events WHERE kind = 'budget_exhausted' "
                                   "AND provider_key = $1 AND subject_key = ANY($2::text[]) AND event_date = $3 AND "
                                   "valid_to IS NULL AND system_to IS NULL", f"{PKEY}fa", list(IDX_KEYS), today(),
                             fetch="fetchval"))
    check(bool(fa.fetch_calls == IDX_KEYS[:2] and fb.fetch_calls == IDX_KEYS[2:] + IDX_KEYS and be2 == 3),
          "same day again: the first source's budget is spent (0 left) — every series goes to the next source",
          f"{fa.fetch_calls} {fb.fetch_calls} {be2}")
    runs = await platform(conn, "SELECT id, status, summary FROM portfolio.security_data_runs WHERE id = ANY($1::uuid[])",
                          [run.run_id, r1.run_id, r2.run_id])
    check(len(runs) == 3, "one summary row per nightly run", f"{len(runs)}")
    # org quality: stale / disagreeing items feeding open proposals → events + a to-do (feed injected) ; test org
    feed_real = await in_org(lambda c: SDN.open_proposal_securities(c, str(ORG_A)))
    find("org quality feed", "the open-proposal feed query (open_proposal_securities) is proven to run; the items it "
         "would return are INJECTED here (seeding a real proposal needs rep5's full proposal path)")
    await set_org_rank_raw(conn, ORG_A, "eod_price", [f"{PKEY}int", f"{PKEY}disp"])
    fi.set("eod_price", S["sec_X"], "50", ago(1))
    fd.set("eod_price", S["sec_X"], "60", ago(1))
    fi.set("eod_price", S["sec_Z"], "50", ago(30))
    fake_pid = str(uuid.uuid4())

    async def feed(c, org):
        return {str(S["sec_X"]): [fake_pid], str(S["sec_Z"]): [fake_pid]}

    skipped = await pool_call(lambda pool: SDN.run_nightly(pool, providers=registry(), trigger="test", today=today(),
                                                           steps=("org_quality",), only_org_ids=[str(ORG_A)],
                                                           feed_fn=feed))
    r3 = await pool_call(lambda pool: SDN.run_nightly(pool, providers=registry(), trigger="test", today=today(), steps=("org_quality",),
                                                      only_org_ids=[str(ORG_A)], include_test_orgs=True, feed_fn=feed))
    oe = {r["subject_key"]: r["kind"] for r in await scoped(
        conn, ORG_A, "SELECT subject_key, kind FROM portfolio.data_quality_events WHERE run_id = $1 AND org_id = $2",
        r3.run_id, ORG_A)}
    todos = int(await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND source = $2 AND "
                                          "status = 'open'", ORG_A, SDN.TODO_SOURCE, fetch="fetchval"))
    seen_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.data_quality_events WHERE org_id = $1",
                              ORG_A, fetch="fetchval"))
    check(bool(feed_real == {} and skipped.orgs_skipped_test == 1 and skipped.orgs_checked == 0
               and oe == {str(S["sec_X"]): "disagreement", str(S["sec_Z"]): "stale"} and r3.todos >= 2 and todos >= 2
               and seen_b == 0),
          "org data: a test org is skipped by default; included, the disagreeing (X) and stale (Z) items feeding open "
          "proposals become org events + to-dos for the org's admins; org B cannot see them",
          f"{skipped.orgs_skipped_test} {oe} {r3.todos} {todos} {seen_b}")
    fi.clear()
    fd.clear()
    await set_org_rank_raw(conn, ORG_A, "eod_price", [])


def _q3_rows(keys):
    return [{"row_num": i + 1, "weight": Decimal(1) / Decimal(len(keys)), "asset_type": "equity",
             "resolution_state": "resolved", "constituent_global_security_id": str(S[f"sec_{k}"]),
             "raw_ticker": tk(k), "raw_name": SEC_NAMES[k], "raw_cusip": None, "raw_isin": None, "raw_sedol": None}
            for i, k in enumerate(keys)]


async def _q3(keys, size=Decimal(1000000)):
    src = RM.Sources(entry={}, terms=None, snapshot={"id": uuid.uuid4()}, rows=_q3_rows(keys))
    return await in_org(lambda c: RV.q3_account_size(c, str(ORG_A), src, custodian_code="altruist", account_size=size,
                                                     p=RM.BuildParams(custodian_code="altruist")))


async def y_readers(conn):
    section("[Y] Readers switched; no-data behaviour unchanged; with a custodian valuation the figure appears with "
            "source + as-of; no vendor names (scan)")
    since = ago(RP.DEFAULT_STALE_DAYS)
    z, x = S["sec_Y"], S["sec_X"]
    # no data anywhere: Y is held only by member 2 (custodian) — use a security nobody holds: FUND2 (no price)
    nod = str(S["sec_FUND2"])
    p0 = await in_org(lambda c: RP._unheld_prices(c, [nod], since, org_id=str(ORG_A), today=today()))
    q0 = await _q3(["FUND2"])
    max_age = int(RV.SETTINGS["replication.viability.price_max_age_days"])
    want_reason = (f"unknown — needs prices for 1 of 1 constituents (a close no older than {max_age} days); prices are "
                   "never estimated.")
    check(bool(p0 == {} and q0["status"] == "unknown" and q0["reason"] == want_reason
               and q0["missing"] == [f"price: {tk('FUND2')} (no close price)"]
               and q0["inputs"].get("price_data_type") == "eod_price"),
          "no data anywhere: proposals / harvest / one-off buys get no price (bought by amount, as today); viability "
          "Q3 returns today's exact message and missing list, and names the missing data type",
          f"{p0} {q0['reason']} {q0['missing']}")
    check(bool(XR.NEEDS_PRICES == "not estimated — needs prices"
               and all(v.startswith("eod_price history") for v in XR.NEEDS_PRICES_MISSING.values())
               and RV.TAX_LINE_TEXT == "Estimated by the tax engine (rep6) — not estimated here"),
          "X-ray: tracking error / harvest value / after-tax performance keep today's 'not estimated — needs prices' "
          "and name the missing data type (eod_price history — no free source carries one)")
    prov: dict = {}
    p1 = await in_org(lambda c: RP._unheld_prices(c, [str(x)], since, org_id=str(ORG_A), today=today(),
                                                  provenance=prov))
    hit = p1.get(str(x))
    check(bool(hit is not None and hit[0] == (X_MV / X_QTY).quantize(E.QTY) and hit[1] == ago(1)
               and hit[2] == "security_price" and prov.get(str(x), {}).get("provider_key") == "custodian"
               and prov[str(x)].get("as_of") == ago(1).isoformat()),
          "proposals / harvest replacements / one-off buys: an unheld security the org holds elsewhere is now priced "
          "from the custodian valuation (price, as-of; provider in the provenance)", f"{hit} {prov.get(str(x))}")
    q1 = await _q3(["X"])
    srcs = q1["inputs"].get("price_sources") or []
    check(bool(q1["status"] in ("yes", "no") and q1["inputs"]["priced"] == 1
               and q1["inputs"].get("oldest_price_date") == ago(1).isoformat()
               and srcs and srcs[0]["provider"] == "custodian" and srcs[0]["oldest_as_of"] == ago(1).isoformat()
               and (q1["inputs"].get("minimum_account") or {}).get("minimum")),
          "viability Q3: with a custodian valuation the minimum-account figure appears, with its source and as-of",
          f"{q1['status']} {q1['reason']} {srcs}")
    pv = await in_org(lambda c: SD.get(c, str(ORG_A), "eod_price", str(x), as_of=today(), account_ids=[str(ACCT_1)],
                                       max_age_days=36500))
    xsrc = (API_DIR / "services" / "xray.py").read_text(encoding="utf-8")
    check(bool(pv.found and pv.value == X_MV / X_QTY and pv.provider_key == "custodian" and "price_provenance" in xsrc
               and 'SD.get(conn, org_id, "eod_price", sec' in xsrc),
          "X-ray embedded gain: the account-scoped custodian value equals the line's own market value ÷ quantity "
          "exactly; X-ray publishes its source + as-of (price_provenance)", f"{pv.value}")
    hits = []
    for rel in READER_FILES:
        for i, line in enumerate((API_DIR / rel).read_text(encoding="utf-8").splitlines(), start=1):
            if VENDOR_NAMES.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:80]}")
    check(not hits, f"static scan: no vendor name in any reader or in the layer's walk ({len(READER_FILES)} files)",
          f"{hits[:5]}")


async def y_mkt_unchanged(conn):
    section("[Y] mkt01–03 indicator behaviour unchanged (read gate, nightly, store)")
    code, out, _ = run_cmd(["git", "diff", "--stat", BEFORE_REF, "--", "apps/api/services/market_data",
                            "apps/api/scripts/market_data_nightly.py", "apps/api/routers/market_data.py"], REPO, 60)
    check(bool(code == 0 and out.strip() == ""), "services/market_data, the indicator nightly entrypoint and its "
          "router are byte-identical to the base", out.strip()[:300])
    from services.market_data import access as MA
    check(bool(MA.MARKET_DATA_READ_PERMISSION is None and callable(MA.require_market_data_read)),
          "the indicator read gate (market_data.access) is unchanged")
    srcs = [(API_DIR / "services" / "security_data" / p).read_text(encoding="utf-8")
            for p in ("layer.py", "nightly.py", "writes.py")]
    check(bool(not any("indicator_observations" in s_ or "indicator_series" in s_ for s_ in srcs)),
          "the security-data layer never reads or writes the indicator store")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(bool(who["u"] == "app_service" and who["b"] is False), "the verify connection is app_service with "
          "rolbypassrls = false", f"{who['u']} {who['b']}")
    cb, ob = await _csv_upload(U_ADMIN_B, f"Index,Date,Level\n{PREFIX}BIDX,{ago(1).isoformat()},7\n", "index_level",
                               {"internal_use": True, "member_display": "not_allowed", "redistribution": False,
                                "retention_after_cancel": "keep"}, name="b.csv")
    res = {}
    for t in NEW_ORG_TABLES + ("portfolio.data_source_rank", "portfolio.securities_global_prices",
                               "portfolio.data_quality_events"):
        res[t] = (int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")),
                  int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")))
    bb = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.data_uploads WHERE org_id = $1", ORG_B,
                          fetch="fetchval"))
    ba = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.data_uploads WHERE org_id = $1", ORG_B,
                          fetch="fetchval"))
    check(bool(cb == 201 and all(a > 0 and b == 0 for a, b in res.values()) and bb == 1 and ba == 0),
          "RLS: org A's rows (entitlements, uploads, rank overrides, uploaded prices, quality events) are non-empty "
          "and invisible to org B; org B's upload invisible to org A", f"{res} {bb} {ba}")
    _, lb = await api(U_ADMIN_B, "GET", "/api/v1/security-data/uploads")
    check(bool(len(lb.get("uploads") or []) == 1 and lb["uploads"][0]["file_name"] == "b.csv"),
          "org B's upload list shows only its own file", f"{[u.get('file_name') for u in lb.get('uploads') or []]}")
    body = {"providers": ["csv_upload", "custodian"]}
    cv, ov = await api(U_VIEWER, "PUT", "/api/v1/security-data/rank/eod_price", body)
    ca, oa = await api(U_ADMIN1, "PUT", "/api/v1/security-data/rank/eod_price", body)
    cx, ox = await api(U_ADMIN1, "PUT", "/api/v1/security-data/rank/eod_price", {"providers": ["vendor_slot"]})
    cc, _ = await api(U_ADMIN1, "PUT", "/api/v1/security-data/rank/eod_price", {"providers": []})
    check(bool(cv == 403 and "manage_org_settings" in str(ov) and ca == 200
               and oa.get("org_override") == ["csv_upload", "custodian"] and cx == 422
               and "not an allowed provider" in str(ox) and cc == 200),
          "rank override gate both ways: the viewer is refused naming manage_org_settings, the org admin accepted; "
          "a provider outside the platform rank refused; [] clears the override", f"{cv} {ca} {cx} {cc}")
    cvp, ovp = await api(U_VIEWER, "GET", "/api/v1/admin/security-data/providers")
    cap, _ = await api(U_ADMIN1, "GET", "/api/v1/admin/security-data/providers")
    csp, osp = await api(U_SUPER, "GET", "/api/v1/admin/security-data/providers")
    cpu, _ = await api(U_ADMIN1, "PUT", "/api/v1/admin/security-data/providers/csv_upload", {"daily_call_budget": 5})
    keys = {p_["key"] for p_ in osp.get("providers") or []}
    check(bool(cvp == 403 and "Super Admin" in str(ovp) and cap == 403 and cpu == 403 and csp == 200
               and {"custodian", "sec_nport", "ssga_spdr", "fred", "csv_upload", "adviser_entry", "vendor_slot",
                    "platform_entry"} <= keys
               and (osp.get("permissions") or {}).get("can_write") is True
               and (osp.get("vocabularies") or {}).get("editable") == list(SDA.PROVIDER_FIELDS)),
          "provider admin: Super Admin only (viewer and org admin 403, also on PUT); the super admin's envelope lists "
          "the editable fields", f"{cvp} {cap} {cpu} {csp}")
    _, ev = await api(U_VIEWER, "GET", "/api/v1/security-data/rank")
    pv, vv = ev.get("permissions") or {}, ev.get("vocabularies") or {}
    _, eq = await api(U_VIEWER, "GET", "/api/v1/security-data/quality")
    check(bool(pv.get("can_read") is True and pv.get("can_write") is False and vv.get("rank_editable") == []
               and vv.get("upload_editable") == [] and vv.get("editable") == [] and bool(vv.get("data_types"))
               and bool(vv.get("quality_kinds")) and isinstance(eq.get("events"), list)),
          "view-only envelope: can_read, no write flag, empty editable lists; data types / quality kinds from the "
          "envelope", f"{pv} {sorted(vv)[:8]}")


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now_ = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now_)
    changed = [k for k in gb if k in gn and (gb[k].get("after"), gb[k].get("expect")) !=
               (gn[k].get("after"), gn[k].get("expect"))]
    removed = [k for k in gb if k not in gn]
    check(bool(len(gb) > 0 and not changed and not removed), "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    mine = {k: v for k, v in gn.items() if "/security-data" in k[1]}
    bad = [(k, v.get("after")) for k, v in mine.items() if NEW_ROUTES.get((k[0][0] if k[0] else "", k[1])) is None
           or NEW_ROUTES[(k[0][0], k[1])] not in str(v.get("after"))]
    missing = [k for k in NEW_ROUTES if not any(k == (m[0][0], m[1]) for m in mine)]
    check(bool(len(mine) == len(NEW_ROUTES) and not bad and not missing),
          f"this sprint's {len(NEW_ROUTES)} routes exist, each naming its gate", f"{len(mine)} {bad[:4]} {missing[:4]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(bool(code == 0 and re.search(r"^# fail 0", out, re.M) is not None), "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^ok \d+ - (?:super admin view:|no flags / no envelope:|org view:|quality view|hover line:)",
                      out, re.M)
    check(bool(len(mine) >= 5 and "not ok" not in out), "the marketdata1 render tests ran and passed (no admin "
          "control without the server's flags)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep6.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep6.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total and total[-1][1] == "0" and not fails and code == 0), "verify_rep6.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-200:] if not total else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


DROPPED_CONNECTION = (asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError, ConnectionError)


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
    pre = real = None
    try:
        await run("pre-teardown", pre_teardown, conn)
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            registry()
            await run("rank walk", y_rank_walk, conn)
            await run("custodian", y_custodian, conn)
            await run("csv upload", y_csv, conn)
            await run("adapters", y_adapters, conn)
            await run("quality", y_quality, conn)
            await run("readers", y_readers, conn)
            await run("isolation + gates", y_isolation, conn)
        else:
            check(False, "seed created the fixtures", "every fixture-scoped section skipped")
        await run("mkt01–03 unchanged", y_mkt_unchanged, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()

        async def after_teardown(c):
            return (await leftovers(c), await counts(c) if pre is not None else None,
                    await real_fingerprints(c) if real is not None else None)

        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            try:
                left, post, after = await after_teardown(conn)
            except DROPPED_CONNECTION as exc:
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
