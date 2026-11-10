"""verify_rep15 — Replication Studio, sprint 15 (phase 4 ends): PLATFORM-PUBLISHED MODELS AND TENANT SUBSCRIPTIONS.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep15.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a seeding exception included) and
exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``), the Auth0 profile call,
R2 (``services.storage.upload_bytes`` / ``download_bytes`` → an in-memory dict that refuses an overwrite — the fake
transport verify_disclosures1 uses; ``get_s3_client`` refuses), and the prices of securities an account does not
hold (``rebalance_proposals._unheld_prices``). NO network (the httpx transport refuses and records), NO model call (the
three AI helpers refuse and record). REAL: the app through an ASGI transport, rep3's registry and approvals, rep5's
joins and proposals, the human-task engine, maker-checker, the disclosure library (resolver + acknowledgements), the
GL posting API, the triggers, RLS and the live database. The script's own connection is app_service
(rolbypassrls = false, asserted).

FIXTURES: a FIXTURE PROVIDER ("platform") org and two fixture TENANT orgs (99000000-0000-0000-0000-0000001f0001 /
0002 / 0003, is_test = true, asserted), users seeded with real user_roles grants (three Super Admins in the fixture
provider org, a provider-org viewer holding view_models only, two org admins per tenant, a tenant member with no
permission) — the app's decision on each asserted; platform securities 'REP15 VERIFY …' with tickers ZZR15…; fixture
custodian ZZR15C. Every reserved prefix is checked absent from real rows first and removed BY ID. The REAL Hollisworks
platform org and its gate are only READ (asserted unchanged and OFF). The ONE platform-scope row this script writes
is a fixture version of model_provider.subadvisory_agreement (only when no real platform version of that key exists),
drafted by a fixture Super Admin and removed by its drafter id; while it exists no real org can act on it (the real
gate is OFF and a real tenant may not subscribe to a fixture provider — both asserted).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with execution.teardown / models.teardown /
replica.teardown / ledger.teardown = 'fixture' (the GL's documented fixture path: fn_guard_posted_lines passes a
DELETE only for an is_test org's entry), keyed by this run's fixture ids; then every counted table is re-read for its
exact pre-test count — audit_log and every table real activity writes to counted over FIXTURE rows only.

--self-checks-only: git / route self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and the
leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import base64
import csv
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
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep15"
BEFORE_REF = "6a83941"   # rep15-work's base: 'chore: refresh schema snapshot'
_C, _P = "/api/v1/model-catalog", "/api/v1/model-provider"
_VM, _MM, _OS, _SA = "view_models", "manage_models", "manage_org_settings", "super_admin"
ROUTES = {
    ("GET", _C): _VM,
    ("GET", f"{_C}/subscriptions"): _VM,
    ("GET", f"{_C}/fees"): _VM,
    ("POST", f"{_C}/{{catalog_model_id}}/acknowledge"): _OS,
    ("POST", f"{_C}/{{catalog_model_id}}/subscribe"): _OS,
    ("POST", f"{_C}/subscriptions/{{subscription_id}}/end"): _OS,
    ("POST", f"{_C}/subscriptions/{{subscription_id}}/adopt"): _MM,
    ("POST", f"{_C}/subscriptions/{{subscription_id}}/refresh"): _MM,
    ("POST", f"{_C}/subscriptions/{{subscription_id}}/copy"): _MM,
    ("GET", f"{_P}/subscribers"): _VM,
    ("GET", _P): _SA,
    ("PUT", f"{_P}/provider"): _SA,
    ("POST", f"{_P}/gate/open"): _SA,
    ("POST", f"{_P}/gate/close"): _SA,
    ("GET", f"{_P}/publishing"): _SA,
    ("POST", f"{_P}/models/{{model_id}}/publishable"): _SA,
    ("POST", f"{_P}/catalog-models/{{catalog_model_id}}/withdraw"): _SA,
    ("POST", f"{_P}/catalog-models/{{catalog_model_id}}/publish"): _SA,
    ("POST", f"{_P}/catalog-versions/{{catalog_version_id}}/decide"): _SA,
    ("POST", f"{_P}/catalog-versions/{{catalog_version_id}}/retire"): _SA,
    ("POST", f"{_P}/subscriptions/{{subscription_id}}/decide"): _SA,
    ("POST", f"{_P}/subscriptions/{{subscription_id}}/suspend"): _SA,
    ("POST", f"{_P}/subscriptions/{{subscription_id}}/resume"): _SA,
    ("POST", f"{_P}/subscriptions/{{subscription_id}}/end"): _SA,
    ("POST", f"{_P}/fees/accrue"): _SA,
    ("GET", f"{_P}/fees/invoices"): _SA,
    ("POST", f"{_P}/fees/invoices"): _SA,
    ("POST", f"{_P}/fees/invoices/{{invoice_id}}/correct"): _SA,
}
OWN_FILES = ("apps/api/services/model_provider.py", "apps/api/routers/model_provider.py")
REFUSAL = "model publishing is not enabled — a registered adviser entity is required"
PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"


def self_routes(check, find):
    """rep15's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "rep15 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate at rep15's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "rep15's routes all still exist today with the same gate", f"{drift[:4]}")


def self_code(check, find):
    """rep15's own code at its own end point: the gate's refusal text; the ADV and agreement from the library (no
    default text in code); the fee posted through the GL posting API and reversed by fn_reverse_journal_entry; no
    model / network client."""
    sv = VS.own_text(SPRINT_KEY, OWN_FILES[0]) or ""
    check(f'GATE_REFUSAL = "{REFUSAL}"' in sv, "the gate's refusal is one constant, worded as the spec states it")
    check('KEY_ADV = "model_provider.adv_2a"' in sv and 'KEY_AGREEMENT = "model_provider.subadvisory_agreement"' in sv
          and "DL.ack_state(" in sv and "DL.require_current(" in sv,
          "the ADV and the sub-advisory agreement come from the disclosure library, the agreement acknowledged by "
          "version (require_current)")
    check("build_entry_on_conn(" in sv and "post_on_conn(" in sv and "fn_reverse_journal_entry" in sv,
          "the fee posts through services.ledger.posting and is corrected by fn_reverse_journal_entry")
    for f in OWN_FILES:
        src = VS.own_text(SPRINT_KEY, f) or ""
        check(bool(src) and not re.search(r"call_claude|anthropic|litellm|httpx\.", src),
              f"{f}: no model call and no network client", f"{len(src)} chars")


def self_checks(check, find):
    self_routes(check, find)
    self_code(check, find)


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
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
from services import disclosures as DL  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import model_provider as PM  # noqa: E402
from services import org_settings as OS  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "rep15verify"
PREFIX = "ZZR15"
NAME_PREFIX = "REP15 VERIFY"
CUSTODIAN = "ZZR15C"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
PROVIDER_NAME = "REP15 Verify Advisers LLC"
ZERO = Decimal(0)


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001f{n:04d}")


FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000001f"
ORG_P, ORG_A, ORG_B = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_P, ORG_A, ORG_B]
U_SA1, U_SA2, U_SA3, U_PV, U_A1, U_A2, U_AMEM, U_B1, U_B2 = (_id(110 + i) for i in range(9))
# uid → (org, users.role, short name, role granted, full name)
USERS = {
    U_SA1: (ORG_P, "super_admin", "sa1", "super_admin", "REP15 Super Admin One"),
    U_SA2: (ORG_P, "super_admin", "sa2", "super_admin", "REP15 Super Admin Two"),
    U_SA3: (ORG_P, "super_admin", "sa3", "super_admin", "REP15 Super Admin Three"),
    U_PV: (ORG_P, "member", "pview", "rep15_platform_viewer", "REP15 Platform Viewer"),
    U_A1: (ORG_A, "org_admin", "a1", "org_admin", "REP15 Tenant A Maker"),
    U_A2: (ORG_A, "org_admin", "a2", "org_admin", "REP15 Tenant A Checker"),
    U_AMEM: (ORG_A, "member", "amem", "rep15_member", "REP15 Tenant A Member"),
    U_B1: (ORG_B, "org_admin", "b1", "org_admin", "REP15 Tenant B Maker"),
    U_B2: (ORG_B, "org_admin", "b2", "org_admin", "REP15 Tenant B Checker"),
}
SUPERS = (U_SA1, U_SA2, U_SA3)
ADMIN_PERMS = {"view_models", "manage_models", "approve_models", "manage_org_settings", "manage_proposals",
               "approve_proposals", "manage_disclosures"}
ENT_A, ENT_B = _id(200), _id(201)
ENTITIES = {ENT_A: ORG_A, ENT_B: ORG_B}
HH_A, HH_B = _id(210), _id(211)
HOUSEHOLDS = {HH_A: (ORG_A, ENT_A, "REP15 Verify Household A"), HH_B: (ORG_B, ENT_B, "REP15 Verify Household B")}
ACC_A1, ACC_A2, ACC_A3, ACC_A4, ACC_B1 = (_id(301 + i) for i in range(5))
ACCOUNTS = {ACC_A1: (ORG_A, ENT_A, HH_A), ACC_A2: (ORG_A, ENT_A, HH_A), ACC_A3: (ORG_A, ENT_A, HH_A),
            ACC_A4: (ORG_A, ENT_A, HH_A), ACC_B1: (ORG_B, ENT_B, HH_B)}
SEC_KEYS = ("S1", "S2", "S3", "S4")
SEC_NAMES = {k: f"{NAME_PREFIX} {k}" for k in SEC_KEYS}
PRICES = {k: "50" for k in SEC_KEYS}
# account → [(security, shares, price)]  — market value = shares × price
HOLDINGS = {
    ACC_A1: [("S1", 400, 50), ("S2", 400, 50), ("S3", 200, 50)],          # 50,000.00
    ACC_A2: [("S1", 1000, 50), ("S2", 1000, 50), ("S3", 500, 50)],        # 125,000.00
    ACC_A3: [("S1", 100, 50)],                                            # 5,000.00 (joins the COPY — no fee)
    ACC_A4: [("S1", 100, 50)],                                            # 5,000.00 (join refused while suspended)
    ACC_B1: [("S1", 800, 50), ("S2", 800, 50), ("S3", 400, 50)],          # 100,000.00
}
V1 = (("S1", 40), ("S2", 40), ("S3", 20))
V2 = (("S1", 30), ("S2", 40), ("S4", 30))
FEE_V1, FEE_V2 = Decimal("12.5"), Decimal("15")
RESTATE = Decimal("100000")     # 175,000 → 275,000: the corrected invoice differs in its cents (0.72 → 1.13)
REP15_TABLES = ("portfolio.model_providers", "portfolio.model_publishing_gates", "portfolio.catalog_models",
                "portfolio.catalog_versions", "portfolio.provider_subscriptions", "portfolio.provider_fee_accruals",
                "portfolio.provider_fee_invoices", "portfolio.provider_fee_invoice_lines",
                "portfolio.model_provider_events")
COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households",
    "public.household_memberships", "public.accounts", "public.account_balances_daily", "portfolio.assets",
    "portfolio.positions", "portfolio.tax_lots", "portfolio.securities_global",
    "portfolio.securities_global_identifiers", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.model_uploads", "portfolio.model_upload_events",
    "portfolio.model_events", "public.account_model_links", "public.rebalance_proposals",
    "public.rebalance_proposal_trades", "public.disclosures", "public.disclosure_versions",
    "public.disclosure_acknowledgements", "public.org_settings", "public.journal_entries", "public.journal_lines",
    "public.chart_of_accounts", "public.ledger_books", "public.posting_templates", *REP15_TABLES,
)
SEED_CHECK_LITERALS = (
    ("portfolio.securities_global", "securities_global_type_chk", ("equity",)),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("ticker",)),
    ("portfolio.positions", "positions_authority_chk", ("manual",)),
    ("portfolio.positions", "positions_source_chk", ("manual",)),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
    ("portfolio.tax_lots", "tax_lots_term_check", ("long",)),
    ("portfolio.models", "models_source_chk", ("upload", "provided", "provided_copy")),
    ("portfolio.model_providers", "model_providers_status_chk", ("registered", "not_registered")),
    ("portfolio.provider_subscriptions", "provider_subscriptions_status_chk", ("requested", "active", "suspended",
                                                                                "ended")),
    ("portfolio.catalog_versions", "catalog_versions_status_chk", ("pending", "published", "retired")),
    ("public.disclosure_acknowledgements", "disclosure_ack_subject_chk", ("model_subscription",)),
    ("public.disclosure_versions", "disclosure_versions_scope_chk", ("platform", "org")),
    ("public.journal_entries", "journal_entries_vehicle_kind_check", ("LEDGER_BOOK",)),
)
UNIQUES = ("model_providers_live_uq", "model_publishing_gates_live_uq", "catalog_models_model_live_uq",
           "catalog_versions_model_version_uq", "catalog_versions_no_uq", "catalog_versions_pending_uq",
           "provider_subscriptions_open_uq", "provider_fee_accruals_live_uq", "provider_fee_invoices_reversal_uq",
           "models_provided_live_uq", "coa_org_code_current", "ledger_books_code_uq", "pt_org_type_scope")
FK_TABLES = REP15_TABLES

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
S3_BUILT: list[str] = []
BLOBS: dict[str, bytes] = {}
PUT_LOG: list[str] = []
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


def total_line() -> str:
    return f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL"


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return None, exc.stdout or "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


def D(v) -> Decimal:
    return Decimal(str(v))


def cents(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def raw_of(acc) -> str:
    return f"ZZ15{int(str(acc)[-4:]):06d}"


def refused(code, body) -> bool:
    """A refusal BY THE GATE: 409 whose detail carries the spec's reason. rep15 fix 1: read the DECODED detail —
    ``dumps`` escapes the reason's em dash to \\u2014 (ensure_ascii), so ``REFUSAL in dumps(body)`` never matched."""
    detail = (body or {}).get("detail") if isinstance(body, dict) else None
    return code == 409 and isinstance(detail, str) and REFUSAL in detail


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """The org context a route sets (org GUC, not super admin), ON the connection inside its transaction."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def db_today(conn) -> date:
    return await conn.fetchval("SELECT current_date")


def _arr(vals, typ="uuid") -> str:
    return "ARRAY[" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals) + f"]::{typ}[]"


def fixture_scope(table: str) -> str:
    """The fixture-row predicate of EVERY counted table (live activity may write any of them)."""
    orgs, users = _arr(FIXTURE_ORGS), _arr(USERS)
    names = _arr(SEC_NAMES.values(), "text")
    secs = f"(SELECT id FROM portfolio.securities_global WHERE name = ANY({names}))"
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR resource_id::text LIKE "
                            f"'{FIXTURE_ID_PREFIX}%'",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.organizations": f"id = ANY({orgs})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.household_memberships": f"household_id = ANY({_arr(HOUSEHOLDS)})",
        "portfolio.securities_global": f"name = ANY({names})",
        "portfolio.securities_global_identifiers": f"global_security_id IN {secs}",
        "public.disclosures": f"created_by = ANY({users})",
        "public.disclosure_versions": f"org_id = ANY({orgs}) OR drafted_by = ANY({users})",
        "public.journal_lines": f"entry_id IN (SELECT id FROM public.journal_entries WHERE {by_org})",
        "portfolio.model_providers": f"provider_org_id = ANY({orgs})",
        "portfolio.model_publishing_gates": f"provider_org_id = ANY({orgs})",
        "portfolio.catalog_models": f"provider_org_id = ANY({orgs})",
        "portfolio.catalog_versions": f"provider_org_id = ANY({orgs})",
        "portfolio.provider_fee_accruals": f"provider_org_id = ANY({orgs})",
        "portfolio.provider_fee_invoices": f"provider_org_id = ANY({orgs})",
        "portfolio.provider_fee_invoice_lines": f"provider_org_id = ANY({orgs})",
    }.get(table, by_org)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
            for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint must never touch: every rep15 table outside the fixture orgs (nothing real writes them
    — the real gate is OFF); the real platform org's gate; disclosure versions / catalog rows outside the fixture;
    2nd Act's chart of accounts, books and posting templates (structural — the fee's ledger is provisioned in the
    fixture provider org only); the demo org's accounts."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    users = [str(u) for u in USERS]
    out = {}
    for t in REP15_TABLES:
        col = "org_id" if t in ("portfolio.provider_subscriptions", "portfolio.model_provider_events") \
            else "provider_org_id"
        out[t.split(".")[1]] = await fingerprint(conn, f"SELECT * FROM {t} WHERE NOT ({col} = ANY($1::uuid[]))", orgs)
    out["real_platform_gate"] = await fingerprint(
        conn, "SELECT * FROM portfolio.model_publishing_gates WHERE provider_org_id = $1::uuid", PLATFORM_ORG_ID)
    out["disclosure_versions"] = await fingerprint(
        conn, "SELECT * FROM public.disclosure_versions WHERE (org_id IS NULL OR NOT (org_id = ANY($1::uuid[]))) "
              "AND NOT (drafted_by = ANY($2::uuid[]))", orgs, users)
    out["disclosures"] = await fingerprint(conn, "SELECT * FROM public.disclosures WHERE created_by IS NULL OR NOT "
                                                 "(created_by = ANY($1::uuid[]))", users)
    for t in ("public.chart_of_accounts", "public.ledger_books", "public.posting_templates"):
        out[f"default_org_{t.split('.')[1]}"] = await fingerprint(
            conn, f"SELECT * FROM {t} WHERE org_id = $1::uuid", DEFAULT_ORG_ID)
    demo = await platform(conn, "SELECT id FROM organizations WHERE slug = 'demo-olivo'", fetch="fetchval")
    S["demo_org"] = demo
    if demo is not None:
        out["demo_olivo_accounts"] = await fingerprint(
            conn, "SELECT id, custodian_code, household_id, is_held_away, tax_status FROM public.accounts WHERE "
                  "org_id = $1", demo)
        out["demo_olivo_models"] = await fingerprint(conn, "SELECT id, source, status FROM portfolio.models WHERE "
                                                           "org_id = $1", demo)
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
    """Child-first by the shared helper; every platform key MATERIALISED first (the security ids) — never a subquery
    over a table the same teardown deletes from."""
    op = await operator_conn()
    try:
        secs = [r["id"] for r in await op.fetch("SELECT id FROM portfolio.securities_global WHERE name LIKE $1",
                                                f"{NAME_PREFIX}%")]
        secs += [r["global_security_id"] for r in await op.fetch(
            "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
            f"{PREFIX}%") if r["global_security_id"] not in secs]
        users = list(USERS)
        plat_versions = [r["id"] for r in await op.fetch(
            "SELECT id FROM public.disclosure_versions WHERE org_id IS NULL AND drafted_by = ANY($1::uuid[])", users)]
        catalog_rows = [r["id"] for r in await op.fetch(
            "SELECT d.id FROM public.disclosures d WHERE d.created_by = ANY($1::uuid[]) AND NOT EXISTS (SELECT 1 FROM "
            "public.disclosure_versions v WHERE v.disclosure_id = d.id AND NOT (v.id = ANY($2::uuid[])) AND "
            "(v.org_id IS NULL OR NOT (v.org_id = ANY($3::uuid[]))))", users, plat_versions, FIXTURE_ORGS)]
        orgs = FIXTURE_ORGS
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [list(HOUSEHOLDS)]),
            "public.disclosure_acknowledgements": ("org_id = ANY($1::uuid[]) OR version_id = ANY($2::uuid[])",
                                                   [orgs, plat_versions]),
            "public.disclosure_versions": ("id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])", [plat_versions, orgs]),
            "public.disclosures": ("id = ANY($1::uuid[])", [catalog_rows]),
            "portfolio.model_providers": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.model_publishing_gates": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.catalog_models": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.catalog_versions": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.provider_fee_accruals": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.provider_fee_invoices": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.provider_fee_invoice_lines": ("provider_org_id = ANY($1::uuid[])", [orgs]),
            "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
            "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown", "ledger.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_rep15")
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
    users = list(USERS)
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [users, f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
        ("SELECT count(*) FROM public.disclosure_versions WHERE drafted_by = ANY($1::uuid[])", [users]),
        ("SELECT count(*) FROM public.disclosures WHERE created_by = ANY($1::uuid[]) AND NOT EXISTS (SELECT 1 FROM "
         "public.disclosure_versions v WHERE v.disclosure_id = disclosures.id)", [users]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    for t in REP15_TABLES:
        col = "org_id" if t in ("portfolio.provider_subscriptions", "portfolio.model_provider_events") \
            else "provider_org_id"
        n += int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {col} = ANY($1::uuid[])", FIXTURE_ORGS,
                                fetch="fetchval"))
    op = await operator_conn()
    try:
        for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
                "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
                "'information_schema')"):
            n += int(await op.fetchval(f"SELECT count(*) FROM {r['t']} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
        n += int(await op.fetchval("SELECT count(*) FROM public.journal_lines jl JOIN public.journal_entries je ON "
                                   "je.id = jl.entry_id WHERE je.org_id = ANY($1::uuid[])", FIXTURE_ORGS))
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


async def api_raw(uid, method, path, body=None, files=None, form=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep15"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
        kw["data"] = form or {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b""
    return res.status_code, res.content


async def api(uid, method, path, body=None, timeout=300.0):
    code, content = await api_raw(uid, method, path, body, timeout=timeout)
    try:
        return code, json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        return code, {"raw": content[:2000].decode("utf-8", "replace")}


class _ctx:
    """The task's RLS contextvars. ENTER IT BEFORE ``pool.acquire()`` — the wrapped pool applies the GUCs once, at
    acquire."""

    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile
    up = list(inspect.signature(storage.upload_bytes).parameters)
    down = list(inspect.signature(storage.download_bytes).parameters)
    if up != ["key", "data", "content_type", "bucket"] or down != ["key", "bucket"]:
        raise RuntimeError(f"storage signatures drifted: upload_bytes{up} download_bytes{down}")

    def fake_upload(key, data, content_type=None, bucket=None):
        if key in BLOBS:
            raise AssertionError(f"fake R2: an existing object was about to be overwritten: {key}")
        BLOBS[key] = bytes(data)
        PUT_LOG.append(key)
        return key

    def fake_download(key, bucket=None):
        return BLOBS[key]

    def no_client(*a, **k):
        S3_BUILT.append("get_s3_client")
        raise AssertionError("verify: a real R2 client must never be built")

    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_s3_client = no_client

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep15: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_rep15: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))

    have = list(inspect.signature(RP._unheld_prices).parameters)
    if have[:3] != ["conn", "keys", "since"] or "org_id" not in have or "today" not in have:
        raise RuntimeError(f"_unheld_prices signature drifted: {have}")

    async def unheld_prices(conn, keys, since, *, org_id, today=None, provenance=None):
        out = {}
        for k in keys:
            key = S.get("key_of", {}).get(str(k))
            if key and key in PRICES:
                out[str(k)] = (D(PRICES[key]).quantize(Decimal("0.00000001")), today or date.today(),
                               "security_price")
        return out

    RP._unheld_prices = unheld_prices


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1d (docs/REP15_DISCOVERY.md)")
    p = REPO / "docs" / "REP15_DISCOVERY.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    heads = re.findall(r"^## (1[a-d])", txt, re.M)
    check(set(heads) >= {"1a", "1b", "1c", "1d"}, "the discovery doc has a section for each of 1a–1d",
          f"{p.exists()} {sorted(set(heads))}")
    n = await platform(conn, "SELECT count(*) FROM portfolio.model_library_entries WHERE shelf = 'platform' AND "
                             "status = 'published' AND system_to IS NULL", fetch="fetchval")
    find("1a live published PLATFORM-shelf library entries (rep3's ungated path, now gated for real orgs)", f"{n}")
    coa = await platform(conn, "SELECT count(*) FROM chart_of_accounts WHERE org_id = $1::uuid AND system_to IS NULL",
                         PLATFORM_ORG_ID, fetch="fetchval")
    find("1c the real Hollisworks platform org's chart of accounts (the fee's ledger is provisioned when the gate "
         "opens)", f"{coa} accounts")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation, the connection does not bypass RLS")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep15.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep15.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-300:] if code else ''}")
    for t in REP15_TABLES:
        sch, name = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", sch, name)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role is not None and role["rolbypassrls"] is False,
          "the verify's own connection does NOT bypass RLS (isolation checks are real)", f"{dict(role or {})}")


async def y_seed_literals(conn):
    section("[Y] CHECK / UNIQUE / FK constraints and unique indexes read from the catalog before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype::text = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    uq = {r["indexname"] for r in await platform(conn, "SELECT indexname FROM pg_indexes WHERE indexname = "
                                                       "ANY($1::text[]) AND indexdef LIKE 'CREATE UNIQUE%'",
                                                 list(UNIQUES))}
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype::text = 'f' AND conrelid = "
                                   "ANY($1::regclass[])", list(FK_TABLES), fetch="fetchval"))
    check(len(SEED_CHECK_LITERALS) > 0 and not bad and uq == set(UNIQUES) and fks >= 25,
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints read); the "
          f"{len(UNIQUES)} UNIQUE indexes and the FKs the seeds rely on are in the catalog",
          "; ".join(bad) + f" uq_missing={sorted(set(UNIQUES) - uq)} fks={fks}")
    S["literals_ok"] = not bad and uq == set(UNIQUES)


async def guard_prefix(conn) -> bool:
    vals = []
    for sql, arg in (("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", f"{PREFIX}%"),
                     ("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE $1", f"{NAME_PREFIX}%"),
                     ("SELECT count(*) FROM public.accounts WHERE upper(custodian_code) LIKE $1", f"{PREFIX}%"),
                     ("SELECT count(*) FROM portfolio.models WHERE name LIKE $1", "%REP15 Verify%"),
                     ("SELECT count(*) FROM portfolio.catalog_versions WHERE name LIKE $1", "%REP15 Verify%")):
        vals.append(int(await platform(conn, sql, arg, fetch="fetchval")))
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    return check(all(v == 0 for v in vals) and o == 0 and u == 0,
                 f"the reserved prefixes ({PREFIX}, '{NAME_PREFIX}', 'REP15 Verify' model names, custodian "
                 f"{CUSTODIAN}, the fixture id range) match no real row", f"{vals} orgs={o} users={u}")


async def check_fixture_roles(conn) -> bool:
    """Each fixture user's role AS THE APP DECIDES IT (user_roles → services.authz Principal), users.role its mirror."""
    from services import authz as _AZ

    bad = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, account_role, _nm, rname, _full) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            lp = await rbac.load_principal(conn, uid)
            pr = await _AZ.load_principal(conn, uid)
            want_super = rname == "super_admin"
            perms = set(getattr(pr, "permissions", ()) or ())
            ok = (rname in names and rbac.is_super_admin(lp) is want_super
                  and bool(pr and pr.is_super_admin) is want_super)
            if rname in ("super_admin", "org_admin"):
                ok = ok and rbac.mirrored_role_string(names) == account_role
            if rname == "org_admin":
                ok = ok and ADMIN_PERMS <= perms
            if rname == "rep15_platform_viewer":
                ok = ok and perms == {"view_models"}
            if rname == "rep15_member":
                ok = ok and not ({"view_models", "manage_models", "manage_org_settings"} & perms)
            if not ok:
                bad.append((str(uid), names, account_role, bool(pr and pr.is_super_admin), sorted(perms)[:12]))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (three Super Admins; "
                 "org admins with the model / proposal / settings / disclosure permissions; the platform viewer "
                 "view_models only; the member none of them)", f"{bad}")


