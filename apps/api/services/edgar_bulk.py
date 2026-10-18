"""EDGAR BULK FETCH — the operator's command-line downloader (bulkfetch).

WHY THIS EXISTS
──────────────────────────────────────────────────────────────────────────────
The pipeline's fetch stage (``services.edgar_pipeline.fetch_stage``) is serial:
measured on the 308-filing gold cohort (run 3915068c, 2026-10-08 15:18-15:28
UTC) it made exactly 3 SEC requests per filing (folder ``index.json``, the SGML
``-index-headers.html``, the document) and took 588 s = 1.91 s per filing —
about 2.7 days for the ~125,000 selected 424B2s of the six big programs, inside
an API process on a plan that spins down. This module runs the SAME store /
classify code from a laptop (tmux), with several workers sharing ONE rate
limiter, a cover-page peek that skips preliminaries before the full download,
claims that make it resumable and safe to run twice, and a run row.

NO MODEL ANYWHERE. Classification is ``edgar_pipeline.classify_document`` (free
rules on the opening text) and the keyword prefilter; nothing here imports a
model client.

SOURCES ARE CONFIGURATION (``portfolio.edgar_sources``). A source names its
form types, where its filer list comes from (the structured-note issuer table
filtered by include_status, or an explicit CIK list) and a NAMED classifier from
:data:`CLASSIFIERS`. The runner takes ``--source`` (default structured_notes).

THE PEEK. A ``Range: bytes=0-16383`` GET of the primary document (with
``Accept-Encoding: identity`` so a partial body is plain bytes), the existing
classifier run on its text. A recognised kind the operator did not ask for is
recorded (document_kind + a live-CHECK status, ``not_pricing_supplement`` —
what ``decide_status`` already gives every non-pricing kind) with no full
download and no R2 write. An unrecognised cover ('other'), or a server that
ignores Range (200 with the whole body), falls through to the normal full
fetch: the peek never loses a filing. Peek requests go through the same
limiter as every other request.

RATE LIMIT. Every request — index, folder listing, header, peek, document —
goes through ``edgar_fetch``'s ONE module limiter (a sliding one-second window,
a real sleep). ``--max-rps`` defaults to 8 and may not exceed 8 without the
explicit override (and never 10, the SEC's own cap). Several runner PROCESSES
share the budget: each holds a heartbeat lease row ``bulk_fetch:<run id>`` in
``portfolio.edgar_pipeline_lease``, and at start a runner divides --max-rps by
the number of live runners (the nightly pipeline's own lease counts as one).

CLAIMS. ``edgar_index_filings.claimed_by / claimed_until`` (migration
bulkfetch_sources_claims_supplements.sql): a batch is claimed with
``FOR UPDATE SKIP LOCKED`` inside one UPDATE, so two runners never take the same
row; a crashed runner's claims lapse; the nightly queue skips claimed rows.
Only rows still at 'selected' (or, by flag, fetch_failed / peek-skipped) are
ever claimed — anything already past 'selected' is skipped, which is what
makes a restart resume.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx

from services import edgar_fetch, edgar_index, edgar_pipeline, storage
from services.database import platform_scope

# ── Configuration ───────────────────────────────────────────────────────────
DEFAULT_SOURCE = "structured_notes"
DEFAULT_MAX_RPS = 8                 # the agreed ceiling across ALL workers
ABSOLUTE_MAX_RPS = edgar_fetch.MAX_REQUESTS_PER_SECOND   # 10 — the SEC's own cap
PEEK_BYTES = 16 * 1024
SEC_ATTEMPTS = 3                    # a filing whose request fails 3 times -> fetch_failed
BACKOFF_BASE = 1.0                  # seconds; 1, 2, 4 ... (Retry-After wins when larger)
RETRY_AFTER_CAP = 60.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
CLAIM_BATCH = 50
CLAIM_TTL_SECONDS = 30 * 60
LEASE_PREFIX = "bulk_fetch:"
LEASE_TTL_SECONDS = 10 * 60
PROGRESS_EVERY = 100
DEFAULT_STOP_AFTER = 50
RUNTIME_CAP_SECONDS = 7 * 86400     # the run row's cap column must be > 0; a CLI run is operator-stopped

# Measured (Task 1a) — the dry-run's ETA and storage model.
MEASURED_SERIAL_SECONDS_PER_FILING = 1.91   # gold cohort run, 924 requests / 308 filings
PRELIMINARY_SHARE = 1 / 3                   # 362 of 1,058 fetched so far were preliminaries
AVG_STORED_DOC_BYTES = 84 * 1024            # gzipped document in R2
AVG_STORED_TEXT_BYTES = 21 * 1024           # gzipped text in R2
R2_FREE_GB = 10.0
R2_PRICE_PER_GB_MONTH = 0.015

# The operator's --kinds vocabulary -> the classifier's document_kind values
# (edgar_index_filings_document_kind_chk). The classifier has NO
# 'prospectus_supplement' kind (Task 1c): a base prospectus supplement has no
# pricing/product/underlying/term-sheet title phrase and lands in 'other', so
# 'prospectus_supplement' selects 'other'.
CLI_KINDS: dict[str, tuple[str, ...]] = {
    "final": ("pricing_supplement",),
    "preliminary": ("preliminary_pricing_supplement",),
    "product_supplement": ("product_supplement",),
    "underlying_supplement": ("underlying_supplement",),
    "term_sheet": ("term_sheet",),
    "prospectus_supplement": ("other",),
    "other": ("other",),
}

# Named classifiers a source row may reference. Code is code; which one a
# source uses is data.
CLASSIFIERS: dict[str, Callable[[str], tuple[str, str]]] = {
    "structured_note_cover": edgar_pipeline.classify_document,
}

_SUBMISSION_CIK_RE = re.compile(r"edgar/data/(\d+)/")
_FORM_TYPE_RE = re.compile(r"<TYPE>\s*([0-9A-Z/\-]+)", re.IGNORECASE)
_CONTENT_RANGE_RE = re.compile(r"bytes\s+\d+-\d+/(\d+)", re.IGNORECASE)


class BulkFetchError(RuntimeError):
    """A run-level problem (configuration, usage). Nothing was half-done."""


class BulkUsageError(BulkFetchError):
    """The operator asked for something that cannot run (unknown bank, bad dates)."""


class SecRequestFailed(RuntimeError):
    """One SEC request failed SEC_ATTEMPTS times (or a non-retryable status)."""


# ═══ Sources ════════════════════════════════════════════════════════════════

@dataclass
class Source:
    name: str
    description: str
    form_types: tuple[str, ...]
    filer_source: str
    filer_include_status: tuple[str, ...]
    filer_ciks: tuple[str, ...]
    classifier: str

    def classify(self, text: str) -> tuple[str, str]:
        fn = CLASSIFIERS.get(self.classifier)
        if fn is None:
            raise BulkFetchError(
                f"source {self.name!r} names classifier {self.classifier!r}, which this code does not "
                f"implement (known: {sorted(CLASSIFIERS)})")
        return fn(text)


def _source_from_row(r) -> Source:
    return Source(
        name=r["name"], description=r["description"], form_types=tuple(r["form_types"] or ()),
        filer_source=r["filer_source"], filer_include_status=tuple(r["filer_include_status"] or ()),
        filer_ciks=tuple(r["filer_ciks"] or ()), classifier=r["classifier"],
    )


async def load_sources(conn) -> list[Source]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT * FROM portfolio.edgar_sources WHERE is_active ORDER BY name")
    return [_source_from_row(r) for r in rows]


async def load_source(conn, name: str) -> Source:
    sources = {s.name: s for s in await load_sources(conn)}
    if name not in sources:
        raise BulkUsageError(f"unknown source {name!r}; active sources: {sorted(sources) or 'none'}")
    src = sources[name]
    src.classify("")   # fail loud now on an unknown classifier, not per filing
    return src


# ═══ Institutions (issuer groups) -> filer entities ═════════════════════════

@dataclass
class Filer:
    cik: str
    filer_name: str
    filer_role: str | None = None
    credit_entity: str | None = None


@dataclass
class Institution:
    name: str
    filers: list[Filer]

    @property
    def ciks(self) -> list[str]:
        return [f.cik for f in self.filers]


async def institution_catalog(conn, source: Source) -> dict[str, Institution]:
    """Every institution the source can fetch, keyed by name (exact case)."""
    if source.filer_source == "explicit":
        return {source.name: Institution(source.name, [Filer(c, c) for c in source.filer_ciks])}
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT filer_cik, issuer_group, filer_name, filer_role, credit_entity
                 FROM portfolio.structured_note_issuers
                WHERE include_status = ANY($1::text[])
                ORDER BY issuer_group, filer_cik""",
            list(source.filer_include_status))
    out: dict[str, Institution] = {}
    for r in rows:
        out.setdefault(r["issuer_group"], Institution(r["issuer_group"], [])).filers.append(
            Filer(r["filer_cik"], r["filer_name"], r["filer_role"], r["credit_entity"]))
    return out


