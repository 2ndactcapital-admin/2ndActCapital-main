"""Replication Studio holdings ingestion — command line (rep1 Task 3).

    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --ticker SPY --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --ticker SPY
    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --ticker QQQ --source sec_nport
    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --all-active [--dry-run]

--ticker  resolve the fund (CUSIP via --cusip), pick its holdings source (or
          --source KEY), fetch, parse, resolve, validate, diff — and write the
          snapshot unless --dry-run.
--dry-run reports what it WOULD write and writes nothing at all: no fund
          created in the security master, no snapshot, no rows, no queued
          edge, no R2 object.
--all-active  every ACTIVE registry entry of every org, once per (fund, source).

Platform data only — no org is involved, so no review task is opened here (the
daily refresh and the API do that). Never prints a secret.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import CONNECT_KWARGS, hydrate, resolve_dsn  # noqa: E402

hydrate()

import asyncpg  # noqa: E402

from services import replica_holdings as H  # noqa: E402
from services import replica_schedule as RS  # noqa: E402


def _summary(res: H.IngestResult) -> dict:
    d = res.as_dict()
    diff = d.pop("diff", {}) or {}
    d["diff"] = {k: (len(v) if isinstance(v, list) else v) for k, v in diff.items()}
    d["validation"] = {k: c["ok"] for k, c in ((d.get("validation") or {}).get("checks") or {}).items()}
    return d


async def one(pool, http, *, ticker: str | None, cusip: str | None, source_key: str | None, dry_run: bool) -> dict:
    fund = await H.resolve_fund(pool, ticker=ticker, cusip=cusip, http=http, create=not dry_run)
    sec = fund.sec or {}
    async with pool.acquire() as conn:
        source = await H.choose_source(conn, ticker=fund.ticker, series_id=sec.get("series_id"),
                                       override_key=source_key)
    res = await H.ingest(pool, fund_global_security_id=fund.global_security_id, ticker=fund.ticker, source=source,
                         series_id=sec.get("series_id"), http=http, dry_run=dry_run)
    out = _summary(res)
    out["fund"] = {"global_security_id": fund.global_security_id, "in_security_master": fund.global_security_id
                   is not None, "would_create" if dry_run else "created": fund.global_security_id is None
                   if dry_run else fund.created, "sec": sec}
    return out


async def main_async(args) -> int:
    dsn, how = await resolve_dsn("DATABASE_URL")
    if not dsn:
        print(f"no working DATABASE_URL: {how}", file=sys.stderr)
        return 2
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, **CONNECT_KWARGS)
    http = H.make_http()
    try:
        if args.all_active:
            out = []
            for f in await RS.active_funds(pool):
                async with pool.acquire() as conn:
                    sources = {s["key"]: s for s in await H.list_sources(conn, active_only=True)}
                try:
                    res = await H.ingest(pool, fund_global_security_id=f["fund_id"], ticker=f["ticker"],
                                         source=sources[f["holdings_source_key"]], series_id=f["series_id"],
                                         http=http, dry_run=args.dry_run)
                    out.append(_summary(res))
                except Exception as exc:  # noqa: BLE001 — reported per fund
                    out.append({"ticker": f["ticker"], "error": f"{type(exc).__name__}: {exc}"})
            print(json.dumps(out, indent=2, default=str))
            return 1 if any("error" in o for o in out) else 0
        print(json.dumps(await one(pool, http, ticker=args.ticker, cusip=args.cusip, source_key=args.source,
                                   dry_run=args.dry_run), indent=2, default=str))
        return 0
    finally:
        await http.aclose()
        await pool.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker")
    g.add_argument("--cusip")
    g.add_argument("--all-active", action="store_true")
    ap.add_argument("--source", help="holdings source key (default: chosen from the sources table)")
    ap.add_argument("--dry-run", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
