"""verify_bulkfetch.py — BULKFETCH: the bulk EDGAR downloader (Part A) and the
gold review screen fixes (Part B).

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_bulkfetch.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on
any failure. Hydrates secrets from Doppler over HTTPS (_db_bootstrap).

NO NETWORK. The SEC is an ``httpx.MockTransport`` (every request logged with its
time and Range header); R2 is two dicts behind ``services.storage.upload_bytes``
/ ``download_bytes`` (``get_s3_client`` is replaced by a function that RAISES, so
a real R2 call cannot happen even by accident); every model entry point
(``note_extraction.proxy.chat`` / ``.jev``) is replaced by a counter that raises.
Real spend: $0.

FIXTURES — all tagged, all torn down at the START and at the END:
  * manifest rows / filer rows / reference_filings rows whose accession starts
    ACC_PREFIX (``-91-``: no other verify uses it — grep'd -55- -58- -61- -72-
    -77- -84- -88- -99-);
  * structured_note_issuers rows in issuer groups named 'VERIFY BF …' with
    9-digit CIKs starting 99091 (no real filer has such a CIK);
  * edgar_sources rows named 'verify_bulkfetch…';
  * edgar_pipeline_runs rows trigger_source 'verify' (a live CHECK value) whose
    details.bulk.source is a fixture source, and their 'bulk_fetch:<id>' leases;
  * a fixture org, a super-admin user and an org-admin user (auth0_sub = the
    literal sub the mocked token carries; teardown finds them by id, sub and
    email pattern), their roles / grants;
  * gold values / readings on the fixture filing only.
Every id is a full UUID built from a counter — never a slice of a UUID.

NEVER TOUCHED: real note_gold_values (count + checksum before/after), the
selection policies (v2 is applied to fixtures with activate_policy=False and the
ACTIVE version stamped — activating v2 here would retire the live v1 for the
nightly job), every real manifest row.

Teardown is FK child-before-parent from pg_constraint (contype::text), then every
touched table is re-read for its exact pre-test count. The nested regressions
(verify_edgarpipelinea.py, verify_edgarcohorts.py, verify_notefix2.py) run ONCE
each as flat subprocesses, 600 s timeout, ONLY after this script's teardown left
zero fixture rows.
"""
from __future__ import annotations

import asyncio
import gzip
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
DISCOVERY_DOC = REPO / "docs" / "BULKFETCH_DISCOVERY.md"
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
NOTE_FIELDS_DOC = REPO / "docs" / "NOTE_FIELDS.md"

TAG = "bulkfetch-verify"
ACC_PREFIX = "9999999999-91-"
SRC_MAIN = "verify_bulkfetch"
SRC_FWP = "verify_bulkfetch_fwp"
GROUP_PREFIX = "VERIFY BF "
R2_PREFIX = "reference/edgar-verify-bulkfetch"
BUCKET = "verify-bulkfetch-bucket"
ORG = UUID("99000000-0000-0000-0000-00000b0f0001")
U_SUPER = UUID("99000000-0000-0000-0000-00000b0f0011")
U_ORGADMIN = UUID("99000000-0000-0000-0000-00000b0f0012")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN]
FIXTURE_EMAIL_LIKE = "bulkfetch_%@test.local"
HEADERS = {"Authorization": "Bearer verify-token"}
FID_GOLD = UUID("99000000-0000-0000-0000-00000b0f1001")
NEW_LABEL = "Underwriting discount / agent commission"

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
    return f"bulkfetch_{uid.hex}"


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
    return f"99091{_cik_n:04d}"


def nodash(acc: str) -> str:
    return acc.replace("-", "")


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.reference_filing_supplements", "portfolio.note_term_readings", "portfolio.note_gold_values",
    "portfolio.edgar_index_filing_filers", "portfolio.edgar_index_filings", "portfolio.reference_filings",
    "portfolio.edgar_pipeline_runs", "portfolio.edgar_pipeline_lease", "portfolio.edgar_sources",
    "portfolio.structured_note_issuers",
)
COUNTED = TOUCHED + ("public.users", "public.organizations", "public.roles", "public.user_roles",
                     "portfolio.edgar_selection_policies", "portfolio.distribution_participants")


