"""verify_edgarpipelinea.py — EDGAR pipeline A: lifecycle, selection, discovery,
fetch-to-R2, the job + lease, the nightly workflow, and the monitoring screen.

WHAT WAS BUILT (edgarpipelinea.structural; this script proves it):
  * migrations/edgarindex_discovery_tables.sql — the four hand-made discovery
    objects, generated from the live DDL; migrations/edgarpipelinea_lifecycle.sql
    — the full status lifecycle CHECK, the new manifest columns,
    primary_issuer_cik (+ function + issuer-table triggers), versioned immutable
    selection policies, pipeline runs, the job lease, reference_filings gzip
    columns, sort indexes, and the DATA steps (policy v1, backfill, linking the
    existing documents, the VERIFY FIXTURE delete, the nightly workflow).
  * services/edgar_index.py (parser + loader shared with scripts/load_edgar_index.py),
    services/edgar_pipeline.py (discovery, selection, fetch, classification,
    CUSIP, lease, runs, Render launch, the job), edgar_pipeline_job.py (the
    Render job entrypoint), services/assistant_actions/edgar_ops.py (the
    nightly workflow's one automated step), services/edgar_pipeline_admin.py +
    routers/edgar_pipeline_admin.py (the monitoring API), and the web screen
    /admin/edgar-pipeline (DataGrid extended with a serverSide mode).

Hydrates secrets from Doppler over HTTPS at startup (_db_bootstrap). Never
prints a credential value. RLS context is set on EVERY read
(``app.is_super_admin`` LOCAL inside an explicit transaction).

REAL DATA IS NOT CHANGED. The ~717K manifest rows are fingerprinted before and
after (md5 over every row's text). The real sample filings (up to 2 per group) the fetch test
touches are snapshotted whole and restored exactly; their reference_filings rows
and every R2 object under the TEST prefix are deleted in teardown. Fixture rows
use accession prefix 9999999999-55- and CIKs 9999999551..3.

THIS SCRIPT MAKES REAL EXTERNAL CALLS: ~40 SEC requests (one daily index, the
sample's folder indexes, headers and documents) at <= 8/s with the declared
User-Agent, R2 uploads under the test prefix, and ONE Render one-off job launch
(stages=[], fetch_cap=0 — it changes nothing) through the real nightly workflow.

ASSERTIONS:
  [Y] Task 1's five findings reported
  [Y] The migration file's definitions match the live objects
  [Y] The lifecycle CHECK accepts every listed status and refuses an unknown one
  [Y] primary_issuer_cik: co-listed parent/subsidiary resolves to the subsidiary;
      filtering by issuer group on the full table executes in under one second
      (the database's own EXPLAIN ANALYZE Execution Time, not wall clock)
  [Y] Policy v1 selects exactly the expected set on a fixture sample, records its
      version on every decided row; an edit to a stored policy is refused
  [Y] Incremental discovery over one fixed past day is idempotent; co-listed
      duplicates collapse to one filing
  [Y] The configured R2 bucket is valid and holds the existing corpus (no fallback)
  [Y] Fetch on the sample (every count derived from its REAL filings): R2 objects exist and are gzipped; sha256 of the
      decompressed bytes matches; sizes recorded; a second run re-uploads
      nothing; statuses are past 'fetched'
  [Y] A bad accession becomes fetch_failed with reason, attempt count and retry
      time — and the run finishes the others
  [Y] Classification rules; a preliminary pricing supplement is never ready
  [Y] CUSIP rule: valid accepted, wrong check digit rejected
  [Y] Measured SEC request rate never exceeds 8/second; every request carried
      the declared User-Agent
  [Y] The lease: a second job is refused; an expired lease can be taken over
  [Y] The fetch cap is honored
  [Y] The nightly workflow has no human step; its run completes immediately;
      the job is recorded in the pipeline-runs table (BLOCKED if no Render key)
  [Y] The existing documents are linked and marked fetched, zero new downloads
  [Y] The VERIFY FIXTURE row is gone
  [Y] Monitoring API: super-admin 200; org admin and member 403 on the IDENTICAL request
  [Y] Server-side paging returns correct totals and pages; sorting and filtering work
  [Y] An issuer edit by super-admin persists and recomputes primary_issuer_cik;
      the same edit by an org admin is refused and leaves the row unchanged
  [Y] Teardown: test R2 objects deleted; fixture rows gone; manifest restored exactly
  [Y] npm run build exits 0

Pass/fail only. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on failure.

Run:  python3 apps/api/scripts/verify_edgarpipelinea.py
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"
MIGRATIONS = [API_DIR / "migrations" / "edgarindex_discovery_tables.sql",
              API_DIR / "migrations" / "edgarpipelinea_lifecycle.sql"]

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — the "ed9a" block, the 9999999999-55- accession prefix and CIKs
# 9999999551..3 are unused by any other verify script (grep'd). Every unique
# value uses a FULL literal, never a slice of a UUID.
# ═══════════════════════════════════════════════════════════════════════════
ORG = UUID("99000000-0000-0000-0000-0000ed9a0001")
U_SUPER = UUID("99000000-0000-0000-0000-0000ed9a0011")     # users.role super_admin; holds only 'member'
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000ed9a0012")  # real org_admin GRANT
U_MEMBER = UUID("99000000-0000-0000-0000-0000ed9a0013")    # only 'member' (zero permissions)
FIXTURE_USERS = [U_SUPER, U_ORGADMIN, U_MEMBER]
PLATFORM_ORG = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")

ACC_PREFIX = "9999999999-55-"
F_YES = "9999999999-55-555001"         # 424B2, JPM finance sub (issuer, yes)       -> selected
F_REVIEW = "9999999999-55-555002"      # 424B2, Barclays PLC (issuer, review)       -> selected
F_UNLISTED = "9999999999-55-555003"    # 424B2, an unlisted filer only              -> not_selected
F_FWP = "9999999999-55-555004"         # FWP, JPM finance sub                       -> not_selected
F_OLD = "9999999999-55-555005"         # 424B2 filed 2018-12-31                     -> not_selected
F_NO = "9999999999-55-555006"          # 424B2, fixture issuer with include 'no'    -> not_selected
F_COLISTED = "9999999999-55-555007"    # 424B2, JPM parent (guarantor) + sub (issuer)
F_PROMOTE = "9999999999-55-555008"     # 424B2, JPM parent + a filer the verify promotes
F_BAD = "9999999999-55-555009"         # 424B2, selected, nonexistent on EDGAR
F_SYN_A = "9999999999-55-555010"       # synthetic daily-index line, co-listed twice
F_SYN_B = "9999999999-55-555011"       # synthetic daily-index line, one filer
CIK_UNLISTED = "9999999551"
CIK_NO = "9999999552"
CIK_PROMOTE = "9999999553"
JPM_SUB, JPM_PARENT, BARCLAYS_PLC = "1665650", "19617", "312069"
FIXTURE_CIKS = [CIK_UNLISTED, CIK_NO, CIK_PROMOTE]

TEST_PREFIX = "reference/edgar-verify-edgarpipelinea"
VERIFY_LEASE = "verify-edgarpipelinea"
FIXED_DAY = date(2025, 3, 14)          # a Friday in a fully loaded quarter
SAMPLE_GROUPS = ["JPMorgan", "Morgan Stanley", "Goldman Sachs", "UBS", "Royal Bank of Canada"]
HEADERS = {"Authorization": "Bearer verify-token"}
ALL_STATUSES = [
    "discovered", "selected", "not_selected", "fetched", "fetch_failed",
    "not_pricing_supplement", "prefilter_skipped", "ready_for_extraction",
    "extraction_submitted", "extracted", "needs_review", "extraction_failed",
]
POST_FETCH = {"not_pricing_supplement", "prefilter_skipped", "ready_for_extraction"}

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []
_blocked: list[str] = []

# Things teardown must undo.
STATE: dict = {
    "pipeline_runs": set(), "workflow_runs": set(), "sample": [], "sample_rows": {},
    "real_inserted": [], "render_job": None,
}


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} (type {type(passed).__name__}), "
        f"not a bool, for label={label!r} — reversed-argument guard"
    )
    global _ok, _n_pass, _n_fail
    line = f"{'[PASS]' if passed else '[FAIL]'} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


def blocked(label: str, detail: str = "") -> None:
    print(f"[BLOCKED] {label}" + (f"  — {detail}" if detail else ""))
    _blocked.append(label)


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers — every read and write sets super-admin context in its own txn
# ═══════════════════════════════════════════════════════════════════════════
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def rls_fetch(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(q, *a)


async def rls_row(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(q, *a)


async def rls_val(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(q, *a)


async def rls_exec(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.execute(q, *a)


class _ExplainingConn:
    """Passes every call through to ``conn``; each read statement is first run
    under EXPLAIN (ANALYZE, FORMAT JSON) with the same arguments, recording the
    server's own "Execution Time" in ms. ``execute`` (the RLS set_config) is
    passed through untimed."""

    def __init__(self, conn):
        self._conn = conn
        self.timings: list[tuple[str, float]] = []

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def _explain(self, sql: str, args) -> None:
        plan = await self._conn.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *args)
        plan = json.loads(plan) if isinstance(plan, str) else plan
        self.timings.append((" ".join(sql.split())[:80], float(plan[0]["Execution Time"])))

    async def fetch(self, sql, *args, **kw):
        await self._explain(sql, args)
        return await self._conn.fetch(sql, *args, **kw)

    async def fetchrow(self, sql, *args, **kw):
        await self._explain(sql, args)
        return await self._conn.fetchrow(sql, *args, **kw)

    async def fetchval(self, sql, *args, **kw):
        await self._explain(sql, args)
        return await self._conn.fetchval(sql, *args, **kw)


async def manifest_fingerprint(conn) -> tuple:
    """(row count, md5 over every real manifest row's full text)."""
    row = await rls_row(
        conn,
        """SELECT count(*) AS n,
                  md5(string_agg(md5(f::text), '' ORDER BY f.accession_number)) AS h
           FROM portfolio.edgar_index_filings f
           WHERE f.accession_number NOT LIKE $1""",
        ACC_PREFIX + "%",
    )
    return int(row["n"]), row["h"]


async def issuers_fingerprint(conn) -> str:
    return await rls_val(
        conn,
        """SELECT md5(string_agg(md5(i::text), '' ORDER BY i.filer_cik))
           FROM portfolio.structured_note_issuers i WHERE NOT (i.filer_cik = ANY($1::text[]))""",
        FIXTURE_CIKS,
    )


def _sub(uid: UUID) -> str:
    return f"edgarpipelinea_{uid.hex}"


# ═══════════════════════════════════════════════════════════════════════════
# R2 helpers (the verify's own boto3 reads, independent of the pipeline code)
# ═══════════════════════════════════════════════════════════════════════════
def _s3():
    from services import storage
    return storage.get_s3_client()


def r2_list(bucket: str, prefix: str) -> list[dict]:
    out, token = [], None
    client = _s3()
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        r = client.list_objects_v2(**kw)
        out += r.get("Contents", [])
        if not r.get("IsTruncated"):
            return out
        token = r.get("NextContinuationToken")


def r2_get(bucket: str, key: str) -> bytes:
    return _s3().get_object(Bucket=bucket, Key=key)["Body"].read()


def r2_delete_prefix(bucket: str, prefix: str) -> int:
    objs = r2_list(bucket, prefix)
    client = _s3()
    for o in objs:
        client.delete_object(Bucket=bucket, Key=o["Key"])
    return len(objs)


def resolve_bucket() -> tuple[str | None, str]:
    """The pipeline's configured bucket, or (None, why) if it is invalid."""
    from services import edgar_pipeline as p
    try:
        return p.r2_bucket(), "configured"
    except p.EdgarPipelineConfigError as exc:
        return None, str(exc)


# ═══════════════════════════════════════════════════════════════════════════
# HTTP through the real app, on THIS event loop (httpx.ASGITransport)
#
# The app's asyncpg pool (services.database, module-global) belongs to the loop
# that created it. A TestClient in an executor thread runs the app on its OWN
# loop, so the moment this process has already created the pool on the main
# loop (nightly_workflow does, via get_pool), every request crashes on a
# foreign-loop pool. main_async() creates the pool here, on this loop, before
# the API sections; ASGITransport then calls the app on the same loop. It runs
# no startup hook, which is fine: register_all() is called in main_async and
# the catalog sync is no-op'd there anyway.
# ═══════════════════════════════════════════════════════════════════════════
async def api(uid, method, path, body=None):
    import httpx
    import main

    sub = _sub(uid)
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG)}
    kw = {"headers": HEADERS}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify") as client:
        res = await client.request(method, path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def _detail(body) -> str:
    return str(body.get("detail", body))[:200] if isinstance(body, dict) else str(body)[:200]


# ═══════════════════════════════════════════════════════════════════════════
# Seed / teardown
# ═══════════════════════════════════════════════════════════════════════════
def _line(cik, name, form, day: date, acc):
    return f"{cik}|{name}|{form}|{day.strftime('%Y%m%d')}|edgar/data/{cik}/{acc}.txt"


FIXTURE_LINES = [
    _line(JPM_SUB, "JPMorgan Chase Financial Co. LLC", "424B2", date(2025, 3, 14), F_YES),
    _line(BARCLAYS_PLC, "BARCLAYS PLC", "424B2", date(2025, 3, 14), F_REVIEW),
    _line(CIK_UNLISTED, "VERIFY UNLISTED FILER", "424B2", date(2025, 3, 14), F_UNLISTED),
    _line(JPM_SUB, "JPMorgan Chase Financial Co. LLC", "FWP", date(2025, 3, 14), F_FWP),
    _line(JPM_SUB, "JPMorgan Chase Financial Co. LLC", "424B2", date(2018, 12, 31), F_OLD),
    _line(CIK_NO, "VERIFY EXCLUDED ISSUER", "424B2", date(2025, 3, 14), F_NO),
    _line(JPM_PARENT, "JPMORGAN CHASE & CO", "424B2", date(2025, 3, 14), F_COLISTED),
    _line(JPM_SUB, "JPMorgan Chase Financial Co. LLC", "424B2", date(2025, 3, 14), F_COLISTED),
    _line(JPM_PARENT, "JPMORGAN CHASE & CO", "424B2", date(2025, 3, 14), F_PROMOTE),
    _line(CIK_PROMOTE, "VERIFY PROMOTED FILER", "424B2", date(2025, 3, 14), F_PROMOTE),
    # Nonexistent on EDGAR, and the NEWEST row in the fetch set, so it is
    # attempted FIRST and the run must carry on to the real filings after it.
    _line(JPM_SUB, "JPMorgan Chase Financial Co. LLC", "424B2", date(2026, 9, 30), F_BAD),
]


async def teardown_fixtures(conn) -> None:
    from services import edgar_pipeline as p

    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM audit_log WHERE org_id = $1 OR user_id = ANY($2::uuid[])",
                           ORG, FIXTURE_USERS)
        await conn.execute("DELETE FROM assistant_activities WHERE org_id = $1 OR user_id = ANY($2::uuid[])",
                           ORG, FIXTURE_USERS)
        await conn.execute("DELETE FROM agent_proposals WHERE org_id = $1 OR proposed_by = ANY($2::uuid[])",
                           ORG, FIXTURE_USERS)
        await conn.execute(
            "DELETE FROM user_roles WHERE user_id = ANY($1::uuid[]) "
            "OR role_id IN (SELECT id FROM roles WHERE org_id = $2)", FIXTURE_USERS, ORG)
        await conn.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE org_id = $1)", ORG)
        await conn.execute("DELETE FROM roles WHERE org_id = $1", ORG)
        await conn.execute(
            "DELETE FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[])",
            FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS])
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)

        await conn.execute("DELETE FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                           ACC_PREFIX + "%")
        await conn.execute("DELETE FROM portfolio.edgar_index_filings WHERE accession_number LIKE $1",
                           ACC_PREFIX + "%")
        await conn.execute("DELETE FROM portfolio.structured_note_issuers WHERE filer_cik = ANY($1::text[])",
                           FIXTURE_CIKS)
        await conn.execute("DELETE FROM portfolio.edgar_pipeline_lease WHERE lease_name = $1", VERIFY_LEASE)
        if STATE["pipeline_runs"]:
            await conn.execute("DELETE FROM portfolio.edgar_pipeline_runs WHERE id = ANY($1::uuid[])",
                               list(STATE["pipeline_runs"]))
        await conn.execute("DELETE FROM portfolio.edgar_pipeline_runs WHERE trigger_source = 'verify'")
        if STATE["workflow_runs"]:
            wr = list(STATE["workflow_runs"])
            await conn.execute("DELETE FROM portfolio.edgar_pipeline_runs WHERE workflow_run_id = ANY($1::uuid[])", wr)
            await conn.execute(
                "DELETE FROM member_todos WHERE related_type = 'workflow_run' AND related_id = ANY($1::uuid[])", wr)
            await conn.execute("DELETE FROM workflow_run_steps WHERE workflow_run_id = ANY($1::uuid[])", wr)
            await conn.execute("DELETE FROM workflow_runs WHERE id = ANY($1::uuid[])", wr)
    _ = p  # imported for its side effect of failing loud if the module is broken


async def seed(conn) -> None:
    from services import edgar_index as ix
    from services.rbac import ensure_role, grant_org_admin

    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "EDGARPIPELINEA Fixture Org", f"{ORG}-edgarpipelinea")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member"), (U_MEMBER, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""",
                uid, ORG, f"{sub}@test.local", sub, sub, role)
        member_role = await ensure_role(conn, ORG, "member", "edgarpipelinea fixture: no permissions")
        for uid in (U_SUPER, U_MEMBER):
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                               uid, member_role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)
        # An issuer whose include_status is 'no' (no real issuer has it today).
        await conn.execute(
            """INSERT INTO portfolio.structured_note_issuers
                   (filer_cik, issuer_group, filer_name, filer_role, credit_entity, include_status, notes)
               VALUES ($1, 'VERIFY Excluded', 'VERIFY EXCLUDED ISSUER', 'issuer', 'Verify Excluded Co',
                       'no', 'edgarpipelinea fixture')""", CIK_NO)
    parsed = ix.parse_index_lines(FIXTURE_LINES)
    await ix.load_records(conn, parsed.filing_records(None), parsed.filer_records())


