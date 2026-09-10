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