async def seed(conn) -> None:
    S["seed_day"] = await db_today(conn)
    S["sec"], S["key_of"], S["asset"], S["pos"] = {}, {}, {}, {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "p"), (ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP15 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid = await rbac.ensure_role(conn, ORG_P, "rep15_platform_viewer", "REP15 verify — view_models only")
        await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                           rid, perm_ids["view_models"])
        await rbac.ensure_role(conn, ORG_A, "rep15_member", "REP15 verify — a member (no permission)")
        for uid, (org, account_role, _nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await rbac.grant_named_role(conn, uid, org, rname)
        for ent, org in ENTITIES.items():
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual'::entity_type, $3)", ent, org, f"REP15 Verify Member {str(ent)[-3:]}")
        for hh, (org, ent, name) in HOUSEHOLDS.items():
            maker = U_B1 if org == ORG_B else U_A1
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)",
                               hh, org, name, maker)
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, "
                               "$3)", hh, ent, maker)
        for key in SEC_KEYS:
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type="equity", currency_code="USD",
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=f"{PREFIX}{key}",
                                    is_super_admin=True)
            S["sec"][key] = UUID(str(sid))
            S["key_of"][str(sid)] = key
        salts = {}
        for org in (ORG_A, ORG_B):
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for acc, (org, ent, hh) in ACCOUNTS.items():
            n = AccountNumber(raw_of(acc))
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, true, false)",
                acc, org, n.masked, n.hashed(salts[org]), CUSTODIAN, ent, hh)
            await conn.execute(
                "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, "
                "cash_value, margin_balance, accrued_income, source_system, source_confidence, is_billing_source, "
                "is_final) VALUES ($1, $2, $3, 0, 0, 0, 0, 'manual', 'CONFIRMED', true, true)", org, acc,
                S["seed_day"])
            for key, qty, price in HOLDINGS[acc]:
                aid = S["asset"].get((org, key))
                if aid is None:
                    aid = S["asset"][(org, key)] = uuid.uuid4()
                    await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                                       "VALUES ($1, $2, $3, $4, 'equity')", aid, org, S["sec"][key], SEC_NAMES[key])
                pid = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                    "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, "
                    "'manual', 'manual', $8)", pid, org, ent, aid, S["seed_day"], D(qty), D(qty) * D(price), acc)
                S["pos"][(acc, key)] = pid
                await conn.execute(
                    "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                    "original_cost, term, status) VALUES ($1, $2, $3, $4, $5, $6, $6, 'long', 'open')", uuid.uuid4(),
                    org, pid, S["seed_day"] - timedelta(days=800), D(qty), D(qty) * D(price) * D("0.6"))
        S["masked"] = {a: AccountNumber(raw_of(a)).masked for a in ACCOUNTS}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          list(USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs (one provider, two tenants) and users are "
          "is_test = true", f"{len(us)}/{len(USERS)} users, {len(os_)}/3 orgs")
    S["seeded"] = await check_fixture_roles(conn) and us == set(USERS) and os_ == set(FIXTURE_ORGS)


