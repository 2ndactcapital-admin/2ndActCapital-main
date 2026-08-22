"""EDGAR reference-corpus harvester — 424B2 and FWP filings.

SCOPE
──────────────────────────────────────────────────────────────────────────────
This module fetches PUBLIC SEC filings into a GLOBAL, non-org-scoped reference
corpus (``portfolio.reference_filings``). It is deliberately NOT part of
Chancery: no ``documents`` row, no drop path, no classifier call (the form type
is already known from EDGAR metadata), no entity linkage, and no writes to
``portfolio.securities_global``.

It stores raw filing bytes and plain text ONLY. Term extraction — payoffs,
barriers, caps — is a later sprint. Nothing here parses a term.

SEC COMPLIANCE — non-negotiable
──────────────────────────────────────────────────────────────────────────────
The SEC blocks clients that misbehave, and a block is account-wide and slow to
lift. Therefore:

  * every request carries a declared ``User-Agent`` read from ``EDGAR_USER_AGENT``.
    An unset value raises — there is no silent default, because a silent default
    is how you get an anonymous client that looks like a scraper.
  * requests are hard-limited to ``MAX_REQUESTS_PER_SECOND`` by a real
    ``asyncio.sleep`` in a shared limiter, not by best-effort spacing.
  * 429 and 5xx responses back off exponentially and honour ``Retry-After``.

INDEX STRATEGY
──────────────────────────────────────────────────────────────────────────────
Quarterly full-index files, not the full-text search API:

    https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{1-4}/master.idx

Pipe-delimited ``CIK|Company Name|Form Type|Date Filed|Filename``. About 30
index files cover 2019-present, versus a quarter-million individual search
calls.

FORM TYPE OVER-SELECTS
──────────────────────────────────────────────────────────────────────────────
424B2 is the prospectus-supplement form for ANY shelf takedown — plain vanilla
notes, MTNs, preferred, covered bonds all use it. So a cheap deterministic
keyword prefilter runs over the extracted text. Rows that fail it are marked
``skipped`` and RETAINED: the negative set is what makes a later precision
measurement possible, so it is never deleted.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import date

import httpx
from starlette.concurrency import run_in_threadpool

from services import html_text, storage

EDGAR_HOST = "https://www.sec.gov"
INDEX_URL = EDGAR_HOST + "/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
ARCHIVE_BASE = EDGAR_HOST + "/Archives/edgar/data/{cik}/{accession_nodash}"

TARGET_FORM_TYPES = frozenset({"424B2", "FWP"})

# The reference/ prefix is NEW and deliberately non-org-scoped. Existing R2
# prefixes (deals/, entity-docs/, spvs/) are all tenant data; this is not.
R2_PREFIX = "reference/edgar"

MAX_REQUESTS_PER_SECOND = 10
MAX_ATTEMPTS = 5
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
REQUEST_TIMEOUT = 120.0

# Cheap deterministic prefilter. Lowercase substring match on extracted text.
#
# The terms are not equally diagnostic. "underlying" appears in plenty of
# vanilla takedowns, so on its own it means nothing; "participation rate" or
# "autocall" appears essentially only in a structured payoff. Measured against
# the first sample: a flat two-hit threshold dropped an "Enhanced Return Notes
# linked to a Basket" FWP whose only hit was "participation rate" — a clear
# false negative. So: any STRONG term passes on its own, and the weak term only
# counts toward a two-hit total.
#
# Both sides are retained either way, so this rule is tunable against the stored
# negative set rather than being a one-way door.
STRONG_KEYWORDS = (
    "barrier",
    "buffer",
    "autocall",
    "contingent coupon",
    "participation rate",
    "initial level",
)
WEAK_KEYWORDS = ("underlying",)
PREFILTER_KEYWORDS = STRONG_KEYWORDS + WEAK_KEYWORDS
PREFILTER_MIN_HITS = 2

_FILE_NUMBER_RE = re.compile(r"<FILE-NUMBER>\s*([0-9\-]+)", re.IGNORECASE)
_DOC_SUFFIXES = (".htm", ".html", ".txt")


class EdgarConfigError(RuntimeError):
    """Raised when required EDGAR configuration is missing."""


@dataclass
class FilingMeta:
    """One row of the quarterly index, plus what the filing index adds."""

    cik: str
    filer_name: str
    form_type: str
    accession_number: str
    filing_date: date
    submission_path: str
    primary_document: str | None = None
    file_number: str | None = None

    @property
    def accession_nodash(self) -> str:
        return self.accession_number.replace("-", "")

    @property
    def archive_base(self) -> str:
        return ARCHIVE_BASE.format(
            cik=self.cik, accession_nodash=self.accession_nodash
        )

    @property
    def source_url(self) -> str:
        if self.primary_document:
            return f"{self.archive_base}/{self.primary_document}"
        return f"{EDGAR_HOST}/Archives/{self.submission_path}"

    def r2_key(self) -> str:
        document = self.primary_document or f"{self.accession_number}.txt"
        return f"{R2_PREFIX}/{self.cik}/{self.accession_number}/{document}"


# ── SEC-compliant HTTP ──────────────────────────────────────────────────────
def user_agent() -> str:
    """Declared SEC User-Agent. Loud failure when unset — never a default."""
    value = (os.environ.get("EDGAR_USER_AGENT") or "").strip()
    if not value:
        raise EdgarConfigError(
            "EDGAR_USER_AGENT is not set. The SEC requires a declared "
            "User-Agent of the form 'Company/1.0 (contact@example.com)' on "
            "every request and blocks clients that omit it. Refusing to make "
            "an anonymous request."
        )
    return value


class RateLimiter:
    """Hard cap of ``rate`` requests per second, enforced with a real sleep."""

    def __init__(self, rate: int = MAX_REQUESTS_PER_SECOND):
        self._rate = rate
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 1.0:
                    self._calls.popleft()
                if len(self._calls) < self._rate:
                    self._calls.append(now)
                    return
                await asyncio.sleep(1.0 - (now - self._calls[0]))


_limiter = RateLimiter()


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET with rate limiting and backoff. Raises on non-retryable failure."""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        await _limiter.acquire()
        try:
            response = await client.get(url, headers={"User-Agent": user_agent()})
        except httpx.HTTPError as exc:
            last_error = exc
            await asyncio.sleep(2**attempt)
            continue

        if response.status_code in RETRY_STATUSES:
            retry_after = response.headers.get("Retry-After")
            delay = 2**attempt
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            last_error = httpx.HTTPStatusError(
                f"{response.status_code} from {url}",
                request=response.request,
                response=response,
            )
            await asyncio.sleep(delay)
            continue

        response.raise_for_status()
        return response

    raise RuntimeError(f"EDGAR request failed after {MAX_ATTEMPTS} attempts: {url}") \
        from last_error


