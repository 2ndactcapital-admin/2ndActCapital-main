"""Bounded EDGAR reference-corpus sample run.

ONE quarter, capped at ``--limit`` filings (default 200). This is NOT a
historical backfill — it proves the pipeline end to end and produces the
numbers the sprint reports.

Usage:
    python3 scripts/harvest_edgar_sample.py --year 2025 --quarter 1 --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.append(
    "/mnt/c/Users/Joe/2ndActCapital/apps/api/venv/lib/python3.14/site-packages"
)

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    override=False,
)

from services import edgar_fetch  # noqa: E402

CONCURRENCY = 5


def sample(filings: list, limit: int) -> list:
    """Deterministic spread across the quarter, not the first N rows.

    Taking the head would return 200 filings from the first trading days and a
    handful of filers, which would make the pass/skip split meaningless.
    """
    ordered = sorted(filings, key=lambda f: (f.filing_date, f.accession_number))
    if len(ordered) <= limit:
        return ordered
    stride = len(ordered) / limit
    return [ordered[int(i * stride)] for i in range(limit)]


async def _process(meta, client, pool, stats, lock):
    try:
        await edgar_fetch.resolve_filing_documents(meta, client)
        if not meta.primary_document:
            raise RuntimeError("no primary document in filing directory")
        raw = await edgar_fetch.fetch_filing(meta, client)
        row_id = await edgar_fetch.store_filing(pool, meta, raw)
    except Exception as exc:  # noqa: BLE001 — one bad filing must not stop the run
        message = f"{type(exc).__name__}: {exc}"
        try:
            await edgar_fetch.record_failure(pool, meta, message)
        except Exception:  # noqa: BLE001
            pass
        async with lock:
            stats["failed"] += 1
            stats["errors"].append((meta.accession_number, message))
        return

    async with lock:
        stats["fetched"] += 1
        stats["bytes"] += len(raw)
        stats["row_ids"].append(row_id)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--quarter", type=int, default=1)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    # Loud failure before a single request goes out.
    edgar_fetch.user_agent()

    # The ingest path is an admin/ops path; store_filing sets app.is_super_admin
    # inside its own transaction so it satisfies the write policies either way.
    database_url = os.environ["DATABASE_URL"]

    print(f"[index] {args.year} QTR{args.quarter}")
    client = edgar_fetch.make_client()
    try:
        filings = await edgar_fetch.fetch_index(args.year, args.quarter, client)
        total_424b2 = sum(1 for f in filings if f.form_type == "424B2")
        total_fwp = sum(1 for f in filings if f.form_type == "FWP")
        print(f"[index] rows={len(filings)} 424B2={total_424b2} FWP={total_fwp}")

        selected = sample(filings, args.limit)
        sel_424b2 = sum(1 for f in selected if f.form_type == "424B2")
        sel_fwp = sum(1 for f in selected if f.form_type == "FWP")
        print(f"[sample] selected={len(selected)} 424B2={sel_424b2} FWP={sel_fwp}")

        pool = await asyncpg.create_pool(
            database_url, statement_cache_size=0, min_size=1, max_size=10
        )
        stats = {"fetched": 0, "failed": 0, "bytes": 0, "row_ids": [], "errors": []}
        lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def guarded(meta):
            async with semaphore:
                await _process(meta, client, pool, stats, lock)

        try:
            await asyncio.gather(*(guarded(meta) for meta in selected))

            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT extraction_status, form_type, count(*) AS n,
                           sum(byte_size) AS bytes
                    FROM portfolio.reference_filings
                    GROUP BY extraction_status, form_type
                    ORDER BY extraction_status, form_type
                    """
                )
                totals = await conn.fetchrow(
                    """
                    SELECT count(*) AS n,
                           count(*) FILTER (WHERE extraction_status = 'extracted') AS passed,
                           count(*) FILTER (WHERE extraction_status = 'skipped') AS skipped,
                           count(*) FILTER (WHERE extraction_status = 'failed') AS failed,
                           sum(byte_size) AS bytes,
                           count(*) FILTER (
                               WHERE retention_classification = 'public_reference'
                           ) AS classified
                    FROM portfolio.reference_filings
                    """
                )
        finally:
            await pool.close()
    finally:
        await client.aclose()

    print("\n=== SAMPLE RUN ===")
    print(f"index rows (424B2+FWP): {len(filings)}  424B2={total_424b2} FWP={total_fwp}")
    print(f"selected: {len(selected)}  fetched ok: {stats['fetched']}  failed: {stats['failed']}")
    print(f"bytes stored: {stats['bytes']}")
    for row in rows:
        print(f"  {row['extraction_status']:>10} {row['form_type']:>6}: {row['n']}")
    print(
        f"table totals: rows={totals['n']} passed={totals['passed']} "
        f"skipped={totals['skipped']} failed={totals['failed']} "
        f"bytes={totals['bytes']} classified={totals['classified']}"
    )
    if stats["errors"]:
        print("\nfailures:")
        for accession, message in stats["errors"][:25]:
            print(f"  {accession}: {message}")
    return 0 if totals["n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