async def fixture_keys(conn) -> dict:
    filings = await rls_fetch(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                              ACC_PREFIX + "%")
    runs = await rls_fetch(conn, "SELECT id FROM portfolio.edgar_pipeline_runs WHERE trigger_source = 'verify' "
                                 "AND details->'bulk'->>'source' LIKE $1", SRC_MAIN + "%")
    return {"filings": [r["id"] for r in filings], "runs": [str(r["id"]) for r in runs]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    f, runs = k["filings"], k["runs"]
    return {
        "portfolio.reference_filing_supplements":
            ("reference_filing_id = ANY($1::uuid[]) OR supplement_accession LIKE $2", [f, ACC_PREFIX + "%"]),
        "portfolio.note_term_readings": ("reference_filing_id = ANY($1::uuid[])", [f]),
        "portfolio.note_gold_values": ("reference_filing_id = ANY($1::uuid[])", [f]),
        "portfolio.edgar_index_filing_filers": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.edgar_index_filings": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.reference_filings": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.edgar_pipeline_runs": ("id::text = ANY($1::text[])", [runs]),
        "portfolio.edgar_pipeline_lease": ("lease_name LIKE 'bulk_fetch:%' AND holder = ANY($1::text[])", [runs]),
        "portfolio.edgar_sources": ("name LIKE $1", [SRC_MAIN + "%"]),
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
                   FROM portfolio.note_gold_values g
                  WHERE NOT EXISTS (SELECT 1 FROM portfolio.reference_filings r
                                     WHERE r.id = g.reference_filing_id AND r.accession_number LIKE $1)""",
        ACC_PREFIX + "%")
    return int(r["n"]), r["h"]


# ═══ Mocks: SEC, R2, models ════════════════════════════════════════════════
DOCS: dict[str, dict] = {}          # accession nodash -> {cik, doc, body, range, fail_status, fail_times, form}
INDEX_FILES: dict[str, str] = {}    # YYYYMMDD -> master.idx body
SEC_LOG: list[dict] = []
R2: dict[str, bytes] = {}
UPLOADS: list[tuple[str, int]] = []
DOWNLOADS: list[str] = []
MODEL_CALLS: list[str] = []
STAMPS: list[float] = []

_DOC_RE = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/(.+)$")
_DAILY_RE = re.compile(r"/Archives/edgar/daily-index/\d{4}/QTR\d/master\.(\d{8})\.idx$")


def sec_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    rng = request.headers.get("Range")
    entry = {"t": time.monotonic(), "url": str(request.url), "range": rng, "ua": request.headers.get("User-Agent")}
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
    if d.get("fail_times", 0) != 0 and tail == "index.json":
        if d["fail_times"] > 0:
            d["fail_times"] -= 1
        return httpx.Response(d.get("fail_status", 503), headers={"Retry-After": "0"})
    if tail == "index.json":
        return httpx.Response(200, json={"directory": {"item": [{"name": d["doc"], "size": len(d["body"])}]}})
    if tail.endswith("-index-headers.html"):
        return httpx.Response(200, text=f"<TYPE>{d.get('form', '424B2')}\n<FILE-NUMBER>333-999999\n")
    if tail == d["doc"]:
        entry["doc_get"] = True
        body = d["body"]
        if rng and d.get("range", "honour") == "honour":
            n = int(rng.split("-")[1]) + 1
            return httpx.Response(206, content=body[:n],
                                  headers={"Content-Range": f"bytes 0-{min(n, len(body)) - 1}/{len(body)}"})
        return httpx.Response(200, content=body)
    return httpx.Response(404)


def sec_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(sec_handler))


def install_mocks():
    from services import edgar_fetch, storage
    from services.note_extraction import proxy

    def fake_upload(key, data, content_type=None, bucket=None):
        R2[key] = bytes(data)
        UPLOADS.append((key, len(data)))
        return key

    def fake_download(key, bucket=None):
        DOWNLOADS.append(key)
        return R2[key]

    def no_s3(*_a, **_k):
        raise AssertionError("a real R2 client was requested during verify_bulkfetch")

    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_s3_client = no_s3

    async def no_chat(*_a, **_k):
        MODEL_CALLS.append("chat")
        raise AssertionError("proxy.chat must never be called by bulkfetch")

    async def no_jev(*_a, **_k):
        MODEL_CALLS.append("jev")
        raise AssertionError("proxy.jev must never be called by bulkfetch")

    proxy.chat = no_chat
    proxy.jev = no_jev

    orig = edgar_fetch.RateLimiter.acquire

    async def stamped(self):
        await orig(self)
        STAMPS.append(self._calls[-1])      # the limiter's OWN recorded time for this slot

    edgar_fetch.RateLimiter.acquire = stamped
    os.environ.setdefault("EDGAR_USER_AGENT", "Hollisworks verify_bulkfetch (verify@test.local)")


# ═══ Fixture builders ══════════════════════════════════════════════════════
COVER_FINAL = ("<html><body><p>PRICING SUPPLEMENT dated September 3, 2026</p>"
               "<p>Notes linked to the S&amp;P 500 Index. Barrier: 70% of the Initial Level.</p>{extra}</body></html>")
COVER_PRELIM = ("<html><body><p>Subject to Completion. PRELIMINARY PRICING SUPPLEMENT dated September 3, 2026</p>"
                "<p>Barrier: 70% of the Initial Level.</p></body></html>")
COVER_PRODUCT = ("<html><body><p>PRODUCT SUPPLEMENT No. 9 dated September 1, 2026</p>"
                 "<p>The applicable pricing supplement will describe the notes.</p></body></html>")
COVER_UNRECOGNISED = ("<html><body>" + "<div></div>" * 2000 +
                      "<p>PRICING SUPPLEMENT</p><p>Barrier: 70% of the Initial Level.</p></body></html>")


async def make_group(conn, name: str, n_ciks: int = 2) -> list[str]:
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


ACTIVE_V: int | None = None


async def make_filing(conn, cik: str, day: date, *, form: str = "424B2", status: str = "selected",
                      body: str | None = None, rng: str = "honour", fail_times: int = 0,
                      fail_status: int = 503, reference: dict | None = None, kind: str | None = None) -> str:
    acc = next_acc()
    async with conn.transaction():
        await _super(conn)
        ref_id = None
        if reference is not None:
            ref_id = await conn.fetchval(
                """INSERT INTO portfolio.reference_filings
                       (cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                        extraction_status, extracted_text, text_r2_key, content_encoding)
                   VALUES ($1, 'VERIFY BF fixture', $2, $3, $4, 'doc.htm', 'https://example.invalid/bulkfetch',
                           $5, $6, $7, 'gzip') RETURNING id""",
                cik, form, acc, day, reference.get("extraction_status", "fetched"),
                reference.get("extracted_text"), reference.get("text_r2_key"))
        await conn.execute(
            """INSERT INTO portfolio.edgar_index_filings
                   (accession_number, form_type, filing_date, index_quarter, submission_path, filer_count,
                    pipeline_status, selection_policy_version, primary_issuer_cik, reference_filing_id,
                    document_kind, status_reason)
               VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8, $9, $10, $11)""",
            acc, form, day, f"{day.year}Q{(day.month - 1) // 3 + 1}", f"edgar/data/{cik}/{acc}.txt", status,
            None if status in ("discovered", "fetched") else ACTIVE_V, cik, ref_id, kind, f"{TAG} fixture")
        await conn.execute(
            "INSERT INTO portfolio.edgar_index_filing_filers (accession_number, cik, company_name) VALUES ($1, $2, $3)",
            acc, cik, "VERIFY BF fixture filer")
    if body is not None:
        DOCS[nodash(acc)] = {"cik": cik, "doc": "doc.htm", "body": body.encode(), "range": rng,
                             "fail_times": fail_times, "fail_status": fail_status, "form": form}
    return acc


async def statuses(conn, accs: list[str]) -> dict[str, dict]:
    rows = await rls_fetch(
        conn, """SELECT accession_number, pipeline_status, document_kind, reference_filing_id, status_reason,
                        claimed_by, selection_policy_version
                   FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])""", accs)
    return {r["accession_number"]: dict(r) for r in rows}


def doc_gets(acc: str, *, full_only: bool = False) -> int:
    n = nodash(acc)
    return sum(1 for e in SEC_LOG if e.get("acc") == n and e.get("doc_get") and (not full_only or not e["range"]))


OUT: list[str] = []


def opts(eb, group: str, d_from: date, d_to: date, **kw):
    base = dict(source=SRC_MAIN, banks=[GROUP_PREFIX + group], date_from=d_from, date_to=d_to,
                bucket=BUCKET, r2_prefix=R2_PREFIX, trigger_source="verify", concurrency=1)
    base.update(kw)
    return eb.RunOptions(**base)


async def run_bulk(pool, eb, o, *, client=None, after_filing=None):
    client = client or sec_client()
    r = eb.Runner(pool, o, client=client, out=OUT.append, backoff_base=0.001, after_filing=after_filing)
    try:
        stats = await r.run()
    finally:
        await client.aclose()
    return r, stats


# ═══ Sections ══════════════════════════════════════════════════════════════
D0 = date(2026, 9, 1)


def y_task1() -> None:
    section("[Y] Task 1 — the nine discovery findings are reported")
    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    heads = re.findall(r"^## (1[a-i])\.", text, re.M)
    check(heads == [f"1{c}" for c in "abcdefghi"], "docs/BULKFETCH_DISCOVERY.md reports 1a..1i in order", f"{heads}")
    m = re.search(r"exactly \*\*3 requests per filing\*\*", text)
    check(m is not None, "1a states the MEASURED request count per filing", m.group(0) if m else "no match")
    m = re.search(r"\*\*1\.91 s per filing\*\*", text)
    check(m is not None, "1a states the measured per-filing time", m.group(0) if m else "no match")
    m = re.search(r"\*\*Where the time goes\.\*\*.*?SEC round-trip.*?R2 PUTs.*?pooler round trips.*?gzip-9",
                  text, re.S)
    check(m is not None, "1a gives the per-filing time breakdown (SEC, R2, DB, CPU)", "found" if m else "no match")
    rules = [r for r in ("preliminary pricing supplement", "subject to completion", "preliminary terms",
                         "`pricing supplement`", "`product supplement`", "`underlying supplement`", "`term sheet`")
             if r in text]
    check(len(rules) == 7, "1c lists the classifier's rules", f"{rules}")


async def y_sources(conn, eb) -> None:
    section("[Y] Sources — form types and filers come from configuration")
    src = await eb.load_source(conn, "structured_notes")
    check(src.form_types == ("424B2",) and src.filer_source == "structured_note_issuers",
          "the seeded 'structured_notes' source is read from portfolio.edgar_sources",
          f"{src.form_types} {src.filer_source} {src.classifier}")
    ciks = await make_group(conn, "Sources")
    a424 = await make_filing(conn, ciks[0], D0, form="424B2")
    afwp = await make_filing(conn, ciks[0], D0, form="FWP")
    s_main = await eb.load_source(conn, SRC_MAIN)
    s_fwp = await eb.load_source(conn, SRC_FWP)
    for s, want, other in ((s_main, a424, afwp), (s_fwp, afwp, a424)):
        insts = await eb.resolve_institutions(conn, s, [GROUP_PREFIX + "Sources"])
        rows = await eb.scope_accessions(conn, eb.Scope(s, insts, D0, D0))
        accs = [r["accession_number"] for r in rows]
        check(accs == [want], f"source {s.name} ({list(s.form_types)}) selects only its own form",
              f"{accs} (excluded {other})")
    try:
        await eb.load_source(conn, "no_such_source")
        check(False, "an unknown --source is refused")
    except eb.BulkUsageError as exc:
        check("structured_notes" in str(exc), "an unknown --source is refused, listing the active sources", str(exc))


async def y_institutions(conn, eb) -> None:
    section("[Y] Institutions — names resolve to every filer CIK; unknown names list the valid ones")
    src = await eb.load_source(conn, "structured_notes")
    insts = await eb.resolve_institutions(conn, src, ["jpmorgan"])
    want = {r["filer_cik"] for r in await rls_fetch(
        conn, "SELECT filer_cik FROM portfolio.structured_note_issuers WHERE issuer_group = 'JPMorgan' "
              "AND include_status = ANY($1::text[])", list(src.filer_include_status))}
    got = set(insts[0].ciks) if insts else set()
    check(bool(want) and got == want, "--banks \"jpmorgan\" (lower case) resolves every JPMorgan filer CIK",
          f"{sorted(got)} vs {sorted(want)}")
    ciks = await make_group(conn, "Inst", 3)
    s_main = await eb.load_source(conn, SRC_MAIN)
    fx = await eb.resolve_institutions(conn, s_main, [(GROUP_PREFIX + "Inst").lower()])
    check(bool(fx) and set(fx[0].ciks) == set(ciks), "a fixture group resolves to exactly its fixture CIKs",
          f"{fx[0].ciks if fx else None}")
    try:
        await eb.resolve_institutions(conn, src, ["JPMorgan", "Not A Bank"])
        check(False, "an unknown institution is an error")
    except eb.BulkUsageError as exc:
        check("Not A Bank" in str(exc) and "Goldman Sachs" in str(exc),
              "an unknown institution errors and lists the valid names", str(exc)[:200])
    for i, c in enumerate(ciks):
        for _ in range(i + 1):
            await make_filing(conn, c, D0)
    out: list[str] = []
    res = await eb.dry_run(conn, eb.Scope(s_main, fx, D0, D0), rate=8, concurrency=6, peek=True, out=out.append)
    text = "\n".join(out)
    lines = [ln for ln in out if any(c in ln for c in ciks)]
    ok = all(re.search(rf"CIK {c}\s+.*Filer {i + 1}.*\s{i + 1}\s*$", text, re.M) for i, c in enumerate(ciks))
    check(ok and len(lines) == 3, "the dry run prints every filer entity with its CIK and count", " | ".join(lines))
    check(res["total"] == 6, "the dry run's scope total equals the fixture filings created", f"{res['total']}")


async def y_dates(conn, eb) -> None:
    section("[Y] Dates — inclusive range; a --to past the index stops with the refresh message")
    ciks = await make_group(conn, "Dates", 1)
    inside = [await make_filing(conn, ciks[0], d) for d in (date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 20))]
    outside = [await make_filing(conn, ciks[0], d) for d in (date(2026, 8, 31), date(2026, 9, 21))]
    s = await eb.load_source(conn, SRC_MAIN)
    insts = await eb.resolve_institutions(conn, s, [GROUP_PREFIX + "Dates"])
    rows = await eb.scope_accessions(conn, eb.Scope(s, insts, date(2026, 9, 1), date(2026, 9, 20)))
    got = {r["accession_number"] for r in rows}
    check(got == set(inside) and not (got & set(outside)),
          "--from 2026-09-01 --to 2026-09-20 selects exactly the 3 fixtures in range (both ends inclusive)",
          f"{len(got)} selected; outside excluded: {not (got & set(outside))}")
    last = await eb.last_indexed_date(conn)
    try:
        await eb.check_dates_indexed(conn, last + timedelta(days=1))
        check(False, "a --to after the last indexed date is refused")
    except eb.BulkUsageError as exc:
        check("--refresh-index" in str(exc), "a --to after the last indexed date stops and says run --refresh-index",
              str(exc).splitlines()[0])


async def y_scope_order(conn, eb) -> None:
    section("[Y] Scope and order — institution order as given, newest first; the dry run writes nothing")
    a = await make_group(conn, "OrderA", 1)
    b = await make_group(conn, "OrderB", 1)
    a_accs = [await make_filing(conn, a[0], D0 + timedelta(days=i)) for i in range(3)]
    b_accs = [await make_filing(conn, b[0], D0 + timedelta(days=i)) for i in range(2)]
    s = await eb.load_source(conn, SRC_MAIN)
    insts = await eb.resolve_institutions(conn, s, [GROUP_PREFIX + "OrderB", GROUP_PREFIX + "OrderA"])
    rows = await eb.scope_accessions(conn, eb.Scope(s, insts, D0, D0 + timedelta(days=5)))
    got = [r["accession_number"] for r in rows]
    want = list(reversed(b_accs)) + list(reversed(a_accs))
    check(got == want, "scope = the named institutions' selected filings, institution (as given) then newest",
          f"{len(got)} rows, order ok={got == want}")
    before = await statuses(conn, got)
    runs_before = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    await eb.dry_run(conn, eb.Scope(s, insts, D0, D0 + timedelta(days=5)), rate=8, concurrency=6, peek=True,
                     out=lambda *_: None)
    after = await statuses(conn, got)
    runs_after = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs")
    check(before == after and runs_before == runs_after, "the dry run wrote nothing (rows and run table unchanged)",
          f"runs {runs_before}->{runs_after}")


async def y_peek(conn, pool, eb) -> None:
    section("[Y] Peek — preliminary skipped on the cover; final / unrecognised / Range-ignored fall through")
    ciks = await make_group(conn, "Peek", 1)
    prelim = await make_filing(conn, ciks[0], D0 + timedelta(days=4), body=COVER_PRELIM)
    final = await make_filing(conn, ciks[0], D0 + timedelta(days=3), body=COVER_FINAL.format(extra=""))
    unrec = await make_filing(conn, ciks[0], D0 + timedelta(days=2), body=COVER_UNRECOGNISED)
    ignored = await make_filing(conn, ciks[0], D0 + timedelta(days=1), body=COVER_FINAL.format(extra=""),
                                rng="ignore")
    log0, up0, st0 = len(SEC_LOG), len(UPLOADS), len(STAMPS)
    r, stats = await run_bulk(pool, eb, opts(eb, "Peek", D0, D0 + timedelta(days=5)))
    st = await statuses(conn, [prelim, final, unrec, ignored])
    p = st[prelim]
    check(p["pipeline_status"] == "not_pricing_supplement" and p["document_kind"] == "preliminary_pricing_supplement",
          "a 206 cover reading 'Preliminary Pricing Supplement' is recorded with its kind and a live-CHECK status",
          f"{p['pipeline_status']} / {p['document_kind']} / {p['status_reason'][:60]}")
    check(doc_gets(prelim, full_only=True) == 0 and p["reference_filing_id"] is None
          and not any(nodash(prelim) in k or prelim in k for k, _ in UPLOADS[up0:]),
          "…with no full GET and no R2 write", f"full GETs {doc_gets(prelim, full_only=True)}")
    check(st[final]["pipeline_status"] == "ready_for_extraction" and doc_gets(final, full_only=True) == 1,
          "a final continues to the full fetch and is stored", f"{st[final]['pipeline_status']}")
    check(st[unrec]["document_kind"] == "pricing_supplement" and doc_gets(unrec, full_only=True) == 1,
          "an unrecognised cover falls through to the full fetch (never lost)",
          f"{st[unrec]['document_kind']}, peek_fallthrough={stats.peek_fallthrough}")
    check(st[ignored]["pipeline_status"] == "ready_for_extraction" and doc_gets(ignored) == 1
          and stats.peek_range_ignored == 1,
          "a 200 (Range ignored) is handled as the full fetch, with no second GET",
          f"doc GETs {doc_gets(ignored)}, range_ignored={stats.peek_range_ignored}")
    n_req = len(SEC_LOG) - log0
    check(n_req > 0 and len(STAMPS) - st0 == n_req,
          "every request, peeks included, took a slot from the shared rate limiter",
          f"{n_req} requests, {len(STAMPS) - st0} limiter slots, {sum(1 for e in SEC_LOG[log0:] if e['range'])} peeks")
    check(all(e["ua"] for e in SEC_LOG[log0:]), "every request carried the declared User-Agent")
    # storage
    ups = UPLOADS[up0:]
    gz_ok = all(k.endswith(".gz") for k, _ in ups) and all(gzip.decompress(R2[k]) for k, _ in ups)
    check(len(ups) == 6 and gz_ok, "documents and text went to (mocked) R2 gzipped (2 objects x 3 stored filings)",
          f"{len(ups)} uploads")
    ext = await rls_fetch(conn, "SELECT extracted_text IS NULL AS n, text_r2_key FROM portfolio.reference_filings "
                                "WHERE accession_number = ANY($1::text[])", [final, unrec, ignored])
    check(len(ext) == 3 and all(r["n"] and r["text_r2_key"] for r in ext),
          "extracted_text stays NULL; the text lives in R2 only", f"{[(r['n'], bool(r['text_r2_key'])) for r in ext]}")
    check(stats.bytes_uploaded == sum(n for _, n in ups) and stats.bytes_uploaded > 0,
          "the summary's bytes uploaded equal the mocked uploads", f"{stats.bytes_uploaded} vs {sum(n for _, n in ups)}")

    # --no-peek
    np_ciks = await make_group(conn, "NoPeek", 1)
    np_prelim = await make_filing(conn, np_ciks[0], D0, body=COVER_PRELIM)
    log0 = len(SEC_LOG)
    await run_bulk(pool, eb, opts(eb, "NoPeek", D0, D0, peek=False))
    st = await statuses(conn, [np_prelim])
    check(not any(e["range"] for e in SEC_LOG[log0:]) and doc_gets(np_prelim, full_only=True) == 1
          and st[np_prelim]["reference_filing_id"] is not None,
          "--no-peek: no Range request; the preliminary is downloaded and stored in full",
          f"{st[np_prelim]['pipeline_status']}")
    # --keep-preliminaries
    kp = await make_group(conn, "KeepPrelim", 1)
    kp_prelim = await make_filing(conn, kp[0], D0, body=COVER_PRELIM)
    await run_bulk(pool, eb, opts(eb, "KeepPrelim", D0, D0, keep_preliminaries=True))
    st = await statuses(conn, [kp_prelim])
    check(doc_gets(kp_prelim) == 2 and doc_gets(kp_prelim, full_only=True) == 1
          and st[kp_prelim]["reference_filing_id"] is not None,
          "--keep-preliminaries: the peeked preliminary is downloaded in full anyway",
          f"doc GETs {doc_gets(kp_prelim)}; {st[kp_prelim]['pipeline_status']}")


async def y_kinds(conn, pool, eb) -> None:
    section("[Y] --kinds — a product supplement is skipped with 'final'; two finals citing one supplement = ONE fetch")
    g = await make_group(conn, "Kinds", 1)
    ps = await make_filing(conn, g[0], D0, body=COVER_PRODUCT)
    up0 = len(UPLOADS)
    await run_bulk(pool, eb, opts(eb, "Kinds", D0, D0, kinds=["final"]))
    st = await statuses(conn, [ps])
    check(st[ps]["document_kind"] == "product_supplement" and doc_gets(ps, full_only=True) == 0
          and len(UPLOADS) == up0, "with --kinds final a product supplement is not downloaded",
          f"{st[ps]['pipeline_status']} / {st[ps]['document_kind']}")

    g2 = await make_group(conn, "Kinds2", 1)
    supp_acc = next_acc()
    supp_cik = g2[0]
    DOCS[nodash(supp_acc)] = {"cik": supp_cik, "doc": "ps9.htm", "body": COVER_PRODUCT.encode(), "range": "honour",
                              "form": "424B2"}
    link = (f"<p>Product supplement no. 9 dated September 1, 2026:</p>"
            f"<p>https://www.sec.gov/Archives/edgar/data/{supp_cik}/{nodash(supp_acc)}/ps9.htm</p>")
    fa = await make_filing(conn, g2[0], D0 + timedelta(days=1), body=COVER_FINAL.format(extra=link))
    fb = await make_filing(conn, g2[0], D0, body=COVER_FINAL.format(extra=link))
    _r, stats = await run_bulk(pool, eb, opts(eb, "Kinds2", D0, D0 + timedelta(days=1),
                                              kinds=["final", "product_supplement"], concurrency=2))
    n_fetch = doc_gets(supp_acc)
    links = await rls_fetch(
        conn, """SELECT s.resolution_method, s.supplement_accession, s.supplement_reference_filing_id
                   FROM portfolio.reference_filing_supplements s
                   JOIN portfolio.reference_filings r ON r.id = s.reference_filing_id
                  WHERE r.accession_number = ANY($1::text[])""", [fa, fb])
    check(n_fetch == 1, "two finals referencing the same supplement cause exactly ONE fetch of it", f"{n_fetch}")
    check(len(links) == 2 and all(lk["supplement_accession"] == supp_acc and lk["resolution_method"] == "link"
                                  for lk in links)
          and len({lk["supplement_reference_filing_id"] for lk in links}) == 1
          and links[0]["supplement_reference_filing_id"] is not None,
          "…and two links, both to the one stored supplement", f"{[dict(x) for x in links]}")
    check(stats.supplements.distinct_found == 1 and stats.supplements.fetched == 1,
          "the summary reports 1 distinct supplement found and fetched", f"{vars(stats.supplements)}")
    fc = await make_filing(conn, g2[0], D0 + timedelta(days=2), body=COVER_FINAL.format(extra=link))
    _r, stats2 = await run_bulk(pool, eb, opts(eb, "Kinds2", D0, D0 + timedelta(days=2),
                                               kinds=["final", "product_supplement"]))
    linked = await rls_val(
        conn, """SELECT s.supplement_accession FROM portfolio.reference_filing_supplements s
                   JOIN portfolio.reference_filings r ON r.id = s.reference_filing_id
                  WHERE r.accession_number = $1""", fc)
    check(doc_gets(supp_acc) == 1 and stats2.supplements.already_stored == 1 and linked == supp_acc,
          "a later run's final citing the already-stored supplement links to it without re-fetching",
          f"supplement GETs {doc_gets(supp_acc)}; {vars(stats2.supplements)}")


async def y_rate_limit(conn, pool, eb) -> None:
    section("[Y] Rate limit — 6 workers, instant mocked SEC, no 1-second window above --max-rps")
    g = await make_group(conn, "Rate", 1)
    for i in range(12):
        await make_filing(conn, g[0], D0 + timedelta(days=i % 5), body=COVER_FINAL.format(extra=""))
    s0 = len(STAMPS)
    max_rps = 4
    _r, stats = await run_bulk(pool, eb, opts(eb, "Rate", D0, D0 + timedelta(days=5), concurrency=6,
                                              max_rps=max_rps, peek=False))
    stamps = sorted(STAMPS[s0:])
    worst = max((sum(1 for u in stamps if t <= u < t + 1.0) for t in stamps), default=0)
    cap = max(1, max_rps // max(stats.live_runners, 1))
    check(len(stamps) == 36 and worst <= max_rps,
          f"{len(stamps)} requests by 6 workers: the busiest 1-second window held {worst} (cap {max_rps})",
          f"effective cap this run {cap} with {stats.live_runners} live runner(s)")
    try:
        eb.check_rate(9, False)
        check(False, "--max-rps 9 without the override is refused")
    except eb.BulkUsageError as exc:
        check("--allow-above-8" in str(exc), "--max-rps above 8 is refused without the explicit override", str(exc)[:120])
    check(eb.check_rate(9, True) == 9, "…and accepted with --allow-above-8")
    try:
        eb.check_rate(11, True)
        check(False, "--max-rps above 10 is refused even with the override")
    except eb.BulkUsageError:
        check(True, "--max-rps above the SEC's 10/s is refused even with the override")


async def y_resume(conn, pool, eb) -> None:
    section("[Y] Resume — a run stopped after K filings restarts with only the rest; two runners never share one")
    g = await make_group(conn, "Resume", 1)
    accs = [await make_filing(conn, g[0], D0 + timedelta(days=i), body=COVER_FINAL.format(extra="")) for i in range(6)]
    K = 2

    async def stop_after(runner):
        if runner.stats.done >= K:
            runner.stop.set()

    _r, s1 = await run_bulk(pool, eb, opts(eb, "Resume", D0, D0 + timedelta(days=6)), after_filing=stop_after)
    st = await statuses(conn, accs)
    left = [a for a in accs if st[a]["pipeline_status"] == "selected"]
    check(s1.done == K and len(left) == 4 and all(st[a]["claimed_by"] is None for a in accs),
          f"the first run stopped after {K}; the rest are still 'selected' and unclaimed", f"done {s1.done}")
    _r, s2 = await run_bulk(pool, eb, opts(eb, "Resume", D0, D0 + timedelta(days=6)))
    gets = {a: doc_gets(a) for a in accs}
    check(s2.done == 4 and all(v == 1 for v in gets.values()),
          "the restart processed only the remaining 4; every filing was downloaded exactly once", f"{gets}")

    g2 = await make_group(conn, "Two", 1)
    accs2 = [await make_filing(conn, g2[0], D0 + timedelta(days=i % 6), body=COVER_FINAL.format(extra=""))
             for i in range(10)]
    o = opts(eb, "Two", D0, D0 + timedelta(days=6), concurrency=3)
    (_ra, sa), (_rb, sb) = await asyncio.gather(run_bulk(pool, eb, o), run_bulk(pool, eb, o))
    gets2 = [doc_gets(a) for a in accs2]
    check(sa.done + sb.done == 10 and all(v == 1 for v in gets2),
          "two runners at once: 10 filings between them, each downloaded exactly once",
          f"runner A {sa.done}, runner B {sb.done}, GETs {gets2}")


async def y_failures(conn, pool, eb) -> None:
    section("[Y] Failures — 429 retried with backoff; 3 failures = fetch_failed; N consecutive stop the run")
    g = await make_group(conn, "Fail", 1)
    retry = await make_filing(conn, g[0], D0 + timedelta(days=1), body=COVER_FINAL.format(extra=""),
                              fail_times=2, fail_status=429)
    bad = await make_filing(conn, g[0], D0, body=COVER_FINAL.format(extra=""), fail_times=-1, fail_status=503)
    r, _s = await run_bulk(pool, eb, opts(eb, "Fail", D0, D0 + timedelta(days=1)))
    st = await statuses(conn, [retry, bad])
    check(st[retry]["pipeline_status"] == "ready_for_extraction" and r.sec.retries >= 2,
          "a mocked 429 (twice) is retried with backoff and the filing then succeeds",
          f"retries {r.sec.retries}, {st[retry]['pipeline_status']}")
    check(st[bad]["pipeline_status"] == "fetch_failed" and "3 attempts" in (st[bad]["status_reason"] or ""),
          "three failures make the filing fetch_failed with the reason", (st[bad]["status_reason"] or "")[:120])

    g2 = await make_group(conn, "Stop", 1)
    accs = [await make_filing(conn, g2[0], D0 + timedelta(days=i), body=COVER_FINAL.format(extra=""), fail_times=-1)
            for i in range(5)]
    _r, s = await run_bulk(pool, eb, opts(eb, "Stop", D0, D0 + timedelta(days=5), stop_after_failures=3))
    st = await statuses(conn, accs)
    failed = [a for a in accs if st[a]["pipeline_status"] == "fetch_failed"]
    untouched = [a for a in accs if st[a]["pipeline_status"] == "selected" and st[a]["claimed_by"] is None]
    check(len(failed) == 3 and len(untouched) == 2 and "3 consecutive failures" in (s.stop_reason or ""),
          "3 consecutive failures stop the run, say why, and release the unstarted claims",
          (s.stop_reason or "")[:100])


async def y_selection_v2(conn, eb) -> None:
    section("[Y] Selection v2 — a WF-style structured supplement moves; a plain MTN stays; other banks untouched")
    from services import edgar_pipeline as ep

    wf = await make_group(conn, "Wells", 1)
    other = await make_group(conn, "OtherBank", 1)
    ml_text = ("PRICING SUPPLEMENT Market Linked Securities — Starting Value, Ending Value and a Threshold "
               "Value of 70% of the Starting Value. Maximum Return 25%.")
    mtn_text = "PRICING SUPPLEMENT Fixed Rate Medium-Term Notes, Series S. Interest rate 4.50% per annum."
    for key, text in (("wf", ml_text), ("mtn", mtn_text), ("oth", ml_text)):
        R2[f"{R2_PREFIX}/sel/{key}.txt.gz"] = gzip.compress(text.encode())
    s_wf = await make_filing(conn, wf[0], D0, status="prefilter_skipped", kind="pricing_supplement",
                             reference={"text_r2_key": f"{R2_PREFIX}/sel/wf.txt.gz"})
    s_mtn = await make_filing(conn, wf[0], D0, status="prefilter_skipped", kind="pricing_supplement",
                              reference={"text_r2_key": f"{R2_PREFIX}/sel/mtn.txt.gz"})
    s_fwp = await make_filing(conn, wf[0], D0, form="FWP", status="not_selected")
    s_oth = await make_filing(conn, other[0], D0, status="prefilter_skipped", kind="pricing_supplement",
                              reference={"text_r2_key": f"{R2_PREFIX}/sel/oth.txt.gz"})
    groups = [GROUP_PREFIX + "Wells"]
    fence = [s_wf, s_mtn, s_fwp, s_oth]
    before = await statuses(conn, fence)
    plan = await eb.plan_selection_v2(conn, groups=groups, bucket=BUCKET, accessions=fence)
    out: list[str] = []
    eb.print_selection_plan(plan, out=out.append)
    after_dry = await statuses(conn, fence)
    check(before == after_dry and any("2026" in ln for ln in out),
          "the dry run prints the counts by year and changes nothing", " | ".join(out)[:200])
    res = await eb.apply_selection_v2(conn, plan, activate_policy=False, stamp_version=ACTIVE_V)
    st = await statuses(conn, fence)
    check(st[s_wf]["pipeline_status"] == "ready_for_extraction",
          "the WF-style structured-note supplement moves forward", f"{st[s_wf]['status_reason'][:100]}")
    check(st[s_mtn]["pipeline_status"] == "prefilter_skipped", "the plain medium-term note stays excluded")
    check(st[s_fwp]["pipeline_status"] == "not_selected", "a WF FWP stays not_selected (form not in policy)")
    check(st[s_oth] == before[s_oth], "another bank's identical row is untouched (not in the v2 groups)")
    check(res["ready_for_extraction"] == 1 and res["selected"] == 0, "apply reports exactly the planned moves",
          f"{res}")
    v2 = eb.build_v2_rules({"form_types": ["424B2"]}, ["Wells Fargo", "Deutsche Bank"])
    check(set(v2) - ep._POLICY_KEYS == set() and ep.prefilter_terms_for(v2, "wells fargo") != ()
          and ep.prefilter_terms_for(v2, "JPMorgan") == (),
          "the v2 rules pass the policy-key check and grant the extra terms to WF/DB only")
    find("selection v2 here is applied with activate_policy=False and the ACTIVE version stamped",
         "activating v2 inside a verify would retire the live v1 policy for the nightly job; the operator's "
         "--selection-v2 path activates it")


async def y_reprocess(conn, eb) -> None:
    section("[Y] --reprocess-fetched — stuck 'fetched' rows classified from stored text, zero SEC requests")
    g = await make_group(conn, "Reproc", 1)
    final_text = "PRICING SUPPLEMENT Notes linked to the Index. Barrier: 70% of the Initial Level."
    a = await make_filing(conn, g[0], D0, status="fetched", reference={"extracted_text": final_text,
                                                                         "extraction_status": "extracted"})
    b = await make_filing(conn, g[0], D0, form="FWP", status="fetched",
                          reference={"extracted_text": "FREE WRITING PROSPECTUS term sheet", "extraction_status": "extracted"})
    log0, dl0 = len(SEC_LOG), len(DOWNLOADS)
    res = await eb.reprocess_fetched(conn, accessions=[a, b], bucket=BUCKET, out=lambda *_: None)
    st = await statuses(conn, [a, b])
    check(st[a]["pipeline_status"] == "ready_for_extraction" and st[a]["document_kind"] == "pricing_supplement",
          "a stored final pricing supplement moves to ready_for_extraction", f"{st[a]['pipeline_status']}")
    check(st[b]["pipeline_status"] == "not_selected" and st[b]["selection_policy_version"] == ACTIVE_V,
          "an FWP the policy would not select becomes not_selected (policy-stamped)", f"{st[b]['status_reason'][:80]}")
    check(len(SEC_LOG) == log0 and len(DOWNLOADS) == dl0 and res["rows"] == 2,
          "zero SEC requests (and no R2 read: the text is in the database)", f"{res}")


async def y_index_refresh(conn, eb) -> None:
    section("[Y] Index refresh — mocked daily files after the last date load once; selection on new rows only")
    g = await make_group(conn, "Refresh", 1)
    pre = await make_filing(conn, g[0], D0, status="discovered")
    last = await eb.last_indexed_date(conn)
    days = eb.weekdays(last + timedelta(days=1), last + timedelta(days=7))[:2]
    new_accs = [next_acc(), next_acc(), next_acc()]
    filed = last.isoformat()          # lines dated ON the last date: max(filing_date) never moves
    INDEX_FILES[days[0].strftime("%Y%m%d")] = (
        "CIK|Company Name|Form Type|Date Filed|Filename\n-----\n"
        f"{g[0]}|VERIFY BF Refresh Filer 1|424B2|{filed}|edgar/data/{g[0]}/{new_accs[0]}.txt\n"
        f"{g[0]}|VERIFY BF Refresh Filer 1|FWP|{filed}|edgar/data/{g[0]}/{new_accs[1]}.txt\n")
    INDEX_FILES[days[1].strftime("%Y%m%d")] = (
        f"{g[0]}|VERIFY BF Refresh Filer 1|424B2|{filed}|edgar/data/{g[0]}/{new_accs[2]}.txt\n")
    through = days[1]
    out: list[str] = []
    dry = await eb.refresh_index(conn, None, through=through, dry_run=True, out=out.append)
    check(dry["files"] == len(eb.weekdays(last, through)) and str(last) in out[0] and str(through) in out[0],
          "--refresh-index --dry-run prints the date span and the number of index files", out[0])
    client = sec_client()
    try:
        r1 = await eb.refresh_index(conn, client, through=through, out=out.append)
        r2 = await eb.refresh_index(conn, client, through=through, out=out.append)
    finally:
        await client.aclose()
    rows = await statuses(conn, new_accs)
    check(sorted(r1["new_accessions"]) == sorted(new_accs) and len(rows) == 3,
          "the mocked index files after the last date loaded exactly once", f"new {r1['new_filings']}")
    check(r2["new_filings"] == 0, "a second run adds zero rows", f"{r2['new_filings']}")
    sel = {a: rows[a]["pipeline_status"] for a in new_accs}
    check(sel[new_accs[0]] == "selected" and sel[new_accs[2]] == "selected" and sel[new_accs[1]] == "not_selected",
          "selection was applied to the new rows (424B2 selected, FWP not)", f"{sel}")
    pre_st = await statuses(conn, [pre])
    check(pre_st[pre]["pipeline_status"] == "discovered", "a pre-existing 'discovered' row was NOT selected by the refresh")
    check(await eb.last_indexed_date(conn) == last, "the last indexed date did not move (fixture lines dated on it)")


def y_no_model() -> None:
    section("[Y] Part A makes zero model calls")
    check(MODEL_CALLS == [], "the mocked model client recorded no call during Part A", f"{MODEL_CALLS}")
    src = (HERE.parents[1] / "services" / "edgar_bulk.py").read_text()
    check(not re.search(r"^\s*(?:from|import)\s+.*(?:proxy|anthropic|openai|litellm|extraction)\b", src, re.M),
          "services/edgar_bulk.py imports no model client")


# ── Part B ─────────────────────────────────────────────────────────────────
async def seed_users(conn) -> None:
    from services.rbac import grant_org_admin
    roles_ok = allowed(await check_defs(conn, "users"), "role")
    check(not roles_ok or {"super_admin", "member"} <= roles_ok, "fixture users.role values satisfy the live CHECK",
          f"{sorted(roles_ok)}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "BULKFETCH Verify Fixture Org", f"{ORG}-bulkfetch")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""", uid, ORG, f"{sub}@test.local", sub, sub, role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)


