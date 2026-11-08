"""verify_s29b — WORKFLOW MANAGER WAVE 4: scheduled / event-triggered workflows run WRITE steps unattended, under S30's
segregation of duties — plus Task 0, the self-grant floor ON everywhere.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_s29b.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a seeding exception included, with the
traceback) and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are
hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network (the httpx transport refuses and records), NO model call (the AI helpers refuse and record). REAL: the app
through an ASGI transport, services.workflow_autonomy, the workflow engine and the scheduler's fire path, the domain
event publisher, services.sod / sod_admin, the guard triggers, RLS and the live database. The script's own connection
does not bypass RLS (rolbypassrls = false, asserted). TIME IS INJECTED: services.workflow_autonomy's clock is a fixed
instant (T0, then T0 + 2 h for the rate-limit window) and every scheduled fire names a fixed occurrence.

FIXTURES: two orgs 99000000-0000-0000-0000-0000c29b0001 / 0002 (is_test = true, asserted); users seeded with real
user_roles grants — the app's decision on each asserted:
  org A  U_A1 admin (TWO logins), U_A2 admin, U_OWN trigger owner (author / configure / view), U_REV reviewer
         (review_agent_proposals + author_workflows), U_V viewer (view_workflow_runs), U_N no workflow key,
         U_SA1 / U_SA2 Super Admins, two PENDING rows linked to U_A1's and U_SA1's persons
  org B  U_B1 admin, U_B2 admin
Fixture verbs (registered for the run, never synced to a catalog): eligible / capped / never / failing / kill-switch
writers whose handler writes ONE marker row to audit_log (the target table the shadow proof counts).
Reserved prefixes (the id range, the sign-in tag 's29bverify', role names 's29b_*', workflow names 'S29B VERIFY%',
marker action 's29bverify.%') are checked absent from real rows first.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN (sod.teardown = 'fixture'), keyed by this
run's fixture ids (the platform kill-switch rows by their fixture Super Admin maker); every touched table re-read for
its exact pre-test count, counted BY FIXTURE IDS ONLY (live activity writes audit_log, member_todos, sod_decisions and
the workflow tables).

--self-checks-only: git / route / static self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and
the leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
--sections a,b,… (S29B FIX 1): the seed, then only the named fixture sections (FIXTURE_SECTIONS names, plus 'real' /
'routes'), then the full teardown and pre-test-count checks; no front end and no nested regression.
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
from datetime import datetime, timedelta, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "s29b"
BEFORE_REF = "5f460af"   # s29b-work's base: 'merge s30'
_R = "any(manage_org_settings|author_workflows|view_workflow_runs)"
ROUTES = {
    ("GET", "/api/v1/admin/workflow-autonomy"): _R,
    ("POST", "/api/v1/admin/workflow-autonomy/settings"): "manage_org_settings",
    ("POST", "/api/v1/admin/workflow-autonomy/versions/{version_id}"): "author_workflows",
    ("POST", "/api/v1/admin/workflow-autonomy/settings/{setting_id}/decide"): "any(manage_org_settings|author_workflows)",
    ("POST", "/api/v1/admin/workflow-autonomy/runs/{run_id}/shadow-review"):
        "any(configure_workflow_triggers|author_workflows)",
    ("POST", "/api/v1/admin/workflow-autonomy/platform-kill-switch"): "super_admin",
}
OWN_FILES = ("apps/api/services/workflow_autonomy.py", "apps/api/routers/workflow_autonomy.py")
NODE_TEST = "tests/s29b/s29b.test.mjs"
DISCOVERY = REPO / "docs" / "S29B_DISCOVERY.md"
AUTONOMY_DOC = REPO / "docs" / "WORKFLOW_AUTONOMY.md"


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def self_routes(check, find):
    """s29b's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed,
          "s29b changed or removed no existing route's gate", f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at s29b's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "s29b's routes all still exist today with the same gate", f"{drift[:4]}")


def self_static(check, find):
    """Static markers, matched by NAME: the engine routes non-Tier-1 steps through the one decision; the verbs table
    in the discovery doc lists every class the code declares; own files make no model / network call."""
    eng = _src("apps/api/services/workflow_engine.py")
    drive = eng.split("async def _drive(")[1].split("\nasync def ")[0] if "async def _drive(" in eng else ""
    check("_run_service_step(" in drive and "SUSPEND_TIER" in drive and "return executed, step_key" in drive,
          "workflow_engine._drive: Tier 1 suspends first (unchanged), every other Service Task goes through "
          "_run_service_step (the autonomy decision)")
    wa = _src("apps/api/services/workflow_autonomy.py")
    declared = dict(re.findall(r'^\s{4}"([a-z0-9_.]+)": \{"class": (NEVER|ELIGIBLE_WITH_CAP|ELIGIBLE)', wa, re.M))
    doc = DISCOVERY.read_text(encoding="utf-8") if DISCOVERY.exists() else ""
    want = {"NEVER": "never", "ELIGIBLE": "eligible", "ELIGIBLE_WITH_CAP": "eligible_with_cap"}
    missing = [k for k, c in declared.items() if not re.search(rf"\|\s*`{re.escape(k)}`\s*\|.*\|\s*{want[c]}\s*\|", doc)]
    check(len(declared) > 10 and not missing, f"docs/S29B_DISCOVERY.md 1a lists every one of the {len(declared)} "
          "classed verbs with the class the code declares", f"{missing[:6]}")
    for sec in ("1a", "1b", "1c", "1d"):
        check(re.search(rf"^##+ {sec}\b", doc, re.M) is not None, f"docs/S29B_DISCOVERY.md has section {sec}")
    adoc = AUTONOMY_DOC.read_text(encoding="utf-8") if AUTONOMY_DOC.exists() else ""
    check("cash stage C" in adoc and "counsel" in adoc, "docs/WORKFLOW_AUTONOMY.md states money movement is excluded "
          "until counsel (cash stage C)")
    for f in OWN_FILES:
        src = _src(f)
        check(bool(src) and not re.search(r"call_claude|^\s*(import|from)\s+(httpx|requests|anthropic|litellm)\b|"
                                          r"httpx\.|requests\.", src, re.M),
              f"{f}: no model call and no network client", f"{len(src)} chars")
    rs = _src("scripts/regression_set.txt")
    check("verify_s29b.py" in rs, "verify_s29b.py is in scripts/regression_set.txt")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
import _verify_total as VT  # noqa: E402

import main  # noqa: E402
from services import agent_proposals as AP  # noqa: E402
from services import domain_events as DE  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import rbac  # noqa: E402
from services import sod  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services import workflow_engine as ENG  # noqa: E402
from services import workflow_nl_generator as G  # noqa: E402
from services import workflow_scheduler as SCHED  # noqa: E402
from services.action_registry import REGISTRY, AssistantAction  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.workflow_steps_deriver import derive_and_store_steps  # noqa: E402

TAG = "s29bverify"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c29b"
T0 = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)   # the injected instant
CLOCK = {"now": T0}
MARKER = "s29bverify.marker"


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_A1, U_A2, U_OWN, U_REV, U_V, U_N, U_SA1, U_SA2, U_B1, U_B2 = (_id(10 + i) for i in range(10))
U_PEND1, U_PEND_SA = _id(30), _id(31)       # PENDING rows linked to U_A1's / U_SA1's persons (approve = a grant)
PSET, PSET2, PROFILE, PROFILE2 = _id(60), _id(61), _id(62), _id(63)
SECOND_SUB = f"auth0|{TAG}-a1-second-login"

V_ELIG = "s29bverify.eligible_write"
V_CAP = "s29bverify.capped_write"
V_NEVER = "s29bverify.money_write"
V_FAIL = "s29bverify.failing_write"
V_KILL = "s29bverify.org_kill_mid_run"
V_PKILL = "s29bverify.platform_kill_mid_run"
FIXTURE_VERBS = (V_ELIG, V_CAP, V_NEVER, V_FAIL, V_KILL, V_PKILL)

# workflow fixtures: name → (definition id, version id, trigger id, [(verb, diagram tier), …])
WF = {
    "elig": (_id(100), _id(101), _id(102), [(V_ELIG, 3)]),
    "two": (_id(110), _id(111), _id(112), [(V_ELIG, 3), (V_ELIG, 3)]),
    "cap": (_id(120), _id(121), _id(122), [(V_CAP, 3)]),
    "fail": (_id(130), _id(131), _id(132), [(V_FAIL, 3)]),
    "kill": (_id(140), _id(141), _id(142), [(V_KILL, 3), (V_ELIG, 3)]),
    "pkill": (_id(150), _id(151), _id(152), [(V_PKILL, 3), (V_ELIG, 3)]),
    "never": (_id(160), _id(161), _id(162), [(V_NEVER, 3)]),
    "t1": (_id(170), _id(171), _id(172), [(V_ELIG, 1)]),
    "evt": (_id(180), _id(181), _id(182), [(V_ELIG, 3)]),
}
EVENT_TYPE = "human_task.completed"

ADMIN_PERMS = {"manage_org_settings", "manage_members", "author_workflows", "configure_workflow_triggers",
               "view_workflow_runs", "review_agent_proposals", "view_audit_log"}