def csv_bytes(rows, salt: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Ticker", "Weight %", "Notes"])
    for key, wt in rows:
        w.writerow([f"{PREFIX}{key}", wt, salt])          # distinct content per upload (the upload log dedupes bytes)
    return buf.getvalue().encode("utf-8")


async def provider_version(rows, *, model_id=None, salt="v1"):
    """rep3 in the FIXTURE provider org: upload (SA1 — the maker), submit (SA1), approve (SA2 — the checker)."""
    code, content = await api_raw(U_SA1, "POST", "/api/v1/models/uploads",
                                  files={"file": (f"rep15_{salt}.csv", csv_bytes(rows, salt), "text/csv")},
                                  form={"model_id": str(model_id)} if model_id else {})
    try:
        up = json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        up = {}
    uid = (up.get("upload") or {}).get("id")
    body = {"model_id": str(model_id)} if model_id else {"model_name": "REP15 Verify Provider Model"}
    c2, o2 = (await api(U_SA1, "POST", f"/api/v1/models/uploads/{uid}/confirm", body)) if uid else (code, up)
    m = (o2 or {}).get("model") or {}
    v = (o2 or {}).get("version") or {}
    c3, o3 = await api(U_SA1, "POST", f"/api/v1/models/{m.get('id')}/versions/{v.get('id')}/submit", {})
    c4, o4 = await api(U_SA2, "POST", f"/api/v1/models/{m.get('id')}/versions/{v.get('id')}/approve", {})
    return {"codes": (code, c2, c3, c4), "model": m.get("id"), "version": v.get("id"), "upload": up.get("upload"),
            "detail": f"{dumps(o2)} {dumps(o3)} {dumps(o4)}"}


async def y_provider_model(conn):
    section("[Y] The fixture provider's own rep3 model (approved by a second Super Admin)")
    r = await provider_version(V1, salt="v1")
    ok = check(r["codes"] == (201, 201, 200, 200) and bool(r["model"]),
               "the provider model v1 uploads (SA1) and is approved by a different Super Admin (SA2)",
               f"{r['codes']} {r['detail']}")
    S["pmodel"], S["pv1"], S["upload1"] = r["model"], r["version"], r["upload"] or {}
    S["provider_model_ok"] = ok


async def y_gate_off(conn):
    section("[Y] Gate OFF refuses every publish / subscribe / adopt / fee write with the reason (valid bodies)")
    c, o = await api(U_SA1, "GET", _P)
    check(c == 200 and (o.get("gate") or {}).get("is_open") is False and o.get("ready_to_open") is False
          and o.get("refusal") == REFUSAL, "the fixture provider's gate starts OFF (no row) and names the refusal",
          f"{c} {dumps(o.get('gate'))}")
    tests = [
        ("mark a model publishable", "POST", f"{_P}/models/{S['pmodel']}/publishable",
         {"default_fee_bps": "12.5", "construction_notes": "REP15 verify"}),
        ("request publication", "POST", f"{_P}/catalog-models/{uuid.uuid4()}/publish",
         {"version_id": S["pv1"], "name": "Holdings of REP15 Verify Index", "description":
          "Replicates the holdings of the REP15 Verify Index.", "construction_notes": "Equal sector weights.",
          "holdings_as_of": S["seed_day"].isoformat()}),
        ("accrue the fee", "POST", f"{_P}/fees/accrue", {"day_from": S["seed_day"].isoformat(),
                                                         "day_to": S["seed_day"].isoformat()}),
        ("invoice the fee", "POST", f"{_P}/fees/invoices", {"subscription_id": str(uuid.uuid4()),
                                                            "period_start": S["seed_day"].isoformat(),
                                                            "period_end": S["seed_day"].isoformat()}),
    ]
    for label, m, path, body in tests:
        c, o = await api(U_SA1, m, path, body)
        check(refused(c, o), f"gate OFF: {label} → 409 with the reason", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "GET", _C)
    check(c == 200 and o.get("catalog") == [] and (o.get("vocabularies") or {}).get("refusal") == REFUSAL,
          "gate OFF: the tenant's catalog is empty and its envelope carries the refusal", f"{c} {dumps(o)[:400]}")
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c2:
            plat = await PM.platform_shelf_refusal(c2, org_id=DEFAULT_ORG_ID, actor_id=U_A1, publishing=False)
            fix = await PM.platform_shelf_refusal(c2, org_id=ORG_A, actor_id=U_A1, publishing=False)
    check(plat == REFUSAL and fix is None, "rep3's platform library shelf: a REAL org loading from it is refused "
          "while the real gate is OFF; a fixture org is not (read-only check)", f"{plat!r} {fix!r}")


async def upload_disclosure(uid, key, scope, n, *, eff=None, summary=None):
    data = f"# {key} — REP15 verify {scope} version {n}\n\nDistinct content {n} {uuid.uuid4()}\n".encode()
    body = {"scope": scope, "file_name": f"rep15_{key.split('.')[-1]}_{scope}_v{n}.md",
            "content_base64": base64.b64encode(data).decode(),
            "summary": summary or f"REP15 verify {key} {scope} version {n}",
            "effective_from": (eff or S["seed_day"]).isoformat()}
    c, o = await api(uid, "POST", f"/api/v1/disclosures/keys/{key}/versions", body)
    return c, ((o or {}).get("version") or {}).get("id"), o


async def publish_disclosure(uid, vid):
    return await api(uid, "POST", f"/api/v1/disclosures/versions/{vid}/publish", {})


async def y_gate_open(conn):
    section("[Y] Opening needs Super Admin, a complete provider record and a published ADV; every change audited")
    c, o = await api(U_SA1, "POST", f"{_P}/gate/open", {"reason": "REP15 verify — try with nothing on file"})
    check(refused(c, o) and "legal name" in dumps(o) and "ADV" in dumps(o),
          "no provider record: opening is refused, each missing item named", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"{_P}/gate/open", {"reason": "REP15 verify — a tenant admin tries"})
    check(c == 403, "an org admin (not Super Admin) cannot open the gate (403)", f"{c} {dumps(o)}")
    c, o = await api(U_SA1, "PUT", f"{_P}/provider", {"legal_name": PROVIDER_NAME, "sec_file_number": "801-99999",
                                                      "registration_status": "registered",
                                                      "effective_date": (S["seed_day"] - timedelta(days=10)).isoformat()})
    check(c == 200 and o.get("registration_status") == "registered" and o.get("ledger_org_id") == str(ORG_P),
          "Super Admin records the registered adviser entity (ledger org = the provider org by default)",
          f"{c} {dumps(o)}")
    c, o = await api(U_SA1, "POST", f"{_P}/gate/open", {"reason": "REP15 verify — record but no ADV"})
    check(refused(c, o) and "ADV" in dumps(o) and "legal name" not in dumps(o),
          "a complete record without a published ADV: still refused, naming only the ADV", f"{c} {dumps(o)}")
    # the database refuses it too (rolled back): an open gate row with no ADV, inserted under the super-admin GUC
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            pr = await conn.fetchval("SELECT id FROM portfolio.model_providers WHERE provider_org_id = $1", ORG_P)
            await conn.execute("INSERT INTO portfolio.model_publishing_gates (provider_org_id, is_open, "
                               "provider_record_id, adv_version_id, adv_sha256, changed_by, reason) VALUES ($1, true, "
                               "$2, gen_random_uuid(), repeat('0', 64), $3, 'direct')", ORG_P, pr, U_SA1)
            db_refused = False
            raise RuntimeError("rollback")
    except asyncpg.PostgresError as exc:
        db_refused = "not enabled" in str(exc) or isinstance(exc, asyncpg.ForeignKeyViolationError)
    except RuntimeError:
        db_refused = False
    check(db_refused, "the DATABASE refuses an open gate without a published ADV (trigger / FK), even as Super Admin")
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await conn.execute("INSERT INTO portfolio.model_providers (provider_org_id, legal_name, ledger_org_id, "
                               "created_by, updated_by) VALUES ($1::uuid, 'x', $1::uuid, $2, $2)", DEFAULT_ORG_ID, U_SA1)
            real_refused = False
            raise RuntimeError("rollback")
    except asyncpg.PostgresError as exc:
        real_refused = "platform org" in str(exc)
    except RuntimeError:
        real_refused = False
    check(real_refused, "a REAL client org can never be a model provider (trigger refuses; rolled back)")
    ok = await platform(conn, "SELECT portfolio.provider_tenant_allowed($1::uuid, $2::uuid)", ORG_P, DEFAULT_ORG_ID,
                        fetch="fetchval")
    check(ok is False, "a fixture provider can never serve a REAL tenant (is_test parity)", f"{ok}")
    c1, vid, o1 = await upload_disclosure(U_SA1, PM.KEY_ADV, "org", 1, summary="REP15 verify ADV Part 2A")
    c2, o2 = await publish_disclosure(U_SA2, vid) if vid else (c1, o1)
    check(c1 == 201 and c2 == 200, "the ADV Part 2A is uploaded at the provider org's scope (SA1) and published by "
          "a second Super Admin (SA2)", f"{c1} {c2} {dumps(o1)} {dumps(o2)}")
    S["adv_version"] = vid
    c, o = await api(U_SA1, "POST", f"{_P}/gate/open", {"reason": "REP15 verify — the entity and its ADV are on file"})
    ok = check(c == 200 and o.get("is_open") is True and o.get("adv_version_id") == vid,
               "Super Admin opens the FIXTURE provider's gate with the record and the published ADV",
               f"{c} {dumps(o)}")
    S["gate_open"] = ok
    c, o = await api(U_SA1, "GET", _P)
    check(c == 200 and o.get("ready_to_open") is True and all(i["met"] for i in o.get("readiness") or [{}])
          and bool(o.get("readiness")), "readiness: every item met", f"{dumps(o.get('readiness'))}")
    ev = [r["action"] for r in await scoped(conn, ORG_P, "SELECT action FROM portfolio.model_provider_events WHERE "
                                                         "org_id = $1 ORDER BY created_at", ORG_P)]
    au = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action LIKE "
                                  "'model_provider.gate_%'", ORG_P, fetch="fetchval"))
    check(ev.count("gate_refused") == 2 and "provider_saved" in ev and ev[-1] == "gate_opened" and au == 3,
          "every gate change and refusal is logged (events + audit_log)", f"{ev} audit={au}")
    ledger = await platform(conn, "SELECT (SELECT count(*) FROM ledger_books WHERE org_id = $1 AND book_code = "
                                  "'MODEL_PROVIDER') AS b, (SELECT count(*) FROM chart_of_accounts WHERE org_id = $1 "
                                  "AND code IN ('1230', '4800')) AS a, (SELECT count(*) FROM posting_templates WHERE "
                                  "org_id = $1 AND transaction_type_code = 'MODEL_PROVIDER_FEE') AS t", ORG_P,
                            fetch="fetchrow")
    check(dict(ledger) == {"b": 1, "a": 2, "t": 1}, "opening provisions the fee's ledger in the provider record's "
          "ledger org (book, receivable 1230, revenue 4800, template)", dumps(dict(ledger)))
    real = await platform(conn, "SELECT portfolio.model_publishing_open($1::uuid)", PLATFORM_ORG_ID, fetch="fetchval")
    check(real is False, "the REAL platform org's gate is still OFF", f"{real}")


async def y_publish(conn):
    section("[Y] Publish: maker-checker (never the requester nor a maker of the version), immutable, naming")
    c, o = await api(U_SA1, "POST", f"{_P}/models/{S['pmodel']}/publishable",
                     {"default_fee_bps": str(FEE_V1), "construction_notes": "REP15 verify — sector neutral"})
    S["cm"] = (o or {}).get("id")
    check(c == 201 and bool(S["cm"]), "the provider model is marked publishable", f"{c} {dumps(o)}")
    c, o = await api(U_SA1, "POST", f"{_P}/models/{S['pmodel']}/publishable", {"default_fee_bps": 12.5})
    check(c == 422, "a JSON float fee is refused (decimal strings only)", f"{c}")
    good = {"version_id": S["pv1"], "name": "Holdings of REP15 Verify Index",
            "description": "Replicates the holdings of the REP15 Verify Index.", "construction_notes":
            "Target weights of the index constituents, rebalanced quarterly.", "holdings_as_of":
            (S["seed_day"] - timedelta(days=2)).isoformat(), "fee_bps": str(FEE_V1)}
    for label, patch in (("a name that is the index's own name", {"name": "REP15 Verify Index"}),
                         ("a description without 'replicates the holdings of'", {"description": "A model portfolio."})):
        c, o = await api(U_SA3, "POST", f"{_P}/catalog-models/{S['cm']}/publish", {**good, **patch})
        check(c == 422, f"naming enforced: {label} → 422", f"{c} {dumps(o)}")
    c, o = await api(U_SA3, "POST", f"{_P}/catalog-models/{S['cm']}/publish", good)
    S["cv1"] = (o or {}).get("id")
    check(c == 201 and o.get("status") == "pending" and len(o.get("weights") or []) == 3,
          "SA3 requests publication of the APPROVED v1 (pending, the weights snapshot frozen)", f"{c} {dumps(o)}")
    for who, label in ((U_SA3, "the requester"), (U_SA1, "a maker of the rep3 version")):
        c, o = await api(who, "POST", f"{_P}/catalog-versions/{S['cv1']}/decide", {"outcome": "approve"})
        check(c == 403, f"{label} cannot publish it (403)", f"{c} {dumps(o)}")
    c, o = await api(U_SA2, "POST", f"{_P}/catalog-versions/{S['cv1']}/decide", {"outcome": "approve",
                                                                                "note": "REP15 verify"})
    ok = check(c == 200 and o.get("status") == "published" and o.get("checker_id") == str(U_SA2),
               "a different Super Admin (SA2) publishes it", f"{c} {dumps(o)}")
    S["published"] = ok
    w = await platform(conn, "SELECT weights FROM portfolio.catalog_versions WHERE id = $1", S["cv1"],
                       fetch="fetchval")
    w = json.loads(w) if isinstance(w, str) else (w or [])
    got = {S["key_of"].get(r["global_security_id"]): D(r["weight"]) for r in w}
    check(got == {k: D(p) / 100 for k, p in V1}, "the published weights are exactly the model's target weights",
          f"{got}")
    for col, val in (("weights", "'[]'::jsonb"), ("fee_bps", "1"), ("status", "'pending'")):
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(f"UPDATE portfolio.catalog_versions SET {col} = {val} WHERE id = $1", S["cv1"])
                imm = False
                raise RuntimeError("rollback")
        except asyncpg.PostgresError as exc:
            imm = "immutable" in str(exc) or "illegal" in str(exc)
        except RuntimeError:
            imm = False
        check(imm, f"a published catalog version is immutable in the database ({col} refused)")


