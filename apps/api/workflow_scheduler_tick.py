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

WHY TWO CONNECTIONS. The scan and the atomic claim run on a PLAIN asyncpg
connection: the scheduler is a platform-level process and must see every org's
triggers, which an org-scoped RLS context would prevent. The runs themselves go
through the ordinary RLS-aware application pool, with each run created under
its OWN trigger's org context — so a trigger in one org can never start a run
in another.

SCHEDULE. Declared in render.yaml as every 5 minutes, UTC. Render's cron
schedules are UTC-only and cannot be made timezone-aware, which is exactly why
per-org local time is resolved inside services/workflow_schedule.py instead.
The 5-minute cadence and the module's 60-minute lookback are a matched pair:
the lookback must comfortably exceed the tick interval, or an occurrence
falling between two ticks would be missed.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from services.database import close_pool, get_pool
from services.workflow_scheduler import run_scheduler_tick


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[scheduler] FATAL: DATABASE_URL is not set", file=sys.stderr)
        return 2

    try:
        # statement_cache_size=0 is mandatory behind Supabase's PgBouncer.
        conn = await asyncpg.connect(
            database_url, statement_cache_size=0, ssl="require", timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] FATAL: cannot connect: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    try:
        pool = await get_pool()
        result = await run_scheduler_tick(conn, pool)
    finally:
        await conn.close()
        await close_pool()

    for err in result.errors:
        print(f"[scheduler] unresolved: {err}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