ROLE_PERMS = {
    "s29b_admin": ADMIN_PERMS,
    # the owner also holds review_agent_proposals, so its refusal as checker is the S30 maker rule, not a missing key
    "s29b_owner": {"author_workflows", "configure_workflow_triggers", "view_workflow_runs", "review_agent_proposals"},
    "s29b_reviewer": {"review_agent_proposals", "author_workflows", "view_workflow_runs"},
    "s29b_viewer": {"view_workflow_runs"},
    "s29b_none": {"view_audit_log"},
}
# uid → (org, users.role mirror, short, role granted, full name)
USERS = {
    U_A1: (ORG_A, "member", "a1", "s29b_admin", "S29B Pat Two-Logins"),
    U_A2: (ORG_A, "member", "a2", "s29b_admin", "S29B Admin Two"),
    U_OWN: (ORG_A, "member", "own", "s29b_owner", "S29B Trigger Owner"),
    U_REV: (ORG_A, "member", "rev", "s29b_reviewer", "S29B Reviewer"),
    U_V: (ORG_A, "member", "v", "s29b_viewer", "S29B Viewer"),
    U_N: (ORG_A, "member", "n", "s29b_none", "S29B No Workflow Key"),
    U_SA1: (ORG_A, "super_admin", "sa1", "super_admin", "S29B Super Admin One"),
    U_SA2: (ORG_A, "super_admin", "sa2", "super_admin", "S29B Super Admin Two"),
    U_B1: (ORG_B, "member", "b1", "s29b_admin", "S29B Org B Admin"),
    U_B2: (ORG_B, "member", "b2", "s29b_admin", "S29B Org B Admin Two"),
}
PENDING = {U_PEND1: (U_A1, "pend1"), U_PEND_SA: (U_SA1, "pendsa")}
ALL_USERS = [*USERS, *PENDING]
COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.user_identities", "public.user_person_links", "public.permission_sets", "public.user_permission_sets",
    "public.permission_set_permissions", "public.profiles", "public.profile_permissions",
    "public.sod_rules", "public.sod_decisions", "public.audit_log", "public.member_todos", "public.agent_proposals",
    "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps", "public.workflow_runs",
    "public.workflow_run_steps", "public.workflow_triggers", "public.workflow_autonomy_settings",
    "public.domain_events", "public.domain_event_deliveries",
)
#: catalog literals the seeds and the API bodies rely on — read back from the CHECK constraints before seeding
SEED_CHECK_LITERALS = (
    ("public.workflow_autonomy_settings", "was_key_chk",
     ("autonomy", "kill_switch", "limits", "shadow_runs_required", "version_autonomy")),
    ("public.workflow_autonomy_settings", "was_status_chk", ("draft", "active", "rejected", "retired")),
    ("public.workflow_run_steps", "workflow_run_steps_autonomy_mode_chk",
     ("shadow", "unattended", "platform_job", "held_for_person")),
    ("public.workflow_runs", "workflow_runs_shadow_review_chk", ("clean", "not_clean")),
    ("public.domain_event_deliveries", "domain_event_deliveries_status_check",
     ("DELIVERED", "FAILED", "STARTING", "RATE_LIMITED")),
    ("public.workflow_triggers", "workflow_triggers_trigger_type_check", ("event", "scheduled")),
    ("public.sod_rules", "sod_rules_kind_chk", ("floor", "default", "policy", "org")),
)
UNIQUES = ("was_active_uq", "was_draft_uq", "workflow_run_steps_idempotency_uq",
           "domain_event_deliveries_event_version_uq", "domain_events_source_dedupe_uq", "sod_rules_active_uq",
           "workflow_steps_workflow_version_id_step_key_key", "users_auth0_sub_key")

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
CALLS: dict[str, int] = {}
CALL_ACTORS: dict[str, list[str]] = {}
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
        return None, (exc.stdout or "") if isinstance(exc.stdout, str) else "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


def sub_of(uid) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid) -> str:
    short = USERS[uid][2] if uid in USERS else PENDING[uid][1]
    return f"{TAG}-{short}@test.local"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)[:700]
    except Exception:  # noqa: BLE001
        return str(o)[:700]


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
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


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(v) + "'" for v in vals) + "]::uuid[]"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS), _arr(ALL_USERS)
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.organizations": f"id = ANY({orgs})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.user_permission_sets": f"user_id = ANY({users}) OR granted_by = ANY({users}) OR permission_set_id IN "
                                       f"(SELECT id FROM public.permission_sets WHERE {by_org})",
        "public.user_identities": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.sod_rules": f"org_id = ANY({orgs}) OR drafted_by = ANY({users}) OR checker_id = ANY({users})",
        "public.sod_decisions": f"org_id = ANY({orgs}) OR checker_id = ANY({users})",
        "public.user_person_links": f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR person_user_id = ANY({users})",
        "public.workflow_autonomy_settings": f"org_id = ANY({orgs}) OR maker_id = ANY({users}) OR "
                                             f"checker_id = ANY({users})",
    }.get(table, by_org)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
            for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Real rows this verify must never change: the platform SoD catalog + rules (none drafted by a fixture user),
    every real org's own rules, and every real autonomy setting (platform or org) not made by a fixture user."""
    users = [str(u) for u in ALL_USERS]
    orgs = [str(o) for o in FIXTURE_ORGS]
    return {
        "sod_item_types": await fingerprint(conn, "SELECT * FROM public.sod_item_types"),
        "platform_rules": await fingerprint(
            conn, "SELECT * FROM public.sod_rules WHERE org_id IS NULL AND NOT (COALESCE(drafted_by::text, '') = "
                  "ANY($1::text[]))", users),
        "real_org_rules": await fingerprint(
            conn, "SELECT * FROM public.sod_rules WHERE org_id IS NOT NULL AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real_autonomy_settings": await fingerprint(
            conn, "SELECT * FROM public.workflow_autonomy_settings WHERE NOT (COALESCE(org_id::text, '') = "
                  "ANY($1::text[])) AND NOT (COALESCE(maker_id::text, '') = ANY($2::text[]))", orgs, users),
    }


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


TEARDOWN_PLAN_SQL = {
    "public.sod_rules": ("org_id = ANY($1::uuid[]) OR drafted_by = ANY($2::uuid[]) OR checker_id = ANY($2::uuid[])", 2),
    "public.sod_decisions": ("org_id = ANY($1::uuid[]) OR checker_id = ANY($2::uuid[])", 2),
    "public.workflow_autonomy_settings": ("org_id = ANY($1::uuid[]) OR maker_id = ANY($2::uuid[]) OR "
                                          "checker_id = ANY($2::uuid[])", 2),
    "public.user_person_links": ("user_id = ANY($1::uuid[]) OR person_user_id = ANY($1::uuid[])", 1),
    "public.user_roles": ("user_id = ANY($1::uuid[])", 1),
    # S29B FIX 1 — no org_id on this table, so the shared org sweep never reaches it: by fixture user (holder or
    # grantor) and by fixture permission set (the sets live in the fixture orgs)
    "public.user_permission_sets": ("user_id = ANY($2::uuid[]) OR granted_by = ANY($2::uuid[]) OR permission_set_id IN "
                                    "(SELECT id FROM public.permission_sets WHERE org_id = ANY($1::uuid[]))", 2),
    "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", 2),
    "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", 2),
}


async def teardown() -> list[str]:
    """Child-first by the shared helper (sod.teardown = 'fixture'), keyed by this run's fixture ids; the platform
    kill-switch rows (org_id NULL) are planned by their fixture Super Admin maker."""
    op = await operator_conn()
    try:
        users = list(ALL_USERS)
        plan = {t: (sql, [FIXTURE_ORGS, users] if n == 2 else [users]) for t, (sql, n) in TEARDOWN_PLAN_SQL.items()}
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("sod.teardown",), label="verify_s29b")
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
    users = list(ALL_USERS)
    for t, (sql, k) in TEARDOWN_PLAN_SQL.items():
        n += int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {sql}",
                                *([FIXTURE_ORGS, users] if k == 2 else [users]), fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                            users, f"%{TAG}%", fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM user_identities WHERE auth0_sub LIKE $1", f"%{TAG}%",
                            fetch="fetchval"))
    n += int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                            fetch="fetchval"))
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


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid, sub: str | None = None) -> str:
    c = {"sub": sub or sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, params=None, sub=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid, sub)}", "User-Agent": "verify_s29b"}}
    if body is not None:
        kw["json"] = body
    if params:
        kw["params"] = params
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}
    raw = res.content.decode("utf-8", "replace")
    try:
        return res.status_code, json.loads(raw or "{}")
    except ValueError:
        return res.status_code, {"raw": raw[:4000]}


class _ctx:
    """The task's RLS contextvars. ENTER IT BEFORE ``pool.acquire()`` — the wrapped pool applies the GUCs at acquire."""

    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def _marker(pool, org_id, user_id, run_id, key):
    async with pool.acquire() as c:
        await c.execute("INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
                        "($1::uuid, $2::uuid, $3, 'workflow_run', $4::uuid, $5::jsonb)", str(org_id),
                        str(user_id) if user_id else None, MARKER, str(run_id) if run_id else None,
                        json.dumps({"verb": key}))


def _make_handler(key: str):
    async def _handler(*, pool, user_id, org_id, run_context, workflow_run_id, **_):
        CALLS[key] = CALLS.get(key, 0) + 1
        CALL_ACTORS.setdefault(key, []).append(str(user_id))
        if key == V_FAIL:
            raise RuntimeError("s29bverify: the fixture write failed on purpose")
        if key == V_KILL:   # turns the ORG kill switch on mid-run (a committed setting, its own connection)
            async with pool.acquire() as c:
                await WA.change_setting(c, org_id=org_id, actor_id=str(U_A1), is_super_admin=False,
                                        key=WA.KEY_KILL, value=True, reason="verify_s29b: stop mid-run")
        if key == V_PKILL:  # the PLATFORM kill switch, by a fixture Super Admin
            with _ctx(org_id, True):
                async with pool.acquire() as c:
                    await WA.change_setting(c, org_id=org_id, actor_id=str(U_SA1), is_super_admin=True,
                                            key=WA.KEY_KILL, value=True, reason="verify_s29b: stop everywhere",
                                            platform=True)
        await _marker(pool, org_id, user_id, workflow_run_id, key)
        return {"data": {"called_for": key, "count": CALLS[key]}, "text": None}
    return _handler


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()
    register_all()   # ASGITransport skips the app lifespan, where the registry is normally filled
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_s29b: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_s29b: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    G.call_claude_text = refuse_model("workflow_nl_generator.call_claude_text")

    async def _hermetic_sync_catalog(pool, org_id):
        return None

    REGISTRY.sync_catalog = _hermetic_sync_catalog
    have = set(AssistantAction.__dataclass_fields__)
    if not {"duty", "autonomy_class"} <= have:
        raise RuntimeError(f"AssistantAction lacks duty / autonomy_class: {sorted(have)}")
    # the handler signature the engine calls (services.workflow_engine._execute_service_task) — the stub matches it
    eng_src = inspect.getsource(ENG._execute_service_task)
    for kw in ("pool=pool", "user_id=actor_id", "run_context=", "workflow_run_id="):
        if kw not in eng_src:
            raise RuntimeError(f"_execute_service_task no longer passes {kw!r} — the fixture handler would drift")
    for key, klass, tier in ((V_ELIG, WA.ELIGIBLE, 2), (V_CAP, WA.ELIGIBLE_WITH_CAP, 2), (V_NEVER, WA.NEVER, 2),
                             (V_FAIL, WA.ELIGIBLE, 2), (V_KILL, WA.ELIGIBLE, 2), (V_PKILL, WA.ELIGIBLE, 2)):
        REGISTRY.register(AssistantAction(
            key=key, module="s29bverify", description="verify_s29b fixture — one marker row per call",
            access_type="write", required_permission=None, tier=tier, reversible=False, render_target="inline",
            handler=_make_handler(key), workflow_invocable=True, duty="write", autonomy_class=klass))
    WA.VERB_AUTONOMY[V_NEVER] = {"class": WA.NEVER, "money": True, "writes": "verify fixture: a money row"}
    WA.VERB_AUTONOMY[V_CAP] = {"class": WA.ELIGIBLE_WITH_CAP, "writes": "verify fixture: N rows",
                               "quantity": lambda ctx: ctx["quantity"]}
    for k in (V_ELIG, V_FAIL, V_KILL, V_PKILL):
        WA.VERB_AUTONOMY[k] = {"class": WA.ELIGIBLE, "writes": "verify fixture: one audit_log marker row"}
    WA.set_clock(lambda: CLOCK["now"])


