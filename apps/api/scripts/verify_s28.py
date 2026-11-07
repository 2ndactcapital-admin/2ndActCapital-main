"""verify_s28 — the AI drift monitor (S28): metrics over the decision log, drift rules with thresholds and minimum
samples, governed alerts (one per org / task / rule, refreshed, auto-closed), versioned thresholds (Super Admin only,
per-task override), the platform job (fires / holds / OFF), the dry-run script, the page's API, isolation.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_s28.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect``).

NO MODEL AND NO NETWORK: the LLM helpers (call_claude_json / call_claude_text / call_claude_with_tools and the S27
chain executor) are replaced by stubs with their CURRENT signatures (asserted equal) that record and refuse any call;
httpx's transport refuses and records any real request. The monitor must make zero of either.

FIXTURES: four orgs (is_test, asserted) — ORG_P, a fixture PLATFORM org the job is seeded into (added to
security_master_jobs.PLATFORM_JOB_ORG_IDS for this run), ORG_A and ORG_B, client orgs; a fixture Super Admin in ORG_P,
an org admin in ORG_A and in ORG_B, a MEMBER in ORG_A (a client role, no manage_org_settings). Every ai_decision_log
row is written into a fixture org with a reserved task_type prefix ``zzd28_`` (matching no real row, asserted), at
created_at instants derived from an anchor captured at seed time — every window edge is INJECTED, never the clock.
Per-task threshold overrides exist only for ``zzd28_`` tasks; the platform defaults (real configuration) are only read.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN, by fixture org / user ids and by fixture
task type (thresholds). Every touched table is re-read for its exact pre-test count BY FIXTURE IDS ONLY (audit_log,
member_todos, ai_decision_log, the three drift tables, workflow rows, users, roles); real rows (decision-log rows that
existed before the run, real thresholds, real alerts and events, real triggers, real drift todos) are compared by
count + checksum keyed by their real id.

--self-checks-only: git / doc / route self-checks only. --cleanup-only: teardown + leftover check. With
VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import ast
import asyncio
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402
from _verify_total import nested_total  # noqa: E402

SPRINT_KEY = "s28"
BEFORE_REF = "3362b7f"   # s28-work @ merge rep13 — the sprint's base
ROUTES = {("GET", "/api/v1/admin/ai/drift"), ("GET", "/api/v1/admin/ai/drift/tasks/{task_type}"),
          ("GET", "/api/v1/admin/ai/drift/thresholds"), ("PUT", "/api/v1/admin/ai/drift/thresholds")}
GATES = {("GET", "/api/v1/admin/ai/drift"): "manage_org_settings",
         ("GET", "/api/v1/admin/ai/drift/tasks/{task_type}"): "manage_org_settings",
         ("GET", "/api/v1/admin/ai/drift/thresholds"): "manage_org_settings",
         ("PUT", "/api/v1/admin/ai/drift/thresholds"): "super"}


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ═══ Self-checks (no database) ══════════════════════════════════════════════
TASK1 = (
    ("docs/S28_DISCOVERY.md", ("## 1a.", "_write_ai_decision", "_safe_log", "edgar_inventory.py", "log_decision",
                               "document_embedding.py", "47 rows with NULL cost", "outside the router",
                               "verify artifacts in REAL orgs"), "1a: every writer, the router, the gaps"),
    ("docs/S28_DISCOVERY.md", ("## 1b.", "662 rows", "member_brief", "calls/active day", "median / p95",
                               "fallback", "failure", "bypass", "effort", "gpt-5-mini-2025-08-07", "every 6 hours"),
     "1b: the live profile per task and the cadence"),
    ("docs/S28_DISCOVERY.md", ("## 1c.", "one event in the recent window must never cross", "⌈100 / N⌉", "20",
                               "version 2", "Not enough data", "member_brief"), "1c: the minimum-sample rule and who lacks history"),
    ("docs/S28_DISCOVERY.md", ("## 1d.", "member_todos", "create_held_run_alerts", "Platform jobs",
                               "jobs/{job_key}/schedule", "create_ai_drift_alert_todos"), "1d: the alert path and the job switch"),
)


def self_routes(check, find):
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "s28 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms}
    check(mine == ROUTES, "s28 adds exactly its four drift routes", f"{o.where()}; got {sorted(mine)}")
    bad = []
    for (ms, p) in o.new:
        for m in ms:
            want = GATES.get((m, p))
            after = str(o.end[(ms, p)].get("after") or "")
            if not want or want not in after:
                bad.append((m, p, after))
    check(not bad and bool(o.new), "each drift route's recorded gate: reads manage_org_settings, the write Super Admin",
          f"{bad}")
    check(VS.still_gated(o) == [], "each drift route exists today with the same gate", f"{VS.still_gated(o)}")


def self_static(check, find):
    for rel, needles, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or _read(rel)
        miss = [n for n in needles if n not in txt]
        check(bool(txt) and not miss, f"Task 1 — {meaning}", f"{rel}: missing {miss}")
    old_jobs = VS.git_show(BEFORE_REF, "apps/api/services/security_master_jobs.py") or ""
    check(bool(old_jobs) and "ai_drift_monitor" not in old_jobs
          and VS.git_show(BEFORE_REF, "apps/api/services/ai_drift.py") is None,
          "REPRODUCED before the sprint: no drift monitor module and no drift job", "")
    jobs = _read("apps/api/services/security_master_jobs.py")
    check('"system:AI — drift monitor"' in jobs and '"ai.drift_monitor"' in jobs and '"ai_drift_monitor":' in jobs,
          "the platform job 'AI — drift monitor' is defined on the existing jobs list (one switch screen)")
    sql = _read("docs/s28_part1.sql")
    check("ai_drift_alerts_open_uq" in sql and "ai_drift_thresholds_live_uq" in sql
          and not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b|\bDROP\b", sql, re.I),
          "the migration is additive (no DELETE / TRUNCATE / DROP)")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. S28 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    need = ("member_brief", "min_baseline", "every 6 hours", "dry run", "switch", "verify_s28.py", "OFF")
    check(bool(top) and all(n in top for n in need), "Task 8: PROJECT_STATUS entry — profile, sample rule, cadence, "
          "dry-run findings, the operator step", f"missing {[n for n in need if n not in top]}")
    check("verify_s28.py" in _read("scripts/regression_set.txt"), "Task 8: in scripts/regression_set.txt")
    dry = _read("docs/S28_DRY_RUN.txt")
    check("FINDINGS:" in dry and "read-only" in dry and "REPLAY" in dry,
          "Task 5: the live dry-run output (and the incident replay) is recorded", f"{len(dry)} chars")
    src = _read("apps/api/services/ai_drift.py")
    tree = ast.parse(src) if src else None
    imported = set()
    for node in ast.walk(tree) if tree else []:
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(f"{node.module}:{','.join(a.name for a in node.names)}")
    banned = [i for i in imported if re.search(r"anthropic|httpx|requests|openai|litellm|extraction|call_claude", i,
                                               re.I)]
    check(bool(tree) and not banned, "services/ai_drift.py imports no model client, no HTTP client, no AI helper",
          f"{sorted(imported)}")


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

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import ai_drift as AD  # noqa: E402
from services import extraction  # noqa: E402
from services import rbac  # noqa: E402
from services import security_master_jobs as J  # noqa: E402
from services import users as users_mod  # noqa: E402
from services import workflow_scheduler as WS  # noqa: E402
from services import workflow_todos as WT  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.workflow_schedule import next_occurrences  # noqa: E402

TAG = "s28verify"
TASK_PREFIX = "zzd28_"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
D = Decimal


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e28{n:02d}")


ORG_P, ORG_A, ORG_B = _id(1), _id(2), _id(3)
FIXTURE_ORGS = [ORG_P, ORG_A, ORG_B]
U_SUPER, U_ADMIN_A, U_MEMBER_A, U_ADMIN_B = _id(11), _id(12), _id(13), _id(14)
USERS = {U_SUPER: (ORG_P, "super_admin", "super", "super_admin", "S28 Super Admin"),
         U_ADMIN_A: (ORG_A, "org_admin", "admin-a", "org_admin", "S28 Org Admin A"),
         U_MEMBER_A: (ORG_A, "member", "member-a", "zzd28_member", "S28 Member A"),
         U_ADMIN_B: (ORG_B, "org_admin", "admin-b", "org_admin", "S28 Org Admin B")}

S: dict = {}
NETWORK_CALLS: list[str] = []
LLM_CALLS: list[str] = []
SQL_LOG: list[str] = []
RUN_IDS: list[UUID] = []
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
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return None, out, f"timeout after {timeout}s"


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def anchor() -> datetime:
    """The fixture timeline's anchor, captured ONCE at seed time (VERIFY_RULES rule 3): every fixture created_at and
    every injected 'now' derive from it."""
    if "anchor" not in S:
        n = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        S["anchor"] = n - timedelta(days=6)
    return S["anchor"]


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    """The SAME org context the routes set (rls_context_middleware → set_config LOCAL, not super admin)."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


