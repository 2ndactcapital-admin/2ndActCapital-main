"""verify_wftemplates1 — Workflow Manager Wave 2 (S29a) ends: THE WORKFLOW TEMPLATE LIBRARY.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_wftemplates1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a seeding exception included, with
the traceback) and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED (each at its CURRENT signature, asserted): the token boundary (``main.verify_token``) and the Auth0 profile
call. NO network (the httpx transport refuses and records), NO model call (the three AI helpers refuse and record —
on ``services.extraction`` AND on the generator's own imported name). The generator writer is driven once with a
LOCAL stub in place of the model helper (canned BPMN text — no model is called) only to prove it calls the shared
validator. REAL: the app through an ASGI transport, services.workflow_templates, the shared validator, the engine
(a Tier-1 step suspends for approval), agent_proposals, the guard triggers, RLS and the live database. The script's
own connection is app_service (rolbypassrls = false, asserted).

FIXTURES: a FIXTURE PLATFORM org and two fixture TENANT orgs (99000000-0000-0000-0000-0000002f0001 / 0002 / 0003,
is_test = true, asserted); users seeded with real user_roles grants (two Super Admins in the fixture platform org; in
tenant A an author, a reviewer holding review_agent_proposals, a viewer holding view_workflow_runs only and a member
holding no workflow key; in tenant B an author) — the app's decision on each asserted; one Profile per tenant;
template keys 'zzwft1_*'; workflow names 'WFT1 VERIFY …'. Every reserved prefix is checked absent from real rows
first. The REAL Hollisworks platform org is only READ: the verify writes NO template row there (asserted by count).

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with workflow_templates.teardown = 'fixture'
(the guard triggers' documented path; a role that is not app_service), keyed by this run's fixture ids; then every
counted table is re-read for its exact pre-test count — audit_log and every workflow table counted over FIXTURE rows
only (orgs / users / resource ids), never the whole table (real activity writes them).

--self-checks-only: git / route self-checks only (no Doppler, database, npm). --cleanup-only: the teardown and the
leftover check only. With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
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
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
TEMPLATE_DIR = REPO / "docs" / "workflow_templates"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "wftemplates1"
BEFORE_REF = "86515c7"   # wftemplates1-work's base: 'merge rep15'
_T, _P = "/api/v1/admin/workflow-templates", "/api/v1/admin/platform/workflow-templates"
_READ = "any(author_workflows|view_workflow_runs|configure_workflow_triggers)"
_AUTH, _SA = "author_workflows", "super_admin"
ROUTES = {
    ("GET", _T): _READ,
    ("GET", f"{_T}/copies"): _READ,
    ("GET", f"{_T}/{{template_id}}"): _READ,
    ("POST", f"{_T}/{{template_id}}/use"): _AUTH,
    ("POST", f"{_T}/org"): _AUTH,
    ("POST", f"{_T}/versions/{{version_id}}/retire"): _AUTH,
    ("GET", _P): _SA,
    ("POST", _P): _SA,
    ("POST", f"{_P}/versions/{{version_id}}/decide"): _SA,
    ("POST", f"{_P}/versions/{{version_id}}/retire"): _SA,
}
OWN_FILES = ("apps/api/services/workflow_templates.py", "apps/api/routers/workflow_templates.py",
             "apps/api/scripts/load_workflow_templates.py")
PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
#: docs/WFTEMPLATES1_DISCOVERY.md 1b — the verbs a shipped template may name (read / draft; the one Tier-1 verb stops).
WAVE2_VERBS = {"entities.count", "entity_graph.show_hierarchy", "investments.count", "marketplace.show_new_deals",
               "portfolio.find_my_investment", "portfolio.show_allocation", "spv.list_open", "spv.show_captable",
               "spv.show_ledger", "tasks.my_todos", "crm.draft_note", "propose", "spv_carry.propose_from_realization"}
NODE_TEST = "tests/wftemplates1/wftemplates1.test.mjs"


def self_routes(check, find):
    """wftemplates1's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that
    gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed,
          "wftemplates1 changed or removed no existing route's gate", f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items()
           if k not in by or g not in str(by[k].get("after")) or "manage_org_settings" in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names exactly its gate at wftemplates1's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "wftemplates1's routes all still exist today with the same gate", f"{drift[:4]}")


def self_code(check, find):
    """The sprint's own code at its own end point: one validator (looked up on the generator module, never a second
    BPMN check), tiers from compute_effective_tier, no model / network client."""
    sv = VS.own_text(SPRINT_KEY, OWN_FILES[0]) or ""
    check("G._validate(" in sv and "G.validate_workflow_bpmn(" in sv and "def _validate" not in sv
          and "find_script_tasks" not in sv and "parse_bpmn" not in sv,
          "the template writer calls THE shared validator (G._validate / G.validate_workflow_bpmn) and carries no "
          "BPMN check of its own")
    check("compute_effective_tier(" in sv and "SUSPEND_TIER" in sv,
          "template tiers are computed with the engine's own compute_effective_tier / SUSPEND_TIER")
    for f in OWN_FILES:
        src = VS.own_text(SPRINT_KEY, f) or ""
        check(bool(src) and not re.search(r"call_claude|anthropic|litellm|httpx\.|requests\.", src),
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
import load_workflow_templates as LOADER  # noqa: E402

import main  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_editor as ED  # noqa: E402
from services import workflow_engine as ENG  # noqa: E402
from services import workflow_nl_generator as G  # noqa: E402
from services import workflow_templates as WT  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402

TAG = "wft1verify"
KEY_PREFIX = "zzwft1_"
NAME_PREFIX = "WFT1 VERIFY"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000002f{n:04d}")


FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000002f"
ORG_P, ORG_A, ORG_B = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_P, ORG_A, ORG_B]
U_SA1, U_SA2, U_AAUTH, U_AREV, U_AVIEW, U_AMEM, U_BAUTH = (_id(110 + i) for i in range(7))
ROLE_PERMS = {
    "wft1_author": {"author_workflows", "view_workflow_runs", "configure_workflow_triggers"},
    "wft1_reviewer": {"review_agent_proposals", "view_workflow_runs"},
    "wft1_viewer": {"view_workflow_runs"},
    "wft1_member": set(),
}
# uid → (org, users.role mirror, short name, role granted, full name)
USERS = {
    U_SA1: (ORG_P, "super_admin", "sa1", "super_admin", "WFT1 Super Admin One"),
    U_SA2: (ORG_P, "super_admin", "sa2", "super_admin", "WFT1 Super Admin Two"),
    U_AAUTH: (ORG_A, "member", "aauth", "wft1_author", "WFT1 Tenant A Author"),
    U_AREV: (ORG_A, "member", "arev", "wft1_reviewer", "WFT1 Tenant A Reviewer"),
    U_AVIEW: (ORG_A, "member", "aview", "wft1_viewer", "WFT1 Tenant A Viewer"),
    U_AMEM: (ORG_A, "member", "amem", "wft1_member", "WFT1 Tenant A Member"),
    U_BAUTH: (ORG_B, "member", "bauth", "wft1_author", "WFT1 Tenant B Author"),
}
PROFILE_A, PROFILE_B = _id(200), _id(201)
PROFILES = {PROFILE_A: (ORG_A, "WFT1 Verify Reviewers A"), PROFILE_B: (ORG_B, "WFT1 Verify Reviewers B")}
TEMPLATE_TABLES = ("public.workflow_templates", "public.workflow_template_versions", "public.workflow_template_copies")
COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.profiles", "public.audit_log", "public.member_todos", "public.agent_proposals",
    "public.workflow_definitions", "public.workflow_versions", "public.workflow_steps", "public.workflow_runs",
    "public.workflow_run_steps", "public.workflow_triggers", *TEMPLATE_TABLES,
)
SEED_CHECK_LITERALS = (
    ("public.workflow_templates", "workflow_templates_scope_chk", ("platform", "org")),
    ("public.workflow_template_versions", "wtv_scope_chk", ("platform", "org")),
    ("public.workflow_template_versions", "wtv_status_chk", ("draft", "published", "rejected", "retired")),
    ("public.workflow_template_versions", "wtv_category_chk", tuple(k for k, _ in WT.CATEGORIES)),
)
UNIQUES = ("workflow_templates_key_live_uq", "workflow_template_versions_no_uq", "workflow_template_versions_draft_uq",
           "workflow_template_copies_definition_uq", "workflow_steps_workflow_version_id_step_key_key",
           "workflow_versions_workflow_definition_id_version_number_key", "profiles_org_id_name_key")