def remove_fixture_verbs():
    for k in FIXTURE_VERBS:
        REGISTRY._actions.pop(k, None)
        WA.VERB_AUTONOMY.pop(k, None)
    WA.set_clock(None)


# ═══ Fixture workflows ══════════════════════════════════════════════════════════════════════════════════════════
def _bpmn(name: str, steps: list[tuple[str, int]]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>'
             '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
             f'xmlns:twoa="http://2ndactcapital.com/bpmn/ext" id="D_s29b_{name}" '
             'targetNamespace="http://2ndactcapital.com/bpmn">'
             f'<bpmn:process id="proc_s29b_{name}" isExecutable="true">'
             '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f0</bpmn:outgoing></bpmn:startEvent>']
    for i, (verb, tier) in enumerate(steps, start=1):
        parts.append(f'<bpmn:serviceTask id="Svc_{i}" name="S29B {name} step {i}"><bpmn:extensionElements>'
                     f'<twoa:governance actionRegistryKey="{verb}" autonomyTier="{tier}"/></bpmn:extensionElements>'
                     f'<bpmn:incoming>f{i - 1}</bpmn:incoming><bpmn:outgoing>f{i}</bpmn:outgoing></bpmn:serviceTask>')
    n = len(steps)
    parts.append(f'<bpmn:endEvent id="End_1"><bpmn:incoming>f{n}</bpmn:incoming></bpmn:endEvent>')
    parts.append('<bpmn:sequenceFlow id="f0" sourceRef="Start_1" targetRef="Svc_1"/>')
    for i in range(1, n + 1):
        parts.append(f'<bpmn:sequenceFlow id="f{i}" sourceRef="Svc_{i}" targetRef="'
                     f'{"Svc_" + str(i + 1) if i < n else "End_1"}"/>')
    parts.append('</bpmn:process></bpmn:definitions>')
    return "".join(parts)


async def runs_of(conn, ver) -> list[dict]:
    return [dict(r) for r in await scoped(conn, ORG_A, "SELECT id, status, autonomy_mode, error_detail FROM "
                                                       "workflow_runs WHERE workflow_version_id = $1 ORDER BY "
                                                       "started_at, id", ver)]


async def steps_of(conn, run_id) -> list[dict]:
    return [dict(r) for r in await scoped(
        conn, ORG_A, "SELECT rs.id, ws.step_key, rs.status, rs.autonomy_mode, rs.delegate_for::text AS delegate_for, "
                     "rs.idempotency_key, rs.result, rs.error_detail, rs.agent_proposal_id FROM workflow_run_steps rs "
                     "JOIN workflow_steps ws ON ws.id = rs.workflow_step_id WHERE rs.workflow_run_id = $1 "
                     "ORDER BY ws.step_key", run_id)]


async def markers(conn, org=ORG_A) -> tuple[int, str]:
    return await fingerprint(conn, "SELECT id, user_id, resource_id, payload FROM public.audit_log WHERE org_id = $1 "
                                   "AND action = $2", org, MARKER)


_FIRE_N = {"n": 0}


async def fire(name) -> dict:
    """A scheduled fire of the fixture trigger through the REAL scheduler fire path, at a FIXED occurrence."""
    d, v, t, _steps = WF[name]
    _FIRE_N["n"] += 1
    pool = await get_pool()
    trig = {"id": t, "org_id": ORG_A, "workflow_version_id": v, "created_by": U_OWN}
    try:
        res = await SCHED._fire(pool, trigger=trig, occurrence_utc=T0 + timedelta(minutes=_FIRE_N["n"]))
        return {"ok": True, **res}
    except Exception as exc:  # noqa: BLE001 — a hold re-raises with the run id
        return {"ok": False, "run_id": getattr(exc, "held_run_id", None), "error": f"{type(exc).__name__}: {exc}"}


async def start_direct(name, extra: dict) -> dict:
    d, v, t, _steps = WF[name]
    pool = await get_pool()
    with _ctx(ORG_A):
        try:
            res = await ENG.start_workflow_run(pool, v, ORG_A, {"trigger_id": str(t), "trigger_type": "scheduled",
                                                               **extra}, U_OWN)
            return {"ok": True, **res}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "run_id": getattr(exc, "held_run_id", None), "error": f"{type(exc).__name__}: {exc}"}


async def set_org(uid, key, value, *, approver=None, sub=None) -> tuple[int, dict]:
    """Ask for an org setting through the API; when it is a loosening draft and an approver is named, decide it."""
    code, b = await api(uid, "POST", "/api/v1/admin/workflow-autonomy/settings",
                        {"key": key, "value": value, "reason": f"verify_s29b: {key}"}, sub=sub)
    st = b.get("setting") or {}
    if code == 201 and st.get("status") == "draft" and approver is not None:
        c2, b2 = await api(approver, "POST", f"/api/v1/admin/workflow-autonomy/settings/{st['id']}/decide",
                           {"outcome": "approve"})
        return c2, b2
    return code, b


async def version_state(uid, name, state) -> tuple[int, dict]:
    return await api(uid, "POST", f"/api/v1/admin/workflow-autonomy/versions/{WF[name][1]}",
                     {"state": state, "reason": f"verify_s29b: {name} → {state}"})


async def opt_in(conn, name) -> bool:
    """shadow → N clean shadow runs (owner-reviewed) → U_A1 asks → U_REV signs off."""
    c1, _b = await version_state(U_OWN, name, "shadow")
    st = await scoped(conn, ORG_A, "SELECT setting_value FROM workflow_autonomy_settings WHERE org_id = $1 AND "
                                   "setting_key = 'shadow_runs_required' AND status = 'active' AND valid_to IS NULL",
                      ORG_A, fetch="fetchval")
    n = int(json.loads(st)) if st else WA.DEFAULT_SHADOW_RUNS
    ok = c1 == 201
    for _ in range(n):
        r = await fire(name)
        c, _b = await api(U_OWN, "POST", f"/api/v1/admin/workflow-autonomy/runs/{r.get('run_id')}/shadow-review",
                          {"outcome": "clean"})
        ok = ok and r.get("status") == "completed" and c == 200
    c3, b3 = await version_state(U_A1, name, "on")
    sid = (b3.get("setting") or {}).get("id")
    c4, b4 = await api(U_REV, "POST", f"/api/v1/admin/workflow-autonomy/settings/{sid}/decide", {"outcome": "approve"})
    return ok and c3 == 201 and c4 == 200


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — discovery 1a–1d (docs/S29B_DISCOVERY.md) and the eligibility list")
    doc = DISCOVERY.read_text(encoding="utf-8") if DISCOVERY.exists() else ""
    for sec in ("1a", "1b", "1c", "1d"):
        check(re.search(rf"^##+ {sec}\b", doc, re.M) is not None, f"docs/S29B_DISCOVERY.md has section {sec}")
    real = [a for a in REGISTRY.all() if a.module != "s29bverify"]
    missing = [a.key for a in real if not re.search(rf"\|\s*`{re.escape(a.key)}`\s*\|", doc)]
    wrong = [a.key for a in real if re.search(rf"\|\s*`{re.escape(a.key)}`\s*\|", doc) and not re.search(
        rf"\|\s*`{re.escape(a.key)}`\s*\|.*\|\s*{WA.autonomy_class(a)}\s*\|", doc)]
    check(bool(real) and not missing and not wrong, f"the 1a table lists every one of the {len(real)} registered verbs "
          "with the class the registry holds", f"missing={missing[:5]} wrong={wrong[:5]}")


async def y_schema(conn):
    section("[Y] Schema: constraints and unique indexes read from the catalog BEFORE seeding")
    got = 0
    bad = []
    for table, name, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2", table, name, fetch="fetchval")
        if d is None:
            bad.append((table, name, "absent"))
            continue
        got += 1
        miss = [x for x in lits if f"'{x}'" not in d]
        if miss:
            bad.append((table, name, miss))
    check(got == len(SEED_CHECK_LITERALS) and not bad, f"{got} CHECK constraints read; every literal the seeds use is "
          "one the database allows", f"{bad}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(len(idx) == len(UNIQUES) and all("UNIQUE" in v for v in idx.values()),
          f"{len(idx)} unique indexes read (one active / one draft setting, one claim per run-step, one run per event "
          "per version, …)", f"missing={sorted(set(UNIQUES) - set(idx))}")
    fks = int(await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY($1::"
                                   "regclass[])", ["public.workflow_autonomy_settings", "public.workflow_run_steps",
                                                   "public.domain_event_deliveries"], fetch="fetchval"))
    check(fks > 0, "FK constraints read for the touched tables", f"{fks}")
    cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE tablename = "
                                                   "'workflow_autonomy_settings'")}
    check(cmds >= {"SELECT", "INSERT", "UPDATE", "DELETE"}, "workflow_autonomy_settings: an RLS policy for every "
          "operation", f"{sorted(cmds)}")
    byp = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(byp is False, "the verify's own connection does NOT bypass RLS (rolbypassrls = false)", f"{byp}")
    S["schema_ok"] = got == len(SEED_CHECK_LITERALS) and not bad and len(idx) == len(UNIQUES) and byp is False


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    r = int(await platform(conn, "SELECT count(*) FROM roles WHERE name LIKE 's29b\\_%'", fetch="fetchval"))
    w = int(await platform(conn, "SELECT count(*) FROM workflow_definitions WHERE name LIKE 'S29B VERIFY%'",
                           fetch="fetchval"))
    m = int(await platform(conn, "SELECT count(*) FROM audit_log WHERE action LIKE 's29bverify.%'", fetch="fetchval"))
    return check(o == 0 and u == 0 and r == 0 and w == 0 and m == 0,
                 "the reserved prefixes (fixture id range, sign-in tag, role names 's29b_*', workflow names, marker "
                 "action) match no real row", f"orgs={o} users={u} roles={r} workflows={w} markers={m}")