def parse_bank_names(raw: str | None) -> list[str]:
    return [b.strip() for b in (raw or "").split(",") if b.strip()]


async def resolve_institutions(conn, source: Source, names: list[str]) -> list[Institution]:
    """``--banks`` names (case-insensitive) -> institutions, in the order given.
    An unknown name is an error that lists every valid one."""
    catalog = await institution_catalog(conn, source)
    by_fold = {k.casefold(): v for k, v in catalog.items()}
    if not names:
        raise BulkUsageError("--banks is required; valid names: " + ", ".join(sorted(catalog)))
    unknown = [n for n in names if n.casefold() not in by_fold]
    if unknown:
        raise BulkUsageError(
            f"unknown institution(s) {unknown} for source {source.name!r}. Valid names: "
            + ", ".join(sorted(catalog)))
    out, seen = [], set()
    for n in names:
        inst = by_fold[n.casefold()]
        if inst.name not in seen:
            seen.add(inst.name)
            out.append(inst)
    return out


# ═══ Scope ══════════════════════════════════════════════════════════════════

@dataclass
class Scope:
    source: Source
    institutions: list[Institution]
    date_from: date
    date_to: date
    order: str = "newest"
    retry_failed: bool = False
    refetch_peek_skipped: bool = False

    def __post_init__(self):
        if self.order not in ("newest", "oldest"):
            raise BulkUsageError("--order must be newest or oldest")
        if self.date_from > self.date_to:
            raise BulkUsageError(f"--from {self.date_from} is after --to {self.date_to}")

    @property
    def statuses(self) -> list[str]:
        return ["selected"] + (["fetch_failed"] if self.retry_failed else [])


# The WHERE that defines "in scope and still to do" — one place, used by the
# counts, the dry-run listing and the claim, so they can never disagree.
_SCOPE_WHERE = """
    f.primary_issuer_cik = ANY($1::text[])
    AND f.form_type = ANY($2::text[])
    AND f.filing_date BETWEEN $3::date AND $4::date
    AND (f.pipeline_status = ANY($5::text[])
         OR ($6::boolean AND f.pipeline_status = 'not_pricing_supplement'
             AND f.reference_filing_id IS NULL AND f.status_reason LIKE 'peek:%'))
"""


def _scope_args(scope: Scope, ciks: list[str]) -> list:
    return [ciks, list(scope.source.form_types), scope.date_from, scope.date_to,
            scope.statuses, scope.refetch_peek_skipped]


async def last_indexed_date(conn) -> date | None:
    async with platform_scope(conn):
        return await conn.fetchval("SELECT max(filing_date) FROM portfolio.edgar_index_filings")


async def check_dates_indexed(conn, date_to: date) -> date | None:
    """Refuse a --to after the newest indexed filing date. Returns that date."""
    last = await last_indexed_date(conn)
    if last is None or date_to > last:
        raise BulkUsageError(
            f"--to {date_to} is after the last indexed filing date ({last}). The index has no rows "
            f"for those days yet, so they would silently be missing from the scope. Run first:\n"
            f"    run_bulk_fetch.py --refresh-index --to {date_to}")
    return last


async def scope_counts(conn, scope: Scope) -> dict:
    """{institution: {"filers": {cik: n}, "years": {year: n}, "total": n}} for the
    rows still to do."""
    out = {}
    for inst in scope.institutions:
        async with platform_scope(conn):
            rows = await conn.fetch(
                f"""SELECT f.primary_issuer_cik AS cik, extract(year FROM f.filing_date)::int AS y,
                           count(*)::int AS n
                      FROM portfolio.edgar_index_filings f
                     WHERE {_SCOPE_WHERE}
                     GROUP BY 1, 2""",
                *_scope_args(scope, inst.ciks))
        filers = {c: 0 for c in inst.ciks}
        years: dict[int, int] = defaultdict(int)
        for r in rows:
            filers[r["cik"]] = filers.get(r["cik"], 0) + r["n"]
            years[r["y"]] += r["n"]
        out[inst.name] = {"filers": filers, "years": dict(sorted(years.items())),
                          "total": sum(filers.values())}
    return out


async def scope_accessions(conn, scope: Scope, *, per_institution_limit: int | None = None) -> list[dict]:
    """The rows still to do, in run order: institution (as given), then newest
    (or oldest) first. Read-only."""
    direction = "DESC" if scope.order == "newest" else "ASC"
    out = []
    for inst in scope.institutions:
        lim = f"LIMIT {int(per_institution_limit)}" if per_institution_limit else ""
        async with platform_scope(conn):
            rows = await conn.fetch(
                f"""SELECT f.accession_number, f.filing_date, f.primary_issuer_cik, f.form_type
                      FROM portfolio.edgar_index_filings f
                     WHERE {_SCOPE_WHERE}
                     ORDER BY f.filing_date {direction}, f.accession_number {direction} {lim}""",
                *_scope_args(scope, inst.ciks))
        out += [dict(r) | {"institution": inst.name} for r in rows]
    return out


# ═══ ETA and storage model (dry-run) ════════════════════════════════════════

def requests_per_filing(peek: bool, prelim_share: float = PRELIMINARY_SHARE) -> float:
    """SEC requests per filing. Without the peek: index.json + header + document
    = 3 (measured). With it: a kept filing costs index.json + peek + document +
    header = 4, a peek-skipped one index.json + peek = 2 (its header is never
    read)."""
    if not peek:
        return 3.0
    return (1 - prelim_share) * 4 + prelim_share * 2


def estimate(n: int, *, rate: float, concurrency: int, peek: bool,
             prelim_share: float = PRELIMINARY_SHARE, stored_bytes_now: int = 0) -> dict:
    """ETA and R2 storage for ``n`` filings. The ETA is the larger of the rate
    bound (requests / rate) and the latency bound (the measured serial
    per-filing time spread over the workers)."""
    req = requests_per_filing(peek, prelim_share)
    rate_bound = n * req / max(rate, 0.001)
    latency_bound = n * MEASURED_SERIAL_SECONDS_PER_FILING / max(concurrency, 1)
    seconds = max(rate_bound, latency_bound)
    kept = n * (1 - prelim_share) if peek else n
    new_bytes = kept * (AVG_STORED_DOC_BYTES + AVG_STORED_TEXT_BYTES)
    total_gb = (stored_bytes_now + new_bytes) / 1024 ** 3
    billable = max(0.0, total_gb - R2_FREE_GB)
    return {
        "filings": n, "requests_per_filing": round(req, 2), "requests": int(round(n * req)),
        "rate_bound_seconds": round(rate_bound), "latency_bound_seconds": round(latency_bound),
        "eta_seconds": round(seconds), "eta_hours": round(seconds / 3600, 1),
        "bound": "rate limit" if rate_bound >= latency_bound else "per-filing latency",
        "kept_filings": int(round(kept)), "new_storage_gb": round(new_bytes / 1024 ** 3, 2),
        "total_storage_gb": round(total_gb, 2),
        "monthly_cost_usd": round(billable * R2_PRICE_PER_GB_MONTH, 2),
    }


async def stored_bytes(conn) -> int:
    async with platform_scope(conn):
        return int(await conn.fetchval(
            """SELECT COALESCE(sum(COALESCE(compressed_byte_size, byte_size, 0)
                               + COALESCE(text_compressed_byte_size, 0)), 0)
                 FROM portfolio.reference_filings""") or 0)


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


async def dry_run(conn, scope: Scope, *, rate: float, concurrency: int, peek: bool,
                  limit: int | None = None, out=print) -> dict:
    """Print the scope per institution (every filer entity with its CIK and
    count, then per year), the ETA and the storage estimate. WRITES NOTHING."""
    counts = await scope_counts(conn, scope)
    total = sum(c["total"] for c in counts.values())
    n = min(total, limit) if limit else total
    out(f"source {scope.source.name}: forms {list(scope.source.form_types)}, "
        f"filed {scope.date_from} .. {scope.date_to} (inclusive), order {scope.order}")
    for inst in scope.institutions:
        c = counts[inst.name]
        out(f"\n{inst.name}: {c['total']:,} filings to fetch")
        for f in inst.filers:
            out(f"    CIK {f.cik:<10} {f.filer_name:<45} {f.filer_role or '':<10} {c['filers'].get(f.cik, 0):>8,}")
        for y, k in c["years"].items():
            out(f"    {y}: {k:,}")
    est = estimate(n, rate=rate, concurrency=concurrency, peek=peek,
                   stored_bytes_now=await stored_bytes(conn))
    out(f"\nTotal in scope: {total:,}" + (f" (limited to {n:,})" if limit else ""))
    out(f"ETA at {rate:g} req/s, {concurrency} workers, peek {'on' if peek else 'off'}: "
        f"{_fmt_eta(est['eta_seconds'])} ({est['requests']:,} SEC requests, "
        f"{est['requests_per_filing']} per filing; bound by {est['bound']})")
    out(f"R2: ~{est['new_storage_gb']} GB new ({est['kept_filings']:,} filings kept, assuming "
        f"{PRELIMINARY_SHARE:.0%} preliminaries skipped by the peek), ~{est['total_storage_gb']} GB total, "
        f"~${est['monthly_cost_usd']}/month at ${R2_PRICE_PER_GB_MONTH}/GB-month beyond {R2_FREE_GB:g} GB")
    out("Dry run: nothing was written.")
    return {"counts": counts, "total": total, "estimate": est}


