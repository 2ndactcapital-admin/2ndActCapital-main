"""verify_bulkfetchui3.py — BULKFETCH UI 3: the Institutions table (Downloaded /
Stored, Refresh, sorting, height, Refresh index moved), the weekday automatic
index refresh, the GS image-cover classifier fix + --reclassify-other, the
zero-byte retry, scripts/run_regressions.sh and the regression-policy edit.

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- \\
        apps/api/venv/bin/python -u apps/api/scripts/verify_bulkfetchui3.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on
any failure. Hydrates secrets from Doppler over HTTPS (_db_bootstrap).

REFUSES TO START (one line, exit 2) while a laptop bulk worker is heartbeating:
it could claim a fixture row and it changes real rows under the checksum.

NO NETWORK. Every SEC request goes to an ``httpx.MockTransport``; R2 is a dict
behind ``services.storage`` (a real S3 client RAISES); every model entry point
is a recorder that raises. Real spend: $0.

FIXTURES — tagged, torn down at the START and at the END:
  * structured_note_issuers in issuer groups 'VERIFY BFUI3 …', 9-digit CIKs
    starting 99094 (asserted unused by any real row first);
  * edgar_index_filings / edgar_index_filing_filers / reference_filings rows
    whose accession starts 9999999999-93- (no other verify uses -93-; the
    accession CHECK needs 10-2-6 digits, and no SEC accession has a 9999999999
    filer agent);
  * edgar_pipeline_runs: requested by a fixture user, OR details.bulk.institutions
    naming a fixture group (the zero-byte Runner run), OR trigger_source
    'schedule' with a schedule_date in 2001 (the scheduled-refresh tests run on
    INJECTED 2001 clocks, so the real day's once-per-day slot is never taken),
    and their 'bulk_fetch:<id>' leases;
  * a fixture org, a super-admin and an org-admin user (is_test = true, auth0_sub
    = the literal sub the mocked token carries).
Every id is a full UUID or a counter — never a slice of a UUID.

NEVER TOUCHED: real edgar_index_filings rows of Wells Fargo, Goldman Sachs and
Jefferies (count + checksum of status/kind before and after); real users,
documents, runs. --reclassify-other is exercised ONLY with ``accessions=`` set
to fixture accessions.

Teardown is FK child-before-parent from pg_constraint (contype::text), then
audit_log / assistant_activities / agent_proposals by fixture id before the
fixture users, then every touched table is re-read for its exact pre-test count.
The regression, verify_bulkfetch.py (the classifier is shared), runs ONCE as a
flat subprocess with VERIFY_NESTED=1 and a 1200 s timeout, only after this
script's own teardown left zero fixture rows.
"""
from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import gzip
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"
DISCOVERY_DOC = REPO / "docs" / "BULKFETCHUI3_DISCOVERY.md"
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
STANDARD_DOC = REPO / "docs" / "SPRINT_WORKFLOW_STANDARD.md"
RUNNER_SH = REPO / "scripts" / "run_regressions.sh"
REGRESSION_SET = REPO / "scripts" / "regression_set.txt"
FIXTURE_TEXTS = HERE.parent / "fixtures" / "bulkfetchui3"
NODE_TEST = "tests/edgar/institutionsTable.test.mjs"

TAG = "bulkfetchui3-verify"
ACC_PREFIX = "9999999999-93-"
CIK_PREFIX = "99094"
GROUP_PREFIX = "VERIFY BFUI3 "
G_ALPHA = GROUP_PREFIX + "Alpha"
G_ZERO = GROUP_PREFIX + "Zero"
G_RECL = GROUP_PREFIX + "Reclassify"
ORG = UUID("99000000-0000-0000-0000-00000b0f2301")
U_SUPER = UUID("99000000-0000-0000-0000-00000b0f2311")
U_ORGADMIN = UUID("99000000-0000-0000-0000-00000b0f2312")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN]
FIXTURE_EMAIL_LIKE = "bulkfetchui3_%@test.local"
HEADERS = {"Authorization": "Bearer verify-token"}
BASE = "/api/v1/admin/edgar/bulk"
BUCKET = "verify-bfui3-bucket"
R2_PREFIX = "reference/edgar-verify-bfui3"
SCHEDULE_YEAR_LIKE = "2001-%"
REAL_GROUPS = ("Wells Fargo", "Goldman Sachs", "Jefferies")
DOWNLOADED_SET = ("fetched", "fetch_failed", "not_pricing_supplement", "prefilter_skipped",
                  "ready_for_extraction", "extraction_submitted", "extracted", "needs_review",
                  "extraction_failed")

GS_FINAL = "0001193125-26-410827"            # "Pricing Supplement No. 26,690 dated September 29, 2026"
GS_PROSPECTUS_FINAL = "0001193125-26-203976"  # "Prospectus Supplement No. 23,980 dated April 30, 2026"
GS_PRELIM = "0000950170-25-001364"           # real GS preliminary: "Subject to Completion. Dated January 3, 2025."
GS_SAMPLES = ["0001193125-26-410827", "0001193125-26-203976", "0001193125-26-267914", "0001193125-26-230908",
              "0001193125-26-008800", "0001193125-26-138073", "0001193125-26-204373", "0000950103-26-006519",
              "0001193125-26-041209", "0001193125-26-221826"]
TRUE_OTHER_SAMPLES = ["0001193125-25-111621", "0001104659-22-011404", "0001193125-22-120761",
                      "0001193125-21-209213", "0001104659-25-109994", "0001193125-21-220520",
                      "0001918704-26-008797", "0000950103-25-009744"]

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} ({type(passed).__name__}), not a bool, for {label!r}")
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def _sub(uid: UUID) -> str:
    return f"bulkfetchui3_{uid.hex}"


# ═══ RLS-scoped DB helpers ═════════════════════════════════════════════════
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


