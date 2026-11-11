"""verify_cpsim01 — CPSIM01: the COUNTERPARTY SIMULATOR begins. The Altruist contract written down; schema ``cpsim`` +
NOLOGIN role ``cpsim_app`` (least privilege, read from the catalog); the sub-app MOUNTED at /cpsim with its own key auth,
the SIMULATED header, request log and the generic-envelope mirror; the counterparty REGISTRY (4 institution types,
simulated-only, bi-temporal) with fake credentials and FAKE bank / FIX values the database refuses to make real; the
clock (business-day advance, audited); the SAFETY rules (counterparty_sim.allowed default off, cpsim.may_connect, the
boundary scan); Admin → Counterparties (Super Admin only, audited, render tests).

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; WSL memory is limited — nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim01.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from
Doppler over HTTPS (``_db_connect``) first — the only network the script itself uses; after that every httpx network
transport refuses and records (asserted empty at the end). The simulator is called IN-PROCESS (httpx ASGITransport
against the real ``main.app``, which mounts it). No model call.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token`` — every call recorded, so
"a Hollisworks JWT never authenticates to /cpsim" is proven by the stub never being consulted) and the Auth0 profile
call. REAL: the app, the mounted sub-app, cpsim.registry / generators / mirror, the database (CHECKs, the bank-details
guard, the org-setting guard, cpsim.may_connect, role cpsim_app), RLS. The script's own connection does not bypass
RLS (rolbypassrls = false, asserted). TIME IS INJECTED: cpsim.registry.set_today (Friday 2026-11-13; the holiday
fixture on Wednesday 2026-11-25, the day before Thanksgiving) and cpsim.mirror.set_now (15:30:00 UTC).

FIXTURES (ids 99000000-0000-0000-0000-0000c951…; sign-in tag cpsim01verify): orgs T and T2, is_test = true (asserted),
committed; the NON-test org R, its demo / non-demo / mixed households and the demo-tagged entities are created INSIDE
ONE ROLLED-BACK TRANSACTION (the CRMDETAIL1 guard makes a real org's demo rows undeletable by design — so they are never
committed). Users with real user_roles grants (the app's decision on each asserted): U_SA Super Admin, U_OA org admin,
U_MEM member. Counterparties carry the reserved slug prefix ``cpsimverify-`` (asserted to match no real row).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by this run's fixture ids; the cpsim
tables have no org_id, so they are passed in the plan by fixture slug prefix (the FK closure reaches every child) and
the health-probe log rows by their recorded ids. Every touched table re-read for its exact pre-test count BY FIXTURE
IDS ONLY (audit_log by fixture org / user / resource ids). Real rows — every non-fixture counterparty, the
counterparty_sim.allowed setting of every real org, demo-tagged entities, the seeded reference rows — fingerprinted
before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: the teardown and the leftover check only.
--only=<registry|credentials|generators|subapp|clock|safety|admin>: the seed, the named sections (and what they
need), the full teardown; no routes / front end / regression. With VERIFY_NESTED=1 the regression prints one
'[SKIP] nested run'.
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
import random
import re
import subprocess
import sys
import traceback
from collections import Counter
from datetime import date, datetime, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cpsim01"
BEFORE_REF = "fca695d"   # cpsim01-work's base: 'merge verifyfix1'
_P = "/api/v1/admin/counterparties"
_I = _P + "/{counterparty_id}"
_SA = "super_admin"
ROUTES = {("GET", _P): _SA, ("POST", _P): _SA, ("GET", _P + "/org-access"): _SA,
          ("PUT", _P + "/org-access/{target_org_id}"): _SA, ("GET", _I): _SA, ("PUT", _I): _SA,
          ("POST", _I + "/activate"): _SA, ("POST", _I + "/disable"): _SA, ("POST", _I + "/key/generate"): _SA,
          ("POST", _I + "/key/rotate"): _SA, ("POST", _I + "/key/revoke"): _SA, ("POST", _I + "/bank/regenerate"): _SA,
          ("PUT", _I + "/fix"): _SA, ("PUT", _I + "/behaviour"): _SA, ("POST", _I + "/clock/advance"): _SA,
          ("GET", _I + "/requests"): _SA, ("POST", _I + "/test-message"): _SA}
SIM_ROUTES = {("GET", "/cpsim/health"), ("POST", "/cpsim/c/{slug}/mirror")}
PACKAGE = ("apps/api/cpsim/__init__.py", "apps/api/cpsim/app.py", "apps/api/cpsim/auth.py", "apps/api/cpsim/db.py",
           "apps/api/cpsim/registry.py", "apps/api/cpsim/mirror.py", "apps/api/cpsim/generators.py")
NODE_TEST = "tests/cpsim01/cpsim01.test.mjs"
NODE_TEST_COUNT = 11
CONTRACT = REPO / "docs" / "cpsim" / "ALTRUIST_CONTRACT.md"
NOTES = REPO / "docs" / "cpsim" / "CPSIM01_NOTES.md"
ADMIN_DOC = REPO / "docs" / "cpsim" / "CPSIM_ADMIN.md"
DESIGN = REPO / "docs" / "cpsim" / "COUNTERPARTY_SIMULATOR_DESIGN_v1.md"
MIGRATION = REPO / "docs" / "cpsim01_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
BOUNDARY = HERE / "cpsim_boundary_check.py"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """The source with comments and docstrings removed (a marker in prose never satisfies a code check)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def self_routes(check, find):
    """cpsim01's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cpsim01 changed no existing route's gate and removed none", f"{o.where()}; changed={sorted(changed)[:3]} "
          f"removed={o.removed[:3]}")
    mine = flat(o.new)
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)} admin routes",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g != by[k].get("after")]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate (super_admin) at cpsim01's own end point",
          f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cpsim01's routes all still exist today with the same gate", f"{drift[:4]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    sims = {(m, r["path"]) for r in data.get("simulator_routes", []) for m in r.get("methods", [])}
    hollis = {r["path"] for r in data.get("routes", [])}
    check(SIM_ROUTES <= sims and not any(p.startswith("/cpsim") for p in hollis),
          "the mounted /cpsim paths are documented as SIMULATOR routes with their own auth (not Hollisworks routes)",
          f"{sorted(sims)}")
    md = _src("docs/AUTHZ1_ROUTES.md")
    check("## Simulator routes — own auth (CPSIM01)" in md and "`/cpsim/c/{slug}/mirror`" in md,
          "docs/AUTHZ1_ROUTES.md carries the simulator section")


def self_static(check, find):
    """Static markers, matched by NAME (never a frozen live count)."""
    for f in PACKAGE:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(requests|anthropic|litellm|socket)\b",
                                          src, re.M),
              f"{f}: present; no model call and no outbound network client", f"{len(src)} chars")
    imports = set()
    for f in PACKAGE:
        imports |= set(re.findall(r"^\s*from\s+(services(?:\.\w+)?)\s+import", _code_only(_src(f)), re.M))
    check(imports == {"services.us_business_days", "services.bitemporal"},
          "the package imports exactly the two named shared helpers from services/", f"{sorted(imports)}")
    m = _code_only(_src("apps/api/main.py"))
    check('SIMULATOR_PREFIX = "/cpsim"' in m and "app.mount(SIMULATOR_PREFIX, cpsim_app)" in m
          and "or _is_simulator(path)" in m and m.count("_is_simulator(") >= 3,
          "main.py mounts the sub-app at /cpsim and both auth middlewares pass the prefix through")
    db = _code_only(_src("apps/api/cpsim/db.py"))
    check('SET LOCAL ROLE {ROLE}' in db and 'ROLE = "cpsim_app"' in db and "statement_cache_size\": 0" in db,
          "cpsim/db.py: SET LOCAL ROLE cpsim_app inside every transaction; statement_cache_size=0 (Rule 2)")
    rt = _code_only(_src("apps/api/routers/admin_counterparties.py"))
    check(rt.count("require_super_admin(") >= 3 and "SDB.as_simulator(conn)" in rt and "SDB.leave_simulator(conn)" in rt
          and "INSERT INTO audit_log" in rt, "the admin router: Super Admin gate, cpsim work as cpsim_app, audit as the "
          "login role in the same transaction")
    os_ = _code_only(_src("apps/api/services/org_settings.py"))
    check('ADMIN_ROUTER_ONLY_SETTING_KEYS = frozenset({"counterparty_sim.allowed"})' in os_
          and "if key in ADMIN_ROUTER_ONLY_SETTING_KEYS:" in os_,
          "the generic settings writer refuses counterparty_sim.allowed")
    mig = MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists() else ""
    for marker in ("CREATE SCHEMA IF NOT EXISTS cpsim", "CREATE ROLE cpsim_app NOLOGIN NOINHERIT",
                   "GRANT cpsim_app TO app_service WITH INHERIT FALSE, SET TRUE", "counterparties_simulated_chk",
                   "counterparties_slug_live_uq", "counterparty_credentials_prefix_chk", "cpsim.aba_checksum_ok",
                   "cpsim.iban_ok", "counterparty_bank_details_guard_trg", "real_bic_examples",
                   "FUNCTION cpsim.may_connect", "public.crm_is_demo_household", "cpsim_org_setting_guard_trg",
                   "portfolio.securities_global_prices"):
        check(marker in mig, f"docs/cpsim01_part1.sql carries {marker!r}")
    check("DROP " not in re.sub(r"--.*", "", mig), "the migration removes nothing (no DROP)")
    code, out, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, "docs/*.sql", args=("--name-only",))
    migs = [f for f in out.split() if f != "docs/schema_snapshot.sql"]
    check(code == 0 and migs == ["docs/cpsim01_part1.sql"] and "docs/schema_snapshot.sql" in out.split(),
          "cpsim01's own diff adds exactly one migration and refreshes the schema snapshot", out.strip()[:200])
    snap = _src("docs/schema_snapshot.sql")
    check(all(f"-- ===== cpsim.{t} =====" in snap for t in ("counterparties", "counterparty_credentials",
                                                          "counterparty_fix_sessions", "counterparty_bank_details",
                                                          "counterparty_behaviour", "counterparty_clock", "request_log",
                                                          "institution_types")),
          "docs/schema_snapshot.sql lists every cpsim table")
    check("verify_cpsim01.py" in _src("scripts/regression_set.txt").split(),
          "verify_cpsim01.py is in scripts/regression_set.txt (bare name)")
    st = _src("docs/PROJECT_STATUS.md")
    check("cpsim01" in st.lower() and "counterparty_sim.allowed" in st, "PROJECT_STATUS records cpsim01")
    adm = ADMIN_DOC.read_text(encoding="utf-8") if ADMIN_DOC.exists() else ""
    check(all(w in adm for w in ("Create a counterparty", "counterparty_sim.allowed", "may_connect", "SIMULATED",
                                 "cpsim_boundary_check", "99")),
          "docs/cpsim/CPSIM_ADMIN.md: how to create one, each field, the safety rules, the fake ranges")
    des = DESIGN.read_text(encoding="utf-8") if DESIGN.exists() else ""
    check("Counterparty Simulator — design v1" in des, "the design doc is in docs/cpsim/")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


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

import _authz1_common as C  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_roles as VR  # noqa: E402
import _verify_scope as VSC  # noqa: E402

import main  # noqa: E402
from cpsim import db as SDB  # noqa: E402
from cpsim import generators as G  # noqa: E402
from cpsim import mirror as SM  # noqa: E402
from cpsim import registry as R  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.us_business_days import is_business_day  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cpsim01verify"
NAME = "CPSIM01 VERIFY "
SLUG_PREFIX = "cpsimverify-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c951"
FRIDAY = date(2026, 11, 13)
MONDAY = date(2026, 11, 16)
PRE_HOLIDAY = date(2026, 11, 25)       # Wednesday; Thursday 2026-11-26 is Thanksgiving
AFTER_HOLIDAY = date(2026, 11, 27)     # Friday
FIXED_NOW = datetime(2026, 11, 13, 15, 30, 0, tzinfo=timezone.utc)
TYPES = ("altruist", "schwab", "domestic_bank", "international_bank")
SETTING = "counterparty_sim.allowed"
REAL_ABA = "026009593"
REAL_IBAN = "GB82WEST12345698765432"
REAL_BIC = "CHASUS33"


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_T, ORG_T2 = _id(1), _id(2)
N_GENERATED = 1000    # generator sample size per kind (a property of the test, not live state)
# ORG_R: non-test, ROLLED BACK only. fix 1: it must sit OUTSIDE the reserved '99000000-' prefix — REP6FIX2's
# organizations_mark_test_fixture trigger forces is_test = true for every id there, so _id(3) silently became a TEST
# org and the "non-test org" proof read a test org (may_connect correctly said true for plain/mixed/empty). A fixed id
# outside the prefix, asserted in guard_prefix to match no real org, and never committed (the insert rolls back).
ORG_R = UUID("c5130000-0000-4000-8000-0000c9510003")
FIXTURE_ORGS = [ORG_T, ORG_T2]
U_SA, U_OA, U_MEM = _id(10), _id(11), _id(12)
ALL_USERS = [U_SA, U_OA, U_MEM]
E_T, E_T2 = _id(40), _id(41)
H_T, H_T2 = _id(30), _id(31)
E_R_DEMO, E_R_DEMO2, E_R_PLAIN = _id(42), _id(43), _id(44)
H_R_DEMO, H_R_PLAIN, H_R_MIXED, H_R_EMPTY = _id(32), _id(33), _id(34), _id(35)
HOUSEHOLDS = [H_T, H_T2]
USERS = {U_SA: (ORG_T, "super_admin", "sa", "super_admin"), U_OA: (ORG_T, "org_admin", "oa", "org_admin"),
         U_MEM: (ORG_T, "member", "mem", "member")}
ROLE_EXPECT = {U_SA: {"org": ORG_T, "roles": ["super_admin"]},
               U_OA: {"org": ORG_T, "roles": ["org_admin"], "has": ["manage_org_settings"], "lacks": []},
               U_MEM: {"org": ORG_T, "roles": ["member"], "lacks": ["manage_org_settings"]}}
SEED_CHECK_LITERALS = (
    ("cpsim.counterparties", "counterparties_status_chk", ("draft", "active", "disabled")),
    ("cpsim.counterparty_credentials", "counterparty_credentials_kind_chk", ("api_key", "fix_password")),
    ("cpsim.counterparty_bank_details", "counterparty_bank_details_kind_chk", ("domestic", "international")),
    ("cpsim.counterparty_behaviour", "counterparty_behaviour_outcome_chk", ("accept", "reject")),
    ("cpsim.counterparty_clock", "counterparty_clock_reason_chk", ("initial", "advance")),
    ("cpsim.counterparty_fix_sessions", "counterparty_fix_sessions_begin_chk", ("FIX.4.2", "FIX.4.4", "FIXT.1.1")),
    ("cpsim.request_log", "request_log_auth_chk", ("ok", "missing", "not_simulator_key", "unknown_key", "revoked",
                                                  "disabled", "unknown_counterparty", "not_required")),
    ("cpsim.institution_types", "institution_types_protocol_chk", ("rest_oauth", "fix", "bank_wire_ach", "bank_swift")),
)
UNIQUES = ("counterparties_slug_live_uq", "counterparty_credentials_live_uq", "counterparty_fix_sessions_live_uq",
           "counterparty_bank_details_live_uq", "counterparty_behaviour_live_uq", "counterparty_clock_live_uq",
           "institution_types_pkey", "org_settings_org_id_setting_key_key")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "tags"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "org_settings": ("org_id", "setting_key", "setting_value", "category", "is_public"),
    "cpsim.counterparties": ("slug", "name", "institution_type", "is_simulated", "status", "destination_path"),
    "cpsim.counterparty_credentials": ("counterparty_id", "kind", "key_prefix", "key_hash", "salt", "last4"),
    "cpsim.counterparty_bank_details": ("counterparty_id", "kind", "bank_name", "aba_wire", "aba_ach",
                                        "account_number", "bic", "iban", "beneficiary_name", "beneficiary_address"),
}
#: raw-shaped rows the refusal checks insert (the VALID twins); evaluated against every CHECK first
CHECK_ROWS = {
    "cpsim.counterparty_credentials": [{"counterparty_id": str(_id(90)), "kind": "fix_password",
                                        "key_prefix": "cpsim_live_", "key_hash": "a" * 64, "salt": "b" * 32,
                                        "last4": "abcd"}],
    "cpsim.counterparty_bank_details": [{"counterparty_id": str(_id(90)), "kind": "domestic", "bank_name": "CPSIM Twin",
                                         "aba_wire": "991234560", "aba_ach": "991234560",
                                         "account_number": "CPSIM0000000001"}],
}
SEED_NON_COLUMN = {"app.is_super_admin", "app.current_org_id", "true", "active", "individual", "general"}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.roles", "public.role_permissions",
                  "public.entities", "public.households", "public.household_memberships", "public.org_settings",
                  "public.audit_log")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.counterparty_fix_sessions",
                "cpsim.counterparty_bank_details", "cpsim.counterparty_behaviour", "cpsim.counterparty_clock",
                "cpsim.request_log")

S: dict = {"cp": {}, "keys": {}, "fixpw": {}, "issued": [], "audits": [], "health_ids": []}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
VERIFY_CALLS: list[str] = []        # every token main.verify_token was asked to verify
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""),
          flush=True)
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
    """Any issued secret (full value, or its random body) inside ``text``."""
    return [v[:14] + "…" for v in S["issued"] if v in text or v.split("_", 2)[-1] in text]


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
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


# ── fixture scopes / counts ─────────────────────────────────────────────────────────────────────────────────────
def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


FIX_CP = f"(SELECT id FROM cpsim.counterparties WHERE slug LIKE '{SLUG_PREFIX}%')"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS + [ORG_R]), _arr(ALL_USERS)
    hhs = _arr(HOUSEHOLDS + [H_R_DEMO, H_R_PLAIN, H_R_MIXED, H_R_EMPTY])
    ents = _arr([E_T, E_T2, E_R_DEMO, E_R_DEMO2, E_R_PLAIN])
    by_org = f"org_id = ANY({orgs})"
    health = _arr(S["health_ids"]) if S["health_ids"] else "ARRAY[]::uuid[]"
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.entities": f"{by_org} OR id = ANY({ents})",
        "public.households": f"{by_org} OR id = ANY({hhs})",
        "public.household_memberships": f"household_id = ANY({hhs})",
        # VERIFYFIX1's shared fixture-only scope: every counterparty.* row sits in the acting Super Admin's fixture
        # org (asserted in y_admin), so org + user ids cover them (cpsim.* is unreadable from this role by design)
        "public.audit_log": VSC.predicate("public.audit_log", FIXTURE_ORGS + [ORG_R], ALL_USERS),
        "cpsim.counterparties": f"slug LIKE '{SLUG_PREFIX}%'",
        "cpsim.request_log": f"slug LIKE '{SLUG_PREFIX}%' OR counterparty_id IN {FIX_CP} OR id = ANY({health})",
    }.get(table, f"counterparty_id IN {FIX_CP}" if table.startswith("cpsim.") else by_org)


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED_PUBLIC:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    for t in CPSIM_TABLES:
        out[t] = int(await sim(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    return out


async def fingerprint(conn, sql, *args, as_sim=False) -> tuple[int, str]:
    q = (f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS s FROM ({sql}) t")
    row = await (sim(conn, q, *args, fetch="fetchrow") if as_sim else platform(conn, q, *args, fetch="fetchrow"))
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    orgs = [str(o) for o in FIXTURE_ORGS + [ORG_R]]
    out = {
        "counterparty_sim.allowed of every real org": await fingerprint(
            conn, "SELECT org_id, setting_value, updated_at FROM org_settings WHERE setting_key = $1 AND NOT "
                  "(org_id = ANY($2::uuid[]))", SETTING, orgs),
        "org_settings (real)": await fingerprint(
            conn, "SELECT id, setting_key, setting_value FROM org_settings WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "demo-tagged entities (the Olivo demo's included)": await fingerprint(
            conn, "SELECT id, display_name, tags, status FROM entities WHERE EXISTS (SELECT 1 FROM unnest(tags) x "
                  "WHERE x LIKE 'demo-%') AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "households (real)": await fingerprint(
            conn, "SELECT id, name FROM households WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
    }
    for label, sql in (("counterparties (non-fixture)", f"SELECT id, slug, status, system_to FROM cpsim.counterparties "
                                                        f"WHERE slug NOT LIKE '{SLUG_PREFIX}%'"),
                       ("institution_types (seeded 4)", "SELECT key, label, field_groups FROM cpsim.institution_types"),
                       ("real_bic_examples", "SELECT bic FROM cpsim.real_bic_examples")):
        out[label] = await fingerprint(conn, sql, as_sim=True)
    return out


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        users, orgs = list(ALL_USERS), list(FIXTURE_ORGS)
        hhs = list(HOUSEHOLDS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [hhs]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            # the simulator's book has no org_id: by the reserved fixture slug prefix (asserted to match no real row);
            # the FK closure reaches credentials, FIX sessions, bank details, behaviour, clock, request log
            "cpsim.counterparties": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
            "cpsim.request_log": ("slug LIKE $1::text OR id = ANY($2::uuid[])", [SLUG_PREFIX + "%",
                                                                                  list(S["health_ids"])]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               label="verify_cpsim01")
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


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def raw(method, path, *, headers=None, body=None, content=None, timeout=300.0):
    """(status, parsed body, headers) through the REAL app (which mounts the simulator)."""
    kw = {"headers": {"User-Agent": "verify_cpsim01", **(headers or {})}}
    if body is not None:
        kw["json"] = body
    if content is not None:
        kw["content"] = content
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}, {}
        except Exception as exc:  # noqa: BLE001
            print(f"[INFO] {method} {path} raised in the app (a 500):")
            traceback.print_exc()
            return 500, {"raw": f"{type(exc).__name__}: {exc}"[:2000]}, {}
    try:
        data = res.json() if res.content else {}
    except ValueError:
        data = {"raw": res.text[:4000]}
    return res.status_code, data, {k.lower(): v for k, v in res.headers.items()}


async def api(uid, method, path, body=None):
    code, data, _h = await raw(method, path, headers={"Authorization": f"Bearer {token_for(uid)}"}, body=body)
    return code, data


def cpu(key: str, tail: str = "") -> str:
    return f"{_P}/{S['cp'][key]}{tail}"


async def mirror_call(slug, key, body):
    h = {"Authorization": f"Bearer {key}"} if key is not None else {}
    return await raw("POST", f"/cpsim/c/{slug}/mirror", headers=h, body=body)


def envelope(mid="m-1", mtype="test.ping"):
    return {"message_type": mtype, "message_id": mid, "as_of": "2026-11-13T14:00:00Z", "payload": {"amount": "10.00"}}


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
        raise AssertionError(f"verify_cpsim01: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cpsim01: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for fn, nm in ((R.set_today, "set_today"), (SM.set_now, "set_now")):
        have = list(inspect.signature(fn).parameters)
        if have != ["fn"]:
            raise RuntimeError(f"cpsim {nm} signature drifted: {have}")
    S["today"] = FRIDAY
    R.set_today(lambda: S["today"])
    SM.set_now(lambda: FIXED_NOW)


def note_audit(action: str, rid) -> None:
    S["audits"].append((action, str(rid)))


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — the Altruist contract (assumed vs documented per field); notes + reuse inventory")
    c = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
    n_assumed = len(re.findall(r"\|\s*ASSUMED\s*\|", c))
    n_doc = len(re.findall(r"\|\s*DOCUMENTED(?: \([^)|]*\))?\s*\|", c))
    check(n_assumed > 0 and n_doc > 0 and n_assumed > n_doc,
          f"ALTRUIST_CONTRACT.md marks each field ASSUMED ({n_assumed}) or DOCUMENTED ({n_doc})", f"{len(c)} chars")
    for w in ("/oauth/authorize", "/oauth/token", "refresh_token", "households", "accounts", "positions",
              "transactions", "X-Altruist-Signature", "HMAC", "sandbox", "client_credentials"):
        check(w in c, f"the contract covers {w!r}")
    n = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    for w in ("portfolio.securities_global", "portfolio.securities_global_identifiers",
              "portfolio.securities_global_prices", "auth0_jwt_middleware", "require_super_admin", "org_settings",
              "Reuse inventory", "services.us_business_days", "services.bitemporal", "WITH INHERIT FALSE"):
        check(w in n, f"CPSIM01_NOTES.md names {w!r}")
    check("sandbox" in n and "client_credentials" in n,
          "the Task 1 finding is recorded: the environment is keyed 'sandbox' and a naive third environment would "
          "send PRODUCTION credentials")


# fix 1: a `$1::regclass` cast resolves the name INSIDE schema cpsim, which this login correctly has no USAGE on
# (permission denied for schema cpsim). Resolve the relation from the catalog by namespace + relname instead — no
# schema lookup, no privilege needed, and the isolation check stays exactly as strict.
REL_OID = ("(SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
           "WHERE n.nspname = split_part($1::text, '.', 1) AND c.relname = split_part($1::text, '.', 2))")


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
                ok = await sim(conn, f"SELECT ({m.group(1)}) IS NOT FALSE FROM jsonb_populate_record(NULL::{table}, "
                                     "$1::jsonb) AS t", json.dumps(row), fetch="fetchval")
                if ok is not True:
                    refused.append((table, d["conname"], i))
    return n, refused


def seed_literals() -> set[str]:
    src = _code_only(inspect.getsource(seed))
    return set(re.findall(r"'([^'$%{}]+)'", src))


async def y_schema(conn):
    section("[Y] cpsim schema + cpsim_app least privilege (from the catalog); constraints read BEFORE seeding")
    by = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    me = await conn.fetchval("SELECT current_user")
    check(by is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{me} {by}")
    tabs = {r["t"] for r in await platform(conn, "SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON "
                                                 "n.oid = c.relnamespace WHERE n.nspname = 'cpsim' AND c.relkind = 'r'")}
    want = {t.split(".")[1] for t in CPSIM_TABLES} | {"institution_types", "real_bic_examples"}
    check(tabs == want, f"the simulator's {len(want)} tables exist under schema cpsim", f"{sorted(tabs)}")
    pub = [r["t"] for r in await platform(conn, "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN "
                                                "pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND "
                                                "c.relname = ANY($1::text[]) AND n.nspname <> 'cpsim'", sorted(want))]
    check(not pub, "no cpsim table name exists in any other schema", f"{pub}")
    role = await platform(conn, "SELECT rolcanlogin, rolinherit, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = "
                                "'cpsim_app'", fetch="fetchrow")
    check(bool(role) and role["rolcanlogin"] is False and role["rolinherit"] is False and role["rolbypassrls"] is False
          and role["rolsuper"] is False, "role cpsim_app: NOLOGIN, NOINHERIT, no RLS bypass, not superuser",
          f"{dict(role) if role else None}")
    mem = await platform(conn, "SELECT m.rolname, am.inherit_option, am.set_option FROM pg_auth_members am JOIN pg_roles "
                               "r ON r.oid = am.roleid JOIN pg_roles m ON m.oid = am.member WHERE r.rolname = "
                               "'cpsim_app' AND m.rolname = current_user", fetch="fetchrow")
    check(bool(mem) and mem["inherit_option"] is False and mem["set_option"] is True,
          "the app's login role is a member of cpsim_app WITH INHERIT FALSE, SET TRUE", f"{dict(mem) if mem else None}")
    # privileges, read from the catalog: every relation privilege cpsim_app holds anywhere
    privs = {(r["t"], r["p"]) for r in await platform(conn, """
        SELECT n.nspname || '.' || c.relname AS t, a.privilege_type AS p
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        WHERE a.grantee = 'cpsim_app'::regrole AND c.relkind IN ('r', 'v', 'm', 'p', 'S')""")}
    expect = {(f"cpsim.{t}", p) for t in want for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    expect |= {(f"portfolio.{t}", "SELECT") for t in ("securities_global", "securities_global_identifiers",
                                                       "securities_global_prices")}
    check(bool(privs) and privs == expect,
          "cpsim_app's relation privileges are EXACTLY the documented set: SELECT/INSERT/UPDATE/DELETE on the cpsim "
          "tables, SELECT on the three security-master / price tables — nothing on any public table (not even "
          "audit_log)", f"extra={sorted(privs - expect)[:5]} missing={sorted(expect - privs)[:5]}")
    col = int(await platform(conn, "SELECT count(*) FROM information_schema.column_privileges WHERE grantee = "
                                   "'cpsim_app'", fetch="fetchval"))
    sch = {(r["s"], r["p"]) for r in await platform(conn, """
        SELECT n.nspname AS s, a.privilege_type AS p FROM pg_namespace n, aclexplode(n.nspacl) a
        WHERE a.grantee = 'cpsim_app'::regrole""")}
    check(sch == {("cpsim", "USAGE"), ("portfolio", "USAGE")},
          "schema privileges: USAGE on cpsim and portfolio only (no CREATE)", f"{sorted(sch)}")
    others = {(r["t"], r["g"]) for r in await platform(conn, """
        SELECT n.nspname || '.' || c.relname AS t, COALESCE(g.rolname, 'PUBLIC') AS g
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        LEFT JOIN pg_roles g ON g.oid = a.grantee
        WHERE n.nspname = 'cpsim' AND c.relkind = 'r' AND a.grantee <> c.relowner
          AND a.grantee <> 'cpsim_app'::regrole""")}
    check(not others, "no role other than cpsim_app (and the owner) holds any privilege on a cpsim table",
          f"{sorted(others)[:6]}")
    check(col >= 0, "column privileges read", f"{col}")
    app_usage = await conn.fetchval("SELECT has_schema_privilege(current_user, 'cpsim', 'USAGE')")
    _r, err = await rolled_back(conn, lambda c: c.fetchval("SELECT count(*) FROM cpsim.counterparties"), role=None,
                                super_admin=True)
    _r2, err2 = await rolled_back(conn, lambda c: c.fetchval("SELECT count(*) FROM cpsim.counterparties"))
    check(app_usage is False and isinstance(err, asyncpg.InsufficientPrivilegeError) and err2 is None,
          "the app's normal role cannot read cpsim.* (permission denied — even with the Super Admin GUC); the same "
          "connection CAN after SET LOCAL ROLE cpsim_app (the simulator's and the admin router's only path)",
          f"usage={app_usage} without={type(err).__name__ if err else None} with={err2!r}")
    _r3, err3 = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO audit_log (org_id, action) VALUES ($1::uuid, 'cpsim.probe')", str(ORG_T)))
    check(isinstance(err3, asyncpg.InsufficientPrivilegeError),
          "as cpsim_app the simulator cannot write a public table (audit_log insert → permission denied)", f"{err3!r}")
    ex = {r["f"] for r in await platform(conn, """
        SELECT p.proname AS f FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'cpsim' AND has_function_privilege('cpsim_app', p.oid, 'EXECUTE')""")}
    check({"may_connect", "aba_checksum_ok", "iban_ok"} <= ex, "cpsim_app may EXECUTE the cpsim functions", f"{ex}")
    # constraints
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
          f"{got} CHECK constraints read; every literal the run writes into a CHECKed column is one it allows", f"{bad}")
    n_chk, refused = await seed_rows_vs_checks(conn, CHECK_ROWS)
    check(bool(n_chk > 0 and not refused), f"{n_chk} CHECKs evaluated by the database against the valid twin rows — "
          "none refuses", f"{refused}")
    bad_rows = {"cpsim.counterparty_credentials": [dict(CHECK_ROWS["cpsim.counterparty_credentials"][0],
                                                        key_prefix="sk_live_")],
                "cpsim.counterparty_bank_details": [dict(CHECK_ROWS["cpsim.counterparty_bank_details"][0],
                                                         aba_wire=REAL_ABA)]}
    _n, bad2 = await seed_rows_vs_checks(conn, bad_rows)
    check({("cpsim.counterparty_credentials", "counterparty_credentials_prefix_chk", 0),
           ("cpsim.counterparty_bank_details", "counterparty_bank_details_aba_wire_chk", 0)} <= set(bad2),
          "the pre-check is not vacuous: a non-cpsim_ prefix and a real ABA are each refused by the same evaluation",
          f"{bad2}")
    lits = seed_literals()
    vals = {x for _t, _n2, ls in SEED_CHECK_LITERALS for x in ls} | {TAG}
    undeclared = sorted(lits - vals - SEED_NON_COLUMN)
    check(bool(lits) and not undeclared, f"every quoted literal in seed()'s source ({len(lits)}) is a checked CHECK "
          "literal or a declared non-column value", f"{undeclared}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    cons = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype IN ('u', 'p') "
                                                       "AND conname = ANY($1::text[])", list(UNIQUES))}
    have = set(idx) | cons
    check(bool(have == set(UNIQUES) and all("UNIQUE" in v for v in idx.values())),
          f"{len(have)} unique indexes / keys read (one live slug, one live key per kind, one live FIX / bank / "
          "behaviour / clock row) — the run respects each", f"missing={sorted(set(UNIQUES) - have)}")
    fks = [r["conname"] for r in await platform(
        conn, "SELECT conname FROM pg_constraint WHERE contype = 'f' AND connamespace = 'cpsim'::regnamespace")]
    check(len(fks) >= 9, "the cpsim foreign keys are read (children → counterparties, archive self-FK, credential "
          "replacement, FIX password)", f"{len(fks)}")
    snap = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    badc = []
    for t, cs in SEED_COLUMNS.items():
        mm = re.search(rf"^-- ===== {re.escape(t)} =====\n((?:--.*\n)+)", snap, re.M)
        have_c = set(re.findall(r"^--\s{3}(\w+)\s", mm.group(1), re.M)) if mm else set()
        miss = [c for c in cs if c not in have_c]
        if not mm or miss:
            badc.append((t, "absent" if not mm else miss))
    check(bool(SEED_COLUMNS) and not badc, f"every INSERT's columns ({len(SEED_COLUMNS)} tables) exist in "
          "docs/schema_snapshot.sql", f"{badc}")
    S["schema_ok"] = bool(got == len(SEED_CHECK_LITERALS) and not bad and not refused and have == set(UNIQUES)
                          and by is False and not badc and privs == expect)


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE display_name LIKE $1 OR id::text LIKE $2",
                           NAME + "%", f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1", SLUG_PREFIX + "%",
                      fetch="fetchval"))
    lg = int(await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE slug LIKE $1", SLUG_PREFIX + "%",
                       fetch="fetchval"))
    r = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1::uuid", ORG_R, fetch="fetchval"))
    return check(o == 0 and u == 0 and e == 0 and c == 0 and lg == 0 and r == 0,
                 "the reserved prefixes (fixture ids, sign-in tag, 'CPSIM01 VERIFY' names, the 'cpsimverify-' slug, "
                 "the out-of-prefix rolled-back org id) match no real row",
                 f"orgs={o} users={u} entities={e} counterparties={c} logs={lg} org_r={r}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_T, "t"), (ORG_T2, "t2")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CPSIM01 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        member_perms = sorted(C.MEMBER_PERMS)
        missing = sorted((set(member_perms) | {"manage_org_settings"}) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        rid = await rbac.ensure_role(conn, ORG_T, "member", "CPSIM01 verify — member")
        for pn in member_perms:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                               "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CPSIM01 {short.upper()}", sub_of(uid), account_role)
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
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS),
                    "fixture orgs and users are is_test = true (privileged insert path)", f"{len(os_)} orgs {len(us)}")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT,
                                             "(a Super Admin, an org admin holding manage_org_settings, a member)")
    S["seeded"] = bool(ok_test and ok_roles)


# ── registry ────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_registry(conn):
    section("[Y] Registry: the 4 institution types, simulated-only, slug unique, bi-temporal history")
    code, b = await api(U_SA, "GET", _P)
    types = {t["key"]: t for t in (b.get("vocabularies") or {}).get("institution_types", [])}
    check(code == 200 and set(types) == set(TYPES) and b.get("simulated") is True and
          b.get("permissions", {}).get("can_write") is True,
          "GET list (Super Admin): the four seeded institution types come from cpsim.institution_types, with the "
          "envelope", f"{code} {dumps(b)[:300]}")
    S["types"] = types
    for key in TYPES:
        S["today"] = FRIDAY
        code, b = await api(U_SA, "POST", _P, {"name": NAME + key, "institution_type": key,
                                               "slug": SLUG_PREFIX + key.replace("_", "-")})
        issued = (b or {}).get("issued") or {}
        ok = check(code == 200 and str(b.get("id", "")).count("-") == 4 and issued.get("api_key", "").startswith(
            "cpsim_live_") and b.get("shown_once") is True and ("fix_password" in issued) is (key == "schwab"),
            f"create {key}: 200, a cpsim_live_ key shown once (a FIX password only for the FIX type)",
            f"{code} {dumps({k: v for k, v in (b or {}).items() if k != 'issued'})[:300]}")
        if ok:
            S["cp"][key] = b["id"]
            S["keys"][key] = issued["api_key"]
            S["issued"].append(issued["api_key"])
            if "fix_password" in issued:
                S["fixpw"][key] = issued["fix_password"]
                S["issued"].append(issued["fix_password"])
            note_audit("counterparty.created", b["id"])
    rows = await sim(conn, "SELECT c.id, c.slug, c.institution_type, c.is_simulated, c.status, c.destination_path, "
                           "(SELECT count(*) FROM cpsim.counterparty_bank_details d WHERE d.counterparty_id = c.id) AS "
                           "banks, (SELECT count(*) FROM cpsim.counterparty_fix_sessions f WHERE f.counterparty_id = "
                           "c.id) AS fix, (SELECT business_date FROM cpsim.counterparty_clock k WHERE k.counterparty_id "
                           "= c.id AND k.valid_to IS NULL) AS bd FROM cpsim.counterparties c WHERE c.slug LIKE $1 AND "
                           "c.system_to IS NULL", SLUG_PREFIX + "%")
    by = {r["institution_type"]: dict(r) for r in rows}
    check(set(by) == set(TYPES) and all(r["is_simulated"] is True and r["status"] == "draft" and
                                        r["destination_path"] == "/cpsim/c/" + r["slug"] for r in by.values()),
          "each of the 4 fixtures is is_simulated, draft, with destination /cpsim/c/<slug>", f"{rows}")
    check(bool(by) and by.get("schwab", {}).get("fix") == 1 and by.get("domestic_bank", {}).get("banks") == 1 and
          by.get("international_bank", {}).get("banks") == 1 and by.get("altruist", {}).get("banks") == 0 and
          by.get("altruist", {}).get("fix") == 0 and all(r["bd"] == FRIDAY for r in by.values()),
          "each type got exactly the field groups it declares (FIX for schwab, bank details for the two banks); the "
          "clock starts at the injected business date", f"{by}")
    if "altruist" not in S["cp"]:
        return
    cid = S["cp"]["altruist"]
    _r, err = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO cpsim.counterparties (slug, name, institution_type, is_simulated, status, destination_path) "
        "VALUES ($1, 'x', 'altruist', false, 'draft', '/cpsim/c/' || $1)", SLUG_PREFIX + "real"))
    _r2, err2 = await rolled_back(conn, lambda c: c.execute(
        "UPDATE cpsim.counterparties SET is_simulated = false WHERE id = $1::uuid", cid))
    _r3, ok3 = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO cpsim.counterparties (slug, name, institution_type, is_simulated, status, destination_path) "
        "VALUES ($1, 'x', 'altruist', true, 'draft', '/cpsim/c/' || $1)", SLUG_PREFIX + "twin"))
    check(getattr(err, "constraint_name", "") == "counterparties_simulated_chk" and
          getattr(err2, "constraint_name", "") == "counterparties_simulated_chk" and ok3 is None,
          "a NON-simulated counterparty is impossible: insert and update with is_simulated = false are refused by the "
          "CHECK; the same insert with true succeeds (rolled back)", f"{err!r} {err2!r} {ok3!r}")
    code, b = await api(U_SA, "POST", _P, {"name": NAME + "dup", "institution_type": "altruist",
                                           "slug": SLUG_PREFIX + "altruist"})
    _r4, err4 = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO cpsim.counterparties (slug, name, institution_type, status, destination_path) VALUES ($1, 'x', "
        "'altruist', 'draft', '/cpsim/c/' || $1)", SLUG_PREFIX + "altruist"))
    check(code == 409 and getattr(err4, "constraint_name", "") == "counterparties_slug_live_uq",
          "slug unique among live rows: the API answers 409 and the database refuses the duplicate",
          f"{code} {dumps(b)[:200]} {err4!r}")
    code, b = await api(U_SA, "PUT", cpu("altruist"), {"name": NAME + "altruist renamed", "notes": "edited"})
    note_audit("counterparty.edited", cid)
    hist = await sim(conn, "SELECT id, name, system_to, archived_from_id FROM cpsim.counterparties WHERE id = $1::uuid "
                           "OR archived_from_id = $1::uuid ORDER BY system_from", cid)
    live_ = [r for r in hist if r["system_to"] is None]
    arch = [r for r in hist if r["system_to"] is not None]
    check(code == 200 and len(live_) == 1 and str(live_[0]["id"]) == cid and live_[0]["name"].endswith("renamed")
          and len(arch) == 1 and str(arch[0]["archived_from_id"]) == cid and arch[0]["name"] == NAME + "altruist",
          "an edit is SYSTEM-axis bi-temporal: the id is kept, the prior state archived with archived_from_id",
          f"{code} {hist}")
    code, d = await api(U_SA, "GET", cpu("altruist"))
    check(code == 200 and len(d.get("history") or []) == 1 and d.get("destination_url", "").endswith(
        "/cpsim/c/" + SLUG_PREFIX + "altruist") and d.get("simulated") is True,
          "GET detail: the history and the destination URL (<API base>/cpsim/c/<slug>)", f"{code} {dumps(d)[:300]}")
    for key in TYPES:
        if key in S["cp"]:
            code, b = await api(U_SA, "POST", cpu(key, "/activate"))
            if check(code == 200 and b.get("to") == "active", f"activate {key}", f"{code} {dumps(b)[:200]}"):
                note_audit("counterparty.activated", S["cp"][key])


# ── credentials ─────────────────────────────────────────────────────────────────────────────────────────────────
async def y_credentials(conn):
    section("[Y] Credentials: prefix, shown once, hashed, rotate / revoke")
    cid, key = S["cp"]["altruist"], S["keys"]["altruist"]
    rows = await sim(conn, "SELECT * FROM cpsim.counterparty_credentials WHERE counterparty_id = $1::uuid", cid)
    live_ = [dict(r) for r in rows if r["rotated_at"] is None and r["revoked_at"] is None]
    check(len(live_) == 1 and live_[0]["key_hash"] == hashlib.sha256((live_[0]["salt"] + key).encode()).hexdigest()
          and live_[0]["last4"] == key[-4:] and live_[0]["key_prefix"] == "cpsim_live_",
          "stored as sha256(salt ‖ key) + last four + the prefix — the hash re-derives from the issued key", f"{rows}")
    dump = dumps([dict(r) for r in await sim(conn, "SELECT c.*, k.*, b.* FROM cpsim.counterparties c LEFT JOIN "
                                                   "cpsim.counterparty_credentials k ON k.counterparty_id = c.id LEFT "
                                                   "JOIN cpsim.counterparty_bank_details b ON b.counterparty_id = c.id "
                                                   "WHERE c.slug LIKE $1", SLUG_PREFIX + "%")])
    code, d = await api(U_SA, "GET", cpu("altruist"))
    code2, lst = await api(U_SA, "GET", _P)
    leaks = secret_leaks(dump) + secret_leaks(dumps(d)) + secret_leaks(dumps(lst))
    check(code == 200 and code2 == 200 and not leaks,
          "the key is never retrievable again: absent from every cpsim row, the detail and the list (last four only)",
          f"{leaks}")
    good = dict(CHECK_ROWS["cpsim.counterparty_credentials"][0], counterparty_id=cid)

    def ins(prefix):
        return lambda c: c.execute("INSERT INTO cpsim.counterparty_credentials (counterparty_id, kind, key_prefix, "
                                   "key_hash, salt, last4) VALUES ($1::uuid, $2, $3, $4, $5, $6)", good["counterparty_id"],
                                   good["kind"], prefix, good["key_hash"], good["salt"], good["last4"])
    _r, err = await rolled_back(conn, ins("sk_live_"))
    _r2, err2 = await rolled_back(conn, ins("cpsim_live_"))
    check(getattr(err, "constraint_name", "") == "counterparty_credentials_prefix_chk" and err2 is None,
          "a credential without the cpsim_ prefix is refused by the database (the valid twin inserts — rolled back)",
          f"{err!r} {err2!r}")
    st, b, h = await mirror_call(SLUG_PREFIX + "altruist", key, envelope("cred-1"))
    check(st == 200 and b.get("mirrored") is True, "the issued key authenticates", f"{st} {dumps(b)[:200]}")
    code, b = await api(U_SA, "POST", cpu("altruist", "/key/rotate"))
    new = ((b or {}).get("issued") or {}).get("api_key", "")
    if new:
        S["issued"].append(new)
        note_audit("counterparty.key_rotated", cid)
    code_old, bo, _h = await mirror_call(SLUG_PREFIX + "altruist", key, envelope("cred-2"))
    code_new, bn, _h = await mirror_call(SLUG_PREFIX + "altruist", new, envelope("cred-3"))
    check(code == 200 and new.startswith("cpsim_live_") and new != key and code_old == 401 and code_new == 200,
          "rotate: a new key shown once; the OLD key → 401, the new one → 200", f"{code} {code_old} {code_new} {dumps(bo)}")
    code, b = await api(U_SA, "POST", cpu("altruist", "/key/revoke"))
    note_audit("counterparty.key_revoked", cid) if code == 200 else None
    st_rev, br, _h = await mirror_call(SLUG_PREFIX + "altruist", new, envelope("cred-4"))
    check(code == 200 and st_rev == 401 and "revoked" in dumps(br),
          "revoke → the key answers 401 ('rotated or revoked')", f"{code} {st_rev} {dumps(br)}")
    code, b = await api(U_SA, "POST", cpu("altruist", "/key/generate"))
    nk = ((b or {}).get("issued") or {}).get("api_key", "")
    if nk:
        S["issued"].append(nk)
        S["keys"]["altruist"] = nk
        note_audit("counterparty.key_generated", cid)
    st3, _b, _h = await mirror_call(SLUG_PREFIX + "altruist", nk, envelope("cred-5"))
    check(code == 200 and st3 == 200, "a fresh key can be generated after a revoke and works", f"{code} {st3}")
    outs = [r["auth_outcome"] for r in await sim(conn, "SELECT auth_outcome FROM cpsim.request_log WHERE "
                                                       "counterparty_id = $1::uuid", cid)]
    check(outs.count("revoked") >= 2, "the request log records the rotated / revoked refusals as 'revoked'", f"{outs}")


# ── generators ──────────────────────────────────────────────────────────────────────────────────────────────────
def _checksum_valid_99() -> str:
    for n in range(10 ** 6):
        head = "99" + f"{n:06d}"
        for d in "0123456789":
            if G.aba_checksum_ok(head + d):
                return head + d
    raise RuntimeError("no checksum-valid 99 number")


async def y_generators(conn):
    section("[Y] Fake bank / FIX values provably not real; the database refuses real-looking ones")
    rng = random.Random(20261110)
    abas = [G.fake_aba(rng) for _ in range(N_GENERATED)]
    db_any = await sim(conn, "SELECT bool_or(cpsim.aba_checksum_ok(x)) FROM unnest($1::text[]) x", abas,
                       fetch="fetchval")
    check(len(abas) == N_GENERATED and not any(G.aba_checksum_ok(a) for a in abas) and db_any is False and
          all(a.startswith("99") for a in abas) and len(set(abas)) > 900,
          "1,000 generated ABAs: every one 99-prefixed and failing the real check digit (Python AND the database "
          "function agree)", f"{db_any} {len(set(abas))}")
    real_ok = await sim(conn, "SELECT cpsim.aba_checksum_ok($1) AND cpsim.iban_ok($2)", REAL_ABA, REAL_IBAN,
                        fetch="fetchval")
    check(real_ok is True and G.aba_checksum_ok(REAL_ABA) and G.iban_ok(REAL_IBAN),
          "the check functions are not vacuous: a published real ABA and a real IBAN pass them", f"{real_ok}")
    ibans = [G.fake_iban(rng) for _ in range(N_GENERATED)]
    db_iban = await sim(conn, "SELECT bool_or(cpsim.iban_ok(x)) FROM unnest($1::text[]) x", ibans, fetch="fetchval")
    check(all(i[2:4] in ("00", "01", "99") for i in ibans) and not any(G.iban_ok(i) for i in ibans) and
          db_iban is False, "1,000 generated IBANs: check digits 00/01/99 (outside the 02–98 ISO produces) and mod-97 "
          "fails — invalid by construction", f"{db_iban}")
    bics = [G.fake_bic(rng) for _ in range(200)]
    check(all(re.fullmatch(r"CPSMXX[A-Z]0(XXX)?", b) for b in bics), "generated BICs: country XX, test location '…0'",
          f"{bics[:3]}")
    cid = S["cp"].get("altruist") or S["cp"].get("domestic_bank")
    valid99 = _checksum_valid_99()
    twin = G.fake_bank_details("domestic", "Twin")

    def ins_dom(**over):
        d = {**twin, **over}
        return lambda c: c.execute(
            "INSERT INTO cpsim.counterparty_bank_details (counterparty_id, kind, bank_name, aba_wire, aba_ach, "
            "account_number, beneficiary_name, beneficiary_address) VALUES ($1::uuid, 'domestic', $2, $3, $4, $5, $6, "
            "$7)", cid, d["bank_name"], d["aba_wire"], d["aba_ach"], d["account_number"], d["beneficiary_name"],
            d["beneficiary_address"])
    itw = G.fake_bank_details("international", "Twin")

    def ins_int(**over):
        d = {**itw, **over}
        return lambda c: c.execute(
            "INSERT INTO cpsim.counterparty_bank_details (counterparty_id, kind, bank_name, account_number, bic, iban, "
            "beneficiary_name, beneficiary_address) VALUES ($1::uuid, 'international', $2, $3, $4, $5, $6, $7)", cid,
            d["bank_name"], d["account_number"], d["bic"], d["iban"], d["beneficiary_name"], d["beneficiary_address"])
    _a, ok_dom = await rolled_back(conn, ins_dom())
    _b, ok_int = await rolled_back(conn, ins_int())
    _c, e_valid99 = await rolled_back(conn, ins_dom(aba_wire=valid99))
    _d, e_real = await rolled_back(conn, ins_dom(aba_ach=REAL_ABA))
    _e, e_bic = await rolled_back(conn, ins_int(bic=REAL_BIC))
    _f, e_bic11 = await rolled_back(conn, ins_int(bic=REAL_BIC + "XXX"))
    _g, e_iban = await rolled_back(conn, ins_int(iban=REAL_IBAN))
    _h, e_acct = await rolled_back(conn, ins_dom(account_number="40817731205"))
    check(ok_dom is None and ok_int is None, "the valid twins (generated fake domestic + international rows) insert — "
          "so each refusal below is the value's", f"{ok_dom!r} {ok_int!r}")
    check(isinstance(e_valid99, asyncpg.CheckViolationError) and "real ABA check digit" in str(e_valid99),
          f"a 99-prefixed routing number that PASSES the real checksum ({valid99}) is refused (the guard)",
          f"{e_valid99!r}")
    check(isinstance(e_real, asyncpg.CheckViolationError), "a published real ABA is refused", f"{e_real!r}")
    check(isinstance(e_bic, asyncpg.CheckViolationError) and "real BIC" in str(e_bic) and
          isinstance(e_bic11, asyncpg.CheckViolationError) and "real BIC" in str(e_bic11),
          f"a real example BIC ({REAL_BIC}, and its 11-character form) is refused by the seeded-list trigger",
          f"{e_bic!r} {e_bic11!r}")
    check(getattr(e_iban, "constraint_name", "") == "counterparty_bank_details_iban_chk",
          "a valid (real-shaped, correct check digits) IBAN is refused", f"{e_iban!r}")
    check(getattr(e_acct, "constraint_name", "") == "counterparty_bank_details_account_chk",
          "a bare digit account number is refused (CPSIM + digits only)", f"{e_acct!r}")
    pw = S["fixpw"].get("schwab")
    fix = await sim(conn, "SELECT k.key_prefix, k.last4, f.host FROM cpsim.counterparty_fix_sessions f JOIN "
                          "cpsim.counterparty_credentials k ON k.id = f.password_credential_id WHERE f.counterparty_id = "
                          "$1::uuid AND f.valid_to IS NULL", S["cp"].get("schwab"), fetch="fetchrow")
    check(bool(pw and fix) and pw.startswith("cpsim_fixpw_") and fix["key_prefix"] == "cpsim_fixpw_" and
          fix["last4"] == pw[-4:] and fix["host"].endswith(".cpsim.invalid"),
          "the FIX password is generated like the key (cpsim_fixpw_, hashed, last four); the FIX host is under the "
          "reserved .invalid TLD", f"{dict(fix) if fix else None}")
    code, b = await api(U_SA, "POST", cpu("domestic_bank", "/bank/regenerate"))
    if code == 200:
        note_audit("counterparty.bank_regenerated", S["cp"]["domestic_bank"])
    hist = await sim(conn, "SELECT count(*) FILTER (WHERE valid_to IS NULL) AS live, count(*) AS n FROM "
                           "cpsim.counterparty_bank_details WHERE counterparty_id = $1::uuid", S["cp"]["domestic_bank"],
                     fetch="fetchrow")
    check(code == 200 and hist["live"] == 1 and hist["n"] == 2,
          "regenerate bank details: valid-axis restatement (the old row closed, one live row)", f"{code} {dict(hist)}")


# ── sub-app ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_subapp(conn):
    section("[Y] Sub-app: own auth, SIMULATED header, mirror in → mirrored out, malformed 422, request log")
    t0 = await sim(conn, "SELECT now()", fetch="fetchval")
    st, b, h = await raw("GET", "/cpsim/health")
    t1 = await sim(conn, "SELECT clock_timestamp()", fetch="fetchval")
    check(st == 200 and h.get("x-counterparty-simulated") == "true" and b.get("simulated") is True,
          "GET /cpsim/health: 200 with X-Counterparty-Simulated: true — no Hollisworks token needed",
          f"{st} {h.get('x-counterparty-simulated')} {b}")
    S["health_ids"] = [r["id"] for r in await sim(
        conn, "SELECT id FROM cpsim.request_log WHERE path LIKE '%/health' AND counterparty_id IS NULL AND slug IS NULL "
              "AND at BETWEEN $1 AND $2", t0, t1)]
    check(len(S["health_ids"]) >= 1, "the health probe is in the request log (recorded for teardown)",
          f"{S['health_ids']}")
    slug, key = SLUG_PREFIX + "domestic-bank", S["keys"]["domestic_bank"]
    cid = S["cp"]["domestic_bank"]
    bd = await sim(conn, "SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = $1::uuid AND "
                         "valid_to IS NULL", cid, fetch="fetchval")
    env = envelope("mirror-ok-1")
    st, b, h = await mirror_call(slug, key, env)
    check(st == 200 and b.get("message_id") == env["message_id"] and b.get("mirrored") is True and
          b.get("payload") == env["payload"] and b.get("message_type") == env["message_type"] and
          b.get("received_at", "").startswith(bd.isoformat() + "T15:30:00") and h.get("x-counterparty-simulated") ==
          "true", "a properly formatted envelope → the mirrored message (same message_id, payload untouched, "
          "mirrored: true, received_at = the COUNTERPARTY's business date + now)", f"{st} {bd} {dumps(b)}")
    bad_env = {"message_type": "X", "as_of": "yesterday", "payload": [], "extra": 1}
    st, b, h = await mirror_call(slug, key, bad_env)
    paths = {e.get("path") for e in (b.get("errors") or [])}
    check(st == 422 and {"$.message_id", "$.message_type", "$.as_of", "$.payload", "$.extra"} <= paths and
          h.get("x-counterparty-simulated") == "true", "malformed → 422 with the schema errors (missing, pattern, "
          "format, type, additional property)", f"{st} {dumps(b)}")
    st, b, _h = await raw("POST", f"/cpsim/c/{slug}/mirror", headers={"Authorization": f"Bearer {key}",
                                                                       "Content-Type": "application/json"},
                          content=b"{not json")
    check(st == 422 and any(e.get("message") == "body is not JSON" for e in b.get("errors") or []),
          "a body that is not JSON → 422", f"{st} {dumps(b)}")
    st_w, bw, hw = await mirror_call(slug, "cpsim_live_" + "0" * 40, envelope("wrong"))
    st_n, bn, _h = await mirror_call(slug, None, envelope("nokey"))
    st_x, bx, _h = await mirror_call(slug, "sk_live_" + "1" * 40, envelope("notsim"))
    check(st_w == 401 and st_n == 401 and st_x == 401 and hw.get("x-counterparty-simulated") == "true" and
          "not a simulator key" in dumps(bx), "wrong key / no key / a non-cpsim_ value → 401 (a VALID envelope each, "
          "so only the gate refuses)", f"{st_w} {st_n} {st_x}")
    st, b, h = await mirror_call(SLUG_PREFIX + "nope", key, envelope("unknown"))
    check(st == 404 and h.get("x-counterparty-simulated") == "true", "unknown slug → 404 (SIMULATED header still on)",
          f"{st} {dumps(b)}")
    n_before = len(VERIFY_CALLS)
    jwt = token_for(U_SA)
    st_j, bj, _h = await mirror_call(slug, jwt, envelope("jwt"))
    st_c, bc, _h = await raw("POST", f"/cpsim/c/{slug}/mirror", headers={"Cookie": f"appSession={jwt}"},
                             body=envelope("cookie"))
    code_h, _bh = await api(U_SA, "GET", _P)
    check(st_j == 401 and st_c == 401 and len(VERIFY_CALLS) == n_before + 1 and code_h == 200,
          "a Hollisworks JWT or session cookie alone does NOT authenticate to /cpsim (401) — and the Hollisworks token "
          "verifier is never even consulted for a /cpsim path (the same token works on the admin API)",
          f"{st_j} {st_c} verify-calls +{len(VERIFY_CALLS) - n_before} admin={code_h}")
    code, b = await api(U_SA, "POST", cpu("domestic_bank", "/disable"))
    note_audit("counterparty.disabled", cid) if code == 200 else None
    st_d, bd_, _h = await mirror_call(slug, key, envelope("disabled"))
    code2, b2 = await api(U_SA, "POST", cpu("domestic_bank", "/activate"))
    note_audit("counterparty.activated", cid) if code2 == 200 else None
    st_a, _b, _h = await mirror_call(slug, key, envelope("reactivated"))
    check(code == 200 and st_d == 401 and "not active" in dumps(bd_) and code2 == 200 and st_a == 200,
          "a disabled counterparty → 401; re-activated → 200 again", f"{code} {st_d} {code2} {st_a}")
    logs = [dict(r) for r in await sim(conn, "SELECT * FROM cpsim.request_log WHERE counterparty_id = $1::uuid OR slug "
                                             "= $2", cid, SLUG_PREFIX + "nope")]
    outs = Counter(r["auth_outcome"] for r in logs)
    check(outs["ok"] >= 4 and outs["unknown_key"] >= 1 and outs["missing"] >= 1 and outs["not_simulator_key"] >= 2
          and outs["disabled"] >= 1 and outs["unknown_counterparty"] >= 1 and
          any(r["shape_valid"] is False and r["status"] == 422 and r["error_count"] >= 5 for r in logs) and
          any(r["shape_valid"] is True and r["status"] == 200 and r["message_type"] == "test.ping" for r in logs),
          "request_log: one row per request — counterparty, path, status, auth outcome, shape-valid, error count, "
          "message type", f"{dict(outs)}")
    cols = {r["column_name"] for r in await platform(conn, "SELECT column_name FROM information_schema.columns WHERE "
                                                           "table_schema = 'cpsim' AND table_name = 'request_log'")}
    text_ = dumps(logs)
    check(not ({"body", "payload", "headers", "authorization"} & cols) and "cpsim_live_" not in text_ and
          not secret_leaks(text_) and jwt not in text_, "request_log rows carry no body and no key (no such column; "
          "no key or token text in any row)", f"{sorted(cols)}")


# ── clock ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_clock(conn):
    section("[Y] Clock: business-day advance (Friday → Monday; over a US holiday), audited")
    S["today"] = FRIDAY
    code, b = await api(U_SA, "POST", _P, {"name": NAME + "clock fri", "institution_type": "domestic_bank",
                                           "slug": SLUG_PREFIX + "clock-fri"})
    S["today"] = PRE_HOLIDAY
    code2, b2 = await api(U_SA, "POST", _P, {"name": NAME + "clock hol", "institution_type": "altruist",
                                             "slug": SLUG_PREFIX + "clock-hol"})
    S["today"] = FRIDAY
    if code != 200 or code2 != 200:
        check(False, "clock fixtures created", f"{code} {dumps(b)[:200]} {code2} {dumps(b2)[:200]}")
        return
    for bb in (b, b2):
        S["issued"] += list((bb.get("issued") or {}).values())
        note_audit("counterparty.created", bb["id"])
    S["cp"]["clock-fri"], S["cp"]["clock-hol"] = b["id"], b2["id"]
    check(FRIDAY.weekday() == 4 and is_business_day(PRE_HOLIDAY) and not is_business_day(date(2026, 11, 26)),
          "the injected dates are what they claim (a Friday; the eve of Thanksgiving)")
    code, a = await api(U_SA, "POST", f"{_P}/{b['id']}/clock/advance")
    note_audit("counterparty.clock_advanced", b["id"]) if code == 200 else None
    check(code == 200 and a.get("from") == FRIDAY.isoformat() and a.get("to") == MONDAY.isoformat(),
          "advance from Friday 2026-11-13 → Monday 2026-11-16", f"{code} {dumps(a)}")
    code, a2 = await api(U_SA, "POST", f"{_P}/{b2['id']}/clock/advance")
    note_audit("counterparty.clock_advanced", b2["id"]) if code == 200 else None
    check(code == 200 and a2.get("from") == PRE_HOLIDAY.isoformat() and a2.get("to") == AFTER_HOLIDAY.isoformat(),
          "advance from Wednesday 2026-11-25 over Thanksgiving → Friday 2026-11-27", f"{code} {dumps(a2)}")
    hist = await sim(conn, "SELECT business_date, reason, valid_to FROM cpsim.counterparty_clock WHERE counterparty_id = "
                           "$1::uuid ORDER BY valid_from", b["id"])
    check(len(hist) == 2 and hist[0]["valid_to"] is not None and hist[1]["valid_to"] is None and
          [r["reason"] for r in hist] == ["initial", "advance"], "the clock keeps its history (valid-axis restatement)",
          f"{hist}")
    aud = await platform(conn, "SELECT payload FROM audit_log WHERE action = 'counterparty.clock_advanced' AND "
                               "resource_id = ANY($1::uuid[])", [b["id"], b2["id"]])
    pays = [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"] for r in aud]
    check(len(pays) == 2 and {(p.get("from"), p.get("to")) for p in pays} == {
        (FRIDAY.isoformat(), MONDAY.isoformat()), (PRE_HOLIDAY.isoformat(), AFTER_HOLIDAY.isoformat())},
          "each advance is audited (counterparty.clock_advanced, from → to)", f"{pays}")
    st, m, _h = await mirror_call(SLUG_PREFIX + "clock-fri", (b.get("issued") or {}).get("api_key"), envelope("c1"))
    check(st == 401, "a draft counterparty does not answer (401) even with its own key — activation is explicit",
          f"{st}")


# ── safety ──────────────────────────────────────────────────────────────────────────────────────────────────────
async def may(conn, org, hh=None):
    return await sim(conn, "SELECT cpsim.may_connect($1::uuid, $2::uuid)", str(org), str(hh) if hh else None,
                     fetch="fetchval")


async def y_safety(conn):
    section("[Y] Safety: setting default off, may_connect rule, the generic path refused, boundary check")
    n = int(await platform(conn, "SELECT count(*) FROM org_settings WHERE setting_key = $1 AND org_id = ANY($2::uuid[])",
                           SETTING, FIXTURE_ORGS, fetch="fetchval"))
    code, oa = await api(U_SA, "GET", f"{_P}/org-access")
    row = next((r for r in (oa.get("rows") or []) if r.get("id") == str(ORG_T)), None)
    m0 = await may(conn, ORG_T)
    check(n == 0 and code == 200 and row is not None and row.get("allowed") is False and oa.get("default") is False
          and m0 is False, "counterparty_sim.allowed defaults OFF (no row); may_connect(test org) = false without it",
          f"{n} {code} {row} {m0}")
    code, b = await api(U_OA, "PUT", f"/api/v1/orgs/{ORG_T}/settings/{SETTING}", {"value": True})
    n2 = int(await platform(conn, "SELECT count(*) FROM org_settings WHERE setting_key = $1 AND org_id = $2::uuid",
                            SETTING, ORG_T, fetch="fetchval"))
    code_ok, _b = await api(U_OA, "PUT", f"/api/v1/orgs/{ORG_T}/settings/brand.footer_text", {"value": "x"})
    check(code == 403 and n2 == 0, "an org admin cannot switch it through the generic settings path (403; nothing "
          "written)", f"{code} {dumps(b)[:200]} {n2} (generic path for another key → {code_ok})")
    if code_ok == 200:
        await platform(conn, "DELETE FROM org_settings WHERE org_id = $1::uuid AND setting_key = 'brand.footer_text'",
                       ORG_T)
    _r, err = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES ($1::uuid, $2, "
        "'true'::jsonb, 'general', false)", str(ORG_T), SETTING), role=None, org=ORG_T)
    _r2, ok2 = await rolled_back(conn, lambda c: c.execute(
        "INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES ($1::uuid, $2, "
        "'true'::jsonb, 'general', false)", str(ORG_T), SETTING), role=None, super_admin=True)
    check(isinstance(err, asyncpg.InsufficientPrivilegeError) and "Super Admin" in str(err) and ok2 is None,
          "the database refuses the setting without app.is_super_admin (an org-scoped write), accepts it with it "
          "(both rolled back)", f"{err!r} {ok2!r}")
    code, b = await api(U_SA, "PUT", f"{_P}/org-access/{ORG_T}", {"allowed": True})
    note_audit("counterparty.org_setting_changed", ORG_T) if code == 200 else None
    m1, m_hh, m_other = await may(conn, ORG_T), await may(conn, ORG_T, H_T), await may(conn, ORG_T, H_T2)
    m_t2 = await may(conn, ORG_T2)
    check(code == 200 and b.get("allowed") is True and m1 is True and m_hh is True and m_other is False and
          m_t2 is False, "with the flag (Super Admin, audited): a TEST org may connect freely (and for its own "
          "household); never for another org's household; another org without the flag stays false",
          f"{code} {m1} {m_hh} {m_other} {m_t2}")

    async def nontest(c):
        await c.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, false)", ORG_R,
                        "CPSIM01 Verify Org R (rolled back)", f"{TAG}-r")
        for eid, nm, tags in ((E_R_DEMO, "demo a", ["demo-cpsimverify"]), (E_R_DEMO2, "demo b", ["demo-cpsimverify"]),
                              (E_R_PLAIN, "plain", [])):
            await c.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, tags) VALUES ($1, $2, "
                            "$3, 'active', 'individual'::entity_type, $4::text[])", eid, ORG_R, NAME + nm, tags)
        for hh, members in ((H_R_DEMO, (E_R_DEMO, E_R_DEMO2)), (H_R_PLAIN, (E_R_PLAIN,)),
                            (H_R_MIXED, (E_R_DEMO, E_R_PLAIN)), (H_R_EMPTY, ())):
            await c.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, ORG_R, NAME + "hh")
            for e in members:
                await c.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, e)
        is_test = await c.fetchval("SELECT is_test FROM organizations WHERE id = $1", ORG_R)
        await c.execute("SET LOCAL ROLE cpsim_app")      # the simulator's path to the function
        out = {"is_test": is_test, "off": await c.fetchval("SELECT cpsim.may_connect($1::uuid, $2::uuid)", str(ORG_R),
                                                           str(H_R_DEMO))}
        await c.execute("SET LOCAL ROLE NONE")           # back to the login role (+ Super Admin GUC) for the setting
        await c.execute("INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public) VALUES "
                        "($1, $2, 'true'::jsonb, 'counterparty_sim', false)", ORG_R, SETTING)
        await c.execute("SET LOCAL ROLE cpsim_app")
        for k, hh in (("none", None), ("demo", H_R_DEMO), ("plain", H_R_PLAIN), ("mixed", H_R_MIXED),
                      ("empty", H_R_EMPTY), ("foreign", H_T)):
            out[k] = await c.fetchval("SELECT cpsim.may_connect($1::uuid, $2::uuid)", str(ORG_R),
                                      str(hh) if hh else None)
        return out
    res, err = await rolled_back(conn, nontest, role=None, super_admin=True)
    check(err is None and res is not None and res["is_test"] is False and res["off"] is False and res["none"] is False
          and res["demo"] is True and res["plain"] is False and res["mixed"] is False and res["empty"] is False and
          res["foreign"] is False,
          "a NON-test org (rolled back): even with the flag on, only a household whose members are ALL demo-tagged "
          "may connect — not the org at large, a plain, mixed or empty household, or another org's", f"{res} {err!r}")
    left = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1::uuid", ORG_R, fetch="fetchval"))
    check(left == 0, "the non-test fixture org and its demo rows were never committed", f"{left}")
    code, out, err2 = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=600)
    check(code == 0 and "CPSIM BOUNDARY: OK" in out, "cpsim_boundary_check passes on the repo",
          f"{code} {(out + err2)[-400:]}")
    planted = API_DIR / "services" / f"_cpsim_planted_{os.getpid()}.py"
    try:
        planted.write_text('PLANTED = "SELECT * FROM cpsim.counterparties"\n', encoding="utf-8")
        code2, out2, err3 = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=600)
    finally:
        planted.unlink(missing_ok=True)
    check(code2 == 1 and f"B1 apps/api/services/{planted.name}:1" in out2 and not planted.exists(),
          "…and FAILS on a planted violation (a temp file referencing cpsim.counterparties from services/), which is "
          "removed again", f"{code2} {(out2 + err3)[-300:]}")


# ── admin ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_admin(conn):
    section("[Y] Admin API: Super Admin only (org admin and member → 403, valid bodies), every action audited")
    cid = S["cp"]["altruist"]
    calls = [("GET", _P, None), ("POST", _P, {"name": NAME + "refused", "institution_type": "altruist",
                                             "slug": SLUG_PREFIX + "refused"}),
             ("GET", f"{_P}/org-access", None), ("PUT", f"{_P}/org-access/{ORG_T}", {"allowed": False}),
             ("GET", f"{_P}/{cid}", None), ("PUT", f"{_P}/{cid}", {"name": NAME + "refused"}),
             ("POST", f"{_P}/{cid}/activate", None), ("POST", f"{_P}/{cid}/disable", None),
             ("POST", f"{_P}/{cid}/key/generate", None), ("POST", f"{_P}/{cid}/key/rotate", None),
             ("POST", f"{_P}/{cid}/key/revoke", None), ("POST", f"{_P}/{cid}/bank/regenerate", None),
             ("PUT", f"{_P}/{cid}/fix", {"heartbt_int": 45}), ("PUT", f"{_P}/{cid}/behaviour",
                                                               {"default_outcome": "accept"}),
             ("POST", f"{_P}/{cid}/clock/advance", None), ("GET", f"{_P}/{cid}/requests", None),
             ("POST", f"{_P}/{cid}/test-message", {"key": S["keys"]["altruist"]})]
    check(len(calls) == len(ROUTES), "every admin route is exercised", f"{len(calls)} vs {len(ROUTES)}")
    before = await counts(conn)
    bad = []
    for uid in (U_OA, U_MEM):
        for m, p, body in calls:
            code, b = await api(uid, m, p, body)
            if code != 403:
                bad.append((USERS[uid][2], m, p, code, dumps(b)[:120]))
    after = await counts(conn)
    check(not bad, f"an org admin and a member get 403 on all {len(calls)} admin routes", f"{bad[:4]}")
    check(after == before, "…and nothing changed (every fixture table at the same count)",
          f"{ {k: (before[k], after[k]) for k in before if before[k] != after[k]} }")
    code, b = await api(U_SA, "PUT", f"{_P}/{cid}/behaviour", {"default_outcome": "reject", "reject_reason": "closed",
                                                               "ach_return_code": "R01", "ach_return_after_days": 2,
                                                               "webhooks_enabled": True})
    note_audit("counterparty.behaviour_updated", cid) if code == 200 else None
    beh = await sim(conn, "SELECT default_outcome, reject_reason, ach_return_code, webhooks_enabled FROM "
                          "cpsim.counterparty_behaviour WHERE counterparty_id = $1::uuid AND valid_to IS NULL", cid,
                    fetch="fetchrow")
    check(code == 200 and beh is not None and beh["default_outcome"] == "reject" and beh["ach_return_code"] == "R01"
          and beh["webhooks_enabled"] is True, "behaviour stored (reject with reason, ACH return R01 after 2 days, "
          "webhooks on)", f"{code} {dumps(b)[:200]} {dict(beh) if beh else None}")
    # fix 1: PUT /behaviour is a SPARSE update (exclude_unset, merged onto the live row), and the PUT above stored
    # reject_reason='closed' — so a bare {"default_outcome": "reject"} merged into a VALID row (200). Clearing the
    # reason explicitly makes the merged row a reject with no reason; the database CHECK must still refuse it.
    code, b = await api(U_SA, "PUT", f"{_P}/{cid}/behaviour", {"default_outcome": "reject", "reject_reason": None})
    check(code == 400 and "counterparty_behaviour_reject_chk" in dumps(b), "a reject without a reason is refused (400 — the database CHECK, named)",
          f"{code} {dumps(b)[:200]}")
    code, b = await api(U_SA, "PUT", f"{_P}/{S['cp']['schwab']}/fix", {"begin_string": "FIXT.1.1",
                                                                       "rotate_password": True})
    pw = ((b or {}).get("issued") or {}).get("fix_password", "")
    if pw:
        S["issued"].append(pw)
        note_audit("counterparty.fix_updated", S["cp"]["schwab"])
    check(code == 200 and pw.startswith("cpsim_fixpw_") and pw != S["fixpw"].get("schwab"),
          "FIX session edited (FIXT.1.1) and its password rotated (shown once)", f"{code} {dumps(b)[:200]}")
    code, b = await api(U_SA, "PUT", f"{_P}/{cid}/fix", {"heartbt_int": 45})
    check(code == 400, "an institution type without a FIX session refuses FIX edits (400)", f"{code}")
    code, b = await api(U_SA, "POST", f"{_P}/{cid}/test-message", {"key": S["keys"]["altruist"]})
    if code == 200:
        note_audit("counterparty.test_message", cid)
    check(code == 200 and b.get("status") == 200 and (b.get("reply") or {}).get("mirrored") is True and
          b.get("simulated_header") == "true" and not secret_leaks(dumps(b)),
          "Send test message: the sample envelope goes to the mirror in-process and the reply comes back (no network, "
          "the key not echoed)", f"{code} {dumps(b)[:300]}")
    code, b = await api(U_SA, "GET", f"{_P}/{cid}/requests?limit=5")
    check(code == 200 and 0 < len(b.get("rows") or []) <= 5 and b.get("limit") == 5,
          "the request log, last N", f"{code} {len(b.get('rows') or [])}")
    code, b = await api(U_SA, "PUT", f"{_P}/org-access/{ORG_T}", {"allowed": False})
    note_audit("counterparty.org_setting_changed", ORG_T) if code == 200 else None
    check(code == 200 and await may(conn, ORG_T) is False, "the setting switched back off (audited)", f"{code}")
    ids = sorted({r for _a, r in S["audits"]})
    rows = await platform(conn, "SELECT action, resource_id::text AS rid, payload::text AS p FROM audit_log WHERE "
                                "action LIKE 'counterparty.%' AND resource_id = ANY($1::uuid[])", ids)
    got = Counter((r["action"], r["rid"]) for r in rows)
    want = Counter(S["audits"])
    check(bool(want) and got == want, f"every action is audited exactly once ({sum(want.values())} counterparty.* rows, "
          "counted by fixture ids)", f"missing={dict(want - got)} extra={dict(got - want)}")
    leaks = secret_leaks(dumps([r["p"] for r in rows]))
    check(not leaks and all('"simulated": true' in r["p"] or r["action"] == "counterparty.org_setting_changed"
                            for r in rows), "no audit payload carries a key or password; each marks simulated",
          f"{leaks}")
    org_of = {r["org_id"] for r in await platform(conn, "SELECT DISTINCT org_id FROM audit_log WHERE action LIKE "
                                                        "'counterparty.%' AND resource_id = ANY($1::uuid[])", ids)}
    check(org_of == {ORG_T}, "the audit rows sit in the acting Super Admin's own org", f"{org_of}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py / visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        if not (HERE / script).exists():
            continue
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=600)
        check(code == 0 and "CURRENT" in out, f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")
    self_static(check, find)


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) == NODE_TEST_COUNT and
          n.group(1) == p.group(1) and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the cpsim01 render tests ({NODE_TEST}, counted by their file: menu, banner, list, wizard per type, fail "
          f"closed, shown once, detail tabs, tab content, send test message, org access) all pass "
          f"({n.group(1) if n else '?'}/{NODE_TEST_COUNT})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1800)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c2, o2, e2 = run_cmd([str(API_DIR / "venv" / "bin" / "python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "cpsim/tests"], API_DIR, timeout=600)
    check(c2 == 0 and re.search(r"\b11 passed\b", o2) is not None, "the generator unit tests pass (cpsim/tests, 11)",
          f"{c2} {(o2 + e2)[-300:]}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_authz1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    t, out, err, code = VT.run_nested(HERE / "verify_authz1.py", cwd=REPO, timeout=2700,
                                      env_extra={"SKIP_BUILD": "1"})
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(t.ok) and not fails, "verify_authz1.py: 0 FAIL (its own final TOTAL line and exit code)",
          f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


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


FIXTURE_SECTIONS = (("registry", y_registry), ("credentials", y_credentials), ("generators", y_generators),
                    ("subapp", y_subapp), ("clock", y_clock), ("safety", y_safety), ("admin", y_admin))
ONLY_NEEDS = {"credentials": ("registry",), "generators": ("registry",), "subapp": ("registry",),
              "clock": (), "safety": (), "admin": ("registry",), "registry": ()}


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
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        chosen = [(n, f) for n, f in FIXTURE_SECTIONS if only is None or n in only]
        if S.get("seeded"):
            for name, fn in chosen:
                need = [x for x in ONLY_NEEDS.get(name, ()) if x == "registry"]
                if need and len(S["cp"]) < len(TYPES):
                    check(False, f"{name}: not run — the registry fixtures were not all created (never skipped "
                                 "silently)")
                    continue
                await run(name, fn, conn)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if only is None:
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows "
                "unchanged; no counterparty left")
        R.set_today(None)
        SM.set_now(None)
        await close_app_pools()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (orgs, users, grants, households, settings, audit rows, every "
                  "cpsim table, the request log)", f"{left}")
            ncp = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1",
                                SLUG_PREFIX + "%", fetch="fetchval"))
            check(ncp == 0, "no fixture counterparty exists after teardown", f"{ncp}")
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
        check(False, "the verify crashed", f"{type(exc).__name__}: {exc}")
        print(total_line())
        rc = 1
    sys.exit(rc)