S: dict = {}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
VALIDATE_CALLS: list[list[str]] = []
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


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def detail_text(body) -> str:
    """The DECODED refusal text (a 422 carries {message, errors}); never the ascii-escaped dump."""
    d = (body or {}).get("detail") if isinstance(body, dict) else None
    if isinstance(d, dict):
        return " | ".join([str(d.get("message") or "")] + [str(e) for e in (d.get("errors") or [])])
    return d if isinstance(d, str) else ""


def linear_bpmn(key: str, steps: list[tuple]) -> str:
    """A linear fixture BPMN: steps = [(kind 'user'|'service'|'script', name, verb or None, profile placeholder)]."""
    ns = 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:twoa="http://2ndactcapital.com/bpmn/ext"'
    out = [f'<?xml version="1.0" encoding="UTF-8"?><bpmn:definitions {ns} id="D_{key}" '
           f'targetNamespace="http://2ndactcapital.com/bpmn/verify"><bpmn:process id="p_{key}" isExecutable="true">',
           '<bpmn:startEvent id="Start_1"><bpmn:outgoing>F1</bpmn:outgoing></bpmn:startEvent>']
    ids = ["Start_1"]
    for i, (kind, name, verb, prof) in enumerate(steps, start=1):
        tag = {"user": "userTask", "service": "serviceTask", "script": "scriptTask"}[kind]
        sid = f"S_{i}"
        ids.append(sid)
        gov = ""
        if verb is not None or prof:
            attrs = (f'actionRegistryKey="{verb}" ' if verb else "") + (f'assignedRoleProfileId="{prof}"' if prof else "")
            gov = f"<bpmn:extensionElements><twoa:governance {attrs.strip()}/></bpmn:extensionElements>"
        out.append(f'<bpmn:{tag} id="{sid}" name="{name}">{gov}<bpmn:incoming>F{i}</bpmn:incoming>'
                   f'<bpmn:outgoing>F{i + 1}</bpmn:outgoing></bpmn:{tag}>')
    n = len(steps)
    ids.append("End_1")
    out.append(f'<bpmn:endEvent id="End_1"><bpmn:incoming>F{n + 1}</bpmn:incoming></bpmn:endEvent>')
    out += [f'<bpmn:sequenceFlow id="F{i + 1}" sourceRef="{ids[i]}" targetRef="{ids[i + 1]}"/>' for i in range(n + 1)]
    out.append("</bpmn:process></bpmn:definitions>")
    return "".join(out)


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


async def db_refusal(conn, gucs: dict, sql, *args) -> str | None:
    """Run ``sql`` in its own transaction with ``gucs`` SET LOCAL; return the database's refusal (None = accepted —
    and then the transaction is rolled back anyway, so nothing a probe writes ever stays)."""
    class _Rollback(Exception):
        pass

    try:
        async with conn.transaction():
            for k, v in gucs.items():
                await conn.execute("SELECT set_config($1, $2, true)", k, v)
            await conn.execute(sql, *args)
            raise _Rollback()
    except _Rollback:
        return None
    except asyncpg.PostgresError as exc:
        return f"{type(exc).__name__}: {exc}"


def _arr(vals, typ="uuid") -> str:
    return "ARRAY[" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals) + f"]::{typ}[]"


def fixture_scope(table: str) -> str:
    """The fixture-row predicate of EVERY counted table (live activity may write any of them)."""
    orgs, users = _arr(FIXTURE_ORGS), _arr(USERS)
    by_org = f"org_id = ANY({orgs})"
    return {
        "public.audit_log": f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR resource_id::text LIKE "
                            f"'{FIXTURE_ID_PREFIX}%'",
        "public.member_todos": f"org_id = ANY({orgs}) OR user_id = ANY({users})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.organizations": f"id = ANY({orgs})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.role_permissions": f"role_id IN (SELECT id FROM public.roles WHERE {by_org})",
        "public.workflow_templates": f"owner_org_id = ANY({orgs})",
        "public.workflow_template_versions": f"owner_org_id = ANY({orgs})",
    }.get(table, by_org)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
            for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Real rows this sprint must never touch: every template table outside the fixture orgs (only the operator's
    loader writes them) and the REAL platform org's templates. Real workflow definitions are NOT fingerprinted —
    human_tasks creates system definitions in real orgs during live activity; the workflow tables are counted by
    fixture ids instead (COUNTED)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {
        "workflow_templates": await fingerprint(
            conn, "SELECT * FROM public.workflow_templates WHERE NOT (owner_org_id = ANY($1::uuid[]))", orgs),
        "workflow_template_versions": await fingerprint(
            conn, "SELECT * FROM public.workflow_template_versions WHERE NOT (owner_org_id = ANY($1::uuid[]))", orgs),
        "workflow_template_copies": await fingerprint(
            conn, "SELECT * FROM public.workflow_template_copies WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "real_platform_templates": await fingerprint(
            conn, "SELECT * FROM public.workflow_templates WHERE owner_org_id = $1::uuid", PLATFORM_ORG_ID),
    }
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
    """Child-first by the shared helper, keyed by this run's fixture ids. The template tables carry owner_org_id
    (not org_id), so they are planned explicitly; every key is a materialised array, never a subquery over a table
    the same teardown deletes from."""
    op = await operator_conn()
    try:
        orgs, users = FIXTURE_ORGS, list(USERS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.workflow_template_copies": ("org_id = ANY($1::uuid[]) OR template_owner_org_id = ANY($1::uuid[])",
                                                [orgs]),
            "public.workflow_template_versions": ("owner_org_id = ANY($1::uuid[])", [orgs]),
            "public.workflow_templates": ("owner_org_id = ANY($1::uuid[])", [orgs]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("workflow_templates.teardown",), label="verify_wftemplates1")
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
        ("SELECT count(*) FROM public.workflow_templates WHERE owner_org_id = ANY($1::uuid[]) OR template_key LIKE $2",
         [FIXTURE_ORGS, f"{KEY_PREFIX}%"]),
        ("SELECT count(*) FROM public.workflow_template_versions WHERE owner_org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM public.workflow_template_copies WHERE org_id = ANY($1::uuid[]) OR "
         "template_owner_org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM public.workflow_definitions WHERE name LIKE $1", [f"{NAME_PREFIX}%"]),
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