GOLD_HTML = """<html><body><p>PRICING SUPPLEMENT</p>
<p>BofA Finance LLC Market-Linked Notes linked to the S&amp;P 500 Index and the Russell 2000 Index</p>
<p>CUSIP No.: 09709TJV4</p>
<p>Per Note $1,000.00 $32.50 $967.50</p>
<p>Total $860,000 $27,950 $832,050</p>
<p>MLPF&amp;S, an affiliate of BofA Finance LLC, is the selling agent.</p>
</body></html>"""


async def make_gold_filing(conn) -> UUID:
    key = f"{R2_PREFIX}/gold/doc.htm.gz"
    R2[key] = gzip.compress(GOLD_HTML.encode())
    acc = next_acc()
    await rls_exec(
        conn, """INSERT INTO portfolio.reference_filings
                     (id, cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                      r2_key, content_encoding, extraction_status)
                 VALUES ($1, '1682472', 'BofA Finance LLC', '424B2', $2, $3, 'doc.htm',
                         'https://example.invalid/bulkfetch', $4, 'gzip', 'fetched')""",
        FID_GOLD, acc, D0, key)
    return FID_GOLD


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


def gpath(field: str) -> str:
    return f"/api/v1/admin/note-extraction/gold/notes/{FID_GOLD}/fields/{field}"


