"""WEEKDAY AUTOMATIC EDGAR INDEX REFRESH (bulkfetchui3).

The filing index (portfolio.edgar_index_filings) only grew when someone pressed
"Refresh index" or ran ``run_bulk_fetch.py --refresh-index``. This step runs
the SAME ``services.edgar_bulk.refresh_index`` (daily index files from the last
loaded date, inclusive, through yesterday UTC; the active selection policy on
the new rows) once per weekday, in-process, from the Render cron tick
(``workflow_scheduler_tick.py``, every 5 minutes, UTC).

WHEN. Monday-Friday, the first tick at or after 10:30 UTC (~6:30 am ET) — by
then EDGAR has published the previous business day's daily index. Weekends and
earlier ticks do nothing.

ONCE PER DAY, BY THE DATABASE. The run row is INSERTed first, ``ON CONFLICT DO
NOTHING`` against the partial unique index edgar_pipeline_runs_schedule_day_uq
on ``details->>'schedule_date'`` (migration bulkfetchui3_scheduled_index_refresh
.sql). Only the tick that inserted the row refreshes; a second tick, a restart
or an overlapping process the same day inserts nothing and returns 'already'.

FAILURE. Anything raised after the row exists is recorded on it (status
'failed', error) and returned — never raised into the tick, so it cannot stop
the workflow scheduler. The failed row holds only its own day: tomorrow's key
is different, and tomorrow's refresh starts from the last loaded date anyway.

OFF SWITCH. ``EDGAR_INDEX_AUTO_REFRESH`` (default on; 0 / false / off / no
turns it off), read per call like the other EDGAR settings.

The row has target 'render' (the CHECK requires a target for refresh_index,
and it does run on Render) and worker_id 'schedule:<host>'. The runs table
offers no Stop / Requeue for it (edgar_bulk_queue.run_out). Its SEC rate is
capped at SCHEDULE_MAX_RPS so a laptop bulk run at 8/s plus this stays under
the SEC's 10/s.

NO MODEL ANYWHERE.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import date, datetime, time, timedelta, timezone

import httpx

from services import edgar_bulk as eb
from services import edgar_fetch
from services.database import platform_scope

AUTO_REFRESH_ENV = "EDGAR_INDEX_AUTO_REFRESH"
REFRESH_AFTER_UTC = time(10, 30)
SCHEDULE_TARGET = "render"
SCHEDULE_MAX_RPS = 2
_OFF = {"0", "false", "off", "no"}


def auto_refresh_enabled() -> bool:
    return (os.environ.get(AUTO_REFRESH_ENV) or "").strip().lower() not in _OFF


def due_day(now_utc: datetime) -> date | None:
    """The UTC day this tick may refresh for, or None (weekend / before 10:30)."""
    now_utc = now_utc.astimezone(timezone.utc)
    if now_utc.weekday() >= 5 or now_utc.time() < REFRESH_AFTER_UTC:
        return None
    return now_utc.date()


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=edgar_fetch.REQUEST_TIMEOUT, follow_redirects=True)


async def _claim_day(conn, day: date, host: str):
    async with platform_scope(conn):
        return await conn.fetchval(
            """INSERT INTO portfolio.edgar_pipeline_runs
                   (trigger_source, status, stages, fetch_cap, runtime_cap_seconds, run_kind, target, params,
                    queued_at, started_at, heartbeat_at, worker_id, worker_host, details)
               VALUES ('schedule', 'running', ARRAY['discover', 'select'], 0, 3600, 'refresh_index', $1,
                       '{"to": null}'::jsonb, now(), now(), now(), $2, $3, $4::jsonb)
               ON CONFLICT ((details->>'schedule_date'))
                   WHERE trigger_source = 'schedule' AND run_kind = 'refresh_index'
               DO NOTHING
               RETURNING id""",
            SCHEDULE_TARGET, f"schedule:{host}", host,
            json.dumps({"schedule_date": day.isoformat(), "through": (day - timedelta(days=1)).isoformat()}))


async def _finish(conn, run_id, *, status: str, error: str | None, res: dict | None, lines: list[str]) -> None:
    summary = {}
    if res is not None:
        summary = {k: v for k, v in res.items() if k != "new_accessions"}
        summary["new_accessions_count"] = len(res.get("new_accessions") or [])
    summary["log"] = lines[-10:]
    summary["final"] = True
    sel = (res or {}).get("selection") or {}
    async with platform_scope(conn):
        await conn.execute(
            """UPDATE portfolio.edgar_pipeline_runs
                  SET status = $2, finished_at = now(), heartbeat_at = now(), error = $3,
                      stop_reason = $4, discovery_days = $5, discovered_new = $6, selected = $7,
                      not_selected = $8, progress = progress || $9::jsonb,
                      details = details || jsonb_build_object('refresh_index_result', $9::jsonb)
                WHERE id = $1""",
            run_id, status, error, "index refreshed (schedule)" if status == "succeeded" else None,
            int((res or {}).get("days_read") or 0), int((res or {}).get("new_filings") or 0),
            int(sel.get("selected") or 0), int(sel.get("not_selected") or 0),
            json.dumps(summary, default=str))


async def maybe_refresh_index(conn, *, now_utc: datetime | None = None, client_factory=None,
                              log=print, host: str | None = None) -> dict:
    """One tick's index-refresh step. Returns {"action": "off" | "not_due" |
    "already" | "succeeded" | "failed", ...}. Never raises after its run row
    exists."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if not auto_refresh_enabled():
        return {"action": "off", "reason": f"{AUTO_REFRESH_ENV} is off"}
    day = due_day(now_utc)
    if day is None:
        return {"action": "not_due", "reason": "weekend or before 10:30 UTC"}
    host = host or socket.gethostname()
    run_id = await _claim_day(conn, day, host)
    if run_id is None:
        return {"action": "already", "day": day.isoformat()}
    log(f"[edgar-index] scheduled refresh for {day} (run {run_id}): through {day - timedelta(days=1)}")
    lines: list[str] = []
    client = None
    try:
        edgar_fetch.user_agent()                       # loud, recorded failure if EDGAR_USER_AGENT is missing
        edgar_fetch.set_rate_limit(SCHEDULE_MAX_RPS)
        client = (client_factory or _default_client)()
        res = await eb.refresh_index(conn, client, through=day - timedelta(days=1),
                                     out=lambda m: (lines.append(m), log(f"[edgar-index] {m}")))
    except Exception as exc:  # noqa: BLE001 — recorded on the run row; the tick carries on
        err = f"{type(exc).__name__}: {exc}"[:2000]
        log(f"[edgar-index] FAILED: {err}")
        await _finish(conn, run_id, status="failed", error=err, res=None, lines=lines)
        return {"action": "failed", "run_id": str(run_id), "day": day.isoformat(), "error": err}
    finally:
        if client is not None:
            await client.aclose()
    await _finish(conn, run_id, status="succeeded", error=None, res=res, lines=lines)
    return {"action": "succeeded", "run_id": str(run_id), "day": day.isoformat(),
            "new_filings": int(res.get("new_filings") or 0)}
