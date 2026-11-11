"""verify_cpsim02 — CPSIM02: the simulated custodian ANSWERS. An Altruist-compatible read API on the simulator (OAuth
authorize / token / refresh; households, accounts, positions, transactions) served from a BOOK an admin uploads
(preview → accept, every rejection reason, replace mode, CSV / JSON round trip, append-only upload log); the counterparty
clock decides what is served; webhooks OUT signed the way the receiver verifies (second secret, forced environment,
the real-secret-claims-simulator refusal, no org_id in the response); the EXISTING Altruist client pointed at it through
a third environment ``simulator`` (explicit branches, no cross-environment credentials, the production fallback gone,
sandbox / production byte-identical); connect without a browser (D4) → identity → positions + transactions synced and
idempotent; isolation (simulator vs real crosswalk both directions, the demo-household rule in a NON-test org, flag off,
cross-org both directions); the CONFORMANCE suite (passes, fails on a planted divergence citing the contract row,
refuses sandbox without the flag); Admin → Counterparties (Super Admin only, audited by fixture ids, render tests);
the menu-visibility harness standalone.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim02.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback) and
exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx network
transport refuses and records (asserted empty at the end). The simulator, the admin API and the webhook receiver are
all called IN-PROCESS through ASGI transports against the real ``main.app`` (which mounts the simulator); the client's
own ``transport`` injection (``altruist_oauth.set_simulator_transport`` — simulator environment ONLY) and the
simulator's (``cpsim.webhooks.set_transport``) point there. No model call.

FIXTURE SECRETS are generated in-process and never printed: the simulator's client id / key come from the fixture
counterparty (key shown once by the admin create call); ALTRUIST_SIMULATOR_WEBHOOK_SECRET = cpsim_whsec_ + random;
ALTRUIST_WEBHOOK_SECRET is replaced IN THIS PROCESS by a random fixture value; CPSIM_BASE_URL is
https://cpsim.verify.invalid (never resolved — every call is in-process).

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile
call. REAL: the app, the mounted sub-app, cpsim.book / oauth / webhooks / registry, the Altruist client, identity and
sync, the receiver, the database (CHECKs, the append-only trigger, the org-setting guard, the may-connect wrapper,
role cpsim_app), RLS. The script's own connection does not bypass RLS (rolbypassrls = false, asserted). TIME IS
INJECTED: cpsim.registry.set_today (Friday 2026-11-13 — the counterparty clock starts there), cpsim.mirror.set_now and
cpsim.oauth.set_now (15:30 UTC; moved two hours on to expire an access token).

THE DELAY PROOF: httpx's ASGITransport ignores timeouts, so the simulator is reached through
``_cpsim_fixtures.TimeoutASGITransport``, which turns the CLIENT's own read timeout into ``httpx.ReadTimeout``. The
behaviour row says delay 16 s (over the client's 15 s default); the client function is called with an injected
timeout of DELAY_CLIENT_TIMEOUT_S = 1.0 s so the section stays fast — the client raises AltruistOAuthError from its
own transport-error branch within a few seconds.

FIXTURES (ids 99000000-0000-0000-0000-0000c952…; sign-in tag cpsim02verify): TEST orgs T and T2 inside the reserved
prefix (is_test asserted); the NON-test org R (id OUTSIDE the prefix — REP6FIX2's trigger forces is_test inside it),
its demo / plain households, accounts, crosswalks, connection and every synced row exist ONLY inside one ROLLED-BACK
transaction (a real org's demo rows are undeletable by design). Users with real user_roles grants (the app's decision
on each asserted): U_SA Super Admin, U_OA org admin, U_CU / U_CU2 custody managers (manage_custody_connections) of T /
T2. Counterparties carry the reserved slug prefix ``cpsim2verify-`` (asserted to match no real row). Fixture
securities are named 'CPSIM02 VERIFY …' with identifiers CPSIM02… / CPSV… (asserted to match no real row); their
platform prices carry provider_key 'cpsim02verify'.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by this run's fixture ids; the cpsim
tables have no org_id, so they are passed by fixture counterparty (slug prefix) with ``cpsim.allow_teardown = 'fixture'``
(the upload log yields only to its owner with that switch); the security master by fixture name; public rows by
fixture org / user / event ids. Every touched table re-read for its exact pre-test count BY FIXTURE IDS ONLY (audit_log
and altruist_webhook_events by fixture ids). Real rows — every non-fixture counterparty and book, the cpsim settings of
every real org, demo-tagged entities, real Altruist connections / crosswalks / webhook events — fingerprinted before
and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<read_api|behaviour|book|clock|client|e2e|isolation|webhooks|conformance|admin|safety|harness>: the seed, the
named sections (and what they need), the full teardown; no routes / front end / regression. With VERIFY_NESTED=1 the
regression prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import csv
import hashlib
import hmac
import importlib.util
import inspect
import io
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
import traceback
from collections import Counter
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

SPRINT_KEY = "cpsim02"
BEFORE_REF = "122ac75"   # cpsim02-work's base: 'merge cpsim01'
_P = "/api/v1/admin/counterparties"
_I = _P + "/{counterparty_id}"
_SA = "super_admin"
ROUTES = {("GET", _I + "/book"): _SA, ("GET", _I + "/book/template"): _SA, ("POST", _I + "/book/preview"): _SA,
          ("POST", _I + "/book/accept"): _SA, ("GET", _I + "/book/export"): _SA,
          ("POST", _I + "/webhooks/test"): _SA, ("GET", _I + "/connections"): _SA,
          ("PUT", _I + "/connections/{target_org_id}"): _SA,
          ("POST", _I + "/connections/{target_org_id}/connect"): _SA,
          ("POST", _I + "/connections/{target_org_id}/sync"): _SA,
          ("GET", _I + "/conformance"): _SA, ("POST", _I + "/conformance"): _SA}
SIM_ROUTES = {("GET", "/cpsim/c/{slug}/oauth/authorize"), ("POST", "/cpsim/c/{slug}/oauth/token"),
              ("GET", "/cpsim/c/{slug}/api/v2/households"), ("GET", "/cpsim/c/{slug}/v2/accounts"),
              ("GET", "/cpsim/c/{slug}/v2/positions"), ("GET", "/cpsim/c/{slug}/v2/transactions")}
NODE_TEST = "tests/cpsim02/cpsim02.test.mjs"
NODE_TEST_COUNT = 8
NOTES = REPO / "docs" / "cpsim" / "CPSIM02_NOTES.md"
CONTRACT = REPO / "docs" / "cpsim" / "ALTRUIST_CONTRACT.md"
ADMIN_DOC = REPO / "docs" / "cpsim" / "CPSIM_ADMIN.md"
DESIGN = REPO / "docs" / "cpsim" / "COUNTERPARTY_SIMULATOR_DESIGN_v1.md"
MIGRATION = REPO / "docs" / "cpsim02_part1.sql"
MIGRATION_01 = REPO / "docs" / "cpsim01_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
BOUNDARY = HERE / "cpsim_boundary_check.py"
CONFORMANCE = HERE / "cpsim_altruist_conformance.py"
HARNESS = HERE / "menuvisibility_harness.mjs"
REGRESSION_SET = REPO / "scripts" / "regression_set.txt"
#: the offline Altruist verifies (Task 1e). sprint1 and sprint4 open a socket to the real Altruist stage host (an
#: unmocked call_households — sprint1 on purpose, sprint4 from the callback's background auto-sync) — NOT run here.
REGRESSIONS = ("verify_cpsim01.py", "verify_authz1.py", "verify_altruist_sprint2_identity_resolution.py",
               "verify_altruist_sprint3_positions_transactions_sync.py",
               "verify_altruist_sprint5_sync_orchestration.py",
               "verify_altruist_sprint6_realtime_webhook_receiver.py")
NOT_RUN = {"verify_altruist_sprint1_openapi_connection.py": "real network: call_households(sandbox) unmocked (:322)",
           "verify_altruist_sprint4_connection_api.py": "real network: the callback's background auto-sync (:511)"}


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cpsim02's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cpsim02 changed no existing route's gate and removed none (the /altruist routes keep theirs)",
          f"{o.where()}; changed={sorted(changed)[:3]} removed={o.removed[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)} admin routes",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate (super_admin) at cpsim02's own end point",
          f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cpsim02's routes all still exist today with the same gate", f"{drift[:4]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    # cpsim02 fix 1 (B): each JSON row carries "methods" (a list, as authz1_routes.simulator_routes() writes it)
    sim = {(m, r.get("path")) for r in data.get("simulator_routes", []) for m in (r.get("methods") or [])}
    check(len(sim) > 0, "docs/AUTHZ1_ROUTES.json carries a non-empty simulator_routes section", f"{len(sim)}")
    check(SIM_ROUTES <= sim, "the six Altruist-compatible simulator routes are listed in the simulator section of "
          "docs/AUTHZ1_ROUTES (own auth, not app routes)", f"missing={sorted(SIM_ROUTES - sim)}")


def self_static(check, find):
    n = _src("docs/cpsim/CPSIM02_NOTES.md")
    for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11"):
        check(f"**{d}" in n or f"| {d} " in n or f"\n{d}." in n or f"{d} —" in n, f"CPSIM02_NOTES records {d} as built")
    for w in ("altruist_connections_environment_check", "ADMIN_ROUTER_ONLY_SETTING_KEYS", "TimeoutASGITransport",
              "counterparty_sim_may_connect", "not_a_demo_household", "Reuse inventory"):
        check(w in n, f"CPSIM02_NOTES names {w!r}")
    c = _src("docs/cpsim/ALTRUIST_CONTRACT.md")
    check("cpsim02" in c and "served" in c.lower(), "ALTRUIST_CONTRACT.md marks the §6 items served by cpsim02")
    oa = _code_only(_src("apps/api/services/altruist_oauth.py"))
    check('prefix = "ALTRUIST_SANDBOX"' in oa and 'elif environment == "production":' in oa and
          'prefix = "ALTRUIST_SIMULATOR"' in oa and "never a fallback to production" in _src(
              "apps/api/services/altruist_oauth.py"),
          "client_credentials: explicit sandbox / production / simulator branches; anything else raises")
    rs = _src("scripts/regression_set.txt")
    check(bool(re.search(r"^verify_cpsim02\.py\s*$", rs, re.M)), "verify_cpsim02.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    check("cpsim02" in st.lower(), "PROJECT_STATUS records cpsim02")
    adm = _src("docs/cpsim/CPSIM_ADMIN.md")
    check(all(w in adm for w in ("Upload a book", "Connect", "Conformance", "ALTRUIST_SIMULATOR_WEBHOOK_SECRET",
                                 "CPSIM_BASE_URL")), "CPSIM_ADMIN.md: the book, connecting an org, webhooks, "
          "conformance and the operator variables")
    todo = _src("docs/OUTSTANDING_TODO_LIST.md")
    check("7.12" in todo or "browser redirect leg" in todo, "the real OAuth browser leg (§7.12) is logged as "
          "outstanding (not fixed here)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


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
from cpsim import mirror as SM  # noqa: E402
from cpsim import oauth as SO  # noqa: E402
from cpsim import registry as R  # noqa: E402
from cpsim import webhooks as SW  # noqa: E402
from services import altruist_conformance as ACF  # noqa: E402
from services import altruist_identity as AI  # noqa: E402
from services import altruist_oauth as AO  # noqa: E402
from services import altruist_positions_sync as APS  # noqa: E402
from services import altruist_webhook as AW  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import portfolio_assets as PA  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cpsim02verify"
NAME = "CPSIM02 VERIFY "
SLUG_PREFIX = "cpsim2verify-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c952"
FRIDAY = date(2026, 11, 13)           # the counterparty clock starts here (registry today injected)
THU = date(2026, 11, 12)              # the first book snapshot (served on Friday)
MON = date(2026, 11, 16)              # the second snapshot (served once the clock advances)
FIXED_NOW = datetime(2026, 11, 13, 15, 30, 0, tzinfo=timezone.utc)
SIM_BASE = "https://cpsim.verify.invalid"
REDIRECT = "https://verify.cpsim02.invalid/altruist/callback"
DELAY_SECONDS = 16                    # over the client's 15 s default
DELAY_CLIENT_TIMEOUT_S = 1.0          # the injected client timeout (keeps the section fast)
DELAY_MAX_ELAPSED_S = 8.0
HTTP_OK, HTTP_FOUND, HTTP_BAD, HTTP_UNAUTH, HTTP_FORBIDDEN, HTTP_NOT_FOUND, HTTP_CONFLICT, HTTP_UNAVAILABLE = (
    200, 302, 400, 401, 403, 404, 409, 503)
HTTP_TOO_LARGE = 413
REFUSED_EXIT = 2
SETTING = "counterparty_sim.allowed"
SETTING_CP = "counterparty_sim.altruist_counterparty"
SIM_XWALK, REAL_XWALK = "ALTRUIST_SIMULATOR", "ALTRUIST"
SIM_LEDGER, REAL_LEDGER = "altruist_simulator", "altruist"
PROVIDER_KEY = "cpsim02verify"


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_T, ORG_T2 = _id(1), _id(2)
# ORG_R: NON-test and ROLLED BACK only — outside the reserved prefix (cpsim01 fix 1), asserted to match no real org
ORG_R = UUID("c5130000-0000-4000-8000-0000c9520003")
FIXTURE_ORGS = [ORG_T, ORG_T2]
U_SA, U_OA, U_CU, U_CU2 = _id(10), _id(11), _id(12), _id(13)
ALL_USERS = [U_SA, U_OA, U_CU, U_CU2]
E_T, E_T2 = _id(40), _id(41)
H_T, H_T2 = _id(30), _id(31)
ACC_T1, ACC_T_REAL, ACC_T2 = _id(50), _id(51), _id(52)
E_R_DEMO, E_R_DEMO2, E_R_PLAIN = _id(42), _id(43), _id(44)
H_R_DEMO, H_R_PLAIN = _id(32), _id(33)
ACC_R_DEMO, ACC_R_PLAIN = _id(53), _id(54)
SEC_A, SEC_B, SEC_D1, SEC_D2 = _id(60), _id(61), _id(62), _id(63)
HOUSEHOLDS = [H_T, H_T2]
ACCOUNTS = [ACC_T1, ACC_T_REAL, ACC_T2]
SECURITIES = [SEC_A, SEC_B, SEC_D1, SEC_D2]
USERS = {U_SA: (ORG_T, "super_admin", "sa", "super_admin"), U_OA: (ORG_T, "org_admin", "oa", "org_admin"),
         U_CU: (ORG_T, "member", "cu", "cpsim02_custody"), U_CU2: (ORG_T2, "member", "cu2", "cpsim02_custody")}
CUSTODY_PERMS = ("manage_custody_connections", "view_custody_connections")
ROLE_EXPECT = {U_SA: {"org": ORG_T, "roles": ["super_admin"]},
               U_OA: {"org": ORG_T, "roles": ["org_admin"], "has": ["manage_org_settings"],
                      "lacks": list(CUSTODY_PERMS)},
               U_CU: {"org": ORG_T, "roles": ["cpsim02_custody"], "has": list(CUSTODY_PERMS),
                      "lacks": ["manage_org_settings"]},
               U_CU2: {"org": ORG_T2, "roles": ["cpsim02_custody"], "has": list(CUSTODY_PERMS),
                       "lacks": ["manage_org_settings"]}}
# the book's external ids (text, compared as text)
A1, A2, A_SHARED, A4 = "cpv-acct-1", "cpv-acct-2", "cpv-acct-shared", "cpv-acct-4"
HH1, HH2, HH3 = "cpv-hh-1", "cpv-hh-2", "cpv-hh-3"
CUSIP_A, TICKER_A, ISIN_A = "CPSIM02A1", "CPSVA", "XXCPSIM02A10"
CUSIP_B, TICKER_B = "CPSIM02B1", "CPSVB"
TICKER_DUP = "CPSVDUP"
CUSIP_UNKNOWN = "CPSIM02Z9"
PRICE_THU = "101.25"                   # platform price on 2026-11-11 (on or before THU)
PRICE_MON = "102.00"
SEED_CHECK_LITERALS = (
    ("cpsim.book_uploads", "book_uploads_kind_chk", ("households", "accounts", "positions", "transactions")),
    ("cpsim.book_uploads", "book_uploads_format_chk", ("csv", "json")),
    ("cpsim.book_positions", "book_positions_row_type_chk", ("security", "cash")),
    ("cpsim.book_positions", "book_positions_price_source_chk", ("uploaded", "platform_price", "cash")),
    ("cpsim.book_transactions", "book_transactions_type_chk", ("buy", "interest")),
    ("cpsim.oauth_tokens", "oauth_tokens_kind_chk", ("access", "refresh")),
    ("cpsim.conformance_runs", "conformance_runs_target_chk", ("simulator", "sandbox")),
    ("cpsim.request_log", "request_log_direction_chk", ("in", "out")),
    ("cpsim.request_log", "request_log_auth_chk", ("ok", "token_revoked", "token_expired", "behaviour_reject",
                                                   "delivered", "unsigned", "not_simulator_key", "bad_grant")),
    ("portfolio.positions", "positions_source_chk", ("altruist", "altruist_simulator")),
    ("public.altruist_connections", "altruist_connections_environment_check", ("sandbox", "production", "simulator")),
    ("public.altruist_oauth_states", "altruist_oauth_states_environment_check", ("sandbox", "production",
                                                                                  "simulator")),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity",)),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "isin", "ticker")),
    ("portfolio.securities_global_prices", "sec_global_price_type_chk", ("close",)),
    ("portfolio.external_references", "ext_ref_record_type_chk", ("account",)),
    ("public.account_import_exceptions", "account_import_exceptions_record_kind_check", ("account",)),
)
UNIQUES = ("book_households_live_uq", "book_accounts_live_uq", "book_positions_live_uq", "book_transactions_live_uq",
           "oauth_codes_hash_uq", "oauth_tokens_hash_uq", "external_references_org_source_ext_type_key",
           "altruist_connections_active_unique", "altruist_webhook_events_event_id_key",
           "accounts_active_identity_uq", "uq_sec_global_ident_issuer_assigned", "uq_sec_global_price_point",
           "org_settings_org_id_setting_key_key")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "tags"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "accounts": ("id", "org_id", "account_number_masked", "account_number_hash", "custodian_code", "registration_type",
                 "tax_status", "primary_entity_id", "household_id"),
    "portfolio.external_references": ("org_id", "source_system", "external_id", "record_type", "record_id"),
    "portfolio.securities_global": ("id", "name", "security_type", "currency_code"),
    "portfolio.securities_global_identifiers": ("global_security_id", "id_type", "id_value", "is_primary"),
    "portfolio.securities_global_prices": ("global_security_id", "price_date", "price", "currency_code", "price_type",
                                           "source", "provider_key"),
    "altruist_connections": ("org_id", "environment", "status", "client_id", "client_secret_encrypted",
                             "client_secret_last4", "access_token_encrypted", "access_token_last4",
                             "refresh_token_encrypted", "refresh_token_last4", "scope", "token_expires_at"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category", "is_public"),
}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.entities", "public.households",
                  "public.household_memberships", "public.accounts", "public.org_settings", "public.audit_log",
                  "public.altruist_connections", "public.altruist_oauth_states", "public.altruist_webhook_events",
                  "public.account_import_exceptions", "public.account_import_batches",
                  "portfolio.external_references", "portfolio.positions", "portfolio.transactions",
                  "portfolio.securities_global", "portfolio.securities_global_identifiers",
                  "portfolio.securities_global_prices")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.counterparty_behaviour",
                "cpsim.counterparty_clock", "cpsim.request_log", "cpsim.book_uploads", "cpsim.book_households",
                "cpsim.book_accounts", "cpsim.book_positions", "cpsim.book_transactions", "cpsim.oauth_codes",
                "cpsim.oauth_tokens", "cpsim.conformance_runs")
NEW_CPSIM_TABLES = ("book_uploads", "book_households", "book_accounts", "book_positions", "book_transactions",
                    "oauth_codes", "oauth_tokens", "conformance_runs")

S: dict = {"cp": {}, "keys": {}, "client_ids": {}, "issued": [], "audits": [], "events": [], "health_ids": []}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
VERIFY_CALLS: list[str] = []
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


def _redact(text: str) -> str:
    """Never print a secret: any issued value (or its random body) and any cpsim_ token is masked."""
    t = str(text)
    for v in S["issued"]:
        if v:
            t = t.replace(v, "[secret]")
            body = v.split("_")[-1]
            if len(body) >= 16:
                t = t.replace(body, "[secret]")
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


def secret_leaks(text: str) -> list[str]:
    return [v[:10] + "…" for v in S["issued"] if v and (v in text or v.split("_")[-1] in text)]


def note_audit(action: str, rid) -> None:
    S["audits"].append((action, str(rid)))


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """One transaction in ONE org's RLS context (no Super Admin carve-out) — the isolation reads."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', $1, true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def sim(conn, sql, *args, fetch="fetch"):
    """One transaction AS cpsim_app (SET LOCAL ROLE — the simulator's own path)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', '', true)")
        await conn.execute("SET LOCAL ROLE cpsim_app")
        return await getattr(conn, fetch)(sql, *args)


class Rollback(Exception):
    pass


async def rolled_back(conn, fn, *, role: str | None = "cpsim_app", super_admin: bool = False, org=None):
    """Run ``fn(conn)`` in a transaction that is ALWAYS rolled back; returns (result, error)."""
    out = {"res": None, "err": None}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', $1, true), "
                               "set_config('app.current_org_id', $2, true)", "true" if super_admin else "false",
                               str(org) if org else "")
            if role:
                await conn.execute(f"SET LOCAL ROLE {role}")
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


REL_OID = ("(SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
           "WHERE n.nspname = split_part($1::text, '.', 1) AND c.relname = split_part($1::text, '.', 2))")


# ── fixture scopes / counts / fingerprints ──────────────────────────────────────────────────────────────────────
def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


def _tarr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals) + "]::text[]"


FIX_CP = f"(SELECT id FROM cpsim.counterparties WHERE slug LIKE '{SLUG_PREFIX}%')"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS + [ORG_R]), _arr(ALL_USERS)
    by_org = f"org_id = ANY({orgs})"
    secs = _arr(SECURITIES)
    events = _tarr(S["events"]) if S["events"] else "ARRAY[]::text[]"
    cps = _arr(S["cp"].values()) if S["cp"] else "ARRAY[]::uuid[]"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.household_memberships": f"household_id = ANY({_arr(HOUSEHOLDS)})",
        "public.audit_log": f"{by_org} OR user_id = ANY({users}) OR resource_id = ANY({orgs}) OR "
                            f"resource_id = ANY({cps})",
        "public.altruist_webhook_events": f"event_id = ANY({events}) OR {by_org}",
        "portfolio.securities_global": f"id = ANY({secs})",
        "portfolio.securities_global_identifiers": f"global_security_id = ANY({secs})",
        "portfolio.securities_global_prices": f"global_security_id = ANY({secs})",
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
    orgs = [str(o) for o in FIXTURE_ORGS + [ORG_R]]
    out = {
        "counterparty_sim.* settings of every real org": await fingerprint(
            conn, "SELECT org_id, setting_key, setting_value, updated_at FROM org_settings WHERE setting_key LIKE "
                  "'counterparty\\_sim.%' AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "demo-tagged entities": await fingerprint(
            conn, "SELECT id, display_name, tags, status FROM entities WHERE EXISTS (SELECT 1 FROM unnest(tags) x "
                  "WHERE x LIKE 'demo-%') AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real Altruist connections": await fingerprint(
            conn, "SELECT id, org_id, environment, status, system_to FROM altruist_connections WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real Altruist crosswalks": await fingerprint(
            conn, "SELECT id, org_id, source_system, external_id, last_seen FROM portfolio.external_references "
                  "WHERE source_system IN ('ALTRUIST', 'ALTRUIST_SIMULATOR') AND NOT (org_id = ANY($1::uuid[]))",
            orgs),
        "real webhook events": await fingerprint(
            conn, "SELECT id, event_id, org_id, processed_at FROM altruist_webhook_events WHERE event_id NOT LIKE "
                  "'cpsim-evt-%' AND event_id NOT LIKE $1", f"{TAG}%"),
        "real positions fed by altruist / altruist_simulator": await fingerprint(
            conn, "SELECT id, org_id, valid_to FROM portfolio.positions WHERE source_system IN ('altruist', "
                  "'altruist_simulator') AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "the security master's real CPSV/CPSIM02 identifiers (none)": await fingerprint(
            conn, "SELECT id FROM portfolio.securities_global_identifiers WHERE (id_value LIKE 'CPSIM02%' OR "
                  "id_value LIKE 'CPSV%' OR id_value LIKE 'XXCPSIM02%') AND NOT (global_security_id = "
                  "ANY($1::uuid[]))", [str(s) for s in SECURITIES]),
    }
    for label, sql in (("counterparties (non-fixture)", f"SELECT id, slug, status, system_to FROM cpsim.counterparties "
                                                        f"WHERE slug NOT LIKE '{SLUG_PREFIX}%'"),
                       ("books (non-fixture)", "SELECT id, counterparty_id, valid_to FROM cpsim.book_positions WHERE "
                                               f"counterparty_id NOT IN {FIX_CP}"),
                       ("upload logs (non-fixture)", "SELECT id FROM cpsim.book_uploads WHERE counterparty_id NOT IN "
                                                     f"{FIX_CP}"),
                       ("institution_types", "SELECT key, label, field_groups FROM cpsim.institution_types")):
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
            "public.altruist_webhook_events": ("event_id = ANY($1::text[])", [list(S["events"])]),
            # the security master has no org_id: the fixture securities by id (the closure reaches identifiers and
            # platform prices; every org-scoped row that points at them lives in a fixture org)
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [list(SECURITIES)]),
            # cpsim02 fix 1 (C): the platform identifiers and prices (org_id NULL) of the fixture securities are
            # seeded explicitly by fixture id (verify_marketdata1's pattern) — the closure refuses rows outside the
            # fixture orgs, and that refusal stays as strict as it is
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            "portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [list(SECURITIES)]),
            # the simulator's book has no org_id: by the reserved fixture slug prefix (asserted to match no real row);
            # the FK closure reaches credentials, behaviour, clock, request log, the book, the upload log, OAuth
            # codes / tokens and conformance runs
            "cpsim.counterparties": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
            "cpsim.request_log": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("cpsim.allow_teardown",), label="verify_cpsim02")
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
    c = await counts(conn)
    n = sum(c.values())
    op = await operator_conn()
    try:
        n += await VC.leftover_count(op, orgs=FIXTURE_ORGS + [ORG_R], users=ALL_USERS, user_sub_like=f"%{TAG}%")
    finally:
        await close_conn(op)
    return n


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
APP_T: httpx.ASGITransport | None = None


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def raw(method, path, *, headers=None, body=None, content=None, files=None, data=None, timeout=300.0):
    """(status, parsed body, headers) through the REAL app (which mounts the simulator)."""
    kw = {"headers": {"User-Agent": "verify_cpsim02", **(headers or {})}}
    if body is not None:
        kw["json"] = body
    if content is not None:
        kw["content"] = content
    if files is not None:
        kw["files"] = files
    if data is not None:
        kw["data"] = data
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}, {}
        except Exception as exc:  # noqa: BLE001
            print(f"[INFO] {method} {path} raised in the app (a 500):")
            traceback.print_exc()
            return 500, {"raw": f"{type(exc).__name__}"}, {}
    ctype = res.headers.get("content-type", "")
    if "json" in ctype:
        try:
            data_out = res.json() if res.content else {}
        except ValueError:
            data_out = {"raw": res.text[:4000]}
    else:
        data_out = {"text": res.text}
    return res.status_code, data_out, {k.lower(): v for k, v in res.headers.items()}


async def api(uid, method, path, body=None, **kw):
    code, data, _h = await raw(method, path, headers={"Authorization": f"Bearer {token_for(uid)}"}, body=body, **kw)
    return code, data


def cpu(key: str, tail: str = "") -> str:
    return f"{_P}/{S['cp'][key]}{tail}"


def book_file(rows, fmt="json", name=None) -> tuple:
    if fmt == "json":
        payload = json.dumps({"rows": rows}).encode()
        return (name or "book.json", payload, "application/json")
    cols = sorted({k for r in rows for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return (name or "book.csv", buf.getvalue().encode(), "text/csv")


async def upload(uid, key, kind, rows, *, step="accept", fmt="json", name=None, raw_file=None):
    f = raw_file or book_file(rows, fmt, name)
    code, out = await api(uid, "POST", cpu(key, f"/book/{step}"), files={"file": f}, data={"kind": kind})
    if code == HTTP_OK and uid == U_SA:
        note_audit("counterparty.book_accepted" if step == "accept" else "counterparty.book_previewed", S["cp"][key])
        for d in (out.get("webhooks") or []):
            if d.get("event_id"):
                S["events"].append(d["event_id"])
    return code, out


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")

    def stub(tok):
        VERIFY_CALLS.append(tok)
        return dict(CLAIMS_BY_TOKEN[tok])

    main.verify_token = stub
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise httpx.ConnectError(f"verify_cpsim02: network disabled ({request.url.host})", request=request)

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cpsim02: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for fn, nm in ((R.set_today, "registry.set_today"), (SM.set_now, "mirror.set_now"), (SO.set_now, "oauth.set_now"),
                   (AO.set_simulator_transport, "altruist_oauth.set_simulator_transport"),
                   (SW.set_transport, "webhooks.set_transport")):
        have = list(inspect.signature(fn).parameters)
        if len(have) != 1:
            raise RuntimeError(f"{nm} signature drifted: {have}")
    S["today"] = FRIDAY
    S["now"] = FIXED_NOW
    R.set_today(lambda: S["today"])
    SM.set_now(lambda: S["now"])
    SO.set_now(lambda: S["now"])
    global APP_T
    APP_T = FX.TimeoutASGITransport(app=main.app)
    AO.set_simulator_transport(APP_T)            # simulator environment ONLY (sandbox / production never read it)
    SW.set_transport(httpx.ASGITransport(app=main.app))
    SW.set_sleep(lambda s: asyncio.sleep(0))     # webhook retry backoff: no real wait
    # in-process configuration (never printed, never written anywhere)
    os.environ[AO.CPSIM_BASE_URL_ENV] = SIM_BASE
    os.environ["ALTRUIST_REDIRECT_URI"] = REDIRECT
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", "cpsim02verify-" + secrets.token_hex(16))
    S["whsec"] = "cpsim_whsec_" + secrets.token_hex(20)
    S["real_whsec"] = "verify-real-" + secrets.token_hex(20)
    S["issued"] += [S["whsec"], S["real_whsec"]]
    os.environ[AW.SIMULATOR_SECRET_ENV_VAR] = S["whsec"]
    os.environ[AW.SECRET_ENV_VAR] = S["real_whsec"]
    os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_ID", f"{TAG}-sandbox-id")
    os.environ.setdefault("ALTRUIST_SANDBOX_CLIENT_SECRET", f"{TAG}-sandbox-secret")
    os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_ID", f"{TAG}-production-id")
    os.environ.setdefault("ALTRUIST_PRODUCTION_CLIENT_SECRET", f"{TAG}-production-secret")


def sim_hosts(key="main") -> dict:
    return AO.simulator_hosts(SLUG_PREFIX + key)


def set_sim_creds(key="main") -> None:
    os.environ["ALTRUIST_SIMULATOR_CLIENT_ID"] = S["client_ids"][key]
    os.environ["ALTRUIST_SIMULATOR_CLIENT_SECRET"] = S["keys"][key]


async def raw_sim(method, url, **kw):
    """A raw request to the simulator through the same in-process transport the client uses."""
    async with httpx.AsyncClient(transport=APP_T, timeout=30.0, follow_redirects=False) as c:
        return await c.request(method, url, **kw)


async def authorize_code(key="main", *, state=None) -> tuple[str, str]:
    state = state or secrets.token_urlsafe(32)
    url = AO.build_authorize_url(environment=AO.SIMULATOR, state=state, hosts=sim_hosts(key))
    res = await raw_sim("GET", url)
    q = parse_qs(urlsplit(res.headers.get("location") or "").query)
    return (q.get("code") or [""])[0], (q.get("state") or [""])[0]


async def token_counts(conn, key="main") -> tuple[int, int]:
    cid = S["cp"].get(key)
    t = int(await sim(conn, "SELECT count(*) FROM cpsim.oauth_tokens WHERE counterparty_id = $1::uuid", cid,
                      fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.oauth_codes WHERE counterparty_id = $1::uuid", cid,
                      fetch="fetchval"))
    return t, c


# ═══ The canonical fixture book (counterparty "main") ═══════════════════════════════════════════════════════════
def main_book() -> dict:
    return {
        "households": [{"household_id": HH1, "name": "Verify household one"},
                       {"household_id": HH2, "name": "Verify household two"},
                       {"household_id": HH3, "name": "Verify household three"}],
        "accounts": [{"account_id": A1, "household_id": HH1, "name": "Verify brokerage one"},
                     {"account_id": A2, "household_id": HH1, "name": "Verify brokerage two (unlinked)"},
                     {"account_id": A_SHARED, "household_id": HH2, "name": "Verify shared id"},
                     {"account_id": A4, "household_id": HH3, "name": "Verify org two account"}],
        "positions": [
            {"position_id": "cpv-p-a1-thu", "account_id": A1, "as_of": THU.isoformat(), "cusip": CUSIP_A,
             "quantity": "10", "currency_code": "USD"},
            {"position_id": "cpv-p-a1-cash-thu", "account_id": A1, "as_of": THU.isoformat(), "row_type": "cash",
             "name": "Cash", "market_value": "500.00", "currency_code": "USD"},
            {"position_id": "cpv-p-a1-mon", "account_id": A1, "as_of": MON.isoformat(), "cusip": CUSIP_A,
             "quantity": "12", "currency_code": "USD"},
            {"position_id": "cpv-p-a1-cash-mon", "account_id": A1, "as_of": MON.isoformat(), "row_type": "cash",
             "name": "Cash", "market_value": "400.00", "currency_code": "USD"},
            {"position_id": "cpv-p-a2", "account_id": A2, "as_of": THU.isoformat(), "ticker": TICKER_A,
             "quantity": "1", "market_value": "101.25"},
            {"position_id": "cpv-p-shared", "account_id": A_SHARED, "as_of": THU.isoformat(), "isin": ISIN_A,
             "quantity": "2", "market_value": "202.50"},
            {"position_id": "cpv-p-a4", "account_id": A4, "as_of": THU.isoformat(), "cusip": CUSIP_A,
             "quantity": "3", "market_value": "300.00"}],
        "transactions": [
            {"transaction_id": "cpv-t-a1-buy-thu", "account_id": A1, "type": "buy", "trade_date": THU.isoformat(),
             "settle_date": MON.isoformat(), "cusip": CUSIP_A, "quantity": "10", "price": "100",
             "gross_amount": "1000", "net_amount": "1000"},
            {"transaction_id": "cpv-t-a1-int-thu", "account_id": A1, "type": "interest",
             "trade_date": THU.isoformat(), "row_type": "cash", "name": "Cash", "gross_amount": "1.25",
             "net_amount": "1.25"},
            {"transaction_id": "cpv-t-a1-buy-mon", "account_id": A1, "type": "buy", "trade_date": MON.isoformat(),
             "cusip": CUSIP_A, "quantity": "2", "price": "102", "gross_amount": "204", "net_amount": "204"},
            {"transaction_id": "cpv-t-a4-buy", "account_id": A4, "type": "buy", "trade_date": THU.isoformat(),
             "cusip": CUSIP_A, "quantity": "3", "price": "100", "gross_amount": "300", "net_amount": "300"}],
    }


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Discovery + CPSIM02_NOTES (D1–D11 as built; differences recorded)")
    n = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for w in ("ALTRUIST_HOSTS", "client_credentials", "SOURCE_SYSTEM_ALTRUIST", "positions_source_chk",
              "altruist_oauth_states_environment_check", "manage_custody_connections", "consume_oauth_state",
              "_to_decimal", "parse_text", "menuvisibility_harness", "adminArea", "anyOf"):
        check(w in n, f"CPSIM02_NOTES.md names {w!r}")
    for w in ("Differences from the prompt", "Decisions as built", "Reuse inventory"):
        check(w in n, f"CPSIM02_NOTES.md has its {w!r} section")
    for s_ in ("sprint1", "sprint2", "sprint3", "sprint4", "sprint5", "sprint6"):
        check(s_ in n, f"CPSIM02_NOTES records the offline status of verify_altruist_{s_}*")
    self_static(check, find)


async def seed_rows_vs_checks(conn, rows_by_table) -> tuple[int, list]:
    n, refused = 0, []
    for table, rows in rows_by_table.items():
        defs = await platform(conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE contype = "
                                    "'c' AND conrelid = " + REL_OID + " ORDER BY conname", table)
        for d in defs:
            m = re.fullmatch(r"CHECK \((.*)\)( NOT VALID)?", d["d"], re.S)
            if not m:
                refused.append((table, d["conname"], "unparsed"))
                continue
            n += 1
            for i, row in enumerate(rows):
                runner = sim if table.startswith("cpsim.") else platform
                ok = await runner(conn, f"SELECT ({m.group(1)}) IS NOT FALSE FROM jsonb_populate_record(NULL::{table}, "
                                        "$1::jsonb) AS t", json.dumps(row), fetch="fetchval")
                if ok is not True:
                    refused.append((table, d["conname"], i))
    return n, refused


async def y_schema(conn):
    section("[Y] Schema: book + upload log + OAuth + conformance under cpsim (cpsim_app only); source / environment "
            "CHECKs extended by the simulator token only; the org-setting guard covers the counterparty key")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{by}")
    tabs = {r["t"] for r in await platform(conn, "SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON "
                                                 "n.oid = c.relnamespace WHERE n.nspname = 'cpsim' AND c.relkind = 'r'")}
    check(set(NEW_CPSIM_TABLES) <= tabs, f"the {len(NEW_CPSIM_TABLES)} cpsim02 tables exist under schema cpsim",
          f"missing={sorted(set(NEW_CPSIM_TABLES) - tabs)}")
    privs = {(r["t"], r["p"]) for r in await platform(conn, """
        SELECT n.nspname || '.' || c.relname AS t, a.privilege_type AS p
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        WHERE a.grantee = (SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app') AND c.relkind IN ('r', 'v', 'm', 'p',
        'S')""")}
    # cpsim03 fix 1: the documented narrower grants (the upload log; cpsim03's append-only executions and never-deleted
    # orders) come from ONE list both verifies share — still the EXACT set, nothing looser
    expect = {(f"cpsim.{t}", p) for t in tabs for p in FX.cpsim_app_privileges(t)}
    expect |= {(f"portfolio.{t}", "SELECT") for t in ("securities_global", "securities_global_identifiers",
                                                       "securities_global_prices")}
    check(bool(privs) and privs == expect,
          "cpsim_app's relation privileges are EXACTLY: S/I/U/D on every cpsim table except the documented "
          f"narrower grants {sorted((t, sorted(w)) for t, w in FX.CPSIM_APP_WITHHELD.items())} (_cpsim_fixtures), SELECT on the three security-master / price tables — nothing new in public",
          f"extra={sorted(privs - expect)[:5]} missing={sorted(expect - privs)[:5]}")
    others = {(r["t"], r["g"]) for r in await platform(conn, """
        SELECT n.nspname || '.' || c.relname AS t, COALESCE(g.rolname, 'PUBLIC') AS g
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        LEFT JOIN pg_roles g ON g.oid = a.grantee
        WHERE n.nspname = 'cpsim' AND c.relkind = 'r' AND a.grantee <> c.relowner
          AND a.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app')""")}
    check(not others, "no role other than cpsim_app (and the owner) holds a privilege on any cpsim table",
          f"{sorted(others)[:6]}")
    app_usage = await conn.fetchval("SELECT has_schema_privilege(current_user, 'cpsim', 'USAGE')")
    _r, err = await rolled_back(conn, lambda c: c.fetchval("SELECT count(*) FROM cpsim.book_positions"), role=None,
                                super_admin=True)
    check(app_usage is False and isinstance(err, asyncpg.InsufficientPrivilegeError),
          "the app's normal role still cannot read cpsim.* (the book included) — even with the Super Admin GUC",
          f"usage={app_usage} {type(err).__name__ if err else None}")
    w = await platform(conn, "SELECT p.prosecdef, has_function_privilege(current_user, p.oid, 'EXECUTE') AS ex, "
                             "has_function_privilege('cpsim_app', p.oid, 'EXECUTE') AS sim_ex, prosrc FROM pg_proc p "
                             "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND "
                             "p.proname = 'counterparty_sim_may_connect'", fetch="fetchrow")
    check(bool(w and w["prosecdef"] and w["ex"] and "cpsim.may_connect" in w["prosrc"]),
          "public.counterparty_sim_may_connect: a SECURITY DEFINER wrapper over cpsim.may_connect (the rule exists "
          "once), executable by the app role", f"{dict(w) if w else None}")
    g = await platform(conn, "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE "
                             "n.nspname = 'public' AND p.proname = 'cpsim_org_setting_guard'", fetch="fetchval")
    check(bool(g and SETTING_CP in g and SETTING in g), "the org-setting guard covers both counterparty_sim keys")
    got, bad = 0, []
    for table, name, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = " + REL_OID +
                                 " AND conname = $2", table, name, fetch="fetchval")
        if d is None:
            bad.append((table, name, "absent"))
            continue
        got += 1
        miss = [x for x in lits if f"'{x}'" not in d]
        if miss:
            bad.append((table, name, miss))
    check(bool(got == len(SEED_CHECK_LITERALS) and got > 0 and not bad),
          f"{got} CHECK constraints read (cpsim by namespace + relname, never ::regclass); every literal the run "
          "writes into a CHECKed column is one it allows", f"{bad}")
    src_chk = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = " + REL_OID +
                                   " AND conname = 'positions_source_chk'", "portfolio.positions", fetch="fetchval")
    toks = set(re.findall(r"'([a-z_]+)'::text", src_chk or ""))
    check(toks == set(PA.SOURCE_SYSTEMS) and "altruist_simulator" in toks,
          "positions_source_chk admits exactly SOURCE_SYSTEMS (the Python vocabulary) — extended by "
          "'altruist_simulator' only", f"{sorted(toks ^ set(PA.SOURCE_SYSTEMS))}")
    envs = set(re.findall(r"'([a-z_]+)'::text", await platform(
        conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = " + REL_OID + " AND conname = $2",
        "public.altruist_connections", "altruist_connections_environment_check", fetch="fetchval") or ""))
    check(envs == set(AO.ENVIRONMENTS) == {"sandbox", "production", "simulator"},
          "the connection environment CHECK equals the client's ENVIRONMENTS (sandbox, production, simulator)",
          f"{envs}")
    xr = await platform(conn, "SELECT count(*) FROM pg_constraint WHERE conrelid = " + REL_OID + " AND contype = 'c' "
                              "AND pg_get_constraintdef(oid) LIKE '%source_system%'", "portfolio.external_references",
                        fetch="fetchval")
    check(int(xr) == 0, "external_references carries NO source_system CHECK (the crosswalk token needed no DDL)",
          f"{xr}")
    rows = {"cpsim.book_positions": [{"counterparty_id": str(_id(90)), "account_external_id": A1,
                                      "external_id": "cpv-x", "as_of": THU.isoformat(), "row_type": "security",
                                      "security_id": str(SEC_A), "name": "x", "quantity": "1", "market_value": "1",
                                      "currency_code": "USD", "price_source": "platform_price"}],
            "cpsim.book_uploads": [{"counterparty_id": str(_id(90)), "file_name": "f.json", "sha256": "a" * 64,
                                    "kind": "positions", "format": "json", "mode": "replace", "rows_accepted": 1,
                                    "rows_rejected": 0, "rejections": []}],
            "public.org_settings": [{"org_id": str(ORG_T), "setting_key": SETTING_CP,
                                     "setting_value": SLUG_PREFIX + "main", "category": "counterparty_sim",
                                     "is_public": False}]}
    n_chk, refused = await seed_rows_vs_checks(conn, rows)
    check(bool(n_chk > 0 and not refused), f"{n_chk} CHECKs evaluated by the database against the valid twin rows",
          f"{refused}")
    _n, bad2 = await seed_rows_vs_checks(conn, {"cpsim.book_positions": [dict(rows["cpsim.book_positions"][0],
                                                                              row_type="cash")]})
    check(("cpsim.book_positions", "book_positions_security_chk", 0) in set(bad2),
          "the pre-check is not vacuous: a cash row naming a security is refused by the same evaluation", f"{bad2}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    cons = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype IN ('u', 'p') "
                                                       "AND conname = ANY($1::text[])", list(UNIQUES))}
    have = set(idx) | cons
    check(bool(have == set(UNIQUES) and all("UNIQUE" in v for v in idx.values())),
          f"{len(have)} unique indexes / keys read (one live book row per id, one live connection per org + "
          "environment, one crosswalk per key, event_id dedup, …) — the run respects each",
          f"missing={sorted(set(UNIQUES) - have)}")
    fks = await platform(conn, "SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace "
                               "WHERE k.contype = 'f' AND n.nspname = 'cpsim'", fetch="fetchval")
    check(int(fks) >= 15, "the cpsim foreign keys are read (the book → counterparties / uploads, OAuth → credentials "
          "/ codes)", f"{fks}")
    trg = await platform(conn, "SELECT tgname FROM pg_trigger t WHERE t.tgrelid = " + REL_OID + " AND NOT "
                               "tgisinternal", "cpsim.book_uploads")
    check([r["tgname"] for r in trg] == ["book_uploads_append_only_trg"], "the upload log carries its append-only "
          "trigger", f"{[r['tgname'] for r in trg]}")
    snap = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    badc = []
    for t, cs in list(SEED_COLUMNS.items()) + [(f"cpsim.{t}", ("id",)) for t in NEW_CPSIM_TABLES]:
        mm = re.search(rf"^-- ===== {re.escape(t)} =====\n((?:--.*\n)+)", snap, re.M)
        have_c = set(re.findall(r"^--\s{3}(\w+)\s", mm.group(1), re.M)) if mm else set()
        miss = [c for c in cs if c not in have_c]
        if not mm or miss:
            badc.append((t, "absent" if not mm else miss))
    check(bool(SEED_COLUMNS) and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables) exist in "
          "docs/schema_snapshot.sql, and the snapshot carries the new cpsim tables", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and not refused and have == set(UNIQUES)
                          and by is False and not badc)


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1 OR id = $2::uuid",
                           f"{FIXTURE_ID_PREFIX}%", ORG_R, fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    s_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE id::text LIKE $1 OR name "
                                  "LIKE $2", f"{FIXTURE_ID_PREFIX}%", NAME + "%", fetch="fetchval"))
    i_ = int(await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value = "
                                  "ANY($1::text[])", [CUSIP_A, TICKER_A, ISIN_A, CUSIP_B, TICKER_B, TICKER_DUP,
                                                      CUSIP_UNKNOWN], fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1", SLUG_PREFIX + "%",
                      fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM altruist_webhook_events WHERE event_id LIKE $1", f"{TAG}%",
                           fetch="fetchval"))
    return check(o == 0 and u == 0 and s_ == 0 and i_ == 0 and c == 0 and e == 0,
                 "the reserved prefixes (fixture ids, the out-of-prefix rolled-back org, sign-in tag, 'CPSIM02 VERIFY' "
                 "securities and their identifiers, the 'cpsim2verify-' slug, the event-id tag) match no real row",
                 f"orgs={o} users={u} securities={s_} identifiers={i_} counterparties={c} events={e}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_T, "t"), (ORG_T2, "t2")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CPSIM02 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set(CUSTODY_PERMS) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in (ORG_T, ORG_T2):
            rid = await rbac.ensure_role(conn, org, "cpsim02_custody", "CPSIM02 verify — custody connections")
            for pn in CUSTODY_PERMS:
                await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                   "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CPSIM02 {short.upper()}", sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await rbac.grant_named_role(conn, uid, org, rname)
        for eid, org, nm in ((E_T, ORG_T, "T family"), (E_T2, ORG_T2, "T2 family")):
            await conn.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type) VALUES "
                               "($1, $2, $3, 'active', 'individual'::entity_type)", eid, org, NAME + nm)
        for hh, org, eid in ((H_T, ORG_T, E_T), (H_T2, ORG_T2, E_T2)):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + "household")
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, eid)
        for acc, org, eid, hh, n in ((ACC_T1, ORG_T, E_T, H_T, 1), (ACC_T_REAL, ORG_T, E_T, H_T, 2),
                                     (ACC_T2, ORG_T2, E_T2, H_T2, 3)):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id) VALUES ($1, $2, $3, $4, 'ALTRUIST', "
                "'individual', 'taxable', $5, $6)", acc, org, f"****{n:04d}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), eid, hh)
        for org, src, ext, acc in ((ORG_T, SIM_XWALK, A1, ACC_T1), (ORG_T, REAL_XWALK, A_SHARED, ACC_T_REAL),
                                   (ORG_T2, SIM_XWALK, A4, ACC_T2)):
            await conn.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                               "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", org, src, ext, acc)
        for sid, nm in ((SEC_A, "A"), (SEC_B, "B"), (SEC_D1, "D1"), (SEC_D2, "D2")):
            await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, currency_code) "
                               "VALUES ($1, $2, 'equity', 'USD')", sid, NAME + nm)
        for sid, typ, val, prim in ((SEC_A, "cusip", CUSIP_A, True), (SEC_A, "ticker", TICKER_A, False),
                                    (SEC_A, "isin", ISIN_A, False), (SEC_B, "cusip", CUSIP_B, True),
                                    (SEC_B, "ticker", TICKER_B, False), (SEC_D1, "ticker", TICKER_DUP, True),
                                    (SEC_D2, "ticker", TICKER_DUP, True)):
            await conn.execute("INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, "
                               "id_value, is_primary) VALUES ($1, $2, $3, $4)", sid, typ, val, prim)
        for d, px in ((date(2026, 11, 11), PRICE_THU), (MON, PRICE_MON)):
            await conn.execute("INSERT INTO portfolio.securities_global_prices (global_security_id, price_date, price, "
                               "currency_code, price_type, source, provider_key) VALUES ($1, $2, $3::numeric, 'USD', "
                               "'close', 'cpsim02 verify', $4)", SEC_A, d, px, PROVIDER_KEY)
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS),
                    "fixture orgs T, T2 and their users are is_test = true (privileged insert path; the reserved "
                    "prefix)", f"{len(os_)} orgs {len(us)} users")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT,
                                             "(a Super Admin, an org admin WITHOUT custody perms, two custody managers)")
    # the counterparties: through the admin API (audited), key shown once — kept in memory only
    ok_cp = True
    for key in ("main", "book", "other"):
        code, b = await api(U_SA, "POST", _P, {"name": NAME + key, "institution_type": "altruist",
                                               "slug": SLUG_PREFIX + key})
        issued = (b or {}).get("issued") or {}
        if code != HTTP_OK or not issued.get("api_key", "").startswith("cpsim_live_"):
            ok_cp = False
            continue
        S["cp"][key] = b["id"]
        S["keys"][key] = issued["api_key"]
        S["issued"].append(issued["api_key"])
        note_audit("counterparty.created", b["id"])
        code2, _ = await api(U_SA, "POST", cpu(key, "/activate"))
        note_audit("counterparty.activated", b["id"]) if code2 == HTTP_OK else None
        ok_cp &= code2 == HTTP_OK
        S["client_ids"][key] = await sim(conn, "SELECT id::text FROM cpsim.counterparty_credentials WHERE "
                                               "counterparty_id = $1::uuid AND kind = 'api_key' AND rotated_at IS NULL "
                                               "AND revoked_at IS NULL", b["id"], fetch="fetchval")
    clk = await sim(conn, "SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = $1::uuid AND "
                          "valid_to IS NULL", S["cp"].get("main"), fetch="fetchval") if S["cp"].get("main") else None
    check(ok_cp and len(S["cp"]) == 3 and clk == FRIDAY, "three fixture Altruist-compatible counterparties created "
          "and activated through the admin API; the main one's clock starts Friday 2026-11-13", f"{clk}")
    ok_book = False
    if S["cp"].get("main"):
        res = {}
        for kind in SB.KINDS:
            code, out = await upload(U_SA, "main", kind, main_book()[kind])
            res[kind] = (code, (out.get("counts") or {}))
        ok_book = all(c == HTTP_OK and r.get("rejected") == 0 and r.get("accepted") == len(main_book()[k])
                      for k, (c, r) in res.items())
        check(ok_book, "the canonical fixture book is uploaded through the admin API (preview-free accept): every "
              "row accepted", f"{res}")
    S["seeded"] = bool(ok_test and ok_roles and ok_cp and ok_book)


