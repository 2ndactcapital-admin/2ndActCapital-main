"""EDGAR pipeline A — discovery, selection, fetch-to-R2, the job and its lease.

STAGES (each reads and writes ``portfolio.edgar_index_filings``, the manifest)
──────────────────────────────────────────────────────────────────────────────
  1. discover  ``discover_incremental`` — EDGAR's DAILY index for every day since
               the newest filing_date already loaded; same parser and loader as
               the quarterly script (services/edgar_index.py).
  2. select    ``select_stage`` — the ACTIVE row of
               ``portfolio.edgar_selection_policies`` decides discovered rows:
               selected | not_selected, each stamped with the policy version.
  3. fetch     ``fetch_stage`` — selected rows newest first (plus fetch_failed
               rows whose retry is due): find the main document from the
               filing's own folder index, download it, gzip it, upload it to
               R2, record sha256 of the RAW bytes plus raw and compressed sizes,
               extract text with the EXISTING extractor and store that gzipped
               in R2 too, classify ``document_kind`` from the opening text, run
               the EXISTING keyword prefilter on pricing supplements only, and
               detect a CUSIP whose check digit validates.

No stage calls a model. Term extraction is sprint B.

ONE DOCUMENT TABLE. ``portfolio.reference_filings`` stays the store for fetched
documents (note terms already point at it); the manifest links to it by
``reference_filing_id``. New rows carry ``content_encoding = 'gzip'``,
``extraction_status = 'fetched'`` and ``extracted_text = NULL`` — the text is in
R2 at ``text_r2_key``. 'fetched' (not 'extracted') is deliberate: the existing
note-terms extraction script selects ``extraction_status = 'extracted'`` and
reads ``extracted_text``, and must not pick up a row whose text is not in the
database.

WHY GZIP BEFORE UPLOAD. Render bills outbound traffic — uploads to R2
included — above the workspace allowance. A 424B2's HTML compresses roughly
5-8x, so compressing first is what keeps a nightly fetch inside it. Downloads
from the SEC are inbound and free.

THE JOB. ``run_job`` is what a Render one-off job runs (``edgar_pipeline_job.py``).
Only one may run at a time: a LEASE ROW (``portfolio.edgar_pipeline_lease``)
with an expiry, renewed as the job works, so a crashed job's lease simply
lapses. NOT a session-level advisory lock — under the transaction pooler the
session can be handed to another client. All SEC traffic in the job goes through
``edgar_fetch``'s one shared limiter, set to 8 requests/second.

THE LAUNCH. ``launch_pipeline_run`` records a run row and asks the Render API to
start a one-off job on the base service, then RETURNS — it never waits for the
job. A launch that cannot happen (lease held, Render refused, no credential) is
recorded on the run row and returned, never raised: a raised error would HOLD
the nightly workflow run, a held run is non-terminal, and the scheduler's
overlap protection would then skip every following night.

DB ACCESS. Every write runs inside its own transaction with
``app.is_super_admin`` set LOCAL (``services.database.platform_scope``) —
never a session-level SET.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

from services import edgar_fetch, edgar_index, storage
from services.database import platform_scope

# ── Configuration ───────────────────────────────────────────────────────────
LEASE_NAME = "edgar_pipeline"
LEASE_TTL_SECONDS = 15 * 60
JOB_RATE_LIMIT = 8                      # requests/second, ALL SEC traffic in the job
DEFAULT_FETCH_CAP = 5000                # filings attempted per job
DEFAULT_RUNTIME_CAP_SECONDS = 4 * 3600  # Render stops a run at 12h; stay well under
MAX_FETCH_CAP = 50000
MAX_RUNTIME_CAP_SECONDS = 10 * 3600
MAX_FETCH_ATTEMPTS = 5
RETRY_BASE = timedelta(hours=6)         # 6h, 12h, 24h, 48h, then give up
CONSECUTIVE_FAILURE_LIMIT = 25          # stop the run: the SEC or R2 is likely down
FETCH_BATCH = 50
OPENING_CHARS = 4000
R2_PREFIX = edgar_fetch.R2_PREFIX       # reference/edgar
STAGES = ("discover", "select", "fetch")
LAUNCH_GUARD = timedelta(minutes=30)    # a launched job not yet holding the lease

DAILY_INDEX_URL = (
    edgar_fetch.EDGAR_HOST
    + "/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{ymd}.idx"
)

RENDER_API = "https://api.render.com/v1"
RENDER_SERVICE_NAME_DEFAULT = "2ndactcapital-workflow-scheduler"
JOB_START_COMMAND = "python edgar_pipeline_job.py --run-id {run_id}"

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


class EdgarPipelineError(RuntimeError):
    """A pipeline failure that is not about one filing."""


class EdgarPipelineConfigError(EdgarPipelineError):
    """Required configuration is missing or invalid. Nothing was attempted."""


class RenderLaunchError(EdgarPipelineError):
    """The Render API did not start the job."""


def fetch_cap_default() -> int:
    raw = (os.environ.get("EDGAR_PIPELINE_FETCH_CAP") or "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_FETCH_CAP


def r2_bucket() -> str:
    """The bucket fetched documents go to. Validated; a bad value fails LOUD.

    ``EDGAR_R2_BUCKET`` wins when set, else ``R2_BUCKET_NAME`` (what
    services.storage uses). Measured during this sprint: Doppler's
    R2_BUCKET_NAME held 'docs_readwrite_hollisworks', which is not a legal
    bucket name at all, while the existing corpus lives in 'hollisworks-docs'.
    An invalid name is refused here, before any filing is touched, rather than
    surfacing as one InvalidBucketName per filing.
    """
    name = (os.environ.get("EDGAR_R2_BUCKET") or os.environ.get("R2_BUCKET_NAME") or "").strip()
    if not name:
        raise EdgarPipelineConfigError(
            "No R2 bucket configured: set EDGAR_R2_BUCKET or R2_BUCKET_NAME in Doppler."
        )
    if not _BUCKET_RE.match(name):
        raise EdgarPipelineConfigError(
            f"R2 bucket name {name!r} is not a valid bucket name (lowercase letters, "
            "digits, '.' and '-', 3-63 chars). Fix R2_BUCKET_NAME (or set "
            "EDGAR_R2_BUCKET) in Doppler; the existing EDGAR corpus is in "
            "'hollisworks-docs'."
        )
    return name


# ═══ Document classification (free rules on the opening text) ══════════════

_PRELIMINARY_RE = re.compile(
    r"preliminary pricing supplement|subject to completion|preliminary terms"
)
_TITLE_PATTERNS = (
    ("pricing_supplement", re.compile(r"pricing supplement")),
    ("product_supplement", re.compile(r"product supplement")),
    ("underlying_supplement", re.compile(r"underlying supplement")),
    ("term_sheet", re.compile(r"term sheet")),
)
DOCUMENT_KINDS = (
    "pricing_supplement",
    "preliminary_pricing_supplement",
    "product_supplement",
    "underlying_supplement",
    "term_sheet",
    "other",
)


def opening_of(text: str) -> str:
    """The first ``OPENING_CHARS`` of text, whitespace collapsed, lowercased."""
    return " ".join(text[: OPENING_CHARS * 3].split()).lower()[:OPENING_CHARS]


def classify_document(text: str) -> tuple[str, str]:
    """``(document_kind, reason)`` from the opening text. Deterministic, no model.

    A preliminary marker anywhere in the opening wins outright: a preliminary
    pricing supplement is NEVER ready for extraction, whatever else it says.
    Otherwise the title phrase that appears FIRST wins — a pricing supplement
    names its product supplement further down its cover ("to product
    supplement no. 4-I"), and a product supplement mentions "the applicable
    pricing supplement" further down its own, so first-mention is the title.
    """
    opening = opening_of(text)
    if not opening:
        return "other", "no text in the opening"
    prelim = _PRELIMINARY_RE.search(opening)
    if prelim:
        return "preliminary_pricing_supplement", f"opening contains {prelim.group(0)!r}"
    best: tuple[int, str, str] | None = None
    for kind, pattern in _TITLE_PATTERNS:
        m = pattern.search(opening)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), kind, m.group(0))
    if best is None:
        return "other", "no title phrase in the opening"
    return best[1], f"first title phrase {best[2]!r} at {best[0]}"


def decide_status(text: str) -> tuple[str, str, str]:
    """``(pipeline_status, status_reason, document_kind)`` for fetched text.

    Only a final pricing supplement can become ready_for_extraction, and only if
    the EXISTING keyword prefilter (edgar_fetch.passes_prefilter) passes. A
    preliminary pricing supplement is classified first and so is never ready,
    however many payoff keywords it contains.
    """
    kind, kind_reason = classify_document(text)
    if not text.strip():
        return "not_pricing_supplement", "extraction produced no text", kind
    if kind == "pricing_supplement":
        hits = edgar_fetch.prefilter_hits(text)
        if edgar_fetch.passes_prefilter(text):
            return "ready_for_extraction", f"pricing supplement; prefilter hits {hits}", kind
        return "prefilter_skipped", f"pricing supplement; prefilter hits {hits}", kind
    return "not_pricing_supplement", f"{kind}: {kind_reason}", kind


# ═══ CUSIP (rule + check digit) ═════════════════════════════════════════════

_CUSIP_LABEL_RE = re.compile(r"CUSIP", re.IGNORECASE)
_CUSIP_TOKEN_RE = re.compile(r"(?<![0-9A-Z])([0-9][0-9A-Z]{7}[0-9])(?![0-9A-Z])")
_CUSIP_WINDOW = 80


def cusip_check_digit(base8: str) -> int:
    """The standard CUSIP modulus-10 'double-add-double' check digit."""
    if len(base8) != 8:
        raise ValueError("a CUSIP base is 8 characters")
    total = 0
    for i, ch in enumerate(base8):
        if ch.isdigit():
            v = int(ch)
        elif "A" <= ch <= "Z":
            v = ord(ch) - ord("A") + 10
        elif ch == "*":
            v = 36
        elif ch == "@":
            v = 37
        elif ch == "#":
            v = 38
        else:
            raise ValueError(f"invalid CUSIP character {ch!r}")
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def is_valid_cusip(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 9 or not value[8].isdigit():
        return False
    try:
        return cusip_check_digit(value[:8]) == int(value[8])
    except ValueError:
        return False


def detect_cusip(text: str) -> str | None:
    """The first CUSIP-shaped token near a 'CUSIP' label whose check digit validates.

    The rule: within ``_CUSIP_WINDOW`` characters after the word CUSIP, a
    9-character upper-case token starting with a digit. A token whose check digit
    does not validate is skipped, never stored.
    """
    for label in _CUSIP_LABEL_RE.finditer(text):
        window = text[label.end(): label.end() + _CUSIP_WINDOW]
        for m in _CUSIP_TOKEN_RE.finditer(window):
            candidate = m.group(1)
            if is_valid_cusip(candidate):
                return candidate
    return None


# ═══ The lease ══════════════════════════════════════════════════════════════

async def acquire_lease(conn, holder: str, ttl_seconds: float = LEASE_TTL_SECONDS,
                        lease_name: str = LEASE_NAME) -> bool:
    """Take the lease if it is free, expired, or already ours. Atomic.

    One INSERT ... ON CONFLICT DO UPDATE ... WHERE: two jobs racing are
    serialized on the row lock, and the loser's WHERE sees the winner's fresh
    expiry and updates nothing.
    """
    async with platform_scope(conn):
        row = await conn.fetchrow(
            """
            INSERT INTO portfolio.edgar_pipeline_lease
                (lease_name, holder, acquired_at, renewed_at, expires_at)
            VALUES ($1, $2, now(), now(), now() + make_interval(secs => $3))
            ON CONFLICT (lease_name) DO UPDATE
               SET holder = EXCLUDED.holder,
                   acquired_at = CASE WHEN portfolio.edgar_pipeline_lease.holder = EXCLUDED.holder
                                      THEN portfolio.edgar_pipeline_lease.acquired_at
                                      ELSE now() END,
                   renewed_at = now(),
                   expires_at = EXCLUDED.expires_at
             WHERE portfolio.edgar_pipeline_lease.expires_at <= now()
                OR portfolio.edgar_pipeline_lease.holder = EXCLUDED.holder
            RETURNING holder
            """,
            lease_name, holder, float(ttl_seconds),
        )
    return row is not None


async def renew_lease(conn, holder: str, ttl_seconds: float = LEASE_TTL_SECONDS,
                      lease_name: str = LEASE_NAME) -> bool:
    async with platform_scope(conn):
        row = await conn.fetchrow(
            """
            UPDATE portfolio.edgar_pipeline_lease
               SET renewed_at = now(), expires_at = now() + make_interval(secs => $3)
             WHERE lease_name = $1 AND holder = $2
            RETURNING holder
            """,
            lease_name, holder, float(ttl_seconds),
        )
    return row is not None


async def release_lease(conn, holder: str, lease_name: str = LEASE_NAME) -> None:
    async with platform_scope(conn):
        await conn.execute(
            """
            UPDATE portfolio.edgar_pipeline_lease
               SET expires_at = now(), renewed_at = now()
             WHERE lease_name = $1 AND holder = $2
            """,
            lease_name, holder,
        )


async def lease_state(conn, lease_name: str = LEASE_NAME) -> dict | None:
    async with platform_scope(conn):
        row = await conn.fetchrow(
            """
            SELECT lease_name, holder, acquired_at, renewed_at, expires_at,
                   expires_at > now() AS held
            FROM portfolio.edgar_pipeline_lease WHERE lease_name = $1
            """,
            lease_name,
        )
    return dict(row) if row else None


# ═══ Pipeline runs ══════════════════════════════════════════════════════════

_RUN_COUNT_COLUMNS = (
    "discovery_days", "discovered_new", "selected", "not_selected",
    "fetch_attempted", "fetched", "fetch_failed", "ready_for_extraction",
    "not_pricing_supplement", "prefilter_skipped", "bytes_uploaded", "sec_requests",
)
_RUN_UPDATABLE = frozenset(_RUN_COUNT_COLUMNS) | frozenset({
    "status", "started_at", "finished_at", "render_service_id", "render_job_id",
    "stop_reason", "error", "details", "workflow_run_id",
})


async def create_run(conn, *, trigger_source: str, requested_by=None, fetch_cap: int,
                     runtime_cap_seconds: int = DEFAULT_RUNTIME_CAP_SECONDS,
                     stages=STAGES, status: str = "launching", workflow_run_id=None):
    stages = list(stages)
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        raise ValueError(f"unknown pipeline stage(s): {unknown}; allowed {list(STAGES)}")
    if not 0 <= int(fetch_cap) <= MAX_FETCH_CAP:
        raise ValueError(f"fetch_cap must be 0..{MAX_FETCH_CAP}")
    if not 1 <= int(runtime_cap_seconds) <= MAX_RUNTIME_CAP_SECONDS:
        raise ValueError(f"runtime_cap_seconds must be 1..{MAX_RUNTIME_CAP_SECONDS}")
    async with platform_scope(conn):
        return await conn.fetchval(
            """
            INSERT INTO portfolio.edgar_pipeline_runs
                (trigger_source, requested_by, workflow_run_id, status, stages,
                 fetch_cap, runtime_cap_seconds)
            VALUES ($1, $2, $3, $4, $5::text[], $6, $7)
            RETURNING id
            """,
            trigger_source, requested_by, workflow_run_id, status, stages,
            int(fetch_cap), int(runtime_cap_seconds),
        )


async def update_run(conn, run_id, **fields) -> None:
    bad = set(fields) - _RUN_UPDATABLE
    if bad:
        raise ValueError(f"not updatable on a pipeline run: {sorted(bad)}")
    if not fields:
        return
    cols, vals = [], []
    for i, (k, v) in enumerate(fields.items(), start=2):
        if k == "details":
            cols.append(f"details = details || ${i}::jsonb")
            v = json.dumps(v, default=str)
        else:
            cols.append(f"{k} = ${i}")
        vals.append(v)
    async with platform_scope(conn):
        await conn.execute(
            f"UPDATE portfolio.edgar_pipeline_runs SET {', '.join(cols)} WHERE id = $1",
            run_id, *vals,
        )


async def load_run(conn, run_id) -> dict | None:
    async with platform_scope(conn):
        row = await conn.fetchrow("SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", run_id)
    return dict(row) if row else None


# ═══ Stage 1: incremental discovery over the daily index ════════════════════

@dataclass
class DiscoveryStats:
    days_read: int = 0
    days_missing: int = 0
    lines_matched: int = 0
    unique_filings: int = 0
    new_filings: int = 0
    new_filers: int = 0
    malformed: int = 0
    days: list = field(default_factory=list)


def daily_index_url(day: date) -> str:
    return DAILY_INDEX_URL.format(
        year=day.year, quarter=(day.month - 1) // 3 + 1, ymd=day.strftime("%Y%m%d")
    )


async def fetch_daily_index(day: date, client: httpx.AsyncClient) -> str | None:
    """The day's master index body, or None when EDGAR has none (weekend,
    holiday, not yet published). Any other failure raises."""
    try:
        response = await edgar_fetch._get(client, daily_index_url(day))
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code in (403, 404):
            return None
        raise
    return response.content.decode("latin-1")


async def discover_day(conn, day: date, client: httpx.AsyncClient,
                       stats: DiscoveryStats | None = None) -> DiscoveryStats:
    stats = stats or DiscoveryStats()
    body = await fetch_daily_index(day, client)
    if body is None:
        stats.days_missing += 1
        stats.days.append({"day": day.isoformat(), "index": "none"})
        return stats
    parsed = edgar_index.parse_index_lines(body.splitlines())
    new_filings, new_filers = await edgar_index.load_records(
        conn, parsed.filing_records(None), parsed.filer_records()
    )
    stats.days_read += 1
    stats.lines_matched += parsed.lines_matched
    stats.unique_filings += len(parsed.filings)
    stats.new_filings += new_filings
    stats.new_filers += new_filers
    stats.malformed += parsed.malformed
    stats.days.append({
        "day": day.isoformat(), "lines": parsed.lines_matched,
        "unique": len(parsed.filings), "new": new_filings,
    })
    return stats


async def discover_incremental(conn, client: httpx.AsyncClient, *,
                               start: date | None = None,
                               through: date | None = None) -> DiscoveryStats:
    """Read the daily index for every day from the newest loaded filing_date
    (inclusive — that day may have been loaded partially) through ``through``
    (default: today, UTC). Weekends are skipped without a request."""
    if start is None:
        async with platform_scope(conn):
            start = await conn.fetchval("SELECT max(filing_date) FROM portfolio.edgar_index_filings")
        start = start or date(2019, 1, 1)
    through = through or datetime.now(timezone.utc).date()
    stats = DiscoveryStats()
    day = start
    while day <= through:
        if day.weekday() < 5:
            await discover_day(conn, day, client, stats)
        day += timedelta(days=1)
    return stats


# ═══ Stage 2: selection ═════════════════════════════════════════════════════

_POLICY_KEYS = frozenset({"form_types", "issuer_include_status", "filing_date_from", "order"})


async def active_policy(conn) -> dict:
    async with platform_scope(conn):
        row = await conn.fetchrow(
            "SELECT version, rules, description FROM portfolio.edgar_selection_policies WHERE is_active"
        )
    if row is None:
        raise EdgarPipelineError("no active selection policy in portfolio.edgar_selection_policies")
    rules = row["rules"]
    if isinstance(rules, str):
        rules = json.loads(rules)
    unknown = set(rules) - _POLICY_KEYS
    if unknown:
        # A rule this code does not understand is refused, never ignored:
        # ignoring it would select filings the policy meant to exclude.
        raise EdgarPipelineError(
            f"selection policy v{row['version']} has rule(s) this code does not "
            f"implement: {sorted(unknown)}"
        )
    return {"version": row["version"], "rules": rules, "description": row["description"]}


_SELECT_SQL = """
WITH decided AS (
    SELECT f.accession_number,
           CASE
             WHEN NOT (f.form_type = ANY($2::text[]))
               THEN 'form ' || f.form_type || ' not in policy'
             WHEN $3::date IS NOT NULL AND f.filing_date < $3::date
               THEN 'filed before ' || $3::date
             WHEN f.primary_issuer_cik IS NULL
               THEN 'no listed issuer among the filers'
             WHEN NOT (i.include_status = ANY($4::text[]))
               THEN 'issuer ' || i.filer_cik || ' include_status ' || i.include_status
           END AS exclusion
    FROM portfolio.edgar_index_filings f
    LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
    WHERE f.pipeline_status = 'discovered' AND {scope}
)
UPDATE portfolio.edgar_index_filings f
   SET pipeline_status = CASE WHEN d.exclusion IS NULL THEN 'selected' ELSE 'not_selected' END,
       status_reason = 'policy v' || $1::int || ': ' || COALESCE(d.exclusion, 'selected'),
       selection_policy_version = $1::int
  FROM decided d
 WHERE f.accession_number = d.accession_number
   AND f.pipeline_status = 'discovered'