async def y_subscribe(conn):
    section("[Y] Subscribe: the in-force agreement acknowledged BY VERSION (tenant override wins) + platform approval")
    real_default = await platform(conn, "SELECT v.id FROM public.disclosure_versions v JOIN public.disclosures d ON "
                                        "d.id = v.disclosure_id WHERE d.key = $1 AND v.scope = 'platform' AND "
                                        "v.status = 'published' AND v.system_to IS NULL LIMIT 1", PM.KEY_AGREEMENT,
                                  fetch="fetchval")
    if real_default is None:
        c1, pv, o1 = await upload_disclosure(U_SA1, PM.KEY_AGREEMENT, "platform", 1,
                                             summary="REP15 verify sub-advisory agreement — platform default")
        c2, o2 = await publish_disclosure(U_SA2, pv) if pv else (c1, o1)
        check(c1 == 201 and c2 == 200, "a FIXTURE platform default of the agreement (no real one exists) is drafted "
              "by SA1 and published by SA2", f"{c1} {c2} {dumps(o2)}")
        S["agreement_default"] = pv
    else:
        find("a real platform default of model_provider.subadvisory_agreement exists — used READ-ONLY",
             str(real_default))
        S["agreement_default"] = str(real_default)
    c1, bv, o1 = await upload_disclosure(U_B1, PM.KEY_AGREEMENT, "org", 1,
                                         summary="REP15 verify agreement — tenant B's own")
    c2, o2 = await publish_disclosure(U_B2, bv) if bv else (c1, o1)
    check(c1 == 201 and c2 == 200, "tenant B's own agreement version (B1 drafts, B2 publishes)", f"{c1} {c2} {dumps(o2)}")
    S["agreement_b1"] = bv
    ca, oa = await api(U_A1, "GET", _C)
    cb, ob = await api(U_B1, "GET", _C)
    ia = next((x for x in oa.get("catalog") or [] if x["catalog_model_id"] == S["cm"]), {})
    ib = next((x for x in ob.get("catalog") or [] if x["catalog_model_id"] == S["cm"]), {})
    check(((ia.get("agreement") or {}).get("agreement") or {}).get("version_id") == S["agreement_default"]
          and (ia.get("agreement") or {}).get("scope") == "platform"
          and ((ib.get("agreement") or {}).get("agreement") or {}).get("version_id") == bv
          and (ib.get("agreement") or {}).get("scope") == "org",
          "resolved PER TENANT: A gets the platform default, B its own (the tenant-specific version overrides)",
          f"{dumps(ia.get('agreement'))} {dumps(ib.get('agreement'))}")
    leak = [k for k in ("model_id", "model_version_id", "weights", "requested_by", "upload_id")
            if any(k in v for v in (ia.get("versions") or [{}]))]
    files = [dict(r) for r in await platform(conn, "SELECT id, sha256, r2_key, file_name FROM portfolio.model_uploads "
                                                   "WHERE org_id = $1", ORG_P)]
    S["provider_files"] = [str(x) for f in files for x in (f["id"], f["sha256"], f["r2_key"], f["file_name"]) if x]
    body = dumps(oa) + dumps(ob)
    hits = [x for x in S["provider_files"] if x in body]
    check(ca == 200 and bool(ia) and not leak and bool(files) and not hits
          and str(S["pmodel"]) not in body and str(S["pv1"]) not in body,
          "the tenant's catalog carries no provider model / version id, no weights, and nothing of the provider's "
          f"holdings files ({len(files)} upload(s): id, hash, R2 key, file name)", f"{leak} {hits[:3]} {dumps(ia)[:300]}")
    c, o = await api(U_A1, "POST", f"{_C}/{S['cm']}/subscribe")
    check(c == 409 and "acknowledge" in dumps(o).lower(), "A: subscribing WITHOUT acknowledging the agreement → 409",
          f"{c} {dumps(o)}")
    c, o = await api(U_B1, "POST", f"{_C}/{S['cm']}/acknowledge", {"version_id": S["agreement_default"]})
    check(c == 409, "B acknowledging the PLATFORM default (not B's version in force) → 409", f"{c} {dumps(o)}")
    for uid, vid in ((U_A1, S["agreement_default"]), (U_B1, bv)):
        c, o = await api(uid, "POST", f"{_C}/{S['cm']}/acknowledge", {"version_id": vid})
        check(c == 200 and o.get("version_id") == vid, f"{'A' if uid == U_A1 else 'B'} acknowledges its version in "
              "force", f"{c} {dumps(o)}")
    c, o = await api(U_AMEM, "POST", f"{_C}/{S['cm']}/subscribe")
    check(c == 403, "a member of tenant A cannot request a subscription (403)", f"{c}")
    c, o = await api(U_A1, "POST", f"{_C}/{S['cm']}/subscribe")
    S["sub_a"] = (o or {}).get("id")
    check(c == 201 and o.get("status") == "requested" and o.get("agreement_scope") == "platform"
          and o.get("agreement_version_id") == S["agreement_default"], "A requests (status requested, its "
          "acknowledged agreement version recorded)", f"{c} {dumps(o)}")
    c, o = await api(U_B1, "POST", f"{_C}/{S['cm']}/subscribe")
    S["sub_b_first"] = (o or {}).get("id")
    check(c == 201 and o.get("agreement_scope") == "org", "B requests (its own agreement recorded)", f"{c} {dumps(o)}")
    task = await platform(conn, "SELECT rs.id, r.org_id FROM workflow_run_steps rs JOIN workflow_runs r ON r.id = "
                                "rs.workflow_run_id WHERE r.context->'human_task'->>'subject_id' = $1 AND "
                                "r.context->'human_task'->>'kind' = 'provided_subscription_approval'",
                          str(S["sub_a"]), fetch="fetchrow")
    check(task is not None and task["org_id"] == ORG_P, "the approval task opens in the PROVIDER org",
          f"{dict(task) if task else None}")
    c, o = await api(U_A2, "POST", f"{_P}/subscriptions/{S['sub_a']}/decide", {"outcome": "approve"})
    check(c == 403, "a tenant org admin cannot approve on the platform side (403)", f"{c}")
    c, o = await api(U_SA1, "POST", f"{_P}/subscriptions/{S['sub_a']}/decide", {"outcome": "approve",
                                                                              "note": "REP15 verify"})
    ok = check(c == 200 and o.get("status") == "active" and o.get("started_on") == S["seed_day"].isoformat()
               and o.get("approved_by") == str(U_SA1) and bool(o.get("mirror_model_id")),
               "a platform Super Admin approves A's request: active, started today, a mirror model in A",
               f"{c} {dumps(o)}")
    S["sub_a_ok"] = ok
    S["mirror_a"] = (o or {}).get("mirror_model_id")
    S["a_first"] = (o or {}).get("first_version") or {}
    # B's agreement changes before approval → the approval refuses; B ends the request and re-subscribes
    c1, bv2, _ = await upload_disclosure(U_B1, PM.KEY_AGREEMENT, "org", 2,
                                         summary="REP15 verify agreement — tenant B's own, version 2")
    c2, _ = await publish_disclosure(U_B2, bv2) if bv2 else (c1, None)
    c, o = await api(U_SA1, "POST", f"{_P}/subscriptions/{S['sub_b_first']}/decide", {"outcome": "approve"})
    check(c1 == 201 and c2 == 200 and c == 409 and "changed" in dumps(o),
          "a newer agreement for B since its acknowledgement: platform approval refuses (409)", f"{c} {dumps(o)}")
    c, o = await api(U_B1, "POST", f"{_C}/subscriptions/{S['sub_b_first']}/end", {"reason": "re-acknowledge v2"})
    check(c == 200 and o.get("status") == "ended" and o.get("ended_on") == S["seed_day"].isoformat(),
          "B ends its stale request (status ended, dated)", f"{c} {dumps(o)}")
    c, o = await api(U_B1, "POST", f"{_C}/{S['cm']}/acknowledge", {"version_id": bv2})
    c2, o2 = await api(U_B1, "POST", f"{_C}/{S['cm']}/subscribe")
    S["sub_b"] = (o2 or {}).get("id")
    c3, o3 = await api(U_SA1, "POST", f"{_P}/subscriptions/{S['sub_b']}/decide", {"outcome": "approve"})
    check(c == 200 and c2 == 201 and c3 == 200 and o3.get("status") == "active" and o2.get("agreement_version_id")
          == bv2, "B re-acknowledges version 2, re-requests, and is approved", f"{c} {c2} {c3} {dumps(o3)}")
    S["mirror_b"] = (o3 or {}).get("mirror_model_id")
    S["b_first"] = (o3 or {}).get("first_version") or {}


async def tasks_for(conn, org, kind, subject_id=None) -> list[dict]:
    rows = await platform(conn, "SELECT rs.id, rs.status, r.context->'human_task' AS ht FROM workflow_run_steps rs "
                                "JOIN workflow_runs r ON r.id = rs.workflow_run_id WHERE r.org_id = $1 AND "
                                "r.context->'human_task'->>'kind' = $2 AND ($3::text IS NULL OR "
                                "r.context->'human_task'->>'subject_id' = $3) ORDER BY rs.created_at", org, kind,
                          str(subject_id) if subject_id else None)
    return [{"id": str(r["id"]), "status": r["status"], "ht": json.loads(r["ht"]) if isinstance(r["ht"], str)
             else r["ht"]} for r in rows]


async def join(maker, checker, a, model_id) -> dict:
    code, out = await api(maker, "POST", f"/api/v1/accounts/{a}/model/join", {"model_id": str(model_id), "settings": {}})
    link = (out or {}).get("link") or {}
    if code != 201 or not link.get("id"):
        return {"code": code, "out": out}
    c2, o2 = await api(checker, "POST", f"/api/v1/accounts/{a}/model/changes/{link['id']}/approve", {})
    return {"code": c2, "out": o2, "link": link["id"]}


