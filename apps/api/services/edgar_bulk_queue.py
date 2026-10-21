"""BULK DOWNLOAD QUEUE — runs queued from the screen, executed by a worker (bulkfetchui).

WHY A QUEUE
──────────────────────────────────────────────────────────────────────────────
A bulk download takes a day or more; the API is on Render's free plan (it spins
down and kills long jobs). So the API NEVER downloads: ``POST
/admin/edgar/bulk/runs`` writes a 'queued' row to ``portfolio.edgar_pipeline_runs``
(run_kind 'bulk_fetch' or 'refresh_index', target 'laptop' or 'render') and a
separate worker process —

    doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --worker --target laptop

— claims the oldest queued row for its target (``FOR UPDATE SKIP LOCKED``) and
runs it through the SAME ``services.edgar_bulk.Runner`` the CLI uses (no second
implementation): ``run_options_from_params`` turns the row's params into the
CLI's ``RunOptions``, with ``run_id`` set so the Runner adopts the row instead
of inserting its own.

LIFECYCLE
  queued ──claim──▶ running ──▶ succeeded | failed | stopped
  queued ──cancel─▶ cancelled
  running + stop_requested ──▶ (in-flight filings finish, unstarted claims released) stopped
  running + heartbeat older than WORKER_LOST_SECONDS ──▶ shown as "worker lost"
  stopped | failed | worker lost ──requeue─▶ queued   (same row; the claim/resume
        logic means the next execution takes only the rows still 'selected')

ONE RUN AT A TIME, ACROSS ALL WORKERS. The SEC budget is 8 requests/second for
everything we run. Rather than re-divide a live budget between two bulk runs
(the Runner's lease division is computed once, at start), a worker REFUSES to
claim while any queued-kind run is 'running' with a fresh heartbeat. The check
and the claim happen under one transaction-scoped advisory lock, so two workers
racing for two different queued runs cannot both pass the check. The nightly
pipeline job still shares the budget through the Runner's lease division.

FENCING. Every heartbeat updates the run row ``WHERE worker_id = me AND status
= 'running'``; zero rows means the run was requeued (or stopped as lost) and
another worker may own it now, so this worker stops and leaves the row, its
claims and its lease alone.

RENDER. There is no Render background worker today. ``render_worker_state``
reads ``EDGAR_BULK_RENDER_WORKER_SERVICE_ID`` (not set in Doppler as of
2026-10-18) and, only when it is set, makes ONE read-only Render API call
(``GET /v1/services/{id}``). Nothing here creates, deploys or modifies a Render
service. Choosing Render when it is not set up (or its worker has not
heartbeated in RENDER_ALIVE_SECONDS) is refused with a 409 and nothing queued.

LAPTOP INSTRUCTIONS (bulkfetchui2). The laptop worker's start command is the
setting ``EDGAR_BULK_LAPTOP_WORKER_COMMAND``, read per request like the other
settings here. Unset, it defaults to the command that actually works on Joe's
laptop (``LAPTOP_COMMAND``). ``laptop_instructions`` builds the "Run on your
laptop" dialog's steps from it, so no step text lives only in the front end.

NO MODEL ANYWHERE.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import socket
import time
import uuid
from datetime import date, datetime, timezone

import httpx

from services import edgar_bulk as eb
from services import edgar_fetch, edgar_pipeline
from services.database import platform_scope

# ── Configuration ───────────────────────────────────────────────────────────
QUEUED_KINDS = ("bulk_fetch", "refresh_index")
TARGETS = ("laptop", "render")
DEFAULT_TARGET = "laptop"
HEARTBEAT_SECONDS = 30
STOP_POLL_SECONDS = 5
POLL_SECONDS = 15
WORKER_LOST_SECONDS = 120          # a laptop worker is "online" within this; a running run past it is "worker lost"
RENDER_ALIVE_SECONDS = 300
MAX_CONCURRENCY = 16
DEFAULT_CONCURRENCY = 6
RENDER_WORKER_ENV = "EDGAR_BULK_RENDER_WORKER_SERVICE_ID"
RENDER_API = edgar_pipeline.RENDER_API
CLAIM_LOCK_KEY = 0x0B0F_5E12       # pg_advisory_xact_lock key for the claim critical section
INSTITUTIONS_CACHE_SECONDS = 60
SETUP_ANCHOR = "render-worker-setup"

LAPTOP_COMMAND_ENV = "EDGAR_BULK_LAPTOP_WORKER_COMMAND"
# The default: what actually starts the worker on Joe's laptop (WSL). The repo
# root, an explicit Doppler project/config and the repo's venv are all needed —
# a bare `doppler run -- python3 …` from another folder does not work there.
LAPTOP_COMMAND = ("cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- "
                  "venv/bin/python -u apps/api/scripts/run_bulk_fetch.py --worker --target laptop")
LAPTOP_TMUX_SESSION = "bulkworker"
RENDER_COMMAND = "python3 apps/api/scripts/run_bulk_fetch.py --worker --target render"
RENDER_REFUSAL = "Render worker is not set up — run on Laptop, or see setup steps"
RENDER_SETUP_STEPS = [
    "In Render, create a Background Worker (not a web service, not a cron job) from this repository, "
    "root directory apps/api, plan Starter (about $7/month — a worker runs continuously).",
    f"Start command: {RENDER_COMMAND}",
    "Connect the service to Doppler (project hollisworks, config prd) with its OWN sync connection — "
    "a Render service does not receive secrets just because another service in the project does. "
    "It needs DATABASE_URL, EDGAR_USER_AGENT and the R2_* secrets.",
    f"Put the new service's id in Doppler as {RENDER_WORKER_ENV} (the API reads it to detect the worker).",
    "Deploy, then check this screen: Render should show 'online' within a minute of the first heartbeat.",
]

# The screen's document-kind checkboxes -> the CLI's --kinds vocabulary
# (edgar_bulk.CLI_KINDS). Labels are the server's (Rule 1).
KIND_OPTIONS = {
    "final": "Final pricing supplements",
    "product_supplement": "Product supplements",
    "preliminary": "Preliminaries",
    "other": "Other",
}
DEFAULT_KINDS = ["final"]
TARGET_LABELS = {"laptop": "Laptop", "render": "Render"}
STATUS_LABELS = {
    "queued": "Queued", "running": "Running", "succeeded": "Succeeded", "failed": "Failed",
    "stopped": "Stopped", "cancelled": "Cancelled", "worker_lost": "Worker lost",
}
RUN_KIND_LABELS = {"bulk_fetch": "Bulk download", "refresh_index": "Refresh index"}


class BulkQueueError(RuntimeError):
    """A request the queue refuses. ``status`` is the HTTP code the API returns."""

    def __init__(self, message: str, *, status: int = 422, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(v) -> str:
    return json.dumps(v, default=str)


def _as_dict(v) -> dict:
    if v is None:
        return {}
    if isinstance(v, str):
        return json.loads(v)
    return dict(v)


# ═══ Params ═════════════════════════════════════════════════════════════════

def _parse_date(v, name: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise BulkQueueError(f"{name} must be a YYYY-MM-DD date, got {v!r}") from None


async def validate_params(conn, raw: dict) -> tuple[dict, eb.Scope]:
    """Validate the screen's choices and normalise them to the params a worker
    runs. Raises BulkQueueError (422) — or 409 with the last indexed date when
    To is past the index ("Refresh the index first")."""
    raw = dict(raw or {})
    source = await eb.load_source(conn, raw.get("source") or eb.DEFAULT_SOURCE)
    banks = raw.get("banks") or []
    if isinstance(banks, str):
        banks = eb.parse_bank_names(banks)
    if not banks:
        raise BulkQueueError("pick at least one institution")
    try:
        insts = await eb.resolve_institutions(conn, source, [str(b) for b in banks])
    except eb.BulkUsageError as exc:
        raise BulkQueueError(str(exc)) from exc
    last = await eb.last_indexed_date(conn)
    d_from = _parse_date(raw.get("from"), "From")
    d_to = _parse_date(raw.get("to"), "To") if raw.get("to") else last
    if last is None or d_to > last:
        raise BulkQueueError(
            f"Refresh the index first — the index ends on {last}, and To is {d_to}.",
            status=409, extra={"error": "index_behind", "last_indexed_date": str(last), "to": str(d_to)})
    if d_from > d_to:
        raise BulkQueueError(f"From {d_from} is after To {d_to}")
    kinds = raw.get("kinds") or list(DEFAULT_KINDS)
    if isinstance(kinds, str):
        kinds = eb.parse_bank_names(kinds)
    unknown = [k for k in kinds if k not in eb.CLI_KINDS]
    if unknown:
        raise BulkQueueError(f"unknown document kind(s) {unknown}; allowed {sorted(eb.CLI_KINDS)}")
    try:
        concurrency = int(raw.get("concurrency") or DEFAULT_CONCURRENCY)
        max_rps = int(raw.get("max_rps") or eb.DEFAULT_MAX_RPS)
    except (TypeError, ValueError):
        raise BulkQueueError("concurrency and max requests/second must be whole numbers") from None
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise BulkQueueError(f"concurrency must be 1..{MAX_CONCURRENCY}")
    if not 1 <= max_rps <= eb.DEFAULT_MAX_RPS:
        # The screen has no --allow-above-8: 8/s is the ceiling across ALL workers.
        raise BulkQueueError(f"max requests/second must be 1..{eb.DEFAULT_MAX_RPS} (8/s is the ceiling)")
    params = {
        "source": source.name,
        "banks": [i.name for i in insts],
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "kinds": list(dict.fromkeys(kinds)),
        "peek": bool(raw.get("peek", True)),
        "keep_preliminaries": bool(raw.get("keep_preliminaries", False)),
        "concurrency": concurrency,
        "max_rps": max_rps,
    }
    return params, eb.Scope(source, insts, d_from, d_to)


def wants_preliminaries(params: dict) -> bool:
    return params.get("keep_preliminaries") or "preliminary" in (params.get("kinds") or [])


def run_options_from_params(params: dict, run_id, **overrides) -> eb.RunOptions:
    """A queued run's params -> the CLI's RunOptions (the same object
    run_bulk_fetch.py builds from its flags)."""
    base = dict(
        source=params.get("source") or eb.DEFAULT_SOURCE, banks=list(params["banks"]),
        date_from=_parse_date(params["from"], "from"), date_to=_parse_date(params["to"], "to"),
        kinds=list(params.get("kinds") or DEFAULT_KINDS), concurrency=int(params.get("concurrency") or 6),
        max_rps=int(params.get("max_rps") or eb.DEFAULT_MAX_RPS), peek=bool(params.get("peek", True)),
        keep_preliminaries=bool(params.get("keep_preliminaries", False)), trigger_source="manual",
        run_id=run_id,
    )
    base.update(overrides)
    return eb.RunOptions(**base)


# ═══ Preview and institutions (read-only) ═══════════════════════════════════

async def measured_rate(conn) -> dict | None:
    """The newest finished-or-running bulk run's own measured filings/second."""
    async with platform_scope(conn):
        r = await conn.fetchrow(
            """SELECT id, progress FROM portfolio.edgar_pipeline_runs
                WHERE run_kind = 'bulk_fetch' AND (progress ? 'rate_per_second')
                  AND (progress->>'done')::int >= 100
                ORDER BY COALESCE(heartbeat_at, finished_at, requested_at) DESC LIMIT 1""")
    if not r:
        return None
    p = _as_dict(r["progress"])
    return {"run_id": str(r["id"]), "filings_per_second": p.get("rate_per_second")}