RETURNING f.pipeline_status
"""


async def select_stage(conn, *, accessions: list[str] | None = None,
                       policy: dict | None = None) -> dict:
    """Decide every 'discovered' row (or only ``accessions``) by the active policy.

    One quarter per transaction on the full table, newest quarter first, so a
    first run over the whole manifest never holds one giant transaction.
    """
    policy = policy or await active_policy(conn)
    rules = policy["rules"]
    args = (
        policy["version"],
        list(rules.get("form_types") or []),
        date.fromisoformat(rules["filing_date_from"]) if rules.get("filing_date_from") else None,
        list(rules.get("issuer_include_status") or []),
    )
    counts = {"selected": 0, "not_selected": 0, "policy_version": policy["version"]}

    async def _apply(scope_sql: str, *scope_args):
        sql = _SELECT_SQL.format(scope=scope_sql)
        async with platform_scope(conn):
            rows = await conn.fetch(sql, *args, *scope_args)
        for r in rows:
            counts[r["pipeline_status"]] += 1

    if accessions is not None:
        await _apply("f.accession_number = ANY($5::text[])", list(accessions))
        return counts
    async with platform_scope(conn):
        quarters = [r["index_quarter"] for r in await conn.fetch(
            """SELECT DISTINCT index_quarter FROM portfolio.edgar_index_filings
               WHERE pipeline_status = 'discovered' ORDER BY index_quarter DESC"""
        )]
    for q in quarters:
        await _apply("f.index_quarter = $5::text", q)
    return counts


# ═══ Stage 3: fetch ═════════════════════════════════════════════════════════

@dataclass
class FetchStats:
    attempted: int = 0
    fetched: int = 0
    failed: int = 0
    ready_for_extraction: int = 0
    not_pricing_supplement: int = 0
    prefilter_skipped: int = 0
    bytes_uploaded: int = 0
    uploads_skipped: int = 0
    stop_reason: str | None = None
    failures: list = field(default_factory=list)


_SUBMISSION_CIK_RE = re.compile(r"edgar/data/(\d+)/")


def _gzip(data: bytes) -> bytes:
    # mtime=0: identical input gives identical bytes, so a re-run compares equal.
    return gzip.compress(data, compresslevel=9, mtime=0)


async def _queue(conn, limit: int, accessions: list[str] | None) -> list[dict]:
    scope = "AND f.accession_number = ANY($2::text[])" if accessions is not None else ""
    args = [limit] + ([list(accessions)] if accessions is not None else [])
    async with platform_scope(conn):
        rows = await conn.fetch(
            f"""
            SELECT f.accession_number, f.form_type, f.filing_date, f.submission_path,
                   f.primary_issuer_cik, f.attempt_count
            FROM portfolio.edgar_index_filings f
            WHERE (f.pipeline_status = 'selected'
                   OR (f.pipeline_status = 'fetch_failed'
                       AND f.next_attempt_at IS NOT NULL AND f.next_attempt_at <= now()))
              {scope}
            ORDER BY f.filing_date DESC, f.accession_number DESC
            LIMIT $1
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def _filer_name(conn, accession: str, cik: str) -> str:
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT cik, company_name FROM portfolio.edgar_index_filing_filers WHERE accession_number = $1",
            accession,
        )
    names = {r["cik"]: r["company_name"] for r in rows}
    return names.get(cik) or next(iter(names.values()), "UNKNOWN")