async def check_defs(conn, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = $1::regclass AND contype::text IN ('c', 'u', 'f')", table)
    return {r["conname"]: r["def"] for r in rows}


async def unique_indexes(conn, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT pg_get_indexdef(indexrelid) AS d FROM pg_index WHERE indrelid = $1::regclass AND indisunique", table)
    return [r["d"] for r in rows]


def allowed(defs: dict[str, str], column: str) -> set[str]:
    out: set[str] = set()
    for d in defs.values():
        if d.startswith("CHECK") and re.search(rf"\(\(?{column}\b", d) and "ARRAY[" in d:
            out |= set(re.findall(r"'([^']+)'::text", d))
    return out


# ═══ Fixture ids ═══════════════════════════════════════════════════════════
_acc_n = 0
_cik_n = 0


def next_acc() -> str:
    global _acc_n
    _acc_n += 1
    return f"{ACC_PREFIX}{_acc_n:06d}"


def next_cik() -> str:
    global _cik_n
    _cik_n += 1
    return f"{CIK_PREFIX}{_cik_n:04d}"


def nodash(acc: str) -> str:
    return acc.replace("-", "")


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.edgar_pipeline_lease", "portfolio.reference_filing_supplements",
    "portfolio.edgar_index_filing_filers", "portfolio.edgar_index_filings", "portfolio.reference_filings",
    "portfolio.edgar_pipeline_runs", "portfolio.structured_note_issuers",
)
COUNTED = TOUCHED + ("public.users", "public.organizations", "public.roles", "public.user_roles",
                     "portfolio.edgar_selection_policies", "portfolio.edgar_fetch_workers")

_RUN_FIXTURE_SQL = """
    SELECT id FROM portfolio.edgar_pipeline_runs r
     WHERE r.requested_by = ANY($1::uuid[])
        OR (r.trigger_source = 'schedule' AND r.details->>'schedule_date' LIKE $3)
        OR (jsonb_typeof(r.details->'bulk'->'institutions') = 'array'
            AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.details->'bulk'->'institutions') b WHERE b LIKE $2))
"""


async def fixture_keys(conn) -> dict:
    filings = await rls_fetch(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                              ACC_PREFIX + "%")
    runs = await rls_fetch(conn, _RUN_FIXTURE_SQL, FIXTURE_USERS, GROUP_PREFIX + "%", SCHEDULE_YEAR_LIKE)
    return {"filings": [r["id"] for r in filings], "runs": [r["id"] for r in runs]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    f, runs = k["filings"], k["runs"]
    return {
        "portfolio.edgar_pipeline_lease": ("lease_name = ANY($1::text[])", [[f"bulk_fetch:{r}" for r in runs]]),
        "portfolio.reference_filing_supplements":
            ("reference_filing_id = ANY($1::uuid[]) OR supplement_accession LIKE $2", [f, ACC_PREFIX + "%"]),
        "portfolio.edgar_index_filing_filers": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.edgar_index_filings": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.reference_filings": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.edgar_pipeline_runs": ("id = ANY($1::uuid[])", [runs]),
        "portfolio.structured_note_issuers": ("issuer_group LIKE $1", [GROUP_PREFIX + "%"]),
    }


async def delete_order(conn) -> list[str]:
    rows = await conn.fetch(
        """SELECT n1.nspname || '.' || c1.relname AS child, n2.nspname || '.' || c2.relname AS parent
             FROM pg_constraint k
             JOIN pg_class c1 ON c1.oid = k.conrelid JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
             JOIN pg_class c2 ON c2.oid = k.confrelid JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
            WHERE k.contype::text = 'f'""")
    tables = set(TOUCHED)
    children: dict[str, set[str]] = {t: set() for t in tables}
    for r in rows:
        if r["child"] in tables and r["parent"] in tables and r["child"] != r["parent"]:
            children[r["parent"]].add(r["child"])
    order, remaining = [], set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not (children[t] & remaining))
        if not ready:
            raise RuntimeError(f"FK cycle among {sorted(remaining)}")
        order += ready
        remaining -= set(ready)
    return order


async def fixture_counts(conn) -> dict[str, int]:
    k = await fixture_keys(conn)
    out = {t: await rls_val(conn, f"SELECT count(*) FROM {t} WHERE {w}", *a) for t, (w, a) in predicates(k).items()}
    out["public.users"] = await rls_val(
        conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    out["public.organizations"] = await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG)
    out["public.roles"] = await rls_val(conn, "SELECT count(*) FROM roles WHERE org_id = $1", ORG)
    return out


async def teardown(conn) -> None:
    k = await fixture_keys(conn)
    preds = predicates(k)
    for t in await delete_order(conn):
        where, args = preds[t]
        await rls_exec(conn, f"DELETE FROM {t} WHERE {where}", *args)
    rows = await rls_fetch(
        conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    uids = list({r["id"] for r in rows} | set(FIXTURE_USERS))
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM audit_log WHERE org_id = $1 OR user_id = ANY($2::uuid[])", ORG, uids)
        await conn.execute("DELETE FROM assistant_activities WHERE org_id = $1 OR user_id = ANY($2::uuid[])", ORG, uids)
        await conn.execute("DELETE FROM agent_proposals WHERE org_id = $1 OR proposed_by = ANY($2::uuid[])", ORG, uids)
        await conn.execute("DELETE FROM user_roles WHERE user_id = ANY($1::uuid[]) "
                           "OR role_id IN (SELECT id FROM roles WHERE org_id = $2)", uids, ORG)
        await conn.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE org_id = $1)", ORG)
        await conn.execute("DELETE FROM roles WHERE org_id = $1", ORG)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", uids)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def table_counts(conn) -> dict[str, int]:
    return {t: await rls_val(conn, f"SELECT count(*) FROM {t}") for t in COUNTED}


async def real_filings_checksum(conn) -> tuple[int, str]:
    """count + md5 over (accession, status, kind) of the three real banks' filings."""
    r = await rls_row(
        conn, """SELECT count(*) AS n,
                        md5(COALESCE(string_agg(f.accession_number || ':' || f.pipeline_status || ':'
                                                || COALESCE(f.document_kind, ''), ',' ORDER BY f.accession_number), '')) AS h
                   FROM portfolio.edgar_index_filings f
                   JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
                  WHERE i.issuer_group = ANY($1::text[]) AND f.accession_number NOT LIKE $2""",
        list(REAL_GROUPS), ACC_PREFIX + "%")
    return int(r["n"]), r["h"]


# ═══ Mocks: SEC, R2, models ════════════════════════════════════════════════
DOCS: dict[str, dict] = {}          # accession nodash -> {doc, body, form}
INDEX_FILES: dict[str, str] = {}    # YYYYMMDD -> master.idx body
SEC_LOG: list[dict] = []
R2: dict[str, bytes] = {}
MODEL_CALLS: list[str] = []
HTTP_CLIENTS: list[str] = []        # every httpx.AsyncClient constructed while a section is watching
WATCH_CLIENTS = [False]

_DOC_RE = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/(.+)$")
_DAILY_RE = re.compile(r"/Archives/edgar/daily-index/\d{4}/QTR\d/master\.(\d{8})\.idx$")


async def sec_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    SEC_LOG.append({"url": str(request.url), "range": request.headers.get("Range")})
    m = _DAILY_RE.search(path)
    if m:
        body = INDEX_FILES.get(m.group(1))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    m = _DOC_RE.search(path)
    if not m:
        return httpx.Response(404)
    d = DOCS.get(m.group(2))
    if d is None:
        return httpx.Response(404)
    tail = m.group(3)
    if tail == "index.json":
        return httpx.Response(200, json={"directory": {"item": [{"name": d["doc"], "size": len(d["body"])}]}})
    if tail.endswith("-index-headers.html"):
        return httpx.Response(200, text=f"<TYPE>{d.get('form', '424B2')}\n<FILE-NUMBER>333-999999\n")
    if tail == d["doc"]:
        body = d["body"]
        rng = request.headers.get("Range")
        if rng:
            n = int(rng.split("-")[1]) + 1
            return httpx.Response(206, content=body[:n],
                                  headers={"Content-Range": f"bytes 0-{max(min(n, len(body)) - 1, 0)}/{len(body)}"})
        return httpx.Response(200, content=body)
    return httpx.Response(404)


def sec_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(sec_handler))