# ── read API ────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_read_api(conn):
    section("[Y] Read API: OAuth authorize / token / refresh and the 4 resources at the client's exact paths, through "
            "the EXISTING client functions; every bad input refused, nothing persisted")
    set_sim_creds("main")
    hosts = sim_hosts("main")
    t0, c0 = await token_counts(conn)
    code, state = await authorize_code()
    check(code.startswith("cpsim_code_") and len(state) > 20, "authorize (build_authorize_url → GET): 302 with a "
          "cpsim_code_ code and the state echoed")
    if code:
        S["issued"].append(code)
    tok = await AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts)
    S["issued"] += [tok.access_token, tok.refresh_token]
    check(tok.access_token.startswith("cpsim_at_") and tok.refresh_token.startswith("cpsim_rt_") and
          tok.expires_in == int(SO.ACCESS_TTL.total_seconds()) and tok.scope == "accounts:read",
          "token (the client's exchange_code_for_tokens): cpsim-prefixed access + refresh tokens, expires_in, scope")
    stored = await sim(conn, "SELECT kind, token_hash, last4 FROM cpsim.oauth_tokens WHERE counterparty_id = $1::uuid",
                       S["cp"]["main"])
    hashes = {r["token_hash"] for r in stored}
    check(SO.digest(tok.access_token) in hashes and not any(r["token_hash"] == tok.access_token for r in stored)
          and all(len(r["last4"]) == 4 for r in stored), "tokens are stored as sha256 (+ last four) only")
    api_base = hosts["api_base"]
    hh = await AO.fetch_resource(api_base=api_base, resource="households", access_token=tok.access_token, transport=APP_T)
    check(sorted(h["id"] for h in hh) == sorted([HH1, HH2, HH3]), "households (fetch_resource — the path every "
          "call_* uses): the book's three households", f"{[h['id'] for h in hh]}")
    ac = await AO.fetch_resource(api_base=api_base, resource="accounts", access_token=tok.access_token, param=HH1, transport=APP_T)
    check(sorted(a["id"] for a in ac) == sorted([A1, A2]), "accounts?household_id=: filtered by household",
          f"{[a['id'] for a in ac]}")
    ps = await AO.fetch_resource(api_base=api_base, resource="positions", access_token=tok.access_token, param=A1, transport=APP_T)
    by_id = {p["id"]: p for p in ps}
    sec, cash = by_id.get("cpv-p-a1-thu") or {}, by_id.get("cpv-p-a1-cash-thu") or {}
    exp_mv = (10 * Decimal(PRICE_THU)).quantize(Decimal("0.01"))
    check(set(by_id) == {"cpv-p-a1-thu", "cpv-p-a1-cash-thu"} and sec.get("cusip") == CUSIP_A and
          sec.get("quantity") == "10" and Decimal(str(sec.get("market_value") or "0")) == exp_mv and
          "quantity" in cash and cash.get("quantity") is None,
          "positions?account_id=: the Thursday snapshot on Friday's clock; a blank market value = quantity × the "
          "last PLATFORM price on or before the as-of date; cash carries no quantity", f"{dumps(ps)[:400]}")
    tx = await AO.fetch_resource(api_base=api_base, resource="transactions", access_token=tok.access_token, param=A1, transport=APP_T)
    check(sorted(t["id"] for t in tx) == ["cpv-t-a1-buy-thu", "cpv-t-a1-int-thu"] and
          all(t["type"] in APS.TRANSACTION_TYPE_MAP for t in tx), "transactions?account_id=: trade_date ≤ the clock; "
          "every type one the client maps", f"{[t['id'] for t in tx]}")
    r = await raw_sim("GET", f"{api_base}/v2/positions", params={"account_id": A1},
                      headers={"Authorization": f"Bearer {tok.access_token}", "Accept": "application/json"})
    body = r.json()
    check(r.headers.get("x-counterparty-simulated") == "true" and isinstance(body.get("positions"), list) and
          all(set(ACF.POSITION_FIELDS) <= set(x) for x in body["positions"]) and not any(
              k in body for k in ACF.PAGINATION_KEYS), "the raw response: SIMULATED header, the primary key "
          "'positions', every T6 primary field, one response (no pagination keys)", f"{dumps(body)[:300]}")
    fresh = await AO.refresh_access_token(environment=AO.SIMULATOR, refresh_token=tok.refresh_token, hosts=hosts)
    S["issued"] += [fresh.access_token, fresh.refresh_token]
    check(fresh.refresh_token != tok.refresh_token and fresh.access_token != tok.access_token,
          "refresh (the client's refresh_access_token): BOTH tokens rotate")
    t1, c1 = await token_counts(conn)
    refused = {}
    for label, coro in (
            ("used code", AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts)),
            ("spent refresh token", AO.refresh_access_token(environment=AO.SIMULATOR, refresh_token=tok.refresh_token,
                                                            hosts=hosts)),
            ("wrong secret", AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts,
                                                         credentials=(S["client_ids"]["main"],
                                                                      S["keys"]["main"] + "0")))):
        try:
            await coro
            refused[label] = "accepted"
        except AO.AltruistOAuthError:
            refused[label] = "refused"
    t2, c2 = await token_counts(conn)
    check(all(v == "refused" for v in refused.values()) and (t2, c2) == (t1, c1),
          "a used code, a spent refresh token and a wrong secret are each refused (≥ 400) and NOTHING is persisted",
          f"{refused} tokens {t1}→{t2} codes {c1}→{c2}")
    try:
        await AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts,
                                          credentials=(S["client_ids"]["main"], "sk_live_not_a_simulator_key"))
        client_side = "sent"
    except AO.AltruistConfigError:
        client_side = "refused before sending"
    rr = await raw_sim("POST", f"{hosts['login_base']}/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": S["client_ids"]["main"], "client_secret": "sk_live_not_a_simulator_key"})
    t3, c3 = await token_counts(conn)
    check(client_side == "refused before sending" and rr.status_code == HTTP_UNAUTH and (t3, c3) == (t1, c1),
          "a NON-cpsim secret: the client refuses to send it; sent raw, the simulator answers 401; nothing persisted",
          f"{client_side} {rr.status_code}")
    old = await raw_sim("GET", f"{api_base}/api/v2/households", headers={"Authorization": f"Bearer {tok.access_token}"})
    S["now"] = FIXED_NOW + timedelta(hours=2)
    try:
        expired = await raw_sim("GET", f"{api_base}/api/v2/households",
                                headers={"Authorization": f"Bearer {fresh.access_token}"})
    finally:
        S["now"] = FIXED_NOW
    key_as_token = await raw_sim("GET", f"{api_base}/api/v2/households",
                                 headers={"Authorization": f"Bearer {S['keys']['main']}"})
    outcomes = [r_["auth_outcome"] for r_ in await sim(
        conn, "SELECT auth_outcome FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND path LIKE "
              "'%/api/v2/households' ORDER BY at DESC LIMIT 3", S["cp"]["main"])]
    check(old.status_code == HTTP_UNAUTH and expired.status_code == HTTP_UNAUTH and
          key_as_token.status_code == HTTP_UNAUTH and {"token_revoked", "token_expired", "token_unknown"} <=
          set(outcomes), "the access token a refresh replaced (revoked), an expired one, and the cpsim_ KEY itself "
          "(it authenticates only the token endpoint) are each refused 401 — each logged with its reason",
          f"{old.status_code}/{expired.status_code}/{key_as_token.status_code} {outcomes}")
    bad_client = await raw_sim("GET", f"{hosts['login_base']}/oauth/authorize", params={
        "response_type": "code", "client_id": "00000000-0000-0000-0000-000000000000", "redirect_uri": REDIRECT,
        "state": "s", "scope": "accounts:read"})
    bad_redirect = await raw_sim("GET", f"{hosts['login_base']}/oauth/authorize", params={
        "response_type": "code", "client_id": S["client_ids"]["main"], "redirect_uri": "http://not-https.invalid/cb",
        "state": "s", "scope": "accounts:read"})
    missing = await raw_sim("GET", f"{hosts['login_base']}/oauth/authorize", params={
        "response_type": "code", "client_id": S["client_ids"]["main"], "redirect_uri": REDIRECT})
    t4, c4 = await token_counts(conn)
    check(bad_client.status_code == HTTP_BAD and bad_redirect.status_code == HTTP_BAD and
          missing.status_code == HTTP_BAD and "location" not in bad_client.headers and (t4, c4) == (t1, c1),
          "authorize: an unknown client_id, a non-https redirect_uri and a missing parameter → 400, no redirect, no "
          "code stored", f"{bad_client.status_code}/{bad_redirect.status_code}/{missing.status_code} codes {c1}→{c4}")
    leaks = secret_leaks(dumps([dict(x) for x in await sim(
        conn, "SELECT path, message_type FROM cpsim.request_log WHERE counterparty_id = $1::uuid", S["cp"]["main"])]))
    check(not leaks, "the request log holds no key, code or token")
    S["tokens"] = fresh
    S["read_ok"] = True


