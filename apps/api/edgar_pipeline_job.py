"""Render one-off job entrypoint — one EDGAR pipeline job, then exit.

    python edgar_pipeline_job.py --run-id <uuid>
        Run the job for an existing portfolio.edgar_pipeline_runs row. This is
        the command services.edgar_pipeline.start_render_job asks Render to run
        on the base service (same build, same Doppler-synced environment).

    python edgar_pipeline_job.py --cli [--fetch-cap N] [--stages discover,select,fetch]
        Create a 'cli' run row and run it in this process (operator use, e.g.
        under `doppler run --`).

    python edgar_pipeline_job.py --cli --cohort <uuid> [--fetch-cap N]
        Same, but the run targets a COHORT: its fetch stage works through
        exactly the cohort's members in cohort order (edgarcohorts). A cohort
        run has one stage, fetch.

Like workflow_scheduler_tick.py this is NOT the API process: it imports no
router and serves no request. It opens one raw asyncpg connection
(statement_cache_size=0 — mandatory behind Supabase's pooler) and every write
sets app.is_super_admin LOCAL inside its own transaction
(services.database.platform_scope).

The stages and both caps live on the run row, not on this command line, so the
launcher, the job and the monitoring screen read one record.

EXIT CODES (Render surfaces them as the job's status):
    0  the job ran (status 'succeeded') or was refused because another job
       holds the lease ('refused' is correct behaviour, not a failure)
    1  the job failed; the error is on the run row
    2  the job could not start (no DATABASE_URL, no connection, no run row)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

import asyncpg

from services import edgar_pipeline


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--fetch-cap", type=int, default=None)
    parser.add_argument("--stages", default=",".join(edgar_pipeline.STAGES))
    parser.add_argument("--cohort", type=UUID, default=None)
    args = parser.parse_args(argv)
    if not args.run_id and not args.cli:
        parser.error("one of --run-id or --cli is required")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[edgar-job] FATAL: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        conn = await asyncpg.connect(database_url, statement_cache_size=0, ssl="require", timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[edgar-job] FATAL: cannot connect: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        run_id = args.run_id
        if args.cli:
            run_id = await edgar_pipeline.create_run(
                conn, trigger_source="cli",
                fetch_cap=edgar_pipeline.fetch_cap_default() if args.fetch_cap is None else args.fetch_cap,
                stages=(["fetch"] if args.cohort else
                        [s.strip() for s in args.stages.split(",") if s.strip()]),
                status="launched", cohort_id=args.cohort,
            )
        if await edgar_pipeline.load_run(conn, run_id) is None:
            print(f"[edgar-job] FATAL: no pipeline run {run_id}", file=sys.stderr)
            return 2
        print(f"[edgar-job] run {run_id} starting")
        try:
            row = await edgar_pipeline.run_job(conn, run_id)
        except Exception as exc:  # noqa: BLE001 — already recorded on the run row
            print(f"[edgar-job] run {run_id} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(
            f"[edgar-job] run {run_id} {row['status']}: discovered_new={row['discovered_new']} "
            f"selected={row['selected']} not_selected={row['not_selected']} "
            f"fetched={row['fetched']} fetch_failed={row['fetch_failed']} "
            f"bytes_uploaded={row['bytes_uploaded']} sec_requests={row['sec_requests']} "
            f"stop={row['stop_reason']}"
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
