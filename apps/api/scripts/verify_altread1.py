"""verify_altread1 — ALTREAD1: the Altruist READ client on Altruist's PUBLISHED Open API (developer.altruist.com, read
2026-11-11): documented OAuth (authorize /api/oauth2/authorize + scope openid; token on the API host with
token_type=id; the 28-day refresh-token expiry shown and enforced), V2 paths, X-API-Request-ID, paging (both page
bases, the empty page, the ceiling), the data.<key> envelope, YYYY/MM/DD dates, the transactions date window, the
transactions mapping (transaction_date / settle / three fees summed exactly / reversal / unknown type), the custodian
ACCOUNT BALANCE (stored + shown; treasury unchanged), COST BASIS as custodian lots (default lot source unchanged,
switchable through the maker-checker lot-method change, reconciliation, no Hollisworks lot overwritten), the simulator
+ conformance on the documented shapes (a planted divergence fails citing the documented row; orders / transfers
labelled ASSUMED), isolation both ways, member visibility, sandbox / production unchanged except the corrections.

WRITTEN BY the sprint. The operator runs (one shell; nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_altread1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback) and
exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx network
transport refuses and RECORDS the URL (a call that would leave the process is a [FAIL] naming it). Every Altruist call
runs in-process: sandbox-environment calls through an ``httpx.MockTransport`` serving the DOCUMENTED shapes (threaded on
every call — ``transport=``), simulator calls through an ASGI transport onto the mounted simulator. No model call.

TIME IS INJECTED: the sync's as-of date (D0, then D1), the refresh-expiry clock (``altruist_oauth.set_clock``).

FIXTURES (ids 99000000-0000-0000-0000-0000a1ad…; sign-in tag altread1verify): TEST orgs A and B (is_test asserted);
users with real user_roles grants decided by the app (asserted): U_SA Super Admin; U_ST advisor (view / manage
proposals + custody connections); U_AP compliance (view / approve proposals); U_RD reader (view_proposals only);
U_M a MEMBER (role 'member', view_proposals, one delegate grant on family E_A); U_STB org B's advisor. One fixture
security 'ALTREAD1 VERIFY ETF' (CUSIP ZZA1R0001, ticker ZZA1RF), its tenant asset in org A. Fixture simulator
counterparties carry the slug prefix ``altread1v-``. Sandbox connections are stored through the REAL
``store_new_connection`` with in-process fake credentials (never printed); the full account number the mock feed sends
is checked to appear nowhere (last four only).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by fixture ids (orgs, users, the
security by id, cpsim by the slug prefix) with every teardown GUC a guard reads; every counted table re-read for its
exact pre-test count BY FIXTURE IDS ONLY (audit_log by fixture org / user ids); real rows (custodian balances / lots,
tax lots, account_balances_daily, lot-method rows, connections, crosswalks, demo-tagged entities, non-fixture cpsim
counterparties / book lots) fingerprinted (count + md5) before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown, per-table counts and the real
fingerprints only. --only=<client|paging|envelope|dates|transactions|balance|lots|simulator|conformance|isolation|
unchanged>: the seed, the named sections (each sets up what it needs itself), the full teardown. --only=front_end
(alone): the render tests + node --test tests/ (SKIP_BUILD=1 skips npm build). VERIFY_NESTED=1: '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import ast
import hashlib
import inspect
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import tempfile
import traceback
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "altread1"
BEFORE_REF = "9866144"   # altread1-work's base: 'merge cpsim04'
ROUTES = {("GET", "/api/v1/custodian-feed/accounts/{account_id}"): "view_proposals"}
SIM_ROUTES = {("GET", "/cpsim/c/{slug}/api/oauth2/authorize"), ("POST", "/cpsim/c/{slug}/oauth2/token"),
              ("GET", "/cpsim/c/{slug}/api/v2/households"), ("GET", "/cpsim/c/{slug}/v2/accounts"),
              ("GET", "/cpsim/c/{slug}/v2/accounts/{account_id}/positions/"),
              ("GET", "/cpsim/c/{slug}/v2/accounts/{account_id}/cost-basis/"),
              ("GET", "/cpsim/c/{slug}/v2/accounts/{account_id}/transactions")}
OLD_SIM_ROUTES = {("GET", "/cpsim/c/{slug}/oauth/authorize"), ("POST", "/cpsim/c/{slug}/oauth/token"),
                  ("GET", "/cpsim/c/{slug}/v2/positions"), ("GET", "/cpsim/c/{slug}/v2/transactions")}
NODE_TEST = "tests/altread1/altread1.test.mjs"
NODE_TEST_MIN = 5
CONFORMANCE = HERE / "cpsim_altruist_conformance.py"
REGRESSIONS = ("verify_cpsim02.py", "verify_cpsim04.py", "verify_altruist_sprint2_identity_resolution.py",
               "verify_altruist_sprint3_positions_transactions_sync.py", "verify_altruist_sprint5_sync_orchestration.py",
               "verify_altruist_sprint6_realtime_webhook_receiver.py", "verify_rep6.py", "verify_authz1.py")
HTTP_OK, HTTP_CREATED, HTTP_FORBIDDEN, HTTP_NOT_FOUND, HTTP_CONFLICT = 200, 201, 403, 404, 409
SECTION_TIMEOUT = 600   # every section must finish in < 10 minutes
N_ACCOUNTS_PAGED = 2500
N_TXNS_PAGED = 1200
PAGE_MAX_ACCOUNTS, PAGE_MAX_TXNS = 1000, 500
CEILING = 2
LOOKBACK_MONTHS = 13
REFRESH_DAYS = 28
CORRECTIONS_MIN = 19


def node_tests_declared() -> int:
    return len(re.findall(r"^test\(", _src("apps/web/" + NODE_TEST), re.M))


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def _git_show(ref: str, rel: str) -> str:
    try:
        p = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=REPO, capture_output=True, text=True, timeout=120)
        return p.stdout if p.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _literal(src: str, name: str):
    """The literal value assigned to a module-level ``name`` in ``src`` (ast; None when absent)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign)
                                                                     else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    return None
    return None


