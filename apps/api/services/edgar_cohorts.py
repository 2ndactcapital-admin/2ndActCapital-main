"""EDGAR cohorts — named, FROZEN lists of filings (edgarcohorts).

A cohort is built on the Filings tab from the same filters the grid uses (or
from rows ticked by hand), then sampled:

    all          every matching filing, newest first
    newest N     the N newest
    oldest N     the N oldest
    random N     N chosen by a SEEDED hash order — hashtextextended(accession,
                 seed): the same seed always gives the same members, a different
                 seed a different set; no client-side randomness is involved.
                 (md5 measured 2x slower over the 537K 'yes' 424B2s. The hash is
                 Postgres's own; a major-version upgrade may change its values,
                 which is one more reason members are FROZEN, not re-derived.)
    stratified   N per stratum, a stratum being issuer group x filing year, or
                 issuer group x era (2019-2021, 2022-2023, 2024-2026); within a
                 stratum the same seeded hash order picks the N

The DEFINITION that built a cohort (source, filters, sampling method,
parameters, seed) is stored on it as JSON, so a cohort can always be explained.
Its members are stored too, in order (``position``) with their stratum label,
because "re-run the definition" would not give the same set once the manifest
grows. Members are DEDUPLICATED BY ACCESSION NUMBER: the manifest is keyed by
accession, issuer filters resolve through ``primary_issuer_cik`` (one issuer
per filing, never a join over every co-listed filer), and a hand-picked list is
de-duplicated before it is stored; the member table's primary key
(cohort_id, accession_number) refuses a duplicate regardless.

FROZEN. ``create_cohort`` inserts the cohort unsealed, inserts its members and
seals it, all in ONE transaction; after that the database refuses any change
(triggers in migrations/edgarcohorts_cohorts_inventory.sql). "Copy and edit"
(``copy_cohort``) creates a new cohort recording what it was copied from.

A cohort has at most ``MAX_COHORT_SIZE`` members; a larger result is refused
before anything is written.

All reads and writes run inside ``platform_scope`` (SET LOCAL
app.is_super_admin in its own transaction). Super-admin only — the router
enforces it; nothing here reads or accepts an org_id.
"""
from __future__ import annotations

import json
import re
from datetime import date

from services import edgar_pipeline
from services import edgar_pipeline_admin as admin
from services.database import platform_scope

MAX_COHORT_SIZE = 50_000
PREVIEW_ROWS = 50
SOURCES = ("filter", "hand")
SAMPLING_METHODS = {
    "all": "All matching",
    "newest": "Newest N",
    "oldest": "Oldest N",
    "random": "Random N (seeded)",
    "stratified": "Stratified: N per stratum",
}
STRATIFY_BY = {
    "issuer_year": "Issuer group x year",
    "issuer_era": "Issuer group x era",
}
ERAS = (("2019-2021", 2019, 2021), ("2022-2023", 2022, 2023), ("2024-2026", 2024, 2026))
NO_ISSUER = "(no listed issuer)"
DEFAULT_SEED = 1
COHORT_KINDS = ("custom", "hand_picked", "template_study", "copy")

# Template study (decision 6): every 'yes' issuer, across the three eras,
# oversampled because document kind is unknown until fetched.
TEMPLATE_STUDY_PER_STRATUM = 8
TEMPLATE_STUDY_FORM = "424B2"

_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class CohortInputError(ValueError):
    """A definition, name or member list the cohort builder refuses."""


class CohortTooLarge(CohortInputError):
    pass


class CohortFrozen(CohortInputError):
    pass


# ═══ Definitions ═══════════════════════════════════════════════════════════
def _int(v, name, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError) as exc:
        raise CohortInputError(f"{name} must be a whole number") from exc
    if not lo <= n <= hi:
        raise CohortInputError(f"{name} must be {lo}..{hi}")
    return n


