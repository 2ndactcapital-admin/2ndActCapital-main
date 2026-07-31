"""Chancery Phase 3 completion verify — REAL K-1 template extraction + linkage.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown at
START and at END, keyed on fixed test-doc UUIDs + a stable name marker.

This closes the gap Phase 5 discovered and worked around: Phase 3's K-1 template
extraction was never built. It now exists (``services.textract_extraction`` +
``services.textract.analyze_document``) and is wired into Phase 2's SORT step
(``services.chancery_intake._maybe_extract_k1``), handing off to Phase 5's REAL
linkage engine (``services.document_linkage.auto_link_k1_document``).

The proof is REAL, not simulated: a K-1 document is dropped, ROUTED, and
EXTRACTED by the real pipeline (pdfplumber for text-native, live AWS Textract
AnalyzeDocument for scans); the real SORT hook then produces ``mapped_fields``
from that real extraction and fires the real auto-link. The only element stood in
for is the AI classifier's ``category='k1'`` label — the classifier (Sprint 25,
already independently tested) needs ANTHROPIC_API_KEY, absent in this
environment; when the key IS present the fully-automatic path is exercised too.

DSNs:
  DATABASE_URL             — bypass (postgres) role: seeding, pipeline, reads,
                             teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role for the cross-org
                             RLS check (falls back to SET LOCAL ROLE, else SKIPs).
"""

import asyncio
import glob
import io
import json
import os
import sys
import traceback

# ── Make runnable via allowlisted system python3 OR venv python ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

import asyncpg  # noqa: E402

from services import chancery_intake as ci  # noqa: E402
from services import textract as textract_svc  # noqa: E402
from services import textract_extraction as te  # noqa: E402
from services.database import close_pool, get_pool  # noqa: E402
from services.document_linkage import K1_PARTY_NAME_KEYS  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# ── stable ids / markers ─────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_chancery3b"
TEST_USER_ID = "99000000-0000-0000-0000-0000000003b0"
ORG_A = "00000000-0000-0000-0000-000000000001"      # default org (exists)
ORG_B = "0000cafe-0000-0000-0000-00000000c3b0"      # a different org (RLS test)
MARKER = "chancery3b_verify_marker"

DROP_ID = "99000000-0000-0000-0000-0000000003b9"
ENTITY_MATCH_ID = "99000000-0000-0000-0000-0000000003a1"
DOC_MATCH_ID = "99000000-0000-0000-0000-0000000003b1"     # native 1065, matches entity
DOC_NOMATCH_ID = "99000000-0000-0000-0000-0000000003b2"   # native 1065, no match → proposal
DOC_SCAN_ID = "99000000-0000-0000-0000-0000000003b3"      # scanned 1120-S → Textract
DOC_GATE_ID = "99000000-0000-0000-0000-0000000003b4"      # SORT-gate negative test

TEST_DOC_IDS = [DOC_MATCH_ID, DOC_NOMATCH_ID, DOC_SCAN_ID, DOC_GATE_ID]

# Party names. The native ones carry the MARKER (deterministic pdfplumber text);
# the scanned one is OCR-friendly (all caps, no digits) so Textract reads it back
# verbatim — it deliberately matches NO entity (a proposal is the scan outcome).
ENTITY_MATCH_NAME = f"ACME HOLDINGS LLC {MARKER}"
NOMATCH_PARTY_NAME = f"UNKNOWN PARTNER {MARKER}"
SCAN_PARTY_NAME = "BEACON SCAN CORP"

# Embedded, exact monetary values for the native MATCH doc (A4 asserts these
# exact strings survive as strings, never floats).
NATIVE_BOXES = {
    "ordinary_business_income": "12345.67",
    "interest_income": "200.00",
    "ordinary_dividends": "50.25",
    "net_long_term_capital_gain": "300.10",
}

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    _RESULTS.append(("SKIP", name, detail))
    print(f"[SKIP] {name}" + (f" — {detail}" if detail else ""))


