"""WEEKDAY HOLDINGS REFRESH for Replication Studio (rep1 Task 5).

The same path bulkfetchui3's weekday EDGAR index refresh uses: the Render cron
entrypoint ``workflow_scheduler_tick.py`` (every 5 minutes, UTC) calls
:func:`maybe_refresh_holdings` after the workflow tick and the index refresh —
in the ENTRYPOINT, not inside ``run_scheduler_tick``, so verify scripts that
drive the workflow tick never reach an issuer site or the SEC.

WHEN. Monday-Friday, the first tick at or after 11:00 UTC (issuers publish the
previous close's holdings overnight US time). Weekends and earlier ticks do
nothing.

ONCE PER DAY, BY THE DATABASE. A 'claim' row is INSERTed first into
portfolio.replica_refresh_events, ON CONFLICT DO NOTHING against the partial
unique index replica_refresh_events_day_uq (run_date, trigger 'schedule').
Only the tick that inserted it refreshes; any other tick that day returns
'already'. Ingestion itself is idempotent too: the same file bytes never make a
second snapshot.

WHAT. Every ACTIVE registry entry across every org, grouped by (fund, holdings
source) — the holdings are platform data, fetched once however many orgs hold
the fund. A failure is recorded per fund on the 'finish' row and never stops
the others; nothing raises into the tick after the claim exists.

MODEL MONITORING (rep4). A new LIVE snapshot is also checked against every
fund-built model of the fund (:func:`monitor_models`): over a model's turnover
threshold → a system-opened draft + review task; under → logged only.

REVIEW TASKS. A new snapshot whose diff exceeds the threshold
(``replica_holdings.compute_diff``) — or that failed validation — opens a
review task (member_todos, every manage_replication holder) in each org that
holds the fund, recorded as a 'holdings_review' action + audit row + domain
event (the Task 1e mechanism).

OFF SWITCH. REPLICA_HOLDINGS_AUTO_REFRESH (default on; 0 / false / off / no
turns it off), read per call.
"""
from __future__ import annotations

import os
import socket
from datetime import date, datetime, time, timezone

from services import edgar_fetch
from services import replica_holdings as H
from services import replica_terms as T
from services.database import reset_rls_context, set_rls_context

AUTO_REFRESH_ENV = "REPLICA_HOLDINGS_AUTO_REFRESH"
REFRESH_AFTER_UTC = time(11, 0)
SCHEDULE_MAX_RPS = 2
_OFF = {"0", "false", "off", "no"}
T_EVENTS = "portfolio.replica_refresh_events"


def auto_refresh_enabled() -> bool:
    return (os.environ.get(AUTO_REFRESH_ENV) or "").strip().lower() not in _OFF


def due_day(now_utc: datetime) -> date | None:
    now_utc = now_utc.astimezone(timezone.utc)
    if now_utc.weekday() >= 5 or now_utc.time() < REFRESH_AFTER_UTC:
        return None
    return now_utc.date()


async def _claim(pool, day: date, host: str, trigger: str = "schedule"):
    async with H.platform_tx(pool) as conn:
        return await conn.fetchval(
            f"""INSERT INTO {T_EVENTS} (run_date, trigger_source, phase, status, host)
                VALUES ($1, $2, 'claim', 'running', $3)
                ON CONFLICT (run_date) WHERE trigger_source = 'schedule' AND phase = 'claim' DO NOTHING
                RETURNING id""", day, trigger, host)


async def _finish(pool, claim_id, *, day: date, trigger: str, status: str, results: list, error: str | None,
                  host: str) -> None:
    async with H.platform_tx(pool) as conn:
        await conn.execute(
            f"""INSERT INTO {T_EVENTS} (run_date, trigger_source, phase, claim_id, status, host, results, error)
                VALUES ($1, $2, 'finish', $3::uuid, $4, $5, $6::jsonb, $7)""",
            day, trigger, str(claim_id), status, host, H._dumps(results), error)


async def active_funds(pool) -> list[dict]:
    """Active entries of every org, grouped by (fund, source). Platform read."""
    async with H.platform_tx(pool) as conn:
        rows = await conn.fetch(
            """SELECT global_security_id::text AS fund_id, holdings_source_key, min(ticker) AS ticker,
                      min(sec_series_id) AS series_id,
                      array_agg(DISTINCT org_id::text) AS org_ids
               FROM portfolio.replica_securities
               WHERE status = 'active' AND valid_to IS NULL AND system_to IS NULL
               GROUP BY global_security_id, holdings_source_key ORDER BY min(ticker)""")
    return [dict(r) for r in rows]


async def open_holdings_review(pool, *, fund_id: str, snapshot_id: str, ticker: str, reason: str,
                               org_ids: list[str] | None = None, actor_id=None) -> list[T.ActionRef]:
    """A review task in each org that holds the fund (or only ``org_ids``)."""
    refs: list[T.ActionRef] = []
    async with H.platform_tx(pool) as conn:
        entries = await conn.fetch(
            "SELECT * FROM portfolio.replica_securities WHERE global_security_id = $1::uuid AND status IN "
            "('active', 'draft') AND valid_to IS NULL AND system_to IS NULL "
            "AND ($2::uuid[] IS NULL OR org_id = ANY($2::uuid[]))", fund_id, org_ids)
        for e in entries:
            e = dict(e)
            recipients = await T._approvers(conn, e["org_id"], T.MANAGE_PERMISSION)
            todo_ids = await T.open_todos(
                conn, org_id=e["org_id"], recipients=recipients, source=T.TODO_SOURCE_HOLDINGS,
                related_type="replica_holdings_snapshot", related_id=snapshot_id,
                title=f"Review holdings change: {e['display_name']}", detail=reason,
                action_key=f"/portfolio/replication/{e['id']}?tab=holdings")
            refs.append(await T._record_action(
                conn, org_id=e["org_id"], entry_id=e["id"], version_id=None, action="holdings_review",
                actor_id=actor_id, detail={"snapshot_id": snapshot_id, "reason": reason, "todo_ids": todo_ids}))
    return refs


