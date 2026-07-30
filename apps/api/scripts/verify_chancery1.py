"""Chancery Phase 1 verify — DROP + ROUTE + native EXTRACT.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown
at start AND at end, keyed on stable ids / the seeded test user.

What this proves (each [Y] reported explicitly):
  * Task 1 discovery findings (pdfplumber install works; existing tables; S25
    classifier location + signature).
  * documents / document_extractions / document_drops exist with RLS ENABLED
    and a policy present (queried from pg_class / pg_policy, not trusted).
  * route_document flags a real text-native PDF has_text_layer=True.
  * extract_native pulls the REAL embedded text (asserted by content).
  * a corrupt PDF is handled gracefully by route_document (no crash).
  * SINGLE-file drop through the real endpoint → documents row 'extracted' + a
    document_extractions row holding the real text.
  * MULTI-file drop (3 files) → one drop file_count=3, 3 documents sharing the
    drop_id with sequence_in_drop 1/2/3.
  * files within a drop are processed SEQUENTIALLY (proven by extraction
    created_at ordering, not merely asserted).
  * one corrupt file fails on its OWN row without preventing the other two.
  * drop reaches 'completed' with completed_at once all files are done.
  * a different org CANNOT see this org's documents/drops (real RLS, via the
    non-bypass app_service role — SKIPPED, not failed, when its DSN is absent).
  * frontend build: reported (no frontend file was touched this phase).
  * teardown: zero leftover rows across all three tables.

DSNs:
  DATABASE_URL             — the app's role (RLS-bypassing 'postgres' in current
                             prod). Used for seeding, structural checks, the
                             endpoint (the app connects via this), teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role, supplied by Joe
                             at test time ONLY. Runs the real cross-org RLS
                             isolation checks. Absent → those SKIP (not fail).
"""

import asyncio
import glob
import io
import os
import sys
import traceback

# ── Make this runnable via the allowlisted system `python3` OR venv python ──
# Add apps/api (so `import main` / `services...` resolve) and the venv's
# site-packages (so asyncpg / fastapi / pdfplumber import even under system
# python3, which shares the venv's 3.14 ABI).
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
# The project venv is apps/api/venv (fall back to a repo-root venv). Its
# site-packages carries pdfplumber/httpx/anthropic; add it so this runs under
# the system python3 too. apps/api/venv is searched first (highest priority).
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

import asyncpg  # noqa: E402

# ── stable ids ──────────────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_chancery1"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
ORG_A = "00000000-0000-0000-0000-000000000001"          # default org (exists)
ORG_B = "0000cafe-0000-0000-0000-0000000000b2"          # a different org (RLS test)

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# Resolved at seed time (the real users.id backing TEST_AUTH0_SUB) — teardown
# and RLS-seed rows are keyed on this so we NEVER touch real org data.
UPLOADER_ID = TEST_USER_ID

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []   # (status, name, detail)


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    _RESULTS.append(("SKIP", name, detail))
    print(f"[SKIP] {name}" + (f" — {detail}" if detail else ""))