# ═══════════════════════════════════════════════════════════════════════════
# Sections
# ═══════════════════════════════════════════════════════════════════════════
async def task1_findings(conn) -> None:
    section("[Y] Task 1 findings")
    reported = []

    # 1a — live DDL is generated from the catalog (the migration check is its own section).
    n = await rls_val(conn, """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                               WHERE n.nspname = 'portfolio' AND c.relname = ANY($1::text[])""",
                      ["edgar_index_filings", "edgar_index_filing_filers", "structured_note_issuers",
                       "v_edgar_filings_explorer"])
    check(n == 4, "1a. the four hand-made objects exist and their DDL is readable from pg_get_*", f"found={n}")
    find("1a. live DDL", "generated with pg_get_constraintdef/indexdef/viewdef + pg_policies + relacl and "
         "recorded verbatim in migrations/edgarindex_discovery_tables.sql (status_reason already existed)")
    reported.append("1a")

    # 1b — edgar_fetch signatures, and the pipeline REUSES the extractor + prefilter.
    from services import edgar_fetch as ef
    sigs = {name: str(inspect.signature(getattr(ef, name))) for name in
            ("fetch_index", "fetch_filing", "store_filing", "resolve_filing_documents",
             "extract_filing_text", "passes_prefilter", "prefilter_hits")}
    src = (API_DIR / "services" / "edgar_pipeline.py").read_text()
    reuse = ("edgar_fetch.extract_filing_text(raw)" in src and "edgar_fetch.passes_prefilter(text)" in src
             and "edgar_fetch.resolve_filing_documents(meta, client)" in src
             and "edgar_fetch.fetch_filing(meta, client)" in src
             and "HTMLParser" not in src)
    check(reuse, "1b. the pipeline calls the EXISTING extractor, prefilter and fetch functions "
                 "(and defines no extractor of its own)")
    find("1b. edgar_fetch signatures", f"{sigs}")
    reported.append("1b")

    # 1c — Render credential (name only) and the base service.
    has_key = bool((os.environ.get("RENDER_API_KEY") or "").strip())
    if has_key:
        import httpx
        from services import edgar_pipeline as p
        async with httpx.AsyncClient(timeout=30) as http:
            try:
                sid = await p.render_service_id(http, os.environ["RENDER_API_KEY"].strip())
            except Exception as exc:  # noqa: BLE001
                sid = None
                find("1c. base service lookup failed", f"{type(exc).__name__}: {exc}")
        check(bool(sid), "1c. RENDER_API_KEY exists in Doppler and resolves the base service for one-off jobs",
              f"service={sid}")
        find("1c. Render", f"RENDER_API_KEY present (name only); base service {sid} "
             f"({p.RENDER_SERVICE_NAME_DEFAULT}: rootDir apps/api, starter plan, Doppler-synced — the API "
             "web service is on the free plan)")
    else:
        blocked("1c. no RENDER_API_KEY in Doppler", "the job launch is BLOCKED")
    reported.append("1c")

    # 1d — DataGrid server-side mode, existing callers untouched.
    dg = (WEB_DIR / "components/ui/DataGrid.jsx").read_text()
    callers = [f for f in (WEB_DIR / "components").rglob("*.jsx")
               if "serverSide=" in f.read_text() and f.name != "DataGrid.jsx"]
    check("serverSide," in dg and "manualPagination: true" in dg and "manualSorting: true" in dg
          and [c.name for c in callers] == ["EdgarPipelineMonitor.jsx"],
          "1d. DataGrid gained an opt-in serverSide mode; only the EDGAR monitor passes it",
          f"callers={[c.name for c in callers]}")
    find("1d. DataGrid", "it was client-side only (TanStack sort/filter/paginate over rowData). Extended "
         "with `serverSide` (manual sorting/paging, text filters hidden, footer counts totalRows); "
         "undefined by default, so existing callers are unchanged")
    reported.append("1d")

    # 1e — nothing references the old VERIFY FIXTURE row.
    refs = await rls_val(conn, """SELECT count(*) FROM portfolio.securities_global_note_terms t
                                  LEFT JOIN portfolio.reference_filings r ON r.id = t.reference_filing_id
                                  WHERE t.reference_filing_id IS NOT NULL AND r.id IS NULL""")
    check(refs == 0, "1e. no note-terms row points at a missing reference_filings row (the fixture "
                     "was unreferenced before its delete)", f"dangling={refs}")
    find("1e. VERIFY FIXTURE", "0 note-terms references, no manifest row; code only creates it "
         "(verify_edgarcorpus) or filters it (run_note_terms_extraction) — deleted, R2 had no objects")
    reported.append("1e")

    check(len(reported) == 5, "Task 1's five findings reported", f"{reported}")


