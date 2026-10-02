"""Queries and edits behind the EDGAR pipeline monitoring screen (super-admin).

Every list here is SERVER-SIDE filtered, sorted and paged: the manifest is
~717K rows and never crosses the wire whole. Filter values and sort keys are
checked against the vocabularies published in the response envelope, and a
column identifier only ever reaches SQL from the frozen ``SORT_COLUMNS`` map —
never from the request.

Filtering by issuer group resolves to ``primary_issuer_cik = ANY(group's
CIKs)``, which the ``edgar_index_filings_primary_issuer_idx`` index serves; the
old per-row issuer lookup (v_edgar_filings_explorer's LATERAL) is what made the
same filter slow.
"""
from __future__ import annotations

import re
from datetime import date

from services.database import platform_scope

# Display labels travel in the envelope (Rule 1), keyed by the stored value.
STATUS_LABELS = {
    "discovered": "Discovered",
    "selected": "Selected",
    "not_selected": "Not selected",
    "fetched": "Fetched",
    "fetch_failed": "Fetch failed",
    "not_pricing_supplement": "Not a pricing supplement",
    "prefilter_skipped": "Prefilter skipped",
    "ready_for_extraction": "Ready for extraction",
    "extraction_submitted": "Extraction submitted",
    "extracted": "Extracted",
    "needs_review": "Needs review",
    "extraction_failed": "Extraction failed",
}
DOCUMENT_KIND_LABELS = {
    "pricing_supplement": "Pricing supplement",
    "preliminary_pricing_supplement": "Preliminary pricing supplement",
    "product_supplement": "Product supplement",
    "underlying_supplement": "Underlying supplement",
    "term_sheet": "Term sheet",
    "other": "Other",
}
FORM_TYPES = ("424B2", "FWP")
INCLUDE_STATUS_LABELS = {"yes": "Include", "review": "Review", "no": "Exclude"}
FILER_ROLE_LABELS = {"issuer": "Issuer", "guarantor": "Guarantor"}
UNLISTED_GROUP = "__unlisted__"

# sort key -> SQL expression. The ONLY path from a request to an identifier.
#
# Issuer group is a FILTER, not a sort key: it lives on the issuer table, so no
# index on the manifest can serve "order 717K rows by a joined name" — measured
# at ~4s even paged-first. Every key below has an index whose column order
# matches its ORDER BY (see _order_by), so each sorted page is an index walk.
SORT_COLUMNS = {
    "filing_date": "f.filing_date",
    "accession_number": "f.accession_number",
    "form_type": "f.form_type",
    "pipeline_status": "f.pipeline_status",
    "document_kind": "f.document_kind",
    "index_quarter": "f.index_quarter",
}
ISSUER_EDITABLE = ("include_status", "credit_entity", "notes")
MAX_PAGE_SIZE = 200
FILTER_FIRST_MAX_ROWS = 40_000
_QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")
_CIK_RE = re.compile(r"^\d{1,10}$")


class EdgarAdminInputError(ValueError):
    """A filter, sort or edit value outside the published vocabulary."""


def sec_filing_url(submission_path: str, accession: str) -> str:
    folder = re.sub(r"[^/]+\.txt$", "", submission_path)
    return (f"https://www.sec.gov/Archives/{folder}{accession.replace('-', '')}/"
            f"{accession}-index.htm")


async def issuer_groups(conn) -> list[str]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT DISTINCT issuer_group FROM portfolio.structured_note_issuers ORDER BY 1"
        )
    return [r["issuer_group"] for r in rows]


async def quarters(conn) -> list[str]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT DISTINCT index_quarter FROM portfolio.edgar_index_filings ORDER BY 1 DESC"
        )
    return [r["index_quarter"] for r in rows]