def make_client() -> httpx.AsyncClient:
    """An httpx client configured for EDGAR. httpx is the declared HTTP dep."""
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True)


# ── Task 3: index + filing fetch ────────────────────────────────────────────
def parse_index(text: str) -> list[FilingMeta]:
    """Parse a ``master.idx`` body into 424B2/FWP metadata rows."""
    filings: list[FilingMeta] = []
    for line in text.splitlines():
        if line.count("|") != 4:
            continue
        cik, filer_name, form_type, filing_date, path = line.split("|")
        form_type = form_type.strip()
        if form_type not in TARGET_FORM_TYPES:
            continue
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        try:
            filed_on = date.fromisoformat(filing_date.strip())
        except ValueError:
            continue
        filings.append(
            FilingMeta(
                cik=cik.strip(),
                filer_name=filer_name.strip(),
                form_type=form_type,
                accession_number=accession,
                filing_date=filed_on,
                submission_path=path.strip(),
            )
        )
    return filings


async def fetch_index(
    year: int, quarter: int, client: httpx.AsyncClient | None = None
) -> list[FilingMeta]:
    """Fetch one quarterly full-index file, filtered to 424B2 and FWP."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter}")
    url = INDEX_URL.format(year=year, quarter=quarter)
    own_client = client is None
    client = client or make_client()
    try:
        response = await _get(client, url)
        body = response.content.decode("latin-1")
    finally:
        if own_client:
            await client.aclose()
    return parse_index(body)


def _pick_primary_document(items: list[dict], form_type: str) -> str | None:
    """Choose the prospectus body from a filing's directory listing.

    EDGAR does not label the primary document in ``index.json``, so: take the
    document-shaped files, drop the index/header artefacts, and pick the
    largest. For a 424B2 or FWP the body is the biggest document in the folder
    by a wide margin — the rest are images and the SGML wrapper.
    """
    candidates = []
    for item in items:
        name = (item.get("name") or "").strip()
        lowered = name.lower()
        if not lowered.endswith(_DOC_SUFFIXES):
            continue
        if "-index" in lowered or lowered.endswith(".hdr.sgml"):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        candidates.append((size, name))
    if not candidates:
        return None
    # Prefer real HTML bodies over the SGML full-submission .txt wrapper.
    html_candidates = [c for c in candidates if c[1].lower().endswith((".htm", ".html"))]
    pool = html_candidates or candidates
    pool.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return pool[0][1]


async def resolve_filing_documents(
    meta: FilingMeta, client: httpx.AsyncClient
) -> FilingMeta:
    """Populate ``primary_document`` and ``file_number`` on ``meta``.

    Two requests: the directory listing names the documents, the SGML header
    carries the ``333-xxxxx`` shelf file number that links a takedown back to
    its registration statement.
    """
    listing = await _get(client, f"{meta.archive_base}/index.json")
    items = (listing.json().get("directory") or {}).get("item") or []
    meta.primary_document = _pick_primary_document(items, meta.form_type)

    header = await _get(
        client, f"{meta.archive_base}/{meta.accession_number}-index-headers.html"
    )
    match = _FILE_NUMBER_RE.search(header.text)
    meta.file_number = match.group(1).strip() if match else None
    return meta


async def fetch_filing(
    meta: FilingMeta, client: httpx.AsyncClient | None = None
) -> bytes:
    """Fetch the raw bytes of a filing's primary document."""
    if not meta.primary_document:
        raise ValueError(
            f"primary_document unresolved for {meta.accession_number}; "
            "call resolve_filing_documents first"
        )
    own_client = client is None
    client = client or make_client()
    try:
        response = await _get(client, meta.source_url)
        return response.content
    finally:
        if own_client:
            await client.aclose()