def failing_client() -> httpx.AsyncClient:
    """Every request answers 400: raise_for_status raises at once (no retry)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(400, text="bad request")))


def install_mocks():
    from services import edgar_fetch, extraction, storage
    from services.note_extraction import proxy

    def fake_upload(key, data, content_type=None, bucket=None):
        R2[key] = bytes(data)
        return key

    def no_s3(*_a, **_k):
        raise AssertionError("a real R2 client was requested during verify_bulkfetchui3")

    storage.upload_bytes = fake_upload
    storage.download_bytes = lambda key, bucket=None: R2[key]
    storage.get_s3_client = no_s3

    def recorder(name):
        async def _no(*_a, **_k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"{name} must never be called by bulkfetchui3")
        return _no

    proxy.chat = recorder("proxy.chat")
    proxy.jev = recorder("proxy.jev")
    for fn in ("call_claude_json", "call_claude_text", "call_claude_with_tools"):
        setattr(extraction, fn, recorder(f"extraction.{fn}"))

    orig_init = httpx.AsyncClient.__init__

    def watched_init(self, *a, **k):
        if WATCH_CLIENTS[0]:
            HTTP_CLIENTS.append(repr(k.get("transport")))
        orig_init(self, *a, **k)

    httpx.AsyncClient.__init__ = watched_init
    os.environ.setdefault("EDGAR_USER_AGENT", "Hollisworks verify_bulkfetchui3 (verify@test.local)")
    edgar_fetch.set_rate_limit(8)


# ═══ API through the real app (mocked token) ═══════════════════════════════
async def api(uid, method, path, body=None):
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


async def run_row(url: str, rid) -> dict:
    """INDEPENDENT re-read: a fresh connection, RLS context set."""
    c = await asyncpg.connect(url, statement_cache_size=0)
    try:
        r = await rls_row(c, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", rid)
        return dict(r) if r else {}
    finally:
        await c.close()


async def fresh_rows(url: str, q: str, *a) -> list[dict]:
    c = await asyncpg.connect(url, statement_cache_size=0)
    try:
        return [dict(r) for r in await rls_fetch(c, q, *a)]
    finally:
        await c.close()


def jd(v) -> dict:
    if v is None:
        return {}
    return json.loads(v) if isinstance(v, str) else dict(v)


# ═══ Fixture builders ══════════════════════════════════════════════════════
ACTIVE_V: int | None = None


async def make_group(conn, name: str, n_ciks: int = 1) -> list[str]:
    ciks = [next_cik() for _ in range(n_ciks)]
    async with conn.transaction():
        await _super(conn)
        for i, cik in enumerate(ciks):
            await conn.execute(
                """INSERT INTO portfolio.structured_note_issuers
                       (filer_cik, issuer_group, filer_name, filer_role, credit_entity, include_status, notes)
                   VALUES ($1, $2, $3, $4, $5, 'yes', $6)""",
                cik, name, f"{name} Filer {i + 1}", "issuer" if i == 0 else "guarantor", f"{name} Credit", TAG)
    return ciks


async def make_filing(conn, cik: str, day: date, *, form: str = "424B2", status: str = "selected",
                      kind: str | None = None, reason: str | None = None, stored: str | None = None,
                      text: str | None = None, next_attempt=None) -> str:
    """One manifest row (+ filer row). stored: None (no reference row), 'r2' (r2_key + text_r2_key),
    'cleared' (reference row, keys NULL). ``text`` (gzipped into mocked R2) is the stored text."""
    acc = next_acc()
    ref_id = None
    async with conn.transaction():
        await _super(conn)
        if stored is not None:
            doc = "doc.htm"
            base = f"{R2_PREFIX}/{cik}/{acc}/{doc}"
            keys = (f"{base}.gz", f"{base}.txt.gz") if stored == "r2" else (None, None)
            ref_id = await conn.fetchval(
                """INSERT INTO portfolio.reference_filings
                       (cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                        r2_key, text_r2_key, extraction_status, retention_classification, content_encoding)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'fetched', 'public_reference', 'gzip')
                   RETURNING id""",
                cik, "VERIFY BFUI3 fixture filer", form, acc, day, doc,
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash(acc)}/{doc}", keys[0], keys[1])
            if stored == "r2":
                R2[keys[0]] = gzip.compress(b"<html>fixture</html>")
                R2[keys[1]] = gzip.compress((text or "fixture text").encode("utf-8"))
        await conn.execute(
            """INSERT INTO portfolio.edgar_index_filings
                   (accession_number, form_type, filing_date, index_quarter, submission_path, filer_count,
                    pipeline_status, selection_policy_version, primary_issuer_cik, status_reason, document_kind,
                    reference_filing_id, next_attempt_at)
               VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8, $9, $10, $11, $12)""",
            acc, form, day, f"{day.year}Q{(day.month - 1) // 3 + 1}", f"edgar/data/{cik}/{acc}.txt", status,
            None if status in ("discovered", "fetched") else ACTIVE_V, cik, reason or f"{TAG} fixture", kind,
            ref_id, next_attempt)
        await conn.execute(
            "INSERT INTO portfolio.edgar_index_filing_filers (accession_number, cik, company_name) VALUES ($1, $2, $3)",
            acc, cik, "VERIFY BFUI3 fixture filer")
    return acc


async def seed(conn) -> None:
    global ACTIVE_V
    from services.rbac import grant_org_admin
    users_defs = await check_defs(conn, "users")
    roles_ok = allowed(users_defs, "role")
    check(not roles_ok or {"super_admin", "member"} <= roles_ok, "fixture users.role values satisfy the live CHECK",
          f"{sorted(roles_ok)}")
    st_ok = allowed(users_defs, "status")
    check(not st_ok or "active" in st_ok, "fixture users.status 'active' satisfies the live CHECK", f"{sorted(st_ok)}")
    idx_defs = await check_defs(conn, "portfolio.edgar_index_filings")
    s_ok = allowed(idx_defs, "pipeline_status")
    k_ok = allowed(idx_defs, "document_kind")
    need_s = {"selected", "not_selected", "discovered", "fetched", "fetch_failed", "not_pricing_supplement",
              "prefilter_skipped", "ready_for_extraction"}
    check(need_s <= s_ok and {"other", "pricing_supplement", "preliminary_pricing_supplement"} <= k_ok,
          "fixture statuses / kinds are live CHECK values (edgar_index_filings)", f"{sorted(need_s - s_ok)}")
    check(any("decided_has_policy" in n for n in idx_defs) and any("one_selector" in n for n in idx_defs),
          "decided_has_policy / one_selector CHECKs read from the catalog (fixtures stamp the ACTIVE policy)")
    ref_defs = await check_defs(conn, "portfolio.reference_filings")
    check({"424B2"} <= allowed(ref_defs, "form_type") and {"fetched"} <= allowed(ref_defs, "extraction_status")
          and {"gzip"} <= allowed(ref_defs, "content_encoding"),
          "fixture reference_filings values satisfy the live CHECKs")
    run_defs = await check_defs(conn, "portfolio.edgar_pipeline_runs")
    check("schedule" in allowed(run_defs, "trigger_source") and "verify" in allowed(run_defs, "trigger_source"),
          "trigger_source 'schedule' is a live CHECK value (migration bulkfetchui3 applied)",
          f"{sorted(allowed(run_defs, 'trigger_source'))}")
    uq = await unique_indexes(conn, "portfolio.edgar_pipeline_runs")
    check(any("schedule_date" in d and "trigger_source = 'schedule'" in d for d in uq),
          "the per-day partial unique index on details->>'schedule_date' exists", f"{len(uq)} unique indexes")
    inc_ok = allowed(await check_defs(conn, "portfolio.structured_note_issuers"), "include_status")
    check(not inc_ok or "yes" in inc_ok, "fixture include_status 'yes' satisfies the live CHECK", f"{sorted(inc_ok)}")
    clash = await rls_val(conn, "SELECT count(*) FROM portfolio.structured_note_issuers "
                                "WHERE filer_cik LIKE $1 AND issuer_group NOT LIKE $2", CIK_PREFIX + "%",
                          GROUP_PREFIX + "%")
    check(clash == 0, f"no real issuer uses a {CIK_PREFIX}… CIK", f"{clash}")
    ACTIVE_V = await rls_val(conn, "SELECT version FROM portfolio.edgar_selection_policies WHERE is_active")
    check(isinstance(ACTIVE_V, int), "an active selection policy exists (fixtures stamp its version)", f"v{ACTIVE_V}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "BULKFETCHUI3 Verify Fixture Org", f"{ORG}-bulkfetchui3")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active, status, is_test)
                   VALUES ($1, $2, $3, $4, $5, $6, true, 'active', true)""",
                uid, ORG, f"{sub}@test.local", sub, sub, role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)
    is_test = await rls_val(conn, "SELECT bool_and(is_test) FROM users WHERE id = ANY($1::uuid[])", FIXTURE_USERS)
    check(is_test is True, "fixture users are is_test = true (independent read)")


# ═══ Sections ══════════════════════════════════════════════════════════════
def y_task1() -> None:
    section("[Y] Task 1 — findings 1a–1d reported with files, SQL and live totals")
    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    check(bool(text), "docs/BULKFETCHUI3_DISCOVERY.md exists")
    for sec_id in ("## 1a.", "## 1b.", "## 1c.", "## 1d."):
        check(sec_id in text, f"discovery doc has section {sec_id}")
    check("reference_filing_id" in text and "text_r2_key" in text and "peek_decision" in text
          and "count(*) FILTER (WHERE r.r2_key IS NOT NULL OR r.text_r2_key IS NOT NULL)" in text,
          "1a names where storage is recorded, how --kinds keeps/discards, and gives the SQL")
    check(all(n in text for n in ("21,877", "11,667", "Goldman Sachs", "Wells Fargo", "Jefferies")),
          "1a reports live totals (all + Wells Fargo, Goldman Sachs, Jefferies)")
    check("edgar_index_filings_queue_idx" in text and "edgar_index_filings_primary_issuer_idx" in text,
          "1a states which index serves the query")
    check(GS_FINAL in text and "3,184" in text and "COVER_LINE_CHARS" in text and "zero" in text.lower(),
          "1b reports the breakdown, the GS sample, the rule and its result")
    check("Choice: (A)" in text and "Operator, in every case" in text, "1c states the choice and the operator steps")
    check("next_attempt_at = NULL" in text and "EmptyDocumentError" in text, "1d states the zero-byte behaviour and fix")