async def y_behaviour(conn):
    section("[Y] Behaviour on reads: reject → the client raises; delay → the client's timeout fires")
    set_sim_creds("main")     # cpsim02 fix 1: self-contained for an --only one-off (was set only by read_api)
    hosts = sim_hosts("main")
    code, state = await authorize_code()
    tok = await AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts)
    S["issued"] += [code, tok.access_token, tok.refresh_token]
    c1, b1 = await api(U_SA, "PUT", cpu("main", "/behaviour"), {"default_outcome": "reject",
                                                                "reject_reason": "simulated maintenance window"})
    note_audit("counterparty.behaviour_updated", S["cp"]["main"]) if c1 == HTTP_OK else None
    err = None
    try:
        await AO.fetch_resource(api_base=hosts["api_base"], resource="households", access_token=tok.access_token, transport=APP_T)
    except AO.AltruistOAuthError as exc:
        err = str(exc)
    raw_r = await raw_sim("GET", f"{hosts['api_base']}/api/v2/households",
                          headers={"Authorization": f"Bearer {tok.access_token}"})
    check(c1 == HTTP_OK and err is not None and f"HTTP {HTTP_UNAVAILABLE}" in err and
          raw_r.status_code == HTTP_UNAVAILABLE and raw_r.json().get("detail") == "simulated maintenance window",
          "behaviour reject → 503 with the reason as JSON; the client raises AltruistOAuthError", f"{c1} {err}")
    c2, _ = await api(U_SA, "PUT", cpu("main", "/behaviour"), {"default_outcome": "delay",
                                                               "delay_seconds": DELAY_SECONDS, "reject_reason": None})
    note_audit("counterparty.behaviour_updated", S["cp"]["main"]) if c2 == HTTP_OK else None
    t0 = time.monotonic()
    err2 = None
    try:
        await AO.fetch_resource(api_base=hosts["api_base"], resource="households", access_token=tok.access_token,
                                timeout=DELAY_CLIENT_TIMEOUT_S, transport=APP_T)
    except AO.AltruistOAuthError as exc:
        err2 = str(exc)
    el = time.monotonic() - t0
    check(c2 == HTTP_OK and err2 is not None and "ReadTimeout" in err2 and el < DELAY_MAX_ELAPSED_S,
          f"behaviour delay {DELAY_SECONDS} s (over the client's 15 s) → the client's own timeout fires "
          f"(injected {DELAY_CLIENT_TIMEOUT_S} s through TimeoutASGITransport) as AltruistOAuthError", f"{err2} {el:.1f}s")
    c3, _ = await api(U_SA, "PUT", cpu("main", "/behaviour"), {"default_outcome": "accept", "delay_seconds": None})
    note_audit("counterparty.behaviour_updated", S["cp"]["main"]) if c3 == HTTP_OK else None
    ok = await AO.fetch_resource(api_base=hosts["api_base"], resource="households", access_token=tok.access_token, transport=APP_T)
    check(c3 == HTTP_OK and len(ok) == 3, "behaviour back to accept: served again")