async def y_use(conn):
    section("[Y] Use: read-only mirror, adoption maker-checker, joins, the provider notice, copy (no link, no fee)")
    av = await tasks_for(conn, ORG_A, PM.TASK_AVAILABLE, S["a_first"].get("version_id"))
    check(len(av) == 1 and av[0]["status"] == "active" and S["a_first"].get("version_id") is not None,
          "A has a 'new version available' task for the first provided version", f"{dumps(av)} {dumps(S['a_first'])}")
    c, o = await api(U_A1, "GET", f"/api/v1/models/{S['mirror_a']}")
    m = (o or {}).get("model") or o
    check(c == 200 and m.get("read_only") is True and m.get("provided_by") == PROVIDER_NAME
          and m.get("source") == "provided" and m.get("name", "").startswith("Holdings of "),
          "the mirror is read-only, 'Provided by' the registered entity, named 'Holdings of …'", f"{c} {dumps(m)[:400]}")
    c, o = await api(U_A1, "POST", f"/api/v1/models/{S['mirror_a']}/drafts")
    check(c == 409 and "read-only" in dumps(o), "a tenant cannot start its own draft of a provided model (409)",
          f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"/api/v1/models/{S['mirror_a']}/versions/{S['a_first']['version_id']}/positions",
                     {"changes": [{"op": "remove", "row_num": 1}]})
    check(c == 409 and "read-only" in dumps(o), "a tenant cannot edit a provided version's positions (409)",
          f"{c} {dumps(o)}")
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
            await conn.execute(
                "INSERT INTO portfolio.model_version_positions (org_id, version_id, row_num, target_weight, "
                "resolution_state) VALUES ($1, $2, 99, 0, 'cash')", ORG_A, S["a_first"]["version_id"])
            ro = False
            raise RuntimeError("rollback")
    except asyncpg.PostgresError as exc:
        ro = "read-only" in str(exc)
    except RuntimeError:
        ro = False
    check(ro, "the DATABASE refuses a position written into a provided version outside the import path")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/adopt", {"version_id": S["a_first"]["version_id"]})
    check(c == 200 and o.get("status") == "submitted", "A1 adopts v1 (the maker: submitted for approval)",
          f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"/api/v1/models/{S['mirror_a']}/versions/{S['a_first']['version_id']}/approve", {})
    check(c == 403, "the maker cannot approve their own adoption (403)", f"{c} {dumps(o)}")
    c, o = await api(U_A2, "POST", f"/api/v1/models/{S['mirror_a']}/versions/{S['a_first']['version_id']}/approve", {})
    ok = check(c == 200 and (o.get("version") or {}).get("status") == "approved", "a different holder of approve_models (A2) approves the "
               "adoption", f"{c} {dumps(o)}")
    av = await tasks_for(conn, ORG_A, PM.TASK_AVAILABLE, S["a_first"]["version_id"])
    check(bool(av) and av[0]["status"] == "completed", "the 'new version available' task closed on adoption",
          dumps(av))
    for maker, checker, vid, sub in ((U_B1, U_B2, S["b_first"].get("version_id"), S["sub_b"]),):
        c1, _ = await api(maker, "POST", f"{_C}/subscriptions/{sub}/adopt", {"version_id": vid})
        c2, o2 = await api(checker, "POST", f"/api/v1/models/{S['mirror_b']}/versions/{vid}/approve", {})
        check(c1 == 200 and c2 == 200, "B adopts its first version (B1 maker, B2 checker)", f"{c1} {c2} {dumps(o2)}")
    joined = [(a, await join(U_A1, U_A2, a, S["mirror_a"])) for a in (ACC_A1, ACC_A2)]
    joined.append((ACC_B1, await join(U_B1, U_B2, ACC_B1, S["mirror_b"])))
    check(all(r["code"] == 200 for _, r in joined), "the subscribed version joins fixture accounts like the tenant's "
          "own (A1, A2 in tenant A; B1 in tenant B; maker-checker)", dumps([(str(a)[-4:], r["code"]) for a, r in joined]))
    S["joined"] = ok and all(r["code"] == 200 for _, r in joined)
    # the provider notice on a proposal for a provided model
    pid = await platform(conn, "SELECT id FROM public.rebalance_proposals WHERE account_id = $1 AND system_to IS NULL "
                               "ORDER BY created_at DESC LIMIT 1", ACC_A1, fetch="fetchval")
    if pid is not None:
        c, o = await api(U_A1, "GET", f"/api/v1/proposals/{pid}")
        pn = (o or {}).get("provider_notice") or ((o or {}).get("proposal") or {}).get("provider_notice") or {}
        check(c == 200 and pn.get("provided_by") == PROVIDER_NAME and pn.get("sub_adviser") is True
              and (pn.get("notice") or {}).get("key") == PM.KEY_NOTICE and PROVIDER_NAME in
              ((pn.get("notice") or {}).get("text") or ""), "the account's proposal shows the provider and the "
              "sub-adviser notice (library key model_provider.member_notice, {provider} filled)", f"{c} {dumps(pn)}")
    else:
        check(False, "the join produced a proposal for account A1", "no rebalance_proposals row")
    with _ctx(ORG_A):
        pool = await get_pool()
        async with pool.acquire() as c2:
            n = await PM.proposal_notice(c2, ORG_A, {"model_id": S["mirror_a"], "account_id": str(ACC_A1)})
            own = await PM.proposal_notice(c2, ORG_A, {"model_id": None, "account_id": str(ACC_A1)})
    check(bool(n) and n["notice"]["key"] == PM.KEY_NOTICE and own is None and PROVIDER_NAME in (n["notice"]["text"] or "")
          and "not automated" in n["tenant_adv_obligation"],
          "the notice resolves through the library for the account (fallback until the default is loaded); a "
          "non-provided proposal carries none; the tenant's ADV obligation is an operator item, not automated",
          dumps(n))
    # copy: a tenant-owned model, no link, no fee
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/copy", {"name": "REP15 Verify Copy"})
    check(c == 422, "a copy keeps the naming rule ('Holdings of …') → 422 otherwise", f"{c}")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/copy", {"name": "Holdings of REP15 Verify Copy"})
    cm = (o or {}).get("model") or {}
    cv = (o or {}).get("version") or {}
    row = await platform(conn, "SELECT source, provided_subscription_id, copied_from_catalog_version_id, provider_name "
                               "FROM portfolio.models WHERE id = $1 AND system_to IS NULL", cm.get("id"),
                         fetch="fetchrow")
    check(c == 201 and row is not None and row["source"] == "provided_copy" and row["provided_subscription_id"] is None
          and str(row["copied_from_catalog_version_id"]) == S["cv1"] and row["provider_name"] is None,
          "the copy is tenant-owned: no subscription link, no provider", f"{c} {dumps(o)[:300]} {dict(row or {})}")
    c1, _ = await api(U_A1, "POST", f"/api/v1/models/{cm.get('id')}/versions/{cv.get('id')}/submit", {})
    c2, _ = await api(U_A2, "POST", f"/api/v1/models/{cm.get('id')}/versions/{cv.get('id')}/approve", {})
    r = await join(U_A1, U_A2, ACC_A3, cm.get("id"))
    check(c1 == 200 and c2 == 200 and r["code"] == 200, "the copy goes through rep3's own approval and ACC_A3 joins it",
          f"{c1} {c2} {r['code']}")
    S["copy"] = cm.get("id")


async def y_new_version(conn):
    section("[Y] A new published version: the 'new version available' task with the exact diff; adoption; pre-approval")
    with _ctx(ORG_B):
        pool = await get_pool()
        async with pool.acquire() as c2:
            await OS.set_setting(c2, ORG_B, PM.PREAPPROVE_SETTING, True, U_B2, pool=pool)
    r = await provider_version(V2, model_id=S["pmodel"], salt="v2")
    check(r["codes"] == (201, 201, 200, 200), "the provider's v2 is approved in rep3 (SA1 maker, SA2 checker)",
          f"{r['codes']} {r['detail']}")
    S["pv2"] = r["version"]
    c, o = await api(U_SA3, "POST", f"{_P}/catalog-models/{S['cm']}/publish",
                     {"version_id": S["pv2"], "name": "Holdings of REP15 Verify Index",
                      "description": "Replicates the holdings of the REP15 Verify Index.",
                      "construction_notes": "Quarterly rebalance; S3 replaced by S4.",
                      "holdings_as_of": S["seed_day"].isoformat(), "fee_bps": str(FEE_V2)})
    S["cv2"] = (o or {}).get("id")
    c2, o2 = await api(U_SA2, "POST", f"{_P}/catalog-versions/{S['cv2']}/decide", {"outcome": "approve"})
    fan = {f.get("subscription_id"): f for f in (o2 or {}).get("fan_out") or []}
    check(c == 201 and c2 == 200 and o2.get("status") == "published" and set(fan) == {S["sub_a"], S["sub_b"]},
          "v2 is requested (SA3), published (SA2), and fans out to both ACTIVE subscriptions", f"{c} {c2} {dumps(o2)}")
    fa = fan.get(S["sub_a"]) or {}
    want = {"S1": Decimal("-0.1"), "S3": Decimal("-0.2"), "S4": Decimal("0.3")}
    got = {S["key_of"].get((r_.get("key") or "").split(":", 1)[-1]) or r_.get("ticker", "").replace(PREFIX, ""):
           D(r_["change"]) for r_ in fa.get("diff") or []}
    check(got == want, "the diff is exact: S1 −0.1, S3 −0.2 (removed), S4 +0.3 (added); S2 unchanged and omitted",
          f"{dumps(fa.get('diff'))}")
    av = await tasks_for(conn, ORG_A, PM.TASK_AVAILABLE, fa.get("version_id"))
    tdiff = {d_.get("ticker"): D(d_["change"]) for d_ in ((av[0]["ht"] or {}).get("payload") or {}).get("diff", [])} \
        if av else {}
    check(len(av) == 1 and av[0]["status"] == "active" and tdiff == {f"{PREFIX}{k}": v for k, v in want.items()},
          "A's task 'new version available' carries the same exact diff", dumps(av)[:500])
    live_a = await platform(conn, "SELECT origin_ref FROM portfolio.model_versions WHERE model_id = $1 AND status = "
                                  "'approved' AND system_to IS NULL", S["mirror_a"], fetch="fetchval")
    check(str(live_a) == S["pv1"], "default OFF: A's accounts stay on v1 until A adopts (no pre-approval setting)",
          f"{live_a}")
    fb = fan.get(S["sub_b"]) or {}
    vb = await platform(conn, "SELECT status, checker_id, origin_ref FROM portfolio.model_versions WHERE id = $1 AND "
                              "system_to IS NULL", fb.get("version_id"), fetch="fetchrow")
    check(bool((fb.get("preapproved") or {}).get("approved")) and vb is not None and vb["status"] == "approved"
          and vb["checker_id"] == U_B2 and str(vb["origin_ref"]) == S["pv2"],
          "B's org setting pre-approves adoption: v2 approved with the setting's recorder (B2) as the checker",
          f"{dumps(fb)} {dict(vb or {})}")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/adopt", {"version_id": fa.get("version_id")})
    c2, _ = await api(U_A1, "POST", f"/api/v1/models/{S['mirror_a']}/versions/{fa.get('version_id')}/approve", {})
    c3, o3 = await api(U_A2, "POST", f"/api/v1/models/{S['mirror_a']}/versions/{fa.get('version_id')}/approve", {})
    check(c == 200 and c2 == 403 and c3 == 200 and (o3.get("version") or {}).get("status") == "approved",
          "A's adoption of v2 needs A's checker: maker A1 refused, A2 approves", f"{c} {c2} {c3} {dumps(o3)}")
    props = int(await platform(conn, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = ANY($1::uuid[]) "
                                     "AND model_version_id = $2 AND system_to IS NULL", [ACC_A1, ACC_A2],
                               fa.get("version_id"), fetch="fetchval"))
    check(props == 2, "the approval yields a rebalance PROPOSAL per account on the model (never a trade)", f"{props}")
    S["v2_ok"] = c3 == 200