async def y_downloaded_stored(conn, url, q) -> None:
    section("[Y] Downloaded / Stored equal the 1a SQL per institution and per filer; exclusions; fresh vs cached")
    ciks = await make_group(conn, G_ALPHA, 2)
    d = date(2026, 9, 10)
    c0, c1 = ciks
    # c0: a known mix
    await make_filing(conn, c0, d, status="selected")
    await make_filing(conn, c0, d, status="selected")
    await make_filing(conn, c0, d, status="not_selected")                                   # excluded
    await make_filing(conn, c0, d, status="discovered")                                     # excluded
    await make_filing(conn, c0, d, status="fetched", stored="r2")                           # D + S
    await make_filing(conn, c0, d, status="not_pricing_supplement", kind="preliminary_pricing_supplement",
                      reason="peek: preliminary (cover only)")                              # D
    await make_filing(conn, c0, d, status="not_pricing_supplement", kind="other", stored="r2")   # D + S
    await make_filing(conn, c0, d, status="prefilter_skipped", kind="pricing_supplement", stored="r2")  # D + S
    await make_filing(conn, c0, d, status="ready_for_extraction", kind="pricing_supplement", stored="cleared")  # D
    await make_filing(conn, c0, d, status="fetch_failed")                                   # D
    await make_filing(conn, c0, d, form="FWP", status="ready_for_extraction", stored="r2")  # wrong form: excluded
    # c1: two handled, one stored
    await make_filing(conn, c1, date(2025, 3, 3), status="ready_for_extraction", kind="pricing_supplement", stored="r2")
    await make_filing(conn, c1, date(2025, 3, 3), status="not_pricing_supplement", kind="product_supplement",
                      reason="peek: product")
    expected = {c0: (6, 3), c1: (2, 1)}

    sql_rows = await rls_fetch(
        conn, """SELECT f.primary_issuer_cik AS cik, count(*)::int AS downloaded,
                        count(*) FILTER (WHERE r.r2_key IS NOT NULL OR r.text_r2_key IS NOT NULL)::int AS stored
                   FROM portfolio.edgar_index_filings f
                   LEFT JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
                  WHERE f.pipeline_status = ANY($2::text[]) AND f.form_type = '424B2'
                    AND f.primary_issuer_cik = ANY($1::text[])
                  GROUP BY 1""", ciks, list(DOWNLOADED_SET))
    direct = {r["cik"]: (r["downloaded"], r["stored"]) for r in sql_rows}
    check(direct == expected, "the 1a SQL run directly gives the hand-counted fixture numbers", f"{direct}")

    code, body = await api(U_SUPER, "GET", f"{BASE}/institutions?fresh=1")
    rows = {r["name"]: r for r in (body or {}).get("rows", [])} if code == 200 else {}
    inst = rows.get(G_ALPHA) or {}
    filers = {f["cik"]: f for f in inst.get("filers", [])}
    per_filer = {c: (filers.get(c, {}).get("downloaded"), filers.get(c, {}).get("stored")) for c in ciks}
    check(code == 200 and per_filer == direct, "per filer: the payload's downloaded/stored equal the 1a SQL",
          f"HTTP {code} {per_filer}")
    check((inst.get("downloaded"), inst.get("stored")) == (8, 4),
          "per institution: downloaded 8 / stored 4 (the sum of its filers, all years)",
          f"{inst.get('downloaded')}, {inst.get('stored')}")
    check(inst.get("total") == 2, "Total still counts only 'selected' (2) — the new columns did not change it",
          f"{inst.get('total')}")
    check(isinstance((body or {}).get("as_of"), str) and bool(body.get("as_of")), "the payload carries as_of",
          f"{(body or {}).get('as_of')}")
    cols = ((body or {}).get("vocabularies") or {}).get("institution_columns") or {}
    check(cols.get("downloaded", {}).get("label") == "Downloaded" and cols.get("stored", {}).get("label") == "Stored"
          and bool(cols["downloaded"].get("help")) and bool(cols["stored"].get("help")),
          "column labels and one-line explanations come from the envelope's vocabularies", f"{cols}")
    # negative: statuses outside the set really are excluded (not just "fewer")
    excl = await rls_val(conn, """SELECT count(*) FROM portfolio.edgar_index_filings
                                   WHERE primary_issuer_cik = $1 AND pipeline_status IN ('selected', 'not_selected', 'discovered')""", c0)
    check(excl == 4 and direct[c0][0] == 6,
          "the 4 selected / not_selected / discovered rows are NOT counted as downloaded (6 of 10 424B2 rows)",
          f"excluded rows {excl}")
    # fresh vs cached
    old_as_of = body.get("as_of") if code == 200 else None
    await make_filing(conn, c1, date(2025, 3, 4), status="ready_for_extraction", kind="pricing_supplement", stored="r2")
    code2, body2 = await api(U_SUPER, "GET", f"{BASE}/institutions")
    i2 = {r["name"]: r for r in (body2 or {}).get("rows", [])}.get(G_ALPHA, {})
    check(code2 == 200 and (i2.get("downloaded"), i2.get("stored")) == (8, 4) and body2.get("as_of") == old_as_of,
          "the cached call within 60 s returns the OLD numbers (and the old as_of) after a fixture change",
          f"{i2.get('downloaded')}, {i2.get('stored')}")
    code3, body3 = await api(U_SUPER, "GET", f"{BASE}/institutions?fresh=1")
    i3 = {r["name"]: r for r in (body3 or {}).get("rows", [])}.get(G_ALPHA, {})
    check(code3 == 200 and (i3.get("downloaded"), i3.get("stored")) == (9, 5),
          "?fresh=1 returns the NEW numbers (9 / 5)", f"{i3.get('downloaded')}, {i3.get('stored')}")


async def y_gate(conn) -> None:
    section("[Y] Institutions gate: non-super-admin 403, super admin 200, identical request (fresh and cached)")
    for path in (f"{BASE}/institutions", f"{BASE}/institutions?fresh=1"):
        c1, b1 = await api(U_ORGADMIN, "GET", path)
        c2, _ = await api(U_SUPER, "GET", path)
        leaked = "rows" in (b1 or {})
        check(c1 == 403 and not leaked and c2 == 200, f"GET {path}: org admin 403 (no rows leaked), super admin 200",
              f"org admin HTTP {c1}; super HTTP {c2}")


def y_frontend() -> None:
    section("[Y] Front end — node tests on the REAL InstitutionPicker; the whole node suite")
    names = {
        "every column sorts both ways with the right comparator (numeric counts, case-insensitive names)":
            "sorting both ways on every column",
        "each header is a sort button with aria-sort; the sorted column alone carries the arrow": "aria-sort + arrow",
        "filer sub-rows stay under their institution and sort the same way among themselves": "sub-rows follow",
        "Downloaded and Stored render after Total, labelled from the envelope, with each row's numbers":
            "new columns",
        "Refresh with 'as of', then Refresh index + its note, in the header after Select none":
            "Refresh with as of; Refresh index + note in the Institutions header",
        "a non-writable envelope renders no Refresh index button and no note": "hidden when not writable",
        "an active refresh_index run disables the button with the right label": "disabled while a refresh runs",
        "the counts re-read every 60 s only while a bulk run is queued or running": "60 s re-read while bulk active",
        "Refresh index is gone from the Time frame card; no fixed max-h-80; the table fills its box":
            "absent from Time frame; no max-h-80",
    }
    try:
        proc = subprocess.run(["node", "--test", NODE_TEST], cwd=str(WEB_DIR), capture_output=True, text=True,
                              timeout=300)
        out = proc.stdout + proc.stderr
        oks = set(re.findall(r"^ok \d+ - (.+)$", out, re.M))
        for name, label in names.items():
            check(name in oks, f"node test: {label}", "ok" if name in oks else "missing or failing")
    except Exception as exc:  # noqa: BLE001
        check(False, "node tests for the Institutions table ran", f"{type(exc).__name__}: {exc}")
    try:
        proc = subprocess.run(["node", "--test", "tests/"], cwd=str(WEB_DIR), capture_output=True, text=True,
                              timeout=900)
        out = proc.stdout + proc.stderr
        m_pass = re.search(r"^# pass (\d+)", out, re.M)
        m_fail = re.search(r"^# fail (\d+)", out, re.M)
        check(proc.returncode == 0 and m_pass is not None and int(m_pass.group(1)) > 0
              and m_fail is not None and int(m_fail.group(1)) == 0,
              "node --test tests/ passes (bugfix1.test.mjs's InstitutionPicker test included)",
              f"{m_pass.group(0) if m_pass else ''} {m_fail.group(0) if m_fail else out[-200:]}")
    except Exception as exc:  # noqa: BLE001
        check(False, "node --test tests/ ran", f"{type(exc).__name__}: {exc}")
    try:
        proc = subprocess.run(["npm", "run", "build"], cwd=str(WEB_DIR), capture_output=True, text=True, timeout=1200)
        check(proc.returncode == 0, "npm run build exits 0", (proc.stdout + proc.stderr)[-300:] if proc.returncode else "")
    except Exception as exc:  # noqa: BLE001
        check(False, "npm run build ran", f"{type(exc).__name__}: {exc}")


