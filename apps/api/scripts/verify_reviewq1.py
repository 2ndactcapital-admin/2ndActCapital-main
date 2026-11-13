"""verify_reviewq1 — the Security master Review queue: per-row choice (Use incoming · Use existing · Keep both) + one Run,
bulk by filter, Known discrepancies (repeat suppression, seen again, move back), list misses → OpenFIGI → review items.

    timeout 600 python3 apps/api/scripts/verify_reviewq1.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_reviewq1.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_reviewq1.py --section a      # seed + ONE section + teardown
    python3 apps/api/scripts/verify_reviewq1.py                              # everything + fast regressions

Sections (each creates the review items it needs itself, on its own fixture securities — any one runs alone):
l READ-ONLY live (before seeding: the real open queue by kind / field / provider; no real Known item, sighting or
alternate) · g gates (every write 403 for a non-Super-Admin, nothing written; org_id in a body → 422) · a the three
actions through ONE Run (Use incoming = a new version, history kept; Use existing = nothing changes; Keep both = an
alternate, and a holding row / a search by the alternate resolve to the same security; no choice → stays open; ticker
collisions refused with the reason; audit rows) · b bulk by filter (count = the filter's own SQL count, inclusion AND
exclusion, a moved count refused, nothing written) · s repeat suppression in the real writer (the matcher, a fixture
provider AND finance_database): same value → seen again, not re-opened; a different value → a new item; move back →
open and the next run leaves it ONE open item; Keep both alternates seen again · m list misses (OpenFIGI stubbed: a
foreign-ISIN miss mapping to an existing security → list_match; confirming adds the alternate and the loader's row
resolution then resolves it; an unmappable one → add_to_master; nothing auto-merged; dry run writes nothing) · r RLS on
the sightings table under app_service · w render tests · j fast regressions (verify_secmaster1, verify_secmaster2,
verify_track1, verify_prices1 — --self-checks-only, VERIFY_REGRESSIONS=fast).

NO NETWORK AND NO MODEL CALL: httpx's transport refuses and records; OpenFIGI answers come from a recorded transport.
NEVER decides, changes or re-opens a REAL review item or a real security: every write names fixture ids; every bulk
filter carries the fixture provider; the real queue is fingerprinted (id, status, valid_to) before seeding and after
teardown, and its open counts by kind / field / provider must not move.

THE CLOCK: TODAY = the database's UTC date, read once at start; every matcher run is passed as_of = TODAY.

FIXTURES: orgs 99000000-0000-0000-0000-0000005a1001 (the fixture PLATFORM org — security_master_jobs.PLATFORM_ORG_ID
points at it for this process, so every audit row lands there); users …5a1101–103; securities named 'ZZQ1 VERIFY …' with
tickers ZZQ…; review provider 'zzq1_fx'; list keys 'zzq1_…'.

TEARDOWN (start and end): _verify_cleanup.cleanup on the operator DSN by fixture org / user / security ids, review
provider / subject, list keys, OpenFIGI mapping values; every counted table at its exact pre-test count.
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
import time
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

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "reviewq1"
BEFORE_REF = "a10c26a"   # reviewq1-work base: main @ 'chore: refresh schema snapshot' (track1 / prices2 / prices3 merged)
P_ = "/api/v1/admin/security-data/master"
NEW_ROUTES = {("POST", f"{P_}/review/run"), ("POST", f"{P_}/review/bulk/estimate"), ("POST", f"{P_}/review/bulk"),
              ("POST", f"{P_}/review/{{review_id}}/move-back")}
REGRESSIONS = ["verify_secmaster1.py", "verify_secmaster2.py", "verify_track1.py", "verify_prices1.py"]


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == NEW_ROUTES and not o.removed,
          "reviewq1's new routes are exactly run / bulk/estimate / bulk / move-back; none removed",
          f"{o.where()}; new={sorted(mine)} removed={o.removed[:3]}")
    check(not o.changed, "no existing route's gate changed", f"{o.changed[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    gates = {k: (by.get(k) or {}).get("after") for k in NEW_ROUTES}
    check(all(g == "super_admin" for g in gates.values()), "every new route is gated Super Admin", f"{gates}")
    drift = VS.still_gated(o, list(o.new))
    check(not drift, "the new routes still exist today with the same gate", f"{drift[:3]}")
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        p = subprocess.run([sys.executable, str(HERE / script), "--check"], cwd=REPO, capture_output=True, text=True,
                           timeout=900)
        check(p.returncode == 0 and "CURRENT" in p.stdout, f"{script} --check: CURRENT", (p.stdout + p.stderr)[-300:])


def self_static(check, find):
    notes = _read("docs/REVIEWQ1_NOTES.md")
    for tok in ("726", "insert_review", "uq_sec_global_ident_issuer_assigned", "is_alternate",
                "security_master_review_sightings", "kept_existing", "kept_both", "used_incoming", "get_by_identifier",
                "resolve_rows", "custodian_lots", "Not offered", "home_vs_us", "list_match", "add_to_master"):
        check(tok in notes, f"REVIEWQ1_NOTES names {tok!r} (evidence / decisions)")
    sql = _read("docs/reviewq1_part1.sql")
    check(all(x in sql for x in ("'used_incoming', 'kept_existing', 'kept_both'", "'list_match', 'add_to_master'",
                                 "ADD COLUMN IF NOT EXISTS is_alternate boolean NOT NULL DEFAULT false",
                                 "security_master_review_sightings", "FOR SELECT USING", "FOR INSERT WITH CHECK",
                                 "FOR UPDATE USING", "FOR DELETE USING", "GRANT SELECT, INSERT, UPDATE, DELETE")),
          "migration: statuses + kinds widened (old literals kept), is_alternate, the sightings table with a policy for "
          "every operation + app_service grants")
    check(all(x in sql for x in ("'open', 'resolved', 'dismissed'", "'ticker_conflict', 'ticker_cusip_conflict'")),
          "the old status / kind literals stay admitted")
    # never weaken an existing assertion: the secmaster1 render test and the legacy route are byte-identical / kept
    old_t = VS.git_show(BEFORE_REF, "apps/web/tests/secmaster1/secmaster1.test.mjs")
    check(bool(old_t) and old_t == _read("apps/web/tests/secmaster1/secmaster1.test.mjs"),
          "tests/secmaster1 (asserts Resolved / Dismissed) is unchanged")
    for v in ("verify_secmaster1.py", "verify_secmaster2.py", "verify_track1.py", "verify_prices1.py"):
        old = VS.git_show(BEFORE_REF, f"apps/api/scripts/{v}")
        check(bool(old) and old == _read(f"apps/api/scripts/{v}"), f"{v} unchanged (no assertion weakened)")
    jsx = _read("apps/web/components/securityData/SecurityMaster.jsx")
    check("export function ReviewQueueView" in jsx and "<ReviewPanel " in jsx and "<ReviewQueueView" not in jsx,
          "the screen renders the new ReviewPanel; the legacy ReviewQueueView stays exported (its test still holds)")
    r = _read("apps/api/routers/security_master.py")
    check('@router.post(f"{_P}/review/{{review_id}}/decision")' in r and 'OUTCOMES = {"resolved": "Resolved", '
          '"dismissed": "Dismissed"}' in r, "the legacy decision route and its outcomes are kept")
    load = _read("apps/api/services/security_master_load.py")
    check("RQ.sighting_if_known" in load and load.count("INSERT INTO portfolio.security_master_review ") == 1,
          "ONE writer (insert_review) inserts review items and consults the Known list first")
    writers = subprocess.run(["git", "grep", "-l", "INSERT INTO portfolio.security_master_review (", "--",
                              "apps/api/services", "apps/api/routers"], cwd=REPO, capture_output=True, text=True)
    files = sorted(writers.stdout.split())
    check(files == ["apps/api/routers/security_master.py", "apps/api/services/security_master_load.py",
                    "apps/api/services/security_master_review.py"],
          "every review-row INSERT: the writer (load), the legacy decision restatement (router), the choices' "
          "restatement (review service) — no provider writes around insert_review", f"{files}")
    check(subprocess.run(["git", "grep", "-n", "insert_review", "--", "apps/api/services/security_master_sync.py"],
                         cwd=REPO, capture_output=True, text=True).returncode == 0,
          "the 13(f) sync writes through insert_review too (suppression covers it)")
    regs = _read("scripts/regression_set.txt").split()
    check("verify_reviewq1.py" in regs, "verify_reviewq1.py is in scripts/regression_set.txt")


def self_logic(check, find):
    from services import security_master_review as RQ

    def offered(kind, field_, existing="US0000000001"):
        return RQ.choices_for({"kind": kind, "field": field_, "existing_value": existing})[0]
    check(offered("identifier_conflict", "isin") == ["use_incoming", "use_existing", "keep_both"],
          "an ISIN / CUSIP / FIGI disagreement offers all three")
    check(all(offered("identifier_conflict", f) == ["use_incoming", "use_existing", "keep_both"]
              for f in ("cusip", "figi")), "cusip / figi: all three")
    check(offered("identifier_conflict", "ticker") == ["use_incoming", "use_existing"], "a ticker: no Keep both")
    check(offered("identifier_conflict", "isin", "held by 00000000-0000-0000-0000-000000000000") == ["use_existing"],
          "a value held by ANOTHER security: Use existing only (one value, one security)")
    check(offered("type_mismatch", "security_type") == ["use_incoming", "use_existing"], "security type: no Keep both")
    check(offered("ticker_cusip_conflict", "ticker") == ["use_incoming", "use_existing"], "ticker + CUSIP: no Keep both")
    check(all(offered(k, "ticker") == ["use_existing"] for k in ("ticker_conflict", "ambiguous_match", "ticker_moved")),
          "ticker_conflict / ambiguous / ticker_moved: Use existing only")
    check(offered("list_match", "isin") == ["keep_both", "use_existing"] and
          offered("add_to_master", "isin", None) == ["use_incoming", "use_existing"], "list_match / add_to_master")
    w: list = []
    sql = RQ._where({"home_vs_us": "true", "provider": "x"}, ("open",), w)
    check(RQ.HOME_VS_US_SQL in sql and w == [["open"], "x"], "the home-vs-US filter is one SQL predicate, bound args")
    rx = re.compile(r"^US[A-Z0-9]{10}(, US[A-Z0-9]{10})*$")
    check(bool(rx.match("US0378331005")) and bool(rx.match("US0378331005, US0378331013")) and
          not rx.match("held by x") and not rx.match("CA0378331005"), "existing 'US…' (one or several) — the regex")
    for bad in ({"org_id": "x"}, {"kind": "nope"}, {"q": "a"}, {"home_vs_us": "yes"}, {"provider": "A;--"}):
        try:
            RQ.check_filters(bad)
            ok = False
        except RQ.ReviewError:
            ok = True
        check(ok, f"filter refused: {bad}")
    from services.security_data import security_lists as SL
    check(SL.miss_identifier({"isin": "BMG5480U1040", "cusip": "G5480U104"}) == ("isin", "BMG5480U1040") and
          SL.miss_identifier({"isin": "US63938C1080", "cusip": "63938C108"}) == ("cusip", "63938C108") and
          SL.miss_identifier({"name": "x"}) is None, "a miss maps by its home-market ISIN, a US one by its CUSIP")
    from services.security_data.providers import openfigi as OF
    check(OF.isin_job("BMG5480U1040") == {"idType": "ID_ISIN", "idValue": "BMG5480U1040", "exchCode": "US"},
          "OpenFIGI ISIN job: the US composite")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)
    self_logic(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import main  # noqa: E402
from services import extraction  # noqa: E402
from services import rbac  # noqa: E402
from services import replica_holdings as RH  # noqa: E402
from services import replica_parsers as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import security_master_load as M  # noqa: E402
from services import security_master_review as RQ  # noqa: E402
from services import security_master_summary as SUM  # noqa: E402
from services import security_search as SS  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.security_data import security_lists as SL  # noqa: E402
from services.security_data import tracking as TR  # noqa: E402
from services.security_data.providers.reference_common import SourceRecord, SourceTransport  # noqa: E402

TAG = "reviewq1verify"
TICKER_PREFIX = "ZZQ"
NAME_PREFIX = "ZZQ1 VERIFY"
LIST_PREFIX = "zzq1"
FX = "zzq1_fx"
VAL_LIKE = "%ZZQ1%"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000005a1{n:03d}")


ORG_P = _id(1)
FIXTURE_ORGS = [ORG_P]
U_SUPER, U_ADMIN, U_MEMBER = _id(101), _id(102), _id(103)
USERS = {U_SUPER: ("super_admin", "super", "super_admin", "REVIEWQ1 Super Admin"),
         U_ADMIN: ("org_admin", "admin", "org_admin", "REVIEWQ1 Org Admin"),
         U_MEMBER: ("member", "member", "zzq1_viewer", "REVIEWQ1 Member")}
#: fixture securities: key → (ticker, type, {id_type: value})
SECS = {
    "G1": ("ZZQZ", "equity", {"cusip": "ZZQ1C0090", "isin": "USZZQ1000090"}),
    "A1": ("ZZQA", "equity", {"cusip": "ZZQ1C0001", "isin": "USZZQ1000001"}),
    "A2": ("ZZQB", "equity", {"cusip": "ZZQ1C0002", "figi": "BBGZZQ1F0002"}),
    "A3": ("ZZQC", "equity", {"cusip": "ZZQ1C0003", "figi": "BBGZZQ1F0003"}),
    "A4": ("ZZQD", "equity", {}),
    "A5": ("ZZQE", "equity", {}),
    "A6": ("ZZQF", "equity", {}),
    "A7": ("ZZQG", "equity", {"cusip": "ZZQ1C0007"}),
    "A8": ("ZZQH", "equity", {"cusip": "ZZQ1C0008", "isin": "USZZQ1000008"}),
    "A9": ("ZZQI", "equity", {"cusip": "ZZQ1C0009"}),
    "B1": ("ZZQJ", "equity", {"cusip": "ZZQ1C0011", "isin": "USZZQ1000011"}),
    "B2": ("ZZQK", "equity", {"cusip": "ZZQ1C0012", "isin": "USZZQ1000012"}),
    "B3": ("ZZQL", "equity", {"cusip": "ZZQ1C0013", "isin": "USZZQ1000013"}),
    "B4": ("ZZQM", "equity", {"cusip": "ZZQ1C0014", "isin": "USZZQ1000014"}),
    "S1": ("ZZQN", "equity", {"cusip": "ZZQ1C0021", "isin": "USZZQ1000021"}),
    "S2": ("ZZQO", "equity", {"cusip": "ZZQ1C0022", "isin": "USZZQ1000022"}),
    "S3": ("ZZQQ", "equity", {"cusip": "ZZQ1C0023", "isin": "USZZQ1000023"}),
    "M1": ("ZZQP", "equity", {"figi": "BBGZZQ1F0031", "isin": "USZZQ1000031"}),
}

S: dict = {}
CLOCK: dict = {}
NETWORK_CALLS: list[str] = []
LLM_CALLS: list[str] = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][1]}"


def sec(k) -> UUID:
    return S["sec"][k]


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


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


FIXTURE_SECS_SQL = ("SELECT id FROM portfolio.securities_global WHERE name LIKE $1 UNION SELECT global_security_id FROM "
                    "portfolio.securities_global_identifiers WHERE id_type = 'ticker' AND id_value LIKE $2")


async def fixture_secs(conn) -> list:
    return [r[0] for r in await platform(conn, FIXTURE_SECS_SQL, f"{NAME_PREFIX}%", f"{TICKER_PREFIX}%")]


def counted_plan(secs: list) -> dict:
    rv = ("global_security_id = ANY($1::uuid[]) OR provider_key = $2 OR subject_key LIKE $3 OR incoming_value LIKE $3",
          [secs, FX, VAL_LIKE])
    return {
        "portfolio.security_master_review_sightings": rv,
        "portfolio.security_master_review": rv,
        "portfolio.openfigi_mappings": ("id_value LIKE $1", [VAL_LIKE]),
        "portfolio.tracked_securities": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.security_list_members": ("list_key LIKE $1 OR global_security_id = ANY($2::uuid[])",
                                            [f"{LIST_PREFIX}%", secs]),
        "portfolio.security_lists": ("list_key LIKE $1", [f"{LIST_PREFIX}%"]),
        "portfolio.security_reference_attributes": ("global_security_id = ANY($1::uuid[]) OR provider_key = $2",
                                                    [secs, FX]),
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, list(USERS)]),
        "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
        "portfolio.securities_global": ("id = ANY($1::uuid[]) OR name LIKE $2", [secs, f"{NAME_PREFIX}%"]),
    }


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs = [r[0] for r in await op.fetch(FIXTURE_SECS_SQL, f"{NAME_PREFIX}%", f"{TICKER_PREFIX}%")]
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%",
                               plan=counted_plan(secs), gucs=(), label="verify_reviewq1")
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


async def leftovers(conn) -> dict:
    secs = await fixture_secs(conn)
    out = {}
    for t, (pred, args) in counted_plan(secs).items():
        n = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
        if n:
            out[t] = n
    n = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                           fetch="fetchval"))
    if n:
        out["organizations"] = n
    return out


REAL_REVIEW = ("FROM portfolio.security_master_review WHERE provider_key <> $1 AND subject_key NOT LIKE $2 AND "
               "incoming_value NOT LIKE $2 AND (global_security_id IS NULL OR NOT global_security_id = ANY($3::uuid[]))")


async def live_open(conn, secs) -> dict:
    rows = await platform(conn, f"SELECT kind, field, provider_key, count(*) AS n {REAL_REVIEW} AND status = 'open' AND "
                                "valid_to IS NULL AND system_to IS NULL GROUP BY 1, 2, 3", FX, VAL_LIKE, secs)
    return {f"{r['kind']}/{r['field']}/{r['provider_key']}": int(r["n"]) for r in rows}


async def real_fingerprints(conn) -> dict:
    secs = await fixture_secs(conn)
    return {
        "real review rows (id, status, valid_to, system_to)": await fingerprint(
            conn, f"SELECT id, status, valid_to, system_to {REAL_REVIEW}", FX, VAL_LIKE, secs),
        "real open queue by kind / field / provider": (0, json.dumps(await live_open(conn, secs), sort_keys=True)),
        "real identifiers (id, value, valid_to, is_alternate)": await fingerprint(
            conn, "SELECT id, id_value, valid_to, is_alternate FROM portfolio.securities_global_identifiers WHERE NOT "
                  "global_security_id = ANY($1::uuid[]) AND id_type IN ('isin', 'cusip', 'figi')", secs),
    }


# ═══ app access + stubs ═════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(ORG_P)}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=600.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_reviewq1"}}
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

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_reviewq1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network
    for name in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        if hasattr(extraction, name):
            def make(n):
                async def no_llm(*a, **k):
                    LLM_CALLS.append(n)
                    raise AssertionError(f"verify_reviewq1: {n} was called")
                return no_llm
            setattr(extraction, name, make(name))
    register_all()
    J.PLATFORM_ORG_ID = ORG_P            # platform_org() reads it at call time: audit rows land in the fixture org


class FigiStub(SourceTransport):
    """OpenFIGI answers for fixture ISINs only (anything else: 'No identifier found.'). Records every job."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.jobs: list = []
        self.requests = 0

    async def post_json(self, url, payload, headers=None):
        self.requests += 1
        self.jobs += list(payload)
        out = []
        for j in payload:
            hit = self.answers.get(j["idValue"])
            out.append({"data": [hit]} if hit else {"warning": "No identifier found."})
        return 200, {}, out


