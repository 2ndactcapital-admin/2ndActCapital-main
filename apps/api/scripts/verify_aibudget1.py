"""verify_aibudget1 — daily model budgets on the ensemble picker, enforced at
the call, and the daily document-reading run that stays inside them.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_aibudget1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

NO MODEL CALLS AND NO NETWORK. ``services.note_extraction.proxy._post`` — the
single network function every ensemble call goes through — is replaced by a
fake that records each request and answers with fixed usage (prompt 200 +
completion 100 = ~300 tokens per call). The proxy's /model/info readers
(``litellm_credentials.chat_capable_models`` / ``list_deployments``) are
stubbed, and every httpx transport refuses and records — any attempt is a
failed check. REAL: the app through an ASGI transport (middleware, Principal,
every gate), services.ai_budget's ledger (atomic reserve / settle on the live
database), services.ai_daily_runs (runs, leases, the scheduler step), the
cascade / replica / statement call paths, RLS and the live database. The app
connection is app_service (rolbypassrls = false, asserted).

FIXTURES: one org 99000000-0000-0000-0000-0000001b0001 (is_test = true,
asserted), two users (super_admin, org_admin); every fixture name starts with
'aib1-fx-' (deployments, catalog LLMs, System One) or 'aib1_fx_' (task keys);
the fund is 'AIBUDGET1 VERIFY FUND'. Ensemble configs are written only for
fixture task keys — the real tasks' configs are recorded before and must be
exactly the same after (none are active today). A "next day" is simulated by
moving the FIXTURE run's and the FIXTURE ledger rows' budget_day back one day
(the database clock cannot be moved); every expected date is computed at the
check.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN,
keyed by this run's fixture ids; then every counted table is re-read for its
exact pre-test count, and real rows (readings, runs, filings, catalog,
configs, ledger) are compared by count + checksum.

--self-checks-only: git / route self-checks only (no Doppler, database, npm).
--cleanup-only: the teardown and the leftover check only. With VERIFY_NESTED=1
the regression section prints one '[SKIP] nested run'.
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
import time
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

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "aibudget1"
BEFORE_REF = "c6f7077"   # main @ 'merge rep10' — the aibudget1-work base
_A = "/api/v1/admin/ai"
ROUTES = {
    ("GET", f"{_A}/jobs"): "super_admin",
    ("GET", f"{_A}/daily-runs"): "super_admin",
    ("POST", f"{_A}/daily-runs/{{task_key}}/run"): "super_admin",
}


def self_routes(check, find):
    """aibudget1's own routes: BEFORE_REF → its own merge (or HEAD while unmerged); each exists today with that
    gate. Never "every route added since a base commit"."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed,
          "aibudget1 changed or removed no existing route's gate", f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms if p.startswith(_A + "/")}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items()
           if k not in by or by[k].get("after") != g or (by[k].get("expect") or {}).get("rule") != "super"]
    check(not bad, f"each of the {len(ROUTES)} is gated super_admin (rule 'super') at aibudget1's own end point",
          f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "aibudget1's routes all still exist today with the same gate", f"{drift[:4]}")


def self_checks(check, find):
    self_routes(check, find)


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

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
import routers.ai_ensembles as RAE  # noqa: E402
import services.litellm_credentials as LC  # noqa: E402
from services import ai_budget as AB  # noqa: E402
from services import ai_daily_runs as DR  # noqa: E402
from services import ai_ensembles as AE  # noqa: E402
from services import held_away as HA  # noqa: E402
from services import rbac  # noqa: E402
from services import replica_terms as RT  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.database import get_pool  # noqa: E402
from services.note_extraction import cascade as CS  # noqa: E402
from services.note_extraction import documents as DOCS  # noqa: E402
from services.note_extraction import proxy as PX  # noqa: E402
from services.note_extraction import runner as RN  # noqa: E402
from services.note_extraction import schema as NS  # noqa: E402

TAG = "aib1verify"
FX = "aib1-fx-"            # deployments, catalog LLMs, System One
FXT = "aib1_fx_"           # task keys
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FUND_NAME = "AIBUDGET1 VERIFY FUND"
FUND_TICKER = "ZZAB1"
SEED_INSTRUMENT_CLASS = "index_etf"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001b{n:04d}")


ORG = _id(1)
U_SUPER, U_ADMIN = _id(11), _id(12)
USERS = {U_SUPER: ("super", "super_admin"), U_ADMIN: ("admin", "org_admin")}

S1 = f"{FX}s1"
M_A, M_B, M_SHARE = f"{FX}llm-a", f"{FX}llm-b", f"{FX}llm-share"
T_PICK, T_PUB = f"{FXT}pick", f"{FXT}public"
T_TOK, T_USD, T_UNL = f"{FXT}tok", f"{FXT}usd", f"{FXT}unl"
T_SHA, T_SHB = f"{FXT}shared_a", f"{FXT}shared_b"
T_CON1, T_CON2 = f"{FXT}con1", f"{FXT}con2"
T_DAILY, T_BUSY = f"{FXT}daily", f"{FXT}busy"
D = {k: f"{FX}{k}" for k in ("tok", "tok_p", "usd", "usd_p", "unl1", "unl2", "sh", "sh_pa", "sh_pb", "con",
                             "con_p1", "con_p2", "con_h", "day", "day2", "day_p", "daily", "daily_p", "busy",
                             "busy_p", "rep1", "rep2", "stm1", "stm2", "cas1", "cas2", "pair1", "pair2")}
PRICE = Decimal("0.001")                  # per token, for the dollar scenario
PROMPT_CHARS, MAX_T = 800, 100            # estimate = 800/4 + 100 = 300 tokens
FAKE_IN, FAKE_OUT = 200, 100              # recorded = 300 tokens per call

COUNTED = ("public.ai_ensemble_configs", "public.ai_daily_usage", "public.ai_daily_runs",
           "public.platform_model_catalog", "public.ai_system_one_models", "public.organizations", "public.users",
           "portfolio.replica_securities", "portfolio.securities_global", "portfolio.note_extraction_runs",
           "portfolio.note_term_readings", "portfolio.note_extraction_staging", "portfolio.replica_term_readings")

S: dict = {}
NETWORK_CALLS: list[str] = []
CALL_LOG: list[dict] = []
FAKE_DELAY = {"s": 0.0}
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
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def utc_today() -> date:
    """Always the UTC date AT THE CALL — never fixed at script start."""
    return datetime.now(timezone.utc).date()


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][0]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][0]}@test.local"