# ═══ self-checks (no DB, no network) ════════════════════════════════════════════════════════════════════════════
def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "altread1 changed no existing app route's gate and removed none", f"{o.where()}; changed={sorted(changed)[:3]} "
          f"removed={o.removed[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own app routes are exactly its {len(ROUTES)} route (GET custodian-feed)",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, "the custodian-feed route names its gate (view_proposals) at altread1's end point", f"{bad[:3]}")
    drift = VS.still_gated(o, [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])])
    check(not drift, "altread1's route still exists today with the same gate", f"{drift[:3]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    sim_ = {(m, r.get("path")) for r in data.get("simulator_routes", []) for m in (r.get("methods") or [])}
    check(SIM_ROUTES <= sim_ and not (OLD_SIM_ROUTES & sim_),
          "docs/AUTHZ1_ROUTES lists the simulator's DOCUMENTED OAuth + read routes and none of the cpsim02-era paths",
          f"missing={sorted(SIM_ROUTES - sim_)[:3]} stale={sorted(OLD_SIM_ROUTES & sim_)[:3]}")


def self_static(check, find):
    n = _flat(_src("docs/altruist/ALTREAD1_NOTES.md"))
    for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
        check(f"**{d}" in n, f"ALTREAD1_NOTES records {d} as built")
    rows = re.findall(r"^\| \d+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \|$", _src("docs/altruist/ALTREAD1_NOTES.md"), re.M)
    check(len(rows) >= CORRECTIONS_MIN, f"ALTREAD1_NOTES lists every correction old → new (≥ {CORRECTIONS_MIN} rows with "
          "the doc page)", f"{len(rows)}")
    for w in ("9866144 merge cpsim04", "VERIFY_REGRESSIONS", "portfolio.custodian_tax_lots",
              "public.account_custodian_balances", "refresh_token_issued_at", "Diff review"):
        check(w in n, f"ALTREAD1_NOTES names {w!r}")
    c = _src("docs/cpsim/ALTRUIST_CONTRACT.md")
    for page in ("DOCUMENTED open-api-authentication", "DOCUMENTED endpoints", "json-code-examples"):
        check(page in _flat(c) or page.split(" ", 1)[-1] in c, f"ALTRUIST_CONTRACT cites {page!r}")
    for w in ("/api/oauth2/authorize", "/oauth2/token", "token_type=id", "28 days", "/v2/accounts/{account_id}/positions/",
              "/v2/accounts/{account_id}/cost-basis/", "/v2/accounts/{account_id}/transactions", "X-API-Request-ID",
              "YYYY/MM/DD", "## 10. Corrections"):
        check(w in c, f"ALTRUIST_CONTRACT (rewritten read path) states {w!r}")
    check("## 8. Orders — ASSUMED — not part of Altruist's published API (Realtime / FIX)" in c
          and "## 9. Transfers — ASSUMED — not part of Altruist's published API (Realtime / FIX)" in c,
          "contract §8 orders and §9 transfers relabelled ASSUMED — not part of Altruist's published API")
    corr = [ln for ln in c.split("## 10. Corrections", 1)[-1].splitlines() if re.match(r"^\| \d+ \|", ln)]
    check(len(corr) >= CORRECTIONS_MIN, "contract §10 corrections table", f"{len(corr)}")
    adm = _src("docs/cpsim/CPSIM_ADMIN.md")
    check("## 14." in adm and "`lots`" in adm and "cost_basis_adjusted" in adm, "CPSIM_ADMIN §14: the lots upload "
          "template and the documented shapes")
    td = _flat(_src("docs/OUTSTANDING_TODO_LIST.md"))
    for w in ("account_custodian_balances", "portfolio_altruist.py", "Realtime API", "flat-file"):
        check(w in td, f"OUTSTANDING_TODO_LIST carries the follow-up {w!r}")
    rs = _src("scripts/regression_set.txt")
    check(bool(re.search(r"^verify_altread1\.py\s*$", rs, re.M)), "verify_altread1.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    check("## ALTREAD1" in st and "STATUS:" in st.split("## ALTREAD1", 1)[-1][:2000], "PROJECT_STATUS carries the "
          "ALTREAD1 STATUS line")
    ao = _src("apps/api/services/altruist_oauth.py")
    check(_literal(ao, "_OAUTH_PATHS") == {"authorize": "/api/oauth2/authorize", "token": "/oauth2/token"}
          and _literal(ao, "DEFAULT_SCOPE") == "openid" and _literal(ao, "TOKEN_TYPE_PARAM") == "id"
          and _literal(ao, "HOUSEHOLDS_PATH") == "/api/v2/households",
          "the client's OAuth paths, scope, token_type and households constant are the documented ones")
    check(_literal(ao, "PAGE_SIZE_MAX") == {"households": 1000, "accounts": 1000, "positions": 1000,
                                            "cost_basis": 1000, "transactions": 500},
          "the client's page_size maxima are the documented ones (1000; transactions 500)")


def self_unchanged(check, find):
    code = subprocess.run(["git", "diff", "--quiet", BEFORE_REF, "--", "apps/api/services/portfolio_altruist.py"],
                          cwd=REPO, timeout=120).returncode
    check(code == 0, "services/portfolio_altruist.py untouched since the sprint base (superseded; removal is a follow-up)")
    before = _git_show(BEFORE_REF, "apps/api/services/altruist_oauth.py")
    now = _src("apps/api/services/altruist_oauth.py")
    check(bool(before) and _literal(before, "ALTRUIST_HOSTS") == _literal(now, "ALTRUIST_HOSTS") is not None,
          "sandbox / production hosts unchanged (ALTRUIST_HOSTS identical to the sprint base)")
    check(_literal(before, "ENVIRONMENTS") == _literal(now, "ENVIRONMENTS"), "the environment list is unchanged")
    for w in ('prefix = "ALTRUIST_SANDBOX"', 'prefix = "ALTRUIST_PRODUCTION"', 'prefix = "ALTRUIST_SIMULATOR"'):
        check(w in before and w in now, f"client_credentials keeps {w!r} (explicit branches)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)
    self_unchanged(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))


from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _cpsim_fixtures as FX  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_roles as VR  # noqa: E402

import main  # noqa: E402
from cpsim import book as SB  # noqa: E402
from cpsim import db as SDB  # noqa: E402
from services import account_models as AM  # noqa: E402
from services import altruist_conformance as ACF  # noqa: E402
from services import altruist_identity as AI  # noqa: E402
from services import altruist_oauth as AO  # noqa: E402
from services import altruist_positions_sync as APS  # noqa: E402
from services import custodian_lots as CL  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import portfolio_assets as PA  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_engine as E  # noqa: E402
from services.us_business_days import is_business_day  # noqa: E402
from services import users as users_mod  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "altread1verify"
NAME = "ALTREAD1 VERIFY "
SLUG_PREFIX = "altread1v-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000a1ad"
D0 = date(2026, 11, 13)           # the first sync's as-of date (a Friday)
D1 = date(2026, 11, 20)           # the later sync's as-of date
SIM_BASE = "https://cpsim.altread1.invalid"
REDIRECT = "https://verify.altread1.invalid/altruist/callback"
SANDBOX = "sandbox"
XWALK = "ALTRUIST"
FULL_NUMBER = "987654321"         # the feed's account number — must never be stored, logged or returned whole
CUSIP_F, TICKER_F = "ZZA1R0001", "ZZA1RF"
TEARDOWN_GUCS = ("execution.teardown", "models.teardown", "replica.teardown", "cpsim.allow_teardown",
                 "payments.teardown")


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SA, U_ST, U_AP, U_RD, U_M, U_STB = (_id(10 + i) for i in range(6))
ALL_USERS = [U_SA, U_ST, U_AP, U_RD, U_M, U_STB]
E_A, E_A2, E_B = _id(40), _id(41), _id(42)
H_A, H_B = _id(30), _id(31)
ACC_T, ACC_L, ACC_BAL, ACC_B = _id(50), _id(51), _id(52), _id(53)
SEC_F = _id(70)
HOUSEHOLDS = [H_A, H_B]
ACCOUNTS = [ACC_T, ACC_L, ACC_BAL, ACC_B]
SECURITIES = [SEC_F]
X_T, X_L, X_BAL, X_B = "ar1v-acct-t", "ar1v-acct-l", "ar1v-acct-bal", "ar1v-acct-b"
HH_A = "ar1v-hh-a"
PERMS = {"advisor": ("view_proposals", "manage_proposals", "view_custody_connections", "manage_custody_connections"),
         "compliance": ("view_proposals", "approve_proposals"),
         "ir_member_relations": ("view_proposals",),
         "member": ("view_proposals",)}
USERS = {U_SA: (ORG_A, "super_admin", "sa", "super_admin"),
         U_ST: (ORG_A, "member", "st", "advisor"),
         U_AP: (ORG_A, "member", "ap", "compliance"),
         U_RD: (ORG_A, "member", "rd", "ir_member_relations"),
         U_M: (ORG_A, "member", "m", "member"),
         U_STB: (ORG_B, "member", "stb", "advisor")}
ROLE_EXPECT = {U_SA: {"org": ORG_A, "roles": ["super_admin"]},
               U_ST: {"org": ORG_A, "roles": ["advisor"], "has": ["manage_proposals", "view_custody_connections"],
                      "lacks": ["approve_proposals"]},
               U_AP: {"org": ORG_A, "roles": ["compliance"], "has": ["approve_proposals"],
                      "lacks": ["manage_proposals"]},
               U_RD: {"org": ORG_A, "roles": ["ir_member_relations"], "has": ["view_proposals"],
                      "lacks": ["manage_proposals", "approve_proposals"]},
               U_M: {"org": ORG_A, "roles": ["member"], "has": ["view_proposals"], "lacks": ["manage_proposals"]},
               U_STB: {"org": ORG_B, "roles": ["advisor"], "has": ["view_custody_connections"]}}
SEED_CHECK_LITERALS = (
    ("public.account_custodian_balances", ("altruist", "altruist_simulator", "sandbox")),
    ("portfolio.custodian_tax_lots", ("altruist", "sandbox")),
    ("public.account_lot_method_settings", ("hollisworks", "custodian", "unknown", "approved")),
    ("portfolio.tax_lots", ("open",)),
    ("public.delegate_grants", ("view_only",)),
    ("public.altruist_connections", ("sandbox",)),
    ("portfolio.external_references", ("account",)),
    ("portfolio.securities_global", ("fund",)),
    ("cpsim.book_uploads", ("lots",)),
)
UNIQUES = ("account_custodian_balances_live_uq", "custodian_tax_lots_live_uq", "book_lots_live_uq",
           "external_references_org_source_ext_type_key", "altruist_connections_active_unique")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id", "is_discretionary", "is_held_away"),
    "delegate_grants": ("org_id", "principal_entity_id", "delegate_user_id", "scope", "effective_from", "granted_by"),
    "portfolio.external_references": ("org_id", "source_system", "external_id", "record_type", "record_id"),
    "portfolio.securities_global": ("id", "name", "security_type", "currency_code"),
    "portfolio.securities_global_identifiers": ("global_security_id", "id_type", "id_value", "is_primary"),
    "portfolio.tax_lots": ("org_id", "position_id", "acquired_date", "quantity", "original_cost", "adjusted_cost",
                           "status", "source_system"),
    "account_custodian_balances": ("org_id", "account_id", "source_system", "environment", "account_balance",
                                   "as_of_date"),
    "portfolio.custodian_tax_lots": ("org_id", "account_id", "source_system", "environment", "external_id", "quantity",
                                     "cost_basis_adjusted", "as_of_date"),
}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.entities", "public.households",
                  "public.household_memberships", "public.accounts", "public.delegate_grants",
                  "public.altruist_connections", "public.account_custodian_balances",
                  "public.account_lot_method_settings", "public.account_balances_daily",
                  "public.account_import_exceptions", "public.audit_log", "portfolio.external_references",
                  "portfolio.positions", "portfolio.transactions", "portfolio.assets", "portfolio.tax_lots",
                  "portfolio.custodian_tax_lots", "portfolio.securities_global",
                  "portfolio.securities_global_identifiers")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.book_uploads", "cpsim.book_households",
                "cpsim.book_accounts", "cpsim.book_positions", "cpsim.book_transactions", "cpsim.book_lots",
                "cpsim.oauth_codes", "cpsim.oauth_tokens", "cpsim.request_log")

S: dict = {"issued": [], "bodies": [], "cp": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {_redact(detail)}" if detail and not passed else ""),
          flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {_redact(detail)}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def total_line() -> str:
    return f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL"


def _redact(text) -> str:
    t = str(text)
    for v in S["issued"]:
        if v:
            t = t.replace(v, "[secret]")
    t = t.replace(FULL_NUMBER, "…" + FULL_NUMBER[-4:])
    return re.sub(r"cpsim_(?:live|at|rt|code|whsec)_[0-9a-f]{8,}", "[secret]", t)


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or "") if isinstance(exc.stdout, str) else "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


def sub_of(uid) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', $1, true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def sim(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', '', true)")
        await conn.execute("SET LOCAL ROLE cpsim_app")
        return await getattr(conn, fetch)(sql, *args)


class Rollback(Exception):
    pass


async def rolled_back(conn, fn, *, org=None):
    """Run ``fn(conn)`` in ONE org's RLS context in a transaction that is ALWAYS rolled back; (result, error)."""
    out = {"res": None, "err": None}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                               "set_config('app.current_org_id', $1, true)", str(org) if org else "")
            try:
                async with conn.transaction():
                    out["res"] = await fn(conn)
            except Exception as exc:  # noqa: BLE001
                out["err"] = exc
            raise Rollback()
    except Rollback:
        pass
    return out["res"], out["err"]


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


async def close_app_pools() -> None:
    p, DBM._pool = DBM._pool, None
    await close_conn(p)
    try:
        await SDB.close_pool()
    except Exception:  # noqa: BLE001
        pass


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