def _norm_type(t: str) -> str:
    return t.replace("timestamptz", "timestamp with time zone").strip().lower()


async def migration_matches(conn) -> None:
    section("[Y] The migration files' definitions match the live objects")
    text = "\n".join(p.read_text() for p in MIGRATIONS)
    flat = re.sub(r"\s+", " ", text).lower()
    tables = ["edgar_index_filings", "edgar_index_filing_filers", "structured_note_issuers",
              "edgar_selection_policies", "edgar_pipeline_runs", "edgar_pipeline_lease"]
    missing_cols = []
    for t in tables:
        cols = await rls_fetch(conn, """SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS typ
                                        FROM pg_attribute a WHERE a.attrelid = ('portfolio.' || $1)::regclass
                                          AND a.attnum > 0 AND NOT a.attisdropped""", t)
        for c in cols:
            pat = rf"\b{re.escape(c['attname'])} {re.escape(_norm_type(c['typ']))}(?![\w\[])"
            if not re.search(pat, _norm_type(flat)):
                missing_cols.append(f"{t}.{c['attname']} {c['typ']}")
    rf_new = ["content_encoding", "compressed_byte_size", "text_r2_key", "text_byte_size",
              "text_compressed_byte_size"]
    for c in rf_new:
        if f"add column {c} " not in flat:
            missing_cols.append(f"reference_filings.{c}")
    check(not missing_cols, "every live column (name + type) on the 6 tables is declared in the migrations",
          f"missing={missing_cols}")

    names = await rls_fetch(conn, """
        SELECT conname AS n FROM pg_constraint WHERE conrelid IN (
            SELECT c.oid FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
            WHERE s.nspname = 'portfolio' AND c.relname = ANY($1::text[])) AND contype IN ('c','f','u')
        UNION ALL SELECT indexrelname FROM pg_stat_all_indexes WHERE schemaname = 'portfolio'
            AND relname = ANY($1::text[]) AND indexrelname NOT LIKE '%_pkey'
        UNION ALL SELECT policyname FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = ANY($1::text[])
        UNION ALL SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (
            SELECT c.oid FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
            WHERE s.nspname = 'portfolio' AND c.relname = ANY($1::text[]))""", tables)
    # Unnamed inline CHECKs get Postgres's own name (<table>_<column>_check);
    # those are proven by the column check above plus the definitions below.
    undeclared = sorted({r["n"] for r in names
                         if r["n"].lower() not in flat and not r["n"].endswith("_check")})
    check(not undeclared and len(names) > 40,
          "every live constraint, index, policy and trigger on those tables is named in the migrations",
          f"checked={len(names)} undeclared={undeclared}")

    chk = await rls_val(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                              "WHERE conname = 'edgar_index_filings_status_chk'")
    live_set = set(re.findall(r"'([a-z_]+)'::text", chk or ""))
    m = re.search(r"edgar_index_filings_status_chk check \(pipeline_status = any \(array\[(.*?)\]", flat)
    file_set = set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()
    check(live_set == file_set == set(ALL_STATUSES),
          "the lifecycle CHECK's value set is identical live, in the file, and in the prompt",
          f"live={sorted(live_set)}")

    view_cols = [r["attname"] for r in await rls_fetch(conn, """
        SELECT attname FROM pg_attribute WHERE attrelid = 'portfolio.v_edgar_filings_explorer'::regclass
          AND attnum > 0 ORDER BY attnum""")]
    view_sql = flat.split("create or replace view portfolio.v_edgar_filings_explorer")[-1]
    opts = await rls_val(conn, "SELECT reloptions::text FROM pg_class WHERE oid = 'portfolio.v_edgar_filings_explorer'::regclass")
    check(all(c in view_sql for c in view_cols) and "security_invoker=true" in (opts or ""),
          "the view's live columns are all in its recorded definition, and it is security_invoker",
          f"cols={view_cols}")
    fns = await rls_fetch(conn, """SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                                   WHERE n.nspname = 'portfolio' AND p.proname LIKE ANY(ARRAY['edgar%','structured_note%'])""")
    check(len(fns) >= 4 and all(f"function portfolio.{r['proname']}" in flat for r in fns),
          "every portfolio edgar function is created in the migrations", f"{[r['proname'] for r in fns]}")


async def lifecycle_check(conn) -> None:
    section("[Y] The lifecycle CHECK accepts every listed status and refuses an unknown one")
    accepted = []
    tr = conn.transaction()
    await tr.start()
    try:
        await _super(conn)
        for s in ALL_STATUSES:
            sp = conn.transaction()
            await sp.start()
            try:
                await conn.execute(
                    """UPDATE portfolio.edgar_index_filings SET pipeline_status = $2,
                              selection_policy_version = 1 WHERE accession_number = $1""", F_YES, s)
                accepted.append(s)
            except asyncpg.PostgresError:
                pass
            finally:
                await sp.rollback()
        refused = False
        sp = conn.transaction()
        await sp.start()
        try:
            await conn.execute("UPDATE portfolio.edgar_index_filings SET pipeline_status = 'bogus_status' "
                               "WHERE accession_number = $1", F_YES)
        except asyncpg.CheckViolationError:
            refused = True
        finally:
            await sp.rollback()
    finally:
        await tr.rollback()
    check(accepted == ALL_STATUSES, f"all {len(ALL_STATUSES)} lifecycle statuses accepted", f"{accepted}")
    check(refused, "an unknown status ('bogus_status') is refused by the CHECK")


async def primary_issuer(conn) -> None:
    section("[Y] primary_issuer_cik — co-listed resolves to the subsidiary; issuer-group filter < 1s")
    from services import edgar_pipeline_admin as adm

    fx = await rls_val(conn, "SELECT primary_issuer_cik FROM portfolio.edgar_index_filings WHERE accession_number = $1",
                       F_COLISTED)
    check(fx == JPM_SUB, "fixture co-listed JPM parent (guarantor) + JPMorgan Chase Financial (issuer) -> the subsidiary",
          f"primary={fx}")
    real = await rls_row(conn, """
        SELECT f.accession_number, f.primary_issuer_cik FROM portfolio.edgar_index_filings f
        WHERE f.accession_number NOT LIKE $1
          AND EXISTS (SELECT 1 FROM portfolio.edgar_index_filing_filers a WHERE a.accession_number = f.accession_number AND a.cik = $2)
          AND EXISTS (SELECT 1 FROM portfolio.edgar_index_filing_filers b WHERE b.accession_number = f.accession_number AND b.cik = $3)
        ORDER BY f.filing_date DESC LIMIT 1""", ACC_PREFIX + "%", JPM_PARENT, JPM_SUB)
    check(real is not None and real["primary_issuer_cik"] == JPM_SUB,
          "a REAL co-listed JPM parent/subsidiary filing resolves to the subsidiary",
          f"{dict(real) if real else None}")

    # The stored value agrees with the function, recomputed independently, on a full quarter.
    mism = await rls_val(conn, """SELECT count(*) FROM portfolio.edgar_index_filings f
                                  WHERE f.index_quarter = '2025Q1'
                                    AND f.primary_issuer_cik IS DISTINCT FROM portfolio.edgar_primary_issuer_cik(f.accession_number)""")
    check(mism == 0, "stored primary_issuer_cik equals the rule recomputed per row over all of 2025Q1",
          f"mismatches={mism}")

    # Time the database's OWN execution of the exact statements list_filings
    # issues (EXPLAIN ANALYZE "Execution Time"), never this machine's wall
    # clock, which is dominated by round trips to Supabase.
    xconn = _ExplainingConn(conn)
    async with conn.transaction():
        await _super(conn)
        res = await adm.list_filings(xconn, filters={"issuer_group": "JPMorgan"}, page=1, page_size=50)
    db_ms = sum(ms for _sql, ms in xconn.timings)
    direct = await rls_val(conn, """SELECT count(*) FROM portfolio.edgar_index_filings f
                                    WHERE EXISTS (SELECT 1 FROM portfolio.structured_note_issuers i
                                                  WHERE i.filer_cik = f.primary_issuer_cik AND i.issuer_group = 'JPMorgan')""")
    check(len(xconn.timings) >= 2 and db_ms < 1000.0 and res["total"] == direct and len(res["rows"]) == 50,
          "filtering the FULL manifest by issuer group (count + first page): database execution under one second",
          f"execution={db_ms:.1f} ms over {len(xconn.timings)} statements "
          f"{[round(ms, 1) for _s, ms in xconn.timings]} total={res['total']} direct={direct}")