async def y_schedule(conn, url) -> None:
    section("[Y] Scheduled refresh: once per weekday after 10:30 UTC; idempotent; off switch; failure recorded")
    from services import edgar_bulk as eb
    from services import edgar_index_schedule as sch
    ciks = await make_group(conn, GROUP_PREFIX + "Schedule")
    real_last = eb.last_indexed_date
    pinned = {"d": date(2001, 1, 2)}

    async def fake_last(_conn):
        return pinned["d"]

    eb.last_indexed_date = fake_last      # the refresh reads 2001 days only; real max(filing_date) never moves
    accs = [next_acc(), next_acc()]
    INDEX_FILES["20010102"] = ("CIK|Company Name|Form Type|Date Filed|Filename\n-----\n"
                               f"{ciks[0]}|VERIFY BFUI3 Schedule Filer|424B2|2001-01-02|edgar/data/{ciks[0]}/{accs[0]}.txt\n")
    INDEX_FILES["20010103"] = ("CIK|Company Name|Form Type|Date Filed|Filename\n-----\n"
                               f"{ciks[0]}|VERIFY BFUI3 Schedule Filer|424B2|2001-01-03|edgar/data/{ciks[0]}/{accs[1]}.txt\n")
    os.environ.pop(sch.AUTO_REFRESH_ENV, None)
    wed_1100 = datetime(2001, 1, 3, 11, 0, tzinfo=timezone.utc)

    async def sched_rows(day: str) -> list[dict]:
        return await fresh_rows(url, """SELECT * FROM portfolio.edgar_pipeline_runs
                                         WHERE trigger_source = 'schedule' AND details->>'schedule_date' = $1""", day)

    try:
        real_max_before = await rls_val(conn, "SELECT max(filing_date) FROM portfolio.edgar_index_filings")
        # not due: before 10:30, weekend
        r_early = await sch.maybe_refresh_index(conn, now_utc=datetime(2001, 1, 3, 10, 29, tzinfo=timezone.utc),
                                                client_factory=sec_client, log=lambda m: None)
        r_sat = await sch.maybe_refresh_index(conn, now_utc=datetime(2001, 1, 6, 12, 0, tzinfo=timezone.utc),
                                              client_factory=sec_client, log=lambda m: None)
        check(r_early["action"] == "not_due" and r_sat["action"] == "not_due"
              and not await sched_rows("2001-01-03") and not await sched_rows("2001-01-06"),
              "a 10:29 UTC weekday tick and a Saturday tick do nothing (no run row, re-read)",
              f"{r_early['action']}, {r_sat['action']}")
        # off switch
        os.environ[sch.AUTO_REFRESH_ENV] = "0"
        r_off = await sch.maybe_refresh_index(conn, now_utc=wed_1100, client_factory=sec_client, log=lambda m: None)
        os.environ.pop(sch.AUTO_REFRESH_ENV, None)
        check(r_off["action"] == "off" and not await sched_rows("2001-01-03"),
              f"{sch.AUTO_REFRESH_ENV}=0 stops it (no run row, re-read)", r_off["action"])
        # first eligible tick
        n_sec = len(SEC_LOG)
        r1 = await sch.maybe_refresh_index(conn, now_utc=wed_1100, client_factory=sec_client, log=lambda m: None)
        rows1 = await sched_rows("2001-01-03")
        row = rows1[0] if rows1 else {}
        det = jd(row.get("details"))
        new_idx = await fresh_rows(url, "SELECT accession_number, pipeline_status FROM portfolio.edgar_index_filings "
                                        "WHERE accession_number = ANY($1::text[])", accs)
        check(r1["action"] == "succeeded" and len(rows1) == 1, "the first eligible weekday tick refreshes and writes ONE run row",
              f"{r1}")
        check(row.get("run_kind") == "refresh_index" and row.get("trigger_source") == "schedule"
              and row.get("status") == "succeeded" and row.get("finished_at") is not None,
              "the row: run_kind refresh_index, trigger_source 'schedule', succeeded (independent re-read)",
              f"{row.get('run_kind')}, {row.get('trigger_source')}, {row.get('status')}")
        check(det.get("through") == "2001-01-02" and (det.get("refresh_index_result") or {}).get("new_filings") == 1
              and row.get("discovered_new") == 1,
              "it refreshed THROUGH YESTERDAY: the 2001-01-02 file loaded, the 2001-01-03 one not; counts in details",
              f"through {det.get('through')}, new {row.get('discovered_new')}")
        check(len(new_idx) == 1 and new_idx[0]["accession_number"] == accs[0],
              "exactly the mocked index's fixture filing is in the index (re-read)", f"{new_idx}")
        check(len(SEC_LOG) > n_sec and all("sec.gov" in e["url"] for e in SEC_LOG[n_sec:]),
              "its SEC requests went to the mocked transport", f"{len(SEC_LOG) - n_sec} request(s)")
        # second tick the same day
        n_sec = len(SEC_LOG)
        r2 = await sch.maybe_refresh_index(conn, now_utc=datetime(2001, 1, 3, 15, 0, tzinfo=timezone.utc),
                                           client_factory=sec_client, log=lambda m: None)
        check(r2["action"] == "already" and len(await sched_rows("2001-01-03")) == 1 and len(SEC_LOG) == n_sec,
              "a second tick the same day writes nothing and makes no request (re-read: still one row)", r2["action"])
        # runs table shows it
        from services import edgar_bulk_queue as q
        listed = await q.list_runs(conn, page=1, page_size=100)
        mine = [r for r in listed["rows"] if r["id"] == str(row.get("id"))]
        check(len(mine) == 1 and mine[0]["trigger_source"] == "schedule" and mine[0]["actions"]["requeue"] is False
              and mine[0]["actions"]["stop"] is False,
              "the Bulk download runs list shows the row (trigger_source 'schedule', no Stop / Requeue)", f"{mine[:1]}")
        # failure: Thursday, a client whose every request answers 400
        pinned["d"] = date(2001, 1, 3)
        r3 = await sch.maybe_refresh_index(conn, now_utc=datetime(2001, 1, 4, 11, 0, tzinfo=timezone.utc),
                                           client_factory=failing_client, log=lambda m: None)
        rows3 = await sched_rows("2001-01-04")
        check(r3["action"] == "failed" and len(rows3) == 1 and rows3[0]["status"] == "failed"
              and bool(rows3[0]["error"]) and rows3[0]["finished_at"] is not None,
              "a mocked failure is recorded on the run row (status failed, error, finished; re-read)",
              f"{r3.get('action')}: {(rows3[0]['error'] if rows3 else '')[:80]}")
        r3b = await sch.maybe_refresh_index(conn, now_utc=datetime(2001, 1, 4, 12, 0, tzinfo=timezone.utc),
                                            client_factory=sec_client, log=lambda m: None)
        pinned["d"] = date(2001, 1, 4)
        r4 = await sch.maybe_refresh_index(conn, now_utc=datetime(2001, 1, 5, 11, 0, tzinfo=timezone.utc),
                                           client_factory=sec_client, log=lambda m: None)
        rows4 = await sched_rows("2001-01-05")
        check(r3b["action"] == "already" and r4["action"] == "succeeded" and len(rows4) == 1
              and rows4[0]["status"] == "succeeded",
              "the failure holds only its own day; the NEXT weekday refreshes normally (re-read)",
              f"{r3b['action']}, {r4['action']}")
        real_max_after = await rls_val(conn, "SELECT max(filing_date) FROM portfolio.edgar_index_filings")
        check(real_max_after == real_max_before, "the real last indexed date did not move", f"{real_max_after}")
        tick_src = (API_DIR / "workflow_scheduler_tick.py").read_text(encoding="utf-8")
        sched_src = (API_DIR / "services" / "workflow_scheduler.py").read_text(encoding="utf-8")
        check("maybe_refresh_index(conn)" in tick_src and "maybe_refresh_index" not in sched_src,
              "the step runs in the cron entrypoint, NOT inside run_scheduler_tick (scheduler verifies stay offline)")
    finally:
        eb.last_indexed_date = real_last
        os.environ.pop(sch.AUTO_REFRESH_ENV, None)


PRE_BULKFETCHUI3_REF = "99dc9e1^"   # the commit before the bulkfetchui3 classifier change


def _pre_bulkfetchui3_pipeline():
    """services/edgar_pipeline.py as it was before bulkfetchui3, loaded as its own module."""
    import importlib.util
    src = subprocess.run(["git", "show", f"{PRE_BULKFETCHUI3_REF}:apps/api/services/edgar_pipeline.py"],
                         cwd=str(API_DIR), capture_output=True, text=True, check=True).stdout
    path = pathlib.Path(tempfile.mkdtemp()) / "edgar_pipeline_pre_bulkfetchui3.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("edgar_pipeline_pre_bulkfetchui3", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