FIX_CP = f"(SELECT id FROM cpsim.counterparties WHERE slug LIKE '{SLUG_PREFIX}%')"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS), _arr(ALL_USERS)
    by_org = f"org_id = ANY({orgs})"
    secs = _arr(SECURITIES)
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.household_memberships": f"household_id = ANY({_arr(HOUSEHOLDS)})",
        "public.audit_log": f"{by_org} OR user_id = ANY({users})",
        "portfolio.securities_global": f"id = ANY({secs})",
        "portfolio.securities_global_identifiers": f"global_security_id = ANY({secs})",
        "cpsim.counterparties": f"slug LIKE '{SLUG_PREFIX}%'",
        "cpsim.request_log": f"slug LIKE '{SLUG_PREFIX}%' OR counterparty_id IN {FIX_CP}",
    }.get(table, f"counterparty_id IN {FIX_CP}" if table.startswith("cpsim.") else by_org)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED_PUBLIC:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    for t in CPSIM_TABLES:
        out[t] = int(await sim(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    return out


async def fingerprint(conn, sql, *args, as_sim=False) -> tuple[int, str]:
    q = f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS s FROM ({sql}) t"
    row = await (sim(conn, q, *args, fetch="fetchrow") if as_sim else platform(conn, q, *args, fetch="fetchrow"))
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    orgs = [str(o) for o in FIXTURE_ORGS]
    nf = "NOT (org_id = ANY($1::uuid[]))"
    out = {
        "custodian balances": await fingerprint(conn, "SELECT id, account_balance, as_of_date, valid_to FROM "
                                                      f"public.account_custodian_balances WHERE {nf}", orgs),
        "custodian lots": await fingerprint(conn, "SELECT id, quantity, cost_basis_adjusted, valid_to FROM "
                                                  f"portfolio.custodian_tax_lots WHERE {nf}", orgs),
        "Hollisworks tax lots": await fingerprint(conn, "SELECT id, quantity, original_cost, adjusted_cost, status, "
                                                        f"valid_to, system_to FROM portfolio.tax_lots WHERE {nf}", orgs),
        "account_balances_daily (the treasury's source)": await fingerprint(
            conn, "SELECT org_id, account_id, as_of_date, source_system, cash_value, total_market_value FROM "
                  f"public.account_balances_daily WHERE {nf}", orgs),
        "lot-method account rows": await fingerprint(conn, "SELECT id, status, lot_source, valid_to, system_to FROM "
                                                           f"public.account_lot_method_settings WHERE {nf}", orgs),
        "Altruist connections": await fingerprint(conn, "SELECT id, environment, status, refresh_token_issued_at, "
                                                        f"system_to FROM altruist_connections WHERE {nf}", orgs),
        "crosswalks": await fingerprint(conn, "SELECT id, source_system, external_id, record_id FROM "
                                              f"portfolio.external_references WHERE {nf}", orgs),
        "demo-tagged entities": await fingerprint(
            conn, "SELECT id, display_name, tags, status FROM entities WHERE EXISTS (SELECT 1 FROM unnest(tags) x "
                  f"WHERE x LIKE 'demo-%') AND {nf}", orgs),
    }
    for label, sql in (("counterparties (non-fixture)", f"SELECT id, slug, status, system_to FROM cpsim.counterparties "
                                                        f"WHERE slug NOT LIKE '{SLUG_PREFIX}%'"),
                       ("book lots (non-fixture)", f"SELECT id, quantity, valid_to FROM cpsim.book_lots WHERE "
                                                   f"counterparty_id NOT IN {FIX_CP}")):
        out[label] = await fingerprint(conn, sql, as_sim=True)
    return out


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        users, orgs = list(ALL_USERS), list(FIXTURE_ORGS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [list(HOUSEHOLDS)]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [list(SECURITIES)]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            "cpsim.counterparties": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
            "cpsim.request_log": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_altread1")
        log = res["log"]
    finally:
        await close_conn(op)
    try:
        log.append(f"cpsim fixtures: {await FX.teardown(SLUG_PREFIX)}")
    except Exception as exc:  # noqa: BLE001
        log.append(f"cpsim fixtures: {type(exc).__name__}")
    return log


async def teardown_retrying() -> list[str]:
    try:
        return await teardown()
    except (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
            ConnectionError) as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; retrying once")
        return await teardown()


async def leftovers(conn) -> int:
    n = sum((await counts(conn)).values())
    op = await operator_conn()
    try:
        n += await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%")
    finally:
        await close_conn(op)
    return n


class org_ctx:
    """A direct service call in ONE org's RLS context — the same contextvars the API's middleware sets."""

    def __init__(self, org, super_admin: bool = False):
        self.org, self.su = str(org), super_admin

    async def __aenter__(self):
        self.tokens = DBM.set_rls_context(self.org, self.su)
        return await DBM.get_pool()

    async def __aexit__(self, *a):
        DBM.reset_rls_context(self.tokens)
        return False


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"User-Agent": "verify_altread1", "Authorization": f"Bearer {token_for(uid)}"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
        except Exception as exc:  # noqa: BLE001
            print(f"[INFO] {method} {path} raised in the app (a 500):")
            traceback.print_exc()
            return 500, {"raw": f"{type(exc).__name__}"}
    try:
        out = res.json() if res.content else {}
    except ValueError:
        out = {"raw": res.text[:4000]}
    S["bodies"].append(res.text[:20000])
    return res.status_code, out


def install_stubs():
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

    async def refuse_network(self, request):
        NETWORK_CALLS.append(f"{request.method} {request.url}")
        raise httpx.ConnectError(f"verify_altread1: network disabled ({request.url.host})", request=request)

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_altread1: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    if len(list(inspect.signature(AO.set_clock).parameters)) != 1:
        raise RuntimeError("altruist_oauth.set_clock signature drifted")
    AO.set_simulator_transport(FX.TimeoutASGITransport(app=FX.mounted_app()))
    os.environ[AO.CPSIM_BASE_URL_ENV] = SIM_BASE
    os.environ["ALTRUIST_REDIRECT_URI"] = REDIRECT
    os.environ["ALTRUIST_TOKEN_ENCRYPTION_KEY"] = "altread1verify-" + secrets.token_hex(16)
    S["client_secret"] = "verify-secret-" + secrets.token_hex(16)
    S["issued"] += [S["client_secret"], os.environ["ALTRUIST_TOKEN_ENCRYPTION_KEY"]]


def restore_stubs():
    AO.set_clock(None)
    AO.set_simulator_transport(None)


# ═══ The documented Altruist feed, in-process (MockTransport) ═════════════════════════════════════════════════════
FEED: dict = {}          # (resource, external id) → [rows]; rows may carry "_on" (the window date) — stripped
REQUESTS: list[dict] = []


def _doc_date(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def _page(rows: list, params: dict, key: str, echo: dict, *, base: int = 0, page_max: int = 1000) -> dict:
    page = int(params.get("page", "0"))
    size = min(int(params.get("page_size", "100")), page_max)
    index = max(page - base, 0)
    return {"paging": {"page": index + base, "page_size": size, "total_pages": -(-len(rows) // size)},
            "data": {**echo, key: rows[index * size:(index + 1) * size]}}


def feed_handler(request: httpx.Request) -> httpx.Response:
    """The DOCUMENTED read path for the sandbox environment (paths, envelope, paging, the transactions window)."""
    path = request.url.path
    params = dict(request.url.params)
    REQUESTS.append({"path": path, "params": params, "rid": request.headers.get("x-api-request-id"),
                     "auth": (request.headers.get("authorization") or "").startswith("Bearer ")})
    api_root = urlsplit(AO.ALTRUIST_HOSTS[SANDBOX]["api_base"]).path
    tail = path[len(api_root):] if path.startswith(api_root) else path
    if tail == AO.HOUSEHOLDS_PATH:
        return httpx.Response(200, json=_page(FEED.get(("households", "*"), []), params, "households", {}))
    if tail == "/v2/accounts":
        hh = params.get("household_id")
        return httpx.Response(200, json=_page(FEED.get(("accounts", hh), []), params, "accounts", {"household_id": hh}))
    m = re.match(r"^/v2/accounts/([^/]+)/(positions/|cost-basis/|transactions)$", tail)
    if not m:
        return httpx.Response(404, json={"detail": f"not a documented path: {tail}"})
    acct, res = m.group(1), m.group(2).rstrip("/").replace("-", "_")
    rows = list(FEED.get((res, acct), []))
    if res == "transactions":
        lo, hi = params.get("date_from"), params.get("date_to")
        lo, hi = date.fromisoformat(lo), date.fromisoformat(hi)
        rows = [r for r in rows if lo <= r["_on"] <= hi]
    rows = [{k: v for k, v in r.items() if k != "_on"} for r in rows]
    return httpx.Response(200, json=_page(rows, params, res, {"account_id": acct},
                                          page_max=500 if res == "transactions" else 1000))


MOCK = httpx.MockTransport(feed_handler)


def pos_row(pid: str, qty: str, mv: str, as_of: date) -> dict:
    return {"id": pid, "cusip": CUSIP_F, "symbol": TICKER_F, "description": NAME + "ETF", "long_short_indicator": "L",
            "currency_code": "USD", "quantity": qty, "as_of_date": _doc_date(as_of), "security_type": "ETF",
            "market_value": mv}


def txn_row(tid: str, typ: str, on: date, *, tdate: str | None = None, flag: str = "N", **over) -> dict:
    r = {"id": tid, "cusip": CUSIP_F, "symbol": TICKER_F, "security_type": "ETF", "quantity": "10", "price": "90",
         "amount": "900.00", "interest": None, "currency_code": "USD", "transaction_type": typ,
         "transaction_subtype": None, "transaction_description": None, "cancellation_reversal_flag": flag,
         "transaction_fees": "0", "broker_fees": "0", "other_fees": "0", "TEFRA_withheld": "0",
         "state_tax_withheld": "0", "as_of_date": _doc_date(on), "transaction_date": tdate or _doc_date(on),
         "settle_date": None, "_on": on}
    r.update(over)
    return r


def lot_row(lid: str, qty: str, mv: str, unadj: str, adj: str, opened: str, as_of: date) -> dict:
    return {"id": lid, "cusip": CUSIP_F, "symbol": TICKER_F, "description": NAME + "ETF", "quantity": qty,
            "market_value": mv, "cost_basis_unadjusted": unadj, "cost_basis_adjusted": adj,
            "unrealized_gain_loss": format(Decimal(mv) - Decimal(adj), "f"), "as_of_date": _doc_date(as_of),
            "open_date": opened}


def reset_feed(as_of: date = D0) -> None:
    """Every resolved org-A account answers (empty lists by default): the sync enumerates them all."""
    FEED.clear()
    for x in (X_T, X_L, X_BAL):
        FEED[("positions", x)] = []
        FEED[("cost_basis", x)] = []
        FEED[("transactions", x)] = []


async def sync_a(as_of: date):
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            return await APS.sync_resolved_accounts(c, org_id=str(ORG_A), environment=SANDBOX, as_of_date=as_of,
                                                    transport=MOCK)


def acct_result(res, x: str):
    return next((a for a in res.accounts if a.altruist_account_id == x), None)


async def holdings(account) -> dict:
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            h = await AM.load_holdings(c, str(ORG_A), str(account))
    lots = sorted(((lot.id, lot.quantity, lot.unit_cost, lot.acquired) for hh in h["holdings"] for lot in hh.lots),
                  key=lambda t: (str(t[0]), t[1]))
    return {"lots": lots, "cash": h["cash"], "cash_as_of": h["cash_as_of"], "lot_source": h.get("lot_source"),
            "keys": sorted(hh.key for hh in h["holdings"])}


async def position_of(conn, account) -> str | None:
    return await platform(conn, "SELECT p.id::text FROM portfolio.positions p WHERE p.org_id = $1::uuid AND "
                                "p.account_id = $2::uuid AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY "
                                "p.as_of_date DESC LIMIT 1", ORG_A, account, fetch="fetchval")


# ═══ sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Discovery + ALTREAD1_NOTES (every correction old → new); cpsim04 merged; fast regression mode present")
    code, out, _e = run_cmd(["git", "log", "main", "--oneline"], REPO, timeout=120)
    check(code == 0 and bool(re.search(r"^\w+ merge cpsim04$", out, re.M)), "cpsim04 is merged to main")
    check(VT.regression_mode() in VT.REGRESSION_MODES and "VERIFY_REGRESSIONS" in _src("apps/api/scripts/_verify_total.py"),
          "VERIFY_REGRESSIONS fast mode is present (cpsim04 fix 1)")
    self_static(check, find)


async def _constraints(conn, nsp: str, rel: str) -> dict:
    rows = await platform(conn, "SELECT k.conname, pg_get_constraintdef(k.oid) AS d FROM pg_constraint k "
                                f"WHERE k.conrelid = ({REL})", nsp, rel)
    return {r["conname"]: r["d"] for r in rows}


def _snapshot_columns() -> dict[str, set]:
    out: dict[str, set] = {}
    cur = None
    for ln in _src("docs/schema_snapshot.sql").splitlines():
        m = re.match(r"^-- ===== ([\w.]+) =====", ln)
        if m:
            cur = m.group(1)
            out[cur] = set()
            continue
        m = re.match(r"^--   (\w+)\s+\S", ln)
        if cur and m and m.group(1) not in ("PRIMARY", "UNIQUE", "FOREIGN"):
            out[cur].add(m.group(1))
    return out


async def y_schema(conn):
    section("[Y] Schema: the smallest additions, RLS for every operation, cpsim lot table cpsim_app only (catalog)")
    for nsp, rel in (("public", "account_custodian_balances"), ("portfolio", "custodian_tax_lots")):
        oid = await platform(conn, REL, nsp, rel, fetch="fetchval")
        if oid is None:
            check(False, f"{nsp}.{rel} exists (catalog)")
            continue
        pols = await platform(conn, "SELECT polcmd::text AS cmd, pg_get_expr(polqual, polrelid) AS q, "
                                    "pg_get_expr(polwithcheck, polrelid) AS w FROM pg_policy WHERE polrelid = $1", oid)
        rls = await platform(conn, "SELECT relrowsecurity FROM pg_class WHERE oid = $1", oid, fetch="fetchval")
        check(bool(rls) and {p["cmd"] for p in pols} == {"r", "a", "w", "d"} and len(pols) == 4
              and all("NULLIF" in ((p["q"] or "") + (p["w"] or "")) for p in pols),
              f"{nsp}.{rel}: RLS on, one policy per operation, every org GUC read through NULLIF", f"{len(pols)}")
    cols = {r["attname"] for r in await platform(conn, "SELECT attname FROM pg_attribute WHERE attrelid = (" + REL +
                                                       ") AND attnum > 0 AND NOT attisdropped", "public",
                                                 "altruist_connections")}
    check("refresh_token_issued_at" in cols, "altruist_connections.refresh_token_issued_at (D1's refresh expiry)")
    oid = await platform(conn, REL, "cpsim", "book_lots", fetch="fetchval")
    app_role = await platform(conn, "SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'", fetch="fetchval")
    privs = set()
    others = []
    if oid is not None:
        acl = await platform(conn, "SELECT x.privilege_type, x.grantee, r.rolname FROM pg_class c, aclexplode(c.relacl) "
                                   "x JOIN pg_roles r ON r.oid = x.grantee WHERE c.oid = $1", oid)
        privs = {r["privilege_type"] for r in acl if r["grantee"] == app_role}
        others = [r["rolname"] for r in acl if r["rolname"] not in ("cpsim_app", "postgres")]
    check(oid is not None and privs == set(FX.cpsim_app_privileges("book_lots")) and not others,
          "cpsim.book_lots: cpsim_app only (SELECT / INSERT / UPDATE / DELETE), no other role", f"{privs} {others}")
    cons = {}
    for t, _l in SEED_CHECK_LITERALS:
        nsp, rel = t.split(".")
        cons[t] = await _constraints(conn, nsp, rel)
    n_read = sum(len(v) for v in cons.values())
    check(n_read > 0 and all(cons[t] for t, _l in SEED_CHECK_LITERALS),
          "UNIQUE / CHECK / FK constraints read from the catalog (by namespace + relname, never ::regclass)", f"{n_read}")
    bad = [f"{t} lacks {x!r}" for t, lits in SEED_CHECK_LITERALS for x in lits
           if not any(f"'{x}'" in d for d in cons[t].values() if d.startswith("CHECK"))]
    check(not bad, "every seeded literal in a CHECKed column matches its CHECK (read from the catalog)", f"{bad[:5]}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(set(UNIQUES) <= set(idx) and all("UNIQUE" in d for d in idx.values()),
          "the unique indexes the seed relies on exist and are UNIQUE (read before seeding)",
          f"missing {sorted(set(UNIQUES) - set(idx))}")
    snap = _snapshot_columns()
    missing = [f"{t}.{c}" for t, cs in SEED_COLUMNS.items() for c in cs
               if c not in (snap.get(t) or snap.get(t.split(".")[-1]) or set())]
    check(not missing, "every INSERT's columns appear in docs/schema_snapshot.sql (refreshed after the migration)",
          f"{missing[:5]}")
    bypass = await platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_service'", fetch="fetchval")
    check(bypass is False, "app_service (the app's role) does not bypass RLS")
    S["schema_ok"] = n_read > 0 and oid is not None


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1", f"{FIXTURE_ID_PREFIX}%",
                           fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    s_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE id::text LIKE $1 OR name "
                                  "LIKE $2", f"{FIXTURE_ID_PREFIX}%", NAME + "%", fetch="fetchval"))
    i_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value = "
                                  "ANY($1::text[])", [CUSIP_F, TICKER_F], fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1", SLUG_PREFIX + "%",
                      fetch="fetchval"))
    return check(o == 0 and u == 0 and s_ == 0 and i_ == 0 and c == 0, "the reserved prefixes (fixture ids, the sign-in "
                 "tag, the 'ALTREAD1 VERIFY' security and its identifiers, the 'altread1v-' slug) match no real row",
                 f"orgs={o} users={u} securities={s_} identifiers={i_} counterparties={c}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"ALTREAD1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, perms in PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"ALTREAD1 verify — {rname}")
                for pn in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"ALTREAD1 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for eid, org, nm in ((E_A, ORG_A, "A family"), (E_A2, ORG_A, "A second family"), (E_B, ORG_B, "B family")):
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) VALUES "
                               "($1, $2, $3, 'active', 'individual'::entity_type)", eid, org, NAME + nm)
        for hh, org, eids in ((H_A, ORG_A, (E_A, E_A2)), (H_B, ORG_B, (E_B,))):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + "household")
            for eid in eids:
                await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh,
                                   eid)
        for n, (acc, org, eid, hh) in enumerate(((ACC_T, ORG_A, E_A2, H_A), (ACC_L, ORG_A, E_A, H_A),
                                                 (ACC_BAL, ORG_A, E_A, H_A), (ACC_B, ORG_B, E_B, H_B)), start=1):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, 'ALTRUIST', 'individual', 'taxable', $5, $6, true, false)", acc, org,
                f"****61{n:02d}", hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), eid, hh)
        for org, ext, acc in ((ORG_A, X_T, ACC_T), (ORG_A, X_L, ACC_L), (ORG_A, X_BAL, ACC_BAL), (ORG_B, X_B, ACC_B)):
            await conn.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                               "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", org, XWALK, ext, acc)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from, granted_by) VALUES ($1, $2, $3, 'view_only', now() - interval '1 hour', $4)",
                           ORG_A, E_A, U_M, U_SA)
        await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, currency_code) "
                           "VALUES ($1, $2, 'fund', 'USD')", SEC_F, NAME + "ETF")
        for typ, val, prim in (("cusip", CUSIP_F, True), ("ticker", TICKER_F, False)):
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1, $2, $3, $4)", SEC_F, typ, val, prim)
    # the tenant asset of the security in org A (so feed positions resolve to it and the engine sees one holding)
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            asset = await PA.create_asset(c, org_id=str(ORG_A), name=NAME + "ETF", asset_type="etf",
                                          global_security_id=str(SEC_F), currency_code="USD")
            await PA.add_identifier(c, org_id=str(ORG_A), asset_id=asset, id_type="cusip", id_value=CUSIP_F,
                                    is_primary=True)
            S["asset"] = asset
    # sandbox connections through the REAL store (fake in-process credentials; never printed)
    conns = {}
    for org in FIXTURE_ORGS:
        tok = AO.TokenResponse("verify-at-" + secrets.token_hex(12), "verify-rt-" + secrets.token_hex(12), 3600, "openid")
        S["issued"] += [tok.access_token, tok.refresh_token]
        async with org_ctx(org) as pool:
            async with pool.acquire() as c:
                conns[org] = await AO.store_new_connection(c, org_id=str(org), environment=SANDBOX,
                                                           client_id="verify-client", client_secret=S["client_secret"],
                                                           tokens=tok, connected_by=None)
    S["conn"] = conns
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS),
                    "fixture orgs A, B and their users are is_test = true (privileged insert path)", f"{len(os_)} {len(us)}")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT, "(Super Admin, advisor, approver, reader, "
                                                                       "member, org B advisor)")
    issued = await platform(conn, "SELECT count(*) FROM altruist_connections WHERE org_id = ANY($1::uuid[]) AND "
                                  "refresh_token_issued_at IS NOT NULL AND system_to IS NULL", FIXTURE_ORGS,
                            fetch="fetchval")
    ok_c = check(int(issued) == 2, "store_new_connection records refresh_token_issued_at on connect (both orgs)",
                 f"{issued}")
    S["seeded"] = bool(ok_test and ok_roles and ok_c and S.get("asset"))