# ── the book ────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_book(conn):
    section("[Y] Book import / export: preview, accept, every rejection reason, replace mode, round trip, append-only "
            "upload log")
    key = "book"
    for kind in ("households", "accounts"):
        c, o = await upload(U_SA, key, kind, main_book()[kind][:2] if kind == "households" else
                            [r for r in main_book()["accounts"] if r["household_id"] in (HH1, HH2)])
    good = [{"position_id": "bk-p-ok", "account_id": A1, "as_of": THU.isoformat(), "cusip": CUSIP_A, "quantity": "5"}]
    bad_pos = [
        {"position_id": "bk-p-unknown", "account_id": A1, "as_of": THU.isoformat(), "cusip": CUSIP_UNKNOWN,
         "quantity": "1"},
        {"position_id": "bk-p-ambig", "account_id": A1, "as_of": THU.isoformat(), "ticker": TICKER_DUP,
         "quantity": "1"},
        {"position_id": "bk-p-noprice", "account_id": A1, "as_of": THU.isoformat(), "cusip": CUSIP_B,
         "quantity": "1"},
        {"position_id": "bk-p-baddate", "account_id": A1, "as_of": "11/12/2026", "cusip": CUSIP_A, "quantity": "1"}]
    before = await sim(conn, "SELECT count(*) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid",
                       S["cp"][key], fetch="fetchval")
    cp_, pv = await upload(U_SA, key, "positions", good + bad_pos, step="preview")
    after_preview = await sim(conn, "SELECT count(*) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid",
                              S["cp"][key], fetch="fetchval")
    reasons = {r["id"]: r["reason"] for r in (pv.get("rejected") or [])}
    check(cp_ == HTTP_OK and pv.get("counts") == {"rows": 5, "accepted": 1, "rejected": 4} and reasons == {
        "bk-p-unknown": "unknown_identifier", "bk-p-ambig": "ambiguous_identifier", "bk-p-noprice": "no_price",
        "bk-p-baddate": "bad_date"} and after_preview == before,
          "PREVIEW: unknown identifier, ambiguous identifier, no price, bad date — each rejected with its reason; the "
          "preview writes nothing", f"{cp_} {dumps(pv.get('counts'))} {reasons}")
    ca, acc = await upload(U_SA, key, "positions", good + bad_pos)
    live = {r["external_id"] for r in await sim(conn, "SELECT external_id FROM cpsim.book_positions WHERE "
                                                      "counterparty_id = $1::uuid AND valid_to IS NULL", S["cp"][key])}
    check(ca == HTTP_OK and live == {"bk-p-ok"} and (acc.get("counts") or {}).get("rejected") == 4,
          "ACCEPT: only the accepted row is in the book; no rejected row stored", f"{live}")
    bad_txn = [{"transaction_id": "bk-t-badtype", "account_id": A1, "type": "transfer", "trade_date": THU.isoformat(),
                "cusip": CUSIP_A},
               {"transaction_id": "bk-t-nopos", "account_id": A1, "type": "buy", "trade_date": THU.isoformat(),
                "cusip": CUSIP_B, "quantity": "1"},
               {"transaction_id": "bk-t-ok", "account_id": A1, "type": "buy", "trade_date": THU.isoformat(),
                "cusip": CUSIP_A, "quantity": "5", "price": "100"}]
    ct, tv = await upload(U_SA, key, "transactions", bad_txn)
    treasons = {r["id"]: r["reason"] for r in (tv.get("rejected") or [])}
    tlive = {r["external_id"] for r in await sim(conn, "SELECT external_id FROM cpsim.book_transactions WHERE "
                                                       "counterparty_id = $1::uuid AND valid_to IS NULL", S["cp"][key])}
    check(ct == HTTP_OK and treasons == {"bk-t-badtype": "bad_type", "bk-t-nopos": "no_position"} and
          tlive == {"bk-t-ok"}, "transactions: a bad type and a transaction without a position in the same account "
          "are rejected with their reasons and not stored", f"{treasons} {tlive}")
    c2, _ = await upload(U_SA, key, "positions", [{"position_id": "bk-p-new", "account_id": A1,
                                                   "as_of": THU.isoformat(), "cusip": CUSIP_A, "quantity": "7"},
                                                  {"position_id": "bk-p-a2", "account_id": A2,
                                                   "as_of": THU.isoformat(), "ticker": TICKER_A, "quantity": "1",
                                                   "market_value": "100"}])
    c3, _ = await upload(U_SA, key, "positions", [{"position_id": "bk-p-new2", "account_id": A1,
                                                   "as_of": THU.isoformat(), "cusip": CUSIP_A, "quantity": "8"}])
    live2 = {r["external_id"]: r["account_external_id"] for r in await sim(
        conn, "SELECT external_id, account_external_id FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND "
              "valid_to IS NULL", S["cp"][key])}
    closed = int(await sim(conn, "SELECT count(*) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND "
                                 "valid_to IS NOT NULL", S["cp"][key], fetch="fetchval"))
    check(c2 == HTTP_OK and c3 == HTTP_OK and live2 == {"bk-p-new2": A1, "bk-p-a2": A2} and closed >= 2,
          "REPLACE mode: an upload naming A1 leaves exactly its new rows for A1; A2 (not named) is untouched; the "
          "replaced rows are closed (valid_to), never deleted", f"{live2} closed={closed}")
    gen = await upload(U_SA, key, "positions", [{"account_id": A2, "as_of": THU.isoformat(), "ticker": TICKER_A,
                                                 "quantity": "1", "market_value": "100"}])
    gid = await sim(conn, "SELECT external_id FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND "
                          "account_external_id = $2 AND valid_to IS NULL", S["cp"][key], A2, fetch="fetchval")
    check(gen[0] == HTTP_OK and isinstance(gid, str) and gid.startswith("sim-") and len(gid) == 20,
          "a blank id becomes a stable generated sim- id", f"{gid}")
    # export CSV → re-import → identical book (round trip)
    exports = {}
    for kind in SB.KINDS:
        ce, out = await api(U_SA, "GET", cpu(key, f"/book/export?format=csv&kind={kind}"))
        exports[kind] = (ce, out.get("text", ""))
        note_audit("counterparty.book_exported", S["cp"][key]) if ce == HTTP_OK else None
    reimport = {}
    for kind in SB.KINDS:
        txt = exports[kind][1]
        if txt.count("\n") > 1:
            reimport[kind] = (await upload(U_SA, key, kind, None, raw_file=(f"{kind}.csv", txt.encode(),
                                                                             "text/csv")))[0]
    again = {}
    for kind in SB.KINDS:
        ce, out = await api(U_SA, "GET", cpu(key, f"/book/export?format=csv&kind={kind}"))
        again[kind] = out.get("text", "")
        note_audit("counterparty.book_exported", S["cp"][key]) if ce == HTTP_OK else None
    check(all(c == HTTP_OK for c, _t in exports.values()) and all(c == HTTP_OK for c in reimport.values()) and
          all(again[k] == exports[k][1] for k in SB.KINDS) and exports["positions"][1].startswith(
              ",".join(SB.COLUMNS["positions"])),
          "ROUND TRIP: export CSV (the import columns) → re-import → the identical book", f"{reimport}")
    cj, js = await api(U_SA, "GET", cpu(key, "/book/export?format=json"))
    note_audit("counterparty.book_exported", S["cp"][key]) if cj == HTTP_OK else None
    doc = js if "households" in js else json.loads(js.get("text") or "{}")   # raw() parses a JSON body
    check(cj == HTTP_OK and set(doc) >= {"households", "accounts", "positions", "transactions", "as_of"} and
          doc.get("simulated") is True and all(set(ACF.POSITION_FIELDS) <= set(p) for ps in doc["positions"].values()
                                               for p in ps), "export JSON: the API's own response shapes")
    for kind in ("positions", "transactions"):
        ct2, tpl = await api(U_SA, "GET", cpu(key, f"/book/template?kind={kind}&format=csv"))
        note_audit("counterparty.book_template_downloaded", S["cp"][key]) if ct2 == HTTP_OK else None
        check(ct2 == HTTP_OK and tpl.get("text", "").splitlines()[0] == ",".join(SB.COLUMNS[kind]),
              f"the {kind} CSV template carries the import columns")
    big = ("big.json", b"[" + b" " * (2 * 1024 * 1024 + 10) + b"]", "application/json")
    cb, _ = await upload(U_SA, key, "positions", None, raw_file=big)
    check(cb == HTTP_TOO_LARGE, "the size limit is enforced (413 before decoding)", f"{cb}")
    uploads = await sim(conn, "SELECT id FROM cpsim.book_uploads WHERE counterparty_id = $1::uuid", S["cp"][key])
    uid = str(uploads[0]["id"]) if uploads else None
    _r, e1 = await rolled_back(conn, lambda c: c.execute("UPDATE cpsim.book_uploads SET file_name = 'x' WHERE id = "
                                                         "$1::uuid", uid))
    _r, e2 = await rolled_back(conn, lambda c: c.execute("DELETE FROM cpsim.book_uploads WHERE id = $1::uuid", uid))
    op = await operator_conn()
    try:
        _r, e3 = await rolled_back(op, lambda c: c.execute("UPDATE cpsim.book_uploads SET file_name = 'x' WHERE id = "
                                                           "$1::uuid", uid), role=None)
        _r, e4 = await rolled_back(op, lambda c: c.execute("DELETE FROM cpsim.book_uploads WHERE id = $1::uuid", uid),
                                   role=None)
    finally:
        await close_conn(op)
    check(bool(uploads) and isinstance(e1, asyncpg.InsufficientPrivilegeError) and isinstance(
        e2, asyncpg.InsufficientPrivilegeError) and e3 is not None and "append-only" in str(e3) and e4 is not None
        and "append-only" in str(e4), "the upload log is append-only: cpsim_app holds no UPDATE / DELETE; even the "
          "owner is refused by the trigger without the teardown switch", f"{e1!r} {e2!r} {e3!r} {e4!r}")


