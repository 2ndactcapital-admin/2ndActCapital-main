"""EDGAR discovery — parse master-index files and load the filing manifest.

Moved out of ``scripts/load_edgar_index.py`` (edgarpipelinea) so the one-off
quarterly loader and the nightly job share ONE parser and ONE loader:

  * ``scripts/load_edgar_index.py`` — quarterly ``filtered_YYYY_QN.txt`` files
  * ``services.edgar_pipeline.discover_incremental`` — EDGAR's DAILY index,
    every day since the newest filing_date already loaded

Each index line is ``CIK|Company Name|Form Type|Date Filed|edgar/data/<cik>/<accession>.txt``.
The quarterly files date as ``YYYY-MM-DD``, the daily files as ``YYYYMMDD``;
both are accepted.

DEDUPE: a parent and its finance subsidiary listed on the same co-issued note
are two index lines with one accession number. They collapse to ONE row in
``portfolio.edgar_index_filings`` and every listed company is kept in
``portfolio.edgar_index_filing_filers``.

LOADING: Postgres refuses ``COPY FROM`` into a table with row-level security,
so records are copied into a TEMP table and INSERTed from there, inside one
transaction with ``app.is_super_admin`` set LOCAL — the tables' normal write
policies stay in force. Only ever INSERTs (ON CONFLICT DO NOTHING), so a
re-run is a no-op. New filings get ``primary_issuer_cik`` computed in the same
transaction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

FORMS = frozenset({"424B2", "FWP"})
ACC_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def quarter_of(day: date) -> str:
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def _parse_date(value: str) -> date:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    return date.fromisoformat(value)


@dataclass
class ParsedIndex:
    """One index file's 424B2/FWP content, deduplicated by accession."""

    # accession -> (form_type, filing_date, submission_path)
    filings: dict = field(default_factory=dict)
    # (accession, cik) -> company_name
    filers: dict = field(default_factory=dict)
    malformed: int = 0
    form_conflicts: int = 0
    lines_matched: int = 0

    def filing_records(self, quarter: str | None = None) -> list[tuple]:
        """``(accession, form, filing_date, index_quarter, path, filer_count)``.

        ``quarter`` pins the quarter for a quarterly file (the file's own
        quarter); None derives it from each filing's date (daily files).
        """
        counts: dict[str, int] = {}
        for acc, _cik in self.filers:
            counts[acc] = counts.get(acc, 0) + 1
        return [
            (acc, form, filed, quarter or quarter_of(filed), path, counts[acc])
            for acc, (form, filed, path) in self.filings.items()
        ]

    def filer_records(self) -> list[tuple]:
        return [(acc, cik, name) for (acc, cik), name in self.filers.items()]


def parse_index_lines(lines) -> ParsedIndex:
    """Parse master-index lines into a deduplicated :class:`ParsedIndex`.

    Header lines, other form types and malformed rows are skipped. A line is
    "malformed" only if it has the 5-field shape of a data row but a field
    fails validation; the free-text header above the dashes is not counted.
    """
    out = ParsedIndex()
    for line in lines:
        parts = [p.strip() for p in line.rstrip("\r\n").split("|")]
        if len(parts) != 5:
            continue
        cik, name, form, filed, fname = parts
        if form not in FORMS:
            continue
        acc = fname.rsplit("/", 1)[-1]
        if acc.endswith(".txt"):
            acc = acc[:-4]
        if not cik.isdigit() or not ACC_RE.match(acc) or not name:
            out.malformed += 1
            continue
        try:
            filed_on = _parse_date(filed)
        except ValueError:
            out.malformed += 1
            continue
        out.lines_matched += 1
        cik = str(int(cik))  # unpadded, matching portfolio.reference_filings
        if acc in out.filings:
            if out.filings[acc][0] != form:
                out.form_conflicts += 1
        else:
            out.filings[acc] = (form, filed_on, fname)
        out.filers[(acc, cik)] = name
    return out


async def load_records(conn, filing_records, filer_records) -> tuple[int, int]:
    """INSERT filings + filers (idempotent). Returns ``(new_filings, new_filers)``.

    Opens its own transaction (a SAVEPOINT if the caller already holds one) and
    sets ``app.is_super_admin`` LOCAL inside it — never a session-level SET.
    """
    if not filing_records:
        return 0, 0
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS tmp_edgar_filings (
                   accession_number text, form_type text, filing_date date,
                   index_quarter text, submission_path text, filer_count integer
               ) ON COMMIT DROP"""
        )
        await conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS tmp_edgar_filers (
                   accession_number text, cik text, company_name text
               ) ON COMMIT DROP"""
        )
        await conn.execute("TRUNCATE tmp_edgar_filings, tmp_edgar_filers")
        await conn.copy_records_to_table("tmp_edgar_filings", records=filing_records)
        await conn.copy_records_to_table("tmp_edgar_filers", records=filer_records)
        new_accessions = await conn.fetch(
            """INSERT INTO portfolio.edgar_index_filings
                   (accession_number, form_type, filing_date, index_quarter,
                    submission_path, filer_count)
               SELECT accession_number, form_type, filing_date, index_quarter,
                      submission_path, filer_count
               FROM tmp_edgar_filings
               ON CONFLICT (accession_number) DO NOTHING
               RETURNING accession_number"""
        )
        filers_status = await conn.execute(
            """INSERT INTO portfolio.edgar_index_filing_filers
                   (accession_number, cik, company_name)
               SELECT t.accession_number, t.cik, t.company_name
               FROM tmp_edgar_filers t
               JOIN portfolio.edgar_index_filings f USING (accession_number)
               ON CONFLICT (accession_number, cik) DO NOTHING"""
        )
        accessions = [r["accession_number"] for r in new_accessions]
        if accessions:
            await conn.execute(
                """UPDATE portfolio.edgar_index_filings
                      SET primary_issuer_cik = portfolio.edgar_primary_issuer_cik(accession_number)
                    WHERE accession_number = ANY($1::text[])""",
                accessions,
            )
    return len(accessions), int(filers_status.split()[-1])
