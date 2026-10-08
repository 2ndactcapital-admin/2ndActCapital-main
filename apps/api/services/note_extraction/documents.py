"""Load a stored filing and map quotes back to the raw HTML.

The filing is read from R2 exactly as sprint A (or the 2025Q1 corpus before it)
stored it — gzip (``content_encoding = 'gzip'``) or identity — and re-extracted
with services.html_text, the same offset-preserving extractor that produced
``extracted_text``. Re-extraction is deterministic, so quote offsets computed
here index the same decoded string the stored offset maps describe.

A quote is a model's claim; ``locate_quote`` turns it into a measured fact: it
is found verbatim (whitespace-normalised) in the filing's text or it is
rejected, and a found quote's text span is mapped to raw-HTML character
offsets. Inside a text node whose raw form is the text itself the mapping is
exact (``html[start:end] == quote``); across markup or entities it widens to
the enclosing text nodes, never narrower than the quote.
"""
from __future__ import annotations

import asyncio
import gzip
import os
from dataclasses import dataclass, field

from services import html_text
from services.note_extraction.quote_match import TextIndex

# The corpus bucket (CONFIRMED in sprint A). Doppler's R2_BUCKET_NAME holds an
# invalid name, so reads use EDGAR_R2_BUCKET when set and the known corpus
# bucket otherwise — never services.storage's default.
CORPUS_BUCKET = "hollisworks-docs"


def corpus_bucket() -> str:
    return (os.environ.get("EDGAR_R2_BUCKET") or "").strip() or CORPUS_BUCKET


@dataclass
class FilingDocument:
    reference_filing_id: str
    html: str                       # decoded raw HTML (offsets index THIS string)
    text: str
    segments: list[tuple[int, int, int, int]]
    encoding: str = "utf-8"
    form_type: str | None = None
    filer_name: str | None = None
    cik: str | None = None
    filing_date: object = None
    accession_number: str | None = None
    _index: TextIndex | None = field(default=None, repr=False)

    @property
    def index(self) -> TextIndex:
        if self._index is None:
            self._index = TextIndex(self.text)
        return self._index


def document_from_html(html: str, *, reference_filing_id: str = "fixture", **meta) -> FilingDocument:
    ex = html_text.extract(html)
    return FilingDocument(reference_filing_id=reference_filing_id, html=html, text=ex.text,
                          segments=ex.segments, encoding=ex.encoding, **meta)


def document_from_bytes(raw: bytes, *, reference_filing_id: str, **meta) -> FilingDocument:
    html, encoding = html_text.decode_html(raw)
    ex = html_text.extract(html, encoding=encoding)
    return FilingDocument(reference_filing_id=reference_filing_id, html=html, text=ex.text,
                          segments=ex.segments, encoding=encoding, **meta)


FILING_COLUMNS = (
    "id, cik, filer_name, form_type, accession_number, filing_date, r2_key, "
    "content_encoding, extraction_status"
)


async def load_filing_row(conn, reference_filing_id) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {FILING_COLUMNS} FROM portfolio.reference_filings WHERE id = $1",
        reference_filing_id,
    )
    return dict(row) if row else None


def _download(r2_key: str, bucket: str) -> bytes:
    from services import storage

    return storage.download_bytes(r2_key, bucket)


async def load_document(conn, reference_filing_id, *, downloader=None) -> FilingDocument:
    """The filing's raw HTML from R2, decoded and re-extracted. Raises on a
    missing row or object — the caller records that as a failed note."""
    row = await load_filing_row(conn, reference_filing_id)
    if row is None:
        raise LookupError(f"reference filing {reference_filing_id} not found")
    if not row["r2_key"]:
        raise LookupError(f"reference filing {reference_filing_id} has no stored document")
    fetch = downloader or _download
    raw = await asyncio.to_thread(fetch, row["r2_key"], corpus_bucket())
    if row["content_encoding"] == "gzip":
        raw = gzip.decompress(raw)
    return document_from_bytes(
        raw, reference_filing_id=str(row["id"]), form_type=row["form_type"],
        filer_name=row["filer_name"], cik=row["cik"], filing_date=row["filing_date"],
        accession_number=row["accession_number"],
    )


# ── Quotes -> offsets ────────────────────────────────────────────────────────
@dataclass
class QuoteLocation:
    text_start: int
    text_end: int
    raw_start: int | None
    raw_end: int | None


def _segment_identity_offset(html: str, text: str, seg) -> int | None:
    """If the segment's raw span is its text plus surrounding whitespace, the
    raw offset of the text's first character; else None."""
    ts, te, rs, re_ = seg
    raw = html[rs:re_]
    stripped = raw.lstrip()
    lead = len(raw) - len(stripped)
    if stripped.rstrip() == text[ts:te]:
        return rs + lead
    return None


def raw_span_for_text_span(doc: FilingDocument, ts: int, te: int) -> tuple[int, int] | None:
    first = last = None
    for seg in doc.segments:
        if seg[1] > ts and first is None:
            first = seg
        if seg[0] < te:
            last = seg
        elif first is not None:
            break
    if first is None or last is None or first[0] >= te:
        return None
    base = _segment_identity_offset(doc.html, doc.text, first)
    start = base + max(0, ts - first[0]) if base is not None else first[2]
    base_last = _segment_identity_offset(doc.html, doc.text, last)
    end = base_last + (min(te, last[1]) - last[0]) if base_last is not None else last[3]
    if end < start:
        return None
    return start, end


def locate_quote(doc: FilingDocument, quote: str | None) -> QuoteLocation | None:
    """Find ``quote`` verbatim in the filing. None = not found = fabricated
    (or too short to anchor — under ``quote_match.MIN_QUOTE_CHARS``
    non-whitespace characters)."""
    span = doc.index.locate(quote)
    if span is None:
        return None
    ts, te = span
    raw = raw_span_for_text_span(doc, ts, te)
    return QuoteLocation(ts, te, raw[0] if raw else None, raw[1] if raw else None)