async def _record_failure(conn, row: dict, exc: BaseException) -> None:
    attempts = int(row["attempt_count"]) + 1
    give_up = attempts >= MAX_FETCH_ATTEMPTS
    reason = f"{type(exc).__name__}: {exc}"[:1500]
    if give_up:
        reason = f"giving up after {attempts} attempts — {reason}"[:1500]
    async with platform_scope(conn):
        await conn.execute(
            """
            UPDATE portfolio.edgar_index_filings
               SET pipeline_status = 'fetch_failed',
                   status_reason = $2,
                   attempt_count = $3,
                   last_attempt_at = now(),
                   next_attempt_at = CASE WHEN $4 THEN NULL
                                          ELSE now() + make_interval(secs => $5) END
             WHERE accession_number = $1
            """,
            row["accession_number"], reason, attempts, give_up,
            RETRY_BASE.total_seconds() * (2 ** (attempts - 1)),
        )


async def fetch_one(conn, row: dict, *, client: httpx.AsyncClient, bucket: str,
                    r2_prefix: str = R2_PREFIX) -> dict:
    """Fetch, store, classify one manifest row. Raises on any per-filing failure."""
    m = _SUBMISSION_CIK_RE.search(row["submission_path"])
    folder_cik = m.group(1) if m else (row["primary_issuer_cik"] or "")
    if not folder_cik:
        raise EdgarPipelineError("cannot derive the filing folder CIK from submission_path")
    meta = edgar_fetch.FilingMeta(
        cik=str(int(folder_cik)),
        filer_name=await _filer_name(conn, row["accession_number"], str(int(folder_cik))),
        form_type=row["form_type"],
        accession_number=row["accession_number"],
        filing_date=row["filing_date"],
        submission_path=row["submission_path"],
    )
    await edgar_fetch.resolve_filing_documents(meta, client)
    if not meta.primary_document:
        raise EdgarPipelineError("the filing's folder index lists no HTML or text document")
    raw = await edgar_fetch.fetch_filing(meta, client)
    if not raw:
        raise EdgarPipelineError("the main document downloaded as zero bytes")

    content_hash = hashlib.sha256(raw).hexdigest()
    raw_gz = _gzip(raw)
    extraction = edgar_fetch.extract_filing_text(raw)
    text = extraction.text
    text_bytes = text.encode("utf-8")
    text_gz = _gzip(text_bytes)
    base = f"{r2_prefix}/{meta.cik}/{meta.accession_number}/{meta.primary_document}"
    raw_key, text_key = f"{base}.gz", f"{base}.txt.gz"

    status, reason, kind = decide_status(text)
    cusip = detect_cusip(text)

    async with platform_scope(conn):
        existing = await conn.fetchrow(
            """SELECT content_hash, r2_key, text_r2_key, content_encoding
               FROM portfolio.reference_filings
               WHERE accession_number = $1 AND primary_document = $2""",
            meta.accession_number, meta.primary_document,
        )
    already = bool(
        existing and existing["content_hash"] == content_hash
        and existing["r2_key"] == raw_key and existing["text_r2_key"] == text_key
        and existing["content_encoding"] == "gzip"
    )
    uploaded = 0
    if not already:
        await asyncio.to_thread(storage.upload_bytes, raw_key, raw_gz, "application/gzip", bucket)
        await asyncio.to_thread(storage.upload_bytes, text_key, text_gz, "application/gzip", bucket)
        uploaded = len(raw_gz) + len(text_gz)

    async with platform_scope(conn):
        ref_id = await conn.fetchval(
            """
            INSERT INTO portfolio.reference_filings (
                cik, filer_name, form_type, accession_number, filing_date,
                file_number, primary_document, source_url, r2_key, content_hash,
                byte_size, extracted_text, extraction_status, extraction_error,
                retention_classification, content_encoding, compressed_byte_size,
                text_r2_key, text_byte_size, text_compressed_byte_size
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NULL, 'fetched',
                    NULL, 'public_reference', 'gzip', $12, $13, $14, $15)
            ON CONFLICT (accession_number, primary_document) DO UPDATE SET
                cik = EXCLUDED.cik, filer_name = EXCLUDED.filer_name,
                form_type = EXCLUDED.form_type, filing_date = EXCLUDED.filing_date,
                file_number = EXCLUDED.file_number, source_url = EXCLUDED.source_url,
                r2_key = EXCLUDED.r2_key, content_hash = EXCLUDED.content_hash,
                byte_size = EXCLUDED.byte_size, extracted_text = NULL,
                extraction_status = 'fetched', extraction_error = NULL,
                content_encoding = 'gzip',
                compressed_byte_size = EXCLUDED.compressed_byte_size,
                text_r2_key = EXCLUDED.text_r2_key,
                text_byte_size = EXCLUDED.text_byte_size,
                text_compressed_byte_size = EXCLUDED.text_compressed_byte_size,
                updated_at = now()
            RETURNING id
            """,
            meta.cik, meta.filer_name, meta.form_type, meta.accession_number,
            meta.filing_date, meta.file_number, meta.primary_document, meta.source_url,
            raw_key, content_hash, len(raw), len(raw_gz), text_key, len(text_bytes),
            len(text_gz),
        )
        await conn.execute(
            """
            UPDATE portfolio.edgar_index_filings
               SET pipeline_status = $2, status_reason = $3, document_kind = $4,
                   detected_cusip = $5, reference_filing_id = $6, fetched_at = now(),
                   attempt_count = attempt_count + 1, last_attempt_at = now(),
                   next_attempt_at = NULL
             WHERE accession_number = $1
            """,
            meta.accession_number, status, reason[:1500], kind, cusip, ref_id,
        )
    return {
        "accession_number": meta.accession_number, "status": status,
        "document_kind": kind, "cusip": cusip, "reference_filing_id": ref_id,
        "bytes_uploaded": uploaded, "upload_skipped": already,
        "raw_key": raw_key, "text_key": text_key,
    }