async def api(uid, method, path, body=None, params=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_wftemplates1"}}
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
    try:
        return res.status_code, json.loads(res.content.decode("utf-8") or "{}")
    except ValueError:
        return res.status_code, {"raw": res.content[:2000].decode("utf-8", "replace")}


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
    register_all()   # ASGITransport skips the app lifespan, where the registry is normally filled
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_wftemplates1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_wftemplates1: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    G.call_claude_text = refuse_model("workflow_nl_generator.call_claude_text")

    # The ONE validator, watched: every call records its caller chain. Same function underneath.
    have = list(inspect.signature(G._validate).parameters)
    if have != ["xml", "valid_action_keys", "valid_profile_ids"]:
        raise RuntimeError(f"_validate signature drifted: {have}")
    original = G._validate

    def spy(xml, valid_action_keys, valid_profile_ids):
        VALIDATE_CALLS.append([f.function for f in inspect.stack()[1:8]])
        return original(xml, valid_action_keys, valid_profile_ids)

    spy.original = original
    G._validate = spy


def callers_since(n0: int) -> list[str]:
    return [fn for chain in VALIDATE_CALLS[n0:] for fn in chain]


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1d (docs/WFTEMPLATES1_DISCOVERY.md)")
    p = REPO / "docs" / "WFTEMPLATES1_DISCOVERY.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    heads = re.findall(r"^## (1[a-d])\.", txt, re.M)
    check(set(heads) >= {"1a", "1b", "1c", "1d"}, "the discovery doc has a section for each of 1a–1d",
          f"{p.exists()} {sorted(set(heads))}")
    listed = set(re.findall(r"^\| `([a-z_.]+)`", txt, re.M))
    registry = {a.key for a in REGISTRY.all()}
    check(bool(WAVE2_VERBS) and WAVE2_VERBS <= registry and WAVE2_VERBS <= listed,
          "1b: every Wave-2 template verb is a registered verb and is listed in the discovery table",
          f"not registered={sorted(WAVE2_VERBS - registry)} not listed={sorted(WAVE2_VERBS - listed)}")
    inv = sorted(k for k in WAVE2_VERBS if getattr(REGISTRY.get(k), "workflow_invocable", False))
    check(inv == ["spv_carry.propose_from_realization"] and REGISTRY.get(inv[0]).tier == 1,
          "1b: of the Wave-2 verbs only spv_carry.propose_from_realization is invocable, and it is Tier 1 (it stops "
          "for approval)", f"{inv}")
    n = await platform(conn, "SELECT count(*) FROM workflow_definitions WHERE org_id = $1::uuid", PLATFORM_ORG_ID,
                       fetch="fetchval")
    find("1a the real platform org's workflow definitions (system workflows, read only)", f"{n}")
    n = await platform(conn, "SELECT count(*) FROM workflow_definitions WHERE is_active = false", fetch="fetchval")
    find("1a workflow_definitions with is_active = false (no route writes it; no run path reads it)", f"{n}")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation, the connection does not bypass RLS")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_wftemplates1.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_wftemplates1.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-300:] if code else ''}")
    for t in TEMPLATE_TABLES:
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
                                   "ANY($1::regclass[])", list(TEMPLATE_TABLES), fetch="fetchval"))
    mc = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                              "'wtv_maker_checker_chk'", fetch="fetchval")
    check(len(SEED_CHECK_LITERALS) > 0 and not bad and uq == set(UNIQUES) and fks >= 10
          and "checker_id <> drafted_by" in (mc or ""),
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints read); the "
          f"{len(UNIQUES)} UNIQUE indexes, the maker-checker CHECK and the FKs the seeds rely on are in the catalog",
          "; ".join(bad) + f" uq_missing={sorted(set(UNIQUES) - uq)} fks={fks} mc={mc}")
    S["literals_ok"] = not bad and uq == set(UNIQUES)


async def guard_prefix(conn) -> bool:
    vals = []
    for sql, arg in (("SELECT count(*) FROM public.workflow_templates WHERE template_key LIKE $1", f"{KEY_PREFIX}%"),
                     ("SELECT count(*) FROM public.workflow_definitions WHERE name LIKE $1", f"{NAME_PREFIX}%"),
                     ("SELECT count(*) FROM public.profiles WHERE name LIKE $1", "WFT1 Verify%"),
                     ("SELECT count(*) FROM public.roles WHERE name LIKE $1", "wft1\\_%")):
        vals.append(int(await platform(conn, sql, arg, fetch="fetchval")))
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1",
                           f"{FIXTURE_ID_PREFIX}%", fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    return check(all(v == 0 for v in vals) and o == 0 and u == 0,
                 f"the reserved prefixes (template keys '{KEY_PREFIX}', workflow names '{NAME_PREFIX}', profile / role "
                 "names, the fixture id range, the sign-in tag) match no real row", f"{vals} orgs={o} users={u}")


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
                  and bool(pr and pr.is_super_admin) is want_super and rbac.mirrored_role_string(names) == account_role)
            if not want_super:
                workflow_keys = {"author_workflows", "view_workflow_runs", "configure_workflow_triggers",
                                 "review_agent_proposals"}
                ok = ok and (perms & workflow_keys) == ROLE_PERMS[rname]
            if not ok:
                bad.append((str(uid), names, account_role, bool(pr and pr.is_super_admin), sorted(perms)[:12]))
    return check(not bad, "every fixture role is a user_roles grant the app decides as meant (two Super Admins; the "
                 "author holds the three workflow keys; the reviewer review_agent_proposals; the viewer "
                 "view_workflow_runs only; the member none)", f"{bad}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "p"), (ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"WFT1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted({p for ps in ROLE_PERMS.values() for p in ps} - set(perm_ids))
        if missing:
            raise RuntimeError(f"permissions absent from the catalog: {missing}")
        for org in (ORG_A, ORG_B):
            for rname, ps in ROLE_PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"WFT1 verify — {rname}")
                for p in sorted(ps):
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
        for uid, (org, account_role, _nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        for pid, (org, name) in PROFILES.items():
            await conn.execute("INSERT INTO profiles (id, org_id, name, description, is_seed) VALUES ($1, $2, $3, "
                               "'wftemplates1 verify', false)", pid, org, name)
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          list(USERS))}
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    check(us == set(USERS) and os_ == set(FIXTURE_ORGS), "fixture orgs (one platform, two tenants) and users are "
          "is_test = true", f"{len(us)}/{len(USERS)} users, {len(os_)}/3 orgs")
    S["seeded"] = await check_fixture_roles(conn) and us == set(USERS) and os_ == set(FIXTURE_ORGS)


def shipped(key: str, as_key: str | None = None) -> dict:
    t = WT.read_template_file(TEMPLATE_DIR, key)
    return {"key": as_key or t["key"], "title": t["title"], "category": t["category"], "description": t["description"],
            "bpmn_xml": t["bpmn_xml"], "parameters": t["parameters"]}