async def open_gold(conn, field: str) -> list[dict]:
    rows = await rls_fetch(conn, """SELECT value, valid_to, metadata FROM portfolio.note_gold_values
                                     WHERE reference_filing_id = $1 AND field_key = $2 ORDER BY reviewed_at""",
                           FID_GOLD, field)
    return [dict(r) for r in rows]


async def y_list_editor(conn) -> None:
    section("[Y] List editor (API) — two underlyings rows stored; removing one supersedes; a bad kind is refused")
    rows2 = [{"name": "S&P 500 Index", "ticker": "SPX", "kind": "index", "initial_level": 2643.85},
             {"name": "Russell 2000 Index", "ticker": "RTY", "kind": "index", "initial_level": 1473.536}]
    code, body = await api(U_SUPER, "PUT", gpath("underlyings"), {"action": "corrected", "value": rows2})
    g = await open_gold(conn, "underlyings")
    cur = [x for x in g if x["valid_to"] is None]
    val = json.loads(cur[0]["value"]) if cur else []
    levels = sorted(float(m.get("initial_level")) for m in val)
    check(code == 200 and len(val) == 2 and levels == [1473.536, 2643.85],
          "saving two underlyings rows (SPX 2643.85, RTY 1473.536) stores both as ONE gold value",
          f"HTTP {code}; {[(m.get('ticker'), m.get('initial_level')) for m in val]}")
    code, _ = await api(U_SUPER, "PUT", gpath("underlyings"), {"action": "corrected", "value": rows2[:1]})
    g = await open_gold(conn, "underlyings")
    cur = [x for x in g if x["valid_to"] is None]
    check(code == 200 and len(g) == 2 and len(cur) == 1 and len(json.loads(cur[0]["value"])) == 1,
          "removing one row and saving supersedes correctly (old row closed, one open row of 1)",
          f"{len(g)} rows, {len(cur)} open")
    bad = [dict(rows2[0], kind="bond")]
    code, body = await api(U_SUPER, "PUT", gpath("underlyings"), {"action": "corrected", "value": bad})
    g2 = await open_gold(conn, "underlyings")
    check(code == 422 and len(g2) == 2, "a row with an invalid kind is refused and nothing is written",
          f"HTTP {code}: {str(body.get('detail', body))[:120]}")