async def preview(conn, raw: dict) -> dict:
    """The dry run (edgar_bulk.plan_scope) for the screen. WRITES NOTHING."""
    t0 = time.perf_counter()
    params, scope = await validate_params(conn, raw)
    peek_effective = params["peek"] and not wants_preliminaries(params)
    plan = await eb.plan_scope(conn, scope, rate=params["max_rps"], concurrency=params["concurrency"],
                               peek=peek_effective,
                               prelim_share=0.0 if wants_preliminaries(params) else eb.PRELIMINARY_SHARE)
    counts = plan["counts"]
    years = sorted({y for c in counts.values() for y in c["years"]})
    institutions = []
    for inst in scope.institutions:
        c = counts[inst.name]
        institutions.append({
            "name": inst.name, "total": c["total"],
            "years": {str(y): n for y, n in c["years"].items()},
            "filers": [{"cik": f.cik, "name": f.filer_name, "role": f.filer_role,
                        "count": c["filers"].get(f.cik, 0)} for f in inst.filers],
        })
    est = dict(plan["estimate"])
    m = await measured_rate(conn)
    if m and m.get("filings_per_second"):
        est["measured_filings_per_second"] = m["filings_per_second"]
        est["eta_seconds_at_measured_rate"] = round(plan["total"] / m["filings_per_second"])
    return {
        "params": params, "institutions": institutions, "years": [str(y) for y in years],
        "total": plan["total"], "estimate": est, "peek_effective": peek_effective,
        "last_indexed_date": str(await eb.last_indexed_date(conn)),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    }