async def y_validator(conn):
    section("[Y] Validator: a script task, an unknown verb, a service task without a verb are refused (each named); "
            "the THREE writers call ONE validator")
    cases = {
        "script task": (linear_bpmn("bad1", [("user", "Prepare", None, None), ("script", "Run code", None, None)]),
                        "Script Task 'S_2' is not permitted"),
        "unknown verb": (linear_bpmn("bad2", [("service", "Do it", "zz.no_such_verb", None)]),
                         "unknown actionRegistryKey 'zz.no_such_verb' on element 'S_1'"),
        "service task without a verb": (linear_bpmn("bad3", [("service", "Do it", "", None)]),
                                        "serviceTask 'S_1' is missing an actionRegistryKey"),
    }
    n_before = int(await platform(conn, "SELECT count(*) FROM public.workflow_template_versions WHERE owner_org_id = "
                                        "$1::uuid", ORG_P, fetch="fetchval"))
    for label, (xml, want) in cases.items():
        body = {"key": f"{KEY_PREFIX}bad", "title": "Bad template", "category": "documents",
                "description": "A deliberately invalid template.", "bpmn_xml": xml, "parameters": []}
        code, res = await api(U_SA1, "POST", _P, body)
        check(code == 422 and want in detail_text(res), f"{label}: the platform draft is refused 422, naming it",
              f"{code} {detail_text(res)[:300]}")
    n_after = int(await platform(conn, "SELECT count(*) FROM public.workflow_template_versions WHERE owner_org_id = "
                                       "$1::uuid", ORG_P, fetch="fetchval"))
    check(n_after == n_before, "no template version was written for any refused draft", f"{n_before} → {n_after}")

    # One validator: the generator, the editor and the template writer each reach the SAME spied function.
    check(WT.G is G and ED.validate_workflow_bpmn is G.validate_workflow_bpmn,
          "the template service and the editor bind the generator module's own validator (no copy)")
    gen_xml = linear_bpmn("gen", [("user", "Review the request", None, None)])

    async def canned(system, messages, **kw):
        return gen_xml  # a LOCAL stub standing in for the model helper — no model is called

    saved_call = G.call_claude_text
    G.call_claude_text = canned
    try:
        n0 = len(VALIDATE_CALLS)
        pool = await get_pool()
        with _ctx(ORG_A):
            gen = await G.generate_workflow(pool, org_id=str(ORG_A), description=f"{NAME_PREFIX} generator writer",
                                            created_by=U_AAUTH, name=f"{NAME_PREFIX} generated")
        c_gen = callers_since(n0)
        n1 = len(VALIDATE_CALLS)
        with _ctx(ORG_A):
            await ED.save_new_version(pool, definition_id=gen["workflow_definition_id"], org_id=str(ORG_A),
                                      bpmn_xml=gen_xml.replace("Review the request", "Review it"),
                                      created_by=U_AAUTH, change_summary="verify edit")
        c_ed = callers_since(n1)
        n2 = len(VALIDATE_CALLS)
        WT.validate_template(shipped("document_intake")["bpmn_xml"],
                             WT.parse_parameters(shipped("document_intake")["parameters"]), platform=True)
        c_wt = callers_since(n2)
    finally:
        G.call_claude_text = saved_call
    check("generate_workflow" in c_gen, "writer 1 (the NL generator) calls the shared _validate", f"{c_gen[:6]}")
    check("save_new_version" in c_ed and "validate_workflow_bpmn" in c_ed,
          "writer 2 (the diagram editor) calls the shared _validate through validate_workflow_bpmn", f"{c_ed[:6]}")
    check("validate_template" in c_wt, "writer 3 (the template library) calls the shared _validate", f"{c_wt[:6]}")
    orig = getattr(G._validate, "original", None)
    check(callable(orig) and orig.__module__ == "services.workflow_nl_generator" and orig.__name__ == "_validate"
          and not hasattr(WT, "_validate") and not hasattr(ED, "_validate"),
          "every one of those calls reached the same single function, services.workflow_nl_generator._validate (no "
          "other module defines one)", f"{getattr(orig, '__module__', None)}")


async def y_shipped(conn):
    section("[Y] 10–12 platform templates authored; every one loads in --dry-run, validates, tiers from the registry")
    code, out, err = run_cmd([sys.executable, str(HERE / "load_workflow_templates.py"), "--dry-run"], REPO, timeout=600)
    m = re.search(r"^DRY RUN: (\d+) template\(s\) validated, (\d+) failed", out, re.M)
    ok_n = int(m.group(1)) if m else -1
    check(code == 0 and m is not None and 10 <= ok_n <= 12 and m.group(2) == "0",
          "load_workflow_templates.py --dry-run: 10–12 templates validated, 0 failed (offline)",
          f"{code} {m.group(0) if m else out[-300:]} {err[-200:]}")
    keys = WT.shipped_keys(TEMPLATE_DIR)
    check(10 <= len(keys) <= 12 and all((TEMPLATE_DIR / f"{k}.md").exists() for k in keys),
          f"{len(keys)} templates shipped as docs/workflow_templates/<key>.bpmn + <key>.md", f"{keys}")
    okc, failed, results = LOADER.dry_run(TEMPLATE_DIR)
    check(failed == 0 and okc == len(keys), "in-process dry run: every shipped template validates", f"{okc}/{len(keys)}")
    bad, verbs, approvals, cats = [], set(), [], set()
    for r in results:
        if not r["ok"]:
            bad.append((r["key"], r["errors"]))
            continue
        cats.add(r["category"])
        for s in r["steps"]:
            if s["step_type"] != "service":
                continue
            a = REGISTRY.get(s["action_registry_key"])
            verbs.add(s["action_registry_key"])
            want_eff = ENG.compute_effective_tier({"autonomy_tier": s["diagram_tier"]}, a)
            want_need = bool(a.workflow_invocable) and want_eff == ENG.SUSPEND_TIER
            if (a is None or s["registry_tier"] != a.tier or s["effective_tier"] != want_eff
                    or s["needs_approval"] is not want_need or s["access_type"] != a.access_type
                    or (want_need and not s["label"].startswith("Needs approval"))):
                bad.append((r["key"], s["step_key"], s["registry_tier"], getattr(a, "tier", None), s["effective_tier"]))
            if want_need:
                approvals.append((r["key"], s["step_key"]))
    check(not bad, "every service step's registry tier, access type, effective tier and 'needs approval' match the "
          "registry and the engine's own rule", f"{bad[:4]}")
    check(bool(verbs) and verbs <= WAVE2_VERBS, "every verb a shipped template names is Wave-2 safe (discovery 1b)",
          f"outside={sorted(verbs - WAVE2_VERBS)}")
    check(approvals == [("distribution_received", "Service_2")],
          "exactly one shipped step needs approval — distribution_received's carry proposal (Tier 1)", f"{approvals}")
    check(cats == {k for k, _ in WT.CATEGORIES}, "the shipped catalog covers all eight categories",
          f"missing={sorted({k for k, _ in WT.CATEGORIES} - cats)}")


async def draft(uid, key_file, as_key, bpmn_xml=None, title=None):
    t = shipped(key_file, as_key)
    if bpmn_xml is not None:
        t["bpmn_xml"] = bpmn_xml
    if title:
        t["title"] = title
    return await api(uid, "POST", _P, t)


async def version_row(conn, vid):
    r = await platform(conn, "SELECT * FROM public.workflow_template_versions WHERE id = $1::uuid AND system_to IS NULL",
                       str(vid), fetch="fetchrow")
    return dict(r) if r else None


