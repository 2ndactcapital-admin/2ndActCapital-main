"""verify_edgarops1 — the nightly capped download of NEW selected filings, the Institutions table that adds up, run
summaries, and unit investment trust ETFs located by CIK.

    timeout 600 python3 apps/api/scripts/verify_edgarops1.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_edgarops1.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_edgarops1.py --section q        # seed nothing shared; ONE section
    python3 apps/api/scripts/verify_edgarops1.py                                # everything + fast regressions

Sections: q queueing (refresh gate, off switch, once per day, new vs backlog, cap 2,500 → 2,000 + 500 next day) ·
w Render chunks (resume across ticks, per-filing outcomes in the right statuses, summary) · l laptop hand-off (R2
missing → waiting + reason; a stub laptop worker completes it; the next day queues its own run, no filing twice) ·
f failure recorded, the step never raises, tomorrow unaffected · t the Institutions table adds up (per filer, per
institution; Earlier) · s run summaries (scheduled, screen, refresh) · u UIT ETFs (C) · h READ-ONLY live checks ·
j fast regressions.

NO NETWORK, NO MODEL: every SEC request goes to an ``httpx.MockTransport`` (SEC_LOG records them); R2 is a dict
behind ``services.storage`` (a real S3 client raises); the fund-terms reader pair is replaced by a recorder that
raises after capturing its messages. The EDGAR rate limiter's wait is a no-op here (the rate the step ASKS for is
asserted separately). REAL: services.edgar_auto_download (queue_day / work_tick / maybe_auto_download), the bulk
queue's claim and Worker, the bulk Runner (claims, peek, store_fetched, statuses), edgar_index_schedule's refresh,
edgar_bulk_queue.institutions / run_out / list_runs, replica_terms.pull_prospectus / run_extraction (up to the
model call), replica_holdings.choose_source / fetch_holdings, ai_daily_runs._replica_queue.

THE CLOCK: every scheduled step gets an injected ``now_utc`` on 2002 weekdays (fixture days), so the real day's
once-per-day slots (refresh and download) are never taken; the cutoff ("new since") of a 2002 day can only come from
2002 fixture runs. Filing dates are in 2019–2026 (≤ the real last indexed date).

FIXTURES (tagged; torn down at the START and at the END): structured_note_issuers groups 'VERIFY EDGAROPS1 …' with
9-digit CIKs 99095xxxx (asserted unused by any real row first); edgar_index_filings / _filers / reference_filings with
accession 9999999999-95-xxxxxx; edgar_pipeline_runs whose details.auto_download.date or details.schedule_date is in
2002, or whose params.accessions / details.bulk.institutions name a fixture; their 'bulk_fetch:<id>' leases;
edgar_fetch_workers 'verify-edgarops1…'; securities 'EDGAROPS1 FX …' + their replica_source_documents; a test org
99000000-0000-0000-0000-0000e0a10001 (is_test) and its replica_securities entry.

REFUSES TO START (exit 2) while a real queued-kind run is running (the queue's one-run-at-a-time rule would make this
script wait on it). A real LAPTOP worker that is heartbeating (even idle) would claim section l's laptop-target fixture
runs and send real SEC requests for fixture accessions, so section l is NOT RUN while one is up — reported as a FAIL
naming the worker, never silently skipped. Every other section queues only render-target runs, which a laptop worker
never claims.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import gzip
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "edgarops1"
BEFORE_REF = "81ed111"   # edgarops1-work base: 'chore: refresh schema snapshot'
REGRESSIONS = ["verify_bulkfetch.py", "verify_bulkfetchui.py", "verify_bulkfetchui2.py", "verify_bulkfetchui3.py",
               "verify_edgarpipelinea.py", "verify_previewperf.py", "verify_rep1.py", "verify_dailyops1.py"]
PROMPT_COUNTS = {"selected": 528_934, "not_selected": 182_085, "not_pricing_supplement": 15_006,
                 "ready_for_extraction": 5_885, "prefilter_skipped": 2_061, "fetched": 200, "fetch_failed": 1}


# ═══ pure checks (also run by --self-checks-only) ═══════════════════════════
def capability_checks(check, find):
    """render_capability reads the environment only: each missing piece gives its own reason."""
    from services import edgar_auto_download as AD

    keep = {k: os.environ.get(k) for k in (*AD.R2_ENV, "R2_BUCKET_NAME", "EDGAR_R2_BUCKET", "EDGAR_USER_AGENT")}
    try:
        for k in keep:
            os.environ.pop(k, None)
        os.environ.update({"R2_ACCOUNT_ID": "x", "R2_ACCESS_KEY_ID": "x", "R2_SECRET_ACCESS_KEY": "x",
                           "EDGAR_R2_BUCKET": "verify-bucket", "EDGAR_USER_AGENT": "verify (v@test.local)"})
        check(AD.render_capability() == (True, None), "render_capability: R2 keys + a valid bucket + a User-Agent → can")
        os.environ.pop("R2_SECRET_ACCESS_KEY")
        ok, why = AD.render_capability()
        check(not ok and "R2_SECRET_ACCESS_KEY missing on the scheduler" in why and "laptop worker" in why
              and why.startswith("waiting — Render cannot store files"),
              "R2 secret missing → cannot, with the reason and the laptop hint", f"{why}")
        os.environ["R2_SECRET_ACCESS_KEY"] = "x"
        os.environ["EDGAR_R2_BUCKET"] = "Not_A_Bucket"
        ok, why = AD.render_capability()
        check(not ok and "not a valid bucket name" in (why or ""), "an invalid bucket → cannot, with the reason", f"{why}")
        os.environ["EDGAR_R2_BUCKET"] = "verify-bucket"
        os.environ.pop("EDGAR_USER_AGENT")
        ok, why = AD.render_capability()
        check(not ok and "EDGAR_USER_AGENT" in (why or ""), "no EDGAR_USER_AGENT → cannot reach the SEC", f"{why}")
        for raw, want in (("", True), ("1", True), ("0", False), ("off", False), ("False", False), ("no", False)):
            os.environ["EDGAR_AUTO_DOWNLOAD"] = raw
            check(AD.enabled() is want, f"EDGAR_AUTO_DOWNLOAD={raw!r} → enabled {want} (default ON)")
        os.environ.pop("EDGAR_AUTO_DOWNLOAD", None)
        for raw, want in (("", 2000), ("2500", 2500), ("abc", 2000), ("0", 2000)):
            os.environ["EDGAR_AUTO_DOWNLOAD_DAILY_CAP"] = raw
            check(AD.daily_cap() == want, f"EDGAR_AUTO_DOWNLOAD_DAILY_CAP={raw!r} → {want}")
        os.environ.pop("EDGAR_AUTO_DOWNLOAD_DAILY_CAP", None)
        check(AD.tick_seconds() == 120, "chunk bound default 120 s (EDGAR_AUTO_DOWNLOAD_TICK_SECONDS)")
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def static_checks(check, find):
    tick = (API_DIR / "workflow_scheduler_tick.py").read_text()
    i_idx, i_dl, i_hold = (tick.find("index = await maybe_refresh_index(conn)"),
                           tick.find("download = await maybe_auto_download(conn, pool)"),
                           tick.find("holdings = await maybe_refresh_holdings(pool)"))
    check(0 < i_idx < i_dl < i_hold, "the tick runs the download step right AFTER the index refresh, before holdings",
          f"{i_idx}/{i_dl}/{i_hold}")
    seg = tick[tick.rfind("try:", 0, i_dl):i_dl + 300]
    check("except Exception" in seg, "the download step is its own try: a problem there cannot stop another step")
    eb = (API_DIR / "services" / "edgar_bulk.py").read_text()
    check("AND ($7::text[] IS NULL OR f.accession_number = ANY($7::text[]))" in eb
          and "SET claimed_by = $8::uuid" in eb and "LIMIT $12" in eb,
          "the bulk scope gains ONE NULL-guarded list term; the claim's own parameters renumbered after it")
    from services import edgar_auto_download as AD
    sql = AD.NEW_SELECTED_SQL
    check("f.pipeline_status = 'selected'" in sql and "f.loaded_at >= $1" in sql and "status IN ('queued', 'running')" in sql
          and "ORDER BY f.loaded_at" in sql, "'new' = selected AND first indexed since the cutoff AND not in a waiting "
          "run; oldest first-indexed first")
    from services import edgar_bulk_queue as BQ
    every = {s for v in BQ._TABLE_STATUSES.values() for s in v}
    check(every == {"selected", "fetched", "ready_for_extraction", "extraction_submitted", "extracted", "needs_review",
                    "extraction_failed", "not_pricing_supplement", "prefilter_skipped", "fetch_failed"}
          and sum(len(v) for v in BQ._TABLE_STATUSES.values()) == len(every),
          "the table's status mapping: every eligible status in exactly ONE column (discovered / not_selected excluded)")
    sql_doc = (REPO / "docs" / "edgarops1_part1.sql").read_text()
    check("edgar_pipeline_runs_auto_download_day_uq" in sql_doc and "DROP" not in sql_doc.upper(),
          "docs/edgarops1_part1.sql: the per-day unique index (additive; no DROP)")
    rt = (API_DIR / "services" / "replica_terms.py").read_text()
    m = re.search(r"    else:\n        filing = await H.latest_series_filing\(http, series_id, \(\"497K\",\)\)\n"
                  r"        kind = \"summary_prospectus\"\n        if filing is None:\n"
                  r"            filing = await H.latest_series_filing\(http, series_id, \(\"485BPOS\",\)\)", rt)
    check(m is not None, "the ordinary-fund series path (497K, then 485BPOS by series id) is unchanged text")
    notes = (REPO / "docs" / "EDGAROPS1_NOTES.md").read_text()
    check(all(s in notes for s in ("Definition of \"new\"", "Chunk bound", "Status mapping", "pick order",
                                   "R2 credentials on the cron service", "497K", "485BPOS")),
          "docs/EDGAROPS1_NOTES.md records: 'new', the chunk bound, the status mapping, the reader's order, R2 on the "
          "cron service, the UIT form choice")
    rs = (REPO / "scripts" / "regression_set.txt").read_text().split()
    check("verify_edgarops1.py" in rs, "verify_edgarops1.py is in scripts/regression_set.txt")


def node_checks(check, find):
    p = subprocess.run(["node", "--test", "tests/edgar/edgarops1.test.mjs", "tests/edgar/institutionsTable.test.mjs",
                        "tests/bugfix1/bugfix1.test.mjs"],
                       cwd=WEB_DIR, capture_output=True, text=True, timeout=600)
    m = re.search(r"^# pass (\d+)$", p.stdout, flags=re.M)
    f = re.search(r"^# fail (\d+)$", p.stdout, flags=re.M)
    check(p.returncode == 0 and m is not None and f is not None and f.group(1) == "0" and int(m.group(1)) >= 25,
          "render tests (node --test): rows add up, Earlier, 2027 → 2025–2027 + Earlier, cap-reached note, waiting "
          "reason, '—', the bulkfetchui3 + bugfix1 table tests", f"pass={m.group(1) if m else None} "
          f"fail={f.group(1) if f else None} {p.stderr[-200:]}")


def self_checks(check, find):
    capability_checks(check, find)
    static_checks(check, find)
    node_checks(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _verify_cleanup as VC  # noqa: E402
from services import ai_daily_runs as DR  # noqa: E402
from services import edgar_auto_download as AD  # noqa: E402
from services import edgar_bulk as EB  # noqa: E402
from services import edgar_bulk_queue as BQ  # noqa: E402
from services import edgar_fetch, edgar_pipeline, storage  # noqa: E402
from services import edgar_index_schedule as SCHED  # noqa: E402
from services import replica_holdings as H  # noqa: E402
from services import replica_terms as T  # noqa: E402
from services.database import get_pool  # noqa: E402

TAG = "edgarops1verify"
ACC_PREFIX = "9999999999-95-"
CIK_PREFIX = "99095"
GROUP_PREFIX = "VERIFY EDGAROPS1 "
SEC_PREFIX = "EDGAROPS1 FX"
WORKER_PREFIX = "verify-edgarops1"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
ORG = UUID("99000000-0000-0000-0000-0000e0a10001")
BACKLOG_LOADED = datetime(2001, 6, 1, tzinfo=timezone.utc)

S: dict = {}
_n_pass = 0
_n_fail = 0
_ctr = {"acc": 0, "cik": 0}


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def at(d: date, hh: int = 11, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)


# 2002 fixture weekdays, one per scenario (2002-01-07 is a Monday; 01-12 / 01-13 are the weekend).
DAY = {"q_gate": date(2002, 1, 7), "q_ok": date(2002, 1, 8), "cap1": date(2002, 1, 9), "cap2": date(2002, 1, 10),
       "w": date(2002, 1, 11), "l1": date(2002, 1, 14), "l2": date(2002, 1, 15), "f1": date(2002, 1, 16),
       "f2": date(2002, 1, 17)}
SATURDAY = date(2002, 1, 12)
assert all(d.weekday() < 5 for d in DAY.values()) and len(set(DAY.values())) == len(DAY) and SATURDAY.weekday() == 5


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


def jd(v) -> dict:
    if v is None:
        return {}
    return json.loads(v) if isinstance(v, str) else dict(v)


async def close_conn(c, timeout: float = 30) -> None:
    if c is None:
        return
    try:
        await asyncio.wait_for(c.close(), timeout)
    except Exception:  # noqa: BLE001
        try:
            c.terminate()
        except Exception:  # noqa: BLE001
            pass


async def close_app_pool() -> None:
    import services.database as DBM
    p, DBM._pool = DBM._pool, None
    await close_conn(p)


FIXTURE_RUNS_SQL = f"""
    (details->'auto_download'->>'date') LIKE '2002-%'
    OR (trigger_source = 'schedule' AND details->>'schedule_date' LIKE '2002-%')
    OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(params->'accessions', '[]'::jsonb)) a(x)
                WHERE starts_with(a.x, '{ACC_PREFIX}'))
    OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(details->'bulk'->'institutions', '[]'::jsonb)) b(x)
                WHERE starts_with(b.x, '{GROUP_PREFIX}'))
    OR worker_id LIKE '{WORKER_PREFIX}%'"""


def _plan() -> dict:
    return {
        "portfolio.edgar_pipeline_lease": (
            f"lease_name IN (SELECT 'bulk_fetch:' || id FROM portfolio.edgar_pipeline_runs WHERE {FIXTURE_RUNS_SQL})", []),
        "portfolio.edgar_pipeline_runs": (FIXTURE_RUNS_SQL, []),
        "portfolio.edgar_fetch_workers": ("starts_with(worker_id, $1)", [WORKER_PREFIX]),
        "portfolio.edgar_index_filing_filers": ("starts_with(accession_number, $1)", [ACC_PREFIX]),
        "portfolio.edgar_index_filings": ("starts_with(accession_number, $1)", [ACC_PREFIX]),
        "portfolio.reference_filings": ("starts_with(accession_number, $1)", [ACC_PREFIX]),
        "portfolio.structured_note_issuers": ("starts_with(issuer_group, $1)", [GROUP_PREFIX]),
        "portfolio.replica_source_documents": (
            "fund_global_security_id IN (SELECT id FROM portfolio.securities_global WHERE starts_with(name, $1))",
            [SEC_PREFIX]),
        "portfolio.securities_global_identifiers": (
            "global_security_id IN (SELECT id FROM portfolio.securities_global WHERE starts_with(name, $1))", [SEC_PREFIX]),
        "portfolio.securities_global": ("starts_with(name, $1)", [SEC_PREFIX]),
    }


async def teardown() -> list[str]:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the teardown: {how}")
    op = await connect(dsn)
    try:
        res = await VC.cleanup(op, orgs=[ORG], plan=_plan(), label="verify_edgarops1")
        return res["log"]
    finally:
        await close_conn(op)


async def leftovers(conn) -> dict:
    out = {}
    for t, (pred, args) in _plan().items():
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {pred}", *args, fetch="fetchval"))
    out["public.organizations"] = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG,
                                                     fetch="fetchval"))
    return {k: v for k, v in out.items() if v}


async def real_status_counts(conn) -> dict:
    rows = await platform(conn, "SELECT pipeline_status AS s, count(*)::int AS n FROM portfolio.edgar_index_filings "
                                "WHERE NOT starts_with(accession_number, $1) GROUP BY 1", ACC_PREFIX)
    return {r["s"]: r["n"] for r in rows}


async def real_runs_fingerprint(conn, before: datetime) -> tuple[int, str]:
    r = await platform(conn, f"""SELECT count(*) AS n, md5(COALESCE(string_agg(id::text || status || COALESCE(
                                     finished_at::text, '') || COALESCE(heartbeat_at::text, ''), ',' ORDER BY id), ''))
                                     AS h FROM portfolio.edgar_pipeline_runs
                                 WHERE requested_at < $1 AND NOT ({FIXTURE_RUNS_SQL})""", before, fetch="fetchrow")
    return int(r["n"]), r["h"]


# ═══ Mocks: SEC, R2, rate, models ═══════════════════════════════════════════
DOCS: dict[str, dict] = {}            # accession nodash -> {"doc", "body", "form"} (404 when absent)
FEEDS: dict[tuple[str, str], list] = {}   # (cik-or-series, form) -> [{"form", "accession", "date"}]
SEC_LOG: list[str] = []
R2: dict[str, bytes] = {}
RATES: list[int] = []
MODEL_MESSAGES: list = []

COVER_FINAL = ("<html><body><p>PRICING SUPPLEMENT dated October 3, 2026</p><p>Auto Callable Contingent Income "
               "Barrier Notes linked to the S&amp;P 500 Index. Barrier: 70% of the Initial Level. Contingent coupon "
               "8.00% per annum. Autocall observation dates.</p></body></html>")
COVER_PLAIN = ("<html><body><p>PRICING SUPPLEMENT dated October 3, 2026</p><p>Fixed rate notes due 2031. Interest "
               "4.00% per annum.</p></body></html>")
COVER_PRELIM = ("<html><body><p>Subject to Completion. PRELIMINARY PRICING SUPPLEMENT dated October 3, 2026</p>"
                "<p>Barrier: 70% of the Initial Level.</p></body></html>")
TICKERS_EXCHANGE = {"fields": ["cik", "name", "ticker", "exchange"],
                    "data": [[884394, "SPDR S&P 500 ETF TRUST", "SPY", "NYSE"],
                             [1067839, "INVESCO QQQ TRUST, SERIES 1", "QQQ", "Nasdaq"],
                             [990950001, "EDGAROPS1 FX UIT WITHOUT A PROSPECTUS", "UITX", "NYSE"]]}
TICKERS_MF = {"fields": ["cik", "seriesId", "classId", "symbol"],
              "data": [[1100663, "S000004310", "C000012345", "EOFXA"]]}
_DOC_RE = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/(.+)$")


def _atom(key: str, form: str) -> str:
    cik = key if key.isdigit() else "1100663"
    entries = "".join(
        f"<entry><content><filing-type>{e['form']}</filing-type><accession-number>{e['accession']}</accession-number>"
        f"<filing-date>{e['date']}</filing-date><filing-href>https://www.sec.gov/x</filing-href></content></entry>"
        for e in FEEDS.get((key, form), []))
    return f"<feed><company-info><cik>{cik.zfill(10)}</cik></company-info>{entries}</feed>"


async def sec_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    SEC_LOG.append(url)
    path = request.url.path
    if path.endswith("/files/company_tickers_exchange.json"):
        return httpx.Response(200, json=TICKERS_EXCHANGE)
    if path.endswith("/files/company_tickers_mf.json"):
        return httpx.Response(200, json=TICKERS_MF)
    if path.endswith("/cgi-bin/browse-edgar"):
        q = request.url.params
        return httpx.Response(200, text=_atom(q.get("CIK"), q.get("type")))
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


def install_mocks():
    def fake_upload(key, data, content_type=None, bucket=None):
        R2[key] = bytes(data)
        return key

    def no_s3(*_a, **_k):
        raise AssertionError("a real R2 client was requested during verify_edgarops1")

    storage.upload_bytes = fake_upload
    storage.download_bytes = lambda key, bucket=None: R2[key]
    storage.get_s3_client = no_s3
    T.storage.upload_bytes = fake_upload

    async def no_wait(self, *a, **k):
        return None

    edgar_fetch.RateLimiter.acquire = no_wait
    orig_set = edgar_fetch.set_rate_limit

    def rec_set(n):
        RATES.append(int(n))
        return orig_set(n)

    edgar_fetch.set_rate_limit = rec_set
    EB.edgar_fetch.set_rate_limit = rec_set
    H.make_http = sec_client
    os.environ["EDGAR_USER_AGENT"] = "Hollisworks verify_edgarops1 (verify@test.local)"
    os.environ["EDGAR_R2_BUCKET"] = "verify-edgarops1-bucket"
    for k in ("EDGAR_AUTO_DOWNLOAD", "EDGAR_AUTO_DOWNLOAD_DAILY_CAP", "EDGAR_AUTO_DOWNLOAD_SINCE",
              "EDGAR_AUTO_DOWNLOAD_TICK_SECONDS", "EDGAR_INDEX_AUTO_REFRESH"):
        os.environ.pop(k, None)


# ═══ Fixture builders ══════════════════════════════════════════════════════
ACTIVE_V: int | None = None


def next_cik() -> str:
    _ctr["cik"] += 1
    return f"{CIK_PREFIX}{_ctr['cik']:04d}"


def next_acc() -> str:
    _ctr["acc"] += 1
    return f"{ACC_PREFIX}{_ctr['acc']:06d}"


def nodash(acc: str) -> str:
    return acc.replace("-", "")


async def make_group(conn, name: str, n_ciks: int = 1) -> list[str]:
    ciks = [next_cik() for _ in range(n_ciks)]
    for i, cik in enumerate(ciks):
        await platform(conn, """INSERT INTO portfolio.structured_note_issuers
                                   (filer_cik, issuer_group, filer_name, filer_role, credit_entity, include_status, notes)
                                VALUES ($1, $2, $3, $4, $5, 'yes', $6)""",
                       cik, GROUP_PREFIX + name, f"{GROUP_PREFIX}{name} Filer {i + 1}",
                       "issuer" if i == 0 else "guarantor", f"{name} Credit", TAG, fetch="execute")
    return ciks


async def make_filings(conn, cik: str, n: int, *, day: date = date(2026, 10, 5), status: str = "selected",
                       loaded_at: datetime | None = None, cover: str | None = COVER_PRELIM, kind: str | None = None,
                       stored: str | None = None, form: str = "424B2") -> list[str]:
    """``n`` manifest rows in ONE statement (+ filer rows); ``cover`` = the SEC mock's document body (None = 404).
    stored: None (no reference row) / 'r2' (both keys) / 'cleared' (a reference row, keys NULL)."""
    accs = [next_acc() for _ in range(n)]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        refs = [None] * n
        if stored is not None:
            refs = [r["id"] for r in await conn.fetch(
                """INSERT INTO portfolio.reference_filings
                       (cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                        r2_key, text_r2_key, extraction_status, retention_classification, content_encoding)
                   SELECT $1, 'VERIFY EDGAROPS1 filer', $2, a, $3, 'doc.htm', 'https://www.sec.gov/x/' || a,
                          CASE WHEN $4 THEN 'verify/' || a || '.gz' END, CASE WHEN $4 THEN 'verify/' || a || '.txt.gz' END,
                          'fetched', 'public_reference', 'gzip'
                     FROM unnest($5::text[]) WITH ORDINALITY u(a, o) ORDER BY o RETURNING id""",
                cik, form, day, stored == "r2", accs)]
        await conn.execute(
            """INSERT INTO portfolio.edgar_index_filings
                   (accession_number, form_type, filing_date, index_quarter, submission_path, filer_count,
                    pipeline_status, selection_policy_version, primary_issuer_cik, status_reason, document_kind,
                    reference_filing_id, loaded_at)
               SELECT a, $1, $2, $3, 'edgar/data/' || $4 || '/' || a || '.txt', 1, $5, $6, $4, $7, $8, r,
                      COALESCE($9, now())
                 FROM unnest($10::text[], $11::uuid[]) u(a, r)""",
            form, day, f"{day.year}Q{(day.month - 1) // 3 + 1}", cik, status,
            None if status == "discovered" else ACTIVE_V, f"{TAG} fixture", kind, loaded_at, accs, refs)
        await conn.execute(
            """INSERT INTO portfolio.edgar_index_filing_filers (accession_number, cik, company_name)
               SELECT a, $1, 'VERIFY EDGAROPS1 filer' FROM unnest($2::text[]) a""", cik, accs)
    if cover is not None:
        for a in accs:
            DOCS[nodash(a)] = {"doc": "doc.htm", "body": cover.encode(), "form": form}
    return accs


async def statuses_of(conn, accs: list[str]) -> dict[str, str]:
    rows = await platform(conn, "SELECT accession_number AS a, pipeline_status AS s FROM portfolio.edgar_index_filings "
                                "WHERE accession_number = ANY($1::text[])", list(accs))
    return {r["a"]: r["s"] for r in rows}


async def auto_row(conn, day: date) -> dict | None:
    r = await platform(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE details->'auto_download'->>'date' = $1",
                       day.isoformat(), fetch="fetchrow")
    return dict(r) if r else None


async def auto_rows(conn, day: date) -> int:
    return int(await platform(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE "
                                    "details->'auto_download'->>'date' = $1", day.isoformat(), fetch="fetchval"))


async def refresh_ok(conn, day: date) -> dict:
    """The REAL scheduled index refresh for a 2002 day: its 'through' (day − 1) is before the real last indexed date,
    so it reads no SEC file (an empty weekday range) and succeeds — the gate the download step waits for."""
    return await SCHED.maybe_refresh_index(conn, now_utc=at(day, 10, 31), client_factory=sec_client,
                                           log=lambda m: None, host="verify-edgarops1")


async def step(conn, pool, day: date, ciks, ids=None, *, capability=(True, None), hh=11, mm=0, **kw) -> dict:
    return await AD.maybe_auto_download(conn, pool, now_utc=at(day, hh, mm), client_factory=sec_client,
                                        host="verify-edgarops1", capability=capability, ciks=ciks,
                                        restrict_to=ids if ids is not None else [], log=lambda m: None, **kw)


async def set_concurrency(conn, run_id, n: int) -> None:
    """VOLUME ONLY: the scheduled run asks for concurrency 2 (asserted); the 2,000- and 500-filing runs here are
    worked at 8 so the laptop-to-Supabase round trips fit the leg. Correctness does not depend on it."""
    await platform(conn, "UPDATE portfolio.edgar_pipeline_runs SET params = jsonb_set(params, '{concurrency}', "
                         "to_jsonb($2::int)) WHERE id = $1", run_id, n, fetch="execute")


async def seed_common(conn) -> None:
    global ACTIVE_V
    ACTIVE_V = await platform(conn, "SELECT version FROM portfolio.edgar_selection_policies WHERE is_active",
                              fetch="fetchval")
    check(isinstance(ACTIVE_V, int), "an active selection policy exists (fixtures stamp its version)", f"v{ACTIVE_V}")
    clash = await platform(conn, "SELECT count(*) FROM portfolio.structured_note_issuers WHERE filer_cik LIKE $1 "
                                 "AND NOT starts_with(issuer_group, $2)", CIK_PREFIX + "%", GROUP_PREFIX,
                           fetch="fetchval")
    check(clash == 0, f"no real issuer uses a {CIK_PREFIX}… CIK", f"{clash}")
    uq = await platform(conn, "SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname = "
                              "'edgar_pipeline_runs_auto_download_day_uq'", fetch="fetchval")
    check(bool(uq) and "UNIQUE" in uq, "the per-day unique index for scheduled downloads is LIVE (part 1 applied)",
          f"{uq}")
    src = await EB.load_source(conn, EB.DEFAULT_SOURCE)
    S["form_types"] = list(src.form_types)
    check("yes" in src.filer_include_status, "the source includes include_status 'yes' (fixture issuers are in its "
          "catalog)", f"{src.filer_include_status}")


# ═══ (q) queueing ═══════════════════════════════════════════════════════════
async def y_queue(conn):
    section("[Q] Queueing: only after a successful refresh; off switch; once per day; new vs backlog; the cap")
    pool = await get_pool()
    ciks = await make_group(conn, "Queue", 1)
    new = await make_filings(conn, ciks[0], 3, cover=COVER_PRELIM)
    backlog = await make_filings(conn, ciks[0], 2, loaded_at=BACKLOG_LOADED, cover=COVER_PRELIM)
    done = await make_filings(conn, ciks[0], 1, status="ready_for_extraction", stored="r2", cover=None)
    d = DAY["q_gate"]
    # (1) no refresh yet → nothing queued
    res = await step(conn, pool, d, ciks)
    check(res["queue"]["action"] == "refresh_not_succeeded" and await auto_rows(conn, d) == 0,
          "before the day's index refresh has run: nothing queued, no run row", f"{res['queue']}")
    # before 10:30 and a Saturday: not due
    res = await step(conn, pool, d, ciks, hh=9)
    check(res["queue"]["action"] == "not_due", "09:00 UTC: not due", f"{res['queue']}")
    res = await step(conn, pool, SATURDAY, ciks)
    check(res["queue"]["action"] == "not_due", "a Saturday: not due", f"{res['queue']}")
    # (2) a FAILED refresh → still nothing
    keep_ua = os.environ.pop("EDGAR_USER_AGENT")
    try:
        r = await refresh_ok(conn, d)
    finally:
        os.environ["EDGAR_USER_AGENT"] = keep_ua
    check(r["action"] == "failed", "fixture day's real refresh FAILED (no User-Agent) and was recorded", f"{r}")
    res = await step(conn, pool, d, ciks)
    check(res["queue"]["action"] == "refresh_not_succeeded" and res["queue"].get("refresh") == "failed"
          and await auto_rows(conn, d) == 0, "after a FAILED refresh: nothing queued", f"{res['queue']}")
    # (3) next day: the refresh succeeds; the off switch first
    d2 = DAY["q_ok"]
    r = await refresh_ok(conn, d2)
    check(r["action"] == "succeeded", "the real scheduled refresh succeeds for the next fixture day", f"{r}")
    os.environ["EDGAR_AUTO_DOWNLOAD"] = "0"
    try:
        res = await step(conn, pool, d2, ciks)
    finally:
        os.environ.pop("EDGAR_AUTO_DOWNLOAD", None)
    check(res["action"] == "off" and await auto_rows(conn, d2) == 0, "EDGAR_AUTO_DOWNLOAD=0: off, nothing queued",
          f"{res}")
    res = await step(conn, pool, d2, ciks)
    row = await auto_row(conn, d2)
    params, auto = jd(row and row["params"]), jd(row and row["details"]).get("auto_download") or {}
    listed = params.get("accessions") or []
    check(res["queue"]["action"] == "queued" and row is not None and row["run_kind"] == "bulk_fetch"
          and row["trigger_source"] == "schedule" and row["status"] == "queued" and row["target"] == "render",
          "after a successful refresh: ONE queued bulk_fetch run in the existing queue, scheduled, target render",
          f"{res['queue']}")
    check(sorted(listed) == sorted(new), "the list is EXACTLY the 3 new selected filings — not the 2 backlog rows "
          "(first indexed before the cutoff), not the already-downloaded one", f"{len(listed)} listed")
    check(not set(listed) & set(backlog) and not set(listed) & set(done), "no backlog or handled filing is listed")
    check(params.get("kinds") == ["final"] and params.get("peek") is True and params.get("max_rps") == SCHED.SCHEDULE_MAX_RPS
          and params.get("concurrency") == AD.CONCURRENCY and params.get("banks") == [GROUP_PREFIX + "Queue"],
          "the run asks for finals, peek on, the schedule's SEC rate (SCHEDULE_MAX_RPS) and concurrency 2",
          f"{ {k: params.get(k) for k in ('kinds', 'peek', 'max_rps', 'concurrency', 'banks')} }")
    check(auto.get("new_since") == f"{d2.isoformat()}T00:00:00+00:00" and auto.get("new_since_from") == "switched on today"
          and auto.get("cap_reached") is False and auto.get("carried_over") == 0 and auto.get("eligible") == 3,
          "the run records the cutoff (its first day, 00:00 UTC), eligible 3, cap not reached", f"{auto}")
    # once per day
    res2 = await step(conn, pool, d2, ciks)
    check(res2["queue"]["action"] == "already" and await auto_rows(conn, d2) == 1,
          "a second tick the same day queues nothing (one row per day, by the unique index)", f"{res2['queue']}")
    S["q_run"] = row["id"]
    # backlog untouched
    st = await statuses_of(conn, backlog)
    check(set(st.values()) == {"selected"}, "the backlog fixtures are still 'selected' and in no list", f"{st}")

    section("[Q] The cap: 2,500 new → 2,000 today, 500 carried over and listed the next day")
    cap_ciks = await make_group(conn, "Cap", 1)
    big = await make_filings(conn, cap_ciks[0], 2500, cover=COVER_PRELIM)
    c1, c2 = DAY["cap1"], DAY["cap2"]
    S["cap_days"] = (c1, c2)
    await refresh_ok(conn, c1)
    res = await step(conn, pool, c1, cap_ciks)
    row1 = await auto_row(conn, c1)
    l1 = jd(row1["params"]).get("accessions") or []
    a1 = jd(row1["details"])["auto_download"]
    order = [r["a"] for r in await platform(conn, """SELECT accession_number AS a FROM portfolio.edgar_index_filings
                                                     WHERE accession_number = ANY($1::text[])
                                                     ORDER BY loaded_at, filing_date, accession_number""", big)]
    check(len(l1) == 2000 and a1["cap_reached"] is True and a1["carried_over"] == 500 and a1["eligible"] == 2500
          and row1["fetch_cap"] == 2000, "2,500 new: 2,000 listed today; cap reached — 500 carried over (recorded)",
          f"listed {len(l1)} {a1}")
    check(l1 == order[:2000], "the 2,000 are the OLDEST first-indexed (FIFO), in that order")
    await set_concurrency(conn, row1["id"], 8)
    w = await AD.work_tick(pool, deadline_seconds=900, client_factory=sec_client, host="verify-edgarops1",
                           capability=(True, None), restrict_to=[row1["id"]], log=lambda m: None)
    st1 = await statuses_of(conn, l1)
    check(w["action"] == "worked" and w["status"] == "succeeded" and set(st1.values()) == {"not_pricing_supplement"},
          "the day's 2,000 are downloaded (peeked: preliminaries → not_pricing_supplement) and the run closes",
          f"{w.get('status')} {dict((v, list(st1.values()).count(v)) for v in set(st1.values()))}")
    await refresh_ok(conn, c2)
    res = await step(conn, pool, c2, cap_ciks)
    row2 = await auto_row(conn, c2)
    l2 = jd(row2["params"]).get("accessions") or []
    a2 = jd(row2["details"])["auto_download"]
    check(sorted(l2) == sorted(order[2000:]) and a2["cap_reached"] is False and a2["new_since_from"] ==
          "the first scheduled download run", "the next day lists exactly the 500 carried over (cutoff from the first "
          "day's run), cap not reached", f"{len(l2)} {a2.get('new_since_from')}")
    await set_concurrency(conn, row2["id"], 8)
    # fix 3 — REPRODUCE the full run's FAIL on purpose: a heartbeat must land AFTER the Runner closed its own run row,
    # while the worker is still writing the summary. Heartbeat every 0.2 s (not 30 s) and the summary write held
    # 1.5 s. Before the fix this returned 'fenced' every time; the run must still close 'succeeded'.
    orig_worker, orig_summary, beats = BQ.Worker, AD.write_summary, []

    class FastBeatWorker(orig_worker):
        def __init__(self, *a, **k):
            k.update(heartbeat_seconds=0.2, stop_poll_seconds=0.1)
            super().__init__(*a, **k)

        async def heartbeat(self):
            ok = await super().heartbeat()
            beats.append(ok)
            return ok

    async def slow_summary(*a, **k):
        await asyncio.sleep(1.5)
        return await orig_summary(*a, **k)

    BQ.Worker, AD.write_summary = FastBeatWorker, slow_summary
    try:
        w2 = await AD.work_tick(pool, deadline_seconds=900, client_factory=sec_client, host="verify-edgarops1",
                                capability=(True, None), restrict_to=[row2["id"]], log=lambda m: None)
    finally:
        BQ.Worker, AD.write_summary = orig_worker, orig_summary
    st2 = await statuses_of(conn, l2)
    check(w2["status"] == "succeeded" and set(st2.values()) == {"not_pricing_supplement"},
          "the 500 carried over are downloaded the next day", f"{w2.get('status')}")
    r2 = await platform(conn, "SELECT status, worker_id FROM portfolio.edgar_pipeline_runs WHERE id = $1", row2["id"],
                        fetch="fetchrow")
    check(len(beats) >= 5 and w2["status"] == "succeeded" and r2["status"] == "succeeded",
          "fix 3: heartbeats racing the worker's OWN close (every 0.2 s, summary write held 1.5 s) — the run still "
          "closes 'succeeded', never 'fenced'", f"{len(beats)} heartbeats; tick {w2.get('status')}; row {r2['status']}")
    check(not set(l1) & set(l2), "no filing is in both days' lists")
    check(SCHED.SCHEDULE_MAX_RPS in RATES and max(RATES) <= 8,
          "the Runner set the SEC rate from the run's own max_rps (the schedule's) — never above 8/s", f"{sorted(set(RATES))}")


# ═══ (w) Render chunks + per-filing outcomes ════════════════════════════════
async def y_work(conn):
    section("[W] Render: bounded chunks resume across ticks; per-filing outcomes; the summary")
    pool = await get_pool()
    ciks = await make_group(conn, "Work", 2)
    final = await make_filings(conn, ciks[0], 3, cover=COVER_FINAL, day=date(2026, 10, 6))
    plain = await make_filings(conn, ciks[0], 2, cover=COVER_PLAIN, day=date(2026, 10, 6))
    prelim = await make_filings(conn, ciks[1], 40, cover=COVER_PRELIM, day=date(2026, 10, 7))
    missing = await make_filings(conn, ciks[1], 1, cover=None, day=date(2026, 10, 7))
    d = DAY["w"]
    await refresh_ok(conn, d)
    res = await step(conn, pool, d, ciks)
    row = await auto_row(conn, d)
    rid = row["id"]
    check(res["queue"]["action"] == "queued" and len(jd(row["params"])["accessions"]) == 46,
          "46 new filings queued as one run", f"{res['queue']}")

    async def slow(_runner):
        await asyncio.sleep(0.2)     # with the round trips: several filings per chunk, never all 46 (stop poll 5 s)

    chunks, statuses = 0, []
    for _ in range(60):
        w = await AD.work_tick(pool, deadline_seconds=1, client_factory=sec_client, host="verify-edgarops1",
                               capability=(True, None), restrict_to=[rid],
                               log=(print if os.environ.get("EDGAROPS1_DEBUG") else (lambda m: None)),
                               runner_kwargs={"after_filing": slow})
        if w["action"] != "worked":
            break
        chunks += 1
        statuses.append(w["status"])
        between = await platform(conn, "SELECT status, worker_id, stop_reason FROM portfolio.edgar_pipeline_runs "
                                       "WHERE id = $1", rid, fetch="fetchrow")
        if w["status"] == "queued":
            S.setdefault("between", dict(between))
        if w["status"] != "queued":
            break
    b = S.get("between") or {}
    check(chunks >= 2 and statuses[:-1] == ["queued"] * (chunks - 1) and statuses[-1] == "succeeded",
          "the run needed ≥2 bounded chunks: back to 'queued' after each, 'succeeded' when the list is done",
          f"{chunks} chunks {statuses}")
    check(b.get("status") == "queued" and b.get("worker_id") is None and "continuing on the next tick" in
          (b.get("stop_reason") or ""), "between ticks the run is queued, unowned, with 'continuing on the next tick'",
          f"{b}")
    st = await statuses_of(conn, final + plain + prelim + missing)
    check({st[a] for a in final} == {"ready_for_extraction"}, "finals with payoff terms → ready_for_extraction")
    check({st[a] for a in plain} == {"prefilter_skipped"}, "finals without payoff terms → prefilter_skipped")
    left = [a for a in final + plain + prelim + missing if st[a] == "selected"]
    diag = await platform(conn, "SELECT accession_number, claimed_by, claimed_until > now() AS held, filing_date "
                                "FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])", left) \
        if left else []
    check({st[a] for a in prelim} == {"not_pricing_supplement"}, "preliminaries (peek) → not_pricing_supplement",
          f"left selected: {[dict(r) for r in diag]} run {rid}")
    check({st[a] for a in missing} == {"fetch_failed"}, "a filing the SEC answers 404 → fetch_failed")
    stored = await platform(conn, """SELECT count(*) FROM portfolio.edgar_index_filings f JOIN portfolio.reference_filings
                                     r ON r.id = f.reference_filing_id WHERE f.accession_number = ANY($1::text[])
                                     AND r.r2_key IS NOT NULL""", final + plain, fetch="fetchval")
    check(stored == 5 and sum(1 for k in R2 if any(nodash(a) in k or a in k for a in final + plain)) >= 5,
          "the 5 full downloads were stored (gzipped, to the R2 stub)", f"{stored}")
    run = BQ.run_out(dict(await platform(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", rid,
                                         fetch="fetchrow")))
    s = run["summary"] or {}
    check(s.get("requested") == 46 and s.get("fetched") == 3 and s.get("stored") == 3 and s.get("failed") == 1
          and s.get("skipped") == {"not_pricing_supplement": 40, "prefilter_skipped": 2} and s.get("skipped_total") == 42
          and s.get("still_to_fetch") == 0 and s.get("carried_over") == 0,
          "the summary is where the listed filings stand: requested 46 = fetched 3 + skipped 42 (40 + 2) + failed 1",
          f"{s}")
    check(isinstance(s.get("duration_seconds"), int) and s["duration_seconds"] >= 1
          and float(jd(run.get("progress")).get("elapsed_seconds", 0)) >= 0 and s.get("working_seconds", 0) > 0,
          "duration from the first start to the finish; working seconds accumulated over the chunks",
          f"{s.get('duration_seconds')} / {s.get('working_seconds')}")
    lease = await platform(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_lease WHERE lease_name = $1",
                           f"bulk_fetch:{rid}", fetch="fetchval")
    claims = await platform(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE claimed_by = $1", rid,
                            fetch="fetchval")
    check(lease == 0 and claims == 0, "no lease and no claim left behind after the last chunk", f"{lease} / {claims}")
    S["w_run"] = rid


# ═══ (l) laptop hand-off ════════════════════════════════════════════════════
async def y_laptop(conn):
    section("[L] R2 missing on the scheduler → waiting with the reason; a laptop worker completes it; the next day "
            "queues its own run, no filing twice")
    pool = await get_pool()
    ciks = await make_group(conn, "Laptop", 1)
    first = await make_filings(conn, ciks[0], 6, cover=COVER_FINAL, day=date(2026, 10, 8))
    d1, d2 = DAY["l1"], DAY["l2"]
    keep = {k: os.environ.get(k) for k in AD.R2_ENV}
    try:
        for k in AD.R2_ENV:
            os.environ.pop(k, None)
        cap = AD.render_capability()           # the REAL capability check, R2 removed
        check(cap[0] is False and "R2_ACCOUNT_ID" in cap[1], "the real capability check says Render cannot store files",
              f"{cap}")
        await refresh_ok(conn, d1)
        res = await AD.maybe_auto_download(conn, pool, now_utc=at(d1), client_factory=sec_client,
                                           host="verify-edgarops1", ciks=ciks, restrict_to=[], log=lambda m: None)
        row = await auto_row(conn, d1)
        rid1 = row["id"]
        w = await AD.work_tick(pool, client_factory=sec_client, host="verify-edgarops1", restrict_to=[rid1],
                               log=lambda m: None)
    finally:
        for k, v in keep.items():
            if v is not None:
                os.environ[k] = v
    re1 = await platform(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", rid1, fetch="fetchrow")
    out = BQ.run_out(dict(re1))
    check(res["queue"]["action"] == "queued" and re1["status"] == "queued" and re1["target"] == "laptop",
          "the day's list is still queued — target laptop, claimable by the laptop worker", f"{re1['status']}/{re1['target']}")
    check(w["action"] == "waiting" and out["display_status"] == "waiting" and out["auto_download"]["waiting"]
          and out["auto_download"]["waiting"].startswith("waiting — Render cannot store files: R2_ACCOUNT_ID")
          and "laptop worker can run it" in out["auto_download"]["waiting"],
          "the runs list shows it as Waiting with the reason (never a silent failure)", f"{out['auto_download']}")
    st = await statuses_of(conn, first)
    check(set(st.values()) == {"selected"}, "nothing was fetched while waiting")
    async with pool.acquire() as c:
        wait = await AD.waiting_summary(c)
    check(wait["runs"] >= 1 and wait["filings"] >= 6, "the screen counts the waiting scheduled run and its filings",
          f"{wait}")
    # the next day, still waiting: its own run, none of day 1's filings
    second = await make_filings(conn, ciks[0], 4, cover=COVER_PRELIM, day=date(2026, 10, 9))
    await refresh_ok(conn, d2)
    res2 = await step(conn, pool, d2, ciks, capability=(False, "waiting — test"))
    row2 = await auto_row(conn, d2)
    l2 = jd(row2["params"]).get("accessions") or []
    check(res2["queue"]["action"] == "queued" and sorted(l2) == sorted(second) and not set(l2) & set(first),
          "the next day queues ITS OWN run: only that day's new filings, none already in the waiting run",
          f"{len(l2)} listed")
    # a stub laptop worker claims and completes day 1, then day 2
    # the laptop worker's own pool shape (run_bulk_fetch.py: a plain asyncpg pool, statement_cache_size=0)
    lpool = await asyncpg.create_pool(os.environ["DATABASE_URL"], statement_cache_size=0, min_size=1, max_size=6)
    lw = BQ.Worker(lpool, target="laptop", worker_id=f"{WORKER_PREFIX}-laptop-{uuid4().hex[:6]}", host="verify-laptop",
                   client_factory=sec_client, out=lambda m: None, restrict_to=[rid1, row2["id"]], poll_seconds=0.1)
    try:
        n = await lw.run(once=True)
        n += await lw.run(once=True)
    finally:
        await lpool.close()
    done1 = await platform(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", rid1, fetch="fetchrow")
    done2 = await platform(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", row2["id"],
                           fetch="fetchrow")
    st = await statuses_of(conn, first + second)
    check(n == 2 and done1["status"] == "succeeded" and done2["status"] == "succeeded"
          and done1["worker_id"].startswith(WORKER_PREFIX),
          "the laptop worker (run_bulk_fetch.py --worker's Worker) claimed and completed both scheduled runs",
          f"{n} {done1['status']} {done2['status']}")
    check({st[a] for a in first} == {"ready_for_extraction"} and {st[a] for a in second} == {"not_pricing_supplement"},
          "their filings landed in the right statuses")
    s1 = BQ.run_out(dict(done1))["summary"] or {}
    check(s1.get("requested") == 6 and s1.get("fetched") == 6 and s1.get("still_to_fetch") == 0
          and BQ.run_out(dict(done1))["auto_download"]["waiting"] is None,
          "the laptop worker wrote the scheduled summary too; no waiting reason once it ran", f"{s1}")
    async with pool.acquire() as c:
        wait2 = await AD.waiting_summary(c)
    check(wait2["runs"] == wait["runs"] - 1 and wait2["filings"] <= wait["filings"] - 6,
          "completed runs leave the waiting count", f"{wait} → {wait2}")


# ═══ (f) failure ════════════════════════════════════════════════════════════
async def y_failure(conn):
    section("[F] A failure is recorded on the day's run, never raised; the next day is unaffected")
    pool = await get_pool()
    ciks = await make_group(conn, "Fail", 1)
    accs = await make_filings(conn, ciks[0], 3, cover=COVER_PRELIM, day=date(2026, 10, 12))
    d1, d2 = DAY["f1"], DAY["f2"]
    await refresh_ok(conn, d1)
    orig = EB.institution_catalog

    async def boom(*_a, **_k):
        raise RuntimeError("verify: the catalog could not be read")

    EB.institution_catalog = boom
    raised = None
    try:
        res = await step(conn, pool, d1, ciks)
    except Exception as exc:  # noqa: BLE001
        raised, res = exc, {}
    finally:
        EB.institution_catalog = orig
    row = await auto_row(conn, d1)
    check(raised is None and res.get("action") == "failed" and res["queue"]["action"] == "failed",
          "the step returned 'failed' — it did not raise into the tick", f"{raised!r} {res.get('queue')}")
    check(row is not None and row["status"] == "failed" and "catalog could not be read" in (row["error"] or "")
          and row["finished_at"] is not None, "the failure is RECORDED as the day's run (status failed, the error)",
          f"{row and row['status']} {row and row['error']}")
    out = BQ.run_out(dict(row))
    check(out["actions"]["requeue"] is False and out["summary"] is None,
          "a failed queueing (no list) offers no Requeue and shows no summary ('—')", f"{out['actions']}")
    # the work step failing inside never raises either
    orig_claim = BQ.claim_next_run

    async def boom_claim(*_a, **_k):
        raise RuntimeError("verify: the claim failed")

    BQ.claim_next_run = boom_claim
    try:
        await platform(conn, "UPDATE portfolio.edgar_pipeline_runs SET status = 'queued', finished_at = NULL "
                             "WHERE id = $1 AND status = 'failed'", row["id"], fetch="execute")
        res_w = await step(conn, pool, d1, ciks, ids=[row["id"]])
    finally:
        BQ.claim_next_run = orig_claim
        await platform(conn, "UPDATE portfolio.edgar_pipeline_runs SET status = 'failed', finished_at = now() "
                             "WHERE id = $1", row["id"], fetch="execute")
    check(res_w["work"]["action"] == "failed" and "claim failed" in res_w["work"]["error"],
          "a failure while working is returned as 'failed' too (the tick prints it, exit 1, and carries on)",
          f"{res_w['work']}")
    await refresh_ok(conn, d2)
    res2 = await step(conn, pool, d2, ciks)
    row2 = await auto_row(conn, d2)
    check(res2["queue"]["action"] == "queued" and sorted(jd(row2["params"])["accessions"]) == sorted(accs),
          "the next day queues normally — including the filings the failed day never listed", f"{res2['queue']}")
    tick = (API_DIR / "workflow_scheduler_tick.py").read_text()
    check('download["action"] == "failed"' in tick and "or download[\"action\"] == \"failed\")" in tick,
          "the tick's exit code reflects a failed download step (Render shows it) after every step has run")


# ═══ (t) the Institutions table ═════════════════════════════════════════════
async def y_table(conn):
    section("[T] The Institutions table adds up per filer and per institution; Earlier; matches the SQL")
    ciks = await make_group(conn, "Table", 2)
    c0, c1 = ciks
    plan = [  # (cik, n, status, day, stored)
        (c0, 3, "selected", date(2026, 3, 2), None), (c0, 2, "selected", date(2025, 3, 3), None),
        (c0, 4, "selected", date(2021, 3, 3), None), (c0, 1, "selected", date(2019, 3, 4), None),
        (c0, 2, "ready_for_extraction", date(2025, 4, 1), "r2"), (c0, 1, "fetched", date(2025, 4, 1), "cleared"),
        (c0, 1, "needs_review", date(2024, 4, 1), "r2"), (c0, 2, "not_pricing_supplement", date(2025, 4, 2), None),
        (c0, 1, "prefilter_skipped", date(2025, 4, 2), "r2"), (c0, 1, "fetch_failed", date(2025, 4, 3), None),
        (c0, 2, "not_selected", date(2025, 4, 3), None), (c0, 1, "discovered", date(2025, 4, 3), None),
        (c1, 5, "selected", date(2024, 6, 3), None), (c1, 1, "extracted", date(2024, 6, 3), "r2"),
    ]
    for cik, n, status, day, stored in plan:
        await make_filings(conn, cik, n, status=status, day=day, cover=None, stored=stored)
    other_form = [f for f in ("FWP",) if f not in S["form_types"]]
    if other_form:
        await make_filings(conn, c0, 2, status="selected", day=date(2026, 3, 2), cover=None, form=other_form[0])
    res = await BQ.institutions(conn, use_cache=False)
    rows = {r["name"]: r for r in res["rows"]}
    inst = rows.get(GROUP_PREFIX + "Table") or {}
    filers = {f["cik"]: f for f in inst.get("filers", [])}
    ys = res["years_shown"]
    check(ys == sorted(ys, key=int) and len(ys) <= 3 and all(y in {"2024", "2025", "2026", "2027"} for y in ys),
          "years_shown = the latest 3 to-fetch years present (dynamic)", f"{ys}")
    # independent SQL per filer
    sql = await platform(conn, """SELECT f.primary_issuer_cik AS cik, f.pipeline_status AS s, count(*)::int AS n,
                                         count(*) FILTER (WHERE r.r2_key IS NOT NULL OR r.text_r2_key IS NOT NULL)::int AS st
                                    FROM portfolio.edgar_index_filings f
                                    LEFT JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
                                   WHERE f.primary_issuer_cik = ANY($1::text[]) AND f.form_type = ANY($2::text[])
                                   GROUP BY 1, 2""", ciks, S["form_types"])
    by = {}
    for r in sql:
        by.setdefault(r["cik"], {})[r["s"]] = (r["n"], r["st"])
    cols = BQ._TABLE_STATUSES
    for cik in ciks:
        f = filers.get(cik) or {}
        exp = {k: sum(by.get(cik, {}).get(s, (0, 0))[0] for s in v) for k, v in cols.items()}
        exp_stored = sum(by.get(cik, {}).get(s, (0, 0))[1] for s in cols["downloaded"])
        got = {k: f.get(k) for k in exp}
        check(got == exp and f.get("stored") == exp_stored, f"filer {cik}: every column equals the SQL run directly",
              f"{got} stored {f.get('stored')} vs {exp} {exp_stored}")
        check(f.get("eligible") == f.get("to_fetch", 0) + f.get("downloaded", 0) + f.get("skipped", 0) + f.get("failed", 0)
              and f.get("stored", 0) <= f.get("downloaded", 0),
              f"filer {cik}: Eligible = To fetch + Downloaded + Skipped + Failed; Stored ≤ Downloaded")
        shown = sum(f.get("years", {}).get(y, 0) for y in ys)
        check(shown + f.get("earlier", -1) == f.get("to_fetch"), f"filer {cik}: years shown + Earlier = To fetch",
              f"{shown} + {f.get('earlier')} vs {f.get('to_fetch')}")
    f0 = filers.get(c0) or {}
    check(f0.get("to_fetch") == 10 and f0.get("downloaded") == 4 and f0.get("stored") == 3 and f0.get("skipped") == 3
          and f0.get("failed") == 1 and f0.get("eligible") == 18 and f0.get("pct_done") == round(100 * 8 / 18, 1),
          "the hand-counted filer: to fetch 10, downloaded 4 (stored 3), skipped 3, failed 1, eligible 18, 44.4%",
          f"{ {k: f0.get(k) for k in ('to_fetch', 'downloaded', 'stored', 'skipped', 'failed', 'eligible', 'pct_done')} }")
    check(f0.get("skipped_by_reason") == {"not_pricing_supplement": 2, "prefilter_skipped": 1},
          "Skipped by reason", f"{f0.get('skipped_by_reason')}")
    if set(ys) == {"2024", "2025", "2026"}:
        check(f0.get("earlier") == 5, "Earlier for the filer = its 2019 + 2021 to-fetch rows (1 + 4)", f"{f0.get('earlier')}")
    check(f0.get("handled") == 8 and f0.get("handled_stored") == 4,
          "bulkfetchui3's numbers kept as handled / handled_stored (8 handled, 4 with an R2 key incl. the prefilter skip)",
          f"{f0.get('handled')} / {f0.get('handled_stored')}")
    for k in ("to_fetch", "downloaded", "stored", "skipped", "failed", "eligible", "earlier", "handled"):
        check(inst.get(k) == sum(f.get(k, 0) for f in filers.values()), f"institution {k} = the sum of its filers",
              f"{inst.get(k)}")
    if other_form:
        check(inst.get("to_fetch") == 15, f"a {other_form[0]} row (not the source's form) is not counted",
              f"{inst.get('to_fetch')}")
    check(inst.get("total") == inst.get("to_fetch"), "'total' (the bulkfetchui3 field) still = To fetch")
    bad = [r["name"] for r in res["rows"] for x in [r, *r["filers"]]
           if x["eligible"] != x["to_fetch"] + x["downloaded"] + x["skipped"] + x["failed"]
           or sum(x["years"].get(y, 0) for y in ys) + x["earlier"] != x["to_fetch"]]
    check(not bad, "EVERY row on the page (real institutions too) adds up", f"{bad[:3]}")
    t = res["totals"]
    check(t["eligible"] == sum(r["eligible"] for r in res["rows"]) and t["to_fetch"] == sum(r["to_fetch"] for r in res["rows"]),
          "the totals row is the sum of the institutions")
    cached = await BQ.institutions(conn)
    check(cached is res or cached["as_of"] == res["as_of"], "the 60 s cache still serves the same read")


# ═══ (s) run summaries ══════════════════════════════════════════════════════
async def y_summaries(conn):
    section("[S] Every run gets a summary on the runs list; old runs show '—'")
    pool = await get_pool()
    async with pool.acquire() as c:
        lst = await BQ.list_runs(c, page=1, page_size=100)
    check(isinstance(lst.get("scheduled_waiting"), dict) and lst.get("summary_fields")
          and [f["key"] for f in lst["summary_fields"]] == ["requested", "fetched", "stored", "skipped_total", "failed",
                                                            "carried_over", "duration_seconds"],
          "the runs list carries the summary field list (labels from the server) and the waiting count")
    manual = {"id": uuid4(), "run_kind": "bulk_fetch", "status": "succeeded", "trigger_source": "manual",
              "started_at": datetime(2026, 11, 13, 3, 12, tzinfo=timezone.utc),
              "finished_at": datetime(2026, 11, 13, 3, 14, 34, tzinfo=timezone.utc), "fetch_cap": 62,
              "progress": {"scope_total": 62, "remaining": 0, "by_status": {"ready_for_extraction": 20, "fetched": 9,
                                                                          "not_pricing_supplement": 33}}}
    s = BQ.run_out(manual)["summary"]
    check(s == {"requested": 62, "fetched": 29, "stored": 29, "skipped": {"not_pricing_supplement": 33},
                "skipped_total": 33, "failed": 0, "still_to_fetch": 0, "carried_over": None, "duration_seconds": 154},
          "a screen run's summary from its Runner counts (carried over n/a)", f"{s}")
    check(BQ.run_out({"id": 1, "run_kind": "refresh_index", "status": "succeeded"})["summary"] is None
          and BQ.run_out({"id": 2, "run_kind": "bulk_fetch", "status": "succeeded", "progress": {}})["summary"] is None,
          "a refresh, and an old run with no counts, have no summary ('—')")
    real = await platform(conn, """SELECT * FROM portfolio.edgar_pipeline_runs WHERE run_kind = 'bulk_fetch'
                                   AND trigger_source = 'manual' AND status = 'succeeded'
                                   ORDER BY requested_at DESC LIMIT 1""", fetch="fetchrow")
    if real:
        rs = BQ.run_out(dict(real))["summary"]
        find("the newest real screen run's summary (READ-ONLY)", f"{rs}")
        check(rs is None or rs["fetched"] + rs["skipped_total"] + rs["failed"] <= (rs["requested"] or 0) + 0,
              "a real run's summary is internally consistent (handled ≤ requested)", f"{rs}")
    if S.get("w_run"):
        r = await platform(conn, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", S["w_run"], fetch="fetchrow")
        o = BQ.run_out(dict(r))
        check(o["auto_download"]["date"] == DAY["w"].isoformat() and o["params"].get("accessions_count") == 46
              and "accessions" not in o["params"] and o["actions"]["stop"] is False,
              "a scheduled run's row: its day, the list size (not the list), queue actions", f"{o['params']}")
    if S.get("cap_days"):
        r = await auto_row(conn, S["cap_days"][0])
        o = BQ.run_out(r)
        check(o["auto_download"]["cap_reached"] is True and o["summary"]["carried_over"] == 500,
              "the capped day's summary says 500 carried over", f"{o['summary']}")


# ═══ (u) UIT ETFs ═══════════════════════════════════════════════════════════
async def make_security(pool, name: str) -> str:
    from services import securities_global as SG
    async with H.platform_tx(pool) as c:
        return str(await SG.create_security(c, name=f"{SEC_PREFIX} {name}", security_type="fund", short_name=name,
                                            currency_code="USD", is_super_admin=True))


async def y_uit(conn):
    section("[U] UIT ETFs: located by the trust CIK; the series path unchanged; clear reasons; a blocked fund retried")
    pool = await get_pool()
    H.clear_sec_cache()
    spy_doc = "<html><body><h1>SPDR S&amp;P 500 ETF Trust — Prospectus</h1><p>Ticker SPY. The Trust seeks investment " \
              "results that correspond to the S&amp;P 500 Index. Annual expense 0.0945%.</p></body></html>"
    acc_spy = "0001193125-26-012345"
    FEEDS[("884394", "485BPOS")] = [{"form": "485BPOS", "accession": acc_spy, "date": "2026-01-22"},
                                    {"form": "485BPOS", "accession": "0001193125-25-011111", "date": "2025-01-21"}]
    FEEDS[("884394", "497")] = [{"form": "497", "accession": "0001193125-25-099999", "date": "2025-02-01"}]
    DOCS[nodash(acc_spy)] = {"doc": "spy485bpos.htm", "body": spy_doc.encode(), "form": "485BPOS"}
    # (1) the SEC files: SPY has no mutual-fund row → its trust CIK from the exchange file
    http = sec_client()
    try:
        ids = await H.sec_ids(http, "SPY")
        check(ids == {"cik": "884394", "series_id": None, "class_id": None, "uit": True},
              "SPY (absent from company_tickers_mf.json) → its trust CIK from company_tickers_exchange.json", f"{ids}")
        SEC_LOG.clear()
        ordinary = await H.sec_ids(http, "EOFXA")
        check(ordinary and ordinary["series_id"] == "S000004310" and not any("company_tickers_exchange" in u for u in SEC_LOG),
              "an ordinary fund resolves from the mutual-fund file — the exchange file is never asked", f"{ordinary}")
        filing, kind = await T.locate_uit_prospectus(http, ticker="SPY", cik=None)
        check(filing["accession"] == acc_spy and filing["form"] == "485BPOS" and kind == "prospectus"
              and filing["cik"] == "884394",
              "no 497K → the NEWEST 485BPOS (2026-01-22), not the older one nor the 2025 497", f"{filing} {kind}")
        FEEDS[("884394", "497K")] = [{"form": "497K", "accession": "0001193125-26-000777", "date": "2025-12-01"}]
        f2, k2 = await T.locate_uit_prospectus(http, ticker="SPY", cik="884394")
        check(f2["form"] == "497K" and k2 == "summary_prospectus", "a 497K (summary prospectus) wins when one exists",
              f"{f2['form']}")
        del FEEDS[("884394", "497K")]
        # (2) pull_prospectus by CIK: stored, same storage, same table
        fund = await make_security(pool, "SPY")
        SEC_LOG.clear()
        doc = await T.pull_prospectus(pool, fund_id=fund, ticker="SPY", series_id=None, http=http)
        check(doc["form_type"] == "485BPOS" and doc["accession_number"] == acc_spy and "S&P 500 Index" in
              (doc["extracted_text"] or "") and any(k.startswith("replica/documents/SPY/") for k in R2),
              "pull_prospectus with NO series id finds the trust's prospectus by CIK and stores it (R2 stub + "
              "replica_source_documents)", f"{doc['form_type']} {doc['accession_number']}")
        check(any("CIK=884394" in u and "type=485BPOS" in u for u in SEC_LOG) and not any("CIK=S0" in u for u in SEC_LOG),
              "the EDGAR feed was asked by the trust's CIK, never by a series", f"{[u for u in SEC_LOG if 'browse' in u][:3]}")
        # (3) the reader gets the document (up to the model call — no model is called)
        saved = (T.active_ensemble, T.assert_slots_not_claude, T.read_pair)

        async def fake_ensemble(_c):
            return {"id": str(uuid4()), "model_1": "verify-m1", "model_2": "verify-m2"}

        class Stop(Exception):
            pass

        async def capture(_e, _s, _specs, messages, *_a, **_k):
            MODEL_MESSAGES.append(messages)
            raise Stop()

        T.active_ensemble, T.assert_slots_not_claude, T.read_pair = (fake_ensemble, lambda e, c: ("verify-m1", "verify-m2"),
                                                                     capture)
        try:
            try:
                await T.run_extraction(pool, fund_id=fund, ticker="SPY", series_id=None, http=http,
                                       catalog_loader=lambda: {}, instrument_class="index_etf")
                stopped = False
            except Stop:
                stopped = True
        finally:
            T.active_ensemble, T.assert_slots_not_claude, T.read_pair = saved
        blob = json.dumps(MODEL_MESSAGES[-1] if MODEL_MESSAGES else [], default=str)
        check(stopped and "S&P 500 Index" in blob and "0.0945%" in blob,
              "run_extraction for SPY (no series id) hands the trust prospectus's text to the reader pair "
              "(captured before any model call)", f"stopped={stopped} {len(blob)} chars")
        # (4) an ordinary fund: the series path, unchanged
        FEEDS[("S000004310", "497K")] = [{"form": "497K", "accession": "0001100663-26-000123", "date": "2026-03-01"}]
        DOCS[nodash("0001100663-26-000123")] = {"doc": "ivv497k.htm", "form": "497K",
                                                 "body": b"<html><body>iShares Core Summary Prospectus</body></html>"}
        fund2 = await make_security(pool, "EOFXA")
        SEC_LOG.clear()
        d2 = await T.pull_prospectus(pool, fund_id=fund2, ticker="EOFXA", series_id="S000004310", http=http)
        check(d2["form_type"] == "497K" and d2["doc_kind"] == "summary_prospectus"
              and any("CIK=S000004310" in u and "type=497K" in u for u in SEC_LOG)
              and not any("company_tickers_exchange" in u for u in SEC_LOG),
              "an ordinary fund with a series id: 497K by SERIES exactly as before; no trust lookup", f"{d2['form_type']}")
        # (5) a UIT with no prospectus form → a clear reason
        try:
            await T.locate_uit_prospectus(http, ticker="UITX", cik=None)
            reason = None
        except T.ExtractionBlocked as exc:
            reason = str(exc)
        check(reason is not None and "UITX is a unit investment trust (CIK 990950001)" in reason
              and "497K, 485BPOS, 497" in reason, "a UIT with no 497K / 485BPOS / 497 → ExtractionBlocked naming it",
              f"{reason}")
        try:
            await T.locate_uit_prospectus(http, ticker="NOSUCHX", cik=None)
            reason2 = None
        except T.ExtractionBlocked as exc:
            reason2 = str(exc)
        check(reason2 is not None and "no SEC series id and no trust CIK" in reason2,
              "a fund with neither a series nor a trust CIK → a clear reason, never a guess", f"{reason2}")
        # (6) QQQ-like: no active issuer file → N-PORT by the trust CIK, or the clear reason
        async with pool.acquire() as c:
            src = await H.choose_source(c, ticker="QQQ", series_id=None, cik="1067839")
            try:
                await H.choose_source(c, ticker="QQQ", series_id=None, cik=None)
                none_reason = None
            except H.NoHoldingsSource as exc:
                none_reason = str(exc)
            spy_src = await H.choose_source(c, ticker="SPY", series_id=None, cik="884394")
        check(src["parser"] == "sec_nport" and none_reason and "no SEC series or trust CIK" in none_reason
              and spy_src["key"] == "ssga_spdr",
              "QQQ (its Invesco file inactive): N-PORT by CIK; without a CIK a clear reason; SPY keeps its issuer file",
              f"{src['key']} / {none_reason} / {spy_src['key']}")
        acc_q = "0001067839-26-000042"
        FEEDS[("1067839", "NPORT-P")] = [{"form": "NPORT-P", "accession": acc_q, "date": "2026-10-28"}]
        DOCS[nodash(acc_q)] = {"doc": "primary_doc.xml", "form": "NPORT-P", "body": b"<edgarSubmission/>"}
        got = await H.fetch_holdings(http, src, ticker="QQQ", series_id=None)
        check(got.accession == acc_q and got.url.endswith(f"/1067839/{nodash(acc_q)}/primary_doc.xml") and got.ext == "xml",
              "QQQ's N-PORT located by the trust CIK (resolved from the ticker file) and fetched", f"{got.url}")
        try:
            await H.fetch_holdings(http, src, ticker="UITX", series_id=None)
            nr = None
        except H.NoHoldingsSource as exc:
            nr = str(exc)
        check(nr is not None and "no holdings source for UITX" in nr and "no NPORT-P filing" in nr,
              "a UIT with no issuer file and no N-PORT → 'no holdings source' with the reason", f"{nr}")
    finally:
        await http.aclose()
    # (7) a previously blocked fund is retried: in the nightly queue, once, carrying its (NULL) CIK
    dsn, _ = await resolve_dsn(OPERATOR_DSN_VAR)
    op = await connect(dsn)
    try:
        await op.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true) "
                         "ON CONFLICT (id) DO NOTHING", ORG, "EDGAROPS1 Verify Fixture Org", f"{ORG}-edgarops1")
        await op.execute(
            """INSERT INTO portfolio.replica_securities (org_id, global_security_id, instrument_class, ticker,
                   holdings_source_key, display_name, status)
               VALUES ($1, $2::uuid, 'index_etf', 'SPY', 'ssga_spdr', 'Holdings of SPY', 'active')""", ORG, fund)
    except asyncpg.UniqueViolationError:
        pass
    finally:
        await close_conn(op)
    items = await DR._replica_queue(pool, include_test_orgs=True, cfg={"model_1": "verify-m1", "model_2": "verify-m2"},
                                    limit=1000)
    mine = [i for i in items if i["id"] == fund]
    check(len(mine) == 1 and mine[0]["series_id"] is None and "cik" in mine[0],
          "SPY's blocked fund (no reading under these models) is in the nightly queue ONCE, and the item carries cik",
          f"{mine}")
    real_items = await DR._replica_queue(pool, include_test_orgs=False,
                                         cfg={"model_1": "verify-m1", "model_2": "verify-m2"}, limit=1000)
    check(fund not in {i["id"] for i in real_items}, "the fixture (a test org) is never in the production queue")


# ═══ (h) live, read-only ════════════════════════════════════════════════════
async def y_live(conn):
    section("[H] READ-ONLY live checks")
    live = S["live_before"]
    diff = {k: (PROMPT_COUNTS.get(k), live.get(k)) for k in set(PROMPT_COUNTS) | set(live) if PROMPT_COUNTS.get(k) != live.get(k)}
    find("the prompt's 'confirmed' status counts (2026-11-12) vs the live index now — the 06:00 'EDGAR pipeline — "
         "nightly' workflow (fetch cap 5,000, newest first) and laptop runs moved them before this sprint ran",
         f"{diff}")
    real_sel = await platform(conn, "SELECT min(filing_date), max(filing_date) FROM portfolio.edgar_index_filings "
                                    "WHERE pipeline_status = 'selected' AND NOT starts_with(accession_number, $1)",
                              ACC_PREFIX, fetch="fetchrow")
    find("live selected filing dates", f"{real_sel[0]} … {real_sel[1]}")
    auto_real = await platform(conn, f"SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE {AD.AUTO_RUN_SQL} "
                                     f"AND NOT ({FIXTURE_RUNS_SQL})", fetch="fetchval")
    check(auto_real == 0, "no REAL scheduled download run exists (nothing deployed has queued one; this script queued "
          "only 2002 fixture days)", f"{auto_real}")


# ═══ (j) regressions ════════════════════════════════════════════════════════
def regression():
    section("[J] Regressions — each ONCE, flat, after teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


# ═══ driver ═════════════════════════════════════════════════════════════════
SECTIONS = {"q": y_queue, "w": y_work, "l": y_laptop, "f": y_failure, "t": y_table, "s": y_summaries, "u": y_uit,
            "h": None, "j": None}


async def run(name, fn, *args):
    t0 = time.monotonic()
    try:
        await fn(*args)
    except Exception as exc:  # noqa: BLE001
        check(False, f"section '{name}' raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"[INFO] {name}: {time.monotonic() - t0:.1f}s", flush=True)


async def real_worker(conn) -> str | None:
    return await platform(conn, f"""SELECT worker_id FROM portfolio.edgar_fetch_workers WHERE stopped_at IS NULL
                                 AND heartbeat_at > now() - interval '{BQ.WORKER_LOST_SECONDS} seconds'
                                 AND NOT starts_with(worker_id, $1) LIMIT 1""", WORKER_PREFIX, fetch="fetchval")


async def refuse_if_busy(conn) -> str | None:
    r = await platform(conn, f"""SELECT id FROM portfolio.edgar_pipeline_runs WHERE status = 'running'
                                 AND run_kind = ANY($1::text[]) AND NOT ({FIXTURE_RUNS_SQL})
                                 AND heartbeat_at >= now() - interval '{BQ.WORKER_LOST_SECONDS} seconds' LIMIT 1""",
                       list(BQ.QUEUED_KINDS), fetch="fetchval")
    return f"a real queued-kind run is running ({r})" if r else None


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    if "--cleanup-only" in sys.argv:
        log = await teardown()
        left = await leftovers(conn)
        print(f"[INFO] cleanup removed: {log}")
        check(not left, "--cleanup-only: zero fixture rows remain", f"{left}")
        await close_conn(conn)
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    busy = await refuse_if_busy(conn)
    if busy:
        print(f"[REFUSED] {busy}")
        await close_conn(conn)
        return 2
    only = None
    if "--section" in sys.argv:
        only = sys.argv[sys.argv.index("--section") + 1].lower()
        if only not in SECTIONS:
            print(f"[FAIL] unknown section {only!r}; one of {sorted(SECTIONS)}")
            print("TOTAL: 0 PASS, 1 FAIL")
            return 1
    if only == "j":
        await close_conn(conn)
        regression()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 0 if _n_fail == 0 else 1
    install_mocks()
    started = await conn.fetchval("SELECT now()")
    seeded = False
    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the app connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
        await run("pre-teardown", teardown)
        check(not await leftovers(conn), "pre-run cleanup: zero fixture rows before seeding")
        S["live_before"] = await real_status_counts(conn)
        S["runs_before"] = await real_runs_fingerprint(conn, started)
        await seed_common(conn)
        seeded = True
        for k, fn in SECTIONS.items():
            if fn is not None and (only is None or only == k):
                if k == "l" and (w := await real_worker(conn)):
                    check(False, "section l NOT RUN: a real laptop worker is heartbeating and would claim the "
                          "laptop-target fixture runs — stop it, then run --section l", f"{w}")
                    continue
                await run(k, fn, conn)
        if only in (None, "h"):
            await run("live", y_live, conn)
        check(all("sec.gov" in u for u in SEC_LOG), "every SEC request went to the mock transport (no network)",
              f"{len(SEC_LOG)} mocked requests")
    finally:
        if seeded:
            section("Teardown — zero fixture rows; real index statuses and real runs unchanged")
            await close_app_pool()
            try:
                log = await teardown()
                print(f"[INFO] teardown removed: {log}")
                left = await leftovers(conn)
                check(not left, "zero fixture rows remain", f"{left}")
                after = await real_status_counts(conn)
                check(after == S["live_before"], "READ-ONLY: the REAL index status counts are unchanged by this run",
                      f"{S['live_before']} → {after}")
                runs_after = await real_runs_fingerprint(conn, started)
                check(runs_after == S["runs_before"], "the real run rows that existed before are unchanged "
                      "(count + checksum of id / status / finish / heartbeat)", f"{S['runs_before'][0]} → {runs_after[0]}")
            except Exception as exc:  # noqa: BLE001
                check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
        await close_conn(conn)
    if only is None:
        if os.environ.get("VERIFY_NESTED") == "1":
            print("[SKIP] nested run")
        else:
            try:
                regression()
            except Exception as exc:  # noqa: BLE001
                check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