async def selection(conn) -> None:
    section("[Y] Policy v1 selects exactly the expected set; version recorded; stored policy immutable")
    from services import edgar_pipeline as p

    pol = await p.active_policy(conn)
    check(pol["version"] == 1 and pol["rules"].get("form_types") == ["424B2"]
          and pol["rules"].get("filing_date_from") == "2019-01-01"
          and sorted(pol["rules"].get("issuer_include_status", [])) == ["review", "yes"],
          "the active policy is v1 with the decided rules (read from the table)", f"{pol['rules']}")
    sample = [F_YES, F_REVIEW, F_UNLISTED, F_FWP, F_OLD, F_NO, F_COLISTED, F_PROMOTE]
    counts = await p.select_stage(conn, accessions=sample)
    rows = {r["accession_number"]: dict(r) for r in await rls_fetch(conn, """
        SELECT accession_number, pipeline_status, selection_policy_version, status_reason
        FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])""", sample)}
    # F_PROMOTE lists JPM parent (guarantor role, include 'yes') and an unlisted
    # filer: "any listed issuer" makes the guarantor its primary, so it is selected.
    expected_sel = {F_YES, F_REVIEW, F_COLISTED, F_PROMOTE}
    expected_not = {F_UNLISTED, F_FWP, F_OLD, F_NO}
    got_sel = {a for a, r in rows.items() if r["pipeline_status"] == "selected"}
    got_not = {a for a, r in rows.items() if r["pipeline_status"] == "not_selected"}
    check(got_sel == expected_sel,
          "selected = issuer 'yes', issuer 'review', co-listed, guarantor-only (and nothing else)",
          f"selected={sorted(got_sel)}")
    check(got_not == expected_not, "not_selected = unlisted filer, FWP, filed 2018, issuer include 'no'",
          f"not_selected={sorted(got_not)}")
    reasons = {a: rows[a]["status_reason"] for a in (F_UNLISTED, F_FWP, F_OLD, F_NO)}
    check("no listed issuer" in reasons[F_UNLISTED] and "form FWP" in reasons[F_FWP]
          and "filed before 2019-01-01" in reasons[F_OLD] and "include_status no" in reasons[F_NO],
          "each exclusion names the rule that excluded it", f"{reasons}")
    check(all(r["selection_policy_version"] == 1 for r in rows.values()),
          "every decided row records policy version 1")
    check(counts["selected"] + counts["not_selected"] == len(sample),
          "the stage's own counts add up to the sample", f"{counts}")

    refused = False
    tr = conn.transaction()
    await tr.start()
    try:
        await _super(conn)
        await conn.execute("UPDATE portfolio.edgar_selection_policies SET description = 'tampered' WHERE version = 1")
    except asyncpg.CheckViolationError:
        refused = True
    finally:
        await tr.rollback()
    check(refused, "an edit to stored policy v1 is refused (check_violation from the retire-only trigger)")
    retired = None
    tr = conn.transaction()
    await tr.start()
    try:
        await _super(conn)
        retired = await conn.fetchval("""UPDATE portfolio.edgar_selection_policies
                                         SET is_active = false, retired_at = now() WHERE version = 1
                                         RETURNING version""")
    except asyncpg.PostgresError as exc:
        retired = f"{type(exc).__name__}"
    finally:
        await tr.rollback()   # never really retire the live policy
    still = await rls_val(conn, "SELECT is_active FROM portfolio.edgar_selection_policies WHERE version = 1")
    check(retired == 1 and still is True, "retiring IS allowed (proven in a rolled-back transaction; v1 still active)",
          f"retired={retired} active_after={still}")


async def discovery(conn) -> None:
    section("[Y] Incremental discovery over one fixed past day is idempotent; co-listed collapse")
    from services import edgar_index as ix
    from services import edgar_pipeline as p

    lines = [_line(JPM_PARENT, "JPMORGAN CHASE & CO", "424B2", FIXED_DAY, F_SYN_A),
             _line(JPM_SUB, "JPMorgan Chase Financial Co. LLC", "424B2", FIXED_DAY, F_SYN_A),
             _line(BARCLAYS_PLC, "BARCLAYS PLC", "424B2", FIXED_DAY, F_SYN_B)]
    parsed = ix.parse_index_lines(lines)
    first = await ix.load_records(conn, parsed.filing_records(None), parsed.filer_records())
    second = await ix.load_records(conn, parsed.filing_records(None), parsed.filer_records())
    row = await rls_row(conn, """SELECT filer_count, primary_issuer_cik, index_quarter,
                                        (SELECT count(*) FROM portfolio.edgar_index_filing_filers WHERE accession_number = $1) AS filers
                                 FROM portfolio.edgar_index_filings WHERE accession_number = $1""", F_SYN_A)
    check(first == (2, 3) and second == (0, 0),
          "synthetic daily lines: first load inserts 2 filings + 3 filers, the second inserts nothing",
          f"first={first} second={second}")
    check(row is not None and row["filer_count"] == 2 and row["filers"] == 2 and row["primary_issuer_cik"] == JPM_SUB
          and row["index_quarter"] == "2025Q1",
          "the co-listed pair collapsed to ONE filing with both filers kept, primary issuer computed at load",
          f"{dict(row) if row else None}")

    from services import edgar_fetch as ef
    ef.set_rate_limit(p.JOB_RATE_LIMIT)
    log = p.RequestLog(keep=100)
    async with p.make_job_client(log) as client:
        body = await p.fetch_daily_index(FIXED_DAY, client)
        if body is None:
            check(False, f"EDGAR's daily index for {FIXED_DAY} was fetched")
            return
        real = ix.parse_index_lines(body.splitlines())
        accs = list(real.filings)
        present = set(r["accession_number"] for r in await rls_fetch(
            conn, "SELECT accession_number FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])", accs))
        missing = [a for a in accs if a not in present]
        STATE["real_inserted"] = missing   # teardown removes exactly these if inserted
        filers_before = {(r["accession_number"], r["cik"]) for r in await rls_fetch(
            conn, "SELECT accession_number, cik FROM portfolio.edgar_index_filing_filers "
                  "WHERE accession_number = ANY($1::text[])", accs)}
        s1 = await p.discover_day(conn, FIXED_DAY, client)
        s2 = await p.discover_day(conn, FIXED_DAY, client)
    filers_after = {(r["accession_number"], r["cik"]) for r in await rls_fetch(
        conn, "SELECT accession_number, cik FROM portfolio.edgar_index_filing_filers "
              "WHERE accession_number = ANY($1::text[])", accs)}
    STATE["extra_filers"] = [pair for pair in filers_after - filers_before if pair[0] not in set(missing)]
    if STATE["extra_filers"]:
        find("extra filers", f"{len(STATE['extra_filers'])} filer rows on existing filings came only from the "
             "daily index; removed in teardown")
    check(real.lines_matched > len(real.filings),
          f"{FIXED_DAY}: co-listed index lines collapse ({real.lines_matched} 424B2/FWP lines -> {len(real.filings)} filings)")
    check(s1.new_filings == len(missing) and s2.new_filings == 0 and s2.new_filers == 0,
          f"{FIXED_DAY}: the first run inserts only what was missing ({len(missing)}), the second inserts nothing",
          f"run1={s1.new_filings}/{s1.new_filers} run2={s2.new_filings}/{s2.new_filers}")
    dup = await rls_val(conn, """SELECT count(*) - count(DISTINCT accession_number) FROM portfolio.edgar_index_filings
                                 WHERE accession_number = ANY($1::text[])""", accs)
    n = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])", accs)
    check(dup == 0 and n == len(accs), "every accession of that day is in the manifest exactly once",
          f"day={len(accs)} in_db={n}")
    if missing:
        find("daily vs quarterly index", f"{len(missing)} filings on the {FIXED_DAY} daily index were not in the "
             "quarterly load; inserted here and removed in teardown")


def _max_per_second(times: list[float]) -> int:
    times = sorted(times)
    best, i = 0, 0
    for j in range(len(times)):
        while times[j] - times[i] >= 1.0:
            i += 1
        best = max(best, j - i + 1)
    return best