_INSTITUTIONS_CACHE: dict = {}


async def institutions(conn, *, source_name: str = eb.DEFAULT_SOURCE, use_cache: bool = True) -> dict:
    """Every institution (issuer group) the source can fetch, with its filer
    entities (name, CIK, role) and SELECTED counts by year. One index-only scan
    (edgar_index_filings_selected_count_idx); cached for 60 s because a live
    bulk run's claims keep dirtying the visibility map."""
    hit = _INSTITUTIONS_CACHE.get(source_name)
    if use_cache and hit and time.monotonic() - hit[0] < INSTITUTIONS_CACHE_SECONDS:
        return hit[1]
    source = await eb.load_source(conn, source_name)
    catalog = await eb.institution_catalog(conn, source)
    ciks = [c for inst in catalog.values() for c in inst.ciks]
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT f.primary_issuer_cik AS cik, extract(year FROM f.filing_date)::int AS y, count(*)::int AS n
                 FROM portfolio.edgar_index_filings f
                WHERE f.pipeline_status = 'selected'
                  AND f.form_type = ANY($2::text[])
                  AND f.primary_issuer_cik = ANY($1::text[])
                GROUP BY 1, 2""",
            ciks, list(source.form_types))
    by_cik: dict[str, dict[int, int]] = {}
    for r in rows:
        by_cik.setdefault(r["cik"], {})[r["y"]] = r["n"]
    out = []
    for name in sorted(catalog, key=str.casefold):
        inst = catalog[name]
        years: dict[int, int] = {}
        filers = []
        for f in inst.filers:
            fy = by_cik.get(f.cik, {})
            for y, n in fy.items():
                years[y] = years.get(y, 0) + n
            filers.append({"cik": f.cik, "name": f.filer_name, "role": f.filer_role,
                           "credit_entity": f.credit_entity,
                           "years": {str(y): n for y, n in sorted(fy.items())}, "total": sum(fy.values())})
        out.append({"name": name, "filers": filers, "years": {str(y): n for y, n in sorted(years.items())},
                    "total": sum(years.values())})
    result = {"source": source.name, "rows": out, "last_indexed_date": str(await eb.last_indexed_date(conn))}
    _INSTITUTIONS_CACHE[source_name] = (time.monotonic(), result)
    return result


# ═══ Workers and Render ═════════════════════════════════════════════════════

async def latest_worker(conn, target: str) -> dict | None:
    async with platform_scope(conn):
        r = await conn.fetchrow(
            """SELECT worker_id, target, host, pid, started_at, heartbeat_at, current_run_id, stopped_at,
                      extract(epoch FROM now() - heartbeat_at)::float AS age_seconds
                 FROM portfolio.edgar_fetch_workers
                WHERE target = $1 AND stopped_at IS NULL
                ORDER BY heartbeat_at DESC LIMIT 1""", target)
    return dict(r) if r else None


def render_http() -> httpx.AsyncClient:
    """The Render API client. Replaced in the verify by a mock that records
    every method — only GETs may ever be made from this module."""
    return httpx.AsyncClient(timeout=20)


_RENDER_CACHE: dict = {}


async def render_service(service_id: str) -> dict:
    """ONE read-only call: GET /v1/services/{id}. Never writes."""
    hit = _RENDER_CACHE.get(service_id)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    key = (os.environ.get("RENDER_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "reason": "RENDER_API_KEY is not set"}
    async with render_http() as http:
        try:
            r = await http.get(f"{RENDER_API}/services/{service_id}",
                               headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        except httpx.HTTPError as exc:
            return {"ok": False, "reason": f"Render API unreachable: {type(exc).__name__}"}
    if r.status_code != 200:
        out = {"ok": False, "reason": f"Render API returned HTTP {r.status_code} for service {service_id}"}
    else:
        body = r.json()
        svc = body.get("service", body)
        stype, suspended = svc.get("type"), svc.get("suspended")
        if stype != "background_worker":
            out = {"ok": False, "reason": f"service {service_id} is a {stype!r}, not a background worker"}
        elif suspended not in (None, "not_suspended"):
            out = {"ok": False, "reason": f"service {service_id} is suspended"}
        else:
            out = {"ok": True, "name": svc.get("name"), "type": stype}
    _RENDER_CACHE[service_id] = (time.monotonic(), out)
    return out


async def render_worker_state(conn) -> dict:
    sid = (os.environ.get(RENDER_WORKER_ENV) or "").strip()
    state = {"configured": False, "online": False, "service_id": sid or None, "reason": None,
             "worker": None, "setup_steps": RENDER_SETUP_STEPS, "setup_anchor": SETUP_ANCHOR,
             "start_command": RENDER_COMMAND}
    if not sid:
        state["reason"] = f"{RENDER_WORKER_ENV} is not set — no Render worker service is configured"
        return state
    svc = await render_service(sid)
    if not svc["ok"]:
        state["reason"] = svc["reason"]
        return state
    state["configured"] = True
    w = await latest_worker(conn, "render")
    state["worker"] = _worker_out(w)
    state["online"] = bool(w and w["age_seconds"] <= RENDER_ALIVE_SECONDS)
    if not state["online"]:
        state["reason"] = f"no Render worker heartbeat in the last {RENDER_ALIVE_SECONDS // 60} minutes"
    return state


def _worker_out(w: dict | None) -> dict | None:
    if not w:
        return None
    return {"worker_id": w["worker_id"], "host": w["host"], "heartbeat_at": w["heartbeat_at"],
            "age_seconds": round(w["age_seconds"]), "current_run_id": w["current_run_id"],
            "started_at": w["started_at"]}


# ═══ Laptop worker instructions (bulkfetchui2) ══════════════════════════════

def laptop_start_command() -> str:
    """The setting, or the working default. Read per call (never cached), like
    every other setting in this module."""
    return (os.environ.get(LAPTOP_COMMAND_ENV) or "").strip() or LAPTOP_COMMAND


def laptop_tmux_command(cmd: str | None = None) -> str:
    """One-liner: a tmux session running the command (shell-quoted, so a
    configured command containing quotes still survives)."""
    return f"tmux new -s {LAPTOP_TMUX_SESSION} {shlex.quote(cmd or laptop_start_command())}"


def laptop_instructions(cmd: str | None = None) -> list[dict]:
    """The "Run on your laptop" dialog's steps, in order. ``section`` is
    'start' (collapsed by the dialog while a worker is already running) or
    'ongoing' (always shown). ``command`` is copyable text; ``keys`` is a
    keystroke, shown but not copied."""
    cmd = cmd or laptop_start_command()
    s = LAPTOP_TMUX_SESSION
    steps = [
        {"key": "open_terminal", "section": "start", "title": "Open the Ubuntu (WSL) terminal",
         "detail": "On the laptop, open Ubuntu from the Start menu. The worker runs inside WSL, not in "
                   "PowerShell or Command Prompt."},
        {"key": "tmux_new", "section": "start", "title": "Start a tmux session",
         "detail": "tmux keeps the worker running after the terminal window is closed. If it says the session "
                   "already exists, a worker may already be running — see \"To check on it later\" below.",
         "command": f"tmux new -s {s}"},
        {"key": "start_worker", "section": "start", "title": "Start the worker",
         "detail": "Paste and run this inside the tmux session. It reads its secrets from Doppler and picks up "
                   "queued runs one at a time.",
         "command": cmd},
        {"key": "detach", "section": "start", "title": "Detach and leave it running",
         "detail": "Press Ctrl-B, then D. The worker keeps running; the terminal can be closed.",
         "keys": "Ctrl-B, then D"},
        {"key": "keep_awake", "section": "ongoing", "title": "Keep the laptop awake",
         "detail": "A run only progresses while the laptop is awake and plugged in. Windows: Settings → System → "
                   "Power → Sleep → \"Never\" when plugged in. A sleeping laptop shows as \"worker lost\" here "
                   f"after {WORKER_LOST_SECONDS // 60} minutes."},
        {"key": "check", "section": "ongoing", "title": "To check on it later",
         "detail": "Reattach to see the worker's output, then detach again with Ctrl-B, then D.",
         "command": f"tmux attach -t {s}"},
        {"key": "stop", "section": "ongoing", "title": "To stop the worker",
         "detail": "Reattach and press Ctrl-C inside tmux; the in-flight filings finish first. Stop it before "
                   "running any verify script — the verify scripts refuse to run while a real worker is "
                   "heartbeating.",
         "keys": "Ctrl-C"},
        {"key": "requeue", "section": "ongoing", "title": "If a run stops or the worker crashes",
         "detail": "Start the worker again and Requeue the run in the Bulk downloads table. It continues where "
                   "it stopped; nothing already downloaded is fetched twice."},
    ]
    for i, step in enumerate(steps, start=1):
        step["n"] = i
    return steps


async def workers_state(conn) -> dict:
    w = await latest_worker(conn, "laptop")
    cmd = laptop_start_command()
    laptop = {"online": bool(w and w["age_seconds"] <= WORKER_LOST_SECONDS), "worker": _worker_out(w),
              "start_command": cmd, "tmux_command": laptop_tmux_command(cmd)}
    return {"laptop": laptop, "render": await render_worker_state(conn),
            "laptop_start_command": cmd, "laptop_instructions": laptop_instructions(cmd),
            "worker_lost_seconds": WORKER_LOST_SECONDS, "render_alive_seconds": RENDER_ALIVE_SECONDS}


# ═══ Queue writes ═══════════════════════════════════════════════════════════

async def _insert_queued(conn, *, run_kind: str, target: str, params: dict, requested_by, fetch_cap: int,
                         stages: list[str], details: dict):
    async with platform_scope(conn):
        return await conn.fetchrow(
            """INSERT INTO portfolio.edgar_pipeline_runs
                   (trigger_source, requested_by, status, stages, fetch_cap, runtime_cap_seconds, run_kind,
                    target, params, queued_at, details)
               VALUES ('manual', $1, 'queued', $2::text[], $3, $4, $5, $6, $7::jsonb, now(), $8::jsonb)
               RETURNING *""",
            requested_by, stages, min(int(fetch_cap), 2_000_000_000), eb.RUNTIME_CAP_SECONDS, run_kind, target,
            _json(params), _json(details))


async def _check_target(conn, target: str) -> dict:
    if target not in TARGETS:
        raise BulkQueueError(f"target must be one of {list(TARGETS)}")
    if target == "render":
        state = await render_worker_state(conn)
        if not (state["configured"] and state["online"]):
            raise BulkQueueError(RENDER_REFUSAL, status=409, extra={
                "error": "render_not_set_up", "reason": state["reason"], "setup_anchor": SETUP_ANCHOR,
                "setup_steps": RENDER_SETUP_STEPS})
        return {"worker_online": True, "start_command": None, "laptop_start_command": laptop_start_command()}
    w = await latest_worker(conn, "laptop")
    online = bool(w and w["age_seconds"] <= WORKER_LOST_SECONDS)
    cmd = laptop_start_command()
    worker = _worker_out(w) if online else None
    return {"worker_online": online, "worker": worker, "worker_host": worker["host"] if worker else None,
            "start_command": None if online else cmd, "tmux_command": None if online else laptop_tmux_command(cmd),
            "laptop_start_command": cmd}


async def queue_bulk_run(conn, raw_params: dict, *, target: str = DEFAULT_TARGET, requested_by=None) -> dict:
    """Validate, check the target, queue. A Render target that is not set up
    is refused BEFORE anything is written."""
    params, scope = await validate_params(conn, raw_params)
    worker = await _check_target(conn, target)
    counts = await eb.scope_counts(conn, scope)
    total = sum(c["total"] for c in counts.values())
    row = await _insert_queued(
        conn, run_kind="bulk_fetch", target=target, params=params, requested_by=requested_by, fetch_cap=total,
        stages=["fetch"], details={"bulk": {"source": params["source"], "institutions": params["banks"],
                                            "scope_total_at_queue": total}})
    return {"run": run_out(dict(row)), **worker}


async def queue_refresh_index(conn, *, to=None, target: str = DEFAULT_TARGET, requested_by=None) -> dict:
    d_to = _parse_date(to, "To") if to else None
    if d_to and d_to > _now().date():
        raise BulkQueueError(f"To {d_to} is in the future")
    worker = await _check_target(conn, target)
    params = {"to": d_to.isoformat() if d_to else None}
    row = await _insert_queued(conn, run_kind="refresh_index", target=target, params=params,
                               requested_by=requested_by, fetch_cap=0, stages=["discover", "select"],
                               details={"refresh_index": {"last_indexed_date_at_queue":
                                                          str(await eb.last_indexed_date(conn))}})
    return {"run": run_out(dict(row)), **worker}


async def _load(conn, run_id) -> dict:
    async with platform_scope(conn):
        r = await conn.fetchrow(
            f"SELECT *, {_LOST_SQL} AS worker_lost FROM portfolio.edgar_pipeline_runs r WHERE id = $1 "
            "AND run_kind = ANY($2::text[])", run_id, list(QUEUED_KINDS))
    if not r:
        raise BulkQueueError(f"no queued-download run {run_id}", status=404)
    return dict(r)


_LOST_SQL = (f"(r.status = 'running' AND (r.heartbeat_at IS NULL OR "
             f"r.heartbeat_at < now() - interval '{WORKER_LOST_SECONDS} seconds'))")


async def request_stop(conn, run_id) -> dict:
    r = await _load(conn, run_id)
    if r["status"] == "queued":
        raise BulkQueueError("this run has not started — cancel it instead", status=409)
    if r["status"] != "running":
        raise BulkQueueError(f"only a running run can be stopped (this one is {r['status']})", status=409)
    async with platform_scope(conn):
        if r["worker_lost"]:
            # Nobody is there to honour the flag: close it now, release its claims.
            await conn.execute(
                """UPDATE portfolio.edgar_pipeline_runs
                      SET status = 'stopped', stop_requested = true, finished_at = now(),
                          stop_reason = 'stopped while the worker was lost (no heartbeat)'
                    WHERE id = $1 AND status = 'running'""", run_id)
            await conn.execute("UPDATE portfolio.edgar_index_filings SET claimed_by = NULL, claimed_until = NULL "
                               "WHERE claimed_by = $1", run_id)
        else:
            await conn.execute("UPDATE portfolio.edgar_pipeline_runs SET stop_requested = true "
                               "WHERE id = $1 AND status = 'running'", run_id)
    return run_out(await _load(conn, run_id))


async def cancel(conn, run_id) -> dict:
    r = await _load(conn, run_id)
    if r["status"] != "queued":
        raise BulkQueueError(f"only a queued run can be cancelled (this one is {r['status']})", status=409)
    async with platform_scope(conn):
        n = await conn.execute(
            """UPDATE portfolio.edgar_pipeline_runs
                  SET status = 'cancelled', finished_at = now(), stop_reason = 'cancelled before it started'
                WHERE id = $1 AND status = 'queued'""", run_id)
    if n.split()[-1] == "0":
        raise BulkQueueError("the run was claimed by a worker a moment ago — stop it instead", status=409)
    return run_out(await _load(conn, run_id))


async def requeue(conn, run_id) -> dict:
    r = await _load(conn, run_id)
    if not (r["status"] in ("stopped", "failed") or r["worker_lost"]):
        raise BulkQueueError(
            f"only a stopped, failed or worker-lost run can be requeued (this one is {r['status']})", status=409)
    async with platform_scope(conn):
        await conn.execute(
            f"""UPDATE portfolio.edgar_pipeline_runs r
                   SET status = 'queued', queued_at = now(), stop_requested = false, worker_id = NULL,
                       worker_host = NULL, heartbeat_at = NULL, finished_at = NULL, error = NULL,
                       stop_reason = NULL,
                       progress = jsonb_build_object('previous', r.progress - 'previous')
                 WHERE id = $1 AND (status IN ('stopped', 'failed') OR {_LOST_SQL})""", run_id)
    return run_out(await _load(conn, run_id))


# ═══ Reads ══════════════════════════════════════════════════════════════════

def run_out(r: dict) -> dict:
    params = _as_dict(r.get("params"))
    progress = _as_dict(r.get("progress"))
    lost = bool(r.get("worker_lost"))
    status = r["status"]
    display = "worker_lost" if lost else status
    total = progress.get("scope_total")
    if total is None:
        total = r.get("fetch_cap")
    return {
        "id": str(r["id"]), "run_kind": r["run_kind"], "status": status, "display_status": display,
        "target": r.get("target"), "params": params, "requested_by": r.get("requested_by"),
        "queued_at": r.get("queued_at"), "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
        "worker_id": r.get("worker_id"), "worker_host": r.get("worker_host"),
        "heartbeat_at": r.get("heartbeat_at"), "stop_requested": bool(r.get("stop_requested")),
        "worker_lost": lost, "progress": progress, "done": progress.get("done", 0), "total": total,
        "rate_per_second": progress.get("rate_per_second"), "eta_seconds": progress.get("eta_seconds"),
        "failed": progress.get("failed", r.get("fetch_failed", 0)), "failures": progress.get("failures", []),
        "stop_reason": r.get("stop_reason"), "error": r.get("error"),
        "actions": {
            "stop": status == "running",
            "cancel": status == "queued",
            "requeue": status in ("stopped", "failed") or lost,
        },
    }


async def list_runs(conn, *, page: int = 1, page_size: int = 25) -> dict:
    page, page_size = max(1, int(page)), max(1, min(int(page_size), 100))
    async with platform_scope(conn):
        total = await conn.fetchval("SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE run_kind = ANY($1::text[])",
                                    list(QUEUED_KINDS))
        rows = await conn.fetch(
            f"""SELECT r.*, {_LOST_SQL} AS worker_lost FROM portfolio.edgar_pipeline_runs r
                 WHERE r.run_kind = ANY($1::text[])
                 ORDER BY COALESCE(r.queued_at, r.requested_at) DESC, r.id DESC
                 LIMIT $2 OFFSET $3""",
            list(QUEUED_KINDS), page_size, (page - 1) * page_size)
    out = [run_out(dict(r)) for r in rows]
    return {"rows": out, "total": int(total), "page": page, "page_size": page_size,
            "active": any(r["status"] in ("queued", "running") for r in out)}


async def get_run(conn, run_id) -> dict:
    return run_out(await _load(conn, run_id))


# ═══ The worker ═════════════════════════════════════════════════════════════

async def claim_next_run(conn, *, target: str, worker_id: str, host: str,
                         restrict_to: list | None = None) -> tuple[dict | None, str | None]:
    """Claim the oldest queued run for ``target``. Returns (row, None) or
    (None, why-not). One run at a time across all workers: the check and the
    claim share one transaction-scoped advisory lock.

    ``restrict_to`` (run ids) is the verify's isolation: a verify worker must
    never claim a REAL queued run. None = every queued run (production)."""
    async with platform_scope(conn):
        await conn.execute("SELECT pg_advisory_xact_lock($1)", CLAIM_LOCK_KEY)
        busy = await conn.fetchrow(
            f"""SELECT id, worker_id, target FROM portfolio.edgar_pipeline_runs
                 WHERE status = 'running' AND run_kind = ANY($1::text[])
                   AND heartbeat_at >= now() - interval '{WORKER_LOST_SECONDS} seconds'
                 LIMIT 1""", list(QUEUED_KINDS))
        if busy:
            return None, (f"run {busy['id']} is running on worker {busy['worker_id']} ({busy['target']}); "
                          "one bulk run at a time keeps the SEC budget at 8/s")
        row = await conn.fetchrow(
            """UPDATE portfolio.edgar_pipeline_runs
                  SET status = 'running', worker_id = $2, worker_host = $3, heartbeat_at = now(),
                      started_at = now(), finished_at = NULL, stop_requested = false, error = NULL,
                      stop_reason = NULL
                WHERE id = (SELECT id FROM portfolio.edgar_pipeline_runs
                             WHERE status = 'queued' AND target = $1 AND run_kind = ANY($4::text[])
                               AND ($5::uuid[] IS NULL OR id = ANY($5::uuid[]))
                             ORDER BY queued_at, id
                             LIMIT 1
                             FOR UPDATE SKIP LOCKED)
            RETURNING *""", target, worker_id, host, list(QUEUED_KINDS),
            None if restrict_to is None else list(restrict_to))
        if row:
            # A requeued run: the previous executor is gone; its claims are this
            # run id's and would otherwise block the resume until they lapse.
            await conn.execute("UPDATE portfolio.edgar_index_filings SET claimed_by = NULL, claimed_until = NULL "
                               "WHERE claimed_by = $1", row["id"])
    return (dict(row), None) if row else (None, None)


class Worker:
    """``run_bulk_fetch.py --worker``. One run at a time; ``once`` = claim at
    most one run, execute it, return."""

    def __init__(self, pool, *, target: str = DEFAULT_TARGET, worker_id: str | None = None,
                 host: str | None = None, client_factory=None, out=print, poll_seconds: float = POLL_SECONDS,
                 heartbeat_seconds: float = HEARTBEAT_SECONDS, stop_poll_seconds: float = STOP_POLL_SECONDS,
                 stop_event: asyncio.Event | None = None, runner_kwargs: dict | None = None,
                 restrict_to: list | None = None):
        if target not in TARGETS:
            raise ValueError(f"--target must be one of {list(TARGETS)}")
        self.pool, self.target, self.out = pool, target, out
        self.host = host or socket.gethostname()
        self.worker_id = worker_id or f"{target}:{self.host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(
            timeout=edgar_fetch.REQUEST_TIMEOUT, follow_redirects=True))
        self.poll_seconds, self.heartbeat_seconds = poll_seconds, heartbeat_seconds
        self.stop_poll_seconds = stop_poll_seconds
        self.stop = stop_event or asyncio.Event()       # Ctrl-C: finish the run as 'stopped', then exit
        self.runner_kwargs = runner_kwargs or {}
        self.restrict_to = restrict_to
        self.current_run_id = None
        self.fenced = False
        self.executed: list[dict] = []

    # ── heartbeat ─────────────────────────────────────────────────────────
    async def register(self) -> None:
        async with self.pool.acquire() as conn, platform_scope(conn):
            await conn.execute(
                """INSERT INTO portfolio.edgar_fetch_workers (worker_id, target, host, pid, details)
                   VALUES ($1, $2, $3, $4, $5::jsonb)
                   ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now(), stopped_at = NULL""",
                self.worker_id, self.target, self.host, os.getpid(), _json({"started_by": "run_bulk_fetch --worker"}))

    async def heartbeat(self) -> bool:
        """Worker row + (when running) the run row. False = fenced: the run row
        is no longer this worker's."""
        async with self.pool.acquire() as conn, platform_scope(conn):
            await conn.execute(
                "UPDATE portfolio.edgar_fetch_workers SET heartbeat_at = now(), current_run_id = $2 "
                "WHERE worker_id = $1", self.worker_id, self.current_run_id)
            if self.current_run_id is None:
                return True
            res = await conn.execute(
                "UPDATE portfolio.edgar_pipeline_runs SET heartbeat_at = now() "
                "WHERE id = $1 AND worker_id = $2 AND status = 'running'", self.current_run_id, self.worker_id)
        return res.split()[-1] != "0"

    async def _stop_requested(self, run_id) -> bool:
        async with self.pool.acquire() as conn, platform_scope(conn):
            return bool(await conn.fetchval(
                "SELECT stop_requested FROM portfolio.edgar_pipeline_runs WHERE id = $1", run_id))

    async def owns_run(self) -> bool:
        if self.fenced or self.current_run_id is None:
            return False
        async with self.pool.acquire() as conn, platform_scope(conn):
            return bool(await conn.fetchval(
                "SELECT 1 FROM portfolio.edgar_pipeline_runs WHERE id = $1 AND worker_id = $2 AND status = 'running'",
                self.current_run_id, self.worker_id))

    async def _watch(self, run_stop: asyncio.Event, run_id) -> None:
        """Heartbeat every heartbeat_seconds; check stop_requested every
        stop_poll_seconds; propagate Ctrl-C."""
        last_hb = time.monotonic()
        while not run_stop.is_set():
            await asyncio.sleep(min(self.stop_poll_seconds, self.heartbeat_seconds))
            if self.stop.is_set():
                run_stop.set()
                break
            if time.monotonic() - last_hb >= self.heartbeat_seconds:
                last_hb = time.monotonic()
                if not await self.heartbeat():
                    self.fenced = True
                    self.out(f"run {run_id}: the run row is no longer this worker's (requeued?) — stopping")
                    run_stop.set()
                    break
            if await self._stop_requested(run_id):
                self.out(f"run {run_id}: stop requested — finishing in-flight filings")
                run_stop.set()
                break

    async def write_progress(self, run_id, progress: dict) -> None:
        async with self.pool.acquire() as conn, platform_scope(conn):
            await conn.execute(
                # keep the requeue's 'previous' snapshot; everything else is this execution's
                "UPDATE portfolio.edgar_pipeline_runs "
                "SET progress = jsonb_strip_nulls(jsonb_build_object('previous', progress->'previous')) || $3::jsonb, "
                "heartbeat_at = now() WHERE id = $1 AND worker_id = $2",
                run_id, self.worker_id, _json(progress))

    async def _close(self, run_id, *, status: str, error: str | None = None, stop_reason: str | None = None,
                     progress: dict | None = None) -> None:
        async with self.pool.acquire() as conn, platform_scope(conn):
            await conn.execute(
                """UPDATE portfolio.edgar_pipeline_runs
                      SET status = $3, finished_at = now(), error = COALESCE($4, error),
                          stop_reason = COALESCE($5, stop_reason),
                          progress = CASE WHEN $6::jsonb IS NULL THEN progress ELSE progress || $6::jsonb END
                    WHERE id = $1 AND worker_id = $2 AND status = 'running'""",
                run_id, self.worker_id, status, error, stop_reason, None if progress is None else _json(progress))

    # ── execution ─────────────────────────────────────────────────────────
    async def execute(self, run: dict) -> str:
        run_id = run["id"]
        self.current_run_id, self.fenced = run_id, False
        await self.heartbeat()
        run_stop = asyncio.Event()
        watcher = asyncio.create_task(self._watch(run_stop, run_id))
        params = _as_dict(run["params"])
        self.out(f"worker {self.worker_id}: claimed {run['run_kind']} run {run_id}")
        try:
            if run["run_kind"] == "refresh_index":
                status = await self._refresh(run_id, params)
            else:
                status = await self._bulk(run_id, params, run_stop)
        except Exception as exc:  # noqa: BLE001 — recorded on the row, the worker keeps serving
            status = "failed"
            if not self.fenced:
                await self._close(run_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000],
                                  stop_reason="the run raised before or after the fetch loop")
            self.out(f"run {run_id} failed: {type(exc).__name__}: {exc}")
        finally:
            run_stop.set()
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.current_run_id = None
            await self.heartbeat()
        self.executed.append({"run_id": run_id, "status": status})
        return status

    async def _bulk(self, run_id, params: dict, run_stop: asyncio.Event) -> str:
        opts = run_options_from_params(params, run_id)
        edgar_fetch.user_agent()   # loud failure before any claim
        client = self.client_factory()

        async def progress(runner):
            await self.write_progress(run_id, eb.progress_snapshot(runner))

        try:
            runner = eb.Runner(self.pool, opts, client=client, out=self.out, stop_event=run_stop,
                               on_progress=progress, owns_run=self.owns_run, **self.runner_kwargs)
            try:
                await runner.run()
            finally:
                if not self.fenced and runner.stats.run_id is not None:
                    await self.write_progress(run_id, eb.progress_snapshot(runner) | {"final": True})
        finally:
            await client.aclose()
        if self.fenced:
            return "fenced"
        async with self.pool.acquire() as conn, platform_scope(conn):
            return await conn.fetchval("SELECT status FROM portfolio.edgar_pipeline_runs WHERE id = $1", run_id)

    async def _refresh(self, run_id, params: dict) -> str:
        edgar_fetch.user_agent()
        edgar_fetch.set_rate_limit(eb.DEFAULT_MAX_RPS)
        client = self.client_factory()
        lines: list[str] = []
        try:
            async with self.pool.acquire() as conn:
                through = _parse_date(params["to"], "to") if params.get("to") else None
                res = await eb.refresh_index(conn, client, through=through, out=lambda m: (lines.append(m),
                                                                                          self.out(m)))
        finally:
            await client.aclose()
        summary = {k: v for k, v in res.items() if k != "new_accessions"}
        summary["new_accessions_count"] = len(res.get("new_accessions") or [])
        summary["log"] = lines[-10:]
        summary["final"] = True
        await self._close(run_id, status="succeeded", stop_reason="index refreshed", progress=summary)
        async with self.pool.acquire() as conn, platform_scope(conn):
            await conn.execute(
                """UPDATE portfolio.edgar_pipeline_runs
                      SET discovery_days = $2, discovered_new = $3, selected = $4, not_selected = $5,
                          details = details || $6::jsonb
                    WHERE id = $1""",
                run_id, int(res.get("days_read") or 0), int(res.get("new_filings") or 0),
                int((res.get("selection") or {}).get("selected") or 0),
                int((res.get("selection") or {}).get("not_selected") or 0),
                _json({"refresh_index_result": summary}))
        return "succeeded"

    async def run(self, *, once: bool = False) -> int:
        """Serve until Ctrl-C (or, with ``once``, after at most one run).
        Returns the number of runs executed."""
        await self.register()
        n = 0
        try:
            while not self.stop.is_set():
                await self.heartbeat()
                async with self.pool.acquire() as conn:
                    run, why = await claim_next_run(conn, target=self.target, worker_id=self.worker_id,
                                                    host=self.host, restrict_to=self.restrict_to)
                if run is not None:
                    await self.execute(run)
                    n += 1
                elif why:
                    self.out(f"worker {self.worker_id}: waiting — {why}")
                if once:
                    break
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            async with self.pool.acquire() as conn, platform_scope(conn):
                await conn.execute("UPDATE portfolio.edgar_fetch_workers SET stopped_at = now(), current_run_id = NULL "
                                   "WHERE worker_id = $1", self.worker_id)
        return n