async def y_identifiers(conn) -> None:
    section("[Y] CUSIP / ISIN — check digit refused; ISIN without the CUSIP is a warning + needs_review")
    code, _ = await api(U_SUPER, "PUT", gpath("cusip"), {"action": "corrected", "value": "09709TJV4"})
    check(code == 200, "CUSIP 09709TJV4 is accepted", f"HTTP {code}")
    code, body = await api(U_SUPER, "PUT", gpath("cusip"), {"action": "corrected", "value": "09709TJV5"})
    detail = str(body.get("detail", ""))
    check(code == 422 and "check digit" in detail, "CUSIP 09709TJV5 is refused with a message", detail[:120])
    g = [x for x in await open_gold(conn, "cusip") if x["valid_to"] is None]
    check(len(g) == 1 and json.loads(g[0]["value"]) == "09709TJV4", "the refused CUSIP did not replace the good one")
    from services.note_extraction.rules import _isin_valid
    isin = next(f"US06745QAB1{d}" for d in range(10) if _isin_valid(f"US06745QAB1{d}"))
    code, body = await api(U_SUPER, "PUT", gpath("isin"), {"action": "corrected", "value": isin})
    g = [x for x in await open_gold(conn, "isin") if x["valid_to"] is None]
    meta = json.loads(g[0]["metadata"]) if g and isinstance(g[0]["metadata"], str) else (g[0]["metadata"] if g else {})
    check(code == 200 and bool(body.get("warnings")) and any("isin" in r for r in meta.get("needs_review_reasons", [])),
          "an ISIN not containing the note's CUSIP is SAVED with a warning and a needs_review reason",
          f"HTTP {code}; warnings {body.get('warnings')}")