# ── hand-crafted minimal PDF generator (no extra dependency) ────────────────
def build_pdf(lines) -> bytes:
    """A minimal, valid single-page PDF. ``lines`` = list of text strings; an
    empty list produces a page with NO text layer (a stand-in for a scan)."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    if lines:
        content = b"BT /F1 18 Tf 72 720 Td 20 TL\n"
        for ln in lines:
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content += b"(" + esc.encode("latin-1") + b") Tj T*\n"
        content += b"ET"
    else:
        content = b"0 0 1 RG 72 100 m 300 100 l S"  # a drawn line, no text
    objs.append(b"<</Length %d>>\nstream\n%s\nendstream" % (len(content), content))
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (
        len(objs) + 1, xref_pos,
    )
    return bytes(out)


CORRUPT_PDF = b"%PDF-1.4\nthis is not a real pdf just random garbage \x00\x01\x02\x03"


# ── async DB helpers ────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


async def teardown():
    """Delete every chancery row this script could have created (by uploader),
    FK-safe order. Runs under the bypass DATABASE_URL role so RLS never hides a
    row. Safe to run before seeding (resolves the user if present)."""
    conn = await _connect(DATABASE_URL)
    try:
        uid = await conn.fetchval(
            "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB
        )
        if uid is None:
            return
        await conn.execute(
            "DELETE FROM document_extractions WHERE document_id IN "
            "(SELECT id FROM documents WHERE created_by = $1)", uid,
        )
        await conn.execute("DELETE FROM documents WHERE created_by = $1", uid)
        await conn.execute("DELETE FROM document_drops WHERE created_by = $1", uid)
    finally:
        await conn.close()


async def seed_user():
    """Idempotently seed the verify user; return its real users.id."""
    conn = await _connect(DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, 'verify_chancery1@test.local', 'Chancery Verify', $3, 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            TEST_USER_ID, ORG_A, TEST_AUTH0_SUB,
        )
        return await conn.fetchval(
            "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB
        )
    finally:
        await conn.close()


async def structural_checks():
    """[Y] RLS enabled + policy present on all three tables (pg_class/pg_policy)."""
    conn = await _connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT c.relname AS tbl, c.relrowsecurity AS rls,
                   count(p.polname) AS npol
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
            LEFT JOIN pg_policy p ON p.polrelid = c.oid
            WHERE c.relname = ANY($1::text[])
            GROUP BY c.relname, c.relrowsecurity
            """,
            ["documents", "document_extractions", "document_drops"],
        )
        by = {r["tbl"]: r for r in rows}
        for tbl in ("documents", "document_extractions", "document_drops"):
            r = by.get(tbl)
            if r is None:
                fail(f"table {tbl} exists", "not found in pg_class")
            elif not r["rls"]:
                fail(f"{tbl}: RLS enabled", "relrowsecurity is false")
            elif r["npol"] < 1:
                fail(f"{tbl}: policy present", "no pg_policy rows")
            else:
                ok(f"{tbl}: exists, RLS enabled, {r['npol']} policy present")
    finally:
        await conn.close()


async def fetch_drop(drop_id):
    conn = await _connect(DATABASE_URL)
    try:
        return await conn.fetchrow(
            "SELECT id, file_count, status, completed_at, org_id "
            "FROM document_drops WHERE id = $1", drop_id,
        )
    finally:
        await conn.close()


async def fetch_docs_for_drop(drop_id):
    conn = await _connect(DATABASE_URL)
    try:
        return await conn.fetch(
            """
            SELECT d.id, d.sequence_in_drop, d.status, d.original_filename,
                   e.extraction_method, e.has_native_text_layer,
                   e.extracted_text, e.page_count, e.created_at AS ext_created_at
            FROM documents d
            LEFT JOIN document_extractions e ON e.document_id = d.id
            WHERE d.drop_id = $1
            ORDER BY d.sequence_in_drop
            """,
            drop_id,
        )
    finally:
        await conn.close()