async def y_platform(conn):
    section("[Y] Maker-checker: the drafter cannot publish a platform template; another Super Admin can; published "
            "content is immutable")
    code, d1 = await draft(U_SA1, "distribution_received", f"{KEY_PREFIX}distribution")
    check(code == 201 and d1.get("status") == "draft" and d1.get("version_no") == 1,
          "Super Admin 1 drafts the fixture platform template (draft, version 1)", f"{code} {dumps(d1)[:300]}")
    code2, d2 = await draft(U_SA1, "new_member_onboarding", f"{KEY_PREFIX}onboarding")
    check(code2 == 201 and d2.get("status") == "draft", "Super Admin 1 drafts a second template", f"{code2}")
    if code != 201 or code2 != 201:
        return
    S["dist_tid"], S["dist_v1"], S["onb_tid"], S["onb_v1"] = d1["template_id"], d1["id"], d2["template_id"], d2["id"]
    code, res = await api(U_AAUTH, "GET", f"{_T}/{d1['template_id']}")
    check(code == 404, "a tenant cannot read an unpublished platform draft (404)", f"{code}")
    before = await version_row(conn, d1["id"])
    code, res = await api(U_SA1, "POST", f"{_P}/versions/{d1['id']}/decide", {"outcome": "approve"})
    after = await version_row(conn, d1["id"])
    check(code == 403 and "maker-checker" in detail_text(res) and after == before,
          "the DRAFTER's own publish is refused 403 (maker-checker, VALID body) and the row is unchanged",
          f"{code} {detail_text(res)}")
    ref = await db_refusal(conn, {"app.is_super_admin": "true"},
                           "UPDATE public.workflow_template_versions SET status = 'published', checker_id = drafted_by, "
                           "published_at = now() WHERE id = $1::uuid", d1["id"])
    check(ref is not None and "wtv_maker_checker_chk" in ref,
          "the database refuses a publish by the drafter too (wtv_maker_checker_chk)", f"{ref}")
    for uid, label in ((U_AAUTH, "a tenant author"), (U_AVIEW, "a tenant viewer")):
        c1, _ = await api(uid, "POST", f"{_P}/versions/{d1['id']}/decide", {"outcome": "approve"})
        c2, _ = await draft(uid, "document_intake", f"{KEY_PREFIX}notsuper")
        check(c1 == 403 and c2 == 403, f"{label} can neither publish nor draft a platform template (403, VALID bodies)",
              f"{c1} {c2}")
    code, res = await api(U_SA2, "POST", f"{_P}/versions/{d1['id']}/decide", {"outcome": "approve"})
    row = await version_row(conn, d1["id"])
    check(code == 200 and row is not None and row["status"] == "published" and str(row["checker_id"]) == str(U_SA2)
          and str(row["drafted_by"]) == str(U_SA1), "a DIFFERENT Super Admin publishes it (checker recorded)",
          f"{code} {detail_text(res)} {row and row['status']}")
    arch = int(await platform(conn, "SELECT count(*) FROM public.workflow_template_versions WHERE archived_from_id = "
                                    "$1::uuid AND status = 'draft' AND system_to IS NOT NULL", d1["id"], fetch="fetchval"))
    check(arch == 1, "the outgoing draft state was archived on the system axis; the live row kept its id", f"{arch}")
    code, _ = await api(U_SA2, "POST", f"{_P}/versions/{d2['id']}/decide", {"outcome": "approve"})
    check(code == 200, "the second template is published by Super Admin 2", f"{code}")
    ref = await db_refusal(conn, {"app.is_super_admin": "true"},
                           "UPDATE public.workflow_template_versions SET bpmn_xml = bpmn_xml || ' ' WHERE id = $1::uuid",
                           d1["id"])
    check(ref is not None and "content is immutable" in ref, "a published version's BPMN cannot be changed (trigger)",
          f"{ref}")
    ref = await db_refusal(conn, {"app.is_super_admin": "false", "app.current_org_id": str(ORG_P)},
                           "UPDATE public.workflow_template_versions SET status = 'retired', retired_by = drafted_by, "
                           "retired_at = now(), retire_reason = 'x' WHERE id = $1::uuid", d1["id"])
    check(ref is not None and "only Super Admin retires" in ref,
          "without the Super Admin GUC even the owner org cannot retire a platform version (trigger)", f"{ref}")
    allowed = await platform(conn, "SELECT public.workflow_template_platform_org_allowed($1::uuid)", DEFAULT_ORG_ID,
                             fetch="fetchval")
    check(allowed is False, "a real client org can never own a platform template", f"{allowed}")
    code, d3 = await draft(U_SA1, "document_intake", f"{KEY_PREFIX}draftonly")
    S["draftonly_vid"] = d3.get("id") if code == 201 else None
    check(code == 201, "a third template stays a draft (for the isolation checks)", f"{code}")
    S["platform_ok"] = True


async def y_browse(conn):
    section("[Y] Browse / preview: the catalog (published platform + own), filter, search, steps with tiers, "
            "prerequisites named")
    code, res = await api(U_AMEM, "GET", _T)
    check(code == 403 and "view_workflow_runs" in detail_text(res), "a member (no workflow key) is refused the catalog",
          f"{code} {detail_text(res)}")
    code, view = await api(U_AVIEW, "GET", _T)
    keys = {t["key"] for t in view.get("templates", [])} if code == 200 else set()
    check(code == 200 and {f"{KEY_PREFIX}distribution", f"{KEY_PREFIX}onboarding"} <= keys
          and f"{KEY_PREFIX}draftonly" not in keys, "a viewer (view_workflow_runs only) browses the published "
          "fixture templates; the draft is absent", f"{code} {sorted(keys)[:6]}")
    p = view.get("permissions", {}) if code == 200 else {}
    vv = view.get("vocabularies", {}) if code == 200 else {}
    check(p.get("can_write") is False and vv.get("editable") == [] and len(vv.get("categories") or []) == 8,
          "the viewer's envelope: can_write false, editable an EMPTY list, the eight categories published",
          f"{p} {vv.get('editable')}")
    code, auth = await api(U_AAUTH, "GET", _T)
    check(code == 200 and auth["permissions"].get("can_write") is True
          and auth["vocabularies"].get("editable") == ["use", "save", "retire"],
          "an author's envelope: can_write true, editable [use, save, retire]", f"{code} {dumps(auth.get('permissions'))}")
    fixture = {t["key"] for t in auth.get("templates", []) if t["key"].startswith(KEY_PREFIX)}
    code, cat = await api(U_AAUTH, "GET", _T, params={"category": "cash_transfers"})
    ck = {t["key"] for t in cat.get("templates", []) if t["key"].startswith(KEY_PREFIX)} if code == 200 else set()
    sql = {r["template_key"] for r in await scoped(
        conn, ORG_A, "SELECT DISTINCT t.template_key FROM public.workflow_template_versions v JOIN "
                     "public.workflow_templates t ON t.id = v.template_id WHERE v.system_to IS NULL AND v.status = "
                     "'published' AND v.category = $1 AND t.template_key LIKE $2", "cash_transfers", f"{KEY_PREFIX}%")}
    check(code == 200 and bool(ck) and ck < fixture and ck == sql,
          "the category filter narrows: non-empty, a strict subset, equal to the same predicate in SQL",
          f"{sorted(ck)} ⊂ {sorted(fixture)} sql={sorted(sql)}")
    code, srch = await api(U_AAUTH, "GET", _T, params={"q": "KYC"})
    sk = {t["key"] for t in srch.get("templates", []) if t["key"].startswith(KEY_PREFIX)} if code == 200 else set()
    check(code == 200 and sk == {f"{KEY_PREFIX}onboarding"}, "search 'KYC' finds the onboarding template only",
          f"{sorted(sk)}")
    code, bad = await api(U_AAUTH, "GET", _T, params={"category": "zz_not_a_category"})
    check(code == 422, "an unknown category is refused 422 (not silently ignored)", f"{code}")
    code, det = await api(U_AVIEW, "GET", f"{_T}/{S['dist_tid']}")
    steps = {s["step_key"]: s for s in det.get("version", {}).get("steps", [])} if code == 200 else {}
    carry = steps.get("Service_2") or {}
    check(code == 200 and carry.get("needs_approval") is True and carry.get("registry_tier") == 1
          and carry.get("effective_tier") == 1 and str(carry.get("label", "")).startswith("Needs approval")
          and steps.get("Service_1", {}).get("needs_approval") is False,
          "the template page: the Tier-1 carry step reads 'Needs approval'; the read step does not", dumps(carry)[:300])
    dver = det.get("version", {}) if code == 200 else {}
    pnames = [p.get("name") for p in (dver.get("parameters") or [])]
    want_pnames = [p["name"] for p in shipped("distribution_received")["parameters"]]
    check(bool(code == 200 and "BPMNShape" in dver.get("drawable_xml", "") and "{{" not in dver.get("drawable_xml", "")
               and want_pnames and pnames == want_pnames),
          "the page carries a drawable diagram (layout generated, placeholders at defaults) and the parameters — "
          "exactly the shipped template's, in order", f"{pnames} want {want_pnames}")
    pre = {p["key"]: p for p in det.get("prerequisites", [])} if code == 200 else {}
    check(pre.get("manage_billing", {}).get("met") is False and pre.get("author_workflows", {}).get("met") is False
          and "you do not hold author_workflows" in det.get("missing_prerequisites", [])
          and pre.get("review_agent_proposals", {}).get("met") is True,
          "prerequisites for the viewer: author_workflows and manage_billing MISSING (named), a reviewer in the org "
          "present", dumps(det.get("prerequisites"))[:400])
    code, detb = await api(U_BAUTH, "GET", f"{_T}/{S['dist_tid']}")
    preb = {p["key"]: p for p in detb.get("prerequisites", [])} if code == 200 else {}
    check(code == 200 and preb.get("review_agent_proposals", {}).get("met") is False
          and any("no one in your firm holds review_agent_proposals" in m for m in detb.get("missing_prerequisites", [])),
          "tenant B (no reviewer) is told no one in the firm holds review_agent_proposals", dumps(preb)[:300])
    code, _ = await api(U_AVIEW, "GET", f"{_T}/{_id(9999)}")
    check(code == 404, "an unknown template id is a 404", f"{code}")
    S["browse_ok"] = True