async def y_totals(conn) -> None:
    section("[Y] Calculated totals — 860000 x 3.25% / 96.75%; stated $27,950 passes, $29,000 flags both numbers")
    from services.note_extraction import checks, derive, rules

    fees = {"min": 3.25, "max": 3.25, "bound": "exact"}
    proceeds = {"min": 96.75, "max": 96.75, "bound": "exact"}
    ds = {d.field_key: d for d in derive.dollar_totals(860000, fees, proceeds)}
    tf, pr = ds.get("total_fees_usd"), ds.get("proceeds_to_issuer_usd")
    check(tf is not None and tf.value["min"] == 27950 and tf.value["max"] == 27950 and tf.derived is True,
          "total_fees_usd = 27,950, marked derived", f"{tf.value if tf else None}")
    check(pr is not None and pr.value["min"] == 832050 and pr.derived is True,
          "proceeds_to_issuer_usd = 832,050, marked derived", f"{pr.value if pr else None}")
    reg = await rls_fetch(conn, "SELECT field_key, extraction_method, derived_from FROM portfolio.note_terms_field_registry "
                                "WHERE field_key = ANY($1::text[]) AND retired_at IS NULL",
                          ["total_fees_usd", "proceeds_to_issuer_usd"])
    check(len(reg) == 2 and all(r["extraction_method"] == "derived" for r in reg),
          "both are live registry rows with extraction_method 'derived'", f"{[dict(r) for r in reg]}")
    vals = {"total_fees_usd": tf.value, "proceeds_to_issuer_usd": pr.value}
    ok_stated = rules.find_stated_dollar_totals("Total $860,000 $27,950 $832,050")
    bad_stated = rules.find_stated_dollar_totals("Total $861,050 $29,000 $832,050")
    f_ok = checks.dollar_totals_match(vals, ok_stated)
    f_bad = checks.dollar_totals_match(vals, bad_stated)
    check(ok_stated is not None and f_ok is None, "a filing stating $27,950 passes the check", f"{ok_stated}")
    check(f_bad is not None and f_bad.needs_review and "$27,950" in f_bad.reason and "$29,000" in f_bad.reason,
          "a filing stating $29,000 flags needs_review naming both numbers", f_bad.reason if f_bad else "no finding")
    for k, v in (("issue_size", 860000), ("total_fees_pct", fees), ("proceeds_to_issuer_pct", proceeds)):
        await api(U_SUPER, "PUT", gpath(k), {"action": "corrected", "value": v})
    code, note = await api(U_SUPER, "GET", f"/api/v1/admin/note-extraction/gold/notes/{FID_GOLD}")
    calc = (note or {}).get("calculated") or {}
    check(code == 200 and ((calc.get("total_fees_usd") or {}).get("value") or {}).get("min") == 27950
          and calc.get("check") is None,
          "the review API shows the calculated totals from the reviewed values, and the stated-total check passes",
          f"HTTP {code}; {calc.get('total_fees_usd')}; check {calc.get('check')}")
    fields = {f["key"]: f for f in (note or {}).get("fields", [])}
    check(fields.get("total_fees_usd", {}).get("calculated") is True and fields.get("cusip", {}).get("calculated") is False,
          "the calculated fields are flagged calculated in the response; others are not")
    code, body = await api(U_SUPER, "PUT", gpath("total_fees_usd"), {"action": "corrected",
                                                                      "value": {"min": 1, "max": 1, "bound": "exact"}})
    check(code == 422 and "calculated" in str(body.get("detail", "")), "a gold write to a calculated field is refused",
          f"HTTP {code}")