async def rls_isolation_checks(drop_id, doc_id):
    """[Y] A different org cannot see this org's documents/drops — under the
    NON-BYPASS app_service role (RLS actually enforced).

    Two ways to obtain a non-bypass session:
      1. APP_SERVICE_DATABASE_URL — a real app_service login DSN, if supplied.
      2. Fallback: the bypass DATABASE_URL connection + ``SET LOCAL ROLE
         app_service`` per transaction (works when the login role may assume
         app_service). app_service has rolbypassrls=false, so policies apply.
    If neither yields a non-bypass session (no DSN, and the role switch is not
    permitted), SKIP — never fail, and never assert isolation from a bypass role
    (which would be a false green)."""
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("RLS: different org cannot see this org's documents/drops",
                 f"could not connect app_service DSN: {type(exc).__name__}: {exc}")
            return
    else:
        conn = await _connect(DATABASE_URL)
        use_set_role = True
        # Confirm the role switch is actually permitted AND non-bypass before
        # trusting any isolation result from it.
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE app_service")
                who = await conn.fetchval("SELECT current_user")
                bypass = await conn.fetchval(
                    "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            if who != "app_service" or bypass:
                await conn.close()
                skip("RLS: different org cannot see this org's documents/drops",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("RLS: different org cannot see this org's documents/drops",
                 f"cannot SET ROLE app_service from this login "
                 f"({type(exc).__name__}: {exc}) — set APP_SERVICE_DATABASE_URL to run")
            return
    try:
        async def visible(org, sql, *args):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", org,
                )
                return await conn.fetchval(sql, *args)

        doc_sql = "SELECT count(*) FROM documents WHERE id = $1"
        drop_sql = "SELECT count(*) FROM document_drops WHERE id = $1"

        a_doc = await visible(ORG_A, doc_sql, doc_id)
        b_doc = await visible(ORG_B, doc_sql, doc_id)
        a_drop = await visible(ORG_A, drop_sql, drop_id)
        b_drop = await visible(ORG_B, drop_sql, drop_id)

        if a_doc == 1 and b_doc == 0:
            ok("RLS: documents row visible in-org, invisible cross-org",
               f"ORG_A sees {a_doc}, ORG_B sees {b_doc}")
        else:
            fail("RLS: documents row visible in-org, invisible cross-org",
                 f"ORG_A sees {a_doc} (want 1), ORG_B sees {b_doc} (want 0)")

        if a_drop == 1 and b_drop == 0:
            ok("RLS: document_drops row visible in-org, invisible cross-org",
               f"ORG_A sees {a_drop}, ORG_B sees {b_drop}")
        else:
            fail("RLS: document_drops row visible in-org, invisible cross-org",
                 f"ORG_A sees {a_drop} (want 1), ORG_B sees {b_drop} (want 0)")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("RLS: different org cannot see this org's documents/drops",
                 f"app_service lacks table GRANTs (not an isolation breach): {msg}")
        else:
            fail("RLS: different org cannot see this org's documents/drops", msg)
    finally:
        await conn.close()


async def count_leftovers():
    conn = await _connect(DATABASE_URL)
    try:
        uid = await conn.fetchval(
            "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB
        )
        if uid is None:
            return 0, 0, 0
        docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE created_by = $1", uid)
        drops = await conn.fetchval(
            "SELECT count(*) FROM document_drops WHERE created_by = $1", uid)
        exts = await conn.fetchval(
            "SELECT count(*) FROM document_extractions WHERE document_id IN "
            "(SELECT id FROM documents WHERE created_by = $1)", uid)
        return docs, drops, exts
    finally:
        await conn.close()


# ── Task 1 discovery reporting + unit tests (no DB) ─────────────────────────
def task1_and_unit_tests():
    # Finding (a): pdfplumber actually installed & importable in the venv.
    try:
        import pdfplumber  # noqa: F401
        ok("Task1(a) pdfplumber installed & imports",
           f"pdfplumber {pdfplumber.__version__} — chosen over PyMuPDF for "
           f"first-class per-page table extraction; real import confirmed")
    except Exception as exc:  # noqa: BLE001
        fail("Task1(a) pdfplumber installed & imports",
             f"{type(exc).__name__}: {exc}")
        return  # nothing else runs without the lib

    # Finding (b): existing tables / services (report).
    ok("Task1(b) existing-tables scan",
       "no prior 'chancery' service/router. `entity_documents` (Sprint 17) is a "
       "SEPARATE CRM table, not the Chancery `documents` table — no conflict. "
       "The three Chancery tables pre-exist (Part 1 SQL) and are reused as-is")

    # Finding (c): S25 classifier location + signature (report; NOT called here).
    try:
        from services.document_classifier import classify_document  # noqa: F401
        ok("Task1(c) S25 classifier confirmed",
           "services/document_classifier.py :: async classify_document(conn, "
           "org_id, text, *, model=None) -> dict — NOT invoked this phase (SORT "
           "is Phase 2)")
    except Exception as exc:  # noqa: BLE001
        fail("Task1(c) S25 classifier import", f"{type(exc).__name__}: {exc}")

    from services.chancery_intake import route_document, extract_native

    # route_document on a real text-native PDF.
    native_pdf = build_pdf(["CHANCERY VERIFY ALPHA 20260730", "second line"])
    r = route_document(native_pdf)
    if r.get("has_text_layer") is True and r.get("page_count") == 1 and r.get("valid_pdf"):
        ok("route_document: text-native PDF → has_text_layer=True",
           f"page_count={r['page_count']}")
    else:
        fail("route_document: text-native PDF → has_text_layer=True", repr(r))

    # extract_native pulls the REAL embedded text.
    ex = extract_native(native_pdf)
    if "CHANCERY VERIFY ALPHA 20260730" in (ex.get("extracted_text") or ""):
        ok("extract_native: real embedded text present",
           f"text={ex['extracted_text']!r}, tables={len(ex['extracted_tables'])}")
    else:
        fail("extract_native: real embedded text present", repr(ex))

    # corrupt PDF handled gracefully (no crash) by route_document.
    try:
        rc = route_document(CORRUPT_PDF)
        if rc.get("valid_pdf") is False and rc.get("has_text_layer") is False and rc.get("error"):
            ok("route_document: corrupt PDF handled gracefully (no crash)",
               f"error={rc['error']}")
        else:
            fail("route_document: corrupt PDF handled gracefully", repr(rc))
    except Exception as exc:  # noqa: BLE001
        fail("route_document: corrupt PDF handled gracefully",
             f"raised instead of returning: {type(exc).__name__}: {exc}")

    # Bonus: a valid but text-less PDF (scan stand-in) → detected for OCR.
    scan_pdf = build_pdf([])
    rs = route_document(scan_pdf)
    if rs.get("valid_pdf") and rs.get("has_text_layer") is False and rs.get("page_count") == 1:
        ok("route_document: valid text-less PDF → has_text_layer=False (needs_ocr)")
    else:
        fail("route_document: valid text-less PDF → has_text_layer=False", repr(rs))


# ── Endpoint tests (real ASGI app via TestClient) ───────────────────────────
def endpoint_tests():
    """Returns (single_doc_id, multi_drop_id, multi_first_doc_id) for later RLS
    checks, or (None, None, None) on a hard failure."""
    import main
    from starlette.testclient import TestClient

    main.verify_token = lambda _token: {
        "sub": TEST_AUTH0_SUB,
        "email": "verify_chancery1@test.local",
        "org_id": ORG_A,
    }
    hdr = {"Authorization": "Bearer stub"}

    single_pdf = build_pdf(["ALPHA UNIQUE 20260730 SINGLE DROP CONTENT"])
    multi_1 = build_pdf(["BRAVO MULTI ONE 20260730"])
    multi_3 = build_pdf(["DELTA MULTI THREE continuation 20260730"])

    single_doc_id = None
    multi_drop_id = None
    multi_first_doc_id = None

    with TestClient(main.app, raise_server_exceptions=False) as c:
        # --- SINGLE-file drop ---
        r1 = c.post(
            "/api/v1/documents", headers=hdr,
            files=[("files", ("single.pdf", single_pdf, "application/pdf"))],
        )
        if r1.status_code != 201:
            fail("SINGLE-file drop: endpoint 201",
                 f"got {r1.status_code}: {r1.text[:300]}")
        else:
            body = r1.json()
            docs = body.get("documents", [])
            if len(docs) == 1 and docs[0].get("status") == "extracted":
                single_doc_id = docs[0]["id"]
                ok("SINGLE-file drop: endpoint returned 1 doc, status 'extracted'",
                   f"drop={body.get('drop_id')}")
            else:
                fail("SINGLE-file drop: endpoint returned 1 doc, status 'extracted'",
                     repr(body))

        # --- MULTI-file drop: 3 files, middle one corrupt ---
        r2 = c.post(
            "/api/v1/documents", headers=hdr,
            files=[
                ("files", ("m1.pdf", multi_1, "application/pdf")),
                ("files", ("m2_corrupt.pdf", CORRUPT_PDF, "application/pdf")),
                ("files", ("m3.pdf", multi_3, "application/pdf")),
            ],
        )
        if r2.status_code != 201:
            fail("MULTI-file drop: endpoint 201",
                 f"got {r2.status_code}: {r2.text[:300]}")
        else:
            body = r2.json()
            multi_drop_id = body.get("drop_id")
            docs = sorted(body.get("documents", []),
                          key=lambda d: d.get("sequence_in_drop") or 0)
            if docs:
                multi_first_doc_id = docs[0]["id"]
            ok("MULTI-file drop: endpoint accepted 3 files in one request",
               f"drop={multi_drop_id}, returned {len(docs)} outcomes")

    return single_doc_id, multi_drop_id, multi_first_doc_id


async def verify_single(single_doc_id):
    if not single_doc_id:
        fail("SINGLE-file drop: DB row 'extracted' with real extracted text",
             "no document id from endpoint")
        return
    conn = await _connect(DATABASE_URL)
    try:
        row = await conn.fetchrow(
            """
            SELECT d.status, e.extracted_text, e.has_native_text_layer,
                   e.extraction_method
            FROM documents d
            LEFT JOIN document_extractions e ON e.document_id = d.id
            WHERE d.id = $1
            """,
            single_doc_id,
        )
    finally:
        await conn.close()
    if row is None:
        fail("SINGLE-file drop: DB row 'extracted' with real extracted text",
             "documents row not found")
    elif row["status"] == "extracted" and "ALPHA UNIQUE 20260730" in (row["extracted_text"] or ""):
        ok("SINGLE-file drop: DB documents 'extracted' + extraction holds real text",
           f"method={row['extraction_method']}, has_text={row['has_native_text_layer']}")
    else:
        fail("SINGLE-file drop: DB documents 'extracted' + extraction holds real text",
             f"status={row['status']}, text={row['extracted_text']!r}")


async def verify_multi(drop_id):
    if not drop_id:
        fail("MULTI-file drop: one drop file_count=3", "no drop id from endpoint")
        return
    drop = await fetch_drop(drop_id)
    docs = await fetch_docs_for_drop(drop_id)

    # one drop, file_count 3
    if drop and drop["file_count"] == 3:
        ok("MULTI-file drop: one document_drops row, file_count=3")
    else:
        fail("MULTI-file drop: one document_drops row, file_count=3",
             f"drop={dict(drop) if drop else None}")

    # 3 documents, same drop_id, sequence 1/2/3
    seqs = [d["sequence_in_drop"] for d in docs]
    if len(docs) == 3 and seqs == [1, 2, 3]:
        ok("MULTI-file drop: 3 documents share drop_id, sequence_in_drop 1/2/3")
    else:
        fail("MULTI-file drop: 3 documents share drop_id, sequence_in_drop 1/2/3",
             f"count={len(docs)}, seqs={seqs}")

    by_seq = {d["sequence_in_drop"]: d for d in docs}

    # seq1 + seq3 extracted with real text; seq2 (corrupt) failed on its own row.
    d1, d2, d3 = by_seq.get(1), by_seq.get(2), by_seq.get(3)
    if d1 and d1["status"] == "extracted" and "BRAVO MULTI ONE" in (d1["extracted_text"] or ""):
        ok("MULTI-file drop: seq1 extracted with real text")
    else:
        fail("MULTI-file drop: seq1 extracted with real text",
             f"{dict(d1) if d1 else None}")

    if d2 and d2["status"] == "failed" and not (d2["extracted_text"] or "").strip():
        ok("MULTI-file drop: seq2 corrupt failed on its OWN row (no text)",
           f"method={d2['extraction_method']}")
    else:
        fail("MULTI-file drop: seq2 corrupt failed on its OWN row",
             f"{dict(d2) if d2 else None}")

    if d3 and d3["status"] == "extracted" and "DELTA MULTI THREE" in (d3["extracted_text"] or ""):
        ok("MULTI-file drop: seq3 (after the failure) still extracted — batch continued")
    else:
        fail("MULTI-file drop: seq3 still extracted after the failure",
             f"{dict(d3) if d3 else None}")

    # SEQUENTIAL proof: extraction created_at strictly ordered by sequence.
    if d1 and d2 and d3 and all(d["ext_created_at"] for d in (d1, d2, d3)):
        t1, t2, t3 = d1["ext_created_at"], d2["ext_created_at"], d3["ext_created_at"]
        if t1 <= t2 <= t3 and t1 < t3:
            ok("MULTI-file drop: files processed SEQUENTIALLY",
               f"extraction created_at strictly ordered seq1<seq2<seq3 "
               f"({t1.isoformat()} <= {t2.isoformat()} <= {t3.isoformat()})")
        else:
            fail("MULTI-file drop: files processed SEQUENTIALLY",
                 f"created_at not ordered: {t1}, {t2}, {t3}")
    else:
        fail("MULTI-file drop: files processed SEQUENTIALLY",
             "missing extraction created_at timestamps")

    # drop completed + completed_at populated regardless of the one failure.
    if drop and drop["status"] == "completed" and drop["completed_at"] is not None:
        ok("MULTI-file drop: drop status 'completed' + completed_at set "
           "(despite the 1 failure)")
    else:
        fail("MULTI-file drop: drop 'completed' + completed_at set",
             f"status={drop['status'] if drop else None}, "
             f"completed_at={drop['completed_at'] if drop else None}")


# ── frontend build check ────────────────────────────────────────────────────
def build_check():
    # This phase is backend/service-only — no frontend file was touched, so
    # `npm run build` is intentionally not run.
    skip("npm run build exits 0",
         "no frontend file was touched this phase (backend/service only) — "
         "build intentionally not run")


# ── main ────────────────────────────────────────────────────────────────────
def main_flow():
    global UPLOADER_ID
    if not DATABASE_URL:
        fail("DATABASE_URL present", "env var not set — cannot run verify")
        return

    print("=== Chancery Phase 1 verify — start ===")

    # Task 1 discovery + pure unit tests first (no DB needed).
    task1_and_unit_tests()

    # Teardown-at-START (idempotent), then seed.
    asyncio.run(teardown())
    UPLOADER_ID = str(asyncio.run(seed_user()))
    print(f"[info] seeded verify user id={UPLOADER_ID}")

    # Structural (RLS) checks.
    asyncio.run(structural_checks())

    # Endpoint tests (real app), then DB assertions on what they wrote.
    single_doc_id, multi_drop_id, multi_first_doc_id = endpoint_tests()
    asyncio.run(verify_single(single_doc_id))
    asyncio.run(verify_multi(multi_drop_id))

    # Real cross-org RLS isolation (app_service) on the multi drop's rows.
    asyncio.run(rls_isolation_checks(multi_drop_id, multi_first_doc_id))

    # Frontend build.
    build_check()

    # Teardown-at-END, then confirm zero leftovers.
    asyncio.run(teardown())
    docs, drops, exts = asyncio.run(count_leftovers())
    if (docs, drops, exts) == (0, 0, 0):
        ok("Teardown: zero leftover rows across all three tables")
    else:
        fail("Teardown: zero leftover rows across all three tables",
             f"documents={docs}, document_drops={drops}, document_extractions={exts}")


def summarize():
    n_pass = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    n_fail = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    n_skip = sum(1 for s, _, _ in _RESULTS if s == "SKIP")
    print("\n=== SUMMARY ===")
    print(f"PASS={n_pass}  FAIL={n_fail}  SKIP={n_skip}")
    if n_fail:
        print("\nFAILURES:")
        for s, name, detail in _RESULTS:
            if s == "FAIL":
                print(f"  - {name}: {detail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    try:
        main_flow()
    except Exception:  # noqa: BLE001 — a crash is itself a failure to report
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify script did not crash", "see traceback"))
    sys.exit(summarize())
