#!/usr/bin/env python3
"""Bulk EDGAR downloader — the operator runs this on a laptop (tmux), not the API.

    doppler run -- python3 apps/api/scripts/run_bulk_fetch.py \\
        --banks "JPMorgan,UBS,Goldman Sachs,Morgan Stanley,Wells Fargo,Bank of America" \\
        --from 2025-01-01 --to 2026-10-01 --kinds final --concurrency 6 --max-rps 8 [--dry-run]

MODES (one per invocation)
  (default)              fetch the scope: selected filings of the source's form types for the named
                         institutions and filing dates (inclusive), institution by institution (order
                         given), newest first. Resumable: anything past 'selected' is skipped.
  --dry-run              print the scope per institution (every filer entity + CIK + count, per year),
                         the ETA and the R2 storage/cost estimate. Writes nothing.
  --peek-test N          peek N in-scope filings (Range GET of the first 16 KB), print HTTP status,
                         bytes and kind, and a verdict on whether the SEC honours Range. Writes nothing.
  --refresh-index        load EDGAR daily index files from the last loaded date through --to (default
                         yesterday), then apply the active selection policy to the NEW rows only.
                         With --dry-run: print the date span and the number of index files only.
  --reprocess-fetched    classify every row stuck at 'fetched' from its stored text (no SEC request).
  --selection-v2         Wells Fargo / Deutsche Bank prefilter v2: print what moves, by year; without
                         --dry-run it then activates policy v2 and moves exactly those rows.
  --worker               serve runs QUEUED from the EDGAR Pipeline screen's "Bulk download" tab
                         (bulkfetchui): heartbeat every 30 s, claim the oldest queued run for --target,
                         execute it with the same Runner as a CLI run, write progress every 100 filings.
                         One run at a time across all workers (8/s SEC budget). Ctrl-C or the screen's
                         Stop: in-flight filings finish, the run is marked 'stopped' (Requeue resumes it).

FLAGS (worker)
  --target laptop|render  which queue to serve (default laptop; 'render' is the entrypoint of a future
                         Render background worker — see docs/PROJECT_STATUS.md, BULKFETCH UI)
  --poll-seconds N       seconds between claim attempts when idle (default 15)
  --once                 claim at most one queued run, execute it, exit

    tmux new -s bulkworker 'doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --worker --target laptop'

FLAGS (fetch)
  --source NAME          a row of portfolio.edgar_sources (default structured_notes)
  --banks LIST           issuer-group names, comma-separated, case-insensitive
  --from / --to          filing dates, inclusive. A --to after the last indexed date stops with a
                         message to run --refresh-index first.
  --kinds LIST           final, preliminary, product_supplement, underlying_supplement, term_sheet,
                         prospectus_supplement, other  (default: final)
  --concurrency N        workers (default 6) — all share ONE rate limiter
  --max-rps N            SEC requests/second across all workers (default 8; above 8 needs
                         --allow-above-8; never above 10)
  --limit N              stop after N filings        --order newest|oldest
  --stop-after-failures N  stop the run after N consecutive failures (default 50)
  --no-peek              always download in full     --keep-preliminaries  download preliminaries too
  --retry-failed         also take fetch_failed rows --refetch-peek-skipped  re-take peek-skipped rows

Ctrl-C: in-flight filings finish, every unstarted claim is released, the run row is closed; exit 0.
No AI model is called anywhere in this script.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {s!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bulk EDGAR downloader (bulkfetch).",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--peek-test", type=int, metavar="N")
    mode.add_argument("--refresh-index", action="store_true")
    mode.add_argument("--reprocess-fetched", action="store_true")
    mode.add_argument("--selection-v2", action="store_true")
    mode.add_argument("--worker", action="store_true")
    p.add_argument("--target", choices=("laptop", "render"), default="laptop")
    p.add_argument("--poll-seconds", type=float, default=15)
    p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--source", default="structured_notes")
    p.add_argument("--banks", default="")
    p.add_argument("--from", dest="date_from", type=_date)
    p.add_argument("--to", dest="date_to", type=_date)
    p.add_argument("--kinds", default="final")
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--max-rps", type=int, default=8)
    p.add_argument("--allow-above-8", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--order", choices=("newest", "oldest"), default="newest")
    p.add_argument("--stop-after-failures", type=int, default=50)
    p.add_argument("--no-peek", action="store_true")
    p.add_argument("--keep-preliminaries", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--refetch-peek-skipped", action="store_true")
    p.add_argument("--groups", default="Wells Fargo,Deutsche Bank",
                   help="--selection-v2 only: issuer groups to re-decide")
    return p


async def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("APP_SERVICE_DATABASE_URL")
    if url and os.environ.get("EDGAR_USER_AGENT"):
        return url
    from _db_bootstrap import bootstrap_async   # hydrate from Doppler over HTTPS
    return await bootstrap_async(quiet=True) or url


async def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from services import edgar_bulk as eb
    from services import edgar_fetch, edgar_pipeline

    import asyncpg
    import httpx

    url = await _database_url()
    if not url:
        print("DATABASE_URL is not set. Run under: doppler run -- python3 apps/api/scripts/run_bulk_fetch.py ...")
        return 2
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    if args.worker:
        return await _worker(url, args)
    conc = max(1, args.concurrency)
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=conc + 3)
    log = edgar_pipeline.RequestLog()
    client = httpx.AsyncClient(timeout=edgar_fetch.REQUEST_TIMEOUT, follow_redirects=True,
                               event_hooks={"request": [log]})
    try:
        async with pool.acquire() as conn:
            if args.refresh_index:
                if not args.dry_run:
                    edgar_fetch.user_agent()
                    edgar_fetch.set_rate_limit(eb.check_rate(args.max_rps, args.allow_above_8))
                await eb.refresh_index(conn, client, through=args.date_to, dry_run=args.dry_run)
                return 0
            if args.reprocess_fetched:
                await eb.reprocess_fetched(conn, bucket=edgar_pipeline.r2_bucket(), dry_run=args.dry_run)
                print(f"SEC requests made: {log.count}")
                return 0
            if args.selection_v2:
                plan = await eb.plan_selection_v2(conn, groups=eb.parse_bank_names(args.groups),
                                                  bucket=edgar_pipeline.r2_bucket())
                eb.print_selection_plan(plan)
                if args.dry_run:
                    print("Dry run: nothing was written.")
                    return 0
                res = await eb.apply_selection_v2(conn, plan)
                print(f"applied: policy v{res['version']} active; {res['selected']} selected, "
                      f"{res['ready_for_extraction']} ready_for_extraction")
                return 0

            if args.date_from is None:
                print("--from is required (filing date, YYYY-MM-DD)")
                return 2
            source = await eb.load_source(conn, args.source)
            insts = await eb.resolve_institutions(conn, source, eb.parse_bank_names(args.banks))
            date_to = args.date_to or await eb.last_indexed_date(conn)
            await eb.check_dates_indexed(conn, date_to)
            eb.wanted_document_kinds(eb.parse_bank_names(args.kinds))
            rate = eb.check_rate(args.max_rps, args.allow_above_8)
            scope = eb.Scope(source, insts, args.date_from, date_to, args.order, args.retry_failed,
                             args.refetch_peek_skipped)
            if args.dry_run:
                await eb.dry_run(conn, scope, rate=rate, concurrency=conc, peek=not args.no_peek,
                                 limit=args.limit)
                return 0
            if args.peek_test is not None:
                edgar_fetch.user_agent()
                edgar_fetch.set_rate_limit(rate)
                await eb.peek_test(conn, scope, args.peek_test, client)
                return 0
        edgar_fetch.user_agent()   # loud failure before any claim if the User-Agent is missing
        opts = eb.RunOptions(
            source=args.source, banks=eb.parse_bank_names(args.banks), date_from=args.date_from,
            date_to=date_to, kinds=eb.parse_bank_names(args.kinds), concurrency=conc, max_rps=rate,
            allow_above_8=args.allow_above_8, limit=args.limit, order=args.order,
            stop_after_failures=args.stop_after_failures, peek=not args.no_peek,
            keep_preliminaries=args.keep_preliminaries, retry_failed=args.retry_failed,
            refetch_peek_skipped=args.refetch_peek_skipped)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _on_signal():
            if not stop.is_set():
                print("\nStopping: finishing in-flight filings, releasing the rest (Ctrl-C again is ignored).")
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, RuntimeError):
                pass
        runner = eb.Runner(pool, opts, client=client, stop_event=stop)
        stats = await runner.run()
        return 0 if stats.stop_reason is None or stop.is_set() else 1
    except eb.BulkUsageError as exc:
        print(f"ERROR: {exc}")
        return 2
    finally:
        await client.aclose()
        await pool.close()


async def _worker(url: str, args) -> int:
    """--worker: the queue's executor (services.edgar_bulk_queue.Worker), which
    runs every claimed bulk_fetch run through the same edgar_bulk.Runner as the
    CLI path above."""
    from services import edgar_bulk_queue as q
    from services import edgar_fetch

    import asyncpg

    edgar_fetch.user_agent()   # loud failure before heartbeating as a worker that cannot fetch
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=q.MAX_CONCURRENCY + 4)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal():
        if not stop.is_set():
            print("\nStopping: finishing in-flight filings; the run will be marked 'stopped' "
                  "(Requeue on the screen resumes it).")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            pass
    worker = q.Worker(pool, target=args.target, poll_seconds=args.poll_seconds, stop_event=stop)
    print(f"worker {worker.worker_id}: serving the {args.target} queue (poll {args.poll_seconds:g}s, "
          f"heartbeat {q.HEARTBEAT_SECONDS}s){' — once' if args.once else ''}. Ctrl-C to stop.")
    try:
        n = await worker.run(once=args.once)
    finally:
        await pool.close()
    print(f"worker {worker.worker_id}: executed {n} run(s); exiting.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
