"""verify_bulkfetchui.py — BULKFETCH UI: the "Bulk download" tab, queued runs, the
worker mode of run_bulk_fetch.py.

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_bulkfetchui.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on
any failure. Hydrates secrets from Doppler over HTTPS (_db_bootstrap).

BEFORE RUNNING: stop any real laptop worker (`tmux attach -t bulkworker`, Ctrl-C)
— one bulk run at a time is enforced across ALL workers, so a real running run
would (correctly) block the fixture workers. The script checks this first.

NO NETWORK. The SEC is an ``httpx.MockTransport`` (every request logged); R2 is
a dict behind ``services.storage`` (a real S3 client RAISES); the Render API is
a MockTransport behind ``edgar_bulk_queue.render_http`` that records every
method (only GETs are allowed); every model entry point (note_extraction.proxy
chat / jev, services.extraction call_claude_*) is a recorder that raises. Real
spend: $0. Workers run IN-PROCESS with --once semantics — no process is spawned
except the flat regressions, the node tests and `npm run build`.

FIXTURES — tagged, torn down at the START and at the END:
  * manifest / filer / reference_filings rows whose accession starts ACC_PREFIX
    (``-92-``: grep'd — no other verify uses it);
  * structured_note_issuers rows in issuer groups 'VERIFY BFUI …' with 9-digit
    CIKs starting 99092 (no real filer has such a CIK);
  * edgar_pipeline_runs rows requested by a fixture user, worked by a
    'verify-bfui:' worker, or whose params.banks name a fixture group; their
    'bulk_fetch:<id>' leases; edgar_fetch_workers rows 'verify-bfui:…';
  * a fixture org, a super-admin and an org-admin user (auth0_sub = the literal
    sub the mocked token carries; teardown finds them by id, sub and email).
Every id is a full UUID or a counter — never a slice of a UUID.

NEVER TOUCHED: real note_gold_values (count + checksum before/after), real
edgar_index_filings rows (every claim is scoped to fixture CIKs; the live
preview is read-only), real queued runs (every fixture worker is restricted to
the fixture run ids — ``Worker(restrict_to=…)``), Render (no service is read
for real, none created).

Teardown is FK child-before-parent from pg_constraint (contype::text), then every
touched table is re-read for its exact pre-test count. The regressions
(verify_bulkfetch.py with its own nesting switched off, verify_edgarpipelinea.py,
verify_edgarcohorts.py) run ONCE each as flat subprocesses, 600 s timeout, ONLY
after this script's teardown left zero fixture rows.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"
DISCOVERY_DOC = REPO / "docs" / "BULKFETCHUI_DISCOVERY.md"
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
MIGRATION = API_DIR / "migrations" / "bulkfetchui_queued_runs.sql"
MONITOR_JSX = WEB_DIR / "components" / "admin" / "EdgarPipelineMonitor.jsx"
BULK_JSX = WEB_DIR / "components" / "admin" / "EdgarBulkDownload.jsx"
BULK_LOGIC = WEB_DIR / "lib" / "edgarBulkDownload.mjs"

TAG = "bulkfetchui-verify"
ACC_PREFIX = "9999999999-92-"
GROUP_PREFIX = "VERIFY BFUI "
WORKER_PREFIX = "verify-bfui:"
ORG = UUID("99000000-0000-0000-0000-00000b0f2001")
U_SUPER = UUID("99000000-0000-0000-0000-00000b0f2011")
U_ORGADMIN = UUID("99000000-0000-0000-0000-00000b0f2012")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN]
FIXTURE_EMAIL_LIKE = "bulkfetchui_%@test.local"
HEADERS = {"Authorization": "Bearer verify-token"}
BASE = "/api/v1/admin/edgar/bulk"
REAL_GROUP = "JPMorgan"
REAL_YEAR = 2025
PREVIEW_LIMIT_SECONDS = 2.0

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
    return f"bulkfetchui_{uid.hex}"


# ═══ RLS-scoped DB helpers (every read sets the platform RLS context) ═══════
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
        "WHERE conrelid = $1::regclass AND contype::text = 'c'", table)
    return {r["conname"]: r["def"] for r in rows}


def allowed(defs: dict[str, str], column: str) -> set[str]:
    out: set[str] = set()
    for d in defs.values():
        if re.search(rf"\(\(?{column}\b", d) and "ARRAY[" in d:
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
    return f"99092{_cik_n:04d}"


def nodash(acc: str) -> str:
    return acc.replace("-", "")


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.edgar_fetch_workers", "portfolio.edgar_pipeline_lease", "portfolio.reference_filing_supplements",
    "portfolio.edgar_index_filing_filers", "portfolio.edgar_index_filings", "portfolio.reference_filings",
    "portfolio.edgar_pipeline_runs", "portfolio.structured_note_issuers",
)
COUNTED = TOUCHED + ("public.users", "public.organizations", "public.roles", "public.user_roles",
                     "portfolio.edgar_selection_policies", "portfolio.note_gold_values")

_RUN_FIXTURE_SQL = """
    SELECT id FROM portfolio.edgar_pipeline_runs r
     WHERE r.requested_by = ANY($1::uuid[])
        OR r.worker_id LIKE $2
        OR (jsonb_typeof(r.params->'banks') = 'array'
            AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.params->'banks') b WHERE b LIKE $3))
        OR (jsonb_typeof(r.details->'bulk'->'institutions') = 'array'
            AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.details->'bulk'->'institutions') b WHERE b LIKE $3))