async def fetch_section(conn, bucket: str) -> None:
    section("[Y] Fetch on a fixed sample, the bad accession, the cap, rate + User-Agent, re-run uploads nothing")
    from services import edgar_pipeline as p

    # A filing co-listed under a parent and its finance subsidiary matches more
    # than one issuer row, so dedupe to one row per accession (lowest group)
    # BEFORE taking up to 2 per group; a group left with fewer is fine.
    # Every fixture accession is excluded: F_BAD sits under JPMorgan's CIK with
    # the newest filing date, so "newest 2 per group" would otherwise take it
    # as one of JPMorgan's real filings, and it would then be targeted twice.
    sample_rows = await rls_fetch(conn, """
        WITH m AS (
            SELECT DISTINCT ON (f.accession_number) f.accession_number, f.filing_date, i.issuer_group
            FROM portfolio.edgar_index_filings f
            JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
            WHERE f.form_type = '424B2' AND f.pipeline_status = 'discovered'
              AND i.include_status = 'yes' AND f.index_quarter = '2026Q3'
              AND i.issuer_group = ANY($1::text[])
              AND f.accession_number NOT LIKE $2 AND f.accession_number <> $3
              AND f.primary_issuer_cik <> ALL($4::text[])
              AND NOT EXISTS (SELECT 1 FROM portfolio.reference_filings r
                              WHERE r.accession_number = f.accession_number)
            ORDER BY f.accession_number, i.issuer_group),
        c AS (
            SELECT accession_number, issuer_group,
                   row_number() OVER (PARTITION BY issuer_group
                                      ORDER BY filing_date DESC, accession_number DESC) AS rn
            FROM m)
        SELECT accession_number, issuer_group FROM c WHERE rn <= 2 ORDER BY accession_number""",
        SAMPLE_GROUPS, ACC_PREFIX + "%", F_BAD, FIXTURE_CIKS)
    sampled = [r["accession_number"] for r in sample_rows]
    dupes = sorted({a for a in sampled if sampled.count(a) > 1})
    check(not dupes, "the sample contains no duplicate accession numbers",
          f"sampled={len(sampled)} distinct={len(set(sampled))} dupes={dupes}")
    sample = list(dict.fromkeys(sampled))   # distinct, order kept
    STATE["sample"] = sample
    snap = {r["accession_number"]: dict(r) for r in await rls_fetch(
        conn, "SELECT * FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])", sample)}
    STATE["sample_rows"] = snap
    groups = {r["issuer_group"] for r in sample_rows}
    # Every expected count below derives from the REAL filings in the sample.
    # F_BAD is not a real filing: it is selected before run 1 (so run 1's
    # select stage never counts it), it is never fetched, and it is never linked.
    n_real = len(set(sample))   # DISTINCT real accessions
    C = 3   # run 1's fetch cap: the bad accession (newest) + C-1 real filings
    fixtures_in_sample = sorted(a for a in sample if a == F_BAD or a.startswith(ACC_PREFIX))
    check(not fixtures_in_sample, "the real sample contains no fixture accession (the bad accession included)",
          f"fixtures_in_sample={fixtures_in_sample}")
    check(len(groups) >= 5 and n_real >= 8,
          f"a fixed sample of {n_real} real 424B2s (up to 2 per group, >= 8) across >= 5 issuer groups, plus "
          "the one deliberately bad accession", f"n_real={n_real} groups={sorted(groups)}")

    # The bad accession is a fixture row; selection decides it like any other.
    await p.select_stage(conn, accessions=[F_BAD])
    ua = os.environ.get("EDGAR_USER_AGENT", "").strip()
    entries: list = []
    per_run: list[tuple[int, int]] = []   # (sec_requests on the run row, requests observed)
    targets = sample + [F_BAD]
    check(targets.count(F_BAD) == 1 and len(targets) == len(set(targets)) == n_real + 1,
          "the select-stage target list holds the bad accession exactly once and no duplicates",
          f"bad_count={targets.count(F_BAD)} targets={len(targets)} distinct={len(set(targets))} n_real={n_real}")

    async def job(cap: int, stages: list[str]):
        rid = await p.create_run(conn, trigger_source="verify", fetch_cap=cap, stages=stages, status="launched")
        STATE["pipeline_runs"].add(rid)
        log = p.RequestLog(keep=100000)
        async with p.make_job_client(log) as client:
            row = await p.run_job(conn, rid, r2_prefix=TEST_PREFIX, lease_name=VERIFY_LEASE,
                                  accessions=targets, client=client, request_log=log)
        entries.extend(log.entries)
        per_run.append((row["sec_requests"], len(log.entries)))
        return row

    # DIAGNOSTIC (selected=9 of 10 in a full run only): state around run 1.
    async def _diag_rows(label: str) -> dict:
        rows = {r["accession_number"]: dict(r) for r in await rls_fetch(conn, """
            SELECT accession_number, pipeline_status, selection_policy_version, primary_issuer_cik, attempt_count
            FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])""", targets)}
        print(f"  [DIAG] {label}: {len(rows)} of {len(targets)} target rows present")
        for a in targets:
            r = rows.get(a)
            print(f"  [DIAG]   {a}: " + ("MISSING" if r is None else
                  f"status={r['pipeline_status']} policy_v={r['selection_policy_version']} "
                  f"primary_issuer_cik={r['primary_issuer_cik']} attempts={r['attempt_count']}"))
        return rows

    await _diag_rows("BEFORE run 1")
    runs_now = await rls_fetch(conn, """
        SELECT id, trigger_source, status, stages, fetch_cap, requested_at, started_at, finished_at, selected
        FROM portfolio.edgar_pipeline_runs
        WHERE trigger_source = 'verify' OR status IN ('launching', 'launched', 'running')
        ORDER BY requested_at""")
    total_runs = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    print(f"  [DIAG] pipeline runs BEFORE run 1: total={total_runs}; verify-or-live rows={len(runs_now)}")
    for r in runs_now:
        print(f"  [DIAG]   {dict(r)}")
    leases_now = await rls_fetch(conn, "SELECT * FROM portfolio.edgar_pipeline_lease ORDER BY lease_name")
    print(f"  [DIAG] lease rows BEFORE run 1: {len(leases_now)}")
    for r in leases_now:
        print(f"  [DIAG]   {dict(r)}")

    # select_stage returns only counts (its RETURNING has no accession), so the
    # accessions it decided are the target rows whose status changed across the call.
    real_select_stage = p.select_stage

    async def _diag_select_stage(c, **kw):
        ident = await c.fetchrow("SELECT current_setting('application_name') AS app, pg_backend_pid() AS pid")
        print(f"  [DIAG] select stage connection: application_name={ident['app']!r} backend_pid={ident['pid']} "
              f"(same object as the verify conn: {c is conn}); accessions arg={kw.get('accessions')}")
        pre = {r["accession_number"]: r["pipeline_status"] for r in await rls_fetch(
            c, "SELECT accession_number, pipeline_status FROM portfolio.edgar_index_filings "
               "WHERE accession_number = ANY($1::text[])", targets)}
        res = await real_select_stage(c, **kw)
        post = {r["accession_number"]: r["pipeline_status"] for r in await rls_fetch(
            c, "SELECT accession_number, pipeline_status FROM portfolio.edgar_index_filings "
               "WHERE accession_number = ANY($1::text[])", targets)}
        decided = [a for a in targets if pre.get(a) != post.get(a)]
        print(f"  [DIAG] select stage returned {res}")
        print(f"  [DIAG] select stage decided {len(decided)}: "
              + ", ".join(f"{a} {pre.get(a)}->{post.get(a)}" for a in decided))
        print(f"  [DIAG] targets NOT decided: "
              + ", ".join(f"{a} ({post.get(a)})" for a in targets if a not in decided))
        return res

    # Run 1: select + fetch, cap C. Bad accession is the newest -> attempted first.
    p.select_stage = _diag_select_stage
    try:
        r1 = await job(C, ["select", "fetch"])
    finally:
        p.select_stage = real_select_stage
    await _diag_rows("AFTER run 1")
    states1 ={r["accession_number"]: r["pipeline_status"] for r in await rls_fetch(
        conn, "SELECT accession_number, pipeline_status FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])",
        targets)}
    touched1 = [a for a, s in states1.items() if s not in ("selected",)]
    # Run 1's select stage decides only the n_real 'discovered' rows; of its C
    # attempts the bad accession is one, so exactly C-1 real filings are fetched.
    remaining = n_real - (C - 1)
    check(r1["status"] == "succeeded" and r1["selected"] == n_real and r1["fetch_attempted"] == C,
          f"the fetch cap is honored: cap {C} -> exactly {C} filings attempted (select stage decided the {n_real} real ones)",
          f"n_real={n_real} status={r1['status']} selected={r1['selected']} attempted={r1['fetch_attempted']} "
          f"err={r1['error']}")
    check(len(touched1) == C and states1.get(F_BAD) == "fetch_failed"
          and sum(1 for a in sample if states1.get(a) == "selected") == remaining,
          f"exactly {C} rows left 'selected' (the bad accession, newest, went first, + {C - 1} real); "
          f"{remaining} real still 'selected'", f"n_real={n_real} {states1}")

    # Run 2: the rest of the REAL filings.
    r2 = await job(50, ["fetch"])
    check(r2["status"] == "succeeded" and r2["fetch_attempted"] == remaining and r2["fetched"] == remaining
          and r2["fetch_failed"] == 0,
          f"a second job fetches the remaining {remaining} real filings (the failed row's retry is not due yet)",
          f"n_real={n_real} attempted={r2['fetch_attempted']} fetched={r2['fetched']} failed={r2['fetch_failed']} "
          f"err={r2['error']}")

    bad = await rls_row(conn, """SELECT pipeline_status, status_reason, attempt_count, last_attempt_at, next_attempt_at
                                 FROM portfolio.edgar_index_filings WHERE accession_number = $1""", F_BAD)
    now = datetime.now(timezone.utc)
    check(bad["pipeline_status"] == "fetch_failed" and bool(bad["status_reason"]) and bad["attempt_count"] == 1
          and bad["last_attempt_at"] is not None and bad["next_attempt_at"] is not None and bad["next_attempt_at"] > now,
          "the bad accession is fetch_failed with a reason, attempt_count 1 and a future retry time",
          f"n_real={n_real} {dict(bad)}")
    check(r1["fetch_failed"] == 1 and r1["fetched"] == C - 1,
          f"and the run that hit it still finished the other {C - 1} filings in its cap",
          f"n_real={n_real} run1 fetched={r1['fetched']} failed={r1['fetch_failed']}")

    # Per-filing proof against R2, read independently.
    rows = await rls_fetch(conn, """
        SELECT f.accession_number, f.pipeline_status, f.reference_filing_id, f.fetched_at, f.document_kind,
               f.detected_cusip, r.r2_key, r.text_r2_key, r.content_hash, r.byte_size, r.compressed_byte_size,
               r.text_byte_size, r.text_compressed_byte_size, r.content_encoding, r.extracted_text IS NULL AS no_text,
               r.extraction_status
        FROM portfolio.edgar_index_filings f JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
        WHERE f.accession_number = ANY($1::text[])""", sample)
    check(len(rows) == n_real, f"all {n_real} real sample filings are linked to a reference_filings row",
          f"n_real={n_real} linked={len(rows)}")
    bad_linked = await rls_val(conn, "SELECT reference_filing_id FROM portfolio.edgar_index_filings "
                                     "WHERE accession_number = $1", F_BAD)
    check(bad_linked is None, "the bad accession is NOT linked to any reference_filings row",
          f"n_real={n_real} {bad_linked}")
    problems = []
    lastmod = {}
    for r in rows:
        try:
            raw_obj = await asyncio.to_thread(r2_get, bucket, r["r2_key"])
            txt_obj = await asyncio.to_thread(r2_get, bucket, r["text_r2_key"])
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{r['accession_number']}: R2 read {type(exc).__name__}")
            continue
        raw = gzip.decompress(raw_obj) if raw_obj[:2] == b"\x1f\x8b" else None
        txt = gzip.decompress(txt_obj) if txt_obj[:2] == b"\x1f\x8b" else None
        if not r["r2_key"].startswith(TEST_PREFIX + "/") or raw is None or txt is None:
            problems.append(f"{r['accession_number']}: not gzipped or wrong prefix")
            continue
        if hashlib.sha256(raw).hexdigest() != r["content_hash"]:
            problems.append(f"{r['accession_number']}: sha256 mismatch")
        if (r["byte_size"], r["compressed_byte_size"], r["text_byte_size"], r["text_compressed_byte_size"]) != \
                (len(raw), len(raw_obj), len(txt), len(txt_obj)):
            problems.append(f"{r['accession_number']}: sizes differ")
        if not (r["content_encoding"] == "gzip" and r["no_text"] and r["extraction_status"] == "fetched"):
            problems.append(f"{r['accession_number']}: row flags")
        if r["pipeline_status"] not in POST_FETCH or r["fetched_at"] is None or r["document_kind"] is None:
            problems.append(f"{r['accession_number']}: status {r['pipeline_status']}")
    check(not problems, "each R2 object exists and is gzipped; sha256(decompressed) = recorded hash; "
                        "raw/compressed/text sizes match; extracted_text NULL; status past 'fetched'",
          f"n_real={n_real} {problems}")
    kinds = sorted({(r["document_kind"], r["pipeline_status"]) for r in rows})
    find("sample outcomes", f"(kind, status) = {kinds}; CUSIPs detected on "
         f"{sum(1 for r in rows if r['detected_cusip'])}/{n_real}; bytes uploaded run1+run2 = "
         f"{r1['bytes_uploaded'] + r2['bytes_uploaded']}")
    for r in rows:
        head = await asyncio.to_thread(lambda k=r["r2_key"]: _s3().head_object(Bucket=bucket, Key=k))
        lastmod[r["r2_key"]] = head["LastModified"]

    # Re-run: put the sample back to 'selected' and fetch again — nothing re-uploads.
    await rls_exec(conn, """UPDATE portfolio.edgar_index_filings SET pipeline_status = 'selected'
                            WHERE accession_number = ANY($1::text[])""", sample)
    r3 = await job(50, ["fetch"])
    after = {}
    for k in lastmod:
        head = await asyncio.to_thread(lambda k=k: _s3().head_object(Bucket=bucket, Key=k))
        after[k] = head["LastModified"]
    skipped = ((r3.get("details") or {}) if isinstance(r3.get("details"), dict)
               else json.loads(r3["details"] or "{}")).get("fetch", {}).get("uploads_skipped")
    check(r3["fetched"] == n_real and r3["bytes_uploaded"] == 0 and skipped == n_real and after == lastmod
          and len(lastmod) == n_real,
          f"a second fetch of the same {n_real} re-uploads nothing ({n_real} skipped, 0 bytes, "
          "R2 LastModified unchanged)",
          f"n_real={n_real} fetched={r3['fetched']} bytes={r3['bytes_uploaded']} skipped={skipped} "
          f"lastmod_unchanged={after == lastmod} objects={len(lastmod)}")

    # Rate + User-Agent across every SEC request of the three jobs.
    times = [t for t, _ua, _u in entries]
    peak = _max_per_second(times)
    uas = {u for _t, u, _url in entries}
    check(len(times) >= 30 and peak <= 8,
          "measured SEC request rate never exceeded 8 in any 1-second window",
          f"requests={len(times)} peak={peak}/s")
    check(bool(ua) and uas == {ua} and all("sec.gov" in u for _t, _ua, u in entries),
          "every SEC request carried the declared User-Agent (EDGAR_USER_AGENT)", f"distinct={len(uas)}")
    check(all(a == b for a, b in per_run),
          "each run row's sec_requests equals the requests actually observed for that job", f"{per_run}")