def normalise_definition(defn: dict) -> dict:
    """Validate a definition and return its canonical, storable form."""
    if not isinstance(defn, dict):
        raise CohortInputError("definition must be an object")
    unknown = set(defn) - {"source", "filters", "accessions", "sampling"}
    if unknown:
        raise CohortInputError(f"unknown definition keys: {sorted(unknown)}")
    source = defn.get("source") or "filter"
    if source not in SOURCES:
        raise CohortInputError(f"source must be one of {list(SOURCES)}")
    out: dict = {"source": source}

    if source == "filter":
        filters = dict(defn.get("filters") or {})
        allowed = {"issuer_group", "issuer_groups", "include_status", "form_type", "status",
                   "document_kind", "date_from", "date_to", "quarter"}
        bad = set(filters) - allowed
        if bad:
            raise CohortInputError(f"unknown filters: {sorted(bad)}")
        clean = {}
        for k, v in filters.items():
            if v in (None, "", []):
                continue
            if k in ("issuer_groups", "include_status"):
                if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                    raise CohortInputError(f"{k} must be a list of strings")
                v = sorted(set(v))
            elif not isinstance(v, str):
                raise CohortInputError(f"{k} must be a string")
            clean[k] = v
        out["filters"] = clean
    else:
        raw = defn.get("accessions")
        if not isinstance(raw, list) or not raw:
            raise CohortInputError("a hand-picked cohort needs a non-empty accessions list")
        seen, accs = set(), []
        for a in raw:
            a = str(a).strip()
            if not _ACCESSION_RE.match(a):
                raise CohortInputError(f"{a!r} is not an accession number (0000000000-00-000000)")
            if a not in seen:            # deduplicate, keep the order ticked
                seen.add(a)
                accs.append(a)
        if len(accs) > MAX_COHORT_SIZE:
            raise CohortTooLarge(f"{len(accs)} filings ticked; a cohort holds at most {MAX_COHORT_SIZE}")
        out["accessions"] = accs

    s = dict(defn.get("sampling") or {"method": "all"})
    method = s.get("method") or "all"
    if method not in SAMPLING_METHODS:
        raise CohortInputError(f"sampling method must be one of {list(SAMPLING_METHODS)}")
    sampling: dict = {"method": method}
    if method in ("newest", "oldest", "random"):
        sampling["n"] = _int(s.get("n"), "n", 1, MAX_COHORT_SIZE)
    if method in ("random", "stratified"):
        sampling["seed"] = _int(s.get("seed", DEFAULT_SEED), "seed", 0, 2**31 - 1)
    if method == "stratified":
        by = s.get("stratify_by") or "issuer_era"
        if by not in STRATIFY_BY:
            raise CohortInputError(f"stratify_by must be one of {list(STRATIFY_BY)}")
        sampling["stratify_by"] = by
        sampling["per_stratum"] = _int(s.get("per_stratum"), "per_stratum", 1, MAX_COHORT_SIZE)
    out["sampling"] = sampling
    return out


def template_study_definition(*, seed: int = DEFAULT_SEED,
                              per_stratum: int = TEMPLATE_STUDY_PER_STRATUM) -> dict:
    """Decision 6: every issuer with include_status 'yes', each era, ~8 each."""
    return normalise_definition({
        "source": "filter",
        "filters": {"include_status": ["yes"], "form_type": TEMPLATE_STUDY_FORM},
        "sampling": {"method": "stratified", "stratify_by": "issuer_era",
                     "per_stratum": per_stratum, "seed": seed},
    })


# ═══ Building the member list ══════════════════════════════════════════════
def _era_sql(col: str) -> str:
    cases = " ".join(f"WHEN {col} BETWEEN DATE '{a}-01-01' AND DATE '{b}-12-31' THEN '{label}'"
                     for label, a, b in ERAS)
    return f"CASE {cases} ELSE 'other' END"