def _where(filters: dict, groups: list[str]) -> tuple[str, list]:
    clauses, args = [], []

    def arg(v):
        args.append(v)
        return f"${len(args)}"

    g = filters.get("issuer_group")
    if g:
        if g == UNLISTED_GROUP:
            clauses.append("f.primary_issuer_cik IS NULL")
        elif g in groups:
            clauses.append(
                "f.primary_issuer_cik = ANY (SELECT filer_cik FROM portfolio.structured_note_issuers "
                f"WHERE issuer_group = {arg(g)})"
            )
        else:
            raise EdgarAdminInputError(f"unknown issuer group {g!r}")
    for key, vocab, col in (
        ("form_type", FORM_TYPES, "f.form_type"),
        ("status", tuple(STATUS_LABELS), "f.pipeline_status"),
        ("document_kind", tuple(DOCUMENT_KIND_LABELS), "f.document_kind"),
    ):
        v = filters.get(key)
        if v:
            if v not in vocab:
                raise EdgarAdminInputError(f"unknown {key} {v!r}")
            clauses.append(f"{col} = {arg(v)}")
    for key, op in (("date_from", ">="), ("date_to", "<=")):
        v = filters.get(key)
        if v:
            if isinstance(v, str):
                try:
                    v = date.fromisoformat(v)
                except ValueError as exc:
                    raise EdgarAdminInputError(f"{key} must be YYYY-MM-DD") from exc
            clauses.append(f"f.filing_date {op} {arg(v)}::date")
    q = filters.get("quarter")
    if q:
        if not _QUARTER_RE.match(q):
            raise EdgarAdminInputError("quarter must look like 2025Q1")
        clauses.append(f"f.index_quarter = {arg(q)}")
    return (" AND ".join(clauses) or "true"), args


def _order_by(sort: str, direction: str) -> str:
    """ORDER BY whose column order matches an index, forward or backward.

    filing_date / accession_number sort on themselves. A CATEGORY sort
    (form, status, kind, quarter) breaks ties newest-first when ascending and
    oldest-first when descending — exactly the mirror image of its
    (category, filing_date DESC, accession DESC) index, so either direction is
    an index walk. A uniform "always newest first" tie-break would force a sort
    of the whole leading category (700K 'discovered' rows) for every page.
    """
    d = direction.upper()
    if sort == "filing_date":
        return f"f.filing_date {d}, f.accession_number {d}"
    if sort == "accession_number":
        return f"f.accession_number {d}"
    tie = "DESC" if d == "ASC" else "ASC"
    return f"{SORT_COLUMNS[sort]} {d}, f.filing_date {tie}, f.accession_number {tie}"


async def list_filings(conn, *, filters: dict, sort: str = "filing_date",
                       direction: str = "desc", page: int = 1, page_size: int = 50) -> dict:
    if sort not in SORT_COLUMNS:
        raise EdgarAdminInputError(f"unknown sort {sort!r}")
    if direction not in ("asc", "desc"):
        raise EdgarAdminInputError("direction must be asc or desc")
    if not 1 <= int(page_size) <= MAX_PAGE_SIZE:
        raise EdgarAdminInputError(f"page_size must be 1..{MAX_PAGE_SIZE}")
    if int(page) < 1:
        raise EdgarAdminInputError("page must be >= 1")
    groups = await issuer_groups(conn)
    where, args = _where(filters, groups)
    order = _order_by(sort, direction)
    n = len(args)
    async with platform_scope(conn):
        total = await conn.fetchval(
            f"SELECT count(*) FROM portfolio.edgar_index_filings f WHERE {where}", *args
        )
        # Page the manifest FIRST, then join the issuer and filer name for that
        # page only. Joining before the LIMIT made the filer lookup run once per
        # matching row (128K for one issuer group: 8s measured).
        #
        # When the filter is selective, filter FIRST (a MATERIALIZED CTE): left
        # to itself the planner walked the sort index backward and discarded
        # 700K rows to find a 6K-row quarter+form match (15.7s measured). The
        # count above already says how big the match is, so this is decided on
        # a real number, not an estimate.
        source = "portfolio.edgar_index_filings"
        prefix = ""
        if where != "true" and int(total) <= FILTER_FIRST_MAX_ROWS:
            prefix = (f"filtered AS MATERIALIZED (SELECT * FROM portfolio.edgar_index_filings f "
                      f"WHERE {where}), ")
            source, where = "filtered", "true"
        rows = await conn.fetch(
            f"""
            WITH {prefix}page AS (
                SELECT f.accession_number, f.form_type, f.filing_date, f.index_quarter,
                       f.submission_path, f.filer_count, f.pipeline_status, f.status_reason,
                       f.document_kind, f.selection_policy_version, f.attempt_count,
                       f.last_attempt_at, f.next_attempt_at, f.fetched_at, f.detected_cusip,
                       f.reference_filing_id, f.primary_issuer_cik
                FROM {source} f
                WHERE {where}
                ORDER BY {order}
                LIMIT ${n + 1} OFFSET ${n + 2}
            )
            SELECT f.*, i.issuer_group, i.credit_entity, i.include_status,
                   pf.company_name AS primary_filer_name
            FROM page f
            LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
            LEFT JOIN portfolio.edgar_index_filing_filers pf
                   ON pf.accession_number = f.accession_number AND pf.cik = f.primary_issuer_cik
            ORDER BY {order}
            """,
            *args, int(page_size), (int(page) - 1) * int(page_size),
        )
    out = []
    for r in rows:
        d = dict(r)
        d["sec_filing_url"] = sec_filing_url(d.pop("submission_path"), d["accession_number"])
        out.append(d)
    return {"rows": out, "total": int(total), "page": int(page), "page_size": int(page_size),
            "sort": sort, "direction": direction}


