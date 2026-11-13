"""NIGHTLY DOWNLOAD OF NEW SELECTED FILINGS (edgarops1).

After the day's scheduled index refresh (services.edgar_index_schedule) has
SUCCEEDED, the Render cron tick puts the day's list of NEW selected filings —
up to the daily cap — into the EXISTING bulk queue (services.edgar_bulk_queue)
as ONE queued ``bulk_fetch`` run, tagged as scheduled for that day. The queue
is the hand-off:

  * Render can run it (R2 credentials and a valid bucket and EDGAR_USER_AGENT on
    the scheduler; the SEC answered today's refresh) → target 'render'; every
    tick claims it with the queue's own ``claim_next_run`` and works it through
    the queue's own ``Worker`` (the bulk ``Runner`` — fetch → peek / classify →
    store → status; nothing forked) for at most ``tick_seconds()``, then puts it
    back to 'queued' until no listed filing is left.
  * Render cannot → target 'laptop', status 'queued', with the reason on the run
    (``details.auto_download.waiting``). ``run_bulk_fetch.py --worker`` claims it
    whenever it runs. If Render later can, the tick re-targets it to 'render'.

"NEW" (docs/EDGAROPS1_NOTES.md §2): ``pipeline_status = 'selected'`` AND
``loaded_at >= the switch-on cutoff`` (first indexed since the step was switched
on, by ANY refresh — the 06:00 nightly pipeline usually indexes first), in the
source's catalog CIKs and form types, and not already listed by a scheduled
download run that is still queued or running. Oldest first-indexed first, so a
carried-over filing goes ahead of the next day's.

CAP. ``EDGAR_AUTO_DOWNLOAD_DAILY_CAP`` (default 2000). The rest carries over to
the next day ("cap reached — N carried over", recorded on the day's run).

ONCE PER DAY, BY THE DATABASE: the run row is INSERTed ``ON CONFLICT DO
NOTHING`` against ``edgar_pipeline_runs_auto_download_day_uq`` on
``details->'auto_download'->>'date'`` (docs/edgarops1_part1.sql). A day with
nothing new still gets its row (closed 'succeeded', "nothing new").

FAILURE. Never raised into the tick: a failure while queueing is recorded as the
day's run row (status 'failed'); a failure while working is recorded by the
Runner/Worker on the run itself. Neither blocks tomorrow, whose key differs and
whose list takes today's leftovers (a failed run is not "still queued").

OFF SWITCH. ``EDGAR_AUTO_DOWNLOAD`` (default ON; 0 / false / off / no turns it
off), read per call like the other EDGAR settings. Off = no new list and no work
on Render; an already-queued run stays claimable by the laptop worker.

NO MODEL ANYWHERE.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from services import edgar_bulk as eb
from services import edgar_bulk_queue as bq
from services import edgar_fetch, edgar_pipeline
from services import edgar_index_schedule as sched
from services.database import platform_scope

ENABLED_ENV = "EDGAR_AUTO_DOWNLOAD"
CAP_ENV = "EDGAR_AUTO_DOWNLOAD_DAILY_CAP"
TICK_SECONDS_ENV = "EDGAR_AUTO_DOWNLOAD_TICK_SECONDS"
SINCE_ENV = "EDGAR_AUTO_DOWNLOAD_SINCE"
DEFAULT_CAP = 2000
DEFAULT_TICK_SECONDS = 120
CONCURRENCY = 2
KINDS = ["final"]
R2_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
LAPTOP_HINT = "a laptop worker can run it (run_bulk_fetch.py --worker --target laptop)"

# Status sets (docs/EDGAROPS1_NOTES.md §6) — one place, shared with the run summaries and the institutions table.
TO_FETCH_STATUSES = ("selected",)
DOWNLOADED_STATUSES = ("fetched", "ready_for_extraction", "extraction_submitted", "extracted", "needs_review",
                       "extraction_failed")
SKIPPED_STATUSES = ("not_pricing_supplement", "prefilter_skipped")
FAILED_STATUSES = ("fetch_failed",)

_OFF = {"0", "false", "off", "no"}



def auto_run_sql(alias: str = "") -> str:
    """The scheduled download runs (the partial unique index's own predicate)."""
    a = f"{alias}." if alias else ""
    return (f"{a}trigger_source = 'schedule' AND {a}run_kind = 'bulk_fetch' "
            f"AND ({a}details->'auto_download'->>'date') IS NOT NULL")


AUTO_RUN_SQL = auto_run_sql()


def enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() not in _OFF


def _env_int(name: str, default: int, lo: int = 1) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v >= lo else default


def daily_cap() -> int:
    return _env_int(CAP_ENV, DEFAULT_CAP)


def tick_seconds() -> int:
    return _env_int(TICK_SECONDS_ENV, DEFAULT_TICK_SECONDS)


def since_override() -> date | None:
    raw = (os.environ.get(SINCE_ENV) or "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def render_capability() -> tuple[bool, str | None]:
    """Can THIS process (the Render cron tick) download and store? Reads the
    environment only — no network. (The SEC being reachable is proven by the
    day's refresh having succeeded, which gates the queueing.)"""
    missing = [k for k in R2_ENV if not (os.environ.get(k) or "").strip()]
    if missing:
        return False, (f"waiting — Render cannot store files: {', '.join(missing)} missing on the scheduler; "
                       f"{LAPTOP_HINT}")
    try:
        edgar_pipeline.r2_bucket()
    except edgar_pipeline.EdgarPipelineConfigError as exc:
        return False, f"waiting — Render cannot store files: {exc} {LAPTOP_HINT}"
    try:
        edgar_fetch.user_agent()
    except edgar_fetch.EdgarConfigError:
        return False, f"waiting — Render cannot reach the SEC: EDGAR_USER_AGENT missing on the scheduler; {LAPTOP_HINT}"
    return True, None


def _json(v) -> str:
    return json.dumps(v, default=str)


def _as_dict(v) -> dict:
    if v is None:
        return {}
    return json.loads(v) if isinstance(v, str) else dict(v)


# ═══ The list ═══════════════════════════════════════════════════════════════

async def refresh_state(conn, day: date) -> str | None:
    """The day's scheduled index refresh status ('succeeded' / 'failed' /
    'running'), or None when it has not run."""
    async with platform_scope(conn):
        return await conn.fetchval(
            """SELECT status FROM portfolio.edgar_pipeline_runs
                WHERE trigger_source = 'schedule' AND run_kind = 'refresh_index'
                  AND details->>'schedule_date' = $1""", day.isoformat())


async def new_since(conn, day: date) -> tuple[datetime, str]:
    """The switch-on cutoff for ``day``: EDGAR_AUTO_DOWNLOAD_SINCE, else the
    earliest ``new_since`` recorded by a scheduled download run on or before
    ``day``, else ``day`` itself at 00:00 UTC (the first day)."""
    o = since_override()
    if o is not None:
        return datetime.combine(o, datetime.min.time(), tzinfo=timezone.utc), f"{SINCE_ENV}={o}"
    async with platform_scope(conn):
        v = await conn.fetchval(
            f"""SELECT min((details->'auto_download'->>'new_since')::timestamptz)
                  FROM portfolio.edgar_pipeline_runs
                 WHERE {AUTO_RUN_SQL} AND (details->'auto_download'->>'date')::date <= $1""", day)
    if v is not None:
        return v, "the first scheduled download run"
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc), "switched on today"


NEW_SELECTED_SQL = f"""
SELECT f.accession_number, f.filing_date, f.primary_issuer_cik, f.loaded_at
  FROM portfolio.edgar_index_filings f
 WHERE f.pipeline_status = 'selected'
   AND f.loaded_at >= $1
   AND f.form_type = ANY($2::text[])
   AND f.primary_issuer_cik = ANY($3::text[])
   AND NOT EXISTS (
         SELECT 1 FROM portfolio.edgar_pipeline_runs r
          WHERE {auto_run_sql('r')}
            AND r.status IN ('queued', 'running')
            AND r.params->'accessions' ? f.accession_number)
 ORDER BY f.loaded_at, f.filing_date, f.accession_number
"""


async def new_selected(conn, *, cutoff: datetime, form_types: list[str], ciks: list[str]) -> list[dict]:
    async with platform_scope(conn):
        return [dict(r) for r in await conn.fetch(NEW_SELECTED_SQL, cutoff, list(form_types), list(ciks))]


def _banks_for(rows: list[dict], catalog: dict) -> list[str]:
    ciks = {r["primary_issuer_cik"] for r in rows}
    return [name for name in sorted(catalog, key=str.casefold) if ciks & set(catalog[name].ciks)]


async def _insert_day(conn, *, day: date, status: str, target: str, params: dict, auto: dict, n: int,
                      stop_reason: str | None = None, error: str | None = None):
    closed = status in ("succeeded", "failed")
    async with platform_scope(conn):
        return await conn.fetchrow(
            f"""INSERT INTO portfolio.edgar_pipeline_runs
                   (trigger_source, status, stages, fetch_cap, runtime_cap_seconds, run_kind, target, params,
                    queued_at, finished_at, stop_reason, error, details)
               VALUES ('schedule', $1, ARRAY['fetch'], $2, $3, 'bulk_fetch', $4, $5::jsonb, now(),
                       CASE WHEN $6 THEN now() END, $7, $8, $9::jsonb)
               ON CONFLICT ((details->'auto_download'->>'date'))
                   WHERE {AUTO_RUN_SQL}
               DO NOTHING
               RETURNING *""",
            status, int(n), eb.RUNTIME_CAP_SECONDS, target, _json(params), closed, stop_reason, error,
            _json({"auto_download": auto | {"date": day.isoformat()}}))


async def queue_day(conn, day: date, *, source_name: str = eb.DEFAULT_SOURCE, cap: int | None = None,
                    ciks: list[str] | None = None, capability: tuple[bool, str | None] | None = None,
                    log=print) -> dict:
    """Queue the day's list as ONE bulk_fetch run. Returns {"action": "queued" |
    "nothing_new" | "already" | "failed", ...}. ``ciks`` narrows the catalog
    (the verify's isolation); production passes None."""
    cap = daily_cap() if cap is None else int(cap)
    can, why = capability or render_capability()
    try:
        source = await eb.load_source(conn, source_name)
        catalog = await eb.institution_catalog(conn, source)
        all_ciks = [c for inst in catalog.values() for c in inst.ciks]
        use_ciks = all_ciks if ciks is None else [c for c in all_ciks if c in set(ciks)]
        cutoff, cutoff_how = await new_since(conn, day)
        rows = await new_selected(conn, cutoff=cutoff, form_types=list(source.form_types), ciks=use_ciks)
        listed = rows[:cap]
        carried = len(rows) - len(listed)
        auto = {"new_since": cutoff.isoformat(), "new_since_from": cutoff_how, "eligible": len(rows),
                "listed": len(listed), "cap": cap, "cap_reached": carried > 0, "carried_over": carried,
                "waiting": None if can else why, "queued_for": "render" if can else "laptop"}
        if not listed:
            row = await _insert_day(conn, day=day, status="succeeded", target="render",
                                    params={"source": source.name, "banks": [], "accessions": []},
                                    auto=auto | {"summary": empty_summary()}, n=0,
                                    stop_reason="nothing new to download")
            if row is None:
                return {"action": "already", "day": day.isoformat()}
            log(f"[edgar-download] {day}: nothing new to download (new since {cutoff.isoformat()})")
            return {"action": "nothing_new", "run_id": str(row["id"]), "day": day.isoformat()}
        params = {"source": source.name, "banks": _banks_for(listed, catalog),
                  "from": min(r["filing_date"] for r in listed).isoformat(),
                  "to": max(r["filing_date"] for r in listed).isoformat(),
                  "kinds": list(KINDS), "peek": True, "keep_preliminaries": False,
                  "concurrency": CONCURRENCY, "max_rps": sched.SCHEDULE_MAX_RPS,
                  "accessions": [r["accession_number"] for r in listed]}
        row = await _insert_day(conn, day=day, status="queued", target="render" if can else "laptop",
                                params=params, auto=auto, n=len(listed))
    except Exception as exc:  # noqa: BLE001 — recorded as the day's run; the tick carries on
        err = f"{type(exc).__name__}: {exc}"[:2000]
        log(f"[edgar-download] {day}: FAILED to queue: {err}")
        try:
            row = await _insert_day(conn, day=day, status="failed", target="render",
                                    params={"source": source_name, "banks": [], "accessions": []},
                                    auto={"error_stage": "queue"}, n=0, stop_reason="could not build the list",
                                    error=err)
        except Exception as exc2:  # noqa: BLE001
            return {"action": "failed", "day": day.isoformat(), "error": err,
                    "record_error": f"{type(exc2).__name__}: {exc2}"[:500]}
        return {"action": "failed" if row is not None else "already", "day": day.isoformat(), "error": err,
                "run_id": str(row["id"]) if row is not None else None}
    if row is None:
        return {"action": "already", "day": day.isoformat()}
    log(f"[edgar-download] {day}: queued {len(listed)} new selected filing(s) as run {row['id']}"
        f"{f'; cap reached — {carried} carried over' if carried else ''}"
        f"{'' if can else f'; {why}'}")
    return {"action": "queued", "run_id": str(row["id"]), "day": day.isoformat(), "listed": len(listed),
            "carried_over": carried, "target": "render" if can else "laptop", "waiting": None if can else why}


# ═══ Summaries ══════════════════════════════════════════════════════════════

def empty_summary() -> dict:
    return {"requested": 0, "fetched": 0, "stored": 0, "skipped": {}, "skipped_total": 0, "failed": 0,
            "still_to_fetch": 0}


async def list_outcome(conn, accessions: list[str]) -> dict:
    """Where the listed filings stand now (the index is the truth): requested,
    fetched (Downloaded), stored (of fetched), skipped by reason, failed, still
    to fetch. Cumulative across ticks and workers."""
    if not accessions:
        return empty_summary()
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT f.pipeline_status AS s, count(*)::int AS n,
                      count(*) FILTER (WHERE r.r2_key IS NOT NULL OR r.text_r2_key IS NOT NULL)::int AS stored
                 FROM portfolio.edgar_index_filings f
                 LEFT JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
                WHERE f.accession_number = ANY($1::text[])
                GROUP BY 1""", list(accessions))
    by = {r["s"]: (r["n"], r["stored"]) for r in rows}
    skipped = {s: by[s][0] for s in SKIPPED_STATUSES if by.get(s, (0, 0))[0]}
    return {
        "requested": len(accessions),
        "fetched": sum(by.get(s, (0, 0))[0] for s in DOWNLOADED_STATUSES),
        "stored": sum(by.get(s, (0, 0))[1] for s in DOWNLOADED_STATUSES),
        "skipped": skipped, "skipped_total": sum(skipped.values()),
        "failed": sum(by.get(s, (0, 0))[0] for s in FAILED_STATUSES),
        "still_to_fetch": sum(by.get(s, (0, 0))[0] for s in TO_FETCH_STATUSES),
    }


def is_auto_run(run: dict) -> bool:
    return (run.get("trigger_source") == "schedule" and run.get("run_kind") == "bulk_fetch"
            and bool(_as_dict(run.get("details")).get("auto_download")))


async def write_summary(conn, run_id, *, working_seconds: float = 0.0) -> dict:
    """Recompute the scheduled run's summary from the index and store it on
    ``details.auto_download.summary`` (plus accumulated working seconds and the
    first start). Called by the tick after each chunk and by the queue Worker
    when any worker finishes a scheduled run."""
    async with platform_scope(conn):
        r = await conn.fetchrow("SELECT params, details, started_at FROM portfolio.edgar_pipeline_runs "
                                "WHERE id = $1", run_id)
    params, details = _as_dict(r["params"]), _as_dict(r["details"])
    auto = details.get("auto_download") or {}
    summary = await list_outcome(conn, params.get("accessions") or [])
    worked = float(auto.get("working_seconds") or 0) + float(working_seconds)
    first = auto.get("first_started_at") or (r["started_at"].isoformat() if r["started_at"] else None)
    async with platform_scope(conn):
        await conn.execute(
            """UPDATE portfolio.edgar_pipeline_runs
                  SET details = jsonb_set(details, '{auto_download}',
                                          COALESCE(details->'auto_download', '{}'::jsonb) || $2::jsonb)
                WHERE id = $1""",
            run_id, _json({"summary": summary, "working_seconds": round(worked, 1), "first_started_at": first}))
    return summary


# ═══ Working the queue from the tick ════════════════════════════════════════

async def _queued_auto_ids(conn) -> list:
    async with platform_scope(conn):
        return [r["id"] for r in await conn.fetch(
            f"SELECT id FROM portfolio.edgar_pipeline_runs WHERE {AUTO_RUN_SQL} AND status = 'queued' "
            "ORDER BY queued_at, id")]


async def _mark_waiting(conn, ids: list, why: str | None, *, retarget: str | None = None) -> None:
    if not ids:
        return
    async with platform_scope(conn):
        await conn.execute(
            """UPDATE portfolio.edgar_pipeline_runs
                  SET target = COALESCE($3, target),
                      details = jsonb_set(details, '{auto_download,waiting}', to_jsonb($2::text))
                WHERE id = ANY($1::uuid[]) AND status = 'queued'""",
            list(ids), why, retarget)


async def work_tick(pool, *, deadline_seconds: float | None = None, client_factory=None, host: str | None = None,
                    capability: tuple[bool, str | None] | None = None, restrict_to: list | None = None,
                    runner_kwargs: dict | None = None, log=print) -> dict:
    """One bounded chunk of the oldest queued scheduled download, on Render.
    Returns {"action": "idle" | "waiting" | "busy" | "worked", ...}. Never raises
    for a run's own failure (recorded on the run by the Runner / Worker)."""
    can, why = capability or render_capability()
    async with pool.acquire() as conn:
        ids = await _queued_auto_ids(conn)
        if restrict_to is not None:
            ids = [i for i in ids if i in set(restrict_to)]
        if not ids:
            return {"action": "idle"}
        if not can:
            await _mark_waiting(conn, ids, why)
            return {"action": "waiting", "runs": len(ids), "reason": why}
        await _mark_waiting(conn, ids, None, retarget="render")
    host = host or socket.gethostname()
    worker = bq.Worker(pool, target="render", host=host, worker_id=f"schedule:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}",
                       client_factory=client_factory, out=lambda m: log(f"[edgar-download] {m}"), restrict_to=ids,
                       runner_kwargs=runner_kwargs)
    async with pool.acquire() as conn:
        run, busy = await bq.claim_next_run(conn, target="render", worker_id=worker.worker_id, host=worker.host,
                                            restrict_to=ids)
    if run is None:
        return {"action": "busy", "reason": busy} if busy else {"action": "idle"}
    bound = float(deadline_seconds if deadline_seconds is not None else tick_seconds())
    loop = asyncio.get_running_loop()
    timer = loop.call_later(bound, worker.stop.set)
    t0 = time.monotonic()
    try:
        status = await worker.execute(run)
    finally:
        timer.cancel()
    worked = time.monotonic() - t0
    deadline_hit = worker.stop.is_set()
    run_id = run["id"]
    async with pool.acquire() as conn:
        # the Worker already wrote the run's summary (and its working seconds) when the Runner returned
        async with platform_scope(conn):
            params = _as_dict(await conn.fetchval("SELECT params FROM portfolio.edgar_pipeline_runs WHERE id = $1",
                                                  run_id))
        summary = await list_outcome(conn, params.get("accessions") or [])
        final = await _settle(conn, run_id, status=status, deadline_hit=deadline_hit, summary=summary)
    log(f"[edgar-download] run {run_id}: chunk of {worked:.0f}s → {final}; {summary}")
    return {"action": "worked", "run_id": str(run_id), "status": final, "summary": summary,
            "seconds": round(worked, 1)}


async def _settle(conn, run_id, *, status: str, deadline_hit: bool, summary: dict) -> str:
    """After a chunk: still filings left and the chunk only hit its time bound →
    back to 'queued' for the next tick (or a laptop worker); nothing left after a
    deadline stop → 'succeeded'. A failure stays 'failed' (shown, requeueable)."""
    if status != "stopped" or not deadline_hit:
        return status
    left = summary["still_to_fetch"]
    async with platform_scope(conn):
        if left > 0:
            await conn.execute(
                """UPDATE portfolio.edgar_pipeline_runs
                      SET status = 'queued', worker_id = NULL, worker_host = NULL,
                          heartbeat_at = NULL, finished_at = NULL, stop_requested = false,
                          stop_reason = $2
                    WHERE id = $1 AND status = 'stopped'""",
                run_id, f"continuing on the next tick — {left} of {summary['requested']} still to fetch")
            return "queued"
        await conn.execute(
            """UPDATE portfolio.edgar_pipeline_runs SET status = 'succeeded', stop_reason = 'list done'
                WHERE id = $1 AND status = 'stopped'""", run_id)
    return "succeeded"


# ═══ The tick's step ════════════════════════════════════════════════════════

async def maybe_auto_download(conn, pool, *, now_utc: datetime | None = None, client_factory=None,
                              host: str | None = None, capability: tuple[bool, str | None] | None = None,
                              deadline_seconds: float | None = None, ciks: list[str] | None = None,
                              restrict_to: list | None = None, runner_kwargs: dict | None = None,
                              log=print) -> dict:
    """The tick's download step: (1) once the day's scheduled index refresh has
    succeeded, queue the day's list (once per day); (2) work one bounded chunk of
    the oldest queued scheduled download if Render can. Never raises."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if not enabled():
        return {"action": "off", "reason": f"{ENABLED_ENV} is off"}
    capability = capability or render_capability()
    out: dict = {"action": "ran"}
    day = sched.due_day(now_utc)
    try:
        if day is None:
            out["queue"] = {"action": "not_due"}
        else:
            state = await refresh_state(conn, day)
            if state != "succeeded":
                out["queue"] = {"action": "refresh_not_succeeded", "refresh": state}
            else:
                out["queue"] = await queue_day(conn, day, capability=capability, ciks=ciks, log=log)
    except Exception as exc:  # noqa: BLE001
        out["queue"] = {"action": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000]}
    try:
        out["work"] = await work_tick(pool, deadline_seconds=deadline_seconds, client_factory=client_factory,
                                      host=host, capability=capability, restrict_to=restrict_to,
                                      runner_kwargs=runner_kwargs, log=log)
    except Exception as exc:  # noqa: BLE001 — the tick and the scheduler carry on
        out["work"] = {"action": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000]}
    if out["queue"].get("action") == "failed" or out["work"].get("action") == "failed":
        out["action"] = "failed"
    return out


async def waiting_summary(conn) -> dict:
    """Scheduled download runs still queued, and how many of their listed
    filings are still to fetch — the screen's "N scheduled runs waiting"."""
    async with platform_scope(conn):
        r = await conn.fetchrow(
            f"""SELECT count(DISTINCT r.id)::int AS runs,
                       count(f.accession_number) FILTER (WHERE f.pipeline_status = 'selected')::int AS filings
                  FROM portfolio.edgar_pipeline_runs r
                  LEFT JOIN LATERAL jsonb_array_elements_text(r.params->'accessions') a(acc) ON true
                  LEFT JOIN portfolio.edgar_index_filings f ON f.accession_number = a.acc
                 WHERE {auto_run_sql('r')}
                   AND r.status = 'queued'""")
    return {"runs": int(r["runs"] or 0), "filings": int(r["filings"] or 0)}