"""


async def fixture_keys(conn) -> dict:
    filings = await rls_fetch(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                              ACC_PREFIX + "%")
    runs = await rls_fetch(conn, _RUN_FIXTURE_SQL, FIXTURE_USERS, WORKER_PREFIX + "%", GROUP_PREFIX + "%")
    return {"filings": [r["id"] for r in filings], "runs": [r["id"] for r in runs]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    f, runs = k["filings"], k["runs"]
    return {
        "portfolio.edgar_fetch_workers": ("worker_id LIKE $1 OR current_run_id = ANY($2::uuid[])",
                                          [WORKER_PREFIX + "%", runs]),
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


async def gold_checksum(conn) -> tuple[int, str]:
    r = await rls_row(
        conn, """SELECT count(*) AS n,
                        md5(COALESCE(string_agg(id::text || field_key || COALESCE(value::text, '') || action
                                                || COALESCE(valid_to::text, '') || metadata::text, ',' ORDER BY id), '')) AS h
                   FROM portfolio.note_gold_values""")
    return int(r["n"]), r["h"]


# ═══ Mocks: SEC, R2, Render, models ════════════════════════════════════════
DOCS: dict[str, dict] = {}          # accession nodash -> {cik, doc, body, form}
INDEX_FILES: dict[str, str] = {}    # YYYYMMDD -> master.idx body
SEC_LOG: list[dict] = []
SEC_DELAY = [0.0]                   # seconds per mocked SEC response (a slow SEC for the stop / overlap tests)
R2: dict[str, bytes] = {}
MODEL_CALLS: list[str] = []
RENDER_CALLS: list[tuple[str, str]] = []
RENDER_SERVICE: dict = {}           # what the mocked GET /v1/services/{id} returns (empty -> 404)
STAMPS: list[float] = []
RUNNER_CALLS: list[dict] = []
OUT: list[str] = []

_DOC_RE = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/(.+)$")
_DAILY_RE = re.compile(r"/Archives/edgar/daily-index/\d{4}/QTR\d/master\.(\d{8})\.idx$")


async def sec_handler(request: httpx.Request) -> httpx.Response:
    if SEC_DELAY[0]:
        await asyncio.sleep(SEC_DELAY[0])
    path = request.url.path
    rng = request.headers.get("Range")
    entry = {"t": time.monotonic(), "url": str(request.url), "range": rng}
    SEC_LOG.append(entry)
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
    entry["acc"] = m.group(2)
    if tail == "index.json":
        return httpx.Response(200, json={"directory": {"item": [{"name": d["doc"], "size": len(d["body"])}]}})
    if tail.endswith("-index-headers.html"):
        return httpx.Response(200, text=f"<TYPE>{d.get('form', '424B2')}\n<FILE-NUMBER>333-999999\n")
    if tail == d["doc"]:
        entry["doc_get"] = True
        body = d["body"]
        if rng:
            n = int(rng.split("-")[1]) + 1
            return httpx.Response(206, content=body[:n],
                                  headers={"Content-Range": f"bytes 0-{min(n, len(body)) - 1}/{len(body)}"})
        return httpx.Response(200, content=body)
    return httpx.Response(404)


def sec_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(sec_handler))


def render_handler(request: httpx.Request) -> httpx.Response:
    RENDER_CALLS.append((request.method, request.url.path))
    if request.method != "GET":
        return httpx.Response(405)
    m = re.search(r"/v1/services/([^/]+)$", request.url.path)
    if m and RENDER_SERVICE.get("id") == m.group(1):
        return httpx.Response(200, json=RENDER_SERVICE)
    return httpx.Response(404, json={"message": "not found"})


def install_mocks():
    from services import edgar_bulk, edgar_bulk_queue, edgar_fetch, extraction, storage
    from services.note_extraction import proxy

    def fake_upload(key, data, content_type=None, bucket=None):
        R2[key] = bytes(data)
        return key

    def fake_download(key, bucket=None):
        return R2[key]

    def no_s3(*_a, **_k):
        raise AssertionError("a real R2 client was requested during verify_bulkfetchui")

    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_s3_client = no_s3

    def recorder(name):
        async def _no(*_a, **_k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"{name} must never be called by bulkfetchui")
        return _no

    proxy.chat = recorder("proxy.chat")
    proxy.jev = recorder("proxy.jev")
    for fn in ("call_claude_json", "call_claude_text", "call_claude_with_tools"):
        setattr(extraction, fn, recorder(f"extraction.{fn}"))

    edgar_bulk_queue.render_http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(render_handler))

    orig_acquire = edgar_fetch.RateLimiter.acquire

    async def stamped(self):
        await orig_acquire(self)
        STAMPS.append(self._calls[-1])      # the limiter's OWN recorded time for this slot

    edgar_fetch.RateLimiter.acquire = stamped

    orig_run = edgar_bulk.Runner.run

    async def recorded_run(self):
        entry = {"run_id": self.opts.run_id, "banks": list(self.opts.banks), "fn": "Runner.run",
                 "t0": time.monotonic(), "t1": None}
        RUNNER_CALLS.append(entry)
        try:
            return await orig_run(self)
        finally:
            entry["t1"] = time.monotonic()      # one in-process clock: no app/DB clock skew

    edgar_bulk.Runner.run = recorded_run
    os.environ.setdefault("EDGAR_USER_AGENT", "Hollisworks verify_bulkfetchui (verify@test.local)")
    os.environ.pop(edgar_bulk_queue.RENDER_WORKER_ENV, None)   # "not set up" unless a section sets it


# ═══ Fixture builders ══════════════════════════════════════════════════════
COVER_FINAL = ("<html><body><p>PRICING SUPPLEMENT dated September 3, 2026</p>"
               "<p>Notes linked to the S&amp;P 500 Index. Barrier: 70% of the Initial Level.</p></body></html>")
ACTIVE_V: int | None = None
SEED_RUNS: list = []


async def make_group(conn, name: str, n_ciks: int = 1) -> list[str]:
    ciks = [next_cik() for _ in range(n_ciks)]
    async with conn.transaction():
        await _super(conn)
        for i, cik in enumerate(ciks):
            await conn.execute(
                """INSERT INTO portfolio.structured_note_issuers
                       (filer_cik, issuer_group, filer_name, filer_role, credit_entity, include_status, notes)
                   VALUES ($1, $2, $3, $4, $5, 'yes', $6)""",
                cik, GROUP_PREFIX + name, f"{GROUP_PREFIX}{name} Filer {i + 1}",
                "issuer" if i == 0 else "guarantor", f"{GROUP_PREFIX}{name} Credit", TAG)
    return ciks


async def make_filing(conn, cik: str, day: date, *, form: str = "424B2", status: str = "selected",
                      body: str | None = COVER_FINAL) -> str:
    acc = next_acc()
    async with conn.transaction():
        await _super(conn)
        await conn.execute(
            """INSERT INTO portfolio.edgar_index_filings
                   (accession_number, form_type, filing_date, index_quarter, submission_path, filer_count,
                    pipeline_status, selection_policy_version, primary_issuer_cik, status_reason)
               VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8, $9)""",
            acc, form, day, f"{day.year}Q{(day.month - 1) // 3 + 1}", f"edgar/data/{cik}/{acc}.txt", status,
            None if status in ("discovered", "fetched") else ACTIVE_V, cik, f"{TAG} fixture")
        await conn.execute(
            "INSERT INTO portfolio.edgar_index_filing_filers (accession_number, cik, company_name) VALUES ($1, $2, $3)",
            acc, cik, "VERIFY BFUI fixture filer")
    if body is not None:
        DOCS[nodash(acc)] = {"cik": cik, "doc": "doc.htm", "body": body.encode(), "form": form}
    return acc


async def statuses(conn, accs: list[str]) -> dict[str, dict]:
    rows = await rls_fetch(
        conn, """SELECT accession_number, pipeline_status, document_kind, reference_filing_id, claimed_by
                   FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])""", accs)
    return {r["accession_number"]: dict(r) for r in rows}


def full_gets(acc: str) -> int:
    n = nodash(acc)
    return sum(1 for e in SEC_LOG if e.get("acc") == n and e.get("doc_get") and not e["range"])


async def insert_queued(conn, banks: list[str], d_from: date, d_to: date, *, target: str = "laptop",
                        kinds=("final",), run_kind: str = "bulk_fetch", params: dict | None = None):
    """A queued run written directly (as the API would), tagged by requested_by."""
    p = params if params is not None else {
        "source": "structured_notes", "banks": banks, "from": d_from.isoformat(), "to": d_to.isoformat(),
        "kinds": list(kinds), "peek": True, "keep_preliminaries": False, "concurrency": 2, "max_rps": 8}
    rid = await rls_val(
        conn, """INSERT INTO portfolio.edgar_pipeline_runs
                     (trigger_source, requested_by, status, stages, fetch_cap, runtime_cap_seconds, run_kind,
                      target, params, queued_at)
                 VALUES ('verify', $1, 'queued', ARRAY['fetch'], 0, 3600, $2, $3, $4::jsonb, clock_timestamp())
                 RETURNING id""",
        U_SUPER, run_kind, target, json.dumps(p))
    return rid


async def run_row(url: str, rid) -> dict:
    """INDEPENDENT re-read: a fresh connection, RLS context set."""
    c = await asyncpg.connect(url, statement_cache_size=0)
    try:
        r = await rls_row(c, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", rid)
        return dict(r) if r else {}
    finally:
        await c.close()


def jd(v) -> dict:
    if v is None:
        return {}
    return json.loads(v) if isinstance(v, str) else dict(v)


def make_worker(pool, q, name: str, *, target: str = "laptop", restrict_to=None, **kw):
    kw.setdefault("poll_seconds", 0.05)
    kw.setdefault("heartbeat_seconds", 0.3)
    kw.setdefault("stop_poll_seconds", 0.05)
    rk = {"backoff_base": 0.001} | kw.pop("runner_kwargs", {})
    return q.Worker(pool, target=target, worker_id=f"{WORKER_PREFIX}{name}", host="verify-host",
                    client_factory=sec_client, out=OUT.append, restrict_to=restrict_to, runner_kwargs=rk, **kw)


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


def detail(body) -> dict:
    d = (body or {}).get("detail")
    return d if isinstance(d, dict) else {"message": d}


async def seed_users(conn) -> None:
    from services.rbac import grant_org_admin
    roles_ok = allowed(await check_defs(conn, "users"), "role")
    check(not roles_ok or {"super_admin", "member"} <= roles_ok, "fixture users.role values satisfy the live CHECK",
          f"{sorted(roles_ok)}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "BULKFETCHUI Verify Fixture Org", f"{ORG}-bulkfetchui")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""", uid, ORG, f"{sub}@test.local", sub, sub, role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)