async def progress(conn, *, runs_limit: int = 25) -> dict:
    async with platform_scope(conn):
        by_quarter = await conn.fetch(
            """SELECT index_quarter, pipeline_status, count(*)::int AS n
               FROM portfolio.edgar_index_filings
               GROUP BY 1, 2 ORDER BY 1 DESC, 2"""
        )
        runs = await conn.fetch(
            """SELECT id, trigger_source, status, stages, fetch_cap, runtime_cap_seconds,
                      render_job_id, requested_at, started_at, finished_at,
                      discovery_days, discovered_new, selected, not_selected,
                      fetch_attempted, fetched, fetch_failed, ready_for_extraction,
                      not_pricing_supplement, prefilter_skipped, bytes_uploaded,
                      sec_requests, stop_reason, error
               FROM portfolio.edgar_pipeline_runs
               ORDER BY requested_at DESC LIMIT $1""",
            runs_limit,
        )
        lease = await conn.fetchrow(
            """SELECT holder, acquired_at, renewed_at, expires_at, expires_at > now() AS held
               FROM portfolio.edgar_pipeline_lease WHERE lease_name = 'edgar_pipeline'"""
        )
        policy = await conn.fetchrow(
            "SELECT version, rules, description FROM portfolio.edgar_selection_policies WHERE is_active"
        )
    quarters_map: dict[str, dict] = {}
    for r in by_quarter:
        q = quarters_map.setdefault(r["index_quarter"], {"index_quarter": r["index_quarter"], "total": 0})
        q[r["pipeline_status"]] = r["n"]
        q["total"] += r["n"]
    return {
        "by_quarter": list(quarters_map.values()),
        "runs": [dict(r) for r in runs],
        "lease": dict(lease) if lease else None,
        "policy": dict(policy) if policy else None,
    }