@asynccontextmanager
async def org_ctx(conn, org):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        yield conn


@asynccontextmanager
async def as_platform():
    """What the job does: the app's RLS pool under the fixture platform org with Super Admin context (contextvars set
    BEFORE acquire — the pool applies them at acquire, rep13 fix 1)."""
    tokens = set_rls_context(str(ORG_P), True)
    try:
        pool = await get_pool()
        async with pool.acquire() as c:
            yield c
    finally:
        reset_rls_context(tokens)


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


def counted_plan() -> dict:
    orgs, users = FIXTURE_ORGS, list(USERS)
    by_org = ("org_id = ANY($1::uuid[])", [orgs])
    return {
        "public.organizations": ("id = ANY($1::uuid[])", [orgs]),
        "public.users": ("id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [users, f"%{TAG}%"]),
        "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
        "public.roles": by_org,
        "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
        "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
        "public.ai_decision_log": ("org_id = ANY($1::uuid[]) OR task_type LIKE $2", [orgs, f"{TASK_PREFIX}%"]),
        "public.ai_drift_alerts": ("org_id = ANY($1::uuid[]) OR task_type LIKE $2", [orgs, f"{TASK_PREFIX}%"]),
        "public.ai_drift_alert_events": by_org,
        "public.ai_drift_thresholds": ("task_type LIKE $1 OR changed_by = ANY($2::uuid[])", [f"{TASK_PREFIX}%", users]),
        "public.workflow_definitions": by_org, "public.workflow_versions": by_org, "public.workflow_steps": by_org,
        "public.workflow_triggers": by_org, "public.workflow_runs": by_org, "public.workflow_run_steps": by_org,
    }


async def counts(conn) -> dict:
    out = {}
    for t, (pred, args) in counted_plan().items():
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
    return out


async def real_fingerprints(conn) -> dict:
    """Real rows keyed by their real id; fixture rows excluded by fixture ids. ai_decision_log: only rows that existed
    BEFORE the run (real AI calls add rows at any time — never counted); none of its columns is updated by real
    activity. workflow_triggers: last_fired_at / occurrence_count excluded (the real scheduler updates them)."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    t0 = S["t_start"]
    return {
        "real decision-log rows that existed before the run": await fingerprint(
            conn, "SELECT id, org_id, task_type, model_requested, model_used, fallback_used, cost_usd, latency_ms, "
                  "success, effort_requested, effort_used, litellm_bypassed, created_at FROM public.ai_decision_log "
                  "WHERE created_at < $1 AND NOT (org_id = ANY($2::uuid[])) AND task_type NOT LIKE $3", t0, orgs,
            f"{TASK_PREFIX}%"),
        "real thresholds (every version)": await fingerprint(
            conn, "SELECT id, task_type, rule_key, threshold, min_recent, min_baseline, enabled, version, valid_to "
                  "FROM public.ai_drift_thresholds WHERE COALESCE(task_type, '') NOT LIKE $1", f"{TASK_PREFIX}%"),
        "real drift alerts": await fingerprint(
            conn, "SELECT id, org_id, task_type, rule_key, status, evaluations, closed_at FROM public.ai_drift_alerts "
                  "WHERE NOT (org_id = ANY($1::uuid[]))", orgs),
        "real drift alert events": await fingerprint(
            conn, "SELECT id, alert_id, event FROM public.ai_drift_alert_events WHERE NOT (org_id = ANY($1::uuid[]))",
            orgs),
        "real drift todos": await fingerprint(
            conn, "SELECT id, user_id, status, detail FROM public.member_todos WHERE source = 'ai_drift' AND NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real triggers (id, org, active, cron)": await fingerprint(
            conn, "SELECT id, org_id, is_active, schedule_cron FROM workflow_triggers WHERE NOT (org_id = "
                  "ANY($1::uuid[]))", orgs),
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


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        orgs, users = FIXTURE_ORGS, list(USERS)
        plan = {
            "public.member_todos": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users]),
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.ai_decision_log": ("org_id = ANY($1::uuid[]) OR task_type LIKE $2", [orgs, f"{TASK_PREFIX}%"]),
            "public.ai_drift_alert_events": ("org_id = ANY($1::uuid[]) OR alert_id IN (SELECT id FROM "
                                             "public.ai_drift_alerts WHERE task_type LIKE $2)", [orgs, f"{TASK_PREFIX}%"]),
            "public.ai_drift_alerts": ("org_id = ANY($1::uuid[]) OR task_type LIKE $2", [orgs, f"{TASK_PREFIX}%"]),
            "public.ai_drift_thresholds": ("task_type LIKE $1", [f"{TASK_PREFIX}%"]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan, gucs=(),
                               label="verify_s28")
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
    out = {}
    for t, (pred, args) in counted_plan().items():
        n = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
        if n:
            out[t] = n
    return out


# ═══ App access + stubs ═════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=600.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_s28"}}
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


def body_text(o) -> str:
    return json.dumps(o, default=str)


def shape(fn, skip_first: bool = False) -> list:
    """A callable's parameter names, kinds and defaults (annotations and the return type ignored) — what a caller's
    arguments bind to. ``skip_first`` compares a stub whose positional first parameter is renamed."""
    ps = list(inspect.signature(fn).parameters.values())
    if skip_first and ps:
        ps = ps[1:]
    return [(p.name, p.kind, p.default) for p in ps]


ORIGINALS: dict = {}


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def no_profile(request, claims):   # users.fetch_auth0_profile(request, claims) — its current signature
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):   # httpx.AsyncHTTPTransport.handle_async_request(self, request)
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_s28: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    # THE LLM BOUNDARY — each stub has the CURRENT signature of what it replaces (asserted in y_no_model).
    async def call_claude_json(system: str, user: str, max_tokens: int = 400, *, org_id=None, model: str | None = None,
                               model_key: str = extraction.DEFAULT_MODEL_KEY, task_type: str = "extraction"):
        LLM_CALLS.append("call_claude_json")
        raise AssertionError("verify_s28: call_claude_json was called")

    async def call_claude_text(system: str, messages: list[dict], max_tokens: int = 400, model: str | None = None, *,
                               org_id=None, model_key: str = extraction.DEFAULT_MODEL_KEY,
                               task_type: str = "text_generation"):
        LLM_CALLS.append("call_claude_text")
        raise AssertionError("verify_s28: call_claude_text was called")

    async def call_claude_with_tools(system: str, messages: list[dict], tools: list[dict], model: str | None = None,
                                     max_tokens: int = 2000, *, org_id=None,
                                     model_key: str = extraction.ASSISTANT_MODEL_KEY, task_type: str = "assistant"):
        LLM_CALLS.append("call_claude_with_tools")
        raise AssertionError("verify_s28: call_claude_with_tools was called")

    async def _execute_chain(*, task_type: str, org_id, model_key: str, model_override, make_call, extract):
        LLM_CALLS.append("_execute_chain")
        raise AssertionError("verify_s28: the S27 chain executor was called")

    for fn in (call_claude_json, call_claude_text, call_claude_with_tools, _execute_chain):
        ORIGINALS[fn.__name__] = getattr(extraction, fn.__name__)
        setattr(extraction, fn.__name__, fn)
    S["stubs"] = {"call_claude_json": call_claude_json, "call_claude_text": call_claude_text,
                  "call_claude_with_tools": call_claude_with_tools, "_execute_chain": _execute_chain}
    register_all()
    J.PLATFORM_JOB_ORG_IDS.add(ORG_P)
    AD.JOB_KWARGS.clear()

    orig_fetch = asyncpg.connection.Connection.fetch

    async def counting_fetch(self, query, *args, timeout=None, record_class=None):   # Connection.fetch's signature
        if S.get("count_sql"):
            SQL_LOG.append(query)
        return await orig_fetch(self, query, *args, timeout=timeout, record_class=record_class)

    asyncpg.connection.Connection.fetch = counting_fetch


# ═══ fixture rows ═══════════════════════════════════════════════════════════
def row(task, org, at, *, cost=D("0.0020"), lat=1000, ok=True, req="m-a", used=None, fb=False, byp=False,
        eff_req=None, eff_used=None):
    return {"org_id": org, "task_type": f"{TASK_PREFIX}{task}", "model_requested": req,
            "model_used": used if used is not None else req, "fallback_used": fb, "cost_usd": cost if ok else None,
            "latency_ms": lat, "success": ok, "effort_requested": eff_req, "effort_used": eff_used,
            "litellm_bypassed": byp, "created_at": at}


async def insert_rows(conn, rows: list[dict]) -> None:
    by_org: dict = {}
    for r in rows:
        by_org.setdefault(r["org_id"], []).append(r)
    for org, rs in by_org.items():
        async with org_ctx(conn, org):
            await conn.executemany(
                "INSERT INTO public.ai_decision_log (org_id, task_type, model_requested, model_used, fallback_used, "
                "cost_usd, latency_ms, success, effort_requested, effort_used, litellm_bypassed, created_at) VALUES "
                "($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                [(r["org_id"], r["task_type"], r["model_requested"], r["model_used"], r["fallback_used"], r["cost_usd"],
                  r["latency_ms"], r["success"], r["effort_requested"], r["effort_used"], r["litellm_bypassed"],
                  r["created_at"]) for r in rs])


async def override(conn, task, rule, *, threshold, min_recent=4, min_baseline=4, enabled=True, note="verify_s28"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await AD.set_threshold(conn, rule_key=rule, task_type=f"{TASK_PREFIX}{task}", threshold=threshold,
                                      min_recent=min_recent, min_baseline=min_baseline, enabled=enabled,
                                      actor_id=U_SUPER, actor_org_id=ORG_P, note=note)


def base_times(t: datetime, n: int) -> list[datetime]:
    """n instants inside the baseline window of an evaluation at ``t`` (2..2+n days before it)."""
    return [t - timedelta(days=2 + i, hours=3) for i in range(n)]


def recent_times(t: datetime, n: int) -> list[datetime]:
    """n instants inside the recent window of an evaluation at ``t`` (1..n hours before it)."""
    return [t - timedelta(hours=1 + i) for i in range(n)]


#: rule → {variant: (baseline rows fn, recent rows fn, override threshold)}; variants cross / inside / few
def rule_fixtures(t: datetime) -> dict:
    A = ORG_A
    out: dict = {}

    def mk(task, base_specs, rec_specs):
        rs = [row(task, A, at, **kw) for at, kw in zip(base_times(t, len(base_specs)), base_specs)]
        rs += [row(task, A, at, **kw) for at, kw in zip(recent_times(t, len(rec_specs)), rec_specs)]
        return rs

    c = lambda v: {"cost": D(v)}            # noqa: E731
    out["cost_per_call"] = {
        "cross": (mk("cost_cross", [c("0.0020")] * 4, [c("0.0031")] * 4), D(50)),   # 0.0031 > 0.0020 × 1.5 = 0.0030
        "inside": (mk("cost_inside", [c("0.0020")] * 4, [c("0.0030")] * 4), D(50)),  # = the limit: not above it
        "few": (mk("cost_few", [c("0.0020")] * 4, [c("0.0031")] * 3), D(50))}        # 3 recent < min 4
    la = lambda v: {"lat": v}               # noqa: E731
    out["latency_p95"] = {
        "cross": (mk("lat_cross", [la(1000)] * 4, [la(1501)] * 4), D(50)),
        "inside": (mk("lat_inside", [la(1000)] * 4, [la(1500)] * 4), D(50)),
        "few": (mk("lat_few", [la(1000)] * 4, [la(1501)] * 3), D(50))}
    n0 = {}
    for rule, key, flag in (("fallback_rate", "fb", {"fb": True}), ("failure_rate", "fail", {"ok": False}),
                            ("bypass_rate", "byp", {"byp": True})):
        out[rule] = {
            "cross": (mk(f"{key}_cross", [n0] * 4, [flag, flag, n0, n0]), D(25)),     # 50 points − 0 > 25
            "inside": (mk(f"{key}_inside", [n0] * 4, [flag, n0, n0, n0]), D(25)),     # 25 points: not above 25
            "few": (mk(f"{key}_few", [n0] * 4, [flag, flag, n0]), D(25))}             # 3 recent < min 4
    hh = {"eff_req": "high", "eff_used": "high"}
    dn = {"eff_req": "high", "eff_used": "low"}
    out["effort_downgrade_rate"] = {
        "cross": (mk("eff_cross", [hh] * 4, [dn, dn, hh, hh]), D(25)),
        "inside": (mk("eff_inside", [hh] * 4, [dn, hh, hh, hh]), D(25)),
        "few": (mk("eff_few", [hh] * 4, [dn, dn, hh]), D(25))}
    new = {"req": "m-new"}
    out["model_change"] = {
        "cross": (mk("mc_cross", [n0] * 4, [new, n0, n0, n0]), None),                   # m-new never in the baseline
        "inside": (mk("mc_inside", [n0] * 4, [n0] * 4), None),
        "few": (mk("mc_few", [n0] * 3, [new, n0, n0, n0]), None)}                       # baseline 3 < min 4
    mis = {"req": "m-a", "used": "m-x"}
    out["model_mismatch"] = {
        "cross": (mk("mm_cross", [n0] * 4, [mis, n0, n0, n0]), None),
        # the same swap WITH fallback_used, and a date-pinned resolution of the alias: neither is a mismatch
        "inside": (mk("mm_inside", [n0] * 4, [{**mis, "fb": True}, {"req": "m-a", "used": "m-a-2026-01-01"}, n0, n0]),
                   None),
        "few": (mk("mm_few", [n0] * 4, []), None)}                                       # 0 recent < min 1
    # silence: baseline 4 calls over 28 days → expected 4 × 24 / 672 = 1/7 ≈ 0.142857 per 24 h window, 0 recent
    out["silence"] = {
        "cross": (mk("sil_cross", [n0] * 4, []), D("0.14")),    # expected ≥ 0.14 → silence
        "inside": (mk("sil_inside", [n0] * 4, []), D("0.15")),  # expected < 0.15 → ok
        "few": (mk("sil_few", [n0] * 3, []), D("0.14"))}        # baseline 3 < min 4
    return out


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1d (docs/S28_DISCOVERY.md) and the live state they rest on")
    self_static(check, find)
    n = await platform(conn, "SELECT count(*) FROM public.ai_decision_log", fetch="fetchval")
    tasks = await platform(conn, "SELECT count(DISTINCT task_type) FROM public.ai_decision_log WHERE task_type NOT LIKE $1",
                           f"{TASK_PREFIX}%", fetch="fetchval")
    check(int(n) > 0 and int(tasks) > 0, "1b: the live decision log reads (non-empty)", f"{n} rows, {tasks} task types")
    if int(n) < 662:
        find("1b: fewer decision-log rows than at discovery", f"{n} now (662 on 2026-11-07)")
    writers = [p for p in ("services/extraction.py", "services/document_embedding.py", "services/edgar_inventory.py")
               if "ai_decision_log" in _read(f"apps/api/{p}")]
    check(len(writers) == 3, "1a: the three writer modules named in discovery still write the log", f"{writers}")
    check(tuple(AD.EFFORT_ORDER) == tuple(sorted(extraction.EFFORT_LEVELS, key=extraction.EFFORT_LEVELS.get)),
          "the monitor's effort order equals services.extraction.EFFORT_LEVELS by budget",
          f"{AD.EFFORT_ORDER} vs {extraction.EFFORT_LEVELS}")


async def y_schema(conn):
    section("[Y] Schema: constraints read from the catalog BEFORE seeding; RLS on every operation; the role")
    names = ["ai_drift_thresholds_scope_chk", "ai_drift_thresholds_rule_chk", "ai_drift_thresholds_value_chk",
             "ai_drift_thresholds_sample_chk", "ai_drift_alerts_status_chk", "ai_drift_alerts_closed_chk",
             "ai_drift_alerts_rule_chk", "ai_drift_alerts_org_id_fkey", "ai_drift_alerts_threshold_id_fkey",
             "ai_drift_alert_events_event_chk", "ai_drift_alert_events_alert_id_fkey", "ai_decision_log_org_id_fkey"]
    defs = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = ANY($1::text[])", names)}
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])",
        ["ai_drift_thresholds_live_uq", "ai_drift_thresholds_version_uq", "ai_drift_alerts_open_uq"])}
    S["catalog"] = bool(defs) and len(defs) == len(names) and len(idx) == 3
    check(S["catalog"], "constraints read from the catalog (CHECK / UNIQUE / FK) — none missing",
          f"missing checks {sorted(set(names) - set(defs))} uniques {sorted(idx)}")
    u = idx.get("ai_drift_alerts_open_uq", "")
    check("UNIQUE" in u and "status = 'open'" in u and "org_id, task_type, rule_key" in u,
          "one OPEN alert per (org, task, rule) — a partial unique index (the de-duplication the database enforces)", u)
    lu = idx.get("ai_drift_thresholds_live_uq", "")
    check("UNIQUE" in lu and "valid_to IS NULL" in lu, "one LIVE threshold per (rule, task) — versions accumulate", lu)
    for t in ("ai_drift_thresholds", "ai_drift_alerts", "ai_drift_alert_events"):
        pol = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                      "tablename = $1", t)}
        check(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t} has a policy for EVERY operation", f"{sorted(pol)}")
    role = await platform(conn, "SELECT r.rolname, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user",
                          fetch="fetchrow")
    S["role_ok"] = bool(role and role["rolbypassrls"] is False)
    check(S["role_ok"], "this script's connection role does NOT bypass RLS (rolbypassrls = false)",
          f"{dict(role) if role else None}")
    live = await platform(conn, "SELECT rule_key FROM public.ai_drift_thresholds WHERE task_type IS NULL AND valid_to "
                                "IS NULL")
    check(sorted(r["rule_key"] for r in live) == sorted(AD.RULES),
          "every rule has a live platform default (the monitor never invents a threshold)", f"{[r['rule_key'] for r in live]}")


async def guard_prefix(conn) -> bool:
    n_l = await platform(conn, "SELECT count(*) FROM public.ai_decision_log WHERE task_type LIKE $1", f"{TASK_PREFIX}%",
                         fetch="fetchval")
    n_t = await platform(conn, "SELECT count(*) FROM public.ai_drift_thresholds WHERE task_type LIKE $1",
                         f"{TASK_PREFIX}%", fetch="fetchval")
    n_a = await platform(conn, "SELECT count(*) FROM public.ai_drift_alerts WHERE task_type LIKE $1", f"{TASK_PREFIX}%",
                         fetch="fetchval")
    n_o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                         fetch="fetchval")
    n_u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                         list(USERS), f"%{TAG}%", fetch="fetchval")
    return check(int(n_l) == 0 and int(n_t) == 0 and int(n_a) == 0 and int(n_o) == 0 and int(n_u) == 0,
                 "reserved fixture prefixes (task types zzd28_…, fixture org / user ids) match no real row",
                 f"log {n_l}, thresholds {n_t}, alerts {n_a}, orgs {n_o}, users {n_u}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_P, "platform"), (ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"S28 Verify {nm}", f"{TAG}-{nm}")
        perm = await conn.fetchval("SELECT id FROM permissions WHERE name = 'view_dashboard'")
        rid = await rbac.ensure_role(conn, ORG_A, "zzd28_member", "S28 verify — a client role (no org settings)")
        await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid, perm)
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname == "zzd28_member":
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, rid)
            else:
                await rbac.grant_named_role(conn, uid, org, rname)
    flags = await platform(conn, "SELECT (SELECT bool_and(is_test) FROM organizations WHERE id = ANY($1::uuid[])) AS o, "
                                 "(SELECT bool_and(is_test) FROM users WHERE id = ANY($2::uuid[])) AS u, "
                                 "(SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])) AS n",
                           FIXTURE_ORGS, list(USERS), fetch="fetchrow")
    S["seeded"] = check(bool(flags["o"] is True and flags["u"] is True and int(flags["n"]) == 3),
                        "fixture orgs and users are is_test = true (asserted)", f"{dict(flags)}")
    S["seeded"] = bool(await check_fixture_roles(conn)) and S["seeded"]


async def check_fixture_roles(conn) -> bool:
    from services import authz as _AZ
    bad = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid, (org, account_role, _nm, rname, _full) in USERS.items():
            names = [r["name"] for r in await conn.fetch(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = $1 AND "
                "r.org_id = $2", uid, org)]
            pr = await _AZ.load_principal(conn, uid)
            ok = rname in names and bool(pr and pr.is_super_admin) is (rname == "super_admin")
            if uid in (U_ADMIN_A, U_ADMIN_B):
                ok = ok and bool(pr) and "manage_org_settings" in pr.permissions
            if uid == U_MEMBER_A:
                ok = ok and bool(pr) and "manage_org_settings" not in pr.permissions and not pr.is_staff
            if not ok:
                bad.append((str(uid), names, bool(pr and pr.is_super_admin)))
    return check(not bad, "every fixture role is a user_roles grant the app decides from (Super Admin; org admins hold "
                 "manage_org_settings; the member does NOT)", f"{bad}")


# ── [Y] metrics exact ───────────────────────────────────────────────────────
async def y_metrics(conn):
    section("[Y] Metrics: a fixture log with hand-computed medians / p95s / rates equals ai_drift's numbers exactly")
    t = anchor() - timedelta(days=1)
    S["t_metrics"] = t
    R = lambda h, **kw: row("metrics", ORG_A, t - timedelta(hours=h), **kw)   # noqa: E731
    recent = [
        R(1, cost=D("0.0010"), lat=100, eff_req="low", eff_used="low"),
        R(2, cost=D("0.0020"), lat=200, req="m-a", used="m-b", fb=True, eff_req="high", eff_used="high"),
        R(3, cost=D("0.0030"), lat=300, byp=True, eff_req="medium", eff_used=None),
        R(4, ok=False, lat=1),
        R(5, cost=D("0.0100"), lat=1000, req="m-a", used="m-a-2025-01-01", eff_req="high", eff_used="medium"),
    ]
    base = [row("metrics", ORG_A, at, cost=D("0.0010"), lat=lat) for at, lat in zip(base_times(t, 4),
                                                                                   (100, 100, 100, 200))]
    # window edges: exactly at the recent start → recent; exactly at now → excluded; 1 µs before the start → baseline
    w = AD.windows(t)
    edges = [row("edges", ORG_A, w.recent_start), row("edges", ORG_A, w.now),
             row("edges", ORG_A, w.recent_start - timedelta(microseconds=1))]
    await insert_rows(conn, recent + base + edges)
    async with org_ctx(conn, ORG_A):
        rec_rows = await AD.read_window(conn, start=w.recent_start, end=w.now, org_ids=[ORG_A],
                                        task_types=[f"{TASK_PREFIX}metrics"], include_test_orgs=True)
        base_rows = await AD.read_window(conn, start=w.baseline_start, end=w.recent_start, org_ids=[ORG_A],
                                         task_types=[f"{TASK_PREFIX}metrics"], include_test_orgs=True)
        e_rec = await AD.read_window(conn, start=w.recent_start, end=w.now, org_ids=[ORG_A],
                                     task_types=[f"{TASK_PREFIX}edges"], include_test_orgs=True)
        e_base = await AD.read_window(conn, start=w.baseline_start, end=w.recent_start, org_ids=[ORG_A],
                                      task_types=[f"{TASK_PREFIX}edges"], include_test_orgs=True)
    check(len(rec_rows) == 5 and len(base_rows) == 4, "one SQL read per window returns exactly the window's rows",
          f"{len(rec_rows)} / {len(base_rows)}")
    check([r["created_at"] for r in e_rec] == [w.recent_start] and len(e_base) == 1,
          "window edges: [start, now) — a row AT the recent start is recent, one AT now is excluded, one 1 µs earlier "
          "is baseline", f"{[r['created_at'] for r in e_rec]} {len(e_base)}")
    s = AD.window_stats(rec_rows)
    b = AD.window_stats(base_rows)
    # BY HAND — recent: costs (non-NULL) 0.0010 0.0020 0.0030 0.0100 → median (0.0020 + 0.0030) / 2 = 0.0025,
    # p95 k = ⌈0.95·4⌉ = 4 → 0.0100, total 0.0160; latency over SUCCESSFUL calls 100 200 300 1000 → median 250,
    # p95 1000 (the failed call's 1 ms is not latency); fallback 1/5 = 20, failure 1/5 = 20, bypass 1/5 = 20;
    # effort over the 4 calls that requested one: medium→none and high→medium are downgrades → 2/4 = 50.
    want = {"calls": 5, "cost": {"n": 4, "median": D("0.0025"), "p95": D("0.0100"), "total": D("0.0160")},
            "latency": {"n": 4, "median": D(250), "p95": D(1000)},
            "fallback": {"n": 5, "events": 1, "rate": D(20)}, "failure": {"n": 5, "events": 1, "rate": D(20)},
            "bypass": {"n": 5, "events": 1, "rate": D(20)}, "effort": {"n": 4, "events": 2, "rate": D(50)}}
    got = {k: s[k] for k in want}
    check(got == want, "recent window: calls, cost median / p95 / total, latency median / p95, fallback / failure / "
          "bypass / effort-downgrade rates equal the hand computation exactly (Decimal)", f"{got}")
    check(all(isinstance(s["cost"][k], Decimal) for k in ("median", "p95", "total")), "cost is Decimal, never float",
          f"{[type(s['cost'][k]).__name__ for k in ('median', 'p95', 'total')]}")
    # m-a: the rows at −1 h, −3 h and −4 h (the failure) → first seen −4 h, 3 calls; m-b (the fallback) −2 h;
    # the date-pinned m-a-2025-01-01 −5 h
    by_hand_models = {"m-a": (t - timedelta(hours=4), 3), "m-b": (t - timedelta(hours=2), 1),
                      "m-a-2025-01-01": (t - timedelta(hours=5), 1)}
    got_m = {m: (v["first_seen"], v["calls"]) for m, v in s["models"].items()}
    check(got_m == by_hand_models, "the set of model_used values, each with its first-seen time and calls", f"{got_m}")
    check(s["mismatch"]["events"] == 0, "the fallback swap and the date-pinned alias are not mismatches",
          f"{s['mismatch']}")
    # BY HAND — baseline: 4 × 0.0010 → median 0.0010, p95 0.0010; latency 100 100 100 200 → median 100, p95 200
    check(b["cost"]["median"] == D("0.0010") and b["cost"]["p95"] == D("0.0010") and b["latency"]["median"] == D(100)
          and b["latency"]["p95"] == D(200) and b["calls"] == 4, "baseline window equals the hand computation",
          f"{b['cost']} {b['latency']}")
    check(AD.p95(range(1, 21)) == D(19) and AD.p95(range(1, 20)) == D(19) and AD.median([1, 2, 3, 4]) == D("2.5"),
          "nearest-rank p95 (n = 20 → the 19th value; n = 19 → the 19th, i.e. the max) and the even-count median", "")


# ── [Y] rules across / inside / minimum sample ──────────────────────────────
async def y_rules(conn):
    section("[Y] Rules: each fires across its threshold, NOT just inside it; below the minimum sample 'not enough data'")
    t = anchor() - timedelta(days=2)
    S["t_rules"] = t
    fx = rule_fixtures(t)
    rows = [r for rule in fx.values() for (rs, _th) in rule.values() for r in rs]
    await insert_rows(conn, rows)
    for rule, variants in fx.items():
        for variant, (rs, th) in variants.items():
            task = rs[0]["task_type"].removeprefix(TASK_PREFIX) if rs else None
            if task is None:
                continue
            await override(conn, task, rule, threshold=th,
                           min_recent=1 if rule in ("model_change", "model_mismatch", "silence") else 4,
                           min_baseline=0 if rule == "model_mismatch" else 4)
    # model_mismatch "few" has no recent row and silence variants none: name their tasks explicitly
    tasks = sorted({r["task_type"] for r in rows})
    async with as_platform() as c:
        w, results = await AD.evaluate(c, now=t, org_ids=[ORG_A], task_types=tasks, include_test_orgs=True)
    S["rule_results"] = results
    by = {(p.task_type, f.rule_key): f for p in results for f in p.findings}
    want = {"cross": "drift", "inside": "ok", "few": "insufficient"}
    for rule, variants in fx.items():
        for variant, (rs, th) in variants.items():
            task = rs[0]["task_type"]
            f = by.get((task, rule))
            check(bool(f and f.status == want[variant]),
                  f"{rule} / {variant}: {want[variant]}" + (" (threshold from the task's override)" if f and
                                                            f.threshold and f.threshold.task_type == task else ""),
                  f"{f.status if f else None}: {f.sentence if f else 'no finding'}")
    # the override is what applied
    ov = [by.get((fx[r]["cross"][0][0]["task_type"], r)) for r in fx]
    check(all(f and f.threshold and f.threshold.task_type and f.threshold.task_type.startswith(TASK_PREFIX) for f in ov),
          "every cross finding used ITS task's override, not the platform default", "")
    # a disabled rule says so and never alarms
    th = AD.Threshold("cost_per_call", D(50), 1, 1, False)
    f = AD.evaluate_rules(AD.window_stats(fx["cost_per_call"]["cross"][0][:4]),
                          AD.window_stats(fx["cost_per_call"]["cross"][0][4:]),
                          {**{k: AD.Threshold(k, AD.DEFAULT_THRESHOLDS[k]["threshold"], 1, 1, True) for k in AD.RULES},
                           "cost_per_call": th})[0]
    check(f.status == "disabled", "a switched-off rule reports 'Off' and never alarms", f"{f.status}")


# ── [Y] model change / mismatch alerts ──────────────────────────────────────
async def y_model_alerts(conn):
    section("[Y] Model change / mismatch: a new model_used in the recent window alerts; the same model does not; a "
            "mismatch without fallback alerts")
    results = S.get("rule_results") or []
    targets = {f"{TASK_PREFIX}mc_cross", f"{TASK_PREFIX}mc_inside", f"{TASK_PREFIX}mm_cross", f"{TASK_PREFIX}mm_inside"}
    mine = [p for p in results if p.task_type in targets]
    check(len(mine) == 4, "the four model fixtures were evaluated", f"{[p.task_type for p in mine]}")
    async with as_platform() as c:
        await AD.apply_findings(c, mine, AD.windows(S["t_rules"]), platform_org_id=ORG_P, owner_ids=[U_SUPER],
                                org_names={})
    rows = await platform(conn, "SELECT task_type, rule_key, status, numbers FROM public.ai_drift_alerts WHERE "
                                "task_type = ANY($1::text[]) AND rule_key IN ('model_change', 'model_mismatch')",
                          sorted(targets))
    got = {(r["task_type"].removeprefix(TASK_PREFIX), r["rule_key"]) for r in rows if r["status"] == "open"}
    check(("mc_cross", "model_change") in got and ("mc_inside", "model_change") not in got,
          "model change: m-new (never in the baseline) opened an alert; the same model did not", f"{sorted(got)}")
    check(("mm_cross", "model_mismatch") in got and ("mm_inside", "model_mismatch") not in got,
          "model mismatch: requested m-a / ran m-x without fallback opened an alert; with fallback_used (and a "
          "date-pinned alias) it did not", f"{sorted(got)}")
    mc = next((r for r in rows if r["task_type"].endswith("mc_cross") and r["rule_key"] == "model_change"), None)
    nums = (json.loads(mc["numbers"]) if isinstance(mc["numbers"], str) else mc["numbers"]) if mc else {}
    check(bool(nums.get("new_models") and nums["new_models"][0].get("model") == "m-new"
               and nums["new_models"][0].get("first_seen")),
          "the model-change alert names the new model with its first-seen time", f"{body_text(nums)[:300]}")


# ── [Y] alert lifecycle ─────────────────────────────────────────────────────
async def y_alerts(conn):
    section("[Y] Alerts: ONE per (task, rule, org) — de-duplicated while open, refreshed, auto-closed after a full "
            "recent window back inside (closure recorded); recipients; org admin sees only their org; member refused")
    T = anchor()
    S["t_alert"] = T
    task = "alert"
    tt = f"{TASK_PREFIX}{task}"
    await override(conn, task, "cost_per_call", threshold=D(50))
    cheap = [row(task, org, at, cost=D("0.0010")) for org in (ORG_A, ORG_B)
             for at in [T - timedelta(days=d) for d in (20, 15, 10, 6, 3, 2)]]
    drift = [row(task, org, T - timedelta(hours=h), cost=D("0.0050")) for org in (ORG_A, ORG_B) for h in (6, 4, 2, 1)]
    await insert_rows(conn, cheap + drift + [row("onlyb", ORG_B, T - timedelta(hours=2))])

    async def run_at(now, orgs=(ORG_A,)):
        async with as_platform() as c:
            w, res = await AD.evaluate(c, now=now, org_ids=list(orgs), task_types=[tt], include_test_orgs=True)
            names = await AD.org_names(c, {p.org_id for p in res})
            out = await AD.apply_findings(c, res, w, platform_org_id=ORG_P, owner_ids=[U_SUPER], org_names=names)
        return out

    q = ("SELECT id, status, evaluations, ok_since, closed_at, close_reason, numbers, link, detail FROM "
         "public.ai_drift_alerts WHERE org_id = $1 AND task_type = $2 AND rule_key = 'cost_per_call' ORDER BY opened_at")
    a1 = await run_at(T, (ORG_A, ORG_B))
    r1 = await platform(conn, q, ORG_A, tt)
    check(a1["opened"] >= 2 and len(r1) == 1 and r1[0]["status"] == "open",
          "a drifting cost per call opens ONE alert per org (org A and org B)", f"{a1} {[dict(r) for r in r1]}")
    alert_id = r1[0]["id"] if r1 else None
    nums = (json.loads(r1[0]["numbers"]) if isinstance(r1[0]["numbers"], str) else r1[0]["numbers"]) if r1 else {}
    # s28 fix 1: numbers are JSON text of numeric(…,6) medians ("0.005000") — compare as Decimals, never as text
    def _dec(v):
        try:
            return D(str(v)) if v is not None else None
        except Exception:
            return None
    check(bool(r1 and _dec(nums.get("recent")) == D("0.005") and _dec(nums.get("baseline")) == D("0.001")
               and nums.get("recent_n") == 4
               and nums.get("baseline_n") == 6 and nums.get("rule") == "cost_per_call"
               and r1[0]["link"] == f"/admin/ai-drift?task={tt}"),
          "the alert carries the numbers (baseline vs recent, sample sizes), the rule and the task page link",
          f"numbers={body_text(nums)} link={r1[0]['link'] if r1 else None}")
    todos = await platform(conn, "SELECT user_id, org_id, status, detail, action_key FROM member_todos WHERE source = "
                                 "'ai_drift' AND related_type = 'ai_drift_alert' AND related_id = $1", alert_id)
    check({t["user_id"] for t in todos} == {U_ADMIN_A, U_SUPER} and all(t["status"] == "open" for t in todos)
          and all((t["action_key"] or "").startswith("/admin/ai-drift") for t in todos),
          "people are told through member_todos (the held-run path): org A's admin and the job's Super Admin — not the "
          "member, not org B", f"{[(str(t['user_id']), str(t['org_id']), t['status']) for t in todos]}")
    await insert_rows(conn, [row(task, ORG_A, T + timedelta(minutes=30), cost=D("0.0060"))])
    a2 = await run_at(T + timedelta(hours=1))
    r2 = await platform(conn, q, ORG_A, tt)
    nums2 = (json.loads(r2[0]["numbers"]) if isinstance(r2[0]["numbers"], str) else r2[0]["numbers"]) if r2 else {}
    todos2 = await platform(conn, "SELECT id FROM member_todos WHERE source = 'ai_drift' AND related_id = $1", alert_id)
    check(bool(len(r2) == 1 and r2[0]["id"] == alert_id and r2[0]["evaluations"] == 2 and nums2.get("recent_n") == 5
               and a2["updated"] >= 1 and a2["opened"] == 0 and len(todos2) == len(todos)),
          "still drifting: the SAME alert is refreshed (evaluations 2, recent n 5) — no second alert, no second todo",
          f"{a2} {[dict(r) for r in r2]}")
    # recovery: cheap calls only in the next windows
    await insert_rows(conn, [row(task, ORG_A, T + timedelta(hours=h), cost=D("0.0010")) for h in (26, 27, 28, 30)])
    a3 = await run_at(T + timedelta(hours=31))
    r3 = await platform(conn, q, ORG_A, tt)
    check(bool(len(r3) == 1 and r3[0]["status"] == "open" and r3[0]["ok_since"] == T + timedelta(hours=31)
               and a3["recovering"] >= 1),
          "back inside: recovering (ok_since recorded), still OPEN until a full recent window passes", f"{a3} "
          f"{[dict(r) for r in r3]}")
    await run_at(T + timedelta(hours=43))
    r4 = await platform(conn, q, ORG_A, tt)
    check(bool(len(r4) == 1 and r4[0]["status"] == "open"), "12 h inside is not a full 24 h window: still open",
          f"{[dict(r) for r in r4]}")
    await insert_rows(conn, [row(task, ORG_A, T + timedelta(hours=h), cost=D("0.0010")) for h in (50, 51, 52, 54)])
    a5 = await run_at(T + timedelta(hours=55))
    r5 = await platform(conn, q, ORG_A, tt)
    check(bool(len(r5) == 1 and r5[0]["status"] == "closed" and r5[0]["closed_at"] == T + timedelta(hours=55)
               and "full 24 h" in (r5[0]["close_reason"] or "") and a5["closed"] >= 1),
          "a full recent window inside: CLOSED automatically — the row is kept with closed_at and the reason",
          f"{a5} {[dict(r) for r in r5]}")
    ev = [e["event"] for e in await platform(conn, "SELECT event FROM public.ai_drift_alert_events WHERE alert_id = $1 "
                                                   "ORDER BY at, created_at", alert_id)]
    check(ev == ["opened", "updated", "recovering", "closed"], "the alert's history: opened → updated → recovering → "
          "closed (append-only events)", f"{ev}")
    t5 = await platform(conn, "SELECT status, detail FROM member_todos WHERE source = 'ai_drift' AND related_id = $1",
                        alert_id)
    check(bool(t5) and all(t["status"] == "done" and "[Closed:" in (t["detail"] or "") for t in t5),
          "its todos are closed with the reason (kept, not deleted)", f"{[dict(t) for t in t5]}")
    S["alert_b"] = await platform(conn, "SELECT id FROM public.ai_drift_alerts WHERE org_id = $1 AND task_type = $2",
                                  ORG_B, tt, fetch="fetchval")
    # the page's API: org scoping and the member
    c_a, o_a = await api(U_ADMIN_A, "GET", f"/api/v1/admin/ai/drift/tasks/{tt}")
    c_b, o_b = await api(U_ADMIN_B, "GET", f"/api/v1/admin/ai/drift/tasks/{tt}")
    c_s, o_s = await api(U_SUPER, "GET", f"/api/v1/admin/ai/drift/tasks/{tt}")
    orgs = lambda o: {a.get("org_id") for a in (o.get("alerts") or [])}   # noqa: E731
    check(bool(c_a == 200 and orgs(o_a) == {str(ORG_A)} and c_b == 200 and orgs(o_b) == {str(ORG_B)}),
          "an org admin sees ONLY their own org's AI alerts (both directions)", f"{c_a} {orgs(o_a)} {c_b} {orgs(o_b)} "
                                                                             f"{body_text(o_a)[:200] if c_a != 200 else ''}")
    check(bool(c_s == 200 and {str(ORG_A), str(ORG_B)} <= orgs(o_s)), "the Super Admin sees every org's alerts",
          f"{c_s} {orgs(o_s)}")
    check(bool(c_a == 200 and o_a.get("permissions", {}).get("can_write") is False
               and o_a.get("vocabularies", {}).get("editable") == []
               and o_s.get("permissions", {}).get("can_write") is True),
          "the envelope: an org admin's can_write false and editable [] (server-side, no client default); Super Admin "
          "can_write", f"{o_a.get('permissions')} {o_a.get('vocabularies', {}).get('editable')}")
    c_o, o_ov = await api(U_ADMIN_A, "GET", "/api/v1/admin/ai/drift")
    tasks_a = {t.get("task_type") for t in (o_ov.get("tasks") or [])}
    c_ob, o_ovb = await api(U_ADMIN_B, "GET", "/api/v1/admin/ai/drift")
    tasks_b = {t.get("task_type") for t in (o_ovb.get("tasks") or [])}
    check(bool(c_o == 200 and tt in tasks_a and f"{TASK_PREFIX}onlyb" not in tasks_a and c_ob == 200
               and f"{TASK_PREFIX}onlyb" in tasks_b and f"{TASK_PREFIX}metrics" not in tasks_b),
          "the task table is scoped to the org admin's org (org B's task absent for A; A's absent for B)",
          f"{c_o} {sorted(x for x in tasks_a if x and x.startswith(TASK_PREFIX))[:6]} {c_ob} "
          f"{sorted(x for x in tasks_b if x and x.startswith(TASK_PREFIX))}")
    for path in ("/api/v1/admin/ai/drift", f"/api/v1/admin/ai/drift/tasks/{tt}", "/api/v1/admin/ai/drift/thresholds"):
        c_m, o_m = await api(U_MEMBER_A, "GET", path)
        check(c_m == 403 and "manage_org_settings" in body_text(o_m), f"a member is refused {path} (403 naming "
              "manage_org_settings)", f"{c_m} {body_text(o_m)[:200]}")


# ── [Y] thresholds ──────────────────────────────────────────────────────────
async def y_thresholds(conn):
    section("[Y] Thresholds: Super Admin edit = a new version, history kept; org admin's VALID write refused (403), "
            "nothing written; a per-task override wins")
    tt = f"{TASK_PREFIX}api"
    body = {"rule_key": "cost_per_call", "task_type": tt, "threshold": "40", "min_recent": 6, "min_baseline": 25,
            "enabled": True, "note": "verify_s28 v1"}
    c1, o1 = await api(U_SUPER, "PUT", "/api/v1/admin/ai/drift/thresholds", body)
    c2, o2 = await api(U_SUPER, "PUT", "/api/v1/admin/ai/drift/thresholds", {**body, "threshold": "35",
                                                                              "note": "verify_s28 v2"})
    check(c1 == 200 and c2 == 200 and (o1.get("threshold") or {}).get("version") == 1
          and (o2.get("threshold") or {}).get("version") == 2 and (o2.get("threshold") or {}).get("previous_version") == 1,
          "Super Admin: two edits = version 1 then version 2", f"{c1} {body_text(o1)[:200]} {c2} {body_text(o2)[:200]}")
    hist = await platform(conn, "SELECT version, threshold, valid_to, changed_by, change_note FROM "
                                "public.ai_drift_thresholds WHERE task_type = $1 AND rule_key = 'cost_per_call' ORDER BY "
                                "version", tt)
    check(bool(len(hist) == 2 and hist[0]["valid_to"] is not None and hist[1]["valid_to"] is None
               and hist[0]["threshold"] == D(40) and hist[1]["threshold"] == D(35)
               and all(h["changed_by"] == U_SUPER for h in hist)),
          "history kept: v1 closed (valid_to set), v2 live; who and why on each", f"{[dict(h) for h in hist]}")
    aud = await platform(conn, "SELECT count(*) FROM audit_log WHERE user_id = $1 AND action = "
                               "'ai_drift.threshold_changed' AND payload->>'task_type' = $2", U_SUPER, tt, fetch="fetchval")
    check(int(aud) == 2, "each edit is audited (audit_log ai_drift.threshold_changed)", f"{aud}")
    c_h, o_h = await api(U_ADMIN_A, "GET", "/api/v1/admin/ai/drift/thresholds")
    vers = [h for h in (o_h.get("history") or []) if h.get("task_type") == tt]
    check(bool(c_h == 200 and len(vers) == 2 and o_h.get("permissions", {}).get("can_write") is False),
          "an org admin READS the thresholds and their history (read-only envelope)", f"{c_h} {len(vers)}")
    n0 = await counts_for_threshold(conn, tt)
    c3, o3 = await api(U_ADMIN_A, "PUT", "/api/v1/admin/ai/drift/thresholds", {**body, "threshold": "10",
                                                                               "note": "an org admin tries"})
    n1 = await counts_for_threshold(conn, tt)
    check(c3 == 403 and n0 == n1, "an org admin's VALID edit is refused (403) and NOTHING is written (rows + audit)",
          f"{c3} {body_text(o3)[:200]} {n0} → {n1}")
    c4, o4 = await api(U_MEMBER_A, "PUT", "/api/v1/admin/ai/drift/thresholds", {**body, "note": "a member tries"})
    check(c4 == 403 and await counts_for_threshold(conn, tt) == n0, "a member's valid edit is refused (403) too",
          f"{c4}")
    c5, o5 = await api(U_SUPER, "PUT", "/api/v1/admin/ai/drift/thresholds", {**body, "threshold": 40.0})
    check(c5 == 422 and await counts_for_threshold(conn, tt) == n0, "a float threshold is refused (422) — Decimal only",
          f"{c5} {body_text(o5)[:200]}")
    c6, o6 = await api(U_SUPER, "PUT", "/api/v1/admin/ai/drift/thresholds",
                       {**body, "rule_key": "model_change", "threshold": "5"})
    check(c6 == 422, "a threshold on a rule that has none is refused (422)", f"{c6} {body_text(o6)[:200]}")
    async with as_platform() as c:
        live = await AD.live_thresholds(c)
    eff = AD.resolve_thresholds(live, tt)["cost_per_call"]
    other = AD.resolve_thresholds(live, f"{TASK_PREFIX}no_override")["cost_per_call"]
    default = next((t for t in live if t.task_type is None and t.rule_key == "cost_per_call"), None)
    check(bool(eff.threshold == D(35) and eff.task_type == tt and eff.min_recent == 6 and default is not None
               and other.id == default.id),
          "a per-task override wins for its task; another task gets the platform default row",
          f"{eff.as_dict()} {other.as_dict()}")


async def counts_for_threshold(conn, tt) -> tuple:
    a = await platform(conn, "SELECT count(*) FROM public.ai_drift_thresholds WHERE task_type = $1", tt, fetch="fetchval")
    b = await platform(conn, "SELECT count(*) FROM audit_log WHERE action = 'ai_drift.threshold_changed' AND "
                             "payload->>'task_type' = $1", tt, fetch="fetchval")
    return int(a), int(b)


# ── [Y] the page's queries ──────────────────────────────────────────────────
async def y_queries(conn):
    section("[Y] GET routes: one query per section, not per row")
    SQL_LOG.clear()
    S["count_sql"] = True
    try:
        c, o = await api(U_ADMIN_A, "GET", "/api/v1/admin/ai/drift")
    finally:
        S["count_sql"] = False
    n_tasks = len(o.get("tasks") or [])
    log_reads = sum(1 for q in SQL_LOG if "public.ai_decision_log" in q)
    th_reads = sum(1 for q in SQL_LOG if "public.ai_drift_thresholds" in q)
    al_reads = sum(1 for q in SQL_LOG if "public.ai_drift_alerts" in q)
    check(bool(c == 200 and n_tasks >= 20 and log_reads == 2 and th_reads == 1 and al_reads == 1),
          f"the task table ({n_tasks} task rows): 2 decision-log reads (baseline, 7 days), 1 thresholds, 1 alerts",
          f"{c} log {log_reads} thresholds {th_reads} alerts {al_reads}")
    SQL_LOG.clear()
    S["count_sql"] = True
    try:
        c2, o2 = await api(U_SUPER, "GET", f"/api/v1/admin/ai/drift/tasks/{TASK_PREFIX}alert")
    finally:
        S["count_sql"] = False
    log2 = sum(1 for q in SQL_LOG if "public.ai_decision_log" in q)
    check(bool(c2 == 200 and len(o2.get("series") or []) == AD.SERIES_DAYS and log2 == 3),
          "a task's detail: 90 daily points from ONE read (+ the two evaluation reads)", f"{c2} series "
          f"{len(o2.get('series') or [])} log reads {log2}")


# ── [Y] the job ─────────────────────────────────────────────────────────────
async def y_job(conn):
    section("[Y] Job: 'AI — drift monitor' fires through the scheduler (injected instant), holds + alerts on a forced "
            "failure; OFF for real; the dry-run script prints findings and opens nothing")
    pool = await get_pool()
    T = anchor() - timedelta(days=3)
    task = "job"
    tt = f"{TASK_PREFIX}{task}"
    await override(conn, task, "cost_per_call", threshold=D(50))
    await insert_rows(conn, [row(task, ORG_A, at, cost=D("0.0010")) for at in base_times(T, 4)]
                      + [row(task, ORG_A, at, cost=D("0.0040")) for at in recent_times(T, 4)])
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        seeded = await J.ensure_platform_jobs(conn, org_id=ORG_P, created_by=U_SUPER, active=True,
                                              jobs={AD.JOB_KEY: J.JOBS[AD.JOB_KEY]})
    spec = J.JOBS[AD.JOB_KEY]
    trig = seeded[AD.JOB_KEY]["trigger_id"]
    AD.JOB_KWARGS.update(now=T, org_ids=[str(ORG_A)], task_types=[tt], include_test_orgs=True)
    now = datetime.now(timezone.utc)

    async def fire(after):
        occ = next_occurrences(schedule_cron=spec.cron, timezone_name=spec.timezone, after_utc=after, count=1)[0]
        res = await WS.run_scheduler_tick(conn, pool, now_utc=occ + timedelta(minutes=5), only_trigger_ids=[trig],
                                          log=lambda m: None)
        return occ, res

    try:
        occ, res = await fire(now)
        fired = res.fired[0] if res.fired else None
        if fired:
            RUN_IDS.append(fired["run_id"])
        steps = await platform(conn, "SELECT rs.status, rs.result FROM workflow_run_steps rs WHERE rs.workflow_run_id = "
                                     "$1", fired["run_id"]) if fired else []
        outs = [json.loads(s["result"]) if isinstance(s["result"], str) else s["result"] for s in steps]
        hd = ((outs[0] or {}).get("handler_data") or {}) if outs else {}
        check(bool(fired and fired["status"] == "completed" and len(steps) == 1 and hd.get("pairs") == 1
                   and hd.get("opened", 0) >= 1 and hd.get("model_calls") == 0),
              "the job fires through the REAL scheduler tick and completes: 1 task/org pair, an alert opened, 0 model "
              "calls (the step's output)", f"{res.summary()} {str(hd)[:300]}")
        al = await platform(conn, "SELECT count(*) FROM public.ai_drift_alerts WHERE task_type = $1 AND org_id = $2 AND "
                                  "status = 'open'", tt, ORG_A, fetch="fetchval")
        check(int(al) >= 1, "…and the alert it opened is in public.ai_drift_alerts", f"{al}")
        orig = AD.read_window

        async def failing_read(conn_, *, start, end, org_ids=None, task_types=None, include_test_orgs=False):
            # the CURRENT signature of services.ai_drift.read_window
            raise RuntimeError("verify_s28 forced read failure")

        check(shape(failing_read, skip_first=True) == shape(orig, skip_first=True)
              and len(shape(failing_read)) == len(shape(orig)),
              "the forced-failure stub has read_window's current signature", f"{inspect.signature(orig)}")
        AD.read_window = failing_read
        try:
            occ2, res2 = await fire(occ + timedelta(minutes=1))
        finally:
            AD.read_window = orig
        held = next((e.get("run_id") for e in res2.errors if str(e.get("trigger_id")) == str(trig)), None)
        if held:
            RUN_IDS.append(held)
        row_h = await platform(conn, "SELECT status, error_detail, org_id FROM workflow_runs WHERE id = $1", held,
                               fetch="fetchrow") if held else None
        check(bool(held and row_h and row_h["status"] == "held" and row_h["org_id"] == ORG_P
                   and "forced read failure" in (row_h["error_detail"] or "")),
              "a forced failure HOLDS the run with the reason", f"{res2.summary()} "
                                                                f"{row_h and (row_h['error_detail'] or '')[:200]}")
        todos = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'workflow_run_held' AND related_id = "
                                     "$1 AND status = 'open'", held) if held else []
        check(bool(held and {t["user_id"] for t in todos} == {U_SUPER}), "…and alerts the Super Admin (the held-run path)",
              f"{[str(t['user_id']) for t in todos]}")
    finally:
        AD.JOB_KWARGS.clear()
    # a run in a TEST platform org never reads a real org
    before = await real_fingerprints(conn)
    out = await AD.run_job(pool, org_id=ORG_P, user_id=U_SUPER, run_context={}, workflow_run_id=None)
    after = await real_fingerprints(conn)
    check(bool(out["data"]["pairs"] == 0 and before == after),
          "with no verify knobs, a run in a test platform org evaluates only its own org (0 pairs) — no real alert",
          f"{out['data']}")
    refused = None
    AD.JOB_KWARGS.update(org_ids=["00000000-0000-0000-0000-000000000001"], include_test_orgs=True)
    try:
        await AD.run_job(pool, org_id=ORG_P, user_id=U_SUPER, run_context={}, workflow_run_id=None)
    except J.PlatformJobError as exc:
        refused = str(exc)
    finally:
        AD.JOB_KWARGS.clear()
    check(bool(refused and "test orgs only" in refused), "…and refuses to evaluate a REAL org from a test platform org",
          f"{refused}")
    try:
        await AD.run_job(pool, org_id=ORG_A, user_id=U_SUPER, run_context={}, workflow_run_id=None)
        cl = None
    except J.PlatformJobError as exc:
        cl = str(exc)
    check(bool(cl and "platform org" in cl), "the action refuses to run in a client org", f"{cl}")
    real = await platform(conn, "SELECT t.is_active, t.schedule_cron, t.timezone FROM workflow_definitions d JOIN "
                                "workflow_triggers t ON t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' "
                                "WHERE d.org_id = $1 AND d.name = $2", J.PLATFORM_ORG_ID, spec.name)
    check(bool(len(real) == 1 and real[0]["is_active"] is False and real[0]["schedule_cron"] == spec.cron),
          "the REAL platform job 'AI — drift monitor' is seeded and OFF", f"{[dict(r) for r in real]}")
    other = await platform(conn, "SELECT count(*) FROM workflow_definitions d JOIN workflow_triggers t ON "
                                 "t.workflow_definition_id = d.id WHERE d.name = $1 AND t.is_active AND d.org_id <> ALL("
                                 "$2::uuid[])", spec.name, FIXTURE_ORGS, fetch="fetchval")
    check(int(other) == 0, "the drift job is OFF for every real org (no active trigger anywhere)", f"{other}")
    c, o = await api(U_SUPER, "GET", "/api/v1/admin/security-data/master/jobs")
    jobs = {j.get("key"): j for j in (o.get("jobs") or [])}
    dj = jobs.get(AD.JOB_KEY) or {}
    check(bool(c == 200 and dj and dj.get("trigger_active") is False and dj.get("fetches")),
          "the job switch screen (Security master → Platform jobs) lists it, OFF, with what it reads",
          f"{c} {str(dj)[:200]}")
    # the dry-run script: findings printed, nothing opened
    n0 = await counts(conn)
    code, out_s, err = run_cmd([sys.executable, str(HERE / "ai_drift_check.py"), "--dry-run", "--include-test-orgs",
                                "--org", str(ORG_A), "--task", tt, "--now", T.isoformat()], REPO, timeout=600)
    n1 = await counts(conn)
    check(bool(code == 0 and "[DRIFT" in out_s and "Cost per call" in out_s and "FINDINGS: 1 task/org pairs" in out_s
               and "read-only" in out_s), "ai_drift_check.py --dry-run prints every finding (the drift included)",
          f"{code} {(out_s + err)[-400:]}")
    check(n0 == n1, "…and opens nothing: every fixture-scoped table at the same count (alerts, events, todos, audit)",
          f"{ {k: (n0[k], n1[k]) for k in n0 if n0[k] != n1[k]} }")


# ── [Y] no model / no network ───────────────────────────────────────────────
async def y_no_model(conn):
    section("[Y] The monitor makes no model call and no network call (stubs at the CURRENT signatures)")
    bad = []
    for name, stub in (S.get("stubs") or {}).items():
        if shape(stub) != shape(ORIGINALS[name]):
            bad.append((name, str(inspect.signature(stub)), str(inspect.signature(ORIGINALS[name]))))
    check(bool(S.get("stubs")) and not bad, "each LLM stub has the current signature of what it replaces", f"{bad}")
    check(LLM_CALLS == [], "no LLM helper / chain executor was called by anything the monitor did", f"{LLM_CALLS[:3]}")
    check(NETWORK_CALLS == [], "no network call was attempted", f"{NETWORK_CALLS[:3]}")


# ── [Y] isolation ───────────────────────────────────────────────────────────
async def y_isolation(conn):
    section("[Y] Isolation both directions under a non-bypassing role")
    if not S.get("role_ok"):
        check(False, "isolation needs a role with rolbypassrls = false", "see the schema section")
        return
    tt = f"{TASK_PREFIX}alert"
    for me, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        own = await scoped(conn, me, "SELECT count(*) FROM public.ai_drift_alerts WHERE task_type = $1", tt,
                           fetch="fetchval")
        theirs = await scoped(conn, me, "SELECT count(*) FROM public.ai_drift_alerts WHERE org_id = $1", other,
                              fetch="fetchval")
        logs = await scoped(conn, me, "SELECT count(*) FROM public.ai_decision_log WHERE org_id = $1", other,
                            fetch="fetchval")
        ev = await scoped(conn, me, "SELECT count(*) FROM public.ai_drift_alert_events WHERE org_id = $1", other,
                          fetch="fetchval")
        check(int(own) >= 1 and int(theirs) == 0 and int(logs) == 0 and int(ev) == 0,
              f"org {str(me)[-2:]} reads its own alert and NONE of org {str(other)[-2:]}'s alerts, events or decision "
              "log", f"own {own} theirs {theirs} log {logs} events {ev}")
    refused = []
    for sql, args in (("INSERT INTO public.ai_drift_alerts (org_id, task_type, rule_key, opened_at, last_evaluated_at, "
                       "last_drift_at, detail, numbers, link) VALUES ($1, $2, 'cost_per_call', now(), now(), now(), 'x', "
                       "'{}'::jsonb, '/x')", (ORG_A, f"{TASK_PREFIX}forged")),
                      ("INSERT INTO public.ai_drift_thresholds (task_type, rule_key, threshold, min_recent, "
                       "min_baseline) VALUES ($1, 'cost_per_call', 1, 1, 1)", (f"{TASK_PREFIX}forged",)),
                      ("UPDATE public.ai_drift_thresholds SET threshold = 1 WHERE task_type IS NULL AND rule_key = "
                       "'cost_per_call' AND valid_to IS NULL RETURNING id", ())):
        try:
            r = await scoped(conn, ORG_A, sql, *args, fetch="fetch")
            refused.append(f"allowed ({len(r)} rows)" if r else "zero rows")
        except asyncpg.InsufficientPrivilegeError:
            refused.append("refused")
    check(refused[0] == "refused" and refused[1] == "refused" and refused[2] == "zero rows",
          "an org context cannot write an alert or a threshold, and its UPDATE of a default matches zero rows",
          f"{refused}")
    plat = await platform(conn, "SELECT count(*) FROM public.ai_drift_alerts WHERE task_type = $1", tt, fetch="fetchval")
    check(int(plat) >= 2, "a Super Admin context sees both orgs' alerts", f"{plat}")


async def y_real_rows(conn):
    section("[Y] Real rows unchanged (count + checksum, keyed by real id)")
    after = await real_fingerprints(conn)
    for k, v in S["real"].items():
        check(after[k] == v, f"{k} unchanged (count + checksum)", f"{v[0]} → {after[k][0]}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=900)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/aidrift/s28.test.mjs"], WEB_DIR, timeout=600)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None and re.search(r"^# pass 9$", out, re.M) is not None,
          "tests/aidrift/s28.test.mjs (by path): 9 pass, 0 fail", (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_s27.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_s27.py")], REPO, timeout=2700, env=env)
    t = nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(t.ok and not fails), "verify_s27.py: 0 FAIL (its own final TOTAL line + exit code)",
          f"{t.detail()}; {fails[:3]} {err[-200:] if not t.ok else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    t0 = time.monotonic()
    log: list[str] = []
    try:
        log = await teardown_retrying()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"CLEANUP_SECONDS: {time.monotonic() - t0:.1f}")
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await close_conn(conn)
    check(not left, "--cleanup-only: zero fixture rows remain", f"{left}")
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
        left0 = await leftovers(conn)
        check(not left0, "pre-run cleanup: zero fixture rows before seeding", f"{left0}")
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if not S.get("catalog") or not await guard_prefix(conn):
            check(False, "seeding needs the catalog read and the reserved prefixes free", "every DB section skipped")
        else:
            S["t_start"] = datetime.now(timezone.utc)
            pre = await counts(conn)
            S["real"] = await real_fingerprints(conn)
            await run("seed", seed, conn)
            if S.get("seeded"):
                await run("metrics", y_metrics, conn)
                await run("rules", y_rules, conn)
                await run("model alerts", y_model_alerts, conn)
                await run("alerts", y_alerts, conn)
                await run("thresholds", y_thresholds, conn)
                await run("queries", y_queries, conn)
                await run("job", y_job, conn)
                await run("isolation", y_isolation, conn)
            else:
                check(False, "seed completed", "every fixture-scoped section skipped")
            await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        await run("no model", y_no_model, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(not left, "zero fixture rows remain (decision-log rows, thresholds, alerts, events, todos, audit, "
                            "workflow rows, roles, users, orgs)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in pre:
                    check(post[t] == pre[t], f"{t} at its pre-test count (by fixture ids)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after[k] == S["real"][k], f"{k} unchanged after teardown (count + checksum)",
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