# ═══ seed ═══════════════════════════════════════════════════════════════════
async def guard_prefix(conn) -> bool:
    n_t = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1 OR "
                               "(id_type = 'ticker' AND id_value LIKE $2)", VAL_LIKE, f"{TICKER_PREFIX}%",
                         fetch="fetchval")
    n_n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%",
                         fetch="fetchval")
    n_r = await platform(conn, "SELECT count(*) FROM portfolio.security_master_review WHERE provider_key = $1 OR "
                               "subject_key LIKE $2 OR incoming_value LIKE $2", FX, VAL_LIKE, fetch="fetchval")
    n_o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                         fetch="fetchval")
    return check(int(n_t) == 0 and int(n_n) == 0 and int(n_r) == 0 and int(n_o) == 0,
                 "reserved fixture keys (ZZQ… identifiers, 'ZZQ1 VERIFY' names, provider zzq1_fx, the fixture org) "
                 "match no real row", f"identifiers {n_t}, names {n_n}, review {n_r}, orgs {n_o}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                           ORG_P, "REVIEWQ1 Verify platform", f"{TAG}-platform")
        rid_view = await rbac.ensure_role(conn, ORG_P, "zzq1_viewer", "REVIEWQ1 verify — no permissions")
        for uid, (account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, ORG_P, f"{TAG}-{nm}@test.local", full,
                sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, ORG_P)
            elif rname == "zzq1_viewer":
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid_view)
            else:
                await rbac.grant_named_role(conn, uid, ORG_P, rname)
        for key, (t, ty, ids) in SECS.items():
            sid = await SG.create_security(conn, name=f"{NAME_PREFIX} {key}", security_type=ty, currency_code="USD",
                                           is_super_admin=True)
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1::uuid, 'ticker', $2, true)", str(sid), t)
            for it, v in ids.items():
                await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                                   "id_value, is_primary) VALUES ($1::uuid, $2, $3, $4)", str(sid), it, v, it == "cusip")
            S.setdefault("sec", {})[key] = UUID(str(sid))
    from services import authz as AZ
    flags = await platform(conn, "SELECT (SELECT bool_and(is_test) FROM organizations WHERE id = ANY($1::uuid[])) AS o, "
                                 "(SELECT bool_and(is_test) FROM users WHERE id = ANY($2::uuid[])) AS u",
                           FIXTURE_ORGS, list(USERS), fetch="fetchrow")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        prs = {uid: await AZ.load_principal(conn, uid) for uid in USERS}
    roles_ok = bool(prs[U_SUPER] and prs[U_SUPER].is_super_admin and prs[U_ADMIN] and not prs[U_ADMIN].is_super_admin
                    and prs[U_MEMBER] and not prs[U_MEMBER].is_super_admin)
    S["seeded"] = check(bool(flags["o"] is True and flags["u"] is True and len(S.get("sec", {})) == len(SECS)
                             and roles_ok),
                        f"fixture org / users is_test (asserted); {len(SECS)} fixture securities; the Super Admin is one, "
                        "the org admin and the member are not", f"{dict(flags)} roles_ok={roles_ok}")