async def check_fixture_roles(conn) -> bool:
    """Each fixture user's role AS THE APP DECIDES IT (user_roles → services.authz Principal)."""
    from services import authz as _AZ

    bad = []
    keys = set().union(*ROLE_PERMS.values())
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, _acct, _nm, rname, _full) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            pr = await _AZ.load_principal(conn, uid)
            want_super = rname == "super_admin"
            perms = set(getattr(pr, "permissions", ()) or ())
            ok = rname in names and pr is not None and pr.admitted and bool(pr.is_super_admin) is want_super
            if not want_super:
                ok = ok and (perms & keys) == ROLE_PERMS[rname]
            if not ok:
                bad.append((str(uid), names, bool(pr and pr.is_super_admin), sorted(perms & keys)[:12]))
        for uid in PENDING:
            pr = await _AZ.load_principal(conn, uid)
            if pr is not None and pr.admitted:
                bad.append((str(uid), "a pending row is admitted"))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (two Super Admins; each "
                 "fixture role holds exactly its keys; the pending rows hold nothing)", f"{bad}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"S29B Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*ROLE_PERMS.values()) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in FIXTURE_ORGS:
            for rname, ps in ROLE_PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"S29B verify — {rname}")
                for p in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        for uid, (org, account_role, _nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        await conn.execute("INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via) "
                           "VALUES ($1, $2, $3, 'https://verify.invalid/', $4, 'operator')",
                           U_A1, ORG_A, SECOND_SUB, f"{TAG}-a1-second@test.local")
        for pid, (person, short) in PENDING.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) VALUES "
                "($1, $2, $3, 'S29B pending second row', $4, 'member', 'pending', true, true)", pid, ORG_A,
                email_of(pid), f"auth0|{TAG}-{short}")
            await conn.execute("INSERT INTO user_person_links (user_id, person_user_id, org_id, linked_via) "
                               "VALUES ($1, $2, $3, 'operator')", pid, person, ORG_A)
        for ps, nm in ((PSET, "held"), (PSET2, "other")):
            await conn.execute("INSERT INTO permission_sets (id, org_id, name, description) VALUES ($1, $2, $3, $4)",
                               ps, ORG_A, f"S29B verify set {nm}", "verify_s29b fixture")
            await conn.execute("INSERT INTO permission_set_permissions (org_id, permission_set_id, permission_key) "
                               "VALUES ($1, $2, 'view_audit_log')", ORG_A, ps)
        for pr, nm in ((PROFILE, "held"), (PROFILE2, "other")):
            await conn.execute("INSERT INTO profiles (id, org_id, name, description, is_seed) VALUES ($1, $2, $3, $4, "
                               "false)", pr, ORG_A, f"S29B verify profile {nm}", "verify_s29b fixture")
            await conn.execute("INSERT INTO profile_permissions (org_id, profile_id, permission_key) "
                               "VALUES ($1, $2, 'view_audit_log')", ORG_A, pr)
        # the actors HOLD the 'held' set and profile (a bundle edit by a holder is a self-grant)
        # S29B FIX 1 — the live table is (user_id, permission_set_id, granted_at, granted_by): no org_id (the org is the
        # permission set's). granted_by is a fixture user (U_A2 — another person, as a real grant would be).
        for uid in (U_A1, U_SA1):
            await conn.execute("INSERT INTO user_permission_sets (user_id, permission_set_id, granted_by) VALUES ($1, "
                               "$2, $3)", uid, PSET, U_A2)
            await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", uid, PROFILE)
        for name, (d, v, _t, steps) in WF.items():
            xml = _bpmn(name, steps)
            await conn.execute("INSERT INTO workflow_definitions (id, org_id, name, description, created_by) VALUES "
                               "($1, $2, $3, 'verify_s29b fixture', $4)", d, ORG_A, f"S29B VERIFY {name}", U_A1)
            await conn.execute("INSERT INTO workflow_versions (id, workflow_definition_id, org_id, version_number, "
                               "bpmn_xml, change_summary, is_current, created_by) VALUES ($1, $2, $3, 1, $4, "
                               "'s29b fixture', true, $5)", v, d, ORG_A, xml, U_A1)
            await derive_and_store_steps(conn, v, ORG_A, xml)
    seeded = list(ALL_USERS)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test", seeded)}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(seeded) and os_ == set(FIXTURE_ORGS), "fixture orgs and users (pending rows included) are "
          "is_test = true", f"{len(us)}/{len(seeded)} users, {len(os_)}/2 orgs")
    # scheduled triggers, created through the REAL route by the owner — INACTIVE, so no real cron tick fires them
    bad = []
    for name, (d, _v, t, _s) in WF.items():
        if name in ("evt", "never"):
            continue
        code, b = await api(U_OWN, "POST", "/api/v1/admin/workflow-triggers", {
            "workflow_definition_id": str(d), "trigger_type": "scheduled", "schedule_cron": "0 9 * * 1",
            "timezone": "UTC", "is_active": False})
        if code != 201:
            bad.append((name, code, dumps(b)))
            continue
        # the fixture trigger id is the one the route returned; WF keeps it for the fires
        WF[name] = (d, WF[name][1], UUID(b["id"]), WF[name][3])
    check(not bad, "each fixture workflow's scheduled trigger is created through the route by its owner (inactive)",
          f"{bad}")
    S["seeded"] = await check_fixture_roles(conn) and us == set(seeded) and os_ == set(FIXTURE_ORGS) and not bad


# ── Task 0 ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def y_floor(conn):
    section("[Y] Task 0 — self-grant floor: ON everywhere, every grant path, every login, Super Admin included")
    pol = await platform(conn, "SELECT version_no, enforce_floor FROM sod_rules WHERE org_id IS NULL AND rule_key = "
                               "'policy:role_grant' AND status = 'active' AND valid_to IS NULL", fetch="fetchrow")
    hist = await platform(conn, "SELECT count(*) FROM sod_rules WHERE org_id IS NULL AND rule_key = 'policy:role_grant' "
                                "AND status = 'retired' AND enforce_floor = false", fetch="fetchval")
    check(pol is not None and pol["enforce_floor"] is True and pol["version_no"] >= 2 and int(hist) >= 1,
          "the live platform policy:role_grant is a NEW version with the floor ON (v1, floor off, kept as history)",
          f"{dict(pol or {})} retired_off={hist}")
    roles = {r["name"]: str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT id, name FROM roles WHERE org_id = $1",
                                                           ORG_A)}

    async def state(uid):
        return await platform(conn, "SELECT (SELECT array_agg(role_id::text ORDER BY role_id) FROM user_roles WHERE "
                                    "user_id = $1) AS roles, (SELECT profile_id::text FROM users WHERE id = $1) AS prof, "
                                    "(SELECT array_agg(permission_set_id::text ORDER BY permission_set_id) FROM "
                                    "user_permission_sets WHERE user_id = $1) AS sets, (SELECT status FROM users WHERE "
                                    "id = $2) AS pend, (SELECT array_agg(permission_key ORDER BY permission_key) FROM "
                                    "profile_permissions WHERE profile_id = $3) AS pp, (SELECT array_agg("
                                    "permission_key ORDER BY permission_key) FROM permission_set_permissions WHERE "
                                    "permission_set_id = $4) AS sp", uid, PENDING_OF[uid], PROFILE, PSET,
                              fetch="fetchrow")

    PENDING_OF = {U_A1: U_PEND1, U_SA1: U_PEND_SA}
    paths = (
        ("role", lambda me: ("PUT", f"/api/v1/admin/users/{me}/role", {"role_id": roles["s29b_viewer"]})),
        ("profile", lambda me: ("PUT", f"/api/v1/admin/users/{me}/profile", {"profile_id": str(PROFILE2)})),
        ("permission set", lambda me: ("POST", f"/api/v1/admin/permission-sets/{PSET2}/users", {"user_id": str(me)})),
        ("profile permission", lambda me: ("PUT", f"/api/v1/admin/profiles/{PROFILE}/permissions",
                                           {"permission_key": "view_workflow_runs", "granted": True})),
        ("permission-set permission", lambda me: ("PUT", f"/api/v1/admin/permission-sets/{PSET}/permissions",
                                                  {"permission_key": "view_workflow_runs", "granted": True})),
        ("approve own pending login", lambda me: ("POST", f"/api/v1/admin/users/{PENDING_OF[me]}/approve",
                                                  {"role_id": roles["s29b_viewer"]})),
    )
    actors = (("an org admin", U_A1, None), ("a SECOND login of the same person", U_A1, SECOND_SUB),
              ("a Super Admin", U_SA1, None))
    n_ref0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_decisions WHERE org_id = $1 AND item_type = "
                                           "'role_grant' AND outcome = 'refused'", ORG_A, fetch="fetchval"))
    a_ref0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                           "'sod.refused'", ORG_A, fetch="fetchval"))
    n = 0
    for who, uid, sub in actors:
        for pname, mk in paths:
            before = dict(await state(uid))
            method, path, body = mk(uid)
            code, b = await api(uid, method, path, body, sub=sub)
            after = dict(await state(uid))
            n += 1
            check(code == 403 and "egregation of duties" in dumps(b) and after == before,
                  f"self-grant via '{pname}' by {who}: 403 by the floor, nothing changed",
                  f"{code} {dumps(b)} {before} → {after}")
    n_ref = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_decisions WHERE org_id = $1 AND item_type = "
                                          "'role_grant' AND outcome = 'refused'", ORG_A, fetch="fetchval"))
    a_ref = int(await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = 'sod.refused'",
                             ORG_A, fetch="fetchval"))
    check(n_ref - n_ref0 == n, f"every one of the {n} refusals is a row in sod_decisions (append-only)",
          f"{n_ref0} → {n_ref}")
    check(a_ref - a_ref0 == n, f"… and an audit_log row each ('sod.refused')", f"{a_ref0} → {a_ref}")
    code, rep = await api(U_A2, "GET", "/api/v1/admin/sod/reports/refusals")
    rows = [r for r in (rep.get("rows") or []) if r.get("item_type") == "role_grant"]
    check(code == 200 and len(rows) >= n, "they show in Admin → Segregation of duties → refused checks",
          f"{code} {len(rows)}")
    # another person's grant is allowed
    code, b = await api(U_A2, "PUT", f"/api/v1/admin/users/{U_V}/role", {"role_id": roles["s29b_owner"]})
    has = await platform(conn, "SELECT EXISTS (SELECT 1 FROM user_roles WHERE user_id = $1 AND role_id = $2::uuid)",
                         U_V, roles["s29b_owner"], fetch="fetchval")
    check(code == 200 and has is True, "another person's grant is allowed (re-read)", f"{code} {dumps(b)}")
    code, _b = await api(U_A2, "PUT", f"/api/v1/admin/users/{U_V}/role", {"role_id": roles["s29b_viewer"]})
    check(code == 200, "… and the viewer is restored to their own role", f"{code}")
    code, b = await api(U_A2, "POST", f"/api/v1/admin/users/{U_PEND1}/approve", {"role_id": roles["s29b_viewer"]})
    st = await platform(conn, "SELECT status FROM users WHERE id = $1", U_PEND1, fetch="fetchval")
    check(code == 200 and st == "active", "a DIFFERENT person admits a pending login (another person's grant)",
          f"{code} {dumps(b)} {st}")
    # the org cannot switch the floor off — a VALID policy body, refused, nothing written
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    code, b = await api(U_A1, "POST", "/api/v1/admin/sod/rules", {
        "rule_kind": "policy", "item_type": "role_grant", "self_approval": "never", "enforce_floor": False,
        "description": "S29B verify: try to turn the self-grant floor off"})
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_rules WHERE org_id = $1", ORG_A, fetch="fetchval"))
    check(code == 403 and n1 == n0, "an org cannot switch the floor off (VALID body → 403, no rule row)",
          f"{code} {dumps(b)} {n0} → {n1}")
    # even an org policy row that SAYS floor off (written past the API) cannot relax it
    await scoped(conn, ORG_B, "INSERT INTO sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, scope, "
                              "severity, self_approval, enforce_floor, description, status, version_no) VALUES "
                              "('policy:role_grant', 'org', $1, 'policy', 'role_grant', 'same_item', 'block', 'never', "
                              "false, 'S29B verify: an org policy saying floor off', 'active', 1)", ORG_B,
                 fetch="execute")
    pool = await get_pool()
    with _ctx(ORG_B):
        async with pool.acquire() as c:
            d = await sod.can_check(c, org_id=ORG_B, item_type=sod.ROLE_GRANT, checker_id=U_B1, maker_ids=[U_B1])
    rolesb = {r["name"]: str(r["id"]) for r in await scoped(conn, ORG_B, "SELECT id, name FROM roles WHERE org_id = $1",
                                                            ORG_B)}
    code, b = await api(U_B1, "PUT", f"/api/v1/admin/users/{U_B1}/role", {"role_id": rolesb["s29b_viewer"]})
    check(d.refused and code == 403, "an org policy row saying 'floor off' does not relax it: the self-grant is still "
          "refused (service and route)", f"{d.as_dict()} {code}")