# Failures that are about the whole run, not one filing: stop rather than mark
# every remaining filing failed.
_SYSTEMIC = (edgar_fetch.EdgarConfigError, EdgarPipelineConfigError)


def _is_storage_error(exc: BaseException) -> bool:
    return type(exc).__module__.startswith(("botocore", "boto3"))


async def fetch_stage(conn, *, cap: int, client: httpx.AsyncClient,
                      bucket: str | None = None, r2_prefix: str = R2_PREFIX,
                      deadline: float | None = None,
                      accessions: list[str] | None = None,
                      on_batch=None) -> FetchStats:
    """Fetch up to ``cap`` filings, newest first. One failure never stops the run.

    ``deadline`` is a ``time.monotonic()`` value; ``on_batch`` is awaited between
    batches (the job renews its lease there).
    """
    bucket = bucket or r2_bucket()
    stats = FetchStats()
    consecutive = 0
    while stats.attempted < cap and stats.stop_reason is None:
        batch = await _queue(conn, min(FETCH_BATCH, cap - stats.attempted), accessions)
        if not batch:
            stats.stop_reason = "queue empty"
            break
        for row in batch:
            if deadline is not None and time.monotonic() >= deadline:
                stats.stop_reason = "runtime cap reached"
                break
            stats.attempted += 1
            try:
                out = await fetch_one(conn, row, client=client, bucket=bucket, r2_prefix=r2_prefix)
            except _SYSTEMIC:
                raise
            except Exception as exc:  # noqa: BLE001 — one filing's failure is recorded, not fatal
                if _is_storage_error(exc):
                    raise
                await _record_failure(conn, row, exc)
                stats.failed += 1
                stats.failures.append({"accession_number": row["accession_number"],
                                       "error": f"{type(exc).__name__}: {exc}"[:300]})
                consecutive += 1
                if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                    stats.stop_reason = (
                        f"{consecutive} consecutive fetch failures — stopping (SEC block or outage?)"
                    )
                    return stats
                continue
            consecutive = 0
            stats.fetched += 1
            stats.bytes_uploaded += out["bytes_uploaded"]
            stats.uploads_skipped += int(out["upload_skipped"])
            if out["status"] == "ready_for_extraction":
                stats.ready_for_extraction += 1
            elif out["status"] == "prefilter_skipped":
                stats.prefilter_skipped += 1
            else:
                stats.not_pricing_supplement += 1
        if on_batch is not None and stats.stop_reason is None:
            await on_batch()
    if stats.attempted >= cap and stats.stop_reason is None:
        stats.stop_reason = "fetch cap reached"
    return stats