# ═══ helpers ════════════════════════════════════════════════════════════════
async def match(conn, provider: str, recs: list[SourceRecord]) -> M.BatchResult:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await M.Matcher(provider, as_of=CLOCK["today"]).process_conn(conn, recs)


def rec(provider=FX, **kw) -> SourceRecord:
    return SourceRecord(provider, as_of=CLOCK["today"], **kw)


async def direct_item(conn, **kw):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await M.insert_review(conn, provider_key=kw.pop("provider_key", FX), **kw)


async def live_item(conn, kind, subject_key, incoming, provider=FX):
    return await platform(conn, "SELECT * FROM portfolio.security_master_review WHERE kind = $1 AND subject_key = $2 AND "
                                "incoming_value = $3 AND provider_key = $4 AND valid_to IS NULL AND system_to IS NULL",
                          kind, subject_key, incoming, provider)


async def item_of(conn, key: str, field_: str, incoming: str, provider=FX):
    rows = await platform(conn, "SELECT * FROM portfolio.security_master_review WHERE global_security_id = $1 AND field = "
                                "$2 AND incoming_value = $3 AND provider_key = $4 AND valid_to IS NULL AND system_to IS "
                                "NULL", sec(key), field_, incoming, provider)
    return rows


async def ids_of(conn, key: str, id_type: str, live_only: bool = True) -> list:
    return [dict(r) for r in await platform(
        conn, "SELECT id, id_value, is_primary, is_alternate, valid_to FROM portfolio.securities_global_identifiers WHERE "
              "global_security_id = $1 AND id_type = $2" + (" AND valid_to IS NULL AND system_to IS NULL" if live_only
                                                           else "") + " ORDER BY id_value", sec(key), id_type)]


async def ident_fp(conn, key: str):
    return await fingerprint(conn, "SELECT id, id_type, id_value, is_primary, is_alternate, valid_to FROM "
                                   "portfolio.securities_global_identifiers WHERE global_security_id = $1", sec(key))


async def fixture_review_fp(conn):
    secs = list(S["sec"].values())
    return await fingerprint(conn, "SELECT id, status, valid_to FROM portfolio.security_master_review WHERE "
                                   "global_security_id = ANY($1::uuid[]) OR provider_key = $2", secs, FX)


async def audit_rows(conn, action: str) -> list:
    return await platform(conn, "SELECT payload FROM audit_log WHERE org_id = $1 AND action = $2 ORDER BY created_at",
                          ORG_P, action)