async def classification() -> None:
    section("[Y] Classification rules; a preliminary pricing supplement is never ready; CUSIP rule")
    from services import edgar_pipeline as p

    payoff = " barrier buffer autocall contingent coupon participation rate underlying "
    cases = {
        "pricing_supplement": "Filed pursuant to Rule 424(b)(2) Pricing Supplement No. 1,234 dated May 1, 2026 "
                              "(To Product Supplement No. 4-I)",
        "preliminary_pricing_supplement": "Subject to Completion. Preliminary Pricing Supplement dated May 1, 2026",
        "product_supplement": "Product Supplement No. 4-I To prospectus dated April 13, 2023 ... the applicable pricing supplement",
        "underlying_supplement": "Underlying Supplement No. 1-I dated April 13, 2023",
        "term_sheet": "Term sheet To prospectus dated April 13, 2023",
        "other": "Prospectus Supplement to Prospectus dated April 13, 2023 Senior Medium-Term Notes",
    }
    got = {k: p.classify_document(v + payoff)[0] for k, v in cases.items()}
    check(got == {k: k for k in cases}, "each document_kind classified from its opening text", f"{got}")
    st_pre, _, _ = p.decide_status(cases["preliminary_pricing_supplement"] + payoff * 5)
    st_pre2, _, _ = p.decide_status("Preliminary Terms No. 123 Pricing Supplement" + payoff)
    st_ok, _, _ = p.decide_status(cases["pricing_supplement"] + payoff)
    st_skip, _, _ = p.decide_status(cases["pricing_supplement"] + " fixed rate senior notes")
    st_prod, _, _ = p.decide_status(cases["product_supplement"] + payoff)
    check(st_pre != "ready_for_extraction" and st_pre2 != "ready_for_extraction",
          "a preliminary pricing supplement full of payoff keywords is NEVER ready_for_extraction",
          f"{st_pre}, {st_pre2}")
    check((st_ok, st_skip, st_prod) == ("ready_for_extraction", "prefilter_skipped", "not_pricing_supplement"),
          "final pricing supplement -> ready (prefilter passes) / prefilter_skipped; product supplement -> not_pricing_supplement",
          f"{st_ok}, {st_skip}, {st_prod}")
    check(p.is_valid_cusip("037833100") is True and p.is_valid_cusip("17330FAB7") is True,
          "a valid CUSIP is accepted (check digit validates)")
    check(p.is_valid_cusip("037833101") is False, "a CUSIP with a wrong check digit is rejected")
    det = p.detect_cusip("CUSIP: 037833101 (typo) ... CUSIP / ISIN: 037833100 / US0378331005")
    det_none = p.detect_cusip("CUSIP No. 037833101")
    check(det == "037833100" and det_none is None,
          "detection skips the bad-check-digit token and takes the valid one; a lone bad one yields nothing",
          f"{det}, {det_none}")


async def lease_section(conn) -> None:
    section("[Y] The lease: a second job is refused; an expired lease can be taken over")
    from services import edgar_pipeline as p

    a = await p.acquire_lease(conn, "verify-holder-A", 600, VERIFY_LEASE)
    b = await p.acquire_lease(conn, "verify-holder-B", 600, VERIFY_LEASE)
    check(a is True and b is False, "holder A takes the lease; holder B is refused while it is live", f"A={a} B={b}")
    rid = await p.create_run(conn, trigger_source="verify", fetch_cap=0, stages=[], status="launched")
    STATE["pipeline_runs"].add(rid)
    row = await p.run_job(conn, rid, lease_name=VERIFY_LEASE, r2_prefix=TEST_PREFIX)
    check(row["status"] == "refused" and "lease" in (row["stop_reason"] or ""),
          "a JOB started while the lease is held is refused and says why", f"{row['status']}: {row['stop_reason']}")
    await rls_exec(conn, "UPDATE portfolio.edgar_pipeline_lease SET expires_at = now() - interval '1 second' "
                         "WHERE lease_name = $1", VERIFY_LEASE)
    b2 = await p.acquire_lease(conn, "verify-holder-B", 600, VERIFY_LEASE)
    holder = await rls_val(conn, "SELECT holder FROM portfolio.edgar_pipeline_lease WHERE lease_name = $1", VERIFY_LEASE)
    check(b2 is True and holder == "verify-holder-B", "after expiry, B takes the lease over", f"holder={holder}")
    await p.release_lease(conn, "verify-holder-B", VERIFY_LEASE)
    src = (API_DIR / "services" / "edgar_pipeline.py").read_text()
    check(not re.search(r"pg_(try_)?advisory_lock\(", src), "no session-level advisory lock is used for the job")