async def _where(conn, defn: dict) -> tuple[str, list]:
    if defn["source"] == "hand":
        return "f.accession_number = ANY($1::text[])", [defn["accessions"]]
    filters = dict(defn["filters"])
    multi_groups = filters.pop("issuer_groups", None)
    include = filters.pop("include_status", None)
    groups = await admin.issuer_groups(conn)
    try:
        where, args = admin._where(filters, groups)   # the Filings tab's own vocabulary checks
    except admin.EdgarAdminInputError as exc:
        raise CohortInputError(str(exc)) from exc
    clauses = [where]
    if multi_groups:
        unknown = [g for g in multi_groups if g not in groups]
        if unknown:
            raise CohortInputError(f"unknown issuer groups {unknown}")
        args.append(multi_groups)
        clauses.append(f"f.primary_issuer_cik IN (SELECT filer_cik FROM portfolio.structured_note_issuers "
                       f"WHERE issuer_group = ANY(${len(args)}::text[]))")
    if include:
        bad = [v for v in include if v not in admin.INCLUDE_STATUS_LABELS]
        if bad:
            raise CohortInputError(f"unknown include_status {bad}")
        args.append(include)
        clauses.append(f"f.primary_issuer_cik IN (SELECT filer_cik FROM portfolio.structured_note_issuers "
                       f"WHERE include_status = ANY(${len(args)}::text[]))")
    return " AND ".join(c for c in clauses if c != "true") or "true", args


async def build_members(conn, defn: dict) -> dict:
    """Run a (normalised) definition against the manifest.

    Returns ``{"matched": n, "members": [(accession, stratum)...] in cohort
    order, "too_large": bool}``. At most MAX_COHORT_SIZE + 1 members are read,
    so an oversized result is detected without reading it all.
    """
    where, args = await _where(conn, defn)
    s = defn["sampling"]
    method = s["method"]
    n = len(args)
    cand = f"""
        cand AS (
            SELECT f.accession_number, f.filing_date,
                   COALESCE(i.issuer_group, '{NO_ISSUER}') AS issuer_group,
                   extract(year FROM f.filing_date)::int AS yr,
                   {_era_sql('f.filing_date')} AS era
            FROM portfolio.edgar_index_filings f
            LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
            WHERE {where}
        )"""
    limit = MAX_COHORT_SIZE + 1
    if method in ("all", "newest", "oldest"):
        d = "ASC" if method == "oldest" else "DESC"
        cap = limit if method == "all" else min(s["n"], limit)
        sql = (f"WITH {cand} SELECT accession_number, NULL::text AS stratum FROM cand "
               f"ORDER BY filing_date {d}, accession_number {d} LIMIT ${n + 1}")
        params = [*args, cap]
    elif method == "random":
        sql = (f"WITH {cand} SELECT accession_number, NULL::text AS stratum FROM cand "
               f"ORDER BY hashtextextended(accession_number, ${n + 1}::bigint), accession_number LIMIT ${n + 2}")
        params = [*args, s["seed"], min(s["n"], limit)]
    else:
        part = "yr::text" if s["stratify_by"] == "issuer_year" else "era"
        sql = f"""
            WITH {cand},
            ranked AS (
                SELECT accession_number, issuer_group || ' | ' || {part} AS stratum,
                       row_number() OVER (PARTITION BY issuer_group, {part}
                                          ORDER BY hashtextextended(accession_number, ${n + 1}::bigint),
                                                   accession_number) AS rk
                FROM cand
            )
            SELECT accession_number, stratum FROM ranked
            WHERE rk <= ${n + 2}
            ORDER BY stratum, rk
            LIMIT ${n + 3}"""
        params = [*args, s["seed"], s["per_stratum"], limit]
    async with platform_scope(conn):
        # The stratified sort over ~540K candidates spilled ~40MB to disk at the
        # default work_mem (3.9s measured); LOCAL to this transaction only.
        await conn.execute("SET LOCAL work_mem = '128MB'")
        matched = await conn.fetchval(f"SELECT count(*) FROM portfolio.edgar_index_filings f WHERE {where}",
                                      *args)
        rows = await conn.fetch(sql, *params)
    members = [(r["accession_number"], r["stratum"]) for r in rows]
    if defn["source"] == "hand":
        missing = sorted(set(defn["accessions"]) - {a for a, _ in members})
        if missing:
            raise CohortInputError(f"{len(missing)} ticked filing(s) are not in the manifest: {missing[:5]}")
        if method == "all":   # a hand-picked list keeps the order it was ticked in
            order = {a: i for i, a in enumerate(defn["accessions"])}
            members.sort(key=lambda m: order[m[0]])
    # Belt and braces: the member table is keyed by accession — never store one twice.
    seen, dedup = set(), []
    for a, st in members:
        if a not in seen:
            seen.add(a)
            dedup.append((a, st))
    return {"matched": int(matched), "members": dedup, "too_large": len(dedup) > MAX_COHORT_SIZE}