async def def_versions(conn, def_id) -> list[dict]:
    return [dict(r) for r in await platform(
        conn, "SELECT id, version_number, bpmn_xml, is_current FROM workflow_versions WHERE workflow_definition_id = "
              "$1::uuid ORDER BY version_number", str(def_id))]


async def y_use(conn):
    section("[Y] Use: parameters → a draft copy owned by the org, provenance, re-validated; runs on the engine with "
            "Tier-1 suspension; editing never touches the template; a newer version never overwrites the copy")
    n_defs = lambda: platform(conn, "SELECT count(*) FROM workflow_definitions WHERE org_id = $1", ORG_A,  # noqa: E731
                              fetch="fetchval")
    before = int(await n_defs())
    body = {"parameters": {"notify_channel": "email"}, "name": f"{NAME_PREFIX} distribution copy"}
    for uid, label in ((U_AVIEW, "a viewer"), (U_AMEM, "a member")):
        code, res = await api(uid, "POST", f"{_T}/{S['dist_tid']}/use", body)
        check(code == 403 and "author_workflows" in detail_text(res),
              f"{label} cannot use a template (403 naming author_workflows, VALID body)", f"{code} {detail_text(res)}")
    code, res = await api(U_AAUTH, "POST", f"{_T}/{S['dist_tid']}/use",
                          {"parameters": {"notify_channel": "a fax"}, "name": f"{NAME_PREFIX} bad"})
    check(code == 422 and "How members are told" in detail_text(res), "a parameter value outside its choices is "
          "refused 422, naming the parameter", f"{code} {detail_text(res)[:200]}")
    check(int(await n_defs()) == before, "no definition was written by any refused use", f"{before}")
    code, cp = await api(U_AAUTH, "POST", f"{_T}/{S['dist_tid']}/use", body)
    check(code == 201 and cp.get("version_number") == 1 and cp.get("template_version_no") == 1
          and cp.get("triggers") == 0, "an author copies the template: a NEW definition + version 1, no trigger",
          f"{code} {dumps(cp)[:300]}")
    if code != 201:
        return
    S["copy_def"], S["copy_ver"] = cp["workflow_definition_id"], cp["workflow_version_id"]
    vers = await def_versions(conn, cp["workflow_definition_id"])
    xml = vers[0]["bpmn_xml"] if vers else ""
    org = await platform(conn, "SELECT org_id FROM workflow_definitions WHERE id = $1::uuid", cp["workflow_definition_id"],
                         fetch="fetchval")
    check(str(org) == str(ORG_A) and len(vers) == 1 and vers[0]["is_current"] is True
          and "Notify members by email" in xml and "{{" not in xml,
          "the copy lives in org A, version 1 is current, the parameter is substituted and no placeholder remains",
          f"{org} {len(vers)}")
    errs = G._validate.original(xml, {a.key for a in REGISTRY.all()},
                                {str(r["id"]) for r in await scoped(conn, ORG_A, "SELECT id FROM profiles")})
    check(errs == [], "the stored copy passes the shared validator", f"{errs}")
    prov = await platform(conn, "SELECT * FROM public.workflow_template_copies WHERE workflow_definition_id = $1::uuid",
                          cp["workflow_definition_id"], fetch="fetchrow")
    check(prov is not None and str(prov["template_version_id"]) == S["dist_v1"] and prov["template_version_no"] == 1
          and json.loads(prov["parameter_values"]).get("notify_channel") == "email" and str(prov["org_id"]) == str(ORG_A),
          "provenance recorded: which template version, the values used, the copying org",
          dumps(dict(prov) if prov else None)[:300])
    trig = int(await platform(conn, "SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = $1::uuid",
                              cp["workflow_definition_id"], fetch="fetchval"))
    check(trig == 0, "the copy is a draft: no trigger exists until someone with configure_workflow_triggers adds one",
          f"{trig}")
    # Profile parameter: own Profile accepted; another org's refused by the SHARED validator.
    code, op = await api(U_AAUTH, "POST", f"{_T}/{S['onb_tid']}/use",
                         {"parameters": {"kyc_reviewer": str(PROFILE_A), "welcome_days": 7},
                          "name": f"{NAME_PREFIX} onboarding copy"})
    oxml = (await def_versions(conn, op["workflow_definition_id"]))[0]["bpmn_xml"] if code == 201 else ""
    check(code == 201 and f'assignedRoleProfileId="{PROFILE_A}"' in oxml and "within 7 days" in oxml,
          "a Profile parameter takes one of the org's own Profiles", f"{code}")
    code, res = await api(U_AAUTH, "POST", f"{_T}/{S['onb_tid']}/use",
                          {"parameters": {"kyc_reviewer": str(PROFILE_B), "welcome_days": 7},
                           "name": f"{NAME_PREFIX} cross-org profile"})
    check(code == 422 and f"unknown assignedRoleProfileId '{PROFILE_B}'" in detail_text(res),
          "another org's Profile is refused by the shared validator (422, named)", f"{code} {detail_text(res)[:200]}")
    # The engine: a Tier-1 step suspends for approval.
    pool = await get_pool()
    with _ctx(ORG_A):
        run = await ENG.start_workflow_run(pool, UUID(S["copy_ver"]), ORG_A, {"verify": "wftemplates1"}, U_AAUTH)
    rs = {r["step_key"]: dict(r) for r in await platform(
        conn, "SELECT s.step_key, rs.status, rs.agent_proposal_id, rs.result FROM workflow_run_steps rs JOIN "
              "workflow_steps s ON s.id = rs.workflow_step_id WHERE rs.workflow_run_id = $1", run["run_id"])}
    svc1 = json.loads(rs.get("Service_1", {}).get("result") or "{}")
    check(run["status"] == "awaiting_approval" and run["suspended_at"] == "Service_2"
          and rs.get("Service_2", {}).get("status") == "suspended" and rs["Service_2"]["agent_proposal_id"] is not None
          and rs.get("Service_2", {}).get("result") is None and svc1.get("invoked") is False
          and rs.get("Service_1", {}).get("status") == "completed",
          "the copy runs on the engine: the read step resolves (nothing invoked) and the Tier-1 carry step SUSPENDS "
          "for approval, unexecuted, with an agent proposal", f"{run} {dumps(rs)[:400]}")
    S["run1"] = run["run_id"]
    # Editing the copy never changes the template.
    tv_before = await fingerprint(conn, "SELECT * FROM public.workflow_template_versions WHERE template_id = $1::uuid",
                                  S["dist_tid"])
    with _ctx(ORG_A):
        ed = await ED.save_new_version(pool, definition_id=UUID(S["copy_def"]), org_id=str(ORG_A),
                                       bpmn_xml=xml.replace("Notify members by email", "Notify members by email today"),
                                       created_by=U_AAUTH, change_summary="verify: the org edits its copy")
    tv_after = await fingerprint(conn, "SELECT * FROM public.workflow_template_versions WHERE template_id = $1::uuid",
                                 S["dist_tid"])
    check(ed["version_number"] == 2 and tv_after == tv_before,
          "editing the copy (version 2 of the org's workflow) leaves every template version row unchanged",
          f"{ed} {tv_before} → {tv_after}")
    # A new template version: announced, never overwriting.
    copy_before = await fingerprint(conn, "SELECT id, version_number, bpmn_xml, is_current FROM workflow_versions "
                                          "WHERE workflow_definition_id = $1::uuid", S["copy_def"])
    prov_before = await fingerprint(conn, "SELECT * FROM public.workflow_template_copies WHERE org_id = $1::uuid",
                                    str(ORG_A))
    v2xml = shipped("distribution_received")["bpmn_xml"].replace(
        '<bpmn:endEvent id="End_1" name="Done">', '<bpmn:userTask id="Task_5" name="Tell the general partner">'
        '<bpmn:incoming>Flow_5</bpmn:incoming><bpmn:outgoing>Flow_6</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:endEvent id="End_1" name="Done">').replace(
        '<bpmn:incoming>Flow_5</bpmn:incoming>\n    </bpmn:endEvent>', '<bpmn:incoming>Flow_6</bpmn:incoming>\n    </bpmn:endEvent>').replace(
        '<bpmn:sequenceFlow id="Flow_5" sourceRef="Task_4" targetRef="End_1"/>',
        '<bpmn:sequenceFlow id="Flow_5" sourceRef="Task_4" targetRef="Task_5"/>'
        '<bpmn:sequenceFlow id="Flow_6" sourceRef="Task_5" targetRef="End_1"/>')
    code, d2 = await draft(U_SA1, "distribution_received", f"{KEY_PREFIX}distribution", bpmn_xml=v2xml)
    code2, _ = await api(U_SA2, "POST", f"{_P}/versions/{d2.get('id')}/decide", {"outcome": "approve"})
    check(code == 201 and code2 == 200 and d2.get("version_no") == 2, "version 2 of the template is drafted and "
          "published by two Super Admins", f"{code} {code2} {detail_text(d2)}")
    S["dist_v2"] = d2.get("id")
    code, cps = await api(U_AAUTH, "GET", f"{_T}/copies")
    mine = next((c for c in cps.get("copies", []) if c["workflow_definition_id"] == S["copy_def"]), {}) if code == 200 else {}
    nv = mine.get("newer_version") or {}
    added = [a.get("step_key") for a in (nv.get("diff") or {}).get("added", [])]
    check(code == 200 and nv.get("version_no") == 2 and added == ["Task_5"],
          "the org is told a newer version exists, with the step diff (Task_5 added)", dumps(mine)[:400])
    copy_after = await fingerprint(conn, "SELECT id, version_number, bpmn_xml, is_current FROM workflow_versions "
                                         "WHERE workflow_definition_id = $1::uuid", S["copy_def"])
    prov_after = await fingerprint(conn, "SELECT * FROM public.workflow_template_copies WHERE org_id = $1::uuid",
                                   str(ORG_A))
    check(copy_after == copy_before and prov_after == prov_before,
          "the new template version changed nothing of the org's copy or its provenance", f"{copy_before} → {copy_after}")
    # Retire version 1: the copy keeps working; version 1 is no longer copied.
    code, _ = await api(U_SA2, "POST", f"{_P}/versions/{S['dist_v1']}/retire", {"reason": "verify: superseded by v2"})
    check(code == 200, "Super Admin retires version 1", f"{code}")
    code, res = await api(U_AAUTH, "POST", f"{_T}/{S['dist_tid']}/use",
                          {"version_id": S["dist_v1"], "parameters": {}, "name": f"{NAME_PREFIX} from retired"})
    check(code == 409 and "retired" in detail_text(res), "a retired version is no longer copied (409)",
          f"{code} {detail_text(res)}")
    cur = await platform(conn, "SELECT id FROM workflow_versions WHERE workflow_definition_id = $1::uuid AND is_current",
                         S["copy_def"], fetch="fetchval")
    with _ctx(ORG_A):
        run2 = await ENG.start_workflow_run(pool, cur, ORG_A, {"verify": "after retire"}, U_AAUTH)
    check(run2["status"] == "awaiting_approval" and run2["suspended_at"] == "Service_2",
          "the copy made from the retired version still runs (and still suspends at the Tier-1 step)", f"{run2}")
    code, catn = await api(U_AAUTH, "GET", _T)
    d = next((t for t in catn.get("templates", []) if t["key"] == f"{KEY_PREFIX}distribution"), {}) if code == 200 else {}
    check(d.get("version_no") == 2, "the catalog now offers version 2", f"{d.get('version_no')}")
    S["use_ok"] = True


