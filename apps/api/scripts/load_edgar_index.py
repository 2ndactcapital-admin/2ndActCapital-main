#!/usr/bin/env python3
"""Load filtered EDGAR master-index rows (424B2 + FWP) into the discovery tables.

Usage (from the repo root):
    doppler run -- apps/api/venv/bin/python apps/api/scripts/load_edgar_index.py ~/edgar_idx

Reads every filtered_YYYY_QN.txt in the folder. Each line is one index row:
    CIK|Company Name|Form Type|Date Filed|edgar/data/<cik>/<accession>.txt

Deduplicates by accession number: a parent and its finance subsidiary listed on
the same filing collapse to ONE row in portfolio.edgar_index_filings, and every
listed company is kept in portfolio.edgar_index_filing_filers.

Safe to re-run: rows already loaded are skipped (ON CONFLICT DO NOTHING).
Only ever INSERTs. One transaction per quarter file.

Postgres refuses COPY FROM into a table with row-level security, so each file is
copied into a temporary table and then INSERTed with super-admin context set,
which keeps the tables' normal security policies in force.

The parser and loader live in services/edgar_index.py (edgarpipelinea), shared
with the nightly job's incremental discovery over EDGAR's daily index.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # apps/api, for services.*

from services.edgar_index import load_records, parse_index_lines  # noqa: E402

FILE_RE = re.compile(r"filtered_(\d{4})_Q([1-4])\.txt$")


def parse_file(path: Path, quarter: str):
    """Parsing lives in services.edgar_index, shared with the nightly job."""
    with open(path, encoding="latin-1") as fh:
        parsed = parse_index_lines(fh)
    return (parsed.filing_records(quarter), parsed.filer_records(),
            parsed.malformed, parsed.form_conflicts)


async def load_quarter(conn, filing_records, filer_records):
    return await load_records(conn, filing_records, filer_records)


async def main(folder: str) -> int:
    files = []
    for p in sorted(Path(folder).expanduser().glob("filtered_*.txt")):
        m = FILE_RE.search(p.name)
        if m:
            files.append((p, f"{m.group(1)}Q{m.group(2)}"))
    if not files:
        print(f"No filtered_YYYY_QN.txt files found in {folder}")
        return 1

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("APP_SERVICE_DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set. Run this under: doppler run -- ...")
        return 1
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        who = await conn.fetchrow(
            "SELECT current_user AS u, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass"
        )
        print(f"Connected as {who['u']} (bypasses RLS: {who['bypass']})\n")

        expected = {}
        print(f"{'quarter':<8} {'index rows':>10} {'unique':>8} {'new':>8} {'filer rows new':>15} "
              f"{'malformed':>9} {'form conflicts':>14}")
        for path, quarter in files:
            frecs, rrecs, malformed, conflicts = parse_file(path, quarter)
            expected[quarter] = {r[0] for r in frecs}
            new_filings, new_filers = await load_quarter(conn, frecs, rrecs)
            print(f"{quarter:<8} {len(rrecs):>10} {len(frecs):>8} {new_filings:>8} "
                  f"{new_filers:>15} {malformed:>9} {conflicts:>14}")

        # Verify against the database, not against what we think we inserted.
        all_expected = set().union(*expected.values())
        quarters = list(expected.keys())
        in_db = await conn.fetchval(
            "SELECT count(*) FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])",
            list(all_expected),
        )
        issuer_cover = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE f.form_type = '424B2') AS total_424b2,
                      count(*) FILTER (WHERE f.form_type = '424B2' AND EXISTS (
                          SELECT 1 FROM portfolio.edgar_index_filing_filers ff
                          JOIN portfolio.structured_note_issuers i ON i.filer_cik = ff.cik
                          WHERE ff.accession_number = f.accession_number
                            AND i.include_status IN ('yes', 'review'))) AS from_listed_issuers
               FROM portfolio.edgar_index_filings f
               WHERE f.index_quarter = ANY($1::text[])""",
            quarters,
        )
    finally:
        await conn.close()

    print(f"\nUnique filings in these files: {len(all_expected):,}")
    print(f"Of those, present in the database: {in_db:,}")
    total = issuer_cover["total_424b2"]
    listed = issuer_cover["from_listed_issuers"]
    pct = (100.0 * listed / total) if total else 0.0
    print(f"424B2 filings from listed issuers: {listed:,} of {total:,} ({pct:.1f}%)")

    if in_db != len(all_expected):
        print(f"\nMISMATCH: {len(all_expected) - in_db:,} filings from these files are not in the database.")
        return 1
    print("\nOK: every unique filing from these files is in the database.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(asyncio.run(main(sys.argv[1])))
