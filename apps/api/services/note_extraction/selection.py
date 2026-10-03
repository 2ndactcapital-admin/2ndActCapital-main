"""Which notes a run reads.

The primary source is the manifest: ``pipeline_status = 'ready_for_extraction'``
rows with a stored document. MEASURED at sprint time (Task 1a): ZERO such rows
exist — the 200 fetched manifest rows are the 2025Q1 corpus, linked without
re-download and never classified. So ``include_corpus=True`` additionally
considers those stored corpus filings, applying sprint A's OWN free rules
(services.edgar_pipeline.decide_status) in memory to the stored text: only a
final pricing supplement that passes the prefilter is eligible. The manifest's
pipeline_status is never changed (out of scope for B1).

Selection is stratified: round-robin across (issuer, filing year) so a few
prolific issuers cannot dominate a pilot.

COHORT (edgarcohorts). ``cohort_notes`` replaces the sampler: exactly the
cohort's members that are ready_for_extraction with a stored document, in
cohort order. Nothing else is added and nothing is re-ordered.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from services.edgar_pipeline import decide_status


@dataclass
class NoteRef:
    reference_filing_id: str
    issuer: str
    filing_year: int | None
    source: str                 # 'manifest_ready' | 'corpus_rules'
    text: str | None = None     # stored extracted_text when available (sampler keywords)


async def ready_notes(conn) -> list[NoteRef]:
    rows = await conn.fetch(
        """SELECT f.reference_filing_id, f.filing_date,
                  COALESCE(i.issuer_group, rf.filer_name) AS issuer
             FROM portfolio.edgar_index_filings f
             JOIN portfolio.reference_filings rf ON rf.id = f.reference_filing_id
             LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
            WHERE f.pipeline_status = 'ready_for_extraction'
              AND f.reference_filing_id IS NOT NULL""")
    return [NoteRef(str(r["reference_filing_id"]), r["issuer"] or "unknown",
                    r["filing_date"].year if r["filing_date"] else None, "manifest_ready") for r in rows]


async def corpus_notes(conn) -> list[NoteRef]:
    rows = await conn.fetch(
        """SELECT rf.id, rf.filer_name, rf.filing_date, rf.extracted_text,
                  i.issuer_group
             FROM portfolio.reference_filings rf
             LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = lpad(rf.cik, 10, '0')
                                                          OR i.filer_cik = rf.cik
            WHERE rf.extracted_text IS NOT NULL AND rf.extraction_status = 'extracted'""")
    out = []
    for r in rows:
        status, _reason, _kind = decide_status(r["extracted_text"] or "")
        if status != "ready_for_extraction":
            continue
        out.append(NoteRef(str(r["id"]), r["issuer_group"] or r["filer_name"] or "unknown",
                           r["filing_date"].year if r["filing_date"] else None, "corpus_rules",
                           r["extracted_text"]))
    return out


def stratify(notes: list[NoteRef], limit: int) -> list[NoteRef]:
    strata: dict[tuple, list[NoteRef]] = defaultdict(list)
    for n in sorted(notes, key=lambda n: n.reference_filing_id):
        strata[(n.issuer, n.filing_year)].append(n)
    keys = sorted(strata, key=lambda k: (str(k[0]), k[1] or 0))
    out: list[NoteRef] = []
    while len(out) < limit and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(out) < limit:
                out.append(strata[k].pop(0))
    return out


async def cohort_notes(conn, cohort_id) -> list[NoteRef]:
    from services import edgar_cohorts
    from services.database import platform_scope

    ids = await edgar_cohorts.ready_reference_ids(conn, cohort_id)
    if not ids:
        return []
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT f.reference_filing_id, f.filing_date,
                      COALESCE(i.issuer_group, rf.filer_name) AS issuer
                 FROM portfolio.edgar_index_filings f
                 JOIN portfolio.reference_filings rf ON rf.id = f.reference_filing_id
                 LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
                WHERE f.reference_filing_id = ANY($1::uuid[])""", ids)
    by = {str(r["reference_filing_id"]): r for r in rows}
    return [NoteRef(i, by[i]["issuer"] or "unknown",
                    by[i]["filing_date"].year if by[i]["filing_date"] else None, "cohort")
            for i in ids if i in by]


async def select_notes(conn, *, limit: int, include_corpus: bool) -> tuple[list[NoteRef], dict]:
    ready = await ready_notes(conn)
    seen = {n.reference_filing_id for n in ready}
    corpus = [n for n in await corpus_notes(conn) if n.reference_filing_id not in seen] if include_corpus else []
    chosen = stratify(ready + corpus, limit)
    return chosen, {"ready_for_extraction": len(ready), "corpus_eligible": len(corpus),
                    "selected": len(chosen), "requested": limit}