# ── Tasks 2–5: eligibility, switches, shadow, execution ─────────────────────────────────────────────────────────
async def y_eligibility(conn):
    section("[Y] Eligibility: every verb classed; 'never' verbs never unattended (validation refuses / run suspends)")
    check(WA.unclassed_verbs(REGISTRY) == [], "every registered verb carries an autonomy class",
          f"{WA.unclassed_verbs(REGISTRY)}")
    for key, why in (("spv.record_transaction", "money"), ("spv.subscribe", "money"),
                     ("entity.link_ownership", "custody")):
        a = REGISTRY.get(key)
        check(a is not None and a.autonomy_class == WA.NEVER, f"{key} is 'never' ({why})",
              f"{getattr(a, 'autonomy_class', None)}")
    absent = [k for k in ("role", "trade", "money_movement") if not any(k in a.key for a in REGISTRY.all()
                                                                      if a.module != "s29bverify")]
    find("no registered verb submits a trade, changes a role or moves money through the custodian path today — the "
         "'never' rule is proven on the fixture money verb and on the two real money / custody verbs; any such verb "
         "added later is 'never' by default (an unlisted write verb fails closed)", f"absent: {absent}")
    class _W:
        key, access_type = "x.unlisted_write", "write"
    check(WA.autonomy_class(_W()) == WA.NEVER, "an unlisted write verb is 'never' (fail closed)")
    d, _v, t, _s = WF["never"]
    body = {"workflow_definition_id": str(d), "trigger_type": "scheduled", "schedule_cron": "0 9 * * 1",
            "timezone": "UTC", "is_active": True}
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = $1", d,
                          fetch="fetchval"))
    code, b = await api(U_OWN, "POST", "/api/v1/admin/workflow-triggers", body)
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = $1", d,
                          fetch="fetchval"))
    det = b.get("detail") if isinstance(b.get("detail"), dict) else {}
    check(code == 422 and n1 == n0 and any(s.get("verb") == V_NEVER for s in det.get("steps") or []),
          "validation REFUSES an active trigger for a workflow whose non-Tier-1 write step is 'never' (named, no row)",
          f"{code} {dumps(b)}")
    code, b = await api(U_OWN, "POST", "/api/v1/admin/workflow-triggers", {**body, "is_active": False})
    tid = b.get("id")
    check(code == 201 and bool(tid), "an INACTIVE trigger is accepted (it starts nothing)", f"{code} {dumps(b)}")
    code2, b2 = await api(U_OWN, "PATCH", f"/api/v1/admin/workflow-triggers/{tid}", {"is_active": True})
    act = await scoped(conn, ORG_A, "SELECT is_active FROM workflow_triggers WHERE id = $1::uuid", tid, fetch="fetchval")
    check(code2 == 422 and act is False, "… and activating it is refused the same way (still inactive)",
          f"{code2} {dumps(b2)} {act}")
    WF["never"] = (d, WF["never"][1], UUID(tid), WF["never"][3]) if tid else WF["never"]
    S["never_trigger"] = bool(tid)


