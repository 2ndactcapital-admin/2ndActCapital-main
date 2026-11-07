"""PRICES1 — the end-of-day price backfill (OPERATOR). Loads N years of daily closes for the coverage set through the
price receptor (services/security_data/prices.py) — never "all 16k": only what the platform needs (held, modelled,
replicated, option underlyings, benchmarks; test orgs left out).

    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices_backfill.py --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices_backfill.py            # the real load
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices_backfill.py --resume   # after a crash / Ctrl-C

Flags: --years N (default 5) · --provider KEY (default: every active EOD source in the platform rank, in order; what
the first does not find goes to the next) · --batch N (securities per transaction, default 25) · --gsid ID (repeat;
narrow to these securities) · --include-test-orgs · --checkpoint PATH (default
apps/api/scripts/.prices_backfill.<provider>.json) · --resume (skip what the checkpoint says is done; without it a
real run starts the checkpoint fresh) · --no-budget (ignore the registry's daily call budget — the operator's call).

--dry-run makes NO network call and writes nothing: coverage-set size, symbols, estimated calls / minutes, the days
it takes at the daily budget. A real run streams batch by batch (one batch in memory), prints a progress line per
batch and a summary per provider, records the provider calls and one security_data_runs row per provider
(summary.job = 'prices_backfill') — the job history the Security master screen lists. Re-running is safe: an equal
close writes nothing; a changed close is a new system version. Exit 0 / 1 (a source failed)."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))

from _db_connect import hydrate  # noqa: E402

hydrate()   # Doppler over HTTPS — never a stale local copy (CLAUDE.md "Secrets")
sys.path.insert(0, str(API_DIR))


def parse(argv):
    ap = argparse.ArgumentParser(description="End-of-day price backfill for the coverage set (PRICES1).")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--provider", action="append", default=None)
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--gsid", action="append", default=None)
    ap.add_argument("--include-test-orgs", action="store_true")
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-budget", action="store_true")
    return ap.parse_args(argv)


async def run(args) -> int:
    from services.database import get_pool
    from services.security_data import prices as P

    if args.years < 1 or args.years > 30:
        print("--years must be 1..30")
        return 2
    pool = await get_pool()
    async with pool.acquire() as conn:
        items = await P.coverage_set(conn, include_test_orgs=args.include_test_orgs, only_gsids=args.gsid)
        order = args.provider or await P.eod_rank(conn)
    by_src: dict = {}
    for it in items:
        for s in it.sources:
            by_src[s] = by_src.get(s, 0) + 1
    print(f"coverage set: {len(items)} securities {json.dumps(by_src, sort_keys=True)}; providers in order: {order}")
    if not order:
        print("no active end-of-day source in the platform rank")
        return 1
    pending, failed = items, False
    for key in order:
        if not pending:
            break
        if args.dry_run:
            from services.security_data import layer as L
            prov = L.code_registry()[key]     # no transport: a dry run never touches the network
        else:
            prov = P.fetcher(key)
        ck = None
        if not args.dry_run or args.resume:
            path = pathlib.Path(args.checkpoint or HERE / f".prices_backfill.{key}.json")
            ck = P.Checkpoint(path, f"{key}:{args.years}")
            if not args.resume and not args.dry_run and path.exists():
                path.unlink()
        res = await P.backfill(pool, prov, pending, years=args.years, dry_run=args.dry_run, checkpoint=ck,
                               batch=args.batch, progress=lambda m: print(m, flush=True),
                               respect_budget=not args.no_budget)
        print(f"[{key}] " + json.dumps({k: v for k, v in res.items() if k not in ("errors",)}, default=str,
                                        sort_keys=True))
        for e in res.get("errors") or []:
            print(f"[{key}] ERROR {e}")
        if args.dry_run:
            print("dry run: nothing fetched, nothing written (only the first provider is estimated)")
            break
        failed = failed or bool(res.get("errors"))
        await P._aclose(prov)
        async with pool.acquire() as conn:
            have = await P.latest_dates(conn, key, [it.gsid for it in pending])
        nf = {it.gsid for it in pending} - set(have)
        pending = [it for it in pending if it.gsid in nf]
        print(f"[{key}] {len(pending)} securities without a series from {key} go to the next source")
    return 1 if failed else 0


def main() -> int:
    args = parse(sys.argv[1:])
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