def y_classifier() -> None:
    section("[Y] Classifier: GS image-cover finals → pricing_supplement; existing cases unchanged; true others stay")
    from services import edgar_pipeline as p
    texts = {acc: (FIXTURE_TEXTS / f"{acc}.txt").read_text(encoding="utf-8") for acc in GS_SAMPLES + TRUE_OTHER_SAMPLES
             if (FIXTURE_TEXTS / f"{acc}.txt").exists()}
    check(len(texts) == len(GS_SAMPLES) + len(TRUE_OTHER_SAMPLES), "all 18 stored-text fixtures are present",
          f"{len(texts)}")
    k, reason = p.classify_document(texts.get(GS_FINAL, ""))
    check(k == "pricing_supplement" and "26,690" in reason,
          f"{GS_FINAL} (GS image cover, 'Pricing Supplement No. 26,690') → pricing_supplement", reason)
    gs = {a: p.classify_document(texts.get(a, ""))[0] for a in GS_SAMPLES}
    check(all(v == "pricing_supplement" for v in gs.values()), "every GS sample (10) → pricing_supplement", f"{gs}")
    oth = {a: p.classify_document(texts.get(a, ""))[0] for a in TRUE_OTHER_SAMPLES}
    check(all(v == "other" for v in oth.values()), "every true-'other' sample (8, five banks + ABS/FWPs) stays 'other'",
          f"{oth}")
    payoff = " barrier buffer autocall contingent coupon participation rate underlying "
    cases = {   # verify_edgarpipelinea's own cases, unchanged
        "pricing_supplement": "Filed pursuant to Rule 424(b)(2) Pricing Supplement No. 1,234 dated May 1, 2026 "
                              "(To Product Supplement No. 4-I)",
        "preliminary_pricing_supplement": "Subject to Completion. Preliminary Pricing Supplement dated May 1, 2026",
        "product_supplement": "Product Supplement No. 4-I To prospectus dated April 13, 2023 ... the applicable pricing supplement",
        "underlying_supplement": "Underlying Supplement No. 1-I dated April 13, 2023",
        "term_sheet": "Term sheet To prospectus dated April 13, 2023",
        "other": "Prospectus Supplement to Prospectus dated April 13, 2023 Senior Medium-Term Notes",
    }
    got = {k2: p.classify_document(v + payoff)[0] for k2, v in cases.items()}
    check(got == {k2: k2 for k2 in cases}, "every existing classifier case keeps its kind", f"{got}")
    filler = " lorem" * 900   # pushes the cover line past the 4,000-character opening
    never = {
        "product_supplement": "Product Supplement No. 4-I To prospectus" + filler + " Pricing Supplement No. 1 dated May 1, 2026",
        "underlying_supplement": "Underlying Supplement No. 1-I" + filler + " Pricing Supplement No. 1 dated May 1, 2026",
        "term_sheet": "Term sheet" + filler + " Pricing Supplement No. 1 dated May 1, 2026",
    }
    got2 = {k2: p.classify_document(v)[0] for k2, v in never.items()}
    check(got2 == {k2: k2 for k2 in never},
          "the cover-line rule never turns a product / underlying / term sheet into a final", f"{got2}")
    # A real GS preliminary (0000950170-25-001364): the original classifier (the commit before
    # bulkfetchui3, pinned so a later merge cannot move it) and this one must agree on it.
    old_p = _pre_bulkfetchui3_pipeline()
    prelim_text = (FIXTURE_TEXTS / f"{GS_PRELIM}.txt").read_text(encoding="utf-8")
    k_old, k_new = old_p.classify_document(prelim_text)[0], p.classify_document(prelim_text)[0]
    check(k_old == k_new == "preliminary_pricing_supplement",
          f"{GS_PRELIM} (real GS preliminary): original and new classifier agree → preliminary_pricing_supplement",
          f"old {k_old} / new {k_new}")
    # Image-cover preliminaries: no title phrase in the opening, a numbered dated cover line,
    # and "Subject to Completion" past the opening (before or after the cover line).
    image_prelims = {
        "marker before cover line": "GS Finance Corp. notes" + filler
            + " Subject to Completion. Dated May 1, 2026. Pricing Supplement No. 1,234 dated May 1, 2026",
        "marker after cover line": "GS Finance Corp. notes" + filler
            + " Pricing Supplement No. 1,234 dated May 1, 2026 ... Subject to Completion",
    }
    got3 = {k2: (p.classify_document(v)[0], p.decide_status(v)[0]) for k2, v in image_prelims.items()}
    check(all(k3 != "pricing_supplement" and st != "ready_for_extraction" for k3, st in got3.values()),
          "an image-cover preliminary ('Subject to Completion' in the text, no title phrase) never becomes a final",
          f"{got3}")
    far = "GS Finance Corp. notes" + " lorem" * 2500 + " Pricing Supplement No. 1 dated May 1, 2026"
    check(p.classify_document(far)[0] == "other", "a cover line beyond 12,000 characters does not count")


async def y_reclassify(conn, url) -> None:
    section("[Y] Reclassify: dry run writes nothing; --apply moves exactly the expected rows; no-text untouched; "
            "second --apply no-op; no HTTP client")
    from services import edgar_bulk as eb
    from services import edgar_pipeline as p
    texts = {acc: (FIXTURE_TEXTS / f"{acc}.txt").read_text(encoding="utf-8")
             for acc in (GS_FINAL, GS_PROSPECTUS_FINAL, *TRUE_OTHER_SAMPLES[:3])}
    (cik,) = await make_group(conn, G_RECL)
    d = date(2026, 9, 29)
    reason0 = "other: no title phrase in the opening"
    a_gs = await make_filing(conn, cik, d, status="not_pricing_supplement", kind="other", reason=reason0,
                             stored="r2", text=texts[GS_FINAL])
    a_pros = await make_filing(conn, cik, d, status="not_pricing_supplement", kind="other", reason=reason0,
                               stored="r2", text=texts[GS_PROSPECTUS_FINAL])
    a_oth = [await make_filing(conn, cik, d, status="not_pricing_supplement", kind="other", reason=reason0,
                               stored="r2", text=texts[a]) for a in TRUE_OTHER_SAMPLES[:3]]
    a_notext = await make_filing(conn, cik, d, status="not_pricing_supplement", kind="other", reason=reason0)
    a_prelim = await make_filing(conn, cik, d, status="not_pricing_supplement",
                                 kind="preliminary_pricing_supplement", reason="peek: preliminary", stored="r2",
                                 text=texts[GS_FINAL])     # not 'other': must be untouched even with final text
    mine = [a_gs, a_pros, *a_oth, a_notext, a_prelim]
    snap_q = """SELECT accession_number, pipeline_status, document_kind, status_reason, selection_policy_version
                  FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[]) ORDER BY 1"""
    downloads: list[str] = []

    def dl(key, bucket=None):
        downloads.append(key)
        return R2[key]

    before = await fresh_rows(url, snap_q, mine)
    HTTP_CLIENTS.clear()
    WATCH_CLIENTS[0] = True
    try:
        dry = await eb.reclassify_other(conn, bucket=BUCKET, downloader=dl, accessions=mine, out=lambda m: None)
        after_dry = await fresh_rows(url, snap_q, mine)
        check(after_dry == before and dry["applied"] is False and dry["changed"] == 2,
              "the DRY RUN (default) writes nothing (before/after equal) and reports 2 changes",
              f"changed {dry['changed']}")
        check(dry["no_text"] == [a_notext] and dry["unchanged"] == 3 and len(dry["examples"]) == 2
              and any("other→pricing_supplement" in k for k in dry["moves"]),
              "dry run: counts by issuer and old→new kind, the examples, and the no-text row listed", f"{dry['moves']}")
        res = await eb.reclassify_other(conn, bucket=BUCKET, downloader=dl, accessions=mine, apply=True,
                                        out=lambda m: None)
        after = {r["accession_number"]: r for r in await fresh_rows(url, snap_q, mine)}
        prev = {r["accession_number"]: r for r in before}
        moved = sorted(a for a in mine if after[a] != prev[a])
        check(res["changed"] == 2 and moved == sorted([a_gs, a_pros]),
              "--apply moves EXACTLY the two GS finals (re-read from an independent connection)", f"{moved}")
        policy = await p.active_policy(conn)
        for a, key in ((a_gs, GS_FINAL), (a_pros, GS_PROSPECTUS_FINAL)):
            exp_status, _, exp_kind = p.decide_status(texts[key])
            r = after[a]
            check(r["document_kind"] == "pricing_supplement" and r["pipeline_status"] == exp_status
                  and exp_status in ("ready_for_extraction", "prefilter_skipped")
                  and (r["status_reason"] or "").startswith("reclassified:other→pricing_supplement")
                  and r["selection_policy_version"] == policy["version"],
                  f"{key}: kind pricing_supplement, status = the normal decision ({exp_status}), "
                  f"status_reason 'reclassified:other→pricing_supplement', active policy v{policy['version']}",
                  f"{r['pipeline_status']} / {(r['status_reason'] or '')[:60]}")
        check(all(after[a] == prev[a] for a in a_oth), "the true-'other' rows are untouched")
        check(after[a_notext] == prev[a_notext], "the row without stored text is untouched")
        check(after[a_prelim] == prev[a_prelim], "a non-'other' row (preliminary) is untouched even with final text")
        res2 = await eb.reclassify_other(conn, bucket=BUCKET, downloader=dl, accessions=mine, apply=True,
                                         out=lambda m: None)
        after2 = {r["accession_number"]: r for r in await fresh_rows(url, snap_q, mine)}
        check(res2["changed"] == 0 and after2 == after, "a second --apply changes nothing (re-read)",
              f"changed {res2['changed']}")
        check(HTTP_CLIENTS == [], "reclassify never constructed an HTTP client", f"{HTTP_CLIENTS}")
        check(bool(downloads) and all(k.startswith(R2_PREFIX) for k in downloads),
              "it read stored text from (mocked) R2 only — fixture keys", f"{len(downloads)} reads")
        import inspect
        sig = inspect.signature(eb.reclassify_other)
        cli = (API_DIR / "scripts" / "run_bulk_fetch.py").read_text(encoding="utf-8")
        body = re.search(r"async def _reclassify_other\(.*?\n(?=async def |def )", cli, re.S)
        check(not any("client" in n for n in sig.parameters) and body is not None and "httpx" not in body.group(0)
              and "--reclassify-other" in cli and "return await _reclassify_other(url, args)" in cli,
              "the service function has no HTTP client parameter and the CLI path creates none")
    finally:
        WATCH_CLIENTS[0] = False