async def y_org_templates(conn):
    section("[Y] Org templates: saved by an author, visible only inside the org (org B 404), versions, retire")
    body = {"workflow_definition_id": S["copy_def"], "key": f"{KEY_PREFIX}ours", "category": "cash_transfers",
            "title": "Our distribution process", "description": "Our firm's own distribution checklist."}
    code, res = await api(U_AVIEW, "POST", f"{_T}/org", body)
    check(code == 403 and "author_workflows" in detail_text(res), "a viewer cannot save an org template (403, VALID "
          "body)", f"{code}")
    code, ot = await api(U_AAUTH, "POST", f"{_T}/org", body)
    check(code == 201 and ot.get("scope") == "org" and ot.get("status") == "published" and ot.get("version_no") == 1,
          "an author saves the org's workflow as an org template (published inside the org)", f"{code} {detail_text(ot)}")
    if code != 201:
        return
    S["org_tid"], S["org_v1"] = ot["template_id"], ot["id"]
    code, ot2 = await api(U_AAUTH, "POST", f"{_T}/org", body)
    check(code == 201 and ot2.get("version_no") == 2, "saving again makes version 2 of the same org template", f"{code}")
    code, cat = await api(U_AVIEW, "GET", _T)
    own = next((t for t in cat.get("templates", []) if t["key"] == f"{KEY_PREFIX}ours"), None) if code == 200 else None
    check(own is not None and own.get("is_own") is True and own.get("scope") == "org",
          "org A's viewer sees the org template in the catalog (its own)", dumps(own)[:200])
    code, catb = await api(U_BAUTH, "GET", _T)
    check(code == 200 and all(t["key"] != f"{KEY_PREFIX}ours" for t in catb.get("templates", [])),
          "org B's catalog does not contain it", f"{code}")
    code, cats = await api(U_SA2, "GET", _T)
    check(code == 200 and all(t["key"] != f"{KEY_PREFIX}ours" for t in cats.get("templates", []))
          and any(t["key"] == f"{KEY_PREFIX}onboarding" for t in cats.get("templates", [])),
          "a Super Admin's catalog (under the Super Admin GUC, which RLS lets read every org) still lists only "
          "platform templates and its own org's — never org A's org template", f"{code}")
    c1, _ = await api(U_BAUTH, "GET", f"{_T}/{ot['template_id']}")
    c2, _ = await api(U_BAUTH, "POST", f"{_T}/{ot['template_id']}/use", {"parameters": {}, "name": f"{NAME_PREFIX} b"})
    c3, _ = await api(U_BAUTH, "POST", f"{_T}/versions/{ot['id']}/retire", {"reason": "not ours"})
    check(c1 == 404 and c2 == 404 and c3 == 404, "org B: read, use and retire of org A's template are each 404",
          f"{c1} {c2} {c3}")
    code, oc = await api(U_AAUTH, "POST", f"{_T}/{ot['template_id']}/use", {"parameters": {}, "name": f"{NAME_PREFIX} own"})
    check(code == 201, "org A copies its own org template", f"{code}")
    code, _ = await api(U_AAUTH, "POST", f"{_T}/versions/{ot['id']}/retire", {"reason": "verify retire"})
    ex = await platform(conn, "SELECT count(*) FROM workflow_definitions WHERE id = $1::uuid",
                        oc.get("workflow_definition_id"), fetch="fetchval")
    check(code == 200 and ex == 1, "retiring the org version leaves the copy in place", f"{code} {ex}")
    ref = await db_refusal(conn, {"app.is_super_admin": "false", "app.current_org_id": str(ORG_A)},
                           "INSERT INTO public.workflow_template_versions (template_id, owner_org_id, scope, version_no, "
                           "status, title, category, description, bpmn_xml, bpmn_sha256, steps, drafted_by) SELECT "
                           "template_id, owner_org_id, scope, 99, 'draft', title, category, description, bpmn_xml, "
                           "bpmn_sha256, steps, drafted_by FROM public.workflow_template_versions WHERE id = $1::uuid",
                           ot["id"])
    check(ref is not None and "saved published" in ref, "an org version can never be a draft (trigger)", f"{ref}")
    S["org_ok"] = True