# ── client auth: documented OAuth, the refresh-token expiry ───────────────────────────────────────────────────────
async def y_client(conn):
    section("[Y] Client auth: authorize path + openid, token on the API host with token_type=id, refresh, refresh-token "
            "expiry surfaced (status) and enforced (a sync refuses with the reason)")
    hosts = AO.ALTRUIST_HOSTS[SANDBOX]
    state = secrets.token_urlsafe(32)
    url = AO.build_authorize_url(environment=SANDBOX, state=state, hosts=hosts,
                                 credentials=("verify-client", S["client_secret"]), redirect_uri=REDIRECT)
    q = parse_qs(urlsplit(url).query)
    check(url.startswith(hosts["login_base"] + "/api/oauth2/authorize?") and q.get("scope") == ["openid"]
          and q.get("state") == [state] and q.get("redirect_uri") == [REDIRECT] and q.get("client_id") == ["verify-client"],
          "authorize URL: the LOGIN host + /api/oauth2/authorize, scope openid, the state, the redirect, the client id",
          urlsplit(url).path)
    seen = []

    def token_handler(req: httpx.Request) -> httpx.Response:
        seen.append({"url": str(req.url), "form": {k: v[0] for k, v in parse_qs(req.content.decode()).items()}})
        return httpx.Response(200, json={"access_token": "verify-at2-" + secrets.token_hex(8),
                                         "refresh_token": "verify-rt2-" + secrets.token_hex(8), "expires_in": 3600,
                                         "token_type": "Bearer"})

    T = httpx.MockTransport(token_handler)
    tok = await AO.exchange_code_for_tokens(environment=SANDBOX, code="verify-code", hosts=hosts, transport=T,
                                            credentials=("verify-client", S["client_secret"]), redirect_uri=REDIRECT)
    S["issued"] += [tok.access_token, tok.refresh_token]
    f0 = seen[0]["form"] if seen else {}
    check(bool(seen) and seen[0]["url"] == hosts["api_base"] + "/oauth2/token" and f0.get("grant_type") ==
          "authorization_code" and f0.get("token_type") == "id" and f0.get("code") == "verify-code"
          and f0.get("redirect_uri") == REDIRECT and f0.get("client_id") == "verify-client"
          and f0.get("client_secret") == S["client_secret"],
          "token (authorization_code): POST to the API host + /oauth2/token, form body with token_type=id, code, "
          "redirect_uri, client_id, client_secret", f"{seen[0]['url'] if seen else None} keys={sorted(f0)}")
    fresh = await AO.refresh_access_token(environment=SANDBOX, refresh_token=tok.refresh_token, hosts=hosts,
                                          transport=T, credentials=("verify-client", S["client_secret"]))
    S["issued"] += [fresh.access_token, fresh.refresh_token]
    f1 = seen[1]["form"] if len(seen) > 1 else {}
    check(len(seen) == 2 and seen[1]["url"] == hosts["api_base"] + "/oauth2/token" and f1.get("grant_type") ==
          "refresh_token" and f1.get("token_type") == "id" and f1.get("refresh_token") == tok.refresh_token
          and fresh.refresh_issued is True, "refresh: POST to the API host + /oauth2/token with grant_type "
          "refresh_token + token_type=id; a returned refresh token restarts its 28-day clock", f"keys={sorted(f1)}")
    now = datetime.now(timezone.utc)
    row = {"refresh_token_issued_at": now, "created_at": now - timedelta(days=90)}
    check(AO.refresh_token_expires_at(row) == now + timedelta(days=REFRESH_DAYS)
          and AO.refresh_token_expires_at({"refresh_token_issued_at": None, "created_at": now}) ==
          now + timedelta(days=REFRESH_DAYS) and AO.REFRESH_TOKEN_LIFETIME == timedelta(days=REFRESH_DAYS),
          "refresh expiry = issued (else created) + 28 days (the documented lifetime)")
    # the route: org B's sandbox connection, the clock injected 27 days on (valid) then 29 days on (expired)
    try:
        AO.set_clock(lambda: datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS - 1))
        c1, b1 = await api(U_STB, "GET", "/api/v1/altruist/status?environment=sandbox")
        AO.set_clock(lambda: datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS + 1))
        c2, b2 = await api(U_STB, "GET", "/api/v1/altruist/status?environment=sandbox")
        before = len(REQUESTS) + len(NETWORK_CALLS)
        c3, b3 = await api(U_STB, "POST", "/api/v1/altruist/sync", {"environment": SANDBOX})
        after = len(REQUESTS) + len(NETWORK_CALLS)
        async with org_ctx(ORG_B) as pool:
            async with pool.acquire() as c:
                try:
                    await AO.require_active_connection(c, org_id=str(ORG_B), environment=SANDBOX)
                    refused = None
                except AO.AltruistRefreshExpiredError as exc:
                    refused = str(exc)
    finally:
        AO.set_clock(None)
    check(c1 == HTTP_OK and b1.get("refresh_token_expired") is False and b1.get("connected") is True
          and bool(b1.get("refresh_token_expires_at")),
          "status 27 days on: connected, refresh_token_expires_at shown, not expired", f"{c1} {dumps(b1)[:300]}")
    check(c2 == HTTP_OK and b2.get("refresh_token_expired") is True and b2.get("connected") is False
          and b2.get("reconnect_required") is True, "status 29 days on: refresh_token_expired, reconnect_required",
          f"{c2} {dumps(b2)[:300]}")
    check(c3 == HTTP_CONFLICT and "refresh token expired" in str(b3.get("detail") or b3.get("error") or "")
          and after == before and refused is not None and "28-day" in refused,
          "a sync with an expired refresh token refuses 409 naming the reason, before any call (none made)",
          f"{c3} {dumps(b3)[:300]} calls {before}→{after}")
    sim_section_ok = await _simulator_auth(conn)
    check(sim_section_ok, "the client's authorize / token / refresh against the SIMULATOR in-process all succeed")