# ── clock ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_clock(conn):
    section("[Y] Clock: positions follow the clock date; transactions after it are served only once it advances")
    set_sim_creds("main")     # cpsim02 fix 1: self-contained for an --only one-off (was set only by read_api)
    hosts = sim_hosts("main")
    code, _s = await authorize_code()
    tok = await AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts)
    S["issued"] += [code, tok.access_token, tok.refresh_token]
    p1 = {p["id"] for p in await AO.fetch_resource(api_base=hosts["api_base"], resource="positions",
                                                   access_token=tok.access_token, param=A1, transport=APP_T)}
    t1 = {t["id"] for t in await AO.fetch_resource(api_base=hosts["api_base"], resource="transactions",
                                                   access_token=tok.access_token, param=A1, transport=APP_T)}
    ca, adv = await api(U_SA, "POST", cpu("main", "/clock/advance"))
    note_audit("counterparty.clock_advanced", S["cp"]["main"]) if ca == HTTP_OK else None
    p2 = {p["id"] for p in await AO.fetch_resource(api_base=hosts["api_base"], resource="positions",
                                                   access_token=tok.access_token, param=A1, transport=APP_T)}
    t2 = {t["id"] for t in await AO.fetch_resource(api_base=hosts["api_base"], resource="transactions",
                                                   access_token=tok.access_token, param=A1, transport=APP_T)}
    check(p1 == {"cpv-p-a1-thu", "cpv-p-a1-cash-thu"} and "cpv-t-a1-buy-mon" not in t1 and ca == HTTP_OK and
          adv.get("to") == MON.isoformat() and p2 == {"cpv-p-a1-mon", "cpv-p-a1-cash-mon"} and
          "cpv-t-a1-buy-mon" in t2, "Friday: the Thursday snapshot, no Monday trade; advance → Monday: the Monday "
          "snapshot and the Monday trade", f"{p1} {t1} → {adv} {p2} {t2}")


# ── client ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _load_old_client():
    text = VS.git_show(BEFORE_REF, "apps/api/services/altruist_oauth.py")
    if not text:
        return None
    path = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"cpsim02_old_altruist_oauth_{os.getpid()}.py"
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cpsim02_old_altruist_oauth", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cpsim02_old_altruist_oauth"] = mod
    spec.loader.exec_module(mod)
    path.unlink(missing_ok=True)
    return mod


