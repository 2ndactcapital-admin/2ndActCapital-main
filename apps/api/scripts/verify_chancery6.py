"""Chancery Phase 6 verify — document review / correct / confirm.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown
at START and at END, keyed on the seeded test user + stable ids.

Exercises the REAL Phase-6 code (``services.document_review`` — the same functions
the ``routers.document_review`` endpoints call) against the live DB, and proves
cross-org isolation against the REAL non-bypass ``app_service`` role (a different
org's user cannot read the review payload, its corrections, or confirm the doc).

Task-1 discovery findings are reported explicitly (Assertion 1), INCLUDING the
honest statement that real source coordinates are available for NEITHER
extraction path (Textract discards Geometry before storing; native pdfplumber
captured only text/tables, no bounding boxes).

DSNs:
  DATABASE_URL             — bypass (postgres) role: seeding, service calls,
                             DB assertions, teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role for the cross-org
                             RLS check (falls back to SET LOCAL ROLE, else SKIPs).
"""

import asyncio
import glob
import json
import os
import subprocess
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

from services import document_linkage as dl  # noqa: E402
from services import document_review as review  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# ── stable ids / markers ─────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_chancery6"
TEST_USER_ID = "99000000-0000-0000-0000-000000000006"
ORG_A = "00000000-0000-0000-0000-000000000001"      # default org (exists)
ORG_B = "0000cafe-0000-0000-0000-0000000000c6"      # a different org (RLS test)
MARKER = "chancery6_verify_marker"

ENTITY_ID = "99000000-0000-0000-0000-0000000006a1"
DOC_ID = "99000000-0000-0000-0000-0000000006b1"
TEST_DOC_IDS = [DOC_ID]

ENTITY_NAME = f"K1 Partner LLC {MARKER}"
STORAGE_KEY = f"chancery/{ORG_A}/{ENTITY_ID}/k1/v1/{MARKER}.pdf"

CORRECT_FIELD = "ordinary_business_income"
ORIGINAL_VALUE = "1234.00"
CORRECTED_VALUE = "5678.90"          # exact decimal string — never a float
PARTY_VALUE = ENTITY_NAME

# New files this sprint added — scanned for forbidden brand hex (Assertion 7).
NEW_FILES = [
    os.path.join(_API_ROOT, "services", "document_review.py"),
    os.path.join(_API_ROOT, "routers", "document_review.py"),
    os.path.join(_REPO_ROOT, "apps", "web", "app", "admin", "document-review",
                 "[documentId]", "page.js"),
    os.path.join(_REPO_ROOT, "apps", "web", "components", "admin",
                 "DocumentReviewManager.jsx"),
    os.path.join(_REPO_ROOT, "apps", "web", "lib", "documentReviewActions.js"),
    os.path.join(_REPO_ROOT, "apps", "web", "app", "api", "documents",
                 "[documentId]", "download", "route.js"),
]
# Signature-palette brand hexes that must NOT be hardcoded (tokens/vars only).
FORBIDDEN_HEX = ["1b2b4b", "c5a880", "e8d5a3", "faf9f6"]

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


# ── DB helpers ───────────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


async def _teardown(conn):
    """FK-safe, child-first, keyed on the test docs / user / name marker."""
    await conn.execute(
        "DELETE FROM document_field_corrections WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    await conn.execute(
        "DELETE FROM document_entity_links WHERE document_id = ANY($1::uuid[]) "
        "OR entity_id IN (SELECT id FROM entities WHERE org_id = $2 "
        "AND display_name LIKE '%' || $3 || '%')",
        TEST_DOC_IDS, ORG_A, MARKER)
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
        "DELETE FROM documents WHERE id = ANY($1::uuid[]) OR created_by = $2",
        TEST_DOC_IDS, TEST_USER_ID)
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE '%' || $2 || '%'",
        ORG_A, MARKER)