def strata_counts(members: list[tuple[str, str | None]]) -> list[dict]:
    counts: dict[str, int] = {}
    for _a, st in members:
        counts[st or "(all)"] = counts.get(st or "(all)", 0) + 1
    return [{"stratum": k, "members": v} for k, v in sorted(counts.items())]


async def preview(conn, definition: dict) -> dict:
    """What a definition WOULD freeze — the count and the members per stratum.
    Nothing is written."""
    defn = normalise_definition(definition)
    built = await build_members(conn, defn)
    members = built["members"][:MAX_COHORT_SIZE]
    sample = await _member_rows(conn, [a for a, _ in members[:PREVIEW_ROWS]])
    for row, (_a, st) in zip(sample, members[:PREVIEW_ROWS]):
        row["stratum"] = st
    return {
        "definition": defn,
        "matched": built["matched"],
        "count": len(built["members"]) if not built["too_large"] else None,
        "too_large": built["too_large"],
        "max_size": MAX_COHORT_SIZE,
        "strata": strata_counts(members),
        "sample": sample,
    }


async def _member_rows(conn, accessions: list[str]) -> list[dict]:
    if not accessions:
        return []
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT f.accession_number, f.form_type, f.filing_date, f.pipeline_status,
                      f.document_kind, i.issuer_group
               FROM portfolio.edgar_index_filings f
               LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
               WHERE f.accession_number = ANY($1::text[])""",
            accessions)
    by = {r["accession_number"]: dict(r) for r in rows}
    return [by[a] for a in accessions if a in by]


# ═══ Saving (frozen) ═══════════════════════════════════════════════════════
def _check_name(name) -> str:
    name = (name or "").strip()
    if not name:
        raise CohortInputError("a cohort needs a name")
    if len(name) > 200:
        raise CohortInputError("name is at most 200 characters")
    return name


async def _insert_frozen(conn, *, name, purpose, kind, definition, members, created_by,
                         copied_from=None) -> str:
    if not members:
        raise CohortInputError("nothing matches — a cohort needs at least one filing")
    if len(members) > MAX_COHORT_SIZE:
        raise CohortTooLarge(
            f"this cohort would have more than {MAX_COHORT_SIZE:,} filings; narrow the filters or sample")
    async with platform_scope(conn):   # one transaction: insert, members, seal
        cid = await conn.fetchval(
            """INSERT INTO portfolio.edgar_cohorts
                   (name, purpose, kind, definition, member_count, copied_from, created_by)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) RETURNING id""",
            name, purpose, kind, json.dumps(definition, default=str), len(members), copied_from, created_by)
        # One INSERT over arrays — COPY FROM is refused on a table with RLS.
        await conn.execute(
            """INSERT INTO portfolio.edgar_cohort_members (cohort_id, accession_number, position, stratum)
               SELECT $1, a, p, s FROM unnest($2::text[], $3::int[], $4::text[]) AS t(a, p, s)""",
            cid, [a for a, _ in members], list(range(1, len(members) + 1)), [st for _, st in members])
        await conn.execute("UPDATE portfolio.edgar_cohorts SET sealed_at = now() WHERE id = $1", cid)
    return str(cid)


async def create_cohort(conn, *, name: str, purpose: str | None, definition: dict,
                        created_by=None, kind: str | None = None) -> dict:
    defn = normalise_definition(definition)
    name = _check_name(name)
    if kind is None:
        kind = "hand_picked" if defn["source"] == "hand" else "custom"
    if kind not in COHORT_KINDS:
        raise CohortInputError(f"kind must be one of {list(COHORT_KINDS)}")
    built = await build_members(conn, defn)
    if built["too_large"]:
        raise CohortTooLarge(
            f"this definition selects more than {MAX_COHORT_SIZE:,} filings; narrow the filters or sample")
    cid = await _insert_frozen(conn, name=name, purpose=purpose, kind=kind, definition=defn,
                               members=built["members"], created_by=created_by)
    return await get_cohort(conn, cid)


async def copy_cohort(conn, source_id, *, name: str, purpose: str | None = None,
                      add: list[str] | None = None, remove: list[str] | None = None,
                      created_by=None) -> dict:
    """"Copy and edit": a NEW cohort = the source's members (in order) minus
    ``remove`` plus ``add`` (appended). The source is never touched."""
    src = await get_cohort(conn, source_id)
    if src is None:
        raise LookupError(f"cohort {source_id} not found")
    name = _check_name(name)
    add = [str(a).strip() for a in (add or [])]
    remove = {str(a).strip() for a in (remove or [])}
    for a in add:
        if not _ACCESSION_RE.match(a):
            raise CohortInputError(f"{a!r} is not an accession number")
    source_members = await cohort_member_list(conn, source_id)
    members = [(a, st) for a, st in source_members if a not in remove]
    have = {a for a, _ in members}
    new = []
    for a in add:
        if a not in have:
            have.add(a)
            new.append(a)
    if new:
        found = {r["accession_number"] for r in await _member_rows(conn, new)}
        missing = [a for a in new if a not in found]
        if missing:
            raise CohortInputError(f"not in the manifest: {missing[:5]}")
        members += [(a, "added") for a in new]
    definition = {"source": "copy", "copied_from": str(source_id),
                  "source_definition": src["definition"],
                  "added": new, "removed": sorted(remove & {a for a, _ in source_members})}
    cid = await _insert_frozen(conn, name=name, purpose=purpose if purpose is not None else src["purpose"],
                               kind="copy", definition=definition, members=members,
                               created_by=created_by, copied_from=source_id)
    return await get_cohort(conn, cid)


# ═══ Reads ═════════════════════════════════════════════════════════════════
def _cohort_dict(r) -> dict:
    d = dict(r)
    if isinstance(d.get("definition"), str):
        d["definition"] = json.loads(d["definition"])
    return d


async def list_cohorts(conn) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT c.id, c.name, c.purpose, c.kind, c.definition, c.member_count, c.copied_from,
                      c.sealed_at, c.created_by, c.created_at,
                      (SELECT count(*) FROM portfolio.edgar_pipeline_runs r WHERE r.cohort_id = c.id)::int AS runs
               FROM portfolio.edgar_cohorts c
               WHERE c.sealed_at IS NOT NULL
               ORDER BY c.created_at DESC""")
    return [_cohort_dict(r) for r in rows]