async def _simulator_auth(conn) -> bool:
    slug = SLUG_PREFIX + "auth-" + secrets.token_hex(3)
    async with SDB.tx() as c:
        cp = await FX.create_counterparty(c, name=NAME + "auth", slug=slug)
    S["issued"].append(cp["key"])
    root = f"{SIM_BASE}/cpsim/c/{slug}"
    hosts = {"api_base": root, "login_base": root}
    inner = FX.TimeoutASGITransport(app=FX.mounted_app())
    seen = []

    class Rec(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.method == "POST":
                seen.append({"path": request.url.path,
                             "form": {k: v[0] for k, v in parse_qs((await request.aread()).decode()).items()}})
            return await inner.handle_async_request(request)

    T = Rec()
    state = secrets.token_urlsafe(32)
    url = AO.build_authorize_url(environment=AO.SIMULATOR, state=state, hosts=hosts,
                                 credentials=(cp["client_id"], cp["key"]), redirect_uri=REDIRECT)
    async with httpx.AsyncClient(transport=T, follow_redirects=False) as client:
        r = await client.get(url)
    loc = r.headers.get("location") or ""
    lq = parse_qs(urlsplit(loc).query)
    ok1 = check(r.status_code == 302 and loc.startswith(REDIRECT) and lq.get("state") == [state] and bool(lq.get("code")),
                "simulator authorize (/api/oauth2/authorize, scope openid): 302 with a code and the state echoed",
                f"{r.status_code}")
    tok = await AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=(lq.get("code") or [""])[0], hosts=hosts,
                                            transport=T, credentials=(cp["client_id"], cp["key"]), redirect_uri=REDIRECT)
    S["issued"] += [tok.access_token, tok.refresh_token]
    ok2 = check(bool(seen) and seen[0]["path"].endswith(f"/cpsim/c/{slug}/oauth2/token")
                and seen[0]["form"].get("token_type") == "id" and tok.access_token.startswith("cpsim_at_"),
                "simulator token: the API host's /oauth2/token, token_type=id, a cpsim access token", f"{seen[:1]}")
    fresh = await AO.refresh_access_token(environment=AO.SIMULATOR, refresh_token=tok.refresh_token, hosts=hosts,
                                          transport=T, credentials=(cp["client_id"], cp["key"]))
    S["issued"] += [fresh.access_token, fresh.refresh_token]
    ok3 = check(fresh.access_token != tok.access_token and fresh.refresh_token != tok.refresh_token,
                "simulator refresh: a new access and refresh token (rotation)")
    async with httpx.AsyncClient(transport=T, follow_redirects=False) as client:
        bad = await client.post(f"{root}/oauth2/token", data={"grant_type": "refresh_token",
                                                             "refresh_token": fresh.refresh_token,
                                                             "client_id": cp["client_id"], "client_secret": cp["key"]})
    ok4 = check(bad.status_code == 400, "the simulator refuses a token request without token_type=id",
                f"{bad.status_code}")
    return bool(ok1 and ok2 and ok3 and ok4)