# ═══ SEC requests: one limiter, bounded retries ═════════════════════════════

class SecClient:
    """Every SEC request of a run. Each attempt first takes a slot from
    ``edgar_fetch``'s ONE module limiter — shared by all workers in the process.
    429 / 5xx / connection errors back off exponentially (``Retry-After``
    honoured, capped); SEC_ATTEMPTS failures raise :class:`SecRequestFailed`."""

    def __init__(self, client: httpx.AsyncClient, *, attempts: int = SEC_ATTEMPTS,
                 backoff_base: float | None = None, sleep=asyncio.sleep):
        self.client = client
        self.attempts = attempts
        self.backoff_base = BACKOFF_BASE if backoff_base is None else backoff_base
        self.sleep = sleep
        self.requests = 0
        self.retries = 0
        self.seconds = 0.0

    async def get(self, url: str, *, range_bytes: int | None = None) -> httpx.Response:
        headers = {"User-Agent": edgar_fetch.user_agent()}
        if range_bytes:
            headers["Range"] = f"bytes=0-{int(range_bytes) - 1}"
            headers["Accept-Encoding"] = "identity"
        last: str = ""
        for attempt in range(self.attempts):
            await edgar_fetch._limiter.acquire()
            self.requests += 1
            t = time.monotonic()
            try:
                r = await self.client.get(url, headers=headers)
            except httpx.TransportError as exc:
                self.seconds += time.monotonic() - t
                last = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < self.attempts:
                    self.retries += 1
                    await self.sleep(self.backoff_base * 2 ** attempt)
                continue
            self.seconds += time.monotonic() - t
            if r.status_code in RETRY_STATUSES:
                last = f"HTTP {r.status_code}"
                if attempt + 1 < self.attempts:
                    delay = self.backoff_base * 2 ** attempt
                    try:
                        delay = max(delay, min(float(r.headers.get("Retry-After") or 0), RETRY_AFTER_CAP))
                    except ValueError:
                        pass
                    self.retries += 1
                    await self.sleep(delay)
                continue
            if r.status_code >= 400:
                raise SecRequestFailed(f"HTTP {r.status_code} (not retried) from {url}")
            return r
        raise SecRequestFailed(f"failed after {self.attempts} attempts ({last}) from {url}")


# ═══ The peek ═══════════════════════════════════════════════════════════════

@dataclass
class PeekResult:
    status_code: int
    bytes_received: int
    total_size: int | None
    honoured: bool          # 206 Partial Content
    kind: str
    reason: str
    body: bytes


def classify_peek_bytes(body: bytes, classify) -> tuple[str, str]:
    """The source's classifier on the text of the first bytes of a document."""
    text = edgar_fetch.extract_filing_text(body).text if body else ""
    return classify(text)


async def peek(sec: SecClient, url: str, classify, nbytes: int = PEEK_BYTES) -> PeekResult:
    r = await sec.get(url, range_bytes=nbytes)
    body = r.content
    total = None
    m = _CONTENT_RANGE_RE.search(r.headers.get("Content-Range") or "")
    if m:
        total = int(m.group(1))
    honoured = r.status_code == 206
    if honoured:
        kind, reason = classify_peek_bytes(body, classify)
    else:
        kind, reason = classify(edgar_fetch.extract_filing_text(body).text if body else "")
    return PeekResult(r.status_code, len(body), total if honoured else len(body), honoured, kind, reason, body)


def wanted_document_kinds(cli_kinds: list[str]) -> set[str]:
    unknown = [k for k in cli_kinds if k not in CLI_KINDS]
    if unknown:
        raise BulkUsageError(f"unknown --kinds {unknown}; valid: {sorted(CLI_KINDS)}")
    return {dk for k in cli_kinds for dk in CLI_KINDS[k]}


def peek_decision(kind: str, wanted: set[str], keep_preliminaries: bool) -> str:
    """'fetch' or 'skip' for a peeked cover. 'other' (unrecognised) is ALWAYS
    fetched: the peek must never lose a filing it cannot read."""
    if kind == "other" or kind in wanted:
        return "fetch"
    if kind == "preliminary_pricing_supplement" and keep_preliminaries:
        return "fetch"
    return "skip"


# ═══ Product supplements (Task 5) ═══════════════════════════════════════════

_ARCHIVE_URL_RE = re.compile(
    r"https?://(?:www\.)?sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/([^\s\"'<>#?]*)", re.IGNORECASE)
_HREF_RE = re.compile(
    r"""<a\b[^>]*href\s*=\s*["']?(https?://(?:www\.)?sec\.gov/Archives/edgar/data/\d+/\d{18}/[^"'\s>#?]*)[^>]*>"""
    r"(.*?)</a>", re.IGNORECASE | re.DOTALL)