async def y_zero_byte(conn, url) -> None:
    section("[Y] Zero-byte download: recorded fetch_failed AND retryable (attempt count + next_attempt_at)")
    from services import edgar_bulk as eb
    from services import edgar_pipeline as p
    (cik,) = await make_group(conn, G_ZERO)
    d = date(2026, 9, 15)
    acc = await make_filing(conn, cik, d, status="selected")
    DOCS[nodash(acc)] = {"doc": "doc.htm", "body": b"", "form": "424B2"}
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)
    try:
        opts = eb.RunOptions(banks=[G_ZERO], date_from=d, date_to=d, kinds=["final"], concurrency=1, max_rps=8,
                             peek=False, bucket=BUCKET, r2_prefix=R2_PREFIX, trigger_source="verify")
        runner = eb.Runner(pool, opts, client=sec_client(), out=lambda m: None, backoff_base=0.001)
        stats = await runner.run()
    finally:
        await pool.close()
    rows = await fresh_rows(url, """SELECT pipeline_status, status_reason, attempt_count, next_attempt_at,
                                           next_attempt_at > now() AS future
                                      FROM portfolio.edgar_index_filings WHERE accession_number = $1""", acc)
    r = rows[0] if rows else {}
    check(stats.failed == 1 and r.get("pipeline_status") == "fetch_failed"
          and "zero bytes" in (r.get("status_reason") or "") and r.get("attempt_count") == 1,
          "a mocked zero-byte document is recorded fetch_failed, attempt_count 1 (re-read)",
          f"{r.get('pipeline_status')} {(r.get('status_reason') or '')[:70]}")
    check(r.get("next_attempt_at") is not None and r.get("future") is True,
          "…with next_attempt_at set in the future (retryable), not NULL", f"{r.get('next_attempt_at')}")
    await rls_exec(conn, "UPDATE portfolio.edgar_index_filings SET next_attempt_at = now() - interval '1 minute' "
                         "WHERE accession_number = $1", acc)
    source = await eb.load_source(conn, eb.DEFAULT_SOURCE)
    insts = await eb.resolve_institutions(conn, source, [G_ZERO])
    counts = await eb.scope_counts(conn, eb.Scope(source, insts, d, d))
    check(counts[G_ZERO]["total"] == 1, "once due, the next run's scope takes it again (no --retry-failed needed)",
          f"{counts[G_ZERO]}")
    check(issubclass(p.EmptyDocumentError, p.EdgarPipelineError), "EmptyDocumentError is an EdgarPipelineError (per filing, not systemic)")


def y_regression_runner() -> None:
    section("[Y] scripts/run_regressions.sh: syntax OK; stub mode prints one line per script, exits non-zero on a failure")
    proc = subprocess.run(["bash", "-n", str(RUNNER_SH)], capture_output=True, text=True, timeout=60)
    check(proc.returncode == 0, "bash -n scripts/run_regressions.sh passes", proc.stderr[-200:])
    listed = [ln.split("#")[0].strip() for ln in REGRESSION_SET.read_text(encoding="utf-8").splitlines()]
    listed = [x for x in listed if x]
    seed_list = ["verify_authz1.py", "verify_usermgmt1.py", "verify_navregroup.py", "verify_bugfix1.py",
                 "verify_dealroom1.py", "verify_dealroom2.py", "verify_bulkfetch.py", "verify_bulkfetchui.py",
                 "verify_bulkfetchui2.py", "verify_bulkfetchui3.py", "verify_edgarpipelinea.py"]
    check(listed == seed_list, "regression_set.txt is seeded with the 11 scripts", f"{listed}")
    src = RUNNER_SH.read_text(encoding="utf-8")
    check("doppler run -p hollisworks -c prd -- " in src and "apps/api/venv/bin/python -u" in src
          and "VERIFY_NESTED=1" in src and "${REGRESSION_TIMEOUT:-1200}" in src
          and "sprint_prompts/logs/regressions" in src and "edgar_fetch_workers" in src,
          "the real command, VERIFY_NESTED=1, the 1200 s timeout, the log path and the worker check are in the script")
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        (t / "verify_stub_ok.py").write_text('print("TOTAL: 3 PASS, 0 FAIL")\n')
        (t / "verify_stub_bad.py").write_text('print("TOTAL: 2 PASS, 1 FAIL")\nimport sys; sys.exit(1)\n')
        (t / "list.txt").write_text("# stub\nverify_stub_ok.py\nverify_stub_bad.py  # fails\n")
        env = os.environ | {"REGRESSION_SET": str(t / "list.txt"), "REGRESSION_SCRIPTS_DIR": str(t),
                            "REGRESSION_RUNNER": f"{sys.executable} -u", "REGRESSION_LOG_ROOT": str(t / "logs"),
                            "REGRESSION_TIMEOUT": "60", "REGRESSION_WORKER_CHECK": "echo 0"}
        p1 = subprocess.run(["bash", str(RUNNER_SH)], capture_output=True, text=True, timeout=300, env=env)
        out = p1.stdout
        check(re.search(r"^verify_stub_ok\.py\s+PASS 3\s+FAIL 0\s+\d+s$", out, re.M) is not None
              and re.search(r"^verify_stub_bad\.py\s+PASS 2\s+FAIL 1\s+\d+s$", out, re.M) is not None
              and "SUMMARY: 2 script(s)" in out and p1.returncode != 0,
              "stub mode: one line per script, a summary, non-zero exit on the failing one", out[-250:])
        p2 = subprocess.run(["bash", str(RUNNER_SH)], capture_output=True, text=True, timeout=120,
                            env=env | {"REGRESSION_WORKER_CHECK": "echo 1"})
        check(p2.returncode == 2 and "heartbeating" in p2.stderr and "PASS" not in p2.stdout,
              "a heartbeating laptop worker: it warns and stops before running anything", p2.stderr[-150:])


NEW_BULLET = '''  - Regression: NOT part of a normal sprint's verify (Joe, 2026-10-22).
    Every verify instead runs the cheap guards: its own checks plus
    `authz1_routes.py --check` (every route gated, no gate changed).
    EXCEPTION: a sprint that changes something shared — auth/authz,
    RLS, a table or service other features read — re-runs the ONE
    most directly affected prior verify, EXACTLY ONCE, flat, after
    teardown, with VERIFY_NESTED=1 and a 1200 s timeout. Name that
    script in the prompt; never "every prior script".
    The full suite runs separately, on the operator's schedule
    (weekly / before a big push): scripts/run_regressions.sh, every
    verify_*.py one after another, one summary line each. Failures
    go to the backlog.
'''
APPENDED = (" Since 2026-10-22 regressions are out of per-sprint verifies by default (see REAL PROOF in the "
            "skeleton); VERIFY_NESTED=1 stays the guard for the one allowed exception.")
NESTING_TAIL = ("The durable fix is to state a predecessor's baseline as a CONFIRMED REAL FACT in the next prompt "
                "rather than instructing the sprint to re-run it.")