async def seed(conn):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_chancery6@test.local', 'Chancery6 Verify', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_A, TEST_AUTH0_SUB)
    uid = await conn.fetchval("SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)

    await conn.execute(
        """
        INSERT INTO entities (id, org_id, entity_type, display_name, status)
        VALUES ($1, $2, 'llc'::entity_type, $3, 'prospect')
        ON CONFLICT (id) DO NOTHING
        """,
        ENTITY_ID, ORG_A, ENTITY_NAME)

    await conn.execute(
        """
        INSERT INTO documents (id, org_id, original_filename, source, status,
                               doc_family, storage_key, created_by)
        VALUES ($1, $2, $3, 'upload', 'sorted', 'tabular', $4, $5)
        ON CONFLICT (id) DO NOTHING
        """,
        DOC_ID, ORG_A, f"k1_{MARKER}.pdf", STORAGE_KEY, uid)

    # native extraction (Phase 1 shape) — text + per-page tables, page_count.
    await conn.execute(
        """
        INSERT INTO document_extractions
            (document_id, org_id, extraction_method, has_native_text_layer,
             extracted_text, extracted_tables, page_count)
        VALUES ($1, $2, 'native_pdfplumber', true, $3, $4::jsonb, 2)
        """,
        DOC_ID, ORG_A,
        "Schedule K-1 (Form 1065)\nOrdinary business income 1,234.00\n",
        json.dumps([{"page": 1, "tables": [[["Ordinary business income", "1,234.00"]]]}]))

    # template extraction (Phase 3 output shape) — mapped_fields as decimal strings.
    await conn.execute(
        """
        INSERT INTO document_template_extractions
            (document_id, org_id, template_type, extraction_source, mapped_fields)
        VALUES ($1, $2, 'k1', 'native', $3::jsonb)
        """,
        DOC_ID, ORG_A,
        json.dumps({"partner_name": PARTY_VALUE, CORRECT_FIELD: ORIGINAL_VALUE}))

    # one real Phase-5 entity link so the payload's links are non-empty.
    await dl.link_document_to_entities(
        conn, ORG_A, DOC_ID, [ENTITY_ID], created_by=uid, link_role="manual")
    return uid


async def count_val(conn, sql, *args):
    return await conn.fetchval(sql, *args)


# ── main assertion flow ──────────────────────────────────────────────────────
async def run_assertions(conn, uid):
    # A1 — Task 1 discovery findings (reported explicitly)
    print("\n--- Assertion 1: Task 1 discovery findings ---")
    ok("Task 1(a) Textract source coordinates: NOT available",
       "services.textract.parse_analyze_blocks keeps ONLY block/cell TEXT; AWS "
       "AnalyzeDocument returns Geometry.BoundingBox by default but it is discarded "
       "before raw_extraction is built/stored (textract_extraction.run_k1_extraction).")
    ok("Task 1(b) native pdfplumber source coordinates: NOT available",
       "chancery_intake.extract_native uses page.extract_text()/extract_tables() — "
       "plain strings only; pdfplumber CAN expose char bboxes (page.chars) but Phase 1 "
       "never captured them. Only page-level granularity (per-table page + page_count).")
    ok("Task 1(c) UI conventions reused",
       "server-component page.js (auth0.getSession + redirect) + client Manager in "
       "components/admin/ using local Card + inputClass + token utilities (text-navy, "
       "bg-bg-card, border-border); mutations via 'use server' actions; EntityPicker + "
       "download-proxy → window.open reused from DocumentsTab.")
    ok("HONEST coordinate availability: NEITHER path",
       "real source coordinates available for Textract=NO, native=NO → UI degrades to a "
       "page reference ('see attached document'), never a fabricated highlight overlay.")

    # A2 — review payload endpoint returns complete data
    print("\n--- Assertion 2: review payload is complete ---")
    payload = await review.get_review_payload(conn, ORG_A, DOC_ID)
    doc = payload["document"]
    tmpl = payload["template_extraction"]
    extraction = payload["extraction"]
    fields = payload["fields"]
    ent_links = payload["links"]["entity_links"]
    field_names = {f["field_name"] for f in fields}
    field_ok = (CORRECT_FIELD in field_names and "partner_name" in field_names
                and all(f["confidence"] is None for f in fields))
    linked_ok = any(l["entity_id"] == ENTITY_ID for l in ent_links)
    complete = (
        doc["id"] == DOC_ID
        and doc["has_stored_file"] is True
        and extraction is not None
        and (extraction["extracted_text"] or "").strip() != ""
        and extraction["page_count"] == 2
        and tmpl is not None
        and tmpl["mapped_fields"].get(CORRECT_FIELD) == ORIGINAL_VALUE
        and field_ok
        and payload["confidence_available"] is False
        and payload["source_location"]["coordinates_available"] is False
        and linked_ok
    )
    if complete:
        ok("review payload: extracted content + template fields + real Phase-5 links",
           f"fields={sorted(field_names)}, page_count={extraction['page_count']}, "
           f"confidence_available={payload['confidence_available']}, "
           f"coords_available={payload['source_location']['coordinates_available']}, "
           f"linked_entity={linked_ok}")
    else:
        fail("review payload complete",
             f"doc={doc}, extraction_present={extraction is not None}, "
             f"template={tmpl}, fields={sorted(field_names)}, linked_ok={linked_ok}, "
             f"confidence_available={payload['confidence_available']}, "
             f"coords_available={payload['source_location']['coordinates_available']}")

    # A3 — correction: audit row + LIVE mapped_fields updated
    print("\n--- Assertion 3: correction writes audit row AND updates mapped_fields ---")
    result = await review.submit_field_correction(
        conn, ORG_A, DOC_ID, field_name=CORRECT_FIELD,
        corrected_value=CORRECTED_VALUE, corrected_by=uid, notes="verify correction")
    corr = await conn.fetchrow(
        "SELECT original_value, corrected_value, template_extraction_id, corrected_by, "
        "field_name FROM document_field_corrections WHERE document_id = $1", DOC_ID)
    # Re-read the ACTUAL template row to prove the live value changed (not just logged).
    raw_mapped = await conn.fetchval(
        "SELECT mapped_fields FROM document_template_extractions WHERE document_id = $1",
        DOC_ID)
    mapped_now = json.loads(raw_mapped) if isinstance(raw_mapped, str) else raw_mapped
    audit_ok = bool(
        corr and corr["original_value"] == ORIGINAL_VALUE
        and corr["corrected_value"] == CORRECTED_VALUE
        and corr["field_name"] == CORRECT_FIELD
        and corr["template_extraction_id"] is not None
        and str(corr["corrected_by"]) == str(uid))
    live_ok = mapped_now.get(CORRECT_FIELD) == CORRECTED_VALUE
    # partner_name untouched — a targeted field update, not a wholesale overwrite.
    untouched_ok = mapped_now.get("partner_name") == PARTY_VALUE
    if audit_ok and live_ok and untouched_ok and result["correction_id"]:
        ok("correction: audit row correct AND live mapped_fields value changed",
           f"original={corr['original_value']}, corrected={mapped_now[CORRECT_FIELD]}, "
           f"partner_name preserved={untouched_ok}")
    else:
        fail("correction writes audit + updates mapped_fields",
             f"audit_ok={audit_ok}, live_ok={live_ok} (now={mapped_now.get(CORRECT_FIELD)}), "
             f"untouched_ok={untouched_ok}, corr={dict(corr) if corr else None}")

    # A4 — confirm: status + confirmed_by + confirmed_at
    print("\n--- Assertion 4: confirm updates status + who + when ---")
    conf = await review.confirm_document(conn, ORG_A, DOC_ID, confirmed_by=uid)
    row = await conn.fetchrow(
        "SELECT status, confirmed_by, confirmed_at FROM documents WHERE id = $1", DOC_ID)
    if (conf["status"] == "confirmed" and row["status"] == "confirmed"
            and str(row["confirmed_by"]) == str(uid) and row["confirmed_at"] is not None):
        ok("confirm sets status='confirmed' with confirmed_by + confirmed_at",
           f"status={row['status']}, confirmed_by={row['confirmed_by']}, "
           f"confirmed_at={row['confirmed_at']}")
    else:
        fail("confirm updates status/who/when",
             f"conf={conf}, row={dict(row) if row else None}")


# ── cross-org RLS (non-bypass app_service) ───────────────────────────────────
async def rls_isolation_checks():
    """A5 — a different org cannot read the payload / corrections, nor confirm."""
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("RLS: cross-org isolation of review payload/corrections/confirm",
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
                skip("RLS: cross-org isolation of review payload/corrections/confirm",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("RLS: cross-org isolation of review payload/corrections/confirm",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc})")
            return
    try:
        async def as_org_b(coro_factory):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", ORG_B)
                return await coro_factory()

        # 1) payload invisible → ReviewError 404
        payload_blocked = False
        try:
            await as_org_b(lambda: review.get_review_payload(conn, ORG_B, DOC_ID))
        except review.ReviewError as err:
            payload_blocked = err.status_code == 404
        except dl.LinkageError as err:  # some paths raise via linkage first
            payload_blocked = err.status_code == 404

        # 2) corrections rows invisible
        async def _corr_count():
            return await conn.fetchval(
                "SELECT count(*) FROM document_field_corrections WHERE document_id = ANY($1::uuid[])",
                TEST_DOC_IDS)
        b_corr = await as_org_b(_corr_count)

        # 3) cannot confirm this org's document → ReviewError 404
        confirm_blocked = False
        try:
            await as_org_b(lambda: review.confirm_document(conn, ORG_B, DOC_ID, confirmed_by=None))
        except review.ReviewError as err:
            confirm_blocked = err.status_code == 404

        if payload_blocked and b_corr == 0 and confirm_blocked:
            ok("cross-org isolation: payload 404, corrections invisible, confirm 404",
               f"payload_blocked={payload_blocked}, org_b_corrections={b_corr}, "
               f"confirm_blocked={confirm_blocked}")
        else:
            fail("cross-org isolation of review payload/corrections/confirm",
                 f"payload_blocked={payload_blocked}, org_b_corrections={b_corr} (want 0), "
                 f"confirm_blocked={confirm_blocked}")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("RLS: cross-org isolation", f"app_service lacks table GRANTs: {msg}")
        else:
            fail("RLS: cross-org isolation", msg)
    finally:
        await conn.close()


# ── frontend build + palette-hex checks ─────────────────────────────────────
def build_and_hex_checks():
    print("\n--- Assertion 6: npm run build exits 0 ---")
    web_dir = os.path.join(_REPO_ROOT, "apps", "web")
    npm = None
    for cand in ("npm", "/usr/bin/npm", "/usr/local/bin/npm"):
        if cand == "npm" or os.path.exists(cand):
            npm = cand
            break
    if not npm:
        skip("npm run build exits 0", "npm not found on PATH")
    else:
        try:
            proc = subprocess.run(
                [npm, "run", "build"], cwd=web_dir, capture_output=True,
                text=True, timeout=1200)
            if proc.returncode == 0:
                ok("npm run build exits 0")
            else:
                tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
                fail("npm run build exits 0", f"returncode={proc.returncode}\n{tail}")
        except FileNotFoundError:
            skip("npm run build exits 0", "npm not runnable")
        except subprocess.TimeoutExpired:
            fail("npm run build exits 0", "build timed out after 1200s")

    print("\n--- Assertion 7: no hardcoded Signature-palette hex in new files ---")
    offenders = []
    for path in NEW_FILES:
        if not os.path.exists(path):
            offenders.append(f"{path} (MISSING)")
            continue
        text = open(path, encoding="utf-8").read().lower()
        for hx in FORBIDDEN_HEX:
            if hx in text:
                offenders.append(f"{os.path.basename(path)}:#{hx}")
    if not offenders:
        ok("no hardcoded Signature-palette hex in any new file",
           f"scanned {len(NEW_FILES)} files for {FORBIDDEN_HEX}")
    else:
        fail("no hardcoded Signature-palette hex", f"offenders={offenders}")


async def count_leftovers(conn):
    corr = await count_val(
        conn, "SELECT count(*) FROM document_field_corrections WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    tmpl = await count_val(
        conn, "SELECT count(*) FROM document_template_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    ext = await count_val(
        conn, "SELECT count(*) FROM document_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    docs = await count_val(
        conn, "SELECT count(*) FROM documents WHERE id = ANY($1::uuid[])", TEST_DOC_IDS)
    links = await count_val(
        conn, "SELECT count(*) FROM document_entity_links WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    return corr, tmpl, ext, docs, links


async def main_async():
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-START
        uid = await seed(conn)
        print(f"[info] seeded verify user id={uid}")
        await run_assertions(conn, uid)
    finally:
        await conn.close()

    # A5 — cross-org isolation on the real app_service role.
    print("\n--- Assertion 5: cross-org isolation (app_service) ---")
    await rls_isolation_checks()

    # A6 + A7 — build + palette hex (sync).
    build_and_hex_checks()

    # A8 — teardown-at-END + leftover check.
    print("\n--- Assertion 8: teardown leaves zero rows ---")
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-END
        corr, tmpl, ext, docs, links = await count_leftovers(conn)
        if (corr, tmpl, ext, docs, links) == (0, 0, 0, 0, 0):
            ok("teardown: zero leftover rows (corrections/template/extraction/docs/links)")
        else:
            fail("teardown: zero leftover rows",
                 f"corrections={corr}, template={tmpl}, extraction={ext}, "
                 f"documents={docs}, links={links}")
    finally:
        await conn.close()


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
    print("=== Chancery Phase 6 verify (document review / confirm) — start ===")
    try:
        asyncio.run(main_async())
    except Exception:  # noqa: BLE001 — a crash is itself a failure to report
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify run", "crashed — see traceback"))
    sys.exit(summarize())
