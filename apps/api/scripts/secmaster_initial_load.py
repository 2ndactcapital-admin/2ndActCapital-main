#!/usr/bin/env python3
"""SECMASTER1 — the ONE-TIME load of the platform security master. Run by the OPERATOR, never by a sprint or the
scheduler (it is long: the 13(f) CUSIPs alone are ~20k OpenFIGI identifiers — ~35 min with OPENFIGI_API_KEY,
~80 min without).

    doppler run -- apps/api/venv/bin/python apps/api/scripts/secmaster_initial_load.py --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/secmaster_initial_load.py
    doppler run -- apps/api/venv/bin/python apps/api/scripts/secmaster_initial_load.py --resume
    doppler run -- apps/api/venv/bin/python apps/api/scripts/secmaster_initial_load.py --source nasdaq_trader

ORDER (each stage only after the previous one finished):
    1. nasdaq_trader     the listed universe (creates securities; ticker, exchange, ETF flag, listing status)
    2. sec_tickers       CIK + exchange (matches stage 1; creates OTC / unlisted-elsewhere tickers it names)
    3. openfigi          FIGI + type for every listed ticker without one (not asked in the last 30 days)
    4. sec_13f_list      the current quarter's 13(f) list (one live row per CUSIP; options kept, never loaded)
    5. openfigi_13f      OpenFIGI for the 13(f) CUSIPs → match / create; each row mapped or "not mapped — why"
    6. finance_database  sector / industry / industry group / country / fund category (never creates)

--dry-run    fetches and parses every source and runs the matcher INSIDE A TRANSACTION THAT IS ROLLED BACK —
             exact new / matched / conflict / unmapped counts, nothing written (no OpenFIGI calls: it reports how
             many identifiers would be asked).
--source X   only stage X (repeatable).
--resume     continue the most recent unfinished load: every batch is checkpointed (source + batch number in
             portfolio.security_master_checkpoints) and a checkpointed batch is skipped. Safe after a crash: a
             batch is one transaction, and re-running a batch writes nothing anyway (the matcher is idempotent).
--batch N    records per batch (default 1000).

A progress line per batch, a summary per source (new / matched / conflicts / unmapped), and one row in the SAME
job history the nightly jobs write (portfolio.security_data_runs, job 'security_master_initial_load'). Nothing is
ever deleted; a disagreement with an existing security is a review item. The delisting check is NOT run here (the
first load establishes the universe; the nightly maintains it). Exit 0 = every stage completed; 1 = a stage failed
or was cut short; 2 = no database. Never prints a secret.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

STAGES = ("nasdaq_trader", "sec_tickers", "openfigi", "sec_13f_list", "openfigi_13f", "finance_database")
JOB = "security_master_initial_load"
STAGE_DONE = 2_000_000_000   # the checkpoint "batch" recording that a whole stage completed (batch_no >= 0 CHECK)


class Checkpoints:
    """--resume: portfolio.security_master_checkpoints, keyed (load_id, source, batch_no). Insert-only."""

    def __init__(self, pool, load_id):
        self.pool = pool
        self.load_id = load_id
        self._done: dict[str, set] = {}

    async def load(self) -> "Checkpoints":
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                for r in await conn.fetch("SELECT source, batch_no FROM portfolio.security_master_checkpoints "
                                          "WHERE load_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                                          str(self.load_id)):
                    self._done.setdefault(r["source"], set()).add(r["batch_no"])
        return self

    async def is_done(self, source: str, batch_no: int) -> bool:
        return batch_no in self._done.get(source, set())

    async def mark(self, source: str, batch_no: int, counts: dict) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    "INSERT INTO portfolio.security_master_checkpoints (load_id, source, batch_no, counts) VALUES "
                    "($1::uuid, $2, $3, $4::jsonb) ON CONFLICT (load_id, source, batch_no) WHERE valid_to IS NULL "
                    "AND system_to IS NULL DO NOTHING", str(self.load_id), source, int(batch_no),
                    json.dumps({k: v for k, v in counts.items() if k != "review_item_ids"}, default=str))
        self._done.setdefault(source, set()).add(batch_no)

    async def stage_done(self, source: str) -> bool:
        return STAGE_DONE in self._done.get(source, set())

    async def finish_stage(self, source: str, summary: dict) -> None:
        await self.mark(source, STAGE_DONE, summary)


async def _last_unfinished_load(pool):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            row = await conn.fetchrow(
                "SELECT c.load_id FROM portfolio.security_master_checkpoints c WHERE c.valid_to IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM portfolio.security_data_runs r WHERE r.summary->>'job' = $1 AND "
                "r.summary->>'load_id' = c.load_id::text AND r.status = 'success') "
                "ORDER BY c.done_at DESC LIMIT 1", JOB)
            return row["load_id"] if row else None


class _Rollback(Exception):
    pass


async def _dry_run_matcher(pool):
    """Patch Matcher.process to run inside a transaction that is always rolled back (counts, no writes)."""
    from services import security_master_load as M

    async def process(self, pool_, records):
        holder = {}
        async with pool_.acquire() as conn:
            try:
                async with conn.transaction():
                    await M._platform(conn)
                    holder["res"] = await self.process_conn(conn, records)
                    raise _Rollback()
            except _Rollback:
                pass
        return holder["res"]

    M.Matcher.process = process

    async def no_mark(*a, **k):
        return M.AbsenceResult(skipped="dry run")

    async def no_record(*a, **k):
        return None

    M.mark_absent = no_mark
    M.record_file = no_record


def _line(source: str, s: dict) -> str:
    matched = s.get("matched_total", s.get("match_matched_total", 0))
    return (f"  {source:<17} new {s.get('new', s.get('match_new', 0)):>6}  matched {matched:>6}  conflicts "
            f"{s.get('conflicts', s.get('match_conflicts', 0)):>5}  unmapped {s.get('unmapped', s.get('not_mapped', 0)):>6}"
            f"{'  deferred ' + str(s.get('deferred')) if s.get('deferred') else ''}")


async def _main(args) -> int:
    from services.database import close_pool, get_pool
    from services import security_master_sync as SS
    from services.market_data.adapters import scrub_error
    from services.security_data.providers.reference_common import RealSourceTransport

    if not os.environ.get("DATABASE_URL"):
        try:
            from _db_connect import admin_dsn, hydrate

            hydrate()
            dsn, _how = await admin_dsn()
            if dsn:
                os.environ["DATABASE_URL"] = dsn
        except Exception:  # noqa: BLE001
            pass
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (run through doppler run --)")
        return 2
    stages = [s for s in STAGES if not args.source or s in args.source]
    pool = await get_pool()
    transport = RealSourceTransport()
    started = datetime.now(timezone.utc)
    load_id = None
    if args.resume:
        load_id = await _last_unfinished_load(pool)
        print(f"resume: {'load ' + str(load_id) if load_id else 'no unfinished load found — starting a new one'}")
    load_id = load_id or uuid.uuid4()
    cp = None if args.dry_run else await Checkpoints(pool, load_id).load()
    if args.dry_run:
        await _dry_run_matcher(pool)
    ctx = SS.SyncContext(pool=pool, transport=transport, run_id=load_id, batch_size=args.batch, log=print,
                         checkpoint=cp)
    summary: dict = {"load_id": str(load_id), "dry_run": args.dry_run, "stages": {}}
    ok = True
    try:
        for st in stages:
            if cp is not None and await cp.stage_done(st):
                print(f"[{st}] already completed in load {load_id} — skipped")
                continue
            print(f"[{st}] starting")
            try:
                if st == "nasdaq_trader":
                    res = await SS.sync_universe(ctx, delisting=False)
                elif st == "sec_tickers":
                    res = await SS.sync_sec_tickers(ctx)
                elif st == "openfigi":
                    if args.dry_run:
                        async with pool.acquire() as conn:
                            async with conn.transaction():
                                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                                n = len(await SS._figi_candidates(conn, 1_000_000))
                        res = {"would_ask": n}
                    else:
                        res = {"rounds": []}
                        while True:
                            r = await SS.sync_openfigi_new(ctx, max_requests=200)
                            res["rounds"].append({k: v for k, v in r.items() if k != "review_item_ids"})
                            print(f"[openfigi] asked {r['asked']} hits {r['hits']} misses {r['misses']}")
                            if not r["asked"] or r["budget_exhausted"]:
                                break
                elif st == "sec_13f_list" and args.dry_run:
                    from services.security_data.providers import sec_13f_list as F13

                    q, url = await F13.latest_quarter(transport)
                    rows, stats = await F13.fetch_quarter(transport, url)
                    async with pool.acquire() as conn:
                        loaded = await SS.quarter_loaded(conn, q)
                    res = {"quarter": q, "already_loaded": loaded, "rows": stats.rows, "kept": stats.kept,
                           "excluded": stats.excluded, "deleted": sum(1 for r in rows if r.status == "deleted")}
                elif st == "sec_13f_list":
                    res = await SS.sync_13f(ctx, load_only=True)
                elif st == "openfigi_13f":
                    async with pool.acquire() as conn:
                        q = await conn.fetchval("SELECT max(quarter) FROM portfolio.sec_13f_list WHERE valid_to IS NULL "
                                                "AND system_to IS NULL")
                    if q is None:
                        res = {"skipped": "no 13(f) quarter is loaded yet"}
                    elif args.dry_run:
                        async with pool.acquire() as conn:
                            n = await conn.fetchval("SELECT count(*) FROM portfolio.sec_13f_list WHERE quarter = $1 AND "
                                                    "map_status = 'pending' AND valid_to IS NULL AND system_to IS NULL", q)
                        res = {"quarter": q, "would_ask": int(n)}
                    else:
                        res = {"quarter": q, "rounds": []}
                        while True:
                            r = await SS.map_13f_pending(ctx, q, max_requests=200)
                            res["rounds"].append({k: v for k, v in r.items() if k != "review_item_ids"})
                            print(f"[openfigi_13f] {q}: asked {r['asked']} mapped {r['mapped']} not mapped "
                                  f"{r['not_mapped']} still pending {r['still_pending']}")
                            if not r["decided"] or r["budget_exhausted"]:
                                break
                elif st == "finance_database":
                    res = await SS.sync_attributes(ctx)
                else:
                    continue
            except Exception as exc:  # noqa: BLE001 — report, stop: later stages depend on earlier ones
                print(f"[{st}] FAILED: {scrub_error(exc)}")
                summary["stages"][st] = {"error": scrub_error(exc)}
                ok = False
                break
            summary["stages"][st] = {k: v for k, v in res.items() if k != "review_item_ids"}
            if res.get("timed_out") or res.get("deferred"):
                ok = False
            if cp is not None:
                await cp.finish_stage(st, summary["stages"][st])
            print(f"[{st}] done")
    finally:
        await transport.aclose()
    print("\nSUMMARY (per source: new / matched / conflicts / unmapped)")
    for st, s in summary["stages"].items():
        print(_line(st, s) if "error" not in s else f"  {st:<17} FAILED: {s['error']}")
    if not args.dry_run:
        await SS.record_run(pool, job=JOB, trigger="manual", status="success" if ok else "partial",
                            started_at=started, summary=summary)
    await close_pool()
    print(f"\nload {load_id}: {'complete' if ok else 'INCOMPLETE — run again with --resume'}"
          f"{' (dry run: nothing written)' if args.dry_run else ''}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", action="append", choices=STAGES)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--batch", type=int, default=1000)
    return asyncio.run(_main(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