async def sightings(conn, item) -> int:
    return int(await platform(conn, "SELECT count(*) FROM portfolio.security_master_review_sightings WHERE kind = $1 AND "
                                    "subject_key = $2 AND field = $3 AND incoming_value = $4 AND provider_key = $5",
                              item["kind"], item["subject_key"], item["field"], item["incoming_value"],
                              item["provider_key"], fetch="fetchval"))


async def run_choices(choices: list, note="verify run"):
    return await api(U_SUPER, "POST", f"{P_}/review/run", {"choices": choices, "note": note})


async def known_row(review_key: tuple):
    c, o = await api(U_SUPER, "GET", f"{P_}/review?status=known&provider={review_key[2]}&limit=1000")
    return next((r for r in o.get("rows") or [] if (r["subject_key"], r["incoming_value"]) == review_key[:2]), None), c


# ═══ sections ═══════════════════════════════════════════════════════════════
async def y_live(conn):
    section("[L] READ-ONLY live — the real queue before anything is seeded")
    o = await live_open(conn, [])
    total = sum(o.values())
    S["live0"] = o
    find(f"the real open queue: {total} items", json.dumps(o, sort_keys=True))
    expect = {"identifier_conflict/isin/finance_database": 382, "type_mismatch/security_type/openfigi": 167,
              "identifier_conflict/cusip/finance_database": 72, "identifier_conflict/figi/finance_database": 68,
              "ticker_conflict/ticker/sec_13f_list": 13, "ticker_cusip_conflict/ticker/finance_database": 10,
              "ticker_cusip_conflict/ticker/openfigi": 8, "identifier_conflict/figi/sec_13f_list": 1,
              "type_mismatch/security_type/finance_database": 1, "ticker_conflict/ticker/sec_tickers": 3,
              "ticker_conflict/ticker/nasdaq_trader": 1}
    if o != expect:
        find("the live queue moved since the sprint read it (2026-11-12: 726) — a real source run, not this sprint "
             "(the before/after proof below is what binds)", f"{total}")
    check(total > 0, "the real queue is readable (Super Admin scope)", f"{total}")
    n_known = int(await platform(conn, "SELECT count(*) FROM portfolio.security_master_review WHERE status = ANY($1) AND "
                                       "valid_to IS NULL AND system_to IS NULL", ["used_incoming", "kept_existing",
                                                                                   "kept_both"], fetch="fetchval"))
    check(n_known == 0, "no real item carries a REVIEWQ1 decision (the sprint decided nothing real)", f"{n_known}")
    n_s = int(await platform(conn, "SELECT count(*) FROM portfolio.security_master_review_sightings", fetch="fetchval"))
    check(n_s == 0, "the sightings table holds no row", f"{n_s}")
    n_a = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE is_alternate",
                             fetch="fetchval"))
    check(n_a == 0, "no real identifier is an alternate", f"{n_a}")
    c, env = await api(U_SUPER, "GET", f"{P_}/review?status=open&limit=1")
    check(c == 200 and env.get("total") == total and env["vocabularies"]["facets"]["home_vs_us"] >= 0,
          "GET /review: total = the real open count; facets carry the home-vs-US count", f"{c} {env.get('total')}")
    find(f"home-market vs US ISIN today: {env.get('vocabularies', {}).get('facets', {}).get('home_vs_us')} of "
         f"{o.get('identifier_conflict/isin/finance_database')} ISIN items")