async def get_cohort(conn, cohort_id) -> dict | None:
    async with platform_scope(conn):
        row = await conn.fetchrow("SELECT * FROM portfolio.edgar_cohorts WHERE id = $1", cohort_id)
    return _cohort_dict(row) if row else None


async def cohort_member_list(conn, cohort_id) -> list[tuple[str, str | None]]:
    """Every member in cohort order: [(accession, stratum)]."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT accession_number, stratum FROM portfolio.edgar_cohort_members "
            "WHERE cohort_id = $1 ORDER BY position", cohort_id)
    return [(r["accession_number"], r["stratum"]) for r in rows]


async def status_counts(conn, cohort_id) -> dict[str, int]:
    """Members by CURRENT manifest status — straight from the manifest."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT f.pipeline_status, count(*)::int AS n
               FROM portfolio.edgar_cohort_members m
               JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
               WHERE m.cohort_id = $1 GROUP BY 1""", cohort_id)
    return {r["pipeline_status"]: r["n"] for r in rows}


async def stratum_counts(conn, cohort_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT COALESCE(stratum, '(all)') AS stratum, count(*)::int AS members
               FROM portfolio.edgar_cohort_members WHERE cohort_id = $1 GROUP BY 1 ORDER BY 1""",
            cohort_id)
    return [dict(r) for r in rows]