# ═══ The job ════════════════════════════════════════════════════════════════

class RequestLog:
    """httpx event hook: counts every SEC request and keeps (time, User-Agent)."""

    def __init__(self, keep: int = 0):
        self.count = 0
        self.keep = keep
        self.entries: list[tuple[float, str | None, str]] = []

    async def __call__(self, request: httpx.Request) -> None:
        self.count += 1
        if self.keep and len(self.entries) < self.keep:
            self.entries.append((time.monotonic(), request.headers.get("User-Agent"), str(request.url)))


def make_job_client(log: RequestLog) -> httpx.AsyncClient:
    """Same settings as ``edgar_fetch.make_client`` plus the request hook."""
    return httpx.AsyncClient(
        timeout=edgar_fetch.REQUEST_TIMEOUT, follow_redirects=True,
        event_hooks={"request": [log]},
    )


async def run_job(conn, run_id, *, r2_prefix: str = R2_PREFIX, lease_name: str = LEASE_NAME,
                  accessions: list[str] | None = None, client: httpx.AsyncClient | None = None,
                  request_log: RequestLog | None = None) -> dict:
    """Run one pipeline job for the run row ``run_id``. Returns the final row.

    The stages, the fetch cap and the runtime cap come from the run row, so the
    launcher, the job and the monitoring screen all read one record.
    """
    run = await load_run(conn, run_id)
    if run is None:
        raise EdgarPipelineError(f"pipeline run {run_id} does not exist")
    holder = str(run_id)
    if not await acquire_lease(conn, holder, lease_name=lease_name):
        state = await lease_state(conn, lease_name)
        await update_run(
            conn, run_id, status="refused", finished_at=datetime.now(timezone.utc),
            stop_reason=(f"another pipeline job holds the lease (holder {state and state['holder']}, "
                         f"expires {state and state['expires_at']})"),
        )
        return await load_run(conn, run_id)

    edgar_fetch.set_rate_limit(JOB_RATE_LIMIT)
    log = request_log or RequestLog()
    own_client = client is None
    client = client or make_job_client(log)
    started = time.monotonic()
    deadline = started + int(run["runtime_cap_seconds"])
    stages = list(run["stages"])
    await update_run(conn, run_id, status="running", started_at=datetime.now(timezone.utc))
    counts: dict = {}
    details: dict = {}

    async def _renew():
        if not await renew_lease(conn, holder, lease_name=lease_name):
            raise EdgarPipelineError("lost the pipeline lease mid-run")

    try:
        if "discover" in stages:
            d = await discover_incremental(conn, client)
            counts.update(discovery_days=d.days_read, discovered_new=d.new_filings)
            details["discovery"] = {k: v for k, v in asdict(d).items() if k != "days"} | {"days": d.days[-15:]}
            await _renew()
        if "select" in stages:
            s = await select_stage(conn, accessions=accessions)
            counts.update(selected=s["selected"], not_selected=s["not_selected"])
            details["selection"] = s
            await _renew()
        stop_reason = None
        if "fetch" in stages and int(run["fetch_cap"]) > 0:
            f = await fetch_stage(
                conn, cap=int(run["fetch_cap"]), client=client, r2_prefix=r2_prefix,
                deadline=deadline, accessions=accessions, on_batch=_renew,
            )
            counts.update(
                fetch_attempted=f.attempted, fetched=f.fetched, fetch_failed=f.failed,
                ready_for_extraction=f.ready_for_extraction,
                not_pricing_supplement=f.not_pricing_supplement,
                prefilter_skipped=f.prefilter_skipped, bytes_uploaded=f.bytes_uploaded,
            )
            details["fetch"] = {"uploads_skipped": f.uploads_skipped, "failures": f.failures[:50]}
            stop_reason = f.stop_reason
        counts["sec_requests"] = log.count
        await update_run(
            conn, run_id, status="succeeded", finished_at=datetime.now(timezone.utc),
            stop_reason=stop_reason, details=details | {"elapsed_seconds": round(time.monotonic() - started, 1)},
            **counts,
        )
    except Exception as exc:  # noqa: BLE001 — recorded on the run row, then re-raised
        counts["sec_requests"] = log.count
        await update_run(
            conn, run_id, status="failed", finished_at=datetime.now(timezone.utc),
            error=f"{type(exc).__name__}: {exc}"[:2000], details=details, **counts,
        )
        raise
    finally:
        if own_client:
            await client.aclose()
        await release_lease(conn, holder, lease_name=lease_name)
    return await load_run(conn, run_id)