# ── Prefilter + extraction ──────────────────────────────────────────────────
def prefilter_hits(text: str) -> list[str]:
    """Distinct prefilter keywords present in ``text``. Deterministic."""
    lowered = text.lower()
    return [keyword for keyword in PREFILTER_KEYWORDS if keyword in lowered]


def passes_prefilter(text: str) -> bool:
    """One strong term, or any two terms. See the keyword tables above."""
    hits = prefilter_hits(text)
    if any(keyword in STRONG_KEYWORDS for keyword in hits):
        return True
    return len(hits) >= PREFILTER_MIN_HITS


def extract_filing_text(raw_bytes: bytes) -> html_text.HtmlExtraction:
    """Plain text + raw-HTML character offsets. No term parsing whatsoever."""
    return html_text.extract_bytes(raw_bytes)


def offset_map_key(r2_key: str) -> str:
    """Sibling key holding the provenance map for ``r2_key``.

    The offset map lives in R2 rather than in a Postgres column: it is one
    entry per text node, which for a 424B2 runs to tens of thousands of
    entries, and inlining that per row would bloat the table for data that is
    only read when tracing a term back to its source span. The key is derived,
    so nothing has to be looked up to find it.
    """
    return f"{r2_key}.offsets.json"


# ── Task 3: idempotent storage ──────────────────────────────────────────────
async def store_filing(pool, meta: FilingMeta, raw_bytes: bytes) -> str:
    """Store one filing's bytes in R2 and upsert its row. Returns the row id.

    IDEMPOTENT on ``(accession_number, primary_document)``: re-running never
    duplicates a row, and identical bytes are never re-uploaded — the stored
    ``content_hash`` is compared first.

    This is the super-admin ingest path, so it sets ``app.is_super_admin``
    inside its own transaction (SET LOCAL semantics, PgBouncer-safe) to satisfy
    the table's write policies.
    """
    if not meta.primary_document:
        raise ValueError("primary_document is required to store a filing")

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    byte_size = len(raw_bytes)
    r2_key = meta.r2_key()

    extraction = extract_filing_text(raw_bytes)
    text = extraction.text
    if not text.strip():
        status, error = "failed", "extraction produced no text"
    elif passes_prefilter(text):
        status, error = "extracted", None
    else:
        # Retained, not deleted — the negative set is the point.
        status, error = "skipped", None

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )
            existing = await conn.fetchrow(
                """
                SELECT id, content_hash, r2_key
                FROM portfolio.reference_filings
                WHERE accession_number = $1 AND primary_document = $2
                """,
                meta.accession_number,
                meta.primary_document,
            )

            already_uploaded = bool(
                existing
                and existing["content_hash"] == content_hash
                and existing["r2_key"] == r2_key
            )
            if not already_uploaded:
                await run_in_threadpool(
                    storage.upload_bytes,
                    r2_key,
                    raw_bytes,
                    "text/html",
                )
                await run_in_threadpool(
                    storage.upload_bytes,
                    offset_map_key(r2_key),
                    _dump_offsets(extraction),
                    "application/json",
                )

            row = await conn.fetchrow(
                """
                INSERT INTO portfolio.reference_filings (
                    cik, filer_name, form_type, accession_number, filing_date,
                    file_number, primary_document, source_url, r2_key,
                    content_hash, byte_size, extracted_text, extraction_status,
                    extraction_error, retention_classification
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, 'public_reference')
                ON CONFLICT (accession_number, primary_document) DO UPDATE SET
                    cik = EXCLUDED.cik,
                    filer_name = EXCLUDED.filer_name,
                    form_type = EXCLUDED.form_type,
                    filing_date = EXCLUDED.filing_date,
                    file_number = EXCLUDED.file_number,
                    source_url = EXCLUDED.source_url,
                    r2_key = EXCLUDED.r2_key,
                    content_hash = EXCLUDED.content_hash,
                    byte_size = EXCLUDED.byte_size,
                    extracted_text = EXCLUDED.extracted_text,
                    extraction_status = EXCLUDED.extraction_status,
                    extraction_error = EXCLUDED.extraction_error,
                    updated_at = now()
                RETURNING id
                """,
                meta.cik,
                meta.filer_name,
                meta.form_type,
                meta.accession_number,
                meta.filing_date,
                meta.file_number,
                meta.primary_document,
                meta.source_url,
                r2_key,
                content_hash,
                byte_size,
                text,
                status,
                error,
            )
            return str(row["id"])


def _dump_offsets(extraction: html_text.HtmlExtraction) -> bytes:
    import json

    return json.dumps(extraction.to_offset_map(), separators=(",", ":")).encode("utf-8")


async def record_failure(pool, meta: FilingMeta, message: str) -> None:
    """Record a filing we could not fetch or extract, without raw bytes."""
    document = meta.primary_document or f"{meta.accession_number}.txt"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )
            await conn.execute(
                """
                INSERT INTO portfolio.reference_filings (
                    cik, filer_name, form_type, accession_number, filing_date,
                    file_number, primary_document, source_url,
                    extraction_status, extraction_error,
                    retention_classification
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'failed', $9,
                        'public_reference')
                ON CONFLICT (accession_number, primary_document) DO UPDATE SET
                    extraction_status = 'failed',
                    extraction_error = EXCLUDED.extraction_error,
                    updated_at = now()
                """,
                meta.cik,
                meta.filer_name,
                meta.form_type,
                meta.accession_number,
                meta.filing_date,
                meta.file_number,
                document,
                meta.source_url,
                message[:2000],
            )