def y_standard() -> None:
    section("[Y] docs/SPRINT_WORKFLOW_STANDARD.md: the new regression bullet; the old text gone; the rest byte-identical")
    cur = STANDARD_DOC.read_text(encoding="utf-8")
    try:
        base = subprocess.run(["git", "show", "main:docs/SPRINT_WORKFLOW_STANDARD.md"], cwd=str(REPO),
                              capture_output=True, text=True, timeout=60, check=True).stdout
    except Exception as exc:  # noqa: BLE001
        check(False, "read the main copy of the standard", f"{type(exc).__name__}: {exc}")
        return
    check(NEW_BULLET in cur, "the new 'Regression: NOT part of a normal sprint's verify' bullet is present")
    check("re-run every PRIOR sprint's own verify script" not in cur, "the old 're-run every PRIOR sprint' text is gone")
    check(NESTING_TAIL + APPENDED in cur, "the Regression-chain nesting gotcha carries the appended sentence")
    start = base.find("  - Regression check: re-run every PRIOR sprint's own verify script\n")
    endmark = '    (see "regression-chain nesting" below).\n'
    end = base.find(endmark, start)
    if start < 0 or end < 0:
        check(False, "main's copy still has the old bullet to compare against")
        return
    expected = base[:start] + NEW_BULLET + base[end + len(endmark):]
    expected = expected.replace(NESTING_TAIL, NESTING_TAIL + APPENDED, 1)
    check(cur == expected, "the rest of the file is byte-identical to main (diff limited to the two edits)",
          f"{len(cur)} vs {len(expected)} chars")


def y_routes() -> None:
    section("[Y] Routes: no route added, no gate changed; authz1_routes.py --check CURRENT")
    proc = subprocess.run([sys.executable, str(HERE.parent / "authz1_routes.py"), "--check"], cwd=str(REPO),
                          capture_output=True, text=True, timeout=600)
    check(proc.returncode == 0 and "CURRENT" in proc.stdout, "authz1_routes.py --check says CURRENT",
          (proc.stdout + proc.stderr)[-200:])
    try:
        base = json.loads(subprocess.run(["git", "show", "main:docs/AUTHZ1_ROUTES.json"], cwd=str(REPO),
                                         capture_output=True, text=True, timeout=60, check=True).stdout)
        cur = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))
        key = lambda r: (r["path"], tuple(r["methods"]))  # noqa: E731
        b = {key(r): r["after"] for r in base["routes"]}
        c = {key(r): r["after"] for r in cur["routes"]}
        changed = {k: (b[k], c.get(k)) for k in b if c.get(k) != b[k]}
        added = sorted(set(c) - set(b))
        check(not changed, "no existing route's gate changed versus main", f"{changed}")
        check(not added, "no new route (the fresh read is ?fresh=1 on the same route)", f"{added}")
        inst = c.get(("/api/v1/admin/edgar/bulk/institutions", ("GET",)))
        check(inst == "super_admin", "GET /admin/edgar/bulk/institutions is still gated super_admin", f"{inst}")
    except Exception as exc:  # noqa: BLE001
        check(False, "compared AUTHZ1_ROUTES.json with main", f"{type(exc).__name__}: {exc}")


def y_no_model() -> None:
    section("[Y] No AI model calls; every SEC request was mocked")
    check(MODEL_CALLS == [], "the mocked model clients recorded no call", f"{MODEL_CALLS}")
    check(all("sec.gov" in e["url"] for e in SEC_LOG), "every SEC request went to the mock", f"{len(SEC_LOG)}")


def y_status_doc() -> None:
    section("[Y] PROJECT_STATUS records the changes, the operator steps and the exact commands")
    text = STATUS_DOC.read_text(encoding="utf-8")
    m = re.search(r"^## 0+\. BULKFETCH UI 3 — .*$", text, re.M)
    check(m is not None, "docs/PROJECT_STATUS.md has the BULKFETCH UI 3 entry", m.group(0)[:80] if m else "no match")
    for needle in ("--reclassify-other", "--reclassify-other --apply", "EDGAR_INDEX_AUTO_REFRESH",
                   "2ndactcapital-workflow-scheduler", "scripts/run_regressions.sh", "?fresh=1",
                   "Downloaded", "Stored"):
        check(needle in text, f"PROJECT_STATUS covers {needle}")


# ═══ Regression ════════════════════════════════════════════════════════════
REGRESSION = "verify_bulkfetch.py"


async def real_workers_live(conn) -> list:
    return [dict(r) for r in await rls_fetch(
        conn, """SELECT worker_id, target, host FROM portfolio.edgar_fetch_workers
                  WHERE stopped_at IS NULL AND target = 'laptop'
                    AND heartbeat_at >= now() - interval '120 seconds'""")]


def y_regression() -> None:
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
        return
    section(f"[Y] Regression: {REGRESSION} — ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 1200 s timeout")
    log_dir = REPO / "sprint_prompts" / "logs"
    try:
        proc = subprocess.run([sys.executable, "-u", str(HERE.parent / REGRESSION)], capture_output=True, text=True,
                              timeout=1200, cwd=str(REPO), env=os.environ | {"VERIFY_NESTED": "1"})
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        out = f"TIMEOUT after 1200s: {(exc.stdout or b'')[-300:]!r}"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"verify_bulkfetchui3.regression.{REGRESSION.removesuffix('.py')}.out").write_text(out)
    except OSError:
        pass
    totals = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    m = totals[-1] if totals else None
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(m is not None and int(m[0]) > 0 and int(m[1]) == 0, f"{REGRESSION} still reports 0 FAIL",
          (f"TOTAL: {m[0]} PASS, {m[1]} FAIL" if m else out[-300:]) + (f"; {fails[:3]}" if fails else ""))


# ═══ Main ══════════════════════════════════════════════════════════════════
async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = url
    conn = await asyncpg.connect(url, statement_cache_size=0)
    live = await real_workers_live(conn)
    if live:
        await conn.close()
        print(f"REFUSING TO START: a laptop bulk worker is heartbeating ({live[0]['worker_id']}) — stop it "
              f"(tmux attach -t bulkworker, Ctrl-C) and re-run.")
        return 2
    install_mocks()
    from services import edgar_bulk_queue as q
    pre_counts = None
    real_before = None

    async def run(name, fn, *a):
        try:
            res = fn(*a)
            if asyncio.iscoroutine(res):
                res = await res
            return res
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")
            return None

    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the verify connection's role does NOT bypass RLS (app_service)",
              f"{await conn.fetchval('SELECT current_user')}")
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "start-of-run teardown", f"{type(exc).__name__}: {exc}")
        left0 = await fixture_counts(conn)
        check(sum(left0.values()) == 0, "no fixture rows before the run", json.dumps(left0))
        pre_counts = await table_counts(conn)
        real_before = await real_filings_checksum(conn)
        check(real_before[0] > 0, "the three real banks' filings are present for the checksum", f"{real_before[0]}")

        await run("task 1", y_task1)
        await run("seed", seed, conn)
        await run("downloaded / stored", y_downloaded_stored, conn, url, q)
        await run("gate", y_gate, conn)
        await run("scheduled refresh", y_schedule, conn, url)
        await run("classifier", y_classifier)
        await run("reclassify", y_reclassify, conn, url)
        await run("zero-byte", y_zero_byte, conn, url)
        await run("regression runner", y_regression_runner)
        await run("standard", y_standard)
        await run("routes", y_routes)
        await run("front end", y_frontend)
        await run("status doc", y_status_doc)
        y_no_model()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        section("[Y] Teardown: zero fixture rows; every touched table at its pre-test count; real filings unchanged")
        teardown_clean = False
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            teardown_clean = sum(left.values()) == 0
            check(teardown_clean, "zero fixture rows remain", json.dumps(left))
            if pre_counts is not None:
                post = await table_counts(conn)
                diff = {t: (pre_counts[t], post[t]) for t in COUNTED if pre_counts[t] != post[t]}
                check(not diff, "every touched table is back at its exact pre-test count", json.dumps(diff))
                teardown_clean = teardown_clean and not diff
            else:
                check(False, "pre-test counts were taken")
                teardown_clean = False
            real_after = await real_filings_checksum(conn)
            check(real_before is not None and real_after == real_before,
                  "real Wells Fargo / Goldman Sachs / Jefferies filings unchanged (count + checksum of status, kind)",
                  f"{real_before} -> {real_after}")
            if await real_workers_live(conn):
                check(False, "regression skipped: a laptop worker started heartbeating mid-run")
                teardown_clean = False
        except Exception as exc:  # noqa: BLE001
            check(False, "end-of-run teardown", f"{type(exc).__name__}: {exc}")
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        await conn.close()
        if teardown_clean:
            y_regression()
        else:
            check(False, "regression skipped: this run's own teardown did not leave zero fixture rows")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