async def _client_requests(mod, env):
    seen = []

    def h(req):
        seen.append((req.method, str(req.url), sorted((k, v) for k, v in req.headers.items()
                                                      if k.lower() != "user-agent"), req.content.decode()))
        if req.url.path.endswith("/oauth/token"):
            return httpx.Response(HTTP_OK, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 60})
        k = req.url.path.rstrip("/").split("/")[-1]
        return httpx.Response(HTTP_OK, json={k: [{"id": 1, "name": "n", "quantity": "2", "type": "buy"}]})

    tr = httpx.MockTransport(h)
    saved = (mod.require_active_connection, mod.get_decrypted_access_token)

    async def rac(conn, **k):
        return {"id": "c1"}

    async def gdat(conn, **k):
        return "tok"

    mod.require_active_connection, mod.get_decrypted_access_token = rac, gdat
    out = []
    orig = httpx.AsyncClient.__init__

    def init(self, *a, **k):
        k["transport"] = tr
        orig(self, *a, **k)
    try:
        out.append(await mod.call_households(None, org_id="o", environment=env, transport=tr))
        out.append(await mod.fetch_accounts_for_household(None, org_id="o", environment=env, household_id="h1",
                                                          transport=tr))
        out.append(await mod.call_positions(None, org_id="o", environment=env, account_id="a1", transport=tr))
        out.append(await mod.call_transactions(None, org_id="o", environment=env, account_id="a1", transport=tr))
        out.append(mod.build_authorize_url(environment=env, state="s"))
        httpx.AsyncClient.__init__ = init
        out.append(await mod.exchange_code_for_tokens(environment=env, code="c"))
        out.append(await mod.refresh_access_token(environment=env, refresh_token="r0"))
    finally:
        httpx.AsyncClient.__init__ = orig
        mod.require_active_connection, mod.get_decrypted_access_token = saved
    return seen, [repr(x) for x in out]


async def y_client(conn):
    section("[Y] Client: the simulator environment with explicit branches; production never a fallback; no "
            "cross-environment credentials; sandbox / production byte-identical")
    try:
        AO.client_credentials("bogus")
        unknown = "returned credentials"
    except AO.AltruistConfigError:
        unknown = "raised"
    check(unknown == "raised", "an unknown environment raises — never the production credentials (contract §7.4)")
    check(AO.client_credentials("production")[0] == os.environ["ALTRUIST_PRODUCTION_CLIENT_ID"] and
          AO.client_credentials("sandbox")[0] == os.environ["ALTRUIST_SANDBOX_CLIENT_ID"],
          "sandbox and production each read their own variables")
    saved = os.environ.get("ALTRUIST_SIMULATOR_CLIENT_SECRET")
    os.environ["ALTRUIST_SIMULATOR_CLIENT_SECRET"] = "sk_live_" + "x" * 20
    try:
        AO.client_credentials("simulator")
        no_prefix = "accepted"
    except AO.AltruistConfigError:
        no_prefix = "refused"
    finally:
        os.environ["ALTRUIST_SIMULATOR_CLIENT_SECRET"] = saved or ""
    refusals = {}
    for label, fn in (("sandbox creds → simulator host",
                       lambda: AO.build_authorize_url(environment="sandbox", state="s", hosts=sim_hosts("main"))),
                      ("production creds → simulator host",
                       lambda: AO.check_destination("production", sim_hosts("main"), "real-secret")),
                      ("simulator creds → sandbox host",
                       lambda: AO.check_destination("simulator", AO.ALTRUIST_HOSTS["sandbox"], "cpsim_live_x")),
                      ("a cpsim_ secret as sandbox credentials",
                       lambda: AO.check_destination("sandbox", AO.ALTRUIST_HOSTS["sandbox"], "cpsim_live_x"))):
        try:
            fn()
            refusals[label] = "sent"
        except AO.AltruistConfigError:
            refusals[label] = "refused"
    check(no_prefix == "refused" and all(v == "refused" for v in refusals.values()),
          "D1 both ways: a simulator secret without the cpsim_ prefix is never sent; sandbox / production "
          "credentials are never sent to a simulator host, and simulator credentials never to a real one",
          f"{no_prefix} {refusals}")
    check(AO.ENVIRONMENTS == ("sandbox", "production", "simulator") and
          AO.ALTRUIST_HOSTS["sandbox"]["api_base"].startswith("https://openapi.stage1"), "ENVIRONMENTS gains 'simulator'; the static sandbox / production hosts are "
          "unchanged")
    old = _load_old_client()
    if old is None:
        check(False, f"the pre-sprint client could not be loaded from {BEFORE_REF}")
        return
    same = {}
    for env in ("sandbox", "production"):
        a = await _client_requests(old, env)
        b = await _client_requests(AO, env)
        same[env] = (a == b, len(a[0]))
    check(all(v[0] and v[1] == 6 for v in same.values()),
          f"sandbox and production send BYTE-IDENTICAL requests (method, URL, headers, body) and parse identically "
          f"before ({BEFORE_REF}) and after — every resource call, authorize, exchange and refresh", f"{same}")
    try:
        old.client_credentials("bogus")
        old_fallback = True
    except Exception:  # noqa: BLE001
        old_fallback = False
    check(old_fallback is True, "reproduced: BEFORE this sprint an unknown environment silently took the production "
          "credentials (the bug the explicit branches close)")


# ── end to end ──────────────────────────────────────────────────────────────────────────────────────────────────
async def _xwalk(conn, org, src, ext):
    return await platform(conn, "SELECT record_id::text AS rid, last_seen FROM portfolio.external_references WHERE "
                                "org_id = $1::uuid AND source_system = $2 AND external_id = $3 AND record_type = "
                                "'account'", org, src, ext, fetch="fetchrow")


async def _acct_counts(conn, org, acc):
    p = int(await platform(conn, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = "
                                 "$2::uuid", org, acc, fetch="fetchval"))
    t = int(await platform(conn, "SELECT count(*) FROM portfolio.transactions t JOIN portfolio.positions p ON p.id = "
                                 "t.position_id WHERE t.org_id = $1::uuid AND p.account_id = $2::uuid", org, acc,
                           fetch="fetchval"))
    return p, t


async def _point(org, key="main"):
    c1, _ = await api(U_SA, "PUT", f"{_P}/org-access/{org}", {"allowed": True})
    note_audit("counterparty.org_setting_changed", org) if c1 == HTTP_OK else None
    c2, b2 = await api(U_SA, "PUT", cpu(key, f"/connections/{org}"), {"point": True})
    note_audit("counterparty.connection_pointed", S["cp"][key]) if c2 == HTTP_OK else None
    return c1, c2, b2


async def y_e2e(conn):
    section("[Y] End to end through the EXISTING client + sync: connect without a browser (D4), identity, positions, "
            "transactions, idempotent re-sync")
    set_sim_creds("main")
    c1, c2, b2 = await _point(ORG_T)
    st = await platform(conn, "SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                        ORG_T, SETTING_CP, fetch="fetchval")
    st_val = json.loads(st) if isinstance(st, str) else st
    check(c1 == HTTP_OK and c2 == HTTP_OK and st_val == SLUG_PREFIX + "main", "Super Admin: counterparty_sim.allowed "
          "on for test org T, and T's simulator pointed at the fixture counterparty (both audited)",
          f"{c1} {c2} {st_val}")
    x_before = await _xwalk(conn, ORG_T, SIM_XWALK, A1)
    code, out = await api(U_CU, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
    conn_row = await platform(conn, "SELECT status, client_id, access_token_last4 FROM altruist_connections WHERE "
                                    "org_id = $1::uuid AND environment = 'simulator' AND system_to IS NULL", ORG_T,
                              fetch="fetchrow")
    check(code == HTTP_OK and out.get("connected") is True and out.get("authorized") == "server_to_server" and
          conn_row is not None and conn_row["status"] == "connected" and conn_row["client_id"] ==
          S["client_ids"]["main"], "POST /altruist/connect {environment: simulator} (the existing route, its own "
          "gate): server-to-server authorize → the SAME callback handling → a stored connection", f"{code} {dumps(out)}")
    after_connect = await _acct_counts(conn, ORG_T, ACC_T1)
    if after_connect[0] == 0:
        find("the post-connect background auto-sync wrote nothing in-process (an ASGI background task) — the explicit "
             "sync below is the proof", f"{after_connect}")
    cs, s1 = await api(U_CU, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    exc = await platform(conn, "SELECT raw_row->>'external_id' AS ext, reason_code FROM account_import_exceptions "
                               "WHERE org_id = $1::uuid", ORG_T)
    exc_map = {r["ext"]: r["reason_code"] for r in exc}
    x_after = await _xwalk(conn, ORG_T, SIM_XWALK, A1)
    p1, t1 = await _acct_counts(conn, ORG_T, ACC_T1)
    srcs = {r["source_system"] for r in await platform(conn, "SELECT DISTINCT source_system FROM portfolio.positions "
                                                             "WHERE org_id = $1::uuid AND account_id = $2::uuid", ORG_T,
                                                       ACC_T1)}
    check(cs == HTTP_OK and s1.get("accounts_resolved") == 1 and s1.get("accounts_synced") == 1 and
          exc_map.get(A2) == AI.REASON_CODE_UNMATCHED_SIMULATOR and exc_map.get(A_SHARED) ==
          AI.REASON_CODE_UNMATCHED_SIMULATOR and x_after is not None and x_before is not None and
          x_after["last_seen"] > x_before["last_seen"],
          "identity: the crosswalked account updates last_seen; every unlinked simulator account (incl. the id only "
          "crosswalked to REAL Altruist) becomes one exception; no account is ever created",
          f"{cs} {dumps(s1)} {exc_map}")
    served_p = len(await AO.fetch_resource(api_base=sim_hosts()["api_base"], resource="positions",
                                           access_token=S["tokens"].access_token, param=A1, transport=APP_T)) if S.get("tokens") else -1
    check(p1 == served_p and p1 > 0 and t1 >= 1 and srcs == {SIM_LEDGER},
          "positions + transactions synced into T's fixture account, tagged altruist_simulator (re-read "
          "independently)", f"positions={p1} served={served_p} transactions={t1} {srcs}")
    cs2, s2 = await api(U_CU, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    p2, t2 = await _acct_counts(conn, ORG_T, ACC_T1)
    check(cs2 == HTTP_OK and (p2, t2) == (p1, t1) and s2.get("positions_created") == 0 and
          s2.get("transactions_created") == 0 and s2.get("positions_skipped_duplicate", 0) >= p1,
          "a second sync is idempotent (no duplicates)", f"{dumps(s2)} {(p1, t1)} → {(p2, t2)}")
    cst, stt = await api(U_CU, "GET", "/api/v1/altruist/status?environment=simulator")
    check(cst == HTTP_OK and stt.get("connected") is True and stt.get("environment") == "simulator" and
          not secret_leaks(dumps(stt)), "status for the simulator environment: same shape, no secret", f"{cst}")
    cm, mo = await api(U_SA, "GET", f"/api/v1/accounts/{ACC_T1}/model")
    if cm == HTTP_OK:
        check(mo.get("custodian_feed") == {"simulated": True}, "the account envelope says custodian_feed.simulated "
              "(the 'Simulated custodian' badge)", f"{mo.get('custodian_feed')}")
    else:
        find("GET /accounts/{id}/model not 200 for the fixture account (its own gate/state) — the badge flag is "
             "proven by the render test and simulator_fed below", f"{cm}")
    fed = await platform(conn, "SELECT EXISTS (SELECT 1 FROM portfolio.external_references WHERE org_id = $1::uuid AND "
                               "source_system = $2 AND record_id = $3::uuid)", ORG_T, SIM_XWALK, ACC_T1,
                         fetch="fetchval")
    check(fed is True, "simulator_fed: the account is crosswalked under ALTRUIST_SIMULATOR")


# ── isolation ───────────────────────────────────────────────────────────────────────────────────────────────────
def _sandbox_mock(positions_for: dict, households: list, accounts: list):
    def h(req):
        p = req.url.path
        if p.endswith("/households"):
            return httpx.Response(HTTP_OK, json={"households": households})
        if p.endswith("/accounts"):
            return httpx.Response(HTTP_OK, json={"accounts": accounts})
        if p.endswith("/positions"):
            return httpx.Response(HTTP_OK, json={"positions": positions_for.get(req.url.params.get("account_id"), [])})
        return httpx.Response(HTTP_OK, json={"transactions": []})
    return httpx.MockTransport(h)


async def y_isolation(conn):
    section("[Y] Isolation: simulator vs real crosswalk both directions; the demo-household rule in a NON-test org; "
            "flag off refused before any call; cross-org both directions")
    real_x0 = await _xwalk(conn, ORG_T, REAL_XWALK, A_SHARED)
    real_p0 = await _acct_counts(conn, ORG_T, ACC_T_REAL)
    sim_x0 = await _xwalk(conn, ORG_T, SIM_XWALK, A1)
    sim_p0 = await _acct_counts(conn, ORG_T, ACC_T1)
    cs, _ = await api(U_CU, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    real_x1 = await _xwalk(conn, ORG_T, REAL_XWALK, A_SHARED)
    real_p1 = await _acct_counts(conn, ORG_T, ACC_T_REAL)
    check(cs == HTTP_OK and real_x0 is not None and real_x1["last_seen"] == real_x0["last_seen"] and
          real_p1 == real_p0, "a SIMULATOR sync never touches the account crosswalked to REAL Altruist (same external "
          "id in the book): its last_seen and its positions unchanged", f"{real_p0} → {real_p1}")
    # the other direction: a SANDBOX sync (mock transport, no network) for org T. cpsim02 fix 1: the simulator
    # baseline is read HERE, after the simulator sync above (which legitimately re-confirms A1's simulator crosswalk,
    # advancing its last_seen) — the claim is about the SANDBOX sync, so it is measured across exactly that sync
    sim_x0 = await _xwalk(conn, ORG_T, SIM_XWALK, A1)
    sim_p0 = await _acct_counts(conn, ORG_T, ACC_T1)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true), "
                           "set_config('app.current_org_id', $1, true)", str(ORG_T))
        await AO.store_new_connection(conn, org_id=str(ORG_T), environment="sandbox", client_id=f"{TAG}-sandbox-id",
                                      client_secret=f"{TAG}-sandbox-secret",
                                      tokens=AO.TokenResponse("verify-at", "verify-rt", 3600, "accounts:read"),
                                      connected_by=None)
        mock = _sandbox_mock({A_SHARED: [{"id": "sbx-pos-1", "cusip": CUSIP_A, "name": NAME + "A", "quantity": "2",
                                          "market_value": "200"}]},
                             [{"id": "sbx-hh", "name": "sbx"}], [{"id": A1, "name": "the simulator's id"},
                                                                 {"id": A_SHARED, "name": "shared"}])
        ident = await AI.resolve_identity(conn, org_id=str(ORG_T), environment="sandbox", transport=mock)
        res = await APS.sync_resolved_accounts(conn, org_id=str(ORG_T), environment="sandbox", transport=mock)
    sim_x1 = await _xwalk(conn, ORG_T, SIM_XWALK, A1)
    sim_p1 = await _acct_counts(conn, ORG_T, ACC_T1)
    real_p2 = await _acct_counts(conn, ORG_T, ACC_T_REAL)
    exc = await platform(conn, "SELECT reason_code FROM account_import_exceptions WHERE org_id = $1::uuid AND "
                               "raw_row->>'external_id' = $2 AND raw_row->>'source_system' = 'ALTRUIST'", ORG_T, A1,
                         fetch="fetchval")
    srcs = {r["source_system"] for r in await platform(conn, "SELECT DISTINCT source_system FROM portfolio.positions "
                                                             "WHERE org_id = $1::uuid AND account_id = $2::uuid", ORG_T,
                                                       ACC_T_REAL)}
    check(sim_x1["last_seen"] == sim_x0["last_seen"] and sim_p1 == sim_p0 and real_p2[0] == real_p0[0] + 1 and
          srcs == {REAL_LEDGER} and res.accounts_synced == 1 and exc == AI.REASON_CODE_UNMATCHED and
          len(ident["resolved"]) == 1, "a SANDBOX sync never touches an ALTRUIST_SIMULATOR account: the simulator "
          "crosswalk's last_seen and its positions unchanged; the simulator's account id is just an unmatched real "
          "account (its own exception); only the real account syncs (tagged 'altruist')",
          f"sim {sim_p0}→{sim_p1} real {real_p0}→{real_p2} {exc} {srcs}; sim last_seen unchanged="
          f"{sim_x1['last_seen'] == sim_x0['last_seen']}; accounts_synced={res.accounts_synced}; "
          f"resolved={len(ident['resolved'])}")
    if NETWORK_CALLS:
        check(False, "the sandbox sync made no network call", f"{NETWORK_CALLS[:2]}")
    # the flag off: T2 has no flag yet
    lg0 = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid",
                        S["cp"]["main"], fetch="fetchval"))
    st0 = int(await platform(conn, "SELECT count(*) FROM altruist_oauth_states WHERE org_id = $1::uuid", ORG_T2,
                             fetch="fetchval"))
    cf, bf = await api(U_CU2, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
    cfs, bfs = await api(U_CU2, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    lg1 = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid",
                        S["cp"]["main"], fetch="fetchval"))
    st1 = int(await platform(conn, "SELECT count(*) FROM altruist_oauth_states WHERE org_id = $1::uuid", ORG_T2,
                             fetch="fetchval"))
    check(cf == HTTP_CONFLICT and cfs == HTTP_CONFLICT and "counterparty_sim.allowed" in dumps(bf) and lg1 == lg0 and
          st1 == st0, "an org WITHOUT the flag: the simulator environment is refused 409 with the reason BEFORE any "
          "call (no request reached the simulator, no OAuth state stored)", f"{cf} {cfs} {dumps(bf)[:200]}")
    # cross-org: point T2 too, connect, sync — each org reads / writes only its own rows
    await _point(ORG_T2)
    t_before = await _acct_counts(conn, ORG_T, ACC_T1), await _acct_counts(conn, ORG_T, ACC_T_REAL)
    cc, _ = await api(U_CU2, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
    cs2, s2 = await api(U_CU2, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    t_after = await _acct_counts(conn, ORG_T, ACC_T1), await _acct_counts(conn, ORG_T, ACC_T_REAL)
    t2p = await _acct_counts(conn, ORG_T2, ACC_T2)
    t_rows_in_t2 = int(await scoped(conn, ORG_T2, "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid",
                                    ORG_T, fetch="fetchval"))
    check(cc == HTTP_OK and cs2 == HTTP_OK and s2.get("accounts_resolved") == 1 and t2p[0] > 0 and
          t_after == t_before and t_rows_in_t2 == 0, "org B's (T2) simulator connection syncs ONLY its own account; "
          "org A's (T) rows unchanged and invisible from B", f"{dumps(s2)} {t_before}→{t_after} {t2p}")
    t2_before = t2p
    cs3, _ = await api(U_CU, "POST", "/api/v1/altruist/sync", {"environment": "simulator"})
    t2_after = await _acct_counts(conn, ORG_T2, ACC_T2)
    x_t2 = await platform(conn, "SELECT count(*) FROM portfolio.external_references WHERE org_id = $1::uuid",
                          ORG_T2, fetch="fetchval")
    check(cs3 == HTTP_OK and t2_after == t2_before and int(x_t2) >= 1, "…and the other direction: org A's sync never "
          "reads or writes org B", f"{t2_before}→{t2_after}")
    await _nontest(conn)


async def _nontest(conn):
    """The NON-test org R, inside ONE rolled-back transaction: a demo household's account syncs; a plain household's
    account → not_a_demo_household and nothing written."""
    hosts = sim_hosts("main")
    code, _s = await authorize_code()
    tok = await AO.exchange_code_for_tokens(environment=AO.SIMULATOR, code=code, hosts=hosts)
    S["issued"] += [code, tok.access_token, tok.refresh_token]

    async def fn(c):
        await c.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, false)", ORG_R,
                        "CPSIM02 Verify Org R (rolled back)", f"{TAG}-r")
        is_test = await c.fetchval("SELECT is_test FROM organizations WHERE id = $1", ORG_R)
        for eid, nm, tags in ((E_R_DEMO, "demo a", ["demo-cpsim2verify"]), (E_R_DEMO2, "demo b", ["demo-cpsim2verify"]),
                              (E_R_PLAIN, "plain", [])):
            await c.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, tags) VALUES ($1, $2, "
                            "$3, 'active', 'individual'::entity_type, $4::text[])", eid, ORG_R, NAME + nm, tags)
        for hh, members in ((H_R_DEMO, (E_R_DEMO, E_R_DEMO2)), (H_R_PLAIN, (E_R_PLAIN,))):
            await c.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, ORG_R, NAME + "hh")
            for e in members:
                await c.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        for acc, eid, hh, n in ((ACC_R_DEMO, E_R_DEMO, H_R_DEMO, 8), (ACC_R_PLAIN, E_R_PLAIN, H_R_PLAIN, 9)):
            await c.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id) VALUES ($1, $2, $3, $4, 'ALTRUIST', "
                "'individual', 'taxable', $5, $6)", acc, ORG_R, f"****{n:04d}",
                hashlib.sha256(f"{TAG}-{n}".encode()).hexdigest(), eid, hh)
        for ext, acc in ((A1, ACC_R_DEMO), (A2, ACC_R_PLAIN)):
            await c.execute("INSERT INTO portfolio.external_references (org_id, source_system, external_id, "
                            "record_type, record_id) VALUES ($1, $2, $3, 'account', $4)", ORG_R, SIM_XWALK, ext, acc)
        for k, v in ((SETTING, "true"), (SETTING_CP, json.dumps(SLUG_PREFIX + "main"))):
            await c.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES "
                            "($1, $2, $3::jsonb, 'counterparty_sim', false)", ORG_R, k, v)
        await c.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_R))
        await AO.store_new_connection(c, org_id=str(ORG_R), environment="simulator",
                                      client_id=S["client_ids"]["main"], client_secret=S["keys"]["main"], tokens=tok,
                                      connected_by=None)
        ident = await AI.resolve_identity(c, org_id=str(ORG_R), environment="simulator")
        res = await APS.sync_resolved_accounts(c, org_id=str(ORG_R), environment="simulator")
        demo_p = await c.fetchval("SELECT count(*) FROM portfolio.positions WHERE org_id = $1 AND account_id = $2",
                                  ORG_R, ACC_R_DEMO)
        plain_p = await c.fetchval("SELECT count(*) FROM portfolio.positions WHERE org_id = $1 AND account_id = $2",
                                   ORG_R, ACC_R_PLAIN)
        plain_t = await c.fetchval("SELECT count(*) FROM portfolio.transactions t JOIN portfolio.positions p ON p.id = "
                                   "t.position_id WHERE p.account_id = $1", ACC_R_PLAIN)
        exc = await c.fetchval("SELECT reason_code FROM account_import_exceptions WHERE org_id = $1 AND "
                               "raw_row->>'external_id' = $2", ORG_R, A2)
        return {"is_test": is_test, "resolved": [r["altruist_account_id"] for r in ident["resolved"]],
                "refused": [r["altruist_account_id"] for r in ident.get("refused", [])], "demo_p": int(demo_p),
                "plain_p": int(plain_p), "plain_t": int(plain_t), "exc": exc,
                "sync_refused": [str(x) for x in res.refused_accounts]}
    out, err = await rolled_back(conn, fn, role=None, super_admin=True)
    check(err is None and out is not None and out["is_test"] is False and out["resolved"] == [A1] and
          out["refused"] == [A2] and out["exc"] == AI.REASON_CODE_NOT_DEMO and out["demo_p"] > 0 and
          out["plain_p"] == 0 and out["plain_t"] == 0 and out["sync_refused"] == [str(ACC_R_PLAIN)],
          "a NON-test org (rolled back): the DEMO household's account syncs; the plain household's account → one "
          "'not_a_demo_household' exception and NOTHING written to positions / transactions (identity AND sync stop "
          "it)", f"{out} {err!r}")
    left = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1::uuid", ORG_R, fetch="fetchval"))
    check(left == 0, "the non-test org and its demo rows were never committed", f"{left}")