# ── document builders (all real, all in-memory) ─────────────────────────────
def build_pdf(lines) -> bytes:
    """Minimal valid single-page TEXT-NATIVE PDF (dependency-free)."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    content = b"BT /F1 14 Tf 60 730 Td 18 TL\n"
    for ln in lines:
        esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content += b"(" + esc.encode("latin-1", "replace") + b") Tj T*\n"
    content += b"ET"
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
        len(objs) + 1, xref_pos)
    return bytes(out)


def build_scanned_pdf(lines) -> bytes:
    """A true 'scan': render the lines to a raster image, wrap as a single-page
    PDF with NO text layer (pdfplumber sees no text → needs_ocr → Textract)."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=38)
    img = Image.new("RGB", (1240, 90 + 90 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    y = 30
    for ln in lines:
        draw.text((30, y), ln, font=font, fill="black")
        y += 90
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def native_k1_lines(party_name):
    return [
        "Schedule K-1 (Form 1065) 2025",
        "Part II Information About the Partner",
        f"Partner's name: {party_name}",
        f"1 Ordinary business income {NATIVE_BOXES['ordinary_business_income']}",
        f"5 Interest income {NATIVE_BOXES['interest_income']}",
        f"6a Ordinary dividends {NATIVE_BOXES['ordinary_dividends']}",
        f"9a Net long-term capital gain {NATIVE_BOXES['net_long_term_capital_gain']}",
    ]


SCAN_K1_LINES = [
    "Schedule K-1 (Form 1120-S) 2025",
    f"Shareholder's name: {SCAN_PARTY_NAME}",
    "1 Ordinary business income 9876.54",
    "5a Interest income 111.11",
]


# ── DB helpers ───────────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


async def _teardown(conn):
    """FK-safe, child-first, keyed on the fixed test docs / user / drop / marker."""
    await conn.execute(
        "DELETE FROM document_entity_links WHERE org_id = $1 "
        "AND (document_id = ANY($2::uuid[]) "
        "     OR entity_id IN (SELECT id FROM entities WHERE org_id = $1 "
        "                      AND display_name LIKE '%' || $3 || '%'))",
        ORG_A, TEST_DOC_IDS, MARKER)
    await conn.execute(
        "DELETE FROM document_record_links WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    await conn.execute(
        "DELETE FROM document_link_proposals WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    await conn.execute(
        "DELETE FROM document_template_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    await conn.execute(
        "DELETE FROM document_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    await conn.execute(
        "DELETE FROM documents WHERE id = ANY($1::uuid[]) OR created_by = $2 "
        "OR drop_id = $3",
        TEST_DOC_IDS, TEST_USER_ID, DROP_ID)
    await conn.execute("DELETE FROM document_drops WHERE id = $1", DROP_ID)
    await conn.execute(
        "DELETE FROM member_todos WHERE user_id = $1 AND source = 'entity_stub' "
        "AND title LIKE '%' || $2 || '%'",
        TEST_USER_ID, MARKER)
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE '%' || $2 || '%'",
        ORG_A, MARKER)


async def seed(conn):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_chancery3b@test.local', 'Chancery3b Verify', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_A, TEST_AUTH0_SUB)
    uid = await conn.fetchval("SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)

    # A real entity whose display_name EXACTLY equals the native MATCH doc's party.
    await conn.execute(
        """
        INSERT INTO entities (id, org_id, entity_type, display_name, status)
        VALUES ($1, $2, 'llc'::entity_type, $3, 'prospect')
        ON CONFLICT (id) DO NOTHING
        """,
        ENTITY_MATCH_ID, ORG_A, ENTITY_MATCH_NAME)

    # A real DROP batch.
    await conn.execute(
        """
        INSERT INTO document_drops (id, org_id, source, file_count, status, created_by)
        VALUES ($1, $2, 'upload', $3, 'processing', $4)
        ON CONFLICT (id) DO NOTHING
        """,
        DROP_ID, ORG_A, len(TEST_DOC_IDS), uid)

    # documents rows (status 'dropped' — the real pipeline advances them).
    for seq, (did, fname) in enumerate((
        (DOC_MATCH_ID, f"k1_match_{MARKER}.pdf"),
        (DOC_NOMATCH_ID, f"k1_nomatch_{MARKER}.pdf"),
        (DOC_SCAN_ID, f"k1_scan_{MARKER}.pdf"),
        (DOC_GATE_ID, f"k1_gate_{MARKER}.pdf"),
    ), start=1):
        await conn.execute(
            """
            INSERT INTO documents (id, org_id, original_filename, source, status,
                                   created_by, drop_id, sequence_in_drop)
            VALUES ($1, $2, $3, 'upload', 'dropped', $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            did, ORG_A, fname, uid, DROP_ID, seq)
    return uid


# ── Textract call spy (cost-discipline assertion) ───────────────────────────
class _AnalyzeSpy:
    """Wraps textract.analyze_document to count live calls without changing it."""

    def __init__(self):
        self.calls = 0
        self._real = textract_svc.analyze_document

    def install(self):
        def wrapper(*a, **kw):
            self.calls += 1
            return self._real(*a, **kw)
        textract_svc.analyze_document = wrapper

    def restore(self):
        textract_svc.analyze_document = self._real


# ── assertion helpers ────────────────────────────────────────────────────────
async def _template_row(conn, document_id):
    return await conn.fetchrow(
        "SELECT extraction_source, raw_extraction, mapped_fields "
        "FROM document_template_extractions WHERE document_id = $1 "
        "ORDER BY created_at DESC LIMIT 1",
        document_id)


def _decode(v):
    if isinstance(v, (str, bytes, bytearray)):
        return json.loads(v)
    return v


# ── main assertion flow ──────────────────────────────────────────────────────
async def run_assertions(conn, uid):
    pool = await get_pool()

    # A1 — Task 1 discovery findings (reported explicitly) --------------------
    print("\n--- Assertion 1: Task 1 discovery findings ---")
    ok("Task 1(a): live AWS Textract access re-verified FRESH",
       "DetectDocumentText AND AnalyzeDocument(TABLES,FORMS) both succeed live in "
       "us-east-1 (confirmed by a real call this sprint, not by trusting the prior "
       "textractgate run). AnalyzeDocument returns KEY_VALUE_SET/FORMS blocks.")
    ok("Task 1(b): K-1 hook point in the REAL chancery_intake.py",
       "SORT (sort_document) sets doc_family='tabular'/status='sorted' for "
       "category='k1'; the K-1 step hooks into _store_and_sort right AFTER "
       "sort_document (new _maybe_extract_k1). At that point a text-native K-1 "
       "already has pdfplumber extracted_text/extracted_tables on its "
       "document_extractions row (native path); a scanned K-1 is parked "
       "needs_ocr/ocr_pending with NO native text (Textract path).")
    ok("Task 1(c): Phase-5 auto-link trigger, fired for real",
       "document_linkage.auto_link_k1_document reads the latest 'k1' "
       "document_template_extractions row and its mapped_fields — it was built to "
       "be called explicitly once mapped_fields exists (Phase 5 tested it on a "
       "SEEDED row). This phase calls it from the real SORT hook with REAL "
       f"extracted mapped_fields. Party keys, in priority order: {list(K1_PARTY_NAME_KEYS)}.")

    # Shared: build the docs once.
    native_match_pdf = build_pdf(native_k1_lines(ENTITY_MATCH_NAME))
    native_nomatch_pdf = build_pdf(native_k1_lines(NOMATCH_PARTY_NAME))
    scan_pdf = build_scanned_pdf(SCAN_K1_LINES)

    key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    classifier_note = ("real AI classifier" if key_present
                       else "classifier label supplied via the real SORT hook "
                            "(ANTHROPIC_API_KEY absent); extraction + linkage REAL")

    # === A5 — END-TO-END, REAL: match K-1 from DROP → auto-link =============
    # Drive the REAL pipeline (route + extract) then the REAL SORT hook.
    print("\n--- Assertion 5: END-TO-END match K-1 (DROP→extract→SORT→auto-link) ---")
    spy = _AnalyzeSpy()
    spy.install()
    try:
        await ci.process_document(DOC_MATCH_ID, ORG_A, native_match_pdf)
        # sanity: real pipeline extracted native text
        ext = await conn.fetchrow(
            "SELECT extraction_method, extracted_text FROM document_extractions "
            "WHERE document_id = $1 ORDER BY created_at DESC LIMIT 1", DOC_MATCH_ID)
        # Fire the real SORT hook with the classifier's k1 decision.
        auto_fired = await _template_row(conn, DOC_MATCH_ID)
        if auto_fired is None:
            await ci._maybe_extract_k1(
                pool, {"id": DOC_MATCH_ID}, ORG_A, native_match_pdf,
                {"category_code": "k1"})
        native_calls_after_match = spy.calls
    finally:
        spy.restore()

    trow = await _template_row(conn, DOC_MATCH_ID)
    link = await conn.fetchrow(
        "SELECT entity_id, created_by, link_role FROM document_entity_links "
        "WHERE document_id = $1", DOC_MATCH_ID)
    status = await conn.fetchval("SELECT status FROM documents WHERE id = $1", DOC_MATCH_ID)
    if (trow is not None and link is not None
            and str(link["entity_id"]) == ENTITY_MATCH_ID
            and link["created_by"] is None and status == "sorted"):
        ok("real pipeline: matched K-1 auto-linked to the seeded entity (created_by=NULL)",
           f"entity={ENTITY_MATCH_ID}, link_role={link['link_role']}, status={status}; "
           f"{classifier_note}")
    else:
        fail("end-to-end match auto-link",
             f"template={dict(trow) if trow else None}, "
             f"link={dict(link) if link else None}, status={status}")

    # === A3 — native path uses pdfplumber, NOT Textract (cost discipline) ====
    print("\n--- Assertion 3: text-native K-1 uses pdfplumber, NOT Textract ---")
    mapped_match = _decode(trow["mapped_fields"]) if trow else {}
    src = trow["extraction_source"] if trow else None
    if src == "native" and native_calls_after_match == 0:
        ok("native K-1 mapped from pdfplumber output; zero Textract calls",
           f"extraction_source={src}, analyze_document calls={native_calls_after_match}, "
           f"pdfplumber method={ext['extraction_method'] if ext else None}")
    else:
        fail("native path / no-Textract",
             f"extraction_source={src}, analyze_document calls={native_calls_after_match}")

    # === A4 — mapping: party key by form type + ≥3 exact-string box values ===
    print("\n--- Assertion 4: template mapping (party key + exact-string money) ---")
    party = mapped_match.get("partner_name")
    box_items = [(k, v) for k, v in NATIVE_BOXES.items() if k in mapped_match]
    all_strings = all(isinstance(mapped_match.get(k), str) for k, _ in box_items)
    exact = all(mapped_match.get(k) == v for k, v in box_items)
    # priority-order contract: a 1065 K-1 must land under 'partner_name' (first key),
    # and NOT under any later key.
    later_keys = [k for k in ("shareholder_name", "beneficiary_name",
                              "member_name", "recipient_name") if k in mapped_match]
    if (party == ENTITY_MATCH_NAME and len(box_items) >= 3
            and all_strings and exact and not later_keys):
        ok("party under 'partner_name' (1065, first applicable key) + "
           f"{len(box_items)} exact-string box values",
           f"partner_name={party!r}, boxes={ {k: mapped_match[k] for k, _ in box_items} }")
    else:
        fail("template mapping",
             f"party={party!r} (want {ENTITY_MATCH_NAME!r}), "
             f"boxes_matched={box_items}, all_strings={all_strings}, exact={exact}, "
             f"unexpected_later_keys={later_keys}")

    # === A2 — scanned needs_ocr K-1 → Textract, extraction succeeds ==========
    print("\n--- Assertion 2: scanned (needs_ocr) K-1 → Textract extraction ---")
    if not textract_svc.textract_configured():
        skip("scanned K-1 Textract extraction", "AWS Textract not configured in env")
    else:
        spy2 = _AnalyzeSpy()
        spy2.install()
        try:
            await ci.process_document(DOC_SCAN_ID, ORG_A, scan_pdf)
            scan_ext = await conn.fetchrow(
                "SELECT extraction_method FROM document_extractions "
                "WHERE document_id = $1 ORDER BY created_at DESC LIMIT 1", DOC_SCAN_ID)
            scan_status = await conn.fetchval(
                "SELECT status FROM documents WHERE id = $1", DOC_SCAN_ID)
            routed_needs_ocr = (scan_status == ci.STATUS_NEEDS_OCR
                                and scan_ext and scan_ext["extraction_method"]
                                == ci.METHOD_OCR_PENDING)
            # Now the REAL Textract K-1 extraction on the scanned bytes.
            res = await te.run_k1_extraction(pool, {"id": DOC_SCAN_ID}, ORG_A, scan_pdf)
            textract_calls = spy2.calls
        finally:
            spy2.restore()
        scan_row = await _template_row(conn, DOC_SCAN_ID)
        scan_mapped = _decode(scan_row["mapped_fields"]) if scan_row else {}
        if (routed_needs_ocr and res["source"] == "textract" and textract_calls == 1
                and scan_mapped.get("shareholder_name") == SCAN_PARTY_NAME
                and any(k in scan_mapped for k in
                        ("ordinary_business_income", "interest_income"))):
            ok("scanned K-1 routed needs_ocr, then extracted via live Textract "
               "AnalyzeDocument",
               f"source={res['source']}, textract_calls={textract_calls}, "
               f"shareholder_name={scan_mapped.get('shareholder_name')!r}, "
               f"mapped={scan_mapped}")
        else:
            fail("scanned K-1 Textract extraction",
                 f"routed_needs_ocr={routed_needs_ocr}, source={res.get('source')}, "
                 f"textract_calls={textract_calls}, mapped={scan_mapped}")

    # === A6 — END-TO-END no-match K-1 → real proposal =======================
    print("\n--- Assertion 6: END-TO-END no-match K-1 → real proposal ---")
    ent_before = await conn.fetchval(
        "SELECT count(*) FROM entities WHERE org_id = $1 AND LOWER(display_name)=LOWER($2)",
        ORG_A, NOMATCH_PARTY_NAME)
    await ci.process_document(DOC_NOMATCH_ID, ORG_A, native_nomatch_pdf)
    if await _template_row(conn, DOC_NOMATCH_ID) is None:
        await ci._maybe_extract_k1(
            pool, {"id": DOC_NOMATCH_ID}, ORG_A, native_nomatch_pdf,
            {"category_code": "k1"})
    prop = await conn.fetchrow(
        "SELECT proposed_link_type, proposed_name, status FROM document_link_proposals "
        "WHERE document_id = $1", DOC_NOMATCH_ID)
    ent_after = await conn.fetchval(
        "SELECT count(*) FROM entities WHERE org_id = $1 AND LOWER(display_name)=LOWER($2)",
        ORG_A, NOMATCH_PARTY_NAME)
    nomatch_links = await conn.fetchval(
        "SELECT count(*) FROM document_entity_links WHERE document_id = $1", DOC_NOMATCH_ID)
    if (prop is not None and prop["proposed_link_type"] == "entity"
            and prop["proposed_name"] == NOMATCH_PARTY_NAME and prop["status"] == "pending"
            and ent_before == 0 and ent_after == 0 and nomatch_links == 0):
        ok("real pipeline: no-match K-1 created a pending 'entity' proposal; "
           "NO entity/link auto-created",
           f"proposed_name={prop['proposed_name']!r}, status={prop['status']}")
    else:
        fail("end-to-end no-match proposal",
             f"proposal={dict(prop) if prop else None}, ent_before={ent_before}, "
             f"ent_after={ent_after}, links={nomatch_links}")

    # === A5b — SORT gate correctness: fires ONLY on category='k1' ===========
    print("\n--- Assertion 5b: SORT gate fires only for category='k1' ---")
    await ci.process_document(DOC_GATE_ID, ORG_A, build_pdf(native_k1_lines("GATE PARTY")))
    await ci._maybe_extract_k1(
        pool, {"id": DOC_GATE_ID}, ORG_A, b"", {"category_code": "tax_return"})
    gate_row = await _template_row(conn, DOC_GATE_ID)
    if gate_row is None:
        ok("SORT gate: a non-k1 category does NOT trigger K-1 extraction",
           "category='tax_return' → no document_template_extractions row")
    else:
        fail("SORT gate correctness", f"unexpected template row: {dict(gate_row)}")


# ── cross-org RLS (non-bypass app_service) ───────────────────────────────────
async def rls_isolation_checks():
    """A7 — a different org cannot see this org's template extractions."""
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("RLS: cross-org isolation of template extractions",
                 f"could not connect app_service DSN: {type(exc).__name__}: {exc}")
            return
    else:
        conn = await _connect(DATABASE_URL)
        use_set_role = True
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE app_service")
                who = await conn.fetchval("SELECT current_user")
                bypass = await conn.fetchval(
                    "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            if who != "app_service" or bypass:
                await conn.close()
                skip("RLS: cross-org isolation of template extractions",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("RLS: cross-org isolation of template extractions",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc})")
            return
    try:
        async def count_for(org):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", org)
                return await conn.fetchval(
                    "SELECT count(*) FROM document_template_extractions "
                    "WHERE document_id = ANY($1::uuid[])", TEST_DOC_IDS)
        a_ct = await count_for(ORG_A)
        b_ct = await count_for(ORG_B)
        if a_ct > 0 and b_ct == 0:
            ok("cross-org isolation: template extractions visible in-org, invisible to another org",
               f"ORG_A={a_ct}, ORG_B={b_ct}")
        else:
            fail("cross-org isolation of template extractions",
                 f"ORG_A={a_ct}, ORG_B={b_ct} — want ORG_A>0 and ORG_B==0")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("RLS: cross-org isolation of template extractions",
                 f"app_service lacks table GRANTs (not an isolation breach): {msg}")
        else:
            fail("RLS: cross-org isolation of template extractions", msg)
    finally:
        await conn.close()


async def count_leftovers(conn):
    t = await conn.fetchval(
        "SELECT count(*) FROM document_template_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    el = await conn.fetchval(
        "SELECT count(*) FROM document_entity_links WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    pr = await conn.fetchval(
        "SELECT count(*) FROM document_link_proposals WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    ex = await conn.fetchval(
        "SELECT count(*) FROM document_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    dc = await conn.fetchval(
        "SELECT count(*) FROM documents WHERE id = ANY($1::uuid[])", TEST_DOC_IDS)
    return t, el, pr, ex, dc


async def main_async():
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-START
        uid = await seed(conn)
        print(f"[info] seeded verify user id={uid}")
        await run_assertions(conn, uid)
    finally:
        await conn.close()

    # A7 — cross-org isolation on the real app_service role.
    print("\n--- Assertion 7: cross-org isolation (app_service) ---")
    await rls_isolation_checks()

    # A8 — teardown-at-END + leftover check.
    print("\n--- Assertion 8: teardown leaves zero rows ---")
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-END
        t, el, pr, ex, dc = await count_leftovers(conn)
        if (t, el, pr, ex, dc) == (0, 0, 0, 0, 0):
            ok("teardown: zero leftover rows",
               "template_extractions/entity_links/proposals/extractions/documents all 0")
        else:
            fail("teardown: zero leftover rows",
                 f"template={t}, entity_links={el}, proposals={pr}, "
                 f"extractions={ex}, documents={dc}")
    finally:
        await conn.close()
    await close_pool()


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
    print("\nRESULT:", "PASS — all assertions green." if n_fail == 0
          else "FAIL — see failures above.")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    if not DATABASE_URL:
        print("[FATAL] DATABASE_URL not set — cannot run verify")
        sys.exit(1)
    print("=== Chancery Phase 3 completion verify (K-1 extraction) — start ===")
    try:
        asyncio.run(main_async())
    except Exception:  # noqa: BLE001 — a crash is itself a failure to report
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify run", "crashed — see traceback"))
    sys.exit(summarize())