async def publish_org_actions(pool, refs: list[T.ActionRef]) -> None:
    """Domain events need the org's RLS context; a platform job has none."""
    for ref in refs:
        tokens = set_rls_context(ref.org_id, False)
        try:
            await T.publish_actions(pool, [ref])
        finally:
            reset_rls_context(tokens)


def review_reason(res: H.IngestResult) -> str | None:
    if not res.created:
        return None
    if res.status != "live":
        failed = [k for k, c in (res.validation.get("checks") or {}).items() if not c["ok"]]
        return f"New holdings for {res.ticker} ({res.as_of}) failed validation: {', '.join(failed)}."
    if res.diff_exceeds_threshold:
        d = res.diff
        return (f"Holdings of {res.ticker} changed on {res.as_of}: {len(d.get('added') or [])} added, "
                f"{len(d.get('removed') or [])} removed, turnover {float(d.get('turnover') or 0):.2%}.")
    return None


async def monitor_models(pool, res: H.IngestResult) -> dict | None:
    """rep4: a NEW LIVE snapshot → every fund-built model of the fund is checked
    against it (services.replica_models.monitor_snapshot). A needs_review
    snapshot is never used. Never raises — the outcome (or the error) is
    reported on the refresh result."""
    if not res.created or res.status != "live" or not res.snapshot_id:
        return None
    try:
        from services import replica_models as RM

        out = await RM.monitor_snapshot(pool, fund_id=res.fund_global_security_id, source_key=res.source_key,
                                        snapshot_id=res.snapshot_id)
        return {"models": len(out["models"]), "errors": len(out["errors"]),
                "outcomes": [m.get("outcome") for m in out["models"]]}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:500]}


async def maybe_refresh_holdings(pool, *, now_utc: datetime | None = None, http_factory=None, log=print,
                                 host: str | None = None) -> dict:
    """One tick's holdings step. {"action": "off" | "not_due" | "already" |
    "succeeded" | "partial" | "failed", ...}. Never raises after the claim."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if not auto_refresh_enabled():
        return {"action": "off", "reason": f"{AUTO_REFRESH_ENV} is off"}
    day = due_day(now_utc)
    if day is None:
        return {"action": "not_due", "reason": "weekend or before 11:00 UTC"}
    host = host or socket.gethostname()
    claim_id = await _claim(pool, day, host)
    if claim_id is None:
        return {"action": "already", "day": day.isoformat()}
    results: list[dict] = []
    http = None
    try:
        edgar_fetch.set_rate_limit(SCHEDULE_MAX_RPS)
        http = (http_factory or H.make_http)()
        for f in await active_funds(pool):
            item = {"fund_id": f["fund_id"], "ticker": f["ticker"], "source": f["holdings_source_key"],
                    "orgs": len(f["org_ids"] or [])}
            try:
                async with pool.acquire() as conn:
                    sources = {s["key"]: s for s in await H.list_sources(conn, active_only=True)}
                source = sources.get(f["holdings_source_key"])
                if source is None:
                    raise H.NoHoldingsSource(f"source '{f['holdings_source_key']}' is not active")
                res = await H.ingest(pool, fund_global_security_id=f["fund_id"], ticker=f["ticker"], source=source,
                                     series_id=f["series_id"], http=http)
                item.update(action="created" if res.created else "unchanged", snapshot_id=res.snapshot_id,
                            status=res.status, as_of=res.as_of.isoformat() if res.as_of else None,
                            unresolved=res.unresolved)
                reason = review_reason(res)
                if reason:
                    refs = await open_holdings_review(pool, fund_id=f["fund_id"], snapshot_id=res.snapshot_id,
                                                      ticker=f["ticker"], reason=reason)
                    await publish_org_actions(pool, refs)
                    item["review_tasks"] = len(refs)
                monitored = await monitor_models(pool, res)
                if monitored is not None:
                    item["model_monitor"] = monitored
            except Exception as exc:  # noqa: BLE001 — recorded per fund; the others carry on
                item.update(action="failed", error=f"{type(exc).__name__}: {exc}"[:500])
                log(f"[replica-holdings] {f['ticker']} FAILED: {item['error']}")
            results.append(item)
    except Exception as exc:  # noqa: BLE001 — recorded on the finish row; the tick carries on
        err = f"{type(exc).__name__}: {exc}"[:2000]
        await _finish(pool, claim_id, day=day, trigger="schedule", status="failed", results=results, error=err,
                      host=host)
        return {"action": "failed", "claim_id": str(claim_id), "day": day.isoformat(), "error": err}
    finally:
        if http is not None:
            await http.aclose()
    failed = [r for r in results if r.get("action") == "failed"]
    status = "succeeded" if not failed else ("failed" if len(failed) == len(results) else "partial")
    await _finish(pool, claim_id, day=day, trigger="schedule", status=status, results=results, error=None, host=host)
    return {"action": status, "claim_id": str(claim_id), "day": day.isoformat(), "funds": len(results),
            "failed": len(failed), "results": results}