# ── webhooks ────────────────────────────────────────────────────────────────────────────────────────────────────
async def _post_webhook(body: bytes, signature: str | None):
    h = {"Content-Type": "application/json"}
    if signature is not None:
        h[AW.SIGNATURE_HEADER] = signature
    return await raw("POST", "/api/v1/altruist/webhook", headers=h, content=body)


def _sign(key: str, body: bytes) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


async def y_webhooks(conn):
    section("[Y] Webhooks: signed out, verified in, forced environment, dedup, resync; off → none; spoof refused; "
            "org_id no longer leaked")
    cw, _ = await api(U_SA, "PUT", cpu("main", "/behaviour"), {"webhooks_enabled": True})
    note_audit("counterparty.behaviour_updated", S["cp"]["main"]) if cw == HTTP_OK else None
    p0 = await _acct_counts(conn, ORG_T, ACC_T1)
    out0 = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND direction "
                               "= 'out'", S["cp"]["main"], fetch="fetchval"))
    new_rows = [{"position_id": "cpv-p-a1-mon-v2", "account_id": A1, "as_of": MON.isoformat(), "cusip": CUSIP_A,
                 "quantity": "15", "currency_code": "USD"},
                {"position_id": "cpv-p-a1-cash-mon-v2", "account_id": A1, "as_of": MON.isoformat(), "row_type": "cash",
                 "name": "Cash", "market_value": "300.00", "currency_code": "USD"}]
    ca, acc = await upload(U_SA, "main", "positions", new_rows)
    dels = acc.get("webhooks") or []
    ev = dels[0] if dels else {}
    row = await platform(conn, "SELECT org_id, processed_at, event_type, raw_payload FROM altruist_webhook_events "
                               "WHERE event_id = $1", ev.get("event_id"), fetch="fetchrow") if ev else None
    payload = (json.loads(row["raw_payload"]) if isinstance(row["raw_payload"], str) else row["raw_payload"]) \
        if row else {}
    p1 = await _acct_counts(conn, ORG_T, ACC_T1)
    out1 = await sim(conn, "SELECT status, auth_outcome, latency_ms, path FROM cpsim.request_log WHERE counterparty_id "
                           "= $1::uuid AND direction = 'out' ORDER BY at DESC LIMIT 1", S["cp"]["main"])
    check(ca == HTTP_OK and len(dels) == 1 and ev.get("event_type") == "account.positions_updated" and
          ev.get("outcome") == "delivered" and ev.get("status") == HTTP_OK and row is not None and
          row["org_id"] == ORG_T and row["processed_at"] is not None and payload.get("environment") == "simulator"
          and payload.get("account_id") == A1 and p1[0] > p0[0],
          "an accepted upload (webhooks on) sends ONE signed account.positions_updated event, received by the REAL "
          "receiver in-process: resolved to T by the ALTRUIST_SIMULATOR crosswalk and the account re-synced",
          f"{ca} {dumps(dels)} {p0}→{p1}")
    out_n = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND "
                                "direction = 'out'", S["cp"]["main"], fetch="fetchval"))
    check(out_n == out0 + 1 and bool(out1) and out1[0]["auth_outcome"] == "delivered" and
          out1[0]["latency_ms"] is not None and
          out1[0]["path"] == SW.RECEIVER_PATH and not secret_leaks(dumps([dict(r) for r in out1])),
          "the delivery is in the request log (direction out, status, latency) — never the secret or the signature")
    body = json.dumps({"event_id": f"{TAG}-forced-1", "event_type": "account.positions_updated", "account_id": A1,
                       "environment": "sandbox"}, separators=(",", ":")).encode()
    S["events"].append(f"{TAG}-forced-1")
    nb = len(NETWORK_CALLS)
    cf, bf, _h = await _post_webhook(body, _sign(S["whsec"], body))
    cd, bd, _h = await _post_webhook(body, _sign(S["whsec"], body))
    n_rows = int(await platform(conn, "SELECT count(*) FROM altruist_webhook_events WHERE event_id = $1",
                                f"{TAG}-forced-1", fetch="fetchval"))
    check(cf == HTTP_OK and bf.get("status") == "resolved" and (bf.get("sync") or {}).get("triggered") is True and
          len(NETWORK_CALLS) == nb and cd == HTTP_OK and bd.get("status") == "duplicate" and n_rows == 1,
          "signed with the SIMULATOR secret, a payload claiming 'sandbox' is FORCED to the simulator environment "
          "(resolved through the simulator crosswalk, synced in-process, no network); a resend is a duplicate "
          "(event_id dedup)", f"{cf} {dumps(bf)} {cd} {dumps(bd)} rows={n_rows}")
    check("org_id" not in bf and "org_id" not in bd, "the receiver no longer returns the internal org_id (§7.10)",
          f"{sorted(bf)}")
    spoof = json.dumps({"event_id": f"{TAG}-spoof-1", "event_type": "account.positions_updated", "account_id": A1,
                        "environment": "simulator"}, separators=(",", ":")).encode()
    S["events"].append(f"{TAG}-spoof-1")
    pb = await _acct_counts(conn, ORG_T, ACC_T1)
    cs, bs, _h = await _post_webhook(spoof, _sign(S["real_whsec"], spoof))
    srow = await platform(conn, "SELECT org_id, processed_at FROM altruist_webhook_events WHERE event_id = $1",
                          f"{TAG}-spoof-1", fetch="fetchrow")
    pa = await _acct_counts(conn, ORG_T, ACC_T1)
    check(cs == HTTP_OK and bs.get("status") == "refused_environment" and srow is not None and srow["org_id"] is None
          and srow["processed_at"] is None and pa == pb, "a REAL-secret-signed payload claiming 'simulator' is logged "
          "and NOT dispatched (no org resolved, nothing synced)", f"{cs} {dumps(bs)}")
    cw2, bw2, _h = await _post_webhook(spoof, "0" * 64)
    cw3, bw3, _h = await _post_webhook(spoof, None)
    check(cw2 == HTTP_UNAUTH and cw3 == HTTP_UNAUTH and bw2 == bw3 == {"detail": "Invalid signature"},
          "a wrong / missing signature → the one generic 401", f"{cw2} {cw3}")
    cw4, _ = await api(U_SA, "PUT", cpu("main", "/behaviour"), {"webhooks_enabled": False})
    note_audit("counterparty.behaviour_updated", S["cp"]["main"]) if cw4 == HTTP_OK else None
    o_before = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND "
                                   "direction = 'out'", S["cp"]["main"], fetch="fetchval"))
    co, ao = await upload(U_SA, "main", "positions", new_rows)
    o_after = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND "
                                  "direction = 'out'", S["cp"]["main"], fetch="fetchval"))
    ct, bt = await api(U_SA, "POST", cpu("main", "/webhooks/test"), {"account_id": A1})
    check(co == HTTP_OK and ao.get("webhooks") == [] and o_after == o_before and ct == HTTP_CONFLICT,
          "webhooks OFF → nothing sent after an accepted upload, and 'Send test event' refuses (409)",
          f"{dumps(ao.get('webhooks'))} {o_before}→{o_after} {ct}")
    saved = os.environ.pop(SW.SECRET_ENV, None)
    try:
        d = await SW.send_event(counterparty_id=S["cp"]["main"], slug=SLUG_PREFIX + "main", account_id=A1,
                                event_type="account.positions_updated", behaviour={"webhooks_enabled": True})
    finally:
        if saved is not None:
            os.environ[SW.SECRET_ENV] = saved
    u = await sim(conn, "SELECT status, auth_outcome FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND "
                        "direction = 'out' ORDER BY at DESC LIMIT 1", S["cp"]["main"], fetch="fetchrow")
    check(d.get("outcome") == "not_sent" and d.get("attempts") == 0 and u is not None and u["auth_outcome"] ==
          "unsigned", "no simulator secret → the simulator refuses to send (recorded 'unsigned', not raised)",
          f"{dumps(d)}")
    cw5, _ = await api(U_SA, "PUT", cpu("main", "/behaviour"), {"webhooks_enabled": True})
    note_audit("counterparty.behaviour_updated", S["cp"]["main"]) if cw5 == HTTP_OK else None
    ct2, bt2 = await api(U_SA, "POST", cpu("main", "/webhooks/test"), {"account_id": A1})
    dl = (bt2 or {}).get("delivery") or {}
    if dl.get("event_id"):
        S["events"].append(dl["event_id"])
    note_audit("counterparty.webhook_test_sent", S["cp"]["main"]) if ct2 == HTTP_OK else None
    check(ct2 == HTTP_OK and dl.get("outcome") == "delivered", "'Send test event' (webhooks on) delivers one signed "
          "event", f"{ct2} {dumps(dl)}")