async def y_suspend(conn):
    section("[Y] Suspension stops adoption and joins; accounts keep their version and get a task to move")
    live_before = await platform(conn, "SELECT id FROM portfolio.model_versions WHERE model_id = $1 AND status = "
                                       "'approved' AND system_to IS NULL", S["mirror_a"], fetch="fetchval")
    c, o = await api(U_A1, "POST", f"{_P}/subscriptions/{S['sub_a']}/suspend", {"reason": "x"})
    check(c == 403, "a tenant admin cannot suspend (platform side, Super Admin)", f"{c}")
    c, o = await api(U_SA1, "POST", f"{_P}/subscriptions/{S['sub_a']}/suspend", {"reason": "REP15 verify suspension"})
    mt = (o or {}).get("move_tasks") or []
    check(c == 200 and o.get("status") == "suspended" and bool(o.get("suspended_at")) and len(mt) == 2
          and all(t.get("task_id") for t in mt), "suspended (dated); each of A's two accounts on it gets a move task",
          f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/refresh")
    check(c == 409 and "suspended" in dumps(o), "while suspended: a new version is not imported (409)", f"{c}")
    r = await join(U_A1, U_A2, ACC_A4, S["mirror_a"])
    check(r["code"] == 409 and "suspended" in dumps(r["out"]), "while suspended: a new account cannot join (409)",
          f"{r['code']} {dumps(r['out'])}")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/copy", {"name": "Holdings of REP15 Verify Copy 2"})
    check(c == 409, "while suspended: no copy (409)", f"{c}")
    live_after = await platform(conn, "SELECT id FROM portfolio.model_versions WHERE model_id = $1 AND status = "
                                      "'approved' AND system_to IS NULL", S["mirror_a"], fetch="fetchval")
    links = int(await platform(conn, "SELECT count(*) FROM public.account_model_links WHERE model_id = $1 AND status = "
                                     "'active' AND system_to IS NULL AND valid_to IS NULL", S["mirror_a"],
                               fetch="fetchval"))
    check(live_after == live_before and links == 2, "existing accounts keep their link and their version", f"{links}")
    c, o = await api(U_SA1, "POST", f"{_P}/subscriptions/{S['sub_a']}/resume", {"reason": "REP15 verify resume"})
    check(c == 200 and o.get("status") == "active", "resumed (active again)", f"{c} {dumps(o)}")
    c, o = await api(U_SA1, "POST", f"{_P}/catalog-versions/{S['cv1']}/retire", {"reason": "REP15 verify — v2 is live"})
    c2, o2 = await api(U_A1, "GET", _C)
    vers = {v["id"]: v["status"] for x in (o2.get("catalog") or []) for v in x.get("versions") or []}
    check(c == 200 and vers.get(S["cv1"]) == "retired" and vers.get(S["cv2"]) == "published",
          "a retired version stays readable in the tenant's catalog", f"{c} {vers}")


async def y_isolation(conn):
    section("[Y] Isolation: the provider reads status / counts / assets only; tenants never see each other")
    S["assets_a"] = sum((D(q) * D(p) for a in (ACC_A1, ACC_A2) for _, q, p in HOLDINGS[a]), Decimal(0))
    S["assets_b"] = sum((D(q) * D(p) for _, q, p in HOLDINGS[ACC_B1]), Decimal(0))
    c, o = await api(U_PV, "GET", f"{_P}/subscribers")
    subs = {s["subscription_id"]: s for s in (o or {}).get("subscriptions") or []}
    usage = {u["subscription_id"]: u for u in (o or {}).get("usage") or []}
    key_sets = {tuple(sorted(s)) for s in subs.values()} | {tuple(sorted(u)) for u in usage.values()}
    allowed = {tuple(sorted(("subscription_id", "tenant_org_id", "tenant_name", "catalog_model_id", "status",
                             "status_label", "requested_at", "started_on", "ended_on", "suspended_at"))),
               tuple(sorted(("tenant_org_id", "subscription_id", "catalog_model_id", "catalog_version_id",
                             "account_count", "model_assets")))}
    check(c == 200 and bool(subs) and key_sets <= allowed, "the platform viewer (view_models, not Super Admin) reads "
          "subscription status and usage with exactly the allowed fields", f"{c} {key_sets}")
    ua, ub = usage.get(S["sub_a"]) or {}, usage.get(S["sub_b"]) or {}
    check(ua.get("account_count") == 2 and D(ua.get("model_assets", "-1")) == S["assets_a"]
          and ub.get("account_count") == 1 and D(ub.get("model_assets", "-1")) == S["assets_b"]
          and ua.get("catalog_version_id") == S["cv2"],
          f"counts and model assets per tenant per version: A 2 accounts {S['assets_a']} (the copy's account not "
          f"counted), B 1 account {S['assets_b']}, both on catalog v2", f"{dumps(ua)} {dumps(ub)}")
    body = dumps(o)
    leaks = [x for x in [str(a) for a in ACCOUNTS] + [m for m in S["masked"].values() if len(m) >= 6]
             + [str(h) for h in HOUSEHOLDS]
             + [h[2] for h in HOUSEHOLDS.values()] + [USERS[u][4] for u in (U_A1, U_A2, U_B1, U_B2, U_AMEM)]
             + [str(e) for e in ENTITIES] + ["REP15 Verify Member"] if x in body]
    check(not leaks, "no account, household, member, entity or person of any tenant in the provider's view",
          f"{leaks[:5]}")
    for t, where in (("portfolio.provider_subscriptions", "org_id = ANY($1::uuid[])"),
                     ("public.account_model_links", "org_id = ANY($1::uuid[])"),
                     ("public.accounts", "org_id = ANY($1::uuid[])"),
                     ("portfolio.positions", "org_id = ANY($1::uuid[])"),
                     ("public.households", "org_id = ANY($1::uuid[])"),
                     ("public.entities", "org_id = ANY($1::uuid[])"),
                     ("portfolio.models", "org_id = ANY($1::uuid[])"),
                     ("public.rebalance_proposals", "org_id = ANY($1::uuid[])"),
                     ("public.disclosure_acknowledgements", "org_id = ANY($1::uuid[])")):
        n = int(await scoped(conn, ORG_P, f"SELECT count(*) FROM {t} WHERE {where}", [ORG_A, ORG_B], fetch="fetchval"))
        mine = int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval"))
        check(n == 0 and mine > 0,
              f"RLS: the provider org reads none of {t} of either tenant (tenant A reads its own: {mine})", f"{n}")
    via = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.provider_model_usage($1::uuid, current_date)", ORG_P,
                       fetch="fetchval")
    check(int(via) == 0, "the usage function answers only for the provider's own org (tenant A's context: 0 rows)",
          f"{via}")
    for t in ("portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
              "portfolio.model_uploads"):
        n = int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_P, fetch="fetchval"))
        check(n == 0, f"RLS: a tenant reads none of the provider's {t} (no holdings file, no registry rows)", f"{n}")
    for org, other, sub_other in ((ORG_B, ORG_A, S["sub_a"]), (ORG_A, ORG_B, S["sub_b"])):
        for t in ("portfolio.provider_subscriptions", "portfolio.model_provider_events"):
            own = int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", org, fetch="fetchval"))
            theirs = int(await scoped(conn, org, f"SELECT count(*) FROM {t} WHERE org_id = $1", other, fetch="fetchval"))
            check(own > 0 and theirs == 0, f"RLS {t}: tenant {'B' if org == ORG_B else 'A'} sees its own ({own}), none "
                  "of the other tenant's", f"{own} {theirs}")
    c, o = await api(U_B1, "GET", f"{_C}/subscriptions")
    ids = {s["id"] for s in (o or {}).get("subscriptions") or []}
    check(c == 200 and S["sub_b"] in ids and S["sub_a"] not in ids, "tenant B's API lists its own subscriptions only",
          f"{ids}")
    c, o = await api(U_B1, "POST", f"{_C}/subscriptions/{S['sub_a']}/adopt", {"version_id": str(uuid.uuid4())})
    check(c == 404, "tenant B's VALID write on tenant A's subscription is a 404", f"{c} {dumps(o)}")
    c, o = await api(U_B1, "GET", f"/api/v1/accounts/{ACC_A1}/model")
    check(c == 404, "tenant B cannot read tenant A's account (404)", f"{c}")
    for path in (_C, f"{_C}/subscriptions", f"{_P}/subscribers"):
        c, o = await api(U_AMEM, "GET", path)
        check(c == 403, f"a member of tenant A (no permission) is refused {path} (403)", f"{c}")
    find("members", "rep15 adds no member-facing route; a member's own-household scope is unchanged (its sprint's "
         "verify covers it) — the member is refused every rep15 route above")
    c, o = await api(U_PV, "GET", _P)
    check(c == 403, "the platform viewer is not Super Admin: the provider record and gate are refused (403)", f"{c}")


async def je_lines(conn, entry_id) -> dict:
    rows = await platform(conn, "SELECT coa.code, jl.debit, jl.credit FROM public.journal_lines jl JOIN "
                                "public.chart_of_accounts coa ON coa.id = jl.account_id WHERE jl.entry_id = $1 "
                                "ORDER BY jl.line_no", entry_id)
    return {r["code"]: (D(r["debit"]), D(r["credit"])) for r in rows}


