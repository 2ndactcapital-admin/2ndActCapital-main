"""Render `type: cron` entrypoint — one scheduler tick, then exit.

    python workflow_scheduler_tick.py

Deliberately minimal. This is NOT the API process: it imports no router, starts
no HTTP server, and serves no request. It opens two connections, runs exactly
one pass of ``services.workflow_scheduler.run_scheduler_tick``, prints what it
did, closes them, and exits.

EXIT CODES — these are what Render surfaces as the run's status, so they are
chosen to make a failing scheduler visible in the dashboard rather than in a
log nobody reads:

    0   the tick completed; every trigger examined was fired or skipped
        cleanly (including "nothing was due", the normal case)
    1   the tick completed but at least one trigger errored — an unusable cron
        expression, an unknown timezone, a definition with no current version,
        or a claimed occurrence whose run would not start
    2   the tick could not run at all (no DATABASE_URL, no connection)

THE ACTION REGISTRY. ``services.assistant_actions.register_all()`` was, until
this sprint, called from exactly one place — ``main.py``'s FastAPI ``startup``
hook. This process never starts FastAPI. An unregistered registry does not fail
loudly here: the engine resolves every Service Task's action to ``None`` and
marks the step *completed*, so a scheduled workflow would report success having
invoked nothing. ``run_scheduler_tick`` therefore registers the actions itself,
once per tick, rather than relying on an import side effect that this process
does not have.

WHY TWO CONNECTIONS. The scan and the atomic claim run on a separate, plain
asyncpg connection: the scheduler is a platform-level process and must see
every org's triggers, which an org-scoped RLS context would prevent. Every
query this connection issues (``services.workflow_scheduler
.load_due_candidates`` / ``_workflow_in_progress`` / ``_claim``, and
``services.workflow_todos.dismiss_orphaned_run_alerts``) goes through
``services.database.platform_scope`` — the SAME ``OR is_super_admin``
carve-out every RLS policy on those tables already carries for exactly this
kind of platform job, never a bypass role or a different DB user.

That carve-out is set ``SET LOCAL``, fresh, INSIDE each individual query's own
transaction — never once for this connection's whole lifetime. An earlier
version of this fix set it once, session-wide, right after connecting, on the
theory that a connection used by nobody else for its whole life has nothing to
leak into. That was wrong, and testing (not review) is what caught it: under
Supabase's transaction-mode pooler this connection's physical backend was
observably the SAME backend handed to the ordinary RLS-aware pool below (see
``get_pool()``) for firing an individual trigger's run — and the moment THAT
pool transaction committed, the pooler reset the shared backend's session
GUCs, silently wiping this connection's session-wide setting mid-tick with no
error. See ``platform_scope``'s docstring for the full mechanism.

The runs themselves go through the ordinary RLS-aware application pool, with
each run created under its OWN trigger's org context — so a trigger in one org
can never start a run in another.

SCHEDULE. Declared in render.yaml as every 5 minutes, UTC. Render's cron
schedules are UTC-only and cannot be made timezone-aware, which is exactly why
per-org local time is resolved inside services/workflow_schedule.py instead.
The 5-minute cadence and the module's 60-minute lookback are a matched pair:
the lookback must comfortably exceed the tick interval, or an occurrence
falling between two ticks would be missed.

WEEKDAY EDGAR INDEX REFRESH (bulkfetchui3). After the workflow tick, the same
process runs ``services.edgar_index_schedule.maybe_refresh_index``: once per
weekday, the first tick at or after 10:30 UTC refreshes the EDGAR filing index
through yesterday and records an edgar_pipeline_runs row (trigger_source
'schedule'). It lives HERE, in the entrypoint, not inside
``run_scheduler_tick`` — verify scripts call that function directly and must
never reach the SEC. Once-per-day is enforced by a unique index, a failure is
recorded on the run row (exit code 1, so Render shows it), and
EDGAR_INDEX_AUTO_REFRESH=0 turns it off.

WEEKDAY REPLICA HOLDINGS REFRESH (rep1). Then, on the same path,
``services.replica_schedule.maybe_refresh_holdings``: once per weekday, the
first tick at or after 11:00 UTC ingests holdings for every active Replication
Studio registry entry (once per day by a unique index; idempotent per file;
failures recorded per fund on portfolio.replica_refresh_events, exit code 1
when the step itself failed). REPLICA_HOLDINGS_AUTO_REFRESH=0 turns it off.

DEAL-ROOM ACCESS (dealroom3). ``services.deal_room_control.access_tick`` sweeps
every NON-test org with live deal access: granted → expired the day after the
access end date, the "expiring" notice, granted → paused on a lapsed
eligibility, paused → granted once it passes. Its own try, like the others.

EXECUTE-BY HANDLING (rep2). Last, ``services.execution.expire_tick`` applies
each overdue open execution instruction's own if-not-filled policy (expire /
carry to today / ask a person) — the one automatic step in the instruction
lifecycle, always logged with no actor. It changes statuses only; nothing is
ever sent to a custodian. Its own step, so a problem here stops nothing above.

REBALANCE PROPOSALS (rep5). After that, ``services.rebalance_proposals.
proposals_tick``: per org with an active account-model link, in that org's RLS
context — expire stale proposals, mark executed the approved ones whose
instructions are all done, re-sweep recent 'model.version_approved' events
(idempotent), and the daily drift / cash-flow scan on each account's own
valuation (after the holdings refresh above). It only ever writes PROPOSALS;
nothing trades. Its own step.

TEST ORGS (rep6 fix 2). No step here acts on a verify fixture org
(``organizations.is_test``, services.orgs): the holdings refresh, its review
tasks and model monitoring, the execute-by sweep and the proposals tick all
skip them. A verify calls the per-org function directly with
``include_test_orgs=True`` (or its own ``org_id``), never through this tick.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from services.database import close_pool, get_pool
from services.deal_room_control import access_tick
from services.edgar_index_schedule import maybe_refresh_index
from services.execution import expire_tick
from services.rebalance_proposals import proposals_tick
from services.replica_schedule import maybe_refresh_holdings
from services.workflow_scheduler import run_scheduler_tick


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[scheduler] FATAL: DATABASE_URL is not set", file=sys.stderr)
        return 2

    try:
        # statement_cache_size=0 is mandatory behind Supabase's PgBouncer.
        # No is_super_admin GUC is set here — see "WHY TWO CONNECTIONS" above:
        # each query that needs the platform-scope carve-out sets it itself,
        # fresh, via services.database.platform_scope.
        conn = await asyncpg.connect(
            database_url, statement_cache_size=0, ssl="require", timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] FATAL: cannot connect: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    index = {"action": "not_run"}
    holdings = {"action": "not_run"}
    try:
        pool = await get_pool()
        result = await run_scheduler_tick(conn, pool)
        # Its own step, after the workflow tick: a problem here can never stop
        # a workflow trigger from firing.
        try:
            index = await maybe_refresh_index(conn)
        except Exception as exc:  # noqa: BLE001
            index = {"action": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if index["action"] in ("succeeded", "failed"):
            print(f"[scheduler] edgar index refresh: {index}")
        # Its own step again: a holdings problem never stops the steps above.
        try:
            holdings = await maybe_refresh_holdings(pool)
        except Exception as exc:  # noqa: BLE001
            holdings = {"action": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if holdings["action"] in ("succeeded", "partial", "failed"):
            print(f"[scheduler] replica holdings refresh: { {k: v for k, v in holdings.items() if k != 'results'} }")
        try:
            expiry = await expire_tick(pool)
            if any(expiry.values()):
                print(f"[scheduler] execution instructions at execute-by: "
                      f"{ {k: len(v) for k, v in expiry.items()} }")
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] execution expiry failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        # DEALROOM3 — deal-room access: expire at the end date, the expiring
        # notice, pause on a lapsed eligibility and restore. Never a test org.
        try:
            rooms = await access_tick(pool)
            moved = {k: sum(len(o.get(k, [])) for o in rooms.values()) for k in ("expired", "paused", "restored")}
            if any(moved.values()):
                print(f"[scheduler] deal-room access: {moved}")
            for org, o in rooms.items():
                for e in o.get("errors", []):
                    print(f"[scheduler] deal-room access ({org}): {e}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] deal-room access failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            props = await proposals_tick(pool)
            errs = [e for o in props.values() for e in o.get("errors", [])]
            made = sum(1 for o in props.values() for s in o.get("scans", []) if s.get("proposal_id"))
            if made or errs:
                print(f"[scheduler] rebalance proposals: {made} generated, {len(errs)} error(s)")
            for e in errs:
                print(f"[scheduler] rebalance proposals: {e}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] rebalance proposals failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        await conn.close()
        await close_pool()

    for err in result.errors:
        print(f"[scheduler] unresolved: {err}", file=sys.stderr)
    if index["action"] == "failed":
        print(f"[scheduler] edgar index refresh failed: {index.get('error')}", file=sys.stderr)
    if holdings["action"] == "failed":
        print(f"[scheduler] replica holdings refresh failed: {holdings.get('error')}", file=sys.stderr)
    return 1 if result.errors or index["action"] == "failed" or holdings["action"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