# ── conformance ─────────────────────────────────────────────────────────────────────────────────────────────────
class _Rename(httpx.AsyncBaseTransport):
    """The planted divergence: a simulator that renames `quantity` (to its alias `units`) in positions."""

    def __init__(self, inner):
        self.inner = inner

    async def handle_async_request(self, request):
        res = await self.inner.handle_async_request(request)
        if request.url.path.endswith("/v2/positions") and res.status_code == HTTP_OK:
            await res.aread()
            doc = json.loads(res.content)
            for p in doc.get("positions", []):
                if "quantity" in p:
                    p["units"] = p.pop("quantity")
            return httpx.Response(res.status_code, headers={k: v for k, v in res.headers.items()
                                                            if k.lower() not in ("content-length",)},
                                  content=json.dumps(doc).encode(), request=request)
        return res


async def y_conformance(conn):
    section("[Y] Conformance: passes on the simulator; FAILS on a planted divergence citing the contract row; refuses "
            "sandbox without the flag")
    creds = (S["client_ids"]["main"], S["keys"]["main"])
    out = await ACF.run(environment=AO.SIMULATOR, hosts=sim_hosts("main"), credentials=creds, transport=APP_T,
                        redirect_uri=REDIRECT)
    ids = [r["id"] for r in out["results"] if r["passed"] is True]
    check(out["failed"] == 0 and len(ids) >= 18, f"the suite PASSES against the simulator ({len(ids)} checks: "
          f"{', '.join(ids)})", f"{[r for r in out['results'] if r['passed'] is False]}")
    bad = await ACF.run(environment=AO.SIMULATOR, hosts=sim_hosts("main"), credentials=creds,
                        transport=_Rename(APP_T), redirect_uri=REDIRECT)
    failed = {r["id"]: r["contract"] for r in bad["results"] if r["passed"] is False}
    check(failed.get("C12") == "§6.5 / T6" and bad["failed"] >= 1,
          "a planted divergence (positions rename quantity → units) FAILS the suite, citing contract row §6.5 / T6 — "
          "even though the client's alias tolerance would have read it", f"{failed}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALTRUIST_SANDBOX_")}
    c1, o1, e1 = run_cmd([sys.executable, str(CONFORMANCE), "--target", "sandbox"], REPO, timeout=120, env=env)
    c2, o2, e2 = run_cmd([sys.executable, str(CONFORMANCE), "--target", "sandbox", "--i-have-sandbox-access"], REPO,
                         timeout=120, env=env)
    check(c1 == REFUSED_EXIT and "REFUSED" in o1 and c2 == REFUSED_EXIT and "REFUSED" in o2,
          "--target sandbox refuses to start without --i-have-sandbox-access, and with it but without "
          "ALTRUIST_SANDBOX_* (exit 2, nothing called)", f"{c1} {o1[-200:]} {c2} {o2[-200:]} {e1[-100:]}")
    cr, br = await api(U_SA, "POST", cpu("main", "/conformance"), {"key": S["keys"]["main"]})
    note_audit("counterparty.conformance_run", S["cp"]["main"]) if cr == HTTP_OK else None
    cg, bg = await api(U_SA, "GET", cpu("main", "/conformance"))
    last = (bg or {}).get("last") or {}
    check(cr == HTTP_OK and br.get("failed") == 0 and cg == HTTP_OK and last.get("failed") == 0 and
          last.get("target") == "simulator" and not secret_leaks(dumps(bg)),
          "the admin page's run: stored (the last result shown), audited, no key in it", f"{cr} {cg} {dumps(last)[:200]}")


# ── admin ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_admin(conn):
    section("[Y] Admin API + page: Super Admin only (org admin → 403 on every new route, identical valid request); "
            "audited by fixture ids")
    cid = S["cp"]["main"]
    f = book_file(main_book()["households"][:1])
    calls = [("GET", f"{_P}/{cid}/book", None, {}), ("GET", f"{_P}/{cid}/book/template?kind=positions&format=csv", None,
                                                       {}),
             ("POST", f"{_P}/{cid}/book/preview", None, {"files": {"file": f}, "data": {"kind": "households"}}),
             ("POST", f"{_P}/{cid}/book/accept", None, {"files": {"file": f}, "data": {"kind": "households"}}),
             ("GET", f"{_P}/{cid}/book/export?format=json", None, {}),
             ("POST", f"{_P}/{cid}/webhooks/test", {"account_id": A1}, {}),
             ("GET", f"{_P}/{cid}/connections", None, {}),
             ("PUT", f"{_P}/{cid}/connections/{ORG_T}", {"point": True}, {}),
             ("POST", f"{_P}/{cid}/connections/{ORG_T}/connect", None, {}),
             ("POST", f"{_P}/{cid}/connections/{ORG_T}/sync", None, {}),
             ("GET", f"{_P}/{cid}/conformance", None, {}),
             ("POST", f"{_P}/{cid}/conformance", {"key": "cpsim_live_" + "0" * 40}, {})]
    check(len(calls) == len(ROUTES), "every new admin route is probed", f"{len(calls)}")
    a0 = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE resource_id = $1::uuid", cid, fetch="fetchval"))
    bad = []
    for m, p, body, kw in calls:
        code, b = await api(U_OA, m, p, body, **kw)
        if code != HTTP_FORBIDDEN:
            bad.append((m, p, code))
    a1 = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE resource_id = $1::uuid", cid, fetch="fetchval"))
    check(not bad and a1 == a0, f"an org admin gets 403 on all {len(calls)} new routes (valid bodies — only the gate "
          "refuses); nothing written or audited", f"{bad} audit {a0}→{a1}")
    cs, b = await api(U_SA, "GET", cpu("main", "/connections"))
    rows = {r["id"]: r for r in (b.get("rows") or [])}
    check(cs == HTTP_OK and rows.get(str(ORG_T), {}).get("points_here") is True and
          rows.get(str(ORG_T), {}).get("connection_status") == "connected",
          "Connections panel: the allowed org that points here, connected", f"{cs} {dumps(rows.get(str(ORG_T)))}")
    cc, bc = await api(U_SA, "POST", cpu("main", f"/connections/{ORG_T}/sync"))
    note_audit("counterparty.connection_synced", cid) if cc == HTTP_OK else None
    check(cc == HTTP_OK and (bc.get("sync") or {}).get("environment") == "simulator",
          "Connections 'Sync now' runs the SAME /altruist sync for the chosen org (in its RLS context)", f"{cc}")
    cb_, bb = await api(U_SA, "GET", cpu("main", "/book"))
    check(cb_ == HTTP_OK and bool((bb.get("vocabularies") or {}).get("book", {}).get("kinds")) and
          bb.get("permissions", {}).get("can_write") is True and len(bb.get("uploads") or []) >= 4,
          "the book envelope: counts, the upload history, kinds / reasons from the server (Rule 1)")
    ids = sorted({r for _a, r in S["audits"]})
    rows_a = await platform(conn, "SELECT action, resource_id::text AS rid, payload::text AS p FROM audit_log WHERE "
                                  "action LIKE 'counterparty.%' AND resource_id = ANY($1::uuid[])", ids)
    got = Counter((r["action"], r["rid"]) for r in rows_a)
    want = Counter(S["audits"])
    check(bool(want) and got == want, f"every action is audited exactly once ({sum(want.values())} counterparty.* rows, "
          "counted by fixture ids)", f"missing={dict(want - got)} extra={dict(got - want)}")
    check(not secret_leaks(dumps([r["p"] for r in rows_a])), "no audit payload carries a key, token or secret")


async def y_safety(conn):
    section("[Y] Safety: boundary check; app role still cannot read cpsim.*; no network; the setting guard")
    code, out, err = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=600)
    check(code == 0 and "CPSIM BOUNDARY: OK" in out, "cpsim_boundary_check passes (services reach the simulator only "
          "over HTTP; the may-connect rule only through its public wrapper)", f"{code} {(out + err)[-300:]}")
    _r, e1 = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES ($1::uuid, $2, "
        "$3::jsonb, 'general', false)", str(ORG_T2), SETTING_CP, json.dumps(SLUG_PREFIX + "main")), role=None,
        org=ORG_T2)
    _r, e2 = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES ($1::uuid, $2, "
        "$3::jsonb, 'general', false) ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = "
        "EXCLUDED.setting_value", str(ORG_T2), SETTING_CP, json.dumps("NOT A SLUG")), role=None, super_admin=True)
    code_g, _b = await api(U_OA, "PUT", f"/api/v1/orgs/{ORG_T}/settings/{SETTING_CP}", {"value": "x"})
    check(isinstance(e1, asyncpg.InsufficientPrivilegeError) and isinstance(e2, asyncpg.CheckViolationError) and
          code_g == HTTP_FORBIDDEN, "counterparty_sim.altruist_counterparty: the database refuses it without the Super "
          "Admin GUC and refuses a non-slug value; the generic settings path refuses it (403)",
          f"{e1!r} {e2!r} {code_g}")
    check(bool(VERIFY_CALLS) and all(t in CLAIMS_BY_TOKEN for t in VERIFY_CALLS),
          "the token boundary saw only this run's fixture tokens (no cpsim_ value ever reached verify_token)")


async def y_harness(conn):
    section("[Y] menuvisibility_harness.mjs standalone: every gate shape, no crash, exit 0")
    code, out, err = run_cmd(["node", str(HARNESS)], REPO, timeout=120)
    try:
        d = json.loads(out)
    except ValueError:
        d = None
    gates = [g["gate"] for g in (d or {}).get("gates", [])]
    kinds = {g.split(":")[0].split(" ")[0] for g in gates}
    check(code == 0 and d is not None and bool(gates) and not any(g.startswith("unknown") for g in gates) and
          {"anyOf", "internal", "roles", "perm"} <= kinds and "TypeError" not in err,
          "node menuvisibility_harness.mjs exits 0 standalone, 0 FAIL: every gate shape described (roles, perm, staff, "
          "internal, anyOf, adminArea), none unknown", f"{code} {err[-300:]}")
    regs = (d or {}).get("regressions", [])
    check(bool(regs) and all(r["unchanged"] or r.get("authz1_expected") for r in regs),
          "the legacy-rule regression comparison holds (AUTHZ1's derived default-allow removal only)",
          f"{[(r['name'], r.get('lost')) for r in regs if not (r['unchanged'] or r.get('authz1_expected'))]}")
    pers = [p["name"] for p in (d or {}).get("navregroup", {}).get("personas", [])
            if not (p["removals_exact"] and p["additions_ok"] and not p["new_refusals"])]
    if pers:
        find("pre-existing, unmasked by the crash fix: the navregroup persona snapshot is stale for these personas "
             "(persona permissions predate later sprints; the traced-gate model lacks the Super Admin bypass) — "
             "logged in OUTSTANDING_TODO_LIST, no menu gate changed", f"{pers}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check reports CURRENT (new admin routes rowed; the new "
          "/cpsim routes in the simulator section)", f"{code} {(out + err)[-300:]}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) == NODE_TEST_COUNT and
          n.group(1) == p.group(1) and re.search(r"^# fail 0$", o1, re.M) is not None,
          f"the cpsim02 render tests ({NODE_TEST}, counted by their file) all pass "
          f"({n.group(1) if n else '?'}/{NODE_TEST_COUNT})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1800)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regressions — each ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s, its OWN final TOTAL line")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")
    for script, why in NOT_RUN.items():
        print(f"[INFO] not run as a regression: {script} — {why}")


SECTION_TIMEOUT = 600   # every one-off section must finish in < 10 minutes


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


async def print_fixture_counts(conn, when: str) -> int:
    c = await counts(conn)
    for t in sorted(c):
        print(f"[INFO] {when}: {t} = {c[t]}")
    return sum(c.values())


async def cleanup_only(conn) -> int:
    await print_fixture_counts(conn, "before cleanup")
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    check(left == 0, "zero fixture rows remain (public + cpsim)", f"{left}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


FIXTURE_SECTIONS = (("read_api", y_read_api), ("behaviour", y_behaviour), ("book", y_book), ("clock", y_clock),
                    ("client", y_client), ("e2e", y_e2e), ("isolation", y_isolation), ("webhooks", y_webhooks),
                    ("conformance", y_conformance), ("admin", y_admin), ("safety", y_safety), ("harness", y_harness))
# cpsim02 fix 1: webhooks uploads MONDAY rows and expects the resync to add positions — served only once the clock
# section has advanced the counterparty to Monday (the full run's order); the one-off now declares that dependency
ONLY_NEEDS = {"read_api": (), "behaviour": (), "book": (), "clock": (), "client": (), "e2e": ("read_api",),
              "isolation": ("read_api", "e2e"), "webhooks": ("read_api", "clock", "e2e"), "conformance": (),
              "admin": ("read_api", "e2e"), "safety": (), "harness": ()}


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    bad = [x for x in want if x not in ONLY_NEEDS and x != "seed"]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, {', '.join(ONLY_NEEDS)}")
    names = {x for x in want if x != "seed"}
    for x in list(names):
        names |= set(ONLY_NEEDS.get(x, ()))
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
        return await cleanup_only(conn)
    if "--self-checks-only" in sys.argv:
        try:
            await run("task 1", y_task1, conn)
            await run("routes", y_routes, conn)
        finally:
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
                if name in ("e2e", "isolation", "webhooks", "admin") and not S.get("tokens"):
                    check(False, f"{name}: not run — the read API section produced no tokens (never skipped "
                                 "silently)")
                    continue
                await run(name, fn, conn)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run "
              "(every simulator, admin and webhook call was in-process)", f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows "
                "unchanged; no counterparty or book row left")
        R.set_today(None)
        SM.set_now(None)
        SO.set_now(None)
        AO.set_simulator_transport(None)
        SW.set_transport(None)
        SW.set_sleep(None)
        await close_app_pools()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (orgs, users, grants, accounts, crosswalks, positions, "
                  "transactions, exceptions, connections, webhook events, settings, audit rows, fixture securities, "
                  "every cpsim table)", f"{left}")
            ncp = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1",
                                SLUG_PREFIX + "%", fetch="fetchval"))
            nbk = int(await sim(conn, f"SELECT (SELECT count(*) FROM cpsim.book_positions WHERE counterparty_id IN "
                                      f"{FIX_CP}) + (SELECT count(*) FROM cpsim.book_uploads WHERE counterparty_id IN "
                                      f"{FIX_CP})", fetch="fetchval"))
            check(ncp == 0 and nbk == 0, "no fixture counterparty or book row exists after teardown", f"{ncp} {nbk}")
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