async def y_normalisation(conn) -> None:
    section("[Y] Normalisation — issuer table / participants matches, affiliate flag, 'no match'")
    from services.note_extraction import normalise_names as nn
    from services.note_extraction import participants as parts

    issuers = await rls_fetch(conn, "SELECT filer_cik, issuer_group, filer_name, filer_role, credit_entity "
                                    "FROM portfolio.structured_note_issuers")
    issuers = [dict(r) for r in issuers]
    async with conn.transaction():
        await _super(conn)
        prow = await parts.load_participants(conn)
    m = nn.match_issuer("BofA Finance LLC", issuers, "Bank of America")
    check(m is not None and m["matched"] and m["issuer_group"] == "Bank of America" and m["needs_review_reason"] is None,
          "\"BofA Finance LLC\" matches the issuer table", f"{m}")
    a = nn.match_agent("MLPF&S", prow, "Bank of America")
    check(a is not None and a["matched"] and a["affiliate"] is True,
          "\"MLPF&S\" on a BofA note shows as an issuer affiliate", f"{a}")
    u = nn.match_agent("Acme Unknown Dealer Corp", prow, "Bank of America")
    check(u is not None and u["matched"] is False, "an unknown dealer shows 'no match'", f"{u}")
    g = nn.match_issuer("Some Other Bank AG", issuers, "Bank of America")
    check(g is not None and g["needs_review_reason"] is not None,
          "a guarantor matching no issuer row gives a needs_review reason", f"{g['needs_review_reason'] if g else None}")
    code, _ = await api(U_SUPER, "PUT", gpath("distribution"),
                        {"action": "corrected", "value": [{"name": "MLPF&S", "role": "issuer_affiliated_agent"},
                                                          {"name": "Acme Unknown Dealer Corp", "role": "dealer"}]})
    code2, note = await api(U_SUPER, "GET", f"/api/v1/admin/note-extraction/gold/notes/{FID_GOLD}")
    dist = ((note or {}).get("normalisation") or {}).get("distribution") or []
    check(code == 200 and code2 == 200 and len(dist) == 2 and dist[0]["affiliate"] is True and dist[1]["matched"] is False,
          "the review API returns the per-row distribution matches", f"{dist}")