async def y_fee(conn):
    section("[Y] The fee: hand-computed bps × average daily assets == accrual == invoice line == GL; reversal")
    day = S["seed_day"]
    start = day - timedelta(days=2)
    c, o = await api(U_SA1, "POST", f"{_P}/fees/accrue", {"day_from": start.isoformat(), "day_to": day.isoformat()})
    check(c == 200 and len(o.get("written") or []) == 2, "accrual over three days: one row per subscription on the "
          "day its accounts were on the model (none before it started)", f"{c} {dumps(o)}")
    acc = await platform(conn, "SELECT * FROM portfolio.provider_fee_accruals WHERE subscription_id = $1 AND valid_to "
                               "IS NULL", S["sub_a"], fetch="fetchrow")
    want_daily = (S["assets_a"] * FEE_V2 / (Decimal(10000) * 365)).quantize(Decimal("1E-10"), rounding=ROUND_HALF_UP)
    check(acc is not None and D(acc["model_assets"]) == S["assets_a"] and D(acc["fee_bps"]) == FEE_V2
          and D(acc["daily_fee"]) == want_daily and acc["account_count"] == 2,
          f"A's accrual: assets {S['assets_a']} × {FEE_V2} bps / (10 000 × 365) = {want_daily}",
          dumps(dict(acc or {})))
    # the identity on a synthetic 30-day series (pure): Σ daily accruals == bps × average daily assets × days / 365
    series = [Decimal(100000 + 997 * i) for i in range(30)]
    acc_sum = sum((PM.daily_fee(x, FEE_V2) for x in series), Decimal(0))
    avg = sum(series) / 30
    hand = cents(FEE_V2 / Decimal(10000) * avg * Decimal(30) / Decimal(365))
    check(cents(acc_sum) == hand == PM.period_amount(sum(series), FEE_V2),
          "on a 30-day series: Σ daily accruals == bps × average daily assets × days / 365 == the invoice formula",
          f"{cents(acc_sum)} {hand} {PM.period_amount(sum(series), FEE_V2)}")
    c, inv = await api(U_SA1, "POST", f"{_P}/fees/invoices", {"subscription_id": S["sub_a"],
                                                              "period_start": start.isoformat(),
                                                              "period_end": day.isoformat()})
    days = 3
    hand = cents(FEE_V2 / Decimal(10000) * (S["assets_a"] / days) * Decimal(days) / Decimal(365))
    line = ((inv or {}).get("lines") or [{}])[0]
    lines = await je_lines(conn, (inv or {}).get("journal_entry_id"))
    je = await platform(conn, "SELECT * FROM public.journal_entries WHERE id = $1", (inv or {}).get("journal_entry_id"),
                        fetch="fetchrow")
    check(c == 201 and D(inv.get("amount", "-1")) == hand and D(line.get("amount", "-1")) == hand
          and line.get("label") == "Model-provider fee" and line.get("days") == days
          and D(line.get("average_daily_assets", "-1")) == (S["assets_a"] / days).quantize(Decimal("0.000001"),
                                                                                          rounding=ROUND_HALF_UP)
          and cents(D(acc["daily_fee"])) == hand
          and lines == {"1230": (hand, ZERO), "4800": (ZERO, hand)} and je is not None and je["posted_at"] is not None
          and je["org_id"] == ORG_P and je["vehicle_kind"] == "LEDGER_BOOK" and je["source_event_id"] == UUID(inv["id"]),
          f"hand {hand} (bps × average daily assets over 3 days × 3/365) == the accrual == the 'Model-provider fee' line "
          "== the posted GL entry (Dr 1230 receivable / Cr 4800 revenue, the provider's ledger book)",
          f"{c} {dumps(inv)} {lines}")
    S["inv1"] = (inv or {}).get("id")
    S["je1"] = (inv or {}).get("journal_entry_id")
    fp_inv = await fingerprint(conn, "SELECT * FROM portfolio.provider_fee_invoices WHERE id = $1", S["inv1"])
    fp_lines = await fingerprint(conn, "SELECT * FROM portfolio.provider_fee_invoice_lines WHERE invoice_id = $1",
                                 S["inv1"])
    fp_je = await fingerprint(conn, "SELECT id, posted_at, memo, entry_date FROM public.journal_entries WHERE id = $1",
                              S["je1"])
    c, o = await api(U_SA1, "POST", f"{_P}/fees/invoices", {"subscription_id": S["sub_a"],
                                                            "period_start": day.isoformat(), "period_end": day.isoformat()})
    check(c == 409, "a second invoice over the same period is refused (correct by reversal first)", f"{c} {dumps(o)}")
    for sql in ("UPDATE portfolio.provider_fee_invoices SET amount = amount + 1 WHERE id = $1",
                "UPDATE portfolio.provider_fee_accruals SET model_assets = 0 WHERE subscription_id = (SELECT "
                "subscription_id FROM portfolio.provider_fee_invoices WHERE id = $1)"):
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(sql, S["inv1"])
                ed = False
                raise RuntimeError("rollback")
        except asyncpg.PostgresError as exc:
            ed = "never" in str(exc) or "permanent" in str(exc)
        except RuntimeError:
            ed = False
        check(ed, f"never edited in place: {sql.split(' SET')[0]} refused by the database")
    # a correction: A1's position restated (+RESTATE), re-accrued (restated), the invoice reversed, re-invoiced
    pid = S["pos"][(ACC_A1, "S1")]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        row = await conn.fetchrow("SELECT * FROM portfolio.positions WHERE id = $1", pid)
        await conn.execute("UPDATE portfolio.positions SET valid_to = now() WHERE id = $1", pid)
        new_pid = await conn.fetchval(
            "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity, market_value, "
            "authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, 'manual', 'manual', $7) RETURNING id",
            row["org_id"], row["owner_entity_id"], row["asset_id"], row["as_of_date"], row["quantity"],
            D(row["market_value"]) + RESTATE, ACC_A1)
    S["pos"][(ACC_A1, "S1")] = new_pid
    S["assets_a"] += RESTATE
    c, o = await api(U_SA1, "POST", f"{_P}/fees/accrue", {"day_from": day.isoformat(), "day_to": day.isoformat()})
    old = await platform(conn, "SELECT count(*) FROM portfolio.provider_fee_accruals WHERE subscription_id = $1 AND "
                               "valid_to IS NOT NULL", S["sub_a"], fetch="fetchval")
    check(c == 200 and len(o.get("restated") or []) == 1 and o.get("unchanged") == 1 and int(old) == 1,
          "re-accrual restates A's day (closed + successor, valid axis); B's day unchanged", f"{c} {dumps(o)}")
    c, rev = await api(U_SA1, "POST", f"{_P}/fees/invoices/{S['inv1']}/correct", {"reason": "REP15 verify — positions "
                                                                                           "restated"})
    rlines = await je_lines(conn, (rev or {}).get("journal_entry_id"))
    rje = await platform(conn, "SELECT reverses_entry_id, posted_at FROM public.journal_entries WHERE id = $1",
                         (rev or {}).get("journal_entry_id"), fetch="fetchrow")
    check(c == 201 and rev.get("kind") == "reversal" and rev.get("reverses_invoice_id") == S["inv1"]
          and rlines == {"1230": (ZERO, hand), "4800": (hand, ZERO)} and rje is not None
          and str(rje["reverses_entry_id"]) == S["je1"] and rje["posted_at"] is not None,
          "a correction POSTS A REVERSAL (fn_reverse_journal_entry: the original entry mirrored) and a reversal "
          "invoice", f"{c} {dumps(rev)} {rlines}")
    after = (await fingerprint(conn, "SELECT * FROM portfolio.provider_fee_invoices WHERE id = $1", S["inv1"]),
             await fingerprint(conn, "SELECT * FROM portfolio.provider_fee_invoice_lines WHERE invoice_id = $1",
                               S["inv1"]),
             await fingerprint(conn, "SELECT id, posted_at, memo, entry_date FROM public.journal_entries WHERE id = $1",
                               S["je1"]))
    check(after == (fp_inv, fp_lines, fp_je), "the original invoice, its lines and its journal entry are unchanged "
          "(count + checksum)", f"{(fp_inv, fp_lines, fp_je)} → {after}")
    c, inv2 = await api(U_SA1, "POST", f"{_P}/fees/invoices", {"subscription_id": S["sub_a"],
                                                               "period_start": start.isoformat(),
                                                               "period_end": day.isoformat()})
    hand2 = cents(FEE_V2 / Decimal(10000) * (S["assets_a"] / days) * Decimal(days) / Decimal(365))
    l2 = await je_lines(conn, (inv2 or {}).get("journal_entry_id"))
    check(c == 201 and hand2 != hand and D(inv2.get("amount", "-1")) == hand2
          and l2 == {"1230": (hand2, ZERO), "4800": (ZERO, hand2)},
          f"re-invoiced on the restated assets: hand {hand2} == invoice == GL", f"{c} {dumps(inv2)} {l2}")
    c, o = await api(U_A1, "GET", f"{_C}/fees")
    ids = {i["id"] for i in (o or {}).get("invoices") or []}
    cb, ob = await api(U_B1, "GET", f"{_C}/fees")
    idsb = {i["id"] for i in (ob or {}).get("invoices") or []}
    check(c == 200 and {S["inv1"], inv2.get("id")} <= ids and not (ids & idsb), "the tenant reads its own invoices; "
          "the other tenant none of them", f"{ids} {idsb}")


async def y_gate_close(conn):
    section("[Y] Gate closed again: subscribe / adopt / copy / join / accrue refused with the reason")
    c, o = await api(U_SA1, "POST", f"{_P}/gate/close", {"reason": "REP15 verify — closed again"})
    check(c == 200 and o.get("is_open") is False, "Super Admin closes the fixture gate", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"{_C}/{S['cm']}/subscribe")
    check(refused(c, o), "gate OFF: a subscription request → 409 with the reason (not a bare 404)", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"{_C}/{S['cm']}/acknowledge", {"version_id": S["agreement_default"]})
    check(refused(c, o), "gate OFF: acknowledging for a subscription → 409 with the reason", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/adopt", {"version_id": str(uuid.uuid4())})
    check(refused(c, o), "gate OFF: adopt → 409 with the reason", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "POST", f"{_C}/subscriptions/{S['sub_a']}/copy", {"name": "Holdings of REP15 Verify C3"})
    check(refused(c, o), "gate OFF: copy → 409 with the reason", f"{c} {dumps(o)}")
    r = await join(U_A1, U_A2, ACC_A4, S["mirror_a"])
    check(refused(r["code"], r["out"]), "gate OFF: a new account joining a provided model → 409 with the reason",
          f"{r['code']} {dumps(r['out'])}")
    c, o = await api(U_SA1, "POST", f"{_P}/fees/accrue", {"day_from": S["seed_day"].isoformat(),
                                                          "day_to": S["seed_day"].isoformat()})
    check(refused(c, o), "gate OFF: accrual → 409 with the reason", f"{c} {dumps(o)}")
    c, o = await api(U_A1, "GET", _C)
    check(c == 200 and o.get("catalog") == [], "gate OFF: the catalog is invisible to tenants again (RLS)",
          f"{dumps(o.get('catalog'))[:200]}")


async def y_real_rows(conn):
    section("[Y] Real and demo rows untouched (count + checksum); the real platform gate OFF and unchanged")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")
    real = await platform(conn, "SELECT portfolio.model_publishing_open($1::uuid)", PLATFORM_ORG_ID, fetch="fetchval")
    check(real is False, "the REAL platform org's gate is OFF after the run", f"{real}")
    if S.get("demo_org") is None:
        find("demo-olivo", "no organization with slug 'demo-olivo' — its rows were not fingerprinted")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    self_code(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    data = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
    rows = data.get("routes", []) if isinstance(data, dict) else data
    have = {(m, r.get("path")): r.get("after") for r in rows for m in (r.get("methods") or [])}
    miss = [(k, g) for k, g in ROUTES.items() if g not in str(have.get(k))]
    check(not miss, f"each of rep15's {len(ROUTES)} routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c15, o15, e15 = run_cmd(["node", "--test", "tests/replication/rep15.test.mjs"], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o15, re.M)
    p = re.search(r"^# pass (\d+)$", o15, re.M)
    check(c15 == 0 and n is not None and p is not None and int(n.group(1)) > 0 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o15, re.M) is not None and "not ok" not in o15,
          f"the rep15 render tests (tests/replication/rep15.test.mjs, run by path) all pass "
          f"({n.group(1) if n else '?'})", f"{c15} {(o15 + e15)[-300:] if c15 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep13.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep13.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_rep13.py: 0 FAIL (its own final TOTAL line and exit code)",
          f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    try:
        log = await teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    check(left == 0, "zero fixture rows remain", f"{left}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


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
    pre = None
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        if not await guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("provider model", y_provider_model, conn)
            if S.get("provider_model_ok"):
                await run("gate off", y_gate_off, conn)
                await run("gate open", y_gate_open, conn)
                if S.get("gate_open"):
                    await run("publish", y_publish, conn)
                    if S.get("published"):
                        await run("subscribe", y_subscribe, conn)
                        if S.get("sub_a_ok") and S.get("mirror_b"):
                            await run("use", y_use, conn)
                            if S.get("joined"):
                                await run("new version", y_new_version, conn)
                                await run("suspend", y_suspend, conn)
                                await run("isolation", y_isolation, conn)
                                if S.get("v2_ok"):
                                    await run("fee", y_fee, conn)
                            else:
                                check(False, "the provided model joined the fixture accounts", "later sections skipped")
                        else:
                            check(False, "both tenants' subscriptions became active", "later sections skipped")
                    else:
                        check(False, "the catalog version was published", "later sections skipped")
                    await run("gate close", y_gate_close, conn)
                else:
                    check(False, "the fixture gate opened", "the publish / subscribe / fee sections skipped")
            else:
                check(False, "the provider model was approved", "every later section skipped")
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [] and S3_BUILT == [],
              "no network call, no model call and no real R2 client during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]} {S3_BUILT}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (GL rows, disclosure rows and platform securities included)",
                  f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count (fixture rows)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k]} → {after.get(k)}")
            real = await platform(conn, "SELECT portfolio.model_publishing_open($1::uuid)", PLATFORM_ORG_ID,
                                  fetch="fetchval")
            check(real is False, "the REAL platform org's gate is OFF after teardown", f"{real}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
        if S.get("no_teardown"):
            print(total_line())
    if os.environ.get("VERIFY_NESTED") == "1":
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
        check(False, "the verify crashed", f"{type(exc).__name__}: {exc}")
        print(total_line())
        rc = 1
    sys.exit(rc)