async def y_gates(conn):
    section("[G] Gates — every REVIEWQ1 write refuses a non-Super-Admin (403), nothing written")
    rid = await direct_item(conn, kind="identifier_conflict", subject_key="isin:CAZZQ1000090", field="isin",
                            incoming_value="CAZZQ1000090", existing_value="USZZQ1000090", global_security_id=sec("G1"))
    # a Known one to try to move back: decided directly by the service (fixture)
    kid = await direct_item(conn, kind="identifier_conflict", subject_key="figi:BBGZZQ1F0090", field="figi",
                            incoming_value="BBGZZQ1F0090", existing_value="none", global_security_id=sec("G1"))
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        k = await RQ.apply_choice(conn, kid, "use_existing", actor_id=U_SUPER, note="gate fixture")
    fp0, idfp0 = await fixture_review_fp(conn), await ident_fp(conn, "G1")
    aud0 = len(await audit_rows(conn, "security_master.review_decided"))
    for uid, who in ((U_ADMIN, "org admin"), (U_MEMBER, "member")):
        for method, path, b in (("GET", f"{P_}/review", None),
                                ("POST", f"{P_}/review/run", {"choices": [{"id": str(rid), "choice": "keep_both"}]}),
                                ("POST", f"{P_}/review/bulk/estimate", {"filters": {"provider": FX}, "choice": "keep_both"}),
                                ("POST", f"{P_}/review/bulk", {"filters": {"provider": FX}, "choice": "keep_both",
                                                               "expected_total": 1}),
                                ("POST", f"{P_}/review/{k['new_id']}/move-back", {"note": "gate probe"})):
            c, o = await api(uid, method, path, b)
            check(c == 403, f"{who}: {method} {path.replace(P_, '…').replace(str(k['new_id']), '{id}')} → 403",
                  f"{c} {str(o)[:160]}")
    check(await fixture_review_fp(conn) == fp0 and await ident_fp(conn, "G1") == idfp0 and
          len(await audit_rows(conn, "security_master.review_decided")) == aud0,
          "after every refusal: review rows, the security's identifiers and audit rows unchanged (before/after)")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/run", {"choices": [{"id": str(rid), "choice": "keep_both"}],
                                                          "org_id": str(ORG_P)})
    check(c == 422, "a body carrying org_id is refused (extra='forbid'); the queue is platform scope", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/run", {"choices": [{"id": str(rid), "choice": "merge"}]})
    check(c == 422 and await fixture_review_fp(conn) == fp0, "an unknown choice → 422, nothing written", f"{c}")
    c, o = await api(U_SUPER, "GET", f"{P_}/review?status=open&provider={FX}")
    pm, vo = o.get("permissions") or {}, o.get("vocabularies") or {}
    row = next((r for r in o.get("rows") or [] if r["id"] == str(rid)), None)
    check(c == 200 and pm.get("can_choose") is True and pm.get("can_bulk") is True and pm.get("can_move_back") is True
          and [x["key"] for x in vo.get("choices", [])] == list(RQ.CHOICES) and row is not None and
          row["choices"] == ["use_incoming", "use_existing", "keep_both"] and row["actions"] == ["resolved", "dismissed"],
          "Super Admin: the envelope grants the choices; the row lists its choices (and the legacy actions, unchanged)",
          f"{c} {pm} {row and row.get('choices')}")


async def y_actions(conn):
    section("[A] The three choices through ONE Run — effects on the security, history, refusals, audit")
    # items from the real writer path (the matcher, fixture provider) …
    r = await match(conn, FX, [rec(cusip="ZZQ1C0001", isin="CAZZQ1000001"),          # A1 isin   → keep_both
                               rec(cusip="ZZQ1C0092", figi="BBGZZQ1F0002"),          # A2 cusip  → use_incoming
                               rec(cusip="ZZQ1C0003", figi="BBGZZQ1F0093"),          # A3 figi   → use_existing
                               rec(cusip="ZZQ1C0008", isin="FRZZQ1000008")])         # A8 isin   → no choice
    check(len(r.review_items) == 4, "the matcher raised 4 identifier conflicts (isin, cusip, figi, isin)",
          f"{r.summary()}")
    # … and the kinds the matcher raises only from other sources, written through the same insert_review
    await direct_item(conn, kind="type_mismatch", subject_key=f"security:{sec('A4')}", field="security_type",
                      incoming_value="fund", existing_value="equity", global_security_id=sec("A4"))
    await direct_item(conn, kind="identifier_conflict", subject_key="ticker:ZZQF", field="ticker", incoming_value="ZZQF",
                      existing_value="ZZQE", global_security_id=sec("A5"))
    await direct_item(conn, kind="ticker_cusip_conflict", subject_key="ticker:ZZQF", field="ticker",
                      incoming_value="ZZQF@ZZQ1C0077", existing_value=f"held by {sec('A6')}", global_security_id=sec("A7"))
    await direct_item(conn, kind="identifier_conflict", subject_key="cusip:ZZQ1C0003", field="cusip",
                      incoming_value="ZZQ1C0003", existing_value=f"held by {sec('A3')}", global_security_id=sec("A9"))
    it = {k: (await item_of(conn, k, f, v))[0] for k, f, v in (
        ("A1", "isin", "CAZZQ1000001"), ("A2", "cusip", "ZZQ1C0092"), ("A3", "figi", "BBGZZQ1F0093"),
        ("A8", "isin", "FRZZQ1000008"), ("A4", "security_type", "fund"), ("A5", "ticker", "ZZQF"),
        ("A7", "ticker", "ZZQF@ZZQ1C0077"), ("A9", "cusip", "ZZQ1C0003"))}
    before = {k: await ident_fp(conn, k) for k in ("A1", "A2", "A3", "A5", "A6", "A7", "A8", "A9")}
    a1_us = (await ids_of(conn, "A1", "isin"))[0]
    a2_cusip = (await ids_of(conn, "A2", "cusip"))[0]
    choices = [{"id": str(it["A1"]["id"]), "choice": "keep_both"}, {"id": str(it["A2"]["id"]), "choice": "use_incoming"},
               {"id": str(it["A3"]["id"]), "choice": "use_existing"}, {"id": str(it["A4"]["id"]), "choice": "use_incoming"},
               {"id": str(it["A5"]["id"]), "choice": "use_incoming"}, {"id": str(it["A7"]["id"]), "choice": "use_incoming"},
               {"id": str(it["A9"]["id"]), "choice": "keep_both"}]
    c, o = await run_choices(choices, note="verify: first pass")
    res = o.get("result") or {}
    check(c == 200 and res.get("applied_counts") == {"use_incoming": 2, "use_existing": 1, "keep_both": 1} and
          res.get("requested") == {"use_incoming": 4, "use_existing": 1, "keep_both": 2},
          "Run: 4 applied (Use incoming 2 · Use existing 1 · Keep both 1) of 7 requested", f"{c} {res.get('applied_counts')} "
          f"{res.get('refused')}")
    ref = {x["id"]: x["reason"] for x in res.get("refused") or []}
    check("never two live securities on one ticker + exchange" in ref.get(str(it["A5"]["id"]), ""),
          "Use incoming of a ticker live on ANOTHER security is refused with the reason", f"{ref.get(str(it['A5']['id']))}")
    check("never two live securities on one ticker + exchange" in ref.get(str(it["A7"]["id"]), ""),
          "Use incoming of a ticker + CUSIP whose ticker another security holds is refused with the reason",
          f"{ref.get(str(it['A7']['id']))}")
    check("not offered" in ref.get(str(it["A9"]["id"]), ""), "Keep both of a value held by another security is refused "
          "(not offered — one value names one security)", f"{ref.get(str(it['A9']['id']))}")
    for k in ("A5", "A6", "A7", "A9"):
        check(await ident_fp(conn, k) == before[k], f"{k}: a refusal changed nothing on the security")
    for k in ("A5", "A7", "A9", "A8"):
        rows = await item_of(conn, k, it[k]["field"], it[k]["incoming_value"])
        check(len(rows) == 1 and rows[0]["status"] == "open" and rows[0]["id"] == it[k]["id"],
              f"{k}: still ONE open item, same id ({'no choice' if k == 'A8' else 'refused'})")
    # Keep both
    isins = await ids_of(conn, "A1", "isin")
    alt = next((x for x in isins if x["id_value"] == "CAZZQ1000001"), None)
    us = next((x for x in isins if x["id_value"] == "USZZQ1000001"), None)
    check(bool(alt and alt["is_alternate"] and not alt["is_primary"] and us and us["id"] == a1_us["id"] and
               not us["is_alternate"]),
          "Keep both: the incoming ISIN added as an ALTERNATE; the existing ISIN row untouched (same id)", f"{isins}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        g = await SG.get_by_identifier(conn, "isin", "CAZZQ1000001")
        rows = [RP.HoldingRow(row_num=1, name="Maple", ticker=None, cusip=None, isin="CAZZQ1000001", sedol=None,
                              weight=Decimal("1"), shares=None, market_value=None, currency="USD", asset_type="equity")]
        rr = await RH.resolve_rows(conn, rows)
        lst = await SUM.securities(conn, filters={"q": f"{NAME_PREFIX} A1"}, limit=5)
    check(bool(g and g["id"] == str(sec("A1"))), "a lookup by the alternate ISIN resolves to the same security")
    check(rr.get(1) == (str(sec("A1")), "isin"), "a holding row carrying only the alternate ISIN resolves to the same "
          "security (replica_holdings.resolve_rows — the list loader's path)", f"{rr}")
    shown = next((x["identifiers"].get("isin") for x in lst["rows"] if x["id"] == sec("A1")), None)
    check(shown == "USZZQ1000001", "the master list still shows the EXISTING ISIN for the security (primary)", f"{shown}")
    found = await SS.search(conn, "CAZZQ1000001") if hasattr(SS, "search") else None
    if found is not None:
        ids_found = [str(x.get("id")) for x in (found if isinstance(found, list) else found.get("results", []))]
        check(str(sec("A1")) in ids_found, "security search by the alternate ISIN finds the security", f"{ids_found[:3]}")
    # Use incoming (identifier): a new version, history kept
    cus = await ids_of(conn, "A2", "cusip", live_only=False)
    old = next((x for x in cus if x["id"] == a2_cusip["id"]), None)
    new = [x for x in cus if x["valid_to"] is None]
    check(bool(old and old["valid_to"] is not None and len(new) == 1 and new[0]["id_value"] == "ZZQ1C0092" and
               new[0]["is_primary"] is True),
          "Use incoming: the security's CUSIP is the incoming one as a NEW row (primary carried); the old row kept "
          "(valid_to set) — history, reversible", f"{cus}")
    # Use incoming (type): system-axis archive, id kept
    ty = await platform(conn, "SELECT security_type FROM portfolio.securities_global WHERE id = $1", sec("A4"),
                        fetch="fetchval")
    arch = await platform(conn, "SELECT security_type FROM portfolio.securities_global WHERE name = $1 AND system_to IS "
                                "NOT NULL", f"{NAME_PREFIX} A4")
    check(ty == "fund" and [r["security_type"] for r in arch] == ["equity"],
          "Use incoming (type): the security is 'fund' (same id); the 'equity' version archived on the system axis")
    # Use existing: nothing changed
    check(await ident_fp(conn, "A3") == before["A3"], "Use existing: nothing on the security changed (identifiers)")
    # statuses + restatement
    st = {}
    for k in ("A1", "A2", "A3", "A4"):
        rows = await item_of(conn, k, it[k]["field"], it[k]["incoming_value"])
        st[k] = (rows[0]["status"], rows[0]["decided_by"], rows[0]["id"] != it[k]["id"]) if rows else None
    check(st == {"A1": ("kept_both", U_SUPER, True), "A2": ("used_incoming", U_SUPER, True),
                 "A3": ("kept_existing", U_SUPER, True), "A4": ("used_incoming", U_SUPER, True)},
          "decided items restated (new id, who) with the choice's status", f"{st}")
    oldrow = await platform(conn, "SELECT valid_to, status FROM portfolio.security_master_review WHERE id = $1",
                            it["A1"]["id"], fetch="fetchrow")
    check(oldrow["valid_to"] is not None and oldrow["status"] == "open", "the open row is closed, not overwritten")
    # Known list vs history
    c, o = await api(U_SUPER, "GET", f"{P_}/review?status=known&provider={FX}&limit=1000")
    kn = {r["incoming_value"] for r in o.get("rows") or []}
    check(c == 200 and {"CAZZQ1000001", "BBGZZQ1F0093"} <= kn and "ZZQ1C0092" not in kn and "fund" not in kn,
          "Known discrepancies: the Keep both and Use existing items; NOT the Use incoming ones", f"{sorted(kn)}")
    kr = next((r for r in o.get("rows") or [] if r["incoming_value"] == "BBGZZQ1F0093"), {})
    check(kr.get("decision_note") == "verify: first pass" and kr.get("seen_again") == 0 and kr.get("decided_at") and
          kr.get("actions") == ["move_back"] and kr.get("choices") == [],
          "a Known row: note, when, seen again 0, Move back offered, no choice", f"{ {k: kr.get(k) for k in ('decision_note', 'seen_again', 'actions')} }")
    c, o = await api(U_SUPER, "GET", f"{P_}/review?status=used_incoming&global_security_id={sec('A2')}")
    check(c == 200 and o.get("total") == 1 and o["rows"][0]["detail"]["decision"]["applied"]["before"] == ["ZZQ1C0002"],
          "the security's history lists its Use incoming decision with before → after", f"{c} {o.get('total')}")
    # audit
    aud = [a for a in await audit_rows(conn, "security_master.review_decided")
           if (json.loads(a["payload"]) if isinstance(a["payload"], str) else a["payload"])["review_id"] in
           {str(it[k]["id"]) for k in it}]
    pays = [json.loads(a["payload"]) if isinstance(a["payload"], str) else a["payload"] for a in aud]
    check(len(pays) == 4 and all(p["before"]["status"] == "open" and p["after"]["status"] in RQ.STATUS_OF.values()
                                 for p in pays), "ONE audit row per applied item (before → after); none for a refusal",
          f"{len(pays)}")
    c, o = await run_choices([{"id": str(it["A3"]["id"]), "choice": "use_incoming"}])
    check(c == 200 and len(o["result"]["refused"]) == 1 and "reload" in o["result"]["refused"][0]["reason"],
          "re-running a decided item (its old id) is refused — never decided twice", f"{o.get('result')}")


async def y_bulk(conn):
    section("[B] Bulk by filter — the filter's own count; inclusion AND exclusion; a moved count writes nothing")
    r = await match(conn, FX, [rec(cusip="ZZQ1C0011", isin="CAZZQ1000011"), rec(cusip="ZZQ1C0012", isin="GBZZQ1000012"),
                               rec(cusip="ZZQ1C0013", isin="JPZZQ1000013"), rec(cusip="ZZQ1C0014", isin="USZZQ1000094")])
    check(len(r.review_items) == 4, "4 ISIN conflicts raised (3 home-market, 1 US)", f"{r.summary()}")
    f = {"provider": FX, "home_vs_us": "true"}
    sql_ids = {r_["id"] for r_ in await platform(
        conn, "SELECT id FROM portfolio.security_master_review WHERE provider_key = $1 AND status = 'open' AND valid_to IS "
              "NULL AND system_to IS NULL AND field = 'isin' AND left(incoming_value, 2) <> 'US' AND existing_value LIKE "
              "'US%' AND existing_value NOT LIKE 'held by%'", FX)}
    all_fx = {r_["id"] for r_ in await platform(
        conn, "SELECT id FROM portfolio.security_master_review WHERE provider_key = $1 AND status = 'open' AND valid_to IS "
              "NULL AND system_to IS NULL", FX)}
    c, est = await api(U_SUPER, "POST", f"{P_}/review/bulk/estimate", {"filters": f, "choice": "keep_both"})
    check(c == 200 and est.get("total") == len(sql_ids) and len(sql_ids) >= 3 and est.get("applicable") == len(sql_ids),
          "estimate: the server's count = the same predicate run directly in SQL; every one offers Keep both",
          f"{c} {est.get('total')} vs {len(sql_ids)}")
    check(bool(sql_ids) and sql_ids < all_fx, "the filter narrows: non-empty AND a strict subset of the provider's open "
          "items", f"{len(sql_ids)} of {len(all_fx)}")
    b4 = (await item_of(conn, "B4", "isin", "USZZQ1000094"))[0]
    check(b4["id"] not in sql_ids, "exclusion: the US-incoming item is outside the filter")
    fp0 = await fixture_review_fp(conn)
    c, o = await api(U_SUPER, "POST", f"{P_}/review/bulk", {"filters": f, "choice": "keep_both",
                                                           "expected_total": len(sql_ids) + 1})
    check(c == 409 and await fixture_review_fp(conn) == fp0, "a confirmed count that no longer matches → 409, nothing "
          "written", f"{c} {o.get('detail')}")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/bulk", {"filters": {}, "choice": "keep_both", "expected_total": 0})
    check(c == 422, "a bulk choice with no filter is refused", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/bulk", {"filters": f, "choice": "keep_both",
                                                           "expected_total": len(sql_ids), "note": "verify bulk"})
    res = o.get("result") or {}
    check(c == 200 and res.get("total") == len(sql_ids) and res["applied_counts"]["keep_both"] == len(sql_ids) and
          not res["refused"], "bulk Keep both: every item the filter selected, and only those", f"{c} {res.get('total')}")
    decided = {UUID(x["id"]) for x in res.get("applied") or []}
    check(decided == sql_ids, "inclusion: the applied ids ARE the SQL predicate's ids")
    b4n = await item_of(conn, "B4", "isin", "USZZQ1000094")
    check(len(b4n) == 1 and b4n[0]["status"] == "open" and b4n[0]["id"] == b4["id"], "exclusion: the US item stays open")
    alts = {k: [x["id_value"] for x in await ids_of(conn, k, "isin") if x["is_alternate"]] for k in ("B1", "B2", "B3")}
    check(alts == {"B1": ["CAZZQ1000011"], "B2": ["GBZZQ1000012"], "B3": ["JPZZQ1000013"]},
          "each bulk-kept ISIN is an alternate on its own security", f"{alts}")


async def y_suppression(conn):
    section("[S] Repeat suppression in the writer — same value → seen again; new value → new item; move back → open")
    for provider, key, cusip, inc in ((FX, "S1", "ZZQ1C0021", "GBZZQ1000021"),
                                      ("finance_database", "S3", "ZZQ1C0023", "NLZZQ1000023")):
        r = await match(conn, provider, [rec(provider, cusip=cusip, isin=inc)])
        it = (await item_of(conn, key, "isin", inc, provider))[0]
        check(len(r.review_items) == 1 and it["status"] == "open", f"{provider}: the run opens one item")
        c, o = await run_choices([{"id": str(it["id"]), "choice": "use_existing"}], note=f"verify {provider}")
        check(c == 200 and o["result"]["applied_counts"]["use_existing"] == 1, f"{provider}: Use existing → Known")
        r2 = await match(conn, provider, [rec(provider, cusip=cusip, isin=inc)])
        rows = await item_of(conn, key, "isin", inc, provider)
        check(r2.review_items == [] and r2.known_seen_again == 1 and len(rows) == 1 and rows[0]["status"] ==
              "kept_existing", f"{provider}: the SAME value again → not re-opened; seen again recorded (1)",
              f"{r2.summary()} {[x['status'] for x in rows]}")
        r3 = await match(conn, provider, [rec(provider, cusip=cusip, isin=inc)])
        kr, _ = await known_row((f"isin:{inc}", inc, provider))
        check(r3.review_items == [] and kr is not None and kr["seen_again"] == 2 and kr["last_seen"] is not None,
              f"{provider}: Known discrepancies shows seen again 2 + last seen", f"{kr and kr.get('seen_again')}")
    # a DIFFERENT value → a new open item; the Known one stays Known
    r = await match(conn, FX, [rec(cusip="ZZQ1C0021", isin="GBZZQ1000029")])
    new = await item_of(conn, "S1", "isin", "GBZZQ1000029")
    old = await item_of(conn, "S1", "isin", "GBZZQ1000021")
    check(len(r.review_items) == 1 and len(new) == 1 and new[0]["status"] == "open" and old[0]["status"] ==
          "kept_existing", "a DIFFERENT incoming value → a NEW open item; the Known one stays Known")
    # move back → open (audited); the next run leaves it ONE open item
    aud0 = len(await audit_rows(conn, "security_master.review_moved_back"))
    c, o = await api(U_SUPER, "POST", f"{P_}/review/{old[0]['id']}/move-back", {"note": "verify: reconsider"})
    back = await item_of(conn, "S1", "isin", "GBZZQ1000021")
    bd = back[0]["detail"] if back and isinstance(back[0]["detail"], dict) else json.loads(back[0]["detail"] if back else "{}")
    check(c == 200 and len(back) == 1 and back[0]["status"] == "open" and back[0]["decided_at"] is None and
          (bd.get("moved_back") or {}).get("from_status") == "kept_existing" and
          (bd.get("moved_back") or {}).get("by") == str(U_SUPER),
          "Move back: the Known item is open again (restated; who / why in detail)", f"{c} {o}")
    aud = await audit_rows(conn, "security_master.review_moved_back")
    check(len(aud) == aud0 + 1, "the move back is audited (one row)")
    s0 = await sightings(conn, back[0])
    r = await match(conn, FX, [rec(cusip="ZZQ1C0021", isin="GBZZQ1000021")])
    rows = await item_of(conn, "S1", "isin", "GBZZQ1000021")
    check(r.review_items == [] and r.known_seen_again == 0 and len(rows) == 1 and rows[0]["status"] == "open" and
          await sightings(conn, back[0]) == s0, "the next run treats it as open: still ONE open item, no sighting")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/{rows[0]['id']}/move-back", {"note": "x"})
    check(c == 409, "an open item cannot be moved back (409)", f"{c}")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/{rows[0]['id']}/move-back", {"note": ""})
    check(c == 422, "move back needs a note (422)", f"{c}")
    # Keep both: seen again through the alternate; move back retires the alternate; the next run → one open item
    await match(conn, FX, [rec(cusip="ZZQ1C0022", isin="DEZZQ1000022")])
    it = (await item_of(conn, "S2", "isin", "DEZZQ1000022"))[0]
    await run_choices([{"id": str(it["id"]), "choice": "keep_both"}])
    r = await match(conn, FX, [rec(cusip="ZZQ1C0022", isin="DEZZQ1000022")])
    kept = await item_of(conn, "S2", "isin", "DEZZQ1000022")
    check(r.review_items == [] and r.known_seen_again == 1 and kept[0]["status"] == "kept_both",
          "Keep both: the source sending the kept value again → seen again (via the alternate), nothing re-opened",
          f"{r.summary()}")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/{kept[0]['id']}/move-back", {"note": "verify: undo keep both"})
    alts = [x for x in await ids_of(conn, "S2", "isin") if x["is_alternate"]]
    check(c == 200 and alts == [] and len(o["result"]["retired_alternate_ids"]) == 1,
          "Move back of a Keep both item retires its alternate (history kept)", f"{c} {alts}")
    r = await match(conn, FX, [rec(cusip="ZZQ1C0022", isin="DEZZQ1000022")])
    rows = await item_of(conn, "S2", "isin", "DEZZQ1000022")
    check(r.review_items == [] and len(rows) == 1 and rows[0]["status"] == "open",
          "the next run raises the disagreement again → still ONE open item (the open unique index)")
    used = await platform(conn, "SELECT id FROM portfolio.security_master_review WHERE status = 'used_incoming' AND "
                                "provider_key = $1 AND valid_to IS NULL LIMIT 1", FX, fetch="fetchval")
    if used is not None:
        c, o = await api(U_SUPER, "POST", f"{P_}/review/{used}/move-back", {"note": "x"})
        check(c == 409, "a Use incoming item is not a Known discrepancy — not moved back from this list (409)", f"{c}")


async def y_misses(conn):
    section("[M] List misses → OpenFIGI (stubbed) → list_match / add_to_master review items; nothing auto-merged")
    misses = [{"name": "ZZQ1 Bermuda Holdings", "ticker": None, "cusip": "G0ZZQ1031", "isin": "BMZZQ1000031",
               "reason": "not in the security master"},
              {"name": "ZZQ1 Cayman Newco", "ticker": None, "cusip": None, "isin": "KYZZQ1000032",
               "reason": "not in the security master"},
              {"name": "ZZQ1 Unknown plc", "ticker": None, "cusip": None, "isin": "IEZZQ1000033",
               "reason": "not in the security master"},
              {"name": "ZZQ1 No identifier", "ticker": None, "cusip": None, "isin": None,
               "reason": "not in the security master"}]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await TR.upsert_list(conn, list_key=f"{LIST_PREFIX}_r2k", name="ZZQ1 Russell fixture", description="fixture",
                             source_kind="index_holdings", source_detail="ZZQ1 fixture N-PORT", as_of=CLOCK["today"],
                             members=[], misses=misses, actor_id=U_SUPER)
    answers = {"BMZZQ1000031": {"figi": "BBGZZQ1F0031", "compositeFIGI": "BBGZZQ1F0031", "ticker": "ZZQP",
                                "name": "ZZQ1 BERMUDA HOLDINGS", "exchCode": "US", "marketSector": "Equity",
                                "securityType": "Common Stock"},
               "KYZZQ1000032": {"figi": "BBGZZQ1F0032", "compositeFIGI": "BBGZZQ1F0032", "ticker": "ZZQNEW",
                                "name": f"{NAME_PREFIX} CAYMAN NEWCO", "exchCode": "US", "marketSector": "Equity",
                                "securityType": "Common Stock"}}
    secs0 = sorted(await fixture_secs(conn))
    m1fp = await ident_fp(conn, "M1")
    n_items = lambda: platform(conn, "SELECT count(*) FROM portfolio.security_master_review WHERE kind = ANY($1) AND "  # noqa: E731
                                     "incoming_value LIKE $2", ["list_match", "add_to_master"], VAL_LIKE, fetch="fetchval")
    n_maps = lambda: platform(conn, "SELECT count(*) FROM portfolio.openfigi_mappings WHERE id_value LIKE $1",  # noqa: E731
                              VAL_LIKE, fetch="fetchval")
    stub = FigiStub(answers)
    mod = await _load_cli()
    args = mod.parse(["--list", f"{LIST_PREFIX}_r2k", "--resolve-misses", "--dry-run"])
    import services.database as DB
    pool = await DB.get_pool()
    out = await mod.resolve_misses(args, pool=pool, transport=stub, printer=lambda *_: None, throttle=False)
    check(out["misses_with_identifier"] == 3 and out["misses_without_identifier"] == 1 and out["list_match"] == 1 and
          out["add_to_master"] == 1 and out["no_answer"] == 1 and out["openfigi_jobs"] == 3,
          "dry run: 3 misses with an ISIN looked up; 1 maps to a master security, 1 to none in the master, 1 unanswered",
          f"{ {k: out[k] for k in ('misses_with_identifier', 'list_match', 'add_to_master', 'no_answer')} }")
    check({j["idType"] for j in stub.jobs} == {"ID_ISIN"} and all(j["exchCode"] == "US" for j in stub.jobs),
          "the jobs are ISIN look-ups for the US composite (the existing OpenFIGI provider)")
    check(int(await n_items()) == 0 and int(await n_maps()) == 0, "dry run: no review item, no mapping row written")
    stub2 = FigiStub(answers)
    args = mod.parse(["--list", f"{LIST_PREFIX}_r2k", "--resolve-misses"])
    out = await mod.resolve_misses(args, pool=pool, transport=stub2, printer=lambda *_: None, throttle=False)
    lm = await platform(conn, "SELECT * FROM portfolio.security_master_review WHERE kind = 'list_match' AND "
                              "incoming_value = 'BMZZQ1000031' AND valid_to IS NULL")
    am = await platform(conn, "SELECT * FROM portfolio.security_master_review WHERE kind = 'add_to_master' AND "
                              "incoming_value = 'KYZZQ1000032' AND valid_to IS NULL")
    check(out["items_created"] == 2 and len(lm) == 1 and lm[0]["global_security_id"] == sec("M1") and
          lm[0]["status"] == "open" and len(am) == 1 and am[0]["global_security_id"] is None,
          "real run: a list_match item on the existing security (by FIGI) and an add_to_master item", f"{out['items_created']}")
    check(int(await n_maps()) == 3, "every OpenFIGI answer recorded (the 30-day memory)")
    check(await ident_fp(conn, "M1") == m1fp and sorted(await fixture_secs(conn)) == secs0,
          "nothing auto-merged: the existing security's identifiers unchanged, no security created")
    row = RP.HoldingRow(row_num=7, name="ZZQ1 Bermuda Holdings", ticker=None, cusip="G0ZZQ1031", isin="BMZZQ1000031",
                        sedol=None, weight=Decimal("0.01"), shares=None, market_value=None, currency="USD",
                        asset_type="equity")
    row2 = RP.HoldingRow(row_num=8, name="ZZQ1 Cayman Newco", ticker=None, cusip=None, isin="KYZZQ1000032", sedol=None,
                         weight=Decimal("0.01"), shares=None, market_value=None, currency="USD", asset_type="equity")

    async def loader_dry_run():
        res = SL.Resolved(f"{LIST_PREFIX}_r2k", "fx", None, "index_holdings", "fx", None)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await SL._members_from_rows(conn, res, [row, row2])
        return res
    r0 = await loader_dry_run()
    check(len(r0.members) == 0 and len(r0.misses) == 2, "before confirming: the loader's row resolution still misses both")
    c, o = await run_choices([{"id": str(lm[0]["id"]), "choice": "keep_both"},
                              {"id": str(am[0]["id"]), "choice": "use_incoming"}], note="verify: confirm list misses")
    res = o.get("result") or {}
    check(c == 200 and res.get("applied_counts") == {"use_incoming": 1, "use_existing": 0, "keep_both": 1},
          "confirmed in the queue: Keep both (list_match) + Use incoming (add_to_master)", f"{c} {res.get('refused')}")
    alt = [x for x in await ids_of(conn, "M1", "isin") if x["is_alternate"]]
    check([x["id_value"] for x in alt] == ["BMZZQ1000031"], "the list's ISIN is an alternate on the existing security")
    created = [a for a in res.get("applied") or [] if a["applied"].get("created_security_id")]
    new_id = created[0]["applied"]["created_security_id"] if created else None
    r1 = await loader_dry_run()
    got = {m["gsid"]: m["matched_by"] for m in r1.members}
    check(got == {str(sec("M1")): "isin", str(new_id): "isin"} and not r1.misses,
          "the loader's next dry run resolves BOTH members (the alternate; the added security) — no miss", f"{got}")
    tick = await platform(conn, "SELECT id_value FROM portfolio.securities_global_identifiers WHERE global_security_id = "
                                "$1::uuid AND id_type = 'ticker' AND valid_to IS NULL", new_id) if new_id else []
    check([t["id_value"] for t in tick] == ["ZZQNEW"], "the added security carries OpenFIGI's ticker")
    stub3 = FigiStub(answers)
    out = await mod.resolve_misses(args, pool=pool, transport=stub3, printer=lambda *_: None, throttle=False)
    check(out["already_in_master"] == 2 and out["items_created"] == 0 and {j["idValue"] for j in stub3.jobs} ==
          {"IEZZQ1000033"}, "a re-run skips what the master now resolves; asks only the unanswered one; no new item",
          f"{out['already_in_master']} {out['items_created']} {stub3.jobs}")
    c, o = await api(U_SUPER, "POST", f"{P_}/review/bulk/estimate", {"filters": {"kind": "list_match", "q": "ZZQ1"},
                                                                    "choice": "keep_both"})
    check(c == 200 and o.get("total") == 0, "list_match items are bulk-confirmable by filter (none left open)", f"{c}")


async def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("security_lists_load_rq1", HERE / "security_lists_load.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def y_rls(conn):
    section("[R] RLS — the sightings table is Super Admin only under the non-bypassing app_service role")
    op = await operator_conn()
    try:
        tr = op.transaction()
        await tr.start()
        try:
            await op.execute("SET LOCAL ROLE app_service")
            byp = await op.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            check(byp is False, "the test role (app_service) has rolbypassrls = false")
            n0 = await op.fetchval("SELECT count(*) FROM portfolio.security_master_review_sightings")
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            n1 = await op.fetchval("SELECT count(*) FROM portfolio.security_master_review_sightings WHERE provider_key = "
                                   "ANY($1)", [FX, "finance_database"])
            check(int(n0) == 0 and int(n1) > 0, "without the Super Admin GUC: zero rows; with it: the fixture sightings",
                  f"{n0} / {n1}")
        finally:
            await tr.rollback()
        tr = op.transaction()
        await tr.start()
        try:
            await op.execute("SET LOCAL ROLE app_service")
            try:
                await op.execute("INSERT INTO portfolio.security_master_review_sightings (kind, subject_key, provider_key) "
                                 "VALUES ('identifier_conflict', 'isin:ZZQ1RLS', $1)", FX)
                refused = False
            except asyncpg.InsufficientPrivilegeError:
                refused = True
            check(refused, "an insert without the Super Admin GUC is refused by RLS")
        finally:
            await tr.rollback()
    finally:
        await close_conn(op)


def y_render():
    section("[W] Render tests (node --test tests/reviewq1/ + secmaster1 / secmaster2 / track1)")
    p = subprocess.run(["node", "--test", "tests/reviewq1/", "tests/secmaster1/", "tests/secmaster2/", "tests/track1/"],
                       cwd=WEB_DIR, capture_output=True, text=True, timeout=600)
    m = re.search(r"^# pass (\d+)$", p.stdout, flags=re.M)
    f = re.search(r"^# fail (\d+)$", p.stdout, flags=re.M)
    check(p.returncode == 0 and m is not None and f is not None and f.group(1) == "0" and int(m.group(1)) >= 23,
          "render tests: choices (fail closed), Run confirm counts, bulk with the server's count, Known + Move back; "
          "secmaster1 / secmaster2 / track1 suites still pass",
          f"pass={m.group(1) if m else None} fail={f.group(1) if f else None} {p.stderr[-300:]}")


def regression():
    section("[J] Regressions — each ONCE, flat, after teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


# ═══ driver ═════════════════════════════════════════════════════════════════
SECTIONS = {"l": "live", "g": "gates", "a": "actions", "b": "bulk", "s": "suppression", "m": "misses", "r": "rls",
            "w": "render", "j": "regressions"}
NEEDS = {"r": ("s",)}   # the RLS proof reads the sightings the suppression section wrote


async def run(name, fn, *args):
    t0 = time.monotonic()
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"section '{name}' raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"[INFO] {name}: {time.monotonic() - t0:.1f}s", flush=True)


async def cleanup_only(conn) -> int:
    log = await teardown_retrying()
    left = await leftovers(conn)
    print(f"[INFO] cleanup removed: {log}")
    check(not left, "--cleanup-only: zero fixture rows remain", f"{left}")
    await close_conn(conn)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    conn = await connect(dsn)
    CLOCK["today"] = await conn.fetchval("SELECT (now() AT TIME ZONE 'UTC')::date")
    print(f"[INFO] database via {how}; TODAY = {CLOCK['today'].isoformat()} (the database's UTC date)")
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    only = None
    if "--section" in sys.argv:
        only = {x.strip() for x in sys.argv[sys.argv.index("--section") + 1].lower().split(",") if x.strip()}
        if not only or not only <= set(SECTIONS):
            print(f"[FAIL] unknown section(s) {sorted(only - set(SECTIONS))}; one of {sorted(SECTIONS)}")
            print("TOTAL: 0 PASS, 1 FAIL")
            return 1
    need = set() if only is None else only | {n for k in only for n in NEEDS.get(k, ())}
    want = (lambda k: True) if only is None else (lambda k: k in need)
    if only is not None and "w" in only:
        y_render()
    if only is not None and only <= {"w", "j"}:
        await close_conn(conn)
        if "j" in only:
            regression()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    pre = real = None
    try:
        await run("pre-teardown", teardown_retrying)
        left0 = await leftovers(conn)
        check(not left0, "pre-run cleanup: zero fixture rows before seeding", f"{left0}")
        real = await real_fingerprints(conn)
        if want("l"):
            await run("live", y_live, conn)
        if not await guard_prefix(conn):
            check(False, "seeding needs the reserved keys free", "every fixture section skipped")
        else:
            pre = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {p}", *a, fetch="fetchval"))
                   for t, (p, a) in counted_plan(await fixture_secs(conn)).items()}
            await run("seed", seed, conn)
            if not S.get("seeded"):
                check(False, "seed completed", "every fixture section skipped")
            else:
                for k, fn in (("g", y_gates), ("a", y_actions), ("b", y_bulk), ("s", y_suppression), ("m", y_misses),
                              ("r", y_rls)):
                    if want(k):
                        await run(SECTIONS[k], fn, conn)
        if only is None:
            y_render()
        check(NETWORK_CALLS == [], "no network call was attempted", f"{NETWORK_CALLS[:3]}")
        check(LLM_CALLS == [], "no LLM helper was called", f"{LLM_CALLS[:3]}")
    finally:
        section("Teardown — zero fixture rows; every counted table at its pre-test count; the real queue unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(not left, "zero fixture rows remain", f"{left}")
            if pre is not None:
                post = {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {p}", *a, fetch="fetchval"))
                        for t, (p, a) in counted_plan(await fixture_secs(conn)).items()}
                for t in pre:
                    check(post[t] == pre[t], f"{t} at its pre-test count (by fixture keys)", f"{pre[t]} → {post[t]}")
            if real is not None:
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"{k} unchanged by the run (count + checksum)",
                          f"{real[k][0]} → {after[k][0]} {real[k][1][:80]} → {after[k][1][:80]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
    if only is None or "j" in only:
        if os.environ.get("VERIFY_NESTED") == "1":
            print("[SKIP] nested run")
        else:
            regression()
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