# ═══ Sections ══════════════════════════════════════════════════════════════
D0 = date(2026, 9, 1)


def y_task1() -> None:
    section("[Y] Task 1 — the five discovery findings are reported")
    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    heads = re.findall(r"^## (1[a-e])\.", text, re.M)
    check(heads == [f"1{c}" for c in "abcde"], "docs/BULKFETCHUI_DISCOVERY.md reports 1a..1e in order", f"{heads}")
    for label, pat in (
        ("1a names the reused functions (plan_scope, scope_counts, estimate, Runner)",
         r"\*\*this sprint factors out `plan_scope\(\.\.\.\)`\*\*"),
        ("1b names the run table and its CHECK values", r"\*\*The bulk Runner already writes `portfolio\.edgar_pipeline_runs`\*\*"),
        ("1c states the refresh cost", r"\*\*12 weekday\s+index files = 12 SEC requests\*\*"),
        ("1d names the API gate", r"`_require_super_admin\(request\)`"),
        ("1e names the read-only Render call", r"`GET https://api\.render\.com/v1/services/\{serviceId\}`"),
    ):
        m = re.search(pat, text)
        check(m is not None, label, m.group(0) if m else "no match")


async def y_schema(conn) -> None:
    section("[Y] Schema — new columns/values live and declared in the migration; old values still accepted; RLS on")
    mig = MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists() else ""
    cols = {r["column_name"]: r for r in await conn.fetch(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'portfolio' AND table_name = 'edgar_pipeline_runs'")}
    new_cols = ("target", "params", "queued_at", "worker_id", "worker_host", "heartbeat_at", "stop_requested",
                "progress")
    missing = [c for c in new_cols if c not in cols]
    undeclared = [c for c in new_cols if not re.search(rf"ADD COLUMN IF NOT EXISTS {c}\b", mig)]
    check(not missing and not undeclared, "every new run column is live AND declared in the migration",
          f"missing live {missing}; undeclared {undeclared}")
    defs = await check_defs(conn, "portfolio.edgar_pipeline_runs")
    st = allowed(defs, "status")
    # Read run_kind / target from their own named CHECKs: matching by column
    # name also picks up edgar_pipeline_runs_queued_target_check, whose
    # run_kind values would pollute the target set.
    kinds = set(re.findall(r"'([^']+)'::text", defs.get("edgar_pipeline_runs_run_kind_check", "")))
    tg = set(re.findall(r"'([^']+)'::text", defs.get("edgar_pipeline_runs_target_check", "")))
    old_st = {"launching", "launched", "launch_failed", "refused", "running", "succeeded", "failed"}
    new_st = {"queued", "stopped", "cancelled"}
    check(old_st <= st and new_st <= st, "status CHECK keeps every old value and adds queued/stopped/cancelled",
          f"{sorted(st)}")
    check({"fetch", "extract", "bulk_fetch", "refresh_index"} <= kinds and tg == {"laptop", "render"},
          "run_kind adds bulk_fetch/refresh_index; target is laptop|render", f"{sorted(kinds)} / {sorted(tg)}")
    declared = all(f"'{v}'::text" in mig for v in new_st | {"bulk_fetch", "refresh_index"})
    check(declared, "the new CHECK values are declared in the migration file")
    accepted = []
    for s in sorted(old_st):
        try:
            async with conn.transaction():
                await _super(conn)
                await conn.execute(
                    """INSERT INTO portfolio.edgar_pipeline_runs (trigger_source, status, stages, fetch_cap,
                           runtime_cap_seconds, run_kind) VALUES ('verify', $1, ARRAY['fetch'], 0, 60, 'fetch')""", s)
                accepted.append(s)
                raise _Rollback()
        except _Rollback:
            pass
        except asyncpg.PostgresError as exc:
            print(f"    status {s!r} refused: {exc}")
    check(set(accepted) == old_st, "an insert with each pre-existing status is still accepted (rolled back)",
          f"{sorted(accepted)}")
    try:
        async with conn.transaction():
            await _super(conn)
            await conn.execute(
                """INSERT INTO portfolio.edgar_pipeline_runs (trigger_source, status, stages, fetch_cap,
                       runtime_cap_seconds, run_kind) VALUES ('verify', 'queued', ARRAY['fetch'], 0, 60, 'bulk_fetch')""")
            raise _Rollback()
        check(False, "a queued bulk_fetch row without a target is refused")
    except _Rollback:
        check(False, "a queued bulk_fetch row without a target is refused", "it was accepted")
    except asyncpg.CheckViolationError as exc:
        check(True, "a queued bulk_fetch row without a target is refused", str(exc)[:100])
    rls = {r["relname"]: r["relrowsecurity"] for r in await conn.fetch(
        "SELECT relname, relrowsecurity FROM pg_class WHERE oid IN "
        "('portfolio.edgar_pipeline_runs'::regclass, 'portfolio.edgar_fetch_workers'::regclass)")}
    check(rls == {"edgar_pipeline_runs": True, "edgar_fetch_workers": True}, "RLS is on for both tables", f"{rls}")
    pol = await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = 'edgar_fetch_workers'")
    check({r["cmd"] for r in pol} == {"SELECT", "INSERT", "UPDATE", "DELETE"},
          "edgar_fetch_workers has a policy for every operation", f"{sorted(r['cmd'] for r in pol)}")
    idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' "
                              "AND indexname = 'edgar_pipeline_runs_queued_idx'")
    check(bool(idx) and "status = 'queued'" in idx and "target" in idx,
          "a partial index serves the queued-run claim by target", f"{idx}")


class _Rollback(Exception):
    pass


async def y_institutions(conn, q) -> dict:
    section("[Y] Institutions endpoint — fixture groups with their filer entities; per-year counts equal SQL")
    a = await make_group(conn, "Alpha", 2)
    b = await make_group(conn, "Beta", 1)
    plan = {(a[0], date(2025, 3, 3)): 3, (a[0], date(2026, 9, 2)): 2, (a[1], date(2025, 6, 2)): 1,
            (b[0], date(2026, 9, 3)): 4}
    for (cik, day), n in plan.items():
        for _ in range(n):
            await make_filing(conn, cik, day)
    # never counted: wrong status, wrong form
    await make_filing(conn, a[0], date(2025, 3, 3), status="not_selected", body=None)
    await make_filing(conn, a[0], date(2025, 3, 3), form="FWP", body=None)
    q._INSTITUTIONS_CACHE.clear()
    code, body = await api(U_SUPER, "GET", f"{BASE}/institutions")
    rows = {r["name"]: r for r in (body or {}).get("rows", [])}
    sql = await rls_fetch(
        conn, """SELECT primary_issuer_cik AS cik, extract(year FROM filing_date)::int AS y, count(*)::int AS n
                   FROM portfolio.edgar_index_filings
                  WHERE primary_issuer_cik = ANY($1::text[]) AND pipeline_status = 'selected' AND form_type = '424B2'
                  GROUP BY 1, 2""", a + b)
    want = {(r["cik"], str(r["y"])): r["n"] for r in sql}
    got = {}
    for g in (GROUP_PREFIX + "Alpha", GROUP_PREFIX + "Beta"):
        for f in rows.get(g, {}).get("filers", []):
            for y, n in f["years"].items():
                got[(f["cik"], y)] = n
    created = {(c, str(d.year)): 0 for (c, d) in plan}
    for (c, d), n in plan.items():
        created[(c, str(d.year))] += n
    check(code == 200 and GROUP_PREFIX + "Alpha" in rows and GROUP_PREFIX + "Beta" in rows,
          "GET institutions lists each fixture group", f"HTTP {code}")
    ents = [(f["cik"], f["name"]) for f in rows.get(GROUP_PREFIX + "Alpha", {}).get("filers", [])]
    check(sorted(c for c, _ in ents) == sorted(a) and all(n.startswith(GROUP_PREFIX) for _, n in ents),
          "Alpha lists both its filer entities with name and CIK", f"{ents}")
    check(bool(want) and got == want == created, "per-entity per-year counts equal SQL and the fixtures created",
          f"api {got} | sql {want}")
    alpha_years = rows.get(GROUP_PREFIX + "Alpha", {}).get("years", {})
    check(alpha_years == {"2025": 4, "2026": 2}, "the group's per-year totals sum its entities", f"{alpha_years}")
    return {"a": a, "b": b}


async def y_preview(conn, url, eb, fx) -> None:
    section("[Y] Preview — counts equal SQL on fixtures; To past the index refused with the date; writes nothing; live < 2 s")
    a, b = fx["a"], fx["b"]
    body = {"banks": [GROUP_PREFIX + "Alpha", GROUP_PREFIX + "Beta"], "from": "2025-01-01", "to": "2026-09-30",
            "kinds": ["final"], "peek": True}
    before_runs = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    before_st = await rls_fetch(conn, "SELECT accession_number, pipeline_status, claimed_by FROM portfolio.edgar_index_filings "
                                      "WHERE accession_number LIKE $1 ORDER BY 1", ACC_PREFIX + "%")
    code, p = await api(U_SUPER, "POST", f"{BASE}/preview", body)
    insts = {i["name"]: i for i in (p or {}).get("institutions", [])}
    sql = await rls_fetch(
        conn, """SELECT primary_issuer_cik AS cik, extract(year FROM filing_date)::int AS y, count(*)::int AS n
                   FROM portfolio.edgar_index_filings
                  WHERE primary_issuer_cik = ANY($1::text[]) AND form_type = '424B2' AND pipeline_status = 'selected'
                    AND filing_date BETWEEN '2025-01-01' AND '2026-09-30' GROUP BY 1, 2""", a + b)
    by_cik: dict[str, int] = {}
    by_group_year: dict[tuple[str, str], int] = {}
    for r in sql:
        by_cik[r["cik"]] = by_cik.get(r["cik"], 0) + r["n"]
        g = GROUP_PREFIX + ("Alpha" if r["cik"] in a else "Beta")
        by_group_year[(g, str(r["y"]))] = by_group_year.get((g, str(r["y"])), 0) + r["n"]
    api_gy = {(n, y): k for n, i in insts.items() for y, k in i["years"].items()}
    api_cik = {f["cik"]: f["count"] for i in insts.values() for f in i["filers"]}
    check(code == 200 and p.get("total") == 10 == sum(by_cik.values()),
          "preview total equals SQL and the 10 fixture filings in range", f"HTTP {code} total {p.get('total')}")
    check(bool(by_group_year) and api_gy == by_group_year, "per institution × year equals SQL", f"{api_gy}")
    check(bool(by_cik) and api_cik == by_cik, "per filer entity equals SQL", f"{api_cik}")
    est = p.get("estimate") or {}
    check(est.get("filings") == 10 and est.get("eta_seconds", 0) > 0 and "monthly_cost_usd" in est
          and "new_storage_gb" in est, "the preview carries ETA, storage and monthly cost",
          f"eta {est.get('eta_seconds')}s, {est.get('new_storage_gb')} GB, ${est.get('monthly_cost_usd')}")
    last = await eb.last_indexed_date(conn)
    code2, p2 = await api(U_SUPER, "POST", f"{BASE}/preview", body | {"to": (last + timedelta(days=1)).isoformat()})
    d2 = detail(p2)
    check(code2 == 409 and "Refresh the index first" in (d2.get("message") or "")
          and d2.get("last_indexed_date") == str(last),
          "a To after the last indexed date is refused with 'Refresh the index first' and the date",
          f"HTTP {code2}: {d2.get('message')}")
    after_runs = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    after_st = await rls_fetch(conn, "SELECT accession_number, pipeline_status, claimed_by FROM portfolio.edgar_index_filings "
                                     "WHERE accession_number LIKE $1 ORDER BY 1", ACC_PREFIX + "%")
    check(before_runs == after_runs and [dict(r) for r in before_st] == [dict(r) for r in after_st] and bool(before_st),
          "the preview wrote nothing (run table and fixture rows unchanged, no claims)",
          f"runs {before_runs}->{after_runs}; {len(before_st)} fixture rows")

    # Live, read-only: one real group, one year.
    real = {"banks": [REAL_GROUP], "from": f"{REAL_YEAR}-01-01", "to": f"{REAL_YEAR}-12-31", "kinds": ["final"],
            "peek": True}
    await api(U_SUPER, "POST", f"{BASE}/preview", real)          # warm the app pool
    t0 = time.perf_counter()
    code3, p3 = await api(U_SUPER, "POST", f"{BASE}/preview", real)
    secs = time.perf_counter() - t0
    src = await eb.load_source(conn, "structured_notes")
    real_ciks = [r["filer_cik"] for r in await rls_fetch(
        conn, "SELECT filer_cik FROM portfolio.structured_note_issuers WHERE issuer_group = $1 "
              "AND include_status = ANY($2::text[])", REAL_GROUP, list(src.filer_include_status))]
    n_sql = await rls_val(
        conn, """SELECT count(*) FROM portfolio.edgar_index_filings WHERE primary_issuer_cik = ANY($1::text[])
                   AND form_type = ANY($2::text[]) AND pipeline_status = 'selected'
                   AND filing_date BETWEEN $3 AND $4""",
        real_ciks, list(src.form_types), date(REAL_YEAR, 1, 1), date(REAL_YEAR, 12, 31))
    print(f"    live preview {REAL_GROUP} {REAL_YEAR}: {secs * 1000:.0f} ms end to end "
          f"(server-side {(p3 or {}).get('elapsed_ms')} ms), total {(p3 or {}).get('total')}, SQL {n_sql}")
    check(code3 == 200 and (p3 or {}).get("total") == n_sql and n_sql > 0,
          f"live preview for {REAL_GROUP} {REAL_YEAR} equals a direct SQL count (read-only)",
          f"{(p3 or {}).get('total')} vs {n_sql}")
    check(secs < PREVIEW_LIMIT_SECONDS, f"live preview returns under {PREVIEW_LIMIT_SECONDS:g} s",
          f"measured {secs:.3f} s")


async def y_queue_laptop(conn, url, q) -> None:
    section("[Y] Queue on laptop with no worker heartbeat — queued, worker_online false, the start command returned")
    body = {"params": {"banks": [GROUP_PREFIX + "Alpha"], "from": "2025-01-01", "to": "2025-12-31",
                       "kinds": ["final"]}}
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs", body)
    run = (res or {}).get("run") or {}
    check(code == 202 and run.get("status") == "queued" and run.get("target") == "laptop",
          "POST runs with no target queues a run on the laptop (the default)", f"HTTP {code} {run.get('status')} {run.get('target')}")
    check(res.get("worker_online") is False and res.get("start_command") == q.LAPTOP_COMMAND,
          "worker_online is false and the exact start command is returned", f"{res.get('start_command')}")
    row = await run_row(url, UUID(run["id"])) if run.get("id") else {}
    p = jd(row.get("params"))
    check(row.get("status") == "queued" and row.get("run_kind") == "bulk_fetch" and row.get("queued_at") is not None
          and p.get("banks") == [GROUP_PREFIX + "Alpha"] and p.get("max_rps") == 8,
          "an independent re-read shows the queued row with its params", f"{row.get('status')} {p}")
    if run.get("id"):
        await q.cancel(conn, UUID(run["id"]))      # never left for a real worker to find


async def y_render(conn, url, q) -> None:
    section("[Y] Render — 409 and nothing queued when not set up; queued with target 'render' when configured + alive")
    body = {"params": {"banks": [GROUP_PREFIX + "Beta"], "from": "2026-09-01", "to": "2026-09-30"}, "target": "render"}
    count_q = "SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE run_kind = ANY($1::text[])"
    kinds = list(q.QUEUED_KINDS)
    os.environ.pop(q.RENDER_WORKER_ENV, None)
    q._RENDER_CACHE.clear()
    n0 = await rls_val(conn, count_q, kinds)
    calls0 = len(RENDER_CALLS)
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs", body)
    n1 = await rls_val(conn, count_q, kinds)
    d = detail(res)
    check(code == 409 and d.get("message") == q.RENDER_REFUSAL and d.get("setup_anchor") == q.SETUP_ANCHOR,
          "not configured: 409 with the refusal message and the setup anchor", f"HTTP {code}: {d.get('message')}")
    check(n0 == n1, "zero runs queued (count before and after)", f"{n0} -> {n1}")
    check(len(RENDER_CALLS) == calls0, "with no service id configured, Render is not even read")

    sid = "srv-verify-bfui-0001"
    os.environ[q.RENDER_WORKER_ENV] = sid
    os.environ.setdefault("RENDER_API_KEY", "verify-render-key")
    RENDER_SERVICE.clear()
    RENDER_SERVICE.update({"id": sid, "type": "background_worker", "suspended": "not_suspended",
                           "name": "verify-bfui-worker"})
    q._RENDER_CACHE.clear()
    try:
        code_nohb, _ = await api(U_SUPER, "POST", f"{BASE}/runs", body)
        n2 = await rls_val(conn, count_q, kinds)
        check(code_nohb == 409 and n2 == n1, "configured but no Render heartbeat in 5 minutes: still 409, nothing queued",
              f"HTTP {code_nohb}; {n1} -> {n2}")
        await rls_exec(conn, "INSERT INTO portfolio.edgar_fetch_workers (worker_id, target, host, pid) "
                             "VALUES ($1, 'render', 'verify-render-host', 1)", f"{WORKER_PREFIX}render-alive")
        code_ok, res_ok = await api(U_SUPER, "POST", f"{BASE}/runs", body)
        run = (res_ok or {}).get("run") or {}
        row = await run_row(url, UUID(run["id"])) if run.get("id") else {}
        check(code_ok == 202 and row.get("target") == "render" and row.get("status") == "queued",
              "configured + alive (mocked): the same request queues with target 'render'",
              f"HTTP {code_ok}; {row.get('target')} {row.get('status')}")
        code_w, w = await api(U_SUPER, "GET", f"{BASE}/workers")
        check(code_w == 200 and w["render"]["configured"] is True and w["render"]["online"] is True,
              "GET workers reports Render configured and online", f"{w.get('render', {}).get('reason')}")
        if run.get("id"):
            await q.cancel(conn, UUID(run["id"]))
    finally:
        os.environ.pop(q.RENDER_WORKER_ENV, None)
        q._RENDER_CACHE.clear()
        await rls_exec(conn, "UPDATE portfolio.edgar_fetch_workers SET stopped_at = now() WHERE worker_id = $1",
                       f"{WORKER_PREFIX}render-alive")


async def y_worker_once(conn, url, pool, q, eb) -> None:
    section("[Y] Worker --once — claims only its own target's run, executes via the CLI code path, writes progress")
    g1 = await make_group(conn, "WorkL", 1)
    g2 = await make_group(conn, "WorkR", 1)
    accs1 = [await make_filing(conn, g1[0], D0 + timedelta(days=i)) for i in range(5)]
    accs2 = [await make_filing(conn, g2[0], D0 + timedelta(days=i)) for i in range(2)]
    run_r = await insert_queued(conn, [GROUP_PREFIX + "WorkR"], D0, D0 + timedelta(days=10), target="render")
    run_l = await insert_queued(conn, [GROUP_PREFIX + "WorkL"], D0, D0 + timedelta(days=10), target="laptop")
    progress_writes: list[dict] = []
    w = make_worker(pool, q, "laptop-once", restrict_to=[run_l, run_r])
    orig = w.write_progress

    async def spy(run_id, progress):
        progress_writes.append(dict(progress))
        await orig(run_id, progress)

    w.write_progress = spy
    saved = eb.PROGRESS_EVERY
    eb.PROGRESS_EVERY = 2
    c0 = len(RUNNER_CALLS)
    try:
        n = await w.run(once=True)
    finally:
        eb.PROGRESS_EVERY = saved
    calls = RUNNER_CALLS[c0:]
    check(n == 1 and [e["run_id"] for e in w.executed] == [run_l],
          "the laptop worker executed exactly the laptop run", f"{w.executed}")
    check(len(calls) == 1 and calls[0]["run_id"] == run_l and calls[0]["fn"] == "Runner.run",
          "it ran through edgar_bulk.Runner.run — the CLI's own code path (recorded by the mock)", f"{calls}")
    row = await run_row(url, run_l)
    prog = jd(row.get("progress"))
    check(row.get("status") == "succeeded" and prog.get("done") == 5 and prog.get("final") is True
          and row.get("worker_id") == w.worker_id and row.get("fetched") == 5 and row.get("finished_at") is not None,
          "independent re-read: 'succeeded', progress done 5 (final), counters written, worker recorded",
          f"{row.get('status')} done {prog.get('done')} fetched {row.get('fetched')}")
    mid = [p for p in progress_writes if not p.get("final")]
    check(len(mid) >= 1 and all(0 < p["done"] <= 5 for p in mid) and len(progress_writes) == len(mid) + 1,
          "progress was written every PROGRESS_EVERY filings while running (2 here) and once on finish",
          f"mid-run writes at done={[p['done'] for p in mid]}")
    st = await statuses(conn, accs1)
    check(len(st) == 5 and all(v["pipeline_status"] == "ready_for_extraction" and v["claimed_by"] is None
                               for v in st.values()),
          "every fixture filing of the run was stored and released", f"{sorted({v['pipeline_status'] for v in st.values()})}")
    rr = await run_row(url, run_r)
    st2 = await statuses(conn, accs2)
    check(rr.get("status") == "queued" and rr.get("worker_id") is None
          and all(v["pipeline_status"] == "selected" for v in st2.values()),
          "the render-target run was NOT claimed by the laptop worker", f"{rr.get('status')}")
    wr = make_worker(pool, q, "render-once", target="render", restrict_to=[run_l, run_r])
    n2 = await wr.run(once=True)
    rr = await run_row(url, run_r)
    check(n2 == 1 and rr.get("status") == "succeeded" and rr.get("worker_id") == wr.worker_id,
          "a --target render worker claims and completes the render run (the future Render entrypoint)",
          f"{rr.get('status')}")


async def y_two_workers(conn, url, pool, q) -> None:
    section("[Y] Two workers — one queued run claimed by exactly one (SKIP LOCKED); never two runs at once; <= 8/s")
    g = await make_group(conn, "Two", 1)
    for i in range(4):
        await make_filing(conn, g[0], D0 + timedelta(days=i))
    rid = await insert_queued(conn, [GROUP_PREFIX + "Two"], D0, D0 + timedelta(days=10))
    wa = make_worker(pool, q, "laptop-a", restrict_to=[rid])
    wb = make_worker(pool, q, "laptop-b", restrict_to=[rid])
    s0 = len(STAMPS)
    na, nb = await asyncio.gather(wa.run(once=True), wb.run(once=True))
    row = await run_row(url, rid)
    check(na + nb == 1 and row.get("status") == "succeeded" and row.get("worker_id") in (wa.worker_id, wb.worker_id),
          "one queued run, two workers at once: executed by exactly one", f"a {na}, b {nb}, by {row.get('worker_id')}")

    g2 = await make_group(conn, "Serial", 1)
    for i in range(12):
        await make_filing(conn, g2[0], D0 + timedelta(days=i))
    t1 = await insert_queued(conn, [GROUP_PREFIX + "Serial"], D0, D0 + timedelta(days=7))
    t2 = await insert_queued(conn, [GROUP_PREFIX + "Serial"], D0 + timedelta(days=8), D0 + timedelta(days=11))
    SEC_DELAY[0] = 0.1          # ~0.4 s per filing: t1 (8 filings, 2 workers) runs for well over a second
    c0 = len(RUNNER_CALLS)
    try:
        wc = make_worker(pool, q, "laptop-c", restrict_to=[t1, t2])
        wd = make_worker(pool, q, "laptop-d", restrict_to=[t1, t2])
        task = asyncio.create_task(wc.run(once=True))
        running = False
        for _ in range(500):
            if await rls_val(conn, "SELECT status FROM portfolio.edgar_pipeline_runs WHERE id = $1", t1) == "running":
                running = True
                break
            await asyncio.sleep(0.02)
        o0 = len(OUT)
        nd = await wd.run(once=True)
        refused = [m for m in OUT[o0:] if "one bulk run at a time" in m]
        check(running and nd == 0 and bool(refused),
              "while a run is running, a second worker claims nothing (one bulk run at a time)",
              refused[0][:140] if refused else f"running={running} executed={nd}")
        await task
        nd2 = await wd.run(once=True)
    finally:
        SEC_DELAY[0] = 0.0
    r1, r2 = await run_row(url, t1), await run_row(url, t2)
    spans = {e["run_id"]: (e["t0"], e["t1"]) for e in RUNNER_CALLS[c0:]}
    s1, s2 = spans.get(t1), spans.get(t2)
    check(nd2 == 1 and r1.get("status") == r2.get("status") == "succeeded" and bool(s1 and s2)
          and s1[1] is not None and s1[1] <= s2[0],
          "after the first finished, the second ran — the two runs never overlapped (in-process clock)",
          f"t1 {s1} / t2 {s2}")
    stamps = sorted(STAMPS[s0:])
    worst = max((sum(1 for u in stamps if t <= u < t + 1.0) for t in stamps), default=0)
    check(len(stamps) > 0 and worst <= 8, f"combined SEC requests across both workers: busiest 1-second window {worst}",
          f"{len(stamps)} requests")


async def y_stop_requeue(conn, url, pool, q) -> None:
    section("[Y] Stop — stop_requested mid-run -> 'stopped', in-flight finished; requeue resumes, nothing fetched twice")
    g = await make_group(conn, "Stop", 1)
    accs = [await make_filing(conn, g[0], D0 + timedelta(days=i)) for i in range(10)]
    rid = await insert_queued(conn, [GROUP_PREFIX + "Stop"], D0, D0 + timedelta(days=12))
    fired: list[int] = []

    async def stop_after_two(runner):
        if runner.stats.done >= 2 and not fired:
            fired.append(None)              # claimed before the await: two workers can't both fire
            code, _ = await api(U_SUPER, "POST", f"{BASE}/runs/{rid}/stop")
            fired[0] = code

    SEC_DELAY[0] = 0.06
    try:
        w = make_worker(pool, q, "laptop-stop", restrict_to=[rid], runner_kwargs={"after_filing": stop_after_two})
        await w.run(once=True)
    finally:
        SEC_DELAY[0] = 0.0
    row = await run_row(url, rid)
    st = await statuses(conn, accs)
    done1 = [a for a in accs if st[a]["pipeline_status"] == "ready_for_extraction"]
    left = [a for a in accs if st[a]["pipeline_status"] == "selected"]
    other = [a for a in accs if a not in done1 and a not in left]
    check(fired == [200] and row.get("status") == "stopped" and row.get("stop_requested") is True,
          "POST stop mid-run (via the API) -> the run closes 'stopped'", f"stop HTTP {fired}; {row.get('status')}")
    check(2 <= len(done1) < 10 and not other and all(st[a]["claimed_by"] is None for a in accs)
          and all(st[a]["reference_filing_id"] is not None for a in done1)
          and jd(row.get("progress")).get("done") == len(done1),
          "in-flight filings finished (each stored row complete), the rest unclaimed and still 'selected'",
          f"done {len(done1)}, left {len(left)}, half-done {other}")
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs/{rid}/requeue")
    check(code == 200 and (res.get("run") or {}).get("status") == "queued", "requeue a stopped run -> queued",
          f"HTTP {code}")
    w2 = make_worker(pool, q, "laptop-resume", restrict_to=[rid])
    await w2.run(once=True)
    row = await run_row(url, rid)
    st = await statuses(conn, accs)
    gets = {a: full_gets(a) for a in accs}
    prog = jd(row.get("progress"))
    check(row.get("status") == "succeeded" and all(v["pipeline_status"] == "ready_for_extraction" for v in st.values()),
          "the requeued run resumed and finished every filing", f"{row.get('status')}")
    check(all(v == 1 for v in gets.values()), "no fixture filing was fetched twice (one full download each)",
          f"{sorted(set(gets.values()))}")
    check(prog.get("done") == 10 - len(done1) and (prog.get("previous") or {}).get("done") == len(done1),
          "the resume processed only the remainder; the stopped attempt's progress is kept as 'previous'",
          f"now {prog.get('done')}, previous {(prog.get('previous') or {}).get('done')}")


async def y_lost_cancel(conn, url, pool, q) -> None:
    section("[Y] Stale heartbeat — 'worker lost', requeue allowed; cancel only on queued runs")
    g = await make_group(conn, "Lost", 1)
    await make_filing(conn, g[0], D0)
    rid = await insert_queued(conn, [GROUP_PREFIX + "Lost"], D0, D0)
    async with pool.acquire() as c:
        claimed, _ = await q.claim_next_run(c, target="laptop", worker_id=f"{WORKER_PREFIX}lost", host="gone",
                                            restrict_to=[rid])
    check(claimed is not None and claimed["id"] == rid, "a worker claimed the run", f"{claimed and claimed['status']}")
    await rls_exec(conn, "UPDATE portfolio.edgar_pipeline_runs SET heartbeat_at = now() - interval '10 minutes' "
                         "WHERE id = $1", rid)
    code, res = await api(U_SUPER, "GET", f"{BASE}/runs/{rid}")
    r = (res or {}).get("run") or {}
    check(code == 200 and r.get("status") == "running" and r.get("display_status") == "worker_lost"
          and r.get("worker_lost") is True and r.get("actions", {}).get("requeue") is True,
          "a running run with a stale heartbeat shows 'worker lost' and offers Requeue", f"{r.get('display_status')}")
    code_c, _ = await api(U_SUPER, "POST", f"{BASE}/runs/{rid}/cancel")
    check(code_c == 409, "cancel is refused on a running (lost) run", f"HTTP {code_c}")
    code_r, res_r = await api(U_SUPER, "POST", f"{BASE}/runs/{rid}/requeue")
    check(code_r == 200 and (res_r.get("run") or {}).get("status") == "queued", "requeue of a worker-lost run is allowed",
          f"HTTP {code_r}")
    ghost = make_worker(pool, q, "lost")
    ghost.current_run_id = rid
    check(await ghost.heartbeat() is False, "the lost worker, if it wakes, is fenced (its heartbeat matches no row)")
    code_c2, res_c2 = await api(U_SUPER, "POST", f"{BASE}/runs/{rid}/cancel")
    row = await run_row(url, rid)
    check(code_c2 == 200 and row.get("status") == "cancelled", "cancel works on the queued run", f"HTTP {code_c2}")
    code_c3, _ = await api(U_SUPER, "POST", f"{BASE}/runs/{rid}/cancel")
    check(code_c3 == 409, "cancel again (now cancelled) is refused", f"HTTP {code_c3}")


async def y_refresh(conn, url, pool, q, eb) -> None:
    section("[Y] refresh_index — queued from the API and executed by the worker with mocked index files")
    g = await make_group(conn, "Refresh", 1)
    last = await eb.last_indexed_date(conn)
    # index files on up to two weekdays from the last date (inclusive — the refresh re-reads it), never
    # past today: the queue refuses a future To
    days = eb.weekdays(last, min(last + timedelta(days=7), date.today()))[-2:]
    new_accs = [next_acc(), next_acc(), next_acc()]
    filed = last.isoformat()          # lines dated ON the last date: max(filing_date) never moves
    lines = [(days[0], f"{g[0]}|VERIFY BFUI Refresh Filer 1|424B2|{filed}|edgar/data/{g[0]}/{new_accs[0]}.txt"),
             (days[0], f"{g[0]}|VERIFY BFUI Refresh Filer 1|FWP|{filed}|edgar/data/{g[0]}/{new_accs[1]}.txt"),
             (days[-1], f"{g[0]}|VERIFY BFUI Refresh Filer 1|424B2|{filed}|edgar/data/{g[0]}/{new_accs[2]}.txt")]
    for d in sorted(set(days)):
        INDEX_FILES[d.strftime("%Y%m%d")] = ("CIK|Company Name|Form Type|Date Filed|Filename\n-----\n"
                                             + "".join(ln + "\n" for day, ln in lines if day == d))
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs/refresh-index", {"to": days[-1].isoformat()})
    run = (res or {}).get("run") or {}
    check(code == 202 and run.get("run_kind") == "refresh_index" and run.get("status") == "queued"
          and run.get("target") == "laptop", "POST runs/refresh-index queues a refresh_index run", f"HTTP {code}")
    rid = UUID(run["id"]) if run.get("id") else None
    w = make_worker(pool, q, "laptop-refresh", restrict_to=[rid] if rid else [])
    log0 = len(SEC_LOG)
    n = await w.run(once=True)
    row = await run_row(url, rid) if rid else {}
    prog = jd(row.get("progress"))
    rows = await statuses(conn, new_accs)
    check(n == 1 and row.get("status") == "succeeded" and prog.get("new_filings") == 3 and len(rows) == 3,
          "the worker executed it: 3 new filings from the mocked daily files", f"{row.get('status')} {prog.get('new_filings')}")
    sel = {a: rows[a]["pipeline_status"] for a in new_accs if a in rows}
    check(sel.get(new_accs[0]) == "selected" and sel.get(new_accs[2]) == "selected"
          and sel.get(new_accs[1]) == "not_selected", "selection applied to the new rows only (424B2 in, FWP out)",
          f"{sel}")
    reqs = [e for e in SEC_LOG[log0:] if "daily-index" in e["url"]]
    check(len(reqs) == len(eb.weekdays(last, days[-1])) and await eb.last_indexed_date(conn) == last,
          "one mocked index request per weekday; the last indexed date did not move", f"{len(reqs)} requests")


def y_frontend(q) -> None:
    section("[Y] Front end — the tab, the Run on radio (Laptop default), the Render refusal, worker status; build 0")
    mon = MONITOR_JSX.read_text(encoding="utf-8")
    bulk = BULK_JSX.read_text(encoding="utf-8") if BULK_JSX.exists() else ""
    logic = BULK_LOGIC.read_text(encoding="utf-8") if BULK_LOGIC.exists() else ""
    m = re.search(r'\{ key: "bulk", label: "Bulk download" \}', mon)
    check(m is not None and "<BulkDownloadTab />" in mon, "EdgarPipelineMonitor has the Bulk download tab",
          m.group(0) if m else "no match")
    radios = re.findall(r'name="bulk-run-on"\s+value="(laptop|render)"', bulk)
    check(radios == ["laptop", "render"], "the Run on radio offers Laptop then Render", f"{radios}")
    m = re.search(r"setTarget\(v\.default_target \?\? null\)", bulk)
    check(m is not None and q.DEFAULT_TARGET == "laptop",
          "the radio's default is the server's default_target, which is 'laptop'", m.group(0) if m else "no match")
    check("vocab.render_refusal" in bulk and "outcome.text" in bulk and "setupAnchor" in bulk,
          "the component renders the server's Render refusal text inline with the setup link")
    check("laptopStatus(workers)" in bulk and "renderStatus(workers)" in bulk and "<CopyButton" in bulk
          and "worker running on" in logic and "no worker — start it with" in logic and '"not set up"' in logic,
          "the worker status (laptop host / start command with Copy; Render 'not set up') is present")
    try:
        proc = subprocess.run(["node", "--test", "tests/edgar/"], cwd=str(WEB_DIR), capture_output=True, text=True,
                              timeout=300)
        out = proc.stdout + proc.stderr
        m = re.search(r"^# pass (\d+)", out, re.M)
        check(proc.returncode == 0 and m is not None and int(m.group(1)) > 0,
              "node tests for the tab's logic pass (envelope fed to the render logic; fails closed)",
              m.group(0) if m else out[-200:])
    except Exception as exc:  # noqa: BLE001
        check(False, "node tests for the tab's logic ran", f"{type(exc).__name__}: {exc}")
    try:
        proc = subprocess.run(["npm", "run", "build"], cwd=str(WEB_DIR), capture_output=True, text=True, timeout=600)
        check(proc.returncode == 0, "npm run build exits 0", (proc.stdout + proc.stderr)[-300:] if proc.returncode else "")
        hits = [p for p in (WEB_DIR / ".next").rglob("*.js") if p.stat().st_size < 5_000_000
                and "bulk-run-on" in p.read_text(errors="ignore")] if proc.returncode == 0 else []
        check(bool(hits), "the built output contains the Bulk download component", f"{len(hits)} chunk(s)")
    except Exception as exc:  # noqa: BLE001
        check(False, "npm run build ran", f"{type(exc).__name__}: {exc}")


async def y_permissions(conn, q) -> None:
    section("[Y] Permission gate on every new endpoint — org admin refused, super admin admitted, identical request")
    from services.rbac import has_org_admin_grant
    async with conn.transaction():
        await _super(conn)
        granted = await has_org_admin_grant(conn, U_ORGADMIN, ORG)
    check(granted is True, "the refused user is a REAL org admin (an org_admin user_roles grant)")
    rid = await insert_queued(conn, [GROUP_PREFIX + "Alpha"], date(2025, 1, 1), date(2025, 12, 31))
    params = {"banks": [GROUP_PREFIX + "Alpha"], "from": "2025-01-01", "to": "2025-12-31"}
    cases = [
        ("GET", f"{BASE}/institutions", None, {200}),
        ("POST", f"{BASE}/preview", params, {200}),
        ("POST", f"{BASE}/runs", {"params": params}, {202}),
        ("POST", f"{BASE}/runs/refresh-index", {}, {202}),
        ("GET", f"{BASE}/runs", None, {200}),
        ("GET", f"{BASE}/runs/{rid}", None, {200}),
        ("POST", f"{BASE}/runs/{rid}/stop", None, {409}),       # queued: "cancel it instead"
        ("POST", f"{BASE}/runs/{rid}/cancel", None, {200}),
        ("POST", f"{BASE}/runs/{rid}/requeue", None, {409}),    # cancelled: not requeueable
        ("GET", f"{BASE}/workers", None, {200}),
    ]
    count_q = "SELECT count(*) FROM portfolio.edgar_pipeline_runs"
    for method, path, body, ok in cases:
        n0 = await rls_val(conn, count_q)
        before = await run_row_conn(conn, rid)
        c1, b1 = await api(U_ORGADMIN, method, path, body)
        n1 = await rls_val(conn, count_q)
        after = await run_row_conn(conn, rid)
        c2, b2 = await api(U_SUPER, method, path, body)
        check(c1 == 403 and n0 == n1 and before == after and c2 in ok,
              f"{method} {path.replace(str(rid), '{id}')}: org admin 403 (nothing written), super admin {c2}",
              f"org admin HTTP {c1}: {str(b1)[:60]}; super HTTP {c2}")
        if method == "POST" and path.endswith(("/runs", "refresh-index")) and c2 == 202:
            new_id = ((b2 or {}).get("run") or {}).get("id")
            if new_id:
                await q.cancel(conn, UUID(new_id))


async def run_row_conn(conn, rid) -> tuple:
    r = await rls_row(conn, "SELECT status, stop_requested, finished_at FROM portfolio.edgar_pipeline_runs WHERE id = $1",
                      rid)
    return tuple(r) if r else ()


def y_no_model_no_render_write() -> None:
    section("[Y] No AI model calls; the Render client made reads only")
    check(MODEL_CALLS == [], "the mocked model clients recorded no call", f"{MODEL_CALLS}")
    methods = sorted({m for m, _ in RENDER_CALLS})
    check(len(RENDER_CALLS) > 0 and methods == ["GET"], "every Render API call was a GET (no write)",
          f"{len(RENDER_CALLS)} calls, methods {methods}")
    for f in ("services/edgar_bulk_queue.py", "services/edgar_bulk.py"):
        src = (API_DIR / f).read_text()
        check(not re.search(r"^\s*(?:from|import)\s+.*(?:proxy|anthropic|openai|litellm|extraction)\b", src, re.M),
              f"{f} imports no model client")
    src = (API_DIR / "services" / "edgar_bulk_queue.py").read_text()
    writes = re.findall(r"\bhttp\.(post|put|patch|delete)\(", src)
    check(writes == [], "edgar_bulk_queue.py has no Render write call in its source", f"{writes}")


def y_status_doc() -> None:
    section("[Y] PROJECT_STATUS records the sprint")
    text = STATUS_DOC.read_text(encoding="utf-8")
    m = re.search(r"^## 0+\. BULKFETCH UI — .*$", text, re.M)
    check(m is not None, "docs/PROJECT_STATUS.md has the BULKFETCH UI entry", m.group(0)[:90] if m else "no match")
    for needle in ("tmux new -s bulkworker", "--worker --target render", "~$7/month", "EDGAR_BULK_RENDER_WORKER_SERVICE_ID",
                   "Queued-run lifecycle", "Measured preview time"):
        check(needle in text, f"PROJECT_STATUS covers {needle}")


# ═══ Regression ════════════════════════════════════════════════════════════
REGRESSIONS = ("verify_bulkfetch.py", "verify_edgarpipelinea.py", "verify_edgarcohorts.py")


def y_regression() -> None:
    section("[Y] Regression: verify_bulkfetch.py, verify_edgarpipelinea.py, verify_edgarcohorts.py — ONCE each, "
            "flat subprocesses, AFTER this script's own teardown")
    log_dir = REPO / "sprint_prompts" / "logs"
    # verify_bulkfetch.py would otherwise re-run pipelinea / cohorts / notefix2 itself
    # (nested, ~12 min) — they run here, once, flat.
    env = os.environ | {"VERIFY_SKIP_NESTED_REGRESSION": "1"}
    for name in REGRESSIONS:
        try:
            proc = subprocess.run([sys.executable, str(HERE.parent / name)], capture_output=True, text=True,
                                  timeout=600, cwd=str(REPO), env=env)
            out = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = f"TIMEOUT after 600s: {(exc.stdout or b'')[-300:]!r}"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"verify_bulkfetchui.regression.{name.removesuffix('.py')}.out").write_text(out)
        except OSError:
            pass
        totals = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
        m = totals[-1] if totals else None
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(m is not None and int(m[0]) > 0 and int(m[1]) == 0, f"{name} still reports 0 FAIL",
              (f"TOTAL: {m[0]} PASS, {m[1]} FAIL" if m else out[-300:]) + (f"; {fails[:3]}" if fails else ""))