async def y_isolation(conn):
    section("[Y] Isolation both directions (rolbypassrls = false); a real org never sees a fixture platform template")
    code, bc = await api(U_BAUTH, "POST", f"{_T}/{S['onb_tid']}/use",
                         {"parameters": {"kyc_reviewer": str(PROFILE_B), "welcome_days": 3},
                          "name": f"{NAME_PREFIX} b copy"})
    check(code == 201, "org B copies a platform template with its own Profile", f"{code} {detail_text(bc)}")
    a_sees_b = await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_template_copies WHERE org_id = $1::uuid",
                            ORG_B, fetch="fetchval")
    b_sees_a = await scoped(conn, ORG_B, "SELECT count(*) FROM public.workflow_template_copies WHERE org_id = $1::uuid",
                            ORG_A, fetch="fetchval")
    a_own = await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_template_copies", fetch="fetchval")
    check(a_sees_b == 0 and b_sees_a == 0 and a_own > 0,
          "copies: org A sees none of org B's and B none of A's; A sees its own (non-empty)", f"{a_sees_b} {b_sees_a} {a_own}")
    b_org_tpl = await scoped(conn, ORG_B, "SELECT count(*) FROM public.workflow_template_versions WHERE owner_org_id = "
                                          "$1::uuid", ORG_A, fetch="fetchval")
    a_draft = await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_template_versions WHERE status = 'draft' "
                                        "AND owner_org_id = $1::uuid", ORG_P, fetch="fetchval")
    p_draft = await scoped(conn, ORG_P, "SELECT count(*) FROM public.workflow_template_versions WHERE status = 'draft' "
                                        "AND owner_org_id = $1::uuid", ORG_P, fetch="fetchval")
    check(b_org_tpl == 0 and a_draft == 0 and p_draft > 0,
          "org B reads none of org A's org-template versions; a tenant reads no platform DRAFT; the owner does",
          f"{b_org_tpl} {a_draft} {p_draft}")
    real = await scoped(conn, DEFAULT_ORG_ID, "SELECT count(*) FROM public.workflow_template_versions WHERE "
                                              "owner_org_id = $1::uuid", ORG_P, fetch="fetchval")
    real_t = await scoped(conn, DEFAULT_ORG_ID, "SELECT count(*) FROM public.workflow_templates WHERE owner_org_id = "
                                                "$1::uuid", ORG_P, fetch="fetchval")
    tenant = await scoped(conn, ORG_A, "SELECT count(*) FROM public.workflow_template_versions WHERE owner_org_id = "
                                       "$1::uuid AND status = 'published'", ORG_P, fetch="fetchval")
    check(real == 0 and real_t == 0 and tenant > 0,
          "a REAL org (2nd Act, is_test = false) reads no fixture platform template; a fixture tenant does", f"{real} "
          f"{real_t} {tenant}")
    ins = await db_refusal(conn, {"app.is_super_admin": "false", "app.current_org_id": str(ORG_B)},
                           "INSERT INTO public.workflow_template_copies (org_id, template_id, template_version_id, "
                           "template_owner_org_id, template_version_no, workflow_definition_id, workflow_version_id, "
                           "copied_by) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 2, gen_random_uuid(), "
                           "gen_random_uuid(), $5::uuid)", str(ORG_A), S["dist_tid"], S["dist_v2"], str(ORG_P),
                           str(U_BAUTH))
    check(ins is not None and "row-level security" in ins,
          "org B cannot write a copy record into org A — refused by RLS (the row is otherwise valid)", f"{ins}")


async def y_usage(conn):
    section("[Y] Governance: usage counts per template version, Super Admin only, counts only; every action audited")
    code, res = await api(U_AAUTH, "GET", _P)
    check(code == 403, "a tenant author cannot read the platform screen", f"{code}")
    code, ov = await api(U_SA2, "GET", _P)
    tpl = next((t for t in ov.get("templates", []) if t["key"] == f"{KEY_PREFIX}onboarding"), {}) if code == 200 else {}
    v1 = next((v for v in tpl.get("versions", []) if v["version_no"] == 1), {})
    raw = dumps(ov)
    leaked = [str(x) for x in (ORG_A, ORG_B, U_AAUTH, U_BAUTH, PROFILE_A, PROFILE_B) if str(x) in raw]
    leaked += [k for k in ("copy_def", "copy_ver") if S.get(k) and S[k] in raw]
    check(code == 200 and v1.get("copies") == 2 and v1.get("orgs") == 2 and not leaked,
          "the onboarding template: 2 copies in 2 organisations — and no tenant org, person, Profile or definition id "
          "anywhere in the response", f"{code} {v1.get('copies')} {v1.get('orgs')} leaked={leaked}")
    dv = next((t for t in ov.get("templates", []) if t["key"] == f"{KEY_PREFIX}distribution"), {}) if code == 200 else {}
    states = sorted((v["version_no"], v["status"]) for v in dv.get("versions", []))
    check(states == [(1, "retired"), (2, "published")], "the platform screen shows version 1 retired and 2 published",
          f"{states}")
    dr = next((t for t in ov.get("templates", []) if t["key"] == f"{KEY_PREFIX}draftonly"), {}) if code == 200 else {}
    dv1 = (dr.get("versions") or [{}])[0]
    code1, ov1 = await api(U_SA1, "GET", _P)
    dr1 = next((t for t in ov1.get("templates", []) if t["key"] == f"{KEY_PREFIX}draftonly"), {}) if code1 == 200 else {}
    check(dv1.get("can_decide") is True and ((dr1.get("versions") or [{}])[0]).get("can_decide") is False,
          "the server's can_decide: true for the second Super Admin, false for the drafter", f"{dv1.get('can_decide')}")
    acts = {r["action"]: int(r["n"]) for r in await platform(
        conn, "SELECT action, count(*) AS n FROM audit_log WHERE org_id = ANY($1::uuid[]) AND action LIKE "
              "'workflow_template_%' GROUP BY action", FIXTURE_ORGS)}
    want = {"workflow_template_drafted", "workflow_template_published", "workflow_template_copied",
            "workflow_template_saved", "workflow_template_retired"}
    check(want <= set(acts), "every action is audited (drafted, published, copied, saved, retired — fixture orgs only)",
          f"{acts}")


async def y_real_rows(conn):
    section("[Y] Real rows untouched (count + checksum); the real platform org gets NO template row")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after.get(k) == v, f"real {k} unchanged (count + checksum)", f"{v} → {after.get(k)}")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    self_code(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-300:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0, "visibility1_routes.py --check: current", (out + err)[-300:])
    data = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
    rows = data.get("routes", []) if isinstance(data, dict) else data
    have = {(m, r.get("path")): r.get("after") for r in rows for m in (r.get("methods") or [])}
    miss = [(k, g) for k, g in ROUTES.items() if g not in str(have.get(k))]
    check(not miss, f"each of wftemplates1's {len(ROUTES)} routes is in AUTHZ1_ROUTES.json with its gate", f"{miss[:4]}")


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
          f"the wftemplates1 render tests ({NODE_TEST}, run by path) all pass ({n.group(1) if n else '?'})",
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
        await run("shipped templates", y_shipped, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("validator", y_validator, conn)
            await run("platform", y_platform, conn)
            if S.get("platform_ok"):
                await run("browse", y_browse, conn)
                await run("use", y_use, conn)
                if S.get("use_ok"):
                    await run("org templates", y_org_templates, conn)
                    await run("isolation", y_isolation, conn)
                    await run("usage", y_usage, conn)
                else:
                    check(False, "the platform template was copied and run", "later sections skipped")
            else:
                check(False, "the fixture platform templates were drafted and published", "later sections skipped")
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run",
              f"{NETWORK_CALLS[:3]} {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (template rows, copies, runs, proposals and todos included)",
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