# ── paging / envelope / dates (in-process mock, no DB) ────────────────────────────────────────────────────────────
def _accounts_server(n: int, *, base: int = 0, echo: bool = True, zero_empty: bool = False):
    rows = [{"id": f"pg-{i:05d}", "account_name": f"n{i}", "account_number": FULL_NUMBER} for i in range(n)]
    log = []

    def h(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        log.append({"page": int(q.get("page", 0)), "size": int(q.get("page_size", 100)),
                    "rid": req.headers.get("x-api-request-id")})
        p, size = int(q.get("page", 0)), int(q.get("page_size", 100))
        if base == 1 and p == 0 and zero_empty:
            chunk, shown = [], 0
        else:
            idx = 0 if (base == 1 and p == 0) else p - base
            chunk, shown = rows[idx * size:(idx + 1) * size], (1 if (base == 1 and p == 0) else p)
        body = {"data": {"household_id": "pg-hh", "accounts": chunk},
                "paging": {"page_size": size, "total_pages": -(-n // size), **({"page": shown} if echo else {})}}
        return httpx.Response(200, json=body)
    return httpx.MockTransport(h), log


async def y_paging(conn):
    section("[Y] Paging by total_pages: 2,500 accounts at page_size 1000 (base 0 and base 1); transactions at 500; an "
            "empty page stops; the ceiling stops and records; X-API-Request-ID per request")
    for kw, label in ((dict(base=0), "base 0"), (dict(base=1), "base 1 (page 0 served as page 1)"),
                      (dict(base=1, zero_empty=True), "base 1 answering page 0 empty"),
                      (dict(base=0, echo=False), "no page echo: walk to an empty page")):
        T, log = _accounts_server(N_ACCOUNTS_PAGED, **kw)
        r = await AO.fetch_pages(api_base="https://x.invalid", resource="accounts", access_token="t", param="pg-hh",
                                 transport=T)
        ids = [i["id"] for i in r.items]
        check(len(ids) == N_ACCOUNTS_PAGED and len(set(ids)) == N_ACCOUNTS_PAGED and not r.ceiling_hit
              and all(x["size"] == PAGE_MAX_ACCOUNTS for x in log),
              f"{label}: all {N_ACCOUNTS_PAGED} accounts read exactly once at page_size {PAGE_MAX_ACCOUNTS} "
              f"({r.pages} requests)", f"{len(ids)} unique {len(set(ids))} pages {r.pages} sizes {[x['size'] for x in log]}")
        rids = [x["rid"] for x in log]
        check(all(rids) and len(set(rids)) == len(rids) and all(_valid_uuid(x) for x in rids) and rids == r.request_ids,
              f"{label}: one fresh X-API-Request-ID (uuid) per request, returned on the result")
    T, log = _accounts_server(N_ACCOUNTS_PAGED, echo=False)
    r = await AO.fetch_pages(api_base="https://x.invalid", resource="accounts", access_token="t", param="pg-hh",
                             transport=T)
    check(r.pages == len(log) and log[-1]["page"] == r.pages - 1 and r.pages == -(-N_ACCOUNTS_PAGED // 1000) + 1,
          "without an echo the read stops on the first EMPTY page", f"pages {r.pages} log {[x['page'] for x in log]}")
    T, log = _accounts_server(N_ACCOUNTS_PAGED)
    try:
        await AO.fetch_resource(api_base="https://x.invalid", resource="accounts", access_token="t", param="pg-hh",
                                transport=T, max_pages=CEILING)
        check(False, "the page ceiling stops the read and records it", "no AltruistPagingCeiling raised")
    except AO.AltruistPagingCeiling as cap:
        check(cap.pages == CEILING and len(cap.partial) == CEILING * 1000 and len(log) == CEILING,
              f"the page ceiling ({CEILING}) stops the read: AltruistPagingCeiling with the {CEILING * 1000} partial rows",
              f"{cap.pages} {len(cap.partial)} requests {len(log)}")
    rows = [txn_row(f"pt-{i:05d}", "BUY", D0) for i in range(N_TXNS_PAGED)]
    rows = [{k: v for k, v in r.items() if k != "_on"} for r in rows]
    tlog = []

    def th(req):
        q = dict(req.url.params)
        tlog.append(int(q.get("page_size", 0)))
        return httpx.Response(200, json=_page(rows, q, "transactions", {"account_id": "pt-a"}, page_max=500))
    r = await AO.fetch_pages(api_base="https://x.invalid", resource="transactions", access_token="t", param="pt-a",
                             query={"date_from": "2026-01-01", "date_to": "2026-11-13"}, transport=httpx.MockTransport(th))
    check(len(r.items) == N_TXNS_PAGED and set(tlog) == {PAGE_MAX_TXNS} and r.pages == -(-N_TXNS_PAGED // 500),
          f"transactions: {N_TXNS_PAGED} rows at page_size {PAGE_MAX_TXNS} (the documented transactions max)",
          f"{len(r.items)} {set(tlog)} {r.pages}")
    try:
        await AO.fetch_pages(api_base="https://x.invalid", resource="transactions", access_token="t", param="pt-a",
                             page_size=501, transport=httpx.MockTransport(th))
        check(False, "a transactions page_size above 500 is refused by the client")
    except ValueError:
        check(True, "a transactions page_size above 500 is refused by the client (never sent)")
    start = AO.add_months(D0, -LOOKBACK_MONTHS)
    w = AO.transaction_windows(start, D0)
    days = sum((hi - lo).days + 1 for lo, hi in w)
    contiguous = all(w[i + 1][0] == w[i][1] + timedelta(days=1) for i in range(len(w) - 1))
    check(w[0][0] == start and w[-1][1] == D0 and contiguous and days == (D0 - start).days + 1
          and all(hi < AO.add_months(lo, 1) for lo, hi in w),
          f"a {LOOKBACK_MONTHS}-month look-back is walked in {len(w)} windows of ≤ 1 month, every day exactly once")


def _valid_uuid(v) -> bool:
    try:
        _uuid.UUID(str(v))
        return True
    except ValueError:
        return False


async def y_envelope(conn):
    section("[Y] Envelope: documented data.<key> read; an old flat fixture still read; a malformed body → "
            "AltruistOAuthError with the reason")
    doc = {"paging": {"page": 0, "page_size": 100, "total_pages": 1}, "data": {"households": [{"id": "h1", "name": "x"}]}}
    for body, form in ((doc, "documented"), ({"households": [{"id": "h1", "name": "x"}]}, "flat"),
                       ([{"id": "h1", "name": "x"}], "list"), ({"data": [{"id": "h1", "name": "x"}]}, "data_list")):
        r = await AO.fetch_pages(api_base="https://x.invalid", resource="households", access_token="t",
                                 transport=httpx.MockTransport(lambda req, b=body: httpx.Response(200, json=b)))
        check([i["id"] for i in r.items] == ["h1"] and r.envelope == form, f"envelope {form!r} read", f"{r.envelope}")
    for body, need in (({"data": {"households": "x"}}, "data.households"), ({"data": {"other": []}}, "data.households"),
                       ({"nothing": 1}, "documented envelope")):
        try:
            await AO.fetch_resource(api_base="https://x.invalid", resource="households", access_token="t",
                                    transport=httpx.MockTransport(lambda req, b=body: httpx.Response(200, json=b)))
            check(False, f"malformed body {dumps(body)} is refused")
        except AO.AltruistOAuthError as exc:
            check(need in str(exc), f"malformed body {dumps(body)[:60]} → AltruistOAuthError naming {need!r}", str(exc))
    wrong = {"paging": {"page": 0, "page_size": 100, "total_pages": 1},
             "data": {"account_id": "someone-else", "positions": [{"id": "p"}]}}
    try:
        await AO.fetch_resource(api_base="https://x.invalid", resource="positions", access_token="t", param="mine",
                                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=wrong)))
        check(False, "a response naming another account is refused")
    except AO.AltruistOAuthError as exc:
        check("account_id" in str(exc), "a response whose data.account_id names another account is refused", str(exc))
    acct = AO._normalize_account({"id": "a", "account_number": FULL_NUMBER, "account_balance": "1"})
    check(acct["account_number_last4"] == FULL_NUMBER[-4:] and FULL_NUMBER not in dumps(acct),
          "the account number is reduced to its last four in the normalized row (raw included)")


async def y_dates(conn):
    section("[Y] Dates: YYYY/MM/DD and ISO both parsed; a bad date → a per-row error, never a guess")
    check(AO.parse_altruist_date("2019/01/15") == date(2019, 1, 15) and AO.parse_altruist_date("2022-08-15") ==
          date(2022, 8, 15) and AO.parse_altruist_date("2022-08-15T10:00:00Z") == date(2022, 8, 15)
          and AO.parse_altruist_date(None) is None and AO.parse_altruist_date("") is None,
          "YYYY/MM/DD, ISO and an ISO timestamp's date part are parsed")
    bad = []
    for v in ("15/01/2019", "2019/13/01", "2019/02/30", "20190115", "2019-01/15", 20190115):
        try:
            AO.parse_altruist_date(v)
            bad.append(v)
        except ValueError:
            pass
    check(not bad, "malformed / impossible dates raise (never guessed)", f"{bad}")


# ── transactions: the window walked, the mapping (fees, reversal, unknown type) ──────────────────────────────────
async def y_transactions(conn):
    section("[Y] Transactions: windows walked (first 13 months, later from the last synced date − 5 business days); "
            "transaction_date / settle_date / three fees summed exactly / interest / description / subtype; reversal; "
            "an unknown type and a bad date → per-row errors")
    reset_feed()
    FEED[("positions", X_T)] = [pos_row("ar1v-p-t1", "10", "1000.00", D0)]
    buy = txn_row("ar1v-t-buy", "BUY", date(2026, 11, 10), settle_date="2026/11/12", amount="900.00",
                  transaction_fees="0.10", broker_fees="0.25", other_fees="0.05", TEFRA_withheld="1.50",
                  state_tax_withheld="0.50", interest="0.12", transaction_subtype="PURCHASE",
                  transaction_description="Bought 10")
    FEED[("transactions", X_T)] = [
        buy,
        txn_row("ar1v-t-div", "DIVIDEND", date(2026, 10, 15), tdate="2026-10-15", amount="12.34", quantity=None,
                price=None),
        txn_row("ar1v-t-foo", "FOO", date(2026, 10, 16)),
        txn_row("ar1v-t-baddate", "BUY", date(2026, 10, 17), tdate="17/10/2026"),
        txn_row("ar1v-t-rev-unknown", "BUY", date(2026, 10, 18), flag="Y"),
        txn_row("ar1v-t-flag-bad", "BUY", date(2026, 10, 19), flag="maybe"),
        txn_row("ar1v-t-old", "BUY", date(2025, 10, 20)),           # inside the 13-month look-back, months back
    ]
    REQUESTS.clear()
    res = await sync_a(D0)
    a = acct_result(res, X_T)
    if a is None:
        check(False, "the sync reached the transactions account", dumps([x.altruist_account_id for x in res.accounts]))
        return
    win = [(r["params"].get("date_from"), r["params"].get("date_to")) for r in REQUESTS
           if r["path"].endswith(f"/v2/accounts/{X_T}/transactions")]
    start = AO.add_months(D0, -LOOKBACK_MONTHS)
    check(bool(win) and win[0][0] == start.isoformat() and win[-1][1] == D0.isoformat() and len(win) ==
          len(AO.transaction_windows(start, D0)) and a.transactions_window == (start, D0, len(win)),
          f"first sync: {len(win)} windows from {start} (13 months back) to {D0}, each sent as ISO date_from / date_to",
          f"{win[:2]} … {win[-1:]}")
    check(all(r["rid"] and r["auth"] for r in REQUESTS), "every feed request carried X-API-Request-ID and a Bearer "
          "Authorization header")
    rows = {r["external_ref"]: dict(r) for r in await platform(
        conn, "SELECT x.external_ref, x.transaction_type_code, x.trade_date, x.settle_date, x.quantity, x.price, "
              "x.gross_amount, x.fees, x.taxes, x.id::text AS id, x.valid_to FROM portfolio.transactions x WHERE "
              "x.org_id = $1::uuid AND x.external_ref LIKE 'ar1v-t-%'", ORG_A)}
    b = rows.get("ar1v-t-buy") or {}
    check(b.get("transaction_type_code") == "buy" and b.get("trade_date") == date(2026, 11, 10)
          and b.get("settle_date") == date(2026, 11, 12) and b.get("fees") == Decimal("0.40")
          and b.get("taxes") == Decimal("2.00") and b.get("gross_amount") == Decimal("900.00"),
          "BUY: transaction_date → trade date, settle_date kept, fees = 0.10 + 0.25 + 0.05 = 0.40 EXACTLY (Decimal), "
          "taxes = TEFRA + state = 2.00, amount → gross", dumps(b))
    det = a.transaction_details.get(b.get("id") or "", {})
    check(det == {"interest": "0.12", "description": "Bought 10", "subtype": "PURCHASE"},
          "interest, transaction_description and transaction_subtype are kept on the sync result", dumps(det))
    d = rows.get("ar1v-t-div") or {}
    check(d.get("transaction_type_code") == "dividend" and d.get("trade_date") == date(2026, 10, 15),
          "an ISO transaction_date is parsed too (DIVIDEND on 2026-10-15)", dumps(d))
    check("ar1v-t-old" in rows, "a transaction 13 months back (inside the look-back) is read — not only the past month")
    errs = {e.external_id: e.reason for e in a.transactions_errors}
    check("txn:ar1v-t-foo" in errs and "unmapped" in errs["txn:ar1v-t-foo"] and "ar1v-t-foo" not in rows,
          "an unknown transaction_type is a per-row error, never guessed", dumps(errs))
    check("txn:ar1v-t-baddate" in errs and "ar1v-t-baddate" not in rows, "a bad date is a per-row error", dumps(errs))
    check("txn:ar1v-t-flag-bad" in errs and "cancellation_reversal_flag" in errs["txn:ar1v-t-flag-bad"],
          "an unrecognised cancellation_reversal_flag value is a per-row error", dumps(errs))
    check(a.transactions_skipped_reversal == 1 and "ar1v-t-rev-unknown" not in rows,
          "a reversal with nothing recorded to reverse is SKIPPED (never booked as a new trade)",
          f"{a.transactions_skipped_reversal}")
    created = [r for r in rows if r.startswith("ar1v-t-")]
    check(len(created) == len(set(created)) == a.transactions_created == 3,
          "each transaction read once and recorded once across all the windows", f"{created} {a.transactions_created}")
    # the later sync: the window starts at the last synced date − 5 business days; the BUY now arrives flagged
    FEED[("transactions", X_T)] = [{**buy, "cancellation_reversal_flag": "Y"}]
    FEED[("positions", X_T)] = [pos_row("ar1v-p-t1", "10", "1000.00", D1)]
    REQUESTS.clear()
    res2 = await sync_a(D1)
    a2 = acct_result(res2, X_T)
    win2 = [(r["params"].get("date_from"), r["params"].get("date_to")) for r in REQUESTS
            if r["path"].endswith(f"/v2/accounts/{X_T}/transactions")]
    want, steps = D0, 0          # computed independently of the client's helper: 5 US business days back
    while steps < 5:
        want -= timedelta(days=1)
        steps += 1 if is_business_day(want) else 0
    check(want < D0 and AO.minus_business_days(D0, 5) == want, f"minus 5 business days from {D0} is {want} (the "
          "client's helper agrees with an independent count)", f"{AO.minus_business_days(D0, 5)}")
    check(bool(win2) and win2[0][0] == want.isoformat() and win2[-1][1] == D1.isoformat(),
          f"later sync: from the last synced date ({D0}) minus 5 business days ({want}) to {D1}", f"{win2}")
    closed = await platform(conn, "SELECT valid_to FROM portfolio.transactions WHERE org_id = $1::uuid AND "
                                  "external_ref = 'ar1v-t-buy'", ORG_A, fetch="fetchval")
    check(a2 is not None and a2.transactions_reversed == 1 and closed is not None,
          "a flagged row whose transaction is recorded and current CLOSES it (valid_to set, no successor)",
          f"{a2.transactions_reversed if a2 else None} {closed} skipped={a2.transactions_skipped_reversal if a2 else None} "
          f"errors={[(e.external_id, e.reason[:80]) for e in (a2.transactions_errors if a2 else [])][:3]} "
          f"windows={win2[:2]}")


# ── balance ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def _resolve(balance: str, as_of: str):
    FEED[("households", "*")] = [{"id": HH_A, "name": NAME + "household"}]
    FEED[("accounts", HH_A)] = [
        {"id": X_BAL, "rep_code": "R1", "account_number": FULL_NUMBER, "account_type": "INDIVIDUAL",
         "account_name": NAME + "balance", "status": "ACTIVE", "date_open": "2020/01/02", "date_closed": None,
         "as_of_date": as_of, "account_balance": balance},
        {"id": X_T, "account_name": NAME + "t", "status": "ACTIVE", "account_balance": None},
        {"id": X_L, "account_name": NAME + "l", "status": "ACTIVE", "account_balance": None}]
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            return await AI.resolve_identity(c, org_id=str(ORG_A), environment=SANDBOX, transport=MOCK)


async def y_balance(conn):
    section("[Y] Account balance: stored with its as-of date (valid-axis) and shown on the account page; treasury "
            "unchanged; the account number never stored whole")
    reset_feed()
    h0 = await holdings(ACC_BAL)
    abd0 = int(await platform(conn, "SELECT count(*) FROM account_balances_daily WHERE org_id = ANY($1::uuid[])",
                              FIXTURE_ORGS, fetch="fetchval"))
    out = await _resolve("125430.17", "2026/11/12")
    entry = next((r for r in out["resolved"] if r["altruist_account_id"] == X_BAL), {})
    live = await platform(conn, "SELECT account_balance, as_of_date, source_system, environment, custodian_status FROM "
                                "account_custodian_balances WHERE org_id = $1::uuid AND account_id = $2::uuid AND "
                                "valid_to IS NULL", ORG_A, ACC_BAL)
    check(len(live) == 1 and live[0]["account_balance"] == Decimal("125430.17") and live[0]["as_of_date"] ==
          date(2026, 11, 12) and live[0]["source_system"] == "altruist" and live[0]["environment"] == SANDBOX
          and live[0]["custodian_status"] == "ACTIVE" and (entry.get("balance") or {}).get("stored") is True,
          "account_balance 125430.17 stored as the CUSTODIAN balance with its as-of date 2026-11-12, source altruist, "
          "environment sandbox, status ACTIVE", dumps([dict(r) for r in live]))
    await _resolve("125430.17", "2026/11/12")
    n_all = int(await platform(conn, "SELECT count(*) FROM account_custodian_balances WHERE account_id = $1::uuid",
                               ACC_BAL, fetch="fetchval"))
    check(n_all == 1, "an unchanged balance writes nothing (still one row)", f"{n_all}")
    await _resolve("125500.00", "2026/11/13")
    rows = await platform(conn, "SELECT account_balance, valid_to FROM account_custodian_balances WHERE account_id = "
                                "$1::uuid ORDER BY valid_from", ACC_BAL)
    check(len(rows) == 2 and rows[0]["valid_to"] is not None and rows[1]["valid_to"] is None
          and rows[1]["account_balance"] == Decimal("125500.00"),
          "a changed balance closes the live row and inserts the new one (Rule 3, valid axis)", dumps([dict(r) for r in rows]))
    code, body = await api(U_ST, "GET", f"/api/v1/custodian-feed/accounts/{ACC_BAL}")
    bal = (body.get("balances") or [{}])[0]
    check(code == HTTP_OK and bal.get("account_balance") == "125500.00" and bal.get("as_of_date") == "2026-11-13"
          and body.get("permissions", {}).get("can_read") is True and body.get("vocabularies", {}).get("editable") == [],
          "GET /custodian-feed/accounts/{id}: the custodian balance with its as-of date (the account page shows "
          "'Custodian balance (as of …)'); envelope with empty editable", f"{code} {dumps(body)[:300]}")
    h1 = await holdings(ACC_BAL)
    abd1 = int(await platform(conn, "SELECT count(*) FROM account_balances_daily WHERE org_id = ANY($1::uuid[])",
                              FIXTURE_ORGS, fetch="fetchval"))
    check(abd0 == abd1 == 0 and h0["cash"] == h1["cash"] and h0["cash_as_of"] == h1["cash_as_of"],
          "treasury unchanged: no account_balances_daily row written; the engine's cash reading identical before / after",
          f"abd {abd0}→{abd1} cash {h0['cash']}→{h1['cash']}")
    stored = await platform(conn, "SELECT count(*) FROM account_import_exceptions WHERE org_id = $1::uuid AND "
                                  "row_payload::text LIKE $2", ORG_A, f"%{FULL_NUMBER}%", fetch="fetchval") \
        if await _has_col(conn, "account_import_exceptions", "row_payload") else 0
    leaks = [b for b in S["bodies"] if FULL_NUMBER in b]
    check(int(stored or 0) == 0 and not leaks, "the feed's full account number appears in no stored row and no response "
          "(last four only)", f"stored={stored} bodies={len(leaks)}")


async def _has_col(conn, table: str, col: str) -> bool:
    return bool(await platform(conn, "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = $1 "
                                     "AND column_name = $2)", table, col, fetch="fetchval"))


# ── cost basis ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_lots(conn):
    section("[Y] Cost basis: custodian lots stored (source, open date, both bases); default lot source unchanged (tax "
            "engine identical before / after); switched to custodian → the engine reads them; reconciliation shows a "
            "planted quantity and basis difference; no Hollisworks lot overwritten")
    reset_feed()
    FEED[("positions", X_L)] = [pos_row("ar1v-p-l1", "10", "1000.00", D0)]
    await sync_a(D0)
    pid = await position_of(conn, ACC_L)
    if not pid:
        check(False, "the lots account has a synced position")
        return
    hw = await platform(conn, "INSERT INTO portfolio.tax_lots (org_id, position_id, acquired_date, quantity, "
                              "original_cost, adjusted_cost, status, source_system) VALUES ($1, $2::uuid, $3, 10, 900, "
                              "900, 'open', 'manual') RETURNING id::text", ORG_A, pid, date(2025, 1, 2), fetch="fetchval")
    h0 = await holdings(ACC_L)
    tl0 = await fingerprint(conn, "SELECT id, quantity, original_cost, adjusted_cost, status, valid_to, system_to FROM "
                                  "portfolio.tax_lots WHERE org_id = $1::uuid", str(ORG_A))
    FEED[("cost_basis", X_L)] = [lot_row("ar1v-lot-1", "6", "600.00", "500.00", "510.00", "2024/11/11", date(2026, 11, 12)),
                                 lot_row("ar1v-lot-2", "3", "300.00", "290.00", "300.00", "2026-10-01", date(2026, 11, 12))]
    res = await sync_a(D0)
    a = acct_result(res, X_L)
    lots = [dict(r) for r in await platform(
        conn, "SELECT external_id, source_system, environment, open_date, quantity, cost_basis_unadjusted, "
              "cost_basis_adjusted, global_security_id, as_of_date FROM portfolio.custodian_tax_lots WHERE org_id = "
              "$1::uuid AND account_id = $2::uuid AND valid_to IS NULL ORDER BY external_id", ORG_A, ACC_L)]
    check(len(lots) == 2 and lots[0]["source_system"] == "altruist" and lots[0]["environment"] == SANDBOX
          and lots[0]["open_date"] == date(2024, 11, 11) and lots[1]["open_date"] == date(2026, 10, 1)
          and lots[0]["cost_basis_unadjusted"] == Decimal("500.00") and lots[0]["cost_basis_adjusted"] == Decimal("510.00")
          and all(str(x["global_security_id"]) == str(SEC_F) for x in lots) and (a.lots or {}).get("stored") == 2,
          "two custodian lots stored: source altruist / sandbox, open dates (YYYY/MM/DD and ISO), both bases, matched "
          "to the security", dumps(lots))
    h1 = await holdings(ACC_L)
    tl1 = await fingerprint(conn, "SELECT id, quantity, original_cost, adjusted_cost, status, valid_to, system_to FROM "
                                  "portfolio.tax_lots WHERE org_id = $1::uuid", str(ORG_A))
    check(h0["lots"] == h1["lots"] and h1["lot_source"] == "hollisworks" and len(h1["lots"]) == 1
          and str(h1["lots"][0][0]) == hw,
          "default lot source 'hollisworks': the tax engine's lots are IDENTICAL before / after the custodian lots",
          f"{h0['lots']} → {h1['lots']}")
    check(tl0 == tl1, "no Hollisworks lot written or overwritten by the cost-basis sync (tax_lots count + checksum)",
          f"{tl0} → {tl1}")
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            rec = await CL.reconcile(c, str(ORG_A), str(ACC_L))
    row = next((r for r in rec["rows"] if r["key"] == str(SEC_F)), {})
    check(row.get("status") == "differs" and row.get("quantity_difference") == "-1" and row.get("basis_difference") ==
          "-90.00" and row.get("hollisworks", {}).get("quantity") == "10" and row.get("custodian", {}).get("lots") == 2,
          "reconciliation: Hollisworks 10 @ 900 vs custodian 9 @ 810 → quantity −1, basis −90, flagged 'differs'",
          dumps(row))
    body = {"custodian_cost_basis_method": "unknown", "lot_source": "custodian", "reason": "use the custodian's lots"}
    cr, br = await api(U_RD, "POST", f"/api/v1/lot-method/accounts/{ACC_L}/changes", body)
    check(cr == HTTP_FORBIDDEN and "manage_proposals" in dumps(br), "the lot-source change refused 403 naming "
          "manage_proposals for a reader (valid body — only the gate refuses)", f"{cr} {dumps(br)[:200]}")
    c1, b1 = await api(U_ST, "POST", f"/api/v1/lot-method/accounts/{ACC_L}/changes", body)
    change = (b1.get("change") or {}).get("id")
    pending_src = await CL_lot_source(ACC_L)
    c2, b2 = await api(U_AP, "POST", f"/api/v1/lot-method/changes/{change}/approve", {"note": "verified"})
    check(c1 == HTTP_CREATED and pending_src == "hollisworks" and c2 == HTTP_OK,
          "lot source requested by the advisor (201; still 'hollisworks' while pending) and approved by a second "
          "person (200) — the existing maker-checker lot-method change", f"{c1} {dumps(b1)[:200]} / {c2} {dumps(b2)[:200]}")
    h2 = await holdings(ACC_L)
    want = sorted([(None, Decimal("3"), E.d(Decimal("300.00")) / E.d(Decimal("3")), date(2026, 10, 1)),
                   (None, Decimal("6"), E.d(Decimal("510.00")) / E.d(Decimal("6")), date(2024, 11, 11))],
                  key=lambda t: (str(t[0]), t[1]))
    check(h2["lot_source"] == "custodian" and h2["lots"] == want,
          "switched to 'custodian': the tax engine reads the custodian lots (no Hollisworks lot id; adjusted basis per "
          "unit; open dates)", f"{h2['lots']}")
    cf, bf = await api(U_ST, "GET", f"/api/v1/custodian-feed/accounts/{ACC_L}")
    recon = (bf.get("reconciliation") or {}).get("rows") or []
    check(cf == HTTP_OK and bf.get("lot_source") == "custodian" and len(bf.get("lots") or []) == 2
          and any(r.get("status") == "differs" for r in recon),
          "the account page envelope: the custodian lots, the reconciliation with the difference, lot source custodian",
          f"{cf} {dumps(bf)[:300]}")
    FEED[("cost_basis", X_L)] = [lot_row("ar1v-lot-2", "3", "300.00", "290.00", "305.00", "2026-10-01", date(2026, 11, 13))]
    res3 = await sync_a(D0)
    a3 = acct_result(res3, X_L)
    live = await platform(conn, "SELECT external_id, cost_basis_adjusted FROM portfolio.custodian_tax_lots WHERE "
                                "account_id = $1::uuid AND valid_to IS NULL", ACC_L)
    closed = int(await platform(conn, "SELECT count(*) FROM portfolio.custodian_tax_lots WHERE account_id = $1::uuid "
                                      "AND valid_to IS NOT NULL", ACC_L, fetch="fetchval"))
    check(len(live) == 1 and live[0]["external_id"] == "ar1v-lot-2" and live[0]["cost_basis_adjusted"] ==
          Decimal("305.00") and closed == 2 and (a3.lots or {}).get("closed") == 1,
          "a changed lot closes and re-inserts; a lot missing from the next complete snapshot is closed (valid axis)",
          f"{dumps([dict(r) for r in live])} closed={closed}")
    tl2 = await fingerprint(conn, "SELECT id, quantity, original_cost, adjusted_cost, status, valid_to, system_to FROM "
                                  "portfolio.tax_lots WHERE org_id = $1::uuid", str(ORG_A))
    check(tl2 == tl0, "still no Hollisworks lot overwritten after the switch and the re-syncs")


async def CL_lot_source(account) -> str:
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            return await CL.lot_source_for(c, str(ORG_A), str(account))


# ── simulator + conformance ─────────────────────────────────────────────────────────────────────────────────────
class Rewrite(httpx.AsyncBaseTransport):
    """A planted divergence on the simulator's responses: 'flat' envelope, 'iso' dates, 'wrong_path' for positions."""

    def __init__(self, inner, mode: str):
        self.inner, self.mode = inner, mode

    async def handle_async_request(self, request):
        if self.mode == "wrong_path" and request.url.path.endswith("/positions/"):
            return httpx.Response(404, json={"detail": "not here"}, headers={"x-counterparty-simulated": "true"})
        res = await self.inner.handle_async_request(request)
        if "/oauth2/" in request.url.path or "/api/oauth2/" in request.url.path:
            return res
        raw = await res.aread()
        try:
            body = json.loads(raw)
        except ValueError:
            return httpx.Response(res.status_code, content=raw, headers=res.headers)
        if self.mode == "flat" and isinstance(body, dict) and isinstance(body.get("data"), dict):
            body = {k: v for k, v in body["data"].items() if isinstance(v, list)}
        if self.mode == "iso":
            body = json.loads(re.sub(r'"(\d{4})/(\d{2})/(\d{2})"', r'"\1-\2-\3"', json.dumps(body)))
        hdr = {k: v for k, v in res.headers.items() if k.lower() not in ("content-length", "content-encoding")}
        return httpx.Response(res.status_code, json=body, headers=hdr)


async def y_simulator(conn):
    section("[Y] Simulator + conformance on the documented shapes; a planted divergence FAILS citing the documented "
            "row; orders / transfers groups still PASS and are labelled ASSUMED")
    out_path = pathlib.Path(tempfile.gettempdir()) / f"altread1_conformance_{secrets.token_hex(4)}.json"
    code, out, err = run_cmd([sys.executable, str(CONFORMANCE), "--target", "simulator", "--json-out", str(out_path)],
                             REPO, timeout=SECTION_TIMEOUT - 60, env={**os.environ})
    data = {}
    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    res = data.get("results") or []
    by = {r["id"]: r for r in res}
    read_ids = [i for i in by if re.match(r"^C(0[0-9]|1[0-7])b?$", i)]
    documented = [i for i in read_ids if i not in ("C08", "C15")]
    check(code == 0 and data.get("failed") == 0 and len(read_ids) >= 18
          and all(by[i]["passed"] in (True, None) for i in read_ids),
          f"the conformance suite in-process: every documented read check PASSES ({data.get('passed')} PASS, "
          f"{data.get('failed')} FAIL)", f"{code} {[(r['id'], r['detail']) for r in res if r['passed'] is False][:4]} "
          f"{err[-200:]}")
    check(all(by[i]["contract"].startswith("DOCUMENTED") for i in documented),
          "every read row cites its DOCUMENTED page", f"{[(i, by[i]['contract']) for i in documented if not by[i]['contract'].startswith('DOCUMENTED')][:3]}")
    ot = [r for r in res if r["id"][:1] in ("O", "T")]
    check(len(ot) >= 28 and all(r["passed"] is True for r in ot) and all(r["contract"].startswith("ASSUMED") for r in ot),
          "orders / transfers groups still PASS and every row is labelled ASSUMED (not part of Altruist's published API)",
          f"{len(ot)} {[(r['id'], r['contract'][:30]) for r in ot if not r['contract'].startswith('ASSUMED')][:3]}")
    check(by.get("C20", {}).get("passed") is True, "the conformance run opened no socket outside the database")
    # the planted divergences (read path only; orders / transfers not run)
    slug = SLUG_PREFIX + "div-" + secrets.token_hex(3)
    import cpsim_altruist_conformance as FXC
    async with SDB.tx() as c:
        cp = await FX.create_counterparty(c, name=NAME + "divergence", slug=slug)
        clock = await c.fetchval("SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = $1::uuid "
                                 "AND valid_to IS NULL", cp["id"])
        await FX.plant_book(c, cp["id"], FXC.fixture_book(None, clock))
    S["issued"].append(cp["key"])
    root = f"{SIM_BASE}/cpsim/c/{slug}"
    hosts = {"api_base": root, "login_base": root}
    base_t = FX.TimeoutASGITransport(app=FX.mounted_app())
    base = await ACF.run(environment=AO.SIMULATOR, hosts=hosts, credentials=(cp["client_id"], cp["key"]),
                         transport=base_t, today=clock)
    check(base["failed"] == 0, "baseline (no divergence) on the divergence fixture: 0 FAIL",
          f"{[(r['id'], r['detail']) for r in base['results'] if r['passed'] is False][:3]}")
    for mode, cid, page in (("flat", "C09", "DOCUMENTED endpoints"), ("iso", "C16", "DOCUMENTED json-code-examples"),
                            ("wrong_path", "C12", "DOCUMENTED endpoints positions")):
        r = await ACF.run(environment=AO.SIMULATOR, hosts=hosts, credentials=(cp["client_id"], cp["key"]),
                          transport=Rewrite(base_t, mode), today=clock)
        row = next((x for x in r["results"] if x["id"] == cid), {})
        check(row.get("passed") is False and row.get("contract", "").startswith(page),
              f"planted divergence '{mode}' FAILS {cid}, citing {page!r}", dumps(row)[:300])


# ── isolation + member visibility ──────────────────────────────────────────────────────────────────────────────
async def y_isolation(conn):
    section("[Y] Isolation both directions (balances, custodian lots, reconciliation); member visibility unchanged")
    await platform(conn, "INSERT INTO account_custodian_balances (org_id, account_id, source_system, environment, "
                         "account_balance, as_of_date) VALUES ($1, $2, 'altruist', 'sandbox', 777.77, $3) "
                         "ON CONFLICT DO NOTHING", ORG_B, ACC_B, D0, fetch="execute")
    await platform(conn, "INSERT INTO portfolio.custodian_tax_lots (org_id, account_id, source_system, environment, "
                         "external_id, quantity, cost_basis_adjusted, as_of_date) VALUES ($1, $2, 'altruist', 'sandbox', "
                         "'iso-lot-b', 1, 10, $3) ON CONFLICT DO NOTHING", ORG_B, ACC_B, D0, fetch="execute")
    for org, own, other in ((ORG_A, ACC_L, ACC_B), (ORG_B, ACC_B, ACC_L)):
        await platform(conn, "INSERT INTO portfolio.custodian_tax_lots (org_id, account_id, source_system, environment, "
                             "external_id, quantity, cost_basis_adjusted, as_of_date) SELECT $1::uuid, $2::uuid, 'altruist', "
                             "'sandbox', 'iso-lot-' || $1::uuid::text, 2, 20, $3::date WHERE NOT EXISTS (SELECT 1 FROM "
                             "portfolio.custodian_tax_lots WHERE external_id = 'iso-lot-' || $1::uuid::text AND "
                             "valid_to IS NULL)", org, own, D0, fetch="execute")
    bypass = await platform(conn, "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user",
                            fetch="fetchval")
    check(bypass is False, "the isolation reads run on a role that does not bypass RLS (asserted)")
    for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        for t in ("public.account_custodian_balances", "portfolio.custodian_tax_lots"):
            mine = int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1::uuid", org, fetch="fetchval"))
            theirs = int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1::uuid", other,
                                      fetch="fetchval"))
            check(mine >= 1 and theirs == 0, f"{t}: org {str(org)[-4:]} sees its own rows ({mine}) and none of org "
                  f"{str(other)[-4:]}'s", f"{mine} / {theirs}")
        _r, err = await rolled_back(conn, lambda c, o=other: c.execute(
            "INSERT INTO portfolio.custodian_tax_lots (org_id, account_id, source_system, environment, external_id, "
            "quantity, as_of_date) VALUES ($1, $2, 'altruist', 'sandbox', 'iso-cross', 1, $3)", o,
            ACC_B if o == ORG_B else ACC_L, D0), org=org)
        check(err is not None, f"a write naming the other org from org {str(org)[-4:]}'s context is refused by RLS",
              f"{type(err).__name__ if err else None}")
    c1, b1 = await api(U_STB, "GET", f"/api/v1/custodian-feed/accounts/{ACC_L}")
    c2, b2 = await api(U_ST, "GET", f"/api/v1/custodian-feed/accounts/{ACC_B}")
    c3, b3 = await api(U_STB, "GET", f"/api/v1/custodian-feed/accounts/{ACC_B}")
    check(c1 == HTTP_NOT_FOUND and c2 == HTTP_NOT_FOUND, "the route: another org's account is a 404 both ways",
          f"{c1} {c2}")
    own_ext = {x.get("external_id") for x in b3.get("lots") or []}
    check(c3 == HTTP_OK and "iso-lot-b" in own_ext and all(not str(e).startswith(f"iso-lot-{ORG_A}") for e in own_ext)
          and all(b.get("account_balance") == "777.77" for b in b3.get("balances") or []),
          "org B's own account: its balance and lots only", f"{c3} {sorted(own_ext)[:5]}")
    m1, mb = await api(U_M, "GET", f"/api/v1/custodian-feed/accounts/{ACC_L}")
    m2, _ = await api(U_M, "GET", f"/api/v1/custodian-feed/accounts/{ACC_T}")
    check(m1 == HTTP_OK and m2 == HTTP_NOT_FOUND, "a MEMBER sees their own family's account (delegate grant on E_A) and "
          "not another family's in the same org (404)", f"{m1} {m2} {dumps(mb)[:160]}")


# ── unchanged / routes / front end ────────────────────────────────────────────────────────────────────────────
async def y_unchanged(conn):
    section("[Y] Sandbox / production unchanged except the documented corrections (diff review); portfolio_altruist.py "
            "untouched; no network")
    self_unchanged(check, find)
    n = _src("docs/altruist/ALTREAD1_NOTES.md")
    check("## 4. Diff review" in n and "Filled in at Task 9" not in n, "the diff review is recorded in ALTREAD1_NOTES §4")
    check(not NETWORK_CALLS and not MODEL_CALLS, "no network call and no model call so far",
          f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py and visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=900,
                                 env={**os.environ, "AUTHZ_TEST_MODE": "1"})
        check(code == 0 and "CURRENT" in out, f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")


async def y_front_end(conn):
    section("[Y] UI render tests (balance, lots, reconciliation, lot-source setting, refresh expiry); node --test tests/; "
            "npm run build")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    declared = node_tests_declared()
    check(declared >= NODE_TEST_MIN and c1 == 0 and n is not None and p is not None and int(n.group(1)) == declared
          and int(p.group(1)) == declared and re.search(r"^# fail 0$", o1, re.M) is not None,
          f"the altread1 render tests ({NODE_TEST}, counted by their file: {declared}) all pass",
          f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1800)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    mode = VT.regression_mode()
    section(f"[Y] Regressions — each ONCE, flat, AFTER teardown, VERIFY_NESTED=1, its OWN final TOTAL line "
            f"(VERIFY_REGRESSIONS={mode})")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)"
              + (" [--self-checks-only]" if mode == "fast" else ""), f"{t.detail()}; {fails[:3]} {err[-200:]}")
        print(f"[INFO] {script} ({mode}): {t.line.strip() if t.line else 'no TOTAL line'}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await asyncio.wait_for(res, SECTION_TIMEOUT)
    except asyncio.TimeoutError:
        check(False, f"{name} exceeded {SECTION_TIMEOUT} s")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    before = await counts(conn)
    print(f"[INFO] fixture rows before cleanup: {sum(before.values())} { {t: n for t, n in sorted(before.items()) if n} }")
    real = await real_fingerprints(conn)
    try:
        print(f"[INFO] teardown removed: {await teardown_retrying()}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    after = await counts(conn)
    nonzero = {t: n for t, n in sorted(after.items()) if n}
    check(not nonzero, f"every counted table at zero fixture rows after cleanup ({len(after)} tables)", f"{nonzero}")
    check(await leftovers(conn) == 0, "zero fixture rows remain (public + cpsim)")
    now = await real_fingerprints(conn)
    for k in real:
        check(now.get(k) == real[k], f"real {k} unchanged by cleanup (count + checksum)", f"{real[k]} → {now.get(k)}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


FIXTURE_SECTIONS = (("client", y_client), ("paging", y_paging), ("envelope", y_envelope), ("dates", y_dates),
                    ("transactions", y_transactions), ("balance", y_balance), ("lots", y_lots),
                    ("simulator", y_simulator), ("isolation", y_isolation), ("unchanged", y_unchanged))
ONLY_NEEDS = {name: () for name, _f in FIXTURE_SECTIONS}


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    want = ["simulator" if x == "conformance" else x for x in want]
    bad = [x for x in want if x not in ONLY_NEEDS and x not in ("seed", "front_end")]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, front_end, {', '.join(ONLY_NEEDS)}")
    if "front_end" in want and want != ["front_end"]:
        raise SystemExit("--only=front_end runs alone (no seed, no DB fixtures)")
    names = {x for x in want if x != "seed"}
    return [n for n, _f in FIXTURE_SECTIONS if n in names]


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working DATABASE_URL", how)
        print(total_line())
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        restore_stubs()
        return await cleanup_only(conn)
    if any(a == "--only=front_end" for a in sys.argv[1:]):
        only_sections()
        try:
            await run("front end", y_front_end, conn)
        finally:
            restore_stubs()
            await close_conn(conn)
        print(total_line())
        return 0 if _n_fail == 0 else 1
    pre = None
    only = only_sections()
    if only is not None:
        print(f"[INFO] --only: seed + {only or '(seed only)'}")
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        bypass = await platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user",
                                fetch="fetchval")
        check(bypass is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
        if not await guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        if only is None:
            await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        chosen = [(n, f) for n, f in FIXTURE_SECTIONS if only is None or n in only]
        if S.get("seeded"):
            for name, fn in chosen:
                await run(name, fn, conn)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        leaks = [v[:8] + "…" for v in S["issued"] if v and any(v in b for b in S["bodies"])]
        check(not leaks, "no response body carried a token, a key or a client secret", f"{leaks[:3]}")
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"network: {NETWORK_CALLS[:3]} model: {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows unchanged")
        restore_stubs()
        await close_app_pools()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            print(f"[INFO] teardown removed: {await teardown_retrying()}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (orgs, users, grants, accounts, crosswalks, connections, positions, "
                  "transactions, assets, Hollisworks + custodian lots, custodian balances, lot-method rows, delegate "
                  "grants, audit rows, the fixture security, every cpsim fixture table incl. book_lots)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in sorted(pre):
                    check(post[t] == pre[t], f"{t} at its pre-test count (fixture rows)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k]} → {after.get(k)}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
        if S.get("no_teardown"):
            print(total_line())
    if only is not None:
        print("[SKIP] regression — --only one-off run")
    elif os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(total_line())
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main_async())
    except BaseException as exc:  # noqa: BLE001 — TOTAL on every exit path, the traceback printed
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise
        traceback.print_exc()
        check(False, "the verify crashed", f"{type(exc).__name__}: {_redact(exc)}")
        print(total_line())
        rc = 1
    sys.exit(rc)