async def nightly_workflow(conn) -> None:
    section("[Y] The nightly workflow: no human step; its run completes immediately; job recorded")
    from services import edgar_pipeline as p, workflow_engine
    from services.action_registry import REGISTRY
    from services.database import get_pool, reset_rls_context, set_rls_context

    d = await rls_row(conn, """SELECT d.id, v.id AS ver, v.bpmn_xml FROM workflow_definitions d
                               JOIN workflow_versions v ON v.workflow_definition_id = d.id AND v.is_current
                               WHERE d.org_id = $1 AND d.name = 'EDGAR pipeline — nightly'""", PLATFORM_ORG)
    if not check(d is not None, "the nightly workflow definition exists in the platform org"):
        return
    xml = d["bpmn_xml"]
    check(not re.search(r"<bpmn:(userTask|manualTask|receiveTask|intermediateCatchEvent)\b", xml)
          and "<bpmn:serviceTask" in xml,
          "its BPMN has a Service Task and NO user/manual/receive task or waiting catch event")
    steps = await rls_fetch(conn, "SELECT step_key, step_type, autonomy_tier, action_registry_key FROM workflow_steps "
                                  "WHERE workflow_version_id = $1", d["ver"])
    eff = [workflow_engine.compute_effective_tier(dict(s), REGISTRY.get(s["action_registry_key"])) for s in steps]
    check(len(steps) == 1 and steps[0]["step_type"] == "service"
          and steps[0]["action_registry_key"] == "edgar.launch_pipeline_job"
          and all(e != workflow_engine.SUSPEND_TIER for e in eff)
          and getattr(REGISTRY.get("edgar.launch_pipeline_job"), "workflow_invocable", False) is True,
          "its one step is an automated, invocable Service Task whose effective tier does NOT suspend for approval",
          f"effective={eff}")
    trig = await rls_row(conn, """SELECT id, schedule_cron, timezone, is_active, created_by FROM workflow_triggers
                                  WHERE workflow_definition_id = $1 AND trigger_type = 'scheduled'""", d["id"])
    check(trig is not None and bool(trig["schedule_cron"]), "it has a scheduled trigger",
          f"{dict(trig) if trig else None}")
    if trig is not None and not trig["is_active"]:
        find("trigger inactive", "created inactive until this branch is deployed (the deployed build does not "
             "register the action) — activate it on /admin/workflows/triggers")

    if not (os.environ.get("RENDER_API_KEY") or "").strip():
        blocked("nightly job launch", "no RENDER_API_KEY — launch recorded as launch_failed, not proven")
        return
    pool = await get_pool()
    tokens = set_rls_context(PLATFORM_ORG, False)
    t0 = time.monotonic()
    try:
        result = await workflow_engine.start_workflow_run(
            pool, d["ver"], PLATFORM_ORG, {"edgar_pipeline": {"stages": [], "fetch_cap": 0}},
            trig["created_by"] if trig else None)
    finally:
        reset_rls_context(tokens)
    elapsed = time.monotonic() - t0
    STATE["workflow_runs"].add(result["run_id"])
    run = await rls_row(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE workflow_run_id = $1", result["run_id"])
    if run:
        STATE["pipeline_runs"].add(run["id"])
    check(result["status"] == "completed" and elapsed < 30,
          "the workflow run COMPLETES at once — it does not wait for the job", f"status={result['status']} {elapsed:.1f}s")
    check(run is not None and run["status"] == "launched" and bool(run["render_job_id"]),
          "the job is recorded in edgar_pipeline_runs: launched, with its Render job id",
          f"{run['status'] if run else None} job={run['render_job_id'] if run else None} err={run['error'] if run else None}")
    if run is not None and run["render_job_id"]:
        STATE["render_job"] = (run["render_service_id"], run["render_job_id"])
        status = {}
        for _ in range(24):   # up to ~4 minutes, so teardown does not race the job
            status = await p.render_job_status(run["render_service_id"], run["render_job_id"])
            if status.get("finishedAt") or status.get("status") in ("succeeded", "failed", "canceled"):
                break
            await asyncio.sleep(10)
        after = await rls_row(conn, "SELECT status, stop_reason, error FROM portfolio.edgar_pipeline_runs WHERE id = $1",
                              run["id"])
        find("Render job outcome", f"render status={status.get('status')} finishedAt={status.get('finishedAt')}; "
             f"run row now {dict(after) if after else None}. On a build without edgar_pipeline_job.py (this branch "
             "not deployed) the job cannot start — that is a deployment fact, not a launch failure")


async def existing_documents(conn) -> None:
    section("[Y] The existing documents are linked and marked fetched, with zero new downloads")
    r = await rls_row(conn, """
        SELECT (SELECT count(*) FROM portfolio.reference_filings WHERE content_encoding = 'identity') AS identity_rows,
               (SELECT count(*) FROM portfolio.reference_filings r WHERE r.content_encoding = 'identity'
                  AND EXISTS (SELECT 1 FROM portfolio.edgar_index_filings f
                              WHERE f.reference_filing_id = r.id AND f.pipeline_status = 'fetched')) AS linked,
               (SELECT count(*) FROM portfolio.reference_filings WHERE content_encoding = 'identity'
                  AND (extracted_text IS NULL OR r2_key LIKE '%.gz')) AS rewritten,
               (SELECT count(*) FROM portfolio.reference_filings WHERE filer_name = 'VERIFY FIXTURE') AS fixture""")
    check(r["identity_rows"] == 200 and r["linked"] == 200,
          "all 200 pre-existing documents (201 minus the deleted fixture) are linked to a manifest row marked fetched",
          f"{dict(r)}")
    check(r["rewritten"] == 0, "none was re-downloaded: still 'identity' encoded, raw HTML key, text in the database")
    check(r["fixture"] == 0, "the VERIFY FIXTURE row is gone")
    st = await rls_val(conn, """SELECT count(*) FROM portfolio.edgar_index_filings
                                WHERE status_reason LIKE 'pre-existing reference_filings document%'
                                  AND selection_policy_version IS NULL AND pipeline_status = 'fetched'""")
    check(st == 200, "their manifest rows say why they are fetched and carry no policy version (none decided them)",
          f"rows={st}")


async def api_section(conn) -> None:
    section("[Y] Monitoring API: super-admin 200; org admin and member 403 on the IDENTICAL request")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "the verify connection's role does NOT bypass RLS", f"rolbypassrls={bypass}")
    paths = ["/api/v1/admin/edgar/filings?issuer_group=JPMorgan&page=2&page_size=25",
             "/api/v1/admin/edgar/progress", "/api/v1/admin/edgar/issuers"]
    for path in paths:
        res = {name: await api(uid, "GET", path) for name, uid in
               (("super", U_SUPER), ("org_admin", U_ORGADMIN), ("member", U_MEMBER))}
        check(res["super"][0] == 200 and res["org_admin"][0] == 403 and res["member"][0] == 403,
              f"GET {path.split('?')[0]}: super 200 / org admin 403 / member 403",
              f"{[(k, v[0]) for k, v in res.items()]} {_detail(res['super'][1]) if res['super'][0] != 200 else ''}")
        if path.startswith("/api/v1/admin/edgar/filings"):
            env = res["super"][1]
            check(env.get("permissions", {}).get("is_super_admin") is True
                  and env.get("vocabularies", {}).get("editable") == []
                  and env.get("vocabularies", {}).get("inline_editable") == []
                  and "issuer_group" not in env.get("vocabularies", {}).get("sortable", ["issuer_group"]),
                  "the filings envelope: is_super_admin true, editable [] (read-only grid), sort keys published")
            check("rows" not in res["member"][1], "the 403 carries no rows")

    before = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    s, b = await api(U_ORGADMIN, "POST", "/api/v1/admin/edgar/runs", {"fetch_cap": 1})
    after = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    check(s == 403 and before == after, "Run now by an org admin: 403 and no run row created", f"HTTP {s}")
    s, b = await api(U_SUPER, "POST", "/api/v1/admin/edgar/runs", {"fetch_cap": 1, "org_id": str(ORG)})
    check(s == 422, "a body carrying org_id is refused (extra='forbid')", f"HTTP {s}")


async def paging_section(conn) -> None:
    section("[Y] Server-side paging: correct totals and pages; sorting and filtering on the full table")
    base = "/api/v1/admin/edgar/filings"
    s, all_ = await api(U_SUPER, "GET", f"{base}?page=1&page_size=50")
    total = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filings")
    check(s == 200 and all_["total"] == total, "unfiltered total equals count(*) of the whole manifest",
          f"api={all_.get('total')} sql={total}")

    p1 = (await api(U_SUPER, "GET", f"{base}?issuer_group=JPMorgan&sort=filing_date&direction=desc&page=1&page_size=50"))[1]
    p2 = (await api(U_SUPER, "GET", f"{base}?issuer_group=JPMorgan&sort=filing_date&direction=desc&page=2&page_size=50"))[1]
    direct = [r["accession_number"] for r in await rls_fetch(conn, """
        SELECT f.accession_number FROM portfolio.edgar_index_filings f
        WHERE f.primary_issuer_cik IN (SELECT filer_cik FROM portfolio.structured_note_issuers WHERE issuer_group = 'JPMorgan')
        ORDER BY f.filing_date DESC, f.accession_number DESC LIMIT 100""")]
    got = [r["accession_number"] for r in p1["rows"]] + [r["accession_number"] for r in p2["rows"]]
    check(got == direct and not (set(got[:50]) & set(got[50:])),
          "pages 1 and 2 are disjoint and together equal the same ordered query run directly in SQL")
    check(all(r["issuer_group"] == "JPMorgan" for r in p1["rows"] + p2["rows"]) and p1["total"] < total,
          "the issuer filter narrows: every row matches, and the total is a strict subset", f"{p1['total']} < {total}")
    s, empty = await api(U_SUPER, "GET", f"{base}?issuer_group=JPMorgan&page={p1['total'] // 50 + 2}&page_size=50")
    check(s == 200 and empty["rows"] == [] and empty["total"] == p1["total"], "a page past the end is empty, same total")

    q = (await api(U_SUPER, "GET", f"{base}?quarter=2025Q1&form_type=FWP&page=1&page_size=200"))[1]
    qn = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE index_quarter = '2025Q1' AND form_type = 'FWP'")
    check(q["total"] == qn and all(r["index_quarter"] == "2025Q1" and r["form_type"] == "FWP" for r in q["rows"]),
          "quarter + form filter: total matches SQL, every row matches (inclusion)", f"{q['total']} vs {qn}")
    other = await rls_val(conn, """SELECT count(*) FROM portfolio.edgar_index_filings
                                   WHERE NOT (index_quarter = '2025Q1' AND form_type = 'FWP')""")
    check(q["total"] + other == total, "and nothing outside the predicate leaked in (exclusion: the complement adds up)")

    st = (await api(U_SUPER, "GET", f"{base}?status=fetched&page=1&page_size=200"))[1]
    stn = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE pipeline_status = 'fetched'")
    check(st["total"] == stn and all(r["pipeline_status"] == "fetched" for r in st["rows"]),
          "status filter matches SQL", f"{st['total']} vs {stn}")
    dr = (await api(U_SUPER, "GET", f"{base}?date_from=2024-01-01&date_to=2024-01-31&issuer_group=UBS&page=1&page_size=200"))[1]
    check(all("2024-01-01" <= str(r["filing_date"])[:10] <= "2024-01-31" for r in dr["rows"]) and dr["total"] > 0,
          "date-range filter: every row inside the range", f"total={dr['total']}")

    srt = (await api(U_SUPER, "GET", f"{base}?sort=form_type&direction=asc&page=1&page_size=50"))[1]
    forms = [r["form_type"] for r in srt["rows"]]
    srt_d = (await api(U_SUPER, "GET", f"{base}?sort=form_type&direction=desc&page=1&page_size=50"))[1]
    check(forms == sorted(forms) and forms[0] == "424B2" and srt_d["rows"][0]["form_type"] == "FWP",
          "sort by form, both directions, on the full table", f"asc first={forms[0]} desc first={srt_d['rows'][0]['form_type']}")
    first = (await api(U_SUPER, "GET", f"{base}?sort=filing_date&direction=asc&page=1&page_size=1"))[1]
    mind = await rls_val(conn, "SELECT min(filing_date) FROM portfolio.edgar_index_filings")
    check(str(first["rows"][0]["filing_date"])[:10] == str(mind), "sort by date ascending starts at the oldest filing")
    bad_sort, _ = await api(U_SUPER, "GET", f"{base}?sort=issuer_name;drop&page=1")
    bad_status, _ = await api(U_SUPER, "GET", f"{base}?status=nope")
    check(bad_sort == 422 and bad_status == 422, "an unknown sort key or status is refused (422), never interpolated")