# ═══ The launch (Render one-off job) ════════════════════════════════════════

def render_credentials() -> str:
    key = (os.environ.get("RENDER_API_KEY") or "").strip()
    if not key:
        raise EdgarPipelineConfigError(
            "RENDER_API_KEY is not set, so no Render one-off job can be launched. "
            "Add it to Doppler (synced to the scheduler service)."
        )
    return key


async def render_service_id(http: httpx.AsyncClient, key: str) -> str:
    """The base service a one-off job runs on: same build, same Doppler env.

    ``EDGAR_PIPELINE_RENDER_SERVICE_ID`` wins; otherwise the service named
    ``EDGAR_PIPELINE_RENDER_SERVICE`` (default: the workflow-scheduler cron —
    rootDir apps/api, a paid instance type, and Doppler-synced).
    """
    sid = (os.environ.get("EDGAR_PIPELINE_RENDER_SERVICE_ID") or "").strip()
    if sid:
        return sid
    name = (os.environ.get("EDGAR_PIPELINE_RENDER_SERVICE") or RENDER_SERVICE_NAME_DEFAULT).strip()
    r = await http.get(f"{RENDER_API}/services", params={"name": name, "limit": 20},
                       headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    if r.status_code != 200:
        raise RenderLaunchError(f"Render API GET /services returned HTTP {r.status_code}: {r.text[:300]}")
    for item in r.json():
        svc = item.get("service", item)
        if svc.get("name") == name:
            return svc["id"]
    raise RenderLaunchError(f"no Render service named {name!r} is visible to RENDER_API_KEY")


async def start_render_job(run_id, *, http: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """POST /v1/services/{id}/jobs. Returns ``(service_id, job_id)``. Never waits."""
    key = render_credentials()
    own = http is None
    http = http or httpx.AsyncClient(timeout=30.0)
    try:
        sid = await render_service_id(http, key)
        r = await http.post(
            f"{RENDER_API}/services/{sid}/jobs",
            json={"startCommand": JOB_START_COMMAND.format(run_id=run_id)},
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        if r.status_code not in (200, 201, 202):
            raise RenderLaunchError(f"Render refused the job: HTTP {r.status_code}: {r.text[:300]}")
        job_id = (r.json() or {}).get("id")
        if not job_id:
            raise RenderLaunchError(f"Render accepted the job but returned no id: {r.text[:300]}")
        return sid, job_id
    finally:
        if own:
            await http.aclose()


async def render_job_status(service_id: str, job_id: str) -> dict:
    key = render_credentials()
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.get(f"{RENDER_API}/services/{service_id}/jobs/{job_id}",
                           headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    return r.json() if r.status_code == 200 else {"http_status": r.status_code, "body": r.text[:300]}


async def _blocking_run(conn) -> dict | None:
    """A job that holds the lease, or one launched recently that has not yet
    taken it (the window between Render accepting a job and the job starting)."""
    state = await lease_state(conn)
    if state and state["held"]:
        return {"why": "lease", "holder": state["holder"], "expires_at": state["expires_at"]}
    async with platform_scope(conn):
        row = await conn.fetchrow(
            """SELECT id, status, requested_at FROM portfolio.edgar_pipeline_runs
               WHERE status IN ('launching', 'launched', 'running')
                 AND requested_at > now() - make_interval(secs => $1)
               ORDER BY requested_at DESC LIMIT 1""",
            LAUNCH_GUARD.total_seconds(),
        )
    if row:
        return {"why": "recent_launch", "holder": str(row["id"]), "status": row["status"],
                "requested_at": row["requested_at"]}
    return None


async def launch_pipeline_run(conn, *, trigger_source: str, requested_by=None,
                              fetch_cap: int | None = None, stages=STAGES,
                              runtime_cap_seconds: int = DEFAULT_RUNTIME_CAP_SECONDS,
                              workflow_run_id=None, starter=None) -> dict:
    """Record a run and launch its Render job. RETURNS IMMEDIATELY; never raises
    for a launch that could not happen — the outcome is on the returned row.

    ``starter`` replaces :func:`start_render_job` (tests only).
    """
    fetch_cap = fetch_cap_default() if fetch_cap is None else int(fetch_cap)
    blocking = await _blocking_run(conn)
    if blocking:
        run_id = await create_run(
            conn, trigger_source=trigger_source, requested_by=requested_by,
            fetch_cap=fetch_cap, runtime_cap_seconds=runtime_cap_seconds, stages=stages,
            status="refused", workflow_run_id=workflow_run_id,
        )
        await update_run(conn, run_id, finished_at=datetime.now(timezone.utc),
                         stop_reason=f"another pipeline job is in progress ({blocking['why']}: {blocking['holder']})",
                         details={"blocking": blocking})
        return await load_run(conn, run_id)

    run_id = await create_run(
        conn, trigger_source=trigger_source, requested_by=requested_by, fetch_cap=fetch_cap,
        runtime_cap_seconds=runtime_cap_seconds, stages=stages, workflow_run_id=workflow_run_id,
    )
    try:
        service_id, job_id = await (starter or start_render_job)(run_id)
    except Exception as exc:  # noqa: BLE001 — recorded, never raised (see module docstring)
        await update_run(conn, run_id, status="launch_failed", finished_at=datetime.now(timezone.utc),
                         error=f"{type(exc).__name__}: {exc}"[:2000])
        return await load_run(conn, run_id)
    await update_run(conn, run_id, status="launched", render_service_id=service_id, render_job_id=job_id)
    return await load_run(conn, run_id)