_LABEL_BREAK_RE = re.compile(r"[\n\r■•●♦▪◦]")
_DOC_SUFFIXES = (".htm", ".html", ".txt")
_TAG_RE = re.compile(r"<[^>]+>")
_PS_NAME_DATE_RE = re.compile(
    r"product\s+supplement\s+(?:no\.?\s*)?(?P<name>[A-Z0-9][A-Z0-9\-\.]*)?[^.;]{0,80}?\bdated\s+"
    r"(?P<date>[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE)


@dataclass
class SupplementRef:
    method: str                    # 'link' | 'name_date'
    accession: str | None = None
    cik: str | None = None
    document: str | None = None
    url: str | None = None
    name: str | None = None
    dated: date | None = None
    reference_text: str | None = None


def _acc_from_nodash(nodash: str) -> str:
    return f"{nodash[:10]}-{nodash[10:12]}-{nodash[12:]}"


def _label_before(text: str, start: int, floor: int) -> str:
    """The label line a URL belongs to: the text before it, back to the last
    line break / bullet or the previous URL (at most 200 characters)."""
    seg = text[max(floor, start - 200):start]
    parts = _LABEL_BREAK_RE.split(seg)
    label = next((x for x in reversed(parts) if x.strip()), "")
    return " ".join(_TAG_RE.sub(" ", label).split())


def _ref_from_url(url: str, cik: str, nodash: str, doc: str, label: str) -> SupplementRef:
    doc = doc if doc.lower().endswith(_DOC_SUFFIXES) else None    # a .pdf or a broken name: use the folder
    return SupplementRef("link", accession=_acc_from_nodash(nodash), cik=str(int(cik)), document=doc,
                         url=url, reference_text=label[:300])


def find_supplement_link(text: str, html: str | None = None) -> SupplementRef | None:
    """The product supplement's own SEC link. Rules only, two shapes, both seen
    in the stored 2025Q1 corpus (90 of its 171 424B2 texts print sec.gov
    archive URLs):

      * a printed list — "Product supplement no. 4-I dated April 13, 2023:"
        then the URL on the next line. The URL's OWN label line (back to the
        previous line break, bullet or URL) must say "product supplement"; a
        paragraph that merely mentions "the related product supplement" above
        a prospectus link does not count.
      * an HTML anchor whose anchor text says "product supplement".

    A link to a .pdf (JPMorgan prints those) keeps the accession and drops the
    document name: the folder index picks the HTML body."""
    prev = 0
    for m in _ARCHIVE_URL_RE.finditer(text or ""):
        label = _label_before(text, m.start(), prev)
        prev = m.end()
        if "product supplement" in label.lower():
            return _ref_from_url(m.group(0), m.group(1), m.group(2), m.group(3), label)
    for m in _HREF_RE.finditer(html or ""):
        anchor = " ".join(_TAG_RE.sub(" ", m.group(2)).split())
        if "product supplement" in anchor.lower():
            um = _ARCHIVE_URL_RE.match(m.group(1))
            if um:
                return _ref_from_url(um.group(0), um.group(1), um.group(2), um.group(3), anchor)
    return None


def _parse_long_date(s: str) -> date | None:
    s = s.replace(".", "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def find_supplement_name_date(text: str) -> SupplementRef | None:
    """'product supplement no. 4-I dated April 13, 2023' -> name + date."""
    m = _PS_NAME_DATE_RE.search(text or "")
    if not m:
        return None
    d = _parse_long_date(m.group("date"))
    if d is None:
        return None
    return SupplementRef("name_date", name=(m.group("name") or None), dated=d,
                         reference_text=" ".join(m.group(0).split())[:300])


async def resolve_name_date(conn, ref: SupplementRef, issuer_ciks: list[str]) -> SupplementRef:
    """Name + date -> an accession: a manifest filing by the same issuer group,
    filed within 3 days of the stated date, already classified
    product_supplement. Never guesses beyond that; unresolved stays unresolved."""
    if ref.dated is None:
        return ref
    async with platform_scope(conn):
        row = await conn.fetchrow(
            """SELECT accession_number, submission_path FROM portfolio.edgar_index_filings
                WHERE primary_issuer_cik = ANY($1::text[])
                  AND filing_date BETWEEN $2::date - 3 AND $2::date + 3
                  AND document_kind = 'product_supplement'
                ORDER BY abs(filing_date - $2::date), accession_number LIMIT 1""",
            issuer_ciks, ref.dated)
    if row:
        m = _SUBMISSION_CIK_RE.search(row["submission_path"] or "")
        ref.accession = row["accession_number"]
        ref.cik = str(int(m.group(1))) if m else None
    return ref


async def upsert_supplement_link(conn, note_ref_id, ref: SupplementRef | None,
                                 supplement_ref_id=None) -> None:
    method = ref.method if (ref and ref.accession) else "unresolved"
    async with platform_scope(conn):
        await conn.execute(
            """INSERT INTO portfolio.reference_filing_supplements
                   (reference_filing_id, supplement_kind, resolution_method, supplement_accession,
                    supplement_cik, supplement_document, supplement_url, supplement_reference_filing_id,
                    reference_text)
               VALUES ($1, 'product_supplement', $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (reference_filing_id, supplement_kind) DO UPDATE SET
                   resolution_method = EXCLUDED.resolution_method,
                   supplement_accession = EXCLUDED.supplement_accession,
                   supplement_cik = EXCLUDED.supplement_cik,
                   supplement_document = EXCLUDED.supplement_document,
                   supplement_url = EXCLUDED.supplement_url,
                   supplement_reference_filing_id = COALESCE(EXCLUDED.supplement_reference_filing_id,
                       portfolio.reference_filing_supplements.supplement_reference_filing_id),
                   reference_text = EXCLUDED.reference_text, updated_at = now()""",
            note_ref_id, method,
            ref.accession if method != "unresolved" else None,
            ref.cik if method != "unresolved" else None,
            ref.document if ref else None, ref.url if ref else None, supplement_ref_id,
            (ref.reference_text if ref else None))


@dataclass
class SupplementStats:
    referenced: int = 0            # finals that referenced one (link or name+date found)
    distinct_found: int = 0
    fetched: int = 0
    already_stored: int = 0
    unresolved: int = 0
    failed: int = 0
    links: int = 0


class SupplementFetcher:
    """Each distinct product supplement is fetched ONCE per run, and never if a
    reference_filings row already holds it. A per-accession lock means two
    workers whose finals cite the same supplement make one fetch between them."""

    def __init__(self, sec: SecClient, bucket: str, r2_prefix: str, stats: SupplementStats):
        self.sec, self.bucket, self.r2_prefix, self.stats = sec, bucket, r2_prefix, stats
        self._locks: dict[str, asyncio.Lock] = {}
        self._ids: dict[str, object] = {}
        self.bytes_uploaded = 0

    async def ensure(self, conn, ref: SupplementRef):
        acc = ref.accession
        lock = self._locks.setdefault(acc, asyncio.Lock())
        async with lock:
            if acc in self._ids:
                return self._ids[acc]
            self.stats.distinct_found += 1
            async with platform_scope(conn):
                existing = await conn.fetchval(
                    "SELECT id FROM portfolio.reference_filings WHERE accession_number = $1 "
                    "ORDER BY created_at LIMIT 1", acc)
            if existing:
                self.stats.already_stored += 1
                self._ids[acc] = existing
                return existing
            ref_id = await self._fetch(conn, ref)
            self._ids[acc] = ref_id
            return ref_id

    async def _fetch(self, conn, ref: SupplementRef):
        cik = ref.cik
        base = edgar_fetch.ARCHIVE_BASE.format(cik=cik, accession_nodash=ref.accession.replace("-", ""))
        doc = ref.document
        if not doc:
            listing = await self.sec.get(f"{base}/index.json")
            items = (listing.json().get("directory") or {}).get("item") or []
            doc = edgar_fetch._pick_primary_document(items, "424B2")
            if not doc:
                raise SecRequestFailed(f"supplement {ref.accession}: folder lists no document")
        header = await self.sec.get(f"{base}/{ref.accession}-index-headers.html")
        form = (_FORM_TYPE_RE.search(header.text).group(1).upper()
                if _FORM_TYPE_RE.search(header.text) else "424B2")
        fn = edgar_fetch._FILE_NUMBER_RE.search(header.text)
        raw = (await self.sec.get(f"{base}/{doc}")).content
        async with platform_scope(conn):
            row = await conn.fetchrow(
                "SELECT filing_date, form_type FROM portfolio.edgar_index_filings WHERE accession_number = $1",
                ref.accession)
        meta = edgar_fetch.FilingMeta(
            cik=cik, filer_name="(product supplement)", form_type=(row["form_type"] if row else form),
            accession_number=ref.accession,
            filing_date=(row["filing_date"] if row else (ref.dated or date.today())),
            submission_path=f"edgar/data/{cik}/{ref.accession}.txt", primary_document=doc,
            file_number=fn.group(1).strip() if fn else None)
        out = await edgar_pipeline.store_fetched(conn, meta, raw, bucket=self.bucket, r2_prefix=self.r2_prefix)
        self.stats.fetched += 1
        self.bytes_uploaded += out["bytes_uploaded"]
        return out["reference_filing_id"]


# ═══ Run bookkeeping ════════════════════════════════════════════════════════

@dataclass
class RunOptions:
    source: str = DEFAULT_SOURCE
    banks: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    kinds: list[str] = field(default_factory=lambda: ["final"])
    concurrency: int = 6
    max_rps: int = DEFAULT_MAX_RPS
    allow_above_8: bool = False
    limit: int | None = None
    order: str = "newest"
    stop_after_failures: int = DEFAULT_STOP_AFTER
    peek: bool = True
    keep_preliminaries: bool = False
    retry_failed: bool = False
    refetch_peek_skipped: bool = False
    r2_prefix: str = edgar_pipeline.R2_PREFIX
    bucket: str | None = None
    trigger_source: str = "cli"


def check_rate(max_rps: int, allow_above_8: bool) -> int:
    if max_rps < 1:
        raise BulkUsageError("--max-rps must be at least 1")
    if max_rps > ABSOLUTE_MAX_RPS:
        raise BulkUsageError(f"--max-rps {max_rps} is above the SEC's own limit of {ABSOLUTE_MAX_RPS}/s")
    if max_rps > DEFAULT_MAX_RPS and not allow_above_8:
        raise BulkUsageError(
            f"--max-rps {max_rps} is above the agreed ceiling of {DEFAULT_MAX_RPS}/s across all workers. "
            "Pass --allow-above-8 to override deliberately.")
    return max_rps


@dataclass
class RunStats:
    run_id: object = None
    started: float = field(default_factory=time.monotonic)
    scope_total: int = 0
    claimed: int = 0
    done: int = 0
    fetched: int = 0
    failed: int = 0
    peek_skipped: int = 0
    peek_fallthrough: int = 0
    peek_range_ignored: int = 0
    bytes_uploaded: int = 0
    bytes_downloaded: int = 0
    bytes_saved_by_peek: int = 0
    consecutive_failures: int = 0
    stop_reason: str | None = None
    by_status: Counter = field(default_factory=Counter)
    by_kind: Counter = field(default_factory=Counter)
    by_institution_kind: dict = field(default_factory=lambda: defaultdict(Counter))
    timings: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)
    supplements: SupplementStats = field(default_factory=SupplementStats)
    effective_rps: int = 0
    live_runners: int = 1

    def summary(self) -> dict:
        return {
            "scope_total": self.scope_total, "claimed": self.claimed, "done": self.done,
            "fetched": self.fetched, "failed": self.failed, "peek_skipped": self.peek_skipped,
            "peek_fallthrough": self.peek_fallthrough, "peek_range_ignored": self.peek_range_ignored,
            "bytes_uploaded": self.bytes_uploaded, "bytes_downloaded": self.bytes_downloaded,
            "bytes_saved_by_peek": self.bytes_saved_by_peek, "by_status": dict(self.by_status),
            "by_kind": dict(self.by_kind),
            "by_institution_kind": {k: dict(v) for k, v in self.by_institution_kind.items()},
            "timings_seconds": {k: round(v, 2) for k, v in self.timings.items()},
            "supplements": vars(self.supplements), "stop_reason": self.stop_reason,
            "effective_rps": self.effective_rps, "live_runners": self.live_runners,
            "failures": self.failures[:50],
        }


async def create_run_row(conn, opts: RunOptions, scope_total: int, details: dict):
    async with platform_scope(conn):
        return await conn.fetchval(
            """INSERT INTO portfolio.edgar_pipeline_runs
                   (trigger_source, status, stages, fetch_cap, runtime_cap_seconds, run_kind,
                    started_at, details)
               VALUES ($1, 'running', ARRAY['fetch'], $2, $3, 'fetch', now(), $4::jsonb)
               RETURNING id""",
            opts.trigger_source, min(int(opts.limit or scope_total), 2_000_000_000),
            RUNTIME_CAP_SECONDS, json.dumps({"bulk": details}, default=str))


async def register_runner(conn, run_id) -> int:
    """Take this run's heartbeat lease; return the number of live SEC runners
    (bulk runners + the nightly pipeline job) including this one."""
    await edgar_pipeline.acquire_lease(conn, str(run_id), LEASE_TTL_SECONDS, f"{LEASE_PREFIX}{run_id}")
    async with platform_scope(conn):
        n = await conn.fetchval(
            """SELECT count(*) FROM portfolio.edgar_pipeline_lease
                WHERE expires_at > now()
                  AND (lease_name LIKE $1 OR lease_name = $2)""",
            LEASE_PREFIX + "%", edgar_pipeline.LEASE_NAME)
    return max(1, int(n or 1))


async def release_runner(conn, run_id) -> None:
    async with platform_scope(conn):
        await conn.execute("DELETE FROM portfolio.edgar_pipeline_lease WHERE lease_name = $1",
                           f"{LEASE_PREFIX}{run_id}")


# ═══ Claims ═════════════════════════════════════════════════════════════════

async def claim_batch(conn, run_id, scope: Scope, inst: Institution, n: int,
                      cursor: tuple[date, str] | None) -> list[dict]:
    """Claim up to ``n`` of the institution's next rows after ``cursor`` (keyset,
    in run order). One UPDATE ... WHERE accession IN (SELECT ... FOR UPDATE
    SKIP LOCKED): rows another runner holds are skipped, never waited on."""
    newest = scope.order == "newest"
    cmp, direction = ("<", "DESC") if newest else (">", "ASC")
    if cursor is None:
        cursor = ((scope.date_to + timedelta(days=1)) if newest else (scope.date_from - timedelta(days=1)), "")
    async with platform_scope(conn):
        rows = await conn.fetch(
            f"""UPDATE portfolio.edgar_index_filings f
                   SET claimed_by = $7::uuid,
                       claimed_until = now() + make_interval(secs => $8)
                 WHERE f.accession_number IN (
                       SELECT f.accession_number FROM portfolio.edgar_index_filings f
                        WHERE {_SCOPE_WHERE}
                          AND (f.claimed_until IS NULL OR f.claimed_until <= now())
                          AND (f.filing_date, f.accession_number) {cmp} ($9::date, $10::text)
                        ORDER BY f.filing_date {direction}, f.accession_number {direction}
                        LIMIT $11
                        FOR UPDATE SKIP LOCKED)
             RETURNING f.accession_number, f.form_type, f.filing_date, f.submission_path,
                       f.primary_issuer_cik, f.attempt_count, f.pipeline_status""",
            *_scope_args(scope, inst.ciks), run_id, float(CLAIM_TTL_SECONDS), cursor[0], cursor[1], int(n))
    out = sorted((dict(r) for r in rows), key=lambda r: (r["filing_date"], r["accession_number"]),
                 reverse=newest)
    return out


async def release_claims(conn, run_id, accessions: list[str] | None = None) -> int:
    async with platform_scope(conn):
        if accessions is None:
            res = await conn.execute(
                "UPDATE portfolio.edgar_index_filings SET claimed_by = NULL, claimed_until = NULL "
                "WHERE claimed_by = $1", run_id)
        else:
            res = await conn.execute(
                "UPDATE portfolio.edgar_index_filings SET claimed_by = NULL, claimed_until = NULL "
                "WHERE claimed_by = $1 AND accession_number = ANY($2::text[])", run_id, accessions)
    return int(res.split()[-1])


async def record_peek_skip(conn, run_id, row: dict, p: PeekResult) -> None:
    async with platform_scope(conn):
        await conn.execute(
            """UPDATE portfolio.edgar_index_filings
                  SET pipeline_status = 'not_pricing_supplement', document_kind = $2,
                      status_reason = left('peek: ' || $3 || ' (cover only, ' || $4::int::text
                                           || ' bytes read; not downloaded)', 1500),
                      last_attempt_at = now(), claimed_by = NULL, claimed_until = NULL
                WHERE accession_number = $1 AND claimed_by = $5""",
            row["accession_number"], p.kind, f"{p.kind}: {p.reason}", p.bytes_received, run_id)


async def record_fetch_failed(conn, row: dict, reason: str) -> None:
    """fetch_failed with the reason; not scheduled for the nightly retry —
    ``--retry-failed`` brings it back into a bulk run."""
    async with platform_scope(conn):
        await conn.execute(
            """UPDATE portfolio.edgar_index_filings
                  SET pipeline_status = 'fetch_failed', status_reason = left($2, 1500),
                      attempt_count = attempt_count + 1, last_attempt_at = now(), next_attempt_at = NULL,
                      claimed_by = NULL, claimed_until = NULL
                WHERE accession_number = $1""",
            row["accession_number"], reason)


# ═══ The runner ═════════════════════════════════════════════════════════════

_SYSTEMIC = (edgar_fetch.EdgarConfigError, edgar_pipeline.EdgarPipelineConfigError, BulkFetchError)


def _is_storage_error(exc: BaseException) -> bool:
    return type(exc).__module__.startswith(("botocore", "boto3"))


class Runner:
    def __init__(self, pool, opts: RunOptions, *, client: httpx.AsyncClient, out=print,
                 stop_event: asyncio.Event | None = None, sleep=asyncio.sleep,
                 backoff_base: float | None = None, after_filing=None):
        self.pool, self.opts, self.out = pool, opts, out
        self.stop = stop_event or asyncio.Event()
        self.sec = SecClient(client, backoff_base=backoff_base, sleep=sleep)
        self.stats = RunStats()
        self.after_filing = after_filing      # verify hook: awaited after each filing
        self._lock = asyncio.Lock()

    def _tick(self, step: str, seconds: float) -> None:
        self.stats.timings[step] = self.stats.timings.get(step, 0.0) + seconds

    async def run(self) -> RunStats:
        o = self.opts
        check_rate(o.max_rps, o.allow_above_8)
        if o.concurrency < 1:
            raise BulkUsageError("--concurrency must be at least 1")
        wanted = wanted_document_kinds(o.kinds)
        async with self.pool.acquire() as conn:
            source = await load_source(conn, o.source)
            insts = await resolve_institutions(conn, source, o.banks)
            await check_dates_indexed(conn, o.date_to)
            scope = Scope(source, insts, o.date_from, o.date_to, o.order, o.retry_failed,
                          o.refetch_peek_skipped)
            counts = await scope_counts(conn, scope)
            self.stats.scope_total = sum(c["total"] for c in counts.values())
            try:
                policy_rules = (await edgar_pipeline.active_policy(conn))["rules"]
            except edgar_pipeline.EdgarPipelineError:
                policy_rules = {}
            bucket = o.bucket or edgar_pipeline.r2_bucket()
            details = {"source": source.name, "institutions": [i.name for i in insts],
                       "from": o.date_from, "to": o.date_to, "kinds": o.kinds, "peek": o.peek,
                       "keep_preliminaries": o.keep_preliminaries, "concurrency": o.concurrency,
                       "max_rps": o.max_rps, "order": o.order, "limit": o.limit,
                       "scope_total": self.stats.scope_total}
            run_id = await create_run_row(conn, o, self.stats.scope_total, details)
            self.stats.run_id = run_id
            live = await register_runner(conn, run_id)
        self.stats.live_runners = live
        self.stats.effective_rps = max(1, o.max_rps // live)
        edgar_fetch.set_rate_limit(self.stats.effective_rps)
        self.out(f"run {run_id}: {self.stats.scope_total:,} filings in scope; {live} live SEC runner(s) "
                 f"-> this run at {self.stats.effective_rps} req/s, {o.concurrency} workers, "
                 f"peek {'on' if o.peek else 'off'}")
        self.scope, self.insts, self.wanted, self.bucket = scope, insts, wanted, bucket
        self.policy_rules = policy_rules
        self.inst_of = {c: i.name for i in insts for c in i.ciks}
        self.want_supplements = "product_supplement" in o.kinds
        self.supplements = SupplementFetcher(self.sec, bucket, o.r2_prefix, self.stats.supplements)
        self.group_ciks = {i.name: i.ciks for i in insts}

        queue: asyncio.Queue = asyncio.Queue(maxsize=max(o.concurrency * 2, CLAIM_BATCH))
        error: list[BaseException] = []
        try:
            producer = asyncio.create_task(self._produce(queue, error))
            workers = [asyncio.create_task(self._work(queue, error)) for _ in range(o.concurrency)]
            await asyncio.gather(producer, *workers)
        finally:
            async with self.pool.acquire() as conn:
                await release_claims(conn, run_id)
                await release_runner(conn, run_id)
                s = self.stats
                if error and s.stop_reason is None:
                    s.stop_reason = f"{type(error[0]).__name__}: {error[0]}"
                await edgar_pipeline.update_run(
                    conn, run_id,
                    status="failed" if error else "succeeded",
                    finished_at=datetime.now(timezone.utc),
                    fetch_attempted=s.done, fetched=s.fetched, fetch_failed=s.failed,
                    ready_for_extraction=s.by_status.get("ready_for_extraction", 0),
                    not_pricing_supplement=s.by_status.get("not_pricing_supplement", 0),
                    prefilter_skipped=s.by_status.get("prefilter_skipped", 0),
                    bytes_uploaded=s.bytes_uploaded + self.supplements.bytes_uploaded,
                    sec_requests=self.sec.requests,
                    stop_reason=s.stop_reason or ("stopped by operator" if self.stop.is_set() else "scope done"),
                    error=(f"{type(error[0]).__name__}: {error[0]}"[:2000] if error else None),
                    details={"bulk_result": s.summary() | {"sec_retries": self.sec.retries}},
                )
        self.stats.bytes_uploaded += self.supplements.bytes_uploaded
        self._final_summary()
        if error:
            raise error[0]
        return self.stats

    async def _produce(self, queue: asyncio.Queue, error: list) -> None:
        o = self.opts
        try:
            async with self.pool.acquire() as conn:
                for inst in self.insts:
                    cursor = None
                    while not self.stop.is_set() and not error:
                        room = CLAIM_BATCH if o.limit is None else min(CLAIM_BATCH, o.limit - self.stats.claimed)
                        if room <= 0:
                            break
                        batch = await claim_batch(conn, self.stats.run_id, self.scope, inst, room, cursor)
                        if not batch:
                            break
                        self.stats.claimed += len(batch)
                        last = batch[-1]
                        cursor = (last["filing_date"], last["accession_number"])
                        for row in batch:
                            await queue.put(row)
                        await edgar_pipeline.renew_lease(conn, str(self.stats.run_id), LEASE_TTL_SECONDS,
                                                         f"{LEASE_PREFIX}{self.stats.run_id}")
                    if (o.limit is not None and self.stats.claimed >= o.limit) or self.stop.is_set():
                        break
        except BaseException as exc:  # noqa: BLE001 — surfaced by run()
            error.append(exc)
            self.stop.set()
        finally:
            for _ in range(o.concurrency):
                await queue.put(None)

    async def _work(self, queue: asyncio.Queue, error: list) -> None:
        async with self.pool.acquire() as conn:
            while True:
                row = await queue.get()
                if row is None:
                    return
                if self.stop.is_set():
                    continue          # claimed but not started: released in bulk at the end
                try:
                    await self._one(conn, row)
                except BaseException as exc:  # noqa: BLE001
                    error.append(exc)
                    self.stop.set()
                    continue
                if self.after_filing is not None:
                    await self.after_filing(self)

    async def _one(self, conn, row: dict) -> None:
        o, s = self.opts, self.stats
        inst = self.inst_of.get(row["primary_issuer_cik"], "?")
        try:
            out = await self._fetch_and_store(conn, row, inst)
        except _SYSTEMIC:
            raise
        except Exception as exc:  # noqa: BLE001 — one filing's failure is recorded
            if _is_storage_error(exc):
                raise
            reason = f"bulk fetch: {type(exc).__name__}: {exc}"
            await record_fetch_failed(conn, row, reason)
            async with self._lock:
                s.failed += 1
                s.done += 1
                s.consecutive_failures += 1
                s.by_status["fetch_failed"] += 1
                s.failures.append({"accession_number": row["accession_number"], "error": reason[:300]})
                if s.consecutive_failures >= o.stop_after_failures and s.stop_reason is None:
                    s.stop_reason = (f"{s.consecutive_failures} consecutive failures — stopping the run "
                                     f"(last: {reason[:200]}). The SEC may be throttling or down; check "
                                     f"and re-run (claims are released, nothing is lost).")
                    self.out(s.stop_reason)
                    self.stop.set()
                self._maybe_progress()
            return
        async with self._lock:
            s.consecutive_failures = 0
            s.done += 1
            s.by_status[out["status"]] += 1
            s.by_kind[out["document_kind"]] += 1
            s.by_institution_kind[inst][out["document_kind"]] += 1
            if out.get("peek_skipped"):
                s.peek_skipped += 1
            else:
                s.fetched += 1
            self._maybe_progress()

    async def _fetch_and_store(self, conn, row: dict, inst: str) -> dict:
        o, s = self.opts, self.stats
        meta = edgar_pipeline.filing_meta(row)
        t = time.monotonic()
        listing = await self.sec.get(f"{meta.archive_base}/index.json")
        items = (listing.json().get("directory") or {}).get("item") or []
        meta.primary_document = edgar_fetch._pick_primary_document(items, meta.form_type)
        if not meta.primary_document:
            raise edgar_pipeline.EdgarPipelineError("the filing's folder index lists no HTML or text document")
        raw: bytes | None = None
        if o.peek:
            p = await peek(self.sec, meta.source_url, self.scope.source.classify)
            s.bytes_downloaded += p.bytes_received
            if not p.honoured:
                s.peek_range_ignored += 1
                raw = p.body                      # the server sent the whole document: use it
            elif peek_decision(p.kind, self.wanted, o.keep_preliminaries) == "skip":
                self._tick("sec", time.monotonic() - t)
                await record_peek_skip(conn, s.run_id, row, p)
                if p.total_size:
                    s.bytes_saved_by_peek += max(0, p.total_size - p.bytes_received)
                return {"status": "not_pricing_supplement", "document_kind": p.kind, "peek_skipped": True}
            elif p.kind == "other":
                s.peek_fallthrough += 1
        if raw is None:
            raw = (await self.sec.get(meta.source_url)).content
            s.bytes_downloaded += len(raw)
        header = await self.sec.get(f"{meta.archive_base}/{meta.accession_number}-index-headers.html")
        fn = edgar_fetch._FILE_NUMBER_RE.search(header.text)
        meta.file_number = fn.group(1).strip() if fn else None
        self._tick("sec", time.monotonic() - t)
        async with platform_scope(conn):
            names = {r["cik"]: r["company_name"] for r in await conn.fetch(
                "SELECT cik, company_name FROM portfolio.edgar_index_filing_filers WHERE accession_number = $1",
                row["accession_number"])}
        meta.filer_name = names.get(meta.cik) or next(iter(names.values()), "UNKNOWN")
        extra = edgar_pipeline.prefilter_terms_for(self.policy_rules, inst)
        out = await edgar_pipeline.store_fetched(conn, meta, raw, bucket=self.bucket, r2_prefix=o.r2_prefix,
                                                 timings=s.timings, extra_strong=extra)
        await release_claims(conn, s.run_id, [row["accession_number"]])
        s.bytes_uploaded += out["bytes_uploaded"]
        if self.want_supplements and out["document_kind"] == "pricing_supplement":
            await self._link_supplement(conn, out["reference_filing_id"], raw, inst)
        return out

    async def _link_supplement(self, conn, note_ref_id, raw: bytes, inst: str) -> None:
        st = self.stats.supplements
        text = edgar_fetch.extract_filing_text(raw).text
        ref = find_supplement_link(text, raw.decode("utf-8", errors="replace"))
        if ref is None:
            ref = find_supplement_name_date(text)
            if ref is not None:
                ref = await resolve_name_date(conn, ref, self.group_ciks.get(inst, []))
        if ref is None:
            await upsert_supplement_link(conn, note_ref_id, None)
            st.unresolved += 1
            return
        st.referenced += 1
        if not ref.accession:
            await upsert_supplement_link(conn, note_ref_id, ref)
            st.unresolved += 1
            return
        try:
            supp_id = await self.supplements.ensure(conn, ref)
        except SecRequestFailed:
            st.failed += 1
            supp_id = None
        await upsert_supplement_link(conn, note_ref_id, ref, supp_id)
        st.links += 1

    def _maybe_progress(self) -> None:
        s = self.stats
        if s.done % PROGRESS_EVERY:
            return
        elapsed = max(time.monotonic() - s.started, 0.001)
        rate = s.done / elapsed
        remaining = max(0, (self.opts.limit or s.scope_total) - s.done)
        eta = remaining / rate if rate else 0
        kinds = ", ".join(f"{k} {v}" for k, v in s.by_kind.most_common())
        self.out(f"[{s.done:,} done / {remaining:,} remaining] {rate:.2f}/s, ETA {_fmt_eta(eta)}; "
                 f"{kinds}; peek-skipped {s.peek_skipped}; failed {s.failed}")

    def _final_summary(self) -> None:
        s = self.stats
        elapsed = time.monotonic() - s.started
        self.out(f"\nRun {s.run_id} finished in {_fmt_eta(elapsed)}: {s.done:,} filings "
                 f"({s.fetched:,} downloaded, {s.peek_skipped:,} skipped by the peek, {s.failed:,} failed). "
                 f"Stop reason: {s.stop_reason or ('stopped by operator' if self.stop.is_set() else 'scope done')}")
        for inst, kinds in s.by_institution_kind.items():
            self.out(f"    {inst}: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        self.out(f"SEC requests {self.sec.requests:,} (retries {self.sec.retries}); "
                 f"uploaded to R2 {s.bytes_uploaded:,} bytes; downloaded {s.bytes_downloaded:,} bytes; "
                 f"saved by peeking {s.bytes_saved_by_peek:,} bytes")
        if s.done:
            per = {k: round(v / max(s.fetched, 1), 3) for k, v in s.timings.items()}
            self.out(f"seconds per downloaded filing by step: {per}")
        if self.want_supplements:
            st = s.supplements
            self.out(f"product supplements: {st.distinct_found} distinct found, {st.fetched} fetched, "
                     f"{st.already_stored} already stored, {st.unresolved} unresolved, {st.failed} failed, "
                     f"{st.links} links")


# ═══ --peek-test ════════════════════════════════════════════════════════════

async def peek_test(conn, scope: Scope, n: int, client: httpx.AsyncClient, *, out=print) -> dict:
    """Peek ``n`` in-scope filings; print status (206 vs 200), bytes and kind,
    then a verdict on whether the SEC honours Range. WRITES NOTHING."""
    rows = (await scope_accessions(conn, scope, per_institution_limit=max(1, n)))[:n]
    sec = SecClient(client)
    results = []
    for row in rows:
        meta = edgar_pipeline.filing_meta(row | {"submission_path": await _submission_path(conn, row)})
        try:
            listing = await sec.get(f"{meta.archive_base}/index.json")
            items = (listing.json().get("directory") or {}).get("item") or []
            meta.primary_document = edgar_fetch._pick_primary_document(items, meta.form_type)
            p = await peek(sec, meta.source_url, scope.source.classify)
            results.append({"accession": row["accession_number"], "status": p.status_code,
                            "bytes": p.bytes_received, "total": p.total_size, "kind": p.kind})
            out(f"{row['accession_number']}  HTTP {p.status_code}  {p.bytes_received:>8,} bytes"
                f"{f' of {p.total_size:,}' if p.total_size else ''}  -> {p.kind}")
        except Exception as exc:  # noqa: BLE001
            results.append({"accession": row["accession_number"], "error": str(exc)})
            out(f"{row['accession_number']}  ERROR {exc}")
    ok = [r for r in results if "status" in r]
    partial = sum(1 for r in ok if r["status"] == 206)
    if not ok:
        verdict = "no peek succeeded — cannot tell"
    elif partial == len(ok):
        verdict = f"SEC HONOURS Range: {partial}/{len(ok)} peeks returned 206 Partial Content"
    elif partial == 0:
        verdict = (f"SEC IGNORES Range: 0/{len(ok)} returned 206 — the peek saves nothing; run with "
                   "--no-peek (the runner already falls through to a full fetch on a 200)")
    else:
        verdict = f"MIXED: {partial}/{len(ok)} returned 206; the runner handles both"
    known = sum(1 for r in ok if r["kind"] != "other")
    out(f"\nVerdict: {verdict}. Cover recognised from the first {PEEK_BYTES // 1024} KB: {known}/{len(ok)}. "
        f"SEC requests made: {sec.requests}. Nothing was written.")
    return {"results": results, "verdict": verdict, "requests": sec.requests}


async def _submission_path(conn, row) -> str:
    async with platform_scope(conn):
        return await conn.fetchval(
            "SELECT submission_path FROM portfolio.edgar_index_filings WHERE accession_number = $1",
            row["accession_number"])


# ═══ --refresh-index (Task 3) ═══════════════════════════════════════════════

def weekdays(start: date, through: date) -> list[date]:
    out, d = [], start
    while d <= through:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


async def refresh_index(conn, client: httpx.AsyncClient | None, *, through: date | None = None,
                        dry_run: bool = False, out=print) -> dict:
    """Load EDGAR's daily index for every weekday from the last loaded filing
    date (INCLUSIVE — a day can be loaded part-way, and a re-read is a no-op:
    inserts are ON CONFLICT DO NOTHING) through ``through`` (default
    yesterday, UTC), with the existing loader; then apply the ACTIVE selection
    policy to exactly the rows this refresh inserted."""
    last = await last_indexed_date(conn)
    start = last or date(2019, 1, 1)
    through = through or (datetime.now(timezone.utc).date() - timedelta(days=1))
    days = weekdays(start, through)
    out(f"index refresh: last loaded filing date {last}; reading {start} .. {through}: "
        f"{len(days)} daily index file(s) (weekdays; a holiday simply has none)")
    if dry_run:
        out("Dry run: nothing was read or written.")
        return {"start": start, "through": through, "files": len(days), "new_filings": 0}
    async with platform_scope(conn):
        src_forms = await conn.fetch("SELECT DISTINCT unnest(form_types) AS f FROM portfolio.edgar_sources "
                                     "WHERE is_active")
    forms = set(edgar_index.FORMS) | {r["f"] for r in src_forms}
    collect: list[str] = []
    stats = edgar_pipeline.DiscoveryStats()
    for d in days:
        await edgar_pipeline.discover_day(conn, d, client, stats, forms=forms, collect=collect)
    sel = await edgar_pipeline.select_stage(conn, accessions=collect) if collect else \
        {"selected": 0, "not_selected": 0, "policy_version": None}
    out(f"read {stats.days_read} file(s) ({stats.days_missing} without an index), "
        f"{stats.new_filings:,} new filings; selection v{sel['policy_version']} on the new rows only: "
        f"{sel['selected']:,} selected, {sel['not_selected']:,} not selected")
    return {"start": start, "through": through, "files": len(days), "days_read": stats.days_read,
            "new_filings": stats.new_filings, "new_accessions": collect, "selection": sel}


# ═══ Stored text (no SEC request) ═══════════════════════════════════════════

def _default_download(key: str, bucket: str) -> bytes:
    return storage.download_bytes(key, bucket)


async def stored_text(row: dict, *, bucket: str | None, downloader=None) -> str:
    """The text of a stored filing: the database column if the row has one (the
    2025Q1 corpus), else the gzipped text object in R2, else the document."""
    if row.get("extracted_text"):
        return row["extracted_text"]
    dl = downloader or _default_download
    if row.get("text_r2_key"):
        data = await asyncio.to_thread(dl, row["text_r2_key"], bucket)
        return gzip.decompress(data).decode("utf-8", errors="replace")
    if row.get("r2_key"):
        data = await asyncio.to_thread(dl, row["r2_key"], bucket)
        if row.get("content_encoding") == "gzip":
            data = gzip.decompress(data)
        return edgar_fetch.extract_filing_text(data).text
    return ""


def policy_exclusion(rules: dict, form_type: str, filing_date: date | None, primary_issuer_cik: str | None,
                     include_status: str | None) -> str | None:
    """Python twin of select_stage's CASE: the reason a row is excluded, or None."""
    if form_type not in (rules.get("form_types") or []):
        return f"form {form_type} not in policy"
    f = rules.get("filing_date_from")
    if f and filing_date and filing_date < date.fromisoformat(f):
        return f"filed before {f}"
    if not primary_issuer_cik:
        return "no listed issuer among the filers"
    if include_status not in (rules.get("issuer_include_status") or []):
        return f"issuer {primary_issuer_cik} include_status {include_status}"
    return None


# ═══ --reprocess-fetched (Task 7) ═══════════════════════════════════════════

async def reprocess_fetched(conn, *, bucket: str | None = None, downloader=None, dry_run: bool = False,
                            accessions: list[str] | None = None, out=print) -> dict:
    """Classify every manifest row stuck at 'fetched' from its STORED text and
    move it to its proper status. No SEC request is ever made (this function
    has no HTTP client at all). A row the active policy would not have
    selected becomes not_selected (with its document_kind recorded); the rest
    get decide_status's answer. Both stamp the active policy version, which the
    decided_has_policy CHECK requires."""
    policy = await edgar_pipeline.active_policy(conn)
    scope_sql = "AND f.accession_number = ANY($1::text[])" if accessions is not None else ""
    async with platform_scope(conn):
        rows = await conn.fetch(
            f"""SELECT f.accession_number, f.form_type, f.filing_date, f.primary_issuer_cik,
                       i.include_status, i.issuer_group, r.extracted_text, r.text_r2_key, r.r2_key,
                       r.content_encoding
                  FROM portfolio.edgar_index_filings f
                  JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
                  LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
                 WHERE f.pipeline_status = 'fetched' {scope_sql}
                 ORDER BY f.filing_date DESC""",
            *([list(accessions)] if accessions is not None else []))
    moves: Counter = Counter()
    kinds: Counter = Counter()
    for r in rows:
        r = dict(r)
        text = await stored_text(r, bucket=bucket, downloader=downloader)
        excl = policy_exclusion(policy["rules"], r["form_type"], r["filing_date"], r["primary_issuer_cik"],
                                r["include_status"])
        kind, _ = edgar_pipeline.classify_document(text)
        if excl:
            status, reason = "not_selected", f"policy v{policy['version']}: {excl}"
        else:
            status, reason, kind = edgar_pipeline.decide_status(
                text, edgar_pipeline.prefilter_terms_for(policy["rules"], r["issuer_group"]))
        reason = f"{reason}; reprocessed from stored text (bulkfetch)"
        moves[status] += 1
        kinds[kind] += 1
        if dry_run:
            continue
        cusip = edgar_pipeline.detect_cusip(text)
        async with platform_scope(conn):
            await conn.execute(
                """UPDATE portfolio.edgar_index_filings
                      SET pipeline_status = $2, status_reason = left($3, 1500), document_kind = $4,
                          detected_cusip = COALESCE(detected_cusip, $5), selection_policy_version = $6
                    WHERE accession_number = $1 AND pipeline_status = 'fetched'""",
                r["accession_number"], status, reason, kind, cusip, policy["version"])
    out(f"reprocess-fetched: {len(rows)} row(s) at 'fetched'; "
        + ", ".join(f"{k} {v}" for k, v in moves.most_common())
        + "; kinds: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common())
        + (" — DRY RUN, nothing written" if dry_run else ""))
    return {"rows": len(rows), "moves": dict(moves), "kinds": dict(kinds)}


# ═══ Selection v2 for Wells Fargo / Deutsche Bank (Task 6) ══════════════════

SELECTION_V2_GROUPS = ("Wells Fargo", "Deutsche Bank")


def build_v2_rules(v1_rules: dict, groups) -> dict:
    """v1's rules, untouched, plus the per-group prefilter extension."""
    rules = dict(v1_rules)
    rules["prefilter"] = {"version": 2, "issuer_groups": list(groups)}
    return rules


async def plan_selection_v2(conn, *, groups=SELECTION_V2_GROUPS, bucket: str | None = None,
                            downloader=None, accessions: list[str] | None = None) -> dict:
    """What v2 would change for ``groups`` only. READ-ONLY.

    (a) not_selected rows of those groups that v2's selection rules select;
    (b) prefilter_skipped rows of those groups whose stored text now passes the
        prefilter with the v2 terms -> ready_for_extraction.
    Other groups' rows are never read."""
    policy = await edgar_pipeline.active_policy(conn)
    v2_rules = build_v2_rules({k: v for k, v in policy["rules"].items() if k != "prefilter"}, groups)
    async with platform_scope(conn):
        cik_rows = await conn.fetch(
            "SELECT filer_cik, issuer_group, include_status FROM portfolio.structured_note_issuers "
            "WHERE lower(issuer_group) = ANY($1::text[])", [g.casefold() for g in groups])
    ciks = [r["filer_cik"] for r in cik_rows]
    status_of = {r["filer_cik"]: r["include_status"] for r in cik_rows}
    group_of = {r["filer_cik"]: r["issuer_group"] for r in cik_rows}
    scope_sql = "AND f.accession_number = ANY($2::text[])" if accessions is not None else ""
    args = [ciks] + ([list(accessions)] if accessions is not None else [])
    async with platform_scope(conn):
        rows = await conn.fetch(
            f"""SELECT f.accession_number, f.form_type, f.filing_date, f.primary_issuer_cik, f.pipeline_status,
                       r.extracted_text, r.text_r2_key, r.r2_key, r.content_encoding
                  FROM portfolio.edgar_index_filings f
                  LEFT JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
                 WHERE f.primary_issuer_cik = ANY($1::text[])
                   AND f.pipeline_status IN ('not_selected', 'prefilter_skipped') {scope_sql}""",
            *args)
    to_selected, to_ready = [], []
    by_year: dict = defaultdict(Counter)
    for r in rows:
        r = dict(r)
        y = r["filing_date"].year
        if r["pipeline_status"] == "not_selected":
            if policy_exclusion(v2_rules, r["form_type"], r["filing_date"], r["primary_issuer_cik"],
                                status_of.get(r["primary_issuer_cik"])) is None:
                to_selected.append(r["accession_number"])
                by_year[y]["not_selected -> selected"] += 1
        else:
            text = await stored_text(r, bucket=bucket, downloader=downloader)
            terms = edgar_pipeline.prefilter_terms_for(v2_rules, group_of.get(r["primary_issuer_cik"]))
            status, reason, _ = edgar_pipeline.decide_status(text, terms)
            if status == "ready_for_extraction":
                to_ready.append((r["accession_number"], reason))
                by_year[y]["prefilter_skipped -> ready_for_extraction"] += 1
    return {"policy": policy, "v2_rules": v2_rules, "groups": list(groups), "ciks": ciks,
            "to_selected": to_selected, "to_ready": to_ready,
            "by_year": {y: dict(c) for y, c in sorted(by_year.items())}, "rows_read": len(rows)}


def print_selection_plan(plan: dict, out=print) -> None:
    out(f"selection v2 for {plan['groups']} (CIKs {plan['ciks']}): {plan['rows_read']} not_selected / "
        f"prefilter_skipped row(s) read")
    if not plan["by_year"]:
        out("    nothing moves")
    for y, c in plan["by_year"].items():
        out(f"    {y}: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    out(f"total: {len(plan['to_selected'])} not_selected -> selected, "
        f"{len(plan['to_ready'])} prefilter_skipped -> ready_for_extraction")


async def apply_selection_v2(conn, plan: dict, *, activate_policy: bool = True,
                             stamp_version: int | None = None) -> dict:
    """Write the plan. With ``activate_policy`` (the operator's path) v2 is
    inserted as the active policy and v1 retired — v1's rules are never
    edited. Every UPDATE is fenced to the plan's own CIKs AND its own
    accessions, so no other bank's row can change."""
    version = stamp_version
    async with platform_scope(conn):
        if activate_policy:
            current = plan["policy"]
            if (current["rules"].get("prefilter") or {}).get("version") == 2:
                version = current["version"]
            else:
                version = int(await conn.fetchval(
                    "SELECT COALESCE(max(version), 0) + 1 FROM portfolio.edgar_selection_policies"))
                await conn.execute(
                    "UPDATE portfolio.edgar_selection_policies SET is_active = false, retired_at = now() "
                    "WHERE is_active")
                await conn.execute(
                    """INSERT INTO portfolio.edgar_selection_policies (version, rules, description, is_active)
                       VALUES ($1, $2::jsonb, $3, true)""",
                    version, json.dumps(plan["v2_rules"]),
                    f"v{version}: v{current['version']}'s rules unchanged, plus prefilter v2 — the "
                    f"Wells Fargo / Deutsche Bank cover vocabulary as strong terms, for issuer groups "
                    f"{plan['groups']} only (bulkfetch).")
        if version is None:
            raise BulkFetchError("apply_selection_v2 needs activate_policy or an explicit stamp_version")
        sel = await conn.execute(
            """UPDATE portfolio.edgar_index_filings
                  SET pipeline_status = 'selected', selection_policy_version = $3, selected_by_cohort_id = NULL,
                      status_reason = 'policy v' || $3::int::text || ': selected (re-decided by selection v2)'
                WHERE accession_number = ANY($1::text[]) AND primary_issuer_cik = ANY($2::text[])
                  AND pipeline_status = 'not_selected'""",
            plan["to_selected"], plan["ciks"], version)
        ready = 0
        for acc, reason in plan["to_ready"]:
            res = await conn.execute(
                """UPDATE portfolio.edgar_index_filings
                      SET pipeline_status = 'ready_for_extraction', selection_policy_version = $3,
                          selected_by_cohort_id = NULL,
                          status_reason = left('policy v' || $3::int::text || ' prefilter v2: ' || $4, 1500)
                    WHERE accession_number = $1 AND primary_issuer_cik = ANY($2::text[])
                      AND pipeline_status = 'prefilter_skipped'""",
                acc, plan["ciks"], version, reason)
            ready += int(res.split()[-1])
    return {"version": version, "selected": int(sel.split()[-1]), "ready_for_extraction": ready}