async def issuer_edit(conn) -> None:
    section("[Y] Issuer edit: super-admin persists + recomputes; org admin refused, row unchanged")
    pre = await rls_val(conn, "SELECT primary_issuer_cik FROM portfolio.edgar_index_filings WHERE accession_number = $1",
                        F_PROMOTE)
    check(pre == JPM_PARENT, "before: the PROMOTE filing's primary is JPM parent (its only listed issuer)", f"{pre}")

    body = {"filer_cik": CIK_PROMOTE, "issuer_group": "VERIFY Promoted", "filer_name": "VERIFY PROMOTED FILER",
            "filer_role": "issuer", "credit_entity": "Verify Promoted Co", "include_status": "review"}
    s, b = await api(U_ORGADMIN, "POST", "/api/v1/admin/edgar/issuers", body)
    n = await rls_val(conn, "SELECT count(*) FROM portfolio.structured_note_issuers WHERE filer_cik = $1", CIK_PROMOTE)
    check(s == 403 and n == 0, "org admin cannot add an issuer (403, no row)", f"HTTP {s}")
    s, b = await api(U_SUPER, "POST", "/api/v1/admin/edgar/issuers", body)
    post = await rls_val(conn, "SELECT primary_issuer_cik FROM portfolio.edgar_index_filings WHERE accession_number = $1",
                         F_PROMOTE)
    check(s == 201 and post == CIK_PROMOTE,
          "super-admin adds the filer as an issuer; primary_issuer_cik recomputes to it (independent re-read)",
          f"HTTP {s} primary={post} {_detail(b) if s != 201 else ''}")

    edit = {"include_status": "yes", "credit_entity": "Verify Promoted Credit", "notes": "edited by verify"}
    before = dict(await rls_row(conn, "SELECT * FROM portfolio.structured_note_issuers WHERE filer_cik = $1", CIK_PROMOTE))
    s, b = await api(U_ORGADMIN, "PUT", f"/api/v1/admin/edgar/issuers/{CIK_PROMOTE}", edit)
    unchanged = dict(await rls_row(conn, "SELECT * FROM portfolio.structured_note_issuers WHERE filer_cik = $1", CIK_PROMOTE))
    check(s == 403 and unchanged == before, "the SAME edit by an org admin is refused (403) and the row is unchanged",
          f"HTTP {s}")
    s, b = await api(U_SUPER, "PUT", f"/api/v1/admin/edgar/issuers/{CIK_PROMOTE}", edit)
    row = await rls_row(conn, "SELECT include_status, credit_entity, notes FROM portfolio.structured_note_issuers WHERE filer_cik = $1",
                        CIK_PROMOTE)
    check(s == 200 and dict(row) == edit, "the super-admin edit persists (independent re-read)", f"HTTP {s} {dict(row)}")
    s, b = await api(U_SUPER, "PUT", f"/api/v1/admin/edgar/issuers/{CIK_PROMOTE}", {"filer_role": "guarantor"})
    check(s == 422, "fields outside the editable list are refused (422)", f"HTTP {s}")
    s, b = await api(U_SUPER, "GET", "/api/v1/admin/edgar/issuers")
    check(s == 200 and b["vocabularies"]["editable"] == ["include_status", "credit_entity", "notes"]
          and isinstance(b.get("unlisted_filers"), list) and len(b["unlisted_filers"]) > 0
          and all(u["cik"] not in {i["filer_cik"] for i in b["rows"]} for u in b["unlisted_filers"]),
          "the issuers envelope publishes the editable fields and an unlisted-filers list with no listed issuer in it")


async def teardown_and_restore(conn, bucket, fp_before, iss_before) -> None:
    section("[Y] Teardown: R2 test objects deleted; fixture rows gone; manifest restored exactly")
    deleted = 0
    if bucket:
        deleted = await asyncio.to_thread(r2_delete_prefix, bucket, TEST_PREFIX + "/")
        left = await asyncio.to_thread(r2_list, bucket, TEST_PREFIX + "/")
        check(left == [], "every R2 object under the test prefix is deleted", f"deleted={deleted} left={len(left)}")

    sample = STATE["sample"]
    snap = STATE["sample_rows"]
    if sample:
        async with conn.transaction():
            await _super(conn)
            ref_ids = [r["reference_filing_id"] for r in await conn.fetch(
                "SELECT reference_filing_id FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[]) "
                "AND reference_filing_id IS NOT NULL", sample)]
            await conn.execute("DELETE FROM portfolio.reference_filings WHERE accession_number = ANY($1::text[])", sample)
            for acc, row in snap.items():
                cols = [c for c in row if c != "accession_number"]
                sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
                await conn.execute(f"UPDATE portfolio.edgar_index_filings SET {sets} WHERE accession_number = $1",
                                   acc, *[row[c] for c in cols])
        restored = {r["accession_number"]: dict(r) for r in await rls_fetch(
            conn, "SELECT * FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])", sample)}
        check(restored == snap, "every sample manifest row is restored exactly (all columns)")
        n_ref = await rls_val(conn, "SELECT count(*) FROM portfolio.reference_filings WHERE id = ANY($1::uuid[])", ref_ids)
        check(n_ref == 0, "the sample's reference_filings rows are deleted", f"left={n_ref}")
    if STATE["real_inserted"]:
        await rls_exec(conn, "DELETE FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])",
                       STATE["real_inserted"])
    for acc, cik in STATE.get("extra_filers", []):
        await rls_exec(conn, "DELETE FROM portfolio.edgar_index_filing_filers WHERE accession_number = $1 AND cik = $2",
                       acc, cik)

    await teardown_fixtures(conn)
    left = await rls_row(conn, """SELECT
        (SELECT count(*) FROM portfolio.edgar_index_filings WHERE accession_number LIKE $1) AS filings,
        (SELECT count(*) FROM portfolio.edgar_index_filing_filers WHERE accession_number LIKE $1) AS filers,
        (SELECT count(*) FROM portfolio.reference_filings WHERE accession_number LIKE $1) AS refs,
        (SELECT count(*) FROM portfolio.structured_note_issuers WHERE filer_cik = ANY($2::text[])) AS issuers,
        (SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE id = ANY($3::uuid[])) AS runs,
        (SELECT count(*) FROM portfolio.edgar_pipeline_lease WHERE lease_name = $4) AS lease,
        (SELECT count(*) FROM users WHERE id = ANY($5::uuid[])) AS users,
        (SELECT count(*) FROM workflow_runs WHERE id = ANY($6::uuid[])) AS wf_runs""",
        ACC_PREFIX + "%", FIXTURE_CIKS, list(STATE["pipeline_runs"]), VERIFY_LEASE, FIXTURE_USERS,
        list(STATE["workflow_runs"]))
    check(all(v == 0 for v in dict(left).values()), "zero fixture rows remain", f"{dict(left)}")
    fp_after = await manifest_fingerprint(conn)
    check(fp_after == fp_before, "the real manifest's fingerprint (row count + md5 of every row) is unchanged",
          f"before={fp_before} after={fp_after}")
    iss_after = await issuers_fingerprint(conn)
    check(iss_after == iss_before, "the real issuer table is unchanged")


def npm_build() -> None:
    section("[Y] npm run build exits 0")
    try:
        proc = subprocess.run(["npm", "run", "build"], cwd=WEB_DIR, capture_output=True, text=True, timeout=1500)
    except Exception as exc:  # noqa: BLE001
        check(False, "npm run build exits 0", type(exc).__name__)
        return
    ok = proc.returncode == 0
    check(ok, "npm run build exits 0", "" if ok else (proc.stdout + proc.stderr)[-600:])
    if ok:
        check("/admin/edgar-pipeline" in proc.stdout, "the build emitted /admin/edgar-pipeline")


async def main_async() -> int:
    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2
    if not (os.environ.get("EDGAR_USER_AGENT") or "").strip():
        print("[FAIL] EDGAR_USER_AGENT not hydrated from Doppler — no SEC request may be made")
        return 1

    from services.action_registry import REGISTRY
    from services.assistant_actions import register_all

    register_all()
    original_sync = REGISTRY.sync_catalog

    async def _noop_sync(pool, org_id):
        return None

    REGISTRY.sync_catalog = _noop_sync   # main.py's startup hook writes the REAL org's catalog

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    bucket = None
    fp_before = iss_before = None
    try:
        section("Bucket")
        # Strict: the bucket comes from Doppler's config, nowhere else. No
        # fallback, no probing for a substitute — a wrong value fails here and
        # the fetch section fails with it.
        bucket, why = resolve_bucket()
        check(bucket is not None, "the configured R2 bucket name is valid (R2_BUCKET_NAME / EDGAR_R2_BUCKET)", why)
        if bucket is not None:
            # Valid is not enough: it must be the bucket that holds the existing
            # corpus (one real object probed by its stored key, never a literal).
            key = await rls_val(conn, "SELECT r2_key FROM portfolio.reference_filings "
                                      "WHERE content_encoding = 'identity' AND r2_key IS NOT NULL LIMIT 1")
            try:
                await asyncio.to_thread(lambda: _s3().head_object(Bucket=bucket, Key=key))
                holds = True
                err = ""
            except Exception as exc:  # noqa: BLE001
                holds, err = False, type(exc).__name__
            if not check(holds, "the configured bucket holds the existing EDGAR corpus",
                         f"bucket={bucket!r} probe={key!r} {err}"):
                bucket = None

        await teardown_fixtures(conn)
        fp_before = await manifest_fingerprint(conn)
        iss_before = await issuers_fingerprint(conn)
        print(f"manifest fingerprint before: rows={fp_before[0]}")

        await task1_findings(conn)
        await migration_matches(conn)
        await seed(conn)

        async def run(name, coro):
            # One section's crash is reported and does not skip the others.
            try:
                await coro
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")

        await run("lifecycle", lifecycle_check(conn))
        await run("primary_issuer", primary_issuer(conn))
        await run("selection", selection(conn))
        await run("classification", classification())
        await run("discovery", discovery(conn))
        await run("lease", lease_section(conn))
        if bucket:
            await run("fetch", fetch_section(conn, bucket))
        else:
            check(False, "fetch tests need the configured R2 bucket", "the bucket check above failed")
        await run("nightly_workflow", nightly_workflow(conn))
        await run("existing_documents", existing_documents(conn))
        # The app's pool must live on THIS loop, the one api() calls the app on.
        from services.database import get_pool
        await get_pool()
        await run("api", api_section(conn))
        await run("paging", paging_section(conn))
        await run("issuer_edit", issuer_edit(conn))
    except Exception as exc:  # noqa: BLE001 — report, then still tear down
        import traceback
        traceback.print_exc()
        check(False, "the verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if fp_before is not None:
                await teardown_and_restore(conn, bucket, fp_before, iss_before)
            else:
                await teardown_fixtures(conn)
        finally:
            REGISTRY.sync_catalog = original_sync
            from services.database import close_pool
            await close_pool()
            await conn.close()

    npm_build()

    print(f"\n{'=' * 70}")
    if _finds:
        print(f"FINDINGS: {len(_finds)}")
    if _blocked:
        print(f"BLOCKED (not counted as FAIL): {len(_blocked)} — {_blocked}")
    print(f"TOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    print("=" * 70)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
