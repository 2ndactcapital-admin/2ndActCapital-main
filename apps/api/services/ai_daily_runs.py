"""aibudget1.structural — the daily document-reading run.

Every day, after the budget day starts (midnight UTC), each PUBLIC-DATA
document job with an active ensemble config and a non-empty queue reads its
queue in the job's own order until the queue is empty or a model's daily
budget stops it (services.ai_budget). The next day resumes where it stopped:
a document already read is no longer in the queue, so nothing is read twice
unless the job's own rules re-read it.

THE JOBS (``JOBS``)
──────────────────────────────────────────────────────────────────────────────
  * note_terms_extraction — 424B2 / FWP pricing supplements. Queue: manifest
    rows ``pipeline_status = 'ready_for_extraction'`` (selection.ready_notes)
    that have NO staging row yet, in the cascade's own stratified order
    (round-robin over issuer × year). Reads through runner.run_notes (the
    cascade, its spend cap, its clean stop), one note_extraction_runs row
    ('cascade') per slice.
  * replica_terms_extraction — fund prospectuses for Replication Studio.
    Queue: funds of live registry entries (active or draft) in NON-test orgs
    with no live model reading under the current prompt version from the same
    two models, oldest entry first. Reads through replica_terms.run_extraction
    (the shared, platform-level reading); an org's DRAFT is still built when a
    person refreshes the entry — and then reuses these readings with no call.
  * statement_positions is NEVER here: member statements are member data, read
    only by the member flow that files them (services.held_away).

RUNS (``public.ai_daily_runs``)
──────────────────────────────────────────────────────────────────────────────
One SCHEDULED run per job per budget day (partial unique index) and at most
one RUNNING run per job (partial unique index) — a run already going is never
started twice; "run now" (manual) follows the same rules and the same budget.
The scheduler is a 5-minute Render cron that must exit, so a run proceeds in
SLICES: each tick claims the running row's lease, reads until its time slice
ends, records progress, and releases the lease; the next tick continues. A run
ends ``finished_queue_empty``, ``stopped_budget`` (which model, its budget and
its usage), or ``stopped_error``. A run whose lease holder died is picked up
again once the lease expires.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from services import ai_budget

DAILY_TASK_KEYS = ("note_terms_extraction", "replica_terms_extraction")
NEVER_SCHEDULED = ("statement_positions",)

RUNNING, FINISHED, STOPPED_BUDGET, STOPPED_ERROR = (
    "running", "finished_queue_empty", "stopped_budget", "stopped_error")
STATUS_LABELS = {RUNNING: "Running", FINISHED: "Finished — queue empty",
                 STOPPED_BUDGET: "Stopped — daily budget reached", STOPPED_ERROR: "Stopped — error"}
TRIGGER_LABELS = {"scheduled": "Daily schedule", "manual": "Run now"}

AUTO_ENV = "AI_DAILY_RUNS"                       # '0' turns the scheduled step off
SLICE_ENV = "AI_DAILY_RUN_SLICE_SECONDS"
LEASE_ENV = "AI_DAILY_RUN_LEASE_SECONDS"
START_ENV = "AI_DAILY_RUN_START_UTC"             # 'HH:MM', default 00:05
CAP_ENV = "AI_DAILY_RUN_SPEND_CAP_USD"           # optional per-run dollar cap for the note job
DEFAULT_SLICE, DEFAULT_LEASE, DEFAULT_START = 240, 600, "00:05"
BATCH = 25
HISTORY_DAYS = 30
NOTE_PUBLIC_FORMS = ("424B2", "FWP")             # the note corpus: EDGAR pricing supplements / free-writing prospectuses


def _env_int(name: str, default: int) -> int:
    try:
        v = int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default
    return v if v > 0 else default


def auto_enabled() -> bool:
    return (os.environ.get(AUTO_ENV) or "1").strip().lower() not in ("0", "false", "off", "no")


def slice_seconds() -> int:
    return _env_int(SLICE_ENV, DEFAULT_SLICE)


def lease_seconds() -> int:
    return _env_int(LEASE_ENV, DEFAULT_LEASE)


def start_after() -> tuple[int, int]:
    raw = (os.environ.get(START_ENV) or DEFAULT_START).strip()
    try:
        h, m = (int(x) for x in raw.split(":", 1))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return 0, 5


def spend_cap():
    """The note job's per-run dollar cap: None when ``CAP_ENV`` is unset (no
    dollar cap — the daily model budgets are the limit; stored as NULL).

    dailyops1: this used to return the sentinel 1e9, which cannot fit
    portfolio.note_extraction_runs.spend_cap_usd (numeric(12,6)) — every
    nightly run since 2026-11-05 stopped on a numeric overflow before reading
    a single document. A configured value that is not a number, negative or
    too large to record raises spend.SpendCapConfigError (the run is then
    stopped at its start with that reason) — never a silent fallback."""
    from services.note_extraction.spend import checked_spend_cap

    raw = (os.environ.get(CAP_ENV) or "").strip()
    return None if not raw else float(checked_spend_cap(raw, source=CAP_ENV))


# ── the public-data guard (dailyops1) ───────────────────────────────────────
# The daily jobs run on the data-sharing OpenAI models (public_data_only in
# platform_model_catalog): ONLY public documents may ever reach them. A job is
# run only when its task key is a public-data task (services.model_catalog.
# PUBLIC_DATA_TASK_KEYS — the one source of that rule), checked in drive()
# BEFORE a single item is selected. statement_positions (member statements) is
# not one, and is also never scheduled. Each job's queue reads only the public
# corpus: the note queue keeps only reference filings classified
# 'public_reference' (EDGAR 424B2/FWP); the replica job reads fund
# prospectuses pulled from EDGAR into the platform-only source-document store.
def public_job_refusal(task_key: str) -> str | None:
    from services import model_catalog as MC

    if task_key in NEVER_SCHEDULED or task_key not in MC.PUBLIC_DATA_TASK_KEYS:
        return (f"'{task_key}' is not a public-data job: the daily run reads documents with data-sharing models "
                f"and may run only {sorted(MC.PUBLIC_DATA_TASK_KEYS)} — refused before any item was selected")
    return None


# ── jobs ────────────────────────────────────────────────────────────────────
@dataclass
class BatchResult:
    done: int = 0
    failed: list = field(default_factory=list)          # [{"id", "reason"}]
    budget: ai_budget.BudgetReached | None = None
    error: str | None = None


@dataclass
class Job:
    task_key: str
    label: str
    load_config: Callable[..., Awaitable[dict | None]]              # (pool) -> active config row
    queue: Callable[..., Awaitable[list[dict]]]                     # (pool, *, exclude, limit, include_test_orgs, cfg) -> [{"id", ...}]
    read_batch: Callable[..., Awaitable[BatchResult]]               # (pool, run, cfg, items, stop_when) -> BatchResult
    catalog_loader: Callable | None = None                          # the proxy price list (None = proxy.deployment_catalog)


async def _note_config(pool) -> dict | None:
    from services.note_extraction.cascade import active_ensemble

    async with pool.acquire() as conn:
        return await active_ensemble(conn, "note_terms_extraction")


async def _note_queue(pool, *, exclude=(), limit: int = BATCH, include_test_orgs: bool = False,
                      cfg: dict | None = None) -> list[dict]:
    """The note corpus is global (no org): include_test_orgs and cfg do not apply."""
    from services.note_extraction import selection

    async with ai_budget._platform_tx(pool) as conn:
        ready = await selection.ready_notes(conn)
        staged = {str(r["reference_filing_id"]) for r in await conn.fetch(
            "SELECT DISTINCT reference_filing_id FROM portfolio.note_extraction_staging")}
        # dailyops1 public-data guard: only the public EDGAR corpus, never anything else.
        public = {str(r["id"]) for r in await conn.fetch(
            "SELECT id FROM portfolio.reference_filings WHERE id = ANY($1::uuid[]) "
            "AND retention_classification = 'public_reference' AND form_type = ANY($2::text[])",
            [n.reference_filing_id for n in ready], list(NOTE_PUBLIC_FORMS))}
    skip = staged | {str(x) for x in exclude}
    todo = [n for n in ready if n.reference_filing_id not in skip and n.reference_filing_id in public]
    return [{"id": n.reference_filing_id, "issuer": n.issuer} for n in selection.stratify(todo, limit)]


async def _note_read(pool, run: dict, cfg: dict, items: list[dict], stop_when) -> BatchResult:
    """One slice of the cascade on its own raw connection (run_notes keeps one
    connection for a whole batch; the RLS pool would hold it in one transaction)."""
    import asyncpg

    from services.note_extraction import cascade, participants, proxy, runner, schema
    from services.note_extraction.spend import SpendCapConfigError, UnpricedModelError

    try:
        cap = spend_cap()                      # refused at the start, before any connection or document
    except SpendCapConfigError as exc:
        return BatchResult(error=f"refused at start: {exc}")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0, ssl="require", timeout=30)
    try:
        specs = schema.build_field_specs(await schema.load_registry_rows(conn))
        schema.assert_no_shared_protection_field(specs)
        catalog = await asyncio.to_thread(proxy.deployment_catalog)
        parts = await participants.load_participants(conn)
        groups = await participants.load_issuer_groups(conn)
        slots = cascade.EnsembleSlots(
            model_1=runner.reader_config("model_1", cfg["model_1"]),
            model_2=runner.reader_config("model_2", cfg["model_2"]),
            jev_route=cfg.get("jev_route"), escalation=None, ensemble_config_id=str(cfg["id"]))
        try:
            summary = await runner.run_notes(
                conn, [i["id"] for i in items], specs, slots, catalog=catalog, spend_cap_usd=cap,
                run_kind="cascade", config={"daily_run_id": str(run["id"]), "trigger": run["trigger"]},
                participant_rows=parts, issuer_groups=groups, stop_when=stop_when)
        except UnpricedModelError as exc:
            return BatchResult(error=str(exc))
    finally:
        await conn.close()
    res = BatchResult(done=summary.notes_done, failed=[{"id": f, "reason": "load failed"} for f in summary.failed_ids])
    if isinstance(summary.stop_exc, ai_budget.BudgetReached):
        res.budget = summary.stop_exc
    elif summary.stop_exc is not None:
        res.error = f"spending cap: {summary.stop_exc}"
    return res


async def _replica_config(pool) -> dict | None:
    from services import replica_terms

    async with pool.acquire() as conn:
        return await replica_terms.active_ensemble(conn)


async def _replica_queue(pool, *, exclude=(), limit: int = BATCH, include_test_orgs: bool = False,
                         cfg: dict | None = None) -> list[dict]:
    from services import orgs as ORGS
    from services import replica_terms as T

    cfg = cfg if cfg is not None else await _replica_config(pool)
    m1, m2 = (cfg or {}).get("model_1"), (cfg or {}).get("model_2")
    async with ai_budget._platform_tx(pool) as conn:
        rows = await conn.fetch(
            f"""SELECT s.global_security_id::text AS id, min(s.ticker) AS ticker, min(s.sec_series_id) AS series_id,
                       min(s.instrument_class) AS instrument_class, min(s.created_at) AS first_at
                  FROM portfolio.replica_securities s
                 WHERE s.status IN ('active', 'draft') AND s.valid_to IS NULL AND s.system_to IS NULL
                   AND s.global_security_id IS NOT NULL AND s.ticker IS NOT NULL
                   AND ($1::boolean OR {ORGS.not_test_org_sql("s.org_id")})
                   AND NOT (s.global_security_id::text = ANY($2::text[]))
                   AND NOT EXISTS (
                       SELECT 1 FROM {T.T_READ} r JOIN public.ai_ensemble_configs e ON e.id = r.ensemble_config_id
                        WHERE r.fund_global_security_id = s.global_security_id AND r.prompt_version = $3
                          AND r.system_to IS NULL AND r.source IN ('model_1', 'model_2')
                          AND e.model_1 = $4 AND e.model_2 = $5)
                 GROUP BY s.global_security_id
                 ORDER BY min(s.created_at), s.global_security_id
                 LIMIT $6""",
            include_test_orgs, [str(x) for x in exclude], T.PROMPT_VERSION, m1, m2, limit)
    return [dict(r) for r in rows]


async def _replica_read(pool, run: dict, cfg: dict, items: list[dict], stop_when) -> BatchResult:
    from services import replica_holdings as H
    from services import replica_terms as T

    res = BatchResult()
    http = H.make_http()
    try:
        for it in items:
            if stop_when():
                break
            r = await T.run_extraction(pool, fund_id=it["id"], ticker=it["ticker"], series_id=it.get("series_id"),
                                       http=http, instrument_class=it.get("instrument_class"))
            if r.budget_reached is not None:
                res.budget = r.budget_reached
                break
            if r.status in ("extracted", "reused"):
                res.done += 1
            else:
                res.failed.append({"id": it["id"], "reason": (r.reason or r.status)[:300]})
    finally:
        await http.aclose()
    return res


JOBS: dict[str, Job] = {
    "note_terms_extraction": Job("note_terms_extraction", "Structured-note terms", _note_config, _note_queue,
                                 _note_read),
    "replica_terms_extraction": Job("replica_terms_extraction", "Fund prospectus terms", _replica_config,
                                    _replica_queue, _replica_read),
}


async def queue_items(job: Job, pool, *, exclude=(), limit: int = BATCH, include_test_orgs: bool = False,
                      cfg: dict | None = None) -> list[dict]:
    return await job.queue(pool, exclude=exclude, limit=limit, include_test_orgs=include_test_orgs, cfg=cfg)


# ── run rows ────────────────────────────────────────────────────────────────
def run_out(r) -> dict:
    d = dict(r)
    for k in ("id", "ensemble_config_id", "started_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("started_at", "finished_at", "heartbeat_at", "lease_until"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    if d.get("budget_day") is not None:
        d["budget_day"] = d["budget_day"].isoformat()
    for k in ("stopped_budget_value", "stopped_used"):
        if d.get(k) is not None:
            d[k] = str(Decimal(d[k]).normalize())
    for k in ("failed_items", "usage"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["status_label"] = STATUS_LABELS.get(d.get("status"), d.get("status"))
    d["trigger_label"] = TRIGGER_LABELS.get(d.get("trigger"), d.get("trigger"))
    return d


class RunConflict(RuntimeError):
    """A run for this job is already going."""


async def create_run(pool, task_key: str, *, trigger: str, started_by=None) -> dict | None:
    """Insert a RUNNING row. A scheduled run that already exists today returns
    None; a run already going raises RunConflict (the partial unique indexes
    decide — never a read-then-write race)."""
    import asyncpg

    try:
        async with ai_budget._platform_tx(pool) as conn:
            if trigger == "scheduled":
                row = await conn.fetchrow(
                    f"""INSERT INTO public.ai_daily_runs (task_key, budget_day, trigger, started_by)
                        VALUES ($1, {ai_budget.BUDGET_DAY_SQL}, 'scheduled', NULL)
                        ON CONFLICT (task_key, budget_day) WHERE trigger = 'scheduled' DO NOTHING
                        RETURNING *""", task_key)
            else:
                row = await conn.fetchrow(
                    f"""INSERT INTO public.ai_daily_runs (task_key, budget_day, trigger, started_by)
                        VALUES ($1, {ai_budget.BUDGET_DAY_SQL}, 'manual', $2::uuid) RETURNING *""",
                    task_key, str(started_by) if started_by else None)
    except asyncpg.UniqueViolationError as exc:
        raise RunConflict(f"a run of '{task_key}' is already going — it is not started twice") from exc
    return run_out(row) if row else None


async def _update(pool, run_id, sql_set: str, *args) -> dict | None:
    async with ai_budget._platform_tx(pool) as conn:
        row = await conn.fetchrow(f"UPDATE public.ai_daily_runs SET {sql_set} WHERE id = $1::uuid RETURNING *",
                                  str(run_id), *args)
    return run_out(row) if row else None


async def claim(pool, run_id) -> dict | None:
    async with ai_budget._platform_tx(pool) as conn:
        row = await conn.fetchrow(
            """UPDATE public.ai_daily_runs SET lease_until = now() + make_interval(secs => $2::integer),
                      heartbeat_at = now()
                WHERE id = $1::uuid AND status = 'running' AND (lease_until IS NULL OR lease_until < now())
            RETURNING *""", str(run_id), lease_seconds())
    return run_out(row) if row else None


def _merge_usage(base: dict, add: dict) -> dict:
    out = {k: dict(v) for k, v in (base or {}).items()}
    for dep, u in (add or {}).items():
        cur = out.setdefault(dep, {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0", "calls": 0})
        cur["input_tokens"] = int(cur.get("input_tokens", 0)) + int(u["input_tokens"])
        cur["output_tokens"] = int(cur.get("output_tokens", 0)) + int(u["output_tokens"])
        cur["cost_usd"] = str(Decimal(str(cur.get("cost_usd", "0"))) + Decimal(str(u["cost_usd"])))
        cur["calls"] = int(cur.get("calls", 0)) + int(u["calls"])
    return out


async def _progress(pool, run_id, *, done: int, failed: list, usage: dict) -> None:
    await _update(pool, run_id,
                  "documents_done = $2, documents_failed = $3, failed_items = $4::jsonb, usage = $5::jsonb, "
                  "heartbeat_at = now(), lease_until = now() + make_interval(secs => $6::integer)",
                  done, len(failed), json.dumps(failed), json.dumps(usage), lease_seconds())


async def _finish(pool, run_id, status: str, *, detail: str | None = None,
                  budget: ai_budget.BudgetReached | None = None) -> dict | None:
    return await _update(
        pool, run_id,
        "status = $2, finished_at = now(), lease_until = NULL, heartbeat_at = now(), stop_detail = $3, "
        "stopped_model = $4, stopped_budget_kind = $5, stopped_budget_value = $6::numeric, stopped_used = $7::numeric",
        status, (detail or "")[:2000] or None,
        budget.deployment if budget else None, budget.kind if budget else None,
        budget.budget if budget else None, budget.used if budget else None)


# ── driving one slice ───────────────────────────────────────────────────────
async def drive(pool, run_id, *, jobs: dict[str, Job] | None = None, deadline: float | None = None,
                include_test_orgs: bool = False, clock=time.monotonic) -> dict:
    """Run one slice of a RUNNING run: claim its lease, read in queue order
    until the queue is empty, a budget stops it, or the slice ends. Returns the
    run row as it stands (``status`` 'running' = continues next slice)."""
    jobs = JOBS if jobs is None else jobs
    run = await claim(pool, run_id)
    if run is None:
        return {"id": str(run_id), "action": "busy_or_finished"}
    job = jobs.get(run["task_key"])
    if deadline is None:
        deadline = clock() + slice_seconds()
    if job is None:
        return await _finish(pool, run_id, STOPPED_ERROR, detail=f"'{run['task_key']}' is not a daily job") or run
    refusal = public_job_refusal(job.task_key)
    if refusal is not None:
        return await _finish(pool, run_id, STOPPED_ERROR, detail=refusal) or run
    done, failed, base_usage = int(run["documents_done"]), list(run["failed_items"] or []), run["usage"] or {}
    scope = None
    try:
        cfg = await job.load_config(pool)
        if cfg is None:
            return await _finish(pool, run_id, STOPPED_ERROR,
                                 detail=f"no active ensemble config for '{run['task_key']}'") or run
        await _update(pool, run_id, "ensemble_config_id = $2::uuid, model_1 = $3, model_2 = $4",
                      str(cfg["id"]), cfg["model_1"], cfg["model_2"])
        catalog = None
        if any(lim.limited and lim.kind == ai_budget.USD_PER_DAY for lim in ai_budget.limits_from_config(cfg).values()):
            from services.note_extraction import proxy
            catalog = await asyncio.to_thread(job.catalog_loader or proxy.deployment_catalog)
        async with ai_budget.job_scope(job.task_key, cfg, catalog=catalog, pool=pool) as scope:
            while True:
                if clock() >= deadline:
                    break
                items = await queue_items(job, pool, exclude=[f["id"] for f in failed], limit=BATCH,
                                          include_test_orgs=include_test_orgs, cfg=cfg)
                if not items:
                    await _progress(pool, run_id, done=done, failed=failed,
                                    usage=_merge_usage(base_usage, scope.usage_json()))
                    return await _finish(pool, run_id, FINISHED, detail=None) or run
                res = await job.read_batch(pool, run, cfg, items, lambda: clock() >= deadline)
                done += res.done
                failed += res.failed
                await _progress(pool, run_id, done=done, failed=failed,
                                usage=_merge_usage(base_usage, scope.usage_json()))
                if res.budget is not None:
                    return await _finish(pool, run_id, STOPPED_BUDGET, detail=str(res.budget),
                                         budget=res.budget) or run
                if res.error is not None:
                    return await _finish(pool, run_id, STOPPED_ERROR, detail=res.error) or run
    except ai_budget.BudgetReached as exc:
        await _progress(pool, run_id, done=done, failed=failed,
                        usage=_merge_usage(base_usage, scope.usage_json() if scope else {}))
        return await _finish(pool, run_id, STOPPED_BUDGET, detail=str(exc), budget=exc) or run
    except Exception as exc:  # noqa: BLE001 — recorded on the run, never silent
        try:
            await _progress(pool, run_id, done=done, failed=failed,
                            usage=_merge_usage(base_usage, scope.usage_json() if scope else {}))
        except Exception:  # noqa: BLE001
            pass
        return await _finish(pool, run_id, STOPPED_ERROR, detail=f"{type(exc).__name__}: {exc}") or run
    # the slice ended with the queue not empty: release the lease, continue next slice
    return await _update(pool, run_id, "lease_until = NULL, heartbeat_at = now()") or run


async def drive_to_end(pool, run_id, *, jobs: dict[str, Job] | None = None, include_test_orgs: bool = False,
                       max_slices: int = 100000) -> dict:
    """'Run now' in the API process: slice after slice until the run ends."""
    out: dict = {}
    for _ in range(max_slices):
        out = await drive(pool, run_id, jobs=jobs, include_test_orgs=include_test_orgs)
        if out.get("action") == "busy_or_finished" or out.get("status") != RUNNING:
            return out
    return out


BACKGROUND: dict[str, asyncio.Task] = {}


async def run_now(pool, task_key: str, *, started_by, jobs: dict[str, Job] | None = None,
                  include_test_orgs: bool = False) -> dict:
    """Start a manual run (same rules, same budget) and drive it in the
    background of this process. RunConflict when one is already going."""
    jobs = JOBS if jobs is None else jobs
    if task_key not in jobs:
        raise KeyError(task_key)
    run = await create_run(pool, task_key, trigger="manual", started_by=started_by)
    task = asyncio.create_task(drive_to_end(pool, run["id"], jobs=jobs, include_test_orgs=include_test_orgs))
    BACKGROUND[run["id"]] = task
    task.add_done_callback(lambda _t, rid=run["id"]: BACKGROUND.pop(rid, None) if BACKGROUND.get(rid) is _t else None)
    return run


# ── the scheduler step ──────────────────────────────────────────────────────
async def daily_tick(pool, *, now_utc: datetime | None = None, jobs: dict[str, Job] | None = None,
                     include_test_orgs: bool = False, budget_seconds: int | None = None,
                     clock=time.monotonic) -> dict:
    """One scheduler tick's daily-run step. For each daily job: continue a
    running run, else start today's scheduled run when the job has an active
    config and a non-empty queue. Never raises; per-job outcomes returned."""
    jobs = JOBS if jobs is None else jobs
    now_utc = now_utc or datetime.now(timezone.utc)
    if not auto_enabled():
        return {"action": "off", "reason": f"{AUTO_ENV} is off"}
    h, m = start_after()
    if (now_utc.hour, now_utc.minute) < (h, m):
        return {"action": "not_due", "reason": f"before {h:02d}:{m:02d} UTC"}
    deadline = clock() + (budget_seconds or slice_seconds())
    out: dict = {"action": "ran", "jobs": {}}
    for task_key, job in jobs.items():
        if task_key in NEVER_SCHEDULED:
            out["jobs"][task_key] = {"action": "never_scheduled"}
            continue
        if public_job_refusal(task_key) is not None:
            out["jobs"][task_key] = {"action": "not_public_data"}
            continue
        try:
            async with ai_budget._platform_tx(pool) as conn:
                running = await conn.fetchrow("SELECT id FROM public.ai_daily_runs WHERE task_key = $1 "
                                              "AND status = 'running'", task_key)
                today = await conn.fetchval(
                    f"SELECT id FROM public.ai_daily_runs WHERE task_key = $1 AND trigger = 'scheduled' "
                    f"AND budget_day = {ai_budget.BUDGET_DAY_SQL}", task_key)
            if running is not None:
                if clock() >= deadline:
                    out["jobs"][task_key] = {"action": "deferred", "run_id": str(running["id"])}
                    continue
                r = await drive(pool, running["id"], jobs=jobs, deadline=deadline,
                                include_test_orgs=include_test_orgs, clock=clock)
                out["jobs"][task_key] = {"action": "continued", "run_id": str(running["id"]),
                                         "status": r.get("status") or r.get("action")}
                continue
            if today is not None:
                out["jobs"][task_key] = {"action": "already", "run_id": str(today)}
                continue
            cfg = await job.load_config(pool)
            if cfg is None:
                out["jobs"][task_key] = {"action": "no_config"}
                continue
            if not await queue_items(job, pool, limit=1, include_test_orgs=include_test_orgs, cfg=cfg):
                out["jobs"][task_key] = {"action": "queue_empty"}
                continue
            run = await create_run(pool, task_key, trigger="scheduled")
            if run is None:
                out["jobs"][task_key] = {"action": "already"}
                continue
            if clock() >= deadline:
                out["jobs"][task_key] = {"action": "started", "run_id": run["id"], "status": RUNNING}
                continue
            r = await drive(pool, run["id"], jobs=jobs, deadline=deadline, include_test_orgs=include_test_orgs,
                            clock=clock)
            out["jobs"][task_key] = {"action": "started", "run_id": run["id"], "status": r.get("status")}
        except RunConflict:
            out["jobs"][task_key] = {"action": "already_running"}
        except Exception as exc:  # noqa: BLE001
            out["jobs"][task_key] = {"action": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return out


# ── the admin screen's read ─────────────────────────────────────────────────
async def overview(pool, *, jobs: dict[str, Job] | None = None) -> list[dict]:
    """Per daily job: label, active config with budgets, queue size (first
    page), today's runs and the last 30 days."""
    from services.ai_ensembles import task_info

    jobs = JOBS if jobs is None else jobs
    out = []
    for task_key, job in jobs.items():
        cfg = await job.load_config(pool)
        async with ai_budget._platform_tx(pool) as conn:
            rows = await conn.fetch(
                f"SELECT * FROM public.ai_daily_runs WHERE task_key = $1 AND budget_day > "
                f"{ai_budget.BUDGET_DAY_SQL} - {HISTORY_DAYS} ORDER BY budget_day DESC, started_at DESC", task_key)
            today = await conn.fetchval(f"SELECT {ai_budget.BUDGET_DAY_SQL}")
            usage = await ai_budget.usage_today(conn, [cfg["model_1"], cfg["model_2"]] if cfg else [])
        runs = [run_out(r) for r in rows]
        try:
            pending = len(await queue_items(job, pool, limit=200, cfg=cfg)) if cfg else None
            queue_error = None
        except Exception as exc:  # noqa: BLE001
            pending, queue_error = None, f"{type(exc).__name__}: {exc}"
        active = None
        if cfg:
            from services.ai_ensembles import _row_out
            active = {k: v for k, v in _row_out({k: cfg[k] for k in cfg if k != "jev_route"}).items()
                      if k in ("id", "task_key", "model_1", "model_2", "model_1_budget_kind", "model_1_budget_value",
                               "model_2_budget_kind", "model_2_budget_value", "activated_at")}
        out.append({**task_info(task_key), "job_label": job.label, "active": active,
                    "queue_pending": pending, "queue_pending_capped_at": 200, "queue_error": queue_error,
                    "today": today.isoformat(), "usage_today": usage,
                    "today_runs": [r for r in runs if r["budget_day"] == today.isoformat()],
                    "history": runs})
    return out