async def y_label(conn) -> None:
    section("[Y] Label — agent_commission_pct display_label (registry and NOTE_FIELDS.md agree)")
    lab = await rls_val(conn, "SELECT display_label FROM portfolio.note_terms_field_registry "
                              "WHERE field_key = 'agent_commission_pct'")
    check(lab == NEW_LABEL, "registry display_label is the new text", f"{lab!r}")
    text = NOTE_FIELDS_DOC.read_text(encoding="utf-8")
    m = re.search(r"^\| ★ `agent_commission_pct` \|.*Display label: \"([^\"]+)\"", text, re.M)
    check(m is not None and m.group(1) == NEW_LABEL, "NOTE_FIELDS.md states the same label",
          m.group(0)[:120] if m else "no match")


async def y_permission(conn) -> None:
    section("[Y] Permission gate on the review API changes — org admin refused, super admin admitted")
    body = {"action": "corrected", "value": "09709TJV4"}
    c1, b1 = await api(U_ORGADMIN, "PUT", gpath("cusip"), body)
    c2, _ = await api(U_SUPER, "PUT", gpath("cusip"), body)
    check(c1 == 403, "an org admin is refused (403)", f"HTTP {c1}: {str(b1)[:100]}")
    check(c2 == 200, "the identical request is admitted for a super admin", f"HTTP {c2}")


def y_status_doc() -> None:
    section("[Y] PROJECT_STATUS records the sprint")
    text = STATUS_DOC.read_text(encoding="utf-8")
    m = re.search(r"^## 0+\. BULKFETCH — .*$", text, re.M)
    check(m is not None, "docs/PROJECT_STATUS.md has the BULKFETCH entry", m.group(0)[:100] if m else "no match")
    for needle in ("defined_outcome_etfs", "--refresh-index", "--peek-test 20", "tmux new -s bulkfetch",
                   "dollar_totals_match"):
        check(needle in text, f"PROJECT_STATUS covers {needle}")


# ═══ Main ══════════════════════════════════════════════════════════════════
async def setup_sources(conn) -> None:
    async with conn.transaction():
        await _super(conn)
        for name, forms in ((SRC_MAIN, ["424B2"]), (SRC_FWP, ["FWP"])):
            await conn.execute(
                """INSERT INTO portfolio.edgar_sources
                       (name, description, form_types, filer_source, filer_include_status, classifier)
                   VALUES ($1, $2, $3, 'structured_note_issuers', ARRAY['yes'], 'structured_note_cover')""",
                name, f"{TAG} fixture source", forms)


async def main_async() -> int:
    global ACTIVE_V
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = url
    install_mocks()
    conn = await asyncpg.connect(url, statement_cache_size=0)
    pool = None
    pre_counts = None
    gold_before = None
    from services import edgar_bulk as eb

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
        used = {"selected", "not_selected", "fetched", "prefilter_skipped", "discovered", "not_pricing_supplement",
                "ready_for_extraction", "fetch_failed"}
        check(bool(st_ok) and used <= st_ok, "every fixture pipeline_status is a live CHECK value", f"{sorted(used - st_ok)}")
        trig = allowed(await check_defs(conn, "portfolio.edgar_pipeline_runs"), "trigger_source")
        check("verify" in trig, "the run rows' trigger_source 'verify' is a live CHECK value", f"{sorted(trig)}")
        pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=12)
        await run("setup sources", setup_sources, conn)

        await run("task 1", y_task1)
        await run("sources", y_sources, conn, eb)
        await run("institutions", y_institutions, conn, eb)
        await run("dates", y_dates, conn, eb)
        await run("scope + order", y_scope_order, conn, eb)
        await run("peek + storage", y_peek, conn, pool, eb)
        await run("kinds + supplements", y_kinds, conn, pool, eb)
        await run("rate limit", y_rate_limit, conn, pool, eb)
        await run("resume", y_resume, conn, pool, eb)
        await run("failures", y_failures, conn, pool, eb)
        await run("selection v2", y_selection_v2, conn, eb)
        await run("reprocess fetched", y_reprocess, conn, eb)
        await run("index refresh", y_index_refresh, conn, eb)
        run_rows = await rls_fetch(conn, "SELECT status FROM portfolio.edgar_pipeline_runs WHERE trigger_source = 'verify' "
                                         "AND details->'bulk'->>'source' = $1", SRC_MAIN)
        check(len(run_rows) > 0 and all(r["status"] in ("succeeded", "failed") for r in run_rows),
              "every bulk run was recorded in edgar_pipeline_runs and closed", f"{len(run_rows)} runs")
        y_no_model()

        await run("seed users", seed_users, conn)
        await run("gold fixture filing", make_gold_filing, conn)
        await run("list editor", y_list_editor, conn)
        await run("identifiers", y_identifiers, conn)
        await run("totals", y_totals, conn)
        await run("normalisation", y_normalisation, conn)
        await run("label", y_label, conn)
        await run("permission gate", y_permission, conn)
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


def y_regression() -> None:
    section("[Y] Regression: verify_edgarpipelinea.py, verify_edgarcohorts.py and verify_notefix2.py, "
            "ONCE each as flat subprocesses, AFTER this script's own teardown")
    for name in ("verify_edgarpipelinea.py", "verify_edgarcohorts.py", "verify_notefix2.py"):
        try:
            proc = subprocess.run([sys.executable, str(HERE.parent / name)], capture_output=True,
                                  text=True, timeout=600, cwd=str(REPO))
            out = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = f"TIMEOUT after 600s: {(exc.stdout or b'')[-300:]!r}"
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        check(m is not None and int(m.group(1)) > 0 and int(m.group(2)) == 0,
              f"{name} still reports 0 FAIL", m.group(0) if m else out[-300:])


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