async def y_switches_shadow(conn):
    section("[Y] Switches + shadow: org OFF / not opted in → no unattended write; shadow writes nothing; opt-in needs "
            "N clean shadow runs and a checker who is not the trigger owner")
    m0 = await markers(conn)
    c0 = CALLS.get(V_ELIG, 0)
    # (a) defaults: org off, version off → the step SUSPENDS for a person
    r = await fire("elig")
    st = await steps_of(conn, r.get("run_id"))
    check(r.get("status") == "awaiting_approval" and len(st) == 1 and st[0]["autonomy_mode"] == "held_for_person"
          and "not opted in" in (st[0]["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c0,
          "org switch OFF, version not opted in: the write step is HELD for a person, nothing ran",
          f"{dumps(r)} {dumps(st)}")
    S["held_run_step"] = st[0]["id"] if st else None
    # a person approves it — the S30 checker rule against the trigger owner; it runs exactly once
    pool = await get_pool()
    with _ctx(ORG_A):
        try:
            await ENG.resolve_tier_approval(pool, st[0]["id"], reviewed_by=str(U_OWN), decision="approved")
            own = "accepted"
        except AP.MakerCheckerError:
            own = "refused"
        ok = await ENG.resolve_tier_approval(pool, st[0]["id"], reviewed_by=str(U_REV), decision="approved")
    check(own == "refused" and ok.get("run_status") == "completed" and CALLS.get(V_ELIG, 0) == c0 + 1,
          "a held step: the trigger owner is refused as checker; another eligible person approves; it runs ONCE",
          f"{own} {dumps(ok)} calls={CALLS.get(V_ELIG, 0) - c0}")
    m1 = await markers(conn)
    c1 = CALLS.get(V_ELIG, 0)
    # (b) shadow: the owner puts the version in shadow — at once (tightening / neutral), no checker
    code, b = await version_state(U_OWN, "elig", "shadow")
    check(code == 201 and (b.get("setting") or {}).get("status") == "active", "shadow is set at once by an author",
          f"{code} {dumps(b)}")
    code, b = await version_state(U_V, "elig", "shadow")
    check(code == 403, "a view-only caller cannot change a version's state (VALID body → 403)", f"{code}")
    shadow_runs = []
    for i in range(3):
        r = await fire("elig")
        shadow_runs.append(r.get("run_id"))
        st = await steps_of(conn, r.get("run_id"))
        res = json.loads(st[0]["result"]) if st and isinstance(st[0]["result"], str) else (st[0]["result"] if st else {})
        sh = (res or {}).get("shadow") or {}
        check(r.get("status") == "completed" and st and st[0]["autonomy_mode"] == "shadow"
              and sh.get("verb") == V_ELIG and sh.get("executed") is False and bool(sh.get("target"))
              and (sh.get("arguments") or {}).get("user_id") == str(U_OWN) and bool(sh.get("would_produce")),
              f"shadow run {i + 1}: the would-be write is RECORDED (verb, arguments, target, result) and not executed",
              f"{dumps(r)} {dumps(sh)}")
    m2 = await markers(conn)
    check(CALLS.get(V_ELIG, 0) == c1 and m2 == m1, "shadow wrote NOTHING: the handler never ran and the target table "
          "(audit_log marker rows) is unchanged — count and checksum", f"calls +{CALLS.get(V_ELIG, 0) - c1} {m1} → {m2}")
    modes = {r["autonomy_mode"] for r in await runs_of(conn, WF["elig"][1]) if r["id"] in shadow_runs}
    check(modes == {"shadow"}, "each run is marked a shadow run (workflow_runs.autonomy_mode)", f"{modes}")
    code, ov = await api(U_A1, "GET", "/api/v1/admin/workflow-autonomy")
    pend = {p["run_id"] for p in ov.get("shadow_reviews_pending") or []}
    check(code == 200 and set(map(str, shadow_runs)) <= pend, "the shadow runs are listed for review on Admin → "
          "Workflows → Autonomy", f"{code} {len(pend)}")
    # opt-in refused with no clean shadow run yet (VALID body)
    n0 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id = $1", ORG_A,
                          fetch="fetchval"))
    code, b = await version_state(U_A1, "elig", "on")
    n1 = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id = $1", ORG_A,
                          fetch="fetchval"))
    check(code == 403 and n1 == n0, "opt-in REFUSED before any clean shadow run (403, nothing written)",
          f"{code} {dumps(b)}")
    # reviews: only the trigger owner; not-clean needs a note
    code, b = await api(U_REV, "POST", f"/api/v1/admin/workflow-autonomy/runs/{shadow_runs[0]}/shadow-review",
                        {"outcome": "clean"})
    check(code == 403, "someone other than the trigger owner cannot review a shadow run (403)", f"{code} {dumps(b)}")
    for rid in shadow_runs[:2]:
        await api(U_OWN, "POST", f"/api/v1/admin/workflow-autonomy/runs/{rid}/shadow-review", {"outcome": "clean"})
    code, b = await version_state(U_A1, "elig", "on")
    check(code == 403, "opt-in REFUSED with 2 of the 3 required clean shadow runs", f"{code} {dumps(b)}")
    code, _b = await api(U_OWN, "POST", f"/api/v1/admin/workflow-autonomy/runs/{shadow_runs[2]}/shadow-review",
                         {"outcome": "clean"})
    rev = await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_runs WHERE id = ANY($1::uuid[]) AND "
                                    "shadow_review_outcome = 'clean' AND shadow_reviewed_by = $2",
                       shadow_runs, U_OWN, fetch="fetchval")
    check(code == 200 and int(rev) == 3, "the owner reviews the three shadow runs clean (re-read)", f"{code} {rev}")
    code, b = await version_state(U_A1, "elig", "on")
    sid = (b.get("setting") or {}).get("id")
    check(code == 201 and (b.get("setting") or {}).get("status") == "draft", "with 3 clean shadow runs the opt-in is "
          "a DRAFT awaiting a checker", f"{code} {dumps(b)}")
    code, b = await api(U_A1, "POST", f"/api/v1/admin/workflow-autonomy/settings/{sid}/decide", {"outcome": "approve"})
    check(code == 403, "the requester cannot approve their own opt-in (403)", f"{code}")
    code, b = await api(U_OWN, "POST", f"/api/v1/admin/workflow-autonomy/settings/{sid}/decide", {"outcome": "approve"})
    still = await scoped(conn, ORG_A, "SELECT status FROM workflow_autonomy_settings WHERE id = $1::uuid", sid,
                         fetch="fetchval")
    check(code == 403 and still == "draft", "the TRIGGER OWNER cannot be the checker (S30: 403, still a draft)",
          f"{code} {dumps(b)} {still}")
    dec = int(await scoped(conn, ORG_A, "SELECT count(*) FROM sod_decisions WHERE org_id = $1 AND item_type = "
                                        "'workflow_autonomy_optin' AND outcome = 'refused'", ORG_A, fetch="fetchval"))
    check(dec >= 2, "both refusals are recorded in sod_decisions (workflow_autonomy_optin)", f"{dec}")
    code, b = await api(U_REV, "POST", f"/api/v1/admin/workflow-autonomy/settings/{sid}/decide", {"outcome": "approve"})
    row = await scoped(conn, ORG_A, "SELECT status, checker_id::text AS c, maker_id::text AS m FROM "
                                    "workflow_autonomy_settings WHERE id = $1::uuid", sid, fetch="fetchrow")
    check(code == 200 and row["status"] == "active" and row["c"] == str(U_REV) and row["m"] == str(U_A1),
          "another eligible person signs it off: active, checker ≠ requester ≠ owner (re-read)", f"{code} {dict(row)}")
    # (d) version on, org switch OFF → still held for a person
    c2 = CALLS.get(V_ELIG, 0)
    r = await fire("elig")
    st = await steps_of(conn, r.get("run_id"))
    check(r.get("status") == "awaiting_approval" and st and st[0]["autonomy_mode"] == "held_for_person"
          and "workflows.autonomy is off" in (st[0]["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c2,
          "opted in but the org switch is OFF: no unattended write (held for a person)", f"{dumps(r)} {dumps(st)}")
    # (e) the org switch: maker-checker
    code, b = await api(U_A1, "POST", "/api/v1/admin/workflow-autonomy/settings",
                        {"key": "autonomy", "value": True, "reason": "verify_s29b: turn on"})
    sid = (b.get("setting") or {}).get("id")
    check(code == 201 and (b.get("setting") or {}).get("status") == "draft", "turning the org switch ON is a draft",
          f"{code} {dumps(b)}")
    code, _b = await api(U_A1, "POST", f"/api/v1/admin/workflow-autonomy/settings/{sid}/decide", {"outcome": "approve"},
                         sub=SECOND_SUB)
    check(code == 403, "the requester cannot approve it — not from their second login either (403)", f"{code}")
    code, _b = await api(U_V, "POST", "/api/v1/admin/workflow-autonomy/settings",
                         {"key": "autonomy", "value": True, "reason": "verify_s29b: viewer"})
    check(code == 403, "a view-only caller cannot ask for it (VALID body → 403)", f"{code}")
    code, b = await api(U_A2, "POST", f"/api/v1/admin/workflow-autonomy/settings/{sid}/decide", {"outcome": "approve"})
    on = await scoped(conn, ORG_A, "SELECT setting_value FROM workflow_autonomy_settings WHERE org_id = $1 AND "
                                   "setting_key = 'autonomy' AND status = 'active' AND valid_to IS NULL", ORG_A,
                      fetch="fetchval")
    aud = int(await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action LIKE "
                                        "'workflow_autonomy.setting_%' AND resource_id = $2::uuid", ORG_A, sid,
                           fetch="fetchval"))
    check(code == 200 and on is not None and json.loads(on) is True and aud >= 2,
          "a DIFFERENT admin approves: the org switch is ON (re-read), requested + approved both audited",
          f"{code} {on} audit={aud}")
    S["optin_ok"] = row is not None and row["status"] == "active" and on is not None and json.loads(on) is True


async def y_execution(conn):
    section("[Y] Execution: delegate audit line; idempotent; limits hold + alert; failure holds")
    # a 'never' verb with BOTH switches on: the trigger-started run still suspends it for a person
    cn = CALLS.get(V_NEVER, 0)
    if S.get("never_trigger"):
        r = await fire("never")
        st = await steps_of(conn, r.get("run_id"))
        check(r.get("status") == "awaiting_approval" and bool(st) and st[0]["autonomy_mode"] == "held_for_person"
              and "never run unattended" in (st[0]["error_detail"] or "") and CALLS.get(V_NEVER, 0) == cn,
              "a 'never' verb (money) with the org switch ON: the run SUSPENDS it for a person, nothing ran",
              f"{dumps(r)} {dumps(st)}")
    else:
        check(False, "the 'never' fixture trigger exists", "y_eligibility did not create it")
    c0, m0 = CALLS.get(V_ELIG, 0), (await markers(conn))[0]
    r = await fire("elig")
    st = await steps_of(conn, r.get("run_id"))
    s0 = st[0] if st else {}
    check(r.get("status") == "completed" and s0.get("autonomy_mode") == "unattended" and s0.get("status") == "completed"
          and s0.get("delegate_for") == str(U_OWN) and bool(s0.get("idempotency_key"))
          and CALLS.get(V_ELIG, 0) == c0 + 1 and (await markers(conn))[0] == m0 + 1,
          "both switches on + opted in: the write runs UNATTENDED, once, for the trigger owner (re-read)",
          f"{dumps(r)} {dumps(s0)}")
    check(CALL_ACTORS.get(V_ELIG, [None])[-1] == str(U_OWN), "the verb ran as the trigger owner's delegate (its "
          "handler got the owner as the acting user)", f"{CALL_ACTORS.get(V_ELIG, [])[-1:]}")
    line = await scoped(conn, ORG_A, "SELECT payload->>'line' FROM audit_log WHERE org_id = $1 AND action = "
                                     "'workflow.unattended_write' AND resource_id = $2", ORG_A, s0.get("id"),
                        fetch="fetchval")
    want = f"on behalf of {U_OWN} by workflow S29B VERIFY elig v1"
    check(line == want, "the delegate audit line: \"on behalf of <owner> by workflow <name> v<n>\"", f"{line!r}")
    code, det = await api(U_A1, "GET", f"/api/v1/admin/workflow-runs/{r.get('run_id')}")
    ds = (det.get("steps") or [{}])[0]
    check(code == 200 and ds.get("unattended") is True and ds.get("delegate_line") == want,
          "the run console marks the step unattended with the delegate line", f"{code} {dumps(ds)}")
    # idempotency: a forced retry of the same run-step writes once
    pool = await get_pool()
    step_row = await scoped(conn, ORG_A, "SELECT ws.* FROM workflow_steps ws WHERE ws.workflow_version_id = $1",
                            WF["elig"][1], fetch="fetchrow")
    with _ctx(ORG_A):
        ENG._SERVICE_STEP_MAP = {step_row["step_key"]: dict(step_row)}
        ENG._SERVICE_CONTEXT = {"pool": pool, "org_id": ORG_A, "actor_id": U_OWN, "run_id": r.get("run_id"),
                                "run_context": {"trigger_id": str(WF["elig"][2]), "trigger_type": "scheduled"},
                                "run_step_ids": {step_row["step_key"]: s0.get("id")}}
        try:
            again = await ENG._run_service_step(step_row["step_key"], REGISTRY.get(V_ELIG))
        finally:
            ENG._SERVICE_STEP_MAP, ENG._SERVICE_CONTEXT = {}, {}
    check(CALLS.get(V_ELIG, 0) == c0 + 1 and (await markers(conn))[0] == m0 + 1 and isinstance(again, dict)
          and (again.get("autonomy") or {}).get("mode") == "unattended",
          "a FORCED RETRY of the same run-step writes nothing more (its claim returns the recorded result)",
          f"calls +{CALLS.get(V_ELIG, 0) - c0} {dumps(again)}")
    # shadow_runs_required 3 → 1: loosening, so a draft + a different admin (also speeds the remaining opt-ins)
    code, b = await set_org(U_A1, "shadow_runs_required", 1, approver=U_A2)
    check(code == 200, "fewer shadow runs is a loosening: drafted by one admin, approved by another", f"{code} {dumps(b)}")
    for name in ("two", "cap", "fail", "kill", "pkill", "t1"):
        if name != "t1":
            check(await opt_in(conn, name), f"'{name}' opted in after its shadow period (owner review + checker)")
    # writes per run
    code, b = await set_org(U_A1, "limits", {"writes_per_run": 1})
    check(code == 201 and (b.get("setting") or {}).get("status") == "active", "lowering a limit applies AT ONCE "
          "(tightening, no checker)", f"{code} {dumps(b)}")
    c1 = CALLS.get(V_ELIG, 0)
    r = await fire("two")
    run = (await runs_of(conn, WF["two"][1]))[-1]
    todo = {str(x["user_id"]) for x in await scoped(conn, ORG_A, "SELECT user_id FROM member_todos WHERE org_id = $1 "
                                                                 "AND source = $2 AND related_id = $3", ORG_A,
                                                    WA.TODO_SOURCE_HOLD, run["id"])}
    check(run["status"] == "held" and "writes per run" in (run["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c1 + 1,
          "over writes-per-run the run HOLDS after the first write (the second never ran)", f"{dumps(run)}")
    check(str(U_OWN) in todo and bool(todo & {str(U_A1), str(U_A2)}), "… and the trigger owner and an org admin are "
          "alerted", f"{todo}")
    code, b = await set_org(U_A1, "limits", {"writes_per_run": 5}, approver=U_A2)
    check(code == 200, "raising it back is a loosening: a different admin approves", f"{code} {dumps(b)}")
    # writes per day
    used = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_run_steps WHERE org_id = $1 AND autonomy_mode "
                                         "= 'unattended' AND autonomy_claimed_at >= $2", ORG_A, WA.day_start(T0),
                            fetch="fetchval"))
    await set_org(U_A1, "limits", {"writes_per_day": used})
    c2 = CALLS.get(V_ELIG, 0)
    r = await fire("elig")
    run = (await runs_of(conn, WF["elig"][1]))[-1]
    check(run["status"] == "held" and "writes per day" in (run["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c2,
          f"over writes-per-day ({used} today) the run HOLDS before writing", f"{dumps(run)}")
    await set_org(U_A1, "limits", {"writes_per_day": 50}, approver=U_A2)
    # runs per day
    runs_today = int(await scoped(conn, ORG_A, "SELECT count(DISTINCT workflow_run_id) FROM workflow_run_steps WHERE "
                                               "org_id = $1 AND autonomy_mode = 'unattended' AND autonomy_claimed_at "
                                               ">= $2", ORG_A, WA.day_start(T0), fetch="fetchval"))
    await set_org(U_A1, "limits", {"runs_per_day": runs_today})
    c3 = CALLS.get(V_ELIG, 0)
    r = await fire("elig")
    run = (await runs_of(conn, WF["elig"][1]))[-1]
    check(run["status"] == "held" and "runs per day" in (run["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c3,
          f"over runs-per-day ({runs_today} unattended runs today) a NEW run holds before its first write",
          f"{dumps(run)}")
    await set_org(U_A1, "limits", {"runs_per_day": 20}, approver=U_A2)
    # eligible_with_cap
    await set_org(U_A1, "limits", {"cap_per_run": 3})
    cc = CALLS.get(V_CAP, 0)
    big = await start_direct("cap", {"quantity": 5})
    small = await start_direct("cap", {"quantity": 2})
    runs = await runs_of(conn, WF["cap"][1])
    check(not big.get("ok") and "per-run cap" in big.get("error", "") and small.get("status") == "completed"
          and CALLS.get(V_CAP, 0) == cc + 1, "eligible_with_cap: 5 items over a cap of 3 HOLDS; 2 items runs (once)",
          f"{dumps(big)} {dumps(small)} {[r['status'] for r in runs][-2:]}")
    await set_org(U_A1, "limits", {"cap_per_run": 100}, approver=U_A2)
    # failure holds — and is never retried
    cf = CALLS.get(V_FAIL, 0)
    r = await fire("fail")
    run = (await runs_of(conn, WF["fail"][1]))[-1]
    st = await steps_of(conn, run["id"])
    check(not r.get("ok") and run["status"] == "held" and st and st[0]["status"] == "failed"
          and bool(st[0]["idempotency_key"]) and CALLS.get(V_FAIL, 0) == cf + 1,
          "a failed unattended write HOLDS the run (no retry storm); the step keeps its claim, marked failed",
          f"{dumps(r)} {dumps(run)} {dumps(st)}")
    step_row = await scoped(conn, ORG_A, "SELECT ws.* FROM workflow_steps ws WHERE ws.workflow_version_id = $1",
                            WF["fail"][1], fetch="fetchrow")
    held = None
    with _ctx(ORG_A):
        ENG._SERVICE_STEP_MAP = {step_row["step_key"]: dict(step_row)}
        ENG._SERVICE_CONTEXT = {"pool": pool, "org_id": ORG_A, "actor_id": U_OWN, "run_id": run["id"],
                                "run_context": {"trigger_id": str(WF["fail"][2]), "trigger_type": "scheduled"},
                                "run_step_ids": {step_row["step_key"]: st[0]["id"] if st else None}}
        try:
            await ENG._run_service_step(step_row["step_key"], REGISTRY.get(V_FAIL))
        except WA.AutonomyHold as exc:
            held = str(exc)
        finally:
            ENG._SERVICE_STEP_MAP, ENG._SERVICE_CONTEXT = {}, {}
    check(held is not None and CALLS.get(V_FAIL, 0) == cf + 1, "a forced retry of the failed step does NOT run it "
          "again (held: an earlier attempt did not finish)", f"{held} calls +{CALLS.get(V_FAIL, 0) - cf}")


async def y_kill(conn):
    section("[Y] Kill switches stop execution mid-run (the next step suspends for a person)")
    c0 = CALLS.get(V_ELIG, 0)
    r = await fire("kill")
    run = (await runs_of(conn, WF["kill"][1]))[-1]
    st = await steps_of(conn, run["id"])
    modes = [(s["step_key"], s["autonomy_mode"], s["status"]) for s in st]
    check(run["status"] == "awaiting_approval" and modes[0][1] == "unattended" and modes[1][1] == "held_for_person"
          and "kill switch" in (st[1]["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c0,
          "the ORG kill switch turned on during step 1 stops step 2 (held for a person, never ran)",
          f"{dumps(run)} {modes}")
    code, b = await set_org(U_A1, "kill_switch", False)
    check(code == 201, "clearing the org kill switch applies at once (the switches it guards were already checked)",
          f"{code} {dumps(b)}")
    code, b = await api(U_A1, "POST", "/api/v1/admin/workflow-autonomy/platform-kill-switch",
                        {"on": True, "reason": "verify_s29b: not a Super Admin"})
    plat = int(await platform(conn, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id IS NULL AND maker_id "
                                    "= $1", U_A1, fetch="fetchval"))
    check(code == 403 and plat == 0, "an org admin cannot set the platform kill switch (VALID body → 403, no row)",
          f"{code} {plat}")
    live = int(await platform(conn, "SELECT count(*) FROM workflow_triggers t JOIN organizations o ON o.id = t.org_id "
                                    "JOIN workflow_versions v ON v.workflow_definition_id = t.workflow_definition_id "
                                    "AND v.is_current JOIN workflow_steps ws ON ws.workflow_version_id = v.id AND "
                                    "ws.step_type = 'service' AND ws.action_registry_key IS NOT NULL WHERE t.is_active "
                                    "AND NOT COALESCE(o.is_test, false)", fetch="fetchval"))
    if live:
        find("the PLATFORM kill switch mid-run proof is skipped: a real active trigger runs a Service Task, and the "
             "platform switch would stop it too", f"{live} live trigger(s)")
        print("[SKIP] platform kill switch mid-run")
        return
    c1 = CALLS.get(V_ELIG, 0)
    r = await fire("pkill")
    run = (await runs_of(conn, WF["pkill"][1]))[-1]
    st = await steps_of(conn, run["id"])
    pool = await get_pool()
    with _ctx(ORG_A, True):
        async with pool.acquire() as c:
            cleared = await WA.change_setting(c, org_id=ORG_A, actor_id=str(U_SA2), is_super_admin=True,
                                              key=WA.KEY_KILL, value=False, reason="verify_s29b: resume", platform=True)
    eff = await scoped(conn, ORG_A, "SELECT setting_value FROM workflow_autonomy_settings WHERE org_id IS NULL AND "
                                    "setting_key = 'kill_switch' AND status = 'active' AND valid_to IS NULL",
                       fetch="fetchval")
    check(run["status"] == "awaiting_approval" and len(st) == 2 and st[1]["autonomy_mode"] == "held_for_person"
          and "platform kill switch" in (st[1]["error_detail"] or "") and CALLS.get(V_ELIG, 0) == c1,
          "the PLATFORM kill switch turned on during step 1 stops step 2", f"{dumps(run)} {dumps(st)}")
    check(cleared.get("status") == "active" and eff is not None and json.loads(eff) is False,
          "… and a Super Admin clears it at once (re-read: off)", f"{dumps(cleared)} {eff}")


async def y_tier1(conn):
    section("[Y] Tier 1 inside an autonomous run: suspends; the trigger owner is refused; another person approves; "
            "the verb runs exactly once")
    c0 = CALLS.get(V_ELIG, 0)
    r = await fire("t1")
    st = await steps_of(conn, r.get("run_id"))
    check(r.get("status") == "awaiting_approval" and st and st[0]["status"] == "suspended"
          and st[0]["autonomy_mode"] is None and CALLS.get(V_ELIG, 0) == c0,
          "with both switches on, a Tier-1 step still SUSPENDS (Tier 1, not autonomy)", f"{dumps(r)} {dumps(st)}")
    pool = await get_pool()
    with _ctx(ORG_A):
        try:
            await ENG.resolve_tier_approval(pool, st[0]["id"], reviewed_by=str(U_OWN), decision="approved")
            own = "accepted"
        except AP.MakerCheckerError:
            own = "refused"
        ok = await ENG.resolve_tier_approval(pool, st[0]["id"], reviewed_by=str(U_REV), decision="approved")
    check(own == "refused" and CALLS.get(V_ELIG, 0) == c0 + 1 and ok.get("run_status") == "completed",
          "the trigger owner is refused as checker (S30); another eligible person approves; the verb ran ONCE",
          f"{own} {dumps(ok)} calls +{CALLS.get(V_ELIG, 0) - c0}")


async def y_events(conn):
    section("[Y] Event triggers: de-duplicated (one run per event per workflow version) and rate-limited")
    d, v, _t, _s = WF["evt"]
    ids = []
    for uid in (U_OWN, U_A1):
        code, b = await api(uid, "POST", "/api/v1/admin/workflow-triggers",
                            {"workflow_definition_id": str(d), "trigger_type": "event", "event_type": EVENT_TYPE,
                             "is_active": True})
        ids.append(b.get("id"))
        check(code == 201, f"an event trigger on '{EVENT_TYPE}' (a task completed) is created by {USERS[uid][4]}",
              f"{code} {dumps(b)}")
    code, b = await api(U_OWN, "POST", "/api/v1/admin/workflow-triggers",
                        {"workflow_definition_id": str(d), "trigger_type": "event", "event_type": "made.up_event",
                         "is_active": True})
    check(code == 422, "an event type outside the supported list is refused (422)", f"{code}")
    pool = await get_pool()
    src = _id(500)
    with _ctx(ORG_A):
        e1 = await DE.publish_event(pool, ORG_A, EVENT_TYPE, "human_task", src, {"n": 1})
        e2 = await DE.publish_event(pool, ORG_A, EVENT_TYPE, "human_task", src, {"n": 1})
    runs = await runs_of(conn, v)
    check(len(e1.get("delivered") or []) == 1 and len(e1.get("deduped_by_version") or []) == 1 and len(runs) == 1,
          "two triggers on the same workflow version: ONE event starts ONE run (the second is de-duplicated)",
          f"{dumps(e1)} runs={len(runs)}")
    check(e2.get("deduped") is True and not e2.get("delivered") and len(await runs_of(conn, v)) == 1,
          "re-publishing the same event starts nothing", f"{dumps(e2)}")
    st = await steps_of(conn, runs[0]["id"]) if runs else []
    check(bool(st) and st[0]["autonomy_mode"] == "held_for_person", "an event-started run follows the same rules (not "
          "opted in → held for a person)", f"{dumps(st)}")
    claims = int(await scoped(conn, ORG_A, "SELECT count(*) FROM domain_event_deliveries WHERE workflow_version_id = $1 "
                                           "AND status = 'DELIVERED'", v, fetch="fetchval"))
    check(claims == 1, "exactly one DELIVERED claim for (event, version) — the unique index", f"{claims}")
    code, b = await set_org(U_A1, "limits", {"event_runs_per_hour": 2})
    check(code == 201, "event runs per hour lowered to 2 (at once)", f"{code} {dumps(b)}")
    with _ctx(ORG_A):
        e3 = await DE.publish_event(pool, ORG_A, EVENT_TYPE, "human_task", _id(501), {"n": 2})
        e4 = await DE.publish_event(pool, ORG_A, EVENT_TYPE, "human_task", _id(502), {"n": 3})
    lim = int(await scoped(conn, ORG_A, "SELECT count(*) FROM domain_event_deliveries WHERE workflow_trigger_id = "
                                        "$1::uuid AND status = 'RATE_LIMITED'", ids[0], fetch="fetchval"))
    todo = {str(x["user_id"]) for x in await scoped(conn, ORG_A, "SELECT user_id FROM member_todos WHERE org_id = $1 "
                                                                 "AND source = $2", ORG_A, DE.TODO_SOURCE_RATE_LIMITED)}
    check(len(e3.get("delivered") or []) == 1 and set(map(str, ids)) <= set(e4.get("rate_limited") or [])
          and not e4.get("delivered") and lim == 1 and len(await runs_of(conn, v)) == 2,
          "the third event in the hour is RATE-LIMITED for the workflow version — through either trigger: recorded, "
          "no run", f"{dumps(e3)} {dumps(e4)} limited_rows={lim}")
    check(str(U_OWN) in todo, "… and the trigger owner is told", f"{todo}")
    CLOCK["now"] = T0 + timedelta(hours=2)
    with _ctx(ORG_A):
        e5 = await DE.publish_event(pool, ORG_A, EVENT_TYPE, "human_task", _id(503), {"n": 4})
    CLOCK["now"] = T0
    check(len(e5.get("delivered") or []) == 1, "an hour later (injected clock) the trigger runs again", f"{dumps(e5)}")


async def y_visibility(conn):
    section("[Y] Visibility: Admin → Workflows → Autonomy envelope (fails closed); daily digest")
    code, ov = await api(U_A1, "GET", "/api/v1/admin/workflow-autonomy")
    p, voc = ov.get("permissions") or {}, ov.get("vocabularies") or {}
    keys = {x.get("key") for x in ov.get("verbs") or []}
    check(code == 200 and p.get("can_manage_org") is True and "autonomy" in (voc.get("editable") or [])
          and {V_ELIG, V_NEVER, "spv.record_transaction"} <= keys and ov.get("classes") == list(WA.CLASSES)
          and (ov.get("switches") or {}).get("org_switch") == WA.ORG_SWITCH_NAME,
          "an org admin: switches, limits, verbs with their class, editable controls", f"{code} {dumps(p)}")
    code, ov = await api(U_V, "GET", "/api/v1/admin/workflow-autonomy")
    p, voc = ov.get("permissions") or {}, ov.get("vocabularies") or {}
    check(code == 200 and p.get("can_manage_org") is False and voc.get("editable") == [],
          "a view-only caller: readable, editable is an EMPTY list", f"{code} {dumps(p)} {voc}")
    code, _ov = await api(U_N, "GET", "/api/v1/admin/workflow-autonomy")
    check(code == 403, "a caller with none of the three read keys is refused (403)", f"{code}")
    pool = await get_pool()
    n1 = await SCHED._digest_unattended(conn, pool, day=T0, log=lambda *_a: None, org_ids=[ORG_A], narrowed=True)
    n2 = await SCHED._digest_unattended(conn, pool, day=T0, log=lambda *_a: None, org_ids=[ORG_A], narrowed=True)
    rows = await scoped(conn, ORG_A, "SELECT user_id, detail FROM member_todos WHERE org_id = $1 AND source = $2",
                        ORG_A, WA.TODO_SOURCE_DIGEST)
    admins = {str(r["user_id"]) for r in rows}
    check(n1 > 0 and n2 == n1 and len(rows) == n1 and {str(U_A1), str(U_A2)} <= admins
          and all("unattended write" in (r["detail"] or "") for r in rows),
          "the daily digest reaches each org admin once (a second pass refreshes, never duplicates)",
          f"{n1} {n2} {len(rows)} {admins}")
    nb = await SCHED._digest_unattended(conn, pool, day=T0, log=lambda *_a: None, org_ids=None, narrowed=True)
    check(nb == 0, "a narrowed tick with no org named digests nothing (no real org is touched)", f"{nb}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    await set_org(U_B1, "kill_switch", True)
    a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id = $1", ORG_A,
                         fetch="fetchval"))
    b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id = $1", ORG_B,
                         fetch="fetchval"))
    ab = int(await scoped(conn, ORG_A, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id = $1", ORG_B,
                          fetch="fetchval"))
    ba = int(await scoped(conn, ORG_B, "SELECT count(*) FROM workflow_autonomy_settings WHERE org_id = $1", ORG_A,
                          fetch="fetchval"))
    check(a > 0 and b > 0 and ab == 0 and ba == 0, "workflow_autonomy_settings: each org reads its own, never the "
          "other's (both directions)", f"A={a} B={b} A→B={ab} B→A={ba}")
    rb = int(await scoped(conn, ORG_B, "SELECT count(*) FROM workflow_run_steps WHERE org_id = $1", ORG_A,
                          fetch="fetchval"))
    check(rb == 0, "org B reads none of org A's run steps (shadow results, delegates)", f"{rb}")
    code, ov = await api(U_B1, "GET", "/api/v1/admin/workflow-autonomy")
    raw = json.dumps(ov, default=str) if code == 200 else ""
    leaked = [str(x) for x in (ORG_A, U_A1, U_OWN, WF["elig"][1]) if str(x) in raw]
    check(code == 200 and not leaked and (ov.get("switches") or {}).get("org_kill_switch") is True
          and (ov.get("switches") or {}).get("autonomy") is False,
          "org B's page carries nothing of org A (its own kill switch on, its autonomy off)", f"{code} {leaked}")
    refusal = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                               "'false', true)", str(ORG_B))
            await conn.execute("INSERT INTO workflow_autonomy_settings (owner_scope, org_id, setting_key, "
                               "setting_value, requires_checker, reason, status, maker_id) VALUES ('org', $1, "
                               "'kill_switch', 'true'::jsonb, false, 'cross-org probe', 'active', $2)", ORG_A, U_B1)
            raise RuntimeError("accepted")
    except asyncpg.PostgresError as exc:
        refusal = type(exc).__name__
    except RuntimeError:
        refusal = None
    check(refusal is not None, "org B cannot write a setting into org A (RLS refuses the insert)", f"{refusal}")


async def y_real(conn):
    section("[Y] Real orgs: autonomy OFF everywhere; real rows unchanged")
    on = int(await platform(conn, "SELECT count(*) FROM workflow_autonomy_settings s JOIN organizations o ON o.id = "
                                  "s.org_id WHERE NOT COALESCE(o.is_test, false) AND s.status = 'active' AND s.valid_to "
                                  "IS NULL AND ((s.setting_key = 'autonomy' AND s.setting_value = 'true'::jsonb) OR "
                                  "(s.setting_key = 'version_autonomy' AND s.setting_value->>'state' = 'on'))",
                            fetch="fetchval"))
    check(on == 0, "autonomy is OFF on every real org (no active switch-on or version-on row)", f"{on}")
    plat = await platform(conn, "SELECT setting_value FROM workflow_autonomy_settings WHERE org_id IS NULL AND "
                                "setting_key = 'kill_switch' AND status = 'active' AND valid_to IS NULL",
                          fetch="fetchval")
    check(plat is None or json.loads(plat) is False, "the platform kill switch is not left on", f"{plat}")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    self_static(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0, "visibility1_routes.py --check: current", (out + err)[-300:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1200)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) > 0 and n.group(1) == p.group(1)
          and re.search(r"^# fail 0$", o1, re.M) is not None and "not ok" not in o1,
          f"the s29b render tests ({NODE_TEST}, run by path) all pass ({n.group(1) if n else '?'})",
          f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_tiergating.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_tiergating.py")], REPO, timeout=2700, env=env)
    t = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(t.ok and not fails, "verify_tiergating.py: 0 FAIL (its own final TOTAL line and exit code)",
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


FIXTURE_SECTIONS = (
    ("floor", y_floor), ("eligibility", y_eligibility), ("switches + shadow", y_switches_shadow),
    ("execution", y_execution), ("kill switches", y_kill), ("tier 1", y_tier1), ("events", y_events),
    ("visibility", y_visibility), ("isolation", y_isolation),
)
#: --sections floor,eligibility,… (comma-separated FIXTURE_SECTIONS names, plus 'real' / 'routes'); None = full run
ONLY = (set(x.strip() for x in sys.argv[sys.argv.index("--sections") + 1].split(","))
        if "--sections" in sys.argv else None)


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
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        if ONLY is not None:
            # S29B FIX 1 — one-off: the seed, then ONLY the named fixture sections (in their normal order), then the
            # same teardown + leftover + pre-test-count checks below. No front end, no nested regression.
            if not S.get("seeded"):
                check(False, "seed completed", "the requested sections were skipped")
            else:
                for name, fn in FIXTURE_SECTIONS:
                    if name in ONLY:
                        await run(name, fn, conn)
            for name, fn in (("real", y_real), ("routes", y_routes)):
                if name in ONLY:
                    await run(name, fn, conn)
        elif S.get("seeded"):
            await run("floor", y_floor, conn)
            await run("eligibility", y_eligibility, conn)
            await run("switches + shadow", y_switches_shadow, conn)
            if S.get("optin_ok"):
                await run("execution", y_execution, conn)
                await run("kill switches", y_kill, conn)
                await run("tier 1", y_tier1, conn)
            else:
                check(False, "opt-in + org switch completed", "execution / kill / tier-1 sections skipped")
            await run("events", y_events, conn)
            await run("visibility", y_visibility, conn)
            await run("isolation", y_isolation, conn)
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        if ONLY is None:
            await run("real", y_real, conn)
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        remove_fixture_verbs()
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (settings, runs, steps, deliveries, decisions, to-dos)",
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
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
        if S.get("no_teardown"):
            print(total_line())
    if os.environ.get("VERIFY_NESTED") == "1" or ONLY is not None:
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