def calls_to(model: str) -> int:
    return sum(1 for c in CALL_LOG if c["model"] == model)


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def db_today(conn) -> date:
    """The budget day by the DATABASE clock, read at the call."""
    return await conn.fetchval(f"SELECT {AB.BUDGET_DAY_SQL}")


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch: everything that is not a fixture."""
    return {
        "configs": await fingerprint(conn, "SELECT * FROM public.ai_ensemble_configs WHERE NOT starts_with(task_key, $1)",
                                     FXT),
        "catalog": await fingerprint(conn, "SELECT * FROM public.platform_model_catalog WHERE NOT starts_with(model_id, "
                                           "$1)", FX),
        "system_one": await fingerprint(conn, "SELECT key, display_name, availability, is_default, model_version FROM "
                                              "public.ai_system_one_models WHERE NOT starts_with(key, $1)", FX),
        "ledger": await fingerprint(conn, "SELECT * FROM public.ai_daily_usage WHERE NOT starts_with(deployment, $1)", FX),
        "daily_runs": await fingerprint(conn, "SELECT id, task_key, status, documents_done FROM public.ai_daily_runs "
                                              "WHERE NOT starts_with(task_key, $1)", FXT),
        "note_readings": await fingerprint(conn, "SELECT id, run_id, field_key, cost_usd FROM "
                                                 "portfolio.note_term_readings"),
        "note_runs": await fingerprint(conn, "SELECT id, status, notes_done, spent_usd FROM "
                                             "portfolio.note_extraction_runs"),
        "staging": await fingerprint(conn, "SELECT id, status FROM portfolio.note_extraction_staging"),
        "replica_readings": await fingerprint(conn, "SELECT id, extraction_run_id FROM "
                                                    "portfolio.replica_term_readings"),
        "filings": await fingerprint(conn, "SELECT accession_number, pipeline_status, reference_filing_id FROM "
                                           "portfolio.edgar_index_filings"),
    }


async def ensemble_state(conn) -> dict:
    rows = await platform(conn, "SELECT task_key, id FROM public.ai_ensemble_configs WHERE is_active "
                                "AND NOT starts_with(task_key, $1)", FXT)
    return {r["task_key"]: str(r["id"]) for r in rows}


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
    import services.database as DBM
    p, DBM._pool = DBM._pool, None
    await close_conn(p)


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


def _plan() -> dict:
    return {
        "public.ai_daily_runs": ("starts_with(task_key, $1) OR started_by = ANY($2::uuid[])", [FXT, list(USERS)]),
        "public.ai_daily_usage": ("starts_with(deployment, $1)", [FX]),
        "public.ai_ensemble_configs": ("starts_with(task_key, $1) OR created_by = ANY($2::uuid[])", [FXT, list(USERS)]),
        "public.platform_model_catalog": ("starts_with(model_id, $1)", [FX]),
        "public.ai_system_one_models": ("starts_with(key, $1)", [FX]),
        "portfolio.securities_global": ("name = $1", [FUND_NAME]),
    }


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        res = await VC.cleanup(op, orgs=[ORG], users=list(USERS), user_sub_like=f"%{TAG}%", plan=_plan(),
                               label="verify_aibudget1")
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
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = $1", [ORG]),
        ("SELECT count(*) FROM ai_daily_runs WHERE starts_with(task_key, $1)", [FXT]),
        ("SELECT count(*) FROM ai_daily_usage WHERE starts_with(deployment, $1)", [FX]),
        ("SELECT count(*) FROM ai_ensemble_configs WHERE starts_with(task_key, $1) OR created_by = ANY($2::uuid[])",
         [FXT, list(USERS)]),
        ("SELECT count(*) FROM platform_model_catalog WHERE starts_with(model_id, $1)", [FX]),
        ("SELECT count(*) FROM ai_system_one_models WHERE starts_with(key, $1)", [FX]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = $1", [FUND_NAME]),
        ("SELECT count(*) FROM portfolio.replica_securities WHERE org_id = $1", [ORG]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ App access + stubs ═════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(ORG)}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_aibudget1"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"raw": "timeout"}
    try:
        return res.status_code, json.loads(res.content.decode("utf-8") or "{}")
    except ValueError:
        return res.status_code, {"raw": res.content[:300].decode("utf-8", "replace")}


def _detail(body) -> str:
    return str(body.get("detail", body))[:300] if isinstance(body, dict) else str(body)[:300]


CHAT_MODELS = {M_A, M_B, M_SHARE, "gpt-5-mini", "gpt-5-nano"}


async def fake_post(path: str, body: dict, timeout: float):
    """The ONE network function of every ensemble call, faked: records the
    request, answers with fixed usage, and prices it for the dollar scenario."""
    model = body.get("model")
    CALL_LOG.append({"path": path, "model": model, "max_tokens": body.get("max_tokens"),
                     "has_max_tokens": "max_tokens" in body, "tags": (body.get("metadata") or {}).get("tags")})
    if FAKE_DELAY["s"]:
        await asyncio.sleep(FAKE_DELAY["s"])
    resp = {"model": model, "choices": [{"message": {"content": "{}" if "rows" not in json.dumps(
        body.get("messages") or [])[:4000] else '{"rows": []}'}}],
            "usage": {"prompt_tokens": FAKE_IN, "completion_tokens": FAKE_OUT}}
    headers = {"x-litellm-attempted-fallbacks": "0"}
    if model in (D["usd"],):
        headers["x-litellm-response-cost"] = str((FAKE_IN + FAKE_OUT) * PRICE)
    return PX.ProxyResponse(200, resp, json.dumps(resp), headers, 3)


def fake_catalog(*names, price: Decimal | None = None) -> dict:
    return {n: PX.Deployment(name=n, deployment_id=None, upstream=f"fixture/{n}", price_key=n,
                             input_cost_per_token=float(price) if price is not None else None,
                             output_cost_per_token=float(price) if price is not None else None,
                             cache_read_cost_per_token=None, supports_response_schema=False, max_input_tokens=None)
            for n in names}


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile
    PX._post = fake_post
    LC.chat_capable_models = lambda: set(CHAT_MODELS)
    LC.list_deployments = lambda: [{"model_name": n, "litellm_params": {"model": f"fixture/{n}"},
                                    "model_info": {}} for n in sorted(CHAT_MODELS)]
    # The fixture task keys exist for this run only; T_PUB stands in for a public-data job.
    AE.KNOWN_TASK_KEYS = frozenset({*AE.KNOWN_TASK_KEYS, T_PICK, T_PUB})
    RAE.KNOWN_TASK_KEYS = AE.KNOWN_TASK_KEYS
    AE.PUBLIC_DATA_TASK_KEYS = frozenset({*AE.PUBLIC_DATA_TASK_KEYS, T_PUB})

    async def refuse_async(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_aibudget1: a network call was attempted: {request.url}")

    # Async only (the rep10 precedent): every ensemble call path is async and goes through proxy._post (faked
    # above); EdgarTools' sync parse blocks its own sockets (edgartools_reader.network_blocked).
    httpx.AsyncHTTPTransport.handle_async_request = refuse_async


# ═══ Fixture jobs (fake documents, the REAL budget + run machinery) ════════
async def config_row(pool, task_key):
    async with AB._platform_tx(pool) as conn:
        row = await conn.fetchrow("SELECT e.*, s.model_route AS jev_route FROM ai_ensemble_configs e JOIN "
                                  "ai_system_one_models s ON s.key = e.system_one_model WHERE e.task_key = $1 "
                                  "AND e.is_active", task_key)
    return dict(row) if row else None


def fx_job(task_key: str, docs: list[str], read_log: list, *, catalog: dict | None = None) -> DR.Job:
    """A document job whose 'reading' is one ensemble read per document: both
    models' calls reserved together (ai_budget.pair), then made through
    proxy.chat — exactly the shape of the real jobs."""
    done: set = set()

    async def load_config(pool):
        return await config_row(pool, task_key)

    async def queue(pool, *, exclude=(), limit=DR.BATCH, include_test_orgs=False, cfg=None):
        return [{"id": d} for d in docs if d not in done and d not in exclude][:limit]

    async def read_batch(pool, run, cfg, items, stop_when):
        res = DR.BatchResult()
        msgs = [{"role": "user", "content": "x" * PROMPT_CHARS}]
        for it in items:
            if stop_when():
                break
            try:
                async with AB.pair([(cfg["model_1"], PROMPT_CHARS, MAX_T), (cfg["model_2"], PROMPT_CHARS, MAX_T)]):
                    await asyncio.gather(*(PX.chat({"model": cfg[s], "messages": msgs, "max_tokens": MAX_T,
                                                    "metadata": {"tags": [task_key, it["id"]]}})
                                           for s in ("model_1", "model_2")))
            except AB.BudgetReached as exc:
                res.budget = exc
                break
            done.add(it["id"])
            read_log.append((task_key, run["id"], it["id"]))
            res.done += 1
        return res

    job = DR.Job(task_key, f"fixture {task_key}", load_config, queue, read_batch,
                 catalog_loader=(lambda: catalog) if catalog else None)
    job.docs = docs  # type: ignore[attr-defined]
    return job


async def insert_config(conn, task_key, m1, m2, k1="unlimited", v1=None, k2="unlimited", v2=None):
    return await platform(
        conn,
        "INSERT INTO ai_ensemble_configs (task_key, model_1, model_1_version, model_2, model_2_version, "
        "system_one_model, system_one_model_version, is_active, activated_at, notes, model_1_budget_kind, "
        "model_1_budget_value, model_2_budget_kind, model_2_budget_value) VALUES ($1, $2, 'fx', $3, 'fx', $4, 'fx', "
        "true, now(), 'verify_aibudget1 fixture', $5, $6::numeric, $7, $8::numeric) RETURNING id",
        task_key, m1, m2, S1, k1, v1, k2, v2, fetch="fetchval")


async def ledger(conn, dep, day=None) -> dict | None:
    day = day or await db_today(conn)
    r = await platform(conn, "SELECT * FROM ai_daily_usage WHERE budget_day = $1 AND deployment = $2", day, dep,
                       fetch="fetchrow")
    return dict(r) if r else None


async def run_row(conn, run_id) -> dict | None:
    r = await platform(conn, "SELECT * FROM ai_daily_runs WHERE id = $1::uuid", str(run_id), fetch="fetchrow")
    return dict(r) if r else None


def used_tokens(row) -> int:
    return int(row["input_tokens"]) + int(row["output_tokens"]) if row else 0


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    pool = await get_pool()
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role is not None and role["rolbypassrls"] is False, "the app connection does not bypass RLS",
          f"{dict(role) if role else None}")
    ready = int(await platform(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE pipeline_status = "
                                     "'ready_for_extraction' AND reference_filing_id IS NOT NULL", fetch="fetchval"))
    staged = int(await platform(conn, "SELECT count(DISTINCT reference_filing_id) FROM "
                                      "portfolio.note_extraction_staging", fetch="fetchval"))
    note_q = await DR.queue_items(DR.JOBS["note_terms_extraction"], pool, limit=10 ** 7)
    staged_ready = int(await platform(
        conn, "SELECT count(DISTINCT f.reference_filing_id) FROM portfolio.edgar_index_filings f JOIN "
              "portfolio.note_extraction_staging s ON s.reference_filing_id = f.reference_filing_id WHERE "
              "f.pipeline_status = 'ready_for_extraction'", fetch="fetchval"))
    check(len(note_q) == ready - staged_ready, "1a. the note job's queue = ready_for_extraction manifest rows minus "
          "those already staged (same predicate run directly in SQL)", f"queue={len(note_q)} ready={ready} "
          f"staged_ready={staged_ready}")
    rep_q = await DR.queue_items(DR.JOBS["replica_terms_extraction"], pool, limit=10 ** 6)
    find("1a jobs that read documents", f"note_terms_extraction: 424B2/FWP, started by scripts/run_note_extraction_"
         f"pilot.py (cascade.active_ensemble) — {len(note_q)} queued ({ready} ready, {staged} staged); "
         f"replica_terms_extraction: prospectuses, started per entry by a person (build_draft → run_extraction, "
         f"replica_terms.active_ensemble with a note_terms_extraction fallback) — {len(rep_q)} queued; "
         "statement_positions: member statements, started by the member flow (held_away.create_statement_import, "
         "statement_ensemble) — member data, never scheduled")
    check("statement_positions" not in DR.JOBS and "statement_positions" in DR.NEVER_SCHEDULED,
          "1a. statement_positions is not a daily job (member data)", f"{sorted(DR.JOBS)}")
    rows = await platform(conn, "SELECT id, input_tokens, output_tokens FROM portfolio.note_term_readings WHERE "
                                "field_key = '__call__' AND source IN ('model_1', 'model_2', 'escalation')")
    VS.when_empty(rows, "1b. every note reader call row records input and output tokens",
                  lambda r: r["input_tokens"] is not None and r["output_tokens"] is not None, check=check, find=find)
    rrows = await platform(conn, "SELECT id, input_tokens, output_tokens FROM portfolio.replica_term_readings WHERE "
                                 "source IN ('model_1', 'model_2') AND status = 'ok' AND field_key = '__call__'")
    VS.when_empty(rrows, "1b. every replica reader call row records tokens",
                  lambda r: r["input_tokens"] is not None and r["output_tokens"] is not None, check=check, find=find)
    src = inspect.getsource(HA._proxy_reader)
    check("input_tokens" not in src and "usage" not in src,
          "1b. REPRODUCED: held_away's statement reader records no tokens anywhere (so the ledger is a table, not "
          "a view over the readings)")
    find("1b token recording", "note readings (__call__ rows) and replica readings carry input/output tokens; the "
         "statement path records none; Jev (System One) is not a Model 1/2 call and is not budgeted; the app reads no "
         "LiteLLM spend log (app_service cannot read the litellm schema; SpendLogs flush asynchronously)")
    d1, d2 = await db_today(conn), utc_today()
    if d1 != d2:   # a check straddling midnight: read both again
        d1, d2 = await db_today(conn), utc_today()
    check(d1 == d2, "1c. the budget day is the UTC date, from the database clock at the call", f"db={d1} utc={d2}")
    find("1c day boundary", "midnight UTC — OpenAI's daily allowances reset on UTC days; shown on the picker and the "
         "daily-run screen from the envelope's day_boundary")
    reg = (HERE / "register_note_extraction_models.py").read_text(encoding="utf-8")
    keys = sorted(set(re.findall(r'\("gpt-5-(?:mini|nano)", "openai/gpt-5-(?:mini|nano)", "([A-Z_]+)"', reg)))
    check(keys == ["OPENAI_API_KEY"], "1d. both OpenAI catalog models are registered against ONE key NAME",
          f"{keys}")
    find("1d OpenAI deployments", "gpt-5-mini → openai/gpt-5-mini and gpt-5-nano → openai/gpt-5-nano, DB-stored on "
         "the proxy, key reference os.environ/OPENAI_API_KEY (Doppler prd_lite_llm). /model/info exposes no project "
         "or organization, so whether that key's OpenAI project has data sharing ON cannot be read from here — an "
         "operator step on OpenAI's dashboard. services.model_catalog records that project as the sharing one "
         "(public_data_only = true). Not changed.")
    find("1e picker shape", "GET /admin/ai/ensembles → {llm_options[], system_one_options[], preselected, blocker, "
         "active, history, permissions}; POST {task_key, model_1, model_2, system_one_model, notes}. EnsemblePanel: "
         "Model 1 / Model 2 / System One selects in one grid → the budget control now sits under each model select")


async def y_schema(conn):
    section("[Y] Schema landed; CHECKs; immutability intact")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_aibudget1.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_aibudget1.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")

    async def expect(sql, *args, constraint=None, kind=asyncpg.CheckViolationError):
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(sql, *args)
                raise RuntimeError("no error")   # rolls the fixture insert back
        except kind as exc:
            return constraint is None or getattr(exc, "constraint_name", None) == constraint
        except Exception:  # noqa: BLE001
            return False

    ins = ("INSERT INTO ai_ensemble_configs (task_key, model_1, model_1_version, model_2, model_2_version, "
           "system_one_model, system_one_model_version, model_1_budget_kind, model_1_budget_value) "
           "VALUES ($1, 'a', 'v', 'b', 'v', $2, 'v', $3, $4::numeric)")
    for kind, val, con in (("unlimited", "5", "ai_ensemble_configs_budget_value_null_chk"),
                           ("tokens_per_day", None, "ai_ensemble_configs_budget_value_null_chk"),
                           ("tokens_per_day", "0", "ai_ensemble_configs_budget_value_positive_chk"),
                           ("usd_per_day", "-1", "ai_ensemble_configs_budget_value_positive_chk"),
                           ("tokens_per_day", "1.5", "ai_ensemble_configs_budget_tokens_whole_chk"),
                           ("per_hour", "5", "ai_ensemble_configs_budget_kind_chk")):
        ok = await expect(ins, f"{FXT}chk", S1, kind, val, constraint=con)
        check(ok, f"the database refuses budget ({kind}, {val}) by {con}")
    n = await platform(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1", f"{FXT}chk",
                       fetch="fetchval")
    check(int(n) == 0, "every refused insert left nothing", f"{n}")
    ok = await expect("INSERT INTO ai_daily_runs (task_key, budget_day, trigger) VALUES ($1, current_date, 'manual')",
                      f"{FXT}chk", constraint="ai_daily_runs_manual_by_chk")
    check(ok, "a manual run must name who started it (ai_daily_runs_manual_by_chk)")
    ok = await expect("INSERT INTO ai_daily_runs (task_key, budget_day, trigger, status, finished_at) VALUES "
                      "($1, current_date, 'scheduled', 'stopped_budget', now())", f"{FXT}chk",
                      constraint="ai_daily_runs_budget_stop_chk")
    check(ok, "a budget stop must name the model (ai_daily_runs_budget_stop_chk)")
    ok = await expect("INSERT INTO ai_daily_usage (budget_day, deployment, input_tokens) VALUES (current_date, $1, -1)",
                      f"{FX}chk", constraint="ai_daily_usage_nonneg_chk")
    check(ok, "the ledger refuses a negative count (ai_daily_usage_nonneg_chk)")
    for t in ("ai_daily_usage", "ai_daily_runs"):
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                       "tablename = $1", t)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"public.{t}: a policy for every operation",
              f"{sorted(cmds)}")
        seen = await conn.fetchval(f"SELECT count(*) FROM public.{t}")   # no super-admin context
        total = await platform(conn, f"SELECT count(*) FROM public.{t}", fetch="fetchval")
        check(int(seen) == 0 and (int(total) >= 0), f"public.{t}: invisible without super-admin context (RLS)",
              f"without={seen} with={total}")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog before seeding")
    defs = {r["conname"]: r["d"] for r in await platform(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE contype = 'c' AND conrelid = "
              "ANY(ARRAY['public.platform_model_catalog', 'public.ai_system_one_models', "
              "'portfolio.securities_global', 'portfolio.replica_securities']::regclass[])")}
    want = (("platform_model_catalog_availability_chk", "available"), ("ai_system_one_models_availability_chk",
            "available"), ("securities_global_type_chk", "fund"), ("replica_securities_status_chk", "active"))
    bad = [c for c, lit in want if f"'{lit}'" not in (defs.get(c) or "")]
    check(not bad, "every CHECK-constrained literal the seed uses is admitted", f"{bad}")
    dn = defs.get("replica_securities_display_name_chk") or ""
    check("Holdings of" in dn, "replica display_name must start 'Holdings of '", dn[:120])
    ic = await platform(conn, "SELECT key FROM portfolio.replica_instrument_classes WHERE key = $1 AND enabled "
                              "AND valid_to IS NULL AND system_to IS NULL", SEED_INSTRUMENT_CLASS, fetch="fetchval")
    uq = {r["conname"] for r in await platform(conn, "SELECT conname FROM pg_constraint WHERE contype = 'u' AND "
                                                     "conrelid = 'public.platform_model_catalog'::regclass")}
    fk = await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conname = "
                              "'ai_ensemble_configs_system_one_model_fkey'", fetch="fetchval")
    check(ic == SEED_INSTRUMENT_CLASS and "platform_model_catalog_model_id_key" in uq and int(fk) == 1,
          "instrument class enabled; catalog model_id UNIQUE; configs' System One FK present", f"{ic} {uq} {fk}")
    S["literals_ok"] = not bad and ic == SEED_INSTRUMENT_CLASS


async def guard_prefix(conn) -> bool:
    n = await leftovers(conn)
    real = await platform(conn, "SELECT count(*) FROM portfolio.replica_securities WHERE ticker = $1 AND NOT org_id = "
                                "$2", FUND_TICKER, ORG, fetch="fetchval")
    return check(n == 0 and int(real) == 0, f"the reserved prefixes ({FX}, {FXT}, {FUND_NAME}, {FUND_TICKER}) and "
                 "the fixture org match no row", f"{n}/{real}")


async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                           ORG, "AIBUDGET1 Verify Org", f"{TAG}-org")
        for uid, (nm, role) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, ORG, email_of(uid),
                f"AIBUDGET1 {nm}", sub_of(uid), role)
        await rbac.grant_org_admin(conn, U_ADMIN, ORG)
        await conn.execute(
            "INSERT INTO ai_system_one_models (key, display_name, provider, model_route, model_version, availability, "
            "is_default, last_verified_at, last_check_detail, notes) VALUES ($1, 'AIB1 fixture System One', "
            "'typesafe', 'fixture-route', 'fx-1', 'available', false, now(), 'fixture', 'verify_aibudget1')", S1)
        for mid, pdo in ((M_A, False), (M_B, False), (M_SHARE, True)):
            await conn.execute("INSERT INTO platform_model_catalog (model_id, display_name, provider, availability, "
                               "public_data_only) VALUES ($1, $2, 'openai', 'available', $3)", mid, f"AIB1 {mid}", pdo)
        sec = await conn.fetchval("INSERT INTO portfolio.securities_global (name, security_type) VALUES ($1, 'fund') "
                                  "RETURNING id", FUND_NAME)
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, display_name, status, created_by) VALUES ($1, $2, $3, $4, 'fixture', $5, 'active', "
            "$6)", ORG, sec, SEED_INSTRUMENT_CLASS, FUND_TICKER, f"Holdings of {FUND_TICKER}", U_SUPER)
    S["fund"] = str(sec)
    org = await platform(conn, "SELECT is_test FROM organizations WHERE id = $1", ORG, fetch="fetchval")
    users = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                           fetch="fetchval")
    check(org is True and int(users) == len(USERS), "fixture org and users are is_test = true", f"{org} {users}")
    S["seeded"] = org is True


# ── picker ──────────────────────────────────────────────────────────────────
async def cfg_count(conn, task_key) -> int:
    return int(await platform(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1", task_key,
                              fetch="fetchval"))


async def y_picker(conn):
    section("[Y] Picker budgets (unlimited / tokens / dollars), validation, history, public-data refusal, "
            "super_admin both ways")
    path = f"{_A}/ensembles"
    s_a, b_a = await api(U_ADMIN, "GET", f"{path}?task_key={T_PICK}")
    s_s, b_s = await api(U_SUPER, "GET", f"{path}?task_key={T_PICK}")
    check(s_a == 403 and s_s == 200, "GET picker: org_admin 403, super_admin 200 on the identical request",
          f"{s_a} {s_s} {_detail(b_a)}")
    if s_s != 200:
        return
    kinds = [k["key"] for k in (b_s.get("vocabularies") or {}).get("budget_kinds") or []]
    check(kinds == list(AB.BUDGET_KINDS), "the envelope carries the budget-kind vocabulary", f"{kinds}")
    db = b_s.get("day_boundary") or {}
    check(db.get("timezone") == "UTC" and db.get("today") == utc_today().isoformat(),
          "the envelope carries the day boundary (UTC) and today's budget day", f"{db}")
    check("{model}" in (b_s.get("vocabularies") or {}).get("semantics_template", "")
          and "across Hollisworks" in (b_s.get("vocabularies") or {}).get("semantics_template", ""),
          "the envelope carries the semantics sentence ('… across Hollisworks')")
    opts = {o["model_id"]: o for o in b_s.get("llm_options") or []}
    share = opts.get(M_SHARE) or {}
    check(share.get("selectable") is False and "public-data-only" in str(share.get("unavailable_reason")),
          "a data-sharing model is unselectable for a non-public job, with the reason", f"{share}")
    check((opts.get(M_A) or {}).get("default_budget_kind") == "unlimited"
          and share.get("default_budget_kind") == "tokens_per_day",
          "default budget: Unlimited for a model with no public_data_only flag; a number for a sharing model")
    usage = b_s.get("usage_today") or {}
    check(M_A in usage and "total_tokens" in (usage.get(M_A) or {}), "today's usage per model is in the envelope",
          f"{list(usage)[:4]}")

    s1, b1 = await api(U_SUPER, "GET", f"{path}?task_key=statement_positions")
    s2, b2 = await api(U_SUPER, "GET", f"{path}?task_key=note_terms_extraction")
    o1 = {o["model_id"]: o for o in (b1.get("llm_options") or [])}.get("gpt-5-mini") or {}
    o2 = {o["model_id"]: o for o in (b2.get("llm_options") or [])}.get("gpt-5-mini") or {}
    check(s1 == 200 and o1.get("selectable") is False and "public-data-only" in str(o1.get("unavailable_reason")),
          "the REAL gpt-5-mini is refused for statement_positions in the picker, with the reason", f"{o1}")
    check(s2 == 200 and o2.get("selectable") is True, "… and selectable for note_terms_extraction (a public job)",
          f"{o2.get('unavailable_reason')}")

    body = {"task_key": T_PICK, "model_1": M_A, "model_2": M_B, "system_one_model": S1, "notes": "aib1 #1",
            "model_1_budget_kind": "tokens_per_day", "model_1_budget_value": 1000,
            "model_2_budget_kind": "usd_per_day", "model_2_budget_value": "2.50"}
    n0 = await cfg_count(conn, T_PICK)
    s_a, b_a = await api(U_ADMIN, "POST", path, body)
    n1 = await cfg_count(conn, T_PICK)
    s_s, b_s = await api(U_SUPER, "POST", path, body)
    n2 = await cfg_count(conn, T_PICK)
    check(s_a == 403 and n1 == n0 and s_s == 201 and n2 == n0 + 1,
          "POST activation: org_admin 403 (nothing written), super_admin 201 on the identical request",
          f"{s_a}/{n0}→{n1} {s_s}/{n2} {_detail(b_s) if s_s != 201 else ''}")
    if s_s != 201:
        return
    first = await platform(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1::uuid", b_s["ensemble"]["id"],
                           fetch="fetchrow")
    check(first is not None and first["model_1_budget_kind"] == "tokens_per_day"
          and first["model_1_budget_value"] == Decimal("1000") and first["model_2_budget_kind"] == "usd_per_day"
          and first["model_2_budget_value"] == Decimal("2.50"),
          "budgets saved per model (independent re-read): 1000 tokens/day, $2.50/day",
          f"{dict(first) if first else None}")

    body2 = {**body, "notes": "aib1 #2", "model_1_budget_kind": "unlimited", "model_1_budget_value": None,
             "model_2_budget_kind": "tokens_per_day", "model_2_budget_value": "5000"}
    s3, b3 = await api(U_SUPER, "POST", path, body2)
    second = await platform(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1::uuid",
                            (b3.get("ensemble") or {}).get("id"), fetch="fetchrow") if s3 == 201 else None
    old = await platform(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", first["id"], fetch="fetchrow")
    active = await platform(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1 AND is_active",
                            T_PICK, fetch="fetchval")
    check(s3 == 201 and second is not None and second["model_1_budget_kind"] == "unlimited"
          and second["model_1_budget_value"] is None, "unlimited stores a NULL value",
          f"{s3} {_detail(b3) if s3 != 201 else dict(second) if second else None}")
    check(second is not None and second["id"] != first["id"] and second["model_1"] == first["model_1"]
          and old["is_active"] is False and old["retired_at"] is not None and int(active) == 1
          and str(b3.get("retired_id")) == str(first["id"]),
          "a budget-only change makes a NEW config row and retires the old one (one active per job)",
          f"old active={old['is_active']} retired_at={old['retired_at']} active_rows={active}")
    for label, sql in (("the active row", "UPDATE ai_ensemble_configs SET model_2_budget_value = 999 WHERE id = $1"),
                       ("the retired row", "UPDATE ai_ensemble_configs SET model_1_budget_value = 999 WHERE id = $1")):
        target = second["id"] if "active" in label else first["id"]
        err = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(sql, target)
        except Exception as exc:  # noqa: BLE001
            err = exc
        after = await platform(conn, "SELECT model_1_budget_value, model_2_budget_value FROM ai_ensemble_configs "
                                     "WHERE id = $1", target, fetch="fetchrow")
        check(err is not None and Decimal(999) not in (after["model_1_budget_value"], after["model_2_budget_value"]),
              f"the immutability trigger refuses editing a budget on {label}; the value is unchanged",
              f"raised={type(err).__name__ if err else None}")

    bad = (("tokens 0", {"model_1_budget_kind": "tokens_per_day", "model_1_budget_value": 0}, "positive"),
           ("tokens -5", {"model_1_budget_kind": "tokens_per_day", "model_1_budget_value": -5}, "positive"),
           ("tokens 1000.5", {"model_1_budget_kind": "tokens_per_day", "model_1_budget_value": "1000.5"}, "whole"),
           ("tokens 'abc'", {"model_1_budget_kind": "tokens_per_day", "model_1_budget_value": "abc"}, "number"),
           ("tokens, no amount", {"model_1_budget_kind": "tokens_per_day", "model_1_budget_value": None}, "amount"),
           ("dollars 2.555", {"model_2_budget_kind": "usd_per_day", "model_2_budget_value": "2.555"}, "cents"),
           ("unlimited with an amount", {"model_1_budget_kind": "unlimited", "model_1_budget_value": 5}, "no amount"),
           ("unknown kind", {"model_1_budget_kind": "per_hour", "model_1_budget_value": 5}, "unknown budget kind"))
    for label, patch, needle in bad:
        n_before = await cfg_count(conn, T_PICK)
        st, bd = await api(U_SUPER, "POST", path, {**body, **patch})
        n_after = await cfg_count(conn, T_PICK)
        check(st == 422 and n_after == n_before and needle in _detail(bd),
              f"a bad budget ({label}) is refused, nothing written", f"{st} {n_before}→{n_after} {_detail(bd)}")

    sp0 = await cfg_count(conn, "statement_positions")
    sp_body = {"task_key": "statement_positions", "model_1": M_SHARE, "model_2": M_A, "system_one_model": S1,
               "model_1_budget_kind": "tokens_per_day", "model_1_budget_value": 1000}
    s_a, b_a = await api(U_ADMIN, "POST", path, sp_body)
    s_s, b_s2 = await api(U_SUPER, "POST", path, sp_body)
    sp1 = await cfg_count(conn, "statement_positions")
    check(s_a == 403 and s_s == 422 and "public-data-only" in _detail(b_s2) and sp1 == sp0,
          "a public_data_only model is refused for statement_positions with the reason (super_admin 422; "
          "org_admin 403 first); nothing written", f"{s_a} {s_s} {sp0}→{sp1} {_detail(b_s2)}")

    pub = {"task_key": T_PUB, "model_1": M_SHARE, "model_2": M_A, "system_one_model": S1}
    s4, b4 = await api(U_SUPER, "POST", path, pub)
    n_pub = await cfg_count(conn, T_PUB)
    check(s4 == 422 and "enter its daily budget" in _detail(b4) and n_pub == 0,
          "a data-sharing model with no budget given is refused (its allowance is never unlimited by default)",
          f"{s4} {_detail(b4)}")
    s5, b5 = await api(U_SUPER, "POST", path, {**pub, "model_1_budget_kind": "tokens_per_day",
                                               "model_1_budget_value": 250000})
    check(s5 == 201 and await cfg_count(conn, T_PUB) == 1,
          "… and accepted for a public-data job with a token budget", f"{s5} {_detail(b5) if s5 != 201 else ''}")

    s6, b6 = await api(U_SUPER, "GET", f"{path}?task_key={T_PICK}")
    hist = b6.get("history") or []
    shown = [(h.get("model_1_budget_kind"), h.get("model_1_budget_value"), h.get("model_2_budget_kind"),
              h.get("model_2_budget_value")) for h in hist]
    check(s6 == 200 and len(hist) == 2 and ("unlimited", None, "tokens_per_day", "5000") in shown
          and ("tokens_per_day", "1000", "usd_per_day", "2.50") in shown,
          "the history list shows each config's budgets", f"{shown}")


# ── enforcement ─────────────────────────────────────────────────────────────
async def scenario(conn, task_key, docs, *, m1, m2, k1="unlimited", v1=None, k2="unlimited", v2=None, catalog=None):
    await insert_config(conn, task_key, m1, m2, k1, v1, k2, v2)
    log: list = []
    job = fx_job(task_key, docs, log, catalog=catalog)
    pool = await get_pool()
    run = await DR.create_run(pool, task_key, trigger="scheduled")
    out = await DR.drive(pool, run["id"], jobs={task_key: job})
    return run, out, log, job


async def y_enforcement(conn):
    section("[Y] Enforcement: stop before the crossing call, both kinds, unlimited, shared across jobs, "
            "concurrency safe, day boundary")
    pool = await get_pool()
    docs = [f"doc{i}" for i in range(1, 7)]

    # tokens_per_day 1,000 with ~300-token calls
    run, out, log, _ = await scenario(conn, T_TOK, docs, m1=D["tok"], m2=D["tok_p"], k1="tokens_per_day", v1="1000")
    row, lg = await run_row(conn, run["id"]), await ledger(conn, D["tok"])
    check(calls_to(D["tok"]) == 3, "tokens_per_day 1,000 with ~300-token calls: the 4th call is never made",
          f"calls={calls_to(D['tok'])}")
    check(row["status"] == "stopped_budget" and row["stopped_model"] == D["tok"]
          and row["stopped_budget_kind"] == "tokens_per_day" and row["stopped_budget_value"] == Decimal("1000")
          and row["documents_done"] == 3,
          "the run stopped cleanly: stopped_budget naming the model, its kind and budget; 3 documents done",
          f"{row['status']} {row['stopped_model']} {row['stopped_budget_kind']} {row['stopped_budget_value']} "
          f"done={row['documents_done']} detail={row['stop_detail']}")
    check(lg is not None and used_tokens(lg) == 900 and used_tokens(lg) <= 1000 and int(lg["reserved_tokens"]) == 0,
          "the ledger: 900 tokens recorded (≤ 1,000), no reservation left behind", f"{lg}")
    check(calls_to(D["tok_p"]) == 3, "never a one-model reading: the partner (unlimited) model was not called for "
          "the 4th document either", f"partner calls={calls_to(D['tok_p'])}")
    u = (row["usage"] if isinstance(row["usage"], dict) else json.loads(row["usage"])).get(D["tok"]) or {}
    check(u.get("input_tokens") == 600 and u.get("output_tokens") == 300 and u.get("calls") == 3,
          "the run row records tokens per model", f"{u}")

    # usd_per_day
    cat = fake_catalog(D["usd"], D["usd_p"], price=PRICE)
    run, out, log, _ = await scenario(conn, T_USD, docs, m1=D["usd"], m2=D["usd_p"], k1="usd_per_day", v1="1.00",
                                      catalog=cat)
    row, lg = await run_row(conn, run["id"]), await ledger(conn, D["usd"])
    check(calls_to(D["usd"]) == 3 and row["status"] == "stopped_budget" and row["stopped_model"] == D["usd"]
          and row["stopped_budget_kind"] == "usd_per_day" and row["documents_done"] == 3,
          "usd_per_day $1.00 with $0.30 calls: the 4th call is never made; stopped_budget naming the model",
          f"calls={calls_to(D['usd'])} {row['status']} {row['stopped_model']} {row['stop_detail']}")
    check(lg is not None and Decimal(lg["cost_usd"]) == Decimal("0.90") and Decimal(lg["cost_usd"]) <= Decimal("1.00")
          and Decimal(lg["reserved_usd"]) == 0, "the ledger: $0.90 recorded (≤ $1.00), as Decimal", f"{lg}")

    # unlimited
    run, out, log, _ = await scenario(conn, T_UNL, docs[:5], m1=D["unl1"], m2=D["unl2"])
    row, lg = await run_row(conn, run["id"]), await ledger(conn, D["unl1"])
    check(row["status"] == "finished_queue_empty" and row["documents_done"] == 5 and calls_to(D["unl1"]) == 5,
          "unlimited runs to the queue's end (finished_queue_empty, 5 documents)",
          f"{row['status']} {row['documents_done']} {row['stop_detail']}")
    check(lg is not None and int(lg["calls"]) == 5 and used_tokens(lg) == 1500,
          "an unlimited model's usage is still counted in the shared ledger", f"{lg}")

    # shared across jobs: job B (unlimited) uses 600 of D_SH, then job A (1,000) fits only one more document
    run_b, _, _, _ = await scenario(conn, T_SHB, docs[:2], m1=D["sh"], m2=D["sh_pb"])
    run_a, _, _, _ = await scenario(conn, T_SHA, docs, m1=D["sh"], m2=D["sh_pa"], k1="tokens_per_day", v1="1000")
    ra, rb, lg = await run_row(conn, run_a["id"]), await run_row(conn, run_b["id"]), await ledger(conn, D["sh"])
    check(rb["documents_done"] == 2 and ra["documents_done"] == 1 and ra["status"] == "stopped_budget"
          and ra["stopped_model"] == D["sh"] and used_tokens(lg) == 900,
          "usage counted across jobs: job B's 600 tokens on the model count against job A's 1,000 — A stops "
          "after ONE document (it would have read 3 on a per-job count)",
          f"B done={rb['documents_done']} A done={ra['documents_done']} {ra['status']} ledger={used_tokens(lg)}")

    # concurrency: two runs of two jobs on one model, gathered
    await insert_config(conn, T_CON1, D["con"], D["con_p1"], "tokens_per_day", "1000")
    await insert_config(conn, T_CON2, D["con"], D["con_p2"], "tokens_per_day", "1000")
    j1, j2 = fx_job(T_CON1, docs, []), fx_job(T_CON2, docs, [])
    r1 = await DR.create_run(pool, T_CON1, trigger="scheduled")
    r2 = await DR.create_run(pool, T_CON2, trigger="scheduled")
    FAKE_DELAY["s"] = 0.05
    try:
        await asyncio.gather(DR.drive(pool, r1["id"], jobs={T_CON1: j1}), DR.drive(pool, r2["id"], jobs={T_CON2: j2}))
    finally:
        FAKE_DELAY["s"] = 0.0
    a, b, lg = await run_row(conn, r1["id"]), await run_row(conn, r2["id"]), await ledger(conn, D["con"])
    check(used_tokens(lg) <= 1000 and calls_to(D["con"]) <= 3 and a["documents_done"] + b["documents_done"] <= 3
          and int(lg["reserved_tokens"]) == 0 and a["status"] == b["status"] == "stopped_budget",
          "two concurrent runs (asyncio.gather) cannot exceed the budget: total ≤ 1,000, both stop cleanly",
          f"ledger={used_tokens(lg)} calls={calls_to(D['con'])} done={a['documents_done']}+{b['documents_done']} "
          f"{a['status']}/{b['status']}")
    lim = AB.Limit("tokens_per_day", Decimal(1000))
    results = await asyncio.gather(*(AB.reserve(pool, [(D["con_h"], 300, Decimal(0), lim)]) for _ in range(10)),
                                   return_exceptions=True)
    won = [r for r in results if isinstance(r, list)]
    lost = [r for r in results if isinstance(r, AB.BudgetReached)]
    lg = await ledger(conn, D["con_h"])
    check(len(won) == 3 and len(lost) == 7 and int(lg["reserved_tokens"]) == 900,
          "ten simultaneous 300-token reservations against 1,000: exactly 3 win (atomic UPDATE … WHERE … RETURNING)",
          f"won={len(won)} lost={len(lost)} other={[type(r).__name__ for r in results if not isinstance(r, (list, AB.BudgetReached))]} "
          f"reserved={lg['reserved_tokens'] if lg else None}")
    for res in won:
        await AB.release(pool, res[0])

    # all-or-nothing pair
    lim_small = AB.Limit("tokens_per_day", Decimal(100))
    err = None
    try:
        await AB.reserve(pool, [(D["pair1"], 300, Decimal(0), lim), (D["pair2"], 300, Decimal(0), lim_small)])
    except AB.BudgetReached as exc:
        err = exc
    p1 = await ledger(conn, D["pair1"])
    check(err is not None and err.deployment == D["pair2"] and (p1 is None or int(p1["reserved_tokens"]) == 0),
          "a pair reservation is all or nothing: the model with room reserved NOTHING when its partner was out",
          f"raised={err} pair1={p1}")

    # day boundary: yesterday's usage does not count; today's does
    today = await db_today(conn)
    await platform(conn, "INSERT INTO ai_daily_usage (budget_day, deployment, input_tokens) VALUES ($1, $2, 5000)",
                   today - timedelta(days=1), D["day"], fetch="execute")
    await platform(conn, "INSERT INTO ai_daily_usage (budget_day, deployment, input_tokens) VALUES ($1, $2, 5000)",
                   today, D["day2"], fetch="execute")
    scope = AB.BudgetScope(pool=pool, task_key=f"{FXT}day", limits={D["day"]: lim, D["day2"]: lim})
    msgs = [{"role": "user", "content": "x" * PROMPT_CHARS}]
    before = len(CALL_LOG)
    async with AB.activate(scope):
        resp = await PX.chat({"model": D["day"], "messages": msgs, "max_tokens": MAX_T})
        refused = None
        try:
            await PX.chat({"model": D["day2"], "messages": msgs, "max_tokens": MAX_T})
        except AB.BudgetReached as exc:
            refused = exc
    t_row = await ledger(conn, D["day"], await db_today(conn))
    y_row = await ledger(conn, D["day"], today - timedelta(days=1))
    check(resp.status == 200 and t_row is not None and used_tokens(t_row) == 300 and used_tokens(y_row) == 5000
          and t_row["budget_day"] == await db_today(conn),
          "day boundary: 5,000 tokens on the PREVIOUS budget day do not count — today's call ran and was dated "
          "today (the database's UTC day at the call)", f"today={t_row} yesterday={used_tokens(y_row)}")
    check(refused is not None and refused.deployment == D["day2"] and calls_to(D["day2"]) == 0
          and len(CALL_LOG) == before + 1,
          "REPRODUCED the other side: the same 5,000 tokens dated TODAY stop the call before it is made",
          f"{refused}")


# ── the real call paths ─────────────────────────────────────────────────────
async def y_paths(conn):
    section("[Y] Enforcement through the real call paths (cascade, replica, statements) — fake caller only")
    pool = await get_pool()
    lim_big = AB.Limit("tokens_per_day", Decimal(10 ** 9))
    lim_tiny = AB.Limit("tokens_per_day", Decimal(10))

    # replica_terms.read_pair
    async with pool.acquire() as c2:
        specs = RT.build_specs(await RT.load_registry(c2))
    msgs, ph = RT.build_messages(specs, "Fund prospectus text. " * 50, fund_label="ZZAB1", form_type="497K")
    cat = fake_catalog(D["rep1"], D["rep2"])
    ens = {"model_1": D["rep1"], "model_2": D["rep2"], "model_1_budget_kind": "tokens_per_day",
           "model_1_budget_value": Decimal(10 ** 9), "model_2_budget_kind": "tokens_per_day",
           "model_2_budget_value": Decimal(10)}
    err = None
    try:
        await RT.read_pair(ens, [D["rep1"], D["rep2"]], specs, msgs, ph, cat)
    except AB.BudgetReached as exc:
        err = exc
    check(err is not None and err.deployment == D["rep2"] and calls_to(D["rep1"]) == 0 and calls_to(D["rep2"]) == 0,
          "replica terms: Model 2 out of budget → BudgetReached and NEITHER reader is called",
          f"raised={err} calls={calls_to(D['rep1'])}/{calls_to(D['rep2'])}")
    calls = await RT.read_pair({**ens, "model_2_budget_kind": "unlimited", "model_2_budget_value": None},
                               [D["rep1"], D["rep2"]], specs, msgs, ph, cat)
    check(len(calls) == 2 and calls_to(D["rep1"]) == 1 and calls_to(D["rep2"]) == 1,
          "replica terms: within budget → both readers called once", f"{[c.status for c in calls]}")

    # held_away.read_statement_pair (+ the max_tokens ceiling under a limited budget)
    text, as_of = "Account statement. AAPL 10 shares 1,800.00\n" * 20, utc_today()
    s_ens = {"model_1": D["stm1"], "model_2": D["stm2"], "model_1_budget_kind": "tokens_per_day",
             "model_1_budget_value": Decimal(10 ** 9), "model_2_budget_kind": "tokens_per_day",
             "model_2_budget_value": Decimal(10)}
    err = None
    try:
        await HA.read_statement_pair(s_ens, [D["stm1"], D["stm2"]], text, as_of)
    except HA.HeldAwayConflict as exc:
        err = exc
    check(err is not None and "daily model budget reached" in str(err) and calls_to(D["stm1"]) == 0
          and calls_to(D["stm2"]) == 0, "statements: Model 2 out of budget → the import is refused, neither reader "
          "called", f"{err}")
    out = await HA.read_statement_pair({**s_ens, "model_2_budget_kind": "unlimited", "model_2_budget_value": None},
                                       [D["stm1"], D["stm2"]], text, as_of)
    c1 = [c for c in CALL_LOG if c["model"] == D["stm1"]]
    c2 = [c for c in CALL_LOG if c["model"] == D["stm2"]]
    check(len(out) == 2 and len(c1) == 1 and len(c2) == 1 and c1[0]["max_tokens"] == AB.default_max_tokens()
          and c2[0]["has_max_tokens"] is False,
          "statements: under a LIMITED budget the reader gets a max_tokens ceiling (a real bound); an unlimited "
          "model's request is left exactly as it was", f"{c1} {c2}")
    try:
        await HA._refuse_sharing_models(conn, [M_A], ORG)
        ok_plain = True
    except HA.HeldAwayConflict:
        ok_plain = False
    err = None
    try:
        await HA._refuse_sharing_models(conn, [M_SHARE], ORG)
    except HA.HeldAwayConflict as exc:
        err = exc
    check(ok_plain and err is not None and "never sent to a data-sharing model" in str(err),
          "statements: a data-sharing model is refused AT THE CALL too (this path never ran the public-data guard)",
          f"{err}")

    # cascade.run_note
    async with pool.acquire() as c2:
        nspecs = NS.build_field_specs(await NS.load_registry_rows(c2))
    html = ("<html><body><p>Pricing supplement. The notes are linked to the S&amp;P 500 Index. Pricing date: "
            "October 1, 2026. Maturity date: October 1, 2027.</p></body></html>").encode()
    doc = DOCS.document_from_bytes(html, reference_filing_id=str(uuid.uuid4()), form_type="424B2",
                                   filer_name="AIBUDGET1 VERIFY", cik="0", filing_date=utc_today(),
                                   accession_number="0000000000-00-000000")
    slots = CS.EnsembleSlots(model_1=RN.reader_config("model_1", D["cas1"]),
                             model_2=RN.reader_config("model_2", D["cas2"]), jev_route=None, escalation=None)
    ccat = fake_catalog(D["cas1"], D["cas2"])
    scope = AB.BudgetScope(pool=pool, task_key="note_terms_extraction", limits={D["cas1"]: lim_big, D["cas2"]: lim_tiny},
                           catalog=ccat)
    err = None
    async with AB.activate(scope):
        try:
            await CS.run_note(doc, nspecs, slots, catalog=ccat, spend=None)
        except AB.BudgetReached as exc:
            err = exc
    check(err is not None and err.deployment == D["cas2"] and calls_to(D["cas1"]) == 0 and calls_to(D["cas2"]) == 0,
          "cascade: Model 2 out of budget → the note stops before EITHER reader is called (never a one-model reading)",
          f"raised={type(err).__name__ if err else None} calls={calls_to(D['cas1'])}/{calls_to(D['cas2'])}")
    scope = AB.BudgetScope(pool=pool, task_key="note_terms_extraction", limits={D["cas1"]: lim_big, D["cas2"]: lim_big},
                           catalog=ccat)
    async with AB.activate(scope):
        outc = await CS.run_note(doc, nspecs, slots, catalog=ccat, spend=None)
    n1, n2 = calls_to(D["cas1"]), calls_to(D["cas2"])
    lg1 = await ledger(conn, D["cas1"])
    check(n1 >= 1 and n1 == n2 and lg1 is not None and int(lg1["calls"]) == n1,
          "cascade: within budget both readers are called the same number of times, every call in the ledger",
          f"{n1}/{n2} status={outc.status} ledger={lg1['calls'] if lg1 else None}")


# ── the daily run ───────────────────────────────────────────────────────────
async def scheduled_live_rows(conn, task_key) -> int:
    return int(await platform(conn, f"SELECT count(*) FROM ai_daily_runs WHERE task_key = $1 AND trigger = "
                                     f"'scheduled' AND budget_day = {AB.BUDGET_DAY_SQL}", task_key, fetch="fetchval"))


async def y_daily(conn):
    section("[Y] Daily run: one per job per day, resume, statement_positions never scheduled, is_test skipped, "
            "run now")
    pool = await get_pool()
    await insert_config(conn, T_DAILY, D["daily"], D["daily_p"], "tokens_per_day", "1000")
    docs = [f"d{i}" for i in range(1, 7)]
    log: list = []
    job = fx_job(T_DAILY, docs, log)
    stmt_log: list = []
    stmt_job = fx_job("statement_positions", ["s1"], stmt_log)
    jobs = {T_DAILY: job, "statement_positions": stmt_job}
    noon = datetime.now(timezone.utc).replace(hour=12, minute=0)
    sp0 = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = 'statement_positions'",
                             fetch="fetchval"))

    t1 = await DR.daily_tick(pool, now_utc=noon, jobs=jobs)
    j1 = (t1.get("jobs") or {}).get(T_DAILY) or {}
    run1 = await run_row(conn, j1.get("run_id")) if j1.get("run_id") else None
    check(j1.get("action") == "started" and run1 is not None and run1["trigger"] == "scheduled"
          and run1["status"] == "stopped_budget" and run1["documents_done"] == 3
          and [x[2] for x in log] == ["d1", "d2", "d3"],
          "the scheduler step starts today's run, reads in queue order and stops on the budget (3 documents)",
          f"{t1} {[x[2] for x in log]}")
    t_pre = await DR.daily_tick(pool, now_utc=noon.replace(hour=0, minute=1), jobs=jobs)
    check(t_pre.get("action") == "not_due", "before 00:05 UTC the step does nothing (after the budget day starts)",
          f"{t_pre}")
    t2 = await DR.daily_tick(pool, now_utc=noon, jobs=jobs)
    live = await scheduled_live_rows(conn, T_DAILY)
    check(((t2.get("jobs") or {}).get(T_DAILY) or {}).get("action") == "already" and live == 1
          and len(log) == 3, "one run per job per day: a second tick the same day starts nothing (live scheduled "
          "rows for the job today = 1)", f"{t2} live={live}")
    sp1 = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = 'statement_positions'",
                             fetch="fetchval"))
    check(((t1.get("jobs") or {}).get("statement_positions") or {}).get("action") == "never_scheduled"
          and sp1 == sp0 and stmt_log == [],
          "statement_positions is never scheduled, even when handed to the step with work queued", f"{t1} {sp0}→{sp1}")
    check("statement_positions" not in DR.JOBS and set(DR.JOBS) == set(DR.DAILY_TASK_KEYS),
          "the real job registry holds exactly the two public-data jobs", f"{sorted(DR.JOBS)}")

    # a run already going is not started twice
    await insert_config(conn, T_BUSY, D["busy"], D["busy_p"])
    busy = await DR.create_run(pool, T_BUSY, trigger="scheduled")
    err = None
    try:
        await DR.create_run(pool, T_BUSY, trigger="manual", started_by=U_SUPER)
    except DR.RunConflict as exc:
        err = exc
    running = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = $1 AND status = 'running'",
                                 T_BUSY, fetch="fetchval"))
    check(err is not None and running == 1, "a run already going is not started twice (one running row per job)",
          f"{err} running={running}")
    DR.JOBS[T_BUSY] = fx_job(T_BUSY, [], [])
    n0 = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = $1", T_BUSY, fetch="fetchval"))
    st, bd = await api(U_SUPER, "POST", f"{_A}/daily-runs/{T_BUSY}/run")
    n1 = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = $1", T_BUSY, fetch="fetchval"))
    check(st == 409 and n1 == n0, "'run now' while a run is going: 409, nothing started", f"{st} {_detail(bd)}")
    fin = await DR.drive(pool, busy["id"], jobs={T_BUSY: DR.JOBS[T_BUSY]})
    check(fin.get("status") == "finished_queue_empty", "a run with an empty queue finishes 'finished_queue_empty'",
          f"{fin.get('status')}")

    # the next day resumes at the next document (the FIXTURE run's and ledger rows' day moved back one)
    await platform(conn, "UPDATE ai_daily_runs SET budget_day = budget_day - 1 WHERE id = $1::uuid", run1["id"],
                   fetch="execute")
    await platform(conn, f"UPDATE ai_daily_usage SET budget_day = budget_day - 1 WHERE deployment = ANY($1::text[]) "
                         f"AND budget_day = {AB.BUDGET_DAY_SQL}", [D["daily"], D["daily_p"]], fetch="execute")
    t3 = await DR.daily_tick(pool, now_utc=noon, jobs=jobs)
    j3 = (t3.get("jobs") or {}).get(T_DAILY) or {}
    run2 = await run_row(conn, j3.get("run_id")) if j3.get("run_id") else None
    read2 = [x[2] for x in log if run2 and x[1] == str(run2["id"])]
    all_read = [x[2] for x in log]
    check(run2 is not None and run2["id"] != run1["id"] and read2[:1] == ["d4"]
          and run2["status"] == "finished_queue_empty" and run2["documents_done"] == 3,
          "the next day resumes at the next document (d4) and finishes the queue", f"{t3} read={read2}")
    check(sorted(all_read) == sorted(docs) and len(all_read) == len(set(all_read)),
          "no document read twice across the two days", f"{all_read}")

    # run now: same rules, same budget
    job.docs.extend(["d7", "d8"])  # type: ignore[attr-defined]
    DR.JOBS[T_DAILY] = job
    before_calls = calls_to(D["daily"])
    n0 = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = $1", T_DAILY, fetch="fetchval"))
    s_a, b_a = await api(U_ADMIN, "POST", f"{_A}/daily-runs/{T_DAILY}/run")
    n1 = int(await platform(conn, "SELECT count(*) FROM ai_daily_runs WHERE task_key = $1", T_DAILY, fetch="fetchval"))
    s_s, b_s = await api(U_SUPER, "POST", f"{_A}/daily-runs/{T_DAILY}/run")
    rid = ((b_s.get("run") or {}).get("id")) if s_s == 202 else None
    task = DR.BACKGROUND.get(rid) if rid else None
    if task is not None:
        await asyncio.wait_for(task, 120)
    deadline = time.monotonic() + 60
    row = await run_row(conn, rid) if rid else None
    while row is not None and row["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        row = await run_row(conn, rid)
    check(s_a == 403 and n1 == n0 and s_s == 202, "'run now': org_admin 403 (nothing started), super_admin 202 on "
          "the identical request", f"{s_a} {s_s} {_detail(b_a)} {_detail(b_s) if s_s != 202 else ''}")
    check(row is not None and row["trigger"] == "manual" and str(row["started_by"]) == str(U_SUPER)
          and row["status"] == "stopped_budget" and row["documents_done"] == 0
          and calls_to(D["daily"]) == before_calls,
          "'run now' obeys the same budget: today's 900 tokens leave no room for another 300 — stopped, no call made",
          f"{dict(row) if row else None}")
    st, bd = await api(U_SUPER, "POST", f"{_A}/daily-runs/statement_positions/run")
    check(st == 422, "'run now' for statement_positions is refused (not a daily job)", f"{st} {_detail(bd)}")

    s_a, _ = await api(U_ADMIN, "GET", f"{_A}/daily-runs")
    s_s, ov = await api(U_SUPER, "GET", f"{_A}/daily-runs")
    mine = next((r for r in ov.get("rows") or [] if r.get("task_key") == T_DAILY), {})
    today_iso = (await db_today(conn)).isoformat()
    check(s_a == 403 and s_s == 200 and mine.get("today") == today_iso and len(mine.get("history") or []) >= 2
          and any(r.get("stopped_model") == D["daily"] for r in mine.get("today_runs") or [])
          and [s["key"] for s in (ov.get("vocabularies") or {}).get("statuses") or []] == list(DR.STATUS_LABELS),
          "the admin envelope: today's run per job (status, which budget stopped it), the last 30 days, the status "
          "vocabulary; org_admin 403 on the identical request", f"{s_a} {s_s} today={mine.get('today')}")
    s_a, _ = await api(U_ADMIN, "GET", f"{_A}/jobs")
    s_s, jb = await api(U_SUPER, "GET", f"{_A}/jobs")
    keys = [r["task_key"] for r in jb.get("rows") or []]
    sp = next((r for r in jb.get("rows") or [] if r["task_key"] == "statement_positions"), {})
    check(s_a == 403 and s_s == 200 and {"note_terms_extraction", "replica_terms_extraction",
                                         "statement_positions"} <= set(keys)
          and sp.get("daily") is False and sp.get("public_data") is False,
          "GET /admin/ai/jobs: every job with its label and data kind; statement_positions is member data and not "
          "daily; org_admin 403", f"{s_a} {s_s} {keys}")

    # is_test orgs are skipped by the replica queue
    cfg = {"model_1": D["rep1"], "model_2": D["rep2"]}
    off = {i["id"] for i in await DR.queue_items(DR.JOBS["replica_terms_extraction"], pool, limit=10 ** 6, cfg=cfg)}
    on = {i["id"] for i in await DR._replica_queue(pool, limit=10 ** 6, include_test_orgs=True, cfg=cfg)}
    check(S.get("fund") not in off and S.get("fund") in on,
          "is_test orgs are skipped: the fixture org's fund is in the replica queue only with include_test_orgs",
          f"in_default={S.get('fund') in off} in_with_test={S.get('fund') in on}")


async def y_real_rows(conn):
    section("[Y] Real-data rules")
    rows = await platform(conn, "SELECT * FROM ai_ensemble_configs WHERE NOT starts_with(task_key, $1)", FXT)
    VS.when_empty(rows, "every real config's budgets satisfy the rule (unlimited ⇔ NULL value)",
                  lambda r: (r["model_1_budget_kind"] == "unlimited") == (r["model_1_budget_value"] is None)
                  and (r["model_2_budget_kind"] == "unlimited") == (r["model_2_budget_value"] is None),
                  check=check, find=find)
    rows = await platform(conn, "SELECT * FROM ai_ensemble_configs WHERE task_key = 'statement_positions' AND "
                                "(model_1 IN (SELECT model_id FROM platform_model_catalog WHERE public_data_only) OR "
                                "model_2 IN (SELECT model_id FROM platform_model_catalog WHERE public_data_only))")
    check(len(rows) == 0, "no statement_positions config (ever) names a data-sharing model",
          f"{[dict(r) for r in rows][:2]}")


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^\s*ok \d+ - (?:lib: |budget control|history cell|daily runs|daily job card)", out, re.M)
    check(len(mine) >= 11 and "not ok" not in out, "the aibudget1 render tests ran and passed (no budget control or "
          "Run now without can_write === true)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_ensemblesystemone.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    env.pop("AUTHZ_TEST_MODE", None)   # the child sets its own, before it imports main
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_ensemblesystemone.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_ensemblesystemone.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-300:] if not total else ''}")


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
    seconds = time.monotonic() - t0
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
    pre = real = state = None
    saved_jobs = dict(DR.JOBS)
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        state = await ensemble_state(conn)
        print(f"[INFO] active real ensemble configs before: {state or 'none'}")
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("picker", y_picker, conn)
            await run("enforcement", y_enforcement, conn)
            await run("call paths", y_paths, conn)
            await run("daily run", y_daily, conn)
        else:
            check(False, "seed completed", "every fixture section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
        check(all(c["path"] == "/v1/chat/completions" for c in CALL_LOG),
              "every model call went to the FAKE caller (no real model call)", f"{len(CALL_LOG)} fake calls")
    finally:
        section("[Y] Teardown — zero fixture rows; the ensemble state exactly as before; real rows unchanged")
        DR.JOBS.clear()
        DR.JOBS.update(saved_jobs)
        for t in list(DR.BACKGROUND.values()):
            t.cancel()
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (configs, ledger, runs, catalog, System One, fund, org, users)",
                  f"{left}")
            if state is not None:
                after_state = await ensemble_state(conn)
                check(after_state == state, "the real ensemble state is exactly as before (active row per task)",
                      f"{state} → {after_state}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                after = await real_fingerprints(conn)
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