async def cohort_runs(conn, cohort_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, run_kind, trigger_source, status, fetch_cap, render_job_id, requested_at,
                      started_at, finished_at, fetch_attempted, fetched, fetch_failed,
                      ready_for_extraction, not_pricing_supplement, prefilter_skipped,
                      stop_reason, error, details
               FROM portfolio.edgar_pipeline_runs WHERE cohort_id = $1
               ORDER BY requested_at DESC""", cohort_id)
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("details"), str):
            d["details"] = json.loads(d["details"])
        out.append(d)
    return out


async def cohort_members_page(conn, cohort_id, *, status: str | None = None, page: int = 1,
                              page_size: int = 50) -> dict:
    if status and status not in admin.STATUS_LABELS:
        raise CohortInputError(f"unknown status {status!r}")
    if not 1 <= int(page_size) <= admin.MAX_PAGE_SIZE:
        raise CohortInputError(f"page_size must be 1..{admin.MAX_PAGE_SIZE}")
    if int(page) < 1:
        raise CohortInputError("page must be >= 1")
    async with platform_scope(conn):
        total = await conn.fetchval(
            """SELECT count(*) FROM portfolio.edgar_cohort_members m
               JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
               WHERE m.cohort_id = $1 AND ($2::text IS NULL OR f.pipeline_status = $2)""",
            cohort_id, status)
        rows = await conn.fetch(
            """SELECT m.position, m.stratum, f.accession_number, f.form_type, f.filing_date,
                      f.pipeline_status, f.status_reason, f.document_kind, f.detected_cusip,
                      f.selection_policy_version, f.selected_by_cohort_id, f.submission_path,
                      i.issuer_group
               FROM portfolio.edgar_cohort_members m
               JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
               LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
               WHERE m.cohort_id = $1 AND ($2::text IS NULL OR f.pipeline_status = $2)
               ORDER BY m.position
               LIMIT $3 OFFSET $4""",
            cohort_id, status, int(page_size), (int(page) - 1) * int(page_size))
    out = []
    for r in rows:
        d = dict(r)
        d["sec_filing_url"] = admin.sec_filing_url(d.pop("submission_path"), d["accession_number"])
        out.append(d)
    return {"rows": out, "total": int(total), "page": int(page), "page_size": int(page_size)}


async def ready_reference_ids(conn, cohort_id, *, statuses=("ready_for_extraction",)) -> list[str]:
    """The cohort's members that are READY for extraction (a stored document,
    manifest status in ``statuses``), as reference_filing ids in COHORT ORDER.
    B1's pilot, harness and gold sampler take this instead of their own sampler."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT f.reference_filing_id
               FROM portfolio.edgar_cohort_members m
               JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
               WHERE m.cohort_id = $1 AND f.pipeline_status = ANY($2::text[])
                 AND f.reference_filing_id IS NOT NULL
               ORDER BY m.position""",
            cohort_id, list(statuses))
    return [str(r["reference_filing_id"]) for r in rows]


# ═══ Running ═══════════════════════════════════════════════════════════════
async def launch_cohort_run(conn, cohort_id, *, run_kind: str = "fetch", fetch_cap: int | None = None,
                            requested_by=None, trigger_source: str = "manual", starter=None) -> dict:
    """Launch the SAME Render job against exactly this cohort's members.
    Same lease, rate limit and runtime cap as every other run."""
    cohort = await get_cohort(conn, cohort_id)
    if cohort is None or cohort.get("sealed_at") is None:
        raise LookupError(f"cohort {cohort_id} not found")
    edgar_pipeline.check_run_kind(run_kind)
    cap = cohort["member_count"] if fetch_cap is None else int(fetch_cap)
    cap = min(cap, edgar_pipeline.MAX_FETCH_CAP)
    return await edgar_pipeline.launch_pipeline_run(
        conn, trigger_source=trigger_source, requested_by=requested_by, fetch_cap=cap,
        cohort_id=cohort["id"], run_kind=run_kind, starter=starter)


def era_of(d: date | None) -> str:
    if d is None:
        return "other"
    for label, a, b in ERAS:
        if a <= d.year <= b:
            return label
    return "other"