async def list_issuers(conn) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT i.filer_cik, i.issuer_group, i.filer_name, i.filer_role,
                      i.credit_entity, i.include_status, i.notes, i.updated_at,
                      (SELECT count(*) FROM portfolio.edgar_index_filings f
                        WHERE f.primary_issuer_cik = i.filer_cik)::int AS primary_filings
               FROM portfolio.structured_note_issuers i
               ORDER BY i.issuer_group, i.filer_role, i.filer_cik"""
        )
    return [dict(r) for r in rows]


async def unlisted_filers(conn, *, limit: int = 100) -> list[dict]:
    """Filers of 424B2s with NO listed issuer among their filers — the
    candidates for promotion. Surfaced only; never auto-included."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT ff.cik, max(ff.company_name) AS company_name,
                      count(*)::int AS filings_424b2,
                      min(f.filing_date) AS first_filed, max(f.filing_date) AS last_filed
               FROM portfolio.edgar_index_filings f
               JOIN portfolio.edgar_index_filing_filers ff ON ff.accession_number = f.accession_number
               WHERE f.form_type = '424B2' AND f.primary_issuer_cik IS NULL
               GROUP BY ff.cik
               ORDER BY count(*) DESC, ff.cik
               LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def _reopen_selection(conn, ciks: list[str]) -> int:
    """Send undecided-by-fetch rows of the affected filings back to 'discovered'
    so the next job re-selects them under the new issuer settings. Rows already
    fetched (or further) are left alone."""
    return int((await conn.execute(
        """UPDATE portfolio.edgar_index_filings f
              SET pipeline_status = 'discovered', selection_policy_version = NULL,
                  status_reason = 'issuer table changed; awaiting re-selection'
            WHERE f.pipeline_status IN ('selected', 'not_selected')
              AND f.accession_number IN (SELECT accession_number
                                           FROM portfolio.edgar_index_filing_filers
                                          WHERE cik = ANY($1::text[]))"""
        , ciks)).split()[-1])


def _check_include(v):
    if v not in INCLUDE_STATUS_LABELS:
        raise EdgarAdminInputError(f"include_status must be one of {list(INCLUDE_STATUS_LABELS)}")


async def update_issuer(conn, cik: str, changes: dict) -> dict:
    """Edit include_status / credit_entity / notes. The AFTER UPDATE trigger on
    the issuer table recomputes primary_issuer_cik for every filing listing it."""
    bad = set(changes) - set(ISSUER_EDITABLE)
    if bad:
        raise EdgarAdminInputError(f"not editable: {sorted(bad)}")
    if not changes:
        raise EdgarAdminInputError("nothing to change")
    if "include_status" in changes:
        _check_include(changes["include_status"])
    if "credit_entity" in changes and not (changes["credit_entity"] or "").strip():
        raise EdgarAdminInputError("credit_entity cannot be blank")
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(changes, start=2))
    async with platform_scope(conn):
        before = await conn.fetchrow(
            "SELECT include_status FROM portfolio.structured_note_issuers WHERE filer_cik = $1", cik
        )
        if before is None:
            return {}
        row = await conn.fetchrow(
            f"""UPDATE portfolio.structured_note_issuers SET {sets}, updated_at = now()
                WHERE filer_cik = $1 RETURNING *""",
            cik, *changes.values(),
        )
        reopened = 0
        if "include_status" in changes and changes["include_status"] != before["include_status"]:
            reopened = await _reopen_selection(conn, [cik])
    return {"issuer": dict(row), "reopened_for_selection": reopened}


async def add_issuer(conn, payload: dict) -> dict:
    cik = str(payload.get("filer_cik") or "").strip()
    if not _CIK_RE.match(cik):
        raise EdgarAdminInputError("filer_cik must be 1-10 digits")
    cik = str(int(cik))
    if payload.get("filer_role") not in FILER_ROLE_LABELS:
        raise EdgarAdminInputError(f"filer_role must be one of {list(FILER_ROLE_LABELS)}")
    _check_include(payload.get("include_status"))
    for k in ("issuer_group", "filer_name", "credit_entity"):
        if not (payload.get(k) or "").strip():
            raise EdgarAdminInputError(f"{k} is required")
    async with platform_scope(conn):
        exists = await conn.fetchval(
            "SELECT 1 FROM portfolio.structured_note_issuers WHERE filer_cik = $1", cik)
        if exists:
            raise EdgarAdminInputError(f"issuer {cik} already exists")
        row = await conn.fetchrow(
            """INSERT INTO portfolio.structured_note_issuers
                   (filer_cik, issuer_group, filer_name, filer_role, credit_entity,
                    include_status, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
            cik, payload["issuer_group"].strip(), payload["filer_name"].strip(),
            payload["filer_role"], payload["credit_entity"].strip(),
            payload["include_status"], payload.get("notes"),
        )
        reopened = await _reopen_selection(conn, [cik])
    return {"issuer": dict(row), "reopened_for_selection": reopened}