# ═══ Main ══════════════════════════════════════════════════════════════════
async def preconditions(conn, q) -> bool:
    section("[Y] Preconditions — no real worker online, no real bulk run running (they would race the fixtures)")
    live = await rls_fetch(
        conn, f"""SELECT worker_id, target, host FROM portfolio.edgar_fetch_workers
                  WHERE stopped_at IS NULL AND worker_id NOT LIKE $1
                    AND heartbeat_at >= now() - interval '{q.WORKER_LOST_SECONDS} seconds'""", WORKER_PREFIX + "%")
    running = await rls_fetch(
        conn, f"""SELECT id FROM portfolio.edgar_pipeline_runs WHERE status = 'running'
                  AND run_kind = ANY($1::text[]) AND heartbeat_at >= now() - interval '{q.WORKER_LOST_SECONDS} seconds'""",
        list(q.QUEUED_KINDS))
    ok1 = check(not live, "no real worker is heartbeating (stop it: tmux attach -t bulkworker, Ctrl-C)",
                f"{[dict(r) for r in live]}")
    ok2 = check(not running, "no real queued-kind run is running", f"{[str(r['id']) for r in running]}")
    return ok1 and ok2


async def main_async() -> int:
    global ACTIVE_V
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = url
    install_mocks()
    from services import edgar_bulk as eb
    from services import edgar_bulk_queue as q
    conn = await asyncpg.connect(url, statement_cache_size=0)
    pool = None
    pre_counts = None
    gold_before = None

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
        check(bypass is False, "the verify connection's role does NOT bypass RLS",
              f"{await conn.fetchval('SELECT current_user')}")
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "start-of-run teardown", f"{type(exc).__name__}: {exc}")
        left0 = await fixture_counts(conn)
        check(sum(left0.values()) == 0, "no fixture rows before the run", json.dumps(left0))
        pre_counts = await table_counts(conn)
        gold_before = await gold_checksum(conn)
        ACTIVE_V = await rls_val(conn, "SELECT version FROM portfolio.edgar_selection_policies WHERE is_active")
        st_ok = allowed(await check_defs(conn, "portfolio.edgar_index_filings"), "pipeline_status")
        used = {"selected", "not_selected", "ready_for_extraction"}
        check(bool(st_ok) and used <= st_ok, "every fixture pipeline_status is a live CHECK value",
              f"{sorted(used - st_ok)}")
        run_defs = await check_defs(conn, "portfolio.edgar_pipeline_runs")
        check("verify" in allowed(run_defs, "trigger_source") and "queued" in allowed(run_defs, "status")
              and {"laptop", "render"} <= allowed(run_defs, "target"),
              "fixture run values (trigger_source 'verify', status 'queued', targets) are live CHECK values")
        pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=20)

        await run("task 1", y_task1)
        await run("schema", y_schema, conn)
        clear = await run("preconditions", preconditions, conn, q)
        await run("seed users", seed_users, conn)
        fx = await run("institutions", y_institutions, conn, q)
        if fx:
            await run("preview", y_preview, conn, url, eb, fx)
        if clear:
            await run("queue laptop", y_queue_laptop, conn, url, q)
            await run("render", y_render, conn, url, q)
            await run("worker once", y_worker_once, conn, url, pool, q, eb)
            await run("two workers", y_two_workers, conn, url, pool, q)
            await run("stop + requeue", y_stop_requeue, conn, url, pool, q)
            await run("worker lost + cancel", y_lost_cancel, conn, url, pool, q)
            await run("refresh index", y_refresh, conn, url, pool, q, eb)
        else:
            check(False, "worker sections skipped: a real worker or running run is live (see preconditions)")
        await run("permission gate", y_permissions, conn, q)
        y_no_model_no_render_write()
        await run("front end", y_frontend, q)
        await run("status doc", y_status_doc)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        section("[Y] Teardown: zero fixture rows; every touched table at its pre-test count; real gold untouched")
        teardown_clean = False
        try:
            if pool is not None:
                await pool.close()
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
            gold_after = await gold_checksum(conn)
            check(gold_before is not None and gold_after == gold_before,
                  "existing note_gold_values rows unchanged (count and checksum)", f"{gold_before} -> {gold_after}")
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
